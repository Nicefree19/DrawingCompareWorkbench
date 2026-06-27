# GOAL — 핵심 목표

## 한 줄 정의
**골든 채점기의 `entity_type` 어휘 갭을 메워, 이미 올바르게 검출된 07(블록 속성값) 변경이 FN으로 오판되던 것을 TP로 바로잡는다** — 측정 recall을 정직하게 0.786→~0.86으로. 검출 로직은 무변경(분식 아님: 이미 검출되는 걸 올바로 채점).

## 배경/맥락
멀티에이전트 검증 결과(반증된 가설 포함): 골든 `07_block_attribute_text_change`(recall=0)는 **검출 결함이 아니다**. compare 엔진은 블록 내부 ATTRIB 값 변경(@100→@200)을 정확히 검출 — `entity_type="block_reference"`, 위치 (500,400) 정확(`drawing_compare_engine.py` `_attribute_diffs`/`_compare_block_reference`).

진짜 근인 = **채점 어휘 불일치**: `truth.json`은 `entity_type:"ATTRIB"`, 예측은 `"block_reference"` → `accuracy_metrics.py:287-289` `match_changes_to_truth`의 **정확-문자열 필터**가 거리 0의 올바른 후보를 거부 → FN. 측정 스크립트 주석이 이미 명명함("ATTRIB vs block_reference vocabulary gap"). [[attrib-recall-compare-surfacing]]

**왜 정직한가**: 엔진이 같은 위치에 실제로 검출한 변경을, 타입 이름이 다르다는 이유만으로 놓친 것. 블록 속성 변경은 의미상 block_reference 변경의 하위다. 채점기의 정확도를 고치는 것이지 제품 메트릭을 부풀리는 게 아니다(검출 로직 0 변경). **단 honesty linchpin은 S1에서 엔진이 07을 실제로 (500,400)에 검출함을 실측 확인 후 진행**.

## 검증 가능한 종료조건 (DoD)
- [x] **SC1 (synonym 수정)**: `entity_type` 매칭이 블록-속성 family `{attrib, attdef, insert, block_reference, block}`를 호환으로 취급. 채점 레이어(accuracy_metrics 또는 measure 정규화)에 한정, 무차별 loosening 금지. · 검증: 단위테스트 — block_reference 예측이 attrib truth와 (동일 위치서) 매칭
- [x] **SC2 (07 recall 회복)**: 골든 07 recall 0→1(측정), aggregate recall 상승. · 검증: `measure_golden_accuracy_baseline`에서 07 `r=1.000`
- [x] **SC3 (과매칭/노이즈 무회귀)**: noise_fp 0 유지, **07 외 fixture의 tp/fp/fn 불변**(only-07 변화). · 검증: 전체 골든 measure 전/후 diff = 07만
- [x] **SC4 (결정적 테스트)**: synonym 매칭 단위테스트 + 골든 07 회복 단언. · 검증: pytest 2회 동일
- [x] **SC5 (비퇴행+dogfood+gate)**: 기존 정확도 테스트 통과, 신규/수정 .py black/isort clean, `cad_policy_gate` 그린, CI 골든 floor 통과(필요시 recall floor 상향 고려), per-PR 목록. · 검증: black/isort + gate + measure floor + 기존 테스트

## 범위 밖
- **검출/매칭 엔진 로직 변경**(이미 올바로 검출 — 건드리지 않음).
- truth.json 변경(truth=사용자 의도, 그대로).
- 무차별 entity_type 필터 완화(과매칭 위험).
- 04/10/11 등 다른 recall 실패(별개 근인, 데드엔드 인접 [[accuracy_forced_content_deadend]]).
- 실제 dry-run.

## 산출물
- `src/services/comparison/accuracy_metrics.py`(또는 measure 정규화) synonym 수정
- 단위테스트(synonym 매칭) + 골든 07 회복 검증
- 워크플로 per-PR 목록(필요시 floor 상향) + 완료 보고
