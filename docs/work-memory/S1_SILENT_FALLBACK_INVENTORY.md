# S1.2 Silent Fallback Inventory

| 항목 | 값 |
|---|---|
| 작업 슬라이스 | S1.2 |
| 작성일 | 2026-05-28 |
| 작성자 | Claude (직접 실행) |
| 검증 방법 | grep + 코드 읽기 (zone_vector_renderer.py, lightweight_viewport.py, embedding_classifier.py, dwg_importer.py, import_pipeline.py, drawing_compare_workbench.py 일부) |

---

## 1. Executive Summary

| 항목 | 결과 |
|---|---|
| 후보 지점 수 | **7개 (확장 후 8개)** |
| 이미 viewport 배지로 가시화된 지점 | **0개** |
| S1.3 작업 대상 | **모두 (8개)** |
| 추가 발견 | `DwgFailureCode` enum (dwg_importer.py:54) + `skipped_reason` 필드 (ZoneVectorRenderResult) — RenderFailureCode와 **연동** 가능 |
| 발견된 monolith 영향 추정 | drawing_compare_workbench.py에 **0줄 추가** (zone crop stale은 별도 helper로 위임) |

**결론**: silent fallback 7개 후보가 모두 실제로 silent하며, S1.3에서 8개 지점에 RenderFailureCode 발신을 추가해야 한다 (1개 후보가 두 시나리오로 분리되어 8개).

---

## 2. 상세 분류표

| # | 후보 | 파일 | 라인 | 현재 동작 | UI 가시화? | RenderFailureCode 매핑 | 우선순위 |
|---|---|---|---|---|---|---|---|
| 1 | Zone SVG draw failed | `src/services/comparison/zone_vector_renderer.py` | **631-652** | `frontend.draw_entities()` 실패 → `logger.warning` + `ZoneVectorRenderResult(skipped_reason="SVG draw failed: ...")` 반환 | ❌ `skipped_reason`은 caller로 전달되나 viewport 배지 없음 | `vector_draw_failed` | **높음** |
| 2 | DWG unsupported version | `src/services/comparison/dwg_importer.py` + `import_pipeline.py` + `zone_vector_renderer.py` | 54 / 585 / 192 | `DwgFailureCode.UNSUPPORTED_VERSION` enum + `logger.warning` + cached DXF fallback | ❌ DwgFailureCode는 진단용이지 viewport 배지 없음 | `dwg_unsupported_version` | **높음** |
| 3 | QQuickWidget unavailable | `src/gui/lightweight_viewport.py` | **71, 98-109** | `_FallbackQuickWidget` 사용 + `logger.warning` | ❌ | `backend_fallback_qquickwidget` | 중간 |
| 4 | QSGLineItem unavailable | `src/gui/lightweight_viewport.py` | **591-599** | `logger.info` ("정상 동작") + `_qsg_available = False`, `_skeleton_renderer = "canvas"` | ❌ | `backend_fallback_canvas_skeleton` | 낮음 |
| 5 | Embedding backend unavailable | `src/services/comparison/ai_classifier/embedding_classifier.py` | **422, 484** | 두 지점: background prepare (422) + 첫 classify_zone (484, **이미 cooldown 적용**) | ❌ | `ai_heuristic_fallback` | 중간 |
| 6a | DWG cached DXF 정상 재사용 | `src/services/comparison/zone_vector_renderer.py` | **139, 152, 173** | `logger.info` (Reusing shared/cached/Cached canonical) | ❌ | `dwg_using_cached_dxf` (severity=info) | 낮음 |
| 6b | DWG vector normalisation 실패 → cached DXF | `src/services/comparison/zone_vector_renderer.py` | **191-197** | `logger.warning` ("DWG vector normalisation failed for %s; using cached DXF") | ❌ | `dwg_using_cached_dxf` (severity 승격 필요) OR 신규 코드 | 중간 |
| 7 | Zone crop stale/cancel | `src/gui/drawing_compare_workbench.py` | **7920, 11782, 11798, 11955, 11972, 13179** (6개 지점) | `logger.debug` ("Ignoring stale zone crop ...") | ❌ | `zone_crop_stale` / `zone_crop_cancelled` | 낮음 |

---

## 3. 지점별 상세 분석

### Point 1 — Zone SVG draw failed (vector_draw_failed)

**파일**: [src/services/comparison/zone_vector_renderer.py:631-652](../../src/services/comparison/zone_vector_renderer.py)

```python
# L625-652
try:
    frontend = Frontend(ctx=RenderContext(doc), out=backend, config=cfg)
    try:
        frontend.set_background(background_color)
    except Exception:
        pass
    frontend.draw_entities(render_entities)
    frontend.pipeline.finalize()
except Exception as exc:
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    logger.warning(
        "Zone SVG draw failed after %d accepted entities for %s: %s",
        accepted_count[0],
        dxf_path.name,
        exc,
        exc_info=True,
    )
    return ZoneVectorRenderResult(
        svg_path="",
        entity_count=accepted_count[0],
        elapsed_ms=elapsed_ms,
        world_bbox=padded,
        truncated=truncated[0],
        skipped_reason=(
            f"SVG draw failed: {type(exc).__name__}: {exc}. "
            "The raster/background viewer should remain available."
        ),
    )
```

**S1.3 변경 제안**:
- `ZoneVectorRenderResult`에 `failure_code: RenderFailureCode = "ok"` 필드 추가
- 실패 시 `failure_code="vector_draw_failed"` 설정
- 부분 성공 (`accepted_count[0] > 0` 이지만 draw 실패)은 `vector_draw_partial`로 별도 분류 가능

### Point 2 — DWG unsupported version (dwg_unsupported_version)

**파일들**:
- [src/services/comparison/dwg_importer.py:54](../../src/services/comparison/dwg_importer.py) — `DwgFailureCode.UNSUPPORTED_VERSION = "DWG_UNSUPPORTED_VERSION"`
- [src/services/comparison/import_pipeline.py:585](../../src/services/comparison/import_pipeline.py) — 영문 메시지 매핑
- [src/services/comparison/zone_vector_renderer.py:191-197](../../src/services/comparison/zone_vector_renderer.py) — fallback logger.warning

```python
# zone_vector_renderer.py L191-197
if fallback is not None:
    logger.warning(
        "DWG vector normalisation failed for %s; using cached DXF %s: %s",
        src.name,
        fallback.name,
        exc,
    )
    return fallback
```

**S1.3 변경 제안**:
- `DwgFailureCode` enum을 폐기하지 말고 그대로 유지
- `RenderFailureCode.dwg_unsupported_version` ← `DwgFailureCode.UNSUPPORTED_VERSION` 매핑 함수 1개 추가
- import_pipeline에서 DWG 실패 시 RenderFailureCode 발신 (예: ImportPipelineResult에 failure_code 필드)

### Point 3 — QQuickWidget unavailable (backend_fallback_qquickwidget)

**파일**: [src/gui/lightweight_viewport.py:71, 98-109](../../src/gui/lightweight_viewport.py)

```python
# L98-109
def _create_quick_widget(parent: QWidget) -> QWidget:
    try:
        widget = QQuickWidget(parent)
        if not isinstance(widget, QWidget):
            raise TypeError(f"QQuickWidget returned non-QWidget {type(widget)!r}")
        return widget
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "QQuickWidget unavailable or invalid (%s); using QWidget fallback",
            exc,
        )
        return _FallbackQuickWidget(parent)
```

**S1.3 변경 제안**:
- `_FallbackQuickWidget`에 `failure_code = "backend_fallback_qquickwidget"` 클래스 속성 추가
- `LightweightDrawingViewport.__init__`에서 `isinstance(self._quick, _FallbackQuickWidget)` 검사 → 진입 시 failure_code 발신 signal

### Point 4 — QSGLineItem unavailable (backend_fallback_canvas_skeleton)

**파일**: [src/gui/lightweight_viewport.py:591-599](../../src/gui/lightweight_viewport.py)

```python
# L591-599
try:
    from src.gui.qsg_line_item import register_qml_type
    register_qml_type()
    self._qsg_available = True
except Exception as exc:  # noqa: BLE001
    logger.info(
        "QSGLineItem unavailable (%s); using Canvas skeleton", exc
    )
    self._qsg_available = False
```

**S1.3 변경 제안**:
- `self._qsg_available = False`일 때 `failure_code = "backend_fallback_canvas_skeleton"` 등록
- **S1.5 once_per_session_logger 적용**: pytest 로그에서 160+ 회 반복되는 INFO 노이즈 제거

### Point 5 — Embedding backend unavailable (ai_heuristic_fallback)

**파일**: [src/services/comparison/ai_classifier/embedding_classifier.py:422, 484](../../src/services/comparison/ai_classifier/embedding_classifier.py)

**두 지점**:
- **L422 (background prepare)**: `_prepare_async_target` 안에서 `BackendUnavailableError` 처리
- **L484 (classify_zone 첫 호출)**: Phase N hotfix — `_last_error` cooldown으로 첫 한 번만 warning, 이후 silent

```python
# L478-488
try:
    self.prepare()
except BackendUnavailableError as exc:
    # _last_error is set inside prepare() — this branch
    # only fires on the very first failure, so the warning
    # appears exactly once per dispatcher lifetime.
    logger.warning(
        "Embedding backend unavailable, abstaining "
        "(further attempts will be silent until config "
        "reload): %s", exc,
    )
    return None
```

**중요 발견**: 이 cooldown 패턴이 **S1.5 `once_per_session_logger`의 참고 모델**. 비슷한 throttling을 generic helper로 추출.

**S1.3 변경 제안**:
- `EmbeddingClassifier`에 `failure_code` 속성 추가, `BackendUnavailableError` 발생 시 `"ai_heuristic_fallback"` 설정
- workbench가 classifier 상태 polling 또는 signal로 받음

### Point 6 — DWG cached DXF reuse (dwg_using_cached_dxf, 두 시나리오)

**파일**: [src/services/comparison/zone_vector_renderer.py:139, 152, 173, 191-197](../../src/services/comparison/zone_vector_renderer.py)

**시나리오 6a (정상 캐시 재사용, info)**:
- L139: `Reusing shared DWG DXF cache for ...`
- L152: `Reusing cached DXF for ...`
- L173: `Cached canonical DWG debug DXF: ...`

**시나리오 6b (실패 후 fallback, warn)**:
- L191-197: `DWG vector normalisation failed for ...; using cached DXF ...`

**S1.3 변경 제안**:
- 6a는 `dwg_using_cached_dxf` (severity=info) — viewport 배지로는 표시 안 함 (HIDDEN 아님, 그러나 noisy 가능). 대신 사용자가 클릭하면 상세 보기.
- 6b는 6a와 구분 — **`dwg_using_cached_dxf` severity를 warn으로 승격** 또는 **신규 enum `dwg_vector_normalise_failed`** 검토 필요
- **현 S1.1 enum에는 6a/6b를 구분하는 코드 없음** → S1.3 시작 시 enum 확장 결정 (1개 추가 or severity context로 분리)

### Point 7 — Zone crop stale/cancel (zone_crop_stale / zone_crop_cancelled)

**파일**: [src/gui/drawing_compare_workbench.py](../../src/gui/drawing_compare_workbench.py) **6개 지점**:
- L7920: `"Could not stop stale zone vector process"` (debug)
- L11782: `"Ignoring stale zone crop render result for inactive pair=%s zone=%s"` (debug)
- L11798: `"Ignoring stale zone crop render result for superseded request pair=%s zone=%s request=%s"` (debug)
- L11955: `"Ignoring stale zone crop render error for inactive pair=%s zone=%s"` (debug)
- L11972: `"Ignoring stale zone crop render error for superseded request pair=%s zone=%s request=%s"` (debug)
- L13179: `"Ignoring stale zone vector result for pair=%s zone=%s svg=%s expected=%s"` (debug)

**모두 monolith 안**. S1.6 freeze rule (≤2줄 추가) 위배 위험.

**S1.3 변경 제안**:
- 6개 지점을 직접 손대지 말고, 별도 helper `src/utils/zone_crop_stale_emitter.py` 신설
- monolith에서는 helper 호출 한 번만 (이미 logger.debug 호출하는 곳에 helper 호출 1줄 추가)
- **모든 6개 지점이 한 helper로 라우팅** → monolith add 라인 수 = 6줄 (한계 5줄 초과 위험)
- **대안**: zone crop stale은 S1 스코프에서 제외하고 별도 슬라이스 또는 후속 작업으로 미루기

**결정 필요**: S1 스코프 유지 vs 축소 (6번이 monolith 한계와 충돌).

---

## 4. 추가 발견 — 기존 코드와의 연동 기회

### 4.1 `DwgFailureCode` enum 이미 존재
- `dwg_importer.py:54`에 `DwgFailureCode.UNSUPPORTED_VERSION` 정의
- import_pipeline에서 한국어 메시지 매핑
- **`RenderFailureCode`와 통합하지 말고**, mapping 함수만 추가하는 게 안전
  - `def from_dwg_failure_code(code: DwgFailureCode) -> RenderFailureCode`

### 4.2 `ZoneVectorRenderResult.skipped_reason` 필드
- 이미 caller에게 fallback 사유를 전달하는 통로가 존재
- S1.3에서 `failure_code: RenderFailureCode` 필드를 추가하면 자연스럽게 연동

### 4.3 embedding_classifier의 cooldown 패턴
- Phase N hotfix가 이미 once-per-session 패턴 적용 (단, classifier별 instance-level)
- **S1.5 `once_per_session_logger`는 이 패턴을 generic하게 추출**
- helper로 만들면 embedding_classifier도 helper 사용으로 리팩토링 가능 (S1 스코프 외)

---

## 5. S1.3 작업 범위 결정

### 권장 — 표준 안 (Option A)
1. **Point 1, 2, 3, 4, 5, 6a, 6b** → S1.3 정식 처리 (7개 발신 지점, 0줄 monolith 추가)
2. **Point 7 (zone crop stale)** → **S1 스코프에서 제외**, 별도 후속 작업 (S1 완료 후 결정)
   - 이유: monolith 6개 지점에 helper 호출 추가 → freeze rule 한계 5줄 초과 위험
   - 대신 S1.6에서 FailureBadge 통합과 함께 단 1줄로 zone crop 카운터를 보여주는 가벼운 정수 표시는 가능

### 대안 안 (Option B) — Zone crop stale도 포함
- monolith에 helper 호출 6줄 추가 (한계 5줄 초과)
- → 사전 예외 요청 필요: [STRUCTURAL_FREEZE_EXCEPTION_REQUEST.md](../collab/STRUCTURAL_FREEZE_EXCEPTION_REQUEST.md) 작성
- 처리 시간 + 사용자 승인 필요

### 결정 요청
**Option A 권장**. Point 7은 별도 후속 슬라이스(S1.7 신설 또는 S2 이후)에서 monolith 분해 작업과 함께 처리.

---

## 6. enum 확장 검토 (S1.3 시작 전)

현재 [render_failure_codes.py](../../src/services/comparison/render_failure_codes.py)의 10개 코드 중:
- `vector_draw_partial` — Point 1에서 부분 성공 케이스 (accepted_count[0] > 0 + draw 실패) 가능. 적용
- `dwg_using_cached_dxf` (severity=info) — Point 6a 적용. Point 6b는 severity 승격 필요

**옵션 A**: 6b를 위해 `dwg_vector_normalise_failed` 신규 enum 추가 (총 11개)
**옵션 B**: 6b도 `dwg_using_cached_dxf` 사용, S1.3에서 발신 시 severity override 가능하게 helper 수정
**옵션 C**: 6b는 `vector_draw_partial` 재사용 (의미가 약간 다름)

**권장 옵션 A** — 의미 명확성이 미래 유지보수에 유리. enum 추가는 11개도 작은 수.

→ S1.3 시작 시 사용자에게 enum 확장 여부 한 번 더 확인.

---

## 7. 다음 단계 (S1.3 입력 contract)

S1.3 작업 시작 시 입력:
1. 본 inventory 확정 (8개 지점, Option A 스코프)
2. enum 확장 결정 (Option A 권장 — `dwg_vector_normalise_failed` 추가 또는 미추가)
3. S1.3 산출물: 각 지점에 5-10줄 추가 + 각 지점 unit test
4. 예상 변경 파일:
   - `src/services/comparison/render_failure_codes.py` (옵션 A 채택 시 enum 1개 추가)
   - `src/services/comparison/zone_vector_renderer.py` (Point 1, 6a, 6b)
   - `src/services/comparison/dwg_importer.py` (Point 2 매핑)
   - `src/services/comparison/import_pipeline.py` (Point 2 발신)
   - `src/gui/lightweight_viewport.py` (Point 3, 4)
   - `src/services/comparison/ai_classifier/embedding_classifier.py` (Point 5)
   - `tests/unit/services/comparison/test_*_failure_codes.py` (각 발신 검증)
5. **monolith 영향 0줄** (Point 7 제외 시)

---

## 8. 검증 출력

```
[1] Zone SVG draw failed         → zone_vector_renderer.py:631, :636
[2] DWG_UNSUPPORTED_VERSION      → dwg_importer.py:54, import_pipeline.py:585
[3+4] QQuickWidget/QSGLineItem   → lightweight_viewport.py:71, :109, :597
[5] Embedding backend unavailable → embedding_classifier.py:422, :484
[6] DWG cached DXF reuse         → zone_vector_renderer.py:139, :152, :173, :192
[7] Zone crop stale/cancel       → drawing_compare_workbench.py:7920, :11782, :11798, :11955, :11972, :13179
```

모든 지점 코드로 검증 완료.
