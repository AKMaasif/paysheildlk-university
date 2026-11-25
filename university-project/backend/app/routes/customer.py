"""customer routes (split from app/api.py)."""
import logging

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
)
from pydantic import BaseModel

from app import ratelimit
from app.agents.runner import (
    resume_case,
)
from app.db import session_scope
from app.ledger import audit
from app.models import (
    Case,
    Device,
    Notification,
    Report,
    User,
)
from app.routes.common import (
    VerifyIn,
    _customer_case_state,
    _customer_steps,
    _pdf_response,
    _public_user,
    finish_session,
    require_customer,
)
from app.runtime import runtime
from app.security import (
    decode_token,
    is_revoked,
    open_totp,
    seal_totp,
    totp_accept,
    totp_uri,
)
from app.util import (
    aware,
    iso,
    nid,
    utcnow,
)

log = logging.getLogger("payshield.api")
router = APIRouter()


class RespondIn(BaseModel):
    decision: str


class DeviceIn(BaseModel):
    label: str = "This phone"
    platform: str = "browser"
    fcm_token: str | None = None


class TotpConfirm(BaseModel):
    code: str


@router.get("/api/customer/cases/{case_id}/receipt")
def customer_receipt(case_id: str, user: User = Depends(require_customer)):
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None or case.customer_id != user.id:
            raise HTTPException(status_code=404, detail="Case not found")
        filed = session.query(Report).filter(Report.case_id == case_id).one_or_none() is not None
    if not filed:
        raise HTTPException(status_code=409, detail="The receipt is not ready yet")
    audit(user.email, "receipt_download", case_id, "customer pdf")
    return _pdf_response(case_id, "customer", None)


@router.post("/api/customer/devices")
def register_device(body: DeviceIn, user: User = Depends(require_customer)):
    with session_scope() as session:
        existing = (
            session.query(Device)
            .filter(Device.user_id == user.id, Device.label == body.label)
            .one_or_none()
        )
        now = utcnow()
        if existing is None:
            session.add(
                Device(
                    id=nid("dev"),
                    user_id=user.id,
                    label=body.label[:200],
                    platform=body.platform[:80],
                    fcm_token=body.fcm_token,
                    trusted=True,
                    last_seen=now,
                    created_at=now,
                )
            )
        else:
            existing.platform = body.platform[:80]
            existing.last_seen = now
            if body.fcm_token:
                existing.fcm_token = body.fcm_token
            existing.trusted = True
    return {"ok": True}


@router.post("/api/customer/totp/setup")
def totp_setup(authorization: str | None = Header(default=None)):
    import pyotp

    user = _pending_or_customer(authorization)
    with session_scope() as session:
        row = session.get(User, user.id)
        if row.totp_enrolled:
            raise HTTPException(status_code=409, detail="Authenticator is already enrolled")
        # An unconfirmed secret is kept, so reloading the page does not invalidate what the customer already added to
        # their app, and a preset demonstration secret stays the same.
        secret = open_totp(row.totp_secret) if row.totp_secret else pyotp.random_base32()
        if not row.totp_secret:
            row.totp_secret = seal_totp(secret)
        row.totp_enrolled = False
        email = row.email
    uri = totp_uri(secret, email)
    import base64
    import io

    import qrcode

    image = qrcode.make(uri)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return {"secret": secret, "otpauth_uri": uri, "qr_png_base64": encoded}


def _pending_or_customer(authorization: str | None) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in required")
    token = authorization.split(" ", 1)[1].strip()
    payload = None
    for purpose in ("access", "totp"):
        try:
            payload = decode_token(runtime.settings, token, purpose=purpose)
            break
        except Exception:
            payload = None
    if payload is None:
        raise HTTPException(status_code=401, detail="Session expired")
    with session_scope() as session:
        user = session.get(User, payload["sub"])
        if user is None or user.role != "customer":
            raise HTTPException(status_code=403, detail="Customer sign-in required")
        if payload.get("purpose") == "access" and is_revoked(session, payload, user):
            raise HTTPException(status_code=401, detail="Session ended")
        session.expunge(user)
        return user


@router.post("/api/customer/totp/confirm")
def totp_confirm(body: TotpConfirm, request: Request, response: Response, authorization: str | None = Header(default=None)):
    user = _pending_or_customer(authorization)
    with session_scope() as session:
        row = session.get(User, user.id)
        ratelimit.check("totp_fail", row.id)
        if not row.totp_secret or not totp_accept(row.id, open_totp(row.totp_secret), body.code):
            ratelimit.hit("totp_fail", row.id)
            raise HTTPException(status_code=401, detail="That code is not valid")
        ratelimit.clear("totp_fail", row.id)
        row.totp_enrolled = True
        user_id, role = row.id, row.role
        snapshot = _public_user(row)
    return finish_session(request, response, user_id, role, snapshot)


@router.get("/api/customer/home")
def customer_home(user: User = Depends(require_customer)):
    with session_scope() as session:
        cases = (
            session.query(Case)
            .filter(Case.customer_id == user.id)
            .order_by(Case.updated_at.desc())
            .all()
        )
        notes = (
            session.query(Notification)
            .filter(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .limit(8)
            .all()
        )
        active = next(
            (case for case in cases if case.status in {"new", "running", "waiting_customer", "waiting_liveness", "waiting_verification", "escalating"}),
            None,
        )
        latest = cases[0] if cases else None
        focus = active or latest
        fresh = False
        if focus is not None and focus.updated_at is not None:
            fresh = (utcnow() - aware(focus.updated_at)).total_seconds() < 6 * 3600
        show_result = focus is not None and focus.status in {"closed_blocked", "closed_released", "closed_cleared", "escalated"} and (active is None) and fresh
        return {
            "customer": {
                "id": user.id,
                "full_name": user.full_name,
                "bank": user.bank,
                "phone": user.phone,
                "must_change_password": bool(user.must_change_password),
            },
            "active": None
            if active is None
            else {
                "id": active.id,
                "status": active.status,
                "amount_lkr": active.amount_lkr,
                "payee": active.payee,
                "pending_prompt": active.pending_prompt,
                "bank": active.bank,
            },
            "result": None
            if not show_result
            else {
                "id": focus.id,
                "status": focus.status,
                "amount_lkr": focus.amount_lkr,
                "payee": focus.payee,
                "sim_freeze_ref": focus.sim_freeze_ref,
                "steps": _customer_steps(focus),
            },
            "notifications": [
                {
                    "id": note.id,
                    "channel": note.channel,
                    "title": note.title,
                    "body": note.body,
                    "case_id": note.case_id,
                    "created_at": iso(note.created_at),
                }
                for note in notes
            ],
            "realert_seconds": runtime.settings.realert_seconds,
            "escalate_seconds": runtime.settings.escalate_seconds,
        }


@router.post("/api/customer/cases/{case_id}/respond")
def customer_respond(case_id: str, body: RespondIn, user: User = Depends(require_customer)):
    decision = body.decision.strip().lower()
    if decision not in {"yes", "no"}:
        raise HTTPException(status_code=400, detail="Answer yes or no")
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None or case.customer_id != user.id:
            raise HTTPException(status_code=404, detail="Case not found")
        if case.status != "waiting_customer":
            # A late answer is refused before it is written anywhere, so it cannot appear in the case's record.
            raise HTTPException(status_code=409, detail="This payment is no longer waiting for that answer")
    from app.events import emit

    emit(
        case_id,
        agent="customer",
        state="done",
        message="Customer answered yes." if decision == "yes" else "Customer answered no.",
        handoff_from="customer",
        handoff_to="verification" if decision == "yes" else "response",
    )
    ok = resume_case(case_id, {"decision": decision}, "waiting_customer")
    if not ok:
        raise HTTPException(status_code=409, detail="This payment is no longer waiting for that answer")
    return _customer_case_state(case_id, user.id)


@router.post("/api/customer/cases/{case_id}/verify")
def customer_verify(case_id: str, body: VerifyIn, user: User = Depends(require_customer)):
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None or case.customer_id != user.id:
            raise HTTPException(status_code=404, detail="Case not found")
    ratelimit.take("verify", case_id)
    ok = resume_case(case_id, {"answers": body.answers}, "waiting_verification")
    if not ok:
        raise HTTPException(status_code=409, detail="This payment is not waiting for security answers")
    return _customer_case_state(case_id, user.id)
