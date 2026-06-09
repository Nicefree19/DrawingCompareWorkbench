# -*- coding: utf-8 -*-
"""Tests for the cluster navigator (spatial detail-cluster jump strip)."""

from __future__ import annotations

from src.gui.cluster_navigator import (
    ClusterNavigator,
    _pan_both_viewports,
    update_cluster_navigator,
)


def _box(x0, y0, x1, y1):
    return {"min_x": x0, "min_y": y0, "max_x": x1, "max_y": y1}


# Real-like: far-left notes (3) + mid-right (2) + far-right (2) → 3 clusters.
_REAL_ZONES = [
    {"zone_id": "C-001", "change_type": "deleted", "old_bbox": _box(444054, -93694, 459756, -76385), "bbox": _box(444054, -93694, 459756, -76385)},
    {"zone_id": "C-003", "change_type": "deleted", "old_bbox": _box(452082, -75921, 452192, -75811), "bbox": _box(452082, -75921, 452192, -75811)},
    {"zone_id": "C-002", "change_type": "added", "bbox": _box(362269, -89681, 377971, -72373)},
    {"zone_id": "C-004", "change_type": "added", "bbox": _box(370297, -71909, 370407, -71799)},
    {"zone_id": "C-005", "change_type": "moved", "old_bbox": _box(3277, 4326, 3387, 4436), "bbox": _box(3277, 4326, 3387, 4436)},
    {"zone_id": "C-006", "change_type": "moved", "old_bbox": _box(7092, 4528, 7202, 4638), "bbox": _box(7092, 4528, 7202, 4638)},
    {"zone_id": "C-007", "change_type": "moved", "old_bbox": _box(4724, 4658, 4967, 4968), "bbox": _box(4724, 4658, 4967, 4968)},
]


class _FakeNav:
    def __init__(self):
        self.clusters = None
        self.on_pan = None
        self.cleared = 0

    def set_clusters(self, clusters, on_pan):
        self.clusters = clusters
        self.on_pan = on_pan

    def clear(self):
        self.cleared += 1
        self.clusters = []


class _FakeViewport:
    world_bbox = (0.0, 0.0, 0.0, 0.0)

    def __init__(self):
        self.cam = []

    def set_camera_to_world_bbox(self, bbox):
        self.cam.append(bbox)


class _FakeWorkbench:
    def __init__(self, overlays):
        self.cluster_navigator_v2 = _FakeNav()
        self._active_overlays_by_zone = {o["zone_id"]: o for o in overlays}
        self.preview_before_lightweight_v2 = _FakeViewport()
        self.preview_after_lightweight_v2 = _FakeViewport()


def test_update_populates_three_clusters_and_pan_moves_both_viewports():
    wb = _FakeWorkbench(_REAL_ZONES)
    update_cluster_navigator(wb)
    clusters = wb.cluster_navigator_v2.clusters
    assert clusters is not None and len(clusters) == 3
    groups = sorted(sorted(c["zone_ids"]) for c in clusters)
    assert groups == [["C-001", "C-003"], ["C-002", "C-004"], ["C-005", "C-006", "C-007"]]

    # The pan callback moves BOTH viewports to one shared cluster frame.
    wb.cluster_navigator_v2.on_pan(clusters[0]["bbox"])
    assert wb.preview_before_lightweight_v2.cam == wb.preview_after_lightweight_v2.cam
    assert len(wb.preview_before_lightweight_v2.cam) == 1


def test_update_clears_navigator_when_no_overlays():
    wb = _FakeWorkbench([])
    wb._active_overlays_by_zone = {}
    update_cluster_navigator(wb)
    assert wb.cluster_navigator_v2.cleared == 1


def test_update_is_safe_without_navigator_attribute():
    class _Bare:
        _active_overlays_by_zone = {}

    update_cluster_navigator(_Bare())  # must not raise


def test_update_skips_pdf_pairs():
    # PDF overlays (image_pixels) lack a usable page height at overlay-set time,
    # and PDF is page-scale anyway → the navigator must be cleared, not populated.
    pdf_zones = [
        {"zone_id": "P1", "change_type": "modified", "bbox_coordinate_space": "image_pixels",
         "pdf_dpi": 200, "old_bbox": _box(0, 0, 10, 10), "bbox": _box(0, 0, 10, 10)},
        {"zone_id": "P2", "change_type": "modified", "bbox_coordinate_space": "image_pixels",
         "pdf_dpi": 200, "old_bbox": _box(500, 500, 510, 510), "bbox": _box(500, 500, 510, 510)},
    ]
    wb = _FakeWorkbench(pdf_zones)
    update_cluster_navigator(wb)
    assert wb.cluster_navigator_v2.cleared == 1
    assert wb.cluster_navigator_v2.clusters in (None, [])


def test_pan_both_viewports_sets_sync_guard():
    wb = _FakeWorkbench(_REAL_ZONES)
    _pan_both_viewports(wb, (1.0, 2.0, 3.0, 4.0))
    assert wb.preview_before_lightweight_v2.cam == [(1.0, 2.0, 3.0, 4.0)]
    assert wb.preview_after_lightweight_v2.cam == [(1.0, 2.0, 3.0, 4.0)]
    assert wb._lightweight_camera_sync_in_progress is False  # reset after


def test_navigator_widget_shows_button_per_cluster_and_click_pans(qapp):
    nav = ClusterNavigator()
    panned = []
    nav.set_clusters(
        [
            {"bbox": (0.0, 0.0, 100.0, 100.0), "count": 2, "zone_ids": ["a", "b"]},
            {"bbox": (5000.0, 0.0, 5100.0, 100.0), "count": 3, "zone_ids": ["c", "d", "e"]},
        ],
        lambda bbox: panned.append(bbox),
    )
    assert len(nav._buttons) == 2
    nav._buttons[1].click()
    assert panned == [(5000.0, 0.0, 5100.0, 100.0)]


def test_navigator_widget_hidden_for_single_cluster(qapp):
    nav = ClusterNavigator()
    nav.set_clusters([{"bbox": (0.0, 0.0, 100.0, 100.0), "count": 1, "zone_ids": ["a"]}], lambda b: None)
    assert nav._buttons == []
    assert nav.isVisible() is False
