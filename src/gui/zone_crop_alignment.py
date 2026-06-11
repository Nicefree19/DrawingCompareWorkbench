# -*- coding: utf-8 -*-
"""Small helpers for synchronising selected-zone lightweight crop views."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]
EMPTY_SIDE_NOTICE = "이 면에는 선택 구역에 해당하는 도면이 없습니다."


def union_bboxes(*bboxes: Optional[tuple[float, float, float, float]]) -> Optional[BBox]:
    valid: list[BBox] = []
    for bbox in bboxes:
        if bbox is None:
            continue
        try:
            candidate = tuple(float(v) for v in bbox[:4])
        except (TypeError, ValueError):
            continue
        # Live top_issues overlays carry the absent side as an EMPTY list —
        # bbox[:4] of [] is () and indexing it crashed the union.
        if len(candidate) < 4:
            continue
        if candidate[2] > candidate[0] and candidate[3] > candidate[1]:
            valid.append(candidate)  # type: ignore[arg-type]
    if not valid:
        return None
    return (
        min(bbox[0] for bbox in valid),
        min(bbox[1] for bbox in valid),
        max(bbox[2] for bbox in valid),
        max(bbox[3] for bbox in valid),
    )


def show_empty_side_frame(viewport: Any, world_bbox: Optional[BBox]) -> bool:
    if world_bbox is None:
        return False
    try:
        show_empty = getattr(viewport, "show_empty_world_bbox", None)
        if callable(show_empty):
            return bool(show_empty(world_bbox, empty_notice=EMPTY_SIDE_NOTICE))
    except Exception:
        logger.debug("zone crop empty-frame failed", exc_info=True)
    return False


def sync_crop_camera(
    viewport: Any,
    *,
    shared_bbox: Optional[BBox],
    loaded_frame_count: int,
) -> None:
    if shared_bbox is not None and loaded_frame_count >= 2:
        viewport.set_camera_to_world_bbox(shared_bbox, padding_ratio=0.0)
        return
    viewport.fit_to_view()


def maybe_log_camera_state(owner: Any, viewport: Any, zone_id: str) -> None:
    log_camera = getattr(owner, "_log_zone_crop_camera_state_v2", None)
    if callable(log_camera):
        log_camera(viewport, zone_id)
