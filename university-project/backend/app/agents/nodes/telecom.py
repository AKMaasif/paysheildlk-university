"""The telecom step(s) of the case graph (split from graph.py)."""
import logging

from app.agents import tools
from app.agents.base import CaseState, _investigate, _note, _receive, _send, pace
from app.events import emit
from app.ledger import set_case_fields
from app.util import utcnow

log = logging.getLogger("payshield.graph")


def telecom(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    phone = state["phone"]
    _receive(state, "telecom", "telecom", ("supervisor",))
    emit(case_id, agent="telecom", state="thinking", message="Reading the SIM profile for this number.")
    calls = {
        "get_sim_swap_status": lambda a: tools.get_sim_swap_status(a["phoneNumber"], a["maxAge"]),
        "get_device_change": lambda a: tools.get_device_change(a["phoneNumber"]),
        "get_location_events": lambda a: tools.get_location_events(a["phoneNumber"]),
        "get_port_out_status": lambda a: tools.get_port_out_status(a["phoneNumber"]),
        "get_sim_history": lambda a: tools.get_sim_history(a["phoneNumber"]),
    }
    args = {
        "get_sim_swap_status": {"phoneNumber": phone, "maxAge": 240},
        "get_device_change": {"phoneNumber": phone},
        "get_location_events": {"phoneNumber": phone},
        "get_port_out_status": {"phoneNumber": phone},
        "get_sim_history": {"phoneNumber": phone},
    }
    seen = _investigate(case_id, "telecom", calls, args)["seen"]
    swap, device, location, port = (seen[k] for k in ("get_sim_swap_status", "get_device_change", "get_location_events", "get_port_out_status"))
    swapped = "yes" if (swap.get("checkSimSwap") or {}).get("swapped") else "no"
    facts = (
        f"swapped: {swapped}\n"
        f"latestSimChange: {(swap.get('retrieveSimSwapDate') or {}).get('latestSimChange')}\n"
        f"device events: {len(device.get('events') or [])}\n"
        f"location events: {len(location.get('events') or [])}\n"
        f"port-out events: {len(port.get('events') or [])}\n"
    )
    if "get_sim_history" in seen:
        history = seen["get_sim_history"]
        facts += f"sim events in 30 days: {len(history.get('events') or [])} (swaps: {history.get('swaps_30d')})\n"
    note = _note(case_id, "telecom", facts, handoff_to="risk")
    set_case_fields(case_id, telecom_summary=note["text"])
    # What this agent actually saw goes to the risk step in the signed handoff, so the score uses the same observation.
    saw = {
        "swapped": swapped,
        "source": swap.get("source"),
        "sim_swap": {
            "source": swap.get("source"),
            "swapped": swapped == "yes",
            "latest_sim_change": (swap.get("retrieveSimSwapDate") or {}).get("latestSimChange"),
            "fetched_at": utcnow().isoformat(),
        },
    }
    return {"telecom_summary": note["text"], **_send(case_id, "telecom", "risk", saw)}
