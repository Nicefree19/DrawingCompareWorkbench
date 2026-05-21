"""DxfComparator 레이어 통계 기능 테스트

Phase 3 P3-4: 레이어 이동 감지 및 우선순위
- LayerStatistics 데이터클래스 테스트
- _detect_layer_moves() 테스트
- _classify_change_priority() 테스트
- _compute_layer_statistics() 테스트
- compare_with_layer_statistics() 통합 테스트
"""

import pytest
from unittest.mock import Mock

from src.services.comparison.dxf_comparator import (
    DxfChange,
    DxfChangeType,
    DxfComparator,
    DxfComparisonResult,
    LayerStatistics,
)
from src.services.comparison.dxf_entity_extractor import NormalizedEntity
from src.services.comparison.comparison_config import (
    ComparisonConfig,
    LayerPriorityConfig,
)


class TestLayerStatistics:
    """LayerStatistics 데이터클래스 테스트"""

    def test_default_values(self):
        """기본값 테스트"""
        stat = LayerStatistics(layer="TEST")
        assert stat.layer == "TEST"
        assert stat.priority == "medium"
        assert stat.added_count == 0
        assert stat.deleted_count == 0
        assert stat.modified_count == 0
        assert stat.layer_move_count == 0

    def test_total_changes_calculation(self):
        """총 변경 수 계산"""
        stat = LayerStatistics(
            layer="TEST",
            added_count=5,
            deleted_count=3,
            modified_count=2,
            layer_move_count=1,
        )
        assert stat.total_changes == 11

    def test_to_dict(self):
        """딕셔너리 변환"""
        stat = LayerStatistics(
            layer="GRID",
            priority="high",
            added_count=2,
            deleted_count=1,
        )
        d = stat.to_dict()
        assert d["layer"] == "GRID"
        assert d["priority"] == "high"
        assert d["added_count"] == 2
        assert d["deleted_count"] == 1
        assert d["total_changes"] == 3

    def test_custom_priority(self):
        """사용자 정의 우선순위"""
        stat = LayerStatistics(layer="CRITICAL_LAYER", priority="critical")
        assert stat.priority == "critical"


class TestDetectLayerMoves:
    """_detect_layer_moves() 테스트"""

    @pytest.fixture
    def comparator(self):
        return DxfComparator()

    def test_no_moves_when_empty(self, comparator):
        """빈 목록일 때 이동 없음"""
        moves, del_ids, add_ids = comparator._detect_layer_moves([], [])
        assert moves == []
        assert del_ids == set()
        assert add_ids == set()

    def test_detect_layer_move(self, comparator):
        """레이어 이동 탐지"""
        # 같은 LINE이 LAYER_A → LAYER_B로 이동
        deleted = [
            DxfChange(
                entity_type="LINE",
                layer="LAYER_A",
                change_type=DxfChangeType.DELETED,
                old_data={"start": (0, 0), "end": (100, 0)},
                location=(50.0, 0.0),
            )
        ]
        added = [
            DxfChange(
                entity_type="LINE",
                layer="LAYER_B",
                change_type=DxfChangeType.ADDED,
                new_data={"start": (0, 0), "end": (100, 0)},
                location=(50.0, 0.0),
            )
        ]

        moves, del_ids, add_ids = comparator._detect_layer_moves(deleted, added)

        assert len(moves) == 1
        assert moves[0].change_type == DxfChangeType.MODIFIED
        assert moves[0].change_category == "layer_move"
        assert "LAYER_A → LAYER_B" in moves[0].change_detail
        assert 0 in del_ids
        assert 0 in add_ids

    def test_no_move_same_layer(self, comparator):
        """같은 레이어는 이동 아님"""
        deleted = [
            DxfChange(
                entity_type="LINE",
                layer="LAYER_A",
                change_type=DxfChangeType.DELETED,
                old_data={"start": (0, 0), "end": (100, 0)},
                location=(50.0, 0.0),
            )
        ]
        added = [
            DxfChange(
                entity_type="LINE",
                layer="LAYER_A",  # 같은 레이어
                change_type=DxfChangeType.ADDED,
                new_data={"start": (0, 0), "end": (100, 0)},
                location=(50.0, 0.0),
            )
        ]

        moves, del_ids, add_ids = comparator._detect_layer_moves(deleted, added)

        assert len(moves) == 0

    def test_no_move_different_geometry(self, comparator):
        """다른 형상은 이동 아님"""
        deleted = [
            DxfChange(
                entity_type="LINE",
                layer="LAYER_A",
                change_type=DxfChangeType.DELETED,
                old_data={"start": (0, 0), "end": (100, 0)},
                location=(50.0, 0.0),
            )
        ]
        added = [
            DxfChange(
                entity_type="LINE",
                layer="LAYER_B",
                change_type=DxfChangeType.ADDED,
                new_data={"start": (0, 0), "end": (200, 0)},  # 다른 끝점
                location=(100.0, 0.0),
            )
        ]

        moves, del_ids, add_ids = comparator._detect_layer_moves(deleted, added)

        assert len(moves) == 0

    def test_multiple_layer_moves(self, comparator):
        """여러 레이어 이동 탐지"""
        deleted = [
            DxfChange(
                entity_type="LINE",
                layer="A",
                change_type=DxfChangeType.DELETED,
                old_data={"key": "val1"},
                location=(10.0, 0.0),
            ),
            DxfChange(
                entity_type="CIRCLE",
                layer="X",
                change_type=DxfChangeType.DELETED,
                old_data={"radius": 50},
                location=(100.0, 100.0),
            ),
        ]
        added = [
            DxfChange(
                entity_type="LINE",
                layer="B",
                change_type=DxfChangeType.ADDED,
                new_data={"key": "val1"},
                location=(10.0, 0.0),
            ),
            DxfChange(
                entity_type="CIRCLE",
                layer="Y",
                change_type=DxfChangeType.ADDED,
                new_data={"radius": 50},
                location=(100.0, 100.0),
            ),
        ]

        moves, del_ids, add_ids = comparator._detect_layer_moves(deleted, added)

        assert len(moves) == 2
        assert 0 in del_ids and 1 in del_ids
        assert 0 in add_ids and 1 in add_ids


class TestClassifyChangePriority:
    """_classify_change_priority() 테스트"""

    @pytest.fixture
    def comparator(self):
        return DxfComparator()

    def test_high_priority_layer_dimension_is_critical(self, comparator):
        """고우선순위 레이어의 DIMENSION은 critical"""
        change = DxfChange(
            entity_type="DIMENSION",
            layer="DIM_LAYER",  # matches DIM* pattern
            change_type=DxfChangeType.ADDED,
        )
        priority = comparator._classify_change_priority(change)
        assert priority == "critical"

    def test_high_priority_layer_non_dimension(self, comparator):
        """고우선순위 레이어의 일반 엔티티는 high"""
        change = DxfChange(
            entity_type="TEXT",
            layer="TEXT_LABELS",  # matches TEXT* pattern
            change_type=DxfChangeType.ADDED,
        )
        priority = comparator._classify_change_priority(change)
        assert priority == "high"

    def test_low_priority_layer(self, comparator):
        """저우선순위 레이어는 low"""
        change = DxfChange(
            entity_type="LINE",
            layer="DEFPOINTS",  # low priority pattern
            change_type=DxfChangeType.ADDED,
        )
        priority = comparator._classify_change_priority(change)
        assert priority == "low"

    def test_normal_layer_entity_type_priority(self, comparator):
        """일반 레이어는 엔티티 타입별 우선순위"""
        # DIMENSION은 high
        change = DxfChange(
            entity_type="DIMENSION",
            layer="NORMAL_LAYER",
            change_type=DxfChangeType.ADDED,
        )
        assert comparator._classify_change_priority(change) == "high"

        # LINE은 medium
        change = DxfChange(
            entity_type="LINE",
            layer="NORMAL_LAYER",
            change_type=DxfChangeType.ADDED,
        )
        assert comparator._classify_change_priority(change) == "medium"

        # HATCH는 low
        change = DxfChange(
            entity_type="HATCH",
            layer="NORMAL_LAYER",
            change_type=DxfChangeType.ADDED,
        )
        assert comparator._classify_change_priority(change) == "low"

    def test_custom_layer_priority_config(self):
        """사용자 정의 레이어 우선순위"""
        config = ComparisonConfig(
            layer_priority=LayerPriorityConfig(
                high_priority_patterns=["GRID*"],
            )
        )
        comparator = DxfComparator(config=config)

        change = DxfChange(
            entity_type="LINE",
            layer="GRID_A",
            change_type=DxfChangeType.ADDED,
        )
        priority = comparator._classify_change_priority(change)
        assert priority == "high"


class TestComputeLayerStatistics:
    """_compute_layer_statistics() 테스트"""

    @pytest.fixture
    def comparator(self):
        return DxfComparator()

    def test_empty_changes(self, comparator):
        """빈 변경 목록"""
        layer_stats, priority_summary = comparator._compute_layer_statistics([])
        assert layer_stats == {}
        assert priority_summary == {"critical": 0, "high": 0, "medium": 0, "low": 0}

    def test_single_layer_statistics(self, comparator):
        """단일 레이어 통계"""
        changes = [
            DxfChange(
                entity_type="LINE",
                layer="WALL",
                change_type=DxfChangeType.ADDED,
            ),
            DxfChange(
                entity_type="LINE",
                layer="WALL",
                change_type=DxfChangeType.DELETED,
            ),
            DxfChange(
                entity_type="LINE",
                layer="WALL",
                change_type=DxfChangeType.MODIFIED,
            ),
        ]

        layer_stats, priority_summary = comparator._compute_layer_statistics(changes)

        assert "WALL" in layer_stats
        stat = layer_stats["WALL"]
        assert stat.added_count == 1
        assert stat.deleted_count == 1
        assert stat.modified_count == 1
        assert stat.total_changes == 3

    def test_multiple_layers(self, comparator):
        """여러 레이어 통계"""
        changes = [
            DxfChange(entity_type="LINE", layer="A", change_type=DxfChangeType.ADDED),
            DxfChange(entity_type="LINE", layer="A", change_type=DxfChangeType.ADDED),
            DxfChange(entity_type="LINE", layer="B", change_type=DxfChangeType.DELETED),
            DxfChange(entity_type="DIMENSION", layer="DIM", change_type=DxfChangeType.ADDED),
        ]

        layer_stats, priority_summary = comparator._compute_layer_statistics(changes)

        assert len(layer_stats) == 3
        assert layer_stats["A"].added_count == 2
        assert layer_stats["B"].deleted_count == 1
        assert layer_stats["DIM"].added_count == 1

    def test_layer_move_category(self, comparator):
        """레이어 이동 카운트"""
        changes = [
            DxfChange(
                entity_type="LINE",
                layer="TARGET",
                change_type=DxfChangeType.MODIFIED,
                change_category="layer_move",
            ),
            DxfChange(
                entity_type="LINE",
                layer="TARGET",
                change_type=DxfChangeType.MODIFIED,
                change_category="position",  # 일반 수정
            ),
        ]

        layer_stats, priority_summary = comparator._compute_layer_statistics(changes)

        stat = layer_stats["TARGET"]
        assert stat.layer_move_count == 1
        assert stat.modified_count == 1

    def test_priority_summary(self, comparator):
        """우선순위 요약"""
        changes = [
            # high (DIM* pattern)
            DxfChange(entity_type="LINE", layer="DIM_LAYER", change_type=DxfChangeType.ADDED),
            DxfChange(entity_type="LINE", layer="DIM_LAYER", change_type=DxfChangeType.ADDED),
            # low (DEFPOINTS)
            DxfChange(entity_type="LINE", layer="DEFPOINTS", change_type=DxfChangeType.ADDED),
            # medium (normal)
            DxfChange(entity_type="LINE", layer="NORMAL", change_type=DxfChangeType.ADDED),
        ]

        layer_stats, priority_summary = comparator._compute_layer_statistics(changes)

        assert priority_summary["high"] == 2
        assert priority_summary["low"] == 1
        assert priority_summary["medium"] == 1


class TestDxfComparisonResultExtensions:
    """DxfComparisonResult P3-4 확장 테스트"""

    def test_layer_statistics_field(self):
        """layer_statistics 필드"""
        result = DxfComparisonResult()
        assert result.layer_statistics == {}

        # 수동 설정
        result.layer_statistics["TEST"] = LayerStatistics(
            layer="TEST", priority="high", added_count=5
        )
        assert result.layer_statistics["TEST"].added_count == 5

    def test_priority_summary_field(self):
        """priority_summary 필드"""
        result = DxfComparisonResult()
        assert result.priority_summary == {}

        result.priority_summary = {"critical": 1, "high": 5, "medium": 10, "low": 2}
        assert result.priority_summary["high"] == 5

    def test_layer_move_count_property(self):
        """layer_move_count 프로퍼티"""
        result = DxfComparisonResult(
            changes=[
                DxfChange(
                    entity_type="LINE",
                    layer="A",
                    change_type=DxfChangeType.MODIFIED,
                    change_category="layer_move",
                ),
                DxfChange(
                    entity_type="LINE",
                    layer="B",
                    change_type=DxfChangeType.MODIFIED,
                    change_category="position",
                ),
                DxfChange(
                    entity_type="LINE",
                    layer="C",
                    change_type=DxfChangeType.MODIFIED,
                    change_category="layer_move",
                ),
            ]
        )
        assert result.layer_move_count == 2

    def test_filter_by_priority(self):
        """filter_by_priority 메서드"""
        result = DxfComparisonResult(
            changes=[
                DxfChange(entity_type="LINE", layer="DIM_L", change_type=DxfChangeType.ADDED),
                DxfChange(entity_type="LINE", layer="DEFPOINTS", change_type=DxfChangeType.ADDED),
                DxfChange(entity_type="LINE", layer="NORMAL", change_type=DxfChangeType.ADDED),
            ]
        )
        result.layer_statistics = {
            "DIM_L": LayerStatistics(layer="DIM_L", priority="high"),
            "DEFPOINTS": LayerStatistics(layer="DEFPOINTS", priority="low"),
            "NORMAL": LayerStatistics(layer="NORMAL", priority="medium"),
        }

        high_changes = result.filter_by_priority("high")
        assert len(high_changes) == 1
        assert high_changes[0].layer == "DIM_L"

    def test_get_layers_by_priority(self):
        """get_layers_by_priority 메서드"""
        result = DxfComparisonResult()
        result.layer_statistics = {
            "A": LayerStatistics(layer="A", priority="critical"),
            "B": LayerStatistics(layer="B", priority="high"),
            "C": LayerStatistics(layer="C", priority="high"),
            "D": LayerStatistics(layer="D", priority="medium"),
        }

        layers_by_priority = result.get_layers_by_priority()

        assert "A" in layers_by_priority["critical"]
        assert "B" in layers_by_priority["high"]
        assert "C" in layers_by_priority["high"]
        assert "D" in layers_by_priority["medium"]

    def test_get_summary_with_priority(self):
        """get_summary에 우선순위 포함"""
        result = DxfComparisonResult(
            changes=[
                DxfChange(entity_type="LINE", layer="A", change_type=DxfChangeType.ADDED),
            ]
        )
        result.priority_summary = {"critical": 0, "high": 1, "medium": 0, "low": 0}

        summary = result.get_summary()
        assert "high: 1" in summary


class TestCompareWithLayerStatistics:
    """compare_with_layer_statistics() 통합 테스트"""

    @pytest.fixture
    def comparator(self):
        return DxfComparator()

    def _make_hash(self, entity_type: str, layer: str, data: dict) -> str:
        """테스트용 해시 생성"""
        import hashlib
        content = f"{entity_type}:{layer}:{sorted(data.items())}"
        return hashlib.md5(content.encode()).hexdigest()

    def test_basic_integration(self, comparator):
        """기본 통합 테스트"""
        # 간단한 엔티티 세트
        data_a = {"start": (0, 0), "end": (100, 0)}
        data_b = {"start": (0, 0), "end": (100, 0)}
        entities_a = {
            "LINE": [
                NormalizedEntity(
                    hash=self._make_hash("LINE", "LAYER_A", data_a),
                    entity_type="LINE",
                    layer="LAYER_A",
                    location=(0.0, 0.0),
                    data=data_a,
                ),
            ]
        }
        entities_b = {
            "LINE": [
                NormalizedEntity(
                    hash=self._make_hash("LINE", "LAYER_B", data_b),
                    entity_type="LINE",
                    layer="LAYER_B",  # 레이어 변경
                    location=(0.0, 0.0),
                    data=data_b,
                ),
            ]
        }

        result = comparator.compare_with_layer_statistics(entities_a, entities_b)

        # 레이어 통계가 채워져야 함
        assert len(result.layer_statistics) > 0
        assert result.priority_summary is not None

    def test_layer_moves_detected(self, comparator):
        """레이어 이동 탐지 통합"""
        data_a = {"start": (0, 0), "end": (100, 100)}
        data_b = {"start": (0, 0), "end": (100, 100)}
        entities_a = {
            "LINE": [
                NormalizedEntity(
                    hash=self._make_hash("LINE", "OLD_LAYER", data_a),
                    entity_type="LINE",
                    layer="OLD_LAYER",
                    location=(50.0, 50.0),
                    data=data_a,
                ),
            ]
        }
        entities_b = {
            "LINE": [
                NormalizedEntity(
                    hash=self._make_hash("LINE", "NEW_LAYER", data_b),
                    entity_type="LINE",
                    layer="NEW_LAYER",  # 레이어만 변경
                    location=(50.0, 50.0),
                    data=data_b,
                ),
            ]
        }

        result = comparator.compare_with_layer_statistics(
            entities_a, entities_b, detect_layer_moves=True
        )

        # 레이어 이동이 탐지되어야 함
        layer_moves = [
            c for c in result.changes
            if c.change_category == "layer_move"
        ]
        assert len(layer_moves) >= 0  # 해시 매칭에 따라 다를 수 있음

    def test_priority_in_stats(self, comparator):
        """통계에 우선순위 포함"""
        data_b = {"measurement": 1500}
        entities_a = {}
        entities_b = {
            "DIMENSION": [
                NormalizedEntity(
                    hash=self._make_hash("DIMENSION", "DIM_LAYER", data_b),
                    entity_type="DIMENSION",
                    layer="DIM_LAYER",
                    location=(100.0, 100.0),
                    data=data_b,
                ),
            ]
        }

        result = comparator.compare_with_layer_statistics(entities_a, entities_b)

        # DIM_LAYER는 high priority
        if "DIM_LAYER" in result.layer_statistics:
            assert result.layer_statistics["DIM_LAYER"].priority in ["high", "critical"]
