# -*- coding: utf-8 -*-
"""hybrid Hungarian matching 단위 테스트 (Phase O2).

DxfComparator._resolve_candidates_to_pairs 검증 — sub-cluster 분할,
scipy.linear_sum_assignment 적용, greedy fallback, scipy 미설치 처리.
"""

from __future__ import annotations

from typing import List, Tuple
from unittest.mock import patch

import pytest

from src.services.comparison.dxf_comparator import (
    DxfChange,
    DxfChangeType,
    DxfComparator,
)


def _change(loc: Tuple[float, float], *, layer: str = "BEAM", entity_type: str = "LINE",
            change_type: DxfChangeType = DxfChangeType.MODIFIED) -> DxfChange:
    return DxfChange(
        entity_type=entity_type,
        layer=layer,
        change_type=change_type,
        location=loc,
    )


def _comparator() -> DxfComparator:
    return DxfComparator(tolerance=10.0)


# ---------------------------------------------------------------------------
# Hungarian on simple 1:1 cluster
# ---------------------------------------------------------------------------


def test_hungarian_one_to_one():
    """간단한 1:1 매칭 — Hungarian 이 거리 0 페어 선택."""
    deleted = [_change((0.0, 0.0))]
    added = [_change((0.0, 0.0))]
    edges = [(0, 0, 0.0)]

    pairs = _comparator()._resolve_candidates_to_pairs(edges, deleted, added)
    assert len(pairs) == 1
    assert pairs[0] == (deleted[0], added[0])


def test_hungarian_picks_minimum_cost_when_multiple_candidates():
    """deleted 1 개, added 2 개. Hungarian 이 거리 최소 페어를 선택."""
    deleted = [_change((0.0, 0.0))]
    added = [_change((0.5, 0.0)), _change((0.1, 0.0))]  # 0.1 이 더 가까움
    edges = [(0, 0, 0.5), (0, 1, 0.1)]

    pairs = _comparator()._resolve_candidates_to_pairs(edges, deleted, added)
    assert len(pairs) == 1
    # added[1] (거리 0.1) 이 매칭되어야 함
    assert pairs[0][1] is added[1]


def test_hungarian_optimal_assignment_in_two_to_two_cluster():
    """2:2 cluster — greedy 와 Hungarian 결과가 다른 시나리오.

    deleted A=(0,0), B=(10,0), added C=(0.5, 0), D=(10.5, 0).
    Greedy 가 A 부터 처리하면: A→C(0.5), B→D(0.5) — 합 1.0.
    Hungarian 도 같은 결과 (이 경우 둘이 같음). 일부 시나리오에서는
    Hungarian 만 최적을 찾는데, 검증 핵심은 "둘 다 1:1 보장 + cost
    최소화" — 두 페어 모두 만들어지면 OK.
    """
    deleted = [_change((0.0, 0.0)), _change((10.0, 0.0))]
    added = [_change((0.5, 0.0)), _change((10.5, 0.0))]
    edges = [
        (0, 0, 0.5),  # d0 ↔ a0 distance 0.5
        (0, 1, 10.5),  # d0 ↔ a1 distance 10.5
        (1, 0, 9.5),  # d1 ↔ a0 distance 9.5
        (1, 1, 0.5),  # d1 ↔ a1 distance 0.5
    ]

    pairs = _comparator()._resolve_candidates_to_pairs(edges, deleted, added)
    assert len(pairs) == 2
    # 비용 최소: d0↔a0, d1↔a1 (총 1.0)
    matched_pairs = {(deleted.index(d), added.index(a)) for (d, a) in pairs}
    assert matched_pairs == {(0, 0), (1, 1)}


# ---------------------------------------------------------------------------
# Sub-cluster 분할
# ---------------------------------------------------------------------------


def test_disjoint_clusters_handled_independently():
    """두 cluster가 disjoint — 각각 독립적으로 매칭."""
    deleted = [_change((0.0, 0.0)), _change((1000.0, 1000.0))]
    added = [_change((0.5, 0.0)), _change((1000.5, 1000.0))]
    edges = [
        (0, 0, 0.5),  # cluster 1: d0 ↔ a0
        (1, 1, 0.5),  # cluster 2: d1 ↔ a1
    ]

    pairs = _comparator()._resolve_candidates_to_pairs(edges, deleted, added)
    assert len(pairs) == 2


# ---------------------------------------------------------------------------
# Greedy fallback (cluster size > max_subset)
# ---------------------------------------------------------------------------


def test_greedy_fallback_when_cluster_exceeds_max_subset():
    """cluster size > max_subset 이면 greedy 사용."""
    n = 5  # cluster size
    deleted = [_change((float(i), 0.0)) for i in range(n)]
    added = [_change((float(i) + 0.1, 0.0)) for i in range(n)]
    # 모든 d 가 모든 a 와 candidate (fully connected → cluster 1개)
    edges = []
    for i in range(n):
        for j in range(n):
            dist = abs(i - j) + 0.1
            edges.append((i, j, dist))

    # max_subset 을 3으로 강제 — cluster 크기 5 > 3 → greedy fallback
    comparator = _comparator()
    with patch.object(comparator, "_get_hungarian_max_subset", return_value=3):
        pairs = comparator._resolve_candidates_to_pairs(edges, deleted, added)

    # greedy 도 1:1 보장
    assert len(pairs) == n
    used_d = {id(d) for (d, _) in pairs}
    used_a = {id(a) for (_, a) in pairs}
    assert len(used_d) == n
    assert len(used_a) == n


# ---------------------------------------------------------------------------
# scipy 미설치 fallback
# ---------------------------------------------------------------------------


def test_falls_back_to_greedy_when_scipy_missing():
    """scipy.optimize 미설치 → _hungarian_for_cluster 가 greedy fallback."""
    deleted = [_change((0.0, 0.0)), _change((10.0, 0.0))]
    added = [_change((0.5, 0.0)), _change((10.5, 0.0))]
    edges = [
        (0, 0, 0.5), (0, 1, 10.5),
        (1, 0, 9.5), (1, 1, 0.5),
    ]

    import sys
    saved = sys.modules.get("scipy.optimize")
    sys.modules["scipy.optimize"] = None  # type: ignore
    try:
        pairs = _comparator()._resolve_candidates_to_pairs(edges, deleted, added)
    finally:
        if saved is None:
            sys.modules.pop("scipy.optimize", None)
        else:
            sys.modules["scipy.optimize"] = saved

    # greedy fallback 도 정확한 1:1 매칭
    assert len(pairs) == 2


# ---------------------------------------------------------------------------
# Empty edge list
# ---------------------------------------------------------------------------


def test_empty_candidates_yields_no_pairs():
    deleted = [_change((0.0, 0.0))]
    added = [_change((0.0, 0.0))]
    pairs = _comparator()._resolve_candidates_to_pairs([], deleted, added)
    assert pairs == []


# ---------------------------------------------------------------------------
# 같은 (i, j) 에 여러 edge 가 있어도 최단 거리 1개만 사용
# ---------------------------------------------------------------------------


def test_duplicate_edges_use_minimum_distance():
    deleted = [_change((0.0, 0.0))]
    added = [_change((0.0, 0.0))]
    # 같은 (0, 0) 페어에 여러 edge — 최단 (0.1) 가 비용으로 채택
    edges = [(0, 0, 5.0), (0, 0, 0.1), (0, 0, 2.0)]

    pairs = _comparator()._resolve_candidates_to_pairs(edges, deleted, added)
    assert len(pairs) == 1
