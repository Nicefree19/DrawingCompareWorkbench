# -*- coding: utf-8 -*-
"""Tests for sheet-frame based before/after viewer alignment."""

from __future__ import annotations

import pytest

from src.services.comparison.sheet_frame_alignment import (
    apply_sheet_frame_camera_alignment,
    build_sheet_frame_alignment,
    camera_bboxes_for_sheet_local_frame,
    sheet_frame_bbox_from_mapping,
)


def test_extracts_frame_from_transform_and_nested_alignment() -> None:
    assert sheet_frame_bbox_from_mapping(
        {"cad_frame_bbox": [0, 0, 420, 297]}
    ) == (0.0, 0.0, 420.0, 297.0)
    assert sheet_frame_bbox_from_mapping(
        {"cad_pdf_alignment": {"cad_frame_bbox": [10, 20, 430, 317]}}
    ) == (10.0, 20.0, 430.0, 317.0)


def test_sheet_frame_alignment_maps_same_local_window_to_each_native_side() -> None:
    before = {"cad_frame_bbox": [0.0, 0.0, 420.0, 297.0]}
    after = {"cad_frame_bbox": [1000.0, 2000.0, 1420.0, 2297.0]}

    result = camera_bboxes_for_sheet_local_frame(
        before,
        after,
        local_bbox=(0.1, 0.2, 0.2, 0.3),
    )

    assert result is not None
    before_bbox, after_bbox, alignment = result
    assert alignment.quality == "exact"
    assert before_bbox == pytest.approx((42.0, 59.4, 84.0, 89.1))
    assert after_bbox == pytest.approx((1042.0, 2059.4, 1084.0, 2089.1))


def test_full_sheet_camera_uses_side_specific_native_frames() -> None:
    before = {"cad_frame_bbox": [0.0, 0.0, 420.0, 297.0]}
    after = {"cad_frame_bbox": [5000.0, -7000.0, 5420.0, -6703.0]}

    result = camera_bboxes_for_sheet_local_frame(before, after)

    assert result is not None
    before_bbox, after_bbox, _alignment = result
    assert before_bbox == (0.0, 0.0, 420.0, 297.0)
    assert after_bbox == (5000.0, -7000.0, 5420.0, -6703.0)


def test_rejects_aspect_mismatch_that_would_map_wrong_sheet_area() -> None:
    before = {"cad_frame_bbox": [0.0, 0.0, 420.0, 297.0]}
    after = {"cad_frame_bbox": [0.0, 0.0, 297.0, 420.0]}

    assert build_sheet_frame_alignment(before, after) is None


class _FakeViewport:
    def __init__(self) -> None:
        self.camera_calls = []

    def set_camera_to_world_bbox(self, bbox) -> None:
        self.camera_calls.append(bbox)


def test_apply_sheet_frame_camera_alignment_sets_side_specific_cameras() -> None:
    before_vp = _FakeViewport()
    after_vp = _FakeViewport()

    alignment = apply_sheet_frame_camera_alignment(
        {"cad_frame_bbox": [0, 0, 420, 297]},
        {"cad_frame_bbox": [1000, 2000, 1420, 2297]},
        before_vp,
        after_vp,
    )

    assert alignment is not None
    assert before_vp.camera_calls == [(0.0, 0.0, 420.0, 297.0)]
    assert after_vp.camera_calls == [(1000.0, 2000.0, 1420.0, 2297.0)]
