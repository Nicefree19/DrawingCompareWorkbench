# -*- coding: utf-8 -*-
"""Unit tests for ADR-003 H5d — build_reference_pdf_overlays.

These prove the slice the project history kept missing: that the DWG diff
overlays, once re-mapped here, land on the *correct* position of a reference
PDF page when run through the SAME converter the lightweight viewport uses
(``transform.convert_bbox_to_world_space``). The positional-accuracy checks
use an explicit DWG frame (not a hand-tuned page) so a frame/Y-flip bug
cannot hide behind a green helper.
"""

from __future__ import annotations

from src.services.comparison.cad_pdf_overlay import (
    DEFAULT_REFERENCE_DPI,
    build_reference_pdf_overlays,
    filter_overlays_to_region,
    recommended_quarter_turns,
    select_pdf_page_for_dwg,
)
from src.services.comparison.page_descriptor import (
    PerPageDescriptor,
    build_dwg_page_descriptor,
)
from src.services.comparison.transform import convert_bbox_to_world_space

# A3 frame in cad_wcs_mm (Y-up) and an A3 PDF page in points (≈ same √2 aspect).
A3_FRAME = (0.0, 0.0, 420.0, 297.0)
A3_PAGE_PT = (1190.55, 841.89)


def _viewport_world(overlay: dict, page_height_pt: float):
    """Reproduce the viewport's overlay→world-points conversion exactly."""
    return convert_bbox_to_world_space(
        overlay["bbox"],
        coordinate_space=overlay["bbox_coordinate_space"],
        pdf_dpi=overlay["pdf_dpi"],
        page_height_points=page_height_pt,
    )


def _center(bbox):
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


# ---------------------------------------------------------------------------
# Positional accuracy (the check that distinguishes "green helper" from
# "user sees a correctly-placed marker")
# ---------------------------------------------------------------------------


def test_center_zone_maps_to_page_center_within_1pt() -> None:
    overlays = [{
        "zone_id": "C-001", "change_type": "modified",
        "bbox": {"min_x": 200.0, "min_y": 138.5, "max_x": 220.0, "max_y": 158.5},
    }]
    conv, quality = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT
    )
    assert quality == "exact"
    assert len(conv) == 1
    world = _viewport_world(conv[0], A3_PAGE_PT[1])
    cx, cy = _center(world)
    assert abs(cx - A3_PAGE_PT[0] / 2.0) < 1.0
    assert abs(cy - A3_PAGE_PT[1] / 2.0) < 1.0


def test_y_axis_up_top_of_frame_maps_to_top_of_page() -> None:
    """A zone at high CAD-Y must land near the TOP of the page (Y-up)."""
    overlays = [{
        "zone_id": "C-top", "change_type": "added",
        "bbox": {"min_x": 0.0, "min_y": 287.0, "max_x": 10.0, "max_y": 297.0},
    }]
    conv, _ = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT
    )
    world = _viewport_world(conv[0], A3_PAGE_PT[1])
    # Top edge of the frame -> within ~10% of page height from the page top.
    assert world[3] > A3_PAGE_PT[1] * 0.9


def test_bottom_left_corner_zone_maps_near_origin() -> None:
    overlays = [{
        "zone_id": "C-bl", "change_type": "deleted",
        "bbox": {"min_x": 0.0, "min_y": 0.0, "max_x": 10.0, "max_y": 10.0},
    }]
    conv, _ = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT
    )
    world = _viewport_world(conv[0], A3_PAGE_PT[1])
    assert world[0] < A3_PAGE_PT[0] * 0.05  # near left
    assert world[1] < A3_PAGE_PT[1] * 0.05  # near bottom (Y-up)


def test_dpi_independent_result() -> None:
    """Same world position regardless of DPI (pixel canvas scales with it)."""
    overlays = [{
        "zone_id": "C-001", "change_type": "modified",
        "bbox": {"min_x": 100.0, "min_y": 50.0, "max_x": 140.0, "max_y": 90.0},
    }]
    conv72, _ = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT, dpi=72.0
    )
    conv150, _ = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT, dpi=150.0
    )
    w72 = _viewport_world(conv72[0], A3_PAGE_PT[1])
    w150 = _viewport_world(conv150[0], A3_PAGE_PT[1])
    for a, b in zip(w72, w150):
        assert abs(a - b) < 1.5  # only integer-pixel rounding differs


# ---------------------------------------------------------------------------
# Overlay shape / field handling
# ---------------------------------------------------------------------------


def test_emits_image_pixels_tl_shape_and_preserves_metadata() -> None:
    overlays = [{
        "zone_id": "C-9", "change_type": "moved", "severity": "high",
        "label": "C-9", "raw_change_count": 42,
        "bbox": {"min_x": 10.0, "min_y": 10.0, "max_x": 30.0, "max_y": 30.0},
        "after_bbox_px": {"min_x": 1.0, "min_y": 2.0, "max_x": 3.0, "max_y": 4.0},
    }]
    conv, _ = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT
    )
    ov = conv[0]
    assert ov["bbox_coordinate_space"] == "image_pixels_tl"
    assert ov["pdf_dpi"] == DEFAULT_REFERENCE_DPI
    assert ov["zone_id"] == "C-9"
    assert ov["change_type"] == "moved"
    assert ov["severity"] == "high"
    assert ov["raw_change_count"] == 42
    assert set(ov["bbox"]) == {"min_x", "min_y", "max_x", "max_y"}
    # stale render-PNG pixel boxes stripped so they can't be misread
    assert "after_bbox_px" not in ov


def test_old_bbox_mapped_for_before_side() -> None:
    overlays = [{
        "zone_id": "C-3", "change_type": "modified",
        "bbox": {"min_x": 200.0, "min_y": 138.5, "max_x": 220.0, "max_y": 158.5},
        "old_bbox": {"min_x": 0.0, "min_y": 0.0, "max_x": 20.0, "max_y": 20.0},
    }]
    conv, _ = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT
    )
    ov = conv[0]
    # old_bbox (near origin) must differ from bbox (center) and be near origin.
    old_world = convert_bbox_to_world_space(
        ov["old_bbox"], coordinate_space=ov["bbox_coordinate_space"],
        pdf_dpi=ov["pdf_dpi"], page_height_points=A3_PAGE_PT[1],
    )
    assert old_world[0] < A3_PAGE_PT[0] * 0.05
    assert old_world[1] < A3_PAGE_PT[1] * 0.05


def test_missing_old_bbox_falls_back_to_bbox() -> None:
    overlays = [{
        "zone_id": "C-1", "change_type": "added",
        "bbox": {"min_x": 100.0, "min_y": 100.0, "max_x": 120.0, "max_y": 120.0},
    }]
    conv, _ = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT
    )
    assert conv[0]["old_bbox"] == conv[0]["bbox"]


# ---------------------------------------------------------------------------
# Quality grading + honest fallback
# ---------------------------------------------------------------------------


def test_aspect_mismatch_grades_estimated_but_still_emits() -> None:
    # A3 landscape frame onto an A4 *portrait* page (aspect mismatch).
    a4_portrait_pt = (595.28, 841.89)
    overlays = [{
        "zone_id": "C-1", "change_type": "modified",
        "bbox": {"min_x": 10.0, "min_y": 10.0, "max_x": 30.0, "max_y": 30.0},
    }]
    conv, quality = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=a4_portrait_pt
    )
    assert quality == "estimated"
    assert len(conv) == 1  # still emitted; viewer surfaces the estimated badge


def test_degenerate_frame_is_relative_only_and_empty() -> None:
    overlays = [{
        "zone_id": "C-1", "change_type": "modified",
        "bbox": {"min_x": 10.0, "min_y": 10.0, "max_x": 30.0, "max_y": 30.0},
    }]
    conv, quality = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=(0.0, 0.0, 0.0, 0.0), page_points_wh=A3_PAGE_PT
    )
    assert quality == "relative_only"
    assert conv == []


def test_missing_page_size_is_noop() -> None:
    overlays = [{"zone_id": "C-1", "change_type": "modified",
                 "bbox": {"min_x": 1.0, "min_y": 1.0, "max_x": 2.0, "max_y": 2.0}}]
    conv, quality = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=None
    )
    assert conv == []
    assert quality == ""


def test_unparseable_and_nondict_overlays_skipped() -> None:
    overlays = [
        "not-a-dict",
        {"zone_id": "C-bad", "bbox": None},
        {"zone_id": "C-bad2", "bbox": {"min_x": "x"}},
        {"zone_id": "C-ok", "change_type": "added",
         "bbox": {"min_x": 10.0, "min_y": 10.0, "max_x": 20.0, "max_y": 20.0}},
    ]
    conv, _ = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT
    )
    ids = [o["zone_id"] for o in conv]
    assert ids == ["C-ok"]


def test_empty_overlays_returns_empty_with_quality() -> None:
    conv, quality = build_reference_pdf_overlays(
        [], dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT
    )
    assert conv == []
    assert quality == "exact"  # alignment is usable; just nothing to map


# ---------------------------------------------------------------------------
# H5b page selection (multi-sheet booklet)
# ---------------------------------------------------------------------------


def _pdf_desc(path: str, page: int, number: str) -> PerPageDescriptor:
    return PerPageDescriptor(
        pdf_path=path, page_index=page, page_size=(420.0, 297.0),
        drawing_number=number,
    )


def _dwg_desc(path: str, number: str) -> PerPageDescriptor:
    return build_dwg_page_descriptor(
        path, texts=[number], frame_bbox=(0, 0, 420, 297)
    )


def test_select_single_page_returns_its_index() -> None:
    dwg = _dwg_desc("3PG1.dwg", "S20-0002")
    pdf = [_pdf_desc("3PG1.pdf", 0, "S20-0002")]
    page, status = select_pdf_page_for_dwg(dwg, pdf)
    assert page == 0
    assert status == "single"


def test_select_multi_page_picks_matching_number() -> None:
    dwg = _dwg_desc("b.dwg", "S20-0002")
    pdf = [
        _pdf_desc("book.pdf", 0, "S20-0001"),
        _pdf_desc("book.pdf", 1, "S20-0002"),
        _pdf_desc("book.pdf", 2, "S20-0003"),
    ]
    page, status = select_pdf_page_for_dwg(dwg, pdf)
    assert page == 1
    assert status in ("auto_confirmed", "review_required")


def test_select_no_descriptors_fallback_zero() -> None:
    dwg = _dwg_desc("x.dwg", "S20-0002")
    page, status = select_pdf_page_for_dwg(dwg, [])
    assert page == 0
    assert status == "fallback"


# ---------------------------------------------------------------------------
# H5e — rotation-aware alignment (landscape DWG plotted to portrait PDF, the
# real 01.3PG1 case). Frame/page numbers below are the actual measured values.
# ---------------------------------------------------------------------------

# Real after.dxf modelspace extents (landscape √2) + real 01.3PG1 page points.
REAL_LANDSCAPE_FRAME = (-60.79, -375.12, 12578.50, 8562.67)  # aspect ≈ 1.414
REAL_PORTRAIT_PAGE = (841.92, 1190.52)                        # aspect ≈ 0.707


def test_recommended_quarter_turns_detects_inverse_aspect() -> None:
    # landscape frame vs portrait page -> needs a 90° turn.
    assert recommended_quarter_turns(REAL_LANDSCAPE_FRAME, REAL_PORTRAIT_PAGE) == 1


def test_recommended_quarter_turns_zero_when_matched() -> None:
    # A3 frame onto A3 page (same orientation) -> no rotation.
    assert recommended_quarter_turns(A3_FRAME, A3_PAGE_PT) == 0


def test_recommended_quarter_turns_zero_for_degenerate() -> None:
    assert recommended_quarter_turns((0.0, 0.0, 0.0, 0.0), A3_PAGE_PT) == 0
    assert recommended_quarter_turns(A3_FRAME, None) == 0


def test_rotation_makes_landscape_frame_exact_on_portrait_page() -> None:
    """The headline H5e fix: real landscape DWG + portrait PDF -> exact."""
    zones = [{
        "zone_id": "C-1", "change_type": "modified",
        "bbox": {"min_x": 5000.0, "min_y": 4000.0, "max_x": 5600.0, "max_y": 4400.0},
    }]
    # Without rotation: estimated (100% aspect mismatch).
    _, q0 = build_reference_pdf_overlays(
        zones, dwg_frame_bbox=REAL_LANDSCAPE_FRAME,
        page_points_wh=REAL_PORTRAIT_PAGE, page_rotation_quarter_turns=0,
    )
    assert q0 == "estimated"
    # With the recommended 90° turn: exact, and the overlay lands in-bounds.
    k = recommended_quarter_turns(REAL_LANDSCAPE_FRAME, REAL_PORTRAIT_PAGE)
    conv, q1 = build_reference_pdf_overlays(
        zones, dwg_frame_bbox=REAL_LANDSCAPE_FRAME,
        page_points_wh=REAL_PORTRAIT_PAGE, page_rotation_quarter_turns=k,
    )
    assert q1 == "exact"
    assert len(conv) == 1
    w = _viewport_world(conv[0], REAL_PORTRAIT_PAGE[1])
    assert 0 <= w[0] <= REAL_PORTRAIT_PAGE[0]
    assert 0 <= w[2] <= REAL_PORTRAIT_PAGE[0]
    assert 0 <= w[1] <= REAL_PORTRAIT_PAGE[1]
    assert 0 <= w[3] <= REAL_PORTRAIT_PAGE[1]


def test_rotation_cw_ccw_place_corner_on_opposite_sides() -> None:
    """k=1 and k=3 both grade exact but mirror the position (GUI toggle)."""
    fx0, fy0, fx1, fy1 = REAL_LANDSCAPE_FRAME
    # A zone in the frame's top-left region.
    zones = [{
        "zone_id": "C-tl", "change_type": "added",
        "bbox": {"min_x": fx0 + 100, "min_y": fy1 - 600,
                 "max_x": fx0 + 700, "max_y": fy1 - 100},
    }]
    c1, q1 = build_reference_pdf_overlays(
        zones, dwg_frame_bbox=REAL_LANDSCAPE_FRAME,
        page_points_wh=REAL_PORTRAIT_PAGE, page_rotation_quarter_turns=1,
    )
    c3, q3 = build_reference_pdf_overlays(
        zones, dwg_frame_bbox=REAL_LANDSCAPE_FRAME,
        page_points_wh=REAL_PORTRAIT_PAGE, page_rotation_quarter_turns=3,
    )
    assert q1 == "exact" and q3 == "exact"
    w1 = _viewport_world(c1[0], REAL_PORTRAIT_PAGE[1])
    w3 = _viewport_world(c3[0], REAL_PORTRAIT_PAGE[1])
    # Opposite corners: one near bottom-left, the other near top-right.
    assert _center(w1) != _center(w3)
    assert (w1[0] < REAL_PORTRAIT_PAGE[0] / 2) != (w3[0] < REAL_PORTRAIT_PAGE[0] / 2)


# ---------------------------------------------------------------------------
# H5f — region scoping (multi-region modelspace -> one PDF sheet). Mirrors the
# real 3PG1 finding: only changes inside the matched 도곽 belong on the sheet.
# ---------------------------------------------------------------------------


def _zone(zid, x0, y0, x1, y1):
    return {"zone_id": zid, "change_type": "modified",
            "bbox": {"min_x": x0, "min_y": y0, "max_x": x1, "max_y": y1}}


def test_region_filter_keeps_inside_drops_outside() -> None:
    region = (0.0, 0.0, 1000.0, 1000.0)
    overlays = [
        _zone("in", 100, 100, 200, 200),       # inside
        _zone("far", -80000, 5000, -79900, 5100),  # far away (the 3PG1 case)
        _zone("edge", 950, 950, 1050, 1050),   # straddles the edge
    ]
    kept = filter_overlays_to_region(overlays, region)  # intersect
    ids = {o["zone_id"] for o in kept}
    assert "in" in ids and "edge" in ids and "far" not in ids


def test_region_filter_contain_mode_excludes_straddlers() -> None:
    region = (0.0, 0.0, 1000.0, 1000.0)
    overlays = [_zone("in", 100, 100, 200, 200), _zone("edge", 950, 950, 1050, 1050)]
    kept = filter_overlays_to_region(overlays, region, mode="contain")
    ids = {o["zone_id"] for o in kept}
    assert ids == {"in"}


def test_region_filter_none_region_passes_through() -> None:
    overlays = [_zone("a", 1, 1, 2, 2), _zone("b", 3, 3, 4, 4)]
    kept = filter_overlays_to_region(overlays, None)
    assert len(kept) == 2


def test_region_scoped_overlay_maps_region_to_full_page() -> None:
    """The selected region becomes the frame -> its content fills the page."""
    region = (5000.0, 4000.0, 5000.0 + 420.0, 4000.0 + 297.0)  # an A3-ish 도곽
    rcx, rcy = (region[0] + region[2]) / 2.0, (region[1] + region[3]) / 2.0  # (5210, 4148.5)
    overlays = [
        _zone("c", rcx - 10, rcy - 10, rcx + 10, rcy + 10),  # at region centre
        _zone("far", -80000.0, 0.0, -79900.0, 100.0),        # other sheet
    ]
    scoped = filter_overlays_to_region(overlays, region)
    assert [o["zone_id"] for o in scoped] == ["c"]
    conv, q = build_reference_pdf_overlays(
        scoped, dwg_frame_bbox=region, page_points_wh=A3_PAGE_PT)
    assert q == "exact" and len(conv) == 1
    w = _viewport_world(conv[0], A3_PAGE_PT[1])
    cx, cy = _center(w)
    assert abs(cx - A3_PAGE_PT[0] / 2.0) < 2.0
    assert abs(cy - A3_PAGE_PT[1] / 2.0) < 2.0


def test_rotation_default_zero_preserves_h5d_behaviour() -> None:
    """Default (no rotation) must not change the H5d matched-A3 result."""
    overlays = [{
        "zone_id": "C-1", "change_type": "modified",
        "bbox": {"min_x": 200.0, "min_y": 138.5, "max_x": 220.0, "max_y": 158.5},
    }]
    conv, quality = build_reference_pdf_overlays(
        overlays, dwg_frame_bbox=A3_FRAME, page_points_wh=A3_PAGE_PT
    )
    assert quality == "exact"
    world = _viewport_world(conv[0], A3_PAGE_PT[1])
    cx, cy = _center(world)
    assert abs(cx - A3_PAGE_PT[0] / 2.0) < 1.0
    assert abs(cy - A3_PAGE_PT[1] / 2.0) < 1.0
