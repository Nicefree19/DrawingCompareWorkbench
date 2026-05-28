# -*- coding: utf-8 -*-
"""Coordinate contract tests spanning CAD, PDF, and viewer tile math."""

from __future__ import annotations

import pytest

from src.services.comparison.transform import (
    COORD_IMAGE_PIXELS_TL,
    fit_world_to_pixels,
    pdf_image_pixels_to_page_points_bbox,
    pdf_page_points_to_image_pixels_bbox,
    transform_bbox,
)
from src.services.comparison.viewer_tile_cache import _tile_range


@pytest.mark.parametrize("dpi", [72.0, 150.0, 200.0, 400.0])
def test_pdf_image_pixels_y_flip_for_72_150_200_400_dpi(dpi: float) -> None:
    page_h = 792.0
    bbox_px = (dpi, 2.0 * dpi, 2.0 * dpi, 3.0 * dpi)

    out = pdf_image_pixels_to_page_points_bbox(
        bbox_px,
        dpi=dpi,
        page_height_points=page_h,
    )

    assert out == pytest.approx((72.0, 576.0, 144.0, 648.0), abs=1e-9)


@pytest.mark.parametrize("dpi", [72.0, 150.0, 200.0, 400.0])
def test_pdf_points_to_render_pixels_round_trips_for_72_150_200_400_dpi(
    dpi: float,
) -> None:
    page_h = 792.0
    bbox_pt = (72.0, 576.0, 144.0, 648.0)

    bbox_px = pdf_page_points_to_image_pixels_bbox(
        bbox_pt,
        dpi=dpi,
        page_height_points=page_h,
    )
    assert bbox_px == pytest.approx((dpi, 2.0 * dpi, 2.0 * dpi, 3.0 * dpi))

    round_trip = pdf_image_pixels_to_page_points_bbox(
        bbox_px,
        dpi=dpi,
        page_height_points=page_h,
    )
    assert round_trip == pytest.approx(bbox_pt, abs=1e-9)


def test_cad_world_to_pixel_to_cad_round_trip_transforms_all_bbox_corners() -> None:
    bbox = (-1200.0, -50.0, 300.0, 750.0)
    params = fit_world_to_pixels(
        bbox,
        (1600, 1200),
        padding_px=32,
        coordinate_space="cad_world",
    )

    pixel_bbox = transform_bbox(params.world_to_pixel, bbox)
    out = transform_bbox(params.pixel_to_world, pixel_bbox)

    assert params.coordinate_space == "cad_wcs_mm"
    assert out == pytest.approx(bbox, abs=1e-9)


def test_transform_bbox_handles_top_left_pdf_image_pixel_space() -> None:
    params = fit_world_to_pixels(
        (0.0, 0.0, 400.0, 300.0),
        (400, 300),
        coordinate_space=COORD_IMAGE_PIXELS_TL,
    )

    assert params.coordinate_space == COORD_IMAGE_PIXELS_TL
    assert params.y_axis == "down"


def test_tile_boundary_bbox_flooring_at_exact_tile_edges() -> None:
    out = _tile_range(
        {"x": 0.0, "y": 0.0, "width": 512.0, "height": 512.0},
        tile_size=512,
        prefetch_radius=0,
    )

    assert out == (0, 0, 0, 0)


def test_tile_boundary_bbox_spanning_adjacent_tiles_reports_full_tile_span() -> None:
    out = _tile_range(
        {"x": 511.5, "y": 10.0, "width": 2.0, "height": 512.0},
        tile_size=512,
        prefetch_radius=0,
    )

    assert out == (0, 0, 1, 1)
