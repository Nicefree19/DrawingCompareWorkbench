# ADR-003: PDF-first 하이브리드 뷰어 — CAD entity diff(검출) + PDF(표시)

| 항목 | 값 |
|---|---|
| 상태 | **Accepted** |
| 작성자 | Claude (deep-interview + 실측 기반) |
| 승인 | nicefree19@gmail.com (2026-05-29) |
| 작성일 | 2026-05-29 |
| 상위 ADR | [ADR-001 PDF-first 전환](ADR-001-pdf-first-transition.md)의 구체화 |
| 영향 범위 | 뷰어 렌더링 경로, 좌표 정합, change_zones 좌표공간, manifest 스키마 |

> 이 ADR은 ADR-001의 "PDF-first viewer with CAD entity diff as truth"를
> **구현 가능한 구체적 아키텍처**로 확정한다. deep-interview(2026-05-29)와
> 실제 고객 도면 PDF 2장(`01.3PG1.pdf`, `02.3PG1_R1.pdf`) 실측이 근거다.

---

## 1. 컨텍스트

### 1.1 사용자 통증 (재인용)
> "큰 도면에 배경 그리드·오프닝 표시 정도만 보이고 주요 정보가 누락. 확대해도
> 부재번호 등 디테일이 안 보임. 고화질 PNG는 속도 문제 우려. 명확하고 정확한
> 차이 비교를 원함."

### 1.2 근본 원인 (코드 확정)
[dxf_renderer.py:80-85](../../src/services/comparison/dxf_renderer.py) `_LIGHT_MODE_SKIP_TYPES = {INSERT, HATCH, MTEXT, DIMENSION}`, `light_mode=True` 기본값. 큰 도면에서 메모리 폭발/hang(주석: *"71MB DXF가 light_mode=False면 22GB RAM + 16분"*)을 막으려 **텍스트·심볼·치수·해치를 의도적으로 skip**. 결과: 배경 윤곽(LINE)만 남고, 8000px PNG 래스터라 확대해도 없는 텍스트는 안 나타남.

### 1.3 deep-interview 결론 (2026-05-29)
1. **표시 범위**: 도면 전체 자유 탐색 (CAD 뷰어처럼 어디든 확대 → 디테일)
2. **입력**: DWG/DXF + PDF 함께 출력 가능
3. **차이 정밀도**: 도면 요소 단위 (부재·텍스트·치수별 무엇이 추가/삭제/변경)

### 1.4 실측 데이터 (이번 세션, 결정적)

**A. 고객 PDF 구조** (`01.3PG1.pdf` / `02.3PG1_R1.pdf`):
| 측정 | 값 |
|---|---|
| 페이지 | 1장 A3 (297×420mm) |
| 내용 | **이미지 PDF** — 텍스트 0, 벡터 0, JPEG 2개 (상하 분할: 7015×4982 + 7015×4939) |
| 해상도 | **≈ 600 DPI** → 확대 시 부재번호 판독 가능 |

**B. PDF↔PDF 비교 실측** ([compare_pdf_documents](../../src/services/comparison/drawing_batch.py)):
| 측정 | 값 | 대조 |
|---|---|---|
| 속도 | **1.59초** | DWG 비교 68초 (43배 빠름) |
| 검출 | 16 region (modified) | — |
| 좌표공간 | `image_pixels` @150dpi | DWG는 `cad_wcs_mm` |
| 차이 "의미" | **없음** — region bbox만, `layer=PDF_PAGE_1`, entity_type 없음, OCR off | DWG는 "ATTRIB @100→@200" 등 의미 보유 |

**C. 16 region 패턴**: x=150 열(6개)·x=375 열(4개) 정렬 변경 + 모서리 3개(노이즈 의심). visual diff만으론 실제 변경 vs 노이즈 구분 불가.

### 1.5 핵심 발견
- **표시는 PDF가 우월**: 600dpi, 1.6초, 확대 디테일 ✓ → §1.2 통증을 정면 해결 (light_mode와 정반대)
- **차이 의미는 DWG가 우월**: PDF visual diff는 "어디"만, "무엇이"는 못 줌 (이미지 PDF라 텍스트/벡터 없음)
- → **표시(PDF) + 검출(DWG)을 결합해야** 사용자 요구(전체 탐색 + 요소 단위 정확도)를 모두 충족

---

## 2. 결정

**하이브리드 3-레이어** (ADR-001 계승, 구체화):

```text
입력: DWG 쌍 (before/after) + PDF 쌍 (before/after, 같은 도면)
  │
  ├─ Truth Layer:   DWG/DXF entity diff  → 요소 단위 변경 (부재/텍스트/치수)
  │                 (현 350K-change 파이프라인 그대로, 의미 보존)
  │
  ├─ Visual Layer:  사용자 PDF (600dpi 이미지)  → 표시 배경
  │                 Qt PDF 렌더 + 줌 시 DPI 재렌더 (확대 디테일)
  │
  └─ Overlay:       DWG diff bbox(cad_wcs_mm) → PDF 좌표 정합 → 클라우드/마커
                    (요소 단위 차이를 PDF 위 정확한 위치에)
```

**핵심 원칙**:
1. **검출과 표시 분리** — 검출 공간(`cad_wcs_mm`)과 표시 공간(`image_pixels`/`pdf_page_points`)이 다를 수 있음. ADR-001의 "truth ≠ visual" 명문화.
2. **PDF는 표시 전용** — 차이 검출에 PDF visual diff를 쓰지 않음 (의미 부족). 단 PDF 없을 때 fallback으로만.
3. **정합은 도곽 기준** — DWG 도곽과 PDF 페이지가 같은 A3 → fit-to-page affine으로 매핑.

---

## 3. 재활용 vs 신규 (실측 기반, reuse 우선)

### 재활용 (이미 작동 — 검증됨)
| 자산 | 위치 | 비고 |
|---|---|---|
| Qt PDF 렌더 + DPI 재렌더 | `qt_pdf_adapter.py` `render_page(page,dpi)` | "전체 탐색+줌" 핵심, 작동 확인 |
| affine 변환 프레임워크 | `transform.py` `fit_world_to_pixels`, `*_to_image_pixels_bbox` | 수학 검증됨 |
| DWG entity diff | 현 비교 파이프라인 | 변경 없음 (truth 유지) |
| 4종 좌표공간 + 정규화 | `transform.py` `CoordinateSpace` | `cad_wcs_mm`/`image_pixels`/`pdf_page_points_bl` |

### 신규 (핵심 공백 — 우선순위순)
| # | 항목 | 위치(예정) | 난이도 |
|---|---|---|---|
| 1 | **`cad_wcs_mm → image_pixels/pdf_page_points` 변환** | `transform.py` 신규 함수 | 중 — 도곽 fit affine. `fit_world_to_pixels` 조합 가능 |
| 2 | **DWG 도곽 ↔ PDF 페이지 정합** | 신규 모듈 `cad_pdf_alignment.py` | 중 — 도곽 bbox 검출 + fit-to-page |
| 3 | **하이브리드 manifest 필드** | `viewer_manifest_v3` `display_overlay_space` | 저 — 검출공간≠표시공간 허용 (덜 침습적, agent 제안) |
| 4 | **DWG diff bbox를 표시 좌표로 emit** | `change_zones.py` / `zone_render_service.py` | 중 |
| 5 | **입력 페어링**: DWG 쌍 + PDF 쌍을 "같은 도면"으로 묶기 | `drawing_batch.py` `are_compatible` 확장 | 중 — 현재 mixed pair 차단(L792) |

**reuse 비율 추정 60%+** (표시·변환·검출 인프라 존재, 정합·배선이 신규).

---

## 4. 좌표 정합 방식

- **입력 전제**: DWG 도곽과 PDF가 **동일 plot 영역** (사용자가 같은 도면을 DWG·PDF로 출력). 실측 PDF는 A3 풀페이지 이미지 → DWG A3 도곽과 1:1.
- **변환**: DWG 도곽 bbox(`cad_wcs_mm`) → PDF 페이지(`image_pixels` @ render dpi). `fit_world_to_pixels(dwg_extents, pdf_pixel_size)` 재활용.
- **품질 태그**: `transform_quality = exact`(도곽 정확 정렬) / `estimated`(추정) / `relative_only`(정합 실패 → 상대 위치만). transform.py에 이미 존재.
- **검증**: 알려진 기준점(도곽 모서리, 표제란 위치) 대조로 정합 오차 측정.

---

## 5. 거부된 대안

| 대안 | 거부 이유 (실측 근거) |
|---|---|
| **B. PDF↔PDF visual diff만** | 실측: 16 region이 bbox만, 의미 0 (`layer=PDF_PAGE_1`). 요소 단위 정확도(사용자 요구) 미충족. 이미지 PDF라 텍스트 diff도 불가(OCR off). 표시 인프라로만 재활용 |
| **light_mode 개선 (텍스트 포함)** | 22GB RAM/16분 hang 위험 여전 (dxf_renderer 주석). 전체 자유 탐색 불가 |
| **CAD 직접 벡터 뷰어 자체 구현 (LOD+컬링)** | PDF로 동등 효과(600dpi+무한줌) 달성 가능. 미검증 자체 엔진은 과투자 |
| **자동 DWG→PDF 변환** | ADR-001 옵션 C 거부 — OSS 변환기 부재. 사용자 PDF 출력으로 대체 |

---

## 6. 결과 / 영향

### 6.1 영향 코드 (예정)
- `src/services/comparison/transform.py` — cad↔pdf 변환 추가
- 신규 `src/services/comparison/cad_pdf_alignment.py` — 도곽 정합
- `src/services/comparison/change_zones.py` / `zone_render_service.py` — 표시 좌표 emit
- `src/services/comparison/viewer_manifest_v3.py` — `display_overlay_space`
- `src/gui/lightweight_viewport.py` — PDF 배경 + DWG 오버레이 동시 (이미 PDF 렌더 있음)
- `src/services/comparison/drawing_batch.py` — DWG쌍+PDF쌍 페어링

### 6.2 제약 (계승)
- **monolith 추가 동결** ([AGENTS.md](../../AGENTS.md)) — 신규는 별도 모듈, `drawing_compare_workbench.py` ≤5줄
- **PySide6.QtPdf LGPLv3** — ADR-002(법무, 미작성) 의존
- ODA-free 정책 유지

### 6.3 후속 ADR 번호 재정렬 (ADR-001 §6.2 갱신 필요)
- ADR-002: Qt LGPL 법무 (미작성)
- **ADR-003: 이 문서** (PDF-first 하이브리드 뷰어)
- ADR-004: AC1015 외 DWG 사용자 안내 UX (S1 연동) ← 기존 003에서 이동
- ADR-005: PyMuPDF comparison-internal 범위 ← 기존 004에서 이동

---

## 7. 완료 기준 (제안)

1. 큰 도면(74MB급)도 `render_timeout` 없이 PDF 배경 표시
2. 확대 시 부재번호·치수 판독 가능 (600dpi PDF)
3. DWG entity diff(요소 단위)가 PDF 위 **정확한 위치**에 오버레이 (정합 오차 목표 < 5px @ 150dpi)
4. 표시 속도: 첫 표시 목표(측정 후 확정), PDF 1.6초 수준 유지
5. 정합 실패 시 `relative_only`로 정직하게 degrade + 배지 표시 (S1 연동)

---

## 8. 열린 질문

1. **멀티시트** — 실측 PDF는 1장이지만 실무는 도면집(여러 장)? PDF 페이지 ↔ DWG 매칭 (S2 multi-sheet 메트릭과 연결, `page_matcher` 존재)
2. **PDF 이미지 2분할** — 한 페이지에 JPEG 2개(상하). 정합 시 단일 페이지로 합성하나, 분할 유지하나
3. **도곽 자동 검출** — DWG·PDF 도곽을 자동 정렬할지, 사용자가 기준점 지정할지
4. **노이즈 필터** — visual fallback 시 모서리 region 같은 노이즈 제거 (DWG diff 주경로면 불필요)
5. **DWG↔PDF 동일성 보장** — 두 입력이 같은 도면임을 파일명/사용자 매칭/도곽 대조 중 무엇으로?
6. **plot 설정 편차** — before/after PDF가 다른 plot 스케일이면 정합 깨짐. 검출/경고 방법

---

## 9. 결정 (사용자 채움)

```
Status:        Accepted
Decision:      하이브리드 — DWG entity diff(검출) + PDF(표시) + 좌표 정합 오버레이
Decision Date: 2026-05-29
Decision Maker: nicefree19@gmail.com
Rationale:     deep-interview(전체 자유탐색 + 요소 단위 정확도) + 실측(PDF 600dpi
               표시 우수·1.6초, visual diff는 region bbox만이라 의미 부족)이
               하이브리드를 유일 해법으로 확정. 사용자 승인.
```

## 10. 관련 자료
- [ADR-001 PDF-first 전환](ADR-001-pdf-first-transition.md) — 상위 결정
- [PDF_FIRST_VIEWER_PERFORMANCE_ROADMAP.md](../collab/PDF_FIRST_VIEWER_PERFORMANCE_ROADMAP.md)
- [transform.py](../../src/services/comparison/transform.py) — 좌표 변환 프레임워크
- [qt_pdf_adapter.py](../../src/services/comparison/qt_pdf_adapter.py) — Qt PDF 렌더
- [dxf_renderer.py](../../src/services/comparison/dxf_renderer.py) — light_mode skip (통증 근원)
- 실측: `01.3PG1.pdf` / `02.3PG1_R1.pdf` (600dpi 이미지 PDF, 16 region, 1.59초)
