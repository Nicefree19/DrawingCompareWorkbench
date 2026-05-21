# -*- coding: utf-8 -*-
"""Unit tests for the Phase G2.7-COORDFIX bbox→world conversion helper.

The lightweight viewport's world space differs depending on the loaded
background:
    * **PDF backgrounds** use **PDF points** (``world_bbox = (0, 0,
      page_w_pt, page_h_pt)``) — see ``LightweightDrawingViewport.
      load_pdf_page``.
    * **DXF/DWG backgrounds** use **CAD world units** (mm/m).

The comparison engine, however, stamps PDF overlays as
``bbox_coordinate_space == "image_pixels"`` with pixel coords measured at
``pdf_dpi`` (e.g. 200). Without a px→pt conversion the cloud markers
land far outside the actual page. This module locks in the conversion
contract so the bug doesn't regress.

Pure Python; no Qt needed (helper lives in ``lightweight_viewport`` but
is import-safe).
"""

from __future__ import annotations

import math

import pytest

# WI-20260509-005 — `lightweight_viewport.py` imports `PySide6.QtQuickWidgets`
# at module load time. In PySide6 minimal installs (e.g. CI runners that
# install only QtCore/QtWidgets), QtQuickWidgets is absent and the module
# import below would raise ``ModuleNotFoundError`` *during pytest collection*,
# aborting the whole batch run with `Interrupted: 1 error during collection`.
# `importorskip` defers to a clean "skipped" outcome and lets the rest of
# the suite continue.
pytest.importorskip(
    "PySide6.QtQuickWidgets",
    reason="PySide6.QtQuickWidgets is not available in this environment "
           "(minimal PySide6 install without Qt Quick).",
)

# Test the public helper directly — no Qt initialization required.
from src.gui.lightweight_viewport import (
    _page_height_points_from_world_bbox,
    _normalise_bbox,
    convert_bbox_to_world_space,
)


# ---------------------------------------------------------------------------
# _normalise_bbox — accepts both dict and 4-tuple forms
# ---------------------------------------------------------------------------


def test_normalise_bbox_accepts_dict_form() -> None:
    out = _normalise_bbox({"min_x": 1, "min_y": 2, "max_x": 3, "max_y": 4})
    assert out == (1.0, 2.0, 3.0, 4.0)


def test_normalise_bbox_accepts_list_form() -> None:
    out = _normalise_bbox([10, 20, 30, 40])
    assert out == (10.0, 20.0, 30.0, 40.0)


def test_normalise_bbox_returns_none_for_garbage() -> None:
    assert _normalise_bbox(None) is None
    assert _normalise_bbox("garbage") is None
    assert _normalise_bbox([1, 2, 3]) is None  # too few elements
    assert _normalise_bbox({"x": 0}) is None  # missing keys


# ---------------------------------------------------------------------------
# convert_bbox_to_world_space — DXF/DWG (no metadata) passes through
# ---------------------------------------------------------------------------


def test_dxf_overlay_passes_through_unchanged() -> None:
    """No coordinate_space → bbox returned verbatim (DXF/DWG case)."""

    bbox = [100.0, 200.0, 300.0, 400.0]
    out = convert_bbox_to_world_space(bbox)
    assert out == (100.0, 200.0, 300.0, 400.0)


def test_dxf_with_world_coordinate_space_passes_through() -> None:
    """Explicit non-image space also passes through."""

    out = convert_bbox_to_world_space(
        [10, 20, 30, 40], coordinate_space="cad_world"
    )
    assert out == (10.0, 20.0, 30.0, 40.0)


def test_image_pixels_with_zero_dpi_passes_through() -> None:
    """Defensive: missing pdf_dpi (=0) → no scaling, returns raw px."""

    out = convert_bbox_to_world_space(
        [100, 200, 300, 400],
        coordinate_space="image_pixels",
        pdf_dpi=0.0,
    )
    assert out == (100.0, 200.0, 300.0, 400.0)


# ---------------------------------------------------------------------------
# convert_bbox_to_world_space — PDF px → pt conversion
# ---------------------------------------------------------------------------


def test_pdf_image_pixels_at_72_dpi_is_identity() -> None:
    """At pdf_dpi=72 the conversion factor is 1.0 (1 pt = 1 px)."""

    out = convert_bbox_to_world_space(
        [100, 200, 300, 400],
        coordinate_space="image_pixels",
        pdf_dpi=72.0,
    )
    assert out == (100.0, 200.0, 300.0, 400.0)


def test_pdf_image_pixels_at_200_dpi_scales_correctly() -> None:
    """At 200 DPI: pt = px * 72/200 = px * 0.36."""

    # 100 px @ 200 DPI = 100 * 72/200 = 36 pt
    out = convert_bbox_to_world_space(
        [100.0, 200.0, 300.0, 400.0],
        coordinate_space="image_pixels",
        pdf_dpi=200.0,
    )
    assert out is not None
    expected = (36.0, 72.0, 108.0, 144.0)
    for got, want in zip(out, expected):
        assert math.isclose(got, want, rel_tol=1e-6), \
            f"expected {expected}, got {out}"


def test_pdf_image_pixels_with_page_height_flips_y_for_qml_world() -> None:
    """PDF image pixels are top-left/Y-down; QML world is bottom-left/Y-up."""

    page_h_pt = 841.89
    out = convert_bbox_to_world_space(
        [200.0, 300.0, 500.0, 600.0],
        coordinate_space="image_pixels",
        pdf_dpi=200.0,
        page_height_points=page_h_pt,
    )
    assert out is not None
    expected = (72.0, page_h_pt - 216.0, 180.0, page_h_pt - 108.0)
    for got, want in zip(out, expected):
        assert math.isclose(got, want, abs_tol=0.01), \
            f"expected {expected}, got {out}"


def test_pdf_image_pixels_at_400_dpi_scales_correctly() -> None:
    """At 400 DPI: pt = px * 72/400 = px * 0.18."""

    out = convert_bbox_to_world_space(
        [100.0, 200.0, 300.0, 400.0],
        coordinate_space="image_pixels",
        pdf_dpi=400.0,
    )
    assert out is not None
    expected = (18.0, 36.0, 54.0, 72.0)
    for got, want in zip(out, expected):
        assert math.isclose(got, want, rel_tol=1e-6), \
            f"expected {expected}, got {out}"


def test_pdf_dict_bbox_at_200_dpi() -> None:
    """Dict bbox {min_x, min_y, max_x, max_y} also gets scaled."""

    out = convert_bbox_to_world_space(
        {"min_x": 100, "min_y": 200, "max_x": 300, "max_y": 400},
        coordinate_space="image_pixels",
        pdf_dpi=200.0,
    )
    assert out is not None
    expected = (36.0, 72.0, 108.0, 144.0)
    for got, want in zip(out, expected):
        assert math.isclose(got, want, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# Realistic PDF page check — A4 portrait
# ---------------------------------------------------------------------------


def test_a4_portrait_page_overlay_lands_inside_page() -> None:
    """Sanity: overlay near the top-left of an A4 page (210mm × 297mm)
    rendered at 200 DPI should land inside the page-points world bbox.

    A4: 210mm wide × 297mm tall = 8.27in × 11.69in
                                = 595.27pt × 841.89pt @ 72 DPI
    A4 raster size at 200 DPI = ~1654 × 2339 px.

    Overlay near top-left at (200px, 300px, 500px, 600px) should
    convert to roughly (72pt, 108pt, 180pt, 216pt) — well inside the
    595×842 page bounds.
    """

    page_w_pt = 595.27
    page_h_pt = 841.89

    out = convert_bbox_to_world_space(
        [200.0, 300.0, 500.0, 600.0],
        coordinate_space="image_pixels",
        pdf_dpi=200.0,
    )
    assert out is not None
    x0, y0, x1, y1 = out
    # Inside page bounds (+ small margin for floating-point):
    assert 0.0 <= x0 < page_w_pt
    assert 0.0 <= y0 < page_h_pt
    assert x1 <= page_w_pt
    assert y1 <= page_h_pt
    # Approximate values from manual calculation:
    assert math.isclose(x0, 72.0, abs_tol=0.5)
    assert math.isclose(y0, 108.0, abs_tol=0.5)


def test_page_height_points_from_world_bbox() -> None:
    assert _page_height_points_from_world_bbox([0, 0, 595.27, 841.89]) == pytest.approx(841.89)
    assert _page_height_points_from_world_bbox({"min_x": 0, "min_y": 10, "max_x": 10, "max_y": 30}) == pytest.approx(20.0)
    assert _page_height_points_from_world_bbox(None) == 0.0


# ---------------------------------------------------------------------------
# Defensive — bad inputs
# ---------------------------------------------------------------------------


def test_returns_none_for_invalid_bbox_input() -> None:
    """Garbage in → None out (caller should skip)."""

    assert convert_bbox_to_world_space(None) is None
    assert convert_bbox_to_world_space("not-a-bbox") is None
    assert convert_bbox_to_world_space([1, 2]) is None  # too short


def test_string_pdf_dpi_silently_treated_as_zero() -> None:
    """Phase G2.7-COORDFIX defensive path — non-numeric pdf_dpi shouldn't
    crash; we pass through unchanged (no scaling)."""

    out = convert_bbox_to_world_space(
        [100, 200, 300, 400],
        coordinate_space="image_pixels",
        pdf_dpi="invalid",  # type: ignore[arg-type]
    )
    # Should NOT raise — falls back to identity
    assert out == (100.0, 200.0, 300.0, 400.0)
