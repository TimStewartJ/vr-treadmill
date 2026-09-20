"""GUI smoke tests: build the real window (no tray icon, no global hotkey, nothing written to disk or the
registry) and drive its handlers the way clicks would."""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

import pytest

from vrtread import gui
from vrtread.capture import CaptureConfig, CaptureMode, RawMouseDevice
from vrtread.gui import FEEL_PRESETS, TreadmillApp, slider_to_speed, speed_to_slider
from vrtread.openxr import LayerStatus
from vrtread.outputs import OpenXrCombineMode, OpenXrFilterMode, OutputMode
from vrtread.settings import AppSettings, settings_from_dict

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the app is Windows-only")

SENSOR = RawMouseDevice(path=r"\\?\HID#VID_093A&PID_2510#7&1&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}", label="Mouse 1 (USB 093A:2510)")


class FakeSource:
    description = "fake sensor"

    def __init__(self) -> None:
        self._last = time.perf_counter()

    def start(self) -> None:
        self._last = time.perf_counter()

    def read(self) -> float:
        now = time.perf_counter()
        elapsed, self._last = now - self._last, now
        return 32_000.0 * elapsed

    def stop(self) -> None:
        pass


class FakeOutput:
    def start(self) -> None:
        pass

    def configure(self, config) -> None:
        pass

    def update(self, vector) -> None:
        pass

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture(scope="module")
def tk_root():
    # One Tk interpreter for the whole module. Creating several in one process intermittently fails on
    # Windows ("Can't find a usable init.tcl"), which would otherwise turn into randomly un-run tests.
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available in this environment: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture()
def make_app(monkeypatch: pytest.MonkeyPatch, tk_root: tk.Tk):
    layer_calls: list[str] = []
    status = LayerStatus(enabled=True, current_manifest=Path("C:/fake/manifest.json"), stale=())

    def record(name: str):
        def call(*_args, **_kwargs):
            layer_calls.append(name)
            return status
        return call

    monkeypatch.setattr(gui, "ensure_layer_enabled", record("ensure"))
    monkeypatch.setattr(gui, "enable_layer", record("enable"))
    monkeypatch.setattr(gui, "disable_layer", record("disable"))
    monkeypatch.setattr(gui, "query_layer_status", record("query"))
    monkeypatch.setattr(gui, "recent_game_sessions", lambda limit=1: [])
    monkeypatch.setattr(gui, "list_mouse_devices", lambda: [SENSOR])
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: layer_calls.append(f"error:{args[1]}"))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *args, **kwargs: layer_calls.append(f"info:{args[1]}"))

    created: list[TreadmillApp] = []

    def factory(settings: AppSettings) -> TreadmillApp:
        app = TreadmillApp(tk_root, settings, enable_tray=False, enable_hotkeys=False, persist=False)
        app.engine._source_factory = lambda _config: FakeSource()
        app.engine._output_factory = lambda _config: FakeOutput()
        app.layer_calls = layer_calls
        created.append(app)
        return app

    yield factory
    for app in created:
        app.shutdown()
    for child in tk_root.winfo_children():
        child.destroy()


def pump(app: TreadmillApp, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.root.update()
        time.sleep(0.01)


def test_speed_slider_is_a_stable_log_scale() -> None:
    for speed in (1_000.0, 12_340.0, 50_000.0, 400_000.0):
        assert slider_to_speed(speed_to_slider(speed)) == pytest.approx(speed, rel=0.01)
    assert speed_to_slider(1.0) == 0.0 and speed_to_slider(1e9) == 1.0


def test_fresh_install_window(make_app) -> None:
    app = make_app(AppSettings())
    pump(app, 0.2)
    assert app.output_mode.get() == "openxr"
    assert "ensure" in app.layer_calls  # OpenXR output selected: registration is kept healthy at start-up
    assert app.speed_text.get() == "40,000 counts/s"
    assert app.notice.grid_info() == {}
    assert app.device_label.get() == gui.ANY_MOUSE


def test_upgraded_install_shows_the_notice_once_and_keeps_its_values(make_app) -> None:
    legacy = settings_from_dict({"treadmill": {"sensitivity": 0.001, "decay": 0.5, "deadzone": 4, "update_hz": 100}})
    app = make_app(legacy)
    pump(app, 0.2)
    assert app.output_mode.get() == "xbox" and app.capture_mode.get() == "cursor"
    assert "ensure" not in app.layer_calls  # Xbox output: the registry is left alone
    assert app.notice.grid_info() != {}
    # Out of nothing but opening the window, the migrated tuning must not drift (sliders are only views).
    assert app._read_treadmill() == legacy.treadmill

    # Touching one slider changes that one value and nothing else.
    app.smoothing.set(100.0)
    app._on_smoothing_slider()
    assert app._read_treadmill().smoothing_ms == 100.0
    assert app._read_treadmill().full_speed == legacy.treadmill.full_speed
    assert app._read_treadmill().deadzone == legacy.treadmill.deadzone

    app._dismiss_notice()
    assert app.notice.grid_info() == {} and app.settings.migrated_from_legacy is False


def test_start_tune_calibrate_stop(make_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gui, "CALIBRATION_SECONDS", 0.6)
    app = make_app(AppSettings(capture=CaptureConfig(mode=CaptureMode.RAW, device=SENSOR.path)))
    assert app.device_label.get() == SENSOR.label

    app.start()
    assert app.engine.is_running, app.layer_calls
    pump(app, 0.6)
    assert app.engine.status().stick_y == pytest.approx(0.8, abs=0.08)  # 32k of 40k counts/s
    assert "belt" in app.reading_text.get() and str(app.stop_button["state"]) == "normal"

    app.apply_preset("Responsive")
    assert app._read_treadmill().smoothing_ms == FEEL_PRESETS["Responsive"]["smoothing_ms"]
    app.speed_slider.set(speed_to_slider(80_000.0))
    app._on_speed_slider()
    pump(app, 0.5)
    assert app.engine.status().stick_y == pytest.approx(0.4, abs=0.06)  # applied live

    app.calibrate()
    pump(app, 1.2)
    assert app._read_treadmill().full_speed == pytest.approx(40_000.0, rel=0.1)  # walking pace / 0.8
    assert "Calibrated" in app.status_text.get()

    app.output_mode.set(OutputMode.XBOX.value)
    app._on_output_changed()
    assert app.output_mode.get() == "openxr"  # refused while running, and the radio button snaps back
    assert "Stop capture" in app.status_text.get()

    app.stop()
    assert not app.engine.is_running and str(app.start_button["state"]) == "normal"


def test_output_options_round_trip_into_the_engine_config(make_app) -> None:
    app = make_app(AppSettings())
    app.filter_label.set(gui.FILTER_LABELS[OpenXrFilterMode.STRICT])
    app.combine_label.set(gui.COMBINE_LABELS[OpenXrCombineMode.REPLACE])
    app.drive_inactive.set(True)
    app._on_output_changed()
    output = app.settings.output
    assert output.openxr_filter_mode is OpenXrFilterMode.STRICT
    assert output.openxr_combine_mode is OpenXrCombineMode.REPLACE
    assert output.openxr_drive_inactive is True

    app.disable_openxr()
    app.enable_openxr()
    assert app.layer_calls[-2:] == ["disable", "enable"]


def test_calibrate_and_detect_explain_themselves_when_used_at_the_wrong_time(make_app) -> None:
    app = make_app(AppSettings())
    app.calibrate()
    assert any(call.startswith("info:Press Start") for call in app.layer_calls)
    app.start()
    app.detect_sensor()
    assert any(call.startswith("info:Press Stop first") for call in app.layer_calls)
    app.stop()


def test_unplugged_saved_sensor_is_remembered_not_forgotten(make_app) -> None:
    other = r"\\?\HID#VID_1234&PID_5678#1&2&3#{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
    app = make_app(AppSettings(capture=CaptureConfig(mode=CaptureMode.RAW, device=other)))
    assert "not connected" in app.device_label.get()
    assert app._read_capture().device == other
