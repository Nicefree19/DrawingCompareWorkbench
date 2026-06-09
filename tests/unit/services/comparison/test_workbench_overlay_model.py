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

from pathlib import Path

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


# ---------------------------------------------------------------------------
# Phase Q1 — revision-cloud visibility at whole-drawing fit.
#
# Root cause (verified end-to-end against the live path): a before/after
# comparison ends with the camera fit to the WHOLE drawing (QML fitToView),
# so on a ~137 m × 551 m multi-detail sheet a 110 mm text-edit zone renders at
# ~0.2 px and is invisible. The data path is healthy (no suppression at the
# real 13-zone scale); the cloud just needs a minimum SCREEN footprint. Q1
# clamps that footprint in the QML cloud/focus delegates, expanding it
# symmetrically about the change centre so the marker still points at the
# real spot.
# ---------------------------------------------------------------------------

# Real DWG 1↔2 "added" zones, mirrored from
# release/sample_compare_dwg_1_2/change_artifacts/change_zones.csv —
# (zone_id, min_x, min_y, max_x, max_y). A mix of 110 mm text edits and
# 100 m+ line clusters proves both extremes survive the adapter.
_REAL_DWG_ADDED_ZONES = [
    ("C-001", -50623.0, -111255.2, -50513.0, -111145.2),    # 110 mm TEXT
    ("C-002", -45547.14, -109829.91, 77894.75, -60599.36),  # 123 m LINE cluster
    ("C-003", -50993.5, -89519.6, -50883.5, -89409.6),      # 110 mm TEXT
    ("C-005", -50870.0, -72477.0, -50760.0, -72367.0),      # 110 mm TEXT
    ("C-007", 77834.75, -60659.36, 79845.58, -59314.27),    # ~2 m LWPOLYLINE
    ("C-010", -55.0, -55.0, 55.0, 55.0),                    # 110 mm TEXT ("CHECK")
    ("C-011", -57867.6, 439575.1, -57707.6, 439735.1),      # 160 mm INSERT
]

# Real drawing extents that span the zones above (≈137 m × 551 m).
_REAL_DWG_WORLD_BBOX = (-57867.6, -111255.2, 79845.58, 439735.1)


def test_lightweight_adapter_keeps_small_and_large_zones_as_clouds() -> None:
    """All real zones (tiny text edits + giant line clusters) must reach the
    cloud layer with their true world bboxes — none dropped (13 « 300/120),
    and the QML clamps the SCREEN footprint, never the world size."""
    viewport = LightweightDrawingViewport.__new__(LightweightDrawingViewport)
    viewport._side = "after"
    viewport._world_bbox = _REAL_DWG_WORLD_BBOX
    captured: dict[str, list[dict]] = {}
    viewport.set_overlays = lambda cloud, focus: captured.update(
        cloud=list(cloud), focus=list(focus)
    )

    overlays = [
        {
            "zone_id": zid,
            "bbox": {"min_x": x0, "min_y": y0, "max_x": x1, "max_y": y1},
            "change_type": "added",
        }
        for (zid, x0, y0, x1, y1) in _REAL_DWG_ADDED_ZONES
    ]

    viewport.push_change_overlays_from_v1(overlays, side="after")

    cloud = captured["cloud"]
    assert len(cloud) == len(_REAL_DWG_ADDED_ZONES)  # nothing suppressed
    by_zone = {c["label"]: c for c in cloud}
    # 110 mm text edit keeps its true (tiny) world size — visibility is the
    # QML's job, not the adapter's.
    assert by_zone["C-001"]["w"] == pytest.approx(110.0, abs=0.5)
    assert by_zone["C-001"]["h"] == pytest.approx(110.0, abs=0.5)
    # 123 m line cluster keeps its true (huge) world size.
    assert by_zone["C-002"]["w"] == pytest.approx(123441.89, abs=1.0)
    # Every cloud entry carries a positive world footprint.
    assert all(c["w"] > 0 and c["h"] > 0 for c in cloud)


def test_qml_cloud_enforces_minimum_screen_footprint() -> None:
    """The QML cloud + focus delegates must clamp the on-screen footprint to a
    minimum and expand symmetrically about the change centre. Mirrors the
    source-assertion style used in test_korean_workbench_ux."""
    qml = Path(
        "src/gui/assets/drawing_compare/LightweightDrawingViewport.qml"
    ).read_text(encoding="utf-8")
    # Tunable minimum footprint exists.
    assert "property real minCloudPx" in qml
    # Cloud width/height clamp to the minimum (not the old unconditional 2 px).
    assert "Math.max(root.minCloudPx, _natW)" in qml
    assert "Math.max(root.minCloudPx, _natH)" in qml
    assert "Math.max(2, (modelData.w || 0) * s)" not in qml
    # Centred expansion — marker stays on the real change spot.
    assert "_cxScreen" in qml and "_cyScreen" in qml
    assert "x: _cxScreen - _drawW / 2" in qml
    assert "y: _cyScreen - _drawH / 2" in qml
    # Focus marker shares the same minimum.
    assert "Math.max(root.minCloudPx, (modelData.w || 0)" in qml


def test_min_footprint_math_matches_qml_cloud_delegate() -> None:
    """Mirrors the Phase Q1 arithmetic in LightweightDrawingViewport.qml
    (cloud delegate). KEEP IN SYNC if the QML bindings change.

    Proves three things on the real DWG 1↔2 geometry:
      1. at whole-drawing fit a 110 mm zone is sub-pixel (the invisibility bug);
      2. the Q1 clamp lifts the footprint to >= minCloudPx (the fix);
      3. a naive top-left-anchored min-size would mis-centre the marker by
         ~half the added pixels, whereas Q1's centred expansion does not.
    """
    MIN_CLOUD_PX = 32.0  # mirrors LightweightDrawingViewport.qml `minCloudPx`

    world = _REAL_DWG_WORLD_BBOX
    view_w, view_h = 1600.0, 1000.0
    ww = max(1.0, world[2] - world[0])
    wh = max(1.0, world[3] - world[1])
    upp = max(ww / view_w, wh / view_h) * 1.05  # QML fitToView()
    cam_cx = (world[0] + world[2]) / 2.0
    s = 1.0 / max(0.0001, upp)

    # 110 mm TEXT zone C-001 (X axis is enough to prove the centring rule).
    zx, zw, zh = -50623.0, 110.0, 110.0
    nat_w, nat_h = zw * s, zh * s
    assert nat_w < 2.0 and nat_h < 2.0  # (1) sub-pixel → invisible without Q1

    draw_w = max(MIN_CLOUD_PX, nat_w)
    draw_h = max(MIN_CLOUD_PX, nat_h)
    assert draw_w == MIN_CLOUD_PX and draw_h == MIN_CLOUD_PX  # (2) clamp fires
    assert min(draw_w, draw_h) >= 32.0  # routes through the scalloped path

    # Projected screen centre of the change.
    cx_screen = view_w / 2 + ((zx + zw / 2) - cam_cx) * s
    # Q1 (centred) marker.
    marker_x = cx_screen - draw_w / 2
    new_center = marker_x + draw_w / 2
    # Naive top-left-anchored min-size (what we must NOT do).
    x_old_topleft = view_w / 2 + (zx - cam_cx) * s
    old_center = x_old_topleft + draw_w / 2

    # (3) naive approach drifts by ~(min - natural)/2; Q1 stays centred.
    assert abs(old_center - cx_screen) == pytest.approx((draw_w - nat_w) / 2, abs=0.01)
    assert abs(old_center - cx_screen) > 10.0
    assert new_center == pytest.approx(cx_screen, abs=1e-6)


# ---------------------------------------------------------------------------
# Phase A — oversized (sheet-crossing) zones render as a faint outline + pin.
#
# The real DWG 1↔2 "giant" zones (C-002/004/006) are NOT clusters: each is a
# single LINE on the 01_SENCHECK review layer — a leader line connecting an
# added review note (the 110 mm TEXT zones) to a detail. Its axis-aligned bbox
# spans ~90% of the drawing width, so a bold scalloped cloud around it blankets
# a band of the sheet and hides the small note clouds Q1 just made visible.
# Phase A renders such zones as a faint dashed outline + a centroid pin instead.
# ---------------------------------------------------------------------------


def test_qml_oversized_cloud_uses_outline_and_centroid_pin() -> None:
    """Source-level guard for the Phase A oversized-cloud rendering policy."""
    qml = Path(
        "src/gui/assets/drawing_compare/LightweightDrawingViewport.qml"
    ).read_text(encoding="utf-8")
    assert "property real largeCloudFraction" in qml
    assert "property bool _oversized" in qml
    # Oversized branch: dashed, half-alpha boundary, early return (no scallop).
    assert "if (cloudWrapper._oversized)" in qml
    assert "setLineDash" in qml
    assert "globalAlpha = 0.5" in qml
    # Centroid pin shown only for oversized zones.
    assert "visible: cloudWrapper._oversized" in qml


def test_oversized_predicate_matches_qml_rule() -> None:
    """Mirrors the Phase A `_oversized` rule in LightweightDrawingViewport.qml.
    KEEP IN SYNC. A leader line spanning ~90% of the drawing width is oversized;
    a 110 mm note is not; a degenerate worldBbox flags nothing."""
    FRACTION = 0.5  # mirrors QML `largeCloudFraction`

    def oversized(world, w, h):
        dw = max(1.0, world[2] - world[0])
        dh = max(1.0, world[3] - world[1])
        return dw > 100 and dh > 100 and (w > FRACTION * dw or h > FRACTION * dh)

    world = _REAL_DWG_WORLD_BBOX  # ~137 m × 551 m
    # C-002 leader line: 123 m wide (~90% of the 137 m width) → oversized.
    assert oversized(world, 123441.89, 49230.55) is True
    # C-001 note: 110 mm → not oversized.
    assert oversized(world, 110.0, 110.0) is False
    # C-007 ~2 m polyline → not oversized.
    assert oversized(world, 2010.0, 1345.0) is False
    # Degenerate worldBbox must not flag anything (guard).
    assert oversized([0.0, 0.0, 1.0, 1.0], 110.0, 110.0) is False


# ---------------------------------------------------------------------------
# B안 — geometry-aware cloud: the adapter forwards a DXF/DWG entity's real
# geometry (CAD-world mm) to the QML so an oversized leader line is drawn along
# its actual shape instead of its bbox. PDF overlays (image_pixels) are skipped.
# ---------------------------------------------------------------------------


def test_lightweight_adapter_forwards_dxf_geometry_to_cloud_entry() -> None:
    viewport = LightweightDrawingViewport.__new__(LightweightDrawingViewport)
    viewport._side = "after"
    viewport._world_bbox = None
    captured: dict[str, list[dict]] = {}
    viewport.set_overlays = lambda cloud, focus: captured.update(cloud=list(cloud))
    overlays = [
        {
            "zone_id": "C-002",
            "bbox": {"min_x": -45547.0, "min_y": -109829.0, "max_x": 77894.0, "max_y": -60599.0},
            "change_type": "added",
            "geometry": {"type": "LINE", "points": [[-45547.0, -109829.0], [77894.0, -60599.0]]},
        }
    ]

    viewport.push_change_overlays_from_v1(overlays, side="after")

    entry = captured["cloud"][0]
    assert entry["geometry"] == [[-45547.0, -109829.0], [77894.0, -60599.0]]


def test_lightweight_adapter_skips_geometry_for_pdf_coordinate_space() -> None:
    viewport = LightweightDrawingViewport.__new__(LightweightDrawingViewport)
    viewport._side = "after"
    viewport._world_bbox = (0.0, 0.0, 1000.0, 800.0)  # PDF page (points)
    captured: dict[str, list[dict]] = {}
    viewport.set_overlays = lambda cloud, focus: captured.update(cloud=list(cloud))
    overlays = [
        {
            "zone_id": "P-1",
            "bbox": {"min_x": 0.0, "min_y": 0.0, "max_x": 100.0, "max_y": 100.0},
            "change_type": "added",
            "bbox_coordinate_space": "image_pixels",
            "pdf_dpi": 200.0,
            "geometry": {"type": "LINE", "points": [[0.0, 0.0], [100.0, 100.0]]},
        }
    ]

    viewport.push_change_overlays_from_v1(overlays, side="after")

    entry = captured["cloud"][0]
    assert "geometry" not in entry  # PDF coordinate space → geometry not forwarded


def test_qml_oversized_cloud_draws_geometry_polyline() -> None:
    qml = Path(
        "src/gui/assets/drawing_compare/LightweightDrawingViewport.qml"
    ).read_text(encoding="utf-8")
    # The oversized branch prefers the real geometry polyline, falling back to
    # the Phase A dashed outline when geometry is absent.
    assert "var geom = modelData.geometry" in qml
    assert "if (geom && geom.length >= 2)" in qml
    assert "ctx.lineTo(lx, ly)" in qml
