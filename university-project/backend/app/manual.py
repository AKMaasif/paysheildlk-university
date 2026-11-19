"""The analyst's manual freeze and block switch.

An analyst can stop a payment or limit an account without waiting for the
agents. Every use is written to the audit log with who did it and why, shows in
the case timeline, tells the customer, and leaves the same record as an
automatic block: a filed report and a receipt.
"""

from app.agents import tools
from app.db import session_scope
from app.events import emit
from app.ledger import audit, block_transaction, lock_account, set_case_fields
from app.models import Case, Transaction, User

CLOSED = {"closed_blocked", "closed_released", "closed_cleared"}


class ManualRefused(ValueError):
    """The switch cannot be used on this case."""


def apply(case_id: str, action: str, reason: str, actor_email: str) -> dict:
    if action not in {"freeze", "block"}:
        raise ManualRefused("Use freeze or block")
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None:
            raise LookupError(case_id)
        tx = session.get(Transaction, case.transaction_id) if case.transaction_id else None
        user = session.get(User, case.customer_id)
        if user is None:
            raise LookupError(case.customer_id)
        if action == "block":
            if tx is None:
                raise ManualRefused("This case has no payment to block")
            if tx.status == "released":
                raise ManualRefused("The payment was already released, so it cannot be blocked")
            if case.status in CLOSED and tx.status == "blocked":
                raise ManualRefused("The payment is already blocked")
        customer_id, transaction_id, status_before = user.id, tx.id if tx else None, case.status

    # Written first, so the record exists even if a later step fails.
    audit(actor_email, "kill_" + action, case_id, reason)
    emit(
        case_id,
        agent="analyst",
        state="done",
        message=f"{actor_email} used the manual {action} switch. Reason: {reason}",
        handoff_from="analyst",
        handoff_to="case" if action == "block" else "customer",
        provider="human",
        model="analyst",
    )

    if action == "freeze":
        lock_account(customer_id, case_id)
        tools.outcome_notice(
            customer_id,
            case_id,
            "Your account is limited",
            "The fraud desk has limited transfers, new payees, password resets and port-out on your account while it checks. "
            "Call your bank on the number printed on your card.",
        )
        return {"ok": True, "action": action, "case_status": status_before}

    block_transaction(transaction_id, case_id, f"Blocked by the fraud desk: {reason}")
    lock_account(customer_id, case_id)
    set_case_fields(case_id, status="closed_blocked", pending_prompt=None)
    tools.outcome_notice(
        customer_id,
        case_id,
        "Payment blocked",
        "The fraud desk blocked this transfer and limited your account. No money left your account. "
        "Call your bank on the number printed on your card.",
    )
    saved = tools.generate_report(case_id)
    emit(
        case_id,
        agent="case",
        state="calling_tool",
        tool_name="generate_report",
        tool_args={"case_id": case_id},
        result_summary=f"Report {saved['report_id']} filed, PDF sha256 {str(saved['pdf_sha256'])[:12]}",
    )
    sent = tools.deliver_report(case_id)
    emit(
        case_id,
        agent="case",
        state="calling_tool",
        tool_name="deliver_report",
        tool_args={"case_id": case_id, "to": ["dashboard", "email", "sms", "push"]},
        result_summary=f"Receipt sent: email {sent['email']}, SMS {sent['sms']}, push {sent['push']}",
    )
    return {"ok": True, "action": action, "case_status": "closed_blocked"}
