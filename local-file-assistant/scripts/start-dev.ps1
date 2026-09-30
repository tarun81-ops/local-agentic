# Runs the backend on its own (port 8756) for API work. The desktop app (cd ui; npm run dev)
# starts its own backend, so you don't need this for normal use. With no API_TOKEN in .env,
# a token is generated and printed; send it as "Authorization: Bearer <token>".
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\backend"
& ".venv\Scripts\python.exe" run.py
