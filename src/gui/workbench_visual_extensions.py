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
        aligned = apply_sheet_frame_camera_alignment(
            before_transform,
            after_transform,
            getattr(workbench, "preview_before_lightweight_v2", None),
            getattr(workbench, "preview_after_lightweight_v2", None),
        )
        if aligned is not None:
            return

        apply_shared_camera_frame(
            before_transform,
            after_transform,
            getattr(workbench, "preview_before_lightweight_v2", None),
            getattr(workbench, "preview_after_lightweight_v2", None),
        )
    except Exception:  # noqa: BLE001
        logger.debug("Shared lightweight camera frame hook failed", exc_info=True)


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
