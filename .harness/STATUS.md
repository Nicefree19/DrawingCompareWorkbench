# STATUS — 실시간 진행 추적

> 매 반복 ⑥ 즉시 갱신. 상태: ⬜대기 / 🔄진행 / ✅완료 / 🚧블록

## 현재 포커스
- **지금 무엇을, 왜**: S1~S5 전부 완료. 클로저 게이트 통과(단일 진입점 클린 재실행 전량 PASS) → 종료 판정
- **마지막 갱신**: 2026-06-27 / Phase 2 종료(5회차)
- **HARNESS_GATE**: tests=PASS · DoD 5/5 · 회귀 0(기존 코드 무변경·단일쌍 보존)

## 단계 현황
| 단계 | 상태 | 다음 할 일 | 검증(T) | 메모 |
|------|------|-----------|---------|------|
| S1 배치/DWG 배선점 실측 | ✅ | (완료) | 실측 | 디렉터리 입력→자동 폴더분기(새필드 불요). top_issues `display_label`=쌍식별 |
| S2 폴더 배치 (PB1) | ✅ | (완료) | T-PB1✅ | 2쌍 폴더→spotcheck `### 쌍: alpha/beta` 분리, csv 쌍 구분행 |
| S3 DWG 온램프 (PB2) | ✅ | (완료) | T-PB2✅ | `_resolve_dwg_backend_mode`: .dwg→ODA배선, 미설치→fail-loud(exit2) |
| S4 결정적+비퇴행 (PB3/PB4) | ✅ | (완료) | T-PB2✅,T-PB3✅,T-PB4✅ | **12 passed**(5→12: 배치3+DWG4). black/isort clean |
| S5 가이드+dogfood+게이트 (PB5) | ✅ | (완료) | T-PB5✅ | 가이드 폴더/DWG 사용법·gate passed·per-PR 유지 |

## 검증 로그 (증거)
- [x] T-PB1 폴더 배치(쌍≥2 구분): 골든 2쌍 폴더 실행 exit0 → `pilot_spotcheck.md` `### 쌍:` 섹션 2개(alpha/beta), csv drawing_label=alpha/beta 구분. (closure 재실행 `쌍 섹션 수=2`)
- [x] T-PB2 DWG 온램프(라우팅/fail-loud): 단위테스트 — .dwg→`DWG_BACKEND_ODA_CONVERTER` 배선(설치 시), 미설치 mock→`PilotSpotcheckError`("DXF로 변환" 안내). DXF→None.
- [x] T-PB3 단일쌍 비퇴행: 기존 PR#56 단일쌍 테스트 전량 통과(single-pair `### 쌍:` 미생성 단언 포함).
- [x] T-PB4 결정적: `pytest` **12 passed** 2회 연속(44.2s·72.9s) 동일.
- [x] T-PB5 dogfood+정책+가이드: black/isort `unchanged`·clean, `CAD policy gate passed`, 가이드 폴더(L15-17)/DWG(L36-37) 사용법, per-PR L81 유지.

## 블록/이슈
- 없음. Phase 2 완결. **남은 진짜 P0는 여전히 사람**: 개발자가 실 DWG 폴더로 이 러너를 돌려 dry-run 수행(이번 작업은 그 입력 마찰만 제거). [[cold_critique_2026_06_17]]

## HARNESS_GATE
- tests=PASS | closure: DoD 5/5 met · TEST 0 unmet · steps 🔄0/🚧0 · 단일진입점 클린 재실행 전량 PASS(폴더배치 exit0·pytest 12×2·black/isort clean·gate passed) · 검증기 무약화(신규 테스트만 추가) · 회귀 0(기존 코드 무변경)
