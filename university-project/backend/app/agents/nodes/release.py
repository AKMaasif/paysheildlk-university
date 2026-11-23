"""The release step(s) of the case graph (split from graph.py)."""
import logging

from app.agents import tools
from app.agents.base import CaseState, _receive, _send, _tool, pace
from app.events import emit
from app.ledger import set_case_fields

log = logging.getLogger("payshield.graph")


def release(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    # Money only moves on a signed instruction: a policy release, or a passed verification.
    sender, order = _receive(state, "release", "verification" if state.get("customer_decision") == "yes" else "policy", ("policy", "verify_wait"))
    if not ((sender == "policy" and order.get("decision") == "release") or (sender == "verify_wait" and order.get("verified") is True)):
        raise PermissionError("release refused: the signed handoff does not authorise it")
    reason = (
        "Customer confirmed the payment and the security answers matched."
        if state.get("customer_decision") == "yes"
        else "Rule layer released the transfer."
    )
    acting = "verification" if state.get("customer_decision") == "yes" else "policy"
    emit(case_id, agent=acting, state="thinking", message=reason)
    # The last look. The decision was made on a snapshot taken earlier, possibly minutes ago. Before money moves the
    # signals are read again and compared. Anything materially worse stops the release and hands the case to a person.
    look = _tool(
        case_id,
        acting,
        "revalidate_before_release",
        {"transaction_id": state["transaction_id"], "compared_with": state.get("snapshot_id")},
        lambda: tools.revalidate_before_release(state["customer_id"], state["transaction_id"], case_id, state.get("snapshot_id"), state.get("customer_decision") == "yes"),
    )
    if not look["ok"]:
        why = "; ".join(look["reasons"])
        emit(
            case_id, agent=acting, state="done", provider="rules", model="deterministic",
            message=f"Release stopped. New information arrived after the decision: {why}. The payment stays held and a person has the case.",
            handoff_from=acting, handoff_to="analyst",
        )
        tools.hold_transaction(state["transaction_id"], case_id)  # never left pending: the money stays frozen for the analyst
        set_case_fields(case_id, status="escalated", pending_prompt=None)
        tools.outcome_notice(
            state["customer_id"], case_id, "Payment still on hold",
            "New information arrived while your payment was being checked, so it is still held and a person on the fraud desk has the case. No money has moved.",
        )
        return {"final_status": "escalated", **_send(case_id, "release", "report", {"final_status": "escalated"})}
    result = _tool(
        case_id,
        acting,
        "release_transaction",
        {"transaction_id": state["transaction_id"], "case_id": case_id},
        lambda: tools.release_transaction(state["transaction_id"], case_id, reason),
    )
    if sender == "verify_wait" and state.get("face_embedding") and state.get("face_verdict") == "match":
        # A very confident match that was also confirmed by the security questions may refresh the stored
        # templates, so a slow change in how the customer looks does not lock them out later.
        _tool(
            case_id,
            "verification",
            "update_face_template",
            {"reason": "very confident match and the security questions passed"},
            lambda: tools.refresh_face_template(state["customer_id"], state["face_embedding"]),
        )
    status = "closed_released" if state.get("customer_decision") == "yes" else "closed_cleared"
    set_case_fields(case_id, status=status, pending_prompt=None)
    tools.outcome_notice(
        state["customer_id"],
        case_id,
        "Payment update",
        "PayShield released the payment you confirmed." if status == "closed_released" else "PayShield let a low-risk payment proceed. No action is needed.",
    )
    _ = result
    return {"final_status": status, **_send(case_id, "release", "report", {"final_status": status})}
