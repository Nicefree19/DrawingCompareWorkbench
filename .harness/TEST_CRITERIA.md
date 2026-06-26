# TEST_CRITERIA — 검증 시나리오

> ⚠️ "동작 확인" 금지. 실제 실행 명령 + 기대 결과. 일부 테스트는 Phase 2에서 신설(아래는 검증 계약).

## 단일 진입점
```bash
# 게이트류
python scripts/cad_policy_gate.py
python scripts/release_environment_check.py --strict   # D1 신설 플래그(또는 항상 게이트)
# 단위/회귀 (offscreen은 GUI 테스트용)
python -m pytest tests/unit/scripts/test_release_environment_check.py tests/unit/scripts/test_build_spec_bundling.py -q
QT_QPA_PLATFORM=offscreen python -m pytest tests/unit/services/comparison/ -k "zone_failure or zero_change or canonical_text or e2e_pipeline" -q
```

## 개별 시나리오

### T1. release env-check가 누락 시 차단 → D1
- 실행: 신설 단위테스트 — `runtime_modules`의 한 REQUIRED(예: scipy) `_import_status`를 unavailable로 monkeypatch → 게이트 함수 호출
- 기대: 종료코드/반환값 **≠0** (nonzero), 메시지에 누락 모듈명. 모두 가용 시 0.
- 연결 DoD: D1

### T2. spec datas 번들 보증 → D2
- 실행: `python -m pytest tests/unit/scripts/test_build_spec_bundling.py -q`
- 기대: 신규 케이스 — spec `datas`가 `src`(QML assets 포함)·`scripts`(render_viewer_package_subprocess.py)를 싣음을 단언. PASS.
- 연결 DoD: D2

### T3. zone 실패가 사용자에게 보임 → D3
- 실행: `QT_QPA_PLATFORM=offscreen python -m pytest ...test_zone_failure_surfacing.py -q` (신설)
- 기대: offscreen 인스턴스에서 `_on_full_zone_tree_overlay_failed_v2`/`_on_zone_crop_render_error_v2`를 강제 호출 → `lbl_status_v2` 텍스트 또는 FailureBadge 코드가 갱신됨을 단언(현재는 무변).
- 연결 DoD: D3

### T4. 변경 0건이 명시됨 → D4
- 실행: 신설 테스트 — 동일 도면쌍(golden 01_identical) 요약 포맷 → "변경 없음"/"일치" 포함 단언 (가능하면 `workbench_summary_format` 순수 함수 레벨)
- 기대: 0-change 요약 텍스트에 명시 문구. 1+ change엔 미포함(특수 케이스만).
- 연결 DoD: D4

### T5. 동결·분식 가드 → D3/D4/D5
- 실행: `python scripts/cad_policy_gate.py` + `python scripts/measure_golden_accuracy_baseline.py`
- 기대: policy gate `passed`(모놀리스 라인-실링 비증가) **AND** golden `noise_fp=0`·recall/precision 비퇴행(가시성 작업이 정확도 안 건드림 입증).
- 연결 DoD: D5(분식 없음·라인-실링)

### T6. 회귀 없음 → D5
- 실행: `python -m pytest tests/unit/services/comparison/ -k "canonical_text or e2e_pipeline or change_zones or region_aware" -q` + 신규 테스트
- 기대: 종료코드 0, 신규 테스트 포함 그린.
- 연결 DoD: D5

## 통과 기준
- [ ] T1~T6 전 시나리오 PASS + 출력 요약을 STATUS.md "검증 로그"에 증거 기록
- [ ] 드롭한 타겟(약함 판정)은 STATUS에 사유 기록(silent 누락 금지)
