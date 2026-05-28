# S1 Task Checklist

진행 상태:
- [x] **Pre**: ADR-001 Accepted
- [x] **Pre**: AGENTS.md Structural Freeze Rules 추가
- [x] **Pre**: Memory 4건 업데이트
- [x] **Pre**: main commit/merge (c92a119)
- [x] **Pre**: `docs/work-memory/` 디렉토리 신설
- [x] **Pre**: implementation_plan / context_notes / task_checklist 작성

## Implementation Steps

### S1.1 — RenderFailureCode enum + 한국어 메시지 ✅ 완료 (2026-05-28)
- [x] Read 기존 `src/services/comparison/render_modes.py`로 enum 패턴 학습
- [x] Write `src/services/comparison/render_failure_codes.py` (260줄, 예상 ~150)
  - [x] enum 10개 값 (ok + 9 fallback)
  - [x] `code`, `message_ko`, `severity`, `suggested_action_ko`, `requires_user_action` 필드
  - [x] 직렬화/역직렬화 helper (`to_payload`, `info_for`, `describe`, `severity_of`, `highest_severity`)
  - [x] frozenset 분류 (`INFO_CODES`/`WARN_CODES`/`ERROR_CODES`/`HIDDEN_CODES`/`USER_ACTION_REQUIRED_CODES`)
- [x] Write `tests/unit/services/comparison/test_render_failure_codes.py` (201줄, 예상 ~80)
  - [x] 10개 enum 한국어 메시지 존재 검증
  - [x] severity 분포 (info≥1, warn≥1, error≥1) + bucket partition 검증
  - [x] 직렬화 round-trip + JSON serialisable
  - [x] `highest_severity` 다중 코드 우선순위
  - [x] `USER_ACTION_REQUIRED_CODES` 일관성
- [x] Run `python -m pytest tests/unit/services/comparison/test_render_failure_codes.py -v` → **22 passed in 2.11s**
- [x] Run `python scripts/cad_policy_gate.py` → **passed**
- [x] git diff --stat (monolith 0줄 확인) → **0줄 (Freeze Rule 준수)**
- [x] commit
- [x] 사용자 검증 요청 → 사용자 옵션 4 (task_checklist 업데이트 후 모두 commit) 승인

### S1.2 — silent fallback 정밀 inventory ✅ 완료 (2026-05-28)
- [x] Read `src/services/comparison/zone_vector_renderer.py` (핵심 영역 130-220, 620-660)
- [x] Read `src/gui/lightweight_viewport.py` (핵심 영역 65-115, 585-615)
- [x] Read `src/services/comparison/ai_classifier/embedding_classifier.py` (핵심 영역 410-500)
- [x] Grep 추가 fallback 패턴 — DWG_UNSUPPORTED_VERSION이 dwg_importer.py:54에 정식 enum으로 존재함을 발견
- [x] Write `docs/work-memory/S1_SILENT_FALLBACK_INVENTORY.md`
  - [x] 7개 후보 (확장 8개) 모두 파일/라인/현재 동작 정리
  - [x] 작업 대상 7개 (Option A, zone crop 제외) / 후속 1개 (Point 7)
  - [x] enum 확장 결정 사항 정리 (Option A 권장: `dwg_vector_normalise_failed` 추가)
  - [x] 기존 `DwgFailureCode`와 `skipped_reason` 필드 연동 기회 발견
- [x] commit (다음 단계)
- [x] 사용자 검증 요청 ← 다음 응답에서

### S1.3 — 발신 통합 (7개 지점, 5개 sub-슬라이스)
S1.2 inventory 결과: 7개 지점 작업 (Point 7 zone crop stale 제외 — 후속).

- [x] **S1.3.1** — RenderFailureCode enum 확장 (`dwg_vector_normalise_failed`) ✅ 완료 (2026-05-28)
  - [x] Edit `render_failure_codes.py`: Literal + ALL_FAILURE_CODES + FAILURE_CODE_INFO에 신규 entry
  - [x] Edit `test_render_failure_codes.py`: ten → eleven rename + 2 신규 테스트
  - [x] pytest: **24 passed in 2.73s**
  - [x] cad_policy_gate: passed
  - [x] monolith: **0줄 변경**
  - [x] commit
- [ ] **S1.3.2** — Point 1, 6a, 6b 통합 (`zone_vector_renderer.py`)
  - [ ] `ZoneVectorRenderResult`에 `failure_code: RenderFailureCode = "ok"` 필드 추가
  - [ ] Point 1 (line 631-652): `vector_draw_failed` 설정 + 부분 성공 시 `vector_draw_partial`
  - [ ] Point 6a (line 139, 152, 173): `dwg_using_cached_dxf` 발신
  - [ ] Point 6b (line 191-197): `dwg_vector_normalise_failed` 발신
  - [ ] 신규 또는 기존 테스트에 발신 검증 추가
- [ ] **S1.3.3** — Point 2 통합 (`dwg_importer.py` + `import_pipeline.py`)
  - [ ] `DwgFailureCode` → `RenderFailureCode` mapping 함수 추가
  - [ ] `ImportPipelineResult`에 `render_failure_code` 필드 추가 (선택)
  - [ ] mapping 함수 테스트
- [ ] **S1.3.4** — Point 3, 4 통합 (`lightweight_viewport.py`)
  - [ ] `_FallbackQuickWidget`에 `failure_code` 클래스 속성
  - [ ] `LightweightDrawingViewport`에 `render_failure_codes() -> list[RenderFailureCode]` API
  - [ ] QSGLineItem fallback 발신
  - [ ] viewport 단위 테스트
- [ ] **S1.3.5** — Point 5 통합 (`embedding_classifier.py`)
  - [ ] `EmbeddingClassifier`에 `failure_code` 속성 + getter
  - [ ] L422, L484 양 지점에서 `ai_heuristic_fallback` 설정
  - [ ] 단위 테스트
- [ ] commit per sub-slice
- [ ] 사용자 검증 요청 (각 sub-slice 종료 시)

### S1.4 — FailureBadge GUI 위젯
- [ ] Read 기존 GUI 위젯 패턴 (`src/gui/region_match_dialog.py` 참고)
- [ ] Write `src/gui/failure_badge.py` (~200줄)
- [ ] Write `tests/unit/gui/test_failure_badge.py` (~120줄, 오프스크린 Qt)
- [ ] Run pytest
- [ ] git diff --stat (monolith 0줄 확인)
- [ ] commit "S1.4: add FailureBadge widget"
- [ ] 사용자 검증 요청

### S1.5 — once_per_session_logger helper
- [ ] Write `src/utils/once_per_session_logger.py` (~50줄)
- [ ] Write `tests/unit/utils/test_once_per_session_logger.py` (~40줄)
- [ ] Run pytest
- [ ] commit "S1.5: add once_per_session_logger helper"
- [ ] 사용자 검증 요청

### S1.6 — monolith 통합 + smoke
- [ ] Read `src/gui/drawing_compare_workbench.py`의 `DrawingCompareWorkbenchV2.__init__` 영역
- [ ] Edit `src/gui/drawing_compare_workbench.py` (정확히 +2줄, replace_all=false로 안전)
- [ ] Edit `scripts/workbench_acceptance_smoke.py` (+1줄 assert)
- [ ] Run smoke test
- [ ] Run full unit suite `python -m pytest tests/unit -q` (3229+N passed)
- [ ] git diff --stat (monolith 정확히 +2줄 확인)
- [ ] commit "S1.6: integrate FailureBadge into workbench monolith (+2 lines)"
- [ ] 사용자 검증 요청

## Post-Implementation Verification

- [ ] Write `docs/work-memory/S1_COMPLETION_REPORT.md`
  - [ ] 산출물 파일 목록 + 라인 수
  - [ ] silent fallback 작업 대상 N개 / 스킵 M개
  - [ ] 단위 테스트 결과
  - [ ] monolith 라인 수 변화 (14,105 → 14,107)
  - [ ] Structural Freeze Rules 준수 검증
- [ ] main merge (사용자 명시 승인 후)
- [ ] Memory 업데이트 (S1 완료 사실, 발견된 추가 패턴 등)

## Abort Triggers (작업 중단 조건)

- monolith 추가 라인 수가 2를 초과해야 하는 상황 발견 시 → 즉시 중단, 사용자 확인
- S1.2에서 실제 silent fallback이 0개로 밝혀짐 → S1 폐기, S2로 직행
- 단위 테스트가 기존 3229 passed를 깨뜨림 → 즉시 중단, 회귀 원인 분석
- 새 P5-G* 게이트 추가 충동 발생 → 중단, AGENTS.md 룰 재확인
