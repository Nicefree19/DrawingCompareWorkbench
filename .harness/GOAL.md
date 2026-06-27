# GOAL — 핵심 목표

## 한 줄 정의
**GUI 비교 완료 경로가 운영자 스팟체크 시트(`pilot_spotcheck.md`)+정답 스켈레톤(`review_ground_truth.csv`)을 자동 산출**하게 해, 구조 엔지니어가 `.bat` 더블클릭만으로 (Python 체크아웃 없이) 측정 가능한 dry-run 산출물을 얻게 한다. + anti-theater 가드(작성·반송 지시 / vapor 런북 정리 / 인간 P0 OPEN 명시).

## 배경/맥락
냉철 리뷰(워크플로 14에이전트, real_vs_theater **0.49**, release-distribution **0.28**)의 추천 #1: **러너↔GUI 심 붕괴**. 현재 `pilot_spotcheck.md`는 `scripts/run_pilot_spotcheck.py`(dev-Python 전용, 출하 패킷에 없음)만 산출 → **엔지니어는 만들 수 없다**. GUI(`drawing_compare_workbench.py`)는 같은 엔진(`FolderComparePipeline`)을 구동하고 완료 핸들러 `_on_auto_finished_v2`(L5929)가 `result.output_dir`를 쥐고 있으나 **시트를 안 만든다**. `build_spotcheck_md`/`build_ground_truth_rows`는 검증된 **순수 함수**(top_issues→문자열), `scripts→import` 선례 존재(`workbench_subprocess.py:100`).

이건 P0(외부 엔지니어 실도면 dry-run)의 *기질*을 "비개발자에겐 불가능"→"더블클릭 한 번"으로 옮기는 유일한 바운디드 코드 루프.

**Anti-theater(리뷰가 적시한 함정)**: 운영자에게 *작성하라*는 안내 없이 5번째 빈 산출물만 더 찍으면 = 이번 세션 실패모드 반복. 그래서 가이드 갱신+vapor 정리+인간 P0 OPEN 명시가 같은 루프에 **필수**.

## 검증 가능한 종료조건 (DoD)
- [x] **GS1 (emission 함수)**: `emit_spotcheck_artifacts(output_dir, pair_name=...)` 모듈 함수(기존 `run_pilot_spotcheck` 인라인 로직 추출, 재구현 0) → output_dir에 md+csv. · 검증: 합성 `artifacts/review_dashboard.json`로 단위테스트(검출행 산출)
- [x] **GS2 (파이프라인 배선·발화 증명)**: 모놀리스가 분해 freeze(라인상한+black-dirty)라 핸들러 직접편집 불가 → **`FolderComparePipeline.run()`이 review_dashboard 직후 `emit_spotcheck_artifacts_safely(output_dir)`를 fail-safe 호출**. GUI·CLI 모든 호출자가 공유 producer로 시트를 얻음(단일 producer, 모놀리스 미접촉). **wired-but-unfired 아님**을 e2e 스모크(실 파이프라인→pilot_spotcheck.md 존재 단언)로 증명. · 검증: e2e 스모크 테스트
- [x] **GS3 (러너 비퇴행)**: 추출 리팩터 후 기존 `run_pilot_spotcheck` 산출·테스트 불변. · 검증: 기존 14 테스트 통과
- [x] **GS4 (anti-theater 가드)**: `INTERNAL_PILOT_GUIDE` 아티팩트 표에 spotcheck 행 + "**작성·반송**" 지시; 존재한 적 없는 `build_customer_pilot_*` vapor 런북 정리; 인간 P0(실 도면 실행)는 **OPEN** 명시. · 검증: grep
- [x] **GS5 (비퇴행+dogfood+gate)**: 신규/수정 .py black/isort clean, `cad_policy_gate` 그린, per-PR 목록, 테스트 결정적. · 검증: black/isort + gate + pytest

## 범위 밖
- **실제 dry-run 수행**(사람·실도면) — 이 루프는 그 *기질*만 옮김, 인간 P0는 OPEN 유지.
- 검출/정확도/엔진 로직 변경.
- 광범위 GUI 리팩터(핸들러에 fail-safe 1지점 + emission 호출만).
- exe 빌드/서명/installer(별개·멀티파일).

## 산출물
- `scripts/run_pilot_spotcheck.py` 리팩터(`emit_spotcheck_artifacts` 추출)
- `src/gui/drawing_compare_workbench.py` `_on_auto_finished_v2` fail-safe 호출
- GUI 배선 테스트 + 기존 러너 테스트 보존
- `docs/INTERNAL_PILOT_GUIDE.md` 갱신 + vapor 런북 정리 + per-PR 목록
