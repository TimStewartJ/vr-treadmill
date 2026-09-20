from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LAYER_NAME = "XR_APILAYER_VRTREAD_treadmill"
LAYER_DESCRIPTION = "VR Treadmill automatic OpenXR locomotion layer"
REGISTRY_BASE = r"Software\Khronos\OpenXR\1\ApiLayers"
EXPLICIT_REGISTRY_PATH = REGISTRY_BASE + r"\Explicit"
IMPLICIT_REGISTRY_PATH = REGISTRY_BASE + r"\Implicit"


@dataclass(frozen=True, slots=True)
class OpenXrLayerPaths:
    manifest_path: Path
    dll_path: Path


@dataclass(frozen=True, slots=True)
class OpenXrLayerStatus:
    explicit_registered: bool
    implicit_registered: bool
    manifest_exists: bool
    dll_exists: bool
    manifest_path: Path
    dll_path: Path

    @property
    def automatic_registered(self) -> bool:
        return self.implicit_registered

    @property
    def legacy_explicit_registered(self) -> bool:
        return self.explicit_registered

    @property
    def ready(self) -> bool:
        return self.manifest_exists and self.dll_exists and self.automatic_registered

    @property
    def message(self) -> str:
        if self.ready:
            return "Automatic OpenXR layer: enabled"
        if self.legacy_explicit_registered and self.manifest_exists and self.dll_exists:
            return "Automatic OpenXR layer: disabled (legacy explicit entry found)"
        if not self.dll_exists:
            return "Automatic OpenXR layer: native DLL not built"
        if not self.manifest_exists:
            return "Automatic OpenXR layer: manifest missing"
        return "Automatic OpenXR layer: disabled"


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def default_layer_paths(root: Path | None = None) -> OpenXrLayerPaths:
    base = root or project_root()
    native_dir = base / "native" / "openxr_layer"
    if not native_dir.exists() and (base / "openxr_layer").exists():
        native_dir = base / "openxr_layer"
    return OpenXrLayerPaths(
        manifest_path=native_dir / "generated" / f"{LAYER_NAME}.json",
        dll_path=native_dir / "bin" / "vrtread_openxr_layer.dll",
    )


def layer_manifest_payload(dll_path: Path) -> dict[str, Any]:
    return {
        "file_format_version": "1.0.0",
        "api_layer": {
            "name": LAYER_NAME,
            "library_path": str(dll_path.resolve()),
            "api_version": "1.0",
            "implementation_version": "3",
            "description": LAYER_DESCRIPTION,
            "disable_environment": "VRTREAD_OPENXR_DISABLE",
        },
    }


def write_layer_manifest(paths: OpenXrLayerPaths | None = None) -> Path:
    layer_paths = paths or default_layer_paths()
    layer_paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = layer_manifest_payload(layer_paths.dll_path)
    layer_paths.manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return layer_paths.manifest_path


def query_layer_status(paths: OpenXrLayerPaths | None = None) -> OpenXrLayerStatus:
    layer_paths = paths or default_layer_paths()
    return OpenXrLayerStatus(
        explicit_registered=_is_registered(layer_paths.manifest_path, implicit=False),
        implicit_registered=_is_registered(layer_paths.manifest_path, implicit=True),
        manifest_exists=layer_paths.manifest_path.exists(),
        dll_exists=layer_paths.dll_path.exists(),
        manifest_path=layer_paths.manifest_path,
        dll_path=layer_paths.dll_path,
    )


def register_layer(paths: OpenXrLayerPaths | None = None, *, implicit: bool = True) -> OpenXrLayerStatus:
    if platform.system() != "Windows":
        raise RuntimeError("OpenXR layer registration is Windows-only.")
    layer_paths = paths or default_layer_paths()
    write_layer_manifest(layer_paths)
    manifest_path = layer_paths.manifest_path.resolve()

    import winreg

    registry_path = IMPLICIT_REGISTRY_PATH if implicit else EXPLICIT_REGISTRY_PATH
    wrote_any_view = False
    last_error: OSError | None = None
    for view_flag in _registry_view_flags():
        access = winreg.KEY_SET_VALUE | view_flag
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, registry_path, 0, access) as key:
                winreg.SetValueEx(key, str(manifest_path), 0, winreg.REG_DWORD, 0)
                wrote_any_view = True
        except OSError as exc:
            last_error = exc

    if not wrote_any_view and last_error is not None:
        raise last_error

    if implicit:
        _delete_registration(manifest_path, implicit=False)
    return query_layer_status(layer_paths)


def unregister_layer(paths: OpenXrLayerPaths | None = None, *, implicit: bool | None = None) -> OpenXrLayerStatus:
    if platform.system() != "Windows":
        raise RuntimeError("OpenXR layer registration is Windows-only.")
    layer_paths = paths or default_layer_paths()
    if implicit is None:
        _delete_registration(layer_paths.manifest_path, implicit=False)
        _delete_registration(layer_paths.manifest_path, implicit=True)
    else:
        _delete_registration(layer_paths.manifest_path, implicit=implicit)
    return query_layer_status(layer_paths)


def ensure_automatic_layer(paths: OpenXrLayerPaths | None = None) -> OpenXrLayerStatus:
    status = query_layer_status(paths)
    if status.ready:
        return status
    if not status.dll_exists:
        raise RuntimeError(
            f"OpenXR layer DLL is missing. Build the layer first: {status.dll_path}"
        )
    return register_layer(paths, implicit=True)


def _registry_view_flags() -> tuple[int, ...]:
    if platform.system() != "Windows":
        return (0,)

    import winreg

    flags = [
        getattr(winreg, "KEY_WOW64_64KEY", 0),
        getattr(winreg, "KEY_WOW64_32KEY", 0),
    ]
    unique_flags: list[int] = []
    for flag in flags:
        if flag not in unique_flags:
            unique_flags.append(flag)
    return tuple(unique_flags) or (0,)


def _is_registered(manifest_path: Path, *, implicit: bool) -> bool:
    if platform.system() != "Windows":
        return False
    registry_path = IMPLICIT_REGISTRY_PATH if implicit else EXPLICIT_REGISTRY_PATH

    import winreg

    for view_flag in _registry_view_flags():
        access = winreg.KEY_READ | view_flag
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path, 0, access) as key:
                value, _value_type = winreg.QueryValueEx(key, str(manifest_path.resolve()))
                if int(value) == 0:
                    return True
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return False


def _delete_registration(manifest_path: Path, *, implicit: bool) -> None:
    registry_path = IMPLICIT_REGISTRY_PATH if implicit else EXPLICIT_REGISTRY_PATH

    import winreg

    for view_flag in _registry_view_flags():
        access = winreg.KEY_SET_VALUE | view_flag
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path, 0, access) as key:
                winreg.DeleteValue(key, str(manifest_path.resolve()))
        except FileNotFoundError:
            continue
        except OSError:
            continue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the VR Treadmill OpenXR API layer.")
    parser.add_argument(
        "command",
        choices=("status", "register", "unregister", "write-manifest"),
        help="OpenXR layer helper command.",
    )
    parser.add_argument(
        "--explicit",
        action="store_true",
        help="Register as a legacy explicit layer for diagnostics. Automatic implicit registration is the default.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            status = query_layer_status()
            _print_status(status)
        elif args.command == "register":
            status = register_layer(implicit=not args.explicit)
            _print_status(status)
        elif args.command == "unregister":
            status = unregister_layer(implicit=False if args.explicit else None)
            _print_status(status)
        elif args.command == "write-manifest":
            path = write_layer_manifest()
            print(path)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def _print_status(status: OpenXrLayerStatus) -> None:
    print(status.message)
    print(f"Manifest: {status.manifest_path}")
    print(f"DLL: {status.dll_path}")
    print(f"Automatic registered: {status.implicit_registered}")
    print(f"Legacy explicit registered: {status.explicit_registered}")


if __name__ == "__main__":
    raise SystemExit(main())
