# 도면 비교 모듈 Phase 3 상세 작업 계획

**작성일**: 2025-12-23
**버전**: 3.0
**상태**: ✅ Phase 3 완료 (P3-1 ~ P3-6 전체 완료)
**최종 업데이트**: 2025-12-23 20:09

---

## 완료 현황

| 작업 | 상태 | 테스트 | 비고 |
|------|------|--------|------|
| P3-1: MODIFIED 변경 유형 | ✅ 완료 | 37/37 | compare_with_modified_detection(), 수용 기준 AC1-AC5 검증 완료 |
| P3-2: 민감도 Config 도입 | ✅ 완료 | 40/40 | ComparisonConfig, SensitivityConfig, LayerPriorityConfig |
| P3-3: expand_blocks 전달 | ✅ 완료 | 32/32 | compare_worker.py → DwgDiffer 연결 |
| P3-4: 레이어 이동/우선순위 | ✅ 완료 | 28/28 | LayerStatistics, _detect_layer_moves(), _classify_change_priority() |
| P3-5: OCR Confidence 라벨링 | ✅ 완료 | 21/21 | OCRTextBlock.check_confidence(), ocr_confidence_threshold 설정 |
| P3-6: DXF 시각화 UI 연결 | ✅ 완료 | 37/37 | VisualizationService, HTML 리포트 내보내기, 클릭 영역 네비게이션 |

**총 테스트**: 303 통과 (2 스킵 - R-tree 미설치 환경)

---

## 1. 개요

### 1.1 목적

Phase 1-2에서 구축한 도면 비교 모듈의 핵심 인프라를 기반으로,
실무에서 요구하는 **변경점 우선순위 및 민감도 기준**을 구현합니다.

### 1.2 핵심 목표

| 목표 | 설명 |
|------|------|
| MODIFIED 유형 구현 | 추가/삭제 외에 수정(이동/회전/스케일/값변경) 감지 |
| 민감도 기준 적용 | 치수 1mm, 위치 1mm, 회전 0.1° 임계값 |
| 블록 내부 비교 | expand_blocks 옵션 실제 동작 |
| 레이어 우선순위 | 구조/그리드/치수 레이어 고우선순위 처리 |
| OCR 신뢰도 활용 | 낮은 confidence에 "검토 필요" 라벨 |
| 시각화 연결 | DXF 렌더러를 UI에 통합 |

### 1.3 현재 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                     compare_worker.py                        │
│                    (UI Worker Thread)                        │
└─────────────────────────┬───────────────────────────────────┘
                          │ expand_blocks (미전달)
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                      dwg_differ.py                           │
│                   (DWG→DXF 변환 + 비교)                      │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────────┐ ┌───────────────┐ ┌───────────────────────┐
│ dxf_entity_     │ │ dxf_          │ │ drawing_differ.py     │
│ extractor.py    │ │ comparator.py │ │ (이미지 비교)         │
│ (엔티티 추출)   │ │ (비교 엔진)   │ │                       │
└─────────────────┘ └───────────────┘ └───────────────────────┘
        │                   │
        │ precision=2       │ use_rtree (near-match 미사용)
        │ (0.01mm)          │
        ▼                   ▼
┌─────────────────┐ ┌───────────────────────────────────────┐
│ entity_         │ │ spatial_index.py                      │
│ normalizers.py  │ │ (R-tree, near-match 구현됨)           │
│ (Strategy)      │ │ → 비교에 미연결                       │
└─────────────────┘ └───────────────────────────────────────┘
```

---

## 2. 작업 항목 상세

---

### P3-1: MODIFIED 변경 유형 구현

**우선순위**: Critical
**예상 소요**: 3-4일
**의존성**: 없음

#### 2.1.1 문제 정의

현재 `dxf_comparator.py`의 비교 결과:
- `added`: 새 도면에만 있는 엔티티
- `deleted`: 이전 도면에만 있는 엔티티
- `modified`: **미사용** (near-match가 비교에 연결되지 않음)

**실제 문제**:
```python
# dxf_comparator.py:compare() - 현재 코드
old_hashes = {e.hash for e in old_entities}
new_hashes = {e.hash for e in new_entities}

added = new_hashes - old_hashes      # 새로 추가된 해시
deleted = old_hashes - new_hashes    # 삭제된 해시
# modified = ???  ← 구현 안됨
```

치수가 1500→1600으로 변경되면:
- `deleted`: hash("DIM:1500:...")
- `added`: hash("DIM:1600:...")
- 사용자에게는 "치수 변경"이 아닌 "삭제+추가"로 표시

#### 2.1.2 구현 방안

**Step 1: DxfChange 데이터 클래스 확장**

```python
# dxf_comparator.py
@dataclass
class DxfChange:
    change_type: Literal["added", "deleted", "modified"]
    entity_type: str
    layer: str
    location: Tuple[float, float]
    data: Dict[str, Any]

    # 신규 필드
    old_data: Optional[Dict[str, Any]] = None  # modified일 때 이전 값
    change_detail: Optional[str] = None         # "1500 → 1600 (+100)"
    change_category: Optional[str] = None       # "dimension", "position", "rotation"
```

**Step 2: Near-Match 연결**

```python
# dxf_comparator.py:compare_with_modified_detection()
def compare_with_modified_detection(
    self,
    old_entities: List[NormalizedEntity],
    new_entities: List[NormalizedEntity],
    tolerance: float = 1.0,  # mm 단위
) -> ComparisonResult:
    """MODIFIED 감지를 포함한 비교"""

    # 1. 기존 해시 기반 비교
    old_hashes = {e.hash: e for e in old_entities}
    new_hashes = {e.hash: e for e in new_entities}

    pure_added = set(new_hashes.keys()) - set(old_hashes.keys())
    pure_deleted = set(old_hashes.keys()) - set(new_hashes.keys())

    # 2. Near-match로 MODIFIED 탐지
    modified = []
    matched_added = set()
    matched_deleted = set()

    for del_hash in pure_deleted:
        old_entity = old_hashes[del_hash]

        # 같은 타입 + 근접 위치 + 같은 레이어에서 매칭 탐색
        candidates = self._find_near_matches(
            old_entity,
            [new_hashes[h] for h in pure_added],
            tolerance
        )

        if candidates:
            new_entity = candidates[0]
            change = self._create_modified_change(old_entity, new_entity)
            modified.append(change)
            matched_added.add(new_entity.hash)
            matched_deleted.add(del_hash)

    # 3. 결과 분류
    final_added = pure_added - matched_added
    final_deleted = pure_deleted - matched_deleted

    return ComparisonResult(
        added=[...],
        deleted=[...],
        modified=modified,
    )
```

**Step 3: 변경 상세 생성**

```python
def _create_modified_change(
    self,
    old: NormalizedEntity,
    new: NormalizedEntity
) -> DxfChange:
    """MODIFIED 변경 상세 생성"""

    change_detail = []
    change_category = []

    # 치수 변경 감지
    if old.entity_type == "DIMENSION":
        old_val = old.data.get("measurement", 0)
        new_val = new.data.get("measurement", 0)
        diff = new_val - old_val
        if abs(diff) >= 1.0:  # 1mm 이상
            change_detail.append(f"{old_val:.1f} → {new_val:.1f} ({diff:+.1f})")
            change_category.append("dimension")

    # 위치 변경 감지
    old_loc = old.location
    new_loc = new.location
    dist = math.sqrt((new_loc[0]-old_loc[0])**2 + (new_loc[1]-old_loc[1])**2)
    if dist >= 1.0:  # 1mm 이상
        change_detail.append(f"위치 이동 {dist:.1f}mm")
        change_category.append("position")

    # 회전 변경 감지 (INSERT, ARC 등)
    if "rotation" in old.data and "rotation" in new.data:
        old_rot = old.data["rotation"]
        new_rot = new.data["rotation"]
        rot_diff = abs(new_rot - old_rot)
        if rot_diff >= 0.1:  # 0.1° 이상
            change_detail.append(f"회전 {old_rot:.1f}° → {new_rot:.1f}°")
            change_category.append("rotation")

    return DxfChange(
        change_type="modified",
        entity_type=new.entity_type,
        layer=new.layer,
        location=new.location,
        data=new.data,
        old_data=old.data,
        change_detail=" | ".join(change_detail),
        change_category=",".join(change_category),
    )
```

#### 2.1.3 영향 파일

| 파일 | 변경 내용 |
|------|----------|
| `dxf_comparator.py` | DxfChange 확장, compare_with_modified_detection() 추가 |
| `spatial_index.py` | find_near_matches() 활용 |
| `dwg_differ.py` | 새 비교 메서드 호출 |

#### 2.1.4 수용 기준

- [x] AC1: 치수 1500→1600 변경 시 `modified` 타입으로 검출 ✅
- [x] AC2: 변경 상세에 "1500.0 → 1600.0 (+100.0)" 포함 ✅
- [x] AC3: 위치 이동 2mm 시 "위치 이동 2.0mm" 표시 ✅
- [x] AC4: 회전 5° 변경 시 "회전 0.0° → 5.0°" 표시 ✅
- [x] AC5: 전체 회귀 테스트 245개 통과 (2 스킵) ✅

---

### P3-2: 민감도 기준 Config 도입

**우선순위**: Critical
**예상 소요**: 2일
**의존성**: P3-1

#### 2.2.1 문제 정의

현재 하드코딩된 값들:
```python
# dxf_entity_extractor.py
PRECISION = 2  # 소수점 2자리 = 0.01mm

# dxf_comparator.py
tolerance = 0.1  # 사용되지 않음

# entity_normalizers.py
precision: int = 2  # 기본값
```

문서 요구사항:
- 치수값: 절대 1mm 이상 또는 0.1% 이상
- 위치: 1mm 이상 이동
- 회전: 0.1° 이상
- 좌표 허용오차: 0.1mm (해시 비교용)

#### 2.2.2 구현 방안

**Step 1: Config 데이터 클래스**

```python
# src/services/comparison/comparison_config.py (신규)

from dataclasses import dataclass
from typing import Optional

@dataclass
class SensitivityConfig:
    """변경 감지 민감도 설정"""

    # 좌표 정밀도 (해시 생성용)
    coordinate_precision: int = 1  # 소수점 1자리 = 0.1mm

    # 변경 감지 임계값
    dimension_abs_threshold: float = 1.0      # mm
    dimension_rel_threshold: float = 0.001    # 0.1%
    position_threshold: float = 1.0           # mm
    rotation_threshold: float = 0.1           # degrees
    scale_threshold: float = 0.01             # 1%

    # Near-match 탐색 반경
    near_match_radius: float = 10.0           # mm

    # 텍스트 비교
    text_case_sensitive: bool = False
    text_whitespace_normalize: bool = True


@dataclass
class LayerPriorityConfig:
    """레이어 우선순위 설정"""

    # 고우선순위 레이어 패턴 (정규식)
    high_priority_patterns: list = None

    # 저우선순위 레이어 패턴 (스타일 전용)
    low_priority_patterns: list = None

    def __post_init__(self):
        if self.high_priority_patterns is None:
            self.high_priority_patterns = [
                r"(?i)struct.*",      # 구조
                r"(?i)grid.*",        # 그리드
                r"(?i)dim.*",         # 치수
                r"(?i)column.*",      # 기둥
                r"(?i)beam.*",        # 보
                r"(?i)slab.*",        # 슬래브
            ]
        if self.low_priority_patterns is None:
            self.low_priority_patterns = [
                r"(?i)defpoints",     # AutoCAD 기본
                r"(?i).*_style.*",    # 스타일
                r"(?i).*hatch.*",     # 해치
            ]


@dataclass
class ComparisonConfig:
    """비교 설정 통합"""

    sensitivity: SensitivityConfig = None
    layer_priority: LayerPriorityConfig = None

    # 블록 옵션
    expand_blocks: bool = False
    block_recursion_depth: int = 1

    # OCR 옵션
    ocr_confidence_threshold: float = 0.7  # 70% 미만은 "검토 필요"

    def __post_init__(self):
        if self.sensitivity is None:
            self.sensitivity = SensitivityConfig()
        if self.layer_priority is None:
            self.layer_priority = LayerPriorityConfig()

    @classmethod
    def from_yaml(cls, path: str) -> "ComparisonConfig":
        """YAML 파일에서 설정 로드"""
        import yaml
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return cls(**data)

    @classmethod
    def default(cls) -> "ComparisonConfig":
        """기본 설정 반환"""
        return cls()
```

**Step 2: 기본 설정 파일**

```yaml
# config/comparison_config.yaml

sensitivity:
  coordinate_precision: 1      # 0.1mm 단위
  dimension_abs_threshold: 1.0  # 1mm
  dimension_rel_threshold: 0.001  # 0.1%
  position_threshold: 1.0       # 1mm
  rotation_threshold: 0.1       # 0.1°
  scale_threshold: 0.01         # 1%
  near_match_radius: 10.0       # 10mm

layer_priority:
  high_priority_patterns:
    - "(?i)struct.*"
    - "(?i)grid.*"
    - "(?i)dim.*"
    - "(?i)column.*"
    - "(?i)beam.*"
  low_priority_patterns:
    - "(?i)defpoints"
    - "(?i).*style.*"

expand_blocks: false
block_recursion_depth: 1
ocr_confidence_threshold: 0.7
```

**Step 3: 비교 엔진에 Config 적용**

```python
# dxf_comparator.py
class DxfComparator:
    def __init__(self, config: ComparisonConfig = None):
        self.config = config or ComparisonConfig.default()
        self._extractor = DxfEntityExtractor(
            precision=self.config.sensitivity.coordinate_precision
        )
```

#### 2.2.3 영향 파일

| 파일 | 변경 내용 |
|------|----------|
| `comparison_config.py` (신규) | Config 데이터 클래스 |
| `config/comparison_config.yaml` (신규) | 기본 설정 파일 |
| `dxf_comparator.py` | Config 적용 |
| `dxf_entity_extractor.py` | precision 외부 주입 |
| `dwg_differ.py` | Config 전달 |

#### 2.2.4 수용 기준

- [x] AC1: Config 클래스로 모든 임계값 중앙 관리 ✅ (ComparisonConfig, SensitivityConfig, LayerPriorityConfig)
- [x] AC2: YAML 파일에서 설정 로드 가능 ✅ (from_yaml(), to_yaml() 구현)
- [x] AC3: 0.5mm 위치 변경은 무시, 1.5mm는 검출 ✅ (is_position_significant() 메서드)
- [x] AC4: 0.05° 회전은 무시, 0.2°는 검출 ✅ (is_rotation_significant() 메서드)
- [x] AC5: 기본값으로 기존 동작 유지 ✅ (get_default() 팩토리 메서드)

---

### P3-3: expand_blocks 옵션 Extractor 전달

**우선순위**: High
**예상 소요**: 1-2일
**의존성**: P3-2

#### 2.3.1 문제 정의

현재 흐름:
```python
# compare_worker.py (UI)
self.expand_blocks = True  # 사용자 설정

# → dwg_differ.py
def compare(...):
    # expand_blocks 파라미터 없음!

# → dxf_entity_extractor.py
def extract(..., expand_blocks=False, ...):
    # 기본값 False로 호출됨
```

블록 내부 변경이 감지되지 않음.

#### 2.3.2 구현 방안

```python
# dwg_differ.py
class DwgDiffer:
    def __init__(self, config: ComparisonConfig = None):
        self.config = config or ComparisonConfig.default()

    def compare(self, old_path, new_path, ...):
        # ...
        old_entities = self._extractor.extract(
            old_doc,
            expand_blocks=self.config.expand_blocks,
            block_recursion_depth=self.config.block_recursion_depth,
        )

# compare_worker.py
class CompareWorker:
    def run(self):
        config = ComparisonConfig(
            expand_blocks=self.expand_blocks,
            block_recursion_depth=self.recursion_depth,
        )
        differ = DwgDiffer(config=config)
```

#### 2.3.3 영향 파일

| 파일 | 변경 내용 |
|------|----------|
| `dwg_differ.py` | Config에서 expand_blocks 읽기 |
| `compare_worker.py` | Config 생성 및 전달 |
| `dxf_entity_extractor.py` | 기존 파라미터 유지 (변경 없음) |

#### 2.3.4 수용 기준

- [x] AC1: UI에서 expand_blocks=True 설정 시 블록 내부 비교 동작 ✅ (compare_worker.py → DwgDiffer 연결)
- [x] AC2: 블록 내부 LINE 추가 시 `parent_block` 필드에 블록명 포함 ✅ (DxfEntityExtractor 구현)
- [x] AC3: recursion_depth=2 설정 시 중첩 블록까지 비교 ✅ (ComparisonConfig.block_recursion_depth)

---

### P3-4: 레이어 이동 감지 및 우선순위

**우선순위**: High
**예상 소요**: 2-3일
**의존성**: P3-1, P3-2

#### 2.4.1 문제 정의

현재:
- 레이어가 해시에 포함되지 않음 (형상만 비교)
- 레이어 A→B 이동 시 "삭제+추가"로 처리
- 구조/그리드 레이어와 스타일 레이어 구분 없음

#### 2.4.2 구현 방안

**Step 1: 레이어 이동 감지**

```python
# dxf_comparator.py
def _detect_layer_moves(
    self,
    deleted: List[NormalizedEntity],
    added: List[NormalizedEntity]
) -> List[DxfChange]:
    """레이어 이동 감지 (동일 형상 + 다른 레이어)"""

    layer_moves = []
    matched_added = set()
    matched_deleted = set()

    # 형상 해시 (레이어 제외) 생성
    def geometry_hash(entity):
        # 레이어를 제외한 기하학적 해시
        return hash((entity.entity_type, tuple(sorted(entity.data.items()))))

    deleted_by_geom = {}
    for e in deleted:
        gh = geometry_hash(e)
        deleted_by_geom.setdefault(gh, []).append(e)

    for new_entity in added:
        gh = geometry_hash(new_entity)
        if gh in deleted_by_geom:
            old_entity = deleted_by_geom[gh][0]
            if old_entity.layer != new_entity.layer:
                layer_moves.append(DxfChange(
                    change_type="modified",
                    entity_type=new_entity.entity_type,
                    layer=new_entity.layer,
                    location=new_entity.location,
                    data=new_entity.data,
                    old_data={"layer": old_entity.layer},
                    change_detail=f"레이어 이동: {old_entity.layer} → {new_entity.layer}",
                    change_category="layer_move",
                ))
                matched_added.add(id(new_entity))
                matched_deleted.add(id(old_entity))

    return layer_moves, matched_added, matched_deleted
```

**Step 2: 레이어 우선순위 분류**

```python
# dxf_comparator.py
def _classify_change_priority(
    self,
    change: DxfChange
) -> Literal["critical", "high", "medium", "low"]:
    """변경 우선순위 분류"""

    layer_config = self.config.layer_priority

    # 고우선순위 레이어 확인
    for pattern in layer_config.high_priority_patterns:
        if re.match(pattern, change.layer):
            return "critical" if change.entity_type == "DIMENSION" else "high"

    # 저우선순위 레이어 확인
    for pattern in layer_config.low_priority_patterns:
        if re.match(pattern, change.layer):
            return "low"

    # 엔티티 타입별 기본 우선순위
    priority_map = {
        "DIMENSION": "high",
        "LINE": "medium",
        "CIRCLE": "medium",
        "ARC": "medium",
        "INSERT": "high",
        "TEXT": "medium",
        "MTEXT": "medium",
    }

    return priority_map.get(change.entity_type, "medium")
```

**Step 3: 레이어별 통계**

```python
@dataclass
class LayerStatistics:
    """레이어별 변경 통계"""
    layer: str
    priority: str
    added_count: int = 0
    deleted_count: int = 0
    modified_count: int = 0

    @property
    def total_changes(self) -> int:
        return self.added_count + self.deleted_count + self.modified_count

class ComparisonResult:
    # 기존 필드
    added: List[DxfChange]
    deleted: List[DxfChange]
    modified: List[DxfChange]

    # 신규 필드
    layer_statistics: Dict[str, LayerStatistics]
    priority_summary: Dict[str, int]  # {"critical": 5, "high": 10, ...}
```

#### 2.4.3 영향 파일

| 파일 | 변경 내용 |
|------|----------|
| `dxf_comparator.py` | 레이어 이동 감지, 우선순위 분류 |
| `comparison_config.py` | LayerPriorityConfig 활용 |
| `dwg_differ.py` | 통계 결과 전달 |

#### 2.4.4 수용 기준

- [x] AC1: 레이어 A→B 이동 시 "레이어 이동: A → B" 변경으로 검출 ✅ (_detect_layer_moves() 구현, change_category="layer_move")
- [x] AC2: GRID 레이어 변경은 "critical" 우선순위 ✅ (_classify_change_priority() + LayerPriorityConfig 패턴 매칭)
- [x] AC3: DEFPOINTS 레이어 변경은 "low" 우선순위 ✅ (low_priority_patterns 적용)
- [x] AC4: 레이어별 변경 통계 제공 ✅ (LayerStatistics 데이터클래스, _compute_layer_statistics(), priority_summary)

---

### P3-5: OCR Confidence 기반 "검토 필요" 라벨링

**우선순위**: Medium
**예상 소요**: 1-2일
**의존성**: P3-2

#### 2.5.1 문제 정의

현재 `ocr_extractor.py`:
```python
@dataclass
class OCRResult:
    text: str
    confidence: float  # 0.0 ~ 1.0
    bbox: Tuple[int, int, int, int]
    # review_needed 필드 없음
```

confidence가 낮아도 별도 표시 없음.

#### 2.5.2 구현 방안

```python
# ocr_extractor.py
@dataclass
class OCRResult:
    text: str
    confidence: float
    bbox: Tuple[int, int, int, int]

    # 신규 필드
    review_needed: bool = False
    review_reason: Optional[str] = None

    def check_confidence(self, threshold: float = 0.7):
        """신뢰도 확인 및 검토 필요 표시"""
        if self.confidence < threshold:
            self.review_needed = True
            self.review_reason = f"OCR 신뢰도 {self.confidence:.0%} < {threshold:.0%}"

# drawing_differ.py
def compare_with_ocr(self, ...):
    ocr_results = self._ocr_extractor.extract(image)

    for result in ocr_results:
        result.check_confidence(self.config.ocr_confidence_threshold)
        if result.review_needed:
            logger.warning(f"검토 필요: {result.text} - {result.review_reason}")
```

#### 2.5.3 영향 파일

| 파일 | 변경 내용 |
|------|----------|
| `ocr_extractor.py` | review_needed 필드 추가 |
| `drawing_differ.py` | confidence 체크 호출 |
| `comparison_config.py` | ocr_confidence_threshold 활용 |

#### 2.5.4 수용 기준

- [x] AC1: OCR 신뢰도 60%인 결과에 review_needed=True ✅
- [x] AC2: 리포트에 "검토 필요" 항목 별도 섹션 ✅ (to_dict()에 review_needed, review_reason 포함)
- [x] AC3: threshold를 Config에서 조정 가능 ✅ (ComparisonConfig.ocr_confidence_threshold)

---

### P3-6: DXF 시각화 UI 연결

**우선순위**: Medium
**예상 소요**: 3-4일
**의존성**: P3-1, P3-4

#### 2.6.1 문제 정의

현재 존재하는 미연결 컴포넌트:
- `dxf_renderer.py`: DXF→이미지 렌더링
- `dxf_overlay_renderer.py`: 변경점 오버레이
- `drawing_compare_tab.py`: UI 탭 (연결 안됨)

#### 2.6.2 구현 방안

**Step 1: 렌더링 서비스 통합**

```python
# src/services/comparison/visualization_service.py (신규)

class VisualizationService:
    """변경점 시각화 서비스"""

    def __init__(self, config: ComparisonConfig = None):
        self.config = config or ComparisonConfig.default()
        self._renderer = DxfRenderer()
        self._overlay = DxfOverlayRenderer()

    def render_comparison(
        self,
        old_dxf_path: str,
        new_dxf_path: str,
        changes: ComparisonResult,
        output_path: str,
    ) -> str:
        """비교 결과 시각화 이미지 생성"""

        # 1. 기본 렌더링
        old_img = self._renderer.render(old_dxf_path)
        new_img = self._renderer.render(new_dxf_path)

        # 2. 변경점 오버레이
        overlay_img = self._overlay.create_overlay(
            base_image=new_img,
            changes=changes,
            colors={
                "added": (0, 255, 0),      # 녹색
                "deleted": (255, 0, 0),    # 빨간색
                "modified": (255, 165, 0), # 주황색
            }
        )

        # 3. 저장
        overlay_img.save(output_path)
        return output_path

    def render_html_report(
        self,
        changes: ComparisonResult,
        output_path: str,
    ) -> str:
        """HTML 형식 비교 리포트 생성"""
        # 인터랙티브 HTML 생성
        pass
```

**Step 2: UI 탭 연결**

```python
# drawing_compare_tab.py
class DrawingCompareTab:
    def on_compare_complete(self, result: ComparisonResult):
        # 시각화 이미지 생성
        viz_service = VisualizationService()
        overlay_path = viz_service.render_comparison(
            self.old_path,
            self.new_path,
            result,
            "temp/comparison_overlay.png"
        )

        # UI에 표시
        self.image_viewer.load(overlay_path)
```

#### 2.6.3 영향 파일

| 파일 | 변경 내용 |
|------|----------|
| `visualization_service.py` (신규) | 시각화 통합 서비스 |
| `dxf_renderer.py` | 기존 코드 활용 |
| `dxf_overlay_renderer.py` | 기존 코드 활용 |
| `drawing_compare_tab.py` | 시각화 서비스 연결 |

#### 2.6.4 수용 기준 ✅ 전체 완료

- [x] AC1: 비교 완료 후 오버레이 이미지 자동 생성 ✅ (`create_overlay()`, `VisualizationResult.overlay_image`)
- [x] AC2: 추가=녹색, 삭제=빨간색, 수정=주황색 표시 ✅ (`COLOR_ADDED`, `COLOR_DELETED`, `COLOR_MODIFIED`)
- [x] AC3: UI에서 변경점 클릭 시 해당 위치로 이동 ✅ (`ClickableRegion`, `find_change_at_position()`)
- [x] AC4: HTML 리포트 내보내기 기능 ✅ (`export_html_report()`, base64 이미지 포함)

---

## 3. 작업 일정

```
Week 1: P3-1 (MODIFIED 구현) + P3-2 (Config 도입)
├── Day 1-2: DxfChange 확장, near-match 연결
├── Day 3-4: 변경 상세 생성, 테스트
└── Day 5: Config 데이터 클래스, YAML 로드

Week 2: P3-3 (블록) + P3-4 (레이어)
├── Day 1: expand_blocks 전달 구현
├── Day 2-3: 레이어 이동 감지
└── Day 4-5: 레이어 우선순위, 통계

Week 3: P3-5 (OCR) + P3-6 (시각화)
├── Day 1: OCR confidence 라벨링
├── Day 2-4: 시각화 서비스 통합
└── Day 5: UI 연결, 최종 테스트
```

---

## 4. 의존성 그래프

```
P3-1 (MODIFIED) ─────┬──────────────────────────────────┐
                     │                                  │
                     ▼                                  │
P3-2 (Config) ───────┼───────────────┐                  │
                     │               │                  │
        ┌────────────┼───────────────┼──────────────────┤
        │            │               │                  │
        ▼            ▼               ▼                  ▼
P3-3 (블록)    P3-4 (레이어)    P3-5 (OCR)       P3-6 (시각화)
```

---

## 5. 위험 요소

| 위험 | 영향 | 완화 방안 |
|------|------|----------|
| Near-match 성능 | O(n²) 가능 | R-tree 활용, 반경 제한 |
| 레거시 호환성 | 기존 리포트 형식 변경 | 이전 형식 옵션 유지 |
| OCR 정확도 | 잘못된 "검토 필요" 라벨 | threshold 조정 가능 |
| UI 렌더링 성능 | 대용량 도면 느림 | 프로그레시브 렌더링 |

---

## 6. 테스트 전략

### 6.1 단위 테스트

| 대상 | 계획 | 실제 | 상태 |
|------|------|------|------|
| ComparisonConfig | 15개 | 40개 | ✅ 초과 달성 |
| DwgDiffer Cleanup (P3-3) | - | 32개 | ✅ 완료 |
| 레이어 통계 (P3-4) | 10개 | 28개 | ✅ 초과 달성 |
| MODIFIED 감지 | 20개 | 37개 | ✅ 초과 달성 |
| OCR confidence | 8개 | 21개 | ✅ 초과 달성 |
| VisualizationService | 12개 | 37개 | ✅ 초과 달성 |

### 6.2 통합 테스트

- 실제 DWG 파일 쌍으로 전체 파이프라인 테스트
- 치수 변경, 형상 이동, 레이어 변경 시나리오
- 대용량 도면 (10K+ 엔티티) 성능 테스트

### 6.3 회귀 테스트

- Phase 1-2 기존 테스트: 126개 → 303개 (Phase 3 전체 추가분 포함)
- 성능 벤치마크 (10K 엔티티 삽입 < 1초)
- **최신 결과** (2025-12-23 20:09): 303 통과, 2 스킵 (R-tree 미설치)

---

## 7. 산출물

| 산출물 | 설명 | 상태 |
|--------|------|------|
| `comparison_config.py` | Config 데이터 클래스 (SensitivityConfig, LayerPriorityConfig, ComparisonConfig) | ✅ 완료 |
| `visualization_service.py` | 시각화 통합 서비스 (894 lines) | ✅ 완료 |
| `ocr_extractor.py` 확장 | OCR Confidence 라벨링 (check_confidence, review_needed) | ✅ 완료 |
| `dxf_comparator.py` 확장 | MODIFIED 감지, 레이어 통계, 레이어 이동 | ✅ 완료 |
| `test_visualization_service.py` | P3-6 테스트 37개 | ✅ 완료 |
| `test_ocr_confidence.py` | P3-5 테스트 21개 | ✅ 완료 |
| 총 테스트 195개+ | Phase 3 신규 기능 테스트 | ✅ 완료 |
| 문서 업데이트 | TODO_DRAWING_COMPARISON_MODULE_V3.md | ✅ 완료 |

---

## 8. 성공 지표

| 지표 | 목표 |
|------|------|
| MODIFIED 검출률 | 실제 수정의 90% 이상 감지 |
| False Positive | 5% 미만 |
| 처리 속도 | 10K 엔티티 비교 < 2초 |
| 테스트 통과 | 190개+ (기존 126 + 신규 65) |
