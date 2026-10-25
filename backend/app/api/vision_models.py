"""Inspection model configuration (no model identifiers or classes are assumed)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from .. import serializers as ser
from ..db import get_db
from ..deps import Auth, require_officer
from ..integrations import http
from ..integrations.roboflow import TASK_TYPES, endpoint, test_model
from ..models import Integration, IntegrationTestLog, ModelConfig, utcnow
from .common import get_or_404

router = APIRouter(prefix="/api/models", tags=["models"])


def _out(m: ModelConfig, integ: Integration | None = None) -> dict:
    return {"id": m.id, "name": m.name, "description": m.description, "integration_id": m.integration_id,
            "project_id": m.project_id, "model_version": m.model_version, "task_type": m.task_type,
            "endpoint_base": m.endpoint_base, "endpoint": endpoint(integ, m) if integ else None, "classes": m.classes,
            "class_rules": m.class_rules, "confidence_threshold": m.confidence_threshold, "overlap_threshold": m.overlap_threshold,
            "facility_categories": m.facility_categories, "input_formats": m.input_formats, "evaluation": m.evaluation,
            "dataset_notes": m.dataset_notes, "enabled": m.enabled, "status": m.status, "last_test_at": ser.iso(m.last_test_at),
            "last_error": m.last_error, "config_revision": m.config_revision, "created_at": ser.iso(m.created_at),
            "updated_at": ser.iso(m.updated_at)}


class ModelIn(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    project_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_\-./]+$")
    model_version: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_\-.]+$")
    task_type: str
    endpoint_base: str | None = Field(default=None, max_length=500)
    classes: list[str] = Field(default_factory=list)
    class_rules: dict = Field(default_factory=dict)
    confidence_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    overlap_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    facility_categories: list[str] = Field(default_factory=list)
    input_formats: list[str] = Field(default_factory=lambda: ["jpeg", "png"])
    evaluation: dict = Field(default_factory=dict)
    dataset_notes: str | None = Field(default=None, max_length=8000)
    enabled: bool = False


def _validate(body: ModelIn) -> None:
    if body.task_type not in TASK_TYPES:
        raise HTTPException(422, f"Task type must be one of: {', '.join(sorted(TASK_TYPES))}.")
    if body.endpoint_base:
        try:
            http.validate_destination(body.endpoint_base)
        except (http.OutboundBlocked, http.OutboundError) as exc:
            raise HTTPException(422, f"Endpoint not permitted: {exc}") from exc
    for cls, rule in body.class_rules.items():
        if cls not in body.classes:
            raise HTTPException(422, f"Class rule '{cls}' is not in the model's supported classes.")
        if not isinstance(rule, dict):
            raise HTTPException(422, "Class rules must be objects.")
        sev = rule.get("severity")
        if sev is not None and sev not in {"low", "medium", "high", "critical"}:
            raise HTTPException(422, f"Invalid severity for '{cls}'.")
        if sev is not None and not str(rule.get("severity_basis") or "").strip():
            raise HTTPException(422, f"Severity for '{cls}' needs a basis (the applicable standard or rule).")


def _provider(db: Session) -> Integration:
    integ = db.execute(select(Integration).where(Integration.adapter == "roboflow")).scalar_one_or_none()
    if integ is None:
        raise HTTPException(500, "Inference provider integration is missing.")
    return integ


@router.get("")
def list_models(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    integ = _provider(db)
    rows = db.execute(select(ModelConfig).order_by(ModelConfig.name)).scalars().all()
    return {"items": [_out(m, integ) for m in rows], "provider": ser.integration(integ), "task_types": sorted(TASK_TYPES)}


@router.post("", status_code=201)
def create_model(body: ModelIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    _validate(body)
    integ = _provider(db)
    m = ModelConfig(integration_id=integ.id, created_by_id=auth.officer.id, status="untested", **body.model_dump())
    db.add(m)
    db.flush()
    audit.record(db, "model.created", officer=auth.officer, entity_type="model_config", entity_id=m.id, after=_out(m, integ),
                 model_info={"project_id": m.project_id, "model_version": m.model_version}, ctx=auth.ctx)
    db.commit()
    return _out(m, integ)


@router.patch("/{model_id}")
def update_model(model_id: str, body: ModelIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    m = get_or_404(db, ModelConfig, model_id, "Model")
    _validate(body)
    integ = _provider(db)
    before = _out(m, integ)
    material = any(getattr(m, k) != v for k, v in body.model_dump().items() if k not in {"name", "description", "dataset_notes", "enabled", "evaluation"})
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    if material:
        m.config_revision += 1
        m.status = "untested"
    audit.record(db, "model.updated", officer=auth.officer, entity_type="model_config", entity_id=m.id, before=before,
                 after=_out(m, integ), model_info={"project_id": m.project_id, "model_version": m.model_version,
                                                   "config_revision": m.config_revision}, ctx=auth.ctx)
    db.commit()
    return _out(m, integ)


@router.post("/{model_id}/test")
def test(model_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    m = get_or_404(db, ModelConfig, model_id, "Model")
    integ = _provider(db)
    started = time.monotonic()
    res = test_model(integ, m)
    now = utcnow()
    m.last_test_at = now
    if res.ok:
        m.status, m.last_error = "operational", None
        integ.connection_status, integ.last_success_at = "operational", now
    else:
        m.status = "auth_failed" if res.status == "auth_failed" else ("not_configured" if res.status == "not_configured" else "failed")
        m.last_error = res.message
        if res.status == "auth_failed":
            integ.connection_status = "auth_failed"
        integ.last_failure_at, integ.last_failure_reason = now, res.message[:500]
    integ.last_test_at = now
    db.add(IntegrationTestLog(integration_id=integ.id, model_config_id=m.id, ok=res.ok, status=m.status, http_status=res.http_status,
                              message=res.message, duration_ms=int((time.monotonic() - started) * 1000), tested_by_id=auth.officer.id))
    audit.record(db, "model.tested", officer=auth.officer, entity_type="model_config", entity_id=m.id, outcome=m.status,
                 summary=res.message[:500], model_info={"project_id": m.project_id, "model_version": m.model_version}, ctx=auth.ctx)
    db.commit()
    return {"ok": res.ok, "message": res.message, "http_status": res.http_status, "model": _out(m, integ)}
