# -*- coding: utf-8 -*-
"""P0-3 — shared viewer frame selection (headless).

Reproduces the real-data divergence (before x_min≈-60.8 vs after x_min≈-81846)
and asserts both viewports would receive ONE shared frame covering both — the
fix for '한쪽만 보임'. The GUI wiring applies this via set_camera_to_world_bbox.
"""

from __future__ import annotations

from src.services.comparison.viewer_frame import (
    apply_shared_camera_frame,
    shared_world_bbox_from_transforms,
)

# Mirrors the user's real run (flat min/max transform dicts as the DXF pipeline emits).
_BEFORE = {"min_x": -60.8, "min_y": -89687.4, "max_x": 377977.7, "max_y": 8562.7}
_AFTER = {"min_x": -81846.1, "min_y": -89687.4, "max_x": 377977.7, "max_y": 12575.2}


def test_shared_frame_is_union_covering_both_sides() -> None:
    shared = shared_world_bbox_from_transforms(_BEFORE, _AFTER)
    assert shared is not None
    x0, y0, x1, y1 = shared
    # Covers the leftmost (after) and the common right/top — ONE frame for both.
    assert x0 == -81846.1
    assert y0 == -89687.4
    assert x1 == 377977.7
    assert y1 == 12575.2
    # Both sides' content lies within the shared frame (no 'one-side-only').
    for t in (_BEFORE, _AFTER):
        assert x0 <= t["min_x"] and t["max_x"] <= x1
        assert y0 <= t["min_y"] and t["max_y"] <= y1


def test_single_valid_side_used_when_other_blank() -> None:
    shared = shared_world_bbox_from_transforms(_BEFORE, None)
    assert shared == (-60.8, -89687.4, 377977.7, 8562.7)
    shared2 = shared_world_bbox_from_transforms({}, _AFTER)
    assert shared2 == (-81846.1, -89687.4, 377977.7, 12575.2)


def test_no_usable_transforms_returns_none() -> None:
    assert shared_world_bbox_from_transforms(None, None) is None
    assert shared_world_bbox_from_transforms({}, {"min_x": 0, "min_y": 0, "max_x": 0, "max_y": 0}) is None


class _FakeViewport:
    def __init__(self, raise_on_set: bool = False) -> None:
        self.bbox = None
        self._raise = raise_on_set

    def set_camera_to_world_bbox(self, bbox) -> None:
        if self._raise:
            raise RuntimeError("boom")
        self.bbox = bbox


def test_apply_shared_camera_frame_sets_both_viewports() -> None:
    a, b = _FakeViewport(), _FakeViewport()
    shared = apply_shared_camera_frame(_BEFORE, _AFTER, a, b)
    assert shared == (-81846.1, -89687.4, 377977.7, 12575.2)
    assert a.bbox == shared and b.bbox == shared


def test_apply_shared_camera_frame_is_defensive() -> None:
    # A viewport that raises must NOT break the others or the call (never crash load).
    bad, good = _FakeViewport(raise_on_set=True), _FakeViewport()
    shared = apply_shared_camera_frame(_BEFORE, _AFTER, bad, None, good)
    assert shared is not None
    assert good.bbox == shared  # good side still set despite bad side raising


def test_apply_shared_camera_frame_no_frame_is_noop() -> None:
    vp = _FakeViewport()
    assert apply_shared_camera_frame(None, None, vp) is None
    assert vp.bbox is None


def test_world_bbox_list_form_also_supported() -> None:
    # transform may carry an explicit world_bbox list instead of flat min/max.
    before = {"world_bbox": [0.0, 0.0, 100.0, 50.0]}
    after = {"world_bbox": [-200.0, 0.0, 100.0, 80.0]}
    shared = shared_world_bbox_from_transforms(before, after)
    assert shared == (-200.0, 0.0, 100.0, 80.0)
