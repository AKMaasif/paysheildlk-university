"""auth routes (split from app/api.py)."""
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from pydantic import BaseModel, Field

from app import ratelimit
from app.db import session_scope
from app.ledger import audit
from app.models import (
    AccessRequest,
    Invitation,
    User,
)
from app.routes.common import (
    _client_ip,
    _pending_step,
    _public_user,
    _session_tokens,
    current_user,
)
from app.runtime import runtime
from app.security import (
    create_token,
    decode_token,
    hash_password,
    open_totp,
    password_needs_upgrade,
    totp_accept,
    verify_password,
)
from app.util import (
    OPERATOR_ROLES,
    nid,
    utcnow,
)

log = logging.getLogger("payshield.api")
router = APIRouter()


class LoginIn(BaseModel):
    email: str
    password: str
    portal: str = "operator"


class TotpIn(BaseModel):
    pending_token: str
    code: str


class RefreshIn(BaseModel):
    refresh_token: str


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
    ip = _client_ip(request)
    ratelimit.take("login_ip", ip)
    attempt = f"{ident}|{ip}"
    ratelimit.check("login_fail", attempt)
    ratelimit.check("login_fail_user", ident)
    failed = False
    with session_scope() as session:
        user = _find_login_user(session, ident, body.portal)
        failed = user is None or not verify_password(body.password, user.password_hash)
    if failed:
        ratelimit.hit("login_fail", attempt)
        ratelimit.hit("login_fail_user", ident)
        audit(ident, "sign_in_failed", ident, "password not recognised")
        raise HTTPException(status_code=401, detail="Username or password is not recognised")
    ratelimit.clear("login_fail", attempt)
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
        enrolled = bool(user.totp_enrolled and user.totp_secret)
    if role == "customer":
        audit(ident, "sign_in_password_ok", user_id, "customer password accepted, authenticator code required" if enrolled else "customer password accepted, authenticator set-up required")
        return _pending_step(user_id, enrolled)
    audit(ident, "sign_in", user_id, f"{body.portal} sign-in")
    return {**_session_tokens(user_id, role), "user": snapshot}


@router.post("/api/auth/totp")
def confirm_login_totp(body: TotpIn, request: Request):
    ratelimit.take("login_ip", _client_ip(request))
    try:
        payload = decode_token(runtime.settings, body.pending_token, purpose="totp")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="That sign-in step expired. Enter your password again.") from exc
    refused = None
    with session_scope() as session:
        user = session.get(User, payload["sub"])
        if user is None or user.status != "active" or not user.totp_secret or not user.totp_enrolled:
            raise HTTPException(status_code=400, detail="Authenticator is not set up for this account")
        ratelimit.check("totp_fail", user.id)
        if not totp_accept(user.id, open_totp(user.totp_secret), body.code):
            ratelimit.hit("totp_fail", user.id)
            refused = (user.username or user.email, user.id)
        else:
            ratelimit.clear("totp_fail", user.id)
            snapshot = _public_user(user)
            user_id, role, who = user.id, user.role, user.username or user.email
    if refused:
        audit(refused[0], "sign_in_code_failed", refused[1], "authenticator code not accepted")
        raise HTTPException(status_code=401, detail="That code is not valid, or it was already used. Wait for your authenticator app to show a new 6-digit code and try again.")
    audit(who, "sign_in", user_id, "customer sign-in with authenticator code")
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
