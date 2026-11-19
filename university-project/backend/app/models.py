from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base
from app.util import utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(40), index=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    bank: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    province: Mapped[str | None] = mapped_column(String(80), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(String(400), nullable=True)
    totp_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_failures: Mapped[int] = mapped_column(Integer, default=0)
    totp_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    restricted_actions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    restricted_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    security: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Customer record held by the bank, and what the customer set up when they registered in the app.
    nic: Mapped[str | None] = mapped_column(String(20), nullable=True)
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
    phone: Mapped[str] = mapped_column(String(20), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    origin: Mapped[str] = mapped_column(String(20), default="seed")
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)


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
    telecom_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    banking_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
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


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300))
    markdown: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    body: Mapped[str] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmergencyMode(Base):
    """A district an analyst has put into emergency hold. Survives a restart."""

    __tablename__ = "emergency_modes"

    district: Mapped[str] = mapped_column(String(80), primary_key=True)
    reason: Mapped[str] = mapped_column(String(200))
    set_by: Mapped[str] = mapped_column(String(255))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
