# One-time setup: Python venv + backend packages, then the UI packages.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Set-Location "$root\backend"
if (-not (Test-Path ".venv")) { python -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
if (Test-Path "requirements-lock.txt") {
  # Exact versions from scripts\lock-backend.ps1, then the dev tools on top.
  & ".venv\Scripts\pip.exe" install -r requirements-lock.txt
}
& ".venv\Scripts\pip.exe" install -r requirements-dev.txt
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

Set-Location "$root\ui"
npm install

Write-Host ""
Write-Host "Done. Make sure Ollama is running with the models pulled:"
Write-Host "  ollama pull qwen3-vl:2b-instruct"
Write-Host "  ollama pull granite-embedding:278m"
Write-Host "Then start the app with:  cd ui; npm run dev"
