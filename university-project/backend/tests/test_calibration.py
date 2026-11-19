"""Customer answers feed a calibration step. The Analytics agent may propose a change to the hold score.
Only an analyst's approval changes it, and it can never leave its hard limits."""
import pytest
from fastapi.testclient import TestClient

from app import calibration
from app.charters import CharterViolation, enforce
from app.db import session_scope
from app.main import app
from app.models import AuditLog, Case, PolicyProposal, PolicySetting
from app.risk import DEFAULTS, SAFE_RANGE, Signals, decide_policy, reset_thresholds, thresholds
from app.runtime import init_runtime, runtime
from app.security import create_token
from app.util import nid, utcnow


@pytest.fixture(autouse=True)
def _defaults():
    reset_thresholds()
    yield
    reset_thresholds()


# Swap 100 hours ago (25) + new handset (15) + location change (10) = 50, with a small amount: held at 45, not at 55.
SCORE_50 = Signals(sim_swap_age_hours=100, new_imei_age_hours=50, location_change_age_hours=10, amount_lkr=1000)


def _case(score, decision, verified, customer="cus_nimal"):
    return Case(
        id=nid("PS-cal"), customer_id=customer, status="closed_cleared", risk_score=score, policy_decision="hold", customer_decision=decision,
        verification_passed=verified, created_at=utcnow(), updated_at=utcnow(), amount_lkr=1, origin="test", provider_counts={},
    )


def _fill(n_legit=0, n_fraud=0, score=47):
    with session_scope() as session:
        for _ in range(n_legit):
            session.add(_case(score, "yes", True))
        for _ in range(n_fraud):
            session.add(_case(score, "no", None))


def _auth(user_id, role):
    return {"Authorization": "Bearer " + create_token(runtime.settings, user_id, role)}


def _existing_in_first_band():
    with session_scope() as session:
        bands = calibration.evidence(session)["bands"]
    return bands[0]["cases"] if bands and bands[0]["from"] == 45 else 0


def test_no_proposal_without_enough_evidence(boot):
    # The seeded history already has a couple of answered holds in the band. Stop one short of the minimum.
    _fill(n_legit=calibration.MIN_CASES - 1 - _existing_in_first_band())
    with session_scope() as session:
        assert calibration.propose(session) == []


def test_no_proposal_when_any_hold_in_the_band_was_fraud(boot):
    _fill(n_legit=40, n_fraud=1)
    with session_scope() as session:
        assert calibration.propose(session) == []


def test_a_proposal_is_made_when_every_answered_hold_in_the_lowest_band_was_a_false_hold(boot):
    _fill(n_legit=25)
    with session_scope() as session:
        made = calibration.propose(session)
        assert len(made) == 1 and made[0]["current"] == 45 and made[0]["proposed"] == 55 and made[0]["status"] == "pending"
        assert "false holds only" in made[0]["reason"]
        # Asking again does not pile up duplicates.
        assert calibration.propose(session) == []
    # A proposal changes nothing by itself.
    assert thresholds()["hold_score"] == 45
    assert decide_policy(SCORE_50).decision == "hold"


def test_a_customer_who_said_yes_but_failed_the_questions_counts_as_fraud(boot):
    with session_scope() as session:
        for _ in range(30):
            session.add(_case(47, "yes", False))
        assert calibration.propose(session) == []


def test_only_the_analytics_agent_may_propose():
    enforce("analytics", "propose_threshold")
    for agent in ("policy", "risk", "alert", "case", "telecom", "banking", "response", "verification", "supervisor"):
        with pytest.raises(CharterViolation):
            enforce(agent, "propose_threshold")


def test_approval_is_the_only_thing_that_moves_the_number(boot):
    _fill(n_legit=25)
    with TestClient(app) as client:
        made = client.post("/api/calibration/propose", headers=_auth("usr_analyst", "fraud_analyst")).json()["proposals"]
        pid = made[0]["id"]
        # Roles that may not approve.
        for who, role in (("usr_auditor", "auditor"), ("usr_supervisor", "bank_supervisor"), ("usr_telco", "telecom_operator")):
            assert client.post(f"/api/calibration/proposals/{pid}/decide", json={"decision": "approve"}, headers=_auth(who, role)).status_code == 403
        assert thresholds()["hold_score"] == 45
        done = client.post(f"/api/calibration/proposals/{pid}/decide", json={"decision": "approve", "note": "false holds are costing customers"}, headers=_auth("usr_analyst", "fraud_analyst"))
        assert done.status_code == 200 and done.json()["status"] == "approved"
        assert thresholds()["hold_score"] == 55
        # It cannot be decided twice.
        assert client.post(f"/api/calibration/proposals/{pid}/decide", json={"decision": "reject"}, headers=_auth("usr_analyst", "fraud_analyst")).status_code == 409
        view = client.get("/api/calibration", headers=_auth("usr_auditor", "auditor")).json()
        assert view["thresholds"]["hold_score"]["value"] == 55 and view["can_approve"] is False
    with session_scope() as session:
        actions = [a.action for a in session.query(AuditLog).all()]
    assert "calibration_propose" in actions and "calibration_approve" in actions
    # The new number now decides: a score of 47 is no longer held for score alone.
    assert decide_policy(SCORE_50).decision == "release"


def test_rejection_changes_nothing(boot):
    _fill(n_legit=25)
    with TestClient(app) as client:
        pid = client.post("/api/calibration/propose", headers=_auth("usr_analyst", "fraud_analyst")).json()["proposals"][0]["id"]
        assert client.post(f"/api/calibration/proposals/{pid}/decide", json={"decision": "reject", "note": "not yet"}, headers=_auth("usr_admin", "super_admin")).json()["status"] == "rejected"
    assert thresholds()["hold_score"] == 45


def test_the_number_survives_a_restart_and_can_be_reset(boot):
    _fill(n_legit=25)
    with TestClient(app) as client:
        pid = client.post("/api/calibration/propose", headers=_auth("usr_analyst", "fraud_analyst")).json()["proposals"][0]["id"]
        client.post(f"/api/calibration/proposals/{pid}/decide", json={"decision": "approve"}, headers=_auth("usr_analyst", "fraud_analyst"))
        reset_thresholds()
        assert thresholds()["hold_score"] == 45
        init_runtime()  # a restart reads the saved number back
        assert thresholds()["hold_score"] == 55
        assert client.post("/api/calibration/reset", headers=_auth("usr_analyst", "fraud_analyst")).status_code == 200
    assert thresholds()["hold_score"] == 45
    with session_scope() as session:
        assert session.query(PolicySetting).count() == 0


def test_hard_limits_hold_even_for_an_approved_proposal(boot):
    low, high = SAFE_RANGE["hold_score"]
    with session_scope() as session:
        session.add(PolicyProposal(id="pol_x", key="hold_score", current=45, proposed=high + 20, reason="out of range", status="pending", created_at=utcnow()))
    with TestClient(app) as client:
        reply = client.post("/api/calibration/proposals/pol_x/decide", json={"decision": "approve"}, headers=_auth("usr_analyst", "fraud_analyst"))
    assert reply.status_code == 409 and thresholds()["hold_score"] == 45
    with pytest.raises(ValueError):
        from app.risk import set_threshold

        set_threshold("hold_score", low - 1)


def test_the_block_and_swap_plus_warning_rules_do_not_depend_on_the_threshold():
    from app.risk import set_threshold

    set_threshold("hold_score", SAFE_RANGE["hold_score"][1])
    assert decide_policy(Signals(port_out=True, new_payee=True, password_reset_age_hours=5, amount_lkr=500_000)).decision == "block"
    assert decide_policy(Signals(sim_swap_age_hours=10, amount_lkr=60_000, new_device_login=True)).decision == "hold"


def test_defaults_are_the_original_rule_table():
    assert DEFAULTS == {"hold_score": 45}
