# -*- coding: utf-8 -*-
"""ADR-003 H5d — build viewer-ready overlays for a DWG diff on a reference PDF.

This is the slice that finally *wires* the PDF-first hybrid pipeline into a
surface the user can see. It takes the change-zone overlays of a DWG-vs-DWG
comparison (whose ``bbox`` is in ``cad_wcs_mm``) and a user-chosen reference
PDF page, and re-expresses each overlay in the coordinate shape the existing
lightweight viewport already consumes for PDF backgrounds:

    {"bbox": {min_x, min_y, max_x, max_y},   # image_pixels_tl
     "bbox_coordinate_space": "image_pixels_tl",
     "pdf_dpi": <dpi>, ...}

The viewport's ``push_change_overlays_from_v1`` →
``convert_bbox_to_world_space`` then converts those image pixels to PDF
points (Y-flipped), landing the cloud markers on the rendered page. Because
we emit the *exact* shape that path already handles, **no viewport change is
required** — only this pure builder plus a thin GUI adapter.

Coordinate chain (verified):

    cad_wcs_mm bbox
      → align_cad_to_pdf(frame, pixel_size)         (H2)
      → alignment.map_cad_bbox(...) → image_pixels_tl (H1; Y flipped)
      → viewport convert_bbox_to_world_space(dpi)    → page points (Y-up)

With ``dpi == 72`` and ``pixel_size == (page_w_pt, page_h_pt)`` the image
pixels equal page points numerically, so the viewport conversion reduces to
the Y-flip alone — the simplest consistent mapping.

Pure Python — depends only on the H1/H2/H5b helpers + ``transform``. No Qt /
ezdxf / I/O, so it is fully unit-testable headless.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .cad_pdf_alignment import align_cad_to_pdf
from .cad_pdf_pairing import pair_dwg_to_pdf
from .page_descriptor import PerPageDescriptor
from .page_matcher import PageMatchOptions
from .transform import (
    Bbox,
    COORD_IMAGE_PIXELS_TL,
    PixelSize,
)

#: Points-per-inch identity DPI. With this DPI the alignment pixel canvas is
#: sized in PDF points, so mapped image pixels equal page points (top-left)
#: and the viewport only has to flip Y. Any positive DPI works as long as the
#: pixel_size is scaled to match; 72 keeps the numbers interpretable.
DEFAULT_REFERENCE_DPI: float = 72.0

#: Keys overwritten on each overlay copy. Everything else (zone_id,
#: change_type, severity, label, priority…) is preserved so the viewport's
#: routing + styling keep working unchanged.
_OVERWRITTEN_KEYS = ("bbox", "old_bbox", "bbox_coordinate_space", "pdf_dpi")

#: Relative aspect tolerance for deciding the DWG frame and PDF page share an
#: orientation (matches DEFAULT_ASPECT_TOLERANCE in cad_pdf_alignment).
_ROTATION_ASPECT_TOLERANCE: float = 0.02


def _bbox_center(bbox: Bbox) -> Tuple[float, float]:
    x0, y0, x1, y1 = bbox
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _rotate_point_quarter(x: float, y: float, cx: float, cy: float, k: int) -> Tuple[float, float]:
    """Rotate (x, y) about (cx, cy) by k*90° CCW (k in 0..3)."""

    dx, dy = x - cx, y - cy
    k %= 4
    if k == 0:
        rx, ry = dx, dy
    elif k == 1:           # 90° CCW
        rx, ry = -dy, dx
    elif k == 2:           # 180°
        rx, ry = -dx, -dy
    else:                  # 270° CCW == 90° CW
        rx, ry = dy, -dx
    return cx + rx, cy + ry


def _rotate_bbox_quarter(bbox: object, center: Tuple[float, float], k: int) -> Optional[Bbox]:
    """Rotate an axis-aligned bbox by k*90° about ``center``.

    90° multiples map an axis-aligned rectangle to another axis-aligned
    rectangle, so the result is exact (no skew). Returns ``None`` for an
    unparseable bbox.
    """

    from .transform import normalise_bbox

    coords = normalise_bbox(bbox)
    if coords is None:
        return None
    x0, y0, x1, y1 = coords
    cx, cy = center
    corners = [
        _rotate_point_quarter(x0, y0, cx, cy, k),
        _rotate_point_quarter(x1, y0, cx, cy, k),
        _rotate_point_quarter(x1, y1, cx, cy, k),
        _rotate_point_quarter(x0, y1, cx, cy, k),
    ]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def recommended_quarter_turns(
    dwg_frame_bbox: object,
    page_points_wh: Optional[Tuple[float, float]],
    *,
    tolerance: float = _ROTATION_ASPECT_TOLERANCE,
) -> int:
    """Suggest the 90° rotation that makes the DWG frame match the PDF page.

    Returns ``0`` when the frame and page already share an orientation
    (aspects match within tolerance) or when no single 90° turn reconciles
    them. Returns ``1`` when the aspects are *inverse* — the common case of a
    landscape DWG plotted to a portrait PDF page (or vice versa), as seen on
    the real 01.3PG1 sheet. The CW vs CCW choice (1 vs 3) is left to the
    caller/GUI toggle because it cannot be inferred from aspect alone.
    """

    from .transform import normalise_bbox

    frame = normalise_bbox(dwg_frame_bbox)
    if frame is None or not page_points_wh:
        return 0
    fw = frame[2] - frame[0]
    fh = frame[3] - frame[1]
    try:
        pw, ph = float(page_points_wh[0]), float(page_points_wh[1])
    except (TypeError, ValueError):
        return 0
    if fw <= 0 or fh <= 0 or pw <= 0 or ph <= 0:
        return 0
    cad_aspect = fw / fh
    pdf_aspect = pw / ph
    if abs(cad_aspect - pdf_aspect) / pdf_aspect <= tolerance:
        return 0
    if abs(cad_aspect - 1.0 / pdf_aspect) / (1.0 / pdf_aspect) <= tolerance:
        return 1
    return 0


def _bbox_to_dict(bbox: Optional[Bbox]) -> Optional[dict]:
    """Express a 4-tuple bbox as the production overlay dict shape."""

    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    return {
        "min_x": float(min(x0, x1)),
        "min_y": float(min(y0, y1)),
        "max_x": float(max(x0, x1)),
        "max_y": float(max(y0, y1)),
    }


def build_reference_pdf_overlays(
    overlays: Sequence[dict],
    *,
    dwg_frame_bbox: object,
    page_points_wh: Optional[Tuple[float, float]],
    dpi: float = DEFAULT_REFERENCE_DPI,
    padding_px: int = 0,
    page_rotation_quarter_turns: int = 0,
) -> Tuple[List[dict], str]:
    """Re-map DWG diff overlays onto a reference PDF page (ADR-003 H5d/H5e).

    Args:
        overlays: the v1 change-zone overlays of a DWG-vs-DWG comparison.
            Each is expected to carry a ``cad_wcs_mm`` ``"bbox"`` (dict or
            4-list) and optional ``"old_bbox"``; non-CAD or unparseable
            entries are skipped.
        dwg_frame_bbox: the DWG drawing-frame extents in cad_wcs_mm. Use the
            drawing's world extents (e.g. the active pair's after_transform
            world bbox) — NOT the change-zone union, which mispositions
            overlays when changes cluster (ADR-003 §H5d risk).
        page_points_wh: ``(width_pt, height_pt)`` of the reference PDF page
            in points. ``None`` (or non-positive) → returns ``([], "")``.
        dpi: image-pixel DPI used to build the alignment canvas. The viewport
            must convert with the same DPI; the GUI adapter passes it through
            via the overlay's ``pdf_dpi`` field.
        padding_px: symmetric margin matching the PDF render (usually 0).
        page_rotation_quarter_turns: ADR-003 H5e — rotate the DWG frame +
            every bbox by ``k*90°`` CCW about the frame centre before fitting.
            Use this when the DWG was plotted to the PDF at a different
            orientation (e.g. a landscape √2 drawing plotted to a portrait
            A3 page — the real 01.3PG1 case). ``recommended_quarter_turns``
            suggests ``k``; the GUI toggle flips ``1↔3`` for CW/CCW. ``0``
            (default) preserves the H5d behaviour.

    Returns:
        ``(converted_overlays, quality)`` where ``quality`` is the alignment
        grade (``exact`` / ``estimated`` / ``relative_only`` / ``""``). On a
        ``relative_only`` (degenerate frame) or missing page size, the
        overlay list is empty so the caller falls back to relative pins and
        surfaces the reason (S1 honesty).
    """

    if not page_points_wh:
        return [], ""
    pw_pt, ph_pt = page_points_wh
    try:
        pw_pt_f = float(pw_pt)
        ph_pt_f = float(ph_pt)
    except (TypeError, ValueError):
        return [], ""
    if pw_pt_f <= 0 or ph_pt_f <= 0 or dpi <= 0:
        return [], ""

    scale = dpi / 72.0
    pixel_size: PixelSize = (round(pw_pt_f * scale), round(ph_pt_f * scale))

    # ADR-003 H5e — rotate the frame (and later each bbox) about the frame
    # centre so an orientation-mismatched plot still aligns exactly.
    from .transform import normalise_bbox

    k = int(page_rotation_quarter_turns) % 4
    frame_norm = normalise_bbox(dwg_frame_bbox)
    rotate_center: Optional[Tuple[float, float]] = None
    align_frame: object = dwg_frame_bbox
    if k and frame_norm is not None:
        rotate_center = _bbox_center(frame_norm)
        align_frame = _rotate_bbox_quarter(frame_norm, rotate_center, k) or dwg_frame_bbox

    alignment = align_cad_to_pdf(
        align_frame, pixel_size, padding_px=padding_px
    )
    if not alignment.is_usable:
        return [], alignment.quality

    converted: List[dict] = []
    for overlay in overlays:
        if not isinstance(overlay, dict):
            continue
        src_bbox: object = overlay.get("bbox")
        old_src: object = overlay.get("old_bbox")
        if k and rotate_center is not None:
            src_bbox = _rotate_bbox_quarter(src_bbox, rotate_center, k)
            old_src = (
                _rotate_bbox_quarter(old_src, rotate_center, k)
                if old_src is not None else None
            )
        mapped = alignment.map_cad_bbox(src_bbox)
        if mapped is None:
            # Non-CAD / unparseable / off-frame — skip rather than draw wrong.
            continue
        mapped_old = alignment.map_cad_bbox(old_src) if old_src is not None else None

        entry = dict(overlay)
        entry["bbox"] = _bbox_to_dict(mapped)
        entry["old_bbox"] = _bbox_to_dict(mapped_old) or entry["bbox"]
        entry["bbox_coordinate_space"] = COORD_IMAGE_PIXELS_TL
        entry["pdf_dpi"] = float(dpi)
        # Drop stale render-PNG pixel boxes so no downstream consumer mistakes
        # them for the reference-PDF position.
        entry.pop("after_bbox_px", None)
        entry.pop("before_bbox_px", None)
        converted.append(entry)

    return converted, alignment.quality


def filter_overlays_to_region(
    overlays: Sequence[dict],
    region_bbox: object,
    *,
    mode: str = "intersect",
) -> List[dict]:
    """Keep only overlays whose cad bbox falls in a selected region (H5f).

    Real working DWGs pack many sheets/details across one modelspace; a single
    reference PDF shows ONE region. Overlaying *every* change onto that PDF is
    meaningless (validated on the real 3PG1 drawing: ~55% of changes lay
    outside the matched 도곽). This filter scopes the overlays to a region the
    user selected in the viewer (its current visible world rect, or a
    rubber-band box) so only that sheet's changes are mapped.

    Args:
        overlays: change-zone overlays carrying a cad_wcs_mm ``"bbox"``.
        region_bbox: the selected region in cad_wcs_mm (dict or 4-seq). The
            same bbox is typically passed as ``dwg_frame_bbox`` to
            :func:`build_reference_pdf_overlays` so the region maps to the
            whole PDF page.
        mode: ``"intersect"`` (default — keep overlays touching the region) or
            ``"contain"`` (keep only fully-inside overlays).

    Returns:
        The filtered overlay list (order preserved). An unparseable/empty
        region returns the overlays unchanged (no scoping).
    """

    from .transform import normalise_bbox

    region = normalise_bbox(region_bbox)
    if region is None:
        return [ov for ov in overlays if isinstance(ov, dict)]
    rx0, ry0, rx1, ry1 = region
    out: List[dict] = []
    for ov in overlays:
        if not isinstance(ov, dict):
            continue
        bb = normalise_bbox(ov.get("bbox"))
        if bb is None:
            continue
        bx0, by0, bx1, by1 = bb
        if mode == "contain":
            keep = bx0 >= rx0 and by0 >= ry0 and bx1 <= rx1 and by1 <= ry1
        else:  # intersect
            keep = not (bx1 < rx0 or bx0 > rx1 or by1 < ry0 or by0 > ry1)
        if keep:
            out.append(ov)
    return out


def select_pdf_page_for_dwg(
    dwg_descriptor: PerPageDescriptor,
    pdf_descriptors: Sequence[PerPageDescriptor],
    *,
    options: Optional[PageMatchOptions] = None,
) -> Tuple[int, str]:
    """Pick the reference-PDF page that best matches a DWG drawing (H5b).

    Delegates to :func:`cad_pdf_pairing.pair_dwg_to_pdf` so a multi-sheet
    booklet PDF resolves to the page whose drawing number / frame matches the
    DWG. Single-page PDFs and inconclusive matches fall back to page 0.

    Returns:
        ``(page_index, status)``. ``status`` is the matcher status
        (``auto_confirmed`` / ``review_required``) or ``"fallback"`` when no
        match was found.
    """

    if not pdf_descriptors:
        return 0, "fallback"
    if len(pdf_descriptors) == 1:
        return pdf_descriptors[0].page_index, "single"

    result = pair_dwg_to_pdf([dwg_descriptor], pdf_descriptors, options=options)
    if result.pairs:
        best = result.pairs[0]
        return best.pdf_page_index, best.status
    return 0, "fallback"


__all__ = [
    "build_reference_pdf_overlays",
    "filter_overlays_to_region",
    "recommended_quarter_turns",
    "select_pdf_page_for_dwg",
    "DEFAULT_REFERENCE_DPI",
]
