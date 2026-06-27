# STATUS — 실시간 진행 추적

> 매 반복 ⑥ 즉시 갱신. 상태: ⬜대기 / 🔄진행 / ✅완료 / 🚧블록

## 현재 포커스
- **지금 무엇을, 왜**: S1~S5 전부 완료(GUI핸들러→**파이프라인 emission 피벗**). 클로저 게이트 → 종료 판정
- **마지막 갱신**: 2026-06-27 / Phase 2 종료
- **HARNESS_GATE**: tests=PASS · DoD 5/5 · 모놀리스 미접촉(freeze 존중) · 검출 무변경

## 피벗 메모 (중요)
- GUI 핸들러 직접 편집 시도 → **모놀리스 분해 freeze 충돌**(cad_policy_gate 라인상한 13482 초과 + black-dirty 2912줄). 사용자 승인 하에 **파이프라인-레벨 emission으로 전환**.
- 시트 빌더를 `src/services/comparison/pilot_spotcheck_sheet.py`로 추출(clean) → `FolderComparePipeline.run()`이 review_dashboard 직후 `emit_spotcheck_artifacts_safely(output_dir)` fail-safe 호출. **GUI·CLI 모든 호출자 자동 산출**(단일 producer, 모놀리스 0줄). 되돌림: 모놀리스 편집·lint 게이트 grandfather-제외.

## 단계 현황
| 단계 | 상태 | 다음 할 일 | 검증(T) | 메모 |
|------|------|-----------|---------|------|
| S1 배선점+발화증명 방법 | ✅ | (완료) | 실측 | (피벗 후 무효화) 모놀리스 freeze 발견이 S3 피벗 유발 |
| S2 emission 함수 추출 (GS1) | ✅ | (완료) | T-GS1✅ | `pilot_spotcheck_sheet.py`(src) 추출, 러너 re-export. 합성 dashboard 테스트 |
| S3 파이프라인 emission (GS2) | ✅ | (완료) | T-GS2✅ | `run()` review_dashboard 직후 `emit_spotcheck_artifacts_safely` fail-safe. e2e 스모크가 pilot_spotcheck.md 발화 단언 |
| S4 anti-theater 가드 (GS4) | ✅ | (완료) | T-GS4✅ | 가이드 spotcheck행+작성·반송+P0 OPEN; vapor 런북 2개 "미구현" 배너 |
| S5 비퇴행+dogfood+gate (GS5) | ✅ | (완료) | T-GS5✅ | 3파일 black/isort clean·gate passed·per-PR(e2e스모크+러너 등록됨) |

## 검증 로그 (증거)
- [x] T-GS1 emission 함수: `pilot_spotcheck_sheet.emit_spotcheck_artifacts` — 합성 dashboard→md(BEAM)/csv. 누락 dashboard도 0행 시트(무크래시). 단위테스트 PASS.
- [x] T-GS2 파이프라인 발화: e2e 스모크(`test_e2e_pipeline_smoke`) 실 파이프라인 실행 → `pilot_spotcheck.md`+`review_ground_truth.csv` 존재·"검출된 변경" 단언 **1 passed**. wired-AND-fired(GUI가 쓰는 바로 그 run()).
- [x] T-GS3 러너 비퇴행: `pytest test_run_pilot_spotcheck.py` **16 passed**(추출 re-export·DWG·실-ODA e2e 전부). 산출 불변.
- [x] T-GS4 anti-theater: 가이드 grep 5(pilot_spotcheck/작성/반송/OPEN), vapor 런북 2개 "미구현" 배너. 인간 P0 OPEN 명시.
- [x] T-GS5 dogfood+정책: 3파일(신규 src·러너·파이프라인) black/isort clean·`CAD policy gate passed`(모놀리스 ceiling 미접촉)·per-PR(e2e스모크 L78·러너 L82 기등록).

## 블록/이슈
- 없음. Phase 2 완결. **anti-theater 핵심**: 시트는 이제 GUI 더블클릭으로 산출(P0 *기질* 이동). 단 **인간 P0(실 도면 dry-run·작성 반송 시트)는 여전히 OPEN** — 가이드에 명시, 컨테이너≠증거.

## HARNESS_GATE
- tests=PASS | closure: DoD 5/5 met · TEST 0 unmet · steps 🔄0/🚧0 · e2e 스모크(파이프라인 발화)+러너 16 passed · 검증기 무약화 · 모놀리스 0줄(freeze 존중) · 검출 무변경
