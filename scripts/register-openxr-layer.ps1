$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
Push-Location $root
try { & $python -m vrtread.openxr enable; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } } finally { Pop-Location }
