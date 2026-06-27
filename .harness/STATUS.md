# STATUS — 실시간 진행 추적

> 매 반복 ⑥ 즉시 갱신. 상태: ⬜대기 / 🔄진행 / ✅완료 / 🚧블록

## 현재 포커스
- **지금 무엇을, 왜**: S1~S5 전부 완료. 클로저 게이트 → 종료 판정
- **마지막 갱신**: 2026-06-27 / Phase 2 종료(5회차)
- **HARNESS_GATE**: tests=PASS · DoD 5/5 · 검출 src 변경 0·골든 floor 비퇴행(r=0.786,noise_fp=0)

## 단계 현황
| 단계 | 상태 | 다음 할 일 | 검증(T) | 메모 |
|------|------|-----------|---------|------|
| S1 최소 훅 + fixture + red 재현 | ✅ | (완료) | 실측 | red 재현(폴더 AC1032 preflight 실패)·단일OK·DXF→AC1032 15,876B. **훅=러너 additive 폴더 사전변환** |
| S2 커밋 fixture (DF2) | ✅ | (완료) | T-DF2✅ | dwg/{before,after}.dwg AC1032·15,876B·trackable + README |
| S3 폴더-DWG 변환 수정 (DF1) | ✅ | (완료) | T-DF1✅ | `_convert_folder_dwgs` 러너 additive. 폴더 DWG red→green(detected=1), 단일 불변 |
| S4 실-ODA e2e (DF3/DF4) | ✅ | (완료) | T-DF3✅,T-DF4✅ | 14 passed. e2e 단일59s+폴더64s 비-skip(실 ODA). mock 12건 보존 |
| S5 비퇴행+정책+골든floor (DF5) | ✅ | (완료) | T-DF5✅ | gate passed·골든 floor exit0(r0.786/noise0)·per-PR L81·검출 src 0변경 |

## 검증 로그 (증거)
- [x] T-DF1 폴더-DWG 변환 수정(red→green): S1 probe 폴더 DWG 수정 전 `Preflight failed AC1032`→수정 후 `detected_count=1`. 단일 DWG도 `=1`(불변).
- [x] T-DF2 커밋 fixture(AC1032·<100KB): `dwg/{before,after}.dwg` 헤더 `AC1032`·각 15,876B·git trackable + 재현 README.
- [x] T-DF3 실-ODA e2e(단일+폴더, 비-skip): `pytest` **14 passed**. e2e 2건 **비-skip**(단일 59.2s·폴더 63.9s = 실 ODA 변환 수행), BEAM 검출 단언.
- [x] T-DF4 단일 DWG 비퇴행+mock 보존: 기존 mock 단위테스트 12건 전량 통과, 단일 DWG 경로 불변.
- [x] T-DF5 dogfood+정책+골든floor: black/isort clean·`CAD policy gate passed`·골든 floor `pairs=15 p=0.647 r=0.786 noise_fp=0` exit0·per-PR L81·detection src 0변경.

## 블록/이슈
- 없음. Phase 2 완결. PR#58의 폴더+DWG 실버그 수정 + mock-only→실 AC1032 비-skip 증명. **남은 진짜 P0는 여전히 사람**(실 DWG 폴더 dry-run).

## HARNESS_GATE
- tests=PASS | closure: DoD 5/5 met · TEST 0 unmet · steps 🔄0/🚧0 · pytest 14 passed(e2e 비-skip) · 검증기 무약화(신규 테스트만 추가) · 검출 src 0변경·골든 floor 비퇴행 · 정책 그린
