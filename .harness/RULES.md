# RULES — 핵심 제약 (경량화)

> ⚠️ 이 목표(recall 회복)에 직결되는 조항만. 전체 매뉴얼 금지. 충돌 시 우선순위 명시.

## 절대 규칙 (위배 시 작업 중단)
- **카운트 분식 금지**: 전역 병합·임계값 완화로 메트릭을 올리지 않는다. 검증된 dead-end(정렬 이미 0.022mm, 341km consolidation 분식 전례). recall은 "놓친 진짜 변경을 보이게"로만 올린다.
- **forced-content fallback(ACC-1/ACC-4) 제거 금지**: load-bearing(radius 등 uncategorized 실변경 검출), 회귀가드 존재.
- **noise_fp 악화 금지**: 01/03/05 순수노이즈 fixture의 FP는 0으로 유지. 어떤 수정도 이걸 깨면 롤백.
- **truth.json·golden fixture 수정 금지**: 채점 대상을 바꾸면 분식. 기존 truth가 정답.

## 설계/코드 제약
- near-match 확장은 **기하+근접 근거로만**(layer/type 무분별 완화 금지). 매칭 반경은 fixture가 선언한 tolerance 존중.
- 묵음 실패 금지 — try/except 폴백이 진짜 신호를 삼키지 않게(이 프로젝트 silent-fallback 고질병).
- 기존 패턴·임포트 스타일 준수. **모놀리스(drawing_compare_workbench.py)에 로직 추가 금지** — 라인-실링 게이트가 차단.
- 한 반복 = 한 fixture 수정(추적성). 여러 fixture 몰아치기 금지.

## 도메인 규격 (해당 시)
- golden truth 매칭: 측정 스크립트가 entity_type을 **case-fold**(truth 대문자 ↔ canonical 소문자) — 타입 비교 시 주의.
- text 변경: 좌표 shift + 내용 변경 동시 케이스는 near-match radius 내에서 **modified**로 복원(add+deleted 분할 아님).

## 우선순위 (충돌 시)
1. 정확성(놓친 진짜 변경 회복) > 2. 정직성(분식 금지·noise 불변) > 3. 단순성(최소 수정) > 4. 성능 > 5. 편의

## 검증 연결
- 분식 금지·noise 불변 = TEST_CRITERIA **T5**.
- 비퇴행(기존 테스트·정책) = **T6·T7**.
