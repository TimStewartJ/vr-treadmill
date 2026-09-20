# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


REPO_ROOT = Path.cwd().resolve()
SPEC_DIR = REPO_ROOT / "packaging"
LAYER_DIR = REPO_ROOT / "native" / "openxr_layer" / "bin"
LAYER_FILES = ["vrtread_openxr_layer.dll", "XR_APILAYER_VRTREAD_treadmill.json"]

binaries = []
datas = []
hiddenimports = []

for package in ("PIL", "pystray", "pynput", "vgamepad"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# The OpenXR layer travels inside the exe (vrtread.openxr.bundle_dir() finds it under sys._MEIPASS), so the
# portable exe and the installed app behave the same. It is added as *data*: it must reach the user's disk
# byte-for-byte (its hash names the deployment folder), so it must never be UPX-packed or otherwise rewritten.
for name in LAYER_FILES:
    source = LAYER_DIR / name
    if not source.is_file():
        raise SystemExit(f"Missing {source}. Run scripts\\build-openxr-layer.ps1 first.")
    datas.append((str(source), "openxr_layer"))

a = Analysis(
    [str(SPEC_DIR / "VRTreadmill.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VRTreadmill",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=["vrtread_openxr_layer.dll"],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
