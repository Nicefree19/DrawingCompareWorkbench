# -*- coding: utf-8 -*-
"""DxfComparator Priority Score 통합 테스트

Phase 3+ 확장: Priority Score가 DxfComparator에 올바르게 통합되었는지 검증
"""

import pytest
from typing import Dict, List

from src.services.comparison.dxf_comparator import (
    DxfComparator,
    DxfChange,
    DxfChangeType,
    DxfComparisonResult,
)
from src.services.comparison.dxf_entity_extractor import NormalizedEntity
from src.services.comparison.priority_score import (
    PriorityLevel,
    PriorityScore,
    ReviewReason,
)


# 테스트용 Mock 엔티티 클래스
class MockEntity:
    """NormalizedEntity Mock"""

    def __init__(
        self,
        entity_type: str,
        layer: str,
        location: tuple = (0.0, 0.0),
        data: dict = None,
        hash_value: str = None,
    ):
        self.entity_type = entity_type
        self.layer = layer
        self.location = location
        self.data = data or {}
        self.hash = hash_value or f"{entity_type}_{layer}_{location[0]}_{location[1]}"


def create_entity(
    entity_type: str,
    layer: str,
    location: tuple = (0.0, 0.0),
    data: dict = None,
    hash_value: str = None,
) -> NormalizedEntity:
    """NormalizedEntity Mock 생성"""
    return MockEntity(
        entity_type=entity_type,
        layer=layer,
        location=location,
        data=data,
        hash_value=hash_value,
    )


class TestPriorityScoreIntegration:
    """DxfComparator에서 Priority Score 통합 테스트"""

    def test_compare_adds_priority_score_to_changes(self):
        """compare() 결과의 각 DxfChange에 priority_score가 설정되는지 확인"""
        comparator = DxfComparator()

        entities_a = {
            "LINE": [
                create_entity("LINE", "S-BEAM", (0, 0), hash_value="line1"),
            ]
        }
        entities_b = {
            "LINE": [
                create_entity("LINE", "S-BEAM", (100, 100), hash_value="line2"),
            ]
        }

        result = comparator.compare(entities_a, entities_b)

        # 변경 사항이 있어야 함
        assert len(result.changes) >= 1

        # 모든 변경 사항에 priority_score가 설정되어야 함
        for change in result.changes:
            assert change.priority_score is not None
            assert isinstance(change.priority_score, PriorityScore)

    def test_structural_layer_gets_critical_priority(self):
        """구조 레이어 변경은 CRITICAL 우선순위를 받는지 확인"""
        comparator = DxfComparator()

        # 구조 레이어 추가 시뮬레이션
        entities_a = {"LINE": []}
        entities_b = {
            "LINE": [
                create_entity("LINE", "S-BEAM", (0, 0), hash_value="beam1"),
            ]
        }

        result = comparator.compare(entities_a, entities_b)

        assert len(result.changes) == 1
        change = result.changes[0]

        # 구조 레이어는 CRITICAL
        assert change.priority_score.priority_level == PriorityLevel.CRITICAL
        assert change.priority_score.review_needed is True

    def test_dimension_layer_gets_high_priority(self):
        """치수 레이어 변경은 HIGH 우선순위를 받는지 확인"""
        comparator = DxfComparator()

        entities_a = {"DIMENSION": []}
        entities_b = {
            "DIMENSION": [
                create_entity("DIMENSION", "DIM", (0, 0), hash_value="dim1"),
            ]
        }

        result = comparator.compare(entities_a, entities_b)

        assert len(result.changes) == 1
        change = result.changes[0]

        # 치수 레이어는 HIGH
        assert change.priority_score.priority_level == PriorityLevel.HIGH

    def test_annotation_layer_gets_medium_priority(self):
        """주석 레이어 변경은 MEDIUM 우선순위를 받는지 확인"""
        comparator = DxfComparator()

        entities_a = {"TEXT": []}
        entities_b = {
            "TEXT": [
                create_entity("TEXT", "TEXT-LAYER", (0, 0), hash_value="text1"),
            ]
        }

        result = comparator.compare(entities_a, entities_b)

        assert len(result.changes) == 1
        change = result.changes[0]

        # 주석 레이어는 MEDIUM
        assert change.priority_score.priority_level == PriorityLevel.MEDIUM

    def test_defpoints_layer_gets_trivial_priority(self):
        """DEFPOINTS 레이어 변경은 TRIVIAL 우선순위를 받는지 확인 (무시되지 않은 경우)"""
        # 기본적으로 DEFPOINTS는 무시됨, 하지만 priority 계산 테스트를 위해
        # ignore_layers를 비워서 테스트
        comparator = DxfComparator(ignore_layers=[])

        # DEFPOINTS 레이어로 직접 변경 생성
        from src.services.comparison.priority_calculator import get_default_calculator
        calculator = get_default_calculator()

        score = calculator.calculate("ADDED", "DEFPOINTS")

        # DEFPOINTS는 TRIVIAL
        assert score.priority_level == PriorityLevel.TRIVIAL


class TestResultSortByPriority:
    """DxfComparisonResult의 우선순위 정렬 테스트"""

    def test_sort_by_priority_highest_first(self):
        """sort_by_priority()가 높은 우선순위를 먼저 정렬하는지 확인"""
        comparator = DxfComparator()

        # 다양한 레이어에 변경 추가
        entities_a = {"LINE": []}
        entities_b = {
            "LINE": [
                create_entity("LINE", "TEXT-NOTE", (0, 0), hash_value="text1"),  # MEDIUM
                create_entity("LINE", "S-BEAM", (10, 10), hash_value="beam1"),  # CRITICAL
                create_entity("LINE", "DIM", (20, 20), hash_value="dim1"),  # HIGH
            ]
        }

        result = comparator.compare(entities_a, entities_b)
        sorted_changes = result.sort_by_priority()

        # CRITICAL이 먼저, HIGH 다음, MEDIUM 마지막
        assert sorted_changes[0].priority_score.priority_level == PriorityLevel.CRITICAL
        assert sorted_changes[1].priority_score.priority_level == PriorityLevel.HIGH
        assert sorted_changes[2].priority_score.priority_level == PriorityLevel.MEDIUM

    def test_get_top_changes_returns_limited_count(self):
        """get_top_changes()가 지정된 개수만 반환하는지 확인"""
        comparator = DxfComparator()

        entities_a = {"LINE": []}
        entities_b = {
            "LINE": [
                create_entity("LINE", f"LAYER-{i}", (i*10, 0), hash_value=f"line{i}")
                for i in range(10)
            ]
        }

        result = comparator.compare(entities_a, entities_b)

        # 상위 3개만 요청
        top_changes = result.get_top_changes(top_n=3)

        assert len(top_changes) == 3

    def test_get_top_changes_with_min_priority_filter(self):
        """get_top_changes()가 최소 우선순위 필터를 적용하는지 확인"""
        comparator = DxfComparator()

        entities_a = {"LINE": []}
        entities_b = {
            "LINE": [
                create_entity("LINE", "S-BEAM", (0, 0), hash_value="beam1"),  # CRITICAL
                create_entity("LINE", "DEFPOINTS", (10, 10), hash_value="defp1"),  # TRIVIAL
                create_entity("LINE", "MISC", (20, 20), hash_value="misc1"),  # MEDIUM (default)
            ]
        }

        result = comparator.compare(entities_a, entities_b)

        # HIGH 이상만 필터
        top_changes = result.get_top_changes(top_n=10, min_priority=PriorityLevel.HIGH)

        for change in top_changes:
            assert change.priority_score.priority_level.value >= PriorityLevel.HIGH.value


class TestFilterByPriorityLevel:
    """filter_by_priority_level() 테스트"""

    def test_filter_by_priority_level_critical(self):
        """CRITICAL 레벨만 필터링되는지 확인"""
        comparator = DxfComparator()

        entities_a = {"LINE": []}
        entities_b = {
            "LINE": [
                create_entity("LINE", "S-BEAM", (0, 0), hash_value="beam1"),  # CRITICAL
                create_entity("LINE", "DIM", (10, 10), hash_value="dim1"),  # HIGH
                create_entity("LINE", "TEXT", (20, 20), hash_value="text1"),  # MEDIUM
            ]
        }

        result = comparator.compare(entities_a, entities_b)
        critical_changes = result.filter_by_priority_level(PriorityLevel.CRITICAL)

        assert len(critical_changes) == 1
        assert critical_changes[0].layer == "S-BEAM"


class TestReviewNeededChanges:
    """get_review_needed_changes() 테스트"""

    def test_review_needed_includes_structural_changes(self):
        """구조 변경은 항상 review_needed에 포함되는지 확인"""
        comparator = DxfComparator()

        entities_a = {"LINE": []}
        entities_b = {
            "LINE": [
                create_entity("LINE", "S-COLUMN", (0, 0), hash_value="col1"),
            ]
        }

        result = comparator.compare(entities_a, entities_b)
        review_needed = result.get_review_needed_changes()

        assert len(review_needed) >= 1
        assert review_needed[0].priority_score.review_needed is True


class TestPriorityStatistics:
    """get_priority_statistics() 테스트"""

    def test_priority_statistics_counts(self):
        """우선순위 통계가 올바르게 계산되는지 확인"""
        comparator = DxfComparator()

        entities_a = {"LINE": []}
        entities_b = {
            "LINE": [
                create_entity("LINE", "S-BEAM", (0, 0), hash_value="beam1"),  # CRITICAL
                create_entity("LINE", "S-WALL", (10, 10), hash_value="wall1"),  # CRITICAL
                create_entity("LINE", "DIM", (20, 20), hash_value="dim1"),  # HIGH
            ]
        }

        result = comparator.compare(entities_a, entities_b)
        stats = result.get_priority_statistics()

        assert stats["by_priority"]["CRITICAL"] == 2
        assert stats["by_priority"]["HIGH"] == 1
        assert stats["changes_with_score"] == 3


class TestToDictIntegration:
    """to_dict()에 priority_score가 포함되는지 테스트"""

    def test_change_to_dict_includes_priority_score(self):
        """DxfChange.to_dict()에 priority_score가 포함되는지 확인"""
        comparator = DxfComparator()

        entities_a = {"LINE": []}
        entities_b = {
            "LINE": [
                create_entity("LINE", "S-BEAM", (0, 0), hash_value="beam1"),
            ]
        }

        result = comparator.compare(entities_a, entities_b)
        change = result.changes[0]
        change_dict = change.to_dict()

        assert "priority_score" in change_dict
        assert change_dict["priority_score"]["priority_level"] == "CRITICAL"


class TestDeletedEntityPriority:
    """삭제된 엔티티도 우선순위가 계산되는지 테스트"""

    def test_deleted_entity_gets_priority(self):
        """삭제된 엔티티에도 priority_score가 설정되는지 확인"""
        comparator = DxfComparator()

        entities_a = {
            "LINE": [
                create_entity("LINE", "S-BEAM", (0, 0), hash_value="beam1"),
            ]
        }
        entities_b = {"LINE": []}

        result = comparator.compare(entities_a, entities_b)

        assert len(result.changes) == 1
        change = result.changes[0]

        assert change.change_type == DxfChangeType.DELETED
        assert change.priority_score is not None
        assert change.priority_score.priority_level == PriorityLevel.CRITICAL

    def test_deleted_has_higher_weight(self):
        """삭제는 추가보다 약간 높은 점수를 받는지 확인"""
        from src.services.comparison.priority_calculator import PriorityCalculator

        calculator = PriorityCalculator()

        added_score = calculator.calculate("ADDED", "TEST-LAYER")
        deleted_score = calculator.calculate("DELETED", "TEST-LAYER")

        # 삭제가 1.2배 가중치
        assert deleted_score.priority_score > added_score.priority_score
