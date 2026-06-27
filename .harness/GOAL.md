# GOAL — 핵심 목표

## 한 줄 정의
**파일럿 러너가 실 리비전 자료(DWG 파일·폴더 단위 다중 쌍)를 그대로 받아** 비교→spotcheck/정답 스켈레톤을 산출 — 기존 엔진의 폴더 배치(`BatchCompareJob`)+DWG 자동변환(`dwg_converter`)을 **배선만**, 재구현 0.

## 배경/맥락
PR#56 파일럿 러너(`scripts/run_pilot_spotcheck.py`)는 **단일 DXF 쌍 전용**. 그러나 실측 결과 실 구조 리비전 자료는 **DWG가 폴더 단위로 묶인 다중 쌍**(`.local` 실샘플 전량 DWG, 커밋된 실 리비전 쌍 0). 엔진은 이미 폴더 스캔·매칭(`FolderComparePipeline.scan_drawing_inputs`)+병렬 배치(`BatchCompareJob.run`)+ODA 자동변환을 보유하나, 파일럿 경로가 단일파일 강제+`dwg_backend_mode` 미설정이라 **둘 다 미발화**.

이 갭을 메우면 개발자가 **실 폴더를 그대로 가리켜** dry-run을 수행할 수 있다 — 8개월 미실행 P0(외부 실도면 검증, [[cold_critique_2026_06_17]])의 마지막 입력 배선. 정확도(07 등 recall)는 실 dry-run이 실증거로 우선순위를 정한 뒤 후속(투기적 정확도 작업=theater 회피).

## 검증 가능한 종료조건 (DoD)
- [x] **PB1 (폴더 배치)**: 러너가 before/after로 **폴더**를 받으면 FolderComparePipeline 폴더 스캔+매칭으로 N쌍 비교 → `pilot_spotcheck.md`가 **쌍별 구분 섹션/행**(쌍 이름·검출수)으로 산출, csv는 쌍 식별(drawing_label) 컬럼 포함. · 검증: 골든 다중쌍 폴더 실행→쌍 ≥2 구분 행
- [x] **PB2 (DWG 온램프)**: 러너가 `.dwg` 입력 시 **기존 승인된 변환 경로**로 라우팅(재구현 0). ODA/변환 불가 시 **fail-loud**(사전변환 안내, 침묵 빈결과 금지). · 검증: 헤드리스 단위테스트(DWG 입력→변환 경로 호출 단언 or 부재 시 명확 에러)
- [x] **PB3 (단일쌍 비퇴행)**: 기존 단일 DXF 쌍 산출물(spotcheck/csv) 불변. · 검증: 기존 `test_run_pilot_spotcheck` 전량 통과
- [x] **PB4 (결정적 테스트)**: 폴더 배치 + DWG 배선 결정적 테스트(골든/헤드리스). · 검증: pytest 2회 동일
- [x] **PB5 (가이드+dogfood+비퇴행)**: 가이드에 폴더/DWG 사용법 1줄. 신규/수정 .py **black/isort clean**. `cad_policy_gate` 그린(DWG "완전지원" 미주장·ODA 정책 준수). per-PR 목록 유지. · 검증: black/isort + gate + grep

## 범위 밖
- **실제 dry-run 수행**(사람·실도면).
- **ODA 변환 엔진 자체** 변경/재구현.
- **검출/정확도 로직 변경**(07 등 recall은 이 루프가 실데이터로 실증한 뒤 후속).
- DWG **"완전 지원" 주장**(정책 위반).
- 새 customer-evidence 게이트 재구현.

## 산출물
- `scripts/run_pilot_spotcheck.py` 확장(폴더/DWG 입력 라우팅)
- `tests/unit/scripts/test_run_pilot_spotcheck.py` 확장(배치/DWG 결정적, black-clean)
- `docs/INTERNAL_PILOT_SPOTCHECK.md` 갱신(폴더/DWG 사용법)
- 워크플로 per-PR 목록 유지 + 완료 보고
