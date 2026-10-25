"""In-app notifications for the signed-in officer."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import serializers as ser
from ..db import get_db
from ..deps import Auth, require_officer
from ..models import Notification, utcnow
from .common import get_or_404

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(unread_only: bool = False, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    stmt = select(Notification).where(Notification.officer_id == auth.officer.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = db.execute(stmt.order_by(Notification.created_at.desc()).limit(50)).scalars().all()
    unread = db.execute(select(func.count()).select_from(Notification).where(Notification.officer_id == auth.officer.id,
                                                                            Notification.read_at.is_(None))).scalar_one()
    return {"unread": unread, "items": [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link,
                                         "created_at": ser.iso(n.created_at), "read_at": ser.iso(n.read_at)} for n in rows]}


@router.post("/{notification_id}/read")
def mark_read(notification_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    n = get_or_404(db, Notification, notification_id, "Notification")
    if n.officer_id == auth.officer.id and n.read_at is None:
        n.read_at = utcnow()
        db.commit()
    return {"ok": True}


@router.post("/read-all")
def mark_all_read(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    db.execute(update(Notification).where(Notification.officer_id == auth.officer.id, Notification.read_at.is_(None)).values(read_at=utcnow()))
    db.commit()
    return {"ok": True}
