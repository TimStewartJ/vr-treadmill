$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$layer = Join-Path $root "native\openxr_layer"
$build = Join-Path $layer "build"

$cmakeCommand = Get-Command cmake -ErrorAction SilentlyContinue
if ($cmakeCommand) {
    $cmake = $cmakeCommand.Source
} else {
    $cmake = Get-ChildItem `
        "C:\Program Files\Microsoft Visual Studio", `
        "C:\Program Files (x86)\Microsoft Visual Studio" `
        -Recurse -Filter cmake.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like "*\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" } |
        Select-Object -First 1 -ExpandProperty FullName
}

if (-not $cmake) {
    throw "cmake.exe was not found. Install CMake or the Visual Studio C++ CMake tools."
}

& $cmake -S $layer -B $build -G "Visual Studio 17 2022" -A x64
& $cmake --build $build --config Release

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}
& $python -m vrtread.openxr write-manifest
