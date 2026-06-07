# -*- coding: utf-8 -*-
"""Tests for review-first dashboard outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.services.comparison.change_zones import export_executive_review_from_artifacts
from src.services.comparison.review_dashboard import export_review_dashboard


def _write_artifacts(base: Path) -> Path:
    artifact_dir = base / "artifacts"
    artifact_dir.mkdir()
    preview_dir = base / "preview"
    preview_dir.mkdir()
    zones_path = artifact_dir / "change_zones.csv"
    columns = [
        "pair_id",
        "zone_id",
        "drawing_number",
        "change_type",
        "severity",
        "status",
        "raw_change_count",
        "added",
        "deleted",
        "modified",
        "bbox_min_x",
        "bbox_min_y",
        "bbox_max_x",
        "bbox_max_y",
        "old_bbox_min_x",
        "old_bbox_min_y",
        "old_bbox_max_x",
        "old_bbox_max_y",
        "layers",
        "entity_types",
        "source_a",
        "source_b",
        "zone_input_source",
        "zone_input_count",
        "zone_coverage_complete",
        "reasons",
    ]
    rows = [
        {
            "pair_id": "S21-0001",
            "zone_id": "C-001",
            "drawing_number": "S21-0001",
            "change_type": "modified",
            "severity": "medium",
            "status": "review_required",
            "raw_change_count": "100",
            "added": "0",
            "deleted": "0",
            "modified": "100",
            "bbox_min_x": "0",
            "bbox_min_y": "0",
            "bbox_max_x": "100",
            "bbox_max_y": "100",
            "old_bbox_min_x": "10",
            "old_bbox_min_y": "10",
            "old_bbox_max_x": "90",
            "old_bbox_max_y": "90",
            "layers": "REBAR-TEXT",
            "entity_types": "ATTRIB | TEXT",
            "source_a": "old.dxf",
            "source_b": "new.dxf",
            "zone_input_source": "stream",
            "zone_input_count": "2",
            "zone_coverage_complete": "True",
            "reasons": "DOWEL BAR (2)SHD13@100 -> DOWEL BAR (2)SHD13@200",
        },
        {
            "pair_id": "S21-0001",
            "zone_id": "C-002",
            "drawing_number": "S21-0001",
            "change_type": "deleted",
            "severity": "high",
            "status": "review_required",
            "raw_change_count": "100",
            "added": "0",
            "deleted": "100",
            "modified": "0",
            "bbox_min_x": "200",
            "bbox_min_y": "200",
            "bbox_max_x": "260",
            "bbox_max_y": "260",
            "old_bbox_min_x": "200",
            "old_bbox_min_y": "200",
            "old_bbox_max_x": "260",
            "old_bbox_max_y": "260",
            "layers": "AA-AXIS-LINE | AA-XXXX-DIMS",
            "entity_types": "LINE",
            "source_a": "old.dxf",
            "source_b": "new.dxf",
            "zone_input_source": "stream",
            "zone_input_count": "2",
            "zone_coverage_complete": "True",
            "reasons": "",
        },
    ]
    with zones_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    preview_manifest = {
        "preview_count": 1,
        "artifacts": [
            {
                "pair_id": "S21-0001",
                "before_image": str(preview_dir / "before.png"),
                "after_image": str(preview_dir / "after.png"),
                "zone_overlays": [
                    {
                        "zone_id": "C-001",
                        "before_bbox_px": [0, 0, 10, 10],
                        "after_bbox_px": [1, 1, 11, 11],
                    }
                ],
                "warnings": [],
            }
        ],
    }
    preview_path = preview_dir / "preview_manifest.json"
    preview_path.write_text(json.dumps(preview_manifest), encoding="utf-8")

    manifest = {
        "pair_count": 1,
        "zone_count": 2,
        "raw_change_count": 200,
        "zone_coverage_complete": True,
        "cloud_region_count": 1,
        "cloud_omitted_zone_count": 1,
        "preview_manifest": str(preview_path),
        "output_paths": {"preview_manifest_json": str(preview_path)},
        "artifacts": [
            {
                "pair_id": "S21-0001",
                "drawing_number": "S21-0001",
                "source_a": "old.dxf",
                "source_b": "new.dxf",
                "after_marked_dxf": str(artifact_dir / "cloud_marked" / "S21-0001_after_marked.dxf"),
                "cloud_region_count": 1,
                "cloud_omitted_zone_count": 1,
            }
        ],
    }
    (artifact_dir / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return artifact_dir


def test_review_dashboard_folds_repetitive_layers_and_prioritizes_structural_issue(tmp_path: Path) -> None:
    artifact_dir = _write_artifacts(tmp_path)

    package = export_review_dashboard(artifact_dir, top_review_issues=1, top_issues_per_drawing=1)

    assert package.total_issue_count == 2
    assert package.review_issue_count == 1
    assert package.folded_pattern_count == 1
    assert package.top_project_issues[0]["zone_id"] == "C-001"
    assert package.top_project_issues[0]["preview_available"] is True
    assert package.top_project_issues[0]["after_bbox_px"] == [1, 1, 11, 11]
    assert package.top_project_issues[0]["old_bbox"] == [10.0, 10.0, 90.0, 90.0]
    dashboard = json.loads(Path(package.output_paths["review_dashboard_json"]).read_text(encoding="utf-8"))
    assert dashboard["top_drawings"][0]["drawing_number"] == "S21-0001"
    assert dashboard["top_issues"][0]["zone_id"] == "C-001"
    assert dashboard["review_queue"]["priority_issue_count"] == 1
    assert dashboard["review_queue"]["mode"] == "structural_core"
    queue_item = dashboard["review_queue"]["items"][0]
    assert queue_item["queue_key"] == "S21-0001:C-001"
    assert queue_item["pair_uuid"] == "S21-0001"
    assert queue_item["zone_id"] == "C-001"
    assert queue_item["old_bbox"] == [10.0, 10.0, 90.0, 90.0]
    assert queue_item["category"] == "rebar"
    assert queue_item["source_format"] == "cad"
    assert queue_item["detection_source"] == "cad_entity"
    assert queue_item["bbox_status"] == "exact"
    assert queue_item["review_status"] == "needs_review"
    assert "SHD13@100" in queue_item["change_summary_ko"]
    assert "SHD13@200" in queue_item["change_summary_ko"]
    assert queue_item["reason_ko"]
    assert dashboard["review_queue"]["pattern_group_count"] == 1
    assert dashboard["pattern_groups"][0]["pattern"]
    assert dashboard["preview_status_counts"]["real_preview"] == 1
    assert dashboard["action_counts"]["needs_review"] == 2
    assert Path(package.output_paths["review_dashboard_json"]).exists()
    assert Path(package.output_paths["review_priority_csv"]).exists()
    assert Path(package.output_paths["layer_pattern_summary_csv"]).exists()

    manifest = json.loads((artifact_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert manifest["review_issue_count"] == 1
    assert manifest["folded_pattern_count"] == 1
    assert manifest["review_queue"]["priority_issue_count"] == 1
    assert manifest["preview_status_counts"]["real_preview"] == 1


def test_review_dashboard_promotes_pdf_text_rebar_change_to_queue(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    zones_path = artifact_dir / "change_zones.csv"
    columns = [
        "pair_id",
        "zone_id",
        "drawing_number",
        "change_type",
        "severity",
        "status",
        "raw_change_count",
        "modified",
        "bbox_min_x",
        "bbox_min_y",
        "bbox_max_x",
        "bbox_max_y",
        "layers",
        "entity_types",
        "source_a",
        "source_b",
        "detection_source",
        "bbox_status",
        "reasons",
    ]
    with zones_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": "PDF-001",
                "zone_id": "P-001",
                "drawing_number": "PDF-001",
                "change_type": "modified",
                "severity": "high",
                "status": "review_required",
                "raw_change_count": "1",
                "modified": "1",
                "bbox_min_x": "100",
                "bbox_min_y": "120",
                "bbox_max_x": "180",
                "bbox_max_y": "170",
                "layers": "PDF-TEXT",
                "entity_types": "PDF_TEXT",
                "source_a": "old.pdf",
                "source_b": "new.pdf",
                "detection_source": "pdf_text",
                "bbox_status": "exact",
                "reasons": "D13@100 -> D13@200",
            }
        )
    manifest = {
        "pair_count": 1,
        "zone_count": 1,
        "raw_change_count": 1,
        "zone_coverage_complete": True,
        "artifacts": [{"pair_id": "PDF-001", "drawing_number": "PDF-001", "source_a": "old.pdf", "source_b": "new.pdf"}],
    }
    (artifact_dir / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    package = export_review_dashboard(artifact_dir)
    dashboard = json.loads(Path(package.output_paths["review_dashboard_json"]).read_text(encoding="utf-8"))
    item = dashboard["review_queue"]["items"][0]

    assert item["source_format"] == "pdf"
    assert item["detection_source"] == "pdf_text"
    assert item["bbox_status"] == "exact"
    assert item["category"] == "rebar"
    assert "D13@100" in item["change_summary_ko"]
    assert "D13@200" in item["change_summary_ko"]


def test_executive_review_html_is_korean_and_links_dashboard_outputs(tmp_path: Path) -> None:
    artifact_dir = _write_artifacts(tmp_path)

    package = export_executive_review_from_artifacts(artifact_dir, top_review_issues=1)
    html_path = Path(package.output_paths["executive_review_html"])
    html = html_path.read_text(encoding="utf-8")

    assert "도면 변경 검토 요약" in html
    assert "이번 비교 판정" in html
    assert "가장 먼저 볼 도면" in html
    assert "우선 검토 변경구역" in html
    assert "반복 패턴 변경" in html
    assert "구름마크가 생략된 이유" in html
    assert "Executive" not in html
    assert "Raw changes" not in html
    assert "Top Drawings" not in html
    assert Path(package.output_paths["review_dashboard_json"]).exists()
