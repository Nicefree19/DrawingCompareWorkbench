# TEST_CRITERIA — 검증 시나리오

> ⚠️ 실제 실행 명령 + 기대 결과.

## 단일 진입점
```bash
# 추가 대상 테스트가 결정적으로 그린인가 (per-PR 후보)
python -m pytest tests/unit/scripts/test_release_environment_check.py tests/unit/scripts/test_build_spec_bundling.py tests/unit/services/comparison/test_canonical_text_recall.py tests/unit/services/comparison/test_e2e_pipeline_smoke.py tests/unit/services/comparison/test_change_zones.py -q
# GUI(offscreen) 결정성
QT_QPA_PLATFORM=offscreen python -m pytest tests/unit/gui/test_zone_tree_failure_surfacing.py -q
# 메타-가드
python -m pytest tests/unit/scripts/test_cad_policy_gate.py -q
python scripts/cad_policy_gate.py
```

## 개별 시나리오

### T1. 워크플로가 신규 테스트를 실행 → C1
- 실행: `grep -E "test_(release_environment_check|build_spec_bundling|canonical_text_recall|e2e_pipeline_smoke|change_zones)" .github/workflows/cad-format-regression.yml`
- 기대: 5개 파일 모두 매치(per-PR pytest step에 포함).
- 연결 DoD: C1

### T2. check_ci_gate 메타-가드 → C2
- 실행: `python -m pytest tests/unit/scripts/test_cad_policy_gate.py -q` (신규 케이스: 워크플로서 critical 테스트 줄 제거 시뮬 → violation 코드)
- 기대: 누락 시 `CAD_POLICY_CI_*` violation, 정상 워크플로엔 violation 없음.
- 연결 DoD: C2

### T3. GUI 테스트 처리 결정 → C3
- 실행: `QT_QPA_PLATFORM=offscreen python -m pytest tests/unit/gui/test_zone_tree_failure_surfacing.py -q` 2회
- 기대: 안정적 그린 → 게이트 포함; 불안정 → 별도 명시 step/job(주석 사유). **silent-skip 없음**(STATUS에 결정 기록).
- 연결 DoD: C3

### T4. 추가 테스트 결정성 → C4
- 실행: 단일 진입점 1번 명령 **2회 연속**
- 기대: 2회 모두 종료코드 0, 동일 pass 수(flaky 아님).
- 연결 DoD: C4

### T5. 비퇴행·기존 보존 → C5
- 실행: `python scripts/cad_policy_gate.py` + 워크플로 기존 step 존재 확인(`grep -E "measure_golden_accuracy_baseline|git diff --check|cad_policy_gate" .github/workflows/cad-format-regression.yml`)
- 기대: gate `passed`; golden floor·diff-check·policy step 모두 잔존.
- 연결 DoD: C5

## 통과 기준
- [ ] T1~T5 PASS + 출력 요약을 STATUS "검증 로그"에 증거 기록
- [ ] 격리/드롭한 테스트는 STATUS에 사유 기록(silent 금지)
