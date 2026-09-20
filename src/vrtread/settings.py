from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from json import JSONDecodeError
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .engine import TreadmillConfig
from .outputs import OpenXrFilterMode, OutputMode, parse_openxr_filter_mode, parse_output_mode


APP_DIR_NAME = "VRTreadmill"
SETTINGS_FILE_NAME = "settings.json"


@dataclass(slots=True)
class AppSettings:
    start_with_windows: bool = False
    start_minimized: bool = False
    output_mode: OutputMode = OutputMode.OPENXR
    openxr_filter_mode: OpenXrFilterMode = OpenXrFilterMode.BALANCED
    treadmill: TreadmillConfig = field(default_factory=TreadmillConfig)


def default_settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / APP_DIR_NAME / SETTINGS_FILE_NAME


def load_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or default_settings_path()
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, JSONDecodeError, OSError):
        return AppSettings()

    if not isinstance(raw, dict):
        return AppSettings()

    defaults = AppSettings()
    treadmill_raw = raw.get("treadmill", {})
    if not isinstance(treadmill_raw, dict):
        treadmill_raw = {}

    return AppSettings(
        start_with_windows=_bool(raw.get("start_with_windows"), defaults.start_with_windows),
        start_minimized=_bool(raw.get("start_minimized"), defaults.start_minimized),
        output_mode=_output_mode(raw.get("output_mode"), defaults.output_mode),
        openxr_filter_mode=_openxr_filter_mode(raw.get("openxr_filter_mode"), defaults.openxr_filter_mode),
        treadmill=TreadmillConfig(
            sensitivity=_float(treadmill_raw.get("sensitivity"), defaults.treadmill.sensitivity),
            decay=_float(treadmill_raw.get("decay"), defaults.treadmill.decay),
            deadzone=_int(treadmill_raw.get("deadzone"), defaults.treadmill.deadzone),
            update_hz=_int(treadmill_raw.get("update_hz"), defaults.treadmill.update_hz),
        ),
    )


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    settings_path = path or default_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(settings)

    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=settings_path.parent,
        delete=False,
        prefix=f".{SETTINGS_FILE_NAME}.",
        suffix=".tmp",
    ) as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        temp_name = handle.name

    os.replace(temp_name, settings_path)


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _output_mode(value: Any, default: OutputMode) -> OutputMode:
    if not isinstance(value, str):
        return default
    try:
        return parse_output_mode(value)
    except ValueError:
        return default


def _openxr_filter_mode(value: Any, default: OpenXrFilterMode) -> OpenXrFilterMode:
    if not isinstance(value, str):
        return default
    try:
        return parse_openxr_filter_mode(value)
    except ValueError:
        return default
