"""Shared helpers for API routers."""

from __future__ import annotations

from datetime import date, datetime, time

from fastapi import HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..security.crypto import sign_payload

FILE_LINK_TTL = 600


def get_or_404(db: Session, model, id_: str, label: str = "Record"):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{label} not found")
    return obj


def paginate(db: Session, stmt, page: int, page_size: int):
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = db.execute(stmt.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return rows, total


def page_params(page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=200)) -> tuple[int, int]:
    return page, page_size


def file_url(kind: str, id_: str, officer_id: str, *, download: bool = False) -> str:
    """Short-lived signed link; the file endpoint also requires an authenticated session."""
    token = sign_payload({"k": kind, "i": id_, "o": officer_id, "d": int(download)}, FILE_LINK_TTL)
    return f"/api/files/{token}"


def parse_date_param(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        d = date.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(422, f"Invalid date: {value}") from exc
    return datetime.combine(d, time.max if end else time.min)


def like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
