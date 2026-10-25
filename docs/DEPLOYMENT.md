# Deployment

1. `APP_ENV=production`, HTTPS-terminating reverse proxy, `PUBLIC_BASE_URL` / `ALLOWED_ORIGINS` set to the public origin. Cookies become `Secure`; HSTS is sent; `/api/docs` is disabled.
2. Generate keys with `python -m app.cli generate-keys` and store them in a secrets manager. **Back up `STORAGE_ENCRYPTION_KEY` separately** — without it stored files cannot be decrypted.
3. PostgreSQL: `pip install "psycopg[binary]"`, `DATABASE_URL=postgresql+psycopg://user:pass@host/db`, run `alembic upgrade head` (append-only triggers are created for PostgreSQL too).
4. Run the worker separately: `RUN_WORKER_IN_PROCESS=false` for API replicas and one or more `python -m app.cli worker` processes.
5. Build the UI (`npm run build`); the API serves `frontend/dist`.
6. Configure SMTP, ClamAV (`MALWARE_SCAN_REQUIRED=true`) and `PDF_TRUST_ROOTS`.
7. Schedule `python -m app.cli retention` daily and `python -m app.cli verify-audit` (alerts on non-zero exit).
8. Backups: SQLite — `python -m app.cli backup` / `restore <archive> --yes`; PostgreSQL — `pg_dump` plus a copy of `STORAGE_DIR`.
9. First officer: `python -m app.cli create-officer` or `issue-link` on the server. There is no public registration.
