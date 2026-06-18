# -*- coding: utf-8 -*-
"""Right-list zone selection must zoom the lightweight viewer to the zone.

Bug ("우측 리스트 선택에 따라 뷰어가 확대되는게 매칭이 안 됨"): the active-zone
overlays come from the dashboard ``top_issues`` list, which never went
through ``_push_overlays_to_lightweight_v2``'s coordinate backfill. For a PDF
pair the bbox is in ``image_pixels`` (e.g. x=1859 @ 200 DPI) with no
``bbox_coordinate_space``/``pdf_dpi``, so ``convert_bbox_to_world_space``
passed it through UNCHANGED and ``set_camera_to_world_bbox`` zoomed to pixel
coordinates far outside the points-space page — the camera never matched the
selected zone. ``_focus_lightweight_on_zone_v2`` now backfills the overlay
first so the image_pixels -> PDF-points conversion fires (1859 px -> 669 pt).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2


_PDF_PAGE_WORLD_BBOX = (0.0, 0.0, 841.9, 1190.52)
_PIXEL_BBOX = {"min_x": 1859.0, "min_y": 1286.0, "max_x": 1969.0, "max_y": 1452.0}


class _FakeViewport:
    def __init__(self, world_bbox):
        self.world_bbox = world_bbox
        self.camera_calls: list[tuple] = []

    def set_camera_to_world_bbox(self, bbox, *, padding_ratio=0.25):
        self.camera_calls.append((tuple(bbox), padding_ratio))


def _make_fake(pair: dict, overlay: dict):
    before = _FakeViewport(_PDF_PAGE_WORLD_BBOX)
    after = _FakeViewport(_PDF_PAGE_WORLD_BBOX)
    ns = SimpleNamespace(
        act_lightweight_viewer_v2=object(),
        _is_lightweight_viewer_active_v2=lambda: True,
        _active_overlays_by_zone={str(overlay["zone_id"]): overlay},
        _active_row={"pair_id": "p"},
        _viewer_pairs_by_id={"p": pair},
        _viewer_root=None,
        preview_before_lightweight_v2=before,
        preview_after_lightweight_v2=after,
        _lightweight_camera_sync_in_progress=False,
        _schedule_lightweight_visible_tile_window_v2=lambda side: None,
        _push_overlays_to_lightweight_v2=lambda pair_id, focus_zone_id=None: None,
    )
    ns._backfill_pdf_overlay_coord_space_v2 = (
        lambda pair_id, ovs: DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(
            ns, pair_id, ovs
        )
    )
    ns._peek_overlay_json_pdf_dpi_v2 = (
        lambda vp: DrawingCompareWorkbenchV2._peek_overlay_json_pdf_dpi_v2(ns, vp)
    )
    return ns, before, after


def test_pdf_zone_focus_zooms_to_points_not_pixels():
    """The camera bbox must be in PDF points (~669), not raw pixels (1859)."""

    pair = {"coordinate_source": "image_pixels", "source_a": "a.pdf",
            "source_b": "b.pdf", "compare_pdf_dpi": 200.0}
    overlay = {"zone_id": "C-004", "bbox": dict(_PIXEL_BBOX),
               "change_type": "modified"}  # NO bbox_coordinate_space / pdf_dpi
    ns, before, after = _make_fake(pair, overlay)

    DrawingCompareWorkbenchV2._focus_lightweight_on_zone_v2(ns, "C-004")

    assert after.camera_calls, "after-side camera should have been moved"
    world_bbox = after.camera_calls[-1][0]
    # Pixel-bbox centre 1914 px * 72 / 200 = 689.04 pt — proves the backfill
    # made the image_pixels -> PDF-points conversion fire. Asserted via the
    # CENTRE because the 2026-06-11 context floor may symmetrically widen the
    # camera window; the conversion evidence is the centre in points space.
    cx = (world_bbox[0] + world_bbox[2]) / 2.0
    assert cx == pytest.approx(689.04, abs=1.5)
    assert cx != pytest.approx(1914.0, abs=10.0)
    # Landed inside the page bounds (was off-page before the fix).
    assert 0.0 <= cx <= 841.9


def test_pdf_zone_focus_moves_before_pane_when_old_bbox_is_empty_list():
    """Live regression (2026-06-11, "변경전 존 포커싱이 되지 않아"): dashboard
    top_issues overlays carry ``old_bbox=[]`` (empty list, NOT None) for PDF
    zones. The before pane reads ``old_bbox`` and the fallback used an
    ``is None`` check, so the empty list slipped past it straight into
    ``if not raw: continue`` — the before camera never moved while the after
    camera focused fine. Both panes must now frame the same window."""

    pair = {"coordinate_source": "image_pixels", "source_a": "a.pdf",
            "source_b": "b.pdf", "compare_pdf_dpi": 200.0}
    overlay = {"zone_id": "C-007", "bbox": dict(_PIXEL_BBOX),
               "old_bbox": [],  # exact live shape from the debug capture
               "change_type": "modified"}
    ns, before, after = _make_fake(pair, overlay)

    DrawingCompareWorkbenchV2._focus_lightweight_on_zone_v2(ns, "C-007")

    assert before.camera_calls, "before-side camera must move (was frozen)"
    assert after.camera_calls, "after-side camera should have been moved"
    assert before.camera_calls[-1][0] == after.camera_calls[-1][0], (
        "both panes must frame the SAME world window (synced view contract)"
    )


def test_union_bboxes_tolerates_empty_list_side():
    """Same empty-``old_bbox=[]`` family: ``union_bboxes`` indexed the
    empty candidate (``()[2]``) and raised IndexError — it must skip it."""

    from src.gui.zone_crop_alignment import union_bboxes

    assert union_bboxes([], (1.0, 2.0, 3.0, 4.0)) == (1.0, 2.0, 3.0, 4.0)
    assert union_bboxes([], None) is None


def test_dxf_zone_focus_passes_world_coords_through_unchanged():
    """Regression guard: DXF overlays (already world coords) must NOT be
    rescaled by the PDF backfill — they pass through untouched."""

    pair = {"coordinate_source": "world", "source_a": "a.dxf", "source_b": "b.dxf"}
    overlay = {"zone_id": "Z", "bbox": {"min_x": 100.0, "min_y": 50.0,
                                        "max_x": 140.0, "max_y": 90.0},
               "change_type": "modified"}
    # world-space viewport bbox (CAD units), not a PDF page
    ns, before, after = _make_fake(pair, overlay)
    before.world_bbox = (0.0, 0.0, 500.0, 500.0)
    after.world_bbox = (0.0, 0.0, 500.0, 500.0)

    DrawingCompareWorkbenchV2._focus_lightweight_on_zone_v2(ns, "Z")

    assert after.camera_calls, "after-side camera should have been moved"
    world_bbox = after.camera_calls[-1][0]
    assert world_bbox[0] == pytest.approx(100.0, abs=0.01)  # unchanged


# --- L6: before/after coordinate alignment for one-sided (added/deleted) zones --
# A zone that exists on only one side ("한쪽 빈 화면") must still frame BOTH panes
# to the SAME world window of the side that HAS the content, so the empty pane
# shows that exact location's surroundings instead of a blank/unrelated frame.
# Real 240111 AC1027 coords: C-002 added @x~370k, C-001 deleted @x~444k.

def test_added_zone_frames_both_panes_to_same_after_location():
    pair = {"coordinate_source": "world", "source_a": "a.dxf", "source_b": "b.dxf"}
    overlay = {"zone_id": "C-002",
               "bbox": {"min_x": 362000.0, "min_y": -90000.0,
                        "max_x": 378000.0, "max_y": -72000.0},
               "old_bbox": [],  # added → absent on the before side
               "change_type": "added"}
    ns, before, after = _make_fake(pair, overlay)
    before.world_bbox = (0.0, -100000.0, 460000.0, 0.0)
    after.world_bbox = (0.0, -100000.0, 460000.0, 0.0)

    DrawingCompareWorkbenchV2._focus_lightweight_on_zone_v2(ns, "C-002")

    assert before.camera_calls and after.camera_calls
    assert before.camera_calls[-1][0] == after.camera_calls[-1][0], (
        "both panes must frame the SAME world window for an added zone"
    )
    cx = (after.camera_calls[-1][0][0] + after.camera_calls[-1][0][2]) / 2.0
    assert 360000.0 <= cx <= 380000.0  # the after location, not blank/origin


def test_deleted_zone_frames_both_panes_to_same_before_location():
    pair = {"coordinate_source": "world", "source_a": "a.dxf", "source_b": "b.dxf"}
    overlay = {"zone_id": "C-001",
               "old_bbox": {"min_x": 444000.0, "min_y": -94000.0,
                            "max_x": 460000.0, "max_y": -76000.0},
               "bbox": [],  # deleted → absent on the after side
               "change_type": "deleted"}
    ns, before, after = _make_fake(pair, overlay)
    before.world_bbox = (0.0, -100000.0, 460000.0, 0.0)
    after.world_bbox = (0.0, -100000.0, 460000.0, 0.0)

    DrawingCompareWorkbenchV2._focus_lightweight_on_zone_v2(ns, "C-001")

    assert before.camera_calls and after.camera_calls
    assert before.camera_calls[-1][0] == after.camera_calls[-1][0], (
        "both panes must frame the SAME world window for a deleted zone"
    )
    cx = (before.camera_calls[-1][0][0] + before.camera_calls[-1][0][2]) / 2.0
    assert 444000.0 <= cx <= 460000.0  # the before location


class _CaptureZoneRenderController:
    def __init__(self):
        self.requests: list[dict] = []

    def is_busy(self) -> bool:
        return False

    def render(self, **kwargs) -> bool:
        self.requests.append(kwargs)
        return True


def test_pdf_zone_crop_request_backfills_top_issue_dpi_before_scaling(tmp_path):
    """Top-issue overlays lack pdf_dpi; crop requests must scale them anyway."""

    page_png = tmp_path / "page.png"
    page_png.write_bytes(b"not-used-by-capture-controller")
    transform = {
        "coordinate_space": "image_pixels",
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": 936.0,
        "max_y": 1323.0,
        "img_width": 936,
        "img_height": 1323,
        "pdf_dpi": 80.0,
        "effective_dpi": 80.0,
    }
    pair = {
        "coordinate_source": "image_pixels",
        "source_a": "a.pdf",
        "source_b": "b.pdf",
        "compare_pdf_dpi": 200.0,
        "before_image": str(page_png),
        "after_image": str(page_png),
        "before_transform": dict(transform),
        "after_transform": dict(transform),
    }
    raw_bbox = {"min_x": 455.5, "min_y": 2660.0, "max_x": 566.0, "max_y": 3137.0}
    overlay = {
        "zone_id": "C-007",
        "bbox": dict(raw_bbox),
        "old_bbox": dict(raw_bbox),
        "change_type": "modified",
    }
    controller = _CaptureZoneRenderController()
    ns = SimpleNamespace(
        _active_row={"pair_id": "p"},
        _active_overlays_by_zone={"C-007": overlay},
        _viewer_pairs_by_id={"p": pair},
        _viewer_root=None,
        _dxf_cache_dir=tmp_path,
        _zone_render_controller_v2=controller,
        _render_status_by_pair={},
        _active_zone_render_request_id_v2=lambda pair_id, zone_id: "req-1",
        _begin_selected_zone_render_request_v2=lambda pair_id, zone_id: "req-new",
        _record_zone_render_perf_event_v2=lambda *args, **kwargs: None,
        _set_preview_status_v2=lambda *args, **kwargs: None,
        _viewer_cache_root_v2=lambda: tmp_path,
        _viewer_pair_from_row_v2=lambda pair_id, row: pair,
    )
    ns._peek_overlay_json_pdf_dpi_v2 = lambda vp: 0.0
    ns._backfill_pdf_overlay_coord_space_v2 = (
        lambda pair_id, overlays: DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(
            ns, pair_id, overlays
        )
    )

    DrawingCompareWorkbenchV2._start_zone_crop_render_v2(ns, "C-007")

    assert controller.requests
    request = controller.requests[0]["request"]
    assert request["before_world_window"]["ymin"] == pytest.approx(989.4, abs=0.5)
    assert request["before_world_window"]["ymax"] == pytest.approx(1329.4, abs=0.5)
    assert ns._active_overlays_by_zone["C-007"]["pdf_dpi"] == 200.0


def test_single_dwg_run_source_repair_prefers_registered_dxf(tmp_path):
    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1032")
    fallback = tmp_path / "dxf_registered" / "before" / "detail.dxf"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("0\nEOF\n", encoding="utf-8")
    ns = SimpleNamespace(
        _viewer_pairs_by_id={"p": {}},
        _source_a=str(source),
        _source_b="",
        _result=None,
        _is_usable_zone_render_source_v2=(
            lambda value: DrawingCompareWorkbenchV2._is_usable_zone_render_source_v2(value)
        ),
    )

    repaired = DrawingCompareWorkbenchV2._source_path_replacement_v2(
        ns, "p", {}, "source_a"
    )

    assert repaired == str(fallback)


def test_deleted_zone_focuses_both_panes_to_the_same_window():
    # Live-review fix (2026-06-11): a deleted zone used to SKIP the after
    # pane, leaving it on an unrelated frame ("대응 요소가 없습니다" over a
    # different part of the drawing). Both panes must now frame the SAME
    # world window of the side that has the content (old_bbox).
    pair = {"coordinate_source": "world", "source_a": "a.dxf", "source_b": "b.dxf"}
    overlay = {
        "zone_id": "C-001",
        "change_type": "deleted",
        "old_bbox": {"min_x": 444054.0, "min_y": -93694.0,
                     "max_x": 459756.0, "max_y": -76385.0},
        "bbox": None,
    }
    ns, before, after = _make_fake(pair, overlay)
    sheet = (0.0, -250000.0, 460000.0, 160000.0)
    before.world_bbox = sheet
    after.world_bbox = sheet

    DrawingCompareWorkbenchV2._focus_lightweight_on_zone_v2(ns, "C-001")

    assert before.camera_calls and after.camera_calls, (
        "BOTH panes must be framed for a deleted zone"
    )
    assert before.camera_calls[-1][0] == after.camera_calls[-1][0], (
        "panes must share one world window so the empty side shows the same spot"
    )


def test_tiny_zone_focus_keeps_sheet_context_floor():
    # Live-review fix (2026-06-11, "지나치게 확대되어 보여"): a 110mm zone on a
    # ~460m sheet must not fill the pane — the camera window keeps >= ~6% of
    # the sheet span so the surroundings stay recognisable.
    pair = {"coordinate_source": "world", "source_a": "a.dxf", "source_b": "b.dxf"}
    overlay = {
        "zone_id": "C-003",
        "change_type": "deleted",
        "old_bbox": {"min_x": 452082.0, "min_y": -75921.0,
                     "max_x": 452192.0, "max_y": -75811.0},  # 110 x 110 mm
        "bbox": None,
    }
    ns, before, after = _make_fake(pair, overlay)
    sheet = (0.0, -250000.0, 460000.0, 160000.0)  # max span 460,000
    before.world_bbox = sheet
    after.world_bbox = sheet

    DrawingCompareWorkbenchV2._focus_lightweight_on_zone_v2(ns, "C-003")

    bbox = before.camera_calls[-1][0]
    span = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
    assert span >= 460000.0 * 0.06 - 1.0, f"context floor missing: span={span}"
    # zone centre preserved (still pointing AT the change)
    cx = (bbox[0] + bbox[2]) / 2.0
    assert cx == pytest.approx(452137.0, abs=1.0)


def test_side_messages_explain_added_and_deleted_meaning():
    captured: dict[str, str] = {}

    class _MsgViewport:
        def __init__(self, name):
            self._name = name

        def set_side_message(self, message):
            captured[self._name] = message

    ns = SimpleNamespace(
        _active_overlays_by_zone={
            "C-001": {"zone_id": "C-001", "change_type": "deleted"},
            "C-002": {"zone_id": "C-002", "change_type": "added"},
        },
        preview_before_lightweight_v2=_MsgViewport("before"),
        preview_after_lightweight_v2=_MsgViewport("after"),
    )

    DrawingCompareWorkbenchV2._set_lightweight_zone_side_messages_v2(ns, "C-001")
    assert "삭제" in captured["before"] and "삭제" in captured["after"]

    DrawingCompareWorkbenchV2._set_lightweight_zone_side_messages_v2(ns, "C-002")
    assert "추가" in captured["before"] and "추가" in captured["after"]


def test_ensure_min_world_span_only_enlarges():
    from src.gui.lightweight_viewport import ensure_min_world_span

    grown = ensure_min_world_span((100.0, 100.0, 110.0, 110.0), 200.0)
    assert grown[2] - grown[0] == pytest.approx(200.0)
    assert (grown[0] + grown[2]) / 2.0 == pytest.approx(105.0)  # centre kept
    kept = ensure_min_world_span((0.0, 0.0, 500.0, 400.0), 200.0)
    assert kept == (0.0, 0.0, 500.0, 400.0)


def test_relocation_pair_focuses_from_and_to_locations():
    # Relocation pair (C-001 deleted ↔ C-002 added, the 82m-moved notes
    # block): the before pane frames the OLD location and the after pane the
    # NEW one — the move reads as a jump between panes.
    pair = {"coordinate_source": "world", "source_a": "a.dxf", "source_b": "b.dxf"}
    overlay = {
        "zone_id": "C-001",
        "change_type": "deleted",
        "old_bbox": {"min_x": 444054.0, "min_y": -93694.0,
                     "max_x": 459756.0, "max_y": -76385.0},
        "bbox": None,
        "relocation": {
            "relocation_pair_id": "R-001",
            "relocation_role": "from",
            "relocation_counterpart": "C-002",
            "relocation_counterpart_bbox": [362269.0, -89681.0, 377971.0, -72373.0],
            "relocation_offset": [-81785.0, 4012.5],
        },
    }
    ns, before, after = _make_fake(pair, overlay)
    sheet = (0.0, -250000.0, 460000.0, 160000.0)
    before.world_bbox = sheet
    after.world_bbox = sheet

    DrawingCompareWorkbenchV2._focus_lightweight_on_zone_v2(ns, "C-001")

    assert before.camera_calls and after.camera_calls
    b_bbox = before.camera_calls[-1][0]
    a_bbox = after.camera_calls[-1][0]
    b_cx = (b_bbox[0] + b_bbox[2]) / 2.0
    a_cx = (a_bbox[0] + a_bbox[2]) / 2.0
    assert b_cx == pytest.approx(451905.0, abs=2.0)  # old location centre
    assert a_cx == pytest.approx(370120.0, abs=2.0)  # new location centre
    assert abs(b_cx - a_cx) == pytest.approx(81785.0, abs=5.0)  # the move itself


def test_relocation_side_messages_say_moved_not_deleted():
    captured: dict[str, str] = {}

    class _MsgViewport:
        def __init__(self, name):
            self._name = name

        def set_side_message(self, message):
            captured[self._name] = message

    overlay = {
        "zone_id": "C-001",
        "change_type": "deleted",
        "relocation": {"relocation_counterpart": "C-002", "relocation_role": "from"},
    }
    ns = SimpleNamespace(
        _active_overlays_by_zone={"C-001": overlay},
        preview_before_lightweight_v2=_MsgViewport("before"),
        preview_after_lightweight_v2=_MsgViewport("after"),
    )

    DrawingCompareWorkbenchV2._set_lightweight_zone_side_messages_v2(ns, "C-001")

    assert "묶음 이동" in captured["before"] and "이동 전" in captured["before"]
    assert "묶음 이동" in captured["after"] and "이동 후" in captured["after"]
    assert "C-002" in captured["after"]
