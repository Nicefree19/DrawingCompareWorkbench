# GOAL — 핵심 목표

## 한 줄 정의
**재현 가능한 `scripts/build_pilot_packet.py`** — 빌드된 exe 디렉터리 + 버전관리 패킷 소스(런처·갱신 가이드·샘플 쌍)로부터 **현행 사내 파일럿 패킷을 한 명령으로 조립**(+zip). 존재한 적 없는 `build_customer_pilot_*` vapor 런북을 실 producer로 대체하고, 패킷 가이드를 **자동 시트(`pilot_spotcheck.md`)+작성·반송**으로 갱신해 빈 수기 양식을 대체한다.

## 배경/맥락
실측: 출하급 패킷(`release/DrawingCompare_v0.9.2_internal_pilot/` = exe+`.bat`+사용가이드+성과요약+스팟체크양식)이 **존재하나 6/11 빌드** — 이번 세션 5 PR(#56·#58·#59·#60·#61) **전부 미반영**. 특히 **PR#61 자동 시트가 없어 빈 수기 양식을 줌**(엔지니어 피드백의 핵심 산출물 누락). 게다가 **조립 스크립트가 repo에 없음**(vapor `build_customer_pilot_*`, grep 실측 부재) → v0.9.3을 명령 한 줄로 못 만듦. `release/`는 gitignore라 패킷·소스가 버전관리 밖.

이 루프는 **재현 가능한 패킷 빌더 + 버전관리 가이드 소스**를 만들어 release-distribution(냉철 리뷰 0.28)을 끌어올린다. **exe 빌드 자체는 범위 밖**(빌드머신·사람) — 스크립트는 *빌드된 exe 디렉터리를 입력*받아 나머지를 조립한다(stub exe로 결정적 테스트).

## 검증 가능한 종료조건 (DoD)
- [x] **PK1 (조립 스크립트)**: `scripts/build_pilot_packet.py --app-dir <빌드된 exe 디렉터리> -o <out> [--version X] [--zip]` → 패킷 디렉터리(`DrawingCompare_실행.bat` + `사용가이드.md` + `샘플도면/` + `app/` 복사 + 매니페스트). · 검증: stub app-dir로 실행→패킷 구조 산출(pytest)
- [x] **PK2 (버전관리 가이드 + 자동 시트)**: repo `docs/pilot_packet/사용가이드.md`(버전관리 소스)가 **자동 `pilot_spotcheck.md`+작성·반송**을 설명(빈 수기 양식 대체). 스크립트가 이를 패킷에 복사. · 검증: grep 패킷/소스 가이드에 `pilot_spotcheck`·작성·반송
- [x] **PK3 (실행 가능 샘플 쌍)**: 패킷에 before/after DXF 샘플 쌍 포함 → 엔지니어가 자기 데이터 없이 첫 비교 가능. · 검증: 조립된 패킷에 샘플 파일 존재
- [x] **PK4 (결정적 테스트)**: stub app-dir fixture→조립→`.bat`·가이드·샘플·app 존재 단언. · 검증: pytest 2회 동일
- [x] **PK5 (vapor 리다이렉트 + dogfood + gate)**: `docs/release/CUSTOMER_PILOT_*` 런북이 `build_pilot_packet.py`(실 producer)를 가리킴. 신규 .py black/isort clean. `cad_policy_gate` 그린. per-PR 목록. · 검증: grep + black/isort + gate

## 범위 밖
- **exe 빌드**(PyInstaller·빌드머신·사람) — 스크립트는 빌드된 app-dir를 입력받음.
- 코드 서명·installer·SmartScreen.
- 실제 패킷 핸드오버·인간 dry-run(여전히 OPEN).
- 검출/엔진/GUI 로직 변경.

## 산출물
- `scripts/build_pilot_packet.py` (조립 producer)
- `docs/pilot_packet/사용가이드.md` (버전관리 가이드 소스, 자동 시트 반영)
- `tests/unit/scripts/test_build_pilot_packet.py` (결정적, stub app-dir)
- `docs/release/CUSTOMER_PILOT_*` 리다이렉트 + per-PR + 완료 보고
