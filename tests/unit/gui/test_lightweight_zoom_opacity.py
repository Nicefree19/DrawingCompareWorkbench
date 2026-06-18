# -*- coding: utf-8 -*-
"""Transparency + zoom controls must drive the active lightweight viewport.

Bug ("뷰어상의 투명도와 확대 축소 기능 제대로 작동되게"): the zoom slider handler
only touched the legacy GPU viewport (``preview_before_v2``) and the opacity
handler only the two GPU viewports — so when the lightweight viewer was the
active surface, the slider/controls did nothing. The handlers now route to the
lightweight viewports, and the viewport gained an absolute, fit-anchored
``apply_zoom_factor`` (100 % == fit-to-view).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
from src.gui.lightweight_viewport import LightweightDrawingViewport


# ---------------------------------------------------------------------------
# Viewport: apply_zoom_factor / _fit_units_per_pixel
# ---------------------------------------------------------------------------

class _FakeRoot:
    def __init__(self, props):
        self._props = props

    def property(self, name):
        return self._props.get(name)


def _viewport_fake(world_bbox, *, width=800.0, height=600.0, cx=50.0, cy=50.0):
    root = _FakeRoot({"width": width, "height": height,
                      "cameraCenterX": cx, "cameraCenterY": cy})
    scheduled: list[float] = []
    set_camera_calls: list[tuple] = []
    ns = SimpleNamespace(
        _quick=SimpleNamespace(rootObject=lambda: root),
        _world_bbox=world_bbox,
        width=lambda: width,
        height=lambda: height,
        set_camera=lambda x, y, upp: set_camera_calls.append((x, y, upp)),
        _maybe_schedule_pdf_rerender=lambda upp: scheduled.append(float(upp)),
    )
    ns._fit_units_per_pixel = (
        lambda: LightweightDrawingViewport._fit_units_per_pixel(ns)
    )
    return ns, set_camera_calls, scheduled


def test_fit_units_per_pixel_matches_qml_formula():
    ns, _calls, _sched = _viewport_fake((0.0, 0.0, 400.0, 300.0))
    fit = LightweightDrawingViewport._fit_units_per_pixel(ns)
    # max(400/800, 300/600) * 1.05 == 0.5 * 1.05
    assert fit == pytest.approx(0.525, abs=1e-6)


def test_apply_zoom_factor_2x_halves_units_per_pixel():
    ns, calls, scheduled = _viewport_fake((0.0, 0.0, 400.0, 300.0), cx=50.0, cy=70.0)
    LightweightDrawingViewport.apply_zoom_factor(ns, 2.0)
    assert calls, "apply_zoom_factor must drive the camera"
    x, y, upp = calls[-1]
    assert x == pytest.approx(50.0) and y == pytest.approx(70.0)  # centre kept
    assert upp == pytest.approx(0.2625, abs=1e-6)  # 0.525 / 2
    assert scheduled and scheduled[-1] == pytest.approx(0.2625, abs=1e-6)


def test_apply_zoom_factor_100pct_equals_fit():
    ns, calls, _sched = _viewport_fake((0.0, 0.0, 400.0, 300.0))
    LightweightDrawingViewport.apply_zoom_factor(ns, 1.0)
    assert calls[-1][2] == pytest.approx(0.525, abs=1e-6)


def test_apply_zoom_factor_noop_without_root():
    calls: list[tuple] = []
    ns = SimpleNamespace(
        _quick=SimpleNamespace(rootObject=lambda: None),
        set_camera=lambda *a: calls.append(a),
    )
    LightweightDrawingViewport.apply_zoom_factor(ns, 2.0)
    assert not calls  # no crash, no camera move


# ---------------------------------------------------------------------------
# Workbench: opacity handler drives every viewport
# ---------------------------------------------------------------------------

def _opacity_fake():
    seen: dict[str, float] = {}

    def vp(name):
        return SimpleNamespace(
            set_overlay_opacity_scale=lambda s, _n=name: seen.__setitem__(_n, s)
        )

    ns = SimpleNamespace(
        preview_before_v2=vp("gpu_before"),
        preview_after_v2=vp("gpu_after"),
        preview_before_lightweight_v2=vp("lw_before"),
        preview_after_lightweight_v2=vp("lw_after"),
    )
    return ns, seen


def test_opacity_slider_drives_all_four_viewports():
    ns, seen = _opacity_fake()
    DrawingCompareWorkbenchV2._on_overlay_opacity_changed_v2(ns, 50)
    assert seen == {
        "gpu_before": 0.5, "gpu_after": 0.5,
        "lw_before": 0.5, "lw_after": 0.5,
    }


def test_opacity_slider_clamps_low_and_high():
    ns, seen = _opacity_fake()
    DrawingCompareWorkbenchV2._on_overlay_opacity_changed_v2(ns, 5)
    assert seen["lw_before"] == pytest.approx(0.30)  # clamped at 30
    DrawingCompareWorkbenchV2._on_overlay_opacity_changed_v2(ns, 150)
    assert seen["lw_after"] == pytest.approx(1.0)  # clamped at 100


def test_opacity_handler_skips_absent_lightweight_viewports():
    seen: dict[str, float] = {}
    ns = SimpleNamespace(
        preview_before_v2=SimpleNamespace(
            set_overlay_opacity_scale=lambda s: seen.__setitem__("gpu_before", s)
        ),
        preview_after_v2=SimpleNamespace(
            set_overlay_opacity_scale=lambda s: seen.__setitem__("gpu_after", s)
        ),
        preview_before_lightweight_v2=None,
        preview_after_lightweight_v2=None,
    )
    DrawingCompareWorkbenchV2._on_overlay_opacity_changed_v2(ns, 80)
    assert seen == {"gpu_before": 0.8, "gpu_after": 0.8}  # no crash on None


# ---------------------------------------------------------------------------
# Workbench: zoom slider routes to the active viewer
# ---------------------------------------------------------------------------

def test_zoom_slider_routes_to_lightweight_when_active():
    factors: list[float] = []

    def lw(name):
        return SimpleNamespace(apply_zoom_factor=lambda f: factors.append(f))

    # GPU viewport must NOT be touched when lightweight is active.
    gpu_touched: list[str] = []
    gpu = SimpleNamespace(
        _quick_ready=True,
        _quick=SimpleNamespace(
            rootObject=lambda: gpu_touched.append("root") or None
        ),
    )
    ns = SimpleNamespace(
        _is_lightweight_viewer_active_v2=lambda: True,
        preview_before_lightweight_v2=lw("before"),
        preview_after_lightweight_v2=lw("after"),
        preview_before_v2=gpu,
    )
    DrawingCompareWorkbenchV2._on_zoom_slider_changed_v2(ns, 200)
    assert factors == [2.0, 2.0]  # both lightweight sides, 200 % -> 2.0x
    assert not gpu_touched  # GPU path skipped


def test_zoom_slider_uses_gpu_path_when_lightweight_inactive():
    applied: list[tuple] = []
    root = _FakeRoot({"panX": 0.0, "panY": 0.0})
    gpu = SimpleNamespace(
        _quick_ready=True,
        _quick=SimpleNamespace(rootObject=lambda: root),
        apply_viewport=lambda z, px, py: applied.append((z, px, py)),
    )
    ns = SimpleNamespace(
        _is_lightweight_viewer_active_v2=lambda: False,
        preview_before_v2=gpu,
        preview_after_v2=SimpleNamespace(),
        _sync_preview_viewport_v2=lambda *a: None,
    )
    DrawingCompareWorkbenchV2._on_zoom_slider_changed_v2(ns, 300)
    assert applied == [(3.0, 0.0, 0.0)]  # legacy path still works


# ---------------------------------------------------------------------------
# Real-widget: opacity value actually propagates to the QML root property
# ---------------------------------------------------------------------------
# The SimpleNamespace tests above prove the handler routes to the viewport.
# These prove the value reaches the REAL QML root property `overlayOpacityScale`
# that the QML overlay bindings consume (cloud markers + focus rectangle), which
# the manual GUI check could not isolate (no marker was in the zoomed frame).


def test_overlay_opacity_scale_propagates_to_real_qml_root():
    from PySide6.QtWidgets import QApplication

    from src.gui.lightweight_viewport import LightweightDrawingViewport

    _ = QApplication.instance() or QApplication([])
    viewport = LightweightDrawingViewport()

    viewport.set_overlay_opacity_scale(0.5)

    assert viewport.overlay_opacity_scale == pytest.approx(0.5)  # internal contract
    root = viewport._quick.rootObject()
    assert root is not None, "QML root must exist after instantiation"
    assert root.property("overlayOpacityScale") == pytest.approx(0.5)  # reaches QML


def test_overlay_opacity_scale_clamps_in_real_qml_root():
    from PySide6.QtWidgets import QApplication

    from src.gui.lightweight_viewport import LightweightDrawingViewport

    _ = QApplication.instance() or QApplication([])
    viewport = LightweightDrawingViewport()
    root = viewport._quick.rootObject()
    assert root is not None

    viewport.set_overlay_opacity_scale(0.2)  # below 0.3 floor
    assert viewport.overlay_opacity_scale == pytest.approx(0.3)
    assert root.property("overlayOpacityScale") == pytest.approx(0.3)

    viewport.set_overlay_opacity_scale(1.5)  # above 1.0 ceiling
    assert viewport.overlay_opacity_scale == pytest.approx(1.0)
    assert root.property("overlayOpacityScale") == pytest.approx(1.0)


def test_set_color_mode_propagates_dark_mode_to_qml_root():
    """L3 — the CAD colour toggle drives the QML root's darkMode (black bg +
    native colours) and re-normalises the loaded primitives, instantly + both
    ways, without a reload."""
    from PySide6.QtWidgets import QApplication

    from src.gui.lightweight_viewport import (
        _SKELETON_INK_DARK,
        LightweightDrawingViewport,
    )

    _ = QApplication.instance() or QApplication([])
    viewport = LightweightDrawingViewport()
    root = viewport._quick.rootObject()
    assert root is not None
    assert viewport.color_mode == "light"
    assert root.property("darkMode") is False

    # Seed raw primitives so the toggle re-normalises them.
    viewport._raw_primitives = [
        {"type": "lines", "geometry": [], "properties": {"color": "#ffffff"}},
        {"type": "lines", "geometry": [], "properties": {"color": "#000000"}},
    ]

    viewport.set_color_mode("dark")
    assert viewport.color_mode == "dark"
    assert root.property("darkMode") is True
    prims = root.property("primitives")
    assert prims[0]["properties"]["color"] == "#ffffff"        # native preserved
    assert prims[1]["properties"]["color"] == _SKELETON_INK_DARK  # black lifted

    viewport.set_color_mode("light")
    assert viewport.color_mode == "light"
    assert root.property("darkMode") is False
