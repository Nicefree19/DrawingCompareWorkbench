"""
R-tree 기반 공간 인덱싱 모듈

DXF 엔티티의 효율적인 공간 검색을 위한 R-tree 인덱스 구현.
rtree 라이브러리 미설치 시 선형 검색 fallback 제공.

주요 기능:
- 엔티티별 바운딩 박스 자동 계산 (TEXT, LINE, CIRCLE, ARC, POLYLINE, INSERT, DIMENSION 지원)
- 교차 검색 (find_intersecting): O(log n)
- 근접 검색 (find_near_point): 허용 오차 기반 검색
- 최근접 이웃 검색 (find_nearest): K-NN 알고리즘

성능:
- rtree 사용 시: O(log n) 검색 성능
- fallback 시: O(n²) 선형 검색 (경고 로깅)

Author: Claude Code
Date: 2025-12-23
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import logging

# rtree 선택적 import (미설치 시 fallback 모드)
try:
    from rtree import index
    RTREE_AVAILABLE = True
except ImportError:
    RTREE_AVAILABLE = False
    logging.getLogger(__name__).debug(
        "rtree 라이브러리가 설치되지 않았습니다. "
        "선형 검색 fallback 모드로 동작합니다 (성능 저하 예상). "
        "설치: pip install rtree"
    )


logger = logging.getLogger(__name__)


@dataclass
class SpatialIndex:
    """
    R-tree 기반 공간 인덱스

    Attributes:
        precision: 좌표 비교 정밀도 (기본 1e-6)
        _idx: rtree.index.Index 인스턴스 (rtree 사용 시) 또는 None
        _entities: 엔티티 ID → 엔티티 객체 매핑
        _counter: 다음 할당할 내부 ID
    """
    precision: float = 1e-6
    _idx: Optional[Any] = field(default=None, init=False, repr=False)
    _entities: Dict[int, Any] = field(default_factory=dict, init=False, repr=False)
    _counter: int = field(default=0, init=False, repr=False)

    def __post_init__(self):
        """R-tree 인덱스 초기화"""
        if RTREE_AVAILABLE:
            # R-tree 인덱스 생성 (3D 지원)
            self._idx = index.Index(interleaved=True, properties=index.Property(dimension=3))
            logger.debug("R-tree 인덱스 초기화 완료")
        else:
            self._idx = None
            logger.warning("R-tree 사용 불가, fallback 모드로 전환")

    def insert(self, entity: Any) -> int:
        """
        단일 엔티티를 인덱스에 삽입

        Args:
            entity: DXF 엔티티 객체

        Returns:
            할당된 내부 ID

        Raises:
            ValueError: 바운딩 박스 계산 실패 시
        """
        bbox = self._compute_bbox(entity)
        if bbox is None:
            raise ValueError(f"엔티티 {entity.dxftype()} 바운딩 박스 계산 실패")

        internal_id = self._counter
        self._counter += 1

        if RTREE_AVAILABLE and self._idx is not None:
            # R-tree에 삽입: (minx, miny, minz, maxx, maxy, maxz)
            self._idx.insert(internal_id, bbox)

        self._entities[internal_id] = entity
        logger.debug(f"엔티티 {entity.dxftype()} 삽입 완료 (ID: {internal_id}, bbox: {bbox})")
        return internal_id

    def bulk_insert(self, entities: List[Any]) -> List[int]:
        """
        대량 엔티티를 인덱스에 삽입

        Args:
            entities: DXF 엔티티 리스트

        Returns:
            할당된 내부 ID 리스트
        """
        ids = []
        for entity in entities:
            try:
                entity_id = self.insert(entity)
                ids.append(entity_id)
            except ValueError as e:
                logger.warning(f"엔티티 삽입 실패: {e}")
                continue

        logger.info(f"벌크 삽입 완료: {len(ids)}/{len(entities)} 엔티티")
        return ids

    def _compute_bbox(self, entity: Any) -> Optional[Tuple[float, float, float, float, float, float]]:
        """
        엔티티별 바운딩 박스 계산

        Args:
            entity: DXF 엔티티 객체

        Returns:
            (minx, miny, minz, maxx, maxy, maxz) 또는 None (지원하지 않는 타입)
        """
        try:
            entity_type = entity.dxftype()

            # TEXT/MTEXT: 삽입점 기준
            if entity_type in ("TEXT", "MTEXT"):
                insert = entity.dxf.insert if hasattr(entity.dxf, "insert") else (0, 0, 0)
                x, y, z = insert[0], insert[1], insert[2] if len(insert) > 2 else 0
                return (x, y, z, x, y, z)

            # LINE: 시작점-끝점
            elif entity_type == "LINE":
                start = entity.dxf.start
                end = entity.dxf.end
                xs = [start[0], end[0]]
                ys = [start[1], end[1]]
                zs = [start[2] if len(start) > 2 else 0, end[2] if len(end) > 2 else 0]
                return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

            # CIRCLE: 중심 + 반지름
            elif entity_type == "CIRCLE":
                center = entity.dxf.center
                radius = entity.dxf.radius
                x, y, z = center[0], center[1], center[2] if len(center) > 2 else 0
                return (x - radius, y - radius, z, x + radius, y + radius, z)

            # ARC: 중심 + 반지름 (각도 무시, 보수적 추정)
            elif entity_type == "ARC":
                center = entity.dxf.center
                radius = entity.dxf.radius
                x, y, z = center[0], center[1], center[2] if len(center) > 2 else 0
                return (x - radius, y - radius, z, x + radius, y + radius, z)

            # POLYLINE/LWPOLYLINE: 모든 정점의 min/max
            elif entity_type in ("POLYLINE", "LWPOLYLINE"):
                if hasattr(entity, "get_points"):
                    points = list(entity.get_points())
                elif hasattr(entity, "points"):
                    points = entity.points()
                else:
                    return None

                if not points:
                    return None

                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                zs = [p[2] if len(p) > 2 else 0 for p in points]
                return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

            # INSERT: 삽입점 기준
            elif entity_type == "INSERT":
                insert = entity.dxf.insert
                x, y, z = insert[0], insert[1], insert[2] if len(insert) > 2 else 0
                return (x, y, z, x, y, z)

            # DIMENSION: 정의점들의 min/max
            elif entity_type.startswith("DIMENSION"):
                points = []
                for attr in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5"):
                    if hasattr(entity.dxf, attr):
                        pt = getattr(entity.dxf, attr)
                        if pt:
                            points.append(pt)

                if not points:
                    return None

                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                zs = [p[2] if len(p) > 2 else 0 for p in points]
                return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

            # 기타 타입: 지원하지 않음
            else:
                logger.debug(f"지원하지 않는 엔티티 타입: {entity_type}")
                return None

        except Exception as e:
            logger.error(f"바운딩 박스 계산 오류: {e}")
            return None

    def find_intersecting(
        self,
        bbox: Tuple[float, float, float, float, float, float]
    ) -> List[Any]:
        """
        주어진 바운딩 박스와 교차하는 모든 엔티티 검색

        Args:
            bbox: 검색 영역 (minx, miny, minz, maxx, maxy, maxz)

        Returns:
            교차하는 엔티티 리스트
        """
        if RTREE_AVAILABLE and self._idx is not None:
            # R-tree 검색: O(log n)
            ids = list(self._idx.intersection(bbox))
            results = [self._entities[i] for i in ids if i in self._entities]
            logger.debug(f"R-tree 교차 검색: {len(results)} 엔티티 발견")
            return results
        else:
            # Fallback: 선형 검색 O(n²)
            logger.warning("rtree 미사용, 선형 검색 수행 (성능 저하)")
            results = []
            minx, miny, minz, maxx, maxy, maxz = bbox

            for entity in self._entities.values():
                entity_bbox = self._compute_bbox(entity)
                if entity_bbox is None:
                    continue

                eminx, eminy, eminz, emaxx, emaxy, emaxz = entity_bbox

                # 바운딩 박스 교차 검사
                if (eminx <= maxx and emaxx >= minx and
                    eminy <= maxy and emaxy >= miny and
                    eminz <= maxz and emaxz >= minz):
                    results.append(entity)

            logger.debug(f"선형 교차 검색: {len(results)} 엔티티 발견")
            return results

    def find_near_point(
        self,
        point: Tuple[float, float, float],
        tolerance: float = 1.0
    ) -> List[Any]:
        """
        점 주변 허용 오차 내의 엔티티 검색

        Args:
            point: 검색 기준점 (x, y, z)
            tolerance: 허용 오차 (기본 1.0)

        Returns:
            허용 오차 내 엔티티 리스트
        """
        x, y, z = point
        bbox = (
            x - tolerance,
            y - tolerance,
            z - tolerance,
            x + tolerance,
            y + tolerance,
            z + tolerance
        )
        return self.find_intersecting(bbox)

    def find_nearest(
        self,
        point: Tuple[float, float, float],
        k: int = 1
    ) -> List[Any]:
        """
        점에서 가장 가까운 K개 엔티티 검색 (K-NN)

        Args:
            point: 검색 기준점 (x, y, z)
            k: 반환할 최대 엔티티 개수 (기본 1)

        Returns:
            거리 순으로 정렬된 최대 K개 엔티티 리스트
        """
        if RTREE_AVAILABLE and self._idx is not None:
            # R-tree 최근접 이웃 검색
            ids = list(self._idx.nearest(point, k))
            results = [self._entities[i] for i in ids if i in self._entities]
            logger.debug(f"R-tree K-NN 검색: {len(results)} 엔티티 발견")
            return results
        else:
            # Fallback: 전체 거리 계산 후 정렬
            logger.warning("rtree 미사용, 선형 K-NN 검색 수행")
            distances = []
            x, y, z = point

            for entity in self._entities.values():
                bbox = self._compute_bbox(entity)
                if bbox is None:
                    continue

                # 바운딩 박스 중심까지 거리 계산
                eminx, eminy, eminz, emaxx, emaxy, emaxz = bbox
                center_x = (eminx + emaxx) / 2
                center_y = (eminy + emaxy) / 2
                center_z = (eminz + emaxz) / 2

                dist = ((center_x - x)**2 + (center_y - y)**2 + (center_z - z)**2)**0.5
                distances.append((dist, entity))

            # 거리 순 정렬 후 K개 반환
            distances.sort(key=lambda item: item[0])
            results = [entity for _, entity in distances[:k]]
            logger.debug(f"선형 K-NN 검색: {len(results)} 엔티티 발견")
            return results


def create_spatial_index(
    entities: Optional[List[Any]] = None,
    precision: float = 1e-6
) -> SpatialIndex:
    """
    공간 인덱스 팩토리 함수

    Args:
        entities: 초기 삽입할 엔티티 리스트 (선택적)
        precision: 좌표 비교 정밀도 (기본 1e-6)

    Returns:
        초기화된 SpatialIndex 인스턴스

    Example:
        >>> from ezdxf import readfile
        >>> doc = readfile("sample.dxf")
        >>> entities = list(doc.modelspace())
        >>> spatial_idx = create_spatial_index(entities)
        >>> nearby = spatial_idx.find_near_point((100, 200, 0), tolerance=5.0)
    """
    idx = SpatialIndex(precision=precision)

    if entities:
        idx.bulk_insert(entities)
        logger.info(f"공간 인덱스 생성 완료: {len(idx._entities)} 엔티티")

    return idx
