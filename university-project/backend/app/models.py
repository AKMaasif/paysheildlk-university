from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

from app.db import Base
from app.util import utcnow


def _encryption_secret() -> str:
    from app.runtime import runtime

    if runtime.settings is not None:
        return runtime.settings.encryption_key
    from app.config import (
        Settings,  # used outside the running service, for example by a migration script
    )

    return Settings.from_env().encryption_key


class Sensitive(TypeDecorator):
    """A personal field stored encrypted. Code reads and writes the plain value; the database holds siv1$ ciphertext.
    The same value always gives the same ciphertext, so `User.email == "x"` and similar lookups keep working: the value
    being compared is encrypted the same way before it reaches the query. Rows written before this existed are plain text
    and still read, and are encrypted by the 0003 migration and by a check at every start."""

    impl = String
    cache_ok = True

    def __init__(self, purpose: str, length: int = 255):
        super().__init__(length)
        self.purpose = purpose

    def _secret(self) -> str:
        return _encryption_secret()

    def process_bind_param(self, value, dialect):
        if value is None or str(value).startswith("siv1$"):
            return value
        from app.crypto import seal_field

        return seal_field(str(value), self._secret(), self.purpose)

    def process_result_value(self, value, dialect):
        if value is None or not str(value).startswith("siv1$"):
            return value
        from app.crypto import open_field

        return open_field(value, self._secret(), self.purpose)


class SecretText(TypeDecorator):
    """Free text stored encrypted (random nonce, so equal texts differ). It is read back whole and never searched by
    content. Rows written before this existed are plain text and still read; the start-up check and the 0005 migration
    encrypt them."""

    impl = Text
    cache_ok = True

    def __init__(self, purpose: str):
        super().__init__()
        self.purpose = purpose

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        from app.crypto import encrypt

        return encrypt(str(value), _encryption_secret(), self.purpose)

    def process_result_value(self, value, dialect):
        if value is None or not str(value).startswith("gcm2$"):
            return value
        from app.crypto import decrypt

        try:
            return decrypt(value, _encryption_secret(), self.purpose)
        except Exception:
            return value  # text that only looks like ciphertext


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(Sensitive("email", 700), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(40), index=True)
    phone: Mapped[str | None] = mapped_column(Sensitive("phone", 160), nullable=True, index=True)
    bank: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    province: Mapped[str | None] = mapped_column(String(80), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_number: Mapped[str | None] = mapped_column(Sensitive("account", 160), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(String(400), nullable=True)
    totp_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_failures: Mapped[int] = mapped_column(Integer, default=0)
    totp_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Sessions issued before this moment are no longer valid (set by "sign out everywhere").
    tokens_valid_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    restricted_actions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    restricted_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    security: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Customer record held by the bank, and what the customer set up when they registered in the app.
    nic: Mapped[str | None] = mapped_column(Sensitive("nic", 160), nullable=True)
    balance_lkr: Mapped[int] = mapped_column(Integer, default=0)
    registered: Mapped[bool] = mapped_column(Boolean, default=False)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    face_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    face_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(80), default="browser")
    fcm_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    trusted: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SimEvent(Base):
    __tablename__ = "sim_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    phone: Mapped[str] = mapped_column(Sensitive("phone", 160), index=True)  # same field name as users.phone, so equal numbers match
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    origin: Mapped[str] = mapped_column(String(20), default="seed")
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # when this system took it in


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    amount_lkr: Mapped[int] = mapped_column(Integer, default=0)
    payee: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payee_bank: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payee_account: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_payee: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), index=True, default="completed")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    origin: Mapped[str] = mapped_column(String(20), default="seed")
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    transaction_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_factors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    policy_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    policy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(40), nullable=True)  # the signals the decision was made on
    telecom_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    banking_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    face_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    face_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verification_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verification_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    sim_freeze_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pending_prompt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider_counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    liveness_strikes: Mapped[int] = mapped_column(Integer, default=0)
    knowledge_round: Mapped[int] = mapped_column(Integer, default=0)
    origin: Mapped[str] = mapped_column(String(20), default="demo")
    scenario: Mapped[str | None] = mapped_column(String(40), nullable=True)
    amount_lkr: Mapped[int] = mapped_column(Integer, default=0)
    payee: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bank: Mapped[str | None] = mapped_column(String(80), nullable=True)
    province: Mapped[str | None] = mapped_column(String(80), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)


class CaseEvent(Base):
    __tablename__ = "case_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    agent: Mapped[str] = mapped_column(String(40), index=True)
    state: Mapped[str] = mapped_column(String(40))
    tool_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tool_args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    handoff_to: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fell_back: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Tool trace. A tool call is two events with the same call_id: phase "started", then "finished" or "failed".
    phase: Mapped[str | None] = mapped_column(String(16), nullable=True)
    call_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # the structured result, with secrets and identifiers redacted
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # planner, why, attempt, source, snapshot, run id, transaction id


class DecisionSnapshot(Base):
    """The exact signals a decision was made on, so it can be reproduced and never mistaken for later information."""

    __tablename__ = "decision_snapshots"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # score, pre_release
    version: Mapped[int] = mapped_column(Integer, default=1)  # 1, 2, 3 ... within the case
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signals: Mapped[dict] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)  # per signal: source, observed, received, fetched, age
    conflicts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    digest: Mapped[str] = mapped_column(String(64))  # sha256 of the signals only: same inputs, same digest
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300))
    markdown: Mapped[str] = mapped_column(SecretText("report"))
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RevokedToken(Base):
    """A session token that was signed out. Tokens are stateless, so signing out records the one that was in use and every
    check refuses it until it would have expired anyway; the row can then be deleted."""

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    actor_email: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(255))
    organisation: Mapped[str] = mapped_column(String(200))
    role_asked: Mapped[str] = mapped_column(String(40))
    scope_asked: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Invitation(Base):
    __tablename__ = "invitations"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40))
    bank: Mapped[str | None] = mapped_column(String(80), nullable=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class SecurityAnswer(Base):
    __tablename__ = "security_answers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(40))
    prompt: Mapped[str] = mapped_column(String(300))
    answer_hash: Mapped[str] = mapped_column(String(200))


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(SecretText("notification"))
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # what is actually known about getting it to the person


class EmergencyMode(Base):
    """A district an analyst has put into emergency hold. Survives a restart."""

    __tablename__ = "emergency_modes"

    district: Mapped[str] = mapped_column(String(80), primary_key=True)
    reason: Mapped[str] = mapped_column(String(200))
    set_by: Mapped[str] = mapped_column(String(255))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PolicySetting(Base):
    """A tunable number in the rule table. Only an analyst's approval changes one."""

    __tablename__ = "policy_settings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PolicyProposal(Base):
    """A change the Analytics agent suggests. It changes nothing until an analyst approves it."""

    __tablename__ = "policy_proposals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(40))
    current: Mapped[int] = mapped_column(Integer)
    proposed: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
