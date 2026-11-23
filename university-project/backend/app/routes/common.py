"""Shared request models, sign-in helpers and view helpers for the route modules (split from app/api.py)."""
import logging
from datetime import timedelta

from fastapi import (
    Depends,
    Header,
    HTTPException,
    Request,
)
from fastapi.responses import Response
from pydantic import BaseModel

from app.db import session_scope
from app.models import (
    Case,
    User,
)
from app.runtime import runtime
from app.security import (
    REFRESH_MINUTES,
    create_token,
    decode_token,
)
from app.util import (
    OPERATOR_ROLES,
    ROLE_LABELS,
    aware,
    iso,
    utcnow,
)

log = logging.getLogger("payshield.api")


def _trusted(address: str) -> bool:
    import ipaddress

    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    for entry in runtime.settings.trusted_proxies:
        try:
            if ip in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def _client_ip(request: Request | None) -> str:
    """The address of the visitor. Behind the web server's proxy every request arrives from the proxy, so its
    X-Forwarded-For header is believed, but only when the direct peer is in TRUSTED_PROXIES. The header is read from
    the right: the first address that is not a trusted proxy is the visitor. A header sent by anyone else is ignored."""
    if request is None or request.client is None:
        return "unknown"
    peer = request.client.host or "unknown"
    if not _trusted(peer):
        return peer
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    for address in reversed(forwarded):
        if not _trusted(address):
            return address
    return peer


def _session_tokens(user_id: str, role: str) -> dict:
    return {
        "token": create_token(runtime.settings, user_id, role, purpose="access"),
        "refresh_token": create_token(runtime.settings, user_id, role, purpose="refresh", minutes=REFRESH_MINUTES),
    }


class VerifyIn(BaseModel):
    answers: dict


def _user_from_header(authorization: str | None) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(runtime.settings, token, purpose="access")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Session expired") from exc
    with session_scope() as session:
        user = session.get(User, payload["sub"])
        if user is None or user.status != "active":
            raise HTTPException(status_code=401, detail="Account is not active")
        session.expunge(user)
        return user


def current_user(authorization: str | None = Header(default=None)) -> User:
    return _user_from_header(authorization)


def require_operator(user: User = Depends(current_user)) -> User:
    if user.role not in OPERATOR_ROLES:
        raise HTTPException(status_code=403, detail="Operator sign-in required")
    return user


def require_drills() -> None:
    """Drills and the demonstration helpers write pretend events into a customer's record. A real deployment turns them off."""
    if runtime.settings is None or not runtime.settings.drills_enabled:
        raise HTTPException(status_code=404, detail="Drills are switched off on this system")


def require_writer(user: User = Depends(require_operator)) -> User:
    if user.role == "auditor":
        raise HTTPException(status_code=403, detail="Auditors have read-only access")
    return user


def require_customer(user: User = Depends(current_user)) -> User:
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="Customer sign-in required")
    return user


def _public_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "role_label": ROLE_LABELS.get(user.role, user.role),
        "bank": user.bank,
        "province": user.province,
        "district": user.district,
        "status": user.status,
        "scope": user.bank or "All banks",
    }


def _timers(case: Case) -> dict | None:
    """Where a waiting case is on its clock: how long it has waited, when the reminder is due, when it goes to a person."""
    if case.status not in {"waiting_customer", "waiting_liveness", "waiting_verification"} or case.waiting_since is None:
        return None
    settings = runtime.settings
    waited = max(0, int((utcnow() - aware(case.waiting_since)).total_seconds()))
    return {
        "phase": case.status,
        "waiting_since": iso(case.waiting_since),
        "waited_seconds": waited,
        "reminders_sent": max(0, (case.alert_count or 1) - 1),
        "reminder_after_seconds": settings.realert_seconds,
        "escalates_after_seconds": settings.escalate_seconds,
        "seconds_to_reminder": max(0, settings.realert_seconds - waited) if (case.alert_count or 1) <= 1 else None,
        "seconds_to_escalation": max(0, settings.escalate_seconds - waited),
        "escalation_at": iso(aware(case.waiting_since) + timedelta(seconds=settings.escalate_seconds)),
    }


def _customer_steps(case: Case) -> list[str]:
    if case.status == "closed_blocked":
        freeze = case.sim_freeze_ref or "pending"
        return [
            "The transfer is blocked.",
            f"We recorded a SIM freeze request, reference {freeze}. Your mobile operator has not been told, so call them to freeze the SIM.",
            "Transfers, new payees, password resets, and port-out stay locked until your bank's fraud desk clears them.",
            "Call your bank on the number printed on your card. Do not use a number from a text message.",
            "This app stays on the phone you registered. It is not tied to the SIM.",
        ]
    if case.status == "closed_released":
        return [
            "You confirmed the payment and the security answers matched.",
            "The transfer was released.",
            "If that confirmation was not really you, call your bank on the number printed on your card.",
        ]
    if case.status == "closed_cleared":
        return [
            "The rule layer let this payment proceed.",
            "No SIM change met the hold threshold.",
        ]
    if case.status == "escalated":
        return [
            "We did not get an answer in time.",
            "The payment is still held.",
            "A person on the fraud desk has the case.",
            "You can still call your bank on the number printed on your card.",
        ]
    return []


PENDING_MINUTES = 5


def _pending_step(user_id: str, enrolled: bool) -> dict:
    """The password was right but the sign-in is not finished: a customer must also give an authenticator code, or
    set the authenticator up first. The pending token can do nothing else and lasts five minutes."""
    return {
        "step": "totp" if enrolled else "enrol",
        "pending_token": create_token(runtime.settings, user_id, "customer", purpose="totp", minutes=PENDING_MINUTES),
    }


def _pdf_response(case_id: str, audience: str, expected_sha: str | None) -> Response:
    import hashlib

    from app.report_pdf import build_case_pdf

    payload = build_case_pdf(case_id, audience)
    digest = hashlib.sha256(payload).hexdigest()
    headers = {
        "Content-Disposition": f'attachment; filename="PayShield-{case_id}{"-receipt" if audience == "customer" else ""}.pdf"',
        "X-Content-SHA256": digest,
    }
    if expected_sha:
        # The analyst PDF is rebuilt from the events up to filing, so it must match the stored fingerprint.
        headers["X-Integrity"] = "match" if expected_sha == digest else "mismatch"
    return Response(content=payload, media_type="application/pdf", headers=headers)


def _customer_case_state(case_id: str, user_id: str) -> dict:
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None or case.customer_id != user_id:
            raise HTTPException(status_code=404, detail="Case not found")
        return {
            "id": case.id,
            "status": case.status,
            "amount_lkr": case.amount_lkr,
            "payee": case.payee,
            "pending_prompt": case.pending_prompt,
            "sim_freeze_ref": case.sim_freeze_ref,
            "steps": _customer_steps(case),
            "verification_detail": case.verification_detail,
        }
