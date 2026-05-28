# -*- coding: utf-8 -*-
"""Tests for PDF-first visual asset provenance manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.cad_visual_backend import CadVisualConversionResult
from src.services.comparison.visual_asset import (
    VisualAssetManifest,
    VisualAssetManifestValidationError,
    build_visual_asset_cache_key,
    probe_visual_asset_nonblank,
    read_visual_asset_manifest,
    validate_visual_asset_policy,
    write_visual_asset_manifest,
)


def test_visual_asset_manifest_round_trips_backend_provenance(tmp_path: Path) -> None:
    source = tmp_path / "drawing.dwg"
    asset = tmp_path / "drawing.pdf"
    source.write_bytes(b"dwg")
    asset.write_bytes(b"%PDF-1.4\n")
    manifest = VisualAssetManifest(
        visual_asset_id="asset-1",
        source_path=str(source),
        asset_path=str(asset),
        asset_kind="cad_to_pdf",
        source_hash="source-hash",
        visual_backend_id="qcad_professional_cli",
        visual_backend_version="3.30.1",
        visual_backend_license_id="commercial-user-provided",
        visual_fidelity="pdf_visual_background",
        transform_quality="estimated",
    )

    payload = manifest.to_dict()
    json.dumps(payload)
    loaded = VisualAssetManifest.from_dict(payload)

    assert loaded.visual_backend_id == "qcad_professional_cli"
    assert loaded.visual_backend_version == "3.30.1"
    assert loaded.visual_backend_license_id == "commercial-user-provided"
    assert loaded.coordinate_contract_version


def test_visual_asset_cache_key_changes_with_backend_profile_and_dpi() -> None:
    base = build_visual_asset_cache_key(
        source_hash="source-hash",
        backend_id="source_pdf",
        backend_version="1",
        license_id="user-provided",
        plot_profile_hash="profile-a",
        layout_name="Layout1",
        page_index=0,
        dpi=150,
        page_size_pt=[595.0, 842.0],
        pixel_size=[1240, 1754],
        transform_quality="estimated",
    )
    changed_dpi = build_visual_asset_cache_key(
        source_hash="source-hash",
        backend_id="source_pdf",
        backend_version="1",
        license_id="user-provided",
        plot_profile_hash="profile-a",
        layout_name="Layout1",
        page_index=0,
        dpi=300,
        page_size_pt=[595.0, 842.0],
        pixel_size=[1240, 1754],
        transform_quality="estimated",
    )
    changed_profile = build_visual_asset_cache_key(
        source_hash="source-hash",
        backend_id="source_pdf",
        backend_version="1",
        license_id="user-provided",
        plot_profile_hash="profile-b",
        layout_name="Layout1",
        page_index=0,
        dpi=150,
        page_size_pt=[595.0, 842.0],
        pixel_size=[1240, 1754],
        transform_quality="estimated",
    )
    changed_page_size = build_visual_asset_cache_key(
        source_hash="source-hash",
        backend_id="source_pdf",
        backend_version="1",
        license_id="user-provided",
        plot_profile_hash="profile-a",
        layout_name="Layout1",
        page_index=0,
        dpi=150,
        page_size_pt=[612.0, 792.0],
        pixel_size=[1240, 1754],
        transform_quality="estimated",
    )
    changed_pixel_size = build_visual_asset_cache_key(
        source_hash="source-hash",
        backend_id="source_pdf",
        backend_version="1",
        license_id="user-provided",
        plot_profile_hash="profile-a",
        layout_name="Layout1",
        page_index=0,
        dpi=150,
        page_size_pt=[595.0, 842.0],
        pixel_size=[1280, 1814],
        transform_quality="estimated",
    )
    changed_quality = build_visual_asset_cache_key(
        source_hash="source-hash",
        backend_id="source_pdf",
        backend_version="1",
        license_id="user-provided",
        plot_profile_hash="profile-a",
        layout_name="Layout1",
        page_index=0,
        dpi=150,
        page_size_pt=[595.0, 842.0],
        pixel_size=[1240, 1754],
        transform_quality="relative_only",
    )

    assert len(base) == 64
    assert base != changed_dpi
    assert base != changed_profile
    assert base != changed_page_size
    assert base != changed_pixel_size
    assert base != changed_quality


def test_visual_asset_manifest_requires_backend_license_id() -> None:
    with pytest.raises(VisualAssetManifestValidationError, match="visual_backend_license_id"):
        VisualAssetManifest(
            visual_asset_id="asset-1",
            source_path="drawing.dwg",
            asset_path="drawing.pdf",
            asset_kind="cad_to_pdf",
            visual_backend_id="qcad_professional_cli",
        )


def test_visual_asset_manifest_from_conversion_result(tmp_path: Path) -> None:
    source = tmp_path / "source.dxf"
    output = tmp_path / "out" / "source.pdf"
    source.write_text("0\nEOF\n", encoding="utf-8")
    output.parent.mkdir()
    output.write_bytes(b"%PDF-1.4\n")
    result = CadVisualConversionResult(
        status="converted",
        reason_code="",
        source_path=str(source),
        output_path=str(output),
        output_format="pdf",
        backend_id="fake_pdf",
        backend_version="1",
        license_id="test-license",
    )

    manifest = VisualAssetManifest.from_conversion_result(result)

    assert manifest.asset_kind == "cad_to_pdf"
    assert manifest.status == "ready"
    assert manifest.visual_fidelity == "pdf_visual_background"
    assert manifest.visual_backend_license_id == "test-license"
    assert manifest.source_hash
    assert manifest.cache_key_hash


def test_customer_grade_visual_asset_policy_accepts_complete_source_pdf(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    manifest = VisualAssetManifest(
        visual_asset_id="source-pdf-1",
        source_path=str(source),
        asset_path=str(source),
        asset_kind="source_pdf",
        source_hash="source-hash",
        source_signature={"source_hash": "source-hash", "schema_version": "1"},
        cache_key_hash=build_visual_asset_cache_key(
            source_hash="source-hash",
            source_signature={"source_hash": "source-hash", "schema_version": "1"},
            backend_id="source_pdf",
            backend_version="1",
            license_id="user-provided",
            page_index=0,
            dpi=150,
            page_size_pt=[595.0, 842.0],
            transform_quality="exact",
        ),
        page_size_pt=[595.0, 842.0],
        dpi=150,
        visual_backend_id="source_pdf",
        visual_backend_version="1",
        visual_backend_license_id="user-provided",
        visual_fidelity="pdf_visual_background",
        transform_quality="exact",
        nonblank_probe_status="passed",
        metadata={
            "nonblank_probe": "visual_assets/source_pdf/nonblank_probe.json",
            "nonblank_probe_hash": "probe-hash",
            "probe_target_hash": "target-hash",
            "probe_method": "pixel_nonblank_probe",
        },
    )

    assert validate_visual_asset_policy(manifest, customer_grade=True) == []


def test_customer_grade_visual_asset_policy_accepts_complete_sidecar_pdf(tmp_path: Path) -> None:
    source = tmp_path / "sidecar.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    source_signature = {"source_hash": "sidecar-hash", "schema_version": "1"}
    manifest = VisualAssetManifest(
        visual_asset_id="sidecar-pdf-1",
        source_path=str(source),
        asset_path=str(source),
        asset_kind="sidecar_pdf",
        source_hash="sidecar-hash",
        source_signature=source_signature,
        cache_key_hash=build_visual_asset_cache_key(
            source_hash="sidecar-hash",
            source_signature=source_signature,
            backend_id="sidecar_pdf",
            backend_version="1",
            license_id="user-provided",
            page_index=0,
            dpi=150,
            page_size_pt=[612.0, 792.0],
            transform_quality="estimated",
        ),
        page_size_pt=[612.0, 792.0],
        dpi=150,
        visual_backend_id="sidecar_pdf",
        visual_backend_version="1",
        visual_backend_license_id="user-provided",
        visual_fidelity="pdf_visual_background",
        transform_quality="estimated",
        nonblank_probe_status="passed",
        metadata={
            "nonblank_probe": "visual_assets/sidecar_pdf/nonblank_probe.json",
            "nonblank_probe_hash": "probe-hash",
            "probe_target_hash": "target-hash",
            "probe_method": "pixel_nonblank_probe",
        },
    )

    assert validate_visual_asset_policy(manifest, customer_grade=True) == []


def test_customer_grade_visual_asset_policy_rejects_incomplete_cad_to_pdf(tmp_path: Path) -> None:
    source = tmp_path / "drawing.dwg"
    output = tmp_path / "drawing.pdf"
    source.write_bytes(b"dwg")
    output.write_bytes(b"%PDF-1.4\n")
    manifest = VisualAssetManifest(
        visual_asset_id="cad-pdf-1",
        source_path=str(source),
        asset_path=str(output),
        asset_kind="cad_to_pdf",
        source_hash="source-hash",
        visual_backend_id="qcad_professional_cli",
        visual_backend_version="3.30.1",
        visual_backend_license_id="commercial-user-provided",
        transform_quality="estimated",
        nonblank_probe_status="failed",
        conversion_invoked_from_hot_path=True,
        metadata={"exact_overlay_allowed": True},
    )

    issues = validate_visual_asset_policy(manifest, customer_grade=True)

    assert "cache_key_hash is required" in issues
    assert "nonblank_probe_status must be passed" in issues
    assert "plot_profile_hash is required for CAD visual conversion" in issues
    assert "CAD visual conversion must not run in the GUI or viewer hot path" in issues
    assert "exact overlay cannot be allowed when transform_quality is not exact" in issues


def test_customer_grade_visual_asset_policy_rejects_stale_cache_key(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    manifest = VisualAssetManifest(
        visual_asset_id="source-pdf-1",
        source_path=str(source),
        asset_path=str(source),
        asset_kind="source_pdf",
        source_hash="source-hash",
        cache_key_hash="not-the-derived-key",
        page_size_pt=[595.0, 842.0],
        dpi=150,
        visual_backend_id="source_pdf",
        visual_backend_version="1",
        visual_backend_license_id="user-provided",
        transform_quality="exact",
        nonblank_probe_status="passed",
    )

    issues = validate_visual_asset_policy(manifest, customer_grade=True)

    assert "cache_key_hash does not match visual asset provenance fields" in issues


def test_customer_grade_visual_asset_policy_rejects_cache_key_with_old_transform_quality(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    source_signature = {"source_hash": "source-hash", "schema_version": "1"}
    stale_key = build_visual_asset_cache_key(
        source_hash="source-hash",
        source_signature=source_signature,
        backend_id="source_pdf",
        backend_version="1",
        license_id="user-provided",
        page_index=0,
        dpi=150,
        page_size_pt=[595.0, 842.0],
        pixel_size=[1240, 1754],
        transform_quality="relative_only",
    )
    manifest = VisualAssetManifest(
        visual_asset_id="source-pdf-1",
        source_path=str(source),
        asset_path=str(source),
        asset_kind="source_pdf",
        source_hash="source-hash",
        source_signature=source_signature,
        cache_key_hash=stale_key,
        page_size_pt=[595.0, 842.0],
        pixel_size=[1240, 1754],
        dpi=150,
        visual_backend_id="source_pdf",
        visual_backend_version="1",
        visual_backend_license_id="user-provided",
        transform_quality="estimated",
        nonblank_probe_status="passed",
        metadata={
            "nonblank_probe": "visual_assets/source_pdf/nonblank_probe.json",
            "nonblank_probe_hash": "probe-hash",
            "probe_target_hash": "target-hash",
            "probe_method": "pixel_nonblank_probe",
        },
    )

    issues = validate_visual_asset_policy(manifest, customer_grade=True)

    assert "cache_key_hash does not match visual asset provenance fields" in issues


def test_customer_grade_visual_asset_policy_rejects_unknown_asset_kind() -> None:
    issues = validate_visual_asset_policy(
        {
            "visual_asset_id": "asset-1",
            "asset_kind": "mystery_renderer",
            "status": "ready",
        },
        customer_grade=True,
    )

    assert "unsupported asset_kind: mystery_renderer" in issues


def test_visual_asset_manifest_file_io(tmp_path: Path) -> None:
    manifest = VisualAssetManifest(
        visual_asset_id="source-pdf-1",
        source_path=str(tmp_path / "source.pdf"),
        asset_path=str(tmp_path / "source.pdf"),
        asset_kind="source_pdf",
        visual_backend_id="source_pdf",
        visual_backend_version="1",
        visual_backend_license_id="user-provided",
    )
    path = write_visual_asset_manifest(tmp_path / "visual_asset.json", manifest)

    loaded = read_visual_asset_manifest(path)

    assert loaded.visual_asset_id == "source-pdf-1"
    assert loaded.visual_backend_license_id == "user-provided"


def test_visual_asset_nonblank_probe_passes_for_pixel_content(tmp_path: Path) -> None:
    Image = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "rendered.png"
    image = Image.new("RGB", (24, 24), "white")
    for x in range(4, 20):
        image.putpixel((x, 12), (0, 0, 0))
    image.save(image_path)

    probe = probe_visual_asset_nonblank(
        asset_path=image_path,
        source_hash="source-hash",
        cache_key_hash="cache-key",
        page_index=2,
        dpi=72,
    )

    assert probe["status"] == "passed"
    assert probe["method"] == "pixel_nonblank_probe"
    assert probe["nonblank"] is True
    assert probe["probe_target_hash"]
    assert probe["probe_hash"]
    assert probe["page_index"] == 2


def test_visual_asset_nonblank_probe_fails_for_blank_white_image(tmp_path: Path) -> None:
    Image = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "blank.png"
    Image.new("RGB", (12, 12), "white").save(image_path)

    probe = probe_visual_asset_nonblank(asset_path=image_path)

    assert probe["status"] == "failed"
    assert probe["reason_code"] == "blank_or_near_white"
    assert probe["nonblank"] is False


def test_visual_asset_nonblank_probe_can_use_rendered_target_for_pdf_asset(tmp_path: Path) -> None:
    Image = pytest.importorskip("PIL.Image")
    pdf_path = tmp_path / "source.pdf"
    render_path = tmp_path / "source_page.png"
    pdf_path.write_bytes(b"%PDF-1.4\n% source\n%%EOF\n")
    image = Image.new("RGB", (16, 16), "white")
    image.putpixel((8, 8), (0, 0, 0))
    image.save(render_path)

    probe = probe_visual_asset_nonblank(
        asset_path=pdf_path,
        probe_target_path=render_path,
        source_hash="source-hash",
    )

    assert probe["status"] == "passed"
    assert probe["asset_path"] == str(pdf_path)
    assert probe["probe_target_path"] == str(render_path)
    assert probe["asset_hash"] != probe["probe_target_hash"]


def test_visual_asset_nonblank_probe_does_not_open_pdf_without_rendered_target(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% source\n%%EOF\n")

    probe = probe_visual_asset_nonblank(asset_path=pdf_path)

    assert probe["status"] == "not_probed"
    assert probe["method"] == "pixel_nonblank_probe_unavailable"
    assert probe["reason_code"] == "probe_target_required"
    assert probe["probe_target_hash"] == ""


def test_customer_grade_visual_asset_policy_requires_probe_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    manifest = VisualAssetManifest(
        visual_asset_id="source-pdf-1",
        source_path=str(source),
        asset_path=str(source),
        asset_kind="source_pdf",
        source_hash="source-hash",
        cache_key_hash=build_visual_asset_cache_key(
            source_hash="source-hash",
            backend_id="source_pdf",
            backend_version="1",
            license_id="user-provided",
            page_index=0,
            dpi=150,
            page_size_pt=[595.0, 842.0],
            transform_quality="estimated",
        ),
        page_size_pt=[595.0, 842.0],
        dpi=150,
        visual_backend_id="source_pdf",
        visual_backend_version="1",
        visual_backend_license_id="user-provided",
        transform_quality="estimated",
        nonblank_probe_status="passed",
    )

    issues = validate_visual_asset_policy(manifest, customer_grade=True)

    assert "metadata.nonblank_probe is required when nonblank probe passed" in issues
    assert "metadata.nonblank_probe_hash is required when nonblank probe passed" in issues
    assert "metadata.probe_target_hash is required when nonblank probe passed" in issues
    assert "metadata.probe_method must be pixel_nonblank_probe when nonblank probe passed" in issues
