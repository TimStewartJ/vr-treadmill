from __future__ import annotations

import subprocess
import sys

from vrtread import driver


def test_query_vigembus_status_ready(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    status = driver.query_vigembus_status(_runner(returncode=0, stdout="STATE              : 4  RUNNING"))

    assert status.state == "ready"
    assert status.installed is True
    assert status.running is True


def test_query_vigembus_status_missing(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    status = driver.query_vigembus_status(_runner(returncode=1060, stderr="service does not exist"))

    assert status.state == "missing"
    assert status.installed is False
    assert status.running is False


def test_query_vigembus_status_installed_but_stopped(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    status = driver.query_vigembus_status(_runner(returncode=0, stdout="STATE              : 1  STOPPED"))

    assert status.state == "not_running"
    assert status.installed is True
    assert status.running is False


def test_query_vigembus_status_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    status = driver.query_vigembus_status()

    assert status.state == "unsupported"
    assert status.installed is False
    assert status.running is False


def test_open_vigembus_download_uses_official_releases(monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(driver.webbrowser, "open", lambda url: opened.append(url) or True)

    assert driver.open_vigembus_download() is True

    assert opened == [driver.VIGEMBUS_INSTALL_URL]


def _runner(returncode: int, stdout: str = "", stderr: str = ""):
    def run(args, **kwargs):
        assert args == ["sc.exe", "query", driver.VIGEMBUS_SERVICE_NAME]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 3
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)

    return run
