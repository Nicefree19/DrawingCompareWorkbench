# -*- coding: utf-8 -*-
"""Top N 필터 모듈

QW-NEW: 상위 N개 변경 사항 필터링 시스템

핵심 기능:
- TopNFilterConfig: 필터 설정 (개수, 우선순위, 변경타입, 레이어 등)
- TopNFilter: 통합 필터 엔진
- FilterResult: 필터 결과 및 통계
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Set, Callable
from enum import Enum
import fnmatch
import logging

from .priority_score import PriorityLevel, PriorityScore

logger = logging.getLogger(__name__)


class FilterMode(Enum):
    """필터 모드

    Attributes:
        TOP_N: 상위 N개만 반환
        THRESHOLD: 임계값 이상만 반환
        PERCENTAGE: 상위 N% 반환
    """
    TOP_N = "top_n"
    THRESHOLD = "threshold"
    PERCENTAGE = "percentage"


@dataclass
class TopNFilterConfig:
    """Top N 필터 설정

    Attributes:
        top_n: 상위 N개 (0이면 필터 없음, 전체 반환)
        mode: 필터 모드 (TOP_N, THRESHOLD, PERCENTAGE)
        min_priority: 최소 우선순위 레벨
        include_change_types: 포함할 변경 타입 (비어있으면 전체)
        exclude_change_types: 제외할 변경 타입
        include_layers: 포함할 레이어 패턴 (fnmatch 스타일)
        exclude_layers: 제외할 레이어 패턴
        review_needed_only: 검토 필요 항목만 필터링
        min_confidence: 최소 신뢰도 (0.0~1.0)
        threshold_score: 임계 점수 (THRESHOLD 모드용)
        percentage: 상위 퍼센트 (PERCENTAGE 모드용)
    """
    top_n: int = 0  # 0 = 필터 없음
    mode: FilterMode = FilterMode.TOP_N
    min_priority: Optional[PriorityLevel] = None
    include_change_types: List[str] = field(default_factory=list)
    exclude_change_types: List[str] = field(default_factory=list)
    include_layers: List[str] = field(default_factory=list)
    exclude_layers: List[str] = field(default_factory=list)
    review_needed_only: bool = False
    min_confidence: float = 0.0
    threshold_score: float = 0.0  # THRESHOLD 모드용
    percentage: float = 100.0  # PERCENTAGE 모드용

    @classmethod
    def create_top_n(cls, n: int, min_priority: Optional[PriorityLevel] = None) -> "TopNFilterConfig":
        """상위 N개 필터 생성

        Args:
            n: 반환할 개수 (0이면 전체)
            min_priority: 최소 우선순위

        Returns:
            TopNFilterConfig 인스턴스
        """
        return cls(top_n=n, mode=FilterMode.TOP_N, min_priority=min_priority)

    @classmethod
    def create_critical_only(cls, top_n: int = 0) -> "TopNFilterConfig":
        """CRITICAL 우선순위만 필터 생성"""
        return cls(top_n=top_n, min_priority=PriorityLevel.CRITICAL)

    @classmethod
    def create_high_and_above(cls, top_n: int = 0) -> "TopNFilterConfig":
        """HIGH 이상 우선순위 필터 생성"""
        return cls(top_n=top_n, min_priority=PriorityLevel.HIGH)

    @classmethod
    def create_review_needed(cls, top_n: int = 0) -> "TopNFilterConfig":
        """검토 필요 항목만 필터 생성"""
        return cls(top_n=top_n, review_needed_only=True)

    @classmethod
    def create_structural_changes(cls, top_n: int = 0) -> "TopNFilterConfig":
        """구조 변경 레이어만 필터 생성"""
        return cls(
            top_n=top_n,
            include_layers=["*BEAM*", "*COLUMN*", "*WALL*", "*SLAB*", "*STRUCT*", "*STEEL*"],
        )

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "top_n": self.top_n,
            "mode": self.mode.value,
            "min_priority": self.min_priority.name if self.min_priority else None,
            "include_change_types": self.include_change_types,
            "exclude_change_types": self.exclude_change_types,
            "include_layers": self.include_layers,
            "exclude_layers": self.exclude_layers,
            "review_needed_only": self.review_needed_only,
            "min_confidence": self.min_confidence,
            "threshold_score": self.threshold_score,
            "percentage": self.percentage,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TopNFilterConfig":
        """딕셔너리에서 생성"""
        min_priority = None
        if data.get("min_priority"):
            min_priority = PriorityLevel[data["min_priority"]]

        mode = FilterMode.TOP_N
        if data.get("mode"):
            mode = FilterMode(data["mode"])

        return cls(
            top_n=data.get("top_n", 0),
            mode=mode,
            min_priority=min_priority,
            include_change_types=data.get("include_change_types", []),
            exclude_change_types=data.get("exclude_change_types", []),
            include_layers=data.get("include_layers", []),
            exclude_layers=data.get("exclude_layers", []),
            review_needed_only=data.get("review_needed_only", False),
            min_confidence=data.get("min_confidence", 0.0),
            threshold_score=data.get("threshold_score", 0.0),
            percentage=data.get("percentage", 100.0),
        )


@dataclass
class FilterStatistics:
    """필터 결과 통계

    Attributes:
        total_count: 전체 변경 개수
        filtered_count: 필터링 후 개수
        excluded_by_priority: 우선순위로 제외된 개수
        excluded_by_change_type: 변경타입으로 제외된 개수
        excluded_by_layer: 레이어로 제외된 개수
        excluded_by_review: 검토 필요 조건으로 제외된 개수
        excluded_by_confidence: 신뢰도로 제외된 개수
        excluded_by_top_n: Top N으로 제한된 개수
        by_priority: 우선순위별 통계
        by_change_type: 변경타입별 통계
    """
    total_count: int = 0
    filtered_count: int = 0
    excluded_by_priority: int = 0
    excluded_by_change_type: int = 0
    excluded_by_layer: int = 0
    excluded_by_review: int = 0
    excluded_by_confidence: int = 0
    excluded_by_top_n: int = 0
    by_priority: Dict[str, int] = field(default_factory=dict)
    by_change_type: Dict[str, int] = field(default_factory=dict)

    @property
    def filter_rate(self) -> float:
        """필터 비율 (0.0~1.0)"""
        if self.total_count == 0:
            return 0.0
        return self.filtered_count / self.total_count

    @property
    def excluded_count(self) -> int:
        """제외된 총 개수"""
        return self.total_count - self.filtered_count

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "total_count": self.total_count,
            "filtered_count": self.filtered_count,
            "excluded_count": self.excluded_count,
            "filter_rate": round(self.filter_rate, 4),
            "excluded_by_priority": self.excluded_by_priority,
            "excluded_by_change_type": self.excluded_by_change_type,
            "excluded_by_layer": self.excluded_by_layer,
            "excluded_by_review": self.excluded_by_review,
            "excluded_by_confidence": self.excluded_by_confidence,
            "excluded_by_top_n": self.excluded_by_top_n,
            "by_priority": self.by_priority,
            "by_change_type": self.by_change_type,
        }


@dataclass
class FilterResult:
    """필터 결과

    Attributes:
        items: 필터링된 항목 목록
        statistics: 필터 통계
        config: 적용된 필터 설정
    """
    items: List[Any] = field(default_factory=list)
    statistics: FilterStatistics = field(default_factory=FilterStatistics)
    config: Optional[TopNFilterConfig] = None

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __getitem__(self, index):
        return self.items[index]

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "count": len(self.items),
            "statistics": self.statistics.to_dict(),
            "config": self.config.to_dict() if self.config else None,
        }


class TopNFilter:
    """상위 N개 필터 엔진

    DxfChange 목록에서 조건에 맞는 상위 변경 사항을 필터링합니다.

    Examples:
        >>> from src.services.comparison.top_n_filter import TopNFilter, TopNFilterConfig
        >>> config = TopNFilterConfig.create_top_n(10, PriorityLevel.HIGH)
        >>> filter_engine = TopNFilter(config)
        >>> result = filter_engine.filter(changes)
        >>> print(f"필터링: {result.statistics.total_count} -> {len(result)}")
    """

    def __init__(self, config: Optional[TopNFilterConfig] = None):
        """초기화

        Args:
            config: 필터 설정 (None이면 기본 설정)
        """
        self.config = config or TopNFilterConfig()

    def filter(
        self,
        changes: List[Any],
        config: Optional[TopNFilterConfig] = None,
    ) -> FilterResult:
        """변경 목록 필터링

        Args:
            changes: DxfChange 또는 유사 객체 목록
            config: 임시 필터 설정 (None이면 인스턴스 설정 사용)

        Returns:
            FilterResult: 필터 결과
        """
        cfg = config or self.config
        stats = FilterStatistics(total_count=len(changes))

        # 필터 비활성화 (top_n=0이고 다른 조건 없음)
        if self._is_no_filter(cfg):
            # 정렬만 수행
            sorted_changes = self._sort_by_priority(changes)
            self._update_statistics(stats, sorted_changes)
            stats.filtered_count = len(sorted_changes)
            return FilterResult(items=sorted_changes, statistics=stats, config=cfg)

        filtered = list(changes)

        # 1. 우선순위 필터
        if cfg.min_priority:
            before_count = len(filtered)
            filtered = self._filter_by_priority(filtered, cfg.min_priority)
            stats.excluded_by_priority = before_count - len(filtered)

        # 2. 변경타입 필터
        if cfg.include_change_types or cfg.exclude_change_types:
            before_count = len(filtered)
            filtered = self._filter_by_change_type(
                filtered, cfg.include_change_types, cfg.exclude_change_types
            )
            stats.excluded_by_change_type = before_count - len(filtered)

        # 3. 레이어 필터
        if cfg.include_layers or cfg.exclude_layers:
            before_count = len(filtered)
            filtered = self._filter_by_layer(
                filtered, cfg.include_layers, cfg.exclude_layers
            )
            stats.excluded_by_layer = before_count - len(filtered)

        # 4. 검토 필요 필터
        if cfg.review_needed_only:
            before_count = len(filtered)
            filtered = self._filter_by_review_needed(filtered)
            stats.excluded_by_review = before_count - len(filtered)

        # 5. 신뢰도 필터
        if cfg.min_confidence > 0:
            before_count = len(filtered)
            filtered = self._filter_by_confidence(filtered, cfg.min_confidence)
            stats.excluded_by_confidence = before_count - len(filtered)

        # 6. 정렬 (우선순위 순)
        filtered = self._sort_by_priority(filtered)

        # 7. Top N / Threshold / Percentage 적용
        before_top_n = len(filtered)
        filtered = self._apply_limit(filtered, cfg)
        stats.excluded_by_top_n = before_top_n - len(filtered)

        # 통계 업데이트
        self._update_statistics(stats, filtered)
        stats.filtered_count = len(filtered)

        logger.debug(
            f"[TopNFilter] {stats.total_count} -> {stats.filtered_count} "
            f"(필터율: {stats.filter_rate:.1%})"
        )

        return FilterResult(items=filtered, statistics=stats, config=cfg)

    def _is_no_filter(self, cfg: TopNFilterConfig) -> bool:
        """필터 조건이 없는지 확인"""
        return (
            cfg.top_n == 0 and
            cfg.min_priority is None and
            not cfg.include_change_types and
            not cfg.exclude_change_types and
            not cfg.include_layers and
            not cfg.exclude_layers and
            not cfg.review_needed_only and
            cfg.min_confidence == 0.0 and
            cfg.threshold_score == 0.0 and
            cfg.percentage >= 100.0
        )

    def _filter_by_priority(
        self, changes: List[Any], min_priority: PriorityLevel
    ) -> List[Any]:
        """우선순위로 필터링"""
        result = []
        for change in changes:
            priority_score = getattr(change, "priority_score", None)
            if priority_score:
                if priority_score.priority_level.value >= min_priority.value:
                    result.append(change)
            else:
                # priority_score가 없으면 제외 (보수적 접근)
                pass
        return result

    def _filter_by_change_type(
        self,
        changes: List[Any],
        include_types: List[str],
        exclude_types: List[str],
    ) -> List[Any]:
        """변경타입으로 필터링"""
        result = []
        include_set = set(t.upper() for t in include_types) if include_types else None
        exclude_set = set(t.upper() for t in exclude_types)

        for change in changes:
            change_type = self._get_change_type(change)
            if not change_type:
                continue

            change_type_upper = change_type.upper()

            # 제외 타입 체크
            if change_type_upper in exclude_set:
                continue

            # 포함 타입 체크
            if include_set and change_type_upper not in include_set:
                continue

            result.append(change)

        return result

    def _filter_by_layer(
        self,
        changes: List[Any],
        include_layers: List[str],
        exclude_layers: List[str],
    ) -> List[Any]:
        """레이어로 필터링 (fnmatch 패턴)"""
        result = []

        for change in changes:
            layer = getattr(change, "layer", "")
            if not layer:
                continue

            layer_upper = layer.upper()

            # 제외 레이어 체크
            excluded = False
            for pattern in exclude_layers:
                if fnmatch.fnmatch(layer_upper, pattern.upper()):
                    excluded = True
                    break
            if excluded:
                continue

            # 포함 레이어 체크
            if include_layers:
                included = False
                for pattern in include_layers:
                    if fnmatch.fnmatch(layer_upper, pattern.upper()):
                        included = True
                        break
                if not included:
                    continue

            result.append(change)

        return result

    def _filter_by_review_needed(self, changes: List[Any]) -> List[Any]:
        """검토 필요 항목만 필터링"""
        result = []
        for change in changes:
            priority_score = getattr(change, "priority_score", None)
            if priority_score and priority_score.review_needed:
                result.append(change)
        return result

    def _filter_by_confidence(
        self, changes: List[Any], min_confidence: float
    ) -> List[Any]:
        """신뢰도로 필터링"""
        result = []
        for change in changes:
            priority_score = getattr(change, "priority_score", None)
            if priority_score:
                if priority_score.confidence_score >= min_confidence:
                    result.append(change)
            else:
                # priority_score가 없으면 포함 (관대한 접근)
                result.append(change)
        return result

    def _sort_by_priority(self, changes: List[Any]) -> List[Any]:
        """우선순위로 정렬 (높은 순)"""
        def sort_key(change):
            priority_score = getattr(change, "priority_score", None)
            if priority_score:
                return (
                    priority_score.priority_level.value,
                    priority_score.priority_score,
                )
            return (0, 0.0)

        return sorted(changes, key=sort_key, reverse=True)

    def _apply_limit(self, changes: List[Any], cfg: TopNFilterConfig) -> List[Any]:
        """최종 개수 제한 적용"""
        if cfg.mode == FilterMode.TOP_N:
            if cfg.top_n > 0:
                return changes[:cfg.top_n]
            return changes

        elif cfg.mode == FilterMode.THRESHOLD:
            result = []
            for change in changes:
                priority_score = getattr(change, "priority_score", None)
                if priority_score and priority_score.priority_score >= cfg.threshold_score:
                    result.append(change)
            return result

        elif cfg.mode == FilterMode.PERCENTAGE:
            if cfg.percentage >= 100.0:
                return changes
            count = max(1, int(len(changes) * cfg.percentage / 100.0))
            return changes[:count]

        return changes

    def _get_change_type(self, change: Any) -> Optional[str]:
        """변경 타입 추출"""
        change_type = getattr(change, "change_type", None)
        if change_type is None:
            return None

        # Enum인 경우
        if hasattr(change_type, "value"):
            return str(change_type.value)

        return str(change_type)

    def _update_statistics(self, stats: FilterStatistics, changes: List[Any]) -> None:
        """통계 업데이트"""
        stats.by_priority = {level.name: 0 for level in PriorityLevel}
        stats.by_change_type = {}

        for change in changes:
            # 우선순위별 통계
            priority_score = getattr(change, "priority_score", None)
            if priority_score:
                stats.by_priority[priority_score.priority_level.name] += 1

            # 변경타입별 통계
            change_type = self._get_change_type(change)
            if change_type:
                change_type_upper = change_type.upper()
                stats.by_change_type[change_type_upper] = (
                    stats.by_change_type.get(change_type_upper, 0) + 1
                )


# 편의 함수
def filter_top_n(
    changes: List[Any],
    top_n: int,
    min_priority: Optional[PriorityLevel] = None,
) -> FilterResult:
    """상위 N개 필터링 편의 함수

    Args:
        changes: 변경 목록
        top_n: 반환할 개수 (0이면 전체)
        min_priority: 최소 우선순위

    Returns:
        FilterResult: 필터 결과
    """
    config = TopNFilterConfig.create_top_n(top_n, min_priority)
    return TopNFilter(config).filter(changes)


def filter_critical_changes(changes: List[Any], top_n: int = 0) -> FilterResult:
    """CRITICAL 변경만 필터링"""
    config = TopNFilterConfig.create_critical_only(top_n)
    return TopNFilter(config).filter(changes)


def filter_review_needed(changes: List[Any], top_n: int = 0) -> FilterResult:
    """검토 필요 변경만 필터링"""
    config = TopNFilterConfig.create_review_needed(top_n)
    return TopNFilter(config).filter(changes)


def filter_structural_changes(changes: List[Any], top_n: int = 0) -> FilterResult:
    """구조 변경만 필터링"""
    config = TopNFilterConfig.create_structural_changes(top_n)
    return TopNFilter(config).filter(changes)


def apply_project_filter(
    changes: List[Any],
    top_n: int,
    min_priority_str: Optional[str] = None,
) -> FilterResult:
    """ProjectConfig 설정으로 필터링

    ProjectConfig.top_n_filter와 연동되는 편의 함수입니다.

    Args:
        changes: 변경 목록
        top_n: 상위 N개 (0이면 전체)
        min_priority_str: 최소 우선순위 문자열 (예: "HIGH", "CRITICAL")

    Returns:
        FilterResult: 필터 결과
    """
    min_priority = None
    if min_priority_str:
        min_priority = PriorityLevel.from_string(min_priority_str)

    config = TopNFilterConfig.create_top_n(top_n, min_priority)
    return TopNFilter(config).filter(changes)
