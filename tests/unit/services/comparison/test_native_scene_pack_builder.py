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
    doc = _canonical_doc(
        entities=[
            _entity("line:1", "line", {"type": "line", "start": {"x": 0.0, "y": 0.0}, "end": {"x": 1.0, "y": 0.0}}),
            _entity("text:1", "text", {"type": "text", "insert": {"x": 0.0, "y": 0.0}, "text": "H-400"}),
            _entity("ins:1", "insert", {"type": "insert", "block_name": "B1", "insert": {"x": 0.0, "y": 0.0}}),
        ],
        extents={"min_x": 0.0, "min_y": 0.0, "max_x": 1.0, "max_y": 0.0},
    )

    pack = build_native_scene_pack(doc)

    assert [p["id"] for p in pack.display_primitives] == ["line:1"]
    assert pack.metadata["unsupported_entity_type_counts"] == {"text": 1, "insert": 1}
    assert pack.warnings and pack.warnings[0]["code"] == "native_scene_pack_unsupported_entity_type"


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
