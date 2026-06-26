# PLAN — 이행 로드맵

> 작은 단위·의존성. 한 반복 = 한 단계. verify-then-fix-or-drop.

## 단계 (순서대로)

### S1. 결정성 검증 (선결)  (복잡도: 낮)
- 무엇을: 5개 결정적 후보 테스트를 각각 단독 + 2회 연속 실행해 per-PR 안전(빠름·flaky 아님) 확인. GUI 테스트는 offscreen 결정성 별도 평가.
- 산출: 후보별 통과/시간/결정성 표(STATUS 검증로그).
- 검증: T4
- 의존: 없음

### S2. per-PR 목록에 추가 (C1)  (복잡도: 낮)
- 무엇을: `cad-format-regression.yml`의 pytest 목록에 S1 통과 결정적 파일 추가(Windows backtick 줄연속 형식 유지).
- 산출: 워크플로 diff.
- 검증: T1
- 의존: S1 →

### S3. GUI 테스트 처리 결정 (C3)  (복잡도: 중)
- 무엇을: zone_tree_failure_surfacing 결정성 따라 — 안정적이면 목록 포함, AV-prone이면 **별도 명시 step/job**(continue-on-error 아님; 격리 사유 주석). silent-skip 금지.
- 산출: 결정 + 사유.
- 검증: T3
- 의존: S1 →

### S4. check_ci_gate 메타-가드 확장 (C2)  (복잡도: 낮)
- 무엇을: `cad_policy_gate.check_ci_gate`에 critical 테스트 파일 존재 단언 추가(기존 required_snippets 패턴 재사용). 워크플로에서 빠지면 violation.
- 산출: 게이트 코드 + tmp_path 테스트(누락 시뮬→violation).
- 검증: T2
- 의존: S2 →(목록 확정 후)

### S5. 통합 비퇴행 (C5)  (복잡도: 낮)
- 무엇을: `cad_policy_gate` 그린·기존 워크플로 step(golden floor·diff-check·policy) 보존 확인.
- 검증: T5
- 의존: S2~S4 →

## 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| 추가 테스트가 CI서 flaky(특히 GUI) | 상 | S1서 결정성 선검증. GUI는 격리. flaky면 드롭+사유. |
| e2e_smoke가 CI 환경서 느림/실패 | 중 | 로컬 ~5s 측정. CI Qt offscreen 의존성 확인. 실패시 격리. |
| check_ci_gate 확장이 기존 테스트 깸 | 중 | 누락-시뮬 tmp_path 테스트로 정밀 검증, accepts 케이스 갱신. |
| 과한 목록→CI 시간 폭증 | 하 | 결정적·빠른 것만(각 <10s 목표). |

## 변경 이력
- 2026-06-26 생성: CI-enforcement 캡스톤. cad-format-regression 하드코딩 목록에 세션 테스트 미포함 확인 기반.
