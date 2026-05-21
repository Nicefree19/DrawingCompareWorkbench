# -*- coding: utf-8 -*-
"""Phase O4 — zone-level 노이즈 필터 단위 테스트.

build_change_zones() 의 single-entity promote 차단 검증:
- min_changes_per_zone=1 (default) → 모든 group 이 zone (회귀 보호)
- min_changes_per_zone=2 + cosmetic 단일 변경 → zone 미생성
- 구조 layer 단일 변경은 noise_score 낮아 promote 유지
- _compute_zone_noise_score 단위 검증
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from src.services.comparison.base import (
    ChangeRecord,
    ChangeType,
    ComparisonResult,
)
from src.services.comparison.change_zones import (
    ChangeZoneOptions,
    _ChangeEnvelope,
    _compute_zone_noise_score,
    build_change_zones,
)


# ---------------------------------------------------------------------------
# 헬퍼 — 직접 ComparisonResult 빌드 (DXF 파일 없이)
# ---------------------------------------------------------------------------


def _make_change(
    *,
    key: str,
    layer: str = "BEAM",
    entity_type: str = "LINE",
    change_type: ChangeType = ChangeType.MODIFIED,
    x: float = 0.0,
    y: float = 0.0,
    width: float = 100.0,
    height: float = 100.0,
    change_category: Optional[str] = None,
) -> ChangeRecord:
    metadata = {
        "layer": layer,
        "entity_type": entity_type,
        "x": x, "y": y, "w": width, "h": height,
        # bbox metadata for change_record_bbox()
        "bbox": [x, y, x + width, y + height],
    }
    if change_category:
        metadata["change_category"] = change_category
    return ChangeRecord(
        key=key,
        change_type=change_type,
        location=f"({x},{y}) - ({x+width},{y+height})",
        metadata=metadata,
    )


def _make_envelope(
    *,
    bbox: tuple = (0.0, 0.0, 1.0, 1.0),
    layer: str = "BEAM",
    change_category: Optional[str] = None,
) -> _ChangeEnvelope:
    change = _make_change(
        key=f"e{id(bbox)}",
        layer=layer,
        x=bbox[0], y=bbox[1],
        width=bbox[2] - bbox[0],
        height=bbox[3] - bbox[1],
        change_category=change_category,
    )
    return _ChangeEnvelope(index=0, change=change, bbox=bbox, old_bbox=None)


def _result(changes: List[ChangeRecord]) -> ComparisonResult:
    res = ComparisonResult(source_a="a.dxf", source_b="b.dxf")
    for c in changes:
        res.add_change(c)
    return res


# ---------------------------------------------------------------------------
# _compute_zone_noise_score — 가중치 검증
# ---------------------------------------------------------------------------


def test_noise_score_zero_for_structural_member():
    env = _make_envelope(bbox=(0, 0, 200, 200), layer="BEAM_TOP")
    options = ChangeZoneOptions()
    score = _compute_zone_noise_score([env], options)
    # single-entity (0.3) + bbox big enough → no micro (0) + structural → no layer score (0)
    # 결과 0.3 만 — single-entity 신호만
    assert 0.25 < score < 0.35


def test_noise_score_high_for_micro_cosmetic_non_structural():
    env = _make_envelope(
        bbox=(0.0, 0.0, 0.5, 0.5),  # micro
        layer="HATCH_PATTERN",  # non-structural
        change_category="cosmetic",
    )
    options = ChangeZoneOptions()
    score = _compute_zone_noise_score([env], options)
    # single (0.3) + cosmetic (0.3) + micro (0.2) + non-structural (0.2) = 1.0
    assert score == pytest.approx(1.0)


def test_noise_score_lowered_when_multi_entity():
    envs = [
        _make_envelope(bbox=(0, 0, 0.5, 0.5), layer="HATCH",
                       change_category="cosmetic"),
        _make_envelope(bbox=(10, 10, 10.5, 10.5), layer="HATCH",
                       change_category="cosmetic"),
    ]
    score = _compute_zone_noise_score(envs, ChangeZoneOptions())
    # single 가산점 (0.3) 빠짐 → 0.7 (cosmetic 0.3 + micro 0.2 + non-struct 0.2)
    assert score == pytest.approx(0.7)


def test_noise_score_empty_envelopes():
    assert _compute_zone_noise_score([], ChangeZoneOptions()) == 0.0


# ---------------------------------------------------------------------------
# build_change_zones — backward-compat (default min=1)
# ---------------------------------------------------------------------------


def test_build_zones_default_min_promotes_single_entity():
    """min_changes_per_zone=1 (default) — 단일 entity 도 zone 으로 promote."""
    changes = [_make_change(key="c1", x=0, y=0, width=100, height=100)]
    res = _result(changes)

    zones = build_change_zones(res)
    assert len(zones) == 1


# ---------------------------------------------------------------------------
# min_changes_per_zone=2 — single-entity noise 차단
# ---------------------------------------------------------------------------


def test_build_zones_min2_suppresses_cosmetic_single_entity():
    """단일 cosmetic 변경 (non-structural layer) 은 zone 미생성."""
    changes = [
        _make_change(
            key="c1",
            layer="HATCH",
            x=0, y=0, width=0.5, height=0.5,
            change_category="cosmetic",
        )
    ]
    res = _result(changes)

    options = ChangeZoneOptions(min_changes_per_zone=2)
    zones = build_change_zones(res, options=options)
    assert zones == []
    assert res.metadata.get("change_zone_noise_suppressed_count") == 1


def test_build_zones_min2_keeps_structural_single_entity():
    """구조 변경 (BEAM layer) 은 단일이라도 noise_score 낮아 보존."""
    changes = [
        _make_change(
            key="c1",
            layer="BEAM",
            x=0, y=0, width=200, height=200,  # not micro
            change_category="position",  # not cosmetic
        )
    ]
    res = _result(changes)

    options = ChangeZoneOptions(min_changes_per_zone=2)
    zones = build_change_zones(res, options=options)
    # noise_score 가 0.3 (single-entity) 만 → 0.7 미만 → promote 됨
    assert len(zones) == 1
    assert "noise_score" in zones[0].metadata


def test_build_zones_min2_keeps_multi_entity_cluster():
    """cluster (2+ entity) 는 noise_score 와 무관하게 promote (min 만족)."""
    changes = [
        _make_change(
            key=f"c{i}",
            layer="HATCH",
            x=i * 10, y=0, width=0.5, height=0.5,
            change_category="cosmetic",
        )
        for i in range(3)
    ]
    res = _result(changes)

    options = ChangeZoneOptions(
        min_changes_per_zone=2,
        cluster_distance=50.0,  # 가까이 묶이도록
    )
    zones = build_change_zones(res, options=options)
    # 3개가 한 cluster → group size 3 ≥ 2 → promote
    assert len(zones) == 1
    assert zones[0].raw_change_count == 3


def test_build_zones_metadata_records_noise_score():
    """promote 된 zone 의 metadata 에 noise_score 기록."""
    changes = [_make_change(key="c1", layer="BEAM", x=0, y=0, width=100, height=100)]
    res = _result(changes)

    zones = build_change_zones(res)
    assert len(zones) == 1
    assert "noise_score" in zones[0].metadata
    assert isinstance(zones[0].metadata["noise_score"], float)


# ---------------------------------------------------------------------------
# 통합 시나리오
# ---------------------------------------------------------------------------


def test_mixed_zones_partial_suppression():
    """3개 변경 중 2개는 cosmetic noise (suppress), 1개는 구조 (keep)."""
    changes = [
        # noise 1
        _make_change(
            key="n1", layer="HATCH",
            x=0, y=0, width=0.5, height=0.5,
            change_category="cosmetic",
        ),
        # noise 2 (서로 멀리 떨어져 다른 cluster)
        _make_change(
            key="n2", layer="HATCH",
            x=5000, y=5000, width=0.5, height=0.5,
            change_category="cosmetic",
        ),
        # structural single
        _make_change(
            key="s1", layer="BEAM",
            x=10000, y=10000, width=300, height=300,
        ),
    ]
    res = _result(changes)

    options = ChangeZoneOptions(min_changes_per_zone=2)
    zones = build_change_zones(res, options=options)

    # 2개 noise zone suppressed, 1개 structural promoted
    assert len(zones) == 1
    assert res.metadata.get("change_zone_noise_suppressed_count") == 2
    # 남은 zone 은 BEAM layer
    assert "BEAM" in zones[0].layers
