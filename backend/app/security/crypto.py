"""Symmetric encryption (AES-256-GCM), hashing and HMAC signing helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import get_settings

_MAGIC = b"IVP1"  # file format marker for encrypted blobs


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def token_hash(token: str) -> str:
    """Hash for storing bearer tokens (session/reset/invite) at rest."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def encrypt_bytes(plaintext: bytes, *, key_name: str = "storage", aad: bytes = b"") -> bytes:
    key = get_settings().key_bytes(key_name)  # type: ignore[arg-type]
    nonce = os.urandom(12)
    return _MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, aad or None)


def decrypt_bytes(blob: bytes, *, key_name: str = "storage", aad: bytes = b"") -> bytes:
    if not blob.startswith(_MAGIC):
        raise ValueError("Encrypted blob has an unknown format")
    key = get_settings().key_bytes(key_name)  # type: ignore[arg-type]
    nonce, ct = blob[4:16], blob[16:]
    return AESGCM(key).decrypt(nonce, ct, aad or None)


def encrypt_secret(value: str) -> str:
    return base64.b64encode(encrypt_bytes(value.encode("utf-8"), key_name="secrets")).decode("ascii")


def decrypt_secret(value: str) -> str:
    return decrypt_bytes(base64.b64decode(value), key_name="secrets").decode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_payload(payload: dict, ttl_seconds: int) -> str:
    """Create a short-lived HMAC-signed token (used for file-access links)."""
    body = dict(payload, exp=int(time.time()) + ttl_seconds)
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(get_settings().key_bytes("signing"), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(sig)}"


def verify_signed_payload(token: str) -> dict | None:
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = _unb64(raw_b64)
        expected = hmac.new(get_settings().key_bytes("signing"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig_b64)):
            return None
        body = json.loads(raw)
        if int(body.get("exp", 0)) < int(time.time()):
            return None
        return body
    except Exception:  # noqa: BLE001 - any malformed token is simply invalid
        return None


def generate_key_b64() -> str:
    return _b64(os.urandom(32))
