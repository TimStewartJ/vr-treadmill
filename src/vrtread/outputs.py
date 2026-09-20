from __future__ import annotations

import ctypes
import mmap
import platform
import struct
import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class OutputMode(str, Enum):
    XBOX = "xbox"
    OPENXR = "openxr"
    BOTH = "both"

    @property
    def uses_openxr(self) -> bool:
        return self in {OutputMode.OPENXR, OutputMode.BOTH}


class OpenXrFilterMode(str, Enum):
    BALANCED = "balanced"
    STRICT = "strict"
    COMPATIBILITY = "compatibility"


@dataclass(frozen=True, slots=True)
class OutputConfig:
    mode: OutputMode = OutputMode.XBOX
    openxr_filter_mode: OpenXrFilterMode = OpenXrFilterMode.BALANCED


@dataclass(frozen=True, slots=True)
class TreadmillVector:
    x: float
    y: float
    active: bool
    timestamp_ms: int
    openxr_filter_mode: OpenXrFilterMode = OpenXrFilterMode.BALANCED


class TreadmillOutput(Protocol):
    def start(self) -> None:
        ...

    def update(self, vector: TreadmillVector) -> None:
        ...

    def reset(self) -> None:
        ...

    def close(self) -> None:
        ...


OPENXR_MAPPING_NAME = "Local\\VRTreadmillOpenXRState_v1"
OPENXR_MAGIC = 0x4D545256
OPENXR_VERSION = 1
OPENXR_ACTIVE_FLAG = 0x1
OPENXR_STRUCT_FORMAT = "<IIIIQQffQQ"
OPENXR_STRUCT_SIZE = struct.calcsize(OPENXR_STRUCT_FORMAT)
OPENXR_STALE_MS = 250
OPENXR_FILTER_MODE_BALANCED = 0
OPENXR_FILTER_MODE_STRICT = 1
OPENXR_FILTER_MODE_COMPATIBILITY = 2

_OPENXR_FILTER_MODE_VALUES = {
    OpenXrFilterMode.BALANCED: OPENXR_FILTER_MODE_BALANCED,
    OpenXrFilterMode.STRICT: OPENXR_FILTER_MODE_STRICT,
    OpenXrFilterMode.COMPATIBILITY: OPENXR_FILTER_MODE_COMPATIBILITY,
}


def now_ms() -> int:
    if platform.system() == "Windows":
        return int(ctypes.windll.kernel32.GetTickCount64())
    return int(time.monotonic() * 1000)


class VGamepadOutput:
    def __init__(self) -> None:
        self._gamepad = None

    def start(self) -> None:
        try:
            import vgamepad as vg
        except Exception as exc:
            raise RuntimeError(f"Could not import vgamepad: {exc}") from exc

        try:
            self._gamepad = vg.VX360Gamepad()
        except Exception as exc:
            raise RuntimeError(
                "Could not create a virtual Xbox 360 controller. "
                "Confirm ViGEmBus is installed and running."
            ) from exc

    def update(self, vector: TreadmillVector) -> None:
        gamepad = self._gamepad
        if gamepad is None:
            raise RuntimeError("Virtual gamepad is not available.")
        gamepad.left_joystick_float(x_value_float=vector.x, y_value_float=vector.y)
        gamepad.update()

    def reset(self) -> None:
        gamepad = self._gamepad
        if gamepad is None:
            return
        gamepad.reset()
        gamepad.update()

    def close(self) -> None:
        self.reset()
        self._gamepad = None


class OpenXrSharedMemoryOutput:
    def __init__(
        self,
        mapping_name: str = OPENXR_MAPPING_NAME,
        filter_mode: OpenXrFilterMode = OpenXrFilterMode.BALANCED,
    ) -> None:
        self._mapping_name = mapping_name
        self._mapping: mmap.mmap | None = None
        self._seq = 0
        self._filter_mode = filter_mode

    def start(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("OpenXR shared-memory output requires Windows.")
        self._mapping = mmap.mmap(-1, OPENXR_STRUCT_SIZE, tagname=self._mapping_name)
        self._write(
            TreadmillVector(
                x=0.0,
                y=0.0,
                active=False,
                timestamp_ms=now_ms(),
                openxr_filter_mode=self._filter_mode,
            )
        )

    def update(self, vector: TreadmillVector) -> None:
        self._filter_mode = vector.openxr_filter_mode
        self._write(vector)

    def reset(self) -> None:
        if self._mapping is None:
            return
        self._write(
            TreadmillVector(
                x=0.0,
                y=0.0,
                active=False,
                timestamp_ms=now_ms(),
                openxr_filter_mode=self._filter_mode,
            )
        )

    def close(self) -> None:
        if self._mapping is None:
            return
        try:
            self.reset()
        finally:
            self._mapping.close()
            self._mapping = None

    def _write(self, vector: TreadmillVector) -> None:
        mapping = self._mapping
        if mapping is None:
            raise RuntimeError("OpenXR shared-memory output is not available.")

        odd_seq = self._seq + 1
        even_seq = self._seq + 2
        flags = OPENXR_ACTIVE_FLAG if vector.active else 0
        filter_mode = openxr_filter_mode_value(vector.openxr_filter_mode)

        struct.pack_into("<Q", mapping, 16, odd_seq)
        struct.pack_into(
            OPENXR_STRUCT_FORMAT,
            mapping,
            0,
            OPENXR_MAGIC,
            OPENXR_VERSION,
            OPENXR_STRUCT_SIZE,
            flags,
            odd_seq,
            vector.timestamp_ms,
            float(vector.x),
            float(vector.y),
            filter_mode,
            0,
        )
        struct.pack_into("<Q", mapping, 16, even_seq)
        self._seq = even_seq


class CompositeOutput:
    def __init__(self, outputs: list[TreadmillOutput]) -> None:
        if not outputs:
            raise ValueError("CompositeOutput requires at least one output.")
        self._outputs = outputs

    def start(self) -> None:
        started: list[TreadmillOutput] = []
        try:
            for output in self._outputs:
                output.start()
                started.append(output)
        except Exception:
            for output in reversed(started):
                output.close()
            raise

    def update(self, vector: TreadmillVector) -> None:
        errors: list[str] = []
        for output in self._outputs:
            try:
                output.update(vector)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))

    def reset(self) -> None:
        for output in self._outputs:
            output.reset()

    def close(self) -> None:
        for output in reversed(self._outputs):
            output.close()


def create_output(config: OutputConfig) -> TreadmillOutput:
    if config.mode == OutputMode.XBOX:
        return VGamepadOutput()
    if config.mode == OutputMode.OPENXR:
        return OpenXrSharedMemoryOutput(filter_mode=config.openxr_filter_mode)
    if config.mode == OutputMode.BOTH:
        return CompositeOutput([VGamepadOutput(), OpenXrSharedMemoryOutput(filter_mode=config.openxr_filter_mode)])
    raise ValueError(f"Unsupported output mode: {config.mode}")


def parse_output_mode(value: str | OutputMode) -> OutputMode:
    if isinstance(value, OutputMode):
        return value
    try:
        return OutputMode(value)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in OutputMode)
        raise ValueError(f"Output mode must be one of: {allowed}") from exc


def parse_openxr_filter_mode(value: str | OpenXrFilterMode) -> OpenXrFilterMode:
    if isinstance(value, OpenXrFilterMode):
        return value
    try:
        return OpenXrFilterMode(value)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in OpenXrFilterMode)
        raise ValueError(f"OpenXR filter mode must be one of: {allowed}") from exc


def openxr_filter_mode_value(mode: str | OpenXrFilterMode) -> int:
    return _OPENXR_FILTER_MODE_VALUES[parse_openxr_filter_mode(mode)]
