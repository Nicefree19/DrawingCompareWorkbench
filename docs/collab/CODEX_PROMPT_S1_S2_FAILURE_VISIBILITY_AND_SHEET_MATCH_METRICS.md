# Codex 작업 프롬프트 — S1 + S2

이 문서는 코덱스 세션 시작 시 그대로 복사·붙여넣기로 사용하기 위한
자기충족 프롬프트입니다. **이 문서 자체가 코덱스에게 보낼 메시지의 본문**입니다.

---

너는 이 저장소의 선임 개발 에이전트다. 한국어로 보고하고, 코드를 먼저 읽고 근거 기반으로 판단해라.

## 작업 위치
`D:\00.Work_AI_Tool\DrawingCompareWorkbench`

## 현재 상태 (재확인 사항)
- main = bdeb1e8 (병합 직후 상태, 전체 unit 3229 passed / 2 skipped)
- 미커밋 변경 없음 (worktree 정상)
- 사용자 불만 5건은 [docs/collab/DRAWING_COMPARE_PERFORMANCE_DEGRADATION_TECHSPEC_ROADMAP.md] 본문에 누적 기록됨

## 이 세션의 범위 = S1 + S2 **둘만**

**S1: 렌더/처리 실패의 사용자 가시화** (1주 안에 재현 가능해야 함)
**S2: 다중 시트 도곽 매칭의 정확도 메트릭화** (recall/precision, fixture 3–5장)

이 외 모든 작업은 **이 세션의 범위가 아니다**. 특히 다음 작업은 절대 하지 마라:
- 새로운 P5-G* 게이트 신설 (G31 이상 금지)
- `src/gui/drawing_compare_workbench.py`에 줄 추가 (모든 신규 위젯은 별도 모듈)
- PDF-first 전환의 구현·skeleton·prototype 코드 (ADR-001이 별도 진행 중이므로 결정 전 코드 작성 금지)
- monolith 분해 리팩토링
- 14,105줄짜리 파일을 더 키우는 것

## S1 상세

### 목적
silent fallback을 정직한 fallback으로 바꾼다. 사용자가 viewport를 보고
"지금 fallback이다 / 지금 부분 실패다 / 지금 캐시 DXF 본다"를 즉시 안다.

### 확인된 silent fallback 7개 지점 (먼저 코드 읽고 검증해라)
1. [src/services/comparison/zone_vector_renderer.py:631] `Zone SVG draw failed after N accepted entities` (부분 성공)
2. [src/services/comparison/zone_vector_renderer.py] `DWG_UNSUPPORTED_VERSION` (AC1015 외 DWG → 캐시 DXF fallback)
3. [src/gui/lightweight_viewport.py:98-109] `_FallbackQuickWidget` (QQuickWidget unavailable)
4. [src/gui/lightweight_viewport.py] `QSGLineItem unavailable; using Canvas skeleton` (테스트마다 160+ 회, INFO 노이즈)
5. [src/services/comparison/ai_classifier/embedding_classifier.py] embedding backend 미배치 → heuristic-only
6. DWG → cached DXF fallback (`Reusing shared DWG DXF cache`)
7. Zone crop stale / cancel — UI에 stale 이유 미노출

각 지점이 정말 silent인지 먼저 코드로 확인하고, 이미 가시화돼 있다면 그 지점은
스킵하고 보고서에 "이미 가시화됨" 으로 명시.

### 산출물
1. **신규 모듈**: `src/services/comparison/render_failure_codes.py`
   - `RenderFailureCode` enum: `OK`, `DWG_UNSUPPORTED_VERSION`,
     `VECTOR_DRAW_PARTIAL`, `VECTOR_DRAW_FAILED`, `BACKEND_FALLBACK_QQUICKWIDGET`,
     `BACKEND_FALLBACK_CANVAS_SKELETON`, `AI_HEURISTIC_FALLBACK`,
     `DWG_USING_CACHED_DXF`, `ZONE_CROP_STALE`, `ZONE_CROP_CANCELLED`
   - 각 code에 한국어 사용자 메시지 + 영문 진단 코드 + severity (info/warn/error) 필드
2. **신규 GUI 모듈**: `src/gui/failure_badge.py`
   - `FailureBadge` 위젯: 상단에 작은 색상 칩(녹=ok 숨김/노=warn/빨=error)
   - 클릭 시 상세 사유 다이얼로그 (메시지 + 발생 시각 + 로그 deep link 텍스트)
   - **`drawing_compare_workbench.py`에 import 1줄 + viewport 위에 add 1줄만** — 그 외 본체 수정 금지
3. **로그 throttling**: `QSGLineItem unavailable` 같은 once-per-session 노이즈는
   첫 발생만 INFO, 이후는 DEBUG로 throttle. 신규 helper:
   `src/utils/once_per_session_logger.py`
4. **회귀 테스트** (`tests/unit/services/comparison/test_render_failure_codes.py`,
   `tests/unit/gui/test_failure_badge.py`):
   - 7개 지점 각각이 정확한 `RenderFailureCode`를 발신하는지
   - 배지가 enum severity에 맞는 색을 띠는지
   - throttled logger가 첫 호출만 INFO인지

### 검증 가능 정의 (DoD)
- 명령: `python -m pytest tests/unit/services/comparison/test_render_failure_codes.py tests/unit/gui/test_failure_badge.py -v`
- 통과 시 발신된 코드/배지 색을 stdout에 출력
- workbench_acceptance_smoke에 "AC1024 DWG 시도 → 배지 노란색 + 메시지 'AC1024 DWG: 호환 캐시 DXF로 비교' 표시" assert 1줄 추가
- 새 `RenderFailureCode` 값이 7개여야 함 (8개 이상이면 스코프 초과 또는 사전 합의 필요)

### 금지 사항 (재강조)
- `drawing_compare_workbench.py`에 add line 수 ≤ 2 (failure_badge import 1줄, viewport 추가 1줄). 초과 시 PR reject.
- 새 P5-G* 게이트, 새 audit script, 새 customer evidence 파일 생성 금지
- 기존 fallback 동작 변경 금지 — 오직 가시화만

---

## S2 상세

### 목적
multi-sheet (한 파일에 여러 도면) 매칭의 recall/precision을 처음으로 메트릭화.
지금 R0~R10 region detection 코드는 있지만 "**같은 시트끼리 정확히 매칭됐는가**"의
숫자 게이트가 없다. 그 게이트만 정의·구현.

### 사전 조건: fixture 확보 (이게 안 되면 S2는 stop)
real fixture가 없으면 합성 fixture로 시작하되, 다음 절차를 따라라:
1. `tests/data/multi_sheet/` 디렉토리 신설 (없으면)
2. 다음 3가지 fixture 종류 각 1개씩 만들기 (DXF 합성 생성 스크립트로 OK):
   - `2sheets_clear.dxf` — 도곽 2개, 시트번호 명확, 매칭 신뢰도 high 기대
   - `3sheets_ambiguous.dxf` — 도곽 3개, 시트번호 1개 누락, 1개 매칭 hold 기대
   - `5sheets_one_renamed.dxf` — 도곽 5개, before→after 사이 1개 도면번호 변경, manual match 필요 기대
3. 각 fixture에 같은 이름 `*_after.dxf` 짝 생성 (실제 차이가 있어야 함)
4. **fixture가 합성이면 manifest에 명시**: `synthetic=true` 플래그
5. real fixture 확보가 사용자 결정에 종속이므로, **fixture 합성 자동화 스크립트**도
   결과물에 포함: `scripts/build_multi_sheet_fixtures.py`

### 산출물
1. **메트릭 정의 모듈**: `src/services/comparison/sheet_match_metrics.py`
   - 입력: before/after region detection 결과 + ground truth 매칭 manifest
   - 출력: `precision`, `recall`, `f1`, `manual_match_required_count`,
     `false_match_count`, `unmatched_count`, `confidence_distribution`
2. **fixture 빌더**: `scripts/build_multi_sheet_fixtures.py`
   - 위 3개 합성 fixture를 ezdxf로 생성
   - 합성 manifest `multi_sheet_ground_truth.json` 동시 출력
3. **벤치마크 명령**: `scripts/benchmark_sheet_match_accuracy.py`
   - 위 fixture 셋을 비교 파이프라인에 통과시키고 메트릭 출력
   - JSON 산출: `.benchmarks/sheet_match_accuracy_synthetic.json`
4. **회귀 테스트**:
   - `tests/unit/services/comparison/test_sheet_match_metrics.py`
   - 정확도 게이트 (합성 기준): `precision ≥ 0.95`, `recall ≥ 0.90`, `false_match_count == 0`
   - **합성에서 이 게이트를 통과 못 하면 region detection 알고리즘 버그를 먼저 잡아라** —
     S2는 메트릭이 사실을 노출하는 것이 목적

### 검증 가능 정의 (DoD)
- 명령 1: `python scripts/build_multi_sheet_fixtures.py --out tests/data/multi_sheet`
- 명령 2: `python scripts/benchmark_sheet_match_accuracy.py --fixture-root tests/data/multi_sheet --out .benchmarks/sheet_match_accuracy_synthetic.json`
- JSON에 `synthetic=true`, `precision`, `recall`, `f1`, `confidence_distribution` 필드
- 명령 3: `python -m pytest tests/unit/services/comparison/test_sheet_match_metrics.py -v`
- 합성 게이트 통과 시 stdout에 "ready to gate real fixtures" 메시지

### 금지 사항
- 합성 fixture로 만든 게이트를 **customer-grade evidence로 인증하지 마라**
- 새 P5-G* 이름 사용 금지. 이 메트릭은 **별도 namespace `sheet_match_*`**
- region detection 알고리즘 자체의 큰 리팩토링 금지. 메트릭이 노출만 하라

---

## 보고 형식 (양쪽 공통)

세션 종료 시 다음 1개 마크다운 파일로 보고:
`docs/collab/CODEX_S1_S2_COMPLETION_REPORT.md`

다음 섹션 포함:
1. **S1 산출물 목록** (실제 추가된 파일 경로 + 라인 수)
2. **S1 검증 결과** (위 DoD 명령 출력)
3. **S1 발견된 silent fallback의 실제 개수** (예측 7개 중 실제 N개)
4. **S2 산출물 목록**
5. **S2 fixture 종류 + manifest**
6. **S2 합성 메트릭 결과** (precision/recall/f1 숫자)
7. **다음 세션을 위한 미해결 항목** (S3~S5 중 어떤 게 가장 막혀 보이는지)
8. **drawing_compare_workbench.py 라인 수 변화** (이전 14,105 → 현재 ?)
   - 2줄 초과 시 사유 명시

## 작업 원칙 재강조
- 사용자 변경을 되돌리지 마라
- 추측 금지, 파일/함수/테스트 라인 번호 인용
- 큰 리팩토링 금지, 재현 가능한 실패 우선
- UI에 실패 원인 노출
- 테스트는 unit + smoke + benchmark + evidence log

## 명시 금지 (한 번 더)
- P5-G31 이후 추가 게이트 신설 금지
- `drawing_compare_workbench.py`에 줄 추가 (>2줄) 금지
- PDF-first 구현 코드 금지
- monolith 분해 금지
- 14,105줄 파일을 더 키우는 어떤 작업도 금지

작업 시작.
