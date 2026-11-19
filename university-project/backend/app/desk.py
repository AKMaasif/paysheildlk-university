import base64
import io
from datetime import timedelta

import pyotp
import qrcode
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app import ratelimit
from app.answers import QUESTION_BANK, hash_secret
from app.charters import CHARTERS
from app.crypto import encrypt
from app.db import session_scope
from app.emergency import breaker_status
from app.ledger import audit, verify_audit_chain
from app.models import Case, SecurityAnswer, Transaction, User
from app.runtime import runtime
from app.security import hash_password, open_totp, totp_valid
from app.util import aware, nid, utcnow

router = APIRouter()


def _writer(authorization: str | None = Header(default=None)) -> User:
    from app.api import current_user

    user = current_user(authorization)
    if user.role not in {"fraud_analyst", "telecom_operator", "bank_supervisor", "super_admin"}:
        raise HTTPException(status_code=403, detail="This action needs an operator who can write")
    return user


class AnswerIn(BaseModel):
    code: str
    answer: str


class EnrolIn(BaseModel):
    full_name: str
    phone: str
    email: str
    district: str
    username: str
    password: str = Field(min_length=8)
    answers: list[AnswerIn]


class ConfirmIn(BaseModel):
    user_id: str
    code: str


class KillIn(BaseModel):
    action: str
    reason: str = Field(min_length=4)


def _charters() -> dict:
    return {
        "agents": [
            {
                "id": key,
                "name": item.role,
                "task": item.task,
                "tools": list(item.tools),
                "scope": item.scope,
                "forbidden": list(item.forbidden),
                "autonomy": item.autonomy,
            }
            for key, item in CHARTERS.items()
        ]
    }


@router.get("/api/charters")
def list_charters(authorization: str | None = Header(default=None)):
    _writer(authorization)
    return _charters()


@router.get("/api/questions")
def questions(authorization: str | None = Header(default=None)):
    _writer(authorization)
    return {"questions": QUESTION_BANK}


@router.post("/api/enrol")
def enrol(body: EnrolIn, authorization: str | None = Header(default=None)):
    actor = _writer(authorization)
    phone = body.phone.strip().replace(" ", "")
    if not phone.startswith("+94") or len(phone) < 11:
        raise HTTPException(status_code=400, detail="Phone must be in +94 format")
    if len(body.answers) < 6:
        raise HTTPException(status_code=400, detail="Pick at least six verification questions")
    known = {item["code"]: item["prompt"] for item in QUESTION_BANK}
    secret = pyotp.random_base32()
    user_id = nid("cus")
    with session_scope() as session:
        if session.query(User).filter(User.email == body.email.strip().lower()).one_or_none():
            raise HTTPException(status_code=409, detail="That email is already enrolled")
        session.add(
            User(
                id=user_id,
                email=body.email.strip().lower(),
                username=body.username.strip().lower(),
                full_name=body.full_name.strip(),
                password_hash=hash_password(body.password),
                role="customer",
                phone=phone,
                district=body.district.strip(),
                totp_secret=encrypt(secret, runtime.settings.encryption_key),
                totp_enrolled=False,
                status="pending",
                created_at=utcnow(),
            )
        )
        for item in body.answers:
            if item.code not in known:
                raise HTTPException(status_code=400, detail="Unknown question")
            session.add(
                SecurityAnswer(
                    id=nid("sec"),
                    user_id=user_id,
                    code=item.code,
                    prompt=known[item.code],
                    answer_hash=hash_secret(item.answer, item.code),
                )
            )
    uri = pyotp.TOTP(secret).provisioning_uri(name=body.email, issuer_name="PayShield.lk")
    image = qrcode.make(uri)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    audit(actor.email, "enrol_customer", user_id, body.district)
    return {
        "user_id": user_id,
        "otpauth": uri,
        "qr_png_base64": base64.b64encode(buffer.getvalue()).decode(),
    }


@router.post("/api/enrol/confirm")
def enrol_confirm(body: ConfirmIn, authorization: str | None = Header(default=None)):
    actor = _writer(authorization)
    with session_scope() as session:
        user = session.get(User, body.user_id)
        if user is None or user.totp_enrolled:
            raise HTTPException(status_code=404, detail="Enrolment is not waiting for a code")
        secret = open_totp(user.totp_secret)
        if not totp_valid(secret, body.code):
            raise HTTPException(status_code=401, detail="That authenticator code was not accepted")
        user.totp_enrolled = True
        user.status = "active"
    audit(actor.email, "enrol_totp", body.user_id, "confirmed")
    return {"enrolled": True}


@router.get("/api/security/status")
def security_status(authorization: str | None = Header(default=None)):
    _writer(authorization)
    chain = verify_audit_chain()
    settings = runtime.settings
    return {
        "chain_ok": chain["ok"],
        "chain_checked": chain["checked"],
        "encryption": "AES-256-GCM",
        "passwords": "Argon2id",
        "jwt_access_minutes": 30,
        "sms": settings.sms_driver,
        "email": settings.email_driver,
        "emergency": breaker_status(),
        "last_rotation": "key is taken from the environment on each start",
    }


@router.post("/api/audit/verify")
def audit_verify(authorization: str | None = Header(default=None)):
    _writer(authorization)
    return verify_audit_chain()


@router.post("/api/cases/{case_id}/kill")
def kill_switch(case_id: str, body: KillIn, authorization: str | None = Header(default=None)):
    from app import manual

    actor = _writer(authorization)
    try:
        return manual.apply(case_id, body.action, body.reason, actor.email)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc
    except manual.ManualRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/analytics")
def analytics(authorization: str | None = Header(default=None)):
    from app.analytics import compute
    from app.api import current_user

    user = current_user(authorization)
    if user.role == "customer":
        raise HTTPException(status_code=403, detail="Operator sign-in required")
    return compute()


@router.post("/api/customer/cases/{case_id}/liveness")
def customer_liveness(case_id: str, body: dict, authorization: str | None = Header(default=None)):
    from app.agents.runner import resume_case
    from app.api import current_user

    user = current_user(authorization)
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="Customer sign-in required")
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None or case.customer_id != user.id:
            raise HTTPException(status_code=404, detail="Case not found")
    ratelimit.take("liveness", case_id)
    frames = []
    for item in body.get("frames") or []:
        try:
            frames.append(base64.b64decode(item))
        except Exception:
            frames.append(str(item).encode())
    # Only the camera frames go to the check. A client-supplied verdict is never forwarded.
    ok = resume_case(case_id, {"frames": frames}, "waiting_liveness")
    if not ok:
        raise HTTPException(status_code=409, detail="This payment is not waiting for a selfie")
    from app.api import _customer_case_state

    return _customer_case_state(case_id, user.id)
