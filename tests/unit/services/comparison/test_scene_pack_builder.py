# -*- coding: utf-8 -*-
"""Unit tests for the scene pack builder (Phase G1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

from src.services.comparison.scene_pack_builder import (
    OVERVIEW_LOD0_FILENAME,
    SCENE_PACK_FILENAME,
    SKELETON_TYPES,
    build_scene_pack,
    primitive_bbox,
)


def _make_sample_dxf(path: Path) -> None:
    """Create a tiny DXF with a known mix of entity types."""

    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 10))
    msp.add_line((10, 10), (20, 0))
    msp.add_circle((5, 5), 2)
    msp.add_arc((0, 0), 3, 0, 90)
    msp.add_lwpolyline([(0, 0), (5, 0), (5, 5), (0, 5), (0, 0)])
    msp.add_text("Hi", dxfattribs={"insert": (3, 3)})
    doc.saveas(str(path))


# ---------------------------------------------------------------------------
# primitive_bbox unit tests (pure-Python helpers)
# ---------------------------------------------------------------------------


def test_primitive_bbox_lines() -> None:
    prim = {
        "type": "lines",
        "geometry": [[0.0, 0.0, 10.0, 5.0], [-2.0, 3.0, 4.0, 9.0]],
    }
    assert primitive_bbox(prim) == (-2.0, 0.0, 10.0, 9.0)


def test_primitive_bbox_path_with_bezier() -> None:
    prim = {
        "type": "path",
        "geometry": [
            ["M", 0.0, 0.0],
            ["L", 10.0, 5.0],
            ["C", 12.0, 7.0, 14.0, 9.0, 16.0, 11.0],
            ["Z"],
        ],
    }
    bbox = primitive_bbox(prim)
    assert bbox == (0.0, 0.0, 16.0, 11.0)


def test_primitive_bbox_filled_paths() -> None:
    prim = {
        "type": "filled-paths",
        "geometry": [
            [["M", 0.0, 0.0], ["L", 5.0, 0.0], ["L", 5.0, 5.0], ["L", 0.0, 5.0]],
            [["M", 10.0, 10.0], ["L", 12.0, 10.0]],
        ],
    }
    assert primitive_bbox(prim) == (0.0, 0.0, 12.0, 10.0)


def test_primitive_bbox_unknown_type_returns_none() -> None:
    assert primitive_bbox({"type": "ufo", "geometry": []}) is None


def test_primitive_bbox_malformed_returns_none() -> None:
    assert primitive_bbox(None) is None  # type: ignore[arg-type]
    assert primitive_bbox({"type": "lines"}) is None  # missing geometry
    assert primitive_bbox({"type": "lines", "geometry": "not-a-list"}) is None


# ---------------------------------------------------------------------------
# build_scene_pack integration test
# ---------------------------------------------------------------------------


def test_build_scene_pack_writes_three_artifacts(tmp_path: Path) -> None:
    src = tmp_path / "sample.dxf"
    _make_sample_dxf(src)
    out_dir = tmp_path / "pack"

    result = build_scene_pack(src, out_dir)

    # No errors, primitives extracted.
    assert result.primitive_count > 0
    assert result.warnings == [] or all("Frontend" not in w for w in result.warnings)
    # Three artifacts present.
    assert (out_dir / SCENE_PACK_FILENAME).exists()
    assert (out_dir / OVERVIEW_LOD0_FILENAME).exists()
    # Index file: rtree by default → primitive_index.rtree + .meta.json
    assert any(out_dir.glob("primitive_index.*"))
    # ScenePackRef populated.
    ref = result.scene_pack_ref
    assert ref.primitive_count == result.primitive_count
    assert ref.json_path.endswith(SCENE_PACK_FILENAME)
    assert ref.overview_lod0_path.endswith(OVERVIEW_LOD0_FILENAME)


def test_build_scene_pack_overview_subset_is_skeleton_only(tmp_path: Path) -> None:
    src = tmp_path / "sample.dxf"
    _make_sample_dxf(src)
    out_dir = tmp_path / "pack"
    build_scene_pack(src, out_dir)

    overview_data = json.loads(
        (out_dir / OVERVIEW_LOD0_FILENAME).read_text(encoding="utf-8")
    )
    # Every primitive in the overview must be of a skeleton type.
    for prim in overview_data["primitives"]:
        assert prim["type"] in SKELETON_TYPES, (
            f"non-skeleton type in overview: {prim['type']!r}"
        )


def test_build_scene_pack_world_bbox_nonzero(tmp_path: Path) -> None:
    src = tmp_path / "sample.dxf"
    _make_sample_dxf(src)
    out_dir = tmp_path / "pack"
    result = build_scene_pack(src, out_dir)

    bbox = result.scene_pack_ref.drawing_world_bbox
    assert bbox != (0.0, 0.0, 0.0, 0.0)
    # Sample geometry roughly fits in this rectangle.
    assert bbox[0] <= 0.0 and bbox[2] >= 10.0


def test_build_scene_pack_grid_backend_force(tmp_path: Path) -> None:
    src = tmp_path / "sample.dxf"
    _make_sample_dxf(src)
    out_dir = tmp_path / "pack"
    result = build_scene_pack(src, out_dir, prefer_index_backend="grid")
    assert result.backend_used == "grid"
    # Grid backend writes a single .json index file.
    assert (out_dir / "primitive_index.json").exists()


def test_build_scene_pack_handles_missing_source(tmp_path: Path) -> None:
    out_dir = tmp_path / "pack"
    result = build_scene_pack(tmp_path / "does_not_exist.dxf", out_dir)
    assert result.primitive_count == 0
    assert result.backend_used == "none"
    assert result.warnings  # at least one warning surfaced


def test_build_scene_pack_artifacts_inside_output_dir(tmp_path: Path) -> None:
    src = tmp_path / "sample.dxf"
    _make_sample_dxf(src)
    out_dir = tmp_path / "deep" / "subdir" / "pack"
    result = build_scene_pack(src, out_dir)
    assert out_dir.exists()
    # All paths in the ref live inside the output dir.
    for p in (
        result.scene_pack_ref.json_path,
        result.scene_pack_ref.overview_lod0_path,
        result.scene_pack_ref.index_path,
    ):
        assert str(out_dir) in p, f"artifact escaped output dir: {p}"


def test_build_scene_pack_truncation_when_max_primitives_exceeded(tmp_path: Path) -> None:
    src = tmp_path / "sample.dxf"
    _make_sample_dxf(src)
    out_dir = tmp_path / "pack"
    result = build_scene_pack(src, out_dir, max_primitives=2)
    assert result.truncated is True
    assert any("Truncated" in w for w in result.warnings)
    assert result.primitive_count == 2
