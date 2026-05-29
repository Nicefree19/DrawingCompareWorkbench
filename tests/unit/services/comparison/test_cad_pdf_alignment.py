# -*- coding: utf-8 -*-
"""Unit tests for ADR-003 H2 — cad_pdf_alignment (DWG frame <-> PDF page)."""

from __future__ import annotations

from src.services.comparison.cad_pdf_alignment import (
    CadPdfAlignment,
    align_cad_to_pdf,
)

# A3 landscape frame (420 x 297 mm) and an aspect-matched pixel page.
A3_FRAME = (0.0, 0.0, 420.0, 297.0)
A3_PAGE_MATCHED = (840, 594)  # 420/297 == 840/594


def test_aspect_match_is_exact() -> None:
    """H2: DWG frame aspect matching the PDF page aspect -> exact."""
    al = align_cad_to_pdf(A3_FRAME, A3_PAGE_MATCHED)
    assert al.quality == "exact"
    assert al.is_usable
    assert al.aspect_mismatch < 0.02


def test_aspect_mismatch_is_estimated() -> None:
    """H2: aspect mismatch beyond tolerance -> estimated (plot drift §8-6).

    A3 frame (1.414) plotted onto a square-ish 600x594 page (~1.01).
    """
    al = align_cad_to_pdf(A3_FRAME, (600, 594))
    assert al.quality == "estimated"
    assert al.is_usable  # still mappable, just flagged
    assert al.aspect_mismatch > 0.02


def test_degenerate_frame_is_relative_only() -> None:
    """H2: collapsed DWG frame -> relative_only, not usable."""
    al = align_cad_to_pdf((10.0, 10.0, 10.0, 10.0), A3_PAGE_MATCHED)
    assert al.quality == "relative_only"
    assert not al.is_usable
    assert al.map_cad_bbox((1, 1, 2, 2)) is None


def test_zero_page_is_relative_only() -> None:
    """H2: zero/empty pixel page -> relative_only."""
    al = align_cad_to_pdf(A3_FRAME, (0, 0))
    assert al.quality == "relative_only"
    assert not al.is_usable


def test_unparseable_frame_is_relative_only() -> None:
    """H2: bad frame input -> relative_only (no crash)."""
    al = align_cad_to_pdf(None, A3_PAGE_MATCHED)
    assert al.quality == "relative_only"
    al2 = align_cad_to_pdf([1, 2], A3_PAGE_MATCHED)
    assert al2.quality == "relative_only"


def test_map_cad_bbox_delegates_to_h1() -> None:
    """H2: map_cad_bbox produces image-pixel coords via the H1 transform.

    The frame centre maps to the page pixel centre.
    """
    al = align_cad_to_pdf(A3_FRAME, A3_PAGE_MATCHED)
    centre = (210.0, 148.5, 210.0, 148.5)  # frame centre point
    px = al.map_cad_bbox(centre)
    assert px is not None
    cx = (px[0] + px[2]) / 2
    cy = (px[1] + px[3]) / 2
    assert abs(cx - 420.0) < 1.0  # 840/2
    assert abs(cy - 297.0) < 1.0  # 594/2


def test_map_cad_bboxes_preserves_order_and_filters_bad() -> None:
    """H2: map_cad_bboxes keeps order; unparseable entries become None."""
    al = align_cad_to_pdf(A3_FRAME, A3_PAGE_MATCHED)
    out = al.map_cad_bboxes(
        [(0, 0, 10, 10), None, {"min_x": 100, "min_y": 50, "max_x": 110, "max_y": 60}]
    )
    assert len(out) == 3
    assert out[0] is not None
    assert out[1] is None  # None input -> None
    assert out[2] is not None


def test_relative_only_map_returns_none() -> None:
    """H2: when alignment is relative_only, map_cad_bbox refuses to guess."""
    al = align_cad_to_pdf((0, 0, 0, 0), A3_PAGE_MATCHED)
    assert al.map_cad_bbox((1, 1, 2, 2)) is None
    assert al.map_cad_bboxes([(1, 1, 2, 2)]) == [None]


def test_real_s20_frame_alignment() -> None:
    """H2: real-ish S20-0002 extents onto the measured 150dpi A3 render.

    Measured render was 1754x2481 (portrait). The S20 extents below are
    landscape-ish, so this is an aspect mismatch -> estimated, but still
    maps points (exercises the real-data path)."""
    frame = (353044.5, 206619.1, 403601.6, 215556.8)
    al = align_cad_to_pdf(frame, (1754, 2481))
    # frame is wide-and-short, page is tall -> mismatch -> estimated
    assert al.quality == "estimated"
    px = al.map_cad_bbox((360000.0, 208000.0, 365000.0, 210000.0))
    assert px is not None
    # mapped bbox is within the page pixel bounds
    assert 0 <= px[0] <= 1754 and 0 <= px[2] <= 1754


def test_to_dict_round_trips_fields() -> None:
    """H2: to_dict exposes all alignment fields for manifest/telemetry."""
    al = align_cad_to_pdf(A3_FRAME, A3_PAGE_MATCHED)
    d = al.to_dict()
    for key in (
        "cad_frame_bbox",
        "pdf_pixel_size",
        "quality",
        "cad_aspect",
        "pdf_aspect",
        "aspect_mismatch",
        "padding_px",
    ):
        assert key in d
    assert d["quality"] == "exact"
