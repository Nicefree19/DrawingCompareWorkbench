# TEST_CRITERIA — 검증 시나리오

> ⚠️ 실제 실행 명령 + 기대 결과. 외부 신호(종료코드·파일·단언)만 통과 증거.

## 단일 진입점
```bash
# 폴더 배치 (골든 다중쌍 폴더 — S1서 확정한 fixture 경로)
python scripts/run_pilot_spotcheck.py <golden_before_dir> <golden_after_dir> -o build/pilot_batch
# 결정적 테스트 + dogfood lint + gate
python -m pytest tests/unit/scripts/test_run_pilot_spotcheck.py -q
python -m black --check scripts/run_pilot_spotcheck.py tests/unit/scripts/test_run_pilot_spotcheck.py
python -m isort --check-only scripts/run_pilot_spotcheck.py tests/unit/scripts/test_run_pilot_spotcheck.py
python scripts/cad_policy_gate.py
```

## 개별 시나리오

### T-PB1. 폴더 배치 → PB1
- 실행: 골든 다중쌍 폴더로 러너 실행(단일 진입점 1번)
- 기대: `<out>/pilot_spotcheck.md`가 **2쌍 이상** 구분(쌍 헤더/행에 쌍 이름), 각 쌍 검출수 표기. `review_ground_truth.csv`에 drawing_label로 쌍 구분 행.
- 연결 DoD: PB1

### T-PB2. DWG 온램프 → PB2
- 실행: `.dwg` 입력으로 러너 호출(헤드리스 단위테스트; 변환 경로 mock/감지)
- 기대: DWG 입력이 **기존 변환 경로로 라우팅**됨을 단언. ODA/변환 불가 시 **명확한 에러 + 사전변환 안내**(침묵 빈결과/단일파일 폴백 아님).
- 연결 DoD: PB2

### T-PB3. 단일쌍 비퇴행 → PB3
- 실행: 기존 단일 DXF 쌍 테스트(PR#56 케이스)
- 기대: spotcheck/csv 산출 불변, 전량 통과.
- 연결 DoD: PB3

### T-PB4. 결정적 → PB4
- 실행: `python -m pytest tests/unit/scripts/test_run_pilot_spotcheck.py -q`
- 기대: 폴더 배치 + DWG 라우팅 테스트 결정적 통과(2회 동일).
- 연결 DoD: PB4

### T-PB5. dogfood + 정책 + 가이드 → PB5
- 실행: `black --check`/`isort --check-only` (신규/수정 .py) + `cad_policy_gate.py` + grep 가이드/워크플로
- 기대: 신규/수정 .py 전부 clean(exit 0); gate `passed`(DWG "완전지원" 문구 0); 가이드에 폴더/DWG 사용법; per-PR 목록에 `test_run_pilot_spotcheck` 유지.
- 연결 DoD: PB5

## 통과 기준
- [x] T-PB1~PB5 PASS + 출력 요약을 STATUS "검증 로그"에 증거 기록
- [x] 재구현 0·침묵 다운그레이드 0·정책 그린 STATUS에 확인
