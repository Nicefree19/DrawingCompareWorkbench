"""Viewer-pair classification + PDF bbox scaling helpers for the workbench.

Fifth satellite extraction of the ``drawing_compare_workbench`` god-object
(tech-debt audit MONO-4). ``_viewer_pair_is_pdf`` classifies a viewer pair as
PDF-vs-PDF (used 30+ times across the viewer paths); ``scale_pdf_bbox_to_render_pixels``
rescales an image-pixel bbox between the compare DPI and the rendered-background
DPI. Both are pure (``union_bboxes`` is imported from its service source; no
circular import). ``drawing_compare_workbench`` re-imports both so the in-file
call sites are unchanged.
"""

from __future__ import annotations

from typing import Optional

from src.services.comparison.zone_render_service import union_bboxes


def _viewer_pair_is_pdf(viewer_pair: dict) -> bool:
    if str(viewer_pair.get("coordinate_source") or "").lower() == "image_pixels":
        return True
    source_a = str(viewer_pair.get("source_a") or "").lower()
    source_b = str(viewer_pair.get("source_b") or "").lower()
    return source_a.endswith(".pdf") and source_b.endswith(".pdf")


def scale_pdf_bbox_to_render_pixels(
    bbox: object,
    overlay: dict,
    viewer_pair: dict,
) -> Optional[tuple[float, float, float, float]]:
    """Return a PDF crop bbox in the rendered background image pixel space."""

    box = union_bboxes(bbox)
    if not box:
        return None
    if not isinstance(overlay, dict) or not isinstance(viewer_pair, dict):
        return box
    if not _viewer_pair_is_pdf(viewer_pair):
        return box
    if str(overlay.get("bbox_coordinate_space") or "").lower() != "image_pixels":
        return box
    transform = viewer_pair.get("after_transform") or viewer_pair.get("before_transform") or {}
    if not isinstance(transform, dict):
        return box
    try:
        bbox_dpi = float(
            overlay.get("pdf_dpi")
            or viewer_pair.get("compare_pdf_dpi")
            or 0.0
        )
    except (TypeError, ValueError):
        bbox_dpi = 0.0
    try:
        image_dpi = float(
            transform.get("effective_dpi")
            or transform.get("dpi")
            or transform.get("pdf_dpi")
            or 0.0
        )
    except (TypeError, ValueError):
        image_dpi = 0.0
    if bbox_dpi <= 0 or image_dpi <= 0 or bbox_dpi == image_dpi:
        return box
    scale = image_dpi / bbox_dpi
    return (
        box[0] * scale,
        box[1] * scale,
        box[2] * scale,
        box[3] * scale,
    )
