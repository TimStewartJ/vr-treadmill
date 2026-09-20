from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from vrtread import __version__, openxr
from vrtread.openxr import (
    DLL_FILE_NAME,
    EXPLICIT_KEY,
    IMPLICIT_KEY,
    LAYER_NAME,
    MANIFEST_FILE_NAME,
    describe_session,
    disable_layer,
    enable_layer,
    ensure_layer_enabled,
    find_bundle,
    parse_layer_log,
    query_layer_status,
    self_test,
)

REPO = Path(__file__).resolve().parents[1]
BUILT_LAYER_DIR = REPO / "native" / "openxr_layer" / "bin"
SOURCE_MANIFEST = REPO / "native" / "openxr_layer" / "manifest" / MANIFEST_FILE_NAME


class FakeRegistry:
    def __init__(self) -> None:
        self.keys: dict[str, dict[str, int]] = {}

    def values(self, key: str) -> dict[str, int]:
        return dict(self.keys.get(key, {}))

    def set_value(self, key: str, name: str, value: int) -> None:
        self.keys.setdefault(key, {})[name] = value

    def delete_value(self, key: str, name: str) -> None:
        self.keys.get(key, {}).pop(name, None)


def manifest_payload(name: str = LAYER_NAME) -> str:
    return json.dumps({"file_format_version": "1.0.0", "api_layer": {"name": name, "library_path": f".\\{DLL_FILE_NAME}"}})


@pytest.fixture()
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / DLL_FILE_NAME).write_bytes(b"layer build one")
    (bundle / MANIFEST_FILE_NAME).write_text(manifest_payload(), encoding="utf-8")
    root = tmp_path / "installed"
    monkeypatch.setenv(openxr.BUNDLE_DIR_ENV, str(bundle))
    monkeypatch.setenv(openxr.INSTALL_ROOT_ENV, str(root))
    return bundle, root


def test_shipped_manifest_is_safe_for_every_process() -> None:
    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))["api_layer"]
    assert manifest["name"] == LAYER_NAME
    # Relative to the manifest, with a directory separator: without one the loader searches PATH instead.
    assert manifest["library_path"] == f".\\{DLL_FILE_NAME}"
    # HKCU is shared between 32- and 64-bit processes. A 32-bit game cannot load this x64 DLL, and the
    # loader fails xrCreateInstance when no layer loads. Windows sets this variable only in 32-bit processes
    # (and strips it again for 64-bit children), so they skip the layer instead.
    assert manifest["disable_environment"] == "PROCESSOR_ARCHITEW6432"
    assert manifest["api_version"] == "1.0"
    assert manifest["implementation_version"].isdigit()


def test_enable_deploys_next_to_a_content_hash_and_registers(layout) -> None:
    bundle, root = layout
    registry = FakeRegistry()
    assert query_layer_status(registry).enabled is False
    assert query_layer_status(registry).message == "OpenXR layer: disabled"

    status = enable_layer(registry)
    assert status.enabled and not status.stale and not status.problem
    manifest = status.current_manifest
    assert manifest is not None and manifest.parent.parent == root
    assert manifest.parent.name == find_bundle().deployment_name
    assert manifest.parent.name.startswith(f"{__version__}-")
    assert (manifest.parent / DLL_FILE_NAME).read_bytes() == b"layer build one"
    assert registry.keys[IMPLICIT_KEY] == {str(manifest): 0}

    assert enable_layer(registry).current_manifest == manifest  # idempotent
    assert ensure_layer_enabled(registry).enabled


def test_updating_never_overwrites_a_dll_a_game_may_have_locked(layout) -> None:
    bundle, root = layout
    registry = FakeRegistry()
    first = enable_layer(registry).current_manifest
    assert first is not None

    (bundle / DLL_FILE_NAME).write_bytes(b"layer build two")
    status = query_layer_status(registry)
    assert status.enabled is False and status.stale == (str(first),)
    assert "outdated" in status.message

    second = ensure_layer_enabled(registry).current_manifest
    assert second is not None and second != first
    assert (second.parent / DLL_FILE_NAME).read_bytes() == b"layer build two"
    assert registry.keys[IMPLICIT_KEY] == {str(second): 0}
    assert not first.parent.exists()  # unlocked here, so the old copy is cleaned up


def test_enable_removes_every_other_copy_of_this_layer_and_nothing_else(layout, tmp_path: Path) -> None:
    registry = FakeRegistry()

    dev_build = tmp_path / "dev" / MANIFEST_FILE_NAME
    dev_build.parent.mkdir()
    dev_build.write_text(manifest_payload(), encoding="utf-8")
    renamed_copy = tmp_path / "renamed" / "whatever.json"
    renamed_copy.parent.mkdir()
    renamed_copy.write_text(manifest_payload(), encoding="utf-8")
    dangling = str(tmp_path / "deleted-worktree" / MANIFEST_FILE_NAME)  # file no longer exists
    other_vendor = tmp_path / "other" / "XR_APILAYER_OTHER_vendor.json"
    other_vendor.parent.mkdir()
    other_vendor.write_text(manifest_payload("XR_APILAYER_OTHER_vendor"), encoding="utf-8")
    other_dangling = str(tmp_path / "gone" / "XR_APILAYER_OTHER_gone.json")
    unreadable = tmp_path / "broken" / "broken.json"
    unreadable.parent.mkdir()
    unreadable.write_text("{not json", encoding="utf-8")

    registry.keys[IMPLICIT_KEY] = {str(dev_build): 0, dangling: 0, str(other_vendor): 0, other_dangling: 0, str(unreadable): 1}
    registry.keys[EXPLICIT_KEY] = {str(renamed_copy): 0}

    status = enable_layer(registry)
    assert status.enabled and not status.stale
    assert registry.keys[IMPLICIT_KEY] == {
        str(status.current_manifest): 0,
        str(other_vendor): 0,
        other_dangling: 0,
        str(unreadable): 1,
    }
    assert registry.keys[EXPLICIT_KEY] == {}


def test_disable_unregisters_and_removes_files(layout) -> None:
    _bundle, root = layout
    registry = FakeRegistry()
    enable_layer(registry)
    status = disable_layer(registry)
    assert status.enabled is False and not status.stale
    assert registry.keys[IMPLICIT_KEY] == {}
    assert not root.exists()
    disable_layer(registry)  # idempotent


def test_a_registration_whose_files_were_deleted_is_not_reported_as_enabled(layout) -> None:
    registry = FakeRegistry()
    manifest = enable_layer(registry).current_manifest
    assert manifest is not None
    (manifest.parent / DLL_FILE_NAME).unlink()
    assert query_layer_status(registry).enabled is False
    assert ensure_layer_enabled(registry).enabled  # and it repairs itself


def test_missing_bundle_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(openxr.BUNDLE_DIR_ENV, str(tmp_path / "nothing-here"))
    monkeypatch.setenv(openxr.INSTALL_ROOT_ENV, str(tmp_path / "installed"))
    status = query_layer_status(FakeRegistry())
    assert status.enabled is False and "missing" in status.problem
    with pytest.raises(FileNotFoundError):
        enable_layer(FakeRegistry())


def test_a_bundle_with_the_wrong_manifest_is_rejected(layout) -> None:
    bundle, _root = layout
    (bundle / MANIFEST_FILE_NAME).write_text(manifest_payload("XR_APILAYER_SOMEONE_else"), encoding="utf-8")
    with pytest.raises(RuntimeError):
        enable_layer(FakeRegistry())


LOG = """\
2026-09-20 09:46:14.549 [100] layer 0.2.0 attached: exe='old.exe' app='Old Session' engine='Unity' api=1.0 mode=active
2026-09-20 09:46:14.550 [100] decision action='stale' hand=left filter=balanced -> drive
2026-09-20 10:00:00.000 [200] layer 0.2.0 attached: exe='bonelab.exe' app='BONELAB' engine='Unity' api=1.0 mode=active
2026-09-20 10:00:00.100 [200] action created name='thumbstick' localized='Thumbstick' type=vector2f subactions=2
2026-09-20 10:00:00.200 [200] decision action='thumbstick' hand=left filter=balanced -> drive
2026-09-20 10:00:00.200 [200] decision action='thumbstick' hand=right filter=balanced -> skip:right-hand
2026-09-20 10:00:05.000 [200] engaged: driving action='thumbstick' hand=left treadmill_y=0.600
"""


def test_layer_log_summary(tmp_path: Path) -> None:
    path = tmp_path / "openxr-layer-bonelab.exe.log"
    path.write_text(LOG, encoding="utf-8")
    session = parse_layer_log(path)
    assert session is not None
    assert (session.exe, session.application, session.engine) == ("bonelab.exe", "BONELAB", "Unity")
    assert session.driving == ("thumbstick (left)",)  # only the latest session, only what is driven
    assert session.engaged is True
    assert describe_session(session) == "BONELAB: treadmill drove thumbstick (left)"

    path.write_text(LOG.rsplit("\n", 2)[0] + "\n", encoding="utf-8")  # same session before the first step
    waiting = parse_layer_log(path)
    assert waiting is not None and waiting.engaged is False
    assert "ready to drive thumbstick (left)" in describe_session(waiting)

    path.write_text(
        "2026-09-20 10:00:00.000 [1] layer 0.2.0 attached: exe='vrmonitor.exe' app='' engine='' api=1.0 "
        "mode=passthrough reason=VR runtime helper process\n",
        encoding="utf-8",
    )
    helper = parse_layer_log(path)
    assert helper is not None and "idle" in describe_session(helper)

    path.write_text("nothing useful\n", encoding="utf-8")
    assert parse_layer_log(path) is None
    assert parse_layer_log(tmp_path / "missing.log") is None


needs_built_layer = pytest.mark.skipif(
    sys.platform != "win32" or not (BUILT_LAYER_DIR / DLL_FILE_NAME).is_file(),
    reason="native layer not built (scripts\\build-openxr-layer.ps1)",
)


@needs_built_layer
def test_real_dll_passes_the_loader_style_self_test() -> None:
    ok, detail = self_test(BUILT_LAYER_DIR / DLL_FILE_NAME)
    assert ok, detail


@needs_built_layer
def test_self_test_reports_a_broken_dll(tmp_path: Path) -> None:
    broken = tmp_path / DLL_FILE_NAME
    broken.write_bytes(b"MZ this is not a DLL")
    ok, detail = self_test(broken)
    assert not ok and "could not load" in detail


@needs_built_layer
def test_built_manifest_is_the_shipped_manifest() -> None:
    assert (BUILT_LAYER_DIR / MANIFEST_FILE_NAME).read_bytes() == SOURCE_MANIFEST.read_bytes()
