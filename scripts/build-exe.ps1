$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'python'
}

Push-Location $repoRoot
try {
    & (Join-Path $repoRoot 'scripts\build-openxr-layer.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw "OpenXR layer build failed with exit code $LASTEXITCODE."
    }

    $env:VGAMEPAD_SKIP_VIGEMBUS_INSTALL = 'true'
    & $python -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed with exit code $LASTEXITCODE."
    }
    & $python -m PyInstaller --noconfirm .\packaging\VRTreadmill.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    $portableLayerBin = Join-Path $repoRoot 'dist\openxr_layer\bin'
    New-Item -ItemType Directory -Force -Path $portableLayerBin | Out-Null
    Copy-Item `
        -Path (Join-Path $repoRoot 'native\openxr_layer\bin\vrtread_openxr_layer.dll') `
        -Destination (Join-Path $portableLayerBin 'vrtread_openxr_layer.dll') `
        -Force

    Write-Host "Built $repoRoot\dist\VRTreadmill.exe"
}
finally {
    Pop-Location
}
