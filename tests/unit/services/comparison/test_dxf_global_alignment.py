# -*- coding: utf-8 -*-
"""global_alignment 단위 테스트 (Phase O2).

cv2 RANSAC + median fallback 양쪽 검증, 그리고 RigidTransform.apply
/inverse round-trip 검증.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple
from unittest.mock import patch

import pytest

from src.services.comparison.global_alignment import (
    RigidTransform,
    _entities_to_pairs,
    _estimate_median_shift,
    apply_to_changes,
    estimate_rigid_transform,
)


# ---------------------------------------------------------------------------
# 헬퍼 — toy NormalizedEntity / DxfChange
# ---------------------------------------------------------------------------


@dataclass
class _Entity:
    layer: str
    location: Tuple[float, float]


@dataclass
class _Change:
    location: Tuple[float, float]


def _build_entities(
    locations: List[Tuple[float, float]],
    *,
    entity_type: str = "LINE",
    layer: str = "BEAM",
) -> Dict[str, List[_Entity]]:
    return {entity_type: [_Entity(layer=layer, location=loc) for loc in locations]}


def _shift(
    entities: Dict[str, List[_Entity]],
    dx: float,
    dy: float,
) -> Dict[str, List[_Entity]]:
    out: Dict[str, List[_Entity]] = {}
    for et, lst in entities.items():
        out[et] = [_Entity(e.layer, (e.location[0] + dx, e.location[1] + dy)) for e in lst]
    return out


# ---------------------------------------------------------------------------
# RigidTransform 기본 동작
# ---------------------------------------------------------------------------


def test_rigid_transform_apply_translation_only():
    t = RigidTransform(dx=10.0, dy=-5.0, theta_rad=0.0)
    assert t.apply(0.0, 0.0) == (10.0, -5.0)
    assert t.apply(100.0, 200.0) == (110.0, 195.0)
    assert t.is_translation_only is True


def test_rigid_transform_apply_with_rotation():
    # 90° 회전: (1, 0) → (0, 1)
    t = RigidTransform(dx=0.0, dy=0.0, theta_rad=math.pi / 2)
    x, y = t.apply(1.0, 0.0)
    assert x == pytest.approx(0.0, abs=1e-9)
    assert y == pytest.approx(1.0, abs=1e-9)


def test_rigid_transform_inverse_roundtrip_translation():
    t = RigidTransform(dx=2.5, dy=-3.0, theta_rad=0.0)
    inv = t.inverse()
    # apply then inverse → identity
    x, y = inv.apply(*t.apply(7.0, 11.0))
    assert x == pytest.approx(7.0, abs=1e-9)
    assert y == pytest.approx(11.0, abs=1e-9)


def test_rigid_transform_inverse_roundtrip_with_rotation():
    t = RigidTransform(dx=2.5, dy=-3.0, theta_rad=math.radians(15))
    inv = t.inverse()
    x, y = inv.apply(*t.apply(7.0, 11.0))
    assert x == pytest.approx(7.0, abs=1e-6)
    assert y == pytest.approx(11.0, abs=1e-6)


def test_is_significant_threshold():
    assert RigidTransform(0.0, 0.0, 0.0).is_significant is False
    assert RigidTransform(0.04, 0.0, 0.0).is_significant is False  # < 0.05mm
    assert RigidTransform(0.06, 0.0, 0.0).is_significant is True  # > 0.05mm
    assert RigidTransform(0.0, 0.0, math.radians(0.005)).is_significant is False
    assert RigidTransform(0.0, 0.0, math.radians(0.02)).is_significant is True


def test_translation_magnitude():
    t = RigidTransform(dx=3.0, dy=4.0, theta_rad=0.0)
    assert t.translation_magnitude == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Candidate pair 수집
# ---------------------------------------------------------------------------


def test_entities_to_pairs_basic_nn():
    a = _build_entities([(0, 0), (10, 10), (20, 20)])
    b = _build_entities([(1, 1), (11, 11), (21, 21)])  # 모두 (+1, +1) shift
    pairs = _entities_to_pairs(a, b, search_radius=5.0)
    assert len(pairs) == 3
    for (loc_a, loc_b) in pairs:
        assert loc_b[0] - loc_a[0] == pytest.approx(1.0)
        assert loc_b[1] - loc_a[1] == pytest.approx(1.0)


def test_entities_to_pairs_respects_layer():
    a = {"LINE": [_Entity("BEAM", (0, 0)), _Entity("GRID", (0, 0))]}
    b = {"LINE": [_Entity("BEAM", (1, 1)), _Entity("GRID", (1, 1))]}
    pairs = _entities_to_pairs(a, b, search_radius=5.0)
    # 같은 layer 끼리만 매칭 — 2 페어
    assert len(pairs) == 2


def test_entities_to_pairs_skips_when_far():
    a = _build_entities([(0, 0)])
    b = _build_entities([(100, 100)])  # 141mm > 50mm 반경
    pairs = _entities_to_pairs(a, b, search_radius=50.0)
    assert pairs == []


# ---------------------------------------------------------------------------
# Estimation — median fallback (cv2 없이 검증)
# ---------------------------------------------------------------------------


def test_median_fallback_pure_translation():
    pairs = [
        ((0.0, 0.0), (-0.5, -0.5)),
        ((10.0, 0.0), (9.5, -0.5)),
        ((0.0, 10.0), (-0.5, 9.5)),
        ((10.0, 10.0), (9.5, 9.5)),
    ]
    transform = _estimate_median_shift(pairs)
    assert transform is not None
    # B → A 방향: A - B = (+0.5, +0.5)
    assert transform.dx == pytest.approx(0.5)
    assert transform.dy == pytest.approx(0.5)
    assert transform.theta_rad == 0.0
    assert transform.inlier_ratio == 1.0


def test_median_fallback_too_few_pairs():
    pairs = [((0.0, 0.0), (1.0, 1.0))] * 3  # 4건 미만
    assert _estimate_median_shift(pairs) is None


# ---------------------------------------------------------------------------
# Estimation — cv2 RANSAC (cv2 가 있을 때만)
# ---------------------------------------------------------------------------


def test_estimate_with_cv2_global_translation():
    """모든 entity 가 (+0.5, +0.5) 시프트된 경우 alignment B→A = (-0.5, -0.5)."""
    a = _build_entities([(x * 100, y * 100) for x in range(3) for y in range(3)])
    b = _shift(a, 0.5, 0.5)

    transform = estimate_rigid_transform(a, b, search_radius=10.0)
    assert transform is not None
    assert transform.dx == pytest.approx(-0.5, abs=0.01)
    assert transform.dy == pytest.approx(-0.5, abs=0.01)
    assert transform.is_significant is True


def test_estimate_rejects_when_only_one_outlier():
    """5/6 entity 는 안 움직였고 1개만 5mm 시프트 — alignment 는 ≈0 (no significant)."""
    a = _build_entities([(0, 0), (100, 0), (200, 0), (0, 100), (100, 100), (200, 100)])
    # entity 5개는 그대로, 1개만 (+0, +5) 시프트
    b_locs = [(0, 0), (100, 0), (200, 0), (0, 100), (100, 100), (200, 105)]
    b = _build_entities(b_locs)

    transform = estimate_rigid_transform(a, b, search_radius=10.0)
    # 가짜 outlier 시프트 흡수 안 해야 함 — alignment 가 ≈(0,0) 이므로
    # is_significant=False 또는 None 반환
    if transform is not None:
        assert transform.translation_magnitude < 0.5  # 1개 outlier 의 영향 미미


def test_estimate_returns_none_for_insufficient_pairs():
    a = _build_entities([(0, 0)])
    b = _build_entities([(0, 0)])
    assert estimate_rigid_transform(a, b) is None


def test_estimate_returns_none_when_layers_disjoint():
    a = {"LINE": [_Entity("BEAM", (i * 10.0, 0.0)) for i in range(10)]}
    b = {"LINE": [_Entity("GRID", (i * 10.0 + 0.5, 0.0)) for i in range(10)]}
    # 같은 layer 페어 없음 — candidate 0 → None
    assert estimate_rigid_transform(a, b) is None


# ---------------------------------------------------------------------------
# Median fallback 강제 (cv2 mock)
# ---------------------------------------------------------------------------


def test_estimate_falls_back_to_median_when_cv2_missing():
    a = _build_entities([(0, 0), (10, 0), (20, 0), (30, 0)])
    b = _shift(a, 0.7, 0.0)

    with patch("src.services.comparison.global_alignment._CV2_AVAILABLE", False):
        transform = estimate_rigid_transform(a, b, search_radius=10.0)

    assert transform is not None
    # B→A: -0.7
    assert transform.dx == pytest.approx(-0.7, abs=0.01)
    assert transform.dy == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# apply_to_changes — DxfChange-like 객체 in-place 변환
# ---------------------------------------------------------------------------


def test_apply_to_changes_inplace():
    changes = [_Change(location=(10.0, 10.0)), _Change(location=(20.0, 20.0))]
    transform = RigidTransform(dx=1.0, dy=-1.0, theta_rad=0.0)
    apply_to_changes(changes, transform)
    assert changes[0].location == (11.0, 9.0)
    assert changes[1].location == (21.0, 19.0)


def test_apply_to_changes_skips_none_location():
    changes = [_Change(location=None), _Change(location=(10.0, 10.0))]
    apply_to_changes(changes, RigidTransform(1.0, 1.0, 0.0))
    assert changes[0].location is None
    assert changes[1].location == (11.0, 11.0)
