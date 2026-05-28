# ADR-001: CAD 비교의 PDF-first 전환 여부

| 항목 | 값 |
|---|---|
| 상태 | **Accepted** |
| 작성자 | Claude (초안 및 정리), 사용자(nicefree19@gmail.com) 승인 |
| 작성일 | 2026-05-28 |
| 결정일 | 2026-05-28 |
| 결정 방식 | Rubber-stamp — 코드와 [PDF_FIRST_VIEWER_PERFORMANCE_ROADMAP.md](../collab/PDF_FIRST_VIEWER_PERFORMANCE_ROADMAP.md)에 이미 박혀 있던 결정을 공식 ADR로 흡수 |
| 영향 범위 | 전체 비교 파이프라인, 라이선스 정책, 고객 인도 일정 |

> 이 ADR은 새 결정이 아니라, 이미 구현되고 있는 노선을 명시 ADR로 공식화한 것이다.
> 향후 PDF-first 작업의 정당성은 이 ADR을 근거로 한다.
> 옵션 B/C로의 회귀 시도는 이 ADR을 Superseded 처리한 후에만 허용된다.

---

## 1. 컨텍스트

### 1.1 현재 상태
- 현 아키텍처: CAD(DXF/DWG)를 **직접 파싱·정규화·렌더** 후 비교.
  핵심 코드: `src/services/comparison/dxf_*`, `dwg_*`, `zone_vector_renderer.py`,
  `lightweight_viewport.py` (QML Canvas 기반)
- ODA-free 정책 유지 중 ([README.md:60-62], [docs/THIRD_PARTY_LICENSE_POLICY.md])
- 네이티브 DWG 지원은 AC1015 단일 버전만. AC1024/AC1032는 진단 셸뿐이고
  실제로는 ezdxf 캐시 DXF로 우회 ([WORKLOG.md:41])

### 1.2 통증
사용자 불만 5개 중 다음이 PDF-first 검토를 촉발:
- "실제 차이가 있는 도면인데 비교 실패"
- "벡터 렌더 실패로 뷰어가 비어 있음"
- DWG 버전/엔티티/폰트/외부참조/레이어/블록의 조합 폭발에 매번 패치 누적

### 1.3 핵심 질문
> CAD를 직접 렌더하는 현 경로 대신, **CAD를 안정적으로 PDF로 변환한 뒤
> PDF 페이지 단위로 비교**하는 경로로 전환할 것인가?

### 1.4 의사결정의 무게
- 영향 코드 라인 수: 비교 파이프라인의 ~40% (대략 30,000+ 라인)
- 영향 기능: zone vector render, region detection, viewer, tile cache,
  selected-zone, multi-sheet 매칭 — 거의 전부
- **되돌리기 비용 매우 큼**. 한 번 결정하면 6~12개월 코스트
- ODA 라이선스 정책 재검토와 묶일 가능성 있음

---

## 2. 옵션 매트릭스

### 옵션 A: 현 경로 유지 (CAD 직접 렌더)
| 항목 | 평가 |
|---|---|
| 라이선스 위험 | ✅ 없음 (ezdxf MIT) |
| 구현 비용 | ✅ 이미 90% 구현됨 |
| DWG 신버전 지원 | 🔴 영원히 부분 지원 (AC1015만 native, 나머지는 fallback) |
| 폰트/외참/블록 정합성 | 🟡 패치 누적 필요 |
| 사용자 체감 안정성 | 🟡 fallback이 silent하면 신뢰 깨짐 (S1으로 일부 보강 중) |
| 회복력 | 🔴 새 DWG 버전마다 작업 발생 |
| 비교 정밀도 | ✅ 엔티티 단위로 가장 정밀 (잘 동작할 때) |

### 옵션 B: ODA 라이선스 도입 → CAD 직접 렌더 강화
| 항목 | 평가 |
|---|---|
| 라이선스 위험 | 🔴 ODA 멤버십 비용·계약 부담, 정책 변경 |
| 구현 비용 | 🟠 ODA 통합 자체는 중간, 기존 코드 재활용 가능 |
| DWG 신버전 지원 | ✅ 광범위 |
| 폰트/외참/블록 정합성 | ✅ 산업 표준 수준 |
| 사용자 체감 안정성 | ✅ 매우 높음 |
| 회복력 | ✅ ODA가 신버전 추적 |
| 비교 정밀도 | ✅ 엔티티 단위 유지 |
| **추가 위험** | 회사 정책상 ODA 금지였던 이유 재검증 필요 |

### 옵션 C: 외부 변환기로 DWG→PDF, 그 후 PDF 비교 (오프라인)
| 항목 | 평가 |
|---|---|
| 라이선스 위험 | 🟠 변환기 선택에 따라 달라짐 (LibreCAD/QCAD/AutoCAD 등) |
| 구현 비용 | 🔴 매우 큼 (전체 파이프라인 재설계) |
| DWG 신버전 지원 | ✅ 변환기 능력에 의존 |
| 폰트/외참/블록 정합성 | 🟡 변환 시 손실 가능 |
| 사용자 체감 안정성 | 🟡 변환 실패 시 또 silent failure |
| 회복력 | 🟡 변환기에 종속 |
| 비교 정밀도 | 🟠 PDF 픽셀/벡터 단위로 떨어짐, 엔티티 의미 손실 |
| 좌표 역매핑 | 🔴 변경 영역 → 원본 CAD 좌표 역매핑이 어려움 |

### 옵션 D: PDF가 원본일 때만 PDF-first, DWG는 기존 경로 유지 (Hybrid)
| 항목 | 평가 |
|---|---|
| 라이선스 위험 | ✅ 현 정책 유지 |
| 구현 비용 | 🟡 PDF 경로는 이미 PDF-first R1~R6에서 진행 중 |
| DWG 신버전 지원 | 🟠 옵션 A와 동일 |
| 폰트/외참/블록 정합성 | 옵션 A 그대로 |
| 사용자 체감 안정성 | 🟡 입력 타입별로 동작이 다름을 사용자가 학습해야 함 |
| 회복력 | 🟡 두 경로 모두 유지보수 |
| 비교 정밀도 | PDF는 픽셀/벡터, DWG는 엔티티 — 보고서 일관성 깨짐 |
| **현실성** | 사실상 **현재 진행 중인 노선**. 코드도 이미 이 방향으로 누적 |

### 옵션 E: 단계적 전환 (옵션 D → 옵션 C로 점진 이동)
| 항목 | 평가 |
|---|---|
| 라이선스 위험 | 🟠 변환기 선택 시점에 재발생 |
| 구현 비용 | 🟠 단계적이라 절대값은 D+C |
| 사용자 체감 안정성 | ✅ 점진 전환이라 후퇴 위험 낮음 |
| 결정 회복력 | ✅ 단계마다 재평가 가능 |
| **현실성** | 가장 합리적이지만 의사결정·우선순위 비용 큼 |

---

## 3. 평가 기준 (가중치)

| 기준 | 가중치 | 이유 |
|---|---|---|
| 라이선스 위험 | 25% | ODA-free 정책이 회사 결정 |
| 사용자 체감 안정성 | 25% | 현재 가장 큰 통증 |
| 구현 비용 (인력·기간) | 20% | 9.6→10 게이트 차단 안 되게 |
| DWG 신버전 회복력 | 15% | 장기 유지보수 |
| 비교 정밀도 (엔티티 의미 보존) | 15% | 구조설계 검토 본질 |

가중 점수 계산은 사용자가 옵션을 좁힌 후 수행.

---

## 4. 의사결정에 필요한 추가 조사 (옵션별)

### 옵션 B (ODA) 선택 시
- 회사 법무·라이선스 부서 재검토
- ODA 멤버십 비용 견적
- 통합 PoC (1~2주)

### 옵션 C/D/E (PDF 경유) 선택 시
- DWG→PDF 변환기 후보 PoC:
  - LibreCAD/QCAD (GPL — 정책 충돌 가능)
  - 상용 CAD의 CLI 변환 (AutoCAD/BricsCAD)
  - Tekla 기존 통합 활용 (조직 내 자산 활용)
- PDF 좌표계 → 원본 CAD 좌표계 역매핑 알고리즘 spike
- multi-sheet 도곽이 PDF 페이지로 1:1 보존되는지 검증

### 옵션 A 선택 시
- 추가 조사 불필요. S1+S2 결과로 충분
- 단, AC1024/AC1032 미지원에 대한 사용자 공지 필요

---

## 5. 결정

```
Status:           Accepted
Decision:         옵션 D 변형 — "PDF-first viewer with CAD entity diff as the source of truth"
Decision Date:    2026-05-28
Decision Maker:   nicefree19@gmail.com (rubber-stamp), Claude (초안 정리)
```

### 결정의 정확한 형태

세 레이어로 분리한다 — 이는 [PDF_FIRST_VIEWER_PERFORMANCE_ROADMAP.md:25-39](../collab/PDF_FIRST_VIEWER_PERFORMANCE_ROADMAP.md)에 이미 명문화된 architecture를 ADR로 흡수한 것이다.

1. **Truth Layer (비교의 진실)**
   - DWG/DXF 입력 → **CAD canonical/entity diff** (현 경로 유지, ezdxf 기반)
   - PDF 입력 → **visual/text/OCR diff**
   - 비교의 정확도는 entity 의미를 보존하는 CAD diff가 담당
2. **Visual Asset Layer (시각 자산)**
   - 원본 PDF 또는 승인된 CAD→PDF/PDF-like artifact
   - PDF/PNG/WebP tile cache
   - **사용자가 직접 제공한 PDF** 또는 **sidecar PDF**가 MVP 1차 소스
3. **Viewer Layer (뷰어)**
   - PDF/tile background **먼저**
   - CAD comparison overlays/clouds/pins 위에
   - vector focus overlay는 secondary enhancement (현재 silent fallback의 주범 — S1에서 정직화됨)

### 백엔드 선택

- **Display path**: `PySide6.QtPdf` (LGPLv3, [THIRD_PARTY_LICENSE_POLICY.md] 법무 검토 항목)
  - 근거: [src/services/comparison/qt_pdf_adapter.py:1-15](../../src/services/comparison/qt_pdf_adapter.py) "no PyMuPDF dependency for the display path"
- **Comparison path 내부 도구**: PyMuPDF 사용 가능 (display 아닌 알고리즘 내부)
  - 단 PyMuPDF AGPL이므로 **고객 빌드 임베딩 금지**, 상용 라이선스 확보 또는 대체 후 임베딩
- **CAD vector render**: `ezdxf` + SVG는 **fallback만**, primary viewer path 금지

### 거부된 옵션과 이유

| 거부 옵션 | 거부 이유 |
|---|---|
| 옵션 A (현 경로 유지) | 사용자 불만 5건 중 4건(빈 화면, 벡터 렌더 실패, 비교 실패, 개선 체감 없음)이 silent vector fallback에서 비롯됨. 옵션 A로는 근본 해결 불가 |
| 옵션 B (ODA 도입) | 회사 정책 자체가 ODA-free([THIRD_PARTY_LICENSE_POLICY.md:60]). 재도입은 큰 정책 후퇴이며 현 비즈니스 의도와 충돌 |
| 옵션 C (자동 DWG→PDF 변환 후 PDF 비교) | LibreDWG GPLv3 금지, ODA 금지, Aspose.CAD/QCAD Professional은 상용·redistribution 위험. 자동 변환에 쓸 수 있는 OSS가 사실상 없음. [CAD_FORMAT_SUPPORT_POLICY.md:75-87]에서 "User converts to supported DXF"를 공식 워크플로우로 못 박음 |
| 옵션 E (단계적 D→C) | C가 실현 불가에 가까우므로 사실상 D 유지와 동치. 별도 ADR로 분리할 가치 없음 |

### 함의 — 사용자가 지금 받아들이는 것

- AC1015 외 DWG는 **영구적으로 사용자 사전 변환** 워크플로우 (DXF로 export 또는 PDF로 plot)
- "한국 구조설계 도면을 그대로 비교"보다는 "사용자가 PDF 출력 후 비교" 사용 시나리오가 1차
- DWG vector render는 secondary fallback이지 primary가 아님 — 이를 사용자에게 정직하게 노출(S1)
- PySide6.QtPdf LGPLv3 법무 검토는 별도 트랙으로 진행 필요

## 6. 결과

### 6.1 영향 받은 (이미 작동 중인) 코드
- `src/services/comparison/qt_pdf_adapter.py` (19.4 KB) — Qt PDF display path
- `src/services/comparison/pdf_display_list_cache.py` (18.5 KB) — DisplayList 캐시
- `src/services/comparison/pdf_cloud_dxf_export.py` (17.3 KB) — 변경 영역 DXF export
- `src/services/comparison/review_report_pdf.py` (22.5 KB) — PDF 리포트
- `src/services/comparison/folder_compare_pipeline.py:1042` — `fidelity="pdf_first"` 분기
- `src/services/comparison/zone_render_service.py:1848` — `renderer_backend="pdf-first-page-fallback"`
- WORKLOG의 PDF-first R1~R6 + P0~P5-G30 작업 전체

### 6.2 후속 ADR (추정, 작성 시점 확정)
- **ADR-002**: PySide6.QtPdf LGPLv3 법무 검토 결과 (별도 트랙)
- **ADR-003**: AC1015 외 DWG의 사용자 안내 UX 정책 (S1 silent fallback 가시화와 연동)
- **ADR-004**: PyMuPDF의 comparison-internal 사용 범위 확정 (display 누출 방지 가드 포함)

### 6.3 회고 일정
- **2026-08-31** — 옵션 D 변형이 사용자 통증 5건을 얼마나 해결했는지 1차 회고
- 회고 기준: silent fallback 발생률, 빈 viewer 발생률, 사용자 만족도 (S1 메트릭 기반)

## 7. 관련 자료
- [PDF_FIRST_VIEWER_PERFORMANCE_ROADMAP.md](../collab/PDF_FIRST_VIEWER_PERFORMANCE_ROADMAP.md) — 결정의 본문 (이 ADR이 흡수한 source)
- [README.md](../../README.md) — ODA-free 정책
- [THIRD_PARTY_LICENSE_POLICY.md](../THIRD_PARTY_LICENSE_POLICY.md) — 라이선스 정책 본문
- [CAD_FORMAT_SUPPORT_POLICY.md](../CAD_FORMAT_SUPPORT_POLICY.md) — DWG 사용자 사전 변환 정책
- [WORKLOG.md](../collab/WORKLOG.md) — PDF-first R/P 작업 기록
- [CODEX_PROMPT_S1_S2_FAILURE_VISIBILITY_AND_SHEET_MATCH_METRICS.md](../collab/CODEX_PROMPT_S1_S2_FAILURE_VISIBILITY_AND_SHEET_MATCH_METRICS.md) — S1 silent fallback 가시화 (이 ADR에 종속)
