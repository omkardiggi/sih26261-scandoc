"""Authentication: login/logout, MFA, password reset, invitations, sessions."""

from __future__ import annotations

import io
import re

import segno
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from .. import serializers as ser
from ..config import get_settings
from ..db import get_db
from ..deps import Auth, request_context, require_officer, require_partial_session, require_same_origin
from ..mailer import mail_transport, send_mail
from ..models import Officer, OfficerSession, utcnow
from ..security import totp
from ..security.crypto import decrypt_secret, encrypt_secret
from ..security.passwords import hash_password, needs_rehash, password_problems, verify_password
from ..security.ratelimit import login_blocked, record_attempt, sensitive_limiter
from ..security.sessions import (
    clear_session_cookies,
    client_ip,
    create_session,
    revoke_all,
    set_session_cookies,
)
from ..services.officers import ProvisioningError, create_officer, find_valid_token, issue_token, link_for

router = APIRouter(prefix="/api/auth", tags=["auth"])

GENERIC_LOGIN_ERROR = "Invalid email or password."


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


class CodeIn(BaseModel):
    code: str = Field(max_length=12)


class EmailIn(BaseModel):
    email: str = Field(max_length=320)


class TokenPasswordIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class DisableMfaIn(BaseModel):
    password: str = Field(max_length=256)
    code: str = Field(max_length=12)


def _me_payload(auth: Auth) -> dict:
    return {
        "officer": ser.officer(auth.officer, full=True),
        "mfa_pending": auth.officer.mfa_enabled and not auth.session.mfa_verified,
        "session": {
            "id": auth.session.id,
            "created_at": ser.iso(auth.session.created_at),
            "expires_at": ser.iso(auth.session.expires_at),
            "idle_timeout_minutes": get_settings().session_idle_minutes,
        },
    }


@router.get("/me")
def me(auth: Auth = Depends(require_partial_session)) -> dict:
    return _me_payload(auth)


@router.post("/login", dependencies=[Depends(require_same_origin)])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    email = body.email.strip().lower()
    ip = client_ip(request)
    blocked = login_blocked(db, email, ip)
    if blocked:
        record_attempt(db, email, ip, False, blocked)
        db.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-in attempts. Please wait and try again later.")

    officer = db.execute(select(Officer).where(Officer.email == email)).scalar_one_or_none()
    ok = verify_password(officer.password_hash if officer else None, body.password)
    if not ok or officer is None or not officer.is_active:
        record_attempt(db, email, ip, False, "bad_credentials" if officer else "unknown_account")
        if officer is not None:
            officer.failed_logins += 1
            audit.record(db, "auth.login_failed", officer=officer, entity_type="officer", entity_id=officer.id,
                         summary="Failed sign-in attempt", ctx=request_context(request))
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_LOGIN_ERROR)

    if needs_rehash(officer.password_hash or ""):
        officer.password_hash = hash_password(body.password)
    record_attempt(db, email, ip, True)
    officer.failed_logins = 0
    token, sess = create_session(db, officer, request, mfa_verified=not officer.mfa_enabled)
    if not officer.mfa_enabled:
        officer.last_login_at = utcnow()
        audit.record(db, "auth.login", officer=officer, entity_type="officer", entity_id=officer.id,
                     summary="Signed in", ctx=request_context(request))
    db.commit()
    set_session_cookies(response, token, sess)
    return {"mfa_required": officer.mfa_enabled, "officer": ser.officer(officer, full=True)}


class SignupIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    designation: str | None = Field(default=None, max_length=200)


EMAIL_RE = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")


@router.get("/signup-config")
def signup_config() -> dict:
    s = get_settings()
    return {"enabled": s.signup_enabled, "allowed_domains": [d.strip().lower() for d in s.signup_allowed_domains.split(",") if d.strip()]}


@router.post("/signup", dependencies=[Depends(require_same_origin)])
def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    """Self-service account creation: any valid email (optionally limited to configured domains) becomes an officer account."""
    cfg = signup_config()
    if not cfg["enabled"]:
        raise HTTPException(403, "Sign-up is disabled on this server. Ask an existing officer for an invitation.")
    ip = client_ip(request)
    if not sensitive_limiter.allow(f"signup:{ip}"):
        raise HTTPException(429, "Too many sign-up attempts. Please wait and try again later.")
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    if cfg["allowed_domains"] and email.rsplit("@", 1)[1] not in cfg["allowed_domains"]:
        raise HTTPException(422, f"Sign-up is limited to these email domains: {', '.join(cfg['allowed_domains'])}.")
    if db.execute(select(Officer).where(Officer.email == email)).scalar_one_or_none():
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    try:
        officer = create_officer(db, email=email, full_name=body.full_name, designation=body.designation, password=body.password)
    except ProvisioningError as exc:
        raise HTTPException(422, str(exc)) from exc
    officer.last_login_at = utcnow()
    audit.record(db, "auth.signup", officer=officer, entity_type="officer", entity_id=officer.id,
                 summary="Created an account by self-service sign-up", ctx=request_context(request))
    token, sess = create_session(db, officer, request, mfa_verified=True)
    db.commit()
    set_session_cookies(response, token, sess)
    return {"officer": ser.officer(officer, full=True)}


@router.post("/mfa/verify")
def mfa_verify(body: CodeIn, request: Request, auth: Auth = Depends(require_partial_session), db: Session = Depends(get_db)) -> dict:
    officer = db.get(Officer, auth.officer.id)
    if not officer.mfa_enabled or not officer.mfa_secret_enc:
        raise HTTPException(400, "Multifactor authentication is not enabled for this account.")
    if not sensitive_limiter.allow(f"mfa:{auth.session.id}"):
        raise HTTPException(429, "Too many verification attempts. Sign in again later.")
    counter = totp.verify_totp(decrypt_secret(officer.mfa_secret_enc), body.code)
    if counter is None or (officer.mfa_last_counter is not None and counter <= officer.mfa_last_counter):
        audit.record(db, "auth.mfa_failed", officer=officer, entity_type="officer", entity_id=officer.id, ctx=request_context(request))
        db.commit()
        raise HTTPException(401, "The verification code is not valid.")
    officer.mfa_last_counter = counter
    sess = db.get(OfficerSession, auth.session.id)
    sess.mfa_verified = True
    officer.last_login_at = utcnow()
    audit.record(db, "auth.login", officer=officer, entity_type="officer", entity_id=officer.id,
                 summary="Signed in with multifactor verification", ctx=request_context(request))
    db.commit()
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, auth: Auth = Depends(require_partial_session), db: Session = Depends(get_db)) -> dict:
    sess = db.get(OfficerSession, auth.session.id)
    sess.revoked_at = utcnow()
    audit.record(db, "auth.logout", officer=auth.officer, entity_type="officer", entity_id=auth.officer.id, ctx=request_context(request))
    db.commit()
    clear_session_cookies(response)
    return {"ok": True}


@router.post("/password-reset/request", status_code=202, dependencies=[Depends(require_same_origin)])
def request_password_reset(body: EmailIn, request: Request, db: Session = Depends(get_db)) -> dict:
    ip = client_ip(request) or "unknown"
    if not sensitive_limiter.allow(f"reset:{ip}"):
        raise HTTPException(429, "Too many requests. Please try again later.")
    email = body.email.strip().lower()
    officer = db.execute(select(Officer).where(Officer.email == email)).scalar_one_or_none()
    if officer is not None and officer.is_active and officer.password_hash:
        raw = issue_token(db, officer, "password_reset")
        sent = send_mail(
            officer.email,
            "Password reset request",
            "A password reset was requested for your officer account.\n\n"
            f"Open this link within 1 hour to choose a new password:\n{link_for('password_reset', raw)}\n\n"
            "If you did not request this, you can ignore this message.",
        )
        audit.record(db, "auth.password_reset_requested", officer=officer, entity_type="officer", entity_id=officer.id,
                     outcome="email_sent" if sent else "email_not_sent", ctx=request_context(request))
        db.commit()
    # Identical response whether or not the account exists.
    return {
        "message": "If an active account exists for this address, password reset instructions have been sent.",
        "delivery": "email" if mail_transport() == "smtp" else ("development_outbox" if mail_transport() == "dev_outbox" else "contact_operator"),
    }


@router.get("/token-info", dependencies=[Depends(require_same_origin)])
def token_info(token: str, purpose: str, db: Session = Depends(get_db)) -> dict:
    if purpose not in {"invite", "password_reset"}:
        raise HTTPException(400, "Unknown token purpose")
    found = find_valid_token(db, token, purpose)
    if not found:
        raise HTTPException(404, "This link is invalid or has expired.")
    _tok, officer = found
    return {"email": officer.email, "full_name": officer.full_name, "expires_at": ser.iso(_tok.expires_at)}


def _consume_and_set_password(db: Session, request: Request, body: TokenPasswordIn, purpose: str) -> None:
    found = find_valid_token(db, body.token, purpose)
    if not found:
        raise HTTPException(400, "This link is invalid or has expired.")
    tok, officer = found
    problems = password_problems(body.password, email=officer.email, name=officer.full_name)
    if problems:
        raise HTTPException(422, " ".join(problems))
    tok.used_at = utcnow()
    officer.password_hash = hash_password(body.password)
    officer.password_changed_at = utcnow()
    officer.failed_logins = 0
    revoke_all(db, officer.id)
    audit.record(db, "auth.invite_accepted" if purpose == "invite" else "auth.password_reset_completed",
                 officer=officer, entity_type="officer", entity_id=officer.id, ctx=request_context(request))
    db.commit()


@router.post("/password-reset/confirm", dependencies=[Depends(require_same_origin)])
def confirm_password_reset(body: TokenPasswordIn, request: Request, db: Session = Depends(get_db)) -> dict:
    _consume_and_set_password(db, request, body, "password_reset")
    return {"ok": True, "message": "Your password has been updated. Sign in with the new password."}


@router.post("/invite/accept", dependencies=[Depends(require_same_origin)])
def accept_invite(body: TokenPasswordIn, request: Request, db: Session = Depends(get_db)) -> dict:
    _consume_and_set_password(db, request, body, "invite")
    return {"ok": True, "message": "Your account is ready. Sign in to continue."}


@router.post("/password")
def change_password(body: ChangePasswordIn, request: Request, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    officer = db.get(Officer, auth.officer.id)
    if not verify_password(officer.password_hash, body.current_password):
        raise HTTPException(400, "The current password is incorrect.")
    problems = password_problems(body.new_password, email=officer.email, name=officer.full_name)
    if problems:
        raise HTTPException(422, " ".join(problems))
    officer.password_hash = hash_password(body.new_password)
    officer.password_changed_at = utcnow()
    revoked = revoke_all(db, officer.id, except_session_id=auth.session.id)
    audit.record(db, "auth.password_changed", officer=officer, entity_type="officer", entity_id=officer.id,
                 after={"other_sessions_revoked": revoked}, ctx=request_context(request))
    db.commit()
    return {"ok": True, "other_sessions_revoked": revoked}


@router.post("/mfa/setup")
def mfa_setup(request: Request, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    officer = db.get(Officer, auth.officer.id)
    if officer.mfa_enabled:
        raise HTTPException(400, "Multifactor authentication is already enabled.")
    secret = totp.generate_secret()
    officer.mfa_pending_secret_enc = encrypt_secret(secret)
    db.commit()
    uri = totp.provisioning_uri(secret, officer.email, get_settings().app_name)
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False, svgns=True)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": buf.getvalue().decode("utf-8")}


@router.post("/mfa/enable")
def mfa_enable(body: CodeIn, request: Request, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    officer = db.get(Officer, auth.officer.id)
    if not officer.mfa_pending_secret_enc:
        raise HTTPException(400, "Start multifactor setup first.")
    secret = decrypt_secret(officer.mfa_pending_secret_enc)
    counter = totp.verify_totp(secret, body.code)
    if counter is None:
        raise HTTPException(400, "The verification code is not valid. Check your authenticator app's clock.")
    officer.mfa_secret_enc = officer.mfa_pending_secret_enc
    officer.mfa_pending_secret_enc = None
    officer.mfa_enabled = True
    officer.mfa_last_counter = counter
    sess = db.get(OfficerSession, auth.session.id)
    sess.mfa_verified = True
    audit.record(db, "auth.mfa_enabled", officer=officer, entity_type="officer", entity_id=officer.id, ctx=request_context(request))
    db.commit()
    return {"ok": True}


@router.post("/mfa/disable")
def mfa_disable(body: DisableMfaIn, request: Request, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    officer = db.get(Officer, auth.officer.id)
    if not officer.mfa_enabled:
        raise HTTPException(400, "Multifactor authentication is not enabled.")
    if not verify_password(officer.password_hash, body.password):
        raise HTTPException(400, "The password is incorrect.")
    if totp.verify_totp(decrypt_secret(officer.mfa_secret_enc), body.code) is None:
        raise HTTPException(400, "The verification code is not valid.")
    officer.mfa_enabled = False
    officer.mfa_secret_enc = None
    officer.mfa_last_counter = None
    audit.record(db, "auth.mfa_disabled", officer=officer, entity_type="officer", entity_id=officer.id, ctx=request_context(request))
    db.commit()
    return {"ok": True}


@router.get("/sessions")
def list_sessions(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(OfficerSession)
        .where(OfficerSession.officer_id == auth.officer.id, OfficerSession.revoked_at.is_(None), OfficerSession.expires_at > utcnow())
        .order_by(OfficerSession.last_seen_at.desc())
    ).scalars().all()
    return {
        "items": [
            {
                "id": s.id,
                "current": s.id == auth.session.id,
                "created_at": ser.iso(s.created_at),
                "last_seen_at": ser.iso(s.last_seen_at),
                "expires_at": ser.iso(s.expires_at),
                "ip": s.ip,
                "user_agent": s.user_agent,
            }
            for s in rows
        ]
    }


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, request: Request, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    sess = db.get(OfficerSession, session_id)
    if sess is None or sess.officer_id != auth.officer.id:
        raise HTTPException(404, "Session not found")
    sess.revoked_at = utcnow()
    audit.record(db, "auth.session_revoked", officer=auth.officer, entity_type="session", entity_id=session_id, ctx=request_context(request))
    db.commit()
    return {"ok": True}
