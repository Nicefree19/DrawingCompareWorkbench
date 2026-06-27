# TEST_CRITERIA — 검증 시나리오

> ⚠️ 실제 실행 명령 + 기대 결과. 외부 신호(파일·종료코드·단언)만 통과 증거.

## 단일 진입점
```bash
# 결정적 테스트 (stub app-dir)
python -m pytest tests/unit/scripts/test_build_pilot_packet.py -q
# dogfood + 정책
python -m black --check scripts/build_pilot_packet.py tests/unit/scripts/test_build_pilot_packet.py
python -m isort --check-only scripts/build_pilot_packet.py tests/unit/scripts/test_build_pilot_packet.py
python scripts/cad_policy_gate.py
# 가이드/리다이렉트 grep
grep -nE "pilot_spotcheck|작성|반송" docs/pilot_packet/사용가이드.md
grep -nE "build_pilot_packet" docs/release/CUSTOMER_PILOT_BATCH_WINDOWS_LIMITED_RELEASE.md
```

## 개별 시나리오

### T-PK1. 조립 스크립트 → PK1
- 실행: stub app-dir(가짜 exe 1개) 만들고 `build_pilot_packet(app_dir, out, version)` 호출
- 기대: `<out>/<packet>/`에 `DrawingCompare_실행.bat`·`사용가이드.md`·`app/DrawingCompareWorkbench/`(복사)·매니페스트(버전·sha) 산출.
- 연결 DoD: PK1

### T-PK2. 자동시트 가이드 → PK2
- 실행: grep `docs/pilot_packet/사용가이드.md` + 조립된 패킷 가이드
- 기대: `pilot_spotcheck.md` 자동 생성 설명 + "작성·반송" 지시 + 인간 dry-run OPEN. 빈 수기 양식 아님.
- 연결 DoD: PK2

### T-PK3. 샘플 쌍 → PK3
- 실행: 조립된 패킷 확인
- 기대: `샘플도면/`(또는 동등)에 before/after DXF 쌍 존재 — 첫 비교용.
- 연결 DoD: PK3

### T-PK4. 결정적 → PK4
- 실행: `pytest test_build_pilot_packet.py`
- 기대: 2회 동일 PASS. 매니페스트·구조 단언.
- 연결 DoD: PK4

### T-PK5. vapor 리다이렉트 + dogfood + gate → PK5
- 실행: grep 런북 + black/isort --check + `cad_policy_gate`
- 기대: `CUSTOMER_PILOT_*`이 `build_pilot_packet.py` 가리킴; 신규 .py clean; gate `passed`; per-PR 목록에 신규 테스트.
- 연결 DoD: PK5

## 통과 기준
- [x] T-PK1~PK5 PASS + 출력 요약을 STATUS "검증 로그"에 증거 기록
- [x] exe 빌드 안 함·재현성(수동 0)·인간 P0 OPEN STATUS에 확인
