# -*- coding: utf-8 -*-
"""RELIABILITY P0-3 — shared viewer frame selection (headless, testable).

Root cause of "한쪽만 보임 / 동떨어진 차이": the lightweight raster loader sets
each viewport's camera frame to that side's OWN render extents
(``_transform_world_bbox_v2(before_transform)`` vs ``after_transform``), so the
two sides show different world regions at different scales. Both drawings
already share a world origin (verified), so the fix is to give BOTH viewports
ONE shared camera frame = the union of the valid per-side render extents.

This module isolates the *selection* logic so it is unit-testable without Qt.
The GUI loader calls :func:`shared_world_bbox_from_transforms` and applies the
result via ``viewport.set_camera_to_world_bbox`` (the PNG placement stays
per-side; only the camera frame is unified — ADR-006 / verifier note).

Pure: depends only on ``viewer_package._v2_world_bbox_from_transform``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .viewer_package import _v2_world_bbox_from_transform

Bbox = Tuple[float, float, float, float]

_EMPTY: Bbox = (0.0, 0.0, 0.0, 0.0)


def _valid(b: Bbox) -> bool:
    return b != _EMPTY and b[2] > b[0] and b[3] > b[1]


def shared_world_bbox_from_transforms(
    before_transform: Optional[Dict[str, Any]],
    after_transform: Optional[Dict[str, Any]],
) -> Optional[Bbox]:
    """The single camera frame both viewports should use = union of the valid
    before/after render extents.

    Returns ``None`` when neither side has a usable world bbox (caller keeps the
    per-side fallback). When only one side is valid, that side's frame is used
    (so an unmatched/blank side does not collapse the other).
    """

    boxes = [
        b for b in (
            _v2_world_bbox_from_transform(before_transform),
            _v2_world_bbox_from_transform(after_transform),
        )
        if _valid(b)
    ]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def apply_shared_camera_frame(
    before_transform: Optional[Dict[str, Any]],
    after_transform: Optional[Dict[str, Any]],
    *viewports: Any,
) -> Optional[Bbox]:
    """Set every viewport's camera to the shared frame (P0-3, best-effort).

    Computes the shared frame and calls ``set_camera_to_world_bbox`` on each
    viewport so both sides show the SAME world region (fixes '한쪽만 보임').
    PNG placement is left to ``load_raster_image`` (per-side). Fully defensive:
    any failure is swallowed so it can NEVER break the comparison load — the
    GUI keeps the per-side fallback. Returns the shared bbox (or ``None``).
    """

    try:
        shared = shared_world_bbox_from_transforms(before_transform, after_transform)
    except Exception:  # noqa: BLE001
        return None
    if shared is None:
        return None
    for vp in viewports:
        setter = getattr(vp, "set_camera_to_world_bbox", None) if vp is not None else None
        if callable(setter):
            try:
                setter(shared)
            except Exception:  # noqa: BLE001
                continue
    return shared


__all__ = ["shared_world_bbox_from_transforms", "apply_shared_camera_frame"]
