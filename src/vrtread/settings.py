from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from json import JSONDecodeError
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .capture import CaptureConfig, CaptureMode
from .motion import (
    MAX_DEADZONE,
    MAX_FULL_SPEED,
    MAX_SMOOTHING_MS,
    MAX_UPDATE_HZ,
    MIN_FULL_SPEED,
    MIN_UPDATE_HZ,
    TreadmillConfig,
    from_legacy,
)
from .outputs import OpenXrCombineMode, OpenXrFilterMode, OutputConfig, OutputMode


APP_DIR_NAME = "VRTreadmill"
SETTINGS_FILE_NAME = "settings.json"
SCHEMA_VERSION = 2


@dataclass(slots=True)
class AppSettings:
    start_with_windows: bool = False
    start_minimized: bool = False
    output_mode: OutputMode = OutputMode.OPENXR
    openxr_filter_mode: OpenXrFilterMode = OpenXrFilterMode.BALANCED
    openxr_combine_mode: OpenXrCombineMode = OpenXrCombineMode.MAX
    openxr_drive_inactive: bool = False
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    treadmill: TreadmillConfig = field(default_factory=TreadmillConfig)
    # True when these settings were converted from a v0.1 install. The GUI uses it to explain, once, that
    # OpenXR output and Raw Input capture exist but were deliberately not switched on behind the user's back.
    migrated_from_legacy: bool = False

    @property
    def output(self) -> OutputConfig:
        return OutputConfig(
            mode=self.output_mode,
            openxr_filter_mode=self.openxr_filter_mode,
            openxr_combine_mode=self.openxr_combine_mode,
            openxr_drive_inactive=self.openxr_drive_inactive,
        )


def default_settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / APP_DIR_NAME / SETTINGS_FILE_NAME


def load_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or default_settings_path()
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, JSONDecodeError, OSError, UnicodeDecodeError):
        return AppSettings()
    if not isinstance(raw, dict):
        return AppSettings()
    return settings_from_dict(raw)


def settings_from_dict(raw: dict[str, Any]) -> AppSettings:
    defaults = AppSettings()
    treadmill_raw = raw.get("treadmill")
    if not isinstance(treadmill_raw, dict):
        treadmill_raw = {}

    legacy = _int(raw.get("schema"), 1) < SCHEMA_VERSION and "sensitivity" in treadmill_raw
    if legacy:
        # A v0.1 install: Xbox output through the cursor-lock capture was the only thing that existed.
        # Keep exactly that, including how fast the player moves, so an upgrade changes nothing by itself.
        treadmill = from_legacy(
            sensitivity=_float(treadmill_raw.get("sensitivity"), 0.003),
            decay=_float(treadmill_raw.get("decay"), 0.85),
            deadzone=_float(treadmill_raw.get("deadzone"), 2.0),
            update_hz=_int(treadmill_raw.get("update_hz"), 100),
        )
        return AppSettings(
            start_with_windows=_bool(raw.get("start_with_windows"), defaults.start_with_windows),
            start_minimized=_bool(raw.get("start_minimized"), defaults.start_minimized),
            output_mode=_enum(OutputMode, raw.get("output_mode"), OutputMode.XBOX),
            openxr_filter_mode=_enum(OpenXrFilterMode, raw.get("openxr_filter_mode"), defaults.openxr_filter_mode),
            capture=CaptureConfig(mode=CaptureMode.CURSOR),
            treadmill=treadmill,
            migrated_from_legacy=True,
        )

    base = defaults.treadmill
    treadmill = TreadmillConfig(
        full_speed=_clamped(treadmill_raw.get("full_speed"), base.full_speed, MIN_FULL_SPEED, MAX_FULL_SPEED),
        smoothing_ms=_clamped(treadmill_raw.get("smoothing_ms"), base.smoothing_ms, 0.0, MAX_SMOOTHING_MS),
        deadzone=_clamped(treadmill_raw.get("deadzone"), base.deadzone, 0.0, MAX_DEADZONE),
        update_hz=int(_clamped(treadmill_raw.get("update_hz"), base.update_hz, MIN_UPDATE_HZ, MAX_UPDATE_HZ)),
        invert=_bool(treadmill_raw.get("invert"), base.invert),
        allow_backward=_bool(treadmill_raw.get("allow_backward"), base.allow_backward),
    )

    capture_raw = raw.get("capture")
    if not isinstance(capture_raw, dict):
        capture_raw = {}
    device = capture_raw.get("device")
    capture = CaptureConfig(
        mode=_enum(CaptureMode, capture_raw.get("mode"), defaults.capture.mode),
        device=device if isinstance(device, str) else "",
    )

    return AppSettings(
        start_with_windows=_bool(raw.get("start_with_windows"), defaults.start_with_windows),
        start_minimized=_bool(raw.get("start_minimized"), defaults.start_minimized),
        output_mode=_enum(OutputMode, raw.get("output_mode"), defaults.output_mode),
        openxr_filter_mode=_enum(OpenXrFilterMode, raw.get("openxr_filter_mode"), defaults.openxr_filter_mode),
        openxr_combine_mode=_enum(OpenXrCombineMode, raw.get("openxr_combine_mode"), defaults.openxr_combine_mode),
        openxr_drive_inactive=_bool(raw.get("openxr_drive_inactive"), defaults.openxr_drive_inactive),
        capture=capture,
        treadmill=treadmill,
        migrated_from_legacy=_bool(raw.get("migrated_from_legacy"), False),
    )


def settings_to_dict(settings: AppSettings) -> dict[str, Any]:
    treadmill = settings.treadmill
    return {
        "schema": SCHEMA_VERSION,
        "start_with_windows": settings.start_with_windows,
        "start_minimized": settings.start_minimized,
        "output_mode": settings.output_mode.value,
        "openxr_filter_mode": settings.openxr_filter_mode.value,
        "openxr_combine_mode": settings.openxr_combine_mode.value,
        "openxr_drive_inactive": settings.openxr_drive_inactive,
        "capture": {"mode": settings.capture.mode.value, "device": settings.capture.device},
        "treadmill": {
            "full_speed": treadmill.full_speed,
            "smoothing_ms": treadmill.smoothing_ms,
            "deadzone": treadmill.deadzone,
            "update_hz": treadmill.update_hz,
            "invert": treadmill.invert,
            "allow_backward": treadmill.allow_backward,
        },
        "migrated_from_legacy": settings.migrated_from_legacy,
    }


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    settings_path = path or default_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = settings_to_dict(settings)

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
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _clamped(value: Any, default: float, low: float, high: float) -> float:
    return min(high, max(low, _float(value, default)))


def _enum(enum_type, value: Any, default):
    if not isinstance(value, str):
        return default
    try:
        return enum_type(value)
    except ValueError:
        return default
