# S1 Context Notes

| 항목 | 값 |
|---|---|
| 세션 시작 | 2026-05-28 |
| 담당 | Claude (직접 실행) |
| 관련 plan | [S1_FAILURE_VISIBILITY_IMPLEMENTATION_PLAN.md](S1_FAILURE_VISIBILITY_IMPLEMENTATION_PLAN.md) |

## 1. Project Context (프로젝트 컨텍스트)

### 1.1 기원
TEKLA_MCP 모노레포에서 도면 비교 모듈만 추출된 독립 프로젝트.
ODA-free 정책 유지 ([THIRD_PARTY_LICENSE_POLICY.md](../THIRD_PARTY_LICENSE_POLICY.md)).
DWG 네이티브 지원은 AC1015만, 그 외는 사용자가 DXF/PDF로 사전 변환.

### 1.2 현재 진척도
- MVP score: 9.6/10 (25/26 게이트). 미해결 1건은 실고객 evidence 부재
- 단위 테스트: 3229 passed (commit c92a119 시점)
- 코드 라인: src 160 파일, tests 189 파일

### 1.3 사용자 불만 (이 작업의 동기)
1. "실제 차이가 있는 도면인데 비교 실패"
2. "선택구역 렌더 실패"
3. "벡터 렌더 실패로 뷰어가 비어 있음"
4. "이전보다 너무 느림"
5. "개선점을 체감하기 어려움"

→ 불만 1, 2, 3, 5는 **silent fallback의 다양한 발현**. S1으로 1차 대응.

## 2. Key Decisions (핵심 결정)

### 2.1 ADR-001 (Accepted, 2026-05-28)
PDF-first viewer + CAD entity diff as truth layer (옵션 D 변형).
근거: [docs/adr/ADR-001-pdf-first-transition.md](../adr/ADR-001-pdf-first-transition.md)

### 2.2 Structural Freeze Rules (AGENTS.md, 2026-05-28)
1. `drawing_compare_workbench.py` 줄 추가 동결 (단일 PR ≤ 5줄)
2. P5-G* 게이트 신설 동결
3. PDF-first 구현 코드 동결 (ADR sign-off 전)

S1 작업 자체가 이 규칙의 첫 자기 준수 테스트.

### 2.3 Memory에 박힌 패턴 (모두 이번 분석에서 생성)
- [gate_inflation_risk](C:\Users\user\.claude\projects\D--00-Work-AI-Tool-DrawingCompareWorkbench\memory\gate_inflation_risk.md)
- [workbench_monolith](C:\Users\user\.claude\projects\D--00-Work-AI-Tool-DrawingCompareWorkbench\memory\workbench_monolith.md)
- [silent_fallback_pattern](C:\Users\user\.claude\projects\D--00-Work-AI-Tool-DrawingCompareWorkbench\memory\silent_fallback_pattern.md)
- [pdf_first_decision](C:\Users\user\.claude\projects\D--00-Work-AI-Tool-DrawingCompareWorkbench\memory\pdf_first_decision.md)

## 3. Discovered Patterns (발견된 패턴)

### 3.1 monolith 14,105줄
`src/gui/drawing_compare_workbench.py`에 V1(`DrawingCompareWorkbench` L2965)과
V2(`DrawingCompareWorkbenchV2` L4468) 두 메인 윈도우 공존.
V1은 사실상 dead code 의심.
워커 클래스 6+ 개도 같은 파일에 있음 (ScanWorker, CompareWorker,
AutoFolderCompareWorker, PairPreviewRenderWorker, VisibleTileWindowWorker,
FullZoneTreeOverlayLoadWorker, FullZoneTreePlanWorker 등).

S1 작업은 이 파일에 **정확히 +2줄만 추가** (import 1 + FailureBadge 인스턴스화 1).

### 3.2 게이트 인플레이션
P5-G1 ~ P5-G30가 1주 안에 누적됨 (2026-05-22 ~ 05-28). 모든 Codex 단독 작업.
S1은 **새 P5-G* 게이트를 일절 만들지 않는다**. 단순히 enum/위젯/테스트만.

### 3.3 silent fallback 7개 지점 (초기 가설)
S1.2에서 정밀 검증 예정. 일부는 이미 가시화돼 있을 수 있음.

## 4. Open Questions (열린 질문)

| # | 질문 | 답변 시점 |
|---|---|---|
| Q1 | `_FallbackQuickWidget` 진입은 실제로 사용자 환경에서 얼마나 발생하나? | S1.2 inventory |
| Q2 | `QSGLineItem` 모듈은 의도된 미존재인가, 빌드 누락인가? | S1.2 inventory |
| Q3 | `Embedding backend unavailable` 경고는 사용자가 `ai_models/` 디렉토리 배치 가이드를 봤는가? | 사용자 확인 필요 |
| Q4 | AC1015 외 DWG fallback 시 사용자에게 변환 가이드 모달을 띄울 것인가? | ADR-003 (후속) |
| Q5 | `once_per_session_logger`는 ENV var (`DRAWING_COMPARE_LOG_UNTHROTTLED=1`)로 우회 가능해야 하나? | S1.5 설계 시 |

## 5. Related Files (관련 파일)

### 5.1 읽어야 할 (S1.2 inventory 단계)
- `src/services/comparison/zone_vector_renderer.py` (전체)
- `src/services/comparison/ai_classifier/embedding_classifier.py`
- `src/gui/lightweight_viewport.py` (전체)
- `src/services/comparison/dxf_read.py` (DXF sanitization 경로)

### 5.2 참조만
- `src/services/comparison/render_modes.py` — 기존 RenderMode 패턴 참고
- `src/services/comparison/viewer_manifest_v3.py` — ScenePackRef 참고
- `src/services/comparison/transform.py` — 좌표 변환 참고

### 5.3 손대지 말 것
- `src/gui/drawing_compare_workbench.py` (S1.6 +2줄 외 금지)
- 기존 P5-G* 게이트 관련 모든 파일
- PDF-first 관련 모든 파일 (qt_pdf_adapter, pdf_display_list_cache 등)

## 6. Communication Rules (의사소통 규칙)

- 사용자 보고는 한국어
- commit message는 영어 (기존 패턴 유지)
- 코드 내 주석은 영어 (기존 패턴 유지)
- 사용자 가시 메시지(`message_ko`)는 한국어
- 각 슬라이스 종료 시 한국어 요약 보고 + 사용자 확인 요청
