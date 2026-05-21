# -*- coding: utf-8 -*-
"""Unit tests for the primitive-bbox spatial index (Phase G1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison.viewer_spatial_index import (
    GridPrimitiveIndex,
    INDEX_FORMAT_VERSION,
    RTreePrimitiveIndex,
    build_primitive_index,
    load_primitive_index,
)

# A small fixture: 5 primitives with known bboxes.
SAMPLE_PRIMITIVES = [
    (0, (0.0, 0.0, 10.0, 10.0)),
    (1, (5.0, 5.0, 15.0, 15.0)),
    (2, (20.0, 20.0, 30.0, 30.0)),
    (3, (-5.0, -5.0, -1.0, -1.0)),
    (4, (100.0, 100.0, 110.0, 110.0)),
]


# ---------------------------------------------------------------------------
# Grid backend (always available)
# ---------------------------------------------------------------------------


def test_grid_backend_query_finds_overlap() -> None:
    idx = build_primitive_index(SAMPLE_PRIMITIVES, prefer_backend="grid")
    hits = idx.query_overlap((4.0, 4.0, 6.0, 6.0))
    # Both 0 and 1 overlap this query.
    assert 0 in hits
    assert 1 in hits


def test_grid_backend_query_returns_empty_for_empty_region() -> None:
    idx = build_primitive_index(SAMPLE_PRIMITIVES, prefer_backend="grid")
    hits = idx.query_overlap((50.0, 50.0, 60.0, 60.0))
    assert hits == []


def test_grid_backend_query_handles_inverted_bbox() -> None:
    idx = build_primitive_index(SAMPLE_PRIMITIVES, prefer_backend="grid")
    # bbox passed with x1<x0 and y1<y0 — must still work
    hits = idx.query_overlap((6.0, 6.0, 4.0, 4.0))
    assert 0 in hits
    assert 1 in hits


def test_grid_backend_dedupes_when_primitive_spans_many_cells() -> None:
    # One huge primitive that spans many cells.
    huge = [(0, (0.0, 0.0, 1000.0, 1000.0))]
    idx = build_primitive_index(huge, prefer_backend="grid")
    hits = idx.query_overlap((100.0, 100.0, 200.0, 200.0))
    assert hits.count(0) == 1, "dedupe failed; got %r" % hits


def test_grid_backend_world_bbox_unions_all_primitives() -> None:
    idx = build_primitive_index(SAMPLE_PRIMITIVES, prefer_backend="grid")
    assert idx.world_bbox == (-5.0, -5.0, 110.0, 110.0)


def test_grid_backend_save_and_load_round_trip(tmp_path: Path) -> None:
    idx = build_primitive_index(SAMPLE_PRIMITIVES, prefer_backend="grid")
    path = tmp_path / "primitive_index.json"
    idx.save_to_disk(path)
    assert path.exists()
    loaded = load_primitive_index(path)
    assert loaded.backend == "grid"
    assert loaded.primitive_count == 5
    hits = loaded.query_overlap((4.0, 4.0, 6.0, 6.0))
    assert 0 in hits and 1 in hits


def test_grid_backend_load_rejects_format_version_mismatch(tmp_path: Path) -> None:
    import json
    p = tmp_path / "stale_index.json"
    p.write_text(json.dumps({
        "format_version": INDEX_FORMAT_VERSION + 99,
        "backend": "grid",
        "cell_size": 1.0,
        "world_bbox": [0, 0, 1, 1],
        "primitive_count": 0,
        "bboxes": {},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="format version"):
        load_primitive_index(p)


def test_grid_backend_empty_input_returns_empty_index() -> None:
    idx = build_primitive_index([], prefer_backend="grid")
    assert idx.primitive_count == 0
    assert idx.query_overlap((0.0, 0.0, 10.0, 10.0)) == []


# ---------------------------------------------------------------------------
# R-tree backend (skipped if rtree missing)
# ---------------------------------------------------------------------------


def _has_rtree() -> bool:
    try:
        import rtree  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_rtree(), reason="rtree not installed")
def test_rtree_backend_query_matches_grid() -> None:
    grid = build_primitive_index(SAMPLE_PRIMITIVES, prefer_backend="grid")
    rtree_idx = build_primitive_index(SAMPLE_PRIMITIVES)  # default: prefer rtree
    assert rtree_idx.backend == "rtree"
    grid_hits = sorted(grid.query_overlap((4.0, 4.0, 6.0, 6.0)))
    rtree_hits = sorted(rtree_idx.query_overlap((4.0, 4.0, 6.0, 6.0)))
    # rtree may return fewer (it's exact); grid may overselect. Both must
    # contain the truly overlapping primitives 0 and 1.
    assert 0 in rtree_hits
    assert 1 in rtree_hits
    # All rtree hits must also be grid hits (grid is a superset).
    for h in rtree_hits:
        assert h in grid_hits


@pytest.mark.skipif(not _has_rtree(), reason="rtree not installed")
def test_rtree_backend_query_returns_int_ids() -> None:
    idx = build_primitive_index(SAMPLE_PRIMITIVES)
    hits = idx.query_overlap((4.0, 4.0, 6.0, 6.0))
    assert all(isinstance(h, int) for h in hits)


@pytest.mark.skipif(not _has_rtree(), reason="rtree not installed")
def test_rtree_backend_save_and_load_round_trip(tmp_path: Path) -> None:
    idx = build_primitive_index(SAMPLE_PRIMITIVES)
    assert idx.backend == "rtree"
    path = tmp_path / "primitive_index.rtree"
    idx.save_to_disk(path)
    # rtree writes .idx + .dat + .meta.json sidecar
    assert (path.with_suffix(".meta.json")).exists()
    assert (path.with_suffix(".idx")).exists()
    assert (path.with_suffix(".dat")).exists()
    loaded = load_primitive_index(path)
    assert loaded.backend == "rtree"
    assert loaded.primitive_count == 5
    hits = loaded.query_overlap((4.0, 4.0, 6.0, 6.0))
    assert 0 in hits and 1 in hits


@pytest.mark.skipif(not _has_rtree(), reason="rtree not installed")
def test_rtree_backend_handles_inverted_bbox() -> None:
    idx = build_primitive_index(SAMPLE_PRIMITIVES)
    hits = idx.query_overlap((6.0, 6.0, 4.0, 4.0))
    assert 0 in hits and 1 in hits


# ---------------------------------------------------------------------------
# Factory / cross-backend behaviour
# ---------------------------------------------------------------------------


def test_factory_prefers_rtree_when_available() -> None:
    # When rtree is present, default factory should pick it.
    if _has_rtree():
        idx = build_primitive_index(SAMPLE_PRIMITIVES)
        assert idx.backend == "rtree"


def test_factory_force_grid_overrides_default() -> None:
    idx = build_primitive_index(SAMPLE_PRIMITIVES, prefer_backend="grid")
    assert idx.backend == "grid"


def test_factory_handles_empty_primitives() -> None:
    idx = build_primitive_index([], prefer_backend="grid")
    assert idx.primitive_count == 0
