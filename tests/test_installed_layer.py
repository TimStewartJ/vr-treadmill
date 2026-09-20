"""Release validation of the *installed* path, the way a real game meets the layer.

Everything else loads the layer explicitly. Real games never do that: the OpenXR loader discovers the layer
through ``HKCU\\Software\\Khronos\\OpenXR\\1\\ApiLayers\\Implicit``. These tests register the layer for real,
let the real loader find it, and check the result from inside a "game".

They modify the current user's registry, so they are opt-in:

    $env:VRTREAD_TEST_REGISTRY = "1"; python -m pytest tests\\test_installed_layer.py

The affected registry keys are snapshotted first and restored exactly afterwards, even on failure. Files are
deployed to a temporary folder, never to the real %LOCALAPPDATA% location. Do not run this while a VR game
is starting.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from vrtread import openxr
from vrtread.openxr import DLL_FILE_NAME, EXPLICIT_KEY, IMPLICIT_KEY, CurrentUserRegistry, disable_layer, enable_layer
from vrtread.outputs import OpenXrSharedMemoryOutput, TreadmillVector, now_ms

REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "native" / "openxr_layer" / "build" / "tests" / "bin" / "vrtread_layer_tests.exe"
BUILT_LAYER = REPO / "native" / "openxr_layer" / "bin" / DLL_FILE_NAME
FROZEN_APP = REPO / "dist" / "VRTreadmill.exe"
PROBE_32_BIT = REPO / "native" / "openxr_layer" / "build-win32-check" / "bin" / "vrtread_probe32.exe"
XR_ERROR_FILE_ACCESS_ERROR = -32

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("VRTREAD_TEST_REGISTRY") != "1" or not PROBE.is_file(),
    reason="modifies HKCU (restored afterwards); opt in with VRTREAD_TEST_REGISTRY=1 after scripts\\test-openxr-layer.ps1",
)


@contextmanager
def registry_restored_afterwards():
    registry = CurrentUserRegistry()
    keys = (IMPLICIT_KEY, EXPLICIT_KEY)
    before = {key: registry.values(key) for key in keys}
    try:
        yield registry
    finally:
        for key in keys:
            for name in registry.values(key):
                if name not in before[key]:
                    registry.delete_value(key, name)
            for name, value in before[key].items():
                if value >= 0 and registry.values(key).get(name) != value:
                    registry.set_value(key, name, value)
        after = {key: registry.values(key) for key in keys}
        assert after == before, f"registry was not restored: {after} != {before}"


def play(
    mapping: str, tmp_path: Path, extra_env: dict[str, str] | None = None, *, as_32_bit_process: bool = False
) -> tuple[str, list[dict]]:
    """Runs the game with NO layer handed to the loader; returns (layer DLL the loader found, frames)."""
    env = {key: value for key, value in os.environ.items() if key not in ("XR_ENABLE_API_LAYERS", "XR_API_LAYER_PATH")}
    env.update(extra_env or {})
    command = [str(PROBE), "--probe", "--implicit", "--mapping", mapping, "--frames", "20", "--interval-ms", "10",
               "--log-dir", str(tmp_path / "logs")]
    if as_32_bit_process:
        command.append("--simulate-wow64")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, env=env)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert not any("error" in record for record in records), result.stdout
    return records[0]["layer_dll"], [record for record in records if "frame" in record]


@contextmanager
def treadmill_walking(mapping: str, y: float):
    output = OpenXrSharedMemoryOutput(mapping_name=mapping)
    output.start()
    stop = threading.Event()

    def publish() -> None:
        while not stop.is_set():
            output.update(TreadmillVector(x=0.0, y=y, active=True, timestamp_ms=now_ms()))
            time.sleep(0.01)

    thread = threading.Thread(target=publish, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
        output.close()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_driven(frames: list[dict], y: float) -> None:
    assert frames
    wrong = [frame for frame in frames if frame["left_y"] != pytest.approx(y) or frame["forward"] != pytest.approx(y)]
    assert not wrong, f"{len(wrong)} of {len(frames)} frames were not driven: {wrong[:3]}"
    for frame in frames:
        assert frame["right_x"] == pytest.approx(0.5) and frame["right_y"] == pytest.approx(0.125)


def assert_untouched(frames: list[dict]) -> None:
    assert frames
    for frame in frames:
        assert frame["left_y"] == 0.0 and frame["forward"] == 0.0 and frame["right_y"] == pytest.approx(0.125)


def test_registered_layer_is_found_by_the_real_loader_like_in_a_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(openxr.INSTALL_ROOT_ENV, str(tmp_path / "installed"))
    monkeypatch.delenv(openxr.BUNDLE_DIR_ENV, raising=False)
    mapping = f"Local\\VRTreadmillInstalled_{uuid.uuid4().hex}"
    game_env = {"VRTREAD_OPENXR_MAPPING": mapping}

    with registry_restored_afterwards(), treadmill_walking(mapping, 0.55):
        status = enable_layer()
        assert status.enabled and not status.stale, status
        deployed = status.current_manifest.parent / DLL_FILE_NAME

        # 1. A 64-bit game, with nothing but the registry to go on, loads the deployed copy and is driven.
        loaded, frames = play(mapping, tmp_path, game_env)
        assert Path(loaded).resolve() == deployed.resolve()
        assert_driven(frames, 0.55)

        # 2. A 32-bit process has PROCESSOR_ARCHITEW6432 set by Windows. The manifest names it as its
        #    disable_environment, so the loader must skip the (x64-only) layer instead of failing to load it,
        #    which would fail xrCreateInstance and keep the game from starting.
        loaded, frames = play(mapping, tmp_path, game_env, as_32_bit_process=True)
        assert loaded == ""
        assert_untouched(frames)

        #    Windows strips that variable from every 64-bit process, even when a parent passes it explicitly,
        #    so nothing (a launcher, Steam, a script) can switch the layer off in a 64-bit game by accident.
        loaded, frames = play(mapping, tmp_path, {**game_env, "PROCESSOR_ARCHITEW6432": "AMD64"})
        assert Path(loaded).resolve() == deployed.resolve()
        assert_driven(frames, 0.55)

        # 3. The in-DLL kill switch: loaded, but passes everything through.
        loaded, frames = play(mapping, tmp_path, {**game_env, "VRTREAD_OPENXR_BYPASS": "1"})
        assert Path(loaded).resolve() == deployed.resolve()
        assert_untouched(frames)

        # 4. Disabled: the next game start does not load it at all.
        assert disable_layer().enabled is False
        loaded, frames = play(mapping, tmp_path, game_env)
        assert loaded == ""
        assert_untouched(frames)


@pytest.mark.skipif(not FROZEN_APP.is_file(), reason="dist\\VRTreadmill.exe not built (scripts\\build-exe.ps1)")
def test_packaged_app_installs_the_exact_dll_that_was_tested(tmp_path: Path) -> None:
    install_root = tmp_path / "installed"
    env = {**os.environ, openxr.INSTALL_ROOT_ENV: str(install_root)}
    env.pop(openxr.BUNDLE_DIR_ENV, None)
    mapping = f"Local\\VRTreadmillInstalled_{uuid.uuid4().hex}"

    with registry_restored_afterwards() as registry, treadmill_walking(mapping, 0.35):
        enabled = subprocess.run([str(FROZEN_APP), "--enable-openxr-layer"], env=env, timeout=120)
        assert enabled.returncode == 0

        ours = [name for name in registry.values(IMPLICIT_KEY) if str(install_root).lower() in name.lower()]
        assert len(ours) == 1, registry.values(IMPLICIT_KEY)
        deployed = Path(ours[0]).parent / DLL_FILE_NAME
        # The DLL inside the shipped exe is byte-for-byte the one the native test-suite just validated.
        assert sha256(deployed) == sha256(BUILT_LAYER)

        loaded, frames = play(mapping, tmp_path, {"VRTREAD_OPENXR_MAPPING": mapping})
        assert Path(loaded).resolve() == deployed.resolve()
        assert_driven(frames, 0.35)

        disabled = subprocess.run([str(FROZEN_APP), "--disable-openxr-layer"], env=env, timeout=120)
        assert disabled.returncode == 0
        assert not [name for name in registry.values(IMPLICIT_KEY) if str(install_root).lower() in name.lower()]
        assert not install_root.exists()


def run_32_bit_game() -> dict:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("XR_", "VRTREAD_"))}
    result = subprocess.run([str(PROBE_32_BIT)], capture_output=True, text=True, timeout=60, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(next(line for line in result.stdout.splitlines() if line.startswith("{")))


@pytest.mark.skipif(not PROBE_32_BIT.is_file(), reason="32-bit client not built (scripts\\build-win32-check.ps1)")
def test_a_genuine_32_bit_game_still_starts_with_the_layer_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real WOW64 process with the real 32-bit Khronos loader, and nothing but the registry to go on.

    HKCU is shared between 32- and 64-bit processes, so a 32-bit game sees the manifest of this x64-only layer.
    The loader cannot load the DLL there, and when no API layer could be loaded it fails xrCreateInstance:
    the game would not start in VR at all.
    """
    monkeypatch.setenv(openxr.INSTALL_ROOT_ENV, str(tmp_path / "installed"))
    monkeypatch.delenv(openxr.BUNDLE_DIR_ENV, raising=False)

    with registry_restored_afterwards() as registry:
        status = enable_layer()
        assert status.enabled and not status.stale

        outcome = run_32_bit_game()
        assert outcome["pointer_bits"] == 32
        assert outcome["processor_architew6432"] == "AMD64"  # set by Windows itself, in every 32-bit process
        assert outcome["create_result"] == 0                 # the game starts
        assert outcome["layer_loaded"] is False              # and the layer stayed out of it

        # Counter-experiment: the very same DLL behind a manifest WITHOUT disable_environment=PROCESSOR_ARCHITEW6432
        # (which is what the unreleased prototype registered) really does break the 32-bit game.
        unprotected = tmp_path / "unprotected"
        unprotected.mkdir()
        (unprotected / DLL_FILE_NAME).write_bytes(BUILT_LAYER.read_bytes())
        manifest = json.loads((status.current_manifest).read_text(encoding="utf-8"))
        manifest["api_layer"]["disable_environment"] = "VRTREAD_TEST_VARIABLE_THAT_IS_NEVER_SET"
        unprotected_manifest = unprotected / openxr.MANIFEST_FILE_NAME
        unprotected_manifest.write_text(json.dumps(manifest), encoding="utf-8")
        registry.set_value(IMPLICIT_KEY, str(unprotected_manifest), 0)

        broken = run_32_bit_game()
        assert broken["create_result"] == XR_ERROR_FILE_ACCESS_ERROR
        assert broken["layer_loaded"] is False
