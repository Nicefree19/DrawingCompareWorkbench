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

### S1.2 — silent fallback 정밀 inventory
- [ ] Read `src/services/comparison/zone_vector_renderer.py` (전체)
- [ ] Read `src/gui/lightweight_viewport.py` (전체)
- [ ] Read `src/services/comparison/ai_classifier/embedding_classifier.py`
- [ ] Grep 추가 fallback 패턴 (`fallback`, `recover`, `using cached`, `unavailable`)
- [ ] Write `docs/work-memory/S1_silent_fallback_inventory.md`
  - [ ] 7개 후보 각각 파일/라인/현재 동작/이미 가시화 여부
  - [ ] 작업 대상 N개 / 스킵 M개 결론
- [ ] commit "S1.2: inventory silent fallback points"
- [ ] 사용자 검증 요청

### S1.3 — 발신 통합 (N개 지점)
S1.2 결과에 따라 결정. 각 지점마다:
- [ ] Edit 해당 파일 (~5-10줄 추가)
- [ ] Write 또는 Edit 해당 unit test (~10줄)
- [ ] Run 해당 unit test
- [ ] commit per file (또는 한 commit으로 묶음)
- [ ] 사용자 검증 요청

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
