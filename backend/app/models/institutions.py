"""Institution registry, configurable fields, programs and historical records."""

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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import created_col, pk, updated_col


class Institution(Base):
    __tablename__ = "institutions"
    __table_args__ = (
        Index("ix_institutions_state", "state"),
        Index("ix_institutions_status", "verification_status"),
    )

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(400), nullable=False, index=True)
    aicte_permanent_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    category: Mapped[str | None] = mapped_column(String(120))
    institution_type: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(60))
    state: Mapped[str | None] = mapped_column(String(80))
    district: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(Text)
    pincode: Mapped[str | None] = mapped_column(String(10))
    website: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    management_body: Mapped[str | None] = mapped_column(String(300))
    establishment_year: Mapped[int | None] = mapped_column(Integer)
    affiliating_body: Mapped[str | None] = mapped_column(String(300))
    awarding_body: Mapped[str | None] = mapped_column(String(300))
    academic_year: Mapped[str | None] = mapped_column(String(9))
    # Approval status may only be recorded with supporting evidence (see API layer).
    approval_status: Mapped[str | None] = mapped_column(String(120))
    approval_status_evidence: Mapped[Any | None] = mapped_column(JSON)
    verification_status: Mapped[str] = mapped_column(
        String(60), default="submission_incomplete", nullable=False
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    extra_fields: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = created_col()
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    updated_at: Mapped[datetime] = updated_col()
    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))


class InstitutionVersion(Base):
    """Immutable snapshot of an institution record before each change."""

    __tablename__ = "institution_versions"
    __table_args__ = (UniqueConstraint("institution_id", "version"),)

    id: Mapped[str] = pk()
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    changed_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    changed_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    changed_at: Mapped[datetime] = created_col()


class InstitutionFieldDefinition(Base):
    """Category-specific configurable fields stored in Institution.extra_fields."""

    __tablename__ = "institution_field_definitions"
    __table_args__ = (UniqueConstraint("category", "key"),)

    id: Mapped[str] = pk()
    category: Mapped[str] = mapped_column(String(120), nullable=False)  # "*" = all categories
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), default="text", nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    help_text: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()


class Program(Base):
    """A program/course offered in a given academic year (one row per year for comparison)."""

    __tablename__ = "programs"
    __table_args__ = (Index("ix_programs_inst_year", "institution_id", "academic_year"),)

    id: Mapped[str] = pk()
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(9), nullable=False)
    level: Mapped[str | None] = mapped_column(String(80))
    program: Mapped[str | None] = mapped_column(String(200))
    course: Mapped[str] = mapped_column(String(300), nullable=False)
    shift: Mapped[str | None] = mapped_column(String(40))
    affiliating_body: Mapped[str | None] = mapped_column(String(300))
    approved_intake: Mapped[int | None] = mapped_column(Integer)
    nri_status: Mapped[str | None] = mapped_column(String(40))
    foreign_status: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), default="officer_entry", nullable=False)
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    source_reference: Mapped[str | None] = mapped_column(Text)
    compliance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()
    updated_at: Mapped[datetime] = updated_col()


class InstitutionRecord(Base):
    """Historical, faculty, infrastructure and compliance records with provenance.

    Every record must reference a source (an uploaded document, an external
    source reference, or an explicit officer entry) — enforced at the API layer.
    """

    __tablename__ = "institution_records"
    __table_args__ = (Index("ix_inst_records_kind", "institution_id", "kind"),)

    id: Mapped[str] = pk()
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    academic_year: Mapped[str | None] = mapped_column(String(9))
    record_date: Mapped[datetime | None] = mapped_column(DateTime)
    summary: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    source_reference: Mapped[str | None] = mapped_column(Text)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("institution_records.id"))
    recorded_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    recorded_at: Mapped[datetime] = created_col()
