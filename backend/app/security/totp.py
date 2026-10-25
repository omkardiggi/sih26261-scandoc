"""RFC 6238 time-based one-time passwords for optional multifactor auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote


def generate_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _code(secret_b32: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def totp_now(secret_b32: str, at: float | None = None, step: int = 30) -> str:
    return _code(secret_b32, int((at if at is not None else time.time()) // step))


def verify_totp(secret_b32: str, code: str, *, window: int = 1, at: float | None = None) -> int | None:
    """Return the matched time-step counter, or None. Callers must reject reuse of a counter."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return None
    now = int((at if at is not None else time.time()) // 30)
    for delta in range(-window, window + 1):
        if hmac.compare_digest(_code(secret_b32, now + delta), code):
            return now + delta
    return None


def provisioning_uri(secret_b32: str, account: str, issuer: str) -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret_b32}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )
