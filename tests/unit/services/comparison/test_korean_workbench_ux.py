# -*- coding: utf-8 -*-
"""Static smoke checks for the Korean high-contrast Workbench UX."""

from __future__ import annotations

from pathlib import Path


SOURCE = Path("src/gui/drawing_compare_workbench.py").read_text(encoding="utf-8")
SUBPROCESS_SOURCE = Path("src/services/comparison/workbench_subprocess.py").read_text(encoding="utf-8")


def test_korean_workbench_primary_labels_are_korean() -> None:
    assert "class DrawingCompareWorkbenchV2" in SOURCE
    assert "window = DrawingCompareWorkbenchV2()" in SOURCE
    assert "도면 변경 비교" in SOURCE
    assert "두 파일 또는 폴더" in SOURCE
    assert "변경 전 파일/폴더" in SOURCE
    assert "변경 후 파일/폴더" in SOURCE
    assert "파일 선택" in SOURCE
    assert "폴더 선택" in SOURCE
    assert "DWG/DXF/PDF 파일 또는 폴더를 선택하세요" in SOURCE
    assert "비교 실행" in SOURCE
    assert "결과 요약" in SOURCE
    assert "우선 검토 도면" in SOURCE
    assert "우선 검토 CSV 열기" in SOURCE
    assert "구름마크 도면 열기" in SOURCE
    assert "요약 대시보드 열기" in SOURCE
    assert "경량 뷰어 열기" in SOURCE
    assert "검토 패키지 열기" in SOURCE


def test_korean_workbench_branding_assets_and_ownership_are_declared() -> None:
    assert "센엔지니어링 그룹 AI 동아리" in SOURCE
    assert "APP_OWNERSHIP_KO" in SOURCE
    assert "app_icon.ico" in SOURCE
    assert "header_banner.png" in SOURCE
    assert "preview_placeholder.png" in SOURCE
    assert "_drawing_compare_asset_path" in SOURCE


def test_korean_workbench_zone_review_viewer_controls_are_declared() -> None:
    assert "class PairPreviewRenderWorker" in SOURCE
    assert "class ZoneCropRenderWorker" in SOURCE
    assert "class ZoneRenderProcessController" in SOURCE
    assert "class GpuDrawingViewport" in SOURCE
    assert "QQuickWidget" in SOURCE
    assert "DrawingGpuViewport.qml" in SOURCE
    assert "visible_tile_model" in SOURCE
    assert "_tile_manifest_for_pair_v2" in SOURCE
    assert "_render_pair_backgrounds_with_timeout" in SOURCE
    assert "GPU_VIEWER_RENDER_TIMEOUT_SECONDS" in SOURCE
    assert "render_timeout" in SOURCE
    assert 'if render_status == "rendered":' in SOURCE
    assert "if False and rendered.get" not in SOURCE
    assert "visibleTiles" in Path("src/gui/assets/drawing_compare/DrawingGpuViewport.qml").read_text(encoding="utf-8")
    qml_source = Path("src/gui/assets/drawing_compare/DrawingGpuViewport.qml").read_text(encoding="utf-8")
    assert "property real sceneWidth" in qml_source
    # The viewer always shows the background image when present (tile mode used
    # to gate it but that left the viewport blank for small tile grids — the
    # current contract is "background always renders, tiles overlay on top").
    assert "source: root.imageSource" in qml_source
    assert "property real overlayOpacityScale" in qml_source
    assert "viewport_model" in SOURCE
    assert "fallback_widgets" in SOURCE
    assert "tile_ready" in SOURCE
    assert "pdf_render" in SOURCE
    assert "PDF 시각 배경" in SOURCE
    assert "미리보기 준비 전 - 상대 위치로 변경구역을 표시합니다." in SOURCE
    assert "타일 준비" in SOURCE
    assert "미리보기 준비" in SOURCE
    assert "????" not in SOURCE
    assert "_pending_render_request_v2" in SOURCE
    assert "_start_pending_render_v2" in SOURCE
    assert "viewer_cache_root" in SOURCE
    assert "ZONE_RENDER_PROCESS_MODULE" in SOURCE
    assert "zone_render_process" in SUBPROCESS_SOURCE
    assert "render_environment_signature" in SOURCE
    assert "_start_zone_crop_render_v2" in SOURCE
    assert "PDF 위치 좌표 없음" in SOURCE
    assert "렌더 중 - 변경구역 위치를 준비하고 있습니다" in SOURCE
    assert "상대위치 표시" in SOURCE
    assert "이전 변경" in SOURCE
    assert "다음 변경" in SOURCE
    assert "선택 구역 원위치" in SOURCE
    assert "btn_zone_confirm_v2" in SOURCE
    assert "btn_zone_ignore_v2" in SOURCE
    assert "btn_zone_false_positive_v2" in SOURCE
    assert "btn_zone_needs_review_v2" in SOURCE
    assert "검토 상태" in SOURCE
    assert "큰 구름마크는 검토 영역" in SOURCE
    assert "업무 큐:" in SOURCE
    assert "변경 후 도면에 새 요소가 생겼습니다." in SOURCE
    assert "변경 전 도면에 있던 요소가 사라졌습니다." in SOURCE
    assert "같은 위치 또는 가까운 위치의 요소 속성/형상이 달라졌습니다." in SOURCE
    qml_source = Path("src/gui/assets/drawing_compare/DrawingGpuViewport.qml").read_text(encoding="utf-8")
    # Selection plumbing: Python still pushes the selected zone id to QML, and the
    # cloud/focus overlay separation (introduced for the customer-grade UX) lets the
    # Repeater render a small focus marker on top of the larger review-area cloud.
    assert "property string selectedZoneId" in qml_source
    assert "property var overlaysFocus" in qml_source
    assert "property var overlaysCloud" in qml_source
    assert "property string viewportSide" in qml_source
    assert "matchSideColorFor" in qml_source
    assert "cloudBorderColor" in qml_source
    assert "#005FCC" in qml_source


def test_v15_drawing_selection_is_metadata_first_and_zone_selection_renders_crop() -> None:
    drawing_start = SOURCE.index("    def _on_drawing_selected_v2")
    drawing_end = SOURCE.index("    def _viewer_overlays_for_pair_v2", drawing_start)
    drawing_body = SOURCE[drawing_start:drawing_end]
    zone_start = SOURCE.index("    def _on_zone_selected_v2")
    zone_end = SOURCE.index("    def _select_zone_in_list_v2", zone_start)
    zone_body = SOURCE[zone_start:zone_end]

    assert "_viewer_pair_is_pdf(viewer_pair)" in drawing_body
    assert "_start_pair_render_v2" in drawing_body
    assert "_start_zone_crop_render_v2" in zone_body


def test_workbench_keeps_qthread_wrappers_until_native_thread_stops() -> None:
    assert "_retired_qthreads_v2" in SOURCE
    assert "def _retire_qthread_v2" in SOURCE
    assert "worker.isRunning()" in SOURCE
    assert "worker.deleteLater()" in SOURCE


def test_workbench_caps_immediate_qml_overlay_load_for_responsiveness() -> None:
    light_source = Path("src/gui/lightweight_viewport.py").read_text(encoding="utf-8")

    assert "GPU_VIEWER_MAX_VISIBLE_OVERLAYS = 120" in SOURCE
    assert "GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD = 300" in SOURCE
    assert "should_use_focus_only_overlay_mode(len(self._last_overlays))" in SOURCE
    assert 'overlay_display_mode="focus_only"' in SOURCE
    assert "MAX_QML_CHANGE_CLOUD_OVERLAYS = 120" in light_source
    assert "FOCUS_ONLY_CHANGE_OVERLAY_SOURCE_THRESHOLD = 300" in light_source
    assert "focus-only overlay mode" in light_source
    assert "selected zone, when present" in light_source


def test_workbench_uses_lightweight_viewer_as_single_visible_path() -> None:
    menu_start = SOURCE.index("self.act_lightweight_viewer_v2 = QAction")
    menu_end = SOURCE.index("view_menu.addAction(self.act_lightweight_viewer_v2)", menu_start)
    menu_body = SOURCE[menu_start:menu_end]
    preview_start = SOURCE.index("self.preview_before_v2 = GpuDrawingViewport()")
    preview_end = SOURCE.index("self.preview_before_v2.viewportChanged.connect", preview_start)
    preview_body = SOURCE[preview_start:preview_end]
    drawing_start = SOURCE.index("    def _on_drawing_selected_v2")
    drawing_end = SOURCE.index("    def _viewer_overlays_for_pair_v2", drawing_start)
    drawing_body = SOURCE[drawing_start:drawing_end]

    assert "DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY = True" in SOURCE
    assert "def _is_lightweight_viewer_active_v2" in SOURCE
    assert "def _set_lightweight_viewer_visible_v2" in SOURCE
    assert "self._lightweight_raster_pairs: set[str] = set()" in SOURCE
    assert "setChecked(DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY)" in menu_body
    assert "self.act_lightweight_viewer_v2.setVisible(False)" in menu_body
    assert "self.preview_before_v2.setVisible(not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY)" in preview_body
    assert (
        "self.preview_before_lightweight_v2.setVisible(DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY)"
        in preview_body
    )
    assert "if not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:" in drawing_body
    assert "if self._is_lightweight_viewer_active_v2():" in drawing_body
    assert "if _viewer_pair_is_pdf(viewer_pair):" in drawing_body
    assert "def _load_lightweight_raster_preview_v2" in SOURCE
    assert "viewport.load_raster_image(" in SOURCE
    assert "def load_raster_image" in Path("src/gui/lightweight_viewport.py").read_text(encoding="utf-8")
    assert "self._load_lightweight_raster_preview_v2(pair_id, viewer_pair)" in drawing_body
    assert "pair_id in self._lightweight_raster_pairs" in SOURCE

    finished_start = SOURCE.index("    def _on_auto_finished_v2")
    finished_end = SOURCE.index("    def _load_dashboard_v2", finished_start)
    finished_body = SOURCE[finished_start:finished_end]
    assert "self._retire_active_worker_v2()" in finished_body
    assert "self._worker = None" not in finished_body

    error_start = SOURCE.index("    def _on_auto_error_v2")
    error_end = SOURCE.index("    def _set_v2_busy", error_start)
    error_body = SOURCE[error_start:error_end]
    assert "self._retire_active_worker_v2()" in error_body
    assert "self._worker = None" not in error_body


def test_korean_light_stylesheet_uses_high_contrast_palette() -> None:
    assert "#F7F8FA" in SOURCE
    assert "#111827" in SOURCE
    assert "#374151" in SOURCE
    assert "#9CA3AF" in SOURCE
    assert "#005FCC" in SOURCE
    assert 'QPushButton[primary="true"]' in SOURCE
