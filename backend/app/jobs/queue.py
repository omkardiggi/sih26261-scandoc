"""Database-backed job queue with leases, retries, timeouts and idempotency keys."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Job, utcnow


class PermanentJobError(Exception):
    """Raised by handlers for failures that retrying cannot fix (e.g. corrupt input)."""


def enqueue(
    db: Session,
    job_type: str,
    payload: dict,
    *,
    idempotency_key: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    institution_id: str | None = None,
    created_by_id: str | None = None,
    priority: int = 100,
    max_attempts: int = 3,
) -> Job:
    """Create a job, or return the existing job with the same idempotency key."""
    if idempotency_key:
        existing = db.execute(select(Job).where(Job.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing is not None:
            return existing
    job = Job(
        type=job_type,
        payload=payload,
        idempotency_key=idempotency_key,
        entity_type=entity_type,
        entity_id=entity_id,
        institution_id=institution_id,
        created_by_id=created_by_id,
        priority=priority,
        max_attempts=max_attempts,
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        existing = db.execute(select(Job).where(Job.idempotency_key == idempotency_key)).scalar_one()
        return existing
    return job


def retry(db: Session, job: Job) -> Job:
    """Re-queue a failed or cancelled job (manual retry by an officer)."""
    if job.status not in {"failed", "cancelled"}:
        raise ValueError("Only failed or cancelled jobs can be retried")
    job.status = "queued"
    job.max_attempts = job.attempts + max(1, min(3, job.max_attempts))
    job.run_after = utcnow()
    job.error = None
    job.progress = 0.0
    job.progress_message = "Queued for retry"
    job.locked_by = None
    job.lease_expires_at = None
    job.finished_at = None
    return job


def reap_expired_leases(db: Session) -> int:
    """Return timed-out running jobs to the queue (or fail them when out of attempts)."""
    now = utcnow()
    expired = db.execute(
        select(Job).where(Job.status == "running", Job.lease_expires_at < now)
    ).scalars().all()
    for job in expired:
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.error = "Job exceeded its processing time limit and has no attempts remaining."
            job.finished_at = now
        else:
            job.status = "queued"
            job.run_after = now
            job.progress_message = "Previous attempt timed out; re-queued"
        job.locked_by = None
        job.lease_expires_at = None
    return len(expired)


def claim(db: Session, worker_id: str, types: set[str] | None = None) -> Job | None:
    now = utcnow()
    stmt = (
        select(Job.id)
        .where(Job.status == "queued", Job.run_after <= now)
        .order_by(Job.priority, Job.created_at)
        .limit(5)
    )
    if types:
        stmt = stmt.where(Job.type.in_(types))
    lease = now + timedelta(seconds=get_settings().job_lease_seconds)
    for (job_id,) in db.execute(stmt).all():
        res = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(
                status="running",
                locked_by=worker_id,
                locked_at=now,
                lease_expires_at=lease,
                attempts=Job.attempts + 1,
                started_at=now,
                progress_message="Started",
            )
        )
        if res.rowcount == 1:
            db.commit()
            return db.get(Job, job_id)
    db.commit()
    return None
