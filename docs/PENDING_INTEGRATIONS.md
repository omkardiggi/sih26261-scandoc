# Pending authorizations and inputs

Everything below is built and waiting for real configuration. Until it is provided, the platform reports the actual status (e.g. "Unable to verify against official records", "No enabled inspection model applies") and never produces placeholder results.

## Official verification sources

| Source | Status | What is needed |
|---|---|---|
| AICTE approval-status page via QR on letters (`approvalstatus.aicte.gov.in`) | **Working** — real HTML page observed on a 2026-27 EoA letter; parser compares name, IDs, academic year and every course intake | Nothing. Officer-triggered only. The page has no published API documentation; if AICTE changes its layout the parser fails closed (`unable_to_verify`). |
| AICTE institution/approval API | Not configured — no public documented API found | Authorised API documentation, endpoint, auth method and a redacted sample response |
| DigiLocker / API Setu (Requester) | Not configured | Requester onboarding on API Setu, OAuth2 client credentials, list of document types |
| University / affiliating-body, state fire/occupancy/land departments | Not configured | For each: documentation, endpoint format, auth, access scope, redacted sample response |

Configure in **Institution Verifier → Official sources** (generic REST adapter: request/response mapping JSON) and press **Test connection** — a source is marked operational only after a real test request succeeds.

## Verification without any API (available now)

- **Issuer QR pages** — QR links on certificates are opened only when they point to official government hosts (`*.gov.in`, `*.nic.in`; add others under Official sources → "Official issuer pages"). The check reports which document values the issuer's page shows; missing values give *unable to verify*, never a false mismatch.
- **Digital signatures** — CCA India root certificates are bundled in `data/trust_roots` (fingerprints in its README) and used by default, so signatures chaining to India PKI are reported as trusted.
- **Manual checks on public official sites** — each document's Official records tab lists the relevant sites (AICTE dashboard, SHCIL e-stamp, UGC, NAAC, NBA, MCA, GST, PAN, DigiLocker, state portals) with a one-click link and a form to record the result with a screenshot. Edit the list in `data/reference/public_verification_sources.json`.

## Inspection models (Roboflow)

Configured from what you provided (workspace `omkars-workspace-qltv8`, key stored only in `backend/.env`):

| Workflow | Status |
|---|---|
| `doctr-demo` (DocTR text recognition) | **Operational** — tested with a synthetic image; text outputs appear as text annotations on inspection media |
| `florence2-base-demo` (Florence-2) | **Operational** — returns an image caption (description of the photo), shown as a text annotation; no bounding boxes in this workflow |

The `rf_…` publishable key cannot run serverless inference and was not stored. Rotate the private key that was shared in chat when convenient (update `ROBOFLOW_API_KEY`).

### Project/version models

Per model: project ID, version, task type (object detection / instance segmentation / classification), exact class names, which classes should create findings and at what severity (with the basis), confidence thresholds, dataset notes, evaluation metrics, and whether you use the Serverless API (default `https://serverless.roboflow.com`) or a dedicated deployment. Set `ROBOFLOW_API_KEY` in `backend/.env` (or store it, encrypted, under Inspection & Analysis → Models), enable the provider, add the model and press **Test**.

## Checklists and requirements

- Inspection checklist criteria with applicable standards and official sources (Inspection & Analysis → Inspection checklist). None are seeded.
- Document requirements: 17 entries transcribed from APH 2024-27 (Annexure-1 §1.1, Annexure-2 §2.1) are seeded as **unconfirmed**. Confirm them against the latest notified edition/corrigenda, and add requirements for other application types.

## Optional infrastructure

- `CLAMD_HOST` for malware scanning (otherwise uploads show "not scanned"); set `MALWARE_SCAN_REQUIRED=true` to block uploads without a scan.
- SMTP for password-reset and invitation emails (without it, links are shown to the issuing officer to hand over securely; in development they are written to `backend/var/mail`).
