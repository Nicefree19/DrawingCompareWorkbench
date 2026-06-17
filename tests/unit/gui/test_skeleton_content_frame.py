# -*- coding: utf-8 -*-
"""Skeleton-only preview must get the content-aware camera frame.

Live test 2026-06-18 (AC1027 multi-detail pair): the raster background was
skipped (fast budget: render_skipped_large_cad_fast_budget), so the skeleton
loaded with the full ~678k-mm multi-detail world_bbox and QML fit-to-view made
every change sub-pixel -> BLANK preview. The proven content-frame applier
(apply_shared_lightweight_camera_frame) was only called on the raster-load path.
This pins that the SKELETON load path now applies it too, once per pair, and not
over a raster-framed pair.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class _FakeViewport:
    def set_fidelity_state(self, *a, **k):
        pass

    def load_scene_pack(self, *a, **k):
        pass


def _make_state():
    scene_pack = SimpleNamespace(overview_lod0_path="lod0.json", primitive_count=42)
    return SimpleNamespace(
        scene_pack_ref=scene_pack,
        last_build_ms=5.0,
        cache_hit=False,
        render_mode="skeleton_preview",
    )


def test_skeleton_path_applies_content_frame_once_when_raster_skipped(qapp, monkeypatch):
    wb = DrawingCompareWorkbenchV2()
    try:
        wb.preview_before_lightweight_v2 = _FakeViewport()
        wb.preview_after_lightweight_v2 = _FakeViewport()
        wb._viewer_session = SimpleNamespace(get_pair_state=lambda pair_id, side: _make_state())
        wb._active_zone_id = ""
        monkeypatch.setattr(wb, "_is_lightweight_viewer_active_v2", lambda: True)
        monkeypatch.setattr(wb, "_push_overlays_to_lightweight_v2", lambda *a, **k: None)

        calls: list = []
        monkeypatch.setattr(
            "src.gui.workbench_visual_extensions.apply_shared_lightweight_camera_frame",
            lambda workbench, viewer_pair: calls.append(viewer_pair),
        )

        pid = "pair_skeleton"
        wb._viewer_pairs_by_id = {pid: {"pair_id": pid}}

        # Raster was skipped (pair not in _lightweight_raster_pairs) -> skeleton
        # load must apply the content frame exactly once.
        wb._apply_session_state_to_viewport_v2(pid, "before", "skeleton_preview")
        assert len(calls) == 1
        assert pid in wb._lightweight_skeleton_framed_pairs

        # The other side's later state push must NOT re-frame (would override a
        # user's zone zoom).
        wb._apply_session_state_to_viewport_v2(pid, "after", "skeleton_preview")
        assert len(calls) == 1
    finally:
        wb.deleteLater()


def test_skeleton_path_skips_frame_for_raster_pair(qapp, monkeypatch):
    """A pair whose raster loaded is framed by the raster path; the skeleton path
    must not double-frame it."""
    wb = DrawingCompareWorkbenchV2()
    try:
        wb.preview_before_lightweight_v2 = _FakeViewport()
        wb.preview_after_lightweight_v2 = _FakeViewport()
        wb._viewer_session = SimpleNamespace(get_pair_state=lambda pair_id, side: _make_state())
        wb._active_zone_id = ""
        monkeypatch.setattr(wb, "_is_lightweight_viewer_active_v2", lambda: True)
        monkeypatch.setattr(wb, "_push_overlays_to_lightweight_v2", lambda *a, **k: None)

        calls: list = []
        monkeypatch.setattr(
            "src.gui.workbench_visual_extensions.apply_shared_lightweight_camera_frame",
            lambda workbench, viewer_pair: calls.append(viewer_pair),
        )

        pid = "pair_raster"
        wb._viewer_pairs_by_id = {pid: {"pair_id": pid}}
        wb._lightweight_raster_pairs.add(pid)  # raster path owns framing

        wb._apply_session_state_to_viewport_v2(pid, "before", "skeleton_preview")
        assert calls == []
        assert pid not in wb._lightweight_skeleton_framed_pairs
    finally:
        wb.deleteLater()
