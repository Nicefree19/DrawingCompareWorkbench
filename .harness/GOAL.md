# GOAL — 핵심 목표

## 한 줄 정의
**실 도면 1쌍으로 ~5분 dry-run이 가능한 무마찰 파일럿 러너** — 검출된 변경을 자동 나열한 spotcheck 시트 + ground-truth 스켈레톤을 한 번에 산출, 골든으로 결정적 검증.

## 배경/맥락
신뢰성/품질-인프라 아크(7 PR) 완료. 모든 cold review의 **P0(최고 레버) = 외부 구조검토자 dry-run으로 ground-truth 수집** — 8개월째 미실행([[cold_critique_2026_06_17]]). `review_ground_truth.csv`는 헤더만, `INTERNAL_PILOT_SPOTCHECK.md`는 빈 표.

실제 dry-run은 사람(구조 엔지니어)만 가능하지만 **마찰을 AI가 제거**할 수 있다: 기존 도구 `prepare_drawing_compare_customer_evidence.py`는 무거운 customer-evidence 머신(P5-G 게이트)이라 5분 파일럿엔 부적합. **경량 러너**로 "도면 2개 → 검출 변경 표 + 정답 스켈레톤"을 만들면, 사용자는 *아는 변경 검출 여부만 마킹*하면 된다.

이건 더 많은 내부 하드닝이 아니라 **제품의 진짜 미지(실도면서 작동하는가)를 줄이는** 작업이다.

## 검증 가능한 종료조건 (DoD)
- [x] **PE1 (러너)**: `scripts/run_pilot_spotcheck.py <before> <after> -o <out>`가 **실제 compare**(FolderComparePipeline 재사용) 실행 → `pilot_spotcheck.md` 산출: 검출 변경 행(위치·타입·한국어 요약) + 운영자 칸(아는변경/검출Y-N/위치정확Y-N/비고). · 검증: 골든쌍 실행→md 존재+검출행
- [x] **PE2 (정답 스켈레톤)**: 동일 실행이 `review_ground_truth.csv` 스켈레톤(검출 기반 행, **기존 스키마** 재사용)을 산출. · 검증: csv 헤더=기존 스키마 + 검출행
- [x] **PE3 (결정적 테스트)**: 골든 1쌍서 spotcheck가 알려진 변경을 나열·csv 스켈레톤 매칭. · 검증: pytest
- [x] **PE4 (운영자 가이드)**: "이 명령 1줄 → 표 채우기" 1-page (INTERNAL_PILOT_SPOTCHECK.md 갱신 or 신규). · 검증: 가이드에 실행 명령 + 판정 기준
- [x] **PE5 (no regression + dogfood)**: 신규 .py **black/isort clean**(방금 머지한 changed-files lint 통과) · 신규 테스트 per-PR 목록 추가 · `cad_policy_gate` 그린. · 검증: black --check + gate + 워크플로 grep

## 범위 밖
- **실제 dry-run 수행**(사람·실도면).
- DWG 변환 환경(ODA) — DXF/지원 경로만.
- 정확도/검출 로직 변경(분식 금지).
- 무거운 customer-evidence 게이트 재구현.

## 산출물
- `scripts/run_pilot_spotcheck.py` (경량 러너)
- `tests/unit/scripts/test_run_pilot_spotcheck.py` (결정적, black-clean)
- `docs/INTERNAL_PILOT_SPOTCHECK.md` 갱신(실행 가이드)
- 워크플로 per-PR 목록 + 완료 보고
