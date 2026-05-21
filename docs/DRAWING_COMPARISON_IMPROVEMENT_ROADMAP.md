# Drawing Comparison Module 개선 로드맵

**작성일**: 2025-12-23
**Phase 3 완료 기준**: 303 tests passed, 2 skipped

---

## 개요

도면 비교 모듈의 핵심 목표: **"빠르게 핵심 변경을 찾고, 결과 신뢰성을 높이는 것"**

이 문서는 3개의 서브에이전트 분석 결과를 종합하여 구체적인 구현 계획을 제시합니다.

---

## 📊 현재 구현 상태 요약

| 아이디어 | 구현율 | 난이도 | 예상 시간 |
|---------|--------|--------|----------|
| **High Impact** |
| #1 Auto Preset | 5% | 낮음 | 0.5-1일 |
| #2 Top List + Focus | 20% | 중간 | 1.5-2일 |
| #3 Confidence Display | 40% | 중간 | 1-1.5일 |
| #4 Layer Profile | 60% | 낮음 | 0.5-1일 |
| #5 Detail Card | 15% | 중간 | 1-1.5일 |
| **Supporting** |
| #6 Threshold Preset | 90% | 낮음 | 2시간 |
| #7 Visual Overlay | 80% | 낮음 | 3시간 |
| #8 Batch Dashboard | 50% | 중간 | 4-6시간 |
| #9 Review Workflow | 10% | 높음 | 8-10시간 |

---

## ⚡ Quick Win 항목 (즉시 적용 가능)

### Tier 1: 1시간 이내 (<1h)

#### QW-1: Sensitivity Preset UI (30분)
**기존 코드 활용율: 90%**

```python
# comparison_config.py에 이미 존재
ComparisonConfig.get_strict()   # 엄격한 설정
ComparisonConfig.get_default()  # 기본 설정
ComparisonConfig.get_relaxed()  # 완화된 설정
```

**필요 작업**:
- GUI에 3-버튼 프리셋 선택 UI 추가
- `QComboBox` 또는 `QButtonGroup`으로 구현

#### QW-2: Color Toggle (45분)
**기존 코드 활용율: 80%**

```python
# visualization_service.py에 이미 존재
COLORS_RGB = {
    'ADDED': (0, 200, 0),      # 녹색
    'DELETED': (200, 0, 0),    # 빨간색
    'MODIFIED': (255, 165, 0), # 주황색
}
```

**필요 작업**:
- 색상 설정 저장/로드 기능
- GUI 색상 선택기 (QColorDialog)

### Tier 2: 2시간 이내 (1-2h)

#### QW-3: HTML Batch Export (1-1.5시간)
**기존 코드 활용율: 70%**

```python
# visualization_service.py
export_html_report()  # 단일 파일 버전 존재
```

**필요 작업**:
- BatchCompareWorker에 HTML 내보내기 통합
- 폴더별 인덱스 HTML 생성

#### QW-4: Project Config Save/Load (1-2시간)
**기존 코드 활용율: 85%**

```python
# comparison_config.py
ComparisonConfig.from_yaml(path)  # YAML 로드
ComparisonConfig.to_yaml(path)    # YAML 저장
```

**필요 작업**:
- GUI에 저장/불러오기 버튼 추가
- 최근 설정 목록 관리

---

## 🎯 High Impact 구현 계획

### Phase A: 기반 시스템 (Week 1)

#### A-1: Priority Scoring System 핵심

**신규 파일**: `src/services/comparison/priority_score.py`

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

class PriorityLevel(Enum):
    """변경 우선순위 레벨"""
    CRITICAL = 5    # 구조 변경: 즉시 검토 필수
    HIGH = 4        # 치수/그리드 변경
    MEDIUM = 3      # 일반 변경
    LOW = 2         # 주석/텍스트 변경
    TRIVIAL = 1     # 무시 가능

class ReviewReason(Enum):
    """검토 필요 사유"""
    OCR_LOW_CONFIDENCE = "ocr_low_confidence"
    NEAR_MATCH_DETECTED = "near_match_detected"
    SSIM_BOUNDARY = "ssim_boundary"
    STRUCTURAL_CHANGE = "structural_change"
    DIMENSION_CHANGE = "dimension_change"
    GRID_CHANGE = "grid_change"

@dataclass
class ConfidenceFactors:
    """신뢰도 평가 요소"""
    ocr_confidence: float = 1.0      # OCR 신뢰도 (0.0~1.0)
    match_distance: float = 0.0       # near-match 거리 (mm)
    ssim_score: float = 1.0           # SSIM 점수 (0.0~1.0)
    layer_reliability: float = 1.0    # 레이어 신뢰도

@dataclass
class PriorityScore:
    """통합 우선순위 점수"""
    priority_level: PriorityLevel
    priority_score: float = 50.0      # 0.0 ~ 100.0
    confidence_score: float = 1.0     # 0.0 ~ 1.0
    review_needed: bool = False
    review_reasons: List[ReviewReason] = field(default_factory=list)

    @property
    def display_label(self) -> str:
        labels = {
            PriorityLevel.CRITICAL: "🔴 CRITICAL",
            PriorityLevel.HIGH: "🟠 HIGH",
            PriorityLevel.MEDIUM: "🟡 MEDIUM",
            PriorityLevel.LOW: "🟢 LOW",
            PriorityLevel.TRIVIAL: "⚪ TRIVIAL",
        }
        return labels.get(self.priority_level, "⚪ UNKNOWN")
```

**신규 파일**: `src/services/comparison/priority_calculator.py`

```python
from dataclasses import dataclass
from typing import Dict, List
from .priority_score import PriorityLevel, PriorityScore, ReviewReason, ConfidenceFactors

@dataclass
class LayerProfile:
    """레이어 프로파일 설정"""
    name: str
    priority: PriorityLevel
    keywords: List[str]
    weight: float = 1.0

# 기본 레이어 프로파일
DEFAULT_LAYER_PROFILES = [
    LayerProfile("structural", PriorityLevel.CRITICAL,
                 ["BEAM", "COLUMN", "WALL", "SLAB", "FOUNDATION", "STEEL"], 2.0),
    LayerProfile("dimension", PriorityLevel.HIGH,
                 ["DIM", "DIMENSION", "MEASURE", "SIZE", "ANNO"], 1.5),
    LayerProfile("grid", PriorityLevel.HIGH,
                 ["GRID", "AXIS", "CENTERLINE", "CL"], 1.5),
    LayerProfile("annotation", PriorityLevel.MEDIUM,
                 ["TEXT", "NOTE", "LABEL", "TAG", "MARK"], 1.0),
    LayerProfile("other", PriorityLevel.LOW,
                 ["DEFPOINTS", "0", "TEMP", "HIDDEN"], 0.5),
]

class PriorityCalculator:
    """우선순위 계산기"""

    def __init__(self, profiles: List[LayerProfile] = None):
        self.profiles = profiles or DEFAULT_LAYER_PROFILES

    def calculate(
        self,
        change_type: str,
        layer_name: str,
        confidence_factors: ConfidenceFactors,
    ) -> PriorityScore:
        # 1. 레이어 기반 기본 우선순위
        base_priority = self._get_layer_priority(layer_name)

        # 2. 변경 유형에 따른 조정
        type_weight = self._get_change_type_weight(change_type)

        # 3. 신뢰도 기반 검토 필요 여부
        review_needed, reasons = self._evaluate_confidence(confidence_factors)

        # 4. 최종 점수 계산
        weight = self._get_layer_weight(layer_name)
        priority_score = base_priority.value * 20 * type_weight * weight

        # 신뢰도가 낮으면 점수 조정
        confidence_score = self._calculate_confidence(confidence_factors)
        if confidence_score < 0.7:
            review_needed = True

        return PriorityScore(
            priority_level=base_priority,
            priority_score=min(100.0, priority_score),
            confidence_score=confidence_score,
            review_needed=review_needed,
            review_reasons=reasons,
        )

    def _get_layer_priority(self, layer_name: str) -> PriorityLevel:
        upper_name = layer_name.upper()
        for profile in self.profiles:
            if any(kw in upper_name for kw in profile.keywords):
                return profile.priority
        return PriorityLevel.MEDIUM

    def _get_layer_weight(self, layer_name: str) -> float:
        upper_name = layer_name.upper()
        for profile in self.profiles:
            if any(kw in upper_name for kw in profile.keywords):
                return profile.weight
        return 1.0

    def _get_change_type_weight(self, change_type: str) -> float:
        weights = {
            "ADDED": 1.0,
            "DELETED": 1.2,  # 삭제는 더 중요
            "MODIFIED": 0.8,
        }
        return weights.get(change_type, 1.0)

    def _evaluate_confidence(
        self, factors: ConfidenceFactors
    ) -> tuple[bool, List[ReviewReason]]:
        reasons = []
        review_needed = False

        if factors.ocr_confidence < 0.7:
            reasons.append(ReviewReason.OCR_LOW_CONFIDENCE)
            review_needed = True

        if factors.match_distance > 5.0:  # 5mm 이상 near-match
            reasons.append(ReviewReason.NEAR_MATCH_DETECTED)
            review_needed = True

        if 0.85 < factors.ssim_score < 0.95:  # 경계 영역
            reasons.append(ReviewReason.SSIM_BOUNDARY)
            review_needed = True

        return review_needed, reasons

    def _calculate_confidence(self, factors: ConfidenceFactors) -> float:
        return (
            factors.ocr_confidence * 0.3 +
            max(0, 1 - factors.match_distance / 20) * 0.3 +
            factors.ssim_score * 0.2 +
            factors.layer_reliability * 0.2
        )
```

#### A-2: Layer Profile One-Click (#4)
**난이도**: 낮음 | **시간**: 0.5일 | **기존 구현**: 60%

```python
# LayerPriorityConfig 활용
config.layer_priority.get_priority(layer_name)  # 이미 존재
```

**추가 작업**:
- GUI에 프로파일 선택 드롭다운
- 프로파일 저장/불러오기

#### A-3: Auto Preset Recommendation (#1)
**난이도**: 낮음 | **시간**: 0.5일 | **기존 구현**: 5%

**구현 로직**:
```python
def recommend_preset(file_stats: Dict) -> str:
    """파일 특성에 따른 프리셋 추천"""
    entity_count = file_stats.get("total_entities", 0)
    has_ocr = file_stats.get("has_text_blocks", False)

    if entity_count > 10000:
        return "relaxed"  # 대용량 파일 → 완화된 설정
    elif has_ocr:
        return "strict"   # OCR 텍스트 → 엄격한 설정
    else:
        return "default"  # 기본 설정
```

### Phase B: UI 통합 (Week 1-2)

#### B-1: Confidence Display (#3)
**난이도**: 중간 | **시간**: 1일 | **기존 구현**: 40%

**기존 코드**:
```python
# ocr_extractor.py - OCRTextBlock
block.check_confidence(threshold=0.7)
block.review_needed  # bool
block.review_reason  # str
```

**추가 UI**:
- 신뢰도 게이지 표시 (QProgressBar)
- 검토 필요 항목 하이라이트
- 필터 옵션 (신뢰도 낮은 항목만 표시)

#### B-2: Top Changes List + Auto Focus (#2)
**난이도**: 중간 | **시간**: 1.5일 | **기존 구현**: 20%

**기존 코드**:
```python
# dxf_comparator.py
_classify_change_priority()  # 우선순위 분류
LayerStatistics  # 레이어별 통계
```

**추가 작업**:
- 우선순위 정렬된 리스트 뷰
- 클릭 시 해당 위치로 스크롤
- 키보드 단축키 (N: 다음, P: 이전)

#### B-3: Change Detail Card (#5)
**난이도**: 중간 | **시간**: 1일 | **기존 구현**: 15%

**기존 코드**:
```python
# DxfChange 데이터
change.change_type      # ADDED/DELETED/MODIFIED
change.old_data         # 이전 값
change.change_detail    # 변경 상세
change.change_category  # 변경 분류
```

**추가 UI**:
- 팝업 카드 또는 사이드 패널
- Before/After 비교 뷰
- 관련 변경사항 링크

---

## 📅 구현 일정

### Week 1: 기반 + Quick Wins

| 일차 | 작업 | 예상 시간 |
|-----|------|----------|
| Day 1 | Quick Wins (QW-1~4) | 4시간 |
| Day 2 | Priority Score 시스템 | 4시간 |
| Day 3 | Layer Profile (#4) | 4시간 |
| Day 4 | Auto Preset (#1) | 4시간 |
| Day 5 | 테스트 및 통합 | 4시간 |

### Week 2: UI 통합

| 일차 | 작업 | 예상 시간 |
|-----|------|----------|
| Day 1-2 | Confidence Display (#3) | 8시간 |
| Day 3-4 | Top List + Focus (#2) | 10시간 |
| Day 5 | Detail Card (#5) | 6시간 |

### Week 3: 고급 기능

| 일차 | 작업 | 예상 시간 |
|-----|------|----------|
| Day 1-2 | Review Workflow (#9) | 10시간 |
| Day 3 | Batch Dashboard (#8) | 6시간 |
| Day 4-5 | 최종 테스트 및 문서화 | 8시간 |

---

## 🧪 테스트 계획

### 단위 테스트 (신규)

```
tests/unit/services/comparison/
├── test_priority_score.py       # PriorityScore 테스트
├── test_priority_calculator.py  # PriorityCalculator 테스트
├── test_layer_profile.py        # LayerProfile 테스트
└── test_quick_wins.py           # Quick Win 기능 테스트
```

### 통합 테스트

```
tests/integration/comparison/
├── test_full_workflow.py        # 전체 워크플로우
├── test_batch_comparison.py     # 배치 비교
└── test_ui_integration.py       # UI 통합
```

---

## 📁 파일 구조 변경

```
src/services/comparison/
├── comparison_config.py      # 기존 (수정)
├── dxf_comparator.py         # 기존 (수정)
├── visualization_service.py  # 기존 (수정)
├── priority_score.py         # 신규 ⭐
├── priority_calculator.py    # 신규 ⭐
└── review_workflow.py        # 신규 (Week 3)

src/gui/unified_load_module/
├── comparison_panel.py       # 기존 (대규모 수정)
├── change_list_widget.py     # 신규 ⭐
├── detail_card_widget.py     # 신규 ⭐
└── confidence_gauge.py       # 신규 ⭐
```

---

## ✅ 체크리스트

### Quick Wins (Day 1)
- [ ] QW-1: Sensitivity Preset UI 추가
- [ ] QW-2: Color Toggle 구현
- [ ] QW-3: HTML Batch Export 통합
- [ ] QW-4: Project Config Save/Load

### Week 1
- [ ] PriorityScore 데이터클래스 구현
- [ ] PriorityCalculator 클래스 구현
- [ ] LayerProfile 시스템 구현
- [ ] Auto Preset 추천 로직
- [ ] 단위 테스트 40개+ 작성

### Week 2
- [ ] Confidence 게이지 위젯
- [ ] Top Changes 리스트 뷰
- [ ] Auto Focus 기능
- [ ] Detail Card 팝업
- [ ] 키보드 단축키

### Week 3
- [ ] Review Workflow 상태 머신
- [ ] Batch Dashboard UI
- [ ] 전체 통합 테스트
- [ ] 사용자 문서 작성

---

## 🔗 관련 문서

- [TODO_DRAWING_COMPARISON_MODULE_V3.md](./TODO_DRAWING_COMPARISON_MODULE_V3.md) - Phase 3 완료 현황
- [SPATIAL_INDEX_IMPLEMENTATION.md](./SPATIAL_INDEX_IMPLEMENTATION.md) - 공간 인덱스
- [dwg_differ_cleanup_implementation.md](./dwg_differ_cleanup_implementation.md) - DWG Differ 구현

---

**다음 단계**: Quick Wins (QW-1~4) 즉시 구현 시작
