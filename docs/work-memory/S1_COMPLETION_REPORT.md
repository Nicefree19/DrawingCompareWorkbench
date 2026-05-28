# S1 Completion Report — Silent Fallback Visibility

| 항목 | 값 |
|---|---|
| Work Item | S1 |
| 완료일 | 2026-05-28 |
| 작성자 | Claude (직접 실행) |
| 근거 ADR | [ADR-001 PDF-first transition](../adr/ADR-001-pdf-first-transition.md) |
| 제약 | [AGENTS.md Structural Freeze Rules](../../AGENTS.md) |
| PR | [#3 claude/s1-failure-visibility](https://github.com/Nicefree19/DrawingCompareWorkbench/pull/3) |
| 슬라이스 | 10/10 완료 (S1.1, S1.2, S1.3.1-5, S1.4, S1.5, S1.6) |

---

## 1. Executive Summary

S1 silent-fallback visibility 로드맵의 모든 슬라이스가 완료됨. silent fallback 발신 정초 (RenderFailureCode taxonomy + 5개 모듈 통합) → GUI badge (FailureBadge widget) → workbench monolith 통합 (+4 lines)까지 직선적으로 완성. 사용자가 viewport 위 status bar에서 silent fallback을 즉시 인지 가능한 상태가 됨.

---

## 2. 산출물 매니페스트

### 신규 파일 (8개)

| 파일 | 라인 | 용도 |
|---|---|---|
| `src/services/comparison/render_failure_codes.py` | 313 | 11-code taxonomy + helper functions + DwgFailureCode bridge |
| `src/gui/failure_badge.py` | 271 | FailureBadge GUI widget + collect_viewport_failure_codes helper |
| `src/utils/once_per_session_logger.py` | 94 | log_once throttle helper |
| `tests/unit/services/comparison/test_render_failure_codes.py` | 256 | 27 tests (enum + bridge) |
| `tests/unit/services/comparison/test_zone_vector_renderer.py` (확장) | +170 | 8 신규 tests (S1.3.2 failure_codes) |
| `tests/unit/gui/test_lightweight_viewport_failure_codes.py` | 130 | 5 tests (S1.3.4 Qt fallbacks) |
| `tests/unit/services/comparison/test_ai_classifier_embedding.py` (확장) | +88 | 4 신규 tests (S1.3.5) |
| `tests/unit/gui/test_failure_badge.py` | 360 | 18 tests (FailureBadge + helper) |
| `tests/unit/utils/__init__.py` | 0 | 디렉토리 신설 |
| `tests/unit/utils/test_once_per_session_logger.py` | 153 | 7 tests (throttle helper) |

### 수정 파일

| 파일 | 라인 변화 | 변경 요약 |
|---|---|---|
| `src/services/comparison/zone_vector_renderer.py` | +44 -2 | resolve_dxf_path failure_codes 인자 + ZoneVectorRenderResult.failure_codes 필드 + 7 return 지점 |
| `src/services/comparison/ai_classifier/embedding_classifier.py` | +29 | failure_code 속성 + getter + prepare() except 통합 |
| `src/gui/lightweight_viewport.py` | +45 -1 | _FallbackQuickWidget.failure_code 속성 + LightweightDrawingViewport.render_failure_codes() API |
| **`src/gui/drawing_compare_workbench.py`** | **+4** | **monolith 통합 (lazy import + FailureBadge + statusBar + set_codes)** |
| `dwg_importer.py`, `import_pipeline.py` | 0 | 수정 없음 (bridge는 외부에서) |

### 신규 docs

| 문서 | 용도 |
|---|---|
| `docs/adr/ADR-001-pdf-first-transition.md` | PDF-first 결정 (rubber-stamp Accepted) |
| `docs/work-memory/S1_FAILURE_VISIBILITY_IMPLEMENTATION_PLAN.md` | S1 6 슬라이스 plan |
| `docs/work-memory/S1_CONTEXT_NOTES.md` | 컨텍스트, 결정, 패턴, 열린 질문 |
| `docs/work-memory/S1_TASK_CHECKLIST.md` | 단계별 체크리스트 |
| `docs/work-memory/S1_SILENT_FALLBACK_INVENTORY.md` | 7개 후보 정밀 분석 |
| `docs/work-memory/S1_COMPLETION_REPORT.md` | 본 문서 |

### 부가 변경

| 파일 | 변경 |
|---|---|
| `.gitignore` | `!docs/work-memory/**` + `!docs/adr/**` 예외 추가 |
| `AGENTS.md` | Structural Freeze Rules 3건 추가 (이 작업 자체의 제약) |

---

## 3. monolith 변화 (Structural Freeze Rule 준수)

| 시점 | line count |
|---|---|
| S1 시작 전 | **14,105** |
| S1 완료 후 | **14,109** |
| 차이 | **+4** (Freeze Rule "≤5줄" 한계 내) ✓ |

**Freeze Rule 준수 검증**:
- S1.1 ~ S1.5: monolith 0줄 (helper/위젯/테스트만)
- S1.6: monolith +4줄 (정확히 lazy import 1 + badge create 1 + statusBar add 1 + set_codes 1)

---

## 4. silent fallback 작업 결과

| # | 후보 | 작업 결과 |
|---|---|---|
| 1 | Zone SVG draw failed | ✅ `vector_draw_failed` 발신 통합 (S1.3.2) |
| 2 | DWG unsupported version | ✅ `from_dwg_failure_code` bridge (S1.3.3) |
| 3 | QQuickWidget unavailable | ✅ `_FallbackQuickWidget.failure_code` + viewport API (S1.3.4) |
| 4 | QSGLineItem unavailable | ✅ `backend_fallback_canvas_skeleton` 발신 (S1.3.4) |
| 5 | Embedding backend unavailable | ✅ `EmbeddingClassifierDispatcher.failure_code()` (S1.3.5) |
| 6a | DWG cached DXF normal reuse | ✅ `dwg_using_cached_dxf` (info) 발신 (S1.3.2) |
| 6b | DWG vector normalisation failed → cached | ✅ `dwg_vector_normalise_failed` (warn) 발신 (S1.3.2) |
| 7 | Zone crop stale/cancel | ⏭ **S1 스코프에서 제외** (사용자 결정 Option A — monolith 6개 지점이 Freeze Rule 충돌). 후속 작업 |

**커버리지**: 7 후보 중 6 통합 + 1 후속 (zone crop stale).

---

## 5. 테스트 결과 (전체)

| 항목 | 값 |
|---|---|
| 전체 unit suite (S1 완료 후) | **3298 passed, 2 skipped in 151.73s** |
| S1 시작 시 baseline | 3229 passed |
| S1으로 추가된 테스트 | **+69 tests** |
| 회귀 | **0건** |

### 슬라이스별 테스트 추가

| 슬라이스 | 신규 테스트 수 |
|---|---|
| S1.1 RenderFailureCode enum | 22 |
| S1.3.1 enum 확장 | 2 |
| S1.3.2 zone_vector_renderer | 8 |
| S1.3.3 from_dwg_failure_code | 3 |
| S1.3.4 lightweight_viewport | 5 |
| S1.3.5 embedding_classifier | 4 |
| S1.4 FailureBadge widget | 14 |
| S1.5 once_per_session_logger | 7 |
| S1.6 collect_viewport_failure_codes helper | 4 |
| **합계** | **69** |

---

## 6. PR #3 commits (12개)

```
01fc15e docs: add S1+S2 codex prompt, ADR-001 PDF-first, freeze rules
c92a119 Merge branch 'claude/pensive-morse-45dc3f': ...
d74ede8 feat(comparison): S1.1 add RenderFailureCode enum with Korean messages
40d5650 fix: include work-memory + adr docs in version control
495b46b docs(s1.2): inventory silent fallback points for S1 visibility work
cf0fc9a feat(comparison): S1.3.1 add dwg_vector_normalise_failed code
dbd2be1 feat(comparison): S1.3.2 integrate failure_codes in zone_vector_renderer
0b53de2 feat(comparison): S1.3.3 add from_dwg_failure_code bridge
535170e feat(gui): S1.3.4 surface Qt backend fallbacks in lightweight viewport
f36cc77 feat(comparison): S1.3.5 surface embedding-backend fallback as failure_code
78cfae8 feat(gui): S1.4 add FailureBadge widget
49d21ee feat(utils): S1.5 add once_per_session_logger helper
<S1.6 commit pending>
```

---

## 7. 사용자가 받는 가치

이전: silent fallback 6개가 모두 log에만 있고 UI 무반응 → 사용자가 "왜 안 되는지 모름" 통증.

이후: viewport 인스턴스화 시점에 발생한 silent fallback이 **workbench status bar에 색상 칩으로 표시**. 클릭하면 한국어 메시지 + 권장 조치 + 진단 코드 다이얼로그.

**최초 demo 시나리오** (S1 코드만으로 가능):
1. 사용자가 V2 viewport를 활성화 (Ctrl+L)
2. 패키지 빌드에 `qsg_line_item`이 없으면 status bar에 회색 칩 "ℹ️ 알림 1건"
3. 클릭 → "QSGLineItem 모듈 없음 — 표준 Canvas 렌더링을 사용합니다 (정상 동작)" 표시

---

## 8. 미해결 / 후속 작업

### S1 후속 (단기)
- **Point 7 (zone crop stale)** 통합: monolith 6개 지점에 helper 호출 추가. Freeze Rule 예외 요청 또는 monolith 분해와 함께
- **Runtime update**: 현재 badge는 V2 init 시점 codes만 표시. DWG 변경 같은 runtime fallback도 update 필요. signal/timer 기반 polling 디자인
- **smoke 테스트 보강**: workbench_acceptance_smoke.py에 "AC1024 DWG → 노란 배지 + 메시지" 시나리오 assert

### once_per_session_logger 적용 (단기)
- S1.5 helper는 생성만 됨. 실제 noisy log (`QSGLineItem unavailable` 160+ 회 등)에 적용은 별도 PR

### 더 큰 후속
- **S2**: multi-sheet 매칭 메트릭 (코덱스 프롬프트의 다음 트랙)
- **monolith 분해**: 14,109줄 → V1 제거 + V2를 5-7 모듈로 분리 (MVP 1.0 후)
- **ADR-002**: PySide6.QtPdf LGPLv3 법무 검토 (별도 트랙)
- **ADR-003**: AC1015 외 DWG의 사용자 안내 UX (S1.6 다이얼로그와 연동)

---

## 9. Lessons Learned (다음 작업에 반영)

### 잘 된 것
- **Layer 0 Work Memory 프로토콜** 준수가 큰 통합 작업의 안전성 보장 (각 슬라이스 단위 commit, 회귀 0)
- **Helper로 monolith 로직 외부화**: S1.6의 `collect_viewport_failure_codes()`처럼 monolith에는 호출 1줄, 로직은 외부 모듈
- **단일 발신점 디자인** (S1.3.5 prepare()의 except에서만 발신): caller 2개 모두 cover
- **`.gitignore` 사전 차단 발견** (`*_PLAN.md` 패턴): 첫 commit 직후 발견하고 fix commit으로 깔끔 해결

### 향후 개선
- **gitignore 검토 우선**: 새 디렉토리 만들 때 `.gitignore` 영향 먼저 확인
- **monolith 안 코드 변경 전 정확한 layout context 파악**: 5602-5631 영역을 미리 봤다면 S1.6 설계 더 빠름
- **Runtime update 디자인 미리**: S1.6 init-only는 demo로는 OK지만 사용자 가치는 runtime update에서 더 큼. 다음 PR에서 추가

---

## 10. Memory 영속화 권장

향후 세션에서 활용할 1건 (이 보고서의 핵심):

> "S1 silent fallback visibility 완료 (2026-05-28, PR #3). RenderFailureCode 11-code taxonomy + 6 silent fallback 통합 + FailureBadge GUI widget + monolith +4줄 통합. 14,105 → 14,109줄. zone crop stale (Point 7)은 후속. Runtime update와 once_per_session_logger 적용은 별도 PR."

→ `memory/s1_completion.md`로 저장 권장.
