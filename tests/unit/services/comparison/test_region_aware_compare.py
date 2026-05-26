from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.comparison.folder_compare_pipeline import _export_region_aware_artifacts
from src.services.comparison.detail_region_matcher import match_sheet_regions
from src.services.comparison.dxf_entity_extractor import NormalizedEntity
from src.services.comparison.localized_compare import compare_localized_region_entities, localize_change_zones
from src.services.comparison.sheet_region_detector import SheetRegion, detect_sheet_regions


ezdxf = pytest.importorskip("ezdxf")


def _write_detail_dxf(path: Path, *, offset_x: float = 0.0, label_prefix: str = "S") -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for index, origin_x in enumerate((0.0, 6000.0), start=1):
        x = offset_x + origin_x
        y = 0.0
        msp.add_lwpolyline(
            [(x, y), (x + 3000, y), (x + 3000, y + 1800), (x, y + 1800)],
            close=True,
            dxfattribs={"layer": "FRAME"},
        )
        msp.add_line((x + 300, y + 350), (x + 2700, y + 350), dxfattribs={"layer": "BEAM"})
        msp.add_line((x + 300, y + 700), (x + 2700, y + 700), dxfattribs={"layer": "BEAM"})
        text = msp.add_text(f"{label_prefix}-{index:03d}", dxfattribs={"height": 120, "layer": "TITLE"})
        text.set_placement((x + 120, y + 1500))
    doc.saveas(str(path))
    return path


def _add_line_frame(msp, x: float, y: float, width: float, height: float, *, layer: str = "FRAME") -> None:
    msp.add_line((x, y), (x + width, y), dxfattribs={"layer": layer})
    msp.add_line((x + width, y), (x + width, y + height), dxfattribs={"layer": layer})
    msp.add_line((x + width, y + height), (x, y + height), dxfattribs={"layer": layer})
    msp.add_line((x, y + height), (x, y), dxfattribs={"layer": layer})


def _write_line_frame_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for index, origin_x in enumerate((0.0, 5000.0), start=1):
        _add_line_frame(msp, origin_x, 0.0, 2600.0, 1600.0)
        msp.add_line((origin_x + 250, 300), (origin_x + 2300, 300), dxfattribs={"layer": "BEAM"})
        text = msp.add_text(f"L-{index:03d}", dxfattribs={"height": 100, "layer": "TITLE"})
        text.set_placement((origin_x + 120, 1300))
    doc.saveas(str(path))
    return path


def _write_insert_frame_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    block = doc.blocks.new(name="DETAIL_FRAME_BLOCK")
    _add_line_frame(block, 0.0, 0.0, 2400.0, 1400.0)
    block.add_line((250, 350), (2150, 350), dxfattribs={"layer": "BEAM"})
    msp = doc.modelspace()
    for index, origin_x in enumerate((0.0, 4200.0), start=1):
        msp.add_blockref("DETAIL_FRAME_BLOCK", (origin_x, 0.0), dxfattribs={"layer": "FRAME"})
        text = msp.add_text(f"B-{index:03d}", dxfattribs={"height": 100, "layer": "TITLE"})
        text.set_placement((origin_x + 120, 1120))
    doc.saveas(str(path))
    return path


def _write_paperspace_viewport_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "BEAM"})
    layout = doc.layouts.new("DETAILS")
    layout.add_viewport(
        center=(100.0, 80.0),
        size=(120.0, 60.0),
        view_center_point=(500.0, 0.0),
        view_height=1000.0,
    )
    doc.saveas(str(path))
    return path


def _write_frame_with_block_info_table(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (4000, 0), (4000, 2200), (0, 2200)],
        close=True,
        dxfattribs={"layer": "FRAME"},
    )
    for y in (400, 800, 1200, 1600):
        msp.add_line((350, y), (3600, y), dxfattribs={"layer": "BEAM"})
    text = msp.add_text("D-101", dxfattribs={"height": 120, "layer": "TITLE"})
    text.set_placement((120, 1900))

    table_x = 4600.0
    table_y = 0.0
    msp.add_lwpolyline(
        [(table_x, table_y), (table_x + 1200, table_y), (table_x + 1200, table_y + 800), (table_x, table_y + 800)],
        close=True,
        dxfattribs={"layer": "00-block info"},
    )
    for offset in (200, 400, 600):
        msp.add_line((table_x, table_y + offset), (table_x + 1200, table_y + offset), dxfattribs={"layer": "00-block info"})
    for offset in (300, 600, 900):
        msp.add_line((table_x + offset, table_y), (table_x + offset, table_y + 800), dxfattribs={"layer": "00-block info"})
    for row, label in enumerate(("DWG HD10", "SCALE 1:20", "DATE", "CHECK")):
        t = msp.add_text(label, dxfattribs={"height": 60, "layer": "00-block info"})
        t.set_placement((table_x + 40, table_y + 80 + row * 180))
    doc.saveas(str(path))
    return path


def _entity_hash(entity_type: str, data: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            {"entity_type": entity_type, "data": data},
            sort_keys=True,
            default=list,
        ).encode("utf-8")
    ).hexdigest()


def _normalized_entity(
    entity_type: str,
    layer: str,
    data: dict,
    location: tuple[float, float],
) -> NormalizedEntity:
    return NormalizedEntity(
        hash=_entity_hash(entity_type, data),
        entity_type=entity_type,
        layer=layer,
        data=data,
        location=location,
    )


def _region_entity_map(x: float, y: float, *, radius: float = 10.0) -> dict[str, list[NormalizedEntity]]:
    line_data = {"start": (x + 300.0, y + 350.0), "end": (x + 2700.0, y + 350.0)}
    circle_data = {"center": (x + 900.0, y + 900.0), "radius": radius}
    return {
        "LINE": [_normalized_entity("LINE", "BEAM", line_data, (x + 1500.0, y + 350.0))],
        "CIRCLE": [_normalized_entity("CIRCLE", "BEAM", circle_data, (x + 900.0, y + 900.0))],
    }


def _sheet_region(region_id: str, x: float, y: float) -> SheetRegion:
    return SheetRegion(
        region_id=region_id,
        source_path=f"{region_id}.dxf",
        source_format="dxf",
        bbox=(x, y, x + 3000.0, y + 1800.0),
        width=3000.0,
        height=1800.0,
        area=5_400_000.0,
        entity_count=2,
        detection_method="cad_frame",
    )


def test_detect_sheet_regions_finds_multiple_cad_detail_frames(tmp_path: Path) -> None:
    source = _write_detail_dxf(tmp_path / "multi_detail.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 2
    assert {region.drawing_number for region in result.regions} == {"S-001", "S-002"}
    assert all(region.detection_method == "cad_frame" for region in result.regions)


def test_detect_sheet_regions_recovers_line_rectangular_frames(tmp_path: Path) -> None:
    source = _write_line_frame_dxf(tmp_path / "line_frames.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 2
    assert {region.drawing_number for region in result.regions} == {"L-001", "L-002"}
    assert all(region.detection_method == "cad_line_frame" for region in result.regions)


def test_detect_sheet_regions_expands_insert_block_frames(tmp_path: Path) -> None:
    source = _write_insert_frame_dxf(tmp_path / "insert_frames.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 2
    assert {region.drawing_number for region in result.regions} == {"B-001", "B-002"}
    assert all(region.detection_method == "cad_line_frame" for region in result.regions)


def test_detect_sheet_regions_uses_paperspace_viewports_when_modelspace_has_no_frames(tmp_path: Path) -> None:
    source = _write_paperspace_viewport_dxf(tmp_path / "layout_viewport.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.detection_method == "viewport_frame"
    assert region.region_kind == "layout_viewport"
    assert region.layout_name == "DETAILS"


def test_detect_sheet_regions_filters_block_info_table_candidates(tmp_path: Path) -> None:
    source = _write_frame_with_block_info_table(tmp_path / "table_filtered.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 1
    assert result.regions[0].drawing_number == "D-101"
    assert "HD10" not in {region.drawing_number for region in result.regions}


def test_match_sheet_regions_uses_title_identity_despite_coordinate_shift(tmp_path: Path) -> None:
    before = detect_sheet_regions(_write_detail_dxf(tmp_path / "before.dxf"), side="before")
    after = detect_sheet_regions(
        _write_detail_dxf(tmp_path / "after.dxf", offset_x=100000.0),
        side="after",
    )

    summary = match_sheet_regions(before.regions, after.regions, pair_id="pair-a")

    assert summary.auto_matched_count == 2
    matched_pairs = {
        (match.before_region_id, match.after_region_id)
        for match in summary.matches
        if match.status == "auto_matched"
    }
    assert ("before-frame-1", "after-frame-1") in matched_pairs
    assert ("before-frame-2", "after-frame-2") in matched_pairs


def test_match_sheet_regions_can_review_match_without_drawing_number() -> None:
    before = SheetRegion(
        region_id="before-r1",
        source_path="before.dxf",
        source_format="dxf",
        bbox=(0.0, 0.0, 1000.0, 500.0),
        width=1000.0,
        height=500.0,
        area=500000.0,
        entity_count=20,
        entity_histogram={"LINE": 18, "TEXT": 2},
        layer_histogram={"BEAM": 18, "TITLE": 2},
        detection_method="cad_frame",
    )
    after = SheetRegion(
        region_id="after-r1",
        source_path="after.dxf",
        source_format="dxf",
        bbox=(100000.0, 50000.0, 101000.0, 50500.0),
        width=1000.0,
        height=500.0,
        area=500000.0,
        entity_count=20,
        entity_histogram={"LINE": 18, "TEXT": 2},
        layer_histogram={"BEAM": 18, "TITLE": 2},
        detection_method="cad_line_frame",
    )

    summary = match_sheet_regions([before], [after], pair_id="pair-no-number")

    assert summary.review_required_count == 1
    match = next(match for match in summary.matches if match.status == "review_required")
    assert match.before_region_id == "before-r1"
    assert match.after_region_id == "after-r1"
    assert match.component_scores["geometry"] >= 0.99
    assert match.component_scores["histogram"] >= 0.99


def test_localize_change_zones_distinguishes_added_and_deleted_sides(tmp_path: Path) -> None:
    before = detect_sheet_regions(_write_detail_dxf(tmp_path / "before.dxf"), side="before")
    after = detect_sheet_regions(_write_detail_dxf(tmp_path / "after.dxf"), side="after")
    match_summary = match_sheet_regions(before.regions, after.regions, pair_id="pair-a")
    zones = [
        {
            "zone_id": "C-1",
            "pair_id": "pair-a",
            "change_type": "added",
            "bbox": [6200.0, 100.0, 6600.0, 500.0],
            "old_bbox": None,
        },
        {
            "zone_id": "C-2",
            "pair_id": "pair-a",
            "change_type": "deleted",
            "bbox": [100.0, 100.0, 500.0, 500.0],
            "old_bbox": [100.0, 100.0, 500.0, 500.0],
        },
        {
            "zone_id": "C-3",
            "pair_id": "pair-a",
            "change_type": "modified",
            "bbox": [200.0, 200.0, 500.0, 500.0],
            "old_bbox": [200.0, 200.0, 500.0, 500.0],
        },
    ]

    localized = localize_change_zones(
        zones,
        before_regions=before.regions,
        after_regions=after.regions,
        match_summary=match_summary,
        pair_id="pair-a",
    )

    by_zone = {zone.zone_id: zone for zone in localized.localized_zones}
    assert by_zone["C-1"].localized_status == "added_in_after_region"
    assert by_zone["C-1"].before_region_id == ""
    assert by_zone["C-1"].after_region_id == "after-frame-2"
    assert by_zone["C-2"].localized_status == "deleted_from_before_region"
    assert by_zone["C-2"].before_region_id == "before-frame-1"
    assert by_zone["C-3"].localized_status == "matched_region_change"


def test_localize_change_zones_does_not_assign_far_outside_bbox_to_nearest_region(tmp_path: Path) -> None:
    before = detect_sheet_regions(_write_detail_dxf(tmp_path / "before.dxf"), side="before")
    after = detect_sheet_regions(_write_detail_dxf(tmp_path / "after.dxf"), side="after")
    match_summary = match_sheet_regions(before.regions, after.regions, pair_id="pair-a")
    zones = [
        {
            "zone_id": "outside",
            "pair_id": "pair-a",
            "change_type": "added",
            "bbox": [100000.0, 100000.0, 100500.0, 100500.0],
            "old_bbox": None,
        }
    ]

    localized = localize_change_zones(
        zones,
        before_regions=before.regions,
        after_regions=after.regions,
        match_summary=match_summary,
        pair_id="pair-a",
    )

    zone = localized.localized_zones[0]
    assert zone.localized_status == "unassigned"
    assert zone.after_region_id == ""
    assert localized.gate_status == "review_required"
    assert localized.unassigned_zone_count == 1
    assert "outside detected detail regions" in localized.gate_reasons[0]


def test_compare_localized_region_entities_ignores_whole_detail_translation() -> None:
    before_region = _sheet_region("before-frame-1", 0.0, 0.0)
    after_region = _sheet_region("after-frame-1", 100000.0, 50000.0)

    result = compare_localized_region_entities(
        _region_entity_map(0.0, 0.0),
        _region_entity_map(100000.0, 50000.0),
        before_region=before_region,
        after_region=after_region,
        match_id="m-1",
    )

    assert result.total_changes == 0
    assert result.metadata["localized_compare"] is True
    assert result.metadata["before_region_id"] == "before-frame-1"
    assert result.metadata["after_region_id"] == "after-frame-1"


def test_compare_localized_region_entities_maps_change_back_to_after_world_bbox() -> None:
    before_region = _sheet_region("before-frame-1", 0.0, 0.0)
    after_region = _sheet_region("after-frame-1", 100000.0, 50000.0)

    result = compare_localized_region_entities(
        _region_entity_map(0.0, 0.0, radius=10.0),
        _region_entity_map(100000.0, 50000.0, radius=20.0),
        before_region=before_region,
        after_region=after_region,
        match_id="m-1",
    )

    assert result.modified_count == 1
    change = result.changes[0]
    assert change.metadata["bbox_coordinate_space"] == "world_from_region_local"
    assert change.metadata["bbox"] == pytest.approx([100880.0, 50880.0, 100920.0, 50920.0])
    assert change.metadata["before_region_id"] == "before-frame-1"
    assert change.metadata["after_region_id"] == "after-frame-1"


def test_folder_pipeline_region_sidecars_are_written(tmp_path: Path) -> None:
    before_path = _write_detail_dxf(tmp_path / "before.dxf")
    after_path = _write_detail_dxf(tmp_path / "after.dxf", offset_x=25000.0)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    change_zones_path = artifact_dir / "change_zones.json"
    change_zones_path.write_text(
        """
        {
          "zones": [
            {
              "zone_id": "C-1",
              "pair_id": "pair-a",
              "change_type": "added",
              "bbox": [25200, 100, 25600, 500],
              "old_bbox": null
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    manifest_path = artifact_dir / "artifact_manifest.json"
    manifest_path.write_text('{"output_paths": {}}', encoding="utf-8")
    candidate = SimpleNamespace(
        pair_uuid="pair-a",
        source_a=SimpleNamespace(path_obj=before_path, path=str(before_path)),
        source_b=SimpleNamespace(path_obj=after_path, path=str(after_path)),
    )
    compare_summary = SimpleNamespace(
        items=[SimpleNamespace(candidate=candidate, status="completed")]
    )
    artifact_package = SimpleNamespace(
        output_paths={
            "change_zones_json": str(change_zones_path),
            "artifact_manifest_json": str(manifest_path),
        },
        warnings=[],
    )

    paths = _export_region_aware_artifacts(
        compare_summary=compare_summary,
        artifact_package=artifact_package,
        artifact_dir=artifact_dir,
        dxf_cache_dir=tmp_path / "dxf_cache",
    )

    assert Path(paths["region_detection_summary_json"]).exists()
    assert Path(paths["region_match_summary_json"]).exists()
    assert Path(paths["region_aware_status_json"]).exists()
    assert Path(paths["multi_frame_validation_json"]).exists()
    localized_path = Path(paths["localized_compare_summary_json"])
    assert localized_path.exists()
    assert "added_in_after_region" in localized_path.read_text(encoding="utf-8")
    status_payload = Path(paths["region_aware_status_json"]).read_text(encoding="utf-8")
    assert "review_gate" in status_payload
    assert "automatic_localized_compare_enabled" in status_payload
    validation_payload = Path(paths["multi_frame_validation_json"]).read_text(encoding="utf-8")
    assert "outside detected detail regions" not in validation_payload
    assert "region_detection_summary_json" in manifest_path.read_text(encoding="utf-8")


def test_folder_pipeline_region_validation_flags_outside_clouds(tmp_path: Path) -> None:
    before_path = _write_detail_dxf(tmp_path / "before.dxf")
    after_path = _write_detail_dxf(tmp_path / "after.dxf", offset_x=25000.0)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    change_zones_path = artifact_dir / "change_zones.json"
    change_zones_path.write_text(
        """
        {
          "zones": [
            {
              "zone_id": "outside",
              "pair_id": "pair-a",
              "change_type": "added",
              "bbox": [100000, 100000, 100500, 100500],
              "old_bbox": null
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    manifest_path = artifact_dir / "artifact_manifest.json"
    manifest_path.write_text('{"output_paths": {}}', encoding="utf-8")
    candidate = SimpleNamespace(
        pair_uuid="pair-a",
        source_a=SimpleNamespace(path_obj=before_path, path=str(before_path)),
        source_b=SimpleNamespace(path_obj=after_path, path=str(after_path)),
    )
    compare_summary = SimpleNamespace(
        items=[SimpleNamespace(candidate=candidate, status="completed")]
    )
    artifact_package = SimpleNamespace(
        output_paths={
            "change_zones_json": str(change_zones_path),
            "artifact_manifest_json": str(manifest_path),
        },
        warnings=[],
    )

    paths = _export_region_aware_artifacts(
        compare_summary=compare_summary,
        artifact_package=artifact_package,
        artifact_dir=artifact_dir,
        dxf_cache_dir=tmp_path / "dxf_cache",
    )

    status = Path(paths["region_aware_status_json"]).read_text(encoding="utf-8")
    validation = Path(paths["multi_frame_validation_json"]).read_text(encoding="utf-8")
    assert '"localized_gate_status": "review_required"' in status
    assert '"unassigned_zone_count": 1' in validation
    assert "outside detected detail regions" in validation


def test_folder_pipeline_opt_in_localized_region_compare_writes_dxf_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DRAWING_COMPARE_AUTO_REGION_COMPARE", "1")
    before_path = _write_detail_dxf(tmp_path / "before.dxf")
    after_path = _write_detail_dxf(tmp_path / "after.dxf", offset_x=25000.0)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    change_zones_path = artifact_dir / "change_zones.json"
    change_zones_path.write_text('{"zones": []}', encoding="utf-8")
    manifest_path = artifact_dir / "artifact_manifest.json"
    manifest_path.write_text('{"output_paths": {}}', encoding="utf-8")
    candidate = SimpleNamespace(
        pair_uuid="pair-a",
        source_a=SimpleNamespace(path_obj=before_path, path=str(before_path)),
        source_b=SimpleNamespace(path_obj=after_path, path=str(after_path)),
    )
    compare_summary = SimpleNamespace(
        items=[SimpleNamespace(candidate=candidate, status="completed")]
    )
    artifact_package = SimpleNamespace(
        output_paths={
            "change_zones_json": str(change_zones_path),
            "artifact_manifest_json": str(manifest_path),
        },
        warnings=[],
    )

    paths = _export_region_aware_artifacts(
        compare_summary=compare_summary,
        artifact_package=artifact_package,
        artifact_dir=artifact_dir,
        dxf_cache_dir=tmp_path / "dxf_cache",
    )

    localized_results_path = Path(paths["localized_region_compare_results_json"])
    payload = json.loads(localized_results_path.read_text(encoding="utf-8"))
    assert payload["automatic_localized_compare_requested"] is True
    assert payload["automatic_localized_compare_enabled"] is True
    assert payload["status"] == "passed"
    assert payload["compared_region_count"] == 2
    assert payload["unsupported_pair_count"] == 0
    assert payload["total_changes"] == 0
    assert payload["pairs"][0]["region_result_count"] == 2
    status = json.loads(Path(paths["region_aware_status_json"]).read_text(encoding="utf-8"))
    assert status["automatic_localized_compare_requested"] is True
    assert status["automatic_localized_compare_enabled"] is True
    assert status["automatic_localized_compare_status"] == "passed"
    validation = json.loads(Path(paths["multi_frame_validation_json"]).read_text(encoding="utf-8"))
    assert validation["automatic_localized_compare_enabled"] is True
    assert validation["automatic_localized_compare_compared_region_count"] == 2
    assert "localized_region_compare_results_json" in manifest_path.read_text(encoding="utf-8")


def test_folder_pipeline_region_sidecars_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAWING_COMPARE_MULTI_FRAME", "off")
    before_path = _write_detail_dxf(tmp_path / "before.dxf")
    after_path = _write_detail_dxf(tmp_path / "after.dxf")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    change_zones_path = artifact_dir / "change_zones.json"
    change_zones_path.write_text('{"zones": []}', encoding="utf-8")
    manifest_path = artifact_dir / "artifact_manifest.json"
    manifest_path.write_text('{"output_paths": {}}', encoding="utf-8")
    candidate = SimpleNamespace(
        pair_uuid="pair-a",
        source_a=SimpleNamespace(path_obj=before_path, path=str(before_path)),
        source_b=SimpleNamespace(path_obj=after_path, path=str(after_path)),
    )
    compare_summary = SimpleNamespace(
        items=[SimpleNamespace(candidate=candidate, status="completed")]
    )
    artifact_package = SimpleNamespace(
        output_paths={
            "change_zones_json": str(change_zones_path),
            "artifact_manifest_json": str(manifest_path),
        },
        warnings=[],
    )

    paths = _export_region_aware_artifacts(
        compare_summary=compare_summary,
        artifact_package=artifact_package,
        artifact_dir=artifact_dir,
        dxf_cache_dir=tmp_path / "dxf_cache",
    )

    assert paths == {}
    assert not (artifact_dir / "region_aware_status.json").exists()
