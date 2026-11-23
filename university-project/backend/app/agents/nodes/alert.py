"""The alert step(s) of the case graph (split from graph.py)."""
import logging

from app.agents import tools
from app.agents.base import CaseState, _note, _receive, _summarise, pace
from app.events import emit
from app.ledger import set_case_fields
from app.privacy import untrusted
from app.util import lkr, utcnow

log = logging.getLogger("payshield.graph")


def alert(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _sender, order = _receive(state, "alert", "alert", ("policy",))
    if order.get("decision") != "hold":
        raise PermissionError("alert only acts on a signed hold")
    emit(case_id, agent="alert", state="thinking", message="Telling the customer on the registered device.")
    amount = state["amount_lkr"]
    payee = state["payee"]
    question = f"Did you make this payment of {lkr(amount)} to {payee}?"
    from app.util import nid

    channels = ("send_push", "send_email", "send_sms")
    call_ids = {channel: nid("call") for channel in channels}
    for channel in channels:  # shown as in progress for as long as the providers take
        emit(case_id, agent="alert", state="calling_tool", tool_name=channel, tool_args={"user_id": state["customer_id"], "case_id": case_id},
             phase="started", call_id=call_ids[channel], meta={"planner": "workflow"})
    bundle = tools.alert_customer(state["customer_id"], case_id, amount, payee)
    for channel, payload in (("send_push", bundle["push"]), ("send_email", bundle["email"]), ("send_sms", bundle["sms"])):
        failed_send = payload.get("status") == "failed"
        emit(
            case_id,
            agent="alert",
            state="calling_tool",
            tool_name=channel,
            tool_args={"user_id": state["customer_id"], "case_id": case_id},
            result_summary=_summarise(channel, payload),
            latency_ms=payload.get("latency_ms"),
            phase="failed" if failed_send else "finished",
            call_id=call_ids[channel],
            result=payload,
            meta={"planner": "workflow", "attempts": payload.get("attempts", 1), "delivery_state": (payload.get("delivery") or {}).get("state")},
        )
    failed = [label for label, payload in (("SMS", bundle["sms"]), ("Email", bundle["email"])) if payload.get("status") == "failed"]
    if len(failed) == 2:
        emit(
            case_id,
            agent="alert",
            state="done",
            message="SMS and email both failed after a retry. The customer can only be reached in the app, so the case is flagged for a desk analyst. The payment stays held.",
            handoff_to="customer",
            provider="rules",
            model="deterministic",
        )
    elif failed:
        other = "email" if failed[0] == "SMS" else "SMS"
        emit(
            case_id,
            agent="alert",
            state="done",
            message=f"{failed[0]} failed after a retry. The customer is reached by push and {other}.",
            handoff_to="customer",
            provider="rules",
            model="deterministic",
        )
    now = utcnow()
    prompt = {
        "type": "payment_confirmation",
        "question": question,
        "amount_lkr": amount,
        "payee": payee,
    }
    set_case_fields(
        case_id,
        status="waiting_customer",
        waiting_since=now,
        last_alert_at=now,
        alert_count=1,
        pending_prompt=prompt,
    )
    _note(case_id, "alert", f"question: Did you make this payment of {lkr(amount)} to {untrusted(payee)}?\nchannels: push, email, sms", handoff_to="customer")
    emit(
        case_id,
        agent="alert",
        state="waiting_for_customer",
        message="Waiting for the customer on the registered device.",
        handoff_from="alert",
        handoff_to="customer",
    )
    return {}
