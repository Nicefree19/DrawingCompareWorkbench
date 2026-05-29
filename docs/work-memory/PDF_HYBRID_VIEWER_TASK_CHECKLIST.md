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

## H2 — DWG 도곽 ↔ PDF 페이지 정합
- [ ] Write `src/services/comparison/cad_pdf_alignment.py` (도곽 검출 + fit affine)
- [ ] transform_quality (exact/estimated/relative_only) 적용
- [ ] Write 테스트 + 실측 PDF(01.3PG1) 정합 오차 측정 (<5px @150dpi)
- [ ] pytest + gate + monolith 0줄
- [ ] commit + 사용자 검증

## H4 — DWG diff bbox를 표시 좌표로 emit
- [ ] Edit change_zones.py / zone_render_service.py — H2 정합으로 좌표 변환 emit
- [ ] display_overlay_space 기록 (H3)
- [ ] Write 테스트 + 전체 회귀 (pytest tests/unit -q, 회귀 0)
- [ ] commit + 사용자 검증

## H5 — DWG + PDF 페어링 (🔴 열린질문 의존)
- [ ] **사용자 확인**: 멀티시트(§8-1) + 동일성 보장(§8-5) 실무 워크플로우
- [ ] Edit drawing_batch.py are_compatible — DWG쌍 + PDF쌍 페어링
- [ ] Write 테스트
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
