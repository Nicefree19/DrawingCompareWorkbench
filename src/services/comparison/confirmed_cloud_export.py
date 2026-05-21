# -*- coding: utf-8 -*-
"""Export cloud-marks for *only* the zones the reviewer has confirmed.

The default change-zones pipeline emits cloud markers for every detected
change. After a reviewer triages the list (확인 / 보류 / 오탐 / 미검토) the operator
typically wants a clean output that highlights only the changes they actually
agreed are real. This module produces that artefact:

- **PDF pair**: rasterise a copy of the *after* page PNG (already rendered by
  ``viewer_package.export_viewer_package``) and draw a red cloud-style outline
  + label around each confirmed change zone. The output is a single PNG so it
  ships standalone and prints/forwards easily.
- **CAD pair (DXF/DWG)**: a thin wrapper that filters
  ``change_zones._build_cloud_regions_by_pair`` to confirmed zones and
  delegates to the existing DXF cloud marker writer.

Public entry point:
- :func:`export_confirmed_cloud_marks` — the only function the GUI needs.
- :func:`export_selected_cloud_marks` — pipeline helper for a separate
  structural auto-export folder; it never mutates confirmed review state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

CONFIRMED_STATUS = "confirmed"
# Phase P (RV-20260508-014) — AIA 표준 cyan (ACI 4 = #00FFFF) 으로 변경.
# 기존 vibrant red 는 ``DEFAULT_CLOUD_COLOR_LEGACY`` 로 보존 (호출자가
# 명시적으로 요청 시 사용). 사용자 친숙성: AutoCAD/Revit 표준 표기와
# 색상 일치.
DEFAULT_CLOUD_COLOR = (0, 200, 220, 255)        # AIA cyan (saturation 살짝 낮춤)
DEFAULT_LABEL_COLOR = (255, 255, 255, 255)
DEFAULT_LABEL_BG = (0, 200, 220, 230)
DEFAULT_CLOUD_COLOR_LEGACY = (220, 38, 38, 255)  # vibrant red (Phase O)
DEFAULT_LABEL_BG_LEGACY = (220, 38, 38, 230)


@dataclass
class ConfirmedCloudExportResult:
    """Outcome of one pair's confirmed-cloud export."""

    pair_id: str
    output_path: str  # final image / DXF path; empty if nothing exported
    confirmed_zone_count: int
    skipped_reason: str = ""  # populated when nothing to export
    is_pdf: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "output_path": self.output_path,
            "confirmed_zone_count": self.confirmed_zone_count,
            "skipped_reason": self.skipped_reason,
            "is_pdf": self.is_pdf,
        }


def export_confirmed_cloud_marks(
    *,
    pair_id: str,
    after_image_path: Optional[str],
    overlays: list[dict],
    review_records: dict[str, Any],
    output_dir: Path,
    is_pdf_pair: bool,
    label_prefix: str = "확인",
    image_dpi: float = 0.0,
) -> ConfirmedCloudExportResult:
    """Render confirmed-only cloud marks on the *after* PNG (PDF pair).

    For non-PDF pairs ``after_image_path`` may still be a CAD-rendered PNG —
    the pipeline writes one for both pair kinds — so the PNG output works as a
    universal "review summary" artefact regardless of source format. The
    caller can additionally request a DXF-only run via the existing
    ``change_zones`` cloud writer if it wants a true CAD overlay.

    Args:
        image_dpi: G2.7-COORDFIX — DPI of ``after_image_path``. When set
            to a positive value AND the overlay carries
            ``bbox_coordinate_space=='image_pixels'`` + ``pdf_dpi``, the
            bbox is scaled by ``image_dpi/pdf_dpi`` so the cloud lands on
            the correct location of the rendered PNG. Without this scale
            the cloud appears at ``pdf_dpi/image_dpi`` of the actual
            change position (e.g. half-position when image_dpi=400 and
            pdf_dpi=200) — which the user reported as "구름이 엉뚱한
            데에 그려진다". Defaults to 0 (no scaling, legacy behaviour).
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    confirmed_zone_ids = _confirmed_zone_ids_for_pair(pair_id, review_records)
    if not confirmed_zone_ids:
        return ConfirmedCloudExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=0,
            skipped_reason="확인(confirmed) 상태 변경구역이 없습니다.",
            is_pdf=is_pdf_pair,
        )

    confirmed_overlays = [
        overlay
        for overlay in (overlays or [])
        if isinstance(overlay, dict) and str(overlay.get("zone_id") or "") in confirmed_zone_ids
    ]
    if not confirmed_overlays:
        return ConfirmedCloudExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=len(confirmed_zone_ids),
            skipped_reason="확인된 변경구역에 일치하는 overlay 데이터를 찾지 못했습니다.",
            is_pdf=is_pdf_pair,
        )

    if not after_image_path or not Path(after_image_path).exists():
        return ConfirmedCloudExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=len(confirmed_zone_ids),
            skipped_reason="변경 후 PNG가 아직 렌더되지 않았습니다. 비교 실행 후 도면을 한 번 선택하세요.",
            is_pdf=is_pdf_pair,
        )

    output_path = output_dir / f"{_safe_pair_name(pair_id)}_confirmed.png"
    saved = _draw_confirmed_clouds_on_png(
        source_png=Path(after_image_path),
        overlays=confirmed_overlays,
        output_path=output_path,
        label_prefix=label_prefix,
        image_dpi=image_dpi,
    )
    return ConfirmedCloudExportResult(
        pair_id=pair_id,
        output_path=str(saved),
        confirmed_zone_count=len(confirmed_overlays),
        skipped_reason="",
        is_pdf=is_pdf_pair,
    )


def export_selected_cloud_marks(
    *,
    pair_id: str,
    after_image_path: Optional[str],
    overlays: list[dict],
    zone_ids: Iterable[str],
    output_dir: Path,
    is_pdf_pair: bool,
    label_prefix: str = "구조",
    output_suffix: str = "auto_structural",
    image_dpi: float = 0.0,
) -> ConfirmedCloudExportResult:
    """Render cloud marks for an explicit zone-id set without review state."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_zone_ids = {
        str(zone_id or "").strip()
        for zone_id in zone_ids
        if str(zone_id or "").strip()
    }
    if not selected_zone_ids:
        return ConfirmedCloudExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=0,
            skipped_reason="선택된 변경구역이 없습니다.",
            is_pdf=is_pdf_pair,
        )

    selected_overlays = [
        overlay
        for overlay in (overlays or [])
        if isinstance(overlay, dict) and str(overlay.get("zone_id") or "") in selected_zone_ids
    ]
    if not selected_overlays:
        return ConfirmedCloudExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=len(selected_zone_ids),
            skipped_reason="선택된 변경구역에 일치하는 overlay 데이터를 찾지 못했습니다.",
            is_pdf=is_pdf_pair,
        )
    if not after_image_path or not Path(after_image_path).exists():
        return ConfirmedCloudExportResult(
            pair_id=pair_id,
            output_path="",
            confirmed_zone_count=len(selected_zone_ids),
            skipped_reason="변경 후 PNG가 아직 렌더되지 않았습니다.",
            is_pdf=is_pdf_pair,
        )

    suffix = _safe_pair_name(output_suffix or "selected")
    output_path = output_dir / f"{_safe_pair_name(pair_id)}_{suffix}.png"
    saved = _draw_confirmed_clouds_on_png(
        source_png=Path(after_image_path),
        overlays=selected_overlays,
        output_path=output_path,
        label_prefix=label_prefix,
        image_dpi=image_dpi,
    )
    return ConfirmedCloudExportResult(
        pair_id=pair_id,
        output_path=str(saved),
        confirmed_zone_count=len(selected_overlays),
        skipped_reason="",
        is_pdf=is_pdf_pair,
    )


# Phase I review fix #5: confirmed-zone selector + safe pair name
# delegated to ``review_helpers`` so PDF DXF / PNG cloud share one
# implementation. Keeping the same names so external callers
# (workbench, tests) stay binary-compatible.
from .review_helpers import (
    confirmed_zone_ids_for_pair as _confirmed_zone_ids_for_pair,
    safe_pair_name as _safe_pair_name,
    resolve_pixel_bbox as _shared_resolve_pixel_bbox,
)


def _draw_confirmed_clouds_on_png(
    *,
    source_png: Path,
    overlays: list[dict],
    output_path: Path,
    label_prefix: str,
    image_dpi: float = 0.0,
) -> Path:
    """Open ``source_png``, paint cloud-style outlines + labels, save copy."""

    from PIL import Image, ImageDraw, ImageFont

    base = Image.open(source_png).convert("RGBA")
    overlay_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay_layer)

    font = _load_label_font()
    counter = 1
    for overlay in overlays:
        bbox = _resolve_pixel_bbox(overlay, image_dpi=image_dpi)
        if not bbox:
            continue
        x0, y0, x1, y1 = bbox
        # Clamp to image bounds so labels never paint past the edge
        x0 = max(0.0, min(float(x0), float(base.width - 1)))
        x1 = max(0.0, min(float(x1), float(base.width - 1)))
        y0 = max(0.0, min(float(y0), float(base.height - 1)))
        y1 = max(0.0, min(float(y1), float(base.height - 1)))
        if x1 - x0 < 4 or y1 - y0 < 4:
            # Tiny boxes — pad outward so reviewer can spot the marker
            cx = (x0 + x1) / 2.0
            cy = (y0 + y1) / 2.0
            half = 16.0
            x0, y0 = max(0.0, cx - half), max(0.0, cy - half)
            x1, y1 = min(float(base.width - 1), cx + half), min(float(base.height - 1), cy + half)

        _draw_cloud_rectangle(draw, (x0, y0, x1, y1), color=DEFAULT_CLOUD_COLOR)

        zone_id = str(overlay.get("zone_id") or "")
        label_text = f"{label_prefix} {counter}" if not zone_id else f"{label_prefix} {counter} · {zone_id}"
        _draw_label(draw, (x0, y0), label_text, font=font)
        counter += 1

    composed = Image.alpha_composite(base, overlay_layer).convert("RGB")
    composed.save(output_path, format="PNG", optimize=True)
    return output_path


def _resolve_pixel_bbox(
    overlay: dict, *, image_dpi: float = 0.0,
) -> Optional[tuple[float, float, float, float]]:
    """Extract a pixel-space bounding box ``(x0, y0, x1, y1)`` from an overlay.

    2nd-review fix (P1-2): delegates the parse step to
    ``review_helpers.resolve_pixel_bbox`` so PNG cloud export and PDF
    DXF export use the SAME bbox parser. The DPI-scaling is the
    PNG-only post-step (PDF export does its own px → mm conversion
    via _bbox_pdf_pixels_to_mm), so we keep that part here.

    G2.7-COORDFIX — When the overlay carries ``bbox_coordinate_space ==
    "image_pixels"`` AND ``pdf_dpi``, AND the caller provides a positive
    ``image_dpi`` for the rendered PNG, the bbox is scaled by
    ``image_dpi/pdf_dpi`` so the cloud lands on the actual change in
    the PNG.
    """

    raw_box = _shared_resolve_pixel_bbox(overlay)
    if raw_box is None:
        return None

    # G2.7-COORDFIX — apply DPI scale when overlay metadata + image_dpi
    # both indicate a PDF pair with mismatched pdf_dpi vs preview_dpi.
    space = str(overlay.get("bbox_coordinate_space") or "")
    if space == "image_pixels" and image_dpi > 0:
        try:
            bbox_dpi = float(overlay.get("pdf_dpi") or 0.0)
        except (TypeError, ValueError):
            bbox_dpi = 0.0
        if bbox_dpi > 0 and bbox_dpi != image_dpi:
            scale = image_dpi / bbox_dpi
            x0, y0, x1, y1 = raw_box
            raw_box = (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
    return raw_box


def _draw_cloud_rectangle(
    draw: "ImageDraw.ImageDraw",  # noqa: F821
    rect: tuple[float, float, float, float],
    *,
    color: tuple[int, int, int, int],
    arc_radius: int = 12,
    line_width: int = 4,
) -> None:
    """Approximate a hand-drawn revision cloud around ``rect``.

    The shape is a sequence of small arcs along each edge giving the classic
    "wavy" look. Falls back to a simple thick rectangle when the rect is too
    small for a meaningful cloud.
    """

    x0, y0, x1, y1 = rect
    w = max(0.0, x1 - x0)
    h = max(0.0, y1 - y0)
    if w < 24 or h < 24:
        draw.rectangle([x0, y0, x1, y1], outline=color, width=line_width)
        return

    diameter = max(8, min(int(arc_radius), int(min(w, h) // 2)))
    radius = diameter // 2

    # Top edge: arcs facing up (180°→360°)
    x = int(x0)
    while x < int(x1):
        bbox = [x, int(y0) - radius, x + diameter, int(y0) + radius]
        draw.arc(bbox, 180, 360, fill=color, width=line_width)
        x += diameter
    # Bottom edge: arcs facing down (0°→180°)
    x = int(x0)
    while x < int(x1):
        bbox = [x, int(y1) - radius, x + diameter, int(y1) + radius]
        draw.arc(bbox, 0, 180, fill=color, width=line_width)
        x += diameter
    # Left edge: arcs facing left (90°→270°)
    y = int(y0)
    while y < int(y1):
        bbox = [int(x0) - radius, y, int(x0) + radius, y + diameter]
        draw.arc(bbox, 90, 270, fill=color, width=line_width)
        y += diameter
    # Right edge: arcs facing right (270°→90° wrap)
    y = int(y0)
    while y < int(y1):
        bbox = [int(x1) - radius, y, int(x1) + radius, y + diameter]
        draw.arc(bbox, 270, 90, fill=color, width=line_width)
        y += diameter


def _draw_label(
    draw: "ImageDraw.ImageDraw",  # noqa: F821
    anchor: tuple[float, float],
    text: str,
    *,
    font: Optional["ImageFont.ImageFont"],  # noqa: F821
    bg: tuple[int, int, int, int] = DEFAULT_LABEL_BG,
    fg: tuple[int, int, int, int] = DEFAULT_LABEL_COLOR,
) -> None:
    """Draw a small filled badge with the zone label above the cloud."""

    x, y = anchor
    pad_x, pad_y = 6, 3
    if font is None:
        text_width = len(text) * 7
        text_height = 14
    else:
        try:
            l, t, r, b = draw.textbbox((0, 0), text, font=font)
            text_width = r - l
            text_height = b - t
        except Exception:
            text_width = len(text) * 7
            text_height = 14
    bg_x0 = max(0.0, x)
    bg_y0 = max(0.0, y - text_height - 2 * pad_y - 2)
    bg_x1 = bg_x0 + text_width + 2 * pad_x
    bg_y1 = bg_y0 + text_height + 2 * pad_y
    draw.rectangle([bg_x0, bg_y0, bg_x1, bg_y1], fill=bg)
    draw.text(
        (bg_x0 + pad_x, bg_y0 + pad_y),
        text,
        fill=fg,
        font=font,
    )


def _load_label_font():
    """Best-effort font load — falls back to default bitmap on any failure."""

    from PIL import ImageFont

    candidates = [
        ("malgun.ttf", 14),  # Korean-friendly Windows default
        ("arial.ttf", 14),
        ("DejaVuSans.ttf", 14),
    ]
    for name, size in candidates:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None
