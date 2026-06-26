# GOAL — 핵심 목표

## 한 줄 정의
**검증된 silent-failure 경로를 visible(사용자 신호) 또는 gated(빌드 차단)로 전환**해, 치명적 실패가 조용히 넘어가지 않게 한다 — 각 전환은 결정적 테스트로 고정.

## 배경/맥락
"프로그램 안정적 실행/작동" 북극성의 다음 단계. 3각도 멀티에이전트 감사(error-UX · runtime · build/deploy) 결과 **단일 뿌리 = silent_fallback**: 코드는 우아하게 저하/삼키지만 사용자·빌드가 모른다.

**정직성 노트(중요):** 감사 에이전트의 "top lever" 다수가 재검증에서 **과장**으로 드러나 제외함 —
- R1 cosmetic 루프(dxf_comparator.py:1320): O(n) 선형, 행 아님 → 드롭.
- R2 viewer OOM: `assert_within_memory_budget` 가드 이미 존재(viewer_package.py:365) → 드롭.
- G2 smoke flag: exe가 `--smoke-exit-ms` 이미 처리(drawing_compare_workbench.py:13456) → 마진.
- G4 cache writability: `ensure_subdir`에 `except OSError` 이미 있음 → 마진.

아래 DoD는 **재검증에서 살아남은 real 항목만**. 목표는 "감사 리스트 소화"가 아니라 verified-real만 고친다.

## 검증 가능한 종료조건 (Definition of Done)
> 각 항목 객관 체크 + 검증 명령. 모호어 금지.

- [ ] **D1 (release gate)**: `scripts/release_environment_check.py`가 REQUIRED 모듈 누락 시 **nonzero 종료**(현재 무조건 `return 0`), 릴리스 스크립트가 PyInstaller **전에 하드 게이트**로 호출. · 검증: 필수모듈 누락 시뮬 → 종료코드≠0 단위테스트
- [ ] **D2 (bundle proof)**: `test_build_spec_bundling.py`가 **datas 존재**(QML assets·`scripts/render_viewer_package_subprocess.py`) 단언 + spec 누락 시 명시. · 검증: `pytest tests/unit/scripts/test_build_spec_bundling.py`
- [ ] **D3 (zone-failure visible)**: zone-tree 로드 실패(`_on_full_zone_tree_overlay_failed_v2`/`_plan_failed_v2`, L7311/7611)·zone-crop 에러(L11223)가 status/badge에 **표면화**(현재 log+perf만). · 검증: offscreen 인스턴스+실패 콜백 → status/badge 갱신 단언
- [ ] **D4 (zero-change clarity)**: 변경 0건 결과가 "변경 없음 · 파일 일치"로 **명시**(현재 모호한 빈 요약). · 검증: 동일 도면쌍 → 요약/상태 텍스트 단언
- [ ] **D5 (no regression)**: comparison at-risk 유닛 + 신규 테스트 그린 · `cad_policy_gate` 그린(**모놀리스 라인-실링 비증가 포함**). · 검증: `pytest` + `cad_policy_gate.py`

## 범위 밖 (Out of Scope)
- **코드 서명**(인증서)·**클린 VM 빌드-실행 테스트**(VM) — P2 잔여, 환경 의존.
- **드롭된 과장 항목**(R1/R2/G2/G4).
- **recall 정확도**(완료).
- **대형 실도면 라이브 GUI 행 재현**(사람·환경).

## 산출물 목록
- `scripts/release_environment_check.py` + `release_drawing_compare_workbench.py` (D1)
- `tests/unit/scripts/test_build_spec_bundling.py` (+spec 필요시) (D2)
- `src/gui/drawing_compare_workbench.py` 또는 satellite (D3/D4, **라인-실링 비증가**)
- 신규 회귀 테스트 + 완료 보고(통과 명령·출력·DoD 충족표)
