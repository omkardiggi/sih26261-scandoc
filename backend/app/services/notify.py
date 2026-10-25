"""In-app notifications."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Notification, Officer


def notify(db: Session, officer_id: str, kind: str, title: str, body: str | None = None, link: str | None = None) -> None:
    db.add(Notification(officer_id=officer_id, kind=kind, title=title[:300], body=body, link=link))


def notify_all(db: Session, kind: str, title: str, body: str | None = None, link: str | None = None) -> None:
    for (oid,) in db.execute(select(Officer.id).where(Officer.is_active.is_(True))).all():
        notify(db, oid, kind, title, body, link)
