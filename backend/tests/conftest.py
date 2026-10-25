"""Test fixtures. All data created here is SYNTHETIC and exists only in a temporary test database."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="ivp-test-"))
os.environ.update({
    "APP_ENV": "test",
    "APP_ENV_FILE": str(_TMP / "none.env"),
    "DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    "STORAGE_DIR": str(_TMP / "storage"),
    "MAIL_DIR": str(_TMP / "mail"),
    "STORAGE_ENCRYPTION_KEY": "q2tW0x6n3K5Q8b1Zr7yV4m9A2cE6gJ0L3pS5uX8zB1E",
    "SECRETS_ENCRYPTION_KEY": "w9eR2tY5uI8oP1aS4dF7gH0jK3lZ6xC9vB2nM5qW8eE",
    "SIGNING_KEY": "z1X2c3V4b5N6m7Q8w9E0r1T2y3U4i5O6p7A8s9D0f1G",
    "RUN_WORKER_IN_PROCESS": "false",
    "LOGIN_MAX_FAILURES": "5",
    "LOGIN_IP_MAX_ATTEMPTS": "1000",  # many test logins share one client IP
    "EXPOSE_MODEL_ADMIN": "true",  # tests configure models through the admin API
    "MAX_DOCUMENT_MB": "5",
    "PUBLIC_BASE_URL": "http://testserver",
    "ALLOWED_ORIGINS": "http://testserver",
})
if os.environ.get("TEST_DATABASE_URL"):  # e.g. an empty PostgreSQL database, to test the hosted configuration
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["STORAGE_BACKEND"] = os.environ.get("TEST_STORAGE_BACKEND", "database")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import BACKEND_DIR, reset_settings_cache  # noqa: E402

reset_settings_cache()

PASSWORD = "Synthetic-Test-Pass-2026!"


@pytest.fixture(scope="session", autouse=True)
def database():
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")
    from app.services.bootstrap import ensure_reference_configuration

    ensure_reference_configuration()
    yield
    from app.db import reset_engine

    reset_engine()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def app():
    from app.main import create_app

    return create_app()


def make_client(app) -> TestClient:
    return TestClient(app, base_url="http://testserver")


def login(client: TestClient, email: str, password: str = PASSWORD):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    if r.status_code == 200:
        client.headers["X-CSRF-Token"] = client.cookies.get("ivp_csrf")
    return r


_counter = {"n": 0}


def new_officer(email: str | None = None, password: str = PASSWORD):
    from app.db import session_scope
    from app.services.officers import create_officer

    _counter["n"] += 1
    email = email or f"officer{_counter['n']}@example.test"
    with session_scope() as db:
        create_officer(db, email=email, full_name=f"Officer Number {_counter['n']}", password=password)
    return email


@pytest.fixture()
def officer_client(app):
    email = new_officer()
    c = make_client(app)
    assert login(c, email).status_code == 200
    c.officer_email = email
    return c


def run_jobs():
    from app.jobs.worker import run_pending_jobs

    return run_pending_jobs(200)


def synthetic_pdf(lines: list[str], *, table: list[list[str]] | None = None, title="SYNTHETIC TEST DOCUMENT") -> bytes:
    """Generate a digital PDF with real text (clearly marked synthetic)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    ss = getSampleStyleSheet()
    story = [Paragraph(title, ss["Title"])] + [Paragraph(x, ss["BodyText"]) for x in lines]
    if table:
        story.append(Spacer(1, 12))
        t = Table(table)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        story.append(t)
    doc.build(story)
    return buf.getvalue()


def create_institution(client, **kw) -> dict:
    body = {"name": "Synthetic Institute of Technology", "state": "Testland", "district": "Sample District",
            "category": "Engineering and Technology", "aicte_permanent_id": None} | kw
    r = client.post("/api/institutions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def upload(client, institution_id, data: bytes, filename="doc.pdf", **form):
    files = {"file": (filename, data, "application/pdf" if filename.endswith(".pdf") else "image/png")}
    return client.post("/api/documents", data={"institution_id": institution_id, **form}, files=files)
