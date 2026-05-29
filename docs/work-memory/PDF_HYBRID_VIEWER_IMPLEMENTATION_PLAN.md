# PDF-first 하이브리드 뷰어 — Implementation Plan (Layer 0)

| 항목 | 값 |
|---|---|
| Work Item | PVH (PDF-first Viewer Hybrid) |
| 작성일 | 2026-05-29 |
| 작성자 | Claude (직접 실행) |
| 근거 ADR | [ADR-003 PDF-first 하이브리드 뷰어](../adr/ADR-003-pdf-first-hybrid-viewer.md) (Accepted) |
| 제약 | [AGENTS.md Structural Freeze Rules](../../AGENTS.md) |
| 상태 | Planning → Ready (H1부터) |

> 컨텍스트(통증·실측·결정·재활용/신규)는 [ADR-003](../adr/ADR-003-pdf-first-hybrid-viewer.md)이 담는다. 이 문서는 ADR §3 "신규 5개"를 실행 슬라이스로 분해한다.

---

## 1. Objective

DWG entity diff(검출, 요소 단위 의미)를 **사용자 PDF(표시, 600dpi 무한줌) 위에 정합 오버레이**하여, "전체 자유 탐색 + 줌 디테일 + 요소 단위 정확한 차이"를 동시에 달성.

**비목표**:
- PDF visual diff를 차이 검출 truth로 쓰지 않음 (실측: region bbox만, 의미 없음)
- monolith `drawing_compare_workbench.py` 줄 추가 (Freeze Rule, 신규는 별도 모듈)
- 새 P5-G* 게이트 신설 금지

## 2. 실측 전제 (ADR §1.4)
- 고객 PDF = 1페이지 A3 이미지 PDF (텍스트/벡터 0, 7015×4982 JPEG ~600dpi)
- PDF-PDF visual diff = 1.59초, 16 region (image_pixels bbox만)
- DWG diff = 68초, 350K entity (cad_wcs_mm, 의미 보유)

## 3. 슬라이스 분해 (5개)

### H1 — `cad_wcs_mm → image_pixels / pdf_page_points` 변환 ⭐ 첫 슬라이스
- **신규**: `transform.py`에 변환 함수 (순수 수학, 열린 질문 무관)
  - `cad_world_to_image_pixels_bbox(bbox, *, affine_params)` 또는 도곽 기반 fit
  - 기존 `fit_world_to_pixels` + `pdf_page_points_to_image_pixels_bbox` 조합
- **DoD**: round-trip 테스트 (cad→pixel→cad 오차 < 0.01), Y축/원점 정확
- **monolith**: 0줄
- **의존**: 없음 — S1.1처럼 정초

### H3 — manifest `display_overlay_space` 필드 (H1 다음)
- **신규**: `viewer_manifest_v3.py`에 `display_overlay_space` (검출공간 ≠ 표시공간 허용)
  - ADR §3 "less invasive option b" — `source_kind` 고정 대신 표시 공간 별도 필드
- **DoD**: v3 manifest round-trip, 하위호환(필드 없으면 기존 동작)
- **monolith**: 0줄
- **의존**: 없음 (스키마)

### H2 — DWG 도곽 ↔ PDF 페이지 정합 (H1 사용)
- **신규 모듈**: `src/services/comparison/cad_pdf_alignment.py`
  - DWG 도곽 bbox 검출 + PDF 페이지(image_pixels @ dpi) fit affine
  - `transform_quality`: exact / estimated / relative_only
- **DoD**: A3 도곽 ↔ A3 PDF 정합 오차 < 5px @ 150dpi (실측 PDF로 검증)
- **monolith**: 0줄
- **의존**: H1 (변환), ⚠️ 열린질문 §8-6 (plot 편차) — exact/estimated로 degrade

### H4 — DWG diff bbox를 표시 좌표로 emit (H1+H2+H3)
- **변경**: `change_zones.py` / `zone_render_service.py`
  - DWG diff 결과(cad_wcs_mm)를 H2 정합으로 image_pixels 변환 후 manifest emit
  - `display_overlay_space` 기록 (H3)
- **DoD**: DWG diff 16개(예) → PDF 위 정확한 위치 오버레이 좌표 생성, 회귀 0
- **monolith**: 0줄
- **의존**: H1, H2, H3

### H5 — DWG + PDF 페어링 (열린질문 의존, 마지막)
- **변경**: `drawing_batch.py` `are_compatible` 확장 (현재 mixed pair 차단 L792)
  - DWG 쌍(검출) + PDF 쌍(표시)을 "같은 도면"으로 묶기
- **DoD**: DWG+PDF 입력 → 하이브리드 비교 결과
- **monolith**: 0줄
- **의존**: 🔴 열린질문 §8-1(멀티시트), §8-5(동일성 보장) — **사용자 실무 워크플로우 확인 필요**

## 4. 실행 순서
```
H1 (변환, 정초)  →  H3 (manifest 스키마)  →  H2 (정합)  →  H4 (emit 통합)
                                                              ↓
                                          [열린질문 사용자 확인]
                                                              ↓
                                                          H5 (페어링)
```
H1~H4는 열린질문 무관하게 진행 가능. H5만 실무 워크플로우 답 대기.

## 5. 검증 (각 슬라이스 DoD)
1. `python -m pytest <슬라이스 테스트> -v` → pass
2. `python scripts/cad_policy_gate.py` → pass
3. `git diff --stat src/gui/drawing_compare_workbench.py` → 0줄
4. (H4 후) 전체 `pytest tests/unit -q` → 회귀 0
5. (H2 후) 실측 PDF(01.3PG1)로 정합 오차 측정

## 6. 위험 (Risks)
| 위험 | 확률 | 대응 |
|---|---|---|
| plot 편차로 정합 깨짐 (§8-6) | 중 | transform_quality=estimated로 degrade + 배지(S1 연동) |
| 멀티시트 PDF (실무는 도면집) (§8-1) | 높 | H5 전 사용자 확인. page_matcher 재활용 검토 |
| DWG↔PDF 동일성 오매칭 (§8-5) | 중 | 파일명/도곽 대조 + 사용자 확인 UI |
| PDF 이미지 2분할 (§8-2) | 낮 | H2에서 페이지 단위 합성 |

## 7. Abort Triggers
- monolith 추가 필요 발견 → 중단, 사용자 확인 (Freeze Rule)
- H2 정합 오차가 실측에서 > 20px → 정합 방식 재설계 (도곽 자동검출 한계)
- 전체 회귀 깨짐 → 즉시 중단
- 새 P5-G* 게이트 충동 → 중단

## 8. 산출물 매니페스트 (PVH 완료 시점, 추정)
```
src/services/comparison/transform.py                  (H1 수정 +함수)
src/services/comparison/cad_pdf_alignment.py          (H2 신규)
src/services/comparison/viewer_manifest_v3.py         (H3 수정)
src/services/comparison/change_zones.py               (H4 수정)
src/services/comparison/zone_render_service.py        (H4 수정)
src/services/comparison/drawing_batch.py              (H5 수정)
tests/unit/services/comparison/test_*_hybrid*.py      (각 신규)
docs/work-memory/PDF_HYBRID_VIEWER_*.md               (plan/checklist)
```
monolith `drawing_compare_workbench.py`: **0줄** (뷰어 배선은 PDF 배경+오버레이가 기존 lightweight_viewport 재활용, 신규 배선 필요 시 별도 슬라이스 +≤5줄)
