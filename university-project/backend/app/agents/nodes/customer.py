"""The customer step(s) of the case graph (split from graph.py)."""
import logging

from langgraph.types import interrupt

from app.agents.base import CaseState, _send
from app.util import lkr

log = logging.getLogger("payshield.graph")


def wait_customer(state: CaseState) -> dict:
    # interrupt() pauses the graph. On resume the node starts again and interrupt() returns the answer.
    payload = interrupt(
        {
            "type": "payment_confirmation",
            "case_id": state["case_id"],
            "amount_lkr": state["amount_lkr"],
            "payee": state["payee"],
            "question": f"Did you make this payment of {lkr(state['amount_lkr'])} to {state['payee']}?",
        }
    )
    decision = payload.get("decision") if isinstance(payload, dict) else str(payload)
    timed_out = decision == "timeout"
    target = "escalate" if timed_out else ("verification_prep" if decision == "yes" else "response")
    signed = _send(state["case_id"], "wait_customer", target, {"customer_decision": decision})
    return {"customer_decision": decision, "timed_out": timed_out, **signed}


def route_customer(state: CaseState) -> str:
    if state.get("timed_out") or state.get("customer_decision") == "timeout":
        return "escalate"
    if state.get("customer_decision") == "yes":
        return "verification_prep"
    return "response"
