# RULES — 핵심 제약 (경량화)

> ⚠️ 이 목표(CI enforcement)에 직결되는 조항만.

## 절대 규칙 (위배 시 작업 중단)
- **silent-skip 금지**: 테스트를 게이트서 빼거나 격리할 때 **반드시 명시 + 사유 기록**. `continue-on-error`/조용한 xfail로 실패를 숨기지 않는다. (이 목표 자체가 silent-inert 제거 — 자기모순 금지.)
- **결정성 우선**: per-PR 게이트에는 **결정적·빠른** 테스트만. flaky하면 추가하지 말고 격리+사유(verify-then-DROP).
- **새 P5-G* 게이트 금지**([[gate_inflation_risk]]): `check_ci_gate` 확장은 **기존 함수·기존 required_snippets 패턴**의 강화이지 신규 audit 게이트가 아니다. 새 워크플로 파일·새 게이트 스크립트 만들지 말 것.
- **분식 금지**: 정확도/카운트 메트릭 손대지 않는다(이건 CI 배선 작업).

## 설계/코드 제약
- 워크플로 편집은 기존 형식 유지(Windows PowerShell backtick 줄연속). 기존 step(golden floor·diff-check·policy gate) **보존**.
- check_ci_gate 확장은 `required_snippets` 딕셔너리에 항목 추가 수준으로 최소.
- 한 반복 = 한 단계. 모놀리스 무관(이 작업은 CI/스크립트 층).

## 우선순위 (충돌 시)
1. 정직성(silent-skip 금지·없는 강제 안 만듦) > 2. 결정성(flaky 배제) > 3. 커버리지(많이 게이트) > 4. CI 속도 > 5. 편의

## 검증 연결
- silent-skip 없음·기존 보존 = TEST **T3·T5**.
- 메타-가드 동작 = **T2**.
