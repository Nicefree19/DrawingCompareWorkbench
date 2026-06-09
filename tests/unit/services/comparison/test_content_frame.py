# -*- coding: utf-8 -*-
"""Unit tests for content-aware overview framing.

Frames the viewer to the PRIMARY change zone (not the union of all zones) so a
multi-detail sheet whose changes span the full extent still gets a real zoom, and
both panes stay aligned. Bboxes use the real overlay ``{min_x,min_y,max_x,max_y}``
dict form; ``to_world`` mimics the CAD pass-through of convert_bbox_to_world_space.
"""

from __future__ import annotations

from src.services.comparison.content_frame import content_frame_from_zone_bboxes
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
