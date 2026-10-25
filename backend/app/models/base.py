from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

OFFICER_ROLE = "AICTE_OFFICER"


def uuid_str() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Naive UTC timestamp (stored consistently across SQLite/PostgreSQL)."""
    return datetime.now(UTC).replace(tzinfo=None)


def pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=uuid_str)


def created_col() -> Mapped[datetime]:
    return mapped_column(DateTime, default=utcnow, nullable=False)


def updated_col() -> Mapped[datetime]:
    return mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
