# GOAL — 핵심 목표

## 한 줄 정의
**lint/format가 PR 변경 .py에 대해 CI 게이트**되게 한다 — 새 스타일 드리프트 차단, 기존 백로그(black 160·isort 112)는 비차단, mypy 설정 잔재 정리.

## 배경/맥락
BDC-2(tech-debt audit): black/isort/mypy가 설치·설정됐으나 **CI 실행 0** (.github/workflows에 lint step 없음). CI-enforcement 캡스톤(테스트 게이트)의 **품질-인프라 짝** — 테스트는 게이트되나 스타일/타입은 미강제.

**백로그 실측(2026-06-26)**: `black --check src/` = 160 파일, `isort --check-only src/` = 112 파일. → **전체 일괄 게이트 불가**(red-wall + 160파일 reformat은 모놀리스 freeze 라인-실링 충돌 + 거대 diff). 정직한 범위 = **changed-files-only**(만지는 파일만 깨끗하게).

**mypy 잔재**: `pyproject [[tool.mypy.overrides]]`가 `src.core.parsers.unified_mgt_parser`·`src.core.validators.*` 등 **이 standalone 레포에 없는 모듈** 참조(monorepo 잔재, 실측 MISSING). → 정리 필요.

## 검증 가능한 종료조건 (DoD)
- [ ] **L1 (changed-files lint 게이트)**: CI가 PR 변경 .py에 `black --check` + `isort --check-only` 실행(전체 아님), `pull_request` 스코프. · 검증: 깨끗한 diff pass, 일부러 망친 변경파일 fail(로컬 재현)
- [ ] **L2 (mypy 설정 정정)**: 존재하지 않는 모듈 override 제거(또는 실존 clean 모듈로 교체). · 검증: `python -m mypy --version` + 설정 파싱 OK + 잔재 override 0
- [ ] **L3 (meta-guard)**: lint step이 워크플로에 존재함을 단언(silent-inert 방지) — `cad_policy_gate.check_ci_gate` 확장 or 전용 테스트. · 검증: lint step 제거 시뮬 → violation/fail
- [ ] **L4 (no regression)**: 기존 step(테스트·golden·diff-check·policy) 보존 · `cad_policy_gate` 그린 · 워크플로 YAML 유효. · 검증: grep + gate

## 범위 밖
- **전체 코드베이스 일괄 reformat**(160/112 churn, 모놀리스 freeze 충돌) — changed-files만.
- **mypy 게이팅**(설정 정정만; 게이트는 설정 안정화 후 별도).
- pylint · pre-commit 훅(선택, 별도).
- workflow_dispatch full-suite.

## 산출물
- `.github/workflows/cad-format-regression.yml` (changed-files lint step)
- `pyproject.toml` (mypy override 정정)
- `scripts/cad_policy_gate.py` + test (메타-가드, 해당 시)
- 완료 보고(워크플로 diff·lint 동작·DoD 충족표)
