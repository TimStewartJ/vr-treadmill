from __future__ import annotations

from pathlib import Path

from vrtread.engine import TreadmillConfig
from vrtread.settings import AppSettings, load_settings, save_settings


def test_load_missing_settings_returns_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing.json")

    assert settings == AppSettings()


def test_load_invalid_json_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{nope", encoding="utf-8")

    settings = load_settings(path)

    assert settings == AppSettings()


def test_save_and_load_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    expected = AppSettings(
        start_with_windows=True,
        start_minimized=True,
        treadmill=TreadmillConfig(sensitivity=0.005, decay=0.7, deadzone=4, update_hz=120),
    )

    save_settings(expected, path)

    assert load_settings(path) == expected
