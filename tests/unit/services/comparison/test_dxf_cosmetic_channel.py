# -*- coding: utf-8 -*-
"""cosmetic 변경 채널 단위 테스트 (Phase O3).

좌표가 동일한 entity 페어에서 color/lineweight/linetype 차이만 있는
경우 ``DxfChange(MODIFIED, change_category="cosmetic")`` 으로 기록되고,
``suppress_cosmetic_only`` 옵션으로 결과에서 제외 가능한지 검증.
"""

from __future__ import annotations

from typing import Dict, List

import pytest

from src.services.comparison.comparison_config import (
    ComparisonConfig,
    SensitivityConfig,
)
from src.services.comparison.dxf_comparator import (
    DxfChange,
    DxfChangeType,
    DxfComparator,
)
from src.services.comparison.dxf_entity_extractor import NormalizedEntity


# ---------------------------------------------------------------------------
# 헬퍼 — 직접 NormalizedEntity 생성 (DXF 파일 없이)
# ---------------------------------------------------------------------------


def _entity(
    *,
    hash_str: str,
    layer: str = "BEAM",
    location=(0.0, 0.0),
    color: int = 7,
    lineweight: int = -1,
    linetype: str = "Continuous",
    entity_type: str = "LINE",
) -> NormalizedEntity:
    return NormalizedEntity(
        hash=hash_str,
        entity_type=entity_type,
        layer=layer,
        data={"start": location, "end": location},
        location=location,
        color=color,
        lineweight=lineweight,
        linetype=linetype,
    )


def _entities_dict(entities: List[NormalizedEntity]) -> Dict[str, List[NormalizedEntity]]:
    """{entity_type: [entities]} 로 그룹핑."""
    out: Dict[str, List[NormalizedEntity]] = {}
    for e in entities:
        out.setdefault(e.entity_type, []).append(e)
    return out


def _comparator(*, suppress_cosmetic: bool = False, detect_cosmetic: bool = True) -> DxfComparator:
    sens = SensitivityConfig(
        cosmetic_detection_enabled=detect_cosmetic,
        suppress_cosmetic_only=suppress_cosmetic,
    )
    config = ComparisonConfig(sensitivity=sens)
    return DxfComparator(config=config)


# ---------------------------------------------------------------------------
# 시나리오 1 — color-only / lineweight-only / linetype-only
# ---------------------------------------------------------------------------


def test_color_only_change_detected_as_cosmetic():
    a = _entity(hash_str="h1", color=7)  # white
    b = _entity(hash_str="h1", color=3)  # green

    result = _comparator().compare(_entities_dict([a]), _entities_dict([b]))

    cosmetic = [c for c in result.changes if c.change_category == "cosmetic"]
    assert len(cosmetic) == 1
    assert cosmetic[0].change_type == DxfChangeType.MODIFIED
    assert "color" in cosmetic[0].change_detail


def test_lineweight_only_change_detected_as_cosmetic():
    a = _entity(hash_str="h1", lineweight=30)  # 0.30mm
    b = _entity(hash_str="h1", lineweight=50)  # 0.50mm

    result = _comparator().compare(_entities_dict([a]), _entities_dict([b]))
    cosmetic = [c for c in result.changes if c.change_category == "cosmetic"]
    assert len(cosmetic) == 1
    assert "lineweight" in cosmetic[0].change_detail


def test_linetype_only_change_detected_as_cosmetic():
    a = _entity(hash_str="h1", linetype="Continuous")
    b = _entity(hash_str="h1", linetype="DASHED")

    result = _comparator().compare(_entities_dict([a]), _entities_dict([b]))
    cosmetic = [c for c in result.changes if c.change_category == "cosmetic"]
    assert len(cosmetic) == 1
    assert "linetype" in cosmetic[0].change_detail


def test_multiple_cosmetic_attrs_combined_in_one_change():
    """color + lineweight 둘 다 변경 → 한 DxfChange 에 모두 기록."""
    a = _entity(hash_str="h1", color=7, lineweight=30)
    b = _entity(hash_str="h1", color=3, lineweight=50)

    result = _comparator().compare(_entities_dict([a]), _entities_dict([b]))
    cosmetic = [c for c in result.changes if c.change_category == "cosmetic"]
    assert len(cosmetic) == 1
    assert "color" in cosmetic[0].change_detail
    assert "lineweight" in cosmetic[0].change_detail


# ---------------------------------------------------------------------------
# 시나리오 2 — suppress_cosmetic_only=True
# ---------------------------------------------------------------------------


def test_suppress_cosmetic_only_filters_changes():
    a = _entity(hash_str="h1", color=7)
    b = _entity(hash_str="h1", color=3)

    result = _comparator(suppress_cosmetic=True).compare(
        _entities_dict([a]), _entities_dict([b])
    )

    # cosmetic 변경이 결과에서 제외됨
    assert all(c.change_category != "cosmetic" for c in result.changes)
    # 통계에 카운터 기록
    assert result.stats.get("cosmetic_suppressed") == 1
    assert result.metadata.get("cosmetic_suppressed_count") == 1


def test_suppress_does_not_affect_non_cosmetic_changes():
    """suppress=True 여도 좌표 차이 같은 일반 변경은 보존."""
    a = _entity(hash_str="h_old", color=7, location=(0.0, 0.0))
    b = _entity(hash_str="h_new", color=3, location=(10.0, 10.0))  # 다른 hash

    result = _comparator(suppress_cosmetic=True).compare(
        _entities_dict([a]), _entities_dict([b])
    )

    # 좌표 다름 → ADDED + DELETED 1쌍
    types = [c.change_type for c in result.changes]
    assert DxfChangeType.ADDED in types
    assert DxfChangeType.DELETED in types


# ---------------------------------------------------------------------------
# 시나리오 3 — 좌표가 다르면 cosmetic 채널 사용 안 함
# ---------------------------------------------------------------------------


def test_coordinate_change_not_categorized_as_cosmetic():
    """hash 가 다른 entity (좌표 다름) 페어는 cosmetic 으로 분류되지 않음 —
    그냥 ADDED + DELETED."""
    a = _entity(hash_str="h_a", color=7)
    b = _entity(hash_str="h_b", color=3)  # 다른 hash → 다른 entity

    result = _comparator().compare(_entities_dict([a]), _entities_dict([b]))

    cosmetic = [c for c in result.changes if c.change_category == "cosmetic"]
    assert cosmetic == []


# ---------------------------------------------------------------------------
# 시나리오 4 — backward-compat (기존 NormalizedEntity 필드 None)
# ---------------------------------------------------------------------------


def test_normalized_entity_default_fields_are_none():
    e = NormalizedEntity(
        hash="h", entity_type="LINE", layer="BEAM", data={}, location=(0.0, 0.0),
    )
    # Phase O3 신규 필드는 default None
    assert e.color is None
    assert e.lineweight is None
    assert e.linetype is None


def test_both_entities_with_none_cosmetic_no_change():
    """양쪽 모두 cosmetic 미설정 (None) → 차이 아님 → cosmetic 변경 없음."""
    a = NormalizedEntity(
        hash="h", entity_type="LINE", layer="BEAM", data={}, location=(0.0, 0.0),
    )
    b = NormalizedEntity(
        hash="h", entity_type="LINE", layer="BEAM", data={}, location=(0.0, 0.0),
    )

    result = _comparator().compare(_entities_dict([a]), _entities_dict([b]))
    assert result.changes == []


def test_one_side_none_other_set_treated_as_change():
    """한쪽만 cosmetic 정보 있음 → 정보 있는 쪽이 변경 — cosmetic 으로 분류."""
    a = NormalizedEntity(
        hash="h", entity_type="LINE", layer="BEAM", data={}, location=(0.0, 0.0),
        color=None,
    )
    b = NormalizedEntity(
        hash="h", entity_type="LINE", layer="BEAM", data={}, location=(0.0, 0.0),
        color=3,
    )

    result = _comparator().compare(_entities_dict([a]), _entities_dict([b]))
    cosmetic = [c for c in result.changes if c.change_category == "cosmetic"]
    assert len(cosmetic) == 1


# ---------------------------------------------------------------------------
# 시나리오 5 — cosmetic_attributes 부분 화이트리스트
# ---------------------------------------------------------------------------


def test_cosmetic_attributes_subset_only_color():
    """``cosmetic_attributes=("color",)`` 면 lineweight 변경은 무시."""
    sens = SensitivityConfig(cosmetic_attributes=("color",))
    config = ComparisonConfig(sensitivity=sens)
    comparator = DxfComparator(config=config)

    # color 동일, lineweight 만 변경
    a = _entity(hash_str="h1", color=7, lineweight=30)
    b = _entity(hash_str="h1", color=7, lineweight=50)

    result = comparator.compare(_entities_dict([a]), _entities_dict([b]))
    cosmetic = [c for c in result.changes if c.change_category == "cosmetic"]
    # lineweight 만 변경됐는데 화이트리스트엔 color 만 → 무시
    assert cosmetic == []


# ---------------------------------------------------------------------------
# 시나리오 6 — cosmetic_detection_enabled=False
# ---------------------------------------------------------------------------


def test_cosmetic_detection_disabled():
    """detection_enabled=False 면 cosmetic 변경 자체를 만들지 않음."""
    a = _entity(hash_str="h1", color=7)
    b = _entity(hash_str="h1", color=3)

    result = _comparator(detect_cosmetic=False).compare(
        _entities_dict([a]), _entities_dict([b])
    )
    cosmetic = [c for c in result.changes if c.change_category == "cosmetic"]
    assert cosmetic == []


# ---------------------------------------------------------------------------
# 시나리오 7 — to_dict round-trip
# ---------------------------------------------------------------------------


def test_sensitivity_config_to_dict_includes_cosmetic_fields():
    sens = SensitivityConfig(
        cosmetic_detection_enabled=False,
        suppress_cosmetic_only=True,
        cosmetic_attributes=("color", "linetype"),
    )
    d = sens.to_dict()
    assert d["cosmetic_detection_enabled"] is False
    assert d["suppress_cosmetic_only"] is True
    assert d["cosmetic_attributes"] == ["color", "linetype"]


def test_sensitivity_config_from_dict_round_trip():
    original = SensitivityConfig(
        cosmetic_detection_enabled=False,
        suppress_cosmetic_only=True,
        cosmetic_attributes=("color",),
    )
    restored = SensitivityConfig.from_dict(original.to_dict())
    assert restored.cosmetic_detection_enabled is False
    assert restored.suppress_cosmetic_only is True
    assert restored.cosmetic_attributes == ("color",)
