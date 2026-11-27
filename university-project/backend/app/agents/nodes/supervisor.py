"""The supervisor step(s) of the case graph (split from graph.py)."""
import logging

from langgraph.types import Send

from app.agents import tools
from app.agents.base import CaseState, _merge_inbox, _receive, _send, _tool, pace
from app.events import emit
from app.ledger import set_case_fields

log = logging.getLogger("payshield.graph")


def supervisor(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    emit(case_id, agent="supervisor", state="thinking", message="Opening the case and assigning the two feeds.")
    _tool(case_id, "supervisor", "create_case", {"case_id": case_id}, lambda: tools.create_case(case_id))
    emit(
        case_id,
        agent="supervisor",
        state="done",
        message="Telecom and banking look at the same case in parallel. The route is fixed.",
        handoff_from="supervisor",
        handoff_to="telecom",
        provider="rules",
        model="deterministic",
    )
    emit(
        case_id,
        agent="supervisor",
        state="done",
        handoff_from="supervisor",
        handoff_to="banking",
        message="Banking agent assigned.",
        provider="rules",
        model="deterministic",
    )
    work = {"phone_tail": state["phone"][-2:], "transaction_id": state["transaction_id"]}
    to_telecom = _send(case_id, "supervisor", "telecom", work)["_inbox"]
    to_banking = _send(case_id, "supervisor", "banking", work)["_inbox"]
    return {"_inbox": _merge_inbox(to_telecom, to_banking)}


def fanout(state: CaseState):
    return [Send("telecom", state), Send("banking", state)]


def _waited() -> str:
    """The time the customer was given, in words, from the setting that was actually used."""
    from app.runtime import runtime

    seconds = int(runtime.settings.escalate_seconds)
    return f"{seconds} seconds" if seconds < 120 else f"{round(seconds / 60)} minutes"


def escalate(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _receive(state, "escalate", "supervisor", ("wait_customer", "verify_wait"))
    emit(
        case_id,
        agent="supervisor",
        state="done",
        message=(
            "The face check could not settle who this is. A human analyst has the case. The transfer stays held."
            if state.get("escalation_reason") == "face"
            else f"No customer response inside {_waited()}. A human analyst has the case. The transfer stays held."
        ),
        handoff_from="alert",
        handoff_to="case",
        provider="rules",
        model="deterministic",
    )
    set_case_fields(
        case_id,
        status="escalated",
        customer_decision="timeout",
        pending_prompt=None,
    )
    tools.outcome_notice(
        state["customer_id"],
        case_id,
        "Payment still on hold",
        (
            "We could not confirm your identity from the face check. The payment is still held and a person on the fraud desk will review it."
            if state.get("escalation_reason") == "face"
            else "We did not get an answer in time. The payment is still held and a person on the fraud desk has the case."
        ),
    )
    return {"final_status": "escalated", "timed_out": True, "customer_decision": "timeout", **_send(case_id, "escalate", "report", {"final_status": "escalated"})}
