# Monolith Decomposition Plan / 모놀리스 분해 계획
## `src/gui/drawing_compare_workbench.py` (MONO-4)

**작성일:** 2026-06-16 · **근거:** `docs/TECH_DEBT_AUDIT_REPORT.md` (MONO-4/5)

## 목표 / Goal

13,694줄 단일 파일(V2 god-object: ~271 메서드, `__init__` 80 attribute)을 동결을
지키며(클래스에 줄 추가 금지) **응집 클러스터를 신규 모듈로 순차 추출**해 점진적으로
축소한다. 각 추출은 **net-negative 줄수**여야 하고 동결의 의도를 전진시킨다.

## 입증된 패턴 / Proven pattern (satellite re-export)

1차 추출(`workbench_overlay_model.py`, 2026-06-16)로 패턴을 확립했다:

1. 응집된 **순수/저상태** 코드 묶음을 신규 `src/gui/<name>.py`로 **그대로** 옮긴다.
2. monolith는 `from src.gui.<name> import (...)`로 **공개 API를 re-export** → 기존
   import 경로(`from ...drawing_compare_workbench import X`)와 모든 in-file 호출처가
   무변경으로 작동.
3. **순환 import 회피**: 신규 모듈은 monolith에서 아무것도 import하지 않는다(상수도
   함께 옮긴다).
4. **검증 게이트(필수)**: `py_compile` 양쪽 → offscreen import로 함수 객체 동일성 →
   해당 모듈 전용 테스트 → `tests/unit/gui/` 전체 → `cad_policy_gate` → `git diff --check`.

## 추출 후보 시퀀스 / Sequenced targets

위험도 오름차순. 각 단계는 **독립 PR**, 위 검증 게이트 통과 필수.

| # | 클러스터 | 성격 | 신규 모듈(안) | 위험 | 비고 |
|---|----------|------|---------------|------|------|
| 1 | overlay 헬퍼 6종 + 상수 | 순수 | `workbench_overlay_model.py` | 낮음 | **완료 2026-06-16** (-142) |
| 2 | 순수 요약/포맷 free 함수군 (`natural_change_summary`, `format_top_issue_label`, `format_pattern_group_label`, `_ko_change_type`, `_change_grade`, `_format_count`, `_int_value`) | 순수 | `workbench_summary_format.py` | 낮음 | **완료 2026-06-16** (-137) |
| 3 | bbox↔pixel 변환 free 함수군 (`compute_pdf_page_pin_overlay`, `_cad_bbox_to_pixel_rect`, `_world_bbox_to_pixel_rect`, `_lightweight_tile_zoom_from_transform`) | 순수(수학) | `workbench_bbox_transform.py` | 낮음 | **완료 2026-06-16** (-150). `scale_pdf_bbox_to_render_pixels`는 30-use `_viewer_pair_is_pdf` 의존이라 잔류 |
| 4 | viewer 소스/경로 resolve free 함수군 (`_resolve_viewer_artifact_path`, `_resolve_pdf_viewer_source_path`, `_existing_pdf_file`, `_is_redacted_artifact_path`) | 순수 | `workbench_viewer_source.py` | 낮음 | **완료 2026-06-16** (-91). `_viewer_pair_is_pdf` 의존 없음 확인; redaction 테스트 통과 |
| 4b | viewer-pair 술어 + PDF bbox 스케일 (`_viewer_pair_is_pdf`, `scale_pdf_bbox_to_render_pixels`) | 순수 | `workbench_viewer_pair.py` | 낮음 | **완료 2026-06-17** (-49). #3에서 잔류했던 쌍을 묶음; `_viewer_pair_is_pdf` 31-use re-import |
| 5 | `_viewer_overlay_cache*` 5필드+메서드 (상태) | 상태 | `OverlayCache` 협력 객체 | 중간 | **완료 2026-06-17** (net -99). `workbench_overlay_cache.py`로 이동, V2엔 5 @property+5 delegator(facade)만 잔류. 안전망 `test_overlay_cache_characterization.py`(8종)이 행동 보존 입증(14+73+184 통과+라이브 부팅) |
| 6 | review-state 순수 헬퍼 (`review_status_ko`·`count_review_records`) | 순수 | `workbench_review_state.py` | 낮음 | **완료 2026-06-17** (-15). 안전망 5종 검증. stateful `_review_records_v2`/path + set-status UI 메서드(~7위젯, 안전망 미커버)는 V2 잔류 — 완전한 stateful ReviewStateController는 **보류**(레버리지 ~0 + 테스트 미커버 사이트 리스크) |
| 7-A | 순수 render-decision (`is_usable_zone_render_source`·request-id 매처 2종) | 순수 | `workbench_render_decisions.py` | 낮음 | **완료 2026-06-17** (net ~0; 격리/테스트성 가치). 안전망 `test_render_decisions_characterization.py`(4종) |
| 7-B | 최장 렌더 콜백 본문 (`_load_lightweight_pdf_v2` 등) | 콜백 | 순수 sub-logic(DPI/cache-state/stats)만 free 함수 | 높음 | **dead-island 영역 — 슬라이스별 안전망 선행**. 본문 대부분(워커+위젯)은 추출 불가 |

## 가드레일 / Guardrails

- **동결 준수**: 추출은 net-negative이므로 ≤5-line-add 권장한계의 예외(삭제·이동).
  클래스 본문에 새 로직 추가 금지.
- **신규 P5-G* 게이트 금지**(동결 #2). 단, monolith 줄수 **비증가 CI assertion**은
  기존 의도의 강화로 허용(신규 audit 게이트 아님) — 5단계 이후 도입 검토.
- `src/gui/lightweight_viewport.py`(~2,049줄)가 제2의 monolith가 되기 전에 동일 패턴
  적용을 병행 감시.
- 5~7단계(상태/콜백)는 **추출 전 테스트 보강** 필수 — 현재 V2는 method-rebinding
  hack(`test_workbench_ai_prepare.py`)으로만 구동되어 회귀 탐지력이 낮다.

## 현황 / Status

- 14,857 → V1 -1,021 → overlay -142 → 요약/포맷 -137 → bbox/pixel -150 → viewer-source -91 → viewer-pair -49 → OverlayCache -53 = **13,214줄** (누적 -1,643, **wc -l 기준**; OverlayCache의 직전 -99 기록은 측정 아티팩트, 실제 -53).
- 순수-함수 추출(#1~4b) + 첫 **상태 협력객체** 추출(#5 OverlayCache, 안전망 선행→추출→행동보존 검증의 모범 사이클) 완료. 남은 #6(review-state)·#7(렌더 콜백)은 동일 패턴(특성화 테스트 선행 → 협력객체 추출)으로, 각각 별도 PR 권장.
- **#6/#7 실행 설계 = `docs/MONO_DECOMPOSITION_EXECUTION_6_7.md`** (정확한 추출 범위·시임·검증 게이트·위험·순서). #6 안전망은 완료(PR #38), #7은 슬라이스별 안전망 선행 필요. 둘 다 저 클린레버리지·고위험이므로 신중한 별도 세션 권장.
- 검증 주의: `tests/unit/gui/`는 PySide6 6.10 비결정 AV로 **간헐 1-fail flaky** 가능 — 회귀 판정 전 반드시 결정적 재실행으로 확인(본 세션서 184-pass 재현).
