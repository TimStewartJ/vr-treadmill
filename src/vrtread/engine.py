from __future__ import annotations

import ctypes
import platform
import threading
import time
from dataclasses import dataclass


@dataclass(slots=True)
class TreadmillConfig:
    sensitivity: float = 0.003
    decay: float = 0.85
    deadzone: int = 2
    update_hz: int = 100


@dataclass(slots=True)
class TreadmillStatus:
    running: bool
    stick_y: float
    pending_delta_y: float
    last_error: str | None


class TreadmillEngine:
    def __init__(self, config: TreadmillConfig | None = None) -> None:
        self._config = config or TreadmillConfig()
        self._delta_y = 0.0
        self._delta_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener = None
        self._gamepad = None
        self._stick_y = 0.0
        self._last_error: str | None = None
        self._center_x = 0
        self._center_y = 0
        self._timer_period_active = False

        self._user32 = ctypes.windll.user32 if platform.system() == "Windows" else None
        self._winmm = ctypes.windll.winmm if platform.system() == "Windows" else None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stop_event.is_set()

    def status(self) -> TreadmillStatus:
        with self._delta_lock:
            pending_delta_y = self._delta_y
        with self._state_lock:
            return TreadmillStatus(
                running=self.is_running,
                stick_y=self._stick_y,
                pending_delta_y=pending_delta_y,
                last_error=self._last_error,
            )

    def start(self, config: TreadmillConfig | None = None) -> None:
        if self.is_running:
            raise RuntimeError("The treadmill engine is already running.")
        if platform.system() != "Windows" or self._user32 is None:
            raise RuntimeError("This app requires Windows.")

        if config is not None:
            self._config = config
        self._validate_config(self._config)

        try:
            from pynput import mouse
            import vgamepad as vg
        except Exception as exc:
            raise RuntimeError(f"Could not import required input/gamepad packages: {exc}") from exc

        try:
            self._gamepad = vg.VX360Gamepad()
        except Exception as exc:
            raise RuntimeError(
                "Could not create a virtual Xbox 360 controller. "
                "Confirm ViGEmBus is installed and running."
            ) from exc

        self._center_x = self._user32.GetSystemMetrics(0) // 2
        self._center_y = self._user32.GetSystemMetrics(1) // 2
        self._stick_y = 0.0
        self._last_error = None
        self._stop_event.clear()
        with self._delta_lock:
            self._delta_y = 0.0

        self._user32.SetCursorPos(self._center_x, self._center_y)
        self._listener = mouse.Listener(on_move=self._on_move)
        self._listener.start()

        self._thread = threading.Thread(target=self._run, name="vrtread-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        listener = self._listener
        if listener is not None:
            listener.stop()
            self._listener = None

        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)

        with self._state_lock:
            self._stick_y = 0.0
        self._thread = None
        self._gamepad = None

    def update_config(self, config: TreadmillConfig) -> None:
        self._validate_config(config)
        self._config = config

    def _on_move(self, x: int, y: int) -> None:
        if self._stop_event.is_set() or self._user32 is None:
            return

        dy = y - self._center_y
        if dy == 0:
            return

        with self._delta_lock:
            self._delta_y += dy
        self._user32.SetCursorPos(self._center_x, self._center_y)

    def _run(self) -> None:
        self._begin_timer_precision()
        try:
            while not self._stop_event.is_set():
                config = self._config
                with self._delta_lock:
                    dy = self._delta_y
                    self._delta_y = 0.0

                if abs(dy) < config.deadzone:
                    dy = 0.0

                target = -dy * config.sensitivity
                current = self._stick_y * config.decay + target
                current = max(-1.0, min(1.0, current))

                gamepad = self._gamepad
                if gamepad is None:
                    raise RuntimeError("Virtual gamepad is not available.")

                gamepad.left_joystick_float(x_value_float=0.0, y_value_float=current)
                gamepad.update()

                with self._state_lock:
                    self._stick_y = current

                time.sleep(1.0 / config.update_hz)
        except Exception as exc:
            with self._state_lock:
                self._last_error = str(exc)
        finally:
            self._reset_gamepad()
            self._end_timer_precision()

    def _reset_gamepad(self) -> None:
        gamepad = self._gamepad
        if gamepad is None:
            return
        try:
            gamepad.reset()
            gamepad.update()
        finally:
            with self._state_lock:
                self._stick_y = 0.0

    def _begin_timer_precision(self) -> None:
        if self._winmm is None or self._timer_period_active:
            return
        if self._winmm.timeBeginPeriod(1) == 0:
            self._timer_period_active = True

    def _end_timer_precision(self) -> None:
        if self._winmm is None or not self._timer_period_active:
            return
        self._winmm.timeEndPeriod(1)
        self._timer_period_active = False

    @staticmethod
    def _validate_config(config: TreadmillConfig) -> None:
        if config.sensitivity <= 0:
            raise ValueError("Sensitivity must be greater than 0.")
        if not 0 <= config.decay < 1:
            raise ValueError("Decay must be at least 0 and less than 1.")
        if config.deadzone < 0:
            raise ValueError("Deadzone must be 0 or greater.")
        if config.update_hz <= 0:
            raise ValueError("Update rate must be greater than 0.")

