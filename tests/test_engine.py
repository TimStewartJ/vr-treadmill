from __future__ import annotations

import threading
import time

import pytest

from vrtread.capture import CaptureConfig, CaptureMode
from vrtread.engine import TreadmillEngine
from vrtread.motion import TreadmillConfig
from vrtread.outputs import OpenXrFilterMode, OutputConfig, OutputMode, TreadmillVector


class FakeSource:
    description = "fake sensor"

    def __init__(self, counts_per_second: float = 0.0, fail_on_start: bool = False) -> None:
        self.counts_per_second = counts_per_second
        self.fail_on_start = fail_on_start
        self.started = False
        self.stopped = False
        self._last = 0.0

    def start(self) -> None:
        if self.fail_on_start:
            raise RuntimeError("sensor unplugged")
        self.started = True
        self._last = time.perf_counter()

    def read(self) -> float:
        now = time.perf_counter()
        elapsed, self._last = now - self._last, now
        return self.counts_per_second * elapsed

    def stop(self) -> None:
        self.stopped = True


class FakeOutput:
    def __init__(self, fail_on_start: bool = False, fail_after_updates: int | None = None) -> None:
        self.fail_on_start = fail_on_start
        self.fail_after_updates = fail_after_updates
        self.vectors: list[TreadmillVector] = []
        self.configs: list[OutputConfig] = []
        self.closed = threading.Event()
        self.started = False

    def start(self) -> None:
        if self.fail_on_start:
            raise RuntimeError("no ViGEmBus")
        self.started = True

    def configure(self, config: OutputConfig) -> None:
        self.configs.append(config)

    def update(self, vector: TreadmillVector) -> None:
        if self.fail_after_updates is not None and len(self.vectors) >= self.fail_after_updates:
            raise RuntimeError("virtual pad vanished")
        self.vectors.append(vector)

    def reset(self) -> None:
        pass

    def close(self) -> None:
        self.closed.set()


def make_engine(source: FakeSource, output: FakeOutput, **config) -> TreadmillEngine:
    return TreadmillEngine(
        TreadmillConfig(**{"full_speed": 10_000.0, "smoothing_ms": 20.0, "update_hz": 200, **config}),
        OutputConfig(mode=OutputMode.OPENXR),
        CaptureConfig(mode=CaptureMode.RAW),
        output_factory=lambda _config: output,
        source_factory=lambda _config: source,
    )


def wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_pipeline_turns_belt_speed_into_the_expected_stick_value() -> None:
    source, output = FakeSource(counts_per_second=5_000.0), FakeOutput()
    engine = make_engine(source, output)
    engine.start()
    try:
        assert wait_until(lambda: len(output.vectors) > 80)
        status = engine.status()
        assert status.running and status.capture == "fake sensor" and status.output_mode == "openxr"
        assert status.stick_y == pytest.approx(0.5, abs=0.05)
        assert status.velocity == pytest.approx(5_000.0, rel=0.25)
        assert all(vector.active and vector.x == 0.0 for vector in output.vectors)
        stamps = [vector.timestamp_ms for vector in output.vectors]
        assert stamps == sorted(stamps)
    finally:
        engine.stop()
    assert source.stopped and output.closed.is_set()
    assert not engine.is_running and engine.status().stick_y == 0.0


def test_update_rate_is_honoured() -> None:
    source, output = FakeSource(), FakeOutput()
    engine = make_engine(source, output, update_hz=100)
    engine.start()
    time.sleep(1.0)
    engine.stop()
    assert 80 <= len(output.vectors) <= 110


def test_live_tuning_and_output_options_apply_without_restart() -> None:
    source, output = FakeSource(counts_per_second=5_000.0), FakeOutput()
    engine = make_engine(source, output)
    engine.start()
    try:
        assert wait_until(lambda: engine.status().stick_y > 0.4)
        engine.update_config(TreadmillConfig(full_speed=20_000.0, smoothing_ms=20.0, update_hz=200))
        assert wait_until(lambda: abs(engine.status().stick_y - 0.25) < 0.05)

        strict = OutputConfig(mode=OutputMode.OPENXR, openxr_filter_mode=OpenXrFilterMode.STRICT)
        engine.update_output_config(strict)
        assert wait_until(lambda: strict in output.configs)

        with pytest.raises(RuntimeError, match="Stop capture"):
            engine.update_output_config(OutputConfig(mode=OutputMode.XBOX))
        with pytest.raises(RuntimeError, match="Stop capture"):
            engine.update_capture_config(CaptureConfig(mode=CaptureMode.CURSOR))
        with pytest.raises(ValueError):
            engine.update_config(TreadmillConfig(full_speed=-1.0))
    finally:
        engine.stop()


def test_output_failure_while_running_stops_cleanly_and_is_reported() -> None:
    source, output = FakeSource(counts_per_second=1_000.0), FakeOutput(fail_after_updates=5)
    engine = make_engine(source, output)
    engine.start()
    assert output.closed.wait(3.0)
    assert wait_until(lambda: not engine.is_running)
    assert engine.status().last_error == "virtual pad vanished"
    assert source.stopped

    # ...and the engine can be started again afterwards.
    healthy = FakeOutput()
    engine._output_factory = lambda _config: healthy
    engine.start()
    assert wait_until(lambda: len(healthy.vectors) > 5)
    assert engine.status().last_error is None
    engine.stop()


def test_output_that_cannot_start_leaves_nothing_running() -> None:
    source, output = FakeSource(), FakeOutput(fail_on_start=True)
    engine = make_engine(source, output)
    with pytest.raises(RuntimeError, match="no ViGEmBus"):
        engine.start()
    assert not engine.is_running and not source.started and output.closed.is_set()


def test_sensor_that_cannot_start_closes_the_output_again() -> None:
    source, output = FakeSource(fail_on_start=True), FakeOutput()
    engine = make_engine(source, output)
    with pytest.raises(RuntimeError, match="sensor unplugged"):
        engine.start()
    assert not engine.is_running and output.started and output.closed.is_set()


def test_invalid_config_is_rejected_before_anything_starts() -> None:
    source, output = FakeSource(), FakeOutput()
    engine = make_engine(source, output, full_speed=-5.0)
    with pytest.raises(ValueError):
        engine.start()
    assert not output.started and not source.started


def test_double_start_and_double_stop() -> None:
    source, output = FakeSource(), FakeOutput()
    engine = make_engine(source, output)
    engine.start()
    with pytest.raises(RuntimeError, match="already running"):
        engine.start()
    engine.stop()
    engine.stop()
