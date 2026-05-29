# PDF-first 하이브리드 뷰어 — Task Checklist (PVH)

근거: [ADR-003](../adr/ADR-003-pdf-first-hybrid-viewer.md) · [Implementation Plan](PDF_HYBRID_VIEWER_IMPLEMENTATION_PLAN.md)

## Pre
- [x] deep-interview (2026-05-29) — 전체 자유탐색 + 요소 단위 정확도
- [x] 현황 파악 (reuse 60%+, cad↔pdf 정합이 공백)
- [x] 실측 (PDF 600dpi 이미지, 1.59초, 16 region bbox만)
- [x] ADR-003 Accepted + commit (62295ad)
- [x] 구현 계획(Layer 0) 작성

## H1 — cad_wcs_mm → image_pixels/pdf_page_points 변환 (정초) ✅ 완료 (2026-05-29)
- [x] Read transform.py 변환 함수 패턴 재확인 (`fit_world_to_pixels`, `*_to_image_pixels_bbox`)
- [x] Write transform.py 신규 함수 `cad_world_to_image_pixels_bbox` + 역 `image_pixels_to_cad_world_bbox` (도곽 affine 기반, +91줄)
- [x] __all__ 등록
- [x] Write 테스트 (9개): round-trip 오차<0.01, Y축 flip, 도곽→전체페이지, 알려진 점 스케일, degenerate→None, unparseable→None, dict form, padding inset, offset frame(실 S20-0002 extents) round-trip
- [x] pytest: **H1 9 passed + 회귀 33 passed (test_transform/test_cad_pdf_tile_transform)**
- [x] cad_policy_gate: passed
- [x] monolith 0줄 (transform.py +91만)
- [x] commit + push

## H3 — manifest display_overlay_space 필드 ✅ 완료 (2026-05-29)
- [x] Read viewer_manifest_v3.py 스키마 (ViewerManifestV3 dataclass L397)
- [x] `display_overlay_space: str = ""` 필드 + __post_init__ normalize + to_dict + from_dict (+16줄)
- [x] Write 테스트 4개: default empty round-trip, image_pixels_tl round-trip, 정규화(pdf_points→pdf_page_points_bl), 하위호환(필드 없는 dict→"")
- [x] pytest: **22 passed (4 신규 + 18 회귀)**
- [x] cad_policy_gate: passed
- [x] monolith 0줄
- [x] commit + push

## H2 — DWG 도곽 ↔ PDF 페이지 정합 ✅ 완료 (2026-05-29)
- [x] Write `src/services/comparison/cad_pdf_alignment.py` (신규, transform.py만 의존)
- [x] `align_cad_to_pdf()` + `CadPdfAlignment` (map_cad_bbox/map_cad_bboxes/is_usable/to_dict)
- [x] transform_quality: aspect 일치<2%→exact / 불일치→estimated(§8-6 plot drift) / degenerate→relative_only
- [x] Write 테스트 10개: aspect match/mismatch, degenerate frame/page, unparseable, map H1 위임, 순서/None 필터, relative_only 거부, 실 S20 extents, to_dict
- [x] pytest: **10 passed**, cad_policy_gate passed, monolith 0줄
- [-] 실측 PDF 정합 오차 <5px: **H4/end-to-end로 연기** (DWG+PDF 쌍 필요 — 현재 PDF만 있고 대응 DWG 도곽 없음). round-trip+aspect로 대체 검증
- [x] commit + push

## H4 — DWG diff bbox를 표시 좌표로 emit
### H4-헬퍼 (순수) ✅ 완료 (2026-05-29)
- [x] `cad_pdf_alignment.build_display_overlays(change_zones, alignment)` — CAD zone → display_bbox(image_pixels_tl), non-CAD/relative_only/unparseable → None
- [x] 원본 키/순서 보존, non-dict 스킵
- [x] Write 테스트 5개 (CAD 매핑, non-CAD 스킵, relative_only None, 메타/순서 보존, 빈 입력)
- [x] pytest: cad_pdf_alignment 15 passed
- [x] **전체 회귀: 3326 passed, 2 skipped (0 regression)**
- [x] cad_policy_gate passed, monolith 0줄
- [x] commit + push
### H4-wiring (파이프라인 통합) ⏸ H5와 함께 (열린질문 의존)
- [ ] change_zones.py / zone_render_service.py / viewer emit 경로에 build_display_overlays 연결
- [ ] alignment는 H5 페어링이 PDF 페이지 크기 공급 시 생성
- [ ] 이유: emit wiring은 DWG+PDF 데이터 흐름 필요 → 멀티시트/동일성(§8) 답 후

## H5 — DWG↔PDF 자동 페어링 + 멀티시트 + emit wiring
- [x] **사용자 확인** (2026-05-29): 멀티시트 둘 다, 도면번호/도곽 자동 대조
- [x] **자산 조사** (Explore): reuse 높음 — pair_identity/drawing_id_pattern/page_matcher/ocr_extractor as-is, sheet_region_detector/page_descriptor/drawing_batch extents glue
### H5a — DWG page descriptor 빌더
- [ ] DXF render + 도면번호(sheet_region_detector/OCR) + 도곽 bbox(_collect_entity_points L2605) + page_size → PerPageDescriptor
- [ ] reuse: sheet_region_detector._extract_drawing_number, drawing_id_pattern, drawing_batch extents
- [ ] 테스트 + commit
### H5b — DWG↔PDF 페어링 오케스트레이터 (신규 cad_pdf_pairing.py)
- [ ] page_matcher 재활용 도면번호+도곽 매칭; PDF 도면번호는 ocr_extractor
- [ ] 멀티시트(Hungarian) + 단일(직결)
- [ ] 테스트 + commit
### H5c — 페어링 → H2 alignment 생성
- [ ] 매칭 쌍 → align_cad_to_pdf (자동 도곽 fit, estimated→S1 배지)
- [ ] 테스트 + commit
### H5d — H4-wiring (emit)
- [ ] build_display_overlays → viewer manifest emit (display_overlay_space), 최소 침습
- [ ] 실측 3PG1 end-to-end 검증 + 전체 회귀
- [ ] commit + 사용자 검증

## Post
- [ ] Write PDF_HYBRID_VIEWER_COMPLETION_REPORT.md
- [ ] 실측 PDF로 end-to-end (DWG diff → PDF 오버레이) 검증
- [ ] Memory 업데이트

## Abort Triggers
- monolith 추가 필요 → 중단, 사용자 확인
- H2 정합 오차 >20px → 정합 재설계
- 회귀 깨짐 → 중단
- 새 P5-G* 충동 → 중단
