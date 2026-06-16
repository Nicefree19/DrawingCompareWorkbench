"""Pure bbox / pixel coordinate-transform helpers for the workbench.

Third satellite extraction of the ``drawing_compare_workbench`` god-object
(tech-debt audit MONO-4; follows ``workbench_overlay_model`` and
``workbench_summary_format``). These functions map change-zone / CAD / world
bboxes into rendered-image pixel space, pure transforms with no Qt or widget
state. ``drawing_compare_workbench`` re-imports each so the in-file call sites
keep working unchanged. ``union_bboxes`` is imported from its service source
(no circular import: services do not import the gui monolith).
"""

from __future__ import annotations

from typing import Optional

from src.services.comparison.zone_render_service import union_bboxes


def compute_pdf_page_pin_overlay(
    base_overlay: dict,
    page_size: dict,
    *,
    pin_width_px: float = 200.0,
    pin_height_px: float = 150.0,
) -> Optional[dict]:
    """Synthesize an overlay entry centered on a PDF page when bbox is unknown.

    Used by the PDF-PDF viewer path: when the change zone has no ``image_pixels``
    bbox we still want to mark the page so the user knows where to look. The pin
    bbox is sized as a small rectangle near the page center so the focus marker
    (drawn with crosshair + pin glyph) stays readable at any zoom level.

    Returns ``None`` when ``page_size`` is missing or non-positive — in that case
    the caller should fall back to the relative-only text status.
    """

    if not isinstance(base_overlay, dict) or not isinstance(page_size, dict):
        return None
    try:
        width = float(page_size.get("width") or 0.0)
        height = float(page_size.get("height") or 0.0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    pin_w = max(40.0, min(float(pin_width_px), width * 0.5))
    pin_h = max(30.0, min(float(pin_height_px), height * 0.5))
    cx = width / 2.0
    cy = height / 2.0
    pixel_box = {
        "x": cx - pin_w / 2.0,
        "y": cy - pin_h / 2.0,
        "width": pin_w,
        "height": pin_h,
    }
    world_bbox = {
        "min_x": pixel_box["x"],
        "min_y": pixel_box["y"],
        "max_x": pixel_box["x"] + pin_w,
        "max_y": pixel_box["y"] + pin_h,
    }
    enriched = dict(base_overlay)
    enriched["pin_only"] = True
    enriched["pdf_page_pin"] = True
    enriched["bbox"] = world_bbox
    enriched.setdefault("after_bbox_px", dict(pixel_box))
    enriched.setdefault("before_bbox_px", dict(pixel_box))
    return enriched


def _cad_bbox_to_pixel_rect(bbox: object, transform: object) -> Optional[dict[str, float]]:
    if not isinstance(transform, dict) or not transform:
        return None
    if isinstance(bbox, dict):
        if {"min_x", "min_y", "max_x", "max_y"}.issubset(bbox):
            coords = [bbox["min_x"], bbox["min_y"], bbox["max_x"], bbox["max_y"]]
        elif {"x", "y", "width", "height"}.issubset(bbox):
            return {
                "x": float(bbox["x"]),
                "y": float(bbox["y"]),
                "width": max(1.0, float(bbox["width"])),
                "height": max(1.0, float(bbox["height"])),
            }
        else:
            return None
    elif isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        coords = list(bbox[:4])
    else:
        return None
    try:
        min_x = float(transform.get("min_x", 0.0))
        min_y = float(transform.get("min_y", 0.0))
        scale_x = float(transform.get("scale_x", 1.0))
        scale_y = float(transform.get("scale_y", 1.0))
        height = float(transform.get("img_height", 0.0))
        width = float(transform.get("img_width", 0.0))
        x1 = (float(coords[0]) - min_x) * scale_x
        x2 = (float(coords[2]) - min_x) * scale_x
        if str(transform.get("coordinate_space") or "").lower() == "image_pixels":
            y1 = (float(coords[1]) - min_y) * scale_y
            y2 = (float(coords[3]) - min_y) * scale_y
        else:
            y1 = height - ((float(coords[3]) - min_y) * scale_y)
            y2 = height - ((float(coords[1]) - min_y) * scale_y)
        left = max(0.0, min(width, min(x1, x2)))
        right = max(0.0, min(width, max(x1, x2)))
        top = max(0.0, min(height, min(y1, y2)))
        bottom = max(0.0, min(height, max(y1, y2)))
        return {
            "x": round(left, 2),
            "y": round(top, 2),
            "width": max(1.0, round(right - left, 2)),
            "height": max(1.0, round(bottom - top, 2)),
        }
    except Exception:
        return None


def _world_bbox_to_pixel_rect(world_bbox: object, transform: object) -> Optional[dict[str, float]]:
    box = union_bboxes(world_bbox)
    if not box or not isinstance(transform, dict):
        return None
    try:
        img_w = float(transform.get("img_width") or transform.get("width") or 0.0)
        img_h = float(transform.get("img_height") or transform.get("height") or 0.0)
        min_x = float(transform.get("min_x", 0.0))
        max_x = float(transform.get("max_x", 0.0))
        min_y = float(transform.get("min_y", 0.0))
        max_y = float(transform.get("max_y", 0.0))
    except (TypeError, ValueError):
        return None
    world_w = max_x - min_x
    world_h = max_y - min_y
    if img_w <= 0 or img_h <= 0 or world_w == 0 or world_h == 0:
        return None
    wx0, wy0, wx1, wy1 = box
    px0 = (wx0 - min_x) / world_w * img_w
    px1 = (wx1 - min_x) / world_w * img_w
    py0 = (max_y - wy1) / world_h * img_h
    py1 = (max_y - wy0) / world_h * img_h
    left = max(0.0, min(img_w, min(px0, px1)))
    right = max(0.0, min(img_w, max(px0, px1)))
    top = max(0.0, min(img_h, min(py0, py1)))
    bottom = max(0.0, min(img_h, max(py0, py1)))
    if right <= left or bottom <= top:
        return None
    return {
        "x": round(left, 2),
        "y": round(top, 2),
        "width": max(1.0, round(right - left, 2)),
        "height": max(1.0, round(bottom - top, 2)),
    }


def _lightweight_tile_zoom_from_transform(transform: object, units_per_pixel: float) -> float:
    if not isinstance(transform, dict):
        return 1.0
    try:
        img_w = float(transform.get("img_width") or transform.get("width") or 0.0)
        img_h = float(transform.get("img_height") or transform.get("height") or 0.0)
        world_w = float(transform.get("max_x", 0.0)) - float(transform.get("min_x", 0.0))
        world_h = float(transform.get("max_y", 0.0)) - float(transform.get("min_y", 0.0))
        upp = max(0.0001, float(units_per_pixel or 1.0))
    except (TypeError, ValueError):
        return 1.0
    scales = []
    if img_w > 0 and world_w != 0:
        scales.append(abs(img_w / world_w))
    if img_h > 0 and world_h != 0:
        scales.append(abs(img_h / world_h))
    if not scales:
        return 1.0
    image_px_per_screen_px = upp * (sum(scales) / len(scales))
    return max(0.0001, 1.0 / max(0.0001, image_px_per_screen_px))
