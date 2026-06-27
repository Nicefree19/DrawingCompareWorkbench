# RULES — 핵심 제약 (경량화)

> ⚠️ 이 목표(GUI 스팟체크 시트 emission + anti-theater)에 직결되는 조항만.

## 절대 규칙 (위배 시 작업 중단)
- **재구현 금지**: 시트 생성은 기존 `build_spotcheck_md`/`build_ground_truth_rows`/`_load_top_issues` 그대로(추출만). 새 시트 로직 0.
- **GUI fail-safe**: emission은 `_on_auto_finished_v2`에서 try/except로 감싼다 — 실패해도 **기존 비교 완료 흐름·산출물 무손상**(warning 로그만). 사용자 비교를 절대 깨지 않는다.
- **발화 증명**: 함수 존재만으로 "완료" 금지. **GUI가 실제 시트를 발화함**을 증명(offscreen 핸들러 구동 산출물 검증 or 소스레벨 호출 단언). wired-but-unfired는 이 프로젝트 시그니처 실패([[dead_island_gate_bypass]], [[native_viewer_producer_gap]]).
- **anti-theater 동반**: 시트 배선만 하고 끝내지 않는다. 가이드 "작성·반송" 지시 + **인간 P0(실 도면 dry-run)는 OPEN 명시**. 컨테이너를 증거로 착각 금지.
- **러너 비퇴행·정답 미조작**: 추출 후 기존 러너 산출 불변, csv 스켈레톤=사실만(승계).

## 설계/코드 제약
- 경량: emission 함수 추출 + GUI 1지점 호출(지연 import). 광범위 GUI 리팩터 금지.
- 신규 테스트 결정적·헤드리스(offscreen은 QT_QPA_PLATFORM=offscreen·temp 설정 격리). per-PR 목록 추가.
- 한 반복 = 한 단계.

## 우선순위 (충돌 시)
1. 정직성(발화 증명·재구현 안 함·인간 P0 OPEN) > 2. GUI 무손상(fail-safe) > 3. 무마찰(더블클릭→시트) > 4. 결정성 > 5. 단순성

## 검증 연결
- emission·러너불변 = T-GS1/T-GS3. GUI 발화 = T-GS2. 가이드·anti-theater = T-GS4. dogfood·gate = T-GS5.
