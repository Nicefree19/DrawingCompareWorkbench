# RULES — 핵심 제약 (경량화)

> ⚠️ 이 목표(fail-loud reliability)에 직결되는 조항만. 충돌 시 우선순위 명시.

## 절대 규칙 (위배 시 작업 중단)
- **verify-then-fix-OR-DROP**: 각 타겟은 ①에서 현재 코드로 근인 재검증. **약하거나 이미 가드돼 있으면 고치지 말고 드롭**(R1/R2/G2/G4 전례 — 감사 에이전트는 과장한다). 없는 문제를 만들지 말 것.
- **모놀리스 라인-실링 비증가**: `src/gui/drawing_compare_workbench.py`(ceiling 13,482)·`lightweight_viewport.py`(2,106)에 **순증가 금지**. D3/D4는 기존 log-only 라인 **대체** 또는 satellite 추출(net-neutral). 매 반복 `cad_policy_gate.py` 확인. (이 게이트는 직전 세션서 추가됨 — 자기 규칙 위반 금지.)
- **숫자 분식 금지**: 변경 카운트/정확도 메트릭을 손대지 않는다(검증된 dead-end). 본 목표는 *가시성/게이팅*이지 정확도 아님.

## 설계/코드 제약
- silent 금지: 사용자가 알아야 할 실패는 status/badge/dialog로, 빌드가 알아야 할 실패는 nonzero로. 단 **친절·행동가능 메시지**(원시 스택 덤프 노출 금지).
- D1 게이트는 기존 `runtime_modules`(release_environment_check) REQUIRED 정의 재사용 — 새 정의 발명 금지.
- 한 반복 = 한 타겟(추적성). 여러 D 몰아치기 금지.
- 기존 패턴·임포트 스타일 준수. 묵음 except 추가 금지.

## 우선순위 (충돌 시)
1. 정직성(없는 문제 안 만듦·분식 금지) > 2. 안정성(fail-loud) > 3. 동결 준수(라인-실링) > 4. 단순성 > 5. 편의

## 검증 연결
- 라인-실링·분식-없음 = TEST **T5**(cad_policy_gate + golden noise 불변 확인).
- 비퇴행 = **T6**.
