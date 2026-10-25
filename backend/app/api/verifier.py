"""Institution Verifier: verification cases, explanations and officer decisions."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from .. import serializers as ser
from ..db import get_db
from ..deps import Auth, require_officer
from ..documents.normalize import normalize_academic_year
from ..jobs import enqueue
from ..models import Decision, Document, Institution, Officer, VerificationCase, utcnow
from ..services.bootstrap import APPLICATION_TYPES
from ..verification.engine import CASE_STATUSES, build_explanation, evaluate_case, outstanding_tasks
from .common import get_or_404, like, page_params, paginate

router = APIRouter(prefix="/api", tags=["verifier"])

CASE_OUTCOMES = {
    "verification_complete_no_discrepancies": "Verification complete — no outstanding discrepancies",
    "verification_complete_discrepancies_noted": "Verification complete — discrepancies noted",
    "returned_for_corrections": "Returned to institution for corrective documents",
    "referred_for_physical_inspection": "Referred for physical inspection / expert review",
    "rejected_by_officer": "Rejected by authorized officer",
    "other": "Other (see justification)",
}


def next_reference(db: Session, model, prefix: str) -> str:
    year = date.today().year
    n = db.execute(select(func.count()).select_from(model).where(model.reference.like(f"{prefix}-{year}-%"))).scalar_one()
    return f"{prefix}-{year}-{n + 1:05d}"


class CaseIn(BaseModel):
    institution_id: str
    academic_year: str
    application_type: str
    title: str | None = Field(default=None, max_length=300)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=10000)
    assigned_to_id: str | None = None


class CasePatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    priority: str | None = Field(default=None, pattern="^(low|normal|high|urgent)$")
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=10000)
    assigned_to_id: str | None = None
    academic_year: str | None = None
    application_type: str | None = None
    reason: str | None = Field(default=None, max_length=2000)


@router.get("/application-types")
def application_types(auth: Auth = Depends(require_officer)) -> dict:
    return {"items": [{"key": k, "label": v} for k, v in APPLICATION_TYPES.items()],
            "case_statuses": CASE_STATUSES, "case_outcomes": CASE_OUTCOMES}


def _case_out(db: Session, c: VerificationCase) -> dict:
    inst = db.get(Institution, c.institution_id)
    docs = db.execute(select(func.count()).select_from(Document).where(Document.case_id == c.id)).scalar_one()
    return ser.case(c, institution_name=inst.name if inst else None,
                    extra={"status_label": CASE_STATUSES.get(c.status, c.status), "document_count": docs,
                           "application_type_label": APPLICATION_TYPES.get(c.application_type, c.application_type),
                           "evaluation": c.evaluation})


@router.get("/cases")
def list_cases(q: str | None = None, status: str | None = None, institution_id: str | None = None,
               priority: str | None = None, open_only: bool = False, assigned_to_me: bool = False,
               pg: tuple[int, int] = Depends(page_params), auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    stmt = select(VerificationCase).join(Institution, Institution.id == VerificationCase.institution_id)
    if q:
        stmt = stmt.where(or_(VerificationCase.reference.ilike(like(q)), VerificationCase.title.ilike(like(q)), Institution.name.ilike(like(q))))
    if status:
        stmt = stmt.where(VerificationCase.status == status)
    if institution_id:
        stmt = stmt.where(VerificationCase.institution_id == institution_id)
    if priority:
        stmt = stmt.where(VerificationCase.priority == priority)
    if open_only:
        stmt = stmt.where(VerificationCase.status != "decision_recorded")
    if assigned_to_me:
        stmt = stmt.where(VerificationCase.assigned_to_id == auth.officer.id)
    rows, total = paginate(db, stmt.order_by(VerificationCase.opened_at.desc()), *pg)
    return ser.page_of([_case_out(db, c) for c in rows], total, *pg)


@router.post("/cases", status_code=201)
def create_case(body: CaseIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    inst = get_or_404(db, Institution, body.institution_id, "Institution")
    ay, _m, ok = normalize_academic_year(body.academic_year)
    if not ok:
        raise HTTPException(422, "Academic year must be like 2026-27.")
    if body.application_type not in APPLICATION_TYPES:
        raise HTTPException(422, "Unknown application type.")
    if body.assigned_to_id and not db.get(Officer, body.assigned_to_id):
        raise HTTPException(422, "Assigned officer not found.")
    for _ in range(3):
        c = VerificationCase(
            reference=next_reference(db, VerificationCase, "VC"), institution_id=inst.id, academic_year=ay,
            application_type=body.application_type,
            title=body.title or f"{APPLICATION_TYPES[body.application_type]} — {ay}", priority=body.priority,
            due_date=datetime.combine(body.due_date, datetime.min.time()) if body.due_date else None, notes=body.notes,
            assigned_to_id=body.assigned_to_id or auth.officer.id, opened_by_id=auth.officer.id,
        )
        try:
            with db.begin_nested():
                db.add(c)
                db.flush()
            break
        except IntegrityError:
            continue
    else:
        raise HTTPException(409, "Could not allocate a case reference; please retry.")
    evaluate_case(db, c, triggered_by_id=auth.officer.id)
    audit.record(db, "case.opened", officer=auth.officer, entity_type="case", entity_id=c.id, institution_id=inst.id, case_id=c.id,
                 after=ser.case(c), ctx=auth.ctx)
    db.commit()
    return _case_out(db, c)


@router.get("/cases/{case_id}")
def get_case(case_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    c = get_or_404(db, VerificationCase, case_id, "Case")
    docs = db.execute(select(Document).where(Document.case_id == c.id).order_by(Document.created_at)).scalars().all()
    out = _case_out(db, c)
    out["documents"] = [ser.document(d) for d in docs]
    out["outstanding_tasks"] = outstanding_tasks(db, c)
    return out


@router.patch("/cases/{case_id}")
def update_case(case_id: str, body: CasePatch, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    c = get_or_404(db, VerificationCase, case_id, "Case")
    before = ser.case(c)
    data = body.model_dump(exclude_unset=True, exclude={"reason"})
    if "academic_year" in data and data["academic_year"]:
        ay, _m, ok = normalize_academic_year(data["academic_year"])
        if not ok:
            raise HTTPException(422, "Academic year must be like 2026-27.")
        data["academic_year"] = ay
    if "application_type" in data and data["application_type"] not in APPLICATION_TYPES:
        raise HTTPException(422, "Unknown application type.")
    if "due_date" in data:
        data["due_date"] = datetime.combine(data["due_date"], datetime.min.time()) if data["due_date"] else None
    for k, v in data.items():
        setattr(c, k, v)
    db.flush()
    if {"academic_year", "application_type"} & data.keys():
        enqueue(db, "evaluate_case", {"case_id": c.id}, entity_type="case", entity_id=c.id, institution_id=c.institution_id,
                created_by_id=auth.officer.id, priority=50)
    audit.record(db, "case.updated", officer=auth.officer, entity_type="case", entity_id=c.id, institution_id=c.institution_id,
                 case_id=c.id, before=before, after=ser.case(c), reason=body.reason, ctx=auth.ctx)
    db.commit()
    return _case_out(db, c)


@router.post("/cases/{case_id}/evaluate")
def reevaluate(case_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    c = get_or_404(db, VerificationCase, case_id, "Case")
    before = c.status
    evaluate_case(db, c, triggered_by_id=auth.officer.id)
    audit.record(db, "case.evaluated", officer=auth.officer, entity_type="case", entity_id=c.id, institution_id=c.institution_id,
                 case_id=c.id, before={"status": before}, after={"status": c.status}, ctx=auth.ctx)
    db.commit()
    return _case_out(db, c)


@router.get("/cases/{case_id}/explanation")
def explanation(case_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    return build_explanation(db, get_or_404(db, VerificationCase, case_id, "Case"))


class DecisionIn(BaseModel):
    outcome: str
    reason: str = Field(min_length=10, max_length=20000)
    evidence: list[dict] = Field(default_factory=list)
    supersedes_id: str | None = None
    acknowledge_outstanding: bool = False


@router.post("/cases/{case_id}/decisions", status_code=201)
def record_case_decision(case_id: str, body: DecisionIn, idempotency_key: str | None = Header(default=None, max_length=120),
                         auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    c = get_or_404(db, VerificationCase, case_id, "Case")
    if idempotency_key:
        existing = db.execute(select(Decision).where(Decision.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing:
            if existing.case_id != c.id:
                raise HTTPException(409, "Idempotency key already used for a different record.")
            return ser.decision(existing, {auth.officer.id: auth.officer})
    if body.outcome not in CASE_OUTCOMES:
        raise HTTPException(422, "Unknown decision outcome.")
    if body.supersedes_id:
        prev = db.get(Decision, body.supersedes_id)
        if prev is None or prev.case_id != c.id:
            raise HTTPException(422, "The decision being corrected does not belong to this case.")
    tasks = outstanding_tasks(db, c)
    blocking = [t for t in tasks if t["kind"] != "decision"]
    if blocking and not body.acknowledge_outstanding:
        raise HTTPException(409, {"message": "Outstanding items remain. Review them or acknowledge them explicitly to record this decision.",
                                  "outstanding": blocking[:20]})
    d = Decision(institution_id=c.institution_id, case_id=c.id, decision_type="case_final", outcome=body.outcome,
                 reason=body.reason.strip(), evidence=audit.jsonable(body.evidence), supersedes_id=body.supersedes_id,
                 idempotency_key=idempotency_key, decided_by_id=auth.officer.id,
                 evaluation_snapshot=audit.jsonable({"case_status": c.status, "evaluation": c.evaluation,
                                                     "outstanding_at_decision": blocking}))
    db.add(d)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.execute(select(Decision).where(Decision.idempotency_key == idempotency_key)).scalar_one()
        return ser.decision(existing, {auth.officer.id: auth.officer})
    before = c.status
    c.status = "decision_recorded"
    c.closed_at = utcnow()
    inst = db.get(Institution, c.institution_id)
    inst.verification_status = "decision_recorded"
    inst.last_verified_at = utcnow()
    audit.record(db, "decision.recorded", officer=auth.officer, entity_type="decision", entity_id=d.id, institution_id=c.institution_id,
                 case_id=c.id, before={"case_status": before}, after={"outcome": d.outcome, "supersedes_id": d.supersedes_id},
                 reason=d.reason, evidence=d.evidence, outcome=d.outcome, ctx=auth.ctx)
    db.commit()
    return ser.decision(d, {auth.officer.id: auth.officer})


@router.get("/cases/{case_id}/decisions")
def list_case_decisions(case_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, VerificationCase, case_id, "Case")
    rows = db.execute(select(Decision).where(Decision.case_id == case_id).order_by(Decision.decided_at.desc())).scalars().all()
    officers = {o.id: o for o in db.execute(select(Officer)).scalars()}
    return {"items": [ser.decision(d, officers) for d in rows]}
