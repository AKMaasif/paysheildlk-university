import logging

from app.facts import collect_signals, recent_transactions, retrieve_sim_swap_date, check_sim_swap
from app.ledger import (
    block_transaction,
    bump_provider,
    hold_transaction,
    lock_account,
    release_transaction,
    request_sim_freeze,
    set_case_fields,
)
from app.notify import BLOCKED_SMS, GENERIC_SMS, send_email, send_push, send_sms
from app.reports import save_report
from app.risk import decide_policy
from app.security import answers_match
from app.util import lkr

log = logging.getLogger("payshield.tools")

SYSTEM = (
    "You write short notes for a fraud analyst at a Sri Lankan bank. "
    "Two sentences. Plain English. Do not say whether to hold, block, or release the money. "
    "Do not invent facts that are not in the note. Money is written as Rs."
)


def narrate(case_id: str, purpose: str, facts: str) -> dict:
    from app.runtime import runtime

    result = runtime.llm.complete(system=SYSTEM, user=facts, purpose=purpose)
    bump_provider(case_id, result.provider)
    return {
        "text": result.text,
        "provider": result.provider,
        "model": result.model,
        "fell_back": result.fell_back,
        "fallback_from": result.fallback_from,
        "latency_ms": result.latency_ms,
    }


def get_sim_swap_status(phone: str, max_age: int = 240) -> dict:
    if _remote_telco():
        return _remote_sim_swap(phone, max_age)
    checked = check_sim_swap(phone, max_age)
    dated = retrieve_sim_swap_date(phone)
    return {
        "checkSimSwap": {"swapped": checked["swapped"]},
        "retrieveSimSwapDate": {"latestSimChange": dated["latestSimChange"]},
        "maxAgeHours": checked["maxAge"],
        "phoneNumber": checked["phoneNumber"],
        "source": "camara-mock",
    }


def _remote_telco() -> bool:
    from app.runtime import runtime

    return bool(runtime.settings.telco_base_url)


def _remote_sim_swap(phone: str, max_age: int) -> dict:
    import httpx

    from app.runtime import runtime

    base = runtime.settings.telco_base_url
    headers = {"Content-Type": "application/json"}
    if runtime.settings.telco_token:
        headers["Authorization"] = f"Bearer {runtime.settings.telco_token}"
    body = {"phoneNumber": phone}
    if max_age:
        body["maxAge"] = max_age
    checked = httpx.post(f"{base}/sim-swap/v1/check", json=body, headers=headers, timeout=15)
    checked.raise_for_status()
    dated = httpx.post(f"{base}/sim-swap/v1/retrieve-date", json={"phoneNumber": phone}, headers=headers, timeout=15)
    dated.raise_for_status()
    check_body = checked.json()
    date_body = dated.json()
    return {
        "checkSimSwap": {"swapped": bool(check_body.get("swapped"))},
        "retrieveSimSwapDate": {"latestSimChange": date_body.get("latestSimChange")},
        "maxAgeHours": max_age,
        "phoneNumber": phone,
        "source": "camara-http",
    }


def _latest_event(phone: str, kind: str) -> dict:
    from app.db import session_scope
    from app.models import SimEvent
    from app.util import iso

    with session_scope() as session:
        rows = (
            session.query(SimEvent)
            .filter(SimEvent.phone == phone, SimEvent.event_type == kind)
            .order_by(SimEvent.occurred_at.desc())
            .limit(3)
            .all()
        )
        return {
            "phoneNumber": phone,
            "event_type": kind,
            "events": [
                {"id": row.id, "occurred_at": iso(row.occurred_at), "detail": row.detail or {}}
                for row in rows
            ],
        }


def get_device_change(phone: str) -> dict:
    return _latest_event(phone, "new_imei")


def get_location_events(phone: str) -> dict:
    return _latest_event(phone, "location_change")


def get_port_out_status(phone: str) -> dict:
    return _latest_event(phone, "port_out")


def get_recent_transactions(customer_id: str) -> dict:
    rows = recent_transactions(customer_id)
    return {"customer_id": customer_id, "transactions": rows}


def compute_risk_score(customer_id: str, transaction_id: str) -> dict:
    signals, context = collect_signals(customer_id, transaction_id)
    policy = decide_policy(signals)
    return {
        "score": policy.score,
        "factors": policy.factors,
        "signals": {
            "swapped": "yes" if signals.swapped else "no",
            "sim_swap_age_hours": None if signals.sim_swap_age_hours is None else round(signals.sim_swap_age_hours, 2),
            "new_imei_age_hours": None if signals.new_imei_age_hours is None else round(signals.new_imei_age_hours, 2),
            "location_change_age_hours": None
            if signals.location_change_age_hours is None
            else round(signals.location_change_age_hours, 2),
            "port_out": signals.port_out,
            "new_payee": "yes" if signals.new_payee else "no",
            "amount_lkr": signals.amount_lkr,
            "password_reset_age_hours": None
            if signals.password_reset_age_hours is None
            else round(signals.password_reset_age_hours, 2),
            "new_device_login": signals.new_device_login,
            "latest_sim_change": signals.latest_sim_change,
        },
        "context": {
            "customer": context["customer"],
            "payee": context["payee"],
            "amount_lkr": context["amount_lkr"],
            "transactions": context["transactions"],
        },
        "decision_input_only": True,
    }


def apply_policy(customer_id: str, transaction_id: str) -> dict:
    from app.db import session_scope
    from app.emergency import must_hold
    from app.models import User

    signals, _context = collect_signals(customer_id, transaction_id)
    policy = decide_policy(signals)
    result = {
        "decision": policy.decision,
        "score": policy.score,
        "factors": list(policy.factors),
        "reason": policy.reason,
        "emergency": None,
    }
    if policy.decision == "release":
        with session_scope() as session:
            user = session.get(User, customer_id)
            district = user.district if user else None
        why = must_hold(district, signals.amount_lkr)
        if why:
            # Emergency mode can turn a release into a hold. It never releases and never lowers a hold or block.
            result["decision"] = "hold"
            result["emergency"] = district
            result["factors"].append({"label": f"{district} is in emergency mode", "points": 0})
            result["reason"] = (
                f"Emergency rule: {district} is in emergency mode ({why}). "
                f"Payments of {lkr(signals.amount_lkr)} or more are held until the customer confirms them on their registered device."
            )
    return result


def create_case(case_id: str) -> dict:
    set_case_fields(case_id, status="running")
    return {"case_id": case_id, "status": "running"}


def generate_report(case_id: str) -> dict:
    """File the report, then fingerprint the analyst PDF so a later download can be checked."""
    import hashlib

    from app.db import session_scope
    from app.models import Report
    from app.report_pdf import build_case_pdf

    saved = save_report(case_id)
    pdf = build_case_pdf(case_id, "analyst")
    digest = hashlib.sha256(pdf).hexdigest()
    with session_scope() as session:
        row = session.get(Report, saved["report_id"])
        row.sha256 = digest
    return {**saved, "pdf_sha256": digest, "pdf_bytes": len(pdf)}


_OUTCOME_WORD = {
    "closed_blocked": "blocked",
    "closed_released": "released",
    "closed_cleared": "released",
    "escalated": "held for a fraud analyst",
}


def deliver_report(case_id: str) -> dict:
    """Send the customer their receipt: PDF by email, a short SMS, and a push to the app.
    The analyst report is already on the dashboard once it is filed."""
    from zoneinfo import ZoneInfo

    from app.db import session_scope
    from app.models import Case
    from app.notify import UnsafeSms, send_email, send_push, send_sms
    from app.report_pdf import build_case_pdf
    from app.util import aware

    from app.models import User

    with session_scope() as session:
        case = session.get(Case, case_id)
        user_id, status, amount, payee = case.customer_id, case.status, case.amount_lkr, case.payee or "the payee"
        moment = aware(case.updated_at).astimezone(ZoneInfo("Asia/Colombo"))
        # A payment that looked like a SIM-swap takeover means the password may be known to someone else.
        # Once the case is closed the customer must choose a new one.
        if case.policy_decision in {"hold", "block"}:
            owner = session.get(User, user_id)
            if owner is not None and owner.registered:
                owner.must_change_password = True
    word = _OUTCOME_WORD.get(status, "updated")
    when = moment.strftime("%d %b at %H:%M")
    receipt = build_case_pdf(case_id, "customer")
    body = (
        f"PayShield receipt for case {case_id}\n\n"
        f"Your payment of {lkr(amount)} to {payee} was {word} on {when} (Sri Lanka time).\n"
        "The attached PDF shows every step and the exact time it happened.\n\n"
        "PayShield will never ask you for a password or a code by SMS or phone call.\n"
    )
    email = send_email(user_id, case_id, f"Your PayShield receipt {case_id}", body, attachments=[(f"PayShield-{case_id}.pdf", receipt, "application/pdf")])
    sms_text = f"PayShield: your payment of {lkr(amount)} to {payee} was {word} on {when}. The receipt is in the PayShield app and your email."
    try:
        sms = send_sms(user_id, case_id, sms_text)
    except UnsafeSms:
        # A payee name with digits could look like a code. Fall back to a message with no details.
        sms = send_sms(user_id, case_id, f"PayShield: your payment was {word}. The receipt is in the PayShield app and your email.")
    push = send_push(user_id, case_id, "Your receipt is ready", f"Payment {word}. Open the app to download the PDF.", {"case_id": case_id, "kind": "receipt"})
    return {"email": email.get("status"), "sms": sms.get("status"), "push": push.get("fcm") or push.get("sent"), "receipt_bytes": len(receipt)}


def ask_security_questions(customer_id: str, answers: dict, liveness: bool, only: list | None = None) -> dict:
    from app.knowledge import grade_round
    from app.runtime import runtime

    pepper = runtime.settings.answer_pepper
    questions = customer_questions(customer_id)
    if only:
        wanted = {item["id"] if isinstance(item, dict) else item for item in only}
        questions = [item for item in questions if item["id"] in wanted]
    passed = grade_round(questions, answers or {}, pepper)
    # Do not say which answer failed.
    detail = "The round matched." if passed else "Let's try another set."
    return {"passed": passed, "asked": len(questions), "detail": detail, "liveness": bool(liveness)}


def customer_questions(customer_id: str) -> list[dict]:
    from app.db import session_scope
    from app.models import SecurityAnswer, User

    with session_scope() as session:
        rows = session.query(SecurityAnswer).filter(SecurityAnswer.user_id == customer_id).all()
        if rows:
            return [{"id": row.code, "prompt": row.prompt, "hash": row.answer_hash} for row in rows]
        user = session.get(User, customer_id)
        security = dict(user.security or {}) if user else {}
    questions = []
    for key, item in security.items():
        if not isinstance(item, dict):
            continue
        questions.append({"id": key, "prompt": item.get("prompt") or key, "hash": item.get("answer_hash") or ""})
    return questions


def alert_customer(user_id: str, case_id: str, amount_lkr: int, payee: str, sms_body: str | None = None) -> dict:
    title = "Confirm a payment"
    push_body = f"A transfer of {lkr(amount_lkr)} to {payee} needs your confirmation in the PayShield app."
    email_body = (
        f"PayShield\n\nA payment of {lkr(amount_lkr)} to {payee} is waiting for your confirmation.\n\n"
        "Open the PayShield app on the phone you registered. We will not send a code by SMS.\n\n"
        "If you did not try to make this payment, choose No in the app.\n"
    )
    push = send_push(user_id, case_id, title, push_body, {"case_id": case_id, "kind": "confirm"})
    email = send_email(user_id, case_id, "Confirm a payment in PayShield", email_body)
    if email.get("status") == "failed":
        email = {**send_email(user_id, case_id, "Confirm a payment in PayShield", email_body), "retried": True}
    sms = send_sms(user_id, case_id, sms_body or GENERIC_SMS)
    if sms.get("status") == "failed":
        sms = {**send_sms(user_id, case_id, sms_body or GENERIC_SMS), "retried": True}
    return {"push": push, "email": email, "sms": sms}


def outcome_notice(user_id: str, case_id: str, title: str, body: str, sms_body: str | None = None) -> dict:
    push = send_push(user_id, case_id, title, body, {"case_id": case_id, "kind": "outcome"})
    email = send_email(user_id, case_id, title, body)
    sms = None
    if sms_body:
        sms = send_sms(user_id, case_id, sms_body)
    return {"push": push, "email": email, "sms": sms}


__all__ = [
    "alert_customer",
    "apply_policy",
    "ask_security_questions",
    "block_transaction",
    "compute_risk_score",
    "create_case",
    "customer_questions",
    "deliver_report",
    "generate_report",
    "get_device_change",
    "get_location_events",
    "get_port_out_status",
    "get_recent_transactions",
    "get_sim_swap_status",
    "hold_transaction",
    "lock_account",
    "narrate",
    "outcome_notice",
    "release_transaction",
    "request_sim_freeze",
    "BLOCKED_SMS",
    "GENERIC_SMS",
]
