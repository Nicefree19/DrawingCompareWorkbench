# -*- coding: utf-8 -*-
"""Phase O2 — DXF entity rigid alignment (translation + rotation).

도면 비교 시 실제 변경이 없는데도 원본 DXF가 (+0.5mm, +0.5mm) 같은
미세 시프트만 가지고 있으면 모든 entity의 hash가 달라져 added/deleted
폭증이 발생한다. 이 모듈은 entity 위치를 기반으로 (Δx, Δy, Δθ) rigid
transform을 추정하고, 그것을 이용해 B 좌표계를 A 좌표계로 백투영하는
단순한 도구를 제공한다.

알고리즘 (보고서 §O2 참조):
    1. (entity_type, layer) 그룹별로 candidate pair 모집
       — 50mm 이내 nearest-neighbor (RANSAC가 outlier 제거)
    2. ``cv2.estimateAffinePartial2D`` 로 RANSAC 기반 추정
       — scale 고정, rotation+translation 만 (rigid)
    3. inlier 비율 < 0.5 면 None (정렬 불신뢰)
    4. shift 가 0.05mm 미만이면 None (적용 무의미)

이 모듈은 pure — Qt/ezdxf I/O 없음. NumPy + OpenCV(선택) 만 의존.
OpenCV 부재 시 median-shift fallback 사용.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# OpenCV는 drawing_differ에서 이미 사용 중. RANSAC affine 추정에 필요.
try:
    import cv2  # type: ignore
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

try:
    import numpy as np  # type: ignore
    _NP_AVAILABLE = True
except ImportError:
    _NP_AVAILABLE = False


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RigidTransform:
    """B → A 좌표계로 매핑하는 rigid 2D transform.

    매핑 식:
        x' = cos(θ)·x − sin(θ)·y + dx
        y' = sin(θ)·x + cos(θ)·y + dy

    Attributes:
        dx: x 방향 평행이동 (mm).
        dy: y 방향 평행이동 (mm).
        theta_rad: 회전각 (radian, 반시계 방향 양수).
        inlier_ratio: RANSAC inlier / 총 candidate 비율 (0~1).
            품질 척도 — 0.5 미만이면 호출자가 신뢰성을 판단해야 함.
        candidate_count: 추정에 사용된 candidate pair 개수.
    """

    dx: float
    dy: float
    theta_rad: float
    inlier_ratio: float = 1.0
    candidate_count: int = 0

    @property
    def translation_magnitude(self) -> float:
        return math.hypot(self.dx, self.dy)

    @property
    def is_significant(self) -> bool:
        """0.05mm 이상 시프트 또는 0.01° 이상 회전이면 적용 가치 있음."""
        if self.translation_magnitude > 0.05:
            return True
        if abs(math.degrees(self.theta_rad)) > 0.01:
            return True
        return False

    @property
    def is_translation_only(self) -> bool:
        """회전이 거의 없는 순수 평행이동인지 — fast-path 분기."""
        return abs(self.theta_rad) < 1e-6

    def apply(self, x: float, y: float) -> Tuple[float, float]:
        """단일 좌표를 transform 한다 (B → A)."""
        if self.is_translation_only:
            return (x + self.dx, y + self.dy)
        c = math.cos(self.theta_rad)
        s = math.sin(self.theta_rad)
        return (c * x - s * y + self.dx, s * x + c * y + self.dy)

    def inverse(self) -> "RigidTransform":
        """A → B 매핑 transform."""
        if self.is_translation_only:
            return RigidTransform(
                dx=-self.dx,
                dy=-self.dy,
                theta_rad=0.0,
                inlier_ratio=self.inlier_ratio,
                candidate_count=self.candidate_count,
            )
        # rotation R(-θ), translation -R(-θ)·t
        c = math.cos(-self.theta_rad)
        s = math.sin(-self.theta_rad)
        inv_dx = -(c * self.dx - s * self.dy)
        inv_dy = -(s * self.dx + c * self.dy)
        return RigidTransform(
            dx=inv_dx,
            dy=inv_dy,
            theta_rad=-self.theta_rad,
            inlier_ratio=self.inlier_ratio,
            candidate_count=self.candidate_count,
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            "dx": self.dx,
            "dy": self.dy,
            "theta_rad": self.theta_rad,
            "theta_deg": math.degrees(self.theta_rad),
            "translation_magnitude": self.translation_magnitude,
            "inlier_ratio": self.inlier_ratio,
            "candidate_count": self.candidate_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RigidTransform":
        """``to_dict`` 산출물에서 복원한다 (파생 키 theta_deg 등은 무시).

        canonical 필드(dx, dy, theta_rad)만 읽으며 누락 시 0.0으로 방어한다.
        품질 필드(inlier_ratio, candidate_count)는 있으면 보존한다.
        """
        return cls(
            dx=float(data.get("dx", 0.0)),
            dy=float(data.get("dy", 0.0)),
            theta_rad=float(data.get("theta_rad", 0.0)),
            inlier_ratio=float(data.get("inlier_ratio", 1.0)),
            candidate_count=int(data.get("candidate_count", 0)),
        )


# ---------------------------------------------------------------------------
# Candidate pair 수집
# ---------------------------------------------------------------------------


def _entities_to_pairs(
    entities_a: Dict[str, List[Any]],
    entities_b: Dict[str, List[Any]],
    *,
    search_radius: float,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """양쪽 entity dict에서 candidate (loc_a, loc_b) 페어를 수집.

    entities_a / entities_b 는 ``Dict[entity_type, List[NormalizedEntity]]``
    형식. NormalizedEntity 는 ``.location: (x, y)``, ``.layer: str`` 만
    필요 (duck-typed).

    매칭 룰:
    - 같은 entity_type 안에서만
    - 같은 layer 만 매칭 (cross-layer 매칭 금지 — false pair 회피)
    - search_radius (50mm 권장) 이내 nearest-neighbor
    - 양쪽 모두 1:1 (한 entity는 한 페어에만)

    이 단계에서 일부 noise pair 가 들어와도 RANSAC이 처리하므로 정확도는
    중요하지 않다. recall 만 중요 (충분한 inlier 확보).
    """
    pairs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []

    for entity_type, list_a in entities_a.items():
        list_b = entities_b.get(entity_type)
        if not list_b:
            continue

        # layer 별로 한 번 더 그룹핑
        by_layer_a: Dict[str, List[Any]] = {}
        by_layer_b: Dict[str, List[Any]] = {}
        for e in list_a:
            by_layer_a.setdefault(e.layer, []).append(e)
        for e in list_b:
            by_layer_b.setdefault(e.layer, []).append(e)

        for layer, group_a in by_layer_a.items():
            group_b = by_layer_b.get(layer)
            if not group_b:
                continue

            used_b: set[int] = set()
            radius_sq = search_radius * search_radius

            for entity_a in group_a:
                xa, ya = entity_a.location
                best_idx = -1
                best_dist_sq = radius_sq

                for j, entity_b in enumerate(group_b):
                    if j in used_b:
                        continue
                    xb, yb = entity_b.location
                    dx = xa - xb
                    dy = ya - yb
                    dist_sq = dx * dx + dy * dy
                    if dist_sq <= best_dist_sq:
                        best_dist_sq = dist_sq
                        best_idx = j

                if best_idx >= 0:
                    used_b.add(best_idx)
                    eb = group_b[best_idx]
                    pairs.append(((xa, ya), (eb.location[0], eb.location[1])))

    return pairs


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------


def _estimate_with_cv2(
    pairs: Sequence[Tuple[Tuple[float, float], Tuple[float, float]]],
    *,
    ransac_threshold: float,
) -> Optional[RigidTransform]:
    """cv2.estimateAffinePartial2D — RANSAC, scale 고정 (rigid)."""
    if not _CV2_AVAILABLE or not _NP_AVAILABLE or len(pairs) < 4:
        return None

    pts_b = np.array([[b[0], b[1]] for (_, b) in pairs], dtype=np.float32)
    pts_a = np.array([[a[0], a[1]] for (a, _) in pairs], dtype=np.float32)

    # estimateAffinePartial2D estimates rotation + translation + uniform scale.
    # We later normalise scale ≈ 1 by extracting rotation from the matrix.
    matrix, mask = cv2.estimateAffinePartial2D(
        pts_b, pts_a,
        method=cv2.RANSAC,
        ransacReprojThreshold=ransac_threshold,
        maxIters=2000,
        confidence=0.99,
        refineIters=10,
    )
    if matrix is None or mask is None:
        return None

    # Extract Δx, Δy, θ. cv2 returns a 2×3 affine of form:
    #   [[ s·cosθ, -s·sinθ, dx],
    #    [ s·sinθ,  s·cosθ, dy]]
    a11, a12, dx = float(matrix[0][0]), float(matrix[0][1]), float(matrix[0][2])
    a21, a22, dy = float(matrix[1][0]), float(matrix[1][1]), float(matrix[1][2])
    scale = math.hypot(a11, a21)
    if scale < 1e-9:
        return None
    theta = math.atan2(a21, a11)

    # rigid 가 아닌 scale 변경이 큰 경우는 배제 (도면 비교에선 의도치 않음)
    if abs(scale - 1.0) > 0.01:  # 1% 이상 scale 변화 → 거부
        logger.info(
            "alignment rejected — non-rigid scale (s=%.4f)",
            scale,
        )
        return None

    inliers = int(mask.sum())
    total = len(pairs)
    inlier_ratio = inliers / total if total else 0.0

    return RigidTransform(
        dx=dx,
        dy=dy,
        theta_rad=theta,
        inlier_ratio=inlier_ratio,
        candidate_count=total,
    )


def _estimate_median_shift(
    pairs: Sequence[Tuple[Tuple[float, float], Tuple[float, float]]],
) -> Optional[RigidTransform]:
    """OpenCV 부재 시 fallback — median translation only (no rotation)."""
    if len(pairs) < 4:
        return None

    deltas_x = sorted(a[0] - b[0] for (a, b) in pairs)
    deltas_y = sorted(a[1] - b[1] for (a, b) in pairs)
    n = len(deltas_x)
    median_x = deltas_x[n // 2]
    median_y = deltas_y[n // 2]

    # inlier ratio: |delta - median| < 1mm 비율
    tolerance = 1.0
    inliers = sum(
        1
        for (a, b) in pairs
        if abs((a[0] - b[0]) - median_x) < tolerance
        and abs((a[1] - b[1]) - median_y) < tolerance
    )
    inlier_ratio = inliers / n if n else 0.0

    return RigidTransform(
        dx=median_x,
        dy=median_y,
        theta_rad=0.0,
        inlier_ratio=inlier_ratio,
        candidate_count=n,
    )


class _ShiftedEntity:
    """Lightweight duck-typed entity (location + layer) for re-pairing after a
    coarse shift, without mutating the caller's NormalizedEntity objects."""

    __slots__ = ("location", "layer")

    def __init__(self, location: Tuple[float, float], layer: str) -> None:
        self.location = location
        self.layer = layer


def _bbox_center(entities: Dict[str, List[Any]]) -> Optional[Tuple[float, float]]:
    """Outlier-robust bounding-box centre of all entity locations.

    Uses the 1st/99th percentile per axis as the box corners (rejecting the
    extreme 1 % each end — e.g. a stray entity or extent pollution) and returns
    their midpoint. Unlike an INTERIOR percentile midpoint (density-sensitive,
    shifts when a revision adds/removes entities), the near-corner midpoint
    tracks the drawing EXTENT, which a whole-drawing translation moves rigidly —
    so ``center_A − center_B`` recovers the translation even with asymmetric
    content. Needs ≥8 points.
    """
    xs: List[float] = []
    ys: List[float] = []
    for lst in entities.values():
        for e in lst:
            loc = getattr(e, "location", None)
            if loc is not None and len(loc) >= 2:
                xs.append(float(loc[0]))
                ys.append(float(loc[1]))
    if len(xs) < 8:
        return None
    xs.sort()
    ys.sort()

    def _center(arr: List[float]) -> float:
        lo = arr[max(0, int(len(arr) * 0.01))]
        hi = arr[min(len(arr) - 1, int(len(arr) * 0.99))]
        return (lo + hi) / 2.0

    return (_center(xs), _center(ys))


def estimate_coarse_translation(
    entities_a: Dict[str, List[Any]],
    entities_b: Dict[str, List[Any]],
) -> Optional[Tuple[float, float]]:
    """Coarse (Δx, Δy) translation B → A from the robust bbox-centre difference.

    Recovers a gross re-origin shift (a revision re-inserted at a different
    model-space origin) that the 50 mm nearest-neighbour pairing in
    ``estimate_rigid_transform`` cannot see — every counterpart is thousands of
    mm away, so it finds no pairs and the diff reports "everything added".

    The estimate need not be exact: ``estimate_rigid_transform`` shifts B by it
    and then RANSAC-refines within ``search_radius`` (with a one-step median
    correction first to absorb a moderate centre skew), and the inlier gate
    rejects a bad coarse — so this only has to land close enough to re-pair.
    Returns None when either side lacks enough geometry.
    """
    ca = _bbox_center(entities_a)
    cb = _bbox_center(entities_b)
    if ca is None or cb is None:
        return None
    return (ca[0] - cb[0], ca[1] - cb[1])


def _shift_entities(
    entities: Dict[str, List[Any]], offset: Tuple[float, float]
) -> Dict[str, List[Any]]:
    """Return entities with locations shifted by ``offset`` (B → B+offset)."""
    dx, dy = offset
    out: Dict[str, List[Any]] = {}
    for et, lst in entities.items():
        shifted: List[Any] = []
        for e in lst:
            loc = getattr(e, "location", None)
            if loc is None or len(loc) < 2:
                continue
            shifted.append(_ShiftedEntity((float(loc[0]) + dx, float(loc[1]) + dy), e.layer))
        out[et] = shifted
    return out


def _compose_coarse_fine(
    coarse: Tuple[float, float], fine: RigidTransform
) -> RigidTransform:
    """Compose a coarse translation (applied first to B) with a fine transform
    estimated on the coarse-shifted B. Final maps raw B → A:
        A = R_fine·(B + coarse) + t_fine = R_fine·B + (R_fine·coarse + t_fine)
    so θ = θ_fine and the translation gains R_fine·coarse.
    """
    cx, cy = coarse
    c = math.cos(fine.theta_rad)
    s = math.sin(fine.theta_rad)
    return RigidTransform(
        dx=fine.dx + (c * cx - s * cy),
        dy=fine.dy + (s * cx + c * cy),
        theta_rad=fine.theta_rad,
        inlier_ratio=fine.inlier_ratio,
        candidate_count=fine.candidate_count,
    )


def estimate_rigid_transform(
    entities_a: Dict[str, List[Any]],
    entities_b: Dict[str, List[Any]],
    *,
    search_radius: float = 50.0,
    ransac_threshold: float = 0.5,
    min_inlier_ratio: float = 0.5,
    min_candidate_count: int = 4,
) -> Optional[RigidTransform]:
    """B → A 매핑 rigid transform 을 추정한다.

    Args:
        entities_a / entities_b: ``Dict[entity_type, List[NormalizedEntity]]``
        search_radius: candidate pair 수집 시 nearest-neighbor 반경 (mm).
            너무 작으면 큰 시프트를 놓치고, 너무 크면 noise pair가 많아짐.
            50mm는 일반 도면에서 안전한 기본값.
        ransac_threshold: RANSAC reprojection threshold (mm).
        min_inlier_ratio: 이 미만이면 alignment 신뢰 안 함 → None.
        min_candidate_count: 후보 pair 가 이 미만이면 None.

    Returns:
        ``RigidTransform`` (B → A) 또는 None (정렬 불가/불신뢰/미세).
        ``RigidTransform.is_significant`` 가 False 인 경우도 그대로 반환 —
        호출자가 적용 여부 판단 (None vs insignificant 구분).
    """
    pairs = _entities_to_pairs(entities_a, entities_b, search_radius=search_radius)
    coarse: Optional[Tuple[float, float]] = None
    if len(pairs) < min_candidate_count:
        # Too few in-radius pairs. This is the normal "no shift / too little
        # data" case — UNLESS the whole drawing was re-originated by a large
        # translation, in which case every counterpart sits far beyond
        # search_radius and nothing pairs (→ the diff reports "everything
        # added", and the before viewer shows nothing). Recover the gross
        # offset and retry; the RANSAC refine + inlier gate below still vet the
        # result, so a wrong coarse self-rejects (no worse than today).
        coarse = estimate_coarse_translation(entities_a, entities_b)
        if coarse is None or math.hypot(coarse[0], coarse[1]) <= search_radius:
            logger.debug(
                "alignment skipped — only %d candidate pairs (< %d), no coarse offset",
                len(pairs), min_candidate_count,
            )
            return None
        shifted_b = _shift_entities(entities_b, coarse)
        # One median-shift correction: pair within a generous radius so true
        # counterparts (now within a few hundred mm of their coarse-shifted
        # position when the bbox centre is skewed by asymmetric content) pair up,
        # and take the MEDIAN offset (robust to wrong neighbours). For an exact
        # coarse this is a ~0 no-op; for a skewed one it pulls B within the fine
        # search_radius so the RANSAC below can lock on.
        refine_pairs = _entities_to_pairs(
            entities_a, shifted_b, search_radius=search_radius * 8.0
        )
        if len(refine_pairs) >= min_candidate_count:
            median = _estimate_median_shift(refine_pairs)
            if median is not None and median.translation_magnitude > 1e-6:
                coarse = (coarse[0] + median.dx, coarse[1] + median.dy)
                shifted_b = _shift_entities(entities_b, coarse)
        pairs = _entities_to_pairs(entities_a, shifted_b, search_radius=search_radius)
        if len(pairs) < min_candidate_count:
            logger.info(
                "coarse translation (dx=%.0f dy=%.0f) brought only %d pairs together "
                "(< %d) — distrust, no alignment",
                coarse[0], coarse[1], len(pairs), min_candidate_count,
            )
            return None
        logger.info(
            "coarse re-origin translation detected: dx=%.0fmm dy=%.0fmm — refining",
            coarse[0], coarse[1],
        )

    transform: Optional[RigidTransform]
    if _CV2_AVAILABLE and _NP_AVAILABLE:
        transform = _estimate_with_cv2(pairs, ransac_threshold=ransac_threshold)
    else:
        logger.info("OpenCV unavailable — using median-shift fallback")
        transform = _estimate_median_shift(pairs)

    if transform is None:
        return None

    if coarse is not None:
        # ``transform`` was estimated on the coarse-shifted B; fold the coarse
        # translation back in so the result maps RAW B → A.
        transform = _compose_coarse_fine(coarse, transform)

    if transform.inlier_ratio < min_inlier_ratio:
        logger.info(
            "alignment rejected — low inlier ratio %.2f (< %.2f), candidates=%d",
            transform.inlier_ratio, min_inlier_ratio, transform.candidate_count,
        )
        return None

    logger.info(
        "alignment estimated — dx=%.3fmm dy=%.3fmm theta=%.4f° "
        "(inlier %.0f%% of %d pairs)",
        transform.dx, transform.dy, math.degrees(transform.theta_rad),
        transform.inlier_ratio * 100, transform.candidate_count,
    )
    return transform


__all__ = [
    "RigidTransform",
    "estimate_rigid_transform",
    "estimate_coarse_translation",
]
