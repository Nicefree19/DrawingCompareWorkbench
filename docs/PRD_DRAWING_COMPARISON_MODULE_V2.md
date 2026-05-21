# PRD: 도면 비교 모듈 통합 개선 계획 v2
# Product Requirements Document: Drawing Comparison Module Improvement Plan v2

**문서 버전**: 2.0
**작성일**: 2025-12-23
**작성자**: Claude Code AI Assistant
**상태**: Draft
**관련 프로젝트**: TEKLA_MCP

---

## 1. Executive Summary

### 1.1 프로젝트 개요

도면 비교 모듈(DWG/DXF Differ)의 성능, 유지보수성, 확장성을 종합적으로 개선하는 프로젝트입니다. 현재 O(n²) 복잡도의 텍스트 블록 매칭, 중복된 진행률 콜백 코드, 하드코딩된 정규화 로직 등의 문제를 해결합니다.

### 1.2 현재 상태 vs 목표

| 지표 | 현재 상태 | 목표 | 개선율 |
|------|-----------|------|--------|
| 텍스트 블록 매칭 복잡도 | O(n²) | O(n log n) | 100x (10K entities) |
| SSIM 비교 시간 (4K 이미지) | ~2.0초 | ~0.5초 | 4x |
| 진행률 콜백 중복 코드 | 150 lines | 0 lines | -150 lines |
| 정규화 메서드 중복 코드 | 200+ lines | 0 lines | -200 lines |
| extract() 메서드 복잡도 | 18-20 | 10 이하 | 50% 감소 |
| 테스트 커버리지 | ~60% | 85%+ | +25% |

### 1.3 핵심 가치 제안

1. **성능**: 대용량 도면(10K+ 엔티티) 비교 시간 90% 단축
2. **유지보수성**: 중복 코드 350+ lines 제거, 메서드 복잡도 50% 감소
3. **확장성**: Strategy 패턴으로 새로운 엔티티 타입 추가 용이
4. **안정성**: 예외 처리 개선으로 임시 파일 누수 방지

---

## 2. Scope & Constraints

### 2.1 범위 내 (In Scope)

- `src/services/comparison/dwg_differ.py` 리팩토링
- `src/services/comparison/dxf_entity_extractor.py` 리팩토링
- `src/services/comparison/dxf_comparator.py` 개선
- `src/gui/unified_load_module/workers/compare_worker.py` 옵션 전달
- 신규 모듈: `progress_tracker.py`, `entity_normalizers/` 패키지

### 2.2 범위 외 (Out of Scope)

- ODA File Converter 라이센스 구매/협상
- GUI 레이아웃 변경
- 새로운 비교 알고리즘 연구 (R-tree 외)
- 다른 CAD 포맷 지원 (DGN, RVT 등)

### 2.3 제약 조건

- Python 3.9+ 호환성 유지
- ezdxf 1.0+ 의존성 유지
- Tekla API 통합 기존 인터페이스 유지
- GUI 응답성 저하 금지 (QProcess 기반 유지)

---

## 3. Phase 1: Critical Performance Optimizations

**예상 기간**: 1주
**우선순위**: P0 (Critical)
**예상 성능 개선**: 4-100x

### 3.1 P0-1: R-tree 기반 공간 인덱싱

#### 3.1.1 현재 상태 분석

**파일**: `src/services/comparison/dxf_entity_extractor.py`
**위치**: `_find_text_insertion_points()` 메서드 (미구현, 외부 호출)

현재 텍스트 블록 매칭은 모든 엔티티 쌍을 비교하는 O(n²) 복잡도:

```python
# 현재 패턴 (O(n²))
for entity_a in entities_a:
    for entity_b in entities_b:
        if distance(entity_a.position, entity_b.position) < tolerance:
            matches.append((entity_a, entity_b))
```

#### 3.1.2 목표 설계

```python
# 목표: O(n log n) R-tree 기반 공간 인덱싱
from rtree import index

class SpatialIndex:
    """R-tree 기반 공간 인덱싱 래퍼"""

    def __init__(self, precision: int = 2):
        self.precision = precision
        self.idx = index.Index()
        self.entities = {}

    def insert(self, entity_id: int, entity: NormalizedEntity):
        """엔티티를 R-tree에 삽입"""
        bbox = self._get_bounding_box(entity)
        self.idx.insert(entity_id, bbox)
        self.entities[entity_id] = entity

    def _get_bounding_box(self, entity: NormalizedEntity) -> tuple:
        """엔티티의 바운딩 박스 계산"""
        if entity.entity_type == "TEXT":
            x, y = entity.data.get("insertion_point", (0, 0))[:2]
            return (x - 1, y - 1, x + 1, y + 1)  # 점 → 작은 박스
        elif entity.entity_type == "LINE":
            start = entity.data.get("start", (0, 0, 0))
            end = entity.data.get("end", (0, 0, 0))
            return (
                min(start[0], end[0]), min(start[1], end[1]),
                max(start[0], end[0]), max(start[1], end[1])
            )
        # ... 다른 엔티티 타입

    def find_near(self, point: tuple, tolerance: float) -> List[NormalizedEntity]:
        """허용 오차 내의 근접 엔티티 검색 (O(log n))"""
        x, y = point[:2]
        bbox = (x - tolerance, y - tolerance, x + tolerance, y + tolerance)
        candidates = list(self.idx.intersection(bbox))
        return [self.entities[c] for c in candidates]
```

#### 3.1.3 구현 상세

**신규 파일**: `src/services/comparison/spatial_index.py`

```python
"""
공간 인덱싱 모듈 - R-tree 기반 O(n log n) 검색

Dependencies:
    - rtree>=1.0.0 (pip install rtree)
    - Requires libspatialindex system library
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Callable
import logging

try:
    from rtree import index
    RTREE_AVAILABLE = True
except ImportError:
    RTREE_AVAILABLE = False

from .dxf_comparator import NormalizedEntity

logger = logging.getLogger(__name__)


@dataclass
class SpatialIndex:
    """R-tree 기반 공간 인덱싱"""

    precision: int = 2
    _idx: Optional[index.Index] = field(default=None, init=False)
    _entities: Dict[int, NormalizedEntity] = field(default_factory=dict, init=False)
    _counter: int = field(default=0, init=False)

    def __post_init__(self):
        if not RTREE_AVAILABLE:
            logger.warning("rtree not available, falling back to O(n²) search")
            return
        p = index.Property()
        p.dimension = 2
        self._idx = index.Index(properties=p)

    def insert(self, entity: NormalizedEntity) -> int:
        """엔티티 삽입, ID 반환"""
        entity_id = self._counter
        self._counter += 1
        self._entities[entity_id] = entity

        if self._idx is not None:
            bbox = self._compute_bbox(entity)
            self._idx.insert(entity_id, bbox)

        return entity_id

    def bulk_insert(self, entities: List[NormalizedEntity]) -> List[int]:
        """벌크 삽입 (더 효율적)"""
        return [self.insert(e) for e in entities]

    def _compute_bbox(self, entity: NormalizedEntity) -> Tuple[float, float, float, float]:
        """엔티티별 바운딩 박스 계산"""
        data = entity.data
        etype = entity.entity_type

        if etype in ("TEXT", "MTEXT"):
            pt = data.get("insertion_point", (0, 0))
            return (pt[0] - 0.1, pt[1] - 0.1, pt[0] + 0.1, pt[1] + 0.1)

        elif etype == "LINE":
            s, e = data.get("start", (0,0,0)), data.get("end", (0,0,0))
            return (min(s[0],e[0]), min(s[1],e[1]), max(s[0],e[0]), max(s[1],e[1]))

        elif etype == "CIRCLE":
            c = data.get("center", (0, 0, 0))
            r = data.get("radius", 0)
            return (c[0]-r, c[1]-r, c[0]+r, c[1]+r)

        elif etype == "ARC":
            c = data.get("center", (0, 0, 0))
            r = data.get("radius", 0)
            return (c[0]-r, c[1]-r, c[0]+r, c[1]+r)

        elif etype in ("LWPOLYLINE", "POLYLINE"):
            points = data.get("points", [])
            if not points:
                return (0, 0, 0, 0)
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            return (min(xs), min(ys), max(xs), max(ys))

        elif etype == "INSERT":
            pt = data.get("insertion_point", (0, 0, 0))
            return (pt[0] - 1, pt[1] - 1, pt[0] + 1, pt[1] + 1)

        elif etype == "DIMENSION":
            # 치수선은 복잡하므로 넉넉한 영역 사용
            defpt = data.get("defpoint", (0, 0, 0))
            return (defpt[0] - 100, defpt[1] - 100, defpt[0] + 100, defpt[1] + 100)

        else:
            return (0, 0, 0, 0)

    def find_intersecting(self, bbox: Tuple[float, float, float, float]) -> List[NormalizedEntity]:
        """바운딩 박스와 교차하는 엔티티 검색"""
        if self._idx is None:
            # Fallback: 전체 검색
            return list(self._entities.values())

        ids = list(self._idx.intersection(bbox))
        return [self._entities[i] for i in ids]

    def find_near_point(self, point: Tuple[float, float], tolerance: float) -> List[NormalizedEntity]:
        """점 주변 허용 오차 내 엔티티 검색"""
        bbox = (
            point[0] - tolerance, point[1] - tolerance,
            point[0] + tolerance, point[1] + tolerance
        )
        return self.find_intersecting(bbox)

    def find_nearest(self, point: Tuple[float, float], count: int = 1) -> List[NormalizedEntity]:
        """가장 가까운 N개 엔티티 검색"""
        if self._idx is None:
            return list(self._entities.values())[:count]

        ids = list(self._idx.nearest((point[0], point[1], point[0], point[1]), count))
        return [self._entities[i] for i in ids]


def create_spatial_index(entities: List[NormalizedEntity], precision: int = 2) -> SpatialIndex:
    """팩토리 함수: 엔티티 리스트로 공간 인덱스 생성"""
    idx = SpatialIndex(precision=precision)
    idx.bulk_insert(entities)
    return idx
```

#### 3.1.4 통합 지점

**수정 파일**: `src/services/comparison/dxf_comparator.py`

```python
# 기존 compare() 메서드 수정
def compare(self, list_a: List[NormalizedEntity], list_b: List[NormalizedEntity], ...) -> ComparisonResult:
    # 기존: O(n²) dict 기반
    # hashes_a = {e.hash: e for e in list_a}
    # hashes_b = {e.hash: e for e in list_b}

    # 신규: R-tree 공간 인덱싱 (텍스트/치수 비교용)
    from .spatial_index import create_spatial_index, RTREE_AVAILABLE

    if RTREE_AVAILABLE:
        text_entities_a = [e for e in list_a if e.entity_type in ("TEXT", "MTEXT")]
        text_entities_b = [e for e in list_b if e.entity_type in ("TEXT", "MTEXT")]

        if text_entities_a and text_entities_b:
            idx_b = create_spatial_index(text_entities_b)
            text_matches = []
            for ea in text_entities_a:
                pt = ea.data.get("insertion_point", (0, 0))
                nearby = idx_b.find_near_point(pt[:2], tolerance=self.tolerance)
                for eb in nearby:
                    if ea.hash == eb.hash:
                        text_matches.append((ea, eb))
```

#### 3.1.5 의존성 추가

**requirements.txt 추가**:
```
rtree>=1.0.0
```

**설치 참고** (libspatialindex 필요):
```bash
# Windows
conda install -c conda-forge rtree

# Ubuntu/Debian
sudo apt-get install libspatialindex-dev
pip install rtree

# macOS
brew install spatialindex
pip install rtree
```

#### 3.1.6 수용 기준

| ID | 기준 | 검증 방법 |
|----|------|-----------|
| P0-1-AC1 | 10K 텍스트 엔티티 비교 시간 < 1초 | 벤치마크 테스트 |
| P0-1-AC2 | 메모리 사용량 증가 < 50% | 프로파일링 |
| P0-1-AC3 | rtree 미설치 시 graceful fallback | 단위 테스트 |
| P0-1-AC4 | 기존 API 호환성 100% | 회귀 테스트 |

---

### 3.2 P0-2: SSIM 다운샘플링 최적화

#### 3.2.1 현재 상태 분석

**파일**: `src/services/comparison/drawing_differ.py` (이미지 비교)

현재 SSIM은 원본 해상도에서 계산되어 4K 이미지에서 ~2초 소요.

#### 3.2.2 목표 설계

```python
def compute_ssim_optimized(img_a: np.ndarray, img_b: np.ndarray,
                           target_size: int = 1024) -> float:
    """다운샘플링된 SSIM 계산 (4x 속도 향상)"""
    import cv2
    from skimage.metrics import structural_similarity

    # 가장 긴 변을 target_size로 리사이즈
    h, w = img_a.shape[:2]
    scale = min(target_size / max(h, w), 1.0)  # 확대는 하지 않음

    if scale < 1.0:
        new_size = (int(w * scale), int(h * scale))
        img_a = cv2.resize(img_a, new_size, interpolation=cv2.INTER_AREA)
        img_b = cv2.resize(img_b, new_size, interpolation=cv2.INTER_AREA)

    # Grayscale 변환 (이미 grayscale이 아닌 경우)
    if len(img_a.shape) == 3:
        img_a = cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY)
        img_b = cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY)

    ssim_score, _ = structural_similarity(img_a, img_b, full=True)
    return ssim_score
```

#### 3.2.3 수용 기준

| ID | 기준 | 검증 방법 |
|----|------|-----------|
| P0-2-AC1 | 4K 이미지 SSIM 계산 < 0.5초 | 벤치마크 |
| P0-2-AC2 | SSIM 정확도 손실 < 5% | 비교 테스트 |
| P0-2-AC3 | 메모리 피크 50% 감소 | 프로파일링 |

---

### 3.3 P0-3: 예외 처리 및 리소스 정리

#### 3.3.1 현재 상태 분석

**파일**: `src/services/comparison/dwg_differ.py`

현재 `_cleanup_temp()` 메서드가 존재하지만, 모든 메서드에서 finally 블록으로 호출되지 않음:

```python
# dwg_differ.py:530-538
def _cleanup_temp(self):
    """임시 파일 정리"""
    if self._temp_dir and self._temp_dir.exists():
        try:
            shutil.rmtree(self._temp_dir)
            self._temp_dir = None
        except Exception as e:
            logger.warning(f"임시 파일 정리 실패: {e}")
```

**문제점**:
- `compare()` 메서드: try-except는 있으나 finally 없음 (lines 95-230)
- `compare_and_mark()` 메서드: finally 없음 (lines 232-330)
- `export_excel()` 메서드: finally 없음 (lines 332-420)

#### 3.3.2 목표 설계

```python
# 컨텍스트 매니저 패턴 도입
class DwgDiffer:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._cleanup_temp()
        return False  # 예외 재발생

    def compare(self, old_path, new_path, ...):
        try:
            # 기존 로직
            ...
        finally:
            self._cleanup_temp()
```

#### 3.3.3 수정 대상

| 메서드 | 라인 | 수정 내용 |
|--------|------|-----------|
| `compare()` | 95-230 | finally 블록 추가 |
| `compare_and_mark()` | 232-330 | finally 블록 추가 |
| `export_excel()` | 332-420 | finally 블록 추가 |
| 클래스 정의 | 50-55 | `__enter__`, `__exit__` 추가 |

#### 3.3.4 수용 기준

| ID | 기준 | 검증 방법 |
|----|------|-----------|
| P0-3-AC1 | 예외 발생 시 임시 파일 100% 정리 | 통합 테스트 |
| P0-3-AC2 | 컨텍스트 매니저 사용 가능 | 단위 테스트 |
| P0-3-AC3 | 기존 호출 방식 호환 | 회귀 테스트 |

---

## 4. Phase 2: High Priority Refactoring

**예상 기간**: 2주
**우선순위**: P1 (High)
**예상 코드 감소**: -350 lines

### 4.1 P1-1: ProgressTracker 클래스 추출

#### 4.1.1 현재 상태 분석

**파일**: `src/services/comparison/dwg_differ.py`

동일한 진행률 콜백 패턴이 4곳에서 반복됨:

```python
# Lines 153-159: compare() 내 Old 파일 처리
def progress_a(current, total, msg):
    if progress_callback:
        if total > 0:
            pct = 20 + int(30 * current / total)
        else:
            pct = 35
        progress_callback(pct, 100, f"Old 파일: {msg}")

# Lines 175-181: compare() 내 New 파일 처리
def progress_b(current, total, msg):
    if progress_callback:
        if total > 0:
            pct = 50 + int(30 * current / total)
        else:
            pct = 65
        progress_callback(pct, 100, f"New 파일: {msg}")

# Lines 283-289: compare_and_mark() 내 유사 패턴
# Lines 303-309: compare_and_mark() 내 유사 패턴
```

#### 4.1.2 목표 설계

**신규 파일**: `src/services/comparison/progress_tracker.py`

```python
"""
진행률 추적 모듈 - 중복 콜백 패턴 통합

Usage:
    tracker = ProgressTracker(callback=my_callback, is_cancelled=cancel_fn)

    # 스테이지 설정 후 리포트
    tracker.set_stage("extraction_old", 20, 50, "Old 파일")
    for i, item in enumerate(items):
        if tracker.report(i, len(items), f"처리 중: {item}"):
            break  # 취소됨

    # 또는 서브 트래커 생성
    sub_callback = tracker.create_sub_tracker("extraction_old", 20, 50, "Old 파일")
    extractor.extract(doc, progress_callback=sub_callback)
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class ProgressStage:
    """진행률 스테이지 정의"""
    name: str
    start_percent: int
    end_percent: int
    message_prefix: str = ""

    def map_percent(self, current: int, total: int) -> int:
        """현재/전체를 스테이지 범위로 매핑"""
        if total <= 0:
            return (self.start_percent + self.end_percent) // 2
        ratio = current / total
        return self.start_percent + int((self.end_percent - self.start_percent) * ratio)


@dataclass
class ProgressTracker:
    """중앙 집중식 진행률 추적기"""

    callback: Optional[Callable[[int, int, str], None]] = None
    is_cancelled_fn: Optional[Callable[[], bool]] = None
    total_percent: int = 100

    _current_stage: Optional[ProgressStage] = field(default=None, init=False)
    _stages: List[ProgressStage] = field(default_factory=list, init=False)

    def set_stage(self, name: str, start: int, end: int, prefix: str = "") -> "ProgressTracker":
        """현재 스테이지 설정 (체이닝 지원)"""
        self._current_stage = ProgressStage(name, start, end, prefix)
        self._stages.append(self._current_stage)
        logger.debug(f"Progress stage: {name} ({start}%-{end}%)")
        return self

    def report(self, current: int, total: int, message: str = "") -> bool:
        """
        진행률 보고

        Args:
            current: 현재 진행 수
            total: 전체 수
            message: 상세 메시지

        Returns:
            True if cancelled, False otherwise
        """
        # 취소 확인
        if self.is_cancelled_fn and self.is_cancelled_fn():
            logger.info("Progress cancelled by user")
            return True

        # 콜백 실행
        if self.callback and self._current_stage:
            stage = self._current_stage
            pct = stage.map_percent(current, total)
            full_msg = f"{stage.message_prefix}: {message}" if stage.message_prefix else message
            self.callback(pct, self.total_percent, full_msg)

        return False

    def create_sub_tracker(self, name: str, start: int, end: int,
                           prefix: str = "") -> Callable[[int, int, str], None]:
        """
        서브 트래커 콜백 생성 (기존 API 호환용)

        Returns:
            (current, total, msg) -> None 형태의 콜백 함수
        """
        stage = ProgressStage(name, start, end, prefix)
        self._stages.append(stage)

        def sub_callback(current: int, total: int, msg: str):
            if self.callback:
                pct = stage.map_percent(current, total)
                full_msg = f"{stage.message_prefix}: {msg}" if stage.message_prefix else msg
                self.callback(pct, self.total_percent, full_msg)

        return sub_callback

    def is_cancelled(self) -> bool:
        """취소 여부 확인"""
        return self.is_cancelled_fn() if self.is_cancelled_fn else False

    def report_simple(self, percent: int, message: str) -> bool:
        """단순 퍼센트 보고 (스테이지 무시)"""
        if self.is_cancelled_fn and self.is_cancelled_fn():
            return True
        if self.callback:
            self.callback(percent, self.total_percent, message)
        return False


# 팩토리 함수
def create_tracker(
    callback: Optional[Callable[[int, int, str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None
) -> ProgressTracker:
    """ProgressTracker 팩토리 함수"""
    return ProgressTracker(callback=callback, is_cancelled_fn=is_cancelled)
```

#### 4.1.3 마이그레이션 가이드

**Before (dwg_differ.py)**:
```python
def compare(self, old_path, new_path, progress_callback=None, is_cancelled=None, ...):
    # Lines 153-159 제거
    def progress_a(current, total, msg):
        if progress_callback:
            if total > 0:
                pct = 20 + int(30 * current / total)
            else:
                pct = 35
            progress_callback(pct, 100, f"Old 파일: {msg}")

    # Lines 175-181 제거
    def progress_b(current, total, msg):
        if progress_callback:
            if total > 0:
                pct = 50 + int(30 * current / total)
            else:
                pct = 65
            progress_callback(pct, 100, f"New 파일: {msg}")

    # 사용
    old_entities = self.extractor.extract(old_doc, progress_callback=progress_a, ...)
    new_entities = self.extractor.extract(new_doc, progress_callback=progress_b, ...)
```

**After (dwg_differ.py)**:
```python
from .progress_tracker import create_tracker

def compare(self, old_path, new_path, progress_callback=None, is_cancelled=None, ...):
    tracker = create_tracker(callback=progress_callback, is_cancelled=is_cancelled)

    # 서브 트래커로 기존 API 호환
    progress_a = tracker.create_sub_tracker("extraction_old", 20, 50, "Old 파일")
    progress_b = tracker.create_sub_tracker("extraction_new", 50, 80, "New 파일")

    old_entities = self.extractor.extract(old_doc, progress_callback=progress_a, ...)
    new_entities = self.extractor.extract(new_doc, progress_callback=progress_b, ...)
```

#### 4.1.4 수용 기준

| ID | 기준 | 검증 방법 |
|----|------|-----------|
| P1-1-AC1 | 중복 코드 150 lines 제거 | diff 확인 |
| P1-1-AC2 | 기존 progress_callback 인터페이스 호환 | 회귀 테스트 |
| P1-1-AC3 | 취소 기능 정상 동작 | 통합 테스트 |
| P1-1-AC4 | 단위 테스트 커버리지 90%+ | pytest-cov |

---

### 4.2 P1-2: extract() 메서드 복잡도 감소

#### 4.2.1 현재 상태 분석

**파일**: `src/services/comparison/dxf_entity_extractor.py`
**메서드**: `extract()` (lines 174-321)
**현재 복잡도**: 18-20 (McCabe)

문제점:
- 147 lines의 단일 메서드
- 다중 중첩 조건문
- 엔티티 타입별 분기가 메서드 내부에 하드코딩

#### 4.2.2 목표 설계

```python
# 메서드 분리 전략
class DxfEntityExtractor:
    def extract(self, doc, ...):
        """메인 추출 (복잡도 10 이하)"""
        entities = []

        # 1. 설정 검증
        self._validate_config(include_layers, exclude_layers)

        # 2. 레이어 필터링
        target_layers = self._filter_layers(doc, include_layers, exclude_layers)

        # 3. 엔티티 추출 (위임)
        for layer in target_layers:
            layer_entities = self._extract_layer_entities(doc, layer, ...)
            entities.extend(layer_entities)

        # 4. 블록 확장 (옵션)
        if expand_blocks:
            entities = self._expand_block_references(doc, entities, ...)

        return entities

    def _validate_config(self, include_layers, exclude_layers):
        """설정 검증 (복잡도 3)"""
        if include_layers and exclude_layers:
            overlap = set(include_layers) & set(exclude_layers)
            if overlap:
                logger.warning(f"Layer conflict: {overlap}")

    def _filter_layers(self, doc, include_layers, exclude_layers) -> List[str]:
        """레이어 필터링 (복잡도 5)"""
        all_layers = [layer.dxf.name for layer in doc.layers]

        if include_layers:
            return [l for l in all_layers if l in include_layers]
        elif exclude_layers:
            return [l for l in all_layers if l not in exclude_layers]
        return all_layers

    def _extract_layer_entities(self, doc, layer, ...) -> List[NormalizedEntity]:
        """레이어별 엔티티 추출 (복잡도 8)"""
        # NormalizerRegistry 사용 (Strategy 패턴)
        ...
```

#### 4.2.3 수용 기준

| ID | 기준 | 검증 방법 |
|----|------|-----------|
| P1-2-AC1 | extract() 복잡도 10 이하 | radon 분석 |
| P1-2-AC2 | 전체 테스트 통과 | pytest |
| P1-2-AC3 | 성능 회귀 없음 | 벤치마크 |

---

### 4.3 P1-3: Strategy 패턴 - EntityNormalizer

#### 4.3.1 현재 상태 분석

**파일**: `src/services/comparison/dxf_entity_extractor.py`
**위치**: lines 614-775

8개의 `_normalize_*` 메서드가 유사한 패턴으로 반복:

```python
# 현재: 하드코딩된 정규화 메서드들
def _normalize_line(self, entity) -> NormalizedEntity: ...      # ~20 lines
def _normalize_circle(self, entity) -> NormalizedEntity: ...    # ~15 lines
def _normalize_arc(self, entity) -> NormalizedEntity: ...       # ~20 lines
def _normalize_polyline(self, entity) -> NormalizedEntity: ...  # ~30 lines
def _normalize_text(self, entity) -> NormalizedEntity: ...      # ~25 lines
def _normalize_mtext(self, entity) -> NormalizedEntity: ...     # ~25 lines
def _normalize_dimension(self, entity) -> NormalizedEntity: ... # ~35 lines
def _normalize_insert(self, entity) -> NormalizedEntity: ...    # ~30 lines
```

#### 4.3.2 목표 설계

**신규 패키지**: `src/services/comparison/entity_normalizers/`

```
entity_normalizers/
├── __init__.py           # 패키지 초기화 및 자동 등록
├── base.py               # EntityNormalizer ABC
├── registry.py           # NormalizerRegistry
├── line_normalizer.py    # LINE 정규화
├── circle_normalizer.py  # CIRCLE 정규화
├── arc_normalizer.py     # ARC 정규화
├── polyline_normalizer.py # POLYLINE, LWPOLYLINE 정규화
├── text_normalizer.py    # TEXT 정규화
├── mtext_normalizer.py   # MTEXT 정규화
├── dimension_normalizer.py # DIMENSION 정규화
└── insert_normalizer.py  # INSERT (블록 참조) 정규화
```

#### 4.3.3 핵심 구현

**base.py**:
```python
"""엔티티 정규화 추상 베이스 클래스"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple
import hashlib


@dataclass
class NormalizedEntity:
    """정규화된 엔티티 데이터"""
    entity_type: str
    layer: str
    data: Dict[str, Any]
    hash: str
    original_handle: Optional[str] = None


class EntityNormalizer(ABC):
    """엔티티 정규화 추상 베이스 클래스"""

    def __init__(self, precision: int = 2):
        self.precision = precision

    @abstractmethod
    def get_entity_type(self) -> str:
        """지원하는 DXF 엔티티 타입 반환"""
        pass

    @abstractmethod
    def normalize(self, entity, precision: int = None) -> NormalizedEntity:
        """엔티티 정규화"""
        pass

    def _round_point(self, point: Tuple, precision: int = None) -> Tuple:
        """좌표 반올림"""
        p = precision if precision is not None else self.precision
        return tuple(round(v, p) for v in point)

    def _compute_hash(self, entity_type: str, layer: str, data: Dict) -> str:
        """해시 계산"""
        hash_input = f"{entity_type}|{layer}|{sorted(data.items())}"
        return hashlib.md5(hash_input.encode()).hexdigest()[:16]
```

**registry.py**:
```python
"""NormalizerRegistry - 엔티티 정규화기 중앙 레지스트리"""

from typing import Dict, Optional, Type, List
import logging

from .base import EntityNormalizer, NormalizedEntity

logger = logging.getLogger(__name__)


class NormalizerRegistry:
    """엔티티 정규화기 레지스트리 (싱글턴)"""

    _instance: Optional["NormalizerRegistry"] = None
    _normalizers: Dict[str, EntityNormalizer] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._normalizers = {}
        return cls._instance

    def register(self, normalizer: EntityNormalizer) -> None:
        """정규화기 등록"""
        entity_type = normalizer.get_entity_type()
        self._normalizers[entity_type] = normalizer
        logger.debug(f"Registered normalizer: {entity_type}")

    def register_class(self, normalizer_class: Type[EntityNormalizer],
                       precision: int = 2) -> None:
        """정규화기 클래스 등록 (인스턴스 자동 생성)"""
        normalizer = normalizer_class(precision=precision)
        self.register(normalizer)

    def get_normalizer(self, entity_type: str) -> Optional[EntityNormalizer]:
        """엔티티 타입에 맞는 정규화기 반환"""
        return self._normalizers.get(entity_type.upper())

    def normalize(self, entity, precision: int = None) -> Optional[NormalizedEntity]:
        """엔티티 정규화 (자동 정규화기 선택)"""
        entity_type = entity.dxftype()
        normalizer = self.get_normalizer(entity_type)

        if normalizer is None:
            logger.debug(f"No normalizer for: {entity_type}")
            return None

        return normalizer.normalize(entity, precision)

    def get_supported_types(self) -> List[str]:
        """지원하는 엔티티 타입 목록"""
        return list(self._normalizers.keys())

    def clear(self) -> None:
        """레지스트리 초기화 (테스트용)"""
        self._normalizers.clear()


# 전역 레지스트리 인스턴스
_registry = NormalizerRegistry()


def get_registry() -> NormalizerRegistry:
    """전역 레지스트리 반환"""
    return _registry


def register_normalizer(normalizer: EntityNormalizer) -> None:
    """전역 레지스트리에 정규화기 등록"""
    _registry.register(normalizer)
```

**line_normalizer.py** (예시):
```python
"""LINE 엔티티 정규화기"""

from .base import EntityNormalizer, NormalizedEntity
from .registry import register_normalizer


class LineNormalizer(EntityNormalizer):
    """LINE 엔티티 정규화"""

    def get_entity_type(self) -> str:
        return "LINE"

    def normalize(self, entity, precision: int = None) -> NormalizedEntity:
        p = precision if precision is not None else self.precision

        start = self._round_point(entity.dxf.start, p)
        end = self._round_point(entity.dxf.end, p)

        # 방향 정규화: 항상 작은 좌표가 start
        if start > end:
            start, end = end, start

        data = {
            "start": start,
            "end": end,
        }

        layer = entity.dxf.layer
        hash_val = self._compute_hash("LINE", layer, data)

        return NormalizedEntity(
            entity_type="LINE",
            layer=layer,
            data=data,
            hash=hash_val,
            original_handle=entity.dxf.handle
        )


# 자동 등록
register_normalizer(LineNormalizer())
```

**__init__.py**:
```python
"""엔티티 정규화기 패키지"""

from .base import EntityNormalizer, NormalizedEntity
from .registry import NormalizerRegistry, get_registry, register_normalizer

# 자동 등록을 위해 모든 정규화기 임포트
from .line_normalizer import LineNormalizer
from .circle_normalizer import CircleNormalizer
from .arc_normalizer import ArcNormalizer
from .polyline_normalizer import PolylineNormalizer
from .text_normalizer import TextNormalizer
from .mtext_normalizer import MTextNormalizer
from .dimension_normalizer import DimensionNormalizer
from .insert_normalizer import InsertNormalizer

__all__ = [
    "EntityNormalizer",
    "NormalizedEntity",
    "NormalizerRegistry",
    "get_registry",
    "register_normalizer",
]
```

#### 4.3.4 마이그레이션 가이드

**Before (dxf_entity_extractor.py)**:
```python
# Lines 614-775 제거
def _normalize_line(self, entity): ...
def _normalize_circle(self, entity): ...
# ... 6개 더
```

**After (dxf_entity_extractor.py)**:
```python
from .entity_normalizers import get_registry

class DxfEntityExtractor:
    def __init__(self, precision: int = 2):
        self.precision = precision
        self.registry = get_registry()

    def _normalize_entity(self, entity) -> Optional[NormalizedEntity]:
        """레지스트리를 통한 엔티티 정규화"""
        return self.registry.normalize(entity, self.precision)
```

#### 4.3.5 수용 기준

| ID | 기준 | 검증 방법 |
|----|------|-----------|
| P1-3-AC1 | 200+ lines 중복 코드 제거 | diff 확인 |
| P1-3-AC2 | 8개 엔티티 타입 100% 지원 | 단위 테스트 |
| P1-3-AC3 | 새 엔티티 추가 < 50 lines | 코드 리뷰 |
| P1-3-AC4 | 기존 해시 호환성 유지 | 회귀 테스트 |

---

## 5. Phase 3: Quick Wins

**예상 기간**: 2-3일
**우선순위**: P2 (Medium)
**예상 영향**: 사용자 경험 개선, 안정성 향상

### 5.1 QW-1: expand_blocks 옵션 전달

**현재**: `expand_blocks=False` 파라미터가 체인을 통해 전달되지 않음

**수정 파일**:
- `src/gui/unified_load_module/workers/compare_worker.py:127`
- `src/services/comparison/dwg_differ.py:145`

**변경 내용**:
```python
# compare_worker.py
expand_blocks = self.options.get("expand_blocks", False)
result = differ.compare(..., expand_blocks=expand_blocks, ...)

# dwg_differ.py
def compare(self, ..., expand_blocks: bool = False, ...):
    old_entities = self.extractor.extract(old_doc, expand_blocks=expand_blocks, ...)
    new_entities = self.extractor.extract(new_doc, expand_blocks=expand_blocks, ...)
```

### 5.2 QW-2: 해시 충돌 경고

**현재**: `dxf_comparator.py:225-226`에서 중복 해시 조용히 덮어씀

**수정 내용**:
```python
# Before
hashes_a = {e.hash: e for e in list_a}

# After
hashes_a = {}
for e in list_a:
    if e.hash in hashes_a:
        logger.warning(f"Hash collision detected: {e.hash} ({e.entity_type})")
    hashes_a[e.hash] = e
```

### 5.3 QW-3: find_near_matches 캐싱

**위치**: `src/services/comparison/drawing_differ.py`

**수정 내용**:
```python
from functools import lru_cache

@lru_cache(maxsize=1000)
def _cached_similarity(hash_a: str, hash_b: str) -> float:
    """해시 기반 유사도 캐싱"""
    # SSIM 등 비용 높은 계산 캐시
    ...
```

### 5.4 QW-4: 엔티티 수 제한 경고

**위치**: `src/services/comparison/dxf_entity_extractor.py`

**수정 내용**:
```python
ENTITY_LIMIT_WARNING = 50000

def extract(self, doc, ...):
    ...
    if len(entities) > ENTITY_LIMIT_WARNING:
        logger.warning(
            f"Large entity count ({len(entities)}). "
            f"Consider using layer filters for better performance."
        )
    return entities
```

### 5.5 Quick Wins 수용 기준

| ID | 항목 | 기준 | 검증 방법 |
|----|------|------|-----------|
| QW-1-AC | expand_blocks | GUI 옵션 동작 | E2E 테스트 |
| QW-2-AC | 해시 경고 | 로그에 경고 출력 | 단위 테스트 |
| QW-3-AC | 캐싱 | 반복 호출 시 속도 향상 | 벤치마크 |
| QW-4-AC | 엔티티 경고 | 50K 초과 시 경고 | 단위 테스트 |

---

## 6. Phase 4: Medium Priority Enhancements

**예상 기간**: 2주 (Phase 2 완료 후)
**우선순위**: P3 (Medium)

### 6.1 P3-1: ODA 배치 처리

- ODA File Converter 설치 확인
- 다중 DWG 파일 일괄 변환
- 변환 큐 관리

### 6.2 P3-2: 의존성 주입 (DI)

- DwgDiffer 생성자에서 Extractor, Comparator 주입 가능하도록
- 테스트 용이성 향상

### 6.3 P3-3: 타입 힌트 완성

- 모든 public 메서드에 타입 힌트 추가
- mypy 검증 통과

### 6.4 P3-4: 청크 기반 Excel 스트리밍

- openpyxl write_only 모드 사용
- 대용량 비교 결과 메모리 효율적 저장

---

## 7. Testing Strategy

### 7.1 단위 테스트

**대상 모듈**:
- `progress_tracker.py`: 10+ 테스트
- `spatial_index.py`: 8+ 테스트
- `entity_normalizers/`: 각 정규화기당 5+ 테스트

**테스트 파일 구조**:
```
tests/unit/services/comparison/
├── test_progress_tracker.py
├── test_spatial_index.py
├── test_normalizer_registry.py
└── entity_normalizers/
    ├── test_line_normalizer.py
    ├── test_circle_normalizer.py
    └── ...
```

### 7.2 통합 테스트

**시나리오**:
1. DWG 파일 비교 전체 파이프라인
2. 취소 기능 동작 확인
3. 대용량 파일 (10K+ 엔티티) 처리
4. 임시 파일 정리 확인

### 7.3 성능 테스트

**벤치마크 케이스**:
| 케이스 | 엔티티 수 | 목표 시간 |
|--------|-----------|-----------|
| Small | 100 | < 0.1초 |
| Medium | 1,000 | < 0.5초 |
| Large | 10,000 | < 2초 |
| XLarge | 50,000 | < 10초 |

### 7.4 회귀 테스트

- 기존 `tests/unit/services/comparison/` 테스트 100% 통과
- GUI 워커 정상 동작
- API 호환성 유지

---

## 8. Implementation Timeline

```
Week 1: Phase 1 (Critical)
├── Day 1-2: P0-1 R-tree 구현
├── Day 3: P0-2 SSIM 다운샘플링
├── Day 4: P0-3 예외 처리
└── Day 5: Phase 1 테스트 및 검증

Week 2-3: Phase 2 (High)
├── Day 1-2: P1-1 ProgressTracker
├── Day 3-4: P1-2 extract() 리팩토링
├── Day 5-7: P1-3 Strategy 패턴
└── Day 8-10: Phase 2 테스트 및 검증

Week 3 (후반): Phase 3 (Quick Wins)
├── Day 1: QW-1, QW-2
└── Day 2: QW-3, QW-4

Week 4+: Phase 4 (Medium)
├── P3-1: ODA 배치 처리
├── P3-2: 의존성 주입
├── P3-3: 타입 힌트
└── P3-4: Excel 스트리밍
```

---

## 9. Risk Assessment

### 9.1 기술적 리스크

| 리스크 | 영향 | 확률 | 완화 전략 |
|--------|------|------|-----------|
| rtree 의존성 설치 실패 | 중 | 낮 | Fallback O(n²) 구현 |
| SSIM 정확도 손실 | 중 | 중 | 조정 가능한 다운샘플 비율 |
| 레거시 코드 호환성 | 높 | 중 | 충분한 회귀 테스트 |

### 9.2 일정 리스크

| 리스크 | 영향 | 확률 | 완화 전략 |
|--------|------|------|-----------|
| Phase 2 복잡도 예상 초과 | 중 | 중 | Phase 3 먼저 진행 가능 |
| 테스트 작성 시간 부족 | 중 | 낮 | 핵심 경로 우선 테스트 |

---

## 10. Success Metrics

### 10.1 정량적 지표

- 텍스트 블록 매칭 성능: 100x 향상 (10K 엔티티 기준)
- SSIM 계산 성능: 4x 향상
- 코드 라인 수: -350 lines
- 메서드 복잡도: 50% 감소
- 테스트 커버리지: 85%+

### 10.2 정성적 지표

- 새 엔티티 타입 추가 용이성
- 코드 가독성 향상
- 유지보수 시간 단축

---

## 11. Appendix

### A. 파일 변경 요약

| 파일 | 변경 유형 | 예상 라인 변경 |
|------|-----------|----------------|
| `dwg_differ.py` | 수정 | -100, +50 |
| `dxf_entity_extractor.py` | 수정 | -200, +30 |
| `dxf_comparator.py` | 수정 | +20 |
| `compare_worker.py` | 수정 | +5 |
| `progress_tracker.py` | 신규 | +120 |
| `spatial_index.py` | 신규 | +150 |
| `entity_normalizers/` | 신규 | +400 |

### B. 의존성 변경

**추가**:
- `rtree>=1.0.0` (선택적, fallback 지원)

**유지**:
- `ezdxf>=1.0.0`
- `opencv-python>=4.5.0`
- `scikit-image>=0.18.0`
- `openpyxl>=3.0.0`

### C. 관련 문서

- [docs/GAP_ANALYSIS_COMPLETION_REPORT.md](GAP_ANALYSIS_COMPLETION_REPORT.md)
- [docs/GUI_P0_THREAD_SAFETY_FIX.md](GUI_P0_THREAD_SAFETY_FIX.md)
- [docs/PHASE1_2_COMPLETION_REPORT.md](PHASE1_2_COMPLETION_REPORT.md)

---

**문서 끝**

*이 PRD는 Claude Code AI Assistant에 의해 자동 생성되었습니다.*
