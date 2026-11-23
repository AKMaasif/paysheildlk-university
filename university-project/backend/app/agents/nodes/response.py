"""The response step(s) of the case graph (split from graph.py)."""
import logging

from app.agents import tools
from app.agents.base import CaseState, _note, _receive, _send, _tool, pace
from app.events import emit
from app.ledger import set_case_fields
from app.notify import BLOCKED_SMS

log = logging.getLogger("payshield.graph")


def response(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _receive(state, "response", "response", ("policy", "wait_customer", "verify_wait"))
    reason = state.get("verification_detail") or state.get("policy_reason") or "Customer did not accept the payment."
    if state.get("customer_decision") == "no":
        reason = "Customer said they did not make this payment."
    emit(case_id, agent="response", state="thinking", message="Blocking the transfer and recording a SIM freeze request (simulated: no operator is contacted).")
    _tool(
        case_id,
        "response",
        "block_transaction",
        {"transaction_id": state["transaction_id"], "case_id": case_id},
        lambda: tools.block_transaction(state["transaction_id"], case_id, reason),
    )
    freeze = _tool(
        case_id,
        "response",
        "request_sim_freeze",
        {"phoneNumber": state["phone"], "case_id": case_id},
        lambda: tools.request_sim_freeze(state["phone"], case_id, reason),
    )
    _tool(
        case_id,
        "response",
        "lock_account",
        {"customer_id": state["customer_id"], "case_id": case_id},
        lambda: tools.lock_account(state["customer_id"], case_id),
    )
    set_case_fields(
        case_id,
        status="closed_blocked",
        customer_decision=state.get("customer_decision"),
        sim_freeze_ref=freeze.get("reference"),
        pending_prompt=None,
    )
    sms = None if state.get("customer_decision") else BLOCKED_SMS
    tools.outcome_notice(
        state["customer_id"],
        case_id,
        "Payment blocked",
        f"The transfer is blocked and no money left your account. A SIM freeze request was recorded, reference {freeze.get('reference')}, "
        "but your mobile operator has not been told. Call your operator and your bank on the numbers printed on your SIM pack and your card.",
        sms_body=sms,
    )
    _note(case_id, "response", f"reason: {reason}\nfreeze: {freeze.get('reference')}", handoff_to="case")
    return {"final_status": "closed_blocked", **_send(case_id, "response", "report", {"final_status": "closed_blocked"})}
