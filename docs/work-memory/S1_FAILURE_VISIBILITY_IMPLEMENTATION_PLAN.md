# S1 Implementation Plan — Silent Fallback Visibility

| 항목 | 값 |
|---|---|
| Work Item ID | S1 |
| 작성일 | 2026-05-28 |
| 작성자 | Claude (직접 실행자) |
| 상태 | Planning → Ready to execute |
| 근거 ADR | [ADR-001 PDF-first](../adr/ADR-001-pdf-first-transition.md) |
| 제약 | [AGENTS.md Structural Freeze Rules](../../AGENTS.md) |

---

## 1. Objective (목적)

`silent fallback` 7개 지점을 사용자 가시화한다. 코드가 친절하게 fallback해도
viewport에 정직한 상태 배지가 표시되어 사용자가 "지금 fallback이다 / 부분 실패다 /
캐시 DXF 본다"를 즉시 인지할 수 있도록 한다.

**비목표**:
- silent fallback 동작 자체 변경 금지 (오직 가시화)
- PDF-first 구현 코드 추가 금지 ([ADR-001](../adr/ADR-001-pdf-first-transition.md) §5)
- monolith `drawing_compare_workbench.py`에 단일 PR add ≤ 2줄
- 새 P5-G* 게이트 신설 금지

## 2. Current State (현재 상태)

### 2.1 코드 근거로 확인된 silent fallback 7개 후보 (S1.2에서 정밀 검증 예정)

| # | 파일 / 라인 (추정) | 현 동작 | 사용자 영향 |
|---|---|---|---|
| 1 | `src/services/comparison/zone_vector_renderer.py:631` | `Zone SVG draw failed after N accepted entities` (부분 성공) | 부분 그림이 완성으로 보임 |
| 2 | `src/services/comparison/zone_vector_renderer.py` | `DWG_UNSUPPORTED_VERSION` → 캐시 DXF | 원본 DWG 비교가 아닌데 사용자는 모름 |
| 3 | `src/gui/lightweight_viewport.py:98-109` | `_FallbackQuickWidget` | QQuickWidget 없을 때 무조건 fallback |
| 4 | `src/gui/lightweight_viewport.py` | `QSGLineItem unavailable; using Canvas skeleton` (160+ INFO 로그) | 로그 노이즈, 사용자는 모름 |
| 5 | `src/services/comparison/ai_classifier/embedding_classifier.py` | `Embedding backend unavailable → heuristic-only` | AI 분류가 작동 안 함을 모름 |
| 6 | `src/services/comparison/zone_vector_renderer.py` | DWG → cached DXF (`Reusing shared DWG DXF cache`) | 캐시 신선도 인지 불가 |
| 7 | `src/gui/drawing_compare_workbench.py` (zone crop) | Zone crop stale/cancel | 왜 결과가 안 나오는지 모름 |

### 2.2 영향 받지 않는 (이미 가시화된) 항목
S1.2에서 코드 정밀 검증 시 일부는 이미 가시화돼 있을 수 있음. 그 경우 보고서에 명시하고 스킵.

## 3. Proposed Changes per Slice (슬라이스별 변경)

### S1.1 — RenderFailureCode enum + 한국어 메시지 (이번 작업 첫 슬라이스)
- **신규 파일**: `src/services/comparison/render_failure_codes.py` (~150줄)
- **enum 값 (10개)**:
  ```python
  OK
  DWG_UNSUPPORTED_VERSION
  VECTOR_DRAW_PARTIAL
  VECTOR_DRAW_FAILED
  BACKEND_FALLBACK_QQUICKWIDGET
  BACKEND_FALLBACK_CANVAS_SKELETON
  AI_HEURISTIC_FALLBACK
  DWG_USING_CACHED_DXF
  ZONE_CROP_STALE
  ZONE_CROP_CANCELLED
  ```
- **각 code에 필드**: `code` (영문 진단), `message_ko` (사용자 한국어), `severity` (info/warn/error), `suggested_action_ko`
- **신규 테스트**: `tests/unit/services/comparison/test_render_failure_codes.py` (~80줄)
  - 10개 enum 값 모두 한국어 메시지 존재
  - severity 분포 (info≥1, warn≥4, error≥2)
  - 직렬화 round-trip
- **monolith 영향**: 0줄
- **DoD**: `python -m pytest tests/unit/services/comparison/test_render_failure_codes.py -v` 전체 pass

### S1.2 — silent fallback 정밀 inventory (코드 읽기 + 문서화)
- **신규 파일**: `docs/work-memory/S1_silent_fallback_inventory.md`
- **각 지점 검증**: 파일 경로, 라인, 현재 동작, 이미 가시화됐는지 여부
- **이미 가시화된 지점은 명시**: 작업 스코프에서 제외
- **monolith 영향**: 0줄 (코드 변경 없음)
- **DoD**: 7개 지점 모두 분류 완료 (작업 대상 N개 / 이미 가시화 M개)

### S1.3 — 7개 (또는 N개) 지점에 RenderFailureCode 발신 추가
- **변경 파일**: S1.2 결과에 따라 N개 (예: `zone_vector_renderer.py`, `embedding_classifier.py` 등)
- **각 지점 추가 코드**: ~5-10줄 (fallback 발생 시 `RenderFailureCode` emit/log/return)
- **신규 테스트**: 각 지점마다 발신 검증 (~10줄 per 지점)
- **monolith 영향**: 0줄
- **DoD**: 각 지점 unit test로 정확한 code 발신 검증

### S1.4 — FailureBadge GUI 위젯
- **신규 파일**: `src/gui/failure_badge.py` (~200줄)
- **위젯**: 상단 작은 색상 칩 (녹=ok 숨김, 노=warn, 빨=error) + 클릭 시 상세 다이얼로그
- **API**: `FailureBadge(parent)`, `set_failure_codes(codes: list[RenderFailureCode])`, `clear()`
- **신규 테스트**: `tests/unit/gui/test_failure_badge.py` (~120줄)
  - 오프스크린 Qt로 색상/텍스트/visibility 검증
  - severity별 색상 매핑
- **monolith 영향**: 0줄
- **DoD**: pytest pass

### S1.5 — once_per_session_logger helper
- **신규 파일**: `src/utils/once_per_session_logger.py` (~50줄)
- **API**: `@once_per_session_log(logger, level=INFO)` 데코레이터 또는 `log_once(logger, level, key, message)`
- **신규 테스트**: throttle 검증 (~40줄)
- **monolith 영향**: 0줄
- **DoD**: 같은 key 두 번째 호출은 DEBUG로 강등됨

### S1.6 — workbench monolith에 FailureBadge 통합
- **변경 파일**: `src/gui/drawing_compare_workbench.py` **+2줄만**
  - import: `from src.gui.failure_badge import FailureBadge`
  - viewport 추가 위치에 `self.failure_badge = FailureBadge(self); layout.addWidget(self.failure_badge)` (한 줄로)
- **smoke 테스트 보강**: 기존 workbench_acceptance_smoke에 "AC1024 DWG 시도 → 노란 배지 + '호환 캐시 DXF로 비교' 메시지" assert 1줄 추가
- **monolith 영향**: 정확히 +2줄 (Freeze Rule 한계 5줄 내)
- **DoD**: smoke 테스트 pass

## 4. Dependencies (의존성)

- ADR-001 Accepted ✅
- AGENTS.md Structural Freeze Rules ✅
- Memory 갱신 ✅ (gate_inflation_risk, workbench_monolith, silent_fallback_pattern, pdf_first_decision)
- main commit/merge ✅ (c92a119)

S2 (multi-sheet metrics)와 S1은 독립. S1 완료 후 S2로 진행.

## 5. Verification Steps (각 슬라이스 DoD)

각 슬라이스 commit 직전 실행:
1. `python -m pytest <해당 슬라이스 테스트 파일> -v` → 전체 pass
2. `python scripts/cad_policy_gate.py` → pass (정책 위반 없음)
3. `git diff --stat` → 영향 라인 수 확인 (monolith ≤2줄)
4. `git diff --check` → 공백/문법 문제 없음
5. (마지막 슬라이스 S1.6 후) 전체 unit suite `python -m pytest tests/unit -q` → 3229+N passed

## 6. Rollback Strategy (롤백 전략)

- 각 슬라이스는 독립 commit → 슬라이스 단위 `git revert <hash>` 가능
- monolith에 영향 주는 S1.6만 별도 commit으로 격리 (앞 5 슬라이스가 통과해도 S1.6만 revert 가능하게)
- 전체 S1 rollback 필요 시: `git revert` chain 또는 별도 cleanup commit

## 7. 작업 순서 (직렬)

1. **S1.1** — RenderFailureCode 모듈 + test (이번 시작 슬라이스)
2. **S1.2** — silent fallback 정밀 inventory
3. **S1.3** — 발신 통합 (각 지점)
4. **S1.4** — FailureBadge 위젯
5. **S1.5** — once_per_session_logger
6. **S1.6** — monolith 통합 + smoke

각 슬라이스 종료 시 사용자 검증 받고 commit.

## 8. 위험 (Risks)

| 위험 | 확률 | 대응 |
|---|---|---|
| S1.2에서 실제 silent fallback 지점이 7개보다 적게 나옴 | 중 | 작업량 축소, 빠른 완료 |
| `_FallbackQuickWidget` 가시화가 Qt 이벤트 루프 위험 | 낮 | S1.4에서 격리 테스트 |
| monolith에 2줄 추가가 기존 import 순서/구조 깨짐 | 낮 | S1.6 분리 commit, 즉시 revert 가능 |
| once_per_session 로깅이 디버깅 어렵게 함 | 낮 | env var로 throttle 해제 옵션 |

## 9. 산출물 매니페스트 (S1 완료 시점)

```
src/services/comparison/render_failure_codes.py        (신규)
src/utils/once_per_session_logger.py                   (신규)
src/gui/failure_badge.py                               (신규)
src/services/comparison/zone_vector_renderer.py        (수정, ~10줄)
src/services/comparison/ai_classifier/embedding_classifier.py  (수정, ~5줄)
src/gui/lightweight_viewport.py                        (수정, ~5줄)
src/gui/drawing_compare_workbench.py                   (수정, +2줄)
tests/unit/services/comparison/test_render_failure_codes.py    (신규)
tests/unit/services/comparison/test_zone_vector_renderer_failure_codes.py (신규 또는 기존 추가)
tests/unit/gui/test_failure_badge.py                   (신규)
tests/unit/utils/test_once_per_session_logger.py       (신규)
scripts/workbench_acceptance_smoke.py                  (수정, +1줄 assert)
docs/work-memory/S1_silent_fallback_inventory.md       (신규)
docs/work-memory/S1_COMPLETION_REPORT.md               (신규, S1.6 후)
```

추정 총 라인 변경: 신규 ~700줄, 수정 ~30줄, monolith 정확히 +2줄.
