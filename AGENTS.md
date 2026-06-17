# TEKLA_MCP Codex Agent Rules

This file is for OpenAI Codex when it works in this repository.

## Role

Codex can implement changes when the user explicitly requests implementation. Claude Code may still be the primary implementer, but Codex is allowed to edit repository files for user-approved tasks.

When the user asks for a review only (no edits), Codex should follow the Review Output format below and describe mutating commands as recommendations instead of running them.

## Operating Rules

- Codex may read and edit files when the user explicitly asks for implementation.
- Keep edits narrowly scoped to the requested task.
- Do not run destructive git commands (e.g., `reset --hard`, `clean -fdx`, force-push, branch deletion) without explicit user approval.
- Do not stage, commit, push, tag, merge, or delete files unless the user explicitly asks.
- Preserve unrelated dirty worktree changes; do not revert in-flight work by the user or Claude Code.
- Run targeted tests for the touched module after edits; broaden the suite when changes cross module boundaries.
- Do not skip git hooks (`--no-verify`) or bypass signing without explicit user approval.
- Do not assume fictional MCP tool names. Use only tools that are actually listed by the active client.

## Review Output

When Codex produces review findings (whether in review-only mode or alongside an implementation task), lead with findings ordered by severity. Use this format for each issue:

- Severity: `CRITICAL` | `HIGH` | `MEDIUM` | `LOW`
- Impact: concrete runtime, security, data, performance, or maintainability risk
- Evidence: file path, line, diff hunk, command output, or reproducible scenario
- Recommendation: specific fix direction
- Tests: targeted test or validation scenario

If no issues are found, say so clearly and list any residual test gaps.

## TEKLA_MCP Context

- Primary project domain: MIDAS Gen MGT parsing, Tekla Structures integration, structural/BIM automation, drawing comparison, and Windows release tooling.
- Collaboration source of truth: `docs/collab/`.
- Review records belong in `docs/collab/REVIEWS.md` or a Sync Packet when requested by the user or by Claude.
- Implementation work that materially changes behavior should leave a 1-line entry in `docs/collab/WORKLOG.md` (append-only) and update `docs/collab/STATUS.md` if the active work item state changes.
- Commit gates should treat unresolved `CRITICAL` or `HIGH` Codex findings as blockers unless the user explicitly accepts the risk.

## Structural Freeze Rules (2026-05-28 도입)

다음 규칙은 명시적 ADR/사용자 합의로 해제되기 전까지 모든 에이전트(Claude, Codex 포함)에 적용된다.

### 1. `src/gui/drawing_compare_workbench.py` 줄 추가 동결
- 이 파일은 현재 **13,198줄**짜리 monolith이며 V2(`DrawingCompareWorkbenchV2` 클래스)가 **유일한** 메인 윈도우다. (줄 수는 항상 `wc -l` 기준으로 적는다 — Python `len(splitlines())` 기반 측정은 다중 줄을 단일 리스트 원소로 넣으면 과소계상된다.)
- 2026-06-17: **#6 순수 review-state 헬퍼 추출** — `review_status_ko`·`count_review_records`를 `workbench_review_state.py`로 이동, V2는 delegator(-15). stateful `_review_records_v2`/`_review_state_path_v2` + set-status UI 메서드는 안전망 미커버라 V2 잔류(stateful ReviewStateController는 보류). 안전망 `test_review_state_characterization.py`(5종)이 검증.
- 2026-06-17: **#7-A 순수 render-decision 추출** — `is_usable_zone_render_source`+request-id 매처 2종을 `workbench_render_decisions.py`로, V2 delegator(net ~0, 줄수보다 격리/테스트성 가치). 렌더 콜백 본문(워커결과+위젯)은 추출 불가로 V2 잔류. 안전망 `test_render_decisions_characterization.py`(4종).
- 2026-06-16: 죽은 V1(`DrawingCompareWorkbench`) 클래스와 V1 전용 `ScanWorker`를 제거(-1,021줄). 저장소 전역 검증에서 V1은 인스턴스화·import 0건이고 `ScanWorker`는 V1 span 내부에서만 참조됨이 확인됨. 근거: `docs/TECH_DEBT_AUDIT_REPORT.md` (MONO-1/2).
- 2026-06-16: god-object 분해 위성 추출 — **1차** 순수 overlay 헬퍼 6종+상수→`workbench_overlay_model.py`(-142), **2차** 순수 요약/포맷 헬퍼 7종→`workbench_summary_format.py`(-137), **3차** 순수 bbox/pixel 변환 4종→`workbench_bbox_transform.py`(-150), **4차** 순수 viewer 소스/경로 resolve 4종→`workbench_viewer_source.py`(-91), **5차** viewer-pair 술어+PDF bbox 스케일(`_viewer_pair_is_pdf`·`scale_pdf_bbox_to_render_pixels`)→`workbench_viewer_pair.py`(-49). monolith는 re-import로 공개 API 보존.
- 2026-06-17: **#5 상태 협력객체 추출** — V2의 `_viewer_overlay_cache*` 5필드+5메서드(LRU+byte 캐시)를 `workbench_overlay_cache.py`의 `OverlayCache`로 이동(net **-53**, wc -l 기준; 직전 기록의 -99는 측정 아티팩트였음). 순수-함수와 달리 V2에 **5 read-only @property + 5 얇은 delegator**를 남겨 call site·14개 테스트 무변경(facade). add 라인 ≤5 초과(facade)이나 net-negative 분해이므로 예외. 안전망: `test_overlay_cache_characterization.py`(8종). 후속 시퀀스: `docs/MONO_DECOMPOSITION_PLAN.md`.
- **모든 신규 위젯/워커는 별도 모듈에 만든다** (예: `src/gui/failure_badge.py`, `src/gui/sheet_match_panel.py`).
- 이 파일에 대한 단일 PR의 add 라인 수 ≤ 5를 권장 한계로 한다(삭제는 권장·예외). 초과 시 PR 본문에 사유 명시.
- 위치 참조는 **라인 번호 대신 클래스/심볼 앵커**를 사용한다(파일 드리프트로 L-번호가 자주 어긋남).

### 2. P5-G* 게이트 신설 동결
- 현재 P5-G1 ~ P5-G30 게이트가 1주 안에 누적된 상태 (gate inflation).
- 새 audit 게이트 추가 제안 시 다음 3개 질문에 답하지 못하면 추가 금지:
  1. 이 게이트가 실패하면 사용자가 어떤 구체적 시나리오에서 무엇을 잃는가?
  2. 동일 신호를 기존 G1~G30 중 어디서도 잡지 못하는가?
  3. recall/precision/사용자 가시 metric인가, 아니면 또 RSS/byte/elapsed인가?
- 기존 게이트 hardening은 허용. 새 namespace의 메트릭(예: `sheet_match_*`)은 허용.

### 3. PDF-first 구현 코드 동결
- PDF-first 전환은 [docs/adr/ADR-001-pdf-first-transition.md]가 Accepted 상태로 결정되기 전까지 구현·skeleton·prototype 금지.
- 진행 중인 PDF 비교 경로의 hardening은 별개 (옵션 D 노선).

### 4. 회피 시 보고 의무
세 규칙 중 하나라도 위반해야 하는 작업이라면, 작업 시작 전 `docs/collab/STRUCTURAL_FREEZE_EXCEPTION_REQUEST.md`에 사유·범위·승인자를 기록한다.
