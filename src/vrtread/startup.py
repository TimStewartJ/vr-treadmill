from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "VRTreadmill"


def current_startup_command(minimized: bool = True) -> str:
    args = _current_launch_args()
    if minimized and "--minimized" not in args:
        args.append("--minimized")
    return quote_command(args)


def quote_command(args: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(args))


def is_startup_enabled() -> bool:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def set_startup_enabled(enabled: bool, command: str | None = None) -> None:
    winreg = _winreg()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            value = command or current_startup_command(minimized=True)
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, value)
            return

        try:
            winreg.DeleteValue(key, RUN_VALUE_NAME)
        except FileNotFoundError:
            return


def _current_launch_args() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]

    launcher = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if launcher is not None and launcher.suffix.lower() == ".exe" and launcher.exists():
        return [str(launcher)]

    return [sys.executable, "-m", "vrtread.gui"]


def _winreg():
    if sys.platform != "win32":
        raise RuntimeError("Start with Windows is only available on Windows.")

    import winreg

    return winreg
