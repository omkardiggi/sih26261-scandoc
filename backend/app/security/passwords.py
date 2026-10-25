"""Password hashing (Argon2id) and password policy."""

from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)

# Pre-computed hash used to equalise timing when an account does not exist.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")

MIN_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, *, email: str = "", name: str = "") -> list[str]:
    """Return a list of policy violations (empty list = acceptable)."""
    problems: list[str] = []
    if len(password) < MIN_LENGTH:
        problems.append(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > 256:
        problems.append("Use at most 256 characters.")
    classes = sum(
        bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")
    )
    if classes < 3:
        problems.append("Combine at least three of: lowercase, uppercase, digits, symbols.")
    lowered = password.lower()
    local = email.split("@")[0].lower() if email else ""
    if local and len(local) >= 4 and local in lowered:
        problems.append("Do not include your email address.")
    for part in name.lower().split():
        if len(part) >= 4 and part in lowered:
            problems.append("Do not include your name.")
            break
    if lowered in {"password1234", "aicteofficer1", "welcome12345", "qwerty123456"}:
        problems.append("Choose a less common password.")
    return problems
