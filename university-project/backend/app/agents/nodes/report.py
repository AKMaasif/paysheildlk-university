"""The report step(s) of the case graph (split from graph.py)."""
import logging

from app.agents import tools
from app.agents.base import CaseState, _note, _receive, _tool, pace
from app.events import emit

log = logging.getLogger("payshield.graph")


def report(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _receive(state, "report", "case", ("release", "response", "escalate"))
    emit(case_id, agent="case", state="thinking", message="Writing the incident report.")
    _tool(case_id, "case", "review_notes", {"case_id": case_id}, lambda: tools.review_notes(case_id))
    saved = _tool(case_id, "case", "generate_report", {"case_id": case_id}, lambda: tools.generate_report(case_id))
    _tool(case_id, "case", "deliver_report", {"case_id": case_id, "to": ["dashboard", "email", "sms", "push"]}, lambda: tools.deliver_report(case_id))
    _note(case_id, "case", f"report: {saved.get('report_id')}\nstatus: {state.get('final_status')}")
    return {}
