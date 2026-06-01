from __future__ import annotations

import sys
from pathlib import Path

from vrtread import startup


def test_quote_command_quotes_paths_with_spaces() -> None:
    command = startup.quote_command(
        [r"C:\Users\Test User\AppData\Local\Programs\VRTreadmill\VRTreadmill.exe", "--minimized"]
    )

    assert command == r'"C:\Users\Test User\AppData\Local\Programs\VRTreadmill\VRTreadmill.exe" --minimized'


def test_current_startup_command_uses_frozen_executable(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\VRTreadmill\VRTreadmill.exe")

    command = startup.current_startup_command()

    assert command == r'"C:\Program Files\VRTreadmill\VRTreadmill.exe" --minimized'


def test_current_startup_command_uses_launcher_exe(monkeypatch, tmp_path: Path) -> None:
    launcher = tmp_path / "vrtread-gui.exe"
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    monkeypatch.setattr(sys, "executable", r"C:\Python312\python.exe")
    if hasattr(sys, "frozen"):
        monkeypatch.delattr(sys, "frozen", raising=False)

    command = startup.current_startup_command()

    assert command == startup.quote_command([str(launcher.resolve()), "--minimized"])


def test_current_startup_command_falls_back_to_python_module(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["vrtread.gui"])
    monkeypatch.setattr(sys, "executable", r"C:\Python312\python.exe")
    if hasattr(sys, "frozen"):
        monkeypatch.delattr(sys, "frozen", raising=False)

    command = startup.current_startup_command()

    assert command == r"C:\Python312\python.exe -m vrtread.gui --minimized"
