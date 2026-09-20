<#
.SYNOPSIS
  Validates the OpenXR approach end to end without a headset.

  1. Native: the test "game" talks to the REAL Khronos OpenXR loader, which loads the layer DLL exactly as it
     would inside a game, on top of a mock runtime that simulates SteamVR's input system. Also checks the
     hand-written OpenXR header against the official one.
  2. Python: unit tests plus cross-language tests where the real app code publishes and the native game reads.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

& (Join-Path $PSScriptRoot "build-openxr-layer.ps1") -Test

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
Push-Location $root
try {
    & $python -m pytest -q -rs
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
}
Write-Host "All OpenXR validation passed."
