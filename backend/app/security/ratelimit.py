"""Login throttling (database-backed) and a lightweight in-memory API rate limiter."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import LoginAttempt, utcnow


def login_blocked(db: Session, email: str, ip: str | None) -> str | None:
    """Return a reason string when the attempt must be refused, else None."""
    s = get_settings()
    since = utcnow() - timedelta(minutes=s.login_failure_window_minutes)
    if ip:
        ip_count = db.execute(
            select(func.count()).select_from(LoginAttempt).where(LoginAttempt.ip == ip, LoginAttempt.created_at >= since)
        ).scalar_one()
        if ip_count >= s.login_ip_max_attempts:
            return "ip_rate_limited"
    failures = db.execute(
        select(func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.email == email, LoginAttempt.success.is_(False), LoginAttempt.created_at >= since)
    ).scalar_one()
    if failures >= s.login_max_failures:
        return "account_temporarily_locked"
    return None


def record_attempt(db: Session, email: str, ip: str | None, success: bool, reason: str | None = None) -> None:
    db.add(LoginAttempt(email=email, ip=ip, success=success, reason=reason))


class SlidingWindowLimiter:
    """Per-key sliding window limiter for general API traffic (per process)."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory
                self._hits.clear()
            return True


api_limiter = SlidingWindowLimiter(limit=600, window_seconds=60)
sensitive_limiter = SlidingWindowLimiter(limit=10, window_seconds=300)
