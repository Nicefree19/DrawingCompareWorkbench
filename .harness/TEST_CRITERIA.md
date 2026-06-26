# TEST_CRITERIA — 검증 시나리오

> ⚠️ 실제 실행 명령 + 기대 결과.

## 단일 진입점
```bash
# 도구(핀 버전)
python -m black --version && python -m isort --vn
# changed-files lint 로직 로컬 재현 (예: main 대비 변경 .py)
git diff --name-only --diff-filter=d origin/main...HEAD -- "*.py" | tr '\n' ' '
# 메타-가드 + 비퇴행
python -m pytest tests/unit/scripts/test_cad_policy_gate.py -q
python scripts/cad_policy_gate.py
python -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('pyproject OK')"
python -c "import yaml; yaml.safe_load(open('.github/workflows/cad-format-regression.yml',encoding='utf-8')); print('YAML OK')"
```

## 개별 시나리오

### T1. changed-files lint 게이트 동작 → L1
- 실행(로컬 재현): 변경 .py 목록 추출 → `black --check`/`isort --check-only` 그 목록만. 깨끗한 변경 1개 + 일부러 망친 변경 1개로 양방향 확인.
- 기대: 깨끗→exit 0, 망침→exit≠0(reformat 필요 보고). 워크플로엔 `pull_request` 스코프 lint step 존재.
- 연결 DoD: L1

### T2. mypy 설정 잔재 정리 → L2
- 실행: `grep -E "src.core.parsers|src.core.validators" pyproject.toml` + `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"`
- 기대: 존재하지 않는 모듈 override **0건**, pyproject 파싱 OK.
- 연결 DoD: L2

### T3. meta-guard → L3
- 실행: `python -m pytest tests/unit/scripts/test_cad_policy_gate.py -q` (신규/확장 케이스: 워크플로서 lint step 제거 시뮬 → violation) 또는 전용 테스트
- 기대: lint step 없으면 violation/fail, 있으면 통과.
- 연결 DoD: L3

### T4. 비퇴행·기존 보존 → L4
- 실행: `python scripts/cad_policy_gate.py` + `grep -E "measure_golden_accuracy_baseline|git diff --check|cad_policy_gate.py|test_change_zones.py" .github/workflows/cad-format-regression.yml` + YAML 파싱
- 기대: gate `passed`; 기존 테스트목록·golden·diff-check·policy step 모두 잔존; YAML 유효.
- 연결 DoD: L4

## 통과 기준
- [ ] T1~T4 PASS + 출력 요약을 STATUS "검증 로그"에 증거 기록
- [ ] changed-files 범위/보류(mypy 게이팅)는 STATUS에 명시(silent 금지)
