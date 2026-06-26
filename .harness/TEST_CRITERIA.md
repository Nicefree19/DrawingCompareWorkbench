# TEST_CRITERIA — 검증 시나리오

> ⚠️ "동작 확인" 금지. 아래는 모두 **실행 검증된** 실명령(2026-06-26 확인). 단일 진입점 우선.

## 단일 진입점
```bash
# 1) 정확도 측정 (per-pair + AGGREGATE recall/precision/f1/noise_fp 출력 + 리포트 갱신)
python scripts/measure_golden_accuracy_baseline.py
# 2) 회귀 (comparison 유닛 전체)
python -m pytest tests/unit/services/comparison/ -q
# 3) 정책/동결 게이트
python scripts/cad_policy_gate.py
```

## 개별 시나리오

### T1. Aggregate recall ≥ 0.90  → DoD-1
- 실행: `python scripts/measure_golden_accuracy_baseline.py`
- 기대: 마지막 줄 `AGGREGATE ... recall=` 값이 **≥ 0.90** (baseline 0.714).
- 연결 DoD: DoD-1

### T2. fixture 07 (ATTRIB) recall 회복  → DoD-2
- 실행: `python scripts/measure_golden_accuracy_baseline.py` → `07_block_attribute_text_change:` 줄 확인
- 기대: `r=1.000` (현재 `r=0.000 fn=1`). 그 변경이 **modified ATTRIB @ (500,400)** 로 surface.
- 연결 DoD: DoD-2

### T3. fixture 10 (TEXT near-match) recall 회복 + 분할 제거  → DoD-2
- 실행: `python scripts/measure_golden_accuracy_baseline.py` → `10_dimension_text_shifted:` 줄 확인
- 기대: `r=1.000` 그리고 `fp` 가 현재 2 → **0** (add+deleted 분할이 1 modified로 병합).
- 연결 DoD: DoD-2, DoD-4

### T4. fixture 04·11 잔여 FN 해소  → DoD-2
- 실행: `python scripts/measure_golden_accuracy_baseline.py` → `04_added_deleted:` / `11_block_geometry_change:` 줄
- 기대: 두 줄 모두 `fn=0` (진짜 변경 누락 0). 단, 채점 아티팩트로 판명되면 RULES에 따라 truth 미수정 + 사유 기록.
- 연결 DoD: DoD-2

### T5. 분식 금지 가드 — noise_fp=0 & precision 비퇴행  → DoD-3, DoD-4, DoD-7
- 실행: `python scripts/measure_golden_accuracy_baseline.py` → `AGGREGATE` 줄
- 기대: `noise_fp=0` (불가침) **AND** `precision` ≥ 0.556. 01/03/05 각 `fp=0`.
- 연결 DoD: DoD-3, DoD-4, DoD-7

### T6. 회귀 없음 — comparison 유닛 전체 그린  → DoD-5
- 실행: `python -m pytest tests/unit/services/comparison/ -q`
- 기대: 종료코드 0, `failed` 0. 각 소스 수정마다 회귀 테스트 1건 신규 포함.
- 연결 DoD: DoD-5

### T7. 정책/동결 게이트 그린  → DoD-6
- 실행: `python scripts/cad_policy_gate.py`
- 기대: `CAD policy gate passed.` 종료코드 0 (모놀리스 라인-실링·정책 무위반).
- 연결 DoD: DoD-6

## 통과 기준
- [ ] T1~T7 전 시나리오 PASS + 각 명령 출력 요약을 STATUS.md "검증 로그"에 증거로 기록
- [ ] 회귀 없음(기존 테스트 그대로 통과, noise_fp 불변)
