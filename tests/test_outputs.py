from __future__ import annotations

import mmap
import platform
import struct
import sys
import uuid
from types import SimpleNamespace

import pytest

from vrtread.outputs import (
    OPENXR_ACTIVE_FLAG,
    OPENXR_MAGIC,
    OPENXR_FILTER_MODE_BALANCED,
    OPENXR_FILTER_MODE_COMPATIBILITY,
    OPENXR_FILTER_MODE_STRICT,
    OPENXR_STRUCT_FORMAT,
    OPENXR_STRUCT_SIZE,
    OPENXR_VERSION,
    OpenXrFilterMode,
    OpenXrSharedMemoryOutput,
    OutputConfig,
    OutputMode,
    TreadmillVector,
    VGamepadOutput,
    create_output,
    now_ms,
)


def test_create_output_uses_requested_mode() -> None:
    assert isinstance(create_output(OutputConfig(OutputMode.XBOX)), VGamepadOutput)
    assert isinstance(create_output(OutputConfig(OutputMode.OPENXR)), OpenXrSharedMemoryOutput)


def test_vgamepad_output_updates_virtual_left_stick(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, float | None, float | None]] = []

    class FakeGamepad:
        def left_joystick_float(self, *, x_value_float: float, y_value_float: float) -> None:
            calls.append(("stick", x_value_float, y_value_float))

        def update(self) -> None:
            calls.append(("update", None, None))

        def reset(self) -> None:
            calls.append(("reset", None, None))

    monkeypatch.setitem(sys.modules, "vgamepad", SimpleNamespace(VX360Gamepad=FakeGamepad))

    output = VGamepadOutput()
    output.start()
    output.update(TreadmillVector(x=0.25, y=0.75, active=True, timestamp_ms=now_ms()))
    output.close()

    assert ("stick", 0.25, 0.75) in calls
    assert ("reset", None, None) in calls


@pytest.mark.skipif(platform.system() != "Windows", reason="Named mmap is Windows-specific")
def test_openxr_shared_memory_output_writes_seqlock_state() -> None:
    mapping_name = f"Local\\VRTreadmillTest_{uuid.uuid4()}"
    output = OpenXrSharedMemoryOutput(mapping_name=mapping_name)
    output.start()

    try:
        output.update(
            TreadmillVector(
                x=0.25,
                y=0.75,
                active=True,
                timestamp_ms=now_ms(),
                openxr_filter_mode=OpenXrFilterMode.STRICT,
            )
        )
        reader = mmap.mmap(-1, OPENXR_STRUCT_SIZE, tagname=mapping_name)
        try:
            values = struct.unpack_from(OPENXR_STRUCT_FORMAT, reader, 0)
        finally:
            reader.close()
    finally:
        output.close()

    magic, version, size, flags, seq, timestamp_ms, x, y, reserved0, reserved1 = values
    assert magic == OPENXR_MAGIC
    assert version == OPENXR_VERSION
    assert size == OPENXR_STRUCT_SIZE
    assert flags & OPENXR_ACTIVE_FLAG
    assert seq % 2 == 0
    assert timestamp_ms > 0
    assert x == pytest.approx(0.25)
    assert y == pytest.approx(0.75)
    assert reserved0 == OPENXR_FILTER_MODE_STRICT
    assert reserved1 == 0


@pytest.mark.skipif(platform.system() != "Windows", reason="Named mmap is Windows-specific")
@pytest.mark.parametrize(
    ("filter_mode", "expected"),
    [
        (OpenXrFilterMode.BALANCED, OPENXR_FILTER_MODE_BALANCED),
        (OpenXrFilterMode.STRICT, OPENXR_FILTER_MODE_STRICT),
        (OpenXrFilterMode.COMPATIBILITY, OPENXR_FILTER_MODE_COMPATIBILITY),
    ],
)
def test_openxr_shared_memory_output_writes_filter_mode(
    filter_mode: OpenXrFilterMode,
    expected: int,
) -> None:
    mapping_name = f"Local\\VRTreadmillTest_{uuid.uuid4()}"
    output = OpenXrSharedMemoryOutput(mapping_name=mapping_name, filter_mode=filter_mode)
    output.start()

    try:
        output.update(
            TreadmillVector(
                x=0.0,
                y=0.5,
                active=True,
                timestamp_ms=now_ms(),
                openxr_filter_mode=filter_mode,
            )
        )
        reader = mmap.mmap(-1, OPENXR_STRUCT_SIZE, tagname=mapping_name)
        try:
            values = struct.unpack_from(OPENXR_STRUCT_FORMAT, reader, 0)
        finally:
            reader.close()
    finally:
        output.close()

    assert values[8] & 0xFF == expected
