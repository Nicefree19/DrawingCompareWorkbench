# PLAN — 이행 로드맵

> 작은 단위·의존성. 한 반복 = 한 단계. verify-then-fix-or-drop.

## 단계 (순서대로)

### S1. 검출-변경 아티팩트 매핑 확인 (선결)  (복잡도: 낮)
- 무엇을: 골든쌍에 FolderComparePipeline 실행 → 산출물 중 **per-change** 소스 확정(review_dashboard.json top issues / change register csv / change_zones.json 중 위치·타입·요약을 가장 깨끗이 주는 것). review_ground_truth.csv 기존 스키마 컬럼 확인.
- 산출: 소스 아티팩트 + 필드 매핑 메모.
- 검증: 실측 필드
- 의존: 없음

### S2. 경량 러너 구현 (PE1)  (복잡도: 중)
- 무엇을: `scripts/run_pilot_spotcheck.py` — argparse(before, after, -o). FolderComparePipeline 실행(재사용, 재구현 금지) → S1 소스 파싱 → `pilot_spotcheck.md`(검출 행 + 운영자 빈칸 + 헤더: 쌍 이름/총 검출수/판정 기준).
- 산출: 러너 + md.
- 검증: T1
- 의존: S1 →

### S3. ground-truth 스켈레톤 (PE2)  (복잡도: 낮)
- 무엇을: 동일 러너가 `review_ground_truth.csv` 스켈레톤(기존 스키마 헤더 + 검출 기반 행) 산출.
- 산출: csv.
- 검증: T2
- 의존: S2 →

### S4. 결정적 테스트 (PE3) + dogfood lint (PE5)  (복잡도: 중)
- 무엇을: `test_run_pilot_spotcheck.py` — 골든쌍 실행→md/csv 산출·알려진 변경 나열 단언. **black/isort clean**하게 작성(새 lint 게이트 통과). per-PR 목록 추가.
- 산출: 테스트(클린) + 워크플로 1줄.
- 검증: T3, T5
- 의존: S2,S3 →

### S5. 운영자 가이드 (PE4) + 비퇴행  (복잡도: 낮)
- 무엇을: INTERNAL_PILOT_SPOTCHECK.md에 "1줄 실행→표 채움→판정" 갱신. cad_policy_gate·기존 step 보존 확인.
- 검증: T4, T5
- 의존: S2~S4 →

## 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| 러너가 compare 재구현(중복) | 상 | FolderComparePipeline 그대로 호출. 출력만 변환. |
| 검출 아티팩트 필드가 빈약(요약/위치 없음) | 중 | S1서 최선 소스 선택. 없으면 change_zones.json centroid+type. |
| 신규 .py가 새 lint 게이트 fail(자기모순) | 상 | 작성 후 즉시 black/isort 적용(dogfood). |
| 대형 실도면서 느림/DWG 변환 필요 | 중 | 골든=DXF로 테스트. 가이드에 "DXF 우선/사전변환" 명시. |
| ground-truth 스키마 변형(분식) | 중 | 기존 헤더 그대로. 스켈레톤은 detection_source='heuristic' 등 사실만. |

## 변경 이력
- 2026-06-26 생성: 인프라 아크 완료 후 P0(외부 검증) 마찰 제거로 전환. 경량 러너=기존 파이프라인 래퍼.
