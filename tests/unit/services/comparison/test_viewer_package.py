# -*- coding: utf-8 -*-
"""Tests for lightweight viewer package export."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest

from src.services.comparison.export_profiles import apply_export_profile_to_json
from src.services.comparison.viewer_package import _render_pair_backgrounds_with_timeout, export_viewer_package


def _write_base_artifacts(base: Path, *, source_a: str = "old.dxf", source_b: str = "new.dxf") -> Path:
    artifact_dir = base / "artifacts"
    artifact_dir.mkdir()
    rows = [
        {
            "pair_id": "S21-0001",
            "zone_id": "C-001",
            "drawing_number": "S21-0001",
            "change_type": "modified",
            "severity": "high",
            "raw_change_count": "10",
            "bbox": "0,0,100,100",
            "old_bbox": "0,0,100,100",
            "layer": "BEAM",
            "entity_type": "LINE",
            "source_a": source_a,
            "source_b": source_b,
        },
        {
            "pair_id": "S21-0001",
            "zone_id": "C-002",
            "drawing_number": "S21-0001",
            "change_type": "deleted",
            "severity": "medium",
            "raw_change_count": "1",
            "bbox": "200,200,260,260",
            "old_bbox": "200,200,260,260",
            "layer": "AA-AXIS-LINE",
            "entity_type": "LINE",
            "source_a": source_a,
            "source_b": source_b,
        },
    ]
    with (artifact_dir / "change_zones.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "pair_count": 1,
        "zone_count": 2,
        "raw_change_count": 11,
        "zone_coverage_complete": True,
        "items": [
            {
                "pair_id": "S21-0001",
                "drawing_number": "S21-0001",
                "source_a": source_a,
                "source_b": source_b,
            }
        ],
    }
    (artifact_dir / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    dashboard = {
        "top_issues": [
            {
                "rank": 1,
                "priority_score": 100,
                "pair_id": "S21-0001",
                "zone_id": "C-001",
                "priority_reason": "구조 변경 우선 검토",
            }
        ],
        "drawings": [],
    }
    (artifact_dir / "review_dashboard.json").write_text(
        json.dumps(dashboard, ensure_ascii=False),
        encoding="utf-8",
    )
    return artifact_dir


def test_viewer_package_writes_overlay_json_without_rendering(tmp_path: Path) -> None:
    artifact_dir = _write_base_artifacts(tmp_path)

    package = export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
    )

    assert package.pair_count == 1
    assert package.overlay_count == 2
    assert package.page_count == 0
    assert package.tile_count == 0
    assert package.rendered_pair_count == 0
    assert package.lazy_pair_count == 1
    assert package.transform_complete is False
    assert Path(package.output_paths["viewer_manifest_json"]).exists()
    assert Path(package.output_paths["viewer_index_html"]).exists()

    overlay = json.loads((tmp_path / "viewer" / "overlays" / "S21-0001.json").read_text(encoding="utf-8"))
    assert overlay["schema_version"] == 2
    assert overlay["zone_count"] == 2
    assert overlay["overlays"][0]["selected_for_review"] is True
    assert overlay["overlays"][0]["after_bbox_px"] is None
    bbox = overlay["overlays"][0]["normalized_bbox"]
    assert 0.0 <= bbox["x"] <= 1.0
    assert 0.0 <= bbox["y"] <= 1.0

    manifest = json.loads((artifact_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert manifest["viewer_schema_version"] == 2
    assert manifest["viewer_overlay_count"] == 2
    assert manifest["transform_complete"] is False


def test_viewer_package_reads_change_zone_csv_bbox_columns(tmp_path: Path) -> None:
    artifact_dir = _write_base_artifacts(tmp_path)
    rows = [
        {
            "pair_id": "S21-0001",
            "zone_id": "C-101",
            "drawing_number": "S21-0001",
            "change_type": "deleted",
            "severity": "high",
            "raw_change_count": "7",
            "bbox_min_x": "10",
            "bbox_min_y": "20",
            "bbox_max_x": "90",
            "bbox_max_y": "140",
            "old_bbox_min_x": "12",
            "old_bbox_min_y": "22",
            "old_bbox_max_x": "92",
            "old_bbox_max_y": "142",
            "layers": "AA-DETL-PCN8|AA-XXXX-TXT1",
            "entity_types": "LINE|MTEXT",
            "source_a": "old.dxf",
            "source_b": "new.dxf",
        },
        {
            "pair_id": "S21-0001",
            "zone_id": "C-102",
            "drawing_number": "S21-0001",
            "change_type": "added",
            "severity": "low",
            "raw_change_count": "1",
            "bbox_min_x": "200",
            "bbox_min_y": "200",
            "bbox_max_x": "300",
            "bbox_max_y": "300",
            "old_bbox_min_x": "",
            "old_bbox_min_y": "",
            "old_bbox_max_x": "",
            "old_bbox_max_y": "",
            "layers": "AA-AXIS-LINE",
            "entity_types": "LINE",
            "source_a": "old.dxf",
            "source_b": "new.dxf",
        }
    ]
    with (artifact_dir / "change_zones.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
    )

    overlay = json.loads((tmp_path / "viewer" / "overlays" / "S21-0001.json").read_text(encoding="utf-8"))
    item = overlay["overlays"][0]
    assert item["bbox"] == {"min_x": 10.0, "min_y": 20.0, "max_x": 90.0, "max_y": 140.0}
    assert item["old_bbox"] == {"min_x": 12.0, "min_y": 22.0, "max_x": 92.0, "max_y": 142.0}
    assert item["normalized_bbox"] is not None
    assert item["normalized_bbox"]["width"] < 1.0
    assert item["normalized_bbox"]["height"] < 1.0
    assert item["layer"] == "AA-DETL-PCN8"
    assert item["entity_type"] == "LINE"


def test_marked_pdf_is_skipped_when_transform_is_missing_for_pdf_source(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    source_pdf = tmp_path / "new.pdf"
    doc = fitz.open()
    doc.new_page(width=300, height=300)
    doc.save(source_pdf)
    doc.close()
    artifact_dir = _write_base_artifacts(tmp_path, source_b=str(source_pdf))

    package = export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        export_marked_pdf=True,
        marked_pdf_mode="selected",
    )

    assert package.marked_pdf_count == 0
    assert package.marked_pdf_skipped_count == 1
    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pairs"][0]["marked_pdf_status"] == "skipped_missing_transform"
    assert not manifest["pairs"][0]["marked_pdf"]


def test_viewer_package_renders_pdf_pair_background(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    old_pdf = tmp_path / "old.pdf"
    new_pdf = tmp_path / "new.pdf"
    for path, label in ((old_pdf, "OLD"), (new_pdf, "NEW")):
        doc = fitz.open()
        page = doc.new_page(width=300, height=300)
        page.insert_text(fitz.Point(40, 80), label)
        doc.save(path)
        doc.close()
    artifact_dir = _write_base_artifacts(tmp_path, source_a=str(old_pdf), source_b=str(new_pdf))

    package = export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="top-issues",
        preview_dpi=72,
        preview_max_edge_px=400,
    )

    assert package.rendered_pair_count == 1
    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    pair = manifest["pairs"][0]
    assert pair["coordinate_source"] == "image_pixels"
    assert pair["background_type"] == "png"
    assert pair["visual_fidelity"] == "pdf_render"
    assert pair["render_lifecycle"] == "ready"
    assert pair["pdf_page"] == 0
    assert Path(pair["after_image"]).exists()
    overlay = json.loads((tmp_path / "viewer" / "overlays" / "S21-0001.json").read_text(encoding="utf-8"))
    assert overlay["coordinate_source"] == "image_pixels"
    assert overlay["visual_fidelity"] == "pdf_render"
    assert overlay["overlays"][0]["after_bbox_px"] == {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}


def test_viewer_package_keeps_pdf_page_copies_after_sharable_redaction(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    customer_dir = tmp_path / "customer"
    output_dir.mkdir()
    customer_dir.mkdir()
    old_pdf = customer_dir / "01.3PG1.pdf"
    new_pdf = customer_dir / "02.3PG1_R1.pdf"
    old_pdf.write_bytes(b"%PDF-1.4 before")
    new_pdf.write_bytes(b"%PDF-1.4 after")
    artifact_dir = _write_base_artifacts(output_dir, source_a=str(old_pdf), source_b=str(new_pdf))
    viewer_dir = output_dir / "viewer"

    export_viewer_package(
        artifact_dir,
        viewer_dir,
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
    )
    manifest_path = viewer_dir / "viewer_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pair = manifest["pairs"][0]
    assert Path(pair["before_page_pdf"]).exists()
    assert Path(pair["after_page_pdf"]).exists()
    assert Path(pair["before_page_pdf"]).name == "S21-0001_before.pdf"
    assert Path(pair["after_page_pdf"]).name == "S21-0001_after.pdf"
    assert pair["page_pdf"] == pair["after_page_pdf"]

    apply_export_profile_to_json(manifest_path, profile="sharable", package_root=output_dir)
    redacted = json.loads(manifest_path.read_text(encoding="utf-8"))["pairs"][0]
    assert redacted["source_a"] == "<redacted>/01.3PG1.pdf"
    assert redacted["source_b"] == "<redacted>/02.3PG1_R1.pdf"
    assert redacted["before_page_pdf"] == "viewer/pages/S21-0001_before.pdf"
    assert redacted["after_page_pdf"] == "viewer/pages/S21-0001_after.pdf"
    assert redacted["page_pdf"] == "viewer/pages/S21-0001_after.pdf"


def test_viewer_package_preserves_pdf_compare_dpi_from_change_zones(tmp_path: Path) -> None:
    artifact_dir = _write_base_artifacts(tmp_path, source_a="old.pdf", source_b="new.pdf")
    rows = [
        {
            "pair_id": "S21-0001",
            "zone_id": "C-001",
            "drawing_number": "S21-0001",
            "change_type": "modified",
            "severity": "high",
            "raw_change_count": "1",
            "bbox_min_x": "1304",
            "bbox_min_y": "1267",
            "bbox_max_x": "1414",
            "bbox_max_y": "1442",
            "source_a": "old.pdf",
            "source_b": "new.pdf",
            "bbox_coordinate_space": "image_pixels",
            "pdf_dpi": "200",
        },
    ]
    with (artifact_dir / "change_zones.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
        preview_dpi=400,
    )

    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    pair = manifest["pairs"][0]
    assert pair["coordinate_source"] == "image_pixels"
    assert pair["compare_pdf_dpi"] == 200
    overlay = json.loads((tmp_path / "viewer" / "overlays" / "S21-0001.json").read_text(encoding="utf-8"))
    assert overlay["compare_pdf_dpi"] == 200
    assert overlay["overlays"][0]["pdf_dpi"] == 200


def test_top_issues_policy_renders_png_tiles_and_pixel_bboxes(tmp_path: Path, monkeypatch) -> None:
    Image = pytest.importorskip("PIL.Image")
    old_dxf = tmp_path / "old.dxf"
    new_dxf = tmp_path / "new.dxf"
    old_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    new_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    artifact_dir = _write_base_artifacts(tmp_path, source_a=str(old_dxf), source_b=str(new_dxf))

    def fake_ensure(path: Path, _cache_dir: Path) -> Path:
        return path

    def fake_render(_dxf: Path, image_path: Path, *, dpi: int, max_edge_px: int):
        Image.new("RGB", (400, 400), "white").save(image_path)
        return {
            "min_x": 0.0,
            "min_y": 0.0,
            "max_x": 200.0,
            "max_y": 200.0,
            "scale_x": 2.0,
            "scale_y": 2.0,
            "img_width": 400,
            "img_height": 400,
        }

    monkeypatch.setattr("src.services.comparison.viewer_package._ensure_preview_dxf", fake_ensure)
    monkeypatch.setattr("src.services.comparison.viewer_package._render_dxf_to_png", fake_render)

    package = export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="top-issues",
        max_zone_tiles=1,
        export_marked_pdf=True,
    )

    assert package.rendered_pair_count == 1
    assert package.tile_count == 1
    assert package.marked_pdf_count == 1
    overlay = json.loads((tmp_path / "viewer" / "overlays" / "S21-0001.json").read_text(encoding="utf-8"))
    assert overlay["overlays"][0]["after_bbox_px"] == {"x": 0.0, "y": 200.0, "width": 200.0, "height": 200.0}
    assert Path(overlay["overlays"][0]["tile_image"]).exists()
    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pairs"][0]["background_type"] == "png"
    assert Path(manifest["pairs"][0]["after_image"]).exists()


def test_viewer_cache_dir_persists_lod_tiles_and_reuses_cache(tmp_path: Path, monkeypatch) -> None:
    Image = pytest.importorskip("PIL.Image")
    old_dxf = tmp_path / "old.dxf"
    new_dxf = tmp_path / "new.dxf"
    old_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    new_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    artifact_dir = _write_base_artifacts(tmp_path, source_a=str(old_dxf), source_b=str(new_dxf))
    cache_dir = tmp_path / "persistent-viewer-cache"

    def fake_ensure(path: Path, _cache_dir: Path) -> Path:
        return path

    def fake_render(_dxf: Path, image_path: Path, *, dpi: int, max_edge_px: int):
        Image.new("RGB", (400, 400), "white").save(image_path)
        return {
            "min_x": 0.0,
            "min_y": 0.0,
            "max_x": 200.0,
            "max_y": 200.0,
            "scale_x": 2.0,
            "scale_y": 2.0,
            "img_width": 400,
            "img_height": 400,
        }

    monkeypatch.setattr("src.services.comparison.viewer_package._ensure_preview_dxf", fake_ensure)
    monkeypatch.setattr("src.services.comparison.viewer_package._render_dxf_to_png", fake_render)

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer-a",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="top-issues",
        max_zone_tiles=0,
        viewer_cache_dir=cache_dir,
    )

    local_manifest = json.loads((tmp_path / "viewer-a" / "tiles_manifest.json").read_text(encoding="utf-8"))
    pair_manifest = local_manifest["pairs"]["S21-0001"]
    assert Path(pair_manifest["tile_root"]) == cache_dir / "tiles"
    assert (cache_dir / "tiles" / "S21-0001").exists()
    assert not (tmp_path / "viewer-a" / "tiles" / "S21-0001").exists()

    def fail_tile_write(**_kwargs):
        raise AssertionError("cache hit should avoid rewriting tile pyramid")

    monkeypatch.setattr("src.services.comparison.viewer_package.write_pair_tile_cache", fail_tile_write)
    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer-b",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="top-issues",
        max_zone_tiles=0,
        viewer_cache_dir=cache_dir,
    )
    second_manifest = json.loads((tmp_path / "viewer-b" / "tiles_manifest.json").read_text(encoding="utf-8"))
    assert second_manifest["pairs"]["S21-0001"]["cache_key"] == pair_manifest["cache_key"]


def test_viewer_index_counts_lod_tiles(tmp_path: Path, monkeypatch) -> None:
    Image = pytest.importorskip("PIL.Image")
    old_dxf = tmp_path / "old.dxf"
    new_dxf = tmp_path / "new.dxf"
    old_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    new_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    artifact_dir = _write_base_artifacts(tmp_path, source_a=str(old_dxf), source_b=str(new_dxf))

    def fake_ensure(path: Path, _cache_dir: Path) -> Path:
        return path

    def fake_render(_dxf: Path, image_path: Path, *, dpi: int, max_edge_px: int):
        Image.new("RGB", (400, 400), "white").save(image_path)
        return {"min_x": 0.0, "min_y": 0.0, "scale_x": 2.0, "scale_y": 2.0, "img_width": 400, "img_height": 400}

    monkeypatch.setattr("src.services.comparison.viewer_package._ensure_preview_dxf", fake_ensure)
    monkeypatch.setattr("src.services.comparison.viewer_package._render_dxf_to_png", fake_render)
    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="top-issues",
        max_zone_tiles=0,
    )

    index_html = (tmp_path / "viewer" / "index.html").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    lod_count = int(manifest["pairs"][0]["lod_tile_count"])
    assert lod_count > 0
    assert f"<td>{lod_count}</td>" in index_html


def test_viewer_package_can_skip_lod_tiles_for_fast_lightweight_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    Image = pytest.importorskip("PIL.Image")
    old_dxf = tmp_path / "old.dxf"
    new_dxf = tmp_path / "new.dxf"
    old_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    new_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    artifact_dir = _write_base_artifacts(tmp_path, source_a=str(old_dxf), source_b=str(new_dxf))

    def fake_ensure(path: Path, _cache_dir: Path) -> Path:
        return path

    def fake_render(_dxf: Path, image_path: Path, *, dpi: int, max_edge_px: int):
        Image.new("RGB", (400, 400), "white").save(image_path)
        return {
            "min_x": 0.0,
            "min_y": 0.0,
            "scale_x": 2.0,
            "scale_y": 2.0,
            "img_width": 400,
            "img_height": 400,
        }

    def fail_tile_write(**_kwargs):
        raise AssertionError("fast lightweight mode should not build LOD tiles")

    monkeypatch.setattr("src.services.comparison.viewer_package._ensure_preview_dxf", fake_ensure)
    monkeypatch.setattr("src.services.comparison.viewer_package._render_dxf_to_png", fake_render)
    monkeypatch.setattr("src.services.comparison.viewer_package.write_pair_tile_cache", fail_tile_write)

    package = export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="top-issues",
        max_zone_tiles=0,
        build_lod_tiles=False,
        viewer_perf_log=True,
    )

    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    perf_path = tmp_path / "viewer" / "viewer_perf.json"
    assert package.tile_count == 0
    assert manifest["build_lod_tiles"] is False
    assert manifest["pairs"][0]["lod_tile_count"] == 0
    assert manifest["pairs"][0]["overlay_tile_count"] == 0
    if perf_path.exists():
        perf = json.loads(perf_path.read_text(encoding="utf-8"))
        assert perf["event_count"] == 0


def test_viewer_render_timeout_keeps_overlay_only_result(tmp_path: Path, monkeypatch) -> None:
    old_dxf = tmp_path / "old.dxf"
    new_dxf = tmp_path / "new.dxf"
    old_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    new_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")

    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="render", timeout=1)

    monkeypatch.setattr("src.services.comparison.viewer_package.subprocess.run", timeout_run)
    result = _render_pair_backgrounds_with_timeout(
        pair_id="S21-0001",
        source_a=old_dxf,
        source_b=new_dxf,
        image_dir=tmp_path / "images",
        dxf_cache_dir=tmp_path / "dxf_cache",
        dpi=60,
        max_edge_px=1200,
        timeout_seconds=1,
    )

    assert result["render_status"] == "render_timeout"
    assert result["before_image"] == ""
    assert "timed out" in result["warnings"][0]


def test_all_policy_respects_max_viewer_pages(tmp_path: Path, monkeypatch) -> None:
    Image = pytest.importorskip("PIL.Image")
    artifact_dir = _write_base_artifacts(tmp_path)
    zones_path = artifact_dir / "change_zones.csv"
    rows = list(csv.DictReader(zones_path.open("r", encoding="utf-8-sig")))
    for row in list(rows):
        copied = dict(row)
        copied["pair_id"] = "S21-0002"
        copied["drawing_number"] = "S21-0002"
        rows.append(copied)
    with zones_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    def fake_ensure(path: Path, _cache_dir: Path) -> Path:
        return path

    def fake_render(_dxf: Path, image_path: Path, *, dpi: int, max_edge_px: int):
        Image.new("RGB", (200, 200), "white").save(image_path)
        return {
            "min_x": 0.0,
            "min_y": 0.0,
            "scale_x": 1.0,
            "scale_y": 1.0,
            "img_width": 200,
            "img_height": 200,
        }

    monkeypatch.setattr("src.services.comparison.viewer_package._ensure_preview_dxf", fake_ensure)
    monkeypatch.setattr("src.services.comparison.viewer_package._render_dxf_to_png", fake_render)

    package = export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        render_policy="all",
        max_viewer_pages=1,
    )

    assert package.pair_count == 2
    assert package.rendered_pair_count == 1
    assert package.lazy_pair_count == 1
    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    statuses = {pair["pair_id"]: pair["render_status"] for pair in manifest["pairs"]}
    assert sorted(statuses.values()) == ["rendered", "skipped_by_page_cap"]
