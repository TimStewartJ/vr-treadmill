from __future__ import annotations

import re
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass


VIGEMBUS_SERVICE_NAME = "ViGEmBus"
VIGEMBUS_INSTALL_URL = "https://github.com/ViGEm/ViGEmBus/releases"

SERVICE_STATE_NAMES = {
    1: "stopped",
    2: "start pending",
    3: "stop pending",
    4: "running",
    5: "continue pending",
    6: "pause pending",
    7: "paused",
}


@dataclass(slots=True)
class DriverStatus:
    state: str
    message: str
    installed: bool
    running: bool
    detail: str = ""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def query_vigembus_status(runner: Runner = subprocess.run) -> DriverStatus:
    if sys.platform != "win32":
        return DriverStatus(
            state="unsupported",
            message="Driver Status: Windows only",
            installed=False,
            running=False,
            detail="ViGEmBus is a Windows driver.",
        )

    try:
        result = runner(
            ["sc.exe", "query", VIGEMBUS_SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except FileNotFoundError:
        return DriverStatus(
            state="unknown",
            message="Driver Status: unable to query Windows services",
            installed=False,
            running=False,
            detail="sc.exe was not found.",
        )
    except subprocess.TimeoutExpired:
        return DriverStatus(
            state="unknown",
            message="Driver Status: query timed out",
            installed=False,
            running=False,
            detail="sc.exe query ViGEmBus timed out.",
        )

    output = f"{result.stdout}\n{result.stderr}".strip()
    if result.returncode != 0:
        return DriverStatus(
            state="missing",
            message="Driver Status: ViGEmBus not installed",
            installed=False,
            running=False,
            detail=output,
        )

    state_number = _parse_service_state(output)
    if state_number == 4:
        return DriverStatus(
            state="ready",
            message="Driver Status: ViGEmBus installed and running",
            installed=True,
            running=True,
            detail=output,
        )

    state_name = SERVICE_STATE_NAMES.get(state_number, "unknown")
    return DriverStatus(
        state="not_running",
        message=f"Driver Status: ViGEmBus installed but {state_name}",
        installed=True,
        running=False,
        detail=output,
    )


def open_vigembus_download() -> bool:
    return webbrowser.open(VIGEMBUS_INSTALL_URL)


def _parse_service_state(output: str) -> int | None:
    match = re.search(r"STATE\s*:\s*(\d+)", output, flags=re.IGNORECASE)
    if match is None:
        return None
    return int(match.group(1))
