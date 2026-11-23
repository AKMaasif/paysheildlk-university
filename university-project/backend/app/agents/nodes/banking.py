"""The banking step(s) of the case graph (split from graph.py)."""
import logging

from app.agents import tools
from app.agents.base import CaseState, _investigate, _note, _receive, _send, pace
from app.events import emit
from app.ledger import set_case_fields
from app.privacy import untrusted
from app.util import lkr

log = logging.getLogger("payshield.graph")


def banking(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _receive(state, "banking", "banking", ("supervisor",))
    emit(case_id, agent="banking", state="thinking", message="Reading recent account activity.")
    customer, payee = state["customer_id"], state["payee"]
    calls = {
        "get_recent_transactions": lambda a: tools.get_recent_transactions(a["customer_id"]),
        "get_customer_profile": lambda a: tools.get_customer_profile(a["customer_id"], a["payee"], a["amount_lkr"]),
        "get_payee_history": lambda a: tools.get_payee_history(a["customer_id"], a["payee"]),
        "get_credential_events": lambda a: tools.get_credential_events(a["customer_id"]),
    }
    args = {
        "get_recent_transactions": {"customer_id": customer},
        "get_customer_profile": {"customer_id": customer, "payee": payee, "amount_lkr": state["amount_lkr"]},
        "get_payee_history": {"customer_id": customer, "payee": payee},
        "get_credential_events": {"customer_id": customer},
    }
    seen = _investigate(case_id, "banking", calls, args)["seen"]
    activity = seen["get_recent_transactions"]
    new_payee = "yes" if any(item.get("new_payee") for item in activity["transactions"][:4]) else "no"
    facts = (
        f"amount: {lkr(state['amount_lkr'])}\n"
        f"payee: {untrusted(state['payee'])}\n"
        f"new_payee: {new_payee}\n"
        f"events: {len(activity['transactions'])}\n"
    )
    if "get_customer_profile" in seen:
        prof = seen["get_customer_profile"]
        facts += (
            f"usual payment high: {lkr(prof['usual_high_lkr'])} over {prof['payments']} earlier payments; "
            f"this payee paid {prof['payee_payments']} time(s) before, established: {'yes' if prof['payee_established'] else 'no'}\n"
        )
    if "get_payee_history" in seen:
        facts += f"earlier payments to this payee: {seen['get_payee_history']['earlier_payments']}\n"
    if "get_credential_events" in seen:
        facts += f"sign-ins and password resets in 48 hours: {len(seen['get_credential_events']['events'])}\n"
    note = _note(case_id, "banking", facts, handoff_to="risk")
    set_case_fields(case_id, banking_summary=note["text"])
    return {"banking_summary": note["text"], **_send(case_id, "banking", "risk", {"new_payee": new_payee})}
