# -*- coding: utf-8 -*-
"""Phase I — PDF cloud → DXF export.

For PDF input pairs the existing CAD-only ``_write_cloud_artifacts``
path (change_zones.py:1103) skips DXF export entirely with a
"cloud-mark DXF export is CAD-only" warning. Customers reviewing
PDF revisions get a PNG cloud overlay but no CAD-importable artifact
they can lay over their working files.

This module fills that gap by building a synthetic DXF whose entities
correspond to the change-zone bboxes:

    * Each confirmed zone → a closed POLYLINE rectangle on layer
      ``CLOUD_MARKS`` (red, lineweight 0.50mm)
    * Each zone gets a TEXT label with its zone_id at the top-left
      corner (height proportional to bbox)
    * A border POLYLINE traces the page extents on layer
      ``PDF_PAGE_BOUNDS`` (gray) so the engineer can see how the
      marks align with the original PDF page

Coordinate space: DXF units = millimeters, origin at PDF page
bottom-left. Y axis is FLIPPED from PDF coordinate (PDF top-left
origin → DXF bottom-left origin) so the layout reads naturally in
AutoCAD / Tekla.

Public API
----------
``export_cloud_marks_to_dxf(...)`` — single entry, mirrors the shape
of ``confirmed_cloud_export.export_confirmed_cloud_marks`` so the
workbench can invoke both for a PDF run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from .review_helpers import (
    confirmed_zone_ids_for_pair as _shared_confirmed_zone_ids,
    safe_pair_name as _shared_safe_pair_name,
    resolve_pixel_bbox as _shared_resolve_pixel_bbox,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass — mirrors confirmed_cloud_export.ConfirmedCloudExportResult
# ---------------------------------------------------------------------------


@dataclass
class PdfCloudDxfExportResult:
    pair_id: str
    output_path: str  # empty when skipped
    confirmed_zone_count: int
    skipped_reason: str = ""
    layer_name: str = "CLOUD_MARKS"
    page_count: int = 0


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------


def _bbox_pdf_pixels_to_mm(
    bbox_px: Sequence[float],
    *,
    pdf_dpi: float,
    page_height_px: float,
) -> Optional[tuple[float, float, float, float]]:
    """Convert a PDF pixel-space bbox to DXF mm with Y-axis flip.

    PDF pixels (top-left origin, Y down at ``pdf_dpi``):
        bbox = (x0, y0, x1, y1)
    DXF mm (bottom-left origin, Y up):
        x_mm = x_px * 25.4 / pdf_dpi
        y_mm = (page_height_px - y_px) * 25.4 / pdf_dpi
    Returns ``(xmin_mm, ymin_mm, xmax_mm, ymax_mm)`` with bottom-left
    convention, or ``None`` for invalid input.

    Phase I review fix #3: defensive geometry rejection
        * page_height_px <= 0 → None (silent oversize was producing
          DXF marks floating off the sheet)
        * bbox can't be parsed → None
        * degenerate bbox (zero area) → None
        * coordinates outside reasonable PDF page bounds → None
    """

    if page_height_px <= 0:
        logger.warning(
            "pdf_cloud_dxf_export: rejecting bbox conversion (page_height_px=%s)",
            page_height_px,
        )
        return None
    try:
        x0_px, y0_px, x1_px, y1_px = (float(v) for v in bbox_px[:4])
    except (TypeError, ValueError):
        logger.warning(
            "pdf_cloud_dxf_export: bbox unparseable: %r", bbox_px,
        )
        return None
    # Normalise ordering — accept reversed bboxes, reject degenerate
    x_min, x_max = min(x0_px, x1_px), max(x0_px, x1_px)
    y_min, y_max = min(y0_px, y1_px), max(y0_px, y1_px)
    if x_max - x_min < 0.5 or y_max - y_min < 0.5:
        logger.debug(
            "pdf_cloud_dxf_export: skipping degenerate bbox: %r", bbox_px,
        )
        return None

    if pdf_dpi <= 0:
        pdf_dpi = 200.0
    px_to_mm = 25.4 / pdf_dpi
    xmin = x_min * px_to_mm
    xmax = x_max * px_to_mm
    # Flip Y — PDF top y becomes DXF bottom y
    ymax_dxf = (page_height_px - y_min) * px_to_mm
    ymin_dxf = (page_height_px - y_max) * px_to_mm
    return xmin, ymin_dxf, xmax, ymax_dxf


def _resolve_pixel_bbox_for_dxf(
    overlay: dict,
) -> Optional[tuple[float, float, float, float]]:
    """Thin wrapper over the shared bbox parser.

    Phase I review fix #5: extracted to ``review_helpers.resolve_pixel_bbox``
    so PDF DXF export and PNG cloud export stay in lockstep. The
    shared helper also normalises bbox ordering so reversed bboxes
    don't produce inverted DXF rectangles.
    """

    return _shared_resolve_pixel_bbox(overlay)


# ---------------------------------------------------------------------------
# Confirmed-zone selector — same contract as confirmed_cloud_export
# ---------------------------------------------------------------------------


# Phase I review fix #5: confirmed-zone selector + safe pair name
# delegated to ``review_helpers`` so PDF DXF / PNG cloud / future
# exporters share one implementation + one test set.
_confirmed_zone_ids_for_pair = _shared_confirmed_zone_ids
_safe_pair_name = _shared_safe_pair_name
CONFIRMED_STATUS = "confirmed"  # re-exported for back-compat


# ---------------------------------------------------------------------------
# Page-size resolution — needed for Y axis flip
# ---------------------------------------------------------------------------


def _resolve_page_height_px(
    pdf_path: Optional[str | Path], pdf_dpi: float, page_index: int = 0,
) -> Optional[float]:
    """Read the PDF page height in pixels at ``pdf_dpi``."""

    if not pdf_path:
        return None
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        try:
            if page_index < 0 or page_index >= doc.page_count:
                page_index = 0
            page = doc[page_index]
            # PDF height in points; pixels = points * dpi / 72
            return page.rect.height * (pdf_dpi / 72.0)
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to read PDF page size for %s", pdf_path)
        return None


# ---------------------------------------------------------------------------
# Public entry — build the DXF
# ---------------------------------------------------------------------------


def export_cloud_marks_to_dxf(
    *,
    pair_id: str,
    overlays: list[dict],
    review_records: dict[str, Any],
    output_dir: Path,
    pdf_path: Optional[str | Path],
    pdf_dpi: float = 200.0,
    page_index: int = 0,
    label_prefix: str = "확인",
    allowed_output_root: Optional[Path] = None,
) -> PdfCloudDxfExportResult:
    """Build a DXF whose entities mark the confirmed change zones.

    Args:
        pair_id: Workbench pair identifier (used in output filename).
        overlays: Overlay dicts from the workbench
            (``_active_overlays_by_zone.values()`` shape).
        review_records: ReviewStateRecord-like map (same shape as
            ``confirmed_cloud_export``).
        output_dir: Destination folder.
        pdf_path: PDF source — required for page height (Y flip).
            Skipped with a friendly reason when absent.
        pdf_dpi: DPI the comparison engine used (default 200).
        page_index: Page on which the overlays live (default 0).
        label_prefix: Korean prefix for the text labels (default 확인).
        allowed_output_root: 2nd-review fix (P0): the parent directory
            ``output_dir`` MUST live under. None (default) uses the
            project's ``out/`` directory. Production callers (workbench)
            pass their session artifact root; tests pass ``tmp_path``.
            Without this, fail-closed validation would either accept
            arbitrary paths (security risk) or reject legitimate
            workbench paths.

    Returns ``PdfCloudDxfExportResult`` mirroring the shape of
    ``ConfirmedCloudExportResult`` so the workbench can dispatch a
    unified result row.
    """

    # 2nd-review fix (P0): NEVER touch the filesystem before validation.
    # Previously `mkdir()` ran on the raw caller-supplied path BEFORE
    # validate_output_path(), so a wrong / malicious caller could create
    # arbitrary directories. Now: import validators → validate (with
    # allowed_base_dir = the project's artifact root) → ONLY THEN mkdir.
    try:
        from src.utils.security_validators import (
            validate_output_path,
            safe_file_open,
            validate_pdf_pages,
            get_allowed_output_dir,
        )
    except ImportError:
        # security_validators is part of the project — if it's missing
        # we're in a broken install. Don't silently bypass.
        return PdfCloudDxfExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=0,
            skipped_reason="보안 검증기 미사용 — 설치 손상 가능. 관리자 문의.",
        )

    try:
        # 2nd-review fix (P0): pin output under an explicit allowed
        # base. Default = project's out/ dir; caller can pass session
        # artifact root or test tmp_path. `allowed_base_dir=None`
        # (the previous default) accepted ANY absolute path which a
        # future caller could exploit accidentally.
        allowed_root = (
            Path(allowed_output_root).resolve()
            if allowed_output_root
            else get_allowed_output_dir()
        )
        # Make sure the allowed root exists so validate_path doesn't
        # reject it on first use (tmp_path pytest fixtures already do)
        allowed_root.mkdir(parents=True, exist_ok=True)
        validated_output_dir = validate_output_path(
            output_dir,
            allowed_base_dir=allowed_root,
        )
    except Exception as exc:  # noqa: BLE001
        return PdfCloudDxfExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=0,
            skipped_reason=f"출력 경로 검증 실패: {exc}",
        )

    # ONLY NOW is it safe to materialise the directory.
    # 3rd-review fix (P1): wrap mkdir in try/except too — Windows can
    # still refuse on OS-level path issues (alternate data streams,
    # invalid chars in segments) that validate_path doesn't catch.
    output_dir = validated_output_dir
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, NotADirectoryError) as exc:
        return PdfCloudDxfExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=0,
            skipped_reason=f"출력 경로 생성 실패 (OS 거부): {exc}",
        )

    # PDF source must exist + be a real PDF file + within safe page count
    safe_pdf_path: Optional[Path] = None
    if pdf_path:
        try:
            safe_pdf_path = safe_file_open(
                pdf_path,
                max_size_mb=200.0,
                allowed_extensions={".pdf"},
            )
            validate_pdf_pages(safe_pdf_path, max_pages=200)
        except Exception as exc:  # noqa: BLE001
            return PdfCloudDxfExportResult(
                pair_id=pair_id,
                output_path="",
                confirmed_zone_count=0,
                skipped_reason=f"PDF 입력 검증 실패: {exc}",
            )

    confirmed_zone_ids = _confirmed_zone_ids_for_pair(pair_id, review_records)
    if not confirmed_zone_ids:
        return PdfCloudDxfExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=0,
            skipped_reason="확인(confirmed) 상태 변경구역이 없습니다.",
        )

    confirmed_overlays = [
        overlay
        for overlay in (overlays or [])
        if isinstance(overlay, dict)
        and str(overlay.get("zone_id") or "") in confirmed_zone_ids
    ]
    if not confirmed_overlays:
        return PdfCloudDxfExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=len(confirmed_zone_ids),
            skipped_reason="확인된 변경구역에 일치하는 overlay 데이터를 찾지 못했습니다.",
        )

    # Page height — must read from the PDF for the Y flip to be right.
    # Use the validated path so we don't re-traverse the original input.
    page_height_px = _resolve_page_height_px(
        safe_pdf_path, pdf_dpi, page_index=page_index,
    ) if safe_pdf_path else None
    if page_height_px is None:
        return PdfCloudDxfExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=len(confirmed_overlays),
            skipped_reason="PDF 페이지 크기를 읽지 못했습니다 (DXF 좌표 변환 불가).",
        )
    page_width_px = None
    try:
        import fitz
        d = fitz.open(str(safe_pdf_path))
        try:
            page = d[page_index if 0 <= page_index < d.page_count else 0]
            page_width_px = page.rect.width * (pdf_dpi / 72.0)
        finally:
            d.close()
    except Exception:
        page_width_px = None

    try:
        import ezdxf
    except ImportError:
        return PdfCloudDxfExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=len(confirmed_overlays),
            skipped_reason="ezdxf 미설치 — pip install ezdxf",
        )

    # Phase P (RV-20260508-014) — AIA 표준 색상 + lineweight + revcloud
    from .revision_marker import (
        ACI_CYAN, ACI_GRAY, ACI_YELLOW,
        LINEWEIGHT_REVCLOUD_MM, LINEWEIGHT_LABEL_MM,
        add_revcloud_to_msp,
        add_revision_triangle_to_msp,
    )

    doc = ezdxf.new(dxfversion="R2010")
    msp = doc.modelspace()

    # Layers — AIA 표준 (CLOUD_MARKS = cyan, CLOUD_LABELS = yellow)
    if "CLOUD_MARKS" not in doc.layers:
        doc.layers.add("CLOUD_MARKS", color=ACI_CYAN, lineweight=LINEWEIGHT_REVCLOUD_MM)
    if "CLOUD_LABELS" not in doc.layers:
        doc.layers.add("CLOUD_LABELS", color=ACI_YELLOW, lineweight=LINEWEIGHT_LABEL_MM)
    if "PDF_PAGE_BOUNDS" not in doc.layers:
        doc.layers.add("PDF_PAGE_BOUNDS", color=ACI_GRAY, lineweight=18)  # gray

    # Page boundary rectangle
    if page_width_px and page_width_px > 0:
        page_w_mm = page_width_px * 25.4 / pdf_dpi
        page_h_mm = page_height_px * 25.4 / pdf_dpi
        msp.add_lwpolyline(
            [(0, 0), (page_w_mm, 0), (page_w_mm, page_h_mm),
             (0, page_h_mm), (0, 0)],
            close=True,
            dxfattribs={"layer": "PDF_PAGE_BOUNDS"},
        )

    # One rectangle + label per confirmed zone
    written = 0
    for idx, overlay in enumerate(confirmed_overlays, start=1):
        bbox_px = _resolve_pixel_bbox_for_dxf(overlay)
        if bbox_px is None:
            continue
        # Phase I review fix #3: invalid geometry now returns None
        # (previously produced silent off-sheet rectangles)
        converted = _bbox_pdf_pixels_to_mm(
            bbox_px,
            pdf_dpi=pdf_dpi,
            page_height_px=page_height_px,
        )
        if converted is None:
            continue
        x0, y0, x1, y1 = converted
        # Phase P (RV-20260508-014) — AIA 표준 revision cloud (bumpy
        # closed polyline) + 안쪽 revision triangle. AutoCAD 의 REVCLOUD
        # 와 호환되어 사용자가 도면을 열었을 때 즉시 표준 표기로 인식.
        change_kind = str(overlay.get("change_type") or "modified")
        if change_kind not in ("modified", "added", "deleted", "mixed"):
            change_kind = "modified"
        add_revcloud_to_msp(
            msp,
            (x0, y0, x1, y1),
            layer="CLOUD_MARKS",
            kind=change_kind,  # type: ignore[arg-type]
        )
        # Revision triangle — bbox 우상단 외부에 배치
        triangle_size_mm = max(4.0, min(10.0, (y1 - y0) * 0.18))
        triangle_anchor = (x1 + triangle_size_mm, y1 + triangle_size_mm)
        add_revision_triangle_to_msp(
            msp,
            triangle_anchor,
            revision_number=idx,
            size=triangle_size_mm,
            layer="CLOUD_LABELS",
            kind=change_kind,  # type: ignore[arg-type]
        )
        # Zone ID label — triangle 옆 (선택적, 기존 호환)
        zone_id = str(overlay.get("zone_id") or f"Z{idx}")
        text_h_mm = max(2.0, min(8.0, (y1 - y0) * 0.15))
        label = f"{label_prefix}{idx:02d} · {zone_id}"
        msp.add_text(
            label,
            dxfattribs={
                "layer": "CLOUD_LABELS",
                "height": text_h_mm,
            },
        ).set_placement((x0, y1 + text_h_mm * 0.3))
        written += 1

    if written == 0:
        return PdfCloudDxfExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=len(confirmed_overlays),
            skipped_reason=f"확인된 {len(confirmed_overlays)}개 zone 모두 좌표 정보가 없어 DXF 생성을 건너뜁니다.",
        )

    output_path = output_dir / f"{_safe_pair_name(pair_id)}_pdf_cloud.dxf"
    try:
        doc.saveas(str(output_path))
    except Exception as exc:
        return PdfCloudDxfExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=len(confirmed_overlays),
            skipped_reason=f"DXF 저장 실패: {exc}",
        )

    return PdfCloudDxfExportResult(
        pair_id=pair_id,
        output_path=str(output_path),
        confirmed_zone_count=written,
        skipped_reason="",
        page_count=1,
    )
