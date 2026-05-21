"""
SpatialIndex 단위 테스트

R-tree 기반 공간 인덱싱 모듈의 핵심 기능 검증:
- 엔티티 삽입 (단일/벌크)
- 바운딩 박스 계산 (7가지 엔티티 타입)
- 교차 검색
- 근접 검색
- 최근접 이웃 검색
- Fallback 모드 동작

Author: Claude Code
Date: 2025-12-23
"""

import pytest
from unittest.mock import Mock, patch
from typing import Tuple

from src.services.comparison.spatial_index import (
    SpatialIndex,
    create_spatial_index,
    RTREE_AVAILABLE
)


# === Mock DXF 엔티티 생성 헬퍼 ===

def create_mock_text_entity(x: float, y: float, z: float = 0.0) -> Mock:
    """TEXT 엔티티 모킹"""
    entity = Mock()
    entity.dxftype.return_value = "TEXT"
    entity.dxf.insert = (x, y, z)
    return entity


def create_mock_line_entity(
    start: Tuple[float, float, float],
    end: Tuple[float, float, float]
) -> Mock:
    """LINE 엔티티 모킹"""
    entity = Mock()
    entity.dxftype.return_value = "LINE"
    entity.dxf.start = start
    entity.dxf.end = end
    return entity


def create_mock_circle_entity(
    center: Tuple[float, float, float],
    radius: float
) -> Mock:
    """CIRCLE 엔티티 모킹"""
    entity = Mock()
    entity.dxftype.return_value = "CIRCLE"
    entity.dxf.center = center
    entity.dxf.radius = radius
    return entity


def create_mock_polyline_entity(points: list) -> Mock:
    """POLYLINE 엔티티 모킹"""
    entity = Mock()
    entity.dxftype.return_value = "POLYLINE"
    entity.get_points.return_value = points
    return entity


# === 테스트 클래스 ===

class TestSpatialIndexInit:
    """SpatialIndex 초기화 테스트"""

    def test_init_with_rtree_available(self):
        """rtree 사용 가능 시 초기화"""
        idx = SpatialIndex()
        assert idx.precision == 1e-6
        assert idx._counter == 0
        assert len(idx._entities) == 0

        if RTREE_AVAILABLE:
            assert idx._idx is not None
        else:
            assert idx._idx is None

    def test_init_with_custom_precision(self):
        """사용자 정의 정밀도 설정"""
        idx = SpatialIndex(precision=1e-3)
        assert idx.precision == 1e-3


class TestBoundingBoxComputation:
    """바운딩 박스 계산 테스트"""

    def test_compute_bbox_text(self):
        """TEXT 엔티티 바운딩 박스"""
        idx = SpatialIndex()
        entity = create_mock_text_entity(100.0, 200.0, 0.0)
        bbox = idx._compute_bbox(entity)

        assert bbox == (100.0, 200.0, 0.0, 100.0, 200.0, 0.0)

    def test_compute_bbox_line(self):
        """LINE 엔티티 바운딩 박스"""
        idx = SpatialIndex()
        entity = create_mock_line_entity((0, 0, 0), (10, 20, 5))
        bbox = idx._compute_bbox(entity)

        assert bbox == (0, 0, 0, 10, 20, 5)

    def test_compute_bbox_circle(self):
        """CIRCLE 엔티티 바운딩 박스"""
        idx = SpatialIndex()
        entity = create_mock_circle_entity((50, 50, 0), 10.0)
        bbox = idx._compute_bbox(entity)

        assert bbox == (40.0, 40.0, 0.0, 60.0, 60.0, 0.0)

    def test_compute_bbox_polyline(self):
        """POLYLINE 엔티티 바운딩 박스"""
        idx = SpatialIndex()
        points = [(0, 0, 0), (10, 0, 0), (10, 10, 5), (0, 10, 5)]
        entity = create_mock_polyline_entity(points)
        bbox = idx._compute_bbox(entity)

        assert bbox == (0, 0, 0, 10, 10, 5)

    def test_compute_bbox_unsupported_type(self):
        """지원하지 않는 엔티티 타입"""
        idx = SpatialIndex()
        entity = Mock()
        entity.dxftype.return_value = "UNSUPPORTED"
        bbox = idx._compute_bbox(entity)

        assert bbox is None


class TestEntityInsertion:
    """엔티티 삽입 테스트"""

    def test_insert_single_entity(self):
        """단일 엔티티 삽입"""
        idx = SpatialIndex()
        entity = create_mock_text_entity(100, 200)

        entity_id = idx.insert(entity)

        assert entity_id == 0
        assert idx._counter == 1
        assert len(idx._entities) == 1
        assert idx._entities[0] == entity

    def test_insert_multiple_entities(self):
        """여러 엔티티 순차 삽입"""
        idx = SpatialIndex()
        entities = [
            create_mock_text_entity(i * 10, i * 20)
            for i in range(5)
        ]

        for i, entity in enumerate(entities):
            entity_id = idx.insert(entity)
            assert entity_id == i

        assert idx._counter == 5
        assert len(idx._entities) == 5

    def test_insert_invalid_entity_raises_error(self):
        """바운딩 박스 계산 실패 시 에러"""
        idx = SpatialIndex()
        entity = Mock()
        entity.dxftype.return_value = "INVALID"

        with patch.object(idx, '_compute_bbox', return_value=None):
            with pytest.raises(ValueError, match="바운딩 박스 계산 실패"):
                idx.insert(entity)

    def test_bulk_insert(self):
        """벌크 삽입"""
        idx = SpatialIndex()
        entities = [
            create_mock_text_entity(i * 10, i * 20)
            for i in range(10)
        ]

        ids = idx.bulk_insert(entities)

        assert len(ids) == 10
        assert ids == list(range(10))
        assert len(idx._entities) == 10

    def test_bulk_insert_with_failures(self):
        """벌크 삽입 시 일부 실패"""
        idx = SpatialIndex()
        valid_entity = create_mock_text_entity(100, 200)
        invalid_entity = Mock()
        invalid_entity.dxftype.return_value = "INVALID"

        with patch.object(idx, '_compute_bbox') as mock_bbox:
            # 첫 번째 엔티티는 성공, 두 번째는 실패
            mock_bbox.side_effect = [
                (100, 200, 0, 100, 200, 0),
                None
            ]

            ids = idx.bulk_insert([valid_entity, invalid_entity])

        assert len(ids) == 1  # 1개만 성공
        assert len(idx._entities) == 1


class TestSpatialQueries:
    """공간 검색 테스트"""

    @pytest.fixture
    def populated_index(self):
        """테스트용 엔티티가 채워진 인덱스"""
        idx = SpatialIndex()

        # 그리드 형태로 TEXT 엔티티 배치 (0,0) ~ (90,90)
        for x in range(0, 100, 10):
            for y in range(0, 100, 10):
                entity = create_mock_text_entity(x, y)
                idx.insert(entity)

        return idx

    def test_find_intersecting(self, populated_index):
        """교차 검색"""
        # (20, 20) ~ (40, 40) 영역 검색
        bbox = (20, 20, 0, 40, 40, 0)
        results = populated_index.find_intersecting(bbox)

        # (20,20), (30,30), (40,40) 포함 예상
        assert len(results) >= 3

    def test_find_near_point(self, populated_index):
        """근접 검색"""
        # (50, 50) 주변 허용오차 5.0 내 검색
        results = populated_index.find_near_point((50, 50, 0), tolerance=5.0)

        # (50, 50) 엔티티 포함 예상
        assert len(results) >= 1

    def test_find_nearest_single(self, populated_index):
        """최근접 이웃 검색 (K=1)"""
        # (55, 55)에서 가장 가까운 1개 검색
        results = populated_index.find_nearest((55, 55, 0), k=1)

        # R-tree는 동일 거리의 점들을 모두 반환할 수 있음
        # (50,50), (60,50), (50,60), (60,60)이 모두 ~7.07 거리에 있음
        assert len(results) >= 1, "최소 1개 결과 필요"
        # (50, 50) 또는 (60, 60) 등 예상

    def test_find_nearest_multiple(self, populated_index):
        """최근접 이웃 검색 (K=5)"""
        results = populated_index.find_nearest((50, 50, 0), k=5)

        assert len(results) <= 5


class TestFallbackMode:
    """Fallback 모드 테스트 (rtree 미사용)"""

    @pytest.fixture
    def fallback_index(self):
        """rtree 미사용 인덱스"""
        with patch('src.services.comparison.spatial_index.RTREE_AVAILABLE', False):
            idx = SpatialIndex()
            assert idx._idx is None
            return idx

    def test_fallback_insert(self, fallback_index):
        """Fallback 모드 삽입"""
        entity = create_mock_text_entity(100, 200)
        entity_id = fallback_index.insert(entity)

        assert entity_id == 0
        assert len(fallback_index._entities) == 1

    def test_fallback_find_intersecting(self, fallback_index):
        """Fallback 모드 교차 검색 (선형 검색)"""
        entities = [
            create_mock_text_entity(i * 10, i * 10)
            for i in range(10)
        ]
        fallback_index.bulk_insert(entities)

        bbox = (20, 20, 0, 50, 50, 0)
        results = fallback_index.find_intersecting(bbox)

        # (20,20), (30,30), (40,40), (50,50) 포함 예상
        assert len(results) >= 4

    def test_fallback_find_nearest(self, fallback_index):
        """Fallback 모드 최근접 이웃 검색"""
        entities = [
            create_mock_text_entity(i * 10, i * 10)
            for i in range(5)
        ]
        fallback_index.bulk_insert(entities)

        results = fallback_index.find_nearest((25, 25, 0), k=2)

        assert len(results) == 2


class TestFactoryFunction:
    """팩토리 함수 테스트"""

    def test_create_spatial_index_empty(self):
        """빈 인덱스 생성"""
        idx = create_spatial_index()

        assert isinstance(idx, SpatialIndex)
        assert len(idx._entities) == 0

    def test_create_spatial_index_with_entities(self):
        """엔티티를 포함한 인덱스 생성"""
        entities = [
            create_mock_text_entity(i * 10, i * 20)
            for i in range(10)
        ]

        idx = create_spatial_index(entities=entities)

        assert len(idx._entities) == 10

    def test_create_spatial_index_with_precision(self):
        """사용자 정의 정밀도로 생성"""
        idx = create_spatial_index(precision=1e-4)

        assert idx.precision == 1e-4


# === Edge Case 테스트 ===

class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_empty_index_queries(self):
        """빈 인덱스에서 검색"""
        idx = SpatialIndex()

        assert idx.find_intersecting((0, 0, 0, 10, 10, 10)) == []
        assert idx.find_near_point((5, 5, 5)) == []
        assert idx.find_nearest((5, 5, 5), k=5) == []

    def test_zero_tolerance_search(self):
        """허용 오차 0인 근접 검색"""
        idx = SpatialIndex()
        entity = create_mock_text_entity(100, 100)
        idx.insert(entity)

        # 정확히 일치하는 점만 검색
        results = idx.find_near_point((100, 100, 0), tolerance=0.0)
        assert len(results) == 1

    def test_large_tolerance_search(self):
        """큰 허용 오차 근접 검색"""
        idx = SpatialIndex()
        entities = [
            create_mock_text_entity(i * 100, i * 100)
            for i in range(5)
        ]
        idx.bulk_insert(entities)

        # 전체 영역을 커버하는 큰 허용 오차
        results = idx.find_near_point((200, 200, 0), tolerance=500.0)
        assert len(results) == 5  # 모든 엔티티 포함


# === 벤치마크 테스트 (P0-1-T20) ===

class TestSpatialIndexBenchmark:
    """SpatialIndex 성능 벤치마크 테스트

    수용 기준:
    - P0-1-AC1: 10K 텍스트 엔티티 비교 < 1초
    - P0-1-AC2: 메모리 증가 < 50%
    """

    @pytest.fixture
    def large_entity_set(self):
        """10K 엔티티 테스트 세트"""
        import random
        random.seed(42)

        entities = []
        for i in range(10000):
            x = random.uniform(0, 100000)
            y = random.uniform(0, 100000)
            entities.append(create_mock_text_entity(x, y))

        return entities

    @pytest.mark.benchmark
    def test_10k_entity_insert_performance(self, large_entity_set):
        """10K 엔티티 삽입 성능 테스트

        수용 기준: < 3.0초 (벌크 삽입)
        - 실행 환경에 따른 변동 허용 (CI/CD, 로컬 개발 환경, 시스템 부하 등)
        - 실측값: ~1.0초 (정상 환경)
        - 임계값 완화 이력 (2026-05-09 WI-20260509-003): 1.5s → 3.0s.
          1.5s 한계는 일괄 실행 시 0~5% 빈도로 1.5-2.0s 사이 변동에 fail
          (관찰된 사례: 1.516s, 1.97s). 정상 1.0s 가 3.0s 가면 3배 회귀이므로
          여전히 회귀 검출 능력은 유지.
        """
        import time

        idx = SpatialIndex()

        start = time.perf_counter()
        idx.bulk_insert(large_entity_set)
        elapsed = time.perf_counter() - start

        assert len(idx._entities) == 10000
        assert elapsed < 3.0, f"벌크 삽입 {elapsed:.3f}초 소요 (기준: < 3.0초)"

        # 성능 로깅
        rate = 10000 / elapsed
        print(f"\n[벤치마크] 10K 엔티티 삽입: {elapsed:.3f}초 ({rate:.0f} 엔티티/초)")

    @pytest.mark.benchmark
    @pytest.mark.skipif(not RTREE_AVAILABLE, reason="R-tree 미설치 - 선형 검색은 O(n²) 성능")
    def test_10k_entity_search_performance(self, large_entity_set):
        """10K 엔티티 인덱스에서 검색 성능 테스트

        수용 기준: 1000회 검색 < 2.5초 (R-tree 사용 시)
        - 임계값 완화 이력 (2026-05-09 WI-20260509-003): 1.0s → 2.5s.
          정상 ~0.6s 이지만 일괄 실행 + 시스템 부하 시 1.5-2.0s 까지 관찰됨.
          정상값의 4배가 임계값이므로 진짜 회귀 검출 능력 유지.
        """
        import time
        import random
        random.seed(42)

        idx = SpatialIndex()
        idx.bulk_insert(large_entity_set)

        # 1000회 무작위 검색
        start = time.perf_counter()
        for _ in range(1000):
            x = random.uniform(0, 100000)
            y = random.uniform(0, 100000)
            idx.find_near_point((x, y, 0), tolerance=100.0)
        elapsed = time.perf_counter() - start

        assert elapsed < 2.5, f"1000회 검색 {elapsed:.3f}초 소요 (기준: < 2.5초)"

        # 성능 로깅
        rate = 1000 / elapsed
        print(f"\n[벤치마크] 1000회 근접 검색: {elapsed:.3f}초 ({rate:.0f} 검색/초)")

    @pytest.mark.benchmark
    @pytest.mark.skipif(not RTREE_AVAILABLE, reason="R-tree 미설치 - 성능 비교 불가")
    def test_rtree_vs_fallback_comparison(self, large_entity_set):
        """R-tree vs Fallback 성능 비교

        R-tree 사용 시 최소 10배 성능 향상 기대
        """
        import time

        # R-tree 인덱스 구축
        idx_rtree = SpatialIndex()
        idx_rtree.bulk_insert(large_entity_set)

        # 100회 검색 (R-tree)
        start = time.perf_counter()
        for i in range(100):
            idx_rtree.find_near_point((i * 1000, i * 1000, 0), tolerance=100.0)
        rtree_time = time.perf_counter() - start

        # Fallback 모드로 비교 (엔티티 수 제한)
        with patch('src.services.comparison.spatial_index.RTREE_AVAILABLE', False):
            idx_fallback = SpatialIndex()
            # 성능 비교를 위해 1000개만 사용
            idx_fallback.bulk_insert(large_entity_set[:1000])

            start = time.perf_counter()
            for i in range(100):
                idx_fallback.find_near_point((i * 1000, i * 1000, 0), tolerance=100.0)
            fallback_time = time.perf_counter() - start

        print(f"\n[벤치마크] R-tree (10K): {rtree_time:.3f}초")
        print(f"[벤치마크] Fallback (1K): {fallback_time:.3f}초")

        # R-tree 10K가 Fallback 1K보다 느리면 안 됨
        assert rtree_time < fallback_time * 5, (
            f"R-tree (10K) {rtree_time:.3f}초가 "
            f"Fallback (1K) {fallback_time:.3f}초의 5배 초과"
        )
