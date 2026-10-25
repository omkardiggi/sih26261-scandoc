"""Inspection cases, media evidence, video frames, model configs, inference and annotations."""

from __future__ import annotations

from datetime import datetime

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


class InspectionCase(Base):
    __tablename__ = "inspection_cases"
    __table_args__ = (Index("ix_inspections_inst", "institution_id"),)

    id: Mapped[str] = pk()
    reference: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("verification_cases.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    inspection_type: Mapped[str] = mapped_column(String(60), default="evidence_review", nullable=False)
    inspection_date: Mapped[datetime | None] = mapped_column(DateTime)
    location: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="open", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()
    updated_at: Mapped[datetime] = updated_col()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (Index("ix_media_inspection", "inspection_id"),)

    id: Mapped[str] = pk()
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspection_cases.id"), nullable=False)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # image | video
    facility_category: Mapped[str | None] = mapped_column(String(80))
    caption: Mapped[str | None] = mapped_column(String(500))
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    detected_type: Mapped[str] = mapped_column(String(20), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    fps: Mapped[float | None] = mapped_column(Float)
    codec: Mapped[str | None] = mapped_column(String(40))
    captured_at: Mapped[str | None] = mapped_column(String(40))
    exif: Mapped[dict | None] = mapped_column(JSON)
    duplicate_of_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"))
    uploaded_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    uploaded_at: Mapped[datetime] = created_col()
    processing_status: Mapped[str] = mapped_column(String(20), default="uploaded", nullable=False)
    processing_error: Mapped[str | None] = mapped_column(Text)
    sample_interval_s: Mapped[float | None] = mapped_column(Float)
    frames_extracted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frames_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frames_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frames_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    preview_key: Mapped[str | None] = mapped_column(String(200))


class VideoFrame(Base):
    __tablename__ = "video_frames"
    __table_args__ = (UniqueConstraint("media_id", "seq"),)

    id: Mapped[str] = pk()
    media_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_s: Mapped[float] = mapped_column(Float, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="extracted", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    integration_id: Mapped[str] = mapped_column(ForeignKey("integrations.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    task_type: Mapped[str] = mapped_column(String(40), nullable=False)
    endpoint_base: Mapped[str | None] = mapped_column(String(500))
    classes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # {"crack": {"finding": true, "severity": "medium", "criterion_code": "BLD-01", "min_confidence": 0.6}}
    class_rules: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.4, nullable=False)
    overlap_threshold: Mapped[float] = mapped_column(Float, default=0.3, nullable=False)
    facility_categories: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    input_formats: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    evaluation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    dataset_notes: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="untested", nullable=False)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    config_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()
    updated_at: Mapped[datetime] = updated_col()


class InferenceRun(Base):
    __tablename__ = "inference_runs"
    __table_args__ = (Index("ix_inference_media", "media_id"),)

    id: Mapped[str] = pk()
    model_config_id: Mapped[str] = mapped_column(ForeignKey("model_configs.id"), nullable=False)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), nullable=False)
    frame_id: Mapped[str | None] = mapped_column(ForeignKey("video_frames.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    model_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    prediction_count: Mapped[int | None] = mapped_column(Integer)
    image_width: Mapped[int | None] = mapped_column(Integer)
    image_height: Mapped[int | None] = mapped_column(Integer)
    raw_response_enc: Mapped[str | None] = mapped_column(Text)
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    created_at: Mapped[datetime] = created_col()


class Annotation(Base):
    __tablename__ = "annotations"
    __table_args__ = (
        Index("ix_annotations_media", "media_id"),
        Index("ix_annotations_frame", "frame_id"),
    )

    id: Mapped[str] = pk()
    media_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), nullable=False)
    frame_id: Mapped[str | None] = mapped_column(ForeignKey("video_frames.id"))
    inference_run_id: Mapped[str | None] = mapped_column(ForeignKey("inference_runs.id"))
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # model | officer
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    geometry_type: Mapped[str] = mapped_column(String(20), nullable=False)  # bbox | polygon | rle
    # bbox: {"x0","y0","x1","y1"} in source-image pixels; polygon: {"points": [[x,y],...], "bbox": {...}}
    geometry: Mapped[dict] = mapped_column(JSON, nullable=False)
    timestamp_s: Mapped[float | None] = mapped_column(Float)
    track_id: Mapped[int | None] = mapped_column(Integer)
    original_annotation_id: Mapped[str | None] = mapped_column(ForeignKey("annotations.id"))
    review_status: Mapped[str] = mapped_column(String(20), default="unreviewed", nullable=False)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()


class InspectionCriterion(Base):
    __tablename__ = "inspection_criteria"

    id: Mapped[str] = pk()
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    facility_category: Mapped[str | None] = mapped_column(String(80))
    applicable_standard: Mapped[str | None] = mapped_column(Text)
    official_source: Mapped[str | None] = mapped_column(Text)
    guidance: Mapped[str | None] = mapped_column(Text)
    requires_physical_verification: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    linked_classes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    created_at: Mapped[datetime] = created_col()


class InspectionAssessment(Base):
    __tablename__ = "inspection_assessments"
    __table_args__ = (UniqueConstraint("inspection_id", "criterion_id"),)

    id: Mapped[str] = pk()
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspection_cases.id"), nullable=False)
    criterion_id: Mapped[str] = mapped_column(ForeignKey("inspection_criteria.id"), nullable=False)
    observed_evidence: Mapped[str | None] = mapped_column(Text)
    detected_issue: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="not_assessed", nullable=False)
    uncertainty: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    review_status: Mapped[str] = mapped_column(String(40), default="not_started", nullable=False)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    reviewed_by_id: Mapped[str | None] = mapped_column(ForeignKey("officers.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = updated_col()
