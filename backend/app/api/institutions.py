"""Institutions: registry, profile, programs, historical records, configurable fields."""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from .. import serializers as ser
from ..db import get_db
from ..deps import Auth, require_officer
from ..documents.normalize import normalize_academic_year, normalize_name
from ..jobs import enqueue
from ..models import (
    AuditEvent,
    CorrectiveAction,
    Document,
    ExternalVerificationAttempt,
    ExtractedField,
    Finding,
    InspectionCase,
    Institution,
    InstitutionFieldDefinition,
    InstitutionRecord,
    InstitutionVersion,
    Program,
    VerificationCase,
)
from .common import get_or_404, like, page_params, paginate

router = APIRouter(prefix="/api", tags=["institutions"])

EDITABLE = ["name", "aicte_permanent_id", "category", "institution_type", "region", "state", "district", "address",
            "pincode", "website", "email", "phone", "management_body", "establishment_year", "affiliating_body",
            "awarding_body", "academic_year", "extra_fields"]
RECORD_KINDS = {"historical_approval", "extension_of_approval", "application", "verification_result", "compliance_report",
                "previous_details", "deficiency", "corrective_history", "faculty", "infrastructure", "other"}
SORTS = {"name": Institution.name, "state": Institution.state, "updated_at": Institution.updated_at,
         "verification_status": Institution.verification_status, "last_verified_at": Institution.last_verified_at}


class InstitutionIn(BaseModel):
    name: str = Field(min_length=2, max_length=400)
    aicte_permanent_id: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=120)
    institution_type: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=60)
    state: str | None = Field(default=None, max_length=80)
    district: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=2000)
    pincode: str | None = Field(default=None, max_length=10)
    website: str | None = Field(default=None, max_length=300)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    management_body: str | None = Field(default=None, max_length=300)
    establishment_year: int | None = None
    affiliating_body: str | None = Field(default=None, max_length=300)
    awarding_body: str | None = Field(default=None, max_length=300)
    academic_year: str | None = Field(default=None, max_length=9)
    extra_fields: dict = Field(default_factory=dict)


class InstitutionPatch(BaseModel):
    changes: dict
    reason: str | None = Field(default=None, max_length=2000)


def _clean(data: dict, db: Session, current: Institution | None = None) -> dict:
    out = {}
    for k in EDITABLE:
        if k not in data:
            continue
        v = data[k]
        if isinstance(v, str):
            v = v.strip() or None
        out[k] = v
    errors = []
    if "name" in out and not out["name"]:
        errors.append("Institution name is required.")
    pid = out.get("aicte_permanent_id")
    if pid:
        if not re.fullmatch(r"1-\d{6,12}", pid):
            errors.append("AICTE Permanent ID must look like '1-' followed by 6–12 digits.")
        dup = db.execute(select(Institution.id).where(Institution.aicte_permanent_id == pid)).scalar_one_or_none()
        if dup and (current is None or dup != current.id):
            errors.append("Another institution already has this AICTE Permanent ID.")
    if out.get("pincode") and not re.fullmatch(r"[1-9]\d{5}", out["pincode"]):
        errors.append("PIN code must be 6 digits.")
    if out.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", out["email"]):
        errors.append("Enter a valid email address.")
    if out.get("website") and not re.match(r"^https?://[^\s]+\.[^\s]+$", out["website"]):
        errors.append("Website must start with http:// or https://.")
    ey = out.get("establishment_year")
    if ey is not None and not (1800 <= int(ey) <= date.today().year):
        errors.append("Establishment year is out of range.")
    if out.get("academic_year"):
        ay, _m, ok = normalize_academic_year(out["academic_year"])
        if not ok:
            errors.append("Academic year must be like 2026-27.")
        else:
            out["academic_year"] = ay
    if "extra_fields" in out:
        if not isinstance(out["extra_fields"], dict):
            errors.append("Additional fields must be an object.")
        else:
            out["extra_fields"] = {str(k)[:80]: (str(v)[:2000] if v is not None else None) for k, v in out["extra_fields"].items()}
            category = out.get("category", current.category if current else None)
            defs = db.execute(select(InstitutionFieldDefinition).where(
                InstitutionFieldDefinition.active.is_(True),
                InstitutionFieldDefinition.category.in_(["*", category or ""]))).scalars().all()
            for d in defs:
                if d.required and not out["extra_fields"].get(d.key):
                    errors.append(f"'{d.label}' is required for this institution category.")
    if errors:
        raise HTTPException(422, " ".join(errors))
    if "name" in out:
        out["name_normalized"] = normalize_name(out["name"])
    return out


def _current_academic_year() -> str:
    today = date.today()
    start = today.year if today.month >= 7 else today.year - 1  # academic years run July–June
    return f"{start}-{str(start + 1)[-2:]}"


def _open_default_case(db: Session, inst: Institution, auth: Auth) -> None:
    """Every new institution gets a verification case so documents and the final decision have a home."""
    from .verifier import next_reference
    from ..verification.engine import evaluate_case

    ay = inst.academic_year or _current_academic_year()
    c = VerificationCase(reference=next_reference(db, VerificationCase, "VC"), institution_id=inst.id, academic_year=ay,
                         application_type="extension_of_approval", title=f"Document verification — {ay}",
                         assigned_to_id=auth.officer.id, opened_by_id=auth.officer.id)
    db.add(c)
    db.flush()
    evaluate_case(db, c, triggered_by_id=auth.officer.id)
    audit.record(db, "case.opened", officer=auth.officer, entity_type="case", entity_id=c.id, institution_id=inst.id, case_id=c.id,
                 summary="Opened automatically when the institution was added", ctx=auth.ctx)


def _refresh_dependents(db: Session, inst_id: str, officer_id: str) -> None:
    """Registry values feed document validation, so re-run those checks whenever the record changes."""
    enqueue(db, "revalidate_institution", {"institution_id": inst_id}, entity_type="institution", entity_id=inst_id,
            institution_id=inst_id, created_by_id=officer_id, priority=40)


def _stats(db: Session, inst_id: str) -> dict:
    def count(model, *conds):
        return db.execute(select(func.count()).select_from(model).where(*conds)).scalar_one()

    return {
        "documents": count(Document, Document.institution_id == inst_id),
        "documents_pending_review": count(Document, Document.institution_id == inst_id, Document.review_status != "reviewed",
                                          Document.status != "rejected"),
        "open_cases": count(VerificationCase, VerificationCase.institution_id == inst_id, VerificationCase.status != "decision_recorded"),
        "open_findings": count(Finding, Finding.institution_id == inst_id, Finding.status.in_(("open", "under_review"))),
        "inspections": count(InspectionCase, InspectionCase.institution_id == inst_id),
        "open_corrective_actions": count(CorrectiveAction, CorrectiveAction.institution_id == inst_id,
                                         CorrectiveAction.status.in_(("open", "submitted", "under_review"))),
        "programs": count(Program, Program.institution_id == inst_id),
    }


@router.get("/institutions/facets")
def facets(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    def distinct(col):
        return [v for (v,) in db.execute(select(col).where(col.is_not(None)).distinct().order_by(col)).all()]

    return {"states": distinct(Institution.state), "categories": distinct(Institution.category),
            "institution_types": distinct(Institution.institution_type), "regions": distinct(Institution.region)}


@router.get("/institutions")
def list_institutions(
    q: str | None = None, state: str | None = None, category: str | None = None, status: str | None = None,
    has_discrepancies: bool | None = None, archived: bool = False,
    sort: str = "name", order: str = Query("asc", pattern="^(asc|desc)$"),
    pg: tuple[int, int] = Depends(page_params), auth: Auth = Depends(require_officer), db: Session = Depends(get_db),
) -> dict:
    open_findings = (select(func.count()).select_from(Finding)
                     .where(Finding.institution_id == Institution.id, Finding.status.in_(("open", "under_review")))
                     .correlate(Institution).scalar_subquery())
    stmt = select(Institution).where(Institution.is_archived.is_(archived))
    if q:
        stmt = stmt.where(or_(Institution.name.ilike(like(q)), Institution.name_normalized.ilike(like(normalize_name(q))),
                              Institution.aicte_permanent_id.ilike(like(q)), Institution.district.ilike(like(q))))
    if state:
        stmt = stmt.where(Institution.state == state)
    if category:
        stmt = stmt.where(Institution.category == category)
    if status:
        stmt = stmt.where(Institution.verification_status == status)
    if has_discrepancies is not None:
        stmt = stmt.where(open_findings > 0 if has_discrepancies else open_findings == 0)
    col = SORTS.get(sort, Institution.name)
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc(), Institution.id)
    rows, total = paginate(db, stmt, *pg)
    counts = dict(db.execute(select(Finding.institution_id, func.count()).where(
        Finding.institution_id.in_([r.id for r in rows]), Finding.status.in_(("open", "under_review"))
    ).group_by(Finding.institution_id)).all()) if rows else {}
    items = [ser.institution(r, extra={"open_findings": counts.get(r.id, 0)}) for r in rows]
    return ser.page_of(items, total, *pg)


@router.post("/institutions", status_code=201)
def create_institution(body: InstitutionIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    data = _clean(body.model_dump(), db)
    inst = Institution(**data, created_by_id=auth.officer.id, updated_by_id=auth.officer.id)
    db.add(inst)
    db.flush()
    audit.record(db, "institution.created", officer=auth.officer, entity_type="institution", entity_id=inst.id,
                 institution_id=inst.id, after=ser.institution(inst), ctx=auth.ctx)
    _open_default_case(db, inst, auth)
    db.commit()
    return ser.institution(inst, extra={"stats": _stats(db, inst.id)})


@router.get("/institutions/{inst_id}")
def get_institution(inst_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    inst = get_or_404(db, Institution, inst_id, "Institution")
    defs = db.execute(select(InstitutionFieldDefinition).where(
        InstitutionFieldDefinition.active.is_(True), InstitutionFieldDefinition.category.in_(["*", inst.category or ""]))
        .order_by(InstitutionFieldDefinition.sort_order)).scalars().all()
    recent = db.execute(select(AuditEvent).where(AuditEvent.institution_id == inst.id).order_by(AuditEvent.id.desc()).limit(8)).scalars().all()
    return ser.institution(inst, extra={
        "stats": _stats(db, inst.id),
        "field_definitions": [_def(d) for d in defs],
        "recent_activity": [ser.audit_event(e) for e in recent],
    })


@router.patch("/institutions/{inst_id}")
def update_institution(inst_id: str, body: InstitutionPatch, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    inst = get_or_404(db, Institution, inst_id, "Institution")
    data = _clean(body.changes, db, inst)
    before = ser.institution(inst)
    changed = [k for k, v in data.items() if k != "name_normalized" and getattr(inst, k) != v]
    if not changed:
        return ser.institution(inst, extra={"stats": _stats(db, inst.id), "changed": []})
    db.add(InstitutionVersion(institution_id=inst.id, version=inst.version, snapshot=audit.jsonable(before),
                              changed_fields=changed, reason=body.reason, changed_by_id=auth.officer.id))
    for k, v in data.items():
        setattr(inst, k, v)
    inst.version += 1
    inst.updated_by_id = auth.officer.id
    db.flush()
    audit.record(db, "institution.updated", officer=auth.officer, entity_type="institution", entity_id=inst.id,
                 institution_id=inst.id, before={k: before.get(k) for k in changed},
                 after={k: ser.institution(inst).get(k) for k in changed}, reason=body.reason, ctx=auth.ctx)
    _refresh_dependents(db, inst.id, auth.officer.id)
    db.commit()
    return ser.institution(inst, extra={"stats": _stats(db, inst.id), "changed": changed})


@router.post("/institutions/{inst_id}/archive")
def archive_institution(inst_id: str, body: dict, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    inst = get_or_404(db, Institution, inst_id, "Institution")
    reason = str(body.get("reason") or "").strip()
    if len(reason) < 3:
        raise HTTPException(422, "A reason is required.")
    archived = bool(body.get("archived", True))
    inst.is_archived = archived
    audit.record(db, "institution.archived" if archived else "institution.restored", officer=auth.officer,
                 entity_type="institution", entity_id=inst.id, institution_id=inst.id, reason=reason, ctx=auth.ctx)
    db.commit()
    return ser.institution(inst)


@router.get("/institutions/{inst_id}/document-summary")
def document_summary(inst_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    from ..verification.summary import institution_summary

    return institution_summary(db, get_or_404(db, Institution, inst_id, "Institution"))


@router.get("/institutions/{inst_id}/versions")
def institution_versions(inst_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, Institution, inst_id, "Institution")
    rows = db.execute(select(InstitutionVersion).where(InstitutionVersion.institution_id == inst_id)
                      .order_by(InstitutionVersion.version.desc())).scalars().all()
    return {"items": [{"version": v.version, "snapshot": v.snapshot, "changed_fields": v.changed_fields, "reason": v.reason,
                       "changed_by_id": v.changed_by_id, "changed_at": ser.iso(v.changed_at)} for v in rows]}


# ---------------------------------------------------------------- approval status (evidence required)

class ApprovalStatusIn(BaseModel):
    attempt_id: str
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/institutions/{inst_id}/approval-status")
def record_approval_status(inst_id: str, body: ApprovalStatusIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """Approval status may only be recorded from an official-source response that states it."""
    inst = get_or_404(db, Institution, inst_id, "Institution")
    a = get_or_404(db, ExternalVerificationAttempt, body.attempt_id, "Verification attempt")
    if a.institution_id != inst.id or a.method != "automated":
        raise HTTPException(422, "Approval status must come from an automated official-source response for this institution.")
    record = ((a.matched_values or {}).get("official_record") or {}).get("fields", {})
    status_text = record.get("approval_status")
    if not status_text:
        raise HTTPException(422, "The official response does not state an approval status.")
    year = record.get("academic_year")
    before = {"approval_status": inst.approval_status}
    inst.approval_status = f"{status_text}" + (f" ({year})" if year else "")
    inst.approval_status_evidence = {"attempt_id": a.id, "source": a.source_name, "source_url": a.source_url,
                                     "retrieved_at": ser.iso(a.attempted_at), "response_sha256": a.response_sha256}
    audit.record(db, "institution.approval_status_recorded", officer=auth.officer, entity_type="institution", entity_id=inst.id,
                 institution_id=inst.id, before=before, after={"approval_status": inst.approval_status},
                 external_source=a.source_name, evidence=[{"type": "external_attempt", "id": a.id}], reason=body.reason, ctx=auth.ctx)
    db.commit()
    return ser.institution(inst)


# ---------------------------------------------------------------- programs

class ProgramIn(BaseModel):
    academic_year: str
    course: str = Field(min_length=2, max_length=300)
    level: str | None = Field(default=None, max_length=80)
    program: str | None = Field(default=None, max_length=200)
    shift: str | None = Field(default=None, max_length=40)
    affiliating_body: str | None = Field(default=None, max_length=300)
    approved_intake: int | None = Field(default=None, ge=0, le=100000)
    nri_status: str | None = Field(default=None, max_length=40)
    foreign_status: str | None = Field(default=None, max_length=40)
    status: str = Field(default="active", pattern="^(active|closed|proposed)$")
    source_document_id: str | None = None
    source_reference: str | None = Field(default=None, max_length=2000)
    compliance: dict = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=2000)


def _program_source(db: Session, inst_id: str, body: ProgramIn) -> str:
    if body.source_document_id:
        d = db.get(Document, body.source_document_id)
        if d is None or d.institution_id != inst_id:
            raise HTTPException(422, "The source document does not belong to this institution.")
        return "document"
    if body.approved_intake is not None and not (body.source_reference or "").strip():
        raise HTTPException(422, "An approved intake must cite its source (a document or an official reference).")
    return "external_source" if body.source_reference else "officer_entry"


@router.get("/institutions/{inst_id}/programs")
def list_programs(inst_id: str, academic_year: str | None = None, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, Institution, inst_id, "Institution")
    stmt = select(Program).where(Program.institution_id == inst_id)
    if academic_year:
        stmt = stmt.where(Program.academic_year == academic_year)
    rows = db.execute(stmt.order_by(Program.academic_year.desc(), Program.level, Program.course)).scalars().all()
    years = sorted({r.academic_year for r in db.execute(select(Program).where(Program.institution_id == inst_id)).scalars()}, reverse=True)
    return {"items": [ser.program(p) for p in rows], "academic_years": years}


@router.post("/institutions/{inst_id}/programs", status_code=201)
def create_program(inst_id: str, body: ProgramIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    inst = get_or_404(db, Institution, inst_id, "Institution")
    ay, _m, ok = normalize_academic_year(body.academic_year)
    if not ok:
        raise HTTPException(422, "Academic year must be like 2026-27.")
    source_type = _program_source(db, inst_id, body)
    p = Program(institution_id=inst.id, academic_year=ay, source_type=source_type, created_by_id=auth.officer.id,
                **body.model_dump(exclude={"academic_year", "reason"}))
    db.add(p)
    db.flush()
    audit.record(db, "program.created", officer=auth.officer, entity_type="program", entity_id=p.id, institution_id=inst.id,
                 after=ser.program(p), reason=body.reason, ctx=auth.ctx)
    _refresh_dependents(db, inst.id, auth.officer.id)
    db.commit()
    return ser.program(p)


@router.patch("/programs/{program_id}")
def update_program(program_id: str, body: ProgramIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    p = get_or_404(db, Program, program_id, "Program")
    ay, _m, ok = normalize_academic_year(body.academic_year)
    if not ok:
        raise HTTPException(422, "Academic year must be like 2026-27.")
    before = ser.program(p)
    p.source_type = _program_source(db, p.institution_id, body)
    p.academic_year = ay
    for k, v in body.model_dump(exclude={"academic_year", "reason"}).items():
        setattr(p, k, v)
    db.flush()
    audit.record(db, "program.updated", officer=auth.officer, entity_type="program", entity_id=p.id, institution_id=p.institution_id,
                 before=before, after=ser.program(p), reason=body.reason, ctx=auth.ctx)
    _refresh_dependents(db, p.institution_id, auth.officer.id)
    db.commit()
    return ser.program(p)


@router.post("/institutions/{inst_id}/programs/import-from-document")
def import_programs(inst_id: str, body: dict, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """Create program rows from a processed document's extracted program table (officer-initiated)."""
    inst = get_or_404(db, Institution, inst_id, "Institution")
    doc = get_or_404(db, Document, str(body.get("document_id")), "Document")
    if doc.institution_id != inst.id or not doc.current_version_id:
        raise HTTPException(422, "Choose a processed document belonging to this institution.")
    if not doc.academic_year:
        raise HTTPException(422, "The document's academic year is unknown; set it before importing programs.")
    rows = db.execute(select(ExtractedField).where(ExtractedField.version_id == doc.current_version_id,
                                                   ExtractedField.group_index.is_not(None))).scalars().all()
    groups: dict[int, dict] = {}
    for f in rows:
        groups.setdefault(f.group_index, {})[f.field_key] = f
    existing = {(normalize_name(p.level or ""), normalize_name(p.course), p.approved_intake) for p in db.execute(
        select(Program).where(Program.institution_id == inst.id, Program.academic_year == doc.academic_year)).scalars()}
    created = []
    for g in groups.values():
        if "program_course" not in g:
            continue
        level = g.get("program_level").value if g.get("program_level") else None
        intake = g.get("program_intake")
        intake_val = int(intake.normalized_value) if intake and intake.normalized_value and intake.normalized_value.isdigit() else None
        key = (normalize_name(level or ""), normalize_name(g["program_course"].value), intake_val)  # same course may run as regular and online
        if key in existing:
            continue
        existing.add(key)
        p = Program(institution_id=inst.id, academic_year=doc.academic_year, course=g["program_course"].value[:300],
                    level=level, program=g["program_name"].value if g.get("program_name") else None,
                    affiliating_body=g["program_affiliating_body"].value if g.get("program_affiliating_body") else None,
                    approved_intake=int(intake.normalized_value) if intake and intake.normalized_value and intake.normalized_value.isdigit() else None,
                    source_type="document", source_document_id=doc.id,
                    source_reference=f"Extracted from '{doc.title}' page {g['program_course'].page_no}", created_by_id=auth.officer.id)
        db.add(p)
        created.append(p)
    db.flush()
    audit.record(db, "program.imported_from_document", officer=auth.officer, entity_type="institution", entity_id=inst.id,
                 institution_id=inst.id, after={"created": len(created), "academic_year": doc.academic_year},
                 evidence=[{"type": "document", "document_id": doc.id}], ctx=auth.ctx)
    db.commit()
    return {"created": len(created), "items": [ser.program(p) for p in created]}


@router.get("/institutions/{inst_id}/programs/compare")
def compare_programs(inst_id: str, year_a: str, year_b: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, Institution, inst_id, "Institution")
    rows = db.execute(select(Program).where(Program.institution_id == inst_id, Program.academic_year.in_([year_a, year_b]))).scalars().all()
    table: dict[tuple, dict] = {}
    for p in rows:
        key = (normalize_name(p.level or ""), normalize_name(p.course))
        entry = table.setdefault(key, {"course": p.course, "level": p.level, year_a: None, year_b: None})
        entry[p.academic_year] = {"approved_intake": p.approved_intake, "status": p.status, "id": p.id}
    out = []
    for e in table.values():
        a, b = e[year_a], e[year_b]
        if a and b:
            change = "unchanged" if a["approved_intake"] == b["approved_intake"] else "intake_changed"
        else:
            change = "added" if b and not a else "removed"
        out.append({**e, "change": change})
    return {"year_a": year_a, "year_b": year_b, "items": sorted(out, key=lambda x: (x["level"] or "", x["course"]))}


# ---------------------------------------------------------------- historical & supporting records

class RecordIn(BaseModel):
    kind: str
    title: str = Field(min_length=2, max_length=300)
    academic_year: str | None = None
    record_date: date | None = None
    summary: str | None = Field(default=None, max_length=10000)
    data: dict = Field(default_factory=dict)
    source_document_id: str | None = None
    source_reference: str | None = Field(default=None, max_length=2000)
    supersedes_id: str | None = None


@router.get("/institutions/{inst_id}/records")
def list_records(inst_id: str, kind: str | None = None, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, Institution, inst_id, "Institution")
    stmt = select(InstitutionRecord).where(InstitutionRecord.institution_id == inst_id)
    if kind:
        stmt = stmt.where(InstitutionRecord.kind == kind)
    rows = db.execute(stmt.order_by(InstitutionRecord.academic_year.desc(), InstitutionRecord.recorded_at.desc())).scalars().all()
    return {"items": [ser.record(r) for r in rows]}


@router.post("/institutions/{inst_id}/records", status_code=201)
def create_record(inst_id: str, body: RecordIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    inst = get_or_404(db, Institution, inst_id, "Institution")
    if body.kind not in RECORD_KINDS:
        raise HTTPException(422, "Unknown record kind.")
    if body.source_document_id:
        d = db.get(Document, body.source_document_id)
        if d is None or d.institution_id != inst.id:
            raise HTTPException(422, "The source document does not belong to this institution.")
        source_type = "document"
    elif (body.source_reference or "").strip():
        source_type = "external_source"
    else:
        raise HTTPException(422, "Historical records must cite a source document or a verifiable source reference.")
    ay = None
    if body.academic_year:
        ay, _m, ok = normalize_academic_year(body.academic_year)
        if not ok:
            raise HTTPException(422, "Academic year must be like 2026-27.")
    from datetime import datetime

    r = InstitutionRecord(institution_id=inst.id, kind=body.kind, title=body.title, academic_year=ay,
                          record_date=datetime.combine(body.record_date, datetime.min.time()) if body.record_date else None,
                          summary=body.summary, data=audit.jsonable(body.data), source_type=source_type,
                          source_document_id=body.source_document_id, source_reference=body.source_reference,
                          supersedes_id=body.supersedes_id, recorded_by_id=auth.officer.id)
    db.add(r)
    db.flush()
    audit.record(db, "institution_record.created", officer=auth.officer, entity_type="institution_record", entity_id=r.id,
                 institution_id=inst.id, after=ser.record(r), ctx=auth.ctx)
    db.commit()
    return ser.record(r)


@router.get("/institutions/{inst_id}/activity")
def institution_activity(inst_id: str, pg: tuple[int, int] = Depends(page_params), auth: Auth = Depends(require_officer),
                         db: Session = Depends(get_db)) -> dict:
    get_or_404(db, Institution, inst_id, "Institution")
    rows, total = paginate(db, select(AuditEvent).where(AuditEvent.institution_id == inst_id).order_by(AuditEvent.id.desc()), *pg)
    return ser.page_of([ser.audit_event(e) for e in rows], total, *pg)


# ---------------------------------------------------------------- configurable fields

class FieldDefIn(BaseModel):
    category: str = Field(min_length=1, max_length=120)
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    label: str = Field(min_length=2, max_length=200)
    data_type: str = Field(default="text", pattern="^(text|number|date|url|boolean)$")
    required: bool = False
    help_text: str | None = Field(default=None, max_length=2000)
    source_reference: str | None = Field(default=None, max_length=2000)
    sort_order: int = 100
    active: bool = True


def _def(d: InstitutionFieldDefinition) -> dict:
    return {"id": d.id, "category": d.category, "key": d.key, "label": d.label, "data_type": d.data_type, "required": d.required,
            "help_text": d.help_text, "source_reference": d.source_reference, "sort_order": d.sort_order, "active": d.active}


@router.get("/institution-fields")
def list_field_defs(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(InstitutionFieldDefinition).order_by(InstitutionFieldDefinition.category,
                                                                  InstitutionFieldDefinition.sort_order)).scalars().all()
    return {"items": [_def(d) for d in rows]}


@router.post("/institution-fields", status_code=201)
def create_field_def(body: FieldDefIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    if body.required and not (body.source_reference or "").strip():
        raise HTTPException(422, "A mandatory field must cite the official requirement that makes it mandatory.")
    if db.execute(select(InstitutionFieldDefinition).where(InstitutionFieldDefinition.category == body.category,
                                                           InstitutionFieldDefinition.key == body.key)).first():
        raise HTTPException(409, "This field already exists for the category.")
    d = InstitutionFieldDefinition(**body.model_dump(), created_by_id=auth.officer.id)
    db.add(d)
    db.flush()
    audit.record(db, "institution_field.created", officer=auth.officer, entity_type="institution_field", entity_id=d.id,
                 after=_def(d), ctx=auth.ctx)
    db.commit()
    return _def(d)


@router.patch("/institution-fields/{def_id}")
def update_field_def(def_id: str, body: FieldDefIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    d = get_or_404(db, InstitutionFieldDefinition, def_id, "Field")
    if body.required and not (body.source_reference or "").strip():
        raise HTTPException(422, "A mandatory field must cite the official requirement that makes it mandatory.")
    before = _def(d)
    for k, v in body.model_dump(exclude={"category", "key"}).items():
        setattr(d, k, v)
    audit.record(db, "institution_field.updated", officer=auth.officer, entity_type="institution_field", entity_id=d.id,
                 before=before, after=_def(d), ctx=auth.ctx)
    db.commit()
    return _def(d)
