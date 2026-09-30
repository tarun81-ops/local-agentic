# Writes backend\requirements-lock.txt: the exact versions installed in .venv right now.
# Run after a working setup; setup.ps1 uses the lock file when it exists, so every rebuild
# (and the installer) gets the same versions you tested with.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\backend"
& ".venv\Scripts\python.exe" -m pip freeze --exclude-editable | Out-File -Encoding utf8 requirements-lock.txt
Write-Host "Wrote backend\requirements-lock.txt"
