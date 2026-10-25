"""Request dependencies: authentication, CSRF enforcement, audit context."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .audit import RequestContext
from .config import get_settings
from .db import get_db
from .models import Officer, OfficerSession
from .security.sessions import CSRF_HEADER, SESSION_COOKIE, client_ip, csrf_valid, load_session, touch

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Auth:
    officer: Officer
    session: OfficerSession
    ctx: RequestContext


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        # Same-origin fetches may omit Origin; SameSite=Strict + CSRF token still apply.
        return True
    allowed = set(get_settings().origins)
    allowed.add(str(request.base_url).rstrip("/"))
    return origin.rstrip("/") in allowed


def request_context(request: Request) -> RequestContext:
    return RequestContext(ip=client_ip(request), request_id=getattr(request.state, "request_id", None))


def _authenticate(request: Request, db: Session, *, allow_mfa_pending: bool) -> Auth:
    loaded = load_session(db, request.cookies.get(SESSION_COOKIE))
    if loaded is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    sess, officer = loaded
    if officer.mfa_enabled and not sess.mfa_verified and not allow_mfa_pending:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Multifactor verification required")
    if request.method not in SAFE_METHODS:
        if not _origin_allowed(request):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Request origin not allowed")
        if not csrf_valid(sess, request.headers.get(CSRF_HEADER)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token")
    touch(db, sess)
    request.state.officer_id = officer.id
    return Auth(officer=officer, session=sess, ctx=request_context(request))


def require_officer(request: Request, db: Session = Depends(get_db)) -> Auth:
    """Every protected endpoint depends on this: server-side authorization for the single role."""
    return _authenticate(request, db, allow_mfa_pending=False)


def require_partial_session(request: Request, db: Session = Depends(get_db)) -> Auth:
    return _authenticate(request, db, allow_mfa_pending=True)


def require_same_origin(request: Request) -> None:
    if request.method not in SAFE_METHODS and not _origin_allowed(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Request origin not allowed")
