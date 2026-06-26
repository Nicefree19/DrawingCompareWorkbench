# PLAN — 이행 로드맵

> DoD까지의 단계별 경로. 작은 단위·의존성 표기. 한 반복 = 한 단계(fixture). 시간추정 대신 복잡도.

## 단계 (순서대로)

### S1. 측정 하네스 고정 + FN 근인 후보 캡처  (복잡도: 낮)
- 무엇을: baseline 재측정으로 현 상태 핀(recall 0.714). 04·07·10·11 각 pair의 예측 vs truth 덤프(어떤 record가 나오고 어디서 매칭 실패하는지).
- 산출: 4개 fixture의 "예측 record 목록 + truth + 매칭 실패 지점" 메모(STATUS 검증로그).
- 검증: T1, T2(현 실패 재현)
- 의존: 없음

### S2. fixture 07 — ATTRIB text 변경 가시화  (복잡도: 높)
- 무엇을: ATTRIB `@100→@200`가 modified로 surface 안 되는 근인 추적(추출은 됨 → 비교/지문/매칭 중 어디서 누락?). 최소 수정.
- 산출: 07 r=0.000→1.000. 회귀 테스트 1건.
- 검증: T2
- 의존: S1 →

### S3. fixture 10 — TEXT near-match(50mm) 발화  (복잡도: 높)
- 무엇을: DIM 레이어 TEXT의 30mm shift+내용변경이 add+deleted로 쪼개지는 근인 추적(`text_near_match_radius=50.0`가 이 경로에 미적용 의심 — layer/type 게이트). 최소 수정.
- 산출: 10 r=0.000→1.000, fp 2→0. 회귀 테스트 1건.
- 검증: T3
- 의존: S1 →

### S4. fixture 04·11 — 잔여 FN 해소  (복잡도: 중)
- 무엇을: 04(added/deleted 1건 누락)·11(block geometry 1건 누락)의 FN 근인 추적. 진짜 변경 누락만 회복(노이즈 유발 금지).
- 산출: 04·11 recall 0.5→1.0(가능 범위). 회귀 테스트.
- 검증: T4
- 의존: S1 →

### S5. 통합 재측정 + 비퇴행 게이트  (복잡도: 중)
- 무엇을: 전체 재측정. recall≥0.90 / noise_fp=0 / precision≥0.556 / comparison 유닛 전체 / policy gate 동시 충족 확인. 미달 시 S2~S4로 자가치유 복귀.
- 산출: recall≥0.90 증거 로그 + 갱신 baseline 리포트.
- 검증: T1·T5·T6·T7
- 의존: S2, S3, S4 →

## 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| 07/10 수정이 near-match 과확장 → 타 fixture FP↑ | 상 | noise fixture(01/03/05) + precision 비퇴행을 매 반복 게이트로. 초과 시 수정 범위 축소(layer/type 제한 매칭). |
| FN 근인이 truth 채점 아티팩트(진짜 변경 아님) | 중 | truth.json 재확인 — 채점 버그면 truth 안 고치고 측정 adapter 검토, 분식 금지. |
| recall↑가 precision↓ 동반(분할 더 발생) | 중 | modified 복원형 수정 우선(add+deleted→modified는 recall·precision 동시 개선). |
| 동일 실패 3회 | 상 | 가정 재검토 후 사람에게 블록 보고(무한 재시도 금지). |

## 변경 이력 (루프 중 PLAN 수정 시 추가)
- 2026-06-26 / 생성: 초기 계획. 입력 = docs/RELEASE_READINESS_PLAN_2026Q3.md Phase 1.
