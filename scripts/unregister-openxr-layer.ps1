# Removes every registration of the VR Treadmill OpenXR layer for the current user.
# Games stop loading it the next time they start.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
Push-Location $root
try { & $python -m vrtread.openxr disable; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } } finally { Pop-Location }
