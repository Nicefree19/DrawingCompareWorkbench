# -*- coding: utf-8 -*-
"""Sheet-frame based before/after viewer alignment helpers.

CAD sheets often move between revisions because of re-origining, title-block
edits, or copied details. In that case a single absolute world bbox cannot make
both viewports inspect the same sheet area. These helpers normalize each side's
detected drawing frame (도곽) into a shared 0..1 sheet-local space, then map the
same local window back to each side's native CAD coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from .transform import normalise_bbox

BBox = Tuple[float, float, float, float]
SHEET_LOCAL_FULL_BBOX: BBox = (0.0, 0.0, 1.0, 1.0)

_FRAME_KEYS = (
    "sheet_frame_bbox",
    "cad_frame_bbox",
    "dwg_frame_bbox",
    "drawing_frame_bbox",
    "frame_bbox",
)


def _valid_bbox(value: object) -> Optional[BBox]:
    bbox = normalise_bbox(value)
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    if x1 <= x0 or y1 <= y0:
        return None
    return (float(x0), float(y0), float(x1), float(y1))


def _aspect(bbox: BBox) -> float:
    return (bbox[2] - bbox[0]) / max(bbox[3] - bbox[1], 1e-9)


def _aspect_mismatch(before_frame: BBox, after_frame: BBox) -> float:
    before_aspect = _aspect(before_frame)
    after_aspect = _aspect(after_frame)
    return abs(before_aspect - after_aspect) / max(after_aspect, 1e-9)


def sheet_frame_bbox_from_mapping(mapping: object) -> Optional[BBox]:
    """Extract a detected sheet/drawing-frame bbox from a transform-like dict."""

    if not isinstance(mapping, Mapping):
        return None
    for key in _FRAME_KEYS:
        bbox = _valid_bbox(mapping.get(key))
        if bbox is not None:
            return bbox
    alignment = mapping.get("cad_pdf_alignment")
    if isinstance(alignment, Mapping):
        bbox = _valid_bbox(alignment.get("cad_frame_bbox"))
        if bbox is not None:
            return bbox
    return None


@dataclass(frozen=True)
class SheetFrameAlignment:
    """A common sheet-local coordinate system for a before/after pair."""

    before_frame: BBox
    after_frame: BBox
    quality: str
    aspect_mismatch: float
    method: str = "cad_frame_bbox"

    @property
    def is_usable(self) -> bool:
        return self.quality != "relative_only"

    def native_bbox_for_local(self, side: str, local_bbox: BBox = SHEET_LOCAL_FULL_BBOX) -> BBox:
        frame = self.before_frame if side == "before" else self.after_frame
        fx0, fy0, fx1, fy1 = frame
        fw = fx1 - fx0
        fh = fy1 - fy0
        lx0, ly0, lx1, ly1 = local_bbox
        return (
            fx0 + lx0 * fw,
            fy0 + ly0 * fh,
            fx0 + lx1 * fw,
            fy0 + ly1 * fh,
        )

    def local_bbox_for_native(self, side: str, native_bbox: BBox) -> BBox:
        frame = self.before_frame if side == "before" else self.after_frame
        fx0, fy0, fx1, fy1 = frame
        fw = max(fx1 - fx0, 1e-9)
        fh = max(fy1 - fy0, 1e-9)
        nx0, ny0, nx1, ny1 = native_bbox
        return (
            (nx0 - fx0) / fw,
            (ny0 - fy0) / fh,
            (nx1 - fx0) / fw,
            (ny1 - fy0) / fh,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "before_frame": list(self.before_frame),
            "after_frame": list(self.after_frame),
            "quality": self.quality,
            "aspect_mismatch": self.aspect_mismatch,
            "method": self.method,
        }


def build_sheet_frame_alignment(
    before_transform: object,
    after_transform: object,
    *,
    aspect_tolerance: float = 0.02,
    max_aspect_mismatch: float = 0.08,
) -> Optional[SheetFrameAlignment]:
    """Build a sheet-frame alignment from transform metadata.

    Returns ``None`` when either side has no usable frame or when the two frame
    aspects are too different to trust for automatic viewport synchronization.
    """

    before_frame = sheet_frame_bbox_from_mapping(before_transform)
    after_frame = sheet_frame_bbox_from_mapping(after_transform)
    if before_frame is None or after_frame is None:
        return None

    mismatch = _aspect_mismatch(before_frame, after_frame)
    if mismatch > max_aspect_mismatch:
        return None
    quality = "exact" if mismatch <= aspect_tolerance else "estimated"
    return SheetFrameAlignment(
        before_frame=before_frame,
        after_frame=after_frame,
        quality=quality,
        aspect_mismatch=mismatch,
    )


def camera_bboxes_for_sheet_local_frame(
    before_transform: object,
    after_transform: object,
    *,
    local_bbox: BBox = SHEET_LOCAL_FULL_BBOX,
) -> Optional[tuple[BBox, BBox, SheetFrameAlignment]]:
    alignment = build_sheet_frame_alignment(before_transform, after_transform)
    if alignment is None or not alignment.is_usable:
        return None
    return (
        alignment.native_bbox_for_local("before", local_bbox),
        alignment.native_bbox_for_local("after", local_bbox),
        alignment,
    )


def apply_sheet_frame_camera_alignment(
    before_transform: object,
    after_transform: object,
    before_viewport: Any,
    after_viewport: Any,
    *,
    local_bbox: BBox = SHEET_LOCAL_FULL_BBOX,
) -> Optional[SheetFrameAlignment]:
    """Set each viewport to the native bbox for the same sheet-local window."""

    result = camera_bboxes_for_sheet_local_frame(
        before_transform,
        after_transform,
        local_bbox=local_bbox,
    )
    if result is None:
        return None
    before_bbox, after_bbox, alignment = result
    calls = ((before_viewport, before_bbox), (after_viewport, after_bbox))
    applied = False
    for viewport, bbox in calls:
        setter = getattr(viewport, "set_camera_to_world_bbox", None) if viewport is not None else None
        if not callable(setter):
            continue
        try:
            setter(bbox)
            applied = True
        except Exception:
            continue
    return alignment if applied else None


__all__ = [
    "BBox",
    "SHEET_LOCAL_FULL_BBOX",
    "SheetFrameAlignment",
    "apply_sheet_frame_camera_alignment",
    "build_sheet_frame_alignment",
    "camera_bboxes_for_sheet_local_frame",
    "sheet_frame_bbox_from_mapping",
]
