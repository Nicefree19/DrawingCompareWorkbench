from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.services.comparison.folder_compare_pipeline as folder_pipeline_module
from src.services.comparison.change_zones import ChangeZoneOptions
from src.services.comparison.folder_compare_pipeline import (
    _build_auto_region_compare_payload,
    _export_region_aware_artifacts,
)
from src.services.comparison.detail_region_matcher import RegionMatch, RegionMatchSummary, match_sheet_regions
from src.services.comparison.dxf_entity_extractor import NormalizedEntity
from src.services.comparison.localized_compare import compare_localized_region_entities, localize_change_zones
from src.services.comparison.region_compare_pipeline import build_region_local_primary_change_zones
from src.services.comparison.region_profile import RegionProfile
from src.services.comparison.region_viewer_package import export_region_viewer_package
from src.services.comparison.sheet_region_detector import (
    SheetRegion,
    _score_frame_candidate,
    _table_rejection_reasons,
    detect_sheet_regions,
)


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


def _write_passing_region_pilot_summary(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "mode": "multi_detail_region_compare_pilot",
                "case_count": 1,
                "overall_status": "passed",
                "acceptance": {
                    "detected_region_rate": {"status": "passed"},
                    "whole_modelspace_fallback_rate": {"status": "passed"},
                    "user_approved_match_accuracy": {"status": "passed"},
                    "false_positive_reduction": {"status": "passed"},
                    "viewer_screenshot_count": {"status": "passed"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
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


def _write_tolerant_line_frame_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (2600.0, 0.0), dxfattribs={"layer": "FRAME"})
    msp.add_line((1.0, 1600.0), (2599.0, 1600.0), dxfattribs={"layer": "FRAME"})
    msp.add_line((0.0, 1.0), (0.0, 1599.0), dxfattribs={"layer": "FRAME"})
    msp.add_line((2600.0, 2.0), (2600.0, 1598.0), dxfattribs={"layer": "FRAME"})
    msp.add_line((250.0, 300.0), (2300.0, 300.0), dxfattribs={"layer": "BEAM"})
    text = msp.add_text("TL-001", dxfattribs={"height": 100, "layer": "TITLE"})
    text.set_placement((120.0, 1300.0))
    doc.saveas(str(path))
    return path


def _write_non_rectangular_closed_polyline_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (2600.0, 0.0), (2100.0, 1600.0), (0.0, 1200.0)],
        close=True,
        dxfattribs={"layer": "FRAME"},
    )
    msp.add_line((250.0, 300.0), (2300.0, 300.0), dxfattribs={"layer": "BEAM"})
    msp.add_line((300.0, 700.0), (1900.0, 900.0), dxfattribs={"layer": "BEAM"})
    text = msp.add_text("NR-001", dxfattribs={"height": 100, "layer": "TITLE"})
    text.set_placement((120.0, 1300.0))
    doc.saveas(str(path))
    return path


def _write_nearly_closed_polyline_frame_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (3000.0, 0.0), (3000.0, 1800.0), (0.0, 1800.0), (0.5, 0.5)],
        close=False,
        dxfattribs={"layer": "FRAME"},
    )
    msp.add_line((300.0, 350.0), (2700.0, 350.0), dxfattribs={"layer": "BEAM"})
    msp.add_line((300.0, 700.0), (2700.0, 700.0), dxfattribs={"layer": "BEAM"})
    text = msp.add_text("NC-001", dxfattribs={"height": 120, "layer": "TITLE"})
    text.set_placement((120.0, 1500.0))
    doc.saveas(str(path))
    return path


def _write_bow_tie_polyline_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (3000.0, 1800.0), (3000.0, 0.0), (0.0, 1800.0)],
        close=True,
        dxfattribs={"layer": "FRAME"},
    )
    msp.add_line((300.0, 350.0), (2700.0, 350.0), dxfattribs={"layer": "BEAM"})
    msp.add_line((300.0, 700.0), (2700.0, 700.0), dxfattribs={"layer": "BEAM"})
    text = msp.add_text("BT-001", dxfattribs={"height": 120, "layer": "TITLE"})
    text.set_placement((120.0, 1500.0))
    doc.saveas(str(path))
    return path


def _write_large_cluster_dxf(
    path: Path,
    *,
    cluster_count: int = 3,
    entities_per_cluster: int = 5000,
) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    columns = 100
    spacing = 8.0
    for cluster_index in range(cluster_count):
        origin_x = cluster_index * 6000.0
        for entity_index in range(entities_per_cluster):
            col = entity_index % columns
            row = entity_index // columns
            x = origin_x + col * spacing
            y = row * spacing
            msp.add_line((x, y), (x + 4.0, y), dxfattribs={"layer": "BEAM"})
    doc.saveas(str(path))
    return path


def _write_title_area_identity_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (4000.0, 0.0), (4000.0, 2200.0), (0.0, 2200.0)],
        close=True,
        dxfattribs={"layer": "FRAME"},
    )
    msp.add_line((350.0, 600.0), (2400.0, 600.0), dxfattribs={"layer": "BEAM"})
    msp.add_line((350.0, 1000.0), (2400.0, 1000.0), dxfattribs={"layer": "BEAM"})
    note = msp.add_text("X-999 BODY NOTE", dxfattribs={"height": 90, "layer": "NOTE"})
    note.set_placement((350.0, 1200.0))
    title = msp.add_text("D-201 PIER CAP DETAIL", dxfattribs={"height": 100, "layer": "TITLE"})
    title.set_placement((3100.0, 180.0))
    doc.saveas(str(path))
    return path


def _write_empty_title_frame_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (3000.0, 0.0), (3000.0, 1800.0), (0.0, 1800.0)],
        close=True,
        dxfattribs={"layer": "FRAME"},
    )
    msp.add_line((300.0, 350.0), (2700.0, 350.0), dxfattribs={"layer": "BEAM"})
    msp.add_line((300.0, 700.0), (2700.0, 700.0), dxfattribs={"layer": "BEAM"})
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


def test_detect_sheet_regions_sanitizes_missing_lwpolyline_subclass() -> None:
    source = Path("tests/data/comparison/cad_samples/dxf/simple_base.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert result.regions
    assert any("DXF sanitized in memory" in warning for warning in result.warnings)


def test_detect_sheet_regions_recovers_line_rectangular_frames(tmp_path: Path) -> None:
    source = _write_line_frame_dxf(tmp_path / "line_frames.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 2
    assert {region.drawing_number for region in result.regions} == {"L-001", "L-002"}
    assert all(region.detection_method == "cad_line_frame" for region in result.regions)


def test_detect_sheet_regions_recovers_slightly_gapped_line_frame(tmp_path: Path) -> None:
    source = _write_tolerant_line_frame_dxf(tmp_path / "tolerant_line_frame.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.detection_method == "cad_line_frame"
    assert region.drawing_number == "TL-001"
    assert "assembled from LINE border segments" in region.confidence_reasons


def test_detect_sheet_regions_does_not_treat_arbitrary_closed_polyline_as_frame(
    tmp_path: Path,
) -> None:
    source = _write_non_rectangular_closed_polyline_dxf(tmp_path / "not_rectangular.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert all(region.detection_method != "cad_frame" for region in result.regions)


def test_detect_sheet_regions_accepts_nearly_closed_polyline_frame(tmp_path: Path) -> None:
    source = _write_nearly_closed_polyline_frame_dxf(tmp_path / "nearly_closed.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.detection_method == "cad_frame"
    assert region.drawing_number == "NC-001"


def test_detect_sheet_regions_rejects_bow_tie_polyline_frame(tmp_path: Path) -> None:
    source = _write_bow_tie_polyline_dxf(tmp_path / "bow_tie.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert all(region.detection_method != "cad_frame" for region in result.regions)


def test_detect_sheet_regions_clusters_large_drawing_without_whole_modelspace_fallback(
    tmp_path: Path,
) -> None:
    source = _write_large_cluster_dxf(tmp_path / "large_clusters.dxf")

    started = time.perf_counter()
    result = detect_sheet_regions(source, side="before")
    elapsed = time.perf_counter() - started

    assert elapsed < 10.0
    assert result.status == "passed"
    assert len(result.regions) == 3
    assert all(region.detection_method == "cad_spatial_cluster" for region in result.regions)
    assert all("grid spatial clustering" in region.confidence_reasons for region in result.regions)
    assert "whole_modelspace" not in {region.detection_method for region in result.regions}


def test_detect_sheet_regions_prefers_title_area_identity(tmp_path: Path) -> None:
    source = _write_title_area_identity_dxf(tmp_path / "title_area.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.drawing_number == "D-201"
    assert "PIER CAP DETAIL" in region.title_text
    assert "X-999" not in region.title_text
    assert region.title_block_bbox is not None
    assert "title text from title area" in region.identity_evidence
    assert "drawing number from title area" in region.identity_evidence


def test_detect_sheet_regions_empty_title_degrades_confidence_without_crashing(
    tmp_path: Path,
) -> None:
    source = _write_empty_title_frame_dxf(tmp_path / "empty_title.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.detection_method == "cad_frame"
    assert region.title_text == ""
    assert region.drawing_number == ""
    assert region.confidence < region.frame_score
    assert "no title text found" in region.identity_evidence


def test_frame_candidate_scoring_records_profile_evidence() -> None:
    profile = RegionProfile.from_dict({"frame_layer_patterns": ["DETAIL-FRAME"]})

    candidate = _score_frame_candidate(
        {
            "is_frame": True,
            "bbox": (0.0, 0.0, 3000.0, 1800.0),
            "layer": "DETAIL-FRAME",
        },
        whole_area=20_000_000.0,
        region_profile=profile,
    )

    assert candidate is not None
    assert candidate.confidence > 0.82
    assert "frame layer profile match" in candidate.reasons

    table_candidate = _score_frame_candidate(
        {
            "is_frame": True,
            "bbox": (0.0, 0.0, 1200.0, 800.0),
            "layer": "BOM-TABLE",
        },
        whole_area=20_000_000.0,
        region_profile=profile,
    )

    assert table_candidate is not None
    assert table_candidate.confidence < 0.82
    assert "table/title keyword penalty" in table_candidate.reasons


def test_detect_sheet_regions_expands_insert_block_frames(tmp_path: Path) -> None:
    source = _write_insert_frame_dxf(tmp_path / "insert_frames.dxf")

    result = detect_sheet_regions(source, side="before")

    assert result.status == "passed"
    assert len(result.regions) == 2
    assert {region.drawing_number for region in result.regions} == {"B-001", "B-002"}
    assert all(region.detection_method == "cad_line_frame" for region in result.regions)
    assert all(
        "expanded from INSERT block virtual entities" in region.confidence_reasons
        for region in result.regions
    )
    assert all(
        "insert block DETAIL_FRAME_BLOCK" in region.confidence_reasons
        for region in result.regions
    )


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

    reasons = _table_rejection_reasons(
        (4600.0, 0.0, 5800.0, 800.0),
        [
            {"entity_type": "LINE", "layer": "00-block info", "text": ""},
            {"entity_type": "LINE", "layer": "00-block info", "text": ""},
            {"entity_type": "LINE", "layer": "00-block info", "text": ""},
            {"entity_type": "LINE", "layer": "00-block info", "text": ""},
            {"entity_type": "LINE", "layer": "00-block info", "text": ""},
            {"entity_type": "LINE", "layer": "00-block info", "text": ""},
            {"entity_type": "TEXT", "layer": "00-block info", "text": "DWG HD10"},
            {"entity_type": "TEXT", "layer": "00-block info", "text": "SCALE 1:20"},
            {"entity_type": "TEXT", "layer": "00-block info", "text": "DATE"},
            {"entity_type": "TEXT", "layer": "00-block info", "text": "CHECK"},
        ],
        whole_area=20_000_000.0,
    )

    assert "table keyword" in reasons
    assert "small relative area" in reasons
    assert "text heavy" in reasons
    assert "grid-like table" in reasons


def test_table_rejection_keeps_structural_detail_candidate_with_title_keywords() -> None:
    entities = (
        [{"entity_type": "ARC", "layer": "Anchor Bolt", "text": ""} for _ in range(18)]
        + [{"entity_type": "CIRCLE", "layer": "Anchor Bolt", "text": ""} for _ in range(12)]
        + [
            {"entity_type": "TEXT", "layer": "TITLE", "text": "DWG HD10"},
            {"entity_type": "TEXT", "layer": "TITLE", "text": "SCALE 1:20"},
        ]
    )

    reasons = _table_rejection_reasons(
        (0.0, 0.0, 3000.0, 1800.0),
        entities,
        whole_area=100_000_000.0,
    )

    assert reasons == tuple()


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


def test_single_unique_low_score_region_pair_is_reviewable_not_silent_unmatched() -> None:
    before = SheetRegion(
        region_id="before-cluster-1",
        source_path="before.dxf",
        source_format="dxf",
        bbox=(0.0, 0.0, 1000.0, 100.0),
        width=1000.0,
        height=100.0,
        area=100000.0,
        entity_count=100,
        entity_histogram={"ARC": 70, "CIRCLE": 30},
        layer_histogram={"Anchor Bolt": 70, "Zero": 30},
        detection_method="cad_spatial_cluster",
    )
    after = SheetRegion(
        region_id="after-cluster-1",
        source_path="after.dxf",
        source_format="dxf",
        bbox=(0.0, 0.0, 100000.0, 50000.0),
        width=100000.0,
        height=50000.0,
        area=5000000000.0,
        entity_count=5000,
        entity_histogram={"LINE": 4000, "LWPOLYLINE": 1000},
        layer_histogram={"AA-DETL": 4500, "TITLE": 500},
        title_text="HD10 DETAIL",
        drawing_number="HD10",
        detection_method="cad_spatial_cluster",
    )

    summary = match_sheet_regions([before], [after], pair_id="pair-low-score")

    assert summary.review_required_count == 1
    assert summary.unmatched_before_count == 0
    assert summary.unmatched_after_count == 0
    match = summary.matches[0]
    assert match.before_region_id == "before-cluster-1"
    assert match.after_region_id == "after-cluster-1"
    assert match.score < 0.60
    assert "unique region pair below review threshold" in " ".join(match.reasons)


def test_match_sheet_regions_blocks_auto_match_on_conflicting_drawing_numbers() -> None:
    before = _sheet_region("before-frame-1", 0.0, 0.0)
    after = _sheet_region("after-frame-1", 0.0, 0.0)
    before = SheetRegion(
        **{
            **before.__dict__,
            "drawing_number": "D-101",
            "title_text": "PIER CAP DETAIL",
            "entity_histogram": {"LINE": 10, "TEXT": 1},
            "layer_histogram": {"BEAM": 10, "TITLE": 1},
        }
    )
    after = SheetRegion(
        **{
            **after.__dict__,
            "drawing_number": "D-102",
            "title_text": "PIER CAP DETAIL",
            "entity_histogram": {"LINE": 10, "TEXT": 1},
            "layer_histogram": {"BEAM": 10, "TITLE": 1},
        }
    )

    summary = match_sheet_regions([before], [after], pair_id="pair-conflict")

    assert summary.auto_matched_count == 0
    assert all(match.status != "auto_matched" for match in summary.matches)


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


def test_region_local_primary_zones_ignore_outside_global_changes(tmp_path: Path) -> None:
    before_region = _sheet_region("before-frame-1", 0.0, 0.0)
    after_region = _sheet_region("after-frame-1", 100000.0, 50000.0)
    outside_before = _normalized_entity(
        "CIRCLE",
        "NOISE",
        {"center": (999000.0, 999000.0), "radius": 50.0},
        (999000.0, 999000.0),
    )
    outside_after = _normalized_entity(
        "CIRCLE",
        "NOISE",
        {"center": (1005000.0, 1005000.0), "radius": 75.0},
        (1005000.0, 1005000.0),
    )
    before_entities = _region_entity_map(0.0, 0.0, radius=10.0)
    after_entities = _region_entity_map(100000.0, 50000.0, radius=20.0)
    before_entities.setdefault("CIRCLE", []).append(outside_before)
    after_entities.setdefault("CIRCLE", []).append(outside_after)

    class FakeExtractor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def extract_from_file(self, path: Path) -> dict[str, list[NormalizedEntity]]:
            self.calls.append(Path(path).name)
            return before_entities if Path(path).name == "before.dxf" else after_entities

    extractor = FakeExtractor()
    payload = build_region_local_primary_change_zones(
        [
            {
                "pair_id": "pair-a",
                "source_a": tmp_path / "before.dxf",
                "source_b": tmp_path / "after.dxf",
                "before_result": SimpleNamespace(regions=(before_region,)),
                "after_result": SimpleNamespace(regions=(after_region,)),
                "match_summary": RegionMatchSummary(
                    pair_id="pair-a",
                    before_count=1,
                    after_count=1,
                    matches=(
                        RegionMatch(
                            match_id="m-1",
                            before_region_id="before-frame-1",
                            after_region_id="after-frame-1",
                            status="manual_matched",
                        ),
                    ),
                ),
            }
        ],
        extractor=extractor,
        zone_options=ChangeZoneOptions(cluster_distance=10.0, bbox_margin=0.0, min_marker_size=1.0),
    )

    assert extractor.calls == ["before.dxf", "after.dxf"]
    assert payload["primary_enabled"] is True
    assert payload["zone_count"] == 1
    zone = payload["zones"][0]
    assert zone["metadata"]["region_local_primary"] is True
    assert zone["metadata"]["region_match_id"] == "m-1"
    assert zone["bbox"] == pytest.approx([100880.0, 50880.0, 100920.0, 50920.0])
    assert 999000.0 not in zone["bbox"]


def test_region_local_primary_uses_resolved_dxf_sources_for_dwg_context(tmp_path: Path) -> None:
    before_region = _sheet_region("before-frame-1", 0.0, 0.0)
    after_region = _sheet_region("after-frame-1", 100000.0, 50000.0)
    cached_before = tmp_path / "cached-before.dxf"
    cached_after = tmp_path / "cached-after.dxf"
    original_before = tmp_path / "before.dwg"
    original_after = tmp_path / "after.dwg"
    before_entities = _region_entity_map(0.0, 0.0, radius=10.0)
    after_entities = _region_entity_map(100000.0, 50000.0, radius=20.0)

    class FakeExtractor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def extract_from_file(self, path: Path) -> dict[str, list[NormalizedEntity]]:
            self.calls.append(Path(path).name)
            return before_entities if Path(path).name == cached_before.name else after_entities

    extractor = FakeExtractor()
    payload = build_region_local_primary_change_zones(
        [
            {
                "pair_id": "pair-dwg",
                "source_a": original_before,
                "source_b": original_after,
                "region_compare_source_a": cached_before,
                "region_compare_source_b": cached_after,
                "region_compare_source_a_reason": "cached_dxf",
                "region_compare_source_b_reason": "cached_dxf",
                "before_result": SimpleNamespace(regions=(before_region,)),
                "after_result": SimpleNamespace(regions=(after_region,)),
                "match_summary": RegionMatchSummary(
                    pair_id="pair-dwg",
                    before_count=1,
                    after_count=1,
                    matches=(
                        RegionMatch(
                            match_id="m-1",
                            before_region_id="before-frame-1",
                            after_region_id="after-frame-1",
                            status="manual_matched",
                        ),
                    ),
                ),
            }
        ],
        extractor=extractor,
        zone_options=ChangeZoneOptions(cluster_distance=10.0, bbox_margin=0.0, min_marker_size=1.0),
    )

    assert extractor.calls == ["cached-before.dxf", "cached-after.dxf"]
    assert payload["unsupported_pair_count"] == 0
    assert payload["primary_enabled"] is True
    assert payload["pair_summaries"][0]["source_a"].endswith("before.dwg")
    assert payload["pair_summaries"][0]["region_compare_source_a"].endswith("cached-before.dxf")


def test_region_local_primary_represents_unmatched_detail_regions(tmp_path: Path) -> None:
    before_region = _sheet_region("before-frame-1", 0.0, 0.0)
    after_region = _sheet_region("after-frame-1", 100000.0, 50000.0)

    class EmptyExtractor:
        def extract_from_file(self, path: Path) -> dict[str, list[NormalizedEntity]]:
            return {}

    payload = build_region_local_primary_change_zones(
        [
            {
                "pair_id": "pair-a",
                "source_a": tmp_path / "before.dxf",
                "source_b": tmp_path / "after.dxf",
                "before_result": SimpleNamespace(regions=(before_region,)),
                "after_result": SimpleNamespace(regions=(after_region,)),
                "match_summary": RegionMatchSummary(
                    pair_id="pair-a",
                    before_count=1,
                    after_count=1,
                    matches=(
                        RegionMatch(
                            match_id="before-only",
                            before_region_id="before-frame-1",
                            status="unmatched_before",
                        ),
                        RegionMatch(
                            match_id="after-only",
                            after_region_id="after-frame-1",
                            status="unmatched_after",
                        ),
                    ),
                ),
            }
        ],
        extractor=EmptyExtractor(),
    )

    assert payload["primary_enabled"] is True
    assert payload["unmatched_detail_zone_count"] == 2
    assert [zone["change_type"] for zone in payload["zones"]] == ["deleted", "added"]
    assert payload["zones"][0]["old_bbox"] == pytest.approx([0.0, 0.0, 3000.0, 1800.0])
    assert payload["zones"][1]["bbox"] == pytest.approx([100000.0, 50000.0, 103000.0, 51800.0])


def test_region_viewer_manifest_renders_focus_packs_for_region_zones(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    before_path = tmp_path / "before.dxf"
    after_path = tmp_path / "after.dxf"
    before_path.write_text("0\nEOF\n", encoding="utf-8")
    after_path.write_text("0\nEOF\n", encoding="utf-8")
    (artifact_dir / "region_detection_summary.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "side": "before",
                        "regions": [
                            {
                                "region_id": "before-frame-1",
                                "source_path": str(before_path),
                                "source_format": "dxf",
                                "bbox": [0.0, 0.0, 3000.0, 1800.0],
                            }
                        ],
                    },
                    {
                        "side": "after",
                        "regions": [
                            {
                                "region_id": "after-frame-1",
                                "source_path": str(after_path),
                                "source_format": "dxf",
                                "bbox": [100000.0, 50000.0, 103000.0, 51800.0],
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "region_match_summary.json").write_text(
        json.dumps(
            {
                "summaries": [
                    {
                        "matches": [
                            {
                                "match_id": "m-1",
                                "status": "manual_matched",
                                "before_region_id": "before-frame-1",
                                "after_region_id": "after-frame-1",
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "localized_change_zones_v2.json").write_text(
        json.dumps(
            {
                "zones": [
                    {
                        "zone_id": "R-001",
                        "pair_id": "pair-a",
                        "change_type": "modified",
                        "bbox": [100880.0, 50880.0, 100920.0, 50920.0],
                        "old_bbox": [880.0, 880.0, 920.0, 920.0],
                        "metadata": {
                            "region_match_id": "m-1",
                            "before_region_id": "before-frame-1",
                            "after_region_id": "after-frame-1",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[Path, tuple[float, float, float, float], Path]] = []

    class FakeRenderResult:
        def __init__(self, output_path: Path) -> None:
            self.output_path = output_path

        def to_dict(self) -> dict:
            return {
                "output_path": str(self.output_path),
                "primitive_count": 3,
                "entity_count": 2,
                "truncated": False,
            }

    def fake_renderer(source_path: Path, bbox, output_dir: Path, **_kwargs):
        calls.append((source_path, bbox, output_dir))
        return FakeRenderResult(output_dir / "zone_focus.json")

    manifest_path = export_region_viewer_package(artifact_dir, renderer=fake_renderer)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["entry_count"] == 1
    assert len(calls) == 2
    assert calls[0][1] == pytest.approx((0.0, 0.0, 3000.0, 1800.0))
    assert calls[1][1] == pytest.approx((100000.0, 50000.0, 103000.0, 51800.0))
    entry = payload["entries"][0]
    assert entry["region_match_status"] == "manual_matched"
    assert entry["before"]["render_status"] == "rendered"
    assert entry["after"]["world_to_region_local"]["translate_x"] == -100000.0


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


def test_region_detection_diagnostic_reports_whole_modelspace_and_viewer_bbox_mismatch(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    artifact_dir = run_dir / "artifacts"
    viewer_dir = run_dir / "viewer"
    artifact_dir.mkdir(parents=True)
    viewer_dir.mkdir()
    (artifact_dir / "region_detection_summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_count": 2,
                "region_count": 2,
                "results": [
                    {
                        "side": "before",
                        "status": "passed",
                        "region_count": 1,
                        "regions": [
                            {
                                "region_id": "before-cluster-1",
                                "detection_method": "cad_spatial_cluster",
                                "bbox": [0.0, 0.0, 100.0, 100.0],
                            }
                        ],
                    },
                    {
                        "side": "after",
                        "status": "passed",
                        "region_count": 1,
                        "regions": [
                            {
                                "region_id": "after-whole-1",
                                "detection_method": "whole_modelspace",
                                "bbox": [0.0, 0.0, 10000.0, 10000.0],
                            }
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (artifact_dir / "region_match_summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pair_count": 1,
                "summaries": [
                    {
                        "pair_id": "pair-a",
                        "auto_matched_count": 0,
                        "review_required_count": 0,
                        "unmatched_before_count": 1,
                        "unmatched_after_count": 1,
                        "matches": [
                            {"status": "unmatched_before"},
                            {"status": "unmatched_after"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "localized_compare_summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pair_count": 1,
                "summaries": [
                    {
                        "pair_id": "pair-a",
                        "total_zones": 10,
                        "assigned_zones": 4,
                        "unassigned_zone_count": 6,
                        "gate_status": "review_required",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "region_aware_status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "feature_mode": "review_gate",
                "fallback_reason": "region-aware output requires review before automatic localized compare",
                "automatic_localized_compare_requested": False,
                "automatic_localized_compare_enabled": False,
                "automatic_localized_compare_status": "not_requested",
                "localized_gate_status": "review_required",
                "gate_reasons": [
                    "one or more detected regions are unmatched",
                ],
            }
        ),
        encoding="utf-8",
    )
    (viewer_dir / "viewer_manifest.json").write_text(
        json.dumps(
            {
                "pair_count": 1,
                "pairs": [
                    {
                        "pair_id": "pair-a",
                        "before_transform": {
                            "min_x": 0.0,
                            "min_y": 0.0,
                            "max_x": 100.0,
                            "max_y": 100.0,
                        },
                        "after_transform": {
                            "min_x": 0.0,
                            "min_y": 0.0,
                            "max_x": 10000.0,
                            "max_y": 10000.0,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[4]
    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "diagnose_region_detection.py"),
            str(run_dir),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["region_detection"]["whole_modelspace_count"] == 1
    assert payload["region_detection"]["methods"]["whole_modelspace"] == 1
    assert payload["region_matching"]["auto_matched_count"] == 0
    assert payload["region_matching"]["approved_match_count"] == 0
    assert payload["region_matching"]["unmatched_before_count"] == 1
    assert payload["region_matching"]["matches"][0]["status"] == "unmatched_before"
    assert payload["localized_compare"]["unassigned_zone_count"] == 6
    assert payload["localized_compare"]["review_required_pair_count"] == 1
    assert payload["region_aware_status"]["feature_mode"] == "review_gate"
    assert payload["region_aware_status"]["automatic_localized_compare_enabled"] is False
    assert payload["viewer"]["bbox_mismatch_pairs"] == 1
    assert payload["viewer"]["max_bbox_area_ratio"] == pytest.approx(10000.0)
    assert "single_region_per_source" in payload["risk_flags"]
    assert "no_approved_region_matches" in payload["risk_flags"]
    assert "region_local_not_enabled" in payload["risk_flags"]


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
    primary_path = Path(paths["localized_change_zones_v2_json"])
    primary_payload = json.loads(primary_path.read_text(encoding="utf-8"))
    assert primary_payload["mode"] == "region_local_primary"
    assert primary_payload["approved_match_count"] == 2
    assert primary_payload["compared_region_count"] == 2
    assert primary_payload["zone_count"] == 0
    region_viewer_path = Path(paths["region_viewer_manifest_json"])
    assert region_viewer_path.exists()
    region_viewer_payload = json.loads(region_viewer_path.read_text(encoding="utf-8"))
    assert region_viewer_payload["mode"] == "region_viewer"
    status = json.loads(Path(paths["region_aware_status_json"]).read_text(encoding="utf-8"))
    assert status["automatic_localized_compare_requested"] is True
    assert status["automatic_localized_compare_enabled"] is True
    assert status["automatic_localized_compare_status"] == "passed"
    assert status["region_local_primary_status"] == "skipped"
    validation = json.loads(Path(paths["multi_frame_validation_json"]).read_text(encoding="utf-8"))
    assert validation["automatic_localized_compare_enabled"] is True
    assert validation["automatic_localized_compare_compared_region_count"] == 2
    assert "localized_region_compare_results_json" in manifest_path.read_text(encoding="utf-8")
    assert "localized_change_zones_v2_json" in manifest_path.read_text(encoding="utf-8")
    assert "region_viewer_manifest_json" in manifest_path.read_text(encoding="utf-8")


def test_manual_matched_regions_are_eligible_for_auto_localized_compare(tmp_path: Path) -> None:
    before_region = _sheet_region("before-r1", 0.0, 0.0)
    after_region = _sheet_region("after-r1", 1000.0, 0.0)
    match_summary = RegionMatchSummary(
        pair_id="pair-manual",
        before_count=1,
        after_count=1,
        matches=(
            RegionMatch(
                match_id="manual-1",
                before_region_id="before-r1",
                after_region_id="after-r1",
                status="manual_matched",
            ),
        ),
    )
    context = {
        "pair_id": "pair-manual",
        "source_a": tmp_path / "before.dxf",
        "source_b": tmp_path / "after.dxf",
        "match_summary": match_summary,
        "localized_summary": SimpleNamespace(gate_status="passed", gate_reasons=()),
        "before_result": SimpleNamespace(regions=(before_region,)),
        "after_result": SimpleNamespace(regions=(after_region,)),
    }
    extractor = SimpleNamespace(extract_from_file=lambda _path: [])

    payload = _build_auto_region_compare_payload(
        [context],
        extractor=extractor,
        compare_localized_region_entities=lambda *_args, **_kwargs: object(),
        serialize_localized_region_result=lambda *_args, **_kwargs: {"total_changes": 0},
    )

    assert payload["automatic_localized_compare_enabled"] is True
    assert payload["compared_region_count"] == 1
    assert payload["pairs"][0]["region_result_count"] == 1


def test_auto_localized_compare_uses_resolved_dxf_sources_for_dwg_context(tmp_path: Path) -> None:
    before_region = _sheet_region("before-r1", 0.0, 0.0)
    after_region = _sheet_region("after-r1", 1000.0, 0.0)
    cached_before = tmp_path / "cached-before.dxf"
    cached_after = tmp_path / "cached-after.dxf"
    calls: list[str] = []
    match_summary = RegionMatchSummary(
        pair_id="pair-dwg",
        before_count=1,
        after_count=1,
        matches=(
            RegionMatch(
                match_id="manual-1",
                before_region_id="before-r1",
                after_region_id="after-r1",
                status="manual_matched",
            ),
        ),
    )
    context = {
        "pair_id": "pair-dwg",
        "source_a": tmp_path / "before.dwg",
        "source_b": tmp_path / "after.dwg",
        "region_compare_source_a": cached_before,
        "region_compare_source_b": cached_after,
        "region_compare_source_a_reason": "cached_dxf",
        "region_compare_source_b_reason": "cached_dxf",
        "match_summary": match_summary,
        "localized_summary": SimpleNamespace(gate_status="passed", gate_reasons=()),
        "before_result": SimpleNamespace(regions=(before_region,)),
        "after_result": SimpleNamespace(regions=(after_region,)),
    }

    def fake_extract(path: Path) -> list:
        calls.append(Path(path).name)
        return []

    payload = _build_auto_region_compare_payload(
        [context],
        extractor=SimpleNamespace(extract_from_file=fake_extract),
        compare_localized_region_entities=lambda *_args, **_kwargs: object(),
        serialize_localized_region_result=lambda *_args, **_kwargs: {"total_changes": 0},
    )

    assert calls == ["cached-before.dxf", "cached-after.dxf"]
    assert payload["unsupported_pair_count"] == 0
    assert payload["automatic_localized_compare_enabled"] is True
    assert payload["pairs"][0]["source_a"].endswith("before.dwg")
    assert payload["pairs"][0]["region_compare_source_a"].endswith("cached-before.dxf")


def test_folder_pipeline_default_enablement_runs_after_passing_pilot_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pilot_summary = _write_passing_region_pilot_summary(tmp_path / "pilot_summary.json")
    monkeypatch.delenv("DRAWING_COMPARE_AUTO_REGION_COMPARE", raising=False)
    monkeypatch.setenv("DRAWING_COMPARE_REGION_PILOT_SUMMARY", str(pilot_summary))
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

    assert "localized_region_compare_results_json" in paths
    assert "localized_change_zones_v2_json" in paths
    payload = json.loads(
        Path(paths["localized_region_compare_results_json"]).read_text(encoding="utf-8")
    )
    assert payload["automatic_localized_compare_request_source"] == "default_pilot_passed"
    assert payload["default_enablement"]["status"] == "enabled"
    status = json.loads(Path(paths["region_aware_status_json"]).read_text(encoding="utf-8"))
    assert status["automatic_localized_compare_requested"] is True
    assert status["region_default_enablement_status"] == "enabled"


def test_folder_pipeline_default_enablement_rejects_incomplete_pilot_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pilot_summary = tmp_path / "pilot_summary.json"
    pilot_summary.write_text(
        json.dumps(
            {
                "mode": "multi_detail_region_compare_pilot",
                "case_count": 1,
                "overall_status": "passed",
                "acceptance": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("DRAWING_COMPARE_AUTO_REGION_COMPARE", raising=False)
    monkeypatch.setenv("DRAWING_COMPARE_REGION_PILOT_SUMMARY", str(pilot_summary))
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

    assert "localized_region_compare_results_json" not in paths
    status = json.loads(Path(paths["region_aware_status_json"]).read_text(encoding="utf-8"))
    assert status["region_default_enablement_status"] == "pilot_not_passed"
    assert "pilot acceptance missing" in " ".join(
        status["region_default_enablement_gate_reasons"]
    )


def test_folder_pipeline_default_enablement_keeps_single_detail_on_global_compare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pilot_summary = _write_passing_region_pilot_summary(tmp_path / "pilot_summary.json")
    monkeypatch.delenv("DRAWING_COMPARE_AUTO_REGION_COMPARE", raising=False)
    monkeypatch.setenv("DRAWING_COMPARE_REGION_PILOT_SUMMARY", str(pilot_summary))
    before_path = _write_tolerant_line_frame_dxf(tmp_path / "before.dxf")
    after_path = _write_tolerant_line_frame_dxf(tmp_path / "after.dxf")
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

    assert "localized_region_compare_results_json" not in paths
    assert "localized_change_zones_v2_json" not in paths
    status = json.loads(Path(paths["region_aware_status_json"]).read_text(encoding="utf-8"))
    assert status["automatic_localized_compare_requested"] is False
    assert status["region_default_enablement_status"] == "review_required"
    assert "single-detail" in " ".join(status["region_default_enablement_gate_reasons"])


def test_folder_pipeline_default_enablement_can_be_rolled_back_by_feature_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pilot_summary = _write_passing_region_pilot_summary(tmp_path / "pilot_summary.json")
    monkeypatch.delenv("DRAWING_COMPARE_AUTO_REGION_COMPARE", raising=False)
    monkeypatch.setenv("DRAWING_COMPARE_REGION_PILOT_SUMMARY", str(pilot_summary))
    monkeypatch.setenv("DRAWING_COMPARE_REGION_LOCAL_DEFAULT", "off")
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

    assert "localized_region_compare_results_json" not in paths
    status = json.loads(Path(paths["region_aware_status_json"]).read_text(encoding="utf-8"))
    assert status["region_default_enablement_status"] == "disabled"
    assert status["automatic_localized_compare_requested"] is False


def test_folder_pipeline_does_not_resolve_region_compare_sources_before_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DRAWING_COMPARE_AUTO_REGION_COMPARE", raising=False)
    monkeypatch.setenv("DRAWING_COMPARE_REGION_LOCAL_DEFAULT", "off")
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

    def fail_if_resolved(*_args, **_kwargs):
        raise AssertionError("region compare source resolution must wait for the auto gate")

    monkeypatch.setattr(
        folder_pipeline_module,
        "_resolve_region_compare_source",
        fail_if_resolved,
    )

    paths = _export_region_aware_artifacts(
        compare_summary=compare_summary,
        artifact_package=artifact_package,
        artifact_dir=artifact_dir,
        dxf_cache_dir=tmp_path / "dxf_cache",
    )

    assert "localized_region_compare_results_json" not in paths
    status = json.loads(Path(paths["region_aware_status_json"]).read_text(encoding="utf-8"))
    assert status["automatic_localized_compare_requested"] is False


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
