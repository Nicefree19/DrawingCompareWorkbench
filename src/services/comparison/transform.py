# -*- coding: utf-8 -*-
"""World↔pixel affine transforms for the lightweight viewer engine.

Phase F replaces the implicit "image bbox is the source of truth" coordinate
model with an explicit world-first model. Every rendered artifact (overview
tile, zone crop, simplified preview) carries a 6-element affine matrix and a
``transform_quality`` tag so the viewer can confidently overlay change-zone
markers without drift.

The math follows the planning report verbatim. Given a world bbox
``[x_min, y_min, x_max, y_max]`` of effective width ``W`` and height ``H``,
target pixel size ``(w_p, h_p)`` and margin ``(m_x, m_y)``:

    s = min((w_p - 2·m_x) / W, (h_p - 2·m_y) / H)
    x_p = m_x + (x_w - x_min) · s
    y_p = h_p - m_y - (y_w - y_min) · s        # Y-axis flipped (image y-down)

    x_w = x_min + (x_p - m_x) / s
    y_w = y_min + (h_p - m_y - y_p) / s

This module is **pure** — no Qt, no ezdxf, no I/O. It is unit-testable in
milliseconds and safe to import from any subprocess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final, Literal, Tuple

# A 6-element flat affine: ``[a, b, c, d, e, f]`` mapping ``(x, y, 1)`` →
# ``(a·x + b·y + c, d·x + e·y + f)``. Compatible with PDF / SVG / Cairo
# conventions. Stored as a tuple so it round-trips cleanly through JSON.
Affine6 = Tuple[float, float, float, float, float, float]
Bbox = Tuple[float, float, float, float]
PixelSize = Tuple[int, int]

TransformQuality = Literal["exact", "estimated", "relative_only"]

#: Sentinel returned when a world bbox collapses to a point (W or H ≤ 0).
DEGENERATE_SCALE: Final[float] = 0.0


@dataclass(frozen=True)
class AffineParams:
    """Result of fitting a world bbox into a pixel canvas.

    Attributes:
        scale: world-units-per-pixel (uniform, always positive when valid).
        offset_x: world x of the canvas left edge (after margin).
        offset_y: world y of the canvas bottom edge (after margin).
        margin_px: pixel margin applied symmetrically (x, y).
        pixel_size: target pixel size (w, h).
        world_bbox: source world bbox (xmin, ymin, xmax, ymax).
        world_to_pixel: 6-element affine for ``(world → pixel)``.
        pixel_to_world: 6-element affine for ``(pixel → world)``.
        quality: ``"exact"`` for the fit produced here. Callers may downgrade
            it to ``"estimated"`` if the world_bbox itself was approximated,
            or ``"relative_only"`` for synthetic backgrounds.
    """

    scale: float
    offset_x: float
    offset_y: float
    margin_px: Tuple[int, int]
    pixel_size: PixelSize
    world_bbox: Bbox
    world_to_pixel: Affine6
    pixel_to_world: Affine6
    quality: TransformQuality = "exact"

    @property
    def units_per_pixel(self) -> float:
        """Alias for ``scale`` — preferred name when used as camera state."""

        return 1.0 / self.scale if self.scale > 0 else math.inf

    def to_manifest_dict(self) -> dict[str, object]:
        """Serialise into the per-artifact manifest fields documented in the plan."""

        return {
            "world_bbox": list(self.world_bbox),
            "pixel_size": list(self.pixel_size),
            "world_to_pixel": list(self.world_to_pixel),
            "pixel_to_world": list(self.pixel_to_world),
            "transform_quality": self.quality,
            "scale_world_per_pixel": (
                1.0 / self.scale if self.scale > 0 else None
            ),
            "margin_px": list(self.margin_px),
        }


def fit_world_to_pixels(
    world_bbox: Bbox,
    pixel_size: PixelSize,
    *,
    padding_px: int | Tuple[int, int] = 0,
    quality: TransformQuality = "exact",
) -> AffineParams:
    """Compute the affine that fits ``world_bbox`` into ``pixel_size``.

    Uniform scaling preserves aspect ratio. The bbox is centred inside the
    pixel canvas so any leftover space is split symmetrically (consistent
    with how matplotlib / Qt scene fits work). ``padding_px`` may be a single
    integer (applied to both axes) or an ``(x, y)`` tuple.

    Args:
        world_bbox: ``(xmin, ymin, xmax, ymax)`` in world units (mm or whatever
            the source DXF uses; the function is unit-agnostic).
        pixel_size: ``(w_p, h_p)`` in pixels.
        padding_px: margin reserved on every side, in pixels. Useful for
            label/badge gutters.
        quality: tag stored on the result. Pass ``"estimated"`` when the world
            bbox is itself a rough estimate (e.g. inferred from page MediaBox
            for a PDF without precise bbox metadata).

    Raises:
        ValueError: if ``pixel_size`` has a non-positive component.
    """

    w_p, h_p = pixel_size
    if w_p <= 0 or h_p <= 0:
        raise ValueError(
            f"pixel_size must be positive, got ({w_p}, {h_p})"
        )

    if isinstance(padding_px, int):
        m_x = m_y = max(0, padding_px)
    else:
        m_x, m_y = padding_px
        m_x = max(0, int(m_x))
        m_y = max(0, int(m_y))

    x_min, y_min, x_max, y_max = world_bbox
    world_w = x_max - x_min
    world_h = y_max - y_min

    avail_w = w_p - 2 * m_x
    avail_h = h_p - 2 * m_y

    if world_w <= 0 or world_h <= 0 or avail_w <= 0 or avail_h <= 0:
        # Degenerate bbox or canvas — return identity-like params with
        # scale=0 so downstream code can detect and refuse to render.
        identity: Affine6 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        return AffineParams(
            scale=DEGENERATE_SCALE,
            offset_x=x_min,
            offset_y=y_min,
            margin_px=(m_x, m_y),
            pixel_size=(int(w_p), int(h_p)),
            world_bbox=tuple(world_bbox),  # type: ignore[arg-type]
            world_to_pixel=identity,
            pixel_to_world=identity,
            quality=quality,
        )

    s = min(avail_w / world_w, avail_h / world_h)

    # Centre the bbox in the canvas so leftover space is split evenly.
    used_w_px = world_w * s
    used_h_px = world_h * s
    extra_x = (avail_w - used_w_px) / 2.0
    extra_y = (avail_h - used_h_px) / 2.0

    # Effective offsets after centring. ``offset_x`` is the world x at the
    # canvas left edge minus the centring shift; symmetric for y.
    eff_m_x = m_x + extra_x
    eff_m_y = m_y + extra_y

    # World→pixel as a 6-tuple. Y axis is flipped because pixel space grows
    # downward while world space grows upward.
    a = s
    b = 0.0
    c = eff_m_x - x_min * s
    d = 0.0
    e = -s
    f = h_p - eff_m_y + y_min * s

    world_to_pixel: Affine6 = (a, b, c, d, e, f)

    # Inverse via direct algebra (matrix is well-conditioned by construction).
    inv_s = 1.0 / s
    pixel_to_world: Affine6 = (
        inv_s,
        0.0,
        x_min - eff_m_x * inv_s,
        0.0,
        -inv_s,
        y_min + (h_p - eff_m_y) * inv_s,
    )

    return AffineParams(
        scale=s,
        offset_x=x_min - eff_m_x * inv_s,
        offset_y=y_min + (h_p - eff_m_y) * inv_s,
        margin_px=(m_x, m_y),
        pixel_size=(int(w_p), int(h_p)),
        world_bbox=tuple(world_bbox),  # type: ignore[arg-type]
        world_to_pixel=world_to_pixel,
        pixel_to_world=pixel_to_world,
        quality=quality,
    )


def apply_affine(matrix: Affine6, x: float, y: float) -> Tuple[float, float]:
    """Apply a 6-element affine to a 2D point. Pure helper, no allocation."""

    a, b, c, d, e, f = matrix
    return (a * x + b * y + c, d * x + e * y + f)


def world_to_pixel(params: AffineParams, x_w: float, y_w: float) -> Tuple[float, float]:
    """Forward transform — convenience wrapper over :func:`apply_affine`."""

    return apply_affine(params.world_to_pixel, x_w, y_w)


def pixel_to_world(params: AffineParams, x_p: float, y_p: float) -> Tuple[float, float]:
    """Inverse transform — convenience wrapper over :func:`apply_affine`."""

    return apply_affine(params.pixel_to_world, x_p, y_p)


def expand_bbox(bbox: Bbox, padding_world: float) -> Bbox:
    """Return ``bbox`` enlarged by ``padding_world`` units on every side.

    Used by the zone-render service to give the user a small context margin
    around the picked change zone before issuing an exact crop.
    """

    if padding_world <= 0:
        return tuple(bbox)  # type: ignore[return-value]
    x_min, y_min, x_max, y_max = bbox
    return (x_min - padding_world, y_min - padding_world,
            x_max + padding_world, y_max + padding_world)


def union_bboxes(*bboxes: Bbox) -> Bbox:
    """Combine 1+ world bboxes into the smallest enclosing bbox.

    Mirrors the helper of the same name in ``zone_render_service.py:158`` —
    duplicated here as a pure-Python fallback so the lightweight viewer module
    has no import dependency on the existing zone renderer (which pulls in
    ezdxf and matplotlib at import time).
    """

    if not bboxes:
        raise ValueError("union_bboxes() requires at least one bbox")
    x_min = min(b[0] for b in bboxes)
    y_min = min(b[1] for b in bboxes)
    x_max = max(b[2] for b in bboxes)
    y_max = max(b[3] for b in bboxes)
    return (x_min, y_min, x_max, y_max)


@dataclass(frozen=True)
class SharedCamera:
    """World-space camera shared between before/after viewports.

    Stored as world centre + units-per-pixel rather than ``(zoom, pan)`` so
    the two viewports can synchronise even when their drawings have
    different extents — the camera describes "what world point is at the
    centre of the screen, and how much world distance one pixel represents".
    """

    center_x: float
    center_y: float
    units_per_pixel: float

    def to_dict(self) -> dict[str, float]:
        return {
            "center_x": self.center_x,
            "center_y": self.center_y,
            "units_per_pixel": self.units_per_pixel,
        }

    @classmethod
    def from_world_bbox(
        cls,
        world_bbox: Bbox,
        viewport_pixel_size: PixelSize,
        *,
        padding_px: int = 16,
    ) -> "SharedCamera":
        """Build a camera that fits the bbox into the viewport (centred)."""

        params = fit_world_to_pixels(world_bbox, viewport_pixel_size,
                                     padding_px=padding_px)
        x_min, y_min, x_max, y_max = world_bbox
        return cls(
            center_x=(x_min + x_max) / 2.0,
            center_y=(y_min + y_max) / 2.0,
            units_per_pixel=(1.0 / params.scale) if params.scale > 0 else math.inf,
        )


__all__ = [
    "Affine6",
    "Bbox",
    "PixelSize",
    "TransformQuality",
    "DEGENERATE_SCALE",
    "AffineParams",
    "SharedCamera",
    "fit_world_to_pixels",
    "apply_affine",
    "world_to_pixel",
    "pixel_to_world",
    "expand_bbox",
    "union_bboxes",
]
