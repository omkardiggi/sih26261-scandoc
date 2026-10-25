# Start the API (with in-process worker) and the Vite dev server.
$root = Split-Path -Parent $PSScriptRoot
Start-Process -NoNewWindow -FilePath "$root\backend\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory "$root\backend"
Set-Location "$root\frontend"
npm run dev
