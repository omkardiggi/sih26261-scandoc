"""Officer provisioning and single-use account tokens."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AuthToken, Officer, utcnow
from ..security.crypto import new_token, token_hash
from ..security.passwords import hash_password, password_problems

TOKEN_TTL = {"invite": timedelta(hours=48), "password_reset": timedelta(hours=1)}


class ProvisioningError(ValueError):
    pass


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 320 or " " in email:
        raise ProvisioningError("Enter a valid email address.")
    return email


def create_officer(
    db: Session,
    *,
    email: str,
    full_name: str,
    designation: str | None = None,
    password: str | None = None,
    created_by: Officer | None = None,
) -> Officer:
    email = normalize_email(email)
    full_name = (full_name or "").strip()
    if not full_name:
        raise ProvisioningError("Full name is required.")
    if db.execute(select(Officer).where(Officer.email == email)).scalar_one_or_none():
        raise ProvisioningError("An officer account with this email already exists.")
    officer = Officer(
        email=email,
        full_name=full_name[:200],
        designation=(designation or "").strip()[:200] or None,
        created_by_id=created_by.id if created_by else None,
    )
    if password is not None:
        problems = password_problems(password, email=email, name=full_name)
        if problems:
            raise ProvisioningError(" ".join(problems))
        officer.password_hash = hash_password(password)
        officer.password_changed_at = utcnow()
    db.add(officer)
    db.flush()
    return officer


def issue_token(db: Session, officer: Officer, purpose: str, created_by: Officer | None = None) -> str:
    """Create a single-use token; previous unused tokens of the same purpose are invalidated."""
    db.execute(
        update(AuthToken)
        .where(AuthToken.officer_id == officer.id, AuthToken.purpose == purpose, AuthToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    raw = new_token(32)
    db.add(
        AuthToken(
            purpose=purpose,
            officer_id=officer.id,
            token_hash=token_hash(raw),
            expires_at=utcnow() + TOKEN_TTL[purpose],
            created_by_id=created_by.id if created_by else None,
        )
    )
    db.flush()
    return raw


def find_valid_token(db: Session, raw: str, purpose: str) -> tuple[AuthToken, Officer] | None:
    if not raw or len(raw) > 200:
        return None
    tok = db.execute(
        select(AuthToken).where(AuthToken.token_hash == token_hash(raw), AuthToken.purpose == purpose)
    ).scalar_one_or_none()
    if tok is None or tok.used_at is not None or tok.expires_at <= utcnow():
        return None
    officer = db.get(Officer, tok.officer_id)
    if officer is None or (purpose == "password_reset" and not officer.is_active):
        return None
    return tok, officer


def link_for(purpose: str, raw: str) -> str:
    base = get_settings().public_base_url.rstrip("/")
    path = "accept-invite" if purpose == "invite" else "reset-password"
    return f"{base}/{path}#token={raw}"
