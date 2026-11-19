import logging
import time
from typing import Annotated, TypedDict

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from app.agents import tools
from app.events import emit
from app.ledger import set_case_fields
from app.notify import BLOCKED_SMS
from app.util import lkr, slt_short, utcnow

log = logging.getLogger("payshield.graph")


def _merge_inbox(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


class CaseState(TypedDict, total=False):
    case_id: str
    customer_id: str
    transaction_id: str
    phone: str
    amount_lkr: int
    payee: str
    telecom_summary: str
    banking_summary: str
    risk_score: int
    risk_factors: list
    risk_summary: str
    policy_decision: str
    policy_reason: str
    customer_decision: str | None
    verification_passed: bool | None
    verification_detail: str | None
    timed_out: bool
    final_status: str
    step: str
    liveness_strikes: int
    knowledge_round: int
    _stored: list
    # Signed handoffs, keyed "sender>receiver". See app/protocol.py.
    _inbox: Annotated[dict, _merge_inbox]


def _send(case_id: str, sender: str, receiver: str, payload: dict) -> dict:
    """Sign a handoff for the next step. The receiver checks it before it acts."""
    from app.protocol import seal
    from app.runtime import runtime

    sealed = seal(runtime.settings.hmac_key, sender, receiver, case_id, payload)
    return {"_inbox": {f"{sender}>{receiver}": sealed}}


def _receive(state: "CaseState", receiver: str, agent: str, senders: tuple[str, ...]) -> tuple[str, dict]:
    """Open the newest signed handoff for this step. Anything unsigned, altered, replayed,
    misaddressed or off-route stops the step, and the guard leaves the money held."""
    from app.protocol import HandoffRejected, open_sealed
    from app.runtime import runtime

    case_id = state["case_id"]
    inbox = state.get("_inbox") or {}
    found = [inbox[f"{s}>{receiver}"] for s in senders if f"{s}>{receiver}" in inbox]
    if not found:
        emit(case_id, agent=agent, state="error", tool_name="verify_handoff", message=f"No signed handoff reached {receiver}.")
        raise HandoffRejected(f"no signed handoff for {receiver}")
    sealed = max(found, key=lambda item: item.get("timestamp", 0))
    try:
        payload = open_sealed(runtime.settings.hmac_key, sealed, receiver, case_id)
    except HandoffRejected as exc:
        emit(case_id, agent=agent, state="error", tool_name="verify_handoff", message=f"Handoff rejected: {exc}")
        raise
    emit(
        case_id,
        agent=agent,
        state="calling_tool",
        tool_name="verify_handoff",
        tool_args={"from": sealed["sender"], "to": receiver},
        result_summary=f"HMAC-SHA256 ok, nonce fresh, sig {sealed['signature'][:12]}",
    )
    return sealed["sender"], payload


def pace() -> None:
    from app.runtime import runtime

    delay = runtime.settings.step_delay_ms
    if delay > 0:
        time.sleep(delay / 1000)


def _guard(agent: str, fn):
    def inner(state: CaseState):
        try:
            from app import faults

            if faults.take_agent_failure(agent):
                raise faults.SimulatedFailure(f"The {agent} agent stopped responding (drill).")
            return fn(state)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            case_id = state.get("case_id", "")
            log.exception("%s failed", agent)
            try:
                if case_id:
                    # The runner's fail-safe decides what the case becomes. The rule explanation stays as it was.
                    emit(
                        case_id,
                        agent=agent,
                        state="error",
                        message=str(exc)[:300],
                        result_summary=str(exc)[:300],
                    )
            except Exception:
                log.exception("could not record the failure")
            raise

    return inner


def _tool(case_id: str, agent: str, name: str, args: dict, fn):
    from app.charters import enforce

    try:
        enforce(agent, name)
    except PermissionError as exc:
        emit(case_id, agent=agent, state="error", tool_name=name, message=str(exc))
        raise
    pace()
    started = time.perf_counter()
    result = fn()
    latency = int((time.perf_counter() - started) * 1000)
    summary = _summarise(name, result)
    emit(
        case_id,
        agent=agent,
        state="calling_tool",
        tool_name=name,
        tool_args=args,
        result_summary=summary,
        latency_ms=latency,
    )
    return result


def _summarise(name: str, result: dict) -> str:
    if name == "get_sim_swap_status":
        swapped = (result.get("checkSimSwap") or {}).get("swapped")
        when = (result.get("retrieveSimSwapDate") or {}).get("latestSimChange")
        if swapped and when:
            return f"SIM was swapped. Latest change {slt_short(when)}."
        if swapped:
            return "SIM was swapped. The feed has no change timestamp."
        return "No SIM swap in the last 240 hours."
    if name == "get_recent_transactions":
        count = len(result.get("transactions") or [])
        return f"{count} recent account events"
    if name == "compute_risk_score":
        return f"Score {result.get('score')} from {len(result.get('factors') or [])} factors. Decision is still with the rules."
    if name == "hold_transaction":
        return f"Transaction {result.get('transaction_id')} is {result.get('status')}"
    if name == "block_transaction":
        return f"Transaction {result.get('transaction_id')} blocked"
    if name == "release_transaction":
        return f"Transaction {result.get('transaction_id')} released"
    if name == "request_sim_freeze":
        return f"Freeze requested, reference {result.get('reference')}"
    if name == "lock_account":
        return "Account limits applied: transfer, new payee, password reset, port-out"
    if name == "send_push":
        return f"Push {result.get('fcm')}, in-app {result.get('in_app')}"
    if name == "send_email":
        if result.get("status") == "failed":
            return "Email failed. The customer is still reached by push and the in-app inbox."
        return f"Email {result.get('status')} via {result.get('driver', 'in-app')}"
    if name == "send_sms":
        if result.get("status") == "failed":
            return "SMS failed. The customer is still reached by push and email."
        return f"SMS {result.get('status')} via {result.get('driver', result.get('status'))}"
    if name == "ask_security_questions":
        return result.get("detail") or "Answers checked"
    if name == "create_case":
        return f"Case {result.get('case_id')} is open"
    if name == "generate_report":
        return f"Report {result.get('report_id')} filed, PDF sha256 {str(result.get('pdf_sha256'))[:12]}"
    if name == "deliver_report":
        return f"Receipt sent: email {result.get('email')}, SMS {result.get('sms')}, push {result.get('push')}"
    if name in {"get_device_change", "get_location_events", "get_port_out_status"}:
        count = len(result.get("events") or [])
        return f"{count} {result.get('event_type')} event(s)"
    if isinstance(result, dict) and result.get("decision"):
        return f"Rule decision: {result.get('decision')}"
    return name


def _note(case_id: str, agent: str, facts: str, handoff_to: str | None = None) -> dict:
    pace()
    note = tools.narrate(case_id, agent, facts)
    emit(
        case_id,
        agent=agent,
        state="done",
        message=note["text"],
        result_summary=note["text"],
        provider=note["provider"],
        model=note["model"],
        fell_back=note["fell_back"],
        fallback_from=note.get("fallback_from"),
        latency_ms=note["latency_ms"],
        handoff_from=agent if handoff_to else None,
        handoff_to=handoff_to,
    )
    return note


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


def telecom(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    phone = state["phone"]
    _receive(state, "telecom", "telecom", ("supervisor",))
    emit(case_id, agent="telecom", state="thinking", message="Reading the SIM profile for this number.")
    swap = _tool(
        case_id,
        "telecom",
        "get_sim_swap_status",
        {"phoneNumber": phone, "maxAge": 240},
        lambda: tools.get_sim_swap_status(phone, 240),
    )
    device = _tool(
        case_id,
        "telecom",
        "get_device_change",
        {"phoneNumber": phone},
        lambda: tools.get_device_change(phone),
    )
    location = _tool(
        case_id,
        "telecom",
        "get_location_events",
        {"phoneNumber": phone},
        lambda: tools.get_location_events(phone),
    )
    port = _tool(
        case_id,
        "telecom",
        "get_port_out_status",
        {"phoneNumber": phone},
        lambda: tools.get_port_out_status(phone),
    )
    swapped = "yes" if (swap.get("checkSimSwap") or {}).get("swapped") else "no"
    facts = (
        f"swapped: {swapped}\n"
        f"latestSimChange: {(swap.get('retrieveSimSwapDate') or {}).get('latestSimChange')}\n"
        f"device events: {len(device.get('events') or [])}\n"
        f"location events: {len(location.get('events') or [])}\n"
        f"port-out events: {len(port.get('events') or [])}\n"
    )
    note = _note(case_id, "telecom", facts, handoff_to="risk")
    set_case_fields(case_id, telecom_summary=note["text"])
    return {"telecom_summary": note["text"], **_send(case_id, "telecom", "risk", {"swapped": swapped})}


def banking(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _receive(state, "banking", "banking", ("supervisor",))
    emit(case_id, agent="banking", state="thinking", message="Reading recent account activity.")
    activity = _tool(
        case_id,
        "banking",
        "get_recent_transactions",
        {"customer_id": state["customer_id"]},
        lambda: tools.get_recent_transactions(state["customer_id"]),
    )
    new_payee = "yes" if any(item.get("new_payee") for item in activity["transactions"][:4]) else "no"
    facts = (
        f"amount: {lkr(state['amount_lkr'])}\n"
        f"payee: {state['payee']}\n"
        f"new_payee: {new_payee}\n"
        f"events: {len(activity['transactions'])}\n"
    )
    note = _note(case_id, "banking", facts, handoff_to="risk")
    set_case_fields(case_id, banking_summary=note["text"])
    return {"banking_summary": note["text"], **_send(case_id, "banking", "risk", {"new_payee": new_payee})}


def risk(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    # Risk needs both feeds. Each one must arrive signed.
    _receive(state, "risk", "risk", ("telecom",))
    _receive(state, "risk", "risk", ("banking",))
    emit(case_id, agent="risk", state="thinking", message="Adding the telecom and bank signals.")
    scored = _tool(
        case_id,
        "risk",
        "compute_risk_score",
        {"customer_id": state["customer_id"], "transaction_id": state["transaction_id"]},
        lambda: tools.compute_risk_score(state["customer_id"], state["transaction_id"]),
    )
    facts = f"score: {scored['score']}\nfactors: {scored['factors']}\nswapped: {scored['signals']['swapped']}\n"
    note = _note(case_id, "risk", facts, handoff_to="policy")
    set_case_fields(
        case_id,
        risk_score=scored["score"],
        risk_factors=scored["factors"],
        risk_summary=note["text"],
    )
    return {
        "risk_score": scored["score"],
        "risk_factors": scored["factors"],
        "risk_summary": note["text"],
        **_send(case_id, "risk", "policy", {"score": scored["score"]}),
    }


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
    decided = tools.apply_policy(state["customer_id"], state["transaction_id"])
    emit(
        case_id,
        agent="policy",
        state="calling_tool",
        tool_name="compute_risk_score",
        tool_args={"customer_id": state["customer_id"], "transaction_id": state["transaction_id"]},
        result_summary=f"Rule decision {decided['decision']}. Score {decided['score']}. {decided['reason']}",
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
    bundle = tools.alert_customer(state["customer_id"], case_id, amount, payee)
    for channel, payload in (("send_push", bundle["push"]), ("send_email", bundle["email"]), ("send_sms", bundle["sms"])):
        emit(
            case_id,
            agent="alert",
            state="calling_tool",
            tool_name=channel,
            tool_args={"user_id": state["customer_id"], "case_id": case_id},
            result_summary=_summarise(channel, payload),
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
    _note(case_id, "alert", f"question: {question}\nchannels: push, email, sms", handoff_to="customer")
    emit(
        case_id,
        agent="alert",
        state="waiting_for_customer",
        message="Waiting for the customer on the registered device.",
        handoff_from="alert",
        handoff_to="customer",
    )
    return {}


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


def _round_prompt(customer_id: str, round_no: int) -> dict:
    from app.knowledge import draw_round

    stored = tools.customer_questions(customer_id)
    shown = draw_round(stored, 3)
    return {"stored": stored, "asked": [item["id"] for item in shown], "public": {"type": "knowledge", "round": round_no, "questions": shown, "notice": "" if round_no == 1 else "Let's try another set"}}


def verification_prep(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _sender, order = _receive(state, "verification_prep", "verification", ("wait_customer",))
    if order.get("customer_decision") != "yes":
        raise PermissionError("verification only starts on a signed yes")
    now = utcnow()
    prompt = {"type": "liveness", "challenge": "Turn your head slightly between the two frames."}
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
        frames = []
        for frame in payload.get("frames") or []:
            if isinstance(frame, (bytes, bytearray)):
                frames.append(bytes(frame))
            elif isinstance(frame, str):
                frames.append(frame.encode())
        checked = analyse(frames, payload.get("liveness"))
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
        return {"step": "knowledge", "knowledge_round": 1, "liveness_strikes": strikes, "customer_decision": "yes", "timed_out": False, "_stored": prompt["asked"]}
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
    if round_no >= 4:
        set_case_fields(case_id, verification_passed=False, verification_detail="Four knowledge rounds failed.", pending_prompt=None)
        return {"verification_passed": False, "step": "failed", "customer_decision": "yes", "timed_out": False, **_send(case_id, "verify_wait", "response", {"verified": False, "reason": "questions"})}
    nxt = round_no + 1
    prompt = _round_prompt(state["customer_id"], nxt)
    set_case_fields(case_id, status="waiting_verification", knowledge_round=nxt, pending_prompt=prompt["public"])
    emit(case_id, agent="verification", state="waiting_for_customer", message="Let's try another set.")
    return {"step": "knowledge", "knowledge_round": nxt, "customer_decision": "yes", "timed_out": False, "_stored": prompt["asked"]}


def route_verification(state: CaseState) -> str:
    if state.get("timed_out"):
        return "escalate"
    if state.get("step") in {"liveness", "knowledge"}:
        return "verify_wait"
    if state.get("verification_passed"):
        return "release"
    return "response"


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
    emit(case_id, agent="verification" if state.get("customer_decision") == "yes" else "policy", state="thinking", message=reason)
    result = _tool(
        case_id,
        "verification" if state.get("customer_decision") == "yes" else "policy",
        "release_transaction",
        {"transaction_id": state["transaction_id"], "case_id": case_id},
        lambda: tools.release_transaction(state["transaction_id"], case_id, reason),
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


def response(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _receive(state, "response", "response", ("policy", "wait_customer", "verify_wait"))
    reason = state.get("verification_detail") or state.get("policy_reason") or "Customer did not accept the payment."
    if state.get("customer_decision") == "no":
        reason = "Customer said they did not make this payment."
    emit(case_id, agent="response", state="thinking", message="Blocking the transfer and asking the operator to freeze the SIM.")
    _tool(
        case_id,
        "response",
        "block_transaction",
        {"transaction_id": state["transaction_id"], "case_id": case_id},
        lambda: tools.block_transaction(state["transaction_id"], case_id, reason),
    )
    freeze = _tool(
        case_id,
        "response",
        "request_sim_freeze",
        {"phoneNumber": state["phone"], "case_id": case_id},
        lambda: tools.request_sim_freeze(state["phone"], case_id, reason),
    )
    _tool(
        case_id,
        "response",
        "lock_account",
        {"customer_id": state["customer_id"], "case_id": case_id},
        lambda: tools.lock_account(state["customer_id"], case_id),
    )
    set_case_fields(
        case_id,
        status="closed_blocked",
        customer_decision=state.get("customer_decision"),
        sim_freeze_ref=freeze.get("reference"),
        pending_prompt=None,
    )
    sms = None if state.get("customer_decision") else BLOCKED_SMS
    tools.outcome_notice(
        state["customer_id"],
        case_id,
        "Payment blocked",
        f"The transfer is blocked. SIM freeze reference {freeze.get('reference')}. "
        "Call your bank on the number printed on your card.",
        sms_body=sms,
    )
    _note(case_id, "response", f"reason: {reason}\nfreeze: {freeze.get('reference')}", handoff_to="case")
    return {"final_status": "closed_blocked", **_send(case_id, "response", "report", {"final_status": "closed_blocked"})}


def escalate(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _receive(state, "escalate", "supervisor", ("wait_customer", "verify_wait"))
    emit(
        case_id,
        agent="supervisor",
        state="done",
        message="No customer response inside 10 minutes. A human analyst has the case. The transfer stays held.",
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
        "We did not get an answer in time. The payment is still held and a person on the fraud desk has the case.",
    )
    return {"final_status": "escalated", "timed_out": True, "customer_decision": "timeout", **_send(case_id, "escalate", "report", {"final_status": "escalated"})}


def report(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _receive(state, "report", "case", ("release", "response", "escalate"))
    emit(case_id, agent="case", state="thinking", message="Writing the incident report.")
    saved = _tool(case_id, "case", "generate_report", {"case_id": case_id}, lambda: tools.generate_report(case_id))
    _tool(case_id, "case", "deliver_report", {"case_id": case_id, "to": ["dashboard", "email", "sms", "push"]}, lambda: tools.deliver_report(case_id))
    _note(case_id, "case", f"report: {saved.get('report_id')}\nstatus: {state.get('final_status')}")
    return {}


def build_graph(checkpointer):
    graph = StateGraph(CaseState)
    graph.add_node("supervisor", _guard("supervisor", supervisor))
    graph.add_node("telecom", _guard("telecom", telecom))
    graph.add_node("banking", _guard("banking", banking))
    graph.add_node("risk", _guard("risk", risk))
    graph.add_node("policy", _guard("policy", policy))
    graph.add_node("alert", _guard("alert", alert))
    graph.add_node("wait_customer", _guard("alert", wait_customer))
    graph.add_node("verification_prep", _guard("verification", verification_prep))
    graph.add_node("verify_wait", _guard("verification", verify_wait))
    graph.add_node("release", _guard("verification", release))
    graph.add_node("response", _guard("response", response))
    graph.add_node("escalate", _guard("supervisor", escalate))
    graph.add_node("report", _guard("case", report))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", fanout, ["telecom", "banking"])
    graph.add_edge("telecom", "risk")
    graph.add_edge("banking", "risk")
    graph.add_edge("risk", "policy")
    graph.add_conditional_edges("policy", route_policy, ["alert", "response", "release"])
    graph.add_edge("alert", "wait_customer")
    graph.add_conditional_edges(
        "wait_customer",
        route_customer,
        ["verification_prep", "response", "escalate"],
    )
    graph.add_edge("verification_prep", "verify_wait")
    graph.add_conditional_edges("verify_wait", route_verification, ["release", "response", "escalate", "verify_wait"])
    graph.add_edge("release", "report")
    graph.add_edge("response", "report")
    graph.add_edge("escalate", "report")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer)


def thread_config(case_id: str) -> dict:
    return {"configurable": {"thread_id": case_id}, "recursion_limit": 80}


def invoke_initial(case_id: str, state: dict):
    from app.runtime import runtime

    return runtime.graph.invoke(state, thread_config(case_id))


def invoke_resume(case_id: str, value: dict):
    from app.runtime import runtime

    return runtime.graph.invoke(Command(resume=value), thread_config(case_id))
