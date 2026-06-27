# GOAL — 핵심 목표

## 한 줄 정의
**파일럿 러너가 광고하는 폴더+DWG 입력을 실제로 작동하게 한다** — 폴더 내 DWG가 비교 전 변환되도록 수정 — 하고, mock-only DWG 테스트를 **커밋된 실 AC1032 골든 fixture + 실-ODA end-to-end 테스트**로 보강해 증명한다.

## 배경/맥락
PR#58(reliability/pilot-dwg-batch-harness)은 폴더+DWG를 광고(`run_pilot_spotcheck._inputs_include_dwg`가 폴더 내 .dwg 감지, CLI가 폴더 배치 약속)하지만 **실제로는 깨져 있다**(이 머신 라이브 재현 2회):

- 단일 DWG 쌍: **작동**(실 ODA AC1032→DXF→compare→검출 1건). ✅
- 폴더+DWG: **실패**. `folder_compare_pipeline.py:383-389`가 `auto_convert_unsupported_dwg`에 **디렉터리 경로**를 넘김 → `dwg_dxf_fallback.py:423`에서 `not_dwg`(suffix≠`.dwg`) 반환 → 폴더 내 DWG 미변환 → preflight "AC1032 unsupported" 실패. ❌

근인: 폴더 경로가 per-file 변환을 하지 않음. **단위테스트가 ODA를 mock**해서 이 갭을 못 잡았다 — 이 코드베이스의 시그니처 실패모드(wired-but-unproven, [[dead_island_gate_bypass]])를 방금 출하한 셈. ODA는 이 머신에 설치됨(`converter_installation_status().installed=True`). 골든 02 DXF→AC1032 DWG 변환(~16KB/파일) 커밋가능 실측됨.

이 루프는 **자신이 방금 출하한 회귀를 고치고**, 실데이터 dry-run([[pilot-runner-enablement-2026-06-27]])의 입력 경로를 mock이 아닌 **실 DWG로 증명**한다.

## 검증 가능한 종료조건 (DoD)
- [x] **DF1 (폴더-DWG 변환 수정)**: 폴더 입력에 DWG가 있으면 비교 전 각 DWG가 **기존 승인 변환 경로**(per-file `auto_convert_unsupported_dwg`)로 변환되어 compare가 정상 진행. 최소 blast-radius(공유 GUI 경로 비파괴) 우선. · 검증: 실 AC1032 DWG 폴더 쌍 e2e(skipif ODA)→검출 ≥1
- [x] **DF2 (커밋 fixture)**: 골든 02 `before/after.dxf`를 AC1032 DWG로 변환한 쌍을 `tests/data/...` 커밋. · 검증: 파일 존재 + 헤더 `AC1032` + 합리적 크기(<100KB/파일)
- [x] **DF3 (실-ODA e2e 테스트)**: `@skipif(not converter_installation_status().installed)`로 **단일 DWG 쌍 + DWG 폴더** 둘 다 실 변환→compare→spotcheck, 알려진 BEAM 변경 검출 단언. · 검증: pytest(로컬 ODA, 비-skip 실행)
- [x] **DF4 (단일 DWG 비퇴행 + mock 보존)**: 기존 mock 단위테스트(`_resolve_dwg_backend_mode` 등) 유지·통과, 단일 DWG 경로 동작 불변. · 검증: pytest
- [x] **DF5 (비퇴행+dogfood+gate)**: 골든 정확도 floor·CAD 회귀 통과, 신규/수정 .py black/isort clean, `cad_policy_gate` 그린, per-PR 목록에 신규 e2e(skipif 포함) 추가. · 검증: black/isort + gate + grep + 골든 floor

## 범위 밖
- ODA 변환 엔진 자체 재구현/변경.
- 검출/정확도 로직 변경(07 채점 어휘는 다음 루프 [[attrib-recall-compare-surfacing]]).
- 실제 dry-run 수행(사람).
- GUI 폴더-DWG 경로의 광범위 리팩터(필요 최소만; 러너 경로 우선).
- DWG "완전 지원" 주장(정책).

## 산출물
- `scripts/run_pilot_spotcheck.py` 또는 `src/services/comparison/folder_compare_pipeline.py` 수정(S1서 최소 훅 확정)
- `tests/data/comparison/golden/dxf/02_single_modification/dwg/` AC1032 쌍(커밋)
- `tests/unit/scripts/test_run_pilot_spotcheck.py` 실-ODA e2e 추가(skipif)
- 워크플로 per-PR 목록 갱신 + 완료 보고
