"""FastAPI application factory."""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import get_settings
from .security.ratelimit import api_limiter

log = logging.getLogger("app")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
    "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def _validate_keys() -> None:
    s = get_settings()
    for name in ("storage", "secrets", "signing"):
        s.key_bytes(name)  # raises a clear RuntimeError when missing/invalid


def _migrate() -> None:
    """Bring the database schema up to date (hosted deployments have no separate release step)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from .config import BACKEND_DIR
    from .db import get_engine

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    engine = get_engine()
    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:  # one instance migrates at a time
            conn.execute(text("SELECT pg_advisory_lock(727001)"))
            try:
                cfg.attributes["connection"] = conn
                command.upgrade(cfg, "head")
                conn.commit()
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(727001)"))
                conn.commit()
    else:
        command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    _validate_keys()
    if os.environ.get("VERCEL") == "1" or os.environ.get("AUTO_MIGRATE") == "1":
        _migrate()
    from .services.bootstrap import ensure_reference_configuration

    ensure_reference_configuration()
    if s.run_worker_in_process and s.app_env != "test":
        from .jobs.worker import start_workers, stop_workers

        start_workers()
        yield
        stop_workers()
    else:
        yield


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(
        title=f"{s.app_name} API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if s.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if s.is_production else "/api/openapi.json",
    )

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        request_id = uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        path = request.url.path
        if path.startswith("/api/"):
            ip = request.client.host if request.client else "unknown"
            if not api_limiter.allow(ip):
                return JSONResponse({"detail": "Too many requests. Please slow down."}, status_code=429)
            length = request.headers.get("content-length")
            max_body = (s.max_video_mb + 5) * 1024 * 1024
            if length and length.isdigit() and int(length) > max_body:
                return JSONResponse({"detail": "Request body is too large."}, status_code=413)
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if path.startswith("/api/docs"):
            pass  # Swagger UI needs its own CDN assets (development only)
        else:
            response.headers.setdefault("Content-Security-Policy", CSP)
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if s.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [
            {"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg", "Invalid value")}
            for e in exc.errors()
        ]
        return JSONResponse({"detail": "Some fields are invalid.", "errors": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "-")
        log.exception("Unhandled error (request %s) on %s %s", rid, request.method, request.url.path)
        return JSONResponse(
            {"detail": f"An unexpected error occurred. Reference: {rid}"}, status_code=500
        )

    from .api import (
        audit_api,
        auth,
        dashboard,
        documents,
        files,
        inspections,
        institutions,
        integrations_api,
        jobs_api,
        notifications,
        officers,
        reports,
        requirements,
        verifier,
        vision_models,
    )

    for module in (
        auth, officers, dashboard, institutions, verifier, documents, requirements, integrations_api,
        inspections, reports, audit_api, jobs_api, notifications, files,
    ):
        app.include_router(module.router)
    if s.expose_model_admin:
        app.include_router(vision_models.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "jobs_mode": s.jobs_mode, "private_mode": s.private_mode}

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    dist: Path = get_settings().frontend_dist
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if not index.exists():
            return JSONResponse(
                {"detail": "Frontend build not found. Run `npm run build` in frontend/ or use the Vite dev server."},
                status_code=404,
            )
        candidate = (dist / full_path).resolve()
        if full_path and dist.resolve() in candidate.parents and candidate.is_file():
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in f"/{full_path}" else {}
            return FileResponse(candidate, headers=headers)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
