# TEST_CRITERIA — 검증 시나리오

> ⚠️ 실제 실행 명령 + 기대 결과. 외부 신호(파일·단언·소스)만 통과 증거.

## 단일 진입점
```bash
# emission 함수 + GUI 배선 테스트
python -m pytest tests/unit/scripts/test_run_pilot_spotcheck.py tests/unit/gui/test_gui_spotcheck_emission.py -q -o log_cli=false
# dogfood + 정책
python -m black --check scripts/run_pilot_spotcheck.py src/gui/drawing_compare_workbench.py tests/unit/gui/test_gui_spotcheck_emission.py
python -m isort --check-only scripts/run_pilot_spotcheck.py tests/unit/gui/test_gui_spotcheck_emission.py
python scripts/cad_policy_gate.py
# anti-theater 가드 grep
grep -nE "pilot_spotcheck|작성|반송|OPEN" docs/INTERNAL_PILOT_GUIDE.md
grep -rn "build_customer_pilot_" docs/release/   # 정리 후 0 또는 실 producer로
```

## 개별 시나리오

### T-GS1. emission 함수 → GS1
- 실행: 합성 `<tmp>/artifacts/review_dashboard.json`(top_issues 1행) 만들고 `emit_spotcheck_artifacts(<tmp>)` 호출
- 기대: `<tmp>/pilot_spotcheck.md`(검출행) + `review_ground_truth.csv`(기존 스키마) 산출.
- 연결 DoD: GS1

### T-GS2. GUI 발화 증명 → GS2
- 실행: (방법A) offscreen GUI 핸들러 `_on_auto_finished_v2`에 합성 result 주입→구동 후 output_dir/pilot_spotcheck.md 존재 단언. (방법B 폴백) `inspect.getsource(_on_auto_finished_v2)`가 `emit_spotcheck_artifacts` 호출 포함 단언.
- 기대: GUI 경로가 시트를 **실제 발화**(or 호출 배선 단언). fail-safe(예외 시 비교 흐름 무손상)도 단언.
- 연결 DoD: GS2

### T-GS3. 러너 비퇴행 → GS3
- 실행: `pytest test_run_pilot_spotcheck.py`(기존 14)
- 기대: 추출 리팩터 후 전량 통과, 산출 불변.
- 연결 DoD: GS3

### T-GS4. anti-theater 가드 → GS4
- 실행: 단일진입점의 grep
- 기대: INTERNAL_PILOT_GUIDE에 pilot_spotcheck 행 + "작성·반송" 지시 + 인간 P0 OPEN 명시. `build_customer_pilot_*` vapor 참조 0(or 실 producer).
- 연결 DoD: GS4

### T-GS5. dogfood + 정책 → GS5
- 실행: black/isort --check + `cad_policy_gate` + per-PR grep
- 기대: 신규/수정 .py clean; gate `passed`; per-PR 목록에 GUI emission 테스트.
- 연결 DoD: GS5

## 통과 기준
- [x] T-GS1~GS5 PASS + 출력 요약을 STATUS "검증 로그"에 증거 기록
- [x] GUI 발화 증명(unfired 아님)·fail-safe·인간 P0 OPEN STATUS에 확인
