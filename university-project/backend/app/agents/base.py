"""What every agent step shares: the case state, signed handoffs, the tool wrapper and the note writer (split from graph.py)."""
import logging
import time
from typing import Annotated, TypedDict

from app.agents import tools
from app.events import emit
from app.privacy import mask
from app.util import slt_short

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
    snapshot_id: str
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
    face_unsure: int
    quality_retakes: int
    max_rounds: int
    face_embedding: list
    face_score: float
    face_verdict: str
    escalation_reason: str
    _stored: list
    asked_before: list
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


def _tool(case_id: str, agent: str, name: str, args: dict, fn, *, planner: str = "workflow", why: str | None = None):
    """Run one tool. It is recorded as two events with one call id: "started" before it runs (so the dashboard can show it
    in progress) and "finished" or "failed" after, with the real duration and the structured result. The charter is
    checked first. planner says who chose the call: the fixed workflow, a mandatory core read, the rule planner,
    the model planner, or a person."""
    from app.charters import enforce
    from app.util import nid

    try:
        enforce(agent, name)
    except PermissionError as exc:
        emit(case_id, agent=agent, state="error", tool_name=name, message=str(exc), meta={"planner": planner, "refused": "charter"})
        raise
    pace()
    call_id = nid("call")
    meta = {"planner": planner, **({"why": why} if why else {})}
    emit(case_id, agent=agent, state="calling_tool", tool_name=name, tool_args=args, phase="started", call_id=call_id, meta=meta)
    started = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:
        emit(
            case_id, agent=agent, state="calling_tool", tool_name=name, tool_args=args, phase="failed", call_id=call_id,
            latency_ms=int((time.perf_counter() - started) * 1000), message=f"{name} failed: {type(exc).__name__}",
            result={"error": type(exc).__name__}, meta=meta,
        )
        raise
    latency = int((time.perf_counter() - started) * 1000)
    emit(
        case_id,
        agent=agent,
        state="calling_tool",
        tool_name=name,
        tool_args=args,
        result_summary=_summarise(name, result),
        latency_ms=latency,
        phase="finished",
        call_id=call_id,
        result=result if isinstance(result, dict) else {"value": result},
        meta=meta,
    )
    return result


_REASONS = {
    "timeout": "the provider timed out", "unreachable": "the provider could not be reached", "rejected_credentials": "the provider refused the credentials",
    "rate_limited": "the provider is rate limiting", "provider_error": "the provider had an error", "rejected": "the provider rejected it",
    "no_address": "there is no email address on file", "no_phone": "there is no mobile number on file", "no_device_token": "no phone has a push token",
    "auth_failed": "the push service refused the sign-in", "recipient_refused": "the mail server refused the recipient", "request_error": "the request could not be made",
}


def _delivery_text(channel: str, result: dict) -> str:
    """One honest sentence from the structured delivery result. It says what is known and what is not."""
    d = result.get("delivery") or {}
    state, label = d.get("state"), {"sms": "SMS", "email": "Email", "push": "Push"}[channel]
    tries = f" after {result['attempts']} attempts" if result.get("attempts", 1) > 1 else ""
    if state == "logged_only":
        return f"{label} logged only. Nothing was sent."
    if state == "inbox_stored":
        return f"{label} kept in the app inbox. No provider was asked."
    if state in {"blocked_not_allowlisted", "blocked_budget"}:
        why = "this recipient is not on the demonstration allow-list" if state == "blocked_not_allowlisted" else "the hourly limit on real messages was reached"
        return f"{label} not sent: {why}. Kept in the app inbox."
    if state == "provider_accepted":
        ident = f" (id {d['provider_message_id']})" if d.get("provider_message_id") else ""
        return f"{label} accepted by {d.get('provider')}{ident}. Delivery is not confirmed."
    if state == "delivered":
        return f"{label} delivery confirmed by {d.get('provider')}."
    if state == "failed":
        extra = " The customer is still reached in the app." if channel != "push" else ""
        return f"{label} failed{tries}: {_REASONS.get(d.get('reason'), d.get('reason') or 'unknown reason')}.{extra}"
    return f"{label} status unknown."


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
        return f"SIM freeze request recorded in PayShield only (simulated), reference {result.get('reference')}. No operator was contacted; the SIM is not frozen."
    if name == "lock_account":
        return "Account limits applied: transfer, new payee, password reset, port-out"
    if name in {"send_push", "send_email", "send_sms"}:
        return _delivery_text(name.removeprefix("send_"), result)
    if name == "ask_security_questions":
        return result.get("detail") or "Answers checked"
    if name == "create_case":
        return f"Case {result.get('case_id')} is open"
    if name == "update_face_template":
        return "Stored face templates refreshed" if result.get("updated") else "Templates already cover this look"
    if name == "review_notes":
        return f"{result.get('checked')} agent note(s) checked against their facts, {result.get('replaced')} replaced"
    if name == "generate_report":
        return f"Report {result.get('report_id')} filed, PDF sha256 {str(result.get('pdf_sha256'))[:12]}"
    if name == "deliver_report":
        return f"Receipt sent: email {result.get('email')}, SMS {result.get('sms')}, push {result.get('push')}"
    if name == "get_sim_history":
        return f"{len(result.get('events') or [])} SIM event(s) in 30 days, {result.get('swaps_30d')} swap(s)"
    if name == "get_customer_profile":
        return f"{result.get('payments')} earlier payments, usual high Rs. {result.get('usual_high_lkr'):,}, payee paid {result.get('payee_payments')} time(s) before"
    if name == "get_payee_history":
        return f"{result.get('earlier_payments')} earlier payment(s) to this payee"
    if name == "get_credential_events":
        return f"{len(result.get('events') or [])} sign-in or reset event(s) in 48 hours"
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
        # What the note was written from, masked, so the Report agent can check the note against it.
        tool_args={"facts": mask(facts)[:900]},
        handoff_from=agent if handoff_to else None,
        handoff_to=handoff_to,
    )
    return note


def _investigate(case_id: str, agent: str, calls: dict, args: dict) -> dict:
    """Core reads, then extra lookups chosen by the planner. Every call goes through _tool, so the
    charter is enforced and each one is recorded."""
    from app.investigator import investigate
    from app.runtime import runtime

    if runtime.settings.mcp_tools:
        from app.mcp_tools import call_tool

        # The same lookups, reached through the Model Context Protocol instead of a direct call.
        calls = {name: (lambda a, name=name: call_tool(agent, name, a)) for name in calls}

    def run_tool(name, tool_args, note):
        if note:
            emit(
                case_id,
                agent=agent,
                state="thinking",
                message=f"Looking further: {note['why']}",
                provider=note["planner"],
                model="deterministic" if note["planner"] == "rules" else "planner",
            )
        # A core read is mandatory and is not planned. An extra one names who chose it and why.
        planner = f"{note['planner']} planner" if note else "core read"
        return _tool(case_id, agent, name, tool_args, lambda: calls[name](tool_args), planner=planner, why=note["why"] if note else None)

    out = investigate(agent, run_tool, lambda name: args[name], case_id)
    for step in out["plan"]:
        if step["step"] == "refused":
            emit(
                case_id,
                agent=agent,
                state="thinking",
                message=f"The planner asked for {step['asked_for']!r}, which is not an allowed lookup here. Ignored.",
                provider=step["planner"],
                model="planner",
            )
    return out
