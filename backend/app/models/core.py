"""Officer accounts, sessions, tokens, audit trail, jobs, notifications, settings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import OFFICER_ROLE, created_col, pk, updated_col, utcnow


class Officer(Base):
    __tablename__ = "officers"
    __table_args__ = (
        CheckConstraint(f"role = '{OFFICER_ROLE}'", name="ck_officers_single_role"),
    )

    id: Mapped[str] = pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    designation: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=OFFICER_ROLE)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret_enc: Mapped[str | None] = mapped_column(Text)
    mfa_pending_secret_enc: Mapped[str | None] = mapped_column(Text)
    mfa_last_counter: Mapped[int | None] = mapped_column(Integer)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = created_col()
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime)


class OfficerSession(Base):
    __tablename__ = "officer_sessions"

    id: Mapped[str] = pk()
    officer_id: Mapped[str] = mapped_column(ForeignKey("officers.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = created_col()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class AuthToken(Base):
    """Single-use invitation / password-reset tokens (only the hash is stored)."""

    __tablename__ = "auth_tokens"

    id: Mapped[str] = pk()
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)  # invite | password_reset
    officer_id: Mapped[str] = mapped_column(ForeignKey("officers.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_email_time", "email", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_col()


class AuditEvent(Base):
    """Append-only, hash-chained audit trail. UPDATE/DELETE are blocked by DB triggers."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_time", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    officer_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"), index=True)
    officer_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    institution_id: Mapped[str | None] = mapped_column(String(36), index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    before: Mapped[Any | None] = mapped_column(JSON)
    after: Mapped[Any | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[Any | None] = mapped_column(JSON)
    external_source: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str | None] = mapped_column(String(60))
    model_info: Mapped[Any | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class AuditChainHead(Base):
    """Single row serialising audit-chain appends (row lock on PostgreSQL)."""

    __tablename__ = "audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_event_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after", "priority"),
        Index("ix_jobs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = pk()
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    progress_message: Mapped[str | None] = mapped_column(String(300))
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    institution_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = updated_col()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_officer_read", "officer_id", "read_at"),)

    id: Mapped[str] = pk()
    officer_id: Mapped[str] = mapped_column(ForeignKey("officers.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = created_col()
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    updated_at: Mapped[datetime] = updated_col()


class StoredObject(Base):
    """Encrypted file bytes, used when storage_backend = "database" (hosts without a persistent disk)."""

    __tablename__ = "stored_objects"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_col()
