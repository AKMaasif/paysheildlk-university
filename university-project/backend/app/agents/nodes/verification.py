"""The verification step(s) of the case graph (split from graph.py)."""
import logging

from langgraph.types import interrupt

from app.agents import tools
from app.agents.base import CaseState, _receive, _send, pace
from app.events import emit
from app.ledger import set_case_fields
from app.util import utcnow

log = logging.getLogger("payshield.graph")


def _round_prompt(customer_id: str, round_no: int, avoid: list | None = None) -> dict:
    from app.knowledge import draw_round

    stored = tools.customer_questions(customer_id)
    shown = draw_round(stored, 3, avoid=set(avoid or []))
    return {"stored": stored, "asked": [item["id"] for item in shown], "public": {"type": "knowledge", "round": round_no, "questions": shown, "notice": "" if round_no == 1 else "Let's try another set"}}


def _liveness_prompt(case_id: str, retake: bool = False, hints: list | None = None) -> dict:
    """The selfie step. With real face verification it carries a signed random challenge for this case."""
    from app import face

    prompt: dict = {"type": "liveness", "challenge": "Turn your head slightly between the two frames."}
    if face.mode() == "dlib":
        ch = face.issue_challenge(case_id)
        prompt = {"type": "liveness", "challenge": "Look at the camera and follow the instructions.", "face": ch}
    if retake:
        prompt["retake"] = True
    if hints:
        prompt["hints"] = list(hints)[:4]
    return prompt


def verification_prep(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _sender, order = _receive(state, "verification_prep", "verification", ("wait_customer",))
    if order.get("customer_decision") != "yes":
        raise PermissionError("verification only starts on a signed yes")
    now = utcnow()
    prompt = _liveness_prompt(case_id)
    set_case_fields(
        case_id,
        status="waiting_liveness",
        customer_decision="yes",
        waiting_since=now,
        last_alert_at=now,
        pending_prompt=prompt,
        liveness_strikes=0,
        knowledge_round=0,
    )
    emit(
        case_id,
        agent="verification",
        state="waiting_for_customer",
        message="Customer said the payment was theirs. Waiting for a live selfie.",
        handoff_from="customer",
        handoff_to="verification",
    )
    return {"customer_decision": "yes", "step": "liveness", "liveness_strikes": 0, "knowledge_round": 0}


def verify_wait(state: CaseState) -> dict:
    from app.charters import enforce
    from app.liveness import analyse

    step = state.get("step") or "liveness"
    payload = interrupt({"type": step, "case_id": state["case_id"], "round": state.get("knowledge_round") or 0})
    if isinstance(payload, dict) and payload.get("decision") == "timeout":
        return {"timed_out": True, "verification_passed": False, "step": "timeout", "customer_decision": "yes", **_send(state["case_id"], "verify_wait", "escalate", {"reason": "timeout"})}
    payload = payload if isinstance(payload, dict) else {}
    case_id = state["case_id"]
    if step == "liveness":
        enforce("verification", "liveness_check")
        from app import face

        if face.mode() != "legacy":
            return _face_step(state, payload)
        frames = []
        for frame in payload.get("frames") or []:
            if isinstance(frame, (bytes, bytearray)):
                frames.append(bytes(frame))
            elif isinstance(frame, str):
                frames.append(frame.encode())
        checked = analyse(frames)
        strikes = int(state.get("liveness_strikes") or 0)
        emit(
            case_id,
            agent="verification",
            state="calling_tool",
            tool_name="liveness_check",
            tool_args={"frames": len(frames)},
            result_summary=f"{checked['verdict']} confidence={checked['confidence']}",
        )
        if checked["verdict"] != "live":
            strikes += 1
            if strikes >= 2:
                set_case_fields(case_id, liveness_strikes=strikes, verification_passed=False, verification_detail="Live selfie failed twice.", pending_prompt=None)
                return {"verification_passed": False, "step": "failed", "liveness_strikes": strikes, "customer_decision": "yes", "timed_out": False, **_send(case_id, "verify_wait", "response", {"verified": False, "reason": "selfie"})}
            set_case_fields(case_id, liveness_strikes=strikes, status="waiting_liveness", pending_prompt={"type": "liveness", "retake": True})
            emit(case_id, agent="verification", state="waiting_for_customer", message="Selfie did not look live. One retake is allowed.")
            return {"step": "liveness", "liveness_strikes": strikes, "customer_decision": "yes", "timed_out": False}
        prompt = _round_prompt(state["customer_id"], 1)
        set_case_fields(case_id, status="waiting_verification", liveness_strikes=strikes, knowledge_round=1, pending_prompt=prompt["public"])
        return {"step": "knowledge", "knowledge_round": 1, "liveness_strikes": strikes, "customer_decision": "yes", "timed_out": False, "_stored": prompt["asked"], "asked_before": list(prompt["asked"])}
    # Grade every question shown in this round, not only the ones the client chose to answer.
    shown = state.get("_stored") or tools.customer_questions(state["customer_id"])
    asked = [item["id"] if isinstance(item, dict) else item for item in shown]
    checked = tools.ask_security_questions(state["customer_id"], payload.get("answers") or {}, True, asked)
    emit(
        case_id,
        agent="verification",
        state="calling_tool",
        tool_name="ask_security_questions",
        tool_args={"round": state.get("knowledge_round") or 1, "count": len(asked)},
        result_summary="round matched" if checked["passed"] else "Let's try another set.",
    )
    if checked["passed"]:
        set_case_fields(case_id, verification_passed=True, verification_detail=checked["detail"], pending_prompt=None)
        return {"verification_passed": True, "verification_detail": checked["detail"], "step": "passed", "customer_decision": "yes", "timed_out": False, **_send(case_id, "verify_wait", "release", {"verified": True})}
    round_no = int(state.get("knowledge_round") or 1)
    if round_no >= int(state.get("max_rounds") or 4):
        set_case_fields(case_id, verification_passed=False, verification_detail="Four knowledge rounds failed.", pending_prompt=None)
        return {"verification_passed": False, "step": "failed", "customer_decision": "yes", "timed_out": False, **_send(case_id, "verify_wait", "response", {"verified": False, "reason": "questions"})}
    nxt = round_no + 1
    earlier = list(state.get("asked_before") or [])
    prompt = _round_prompt(state["customer_id"], nxt, avoid=earlier)
    set_case_fields(case_id, status="waiting_verification", knowledge_round=nxt, pending_prompt=prompt["public"])
    emit(case_id, agent="verification", state="waiting_for_customer", message="Let's try another set.")
    return {"step": "knowledge", "knowledge_round": nxt, "customer_decision": "yes", "timed_out": False, "_stored": prompt["asked"], "asked_before": earlier + list(prompt["asked"])}


def _face_step(state: CaseState, payload: dict) -> dict:
    """Liveness and identity: a live capture answered a random challenge, and the face matches the one on file."""
    from app import face
    from app.charters import enforce
    from app.db import session_scope
    from app.models import User

    case_id, customer_id = state["case_id"], state["customer_id"]
    enforce("verification", "face_match")
    with session_scope() as session:
        user = session.get(User, customer_id)
        sealed = user.face_ref if user else None
    frames = payload.get("frames") or []
    try:
        result = face.verify(sealed, frames, payload.get("offsets_ms") or [], payload.get("challenge"), case_id)
    except (face.ChallengeError, face.BadCapture) as exc:
        # An expired or reused challenge, or a broken upload, is not evidence against the customer.
        result = face.Result("poor_quality", hints=[str(exc)])
    live = result.liveness or {}
    emit(
        case_id,
        agent="verification",
        state="calling_tool",
        tool_name="liveness_check",
        tool_args={"frames": len(payload.get("frames") or []), "challenge": (live.get("challenge") or {})},
        result_summary=("live person, challenge followed" if live.get("live") else ("not accepted as live: " + "; ".join(result.hints[:2])) if result.verdict == "fake" else "not assessed")
        if result.verdict not in {"unavailable", "no_template"}
        else result.hints[0],
    )
    emit(
        case_id,
        agent="verification",
        state="calling_tool",
        tool_name="face_match",
        tool_args={"templates": "on file", "frames_used": result.frames_used},
        result_summary=(
            f"similarity {result.score:g}/100 ({result.verdict}), distance {result.distance}"
            if result.score is not None
            else f"{result.verdict}: {'; '.join(result.hints[:2])}"
        ),
    )
    decision = face.decide(result, state)
    carry = {k: decision[k] for k in ("liveness_strikes", "face_unsure", "quality_retakes") if k in decision}
    set_case_fields(case_id, face_score=result.score, face_verdict=result.verdict)
    base = {"customer_decision": "yes", "timed_out": False, "face_verdict": result.verdict, **carry}
    nxt = decision["next"]
    if nxt == "questions":
        prompt = _round_prompt(customer_id, 1)
        set_case_fields(case_id, status="waiting_verification", knowledge_round=1, pending_prompt=prompt["public"], liveness_strikes=carry.get("liveness_strikes", state.get("liveness_strikes") or 0))
        emit(case_id, agent="verification", state="waiting_for_customer", message=("Face matched. Now the security questions." if result.verdict == "match" else "Face likely matched. Two rounds of security questions will decide."))
        out = {**base, "step": "knowledge", "knowledge_round": 1, "max_rounds": decision["max_rounds"], "face_score": result.score or 0.0, "_stored": prompt["asked"], "asked_before": list(prompt["asked"])}
        if result.embedding and (result.score or 0) >= face.REFRESH_SCORE and result.verdict == "match":
            out["face_embedding"] = result.embedding
        return out
    if nxt == "retake":
        default = {
            "no_match": "The scan did not match the face on file. Face the camera in good light and try again.",
            "uncertain": "We could not be sure it is you. Face the camera in good light, with your face fully in view, and try again.",
        }.get(result.verdict, "Please try again.")
        hints = result.hints or [default]
        set_case_fields(case_id, status="waiting_liveness", pending_prompt=_liveness_prompt(case_id, retake=True, hints=hints))
        emit(case_id, agent="verification", state="waiting_for_customer", message="Another face scan is needed. " + hints[0])
        return {**base, "step": "liveness"}
    if nxt == "block":
        set_case_fields(case_id, verification_passed=False, verification_detail="The face check failed twice.", pending_prompt=None)
        return {**base, "verification_passed": False, "step": "failed", **_send(case_id, "verify_wait", "response", {"verified": False, "reason": "selfie"})}
    # review: a person decides. The money stays held and nothing is released automatically.
    set_case_fields(case_id, verification_passed=False, verification_detail="The face check could not settle it. A person reviews the case.", pending_prompt=None)
    return {**base, "timed_out": True, "verification_passed": False, "step": "timeout", "escalation_reason": "face", **_send(case_id, "verify_wait", "escalate", {"reason": "face"})}


def route_verification(state: CaseState) -> str:
    if state.get("timed_out"):
        return "escalate"
    if state.get("step") in {"liveness", "knowledge"}:
        return "verify_wait"
    if state.get("verification_passed"):
        return "release"
    return "response"
