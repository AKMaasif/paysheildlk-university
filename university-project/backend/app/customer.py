"""The customer's own site: first-time registration, account summary and password change.

A customer does not create an account here. The bank already holds the record (name, email, NIC,
mobile number and account number, added from the console). Registration proves the person typing
matches that record, takes a live face scan, and lets them choose a password. After that they sign in
with their username (the one they chose, or the one made for them) and the password.
"""

import base64
import hashlib
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import ratelimit
from app.api import _client_ip, _public_user, require_customer
from app.crypto import encrypt
from app.db import session_scope
from app.ledger import audit
from app.liveness import analyse
from app.models import Device, Transaction, User
from app.runtime import runtime
from app.security import hash_password, verify_password
from app.util import aware, iso, utcnow

router = APIRouter()

COLOMBO = ZoneInfo("Asia/Colombo")
MONEY_KINDS = {"transfer", "bill", "purchase", "salary"}
KIND_LABEL = {"transfer": "Transfer", "bill": "Bill payment", "purchase": "Card purchase", "salary": "Salary"}
STATUS_LABEL = {
    "completed": "Completed",
    "released": "Completed",
    "held": "On hold",
    "blocked": "Blocked",
    "pending": "Being checked",
}
NO_MATCH = "These details do not match a BankCylone account. Check them against your bank records."


# ---------- normalising what people type

USERNAME_RULE = re.compile(r"[a-z0-9][a-z0-9._-]{3,29}")


def new_username(session, user_id: str, wanted: str | None, display_name: str) -> str:
    """The name a customer signs in with. A chosen name must be 4 to 30 letters, digits, dots, dashes or underscores and
    not already taken (letters are compared in lower case). With none chosen, one is made from the name plus four random
    characters, so it is neither a guess from the bank's records nor a clash between two people with the same name."""
    import secrets

    def taken(name: str) -> bool:
        return session.query(User).filter(User.username == name, User.id != user_id).first() is not None

    chosen = (wanted or "").strip().lower()
    if chosen:
        if not USERNAME_RULE.fullmatch(chosen):
            raise HTTPException(status_code=422, detail="Choose a username of 4 to 30 characters: letters, digits, dot, dash or underscore.")
        if taken(chosen):
            raise HTTPException(status_code=409, detail="That username is already taken. Choose another.")
        return chosen
    base = re.sub(r"[^a-z0-9]+", ".", display_name.lower()).strip(".")[:20] or "customer"
    for _ in range(20):
        candidate = f"{base}-{secrets.token_hex(2)}"
        if not taken(candidate):
            return candidate
    raise HTTPException(status_code=503, detail="Could not make a username. Please choose one.")


def clean_name(value: str) -> str:
    return " ".join((value or "").lower().split())


def clean_nic(value: str) -> str:
    return re.sub(r"[\s-]", "", value or "").upper()


def clean_account(value: str) -> str:
    return re.sub(r"[\s-]", "", value or "")


def clean_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("94") and len(digits) == 11:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+94" + digits[1:]
    if len(digits) == 9:
        return "+94" + digits
    return "+" + digits if digits else ""


def password_problem(password: str, *, name: str = "", nic: str = "", email: str = "") -> str | None:
    """Plain rules a person can follow. Returns what to fix, or None when the password is acceptable."""
    if len(password) < 8:
        return "Use at least 8 characters."
    if len(password) > 128:
        return "Use no more than 128 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Use at least one letter and one number."
    low = password.lower()
    if low in {"password1", "password123", "12345678a", "qwerty123", "bankcylone1"}:
        return "That password is too common. Choose another."
    if any(part and len(part) >= 4 and part in low for part in re.split(r"\s+", clean_name(name))):
        return "Do not use your name in your password."
    if nic and clean_nic(nic).lower() in low:
        return "Do not use your NIC number in your password."
    if email and email.split("@")[0].lower() in low and len(email.split("@")[0]) >= 4:
        return "Do not use your email in your password."
    return None


def mask_account(number: str | None) -> str:
    value = clean_account(number or "")
    return ("•" * max(0, len(value) - 4) + value[-4:]) if value else ""


def mask_phone(phone: str | None) -> str:
    value = phone or ""
    return value[:3] + " " + value[3:5] + " ••• " + value[-4:] if len(value) >= 11 else value


# ---------- registration

from app.limits import MAX_FRAMES, Frame, check_frames_total  # noqa: E402


class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=5, max_length=255)
    nic: str = Field(min_length=9, max_length=20)
    phone: str = Field(min_length=9, max_length=20)
    account_number: str = Field(min_length=6, max_length=32)
    password: str = Field(min_length=1, max_length=200)
    confirm_password: str = Field(min_length=1, max_length=200)
    frames: list[Frame] = Field(default_factory=list, max_length=MAX_FRAMES)
    offsets_ms: list[int] = Field(default_factory=list, max_length=MAX_FRAMES)
    challenge: str = Field(default="", max_length=1000)
    username: str | None = Field(default=None, max_length=40)


@router.get("/api/customer/register/challenge")
def register_challenge(request: Request):
    """The random instructions for the face scan. Signed, short-lived and usable once."""
    from app import face

    ratelimit.take("register_ip", _client_ip(request))
    mode = face.mode()
    if mode == "unavailable":
        raise HTTPException(status_code=503, detail="Face scanning is not available right now. Please try again later.")
    if mode == "legacy":
        return {"mode": "legacy", "steps": [], "instructions": [], "token": ""}
    return {"mode": "dlib", **face.issue_challenge("register")}


@router.post("/api/customer/register")
def register(body: RegisterIn, request: Request):
    ratelimit.take("register_ip", _client_ip(request))
    account = clean_account(body.account_number)
    ratelimit.check("register_fail", account)

    with session_scope() as session:
        user = session.query(User).filter(User.role == "customer", User.account_number == account).one_or_none()
        matches = (
            user is not None
            and clean_name(user.full_name) == clean_name(body.full_name)
            and (user.email or "").lower() == body.email.strip().lower()
            and clean_nic(user.nic or "") == clean_nic(body.nic)
            and clean_phone(user.phone or "") == clean_phone(body.phone)
        )
        already = bool(matches and user.registered)
        if matches:
            user_id, display_name, nic, email = user.id, user.full_name, user.nic or "", user.email
    # The audit log writes in its own session, so it must be called after this one is closed.
    if not matches:
        ratelimit.hit("register_fail", account)
        audit("registration", "register_no_match", account[-4:], "details did not match the bank record")
        raise HTTPException(status_code=422, detail=NO_MATCH)
    if already:
        raise HTTPException(status_code=409, detail="This account is already registered. Sign in instead.")

    if body.password != body.confirm_password:
        raise HTTPException(status_code=422, detail="The two passwords are not the same.")
    problem = password_problem(body.password, name=display_name, nic=nic, email=email)
    if problem:
        raise HTTPException(status_code=422, detail=problem)

    check_frames_total(body.frames)
    # The face scan is decided here, from the camera frames. The browser cannot claim a pass.
    from app import face

    ratelimit.take("liveness", "register:" + user_id)
    mode = face.mode()
    if mode == "unavailable":
        raise HTTPException(status_code=503, detail="Face scanning is not available right now. Please try again later.")
    if mode == "legacy":
        frames: list[bytes] = []
        for item in body.frames:
            try:
                frames.append(base64.b64decode(item, validate=False))
            except Exception:
                frames.append(b"")
        verdict = analyse(frames)
        if verdict["verdict"] != "live":
            raise HTTPException(status_code=422, detail="The face scan did not pass. Face the camera in good light and try again.")
        sealed = encrypt(base64.b64encode(frames[0]).decode(), runtime.settings.encryption_key, "face")
        face_hash = verdict["image_sha256"]
    else:
        try:
            enrolled = face.enroll(body.frames, body.offsets_ms, body.challenge)
        except (face.ChallengeError, face.BadCapture) as exc:
            raise HTTPException(status_code=422, detail=f"The face scan was not accepted. {exc}") from exc
        if not enrolled["ok"]:
            raise HTTPException(status_code=422, detail="The face scan was not accepted. " + " ".join(enrolled["hints"][:3]))
        sealed, face_hash = enrolled["sealed"], enrolled["hash"]

    with session_scope() as session:
        user = session.get(User, user_id)
        if user.registered:
            raise HTTPException(status_code=409, detail="This account is already registered. Sign in instead.")
        username = new_username(session, user_id, body.username, display_name)
        user.username = username
        user.password_hash = hash_password(body.password)
        user.registered = True
        user.registered_at = utcnow()
        user.password_changed_at = utcnow()
        user.must_change_password = False
        user.status = "active"
        user.totp_enrolled = False  # the authenticator is confirmed with a first code before the customer is signed in
        user.face_hash = face_hash
        # The reference images and face templates are kept encrypted. They are never shown back and never sent to a model.
        user.face_ref = sealed
        session.add(
            Device(
                id="dev_" + hashlib.sha256((user_id + str(utcnow())).encode()).hexdigest()[:10],
                user_id=user_id,
                label="Registered phone",
                platform="Browser",
                trusted=True,
                last_seen=utcnow(),
            )
        )
        snapshot = _public_user(user)
        role = user.role
    audit(username, "customer_registered", user_id, "account claimed, face scan stored")

    from app.notify import send_email, send_sms

    first = display_name.split()[0]
    send_email(
        user_id,
        None,
        "Welcome to PayShield.lk",
        f"Hello {first},\n\nYour BankCylone account is now registered on PayShield.lk. "
        f"You sign in with your username ({username}), the password you chose and the 6-digit code from your authenticator app.\n\n"
        "If you did not do this, call BankCylone on the number printed on your card straight away.\n"
        "PayShield.lk will never ask for your password by SMS, email or phone.\n",
    )
    send_sms(user_id, None, "BankCylone: your PayShield.lk account is now registered. If this was not you, call your bank on the number on your card.")
    from app.api import _pending_step

    _ = role
    return {**_pending_step(user_id, False), "user": snapshot, "username": username}


# ---------- account summary

@router.get("/api/customer/account")
def account(user: User = Depends(require_customer)):
    now = utcnow()
    with session_scope() as session:
        row = session.get(User, user.id)
        txs = (
            session.query(Transaction)
            .filter(Transaction.customer_id == row.id, Transaction.kind.in_(MONEY_KINDS))
            .order_by(Transaction.occurred_at.desc())
            .limit(40)
            .all()
        )
        held = sum(int(tx.amount_lkr or 0) for tx in txs if tx.status in {"held", "pending"} and tx.kind == "transfer")
        since = now - timedelta(days=30)
        spent = sum(
            int(tx.amount_lkr or 0)
            for tx in txs
            if tx.kind != "salary" and tx.status in {"completed", "released"} and aware(tx.occurred_at) >= since
        )
        payees = len({tx.payee for tx in txs if tx.payee and tx.kind != "salary"})
        devices = session.query(Device).filter(Device.user_id == row.id, Device.trusted.is_(True)).count()
        balance = int(row.balance_lkr or 0)
        history = [
            {
                "id": tx.id,
                "date": iso(tx.occurred_at),
                "label": tx.payee or KIND_LABEL.get(tx.kind, tx.kind),
                "kind": KIND_LABEL.get(tx.kind, tx.kind),
                "amount_lkr": int(tx.amount_lkr or 0),
                "direction": "credit" if tx.kind == "salary" else "debit",
                "status": STATUS_LABEL.get(tx.status, tx.status),
                "status_code": tx.status,
            }
            for tx in txs
        ]
        return {
            "bank": row.bank,
            "holder": row.full_name,
            "username": row.username,
            "account_number": mask_account(row.account_number),
            "branch": row.branch,
            "district": row.district,
            "phone": mask_phone(row.phone),
            "email": row.email,
            "balance_lkr": balance,
            "held_lkr": held,
            "available_lkr": max(0, balance - held),
            "spent_30d_lkr": spent,
            "payees": payees,
            "transactions": history,
            "security": {
                "registered_at": iso(row.registered_at) if row.registered_at else None,
                "password_changed_at": iso(row.password_changed_at) if row.password_changed_at else None,
                "face_scan": bool(row.face_ref),
                "trusted_devices": devices,
                "must_change_password": bool(row.must_change_password),
                "limited": bool(row.restricted),
            },
            "as_of": iso(now),
        }


# ---------- change password

class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)
    confirm_password: str = Field(min_length=1, max_length=200)


@router.post("/api/customer/password")
def change_password(body: PasswordIn, user: User = Depends(require_customer)):
    ratelimit.check("pw_change", user.id)
    with session_scope() as session:
        row = session.get(User, user.id)
        ok = verify_password(body.old_password, row.password_hash)
        name, nic, email = row.full_name, row.nic or "", row.email
    if not ok:
        ratelimit.hit("pw_change", user.id)
        audit(user.email, "password_change_refused", user.id, "old password was wrong")
        raise HTTPException(status_code=401, detail="Your current password is not correct.")
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=422, detail="The two new passwords are not the same.")
    if body.new_password == body.old_password:
        raise HTTPException(status_code=422, detail="Choose a password you have not used before.")
    problem = password_problem(body.new_password, name=name, nic=nic, email=email)
    if problem:
        raise HTTPException(status_code=422, detail=problem)

    changed_at = utcnow()
    with session_scope() as session:
        row = session.get(User, user.id)
        row.password_hash = hash_password(body.new_password)
        row.password_changed_at = changed_at
        row.must_change_password = False
    ratelimit.clear("pw_change", user.id)
    audit(user.email, "password_changed", user.id, "customer changed the password")

    from app.notify import send_email, send_sms

    when = aware(changed_at).astimezone(COLOMBO)
    first = name.split()[0]
    email_result = send_email(
        user.id,
        None,
        "Your PayShield.lk password was changed",
        f"Hello {first},\n\nThe password for your BankCylone account on PayShield.lk was changed on "
        f"{when.strftime('%d %b %Y')} at {when.strftime('%H:%M')} (Sri Lanka time).\n\n"
        "If you made this change, you do not need to do anything.\n"
        "If you did not, call BankCylone on the number printed on your card straight away.\n\n"
        "PayShield.lk will never ask for your password by SMS, email or phone.\n",
    )
    sms_result = send_sms(
        user.id,
        None,
        "BankCylone: your PayShield.lk password was changed. If this was not you, call your bank on the number on your card.",
    )
    return {"ok": True, "email": email_result.get("status"), "sms": sms_result.get("status"), "changed_at": iso(changed_at)}
