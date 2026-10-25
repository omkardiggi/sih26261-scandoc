"""Background worker: claims jobs and dispatches them to registered handlers."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import update

from ..config import get_settings
from ..db import session_factory
from ..models import Job, utcnow
from .queue import PermanentJobError, claim, reap_expired_leases

log = logging.getLogger("app.worker")

Handler = Callable[["JobContext"], dict | None]
HANDLERS: dict[str, Handler] = {}


def handler(job_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return deco


class JobContext:
    def __init__(self, job: Job):
        self.job_id = job.id
        self.type = job.type
        self.payload = dict(job.payload or {})
        self.attempt = job.attempts
        self.created_by_id = job.created_by_id
        self._last_progress = 0.0

    def progress(self, fraction: float, message: str | None = None, *, force: bool = False) -> None:
        """Persist progress (0..1) and extend the lease. Throttled to ~1 write/second."""
        now = time.monotonic()
        if not force and now - self._last_progress < 1.0:
            return
        self._last_progress = now
        lease = utcnow() + timedelta(seconds=get_settings().job_lease_seconds)
        with session_factory()() as db:
            db.execute(
                update(Job)
                .where(Job.id == self.job_id, Job.status == "running")
                .values(progress=max(0.0, min(1.0, fraction)), progress_message=message, lease_expires_at=lease)
            )
            db.commit()


def _load_handlers() -> None:
    # Import modules that register handlers via @handler.
    from ..documents import pipeline  # noqa: F401
    from ..inspection import pipeline as inspection_pipeline  # noqa: F401
    from ..verification import jobs as verification_jobs  # noqa: F401


def run_job(job: Job) -> None:
    fn = HANDLERS.get(job.type)
    ctx = JobContext(job)
    Session = session_factory()
    if fn is None:
        with Session() as db:
            j = db.get(Job, job.id)
            j.status, j.error, j.finished_at = "failed", f"No handler registered for job type '{job.type}'", utcnow()
            db.commit()
        return
    try:
        result = fn(ctx)
        with Session() as db:
            j = db.get(Job, job.id)
            j.status = "succeeded"
            j.result = result or {}
            j.progress = 1.0
            j.progress_message = "Completed"
            j.finished_at = utcnow()
            j.locked_by = None
            j.lease_expires_at = None
            db.commit()
    except Exception as exc:  # noqa: BLE001
        permanent = isinstance(exc, PermanentJobError)
        log.warning("job %s (%s) attempt %s failed: %s", job.id, job.type, job.attempts, type(exc).__name__)
        log.debug("job failure detail: %s", traceback.format_exc())
        with Session() as db:
            j = db.get(Job, job.id)
            message = str(exc) if permanent else f"{type(exc).__name__}: {exc}"
            j.error = message[:2000]
            j.locked_by = None
            j.lease_expires_at = None
            if permanent or j.attempts >= j.max_attempts:
                j.status = "failed"
                j.finished_at = utcnow()
                j.progress_message = "Failed"
            else:
                j.status = "queued"
                j.run_after = utcnow() + timedelta(seconds=min(300, 5 * 2 ** (j.attempts - 1)))
                j.progress_message = f"Attempt {j.attempts} failed; retry scheduled"
            db.commit()
        on_failure = getattr(fn, "on_final_failure", None)
        if on_failure and (permanent or job.attempts >= job.max_attempts):
            try:
                on_failure(ctx, message)
            except Exception:  # noqa: BLE001
                log.exception("on_final_failure hook failed for job %s", job.id)


class Worker(threading.Thread):
    def __init__(self, index: int = 0):
        super().__init__(daemon=True, name=f"job-worker-{index}")
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{index}"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # pragma: no cover - exercised via integration runs
        _load_handlers()
        Session = session_factory()
        poll = get_settings().job_poll_seconds
        while not self._stop.is_set():
            try:
                with Session() as db:
                    reap_expired_leases(db)
                    db.commit()
                    job = claim(db, self.worker_id)
                if job is None:
                    self._stop.wait(poll)
                    continue
                run_job(job)
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                self._stop.wait(poll * 3)


def run_pending_jobs(limit: int = 100) -> int:
    """Synchronously process queued jobs (used by tests and the CLI)."""
    _load_handlers()
    Session = session_factory()
    done = 0
    while done < limit:
        with Session() as db:
            reap_expired_leases(db)
            db.commit()
            job = claim(db, "sync-runner")
        if job is None:
            break
        run_job(job)
        done += 1
    return done


def run_pending_for(seconds: float) -> int:
    """Run queued jobs one at a time until none remain or ``seconds`` have passed (request-driven job mode)."""
    import time

    _load_handlers()
    Session = session_factory()
    started, done = time.monotonic(), 0
    while time.monotonic() - started < seconds:
        with Session() as db:
            reap_expired_leases(db)
            db.commit()
            job = claim(db, "request-runner")
        if job is None:
            break
        run_job(job)
        done += 1
    return done


_workers: list[Worker] = []


def start_workers() -> None:
    if _workers:
        return
    for i in range(max(1, get_settings().worker_threads)):
        w = Worker(i)
        w.start()
        _workers.append(w)


def stop_workers() -> None:
    for w in _workers:
        w.stop()
    for w in _workers:
        w.join(timeout=5)
    _workers.clear()
