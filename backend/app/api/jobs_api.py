"""Background job monitoring and manual retry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from .. import serializers as ser
from ..db import get_db
from ..deps import Auth, require_officer
from ..jobs import retry
from ..models import Document, DocumentVersion, Job, MediaAsset, utcnow
from .common import get_or_404, page_params, paginate

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("/drain")
def drain_jobs(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """Request job mode: run queued work now (the web app calls this while work is pending)."""
    from ..config import get_settings
    from ..jobs.worker import run_pending_for

    if get_settings().jobs_mode != "request":
        return {"mode": "worker", "ran": 0, "pending": 0}
    db.close()  # do not hold this request's connection while jobs run
    ran = run_pending_for(seconds=200)
    from ..db import session_scope

    with session_scope() as s2:
        pending = s2.query(Job).filter(Job.status.in_(("queued", "running"))).count()
    return {"mode": "request", "ran": ran, "pending": pending}


@router.get("")
def list_jobs(status: str | None = None, type: str | None = None, entity_id: str | None = None,
              pg: tuple[int, int] = Depends(page_params), auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    stmt = select(Job)
    if status:
        stmt = stmt.where(Job.status == status)
    if type:
        stmt = stmt.where(Job.type == type)
    if entity_id:
        stmt = stmt.where(Job.entity_id == entity_id)
    rows, total = paginate(db, stmt.order_by(Job.created_at.desc()), *pg)
    return ser.page_of([ser.job(j) for j in rows], total, *pg)


@router.get("/{job_id}")
def get_job(job_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    return ser.job(get_or_404(db, Job, job_id, "Job"))


@router.post("/{job_id}/retry")
def retry_job(job_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    j = get_or_404(db, Job, job_id, "Job")
    try:
        retry(db, j)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if j.type == "process_document_version":
        v = db.get(DocumentVersion, j.payload.get("version_id"))
        if v and v.processing_status == "failed":
            v.processing_status = "queued"
            d = db.get(Document, v.document_id)
            d.status = "processing"
    elif j.type in ("analyze_image", "process_video"):
        m = db.get(MediaAsset, j.payload.get("media_id"))
        if m:
            m.processing_status = "queued"
    audit.record(db, "job.retried", officer=auth.officer, entity_type="job", entity_id=j.id, institution_id=j.institution_id,
                 summary=f"Manual retry of {j.type}", ctx=auth.ctx)
    db.commit()
    return ser.job(j)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    j = get_or_404(db, Job, job_id, "Job")
    if j.status != "queued":
        raise HTTPException(409, "Only queued jobs can be cancelled.")
    j.status, j.finished_at, j.progress_message = "cancelled", utcnow(), "Cancelled by officer"
    audit.record(db, "job.cancelled", officer=auth.officer, entity_type="job", entity_id=j.id, institution_id=j.institution_id, ctx=auth.ctx)
    db.commit()
    return ser.job(j)
