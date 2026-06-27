# TEST_CRITERIA — 검증 시나리오

> ⚠️ 실제 실행 명령 + 기대 결과. 외부 신호(종료코드·measure 리포트·단언)만 통과 증거.

## 단일 진입점
```bash
# synonym 단위테스트
python -m pytest tests/unit/services/comparison/test_accuracy_metrics.py -q
# 전체 골든 measure (07 회복 + only-07 + noise_fp=0)
python scripts/measure_golden_accuracy_baseline.py --out-json build/reports/golden-accuracy.json --out-md build/reports/golden-accuracy.md --max-noise-fp 0 --min-precision 0.50 --min-recall 0.68
# dogfood + 정책
python -m black --check <수정/신규 .py>
python -m isort --check-only <수정/신규 .py>
python scripts/cad_policy_gate.py
```

## 개별 시나리오

### T-SC1. synonym 매칭 → SC1
- 실행: synonym 단위테스트
- 기대: `entity_type="block_reference"` 예측이 `entity_type="attrib"` truth와 **동일 위치서 매칭**(TP). 무관 타입(line vs attrib)은 비매칭. 위치 먼 경우 비매칭(거리 게이트 병존).
- 연결 DoD: SC1

### T-SC2. 07 recall 회복 → SC2
- 실행: 전체 골든 measure
- 기대: `07_block_attribute_text_change` per-pair `r=1.000`(이전 0.000). aggregate recall 상승(≈0.786→~0.86).
- 연결 DoD: SC2

### T-SC3. only-07 + noise 무회귀 → SC3
- 실행: measure 전/후 per-pair 리포트 비교
- 기대: 07 외 14개 fixture의 tp/fp/fn **불변**, `noise_fp=0` 유지. 변화는 07 한 줄.
- 연결 DoD: SC3

### T-SC4. 결정적 → SC4
- 실행: `pytest test_accuracy_metrics.py`(+골든 07 회복 단언 테스트)
- 기대: 2회 동일 PASS.
- 연결 DoD: SC4

### T-SC5. dogfood + 정책 + floor → SC5
- 실행: black/isort --check + `cad_policy_gate` + measure floor + 기존 정확도 테스트
- 기대: 신규/수정 .py clean; gate `passed`; floor exit 0(noise_fp 0·p≥0.50·r≥0.68); 기존 테스트 통과; per-PR 목록 갱신.
- 연결 DoD: SC5

## 통과 기준
- [x] T-SC1~SC5 PASS + 출력 요약을 STATUS "검증 로그"에 증거 기록
- [x] 엔진 실검출 입증·검출 무변경·only-07 변화 STATUS에 확인
