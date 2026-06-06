# -*- coding: utf-8 -*-
"""Surfacing the per-zone crop render in the lightweight viewer.

Root cause of "PDF는 되는데 DWG는 변경부위 포커싱이 안 됨": the zone-crop worker
produces a crisp per-zone PNG (``before_image``/``after_image``) framed by a
CAD-world window, but ``_on_zone_crop_render_finished_v2`` only loaded it into the
LEGACY GPU viewport — a block gated ``if not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY``.
On QtQuick machines (lightweight-only) that block is skipped, so the crisp crop
rendered and was thrown away; the lightweight viewer kept magnifying the
fixed-resolution full-drawing raster into pixel-mush (and off-frame zones showed
nothing). ``_apply_zone_crop_to_lightweight_v2`` surfaces the crop on the active
lightweight surface.

These tests lock in: a ``ready`` (real vector) crop is loaded into BOTH
lightweight viewports framed by its world transform and the camera re-focuses on
the change; PDF crops (``pdf_render``) and crop fallbacks are left to their own
paths; and a relative-only zone after a prior crop restores the full-drawing
raster instead of stranding the camera on a stale neighbouring crop.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2


class _FakeViewport:
    def __init__(self):
        self.raster_calls: list[tuple] = []
        self.fidelity: list[tuple] = []
        self.camera_calls: list[tuple] = []

    def load_raster_image(self, image_path, *, world_bbox=None, empty_notice=""):
        self.raster_calls.append((image_path, world_bbox))
        return image_path is not None  # mirrors real: None path -> not loaded

    def set_fidelity_state(self, mode, status_text=""):
        self.fidelity.append((mode, status_text))

    def set_camera_to_world_bbox(self, world_bbox, padding_ratio=0.0):
        self.camera_calls.append((world_bbox, padding_ratio))


def _make_fake():
    before = _FakeViewport()
    after = _FakeViewport()
    ns = SimpleNamespace(
        preview_before_lightweight_v2=before,
        preview_after_lightweight_v2=after,
        _viewer_root=None,
        _lightweight_raster_pairs=set(),
        _viewer_pairs_by_id={},
        push_calls=[],
        focus_calls=[],
        restore_calls=[],
    )
    ns._transform_world_bbox_v2 = (
        lambda transform: DrawingCompareWorkbenchV2._transform_world_bbox_v2(ns, transform)
    )
    ns._push_overlays_to_lightweight_v2 = (
        lambda pid, focus_zone_id="": ns.push_calls.append((pid, focus_zone_id))
    )
    ns._focus_lightweight_on_zone_v2 = (
        lambda zone_id: ns.focus_calls.append(zone_id)
    )
    ns._load_lightweight_raster_preview_v2 = (
        lambda pid, vp: ns.restore_calls.append(pid)
    )
    return ns, before, after


def _call(ns, pair_id, zone_id, payload, status):
    DrawingCompareWorkbenchV2._apply_zone_crop_to_lightweight_v2(
        ns, pair_id, zone_id, payload, status
    )


_TX_BEFORE = {"min_x": 10.0, "min_y": 20.0, "max_x": 110.0, "max_y": 70.0,
              "img_width": 1400.0, "img_height": 700.0}
_TX_AFTER = {"min_x": 12.0, "min_y": 22.0, "max_x": 112.0, "max_y": 72.0,
             "img_width": 1400.0, "img_height": 700.0}
_READY_PAYLOAD = {
    "before_image": "C:/render/zone_C004_before.png",
    "after_image": "C:/render/zone_C004_after.png",
    "before_transform": dict(_TX_BEFORE),
    "after_transform": dict(_TX_AFTER),
}


def test_ready_crop_loads_into_both_lightweight_viewports_and_refocuses():
    ns, before, after = _make_fake()

    _call(ns, "pair-1", "C-004", dict(_READY_PAYLOAD), "ready")

    # each viewport got its OWN crop PNG framed by its OWN world transform
    assert len(before.raster_calls) == 1 and len(after.raster_calls) == 1
    bpath, bbbox = before.raster_calls[0]
    assert str(bpath).endswith("zone_C004_before.png")
    assert bbbox == (10.0, 20.0, 110.0, 70.0)
    apath, abbox = after.raster_calls[0]
    assert str(apath).endswith("zone_C004_after.png")
    assert abbox == (12.0, 22.0, 112.0, 72.0)
    # real-render fidelity, pair tracked as a raster/crop pair
    assert before.fidelity[-1][0] == "raster_refined"
    assert after.fidelity[-1][0] == "raster_refined"
    assert "pair-1" in ns._lightweight_raster_pairs
    assert ns._lightweight_zone_crop_pair_v2 == "pair-1"
    # overlays re-pushed with the zone as focus
    assert ns.push_calls == [("pair-1", "C-004")]
    # camera framed on each side's loaded crop WINDOW (guaranteed-visible), not
    # the tiny change bbox (which left the crop off-frame / blank in live runs)
    assert before.camera_calls and before.camera_calls[-1][0] == (10.0, 20.0, 110.0, 70.0)
    assert after.camera_calls and after.camera_calls[-1][0] == (12.0, 22.0, 112.0, 72.0)


def test_pdf_render_status_is_left_to_pdf_path():
    """PDF crops keep their full-page + re-render-on-zoom path: no raster load,
    no restore (the lightweight crop framing would misalign PDF markers)."""

    ns, before, after = _make_fake()

    _call(ns, "pair-pdf", "C-004", dict(_READY_PAYLOAD), "pdf_render")

    assert before.raster_calls == [] and after.raster_calls == []
    assert ns.restore_calls == []
    assert getattr(ns, "_lightweight_zone_crop_pair_v2", "") == ""


def test_relative_only_after_prior_crop_restores_full_raster():
    """A relative-only zone selected AFTER a crisp crop was shown must not be left
    sitting on the prior zone's crop (wrong region). Restore the full raster."""

    ns, before, after = _make_fake()
    ns._lightweight_zone_crop_pair_v2 = "pair-1"  # a prior crop is on screen
    ns._viewer_pairs_by_id["pair-1"] = {
        "coordinate_source": "world", "source_a": "a.dxf", "source_b": "b.dxf",
    }

    _call(ns, "pair-1", "C-009", dict(_READY_PAYLOAD), "relative_only")

    assert ns.restore_calls == ["pair-1"]  # full raster restored
    assert ns.focus_calls == ["C-009"]  # camera re-fit on the relative-only zone
    assert ns._lightweight_zone_crop_pair_v2 == ""  # crop no longer on screen


def test_relative_only_without_prior_crop_is_noop():
    """First zone of a pair is relative-only: full raster already showing from
    pair-load, so nothing to restore."""

    ns, before, after = _make_fake()  # _lightweight_zone_crop_pair_v2 unset

    _call(ns, "pair-1", "C-009", dict(_READY_PAYLOAD), "relative_only")

    assert ns.restore_calls == []
    assert before.raster_calls == [] and after.raster_calls == []


def test_relative_only_restore_skips_pdf_pair():
    """Defensive: even if a PDF pair were marked crop-active, the restore must not
    drive a PDF pair through the raster path (it has its own page path)."""

    ns, before, after = _make_fake()
    ns._lightweight_zone_crop_pair_v2 = "pair-pdf"
    ns._viewer_pairs_by_id["pair-pdf"] = {
        "coordinate_source": "image_pixels", "source_a": "a.pdf", "source_b": "b.pdf",
    }

    _call(ns, "pair-pdf", "C-009", dict(_READY_PAYLOAD), "relative_only")

    assert ns.restore_calls == []  # PDF pair not restored via raster path
    assert ns._lightweight_zone_crop_pair_v2 == ""  # but the flag is cleared


def test_ready_crop_with_both_images_missing_keeps_relative_only():
    """``ready`` status but neither crop PNG resolves -> honest degradation: no
    fidelity flip to raster_refined, pair not marked as crop-active."""

    ns, before, after = _make_fake()
    payload = {
        "before_image": "",  # -> _resolve returns None -> load returns False
        "after_image": "",
        "before_transform": dict(_TX_BEFORE),
        "after_transform": dict(_TX_AFTER),
    }

    _call(ns, "pair-1", "C-004", payload, "ready")

    # both sides attempted, both resolved to None path -> load returns False
    assert before.raster_calls[0][0] is None and after.raster_calls[0][0] is None
    # neither loaded -> no raster_refined, not tracked, no focus/push
    assert all(mode != "raster_refined" for mode, _ in before.fidelity)
    assert "pair-1" not in ns._lightweight_raster_pairs
    assert getattr(ns, "_lightweight_zone_crop_pair_v2", "") == ""
    assert ns.push_calls == [] and ns.focus_calls == []


def test_blank_side_skipped_when_zone_window_outside_that_side_background():
    """Disjoint-coord DWG: the zone window sits inside the AFTER background but
    far outside the BEFORE background (revised drawing re-originated), so the
    worker wrote a blank white before-crop. The before side must degrade to
    relative_only (NOT paint white); the after side shows the crisp crop."""

    ns, before, after = _make_fake()
    # Full-drawing backgrounds in disjoint world ranges (mirrors the real DWG:
    # before ~(350k,200k), after ~(480k,-100k)).
    ns._viewer_pairs_by_id["pair-1"] = {
        "coordinate_source": "world",
        "source_a": "a.dxf", "source_b": "b.dxf",
        "before_transform": {"min_x": 350000.0, "min_y": 200000.0,
                             "max_x": 400000.0, "max_y": 215000.0,
                             "img_width": 8000.0, "img_height": 1414.0},
        "after_transform": {"min_x": 480000.0, "min_y": -110000.0,
                            "max_x": 532000.0, "max_y": -100000.0,
                            "img_width": 8000.0, "img_height": 1414.0},
    }
    zone_tx = {"min_x": 507000.0, "min_y": -105000.0, "max_x": 517000.0,
               "max_y": -101000.0, "img_width": 1600.0, "img_height": 900.0}
    payload = {
        "before_image": "C:/render/zone_before.png",
        "after_image": "C:/render/zone_after.png",
        "before_transform": dict(zone_tx),
        "after_transform": dict(zone_tx),
        "world_window": {"xmin": 507000.0, "ymin": -105000.0,
                         "xmax": 517000.0, "ymax": -101000.0},
    }

    _call(ns, "pair-1", "C-040", payload, "ready")

    # before side: zone window outside its background -> NOT loaded, honest degrade
    assert before.raster_calls == []
    assert before.fidelity[-1][0] == "relative_only"
    # after side: zone window inside its background -> crisp crop loaded
    assert len(after.raster_calls) == 1
    assert str(after.raster_calls[0][0]).endswith("zone_after.png")
    assert after.fidelity[-1][0] == "raster_refined"
    # one side loaded -> pair tracked, overlays pushed, camera fit on AFTER only
    assert ns._lightweight_zone_crop_pair_v2 == "pair-1"
    assert ns.push_calls == [("pair-1", "C-040")]
    assert before.camera_calls == []  # blank side: no crop, no camera fit
    assert after.camera_calls and after.camera_calls[-1][0] == (507000.0, -105000.0, 517000.0, -101000.0)


def test_noop_when_lightweight_viewports_absent():
    ns = SimpleNamespace(
        preview_before_lightweight_v2=None,
        preview_after_lightweight_v2=None,
    )
    # must not raise
    DrawingCompareWorkbenchV2._apply_zone_crop_to_lightweight_v2(
        ns, "pair-1", "C-004", dict(_READY_PAYLOAD), "ready"
    )


def test_loaded_fidelity_is_real_render_mode_without_watermark():
    """Regression guard for the false '실배경 아님' watermark over a real DWG.

    The lightweight viewport's set_fidelity_state expects a RenderMode and
    runs it through style_for(); an UNRECOGNISED mode falls back to the
    relative_only style (orange badge + watermark). The crop/raster paths used
    the string ``"exact_world_render"`` — which is NOT a RenderMode (it is a
    legacy GpuViewport fidelity string), so the real, crisp DWG drawing got
    branded '상대 위치 모드 · 실배경 아님'. The correct value is
    ``"raster_refined"`` (🔵 실제 렌더, no watermark)."""

    from src.services.comparison.render_modes import RENDER_MODE_STYLES, style_for

    # what the fix now uses: a real mode, no watermark
    assert "raster_refined" in RENDER_MODE_STYLES
    assert style_for("raster_refined").show_watermark is False
    # the old string was never a real mode -> fell back to relative_only+watermark
    assert "exact_world_render" not in RENDER_MODE_STYLES
    assert style_for("exact_world_render").show_watermark is True

    # and the crop path actually feeds the real mode to the viewport
    ns, before, after = _make_fake()
    _call(ns, "pair-1", "C-004", dict(_READY_PAYLOAD), "ready")
    assert after.fidelity[-1][0] in RENDER_MODE_STYLES
    assert style_for(after.fidelity[-1][0]).show_watermark is False
