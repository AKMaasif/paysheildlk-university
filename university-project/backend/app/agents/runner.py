import logging
import threading

from app.agents.graph import invoke_initial, invoke_resume
from app.db import session_scope
from app.events import emit
from app.ledger import set_case_fields
from app.models import Case, Transaction, User
from app.notify import GENERIC_SMS, send_email, send_push, send_sms
from app.util import aware, lkr, utcnow

log = logging.getLogger("payshield.runner")

_graph_lock = threading.Lock()
_case_guards: dict[str, threading.Lock] = {}
_guard_lock = threading.Lock()


def case_lock(case_id: str) -> threading.Lock:
    with _guard_lock:
        if case_id not in _case_guards:
            _case_guards[case_id] = threading.Lock()
        return _case_guards[case_id]


def fail_safe(case_id: str, reason: str) -> str:
    """An agent failed. Make sure the money is not left free to move.

    A payment that is still pending is held. A payment already blocked or released
    stays that way. Anything unfinished goes to a human analyst, and the customer
    is told the payment is still on hold. Returns the case status it ended with."""
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None:
            return "unknown"
        tx = session.get(Transaction, case.transaction_id) if case.transaction_id else None
        customer_id = case.customer_id
        if tx is not None and tx.status == "pending":
            tx.status = "held"
        money = tx.status if tx is not None else "unknown"
        if money == "blocked":
            status = "closed_blocked"
        elif money == "released":
            status = case.status if case.status in {"closed_released", "closed_cleared"} else "closed_released"
        else:
            status = "escalated"
        case.status = status
        case.pending_prompt = None
        case.updated_at = utcnow()
    held = status == "escalated"
    emit(
        case_id,
        agent="supervisor",
        state="done",
        message=(
            f"{reason} The money is held and a human analyst has the case. Nothing is released automatically."
            if held
            else f"{reason} The payment had already reached its outcome ({money}), so only the follow-up needs an analyst."
        ),
        handoff_from="supervisor",
        handoff_to="analyst",
        provider="rules",
        model="deterministic",
    )
    if held:
        from app.agents import tools

        tools.outcome_notice(
            customer_id,
            case_id,
            "Payment still on hold",
            "Something went wrong on our side. Your payment is held and a person on the fraud desk has the case. No money has moved.",
        )
    return status


def begin_case(case_id: str) -> None:
    with case_lock(case_id):
        try:
            _begin(case_id)
        except Exception as exc:
            log.exception("case %s failed to start", case_id)
            fail_safe(case_id, f"An agent failed: {str(exc)[:160]}.")


def _begin(case_id: str) -> None:
    with session_scope() as session:
        case = session.get(Case, case_id)
        user = session.get(User, case.customer_id)
        tx = session.get(Transaction, case.transaction_id)
        state = {
            "case_id": case.id,
            "customer_id": user.id,
            "transaction_id": tx.id,
            "phone": user.phone,
            "amount_lkr": tx.amount_lkr,
            "payee": tx.payee or "",
        }
        case.status = "running"
        case.updated_at = utcnow()
    with _graph_lock:
        invoke_initial(case_id, state)


def resume_case(case_id: str, value: dict, expect_status: str) -> bool:
    with case_lock(case_id):
        with session_scope() as session:
            case = session.get(Case, case_id)
            if case is None or case.status != expect_status:
                return False
        try:
            with _graph_lock:
                invoke_resume(case_id, value)
        except Exception as exc:
            log.exception("case %s failed on resume", case_id)
            fail_safe(case_id, f"An agent failed: {str(exc)[:160]}.")
        return True


def spawn_case(case_id: str) -> None:
    threading.Thread(target=begin_case, args=(case_id,), name=f"case-{case_id}", daemon=True).start()


def realert_case(case_id: str) -> bool:
    from app.runtime import runtime

    with case_lock(case_id):
        with session_scope() as session:
            case = session.get(Case, case_id)
            if case is None or case.status not in {"waiting_customer", "waiting_verification", "waiting_liveness"}:
                return False
            user_id = case.customer_id
            amount = case.amount_lkr
            payee = case.payee or ""
            count = case.alert_count or 1
            phase = case.status
            case.alert_count = count + 1
            case.last_alert_at = utcnow()
            case.updated_at = utcnow()
        title = "Still waiting for you"
        if phase == "waiting_verification":
            body = "PayShield still needs the security answers on this phone before the payment can move."
        else:
            body = f"PayShield still needs a yes or no for the transfer of {lkr(amount)} to {payee}."
        emit(
            case_id,
            agent="alert",
            state="thinking",
            message=f"No response for {runtime.settings.realert_seconds // 60} minutes. Sending the reminder.",
        )
        push = send_push(user_id, case_id, title, body, {"case_id": case_id, "kind": "realert"})
        email = send_email(user_id, case_id, title, body)
        sms = send_sms(user_id, case_id, GENERIC_SMS)
        emit(
            case_id,
            agent="alert",
            state="calling_tool",
            tool_name="send_push",
            tool_args={"user_id": user_id, "realert": True},
            result_summary=f"Reminder push {push.get('fcm')}, email {email.get('status')}, sms {sms.get('status')}",
        )
        emit(
            case_id,
            agent="alert",
            state="waiting_for_customer",
            message="Reminder sent. Still waiting for the customer.",
        )
        return True


def escalate_case(case_id: str) -> bool:
    with case_lock(case_id):
        with session_scope() as session:
            case = session.get(Case, case_id)
            if case is None or case.status not in {"waiting_customer", "waiting_verification", "waiting_liveness"}:
                return False
            case.status = "escalating"
            case.updated_at = utcnow()
        try:
            with _graph_lock:
                invoke_resume(case_id, {"decision": "timeout"})
        except Exception:
            log.exception("escalate failed for %s", case_id)
            set_case_fields(case_id, status="error")
            emit(case_id, agent="supervisor", state="error", message="Escalation failed.")
            return False
        return True


def process_timeouts(now=None) -> dict:
    from app.runtime import runtime

    moment = aware(now or utcnow())
    realert_after = runtime.settings.realert_seconds
    escalate_after = runtime.settings.escalate_seconds
    due: list[tuple[str, str]] = []
    with session_scope() as session:
        rows = (
            session.query(Case)
            .filter(Case.status.in_(("waiting_customer", "waiting_verification", "waiting_liveness")))
            .all()
        )
        for case in rows:
            if case.waiting_since is None:
                continue
            elapsed = (moment - aware(case.waiting_since)).total_seconds()
            if elapsed >= escalate_after:
                due.append((case.id, "escalate"))
                continue
            if case.last_alert_at is None:
                continue
            since_alert = (aware(case.last_alert_at) - aware(case.waiting_since)).total_seconds()
            if elapsed >= realert_after and since_alert < 15:
                due.append((case.id, "realert"))
    done = {"realert": 0, "escalate": 0}
    for case_id, action in due:
        if action == "realert" and realert_case(case_id):
            done["realert"] += 1
        elif action == "escalate" and escalate_case(case_id):
            done["escalate"] += 1
    return done


def timeout_loop(stop: threading.Event, generation: int | None = None) -> None:
    from app.runtime import runtime

    while not stop.wait(2):
        if generation is not None and runtime.generation != generation:
            return
        try:
            process_timeouts()
        except Exception:
            log.exception("timeout sweep failed")
