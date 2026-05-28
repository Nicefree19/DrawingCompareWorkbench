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
from typing import Any, Final, Literal, Optional, Tuple

# A 6-element flat affine: ``[a, b, c, d, e, f]`` mapping ``(x, y, 1)`` →
# ``(a·x + b·y + c, d·x + e·y + f)``. Compatible with PDF / SVG / Cairo
# conventions. Stored as a tuple so it round-trips cleanly through JSON.
Affine6 = Tuple[float, float, float, float, float, float]
Bbox = Tuple[float, float, float, float]
PixelSize = Tuple[int, int]

TransformQuality = Literal["exact", "estimated", "relative_only"]
CoordinateSpace = Literal[
    "cad_wcs_mm",
    "region_local_cad",
    "pdf_page_points_bl",
    "image_pixels_tl",
    # Legacy aliases kept readable because old viewer packages already use them.
    "world_xy_2d",
    "cad_world",
    "image_pixels",
]
SourceTruth = Literal["cad_entity", "pdf_visual", "pdf_text", "ocr", "unknown"]
YAxis = Literal["up", "down"]

#: Sentinel returned when a world bbox collapses to a point (W or H ≤ 0).
DEGENERATE_SCALE: Final[float] = 0.0

COORDINATE_CONTRACT_VERSION: Final[str] = "coordinate_contract.v1"

COORD_CAD_WCS_MM: Final[str] = "cad_wcs_mm"
COORD_REGION_LOCAL_CAD: Final[str] = "region_local_cad"
COORD_PDF_PAGE_POINTS_BL: Final[str] = "pdf_page_points_bl"
COORD_IMAGE_PIXELS_TL: Final[str] = "image_pixels_tl"

COORD_LEGACY_WORLD_XY_2D: Final[str] = "world_xy_2d"
COORD_LEGACY_CAD_WORLD: Final[str] = "cad_world"
COORD_LEGACY_IMAGE_PIXELS: Final[str] = "image_pixels"

SOURCE_TRUTH_CAD_ENTITY: Final[str] = "cad_entity"
SOURCE_TRUTH_PDF_VISUAL: Final[str] = "pdf_visual"
SOURCE_TRUTH_UNKNOWN: Final[str] = "unknown"

Y_AXIS_UP: Final[str] = "up"
Y_AXIS_DOWN: Final[str] = "down"

_COORDINATE_SPACE_ALIASES: Final[dict[str, str]] = {
    "": COORD_CAD_WCS_MM,
    "world": COORD_CAD_WCS_MM,
    COORD_LEGACY_WORLD_XY_2D: COORD_CAD_WCS_MM,
    COORD_LEGACY_CAD_WORLD: COORD_CAD_WCS_MM,
    COORD_LEGACY_IMAGE_PIXELS: COORD_IMAGE_PIXELS_TL,
    "pdf_page_points": COORD_PDF_PAGE_POINTS_BL,
    "pdf_points": COORD_PDF_PAGE_POINTS_BL,
}


def normalize_coordinate_space(value: object) -> str:
    """Return the canonical coordinate-space token for manifest metadata.

    Old viewer packages used broad labels such as ``cad_world`` and
    ``image_pixels``. R1 keeps those packages loadable while new manifests use
    unambiguous axis/origin names.
    """

    raw = str(value or "").strip()
    return _COORDINATE_SPACE_ALIASES.get(raw, raw)


def coordinate_space_y_axis(space: object) -> str:
    """Return ``"up"`` or ``"down"`` for a coordinate space."""

    canonical = normalize_coordinate_space(space)
    return Y_AXIS_DOWN if canonical == COORD_IMAGE_PIXELS_TL else Y_AXIS_UP


def source_truth_for_coordinate_space(space: object) -> str:
    """Best-effort source truth implied by a coordinate space."""

    canonical = normalize_coordinate_space(space)
    if canonical in {COORD_CAD_WCS_MM, COORD_REGION_LOCAL_CAD}:
        return SOURCE_TRUTH_CAD_ENTITY
    if canonical in {COORD_PDF_PAGE_POINTS_BL, COORD_IMAGE_PIXELS_TL}:
        return SOURCE_TRUTH_PDF_VISUAL
    return SOURCE_TRUTH_UNKNOWN


def normalise_bbox(raw: object) -> Optional[Bbox]:
    """Return a bbox tuple from dict/list forms used across manifests."""

    if raw is None:
        return None
    if isinstance(raw, dict):
        try:
            return (
                float(raw["min_x"]),
                float(raw["min_y"]),
                float(raw["max_x"]),
                float(raw["max_y"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(raw, (list, tuple)):
        if len(raw) < 4:
            return None
        try:
            return (
                float(raw[0]),
                float(raw[1]),
                float(raw[2]),
                float(raw[3]),
            )
        except (TypeError, ValueError):
            return None
    return None


def transform_bbox(matrix: Affine6, bbox: Bbox) -> Bbox:
    """Transform all four bbox corners and return the enclosing bbox."""

    x0, y0, x1, y1 = bbox
    points = (
        apply_affine(matrix, x0, y0),
        apply_affine(matrix, x0, y1),
        apply_affine(matrix, x1, y0),
        apply_affine(matrix, x1, y1),
    )
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pdf_image_pixels_to_page_points_bbox(
    bbox: object,
    *,
    dpi: float,
    page_height_points: float = 0.0,
) -> Optional[Bbox]:
    """Convert top-left image pixels to PDF/page points.

    When ``page_height_points`` is supplied, the result uses bottom-left,
    Y-up page coordinates. Without it, the helper performs only DPI scaling
    and keeps the input Y orientation.
    """

    coords = normalise_bbox(bbox)
    if coords is None:
        return None
    dpi_val = _safe_float(dpi)
    if dpi_val <= 0:
        return coords
    scale = 72.0 / dpi_val
    x0, y0, x1, y1 = coords
    left = min(x0, x1) * scale
    right = max(x0, x1) * scale
    top = min(y0, y1) * scale
    bottom = max(y0, y1) * scale
    page_h = _safe_float(page_height_points)
    if page_h > 0:
        return (left, page_h - bottom, right, page_h - top)
    return (left, top, right, bottom)


def pdf_page_points_to_image_pixels_bbox(
    bbox: object,
    *,
    dpi: float,
    page_height_points: float = 0.0,
) -> Optional[Bbox]:
    """Convert PDF/page points to top-left image pixels."""

    coords = normalise_bbox(bbox)
    if coords is None:
        return None
    dpi_val = _safe_float(dpi)
    if dpi_val <= 0:
        return coords
    scale = dpi_val / 72.0
    x0, y0, x1, y1 = coords
    left = min(x0, x1) * scale
    right = max(x0, x1) * scale
    y_min = min(y0, y1)
    y_max = max(y0, y1)
    page_h = _safe_float(page_height_points)
    if page_h > 0:
        return (
            left,
            (page_h - y_max) * scale,
            right,
            (page_h - y_min) * scale,
        )
    return (left, y_min * scale, right, y_max * scale)


def convert_bbox_to_world_space(
    bbox: object,
    *,
    coordinate_space: object = "",
    pdf_dpi: float = 0.0,
    page_height_points: float = 0.0,
) -> Optional[Bbox]:
    """Convert a bbox to the lightweight viewer's current world space.

    CAD spaces are already world-like and pass through. PDF image-pixel bboxes
    are scaled to PDF points and Y-flipped when the page height is known.
    """

    coords = normalise_bbox(bbox)
    if coords is None:
        return None
    space = normalize_coordinate_space(coordinate_space)
    if space == COORD_IMAGE_PIXELS_TL:
        return pdf_image_pixels_to_page_points_bbox(
            coords,
            dpi=pdf_dpi,
            page_height_points=page_height_points,
        )
    return coords


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
    coordinate_space: str = COORD_CAD_WCS_MM
    source_truth: str = SOURCE_TRUTH_CAD_ENTITY
    y_axis: str = Y_AXIS_UP

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
            "coordinate_contract_version": COORDINATE_CONTRACT_VERSION,
            "bbox_coordinate_space": self.coordinate_space,
            "source_truth": self.source_truth,
            "y_axis": self.y_axis,
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
    coordinate_space: CoordinateSpace | str = COORD_CAD_WCS_MM,
    source_truth: SourceTruth | str | None = None,
    y_axis: YAxis | str | None = None,
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

    bbox_coordinate_space = normalize_coordinate_space(coordinate_space)
    bbox_source_truth = str(
        source_truth or source_truth_for_coordinate_space(bbox_coordinate_space)
    )
    bbox_y_axis = str(y_axis or coordinate_space_y_axis(bbox_coordinate_space))

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
            coordinate_space=bbox_coordinate_space,
            source_truth=bbox_source_truth,
            y_axis=bbox_y_axis,
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
        coordinate_space=bbox_coordinate_space,
        source_truth=bbox_source_truth,
        y_axis=bbox_y_axis,
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
    "CoordinateSpace",
    "SourceTruth",
    "YAxis",
    "DEGENERATE_SCALE",
    "COORDINATE_CONTRACT_VERSION",
    "COORD_CAD_WCS_MM",
    "COORD_REGION_LOCAL_CAD",
    "COORD_PDF_PAGE_POINTS_BL",
    "COORD_IMAGE_PIXELS_TL",
    "COORD_LEGACY_WORLD_XY_2D",
    "COORD_LEGACY_CAD_WORLD",
    "COORD_LEGACY_IMAGE_PIXELS",
    "SOURCE_TRUTH_CAD_ENTITY",
    "SOURCE_TRUTH_PDF_VISUAL",
    "SOURCE_TRUTH_UNKNOWN",
    "Y_AXIS_UP",
    "Y_AXIS_DOWN",
    "AffineParams",
    "SharedCamera",
    "normalize_coordinate_space",
    "coordinate_space_y_axis",
    "source_truth_for_coordinate_space",
    "normalise_bbox",
    "transform_bbox",
    "pdf_image_pixels_to_page_points_bbox",
    "pdf_page_points_to_image_pixels_bbox",
    "convert_bbox_to_world_space",
    "fit_world_to_pixels",
    "apply_affine",
    "world_to_pixel",
    "pixel_to_world",
    "expand_bbox",
    "union_bboxes",
]
