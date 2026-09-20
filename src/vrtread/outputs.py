from __future__ import annotations

import ctypes
import math
import os
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

    @property
    def uses_xbox(self) -> bool:
        return self in {OutputMode.XBOX, OutputMode.BOTH}


class OpenXrFilterMode(str, Enum):
    BALANCED = "balanced"
    STRICT = "strict"
    COMPATIBILITY = "compatibility"


class OpenXrCombineMode(str, Enum):
    MAX = "max"          # whichever of treadmill / physical stick is pushed further wins
    REPLACE = "replace"  # treadmill replaces the physical stick while walking
    ADD = "add"          # treadmill + physical stick, clamped


@dataclass(frozen=True, slots=True)
class OutputConfig:
    mode: OutputMode = OutputMode.XBOX
    openxr_filter_mode: OpenXrFilterMode = OpenXrFilterMode.BALANCED
    openxr_combine_mode: OpenXrCombineMode = OpenXrCombineMode.MAX
    openxr_drive_inactive: bool = False


@dataclass(frozen=True, slots=True)
class TreadmillVector:
    x: float
    y: float
    active: bool
    timestamp_ms: int


class TreadmillOutput(Protocol):
    def start(self) -> None:
        ...

    def configure(self, config: OutputConfig) -> None:
        ...

    def update(self, vector: TreadmillVector) -> None:
        ...

    def reset(self) -> None:
        ...

    def close(self) -> None:
        ...


# ---------------------------------------------------------------------------------------------------------
# Shared-memory contract with the OpenXR layer. Keep in sync with
# native/openxr_layer/include/vrtread_shared_memory.h (tests on both sides assert the layout).
# ---------------------------------------------------------------------------------------------------------

OPENXR_MAPPING_NAME = "Local\\VRTreadmillOpenXRState_v2"
OPENXR_MAGIC = 0x4D545256
OPENXR_VERSION = 2
OPENXR_FLAG_ACTIVE = 0x1
OPENXR_OPTION_DRIVE_INACTIVE = 0x1
OPENXR_STRUCT_FORMAT = "<IIIIQQffIIIIQ"
OPENXR_STRUCT_SIZE = struct.calcsize(OPENXR_STRUCT_FORMAT)
OPENXR_SEQ_OFFSET = 16
OPENXR_STALE_MS = 250

_FILTER_MODE_VALUES = {
    OpenXrFilterMode.BALANCED: 0,
    OpenXrFilterMode.STRICT: 1,
    OpenXrFilterMode.COMPATIBILITY: 2,
}
_COMBINE_MODE_VALUES = {
    OpenXrCombineMode.MAX: 0,
    OpenXrCombineMode.REPLACE: 1,
    OpenXrCombineMode.ADD: 2,
}

assert OPENXR_STRUCT_SIZE == 64, "OpenXR shared state must match the 64-byte C++ SharedState"


def _bind_tick_count():
    if platform.system() != "Windows":
        return None
    function = ctypes.WinDLL("kernel32").GetTickCount64
    # ctypes assumes a 32-bit int return value. Left like that, the timestamp goes negative once the PC has
    # been up for 24.8 days; the layer (which reads the real 64-bit counter) then sees every block as coming
    # from the future and ignores the treadmill until the next reboot.
    function.restype = ctypes.c_uint64
    function.argtypes = []
    return function


_get_tick_count_64 = _bind_tick_count()


def now_ms() -> int:
    """Same clock the layer uses for its staleness check (GetTickCount64)."""
    if _get_tick_count_64 is not None:
        return int(_get_tick_count_64())
    return int(time.monotonic() * 1000)


def _finite_unit(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        return 0.0
    return max(-1.0, min(1.0, value))


def pack_openxr_state(seq: int, vector: TreadmillVector, config: OutputConfig, publisher_pid: int) -> bytes:
    options = OPENXR_OPTION_DRIVE_INACTIVE if config.openxr_drive_inactive else 0
    return struct.pack(
        OPENXR_STRUCT_FORMAT,
        OPENXR_MAGIC,
        OPENXR_VERSION,
        OPENXR_STRUCT_SIZE,
        OPENXR_FLAG_ACTIVE if vector.active else 0,
        seq,
        int(vector.timestamp_ms),
        _finite_unit(vector.x),
        _finite_unit(vector.y),
        _FILTER_MODE_VALUES[config.openxr_filter_mode],
        _COMBINE_MODE_VALUES[config.openxr_combine_mode],
        options,
        publisher_pid & 0xFFFFFFFF,
        0,
    )


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

    def configure(self, config: OutputConfig) -> None:
        return None

    def update(self, vector: TreadmillVector) -> None:
        gamepad = self._gamepad
        if gamepad is None:
            raise RuntimeError("Virtual gamepad is not available.")
        gamepad.left_joystick_float(x_value_float=_finite_unit(vector.x), y_value_float=_finite_unit(vector.y))
        gamepad.update()

    def reset(self) -> None:
        gamepad = self._gamepad
        if gamepad is None:
            return
        gamepad.reset()
        gamepad.update()

    def close(self) -> None:
        try:
            self.reset()
        finally:
            self._gamepad = None


class OpenXrPublisherBusyError(RuntimeError):
    pass


class OpenXrSharedMemoryOutput:
    """Publishes the treadmill vector for the OpenXR layer through a named, seqlock-protected block."""

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _PAGE_READWRITE = 0x04
    _FILE_MAP_ALL_ACCESS = 0x000F001F
    _ERROR_ALREADY_EXISTS = 183

    def __init__(self, config: OutputConfig | None = None, mapping_name: str = OPENXR_MAPPING_NAME) -> None:
        self._config = config or OutputConfig(mode=OutputMode.OPENXR)
        self._mapping_name = mapping_name
        self._mutex_name = mapping_name + "_publisher"
        self._kernel32 = None
        self._mutex = None
        self._mapping = None
        self._view = None
        self._buffer = None
        self._seq = 0
        self._pid = os.getpid()

    def start(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("OpenXR shared-memory output requires Windows.")
        if self._buffer is not None:
            return

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateFileMappingW.restype = ctypes.c_void_p
        kernel32.CreateFileMappingW.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_wchar_p,
        ]
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
        kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32 = kernel32

        try:
            # The mapping itself can outlive us (a running game keeps it open), so "already exists" on the
            # mapping says nothing. A separate mutex, only ever held by publishers, does.
            ctypes.set_last_error(0)
            mutex = kernel32.CreateMutexW(None, 0, self._mutex_name)
            if not mutex:
                raise ctypes.WinError(ctypes.get_last_error())
            self._mutex = mutex
            if ctypes.get_last_error() == self._ERROR_ALREADY_EXISTS:
                raise OpenXrPublisherBusyError(
                    "Another copy of VR Treadmill is already publishing OpenXR movement. Close it first."
                )

            mapping = kernel32.CreateFileMappingW(
                self._INVALID_HANDLE_VALUE, None, self._PAGE_READWRITE, 0, OPENXR_STRUCT_SIZE, self._mapping_name
            )
            if not mapping:
                raise ctypes.WinError(ctypes.get_last_error())
            self._mapping = mapping

            view = kernel32.MapViewOfFile(mapping, self._FILE_MAP_ALL_ACCESS, 0, 0, OPENXR_STRUCT_SIZE)
            if not view:
                raise ctypes.WinError(ctypes.get_last_error())
            self._view = view
            self._buffer = (ctypes.c_char * OPENXR_STRUCT_SIZE).from_address(view)

            # A game that is still running may have kept a previous block alive; carry its sequence on.
            (existing_seq,) = struct.unpack_from("<Q", self._buffer, OPENXR_SEQ_OFFSET)
            self._seq = existing_seq + (existing_seq & 1)
        except Exception:
            self._release()
            raise

        self.reset()

    def configure(self, config: OutputConfig) -> None:
        self._config = config

    def update(self, vector: TreadmillVector) -> None:
        self._write(vector)

    def reset(self) -> None:
        if self._buffer is None:
            return
        self._write(TreadmillVector(x=0.0, y=0.0, active=False, timestamp_ms=now_ms()))

    def close(self) -> None:
        if self._buffer is None:
            self._release()
            return
        try:
            self.reset()
        finally:
            self._release()

    def _write(self, vector: TreadmillVector) -> None:
        buffer = self._buffer
        if buffer is None:
            raise RuntimeError("OpenXR shared-memory output is not available.")

        odd_seq = self._seq + 1
        even_seq = self._seq + 2
        # Build the payload first: everything between the two seq writes is time in which readers must wait.
        payload = pack_openxr_state(odd_seq, vector, self._config, self._pid)
        # Seqlock: readers ignore the block while seq is odd and re-check seq after copying.
        struct.pack_into("<Q", buffer, OPENXR_SEQ_OFFSET, odd_seq)
        ctypes.memmove(ctypes.addressof(buffer), payload, OPENXR_STRUCT_SIZE)
        struct.pack_into("<Q", buffer, OPENXR_SEQ_OFFSET, even_seq)
        self._seq = even_seq

    def _release(self) -> None:
        kernel32 = self._kernel32
        self._buffer = None
        if kernel32 is not None:
            if self._view:
                kernel32.UnmapViewOfFile(self._view)
            if self._mapping:
                kernel32.CloseHandle(self._mapping)
            if self._mutex:
                kernel32.CloseHandle(self._mutex)
        self._view = None
        self._mapping = None
        self._mutex = None


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
                try:
                    output.close()
                except Exception:
                    pass
            raise

    def configure(self, config: OutputConfig) -> None:
        for output in self._outputs:
            output.configure(config)

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
        errors: list[str] = []
        for output in self._outputs:
            try:
                output.reset()
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))

    def close(self) -> None:
        errors: list[str] = []
        for output in reversed(self._outputs):
            try:
                output.close()
            except Exception as exc:  # one output failing must not leave the other one live
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))


def create_output(config: OutputConfig) -> TreadmillOutput:
    if config.mode == OutputMode.XBOX:
        return VGamepadOutput()
    if config.mode == OutputMode.OPENXR:
        return OpenXrSharedMemoryOutput(config)
    if config.mode == OutputMode.BOTH:
        return CompositeOutput([VGamepadOutput(), OpenXrSharedMemoryOutput(config)])
    raise ValueError(f"Unsupported output mode: {config.mode}")


def _parse_enum(enum_type, value, label: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise ValueError(f"{label} must be one of: {allowed}") from exc


def parse_output_mode(value: str | OutputMode) -> OutputMode:
    return _parse_enum(OutputMode, value, "Output mode")


def parse_openxr_filter_mode(value: str | OpenXrFilterMode) -> OpenXrFilterMode:
    return _parse_enum(OpenXrFilterMode, value, "OpenXR filter mode")


def parse_openxr_combine_mode(value: str | OpenXrCombineMode) -> OpenXrCombineMode:
    return _parse_enum(OpenXrCombineMode, value, "OpenXR combine mode")
