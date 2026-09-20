from __future__ import annotations

import argparse
import ctypes
import math
import queue
import statistics
import sys
import threading
import time
import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, ttk

from . import __version__
from .capture import CaptureConfig, CaptureMode, RawMouseDevice, detect_most_active_mouse, list_mouse_devices, match_device
from .driver import VIGEMBUS_INSTALL_URL, open_vigembus_download, query_vigembus_status
from .engine import TreadmillEngine
from .motion import TreadmillConfig, suggest_full_speed
from .openxr import describe_session, disable_layer, doctor, enable_layer, ensure_layer_enabled, query_layer_status, recent_game_sessions
from .outputs import OpenXrCombineMode, OpenXrFilterMode, OutputConfig, OutputMode
from .settings import AppSettings, load_settings, save_settings
from .startup import current_startup_command, set_startup_enabled


# "Feel" presets. They deliberately leave the walking-speed scale alone: that depends on the sensor and how
# it is mounted, and is what Calibrate is for.
FEEL_PRESETS = {
    "Smooth": {"smoothing_ms": 160.0, "deadzone": 0.02},
    "Balanced": {"smoothing_ms": 80.0, "deadzone": 0.01},
    "Responsive": {"smoothing_ms": 30.0, "deadzone": 0.005},
}

OUTPUT_LABELS = {
    OutputMode.OPENXR: "OpenXR (recommended) - works inside OpenXR games with no per-game setup",
    OutputMode.XBOX: "Xbox controller - for games that accept a gamepad next to VR controllers",
    OutputMode.BOTH: "Both",
}
FILTER_LABELS = {
    OpenXrFilterMode.BALANCED: "Balanced - the game's left-stick movement (recommended)",
    OpenXrFilterMode.STRICT: "Strict - only actions that are clearly named as movement",
    OpenXrFilterMode.COMPATIBILITY: "Compatibility - also guess from names when a game hides its bindings",
}
COMBINE_LABELS = {
    OpenXrCombineMode.MAX: "Whichever is pushed further: treadmill or thumbstick (recommended)",
    OpenXrCombineMode.REPLACE: "Treadmill replaces the thumbstick while walking",
    OpenXrCombineMode.ADD: "Add treadmill and thumbstick together",
}
ANY_MOUSE = "Any mouse (not recommended: your desk mouse will move you too)"

_SPEED_SLIDER_MIN_LOG = 3.0   # 1,000 counts/s
_SPEED_SLIDER_SPAN_LOG = 2.7  # ... to ~500,000 counts/s
CALIBRATION_SECONDS = 5.0


def speed_to_slider(full_speed: float) -> float:
    return min(1.0, max(0.0, (math.log10(max(1.0, full_speed)) - _SPEED_SLIDER_MIN_LOG) / _SPEED_SLIDER_SPAN_LOG))


def slider_to_speed(position: float) -> float:
    return round(10 ** (_SPEED_SLIDER_MIN_LOG + _SPEED_SLIDER_SPAN_LOG * min(1.0, max(0.0, position))), -1)


class TreadmillApp:
    def __init__(
        self,
        root: tk.Tk,
        settings: AppSettings | None = None,
        *,
        enable_tray: bool = True,
        enable_hotkeys: bool = True,
        persist: bool = True,
    ) -> None:
        self.root = root
        self.settings = settings or load_settings()
        self.persist = persist
        self.engine = TreadmillEngine(self.settings.treadmill, self.settings.output, self.settings.capture)
        self.hotkeys = None
        self.tray_icon = None
        self.exiting = False
        self.devices: list[RawMouseDevice] = []
        self._busy = False  # a detection or calibration thread is running
        self._loading = True  # suppress change handlers while widgets are being populated
        self._refresh_ticks = 0
        # Worker, hotkey and tray threads never touch Tk. They queue callbacks that the Tk thread runs.
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()

        treadmill = self.settings.treadmill
        self.speed_slider = tk.DoubleVar(value=speed_to_slider(treadmill.full_speed))
        self.smoothing = tk.DoubleVar(value=treadmill.smoothing_ms)
        self.deadzone_percent = tk.DoubleVar(value=treadmill.deadzone * 100.0)
        self.update_hz = tk.DoubleVar(value=treadmill.update_hz)
        self.invert = tk.BooleanVar(value=treadmill.invert)
        self.allow_backward = tk.BooleanVar(value=treadmill.allow_backward)
        self.speed_text = tk.StringVar()
        self.smoothing_text = tk.StringVar()
        self.deadzone_text = tk.StringVar()
        self.update_hz_text = tk.StringVar()

        self.output_mode = tk.StringVar(value=self.settings.output_mode.value)
        self.filter_label = tk.StringVar(value=FILTER_LABELS[self.settings.openxr_filter_mode])
        self.combine_label = tk.StringVar(value=COMBINE_LABELS[self.settings.openxr_combine_mode])
        self.drive_inactive = tk.BooleanVar(value=self.settings.openxr_drive_inactive)
        self.capture_mode = tk.StringVar(value=self.settings.capture.mode.value)
        self.device_label = tk.StringVar(value=ANY_MOUSE)
        self.start_with_windows = tk.BooleanVar(value=self.settings.start_with_windows)
        self.start_minimized = tk.BooleanVar(value=self.settings.start_minimized)

        self.driver_status_text = tk.StringVar(value="Xbox driver: checking...")
        self.openxr_status_text = tk.StringVar(value="OpenXR layer: checking...")
        self.recent_game_text = tk.StringVar(value="")
        self.status_text = tk.StringVar(value="Stopped")
        self.reading_text = tk.StringVar(value="Stick +0.000    belt 0 counts/s")

        # Source of truth for tuning. The sliders are only views of it: a slider replaces the one field the user
        # actually moved, so opening the window can never round or clamp values it was merely displaying.
        self._treadmill = treadmill

        self.root.title(f"VR Treadmill {__version__}")
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.bind("<Unmap>", self._on_unmap)
        self._build()
        self._loading = False
        self._update_tuning_labels()
        self.refresh_devices()
        self.refresh_driver_status()
        self._prepare_openxr()
        if enable_hotkeys:
            self._start_hotkey_listener()
        if enable_tray:
            self._start_tray_icon()
        self._drain_ui_queue()
        self._refresh()

    # ------------------------------------------------------------------------------------------------ build

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Walk on your treadmill to move in VR.", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")

        self.notice = ttk.Frame(frame, padding=(8, 6), relief="groove")
        ttk.Label(
            self.notice,
            wraplength=470,
            justify="left",
            text=(
                "Updated from an earlier version. Nothing was changed for you: it still drives an Xbox controller "
                "with the cursor locked, at the same speed as before.\n"
                "New: choose OpenXR on the Output tab to move in OpenXR games without per-game setup, and "
                "Raw Input on the Sensor tab to free the cursor (then press Calibrate)."
            ),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(self.notice, text="Got it", command=self._dismiss_notice).grid(row=0, column=1, sticky="ne", padx=(8, 0))
        if self.settings.migrated_from_legacy:
            self.notice.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        controls = ttk.Frame(frame)
        controls.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.start_button = ttk.Button(controls, text="Start", command=self.start)
        self.stop_button = ttk.Button(controls, text="Stop (F8)", command=self.stop, state="disabled")
        self.start_button.grid(row=0, column=0, padx=(0, 6))
        self.stop_button.grid(row=0, column=1, padx=(0, 12))
        ttk.Label(controls, textvariable=self.status_text).grid(row=0, column=2, sticky="w")

        self.meter = tk.Canvas(frame, width=480, height=22, background="white", highlightthickness=1)
        self.meter.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(frame, textvariable=self.reading_text, font=("Consolas", 9)).grid(row=4, column=0, sticky="w")

        notebook = ttk.Notebook(frame)
        notebook.grid(row=5, column=0, sticky="nsew", pady=(10, 0))
        frame.rowconfigure(5, weight=1)
        notebook.add(self._build_output_tab(notebook), text="Output")
        notebook.add(self._build_sensor_tab(notebook), text="Sensor")
        notebook.add(self._build_tuning_tab(notebook), text="Tuning")
        notebook.add(self._build_app_tab(notebook), text="App")

    def _build_output_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=10)
        tab.columnconfigure(0, weight=1)
        for row, mode in enumerate((OutputMode.OPENXR, OutputMode.XBOX, OutputMode.BOTH)):
            ttk.Radiobutton(
                tab, text=OUTPUT_LABELS[mode], value=mode.value, variable=self.output_mode, command=self._on_output_changed
            ).grid(row=row, column=0, sticky="w")

        openxr = ttk.LabelFrame(tab, text="OpenXR", padding=8)
        openxr.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        openxr.columnconfigure(0, weight=1)
        ttk.Label(openxr, textvariable=self.openxr_status_text, wraplength=450, justify="left").grid(row=0, column=0, sticky="w")
        ttk.Label(openxr, textvariable=self.recent_game_text, wraplength=450, justify="left", foreground="#355").grid(
            row=1, column=0, sticky="w", pady=(2, 0)
        )
        buttons = ttk.Frame(openxr)
        buttons.grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Button(buttons, text="Enable", command=self.enable_openxr).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Disable", command=self.disable_openxr).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(buttons, text="Diagnostics...", command=self.show_diagnostics).grid(row=0, column=2)

        ttk.Label(openxr, text="Which game input to drive:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        filter_box = ttk.Combobox(openxr, textvariable=self.filter_label, values=list(FILTER_LABELS.values()), state="readonly")
        filter_box.grid(row=4, column=0, sticky="ew")
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._on_output_changed())
        ttk.Label(openxr, text="When you also push the thumbstick:").grid(row=5, column=0, sticky="w", pady=(8, 0))
        combine_box = ttk.Combobox(openxr, textvariable=self.combine_label, values=list(COMBINE_LABELS.values()), state="readonly")
        combine_box.grid(row=6, column=0, sticky="ew")
        combine_box.bind("<<ComboboxSelected>>", lambda _event: self._on_output_changed())
        ttk.Checkbutton(
            openxr,
            text="Keep walking when the left controller is asleep or put down (advanced)",
            variable=self.drive_inactive,
            command=self._on_output_changed,
        ).grid(row=7, column=0, sticky="w", pady=(8, 0))

        xbox = ttk.LabelFrame(tab, text="Xbox controller", padding=8)
        xbox.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(xbox, textvariable=self.driver_status_text).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Button(xbox, text="Refresh", command=self.refresh_driver_status).grid(row=1, column=0, sticky="w", pady=(6, 0), padx=(0, 6))
        ttk.Button(xbox, text="Get the ViGEmBus driver", command=self.open_driver_install).grid(row=1, column=1, sticky="w", pady=(6, 0))
        return tab

    def _build_sensor_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=10)
        tab.columnconfigure(0, weight=1)
        ttk.Radiobutton(
            tab,
            text="Raw Input (recommended) - reads one sensor directly; your cursor stays free",
            value=CaptureMode.RAW.value,
            variable=self.capture_mode,
            command=self._on_capture_changed,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            tab,
            text="Legacy cursor lock - how v0.1 worked; every mouse moves you and the cursor is pinned",
            value=CaptureMode.CURSOR.value,
            variable=self.capture_mode,
            command=self._on_capture_changed,
        ).grid(row=1, column=0, sticky="w")

        sensor = ttk.LabelFrame(tab, text="Treadmill sensor (Raw Input)", padding=8)
        sensor.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        sensor.columnconfigure(0, weight=1)
        self.device_box = ttk.Combobox(sensor, textvariable=self.device_label, state="readonly")
        self.device_box.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.device_box.bind("<<ComboboxSelected>>", lambda _event: self._on_capture_changed())
        self.detect_button = ttk.Button(sensor, text="Detect treadmill sensor", command=self.detect_sensor)
        self.detect_button.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(sensor, text="Refresh list", command=self.refresh_devices).grid(row=1, column=1, sticky="e", pady=(6, 0))
        ttk.Label(
            sensor,
            wraplength=450,
            justify="left",
            foreground="#555",
            text="Detect: press the button, then walk (or push the belt) for three seconds without touching your normal mouse.",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        direction = ttk.LabelFrame(tab, text="Direction", padding=8)
        direction.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(
            direction, text="Invert (tick this if walking forward moves you backward)", variable=self.invert, command=self._on_direction_changed
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            direction, text="Allow backward movement", variable=self.allow_backward, command=self._on_direction_changed
        ).grid(row=1, column=0, sticky="w")
        return tab

    def _build_tuning_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=10)
        tab.columnconfigure(1, weight=1)

        calibrate = ttk.Frame(tab)
        calibrate.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.calibrate_button = ttk.Button(calibrate, text="Calibrate walking speed", command=self.calibrate)
        self.calibrate_button.grid(row=0, column=0, sticky="w")
        ttk.Label(
            calibrate, foreground="#555", wraplength=300, justify="left",
            text="Press Start, walk at a comfortable pace, then press this and keep walking for five seconds.",
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))

        self._add_slider(
            tab, 1, "Pace for full speed", "How fast the belt has to move for the game to get full stick. Lower = you move faster.",
            self.speed_slider, self.speed_text, 0.0, 1.0, self._on_speed_slider,
        )
        self._add_slider(
            tab, 3, "Smoothing", "Evens out steps. Higher is smoother but starts and stops later.",
            self.smoothing, self.smoothing_text, 0.0, 300.0, self._on_smoothing_slider,
        )
        self._add_slider(
            tab, 5, "Noise filter", "Belt movement slower than this share of full speed is ignored.",
            self.deadzone_percent, self.deadzone_text, 0.0, 10.0, self._on_deadzone_slider,
        )
        self._add_slider(
            tab, 7, "Update rate", "How often movement is sent. It no longer affects speed; 100 Hz is plenty.",
            self.update_hz, self.update_hz_text, 30.0, 200.0, self._on_update_hz_slider,
        )

        presets = ttk.Frame(tab)
        presets.grid(row=9, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(presets, text="Feel:").grid(row=0, column=0, padx=(0, 8))
        for column, name in enumerate(FEEL_PRESETS, start=1):
            ttk.Button(presets, text=name, command=lambda preset=name: self.apply_preset(preset)).grid(row=0, column=column, padx=(0, 6))
        return tab

    def _build_app_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=10)
        ttk.Checkbutton(tab, text="Start with Windows", variable=self.start_with_windows, command=self._on_start_with_windows_changed).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Checkbutton(tab, text="Start minimized to tray", variable=self.start_minimized, command=self._on_start_minimized_changed).grid(
            row=1, column=0, sticky="w"
        )
        ttk.Button(tab, text="Hide to tray", command=self.hide_to_tray).grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(tab, text="F8 stops capture from anywhere, including inside VR.", foreground="#555").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Label(tab, text=f"VR Treadmill {__version__}", foreground="#555").grid(row=4, column=0, sticky="w", pady=(10, 0))
        return tab

    def _add_slider(self, parent, row, label, description, variable, value_text, low, high, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(10, 0))
        ttk.Scale(parent, variable=variable, from_=low, to=high, command=lambda _value: command()).grid(
            row=row, column=1, sticky="ew", padx=8, pady=(10, 0)
        )
        ttk.Label(parent, textvariable=value_text, width=16, anchor="e").grid(row=row, column=2, sticky="e", pady=(10, 0))
        ttk.Label(parent, text=description, wraplength=450, justify="left", foreground="#555").grid(row=row + 1, column=0, columnspan=3, sticky="w")

    # ------------------------------------------------------------------------------------------ read widgets

    def _read_treadmill(self) -> TreadmillConfig:
        return self._treadmill

    def _read_output(self) -> OutputConfig:
        filters = {label: mode for mode, label in FILTER_LABELS.items()}
        combines = {label: mode for mode, label in COMBINE_LABELS.items()}
        return OutputConfig(
            mode=OutputMode(self.output_mode.get()),
            openxr_filter_mode=filters.get(self.filter_label.get(), OpenXrFilterMode.BALANCED),
            openxr_combine_mode=combines.get(self.combine_label.get(), OpenXrCombineMode.MAX),
            openxr_drive_inactive=bool(self.drive_inactive.get()),
        )

    def _read_capture(self) -> CaptureConfig:
        device = ""
        for candidate in self.devices:
            if candidate.label == self.device_label.get():
                device = candidate.path
        if not device and self.device_label.get() != ANY_MOUSE:
            device = self.settings.capture.device  # selected sensor is currently unplugged: keep remembering it
        return CaptureConfig(mode=CaptureMode(self.capture_mode.get()), device=device)

    def _save_settings(self) -> None:
        self.settings.treadmill = self._read_treadmill()
        output = self._read_output()
        self.settings.output_mode = output.mode
        self.settings.openxr_filter_mode = output.openxr_filter_mode
        self.settings.openxr_combine_mode = output.openxr_combine_mode
        self.settings.openxr_drive_inactive = output.openxr_drive_inactive
        self.settings.capture = self._read_capture()
        self.settings.start_with_windows = bool(self.start_with_windows.get())
        self.settings.start_minimized = bool(self.start_minimized.get())
        if self.persist:
            save_settings(self.settings)

    # ---------------------------------------------------------------------------------------- start / stop

    def start(self) -> None:
        try:
            treadmill, output, capture = self._read_treadmill(), self._read_output(), self._read_capture()
            treadmill.validate()
            if output.mode.uses_openxr:
                self.openxr_status_text.set(ensure_layer_enabled().message)
            self._save_settings()
            self.engine.start(treadmill, output, capture)
        except Exception as exc:
            messagebox.showerror("Could not start VR Treadmill", str(exc))
            return
        self._set_running(True)
        locked = " - cursor is locked to the screen centre" if capture.mode is CaptureMode.CURSOR else ""
        self.status_text.set(f"Running{locked}")

    def stop(self) -> None:
        self.engine.stop()
        self._set_running(False)
        self.status_text.set("Stopped")

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    # ------------------------------------------------------------------------------------- change handlers

    def _on_speed_slider(self) -> None:
        self._change_tuning(full_speed=slider_to_speed(float(self.speed_slider.get())))

    def _on_smoothing_slider(self) -> None:
        self._change_tuning(smoothing_ms=round(float(self.smoothing.get()), 1))

    def _on_deadzone_slider(self) -> None:
        self._change_tuning(deadzone=round(float(self.deadzone_percent.get()) / 100.0, 4))

    def _on_update_hz_slider(self) -> None:
        self._change_tuning(update_hz=int(round(float(self.update_hz.get()))))

    def _on_direction_changed(self) -> None:
        self._change_tuning(invert=bool(self.invert.get()), allow_backward=bool(self.allow_backward.get()))

    def _change_tuning(self, **changes) -> None:
        if self._loading:
            return
        try:
            config = replace(self._treadmill, **changes)
            config.validate()
            self._treadmill = config
            if self.engine.is_running:
                self.engine.update_config(config)
            self._save_settings()
        except Exception as exc:
            self.status_text.set(f"Tuning error: {exc}")
        self._update_tuning_labels()

    def _on_output_changed(self) -> None:
        if self._loading:
            return
        try:
            output = self._read_output()
            self.engine.update_output_config(output)
            self._save_settings()
            if output.mode.uses_openxr:
                self._prepare_openxr()
        except Exception as exc:
            self.output_mode.set(self.settings.output_mode.value)
            self.status_text.set(str(exc))

    def _on_capture_changed(self) -> None:
        if self._loading:
            return
        try:
            self.engine.update_capture_config(self._read_capture())
            self._save_settings()
        except Exception as exc:
            self.capture_mode.set(self.settings.capture.mode.value)
            self._select_device_label(self.settings.capture.device)
            self.status_text.set(str(exc))

    def apply_preset(self, name: str) -> None:
        preset = FEEL_PRESETS[name]
        self.smoothing.set(preset["smoothing_ms"])
        self.deadzone_percent.set(preset["deadzone"] * 100.0)
        self._change_tuning(smoothing_ms=preset["smoothing_ms"], deadzone=preset["deadzone"])
        self.status_text.set(f"{name} feel applied")

    def _update_tuning_labels(self) -> None:
        config = self._treadmill
        self.speed_text.set(f"{config.full_speed:,.0f} counts/s")
        self.smoothing_text.set(f"{config.smoothing_ms:.0f} ms")
        self.deadzone_text.set(f"{config.deadzone * 100.0:.1f} %")
        self.update_hz_text.set(f"{config.update_hz} Hz")

    def _dismiss_notice(self) -> None:
        self.settings.migrated_from_legacy = False
        self.notice.grid_remove()
        self._save_settings()

    # --------------------------------------------------------------------------------------------- sensor

    def refresh_devices(self) -> None:
        try:
            self.devices = list_mouse_devices()
        except Exception:
            self.devices = []
        self.device_box.configure(values=[ANY_MOUSE] + [device.label for device in self.devices])
        self._select_device_label(self.settings.capture.device)

    def _select_device_label(self, device_path: str) -> None:
        if not device_path:
            self.device_label.set(ANY_MOUSE)
            return
        matched = match_device(device_path, [device.path for device in self.devices])
        for device in self.devices:
            if matched is not None and device.path == matched:
                self.device_label.set(device.label)
                return
        self.device_label.set("Saved sensor (not connected right now)")

    def detect_sensor(self) -> None:
        if self._busy:
            return
        if self.engine.is_running:
            messagebox.showinfo("Detect treadmill sensor", "Press Stop first, then detect the sensor.")
            return
        self._busy = True
        self.detect_button.configure(state="disabled")
        self.status_text.set("Detecting: walk on the treadmill now, and leave your normal mouse alone...")

        def work() -> None:
            try:
                found, error = detect_most_active_mouse(3.0), None
            except Exception as exc:
                found, error = None, exc
            self._post(lambda: self._detection_finished(found, error))

        threading.Thread(target=work, name="vrtread-detect", daemon=True).start()

    def _detection_finished(self, found: RawMouseDevice | None, error: Exception | None) -> None:
        self._busy = False
        self.detect_button.configure(state="normal")
        if error is not None:
            self.status_text.set(f"Detection failed: {error}")
            return
        if found is None:
            self.status_text.set("No movement seen. Is the sensor plugged in and touching the belt?")
            return
        self.settings.capture = CaptureConfig(mode=CaptureMode.RAW, device=found.path)
        self.capture_mode.set(CaptureMode.RAW.value)
        self.refresh_devices()
        self._save_settings()
        self.status_text.set(f"Treadmill sensor set to {self.device_label.get()}")

    def calibrate(self) -> None:
        if self._busy:
            return
        if not self.engine.is_running:
            messagebox.showinfo("Calibrate walking speed", "Press Start and begin walking at a comfortable pace, then press Calibrate.")
            return
        self._busy = True
        self.calibrate_button.configure(state="disabled")
        self.status_text.set("Calibrating: keep walking at a comfortable pace...")

        def work() -> None:
            samples: list[float] = []
            deadline = time.time() + CALIBRATION_SECONDS
            while time.time() < deadline and self.engine.is_running:
                samples.append(abs(self.engine.status().velocity))
                time.sleep(0.02)
            self._post(lambda: self._calibration_finished(samples))

        threading.Thread(target=work, name="vrtread-calibrate", daemon=True).start()

    def _calibration_finished(self, samples: list[float]) -> None:
        self._busy = False
        self.calibrate_button.configure(state="normal")
        moving = [value for value in samples if value > 0.0]
        # Walking is bursty (each step); the median of the moving samples is a steady estimate of pace.
        if len(moving) < max(10, len(samples) // 3):
            self.status_text.set("Calibration saw too little movement. Walk steadily for the whole five seconds.")
            return
        try:
            full_speed = suggest_full_speed(statistics.median(moving))
        except ValueError as exc:
            self.status_text.set(str(exc))
            return
        self.speed_slider.set(speed_to_slider(full_speed))
        self._change_tuning(full_speed=full_speed)
        self.status_text.set(f"Calibrated: a comfortable pace now gives 80 % stick ({full_speed:,.0f} counts/s = 100 %)")

    # --------------------------------------------------------------------------------------------- OpenXR

    def _prepare_openxr(self) -> None:
        """Keeps the layer registration healthy whenever OpenXR output is selected."""
        try:
            if self._read_output().mode.uses_openxr:
                self.openxr_status_text.set(ensure_layer_enabled().message)
            else:
                self.openxr_status_text.set(query_layer_status().message)
        except Exception as exc:
            self.openxr_status_text.set(f"OpenXR layer: {exc}")
        self._refresh_recent_game()

    def enable_openxr(self) -> None:
        try:
            self.openxr_status_text.set(enable_layer().message)
        except Exception as exc:
            messagebox.showerror("Could not enable the OpenXR layer", str(exc))

    def disable_openxr(self) -> None:
        try:
            self.openxr_status_text.set(disable_layer().message)
            if self._read_output().mode.uses_openxr:
                self.status_text.set("OpenXR layer disabled. It is enabled again when you press Start with OpenXR output.")
        except Exception as exc:
            messagebox.showerror("Could not disable the OpenXR layer", str(exc))

    def _refresh_recent_game(self) -> None:
        try:
            sessions = recent_game_sessions(limit=1)
        except Exception:
            sessions = []
        self.recent_game_text.set(f"Last OpenXR game - {describe_session(sessions[0])}" if sessions else "")

    def show_diagnostics(self) -> None:
        try:
            _healthy, lines = doctor()
        except Exception as exc:
            lines = [f"Diagnostics failed: {exc}"]
        window = tk.Toplevel(self.root)
        window.title("OpenXR diagnostics")
        text = tk.Text(window, width=110, height=22, wrap="word")
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")
        text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

    # ----------------------------------------------------------------------------------------- Xbox driver

    def refresh_driver_status(self) -> None:
        self.driver_status_text.set(query_vigembus_status().message.replace("Driver Status", "Xbox driver"))

    def open_driver_install(self) -> None:
        if not open_vigembus_download():
            messagebox.showerror("Could not open the driver page", f"Open this URL manually: {VIGEMBUS_INSTALL_URL}")

    # --------------------------------------------------------------------------------------- startup items

    def _on_start_with_windows_changed(self) -> None:
        enabled = bool(self.start_with_windows.get())
        previous = (self.settings.start_with_windows, self.settings.start_minimized)
        try:
            if enabled:
                self.start_minimized.set(True)
                set_startup_enabled(True, current_startup_command(minimized=True))
            else:
                set_startup_enabled(False)
            self._save_settings()
            self.status_text.set("Startup settings saved")
        except Exception as exc:
            self.start_with_windows.set(previous[0])
            self.start_minimized.set(previous[1])
            messagebox.showerror("Could not update Windows startup", str(exc))

    def _on_start_minimized_changed(self) -> None:
        if self.start_with_windows.get() and not self.start_minimized.get():
            self.start_with_windows.set(False)
            self._on_start_with_windows_changed()
        else:
            self._save_settings()
            self.status_text.set("Startup settings saved")

    # ------------------------------------------------------------------------------- tray / hotkey / window

    def _post(self, callback) -> None:
        """Thread-safe: schedules `callback` to run on the Tk thread."""
        self._ui_queue.put(callback)

    def _drain_ui_queue(self) -> None:
        if self.exiting:
            return
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as exc:
                self.status_text.set(f"Error: {exc}")
            if self.exiting:  # the callback was Exit
                return
        self.root.after(30, self._drain_ui_queue)

    def _emergency_stop(self) -> None:
        # Runs on the hotkey thread. Stop the engine right here rather than asking the UI thread to do it:
        # F8 has to work even if the window is busy or a dialog is open.
        self.engine.stop()
        self._post(self.stop)

    def _start_hotkey_listener(self) -> None:
        try:
            from pynput import keyboard

            self.hotkeys = keyboard.GlobalHotKeys({"<f8>": self._emergency_stop})
            self.hotkeys.start()
        except Exception as exc:
            self.status_text.set(f"Stopped - F8 hotkey unavailable: {exc}")

    def _start_tray_icon(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception as exc:
            self.status_text.set(f"Stopped - tray unavailable: {exc}")
            return

        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((8, 8, 56, 56), fill="#2b7cff", outline="#0f3f91", width=4)
        draw.line((22, 36, 32, 22, 42, 36), fill="white", width=5)
        self.tray_icon = pystray.Icon(
            "vrtread",
            image,
            "VR Treadmill",
            pystray.Menu(
                pystray.MenuItem("Show", lambda _icon, _item: self._post(self.show_window), default=True),
                pystray.MenuItem("Start", lambda _icon, _item: self._post(self.start)),
                pystray.MenuItem("Stop", lambda _icon, _item: self._post(self.stop)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", lambda _icon, _item: self._post(self.close)),
            ),
        )
        self.tray_icon.run_detached()

    def hide_to_tray(self) -> None:
        if self.tray_icon is None:
            self.close()
            return
        self.root.withdraw()

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def shutdown(self) -> None:
        """Stops everything the app owns, without touching the Tk root."""
        if self.exiting:
            return
        try:
            self._save_settings()
        except Exception:
            pass
        self.exiting = True
        if self.hotkeys is not None:
            self.hotkeys.stop()
            self.hotkeys = None
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.engine.stop()

    def close(self) -> None:
        if self.exiting:
            return
        self.shutdown()
        self.root.destroy()

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is self.root and not self.exiting and self.root.state() == "iconic":
            self.root.after(0, self.hide_to_tray)

    # --------------------------------------------------------------------------------------------- refresh

    def _refresh(self) -> None:
        if self.exiting:
            return
        status = self.engine.status()
        self.reading_text.set(f"Stick {status.stick_y:+.3f}    belt {status.velocity:+,.0f} counts/s")
        self._draw_meter(status.stick_y)

        if status.last_error and self.stop_button["state"] == "normal":
            self.engine.stop()
            self._set_running(False)
            self.status_text.set(f"Stopped after an error: {status.last_error}")
        elif not status.running and self.stop_button["state"] == "normal":
            self._set_running(False)
            self.status_text.set("Stopped")

        self._refresh_ticks += 1
        if self._refresh_ticks % 50 == 0:  # every 5 s: did a game pick the layer up?
            self._refresh_recent_game()
        self.root.after(100, self._refresh)

    def _draw_meter(self, value: float) -> None:
        self.meter.delete("all")
        width = max(int(self.meter.winfo_width()), int(self.meter["width"]))
        height = int(self.meter["height"])
        center = width // 2
        end = center + int((width // 2 - 4) * max(-1.0, min(1.0, value)))
        self.meter.create_line(center, 0, center, height, fill="#777")
        self.meter.create_rectangle(min(center, end), 4, max(center, end), height - 4, fill="#2b7cff", outline="")


# ---------------------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------------------

_SINGLE_INSTANCE_MUTEX = "Local\\VRTreadmillApp"
_ERROR_ALREADY_EXISTS = 183


def acquire_single_instance():
    """Returns a handle to keep alive, or None if another VR Treadmill window/tray icon already exists."""
    if sys.platform != "win32":
        return object()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, 0, _SINGLE_INSTANCE_MUTEX)
    if handle and ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return None
    return handle or object()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the VR Treadmill tray app.")
    parser.add_argument("--minimized", action="store_true", help="Start hidden in the system tray.")
    parser.add_argument("--show", action="store_true", help="Show the window even if Start minimized is enabled.")
    maintenance = parser.add_mutually_exclusive_group()
    maintenance.add_argument("--enable-openxr-layer", action="store_true", help="Register the OpenXR layer and exit (installer).")
    maintenance.add_argument("--disable-openxr-layer", action="store_true", help="Unregister the OpenXR layer and exit (uninstaller).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.enable_openxr_layer or args.disable_openxr_layer:
        try:
            status = enable_layer() if args.enable_openxr_layer else disable_layer()
        except Exception:
            return 1
        return 0 if status.enabled == bool(args.enable_openxr_layer) else 1

    instance_lock = acquire_single_instance()
    if instance_lock is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("VR Treadmill", "VR Treadmill is already running. Look for its icon in the system tray.")
        root.destroy()
        return 0

    settings = load_settings()
    start_minimized = (args.minimized or settings.start_minimized) and not args.show

    root = tk.Tk()
    if start_minimized:
        root.withdraw()
    app = TreadmillApp(root, settings)
    if start_minimized and app.tray_icon is None:
        app.show_window()
    root.mainloop()
    del instance_lock
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
