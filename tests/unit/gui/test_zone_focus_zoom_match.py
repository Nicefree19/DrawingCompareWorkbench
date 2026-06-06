# -*- coding: utf-8 -*-
"""Right-list zone selection must zoom the lightweight viewer to the zone.

Bug ("우측 리스트 선택에 따라 뷰어가 확대되는게 매칭이 안 됨"): the active-zone
overlays come from the dashboard ``top_issues`` list, which never went
through ``_push_overlays_to_lightweight_v2``'s coordinate backfill. For a PDF
pair the bbox is in ``image_pixels`` (e.g. x=1859 @ 200 DPI) with no
``bbox_coordinate_space``/``pdf_dpi``, so ``convert_bbox_to_world_space``
passed it through UNCHANGED and ``set_camera_to_world_bbox`` zoomed to pixel
coordinates far outside the points-space page — the camera never matched the
selected zone. ``_focus_lightweight_on_zone_v2`` now backfills the overlay
first so the image_pixels -> PDF-points conversion fires (1859 px -> 669 pt).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2


_PDF_PAGE_WORLD_BBOX = (0.0, 0.0, 841.9, 1190.52)
_PIXEL_BBOX = {"min_x": 1859.0, "min_y": 1286.0, "max_x": 1969.0, "max_y": 1452.0}


class _FakeViewport:
    def __init__(self, world_bbox):
        self.world_bbox = world_bbox
        self.camera_calls: list[tuple] = []

    def set_camera_to_world_bbox(self, bbox, *, padding_ratio=0.25):
        self.camera_calls.append((tuple(bbox), padding_ratio))


def _make_fake(pair: dict, overlay: dict):
    before = _FakeViewport(_PDF_PAGE_WORLD_BBOX)
    after = _FakeViewport(_PDF_PAGE_WORLD_BBOX)
    ns = SimpleNamespace(
        act_lightweight_viewer_v2=object(),
        _is_lightweight_viewer_active_v2=lambda: True,
        _active_overlays_by_zone={str(overlay["zone_id"]): overlay},
        _active_row={"pair_id": "p"},
        _viewer_pairs_by_id={"p": pair},
        _viewer_root=None,
        preview_before_lightweight_v2=before,
        preview_after_lightweight_v2=after,
        _lightweight_camera_sync_in_progress=False,
        _schedule_lightweight_visible_tile_window_v2=lambda side: None,
        _push_overlays_to_lightweight_v2=lambda pair_id, focus_zone_id=None: None,
    )
    ns._backfill_pdf_overlay_coord_space_v2 = (
        lambda pair_id, ovs: DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(
            ns, pair_id, ovs
        )
    )
    ns._peek_overlay_json_pdf_dpi_v2 = (
        lambda vp: DrawingCompareWorkbenchV2._peek_overlay_json_pdf_dpi_v2(ns, vp)
    )
    return ns, before, after


def test_pdf_zone_focus_zooms_to_points_not_pixels():
    """The camera bbox must be in PDF points (~669), not raw pixels (1859)."""

    pair = {"coordinate_source": "image_pixels", "source_a": "a.pdf",
            "source_b": "b.pdf", "compare_pdf_dpi": 200.0}
    overlay = {"zone_id": "C-004", "bbox": dict(_PIXEL_BBOX),
               "change_type": "modified"}  # NO bbox_coordinate_space / pdf_dpi
    ns, before, after = _make_fake(pair, overlay)

    DrawingCompareWorkbenchV2._focus_lightweight_on_zone_v2(ns, "C-004")

    assert after.camera_calls, "after-side camera should have been moved"
    world_bbox = after.camera_calls[-1][0]
    # 1859 * 72 / 200 = 669.24 — proves the backfill made the conversion fire.
    assert world_bbox[0] == pytest.approx(669.24, abs=1.0)
    assert world_bbox[0] != pytest.approx(1859.0, abs=1.0)
    # Landed inside the page bounds (was off-page before the fix).
    assert 0.0 <= world_bbox[0] <= 841.9


def test_dxf_zone_focus_passes_world_coords_through_unchanged():
    """Regression guard: DXF overlays (already world coords) must NOT be
    rescaled by the PDF backfill — they pass through untouched."""

    pair = {"coordinate_source": "world", "source_a": "a.dxf", "source_b": "b.dxf"}
    overlay = {"zone_id": "Z", "bbox": {"min_x": 100.0, "min_y": 50.0,
                                        "max_x": 140.0, "max_y": 90.0},
               "change_type": "modified"}
    # world-space viewport bbox (CAD units), not a PDF page
    ns, before, after = _make_fake(pair, overlay)
    before.world_bbox = (0.0, 0.0, 500.0, 500.0)
    after.world_bbox = (0.0, 0.0, 500.0, 500.0)

    DrawingCompareWorkbenchV2._focus_lightweight_on_zone_v2(ns, "Z")

    assert after.camera_calls, "after-side camera should have been moved"
    world_bbox = after.camera_calls[-1][0]
    assert world_bbox[0] == pytest.approx(100.0, abs=0.01)  # unchanged
