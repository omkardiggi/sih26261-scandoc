"""Vercel entrypoint: the FastAPI application (API + built web app) from backend/app."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.main import app  # noqa: E402,F401
