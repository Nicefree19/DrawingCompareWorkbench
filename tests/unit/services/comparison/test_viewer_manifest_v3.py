# -*- coding: utf-8 -*-
"""Unit tests for viewer_manifest v3 schema (Phase G1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.viewer_manifest_v3 import (
    IDENTITY_AFFINE,
    SCHEMA_VERSION,
    EvidenceRef,
    ManifestV3ValidationError,
    ScenePackRef,
    SourceSignature,
    ViewerManifestV3,
    ZoneRequestRef,
    is_v3_manifest,
    load_manifest_v3,
    write_manifest_v3,
)


def _make_minimal() -> ViewerManifestV3:
    return ViewerManifestV3(
        pair_uuid="uuid-G1",
        package_version="phaseG1",
        source_kind="normalized_dxf",
    )


def test_round_trip_minimal_manifest(tmp_path: Path) -> None:
    m = _make_minimal()
    path = tmp_path / "viewer_manifest_v3.json"
    write_manifest_v3(path, m)
    loaded = load_manifest_v3(path)
    assert loaded.schema_version == SCHEMA_VERSION
    assert loaded.pair_uuid == "uuid-G1"
    assert loaded.source_kind == "normalized_dxf"
    assert loaded.current_render_mode == "relative_only"
    assert loaded.before_scene_pack is None
    assert loaded.after_scene_pack is None
    assert loaded.zone_requests == []
    assert loaded.evidence == []


def test_rejects_unknown_source_kind() -> None:
    with pytest.raises(ManifestV3ValidationError, match="source_kind"):
        ViewerManifestV3(
            pair_uuid="x", package_version="x", source_kind="floppy_disk",  # type: ignore[arg-type]
        )


def test_rejects_unknown_overlay_space() -> None:
    with pytest.raises(ManifestV3ValidationError, match="overlay_space"):
        ViewerManifestV3(
            pair_uuid="x", package_version="x",
            source_kind="normalized_dxf", overlay_space="floating",  # type: ignore[arg-type]
        )


def test_rejects_unknown_render_mode() -> None:
    with pytest.raises(ManifestV3ValidationError, match="current_render_mode"):
        ViewerManifestV3(
            pair_uuid="x", package_version="x", source_kind="normalized_dxf",
            current_render_mode="quantum_state",  # type: ignore[arg-type]
        )


def test_load_rejects_v2_manifest(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps({"schema_version": "viewer_manifest.v2", "pair_uuid": "u"}),
        encoding="utf-8",
    )
    with pytest.raises(ManifestV3ValidationError, match="schema_version"):
        load_manifest_v3(p)


def test_load_rejects_corrupt_json(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text("{ broken", encoding="utf-8")
    with pytest.raises(ManifestV3ValidationError, match="Cannot read"):
        load_manifest_v3(p)


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ManifestV3ValidationError, match="not found"):
        load_manifest_v3(tmp_path / "absent.json")


def test_atomic_write_does_not_leak_tmp(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    write_manifest_v3(p, _make_minimal())
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_is_v3_manifest_true_for_real(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    write_manifest_v3(p, _make_minimal())
    assert is_v3_manifest(p)


def test_is_v3_manifest_false_for_v2(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps({"schema_version": "viewer_manifest.v2"}),
        encoding="utf-8",
    )
    assert not is_v3_manifest(p)


def test_scene_pack_ref_round_trip(tmp_path: Path) -> None:
    pack = ScenePackRef(
        json_path="packs/scene_pack.json",
        index_path="packs/primitive_index.rtree",
        overview_lod0_path="packs/overview_lod0.json",
        primitive_count=12345,
        drawing_world_bbox=(-100.0, -50.0, 200.0, 80.0),
        elapsed_build_ms=1234.5,
    )
    m = ViewerManifestV3(
        pair_uuid="p", package_version="v3", source_kind="normalized_dxf",
        before_scene_pack=pack, after_scene_pack=pack,
    )
    path = tmp_path / "manifest.json"
    write_manifest_v3(path, m)
    loaded = load_manifest_v3(path)
    assert loaded.before_scene_pack is not None
    assert loaded.before_scene_pack.primitive_count == 12345
    assert loaded.before_scene_pack.drawing_world_bbox == (-100.0, -50.0, 200.0, 80.0)


def test_zone_request_ref_round_trip(tmp_path: Path) -> None:
    req = ZoneRequestRef(
        zone_id="Z-007",
        side="after",
        bbox_world=(10.0, 20.0, 30.0, 40.0),
        bbox_coordinate_space="image_pixels",
        pad_world=2.5,
        target_px_w=1280,
        target_px_h=720,
        cache_key="abcdef",
    )
    m = ViewerManifestV3(
        pair_uuid="p", package_version="v3", source_kind="normalized_dxf",
        zone_requests=[req],
    )
    path = tmp_path / "manifest.json"
    write_manifest_v3(path, m)
    loaded = load_manifest_v3(path)
    assert len(loaded.zone_requests) == 1
    assert loaded.zone_requests[0].zone_id == "Z-007"
    assert loaded.zone_requests[0].bbox_world == (10.0, 20.0, 30.0, 40.0)
    assert loaded.zone_requests[0].coordinate_contract_version == "coordinate_contract.v1"
    assert loaded.zone_requests[0].bbox_coordinate_space == "image_pixels_tl"
    assert loaded.zone_requests[0].source_truth == "pdf_visual"
    assert loaded.zone_requests[0].y_axis == "down"
    assert loaded.zone_requests[0].cache_key == "abcdef"


def test_evidence_ref_round_trip(tmp_path: Path) -> None:
    ev = EvidenceRef(
        zone_id="Z-007",
        side="after",
        raster_uri="cache/zone_rasters/abc.png",
        world_bbox=(10.0, 20.0, 30.0, 40.0),
        pixel_size=(800, 600),
        world_to_pixel=(40.0, 0.0, -400.0, 0.0, -30.0, 1200.0),
        pixel_to_world=(0.025, 0.0, 10.0, 0.0, -0.0333, 40.0),
        transform_quality="estimated",
        bbox_coordinate_space="pdf_page_points_bl",
        render_ms=850.0,
        cache_hit=True,
        visual_fidelity="relative_overlay",
        render_lifecycle="fallback_visible",
        fallback_reason_code="source_render_failed",
        warnings=["zone_render_fallback:source_render_failed:RuntimeError"],
    )
    m = ViewerManifestV3(
        pair_uuid="p", package_version="v3", source_kind="normalized_dxf",
        evidence=[ev],
    )
    path = tmp_path / "manifest.json"
    write_manifest_v3(path, m)
    loaded = load_manifest_v3(path)
    assert len(loaded.evidence) == 1
    assert loaded.evidence[0].cache_hit is True
    assert loaded.evidence[0].pixel_size == (800, 600)
    assert loaded.evidence[0].transform_quality == "estimated"
    assert loaded.evidence[0].bbox_coordinate_space == "pdf_page_points_bl"
    assert loaded.evidence[0].source_truth == "pdf_visual"
    assert loaded.evidence[0].y_axis == "up"
    assert loaded.evidence[0].visual_fidelity == "relative_overlay"
    assert loaded.evidence[0].render_lifecycle == "fallback_visible"
    assert loaded.evidence[0].fallback_reason_code == "source_render_failed"
    assert loaded.evidence[0].warnings == ["zone_render_fallback:source_render_failed:RuntimeError"]


def test_v3_old_zone_and_evidence_payloads_default_coordinate_contract() -> None:
    req = ZoneRequestRef.from_dict({
        "zone_id": "legacy-zone",
        "bbox_world": [1, 2, 3, 4],
    })
    ev = EvidenceRef.from_dict({
        "zone_id": "legacy-evidence",
        "world_bbox": [1, 2, 3, 4],
        "pixel_size": [10, 20],
    })

    assert req.bbox_coordinate_space == "cad_wcs_mm"
    assert req.source_truth == "cad_entity"
    assert req.y_axis == "up"
    assert ev.bbox_coordinate_space == "cad_wcs_mm"
    assert ev.source_truth == "cad_entity"
    assert ev.y_axis == "up"


def test_source_signature_round_trip() -> None:
    sig = SourceSignature(
        source_path="C:/x/y.dwg",
        file_hash="aabbcc",
        source_hash="source-hash",
        file_size=123,
        mtime_ns=456,
        signature_schema_version="1",
        dxf_version="AC1027",
        font_sig="ddee",
        backend_sig="ezdxf-1.4.3",
    )
    d = sig.to_dict()
    rebuilt = SourceSignature.from_dict(d)
    assert rebuilt == sig


def test_render_mode_field_serialises() -> None:
    m = ViewerManifestV3(
        pair_uuid="p", package_version="v3", source_kind="normalized_dxf",
        current_render_mode="vector_focus",
    )
    d = m.to_dict()
    assert d["current_render_mode"] == "vector_focus"
    rebuilt = ViewerManifestV3.from_dict(d)
    assert rebuilt.current_render_mode == "vector_focus"


def test_auto_populates_created_at_when_blank() -> None:
    m = ViewerManifestV3(
        pair_uuid="p", package_version="v3", source_kind="normalized_dxf",
    )
    assert m.created_at_utc  # not empty


def test_renderer_capabilities_dict_preserved(tmp_path: Path) -> None:
    m = ViewerManifestV3(
        pair_uuid="p", package_version="v3", source_kind="normalized_dxf",
        renderer_capabilities={
            "viewer_engine": "lightweight",
            "scene_pack_built": False,
            "tile_size": 512,
        },
    )
    path = tmp_path / "manifest.json"
    write_manifest_v3(path, m)
    loaded = load_manifest_v3(path)
    assert loaded.renderer_capabilities["viewer_engine"] == "lightweight"
    assert loaded.renderer_capabilities["scene_pack_built"] is False
    assert loaded.renderer_capabilities["tile_size"] == 512


# ---------------------------------------------------------------------------
# ADR-003 H3 — display_overlay_space field
# ---------------------------------------------------------------------------


def test_display_overlay_space_defaults_empty(tmp_path: Path) -> None:
    """H3: default is empty string (means 'use detection space', legacy)."""
    m = ViewerManifestV3(
        pair_uuid="p", package_version="v3", source_kind="normalized_dxf"
    )
    assert m.display_overlay_space == ""
    path = tmp_path / "manifest.json"
    write_manifest_v3(path, m)
    loaded = load_manifest_v3(path)
    assert loaded.display_overlay_space == ""


def test_display_overlay_space_round_trip(tmp_path: Path) -> None:
    """H3: a hybrid manifest carries image_pixels_tl display space through
    write/load (DWG detected in cad_wcs_mm, displayed on a PDF page)."""
    m = ViewerManifestV3(
        pair_uuid="p",
        package_version="v3",
        source_kind="mixed",
        display_overlay_space="image_pixels_tl",
    )
    assert m.display_overlay_space == "image_pixels_tl"
    path = tmp_path / "manifest.json"
    write_manifest_v3(path, m)
    loaded = load_manifest_v3(path)
    assert loaded.display_overlay_space == "image_pixels_tl"


def test_display_overlay_space_normalised_on_construction() -> None:
    """H3: legacy coordinate aliases are normalised (pdf_points ->
    pdf_page_points_bl) so the manifest stores the canonical token."""
    m = ViewerManifestV3(
        pair_uuid="p",
        package_version="v3",
        source_kind="mixed",
        display_overlay_space="pdf_points",
    )
    assert m.display_overlay_space == "pdf_page_points_bl"


def test_display_overlay_space_backward_compat_missing_field() -> None:
    """H3: a v3 dict WITHOUT display_overlay_space (older package) loads as
    empty string — no crash, legacy behaviour preserved."""
    m = ViewerManifestV3(
        pair_uuid="p", package_version="v3", source_kind="normalized_dxf"
    )
    d = m.to_dict()
    del d["display_overlay_space"]
    loaded = ViewerManifestV3.from_dict(d)
    assert loaded.display_overlay_space == ""
