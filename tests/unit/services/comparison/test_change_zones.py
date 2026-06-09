# -*- coding: utf-8 -*-
"""Tests for grouped drawing change zones and cloud-mark artifacts."""

import csv
import json
from datetime import datetime
from pathlib import Path

import pytest

from src.services.comparison.base import ChangeRecord, ChangeType, ComparisonResult
from src.services.comparison.change_zones import (
    ChangeZoneOptions,
    CloudMarkOptions,
    build_change_zones,
    export_change_artifacts,
    export_executive_review_from_artifacts,
    write_change_zone_stream,
)
from src.services.comparison.review_dashboard import export_review_dashboard
from src.services.comparison.drawing_batch import (
    BatchCompareItemResult,
    BatchCompareSummary,
    DrawingFileDescriptor,
    DrawingKind,
    MatchCandidate,
    MatchStatus,
    parse_filename_identity,
)


def _result(changes: list[ChangeRecord]) -> ComparisonResult:
    result = ComparisonResult(source_a="old.dxf", source_b="new.dxf")
    for change in changes:
        result.add_change(change)
    result.metadata["change_counts"] = {
        "added": result.added_count,
        "deleted": result.deleted_count,
        "modified": result.modified_count,
    }
    return result


def _line_change(
    key: str,
    change_type: ChangeType,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    layer: str = "BEAM",
) -> ChangeRecord:
    data = {"start": start, "end": end}
    return ChangeRecord(
        key=key,
        change_type=change_type,
        old_value=data if change_type != ChangeType.ADDED else None,
        new_value=data if change_type != ChangeType.DELETED else None,
        metadata={
            "layer": layer,
            "entity_type": "LINE",
            "change_type": change_type.value,
        },
    )


def _summary_for_result(tmp_path: Path, result: ComparisonResult) -> BatchCompareSummary:
    old_path = tmp_path / "S21-9001_old.dxf"
    new_path = tmp_path / "S21-9001_new.dxf"
    old_path.write_text("0\nEOF\n", encoding="utf-8")
    new_path.write_text("0\nEOF\n", encoding="utf-8")
    source_a = DrawingFileDescriptor(
        path=str(old_path),
        kind=DrawingKind.CAD,
        extension=".dxf",
        identity=parse_filename_identity(old_path),
    )
    source_b = DrawingFileDescriptor(
        path=str(new_path),
        kind=DrawingKind.CAD,
        extension=".dxf",
        identity=parse_filename_identity(new_path),
    )
    candidate = MatchCandidate(
        source_a=source_a,
        source_b=source_b,
        score=0.99,
        status=MatchStatus.AUTO_CONFIRMED,
    )
    return BatchCompareSummary(
        started_at=datetime.now(),
        requested_pairs=1,
        items=[
            BatchCompareItemResult(
                candidate=candidate,
                result=result,
                status="completed",
            )
        ],
    )


def test_nearby_raw_changes_group_into_one_zone() -> None:
    changes = [
        _line_change("a", ChangeType.ADDED, (0, 0), (100, 0)),
        _line_change("b", ChangeType.MODIFIED, (160, 0), (220, 0)),
    ]

    zones = build_change_zones(
        _result(changes),
        pair_id="S21-0001",
        drawing_number="S21-0001",
        options=ChangeZoneOptions(cluster_distance=120, bbox_margin=0),
    )

    assert len(zones) == 1
    assert zones[0].zone_id == "C-001"
    assert zones[0].raw_change_count == 2
    assert zones[0].added_count == 1
    assert zones[0].modified_count == 1


def test_single_line_zone_retains_geometry_points() -> None:
    # B안 — a lone LINE change keeps its endpoints (CAD-world mm) so the viewer
    # can draw the revision cloud along the actual leader line instead of its
    # (here very wide) axis-aligned bbox.
    changes = [_line_change("a", ChangeType.ADDED, (10.0, 20.0), (5000.0, 800.0))]

    zones = build_change_zones(
        _result(changes),
        pair_id="P",
        drawing_number="P",
        options=ChangeZoneOptions(cluster_distance=120, bbox_margin=0),
    )

    assert len(zones) == 1
    geom = zones[0].geometry
    assert geom == {"type": "LINE", "points": [[10.0, 20.0], [5000.0, 800.0]]}
    # round-trips through to_dict (so it can reach the overlay/QML layer)
    assert zones[0].to_dict()["geometry"] == geom


def test_multi_entity_zone_has_no_geometry() -> None:
    # Ambiguous (>1 raw change) → no single representative shape → None, so the
    # viewer keeps the bbox outline (Phase A) rather than guessing a polyline.
    changes = [
        _line_change("a", ChangeType.ADDED, (0, 0), (100, 0)),
        _line_change("b", ChangeType.ADDED, (10, 10), (110, 10)),
    ]

    zones = build_change_zones(
        _result(changes),
        pair_id="P",
        drawing_number="P",
        options=ChangeZoneOptions(cluster_distance=120, bbox_margin=0),
    )

    assert len(zones) == 1
    assert zones[0].raw_change_count == 2
    assert zones[0].geometry is None


def test_zone_overlay_carries_geometry_to_live_overlay() -> None:
    # B2 (live source pin) — a fresh DWG/DXF compare converts DrawingChangeZone
    # → ZoneOverlay → overlay dict. Geometry must survive THAT conversion, not
    # only DrawingChangeZone.to_dict() (which the live path does not use).
    from src.services.comparison.change_zones import DrawingChangeZone
    from src.services.comparison.review_project import _zone_overlay

    geom = {"type": "LINE", "points": [[-45547.0, -109829.0], [77894.0, -60599.0]]}
    zone = DrawingChangeZone(
        zone_id="C-002",
        pair_id="P",
        drawing_number="P",
        change_type="added",
        bbox=(-45547.0, -109829.0, 77894.0, -60599.0),
        geometry=geom,
    )

    overlay = _zone_overlay(zone, before_transform={}, after_transform={}).to_dict()

    assert overlay["geometry"] == geom


def test_change_zone_csv_roundtrips_geometry_to_overlay(tmp_path: Path) -> None:
    # B2 (overlay export) — geometry survives zone → CSV → _overlay_from_zone_row,
    # the reloaded overlay path used by the full zone-tree rebuild (so the cloud
    # doesn't revert to a bbox box after the initial preview).
    import csv as _csv

    from src.services.comparison.change_zones import (
        DrawingChangeZone,
        _write_change_zones_csv,
    )
    from src.services.comparison.viewer_package import _overlay_from_zone_row

    geom = {"type": "LINE", "points": [[-45547.0, -109829.0], [77894.0, -60599.0]]}
    zone = DrawingChangeZone(
        zone_id="C-002",
        pair_id="P",
        drawing_number="P",
        change_type="added",
        bbox=(-45547.0, -109829.0, 77894.0, -60599.0),
        geometry=geom,
    )
    csv_path = tmp_path / "change_zones.csv"
    _write_change_zones_csv(csv_path, [zone])

    rows = list(_csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    assert rows[0]["geometry"]  # column present and populated

    overlay = _overlay_from_zone_row(
        rows[0],
        (-50000.0, -120000.0, 80000.0, -50000.0),
        None,
        False,
        before_transform=None,
        after_transform=None,
    )
    assert overlay["geometry"] == geom


def test_stream_built_zone_recovers_line_geometry(tmp_path: Path) -> None:
    # B3 — the change-zone STREAM (used by large/real comparisons) must preserve
    # a LINE's geometry so stream-built zones can draw geometry-aware clouds.
    from src.services.comparison.change_zones import write_change_zone_stream

    change = _line_change("a", ChangeType.ADDED, (10.0, 20.0), (5000.0, 800.0))
    stream_path = tmp_path / "changes.stream.jsonl"
    meta = write_change_zone_stream([change], stream_path, pair_id="P")

    result = ComparisonResult(source_a="old.dxf", source_b="new.dxf")
    result.metadata.update(meta)  # change_zone_stream_path + complete flag

    zones = build_change_zones(
        result,
        pair_id="P",
        drawing_number="P",
        options=ChangeZoneOptions(cluster_distance=120, bbox_margin=0),
    )

    assert len(zones) == 1
    assert zones[0].metadata.get("zone_input_source") == "stream"
    assert zones[0].geometry == {"type": "LINE", "points": [[10.0, 20.0], [5000.0, 800.0]]}


def test_stream_without_geometry_field_is_backward_compatible(tmp_path: Path) -> None:
    # An older stream (no `geometry` field) must still build zones, with
    # geometry=None — additive field, no schema-version bump.
    import json as _json

    from src.services.comparison.change_zones import CHANGE_ZONE_STREAM_SCHEMA_VERSION

    rec = {
        "schema_version": CHANGE_ZONE_STREAM_SCHEMA_VERSION,
        "key": "a",
        "change_type": "added",
        "layer": "BEAM",
        "entity_type": "LINE",
        "bbox": [10.0, 20.0, 5000.0, 800.0],
        # NOTE: no "geometry" key — simulates a stream written before B3.
    }
    stream_path = tmp_path / "old.stream.jsonl"
    stream_path.write_text(_json.dumps(rec) + "\n", encoding="utf-8")

    result = ComparisonResult(source_a="old.dxf", source_b="new.dxf")
    result.metadata["change_zone_stream_path"] = str(stream_path)
    result.metadata["change_zone_stream_complete"] = True

    zones = build_change_zones(
        result,
        pair_id="P",
        drawing_number="P",
        options=ChangeZoneOptions(cluster_distance=120, bbox_margin=0),
    )

    assert len(zones) == 1
    assert zones[0].geometry is None


def test_pdf_visual_change_uses_top_left_bbox() -> None:
    change = ChangeRecord(
        key="page_0_Region_1",
        change_type=ChangeType.MODIFIED,
        location="page 0: (10, 20) - (50, 60)",
        metadata={
            "source_format": "pdf",
            "entity_type": "PDF_REGION",
            "layer": "PDF_PAGE_1",
            "page": 0,
            "x": 10,
            "y": 20,
            "w": 40,
            "h": 40,
            "pdf_dpi": 200,
        },
    )

    zones = build_change_zones(
        _result([change]),
        pair_id="pdf-pair",
        drawing_number="PDF-001",
        options=ChangeZoneOptions(cluster_distance=10, bbox_margin=0, min_marker_size=1),
    )

    assert len(zones) == 1
    assert zones[0].bbox == (10.0, 20.0, 50.0, 60.0)
    assert zones[0].layers == ("PDF_PAGE_1",)
    assert zones[0].entity_types == ("PDF_REGION",)
    assert float(zones[0].metadata["pdf_dpi"]) == 200


def test_pdf_page_match_metadata_survives_zone_and_csv_export(tmp_path: Path) -> None:
    change = ChangeRecord(
        key="page_a2_b5_region_1",
        change_type=ChangeType.MODIFIED,
        metadata={
            "source_format": "pdf",
            "entity_type": "PDF_REGION",
            "layer": "PDF_PAGE_MATCH",
            "page": 2,
            "page_a": 2,
            "page_b": 5,
            "page_match_status": "auto_confirmed",
            "page_match_score": 0.94,
            "bbox": [10, 20, 50, 60],
            "bbox_coordinate_space": "image_pixels",
            "pdf_dpi": 200,
        },
    )
    result = _result([change])

    zones = build_change_zones(
        result,
        pair_id="pdf-pair",
        drawing_number="PDF-001",
        options=ChangeZoneOptions(cluster_distance=10, bbox_margin=0, min_marker_size=1),
    )

    assert zones[0].metadata["page_a"] == "2"
    assert zones[0].metadata["page_b"] == "5"
    assert zones[0].metadata["page_match_status"] == "auto_confirmed"

    summary = _summary_for_result(tmp_path, result)
    package = export_change_artifacts(summary, tmp_path / "artifacts", export_cloud_marks=False)
    with open(package.output_paths["change_zones_csv"], encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["page_a"] == "2"
    assert rows[0]["page_b"] == "5"
    assert rows[0]["page_match_status"] == "auto_confirmed"
    assert rows[0]["page_match_score"] == "0.94"


def test_distant_raw_changes_remain_separate_zones() -> None:
    changes = [
        _line_change("a", ChangeType.ADDED, (0, 0), (100, 0)),
        _line_change("b", ChangeType.ADDED, (5000, 5000), (5100, 5000)),
    ]

    zones = build_change_zones(
        _result(changes),
        options=ChangeZoneOptions(cluster_distance=100, bbox_margin=0),
    )

    assert len(zones) == 2
    assert [zone.zone_id for zone in zones] == ["C-001", "C-002"]


def test_transitive_mega_zone_is_split_into_local_buckets() -> None:
    changes = [
        _line_change(f"chain-{idx}", ChangeType.ADDED, (idx * 90.0, 0), (idx * 90.0 + 10.0, 0))
        for idx in range(12)
    ]

    zones = build_change_zones(
        _result(changes),
        options=ChangeZoneOptions(
            cluster_distance=100,
            bbox_margin=0,
            min_marker_size=1,
            max_zone_raw_changes=100,
            max_zone_span=250,
            mega_zone_grid_size=250,
        ),
    )

    assert len(zones) > 1
    assert sum(zone.raw_change_count for zone in zones) == len(changes)
    assert all((zone.bbox[2] - zone.bbox[0]) <= 260 for zone in zones)
    assert all(zone.metadata.get("mega_zone_split") is True for zone in zones)


def test_deleted_and_moved_changes_keep_old_bbox() -> None:
    deleted = _line_change("deleted", ChangeType.DELETED, (10, 10), (110, 10))
    moved = ChangeRecord(
        key="moved",
        change_type=ChangeType.MODIFIED,
        old_value={"start": (1000, 1000), "end": (1100, 1000)},
        new_value={"start": (1200, 1200), "end": (1300, 1200)},
        metadata={
            "layer": "GRID",
            "entity_type": "LINE",
            "change_type": "modified",
            "old_x": 1050,
            "old_y": 1000,
        },
    )

    zones = build_change_zones(
        _result([deleted, moved]),
        options=ChangeZoneOptions(cluster_distance=50, bbox_margin=0),
    )

    assert len(zones) == 2
    assert zones[0].deleted_count == 1
    assert zones[0].old_bbox is not None
    assert zones[1].change_type == "moved"
    assert zones[1].old_bbox is not None


def test_cloud_marker_creates_one_label_per_zone(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")

    base = tmp_path / "base.dxf"
    output = tmp_path / "marked.dxf"
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (100, 0))
    doc.saveas(str(base))

    zones = build_change_zones(
        _result(
            [
                _line_change("a", ChangeType.ADDED, (0, 0), (100, 0)),
                _line_change("b", ChangeType.ADDED, (5000, 5000), (5100, 5000)),
            ]
        ),
        options=ChangeZoneOptions(cluster_distance=100, bbox_margin=0),
    )

    from src.services.comparison.dxf_cloud_marker import DxfCloudMarker

    DxfCloudMarker(add_labels=True).create_marked_dxf_from_zones(base, zones, output)

    marked = ezdxf.readfile(str(output))
    labels = [
        entity.dxf.text
        for entity in marked.modelspace()
        if entity.dxftype() == "TEXT" and entity.dxf.text.startswith("C-")
    ]
    assert sorted(labels) == ["C-001", "C-002"]


def test_export_change_artifacts_writes_register_and_manifest(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")

    old_path = tmp_path / "S21-0001_old.dxf"
    new_path = tmp_path / "S21-0001_new.dxf"
    for path in (old_path, new_path):
        doc = ezdxf.new()
        doc.modelspace().add_line((0, 0), (100, 0))
        doc.saveas(str(path))

    source_a = DrawingFileDescriptor(
        path=str(old_path),
        kind=DrawingKind.CAD,
        extension=".dxf",
        identity=parse_filename_identity(old_path),
    )
    source_b = DrawingFileDescriptor(
        path=str(new_path),
        kind=DrawingKind.CAD,
        extension=".dxf",
        identity=parse_filename_identity(new_path),
    )
    candidate = MatchCandidate(
        source_a=source_a,
        source_b=source_b,
        score=0.99,
        status=MatchStatus.AUTO_CONFIRMED,
    )
    summary = BatchCompareSummary(
        started_at=datetime.now(),
        requested_pairs=1,
        items=[
            BatchCompareItemResult(
                candidate=candidate,
                result=_result([_line_change("a", ChangeType.ADDED, (0, 0), (100, 0))]),
                status="completed",
            )
        ],
    )

    package = export_change_artifacts(summary, tmp_path / "artifacts", export_cloud_marks=True)

    assert package.zone_count == 1
    assert Path(package.output_paths["change_zones_csv"]).exists()
    assert Path(package.output_paths["review_index_html"]).exists()
    assert Path(package.output_paths["artifact_manifest_json"]).exists()
    assert Path(package.artifacts[0].after_marked_dxf).exists()
    result = summary.items[0].result
    assert result.metadata["change_zone_count"] == 1
    assert result.metadata["marked_artifacts"]["after_marked_dxf"]


def test_selected_cloud_export_caps_regions_and_records_omitted_zones(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")

    old_path = tmp_path / "S21-0002_old.dxf"
    new_path = tmp_path / "S21-0002_new.dxf"
    for path in (old_path, new_path):
        doc = ezdxf.new()
        doc.modelspace().add_line((0, 0), (100, 0))
        doc.saveas(str(path))

    source_a = DrawingFileDescriptor(
        path=str(old_path),
        kind=DrawingKind.CAD,
        extension=".dxf",
        identity=parse_filename_identity(old_path),
    )
    source_b = DrawingFileDescriptor(
        path=str(new_path),
        kind=DrawingKind.CAD,
        extension=".dxf",
        identity=parse_filename_identity(new_path),
    )
    candidate = MatchCandidate(
        source_a=source_a,
        source_b=source_b,
        score=0.99,
        status=MatchStatus.AUTO_CONFIRMED,
    )
    changes = [
        _line_change(
            f"line_{index}",
            ChangeType.ADDED,
            (index * 5000.0, 0),
            (index * 5000.0 + 100, 0),
        )
        for index in range(5)
    ]
    summary = BatchCompareSummary(
        started_at=datetime.now(),
        requested_pairs=1,
        items=[
            BatchCompareItemResult(
                candidate=candidate,
                result=_result(changes),
                status="completed",
            )
        ],
    )

    package = export_change_artifacts(
        summary,
        tmp_path / "artifacts",
        zone_options=ChangeZoneOptions(cluster_distance=10, bbox_margin=0),
        cloud_options=CloudMarkOptions(
            export_mode="selected",
            region_distance=10,
            max_regions_per_pair=2,
            max_regions_total=2,
        ),
        export_cloud_marks=True,
    )

    assert package.zone_count == 5
    assert package.cloud_region_count == 2
    assert package.cloud_omitted_zone_count == 3
    assert package.artifacts[0].cloud_region_count == 2
    assert package.artifacts[0].cloud_omitted_zone_count == 3
    assert Path(package.artifacts[0].after_marked_dxf).exists()
    with open(package.output_paths["cloud_omitted_zones_csv"], "r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert {row["omitted_reason"] for row in rows} == {"max_region_cap"}


def test_executive_review_is_generated_from_existing_artifacts(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    marked = artifact_dir / "cloud_marked" / "S21-0001_after_marked.dxf"
    marked.parent.mkdir()
    marked.write_text("0\nEOF\n", encoding="utf-8")
    manifest = {
        "output_dir": str(artifact_dir),
        "pair_count": 2,
        "zone_count": 3,
        "raw_change_count": 17,
        "zone_coverage_complete": True,
        "cloud_export_mode": "selected",
        "cloud_region_count": 2,
        "cloud_omitted_zone_count": 1,
        "artifacts": [
            {
                "pair_id": "S21-0001",
                "drawing_number": "S21-0001",
                "after_marked_dxf": str(marked),
                "cloud_region_count": 2,
                "cloud_omitted_zone_count": 1,
            },
            {
                "pair_id": "S21-0002",
                "drawing_number": "S21-0002",
                "cloud_region_count": 0,
                "cloud_omitted_zone_count": 0,
            },
        ],
        "output_paths": {
            "change_zones_csv": str(artifact_dir / "change_zones.csv"),
            "artifact_manifest_json": str(artifact_dir / "artifact_manifest.json"),
        },
    }
    (artifact_dir / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    with open(artifact_dir / "change_zones.csv", "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "pair_id",
                "zone_id",
                "drawing_number",
                "change_type",
                "severity",
                "raw_change_count",
                "added",
                "deleted",
                "modified",
                "bbox_min_x",
                "bbox_min_y",
                "bbox_max_x",
                "bbox_max_y",
                "layers",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": "S21-0001",
                "zone_id": "C-001",
                "drawing_number": "S21-0001",
                "change_type": "mixed",
                "severity": "high",
                "raw_change_count": 10,
                "added": 5,
                "deleted": 5,
                "modified": 0,
                "bbox_min_x": 0,
                "bbox_min_y": 0,
                "bbox_max_x": 100,
                "bbox_max_y": 100,
                "layers": "AA-DETL-PCN8 | GRID",
            }
        )
        writer.writerow(
            {
                "pair_id": "S21-0002",
                "zone_id": "C-001",
                "drawing_number": "S21-0002",
                "change_type": "added",
                "severity": "medium",
                "raw_change_count": 4,
                "added": 4,
                "deleted": 0,
                "modified": 0,
                "bbox_min_x": 0,
                "bbox_min_y": 0,
                "bbox_max_x": 10,
                "bbox_max_y": 10,
                "layers": "COLUMN",
            }
        )
        writer.writerow(
            {
                "pair_id": "S21-0001",
                "zone_id": "C-002",
                "drawing_number": "S21-0001",
                "change_type": "deleted",
                "severity": "low",
                "raw_change_count": 3,
                "added": 0,
                "deleted": 3,
                "modified": 0,
                "bbox_min_x": 100,
                "bbox_min_y": 100,
                "bbox_max_x": 110,
                "bbox_max_y": 110,
                "layers": "AA-XXXX-TEST",
            }
        )

    package = export_executive_review_from_artifacts(
        artifact_dir,
        top_drawings=1,
        top_zones=2,
    )

    assert package.raw_change_count == 17
    assert package.zone_count == 3
    assert package.top_drawings[0]["drawing_number"] == "S21-0001"
    assert [zone["zone_id"] for zone in package.top_zones] == ["C-001", "C-001"]
    assert {pattern["pattern"] for pattern in package.repeated_patterns} == {
        "AA-DETL-PCN8",
        "AA-XXXX-*",
    }
    assert Path(package.output_paths["executive_review_html"]).exists()
    assert Path(package.output_paths["drawing_change_brief_md"]).exists()
    assert Path(package.output_paths["drawing_change_brief_csv"]).exists()
    assert Path(package.output_paths["review_dashboard_json"]).exists()
    assert Path(package.output_paths["review_priority_csv"]).exists()
    assert Path(package.output_paths["layer_pattern_summary_csv"]).exists()
    html_text = Path(package.output_paths["executive_review_html"]).read_text(encoding="utf-8")
    assert "원시 변경" in html_text
    assert "도면 변경 검토 요약" in html_text
    assert "S21-0001" in html_text
    updated_manifest = json.loads((artifact_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert updated_manifest["output_paths"]["executive_review_html"].endswith("executive_review.html")
    assert updated_manifest["output_paths"]["review_dashboard_json"].endswith("review_dashboard.json")


def test_stream_backed_zones_use_all_records_when_memory_details_are_truncated(tmp_path: Path) -> None:
    changes = [
        _line_change(f"line_{index}", ChangeType.ADDED, (index * 1000.0, 0), (index * 1000.0 + 100, 0))
        for index in range(5)
    ]
    result = _result(changes[:2])
    result.metadata["change_counts"] = {"added": 5, "deleted": 0, "modified": 0}
    result.metadata["truncated_changes"] = True
    result.metadata.update(write_change_zone_stream(changes, tmp_path / "zones.jsonl", pair_id="S21-9001"))

    zones = build_change_zones(
        result,
        options=ChangeZoneOptions(cluster_distance=10, bbox_margin=0),
    )

    assert len(zones) == 5
    assert sum(zone.raw_change_count for zone in zones) == 5
    assert result.metadata["change_zone_input_source"] == "stream"
    assert result.metadata["change_zone_input_count"] == 5
    assert result.metadata["change_zone_coverage_complete"] is True


def test_canonical_dict_bboxes_preserve_side_specific_modified_zone() -> None:
    change = ChangeRecord(
        key="diff:modified_reorigin",
        change_type=ChangeType.MODIFIED,
        old_value={
            "bbox": {"min_x": 1000.0, "min_y": 2000.0, "max_x": 1100.0, "max_y": 2000.0},
        },
        new_value={
            "bbox": {"min_x": 151000.5, "min_y": -88000.0, "max_x": 151100.5, "max_y": -88000.0},
        },
        metadata={
            "layer": "BEAM",
            "entity_type": "LINE",
            "bbox": {"min_x": 1000.0, "min_y": 2000.0, "max_x": 1100.5, "max_y": 2000.0},
            "old_bbox": {"min_x": 1000.0, "min_y": 2000.0, "max_x": 1100.0, "max_y": 2000.0},
            "new_bbox": {"min_x": 151000.5, "min_y": -88000.0, "max_x": 151100.5, "max_y": -88000.0},
            "detection_source": "canonical-drawing-compare",
            "registered_reorigin": True,
        },
    )

    zones = build_change_zones(
        _result([change]),
        options=ChangeZoneOptions(cluster_distance=10, bbox_margin=0),
    )

    assert len(zones) == 1
    zone = zones[0]
    assert zone.change_type == "modified"
    assert zone.old_bbox == (1000.0, 1975.0, 1100.0, 2025.0)
    assert zone.bbox == (151000.5, -88025.0, 151100.5, -87975.0)


def test_stream_text_evidence_reaches_review_queue_summary(tmp_path: Path) -> None:
    change = ChangeRecord(
        key="attrib_rebar_spacing",
        change_type=ChangeType.MODIFIED,
        old_value={"text": "D13@100", "content": "D13@100"},
        new_value={"text": "D13@200", "content": "D13@200"},
        location="(100, 100)",
        metadata={
            "layer": "REBAR-TEXT",
            "entity_type": "ATTRIB",
            "change_type": "modified",
            "change_detail": '내용 "D13@100" -> "D13@200"',
            "change_category": "content",
            "x": 100,
            "y": 100,
            "w": 30,
            "h": 20,
            "source_format": "cad",
            "detection_source": "cad_entity",
            "bbox_status": "exact",
        },
    )
    result = _result([change])
    result.metadata.update(write_change_zone_stream([change], tmp_path / "zones.jsonl", pair_id="S21-9001"))

    package = export_change_artifacts(
        _summary_for_result(tmp_path, result),
        tmp_path / "artifacts",
        export_cloud_marks=False,
    )
    with open(package.output_paths["change_zones_csv"], encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["old_text"] == "D13@100"
    assert rows[0]["new_text"] == "D13@200"

    dashboard = export_review_dashboard(package.output_dir)
    item = dashboard.review_queue["items"][0]
    assert item["category"] == "rebar"
    assert "D13@100" in item["change_summary_ko"]
    assert "D13@200" in item["change_summary_ko"]


def test_truncated_result_without_stream_is_reported_as_incomplete(tmp_path: Path) -> None:
    result = _result([_line_change("line_1", ChangeType.ADDED, (0, 0), (100, 0))])
    result.metadata["change_counts"] = {"added": 5, "deleted": 0, "modified": 0}
    result.metadata["truncated_changes"] = True

    package = export_change_artifacts(
        _summary_for_result(tmp_path, result),
        tmp_path / "artifacts",
        export_cloud_marks=False,
    )

    assert package.zone_coverage_complete is False
    assert package.artifacts[0].zone_input_source == "memory"
    assert package.artifacts[0].zone_coverage_complete is False
    assert any("retained detailed change records" in warning for warning in package.warnings)


def test_corrupt_stream_is_reported_without_silent_memory_fallback(tmp_path: Path) -> None:
    stream = tmp_path / "bad.jsonl"
    stream.write_text("{bad json\n", encoding="utf-8")
    result = _result([_line_change("line_1", ChangeType.ADDED, (0, 0), (100, 0))])
    result.metadata["change_zone_stream_path"] = str(stream)
    result.metadata["change_zone_stream_complete"] = True

    package = export_change_artifacts(
        _summary_for_result(tmp_path, result),
        tmp_path / "artifacts",
        export_cloud_marks=False,
    )

    assert package.zone_count == 0
    assert package.zone_coverage_complete is False
    assert package.artifacts[0].zone_input_source == "stream"
    assert any("invalid change-zone stream JSON" in warning for warning in package.warnings)
