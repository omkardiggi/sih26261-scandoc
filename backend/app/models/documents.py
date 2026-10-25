"""Verification cases, documents, versions, pages, extraction and requirements."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
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


class VerificationCase(Base):
    __tablename__ = "verification_cases"
    __table_args__ = (Index("ix_cases_inst_status", "institution_id", "status"),)

    id: Mapped[str] = pk()
    reference: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(9), nullable=False)
    application_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(60), default="submission_incomplete", nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    assigned_to_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    opened_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    opened_at: Mapped[datetime] = created_col()
    updated_at: Mapped[datetime] = updated_col()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime)
    evaluation: Mapped[dict | None] = mapped_column(JSON)


class DocumentCategory(Base):
    __tablename__ = "document_categories"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # [{"pattern": "extension of approval", "weight": 3}] — explainable keyword classifier
    keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    expected_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    signature_expected: Mapped[bool | None] = mapped_column(Boolean)
    validity_applicable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(80))
    icon: Mapped[str | None] = mapped_column(String(40))


class DocumentRequirement(Base):
    """Configurable document requirements with mandatory official source references."""

    __tablename__ = "document_requirements"

    id: Mapped[str] = pk()
    category_key: Mapped[str] = mapped_column(ForeignKey("document_categories.key"), nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    application_type: Mapped[str] = mapped_column(String(80), nullable=False)
    institution_category: Mapped[str | None] = mapped_column(String(120))  # None = all
    academic_years: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [] = any
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    condition: Mapped[str | None] = mapped_column(Text)
    validity_rule: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_reference: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_page: Mapped[str | None] = mapped_column(String(40))
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    confirmed_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()
    import_key: Mapped[str | None] = mapped_column(String(120), unique=True)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_inst", "institution_id"),
        Index("ix_documents_case", "case_id"),
        Index("ix_documents_status", "status"),
    )

    id: Mapped[str] = pk()
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("verification_cases.id"))
    category_key: Mapped[str | None] = mapped_column(ForeignKey("document_categories.key"))
    category_source: Mapped[str] = mapped_column(String(20), default="unclassified", nullable=False)
    classification: Mapped[dict | None] = mapped_column(JSON)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    academic_year: Mapped[str | None] = mapped_column(String(9))
    issuing_authority: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(40), default="uploaded", nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text)
    check_summary: Mapped[dict | None] = mapped_column(JSON)
    review_status: Mapped[str] = mapped_column(String(20), default="unreviewed", nullable=False)
    reviewed_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    current_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", use_alter=True, name="fk_documents_current_version")
    )
    is_historical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()
    updated_at: Mapped[datetime] = updated_col()


class DocumentVersion(Base):
    """An immutable uploaded file. The original bytes are never modified."""

    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_no"),)

    id: Mapped[str] = pk()
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    declared_mime: Mapped[str | None] = mapped_column(String(120))
    detected_type: Mapped[str] = mapped_column(String(20), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False)
    # Decrypted processing copy for password-protected PDFs (original stays untouched in storage_key).
    processing_key: Mapped[str | None] = mapped_column(String(200))
    page_count: Mapped[int | None] = mapped_column(Integer)
    pdf_encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active_content: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    malware_status: Mapped[str] = mapped_column(String(20), default="not_scanned", nullable=False)
    malware_detail: Mapped[str | None] = mapped_column(String(300))
    duplicate_of_version_id: Mapped[str | None] = mapped_column(ForeignKey("document_versions.id"))
    upload_note: Mapped[str | None] = mapped_column(Text)
    uploaded_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    uploaded_at: Mapped[datetime] = created_col()
    processing_status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    processing_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    text_layer: Mapped[str | None] = mapped_column(String(20))
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mean_ocr_confidence: Mapped[float | None] = mapped_column(Float)
    metadata_info: Mapped[dict | None] = mapped_column(JSON)


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("version_id", "page_no"),)

    id: Mapped[str] = pk()
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    width_pt: Mapped[float] = mapped_column(Float, nullable=False)
    height_pt: Mapped[float] = mapped_column(Float, nullable=False)
    render_key: Mapped[str | None] = mapped_column(String(200))
    render_width: Mapped[int | None] = mapped_column(Integer)
    render_height: Mapped[int | None] = mapped_column(Integer)
    text_source: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    is_blank: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unreadable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TextBlock(Base):
    """A positioned line of text (bbox normalised to 0..1 of page width/height)."""

    __tablename__ = "text_blocks"
    __table_args__ = (Index("ix_text_blocks_version_page", "version_id", "page_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bbox: Mapped[list] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # embedded | ocr


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (Index("ix_fields_version_key", "version_id", "field_key"),)

    id: Mapped[str] = pk()
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    field_key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    original_text: Mapped[str | None] = mapped_column(Text)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_basis: Mapped[str | None] = mapped_column(String(60))
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(20), default="unchecked", nullable=False)
    validation_message: Mapped[str | None] = mapped_column(Text)
    group_index: Mapped[int | None] = mapped_column(Integer)  # e.g. program row number
    created_at: Mapped[datetime] = created_col()


class FieldCorrection(Base):
    """Officer correction of an extracted value; the original extraction is preserved."""

    __tablename__ = "field_corrections"

    id: Mapped[str] = pk()
    field_id: Mapped[str] = mapped_column(ForeignKey("extracted_fields.id"), nullable=False, index=True)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_by_id: Mapped[str] = mapped_column(ForeignKey("officers.id"), nullable=False)
    corrected_at: Mapped[datetime] = created_col()


class ExtractedTable(Base):
    __tablename__ = "extracted_tables"

    id: Mapped[str] = pk()
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), nullable=False, index=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox: Mapped[list | None] = mapped_column(JSON)
    table_type: Mapped[str] = mapped_column(String(30), default="generic", nullable=False)
    header: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    rows: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="embedded", nullable=False)


class CheckRun(Base):
    """A run of validation / comparison / integrity checks (results are never overwritten)."""

    __tablename__ = "check_runs"
    __table_args__ = (Index("ix_check_runs_target", "kind", "version_id", "case_id"),)

    id: Mapped[str] = pk()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("verification_cases.id"))
    institution_id: Mapped[str | None] = mapped_column(ForeignKey("institutions.id"))
    version_id: Mapped[str | None] = mapped_column(ForeignKey("document_versions.id"))
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    triggered_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    started_at: Mapped[datetime] = created_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary: Mapped[dict | None] = mapped_column(JSON)


class ValidationResult(Base):
    __tablename__ = "validation_results"
    __table_args__ = (Index("ix_validation_run", "run_id"),)

    id: Mapped[str] = pk()
    run_id: Mapped[str] = mapped_column(ForeignKey("check_runs.id"), nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("verification_cases.id"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    version_id: Mapped[str | None] = mapped_column(ForeignKey("document_versions.id"))
    rule_key: Mapped[str] = mapped_column(String(80), nullable=False)
    rule_label: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = created_col()


class IntegrityCheck(Base):
    __tablename__ = "integrity_checks"
    __table_args__ = (Index("ix_integrity_version", "version_id"),)

    id: Mapped[str] = pk()
    run_id: Mapped[str] = mapped_column(ForeignKey("check_runs.id"), nullable=False)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), nullable=False)
    check_type: Mapped[str] = mapped_column(String(40), nullable=False)
    dimension: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = created_col()


class ComparisonResult(Base):
    __tablename__ = "comparison_results"
    __table_args__ = (Index("ix_comparison_run", "run_id"),)

    id: Mapped[str] = pk()
    run_id: Mapped[str] = mapped_column(ForeignKey("check_runs.id"), nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("verification_cases.id"))
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    field_key: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    values: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_col()


class EvidenceArtifact(Base):
    """Derived evidence images (e.g. error-level-analysis heatmaps), stored separately from originals."""

    __tablename__ = "evidence_artifacts"

    id: Mapped[str] = pk()
    version_id: Mapped[str | None] = mapped_column(ForeignKey("document_versions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False)
    mime: Mapped[str] = mapped_column(String(60), default="image/png", nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[Any | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = created_col()
