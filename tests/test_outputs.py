from __future__ import annotations

import mmap
import os
import re
import struct
import sys
import uuid
from pathlib import Path

import pytest

from vrtread import outputs
from vrtread.outputs import (
    OPENXR_MAGIC,
    OPENXR_MAPPING_NAME,
    OPENXR_SEQ_OFFSET,
    OPENXR_STALE_MS,
    OPENXR_STRUCT_FORMAT,
    OPENXR_STRUCT_SIZE,
    OPENXR_VERSION,
    CompositeOutput,
    OpenXrCombineMode,
    OpenXrFilterMode,
    OpenXrPublisherBusyError,
    OpenXrSharedMemoryOutput,
    OutputConfig,
    OutputMode,
    TreadmillVector,
    VGamepadOutput,
    create_output,
    now_ms,
    pack_openxr_state,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="named shared memory is Windows-only")
HEADER = Path(__file__).resolve().parents[1] / "native" / "openxr_layer" / "include" / "vrtread_shared_memory.h"
FIELDS = ("magic", "version", "size", "flags", "seq", "timestamp", "x", "y", "filter", "combine", "options", "pid", "reserved")


def unpack(buffer) -> dict:
    return dict(zip(FIELDS, struct.unpack_from(OPENXR_STRUCT_FORMAT, buffer, 0)))


def unique_name() -> str:
    return f"Local\\VRTreadmillPyTest_{uuid.uuid4().hex}"


def test_python_contract_matches_the_cpp_header() -> None:
    header = HEADER.read_text(encoding="utf-8")

    def constant(name: str) -> int:
        match = re.search(rf"constexpr \w+ {name}\s*=\s*(0x[0-9A-Fa-f]+|\d+)", header)
        assert match, name
        return int(match.group(1), 0)

    assert constant("kMagic") == OPENXR_MAGIC
    assert constant("kVersion") == OPENXR_VERSION
    assert constant("kStaleMs") == OPENXR_STALE_MS
    assert constant("kFlagActive") == outputs.OPENXR_FLAG_ACTIVE
    assert constant("kOptionDriveInactive") == outputs.OPENXR_OPTION_DRIVE_INACTIVE
    assert f"sizeof(SharedState) == {OPENXR_STRUCT_SIZE}" in header
    assert f"'{OPENXR_STRUCT_FORMAT}'" in header
    assert OPENXR_MAPPING_NAME.replace("\\", "\\\\") in header

    for enum_name, mapping in (
        ("kFilter", {"Balanced": OpenXrFilterMode.BALANCED, "Strict": OpenXrFilterMode.STRICT, "Compatibility": OpenXrFilterMode.COMPATIBILITY}),
        ("kCombine", {"MaxMagnitude": OpenXrCombineMode.MAX, "Replace": OpenXrCombineMode.REPLACE, "Add": OpenXrCombineMode.ADD}),
    ):
        table = outputs._FILTER_MODE_VALUES if enum_name == "kFilter" else outputs._COMBINE_MODE_VALUES
        for suffix, member in mapping.items():
            match = re.search(rf"{enum_name}{suffix}\s*=\s*(\d+)", header)
            assert match, suffix
            assert int(match.group(1)) == table[member]


def test_pack_layout_and_sanitising() -> None:
    config = OutputConfig(
        mode=OutputMode.OPENXR,
        openxr_filter_mode=OpenXrFilterMode.COMPATIBILITY,
        openxr_combine_mode=OpenXrCombineMode.ADD,
        openxr_drive_inactive=True,
    )
    block = unpack(pack_openxr_state(10, TreadmillVector(x=0.0, y=0.5, active=True, timestamp_ms=1234), config, 4321))
    assert block == {
        "magic": OPENXR_MAGIC, "version": 2, "size": 64, "flags": 1, "seq": 10, "timestamp": 1234, "x": 0.0,
        "y": 0.5, "filter": 2, "combine": 2, "options": 1, "pid": 4321, "reserved": 0,
    }
    assert struct.calcsize("<IIII") == OPENXR_SEQ_OFFSET

    # The layer validates too, but nothing out of range should ever be published.
    for bad, expected in ((float("nan"), 0.0), (float("inf"), 0.0), (7.0, 1.0), (-7.0, -1.0)):
        packed = unpack(pack_openxr_state(2, TreadmillVector(x=bad, y=bad, active=False, timestamp_ms=1), config, 1))
        assert packed["x"] == expected and packed["y"] == expected
        assert packed["flags"] == 0


def test_create_output_modes() -> None:
    assert isinstance(create_output(OutputConfig(mode=OutputMode.XBOX)), VGamepadOutput)
    assert isinstance(create_output(OutputConfig(mode=OutputMode.OPENXR)), OpenXrSharedMemoryOutput)
    assert isinstance(create_output(OutputConfig(mode=OutputMode.BOTH)), CompositeOutput)
    assert OutputMode.BOTH.uses_openxr and OutputMode.BOTH.uses_xbox
    assert not OutputMode.XBOX.uses_openxr and not OutputMode.OPENXR.uses_xbox


@windows_only
def test_shared_memory_writer_is_a_valid_seqlock_publisher() -> None:
    name = unique_name()
    output = OpenXrSharedMemoryOutput(OutputConfig(mode=OutputMode.OPENXR), mapping_name=name)
    output.start()
    try:
        reader = mmap.mmap(-1, OPENXR_STRUCT_SIZE, tagname=name, access=mmap.ACCESS_READ)
        try:
            idle = unpack(reader)
            assert idle["flags"] == 0 and idle["y"] == 0.0 and idle["seq"] % 2 == 0  # started, not yet capturing

            before = now_ms()
            output.update(TreadmillVector(x=0.0, y=0.625, active=True, timestamp_ms=before))
            block = unpack(reader)
            assert block["seq"] == idle["seq"] + 2 and block["seq"] % 2 == 0
            assert block["flags"] == 1 and block["y"] == 0.625
            assert block["timestamp"] == before
            assert block["pid"] == os.getpid()

            output.configure(OutputConfig(mode=OutputMode.OPENXR, openxr_filter_mode=OpenXrFilterMode.STRICT))
            output.update(TreadmillVector(x=0.0, y=0.25, active=True, timestamp_ms=now_ms()))
            assert unpack(reader)["filter"] == 1

            output.close()  # stopping capture must release the player immediately
            closed = unpack(reader)
            assert closed["flags"] == 0 and closed["y"] == 0.0 and closed["seq"] % 2 == 0
        finally:
            reader.close()
    finally:
        output.close()


@windows_only
def test_second_publisher_is_refused_and_does_not_disturb_the_first() -> None:
    name = unique_name()
    first = OpenXrSharedMemoryOutput(mapping_name=name)
    first.start()
    try:
        first.update(TreadmillVector(x=0.0, y=0.5, active=True, timestamp_ms=now_ms()))
        second = OpenXrSharedMemoryOutput(mapping_name=name)
        with pytest.raises(OpenXrPublisherBusyError):
            second.start()
        second.close()  # safe after a failed start

        reader = mmap.mmap(-1, OPENXR_STRUCT_SIZE, tagname=name, access=mmap.ACCESS_READ)
        try:
            assert unpack(reader)["y"] == 0.5
        finally:
            reader.close()
    finally:
        first.close()

    # Once the first one is gone, the name is free again.
    third = OpenXrSharedMemoryOutput(mapping_name=name)
    third.start()
    third.close()


@windows_only
def test_restarted_app_continues_the_sequence_a_running_game_still_holds() -> None:
    name = unique_name()
    game_view = mmap.mmap(-1, OPENXR_STRUCT_SIZE, tagname=name)  # a game keeps the block alive across app restarts
    try:
        first = OpenXrSharedMemoryOutput(mapping_name=name)
        first.start()
        for _ in range(5):
            first.update(TreadmillVector(x=0.0, y=0.1, active=True, timestamp_ms=now_ms()))
        last_seq = unpack(game_view)["seq"]
        first._release()  # simulate a crash: no clean "inactive" write, handles just vanish
        struct.pack_into("<Q", game_view, OPENXR_SEQ_OFFSET, last_seq + 1)  # ...mid-write, seq left odd

        second = OpenXrSharedMemoryOutput(mapping_name=name)
        second.start()
        try:
            block = unpack(game_view)
            assert block["seq"] % 2 == 0 and block["seq"] > last_seq
            assert block["flags"] == 0
        finally:
            second.close()
    finally:
        game_view.close()


def test_update_before_start_is_an_error_not_a_crash() -> None:
    output = OpenXrSharedMemoryOutput(mapping_name=unique_name())
    with pytest.raises(RuntimeError):
        output.update(TreadmillVector(x=0.0, y=0.0, active=True, timestamp_ms=0))
    output.reset()
    output.close()


class RecordingOutput:
    def __init__(self, fail_on: str = "") -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if name == self.fail_on:
            raise RuntimeError(f"{name} failed")

    def start(self) -> None:
        self._record("start")

    def configure(self, config: OutputConfig) -> None:
        self._record("configure")

    def update(self, vector: TreadmillVector) -> None:
        self._record("update")

    def reset(self) -> None:
        self._record("reset")

    def close(self) -> None:
        self._record("close")


def test_composite_start_failure_closes_what_already_started() -> None:
    good, bad = RecordingOutput(), RecordingOutput(fail_on="start")
    with pytest.raises(RuntimeError):
        CompositeOutput([good, bad]).start()
    assert good.calls == ["start", "close"]


def test_composite_close_reaches_every_output_even_if_one_fails() -> None:
    failing, other = RecordingOutput(fail_on="close"), RecordingOutput()
    composite = CompositeOutput([other, failing])
    with pytest.raises(RuntimeError):
        composite.close()
    assert other.calls == ["close"] and failing.calls == ["close"]


def test_composite_update_reaches_every_output_even_if_one_fails() -> None:
    failing, other = RecordingOutput(fail_on="update"), RecordingOutput()
    with pytest.raises(RuntimeError):
        CompositeOutput([failing, other]).update(TreadmillVector(0.0, 0.0, True, 0))
    assert other.calls == ["update"]


def test_vgamepad_output_drives_the_left_stick(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePad:
        def __init__(self) -> None:
            self.calls: list = []

        def left_joystick_float(self, x_value_float: float, y_value_float: float) -> None:
            self.calls.append(("stick", x_value_float, y_value_float))

        def update(self) -> None:
            self.calls.append("update")

        def reset(self) -> None:
            self.calls.append("reset")

    fake_module = type(sys)("vgamepad")
    pad = FakePad()
    fake_module.VX360Gamepad = lambda: pad
    monkeypatch.setitem(sys.modules, "vgamepad", fake_module)

    output = VGamepadOutput()
    output.start()
    output.update(TreadmillVector(x=0.0, y=float("nan"), active=True, timestamp_ms=0))
    output.update(TreadmillVector(x=0.0, y=0.5, active=True, timestamp_ms=0))
    output.close()
    assert pad.calls == [("stick", 0.0, 0.0), "update", ("stick", 0.0, 0.5), "update", "reset", "update"]
