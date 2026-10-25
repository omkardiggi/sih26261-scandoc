# API overview

All endpoints are under `/api`, require a signed-in officer session (except login, password reset, invitation acceptance, token info and health) and a `X-CSRF-Token` header on mutations. Interactive OpenAPI docs: `/api/docs` (development only).

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login`, `/auth/mfa/verify`, `/auth/logout`, `/auth/password`, `/auth/mfa/{setup,enable,disable}`, `/auth/password-reset/{request,confirm}`, `/auth/invite/accept`, `GET /auth/me`, `/auth/sessions` |
| Officers | `GET/POST /officers`, `POST /officers/{id}/{reset-link,deactivate,reactivate}` |
| Dashboard | `GET /dashboard/{summary,charts,activity,priorities}` |
| Institutions | `GET/POST /institutions`, `GET/PATCH /institutions/{id}`, `/versions`, `/programs`, `/programs/compare`, `/programs/import-from-document`, `/records`, `/activity`, `/approval-status`, `/institution-fields` |
| Verifier | `GET/POST /cases`, `GET/PATCH /cases/{id}`, `/evaluate`, `/explanation`, `/decisions`; `POST /documents` (multipart), `GET /documents`, `GET/PATCH /documents/{id}`, `/text`, `/revalidate`, `/reprocess`, `/official-options`, `/official-verification`, `/official-checks`, `/review`; `POST /fields/{id}/corrections`; `GET /official-attempts/{id}/response`; `/requirements`, `/document-categories`, `/integrations` |
| Inspection | `/inspections`, `/inspections/{id}/media`, `/media/{id}`, `/media/{id}/analyze`, `/media/{id}/annotations`, `/annotations/{id}/{review,correct}`, `/inspection-criteria`, `/inspections/{id}/assessments/{criterion}`, `/findings`, `/corrective-actions`, `/models` |
| Reports & audit | `GET /reports/{kind}?format=json|csv|pdf`, `/reports/case/{id}`, `GET /audit`, `POST /audit/verify`, `/jobs`, `/jobs/{id}/{retry,cancel}`, `/notifications` |
| Files | `GET /files/{signed-token}` (session + short-lived HMAC token; Range supported for video) |
