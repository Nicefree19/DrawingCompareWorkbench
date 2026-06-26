# TEST_CRITERIA — 검증 시나리오

> ⚠️ 실제 실행 명령 + 기대 결과.

## 단일 진입점
```bash
# 러너를 골든쌍에 (헤드리스)
python scripts/run_pilot_spotcheck.py tests/data/comparison/golden/dxf/02_single_modification/before.dxf tests/data/comparison/golden/dxf/02_single_modification/after.dxf -o build/pilot_demo
# 결정적 테스트 + dogfood lint + gate
python -m pytest tests/unit/scripts/test_run_pilot_spotcheck.py -q
python -m black --check scripts/run_pilot_spotcheck.py tests/unit/scripts/test_run_pilot_spotcheck.py
python -m isort --check-only scripts/run_pilot_spotcheck.py tests/unit/scripts/test_run_pilot_spotcheck.py
python scripts/cad_policy_gate.py
```

## 개별 시나리오

### T1. 러너가 spotcheck 시트 산출 → PE1
- 실행: 단일 진입점 1번 명령(골든 02쌍)
- 기대: `<out>/pilot_spotcheck.md` 존재. 검출 변경 1+ 행(위치·타입·요약) + 운영자 칸 헤더(아는변경/검출Y-N/위치정확Y-N/비고) + 쌍 이름·총 검출수.
- 연결 DoD: PE1

### T2. ground-truth 스켈레톤 → PE2
- 실행: 동일 실행 후 `<out>/review_ground_truth.csv` 확인
- 기대: 헤더 = **기존 review_ground_truth 스키마**(drawing_label,category,summary_contains,source_format,detection_source,bbox_status), 검출 기반 행 존재, 운영자 확인용 칸은 사실/공란.
- 연결 DoD: PE2

### T3. 골든 결정적 → PE3
- 실행: `python -m pytest tests/unit/scripts/test_run_pilot_spotcheck.py -q`
- 기대: 골든 02쌍(단일 수정)서 spotcheck가 그 변경을 나열, csv 스켈레톤에 대응 행. 2회 동일.
- 연결 DoD: PE3

### T4. 운영자 가이드 → PE4
- 실행: `grep -nE "run_pilot_spotcheck|배포 진행|누락" docs/INTERNAL_PILOT_SPOTCHECK.md`
- 기대: 실행 명령 1줄 + 판정 기준(누락 0 → 배포가/부) 존재.
- 연결 DoD: PE4

### T5. dogfood lint + 비퇴행 → PE5
- 실행: `black --check`/`isort --check-only` (신규 .py 2개) + `cad_policy_gate.py` + 워크플로 grep(test_run_pilot_spotcheck 포함)
- 기대: 신규 .py 전부 clean(exit 0); gate `passed`; per-PR 목록에 신규 테스트 포함; 기존 step 보존.
- 연결 DoD: PE5

## 통과 기준
- [x] T1~T5 PASS + 출력 요약을 STATUS "검증 로그"에 증거 기록
- [x] 정답을 지어내지 않음(스켈레톤=사실+공란) STATUS에 확인
