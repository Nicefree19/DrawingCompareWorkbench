# PLAN — 이행 로드맵

> 작은 단위·의존성. 한 반복 = 한 단계. verify-then-fix-or-drop.

## 단계 (순서대로)

### S1. Honesty linchpin + 정확한 수정 지점 실측 (선결)  (복잡도: 낮)
- 무엇을: ① **honesty 확인** — 골든 07에 실 measure/compare 1회 실행, 예측 산출을 덤프해 엔진이 (500,400)에 변경을 실제 검출(`entity_type=block_reference`, 거리≈0)하고 채점기가 **오직 entity_type 불일치로** 거부함을 실측 확인. (검출 안 하면 = 분식이므로 **중단**.) ② 정확한 필터 코드(`accuracy_metrics.py` match_changes_to_truth)와 정규화(`measure_golden_accuracy_baseline.py`)의 entity_type 처리 줄 확인. ③ 현재 07 r=0 재현(red).
- 산출: 예측 덤프(검출 입증) + 필터 위치 + red.
- 검증: 실측(엔진 검출 입증)
- 의존: 없음

### S2. synonym 수정 (SC1)  (복잡도: 낮)
- 무엇을: entity_type 비교에 블록-속성 family synonym 세트 도입. 같은 family면 호환. 다른 타입(line/circle/text/dimension)은 불변. 채점 레이어 한정.
- 산출: 수정.
- 검증: T-SC1
- 의존: S1 →

### S3. 단위테스트 (SC4)  (복잡도: 낮)
- 무엇을: synonym 매칭 단위테스트(block_reference 예측 + attrib truth 동일위치 → 매칭; 다른 family는 비매칭; 위치 멀면 비매칭). black/isort clean. per-PR 목록 추가.
- 산출: 테스트.
- 검증: T-SC4
- 의존: S2 →

### S4. 전체 골든 measure 전/후 diff (SC2/SC3)  (복잡도: 중)
- 무엇을: 전체 골든 measure 실행 → 07 `r=1.000`, aggregate recall 상승, **noise_fp=0**, 07 외 fixture per-pair tp/fp/fn **불변**(전/후 비교).
- 산출: measure 리포트 전/후.
- 검증: T-SC2, T-SC3
- 의존: S2 →

### S5. 비퇴행 + dogfood + floor (SC5)  (복잡도: 낮)
- 무엇을: 기존 정확도 단위테스트 통과, `cad_policy_gate` 그린, dogfood lint, CI 골든 floor 통과 확인. recall floor 상향은 **선택**(보수적으로 보류 가능 — 다른 fixture 변동 대비).
- 검증: T-SC5
- 의존: S2~S4 →

## 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| 엔진이 07을 실제 검출 안 함(=분식) | 치명 | **S1서 예측 덤프로 검출 입증 못 하면 즉시 중단·방향 재고**. |
| synonym이 과매칭(다른 fixture 오TP) | 상 | family 세트만(블록계열). S4서 07 외 per-pair 불변 증명. 위치 거리 게이트 병존. |
| noise_fp 회귀 | 상 | noise fixture는 expected=0 → recall측 FN→TP 수정은 FP 무생성(구조적). S4서 noise_fp=0 확인. |
| floor 상향이 다른 fixture 변동에 취약 | 중 | floor는 보수적 유지(상향 보류). 측정 개선은 리포트로 표시. |
| 기존 accuracy 테스트가 정확-문자열 가정 | 중 | S5서 기존 테스트 실행. 깨지면 synonym 의도 반영하도록 보수 갱신(약화 아님). |

## 변경 이력
- 2026-06-27 생성: 멀티에이전트가 07 근인을 검출결함→채점어휘갭으로 정정. 채점기 synonym 수정(검출 무변경). honesty linchpin=S1 실측.
