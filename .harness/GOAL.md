# GOAL — 핵심 목표

## 한 줄 정의
Golden 코퍼스(15쌍)에서 **detection recall ≥ 0.90** 을 달성하고 **recall=0 fixture 0건**으로 만든다 — precision·noise·기존 테스트는 비퇴행.

## 배경/맥락
프로젝트 자신의 출시 게이트(`scripts/audit_drawing_compare_release_readiness.py`: recall≥0.90 · precision≥0.85 · 고객증거)의 **AI가 단독으로 닫을 수 있는 절반**이 recall이다. 전체 계획은 [docs/RELEASE_READINESS_PLAN_2026Q3.md](../docs/RELEASE_READINESS_PLAN_2026Q3.md) (이 하네스 = 그 계획의 **Phase 1**).

오늘 실측 baseline (`python scripts/measure_golden_accuracy_baseline.py`, 2026-06-26):
`precision=0.556 recall=0.714 f1=0.625 noise_fp=0`, recall=0 fixture **2건(07, 10)**, FN 총 4건(04·07·10·11).

핵심 발견: 07·10은 "기능 부재"가 아니라 **기존 메커니즘이 발화하지 않는 디버깅 과제**다 —
- 07(ATTRIB text `@100→@200`): ATTRIB 추출은 이미 존재([dxf_entity_extractor.py:102](../src/services/comparison/dxf_entity_extractor.py)) 인데 modified로 surface 안 됨.
- 10(TEXT 30mm shift + `1500→1550`): `text_near_match_radius=50.0` 이미 존재([comparison_config.py:125](../src/services/comparison/comparison_config.py)) 인데 add+deleted로 쪼개짐.

## 검증 가능한 종료조건 (Definition of Done)
> 각 항목은 `scripts/measure_golden_accuracy_baseline.py` 출력 또는 명령으로 객관 확인.

- [ ] DoD-1: golden aggregate **recall ≥ 0.90**  · 검증: 측정 스크립트 `AGGREGATE ... recall=` 값
- [ ] DoD-2: **recall=0 fixture 0건** (07·10 포함 전 fixture r>0)  · 검증: per-pair 표에 `r=0.000` 부재
- [ ] DoD-3: **noise_fp = 0 유지** (01/03/05 순수노이즈 fixture)  · 검증: 측정 스크립트 `noise_fp=` 값
- [ ] DoD-4: **precision ≥ 0.556** (baseline 비퇴행; 가능하면 상승)  · 검증: 측정 스크립트 `precision=` 값
- [ ] DoD-5: 각 소스 수정마다 **회귀 테스트 1건** 추가 + comparison 유닛 전체 그린  · 검증: `pytest tests/unit/services/comparison/ -q`
- [ ] DoD-6: **cad_policy_gate 그린** (모놀리스 비증가·정책 무위반)  · 검증: `python scripts/cad_policy_gate.py`
- [ ] DoD-7: **RULES.md 전 조항 준수** (카운트 분식·forced-content 제거 0건)  · 검증: RULES 대조 + DoD-3 noise_fp

## 범위 밖 (Out of Scope)
- **Phase 0 파일럿(실도면 ground truth 수집)** — 인간 담당, 별도 트랙.
- **precision ≥ 0.85** — Phase 2(near-match 페어링 스파이크, 인간 go/no-go 필요). 본 하네스는 "비퇴행"까지만.
- **AC1032 / AI-KDS 전략 분기** — Phase 3, 인간 결정.
- **새 golden fixture 발명 / truth.json 수정** — 채점 분식 방지. 기존 truth가 정답.
- **모놀리스/뷰어 리팩터·폴리시** — 본 목표 무관.

## 산출물 목록
- 수정된 `src/services/comparison/*.py` (recall 근인 수정)
- 신규 회귀 테스트 (`tests/unit/services/comparison/`)
- 갱신된 `docs/GOLDEN_ACCURACY_BASELINE_REPORT.md` (recall≥0.90 증거)
- 완료 보고: 통과 명령+출력, 변경 파일, DoD 충족표
