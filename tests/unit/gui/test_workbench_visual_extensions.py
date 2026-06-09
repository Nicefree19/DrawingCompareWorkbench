# -*- coding: utf-8 -*-
"""Tests for lightweight visual extension hooks."""

from __future__ import annotations

from src.gui.workbench_visual_extensions import apply_shared_lightweight_camera_frame


class _FakeViewport:
    def __init__(self) -> None:
        self.camera_calls = []

    def set_camera_to_world_bbox(self, bbox) -> None:
        self.camera_calls.append(bbox)


class _FakeWorkbench:
    def __init__(self) -> None:
        self.preview_before_lightweight_v2 = _FakeViewport()
        self.preview_after_lightweight_v2 = _FakeViewport()


def test_visual_extension_prefers_sheet_frame_alignment_when_frames_exist() -> None:
    workbench = _FakeWorkbench()

    apply_shared_lightweight_camera_frame(
        workbench,
        {
            "before_transform": {
                "min_x": -1000.0,
                "min_y": -1000.0,
                "max_x": 5000.0,
                "max_y": 5000.0,
            },
            "after_transform": {
                "min_x": 100000.0,
                "min_y": 200000.0,
                "max_x": 105000.0,
                "max_y": 205000.0,
            },
            "before_cad_frame_bbox": [0.0, 0.0, 420.0, 297.0],
            "after_cad_frame_bbox": [1000.0, 2000.0, 1420.0, 2297.0],
        },
    )

    assert workbench.preview_before_lightweight_v2.camera_calls == [
        (0.0, 0.0, 420.0, 297.0)
    ]
    assert workbench.preview_after_lightweight_v2.camera_calls == [
        (1000.0, 2000.0, 1420.0, 2297.0)
    ]


def test_visual_extension_falls_back_to_existing_union_when_no_frames_exist() -> None:
    workbench = _FakeWorkbench()

    apply_shared_lightweight_camera_frame(
        workbench,
        {
            "before_transform": {
                "min_x": 0.0,
                "min_y": 0.0,
                "max_x": 100.0,
                "max_y": 100.0,
            },
            "after_transform": {
                "min_x": 1000.0,
                "min_y": 1000.0,
                "max_x": 1100.0,
                "max_y": 1100.0,
            },
        },
    )

    assert workbench.preview_before_lightweight_v2.camera_calls == [
        (0.0, 0.0, 1100.0, 1100.0)
    ]
    assert workbench.preview_after_lightweight_v2.camera_calls == [
        (0.0, 0.0, 1100.0, 1100.0)
    ]


def _cad_box(x0, y0, x1, y1):
    return {"min_x": x0, "min_y": y0, "max_x": x1, "max_y": y1}


def test_visual_extension_frames_to_primary_change_zone_not_full_sheet() -> None:
    """When no 도곽 frame exists, both panes frame to the PRIMARY change zone
    (rank 1), not the whole multi-detail sheet — so changes are visible at real
    size on load and both panes stay aligned (the same shared frame)."""

    workbench = _FakeWorkbench()
    # Far-left low-priority notes zone + far-right rank-1 deleted detail.
    workbench._active_overlays_by_zone = {
        "C-005": {
            "change_type": "moved",
            "priority_rank": 6,
            "old_bbox": _cad_box(3277.0, 4326.0, 3387.0, 4436.0),
            "bbox": _cad_box(3277.0, 4326.0, 3387.0, 4436.0),
        },
        "C-001": {
            "change_type": "deleted",
            "priority_rank": 1,
            "old_bbox": _cad_box(444054.0, -93694.0, 459756.0, -76385.0),
            "bbox": _cad_box(444054.0, -93694.0, 459756.0, -76385.0),
        },
    }

    apply_shared_lightweight_camera_frame(
        workbench,
        {
            # Whole-sheet extents (~459k wide) — what the old path would frame to.
            "before_transform": {"min_x": -60.0, "min_y": -93700.0, "max_x": 459763.0, "max_y": 8562.0},
            "after_transform": {"min_x": -60.0, "min_y": -89687.0, "max_x": 377977.0, "max_y": 8562.0},
        },
    )

    before_calls = workbench.preview_before_lightweight_v2.camera_calls
    after_calls = workbench.preview_after_lightweight_v2.camera_calls
    assert len(before_calls) == 1 and len(after_calls) == 1
    # One shared frame for both panes (alignment preserved).
    assert before_calls[0] == after_calls[0]
    frame = before_calls[0]
    # Centered on the far-right rank-1 zone, NOT the full-sheet union.
    assert frame[0] > 400000.0
    assert frame[0] <= 444054.0 and frame[2] >= 459756.0  # contains C-001
    assert (frame[2] - frame[0]) < 60000.0  # a real zoom, not the 459k sheet
