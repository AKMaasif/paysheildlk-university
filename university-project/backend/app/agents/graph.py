import logging
import time
from typing import Annotated, TypedDict

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from app import ops
from app.agents import tools
from app.events import emit
from app.ledger import set_case_fields
from app.notify import BLOCKED_SMS
from app.privacy import mask, untrusted
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
            ops.count("agent_errors", agent=agent)
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
    from app.util import nid

    channels = ("send_push", "send_email", "send_sms")
    call_ids = {channel: nid("call") for channel in channels}
    for channel in channels:  # shown as in progress for as long as the providers take
        emit(case_id, agent="alert", state="calling_tool", tool_name=channel, tool_args={"user_id": state["customer_id"], "case_id": case_id},
             phase="started", call_id=call_ids[channel], meta={"planner": "workflow"})
    bundle = tools.alert_customer(state["customer_id"], case_id, amount, payee)
    for channel, payload in (("send_push", bundle["push"]), ("send_email", bundle["email"]), ("send_sms", bundle["sms"])):
        failed_send = payload.get("status") == "failed"
        emit(
            case_id,
            agent="alert",
            state="calling_tool",
            tool_name=channel,
            tool_args={"user_id": state["customer_id"], "case_id": case_id},
            result_summary=_summarise(channel, payload),
            latency_ms=payload.get("latency_ms"),
            phase="failed" if failed_send else "finished",
            call_id=call_ids[channel],
            result=payload,
            meta={"planner": "workflow", "attempts": payload.get("attempts", 1), "delivery_state": (payload.get("delivery") or {}).get("state")},
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
    _note(case_id, "alert", f"question: Did you make this payment of {lkr(amount)} to {untrusted(payee)}?\nchannels: push, email, sms", handoff_to="customer")
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
    if round_no >= int(state.get("max_rounds") or 4):
        set_case_fields(case_id, verification_passed=False, verification_detail="Four knowledge rounds failed.", pending_prompt=None)
        return {"verification_passed": False, "step": "failed", "customer_decision": "yes", "timed_out": False, **_send(case_id, "verify_wait", "response", {"verified": False, "reason": "questions"})}
    nxt = round_no + 1
    prompt = _round_prompt(state["customer_id"], nxt)
    set_case_fields(case_id, status="waiting_verification", knowledge_round=nxt, pending_prompt=prompt["public"])
    emit(case_id, agent="verification", state="waiting_for_customer", message="Let's try another set.")
    return {"step": "knowledge", "knowledge_round": nxt, "customer_decision": "yes", "timed_out": False, "_stored": prompt["asked"]}


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
        out = {**base, "step": "knowledge", "knowledge_round": 1, "max_rounds": decision["max_rounds"], "face_score": result.score or 0.0, "_stored": prompt["asked"]}
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


def response(state: CaseState) -> dict:
    pace()
    case_id = state["case_id"]
    _receive(state, "response", "response", ("policy", "wait_customer", "verify_wait"))
    reason = state.get("verification_detail") or state.get("policy_reason") or "Customer did not accept the payment."
    if state.get("customer_decision") == "no":
        reason = "Customer said they did not make this payment."
    emit(case_id, agent="response", state="thinking", message="Blocking the transfer and recording a SIM freeze request (simulated: no operator is contacted).")
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
        f"The transfer is blocked and no money left your account. A SIM freeze request was recorded, reference {freeze.get('reference')}, "
        "but your mobile operator has not been told. Call your operator and your bank on the numbers printed on your SIM pack and your card.",
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
        message=(
            "The face check could not settle who this is. A human analyst has the case. The transfer stays held."
            if state.get("escalation_reason") == "face"
            else "No customer response inside 10 minutes. A human analyst has the case. The transfer stays held."
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
