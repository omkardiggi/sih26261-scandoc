# Docura — Institution Verification Platform

A web application for authorized **AICTE Officers** to manage institution records, verify submitted documents, analyse inspection photographs and videos, and record decisions, with every result evidence-linked and audited.

- **One role**: AICTE Officer (enforced by a database CHECK constraint and server-side authorization on every endpoint).
- **Five sections**: Dashboard · Institutions · Institution Verifier · Inspection & Analysis · Reports & Audit.
- **No fabricated data**: the database starts empty, statistics come from live queries, and nothing is "verified" unless an official source returns supporting evidence.

## Quick start (Windows, development)

Prerequisites: Python 3.12+, Node 20+, FFmpeg on `PATH` (needed for video).

```powershell
.\scripts\setup.ps1          # venv + Python deps, .env with generated keys, DB migrations, npm install, frontend build
cd backend
.venv\Scripts\python -m app.cli create-officer --email you@example.gov.in --name "Your Name"   # prompts for password (never echoed)
cd ..
.\scripts\dev.ps1            # API on :8000, web app on http://localhost:5173
```

Or serve the built UI from the API alone: `cd backend; .venv\Scripts\python -m uvicorn app.main:app --port 8000` and open http://localhost:8000.

### AI document & image analysis

Set `ROBOFLOW_API_KEY` in `backend/.env` and restart. The analysis workflow (`ROBOFLOW_WORKSPACE` / `DOCUMENT_TEXT_WORKFLOW`,
pre-filled in `.env.example`) is then registered and used automatically: every uploaded page is re-read by the AI, signatures,
stamps and photographs are analysed, and inspection photos get a condition assessment. Without a key the platform still runs
with local OCR and checks only. The key is used server-side only and never reaches the browser.

Anyone can also create an account from **Sign up** on the login page with any email address (e.g. Gmail). Set
`SIGNUP_ALLOWED_DOMAINS` to limit sign-up to certain domains, or `SIGNUP_ENABLED=false` to turn it off.

Further officers are invited from the account menu → **Officer accounts** (single-use links), or by an operator with `python -m app.cli issue-link --email ... --name ...`.

## What is implemented

| Area | Implementation |
|---|---|
| Auth | Argon2id passwords, DB-backed sessions (HttpOnly, SameSite=Strict, Secure in production), 30-min idle / 8-h absolute expiry, CSRF token + Origin check, login throttling and lockout, optional TOTP MFA, password reset, invitations, session revocation |
| Storage | Every file AES-256-GCM encrypted at rest; originals never modified; renders, processing copies and evidence stored separately; short-lived HMAC-signed file links that also require a session |
| Uploads | Extension + magic-byte checks, size and page limits, corrupted-file and decompression-bomb rejection, password-protected PDF handling, active-content detection (never executed), optional ClamAV scan, SHA-256 duplicate detection, versioning |
| Extraction | PyMuPDF text with coordinates, pdfplumber tables, local RapidOCR for scans (per-line boxes + confidence), program/intake table parsing, field provenance (page, bbox, method, source text) |
| Validation | Expected fields, identifier formats, academic-year logic, date consistency, stated-validity expiry, registry identity match (exact IDs, normalised names; close matches flagged, never auto-accepted), programs/intake vs registry, table totals, unreadable/blank pages, duplicates, internal contradictions |
| Official records | AICTE issuer QR approval-status page (allowlisted host, officer-triggered, exact comparison incl. course intakes); configurable REST adapter for authorised APIs; officer-recorded checks with evidence; missing source ⇒ **Unable to verify against official records** |
| Integrity | SHA-256 re-verification, PDF signature validation (pyHanko; trust anchors via `PDF_TRUST_ROOTS`), revision history, metadata, hidden text, font anomalies on numbers, rendered-vs-embedded comparison, JPEG error-level analysis with heatmaps, duplicated-region detection, ink regions, QR/barcode decoding — each dimension separate, each indicator with significance + evidence |
| Cross-document | Identity, IDs, academic year, trust, establishment year, PIN and per-course intake compared across a case's documents with links to sources |
| Requirements | Registry seeded from APH 2024-27 Annexure-1 §1.1 and Annexure-2 §2.1 with clause/page/excerpt; marked unconfirmed until an officer confirms |
| Inspection | Image upload/EXIF, video validation (FFprobe), FFmpeg sampling with true `pts_time` timestamps, configured-model inference only (server-side key), boxes/polygons drawn from real output, IoU tracking, class-rule findings, officer confirm/reject/correct (originals preserved), checklist assessments, corrective actions |
| Decisions & audit | Append-only decisions (DB triggers) with idempotency keys and supersede-based corrections; hash-chained append-only audit trail with verification |
| Reports | Institutions, documents, pending cases, discrepancies, inspections, corrective actions, decisions, officer activity, integration status, audit — JSON preview, CSV (formula-injection safe), PDF; per-case 9-part verification report |
| Jobs | DB-backed queue with leases, retries/backoff, timeouts, idempotency keys, progress, manual retry |

## Tests

```powershell
cd backend; .venv\Scripts\python -m pytest            # 26 backend tests (synthetic fixtures only)
cd frontend; npx playwright test                      # browser test (desktop) against an isolated e2e database — see docs/TESTING.md
```

## Documentation

- [docs/PENDING_INTEGRATIONS.md](docs/PENDING_INTEGRATIONS.md) — what is needed from you (APIs, Roboflow models, checklists)
- [docs/VERIFICATION_METHODOLOGY.md](docs/VERIFICATION_METHODOLOGY.md) — status rules, checks, limitations
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) — configuring official sources and inference models
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — production, PostgreSQL, backups, security checklist
- [docs/API.md](docs/API.md) — API overview (interactive docs at `/api/docs` in development)
- [docs/TESTING.md](docs/TESTING.md) — test suites and e2e setup
