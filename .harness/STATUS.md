# STATUS — 실시간 진행 추적

> 매 반복 ⑥ 즉시 갱신. 상태: ⬜대기 / 🔄진행 / ✅완료 / 🚧블록

## 현재 포커스
- **지금 무엇을, 왜**: S1~S5 전부 완료. 클로저 게이트 통과(단일 진입점 클린 재실행 전량 PASS) → 종료 판정
- **마지막 갱신**: 2026-06-27 / Phase 2 종료(4회차)
- **HARNESS_GATE**: tests=PASS · DoD 5/5 · 회귀 0(신규 파일만, 기존 코드 무변경)

## 단계 현황
| 단계 | 상태 | 다음 할 일 | 검증(T) | 메모 |
|------|------|-----------|---------|------|
| S1 검출 아티팩트 매핑 | ✅ | (완료) | 실측 | 소스=`artifacts/review_dashboard.json`→`top_issues[]`. gt 6필드 1:1 매핑 실측 |
| S2 경량 러너 (PE1) | ✅ | (완료) | T1✅ | `scripts/run_pilot_spotcheck.py` 골든02 실행→md 1검출행 |
| S3 gt 스켈레톤 (PE2) | ✅ | (완료) | T2✅ | 동일 러너가 csv 산출, 헤더=기존 스키마, 검출행+정답 미조작 |
| S4 테스트+dogfood lint (PE3/PE5) | ✅ | (완료) | T3✅,T5✅ | `test_run_pilot_spotcheck.py` 5 passed×2·black/isort clean·gate passed·CI L81 등록 |
| S5 가이드+비퇴행 (PE4) | ✅ | (완료) | T4✅,T5✅ | INTERNAL_PILOT_SPOTCHECK.md 자동 러너 섹션(명령+판정+누락) |

## S1 매핑 메모 (확정)
- **per-change 소스**: `artifacts/review_dashboard.json` → `top_issues[]` (flat list, 골든02=1행). `review_priority.csv`는 동일필드 CSV판(폴백 가능).
- **gt 스키마 매핑(실측)**: drawing_label←display_label · category←category(`member`) · summary_contains←change_summary_ko/major_layers · source_format←source_format(`cad`) · detection_source←detection_source(`cad_entity`) · bbox_status←bbox_status(`exact`).
- **운영자 표 필드**: 위치=bbox_text+major_layers · 타입=change_type_ko/severity_ko · 요약=change_summary_ko · 증감=added/deleted/modified. 전부 검출 산출물(분식 0).

## 검증 로그 (증거)
- [x] T1 러너→spotcheck.md(검출행+운영자칸): 골든02 실행 `검출 변경: 1건`. md L11=검출행(`…/BEAM | 혼합·높음 | 구조 부재 표기 변경… | +1/-1/~0`) + 운영자칸 4(아는변경/검출/위치정확/비고). [pytest로 잠금→T3]
- [x] T2 review_ground_truth.csv 스켈레톤(기존 스키마): 헤더=`drawing_label,category,summary_contains,source_format,detection_source,bbox_status,notes`. 행=`after,member,BEAM;mixed,cad,cad_entity,exact,검출기반 스켈레톤…`. 정답 미조작. [pytest로 잠금→T3]
- [x] T3 골든 결정적(알려진 변경 나열): `pytest test_run_pilot_spotcheck.py` → **5 passed** 2회 연속(13.14s, 11.04s) 동일. 순수 변환 4 + 골든 파이프라인 통합 1(BEAM 검출·csv 헤더=스키마).
- [x] T4 운영자 가이드(실행명령+판정): `grep -nE "run_pilot_spotcheck|배포 진행|누락"` → L11 명령, L28 "배포 진행 후보(누락 0)", L23/29 누락 기록. 실행 명령 1줄 + 판정 기준 존재.
- [x] T5 신규.py black/isort clean + gate + 목록: black --check `2 files unchanged`, isort clean, `CAD policy gate passed`, CI 워크플로 L81 `test_run_pilot_spotcheck.py` 등록, 기존 step 보존(단일 행 추가).

## 블록/이슈
- 없음. Phase 2 완결. **남은 진짜 P0는 여전히 사람**: 구조 엔지니어가 실도면 쌍으로 이 러너를 돌려 dry-run을 수행(이번 작업은 그 마찰만 제거). [[cold_critique_2026_06_17]]

## HARNESS_GATE
- tests=PASS | closure: DoD 5/5 met · TEST 0 unmet · steps 🔄0/🚧0 · 단일진입점 클린 재실행 전량 PASS · 검증기 무약화(신규 테스트만 추가) · 회귀 0(기존 코드 무변경)