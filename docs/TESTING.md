# Testing

All fixtures are synthetic, generated inside the tests, and live in a temporary database (backend) or an isolated `backend/var/e2e` database (browser test). External services are simulated with `httpx.MockTransport` only inside tests.

## Backend (pytest)

`cd backend; .venv\Scripts\python -m pytest` — auth (login/logout, CSRF, single role, rate limiting, invitations, reset, MFA, RFC 6238 vector), document pipeline (extraction, OCR on a scanned image, validation, unverifiable status, identity mismatch, cross-document conflicts, invalid/oversized/spoofed uploads, duplicates, versions, overlay tampering, real PDF signatures intact/tampered, append-only idempotent decisions, corrections), official adapters (QR verified/mismatch/not-allowlisted/error/not-found, REST adapter auth failure and success), inference (annotations equal model output, auth failure → no detections, retry), video timestamps and tracking, audit chain and append-only triggers, dashboard figures vs queries, reports with filters (JSON/CSV/PDF), signed file links.

## Browser (Playwright, desktop Chrome)

```powershell
cd backend
$env:DATABASE_URL="sqlite:///var/e2e/e2e.db"; $env:STORAGE_DIR="var/e2e/storage"; $env:PUBLIC_BASE_URL="http://127.0.0.1:8765"; $env:ALLOWED_ORIGINS="http://127.0.0.1:8765"
.venv\Scripts\python -m alembic upgrade head
# create a synthetic e2e officer in this database, then:
.venv\Scripts\python -m uvicorn app.main:app --port 8765
cd ..\frontend; $env:E2E_EMAIL="..."; $env:E2E_PASSWORD="..."; npx playwright test
```
