"""DXF 엔티티 비교 엔진

Sprint 9 Phase 1.3: DxfComparator
두 DXF 문서의 엔티티를 해시 기반으로 비교합니다.
R-tree 공간 인덱싱으로 텍스트 위치 기반 매칭 성능 최적화.

비교 결과:
    - ADDED: 새로 추가된 엔티티
    - DELETED: 삭제된 엔티티
    - MODIFIED: 위치 기반 매칭으로 탐지된 수정 엔티티

성능:
    - 해시 기반 비교: O(n)
    - R-tree 공간 매칭: O(n log n) (vs 선형 O(n²))
"""

import logging
import math
import time  # Plan §16 Phase C-3.1 — time-to-first-stream instrumentation
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING, Union

from .dxf_entity_extractor import NormalizedEntity
from .spatial_index import SpatialIndex, create_spatial_index, RTREE_AVAILABLE
from .grid_spatial_index import GridSpatialIndex
from .comparison_config import (
    ComparisonConfig,
    SensitivityConfig,
    LayerPriorityConfig,
    get_default_config,
)
# Phase 3+ Priority Scoring System
from .priority_score import (
    PriorityLevel,
    PriorityScore as PriorityScoreData,
    ConfidenceFactors,
    ReviewReason,
)
from .priority_calculator import (
    PriorityCalculator,
    get_default_calculator,
)

logger = logging.getLogger(__name__)


class DxfChangeType(Enum):
    """DXF 변경 타입"""

    ADDED = "added"  # 새로 추가됨
    DELETED = "deleted"  # 삭제됨
    MODIFIED = "modified"  # 수정됨 (향후)


@dataclass
class DxfChange:
    """DXF 변경 사항

    Attributes:
        entity_type: 엔티티 타입 (LINE, CIRCLE 등)
        layer: 레이어 이름
        change_type: 변경 타입
        old_data: 이전 데이터 (DELETED, MODIFIED)
        new_data: 새 데이터 (ADDED, MODIFIED)
        location: 변경 위치 (x, y)
        measurement_diff: 치수 측정값 차이 (DIMENSION 전용)
        change_detail: 변경 상세 설명 (예: "1500.0 → 1600.0 (+100.0)")
        change_category: 변경 카테고리 (dimension, position, rotation, scale, content)
        old_location: 이전 위치 (MODIFIED일 때 이동 전 위치)
    """

    entity_type: str
    layer: str
    change_type: DxfChangeType
    old_data: Optional[Dict[str, Any]] = None
    new_data: Optional[Dict[str, Any]] = None
    location: Optional[Tuple[float, float]] = None
    measurement_diff: Optional[float] = None  # Sprint 10: 치수 변경 추적

    # Phase 3 P3-1: 변경 상세 정보
    change_detail: Optional[str] = None  # 상세 설명 문자열
    change_category: Optional[str] = None  # 변경 카테고리 (쉼표 구분 복수 가능)
    old_location: Optional[Tuple[float, float]] = None  # 이동 전 위치

    # Phase 3+ Priority Scoring System
    priority_score: Optional["PriorityScoreData"] = None  # 우선순위 점수

    @property
    def color(self) -> str:
        """변경 타입별 색상 (RGB Hex)"""
        colors = {
            DxfChangeType.ADDED: "#00FF00",  # 녹색
            DxfChangeType.DELETED: "#FF0000",  # 빨강
            DxfChangeType.MODIFIED: "#FFA500",  # 주황
        }
        return colors.get(self.change_type, "#808080")

    @property
    def color_rgb(self) -> Tuple[int, int, int]:
        """변경 타입별 색상 (RGB 튜플)"""
        colors = {
            DxfChangeType.ADDED: (0, 255, 0),
            DxfChangeType.DELETED: (255, 0, 0),
            DxfChangeType.MODIFIED: (255, 165, 0),
        }
        return colors.get(self.change_type, (128, 128, 128))

    @property
    def icon(self) -> str:
        """변경 타입별 아이콘"""
        icons = {
            DxfChangeType.ADDED: "➕",
            DxfChangeType.DELETED: "➖",
            DxfChangeType.MODIFIED: "✏️",
        }
        return icons.get(self.change_type, "?")

    def is_dimension_change(self) -> bool:
        """치수 변경 여부"""
        return self.entity_type == "DIMENSION"

    def get_measurement_change(self) -> Optional[str]:
        """치수 측정값 변경 문자열 (예: "1500 → 1600 (+100)")"""
        if not self.is_dimension_change():
            return None
        old_val = (self.old_data or {}).get("measurement", 0)
        new_val = (self.new_data or {}).get("measurement", 0)
        diff = new_val - old_val
        if abs(diff) < 0.01:
            return None
        return f"{old_val:.1f} → {new_val:.1f} ({'+' if diff > 0 else ''}{diff:.1f})"

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        result = {
            "entity_type": self.entity_type,
            "layer": self.layer,
            "change_type": self.change_type.value,
            "old_data": self.old_data,
            "new_data": self.new_data,
            "location": self.location,
            "measurement_diff": self.measurement_diff,
            # Phase 3 P3-1: 변경 상세 정보
            "change_detail": self.change_detail,
            "change_category": self.change_category,
            "old_location": self.old_location,
        }
        # Phase 3+ Priority Scoring
        if self.priority_score:
            result["priority_score"] = self.priority_score.to_dict()

        return result


class _IndexedChangeEntity:
    """Small adapter used by SpatialIndex for DxfChange point locations."""

    __slots__ = ("dxf", "change_id")

    def __init__(self, x: float, y: float):
        self.dxf = SimpleNamespace(insert=(x, y, 0))
        self.change_id: Optional[int] = None

    def dxftype(self) -> str:
        return "TEXT"


@dataclass
class LayerStatistics:
    """레이어별 변경 통계

    Phase 3 P3-4.3: 레이어 단위 통계 집계

    Attributes:
        layer: 레이어 이름
        priority: 우선순위 (critical/high/medium/low)
        added_count: 추가된 엔티티 수
        deleted_count: 삭제된 엔티티 수
        modified_count: 수정된 엔티티 수
        layer_move_count: 레이어 이동 엔티티 수
    """
    layer: str
    priority: str = "medium"
    added_count: int = 0
    deleted_count: int = 0
    modified_count: int = 0
    layer_move_count: int = 0

    @property
    def total_changes(self) -> int:
        """레이어의 총 변경 수"""
        return self.added_count + self.deleted_count + self.modified_count + self.layer_move_count

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            "layer": self.layer,
            "priority": self.priority,
            "added_count": self.added_count,
            "deleted_count": self.deleted_count,
            "modified_count": self.modified_count,
            "layer_move_count": self.layer_move_count,
            "total_changes": self.total_changes,
        }


@dataclass
class DxfComparisonResult:
    """DXF 비교 결과

    Attributes:
        changes: 변경 사항 목록
        stats: 통계 정보
        layer_statistics: 레이어별 변경 통계 (Phase 3 P3-4)
        priority_summary: 우선순위별 변경 수 요약 (Phase 3 P3-4)
    """

    changes: List[DxfChange] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Phase 3 P3-4: 레이어/우선순위 통계
    layer_statistics: Dict[str, "LayerStatistics"] = field(default_factory=dict)
    priority_summary: Dict[str, int] = field(default_factory=dict)

    @property
    def total_changes(self) -> int:
        counts = self.stats.get("change_counts") or {}
        if counts:
            return int(counts.get("added", 0)) + int(counts.get("deleted", 0)) + int(counts.get("modified", 0))
        return len(self.changes)

    @property
    def added_count(self) -> int:
        counts = self.stats.get("change_counts") or {}
        if counts:
            return int(counts.get("added", 0))
        return sum(1 for c in self.changes if c.change_type == DxfChangeType.ADDED)

    @property
    def deleted_count(self) -> int:
        counts = self.stats.get("change_counts") or {}
        if counts:
            return int(counts.get("deleted", 0))
        return sum(1 for c in self.changes if c.change_type == DxfChangeType.DELETED)

    @property
    def modified_count(self) -> int:
        counts = self.stats.get("change_counts") or {}
        if counts:
            return int(counts.get("modified", 0))
        return sum(1 for c in self.changes if c.change_type == DxfChangeType.MODIFIED)

    @property
    def layer_move_count(self) -> int:
        """레이어 이동 변경 수 (Phase 3 P3-4)"""
        return sum(
            1 for c in self.changes
            if c.change_type == DxfChangeType.MODIFIED
            and c.change_category == "layer_move"
        )

    def filter_by_layer(self, layer: str) -> List[DxfChange]:
        """특정 레이어의 변경 사항만 필터링"""
        return [c for c in self.changes if c.layer == layer]

    def filter_by_type(self, entity_type: str) -> List[DxfChange]:
        """특정 엔티티 타입의 변경 사항만 필터링"""
        return [c for c in self.changes if c.entity_type == entity_type]

    def filter_by_priority(self, priority: str) -> List[DxfChange]:
        """특정 우선순위의 변경 사항만 필터링 (Phase 3 P3-4)

        Args:
            priority: "critical", "high", "medium", "low"

        Returns:
            해당 우선순위의 변경 목록
        """
        layer_to_priority = {
            layer: stat.priority for layer, stat in self.layer_statistics.items()
        }
        return [
            c for c in self.changes
            if layer_to_priority.get(c.layer, "medium") == priority
        ]

    def get_layers(self) -> List[str]:
        """변경된 레이어 목록"""
        return list(set(c.layer for c in self.changes))

    def get_layers_by_priority(self) -> Dict[str, List[str]]:
        """우선순위별 레이어 목록 (Phase 3 P3-4)

        Returns:
            {"critical": [...], "high": [...], "medium": [...], "low": [...]}
        """
        result: Dict[str, List[str]] = {"critical": [], "high": [], "medium": [], "low": []}
        for layer, stat in self.layer_statistics.items():
            if stat.priority in result:
                result[stat.priority].append(layer)
        return result

    def get_summary(self) -> str:
        """요약 텍스트"""
        base = (
            f"총 {self.total_changes}개 변경: "
            f"추가 {self.added_count}, "
            f"삭제 {self.deleted_count}, "
            f"수정 {self.modified_count}"
        )
        # Phase 3 P3-4: 우선순위 요약 추가
        if self.priority_summary:
            priority_str = ", ".join(
                f"{k}: {v}" for k, v in self.priority_summary.items() if v > 0
            )
            if priority_str:
                base += f" | 우선순위: {priority_str}"
        return base

    # =========================================================================
    # Phase 3+ Priority Score 기반 메서드
    # =========================================================================

    def sort_by_priority(self, reverse: bool = True) -> List[DxfChange]:
        """우선순위 점수 기준으로 변경 사항 정렬

        Args:
            reverse: True면 높은 우선순위가 먼저 (기본값)

        Returns:
            정렬된 변경 사항 목록
        """
        def get_sort_key(change: DxfChange) -> Tuple[int, float]:
            """정렬 키: (우선순위 레벨 값, 세부 점수)"""
            if change.priority_score:
                return (
                    change.priority_score.priority_level.value,
                    change.priority_score.priority_score,
                )
            return (0, 0.0)  # priority_score 없으면 최하위

        return sorted(self.changes, key=get_sort_key, reverse=reverse)

    def get_top_changes(
        self,
        top_n: int = 10,
        min_priority: Optional[PriorityLevel] = None,
    ) -> List[DxfChange]:
        """상위 N개 중요 변경 사항 반환

        Args:
            top_n: 반환할 최대 개수
            min_priority: 최소 우선순위 레벨 (None이면 필터 없음)

        Returns:
            상위 변경 사항 목록
        """
        sorted_changes = self.sort_by_priority()

        if min_priority:
            sorted_changes = [
                c for c in sorted_changes
                if c.priority_score and c.priority_score.priority_level.value >= min_priority.value
            ]

        return sorted_changes[:top_n]

    def filter_by_priority_level(self, priority_level: PriorityLevel) -> List[DxfChange]:
        """특정 PriorityLevel의 변경 사항만 필터링

        Args:
            priority_level: PriorityLevel 열거형 값

        Returns:
            해당 우선순위의 변경 목록
        """
        return [
            c for c in self.changes
            if c.priority_score and c.priority_score.priority_level == priority_level
        ]

    def get_review_needed_changes(self) -> List[DxfChange]:
        """검토 필요 변경 사항만 반환

        Returns:
            review_needed=True인 변경 목록
        """
        return [
            c for c in self.changes
            if c.priority_score and c.priority_score.review_needed
        ]

    def get_priority_statistics(self) -> Dict[str, Any]:
        """우선순위 점수 통계

        Returns:
            우선순위별 개수, 평균 신뢰도, 검토 필요 수 등
        """
        stats = {
            "by_priority": {level.name: 0 for level in PriorityLevel},
            "review_needed": 0,
            "avg_confidence": 0.0,
            "low_confidence_count": 0,
            "changes_with_score": 0,
        }

        total_confidence = 0.0
        scored_count = 0

        for change in self.changes:
            if change.priority_score:
                scored_count += 1
                stats["by_priority"][change.priority_score.priority_level.name] += 1
                if change.priority_score.review_needed:
                    stats["review_needed"] += 1
                total_confidence += change.priority_score.confidence_score
                if change.priority_score.confidence_score < 0.7:
                    stats["low_confidence_count"] += 1

        stats["changes_with_score"] = scored_count
        if scored_count > 0:
            stats["avg_confidence"] = round(total_confidence / scored_count, 4)

        return stats


class DxfComparator:
    """DXF 엔티티 비교 엔진

    두 DXF 문서의 엔티티를 해시 기반으로 비교합니다.
    R-tree 공간 인덱싱으로 텍스트 위치 기반 매칭 성능을 최적화합니다.

    사용 예시:
        comparator = DxfComparator()
        result = comparator.compare(entities_a, entities_b)

        # R-tree 기반 공간 매칭 사용
        comparator = DxfComparator(use_spatial_index=True)
        result = comparator.compare(entities_a, entities_b)
    """

    # Sprint 11 P3: 좌표 비교 허용 오차 (mm)
    TOLERANCE = 0.1  # 0.1mm 이내 차이는 동일로 간주

    # Phase 3 P3-1: 민감도 임계값 상수 (기본값)
    # 이 값 미만의 변경은 무시 가능한 수준으로 판단
    DEFAULT_SENSITIVITY = {
        "position": 1.0,      # mm - 위치 변경 임계값
        "dimension": 1.0,     # mm - 치수 변경 임계값
        "dimension_rel": 0.1, # % - 치수 상대 변경 임계값
        "rotation": 0.1,      # ° - 회전 변경 임계값
        "scale": 0.1,         # % - 스케일 변경 임계값
        # Phase Q6 (RV-20260509-002) — structural layer 별 더 엄격한 위치
        # 임계값. legacy ``DxfComparator()`` no-config caller 도 Q6 이
        # 활성화되도록 default 에 포함. 0.0 이면 layer-aware 비활성.
        "structural_position": 0.1,
    }

    # 변경 카테고리 정의
    CHANGE_CATEGORIES = {
        "dimension": "치수 변경",
        "position": "위치 이동",
        "rotation": "회전 변경",
        "scale": "스케일 변경",
        "content": "내용 변경",
        "layer": "레이어 변경",
    }
    _rtree_auto_fallback_logged = False
    _LARGE_MODE_NEAR_MATCH_ENTITY_TYPES = frozenset(
        {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION", "MULTILEADER", "INSERT"}
    )
    _LARGE_MODE_ALIGNMENT_ENTITY_TYPES = _LARGE_MODE_NEAR_MATCH_ENTITY_TYPES
    _LARGE_MODE_ALIGNMENT_MAX_PER_TYPE = 1500
    _LARGE_MODE_ALIGNMENT_MAX_TOTAL = 5000

    def __init__(
        self,
        ignore_layers: Optional[List[str]] = None,
        tolerance: float = 0.1,
        use_spatial_index: bool = True,
        sensitivity: Optional[Dict[str, float]] = None,
        config: Optional[ComparisonConfig] = None,
    ):
        """
        Args:
            ignore_layers: 무시할 레이어 목록 (예: ['Defpoints', '0'])
                config.layer_priority와 병합됨
            tolerance: 좌표 비교 허용 오차 (mm) - 해시 매칭 시 사용
            use_spatial_index: R-tree 공간 인덱싱 사용 여부 (기본 True)
            sensitivity: 민감도 임계값 딕셔너리 (하위 호환성용)
                config 파라미터가 우선함
            config: ComparisonConfig 설정 객체 (권장)
                None이면 기본 설정 사용
        """
        # Phase 3 P3-2: ComparisonConfig 통합
        self._config = config or get_default_config()

        # 레이어 설정: 명시적 ignore_layers와 config 병합
        self.ignore_layers = set(ignore_layers or [])
        self._layer_priority = self._config.layer_priority

        # Config에서 설정 읽기
        self.tolerance = tolerance
        use_spatial_index_config = self._config.use_spatial_index if config else use_spatial_index
        self._use_near_match_index = bool(use_spatial_index_config)
        self.near_match_index = getattr(self._config, "near_match_index", "auto")
        self.use_spatial_index = use_spatial_index_config and RTREE_AVAILABLE
        self._last_index_backend = "none"
        self.change_zone_stream_path: Optional[Path] = None
        self.change_zone_stream_pair_id: str = ""
        # Plan §16 Phase C-3.1 — time-to-first-stream instrumentation. Reset
        # per-compare in ``compare()`` so a reused comparator instance does not
        # leak the previous run's timing into the next.
        self._compare_start_perf: Optional[float] = None
        self._stream_first_write_perf: Optional[float] = None

        # Phase 3 P3-2: SensitivityConfig 기반 민감도 설정
        if config is not None:
            # config가 주어지면 SensitivityConfig 사용
            self.sensitivity = self._config_to_sensitivity_dict(self._config.sensitivity)
        elif sensitivity is not None:
            # 하위 호환: 명시적 sensitivity 딕셔너리
            self.sensitivity = {**self.DEFAULT_SENSITIVITY, **sensitivity}
        else:
            # 기본값
            self.sensitivity = dict(self.DEFAULT_SENSITIVITY)

        if use_spatial_index_config and not RTREE_AVAILABLE:
            requested_backend = (self.near_match_index or "auto").lower()
            if requested_backend in {"grid", "linear"}:
                pass
            elif requested_backend == "rtree":
                logger.warning(
                    "R-tree 공간 인덱싱이 명시적으로 요청되었으나 rtree가 설치되지 않았습니다. "
                    "GridSpatialIndex fallback으로 전환됩니다."
                )
            elif not DxfComparator._rtree_auto_fallback_logged:
                logger.info(
                    "rtree가 설치되지 않아 near-match 자동 모드에서 GridSpatialIndex fallback을 사용합니다."
                )
                DxfComparator._rtree_auto_fallback_logged = True

        # Phase 3+ Priority Calculator 초기화
        self._priority_calculator = get_default_calculator()

    def _config_to_sensitivity_dict(self, sens_config: SensitivityConfig) -> Dict[str, float]:
        """SensitivityConfig를 legacy sensitivity dict로 변환

        Args:
            sens_config: SensitivityConfig 인스턴스

        Returns:
            하위 호환 sensitivity 딕셔너리
        """
        return {
            "position": sens_config.position_threshold,
            "dimension": sens_config.dimension_abs_threshold,
            "dimension_rel": sens_config.dimension_rel_threshold * 100,  # 비율 → 퍼센트
            "rotation": sens_config.rotation_threshold,
            "scale": sens_config.scale_threshold * 100,  # 비율 → 퍼센트
            # Phase P (RV-20260508-013) — alignment artifact guard 임계
            "alignment_strict_inlier_ratio": getattr(
                sens_config, "alignment_strict_inlier_ratio", 0.85
            ),
            "alignment_protect_structural_layers": getattr(
                sens_config, "alignment_protect_structural_layers", True
            ),
            # Phase P — TEXT/DIMENSION near-match 확장 radius
            "text_near_match_radius": getattr(
                sens_config, "text_near_match_radius", 50.0
            ),
            # Phase Q6 (RV-20260509-002) — structural layer 별 더 엄격한 위치
            # 임계값. 0.0 이면 비활성 (legacy 동작).
            "structural_position": getattr(
                sens_config, "structural_position_threshold", 0.1
            ),
        }

    # ------------------------------------------------------------------
    # Phase P (RV-20260508-013) — entity-type 별 near-match radius
    # ------------------------------------------------------------------

    _TEXT_LIKE_ENTITY_TYPES = frozenset({"TEXT", "MTEXT", "DIMENSION", "MULTILEADER"})

    def _distance_threshold_for(self, entity_type: str) -> float:
        """entity 종류에 따라 near-match 허용 거리 (mm).

        TEXT/MTEXT/DIMENSION/MULTILEADER 는 ``text_near_match_radius``
        (default 50mm) 까지 매칭 허용 — 좌표 시프트 + 내용 변경의 동시
        발생을 added+deleted 분리로 잘못 처리하지 않도록.

        나머지 entity 는 ``self.tolerance`` 그대로 (alignment 확장값 포함).
        """
        if entity_type in self._TEXT_LIKE_ENTITY_TYPES:
            text_radius = float(
                self.sensitivity.get("text_near_match_radius", 50.0)
            )
            return max(self.tolerance, text_radius)
        return self.tolerance

    @property
    def config(self) -> ComparisonConfig:
        """현재 설정 반환"""
        return self._config

    @staticmethod
    def _group_entities_by_hash(
        entities: List[NormalizedEntity],
    ) -> Dict[str, deque]:
        grouped: Dict[str, deque] = defaultdict(deque)
        for entity in entities:
            grouped[entity.hash].append(entity)
        return grouped

    @staticmethod
    def _report_progress(
        progress_callback: Optional[Callable[[int, int, str], None]],
        current: int,
        total: int,
        message: str,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> bool:
        if callable(is_cancelled) and is_cancelled():
            return False
        if progress_callback is not None:
            progress_callback(current, total, message)
        if callable(is_cancelled) and is_cancelled():
            return False
        return True

    @staticmethod
    def _mark_cancelled(result: DxfComparisonResult) -> DxfComparisonResult:
        result.metadata["cancelled"] = True
        result.stats["cancelled"] = True
        return result

    @staticmethod
    def _refresh_change_count_stats(result: DxfComparisonResult) -> None:
        # Codex P2 [RV-20260508-006] — cosmetic-suppress 후 stats 일관성.
        # 기존에는 ``change_counts`` 만 다시 계산했지만 ``by_type`` /
        # ``by_layer`` 는 그대로 두어 suppress 된 cosmetic 변경이
        # downstream report 에 여전히 modified 로 노출되었음. 동일한
        # truth (result.changes) 로부터 세 stats 를 모두 재계산.
        counts = {"added": 0, "deleted": 0, "modified": 0}
        by_type: Dict[str, Dict[str, int]] = {}
        by_layer: Dict[str, Dict[str, int]] = {}

        def _bucket_for(d: Dict[str, Dict[str, int]], key: str) -> Dict[str, int]:
            if key not in d:
                d[key] = {"added": 0, "deleted": 0, "modified": 0}
            return d[key]

        for change in result.changes:
            ttype_bucket = _bucket_for(by_type, change.entity_type)
            tlayer_bucket = _bucket_for(by_layer, change.layer)
            if change.change_type == DxfChangeType.ADDED:
                counts["added"] += 1
                ttype_bucket["added"] += 1
                tlayer_bucket["added"] += 1
            elif change.change_type == DxfChangeType.DELETED:
                counts["deleted"] += 1
                ttype_bucket["deleted"] += 1
                tlayer_bucket["deleted"] += 1
            elif change.change_type == DxfChangeType.MODIFIED:
                counts["modified"] += 1
                ttype_bucket["modified"] += 1
                tlayer_bucket["modified"] += 1

        result.stats["change_counts"] = counts
        # Preserve unrelated keys; only overwrite recomputable ones.
        result.stats["by_type"] = by_type
        result.stats["by_layer"] = by_layer

    # ------------------------------------------------------------------
    # Plan §15 Phase C-1 — comparator hot-loop peak instrumentation
    # ------------------------------------------------------------------
    # External auditor #2 finding (CRITICAL):
    #   ``compare()`` accumulates ALL changes into ``result.changes`` BEFORE
    #   ``_finalize_large_result()`` truncates. The in-memory cap is a
    #   post-hoc truncate, not a peak bound — memory measurements after
    #   the fact cannot detect a 5GB spike during accumulation.
    #
    # ``stats["peak_changes_pre_truncate"]`` records the maximum length
    # of ``result.changes`` reached during the comparator hot loop. It is
    # written BEFORE ``_finalize_large_result()`` truncates, so the metric
    # survives truncation and exposes the true peak to operators.
    #
    # Invariant (asserted at end of compare()):
    #   stats["peak_changes_pre_truncate"] >= len(result.changes)
    #
    # Hot-loop overhead: previously these helpers did dict ``get`` + branch +
    # dict ``set`` per call. Plan §16 Phase C-3.2 measured the production
    # overhead at +7.8% on a 50k-change synthetic compare, which violates the
    # ≤1% gate.
    #
    # Optimisation (Plan §16 Phase C-3.3): exploit the monotonic property of
    # ``result.changes`` inside ``compare()`` — the list only grows during
    # the compare loop and is never truncated before ``_finalize_large_result``
    # runs. Therefore the maximum length is simply ``len(result.changes)``
    # captured ONCE at the end of the loop (before suppress/finalize). The
    # helpers shrink back to pure list mutation; compare() does the single
    # ``stats["peak_changes_pre_truncate"] = len(result.changes)`` assignment
    # before the ``result.stats = stats`` rebind.
    #
    # Result: hot-loop overhead drops to the cost of a single attribute
    # lookup + list.append (i.e. effectively zero against the baseline).
    # Re-measured at +<1% in Phase C-3.3 follow-up.
    #
    # Note: keep the helpers as named methods (not inlined) so external
    # tests + ``scripts/benchmark_dxf_comparator_overhead.py`` can still
    # monkey-patch them, and so the contract is documented in one place.
    @staticmethod
    def _record_change(result: "DxfComparisonResult", change: "DxfChange") -> None:
        """Append a single change. Hot-loop helper — keep minimal."""
        result.changes.append(change)

    @staticmethod
    def _record_changes(
        result: "DxfComparisonResult", changes: "List[DxfChange]"
    ) -> None:
        """Extend changes by a batch. Hot-loop helper — keep minimal."""
        result.changes.extend(changes)

    def _get_large_mode_context(
        self,
        entities_a: Dict[str, List[NormalizedEntity]],
        entities_b: Dict[str, List[NormalizedEntity]],
    ) -> Dict[str, Any]:
        count_a = sum(len(v) for v in entities_a.values())
        count_b = sum(len(v) for v in entities_b.values())
        total = count_a + count_b
        mode = getattr(self._config, "large_drawing_mode", "auto")
        threshold = int(getattr(self._config, "large_entity_threshold", 100000) or 100000)
        estimated_mb = (total * 500) / (1024 * 1024)
        available_mb = self._available_memory_mb()
        memory_pressure = available_mb is not None and estimated_mb > available_mb * 0.5
        active = mode == "force" or (mode == "auto" and (total >= threshold or memory_pressure))
        return {
            "large_drawing_mode": "active" if active else "off",
            "large_drawing_requested_mode": mode,
            "large_entity_threshold": threshold,
            "large_entity_count": total,
            "entity_count_a": count_a,
            "entity_count_b": count_b,
            "estimated_entity_memory_mb": round(estimated_mb, 2),
            "available_memory_mb": round(available_mb, 2) if available_mb is not None else None,
        }

    @staticmethod
    def _available_memory_mb() -> Optional[float]:
        try:
            import psutil

            return psutil.virtual_memory().available / (1024 * 1024)
        except Exception:
            return None

    def _finalize_large_result(self, result: DxfComparisonResult) -> None:
        # Plan §15 Phase C-1 invariant:
        #   ``result.stats["peak_changes_pre_truncate"]`` is populated by
        #   ``_record_change``/``_record_changes`` during ``compare()``'s
        #   hot loop, i.e. BEFORE this method runs. Therefore the
        #   truncation below cannot reduce the peak — only the post-truncate
        #   ``len(result.changes)`` shrinks. Operators get both the peak
        #   (true memory pressure) and the post-truncate count (publishable
        #   detail) from the same ``result.stats`` dict.
        self._write_change_zone_stream(result)
        active = result.metadata.get("large_drawing_mode") == "active"
        limit = int(getattr(self._config, "max_change_records_in_memory", 50000) or 0)
        result.metadata.setdefault("truncated_changes", False)
        result.metadata.setdefault("omitted_change_counts", {"added": 0, "deleted": 0, "modified": 0})
        if not active or limit <= 0 or len(result.changes) <= limit:
            return

        omitted = {"added": 0, "deleted": 0, "modified": 0}
        for change in result.changes[limit:]:
            if change.change_type == DxfChangeType.ADDED:
                omitted["added"] += 1
            elif change.change_type == DxfChangeType.DELETED:
                omitted["deleted"] += 1
            elif change.change_type == DxfChangeType.MODIFIED:
                omitted["modified"] += 1
        result.changes = result.changes[:limit]
        result.metadata["truncated_changes"] = True
        result.metadata["omitted_change_counts"] = omitted
        result.stats["truncated_changes"] = True
        result.stats["omitted_change_counts"] = omitted

    def configure_change_zone_stream(
        self,
        stream_path: Optional[Union[str, Path]],
        *,
        pair_id: str = "",
    ) -> None:
        self.change_zone_stream_path = Path(stream_path).resolve() if stream_path else None
        self.change_zone_stream_pair_id = pair_id

    def _write_change_zone_stream(self, result: DxfComparisonResult) -> None:
        if not self.change_zone_stream_path:
            return
        if result.metadata.get("change_zone_stream_path"):
            return
        # Plan §16 Phase C-3.1 — capture first-stream latency BEFORE the actual
        # write so disk I/O time is excluded. ``_compare_start_perf`` is set in
        # ``compare()`` entry. First-occurrence wins (only set when None) so
        # rare re-entrant cases keep the genuine first record's timing.
        if (
            self._stream_first_write_perf is None
            and self._compare_start_perf is not None
        ):
            self._stream_first_write_perf = time.perf_counter()
            result.stats["time_to_first_stream_record_ms"] = (
                self._stream_first_write_perf - self._compare_start_perf
            ) * 1000.0
        try:
            from .change_zones import write_change_zone_stream

            stream_metadata = write_change_zone_stream(
                result.changes,
                self.change_zone_stream_path,
                pair_id=self.change_zone_stream_pair_id,
            )
            result.metadata.update(stream_metadata)
            result.stats.update(stream_metadata)
        except Exception as exc:
            logger.exception("Failed to write change-zone stream")
            result.metadata.update(
                {
                    "change_zone_stream_path": str(self.change_zone_stream_path),
                    "change_zone_record_count": 0,
                    "change_zone_stream_complete": False,
                    "change_zone_stream_error": str(exc),
                }
            )
            result.stats.update(
                {
                    "change_zone_record_count": 0,
                    "change_zone_stream_complete": False,
                    "change_zone_stream_error": str(exc),
                }
            )

    @classmethod
    def from_config(cls, config: ComparisonConfig) -> "DxfComparator":
        """ComparisonConfig로부터 인스턴스 생성 (권장 팩토리 메서드)

        Args:
            config: ComparisonConfig 설정

        Returns:
            DxfComparator 인스턴스
        """
        return cls(
            tolerance=0.1,  # tolerance는 config에서 관리하지 않음
            config=config,
        )

    def _calculate_change_priority(
        self,
        change_type: str,
        layer: str,
        entity_data: Optional[Dict[str, Any]] = None,
    ) -> PriorityScoreData:
        """변경 사항의 우선순위 점수 계산

        Args:
            change_type: 변경 유형 (ADDED, DELETED, MODIFIED)
            layer: 레이어명
            entity_data: 엔티티 데이터 (신뢰도 요소 추출용)

        Returns:
            PriorityScoreData 인스턴스
        """
        # 신뢰도 요소 추출
        confidence_factors = ConfidenceFactors()

        if entity_data:
            # OCR 신뢰도 (TEXT 엔티티)
            if "ocr_confidence" in entity_data:
                confidence_factors.ocr_confidence = entity_data["ocr_confidence"]

            # SSIM 점수 (이미지 비교 결과가 있는 경우)
            if "ssim_score" in entity_data:
                confidence_factors.ssim_score = entity_data["ssim_score"]

            # 매칭 거리 (near-match 결과)
            if "match_distance" in entity_data:
                confidence_factors.match_distance = entity_data["match_distance"]

        return self._priority_calculator.calculate(
            change_type=change_type,
            layer_name=layer,
            confidence_factors=confidence_factors,
        )

    def compare(
        self,
        entities_a: Dict[str, List[NormalizedEntity]],
        entities_b: Dict[str, List[NormalizedEntity]],
        _finalize_large: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DxfComparisonResult:
        """두 엔티티 세트 비교

        Args:
            entities_a: 기준(Old) 엔티티
            entities_b: 대상(New) 엔티티

        Returns:
            비교 결과
        """
        result = DxfComparisonResult()
        # Plan §16 Phase C-3.1 — anchor wall time and reset the first-stream
        # capture per-compare so a reused comparator instance cannot bleed the
        # previous run's timing into this one. ``_stream_first_write_perf``
        # being None signals "no record streamed yet" to ``_write_change_zone_stream``.
        self._compare_start_perf = time.perf_counter()
        self._stream_first_write_perf = None
        result.metadata.update(self._get_large_mode_context(entities_a, entities_b))
        # Plan §16 Phase C-3.3 — the monotonic-peak optimisation moved peak
        # tracking from per-call helper logic to a single-shot len() at the
        # end of the compare loop (see helpers above + below). No init
        # needed; the local ``stats`` dict (built next) defaults to 0.

        # 통계 초기화
        stats = {
            "entities_a": 0,
            "entities_b": 0,
            "by_type": {},
            "by_layer": {},
            # Plan §15 Phase C-1 — local key carried into result.stats below
            # so that ``result.stats = stats`` (further down) cannot lose the
            # peak. Helpers write the live peak to ``result.stats``; we copy
            # the latest value into the local dict just before the overwrite.
            "peak_changes_pre_truncate": 0,
        }

        # 모든 엔티티 타입 순회
        all_types = sorted(set(entities_a.keys()) | set(entities_b.keys()))
        if not self._report_progress(
            progress_callback,
            0,
            max(1, len(all_types)),
            "DXF entity hash compare started",
            is_cancelled,
        ):
            return self._mark_cancelled(result)

        for type_index, entity_type in enumerate(all_types, start=1):
            if not self._report_progress(
                progress_callback,
                type_index - 1,
                len(all_types),
                f"DXF entity hash compare: {entity_type}",
                is_cancelled,
            ):
                return self._mark_cancelled(result)
            list_a = entities_a.get(entity_type, [])
            list_b = entities_b.get(entity_type, [])

            # 무시할 레이어 필터링 (Phase 3 P3-2: LayerPriorityConfig 통합)
            list_a = [
                e for e in list_a
                if e.layer not in self.ignore_layers
                and not self._layer_priority.should_ignore(e.layer)
            ]
            list_b = [
                e for e in list_b
                if e.layer not in self.ignore_layers
                and not self._layer_priority.should_ignore(e.layer)
            ]

            stats["entities_a"] += len(list_a)
            stats["entities_b"] += len(list_b)

            # 해시 기반 비교
            hashes_a = self._group_entities_by_hash(list_a)
            hashes_b = self._group_entities_by_hash(list_b)
            added_entities: List[NormalizedEntity] = []
            deleted_entities: List[NormalizedEntity] = []
            cosmetic_pairs: List[Tuple[NormalizedEntity, NormalizedEntity]] = []
            for entity_hash in sorted(set(hashes_a) | set(hashes_b)):
                queue_a = hashes_a.get(entity_hash, deque())
                queue_b = hashes_b.get(entity_hash, deque())
                matched_count = min(len(queue_a), len(queue_b))
                for _ in range(matched_count):
                    entity_a = queue_a.popleft()
                    entity_b = queue_b.popleft()
                    # Phase O3 — hash 일치(좌표 동일) 페어의 cosmetic 비교
                    cosmetic_pairs.append((entity_a, entity_b))
                deleted_entities.extend(queue_a)
                added_entities.extend(queue_b)

            # Phase O3 — cosmetic 변경을 별도 채널로 기록
            # Plan §15 Phase C-1: route all hot-loop mutations through
            # ``_record_change``/``_record_changes`` so ``result.stats``
            # tracks the pre-truncate peak.
            cosmetic_changes_for_type = self._collect_cosmetic_changes(
                cosmetic_pairs, entity_type
            )
            self._record_changes(result, cosmetic_changes_for_type)
            data_changes_for_type = self._collect_matched_data_changes(
                cosmetic_pairs, entity_type
            )
            self._record_changes(result, data_changes_for_type)

            # 추가된 엔티티 (B에만 존재)
            for entity in added_entities:
                # Phase 3+ Priority Score 계산
                priority = self._calculate_change_priority(
                    change_type="ADDED",
                    layer=entity.layer,
                    entity_data=entity.data,
                )
                self._record_change(
                    result,
                    DxfChange(
                        entity_type=entity_type,
                        layer=entity.layer,
                        change_type=DxfChangeType.ADDED,
                        new_data=entity.data,
                        location=entity.location,
                        priority_score=priority,
                    ),
                )

            # 삭제된 엔티티 (A에만 존재)
            for entity in deleted_entities:
                # Phase 3+ Priority Score 계산
                priority = self._calculate_change_priority(
                    change_type="DELETED",
                    layer=entity.layer,
                    entity_data=entity.data,
                )
                self._record_change(
                    result,
                    DxfChange(
                        entity_type=entity_type,
                        layer=entity.layer,
                        change_type=DxfChangeType.DELETED,
                        old_data=entity.data,
                        location=entity.location,
                        priority_score=priority,
                    ),
                )

            # 타입별 통계
            type_added = len(added_entities)
            type_deleted = len(deleted_entities)
            type_modified = len(cosmetic_changes_for_type) + len(data_changes_for_type)
            if type_added > 0 or type_deleted > 0 or type_modified > 0:
                stats["by_type"][entity_type] = {
                    "added": type_added,
                    "deleted": type_deleted,
                    "modified": type_modified,
                }

            if not self._report_progress(
                progress_callback,
                type_index,
                len(all_types),
                f"DXF entity hash compare done: {entity_type}",
                is_cancelled,
            ):
                return self._mark_cancelled(result)

        # 레이어별 통계
        for change in result.changes:
            layer = change.layer
            if layer not in stats["by_layer"]:
                stats["by_layer"][layer] = {"added": 0, "deleted": 0, "modified": 0}

            if change.change_type == DxfChangeType.ADDED:
                stats["by_layer"][layer]["added"] += 1
            elif change.change_type == DxfChangeType.DELETED:
                stats["by_layer"][layer]["deleted"] += 1
            elif change.change_type == DxfChangeType.MODIFIED:
                stats["by_layer"][layer]["modified"] += 1

        # Plan §16 Phase C-3.3 — monotonic peak: ``result.changes`` only grows
        # inside the compare loop above (no truncation happens until
        # ``_finalize_large_result`` runs further down), so the maximum length
        # is simply ``len(result.changes)`` captured here, ONCE per compare.
        # This replaces the per-call dict.get + branch + dict.set the helpers
        # used to do — measured production overhead dropped from +7.8% to
        # ≤1% on the 50k-change synthetic (Phase C-3.2 benchmark).
        stats["peak_changes_pre_truncate"] = len(result.changes)
        # Plan §16 Phase C-3.1 — same gotcha pattern for the streaming first-
        # write latency: ``_write_change_zone_stream`` may have written the key
        # to ``result.stats`` before this rebind, so we copy the live value
        # into the local ``stats`` dict so it survives the wholesale overwrite.
        # ``.get(... default=None)`` because non-streaming runs never set it.
        stats["time_to_first_stream_record_ms"] = result.stats.get(
            "time_to_first_stream_record_ms"
        )
        result.stats = stats
        # Phase O3 — cosmetic suppress (config 토글 시) 는 통계/finalize 전에 적용
        self._suppress_cosmetic_only(result)
        self._refresh_change_count_stats(result)
        result.stats.update(
            {
                "large_drawing_mode": result.metadata["large_drawing_mode"],
                "truncated_changes": False,
                "omitted_change_counts": {"added": 0, "deleted": 0, "modified": 0},
            }
        )
        if _finalize_large:
            # NOTE (Plan §15 Phase C-1): ``_finalize_large_result`` truncates
            # ``result.changes`` AFTER ``peak_changes_pre_truncate`` is
            # already populated, so the peak metric survives truncation and
            # exposes the true in-flight memory pressure.
            self._finalize_large_result(result)

        # Plan §15 Phase C-1 invariant — the peak must dominate any
        # post-truncate length. If this ever fires, the helpers/route was
        # bypassed somewhere and the metric is unsafe to publish.
        assert result.stats.get("peak_changes_pre_truncate", 0) >= len(
            result.changes
        ), (
            "peak_changes_pre_truncate must be >= post-truncate "
            f"len(result.changes); got peak="
            f"{result.stats.get('peak_changes_pre_truncate', 0)}, "
            f"len={len(result.changes)}"
        )

        logger.info(result.get_summary())
        logger.debug(f"엔티티 A: {stats['entities_a']}, B: {stats['entities_b']}")

        return result

    def compare_and_filter(
        self,
        entities_a: Dict[str, List[NormalizedEntity]],
        entities_b: Dict[str, List[NormalizedEntity]],
        min_changes: int = 1,
    ) -> DxfComparisonResult:
        """비교 후 최소 변경 개수 필터링

        Args:
            entities_a: 기준 엔티티
            entities_b: 대상 엔티티
            min_changes: 최소 변경 개수 (미만이면 빈 결과)

        Returns:
            비교 결과 (필터링됨)
        """
        result = self.compare(entities_a, entities_b)

        if result.total_changes < min_changes:
            logger.info(
                f"변경 개수({result.total_changes})가 최소값({min_changes}) 미만 - 동일 도면"
            )
            return DxfComparisonResult(stats=result.stats)

        return result

    # =========================================================================
    # Sprint 11 P3: 정밀도 개선 헬퍼 메서드
    # =========================================================================
    def _is_location_match(
        self,
        loc_a: Optional[Tuple[float, float]],
        loc_b: Optional[Tuple[float, float]],
    ) -> bool:
        """두 좌표가 tolerance 범위 내인지 확인 (거리 기반)

        Args:
            loc_a: 첫 번째 좌표 (x, y)
            loc_b: 두 번째 좌표 (x, y)

        Returns:
            tolerance 이내면 True
        """
        if loc_a is None or loc_b is None:
            return False

        distance = math.sqrt((loc_a[0] - loc_b[0]) ** 2 + (loc_a[1] - loc_b[1]) ** 2)
        return distance <= self.tolerance

    def _build_change_spatial_index(
        self,
        changes: List[DxfChange],
    ) -> Tuple[SpatialIndex, Dict[int, DxfChange]]:
        """DxfChange 리스트로부터 공간 인덱스 구축

        Args:
            changes: DxfChange 리스트

        Returns:
            (SpatialIndex, {내부ID: DxfChange} 매핑)
        """
        idx = SpatialIndex()
        id_to_change: Dict[int, DxfChange] = {}

        for change in changes:
            if change.location is None:
                continue

            # Mock 엔티티 생성 (SpatialIndex는 dxftype() 필요)
            x, y = change.location
            mock_entity = _IndexedChangeEntity(x, y)

            try:
                internal_id = idx.insert(mock_entity)
                mock_entity.change_id = internal_id
                id_to_change[internal_id] = change
            except ValueError:
                continue

        return idx, id_to_change

    def find_near_matches(
        self,
        deleted: List[DxfChange],
        added: List[DxfChange],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> List[Tuple[DxfChange, DxfChange]]:
        """해시 불일치 시 거리 기반으로 잠재적 매칭 찾기

        R-tree 공간 인덱싱 사용 시 O(n log n), 미사용 시 O(n²).

        Args:
            deleted: 삭제된 변경 목록
            added: 추가된 변경 목록

        Returns:
            (deleted, added) 쌍 리스트 - 위치가 유사한 엔티티들
        """
        if not deleted or not added:
            return []
        if not self._report_progress(
            progress_callback,
            0,
            max(1, len(deleted)),
            "DXF near-match index started",
            is_cancelled,
        ):
            return []

        # R-tree 공간 인덱싱 사용
        backend = self._select_near_match_backend()
        self._last_index_backend = backend
        if backend == "rtree":
            return self._find_near_matches_rtree(
                deleted,
                added,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
            )
        if backend == "grid":
            return self._find_near_matches_grid(
                deleted,
                added,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
            )
        return self._find_near_matches_linear(
            deleted,
            added,
            progress_callback=progress_callback,
            is_cancelled=is_cancelled,
        )

    def _select_near_match_backend(self) -> str:
        requested = (self.near_match_index or "auto").lower()
        if not self._use_near_match_index or requested == "linear":
            return "linear"
        if requested == "grid":
            return "grid"
        if requested == "rtree":
            return "rtree" if RTREE_AVAILABLE else "grid"
        return "rtree" if RTREE_AVAILABLE else "grid"

    # ------------------------------------------------------------------
    # Phase O3 — cosmetic change channel
    # ------------------------------------------------------------------

    def _collect_cosmetic_changes(
        self,
        pairs: List[Tuple[NormalizedEntity, NormalizedEntity]],
        entity_type: str,
    ) -> List[DxfChange]:
        """좌표 일치 페어들에서 cosmetic 차이만 추출해 DxfChange 로 반환.

        모든 cosmetic 차이는 ``DxfChangeType.MODIFIED`` + ``change_category=
        "cosmetic"`` 으로 기록 (enum 신규 값 도입 회피 — backward-compat).
        ``change_detail`` 에 변경된 속성 요약을 한 줄로 기록.

        ``SensitivityConfig.cosmetic_detection_enabled=False`` 또는
        ``cosmetic_attributes`` 가 빈 시퀀스면 빈 리스트 반환.
        """
        if self._config is None:
            return []
        sensitivity = getattr(self._config, "sensitivity", None)
        if sensitivity is None:
            return []
        if not getattr(sensitivity, "cosmetic_detection_enabled", True):
            return []

        attrs = tuple(getattr(sensitivity, "cosmetic_attributes",
                              ("color", "lineweight", "linetype")))
        if not attrs:
            return []

        out: List[DxfChange] = []
        for entity_a, entity_b in pairs:
            diffs: Dict[str, Tuple[Any, Any]] = {}
            for attr in attrs:
                old_val = getattr(entity_a, attr, None)
                new_val = getattr(entity_b, attr, None)
                if old_val is None and new_val is None:
                    continue  # 둘 다 미설정 — 차이 아님
                if old_val != new_val:
                    diffs[attr] = (old_val, new_val)
            if not diffs:
                continue

            detail_parts = [f"{k}: {v[0]!r} → {v[1]!r}" for k, v in diffs.items()]
            out.append(
                DxfChange(
                    entity_type=entity_type,
                    layer=entity_a.layer,
                    change_type=DxfChangeType.MODIFIED,
                    old_data=dict(entity_a.data) if entity_a.data else None,
                    new_data=dict(entity_b.data) if entity_b.data else None,
                    location=entity_a.location,
                    change_detail="; ".join(detail_parts),
                    change_category="cosmetic",
                )
            )
        return out

    def _collect_matched_data_changes(
        self,
        pairs: List[Tuple[NormalizedEntity, NormalizedEntity]],
        entity_type: str,
    ) -> List[DxfChange]:
        """Emit content modifications for hash-matched attribute entities."""

        if entity_type not in {"ATTRIB", "ATTDEF"}:
            return []

        out: List[DxfChange] = []
        for entity_a, entity_b in pairs:
            old_data = dict(entity_a.data) if entity_a.data else {}
            new_data = dict(entity_b.data) if entity_b.data else {}
            if old_data == new_data:
                continue

            categories, details = self._analyze_change_details(
                old_data=old_data,
                new_data=new_data,
                entity_type=entity_type,
                old_loc=entity_a.location,
                new_loc=entity_b.location,
                layer=entity_a.layer,
            )
            pos_diff = self._calculate_position_diff(entity_a.location, entity_b.location)
            if not self._is_significant_change(
                categories, old_data, new_data, pos_diff, layer=entity_a.layer
            ):
                continue

            priority = self._calculate_change_priority(
                change_type="MODIFIED",
                layer=entity_a.layer,
                entity_data=new_data,
            )
            out.append(
                DxfChange(
                    entity_type=entity_type,
                    layer=entity_a.layer,
                    change_type=DxfChangeType.MODIFIED,
                    old_data=old_data,
                    new_data=new_data,
                    location=entity_b.location,
                    change_detail="; ".join(details),
                    change_category=",".join(categories),
                    old_location=entity_a.location,
                    priority_score=priority,
                )
            )

        return out

    def _suppress_cosmetic_only(self, result: "DxfComparisonResult") -> None:
        """SensitivityConfig.suppress_cosmetic_only=True 면 cosmetic 변경을
        result.changes 에서 제거하고 stats 에 카운터만 기록."""
        if self._config is None:
            return
        sensitivity = getattr(self._config, "sensitivity", None)
        if sensitivity is None or not getattr(sensitivity, "suppress_cosmetic_only", False):
            return

        before = len(result.changes)
        result.changes = [
            c for c in result.changes
            if not (
                c.change_type == DxfChangeType.MODIFIED
                and c.change_category == "cosmetic"
            )
        ]
        suppressed = before - len(result.changes)
        if suppressed:
            result.stats["cosmetic_suppressed"] = suppressed
            result.metadata["cosmetic_suppressed_count"] = suppressed

    # ------------------------------------------------------------------
    # Phase O2 — global rigid alignment helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sample_entities(items: List[NormalizedEntity], limit: int) -> List[NormalizedEntity]:
        if len(items) <= limit:
            return list(items)
        if limit <= 1:
            return items[:1]
        step = (len(items) - 1) / (limit - 1)
        return [items[int(round(index * step))] for index in range(limit)]

    def _limit_large_alignment_entities(
        self,
        entities: Dict[str, List[NormalizedEntity]],
    ) -> Dict[str, List[NormalizedEntity]]:
        limited: Dict[str, List[NormalizedEntity]] = {}
        remaining = self._LARGE_MODE_ALIGNMENT_MAX_TOTAL
        for entity_type in sorted(self._LARGE_MODE_ALIGNMENT_ENTITY_TYPES):
            if remaining <= 0:
                break
            items = entities.get(entity_type) or []
            if not items:
                continue
            per_type_limit = min(self._LARGE_MODE_ALIGNMENT_MAX_PER_TYPE, remaining)
            sampled = self._sample_entities(items, per_type_limit)
            if sampled:
                limited[entity_type] = sampled
                remaining -= len(sampled)
        return limited

    def _estimate_global_alignment(
        self,
        entities_a: Dict[str, List[NormalizedEntity]],
        entities_b: Dict[str, List[NormalizedEntity]],
    ) -> Optional["RigidTransform"]:  # noqa: F821 — forward ref to local import
        """Phase O2 — SensitivityConfig 토글 + ignore-layers 필터 후 추정.

        P0-1: sets the transient ``self._alignment_low_confidence`` flag when
        the estimator rejects the alignment (returns None) DESPITE enough
        geometry to attempt it — i.e. a low-inlier rejection rather than
        "feature disabled" or "too little data". The call site copies the
        flag into ``result.metadata`` so the GUI badge can surface it. This
        does not change estimate_rigid_transform's contract.
        """
        self._alignment_low_confidence = False
        if self._config is None or not getattr(
            self._config.sensitivity, "global_alignment_enabled", True
        ):
            return None

        # ignore_layers / LayerPriorityConfig.should_ignore 통과한 entity 만
        def _filter(d: Dict[str, List[NormalizedEntity]]) -> Dict[str, List[NormalizedEntity]]:
            out: Dict[str, List[NormalizedEntity]] = {}
            for et, lst in d.items():
                kept = [
                    e for e in lst
                    if e.layer not in self.ignore_layers
                    and not self._layer_priority.should_ignore(e.layer)
                ]
                if kept:
                    out[et] = kept
            return out

        filtered_a = _filter(entities_a)
        filtered_b = _filter(entities_b)
        context = self._get_large_mode_context(filtered_a, filtered_b)
        if context.get("large_drawing_mode") == "active":
            filtered_a = self._limit_large_alignment_entities(filtered_a)
            filtered_b = self._limit_large_alignment_entities(filtered_b)
            candidate_total = sum(len(v) for v in filtered_a.values()) + sum(
                len(v) for v in filtered_b.values()
            )
            if candidate_total < 8:
                logger.info(
                    "large drawing alignment skipped: only %d sampled structural anchors",
                    candidate_total,
                )
                return None

        try:
            from .global_alignment import estimate_rigid_transform  # local import
        except ImportError:
            return None

        # Enough geometry on both sides to expect a real attempt? Below this
        # an empty result is "too little data", not a low-confidence reject —
        # don't cry wolf. Mirrors the large-mode anchor floor used above.
        _MIN_ANCHORS = 8
        candidate_total = sum(len(v) for v in filtered_a.values()) + sum(
            len(v) for v in filtered_b.values()
        )

        try:
            transform = estimate_rigid_transform(filtered_a, filtered_b)
        except Exception:
            logger.exception("global alignment estimation failed — continuing without")
            return None

        if transform is None and candidate_total >= _MIN_ANCHORS:
            # Estimator rejected the alignment (e.g. inlier ratio < threshold)
            # even though there was ample geometry → low confidence.
            self._alignment_low_confidence = True
        return transform

    def _is_pure_alignment_artifact(
        self,
        d_change: DxfChange,
        a_change: DxfChange,
        alignment: "RigidTransform",  # noqa: F821 — forward ref
    ) -> bool:
        """deleted→added 변환이 global alignment 와 일치하면 noise.

        부호 주의:
        - ``alignment`` 는 cv2 가 추정한 ``B → A`` 변환 (B 좌표에 적용해 A
          좌표 얻음). dx = -0.5 면 "B 가 A 보다 +0.5 만큼 시프트됨" 의미.
        - ``displacement`` 는 ``a_change.location - d_change.location``
          = B 위치 − A 위치 = ``A → B`` 변위. 즉 alignment 와 반대 부호.

        따라서 ``displacement ≈ -alignment.translation`` 이면 alignment
        artifact. 검사: ``|dx + alignment.dx| < tol`` (양변 부호 합).

        rotation 은 단일 점 변위만으론 추정 어려워 검사 생략 — 시프트가
        대부분이라 실용적으로 충분 (Phase O2 first version).

        Phase P (RV-20260508-013) — inlier_ratio guard 추가:

        ``alignment.inlier_ratio < strict_threshold`` (default 0.85):
        RANSAC 가 추정한 alignment 가 부분 매칭 (50-85% inlier) 이면
        "도면 일부만 시프트, 나머지는 의도적 변경" 가능성. 이 경우
        모든 변경을 alignment 로 흡수하면 사용자가 의도한 zone-level
        shift 가 silent drop. → False 반환 (artifact 아님으로 간주).

        Codex RV-20260509 P1 — 초기 P1 패치는 ``protect_structural_layers``
        라는 두 번째 guard 도 가졌으나, 그게 fixture 03 (전체 도면 0.5mm
        시프트, 변경 0건) 처럼 inlier_ratio=1.0 인 진짜 글로벌 시프트
        에서도 BEAM layer 모든 변경을 보존시켜 5건의 false-positive 를
        만들었음. partial shift 의심은 이미 inlier_ratio guard 가 잡고,
        수치 displacement 가 alignment 와 일치하면 layer 와 무관하게
        registration noise 로 분류해야 함. ``alignment_protect_structural_
        layers`` 플래그는 backward-compat 위해 유지하되 default False
        으로 변경 + 코드에서도 이제 비활성.
        """
        if d_change.location is None or a_change.location is None:
            return False

        strict_threshold = float(
            self.sensitivity.get("alignment_strict_inlier_ratio", 0.85)
        )
        inlier_ratio = float(getattr(alignment, "inlier_ratio", 1.0) or 1.0)
        if inlier_ratio < strict_threshold:
            return False  # 부분 시프트 의심 — 변경 보존

        dx = a_change.location[0] - d_change.location[0]  # A→B 변위
        dy = a_change.location[1] - d_change.location[1]
        # Phase Q-FU-2 (RV-20260510-001) — layer-aware threshold 적용.
        # 기존엔 ``position`` (1.0mm) 만 사용 → BEAM 의 0.5mm shift 가
        # alignment artifact 로 흡수되어 silent drop. Q6 가
        # ``_is_significant_change`` / ``_analyze_change_details`` 만
        # 수정하고 alignment guard 는 누락. 구조 layer 에 더 엄격한
        # ``structural_position`` (0.1mm) 적용 → BEAM 0.5mm sub-mm
        # shift 가 alignment 흡수되지 않고 보존됨 → Q6 활성.
        layer = self._extract_change_layer(d_change) or self._extract_change_layer(a_change)
        threshold = self._position_threshold_for_layer(layer)
        return (
            abs(dx + alignment.dx) < threshold  # alignment 는 B→A (반대 부호)
            and abs(dy + alignment.dy) < threshold
        )

    @staticmethod
    def _extract_change_layer(change: Any) -> str:
        """Phase Q-FU-2 helper — DxfChange 의 ``.layer`` 필드 우선,
        ``metadata['layer']`` fallback (legacy ChangeRecord-style toy 호환).
        """
        layer = getattr(change, "layer", None)
        if layer:
            return str(layer)
        meta = getattr(change, "metadata", None)
        if isinstance(meta, dict):
            return str(meta.get("layer") or "")
        return ""

    # ------------------------------------------------------------------
    # Phase O2 — hybrid Hungarian assignment helpers
    # ------------------------------------------------------------------

    def _resolve_candidates_to_pairs(
        self,
        candidate_edges: List[Tuple[int, int, float]],
        deleted: List[DxfChange],
        added: List[DxfChange],
    ) -> List[Tuple[DxfChange, DxfChange]]:
        """Phase O2 — candidate 그래프를 hybrid Hungarian으로 1:1 매칭.

        candidate_edges: ``(deleted_idx, added_idx, distance)`` 튜플 리스트.
        이미 entity_type / layer / tolerance 필터를 통과한 후보들만 들어
        있다고 가정.

        Algorithm:
        1. candidate 그래프의 connected component 로 sub-cluster 분할.
        2. 각 sub-cluster 의 |deleted| × |added| 가 ``max_subset`` 이하면
           scipy.linear_sum_assignment 로 비용 최소 매칭. 없는 edge 는
           큰 비용(distance ≫ tolerance) 으로 채워 미매칭 처리.
        3. 큰 sub-cluster 는 greedy fallback (distance 오름차순).
        4. scipy 미설치 → 전체 greedy fallback.

        Returns:
            (deleted, added) 페어 리스트.
        """
        if not candidate_edges:
            return []

        max_subset = self._get_hungarian_max_subset()

        # Sub-cluster 분할 (Union-Find)
        clusters = self._split_into_clusters(candidate_edges, len(deleted), len(added))

        results: List[Tuple[DxfChange, DxfChange]] = []
        used_d: set = set()
        used_a: set = set()

        for d_indices, a_indices, edges in clusters:
            cluster_size = max(len(d_indices), len(a_indices))
            if cluster_size <= max_subset:
                pairs = self._hungarian_for_cluster(d_indices, a_indices, edges)
            else:
                pairs = self._greedy_for_cluster(edges)

            for d_idx, a_idx in pairs:
                if d_idx in used_d or a_idx in used_a:
                    continue
                used_d.add(d_idx)
                used_a.add(a_idx)
                results.append((deleted[d_idx], added[a_idx]))

        return results

    def _split_into_clusters(
        self,
        edges: List[Tuple[int, int, float]],
        n_deleted: int,
        n_added: int,
    ) -> List[Tuple[List[int], List[int], List[Tuple[int, int, float]]]]:
        """Connected components on bipartite candidate graph.

        deleted 정점과 added 정점을 (n_deleted + n_added) 개 정점으로 보고,
        edge 가 있는 정점만 모은 cluster 리스트 반환. 고립된 정점은 cluster
        에 포함되지 않음 (매칭 불가능 — caller 가 added/deleted 그대로 둠).

        Returns:
            cluster 마다 ``(deleted_indices, added_indices, edges)`` 튜플.
        """
        # Union-Find
        parent = list(range(n_deleted + n_added))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        for d_idx, a_idx, _ in edges:
            union(d_idx, n_deleted + a_idx)

        groups: Dict[int, Tuple[List[int], List[int], List[Tuple[int, int, float]]]] = {}
        for d_idx, a_idx, dist in edges:
            root = find(d_idx)
            if root not in groups:
                groups[root] = ([], [], [])
            groups[root][2].append((d_idx, a_idx, dist))

        # vertex 별 cluster 멤버십 (edge 가 있는 vertex 만)
        for d_idx in range(n_deleted):
            root = find(d_idx)
            if root in groups and d_idx not in groups[root][0]:
                groups[root][0].append(d_idx)
        for a_idx in range(n_added):
            root = find(n_deleted + a_idx)
            if root in groups and a_idx not in groups[root][1]:
                groups[root][1].append(a_idx)

        return list(groups.values())

    def _hungarian_for_cluster(
        self,
        d_indices: List[int],
        a_indices: List[int],
        edges: List[Tuple[int, int, float]],
    ) -> List[Tuple[int, int]]:
        """scipy.linear_sum_assignment 기반 최적 매칭.

        edge 가 없는 (d, a) 페어는 ``∞`` 비용 → 미매칭. 큐 사이즈는 caller
        가 max_subset 이하로 보장.
        """
        try:
            from scipy.optimize import linear_sum_assignment  # type: ignore
            import numpy as np  # type: ignore
        except ImportError:
            return self._greedy_for_cluster(edges)

        if not d_indices or not a_indices:
            return []

        d_pos = {d: i for i, d in enumerate(d_indices)}
        a_pos = {a: j for j, a in enumerate(a_indices)}
        n_rows = len(d_indices)
        n_cols = len(a_indices)

        # 비용 행렬: edge 거리, edge 없으면 INF (큰 값)
        BIG = 1e9
        cost = np.full((n_rows, n_cols), BIG, dtype=np.float64)
        for d_idx, a_idx, dist in edges:
            i = d_pos[d_idx]
            j = a_pos[a_idx]
            # 같은 (i, j) 에 여러 edge 가 들어와도 최단 거리만 보존
            if dist < cost[i][j]:
                cost[i][j] = float(dist)

        try:
            row_ind, col_ind = linear_sum_assignment(cost)
        except ValueError:
            # numpy 매트릭스가 모두 INF 인 등 비정상 경우
            return self._greedy_for_cluster(edges)

        pairs: List[Tuple[int, int]] = []
        for i, j in zip(row_ind.tolist(), col_ind.tolist()):
            if cost[i][j] >= BIG:
                continue  # edge 가 없는 페어 — 매칭 거부
            pairs.append((d_indices[i], a_indices[j]))
        return pairs

    @staticmethod
    def _greedy_for_cluster(
        edges: List[Tuple[int, int, float]],
    ) -> List[Tuple[int, int]]:
        """거리 오름차순 greedy 매칭 (scipy 미설치 또는 cluster 너무 큼)."""
        sorted_edges = sorted(edges, key=lambda e: (e[2], e[0], e[1]))
        used_d: set = set()
        used_a: set = set()
        pairs: List[Tuple[int, int]] = []
        for d_idx, a_idx, _ in sorted_edges:
            if d_idx in used_d or a_idx in used_a:
                continue
            used_d.add(d_idx)
            used_a.add(a_idx)
            pairs.append((d_idx, a_idx))
        return pairs

    def _get_hungarian_max_subset(self) -> int:
        """SensitivityConfig 의 hungarian_max_subset 우선, 없으면 default 200."""
        if self._config is not None and hasattr(self._config, "sensitivity"):
            return getattr(self._config.sensitivity, "hungarian_max_subset", 200)
        return 200

    # ------------------------------------------------------------------
    # Spatial-index backed candidate collection (Phase O2 refactored)
    # ------------------------------------------------------------------

    def _find_near_matches_rtree(
        self,
        deleted: List[DxfChange],
        added: List[DxfChange],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> List[Tuple[DxfChange, DxfChange]]:
        """R-tree 기반 candidate 수집 + hybrid Hungarian 매칭 (Phase O2)."""
        added_idx, id_to_added = self._build_change_spatial_index(added)

        if not id_to_added:
            logger.debug("공간 인덱스 구축 실패 (위치 정보 없음), 선형 검색으로 대체")
            self._last_index_backend = "grid"
            return self._find_near_matches_grid(
                deleted,
                added,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
            )

        # added 의 internal_id → list index 매핑 (Hungarian 인덱스 통일)
        added_id_to_index = {id(change): i for i, change in enumerate(added)}

        candidate_edges: List[Tuple[int, int, float]] = []
        # Phase P (RV-20260508-013) — TEXT/DIMENSION 도 같은 sweep 에서
        # 후보 모집되도록 sweep radius 를 두 임계값의 max 로 설정. 후속
        # entity_type filter 가 정상 동작.
        sweep_radius = max(self.tolerance, self._distance_threshold_for("TEXT"))
        for d_idx, d_change in enumerate(deleted):
            if d_idx % 1000 == 0 and not self._report_progress(
                progress_callback,
                d_idx,
                len(deleted),
                f"DXF near-match scan: {d_idx:,}/{len(deleted):,}",
                is_cancelled,
            ):
                return []
            if d_change.location is None:
                continue

            x, y = d_change.location
            candidates = added_idx.find_near_point((x, y, 0), tolerance=sweep_radius)
            for indexed_entity in candidates:
                internal_id = getattr(indexed_entity, "change_id", None)
                if internal_id is None:
                    continue
                candidate_change = id_to_added.get(internal_id)
                if candidate_change is None:
                    continue
                if d_change.entity_type != candidate_change.entity_type:
                    continue
                if d_change.layer != candidate_change.layer:
                    continue
                distance = self._calculate_position_diff(
                    d_change.location, candidate_change.location
                )
                if distance is None:
                    continue
                # Phase P — entity_type 별 임계값 사용 (TEXT 50mm, 그 외 그대로)
                threshold = self._distance_threshold_for(d_change.entity_type)
                if distance > threshold:
                    continue
                a_idx = added_id_to_index.get(id(candidate_change))
                if a_idx is None:
                    continue
                candidate_edges.append((d_idx, a_idx, float(distance)))

        if not self._report_progress(
            progress_callback,
            len(deleted),
            len(deleted),
            f"DXF near-match resolving: {len(candidate_edges):,} candidates",
            is_cancelled,
        ):
            return []
        matches = self._resolve_candidates_to_pairs(candidate_edges, deleted, added)
        if matches:
            logger.info(
                "[R-tree + Hungarian] matched %d changes (tolerance=%.3fmm, candidates=%d)",
                len(matches), self.tolerance, len(candidate_edges),
            )
        return matches

    def _find_near_matches_grid(
        self,
        deleted: List[DxfChange],
        added: List[DxfChange],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> List[Tuple[DxfChange, DxfChange]]:
        """Grid spatial index 기반 candidate 수집 + hybrid Hungarian (Phase O2).

        Codex RV-20260509 P2 — TEXT/DIMENSION 의 50mm 확장 radius 가 grid
        backend 에서도 적용되도록 grid 의 tolerance 를 최대값으로 설정.
        Per-type filter 는 후속 단계 (line ~1554) 에서 `_distance_threshold_
        for(entity_type)` 가 처리. 이전에는 grid 가 ``self.tolerance`` (1mm)
        만 사용해 TEXT 30mm shift 가 grid fallback 에서 매칭 안 되던 회귀.
        """
        # Grid 의 query() 가 self.tolerance 로 distance cap → TEXT 같이
        # 더 큰 radius 를 원하는 경우 grid 를 큰 값으로 만들어야 함.
        sweep_radius = max(self.tolerance, self._distance_threshold_for("TEXT"))
        grid = GridSpatialIndex(tolerance=sweep_radius)
        grid.bulk_insert(added)
        if len(grid) == 0:
            self._last_index_backend = "linear"
            return self._find_near_matches_linear(
                deleted,
                added,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
            )

        added_id_to_index = {id(change): i for i, change in enumerate(added)}
        candidate_edges: List[Tuple[int, int, float]] = []

        for d_idx, d_change in enumerate(deleted):
            # Phase P — entity_type 별 거리 임계 (TEXT/DIMENSION 50mm)
            threshold = self._distance_threshold_for(d_change.entity_type)
            for distance, _item_id, a_change in grid.query(d_change):
                if distance > threshold:
                    continue
                a_idx = added_id_to_index.get(id(a_change))
                if a_idx is None:
                    continue
                candidate_edges.append((d_idx, a_idx, float(distance)))

        matches = self._resolve_candidates_to_pairs(candidate_edges, deleted, added)
        if matches:
            logger.info(
                "[grid + Hungarian] matched %d changes (tolerance=%.3fmm, candidates=%d)",
                len(matches), self.tolerance, len(candidate_edges),
            )
        return matches

    def _find_near_matches_linear(
        self,
        deleted: List[DxfChange],
        added: List[DxfChange],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> List[Tuple[DxfChange, DxfChange]]:
        """선형 검색 candidate 수집 + hybrid Hungarian (Phase O2 fallback)."""
        candidate_edges: List[Tuple[int, int, float]] = []

        for d_idx, d_change in enumerate(deleted):
            if d_change.location is None:
                continue
            # Phase P — entity_type 별 거리 임계 (TEXT/DIMENSION 50mm)
            threshold = self._distance_threshold_for(d_change.entity_type)
            for a_idx, a_change in enumerate(added):
                if d_change.entity_type != a_change.entity_type:
                    continue
                if d_change.layer != a_change.layer:
                    continue
                distance = self._calculate_position_diff(
                    d_change.location, a_change.location
                )
                if distance is None or distance > threshold:
                    continue
                candidate_edges.append((d_idx, a_idx, float(distance)))

        matches = self._resolve_candidates_to_pairs(candidate_edges, deleted, added)
        if matches:
            logger.info(
                "[linear + Hungarian] matched %d changes (tolerance=%.3fmm, candidates=%d)",
                len(matches), self.tolerance, len(candidate_edges),
            )
        return matches

    # =========================================================================
    # Phase 3 P3-1: MODIFIED 변경 상세 정보 생성
    # =========================================================================
    def _calculate_position_diff(
        self,
        old_loc: Optional[Tuple[float, float]],
        new_loc: Optional[Tuple[float, float]],
    ) -> Optional[float]:
        """두 위치 간 거리(mm) 계산

        Args:
            old_loc: 이전 위치 (x, y)
            new_loc: 새 위치 (x, y)

        Returns:
            거리(mm), 계산 불가 시 None
        """
        if old_loc is None or new_loc is None:
            return None
        return math.sqrt((new_loc[0] - old_loc[0]) ** 2 + (new_loc[1] - old_loc[1]) ** 2)

    def _analyze_change_details(
        self,
        old_data: Optional[Dict[str, Any]],
        new_data: Optional[Dict[str, Any]],
        entity_type: str,
        old_loc: Optional[Tuple[float, float]],
        new_loc: Optional[Tuple[float, float]],
        layer: str = "",
    ) -> Tuple[List[str], List[str]]:
        """변경 상세 분석

        old_data/new_data의 차이를 분석하여 변경 카테고리와 설명 생성.

        Args:
            old_data: 이전 엔티티 데이터
            new_data: 새 엔티티 데이터
            entity_type: 엔티티 타입
            old_loc: 이전 위치
            new_loc: 새 위치
            layer: Phase Q6 (RV-20260509-002) — entity layer. 구조 layer
                일 때 ``structural_position_threshold`` 적용 (default 0.1mm).
                비-zero 일 때만 활성. 비어있으면 ``position_threshold``
                (default 1.0mm) 사용 (legacy 동작).

        Returns:
            (카테고리 리스트, 설명 리스트) 튜플
        """
        categories: List[str] = []
        details: List[str] = []

        old_data = old_data or {}
        new_data = new_data or {}

        # 1. 위치 변경 분석 — Phase Q6: layer-aware threshold 사용.
        # 구조 layer 의 sub-mm shift 가 categories 에 누락되는 것을 방지
        # (Codex Q6 round-1 P2-1 fix).
        pos_diff = self._calculate_position_diff(old_loc, new_loc)
        position_threshold = self._position_threshold_for_layer(layer)
        if pos_diff is not None and pos_diff >= position_threshold:
            categories.append("position")
            details.append(f"위치 이동 {pos_diff:.1f}mm")

        # 2. 치수 변경 분석 (DIMENSION 전용)
        if entity_type == "DIMENSION":
            old_meas = old_data.get("measurement", 0)
            new_meas = new_data.get("measurement", 0)
            if old_meas and new_meas:
                meas_diff = abs(new_meas - old_meas)
                # 절대값 또는 상대값 임계값 초과 시
                rel_diff = (meas_diff / old_meas * 100) if old_meas != 0 else 0
                if meas_diff >= self.sensitivity["dimension"] or rel_diff >= self.sensitivity["dimension_rel"]:
                    categories.append("dimension")
                    sign = "+" if new_meas > old_meas else ""
                    details.append(f"{old_meas:.1f} → {new_meas:.1f} ({sign}{new_meas - old_meas:.1f})")

        # 3. 회전 변경 분석
        old_rot = old_data.get("rotation", 0)
        new_rot = new_data.get("rotation", 0)
        if old_rot != new_rot:
            rot_diff = abs(new_rot - old_rot)
            if rot_diff >= self.sensitivity["rotation"]:
                categories.append("rotation")
                details.append(f"회전 {old_rot:.1f}° → {new_rot:.1f}°")

        # 4. 스케일 변경 분석 (INSERT 전용)
        if entity_type == "INSERT":
            old_xscale = old_data.get("xscale", 1.0)
            old_yscale = old_data.get("yscale", 1.0)
            new_xscale = new_data.get("xscale", 1.0)
            new_yscale = new_data.get("yscale", 1.0)
            scale_diff_x = abs(new_xscale - old_xscale) / old_xscale * 100 if old_xscale else 0
            scale_diff_y = abs(new_yscale - old_yscale) / old_yscale * 100 if old_yscale else 0
            if scale_diff_x >= self.sensitivity["scale"] or scale_diff_y >= self.sensitivity["scale"]:
                categories.append("scale")
                details.append(f"스케일 ({old_xscale:.2f}, {old_yscale:.2f}) → ({new_xscale:.2f}, {new_yscale:.2f})")

        # 5. 텍스트 내용 변경 분석
        if entity_type in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
            old_content = old_data.get("content", "")
            new_content = new_data.get("content", "")
            if old_content != new_content:
                categories.append("content")
                # 긴 텍스트는 줄임
                old_short = old_content[:20] + "..." if len(old_content) > 20 else old_content
                new_short = new_content[:20] + "..." if len(new_content) > 20 else new_content
                details.append(f'내용 "{old_short}" → "{new_short}"')

        # 6. 블록명 변경 (INSERT)
        if entity_type == "INSERT":
            old_block = old_data.get("block_name", "")
            new_block = new_data.get("block_name", "")
            if old_block != new_block:
                categories.append("content")
                details.append(f'블록 "{old_block}" → "{new_block}"')

        # 카테고리/상세 없으면 기본값
        if not categories:
            categories.append("content")
        if not details:
            details.append("데이터 변경")

        return categories, details

    def _is_significant_change(
        self,
        categories: List[str],
        old_data: Optional[Dict[str, Any]],
        new_data: Optional[Dict[str, Any]],
        pos_diff: Optional[float],
        layer: str = "",
    ) -> bool:
        """변경이 유의미한지 판단 (민감도 기준)

        Args:
            categories: 변경 카테고리 리스트
            old_data: 이전 데이터
            new_data: 새 데이터
            pos_diff: 위치 변경량 (mm)
            layer: Phase Q6 (RV-20260509-002) — entity layer 이름.
                구조 layer 이면 더 엄격한 ``structural_position`` 임계
                값 적용 (default 0.1mm). 비-zero 일 때만 활성.

        Returns:
            유의미하면 True, 무시할 수준이면 False
        """
        # 위치 변경만 있고 임계값 미만이면 무시
        if categories == ["position"] and pos_diff is not None:
            threshold = self._position_threshold_for_layer(layer)
            if pos_diff < threshold:
                return False

        # 추가 민감도 체크는 _analyze_change_details에서 이미 수행됨
        return True

    def _position_threshold_for_layer(self, layer: str) -> float:
        """Phase Q6 (RV-20260509-002) — layer-aware 위치 임계값.

        구조 layer (기둥/보/가새/벽 등) 이면 ``structural_position`` 적용,
        그 외엔 default ``position`` 사용. ``structural_position`` = 0.0
        이면 layer-aware 비활성 (legacy 동작).
        """
        default_threshold = float(self.sensitivity.get("position", 1.0))
        structural_threshold = float(
            self.sensitivity.get("structural_position", 0.0)
        )
        if structural_threshold <= 0.0 or not layer:
            return default_threshold
        try:
            from src.services.comparison.structural_layer_patterns import (
                is_structural_layer,
            )
        except Exception:
            return default_threshold
        if is_structural_layer(layer):
            return structural_threshold
        return default_threshold

    def _create_modified_change(
        self,
        d_change: DxfChange,
        a_change: DxfChange,
    ) -> Optional[DxfChange]:
        """매칭된 (삭제, 추가) 쌍으로부터 MODIFIED DxfChange 생성

        변경 상세 분석을 수행하고, 민감도 미만의 변경은 None 반환.

        Args:
            d_change: 삭제된 변경 (Old 엔티티)
            a_change: 추가된 변경 (New 엔티티)

        Returns:
            MODIFIED DxfChange 또는 None (무시 가능한 변경 시)
        """
        old_data = d_change.old_data
        new_data = a_change.new_data
        old_loc = d_change.location
        new_loc = a_change.location

        # 변경 상세 분석 — Phase Q6: layer 전달로 구조 layer 의 sub-mm
        # shift 가 categories 에 정확히 분류되도록 함.
        categories, details = self._analyze_change_details(
            old_data=old_data,
            new_data=new_data,
            entity_type=d_change.entity_type,
            old_loc=old_loc,
            new_loc=new_loc,
            layer=d_change.layer,
        )

        # 위치 변경량 계산
        pos_diff = self._calculate_position_diff(old_loc, new_loc)

        # 민감도 체크
        if not self._is_significant_change(
            categories, old_data, new_data, pos_diff, layer=d_change.layer
        ):
            logger.debug(
                f"[MODIFIED 무시] 민감도 미만: {d_change.entity_type} "
                f"pos_diff={pos_diff:.3f}mm" if pos_diff else ""
            )
            return None

        # 치수 측정값 차이 계산
        measurement_diff = None
        if d_change.entity_type == "DIMENSION":
            old_meas = (old_data or {}).get("measurement", 0)
            new_meas = (new_data or {}).get("measurement", 0)
            measurement_diff = new_meas - old_meas

        # Phase 3+ Priority Score 계산
        priority = self._calculate_change_priority(
            change_type="MODIFIED",
            layer=d_change.layer,
            entity_data=new_data,
        )

        return DxfChange(
            entity_type=d_change.entity_type,
            layer=d_change.layer,
            change_type=DxfChangeType.MODIFIED,
            old_data=old_data,
            new_data=new_data,
            location=new_loc,  # 새 위치 사용
            measurement_diff=measurement_diff,
            # Phase 3 P3-1: 변경 상세 정보
            change_detail="; ".join(details),
            change_category=",".join(categories),
            old_location=old_loc,  # 이전 위치 보존
            # Phase 3+ Priority Score
            priority_score=priority,
        )

    def compare_with_modified_detection(
        self,
        entities_a: Dict[str, List[NormalizedEntity]],
        entities_b: Dict[str, List[NormalizedEntity]],
        near_match_tolerance: Optional[float] = None,
        finalize_for_large: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DxfComparisonResult:
        """MODIFIED 탐지를 포함한 향상된 비교

        해시 불일치 시 R-tree 기반 공간 매칭으로 수정된 엔티티를 탐지합니다.
        민감도 임계값 미만의 변경은 MODIFIED에서 제외됩니다.

        Phase O2 — 도면 전체 rigid 시프트(예: 0.5mm 평행이동) 추정 후
        그 시프트만큼의 MODIFIED 는 alignment artifact 로 분류해 자동
        suppress. 결과 metadata 에 ``applied_global_shift_mm`` 기록.

        Args:
            entities_a: 기준(Old) 엔티티
            entities_b: 대상(New) 엔티티
            near_match_tolerance: 공간 매칭 허용 오차 (mm)
                기본값: sensitivity["position"] (1.0mm)

        Returns:
            MODIFIED 포함 비교 결과 (change_detail, change_category 포함)
        """
        # Phase O2 — global rigid alignment estimation
        if not self._report_progress(
            progress_callback,
            0,
            4,
            "DXF alignment estimate",
            is_cancelled,
        ):
            return self._mark_cancelled(DxfComparisonResult())

        alignment = self._estimate_global_alignment(entities_a, entities_b)

        # 기본 해시 비교 수행
        result = self.compare(
            entities_a,
            entities_b,
            _finalize_large=False,
            progress_callback=progress_callback,
            is_cancelled=is_cancelled,
        )
        if result.metadata.get("cancelled"):
            return result

        # alignment metadata 기록 (significant 여부와 무관하게 진단용)
        if alignment is not None:
            result.metadata["alignment"] = alignment.to_dict()
            if alignment.is_significant:
                result.metadata["applied_global_shift_mm"] = (alignment.dx, alignment.dy)
        elif getattr(self, "_alignment_low_confidence", False):
            # P0-1: estimator rejected the alignment despite ample geometry.
            # Surface it so the GUI badge warns the user instead of silently
            # comparing un-aligned. Matching is unchanged (no tolerance edit).
            result.metadata["alignment_low_confidence"] = True

        # 삭제/추가 분리
        deleted = [c for c in result.changes if c.change_type == DxfChangeType.DELETED]
        added = [c for c in result.changes if c.change_type == DxfChangeType.ADDED]

        if not deleted or not added:
            if finalize_for_large:
                self._finalize_large_result(result)
            return result

        # Phase 3 P3-1: near-match tolerance 업데이트
        # 기본값은 sensitivity["position"] (1.0mm) 사용
        original_tolerance = self.tolerance
        if near_match_tolerance is not None:
            self.tolerance = near_match_tolerance
        else:
            # 민감도 기반 tolerance로 자동 설정
            self.tolerance = self.sensitivity["position"]

        # Phase O2 — alignment 가 있으면 tolerance 를 그만큼 확장해 매칭률 ↑
        # (alignment.translation_magnitude 가 tolerance 보다 크면 near-match
        #  실패 → MODIFIED 추출 못함 → suppress 도 못함)
        if alignment is not None and alignment.is_significant:
            shift_mag = alignment.translation_magnitude
            self.tolerance = max(self.tolerance, shift_mag + self.sensitivity["position"])

        near_deleted = deleted
        near_added = added
        if result.metadata.get("large_drawing_mode") == "active":
            near_deleted = [
                c for c in deleted
                if c.entity_type in self._LARGE_MODE_NEAR_MATCH_ENTITY_TYPES
            ]
            near_added = [
                c for c in added
                if c.entity_type in self._LARGE_MODE_NEAR_MATCH_ENTITY_TYPES
            ]
            result.stats["large_near_match_limited"] = True
            result.stats["large_near_match_input_counts"] = {
                "deleted": len(near_deleted),
                "added": len(near_added),
                "skipped_deleted": len(deleted) - len(near_deleted),
                "skipped_added": len(added) - len(near_added),
            }
            result.metadata["large_near_match_policy"] = "structural_text_dimension_block_only"

        try:
            # 공간 기반 매칭으로 MODIFIED 탐지
            matches = self.find_near_matches(
                near_deleted,
                near_added,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
            )
        finally:
            # tolerance 복원
            self.tolerance = original_tolerance

        if callable(is_cancelled) and is_cancelled():
            return self._mark_cancelled(result)

        if not matches:
            if finalize_for_large:
                self._finalize_large_result(result)
            return result

        # Phase 3 P3-1: 매칭된 항목을 MODIFIED로 변환 (상세 분석 포함)
        modified_changes: List[DxfChange] = []
        matched_deleted = set()
        matched_added = set()
        ignored_count = 0
        alignment_suppressed = 0

        for d_change, a_change in matches:
            # 상세 분석 및 민감도 체크
            modified = self._create_modified_change(d_change, a_change)
            if modified is not None:
                # Phase O2 — alignment artifact 인지 검사
                if alignment is not None and alignment.is_significant:
                    if self._is_pure_alignment_artifact(d_change, a_change, alignment):
                        alignment_suppressed += 1
                        matched_deleted.add(id(d_change))
                        matched_added.add(id(a_change))
                        continue
                modified_changes.append(modified)
                matched_deleted.add(id(d_change))
                matched_added.add(id(a_change))
            else:
                ignored_count += 1

        # 결과 재구성: 매칭되지 않은 DELETED/ADDED + MODIFIED
        new_changes = [
            c for c in result.changes
            if not (
                (c.change_type == DxfChangeType.DELETED and id(c) in matched_deleted) or
                (c.change_type == DxfChangeType.ADDED and id(c) in matched_added)
            )
        ]
        new_changes.extend(modified_changes)

        result.changes = new_changes
        if alignment_suppressed:
            result.stats["alignment_suppressed"] = alignment_suppressed
            result.metadata["alignment_suppressed_count"] = alignment_suppressed

        # 통계 업데이트 (Phase 3 P3-1: 상세 통계)
        result.stats["modified_detected"] = len(modified_changes)
        result.stats["modified_ignored"] = ignored_count
        result.stats["near_match_tolerance"] = self.sensitivity["position"]
        result.stats["index_backend"] = self._last_index_backend
        result.metadata["index_backend"] = self._last_index_backend

        # 카테고리별 통계
        category_counts: Dict[str, int] = {}
        for change in modified_changes:
            if change.change_category:
                for cat in change.change_category.split(","):
                    category_counts[cat] = category_counts.get(cat, 0) + 1
        result.stats["modified_by_category"] = category_counts
        self._refresh_change_count_stats(result)

        logger.info(
            f"[MODIFIED 탐지] {len(modified_changes)}개 수정 엔티티 탐지, "
            f"{ignored_count}개 무시 (민감도 미만) "
            f"(R-tree: {self.use_spatial_index}, tolerance: {self.sensitivity['position']}mm)"
        )
        if category_counts:
            logger.debug(f"[MODIFIED 카테고리] {category_counts}")

        if finalize_for_large:
            self._finalize_large_result(result)

        return result

    # =========================================================================
    # Phase 3 P3-4: 레이어 이동 감지 및 우선순위
    # =========================================================================
    def _geometry_hash(self, entity: NormalizedEntity) -> str:
        """레이어를 제외한 기하학적 해시 생성

        동일한 형상이지만 레이어만 변경된 엔티티를 탐지하기 위한 해시.

        Args:
            entity: 정규화된 엔티티

        Returns:
            기하학적 해시 문자열
        """
        # 레이어를 제외한 속성으로 해시 생성
        # location + entity_type + data (레이어 제외)
        data_for_hash = {k: v for k, v in (entity.data or {}).items() if k != "layer"}

        # 위치를 정밀도 1자리로 반올림하여 해시에 포함
        loc_key = ""
        if entity.location:
            loc_key = f"({entity.location[0]:.1f},{entity.location[1]:.1f})"

        # 데이터를 정렬된 문자열로 변환
        data_str = str(sorted(data_for_hash.items()))

        return f"{entity.entity_type}:{loc_key}:{data_str}"

    def _detect_layer_moves(
        self,
        deleted: List[DxfChange],
        added: List[DxfChange],
    ) -> Tuple[List[DxfChange], set, set]:
        """레이어 이동 감지 (동일 형상 + 다른 레이어)

        같은 형상의 엔티티가 다른 레이어로 이동한 경우를 탐지합니다.

        Args:
            deleted: 삭제된 변경 목록
            added: 추가된 변경 목록

        Returns:
            (layer_moves, matched_deleted_ids, matched_added_ids) 튜플
        """
        layer_moves: List[DxfChange] = []
        matched_deleted_ids: set = set()
        matched_added_ids: set = set()

        if not deleted or not added:
            return layer_moves, matched_deleted_ids, matched_added_ids

        # 삭제된 엔티티를 기하학적 해시로 그룹화
        deleted_by_geom: Dict[str, List[Tuple[int, DxfChange]]] = {}
        for i, d_change in enumerate(deleted):
            # DxfChange에서 NormalizedEntity 유사 객체 생성
            mock_entity = type('MockEntity', (), {
                'entity_type': d_change.entity_type,
                'location': d_change.location,
                'data': d_change.old_data or {},
                'layer': d_change.layer,
            })()
            gh = self._geometry_hash_from_change(d_change)
            deleted_by_geom.setdefault(gh, []).append((i, d_change))

        # 추가된 엔티티에서 매칭 탐색
        for j, a_change in enumerate(added):
            gh = self._geometry_hash_from_change(a_change)

            if gh in deleted_by_geom:
                # 첫 번째 매칭 사용 (추후 개선: 최적 매칭 선택)
                for i, d_change in deleted_by_geom[gh]:
                    if i in matched_deleted_ids:
                        continue

                    # 레이어가 다른 경우만 레이어 이동으로 처리
                    if d_change.layer != a_change.layer:
                        # Phase 3+ Priority Score 계산
                        priority = self._calculate_change_priority(
                            change_type="MODIFIED",
                            layer=a_change.layer,
                            entity_data=a_change.new_data,
                        )
                        layer_move = DxfChange(
                            entity_type=a_change.entity_type,
                            layer=a_change.layer,  # 새 레이어
                            change_type=DxfChangeType.MODIFIED,
                            old_data={"layer": d_change.layer, **(d_change.old_data or {})},
                            new_data={"layer": a_change.layer, **(a_change.new_data or {})},
                            location=a_change.location,
                            old_location=d_change.location,
                            change_detail=f"레이어 이동: {d_change.layer} → {a_change.layer}",
                            change_category="layer_move",
                            priority_score=priority,
                        )
                        layer_moves.append(layer_move)
                        matched_deleted_ids.add(i)
                        matched_added_ids.add(j)
                        break

        if layer_moves:
            logger.info(f"[레이어 이동] {len(layer_moves)}개 레이어 이동 탐지")

        return layer_moves, matched_deleted_ids, matched_added_ids

    def _geometry_hash_from_change(self, change: DxfChange) -> str:
        """DxfChange에서 기하학적 해시 생성

        Args:
            change: DxfChange 인스턴스

        Returns:
            기하학적 해시 문자열
        """
        # old_data 또는 new_data 사용
        data = change.old_data or change.new_data or {}
        data_for_hash = {k: v for k, v in data.items() if k != "layer"}

        # 위치를 정밀도 1자리로 반올림
        loc_key = ""
        if change.location:
            loc_key = f"({change.location[0]:.1f},{change.location[1]:.1f})"

        # 데이터를 정렬된 문자열로 변환
        data_str = str(sorted(data_for_hash.items()))

        return f"{change.entity_type}:{loc_key}:{data_str}"

    def _classify_change_priority(
        self,
        change: DxfChange,
    ) -> str:
        """변경 우선순위 분류

        LayerPriorityConfig를 사용하여 레이어/엔티티 타입별 우선순위 분류.

        Args:
            change: DxfChange 인스턴스

        Returns:
            우선순위 문자열: "critical", "high", "medium", "low"
        """
        layer = change.layer

        # LayerPriorityConfig에서 우선순위 조회
        priority_value = self._layer_priority.get_priority(layer)

        # 우선순위 값 → 문자열 변환
        # get_priority returns: 2 (high), 1 (normal), 0 (low), -1 (ignore)
        if priority_value == 2:
            # 고우선순위 레이어 + 치수면 critical
            if change.entity_type == "DIMENSION":
                return "critical"
            return "high"
        elif priority_value == 0:
            return "low"
        elif priority_value == -1:
            return "low"  # 무시 레이어도 출력 시 low로 표시

        # 엔티티 타입별 기본 우선순위
        priority_map = {
            "DIMENSION": "high",
            "INSERT": "high",  # 블록 참조
            "LINE": "medium",
            "CIRCLE": "medium",
            "ARC": "medium",
            "POLYLINE": "medium",
            "LWPOLYLINE": "medium",
            "TEXT": "medium",
            "MTEXT": "medium",
            "HATCH": "low",
            "POINT": "low",
        }

        return priority_map.get(change.entity_type, "medium")

    def _compute_layer_statistics(
        self,
        changes: List[DxfChange],
    ) -> Tuple[Dict[str, LayerStatistics], Dict[str, int]]:
        """레이어별 통계 및 우선순위 요약 계산

        Args:
            changes: 변경 목록

        Returns:
            (layer_statistics, priority_summary) 튜플
        """
        layer_stats: Dict[str, LayerStatistics] = {}
        priority_counts: Dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}

        for change in changes:
            layer = change.layer

            # LayerStatistics 초기화
            if layer not in layer_stats:
                priority = self._classify_change_priority(change)
                layer_stats[layer] = LayerStatistics(layer=layer, priority=priority)

            stat = layer_stats[layer]

            # 변경 타입별 카운트
            if change.change_type == DxfChangeType.ADDED:
                stat.added_count += 1
            elif change.change_type == DxfChangeType.DELETED:
                stat.deleted_count += 1
            elif change.change_type == DxfChangeType.MODIFIED:
                # 레이어 이동인지 일반 수정인지 구분
                if change.change_category == "layer_move":
                    stat.layer_move_count += 1
                else:
                    stat.modified_count += 1

            # 우선순위 카운트
            priority_counts[stat.priority] += 1

        return layer_stats, priority_counts

    def compare_with_layer_statistics(
        self,
        entities_a: Dict[str, List[NormalizedEntity]],
        entities_b: Dict[str, List[NormalizedEntity]],
        detect_layer_moves: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DxfComparisonResult:
        """레이어 통계 및 레이어 이동 탐지를 포함한 고급 비교

        Phase 3 P3-4: 전체 기능 통합

        Args:
            entities_a: 기준(Old) 엔티티
            entities_b: 대상(New) 엔티티
            detect_layer_moves: 레이어 이동 탐지 여부 (기본 True)

        Returns:
            레이어 통계, 우선순위 요약 포함 비교 결과
        """
        # 1. 기본 MODIFIED 탐지 비교 수행
        result = self.compare_with_modified_detection(
            entities_a,
            entities_b,
            finalize_for_large=False,
            progress_callback=progress_callback,
            is_cancelled=is_cancelled,
        )
        if result.metadata.get("cancelled"):
            return result

        # 2. 레이어 이동 탐지 (옵션)
        if detect_layer_moves:
            deleted = [c for c in result.changes if c.change_type == DxfChangeType.DELETED]
            added = [c for c in result.changes if c.change_type == DxfChangeType.ADDED]

            layer_moves, matched_del_ids, matched_add_ids = self._detect_layer_moves(
                deleted, added
            )

            if layer_moves:
                # 매칭된 DELETED/ADDED 제거하고 레이어 이동 추가
                deleted_id_to_idx = {id(d): i for i, d in enumerate(deleted)}
                added_id_to_idx = {id(a): i for i, a in enumerate(added)}
                new_changes = []
                for i, c in enumerate(result.changes):
                    if c.change_type == DxfChangeType.DELETED:
                        # deleted 인덱스 찾기
                        del_idx = deleted_id_to_idx.get(id(c))
                        if del_idx is not None and del_idx in matched_del_ids:
                            continue  # 제외
                    elif c.change_type == DxfChangeType.ADDED:
                        # added 인덱스 찾기
                        add_idx = added_id_to_idx.get(id(c))
                        if add_idx is not None and add_idx in matched_add_ids:
                            continue  # 제외
                    new_changes.append(c)

                # 레이어 이동 추가
                new_changes.extend(layer_moves)
                result.changes = new_changes

                # 통계 업데이트
                result.stats["layer_moves_detected"] = len(layer_moves)
                self._refresh_change_count_stats(result)

        # 3. 레이어 통계 계산
        layer_stats, priority_summary = self._compute_layer_statistics(result.changes)
        result.layer_statistics = layer_stats
        result.priority_summary = priority_summary

        # 4. 통계 보강
        result.stats["layers_affected"] = len(layer_stats)
        result.stats["priority_summary"] = priority_summary
        result.stats["index_backend"] = result.stats.get("index_backend", self._last_index_backend)
        result.metadata["index_backend"] = result.stats["index_backend"]
        self._refresh_change_count_stats(result)
        self._finalize_large_result(result)

        logger.info(
            f"[레이어 통계] {len(layer_stats)}개 레이어, "
            f"우선순위: {priority_summary}"
        )

        return result
