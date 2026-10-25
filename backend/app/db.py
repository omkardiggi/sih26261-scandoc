"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict = {"pool_pre_ping": True}
        if not url.startswith("sqlite"):
            kwargs["pool_recycle"] = 240  # hosted Postgres closes idle connections; replace them before that happens
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
            if url.startswith("sqlite:///"):
                from pathlib import Path

                Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            _configure_sqlite(_engine)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def reset_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background work and scripts."""
    db = session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def is_sqlite() -> bool:
    return get_settings().database_url.startswith("sqlite")


@event.listens_for(Session, "do_orm_execute")
def _hide_removed_documents(state) -> None:
    """Documents an officer removed are left out of every query (the record and its audit trail are kept).

    Pass ``execution_options(include_removed=True)`` to see them, e.g. for the audit view or to undo a removal.
    """
    if state.is_select and not state.is_column_load and not state.is_relationship_load \
            and not state.execution_options.get("include_removed", False):
        from sqlalchemy.orm import with_loader_criteria

        from .models import Document

        state.statement = state.statement.options(
            with_loader_criteria(Document, lambda cls: cls.status != "removed", include_aliases=True))
