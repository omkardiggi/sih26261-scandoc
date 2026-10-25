"""Application configuration loaded from environment variables / .env.

Secrets (encryption keys, API credentials) are only ever read from the
environment. Nothing in this module provides a usable default secret for
production: missing keys cause startup to fail outside development/test.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("APP_ENV_FILE", str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "Institution Verification Platform"
    public_base_url: str = "http://localhost:5173"
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000"

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'var' / 'app.db').as_posix()}"
    storage_dir: Path = BACKEND_DIR / "var" / "storage"
    mail_dir: Path = BACKEND_DIR / "var" / "mail"
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"

    # 32-byte keys, base64 encoded. Generate with: python -m app.cli generate-keys
    storage_encryption_key: str = ""
    secrets_encryption_key: str = ""
    signing_key: str = ""

    # Sessions
    session_absolute_hours: int = 8
    session_idle_minutes: int = 30
    cookie_secure: bool | None = None  # defaults to True in production

    # Login rate limiting
    login_max_failures: int = 5
    login_failure_window_minutes: int = 15
    login_lockout_minutes: int = 15
    login_ip_max_attempts: int = 30

    # Upload limits
    max_document_mb: int = 50
    max_image_mb: int = 25
    max_video_mb: int = 1024
    max_pdf_pages: int = 500
    max_image_pixels: int = 80_000_000

    # Processing
    ocr_render_dpi: int = 200
    preview_render_dpi: int = 110
    ocr_low_confidence: float = 0.75
    video_default_sample_seconds: float = 2.0
    video_max_frames: int = 600
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    # Where encrypted files live: "file" (storage_dir) or "database" (a table, for hosts without a persistent disk).
    storage_backend: Literal["file", "database"] = "file"

    # Background jobs. "worker": in-process worker threads. "request": queued work is run by short API calls the
    # web app makes while it is open (serverless hosts, where threads cannot outlive a request).
    jobs_mode: Literal["worker", "request"] = "worker"
    run_worker_in_process: bool = True
    worker_threads: int = 2
    job_lease_seconds: int = 900
    job_poll_seconds: float = 1.0

    # Optional external services (unconfigured => reported as unavailable)
    clamd_host: str = ""
    clamd_port: int = 3310
    malware_scan_required: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # Private mode: nothing leaves this server. Every outbound request is refused except to this machine / this network
    # (e.g. a self-hosted model server) and, if allowed below, official government lookups (.gov.in / .nic.in pages that
    # a document's QR code links to; only that address is requested, no document content is sent).
    private_mode: bool = False
    allow_official_lookups: bool = True

    # Outbound integration safety
    integration_timeout_seconds: float = 20.0
    workflow_timeout_seconds: float = 150.0  # multi-step vision workflows take ~30s per image
    workflow_max_response_bytes: int = 48 * 1024 * 1024  # responses carry rendered visualisation images
    integration_max_response_bytes: int = 10 * 1024 * 1024
    allow_private_integration_hosts: bool = False  # test-only escape hatch

    # Trust anchors for PDF signature validation (PEM/DER files, comma separated)
    pdf_trust_roots: str = "../data/trust_roots"

    # Inspection model configuration is operator-only (CLI); set true to expose the admin API.
    expose_model_admin: bool = False
    # Self-service sign-up on the login page. Allowed email domains, comma separated (e.g. "gmail.com,aicte-india.org");
    # empty = any email address.
    signup_enabled: bool = True
    signup_allowed_domains: str = ""

    # Document & image analysis workflow (Roboflow). The workspace and workflow ids are not secret; the API key is read from
    # ROBOFLOW_API_KEY. When a key is present the workflow is registered and enabled automatically at startup.
    roboflow_workspace: str = "omkars-workspace-qltv8"
    document_text_workflow: str = "document-field-extraction-1792870310871"
    audit_retention_days: int = 0  # 0 = retain indefinitely
    session_retention_days: int = 30
    login_attempt_retention_days: int = 90

    @field_validator("database_url", mode="after")
    @classmethod
    def _resolve_sqlite(cls, v: str) -> str:
        if v.startswith(("postgres://", "postgresql://")):
            return "postgresql+psycopg://" + v.split("://", 1)[1]  # hosted Postgres URLs name no driver
        if v.startswith("sqlite:///") and not v.startswith("sqlite:////"):
            p = Path(v.removeprefix("sqlite:///"))
            if not p.is_absolute():
                p = (BACKEND_DIR / p).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{p.as_posix()}"
        return v

    @field_validator("storage_dir", "mail_dir", "frontend_dist", mode="before")
    @classmethod
    def _resolve_path(cls, v: str | Path) -> Path:
        p = Path(v)
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure

    @property
    def origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]

    def key_bytes(self, name: Literal["storage", "secrets", "signing"]) -> bytes:
        raw = {
            "storage": self.storage_encryption_key,
            "secrets": self.secrets_encryption_key,
            "signing": self.signing_key,
        }[name]
        if not raw:
            raise RuntimeError(
                f"{name.upper()} key is not configured. Run `python -m app.cli generate-keys` "
                "and place the values in backend/.env (see .env.example)."
            )
        try:
            key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"{name.upper()} key is not valid base64") from exc
        if len(key) != 32:
            raise RuntimeError(f"{name.upper()} key must decode to 32 bytes")
        return key

    trust_root_paths: list[str] = Field(default_factory=list)


def _vercel_defaults() -> None:
    """Defaults for Vercel: read-only code directory, no persistent disk, no long-lived threads, 4.5 MB request bodies."""
    if os.environ.get("VERCEL") != "1":
        return
    hosts = [os.environ.get(k) for k in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_BRANCH_URL", "VERCEL_URL")]
    hosts = [h for h in dict.fromkeys(hosts) if h]
    db = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    defaults = {
        "APP_ENV": "production",
        "STORAGE_BACKEND": "database",
        "JOBS_MODE": "request",
        "RUN_WORKER_IN_PROCESS": "false",
        "STORAGE_DIR": "/tmp/ivp/storage",
        "MAIL_DIR": "/tmp/ivp/mail",
        "MAX_DOCUMENT_MB": "4",
        "MAX_IMAGE_MB": "4",
        "MAX_VIDEO_MB": "4",
        "PUBLIC_BASE_URL": f"https://{hosts[0]}" if hosts else "",
        "ALLOWED_ORIGINS": ",".join(f"https://{h}" for h in hosts),
    }
    if db:
        defaults["DATABASE_URL"] = db
    if os.environ.get("DATABASE_URL_UNPOOLED"):
        # A direct connection: the start-up migration lock and prepared statements need a real session, not a pooler.
        os.environ["DATABASE_URL"] = os.environ["DATABASE_URL_UNPOOLED"]
    for k, v in defaults.items():
        if v:
            os.environ.setdefault(k, v)
    # Custom domains may be listed in ALLOWED_ORIGINS; this deployment's own Vercel addresses are always allowed.
    own = [f"https://{h}" for h in hosts]
    listed = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    os.environ["ALLOWED_ORIGINS"] = ",".join(dict.fromkeys(listed + own))


@lru_cache
def get_settings() -> Settings:
    from dotenv import load_dotenv

    # Make .env values visible to os.environ too (integration credentials are referenced by env-var name).
    load_dotenv(os.environ.get("APP_ENV_FILE", str(BACKEND_DIR / ".env")), override=False)
    _vercel_defaults()
    s = Settings()
    s.trust_root_paths = [p.strip() for p in s.pdf_trust_roots.split(",") if p.strip()]
    return s


def reset_settings_cache() -> None:
    get_settings.cache_clear()
