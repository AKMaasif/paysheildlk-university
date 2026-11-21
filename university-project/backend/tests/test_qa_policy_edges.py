"""The less-travelled paths of calibration, emergency mode and the remote operator feed."""
import dataclasses

import httpx
import pytest

from app import calibration, emergency, faults
from app.agents import tools
from app.agents.runner import begin_case
from app.db import session_scope
from app.demo import trigger
from app.models import Case, PolicyProposal, PolicySetting, Transaction
from app.risk import reset_thresholds, set_threshold, thresholds
from app.runtime import runtime
from app.util import nid, utcnow


@pytest.fixture(autouse=True)
def _clean():
    reset_thresholds()
    faults.clear_all()
    yield
    reset_thresholds()
    faults.clear_all()


def case(score, decision, verified, policy="hold"):
    return Case(id=nid("PS-edge"), customer_id="cus_nimal", status="closed_cleared", risk_score=score, policy_decision=policy, customer_decision=decision,
                verification_passed=verified, created_at=utcnow(), updated_at=utcnow(), amount_lkr=1, origin="test", provider_counts={})


# ------------------------------------------------------------------ what a customer's answer says about a hold

@pytest.mark.parametrize("decision,verified,expected", [
    ("no", None, "fraud"), ("no", True, "fraud"), ("yes", True, "legitimate"), ("yes", False, "fraud"),
    ("yes", None, None), (None, None, None), ("supervisor_block", None, None), ("", True, None),
])
def test_each_answer_maps_to_fraud_legitimate_or_not_yet_known(decision, verified, expected):
    assert calibration._outcome(case(50, decision, verified)) == expected


def test_evidence_counts_only_holds_at_or_above_the_hold_score_and_only_answered_ones(boot):
    with session_scope() as session:
        before = calibration.evidence(session)["answered_cases"]
        for row in (case(50, "yes", True), case(50, "no", None), case(50, None, None), case(30, "yes", True), case(80, "yes", True), case(50, "yes", True, policy="release")):
            session.add(row)
    with session_scope() as session:
        ev = calibration.evidence(session)
    assert ev["answered_cases"] == before + 3  # the answered 50, 50 and 80; not the unanswered, the low score, or the released one
    starts = [b["from"] for b in ev["bands"]]
    assert starts == sorted(starts) and all(s >= 45 for s in starts)


def test_bands_are_ten_points_wide_and_the_top_band_stops_at_100(boot):
    with session_scope() as session:
        session.add(case(97, "yes", True))
    with session_scope() as session:
        top = calibration.evidence(session)["bands"][-1]
    assert top["from"] == 95 and top["to"] == 100


# ------------------------------------------------------------------ when a proposal is not made

def _fill_band(session, legit, fraud, score=47):
    # Start from no evidence: the seeded demo history already holds answered cases.
    session.query(Case).filter(Case.origin != "test").update({Case.customer_decision: None, Case.verification_passed: None})
    for _ in range(legit):
        session.add(case(score, "yes", True))
    for _ in range(fraud):
        session.add(case(score, "no", None))
    session.flush()  # sessions here do not flush by themselves


def test_no_proposal_when_the_lowest_band_is_not_at_the_current_threshold(boot):
    set_threshold("hold_score", 50)  # the evidence starts at 50 now, but the cases sit at 47
    with session_scope() as session:
        assert calibration.propose(session) == []


def test_no_proposal_that_would_leave_the_safe_range(boot):
    set_threshold("hold_score", 65)
    with session_scope() as session:
        _fill_band(session, 40, 0, score=66)
        assert calibration.propose(session) == []  # 65 + 10 = 75 is above the limit of 70


def test_the_same_proposal_is_not_made_twice_while_one_is_waiting(boot):
    with session_scope() as session:
        _fill_band(session, calibration.MIN_CASES + 5, 0)
        assert len(calibration.propose(session)) == 1
        assert calibration.propose(session) == []
        assert session.query(PolicyProposal).filter(PolicyProposal.status == "pending").count() == 1


# ------------------------------------------------------------------ the human gate

def _pending(session):
    _fill_band(session, calibration.MIN_CASES + 5, 0)
    return calibration.propose(session)[0]["id"]


def test_deciding_an_unknown_or_already_decided_proposal_is_refused(boot):
    with session_scope() as session:
        with pytest.raises(LookupError):
            calibration.decide(session, "pol-none", True, "a@b.lk", "x")
        pid = _pending(session)
        calibration.decide(session, pid, False, "a@b.lk", "not now")
        with pytest.raises(ValueError, match="already been decided"):
            calibration.decide(session, pid, True, "a@b.lk", "changed my mind")
    assert thresholds()["hold_score"] == 45


def test_a_proposal_goes_stale_when_the_threshold_moved_after_it_was_written(boot):
    with session_scope() as session:
        pid = _pending(session)
    set_threshold("hold_score", 50)
    with session_scope() as session:
        with pytest.raises(ValueError, match="has changed"):
            calibration.decide(session, pid, True, "a@b.lk", "x")
    with session_scope() as session:
        assert session.get(PolicyProposal, pid).status == "superseded"
    assert thresholds()["hold_score"] == 50


def test_approving_twice_over_time_updates_the_stored_setting_and_records_who(boot):
    with session_scope() as session:
        pid = _pending(session)
        calibration.decide(session, pid, True, "first@b.lk", "ok")
    assert thresholds()["hold_score"] == 55
    with session_scope() as session:
        assert session.get(PolicySetting, "hold_score").updated_by == "first@b.lk"
        _fill_band(session, calibration.MIN_CASES + 5, 0, score=57)
        again = calibration.propose(session)
        assert again and again[0]["proposed"] == 65
        calibration.decide(session, again[0]["id"], True, "second@b.lk", "ok again")
    with session_scope() as session:
        assert session.get(PolicySetting, "hold_score").value == 65 and session.get(PolicySetting, "hold_score").updated_by == "second@b.lk"
    assert thresholds()["hold_score"] == 65


def test_a_stored_setting_that_is_now_out_of_range_is_ignored_on_load(boot):
    with session_scope() as session:
        session.add(PolicySetting(key="hold_score", value=999, updated_by="old@b.lk", updated_at=utcnow()))
        session.add(PolicySetting(key="not_a_setting", value=5, updated_by="old@b.lk", updated_at=utcnow()))
    with session_scope() as session:
        calibration.load_thresholds(session)
    assert thresholds() == {"hold_score": 45}


def test_a_decision_note_is_cut_to_500_characters(boot):
    with session_scope() as session:
        pid = _pending(session)
        out = calibration.decide(session, pid, False, "a@b.lk", "n" * 900)
    assert len(out["decision_note"]) == 500


# ------------------------------------------------------------------ emergency mode housekeeping

def test_setting_an_emergency_twice_updates_it_and_clearing_a_missing_one_is_harmless(boot):
    emergency.force_emergency("Kandy", "first reason", "a@b.lk")
    emergency.force_emergency("Kandy", "r" * 400, "b@b.lk")
    with session_scope() as session:
        from app.models import EmergencyMode

        row = session.get(EmergencyMode, "Kandy")
        assert row.set_by == "b@b.lk" and len(row.reason) == 200
    assert emergency.clear_emergency("Nowhere") is not None
    assert emergency.clear_all() >= 1
    assert emergency.clear_all() == 0


# ------------------------------------------------------------------ the remote operator feed

class Feed:
    """Stands in for the operator's CAMARA service."""

    def __init__(self, swapped=True, fail=None):
        self.calls, self.swapped, self.fail = [], swapped, fail

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers or {}, timeout))
        if self.fail:
            raise self.fail
        request = httpx.Request("POST", url)
        if url.endswith("/check"):
            return httpx.Response(200, json={"swapped": self.swapped}, request=request)
        return httpx.Response(200, json={"latestSimChange": "2026-01-01T00:00:00Z"}, request=request)


def with_feed(monkeypatch, feed, token="secret-token"):
    monkeypatch.setattr(httpx, "post", feed)
    monkeypatch.setattr(runtime, "settings", dataclasses.replace(runtime.settings, telco_base_url="https://telco.example/camara", telco_token=token))


def test_the_remote_feed_is_called_with_the_token_and_the_window(boot, monkeypatch):
    feed = Feed()
    with_feed(monkeypatch, feed)
    out = tools.get_sim_swap_status("+94772451183", 120)
    assert out["source"] == "camara-http" and out["checkSimSwap"]["swapped"] is True and out["retrieveSimSwapDate"]["latestSimChange"].startswith("2026")
    (check_url, check_body, check_headers, check_timeout), (date_url, date_body, _h, _t) = feed.calls
    assert check_url == "https://telco.example/camara/sim-swap/v1/check" and check_body == {"phoneNumber": "+94772451183", "maxAge": 120}
    assert check_headers["Authorization"] == "Bearer secret-token" and check_timeout == 15
    assert date_url.endswith("/retrieve-date") and "maxAge" not in date_body


def test_without_a_token_no_authorization_header_is_sent(boot, monkeypatch):
    feed = Feed()
    with_feed(monkeypatch, feed, token="")
    tools.get_sim_swap_status("+94772451183", 0)
    assert "Authorization" not in feed.calls[0][2] and "maxAge" not in feed.calls[0][1]


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout("slow"), httpx.ConnectError("down"), httpx.ReadTimeout("slow")])
def test_a_feed_that_is_down_holds_the_money_and_gives_the_case_to_a_person(boot, monkeypatch, failure):
    with_feed(monkeypatch, Feed(fail=failure))
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    with session_scope() as session:
        row = session.get(Case, case_id)
        assert row.status == "escalated" and session.get(Transaction, row.transaction_id).status == "held"


def test_a_feed_that_answers_with_an_error_status_is_a_failure_not_a_no_swap(boot, monkeypatch):
    def broken(url, json=None, headers=None, timeout=None):
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", broken)
    monkeypatch.setattr(runtime, "settings", dataclasses.replace(runtime.settings, telco_base_url="https://telco.example"))
    with pytest.raises(httpx.HTTPStatusError):
        tools.get_sim_swap_status("+94772451183", 240)


def test_over_the_api_a_stale_proposal_is_marked_superseded_and_cannot_be_approved_later(boot):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.security import create_token

    with session_scope() as session:
        pid = _pending(session)
    with session_scope() as session:  # moved by someone else after the proposal was written, and saved
        session.add(PolicySetting(key="hold_score", value=50, updated_by="other@b.lk", updated_at=utcnow()))
    set_threshold("hold_score", 50)
    headers = {"Authorization": "Bearer " + create_token(runtime.settings, "usr_analyst", "fraud_analyst")}
    with TestClient(app) as client:
        first = client.post(f"/api/calibration/proposals/{pid}/decide", json={"decision": "approve", "note": "ok"}, headers=headers)
        assert first.status_code == 409 and "changed" in first.json()["detail"]
        with session_scope() as session:
            assert session.get(PolicyProposal, pid).status == "superseded"
        again = client.post(f"/api/calibration/proposals/{pid}/decide", json={"decision": "approve", "note": "ok"}, headers=headers)
        assert again.status_code == 409 and "already been decided" in again.json()["detail"]
    assert thresholds()["hold_score"] == 50
