<#
.SYNOPSIS
  Builds the genuine 32-bit OpenXR client used by tests\test_installed_layer.py to prove that 32-bit games
  are not affected by this x64-only layer. Optional: only needed for that one release-validation test.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "native\openxr_layer\tests\win32_check"
$build = Join-Path $root "native\openxr_layer\build-win32-check"

$cmake = (Get-Command cmake.exe -ErrorAction SilentlyContinue).Source
if (-not $cmake) {
    $cmake = Get-ChildItem "C:\Program Files\Microsoft Visual Studio", "C:\Program Files (x86)\Microsoft Visual Studio" `
        -Recurse -Filter cmake.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like "*\CMake\bin\cmake.exe" } | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $cmake) { throw "cmake.exe was not found." }

& $cmake -S $source -B $build -A Win32
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed with exit code $LASTEXITCODE." }
& $cmake --build $build --config Release -- /m /v:m /nologo
if ($LASTEXITCODE -ne 0) { throw "32-bit build failed with exit code $LASTEXITCODE." }
Write-Host "Built $build\bin\vrtread_probe32.exe"
