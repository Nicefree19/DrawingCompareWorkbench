# -*- coding: utf-8 -*-
"""Clean-room native DWG expansion goldens — X1 HATCH / X2 INSERT / X3 POLYLINE.

own_viewer_expansion harness (.harness/own_viewer_expansion_20260629). These
close the *honest partial* residue the own_viewer_completion harness left:

* X3 — legacy POLYLINE: the old POLYLINE_2D (0x0F) / POLYLINE_3D (0x10) header +
  its owned VERTEX (0x0A / 0x0B) children + the SEQEND (0x06) terminator. The
  reader emits a canonical "polyline" carrying the assembled vertices.
* X1 — HATCH real boundary loops: the reader carries the boundary LINE/ARC edge
  loops into canonical hatch (not just the world bbox), and the scene-pack
  builder renders the real boundary, dropping the ``partial`` flag.
* X2 — INSERT block expansion in the diagnostic canonical: ``build_r2018_canonical_document``
  populates ``doc["blocks"]`` (entmode==0 grouped by owner) so an INSERT expands
  its block geometry instead of a marker box.

GROUND TRUTH PROVENANCE (validation-only, NEVER product code):
  ODAFileConverter "26.10.0" (offline golden oracle) converted the local
  git-ignored ``.local/native_cad_real_samples/acadsharp/sample_AC1032.dwg`` to an
  ACAD2018 DXF; ezdxf (1.3.x, validation-only) read the modelspace/blocks. The
  constants below are the EXACT geometry ODA reports, keyed by entity handle. The
  native clean-room decoder reproduces them with ZERO ODA/ezdxf call at runtime.
  Tolerances: POLYLINE vertices 1e-6, HATCH boundary vertices 1e-3 (per DoD) —
  NOT weakened. Real-file tests SKIP when the local corpus is absent (CI-safe).

Harness selectors: test names carry ``polyline2d_decode`` (X3), ``hatch_boundary``
(X1), ``insert_expansion`` (X2) so ``-k`` picks each DoD independently.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison.dwg_r2018_reader import (
    R2018_VERSION_CODE,
    build_r2018_canonical_document,
    read_r2018_entities,
)
from src.services.comparison.native_scene_pack_builder import build_native_scene_pack

_AC1032_SAMPLE = Path(".local/native_cad_real_samples/acadsharp/sample_AC1032.dwg")


def _approx(actual: float, expected: float, tol: float = 1e-6) -> bool:
    return abs(actual - expected) <= tol


# ---------------------------------------------------------------------------
# X3 — legacy POLYLINE (POLYLINE_3D 0x10 + VERTEX_3D 0x0B children).
# ODA GT: modelspace POLYLINE handle 0x42B (DXF flags=8, 3D polyline), 5 vertices.
# (The 3D-polyline vertex Z is what ODA's DXF surfaces; verified 1:1 below.)
# ---------------------------------------------------------------------------
_GT_POLYLINE_3D_HANDLE = 0x42B
_GT_POLYLINE_3D_VERTICES = [
    (232.60172074430375, 0.8926935903469939, 0.0),
    (233.41498972012593, 3.3485871039181467, 5.47860020423747),
    (229.03210955673597, 4.7361188530519485, 0.0),
    (227.8788384227953, 8.433590754934926, 5.47860020423747),
    (225.74494943419347, 9.922907349003111, 0.0),
]


def test_real_ac1032_polyline2d_decode_matches_ground_truth() -> None:
    # The clean-room reader decodes the legacy POLYLINE (header + owned VERTEX
    # children + SEQEND) and emits a canonical "polyline" whose assembled vertices
    # match ODA's ground truth within 1e-6. Before this DoD the reader had no
    # 0x0F/0x10 decoder, so the polyline was dropped (no POLYLINE entity, no
    # vertices) — see negatives/tx3_pre.log.
    if not _AC1032_SAMPLE.exists():
        pytest.skip(f"local AC1032 sample not present: {_AC1032_SAMPLE}")

    table = read_r2018_entities(_AC1032_SAMPLE.read_bytes(), version_code=R2018_VERSION_CODE)
    assert table.status == "decoded", table.message
    assert table.type_counts.get("POLYLINE", 0) >= 1, table.type_counts

    by = {e.handle: e for e in table.entities}
    assert _GT_POLYLINE_3D_HANDLE in by, sorted(f"{h:X}" for h in by)
    poly = by[_GT_POLYLINE_3D_HANDLE]
    assert poly.type_name == "POLYLINE", poly.type_name
    verts = poly.geometry["vertices"]
    assert len(verts) == len(_GT_POLYLINE_3D_VERTICES), verts
    for got, want in zip(verts, _GT_POLYLINE_3D_VERTICES):
        assert all(_approx(a, b) for a, b in zip(got, want)), (got, want)


def test_real_ac1032_polyline2d_decode_round_trips_to_canonical() -> None:
    # The decoded legacy POLYLINE flows into the canonical document AND the
    # scene-pack builder as a real polyline (segments between consecutive
    # vertices), not a dropped/unsupported entity.
    if not _AC1032_SAMPLE.exists():
        pytest.skip(f"local AC1032 sample not present: {_AC1032_SAMPLE}")

    doc = build_r2018_canonical_document(
        _AC1032_SAMPLE.read_bytes(), version_code=R2018_VERSION_CODE
    )
    handle_hex = f"{_GT_POLYLINE_3D_HANDLE:X}"
    poly = next(
        (e for e in doc["entities"] if e.get("handle") == handle_hex), None
    )
    assert poly is not None, "legacy polyline missing from canonical document"
    assert poly["type"] == "polyline"
    assert len(poly["geometry"]["vertices"]) == len(_GT_POLYLINE_3D_VERTICES)

    pack = build_native_scene_pack(doc)
    prim = next((p for p in pack.display_primitives if p.get("id") == poly["id"]), None)
    assert prim is not None, "legacy polyline produced no display primitive"
    # 5 vertices, open polyline => 4 line segments.
    assert len(prim["geometry"]) == len(_GT_POLYLINE_3D_VERTICES) - 1


# ---------------------------------------------------------------------------
# X1 — HATCH real boundary loops.
# ODA GT: HATCH handle 0x35A, single EdgePath of 4 LINE edges (a rectangle).
# ---------------------------------------------------------------------------
_GT_HATCH_HANDLE = 0x35A
# The 4 corner vertices of the rectangular LINE-edge boundary (ODA edge starts).
_GT_HATCH_BOUNDARY = [
    (102.7457616028918, 0.0853177934775715),
    (123.1990874847903, 0.0853177934775715),
    (123.1990874847903, 13.71006874482629),
    (102.7457616028918, 13.71006874482629),
]


def _boundary_points(geometry: dict) -> list:
    pts = []
    for loop in geometry.get("boundary_loops") or []:
        for vertex in loop.get("vertices") or []:
            pts.append((vertex["x"], vertex["y"]))
    return pts


def test_real_ac1032_hatch_boundary_matches_ground_truth() -> None:
    # The reader carries the HATCH boundary loop polylines (LINE/ARC edges) into
    # canonical hatch geometry; the boundary vertices match ODA within 1e-3.
    # Before this DoD canonical hatch carried only the world bbox (no
    # boundary_loops) — see negatives/tx1_pre.log.
    if not _AC1032_SAMPLE.exists():
        pytest.skip(f"local AC1032 sample not present: {_AC1032_SAMPLE}")

    doc = build_r2018_canonical_document(
        _AC1032_SAMPLE.read_bytes(), version_code=R2018_VERSION_CODE
    )
    handle_hex = f"{_GT_HATCH_HANDLE:X}"
    hatch = next(
        (e for e in doc["entities"] if e.get("handle") == handle_hex), None
    )
    assert hatch is not None, "target HATCH missing from canonical document"
    assert hatch["type"] == "hatch"
    loops = hatch["geometry"].get("boundary_loops")
    assert loops, "HATCH carries no boundary_loops (still bbox-only)"
    points = _boundary_points(hatch["geometry"])
    # Every ODA corner must be matched by a decoded boundary vertex within 1e-3.
    for want in _GT_HATCH_BOUNDARY:
        assert any(
            _approx(px, want[0], 1e-3) and _approx(py, want[1], 1e-3)
            for px, py in points
        ), (want, points)


def test_real_ac1032_hatch_boundary_renders_real_lines_not_bbox() -> None:
    # The scene-pack builder renders the real boundary (4 distinct LINE segments
    # of the rectangle) and drops the ``partial`` flag for a fully-decoded
    # LINE-edge boundary (no longer a single bbox rectangle approximation).
    if not _AC1032_SAMPLE.exists():
        pytest.skip(f"local AC1032 sample not present: {_AC1032_SAMPLE}")

    doc = build_r2018_canonical_document(
        _AC1032_SAMPLE.read_bytes(), version_code=R2018_VERSION_CODE
    )
    pack = build_native_scene_pack(doc)
    handle_hex = f"{_GT_HATCH_HANDLE:X}"
    hatch = next((e for e in doc["entities"] if e.get("handle") == handle_hex), None)
    prim = next((p for p in pack.display_primitives if p.get("id") == hatch["id"]), None)
    assert prim is not None, "HATCH produced no display primitive"
    # 4 LINE edges -> 4 segments; full boundary => not partial.
    assert len(prim["geometry"]) >= 4
    assert not prim.get("properties", {}).get("partial"), prim.get("properties")


# ---------------------------------------------------------------------------
# X2 — INSERT block expansion in the diagnostic canonical document.
# ODA GT: INSERT 0x704 -> block 'MyBlock' (LINE x2 + CIRCLE + ATTDEF). Expanding
# it yields > 1 line primitive (>> the 4-segment marker box).
# ---------------------------------------------------------------------------
_GT_INSERT_HANDLE = 0x704
_GT_INSERT_BLOCK = "MyBlock"


def test_real_ac1032_insert_expansion_populates_blocks_and_expands() -> None:
    # build_r2018_canonical_document populates doc["blocks"] (was []), and the
    # scene-pack builder expands an INSERT's block geometry (transformed) instead
    # of rendering a 4-segment marker box. Before this DoD blocks==[] and INSERT
    # rendered a marker — see negatives/tx2_pre.log.
    if not _AC1032_SAMPLE.exists():
        pytest.skip(f"local AC1032 sample not present: {_AC1032_SAMPLE}")

    doc = build_r2018_canonical_document(
        _AC1032_SAMPLE.read_bytes(), version_code=R2018_VERSION_CODE
    )
    assert doc["blocks"], "diagnostic canonical still emits blocks=[]"

    pack = build_native_scene_pack(doc)
    handle_hex = f"{_GT_INSERT_HANDLE:X}"
    insert = next((e for e in doc["entities"] if e.get("handle") == handle_hex), None)
    assert insert is not None, "target INSERT missing from canonical document"
    assert insert["type"] == "insert"
    assert insert["geometry"]["block_name"] == _GT_INSERT_BLOCK

    # The INSERT must expand into MORE than a 4-segment marker box: one ``lines``
    # primitive (the INSERT's id) carrying the transformed block geometry. MyBlock
    # has 2 LINE + 1 CIRCLE => 2 + circle_segments line segments (>> 4).
    prim = next((p for p in pack.display_primitives if p.get("id") == insert["id"]), None)
    assert prim is not None, "INSERT produced no display primitive"
    assert prim["type"] == "lines"
    assert len(prim["geometry"]) > 4, len(prim["geometry"])
    props = prim.get("properties", {})
    # Expanded => flagged as a real block expansion, NOT the marker-box partial.
    assert props.get("render") != "insert_marker", props
    assert props.get("expanded_block") == _GT_INSERT_BLOCK, props
