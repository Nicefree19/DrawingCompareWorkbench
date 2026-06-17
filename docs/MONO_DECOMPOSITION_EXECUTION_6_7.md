# MONO-4 #6 / #7 실행 설계 / Execution plan for the entangled state+callback clusters

**작성일:** 2026-06-17 · **선행:** `docs/MONO_DECOMPOSITION_PLAN.md`, `docs/TECH_DEBT_AUDIT_REPORT.md`
**현재 monolith:** `src/gui/drawing_compare_workbench.py` = **13,214줄** (wc -l 기준)

> **요약 / Bottom line.** #1~5(순수-함수 + OverlayCache)는 깔끔한 net-negative 추출이었다.
> #6(review-state)·#7(render callbacks)은 god-object의 **UI-얽힌 핵심부**라 클린 추출
> 레버리지가 낮고 위험이 높다. 기계적으로 몰아붙이지 말고, **가장 작은 안전 슬라이스를
> 안전망 우선으로** 하나씩 진행한다. 각 슬라이스 = 별도 PR.

## 입증된 사이클 / Proven cycle (반드시 준수)

1. **특성화 테스트 선행** — 추출 대상의 현재 동작을 실 V2 인스턴스(`qapp`)로 고정.
2. **추출** — 협력객체 또는 free 함수. monolith는 @property/delegator facade로 인터페이스 보존.
3. **행동 보존 입증** — 안전망 + `tests/unit/services/comparison/test_viewer_*` + 전체 `tests/unit/gui/` + 라이브 스모크 부팅(`DRAWING_COMPARE_SMOKE_EXIT_MS`).
4. **게이트** — `py_compile` 양쪽 · offscreen import 객체 동일성 · `cad_policy_gate` · `git diff --check`.

**검증 함정 (실측):**
- `tests/unit/gui/` 전체 + 무거운 `test_workbench_phase_c.py`를 **함께** 돌리면 PySide6 6.10 비결정 AV로 pytest 프로세스가 teardown에서 죽는다(하네스 천장, 제품 무관). → 파일별 또는 `-k` 서브셋, 단독 실행. **gui 1-fail는 결정적 재실행으로 flaky 여부 먼저 확인.**
- monolith 줄 수는 **항상 `wc -l`** (Python `len(splitlines())`는 다중 줄을 단일 리스트 원소로 넣으면 과소계상 — #5에서 -99로 잘못 보고, 실제 -53).

---

## #6 — ReviewStateController (중간→높음 위험)

**안전망: 완료** — `tests/unit/services/comparison/test_review_state_characterization.py` (5종, PR #38 머지).

### 깨끗이 추출 가능한 코어 (낮은 레버리지)
| 메서드/필드 | 성격 | 비고 |
|---|---|---|
| `_review_records_v2: dict` | 상태 | controller가 소유, V2는 @property |
| `_review_state_path_v2` | 상태 | 영속 경로 |
| `_review_record_key_v2` | 순수 | `review_state_key` 위임 |
| `_review_status_ko_v2` | 순수 | 상태→한국어 맵 |
| `_review_record_counts_for_pair_v2` | 순수 | `_review_records_v2`만 읽음 |
| record load/save | I/O | `save_review_state`/load + path 관리 |

### V2에 잔류 (UI/cross-state 얽힘 — controller를 호출만)
- `_review_status_for_zone_v2` — `_active_issue_by_zone`/`_active_overlays_by_zone`도 읽음(record 조회만 위임).
- `_set_zone_review_status_v2` — ~7 위젯 오케스트레이션(`zone_detail_v2`·queue summary·filter·progress·badges·auto-advance). record write만 controller에 위임.

### 설계 / Seam
```
class ReviewStateController:
    records: dict[str, ReviewStateRecord]
    path: Optional[Path]
    def clear(); def get(key); def set(record) -> save; def counts_for_pair(pair_id)
    @staticmethod key(pair_id, zone_id); status_ko(status)
    def load(path)  # save_review_state/load 캡슐화
# V2: self._review_records_v2 -> @property = controller.records
#     _review_record_* -> 얇은 delegator; _set_zone_review_status_v2는 record write만 위임 후 기존 UI 호출 유지
```
**예상 레버리지:** net ~ -40~-60줄 (코어만; 대부분 잔류). **위험:** 영속 경로 + fallback 체인 — 5종 안전망으로 검증.
**별도 PR 1개.** `src/gui/workbench_review_state.py`.

---

## #7 — render-callback 분해 (높음 위험)

**안전망: 미작성** — 추출 슬라이스를 정하는 즉시 그 슬라이스용 특성화 테스트 먼저.

### 현실 / Reality
가장 긴 콜백들 — `_load_lightweight_pdf_v2`(265줄), `_on_pair_render_finished_v2`, `_on_zone_crop_render_finished_v2`, `_run_initial_zone_heavy_render_v2`, `_on_visible_tile_window_finished_v2` — 은 **워커 결과 + viewport 위젯 조작**이 본문이다. 대부분 추출 불가(위젯/시그널). 메모리 이력상 **dead-island 버그가 숨는 곳**.

### 클린 추출 가능 (PURE 결정 sub-logic만, free 함수로)
한 번에 하나씩, 각자 특성화 테스트 + free 함수 추출:
| 후보 | 위치(근사) | 성격 |
|---|---|---|
| 활성 zone render request-id 계산/매칭 (`_active_zone_render_request_id_v2`, `_is_current_zone_render_request_v2`, `_begin_selected_zone_render_request_v2`) | L7616~7640 | 순수 문자열/세대(generation) 로직 |
| DPI 캡 / effective-dpi 산정 (in `_load_lightweight_pdf_v2`) | L8135+ | 순수 수치 |
| cache-state 분류 / `stats` dict 조립 | L8153+ | 순수 |
| `_pair_needs_render_v2` 판정 | L10069 | 순수-ish (입력만) |
| `_is_usable_zone_render_source_v2` | L10198 | 순수(staticmethod) |

### 접근 / Approach
1. 위 후보 중 **request-id 매칭**부터(가장 순수, 가장 자주 호출). 특성화 테스트 → `workbench_render_decisions.py`로 free 함수 추출 → V2는 호출.
2. 이어서 DPI/cache-state/`stats` 조립을 `_load_lightweight_pdf_v2`에서 free 함수로 분리(265줄 → orchestrator + 순수 helper들).
3. **위젯 조작 본문은 절대 free 함수로 빼지 않는다** — V2 orchestrator로 남긴다.

**예상 레버리지:** 슬라이스당 net ~ -20~-40줄. **위험:** 높음 — 슬라이스마다 안전망 필수, 라이브 스모크 + 가능하면 실도면 GUI 비교까지.
**슬라이스마다 별도 PR.** `src/gui/workbench_render_decisions.py`.

---

## 순서 / Sequencing 권장
1. **#6 ReviewStateController** (안전망 완료 → 즉시 실행 가능, 위험 중간).
2. **#7 슬라이스 A: request-id 매칭** (가장 순수).
3. **#7 슬라이스 B: `_load_lightweight_pdf_v2` 순수 helper 분리**.
4. (선택) `lightweight_viewport.py`(~2,049줄)가 제2 monolith 되기 전 동일 패턴 감시.

각 단계는 **독립 PR + 위 사이클/게이트 통과**. 무리한 일괄 진행 금지.
