# -*- coding: utf-8 -*-
"""PDF overlay coordinate-metadata backfill.

Root cause of "도면은 보이나 흐리게/엉뚱한 위치 · 실배경 아님": the change-marker
bbox is in PDF image_pixels (e.g. x=1859 @ 200 DPI), but dashboard ``top_issues``
overlays drop ``bbox_coordinate_space``/``pdf_dpi``. push_change_overlays_from_v1
then passes the RAW PIXELS through (no conversion), so markers land in pixel space
while the PDF background is in points (0..842) — the page renders off-screen and
only relative markers show. The backfill restores the metadata (sourced from the
canonical overlay JSON, since the manifest pair record has them as None) so the
image_pixels -> PDF-points conversion fires.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2


def _fake(pair, canonical):
    return SimpleNamespace(
        _viewer_pairs_by_id={"p": pair},
        _viewer_overlays_for_pair_v2=lambda pid: canonical,
    )


_PDF_PAIR = {"coordinate_source": "image_pixels", "source_a": "a.pdf",
             "source_b": "b.pdf", "compare_pdf_dpi": None}
_CANONICAL = [{"zone_id": "C-004", "pdf_dpi": 200.0, "bbox_coordinate_space": "image_pixels"}]


def test_backfill_stamps_space_and_dpi_from_canonical_overlays():
    fake = _fake(_PDF_PAIR, _CANONICAL)
    overlays = [{"zone_id": "C-004", "bbox": {"min_x": 1859.0, "min_y": 1286.0,
                                              "max_x": 1969.0, "max_y": 1452.0}}]
    out = DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(fake, "p", overlays)
    assert out[0]["bbox_coordinate_space"] == "image_pixels"
    assert out[0]["pdf_dpi"] == 200.0


def test_backfilled_overlay_converts_to_pdf_points():
    """End-to-end: after backfill, the shared conversion lands the marker inside
    the page (points), not at the raw pixel coordinate."""
    from src.gui.lightweight_viewport import convert_bbox_to_world_space

    fake = _fake(_PDF_PAIR, _CANONICAL)
    overlays = [{"zone_id": "C-004", "bbox": {"min_x": 1859.0, "min_y": 1286.0,
                                              "max_x": 1969.0, "max_y": 1452.0}}]
    ov = DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(fake, "p", overlays)[0]
    coords = convert_bbox_to_world_space(
        ov["bbox"], coordinate_space=ov["bbox_coordinate_space"],
        pdf_dpi=float(ov["pdf_dpi"]), page_height_points=1190.52,
    )
    assert coords is not None
    x0, y0, x1, y1 = coords
    assert 0.0 <= x0 <= 842.0 and x0 == pytest.approx(669.24, abs=1.0)  # 1859*72/200
    assert 0.0 <= y1 <= 1191.0  # within page height


def test_backfill_preserves_existing_metadata():
    fake = _fake(_PDF_PAIR, _CANONICAL)
    overlays = [{"zone_id": "C-004", "bbox": {"min_x": 1.0, "min_y": 2.0, "max_x": 3.0, "max_y": 4.0},
                 "bbox_coordinate_space": "image_pixels", "pdf_dpi": 144.0}]
    out = DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(fake, "p", overlays)
    assert out[0]["pdf_dpi"] == 144.0  # not overwritten


def test_backfill_noop_for_non_pdf_pair():
    fake = _fake({"coordinate_source": "world", "source_a": "a.dxf", "source_b": "b.dxf"}, [])
    overlays = [{"zone_id": "Z", "bbox": {"min_x": 1.0, "min_y": 2.0, "max_x": 3.0, "max_y": 4.0}}]
    out = DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(fake, "p", overlays)
    assert "bbox_coordinate_space" not in out[0]  # DXF overlays untouched
