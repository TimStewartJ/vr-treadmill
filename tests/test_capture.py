from __future__ import annotations

import ctypes
import os
import struct
import sys
import time

import pytest

from vrtread import capture
from vrtread.capture import (
    RAW_HEADER_SIZE,
    RAW_MOUSE_SIZE,
    CaptureConfig,
    CaptureMode,
    CursorRecenterSource,
    RawInputMouseSource,
    create_source,
    hardware_id_of,
    match_device,
    parse_raw_input,
)

SENSOR = r"\\?\HID#VID_093A&PID_2510#7&2a1b3c4d&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
SENSOR_OTHER_PORT = r"\\?\HID#VID_093A&PID_2510#8&99887766&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
DESKTOP_MOUSE = r"\\?\HID#VID_046D&PID_C08B&MI_00#9&1&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"


def raw_input_block(device_type: int, handle: int, flags: int, dx: int, dy: int) -> bytes:
    header_format = "<IIQQ" if ctypes.sizeof(ctypes.c_void_p) == 8 else "<IIII"
    header = struct.pack(header_format, device_type, RAW_HEADER_SIZE + RAW_MOUSE_SIZE, handle, 0)
    return header + struct.pack("<HxxIIiiI", flags, 0, 0, dx, dy, 0)


def test_struct_sizes_match_windows() -> None:
    pointer = ctypes.sizeof(ctypes.c_void_p)
    assert RAW_HEADER_SIZE == (24 if pointer == 8 else 16)  # sizeof(RAWINPUTHEADER)
    assert RAW_MOUSE_SIZE == 24  # sizeof(RAWMOUSE)


def test_parse_relative_mouse_motion() -> None:
    event = parse_raw_input(raw_input_block(capture.RIM_TYPEMOUSE, 0x1234ABCD, 0, 3, -250))
    assert event is not None
    assert (event.device_handle, event.dx, event.dy, event.absolute) == (0x1234ABCD, 3, -250, False)


def test_parse_flags_and_rejects() -> None:
    absolute = parse_raw_input(raw_input_block(capture.RIM_TYPEMOUSE, 1, capture.MOUSE_MOVE_ABSOLUTE, 30000, 30000))
    assert absolute is not None and absolute.absolute  # tablets / remote desktop: caller must ignore these
    assert parse_raw_input(raw_input_block(1, 1, 0, 5, 5)) is None  # keyboard
    assert parse_raw_input(raw_input_block(2, 1, 0, 5, 5)) is None  # HID
    assert parse_raw_input(b"") is None
    assert parse_raw_input(b"\x00" * 10) is None


def test_hardware_id() -> None:
    assert hardware_id_of(SENSOR) == "VID_093A&PID_2510"
    assert hardware_id_of(SENSOR.lower()) == "VID_093A&PID_2510"
    assert hardware_id_of(r"\\?\Root#RDP_MOU#0000#{378de44c}") == ""
    assert hardware_id_of("") == ""


def test_selected_sensor_is_found_again() -> None:
    assert match_device(SENSOR, [DESKTOP_MOUSE, SENSOR]) == SENSOR
    assert match_device(SENSOR.upper(), [SENSOR]) == SENSOR
    # Plugged into a different USB port: the path changed, the hardware did not.
    assert match_device(SENSOR, [DESKTOP_MOUSE, SENSOR_OTHER_PORT]) == SENSOR_OTHER_PORT
    # Two identical sensors: refuse to guess.
    assert match_device(SENSOR, [SENSOR_OTHER_PORT, SENSOR_OTHER_PORT + "x"]) is None
    assert match_device(SENSOR, [DESKTOP_MOUSE]) is None
    assert match_device("", [SENSOR]) is None


def test_source_factory() -> None:
    assert isinstance(create_source(CaptureConfig(mode=CaptureMode.CURSOR)), CursorRecenterSource)
    assert isinstance(create_source(CaptureConfig(mode=CaptureMode.RAW, device=SENSOR)), RawInputMouseSource)


def test_raw_source_only_counts_the_selected_sensor_and_maps_up_to_forward() -> None:
    source = RawInputMouseSource(SENSOR)
    source._resolved = SENSOR  # as start() would after finding the device
    source._on_event(SENSOR, 0, -120)        # belt moving: mouse "up" = forward
    source._on_event(DESKTOP_MOUSE, 0, -999)  # the desk mouse must not move the player
    source._on_event(SENSOR.upper(), 5, -30)
    assert source.read() == 150.0
    assert source.read() == 0.0

    every_mouse = RawInputMouseSource("")
    every_mouse._on_event(DESKTOP_MOUSE, 0, 40)
    every_mouse._on_event("", 0, 2)  # synthesized input
    assert every_mouse.read() == -42.0


@pytest.mark.skipif(sys.platform != "win32", reason="Raw Input is Windows-only")
def test_missing_selected_sensor_is_a_clear_error() -> None:
    source = RawInputMouseSource(r"\\?\HID#VID_FFFF&PID_FFFF#nope#{378de44c}")
    with pytest.raises(RuntimeError, match="not connected"):
        source.start()
    source.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="Raw Input is Windows-only")
def test_device_enumeration_does_not_crash() -> None:
    for device in capture.list_mouse_devices():
        assert device.path and device.label


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("VRTREAD_TEST_DESKTOP") != "1",
    reason="moves the real cursor by a few pixels; opt in with VRTREAD_TEST_DESKTOP=1 on an interactive desktop",
)
def test_raw_input_end_to_end_with_synthesized_mouse_motion() -> None:
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

    def move(dy: int) -> None:
        event = INPUT(0, MOUSEINPUT(0, dy, 0, 0x0001, 0, 0))  # MOUSEEVENTF_MOVE, relative
        assert ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) == 1

    source = RawInputMouseSource("")
    source.start()
    try:
        source.read()
        for _ in range(5):
            move(-4)
            time.sleep(0.02)
        deadline = time.time() + 2.0
        total = 0.0
        while total < 20.0 and time.time() < deadline:
            time.sleep(0.02)
            total += source.read()
        assert total == 20.0
    finally:
        source.stop()
        for _ in range(5):
            move(4)  # put the cursor back
