import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from app import ratelimit
from app.agents.runner import (
    escalate_case,
    process_timeouts,
    realert_case,
    resume_case,
    spawn_case,
)
from app.db import session_scope
from app.events import event_dict
from app.facts import InvalidPhone, UnknownPhone, check_sim_swap, retrieve_sim_swap_date
from app.ledger import audit
from app.models import (
    AccessRequest,
    AuditLog,
    Case,
    CaseEvent,
    Device,
    Invitation,
    Notification,
    Report,
    SimEvent,
    Transaction,
    User,
)
from app.runtime import runtime
from app.security import (
    REFRESH_MINUTES,
    create_token,
    decode_token,
    hash_password,
    open_totp,
    password_needs_upgrade,
    seal_totp,
    totp_uri,
    totp_valid,
    verify_password,
)
from app.util import (
    COLOMBO,
    OPERATOR_ROLES,
    ROLE_LABELS,
    age_phrase,
    aware,
    colombo_stamp,
    iso,
    nid,
    scoped_bank,
    severity,
    utcnow,
)

log = logging.getLogger("payshield.api")
router = APIRouter()

SCENARIOS = [
    {
        "id": "sim_swap_transfer",
        "title": "SIM swap and Rs. 200,000 to a new payee",
        "detail": "The rule layer holds the money and asks the customer on the registered device.",
    },
    {
        "id": "port_out_critical",
        "title": "Port-out, password reset, and Rs. 800,000",
        "detail": "The rule layer blocks the transfer without waiting for the customer.",
    },
    {
        "id": "large_payment",
        "title": "Rs. 75,000 payment with no SIM signal",
        "detail": "The normal rules release it. Switch on emergency mode for the district first and the same payment is held.",
    },
    {
        "id": "routine_payment",
        "title": "Routine bill of Rs. 4,850",
        "detail": "No new SIM event is added. The rule layer releases the payment.",
    },
]

AGENT_CATALOG = [
    {"id": "supervisor", "name": "Supervisor", "task": "Assigns the telecom and banking agents. The route is fixed.", "kind": "rules"},
    {"id": "telecom", "name": "Telecom Agent", "task": "SIM swap, handset, location, and port-out. Looks further (SIM history) when the first reads call for it.", "kind": "model"},
    {"id": "banking", "name": "Banking Agent", "task": "Payments, new payees, and credential events. Compares the payment with what is usual for this customer.", "kind": "model"},
    {"id": "risk", "name": "Risk Agent", "task": "Turns the two feeds into a score.", "kind": "model"},
    {"id": "policy", "name": "Policy rules", "task": "Hold, block, or release. Not a model.", "kind": "rules"},
    {"id": "alert", "name": "Alert Agent", "task": "Push, email, and an SMS with no link and no code.", "kind": "model"},
    {"id": "verification", "name": "Verification Agent", "task": "Security questions, then an optional liveness placeholder.", "kind": "model"},
    {"id": "response", "name": "Response Agent", "task": "Block the transfer, request a SIM freeze, limit the account.", "kind": "model"},
    {"id": "analytics", "name": "Analytics Agent", "task": "Counts payments by district, province and day and writes the dashboard brief, and may propose a rule change that an analyst must approve.", "kind": "model"},
    {"id": "case", "name": "Case / Report Agent", "task": "Checks each agent note against its facts, then writes the incident report for the fraud desk.", "kind": "model"},
]


class LoginIn(BaseModel):
    email: str
    password: str
    portal: str = "operator"


class TotpIn(BaseModel):
    pending_token: str
    code: str


class RefreshIn(BaseModel):
    refresh_token: str


def _client_ip(request: Request | None) -> str:
    if request is None or request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def _session_tokens(user_id: str, role: str) -> dict:
    return {
        "token": create_token(runtime.settings, user_id, role, purpose="access"),
        "refresh_token": create_token(runtime.settings, user_id, role, purpose="refresh", minutes=REFRESH_MINUTES),
    }


class AccessIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: str
    organisation: str
    role_asked: str
    scope_asked: str
    reason: str = Field(min_length=4, max_length=1000)


class ActivateIn(BaseModel):
    token: str
    email: str
    password: str = Field(min_length=8, max_length=200)


class CheckIn(BaseModel):
    phoneNumber: str
    maxAge: int | None = None


class PhoneIn(BaseModel):
    phoneNumber: str


class DemoIn(BaseModel):
    customer_id: str
    scenario: str


class NudgeIn(BaseModel):
    case_id: str
    action: str


class RespondIn(BaseModel):
    decision: str


class VerifyIn(BaseModel):
    answers: dict
    liveness: bool = False


class DeviceIn(BaseModel):
    label: str = "This phone"
    platform: str = "browser"
    fcm_token: str | None = None


class TotpConfirm(BaseModel):
    code: str


class OverrideIn(BaseModel):
    action: str
    reason: str = Field(min_length=4, max_length=500)


class DecideIn(BaseModel):
    decision: str


class UserStatusIn(BaseModel):
    status: str


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


def _load_users(session, ids: set[str]) -> dict[str, User]:
    if not ids:
        return {}
    rows = session.query(User).filter(User.id.in_(ids)).all()
    return {row.id: row for row in rows}


def _case_passes(case: Case, user: User, bank: str | None, province: str | None, risk: str | None) -> bool:
    forced = scoped_bank(user.role, user.bank, bank)
    if forced and case.bank != forced:
        return False
    if province and province not in {"", "all"} and case.province != province:
        return False
    if risk and risk not in {"", "all"}:
        if severity(case.risk_score) != risk:
            return False
    return True


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


def _case_brief(case: Case, customer: User | None) -> dict:
    return {
        "timers": _timers(case),
        "id": case.id,
        "customer_id": case.customer_id,
        "customer_name": customer.full_name if customer else "",
        "phone": customer.phone if customer else "",
        "account_number": customer.account_number if customer else "",
        "branch": customer.branch if customer else "",
        "bank": case.bank,
        "province": case.province,
        "district": case.district,
        "status": case.status,
        "risk_score": case.risk_score,
        "severity": severity(case.risk_score),
        "policy_decision": case.policy_decision,
        "policy_reason": case.policy_reason,
        "amount_lkr": case.amount_lkr,
        "payee": case.payee,
        "transaction_id": case.transaction_id,
        "created_at": iso(case.created_at),
        "updated_at": iso(case.updated_at),
        "waiting_since": iso(case.waiting_since),
        "alert_count": case.alert_count,
        "customer_decision": case.customer_decision,
        "verification_detail": case.verification_detail,
        "verification_passed": case.verification_passed,
        "origin": case.origin,
        "scenario": case.scenario,
        "sim_freeze_ref": case.sim_freeze_ref,
        "restricted": bool(customer.restricted) if customer else False,
    }


def _visible(role: str, user_id: str, bank: str | None, event: dict) -> bool:
    if role == "customer":
        return event.get("customer_id") == user_id
    if role == "bank_supervisor" and bank:
        return event.get("bank") in {bank, None}
    return role in OPERATOR_ROLES


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


def _db_label() -> str:
    url = runtime.settings.database_url if runtime.settings else ""
    return "postgres" if url.startswith("postgres") else "sqlite"


@router.get("/api/health")
def health():
    settings = runtime.settings
    primary = "mock"
    if settings and settings.gemini_api_key:
        primary = "gemini"
    elif settings and settings.meta_api_key:
        primary = "meta"
    return {
        "ok": True,
        "service": "payshield",
        "database": _db_label(),
        "checkpointer": runtime.checkpointer_kind,
        "redis": "connected" if runtime.redis is not None else "memory",
        "llm": {
            "primary": primary,
            "gemini_configured": bool(settings and settings.gemini_api_key),
            "meta_configured": bool(settings and settings.meta_api_key),
            "gemini_model": settings.gemini_model if settings else "",
            "meta_model": settings.meta_model if settings else "",
            "fallback": "meta" if settings and settings.meta_api_key else "mock",
        },
        "sms": settings.sms_driver if settings else "log",
        "telco": "operator gateway" if settings and settings.telco_base_url else "simulated CAMARA",
        "email": settings.email_driver if settings else "log",
        "push": settings.push_driver if settings else "websocket",
        "timezone": "Asia/Colombo",
        "demo_data": True,
        "realert_seconds": settings.realert_seconds if settings else 180,
        "escalate_seconds": settings.escalate_seconds if settings else 600,
        "jwt_dev_secret": bool(settings and settings.jwt_is_dev_default),
        # What the presenter needs to say truthfully about this run. Counts only: no address, number or key is shown.
        "mode": settings.demo_mode if settings else "deterministic",
        "mode_note": settings.mode_note if settings else "",
        "ignored_by_mode": list(settings.ignored_by_mode) if settings else [],
        "recipients_cleared": {"phones": len(settings.demo_allowed_phones), "emails": len(settings.demo_allowed_emails)} if settings else {},
        "real_actions": {
            "bank_ledger": "synthetic",
            "sim_freeze": "simulated request, no operator integration",
            "sim_swap_data": "operator gateway" if settings and settings.telco_base_url else "synthetic",
            "sms": "real provider" if settings and settings.sms_driver == "textlk" else "logged only",
            "email": "real provider" if settings and settings.email_driver == "smtp" else "logged only",
        },
    }


@router.get("/")
def root():
    return {"service": "PayShield", "docs": "/docs"}


def _find_login_user(session, identifier: str, portal: str) -> User | None:
    """Operators sign in with their email. Customers sign in with the name they registered with."""
    ident = identifier.strip().lower()
    if portal == "customer":
        spaced = " ".join(ident.split())
        matches = (
            session.query(User)
            .filter(User.role == "customer", User.registered.is_(True))
            .filter((User.username == spaced) | (User.email == ident))
            .all()
        )
        return matches[0] if len(matches) == 1 else None
    return session.query(User).filter(User.email == ident).one_or_none()


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request):
    ident = " ".join(body.email.strip().lower().split())
    ratelimit.take("login_ip", _client_ip(request))
    ratelimit.check("login_fail", ident)
    failed = False
    with session_scope() as session:
        user = _find_login_user(session, ident, body.portal)
        failed = user is None or not verify_password(body.password, user.password_hash)
    if failed:
        ratelimit.hit("login_fail", ident)
        audit(ident, "sign_in_failed", ident, "password not recognised")
        raise HTTPException(status_code=401, detail="Username or password is not recognised")
    ratelimit.clear("login_fail", ident)
    with session_scope() as session:
        user = _find_login_user(session, ident, body.portal)
        if password_needs_upgrade(user.password_hash):
            # Legacy PBKDF2 hashes are replaced with Argon2id the first time the password is proven.
            user.password_hash = hash_password(body.password)
        if user.status != "active":
            raise HTTPException(status_code=403, detail="This account is disabled")
        if body.portal == "operator" and user.role == "customer":
            raise HTTPException(status_code=403, detail="Use the customer app for this account")
        if body.portal == "customer" and user.role != "customer":
            raise HTTPException(status_code=403, detail="Operator accounts sign in on the console")
        snapshot = _public_user(user)
        user_id = user.id
        role = user.role
    audit(ident, "sign_in", user_id, f"{body.portal} sign-in")
    return {**_session_tokens(user_id, role), "user": snapshot}


@router.post("/api/auth/totp")
def confirm_login_totp(body: TotpIn):
    try:
        payload = decode_token(runtime.settings, body.pending_token, purpose="totp")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="That sign-in step expired") from exc
    with session_scope() as session:
        user = session.get(User, payload["sub"])
        if user is None or not user.totp_secret or not user.totp_enrolled:
            raise HTTPException(status_code=400, detail="Authenticator is not enrolled")
        ratelimit.check("totp_fail", user.id)
        if not totp_valid(open_totp(user.totp_secret), body.code):
            ratelimit.hit("totp_fail", user.id)
            raise HTTPException(status_code=401, detail="That code is not valid")
        ratelimit.clear("totp_fail", user.id)
        snapshot = _public_user(user)
        user_id, role = user.id, user.role
    return {**_session_tokens(user_id, role), "user": snapshot}


@router.post("/api/auth/refresh")
def refresh_session(body: RefreshIn, request: Request):
    ratelimit.take("refresh", _client_ip(request))
    try:
        payload = decode_token(runtime.settings, body.refresh_token, purpose="refresh")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Session expired") from exc
    with session_scope() as session:
        user = session.get(User, payload.get("sub"))
        if user is None or user.status != "active" or user.role != payload.get("role"):
            raise HTTPException(status_code=401, detail="Session expired")
        user_id, role = user.id, user.role
    # Only a new access token. The refresh token keeps its original expiry, so a session cannot live forever.
    return {"token": create_token(runtime.settings, user_id, role, purpose="access")}


@router.post("/api/auth/request-access")
def request_access(body: AccessIn):
    if body.role_asked not in OPERATOR_ROLES:
        raise HTTPException(status_code=400, detail="Choose an operator role")
    with session_scope() as session:
        session.add(
            AccessRequest(
                id=nid("acr"),
                name=body.name.strip(),
                email=body.email.strip().lower(),
                organisation=body.organisation.strip(),
                role_asked=body.role_asked,
                scope_asked=body.scope_asked.strip(),
                reason=body.reason.strip(),
                state="pending",
                created_at=utcnow(),
            )
        )
    return {"state": "pending"}


@router.post("/api/auth/activate")
def activate(body: ActivateIn):
    email = body.email.strip().lower()
    with session_scope() as session:
        invite = session.get(Invitation, body.token.strip())
        if invite is None or invite.used or invite.email.lower() != email:
            raise HTTPException(status_code=400, detail="That activation token is not valid")
        if session.query(User).filter(User.email == email).one_or_none():
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        session.add(
            User(
                id=nid("usr"),
                email=email,
                full_name=invite.full_name,
                password_hash=hash_password(body.password),
                role=invite.role,
                bank=invite.bank,
                status="active",
                totp_enrolled=False,
                restricted=False,
                created_at=utcnow(),
            )
        )
        invite.used = True
        role_name = invite.role
    audit(email, "activate", email, f"Activated as {role_name}")
    return {"ok": True}


@router.get("/api/me")
def me(user: User = Depends(current_user)):
    return _public_user(user)


def _camara_error(status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={"status": status, "code": code, "message": message})


@router.post("/sim-swap/v1/check")
def camara_check(body: CheckIn):
    try:
        result = check_sim_swap(body.phoneNumber, body.maxAge)
    except InvalidPhone:
        return _camara_error(400, "INVALID_ARGUMENT", "phoneNumber must be E.164")
    except UnknownPhone:
        return _camara_error(404, "NOT_FOUND", "SIM swap data is not available for this phone number.")
    except ValueError as exc:
        return _camara_error(400, "INVALID_ARGUMENT", str(exc))
    return {"swapped": result["swapped"]}


@router.post("/sim-swap/v1/retrieve-date")
def camara_date(body: PhoneIn):
    try:
        result = retrieve_sim_swap_date(body.phoneNumber)
    except InvalidPhone:
        return _camara_error(400, "INVALID_ARGUMENT", "phoneNumber must be E.164")
    except UnknownPhone:
        return _camara_error(404, "NOT_FOUND", "SIM swap data is not available for this phone number.")
    return {"latestSimChange": result["latestSimChange"]}


def _selected_cases(user: User, bank: str | None, province: str | None, risk: str | None):
    with session_scope() as session:
        cases = session.query(Case).all()
        chosen = [case for case in cases if _case_passes(case, user, bank, province, risk)]
        users = _load_users(session, {case.customer_id for case in chosen})
        return chosen, users


@router.get("/api/overview")
def overview(
    user: User = Depends(require_operator),
    bank: str | None = None,
    province: str | None = None,
    risk: str | None = None,
):
    cases, users = _selected_cases(user, bank, province, risk)
    # Open holds are transactions still held. Recompute from the ledger so a released case drops out.
    with session_scope() as session:
        tx_rows = session.query(Transaction).filter(Transaction.status == "held").all()
        tx_cases = {tx.case_id: tx for tx in tx_rows}
    held_cases = []
    for case in cases:
        tx = tx_cases.get(case.id)
        if tx is not None:
            held_cases.append(case)
    blocked = [case for case in cases if case.status == "closed_blocked"]
    waiting = [case for case in cases if case.status in {"waiting_customer", "waiting_verification"}]
    open_cases = [case for case in cases if case.status not in {"closed_blocked", "closed_released", "closed_cleared"}]
    now = utcnow()
    forced = scoped_bank(user.role, user.bank, bank)
    with session_scope() as session:
        sim_q = session.query(SimEvent).filter(SimEvent.event_type == "sim_swap", SimEvent.occurred_at >= now - timedelta(days=7))
        sim_rows = sim_q.all()
        if forced or (province and province not in {"", "all"}):
            phones = {
                customer.phone
                for customer in session.query(User).filter(User.role == "customer").all()
                if (not forced or customer.bank == forced)
                and (not province or province in {"", "all"} or customer.province == province)
            }
            sim_rows = [row for row in sim_rows if row.phone in phones]
    weekly = _weekly(cases)
    districts: dict[str, dict] = {}
    for case in cases:
        bucket = districts.setdefault(case.district or "Unknown", {"district": case.district or "Unknown", "cases": 0, "amount_lkr": 0})
        bucket["cases"] += 1
        bucket["amount_lkr"] += case.amount_lkr or 0
    today = now.astimezone(COLOMBO).date()
    cases_today = sum(1 for case in cases if aware(case.created_at).astimezone(COLOMBO).date() == today)
    bands = {"crit": 0, "high": 0, "med": 0, "low": 0}
    for case in cases:
        score = case.risk_score or 0
        if score >= 85:
            bands["crit"] += 1
        elif score >= 70:
            bands["high"] += 1
        elif score >= 40:
            bands["med"] += 1
        else:
            bands["low"] += 1
    reply_samples: list[float] = []
    with session_scope() as session:
        answers = session.query(CaseEvent).filter(CaseEvent.agent == "customer", CaseEvent.state == "done").all()
        by_case = {case.id: case for case in cases}
        for event in answers:
            case = by_case.get(event.case_id)
            if case is None:
                continue
            start = case.waiting_since or case.created_at
            delta = (aware(event.created_at) - aware(start)).total_seconds()
            if 0 <= delta <= 24 * 3600:
                reply_samples.append(delta)
    avg_reply = int(sum(reply_samples) / len(reply_samples)) if reply_samples else None
    queue = sorted(open_cases, key=lambda case: aware(case.updated_at), reverse=True)[:8]
    top = sorted(cases, key=lambda case: (case.amount_lkr or 0, case.risk_score or 0), reverse=True)[:8]
    by_bank: dict[str, dict] = {}
    for case in cases:
        bank_row = by_bank.setdefault(case.bank or "Unknown", {"bank": case.bank or "Unknown", "cases": 0, "amount_lkr": 0})
        bank_row["cases"] += 1
        bank_row["amount_lkr"] += case.amount_lkr or 0
    return {
        "kpis": {
            "cases": len(cases),
            "open_cases": len(open_cases),
            "waiting": len(waiting),
            "held_lkr": sum(case.amount_lkr or 0 for case in held_cases),
            "held_count": len(held_cases),
            "sim_swaps_7d": len(sim_rows),
            "blocked_lkr": sum(case.amount_lkr or 0 for case in blocked),
            "blocked_count": len(blocked),
            "cases_today": cases_today,
            "avg_reply_seconds": avg_reply,
            "severity": bands,
        },
        "weekly": weekly,
        "queue": [_case_brief(case, users.get(case.customer_id)) for case in queue],
        "districts": sorted(districts.values(), key=lambda item: item["amount_lkr"], reverse=True)[:6],
        "by_bank": sorted(by_bank.values(), key=lambda item: item["amount_lkr"], reverse=True),
        "top_cases": [_case_brief(case, users.get(case.customer_id)) for case in top],
    }


def _weekly(cases: list[Case]) -> list[dict]:
    from app.util import COLOMBO

    today = utcnow().astimezone(COLOMBO).date()
    monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=offset) for offset in range(11, -1, -1)]
    buckets = {start: {"label": start.strftime("%d %b"), "week": start.isoformat(), "cases": 0, "blocked_lkr": 0, "held_lkr": 0} for start in starts}
    for case in cases:
        local = aware(case.created_at).astimezone(COLOMBO).date()
        start = local - timedelta(days=local.weekday())
        if start not in buckets:
            continue
        buckets[start]["cases"] += 1
        if case.status == "closed_blocked":
            buckets[start]["blocked_lkr"] += case.amount_lkr or 0
        if case.policy_decision == "hold":
            buckets[start]["held_lkr"] += case.amount_lkr or 0
    return [buckets[start] for start in starts]


@router.get("/api/cases")
def list_cases(
    user: User = Depends(require_operator),
    bank: str | None = None,
    province: str | None = None,
    risk: str | None = None,
    status: str | None = None,
):
    cases, users = _selected_cases(user, bank, province, risk)
    if status and status not in {"", "all"}:
        cases = [case for case in cases if case.status == status]
    cases.sort(key=lambda case: aware(case.updated_at), reverse=True)
    return {"cases": [_case_brief(case, users.get(case.customer_id)) for case in cases]}


@router.get("/api/cases/{case_id}")
def get_case(case_id: str, user: User = Depends(require_operator)):
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None or not _case_passes(case, user, None, None, None):
            raise HTTPException(status_code=404, detail="Case not found")
        customer = session.get(User, case.customer_id)
        tx = session.get(Transaction, case.transaction_id) if case.transaction_id else None
        events = (
            session.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id)
            .order_by(CaseEvent.created_at.asc())
            .all()
        )
        report = session.query(Report).filter(Report.case_id == case_id).one_or_none()
        return {
            "case": {
                **_case_brief(case, customer),
                "telecom_summary": case.telecom_summary,
                "banking_summary": case.banking_summary,
                "risk_summary": case.risk_summary,
                "risk_factors": case.risk_factors or [],
                "pending_prompt": case.pending_prompt,
                "provider_counts": case.provider_counts or {},
            },
            "customer": {
                "id": customer.id,
                "full_name": customer.full_name,
                "email": customer.email,
                "phone": customer.phone,
                "bank": customer.bank,
                "account_number": customer.account_number,
                "branch": customer.branch,
                "province": customer.province,
                "district": customer.district,
                "restricted": customer.restricted,
                "restricted_actions": customer.restricted_actions or [],
            },
            "transaction": None
            if tx is None
            else {
                "id": tx.id,
                "kind": tx.kind,
                "amount_lkr": tx.amount_lkr,
                "payee": tx.payee,
                "payee_bank": tx.payee_bank,
                "payee_account": tx.payee_account,
                "new_payee": tx.new_payee,
                "status": tx.status,
                "occurred_at": iso(tx.occurred_at),
            },
            "events": [event_dict(event, customer.id, case.bank) for event in events],
            "report": None if report is None else {"id": report.id, "title": report.title, "created_at": iso(report.created_at)},
        }


@router.get("/api/events")
def list_events(
    user: User = Depends(require_operator),
    case_id: str | None = None,
    limit: int = Query(default=200, le=500),
):
    with session_scope() as session:
        query = session.query(CaseEvent, Case).join(Case, Case.id == CaseEvent.case_id)
        if case_id:
            query = query.filter(CaseEvent.case_id == case_id)
        rows = query.order_by(CaseEvent.created_at.desc()).limit(limit).all()
        events = []
        for event, case in rows:
            if not _case_passes(case, user, None, None, None):
                continue
            events.append(event_dict(event, case.customer_id, case.bank))
        events.reverse()
        return {"events": events}


@router.get("/api/sim-events")
def sim_events(
    user: User = Depends(require_operator),
    bank: str | None = None,
    province: str | None = None,
):
    forced = scoped_bank(user.role, user.bank, bank)
    with session_scope() as session:
        customers = session.query(User).filter(User.role == "customer").all()
        by_phone = {}
        for customer in customers:
            if forced and customer.bank != forced:
                continue
            if province and province not in {"", "all"} and customer.province != province:
                continue
            by_phone[customer.phone] = customer
        rows = session.query(SimEvent).order_by(SimEvent.occurred_at.desc()).limit(400).all()
        events = []
        for row in rows:
            customer = by_phone.get(row.phone)
            if customer is None:
                continue
            events.append(
                {
                    "id": row.id,
                    "phone": row.phone,
                    "event_type": row.event_type,
                    "occurred_at": iso(row.occurred_at),
                    "age": age_phrase(row.occurred_at),
                    "detail": row.detail or {},
                    "origin": row.origin,
                    "case_id": row.case_id,
                    "customer_id": customer.id,
                    "customer_name": customer.full_name,
                    "bank": customer.bank,
                    "district": customer.district,
                    "province": customer.province,
                }
            )
        newest = {}
        for event in events:
            current = newest.get(event["event_type"])
            if current is None or (event["occurred_at"] or "") > (current["occurred_at"] or ""):
                newest[event["event_type"]] = event
    def source(label, kind, basis):
        event = newest.get(kind)
        return {
            "source": label,
            "granularity": "event",
            "newest": None if event is None else event["occurred_at"],
            "age": "never" if event is None else event["age"],
            "status": "Mock feed" if event else "No rows",
            "basis": basis,
        }
    sources = [
        source("CAMARA SIM swap", "sim_swap", "Mock operator feed shaped like checkSimSwap and retrieveSimSwapDate."),
        source("Handset / IMEI", "new_imei", "Mock device-change feed. Not part of the CAMARA SIM swap schema."),
        source("Location change", "location_change", "Mock serving-location feed."),
        source("Port-out", "port_out", "Mock port-out requests."),
        source("SIM freeze requests", "sim_freeze", "Raised by the response agent. The operator mock records the reference."),
    ]
    return {"sources": sources, "events": events}


@router.get("/api/holds")
def holds(
    user: User = Depends(require_operator),
    bank: str | None = None,
    province: str | None = None,
):
    forced = scoped_bank(user.role, user.bank, bank)
    with session_scope() as session:
        rows = (
            session.query(Transaction, User, Case)
            .join(User, User.id == Transaction.customer_id)
            .outerjoin(Case, Case.id == Transaction.case_id)
            .filter(Transaction.status == "held")
            .all()
        )
        items = []
        for tx, customer, case in rows:
            if forced and customer.bank != forced:
                continue
            if province and province not in {"", "all"} and customer.province != province:
                continue
            items.append(
                {
                    "transaction_id": tx.id,
                    "case_id": tx.case_id,
                    "customer_id": customer.id,
                    "customer_name": customer.full_name,
                    "bank": customer.bank,
                    "province": customer.province,
                    "district": customer.district,
                    "amount_lkr": tx.amount_lkr,
                    "payee": tx.payee,
                    "payee_bank": tx.payee_bank,
                    "new_payee": tx.new_payee,
                    "occurred_at": iso(tx.occurred_at),
                    "case_status": None if case is None else case.status,
                    "risk_score": None if case is None else case.risk_score,
                    "severity": severity(None if case is None else case.risk_score),
                    "policy_decision": None if case is None else case.policy_decision,
                    "policy_reason": None if case is None else case.policy_reason,
                    "can_override": user.role in {"bank_supervisor", "super_admin"},
                }
            )
        items.sort(key=lambda item: item["amount_lkr"], reverse=True)
        return {"holds": items}


@router.post("/api/transactions/{transaction_id}/override")
def override(transaction_id: str, body: OverrideIn, user: User = Depends(require_writer)):
    if user.role not in {"bank_supervisor", "super_admin"}:
        raise HTTPException(status_code=403, detail="Only a bank supervisor can release or block a held payment")
    if body.action not in {"release", "block"}:
        raise HTTPException(status_code=400, detail="Action must be release or block")
    with session_scope() as session:
        tx = session.get(Transaction, transaction_id)
        if tx is None or tx.status != "held":
            raise HTTPException(status_code=409, detail="Only a held payment can be overridden")
        customer = session.get(User, tx.customer_id)
        if user.role == "bank_supervisor" and user.bank and customer.bank != user.bank:
            raise HTTPException(status_code=403, detail="That payment is outside your bank")
        case = session.get(Case, tx.case_id) if tx.case_id else None
        tx_customer = tx.customer_id
        detail = dict(tx.detail or {})
        detail["override"] = {"by": user.email, "action": body.action, "reason": body.reason.strip()}
        tx.detail = detail
        if body.action == "release" and tx.kind == "transfer":
            # The same rule as ledger.release_transaction: the money leaves the account when the payment is released, once.
            customer.balance_lkr = max(0, int(customer.balance_lkr or 0) - int(tx.amount_lkr or 0))
        tx.status = "released" if body.action == "release" else "blocked"
        case_id = case.id if case else None
        if case is not None:
            case.status = "closed_released" if body.action == "release" else "closed_blocked"
            case.customer_decision = "supervisor_" + body.action
            case.pending_prompt = None
            case.updated_at = utcnow()
            note = (case.policy_reason or "") + f" Supervisor {body.action}: {body.reason.strip()}"
            case.policy_reason = note.strip()
    audit(user.email, "supervisor_override", transaction_id, f"{body.action}: {body.reason.strip()}")
    if case_id:
        from app.events import emit

        emit(
            case_id,
            agent="supervisor",
            state="done",
            tool_name="supervisor_override",
            tool_args={"action": body.action, "transaction_id": transaction_id},
            result_summary=f"Supervisor {body.action}. {body.reason.strip()}",
            provider="rules",
            model="deterministic",
        )
        # The case is closed, so the customer is told and the same record is left as for any other outcome.
        from app.agents import tools

        released = body.action == "release"
        tools.outcome_notice(
            tx_customer,
            case_id,
            "Payment released" if released else "Payment blocked",
            "After a check by the bank's fraud desk your payment was released." if released
            else "The bank's fraud desk blocked this transfer and no money left your account. Call your bank on the number printed on your card.",
        )
        tools.review_notes(case_id)
        tools.generate_report(case_id)
        tools.deliver_report(case_id)
    return {"ok": True, "status": "released" if body.action == "release" else "blocked"}


@router.get("/api/map")
def map_data(
    user: User = Depends(require_operator),
    bank: str | None = None,
    province: str | None = None,
    risk: str | None = None,
):
    forced = scoped_bank(user.role, user.bank, bank)
    with session_scope() as session:
        customers = session.query(User).filter(User.role == "customer").all()
        cases = session.query(Case).all()
        events = session.query(SimEvent).order_by(SimEvent.occurred_at.desc()).limit(200).all()
    by_customer: dict[str, list[Case]] = {}
    for case in cases:
        by_customer.setdefault(case.customer_id, []).append(case)
    points = []
    for customer in customers:
        if customer.lat is None or customer.lng is None:
            continue  # a customer with no known position cannot be drawn
        if forced and customer.bank != forced:
            continue
        if province and province not in {"", "all"} and customer.province != province:
            continue
        related = by_customer.get(customer.id, [])
        if risk and risk not in {"", "all"}:
            if not any(severity(case.risk_score) == risk for case in related):
                continue
        open_cases = [case for case in related if case.status not in {"closed_blocked", "closed_released", "closed_cleared"}]
        score = max((case.risk_score or 0) for case in related) if related else 0
        points.append(
            {
                "id": customer.id,
                "name": customer.full_name,
                "bank": customer.bank,
                "province": customer.province,
                "district": customer.district,
                "lat": customer.lat,
                "lng": customer.lng,
                "risk_score": score,
                "severity": severity(score),
                "open_cases": len(open_cases),
                "held_lkr": sum(case.amount_lkr or 0 for case in open_cases if case.policy_decision == "hold"),
            }
        )
    phones = {customer.phone: customer for customer in customers}
    sim_points = []
    for event in events:
        customer = phones.get(event.phone)
        if customer is None or customer.lat is None:
            continue
        if forced and customer.bank != forced:
            continue
        sim_points.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "lat": customer.lat,
                "lng": customer.lng,
                "district": customer.district,
                "occurred_at": iso(event.occurred_at),
                "customer_name": customer.full_name,
            }
        )
    return {"customers": points, "sim_events": sim_points}


@router.get("/api/agents")
def agents(user: User = Depends(require_operator)):
    settings = runtime.settings
    configured = settings.gemini_model if settings.gemini_api_key else ("meta:" + settings.meta_model if settings.meta_api_key else "mock-deterministic")
    fallback = settings.meta_model if settings.meta_api_key else "mock-deterministic"
    with session_scope() as session:
        rows = session.query(CaseEvent).filter(CaseEvent.phase.is_distinct_from("started")).all()  # a start marker is not a finished call
    grouped: dict[str, list[CaseEvent]] = {}
    for row in rows:
        grouped.setdefault(row.agent, []).append(row)
    payload = []
    for spec in AGENT_CATALOG:
        events = grouped.get(spec["id"], [])
        errors = [event for event in events if event.state == "error"]
        calls = [event for event in events if event.state in {"done", "calling_tool", "error"}]
        latencies = [event.latency_ms for event in events if event.latency_ms is not None]
        fallbacks = [event for event in events if event.fell_back]
        provided = [event for event in events if event.provider]
        provided.sort(key=lambda event: aware(event.created_at))
        last = provided[-1] if provided else None
        success = None
        if calls:
            success = round(100 * (len(calls) - len(errors)) / len(calls), 1)
        payload.append(
            {
                **spec,
                "configured_model": "deterministic" if spec["kind"] == "rules" else configured,
                "fallback_model": None if spec["kind"] == "rules" else fallback,
                "last_provider": None if last is None else last.provider,
                "last_model": None if last is None else last.model,
                "calls": len(calls),
                "errors": len(errors),
                "success_rate": success,
                "latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
                "fallbacks": len(fallbacks),
            }
        )
    return {"agents": payload, "primary": health()["llm"]}


@router.get("/api/reports")
def reports(user: User = Depends(require_operator)):
    with session_scope() as session:
        rows = session.query(Report, Case).join(Case, Case.id == Report.case_id).all()
        items = []
        for report, case in rows:
            if not _case_passes(case, user, None, None, None):
                continue
            items.append(
                {
                    "id": report.id,
                    "case_id": case.id,
                    "title": report.title,
                    "created_at": iso(report.created_at),
                    "bank": case.bank,
                    "status": case.status,
                    "amount_lkr": case.amount_lkr,
                    "customer_id": case.customer_id,
                }
            )
        items.sort(key=lambda item: item["created_at"] or "", reverse=True)
        return {"reports": items}


def _report_for(user: User, report_id: str) -> tuple[Report, Case]:
    with session_scope() as session:
        report = session.get(Report, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        case = session.get(Case, report.case_id)
        if case is None or not _case_passes(case, user, None, None, None):
            raise HTTPException(status_code=404, detail="Report not found")
        session.expunge(report)
        session.expunge(case)
        return report, case


@router.get("/api/reports/{report_id}")
def report_detail(report_id: str, user: User = Depends(require_operator)):
    report, case = _report_for(user, report_id)
    return {
        "id": report.id,
        "case_id": case.id,
        "title": report.title,
        "markdown": report.markdown,
        "created_at": iso(report.created_at),
    }


@router.get("/api/reports/{report_id}/markdown")
def report_markdown(report_id: str, user: User = Depends(require_operator)):
    report, _case = _report_for(user, report_id)
    audit(user.email, "report_download", report.case_id, "markdown")
    return PlainTextResponse(report.markdown, media_type="text/markdown")


@router.get("/api/reports/{report_id}/pdf")
def report_pdf(report_id: str, user: User = Depends(require_operator)):
    report, _case = _report_for(user, report_id)
    audit(user.email, "report_download", report.case_id, "pdf")
    return _pdf_response(report.case_id, "analyst", report.sha256)


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


@router.get("/api/admin/summary")
def admin_summary(user: User = Depends(require_operator)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Administration is limited to the super admin")
    with session_scope() as session:
        requests = session.query(AccessRequest).all()
        users = session.query(User).filter(User.role != "customer").all()
        audits = session.query(AuditLog).count()
        recent_refused = session.query(AuditLog).filter(AuditLog.action == "access_refused").count()
        return {
            "pending_requests": sum(1 for row in requests if row.state == "pending"),
            "users": len(users),
            "disabled_users": sum(1 for row in users if row.status != "active"),
            "active_users": sum(1 for row in users if row.status == "active"),
            "audit_entries": audits,
            "refused_recent": recent_refused,
        }


@router.get("/api/admin/requests")
def admin_requests(user: User = Depends(require_operator)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Administration is limited to the super admin")
    with session_scope() as session:
        rows = session.query(AccessRequest).order_by(AccessRequest.created_at.desc()).all()
        return {
            "requests": [
                {
                    "id": row.id,
                    "name": row.name,
                    "email": row.email,
                    "organisation": row.organisation,
                    "role_asked": row.role_asked,
                    "scope_asked": row.scope_asked,
                    "reason": row.reason,
                    "state": row.state,
                    "decided_by": row.decided_by,
                    "created_at": iso(row.created_at),
                }
                for row in rows
            ]
        }


@router.post("/api/admin/requests/{request_id}")
def decide_request(request_id: str, body: DecideIn, user: User = Depends(require_writer)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Administration is limited to the super admin")
    if body.decision not in {"approve", "refuse"}:
        raise HTTPException(status_code=400, detail="Decision must be approve or refuse")
    invite_token = None
    with session_scope() as session:
        row = session.get(AccessRequest, request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Request not found")
        if row.state != "pending":
            raise HTTPException(status_code=409, detail="That request is already decided")
        row.state = "approved" if body.decision == "approve" else "refused"
        row.decided_by = user.email
        if body.decision == "approve" and session.query(User).filter(User.email == row.email).one_or_none() is None:
            invite_token = "PS-" + nid("inv").split("_")[-1][:8].upper()
            session.add(
                Invitation(
                    token=invite_token,
                    email=row.email,
                    full_name=row.name,
                    role=row.role_asked if row.role_asked in OPERATOR_ROLES else "fraud_analyst",
                    bank=None,
                    used=False,
                )
            )
        request_email = row.email
    audit(user.email, "access_" + body.decision, request_id, request_email)
    return {"state": "approved" if body.decision == "approve" else "refused", "invite_token": invite_token}


@router.get("/api/admin/users")
def admin_users(user: User = Depends(require_operator)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Administration is limited to the super admin")
    with session_scope() as session:
        rows = session.query(User).filter(User.role != "customer").order_by(User.full_name.asc()).all()
        return {"users": [_public_user(row) for row in rows]}


@router.post("/api/admin/users/{user_id}")
def set_user_status(user_id: str, body: UserStatusIn, user: User = Depends(require_writer)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Administration is limited to the super admin")
    if body.status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="Status must be active or disabled")
    if user_id == user.id and body.status == "disabled":
        raise HTTPException(status_code=409, detail="You cannot disable your own account")
    with session_scope() as session:
        target = session.get(User, user_id)
        if target is None or target.role == "customer":
            raise HTTPException(status_code=404, detail="User not found")
        target.status = body.status
    audit(user.email, "user_" + body.status, user_id, body.status)
    return {"ok": True}


@router.get("/api/admin/audit")
def admin_audit(user: User = Depends(require_operator)):
    if user.role not in {"super_admin", "auditor"}:
        raise HTTPException(status_code=403, detail="The audit log is limited to the super admin and the auditor")
    with session_scope() as session:
        rows = session.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()
        return {
            "entries": [
                {
                    "id": row.id,
                    "actor_email": row.actor_email,
                    "action": row.action,
                    "target": row.target,
                    "detail": row.detail,
                    "created_at": iso(row.created_at),
                    "created_label": colombo_stamp(row.created_at),
                }
                for row in rows
            ]
        }


@router.get("/api/settings")
def settings_view(user: User = Depends(require_operator)):
    body = health()
    body["step_delay_ms"] = runtime.settings.step_delay_ms
    body["demo_assist"] = runtime.settings.demo_assist
    body["telco"] = "camara-http" if runtime.settings.telco_base_url else "camara-mock"
    body["actor"] = _public_user(user)
    return body


@router.post("/api/demo/trigger", dependencies=[Depends(require_drills)])
def demo_trigger(body: DemoIn, user: User = Depends(require_writer)):
    from app.demo import trigger

    try:
        case_id = trigger(body.customer_id, body.scenario)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Unknown customer") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(user.email, "demo_trigger", case_id, body.scenario)
    spawn_case(case_id)
    return {"case_id": case_id}


class UnregisterIn(BaseModel):
    customer_id: str


class SimEventIn(BaseModel):
    phone: str
    event_type: str
    occurred_at: datetime | None = None
    cancelled: bool = False
    event_id: str | None = Field(default=None, max_length=60)
    source: str = Field(default="injected", max_length=20)


@router.post("/api/sim-events", dependencies=[Depends(require_drills)])
def inject_sim_event(body: SimEventIn, user: User = Depends(require_writer)):
    from app.ingest import ingest

    result = ingest(body.phone, body.event_type, body.occurred_at or utcnow(), {"cancelled": True} if body.cancelled else {}, body.source, body.event_id)
    if result["status"] == "rejected":
        raise HTTPException(status_code=422, detail=result["reason"])
    audit(user.email, "sim_event_injected", result.get("customer_id") or "-", f"{body.event_type} {result['status']}")
    return result


@router.post("/api/demo/unregister", dependencies=[Depends(require_drills)])
def demo_unregister(body: UnregisterIn, user: User = Depends(require_writer)):
    from app.demo import unregister

    if not unregister(body.customer_id):
        raise HTTPException(status_code=404, detail="Unknown customer")
    audit(user.email, "demo_unregister", body.customer_id, "returned to first-time state")
    return {"ok": True}


@router.post("/api/demo/reset", dependencies=[Depends(require_drills)])
def demo_reset(user: User = Depends(require_writer)):
    from app.demo import reset_demo

    result = reset_demo()
    audit(user.email, "demo_reset", "demo", str(result))
    return result


@router.get("/api/demo/assist", dependencies=[Depends(require_drills)])
def demo_assist(customer_id: str, user: User = Depends(require_operator)):
    from app.demo import assist

    try:
        return assist(customer_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Unknown customer") from exc


@router.get("/api/demo/customers", dependencies=[Depends(require_drills)])
def demo_customers(user: User = Depends(require_operator)):
    from app.demo import list_customers

    return {"customers": list_customers(), "scenarios": SCENARIOS}


@router.post("/api/demo/nudge")
def demo_nudge(body: NudgeIn, user: User = Depends(require_writer)):
    if body.action == "realert":
        ok = realert_case(body.case_id)
    elif body.action == "escalate":
        ok = escalate_case(body.case_id)
    else:
        raise HTTPException(status_code=400, detail="Action must be realert or escalate")
    if not ok:
        raise HTTPException(status_code=409, detail="That case is not waiting for the customer")
    audit(user.email, "demo_" + body.action, body.case_id, body.action)
    return {"ok": True}


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
        secret = pyotp.random_base32()
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
        session.expunge(user)
        return user


@router.post("/api/customer/totp/confirm")
def totp_confirm(body: TotpConfirm, authorization: str | None = Header(default=None)):
    user = _pending_or_customer(authorization)
    with session_scope() as session:
        row = session.get(User, user.id)
        ratelimit.check("totp_fail", row.id)
        if not row.totp_secret or not totp_valid(open_totp(row.totp_secret), body.code):
            ratelimit.hit("totp_fail", row.id)
            raise HTTPException(status_code=401, detail="That code is not valid")
        ratelimit.clear("totp_fail", row.id)
        row.totp_enrolled = True
        user_id, role = row.id, row.role
        snapshot = _public_user(row)
    return {**_session_tokens(user_id, role), "user": snapshot}


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
    ok = resume_case(case_id, {"answers": body.answers, "liveness": body.liveness}, "waiting_verification")
    if not ok:
        raise HTTPException(status_code=409, detail="This payment is not waiting for security answers")
    return _customer_case_state(case_id, user.id)


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


def _snapshot(role: str, user_id: str, bank: str | None) -> list[dict]:
    with session_scope() as session:
        rows = (
            session.query(CaseEvent, Case)
            .join(Case, Case.id == CaseEvent.case_id)
            .order_by(CaseEvent.created_at.desc())
            .limit(250)
            .all()
        )
        events = []
        for event, case in rows:
            payload = event_dict(event, case.customer_id, case.bank)
            if _visible(role, user_id, bank, payload):
                events.append(payload)
        events.reverse()
        return events


@router.websocket("/ws")
async def stream(websocket: WebSocket):
    await websocket.accept()
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return
    try:
        payload = decode_token(runtime.settings, token, purpose="access")
    except Exception:
        await websocket.close(code=4401)
        return
    with session_scope() as session:
        user = session.get(User, payload["sub"])
        if user is None or user.status != "active":
            await websocket.close(code=4401)
            return
        role, user_id, bank = user.role, user.id, user.bank
    queue = runtime.bus.subscribe()
    closed = asyncio.ensure_future(_until_closed(websocket))
    try:
        await websocket.send_json({"type": "snapshot", "events": _snapshot(role, user_id, bank)})
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _pending = await asyncio.wait({getter, closed}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
            if closed in done:
                getter.cancel()
                break  # the client left: stop at once rather than at the next ping
            if getter not in done:
                getter.cancel()
                await websocket.send_json({"type": "ping"})
                continue
            event = getter.result()
            if not _visible(role, user_id, bank, event):
                continue
            await websocket.send_json({"type": "event", "event": event})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.info("websocket closed")
    finally:
        closed.cancel()
        runtime.bus.unsubscribe(queue)


async def _until_closed(websocket: WebSocket) -> None:
    """Returns when the client disconnects. Anything the client sends is read and ignored."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


# Imported for tests that want to drive the sweeper without the HTTP layer.
__all__ = ["process_timeouts", "router"]
