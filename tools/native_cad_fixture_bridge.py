"""Deterministic native CAD bridge fixture for contract tests.

The script accepts an input DWG-like path and ACAD version code, then emits the
same JSON shape an approved native bridge must produce. It does not parse DWG
sections; tests use it to validate the Python-side contract and comparison
flow without requiring a local CAD application.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.comparison.native_scene_pack import (  # noqa: E402
    NativeScenePack,
    bridge_payload_from_scene_pack,
    source_signature,
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: native_cad_fixture_bridge.py <input-dwg> [acadver]", file=sys.stderr)
        return 2
    path = Path(args[0])
    acadver = args[1] if len(args) > 1 else _detect_acadver(path)
    payload = bridge_payload_from_scene_pack(
        _scene_pack(path, acadver),
        drawing=_drawing_payload(acadver, modified=_is_modified(path)),
    )
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


def _detect_acadver(path: Path) -> str:
    try:
        return path.read_bytes()[:6].decode("ascii")
    except Exception:
        return "AC0000"


def _is_modified(path: Path) -> bool:
    stem = path.stem.casefold()
    return any(token in stem for token in ("after", "modified", "new", "r1", "rev"))


def _scene_pack(path: Path, acadver: str) -> NativeScenePack:
    modified = _is_modified(path)
    mark = "H-450" if modified else "H-400"
    drawing = _drawing_payload(acadver, modified=modified)
    return NativeScenePack(
        source={
            **source_signature(path),
            "format": "dwg",
            "acad_version": acadver,
            "fixture_variant": "modified" if modified else "base",
        },
        adapter={
            "name": "native-cad-fixture-bridge",
            "version": "1",
            "contract": "native-cad-bridge-result/v1",
        },
        layouts=[
            {"name": "Model", "paper_space": False, "origin": [0.0, 0.0]},
            {"name": "S-101", "paper_space": True, "paper_size_mm": [841.0, 594.0]},
        ],
        layers=drawing["layers"],
        blocks=drawing["blocks"],
        entities=drawing["model_space"],
        display_primitives=[
            {
                "id": "steel-axis",
                "type": "lines",
                "geometry": [[0.0, 0.0, 120.0, 0.0], [0.0, 35.0, 120.0, 35.0]],
                "layer": "STEEL",
                "stroke": "#2f6f8f",
            },
            {
                "id": "frame-outline",
                "type": "path",
                "geometry": [
                    ["M", 0.0, 0.0],
                    ["L", 120.0, 0.0],
                    ["L", 120.0, 60.0],
                    ["L", 0.0, 60.0],
                    ["Z"],
                ],
                "layer": "FRAME",
                "stroke": "#555555",
            },
        ],
        dimensions=[
            {
                "handle": "D1",
                "measurement": 120.0,
                "text": "120000",
                "style": "ISO-25",
                "associative": True,
            }
        ],
        text_runs=[
            {"handle": "20", "text": f"BEAM {mark}", "font": "Arial", "layout": "Model"}
        ],
        xrefs=[
            {
                "name": "GRID_BASE",
                "resolved": False,
                "policy": "placeholder",
                "path_hint": "grid_base.dwg",
            }
        ],
        coordinate_spaces={
            "model": {"origin": [0.0, 0.0, 0.0], "units": "mm"},
            "paper": {"origin": [0.0, 0.0, 0.0], "units": "mm"},
        },
        bbox=(0.0, 0.0, 120.0, 60.0),
        warnings=[],
        metadata={"fixture": True, "comparison_mark": mark},
    )


def _drawing_payload(acadver: str, *, modified: bool) -> dict[str, Any]:
    mark = "H-450" if modified else "H-400"
    return {
        "header": {"$ACADVER": acadver, "$INSUNITS": 4},
        "layers": [
            {"name": "STEEL", "color": 3, "linetype": "Continuous", "lineweight": 25},
            {"name": "ANNO", "color": 7, "linetype": "Continuous"},
            {"name": "FRAME", "color": 8, "linetype": "Continuous"},
        ],
        "blocks": [
            {
                "name": "GRID_TAG",
                "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
                "entities": [
                    {
                        "type": "LINE",
                        "handle": "B10",
                        "layer": "FRAME",
                        "geometry": {
                            "start": {"x": -2.0, "y": 0.0, "z": 0.0},
                            "end": {"x": 2.0, "y": 0.0, "z": 0.0},
                        },
                    }
                ],
            }
        ],
        "model_space": [
            {
                "type": "LINE",
                "handle": "10",
                "layer": "STEEL",
                "style": {"color": 256, "linetype": "BYLAYER", "lineweight": -1},
                "geometry": {
                    "start": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "end": {"x": 120.0, "y": 0.0, "z": 0.0},
                },
            },
            {
                "type": "TEXT",
                "handle": "20",
                "layer": "ANNO",
                "geometry": {
                    "insert": {"x": 10.0, "y": 16.0, "z": 0.0},
                    "height": 2.5,
                    "text": f"BEAM {mark}",
                    "rotation_deg": 0.0,
                },
            },
            {
                "type": "INSERT",
                "handle": "30",
                "layer": "FRAME",
                "geometry": {
                    "block_name": "GRID_TAG",
                    "insert": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 1.0, "y": 1.0, "z": 1.0},
                    "rotation_deg": 0.0,
                    "attributes": [{"tag": "GRID", "text": "A1"}],
                },
            },
        ],
        "metadata": {"fixture": True, "variant": "modified" if modified else "base"},
    }


if __name__ == "__main__":
    raise SystemExit(main())
