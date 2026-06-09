# -*- coding: utf-8 -*-
"""Small GUI extension hooks kept outside the workbench monolith."""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_BEFORE_FRAME_KEYS = (
    "cad_frame_bbox_a",
    "before_cad_frame_bbox",
    "dwg_frame_bbox_a",
    "before_dwg_frame_bbox",
    "source_a_frame_bbox",
    "frame_bbox_a",
)
_AFTER_FRAME_KEYS = (
    "cad_frame_bbox_b",
    "after_cad_frame_bbox",
    "dwg_frame_bbox_b",
    "after_dwg_frame_bbox",
    "source_b_frame_bbox",
    "frame_bbox_b",
)
_COMMON_FRAME_KEYS = (
    "sheet_frame_bbox",
    "cad_frame_bbox",
    "dwg_frame_bbox",
    "drawing_frame_bbox",
    "frame_bbox",
)


def attach_visual_extensions(workbench: Any, menu_bar: Any) -> None:
    """Attach optional visual-review menu actions without breaking menu build."""

    try:
        from src.gui.hybrid_reference_pdf import attach_reference_pdf_action

        attach_reference_pdf_action(workbench, menu_bar)
    except Exception:  # noqa: BLE001
        logger.debug("Optional visual extension attach failed", exc_info=True)


def apply_shared_lightweight_camera_frame(
    workbench: Any,
    viewer_pair: Mapping[str, Any],
) -> None:
    """Synchronize before/after lightweight camera frames when transforms exist."""

    try:
        from src.services.comparison.sheet_frame_alignment import (
            apply_sheet_frame_camera_alignment,
        )
        from src.services.comparison.viewer_frame import apply_shared_camera_frame

        before_vp = getattr(workbench, "preview_before_lightweight_v2", None)
        after_vp = getattr(workbench, "preview_after_lightweight_v2", None)

        before_transform = _transform_with_pair_frame(
            viewer_pair.get("before_transform"),
            viewer_pair,
            side="before",
        )
        after_transform = _transform_with_pair_frame(
            viewer_pair.get("after_transform"),
            viewer_pair,
            side="after",
        )

        # 1) Sheet-frame (도곽) alignment — returns None for multi-detail sheets
        #    that have no single modelspace drawing frame.
        aligned = apply_sheet_frame_camera_alignment(
            before_transform,
            after_transform,
            before_vp,
            after_vp,
        )
        if aligned is not None:
            return

        # 2) Content-aware framing — frame both panes to the union of the active
        #    change-zone bboxes so a multi-detail sheet shows its changed details
        #    at real size on load instead of a near-blank full sheet. Falls
        #    through when there are no usable change zones.
        if _apply_content_frame_to_change_zones(workbench, before_vp, after_vp):
            return

        # 3) Fallback — the existing shared full-extents camera frame.
        apply_shared_camera_frame(
            before_transform,
            after_transform,
            before_vp,
            after_vp,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Shared lightweight camera frame hook failed", exc_info=True)


def _apply_content_frame_to_change_zones(workbench: Any, before_vp: Any, after_vp: Any) -> bool:
    """Frame both lightweight viewports to the union of the active change-zone
    bboxes; return True when a content frame was computed and applied.

    Root-cause fix: the default path fits the camera to the whole multi-detail
    sheet, making every detail sub-pixel on load. The change-zone world bboxes are
    already in ``workbench._active_overlays_by_zone``; reuse the SAME coordinate
    conversion the per-zone focus path uses (CAD pass-through; PDF pixel→points).
    Keeps ONE shared frame for both panes (preserves before/after alignment).
    """

    overlays = list((getattr(workbench, "_active_overlays_by_zone", {}) or {}).values())
    if not overlays:
        return False
    try:
        from src.services.comparison.content_frame import content_frame_from_zone_bboxes
        from src.gui.lightweight_viewport import (
            _page_height_points_from_world_bbox,
            convert_bbox_to_world_space,
        )
    except Exception:  # noqa: BLE001
        return False

    page_height = _page_height_points_from_world_bbox(
        getattr(before_vp, "world_bbox", (0.0, 0.0, 0.0, 0.0))
    )

    def _to_world(raw: Any, space: str, dpi: float):
        return convert_bbox_to_world_space(
            raw,
            coordinate_space=space,
            pdf_dpi=dpi,
            page_height_points=page_height,
        )

    frame = content_frame_from_zone_bboxes(overlays, _to_world)
    if frame is None:
        return False

    applied = False
    for viewport in (before_vp, after_vp):
        setter = getattr(viewport, "set_camera_to_world_bbox", None) if viewport is not None else None
        if not callable(setter):
            continue
        try:
            setter(frame)
            applied = True
        except Exception:  # noqa: BLE001
            continue
    return applied


def _transform_with_pair_frame(
    transform: Any,
    viewer_pair: Mapping[str, Any],
    *,
    side: str,
) -> Any:
    if not isinstance(transform, Mapping):
        return transform
    if any(key in transform for key in _COMMON_FRAME_KEYS):
        return transform
    side_keys = _BEFORE_FRAME_KEYS if side == "before" else _AFTER_FRAME_KEYS
    for key in (*side_keys, *_COMMON_FRAME_KEYS):
        value = viewer_pair.get(key)
        if value is not None:
            enriched = dict(transform)
            enriched["sheet_frame_bbox"] = value
            enriched["sheet_frame_bbox_source_key"] = key
            return enriched
    return transform


__all__ = [
    "attach_visual_extensions",
    "apply_shared_lightweight_camera_frame",
]
