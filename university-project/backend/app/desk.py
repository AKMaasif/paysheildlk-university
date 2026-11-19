import base64
import secrets
from datetime import timedelta

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app import ratelimit
from app.answers import QUESTION_BANK, hash_secret
from app.charters import CHARTERS
from app.db import session_scope
from app.emergency import breaker_status
from app.ledger import audit, verify_audit_chain
from app.models import Case, SecurityAnswer, Transaction, User
from app.runtime import runtime
from app.security import hash_password
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
    """The bank's record for a customer. The customer claims it later by registering in the app."""

    full_name: str = Field(min_length=2, max_length=200)
    phone: str
    email: str
    nic: str = Field(min_length=9, max_length=20)
    account_number: str = Field(min_length=6, max_length=32)
    district: str
    branch: str = ""
    balance_lkr: int = Field(default=0, ge=0, le=1_000_000_000)
    answers: list[AnswerIn]


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
    from app.customer import clean_account, clean_nic, clean_phone
    from app.geo import province_of

    actor = _writer(authorization)
    phone = clean_phone(body.phone)
    if not phone.startswith("+94") or len(phone) != 12:
        raise HTTPException(status_code=400, detail="Mobile number must be a Sri Lankan number, for example +94771234567")
    if len(body.answers) < 6:
        raise HTTPException(status_code=400, detail="Pick at least six verification questions")
    known = {item["code"]: item["prompt"] for item in QUESTION_BANK}
    email = body.email.strip().lower()
    account = clean_account(body.account_number)
    nic = clean_nic(body.nic)
    user_id = nid("cus")
    with session_scope() as session:
        if session.query(User).filter(User.email == email).one_or_none():
            raise HTTPException(status_code=409, detail="That email is already on a customer record")
        if session.query(User).filter(User.role == "customer", User.account_number == account).first():
            raise HTTPException(status_code=409, detail="That account number is already on a customer record")
        session.add(
            User(
                id=user_id,
                email=email,
                full_name=" ".join(body.full_name.split()),
                # The customer sets the real password when they register. Until then this one cannot be used.
                password_hash=hash_password(secrets.token_urlsafe(24)),
                role="customer",
                phone=phone,
                bank="BankCylone",
                district=body.district.strip(),
                province=province_of(body.district.strip()),
                branch=body.branch.strip() or None,
                account_number=account,
                nic=nic,
                balance_lkr=body.balance_lkr,
                registered=False,
                totp_enrolled=False,
                status="active",
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
    audit(actor.email, "enrol_customer", user_id, body.district)
    return {"user_id": user_id, "registered": False}


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
            frames.append(base64.b64decode(item, validate=True))
        except Exception:
            raise HTTPException(status_code=422, detail="A camera frame could not be read. Please scan again.")
    offsets = [int(x) for x in (body.get("offsets_ms") or []) if isinstance(x, (int, float))]
    # Only the camera frames, their times and the signed challenge go to the check. A client-supplied verdict is never forwarded.
    ok = resume_case(case_id, {"frames": frames, "offsets_ms": offsets, "challenge": str(body.get("challenge") or "")}, "waiting_liveness")
    if not ok:
        raise HTTPException(status_code=409, detail="This payment is not waiting for a selfie")
    from app.api import _customer_case_state

    return _customer_case_state(case_id, user.id)


# ---------------------------------------------------------------- calibration (the feedback loop with a human gate)

class DecideIn(BaseModel):
    decision: str
    note: str = ""


def _approver(authorization: str | None) -> User:
    from app.api import current_user

    user = current_user(authorization)
    if user.role not in {"fraud_analyst", "super_admin"}:
        raise HTTPException(status_code=403, detail="Only a fraud analyst or the super admin can approve a rule change")
    return user


@router.get("/api/calibration")
def calibration_view(authorization: str | None = Header(default=None)):
    from app import calibration
    from app.api import current_user
    from app.models import PolicyProposal
    from app.risk import DEFAULTS, SAFE_RANGE, thresholds

    user = current_user(authorization)
    if user.role == "customer":
        raise HTTPException(status_code=403, detail="Operator sign-in required")
    with session_scope() as session:
        rows = session.query(PolicyProposal).order_by(PolicyProposal.created_at.desc()).limit(30).all()
        return {
            "thresholds": {k: {"value": v, "default": DEFAULTS[k], "min": SAFE_RANGE[k][0], "max": SAFE_RANGE[k][1]} for k, v in thresholds().items()},
            "evidence": calibration.evidence(session),
            "min_cases": calibration.MIN_CASES,
            "proposals": [calibration.proposal_dict(r) for r in rows],
            "can_approve": user.role in {"fraud_analyst", "super_admin"},
        }


@router.post("/api/calibration/propose")
def calibration_propose(authorization: str | None = Header(default=None)):
    from app import calibration

    actor = _writer(authorization)
    with session_scope() as session:
        made = calibration.propose(session)
    audit(actor.email, "calibration_propose", "hold_score", f"{len(made)} proposal(s)")
    return {"proposals": made}


@router.post("/api/calibration/proposals/{proposal_id}/decide")
def calibration_decide(proposal_id: str, body: DecideIn, authorization: str | None = Header(default=None)):
    from app import calibration

    actor = _approver(authorization)
    if body.decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="Choose approve or reject")
    try:
        with session_scope() as session:
            result = calibration.decide(session, proposal_id, body.decision == "approve", actor.email, body.note)
    except LookupError:
        raise HTTPException(status_code=404, detail="Proposal not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit(actor.email, "calibration_" + body.decision, proposal_id, f"{result['key']} {result['current']} -> {result['proposed']}")
    return result


@router.post("/api/calibration/reset")
def calibration_reset(authorization: str | None = Header(default=None)):
    from app import calibration

    actor = _approver(authorization)
    with session_scope() as session:
        calibration.reset(session, actor.email)
    audit(actor.email, "calibration_reset", "hold_score", "back to the default")
    return {"ok": True}
