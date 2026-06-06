# -*- coding: utf-8 -*-
"""A programmatic zone-focus zoom must trigger the higher-DPI PDF re-render.

Bug: clicking a change zone calls set_camera_to_world_bbox, which sets
unitsPerPixel directly (no QML wheel event), so it never reached
_on_qml_viewport_changed -> the PDF stayed at the base 150-DPI render and the
auto-zoomed change looked blurry ("도면은 보이나 흐리게"). The fix calls
_maybe_schedule_pdf_rerender from set_camera_to_world_bbox too.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_zone_focus_zoom_schedules_pdf_rerender():
    from src.gui.lightweight_viewport import LightweightDrawingViewport

    scheduled: list[float] = []

    class _FakeRoot:
        def property(self, name):
            return {"width": 800.0, "height": 600.0}.get(name, 0.0)

        def setProperty(self, *args):
            return None

    fake_self = SimpleNamespace(
        _quick=SimpleNamespace(rootObject=lambda: _FakeRoot()),
        width=lambda: 800,
        height=lambda: 600,
        _maybe_schedule_pdf_rerender=lambda upp: scheduled.append(float(upp)),
    )

    # zoom into a small change-zone bbox
    LightweightDrawingViewport.set_camera_to_world_bbox(
        fake_self, (100.0, 100.0, 140.0, 140.0)
    )

    assert len(scheduled) == 1, "zone focus must schedule a higher-DPI PDF re-render"
    assert scheduled[0] > 0.0


def test_zone_focus_rerender_schedule_failure_is_non_fatal():
    from src.gui.lightweight_viewport import LightweightDrawingViewport

    class _FakeRoot:
        def property(self, name):
            return {"width": 800.0, "height": 600.0}.get(name, 0.0)

        def setProperty(self, *args):
            return None

    def _boom(_upp):
        raise RuntimeError("synthetic rerender failure")

    fake_self = SimpleNamespace(
        _quick=SimpleNamespace(rootObject=lambda: _FakeRoot()),
        width=lambda: 800,
        height=lambda: 600,
        _maybe_schedule_pdf_rerender=_boom,
    )

    # must not raise — focus is more important than the re-render optimisation
    LightweightDrawingViewport.set_camera_to_world_bbox(
        fake_self, (100.0, 100.0, 140.0, 140.0)
    )


def test_fire_pdf_rerender_caps_pixel_budget():
    """The zoom re-render must cap max_render_pixels so a deep zoom (600 DPI)
    cannot produce an oversized image the viewport can't display (which blanked
    the PDF background)."""
    from src.gui.lightweight_viewport import (
        LightweightDrawingViewport,
        _PDF_RERENDER_MAX_PIXELS,
    )

    captured: dict = {}

    def _spy_load_pdf_page(path, *, page_index=0, target_dpi=150.0, cache_dir=None,
                           max_render_pixels=None, **kw):
        captured["target_dpi"] = target_dpi
        captured["max_render_pixels"] = max_render_pixels
        return True

    fake_self = SimpleNamespace(
        _pdf_render_state={
            "pending_dpi": 600.0,
            "pdf_path": "C:/x/page.pdf",
            "page_index": 0,
            "cache_dir": None,
            "current_dpi": 150.0,
        },
        load_pdf_page=_spy_load_pdf_page,
    )
    LightweightDrawingViewport._fire_pdf_rerender(fake_self)
    assert captured["max_render_pixels"] == _PDF_RERENDER_MAX_PIXELS
    assert _PDF_RERENDER_MAX_PIXELS < 47_000_000  # well below the 47-69 MP that blanked
