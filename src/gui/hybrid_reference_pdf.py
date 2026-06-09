# -*- coding: utf-8 -*-
"""ADR-003 H5d — GUI adapter: show a DWG diff over the user's reference PDF.

This is the thin Qt glue that finally makes the PDF-first hybrid visible.
All the heavy lifting (coordinate math, alignment, pairing) lives in the
pure, fully-tested ``cad_pdf_overlay`` service module; this file only:

  1. adds a menu action to the workbench, and
  2. on trigger: captures the active DWG-diff overlays + drawing frame,
     asks the user for a reference PDF, loads it into both lightweight
     viewports, re-maps the change zones onto the PDF page via
     ``build_reference_pdf_overlays``, and pushes them through the viewport's
     existing ``push_change_overlays_from_v1`` path (image_pixels_tl → page
     points). No viewport coordinate code is changed.

Kept OUT of the 14,109-line monolith on purpose (Structural Freeze Rule):
the monolith only calls :func:`attach_reference_pdf_action` once, in 5 lines.

Honesty (S1 pattern): when the DWG frame / PDF page aspect mismatch grades
the alignment ``estimated``, or no overlay can be mapped, the user is told
via the viewport side-message + fidelity badge rather than being shown
markers at a silently-wrong position.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Render DPI for the PDF *background bitmap* (sharpness only). Overlay
# placement is DPI-independent and uses page POINTS, so this does not affect
# marker positions.
_PDF_BACKGROUND_DPI = 200.0


def attach_reference_pdf_action(workbench: Any, menu_bar: Any = None) -> bool:
    """Add the '참조 PDF 위에 차이 표시' action to the workbench menu.

    Called once from ``DrawingCompareWorkbenchV2._build_menu_bar_v2``. Safe to
    call repeatedly (rebuilds the menu each time). Returns True on success.
    Never raises — a failure here must not break the rest of the menu.
    """

    try:
        from PySide6.QtGui import QAction, QKeySequence

        bar = menu_bar if menu_bar is not None else workbench.menuBar()
        tools_menu = bar.addMenu("&도구")
        action = QAction("📄 참조 PDF 위에 차이 표시...", workbench)
        action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        action.setStatusTip(
            "현재 도면 비교의 변경 구역을 사용자가 선택한 고해상도 PDF 페이지 "
            "위에 정합 오버레이로 표시합니다 (ADR-003 하이브리드 뷰어)."
        )
        action.triggered.connect(lambda: show_diff_over_reference_pdf(workbench))
        tools_menu.addAction(action)

        # ADR-003 H5e — 90° rotation direction toggle. The auto-detector picks
        # whether a turn is needed (landscape DWG ↔ portrait PDF); this toggle
        # flips CW↔CCW when the overlay lands mirrored.
        rotate_action = QAction("↻ 참조 PDF 90° 회전 방향 전환", workbench)
        rotate_action.setCheckable(True)
        rotate_action.setStatusTip(
            "오버레이가 90° 돌아간 위치에 찍히면 켜서 회전 방향(시계/반시계)을 뒤집습니다."
        )
        tools_menu.addAction(rotate_action)

        # ADR-003 H5f — scope to the current view region. Real working DWGs pack
        # many sheets in one modelspace; this maps only the changes in the area
        # you have framed onto the single PDF sheet. ON by default.
        scope_action = QAction("🔲 현재 보기 영역만 (다중구역 도면)", workbench)
        scope_action.setCheckable(True)
        scope_action.setChecked(True)
        scope_action.setStatusTip(
            "켜면 현재 뷰어에 보이는 영역(도곽)의 변경만 PDF에 정합합니다. "
            "끄면 도면 전체를 한 PDF에 매핑(단일 시트 도면용)."
        )
        tools_menu.addAction(scope_action)

        workbench._reference_pdf_action = action  # keep refs alive
        workbench._reference_pdf_rotate_action = rotate_action
        workbench._reference_pdf_scope_action = scope_action
        return True
    except Exception:  # noqa: BLE001 — menu build must survive this
        logger.debug("attach_reference_pdf_action failed", exc_info=True)
        return False


def _active_pair_id(workbench: Any) -> str:
    row = getattr(workbench, "_active_row", None)
    if isinstance(row, dict):
        return str(row.get("pair_id") or "")
    return ""


def _active_overlays(workbench: Any) -> List[dict]:
    by_zone = getattr(workbench, "_active_overlays_by_zone", None)
    if isinstance(by_zone, dict) and by_zone:
        return [ov for ov in by_zone.values() if isinstance(ov, dict)]
    return []


def _dwg_frame_bbox(workbench: Any, pair_id: str) -> Optional[Tuple[float, float, float, float]]:
    """Best available DWG drawing frame: the active pair's render world bbox.

    Uses ``_transform_world_bbox_v2`` over the after/before transform — the
    drawing's world extents the render framed — NOT the change-zone union
    (which mispositions overlays when changes cluster, ADR-003 §H5d risk).
    """

    pairs = getattr(workbench, "_viewer_pairs_by_id", {}) or {}
    viewer_pair = pairs.get(pair_id, {}) if isinstance(pairs, dict) else {}
    transform = None
    if isinstance(viewer_pair, dict):
        transform = viewer_pair.get("after_transform") or viewer_pair.get("before_transform")
    helper = getattr(workbench, "_transform_world_bbox_v2", None)
    if transform and callable(helper):
        try:
            bbox = helper(transform)
        except Exception:  # noqa: BLE001
            logger.debug("frame bbox derivation failed", exc_info=True)
            bbox = None
        if bbox and len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
            return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    return None


def _page_points_from_viewport(viewport: Any) -> Optional[Tuple[float, float]]:
    """Read the (width_pt, height_pt) the viewport set from the PDF page."""

    wb = getattr(viewport, "_world_bbox", None)
    if wb and len(wb) == 4:
        w = float(wb[2]) - float(wb[0])
        h = float(wb[3]) - float(wb[1])
        if w > 0 and h > 0:
            return (w, h)
    return None


def _capture_view_region(workbench: Any) -> Optional[Tuple[float, float, float, float]]:
    """ADR-003 H5f — the cad-world rect currently framed in the viewer.

    Multi-region working DWGs hold many sheets in one modelspace; the user
    frames the 도곽 of interest (zoom/pan) and we scope overlays to that rect.
    Read from the CAD render BEFORE the PDF replaces the background. Tries the
    after side first, then before.
    """

    for attr in ("preview_after_lightweight_v2", "preview_before_lightweight_v2"):
        vp = getattr(workbench, attr, None)
        getter = getattr(vp, "visible_world_rect", None) if vp is not None else None
        if callable(getter):
            try:
                rect = getter()
            except Exception:  # noqa: BLE001
                rect = None
            if rect and len(rect) == 4 and rect[2] > rect[0] and rect[3] > rect[1]:
                return (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    return None


def show_diff_over_reference_pdf(workbench: Any, page_index: int = 0) -> bool:
    """Handler: overlay the active DWG diff onto a user-chosen reference PDF.

    Returns True when overlays were placed on at least one side. All failure
    modes are surfaced to the user (dialog / side-message) instead of silently
    doing nothing.
    """

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from src.services.comparison.cad_pdf_overlay import (
        DEFAULT_REFERENCE_DPI,
        build_reference_pdf_overlays,
        filter_overlays_to_region,
        recommended_quarter_turns,
    )

    def _action_checked(attr: str, default: bool = False) -> bool:
        act = getattr(workbench, attr, None)
        try:
            return bool(act.isChecked()) if act is not None else default
        except Exception:  # noqa: BLE001
            return default

    def _rotate_dir_flipped() -> bool:
        return _action_checked("_reference_pdf_rotate_action", False)

    def _warn(msg: str) -> bool:
        try:
            QMessageBox.information(workbench, "참조 PDF 오버레이", msg)
        except Exception:  # noqa: BLE001
            logger.info("reference-PDF overlay: %s", msg)
        return False

    pair_id = _active_pair_id(workbench)
    overlays = _active_overlays(workbench)
    if not overlays:
        return _warn("표시할 변경 구역이 없습니다. 먼저 도면 비교를 실행하고 한 쌍을 선택하세요.")

    frame = _dwg_frame_bbox(workbench, pair_id)
    if frame is None:
        return _warn(
            "DWG 도곽(월드 익스텐트)을 확인할 수 없습니다. 렌더 미리보기가 준비된 "
            "도면 쌍을 선택한 뒤 다시 시도하세요."
        )

    # ADR-003 H5f — scope to the framed region for multi-region drawings.
    # Capture the cad-world view rect NOW, before the PDF replaces the CAD
    # background; use it as both the change filter and the alignment frame.
    if _action_checked("_reference_pdf_scope_action", True):
        region = _capture_view_region(workbench)
        if region is not None:
            scoped = filter_overlays_to_region(overlays, region)
            if not scoped:
                return _warn(
                    "현재 보기 영역 안에 변경 구역이 없습니다. 비교할 도곽이 "
                    "화면에 보이도록 확대/이동한 뒤 다시 시도하세요."
                )
            logger.info("H5f region scope: %d/%d overlays in view rect %s",
                        len(scoped), len(overlays), region)
            overlays = scoped
            frame = region

    pdf_path, _ = QFileDialog.getOpenFileName(
        workbench, "참조 PDF 선택", "", "PDF 파일 (*.pdf)"
    )
    if not pdf_path:
        return False
    pdf = Path(pdf_path)

    viewports = [
        (getattr(workbench, "preview_before_lightweight_v2", None), "before"),
        (getattr(workbench, "preview_after_lightweight_v2", None), "after"),
    ]
    placed_any = False
    quality_seen = ""
    focus = getattr(workbench, "_active_zone_id", "") or ""

    for viewport, side in viewports:
        if viewport is None:
            continue
        try:
            loaded = viewport.load_pdf_page(
                pdf, page_index=page_index, target_dpi=_PDF_BACKGROUND_DPI
            )
        except Exception:  # noqa: BLE001
            logger.exception("reference PDF load failed (%s side)", side)
            loaded = False
        if not loaded:
            try:
                viewport.set_side_message(f"{pdf.name}: PDF 페이지를 불러오지 못했습니다.")
            except Exception:  # noqa: BLE001
                pass
            continue

        page_points = _page_points_from_viewport(viewport)
        # ADR-003 H5e — auto-detect the 90° turn (landscape DWG ↔ portrait PDF);
        # the toggle flips CW↔CCW (1↔3) when the result lands mirrored.
        k = recommended_quarter_turns(frame, page_points)
        if k and _rotate_dir_flipped():
            k = (4 - k) % 4
        converted, quality = build_reference_pdf_overlays(
            overlays,
            dwg_frame_bbox=frame,
            page_points_wh=page_points,
            dpi=DEFAULT_REFERENCE_DPI,
            page_rotation_quarter_turns=k,
        )
        quality_seen = quality or quality_seen
        try:
            viewport.push_change_overlays_from_v1(
                converted, side=side, focus_zone_id=focus
            )
        except Exception:  # noqa: BLE001
            logger.exception("push reference overlays failed (%s side)", side)
            continue

        if converted:
            placed_any = True
        _apply_quality_feedback(viewport, quality, len(converted))

    if not placed_any:
        return _warn(
            "오버레이를 PDF 위에 배치하지 못했습니다 (정합 품질: "
            f"{quality_seen or 'relative_only'}). 도곽과 PDF의 가로:세로 비율이 "
            "크게 다르면 정합이 불가능합니다."
        )
    return True


def _apply_quality_feedback(viewport: Any, quality: str, count: int) -> None:
    """Surface alignment quality honestly (S1 pattern) on the viewport badge."""

    try:
        if quality == "exact":
            # raster_refined = authoritative real-raster surface (the PDF page).
            viewport.set_fidelity_state(
                "raster_refined", status_text=f"PDF 정합 / {count}개 변경"
            )
            viewport.set_side_message("")
        elif quality == "estimated":
            viewport.set_fidelity_state(
                "raster_refined", status_text=f"PDF 정합(추정) / {count}개 변경"
            )
            viewport.set_side_message(
                "⚠ 도곽과 PDF 비율이 달라 위치가 추정입니다 (plot 설정 차이 가능)."
            )
        else:
            viewport.set_fidelity_state(
                "relative_only", status_text="PDF 정합 불가"
            )
            viewport.set_side_message(
                "정합 불가 — 상대 위치로 대체되었습니다."
            )
    except Exception:  # noqa: BLE001
        logger.debug("quality feedback failed", exc_info=True)


__all__ = ["attach_reference_pdf_action", "show_diff_over_reference_pdf"]
