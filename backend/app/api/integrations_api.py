"""Integration configuration and real connection tests (managed from contextual settings panels)."""

from __future__ import annotations

import re
import smtplib
import ssl
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from .. import serializers as ser
from ..config import get_settings
from ..db import get_db
from ..deps import Auth, require_officer
from ..integrations import clamav, http
from ..integrations.official import test_rest
from ..integrations.secrets import ENV_NAME_RE
from ..models import Integration, IntegrationTestLog, utcnow
from ..security.crypto import encrypt_secret
from .common import get_or_404

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

AUTH_METHODS = {"none", "bearer", "api_key_header", "basic", "api_key_query"}


@router.get("")
def list_integrations(kind: str | None = None, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    stmt = select(Integration)
    if not get_settings().expose_model_admin:
        stmt = stmt.where(Integration.kind != "vision_model_provider")
    if kind:
        stmt = stmt.where(Integration.kind == kind)
    rows = db.execute(stmt.order_by(Integration.kind, Integration.name)).scalars().all()
    s = get_settings()
    out = []
    for i in rows:
        item = ser.integration(i)
        if i.adapter == "clamav":
            item["environment_configured"] = bool(s.clamd_host)
        elif i.adapter == "smtp":
            item["environment_configured"] = bool(s.smtp_host and s.smtp_from)
        out.append(item)
    return {"items": out}


class IntegrationIn(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    purpose: str = Field(min_length=5, max_length=4000)
    provider: str | None = Field(default=None, max_length=200)
    source_owner: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    auth_method: str = "none"
    auth_header_name: str | None = Field(default=None, max_length=100)
    secret_env_var: str | None = Field(default=None, max_length=100)
    config: dict = Field(default_factory=dict)
    claim_types: list[str] = Field(default_factory=list)
    documentation_url: str | None = Field(default=None, max_length=500)
    required_permissions: str | None = Field(default=None, max_length=4000)
    coverage_notes: str | None = Field(default=None, max_length=4000)
    rate_limit_notes: str | None = Field(default=None, max_length=4000)
    privacy_notes: str | None = Field(default=None, max_length=4000)
    enabled: bool = False


def _validate(body: IntegrationIn, existing: Integration | None = None) -> None:
    if body.auth_method not in AUTH_METHODS:
        raise HTTPException(422, "Unknown authentication method.")
    if body.base_url:
        try:
            http.validate_destination(body.base_url)
        except http.OutboundBlocked as exc:
            raise HTTPException(422, f"Base URL not permitted: {exc}") from exc
        except http.OutboundError as exc:
            raise HTTPException(422, f"Base URL host could not be resolved: {exc}") from exc
    if body.secret_env_var and not ENV_NAME_RE.match(body.secret_env_var):
        raise HTTPException(422, "Environment variable names must be UPPER_SNAKE_CASE.")
    if body.documentation_url and not body.documentation_url.startswith("https://"):
        raise HTTPException(422, "Documentation URL must use HTTPS.")
    for ct in body.claim_types:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,59}", ct):
            raise HTTPException(422, f"Invalid claim type '{ct}'.")


@router.post("", status_code=201)
def create_integration(body: IntegrationIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """Add an official-record source (generic REST adapter configured from provider documentation)."""
    _validate(body)
    key = re.sub(r"[^a-z0-9]+", "_", body.name.lower()).strip("_")[:60]
    if db.execute(select(Integration).where(Integration.key == key)).first():
        key = f"{key}_{int(time.time())}"
    i = Integration(key=key, kind="official_record", adapter="generic_rest", updated_by_id=auth.officer.id,
                    connection_status="untested", **body.model_dump())
    db.add(i)
    db.flush()
    audit.record(db, "integration.created", officer=auth.officer, entity_type="integration", entity_id=i.id,
                 after=ser.integration(i), ctx=auth.ctx)
    db.commit()
    return ser.integration(i)


@router.patch("/{integration_id}")
def update_integration(integration_id: str, body: IntegrationIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    i = get_or_404(db, Integration, integration_id, "Integration")
    _validate(body, i)
    before = ser.integration(i)
    fields = body.model_dump()
    if i.builtin:
        # Built-in entries keep their identity; adapter-specific config is still editable.
        for locked in ("name",):
            fields.pop(locked, None)
    material = any(fields.get(k) != getattr(i, k) for k in ("base_url", "auth_method", "auth_header_name", "secret_env_var", "config"))
    for k, v in fields.items():
        setattr(i, k, v)
    if material:
        i.connection_status = "untested"  # configuration changed: it must be tested again
    i.updated_by_id = auth.officer.id
    audit.record(db, "integration.updated", officer=auth.officer, entity_type="integration", entity_id=i.id,
                 before=before, after=ser.integration(i), ctx=auth.ctx)
    db.commit()
    return ser.integration(i)


class SecretIn(BaseModel):
    value: str = Field(min_length=4, max_length=4000)


@router.put("/{integration_id}/secret")
def set_secret(integration_id: str, body: SecretIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """Write-only: the value is encrypted at rest and never returned by any endpoint."""
    i = get_or_404(db, Integration, integration_id, "Integration")
    i.secret_enc = encrypt_secret(body.value.strip())
    i.connection_status = "untested"
    audit.record(db, "integration.secret_set", officer=auth.officer, entity_type="integration", entity_id=i.id,
                 summary="Credential stored (encrypted)", ctx=auth.ctx)
    db.commit()
    return ser.integration(i)


@router.delete("/{integration_id}/secret")
def clear_secret(integration_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    i = get_or_404(db, Integration, integration_id, "Integration")
    i.secret_enc = None
    i.connection_status = "untested"
    audit.record(db, "integration.secret_cleared", officer=auth.officer, entity_type="integration", entity_id=i.id, ctx=auth.ctx)
    db.commit()
    return ser.integration(i)


def _smtp_test() -> tuple[bool, str]:
    s = get_settings()
    if not (s.smtp_host and s.smtp_from):
        return False, "SMTP_HOST and SMTP_FROM are not configured."
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            smtp.ehlo()
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password)
            smtp.noop()
        return True, "Connected and authenticated with the SMTP server (no message sent)."
    except Exception as exc:  # noqa: BLE001
        return False, f"SMTP connection failed: {type(exc).__name__}"


@router.post("/{integration_id}/test")
def test_integration(integration_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    i = get_or_404(db, Integration, integration_id, "Integration")
    started = time.monotonic()
    http_status, auth_failed = None, False
    if i.adapter == "generic_rest":
        ok, message, http_status, _d, auth_failed = test_rest(i)
    elif i.adapter == "clamav":
        ok, message = clamav.ping()
    elif i.adapter == "smtp":
        ok, message = _smtp_test()
    elif i.adapter == "aicte_qr_status":
        ok, message = False, ("This source is reached through the QR link printed on each letter, so it is tested on every use. "
                              f"Last successful use: {ser.iso(i.last_success_at) or 'never'}.")
    elif i.adapter == "roboflow":
        ok, message = False, "Test each configured model from Inspection & Analysis → Models (a real inference request)."
    else:
        ok, message = False, "No test is available for this adapter."
    duration = int((time.monotonic() - started) * 1000)
    now = utcnow()
    i.last_test_at = now
    if i.adapter in {"generic_rest", "clamav", "smtp"}:
        if ok:
            i.connection_status, i.last_success_at = "operational", now
        else:
            i.connection_status = "auth_failed" if auth_failed else "failed"
            i.last_failure_at, i.last_failure_reason = now, message[:500]
    log = IntegrationTestLog(integration_id=i.id, ok=ok, status=i.connection_status, http_status=http_status, message=message,
                             duration_ms=duration, tested_by_id=auth.officer.id)
    db.add(log)
    audit.record(db, "integration.tested", officer=auth.officer, entity_type="integration", entity_id=i.id,
                 outcome="operational" if ok else "failed", external_source=i.name, summary=message[:500], ctx=auth.ctx)
    db.commit()
    return {"ok": ok, "message": message, "http_status": http_status, "duration_ms": duration, "integration": ser.integration(i)}


@router.get("/{integration_id}/tests")
def test_history(integration_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, Integration, integration_id, "Integration")
    rows = db.execute(select(IntegrationTestLog).where(IntegrationTestLog.integration_id == integration_id)
                      .order_by(IntegrationTestLog.tested_at.desc()).limit(50)).scalars().all()
    return {"items": [{"id": r.id, "ok": r.ok, "status": r.status, "http_status": r.http_status, "message": r.message,
                       "duration_ms": r.duration_ms, "tested_at": ser.iso(r.tested_at), "model_config_id": r.model_config_id} for r in rows]}
