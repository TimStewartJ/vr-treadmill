<#
.SYNOPSIS
  Builds the VR Treadmill OpenXR API layer (native\openxr_layer\bin).

.PARAMETER Test
  Also builds the test harness (downloads the pinned Khronos OpenXR loader) and runs it:
  real loader + layer DLL + mock runtime, negotiation tests, and the header ABI check.

.PARAMETER Rebuild
  Recompiles the layer from scratch even if the build system thinks it is up to date. Release builds use
  this: incremental builds trust file timestamps, and a source file restored with an older timestamp
  (git checkout, a copied backup) is silently skipped, leaving a DLL built from different code.

.PARAMETER Clean
  Deletes the whole CMake build directory first (also re-downloads and rebuilds the test dependencies).
#>
param(
    [switch]$Test,
    [switch]$Rebuild,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$layer = Join-Path $root "native\openxr_layer"
$build = Join-Path $layer "build"
$dll = Join-Path $layer "bin\vrtread_openxr_layer.dll"
$manifest = Join-Path $layer "bin\XR_APILAYER_VRTREAD_treadmill.json"

function Find-VsTool([string]$name, [string]$pattern) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return Get-ChildItem "C:\Program Files\Microsoft Visual Studio", "C:\Program Files (x86)\Microsoft Visual Studio" `
        -Recurse -Filter $name -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like $pattern } |
        Select-Object -First 1 -ExpandProperty FullName
}

function Invoke-Checked([string]$what, [scriptblock]$action) {
    & $action
    if ($LASTEXITCODE -ne 0) { throw "$what failed with exit code $LASTEXITCODE." }
}

$cmake = Find-VsTool "cmake.exe" "*\CMake\bin\cmake.exe"
if (-not $cmake) { throw "cmake.exe was not found. Install CMake or the Visual Studio C++ CMake tools." }

if ($Clean -and (Test-Path $build)) { Remove-Item -Recurse -Force $build }

# No -G: CMake picks the newest installed Visual Studio, so this keeps working as build machines move on
# (GitHub's windows-latest image dropped VS 2022). -A requires a Visual Studio generator, which is the default.
$testsFlag = if ($Test) { "ON" } else { "OFF" }
Invoke-Checked "CMake configure" { & $cmake -S $layer -B $build -A x64 "-DVRTREAD_BUILD_TESTS=$testsFlag" }
if ($Rebuild) {
    Invoke-Checked "Layer rebuild" { & $cmake --build $build --config Release --target vrtread_openxr_layer --clean-first -- /m /v:m /nologo }
}
Invoke-Checked "Layer build" { & $cmake --build $build --config Release -- /m /v:m /nologo }

if (-not (Test-Path $dll) -or -not (Test-Path $manifest)) { throw "Build finished but $dll or its manifest is missing." }

# Sanity check: after a successful build the DLL can never be older than its newest source. (This does not
# catch a source restored with an *older* timestamp; -Rebuild exists for that.)
$newestSource = Get-ChildItem (Join-Path $layer "src"), (Join-Path $layer "include") -Recurse -File |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ((Get-Item $dll).LastWriteTime -lt $newestSource.LastWriteTime) {
    throw "Stale DLL: $dll is older than $($newestSource.FullName). Re-run with -Clean."
}

# The OpenXR loader fails xrCreateInstance when no API layer can be loaded, so a layer DLL with a missing
# dependency can stop a game from starting. It must import nothing beyond kernel32 (static CRT).
$dumpbin = Find-VsTool "dumpbin.exe" "*\bin\Hostx64\x64\dumpbin.exe"
if ($dumpbin) {
    $imports = & $dumpbin /nologo /dependents $dll | Where-Object { $_ -match '^\s+\S+\.dll\s*$' } | ForEach-Object { $_.Trim() }
    $unexpected = $imports | Where-Object { $_ -ine "KERNEL32.dll" }
    if ($unexpected) { throw "The layer DLL must only import KERNEL32.dll but also imports: $($unexpected -join ', ')" }
    Write-Host "Imports: $($imports -join ', ')"
} else {
    Write-Warning "dumpbin.exe not found; skipped the import check."
}

Write-Host "Built $dll ($((Get-Item $dll).VersionInfo.FileVersion))"

if ($Test) {
    $ctest = Join-Path (Split-Path $cmake) "ctest.exe"
    Invoke-Checked "Layer tests" { & $ctest --test-dir $build -C Release --output-on-failure }
}
