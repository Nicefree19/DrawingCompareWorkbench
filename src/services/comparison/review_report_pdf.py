# -*- coding: utf-8 -*-
"""1-click PDF review report generator.

Compiles the reviewer's session into a single A4 PDF the operator can email,
print, or attach to a project ticket. The report intentionally focuses on the
confirmed-only artefacts (the only marks the reviewer agreed are real) so the
recipient gets the minimum viable hand-off package.

Layout:
- Page 1 — Cover: project name, run timestamp, totals (총 변경, 확인/보류/오탐).
- Page 2..N — One per drawing pair: confirmed-cloud PNG (full bleed) + zone
  memo table.
- Last page — Appendix: cumulative zone list (zone_id · 도면 · 상태 · 메모).

This module deliberately uses PyMuPDF (already required by viewer_package for
PDF rendering) so we don't pull in another PDF dependency just for the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Optional

from .review_project import normalize_review_status

A4_WIDTH = 595.0
A4_HEIGHT = 842.0
MARGIN = 36.0
COVER_TITLE_SIZE = 24
SECTION_TITLE_SIZE = 14
BODY_SIZE = 10
HEADER_COLOR = (0.067, 0.094, 0.153)  # near #111827
ACCENT_COLOR_FALLBACK = (0.86, 0.15, 0.15)  # red — match cloud mark
MUTED_COLOR = (0.42, 0.45, 0.50)
TABLE_HEADER_BG = (0.93, 0.94, 0.96)
TABLE_ROW_ALT_BG = (0.97, 0.98, 0.99)


@dataclass
class ReviewReportInput:
    """Bundle of facts the report draws from."""

    project_label: str
    run_started_at: str
    source_a: str
    source_b: str
    drawing_rows: list[dict]  # subset of dashboard["drawings"] for context
    review_records: dict[str, Any]  # {key: ReviewStateRecord | dict}
    confirmed_cloud_dir: Optional[Path]  # produced by confirmed_cloud_export
    overlays_by_pair: dict[str, list[dict]]  # pair_id -> overlay dict list
    settings: Any = None  # ReportSettings | None — branding/reviewer profile


@dataclass
class ReviewReportResult:
    output_path: str
    page_count: int
    confirmed_total: int
    ignored_total: int
    false_positive_total: int
    needs_review_total: int


def generate_review_report_pdf(
    *,
    inputs: ReviewReportInput,
    output_path: Path,
) -> ReviewReportResult:
    """Compose and save the review summary PDF."""

    import fitz  # PyMuPDF

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()

    counts = _count_review_status(inputs.review_records)
    accent = _accent_color(inputs.settings)
    settings_obj = inputs.settings

    # Cover page (with branding strip + reviewer info card)
    _draw_cover_page(doc, inputs, counts, accent, settings_obj)

    # Per-pair pages with confirmed cloud PNG + memo table
    pair_ids = sorted({
        rec_pair_id(r)
        for r in inputs.review_records.values()
        if rec_status(r) == "confirmed" and rec_pair_id(r)
    })
    for pair_id in pair_ids:
        _draw_pair_page(doc, pair_id, inputs, accent)

    # Appendix — full zone table
    _draw_appendix_page(doc, inputs, accent)

    # Sign-off page (only when reviewer or signature is configured)
    if settings_obj is not None and (
        getattr(settings_obj, "reviewer_name", "") or getattr(settings_obj, "reviewer_signature_path", "")
    ):
        _draw_signoff_page(doc, inputs, accent, settings_obj)

    # Apply page footers (number, project, reviewer, company name)
    _apply_page_footers(doc, inputs, settings_obj)

    doc.save(str(output_path))
    page_count = doc.page_count
    doc.close()

    return ReviewReportResult(
        output_path=str(output_path),
        page_count=page_count,
        confirmed_total=counts["confirmed"],
        ignored_total=counts["hold"],
        false_positive_total=counts["false_positive"],
        needs_review_total=counts["needs_review"],
    )


def _accent_color(settings: Any) -> tuple[float, float, float]:
    if settings is None:
        return ACCENT_COLOR_FALLBACK
    accessor = getattr(settings, "accent_color_rgb", None)
    if callable(accessor):
        try:
            value = accessor()
            if isinstance(value, tuple) and len(value) == 3:
                return value
        except Exception:
            pass
    if isinstance(accessor, tuple) and len(accessor) == 3:
        return accessor
    return ACCENT_COLOR_FALLBACK


# --- Drawing helpers -------------------------------------------------------


def _draw_cover_page(
    doc: Any,
    inputs: ReviewReportInput,
    counts: dict[str, int],
    accent: tuple[float, float, float],
    settings: Any,
) -> None:
    import fitz

    page = doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)

    # Top accent bar (full-width thick stripe in the brand color)
    page.draw_rect(
        fitz.Rect(0, 0, A4_WIDTH, 12),
        color=accent,
        fill=accent,
    )

    # Optional company logo (top-left within margin)
    logo_path = _resolve_image_path(getattr(settings, "company_logo_path", "") if settings else "")
    logo_height = 44
    if logo_path and logo_path.exists():
        logo_rect = fitz.Rect(MARGIN, 28, MARGIN + 140, 28 + logo_height)
        try:
            page.insert_image(logo_rect, filename=str(logo_path), keep_proportion=True)
        except Exception:
            pass

    # Company name (top-right)
    company_name = (getattr(settings, "company_name", "") if settings else "") or "센엔지니어링 그룹"
    page.insert_text(
        (A4_WIDTH - MARGIN - 200, 50),
        company_name,
        fontsize=11,
        color=HEADER_COLOR,
        fontname="helv",
    )

    # Big title
    title_y = 28 + logo_height + 60
    page.insert_text(
        (MARGIN, title_y),
        "도면 변경 검토 보고서",
        fontsize=COVER_TITLE_SIZE,
        color=HEADER_COLOR,
        fontname="helv",
    )
    page.draw_line(
        fitz.Point(MARGIN, title_y + 10),
        fitz.Point(MARGIN + 240, title_y + 10),
        color=accent,
        width=2.4,
    )

    # Project + sources block
    block_y = title_y + 36
    block_lines = [
        ("프로젝트", inputs.project_label or "-"),
        ("비교 실행", _format_ts(inputs.run_started_at)),
        ("변경 전", _short_path(inputs.source_a)),
        ("변경 후", _short_path(inputs.source_b)),
        ("보고서 생성", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    for label, value in block_lines:
        page.insert_text((MARGIN, block_y), f"{label}", fontsize=BODY_SIZE, color=MUTED_COLOR)
        page.insert_text((MARGIN + 90, block_y), value, fontsize=BODY_SIZE, color=HEADER_COLOR)
        block_y += 16

    # Reviewer card
    reviewer_y = block_y + 18
    card_rect = fitz.Rect(MARGIN, reviewer_y, A4_WIDTH - MARGIN, reviewer_y + 64)
    page.draw_rect(card_rect, color=MUTED_COLOR, width=0.4)
    page.insert_text(
        (MARGIN + 10, reviewer_y + 18),
        "검토자",
        fontsize=BODY_SIZE,
        color=accent,
        fontname="helv",
    )
    reviewer_line = (
        settings.reviewer_one_line() if settings is not None and hasattr(settings, "reviewer_one_line")
        else "(검토자 정보 미입력 — 보고서 설정에서 입력 가능)"
    )
    page.insert_text(
        (MARGIN + 60, reviewer_y + 18),
        reviewer_line,
        fontsize=BODY_SIZE,
        color=HEADER_COLOR,
    )
    contact = (getattr(settings, "reviewer_contact", "") if settings else "") or "-"
    page.insert_text(
        (MARGIN + 10, reviewer_y + 42),
        "연락처",
        fontsize=BODY_SIZE,
        color=accent,
        fontname="helv",
    )
    page.insert_text(
        (MARGIN + 60, reviewer_y + 42),
        contact,
        fontsize=BODY_SIZE,
        color=HEADER_COLOR,
    )

    # Stats card with accent left bar
    stats_y = reviewer_y + 88
    page.insert_text(
        (MARGIN, stats_y),
        "검토 통계",
        fontsize=SECTION_TITLE_SIZE,
        color=HEADER_COLOR,
        fontname="helv",
    )
    stats_y += 8
    page.draw_line(
        fitz.Point(MARGIN, stats_y),
        fitz.Point(A4_WIDTH - MARGIN, stats_y),
        color=accent,
        width=1.0,
    )
    stats_y += 12
    total = sum(counts.get(key, 0) for key in ("confirmed", "hold", "false_positive", "needs_review"))
    rows = [
        ("총 변경구역", str(total), False),
        ("확인 (confirmed)", str(counts["confirmed"]), True),
        ("보류 (hold)", str(counts["hold"]), False),
        ("오탐 (false_positive)", str(counts["false_positive"]), False),
        ("미검토 (needs_review)", str(counts["needs_review"]), False),
    ]
    for idx, (label, value, highlight) in enumerate(rows):
        if idx % 2 == 0:
            page.draw_rect(
                fitz.Rect(MARGIN, stats_y - 10, A4_WIDTH - MARGIN, stats_y + 4),
                color=TABLE_ROW_ALT_BG,
                fill=TABLE_ROW_ALT_BG,
            )
        page.insert_text((MARGIN + 8, stats_y), label, fontsize=BODY_SIZE, color=HEADER_COLOR)
        page.insert_text(
            (MARGIN + 240, stats_y),
            value,
            fontsize=BODY_SIZE,
            color=accent if highlight else HEADER_COLOR,
            fontname="helv",
        )
        stats_y += 16


def _draw_pair_page(doc: Any, pair_id: str, inputs: ReviewReportInput, accent: tuple[float, float, float]) -> None:
    import fitz

    page = doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)
    drawing_label = _drawing_label_for_pair(pair_id, inputs.drawing_rows)

    # Section header strip
    page.draw_rect(
        fitz.Rect(MARGIN, MARGIN, A4_WIDTH - MARGIN, MARGIN + 28),
        color=accent,
        fill=accent,
    )
    page.insert_text(
        (MARGIN + 10, MARGIN + 18),
        f"도면: {drawing_label}",
        fontsize=SECTION_TITLE_SIZE,
        color=(1.0, 1.0, 1.0),
        fontname="helv",
    )
    page.insert_text(
        (MARGIN, MARGIN + 42),
        f"pair_id: {pair_id}",
        fontsize=8,
        color=MUTED_COLOR,
    )

    # Confirmed cloud image (top half)
    image_y0 = MARGIN + 56
    image_y1 = A4_HEIGHT * 0.60
    image_rect = fitz.Rect(MARGIN, image_y0, A4_WIDTH - MARGIN, image_y1)
    cloud_png = _resolve_confirmed_cloud_png(pair_id, inputs.confirmed_cloud_dir)
    if cloud_png and cloud_png.exists():
        page.draw_rect(image_rect, color=MUTED_COLOR, width=0.4)
        page.insert_image(image_rect, filename=str(cloud_png), keep_proportion=True)
    else:
        page.draw_rect(image_rect, color=MUTED_COLOR, width=0.5)
        page.insert_text(
            (image_rect.x0 + 8, image_rect.y0 + 16),
            "(확인된 변경 구름마크 PNG가 아직 생성되지 않았습니다 — Workbench의 [확인된 변경 구름마크 추출] 버튼을 먼저 실행하세요.)",
            fontsize=BODY_SIZE,
            color=MUTED_COLOR,
        )

    # Memo table — alternating rows + header bg
    table_y = image_y1 + 16
    page.insert_text((MARGIN, table_y), "검토 메모", fontsize=SECTION_TITLE_SIZE, color=HEADER_COLOR, fontname="helv")
    table_y += 12
    page.draw_line(fitz.Point(MARGIN, table_y), fitz.Point(A4_WIDTH - MARGIN, table_y), color=accent, width=1.0)
    table_y += 6
    pair_records = [r for r in inputs.review_records.values() if rec_pair_id(r) == pair_id]
    pair_records.sort(key=lambda r: rec_zone_id(r))
    if not pair_records:
        page.insert_text(
            (MARGIN + 4, table_y + 12),
            "(이 도면에 기록된 검토 메모가 없습니다.)",
            fontsize=BODY_SIZE,
            color=MUTED_COLOR,
        )
        return

    # Header row with light grey background
    header_height = 18
    page.draw_rect(
        fitz.Rect(MARGIN, table_y, A4_WIDTH - MARGIN, table_y + header_height),
        color=TABLE_HEADER_BG,
        fill=TABLE_HEADER_BG,
    )
    header_baseline = table_y + 13
    page.insert_text((MARGIN + 6, header_baseline), "zone_id", fontsize=BODY_SIZE, color=HEADER_COLOR, fontname="helv")
    page.insert_text((MARGIN + 110, header_baseline), "상태", fontsize=BODY_SIZE, color=HEADER_COLOR, fontname="helv")
    page.insert_text((MARGIN + 180, header_baseline), "메모", fontsize=BODY_SIZE, color=HEADER_COLOR, fontname="helv")
    table_y += header_height

    row_height = 14
    for idx, rec in enumerate(pair_records):
        if table_y > A4_HEIGHT - MARGIN - 24:
            break
        if idx % 2 == 0:
            page.draw_rect(
                fitz.Rect(MARGIN, table_y, A4_WIDTH - MARGIN, table_y + row_height),
                color=TABLE_ROW_ALT_BG,
                fill=TABLE_ROW_ALT_BG,
            )
        zone = rec_zone_id(rec) or "-"
        status_label = _status_ko(rec_status(rec))
        note = rec_note(rec) or "-"
        if note.startswith("Workbench V2:"):
            note = "-"  # default auto-note hidden
        baseline = table_y + 10
        page.insert_text((MARGIN + 6, baseline), zone[:18], fontsize=BODY_SIZE - 1, color=HEADER_COLOR)
        page.insert_text(
            (MARGIN + 110, baseline),
            status_label,
            fontsize=BODY_SIZE - 1,
            color=accent if rec_status(rec) == "confirmed" else HEADER_COLOR,
            fontname="helv",
        )
        page.insert_text((MARGIN + 180, baseline), note[:80], fontsize=BODY_SIZE - 1, color=HEADER_COLOR)
        table_y += row_height


def _draw_appendix_page(doc: Any, inputs: ReviewReportInput, accent: tuple[float, float, float]) -> None:
    import fitz

    page = doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)
    page.insert_text(
        (MARGIN, MARGIN + 8),
        "부록 — 전체 변경구역 목록",
        fontsize=SECTION_TITLE_SIZE,
        color=HEADER_COLOR,
        fontname="helv",
    )
    y = MARGIN + 24
    page.draw_line(fitz.Point(MARGIN, y), fitz.Point(A4_WIDTH - MARGIN, y), color=accent, width=1.0)
    y += 6
    header_height = 18
    page.draw_rect(
        fitz.Rect(MARGIN, y, A4_WIDTH - MARGIN, y + header_height),
        color=TABLE_HEADER_BG,
        fill=TABLE_HEADER_BG,
    )
    page.insert_text((MARGIN + 6, y + 13), "도면", fontsize=BODY_SIZE, color=HEADER_COLOR, fontname="helv")
    page.insert_text((MARGIN + 200, y + 13), "zone_id", fontsize=BODY_SIZE, color=HEADER_COLOR, fontname="helv")
    page.insert_text((MARGIN + 320, y + 13), "상태", fontsize=BODY_SIZE, color=HEADER_COLOR, fontname="helv")
    page.insert_text((MARGIN + 380, y + 13), "메모", fontsize=BODY_SIZE, color=HEADER_COLOR, fontname="helv")
    y += header_height

    sorted_records = sorted(
        inputs.review_records.values(),
        key=lambda r: (rec_pair_id(r), rec_zone_id(r)),
    )
    row_height = 13
    for idx, rec in enumerate(sorted_records):
        if y > A4_HEIGHT - MARGIN - 24:
            page = doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)
            y = MARGIN + 8
        if idx % 2 == 0:
            page.draw_rect(
                fitz.Rect(MARGIN, y, A4_WIDTH - MARGIN, y + row_height),
                color=TABLE_ROW_ALT_BG,
                fill=TABLE_ROW_ALT_BG,
            )
        baseline = y + 10
        pair_label = _drawing_label_for_pair(rec_pair_id(rec), inputs.drawing_rows)[:28]
        page.insert_text((MARGIN + 6, baseline), pair_label, fontsize=BODY_SIZE - 1, color=HEADER_COLOR)
        page.insert_text((MARGIN + 200, baseline), rec_zone_id(rec)[:18], fontsize=BODY_SIZE - 1, color=HEADER_COLOR)
        page.insert_text(
            (MARGIN + 320, baseline),
            _status_ko(rec_status(rec)),
            fontsize=BODY_SIZE - 1,
            color=accent if rec_status(rec) == "confirmed" else HEADER_COLOR,
            fontname="helv",
        )
        note = rec_note(rec) or ""
        if note.startswith("Workbench V2:"):
            note = ""
        page.insert_text(
            (MARGIN + 380, baseline),
            note[:36],
            fontsize=BODY_SIZE - 1,
            color=MUTED_COLOR,
        )
        y += row_height


def _draw_signoff_page(
    doc: Any,
    inputs: ReviewReportInput,
    accent: tuple[float, float, float],
    settings: Any,
) -> None:
    """Optional final page with reviewer name + signature/stamp + date."""

    import fitz

    page = doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)
    page.draw_rect(
        fitz.Rect(MARGIN, MARGIN, A4_WIDTH - MARGIN, MARGIN + 28),
        color=accent,
        fill=accent,
    )
    page.insert_text(
        (MARGIN + 10, MARGIN + 18),
        "검토 확인",
        fontsize=SECTION_TITLE_SIZE,
        color=(1.0, 1.0, 1.0),
        fontname="helv",
    )

    # Reviewer info block
    y = MARGIN + 60
    info_rows = [
        ("검토자", getattr(settings, "reviewer_name", "") or "-"),
        ("직급", getattr(settings, "reviewer_title", "") or "-"),
        ("부서", getattr(settings, "reviewer_department", "") or "-"),
        ("연락처", getattr(settings, "reviewer_contact", "") or "-"),
        ("검토 일자", datetime.now().strftime("%Y-%m-%d")),
    ]
    for label, value in info_rows:
        page.insert_text((MARGIN, y), label, fontsize=BODY_SIZE, color=MUTED_COLOR)
        page.insert_text((MARGIN + 90, y), value, fontsize=BODY_SIZE, color=HEADER_COLOR)
        y += 18

    # Stamp/signature image area (right side)
    stamp_path = _resolve_image_path(getattr(settings, "reviewer_signature_path", "") or "")
    stamp_box = fitz.Rect(A4_WIDTH - MARGIN - 160, MARGIN + 60, A4_WIDTH - MARGIN, MARGIN + 220)
    page.draw_rect(stamp_box, color=MUTED_COLOR, width=0.6)
    page.insert_text(
        (stamp_box.x0 + 6, stamp_box.y0 + 14),
        "(인) / 서명",
        fontsize=BODY_SIZE - 1,
        color=MUTED_COLOR,
    )
    if stamp_path and stamp_path.exists():
        # Center the stamp image inside the stamp_box with padding
        pad = 12
        inner = fitz.Rect(stamp_box.x0 + pad, stamp_box.y0 + pad, stamp_box.x1 - pad, stamp_box.y1 - pad)
        try:
            page.insert_image(inner, filename=str(stamp_path), keep_proportion=True)
        except Exception:
            pass

    # Statement
    y += 24
    statement = (
        "본 보고서에 기재된 검토 결과(확인된 변경구역 + 메모)에 대해 위 검토자가 검토를 완료하였음을 확인합니다."
    )
    page.insert_text((MARGIN, y), statement, fontsize=BODY_SIZE, color=HEADER_COLOR)
    y += 16
    page.insert_text(
        (MARGIN, y),
        f"보고서 ID: review_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        fontsize=BODY_SIZE - 1,
        color=MUTED_COLOR,
    )


def _apply_page_footers(doc: Any, inputs: ReviewReportInput, settings: Any) -> None:
    """Add a small footer to every page (page N/total + project + reviewer + company)."""

    import fitz

    total = doc.page_count
    company = (getattr(settings, "company_name", "") if settings else "") or "센엔지니어링 그룹"
    reviewer = (getattr(settings, "reviewer_name", "") if settings else "") or ""
    note = (getattr(settings, "footer_note", "") if settings else "") or ""
    project = inputs.project_label or ""
    for page_idx in range(total):
        page = doc[page_idx]
        footer_y = A4_HEIGHT - 18
        # Left: company / project
        left_text = company
        if project:
            left_text = f"{company}  ·  {project}"
        if note:
            left_text = f"{left_text}  ·  {note}"
        page.insert_text(
            (MARGIN, footer_y),
            left_text,
            fontsize=8,
            color=MUTED_COLOR,
        )
        # Right: reviewer · page N/total
        right_text = f"page {page_idx + 1} / {total}"
        if reviewer:
            right_text = f"{reviewer}  ·  {right_text}"
        page.insert_text(
            (A4_WIDTH - MARGIN - 160, footer_y),
            right_text,
            fontsize=8,
            color=MUTED_COLOR,
        )


# --- Field accessors (tolerate dict or dataclass records) ------------------


def rec_pair_id(record: Any) -> str:
    return str(getattr(record, "pair_id", "") or (record.get("pair_id") if isinstance(record, dict) else "") or "")


def rec_zone_id(record: Any) -> str:
    return str(getattr(record, "zone_id", "") or (record.get("zone_id") if isinstance(record, dict) else "") or "")


def rec_status(record: Any) -> str:
    return normalize_review_status(
        getattr(record, "status", "")
        or (record.get("status") if isinstance(record, dict) else "")
        or ""
    )


def rec_note(record: Any) -> str:
    return str(getattr(record, "note", "") or (record.get("note") if isinstance(record, dict) else "") or "")


# --- Misc helpers ----------------------------------------------------------


def _count_review_status(records: dict[str, Any]) -> dict[str, int]:
    counts = {"confirmed": 0, "hold": 0, "false_positive": 0, "needs_review": 0}
    for r in (records or {}).values():
        status = rec_status(r)
        counts[status] += 1
    counts["ignored"] = counts["hold"]
    return counts


def _resolve_image_path(value: str) -> Optional[Path]:
    """Coerce a user-configured logo/signature path into a Path or None."""

    if not value:
        return None
    try:
        return Path(value)
    except Exception:
        return None


def _resolve_confirmed_cloud_png(pair_id: str, confirmed_cloud_dir: Optional[Path]) -> Optional[Path]:
    if confirmed_cloud_dir is None:
        return None
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in pair_id)
    candidate = Path(confirmed_cloud_dir) / f"{safe}_confirmed.png"
    return candidate if candidate.exists() else None


def _drawing_label_for_pair(pair_id: str, drawing_rows: list[dict]) -> str:
    for row in drawing_rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("pair_id") or "") == pair_id:
            label = str(row.get("display_label") or row.get("drawing_number") or "")
            if label:
                return label
    return pair_id or "-"


def _status_ko(status: str) -> str:
    return {
        "confirmed": "확인",
        "hold": "보류",
        "ignored": "보류",
        "false_positive": "오탐",
        "needs_review": "미검토",
    }.get(status, status or "-")


def _format_ts(ts: str) -> str:
    if not ts:
        return "-"
    return ts.replace("T", " ")[:19]


def _short_path(p: str) -> str:
    if not p:
        return "-"
    text = str(p)
    cleaned = text.strip().rstrip("\\/")
    if not cleaned:
        return "-"
    normalized = cleaned.replace("\\", "/")
    if normalized.startswith("<redacted>/") or normalized.startswith("/redacted/"):
        return normalized.rsplit("/", 1)[-1] or "-"
    try:
        if "\\" in cleaned or (len(cleaned) >= 2 and cleaned[1] == ":"):
            name = PureWindowsPath(cleaned).name
        else:
            name = PurePosixPath(normalized).name
    except Exception:
        name = ""
    if name:
        return name
    if len(cleaned) <= 80:
        return cleaned
    return f"...{cleaned[-78:]}"
