"""Integrations, official verification attempts, findings, corrective actions, decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_col, pk, updated_col


class Integration(Base):
    __tablename__ = "integrations"

    id: Mapped[str] = pk()
    key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    adapter: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(200))
    source_owner: Mapped[str | None] = mapped_column(String(200))
    base_url: Mapped[str | None] = mapped_column(String(500))
    auth_method: Mapped[str] = mapped_column(String(30), default="none", nullable=False)
    auth_header_name: Mapped[str | None] = mapped_column(String(100))
    secret_env_var: Mapped[str | None] = mapped_column(String(100))
    secret_enc: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    claim_types: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    documentation_url: Mapped[str | None] = mapped_column(String(500))
    required_permissions: Mapped[str | None] = mapped_column(Text)
    coverage_notes: Mapped[str | None] = mapped_column(Text)
    rate_limit_notes: Mapped[str | None] = mapped_column(Text)
    privacy_notes: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    connection_status: Mapped[str] = mapped_column(String(30), default="untested", nullable=False)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_failure_reason: Mapped[str | None] = mapped_column(Text)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = created_col()
    updated_at: Mapped[datetime] = updated_col()
    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))


class IntegrationTestLog(Base):
    __tablename__ = "integration_test_logs"

    id: Mapped[str] = pk()
    integration_id: Mapped[str] = mapped_column(ForeignKey("integrations.id"), nullable=False, index=True)
    model_config_id: Mapped[str | None] = mapped_column(ForeignKey("model_configs.id"))
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    tested_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    tested_at: Mapped[datetime] = created_col()


class ExternalVerificationAttempt(Base):
    __tablename__ = "external_verification_attempts"
    __table_args__ = (
        Index("ix_ext_attempt_case", "case_id"),
        Index("ix_ext_attempt_document", "document_id"),
    )

    id: Mapped[str] = pk()
    integration_id: Mapped[str | None] = mapped_column(ForeignKey("integrations.id"))
    method: Mapped[str] = mapped_column(String(30), nullable=False)  # automated | officer_recorded
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("verification_cases.id"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    field_id: Mapped[str | None] = mapped_column(ForeignKey("extracted_fields.id"))
    claim_type: Mapped[str] = mapped_column(String(60), nullable=False)
    claim: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    request_summary: Mapped[dict | None] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    source_name: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(500))
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    response_enc: Mapped[str | None] = mapped_column(Text)
    matched_values: Mapped[dict | None] = mapped_column(JSON)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    attempted_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    attempted_at: Mapped[datetime] = created_col()
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        Index("ix_findings_inst_status", "institution_id", "status"),
        Index("ix_findings_case", "case_id"),
        Index("ix_findings_inspection", "inspection_id"),
    )

    id: Mapped[str] = pk()
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("verification_cases.id"))
    inspection_id: Mapped[str | None] = mapped_column(ForeignKey("inspection_cases.id"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    media_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"))
    criterion_id: Mapped[str | None] = mapped_column(ForeignKey("inspection_criteria.id"))
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="not_assessed", nullable=False)
    severity_basis: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    origin_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    requires_physical_verification: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()
    updated_at: Mapped[datetime] = updated_col()
    resolved_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution_note: Mapped[str | None] = mapped_column(Text)


class CorrectiveAction(Base):
    __tablename__ = "corrective_actions"
    __table_args__ = (Index("ix_corrective_inst_status", "institution_id", "status"),)

    id: Mapped[str] = pk()
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    finding_id: Mapped[str | None] = mapped_column(ForeignKey("findings.id"))
    case_id: Mapped[str | None] = mapped_column(ForeignKey("verification_cases.id"))
    inspection_id: Mapped[str | None] = mapped_column(ForeignKey("inspection_cases.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    evidence_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()
    updated_at: Mapped[datetime] = updated_col()
    closed_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)


class Decision(Base):
    """Officer decisions. Append-only: corrections create a new record that supersedes."""

    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_case", "case_id"),)

    id: Mapped[str] = pk()
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("verification_cases.id"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    inspection_id: Mapped[str | None] = mapped_column(ForeignKey("inspection_cases.id"))
    decision_type: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome: Mapped[str] = mapped_column(String(60), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    evaluation_snapshot: Mapped[Any | None] = mapped_column(JSON)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("decisions.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    decided_by_id: Mapped[str] = mapped_column(ForeignKey("officers.id"), nullable=False)
    decided_at: Mapped[datetime] = created_col()
