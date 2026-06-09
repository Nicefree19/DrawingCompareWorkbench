# -*- coding: utf-8 -*-
"""Unit tests for content-aware overview framing.

Frames the viewer to the PRIMARY change zone (not the union of all zones) so a
multi-detail sheet whose changes span the full extent still gets a real zoom, and
both panes stay aligned. Bboxes use the real overlay ``{min_x,min_y,max_x,max_y}``
dict form; ``to_world`` mimics the CAD pass-through of convert_bbox_to_world_space.
"""

from __future__ import annotations

from src.services.comparison.content_frame import (
    cluster_zone_bboxes,
    content_frame_from_zone_bboxes,
)
from src.services.comparison.transform import normalise_bbox


def _to_world(raw, space, dpi):
    # CAD pass-through (empty coordinate space), like convert_bbox_to_world_space.
    return normalise_bbox(raw)


def _box(x0, y0, x1, y1):
    return {"min_x": x0, "min_y": y0, "max_x": x1, "max_y": y1}


# Three real-like clusters: far-left notes, mid-right, far-right (rank 1 = far-right).
FAR_LEFT = _box(3277.0, 4326.0, 3387.0, 4436.0)
MID_RIGHT = _box(362269.0, -89681.0, 377971.0, -72373.0)
FAR_RIGHT = _box(444054.0, -93694.0, 459756.0, -76385.0)


def test_frames_to_primary_zone_not_union():
    overlays = [
        {"zone_id": "C-005", "change_type": "moved", "priority_rank": 6, "old_bbox": FAR_LEFT, "bbox": FAR_LEFT},
        {"zone_id": "C-002", "change_type": "added", "priority_rank": 2, "old_bbox": None, "bbox": MID_RIGHT},
        {"zone_id": "C-001", "change_type": "deleted", "priority_rank": 1, "old_bbox": FAR_RIGHT, "bbox": FAR_RIGHT},
    ]
    frame = content_frame_from_zone_bboxes(overlays, _to_world)
    assert frame is not None
    # Centered on the rank-1 (far-right) zone, NOT a union spanning to far-left.
    assert frame[0] > 400000.0, "frame must not span back to the far-left cluster"
    assert frame[0] <= 444054.0 and frame[2] >= 459756.0  # contains C-001
    # A real zoom: far smaller than the ~459k-wide sheet.
    assert (frame[2] - frame[0]) < 60000.0


def test_primary_chosen_by_priority_rank():
    overlays = [
        {"change_type": "added", "priority_rank": 5, "bbox": FAR_LEFT},
        {"change_type": "deleted", "priority_rank": 1, "old_bbox": FAR_RIGHT, "bbox": FAR_RIGHT},
    ]
    frame = content_frame_from_zone_bboxes(overlays, _to_world)
    assert frame is not None and frame[0] > 400000.0  # rank-1 far-right wins


def test_primary_falls_back_to_score_then_order_without_rank():
    overlays = [
        {"change_type": "added", "priority_score": 100.0, "bbox": FAR_LEFT},
        {"change_type": "deleted", "priority_score": 900.0, "old_bbox": FAR_RIGHT, "bbox": FAR_RIGHT},
    ]
    frame = content_frame_from_zone_bboxes(overlays, _to_world)
    assert frame is not None and frame[0] > 400000.0  # higher score wins


def test_deleted_uses_old_bbox_added_uses_new_bbox():
    # Deleted primary: only old_bbox is meaningful; new is degenerate → use old.
    overlays = [
        {"change_type": "deleted", "priority_rank": 1, "old_bbox": FAR_RIGHT, "bbox": _box(0, 0, 0, 0)},
    ]
    frame = content_frame_from_zone_bboxes(overlays, _to_world)
    assert frame is not None and frame[0] > 400000.0  # framed by old_bbox, not [0,0,0,0]


def test_degenerate_primary_skips_to_next_zone():
    overlays = [
        {"change_type": "moved", "priority_rank": 1, "old_bbox": _box(0, 0, 0, 0), "bbox": None},
        {"change_type": "deleted", "priority_rank": 2, "old_bbox": FAR_RIGHT, "bbox": FAR_RIGHT},
    ]
    frame = content_frame_from_zone_bboxes(overlays, _to_world)
    assert frame is not None and frame[0] > 400000.0  # rank-1 unusable → rank-2 used


def test_padding_expands_around_primary():
    overlays = [{"change_type": "deleted", "priority_rank": 1, "old_bbox": FAR_RIGHT, "bbox": FAR_RIGHT}]
    frame = content_frame_from_zone_bboxes(overlays, _to_world, padding_ratio=0.4)
    # 0.4 padding each side → width grows by ~80% of the zone width.
    zone_w = 459756.0 - 444054.0
    assert (frame[2] - frame[0]) > zone_w * 1.7


def test_empty_or_unusable_returns_none():
    assert content_frame_from_zone_bboxes([], _to_world) is None
    assert content_frame_from_zone_bboxes(
        [{"change_type": "moved", "old_bbox": _box(0, 0, 0, 0), "bbox": None}], _to_world
    ) is None


def test_modified_zone_frames_both_sides_tightly():
    # A genuine MODIFIED detail (both sides present at ~the same position — the
    # shared-origin case the registered DXFs always produce): the frame must
    # contain both before+after and stay a tight zoom (both panes overlay-align).
    overlays = [
        {"change_type": "modified", "priority_rank": 1,
         "old_bbox": _box(100.0, 100.0, 200.0, 200.0),
         "bbox": _box(108.0, 104.0, 208.0, 204.0)},
    ]
    frame = content_frame_from_zone_bboxes(overlays, _to_world)
    assert frame is not None
    assert frame[0] <= 100.0 and frame[1] <= 100.0 and frame[2] >= 208.0 and frame[3] >= 204.0
    assert (frame[2] - frame[0]) < 400.0  # tight zoom around the change, not the sheet


def test_moved_zone_frames_both_positions_so_the_move_is_visible():
    # A reposition (before at A, after at the shifted B): the frame spans BOTH so
    # the user sees the move; both panes share that one frame (aligned world window).
    overlays = [
        {"change_type": "moved", "priority_rank": 1,
         "old_bbox": _box(100.0, 100.0, 200.0, 200.0),
         "bbox": _box(900.0, 100.0, 1000.0, 200.0)},
    ]
    frame = content_frame_from_zone_bboxes(overlays, _to_world)
    assert frame is not None
    assert frame[0] <= 100.0 and frame[2] >= 1000.0  # spans both A and B


# --- cluster_zone_bboxes (spatial detail clusters for the navigator) ---

# Real-like: far-left notes (3), mid-right (2), far-right (2) — must split into 3.
_REAL_ZONES = [
    {"zone_id": "C-001", "change_type": "deleted", "old_bbox": _box(444054, -93694, 459756, -76385), "bbox": _box(444054, -93694, 459756, -76385)},
    {"zone_id": "C-003", "change_type": "deleted", "old_bbox": _box(452082, -75921, 452192, -75811), "bbox": _box(452082, -75921, 452192, -75811)},
    {"zone_id": "C-002", "change_type": "added", "old_bbox": None, "bbox": _box(362269, -89681, 377971, -72373)},
    {"zone_id": "C-004", "change_type": "added", "old_bbox": None, "bbox": _box(370297, -71909, 370407, -71799)},
    {"zone_id": "C-005", "change_type": "moved", "old_bbox": _box(3277, 4326, 3387, 4436), "bbox": _box(3277, 4326, 3387, 4436)},
    {"zone_id": "C-006", "change_type": "moved", "old_bbox": _box(7092, 4528, 7202, 4638), "bbox": _box(7092, 4528, 7202, 4638)},
    {"zone_id": "C-007", "change_type": "moved", "old_bbox": _box(4724, 4658, 4967, 4968), "bbox": _box(4724, 4658, 4967, 4968)},
]


def test_clusters_split_into_distinct_detail_groups():
    clusters = cluster_zone_bboxes(_REAL_ZONES, _to_world)
    groups = sorted(sorted(c["zone_ids"]) for c in clusters)
    assert groups == [["C-001", "C-003"], ["C-002", "C-004"], ["C-005", "C-006", "C-007"]]
    # Ordered left-to-right by x; far-left notes first.
    assert clusters[0]["zone_ids"] and clusters[0]["bbox"][0] < clusters[-1]["bbox"][0]


def test_clusters_outlier_zone_does_not_collapse_others():
    # One far outlier zone must NOT bridge the two tight clusters into one.
    zones = [
        {"zone_id": "A1", "change_type": "added", "bbox": _box(0, 0, 100, 100)},
        {"zone_id": "A2", "change_type": "added", "bbox": _box(150, 0, 250, 100)},
        {"zone_id": "B1", "change_type": "added", "bbox": _box(500000, 0, 500100, 100)},
    ]
    clusters = cluster_zone_bboxes(zones, _to_world)
    groups = sorted(sorted(c["zone_ids"]) for c in clusters)
    assert groups == [["A1", "A2"], ["B1"]]


def test_clusters_single_group_when_no_clean_separation():
    # Evenly-spaced zones (no large relative jump) → one cluster, not over-split.
    zones = [
        {"zone_id": f"Z{i}", "change_type": "added", "bbox": _box(i * 1000.0, 0, i * 1000.0 + 100, 100)}
        for i in range(5)
    ]
    clusters = cluster_zone_bboxes(zones, _to_world)
    assert len(clusters) == 1
    assert sorted(clusters[0]["zone_ids"]) == ["Z0", "Z1", "Z2", "Z3", "Z4"]


def test_clusters_empty_and_single():
    assert cluster_zone_bboxes([], _to_world) == []
    one = cluster_zone_bboxes([{"zone_id": "X", "change_type": "added", "bbox": _box(0, 0, 100, 100)}], _to_world)
    assert len(one) == 1 and one[0]["zone_ids"] == ["X"] and one[0]["count"] == 1


def test_clusters_caps_zone_count_on_dense_pairs():
    # A dense pair (many tiny zones) must not blow up the O(n^2) clustering: the
    # cap keeps only the largest-area zones (the most significant changes).
    zones = [
        {"zone_id": f"S{i}", "change_type": "added", "bbox": _box(i * 10.0, 0.0, i * 10.0 + 5.0, 5.0)}
        for i in range(50)
    ]
    zones += [
        {"zone_id": "BIG1", "change_type": "added", "bbox": _box(0.0, 1000.0, 500.0, 1500.0)},
        {"zone_id": "BIG2", "change_type": "added", "bbox": _box(9000.0, 1000.0, 9500.0, 1500.0)},
    ]
    clusters = cluster_zone_bboxes(zones, _to_world, max_zones=4)
    assert sum(c["count"] for c in clusters) <= 4
    kept = {z for c in clusters for z in c["zone_ids"]}
    assert "BIG1" in kept and "BIG2" in kept  # biggest-area zones survive the cap
