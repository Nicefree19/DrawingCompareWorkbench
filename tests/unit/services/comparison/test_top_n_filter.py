# -*- coding: utf-8 -*-
"""Top N 필터 테스트

QW-NEW: TopNFilter 모듈 테스트
"""

import pytest
from dataclasses import dataclass
from typing import Optional
from enum import Enum

from src.services.comparison.top_n_filter import (
    FilterMode,
    TopNFilterConfig,
    FilterStatistics,
    FilterResult,
    TopNFilter,
    filter_top_n,
    filter_critical_changes,
    filter_review_needed,
    filter_structural_changes,
    apply_project_filter,
)
from src.services.comparison.priority_score import (
    PriorityLevel,
    PriorityScore,
    ReviewReason,
)


class MockChangeType(Enum):
    """테스트용 변경 타입"""
    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"


@dataclass
class MockChange:
    """테스트용 변경 객체"""
    layer: str
    change_type: MockChangeType
    priority_score: Optional[PriorityScore] = None


def create_mock_change(
    layer: str = "TEST_LAYER",
    change_type: MockChangeType = MockChangeType.ADDED,
    priority_level: PriorityLevel = PriorityLevel.MEDIUM,
    priority_score_value: float = 50.0,
    confidence: float = 0.9,
    review_needed: bool = False,
) -> MockChange:
    """테스트용 변경 객체 생성"""
    return MockChange(
        layer=layer,
        change_type=change_type,
        priority_score=PriorityScore(
            priority_level=priority_level,
            priority_score=priority_score_value,
            confidence_score=confidence,
            review_needed=review_needed,
            source_layer=layer,
            change_type=change_type.value,
        ),
    )


def create_mock_changes(count: int = 10) -> list:
    """테스트용 변경 목록 생성 (다양한 우선순위)"""
    changes = []
    levels = list(PriorityLevel)

    for i in range(count):
        level = levels[i % len(levels)]
        layer_type = ["BEAM", "DIMENSION", "TEXT", "GRID", "REF"][i % 5]
        change_type = [MockChangeType.ADDED, MockChangeType.DELETED, MockChangeType.MODIFIED][i % 3]

        changes.append(create_mock_change(
            layer=f"S-{layer_type}-{i}",
            change_type=change_type,
            priority_level=level,
            priority_score_value=level.value * 20,
            confidence=0.5 + (i % 5) * 0.1,
            review_needed=(i % 3 == 0),
        ))

    return changes


class TestFilterMode:
    """FilterMode 열거형 테스트"""

    def test_filter_mode_values(self):
        """필터 모드 값 확인"""
        assert FilterMode.TOP_N.value == "top_n"
        assert FilterMode.THRESHOLD.value == "threshold"
        assert FilterMode.PERCENTAGE.value == "percentage"


class TestTopNFilterConfig:
    """TopNFilterConfig 테스트"""

    def test_default_values(self):
        """기본값 확인"""
        config = TopNFilterConfig()
        assert config.top_n == 0
        assert config.mode == FilterMode.TOP_N
        assert config.min_priority is None
        assert config.include_change_types == []
        assert config.exclude_change_types == []
        assert config.include_layers == []
        assert config.exclude_layers == []
        assert config.review_needed_only is False
        assert config.min_confidence == 0.0

    def test_create_top_n(self):
        """create_top_n 팩토리"""
        config = TopNFilterConfig.create_top_n(10, PriorityLevel.HIGH)
        assert config.top_n == 10
        assert config.min_priority == PriorityLevel.HIGH
        assert config.mode == FilterMode.TOP_N

    def test_create_critical_only(self):
        """create_critical_only 팩토리"""
        config = TopNFilterConfig.create_critical_only(5)
        assert config.top_n == 5
        assert config.min_priority == PriorityLevel.CRITICAL

    def test_create_high_and_above(self):
        """create_high_and_above 팩토리"""
        config = TopNFilterConfig.create_high_and_above()
        assert config.min_priority == PriorityLevel.HIGH

    def test_create_review_needed(self):
        """create_review_needed 팩토리"""
        config = TopNFilterConfig.create_review_needed(10)
        assert config.top_n == 10
        assert config.review_needed_only is True

    def test_create_structural_changes(self):
        """create_structural_changes 팩토리"""
        config = TopNFilterConfig.create_structural_changes()
        assert "*BEAM*" in config.include_layers
        assert "*COLUMN*" in config.include_layers
        assert "*STRUCT*" in config.include_layers

    def test_to_dict(self):
        """딕셔너리 변환"""
        config = TopNFilterConfig(
            top_n=10,
            min_priority=PriorityLevel.HIGH,
            include_change_types=["ADDED"],
        )
        data = config.to_dict()
        assert data["top_n"] == 10
        assert data["min_priority"] == "HIGH"
        assert data["include_change_types"] == ["ADDED"]

    def test_from_dict(self):
        """딕셔너리에서 생성"""
        data = {
            "top_n": 5,
            "min_priority": "CRITICAL",
            "mode": "threshold",
            "threshold_score": 80.0,
        }
        config = TopNFilterConfig.from_dict(data)
        assert config.top_n == 5
        assert config.min_priority == PriorityLevel.CRITICAL
        assert config.mode == FilterMode.THRESHOLD
        assert config.threshold_score == 80.0

    def test_from_dict_empty(self):
        """빈 딕셔너리에서 생성"""
        config = TopNFilterConfig.from_dict({})
        assert config.top_n == 0
        assert config.min_priority is None


class TestFilterStatistics:
    """FilterStatistics 테스트"""

    def test_default_values(self):
        """기본값 확인"""
        stats = FilterStatistics()
        assert stats.total_count == 0
        assert stats.filtered_count == 0
        assert stats.filter_rate == 0.0

    def test_filter_rate(self):
        """필터 비율 계산"""
        stats = FilterStatistics(total_count=100, filtered_count=30)
        assert stats.filter_rate == 0.3

    def test_excluded_count(self):
        """제외 개수 계산"""
        stats = FilterStatistics(total_count=100, filtered_count=30)
        assert stats.excluded_count == 70

    def test_to_dict(self):
        """딕셔너리 변환"""
        stats = FilterStatistics(
            total_count=100,
            filtered_count=25,
            excluded_by_priority=50,
            excluded_by_top_n=25,
        )
        data = stats.to_dict()
        assert data["total_count"] == 100
        assert data["filtered_count"] == 25
        assert data["excluded_count"] == 75
        assert data["filter_rate"] == 0.25


class TestFilterResult:
    """FilterResult 테스트"""

    def test_len(self):
        """길이 확인"""
        result = FilterResult(items=[1, 2, 3])
        assert len(result) == 3

    def test_iter(self):
        """반복 확인"""
        result = FilterResult(items=[1, 2, 3])
        assert list(result) == [1, 2, 3]

    def test_getitem(self):
        """인덱싱 확인"""
        result = FilterResult(items=["a", "b", "c"])
        assert result[0] == "a"
        assert result[2] == "c"

    def test_to_dict(self):
        """딕셔너리 변환"""
        config = TopNFilterConfig(top_n=5)
        stats = FilterStatistics(total_count=10, filtered_count=5)
        result = FilterResult(items=[1, 2, 3], statistics=stats, config=config)
        data = result.to_dict()
        assert data["count"] == 3
        assert data["statistics"]["total_count"] == 10


class TestTopNFilter:
    """TopNFilter 테스트"""

    def test_no_filter(self):
        """필터 없음 (전체 반환)"""
        changes = create_mock_changes(10)
        filter_engine = TopNFilter()
        result = filter_engine.filter(changes)

        assert len(result) == 10
        assert result.statistics.total_count == 10
        assert result.statistics.filtered_count == 10

    def test_top_n_filter(self):
        """Top N 필터"""
        changes = create_mock_changes(20)
        config = TopNFilterConfig.create_top_n(5)
        filter_engine = TopNFilter(config)
        result = filter_engine.filter(changes)

        assert len(result) == 5
        assert result.statistics.total_count == 20
        assert result.statistics.excluded_by_top_n == 15

    def test_priority_filter(self):
        """우선순위 필터"""
        changes = [
            create_mock_change(priority_level=PriorityLevel.CRITICAL),
            create_mock_change(priority_level=PriorityLevel.HIGH),
            create_mock_change(priority_level=PriorityLevel.MEDIUM),
            create_mock_change(priority_level=PriorityLevel.LOW),
            create_mock_change(priority_level=PriorityLevel.TRIVIAL),
        ]

        config = TopNFilterConfig(min_priority=PriorityLevel.HIGH)
        result = TopNFilter(config).filter(changes)

        assert len(result) == 2  # CRITICAL, HIGH
        assert result.statistics.excluded_by_priority == 3

    def test_change_type_include_filter(self):
        """변경타입 포함 필터"""
        changes = [
            create_mock_change(change_type=MockChangeType.ADDED),
            create_mock_change(change_type=MockChangeType.DELETED),
            create_mock_change(change_type=MockChangeType.MODIFIED),
        ]

        config = TopNFilterConfig(include_change_types=["ADDED", "DELETED"])
        result = TopNFilter(config).filter(changes)

        assert len(result) == 2
        assert result.statistics.excluded_by_change_type == 1

    def test_change_type_exclude_filter(self):
        """변경타입 제외 필터"""
        changes = [
            create_mock_change(change_type=MockChangeType.ADDED),
            create_mock_change(change_type=MockChangeType.DELETED),
            create_mock_change(change_type=MockChangeType.MODIFIED),
        ]

        config = TopNFilterConfig(exclude_change_types=["MODIFIED"])
        result = TopNFilter(config).filter(changes)

        assert len(result) == 2

    def test_layer_include_filter(self):
        """레이어 포함 필터 (패턴)"""
        changes = [
            create_mock_change(layer="S-BEAM-001"),
            create_mock_change(layer="S-COLUMN-001"),
            create_mock_change(layer="S-TEXT-001"),
        ]

        config = TopNFilterConfig(include_layers=["*BEAM*", "*COLUMN*"])
        result = TopNFilter(config).filter(changes)

        assert len(result) == 2
        assert result.statistics.excluded_by_layer == 1

    def test_layer_exclude_filter(self):
        """레이어 제외 필터 (패턴)"""
        changes = [
            create_mock_change(layer="S-BEAM-001"),
            create_mock_change(layer="S-TEXT-001"),
            create_mock_change(layer="DEFPOINTS"),
        ]

        config = TopNFilterConfig(exclude_layers=["DEFPOINTS", "*TEXT*"])
        result = TopNFilter(config).filter(changes)

        assert len(result) == 1
        assert result.items[0].layer == "S-BEAM-001"

    def test_review_needed_filter(self):
        """검토 필요 필터"""
        changes = [
            create_mock_change(review_needed=True),
            create_mock_change(review_needed=True),
            create_mock_change(review_needed=False),
        ]

        config = TopNFilterConfig(review_needed_only=True)
        result = TopNFilter(config).filter(changes)

        assert len(result) == 2
        assert result.statistics.excluded_by_review == 1

    def test_confidence_filter(self):
        """신뢰도 필터"""
        changes = [
            create_mock_change(confidence=0.9),
            create_mock_change(confidence=0.7),
            create_mock_change(confidence=0.4),
        ]

        config = TopNFilterConfig(min_confidence=0.6)
        result = TopNFilter(config).filter(changes)

        assert len(result) == 2
        assert result.statistics.excluded_by_confidence == 1

    def test_sort_by_priority(self):
        """우선순위 정렬"""
        changes = [
            create_mock_change(priority_level=PriorityLevel.LOW),
            create_mock_change(priority_level=PriorityLevel.CRITICAL),
            create_mock_change(priority_level=PriorityLevel.MEDIUM),
        ]

        result = TopNFilter().filter(changes)

        # CRITICAL이 첫 번째
        assert result.items[0].priority_score.priority_level == PriorityLevel.CRITICAL
        assert result.items[-1].priority_score.priority_level == PriorityLevel.LOW

    def test_threshold_mode(self):
        """임계 점수 모드"""
        changes = [
            create_mock_change(priority_score_value=100),
            create_mock_change(priority_score_value=80),
            create_mock_change(priority_score_value=50),
            create_mock_change(priority_score_value=30),
        ]

        config = TopNFilterConfig(mode=FilterMode.THRESHOLD, threshold_score=60)
        result = TopNFilter(config).filter(changes)

        assert len(result) == 2

    def test_percentage_mode(self):
        """상위 퍼센트 모드"""
        changes = create_mock_changes(100)

        config = TopNFilterConfig(mode=FilterMode.PERCENTAGE, percentage=25.0)
        result = TopNFilter(config).filter(changes)

        assert len(result) == 25

    def test_combined_filters(self):
        """복합 필터"""
        changes = create_mock_changes(50)

        config = TopNFilterConfig(
            top_n=10,
            min_priority=PriorityLevel.MEDIUM,
            include_layers=["*BEAM*", "*DIMENSION*"],
        )
        result = TopNFilter(config).filter(changes)

        assert len(result) <= 10
        for item in result.items:
            assert item.priority_score.priority_level.value >= PriorityLevel.MEDIUM.value

    def test_statistics_by_priority(self):
        """우선순위별 통계"""
        changes = [
            create_mock_change(priority_level=PriorityLevel.CRITICAL),
            create_mock_change(priority_level=PriorityLevel.CRITICAL),
            create_mock_change(priority_level=PriorityLevel.HIGH),
            create_mock_change(priority_level=PriorityLevel.MEDIUM),
        ]

        result = TopNFilter().filter(changes)

        assert result.statistics.by_priority["CRITICAL"] == 2
        assert result.statistics.by_priority["HIGH"] == 1
        assert result.statistics.by_priority["MEDIUM"] == 1

    def test_statistics_by_change_type(self):
        """변경타입별 통계"""
        changes = [
            create_mock_change(change_type=MockChangeType.ADDED),
            create_mock_change(change_type=MockChangeType.ADDED),
            create_mock_change(change_type=MockChangeType.DELETED),
        ]

        result = TopNFilter().filter(changes)

        assert result.statistics.by_change_type["ADDED"] == 2
        assert result.statistics.by_change_type["DELETED"] == 1

    def test_filter_with_temp_config(self):
        """임시 설정으로 필터"""
        changes = create_mock_changes(20)
        filter_engine = TopNFilter()  # 기본 설정

        temp_config = TopNFilterConfig.create_top_n(3)
        result = filter_engine.filter(changes, config=temp_config)

        assert len(result) == 3


class TestConvenienceFunctions:
    """편의 함수 테스트"""

    def test_filter_top_n(self):
        """filter_top_n 함수"""
        changes = create_mock_changes(20)
        result = filter_top_n(changes, 5)

        assert len(result) == 5
        assert result.statistics.total_count == 20

    def test_filter_top_n_with_priority(self):
        """filter_top_n 함수 (우선순위 포함)"""
        changes = create_mock_changes(20)
        result = filter_top_n(changes, 10, PriorityLevel.HIGH)

        for item in result.items:
            assert item.priority_score.priority_level.value >= PriorityLevel.HIGH.value

    def test_filter_critical_changes(self):
        """filter_critical_changes 함수"""
        changes = [
            create_mock_change(priority_level=PriorityLevel.CRITICAL),
            create_mock_change(priority_level=PriorityLevel.HIGH),
            create_mock_change(priority_level=PriorityLevel.CRITICAL),
            create_mock_change(priority_level=PriorityLevel.LOW),
        ]

        result = filter_critical_changes(changes)

        assert len(result) == 2
        for item in result.items:
            assert item.priority_score.priority_level == PriorityLevel.CRITICAL

    def test_filter_review_needed(self):
        """filter_review_needed 함수"""
        changes = [
            create_mock_change(review_needed=True),
            create_mock_change(review_needed=False),
            create_mock_change(review_needed=True),
        ]

        result = filter_review_needed(changes)

        assert len(result) == 2

    def test_filter_structural_changes(self):
        """filter_structural_changes 함수"""
        changes = [
            create_mock_change(layer="S-BEAM-001"),
            create_mock_change(layer="S-TEXT-001"),
            create_mock_change(layer="S-COLUMN-002"),
        ]

        result = filter_structural_changes(changes)

        assert len(result) == 2

    def test_apply_project_filter(self):
        """apply_project_filter 함수"""
        changes = create_mock_changes(20)
        result = apply_project_filter(changes, top_n=5, min_priority_str="HIGH")

        assert len(result) <= 5


class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_empty_changes(self):
        """빈 변경 목록"""
        result = filter_top_n([], 10)

        assert len(result) == 0
        assert result.statistics.total_count == 0
        assert result.statistics.filtered_count == 0

    def test_top_n_larger_than_list(self):
        """Top N이 목록보다 큰 경우"""
        changes = create_mock_changes(5)
        result = filter_top_n(changes, 100)

        assert len(result) == 5

    def test_change_without_priority_score(self):
        """priority_score가 없는 변경"""
        changes = [
            MockChange(layer="TEST", change_type=MockChangeType.ADDED, priority_score=None),
            create_mock_change(priority_level=PriorityLevel.CRITICAL),
        ]

        # priority_score가 없는 항목은 우선순위 필터에서 제외됨
        config = TopNFilterConfig(min_priority=PriorityLevel.MEDIUM)
        result = TopNFilter(config).filter(changes)

        assert len(result) == 1

    def test_case_insensitive_change_type(self):
        """대소문자 무시 변경타입"""
        changes = [
            create_mock_change(change_type=MockChangeType.ADDED),
        ]

        config = TopNFilterConfig(include_change_types=["added"])  # 소문자
        result = TopNFilter(config).filter(changes)

        assert len(result) == 1

    def test_case_insensitive_layer(self):
        """대소문자 무시 레이어"""
        changes = [
            create_mock_change(layer="S-beam-001"),  # 소문자
        ]

        config = TopNFilterConfig(include_layers=["*BEAM*"])  # 대문자
        result = TopNFilter(config).filter(changes)

        assert len(result) == 1

    def test_zero_percentage(self):
        """0% 퍼센트 모드"""
        changes = create_mock_changes(10)

        config = TopNFilterConfig(mode=FilterMode.PERCENTAGE, percentage=0.0)
        result = TopNFilter(config).filter(changes)

        # 최소 1개는 반환
        assert len(result) >= 0

    def test_100_percentage(self):
        """100% 퍼센트 모드 (전체)"""
        changes = create_mock_changes(10)

        config = TopNFilterConfig(mode=FilterMode.PERCENTAGE, percentage=100.0)
        result = TopNFilter(config).filter(changes)

        assert len(result) == 10


class TestIntegration:
    """통합 테스트"""

    def test_realistic_workflow(self):
        """실제 워크플로우 시뮬레이션"""
        # 1. 대량 변경 생성
        changes = []
        for i in range(100):
            layer_type = ["BEAM", "COLUMN", "WALL", "TEXT", "DIM", "REF"][i % 6]
            level = [
                PriorityLevel.CRITICAL,
                PriorityLevel.HIGH,
                PriorityLevel.MEDIUM,
                PriorityLevel.LOW,
                PriorityLevel.TRIVIAL,
            ][i % 5]

            changes.append(create_mock_change(
                layer=f"S-{layer_type}-{i:03d}",
                priority_level=level,
                review_needed=(i % 10 == 0),
            ))

        # 2. 구조 변경만 상위 10개
        config = TopNFilterConfig(
            top_n=10,
            min_priority=PriorityLevel.HIGH,
            include_layers=["*BEAM*", "*COLUMN*", "*WALL*"],
        )
        result = TopNFilter(config).filter(changes)

        # 3. 검증
        assert len(result) <= 10
        assert result.statistics.total_count == 100
        assert result.statistics.filter_rate <= 0.1

        for item in result.items:
            assert item.priority_score.priority_level.value >= PriorityLevel.HIGH.value
            assert any(kw in item.layer.upper() for kw in ["BEAM", "COLUMN", "WALL"])

    def test_filter_result_with_project_config(self):
        """ProjectConfig 연동 시뮬레이션"""
        changes = create_mock_changes(50)

        # ProjectConfig.top_n_filter = 20 시뮬레이션
        project_top_n = 20
        result = apply_project_filter(changes, project_top_n)

        assert len(result) == 20
        assert result.config is not None
        assert result.config.top_n == 20
