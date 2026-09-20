"""Treadmill sensor capture.

Two implementations of :class:`MotionSource`:

``RawInputMouseSource`` (recommended)
    Windows Raw Input on a hidden message-only window. Reads unaccelerated counts from one chosen mouse
    device, so the desktop mouse does not move the player, the cursor is not locked, and Windows pointer
    speed / "enhance pointer precision" cannot distort walking speed.

``CursorRecenterSource`` (legacy, v0.1 behaviour)
    Global mouse hook that measures how far the cursor moved and snaps it back to the screen centre.
    Every mouse drives locomotion and pointer acceleration applies. Kept so an upgrade does not change how
    an existing setup feels until the user opts in to Raw Input and recalibrates.

``read()`` returns the forward counts accumulated since the previous call: mouse "up" (negative Y) is forward.
"""

from __future__ import annotations

import ctypes
import platform
import re
import struct
import threading
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


class CaptureMode(str, Enum):
    RAW = "raw"
    CURSOR = "cursor"


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    mode: CaptureMode = CaptureMode.RAW
    device: str = ""  # Raw Input device interface path; empty = every mouse


class MotionSource(Protocol):
    description: str

    def start(self) -> None:
        ...

    def read(self) -> float:
        ...

    def stop(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class RawMouseDevice:
    path: str   # stable device interface path, e.g. \\?\HID#VID_093A&PID_2510#7&...#{378de44c-...}
    label: str  # human readable

    @property
    def hardware_id(self) -> str:
        return hardware_id_of(self.path)


_HARDWARE_ID = re.compile(r"(VID_[0-9A-F]{4}&PID_[0-9A-F]{4})", re.IGNORECASE)


def hardware_id_of(device_path: str) -> str:
    match = _HARDWARE_ID.search(device_path or "")
    return match.group(1).upper() if match else ""


def match_device(selected: str, available: list[str]) -> str | None:
    """Finds the selected device among the connected ones.

    The interface path embeds the USB port, so it changes when the sensor is plugged in somewhere else.
    Fall back to the VID/PID when exactly one connected mouse has it.
    """
    if not selected:
        return None
    lowered = selected.lower()
    for path in available:
        if path.lower() == lowered:
            return path
    wanted = hardware_id_of(selected)
    if wanted:
        candidates = [path for path in available if hardware_id_of(path) == wanted]
        if len(candidates) == 1:
            return candidates[0]
    return None


# ---------------------------------------------------------------------------------------------------------
# RAWINPUT parsing (pure, unit-tested with synthetic buffers)
# ---------------------------------------------------------------------------------------------------------

RIM_TYPEMOUSE = 0
MOUSE_MOVE_ABSOLUTE = 0x0001
_POINTER_SIZE = ctypes.sizeof(ctypes.c_void_p)
# RAWINPUTHEADER: DWORD dwType, DWORD dwSize, HANDLE hDevice, WPARAM wParam
_RAW_HEADER_FORMAT = "<IIQQ" if _POINTER_SIZE == 8 else "<IIII"
RAW_HEADER_SIZE = struct.calcsize(_RAW_HEADER_FORMAT)
# RAWMOUSE: USHORT usFlags, (pad), ULONG buttons, ULONG ulRawButtons, LONG lLastX, LONG lLastY, ULONG extra
_RAW_MOUSE_FORMAT = "<HxxIIiiI"
RAW_MOUSE_SIZE = struct.calcsize(_RAW_MOUSE_FORMAT)


@dataclass(frozen=True, slots=True)
class RawMouseEvent:
    device_handle: int
    dx: int
    dy: int
    absolute: bool


def parse_raw_input(buffer: bytes) -> RawMouseEvent | None:
    """Decodes a RAWINPUT block; returns None for anything that is not relative mouse motion data."""
    if len(buffer) < RAW_HEADER_SIZE + RAW_MOUSE_SIZE:
        return None
    device_type, _size, device_handle, _wparam = struct.unpack_from(_RAW_HEADER_FORMAT, buffer, 0)
    if device_type != RIM_TYPEMOUSE:
        return None
    flags, _buttons, _raw_buttons, dx, dy, _extra = struct.unpack_from(_RAW_MOUSE_FORMAT, buffer, RAW_HEADER_SIZE)
    return RawMouseEvent(
        device_handle=int(device_handle),
        dx=int(dx),
        dy=int(dy),
        absolute=bool(flags & MOUSE_MOVE_ABSOLUTE),
    )


# ---------------------------------------------------------------------------------------------------------
# Win32 plumbing
# ---------------------------------------------------------------------------------------------------------

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_INPUT = 0x00FF
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
HWND_MESSAGE = -3

_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM) if platform.system() == "Windows" else None


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class _RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]


class _Win32:
    """Lazily bound user32/kernel32 entry points with explicit 64-bit safe signatures."""

    _instance: "_Win32 | None" = None

    def __init__(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        user32.RegisterClassW.restype = wintypes.ATOM
        user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DefWindowProcW.restype = _LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.GetMessageW.restype = ctypes.c_int
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = _LRESULT
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(_RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
        user32.GetRawInputData.restype = wintypes.UINT
        user32.GetRawInputData.argtypes = [
            wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(wintypes.UINT), wintypes.UINT,
        ]
        user32.GetRawInputDeviceList.restype = wintypes.UINT
        user32.GetRawInputDeviceList.argtypes = [
            ctypes.POINTER(_RAWINPUTDEVICELIST), ctypes.POINTER(wintypes.UINT), wintypes.UINT,
        ]
        user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
        user32.GetRawInputDeviceInfoW.argtypes = [
            wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(wintypes.UINT),
        ]
        self.user32 = user32
        self.kernel32 = kernel32

    @classmethod
    def get(cls) -> "_Win32":
        if platform.system() != "Windows":
            raise RuntimeError("Raw Input capture requires Windows.")
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def _device_path(win32: _Win32, handle: int) -> str:
    size = wintypes.UINT(0)
    win32.user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(size))
    if size.value == 0 or size.value > 4096:
        return ""
    buffer = ctypes.create_unicode_buffer(size.value + 1)
    copied = win32.user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buffer, ctypes.byref(size))
    if copied in (0, 0xFFFFFFFF):
        return ""
    return buffer.value


def _label_for(path: str, index: int) -> str:
    hardware = hardware_id_of(path)
    if hardware:
        vid, pid = hardware.replace("VID_", "").replace("PID_", "").split("&")
        return f"Mouse {index} (USB {vid}:{pid})"
    if "rdp" in path.lower():
        return f"Mouse {index} (remote desktop)"
    return f"Mouse {index}"


def list_mouse_devices() -> list[RawMouseDevice]:
    win32 = _Win32.get()
    count = wintypes.UINT(0)
    entry_size = ctypes.sizeof(_RAWINPUTDEVICELIST)
    if win32.user32.GetRawInputDeviceList(None, ctypes.byref(count), entry_size) == 0xFFFFFFFF or count.value == 0:
        return []
    entries = (_RAWINPUTDEVICELIST * count.value)()
    found = win32.user32.GetRawInputDeviceList(entries, ctypes.byref(count), entry_size)
    if found == 0xFFFFFFFF:
        return []

    devices: list[RawMouseDevice] = []
    for entry in entries[:found]:
        if entry.dwType != RIM_TYPEMOUSE:
            continue
        path = _device_path(win32, entry.hDevice)
        if path:
            devices.append(RawMouseDevice(path=path, label=_label_for(path, len(devices) + 1)))
    return devices


class RawInputListener:
    """Owns the hidden window and its message loop; hands every relative mouse event to ``on_event``."""

    _CLASS_NAME = "VRTreadmillRawInput"

    def __init__(self, on_event: Callable[[str, int, int], None]) -> None:
        self._on_event = on_event
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._hwnd = None
        self._paths: dict[int, str] = {}
        self._wndproc = None

    def start(self) -> None:
        if self._thread is not None:
            return
        _Win32.get()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="vrtread-rawinput", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            self.stop()
            raise RuntimeError("Raw Input capture did not start in time.")
        if self._error is not None:
            error = self._error
            self.stop()
            raise RuntimeError(f"Could not start Raw Input capture: {error}") from error

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        hwnd = self._hwnd
        if hwnd:
            _Win32.get().user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if threading.current_thread() is not thread:
            thread.join(timeout=3.0)
        self._thread = None

    def _run(self) -> None:
        win32 = _Win32.get()
        user32 = win32.user32
        instance = win32.kernel32.GetModuleHandleW(None)
        class_name = f"{self._CLASS_NAME}_{threading.get_ident()}"
        registered = False
        try:
            self._wndproc = _WNDPROC(self._window_proc)
            window_class = _WNDCLASSW()
            window_class.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
            window_class.hInstance = instance
            window_class.lpszClassName = class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True

            hwnd = user32.CreateWindowExW(0, class_name, "VR Treadmill capture", 0, 0, 0, 0, 0, HWND_MESSAGE, None, instance, None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._hwnd = hwnd

            # Generic desktop / mouse. INPUTSINK: keep receiving while another window (the game) has focus.
            device = _RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)
            if not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(_RAWINPUTDEVICE)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException as exc:  # reported to start() on the calling thread
            self._error = exc
            self._ready.set()
            if self._hwnd:
                user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            if registered:
                user32.UnregisterClassW(class_name, instance)
            return

        self._ready.set()
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self._hwnd = None
            user32.UnregisterClassW(class_name, instance)

    def _window_proc(self, hwnd, message, wparam, lparam):
        user32 = _Win32.get().user32
        try:
            if message == WM_INPUT:
                self._handle_input(lparam)
            elif message == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            elif message == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass  # an exception must never unwind through the Win32 message dispatcher
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _handle_input(self, raw_input_handle: int) -> None:
        win32 = _Win32.get()
        size = wintypes.UINT(0)
        win32.user32.GetRawInputData(raw_input_handle, RID_INPUT, None, ctypes.byref(size), RAW_HEADER_SIZE)
        if size.value == 0 or size.value > 1024:
            return
        buffer = ctypes.create_string_buffer(size.value)
        copied = win32.user32.GetRawInputData(raw_input_handle, RID_INPUT, buffer, ctypes.byref(size), RAW_HEADER_SIZE)
        if copied in (0, 0xFFFFFFFF):
            return
        event = parse_raw_input(buffer.raw[:copied])
        if event is None or event.absolute or (event.dx == 0 and event.dy == 0):
            return
        path = self._paths.get(event.device_handle)
        if path is None:
            # Handle 0 is input synthesized with SendInput (remote desktop tools, tests).
            path = _device_path(win32, event.device_handle) if event.device_handle else ""
            self._paths[event.device_handle] = path
        self._on_event(path, event.dx, event.dy)


class RawInputMouseSource:
    def __init__(self, device: str = "") -> None:
        self._selected = device
        self._resolved: str | None = None
        self._lock = threading.Lock()
        self._forward = 0.0
        self._listener = RawInputListener(self._on_event)
        self.description = "Raw Input"

    def start(self) -> None:
        if self._selected:
            available = [device.path for device in list_mouse_devices()]
            self._resolved = match_device(self._selected, available)
            if self._resolved is None:
                raise RuntimeError(
                    "The selected treadmill sensor is not connected. Plug it in, or choose the sensor again "
                    "with 'Detect treadmill sensor'."
                )
            self.description = "Raw Input (selected sensor)"
        else:
            self._resolved = None
            self.description = "Raw Input (all mice)"
        with self._lock:
            self._forward = 0.0
        self._listener.start()

    def read(self) -> float:
        with self._lock:
            forward = self._forward
            self._forward = 0.0
        return forward

    def stop(self) -> None:
        self._listener.stop()

    def _on_event(self, path: str, dx: int, dy: int) -> None:
        if self._resolved is not None and path.lower() != self._resolved.lower():
            return
        with self._lock:
            self._forward -= dy  # mouse "up" is negative Y


def detect_most_active_mouse(duration_s: float = 3.0, stop_event: threading.Event | None = None) -> RawMouseDevice | None:
    """Listens to every mouse for ``duration_s`` and returns the one that moved the most along Y."""
    totals: dict[str, float] = {}
    lock = threading.Lock()

    def on_event(path: str, dx: int, dy: int) -> None:
        if not path:
            return
        with lock:
            totals[path] = totals.get(path, 0.0) + abs(dy)

    listener = RawInputListener(on_event)
    listener.start()
    try:
        (stop_event or threading.Event()).wait(timeout=duration_s)
    finally:
        listener.stop()

    with lock:
        if not totals:
            return None
        best_path = max(totals, key=lambda key: totals[key])
        if totals[best_path] < 50:  # a bumped desk is not a treadmill
            return None
    for device in list_mouse_devices():
        if device.path.lower() == best_path.lower():
            return device
    return RawMouseDevice(path=best_path, label=_label_for(best_path, 1))


class CursorRecenterSource:
    description = "Legacy cursor lock (all mice)"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._forward = 0.0
        self._listener = None
        self._user32 = None
        self._center_x = 0
        self._center_y = 0
        self._stopped = True

    def start(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("Cursor capture requires Windows.")
        try:
            from pynput import mouse
        except Exception as exc:
            raise RuntimeError(f"Could not import the mouse capture package: {exc}") from exc

        self._user32 = ctypes.windll.user32
        self._center_x = self._user32.GetSystemMetrics(0) // 2
        self._center_y = self._user32.GetSystemMetrics(1) // 2
        with self._lock:
            self._forward = 0.0
        self._stopped = False
        self._user32.SetCursorPos(self._center_x, self._center_y)
        self._listener = mouse.Listener(on_move=self._on_move)
        self._listener.start()

    def read(self) -> float:
        with self._lock:
            forward = self._forward
            self._forward = 0.0
        return forward

    def stop(self) -> None:
        self._stopped = True
        listener = self._listener
        if listener is not None:
            listener.stop()
            self._listener = None

    def _on_move(self, x: int, y: int) -> None:
        if self._stopped or self._user32 is None:
            return
        dy = y - self._center_y
        if dy == 0:
            return
        with self._lock:
            self._forward -= dy
        self._user32.SetCursorPos(self._center_x, self._center_y)


def create_source(config: CaptureConfig) -> MotionSource:
    if config.mode == CaptureMode.CURSOR:
        return CursorRecenterSource()
    return RawInputMouseSource(config.device)
