"""Pure pixel-space alignment math for P0-2b visual alignment.

Goal: when the diff pipeline computed a *significant* rigid alignment ``T``,
warp the **after** raster so it visually aligns with the **before** raster
(translation AND rotation) WITHOUT changing any diff result. The raster warp,
the serialized ``after_transform``, and the change markers are all derived from
the SAME ``RigidTransform`` so they cannot desync (lockstep by construction).

Direction convention (verified against ``dxf_comparator.py`` :1509 and the sign
note at :1529): ``estimate_rigid_transform(before, after)`` returns ``T`` mapping
**AFTER (B) -> BEFORE (A)**. So to bring after-side geometry into the before
frame we apply ``T`` DIRECTLY (NOT its inverse).

This module is pure: no Qt, no file IO. ``numpy``/``cv2`` are imported lazily
*inside* :func:`warp_after_image` only — every other function is plain Python and
fully unit-testable headless.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence, Tuple

# a, b, c, d, e, f  ->  x' = a*x + b*y + e ; y' = c*x + d*y + f
Affine = Tuple[float, float, float, float, float, float]

Mat3 = Tuple[
    Tuple[float, float, float],
    Tuple[float, float, float],
    Tuple[float, float, float],
]


# ---------------------------------------------------------------------------
# small pure 3x3 homogeneous affine helpers
# ---------------------------------------------------------------------------


def _affine_from_transform(side: Dict[str, Any], key: str) -> Optional[Affine]:
    """Pull the {a,b,c,d,e,f} affine ``key`` out of a transform dict."""
    if not isinstance(side, dict):
        return None
    raw = side.get(key)
    if not isinstance(raw, dict):
        return None
    try:
        return (
            float(raw["a"]),
            float(raw["b"]),
            float(raw["c"]),
            float(raw["d"]),
            float(raw["e"]),
            float(raw["f"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _affine_to_mat3(af: Affine) -> Mat3:
    a, b, c, d, e, f = af
    return ((a, b, e), (c, d, f), (0.0, 0.0, 1.0))


def _mat3_to_affine(m: Mat3) -> Affine:
    return (m[0][0], m[0][1], m[1][0], m[1][1], m[0][2], m[1][2])


def _mat3_mul(m: Mat3, n: Mat3) -> Mat3:
    return tuple(  # type: ignore[return-value]
        tuple(
            m[i][0] * n[0][j] + m[i][1] * n[1][j] + m[i][2] * n[2][j]
            for j in range(3)
        )
        for i in range(3)
    )


def _rigid_to_mat3(rigid: Any) -> Mat3:
    """World-space matrix for ``RigidTransform`` (maps after B -> before A)."""
    theta = float(rigid.theta_rad)
    cs = math.cos(theta)
    sn = math.sin(theta)
    return ((cs, -sn, float(rigid.dx)), (sn, cs, float(rigid.dy)), (0.0, 0.0, 1.0))


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def is_alignment_active(rigid: Any) -> bool:
    """True only when there is a transform worth applying.

    Mirrors ``RigidTransform.is_significant``; ``None`` / missing -> False so the
    caller's default path (no warp) is preserved with zero behaviour change.
    """
    if rigid is None:
        return False
    try:
        return bool(rigid.is_significant)
    except AttributeError:
        return False


def compose_after_pixel_affine(
    before_transform: Dict[str, Any],
    after_transform: Dict[str, Any],
    rigid: Any,
) -> Optional[Affine]:
    """Pixel-space affine that warps an *after* image into the *before* frame.

    ``M_px = W2P_before . T(B->A) . P2W_after``  (3x3 compose, top 2 rows).

    A point in *after pixel* coordinates is mapped to *before pixel*
    coordinates. This is the forward ``src -> dst`` matrix expected by
    ``cv2.warpAffine`` (which inverts it internally to sample).

    Returns ``None`` when no warp should happen (no/insignificant transform, or
    either transform dict is missing the affines) so the caller keeps the
    untouched after image.
    """
    if not is_alignment_active(rigid):
        return None
    p2w_after = _affine_from_transform(after_transform, "pixel_to_world")
    w2p_before = _affine_from_transform(before_transform, "world_to_pixel")
    if p2w_after is None or w2p_before is None:
        return None
    composed = _mat3_mul(
        _affine_to_mat3(w2p_before),
        _mat3_mul(_rigid_to_mat3(rigid), _affine_to_mat3(p2w_after)),
    )
    return _mat3_to_affine(composed)


def aligned_after_transform(
    before_transform: Dict[str, Any],
    after_transform: Dict[str, Any],
    rigid: Any,
) -> Dict[str, Any]:
    """The ``after_transform`` to serialize after the warp.

    Once the after raster is warped into the before pixel frame, its pixel<->world
    mapping IS the before mapping, so the viewer's hit-testing stays correct.
    When no warp happens, the original after_transform is returned unchanged.
    """
    if not is_alignment_active(rigid):
        return after_transform
    if _affine_from_transform(before_transform, "world_to_pixel") is None:
        return after_transform
    return dict(before_transform)


def align_world_point(point: Sequence[float], rigid: Any) -> Tuple[float, float]:
    """Map a single *after-frame* world point into the *before* frame via ``T``.

    Identity when the transform is not active. Used to keep change markers (whose
    world coords are in the after frame) on the warped raster.
    """
    x, y = float(point[0]), float(point[1])
    if not is_alignment_active(rigid):
        return (x, y)
    return rigid.apply(x, y)


def align_world_bbox(
    bbox: Optional[Sequence[float]], rigid: Any
) -> Optional[Sequence[float]]:
    """Transform an axis-aligned ``[xmin, ymin, xmax, ymax]`` by ``T`` and
    re-envelope (rotation can tilt the box, so we take the new extent).

    Returns the input unchanged when the transform is not active or the bbox is
    malformed (defensive — never fabricate geometry).
    """
    if not is_alignment_active(rigid) or bbox is None:
        return bbox
    try:
        xmin, ymin, xmax, ymax = (
            float(bbox[0]),
            float(bbox[1]),
            float(bbox[2]),
            float(bbox[3]),
        )
    except (TypeError, ValueError, IndexError):
        return bbox
    corners = (
        rigid.apply(xmin, ymin),
        rigid.apply(xmax, ymin),
        rigid.apply(xmax, ymax),
        rigid.apply(xmin, ymax),
    )
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return [min(xs), min(ys), max(xs), max(ys)]


def warp_after_image(image: Any, pixel_affine: Optional[Affine], out_size: Tuple[int, int]) -> Any:
    """Warp an after-image numpy array by ``pixel_affine`` into ``out_size``.

    Deterministic: fixed ``INTER_LINEAR`` + constant white border (matches the
    render background). Returns ``image`` unchanged when ``pixel_affine`` is None.
    Raises ImportError only if cv2/numpy are unavailable AND a warp is requested.
    """
    if pixel_affine is None:
        return image
    import numpy as np  # local: keep module import-light & pure for the math API

    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError("OpenCV is required for after-raster alignment warp") from exc

    a, b, c, d, e, f = pixel_affine
    matrix = np.array([[a, b, e], [c, d, f]], dtype=np.float64)
    width, height = int(out_size[0]), int(out_size[1])
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
