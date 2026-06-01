$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'python'
}

Push-Location $repoRoot
try {
    & $python -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed with exit code $LASTEXITCODE."
    }
    & $python -m PyInstaller --noconfirm .\packaging\VRTreadmill.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    Write-Host "Built $repoRoot\dist\VRTreadmill.exe"
}
finally {
    Pop-Location
}
