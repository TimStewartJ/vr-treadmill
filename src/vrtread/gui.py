from __future__ import annotations

import argparse
import tkinter as tk
from tkinter import messagebox, ttk

from .driver import open_vigembus_download, query_vigembus_status
from .engine import TreadmillConfig, TreadmillEngine
from .openxr import ensure_automatic_layer, query_layer_status, register_layer, unregister_layer
from .outputs import OpenXrFilterMode, OutputConfig, OutputMode
from .settings import AppSettings, load_settings, save_settings
from .startup import current_startup_command, set_startup_enabled


TUNING_PRESETS = {
    "Comfort": TreadmillConfig(sensitivity=0.0025, decay=0.82, deadzone=4, update_hz=100),
    "Balanced": TreadmillConfig(sensitivity=0.0030, decay=0.85, deadzone=2, update_hz=100),
    "Faster": TreadmillConfig(sensitivity=0.0045, decay=0.85, deadzone=2, update_hz=120),
    "Snappy": TreadmillConfig(sensitivity=0.0040, decay=0.70, deadzone=2, update_hz=120),
}

OPENXR_FILTER_LABELS = {
    OpenXrFilterMode.STRICT: "Strict - locomotion-named left stick only",
    OpenXrFilterMode.BALANCED: "Balanced - any non-menu left stick",
    OpenXrFilterMode.COMPATIBILITY: "Compatibility - allow locomotion names if bindings are missing",
}


class TreadmillApp:
    def __init__(self, root: tk.Tk, settings: AppSettings | None = None) -> None:
        self.root = root
        self.settings = settings or load_settings()
        self.engine = TreadmillEngine(
            output_config=OutputConfig(
                mode=self.settings.output_mode,
                openxr_filter_mode=self.settings.openxr_filter_mode,
            )
        )
        self.hotkeys = None
        self.tray_icon = None
        self.exiting = False

        defaults = self.settings.treadmill
        self.sensitivity = tk.DoubleVar(value=defaults.sensitivity)
        self.decay = tk.DoubleVar(value=defaults.decay)
        self.deadzone = tk.DoubleVar(value=defaults.deadzone)
        self.update_hz = tk.DoubleVar(value=defaults.update_hz)
        self.sensitivity_value = tk.StringVar()
        self.decay_value = tk.StringVar()
        self.deadzone_value = tk.StringVar()
        self.update_hz_value = tk.StringVar()
        self.output_mode = tk.StringVar(value=self.settings.output_mode.value)
        self.openxr_filter_mode = tk.StringVar(value=self.settings.openxr_filter_mode.value)
        self.start_with_windows = tk.BooleanVar(value=self.settings.start_with_windows)
        self.start_minimized = tk.BooleanVar(value=self.settings.start_minimized)
        self.driver_status_text = tk.StringVar(value="Driver Status: checking...")
        self.openxr_status_text = tk.StringVar(value="Automatic OpenXR layer: checking...")
        self.status_text = tk.StringVar(value="Stopped")
        self.stick_text = tk.StringVar(value="Stick Y: +0.000")

        self.root.title("VR Treadmill")
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.bind("<Unmap>", self._on_unmap)
        self._build()
        self._auto_enable_openxr_if_selected()
        self._start_hotkey_listener()
        self._start_tray_icon()
        self._refresh()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        ttk.Label(frame, text="VR Treadmill turns forward/back treadmill motion into VR locomotion input.").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )

        driver_frame = ttk.LabelFrame(frame, text="Xbox fallback driver")
        driver_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        driver_frame.columnconfigure(1, weight=1)
        ttk.Label(driver_frame, textvariable=self.driver_status_text).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 4)
        )
        ttk.Button(driver_frame, text="Refresh", command=self.refresh_driver_status).grid(
            row=1, column=0, sticky="w", padx=(8, 4), pady=(0, 8)
        )
        ttk.Button(driver_frame, text="Install/Open ViGEmBus Driver", command=self.open_driver_install).grid(
            row=1, column=1, sticky="w", padx=(4, 8), pady=(0, 8)
        )

        output_frame = ttk.LabelFrame(frame, text="Output")
        output_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        output_frame.columnconfigure(1, weight=1)
        ttk.Radiobutton(
            output_frame,
            text="Xbox controller",
            value=OutputMode.XBOX.value,
            variable=self.output_mode,
            command=self._on_output_mode_changed,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        ttk.Radiobutton(
            output_frame,
            text="OpenXR automatic",
            value=OutputMode.OPENXR.value,
            variable=self.output_mode,
            command=self._on_output_mode_changed,
        ).grid(row=0, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Radiobutton(
            output_frame,
            text="Both",
            value=OutputMode.BOTH.value,
            variable=self.output_mode,
            command=self._on_output_mode_changed,
        ).grid(row=0, column=2, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(output_frame, text="OpenXR filter:").grid(row=1, column=0, sticky="w", padx=8, pady=(8, 0))
        for column, filter_mode in enumerate(
            (OpenXrFilterMode.STRICT, OpenXrFilterMode.BALANCED, OpenXrFilterMode.COMPATIBILITY)
        ):
            ttk.Radiobutton(
                output_frame,
                text=OPENXR_FILTER_LABELS[filter_mode],
                value=filter_mode.value,
                variable=self.openxr_filter_mode,
                command=self._on_filter_mode_changed,
            ).grid(row=2 + column, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 0))
        ttk.Label(output_frame, textvariable=self.openxr_status_text).grid(
            row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 4)
        )
        ttk.Button(output_frame, text="Refresh OpenXR", command=self.refresh_openxr_status).grid(
            row=6, column=0, sticky="w", padx=(8, 4), pady=(0, 8)
        )
        ttk.Button(output_frame, text="Enable Automatic OpenXR", command=self.register_openxr_layer).grid(
            row=6, column=1, sticky="w", padx=(4, 4), pady=(0, 8)
        )
        ttk.Button(output_frame, text="Disable Automatic OpenXR", command=self.unregister_openxr_layer).grid(
            row=6, column=2, sticky="w", padx=(4, 8), pady=(0, 8)
        )

        tuning_frame = ttk.LabelFrame(frame, text="Tuning")
        tuning_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        tuning_frame.columnconfigure(1, weight=1)

        ttk.Label(
            tuning_frame,
            text="Start with Balanced. If movement feels too slow, try Faster. If it drifts or feels jumpy, try Comfort.",
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 4))

        preset_frame = ttk.Frame(tuning_frame)
        preset_frame.grid(row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))
        ttk.Label(preset_frame, text="Quick presets:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        for column, preset_name in enumerate(TUNING_PRESETS, start=1):
            ttk.Button(
                preset_frame,
                text=preset_name,
                command=lambda name=preset_name: self.apply_preset(name),
            ).grid(row=0, column=column, padx=(0, 6))

        self._add_slider(
            tuning_frame,
            "Movement speed",
            "Higher means less treadmill/mouse movement is needed to push the virtual stick forward.",
            self.sensitivity,
            self.sensitivity_value,
            2,
            0.001,
            0.010,
        )
        self._add_slider(
            tuning_frame,
            "Stop smoothness",
            "Higher coasts more smoothly; lower recenters faster when you stop walking.",
            self.decay,
            self.decay_value,
            4,
            0.50,
            0.95,
        )
        self._add_slider(
            tuning_frame,
            "Noise filter",
            "Higher ignores tiny accidental movements; lower makes very slow walking easier to detect.",
            self.deadzone,
            self.deadzone_value,
            6,
            0,
            10,
        )
        self._add_slider(
            tuning_frame,
            "Update rate",
            "How often the virtual controller is updated. 100 Hz is usually enough.",
            self.update_hz,
            self.update_hz_value,
            8,
            30,
            200,
        )

        ttk.Checkbutton(
            frame,
            text="Start with Windows",
            variable=self.start_with_windows,
            command=self._on_start_with_windows_changed,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))

        ttk.Checkbutton(
            frame,
            text="Start minimized to tray",
            variable=self.start_minimized,
            command=self._on_start_minimized_changed,
        ).grid(row=5, column=0, columnspan=2, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.start_button = ttk.Button(buttons, text="Start treadmill capture", command=self.start)
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.hide_button = ttk.Button(buttons, text="Hide to tray", command=self.hide_to_tray)
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button.grid(row=0, column=1, padx=(0, 8))
        self.hide_button.grid(row=0, column=2)

        ttk.Label(frame, textvariable=self.status_text).grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Label(frame, textvariable=self.stick_text).grid(row=8, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="Emergency stop hotkey: F8").grid(row=9, column=0, columnspan=2, sticky="w")

        self.meter = tk.Canvas(frame, width=280, height=28, background="white", highlightthickness=1)
        self.meter.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self._update_tuning_labels()
        self.refresh_driver_status()
        self.refresh_openxr_status()

    def _add_slider(
        self,
        parent: ttk.Frame,
        label: str,
        description: str,
        variable: tk.DoubleVar,
        value_label: tk.StringVar,
        row: int,
        from_: float,
        to: float,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", padx=8, pady=(8, 0))
        ttk.Scale(
            parent,
            variable=variable,
            from_=from_,
            to=to,
            command=lambda _value: self._on_tuning_changed(),
        ).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=(8, 0))
        ttk.Label(parent, textvariable=value_label, width=12).grid(row=row, column=2, sticky="e", padx=(0, 8), pady=(8, 0))
        ttk.Label(parent, text=description, wraplength=420, foreground="#555").grid(
            row=row + 1, column=0, columnspan=3, sticky="w", padx=8
        )

    def _start_hotkey_listener(self) -> None:
        try:
            from pynput import keyboard

            self.hotkeys = keyboard.GlobalHotKeys({"<f8>": self._hotkey_stop})
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
                pystray.MenuItem("Show", self._tray_show, default=True),
                pystray.MenuItem("Start capture", self._tray_start),
                pystray.MenuItem("Stop", self._tray_stop),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self._tray_exit),
            ),
        )
        self.tray_icon.run_detached()

    def start(self) -> None:
        try:
            config = self._read_config()
            output_config = self._read_output_config()
            if output_config.mode.uses_openxr:
                status = ensure_automatic_layer()
                self.openxr_status_text.set(status.message)
            self.settings.treadmill = config
            self.settings.output_mode = output_config.mode
            self.settings.openxr_filter_mode = output_config.openxr_filter_mode
            self._save_settings()
            self.engine.start(config, output_config)
        except Exception as exc:
            messagebox.showerror("Could not start VR Treadmill", str(exc))
            return

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_text.set("Running - cursor is locked to screen center")

    def stop(self) -> None:
        self.engine.stop()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_text.set("Stopped")

    def hide_to_tray(self) -> None:
        if self.tray_icon is None:
            self.close()
            return
        self.root.withdraw()
        self.status_text.set("Hidden to tray")

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def close(self) -> None:
        self.exiting = True
        self._save_settings()
        if self.hotkeys is not None:
            self.hotkeys.stop()
            self.hotkeys = None
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.engine.stop()
        self.root.destroy()

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is self.root and not self.exiting and self.root.state() == "iconic":
            self.root.after(0, self.hide_to_tray)

    def _hotkey_stop(self) -> None:
        self.root.after(0, self.stop)

    def _tray_show(self, icon, item) -> None:
        self.root.after(0, self.show_window)

    def _tray_start(self, icon, item) -> None:
        self.root.after(0, self.start)

    def _tray_stop(self, icon, item) -> None:
        self.root.after(0, self.stop)

    def _tray_exit(self, icon, item) -> None:
        self.root.after(0, self.close)

    def refresh_driver_status(self) -> None:
        status = query_vigembus_status()
        self.driver_status_text.set(status.message)

    def refresh_openxr_status(self) -> None:
        status = query_layer_status()
        self.openxr_status_text.set(status.message)

    def _auto_enable_openxr_if_selected(self) -> None:
        if not self.settings.output_mode.uses_openxr:
            return
        try:
            status = ensure_automatic_layer()
        except Exception as exc:
            self.openxr_status_text.set(f"Automatic OpenXR layer: {exc}")
            return
        self.openxr_status_text.set(status.message)

    def register_openxr_layer(self) -> None:
        try:
            status = register_layer()
        except Exception as exc:
            messagebox.showerror("Could not enable automatic OpenXR", str(exc))
            return
        self.openxr_status_text.set(status.message)
        self.status_text.set("Automatic OpenXR enabled")

    def unregister_openxr_layer(self) -> None:
        try:
            status = unregister_layer()
        except Exception as exc:
            messagebox.showerror("Could not disable automatic OpenXR", str(exc))
            return
        self.openxr_status_text.set(status.message)
        self.status_text.set("Automatic OpenXR disabled")

    def open_driver_install(self) -> None:
        if open_vigembus_download():
            self.status_text.set("Opened ViGEmBus driver download page")
            return

        messagebox.showerror(
            "Could not open driver page",
            "Open this URL manually: https://github.com/ViGEm/ViGEmBus/releases",
        )

    def apply_preset(self, name: str) -> None:
        config = TUNING_PRESETS[name]
        self._set_config_controls(config)
        self._on_tuning_changed()
        self.status_text.set(f"{name} preset applied")

    def _on_start_with_windows_changed(self) -> None:
        enabled = self.start_with_windows.get()
        previous_start_with_windows = self.settings.start_with_windows
        previous_start_minimized = self.settings.start_minimized

        try:
            if enabled:
                self.start_minimized.set(True)
                set_startup_enabled(True, current_startup_command(minimized=True))
            else:
                set_startup_enabled(False)

            self.settings.start_with_windows = enabled
            self.settings.start_minimized = self.start_minimized.get()
            self._save_settings()
            self.status_text.set("Startup settings saved")
        except Exception as exc:
            self.start_with_windows.set(previous_start_with_windows)
            self.start_minimized.set(previous_start_minimized)
            messagebox.showerror("Could not update Windows startup", str(exc))

    def _on_start_minimized_changed(self) -> None:
        self.settings.start_minimized = self.start_minimized.get()
        self._save_settings()
        if self.start_with_windows.get() and not self.start_minimized.get():
            self.start_with_windows.set(False)
            self._on_start_with_windows_changed()
        else:
            self.status_text.set("Startup settings saved")

    def _read_config(self) -> TreadmillConfig:
        return TreadmillConfig(
            sensitivity=round(float(self.sensitivity.get()), 4),
            decay=round(float(self.decay.get()), 2),
            deadzone=int(round(float(self.deadzone.get()))),
            update_hz=int(round(float(self.update_hz.get()))),
        )

    def _read_output_config(self) -> OutputConfig:
        return OutputConfig(
            mode=OutputMode(self.output_mode.get()),
            openxr_filter_mode=OpenXrFilterMode(self.openxr_filter_mode.get()),
        )

    def _save_settings(self) -> None:
        try:
            self.settings.treadmill = self._read_config()
        except ValueError:
            pass
        output_config = self._read_output_config()
        self.settings.output_mode = output_config.mode
        self.settings.openxr_filter_mode = output_config.openxr_filter_mode
        self.settings.start_with_windows = self.start_with_windows.get()
        self.settings.start_minimized = self.start_minimized.get()
        save_settings(self.settings)

    def _set_config_controls(self, config: TreadmillConfig) -> None:
        self.sensitivity.set(config.sensitivity)
        self.decay.set(config.decay)
        self.deadzone.set(config.deadzone)
        self.update_hz.set(config.update_hz)
        self._update_tuning_labels()

    def _on_tuning_changed(self) -> None:
        self._update_tuning_labels()
        try:
            config = self._read_config()
            self.settings.treadmill = config
            if self.engine.is_running:
                self.engine.update_config(config)
                self.status_text.set("Tuning updated live")
            else:
                self.status_text.set("Tuning saved - start capture to test")
            self._save_settings()
        except Exception as exc:
            self.status_text.set(f"Tuning error: {exc}")

    def _on_output_mode_changed(self) -> None:
        try:
            output_config = self._read_output_config()
            self.engine.update_output_config(output_config)
            self.settings.output_mode = output_config.mode
            self.settings.openxr_filter_mode = output_config.openxr_filter_mode
            self._save_settings()
            if output_config.mode.uses_openxr:
                status = ensure_automatic_layer()
                self.openxr_status_text.set(status.message)
            self.status_text.set(f"Output mode saved: {output_config.mode.value}")
        except Exception as exc:
            self.output_mode.set(self.settings.output_mode.value)
            self.status_text.set(str(exc))

    def _on_filter_mode_changed(self) -> None:
        try:
            output_config = self._read_output_config()
            self.engine.update_output_config(output_config)
            self.settings.openxr_filter_mode = output_config.openxr_filter_mode
            self._save_settings()
            self.status_text.set(f"OpenXR filter saved: {output_config.openxr_filter_mode.value}")
        except Exception as exc:
            self.openxr_filter_mode.set(self.settings.openxr_filter_mode.value)
            self.status_text.set(str(exc))

    def _update_tuning_labels(self) -> None:
        self.sensitivity_value.set(f"{float(self.sensitivity.get()):.4f}")
        self.decay_value.set(f"{float(self.decay.get()):.2f}")
        self.deadzone_value.set(f"{int(round(float(self.deadzone.get())))} px")
        self.update_hz_value.set(f"{int(round(float(self.update_hz.get())))} Hz")

    def _refresh(self) -> None:
        status = self.engine.status()
        self.stick_text.set(f"Stick Y: {status.stick_y:+.3f}")
        self._draw_meter(status.stick_y)

        if status.last_error:
            self.status_text.set(f"Error: {status.last_error}")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
        elif not status.running and self.stop_button["state"] == "normal":
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.status_text.set("Stopped")

        self.root.after(100, self._refresh)

    def _draw_meter(self, value: float) -> None:
        self.meter.delete("all")
        width = int(self.meter["width"])
        height = int(self.meter["height"])
        center = width // 2
        end = center + int((width // 2 - 4) * max(-1.0, min(1.0, value)))
        self.meter.create_line(center, 0, center, height, fill="#777")
        self.meter.create_rectangle(min(center, end), 4, max(center, end), height - 4, fill="#2b7cff")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the VR Treadmill tray app.")
    parser.add_argument("--minimized", action="store_true", help="Start hidden in the system tray.")
    parser.add_argument("--show", action="store_true", help="Show the window even if Start minimized is enabled.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    start_minimized = (args.minimized or settings.start_minimized) and not args.show

    root = tk.Tk()
    if start_minimized:
        root.withdraw()

    app = TreadmillApp(root, settings)
    if start_minimized:
        if app.tray_icon is None:
            app.show_window()
        else:
            app.hide_to_tray()

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
