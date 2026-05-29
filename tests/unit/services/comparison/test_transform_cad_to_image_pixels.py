# -*- coding: utf-8 -*-
"""Unit tests for ADR-003 H1 — cad_wcs_mm <-> image_pixels bbox mapping."""

from __future__ import annotations

from src.services.comparison.transform import (
    cad_world_to_image_pixels_bbox,
    image_pixels_to_cad_world_bbox,
)


# A3 landscape drawing frame in cad_wcs_mm (420 x 297) and a 1:1 pixel page.
FRAME = (0.0, 0.0, 420.0, 297.0)
PAGE = (840, 594)  # 2 px per mm, aspect-matched to the frame


def test_round_trip_cad_to_pixel_to_cad() -> None:
    """H1 core: cad -> pixel -> cad must recover the original bbox.

    The frame fit is a pure axis-aligned affine (no rotation), so the
    round trip is exact up to float error.
    """
    cad = (100.0, 50.0, 200.0, 150.0)
    px = cad_world_to_image_pixels_bbox(cad, cad_frame_bbox=FRAME, pixel_size=PAGE)
    assert px is not None
    back = image_pixels_to_cad_world_bbox(px, cad_frame_bbox=FRAME, pixel_size=PAGE)
    assert back is not None
    for got, want in zip(back, cad):
        assert abs(got - want) < 0.01, f"round-trip drift: {back} vs {cad}"


def test_full_frame_maps_to_full_page() -> None:
    """H1: the frame bbox itself maps to the whole pixel canvas.

    With aspect-matched page (840x594 for 420x297) and zero padding, the
    frame fills the canvas exactly.
    """
    px = cad_world_to_image_pixels_bbox(FRAME, cad_frame_bbox=FRAME, pixel_size=PAGE)
    assert px is not None
    x0, y0, x1, y1 = px
    assert abs(x0 - 0.0) < 0.5 and abs(x1 - 840.0) < 0.5
    assert abs(y0 - 0.0) < 0.5 and abs(y1 - 594.0) < 0.5


def test_y_axis_is_flipped() -> None:
    """H1: CAD Y-up -> image Y-down. A bbox near the TOP of the drawing
    (high CAD y) must land near the TOP of the image (low pixel y)."""
    top_cad = (10.0, 280.0, 30.0, 295.0)  # high y in cad
    px = cad_world_to_image_pixels_bbox(top_cad, cad_frame_bbox=FRAME, pixel_size=PAGE)
    assert px is not None
    _, py0, _, py1 = px
    # near top of a 594px page => small pixel y
    assert py0 < 60, f"expected small pixel y near image top, got {py0}"


def test_known_point_scale() -> None:
    """H1: a mid-frame bbox maps to expected pixels at 2 px/mm.

    CAD (210,148.5) is the frame centre; at 2 px/mm the pixel centre is
    (420, 297) on the 840x594 canvas.
    """
    centre = (210.0, 148.5, 210.0, 148.5)  # degenerate point at centre
    px = cad_world_to_image_pixels_bbox(centre, cad_frame_bbox=FRAME, pixel_size=PAGE)
    assert px is not None
    cx = (px[0] + px[2]) / 2
    cy = (px[1] + px[3]) / 2
    assert abs(cx - 420.0) < 1.0
    assert abs(cy - 297.0) < 1.0


def test_degenerate_frame_returns_none() -> None:
    """H1: a collapsed frame (zero width/height) yields None, not garbage."""
    point_frame = (10.0, 10.0, 10.0, 10.0)
    assert cad_world_to_image_pixels_bbox(
        (1, 1, 2, 2), cad_frame_bbox=point_frame, pixel_size=PAGE
    ) is None
    assert image_pixels_to_cad_world_bbox(
        (1, 1, 2, 2), cad_frame_bbox=point_frame, pixel_size=PAGE
    ) is None


def test_unparseable_bbox_returns_none() -> None:
    """H1: bad bbox input returns None (caller falls back to relative_only)."""
    assert cad_world_to_image_pixels_bbox(
        None, cad_frame_bbox=FRAME, pixel_size=PAGE
    ) is None
    assert cad_world_to_image_pixels_bbox(
        [1, 2], cad_frame_bbox=FRAME, pixel_size=PAGE
    ) is None
    assert cad_world_to_image_pixels_bbox(
        (1, 1, 2, 2), cad_frame_bbox=None, pixel_size=PAGE
    ) is None


def test_dict_bbox_form_accepted() -> None:
    """H1: dict bbox form (production v1 overlay shape) works."""
    cad = {"min_x": 100.0, "min_y": 50.0, "max_x": 200.0, "max_y": 150.0}
    px = cad_world_to_image_pixels_bbox(cad, cad_frame_bbox=FRAME, pixel_size=PAGE)
    assert px is not None
    # equivalent to the tuple form
    px_tuple = cad_world_to_image_pixels_bbox(
        (100.0, 50.0, 200.0, 150.0), cad_frame_bbox=FRAME, pixel_size=PAGE
    )
    for a, b in zip(px, px_tuple):
        assert abs(a - b) < 1e-6


def test_padding_shrinks_drawable_area() -> None:
    """H1: padding reserves a margin, so the frame no longer fills edge-to-edge."""
    px = cad_world_to_image_pixels_bbox(
        FRAME, cad_frame_bbox=FRAME, pixel_size=PAGE, padding_px=20
    )
    assert px is not None
    x0, y0, x1, y1 = px
    # with 20px padding the frame is inset from the canvas edges
    assert x0 >= 19.0 and y0 >= 19.0
    assert x1 <= 821.0 and y1 <= 575.0


def test_round_trip_with_offset_frame() -> None:
    """H1: a frame not anchored at origin still round-trips (real DWG
    extents are rarely at 0,0 — e.g. the S20-0002 sample)."""
    frame = (353044.5, 206619.1, 403601.6, 215556.8)  # real-ish DWG extents
    cad = (360000.0, 208000.0, 365000.0, 210000.0)
    px = cad_world_to_image_pixels_bbox(cad, cad_frame_bbox=frame, pixel_size=(1754, 2481))
    assert px is not None
    back = image_pixels_to_cad_world_bbox(px, cad_frame_bbox=frame, pixel_size=(1754, 2481))
    assert back is not None
    for got, want in zip(back, cad):
        assert abs(got - want) < 0.1, f"offset-frame round-trip drift: {back} vs {cad}"
