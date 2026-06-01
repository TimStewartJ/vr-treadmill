from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .engine import TreadmillConfig, TreadmillEngine


class TreadmillApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.engine = TreadmillEngine()
        self.hotkeys = None

        self.sensitivity = tk.StringVar(value=str(TreadmillConfig.sensitivity))
        self.decay = tk.StringVar(value=str(TreadmillConfig.decay))
        self.deadzone = tk.StringVar(value=str(TreadmillConfig.deadzone))
        self.update_hz = tk.StringVar(value=str(TreadmillConfig.update_hz))
        self.status_text = tk.StringVar(value="Stopped")
        self.stick_text = tk.StringVar(value="Stick Y: +0.000")

        self.root.title("VR Treadmill")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._start_hotkey_listener()
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
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button.grid(row=0, column=1)

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

            self.hotkeys = keyboard.GlobalHotKeys({"<f8>": self.engine.stop})
            self.hotkeys.start()
        except Exception as exc:
            self.status_text.set(f"Stopped - F8 hotkey unavailable: {exc}")

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

    def close(self) -> None:
        if self.hotkeys is not None:
            self.hotkeys.stop()
        self.engine.stop()
        self.root.destroy()

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

