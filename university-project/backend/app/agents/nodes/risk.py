"""The risk step(s) of the case graph (split from graph.py)."""
import logging

from app.agents import tools
from app.agents.base import CaseState, _note, _receive, _send, _tool, pace
from app.events import emit
from app.ledger import set_case_fields

log = logging.getLogger("payshield.graph")


def risk(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    # Risk needs both feeds. Each one must arrive signed.
    _sender, from_telecom = _receive(state, "risk", "risk", ("telecom",))
    _receive(state, "risk", "risk", ("banking",))
    telecom_saw = from_telecom.get("sim_swap")
    emit(case_id, agent="risk", state="thinking", message="Adding the telecom and bank signals.")
    scored = _tool(
        case_id,
        "risk",
        "compute_risk_score",
        {"customer_id": state["customer_id"], "transaction_id": state["transaction_id"]},
        lambda: tools.compute_risk_score(state["customer_id"], state["transaction_id"], case_id=case_id, telecom=telecom_saw),
    )
    if scored.get("conflicts"):
        emit(case_id, agent="risk", state="thinking", provider="rules", model="deterministic",
             message="Two sources disagree about the SIM: " + "; ".join(f"{c['kind'].replace('_', ' ')} (used: {c['used']})" for c in scored["conflicts"]))
    facts = f"score: {scored['score']} (capped at 100)\nfactors: {scored['factors']}\nswapped: {scored['signals']['swapped']}\n"
    note = _note(case_id, "risk", facts, handoff_to="policy")
    set_case_fields(
        case_id,
        risk_score=scored["score"],
        risk_factors=scored["factors"],
        risk_summary=note["text"],
        snapshot_id=scored["snapshot_id"],
    )
    return {
        "snapshot_id": scored["snapshot_id"],
        "risk_score": scored["score"],
        "risk_factors": scored["factors"],
        "risk_summary": note["text"],
        **_send(case_id, "risk", "policy", {"score": scored["score"]}),
    }
