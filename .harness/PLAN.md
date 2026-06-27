# PLAN — 이행 로드맵

> 작은 단위·의존성. 한 반복 = 한 단계. verify-then-fix-or-drop.

## 단계 (순서대로)

### S1. 배치/DWG 배선점 실측 (선결)  (복잡도: 낮)
- 무엇을: ① 골든 다중쌍 **폴더**를 FolderComparePipeline에 넣어 산출 `review_dashboard.json` `top_issues`가 쌍 식별(pair_id/display_label/drawing_number)을 어떻게 담는지 실측. ② `.dwg` 입력→ODA 자동변환 발화 조건(`_is_explicit_oda_converter_backend`/`dwg_backend_mode`) + 변환 불가 신호 실측. ③ `cad_policy_gate`가 금지하는 DWG/ODA 문구 목록 확인.
- 산출: 3개 배선점 메모(쌍 그룹핑 키 · 변환 트리거 · 금지 문구).
- 검증: 실측 필드/호출
- 의존: 없음

### S2. 폴더 배치 (PB1)  (복잡도: 중)
- 무엇을: 러너 입력이 디렉터리면 단일-파일 강제 경로 우회 → 폴더 스캔/매칭 사용. `pilot_spotcheck.md`를 쌍별 그룹(쌍 헤더 + 검출행), csv는 drawing_label로 쌍 구분. 단일 파일 입력은 기존 경로 보존(분기만).
- 산출: 폴더 분기 + 그룹 출력.
- 검증: T-PB1
- 의존: S1 →

### S3. DWG 온램프 (PB2)  (복잡도: 중)
- 무엇을: `.dwg` 입력 감지 → 기존 승인 변환 경로 라우팅(backend mode 배선). 변환 불가/ODA 부재 시 fail-loud(사전변환 안내). 정책 문구 준수.
- 산출: DWG 라우팅 + fail-loud.
- 검증: T-PB2
- 의존: S1 →

### S4. 결정적 테스트 + 비퇴행 (PB3/PB4)  (복잡도: 중)
- 무엇을: 골든 다중쌍 폴더 통합 테스트 + DWG 라우팅 헤드리스 단위테스트(real ODA 불요, 배선/fail-loud 단언). 기존 단일쌍 테스트 보존. black/isort clean.
- 산출: 테스트(클린).
- 검증: T-PB3, T-PB4
- 의존: S2,S3 →

### S5. 가이드 + dogfood + 게이트 (PB5)  (복잡도: 낮)
- 무엇을: INTERNAL_PILOT_SPOTCHECK.md에 폴더/DWG 사용법. `cad_policy_gate` 그린 확인. per-PR 목록 유지.
- 검증: T-PB5
- 의존: S2~S4 →

## 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| 폴더 배치가 compare 재구현 유발 | 상 | FolderComparePipeline 폴더 스캔 그대로 호출. 출력 그룹핑만. |
| DWG 온램프가 정책 게이트 위반(ODA 문구/완전지원) | 상 | 기존 변환 경로 재사용·"완전지원" 미주장·fail-loud. S1서 금지문구 확인. |
| ODA 부재 환경서 침묵 다운그레이드 | 중 | fail-loud 명시([[oda_dual_path_slim_gap]] 교훈: 침묵 빈결과 금지). |
| `top_issues` 쌍 식별 빈약 | 중 | S1 실측. display_label/pair_id로 그룹. 빈약 시 drawing_change_brief.csv(쌍별) 보강. |
| 단일쌍 회귀 | 상 | PB3 기존 테스트 보존, 분기만 추가(기존 경로 무변경). |
| 골든 폴더에 쌍 매칭 어려움 | 중 | 골든 쌍 폴더 구성 실측(S1). 안 되면 임시 폴더에 before/after 복사 fixture. |

## 변경 이력
- 2026-06-27 생성: PR#56(단일쌍 러너) 후속. 실데이터(DWG 폴더) 인에이블먼트로 dry-run 실현. 엔진 기존 배치+변환 배선.
