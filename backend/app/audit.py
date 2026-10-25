"""Tamper-evident, append-only audit trail.

Each event stores ``hash = SHA256(prev_hash || canonical_json(event))``. The
chain head row is locked while appending so concurrent writers serialise.
UPDATE and DELETE on ``audit_events`` are rejected by database triggers
(see the initial migration), and :func:`verify_chain` detects any break.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditChainHead, AuditEvent, Officer, utcnow

GENESIS_HASH = "0" * 64

_HASHED_FIELDS = (
    "occurred_at",
    "officer_id",
    "officer_email",
    "action",
    "entity_type",
    "entity_id",
    "institution_id",
    "case_id",
    "summary",
    "before",
    "after",
    "reason",
    "evidence",
    "external_source",
    "outcome",
    "model_info",
    "ip",
    "request_id",
)


def jsonable(value: Any) -> Any:
    """Convert values to a JSON-stable representation (used before storing in JSON columns)."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical(ev: AuditEvent) -> bytes:
    data = {}
    for f in _HASHED_FIELDS:
        v = getattr(ev, f)
        data[f] = v.isoformat() if isinstance(v, datetime) else v
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_hash(prev_hash: str, ev: AuditEvent) -> str:
    return hashlib.sha256(prev_hash.encode("ascii") + _canonical(ev)).hexdigest()


@dataclass
class RequestContext:
    ip: str | None = None
    request_id: str | None = None


def record(
    db: Session,
    action: str,
    *,
    officer: Officer | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    institution_id: str | None = None,
    case_id: str | None = None,
    summary: str | None = None,
    before: Any = None,
    after: Any = None,
    reason: str | None = None,
    evidence: Any = None,
    external_source: str | None = None,
    outcome: str | None = None,
    model_info: Any = None,
    ctx: RequestContext | None = None,
) -> AuditEvent:
    """Append an audit event within the caller's transaction."""
    head = db.execute(
        select(AuditChainHead).where(AuditChainHead.id == 1).with_for_update()
    ).scalar_one_or_none()
    if head is None:
        head = AuditChainHead(id=1, last_event_id=0, last_hash=GENESIS_HASH)
        db.add(head)
        db.flush()

    ev = AuditEvent(
        occurred_at=utcnow(),
        officer_id=officer.id if officer else None,
        officer_email=officer.email if officer else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        institution_id=institution_id,
        case_id=case_id,
        summary=summary,
        before=jsonable(before),
        after=jsonable(after),
        reason=reason,
        evidence=jsonable(evidence),
        external_source=external_source,
        outcome=outcome,
        model_info=jsonable(model_info),
        ip=ctx.ip if ctx else None,
        request_id=ctx.request_id if ctx else None,
        prev_hash=head.last_hash,
    )
    ev.hash = compute_hash(head.last_hash, ev)
    db.add(ev)
    db.flush()
    head.last_event_id = ev.id
    head.last_hash = ev.hash
    return ev


def verify_chain(db: Session, batch: int = 2000) -> dict:
    """Recompute every hash. Returns {"ok", "events_checked", "first_break"}."""
    prev = GENESIS_HASH
    checked = 0
    last_id = 0
    while True:
        rows = (
            db.execute(select(AuditEvent).where(AuditEvent.id > last_id).order_by(AuditEvent.id).limit(batch))
            .scalars()
            .all()
        )
        if not rows:
            break
        for ev in rows:
            if ev.prev_hash != prev:
                return {"ok": False, "events_checked": checked, "first_break": {"event_id": ev.id, "problem": "Previous-hash link does not match the preceding event."}}
            if compute_hash(prev, ev) != ev.hash:
                return {"ok": False, "events_checked": checked, "first_break": {"event_id": ev.id, "problem": "Event contents do not match the stored hash."}}
            prev = ev.hash
            checked += 1
            last_id = ev.id
    head = db.get(AuditChainHead, 1)
    if head is not None and checked and head.last_hash != prev:
        return {"ok": False, "events_checked": checked, "first_break": {"event_id": head.last_event_id, "problem": "Chain head does not match the latest event (possible truncation)."}}
    return {"ok": True, "events_checked": checked, "first_break": None, "head_hash": prev}
