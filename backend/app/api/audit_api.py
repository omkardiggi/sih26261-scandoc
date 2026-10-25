"""Audit trail browsing and tamper-evidence verification."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from .. import serializers as ser
from ..db import get_db
from ..deps import Auth, require_officer
from ..models import AuditEvent
from .common import page_params, paginate, parse_date_param

router = APIRouter(prefix="/api/audit", tags=["audit"])


def audit_query(officer_id=None, action=None, entity_type=None, entity_id=None, institution_id=None, case_id=None,
                date_from=None, date_to=None):
    stmt = select(AuditEvent)
    for col, val in ((AuditEvent.officer_id, officer_id), (AuditEvent.entity_type, entity_type), (AuditEvent.entity_id, entity_id),
                     (AuditEvent.institution_id, institution_id), (AuditEvent.case_id, case_id)):
        if val:
            stmt = stmt.where(col == val)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if (f := parse_date_param(date_from)):
        stmt = stmt.where(AuditEvent.occurred_at >= f)
    if (t := parse_date_param(date_to, end=True)):
        stmt = stmt.where(AuditEvent.occurred_at <= t)
    return stmt.order_by(AuditEvent.id.desc())


@router.get("")
def list_events(officer_id: str | None = None, action: str | None = None, entity_type: str | None = None, entity_id: str | None = None,
                institution_id: str | None = None, case_id: str | None = None, date_from: str | None = None, date_to: str | None = None,
                pg: tuple[int, int] = Depends(page_params), auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    rows, total = paginate(db, audit_query(officer_id, action, entity_type, entity_id, institution_id, case_id, date_from, date_to), *pg)
    return ser.page_of([ser.audit_event(e) for e in rows], total, *pg)


@router.get("/actions")
def actions(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    return {"items": [a for (a,) in db.execute(select(AuditEvent.action).distinct().order_by(AuditEvent.action)).all()]}


@router.post("/verify")
def verify(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    result = audit.verify_chain(db)
    audit.record(db, "audit.chain_verified", officer=auth.officer, outcome="intact" if result["ok"] else "broken",
                 after={"events_checked": result["events_checked"], "first_break": result["first_break"]}, ctx=auth.ctx)
    db.commit()
    return result
