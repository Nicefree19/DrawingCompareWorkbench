# -*- coding: utf-8 -*-
"""Tests for the native canonical-entity -> NativeScenePack producer.

Covers the missing producer for ``viewer_lod0_real_evidence_pending``: it must
flatten clean-room native-reader canonical geometry into viewer ``lines``
primitives, count (not silently drop) unsupported entity types, and feed the
existing ``native-cad-viewer-evidence/v1`` path to a real, framed, within-budget
evidence packet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.services.comparison.dwg_importer import DwgImporter
from src.services.comparison.dwg_native_reader import DwgNativeAc1015Adapter
from src.services.comparison.native_scene_pack import native_scene_viewer_evidence_payload
from src.services.comparison.native_scene_pack_builder import (
    DEFAULT_CIRCLE_SEGMENTS,
    PRODUCER_ID,
    build_native_scene_pack,
    build_native_scene_pack_ref,
)
from src.services.comparison.viewer_primitive_source import resolve_viewer_primitive_source

# Local-only AC1015 real sample (git-ignored corpus). The integration test
# below runs the full native-import -> producer -> evidence chain when it is
# present and skips visibly otherwise, so CI proves producer logic via the
# hand-built canonical docs while local runs prove the real-sample chain.
REAL_AC1015_SAMPLE = Path(
    ".local/native_cad_real_samples/nextgis_dwg_samples/line_2000.dwg"
)


def _canonical_doc(entities: list[dict[str, Any]], extents: dict[str, Any] | None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema_version": "canonical-drawing/v1",
        "drawing": {
            "source": {"path": "synthetic", "acad_version": "AC1015", "format": "dwg"},
            "importer": {"name": "native-ac1015", "backend": "native"},
        },
        "layers": [{"id": "layer:0", "name": "0"}],
        "blocks": [],
        "entities": entities,
        "import_report": {"status": "ok", "error_code": None, "adapter": {"name": "native-ac1015"}},
    }
    if extents is not None:
        doc["extents"] = extents
    return doc


def _entity(entity_id: str, etype: str, geometry: dict[str, Any]) -> dict[str, Any]:
    return {"id": entity_id, "type": etype, "layer_id": "layer:0", "geometry": geometry}


def test_build_native_scene_pack_flattens_each_geometry_type() -> None:
    doc = _canonical_doc(
        entities=[
            _entity("line:1", "line", {"type": "line", "start": {"x": 0.0, "y": 0.0, "z": 0.0}, "end": {"x": 10.0, "y": 0.0, "z": 0.0}}),
            _entity("circle:1", "circle", {"type": "circle", "center": {"x": 5.0, "y": 5.0, "z": 0.0}, "radius": 2.5}),
            _entity("arc:1", "arc", {"type": "arc", "center": {"x": 0.0, "y": 0.0, "z": 0.0}, "radius": 5.0, "start_angle_deg": 0.0, "end_angle_deg": 90.0, "sweep_direction": "ccw"}),
            _entity("poly:1", "polyline", {"type": "polyline", "vertices": [{"point": {"x": 0.0, "y": 0.0}}, {"point": {"x": 1.0, "y": 1.0}}, {"point": {"x": 2.0, "y": 0.0}}], "closed": False}),
        ],
        extents={"min_x": -5.0, "min_y": -5.0, "max_x": 10.0, "max_y": 10.0},
    )

    pack = build_native_scene_pack(doc)

    by_id = {p["id"]: p for p in pack.display_primitives}
    assert set(by_id) == {"line:1", "circle:1", "arc:1", "poly:1"}
    assert all(p["type"] == "lines" for p in pack.display_primitives)
    # line -> a single segment with the exact endpoints.
    assert by_id["line:1"]["geometry"] == [[0.0, 0.0, 10.0, 0.0]]
    # circle -> a closed N-gon (DEFAULT_CIRCLE_SEGMENTS segments).
    assert len(by_id["circle:1"]["geometry"]) == DEFAULT_CIRCLE_SEGMENTS
    # 90-degree arc -> a proportional slice of the circle tessellation.
    assert 2 <= len(by_id["arc:1"]["geometry"]) < DEFAULT_CIRCLE_SEGMENTS
    # open 3-vertex polyline -> two segments.
    assert by_id["poly:1"]["geometry"] == [[0.0, 0.0, 1.0, 1.0], [1.0, 1.0, 2.0, 0.0]]
    # bbox comes straight from canonical extents.
    assert pack.bbox == (-5.0, -5.0, 10.0, 10.0)
    assert pack.adapter["producer"] == PRODUCER_ID
    assert pack.metadata["primitive_count"] == 4


def test_build_native_scene_pack_counts_unsupported_types_without_dropping_silently() -> None:
    # POINT and 3DFACE are still unsupported by the producer; they must be
    # counted (no silent drop), not rendered as a wrong shape.
    doc = _canonical_doc(
        entities=[
            _entity("line:1", "line", {"type": "line", "start": {"x": 0.0, "y": 0.0}, "end": {"x": 1.0, "y": 0.0}}),
            _entity("pt:1", "point", {"type": "point", "location": {"x": 0.0, "y": 0.0}}),
            _entity("face:1", "3dface", {"type": "3dface", "points": []}),
        ],
        extents={"min_x": 0.0, "min_y": 0.0, "max_x": 1.0, "max_y": 0.0},
    )

    pack = build_native_scene_pack(doc)

    assert [p["id"] for p in pack.display_primitives] == ["line:1"]
    assert pack.metadata["unsupported_entity_type_counts"] == {"point": 1, "3dface": 1}
    assert any(w["code"] == "native_scene_pack_unsupported_entity_type" for w in pack.warnings)


def test_build_native_scene_pack_falls_back_to_primitive_bbox_without_extents() -> None:
    doc = _canonical_doc(
        entities=[
            _entity("line:1", "line", {"type": "line", "start": {"x": 2.0, "y": 3.0}, "end": {"x": 8.0, "y": 9.0}}),
        ],
        extents=None,
    )

    pack = build_native_scene_pack(doc)

    assert pack.bbox == (2.0, 3.0, 8.0, 9.0)


def test_native_scene_pack_feeds_real_viewer_evidence_packet() -> None:
    doc = _canonical_doc(
        entities=[
            _entity("line:1", "line", {"type": "line", "start": {"x": 0.0, "y": 0.0}, "end": {"x": 10.0, "y": 10.0}}),
            _entity("circle:1", "circle", {"type": "circle", "center": {"x": 5.0, "y": 5.0}, "radius": 5.0}),
        ],
        extents={"min_x": 0.0, "min_y": 0.0, "max_x": 10.0, "max_y": 10.0},
    )
    pack = build_native_scene_pack(doc)

    overlay = {
        "zone_id": "content",
        "change_type": "added",
        "priority_rank": 1,
        "old_bbox": None,
        "bbox": {"min_x": 0.0, "min_y": 0.0, "max_x": 10.0, "max_y": 10.0},
    }
    evidence = native_scene_viewer_evidence_payload(
        pack, change_overlays=[overlay], import_report=doc["import_report"]
    )

    assert evidence["schema_version"] == "native-cad-viewer-evidence/v1"
    assert evidence["source_kind"] == "native_cad"
    assert evidence["viewer"]["primitive_count"] == 2
    assert evidence["viewer"]["within_primitive_budget"] is True
    assert evidence["viewer"]["within_payload_byte_budget"] is True
    assert evidence["primary_change_frame"]["status"] == "framed"
    assert evidence["import_report"]["status"] == "ok"
    assert evidence["native_scene_pack"]["adapter"]["producer"] == PRODUCER_ID


def test_native_scene_pack_ref_renders_through_viewport_seam(tmp_path: Path) -> None:
    # Fork A foundation: a native scene pack must drive the SAME viewport seam
    # (resolve_viewer_primitive_source) the ezdxf scene pack uses, classified as
    # the native producer, not a degraded fallback.
    doc = _canonical_doc(
        entities=[
            _entity("line:1", "line", {"type": "line", "start": {"x": 0.0, "y": 0.0}, "end": {"x": 10.0, "y": 5.0}}),
            _entity("circle:1", "circle", {"type": "circle", "center": {"x": 5.0, "y": 5.0}, "radius": 3.0}),
        ],
        extents={"min_x": 0.0, "min_y": 0.0, "max_x": 10.0, "max_y": 10.0},
    )

    ref = build_native_scene_pack_ref(doc, tmp_path)
    assert Path(ref.overview_lod0_path).exists()
    assert ref.primitive_count == 2

    source = resolve_viewer_primitive_source(ref)
    assert source.ok is True
    assert source.degraded is False
    assert source.render_mode == "skeleton_preview"
    assert source.provenance["producer_id"] == "native_scene_pack"
    assert source.status_text == "NativeScenePack preview"
    assert len(source.primitives) == 2
    assert source.world_bbox == (0.0, 0.0, 10.0, 10.0)


def test_build_native_scene_pack_renders_ellipse_tessellation() -> None:
    # R1 (Fork A renderer fidelity): the AC1032 reader decodes ELLIPSE
    # (center/major_axis/ratio/params); the producer must tessellate it into a
    # ``lines`` primitive rather than counting it unsupported.
    doc = _canonical_doc(
        entities=[
            _entity(
                "ell:1",
                "ellipse",
                {
                    "type": "ellipse",
                    "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "major_axis": {"x": 10.0, "y": 0.0, "z": 0.0},
                    "ratio": 0.5,
                    "start_param": 0.0,
                    "end_param": 2.0 * 3.141592653589793,
                },
            ),
        ],
        extents={"min_x": -10.0, "min_y": -5.0, "max_x": 10.0, "max_y": 5.0},
    )

    pack = build_native_scene_pack(doc)

    assert [p["id"] for p in pack.display_primitives] == ["ell:1"]
    prim = pack.display_primitives[0]
    assert prim["type"] == "lines"
    assert prim["source_entity_type"] == "ellipse"
    assert len(prim["geometry"]) >= 8
    # every sampled endpoint lies on the ellipse x^2/100 + y^2/25 == 1.
    for seg in prim["geometry"]:
        for px, py in ((seg[0], seg[1]), (seg[2], seg[3])):
            assert abs((px * px) / 100.0 + (py * py) / 25.0 - 1.0) < 1e-6, (px, py)
    assert "text" not in pack.metadata["unsupported_entity_type_counts"]


def test_build_native_scene_pack_renders_hatch_boundary_box() -> None:
    # R1: HATCH boundary loops are not carried in canonical (only the decoded
    # world bbox), so the producer renders the boundary extent rectangle and
    # marks it a partial render (honest, no silent drop, [[silent_fallback_pattern]]).
    doc = _canonical_doc(
        entities=[
            {
                "id": "h:1",
                "type": "hatch",
                "layer_id": "layer:0",
                "bbox": {"min_x": 1.0, "min_y": 2.0, "max_x": 5.0, "max_y": 8.0},
                "geometry": {"type": "hatch", "pattern": "ANSI31", "solid": False, "num_paths": 1},
            },
        ],
        extents={"min_x": 0.0, "min_y": 0.0, "max_x": 6.0, "max_y": 9.0},
    )

    pack = build_native_scene_pack(doc)

    assert [p["id"] for p in pack.display_primitives] == ["h:1"]
    prim = pack.display_primitives[0]
    assert prim["type"] == "lines"
    assert prim["source_entity_type"] == "hatch"
    assert prim.get("properties", {}).get("partial") is True
    # closed rectangle of the boundary extent.
    assert prim["geometry"] == [
        [1.0, 2.0, 5.0, 2.0],
        [5.0, 2.0, 5.0, 8.0],
        [5.0, 8.0, 1.0, 8.0],
        [1.0, 8.0, 1.0, 2.0],
    ]
    # honest accounting: a partial render is tracked, not a silent full-fidelity claim.
    assert pack.metadata.get("partial_render_entity_type_counts", {}).get("hatch") == 1


def test_native_render_color_and_linetype_carried_into_primitives() -> None:
    # R2 (Fork A): the AC1032 reader resolves ACI color + linetype into
    # canonical ``style``; the producer must carry them into each primitive's
    # ``properties`` so the QML viewport renders per-entity color (it already
    # reads ``prim.properties.color``) instead of a single monochrome ink.
    doc = _canonical_doc(
        entities=[
            {
                "id": "line:red", "type": "line", "layer_id": "layer:0",
                "style": {"color": 1, "linetype": "DASHED"},
                "geometry": {"type": "line", "start": {"x": 0.0, "y": 0.0}, "end": {"x": 1.0, "y": 0.0}},
            },
            {
                "id": "line:blue", "type": "line", "layer_id": "layer:0",
                "style": {"color": 5, "linetype": "CONTINUOUS"},
                "geometry": {"type": "line", "start": {"x": 0.0, "y": 1.0}, "end": {"x": 1.0, "y": 1.0}},
            },
        ],
        extents={"min_x": 0.0, "min_y": 0.0, "max_x": 1.0, "max_y": 1.0},
    )

    pack = build_native_scene_pack(doc)
    by_id = {p["id"]: p for p in pack.display_primitives}

    assert by_id["line:red"]["properties"]["color"] == "#FF0000"
    assert by_id["line:blue"]["properties"]["color"] == "#0000FF"
    assert by_id["line:red"]["properties"]["linetype"] == "DASHED"
    # multi-colour drawing renders >1 distinct colour (not monochrome inkColor).
    distinct = {p.get("properties", {}).get("color") for p in pack.display_primitives}
    distinct.discard(None)
    assert len(distinct) == 2


def test_native_render_color_bylayer_falls_back_to_default_ink() -> None:
    # ACI 256 (BYLAYER) / 0 (BYBLOCK) / 7 (black-white) resolve to the viewer's
    # theme ink (None color property) — honest, not a wrong fixed colour.
    doc = _canonical_doc(
        entities=[
            {
                "id": "line:bylayer", "type": "line", "layer_id": "layer:0",
                "style": {"color": 256, "linetype": "BYLAYER"},
                "geometry": {"type": "line", "start": {"x": 0.0, "y": 0.0}, "end": {"x": 1.0, "y": 0.0}},
            },
        ],
        extents={"min_x": 0.0, "min_y": 0.0, "max_x": 1.0, "max_y": 0.0},
    )

    pack = build_native_scene_pack(doc)
    assert "color" not in pack.display_primitives[0].get("properties", {})


def test_build_native_scene_pack_emits_text_dimension_insert_primitives() -> None:
    # R1b (completes DoD-R1): every visible decoded type yields a viewport
    # primitive. TEXT/MTEXT/DIMENSION -> ``text`` primitives; INSERT -> a marker
    # box (block expansion is a documented follow-up). Nothing silently dropped.
    doc = _canonical_doc(
        entities=[
            _entity("text:1", "text", {"type": "text", "insert": {"x": 1.0, "y": 2.0, "z": 0.0}, "height": 2.5, "rotation_deg": 0.0, "text": "H-400"}),
            _entity("mtext:1", "mtext", {"type": "mtext", "insert": {"x": 3.0, "y": 4.0, "z": 0.0}, "height": 3.0, "text": "철근 D25"}),
            _entity("dim:1", "dimension", {"type": "dimension", "text_midpoint": {"x": 5.0, "y": 6.0, "z": 0.0}, "measurement": 1234.5, "dimtype": 0, "text": ""}),
            {
                "id": "ins:1", "type": "insert", "layer_id": "layer:0",
                "bbox": {"min_x": 0.0, "min_y": 0.0, "max_x": 2.0, "max_y": 2.0},
                "geometry": {"type": "insert", "insert": {"x": 1.0, "y": 1.0}, "scale": {"x": 1.0, "y": 1.0}, "rotation_deg": 0.0, "block_name": "COL"},
            },
        ],
        extents={"min_x": 0.0, "min_y": 0.0, "max_x": 6.0, "max_y": 6.0},
    )

    pack = build_native_scene_pack(doc)
    by_id = {p["id"]: p for p in pack.display_primitives}

    # no visible type silently dropped.
    assert pack.metadata["unsupported_entity_type_counts"] == {}
    # TEXT/MTEXT/DIMENSION -> text primitives carrying value + position.
    assert by_id["text:1"]["type"] == "text"
    assert by_id["text:1"]["text"] == "H-400"
    assert (by_id["text:1"]["x"], by_id["text:1"]["y"]) == (1.0, 2.0)
    assert by_id["mtext:1"]["type"] == "text" and by_id["mtext:1"]["text"] == "철근 D25"
    # DIMENSION renders its measurement label (formatted from measurement) -> partial.
    assert by_id["dim:1"]["type"] == "text"
    assert by_id["dim:1"]["text"] == "1234" or by_id["dim:1"]["text"].startswith("1234")
    assert by_id["dim:1"]["properties"]["partial"] is True
    # INSERT -> marker box (lines), flagged partial, block expansion deferred.
    assert by_id["ins:1"]["type"] == "lines"
    assert by_id["ins:1"]["properties"]["partial"] is True
    assert by_id["ins:1"]["properties"]["render"] == "insert_marker"
    # honest accounting: dimension + insert tracked as partial renders.
    assert pack.metadata["partial_render_entity_type_counts"].get("insert") == 1
    assert pack.metadata["partial_render_entity_type_counts"].get("dimension") == 1


@pytest.mark.skipif(
    not REAL_AC1015_SAMPLE.exists(),
    reason="local AC1015 real sample (.local corpus) not present",
)
def test_real_native_import_produces_real_viewer_evidence() -> None:
    doc = DwgImporter(adapter=DwgNativeAc1015Adapter()).import_file(REAL_AC1015_SAMPLE)
    assert doc["import_report"]["status"] == "ok"

    pack = build_native_scene_pack(doc)
    assert pack.display_primitives, "real native import must yield viewer primitives"

    ext = doc["extents"]
    overlay = {
        "zone_id": "content",
        "change_type": "added",
        "priority_rank": 1,
        "old_bbox": None,
        "bbox": {"min_x": ext["min_x"], "min_y": ext["min_y"], "max_x": ext["max_x"], "max_y": ext["max_y"]},
    }
    evidence = native_scene_viewer_evidence_payload(
        pack, change_overlays=[overlay], import_report=doc["import_report"]
    )

    assert evidence["schema_version"] == "native-cad-viewer-evidence/v1"
    assert evidence["viewer"]["within_primitive_budget"] is True
    assert evidence["viewer"]["within_payload_byte_budget"] is True
    assert evidence["primary_change_frame"]["status"] == "framed"
    assert evidence["import_report"]["status"] == "ok"
