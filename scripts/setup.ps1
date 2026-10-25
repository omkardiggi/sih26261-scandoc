# One-time development setup (Windows PowerShell).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\backend"
if (-not (Test-Path ".venv")) { python -m venv .venv }
.\.venv\Scripts\python -m pip install --upgrade pip -q
.\.venv\Scripts\python -m pip install -r requirements-dev.txt -q
.\.venv\Scripts\python -m app.cli init-env
.\.venv\Scripts\python -m alembic upgrade head
Set-Location "$root\frontend"
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
npm install --no-audit --no-fund
npm run build
Write-Host ""
Write-Host "Setup complete. Create the first officer:"
Write-Host "  cd backend; .venv\Scripts\python -m app.cli create-officer --email you@example.gov.in --name 'Your Name'"
Write-Host "Then run: .\scripts\dev.ps1"
