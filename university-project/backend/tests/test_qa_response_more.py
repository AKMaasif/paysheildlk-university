"""The Response agent and the people who can overrule it: a supervisor's release or block, a late answer from the customer,
the analyst's switch, and what each outcome leaves behind."""
import pytest
from fastapi.testclient import TestClient

from app import manual
from app.agents.runner import begin_case, escalate_case, realert_case, resume_case
from app.db import session_scope
from app.demo import trigger
from app.main import app
from app.models import (
    AuditLog,
    Case,
    CaseEvent,
    Notification,
    Report,
    Transaction,
    User,
)
from app.runtime import runtime
from app.security import create_token


def bearer(user_id, role):
    return {"Authorization": "Bearer " + create_token(runtime.settings, user_id, role)}


SUPERVISOR = ("usr_supervisor", "bank_supervisor")
CUSTOMER = ("cus_nimal", "customer")


@pytest.fixture()
def held(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    return case_id


def snapshot(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        tx = session.get(Transaction, case.transaction_id)
        user = session.get(User, case.customer_id)
        return {"case": case.status, "tx": tx.status, "balance": user.balance_lkr, "amount": tx.amount_lkr, "tx_id": tx.id, "decision": case.customer_decision,
                "prompt": case.pending_prompt, "restricted": bool(user.restricted), "freeze": case.sim_freeze_ref}


def events(case_id, agent=None):
    with session_scope() as session:
        rows = session.query(CaseEvent).filter(CaseEvent.case_id == case_id).order_by(CaseEvent.created_at).all()
        return [(r.agent, r.state, r.message or r.result_summary or "") for r in rows if agent is None or r.agent == agent]


def override(client, tx_id, action, reason="Called the customer and confirmed"):
    return client.post(f"/api/transactions/{tx_id}/override", json={"action": action, "reason": reason}, headers=bearer(*SUPERVISOR))


# ------------------------------------------------------------------ a supervisor's decision

def test_a_supervisor_release_pays_once_closes_the_case_and_is_recorded(held):
    before = snapshot(held)
    with TestClient(app) as client:
        assert override(client, before["tx_id"], "release").status_code == 200
        after = snapshot(held)
        assert (after["case"], after["tx"], after["decision"], after["prompt"]) == ("closed_released", "released", "supervisor_release", None)
        assert after["balance"] == before["balance"] - before["amount"]
        assert override(client, before["tx_id"], "release").status_code == 409  # a released payment is no longer held
        assert snapshot(held)["balance"] == after["balance"]
    with session_scope() as session:
        entry = session.query(AuditLog).filter(AuditLog.action == "supervisor_override").one()
        assert entry.actor_email == "supervisor@payshield.lk" and "release" in entry.detail and "confirmed" in entry.detail
        assert session.get(Transaction, before["tx_id"]).detail["override"] == {"by": "supervisor@payshield.lk", "action": "release", "reason": "Called the customer and confirmed"}
    assert any("supervisor" in m.lower() for _a, _s, m in events(held, "supervisor"))


@pytest.mark.parametrize("action,title", [("release", "Payment released"), ("block", "Payment blocked")])
def test_after_a_supervisors_decision_the_customer_is_told_and_a_report_and_receipt_exist(held, action, title):
    tx_id = snapshot(held)["tx_id"]
    with TestClient(app) as client:
        assert override(client, tx_id, action).status_code == 200
        with session_scope() as session:
            notes = [n for n in session.query(Notification).filter(Notification.case_id == held).all() if n.title == title]
            assert notes and all("code" not in n.body.lower() and "http" not in n.body.lower() for n in notes)
            assert session.query(Report).filter(Report.case_id == held).count() == 1
        receipt = client.get(f"/api/customer/cases/{held}/receipt", headers=bearer(*CUSTOMER))
        assert receipt.status_code == 200 and receipt.content.startswith(b"%PDF")


def test_a_supervisor_block_keeps_the_money_and_closes_the_case(held):
    before = snapshot(held)
    with TestClient(app) as client:
        assert override(client, before["tx_id"], "block", "Payee is on a fraud list").status_code == 200
    after = snapshot(held)
    assert (after["case"], after["tx"], after["decision"], after["balance"]) == ("closed_blocked", "blocked", "supervisor_block", before["balance"])


@pytest.mark.parametrize("action,reason,status", [("delete", "a good reason", 400), ("", "a good reason", 400), ("release", "no", 422), ("release", "x" * 501, 422), ("release", "", 422)])
def test_a_bad_override_request_changes_nothing(held, action, reason, status):
    before = snapshot(held)
    with TestClient(app) as client:
        assert override(client, before["tx_id"], action, reason).status_code == status
    assert snapshot(held) == before


def test_only_a_held_payment_can_be_overruled(held):
    with TestClient(app) as client:
        assert override(client, "TX-none", "block").status_code == 409
        resume_case(held, {"decision": "no"}, "waiting_customer")  # the customer blocks it
        blocked = snapshot(held)
        assert override(client, blocked["tx_id"], "release").status_code == 409
    assert snapshot(held) == blocked


# ------------------------------------------------------------------ a late answer from the customer

def test_a_customer_who_answers_yes_after_a_supervisor_blocked_cannot_release_it_and_is_not_recorded_as_having_answered(held):
    tx_id = snapshot(held)["tx_id"]
    with TestClient(app) as client:
        assert override(client, tx_id, "block").status_code == 200
        answered_before = len(events(held, "customer"))
        late = client.post(f"/api/customer/cases/{held}/respond", json={"decision": "yes"}, headers=bearer(*CUSTOMER))
        assert late.status_code == 409
    assert snapshot(held)["tx"] == "blocked"
    assert len(events(held, "customer")) == answered_before, "a refused answer must not appear in the case timeline"


def test_a_customer_who_answers_no_after_a_supervisor_released_changes_nothing(held):
    tx_id = snapshot(held)["tx_id"]
    with TestClient(app) as client:
        override(client, tx_id, "release")
        done = snapshot(held)
        assert client.post(f"/api/customer/cases/{held}/respond", json={"decision": "no"}, headers=bearer(*CUSTOMER)).status_code == 409
    assert snapshot(held) == done


def test_reminders_and_escalation_do_nothing_for_a_case_a_person_has_closed(held):
    with TestClient(app) as client:
        override(client, snapshot(held)["tx_id"], "block")
    assert realert_case(held) is False and escalate_case(held) is False
    assert resume_case(held, {"decision": "yes"}, "waiting_customer") is False


# ------------------------------------------------------------------ what the customer's own "no" leaves behind

def test_the_customers_no_blocks_limits_the_account_asks_for_a_sim_freeze_notifies_and_files_a_receipt(held):
    before = snapshot(held)
    resume_case(held, {"decision": "no"}, "waiting_customer")
    after = snapshot(held)
    assert (after["case"], after["tx"], after["balance"]) == ("closed_blocked", "blocked", before["balance"])
    assert after["restricted"] is True and str(after["freeze"]).startswith("FRZ-")
    with session_scope() as session:
        titles = {n.title for n in session.query(Notification).filter(Notification.case_id == held).all()}
        assert any("block" in t.lower() for t in titles), titles
        assert session.query(Report).filter(Report.case_id == held).count() == 1
    with TestClient(app) as client:
        receipt = client.get(f"/api/customer/cases/{held}/receipt", headers=bearer(*CUSTOMER))
        assert receipt.status_code == 200 and receipt.content.startswith(b"%PDF")


def test_a_blocked_customer_is_told_in_plain_words_that_no_money_left_the_account(held):
    resume_case(held, {"decision": "no"}, "waiting_customer")
    with session_scope() as session:
        bodies = " ".join(n.body for n in session.query(Notification).filter(Notification.case_id == held, Notification.title.like("%lock%") | Notification.title.like("%block%")).all()).lower()
    assert "no money left your account" in bodies and "printed on your sim pack and your card" in bodies and "code" not in bodies


# ------------------------------------------------------------------ the analyst's switch

def test_the_switch_on_a_case_waiting_for_the_customer_closes_it_and_stops_the_customer_answering(held):
    manual.apply(held, "block", "Customer rang in", "analyst@payshield.lk")
    after = snapshot(held)
    assert (after["case"], after["tx"], after["prompt"]) == ("closed_blocked", "blocked", None)
    with TestClient(app) as client:
        assert client.post(f"/api/customer/cases/{held}/respond", json={"decision": "yes"}, headers=bearer(*CUSTOMER)).status_code == 409
    assert snapshot(held)["tx"] == "blocked"


def test_the_switch_leaves_a_human_step_in_the_timeline_and_an_audit_entry_with_the_reason(held):
    manual.apply(held, "freeze", "Precaution while we call them", "analyst@payshield.lk")
    human = [m for a, _s, m in events(held, "analyst")]
    assert human and "analyst@payshield.lk" in human[0] and "Precaution" in human[0]
    with session_scope() as session:
        entry = session.query(AuditLog).filter(AuditLog.action == "kill_freeze").one()
        assert entry.actor_email == "analyst@payshield.lk" and entry.target == held and "Precaution" in entry.detail


def test_a_freeze_limits_the_account_tells_the_customer_and_leaves_the_payment_held(held):
    manual.apply(held, "freeze", "Precaution", "analyst@payshield.lk")
    after = snapshot(held)
    assert after["restricted"] is True and after["tx"] == "held" and after["case"] == "waiting_customer"
    with session_scope() as session:
        assert any(n.title == "Your account is limited" for n in session.query(Notification).filter(Notification.case_id == held).all())


def test_freezing_twice_is_harmless(held):
    manual.apply(held, "freeze", "first", "a@b.lk")
    manual.apply(held, "freeze", "second", "a@b.lk")
    assert snapshot(held)["restricted"] is True


def test_the_switch_refuses_a_case_without_a_payment(held):
    with session_scope() as session:
        session.get(Case, held).transaction_id = None
    with pytest.raises(manual.ManualRefused, match="no payment"):
        manual.apply(held, "block", "nothing to stop", "a@b.lk")
    manual.apply(held, "freeze", "the account can still be limited", "a@b.lk")
