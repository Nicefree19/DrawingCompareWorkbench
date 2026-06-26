# RULES — 핵심 제약 (경량화)

> ⚠️ 이 목표(lint/type CI)에 직결되는 조항만.

## 절대 규칙 (위배 시 작업 중단)
- **백로그 일괄 reformat 금지**: 160 black + 112 isort 백로그를 한 번에 고치지 않는다(거대 diff·모놀리스 freeze 라인-실링 충돌). changed-files-only.
- **모놀리스 라인-실링 비증가**: black/isort가 `drawing_compare_workbench.py`(13,467)/`lightweight_viewport.py`를 reformat해 라인 증가 시 `cad_policy_gate` trip → 이 작업서 그 파일들 reformat하지 않는다(건드리면 별도 처리).
- **silent 금지**: lint step은 **gating**(continue-on-error 아님). 단 changed-files 범위는 명시(전체인 척 금지).
- **새 P5-G* 게이트 금지**: check_ci_gate 확장은 기존 패턴. 새 워크플로/게이트 스크립트 신설 금지.

## 설계/코드 제약
- 도구 버전은 requirements-dev 핀(black 23.12·isort 5.12) — CI/로컬 일치.
- 워크플로 편집은 기존 형식·기존 step 보존(PowerShell backtick).
- mypy는 **설정 정정만**(死 override 제거), 게이팅 안 함(범위 밖).
- 한 반복 = 한 단계.

## 우선순위 (충돌 시)
1. 정직성(범위 명시·死설정 정리) > 2. 비파괴(백로그 비차단·freeze 준수) > 3. 강제(changed-files gating) > 4. 커버리지 > 5. 편의

## 검증 연결
- 범위/비파괴 = T1(changed-files만). freeze = `cad_policy_gate`(T4).
- meta-guard = T3.
