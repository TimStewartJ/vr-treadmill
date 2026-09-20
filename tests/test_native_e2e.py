"""Cross-language end-to-end tests.

    Python app code  --shared memory-->  layer DLL  <--real Khronos loader--  native "game"  --> mock runtime

The native side is ``vrtread_layer_tests.exe --probe`` (built by scripts\\test-openxr-layer.ps1). It plays a
Unity-style game (one thumbstick action for both hands) and an Unreal-style game (a float forward axis) and
prints what each one read, per frame. The mock hardware holds the left stick at (0.25, 0) and the right stick at
(0.5, 0.125), so anything the treadmill touches that it should not is immediately visible.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from vrtread.capture import CaptureConfig
from vrtread.engine import TreadmillEngine
from vrtread.motion import TreadmillConfig
from vrtread.outputs import OpenXrSharedMemoryOutput, OutputConfig, OutputMode, TreadmillVector, now_ms

REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "native" / "openxr_layer" / "build" / "tests" / "bin" / "vrtread_layer_tests.exe"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not PROBE.is_file(),
    reason="native test harness not built (scripts\\test-openxr-layer.ps1)",
)


def run_probe(mapping: str, tmp_path: Path, frames: int, interval_ms: int) -> subprocess.Popen:
    return subprocess.Popen(
        [str(PROBE), "--probe", "--mapping", mapping, "--frames", str(frames), "--interval-ms", str(interval_ms),
         "--log-dir", str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


BUILT_LAYER = REPO / "native" / "openxr_layer" / "bin" / "vrtread_openxr_layer.dll"


def frames_of(process: subprocess.Popen) -> list[dict]:
    stdout, stderr = process.communicate(timeout=60)
    assert process.returncode == 0, f"probe failed ({process.returncode}):\n{stdout}\n{stderr}"
    records = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    assert records and not any("error" in record for record in records), stdout
    # Every result below is only meaningful if the loader really loaded the DLL that was just built.
    loaded = [record["layer_dll"] for record in records if "layer_dll" in record]
    assert loaded and Path(loaded[0]).resolve() == BUILT_LAYER.resolve(), loaded
    return [record for record in records if "frame" in record]


def test_python_publisher_drives_only_the_left_hand_in_the_game(tmp_path: Path) -> None:
    mapping = f"Local\\VRTreadmillE2E_{uuid.uuid4().hex}"
    output = OpenXrSharedMemoryOutput(OutputConfig(mode=OutputMode.OPENXR), mapping_name=mapping)
    output.start()
    stop = threading.Event()

    def publish() -> None:  # what the engine does at 100 Hz
        while not stop.is_set():
            output.update(TreadmillVector(x=0.0, y=0.625, active=True, timestamp_ms=now_ms()))
            time.sleep(0.01)

    publisher = threading.Thread(target=publish, daemon=True)
    publisher.start()
    try:
        frames = frames_of(run_probe(mapping, tmp_path, frames=25, interval_ms=10))
    finally:
        stop.set()
        publisher.join()
        output.close()

    assert len(frames) == 25
    for frame in frames:
        assert frame["left_y"] == pytest.approx(0.625)   # Unity-style game, left hand: treadmill
        assert frame["forward"] == pytest.approx(0.625)  # Unreal-style float axis: treadmill
        assert frame["left_x"] == pytest.approx(0.25)    # strafe untouched
        assert frame["right_x"] == pytest.approx(0.5)    # right hand untouched
        assert frame["right_y"] == pytest.approx(0.125)

    log = (tmp_path / "openxr-layer-vrtread_layer_tests.exe.log").read_text(encoding="utf-8")
    assert "engaged: driving action=" in log


class LiveProbe:
    """A running probe whose frames can be watched while it plays, so tests react to what the game has
    actually read instead of guessing how long process start-up takes on this machine."""

    def __init__(self, mapping: str, tmp_path: Path, frames: int, interval_ms: int) -> None:
        self.process = run_probe(mapping, tmp_path, frames, interval_ms)
        self.frames: list[dict] = []
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            if line.startswith("{"):
                record = json.loads(line)
                if "frame" in record or "error" in record:
                    with self._lock:
                        self.frames.append(record)

    def count(self, predicate) -> int:
        with self._lock:
            return sum(1 for frame in self.frames if predicate(frame))

    def finish(self) -> list[dict]:
        self.process.wait(timeout=60)
        self._reader.join(timeout=10)
        stderr = self.process.stderr.read() if self.process.stderr else ""
        assert self.process.returncode == 0, f"probe failed ({self.process.returncode}): {stderr}"
        with self._lock:
            assert self.frames and "error" not in self.frames[0], self.frames[:1]
            return list(self.frames)


def publish_until(output: OpenXrSharedMemoryOutput, y: float, condition, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while not condition():
        assert time.time() < deadline, "the game never read the treadmill value"
        output.update(TreadmillVector(x=0.0, y=y, active=True, timestamp_ms=now_ms()))
        time.sleep(0.01)


def test_stopping_capture_releases_the_player_within_the_same_game_session(tmp_path: Path) -> None:
    mapping = f"Local\\VRTreadmillE2E_{uuid.uuid4().hex}"
    output = OpenXrSharedMemoryOutput(mapping_name=mapping)
    output.start()
    probe = LiveProbe(mapping, tmp_path, frames=80, interval_ms=20)
    try:
        publish_until(output, 0.5, lambda: probe.count(lambda frame: frame["left_y"] == pytest.approx(0.5)) >= 10)
    finally:
        output.close()  # user pressed Stop / F8
    frames = probe.finish()

    driven = [frame for frame in frames if frame["left_y"] == pytest.approx(0.5)]
    released = [frame for frame in frames if frame["left_y"] == 0.0]
    assert len(driven) >= 10
    assert len(released) >= 20
    assert len(driven) + len(released) == len(frames)  # never anything in between
    first_released = frames.index(released[0])
    assert all(frame["left_y"] == 0.0 and frame["forward"] == 0.0 for frame in frames[first_released:])  # and it stays released
    assert all(frame["right_y"] == pytest.approx(0.125) for frame in frames)


def test_a_crashed_app_cannot_leave_the_player_walking(tmp_path: Path) -> None:
    mapping = f"Local\\VRTreadmillE2E_{uuid.uuid4().hex}"
    output = OpenXrSharedMemoryOutput(mapping_name=mapping)
    output.start()
    probe = LiveProbe(mapping, tmp_path, frames=80, interval_ms=20)
    try:
        publish_until(output, 0.9, lambda: probe.count(lambda frame: frame["left_y"] == pytest.approx(0.9)) >= 10)
        hung_at = probe.count(lambda frame: True)
        # The app hangs here: the block still says "active, y=0.9" but is never refreshed again.
        frames = probe.finish()
    finally:
        output.close()

    # 250 ms staleness limit at 20 ms per frame = 13 frames; allow a few more for scheduling.
    still_walking = [frame for frame in frames[hung_at:] if frame["left_y"] != 0.0]
    assert len(still_walking) <= 18, len(still_walking)
    assert len(frames) - hung_at > 30  # the game really did keep running long after the hang
    assert all(frame["left_y"] == 0.0 and frame["forward"] == 0.0 for frame in frames[-15:])

class ConstantBelt:
    description = "simulated belt"

    def __init__(self, counts_per_second: float) -> None:
        self.counts_per_second = counts_per_second
        self._last = 0.0

    def start(self) -> None:
        self._last = time.perf_counter()

    def read(self) -> float:
        now = time.perf_counter()
        elapsed, self._last = now - self._last, now
        return self.counts_per_second * elapsed

    def stop(self) -> None:
        pass


def test_whole_pipeline_from_sensor_counts_to_the_value_the_game_reads(tmp_path: Path) -> None:
    mapping = f"Local\\VRTreadmillE2E_{uuid.uuid4().hex}"
    engine = TreadmillEngine(
        TreadmillConfig(full_speed=40_000.0, smoothing_ms=30.0, update_hz=100),
        OutputConfig(mode=OutputMode.OPENXR),
        CaptureConfig(),
        output_factory=lambda config: OpenXrSharedMemoryOutput(config, mapping_name=mapping),
        source_factory=lambda _config: ConstantBelt(30_000.0),  # walking at 75 % of full speed
    )
    engine.start()
    try:
        time.sleep(0.5)  # let the low-pass settle
        frames = frames_of(run_probe(mapping, tmp_path, frames=30, interval_ms=10))
    finally:
        engine.stop()

    assert engine.status().last_error is None
    for frame in frames:
        assert frame["left_y"] == pytest.approx(0.75, abs=0.06)
        assert frame["forward"] == pytest.approx(frame["left_y"])
        assert frame["right_y"] == pytest.approx(0.125)
