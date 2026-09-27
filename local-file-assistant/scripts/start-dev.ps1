$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\backend"

$env:OLLAMA_KEEP_ALIVE = "-1"
& ".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8756
