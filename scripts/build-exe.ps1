<#
.PARAMETER UseBuiltLayer
  Package the OpenXR layer DLL that is already in native\openxr_layer\bin instead of compiling it again.
  Release flow: scripts\build-openxr-layer.ps1 -Test, then this switch, so the DLL that ships is the very
  binary the test-suite validated. Without the switch the layer is recompiled from scratch first.
#>
param(
    [switch]$UseBuiltLayer
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    $python = 'python'  # CI installs into the runner's Python instead of a venv
}

Push-Location $repoRoot
try {
    # The layer DLL and manifest are bundled into the exe (see packaging\VRTreadmill.spec).
    if (-not $UseBuiltLayer) {
        & (Join-Path $repoRoot 'scripts\build-openxr-layer.ps1') -Rebuild
    }

    $env:VGAMEPAD_SKIP_VIGEMBUS_INSTALL = 'true'
    & $python -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency install failed with exit code $LASTEXITCODE."
    }
    & $python -m PyInstaller --clean --noconfirm packaging\VRTreadmill.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    if (-not (Test-Path (Join-Path $repoRoot 'dist\VRTreadmill.exe'))) {
        throw "PyInstaller reported success but dist\VRTreadmill.exe is missing."
    }
    Write-Host "Built $repoRoot\dist\VRTreadmill.exe"
}
finally {
    Pop-Location
}
