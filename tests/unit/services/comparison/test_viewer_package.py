# -*- coding: utf-8 -*-
"""Tests for lightweight viewer package export."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest

from src.services.comparison import viewer_package as viewer_package_module
from src.services.comparison.cad_visual_backend import (
    CAD_VISUAL_BACKEND_DISABLED,
    CadVisualBackend,
    CadVisualBackendCapabilities,
)
from src.services.comparison.export_profiles import apply_export_profile_to_json
from src.services.comparison.render_backend_registry import (
    CAD_VISUAL_BACKEND_ENV,
    RenderBackendRegistry,
)
from src.services.comparison.viewer_package import (
    CAD_VISUAL_CONVERSION_DEFERRED,
    ViewerPackageOptions,
    _build_v2_manifest_from_v1,
    _build_v3_manifest_from_v1,
    _render_pair_backgrounds_with_timeout,
    export_viewer_package,
)
from src.services.comparison.visual_asset import validate_visual_asset_policy


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


def test_v3_manifest_uses_lightweight_source_hash_without_mislabeling_file_hash(tmp_path: Path) -> None:
    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    before.write_text("0\nEOF\n", encoding="utf-8")
    after.write_text("0\nEOF\n", encoding="utf-8")
    manifest = _build_v3_manifest_from_v1(
        v1_manifest={
            "schema_version": 2,
            "pairs": [
                {
                    "pair_uuid": "pair-1",
                    "source_a": str(before),
                    "source_b": str(after),
                    "coordinate_source": "cad_world",
                }
            ],
        },
        options=ViewerPackageOptions(),
        viewer_root=tmp_path / "viewer",
    )

    assert manifest.before_source_signature.source_hash
    assert manifest.after_source_signature.source_hash
    assert manifest.before_source_signature.file_hash == ""
    assert manifest.before_source_signature.file_size == before.stat().st_size


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

    viewer_manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    pair_entry = viewer_manifest["pairs"][0]
    page_manifest_path = Path(pair_entry["overlay_pages_manifest"])
    assert page_manifest_path.exists()
    page_manifest = json.loads(page_manifest_path.read_text(encoding="utf-8"))
    assert page_manifest["overlay_count"] == 2
    assert page_manifest["page_count"] == 1
    assert viewer_manifest["directories"]["overlay_pages"].endswith("overlay_pages")


def test_viewer_package_records_disabled_cad_visual_provenance_without_conversion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_dir = _write_base_artifacts(tmp_path)

    def fail_conversion(*_args, **_kwargs):
        raise AssertionError("default viewer export must not invoke CAD visual conversion")

    monkeypatch.setattr(RenderBackendRegistry, "convert_cad_visual", fail_conversion)

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
    )

    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    pair_conversion = manifest["pairs"][0]["cad_visual_conversion"]
    assert manifest["cad_visual_backend"]["backend_id"] == "disabled"
    assert pair_conversion["before"]["status"] == "skipped"
    assert pair_conversion["before"]["reason_code"] == CAD_VISUAL_BACKEND_DISABLED
    assert pair_conversion["before"]["backend_id"] == "disabled"
    assert pair_conversion["before"]["license_id"] == "none"
    assert pair_conversion["after"]["reason_code"] == CAD_VISUAL_BACKEND_DISABLED
    cad_manifest_path = (
        tmp_path
        / "viewer"
        / "visual_assets"
        / "S21-0001"
        / "before"
        / "cad_visual_provenance"
        / "visual_asset_manifest.json"
    )
    assert cad_manifest_path.exists()
    cad_manifest = json.loads(cad_manifest_path.read_text(encoding="utf-8"))
    assert cad_manifest["asset_kind"] == "relative_only"
    assert cad_manifest["status"] == "skipped"
    assert cad_manifest["reason_code"] == CAD_VISUAL_BACKEND_DISABLED
    assert cad_manifest["visual_backend_id"] == "disabled"
    assert manifest["pairs"][0]["visual_assets"]["before"]["cad_visual_provenance"]["manifest_path"] == str(cad_manifest_path)


def test_viewer_package_writes_source_pdf_visual_asset_manifests_for_pdf_pair(tmp_path: Path) -> None:
    before = tmp_path / "before.pdf"
    after = tmp_path / "after.pdf"
    before.write_bytes(b"%PDF-1.4\n% before\n%%EOF\n")
    after.write_bytes(b"%PDF-1.4\n% after\n%%EOF\n")
    artifact_dir = _write_base_artifacts(tmp_path, source_a=str(before), source_b=str(after))

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
    )

    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    pair = manifest["pairs"][0]
    assert manifest["visual_asset_manifest_count"] == 2
    assert len(manifest["visual_asset_manifest_paths"]) == 2
    before_entry = pair["visual_assets"]["before"]["source_pdf"]
    after_entry = pair["visual_assets"]["after"]["source_pdf"]
    before_manifest = json.loads(Path(before_entry["manifest_path"]).read_text(encoding="utf-8"))
    after_manifest = json.loads(Path(after_entry["manifest_path"]).read_text(encoding="utf-8"))

    assert before_manifest["asset_kind"] == "source_pdf"
    assert before_manifest["asset_path"].endswith("S21-0001_before.pdf")
    assert before_manifest["source_hash"]
    assert before_manifest["cache_key_hash"]
    assert before_manifest["nonblank_probe_status"] == "not_probed"
    assert after_manifest["asset_kind"] == "source_pdf"
    assert after_manifest["asset_path"].endswith("S21-0001_after.pdf")


def test_viewer_package_tile_cache_key_tracks_visual_asset_target_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = tmp_path / "before.pdf"
    after = tmp_path / "after.pdf"
    before.write_bytes(b"%PDF-1.4\n% before\n%%EOF\n")
    after.write_bytes(b"%PDF-1.4\n% after\n%%EOF\n")
    artifact_dir = _write_base_artifacts(tmp_path, source_a=str(before), source_b=str(after))

    def _fake_probe_first(**_kwargs: object) -> dict:
        return {
            "status": "not_probed",
            "method": "pixel_nonblank_probe_unavailable",
            "probe_hash": "probe-hash-v1",
            "asset_hash": "asset-hash",
            "probe_target_hash": "target-hash-v1",
            "probe_target_path": "",
        }

    monkeypatch.setattr(viewer_package_module, "probe_visual_asset_nonblank", _fake_probe_first)
    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer_1",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
    )
    manifest_1 = json.loads((tmp_path / "viewer_1" / "viewer_manifest.json").read_text(encoding="utf-8"))

    def _fake_probe_second(**_kwargs: object) -> dict:
        payload = _fake_probe_first()
        payload["probe_hash"] = "probe-hash-v2"
        payload["probe_target_hash"] = "target-hash-v2"
        return payload

    monkeypatch.setattr(viewer_package_module, "probe_visual_asset_nonblank", _fake_probe_second)
    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer_2",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
    )
    manifest_2 = json.loads((tmp_path / "viewer_2" / "viewer_manifest.json").read_text(encoding="utf-8"))

    pair_1 = manifest_1["pairs"][0]
    pair_2 = manifest_2["pairs"][0]
    assert pair_1["visual_asset_identity_hash"] != pair_2["visual_asset_identity_hash"]
    assert pair_1["tile_cache_key"] != pair_2["tile_cache_key"]


def test_viewer_package_writes_sidecar_pdf_visual_asset_manifests_for_cad_pair(tmp_path: Path) -> None:
    artifact_dir = _write_base_artifacts(tmp_path, source_a="before.dwg", source_b="after.dxf")
    before_sidecar = artifact_dir / "before_visual.pdf"
    after_sidecar = artifact_dir / "after_visual.pdf"
    before_sidecar.write_bytes(b"%PDF-1.4\n% before sidecar\n%%EOF\n")
    after_sidecar.write_bytes(b"%PDF-1.4\n% after sidecar\n%%EOF\n")
    manifest_path = artifact_dir / "artifact_manifest.json"
    artifact_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_manifest["items"][0]["before_sidecar_pdf"] = before_sidecar.name
    artifact_manifest["items"][0]["after_sidecar_pdf"] = after_sidecar.name
    manifest_path.write_text(json.dumps(artifact_manifest, ensure_ascii=False), encoding="utf-8")

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
    )

    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    pair = manifest["pairs"][0]
    before_entry = pair["visual_assets"]["before"]["sidecar_pdf"]
    after_entry = pair["visual_assets"]["after"]["sidecar_pdf"]
    before_manifest = json.loads(Path(before_entry["manifest_path"]).read_text(encoding="utf-8"))
    after_manifest = json.loads(Path(after_entry["manifest_path"]).read_text(encoding="utf-8"))

    assert manifest["visual_asset_manifest_count"] == 4
    assert pair["before_sidecar_pdf"].endswith("S21-0001_before_sidecar.pdf")
    assert pair["after_sidecar_pdf"].endswith("S21-0001_after_sidecar.pdf")
    assert before_manifest["asset_kind"] == "sidecar_pdf"
    assert before_manifest["status"] == "source_only"
    assert before_manifest["asset_path"].endswith("S21-0001_before_sidecar.pdf")
    assert before_manifest["source_hash"]
    assert before_manifest["cache_key_hash"]
    assert before_manifest["nonblank_probe_status"] == "not_probed"
    assert validate_visual_asset_policy(before_manifest, customer_grade=True) == []
    assert after_manifest["asset_kind"] == "sidecar_pdf"
    assert "source_pdf" not in pair["visual_assets"]["before"]


def test_viewer_package_hybrid_cad_pair_renders_sidecar_pdf_and_display_overlays(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    artifact_dir = _write_base_artifacts(tmp_path, source_a="before.dwg", source_b="after.dwg")
    before_sidecar = artifact_dir / "before_visual.pdf"
    after_sidecar = artifact_dir / "after_visual.pdf"
    for path, label in ((before_sidecar, "BEFORE"), (after_sidecar, "AFTER")):
        doc = fitz.open()
        page = doc.new_page(width=420, height=297)
        page.insert_text(fitz.Point(40, 80), label)
        doc.save(path)
        doc.close()

    manifest_path = artifact_dir / "artifact_manifest.json"
    artifact_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_manifest["items"][0].update(
        {
            "before_sidecar_pdf": before_sidecar.name,
            "after_sidecar_pdf": after_sidecar.name,
            "cad_frame_bbox": [0, 0, 420, 297],
        }
    )
    manifest_path.write_text(json.dumps(artifact_manifest, ensure_ascii=False), encoding="utf-8")

    package = export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="top-issues",
        preview_dpi=72,
        preview_max_edge_px=1000,
        build_lod_tiles=False,
    )

    assert package.rendered_pair_count == 1
    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    pair = manifest["pairs"][0]
    assert pair["source_a"].endswith("before.dwg")
    assert pair["coordinate_source"] == "cad_world"
    assert pair["visual_fidelity"] == "pdf_render"
    assert pair["display_overlay_space"] == "image_pixels_tl"
    assert pair["transform_quality"] == "exact"
    assert pair["after_sidecar_pdf"].endswith("S21-0001_after_sidecar.pdf")
    assert pair["page_pdf"].endswith("S21-0001_after_sidecar.pdf")
    assert Path(pair["after_image"]).exists()
    assert pair["after_transform"]["coordinate_space"] == "cad_wcs_mm"
    assert pair["after_transform"]["display_coordinate_space"] == "image_pixels_tl"
    assert pair["after_transform"]["cad_pdf_alignment"]["quality"] == "exact"

    overlay_payload = json.loads((tmp_path / "viewer" / "overlays" / "S21-0001.json").read_text(encoding="utf-8"))
    assert overlay_payload["coordinate_source"] == "cad_world"
    assert overlay_payload["display_overlay_space"] == "image_pixels_tl"
    overlay = overlay_payload["overlays"][0]
    assert overlay["zone_id"] == "C-001"
    assert overlay["display_overlay_space"] == "image_pixels_tl"
    assert overlay["transform_quality"] == "exact"
    assert overlay["display_bbox"] == pytest.approx([0.0, 197.0, 100.0, 297.0], abs=1.0)
    assert overlay["after_bbox_px"] == pytest.approx(
        {"x": 0.0, "y": 197.0, "width": 100.0, "height": 100.0},
        abs=1.0,
    )

    v3_manifest = json.loads((tmp_path / "viewer" / "viewer_manifest_v3.json").read_text(encoding="utf-8"))
    assert v3_manifest["source_kind"] == "normalized_dxf"
    assert v3_manifest["display_overlay_space"] == "image_pixels_tl"


def test_viewer_package_ignores_env_cad_visual_backend_for_metadata_only_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_dir = _write_base_artifacts(tmp_path)
    monkeypatch.setenv(CAD_VISUAL_BACKEND_ENV, "qcad_professional_cli")

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
    )

    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    assert manifest["cad_visual_backend"]["backend_id"] == "disabled"
    assert manifest["pairs"][0]["cad_visual_conversion"]["before"]["backend_id"] == "disabled"


def test_viewer_package_defers_opted_in_cad_visual_backend_without_conversion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_dir = _write_base_artifacts(tmp_path)

    class FakePdfBackend(CadVisualBackend):
        @property
        def capabilities(self) -> CadVisualBackendCapabilities:
            return CadVisualBackendCapabilities(
                backend_id="fake_pdf",
                backend_version="1.2.3",
                license_id="test_license",
                can_convert_to_pdf=True,
                enabled_by_default=True,
            )

        def convert(self, _request):  # pragma: no cover - must not be called
            raise AssertionError("R5.5 only records provenance; it must not convert")

        def probe(self):  # pragma: no cover - must not be called
            raise AssertionError("R5.5 metadata-only export must not probe external tools")

    registry = RenderBackendRegistry()
    registry.register(FakePdfBackend())
    monkeypatch.setattr(
        "src.services.comparison.viewer_package.get_default_render_backend_registry",
        lambda: registry,
    )

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
        cad_visual_backend="fake_pdf",
        cad_visual_conversion_timeout_seconds=42,
    )

    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    before = manifest["pairs"][0]["cad_visual_conversion"]["before"]
    assert manifest["cad_visual_backend"]["backend_id"] == "fake_pdf"
    assert manifest["cad_visual_backend"]["backend_version"] == "1.2.3"
    assert manifest["cad_visual_backend"]["license_id"] == "test_license"
    assert before["status"] == "skipped"
    assert before["reason_code"] == CAD_VISUAL_CONVERSION_DEFERRED
    assert before["backend_id"] == "fake_pdf"
    assert before["backend_version"] == "1.2.3"
    assert before["license_id"] == "test_license"
    assert before["metadata"]["timeout_s"] == 42.0
    assert before["metadata"]["provenance_only"] is True


def test_v2_and_v3_manifests_preserve_cad_visual_backend_capabilities(tmp_path: Path) -> None:
    cad_backend = {
        "backend_id": "fake_pdf",
        "backend_version": "1.2.3",
        "license_id": "test_license",
        "can_convert_to_pdf": True,
    }
    v1 = {
        "schema_version": 2,
        "cad_visual_backend": cad_backend,
        "pairs": [
            {
                "pair_id": "S21-0001",
                "source_a": "old.dxf",
                "source_b": "new.dxf",
                "coordinate_source": "cad_world",
            }
        ],
    }

    v2 = _build_v2_manifest_from_v1(v1_manifest=v1, options=ViewerPackageOptions())
    v3 = _build_v3_manifest_from_v1(
        v1_manifest=v1,
        options=ViewerPackageOptions(),
        viewer_root=tmp_path / "viewer",
    )

    assert v2.renderer_capabilities["cad_visual_backend"]["backend_id"] == "fake_pdf"
    assert v2.renderer_capabilities["cad_visual_backend"]["license_id"] == "test_license"
    assert v3.renderer_capabilities["cad_visual_backend"]["backend_version"] == "1.2.3"
    assert "cad_visual:fake_pdf:1.2.3:test_license" in v3.before_source_signature.backend_sig


def test_v2_manifest_accepts_v1_img_width_height_transforms() -> None:
    v1 = {
        "schema_version": 2,
        "pairs": [
            {
                "pair_id": "S21-0001",
                "source_a": "old.dxf",
                "source_b": "new.dxf",
                "coordinate_source": "cad_world",
                "before_image": "before.png",
                "after_image": "after.png",
                "before_transform": {
                    "min_x": -60.0,
                    "min_y": -100.0,
                    "max_x": 460000.0,
                    "max_y": 9000.0,
                    "img_width": 8000,
                    "img_height": 1779,
                },
                "after_transform": {
                    "min_x": -60.0,
                    "min_y": -90.0,
                    "max_x": 378000.0,
                    "max_y": 9000.0,
                    "img_width": 8000,
                    "img_height": 2079,
                },
            }
        ],
    }

    v2 = _build_v2_manifest_from_v1(v1_manifest=v1, options=ViewerPackageOptions())

    pair = v2.pairs[0]
    assert pair.before is not None
    assert pair.after is not None
    assert pair.before.pixel_size == (8000, 1779)
    assert pair.after.pixel_size == (8000, 2079)
    assert v2.shared_world_bbox == (-60.0, -100.0, 460000.0, 9000.0)


def test_viewer_package_limits_overlay_rows_before_materialisation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_dir = _write_base_artifacts(tmp_path)
    rows = []
    for index in range(100):
        zone_id = f"C-{index:03d}"
        rows.append(
            {
                "pair_id": "S21-0001",
                "zone_id": zone_id,
                "drawing_number": "S21-0001",
                "change_type": "modified",
                "severity": "medium",
                "raw_change_count": str(100 - index),
                "bbox": f"{index},{index},{index + 10},{index + 10}",
                "old_bbox": f"{index},{index},{index + 10},{index + 10}",
                "layer": "BEAM",
                "entity_type": "LINE",
                "source_a": "old.dxf",
                "source_b": "new.dxf",
            }
        )
    with (artifact_dir / "change_zones.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    dashboard = {
        "top_issues": [
            {
                "rank": 1,
                "priority_score": 100,
                "pair_id": "S21-0001",
                "zone_id": "C-099",
                "priority_reason": "selected late row",
            }
        ]
    }
    (artifact_dir / "review_dashboard.json").write_text(
        json.dumps(dashboard, ensure_ascii=False),
        encoding="utf-8",
    )

    original_overlay = viewer_package_module._overlay_from_zone_row
    call_count = {"value": 0}

    def counted_overlay(*args, **kwargs):
        call_count["value"] += 1
        return original_overlay(*args, **kwargs)

    monkeypatch.setattr(viewer_package_module, "_overlay_from_zone_row", counted_overlay)

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
        max_overlay_records_per_pair=5,
    )

    overlay = json.loads((tmp_path / "viewer" / "overlays" / "S21-0001.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))

    assert call_count["value"] == 5
    assert overlay["overlay_count"] == 5
    assert overlay["zone_count"] == 100
    assert overlay["overlay_total_count"] == 100
    assert overlay["overlay_deferred_count"] == 95
    assert overlay["overlay_deferred"] is True
    assert "C-099" in {item["zone_id"] for item in overlay["overlays"]}
    assert manifest["pairs"][0]["overlay_deferred_count"] == 95
    assert manifest["overlay_count"] == 5
    source_rows = list(csv.DictReader((artifact_dir / "change_zones.csv").open("r", encoding="utf-8-sig")))
    assert len(source_rows) == 100


def test_viewer_package_truncates_large_legacy_overlay_json_after_paging(tmp_path: Path) -> None:
    artifact_dir = _write_base_artifacts(tmp_path)
    rows = []
    for index in range(30):
        rows.append(
            {
                "pair_id": "S21-0001",
                "zone_id": f"C-{index:03d}",
                "drawing_number": "S21-0001",
                "change_type": "modified",
                "severity": "medium",
                "raw_change_count": str(index + 1),
                "bbox": f"{index},{index},{index + 10},{index + 10}",
                "old_bbox": f"{index},{index},{index + 10},{index + 10}",
                "layer": "BEAM",
                "entity_type": "LINE",
                "source_a": "old.dxf",
                "source_b": "new.dxf",
            }
        )
    with (artifact_dir / "change_zones.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    package = export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="lazy",
        max_visible_overlays=25,
    )

    overlay = json.loads((tmp_path / "viewer" / "overlays" / "S21-0001.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    pair_entry = manifest["pairs"][0]
    page_manifest_path = Path(pair_entry["overlay_pages_manifest"])
    page_manifest = json.loads(page_manifest_path.read_text(encoding="utf-8"))

    assert package.overlay_count == 30
    assert overlay["overlay_count"] == 30
    assert overlay["overlay_legacy_count"] == 25
    assert overlay["overlay_legacy_truncated"] is True
    assert len(overlay["overlays"]) == 25
    assert page_manifest["overlay_count"] == 30
    assert page_manifest["page_count"] == 1
    assert pair_entry["overlay_legacy_count"] == 25
    assert pair_entry["overlay_legacy_truncated"] is True


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
    assert manifest["visual_asset_manifest_count"] == 4
    before_source_entry = pair["visual_assets"]["before"]["source_pdf"]
    before_raster_entry = pair["visual_assets"]["before"]["raster_fallback"]
    before_source_manifest = json.loads(Path(before_source_entry["manifest_path"]).read_text(encoding="utf-8"))
    before_raster_manifest = json.loads(Path(before_raster_entry["manifest_path"]).read_text(encoding="utf-8"))
    source_probe = json.loads(Path(before_source_manifest["metadata"]["nonblank_probe"]).read_text(encoding="utf-8"))
    raster_probe = json.loads(Path(before_raster_manifest["metadata"]["nonblank_probe"]).read_text(encoding="utf-8"))
    assert before_source_manifest["nonblank_probe_status"] == "passed"
    assert before_raster_manifest["nonblank_probe_status"] == "passed"
    assert source_probe["method"] == "pixel_nonblank_probe"
    assert source_probe["asset_path"].endswith("S21-0001_before.pdf")
    assert source_probe["probe_target_path"].endswith("S21-0001_before.png")
    assert raster_probe["asset_path"].endswith("S21-0001_before.png")
    assert validate_visual_asset_policy(before_source_manifest, customer_grade=True) == []
    assert validate_visual_asset_policy(before_raster_manifest, customer_grade=True) == []
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


def test_pdf_overlay_pixel_bbox_scales_to_render_background_dpi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Image = pytest.importorskip("PIL.Image")
    before = tmp_path / "old.pdf"
    after = tmp_path / "new.pdf"
    before.write_bytes(b"%PDF-1.4 before")
    after.write_bytes(b"%PDF-1.4 after")
    artifact_dir = _write_base_artifacts(
        tmp_path,
        source_a=str(before),
        source_b=str(after),
    )
    rows = [
        {
            "pair_id": "S21-0001",
            "zone_id": "C-001",
            "drawing_number": "S21-0001",
            "change_type": "modified",
            "severity": "high",
            "raw_change_count": "5",
            "bbox_min_x": "455.5",
            "bbox_min_y": "2660.0",
            "bbox_max_x": "566.0",
            "bbox_max_y": "3137.0",
            "source_a": str(before),
            "source_b": str(after),
            "bbox_coordinate_space": "image_pixels",
            "pdf_dpi": "200",
        },
    ]
    with (artifact_dir / "change_zones.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    def fake_render_pdf_to_png(
        _pdf_path: Path,
        output_path: Path,
        *,
        dpi: int,
        max_edge_px: int,
        page_index: int = 0,
    ) -> dict:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (936, 1323), "white").save(output_path)
        return {
            "min_x": 0.0,
            "min_y": 0.0,
            "max_x": 936.0,
            "max_y": 1323.0,
            "img_width": 936,
            "img_height": 1323,
            "scale_x": 1.0,
            "scale_y": 1.0,
            "coordinate_space": "image_pixels",
            "page": int(page_index),
            "dpi": 80.0,
            "pdf_dpi": 80.0,
            "effective_dpi": 80.0,
            "requested_dpi": float(dpi),
        }

    monkeypatch.setattr(
        viewer_package_module,
        "_render_pdf_to_png",
        fake_render_pdf_to_png,
    )

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        review_dashboard=artifact_dir / "review_dashboard.json",
        render_policy="top-issues",
        preview_dpi=150,
    )

    overlay = json.loads((tmp_path / "viewer" / "overlays" / "S21-0001.json").read_text(encoding="utf-8"))
    first = overlay["overlays"][0]
    assert first["pdf_dpi"] == 200
    assert first["after_bbox_px"] == pytest.approx(
        {"x": 182.2, "y": 1064.0, "width": 44.2, "height": 190.8},
        abs=0.01,
    )


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
    perf_path = tmp_path / "viewer" / "viewer_perf.jsonl"
    assert package.tile_count == 0
    assert manifest["build_lod_tiles"] is False
    assert manifest["pairs"][0]["lod_tile_count"] == 0
    assert manifest["pairs"][0]["overlay_tile_count"] == 0
    events = [
        json.loads(line)
        for line in perf_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(event["event"] == "package_background_render" for event in events)
    assert not any(event["event"] == "package_tile_write" for event in events)


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
    assert result["worker_spawned"] is True
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


def test_sheet_frame_bboxes_annotate_transforms_without_world_bbox_fallback() -> None:
    before_transform = {
        "min_x": -1000.0,
        "min_y": -1000.0,
        "max_x": 1000.0,
        "max_y": 1000.0,
        "img_width": 200,
        "img_height": 200,
    }
    after_transform = dict(before_transform)
    annotated_before, annotated_after, before_frame, after_frame = (
        viewer_package_module._attach_sheet_frame_bboxes_to_transforms(
            rows=[
                {
                    "before_cad_frame_bbox": "0,0,420,297",
                    "after_cad_frame_bbox": "1000,2000,1420,2297",
                }
            ],
            artifact={},
            before_transform=before_transform,
            after_transform=after_transform,
        )
    )

    assert before_frame == (0.0, 0.0, 420.0, 297.0)
    assert after_frame == (1000.0, 2000.0, 1420.0, 2297.0)
    assert annotated_before["sheet_frame_bbox"] == [0.0, 0.0, 420.0, 297.0]
    assert annotated_before["cad_frame_bbox"] == [0.0, 0.0, 420.0, 297.0]
    assert annotated_after["sheet_frame_bbox"] == [1000.0, 2000.0, 1420.0, 2297.0]

    world_before, world_after, world_before_frame, world_after_frame = (
        viewer_package_module._attach_sheet_frame_bboxes_to_transforms(
            rows=[{"world_bbox": "0,0,9999,9999", "cad_world_bbox": "0,0,9999,9999"}],
            artifact={},
            before_transform=before_transform,
            after_transform=after_transform,
        )
    )

    assert world_before_frame is None
    assert world_after_frame is None
    assert "sheet_frame_bbox" not in world_before
    assert "sheet_frame_bbox" not in world_after


def test_viewer_package_propagates_sheet_frame_bboxes_to_cad_pair_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    Image = pytest.importorskip("PIL.Image")
    old_dxf = tmp_path / "old.dxf"
    new_dxf = tmp_path / "new.dxf"
    old_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    new_dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    artifact_dir = _write_base_artifacts(tmp_path, source_a=str(old_dxf), source_b=str(new_dxf))
    zones_path = artifact_dir / "change_zones.csv"
    rows = list(csv.DictReader(zones_path.open("r", encoding="utf-8-sig")))
    for row in rows:
        row["before_cad_frame_bbox"] = "0,0,420,297"
        row["after_cad_frame_bbox"] = "1000,2000,1420,2297"
    with zones_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    def fake_ensure(path: Path, _cache_dir: Path) -> Path:
        return path

    def fake_render(_dxf: Path, image_path: Path, *, dpi: int, max_edge_px: int):
        Image.new("RGB", (200, 200), "white").save(image_path)
        return {
            "min_x": -500.0,
            "min_y": -500.0,
            "max_x": 1500.0,
            "max_y": 1500.0,
            "scale_x": 0.1,
            "scale_y": 0.1,
            "img_width": 200,
            "img_height": 200,
        }

    monkeypatch.setattr("src.services.comparison.viewer_package._ensure_preview_dxf", fake_ensure)
    monkeypatch.setattr("src.services.comparison.viewer_package._render_dxf_to_png", fake_render)

    export_viewer_package(
        artifact_dir,
        tmp_path / "viewer",
        render_policy="all",
        max_zone_tiles=0,
    )

    manifest = json.loads((tmp_path / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    pair = manifest["pairs"][0]
    assert pair["before_cad_frame_bbox"] == [0.0, 0.0, 420.0, 297.0]
    assert pair["after_cad_frame_bbox"] == [1000.0, 2000.0, 1420.0, 2297.0]
    assert pair["before_transform"]["sheet_frame_bbox"] == [0.0, 0.0, 420.0, 297.0]
    assert pair["after_transform"]["sheet_frame_bbox"] == [1000.0, 2000.0, 1420.0, 2297.0]

    overlay = json.loads((tmp_path / "viewer" / "overlays" / "S21-0001.json").read_text(encoding="utf-8"))
    assert overlay["before_cad_frame_bbox"] == [0.0, 0.0, 420.0, 297.0]
    assert overlay["after_cad_frame_bbox"] == [1000.0, 2000.0, 1420.0, 2297.0]


def test_v3_manifest_carries_real_pair_uuid_and_no_inline_packs(tmp_path: Path) -> None:
    """2026-06-12 contract: pair_uuid must be the REAL pair id (the old
    "viewer-package" literal made every GUI ViewerSession lookup miss, so
    lazy pack builds never ran — live 115 MB pair stuck in overlay-only).
    Pack refs stay None in the manifest: packs live in the GLOBAL cache
    (lazy build + pipeline detached prewarm), because baking them here runs
    in the isolated proxy process where each side costs a cold multi-minute
    parse and the sharable redaction masks ref paths anyway."""

    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    before.write_text("0\nEOF\n", encoding="utf-8")
    after.write_text("0\nEOF\n", encoding="utf-8")

    manifest = _build_v3_manifest_from_v1(
        v1_manifest={
            "schema_version": 2,
            "pairs": [
                {
                    "pair_id": "pair_realhash77",
                    "source_a": str(before),
                    "source_b": str(after),
                    "coordinate_source": "cad_world",
                }
            ],
        },
        options=ViewerPackageOptions(),
        viewer_root=tmp_path / "viewer",
    )

    assert manifest.pair_uuid == "pair_realhash77"
    assert manifest.before_scene_pack is None
    assert manifest.after_scene_pack is None
    assert manifest.renderer_capabilities["scene_pack_built"] is False


def test_v3_manifest_pair_uuid_falls_back_to_literal_only_without_pairs(tmp_path: Path) -> None:
    manifest = _build_v3_manifest_from_v1(
        v1_manifest={"schema_version": 2, "pairs": []},
        options=ViewerPackageOptions(),
        viewer_root=tmp_path / "viewer",
    )
    assert manifest.pair_uuid == "viewer-package"
