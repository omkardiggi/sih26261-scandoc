"""Document requirements registry and document categories (configuration with official source references)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from .. import serializers as ser
from ..db import get_db
from ..deps import Auth, require_officer
from ..documents.normalize import normalize_academic_year
from ..models import DocumentCategory, DocumentRequirement, utcnow
from ..services.bootstrap import APPLICATION_TYPES
from .common import get_or_404

router = APIRouter(prefix="/api", tags=["requirements"])


def _cat(c: DocumentCategory) -> dict:
    return {"key": c.key, "label": c.label, "description": c.description, "keywords": c.keywords, "expected_fields": c.expected_fields,
            "signature_expected": c.signature_expected, "validity_applicable": c.validity_applicable, "notes": c.notes,
            "builtin": c.builtin, "active": c.active, "sort_order": c.sort_order}


def _req(r: DocumentRequirement, cats: dict[str, str] | None = None) -> dict:
    return {"id": r.id, "category_key": r.category_key, "category_label": (cats or {}).get(r.category_key), "title": r.title,
            "application_type": r.application_type, "application_type_label": APPLICATION_TYPES.get(r.application_type),
            "institution_category": r.institution_category, "academic_years": r.academic_years, "mandatory": r.mandatory,
            "condition": r.condition, "validity_rule": r.validity_rule, "source_reference": r.source_reference,
            "source_url": r.source_url, "source_page": r.source_page, "source_excerpt": r.source_excerpt,
            "confirmed": r.confirmed_at is not None, "confirmed_by_id": r.confirmed_by_id, "confirmed_at": ser.iso(r.confirmed_at),
            "active": r.active, "import_key": r.import_key, "created_at": ser.iso(r.created_at)}


@router.get("/document-categories")
def list_categories(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(DocumentCategory).order_by(DocumentCategory.sort_order, DocumentCategory.label)).scalars().all()
    return {"items": [_cat(c) for c in rows]}


class CategoryIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    label: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    keywords: list[dict] = Field(default_factory=list)
    expected_fields: list[str] = Field(default_factory=list)
    signature_expected: bool | None = None
    validity_applicable: bool = False
    notes: str | None = Field(default=None, max_length=2000)
    active: bool = True


def _check_keywords(kws: list[dict]) -> list[dict]:
    out = []
    for k in kws:
        p = str(k.get("pattern", "")).strip().lower()
        try:
            w = float(k.get("weight", 1))
        except (TypeError, ValueError):
            raise HTTPException(422, "Keyword weights must be numbers.") from None
        if p and 0 < w <= 10:
            out.append({"pattern": p[:120], "weight": w})
    return out


@router.post("/document-categories", status_code=201)
def create_category(body: CategoryIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    if db.get(DocumentCategory, body.key):
        raise HTTPException(409, "A category with this key exists.")
    c = DocumentCategory(**body.model_dump(exclude={"keywords"}), keywords=_check_keywords(body.keywords))
    db.add(c)
    db.flush()
    audit.record(db, "document_category.created", officer=auth.officer, entity_type="document_category", entity_id=c.key,
                 after=_cat(c), ctx=auth.ctx)
    db.commit()
    return _cat(c)


@router.patch("/document-categories/{key}")
def update_category(key: str, body: CategoryIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    c = get_or_404(db, DocumentCategory, key, "Category")
    before = _cat(c)
    for k, v in body.model_dump(exclude={"key", "keywords"}).items():
        setattr(c, k, v)
    c.keywords = _check_keywords(body.keywords)
    audit.record(db, "document_category.updated", officer=auth.officer, entity_type="document_category", entity_id=c.key,
                 before=before, after=_cat(c), ctx=auth.ctx)
    db.commit()
    return _cat(c)


@router.get("/requirements")
def list_requirements(application_type: str | None = None, include_inactive: bool = False,
                      auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    stmt = select(DocumentRequirement)
    if application_type:
        stmt = stmt.where(DocumentRequirement.application_type == application_type)
    if not include_inactive:
        stmt = stmt.where(DocumentRequirement.active.is_(True))
    rows = db.execute(stmt.order_by(DocumentRequirement.application_type, DocumentRequirement.import_key, DocumentRequirement.title)).scalars().all()
    cats = {c.key: c.label for c in db.execute(select(DocumentCategory)).scalars()}
    return {"items": [_req(r, cats) for r in rows], "application_types": APPLICATION_TYPES}


class RequirementIn(BaseModel):
    category_key: str
    title: str = Field(min_length=3, max_length=400)
    application_type: str
    institution_category: str | None = Field(default=None, max_length=120)
    academic_years: list[str] = Field(default_factory=list)
    mandatory: bool = True
    condition: str | None = Field(default=None, max_length=4000)
    validity_rule: dict = Field(default_factory=dict)
    source_reference: str = Field(min_length=5, max_length=2000)
    source_url: str | None = Field(default=None, max_length=500)
    source_page: str | None = Field(default=None, max_length=40)
    source_excerpt: str | None = Field(default=None, max_length=8000)
    active: bool = True
    reason: str | None = Field(default=None, max_length=2000)


def _validate_req(db: Session, body: RequirementIn) -> list[str]:
    if not db.get(DocumentCategory, body.category_key):
        raise HTTPException(422, "Unknown document category.")
    if body.application_type not in APPLICATION_TYPES:
        raise HTTPException(422, "Unknown application type.")
    years = []
    for y in body.academic_years:
        ay, _m, ok = normalize_academic_year(y)
        if not ok:
            raise HTTPException(422, f"Invalid academic year: {y}")
        years.append(ay)
    if body.source_url and not body.source_url.startswith("https://"):
        raise HTTPException(422, "Source URL must use HTTPS.")
    return years


@router.post("/requirements", status_code=201)
def create_requirement(body: RequirementIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    years = _validate_req(db, body)
    r = DocumentRequirement(**body.model_dump(exclude={"academic_years", "reason"}), academic_years=years, created_by_id=auth.officer.id)
    db.add(r)
    db.flush()
    audit.record(db, "requirement.created", officer=auth.officer, entity_type="requirement", entity_id=r.id, after=_req(r),
                 reason=body.reason, ctx=auth.ctx)
    db.commit()
    return _req(r)


@router.patch("/requirements/{req_id}")
def update_requirement(req_id: str, body: RequirementIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    r = get_or_404(db, DocumentRequirement, req_id, "Requirement")
    years = _validate_req(db, body)
    before = _req(r)
    for k, v in body.model_dump(exclude={"academic_years", "reason"}).items():
        setattr(r, k, v)
    r.academic_years = years
    r.confirmed_at = None  # a changed requirement must be confirmed again
    r.confirmed_by_id = None
    audit.record(db, "requirement.updated", officer=auth.officer, entity_type="requirement", entity_id=r.id, before=before,
                 after=_req(r), reason=body.reason, ctx=auth.ctx)
    db.commit()
    return _req(r)


@router.post("/requirements/{req_id}/confirm")
def confirm_requirement(req_id: str, body: dict, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    r = get_or_404(db, DocumentRequirement, req_id, "Requirement")
    note = str(body.get("note") or "").strip()
    if len(note) < 5:
        raise HTTPException(422, "Describe how you confirmed this requirement (e.g. the handbook edition and clause checked).")
    r.confirmed_at = utcnow()
    r.confirmed_by_id = auth.officer.id
    audit.record(db, "requirement.confirmed", officer=auth.officer, entity_type="requirement", entity_id=r.id,
                 reason=note, external_source=r.source_reference, ctx=auth.ctx)
    db.commit()
    return _req(r)


@router.post("/requirements/import")
async def import_requirements_file(file: UploadFile = File(...), auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """Import a requirements JSON file (same format as data/reference/aph_2024_27_requirements.json)."""
    from ..services.bootstrap import import_requirements

    raw = await file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "The file is too large.")
    try:
        data = json.loads(raw.decode("utf-8"))
        for req in data.get("requirements", []):
            if not str(req.get("source_reference", "")).strip():
                raise ValueError(f"Requirement '{req.get('title')}' has no source_reference.")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError) as exc:
        raise HTTPException(422, f"Invalid requirements file: {exc}") from exc
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "req.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        try:
            added = import_requirements(db, p, created_by_id=auth.officer.id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, f"Invalid requirements file: {exc}") from exc
    audit.record(db, "requirement.imported", officer=auth.officer, entity_type="requirement", after={"added": added},
                 external_source=(data.get("source") or {}).get("title"), ctx=auth.ctx)
    db.commit()
    return {"added": added}
