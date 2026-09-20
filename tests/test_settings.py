from __future__ import annotations

import json
from pathlib import Path

import pytest

from vrtread.capture import CaptureConfig, CaptureMode
from vrtread.motion import TreadmillConfig
from vrtread.outputs import OpenXrCombineMode, OpenXrFilterMode, OutputMode
from vrtread.settings import AppSettings, load_settings, save_settings, settings_from_dict, settings_to_dict


# The settings.json of the v0.1 install this project is actually used with.
LEGACY_INSTALL = {
    "start_with_windows": True,
    "start_minimized": True,
    "treadmill": {"sensitivity": 0.001, "decay": 0.5, "deadzone": 4, "update_hz": 100},
}


def test_fresh_install_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing.json")
    assert settings.output_mode is OutputMode.OPENXR  # needs no driver
    assert settings.capture == CaptureConfig(mode=CaptureMode.RAW, device="")
    assert settings.treadmill == TreadmillConfig()
    assert settings.migrated_from_legacy is False


def test_upgrading_a_v01_install_changes_nothing_by_itself() -> None:
    settings = settings_from_dict(LEGACY_INSTALL)
    # Same output path and same capture method the user has been walking on...
    assert settings.output_mode is OutputMode.XBOX
    assert settings.capture.mode is CaptureMode.CURSOR
    # ...and the same speed and feel, expressed in the new units.
    assert settings.treadmill.full_speed == pytest.approx(50_000.0)
    assert settings.treadmill.smoothing_ms == pytest.approx(14.427, abs=1e-3)
    assert settings.treadmill.deadzone == pytest.approx(0.008)
    assert settings.treadmill.update_hz == 100
    assert settings.treadmill.invert is False
    assert settings.start_with_windows is True
    assert settings.start_minimized is True
    assert settings.migrated_from_legacy is True
    settings.treadmill.validate()


def test_prototype_settings_with_output_mode_are_respected() -> None:
    raw = dict(LEGACY_INSTALL, output_mode="both", openxr_filter_mode="strict")
    settings = settings_from_dict(raw)
    assert settings.output_mode is OutputMode.BOTH
    assert settings.openxr_filter_mode is OpenXrFilterMode.STRICT


def test_migration_happens_once(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(LEGACY_INSTALL), encoding="utf-8")
    migrated = load_settings(path)
    save_settings(migrated, path)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema"] == 2
    assert "sensitivity" not in on_disk["treadmill"]
    assert load_settings(path) == migrated


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings(
        start_with_windows=True,
        start_minimized=True,
        output_mode=OutputMode.BOTH,
        openxr_filter_mode=OpenXrFilterMode.COMPATIBILITY,
        openxr_combine_mode=OpenXrCombineMode.REPLACE,
        openxr_drive_inactive=True,
        capture=CaptureConfig(mode=CaptureMode.RAW, device=r"\\?\HID#VID_093A&PID_2510#7&1#{378de44c}"),
        treadmill=TreadmillConfig(
            full_speed=31_250.0, smoothing_ms=120.0, deadzone=0.02, update_hz=144, invert=True, allow_backward=False
        ),
    )
    save_settings(settings, path)
    assert load_settings(path) == settings
    assert settings_from_dict(settings_to_dict(settings)) == settings


def test_output_config_view() -> None:
    settings = AppSettings(output_mode=OutputMode.OPENXR, openxr_drive_inactive=True)
    assert settings.output.mode is OutputMode.OPENXR
    assert settings.output.openxr_drive_inactive is True


@pytest.mark.parametrize("content", ["", "not json", "[1, 2]", "null", '{"treadmill": 5}', '{"capture": "x"}'])
def test_corrupt_files_fall_back_to_defaults(tmp_path: Path, content: str) -> None:
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    assert load_settings(path).treadmill == TreadmillConfig()


def test_hostile_values_are_clamped_or_defaulted() -> None:
    settings = settings_from_dict(
        {
            "schema": 2,
            "output_mode": "telepathy",
            "openxr_combine_mode": 7,
            "openxr_drive_inactive": "yes",
            "capture": {"mode": "psychic", "device": 42},
            "treadmill": {
                "full_speed": -5,
                "smoothing_ms": 1e12,
                "deadzone": float("nan"),
                "update_hz": True,
                "invert": "true",
            },
        }
    )
    defaults = AppSettings()
    assert settings.output_mode is defaults.output_mode
    assert settings.openxr_combine_mode is defaults.openxr_combine_mode
    assert settings.openxr_drive_inactive is False
    assert settings.capture == CaptureConfig()
    assert settings.treadmill.full_speed == 100.0
    assert settings.treadmill.smoothing_ms == 1000.0
    assert settings.treadmill.deadzone == defaults.treadmill.deadzone
    assert settings.treadmill.update_hz == defaults.treadmill.update_hz
    assert settings.treadmill.invert is False
    settings.treadmill.validate()  # whatever comes out of a settings file must be startable


def test_legacy_garbage_still_migrates_to_something_valid() -> None:
    settings = settings_from_dict({"treadmill": {"sensitivity": 0, "decay": 5, "deadzone": -3, "update_hz": 100000}})
    settings.treadmill.validate()
