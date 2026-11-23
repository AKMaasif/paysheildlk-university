"""The policy step(s) of the case graph (split from graph.py)."""
import logging

from app.agents import tools
from app.agents.base import CaseState, _receive, _send, _tool, pace
from app.events import emit
from app.ledger import set_case_fields

log = logging.getLogger("payshield.graph")


def policy(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _receive(state, "policy", "policy", ("risk",))
    emit(
        case_id,
        agent="policy",
        state="thinking",
        message="Applying the hold, block, and release rules. This step does not call a model.",
        provider="rules",
        model="deterministic",
    )
    decided = tools.apply_policy(state["customer_id"], state["transaction_id"], state.get("snapshot_id"))
    emit(
        case_id,
        agent="policy",
        state="calling_tool",
        tool_name="compute_risk_score",
        tool_args={"customer_id": state["customer_id"], "transaction_id": state["transaction_id"], "snapshot": (decided.get("snapshot") or {}).get("id")},
        result_summary=f"Rule decision {decided['decision']}. Score {decided['score']}. {decided['reason']}",
        result={"decision": decided["decision"], "score": decided["score"], "reason": decided["reason"], "emergency": decided.get("emergency"), "snapshot": decided.get("snapshot")},
        meta={"planner": "rules", "snapshot_id": (decided.get("snapshot") or {}).get("id")},
        provider="rules",
        model="deterministic",
    )
    if decided["decision"] in {"hold", "block"}:
        _tool(
            case_id,
            "policy",
            "hold_transaction",
            {"transaction_id": state["transaction_id"], "case_id": case_id},
            lambda: tools.hold_transaction(state["transaction_id"], case_id),
        )
    handoff = {"hold": "alert", "block": "response", "release": "release"}[decided["decision"]]
    emit(
        case_id,
        agent="policy",
        state="done",
        message=decided["reason"],
        result_summary=decided["reason"],
        handoff_from="policy",
        handoff_to=handoff,
        provider="rules",
        model="deterministic",
    )
    set_case_fields(
        case_id,
        policy_decision=decided["decision"],
        policy_reason=decided["reason"],
        risk_score=decided["score"],
        risk_factors=decided["factors"],
    )
    return {
        "policy_decision": decided["decision"],
        "policy_reason": decided["reason"],
        "risk_score": decided["score"],
        "risk_factors": decided["factors"],
        **_send(case_id, "policy", handoff, {"decision": decided["decision"], "transaction_id": state["transaction_id"]}),
    }


def route_policy(state: CaseState) -> str:
    return {"hold": "alert", "block": "response", "release": "release"}[state["policy_decision"]]
