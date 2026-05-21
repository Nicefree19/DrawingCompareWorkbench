# -*- coding: utf-8 -*-
"""Shared helpers for cloud-export modules.

Phase I review feedback (Section 2 issue #5): the
``_confirmed_zone_ids_for_pair`` and pixel-bbox parsing functions
were duplicated across ``confirmed_cloud_export`` and
``pdf_cloud_dxf_export``. The two copies are functionally identical
today but a future patch that touches one without the other would
silently produce mismatched PNG vs DXF outputs.

This module centralises both, with shared unit-test coverage so the
two consumers stay in lock-step.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence


CONFIRMED_STATUS = "confirmed"


def confirmed_zone_ids_for_pair(
    pair_id: str, review_records: Optional[dict[str, Any]],
) -> set[str]:
    """Return every zone_id that's marked ``confirmed`` for ``pair_id``.

    Accepts both dataclass-style records (``record.pair_id``) and
    dict-style records (``record["pair_id"]``). Skips records that
    are missing any of pair_id / status / zone_id.
    """

    confirmed: set[str] = set()
    for record in (review_records or {}).values():
        record_pair = getattr(record, "pair_id", None) or (
            record.get("pair_id") if isinstance(record, dict) else ""
        )
        record_status = getattr(record, "status", None) or (
            record.get("status") if isinstance(record, dict) else ""
        )
        record_zone = getattr(record, "zone_id", None) or (
            record.get("zone_id") if isinstance(record, dict) else ""
        )
        if (
            str(record_pair) == str(pair_id)
            and str(record_status) == CONFIRMED_STATUS
            and record_zone
        ):
            confirmed.add(str(record_zone))
    return confirmed


def safe_pair_name(pair_id: Any) -> str:
    """Sanitise a pair_id to a filesystem-safe filename component."""

    cleaned = "".join(
        ch if ch.isalnum() or ch in {"_", "-"} else "_"
        for ch in str(pair_id or "")
    )
    return cleaned or "pair"


def resolve_pixel_bbox(
    overlay: dict,
) -> Optional[tuple[float, float, float, float]]:
    """Extract a pixel-space bounding box ``(x0, y0, x1, y1)`` from
    an overlay dict.

    Tries the most specific source first (``after_bbox_px``), then
    generic pixel boxes, then the normalised ``bbox`` field as a last
    resort. Returns ``None`` for any unparseable input — caller should
    skip the zone rather than crash.

    Phase I review fix: also normalises bbox order so reversed
    bboxes (x0 > x1 or y0 > y1) come out in canonical (min, min,
    max, max) form. Without this, reversed bboxes silently produce
    inverted DXF rectangles that float outside the page.
    """

    bbox = (
        overlay.get("after_bbox_px")
        or overlay.get("bbox_px")
        or overlay.get("image_pixels")
    )
    raw_box: Optional[tuple[float, float, float, float]] = None
    if isinstance(bbox, dict):
        try:
            x = float(bbox.get("x", 0.0))
            y = float(bbox.get("y", 0.0))
            w = float(bbox.get("width", 0.0))
            h = float(bbox.get("height", 0.0))
        except (TypeError, ValueError):
            return None
        raw_box = (x, y, x + max(1.0, w), y + max(1.0, h))
    elif isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            raw_box = (float(bbox[0]), float(bbox[1]),
                       float(bbox[2]), float(bbox[3]))
        except (TypeError, ValueError):
            return None
    if raw_box is None:
        # Final fallback — generic ``bbox`` field
        bbox = overlay.get("bbox")
        if isinstance(bbox, dict):
            try:
                raw_box = (
                    float(bbox.get("min_x", 0.0)),
                    float(bbox.get("min_y", 0.0)),
                    float(bbox.get("max_x", 0.0)),
                    float(bbox.get("max_y", 0.0)),
                )
            except (TypeError, ValueError):
                return None
        elif isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            try:
                raw_box = (float(bbox[0]), float(bbox[1]),
                           float(bbox[2]), float(bbox[3]))
            except (TypeError, ValueError):
                return None
    if raw_box is None:
        return None

    # Normalise ordering — reject degenerate (zero-area) bboxes
    x0, y0, x1, y1 = raw_box
    x_min, x_max = min(x0, x1), max(x0, x1)
    y_min, y_max = min(y0, y1), max(y0, y1)
    if x_max - x_min < 0.5 or y_max - y_min < 0.5:
        return None
    return (x_min, y_min, x_max, y_max)


__all__ = [
    "CONFIRMED_STATUS",
    "confirmed_zone_ids_for_pair",
    "safe_pair_name",
    "resolve_pixel_bbox",
]
