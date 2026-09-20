"""Installs, registers and diagnoses the VR Treadmill OpenXR API layer.

The layer is a DLL plus a static manifest that sit next to each other (the manifest uses a relative
``library_path``). They ship inside the app and are deployed to

    %LOCALAPPDATA%\\VRTreadmill\\openxr_layer\\<version>-<dll hash>\\

before being registered under ``HKCU\\Software\\Khronos\\OpenXR\\1\\ApiLayers\\Implicit``. The folder name
contains the DLL's hash, so an update never has to overwrite a DLL that a running game has locked, and the
registration can never point at a half-written file.

Registration is self-healing: enabling the layer removes every *other* registration of this layer (older
versions, development builds, entries whose files are gone). Two copies of the layer in one game would
both inject, and the OpenXR loader silently prefers an implicit copy over an explicit one.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import __version__

LAYER_NAME = "XR_APILAYER_VRTREAD_treadmill"
MANIFEST_FILE_NAME = f"{LAYER_NAME}.json"
DLL_FILE_NAME = "vrtread_openxr_layer.dll"
IMPLICIT_KEY = r"Software\Khronos\OpenXR\1\ApiLayers\Implicit"
EXPLICIT_KEY = r"Software\Khronos\OpenXR\1\ApiLayers\Explicit"
RUNTIME_KEY = r"SOFTWARE\Khronos\OpenXR\1"

BUNDLE_DIR_ENV = "VRTREAD_OPENXR_LAYER_DIR"
INSTALL_ROOT_ENV = "VRTREAD_OPENXR_INSTALL_ROOT"
LOG_DIR_ENV = "VRTREAD_OPENXR_LOG_DIR"


# ---------------------------------------------------------------------------------------------------------
# Registry access (injectable so the logic is testable without touching the real registry)
# ---------------------------------------------------------------------------------------------------------

class LayerRegistry(Protocol):
    def values(self, key: str) -> dict[str, int]:
        ...

    def set_value(self, key: str, name: str, value: int) -> None:
        ...

    def delete_value(self, key: str, name: str) -> None:
        ...


class CurrentUserRegistry:
    """HKEY_CURRENT_USER. HKCU\\Software is not WOW64-redirected, so one view covers 32- and 64-bit apps."""

    def values(self, key: str) -> dict[str, int]:
        import winreg

        result: dict[str, int] = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as handle:
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(handle, index)
                    except OSError:
                        break
                    result[name] = value if isinstance(value, int) else -1
                    index += 1
        except FileNotFoundError:
            pass
        return result

    def set_value(self, key: str, name: str, value: int) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_DWORD, value)

    def delete_value(self, key: str, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            pass


def _default_registry() -> LayerRegistry:
    if platform.system() != "Windows":
        raise RuntimeError("The OpenXR layer can only be registered on Windows.")
    return CurrentUserRegistry()


# ---------------------------------------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------------------------------------

def bundle_dir() -> Path:
    """Where this build of the app carries the layer DLL and manifest."""
    override = os.environ.get(BUNDLE_DIR_ENV)
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / "openxr_layer"
    return Path(__file__).resolve().parents[2] / "native" / "openxr_layer" / "bin"


def install_root() -> Path:
    override = os.environ.get(INSTALL_ROOT_ENV)
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "VRTreadmill" / "openxr_layer"


def log_dir() -> Path:
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "VRTreadmill" / "logs"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class LayerBundle:
    dll: Path
    manifest: Path
    dll_hash: str

    @property
    def deployment_name(self) -> str:
        return f"{__version__}-{self.dll_hash[:12]}"


def find_bundle(directory: Path | None = None) -> LayerBundle:
    source = directory or bundle_dir()
    dll = source / DLL_FILE_NAME
    manifest = source / MANIFEST_FILE_NAME
    if not dll.is_file() or not manifest.is_file():
        hint = (
            "Reinstall VR Treadmill."
            if getattr(sys, "frozen", False)
            else "Build it with scripts\\build-openxr-layer.ps1."
        )
        raise FileNotFoundError(f"The OpenXR layer is missing from {source}. {hint}")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        name = payload["api_layer"]["name"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"The OpenXR layer manifest {manifest} is not valid: {exc}") from exc
    if name != LAYER_NAME:
        raise RuntimeError(f"The OpenXR layer manifest {manifest} describes '{name}', expected '{LAYER_NAME}'.")
    return LayerBundle(dll=dll, manifest=manifest, dll_hash=_file_hash(dll))


# ---------------------------------------------------------------------------------------------------------
# Status / enable / disable
# ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LayerStatus:
    enabled: bool                  # the current build is deployed and registered; games will load it
    current_manifest: Path | None  # where the current build is (or would be) deployed
    stale: tuple[str, ...]         # other registrations of this layer that should not be there
    problem: str = ""

    @property
    def message(self) -> str:
        if self.problem:
            return f"OpenXR layer: {self.problem}"
        if self.enabled and self.stale:
            return "OpenXR layer: enabled (older registrations will be cleaned up)"
        if self.enabled:
            return "OpenXR layer: enabled - OpenXR games pick it up automatically"
        if self.stale:
            return "OpenXR layer: an outdated copy is registered - press Enable to update it"
        return "OpenXR layer: disabled"


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def _is_our_manifest(path: str) -> bool:
    """True if a registered manifest belongs to this layer, including entries whose file is gone."""
    candidate = Path(path)
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        return payload.get("api_layer", {}).get("name") == LAYER_NAME
    except (OSError, ValueError, AttributeError):
        return candidate.name.lower() == MANIFEST_FILE_NAME.lower()


def _our_registrations(registry: LayerRegistry) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for key in (IMPLICIT_KEY, EXPLICIT_KEY):
        for name in registry.values(key):
            if _is_our_manifest(name):
                found.append((key, name))
    return found


def query_layer_status(registry: LayerRegistry | None = None) -> LayerStatus:
    registry = registry or _default_registry()
    try:
        bundle = find_bundle()
    except (OSError, RuntimeError) as exc:
        stale = tuple(name for _key, name in _our_registrations(registry))
        return LayerStatus(enabled=False, current_manifest=None, stale=stale, problem=str(exc))

    current = install_root() / bundle.deployment_name / MANIFEST_FILE_NAME
    implicit = registry.values(IMPLICIT_KEY)
    registered = any(_same_path(name, current) and value == 0 for name, value in implicit.items())
    deployed = current.is_file() and (current.parent / DLL_FILE_NAME).is_file()
    stale = tuple(name for _key, name in _our_registrations(registry) if not _same_path(name, current))
    return LayerStatus(enabled=registered and deployed, current_manifest=current, stale=stale)


def _deploy(bundle: LayerBundle) -> Path:
    target_dir = install_root() / bundle.deployment_name
    target_dll = target_dir / DLL_FILE_NAME
    target_manifest = target_dir / MANIFEST_FILE_NAME
    target_dir.mkdir(parents=True, exist_ok=True)

    if not (target_dll.is_file() and _file_hash(target_dll) == bundle.dll_hash):
        temporary = target_dir / (DLL_FILE_NAME + ".tmp")
        shutil.copyfile(bundle.dll, temporary)
        os.replace(temporary, target_dll)
    if not (target_manifest.is_file() and target_manifest.read_bytes() == bundle.manifest.read_bytes()):
        temporary = target_dir / (MANIFEST_FILE_NAME + ".tmp")
        shutil.copyfile(bundle.manifest, temporary)
        os.replace(temporary, target_manifest)
    return target_manifest


def _remove_old_deployments(keep: Path | None) -> None:
    root = install_root()
    if not root.is_dir():
        return
    for child in root.iterdir():
        if not child.is_dir() or (keep is not None and _same_path(child, keep)):
            continue
        # A running game keeps its copy of the DLL locked; that folder simply goes next time.
        shutil.rmtree(child, ignore_errors=True)
    if keep is None:
        try:
            root.rmdir()
        except OSError:
            pass


def enable_layer(registry: LayerRegistry | None = None) -> LayerStatus:
    """Deploys the current build, registers it as an implicit layer, and removes every other copy."""
    registry = registry or _default_registry()
    bundle = find_bundle()
    manifest = _deploy(bundle)

    registry.set_value(IMPLICIT_KEY, str(manifest), 0)
    for key, name in _our_registrations(registry):
        if not (key == IMPLICIT_KEY and _same_path(name, manifest)):
            registry.delete_value(key, name)
    _remove_old_deployments(keep=manifest.parent)
    return query_layer_status(registry)


def ensure_layer_enabled(registry: LayerRegistry | None = None) -> LayerStatus:
    registry = registry or _default_registry()
    status = query_layer_status(registry)
    if status.enabled and not status.stale:
        return status
    return enable_layer(registry)


def disable_layer(registry: LayerRegistry | None = None) -> LayerStatus:
    """Unregisters every copy of this layer. Games stop loading it the next time they start."""
    registry = registry or _default_registry()
    for key, name in _our_registrations(registry):
        registry.delete_value(key, name)
    _remove_old_deployments(keep=None)
    return query_layer_status(registry)


# ---------------------------------------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------------------------------------

class _NegotiateLoaderInfo(ctypes.Structure):
    _fields_ = [
        ("structType", ctypes.c_int),
        ("structVersion", ctypes.c_uint32),
        ("structSize", ctypes.c_size_t),
        ("minInterfaceVersion", ctypes.c_uint32),
        ("maxInterfaceVersion", ctypes.c_uint32),
        ("minApiVersion", ctypes.c_uint64),
        ("maxApiVersion", ctypes.c_uint64),
    ]


class _NegotiateApiLayerRequest(ctypes.Structure):
    _fields_ = [
        ("structType", ctypes.c_int),
        ("structVersion", ctypes.c_uint32),
        ("structSize", ctypes.c_size_t),
        ("layerInterfaceVersion", ctypes.c_uint32),
        ("layerApiVersion", ctypes.c_uint64),
        ("getInstanceProcAddr", ctypes.c_void_p),
        ("createApiLayerInstance", ctypes.c_void_p),
    ]


def _xr_version(major: int, minor: int, patch: int) -> int:
    return ((major & 0xFFFF) << 48) | ((minor & 0xFFFF) << 32) | (patch & 0xFFFFFFFF)


def self_test(dll_path: Path) -> tuple[bool, str]:
    """Loads the layer DLL and negotiates with it exactly like the OpenXR loader does.

    Catches the failures that would otherwise only show up as "my game no longer starts in VR": a DLL
    blocked by antivirus, a truncated file, a missing export.
    """
    if platform.system() != "Windows":
        return False, "The OpenXR layer only runs on Windows."
    try:
        library = ctypes.WinDLL(str(dll_path))
    except OSError as exc:
        return False, f"Windows could not load {dll_path.name}: {exc}"
    try:
        negotiate = library.xrNegotiateLoaderApiLayerInterface
    except AttributeError:
        return False, f"{dll_path.name} does not export xrNegotiateLoaderApiLayerInterface."
    negotiate.restype = ctypes.c_int
    negotiate.argtypes = [ctypes.POINTER(_NegotiateLoaderInfo), ctypes.c_char_p, ctypes.POINTER(_NegotiateApiLayerRequest)]

    loader = _NegotiateLoaderInfo(1, 1, ctypes.sizeof(_NegotiateLoaderInfo), 1, 1, _xr_version(1, 0, 0), _xr_version(1, 0x3FF, 0xFFF))
    request = _NegotiateApiLayerRequest(2, 1, ctypes.sizeof(_NegotiateApiLayerRequest), 0, 0, None, None)
    result = negotiate(ctypes.byref(loader), LAYER_NAME.encode("ascii"), ctypes.byref(request))
    if result != 0:
        return False, f"The layer refused to negotiate with the loader (XrResult {result})."
    if not request.getInstanceProcAddr or not request.createApiLayerInstance or request.layerInterfaceVersion != 1:
        return False, "The layer negotiated but returned an incomplete interface."
    return True, "The layer DLL loads and negotiates correctly."


def active_runtime() -> str:
    if platform.system() != "Windows":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, RUNTIME_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
            manifest, _kind = winreg.QueryValueEx(handle, "ActiveRuntime")
    except OSError:
        return ""
    try:
        name = json.loads(Path(manifest).read_text(encoding="utf-8")).get("runtime", {}).get("name")
    except (OSError, ValueError, AttributeError):
        name = None
    return f"{name} ({manifest})" if name else str(manifest)


@dataclass(frozen=True, slots=True)
class GameSession:
    exe: str
    application: str
    engine: str
    when: str
    mode: str                  # "active" or "passthrough ..."
    driving: tuple[str, ...]   # actions the layer decided to drive
    engaged: bool              # treadmill movement actually reached the game
    log_path: Path


_ATTACHED = re.compile(
    r"^(?P<when>\S+ \S+) \[\d+\] layer \S+ attached: exe='(?P<exe>[^']*)' app='(?P<app>[^']*)' "
    r"engine='(?P<engine>[^']*)' api=\S+ mode=(?P<mode>.*)$"
)
_DECISION = re.compile(r"decision action='(?P<action>[^']*)' hand=(?P<hand>\S+) filter=\S+ -> drive$")


def parse_layer_log(path: Path) -> GameSession | None:
    """Summarises the most recent game session recorded in one layer log."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    start = None
    for index in range(len(lines) - 1, -1, -1):
        if _ATTACHED.match(lines[index]):
            start = index
            break
    if start is None:
        return None
    attached = _ATTACHED.match(lines[start])
    assert attached is not None
    driving: list[str] = []
    engaged = False
    for line in lines[start + 1 :]:
        decision = _DECISION.search(line)
        if decision:
            label = f"{decision['action']} ({decision['hand']})"
            if label not in driving:
                driving.append(label)
        if " engaged: " in line:
            engaged = True
    return GameSession(
        exe=attached["exe"],
        application=attached["app"],
        engine=attached["engine"],
        when=attached["when"],
        mode=attached["mode"].strip(),
        driving=tuple(driving),
        engaged=engaged,
        log_path=path,
    )


def recent_game_sessions(limit: int = 5) -> list[GameSession]:
    directory = log_dir()
    if not directory.is_dir():
        return []
    logs = sorted(directory.glob("openxr-layer-*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    sessions = [session for session in (parse_layer_log(path) for path in logs) if session is not None]
    return sessions[:limit]


def describe_session(session: GameSession) -> str:
    title = session.application or session.exe
    if not session.mode.startswith("active"):
        return f"{title}: layer loaded but idle ({session.mode})"
    if session.engaged:
        return f"{title}: treadmill drove {', '.join(session.driving) or 'locomotion'}"
    if session.driving:
        return f"{title}: ready to drive {', '.join(session.driving)} (no treadmill movement seen yet)"
    return f"{title}: no left-stick locomotion action found yet"


def doctor(registry: LayerRegistry | None = None) -> tuple[bool, list[str]]:
    """Human-readable health report. Returns (healthy, lines)."""
    registry = registry or _default_registry()
    lines: list[str] = []
    healthy = True

    status = query_layer_status(registry)
    lines.append(status.message)
    if status.current_manifest is not None:
        lines.append(f"  manifest: {status.current_manifest}")
    for name in status.stale:
        lines.append(f"  stale registration: {name}")
    if not status.enabled:
        healthy = False

    try:
        bundle = find_bundle()
        ok, detail = self_test(bundle.dll)
        lines.append(f"Self-test: {detail}")
        healthy = healthy and ok
    except (OSError, RuntimeError) as exc:
        lines.append(f"Self-test: {exc}")
        healthy = False

    runtime = active_runtime()
    lines.append(f"Active OpenXR runtime: {runtime or 'none found - install SteamVR or your headset software'}")
    if not runtime:
        healthy = False

    others = [name for name, value in registry.values(IMPLICIT_KEY).items() if value == 0 and not _is_our_manifest(name)]
    for name in others:
        lines.append(f"Other implicit layer (this user): {name}")

    lines.append("Note: games started 'as administrator' ignore per-user OpenXR layers, including this one.")
    sessions = recent_game_sessions()
    if sessions:
        lines.append("Recent OpenXR games:")
        lines.extend(f"  {session.when}  {describe_session(session)}" for session in sessions)
    else:
        lines.append("Recent OpenXR games: none yet. Start an OpenXR game after enabling the layer.")
    return healthy, lines


# ---------------------------------------------------------------------------------------------------------
# CLI: vrtread-openxr status | enable | disable | doctor
# ---------------------------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the VR Treadmill OpenXR API layer.")
    parser.add_argument("command", choices=("status", "enable", "disable", "doctor"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            print(query_layer_status().message)
        elif args.command == "enable":
            print(enable_layer().message)
        elif args.command == "disable":
            print(disable_layer().message)
        else:
            healthy, lines = doctor()
            print("\n".join(lines))
            return 0 if healthy else 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
