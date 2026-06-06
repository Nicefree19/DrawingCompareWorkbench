# -*- coding: utf-8 -*-
"""DXF/raster pairs must push a real raster background into the lightweight viewer.

Verification-gap closeout (Phase C1): a large DWG live test showed the lightweight
viewer in "상대 위치 모드 · raster preview" (relative-only, no real background). The
code path to show a real CAD background already exists —
``_load_lightweight_raster_preview_v2`` resolves the rendered ``before_image`` /
``after_image`` PNG + the world transform and calls ``load_raster_image`` on each
lightweight viewport, then sets fidelity to ``raster_refined``. The huge DWG
fell back because its full raster render was pending/failed (an honest fallback).

These tests lock in that path: when a rendered raster + transform exist, the
lightweight viewer is fed the real background (so DXF zone-focus zoom lands on
actual geometry like the PDF case); when a side's raster is missing, that side
honestly degrades to ``relative_only``.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2


class _FakeViewport:
    def __init__(self):
        self.raster_calls: list[tuple] = []
        self.fidelity: list[tuple] = []

    def load_raster_image(self, image_path, *, world_bbox=None, empty_notice=""):
        self.raster_calls.append((image_path, world_bbox))
        return image_path is not None  # mirrors real: None path -> not loaded

    def set_fidelity_state(self, mode, status_text=""):
        self.fidelity.append((mode, status_text))


def _make_fake():
    before = _FakeViewport()
    after = _FakeViewport()
    ns = SimpleNamespace(
        preview_before_lightweight_v2=before,
        preview_after_lightweight_v2=after,
        _viewer_root=None,
        _lightweight_raster_pairs=set(),
        _active_zone_id="",
        _push_overlays_to_lightweight_v2=lambda *a, **k: None,
    )
    ns._transform_world_bbox_v2 = (
        lambda transform: DrawingCompareWorkbenchV2._transform_world_bbox_v2(ns, transform)
    )
    return ns, before, after


_TX = {"min_x": 0.0, "min_y": 0.0, "max_x": 100.0, "max_y": 50.0,
       "img_width": 200.0, "img_height": 100.0}


def test_raster_preview_pushes_real_background_with_world_bbox():
    """Both sides have a rendered PNG + transform -> real raster background loaded
    into both lightweight viewports with the world bbox, fidelity raster_refined."""

    ns, before, after = _make_fake()
    viewer_pair = {
        "before_image": "C:/render/cold_before.png",
        "after_image": "C:/render/cold_after.png",
        "before_transform": dict(_TX),
        "after_transform": dict(_TX),
    }

    stats = DrawingCompareWorkbenchV2._load_lightweight_raster_preview_v2(
        ns, "pair-1", viewer_pair
    )

    # both viewports got a load_raster_image call with the resolved path + world bbox
    assert len(before.raster_calls) == 1 and len(after.raster_calls) == 1
    bpath, bbbox = before.raster_calls[0]
    assert str(bpath).endswith("cold_before.png")
    assert bbbox == (0.0, 0.0, 100.0, 50.0)  # from _transform_world_bbox_v2
    assert str(after.raster_calls[0][0]).endswith("cold_after.png")
    # both report the real-background fidelity, not relative-only
    assert before.fidelity[-1][0] == "raster_refined"
    assert after.fidelity[-1][0] == "raster_refined"
    assert "pair-1" in ns._lightweight_raster_pairs
    assert stats["loaded_before"] is True and stats["loaded_after"] is True


def test_raster_preview_degrades_missing_side_to_relative_only():
    """When one side's rendered raster is missing, that side honestly degrades to
    relative_only while the other still shows the real background."""

    ns, before, after = _make_fake()
    viewer_pair = {
        "before_image": "C:/render/only_before.png",
        "after_image": "",  # missing -> _resolve returns None -> not loaded
        "before_transform": dict(_TX),
        "after_transform": dict(_TX),
    }

    stats = DrawingCompareWorkbenchV2._load_lightweight_raster_preview_v2(
        ns, "pair-2", viewer_pair
    )

    assert before.fidelity[-1][0] == "raster_refined"
    assert after.fidelity[-1][0] == "relative_only"  # honest degradation
    assert stats["loaded_before"] is True and stats["loaded_after"] is False
    assert "pair-2" in ns._lightweight_raster_pairs


def test_raster_preview_noop_when_lightweight_viewports_absent():
    """No lightweight viewports -> no-op stats, never raises."""

    ns = SimpleNamespace(
        preview_before_lightweight_v2=None,
        preview_after_lightweight_v2=None,
    )
    stats = DrawingCompareWorkbenchV2._load_lightweight_raster_preview_v2(
        ns, "pair-3", {"before_image": "C:/x.png", "before_transform": dict(_TX)}
    )
    assert stats["loaded_before"] is False and stats["loaded_after"] is False
