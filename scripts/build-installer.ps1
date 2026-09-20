$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $repoRoot 'dist\VRTreadmill.exe'
if (-not (Test-Path $exe)) {
    throw "Missing $exe. Run scripts\build-exe.ps1 first."
}

& (Join-Path $repoRoot 'scripts\build-openxr-layer.ps1')

$iscc = $env:ISCC_PATH
if (-not $iscc) {
    $command = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($command) {
        $iscc = $command.Source
    }
}
if (-not $iscc) {
    $commonPaths = @(
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    foreach ($commonPath in $commonPaths) {
        if (Test-Path $commonPath) {
            $iscc = $commonPath
            break
        }
    }
}
if (-not $iscc -or -not (Test-Path $iscc)) {
    throw "Inno Setup 6 compiler not found. Install it or set ISCC_PATH to ISCC.exe."
}

Push-Location $repoRoot
try {
    & $iscc .\packaging\VRTreadmill.iss
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
    Write-Host "Built $repoRoot\dist\VRTreadmill-Setup.exe"
}
finally {
    Pop-Location
}
