# -*- coding: utf-8 -*-
"""Unit tests for the world↔pixel affine transform module (Phase F P0)."""

from __future__ import annotations

import math

import pytest

from src.services.comparison.transform import (
    COORDINATE_CONTRACT_VERSION,
    COORD_CAD_WCS_MM,
    COORD_IMAGE_PIXELS_TL,
    SOURCE_TRUTH_CAD_ENTITY,
    DEGENERATE_SCALE,
    AffineParams,
    SharedCamera,
    apply_affine,
    convert_bbox_to_world_space,
    coordinate_space_y_axis,
    expand_bbox,
    fit_world_to_pixels,
    normalize_coordinate_space,
    pixel_to_world,
    source_truth_for_coordinate_space,
    transform_bbox,
    union_bboxes,
    world_to_pixel,
)


def test_fit_square_world_into_square_pixel_canvas_no_padding() -> None:
    p = fit_world_to_pixels((0.0, 0.0, 100.0, 100.0), (100, 100))
    # 1 world unit per pixel.
    assert p.scale == pytest.approx(1.0)
    # Origin (0,0) world → bottom-left of canvas → pixel (0, 100) given y-flip.
    px, py = world_to_pixel(p, 0.0, 0.0)
    assert px == pytest.approx(0.0)
    assert py == pytest.approx(100.0)


def test_fit_round_trip_corner_points() -> None:
    p = fit_world_to_pixels((10.0, 20.0, 110.0, 220.0), (200, 400))
    for x_w, y_w in [(10.0, 20.0), (110.0, 220.0), (60.0, 120.0)]:
        x_p, y_p = world_to_pixel(p, x_w, y_w)
        x_w2, y_w2 = pixel_to_world(p, x_p, y_p)
        assert x_w2 == pytest.approx(x_w, abs=1e-9)
        assert y_w2 == pytest.approx(y_w, abs=1e-9)


def test_fit_preserves_aspect_ratio_when_world_wider_than_canvas() -> None:
    # World 200×100 into canvas 100×100 → scale limited by width (0.5).
    p = fit_world_to_pixels((0.0, 0.0, 200.0, 100.0), (100, 100))
    assert p.scale == pytest.approx(0.5)
    # World height (100) × scale (0.5) = 50 px → 25 px margin top+bottom.
    # World point (0, 0) → x_p = 0, y_p = h_p - eff_m_y - 0 = 100 - 25 = 75.
    px, py = world_to_pixel(p, 0.0, 0.0)
    assert px == pytest.approx(0.0, abs=1e-9)
    assert py == pytest.approx(75.0, abs=1e-9)


def test_fit_with_uniform_padding_centres_correctly() -> None:
    p = fit_world_to_pixels((0.0, 0.0, 100.0, 100.0), (200, 200), padding_px=20)
    # available area 160×160 → scale 1.6 px/unit.
    assert p.scale == pytest.approx(1.6)


def test_fit_degenerate_world_bbox_returns_zero_scale() -> None:
    p = fit_world_to_pixels((50.0, 50.0, 50.0, 50.0), (100, 100))
    assert p.scale == DEGENERATE_SCALE
    # Quality should still default to "exact" — the caller must check scale.
    assert p.quality == "exact"


def test_fit_rejects_non_positive_pixel_size() -> None:
    with pytest.raises(ValueError, match="positive"):
        fit_world_to_pixels((0.0, 0.0, 100.0, 100.0), (0, 100))
    with pytest.raises(ValueError, match="positive"):
        fit_world_to_pixels((0.0, 0.0, 100.0, 100.0), (100, -5))


def test_fit_quality_propagates_to_params() -> None:
    p = fit_world_to_pixels((0.0, 0.0, 10.0, 10.0), (100, 100), quality="estimated")
    assert p.quality == "estimated"
    p2 = fit_world_to_pixels((0.0, 0.0, 10.0, 10.0), (100, 100), quality="relative_only")
    assert p2.quality == "relative_only"


def test_apply_affine_identity() -> None:
    identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    assert apply_affine(identity, 7.5, -3.2) == (7.5, -3.2)


def test_to_manifest_dict_has_required_fields() -> None:
    p = fit_world_to_pixels((0.0, 0.0, 100.0, 100.0), (200, 200), padding_px=10)
    d = p.to_manifest_dict()
    for key in (
        "world_bbox",
        "pixel_size",
        "world_to_pixel",
        "pixel_to_world",
        "transform_quality",
        "coordinate_contract_version",
        "bbox_coordinate_space",
        "source_truth",
        "y_axis",
    ):
        assert key in d
    assert d["transform_quality"] == "exact"
    assert d["coordinate_contract_version"] == COORDINATE_CONTRACT_VERSION
    assert d["bbox_coordinate_space"] == COORD_CAD_WCS_MM
    assert d["source_truth"] == SOURCE_TRUTH_CAD_ENTITY
    assert d["y_axis"] == "up"
    assert len(d["world_to_pixel"]) == 6
    assert len(d["pixel_to_world"]) == 6


def test_coordinate_space_aliases_normalise_to_canonical_contract() -> None:
    assert normalize_coordinate_space("cad_world") == COORD_CAD_WCS_MM
    assert normalize_coordinate_space("world_xy_2d") == COORD_CAD_WCS_MM
    assert normalize_coordinate_space("image_pixels") == COORD_IMAGE_PIXELS_TL
    assert coordinate_space_y_axis("image_pixels") == "down"
    assert source_truth_for_coordinate_space("image_pixels") == "pdf_visual"


def test_fit_metadata_can_describe_pdf_image_pixels() -> None:
    p = fit_world_to_pixels(
        (0.0, 0.0, 400.0, 300.0),
        (400, 300),
        coordinate_space="image_pixels",
    )

    assert p.coordinate_space == COORD_IMAGE_PIXELS_TL
    assert p.source_truth == "pdf_visual"
    assert p.y_axis == "down"


def test_convert_bbox_to_world_space_pdf_image_pixels_flips_y() -> None:
    out = convert_bbox_to_world_space(
        [200.0, 300.0, 500.0, 600.0],
        coordinate_space="image_pixels",
        pdf_dpi=200.0,
        page_height_points=841.89,
    )

    assert out == pytest.approx((72.0, 625.89, 180.0, 733.89), abs=0.01)


def test_transform_bbox_uses_all_four_corners() -> None:
    matrix = (2.0, 1.0, 10.0, -0.5, 3.0, 20.0)
    out = transform_bbox(matrix, (0.0, 0.0, 10.0, 5.0))

    points = [
        apply_affine(matrix, 0.0, 0.0),
        apply_affine(matrix, 0.0, 5.0),
        apply_affine(matrix, 10.0, 0.0),
        apply_affine(matrix, 10.0, 5.0),
    ]
    assert out == (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )


def test_expand_bbox_grows_uniformly() -> None:
    out = expand_bbox((0.0, 0.0, 10.0, 10.0), 2.0)
    assert out == (-2.0, -2.0, 12.0, 12.0)


def test_expand_bbox_zero_padding_returns_input_tuple() -> None:
    bbox = (1.0, 2.0, 3.0, 4.0)
    assert expand_bbox(bbox, 0.0) == bbox
    assert expand_bbox(bbox, -1.0) == bbox  # negative is no-op for safety


def test_union_bboxes_combines_correctly() -> None:
    out = union_bboxes((0.0, 0.0, 10.0, 10.0), (5.0, -5.0, 15.0, 5.0))
    assert out == (0.0, -5.0, 15.0, 10.0)


def test_union_bboxes_requires_at_least_one() -> None:
    with pytest.raises(ValueError, match="at least one"):
        union_bboxes()


def test_shared_camera_from_world_bbox_centres() -> None:
    cam = SharedCamera.from_world_bbox((0.0, 0.0, 100.0, 200.0), (200, 400))
    assert cam.center_x == pytest.approx(50.0)
    assert cam.center_y == pytest.approx(100.0)
    assert cam.units_per_pixel > 0
    assert math.isfinite(cam.units_per_pixel)


def test_shared_camera_serialisation_round_trip() -> None:
    cam = SharedCamera(center_x=12.5, center_y=-3.0, units_per_pixel=0.25)
    d = cam.to_dict()
    assert d == {"center_x": 12.5, "center_y": -3.0, "units_per_pixel": 0.25}


def test_units_per_pixel_property_matches_inverse_scale() -> None:
    p = fit_world_to_pixels((0.0, 0.0, 1000.0, 1000.0), (100, 100))
    assert p.units_per_pixel == pytest.approx(10.0)


def test_pixel_round_trip_centre_point() -> None:
    """Centre of world bbox should map to centre of (padded) canvas."""

    p = fit_world_to_pixels((0.0, 0.0, 100.0, 100.0), (200, 200), padding_px=10)
    cx_w, cy_w = 50.0, 50.0
    cx_p, cy_p = world_to_pixel(p, cx_w, cy_w)
    assert cx_p == pytest.approx(100.0, abs=1e-9)
    assert cy_p == pytest.approx(100.0, abs=1e-9)
