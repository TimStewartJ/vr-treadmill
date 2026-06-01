from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .engine import TreadmillConfig, TreadmillEngine


class TreadmillApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.engine = TreadmillEngine()
        self.hotkeys = None
        self.tray_icon = None
        self.exiting = False

        defaults = TreadmillConfig()
        self.sensitivity = tk.StringVar(value=str(defaults.sensitivity))
        self.decay = tk.StringVar(value=str(defaults.decay))
        self.deadzone = tk.StringVar(value=str(defaults.deadzone))
        self.update_hz = tk.StringVar(value=str(defaults.update_hz))
        self.status_text = tk.StringVar(value="Stopped")
        self.stick_text = tk.StringVar(value="Stick Y: +0.000")

        self.root.title("VR Treadmill")
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.bind("<Unmap>", self._on_unmap)
        self._build()
        self._start_hotkey_listener()
        self._start_tray_icon()
        self._refresh()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        ttk.Label(frame, text="Mouse treadmill to Xbox left-stick Y").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )

        self._add_entry(frame, "Sensitivity", self.sensitivity, 1)
        self._add_entry(frame, "Decay", self.decay, 2)
        self._add_entry(frame, "Deadzone", self.deadzone, 3)
        self._add_entry(frame, "Update Hz", self.update_hz, 4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.start_button = ttk.Button(buttons, text="Start capture", command=self.start)
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.hide_button = ttk.Button(buttons, text="Hide to tray", command=self.hide_to_tray)
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button.grid(row=0, column=1, padx=(0, 8))
        self.hide_button.grid(row=0, column=2)

        ttk.Label(frame, textvariable=self.status_text).grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Label(frame, textvariable=self.stick_text).grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="Emergency stop hotkey: F8").grid(row=8, column=0, columnspan=2, sticky="w")

        self.meter = tk.Canvas(frame, width=280, height=28, background="white", highlightthickness=1)
        self.meter.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    @staticmethod
    def _add_entry(parent: ttk.Frame, label: str, variable: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(parent, textvariable=variable, width=12).grid(row=row, column=1, sticky="e", pady=(8, 0))

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
            self.engine.start(self._read_config())
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

    def _read_config(self) -> TreadmillConfig:
        return TreadmillConfig(
            sensitivity=float(self.sensitivity.get()),
            decay=float(self.decay.get()),
            deadzone=int(self.deadzone.get()),
            update_hz=int(self.update_hz.get()),
        )

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


def main() -> int:
    root = tk.Tk()
    TreadmillApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
