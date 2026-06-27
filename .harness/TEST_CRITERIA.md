# TEST_CRITERIA — 검증 시나리오

> ⚠️ 실제 실행 명령 + 기대 결과. 외부 신호(종료코드·파일·단언·헤더)만 통과 증거.

## 단일 진입점
```bash
# 실-ODA e2e 포함 전체 (로컬 ODA 설치 시 비-skip)
python -m pytest tests/unit/scripts/test_run_pilot_spotcheck.py -q
# fixture 헤더/크기
python -c "import pathlib; p=pathlib.Path('tests/data/comparison/golden/dxf/02_single_modification/dwg/before.dwg'); b=p.read_bytes(); print('AC1032' if b[:6]==b'AC1032' else b[:6], len(b))"
# dogfood + 정책 + 골든 floor
python -m black --check scripts/run_pilot_spotcheck.py tests/unit/scripts/test_run_pilot_spotcheck.py
python -m isort --check-only scripts/run_pilot_spotcheck.py tests/unit/scripts/test_run_pilot_spotcheck.py
python scripts/cad_policy_gate.py
python scripts/measure_golden_accuracy_baseline.py --out-json build/reports/golden-accuracy.json --max-noise-fp 0 --min-precision 0.50 --min-recall 0.68
```

## 개별 시나리오

### T-DF1. 폴더-DWG 변환 수정 → DF1
- 실행: 실 AC1032 DWG **폴더** 쌍으로 `run_pilot_spotcheck`(e2e 테스트 내, 로컬 ODA)
- 기대: 변환 발화→compare 정상→`detected_count ≥ 1`, BEAM 변경 surface. (수정 전: preflight AC1032 실패)
- 연결 DoD: DF1

### T-DF2. 커밋 fixture → DF2
- 실행: 단일 진입점의 fixture 헤더/크기 명령
- 기대: `before.dwg`/`after.dwg` 존재, 헤더 `AC1032`, <100KB/파일.
- 연결 DoD: DF2

### T-DF3. 실-ODA e2e → DF3
- 실행: `pytest test_run_pilot_spotcheck.py`(로컬 ODA 설치 → 비-skip)
- 기대: 단일 DWG 쌍 + DWG 폴더 e2e 둘 다 PASS(skip 아님), BEAM 검출 단언. STATUS에 "비-skip 실행" 증거.
- 연결 DoD: DF3

### T-DF4. 단일 DWG 비퇴행 + mock 보존 → DF4
- 실행: 동일 pytest
- 기대: 기존 mock 단위테스트(`_resolve_dwg_backend_mode` 등) 전량 통과. 단일 DWG 경로 불변.
- 연결 DoD: DF4

### T-DF5. dogfood + 정책 + 골든 floor → DF5
- 실행: black/isort --check + `cad_policy_gate` + `measure_golden_accuracy_baseline`(floor) + per-PR grep
- 기대: 신규/수정 .py clean; gate `passed`; 골든 floor 통과(noise_fp 0·p≥0.50·r≥0.68 유지); per-PR 목록에 e2e 포함.
- 연결 DoD: DF5

## 통과 기준
- [x] T-DF1~DF5 PASS + 출력 요약을 STATUS "검증 로그"에 증거 기록
- [x] 변환 재구현 0·공유경로 비퇴행·실 DWG 비-skip 실행 STATUS에 확인
