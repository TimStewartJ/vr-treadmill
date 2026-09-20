from __future__ import annotations

import ctypes
import platform
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .outputs import OutputConfig, TreadmillOutput, TreadmillVector, create_output, now_ms


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
    output_mode: str
    openxr_filter_mode: str


class TreadmillEngine:
    def __init__(
        self,
        config: TreadmillConfig | None = None,
        output_config: OutputConfig | None = None,
        output_factory: Callable[[OutputConfig], TreadmillOutput] = create_output,
    ) -> None:
        self._config = config or TreadmillConfig()
        self._output_config = output_config or OutputConfig()
        self._output_factory = output_factory
        self._delta_y = 0.0
        self._delta_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener = None
        self._output: TreadmillOutput | None = None
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
                output_mode=self._output_config.mode.value,
                openxr_filter_mode=self._output_config.openxr_filter_mode.value,
            )

    def start(self, config: TreadmillConfig | None = None, output_config: OutputConfig | None = None) -> None:
        if self.is_running:
            raise RuntimeError("The treadmill engine is already running.")
        if platform.system() != "Windows" or self._user32 is None:
            raise RuntimeError("This app requires Windows.")

        if config is not None:
            self._config = config
        if output_config is not None:
            self._output_config = output_config
        self._validate_config(self._config)

        try:
            from pynput import mouse
        except Exception as exc:
            raise RuntimeError(f"Could not import required mouse capture package: {exc}") from exc

        self._stick_y = 0.0
        self._last_error = None
        self._stop_event.clear()
        with self._delta_lock:
            self._delta_y = 0.0

        output = self._output_factory(self._output_config)
        try:
            output.start()
            self._output = output

            self._center_x = self._user32.GetSystemMetrics(0) // 2
            self._center_y = self._user32.GetSystemMetrics(1) // 2
            self._user32.SetCursorPos(self._center_x, self._center_y)
            self._listener = mouse.Listener(on_move=self._on_move)
            self._listener.start()

            self._thread = threading.Thread(target=self._run, name="vrtread-engine", daemon=True)
            self._thread.start()
        except Exception:
            self._stop_event.set()
            listener = self._listener
            if listener is not None:
                listener.stop()
                self._listener = None
            output.close()
            self._output = None
            raise

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

    def update_config(self, config: TreadmillConfig) -> None:
        self._validate_config(config)
        self._config = config

    def update_output_config(self, output_config: OutputConfig) -> None:
        if self.is_running and output_config.mode != self._output_config.mode:
            raise RuntimeError("Stop capture before changing output mode.")
        self._output_config = output_config

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

                output = self._output
                if output is None:
                    raise RuntimeError("Treadmill output is not available.")

                output.update(
                    TreadmillVector(
                        x=0.0,
                        y=current,
                        active=True,
                        timestamp_ms=now_ms(),
                        openxr_filter_mode=self._output_config.openxr_filter_mode,
                    )
                )

                with self._state_lock:
                    self._stick_y = current

                time.sleep(1.0 / config.update_hz)
        except Exception as exc:
            with self._state_lock:
                self._last_error = str(exc)
        finally:
            self._reset_output()
            self._end_timer_precision()

    def _reset_output(self) -> None:
        output = self._output
        if output is None:
            return
        try:
            output.close()
        finally:
            self._output = None
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
