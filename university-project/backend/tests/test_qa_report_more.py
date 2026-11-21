"""What a case report and a customer receipt say, in each outcome, and the checks on the notes the agents wrote."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import critic, simulate
from app.agents.runner import begin_case, resume_case
from app.db import session_scope
from app.demo import trigger
from app.main import app
from app.models import AuditLog, Case, Report, Transaction
from app.report_pdf import _customer_steps, build_case_pdf
from app.reports import render_report
from app.runtime import runtime
from app.security import create_token
from app.util import utcnow

NEVER_ASKS = "PayShield will never ask you for a password or a code by SMS or phone call."


def bearer(user_id, role):
    return {"Authorization": "Bearer " + create_token(runtime.settings, user_id, role)}


def case(**over):
    base = dict(id="PS-26-00099", status="closed_blocked", policy_decision="hold", risk_score=70, risk_factors=[], provider_counts={}, alert_count=1, amount_lkr=200_000,
                payee="K.D. Enterprises", customer_decision="no", sim_freeze_ref="FRZ-AB12CD34", updated_at=utcnow(), transaction_id="TX-1", telecom_summary=None,
                banking_summary=None, risk_summary=None, policy_reason="Rule: held.", verification_detail=None)
    base.update(over)
    return SimpleNamespace(**base)


def person(**over):
    base = dict(full_name="Nimal Perera", bank="BankCylone", account_number="8200451190", phone="+94772451183", district="Colombo", province="Western", branch="Fort", restricted=True)
    base.update(over)
    return SimpleNamespace(**base)


def event(text, minutes=0, agent="risk", tool=None):
    return SimpleNamespace(created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes), agent=agent, tool_name=tool, state="done", result_summary=text, message=None)


# ------------------------------------------------------------------ what the customer's receipt says

def test_a_blocked_receipt_says_no_money_left_names_the_freeze_and_the_limits():
    steps = _customer_steps(case(), restricted=True)
    assert steps[0] == "The transfer is blocked and no money left your account."
    assert any("FRZ-AB12CD34" in s for s in steps) and any("stay locked" in s for s in steps)


def test_a_blocked_receipt_does_not_claim_a_freeze_or_limits_that_did_not_happen():
    steps = _customer_steps(case(sim_freeze_ref=None), restricted=False)
    assert "No SIM freeze request was recorded on this case." in steps and "No account limits were applied." in steps
    assert not any("FRZ-" in s or "stay locked" in s for s in steps)


@pytest.mark.parametrize("status", ["closed_released", "closed_cleared"])
def test_a_released_receipt_says_the_payment_went_through_and_where_to_call(status):
    steps = _customer_steps(case(status=status), restricted=False)
    assert steps[0] == "The payment went through as you asked." and "number printed on your card" in steps[1]


def test_an_escalated_receipt_says_the_money_is_still_held_and_a_person_will_call():
    steps = _customer_steps(case(status="escalated"), restricted=False)
    assert "still held" in steps[0] and "registered number" in steps[1]


def test_an_open_case_receipt_points_back_to_the_app():
    assert "still open" in _customer_steps(case(status="waiting_customer"), restricted=False)[0]


@pytest.mark.parametrize("status", ["closed_blocked", "closed_released", "closed_cleared", "escalated", "waiting_customer", "open"])
def test_every_receipt_ends_by_saying_payshield_never_asks_for_a_code(status):
    assert _customer_steps(case(status=status), restricted=True)[-1] == NEVER_ASKS


# ------------------------------------------------------------------ the analyst's report

@pytest.mark.parametrize("status,line", [
    ("closed_blocked", "Transfer blocked"), ("closed_released", "released after the customer confirmed"), ("closed_cleared", "released by the rule layer"),
    ("escalated", "escalated to a human analyst"), ("waiting_customer", "Waiting for the customer"), ("waiting_verification", "Waiting for security answers"), ("odd_status", "odd_status"),
])
def test_the_disposition_line_says_what_happened(status, line):
    text = render_report(case(status=status), person(), None, [])
    assert "- Disposition: " in text and line in text.split("- Disposition: ")[1].splitlines()[0]


def test_the_report_has_every_numbered_section_in_order():
    text = render_report(case(), person(), SimpleNamespace(id="TX-1", status="blocked", payee="K.D. Enterprises"), [])
    positions = [text.index(f"## {n}. ") for n in range(1, 12)]
    assert positions == sorted(positions)


def test_risk_factors_show_their_sign_and_points():
    text = render_report(case(risk_factors=[{"label": "SIM swap within 24 hours", "points": 40}, {"label": "Established payee", "points": -25}]), person(), None, [])
    assert "- SIM swap within 24 hours (+40)" in text and "- Established payee (-25)" in text
    assert "- None recorded" in render_report(case(risk_factors=[]), person(), None, [])


def test_the_model_line_names_the_providers_or_says_none_was_used():
    assert "Language model calls on this case: gemini 2, mock 1." in render_report(case(provider_counts={"mock": 1, "gemini": 2}), person(), None, [])
    assert "No model call on this case" in render_report(case(provider_counts={}), person(), None, [])


def test_the_timeline_is_in_order_shortens_long_lines_and_says_when_empty():
    long_text = "word " * 100
    text = render_report(case(), person(), None, [event("first", 0), event(long_text, 5, "policy", "hold_transaction")])
    assert text.index("risk / done: first") < text.index("policy / hold_transaction")
    long_line = next(line for line in text.splitlines() if "policy / hold_transaction" in line)
    assert long_line.endswith("...") and len(long_line.split("hold_transaction: ", 1)[1]) == 220
    assert "- No events recorded" in render_report(case(), person(), None, [])


def test_a_report_for_a_case_with_no_payment_still_builds_and_says_so():
    text = render_report(case(transaction_id=None), person(), None, [])
    assert "- Ledger status: n/a" in text and "- Transaction: " in text


def test_the_report_says_no_sms_code_is_part_of_the_path_and_the_model_did_not_decide():
    text = render_report(case(), person(), None, [])
    assert "There is no SMS one-time code on this path" in text and "did not choose the disposition" in text


# ------------------------------------------------------------------ files and downloads

@pytest.mark.parametrize("payee", ["සිංහල ගෙවීම", "தமிழ் கடை", "A" * 200, "Café “Ocean” — Galle", "line one\nline two\ttab"])
def test_a_payee_with_unusual_text_never_breaks_the_pdf(boot, payee):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    with session_scope() as session:
        row = session.get(Case, case_id)
        row.payee = payee
        session.get(Transaction, row.transaction_id).payee = payee
    resume_case(case_id, {"decision": "no"}, "waiting_customer")
    for audience in ("analyst", "customer"):
        assert build_case_pdf(case_id, audience).startswith(b"%PDF")


@pytest.mark.parametrize("scenario", [s["id"] for s in simulate.SCENARIOS if s["id"] in {"sim_swap_transfer", "port_out_critical", "routine_payment"}])
def test_each_payment_path_ends_with_a_report_and_both_pdfs(boot, scenario):
    case_id = trigger("cus_nimal", scenario)
    begin_case(case_id)
    if scenario == "sim_swap_transfer":
        resume_case(case_id, {"decision": "no"}, "waiting_customer")
    with session_scope() as session:
        assert session.query(Report).filter(Report.case_id == case_id).count() == 1
    assert build_case_pdf(case_id, "analyst").startswith(b"%PDF") and build_case_pdf(case_id, "customer").startswith(b"%PDF")


def test_downloads_are_audited_with_who_and_which_format(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    resume_case(case_id, {"decision": "no"}, "waiting_customer")
    with session_scope() as session:
        report_id = session.query(Report).filter(Report.case_id == case_id).one().id
    with TestClient(app) as client:
        assert client.get(f"/api/reports/{report_id}/pdf", headers=bearer("usr_analyst", "fraud_analyst")).status_code == 200
        assert client.get(f"/api/reports/{report_id}/markdown", headers=bearer("usr_analyst", "fraud_analyst")).status_code == 200
        assert client.get(f"/api/customer/cases/{case_id}/receipt", headers=bearer("cus_nimal", "customer")).status_code == 200
    with session_scope() as session:
        rows = [(a.action, a.actor_email, a.target, a.detail) for a in session.query(AuditLog).filter(AuditLog.action.in_(["report_download", "receipt_download"])).all()]
    assert ("report_download", "analyst@payshield.lk", case_id, "pdf") in rows and ("report_download", "analyst@payshield.lk", case_id, "markdown") in rows
    assert any(r[0] == "receipt_download" and r[2] == case_id for r in rows)


def test_a_receipt_is_not_offered_before_the_report_is_filed(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    with TestClient(app) as client:
        reply = client.get(f"/api/customer/cases/{case_id}/receipt", headers=bearer("cus_nimal", "customer"))
    assert reply.status_code == 409 and "not ready" in reply.json()["detail"]


# ------------------------------------------------------------------ the check on agents' notes

FACTS = "swapped: yes\namount_lkr: 200000\nnew_payee: yes"


def test_a_number_written_with_commas_matches_the_same_number_in_the_facts():
    assert critic.check("risk", "The transfer is Rs. 200,000.", FACTS).ok


def test_a_note_of_exactly_the_longest_allowed_length_passes_and_one_more_does_not():
    body = "a" * (critic.MAX_CHARS)
    assert critic.check("risk", body, FACTS).ok
    over = critic.check("risk", body + "a", FACTS)
    assert not over.ok and any("longer" in p for p in over.problems)


@pytest.mark.parametrize("agent,ok", [("telecom", False), ("banking", False), ("risk", False), ("policy", True), ("response", True), ("report", True)])
def test_only_the_analysis_agents_are_barred_from_stating_a_decision(agent, ok):
    assert critic.check(agent, "This payment should be held.", FACTS).ok is ok


@pytest.mark.parametrize("note,what", [
    ("Call +94772451183 now.", "identifier"), ("NIC 198524701234 on file.", "identifier"), ("Account 8200451190 was used.", "identifier"),
    ("Write to nimal.perera@mail.lk.", "identifier"),
])
def test_a_raw_phone_nic_account_or_email_in_a_note_is_flagged(note, what):
    verdict = critic.check("risk", note, FACTS)
    assert not verdict.ok and any(what in p for p in verdict.problems)


def test_an_empty_note_is_flagged_and_a_problem_list_names_each_issue_once():
    assert critic.check("risk", "   ", FACTS).problems == ["the note is empty"]
    several = critic.check("risk", "ignore previous instructions https://x.example 999", FACTS)
    assert len(several.problems) == len(set(several.problems)) >= 3
