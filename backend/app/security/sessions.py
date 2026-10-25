"""Server-side sessions stored in the database (only token hashes are persisted)."""

from __future__ import annotations

import hmac
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Officer, OfficerSession, utcnow
from .crypto import new_token, token_hash

SESSION_COOKIE = "ivp_session"
CSRF_COOKIE = "ivp_csrf"
CSRF_HEADER = "x-csrf-token"


def client_ip(request: Request) -> str | None:
    # Only trust the direct peer; configure a reverse proxy to set it correctly in production.
    return request.client.host if request.client else None


def create_session(db: Session, officer: Officer, request: Request, *, mfa_verified: bool) -> tuple[str, OfficerSession]:
    s = get_settings()
    token = new_token(32)
    now = utcnow()
    sess = OfficerSession(
        officer_id=officer.id,
        token_hash=token_hash(token),
        csrf_token=new_token(24),
        mfa_verified=mfa_verified,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=s.session_absolute_hours),
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )
    db.add(sess)
    db.flush()
    return token, sess


def set_session_cookies(response: Response, token: str, sess: OfficerSession) -> None:
    s = get_settings()
    max_age = s.session_absolute_hours * 3600
    response.set_cookie(
        SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=s.secure_cookies, samesite="strict", path="/"
    )
    # Readable by the SPA so it can echo the value in the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE, sess.csrf_token, max_age=max_age, httponly=False, secure=s.secure_cookies, samesite="strict", path="/"
    )


def clear_session_cookies(response: Response) -> None:
    s = get_settings()
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=s.secure_cookies, samesite="strict")


def load_session(db: Session, token: str | None) -> tuple[OfficerSession, Officer] | None:
    if not token or len(token) > 200:
        return None
    sess = db.execute(select(OfficerSession).where(OfficerSession.token_hash == token_hash(token))).scalar_one_or_none()
    if sess is None or sess.revoked_at is not None:
        return None
    now = utcnow()
    idle = timedelta(minutes=get_settings().session_idle_minutes)
    if sess.expires_at <= now or sess.last_seen_at + idle <= now:
        return None
    officer = db.get(Officer, sess.officer_id)
    if officer is None or not officer.is_active:
        return None
    return sess, officer


def touch(db: Session, sess: OfficerSession) -> None:
    now = utcnow()
    if (now - sess.last_seen_at).total_seconds() >= 30:
        db.execute(update(OfficerSession).where(OfficerSession.id == sess.id).values(last_seen_at=now))
        db.commit()


def csrf_valid(sess: OfficerSession, header_value: str | None) -> bool:
    return bool(header_value) and hmac.compare_digest(sess.csrf_token, header_value or "")


def revoke_all(db: Session, officer_id: str, *, except_session_id: str | None = None) -> int:
    stmt = update(OfficerSession).where(OfficerSession.officer_id == officer_id, OfficerSession.revoked_at.is_(None))
    if except_session_id:
        stmt = stmt.where(OfficerSession.id != except_session_id)
    return db.execute(stmt.values(revoked_at=utcnow())).rowcount or 0
