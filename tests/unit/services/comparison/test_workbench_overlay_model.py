# -*- coding: utf-8 -*-
"""Unit tests for the cloud/focus overlay model helpers used by the GPU viewport.

These helpers split a flat overlay list into a "검토 영역" (cloud) layer and a
"선택 변경점" (focus) layer with pin/crosshair semantics. They are pure functions
so they can be exercised without instantiating any Qt widget. Coverage targets:
- match-side classification (deleted/added/modified/moved/mixed → a_only/b_only/matched/mixed)
- before/after side dimming for one-sided changes
- selected zone produces both dimmed cloud + focus pin entries
- pin_only path used for PDF page-level fallback skips the cloud
"""

from __future__ import annotations

import pytest

from src.gui.drawing_compare_workbench import (
    GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD,
    GpuDrawingViewport,
    build_overlay_entries,
    overlay_cloud_should_dim,
    resolve_overlay_match_side,
    should_use_focus_only_overlay_mode,
    split_overlay_entries,
)
from src.gui.lightweight_viewport import LightweightDrawingViewport


@pytest.mark.parametrize(
    "change_type,expected",
    [
        ("deleted", "a_only"),
        ("DELETED", "a_only"),
        ("removed", "a_only"),
        ("added", "b_only"),
        ("Added", "b_only"),
        ("modified", "matched"),
        ("moved", "matched"),
        ("mixed", "mixed"),
        ("", "matched"),
        (None, "matched"),
    ],
)
def test_resolve_match_side_classifies_change_types(change_type, expected) -> None:
    assert resolve_overlay_match_side(change_type) == expected


def test_should_dim_cloud_dims_selected_overlay_unconditionally() -> None:
    assert overlay_cloud_should_dim("matched", before=True, selected=True) is True
    assert overlay_cloud_should_dim("a_only", before=False, selected=True) is True


def test_should_dim_cloud_dims_one_sided_change_on_wrong_viewport() -> None:
    # b_only(추가) overlay is shown dim on the before viewport because nothing
    # to compare exists on side A — the cloud is just a contextual outline.
    assert overlay_cloud_should_dim("b_only", before=True, selected=False) is True
    assert overlay_cloud_should_dim("b_only", before=False, selected=False) is False
    # a_only(삭제) overlay is dim on the after viewport.
    assert overlay_cloud_should_dim("a_only", before=False, selected=False) is True
    assert overlay_cloud_should_dim("a_only", before=True, selected=False) is False


def test_should_dim_cloud_keeps_matched_normal_when_not_selected() -> None:
    assert overlay_cloud_should_dim("matched", before=True, selected=False) is False
    assert overlay_cloud_should_dim("matched", before=False, selected=False) is False


def test_build_entries_returns_single_cloud_for_unselected_zone() -> None:
    entries = build_overlay_entries(
        zone_id="z1",
        rect=(10.0, 20.0, 100.0, 50.0),
        change_type="modified",
        label="z1",
        raw_change_count=4,
        cluster_count=0,
        selected=False,
        before=False,
    )
    assert len(entries) == 1
    cloud = entries[0]
    assert cloud["role"] == "cloud"
    assert cloud["matchSide"] == "matched"
    assert cloud["dimmed"] is False
    assert cloud["x"] == 10.0
    assert cloud["width"] == 100.0
    assert cloud["labelMode"] == "area"


def test_build_entries_returns_dimmed_cloud_plus_focus_for_selected_zone() -> None:
    entries = build_overlay_entries(
        zone_id="z1",
        rect=(10.0, 20.0, 100.0, 60.0),
        change_type="deleted",
        label="z1",
        selected=True,
        before=True,
    )
    assert len(entries) == 2
    cloud, focus = entries
    assert cloud["role"] == "cloud"
    assert cloud["dimmed"] is True  # selected zones always dim the cloud
    assert focus["role"] == "focus"
    assert focus["matchSide"] == "a_only"
    assert focus["crosshair"] is True
    assert focus["pinX"] == pytest.approx(10.0 + 100.0 / 2.0)
    assert focus["pinY"] == pytest.approx(20.0 + 60.0 / 2.0)
    assert focus["labelMode"] == "compact"
    assert focus["pinOnly"] is False


def test_build_entries_pin_only_skips_cloud() -> None:
    entries = build_overlay_entries(
        zone_id="pdf-1",
        rect=(50.0, 60.0, 200.0, 150.0),
        change_type="added",
        label="pdf-1",
        selected=True,
        before=False,
        pin_only=True,
    )
    assert len(entries) == 1
    only = entries[0]
    assert only["role"] == "focus"
    assert only["pinOnly"] is True
    assert only["matchSide"] == "b_only"


def test_build_entries_unselected_pin_only_yields_no_entries() -> None:
    # Defensive: pin_only without selection means there is nothing to draw.
    entries = build_overlay_entries(
        zone_id="pdf-2",
        rect=(0.0, 0.0, 100.0, 100.0),
        change_type="modified",
        label="pdf-2",
        selected=False,
        before=False,
        pin_only=True,
    )
    assert entries == []


def test_split_overlay_entries_partitions_by_role() -> None:
    flat = [
        {"role": "cloud", "zoneId": "a"},
        {"role": "focus", "zoneId": "a"},
        {"role": "cloud", "zoneId": "b"},
        {"zoneId": "c"},  # missing role defaults to cloud bucket
        "not a dict",  # ignored
    ]
    cloud, focus = split_overlay_entries(flat)
    assert [item["zoneId"] for item in cloud] == ["a", "b", "c"]
    assert [item["zoneId"] for item in focus] == ["a"]


def test_focus_only_overlay_mode_starts_after_source_threshold() -> None:
    assert should_use_focus_only_overlay_mode(
        GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD
    ) is False
    assert should_use_focus_only_overlay_mode(
        GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD + 1
    ) is True


def test_gpu_overlay_model_skips_cloud_canvas_entries_for_large_unselected_sets() -> None:
    viewport = GpuDrawingViewport.__new__(GpuDrawingViewport)
    viewport._selected_zone_id = ""
    viewport._overlays_by_zone = {}
    viewport._tile_manifest = {}
    viewport._viewer_root = None
    viewport._quick = None
    overlays = [
        {
            "zone_id": f"z{i}",
            "normalized_bbox": {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
            "change_type": "modified",
            "raw_change_count": 1,
        }
        for i in range(GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD + 1)
    ]

    model = viewport._overlay_model(
        overlays,
        before=False,
        real_image=False,
        viewport_rect=None,
    )

    assert model == []


def test_gpu_overlay_model_keeps_selected_focus_for_large_sets_without_cloud() -> None:
    viewport = GpuDrawingViewport.__new__(GpuDrawingViewport)
    selected = {
        "zone_id": "z5",
        "normalized_bbox": {"x": 0.2, "y": 0.3, "width": 0.1, "height": 0.1},
        "change_type": "modified",
        "raw_change_count": 2,
    }
    overlays = [
        {
            "zone_id": f"z{i}",
            "normalized_bbox": {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
            "change_type": "modified",
            "raw_change_count": 1,
        }
        for i in range(GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD + 1)
    ]
    overlays[5] = selected
    viewport._selected_zone_id = "z5"
    viewport._overlays_by_zone = {"z5": selected}
    viewport._tile_manifest = {}
    viewport._viewer_root = None
    viewport._quick = None

    model = viewport._overlay_model(
        overlays,
        before=False,
        real_image=False,
        viewport_rect=None,
    )

    assert [entry["role"] for entry in model] == ["focus"]
    assert model[0]["zoneId"] == "z5"


def test_lightweight_overlay_adapter_uses_focus_only_for_large_sets() -> None:
    viewport = LightweightDrawingViewport.__new__(LightweightDrawingViewport)
    viewport._side = "after"
    viewport._world_bbox = None
    captured: dict[str, list[dict]] = {}

    def capture(cloud, focus):
        captured["cloud"] = list(cloud)
        captured["focus"] = list(focus)

    viewport.set_overlays = capture
    overlays = [
        {
            "zone_id": f"z{i}",
            "bbox": {"min_x": 0, "min_y": 0, "max_x": 10, "max_y": 10},
            "change_type": "modified",
        }
        for i in range(GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD + 1)
    ]

    viewport.push_change_overlays_from_v1(overlays, side="after", focus_zone_id="z5")

    assert captured["cloud"] == []
    assert [entry["zoneId"] for entry in captured["focus"]] == ["z5"]


def test_build_entries_clamps_minimum_dimensions_to_one_pixel() -> None:
    entries = build_overlay_entries(
        zone_id="tiny",
        rect=(5.0, 5.0, 0.0, 0.0),
        change_type="added",
        label="tiny",
        selected=False,
        before=False,
    )
    assert entries[0]["width"] == 1.0
    assert entries[0]["height"] == 1.0


def test_build_entries_dim_b_only_cloud_on_before_viewport_when_unselected() -> None:
    entries = build_overlay_entries(
        zone_id="add-1",
        rect=(0.0, 0.0, 30.0, 30.0),
        change_type="added",
        label="add-1",
        selected=False,
        before=True,
    )
    assert entries[0]["matchSide"] == "b_only"
    assert entries[0]["dimmed"] is True
