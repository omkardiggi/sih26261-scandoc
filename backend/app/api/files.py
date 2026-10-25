"""Restricted file access via short-lived signed links (session also required)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import audit, storage
from ..db import get_db
from ..deps import Auth, require_officer
from ..models import DocumentPage, DocumentVersion, EvidenceArtifact, MediaAsset, VideoFrame
from ..security.crypto import verify_signed_payload

router = APIRouter(prefix="/api/files", tags=["files"])

IMAGE_MIME = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "tiff": "image/tiff"}
VIDEO_MIME = {"mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
              "mkv": "video/x-matroska", "avi": "video/x-msvideo"}


def _resolve(db: Session, kind: str, id_: str) -> tuple[str, str, str | None, dict]:
    """Return (storage_key, mime, download_name, audit_context)."""
    if kind == "page":
        p = db.get(DocumentPage, id_)
        if p and p.render_key:
            return p.render_key, "image/png", None, {}
    elif kind == "artifact":
        a = db.get(EvidenceArtifact, id_)
        if a:
            return a.storage_key, a.mime, None, {}
    elif kind == "original":
        v = db.get(DocumentVersion, id_)
        if v:
            mime = "application/pdf" if v.detected_type == "pdf" else IMAGE_MIME.get(v.detected_type, "application/octet-stream")
            return v.storage_key, mime, v.original_filename, {"document_id": v.document_id, "version_id": v.id}
    elif kind == "frame":
        f = db.get(VideoFrame, id_)
        if f:
            return f.storage_key, "image/jpeg", None, {}
    elif kind == "media":
        m = db.get(MediaAsset, id_)
        if m:
            mime = IMAGE_MIME.get(m.detected_type) or VIDEO_MIME.get(m.detected_type, "application/octet-stream")
            return m.storage_key, mime, m.original_filename, {"media_id": m.id, "institution_id": m.institution_id}
    raise HTTPException(404, "File not found")


def _range_response(data: bytes, mime: str, headers: dict, range_header: str) -> Response | None:
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None
    size = len(data)
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
    else:
        start, end = max(0, size - int(m.group(2))), size - 1
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    return Response(content=data[start : end + 1], status_code=206, media_type=mime,
                    headers=headers | {"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes"})


@router.get("/{token}", response_model=None)
def get_file(token: str, request: Request, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> Response:
    payload = verify_signed_payload(token)
    if payload is None or payload.get("o") != auth.officer.id:
        raise HTTPException(403, "This file link is invalid or has expired.")
    key, mime, name, ctx = _resolve(db, payload["k"], payload["i"])
    data = storage.get(key)
    headers = {
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; sandbox",
        "X-Content-Type-Options": "nosniff",
    }
    if payload["k"] == "original" or payload.get("d"):
        safe = (name or "download").replace('"', "")
        headers["Content-Disposition"] = f'attachment; filename="{safe}"'
        audit.record(db, "file.downloaded", officer=auth.officer, entity_type=payload["k"], entity_id=payload["i"],
                     institution_id=ctx.get("institution_id"), ctx=auth.ctx, after=ctx)
        db.commit()
        if payload["k"] == "original" and mime == "application/pdf":
            mime = "application/octet-stream"  # never render untrusted PDFs inline in the browser
    else:
        headers["Content-Disposition"] = "inline"
        if mime.startswith("video/") and request.headers.get("range"):
            ranged = _range_response(data, mime, headers, request.headers["range"])
            if ranged is not None:
                return ranged
    headers["Accept-Ranges"] = "bytes" if mime.startswith("video/") else "none"
    return Response(content=data, media_type=mime, headers=headers)
