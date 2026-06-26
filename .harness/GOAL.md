# GOAL — 핵심 목표

## 한 줄 정의
**per-PR CI가 이번 신뢰성 작업(게이트·테스트)을 실제로 강제**하게 한다 — silently-inert(존재하나 미실행) 테스트 0.

## 배경/맥락
신뢰성 아크의 **캡스톤**. 직전까지 deps·freeze·recall·build-spec·fail-loud을 게이트/테스트로 추가했으나, **per-PR 게이트(`.github/workflows/cad-format-regression.yml`)는 하드코딩 15-파일 pytest 목록만 실행** → 이번 세션 신규 테스트 **전부 미포함**:
- `test_release_environment_check.py`(D1·deps 계약) · `test_build_spec_bundling.py`(D2·#52) · `test_canonical_text_recall.py`(recall) · `test_e2e_pipeline_smoke.py`(E2E) · `test_change_zones.py`(rebar cap) · `test_zone_tree_failure_surfacing.py`(D3).

즉 게이트는 만들었으나 **PR을 보호하지 않는다** = silent_fallback의 CI-층 재발. (단 golden floor `--min-recall 0.68`·`cad_policy_gate`·`git diff --check`는 이미 per-PR.)

**정직성 노트**: 전체 comparison 스위트 gating은 PySide6 native-AV 천장([[full_suite_health]])으로 비결정 → 범위 밖. 목표는 **결정적** 테스트만 per-PR로.

## 검증 가능한 종료조건 (DoD)
- [ ] **C1**: 결정적 신규 테스트가 per-PR pytest 목록에 추가됨 — release_environment_check·build_spec_bundling·canonical_text_recall·e2e_pipeline_smoke·change_zones. · 검증: 워크플로 grep + 각 테스트 그린
- [ ] **C2 (meta-guard)**: `cad_policy_gate.check_ci_gate`가 위 critical 테스트 파일이 워크플로에 있음을 단언(미래 silent 제거 방지) — **기존 check_ci_gate 확장**(신규 게이트 아님). · 검증: 파일 제거 시뮬→게이트 violation
- [ ] **C3**: GUI 테스트(zone_tree_failure_surfacing)는 결정성 평가 후 **포함 or 별도 명시 job으로 격리**(silent-skip 금지). · 검증: 결정 + 사유 기록
- [ ] **C4**: 추가 테스트가 per-PR서 **결정적 그린**(반복 실행 flaky 아님). · 검증: 2회 연속 그린
- [ ] **C5 (no regression)**: `cad_policy_gate` 그린(라인-실링 포함)·기존 게이트 무변. · 검증: gate + 기존 워크플로 step 보존

## 범위 밖
- 전체 comparison/gui 스위트 gating(native-AV 천장).
- **lint/type CI**(별도 작업).
- workflow_dispatch full-suite 변경.
- 새 비교 로직·정확도(분식 금지).

## 산출물
- `.github/workflows/cad-format-regression.yml`(테스트 목록 확장)
- `scripts/cad_policy_gate.py`(check_ci_gate 확장) + `tests/unit/scripts/test_cad_policy_gate.py`
- 완료 보고(워크플로 diff·테스트 그린·DoD 충족표)
