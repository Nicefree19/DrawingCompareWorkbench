# PLAN — 이행 로드맵

> 작은 단위·의존성. 한 반복 = 한 단계. verify-then-fix-or-drop.

## 단계 (순서대로)

### S1. 배선점 실측 + 발화증명 방법 확정 (선결)  (복잡도: 낮)
- 무엇을: ① `_on_auto_finished_v2`(L5929) 본문 읽고 output_dir/dashboard 가용 시점·기존 산출물 쓰는 위치 확인. ② emission 호출을 끼울 정확한 지점. ③ **발화증명 방법 택일** — offscreen GUI 인스턴스화로 핸들러 구동이 이 환경서 가능한지 probe([[headless_gui_wiring_verification]]: LOCALAPPDATA temp 격리); 불가/불안정이면 소스레벨 호출 단언(`inspect.getsource`)으로 폴백. ④ vapor 런북·INTERNAL_PILOT_GUIDE 아티팩트 표 위치 확인.
- 산출: 삽입 지점 + 증명방법 결정 메모.
- 검증: 실측
- 의존: 없음

### S2. emission 함수 추출 (GS1)  (복잡도: 낮)
- 무엇을: `run_pilot_spotcheck`의 인라인(md/csv 쓰기) 로직을 모듈 함수 `emit_spotcheck_artifacts(output_dir, pair_name)`로 추출. `run_pilot_spotcheck`는 이를 호출(동작 불변). 단위테스트: 합성 dashboard→md/csv.
- 산출: 함수 + 테스트.
- 검증: T-GS1, T-GS3(러너 불변)
- 의존: S1 →

### S3. GUI fail-safe 배선 (GS2)  (복잡도: 중)
- 무엇을: `_on_auto_finished_v2`에 `emit_spotcheck_artifacts(output_dir, pair_name)` try/except 호출(실패 시 logger.warning, GUI 흐름 무손상). pair_name은 result/output_dir서 파생.
- 산출: 배선 + 발화증명 테스트(S1 결정 방법).
- 검증: T-GS2
- 의존: S2 →

### S4. anti-theater 가드 (GS4)  (복잡도: 낮)
- 무엇을: `INTERNAL_PILOT_GUIDE.md` 아티팩트 표에 `pilot_spotcheck.md` 행 + "작성·반송" 지시. vapor 런북(`docs/release/CUSTOMER_PILOT_*`의 부재 `build_customer_pilot_*` 호출) 정리(실 producer로 리다이렉트 or 삭제). 인간 P0 OPEN 명시.
- 검증: T-GS4
- 의존: 없음(병행 가능)

### S5. 비퇴행 + dogfood + gate (GS5)  (복잡도: 낮)
- 무엇을: 신규/수정 .py black/isort clean, `cad_policy_gate` 그린, per-PR 목록 추가, 전체 관련 테스트 결정적.
- 검증: T-GS5
- 의존: S2~S4 →

## 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| emission 실패가 GUI 비교 흐름 깨뜨림 | 상 | **fail-safe try/except 필수**(실패=warning 로그, 비교 산출 무손상). |
| wired-but-unfired(이 프로젝트 시그니처) | 상 | 발화증명 테스트 필수 — offscreen 구동 or 소스레벨 호출 단언. 함수존재만으론 불충분. |
| GUI offscreen 인스턴스화 크래시(QML AV) | 중 | S1 probe. 불안정이면 소스레벨 단언 폴백(정직히 한계 명시). |
| scripts→GUI import가 frozen서 실패 | 중 | 선례 `workbench_subprocess.py:100`. 함수는 지연 import. |
| 빈 시트만 찍는 theater | 상 | GS4 가이드 "작성·반송"+인간 P0 OPEN 동반 필수(GOAL 명시). |
| 추출 리팩터가 러너 회귀 | 상 | run_pilot_spotcheck가 추출함수 호출, 기존 14테스트 보존. |

## 변경 이력
- 2026-06-27 생성: 냉철 리뷰(0.49) 추천 #1. 러너↔GUI 심 붕괴 — 엔지니어가 더블클릭만으로 시트 산출. anti-theater 가드 동반.
