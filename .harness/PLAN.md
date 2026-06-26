# PLAN — 이행 로드맵

> 작은 단위·의존성. 한 반복 = 한 단계. verify-then-fix-or-drop.

## 단계 (순서대로)

### S1. changed-files lint 메커니즘 설계·로컬 검증 (선결)  (복잡도: 중)
- 무엇을: GH Actions `pull_request`서 변경 .py 목록 추출(`git diff --name-only origin/${{ github.base_ref }}...HEAD` → `*.py` 필터) → `black --check` + `isort --check-only` 그 목록만. push-only 트리거엔 skip(base 없음). 로컬서 동일 로직 재현(깨끗한 변경 pass·망친 변경 fail).
- 산출: 검증된 셸 스니펫.
- 검증: T1
- 의존: 없음

### S2. CI 워크플로에 lint step 추가 (L1)  (복잡도: 낮)
- 무엇을: cad-format-regression.yml에 `Lint changed Python files` step(`if: github.event_name == 'pull_request'`). 기존 step 보존.
- 산출: 워크플로 diff.
- 검증: T1, T4(YAML)
- 의존: S1 →

### S3. mypy 설정 정정 (L2)  (복잡도: 낮)
- 무엇을: `pyproject.toml`의 `[[tool.mypy.overrides]]`서 실존하지 않는 모듈(src.core.parsers.*·src.core.validators.* 등) 제거 — 잔재 정리. (게이팅은 안 함.)
- 산출: pyproject diff + 잔재 0 확인.
- 검증: T2
- 의존: 없음 (S1~S2와 독립)

### S4. meta-guard (L3)  (복잡도: 낮)
- 무엇을: lint step 존재를 `check_ci_gate` required_snippets에 추가(예: "black --check") 또는 전용 테스트. 제거 시 violation.
- 산출: 게이트/테스트 + 누락-시뮬 케이스.
- 검증: T3
- 의존: S2 →

### S5. 통합 비퇴행 (L4)  (복잡도: 낮)
- 무엇을: 기존 step(테스트·golden·diff-check·policy) 보존·cad_policy_gate 그린·YAML 유효 확인.
- 검증: T4
- 의존: S2~S4 →

## 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| changed-files diff 로직이 CI서 오작동(base ref/rename/삭제) | 상 | S1 로컬 정밀 검증 + 삭제파일 제외(`--diff-filter=d`). pull_request만. |
| black/isort 버전 불일치로 CI vs 로컬 결과 다름 | 중 | requirements-dev 핀 버전 사용(black 23.12·isort 5.12). CI도 동일 설치. |
| 변경파일이 백로그 파일(이미 미포맷)일 때 PR이 reformat 강제→큰 diff | 중 | 의도된 동작(만지면 정리). 단 모놀리스 변경 시 라인-실링 동시 주의 — 사유 기록. |
| mypy override 제거가 의도된 strict 의도 깸 | 하 | 해당 모듈 부재 입증(MISSING) → 死설정이라 무영향. |

## 변경 이력
- 2026-06-26 생성: BDC-2. 백로그 실측(black160/isort112)으로 전체게이트→changed-files 범위 축소. mypy=잔재정리만.
