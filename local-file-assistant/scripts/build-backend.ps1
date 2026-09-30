# Freezes the Python backend into backend\dist\lfa-backend\lfa-backend.exe with PyInstaller,
# checks that no secrets were bundled, then starts the frozen exe once to prove it runs.
# electron-builder then ships that folder inside the installer (see ui\package.json extraResources).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\backend"
$py = ".venv\Scripts\python.exe"

$extra = @()
# OCR is optional; bundle its models only when it's installed.
$ErrorActionPreference = "Continue"
& $py -c "import rapidocr_onnxruntime" 2>$null | Out-Null
$hasOcr = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if ($hasOcr) { $extra += @("--collect-all", "rapidocr_onnxruntime", "--collect-all", "onnxruntime") }

& ".venv\Scripts\pyinstaller.exe" run.py `
  --name lfa-backend `
  --noconfirm --clean --onedir --console `
  --collect-all lancedb `
  --collect-all pyarrow `
  --collect-all pymupdf `
  --collect-submodules uvicorn `
  --collect-submodules app `
  --collect-submodules send2trash `
  --collect-submodules watchdog `
  @extra
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$dist = "$root\backend\dist\lfa-backend"
$exe = "$dist\lfa-backend.exe"

# 1. Secrets must never ship: .env holds the local API token and settings.
$leaked = Get-ChildItem $dist -Recurse -Force -Include ".env", "api_token.txt", "*.db"
if ($leaked) { throw "Refusing to ship secrets/data: $($leaked.FullName -join ', ')" }

# 2. Smoke test: the frozen exe starts, answers /health, and rejects a missing token.
$port = 18756
$tmp = Join-Path $env:TEMP "lfa-smoke-$(Get-Random)"
$env:PORT = "$port"; $env:API_TOKEN = "smoke-token"; $env:DATA_DIR = $tmp
$env:DB_PATH = "$tmp\index.db"; $env:VECTOR_DB_DIR = "$tmp\lancedb"
$proc = Start-Process $exe -WorkingDirectory $dist -PassThru -WindowStyle Hidden
try {
  $ok = $false
  for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    try { if ((Invoke-RestMethod "http://127.0.0.1:$port/health").ok) { $ok = $true; break } } catch {}
  }
  if (-not $ok) { throw "Frozen backend did not answer /health within 30 s" }
  $headers = @{ Authorization = "Bearer smoke-token" }
  $s = Invoke-RestMethod "http://127.0.0.1:$port/settings" -Headers $headers
  Write-Host "Smoke test OK. File types: $($s.file_types -join ', ')"
  try { Invoke-RestMethod "http://127.0.0.1:$port/settings" | Out-Null; throw "API answered without a token" }
  catch { if ($_.Exception.Response.StatusCode.value__ -ne 401) { throw } }
} finally {
  if (-not $proc.HasExited) { taskkill /pid $proc.Id /T /F | Out-Null }
  Remove-Item Env:PORT, Env:API_TOKEN, Env:DATA_DIR, Env:DB_PATH, Env:VECTOR_DB_DIR -ErrorAction SilentlyContinue
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Built backend\dist\lfa-backend. Now: cd ui; npm run build"
