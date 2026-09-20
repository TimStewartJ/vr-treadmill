from __future__ import annotations

import ctypes
import platform
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .capture import CaptureConfig, MotionSource, create_source
from .motion import MotionModel, TreadmillConfig
from .outputs import OutputConfig, TreadmillOutput, TreadmillVector, create_output, now_ms

__all__ = ["TreadmillConfig", "TreadmillEngine", "TreadmillStatus"]


@dataclass(slots=True)
class TreadmillStatus:
    running: bool
    stick_y: float
    velocity: float  # raw belt speed, sensor counts per second
    last_error: str | None
    output_mode: str
    capture: str


class TreadmillEngine:
    """Sensor counts -> motion model -> output(s), on a fixed-rate worker thread."""

    def __init__(
        self,
        config: TreadmillConfig | None = None,
        output_config: OutputConfig | None = None,
        capture_config: CaptureConfig | None = None,
        output_factory: Callable[[OutputConfig], TreadmillOutput] = create_output,
        source_factory: Callable[[CaptureConfig], MotionSource] = create_source,
    ) -> None:
        self._config = config or TreadmillConfig()
        self._output_config = output_config or OutputConfig()
        self._capture_config = capture_config or CaptureConfig()
        self._output_factory = output_factory
        self._source_factory = source_factory

        self._model = MotionModel(self._config)
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._source: MotionSource | None = None
        self._output: TreadmillOutput | None = None
        self._stick_y = 0.0
        self._velocity = 0.0
        self._capture_description = ""
        self._last_error: str | None = None
        self._pending_output_config: OutputConfig | None = None
        self._timer_period_active = False
        self._winmm = ctypes.windll.winmm if platform.system() == "Windows" else None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stop_event.is_set()

    def status(self) -> TreadmillStatus:
        with self._state_lock:
            return TreadmillStatus(
                running=self.is_running,
                stick_y=self._stick_y,
                velocity=self._velocity,
                last_error=self._last_error,
                output_mode=self._output_config.mode.value,
                capture=self._capture_description,
            )

    def start(
        self,
        config: TreadmillConfig | None = None,
        output_config: OutputConfig | None = None,
        capture_config: CaptureConfig | None = None,
    ) -> None:
        if self.is_running:
            raise RuntimeError("The treadmill engine is already running.")
        self.stop()  # reap a worker that ended on its own (error) before starting another

        if config is not None:
            self._config = config
        if output_config is not None:
            self._output_config = output_config
        if capture_config is not None:
            self._capture_config = capture_config
        self._config.validate()

        self._model = MotionModel(self._config)
        with self._state_lock:
            self._stick_y = 0.0
            self._velocity = 0.0
            self._last_error = None
            self._pending_output_config = None
        self._stop_event.clear()

        output = self._output_factory(self._output_config)
        source: MotionSource | None = None
        try:
            output.start()
            output.configure(self._output_config)
            source = self._source_factory(self._capture_config)
            source.start()
        except Exception:
            self._stop_event.set()
            if source is not None:
                _quietly(source.stop)
            _quietly(output.close)
            raise

        self._output = output
        self._source = source
        with self._state_lock:
            self._capture_description = source.description
        self._thread = threading.Thread(target=self._run, name="vrtread-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)
        self._thread = None
        with self._state_lock:
            self._stick_y = 0.0
            self._velocity = 0.0

    def update_config(self, config: TreadmillConfig) -> None:
        config.validate()
        self._config = config
        self._model.configure(config)

    def update_output_config(self, output_config: OutputConfig) -> None:
        if self.is_running and output_config.mode != self._output_config.mode:
            raise RuntimeError("Stop capture before changing output mode.")
        self._output_config = output_config
        with self._state_lock:
            self._pending_output_config = output_config

    def update_capture_config(self, capture_config: CaptureConfig) -> None:
        if self.is_running and capture_config != self._capture_config:
            raise RuntimeError("Stop capture before changing the treadmill sensor.")
        self._capture_config = capture_config

    def _run(self) -> None:
        source = self._source
        output = self._output
        self._begin_timer_precision()
        try:
            if source is None or output is None:
                raise RuntimeError("Treadmill engine started without a sensor or an output.")
            last = time.perf_counter()
            deadline = last
            source.read()  # discard whatever accumulated while starting up
            while not self._stop_event.is_set():
                period = 1.0 / self._config.update_hz
                deadline += period
                delay = deadline - time.perf_counter()
                if delay < -period:  # fell behind (system sleep, debugger): do not burst to catch up
                    deadline = time.perf_counter()
                elif delay > 0 and self._stop_event.wait(delay):
                    break

                now = time.perf_counter()
                dt = now - last
                last = now
                stick = self._model.step(source.read(), dt)

                with self._state_lock:
                    pending = self._pending_output_config
                    self._pending_output_config = None
                if pending is not None:
                    output.configure(pending)

                output.update(TreadmillVector(x=0.0, y=stick, active=True, timestamp_ms=now_ms()))
                with self._state_lock:
                    self._stick_y = stick
                    self._velocity = self._model.velocity
        except Exception as exc:
            with self._state_lock:
                self._last_error = str(exc) or exc.__class__.__name__
        finally:
            self._stop_event.set()
            if source is not None:
                _quietly(source.stop)
            if output is not None:
                _quietly(output.close)  # publishes "inactive", so the layer lets go immediately
            self._source = None
            self._output = None
            with self._state_lock:
                self._stick_y = 0.0
                self._velocity = 0.0
            self._end_timer_precision()

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


def _quietly(action: Callable[[], None]) -> None:
    try:
        action()
    except Exception:
        pass
