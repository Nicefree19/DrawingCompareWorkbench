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
