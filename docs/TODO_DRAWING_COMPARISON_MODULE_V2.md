# TODO: 도면 비교 모듈 통합 개선 계획 v2
# Detailed Work Breakdown Structure

**문서 버전**: 1.0
**작성일**: 2025-12-23
**기반 PRD**: [PRD_DRAWING_COMPARISON_MODULE_V2.md](PRD_DRAWING_COMPARISON_MODULE_V2.md)
**총 예상 기간**: 4주
**총 작업 항목**: 67개

---

## 진행 상태 범례

- [ ] 대기 (Pending)
- [~] 진행 중 (In Progress)
- [x] 완료 (Completed)
- [!] 블록됨 (Blocked)
- [-] 취소/스킵 (Cancelled/Skipped)

---

## Phase 1: Critical Performance Optimizations (1주)

**예상 기간**: 5일
**우선순위**: P0 (Critical)
**담당**: TBD

### P0-1: R-tree 기반 공간 인덱싱 (Day 1-2)

#### 1.1 환경 설정
- [ ] P0-1-T01: rtree 패키지 의존성 추가 (`requirements.txt`)
- [ ] P0-1-T02: libspatialindex 설치 가이드 문서화
- [ ] P0-1-T03: rtree import 가용성 체크 유틸리티 작성

#### 1.2 SpatialIndex 클래스 구현
- [ ] P0-1-T04: `src/services/comparison/spatial_index.py` 파일 생성
- [ ] P0-1-T05: SpatialIndex 데이터클래스 정의
  - [ ] `precision`, `_idx`, `_entities`, `_counter` 필드
  - [ ] `__post_init__()` R-tree 초기화
- [ ] P0-1-T06: `insert()` 메서드 구현 (단일 엔티티 삽입)
- [ ] P0-1-T07: `bulk_insert()` 메서드 구현 (벌크 삽입)
- [ ] P0-1-T08: `_compute_bbox()` 메서드 구현
  - [ ] TEXT/MTEXT 바운딩 박스
  - [ ] LINE 바운딩 박스
  - [ ] CIRCLE 바운딩 박스
  - [ ] ARC 바운딩 박스
  - [ ] POLYLINE/LWPOLYLINE 바운딩 박스
  - [ ] INSERT 바운딩 박스
  - [ ] DIMENSION 바운딩 박스
- [ ] P0-1-T09: `find_intersecting()` 메서드 구현
- [ ] P0-1-T10: `find_near_point()` 메서드 구현
- [ ] P0-1-T11: `find_nearest()` 메서드 구현
- [ ] P0-1-T12: `create_spatial_index()` 팩토리 함수 구현

#### 1.3 Fallback 구현
- [ ] P0-1-T13: rtree 미설치 시 O(n²) fallback 로직 구현
- [ ] P0-1-T14: RTREE_AVAILABLE 플래그 기반 분기 처리

#### 1.4 DxfComparator 통합
- [ ] P0-1-T15: `dxf_comparator.py` import 추가
- [ ] P0-1-T16: `compare()` 메서드에 R-tree 텍스트 매칭 통합
- [ ] P0-1-T17: 기존 해시 기반 비교와 R-tree 비교 조합

#### 1.5 테스트
- [ ] P0-1-T18: `tests/unit/services/comparison/test_spatial_index.py` 생성
- [ ] P0-1-T19: SpatialIndex 단위 테스트 8개 작성
  - [ ] test_insert_single_entity
  - [ ] test_bulk_insert
  - [ ] test_find_intersecting
  - [ ] test_find_near_point
  - [ ] test_find_nearest
  - [ ] test_empty_index
  - [ ] test_fallback_without_rtree
  - [ ] test_bbox_computation_all_types
- [ ] P0-1-T20: 10K 엔티티 벤치마크 테스트 작성

**P0-1 수용 기준 검증**:
- [ ] P0-1-AC1: 10K 텍스트 엔티티 비교 < 1초
- [ ] P0-1-AC2: 메모리 증가 < 50%
- [ ] P0-1-AC3: rtree 미설치 시 graceful fallback
- [ ] P0-1-AC4: 기존 API 100% 호환

---

### P0-2: SSIM 다운샘플링 최적화 (Day 3)

#### 2.1 SSIM 최적화 함수 구현
- [ ] P0-2-T01: `drawing_differ.py`에 `compute_ssim_optimized()` 함수 추가
- [ ] P0-2-T02: target_size 파라미터 구현 (기본값 1024)
- [ ] P0-2-T03: INTER_AREA 다운샘플링 적용
- [ ] P0-2-T04: Grayscale 변환 최적화

#### 2.2 기존 코드 수정
- [ ] P0-2-T05: 기존 SSIM 호출부 `compute_ssim_optimized()` 교체
- [ ] P0-2-T06: 설정 가능한 다운샘플 비율 파라미터 추가

#### 2.3 테스트
- [ ] P0-2-T07: SSIM 정확도 비교 테스트 (원본 vs 다운샘플)
- [ ] P0-2-T08: 4K 이미지 벤치마크 테스트

**P0-2 수용 기준 검증**:
- [ ] P0-2-AC1: 4K 이미지 SSIM < 0.5초
- [ ] P0-2-AC2: 정확도 손실 < 5%
- [ ] P0-2-AC3: 메모리 피크 50% 감소

---

### P0-3: 예외 처리 및 리소스 정리 (Day 4)

#### 3.1 컨텍스트 매니저 구현
- [ ] P0-3-T01: `DwgDiffer.__enter__()` 메서드 추가
- [ ] P0-3-T02: `DwgDiffer.__exit__()` 메서드 추가 (_cleanup_temp 호출)

#### 3.2 finally 블록 추가
- [ ] P0-3-T03: `compare()` 메서드 finally 블록 추가 (lines 95-230)
- [ ] P0-3-T04: `compare_and_mark()` 메서드 finally 블록 추가 (lines 232-330)
- [ ] P0-3-T05: `export_excel()` 메서드 finally 블록 추가 (lines 332-420)

#### 3.3 테스트
- [ ] P0-3-T06: 예외 발생 시 임시 파일 정리 테스트
- [ ] P0-3-T07: 컨텍스트 매니저 사용 테스트
- [ ] P0-3-T08: 기존 호출 방식 호환성 테스트

**P0-3 수용 기준 검증**:
- [ ] P0-3-AC1: 예외 발생 시 임시 파일 100% 정리
- [ ] P0-3-AC2: 컨텍스트 매니저 정상 동작
- [ ] P0-3-AC3: 기존 호출 방식 호환

---

### Phase 1 마무리 (Day 5)

- [ ] P1-FINAL-T01: Phase 1 전체 회귀 테스트 실행
- [ ] P1-FINAL-T02: 성능 벤치마크 결과 문서화
- [ ] P1-FINAL-T03: 코드 리뷰 및 피드백 반영
- [ ] P1-FINAL-T04: Phase 1 완료 보고서 작성

---

## Phase 2: High Priority Refactoring (2주)

**예상 기간**: 10일
**우선순위**: P1 (High)
**담당**: TBD

### P1-1: ProgressTracker 클래스 추출 (Day 1-2)

#### 1.1 ProgressTracker 모듈 생성
- [ ] P1-1-T01: `src/services/comparison/progress_tracker.py` 파일 생성
- [ ] P1-1-T02: `ProgressStage` 데이터클래스 구현
  - [ ] name, start_percent, end_percent, message_prefix 필드
  - [ ] `map_percent()` 메서드
- [ ] P1-1-T03: `ProgressTracker` 데이터클래스 구현
  - [ ] callback, is_cancelled_fn, total_percent 필드
  - [ ] _current_stage, _stages 내부 필드
- [ ] P1-1-T04: `set_stage()` 메서드 구현 (체이닝 지원)
- [ ] P1-1-T05: `report()` 메서드 구현 (취소 확인 + 콜백 실행)
- [ ] P1-1-T06: `create_sub_tracker()` 메서드 구현 (기존 API 호환용)
- [ ] P1-1-T07: `is_cancelled()` 메서드 구현
- [ ] P1-1-T08: `report_simple()` 메서드 구현
- [ ] P1-1-T09: `create_tracker()` 팩토리 함수 구현

#### 1.2 DwgDiffer 마이그레이션
- [ ] P1-1-T10: `dwg_differ.py` import 추가
- [ ] P1-1-T11: `compare()` 메서드 내 중복 콜백 제거 (lines 153-159, 175-181)
- [ ] P1-1-T12: `compare_and_mark()` 메서드 내 중복 콜백 제거 (lines 283-289, 303-309)
- [ ] P1-1-T13: ProgressTracker 사용으로 교체

#### 1.3 테스트
- [ ] P1-1-T14: `tests/unit/services/comparison/test_progress_tracker.py` 생성
- [ ] P1-1-T15: ProgressTracker 단위 테스트 10개 작성
  - [ ] test_set_stage_chaining
  - [ ] test_report_mapping
  - [ ] test_report_cancelled
  - [ ] test_create_sub_tracker
  - [ ] test_sub_tracker_callback
  - [ ] test_is_cancelled
  - [ ] test_report_simple
  - [ ] test_no_callback
  - [ ] test_zero_total_handling
  - [ ] test_multiple_stages
- [ ] P1-1-T16: 통합 테스트 - DwgDiffer와 ProgressTracker 연동

**P1-1 수용 기준 검증**:
- [ ] P1-1-AC1: 중복 코드 150 lines 제거 확인
- [ ] P1-1-AC2: 기존 progress_callback 인터페이스 호환
- [ ] P1-1-AC3: 취소 기능 정상 동작
- [ ] P1-1-AC4: 단위 테스트 커버리지 90%+

---

### P1-2: extract() 메서드 복잡도 감소 (Day 3-4)

#### 2.1 헬퍼 메서드 분리
- [ ] P1-2-T01: `_validate_config()` 메서드 추출
- [ ] P1-2-T02: `_filter_layers()` 메서드 추출
- [ ] P1-2-T03: `_extract_layer_entities()` 메서드 추출
- [ ] P1-2-T04: `_expand_block_references()` 메서드 추출 (기존 로직 분리)

#### 2.2 extract() 리팩토링
- [ ] P1-2-T05: `extract()` 메서드 단순화 (헬퍼 메서드 호출로 대체)
- [ ] P1-2-T06: 중첩 조건문 평탄화

#### 2.3 테스트
- [ ] P1-2-T07: radon 복잡도 측정 (목표: 10 이하)
- [ ] P1-2-T08: 기존 테스트 전체 통과 확인
- [ ] P1-2-T09: 성능 회귀 테스트

**P1-2 수용 기준 검증**:
- [ ] P1-2-AC1: extract() 복잡도 10 이하
- [ ] P1-2-AC2: 전체 테스트 통과
- [ ] P1-2-AC3: 성능 회귀 없음

---

### P1-3: Strategy 패턴 - EntityNormalizer (Day 5-7)

#### 3.1 패키지 구조 생성
- [ ] P1-3-T01: `src/services/comparison/entity_normalizers/` 디렉토리 생성
- [ ] P1-3-T02: `__init__.py` 생성

#### 3.2 Base 클래스 구현
- [ ] P1-3-T03: `base.py` 파일 생성
- [ ] P1-3-T04: `NormalizedEntity` 데이터클래스 이동/정리
- [ ] P1-3-T05: `EntityNormalizer` ABC 구현
  - [ ] `get_entity_type()` 추상 메서드
  - [ ] `normalize()` 추상 메서드
  - [ ] `_round_point()` 헬퍼 메서드
  - [ ] `_compute_hash()` 헬퍼 메서드

#### 3.3 Registry 구현
- [ ] P1-3-T06: `registry.py` 파일 생성
- [ ] P1-3-T07: `NormalizerRegistry` 싱글턴 클래스 구현
  - [ ] `register()` 메서드
  - [ ] `register_class()` 메서드
  - [ ] `get_normalizer()` 메서드
  - [ ] `normalize()` 메서드
  - [ ] `get_supported_types()` 메서드
  - [ ] `clear()` 메서드 (테스트용)
- [ ] P1-3-T08: `get_registry()` 함수 구현
- [ ] P1-3-T09: `register_normalizer()` 함수 구현

#### 3.4 Concrete Normalizer 구현
- [ ] P1-3-T10: `line_normalizer.py` - LineNormalizer 구현
- [ ] P1-3-T11: `circle_normalizer.py` - CircleNormalizer 구현
- [ ] P1-3-T12: `arc_normalizer.py` - ArcNormalizer 구현
- [ ] P1-3-T13: `polyline_normalizer.py` - PolylineNormalizer 구현 (LWPOLYLINE 포함)
- [ ] P1-3-T14: `text_normalizer.py` - TextNormalizer 구현
- [ ] P1-3-T15: `mtext_normalizer.py` - MTextNormalizer 구현
- [ ] P1-3-T16: `dimension_normalizer.py` - DimensionNormalizer 구현
- [ ] P1-3-T17: `insert_normalizer.py` - InsertNormalizer 구현

#### 3.5 자동 등록 설정
- [ ] P1-3-T18: 각 Normalizer 모듈 하단에 `register_normalizer()` 호출 추가
- [ ] P1-3-T19: `__init__.py`에서 모든 normalizer 임포트

#### 3.6 DxfEntityExtractor 마이그레이션
- [ ] P1-3-T20: `dxf_entity_extractor.py` import 추가
- [ ] P1-3-T21: `_normalize_*` 메서드 8개 제거 (lines 614-775)
- [ ] P1-3-T22: `_normalize_entity()` 메서드 추가 (registry 사용)
- [ ] P1-3-T23: 기존 호출부 수정

#### 3.7 테스트
- [ ] P1-3-T24: `tests/unit/services/comparison/test_normalizer_registry.py` 생성
- [ ] P1-3-T25: Registry 단위 테스트 작성
- [ ] P1-3-T26: `tests/unit/services/comparison/entity_normalizers/` 디렉토리 생성
- [ ] P1-3-T27: 각 Normalizer별 단위 테스트 작성 (8개 파일)
  - [ ] test_line_normalizer.py
  - [ ] test_circle_normalizer.py
  - [ ] test_arc_normalizer.py
  - [ ] test_polyline_normalizer.py
  - [ ] test_text_normalizer.py
  - [ ] test_mtext_normalizer.py
  - [ ] test_dimension_normalizer.py
  - [ ] test_insert_normalizer.py
- [ ] P1-3-T28: 해시 호환성 회귀 테스트

**P1-3 수용 기준 검증**:
- [ ] P1-3-AC1: 200+ lines 중복 코드 제거 확인
- [ ] P1-3-AC2: 8개 엔티티 타입 100% 지원
- [ ] P1-3-AC3: 새 엔티티 추가 < 50 lines
- [ ] P1-3-AC4: 기존 해시 호환성 유지

---

### Phase 2 마무리 (Day 8-10)

- [ ] P2-FINAL-T01: Phase 2 전체 회귀 테스트 실행
- [ ] P2-FINAL-T02: 코드 라인 수 비교 문서화
- [ ] P2-FINAL-T03: 복잡도 측정 결과 문서화 (radon)
- [ ] P2-FINAL-T04: 코드 리뷰 및 피드백 반영
- [ ] P2-FINAL-T05: Phase 2 완료 보고서 작성

---

## Phase 3: Quick Wins (2-3일)

**예상 기간**: 2-3일
**우선순위**: P2 (Medium)
**담당**: TBD

### QW-1: expand_blocks 옵션 전달

- [ ] QW-1-T01: `compare_worker.py` 수정 - options에서 expand_blocks 추출
- [ ] QW-1-T02: `dwg_differ.py` compare() 시그니처에 expand_blocks 추가
- [ ] QW-1-T03: extractor.extract() 호출 시 expand_blocks 전달
- [ ] QW-1-T04: GUI 옵션 패널에 expand_blocks 체크박스 확인/추가
- [ ] QW-1-T05: E2E 테스트 작성

### QW-2: 해시 충돌 경고

- [ ] QW-2-T01: `dxf_comparator.py` 해시 딕셔너리 생성부 수정 (lines 225-226)
- [ ] QW-2-T02: 중복 해시 발견 시 logger.warning 추가
- [ ] QW-2-T03: 단위 테스트 작성 - 해시 충돌 시나리오

### QW-3: find_near_matches 캐싱

- [ ] QW-3-T01: `drawing_differ.py`에 @lru_cache 적용
- [ ] QW-3-T02: 캐시 키 설계 (해시 기반)
- [ ] QW-3-T03: 캐시 효과 벤치마크 테스트

### QW-4: 엔티티 수 제한 경고

- [ ] QW-4-T01: `dxf_entity_extractor.py`에 ENTITY_LIMIT_WARNING 상수 추가 (50000)
- [ ] QW-4-T02: extract() 반환 전 엔티티 수 체크 및 경고 로깅
- [ ] QW-4-T03: 단위 테스트 작성

### Phase 3 마무리

- [ ] P3-FINAL-T01: Quick Wins 전체 테스트 실행
- [ ] P3-FINAL-T02: Phase 3 완료 보고서 작성

---

## Phase 4: Medium Priority Enhancements (2주+)

**예상 기간**: 2주 (Phase 2 완료 후)
**우선순위**: P3 (Medium)
**담당**: TBD

### P3-1: ODA 배치 처리

- [ ] P3-1-T01: ODA File Converter 설치 확인 유틸리티 작성
- [ ] P3-1-T02: 다중 DWG 파일 일괄 변환 함수 구현
- [ ] P3-1-T03: 변환 큐 관리 클래스 구현
- [ ] P3-1-T04: 배치 처리 진행률 추적 연동
- [ ] P3-1-T05: 통합 테스트 작성

### P3-2: 의존성 주입 (DI)

- [ ] P3-2-T01: DwgDiffer 생성자 리팩토링 - extractor, comparator 주입 가능
- [ ] P3-2-T02: 기본값 설정 (기존 호환성 유지)
- [ ] P3-2-T03: Mock 객체 사용 테스트 작성
- [ ] P3-2-T04: DI 사용 예제 문서화

### P3-3: 타입 힌트 완성

- [ ] P3-3-T01: `dwg_differ.py` 모든 public 메서드 타입 힌트 추가
- [ ] P3-3-T02: `dxf_entity_extractor.py` 타입 힌트 추가
- [ ] P3-3-T03: `dxf_comparator.py` 타입 힌트 추가
- [ ] P3-3-T04: mypy 검증 통과 확인
- [ ] P3-3-T05: py.typed 마커 파일 추가

### P3-4: 청크 기반 Excel 스트리밍

- [ ] P3-4-T01: openpyxl write_only 모드 연구
- [ ] P3-4-T02: export_excel() 메서드 스트리밍 방식으로 리팩토링
- [ ] P3-4-T03: 대용량 비교 결과 (50K+ rows) 메모리 테스트
- [ ] P3-4-T04: 벤치마크 비교 문서화

### Phase 4 마무리

- [ ] P4-FINAL-T01: Phase 4 전체 테스트 실행
- [ ] P4-FINAL-T02: 최종 성능 벤치마크 실행
- [ ] P4-FINAL-T03: 전체 프로젝트 완료 보고서 작성

---

## 전체 진행 현황 요약

### Phase별 작업 수

| Phase | 총 작업 수 | 완료 | 진행 중 | 대기 |
|-------|-----------|------|---------|------|
| Phase 1 | 24 | 0 | 0 | 24 |
| Phase 2 | 28 | 0 | 0 | 28 |
| Phase 3 | 10 | 0 | 0 | 10 |
| Phase 4 | 5 (그룹) | 0 | 0 | 5 |
| **총계** | **67** | **0** | **0** | **67** |

### 마일스톤 체크포인트

| 마일스톤 | 목표일 | 상태 | 완료 기준 |
|----------|--------|------|-----------|
| M1: Phase 1 완료 | Week 1 | [ ] | P0-1,2,3 모든 AC 통과 |
| M2: Phase 2 완료 | Week 3 | [ ] | P1-1,2,3 모든 AC 통과 |
| M3: Phase 3 완료 | Week 3.5 | [ ] | QW-1~4 완료 |
| M4: Phase 4 완료 | Week 5+ | [ ] | P3-1~4 완료 |
| M5: 최종 검증 | Week 5+ | [ ] | 전체 회귀 테스트 통과 |

---

## 의존성 그래프

```
Phase 1 (병렬 가능)
├── P0-1: R-tree
├── P0-2: SSIM
└── P0-3: 예외처리

Phase 2 (순차적)
├── P1-1: ProgressTracker ─────────┐
├── P1-2: extract() 리팩토링 ──────┼── P1-3 의존
└── P1-3: Strategy 패턴 ←──────────┘

Phase 3 (병렬 가능, Phase 1,2 완료 후)
├── QW-1: expand_blocks
├── QW-2: 해시 경고
├── QW-3: 캐싱
└── QW-4: 엔티티 경고

Phase 4 (병렬 가능, Phase 2 완료 후)
├── P3-1: ODA 배치
├── P3-2: DI
├── P3-3: 타입 힌트
└── P3-4: Excel 스트리밍
```

---

## 리스크 체크리스트

### 기술적 리스크

- [ ] R-T01: rtree/libspatialindex 설치 문제 → Fallback 구현 확인
- [ ] R-T02: SSIM 정확도 손실 → 임계값 조정 가능 확인
- [ ] R-T03: 해시 호환성 깨짐 → 회귀 테스트 강화
- [ ] R-T04: 성능 회귀 → 벤치마크 자동화

### 일정 리스크

- [ ] R-S01: Phase 2 복잡도 예상 초과 → Phase 3 먼저 진행 가능
- [ ] R-S02: 코드 리뷰 지연 → 병렬 리뷰 체계 수립

---

## 완료 정의 (Definition of Done)

각 작업 항목은 다음 조건을 만족해야 완료로 표시:

1. [ ] 코드 구현 완료
2. [ ] 단위 테스트 작성 및 통과
3. [ ] 회귀 테스트 통과
4. [ ] 코드 리뷰 완료 (해당되는 경우)
5. [ ] 문서 업데이트 (해당되는 경우)

---

**문서 끝**

*마지막 업데이트: 2025-12-23*
