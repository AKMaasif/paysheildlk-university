import pytest
from fastapi.testclient import TestClient

from app import faults
from app.agents.runner import (
    begin_case,
    escalate_case,
    fail_safe,
    realert_case,
    resume_case,
)
from app.db import session_scope
from app.demo import reset_demo, trigger
from app.emergency import SPIKE_COUNT, breaker_status, clear_emergency, force_emergency
from app.main import app
from app.models import Case, CaseEvent, Notification, Report, Transaction, User
from app.runtime import runtime
from app.security import create_token


@pytest.fixture(autouse=True)
def _clean_faults():
    faults.clear_all()
    yield
    faults.clear_all()


def _auth(user_id="usr_analyst", role="fraud_analyst"):
    return {"Authorization": "Bearer " + create_token(runtime.settings, user_id, role)}


def _state(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        tx = session.get(Transaction, case.transaction_id)
        return case.status, tx.status, case.policy_decision, case.policy_reason or ""


def _events(case_id):
    with session_scope() as session:
        rows = session.query(CaseEvent).filter(CaseEvent.case_id == case_id).order_by(CaseEvent.created_at).all()
        return [(row.agent, row.state, row.tool_name, row.provider, row.fell_back, row.message or row.result_summary or "") for row in rows]


# District emergency mode

def test_a_large_payment_is_normally_released(boot):
    case_id = trigger("cus_nimal", "large_payment")
    begin_case(case_id)
    status, money, decision, _ = _state(case_id)
    assert (decision, money, status) == ("release", "released", "closed_cleared")


def test_emergency_mode_holds_a_payment_the_rules_would_release(boot):
    force_emergency("Colombo", "analyst decision", by="analyst@payshield.lk")
    case_id = trigger("cus_nimal", "large_payment")
    begin_case(case_id)
    status, money, decision, reason = _state(case_id)
    assert (decision, money, status) == ("hold", "held", "waiting_customer")
    assert "Emergency rule" in reason and "Colombo" in reason
    # The customer still decides, and the normal step-up still applies.
    assert resume_case(case_id, {"decision": "no"}, "waiting_customer")
    assert _state(case_id)[:2] == ("closed_blocked", "blocked")


def test_emergency_mode_ignores_small_payments_and_other_districts(boot):
    force_emergency("Jaffna", "analyst decision", by="analyst@payshield.lk")
    case_id = trigger("cus_nimal", "large_payment")  # Nimal is in Colombo
    begin_case(case_id)
    assert _state(case_id)[2] == "release"
    force_emergency("Colombo", "analyst decision", by="analyst@payshield.lk")
    small = trigger("cus_nimal", "routine_payment")  # Rs. 4,850
    begin_case(small)
    assert _state(small)[2] == "release"


def test_emergency_mode_never_lowers_a_block(boot):
    force_emergency("Colombo", "analyst decision", by="analyst@payshield.lk")
    case_id = trigger("cus_nimal", "port_out_critical")
    begin_case(case_id)
    status, money, decision, reason = _state(case_id)
    assert (status, money, decision) == ("closed_blocked", "blocked", "block")
    assert "Emergency rule" not in reason


def test_a_real_spike_trips_the_district_by_itself(boot):
    assert "Colombo" not in breaker_status()["active"]
    from app.simulate import _inject_spike

    _inject_spike("Colombo")
    status = breaker_status("Colombo")
    assert status["tripped"] and "fraud spike" in status["active"]["Colombo"]
    assert status["recent_counts"]["Colombo"] == SPIKE_COUNT
    case_id = trigger("cus_nimal", "large_payment")
    begin_case(case_id)
    assert _state(case_id)[2] == "hold"


def test_the_simulated_book_never_counts_toward_a_live_spike(boot):
    # The analytics book has plenty of held and blocked rows with districts, some inside the window's hour.
    assert breaker_status()["active"] == {}


def test_emergency_mode_survives_a_restart_and_clears_on_request(boot):
    force_emergency("Kandy", "fraud ring", by="analyst@payshield.lk")
    from app.runtime import init_runtime

    init_runtime()
    assert "Kandy" in breaker_status()["active"]
    clear_emergency("Kandy")
    assert "Kandy" not in breaker_status()["active"]


def test_the_analyst_switch_is_audited_and_validated(boot):
    with TestClient(app) as client:
        bad = client.post("/api/emergency", json={"district": "Atlantis", "on": True}, headers=_auth())
        assert bad.status_code == 400
        read_only = client.post("/api/emergency", json={"district": "Colombo", "on": True}, headers=_auth("usr_auditor", "auditor"))
        assert read_only.status_code == 403
        on = client.post("/api/emergency", json={"district": "Colombo", "on": True, "reason": "fraud ring"}, headers=_auth())
        assert on.status_code == 200 and on.json()["tripped"]
        listed = client.get("/api/emergency", headers=_auth("usr_auditor", "auditor")).json()
        assert "Colombo" in listed["active"]
        off = client.post("/api/emergency", json={"district": "Colombo", "on": False, "reason": "all clear"}, headers=_auth())
        assert not off.json()["tripped"]
    from app.ledger import verify_audit_chain
    from app.models import AuditLog

    with session_scope() as session:
        actions = [row.action for row in session.query(AuditLog).order_by(AuditLog.created_at)]
    assert "emergency_on" in actions and "emergency_off" in actions
    assert verify_audit_chain()["ok"]


# Models down

def test_with_both_models_down_the_rules_still_hold_and_nothing_is_released(boot, monkeypatch):
    normal = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(normal)
    baseline = _state(normal)
    monkeypatch.setattr(runtime.settings, "gemini_api_key", "set-but-down")
    faults.turn_on("llm_down")
    drilled = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(drilled)
    status, money, decision, _ = _state(drilled)
    assert (status, money, decision) == (baseline[0], baseline[1], baseline[2]) == ("waiting_customer", "held", "hold")
    notes = [event for event in _events(drilled) if event[3] in {"mock", "gemini"} and event[0] in {"telecom", "banking", "risk"} and event[1] == "done"]
    assert notes and all(event[3] == "mock" and event[4] is True for event in notes)


# Channel failure

def test_sms_and_email_failing_fall_back_to_push_and_flag_the_case(boot):
    faults.turn_on("sms_down")
    faults.turn_on("email_down")
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    status, money, _decision, _ = _state(case_id)
    assert (status, money) == ("waiting_customer", "held")
    messages = " ".join(event[5] for event in _events(case_id))
    assert "SMS failed" in messages and "Email failed" in messages
    assert "flagged for a desk analyst" in messages
    with session_scope() as session:
        channels = {row.channel for row in session.query(Notification).filter(Notification.case_id == case_id)}
    # The app inbox still reached the customer. Nothing was recorded as sent by SMS or email.
    assert channels == {"push"}


def test_one_channel_failing_names_the_other_two(boot):
    faults.turn_on("sms_down")
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    messages = " ".join(event[5] for event in _events(case_id))
    assert "SMS failed after a retry. The customer is reached by push and email." in messages
    with session_scope() as session:
        channels = {row.channel for row in session.query(Notification).filter(Notification.case_id == case_id)}
    assert channels == {"push", "email"}


# Customer unreachable

def test_a_silent_customer_is_reminded_then_escalated_and_the_money_stays_held(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    assert realert_case(case_id)
    assert escalate_case(case_id)
    status, money, _decision, _ = _state(case_id)
    assert (status, money) == ("escalated", "held")
    with session_scope() as session:
        assert session.query(Report).filter(Report.case_id == case_id).count() == 1


# Agent failure

def test_a_crashing_agent_leaves_the_money_held_with_an_analyst(boot):
    faults.turn_on("fail_agent", "risk")
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    status, money, _decision, _ = _state(case_id)
    assert (status, money) == ("escalated", "held")
    events = _events(case_id)
    assert any(event[1] == "error" and event[0] == "risk" for event in events)
    assert any("a human analyst has the case" in event[5] for event in events)
    with session_scope() as session:
        outcome = session.query(Notification).filter(Notification.case_id == case_id, Notification.title == "Payment still on hold").count()
    assert outcome >= 1
    assert not faults.is_on("fail_agent"), "a drill crash fires once"


def test_a_crash_after_the_money_moved_does_not_undo_it(boot):
    case_id = trigger("cus_nimal", "routine_payment")
    begin_case(case_id)
    assert _state(case_id)[:2] == ("closed_cleared", "released")
    # The case keeps the status it had. The failure only adds an analyst follow-up.
    assert fail_safe(case_id, "The report agent failed.") == "closed_cleared"
    assert _state(case_id)[1] == "released"


def test_the_rule_explanation_survives_a_crash(boot):
    faults.turn_on("fail_agent", "alert")
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    _status, money, decision, reason = _state(case_id)
    assert money == "held" and decision == "hold"
    assert reason.startswith("Rule:") and "drill" not in reason


# Manual switch

def test_the_manual_block_switch_blocks_notifies_audits_and_files_a_receipt(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    with TestClient(app) as client:
        refused = client.post(f"/api/cases/{case_id}/kill", json={"action": "block", "reason": "no"}, headers=_auth())
        assert refused.status_code == 422  # reason too short
        done = client.post(f"/api/cases/{case_id}/kill", json={"action": "block", "reason": "Customer called the desk"}, headers=_auth())
        assert done.status_code == 200
        again = client.post(f"/api/cases/{case_id}/kill", json={"action": "block", "reason": "Customer called the desk"}, headers=_auth())
        assert again.status_code == 409
    status, money, _decision, _ = _state(case_id)
    assert (status, money) == ("closed_blocked", "blocked")
    with session_scope() as session:
        user = session.get(User, "cus_nimal")
        assert user.restricted is True
        titles = {row.title for row in session.query(Notification).filter(Notification.case_id == case_id)}
        assert session.query(Report).filter(Report.case_id == case_id).count() == 1
    assert "Payment blocked" in titles and any("receipt" in title.lower() for title in titles)
    assert any(event[0] == "analyst" and "manual block switch" in event[5] for event in _events(case_id))
    tools_seen = [event[2] for event in _events(case_id) if event[0] == "case"]
    assert tools_seen == ["review_notes", "generate_report", "deliver_report"]
    from app.models import AuditLog

    with session_scope() as session:
        assert session.query(AuditLog).filter(AuditLog.action == "kill_block", AuditLog.target == case_id).count() == 1


def test_a_manual_block_cannot_undo_a_release(boot):
    case_id = trigger("cus_nimal", "routine_payment")
    begin_case(case_id)
    with TestClient(app) as client:
        reply = client.post(f"/api/cases/{case_id}/kill", json={"action": "block", "reason": "changed my mind"}, headers=_auth())
    assert reply.status_code == 409
    assert _state(case_id)[1] == "released"


# The simulate panel

@pytest.fixture
def inline(monkeypatch):
    """Run scenario threads in the test, without waiting, so each outcome can be checked."""
    import app.simulate as simulate

    monkeypatch.setattr(simulate, "spawn_case", begin_case)
    monkeypatch.setattr(simulate, "_later", lambda work: work())
    monkeypatch.setattr(simulate.time, "sleep", lambda _seconds: None)
    return simulate


EXPECTED = {
    # scenario: (case status, money, rule decision)
    "sim_swap_transfer": ("waiting_customer", "held", "hold"),
    "port_out_critical": ("closed_blocked", "blocked", "block"),
    "routine_payment": ("closed_cleared", "released", "release"),
    "customer_silent": ("escalated", "held", "hold"),
    "models_down": ("waiting_customer", "held", "hold"),
    "channel_failure": ("waiting_customer", "held", "hold"),
    "district_spike": ("waiting_customer", "held", "hold"),
    "agent_failure": ("escalated", "held", None),
    "manual_freeze": ("closed_blocked", "blocked", "hold"),
}


@pytest.mark.parametrize("scenario", sorted(EXPECTED))
def test_each_scenario_button_drives_its_real_outcome(boot, inline, scenario):
    with TestClient(app) as client:
        reply = client.post("/api/simulate/run", json={"scenario": scenario}, headers=_auth())
    assert reply.status_code == 200
    case_id = reply.json()["case_id"]
    status, money, decision, _reason = _state(case_id)
    assert (status, money, decision) == EXPECTED[scenario]
    from app.models import AuditLog

    with session_scope() as session:
        assert session.query(AuditLog).filter(AuditLog.action == "simulate_" + scenario, AuditLog.target == case_id).count() == 1


def test_the_simulate_endpoints_are_for_the_right_people(boot, inline):
    from app.emergency import SPIKE_COUNT

    with TestClient(app) as client:
        listing = client.get("/api/simulate", headers=_auth("usr_auditor", "auditor"))
        assert listing.status_code == 200 and len(listing.json()["customers"]) >= 10
        assert {item["id"] for item in listing.json()["scenarios"]} == set(EXPECTED)
        assert client.get("/api/simulate", headers=_auth("cus_nimal", "customer")).status_code == 403
        assert client.post("/api/simulate/run", json={"scenario": "models_down"}, headers=_auth("usr_auditor", "auditor")).status_code == 403
        assert client.post("/api/simulate/run", json={"scenario": "nope"}, headers=_auth()).status_code == 404
        assert client.post("/api/simulate/clear", headers=_auth("usr_auditor", "auditor")).status_code == 403
        client.post("/api/simulate/run", json={"scenario": "district_spike"}, headers=_auth())
        client.post("/api/simulate/run", json={"scenario": "models_down"}, headers=_auth())
        assert set(client.get("/api/emergency", headers=_auth()).json()["faults"]) == {"llm_down"}
        cleared = client.post("/api/simulate/clear", headers=_auth()).json()
        assert cleared["drill_payments_removed"] == SPIKE_COUNT and cleared["faults_off"] == ["llm_down"]
        after = client.get("/api/emergency", headers=_auth()).json()
        assert after["active"] == {} and after["faults"] == {}


def test_clearing_drills_leaves_an_analysts_own_emergency_switch_alone(boot, inline):
    force_emergency("Kandy", "fraud ring", by="analyst@payshield.lk")
    with TestClient(app) as client:
        client.post("/api/simulate/clear", headers=_auth())
    assert "Kandy" in breaker_status()["active"]


def test_reset_switches_every_drill_off(boot):
    faults.turn_on("llm_down")
    faults.turn_on("sms_down")
    assert reset_demo()["faults_cleared"] == ["llm_down", "sms_down"]
    assert faults.snapshot() == {}
