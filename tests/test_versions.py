from __future__ import annotations

import re
from pathlib import Path

from vrtread import __version__
from vrtread.openxr import DLL_FILE_NAME, LAYER_NAME

REPO = Path(__file__).resolve().parents[1]


def test_one_version_everywhere() -> None:
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    cmake = (REPO / "native" / "openxr_layer" / "CMakeLists.txt").read_text(encoding="utf-8")
    installer = (REPO / "packaging" / "VRTreadmill.iss").read_text(encoding="utf-8")

    assert re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1) == __version__
    assert re.search(r"project\(vrtread_openxr_layer VERSION ([0-9.]+)", cmake).group(1) == __version__
    assert re.search(r'#define MyAppVersion "([^"]+)"', installer).group(1) == __version__


def test_layer_identity_is_consistent_between_python_and_native() -> None:
    layer_source = (REPO / "native" / "openxr_layer" / "src" / "layer.cpp").read_text(encoding="utf-8")
    cmake = (REPO / "native" / "openxr_layer" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert f'kLayerName[] = "{LAYER_NAME}"' in layer_source
    assert f'OUTPUT_NAME "{Path(DLL_FILE_NAME).stem}"' in cmake
