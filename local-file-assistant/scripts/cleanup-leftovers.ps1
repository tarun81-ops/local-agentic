# Sends files the app no longer uses to the Recycle Bin (never a permanent delete).
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\cleanup-leftovers.ps1
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName Microsoft.VisualBasic
$root = Split-Path -Parent $PSScriptRoot

$leftovers = @(
  "ui\src\api\search.js",
  "ui\src\pages\index-settings.js",
  "ui\electron\overlay.html",
  "backend\test_organizer.py",
  "backend\test_vertical_slice.py",
  "backend\app\core\llm\image_tagger.py",
  "backend\data",   # old test scratch (smoke_test.db, organize_scratch)
  "data"            # old index location; the app now uses %APPDATA%\LocalFileAssistant
)

foreach ($rel in $leftovers) {
  $p = Join-Path $root $rel
  if (-not (Test-Path $p)) { Write-Host "already gone: $rel"; continue }
  if ((Get-Item $p) -is [System.IO.DirectoryInfo]) {
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p, 'OnlyErrorDialogs', 'SendToRecycleBin')
  } else {
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin')
  }
  Write-Host "recycled: $rel"
}

# Empty folders left behind
foreach ($rel in @("ui\src\api")) {
  $p = Join-Path $root $rel
  if ((Test-Path $p) -and -not (Get-ChildItem $p -Force)) { Remove-Item $p; Write-Host "removed empty folder: $rel" }
}
Write-Host "Done. Restore anything from the Recycle Bin if needed."
