from datetime import timedelta

from helpers import live_selfie

from app.agents.runner import begin_case, process_timeouts, resume_case
from app.db import session_scope
from app.demo import trigger
from app.models import Case, Notification, Report, Transaction, User
from app.reports import render_pdf
from app.util import aware


def _case(case_id):
    with session_scope() as session:
        case = session.get(Case, case_id)
        tx = session.get(Transaction, case.transaction_id)
        user = session.get(User, case.customer_id)
        report = session.query(Report).filter(Report.case_id == case_id).one_or_none()
        session.expunge(case)
        session.expunge(tx)
        session.expunge(user)
        if report is not None:
            session.expunge(report)
        return case, tx, user, report


def test_customer_says_no_blocks_and_files_a_report(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    case, tx, _user, _report = _case(case_id)
    assert case.status == "waiting_customer"
    assert tx.status == "held"
    assert case.policy_decision == "hold"
    assert case.risk_score >= 45

    assert resume_case(case_id, {"decision": "no"}, "waiting_customer")
    case, tx, user, report = _case(case_id)
    assert case.status == "closed_blocked"
    assert tx.status == "blocked"
    assert case.sim_freeze_ref
    assert user.restricted is True
    assert "transfer" in (user.restricted_actions or [])
    assert report is not None
    assert "Nimal Perera" in report.markdown
    assert "200,000" in report.markdown
    assert "rule layer" in report.markdown.lower()
    assert case.sim_freeze_ref in report.markdown
    assert report.markdown.encode("utf-8")
    assert render_pdf(report.markdown).startswith(b"%PDF")


def test_customer_says_yes_and_passes_verification(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    assert resume_case(case_id, {"decision": "yes"}, "waiting_customer")
    case, tx, _user, _report = _case(case_id)
    assert case.status == "waiting_liveness"
    assert tx.status == "held"
    assert resume_case(case_id, live_selfie(), "waiting_liveness")
    case, tx, _user, _report = _case(case_id)
    assert case.status == "waiting_verification"
    prompts = case.pending_prompt["questions"]
    from app.seed import PILOT_ANSWERS

    answers = PILOT_ANSWERS["cus_nimal"]
    given = {item["id"]: answers[item["id"]] for item in prompts}

    assert resume_case(case_id, {"answers": given}, "waiting_verification")
    case, tx, user, report = _case(case_id)
    assert case.status == "closed_released"
    assert case.verification_passed is True
    assert tx.status == "released"
    assert user.restricted is False
    assert "released" in report.markdown.lower()
    assert "matched" in (case.verification_detail or "").lower()


def test_wrong_security_answers_block_the_payment(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    assert resume_case(case_id, {"decision": "yes"}, "waiting_customer")
    assert resume_case(case_id, live_selfie(), "waiting_liveness")
    for _ in range(4):
        case, _tx, _user, _report = _case(case_id)
        assert case.status == "waiting_verification"
        prompts = case.pending_prompt["questions"]
        given = {item["id"]: "not-the-answer" for item in prompts}
        assert "wrong" not in (case.pending_prompt.get("notice") or "").lower()
        assert resume_case(case_id, {"answers": given}, "waiting_verification")
    case, tx, user, report = _case(case_id)
    assert case.status == "closed_blocked"
    assert case.verification_passed is False
    assert tx.status == "blocked"
    assert user.restricted is True
    assert "blocked" in report.markdown.lower()


def test_timeout_realerts_then_escalates_and_keeps_the_hold(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    case, tx, _user, _report = _case(case_id)
    assert case.status == "waiting_customer"
    assert case.alert_count == 1
    started = aware(case.waiting_since)

    first = process_timeouts(started + timedelta(seconds=181))
    assert first["realert"] == 1
    case, tx, _user, _report = _case(case_id)
    assert case.status == "waiting_customer"
    assert case.alert_count == 2
    assert tx.status == "held"

    second = process_timeouts(started + timedelta(seconds=601))
    assert second["escalate"] == 1
    case, tx, _user, report = _case(case_id)
    assert case.status == "escalated"
    assert tx.status == "held"
    assert report is not None
    assert "held" in report.markdown.lower()
    with session_scope() as session:
        notes = session.query(Notification).filter(Notification.case_id == case_id).all()
        channels = {note.channel for note in notes}
    assert {"push", "email", "sms"} <= channels


def test_routine_payment_is_released_without_asking(boot):
    case_id = trigger("cus_dilani", "routine_payment")
    begin_case(case_id)
    case, tx, user, report = _case(case_id)
    assert case.status == "closed_cleared"
    assert case.policy_decision == "release"
    assert tx.status == "released"
    assert user.restricted is False
    assert case.alert_count == 0
    assert report is not None


def test_critical_port_out_blocks_without_waiting(boot):
    case_id = trigger("cus_nimal", "port_out_critical")
    begin_case(case_id)
    case, tx, user, report = _case(case_id)
    assert case.policy_decision == "block"
    assert case.status == "closed_blocked"
    assert tx.status == "blocked"
    assert case.customer_decision is None
    assert case.sim_freeze_ref
    assert user.restricted is True
    assert "port-out" in report.markdown.lower() or "blocked" in report.markdown.lower()
