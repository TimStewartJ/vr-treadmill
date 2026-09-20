from __future__ import annotations

import json
from pathlib import Path

from vrtread.openxr import (
    LAYER_NAME,
    OpenXrLayerStatus,
    default_layer_paths,
    layer_manifest_payload,
    write_layer_manifest,
)


def test_layer_manifest_payload_uses_absolute_dll_path(tmp_path: Path) -> None:
    dll_path = tmp_path / "bin" / "vrtread_openxr_layer.dll"

    payload = layer_manifest_payload(dll_path)

    assert payload["api_layer"]["name"] == LAYER_NAME
    assert payload["api_layer"]["library_path"] == str(dll_path.resolve())
    assert payload["api_layer"]["implementation_version"] == "3"
    assert payload["api_layer"]["disable_environment"] == "VRTREAD_OPENXR_DISABLE"


def test_write_layer_manifest_round_trip(tmp_path: Path) -> None:
    paths = default_layer_paths(tmp_path)

    manifest_path = write_layer_manifest(paths)

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_path == paths.manifest_path
    assert raw == layer_manifest_payload(paths.dll_path)


def test_layer_status_requires_automatic_registration(tmp_path: Path) -> None:
    paths = default_layer_paths(tmp_path)
    paths.dll_path.parent.mkdir(parents=True)
    paths.dll_path.write_bytes(b"fake")
    paths.manifest_path.parent.mkdir(parents=True)
    paths.manifest_path.write_text("{}", encoding="utf-8")

    status = OpenXrLayerStatus(
        explicit_registered=True,
        implicit_registered=False,
        manifest_exists=True,
        dll_exists=True,
        manifest_path=paths.manifest_path,
        dll_path=paths.dll_path,
    )

    assert not status.ready
    assert "legacy explicit" in status.message
