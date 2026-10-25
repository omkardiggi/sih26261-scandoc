"""Encrypted file storage (AES-256-GCM at rest) with path-safe keys.

Originals, processed renders and evidence artefacts are stored under separate
prefixes; originals are written once and never modified.
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .config import get_settings
from .security.crypto import decrypt_bytes, encrypt_bytes

_KEY_RE = re.compile(r"^[a-z0-9_\-]+(/[a-z0-9_\-]+)*\.bin$")


class StorageError(Exception):
    pass


def _root() -> Path:
    root = get_settings().storage_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path_for(key: str) -> Path:
    if not _KEY_RE.match(key):
        raise StorageError("Invalid storage key")
    root = _root().resolve()
    p = (root / key).resolve()
    if root not in p.parents:
        raise StorageError("Storage key escapes storage root")
    return p


def _in_database() -> bool:
    return get_settings().storage_backend == "database"


def _db_put(key: str, blob: bytes) -> None:
    from .db import session_scope
    from .models import StoredObject

    with session_scope() as db:  # its own transaction, like a file write
        db.add(StoredObject(key=key, data=blob, size=len(blob)))


def _db_get(key: str) -> bytes | None:
    from .db import session_scope
    from .models import StoredObject

    with session_scope() as db:
        row = db.get(StoredObject, key)
        return bytes(row.data) if row is not None else None


def put(data: bytes, prefix: str) -> str:
    """Encrypt and store bytes; returns the new storage key."""
    if not re.fullmatch(r"[a-z0-9_\-]+(/[a-z0-9_\-]+)*", prefix):
        raise StorageError("Invalid storage prefix")
    now = datetime.now(UTC)
    key = f"{prefix}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}.bin"
    if not _KEY_RE.match(key):
        raise StorageError("Invalid storage key")
    if _in_database():
        _db_put(key, encrypt_bytes(data, aad=key.encode("utf-8")))
        return key
    path = _path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = encrypt_bytes(data, aad=key.encode("utf-8"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return key


def get(key: str) -> bytes:
    if _in_database():
        if not _KEY_RE.match(key):
            raise StorageError("Invalid storage key")
        blob = _db_get(key)
        if blob is None:
            raise StorageError("Stored file is missing")
        return decrypt_bytes(blob, aad=key.encode("utf-8"))
    path = _path_for(key)
    if not path.exists():
        raise StorageError("Stored file is missing")
    return decrypt_bytes(path.read_bytes(), aad=key.encode("utf-8"))


def exists(key: str) -> bool:
    if _in_database():
        return _KEY_RE.match(key) is not None and _db_get(key) is not None
    return _path_for(key).exists()


def delete(key: str) -> None:
    if _in_database():
        from .db import session_scope
        from .models import StoredObject

        with session_scope() as db:
            row = db.get(StoredObject, key)
            if row is not None:
                db.delete(row)
        return
    path = _path_for(key)
    if path.exists():
        path.unlink()


def _tmp_dir() -> Path:
    d = get_settings().storage_dir.parent / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def plaintext_tempfile(key: str | None = None, *, data: bytes | None = None, suffix: str = "") -> Iterator[Path]:
    """Decrypt to a private temporary file for tools that need a path (e.g. FFmpeg)."""
    payload = data if data is not None else get(key)  # type: ignore[arg-type]
    fd, name = tempfile.mkstemp(dir=_tmp_dir(), suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        yield Path(name)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


@contextmanager
def temp_directory() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(dir=_tmp_dir()) as d:
        yield Path(d)
