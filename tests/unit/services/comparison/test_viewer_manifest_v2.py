# -*- coding: utf-8 -*-
"""Unit tests for the viewer_manifest v2 schema (Phase F P0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.transform import fit_world_to_pixels
from src.services.comparison.viewer_manifest_v2 import (
    IDENTITY_AFFINE,
    SCHEMA_VERSION,
    ArtifactRef,
    ManifestValidationError,
    PairEntry,
    ViewerManifestV2,
    is_v2_manifest,
    load_manifest_v2,
    write_manifest_v2,
)


def _make_manifest(pair_id: str = "p1") -> ViewerManifestV2:
    return ViewerManifestV2(
        pair_uuid="uuid-1",
        package_version="1.0",
        source_kind="normalized_dxf",
        before_world_bbox=(0.0, 0.0, 100.0, 100.0),
        after_world_bbox=(0.0, 0.0, 100.0, 100.0),
        shared_world_bbox=(0.0, 0.0, 100.0, 100.0),
        pairs=[PairEntry(pair_id=pair_id)],
    )


def test_round_trip_minimal_manifest(tmp_path: Path) -> None:
    m = _make_manifest()
    path = tmp_path / "viewer_manifest.json"
    write_manifest_v2(path, m)
    assert path.exists()
    loaded = load_manifest_v2(path)
    assert loaded.schema_version == SCHEMA_VERSION
    assert loaded.pair_uuid == "uuid-1"
    assert len(loaded.pairs) == 1
    assert loaded.pairs[0].pair_id == "p1"
    assert loaded.pairs[0].background_fidelity == "relative_only"
    assert loaded.pairs[0].render_job_status == "idle"


def test_artifact_ref_from_affine_params_carries_quality() -> None:
    params = fit_world_to_pixels((0.0, 0.0, 100.0, 100.0), (200, 200))
    ref = ArtifactRef.from_affine_params(
        image_uri="before.png",
        params=params,
        renderer_id="ezdxf-crop",
        renderer_version="1.4.3",
    )
    assert ref.transform_quality == "exact"
    assert ref.world_bbox == (0.0, 0.0, 100.0, 100.0)
    assert ref.pixel_size == (200, 200)
    assert len(ref.world_to_pixel) == 6
    assert len(ref.pixel_to_world) == 6
    assert ref.bbox_coordinate_space == "cad_wcs_mm"
    assert ref.source_truth == "cad_entity"
    assert ref.y_axis == "up"


def test_artifact_ref_to_dict_round_trip() -> None:
    params = fit_world_to_pixels(
        (0.0, 0.0, 50.0, 50.0),
        (100, 100),
        quality="estimated",
        coordinate_space="image_pixels",
    )
    ref = ArtifactRef.from_affine_params(image_uri="x.png", params=params)
    payload = ref.to_dict()
    assert payload["transform_quality"] == "estimated"
    assert payload["coordinate_contract_version"] == "coordinate_contract.v1"
    assert payload["bbox_coordinate_space"] == "image_pixels_tl"
    assert payload["source_truth"] == "pdf_visual"
    assert payload["y_axis"] == "down"
    assert payload["world_bbox"] == [0.0, 0.0, 50.0, 50.0]
    rebuilt = ArtifactRef.from_dict(payload)
    assert rebuilt.transform_quality == "estimated"
    assert rebuilt.bbox_coordinate_space == "image_pixels_tl"
    assert rebuilt.source_truth == "pdf_visual"
    assert rebuilt.y_axis == "down"
    assert rebuilt.world_bbox == (0.0, 0.0, 50.0, 50.0)


def test_artifact_ref_from_old_payload_defaults_coordinate_contract() -> None:
    rebuilt = ArtifactRef.from_dict({
        "image_uri": "legacy.png",
        "world_bbox": [0.0, 0.0, 10.0, 10.0],
        "pixel_size": [100, 100],
        "transform_quality": "exact",
    })

    assert rebuilt.coordinate_contract_version == "coordinate_contract.v1"
    assert rebuilt.bbox_coordinate_space == "cad_wcs_mm"
    assert rebuilt.source_truth == "cad_entity"
    assert rebuilt.y_axis == "up"


def test_artifact_ref_rejects_unknown_transform_quality() -> None:
    with pytest.raises(ManifestValidationError, match="transform_quality"):
        ArtifactRef.from_dict({
            "image_uri": "x.png",
            "transform_quality": "made_up_value",
        })


def test_pair_entry_rejects_unknown_fidelity() -> None:
    with pytest.raises(ManifestValidationError, match="background_fidelity"):
        PairEntry(pair_id="p", background_fidelity="ultra_exact")  # type: ignore[arg-type]


def test_pair_entry_rejects_unknown_job_status() -> None:
    with pytest.raises(ManifestValidationError, match="render_job_status"):
        PairEntry(pair_id="p", render_job_status="cooking")  # type: ignore[arg-type]


def test_manifest_rejects_unknown_source_kind() -> None:
    with pytest.raises(ManifestValidationError, match="source_kind"):
        ViewerManifestV2(
            pair_uuid="u",
            package_version="1.0",
            source_kind="unsupported",  # type: ignore[arg-type]
        )


def test_manifest_rejects_unknown_overlay_space() -> None:
    with pytest.raises(ManifestValidationError, match="overlay_space"):
        ViewerManifestV2(
            pair_uuid="u",
            package_version="1.0",
            source_kind="normalized_dxf",
            overlay_space="floating",  # type: ignore[arg-type]
        )


def test_load_rejects_v1_manifest(tmp_path: Path) -> None:
    """A v1 manifest (or anything missing the v2 schema_version) must fail loud."""

    p = tmp_path / "viewer_manifest.json"
    p.write_text(
        json.dumps({"schema_version": "viewer_manifest.v1", "pair_uuid": "u"}),
        encoding="utf-8",
    )
    with pytest.raises(ManifestValidationError, match="schema_version"):
        load_manifest_v2(p)


def test_load_rejects_corrupt_json(tmp_path: Path) -> None:
    p = tmp_path / "viewer_manifest.json"
    p.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="Cannot read"):
        load_manifest_v2(p)


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ManifestValidationError, match="not found"):
        load_manifest_v2(tmp_path / "absent.json")


def test_is_v2_manifest_returns_false_for_v1(tmp_path: Path) -> None:
    p = tmp_path / "viewer_manifest.json"
    p.write_text(json.dumps({"schema_version": "viewer_manifest.v1"}), encoding="utf-8")
    assert not is_v2_manifest(p)


def test_is_v2_manifest_returns_false_for_missing(tmp_path: Path) -> None:
    assert not is_v2_manifest(tmp_path / "nope.json")


def test_is_v2_manifest_returns_true_for_v2(tmp_path: Path) -> None:
    p = tmp_path / "viewer_manifest.json"
    write_manifest_v2(p, _make_manifest())
    assert is_v2_manifest(p)


def test_atomic_write_does_not_leave_tmp_file(tmp_path: Path) -> None:
    p = tmp_path / "viewer_manifest.json"
    write_manifest_v2(p, _make_manifest())
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_pair_entry_with_artifact_refs_round_trip(tmp_path: Path) -> None:
    params = fit_world_to_pixels((0.0, 0.0, 100.0, 200.0), (200, 400))
    before_ref = ArtifactRef.from_affine_params(image_uri="before.png", params=params)
    after_ref = ArtifactRef.from_affine_params(image_uri="after.png", params=params)
    m = ViewerManifestV2(
        pair_uuid="uuid-1",
        package_version="1.0",
        source_kind="normalized_dxf",
        before_world_bbox=(0.0, 0.0, 100.0, 200.0),
        after_world_bbox=(0.0, 0.0, 100.0, 200.0),
        shared_world_bbox=(0.0, 0.0, 100.0, 200.0),
        pairs=[
            PairEntry(
                pair_id="p1",
                background_fidelity="exact_world_render",
                render_job_status="idle",
                before=before_ref,
                after=after_ref,
            )
        ],
    )
    path = tmp_path / "viewer_manifest.json"
    write_manifest_v2(path, m)
    loaded = load_manifest_v2(path)
    assert loaded.pairs[0].background_fidelity == "exact_world_render"
    assert loaded.pairs[0].before is not None
    assert loaded.pairs[0].before.image_uri == "before.png"
    assert loaded.pairs[0].before.world_bbox == (0.0, 0.0, 100.0, 200.0)
    assert loaded.pairs[0].after is not None
    assert loaded.pairs[0].after.transform_quality == "exact"


def test_manifest_auto_populates_created_at_when_blank() -> None:
    m = ViewerManifestV2(
        pair_uuid="u", package_version="1.0", source_kind="normalized_dxf"
    )
    assert m.created_at_utc  # not empty
    assert "T" in m.created_at_utc or " " in m.created_at_utc  # ISO-ish


def test_relative_only_fidelity_with_identity_affine_is_valid() -> None:
    """The most common 'no exact background' case should round-trip cleanly."""

    ref = ArtifactRef(
        image_uri="placeholder.png",
        world_bbox=(0.0, 0.0, 1.0, 1.0),
        pixel_size=(1024, 1024),
        world_to_pixel=IDENTITY_AFFINE,
        pixel_to_world=IDENTITY_AFFINE,
        transform_quality="relative_only",
    )
    payload = ref.to_dict()
    rebuilt = ArtifactRef.from_dict(payload)
    assert rebuilt.transform_quality == "relative_only"


# ---------------------------------------------------------------------------
# Phase F P0 — viewer_package v1→v2 translation
# ---------------------------------------------------------------------------


def test_v1_to_v2_translation_cad_render_pair() -> None:
    """A v1 CAD pair with after_transform should map to exact_world_render."""

    from src.services.comparison.viewer_package import (
        ViewerPackageOptions,
        _build_v2_manifest_from_v1,
    )

    v1 = {
        "schema_version": 2,
        "viewer_engine": "auto",
        "pairs": [
            {
                "pair_id": "p1",
                "coordinate_source": "cad_world",
                "visual_fidelity": "cad_render",
                "render_lifecycle": "ready",
                "render_status": "rendered",
                "before_image": "/tmp/before.png",
                "after_image": "/tmp/after.png",
                "before_transform": {
                    "world_bbox": [0.0, 0.0, 1000.0, 800.0],
                    "pixel_size": [1200, 960],
                },
                "after_transform": {
                    "world_bbox": [0.0, 0.0, 1000.0, 800.0],
                    "pixel_size": [1200, 960],
                },
            }
        ],
    }
    out = _build_v2_manifest_from_v1(
        v1_manifest=v1,
        options=ViewerPackageOptions(),
    )
    assert out.source_kind == "normalized_dxf"
    assert out.overlay_space == "world"
    assert len(out.pairs) == 1
    p = out.pairs[0]
    assert p.background_fidelity == "exact_world_render"
    assert p.render_job_status == "idle"
    assert p.before is not None and p.after is not None
    assert p.before.transform_quality == "exact"
    assert p.after.transform_quality == "exact"
    assert p.after.world_bbox == (0.0, 0.0, 1000.0, 800.0)


def test_v1_to_v2_translation_pdf_pair_image_pixels() -> None:
    """PDF pairs (coordinate_source=image_pixels) should map to PDF source_kind."""

    from src.services.comparison.viewer_package import (
        ViewerPackageOptions,
        _build_v2_manifest_from_v1,
    )

    v1 = {
        "schema_version": 2,
        "pairs": [
            {
                "pair_id": "pdf-1",
                "coordinate_source": "image_pixels",
                "visual_fidelity": "pdf_render",
                "render_status": "rendered",
                "before_image": "/tmp/b.png",
                "after_image": "/tmp/a.png",
                "before_transform": {
                    "pdf_page_size": {"width": 612.0, "height": 792.0},
                    "pixel_size": [800, 1024],
                },
                "after_transform": {
                    "pdf_page_size": {"width": 612.0, "height": 792.0},
                    "pixel_size": [800, 1024],
                },
            }
        ],
    }
    out = _build_v2_manifest_from_v1(
        v1_manifest=v1, options=ViewerPackageOptions()
    )
    assert out.source_kind == "pdf"
    assert out.pairs[0].background_fidelity == "exact_world_tile_sparse"
    assert out.pairs[0].after is not None
    assert out.pairs[0].after.bbox_coordinate_space == "pdf_page_points_bl"
    assert out.pairs[0].after.source_truth == "pdf_visual"
    assert out.pairs[0].after.y_axis == "up"


def test_v1_to_v2_translation_relative_overlay_forces_relative_only() -> None:
    """Pairs without after_transform must downgrade to relative_only."""

    from src.services.comparison.viewer_package import (
        ViewerPackageOptions,
        _build_v2_manifest_from_v1,
    )

    v1 = {
        "schema_version": 2,
        "pairs": [
            {
                "pair_id": "p-lazy",
                "coordinate_source": "cad_world",
                "visual_fidelity": "cad_render",   # claims exact, but...
                "render_status": "lazy_not_rendered",
                "before_image": "",
                "after_image": "",
                "before_transform": None,
                "after_transform": None,           # ...no transform → forced relative
            }
        ],
    }
    out = _build_v2_manifest_from_v1(v1_manifest=v1, options=ViewerPackageOptions())
    p = out.pairs[0]
    assert p.background_fidelity == "relative_only"
    assert p.render_job_status == "idle"
    assert p.before is None and p.after is None


def test_v1_to_v2_translation_render_failed_maps_to_failed() -> None:
    from src.services.comparison.viewer_package import (
        ViewerPackageOptions,
        _build_v2_manifest_from_v1,
    )

    v1 = {
        "schema_version": 2,
        "pairs": [{
            "pair_id": "p-fail",
            "coordinate_source": "cad_world",
            "visual_fidelity": "cad_render",
            "render_status": "render_failed",
            "render_warning": "matplotlib backend crashed",
        }],
    }
    out = _build_v2_manifest_from_v1(v1_manifest=v1, options=ViewerPackageOptions())
    assert out.pairs[0].render_job_status == "failed"
    assert "matplotlib" in out.pairs[0].notes


def test_v1_to_v2_translation_render_timeout_maps_to_timed_out() -> None:
    from src.services.comparison.viewer_package import (
        ViewerPackageOptions,
        _build_v2_manifest_from_v1,
    )

    v1 = {
        "schema_version": 2,
        "pairs": [{
            "pair_id": "p-timeout",
            "coordinate_source": "cad_world",
            "render_status": "render_timeout",
        }],
    }
    out = _build_v2_manifest_from_v1(v1_manifest=v1, options=ViewerPackageOptions())
    assert out.pairs[0].render_job_status == "timed_out"


def test_v1_to_v2_mixed_coordinate_sources() -> None:
    from src.services.comparison.viewer_package import (
        ViewerPackageOptions,
        _build_v2_manifest_from_v1,
    )

    v1 = {
        "schema_version": 2,
        "pairs": [
            {"pair_id": "cad-1", "coordinate_source": "cad_world", "visual_fidelity": "cad_render"},
            {"pair_id": "pdf-1", "coordinate_source": "image_pixels", "visual_fidelity": "pdf_render"},
        ],
    }
    out = _build_v2_manifest_from_v1(v1_manifest=v1, options=ViewerPackageOptions())
    assert out.source_kind == "mixed"
