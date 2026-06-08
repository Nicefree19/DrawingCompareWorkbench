# -*- coding: utf-8 -*-
"""Dedicated desktop UI for drawing batch comparison."""

from __future__ import annotations

import logging
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, List, Mapping, Optional

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QRectF, Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QBrush, QDesktopServices, QIcon, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


QT_QUICK_DISABLED = _env_flag("DRAWING_COMPARE_DISABLE_QT_QUICK")
try:
    if QT_QUICK_DISABLED:
        raise ImportError("Qt Quick disabled by DRAWING_COMPARE_DISABLE_QT_QUICK")
    from PySide6.QtQuickWidgets import QQuickWidget

    QT_QUICK_AVAILABLE = True
except Exception:
    QQuickWidget = None  # type: ignore[assignment]
    QT_QUICK_AVAILABLE = False

from src.gui import workbench_visual_extensions as visual_ext
from src.gui import zone_crop_alignment as zone_align
from src.gui.compare_runtime_diagnostics import default_gui_dwg_backend_mode, format_auto_compare_error
from src.gui.source_path_repair import has_lossy_path_text, registered_dxf_fallback_for_source
from src.gui.theme import NanoColors, get_stylesheet
from src.services.comparison import ComparisonConfig
from src.services.comparison.cache_budget import resolve_cache_byte_limit
from src.services.comparison.drawing_batch import (
    BatchCompareJob,
    BatchCompareOptions,
    BatchCompareSummary,
    DescriptorBuildOptions,
    DrawingFileDescriptor,
    MatchCandidate,
    MatchStatus,
    MatchingOptions,
    SUPPORTED_DRAWING_EXTENSIONS,
    apply_manual_matches,
    are_compatible,
    confirmed_pair_uniqueness_violations,
    load_manual_match_csv,
    match_drawing_sets,
    quality_gate_visible_statuses,
    scan_drawing_inputs,
    score_match,
    write_manual_match_csv,
)
from src.services.comparison.folder_compare_pipeline import (
    FolderComparePipeline,
    FolderCompareRunRequest,
    FolderCompareRunResult,
)
from src.services.comparison.change_zones import CloudMarkOptions, export_executive_review_from_artifacts
from src.services.comparison.review_project import (
    PreviewArtifact,
    ReviewStateRecord,
    collect_review_zones,
    export_preview_artifacts,
    load_review_state,
    normalize_review_status,
    review_state_key,
    save_review_state,
    update_artifact_manifest,
    write_review_project,
)
from src.services.comparison.confirmed_cloud_export import (
    ConfirmedCloudExportResult,
    export_confirmed_cloud_marks,
)
from src.services.comparison.report_settings import (
    REPORT_SETTINGS_FILENAME,
    ReportSettings,
    load_report_settings,
    save_report_settings,
)
from src.services.comparison.review_report_pdf import (
    ReviewReportInput,
    ReviewReportResult,
    generate_review_report_pdf,
)
from src.services.comparison.run_contract import validate_run_completion
from src.services.comparison.zone_classifier import (
    CATEGORY_OTHER,
    ZoneCategoryResult,
    category_summary,
    classify_zone,
)
# Phase N — wires the Phase H/I/J/K/L AI cascade into the workbench's
# per-pair zone classification. Heuristic-only users see no change;
# users with use_embedding/use_llm enabled now get cascade results.
from src.services.comparison.zone_classifier_adapter import (
    classify_zone_with_cascade,
)
from src.services.comparison.viewer_package import _render_pair_backgrounds_with_timeout, _write_index_html
from src.services.comparison.viewer_perf_summary import (
    format_viewer_perf_summary_korean,
    summarize_viewer_perf,
)
from src.services.comparison.viewer_overlay_pages import (
    OverlayPageStore,
    iter_overlay_page_store,
)
from src.services.comparison.workbench_subprocess import (
    ZONE_RENDER_PROCESS_MODULE,
    ZONE_VECTOR_WORKER_MODULE,
    worker_command_for_module,
    worker_working_directory,
)
from src.services.comparison.suppression_audit import (
    SuppressionAuditReport,
    audit_from_comparison_result,
    build_suppression_audit,
)
from src.services.comparison.viewer_tile_cache import (
    ViewerTileCacheOptions,
    append_viewer_perf_event,
    append_pair_to_tiles_manifest_jsonl,
    materialise_tiles_manifest_from_jsonl,
    pair_tile_manifest_path,
    tiles_manifest_is_current,
    viewer_cache_key,
    visible_overlay_tile_items,
    visible_tile_model,
    visible_or_clustered_overlays,
    viewport_rect_from_transform,
    write_pair_tile_cache,
    write_pair_visible_tile_cache,
)
from src.services.comparison.zone_render_service import (
    RenderJob,
    bbox_to_pixel_rect as zone_bbox_to_pixel_rect,
    canonical_window_from_bbox,
    render_zone_pair,
    render_environment_signature,
    union_bboxes,
)

logger = logging.getLogger(__name__)

APP_TITLE_KO = "도면 변경 비교"
APP_SUBTITLE_KO = "두 파일 또는 폴더를 선택하면 변경 위치와 구름마크 산출물을 자동 생성합니다."
APP_OWNERSHIP_KO = "개발/운영: 센엔지니어링 그룹 AI 동아리"

RENDER_STATUS_LABELS = {
    "not_requested": "렌더 대기",
    "rendering": "렌더 중",
    "ready": "실미리보기",
    "failed": "미리보기 실패",
    "gpu_ready": "GPU 뷰어",
    "tile_ready": "타일 준비",
    "tile_rendering": "타일 렌더 중",
    "pdf_render": "PDF 시각 배경",
    "relative_only": "상대위치 표시",
    "fallback_widgets": "호환 뷰어",
    "render_timeout": "렌더 시간 초과 · 상대위치",
}

GPU_VIEWER_TILE_SIZE = 512
# Keep the full analysis/zone list, but cap the immediate QML overlay model.
# Each cloud marker owns a Canvas + label; 500+ markers can starve the Qt event
# loop on ordinary DWG files with thousands of detected regions.
GPU_VIEWER_MAX_VISIBLE_OVERLAYS = 120
GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD = 300
GPU_VIEWER_MEMORY_BUDGET_MB = 512
GPU_VIEWER_RENDER_TIMEOUT_SECONDS = 30
DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY = QT_QUICK_AVAILABLE
GUI_FIRST_SELECTION_ZONE_LIMIT = 500
GUI_FULL_ZONE_TREE_IDLE_DELAY_MS = 120
GUI_INITIAL_ZONE_SELECT_DELAY_MS = 75
GUI_INITIAL_ZONE_HEAVY_RENDER_DELAY_MS = 250
# ② full-detail upgrade: delay before silently re-rendering the on-screen fast
# crop from source (text/dims/blocks). Small so the upgrade feels prompt, but
# non-zero so the fast crop paints first and is never delayed by the upgrade.
GUI_ZONE_FULL_DETAIL_UPGRADE_DELAY_MS = 90
GUI_LIGHTWEIGHT_PAIR_LOAD_DELAY_MS = 25
GUI_PDF_INITIAL_RENDER_MAX_PIXELS = 5_000_000
GUI_PDF_ADJACENT_PREWARM_DELAY_MS = 350
GUI_OVERLAY_CACHE_PAIR_LIMIT = 8
GUI_OVERLAY_CACHE_BYTE_LIMIT = resolve_cache_byte_limit(
    specific_env_var="DRAWING_COMPARE_GUI_OVERLAY_CACHE_MB",
    default_mb=8,
)
GUI_UNKNOWN_OVERLAY_JSON_DEFER_BYTES = 1 * 1024 * 1024
GUI_FULL_ZONE_TREE_CHUNK_ZONE_THRESHOLD = 500
GUI_FULL_ZONE_TREE_CHUNK_ITEM_LIMIT = 80
GUI_FULL_ZONE_TREE_CHUNK_TIME_BUDGET_MS = 8.0
GUI_FULL_ZONE_TREE_CHUNK_DELAY_MS = 0

# B3 — preview render quality presets exposed to the user.
# Each entry: (label_ko, dpi, max_edge_px). Default key drives the GUI request
# so the user gets crisper drawings than the pipeline's lazy default (80 DPI).
#
# Phase B1.5 raised the default to "초고화질" (200 DPI / 6000 px) and added the
# "초최고화질" (300 DPI / 8000 px) tier. Rationale: the inline SVG vector
# overlay handles the truly-sharp text/dimension/INSERT detail (vector-zoom
# inside the workbench), so the PNG layer is now used as the navigation /
# context map. A sharper default PNG gives the reviewer a better orientation
# baseline before they zoom into a zone for the SVG-quality detail. Higher
# DPI here costs ~2x render time per pair vs the 120-DPI baseline; on the
# 71 MB customer DXF that's ~50 s/pair at 300 DPI, ~30 s/pair at 200 DPI.
PREVIEW_QUALITY_PRESETS: list[tuple[str, int, int]] = [
    # Audit-gates §10 follow-up — auto-quality is the new default. Index 0 is
    # a sentinel that ``_run_auto_compare`` translates into a runtime call to
    # ``adaptive_quality.select_quality()``; the DPI/edge values here are only
    # used as a worst-case ceiling so any code path that bypasses the auto
    # branch still produces a sensible PNG. The S20 hang incident proved that
    # making users pick "구조도면 정밀 (DPI 400)" by default is unsafe.
    ("🤖 자동 (권장) — 입력 크기에 맞춰 최적 화질", 0, 0),
    ("보통 (DPI 80)", 80, 2400),
    ("고화질 (DPI 120)", 120, 3600),
    ("초고화질 (DPI 200)", 200, 6000),
    ("초최고화질 (DPI 300)", 300, 8000),
    # DPI 400 retained for explicit override on small drawings only — the
    # GUI tooltip warns the user about S20-class memory blow-up.
    ("📐 구조도면 정밀 (DPI 400) — 작은 도면 전용", 400, 10000),
]
# Audit-gates §10 follow-up — default is now index 0 (자동). The previous
# default index 4 (DPI 400) was the hang trigger on S20-class data.
PREVIEW_QUALITY_DEFAULT_INDEX = 0  # "🤖 자동 (권장)"
PREVIEW_QUALITY_AUTO_INDEX = 0     # explicit alias used by run helpers

# C3 — compare run presets. Each entry: (label_ko, quality_index, recursive,
# viewer_render_policy). Selecting a preset auto-fills the recursive checkbox
# and quality combo and overrides the viewer_render_policy used for the run
# so the user can pick a familiar workflow shape without touching individual
# options.
#
# QW2 — "⚡ 초고속 스캔" prepended at index 0. Uses ``viewer_render_policy="lazy"``
# so the PNG previews are generated on-demand when the user clicks a drawing
# instead of upfront. For a 29-pair set this turns a ~2-hour upfront render
# into a ~5-minute "results ready" experience; previews appear in seconds when
# inspected one by one.
COMPARE_PRESETS: list[tuple[str, int, bool, str]] = [
    # Audit-gates §10 follow-up — preset quality_index updated for the new
    # PREVIEW_QUALITY_PRESETS layout (index 0 is now "🤖 자동 (권장)" instead
    # of "보통 (DPI 80)"). All workflow presets now default to auto-quality
    # so the user no longer has to know about DPI tiers.
    ("⚡ 초고속 스캔 (5분 내 결과, 미리보기 lazy)", 1, False, "lazy"),  # explicit DPI 80 for fastest scan
    ("🤖 자동 검토 (단일 폴더, 자동 화질)", 0, False, "top-issues"),  # default
    ("빠른 스캔 (보통, 단일 폴더)", 1, False, "top-issues"),
    ("정밀 검토 (초고화질, 단일 폴더)", 3, False, "top-issues"),  # was 2 (초고화질)
    ("전체 폴더 스캔 (자동 화질, 하위 포함)", 0, True, "top-issues"),  # was 1, now auto
]
COMPARE_PRESET_DEFAULT_INDEX = 1  # "🤖 자동 검토" — auto-quality default

RECENT_PATHS_LIMIT = 10
RECENT_PATHS_FILENAME = "recent_paths.json"

# QW1 — first-run tutorial
TUTORIAL_COMPLETED_FILENAME = "tutorial_completed.flag"
TUTORIAL_PAGES: list[tuple[str, str]] = [
    (
        "1단계: 파일/폴더 선택",
        "변경 전/후의 도면을 선택합니다.\n\n"
        "• 메뉴바 → [파일 → 🆕 새 비교 시작] (단축키 Ctrl+N)\n"
        "• 한 파일씩: [파일 선택] / 폴더 묶음: [폴더 선택]\n"
        "• 이전 비교를 다시 열려면 [▼ 최근 비교 불러오기]\n\n"
        "지원 형식: DWG, DXF, PDF (양쪽 같은 형식이어야 자동 매칭됩니다)",
    ),
    (
        "2단계: 비교 모드 선택",
        "프리셋으로 한 번에 화질 + 옵션을 정합니다.\n\n"
        "• ⚡ 초고속 스캔 — 큰 폴더에서 5분 내 결과 (미리보기 lazy)\n"
        "• 표준 검토 — 기본값, 우선검토 도면 미리보기 자동 생성\n"
        "• 정밀 검토 — 초고화질 (DPI 200), 작은 도면에 권장\n"
        "• 전체 폴더 스캔 — 하위 폴더까지 일괄 비교\n\n"
        "[비교 실행] 클릭 → 진행률 표시 → 자동으로 입력 영역이 접혀 뷰어가 최대화됩니다.",
    ),
    (
        "3단계: 변경구역 검토 (단축키)",
        "키보드만으로 빠르게 검토합니다.\n\n"
        "• J / ↓ : 다음 변경구역\n"
        "• K / ↑ : 이전 변경구역\n"
        "• 1 : 확인 (confirmed)\n"
        "• 2 : 보류 (hold)\n"
        "• 3 : 오탐 (false positive)\n"
        "• 4 : 추가 검토 (needs review)\n\n"
        "마킹하면 자동으로 다음 미검토 zone으로 이동합니다.\n"
        "우측 패널의 [Top 변경구역] 필터로 미검토만 보기도 가능.",
    ),
    (
        "4단계: 메모 + 시각 조작",
        "메모와 뷰어 컨트롤로 정밀 검토합니다.\n\n"
        "• 각 zone에 메모 입력 → Ctrl+Enter로 영구 저장\n"
        "• 변경점 투명도 슬라이더 → 마커 흐려서 좌표/도면 노출\n"
        "• 줌 슬라이더 / [전체 보기] / [100%] 버튼\n"
        "• F 키 : 입력 영역 접기/펼치기 (뷰어 최대화)\n"
        "• Ctrl+0 : 뷰어 fit-to-view",
    ),
    (
        "5단계: 결과 공유 + 구름마크 출력",
        "검토 완료 후 결과 산출물을 추출합니다.\n\n"
        "• [확인된 변경 구름마크 추출] — confirmed zone만 빨간 cloud 그려진 PNG\n"
        "• [요약 대시보드 열기] — HTML 보고서\n"
        "• [우선 검토 CSV 열기] — 엑셀로 정리된 변경 목록\n"
        "• [구름마크 도면 열기] — 자동 생성 cloud DXF\n\n"
        "이 가이드는 [도움말 → 시작 가이드]에서 언제든 다시 볼 수 있습니다.",
    ),
]


def resolve_overlay_match_side(change_type: str) -> str:
    """Classify a change type into A-only / B-only / matched / mixed buckets.

    deleted → ``a_only`` (변경 전 A에만 존재), added → ``b_only`` (변경 후 B에만 존재),
    modified/moved → ``matched`` (양쪽 매칭), mixed → ``mixed``. Used by the GPU viewport
    to dim or highlight cloud overlays based on which viewport side is showing them.
    """

    normalized = str(change_type or "").lower()
    if "delete" in normalized or "remove" in normalized:
        return "a_only"
    if "add" in normalized:
        return "b_only"
    if "mixed" in normalized:
        return "mixed"
    return "matched"


def overlay_cloud_should_dim(match_side: str, *, before: bool, selected: bool) -> bool:
    """Decide whether a cloud overlay should render dimmed.

    Dim when (1) it belongs to the selected zone so the focus marker stands out,
    or (2) the change is one-sided and we are showing the wrong viewport side
    (b_only changes on the before viewport, a_only on the after viewport).
    """

    if selected:
        return True
    if before and match_side == "b_only":
        return True
    if (not before) and match_side == "a_only":
        return True
    return False


def build_overlay_entries(
    *,
    zone_id: str,
    rect: tuple[float, float, float, float],
    change_type: str,
    label: str,
    raw_change_count: int = 0,
    cluster_count: int = 0,
    selected: bool = False,
    before: bool = False,
    pin_only: bool = False,
) -> list[dict]:
    """Build cloud + focus overlay entries for the QML viewport.

    For non-selected zones returns a single ``cloud`` entry. For the selected zone
    returns a dimmed cloud entry plus a ``focus`` entry carrying pin coordinates,
    a crosshair flag and a compact label so the QML side can render a small marker
    on top of the larger cloud area. ``pin_only`` (used for PDF page-level fallback
    when bbox is unknown) skips the cloud and emits only the focus pin.
    """

    match_side = resolve_overlay_match_side(change_type)
    dim_cloud = overlay_cloud_should_dim(match_side, before=before, selected=selected)
    width = max(1.0, float(rect[2]))
    height = max(1.0, float(rect[3]))
    x = float(rect[0])
    y = float(rect[1])
    entries: list[dict] = []

    if not pin_only:
        entries.append(
            {
                "role": "cloud",
                "zoneId": zone_id,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "changeType": str(change_type or "mixed"),
                "matchSide": match_side,
                "label": label,
                "labelMode": "area",
                "dimmed": dim_cloud,
                "rawChangeCount": int(raw_change_count or 0),
                "clusterCount": int(cluster_count or 0),
            }
        )

    if selected:
        entries.append(
            {
                "role": "focus",
                "zoneId": zone_id,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "pinX": x + width / 2.0,
                "pinY": y + height / 2.0,
                "crosshair": True,
                "changeType": str(change_type or "mixed"),
                "matchSide": match_side,
                "label": label,
                "labelMode": "compact",
                "rawChangeCount": int(raw_change_count or 0),
                "clusterCount": int(cluster_count or 0),
                "pinOnly": bool(pin_only),
            }
        )

    return entries


def split_overlay_entries(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Partition a flat overlay model into ``(cloud, focus)`` lists for QML."""

    cloud: list[dict] = []
    focus: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("role") == "focus":
            focus.append(entry)
        else:
            cloud.append(entry)
    return cloud, focus


def should_use_focus_only_overlay_mode(overlay_source_count: int) -> bool:
    """Return True when cloud Canvas rendering should be skipped.

    The analysis/result model can hold thousands of zones, but QML renders each
    cloud marker as a Canvas plus label. Once the source set is this large the
    viewport should keep the full zone list in Python and only send the selected
    zone's focus marker to QML.
    """

    try:
        count = int(overlay_source_count)
    except (TypeError, ValueError):
        count = 0
    return count > GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD


def match_side_ko(change_type: str) -> str:
    """Render the A-only / B-only / matched / mixed bucket as a Korean phrase."""

    side = resolve_overlay_match_side(change_type)
    if side == "a_only":
        return "변경 전(A)에만 존재"
    if side == "b_only":
        return "변경 후(B)에만 존재"
    if side == "mixed":
        return "혼합 (A/B 모두에 일부)"
    return "양쪽 매칭됨"


def natural_change_summary(
    data: dict,
    *,
    added: int,
    deleted: int,
    modified: int,
    moved: int,
    top_layers: str = "",
) -> str:
    """Compose a single-line natural-language summary of a change zone.

    Combines the dominant change kind with the leading layer when present so the
    reviewer can grasp the gist of the zone without parsing every count line.
    Examples:
    - ``"GRID 레이어에 추가 5건, 수정 2건"``
    - ``"치수 변경 - 수정 7건"``
    - ``"변경 없음"``

    Phase O Commit 4 [RV-20260508-010] — entity_types 에 ATTRIB/ATTDEF
    가 포함되면 사용자가 "블록 텍스트 변경" 사례임을 즉시 인지할 수
    있도록 suffix 를 추가. 사용자 사례 (DOWEL BAR ... @100 → @200) 가
    바로 이 경로로 surface.
    """

    if not isinstance(data, dict):
        data = {}
    layer = top_layers.split(" | ")[0].split(",")[0].strip() if top_layers else ""
    parts: list[str] = []
    if added:
        parts.append(f"추가 {added}건")
    if deleted:
        parts.append(f"삭제 {deleted}건")
    if modified:
        parts.append(f"수정 {modified}건")
    if moved:
        parts.append(f"이동 {moved}건")

    # Phase O Commit 4 — ATTRIB/ATTDEF 감지 후 suffix
    entity_types_raw = data.get("entity_types") or data.get("top_entity_types") or ""
    entity_types_str = ""
    if isinstance(entity_types_raw, str):
        entity_types_str = entity_types_raw.upper()
    elif isinstance(entity_types_raw, (list, tuple, set)):
        entity_types_str = " | ".join(str(t).upper() for t in entity_types_raw)
    has_block_text = (
        "ATTRIB" in entity_types_str or "ATTDEF" in entity_types_str
    )

    if not parts:
        if has_block_text:
            return "변경 없음 (블록 텍스트 영역만 포함)"
        return "변경 없음"
    body = ", ".join(parts)
    if layer:
        body = f"{layer} 레이어에 {body}"
    if has_block_text:
        body = f"{body} · 블록 텍스트 변경 포함"
    return body


def format_top_issue_label(issue: dict) -> str:
    """Render a single review-dashboard top-issue entry as a list label.

    Returns a two-line string:
    - line 1: drawing number · zone id · severity (한국어)
    - line 2: priority rank/score and raw change count for quick triage
    """

    if not isinstance(issue, dict):
        return "(이슈 정보 없음)"
    drawing = str(issue.get("drawing_label") or issue.get("drawing_number") or issue.get("display_label") or issue.get("pair_id") or "-")
    zone = str(issue.get("zone_id") or "-")
    severity_ko = str(issue.get("severity_ko") or issue.get("severity") or issue.get("category") or "-")
    rank = issue.get("priority_rank") or issue.get("priority_rank_in_drawing")
    summary = str(issue.get("change_summary_ko") or "")
    category = str(issue.get("category") or "")
    try:
        score = float(issue.get("priority_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    raw = issue.get("raw_change_count")
    try:
        raw_int = int(float(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        raw_int = 0
    rank_text = f"#{int(rank)}" if isinstance(rank, (int, float)) else "#?"
    headline = summary or f"{drawing} · {zone} · {severity_ko}"
    return f"{headline}\n{drawing} · {zone} · {rank_text} · {category or severity_ko} · 점수 {score:.1f} · raw {raw_int}"


def format_pattern_group_label(pattern: dict) -> str:
    """Render a layer-pattern group entry for the repeated-pattern tab."""

    if not isinstance(pattern, dict):
        return "(패턴 정보 없음)"
    label = str(pattern.get("pattern") or "(이름 없음)")
    affected = pattern.get("affected_drawing_count")
    try:
        affected_int = int(float(affected)) if affected is not None else 0
    except (TypeError, ValueError):
        affected_int = 0
    raw = pattern.get("raw_change_count")
    try:
        raw_int = int(float(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        raw_int = 0
    zones = pattern.get("zone_count")
    try:
        zones_int = int(float(zones)) if zones is not None else 0
    except (TypeError, ValueError):
        zones_int = 0
    layers = str(pattern.get("top_layers") or "-")
    return f"{label} · 도면 {affected_int} · 변경구역 {zones_int} · 변경 {raw_int}\n주요 layer: {layers}"


def compute_pdf_page_pin_overlay(
    base_overlay: dict,
    page_size: dict,
    *,
    pin_width_px: float = 200.0,
    pin_height_px: float = 150.0,
) -> Optional[dict]:
    """Synthesize an overlay entry centered on a PDF page when bbox is unknown.

    Used by the PDF-PDF viewer path: when the change zone has no ``image_pixels``
    bbox we still want to mark the page so the user knows where to look. The pin
    bbox is sized as a small rectangle near the page center so the focus marker
    (drawn with crosshair + pin glyph) stays readable at any zoom level.

    Returns ``None`` when ``page_size`` is missing or non-positive — in that case
    the caller should fall back to the relative-only text status.
    """

    if not isinstance(base_overlay, dict) or not isinstance(page_size, dict):
        return None
    try:
        width = float(page_size.get("width") or 0.0)
        height = float(page_size.get("height") or 0.0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    pin_w = max(40.0, min(float(pin_width_px), width * 0.5))
    pin_h = max(30.0, min(float(pin_height_px), height * 0.5))
    cx = width / 2.0
    cy = height / 2.0
    pixel_box = {
        "x": cx - pin_w / 2.0,
        "y": cy - pin_h / 2.0,
        "width": pin_w,
        "height": pin_h,
    }
    world_bbox = {
        "min_x": pixel_box["x"],
        "min_y": pixel_box["y"],
        "max_x": pixel_box["x"] + pin_w,
        "max_y": pixel_box["y"] + pin_h,
    }
    enriched = dict(base_overlay)
    enriched["pin_only"] = True
    enriched["pdf_page_pin"] = True
    enriched["bbox"] = world_bbox
    enriched.setdefault("after_bbox_px", dict(pixel_box))
    enriched.setdefault("before_bbox_px", dict(pixel_box))
    return enriched


def scale_pdf_bbox_to_render_pixels(
    bbox: object,
    overlay: dict,
    viewer_pair: dict,
) -> Optional[tuple[float, float, float, float]]:
    """Return a PDF crop bbox in the rendered background image pixel space."""

    box = union_bboxes(bbox)
    if not box:
        return None
    if not isinstance(overlay, dict) or not isinstance(viewer_pair, dict):
        return box
    if not _viewer_pair_is_pdf(viewer_pair):
        return box
    if str(overlay.get("bbox_coordinate_space") or "").lower() != "image_pixels":
        return box
    transform = viewer_pair.get("after_transform") or viewer_pair.get("before_transform") or {}
    if not isinstance(transform, dict):
        return box
    try:
        bbox_dpi = float(
            overlay.get("pdf_dpi")
            or viewer_pair.get("compare_pdf_dpi")
            or 0.0
        )
    except (TypeError, ValueError):
        bbox_dpi = 0.0
    try:
        image_dpi = float(
            transform.get("effective_dpi")
            or transform.get("dpi")
            or transform.get("pdf_dpi")
            or 0.0
        )
    except (TypeError, ValueError):
        image_dpi = 0.0
    if bbox_dpi <= 0 or image_dpi <= 0 or bbox_dpi == image_dpi:
        return box
    scale = image_dpi / bbox_dpi
    return (
        box[0] * scale,
        box[1] * scale,
        box[2] * scale,
        box[3] * scale,
    )


def _workbench_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "DrawingCompareWorkbench"
    return Path.home() / ".drawing_compare_workbench"


def _drawing_compare_asset_path(name: str) -> Path:
    candidates = [Path(__file__).resolve().parent / "assets" / "drawing_compare" / name]
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        root = Path(frozen_root)
        candidates.extend(
            [
                root / "src" / "gui" / "assets" / "drawing_compare" / name,
                root / "assets" / "drawing_compare" / name,
                root / "drawing_compare" / name,
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _workbench_worker_cwd() -> Path:
    return worker_working_directory(project_root=Path(__file__).resolve().parents[2])


def _read_json_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _cad_bbox_to_pixel_rect(bbox: object, transform: object) -> Optional[dict[str, float]]:
    if not isinstance(transform, dict) or not transform:
        return None
    if isinstance(bbox, dict):
        if {"min_x", "min_y", "max_x", "max_y"}.issubset(bbox):
            coords = [bbox["min_x"], bbox["min_y"], bbox["max_x"], bbox["max_y"]]
        elif {"x", "y", "width", "height"}.issubset(bbox):
            return {
                "x": float(bbox["x"]),
                "y": float(bbox["y"]),
                "width": max(1.0, float(bbox["width"])),
                "height": max(1.0, float(bbox["height"])),
            }
        else:
            return None
    elif isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        coords = list(bbox[:4])
    else:
        return None
    try:
        min_x = float(transform.get("min_x", 0.0))
        min_y = float(transform.get("min_y", 0.0))
        scale_x = float(transform.get("scale_x", 1.0))
        scale_y = float(transform.get("scale_y", 1.0))
        height = float(transform.get("img_height", 0.0))
        width = float(transform.get("img_width", 0.0))
        x1 = (float(coords[0]) - min_x) * scale_x
        x2 = (float(coords[2]) - min_x) * scale_x
        if str(transform.get("coordinate_space") or "").lower() == "image_pixels":
            y1 = (float(coords[1]) - min_y) * scale_y
            y2 = (float(coords[3]) - min_y) * scale_y
        else:
            y1 = height - ((float(coords[3]) - min_y) * scale_y)
            y2 = height - ((float(coords[1]) - min_y) * scale_y)
        left = max(0.0, min(width, min(x1, x2)))
        right = max(0.0, min(width, max(x1, x2)))
        top = max(0.0, min(height, min(y1, y2)))
        bottom = max(0.0, min(height, max(y1, y2)))
        return {
            "x": round(left, 2),
            "y": round(top, 2),
            "width": max(1.0, round(right - left, 2)),
            "height": max(1.0, round(bottom - top, 2)),
        }
    except Exception:
        return None


def _world_bbox_to_pixel_rect(world_bbox: object, transform: object) -> Optional[dict[str, float]]:
    box = union_bboxes(world_bbox)
    if not box or not isinstance(transform, dict):
        return None
    try:
        img_w = float(transform.get("img_width") or transform.get("width") or 0.0)
        img_h = float(transform.get("img_height") or transform.get("height") or 0.0)
        min_x = float(transform.get("min_x", 0.0))
        max_x = float(transform.get("max_x", 0.0))
        min_y = float(transform.get("min_y", 0.0))
        max_y = float(transform.get("max_y", 0.0))
    except (TypeError, ValueError):
        return None
    world_w = max_x - min_x
    world_h = max_y - min_y
    if img_w <= 0 or img_h <= 0 or world_w == 0 or world_h == 0:
        return None
    wx0, wy0, wx1, wy1 = box
    px0 = (wx0 - min_x) / world_w * img_w
    px1 = (wx1 - min_x) / world_w * img_w
    py0 = (max_y - wy1) / world_h * img_h
    py1 = (max_y - wy0) / world_h * img_h
    left = max(0.0, min(img_w, min(px0, px1)))
    right = max(0.0, min(img_w, max(px0, px1)))
    top = max(0.0, min(img_h, min(py0, py1)))
    bottom = max(0.0, min(img_h, max(py0, py1)))
    if right <= left or bottom <= top:
        return None
    return {
        "x": round(left, 2),
        "y": round(top, 2),
        "width": max(1.0, round(right - left, 2)),
        "height": max(1.0, round(bottom - top, 2)),
    }


def _lightweight_tile_zoom_from_transform(transform: object, units_per_pixel: float) -> float:
    if not isinstance(transform, dict):
        return 1.0
    try:
        img_w = float(transform.get("img_width") or transform.get("width") or 0.0)
        img_h = float(transform.get("img_height") or transform.get("height") or 0.0)
        world_w = float(transform.get("max_x", 0.0)) - float(transform.get("min_x", 0.0))
        world_h = float(transform.get("max_y", 0.0)) - float(transform.get("min_y", 0.0))
        upp = max(0.0001, float(units_per_pixel or 1.0))
    except (TypeError, ValueError):
        return 1.0
    scales = []
    if img_w > 0 and world_w != 0:
        scales.append(abs(img_w / world_w))
    if img_h > 0 and world_h != 0:
        scales.append(abs(img_h / world_h))
    if not scales:
        return 1.0
    image_px_per_screen_px = upp * (sum(scales) / len(scales))
    return max(0.0001, 1.0 / max(0.0001, image_px_per_screen_px))


def _viewer_pair_is_pdf(viewer_pair: dict) -> bool:
    if str(viewer_pair.get("coordinate_source") or "").lower() == "image_pixels":
        return True
    source_a = str(viewer_pair.get("source_a") or "").lower()
    source_b = str(viewer_pair.get("source_b") or "").lower()
    return source_a.endswith(".pdf") and source_b.endswith(".pdf")


def _is_redacted_artifact_path(value: Any) -> bool:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return False
    return (
        text.startswith("<redacted>/")
        or text.startswith("&lt;redacted&gt;/")
        or text.startswith("/redacted/")
    )


def _resolve_viewer_artifact_path(value: Any, viewer_root: Optional[Path]) -> Optional[Path]:
    """Resolve a path stored in ``viewer_manifest.json``.

    Sharable exports rewrite absolute paths to package-relative values such as
    ``viewer/images/...``. The manifest itself lives in ``<output>/viewer``, so
    those paths are relative to the package root, not always the manifest
    directory. Try both roots before giving up.
    """

    text = str(value or "").strip()
    if not text or _is_redacted_artifact_path(text):
        return None
    path = Path(text)
    if path.is_absolute():
        return path

    roots: list[Path] = []
    if viewer_root:
        root = Path(viewer_root)
        roots.extend([root, root.parent])

    candidates = [path]
    candidates.extend(root / path for root in roots)
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue

    if viewer_root:
        root = Path(viewer_root)
        first_part = path.parts[0] if path.parts else ""
        if first_part and first_part.lower() == root.name.lower():
            return root.parent / path
        return root / path
    return path


def _existing_pdf_file(value: Any) -> Optional[Path]:
    text = str(value or "").strip()
    if not text or _is_redacted_artifact_path(text):
        return None
    try:
        path = Path(text)
        if path.suffix.lower() == ".pdf" and path.exists():
            return path
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def _resolve_pdf_viewer_source_path(
    viewer_pair: dict,
    side: str,
    viewer_root: Optional[Path],
) -> tuple[Optional[Path], str]:
    """Resolve the PDF file a lightweight viewport should render.

    Sharable exports intentionally redact ``source_a``/``source_b``. The
    workbench must therefore prefer real source paths only when available and
    fall back to package-local PDF copies for customer-shareable runs.
    """

    if side not in {"before", "after"}:
        raise ValueError(f"Unsupported PDF viewer side: {side}")

    source_key = "source_a" if side == "before" else "source_b"
    source_path = _existing_pdf_file(viewer_pair.get(source_key))
    if source_path is not None:
        return source_path, source_key

    package_keys = (
        ("before_page_pdf", "page_pdf") if side == "before" else ("after_page_pdf", "page_pdf")
    )
    for key in package_keys:
        package_path = _resolve_viewer_artifact_path(viewer_pair.get(key), viewer_root)
        if package_path is None:
            continue
        try:
            if package_path.suffix.lower() == ".pdf" and package_path.exists():
                return package_path, key
        except (OSError, ValueError, RuntimeError):
            continue

    return None, "missing"


class ScanWorker(QThread):
    """Background descriptor scan and matching worker."""

    finished = Signal(object, object, object)  # candidates, descriptors_a, descriptors_b
    error = Signal(str)
    progress = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        source_a: str,
        source_b: str,
        recursive: bool,
        use_ocr: bool,
        enable_cache: bool = True,
    ):
        super().__init__()
        self.source_a = source_a
        self.source_b = source_b
        self.recursive = recursive
        self.use_ocr = use_ocr
        self.enable_cache = enable_cache
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            options = DescriptorBuildOptions(
                recursive=self.recursive,
                use_ocr_fallback=self.use_ocr,
                enable_cache=self.enable_cache,
            )
            self.progress.emit("Scanning A drawings...")
            descriptors_a = scan_drawing_inputs(self.source_a, options=options)
            if self._cancelled:
                self.cancelled.emit()
                return
            self.progress.emit("Scanning B drawings...")
            descriptors_b = scan_drawing_inputs(self.source_b, options=options)
            if self._cancelled:
                self.cancelled.emit()
                return
            self.progress.emit("Matching drawings...")
            candidates = match_drawing_sets(
                descriptors_a,
                descriptors_b,
                options=MatchingOptions(),
            )
            if self._cancelled:
                self.cancelled.emit()
                return
            self.finished.emit(candidates, descriptors_a, descriptors_b)
        except Exception as exc:
            logger.exception("Drawing scan failed")
            self.error.emit(str(exc))


class CompareWorker(QThread):
    """Background batch comparison worker."""

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)

    def __init__(
        self,
        candidates: List[MatchCandidate],
        expand_blocks: bool,
        dxf_cache_dir: Path,
        compare_state_dir: Path,
    ):
        super().__init__()
        self.candidates = candidates
        self.expand_blocks = expand_blocks
        self.dxf_cache_dir = dxf_cache_dir
        self.compare_state_dir = compare_state_dir
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            config = ComparisonConfig.get_default()
            config.expand_blocks = self.expand_blocks
            job = BatchCompareJob(
                self.candidates,
                options=BatchCompareOptions(
                    comparison_config=config,
                    dxf_cache_dir=self.dxf_cache_dir,
                    compare_state_dir=self.compare_state_dir,
                ),
            )

            def progress(current: int, total: int, message: str) -> None:
                percent = 0 if total == 0 else int((current / total) * 100)
                self.progress.emit(percent, message)

            summary = job.run(
                progress_callback=progress,
                is_cancelled=lambda: self._cancelled,
            )
            self.progress.emit(100, "Cancelled" if summary.cancelled else "Completed")
            self.finished.emit(summary)
        except Exception as exc:
            logger.exception("Drawing batch comparison failed")
            self.error.emit(str(exc))


def _runtime_budget_spool_dirs_for_folder_request(
    request: FolderCompareRunRequest,
) -> Optional[list[Path]]:
    if not request.output_dir:
        return None
    output_dir = Path(request.output_dir)
    dirs = [output_dir / "artifacts", output_dir / "viewer"]
    viewer_cache_dir = getattr(request, "viewer_cache_dir", None)
    if viewer_cache_dir:
        dirs.append(Path(viewer_cache_dir))
    return dirs


class AutoFolderCompareWorker(QThread):
    """Background worker for the simplified Korean folder comparison flow."""

    review_ready = Signal(object)
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)

    def __init__(
        self,
        request: FolderCompareRunRequest,
        *,
        viewer_memory_cap_mb: Optional[float] = None,
    ):
        super().__init__()
        self.request = request
        self._cancelled = False
        # Audit-gates §10.4 — viewer memory cap enforced at the GUI boundary.
        # Default 4096 MiB matches DEFAULT_VIEWER_MEMORY_BUDGET_MB and is
        # tuned for S20-class DWG (350K change-zone records). Callers may
        # override via the ``viewer_memory_cap_mb`` argument.
        self.viewer_memory_cap_mb = (
            float(viewer_memory_cap_mb)
            if viewer_memory_cap_mb is not None
            else 4096.0
        )

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        # Audit-gates §10.4 — RuntimeBudgetSampler integration so the GUI can
        # detect the 96% "viewer package" hang scenario early and surface a
        # localized error to the user instead of hanging indefinitely.
        from src.services.comparison.runtime_budget import (
            MemoryBudgetExceeded,
            RuntimeBudgetSampler,
        )

        sampler = RuntimeBudgetSampler(
            spool_dirs=_runtime_budget_spool_dirs_for_folder_request(self.request),
        )
        sampler.start_sampling()
        try:
            pipeline = FolderComparePipeline(self.request)

            def progress(_stage: str, percent: int, message: str) -> None:
                self.progress.emit(percent, message)

            result = pipeline.run(
                progress_callback=progress,
                is_cancelled=lambda: self._cancelled,
                runtime_sampler=sampler,
                viewer_memory_cap_mb=self.viewer_memory_cap_mb,
                first_review_ready_callback=self.review_ready.emit,
            )
            self.finished.emit(result)
        except MemoryBudgetExceeded as exc:
            logger.exception(
                "Viewer memory budget exceeded at %s (current=%.1fMB > max=%.1fMB)",
                exc.stage,
                exc.current_mb,
                exc.max_mb,
            )
            self.error.emit(
                f"메모리 한계 초과 ({exc.stage}): "
                f"현재 {exc.current_mb:.0f}MB > 한계 {exc.max_mb:.0f}MB. "
                f"화질을 낮추거나(DPI 80/200) 'lazy' 프리셋을 사용하세요."
            )
        except Exception as exc:
            logger.exception("Korean folder comparison flow failed")
            self.error.emit(format_auto_compare_error(exc, self.request))
        finally:
            try:
                sampler.stop()
            except Exception:
                pass


def _initial_tile_viewport_from_overlays(overlays: Sequence[dict[str, Any]]) -> Optional[dict[str, float]]:
    """Return a bounded first-review tile window around the top visible issue."""

    selected = [item for item in overlays if isinstance(item, dict) and item.get("selected_for_review")]
    selected_ids = {id(item) for item in selected}
    candidates = selected + [item for item in overlays if isinstance(item, dict) and id(item) not in selected_ids]
    for overlay in candidates:
        value = overlay.get("after_bbox_px") or overlay.get("before_bbox_px")
        rect: Optional[dict[str, float]] = None
        if isinstance(value, dict):
            try:
                rect = {
                    "x": float(value.get("x", 0.0)),
                    "y": float(value.get("y", 0.0)),
                    "width": max(1.0, float(value.get("width", 1.0))),
                    "height": max(1.0, float(value.get("height", 1.0))),
                }
            except (TypeError, ValueError):
                rect = None
        elif isinstance(value, (list, tuple)) and len(value) >= 4:
            try:
                left = float(value[0])
                top = float(value[1])
                right = float(value[2])
                bottom = float(value[3])
                rect = {"x": left, "y": top, "width": max(1.0, right - left), "height": max(1.0, bottom - top)}
            except (TypeError, ValueError):
                rect = None
        if not rect:
            continue
        window = float(GPU_VIEWER_TILE_SIZE)
        center_x = float(rect["x"]) + float(rect["width"]) / 2.0
        center_y = float(rect["y"]) + float(rect["height"]) / 2.0
        return {
            "x": max(0.0, center_x - window / 2.0),
            "y": max(0.0, center_y - window / 2.0),
            "width": window,
            "height": window,
        }
    return None


class PairPreviewRenderWorker(QThread):
    """Render one selected drawing pair and enrich overlay bbox metadata."""

    finished = Signal(str, object, object)  # pair_id, viewer_pair, overlays
    error = Signal(str, str)  # pair_id, message

    def __init__(
        self,
        *,
        pair_id: str,
        viewer_pair: dict,
        dxf_cache_dir: Path,
        viewer_root: Path,
        viewer_cache_root: Optional[Path] = None,
        build_lod_tiles: bool = True,
    ):
        super().__init__()
        self.pair_id = pair_id
        self.viewer_pair = dict(viewer_pair)
        self.dxf_cache_dir = Path(dxf_cache_dir)
        self.viewer_root = Path(viewer_root)
        self.viewer_cache_root = Path(viewer_cache_root) if viewer_cache_root else self.viewer_root
        self.build_lod_tiles = bool(build_lod_tiles)

    def run(self) -> None:
        try:
            started = perf_counter()
            source_a = self.viewer_pair.get("source_a")
            source_b = self.viewer_pair.get("source_b")
            image_dir = self.viewer_root / "images"
            page_a = _int_value(
                self.viewer_pair.get("page_a", self.viewer_pair.get("page", 0)),
                0,
            )
            page_b = _int_value(
                self.viewer_pair.get("page_b", self.viewer_pair.get("page", 0)),
                0,
            )
            rendered = _render_pair_backgrounds_with_timeout(
                pair_id=self.pair_id,
                source_a=Path(source_a) if source_a else None,
                source_b=Path(source_b) if source_b else None,
                image_dir=image_dir,
                dxf_cache_dir=self.dxf_cache_dir,
                dpi=80,
                max_edge_px=2400,
                timeout_seconds=GPU_VIEWER_RENDER_TIMEOUT_SECONDS,
                page_a=page_a,
                page_b=page_b,
            )
            render_ms = round((perf_counter() - started) * 1000.0, 3)
            render_status = str(rendered.get("render_status") or "render_failed")
            render_warnings = [str(item) for item in (rendered.get("warnings") or [])]

            updated_pair = dict(self.viewer_pair)
            updated_pair.update(
                {
                    "before_image": rendered.get("before_image") or "",
                    "after_image": rendered.get("after_image") or "",
                    "before_transform": rendered.get("before_transform") or {},
                    "after_transform": rendered.get("after_transform") or {},
                    "background_type": "png" if render_status == "rendered" else "none",
                    "render_status": render_status,
                    "render_warning": "\n".join(render_warnings),
                }
            )
            pages_manifest_path = _resolve_viewer_artifact_path(
                updated_pair.get("overlay_pages_manifest"),
                self.viewer_root,
            )
            use_paged_overlay_store = (
                pages_manifest_path is not None
                and pages_manifest_path.exists()
                and _viewer_pair_is_pdf(updated_pair)
            )
            overlay_value = str(updated_pair.get("overlay_json") or "")
            overlay_path = Path(overlay_value) if overlay_value else None
            payload: dict = {}
            if use_paged_overlay_store:
                store = OverlayPageStore(pages_manifest_path)
                overlays = list(store.iter_visible_pdf_pages(page_a, page_b))
                updated_pair["_overlay_materialization_scope"] = "visible_pdf_page"
                updated_pair["_overlay_page_files_read"] = int(store.last_page_files_read)
                updated_pair["_overlay_page_files_skipped"] = int(store.last_page_files_skipped)
                updated_pair["_overlay_declared_count"] = int(store.overlay_count)
            else:
                payload = _read_json_file(overlay_path) if overlay_path and overlay_path.exists() else {}
                overlays = payload.get("overlays", []) if isinstance(payload, dict) else []
                if not isinstance(overlays, list):
                    overlays = []
            cache_hit = False
            tile_ms = 0.0
            tile_manifest: dict = {}
            if render_status == "rendered":
                for overlay in overlays:
                    if not isinstance(overlay, dict):
                        continue
                    before_px = _cad_bbox_to_pixel_rect(overlay.get("old_bbox") or overlay.get("bbox"), updated_pair["before_transform"])
                    after_px = _cad_bbox_to_pixel_rect(overlay.get("bbox"), updated_pair["after_transform"])
                    if before_px:
                        overlay["before_bbox_px"] = before_px
                    if after_px:
                        overlay["after_bbox_px"] = after_px
                if overlay_path and not use_paged_overlay_store:
                    payload.update(
                        {
                            "viewer_coordinate_space": "pixel",
                            "before_transform": updated_pair["before_transform"],
                            "after_transform": updated_pair["after_transform"],
                            "before_image": updated_pair["before_image"],
                            "after_image": updated_pair["after_image"],
                            "overlays": overlays,
                        }
                    )
                    _write_json_file(overlay_path, payload)
                if self.build_lod_tiles:
                    tile_options = ViewerTileCacheOptions(
                        tile_size=GPU_VIEWER_TILE_SIZE,
                        max_visible_overlays=GPU_VIEWER_MAX_VISIBLE_OVERLAYS,
                        viewer_memory_budget_mb=GPU_VIEWER_MEMORY_BUDGET_MB,
                    )
                    cache_key = viewer_cache_key(
                        pair_uuid=self.pair_id,
                        source_a=Path(source_a) if source_a else None,
                        source_b=Path(source_b) if source_b else None,
                        options=tile_options,
                    )
                    tile_started = perf_counter()
                    cache_tiles_manifest = self.viewer_cache_root / "tiles_manifest.json"
                    cache_hit = tiles_manifest_is_current(cache_tiles_manifest, self.pair_id, cache_key)
                    if cache_hit:
                        tile_manifest = _read_json_file(cache_tiles_manifest).get("pairs", {}).get(self.pair_id, {})
                    else:
                        initial_viewport = _initial_tile_viewport_from_overlays(overlays)
                        if initial_viewport:
                            tile_manifest = write_pair_visible_tile_cache(
                                pair_uuid=self.pair_id,
                                before_image=str(updated_pair.get("before_image") or ""),
                                after_image=str(updated_pair.get("after_image") or ""),
                                overlays=overlays,
                                tile_root=self.viewer_cache_root / "tiles",
                                overlay_tile_root=self.viewer_cache_root / "overlay_tiles",
                                options=tile_options,
                                viewport_rect=initial_viewport,
                                zoom=1.0,
                                prefetch_radius=1,
                                cache_key=cache_key,
                            )
                        else:
                            tile_manifest = write_pair_tile_cache(
                                pair_uuid=self.pair_id,
                                before_image=str(updated_pair.get("before_image") or ""),
                                after_image=str(updated_pair.get("after_image") or ""),
                                overlays=overlays,
                                tile_root=self.viewer_cache_root / "tiles",
                                overlay_tile_root=self.viewer_cache_root / "overlay_tiles",
                                options=tile_options,
                                cache_key=cache_key,
                            )
                        append_pair_to_tiles_manifest_jsonl(self.viewer_cache_root, tile_manifest)
                        materialise_tiles_manifest_from_jsonl(self.viewer_cache_root, keep_jsonl=False)
                    updated_pair["tile_manifest"] = str(pair_tile_manifest_path(self.viewer_cache_root / "tiles", self.pair_id))
                    tile_ms = round((perf_counter() - tile_started) * 1000.0, 3)
                    updated_pair["tile_cache_key"] = cache_key
                    updated_pair["lod_tile_count"] = int(tile_manifest.get("tile_count", 0))
                    updated_pair["overlay_tile_count"] = int(tile_manifest.get("overlay_tile_count", 0))
                else:
                    updated_pair["tile_manifest"] = ""
                    updated_pair["tile_cache_key"] = ""
                    updated_pair["lod_tile_count"] = 0
                    updated_pair["overlay_tile_count"] = 0
            else:
                updated_pair["tile_cache_key"] = ""
                updated_pair["lod_tile_count"] = 0
                updated_pair["overlay_tile_count"] = 0
            append_viewer_perf_event(
                self.viewer_root,
                "pair_render",
                pair_uuid=self.pair_id,
                render_ms=render_ms,
                tile_ms=tile_ms,
                tile_cache_attempted=bool(self.build_lod_tiles and render_status == "rendered"),
                tile_cache_hit=cache_hit,
                tile_count=updated_pair["lod_tile_count"],
                overlay_tile_count=updated_pair["overlay_tile_count"],
                tile_pyramid_ms=float((tile_manifest or {}).get("tile_pyramid_ms") or 0.0),
                overlay_tile_ms=float((tile_manifest or {}).get("overlay_tile_ms") or 0.0),
                tile_cache_write_ms=float((tile_manifest or {}).get("tile_cache_write_ms") or 0.0),
                tile_payload_bytes=int((tile_manifest or {}).get("tile_payload_bytes") or 0),
                overlay_tile_payload_bytes=int((tile_manifest or {}).get("overlay_tile_payload_bytes") or 0),
                cache_total_estimated_bytes=int((tile_manifest or {}).get("cache_total_estimated_bytes") or 0),
                cache_byte_limit=int((tile_manifest or {}).get("cache_byte_limit") or 0),
                eviction_count=int((tile_manifest or {}).get("eviction_count") or 0),
                evicted_pair_count=int((tile_manifest or {}).get("evicted_pair_count") or 0),
                evicted_estimated_bytes=int((tile_manifest or {}).get("evicted_estimated_bytes") or 0),
                cache_retained_estimated_bytes=int((tile_manifest or {}).get("cache_retained_estimated_bytes") or 0),
                cache_estimated_bytes_before_eviction=int(
                    (tile_manifest or {}).get("cache_estimated_bytes_before_eviction") or 0
                ),
                eviction_reason=str((tile_manifest or {}).get("eviction_reason") or ""),
                overlay_count=int((tile_manifest or {}).get("overlay_count") or len(overlays)),
                materialized_overlay_count=int((tile_manifest or {}).get("materialized_overlay_count") or 0),
                overlay_omitted_count=int((tile_manifest or {}).get("overlay_omitted_count") or 0),
                overlay_load_strategy="paged_overlay_store" if use_paged_overlay_store else "overlay_json",
                overlay_page_files_read=int(updated_pair.get("_overlay_page_files_read") or 0),
                overlay_page_files_skipped=int(updated_pair.get("_overlay_page_files_skipped") or 0),
                declared_overlay_count=int(updated_pair.get("_overlay_declared_count") or len(overlays)),
                generation_mode=str((tile_manifest or {}).get("generation_mode") or "full_pyramid"),
                pyramid_complete=bool((tile_manifest or {}).get("pyramid_complete", True)),
                materialized_tile_count=int((tile_manifest or {}).get("materialized_tile_count") or updated_pair["lod_tile_count"]),
                planned_tile_count=int((tile_manifest or {}).get("planned_tile_count") or updated_pair["lod_tile_count"]),
            )
            self.finished.emit(self.pair_id, updated_pair, overlays)
        except Exception as exc:
            logger.exception("Selected pair preview render failed")
            self.error.emit(self.pair_id, str(exc))


class VisibleTileWindowWorker(QThread):
    """Materialize one newly visible tile window without blocking the GUI."""

    finished = Signal(str, int, object)  # pair_id, generation, manifest
    error = Signal(str, int, str)

    def __init__(
        self,
        *,
        pair_id: str,
        generation: int,
        viewer_pair: dict,
        overlays: Sequence[dict[str, Any]],
        viewer_root: Path,
        viewer_cache_root: Path,
        viewport_rect: dict[str, float],
        zoom: float,
        cache_key: str,
    ) -> None:
        super().__init__()
        self.pair_id = str(pair_id)
        self.generation = int(generation)
        self.viewer_pair = dict(viewer_pair or {})
        self.overlays = [item for item in overlays if isinstance(item, dict)]
        self.viewer_root = Path(viewer_root)
        self.viewer_cache_root = Path(viewer_cache_root)
        self.viewport_rect = dict(viewport_rect or {})
        self.zoom = float(zoom or 1.0)
        self.cache_key = str(cache_key or "")

    def run(self) -> None:
        try:
            started = perf_counter()
            tile_options = ViewerTileCacheOptions(
                tile_size=GPU_VIEWER_TILE_SIZE,
                max_visible_overlays=GPU_VIEWER_MAX_VISIBLE_OVERLAYS,
                viewer_memory_budget_mb=GPU_VIEWER_MEMORY_BUDGET_MB,
            )
            before_image = _resolve_viewer_artifact_path(
                self.viewer_pair.get("before_image"), self.viewer_root,
            )
            after_image = _resolve_viewer_artifact_path(
                self.viewer_pair.get("after_image"), self.viewer_root,
            )
            if not before_image or not after_image or not before_image.exists() or not after_image.exists():
                raise RuntimeError("visible tile source images are missing")

            cache_key = self.cache_key
            if not cache_key:
                source_a = str(self.viewer_pair.get("source_a") or "")
                source_b = str(self.viewer_pair.get("source_b") or "")
                cache_key = viewer_cache_key(
                    pair_uuid=self.pair_id,
                    source_a=Path(source_a) if source_a else before_image,
                    source_b=Path(source_b) if source_b else after_image,
                    options=tile_options,
                )

            manifest = write_pair_visible_tile_cache(
                pair_uuid=self.pair_id,
                before_image=str(before_image),
                after_image=str(after_image),
                overlays=self.overlays,
                tile_root=self.viewer_cache_root / "tiles",
                overlay_tile_root=self.viewer_cache_root / "overlay_tiles",
                options=tile_options,
                viewport_rect=self.viewport_rect,
                zoom=self.zoom,
                prefetch_radius=1,
                cache_key=cache_key,
            )
            append_pair_to_tiles_manifest_jsonl(self.viewer_cache_root, manifest)
            materialise_tiles_manifest_from_jsonl(self.viewer_cache_root, keep_jsonl=False)
            append_viewer_perf_event(
                self.viewer_root,
                "visible_tile_window_materialise",
                pair_uuid=self.pair_id,
                generation=self.generation,
                tile_count=int(manifest.get("tile_count") or 0),
                materialized_tile_count=int(manifest.get("materialized_tile_count") or 0),
                planned_tile_count=int(manifest.get("planned_tile_count") or 0),
                omitted_tile_count=int(manifest.get("omitted_tile_count") or 0),
                overlay_tile_count=int(manifest.get("overlay_tile_count") or 0),
                tile_payload_bytes=int(manifest.get("tile_payload_bytes") or 0),
                overlay_tile_payload_bytes=int(manifest.get("overlay_tile_payload_bytes") or 0),
                cache_total_estimated_bytes=int(manifest.get("cache_total_estimated_bytes") or 0),
                cache_byte_limit=int(manifest.get("cache_byte_limit") or 0),
                eviction_count=int(manifest.get("eviction_count") or 0),
                evicted_pair_count=int(manifest.get("evicted_pair_count") or 0),
                evicted_estimated_bytes=int(manifest.get("evicted_estimated_bytes") or 0),
                cache_retained_estimated_bytes=int(manifest.get("cache_retained_estimated_bytes") or 0),
                cache_estimated_bytes_before_eviction=int(
                    manifest.get("cache_estimated_bytes_before_eviction") or 0
                ),
                eviction_reason=str(manifest.get("eviction_reason") or ""),
                overlay_count=int(manifest.get("overlay_count") or 0),
                materialized_overlay_count=int(manifest.get("materialized_overlay_count") or 0),
                outside_viewport_overlay_count=int(manifest.get("outside_viewport_overlay_count") or 0),
                zoom=self.zoom,
                tile_window_ms=round((perf_counter() - started) * 1000.0, 3),
            )
            self.finished.emit(self.pair_id, self.generation, manifest)
        except Exception as exc:
            logger.exception("Visible tile window materialization failed")
            self.error.emit(self.pair_id, self.generation, str(exc))


class FullZoneTreeOverlayLoadWorker(QThread):
    """Load overlay records off the GUI thread for large tree rebuilds."""

    loaded = Signal(str, int, object)  # pair_id, generation, payload
    failed = Signal(str, int, str)

    def __init__(
        self,
        *,
        pair_id: str,
        generation: int,
        overlay_path: Path,
        viewer_pair: dict,
        overlay_pages_manifest_path: Optional[Path] = None,
    ):
        super().__init__()
        self.pair_id = str(pair_id)
        self.generation = int(generation)
        self.overlay_path = Path(overlay_path)
        self.overlay_pages_manifest_path = (
            Path(overlay_pages_manifest_path) if overlay_pages_manifest_path else None
        )
        self.viewer_pair = dict(viewer_pair or {})
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        started = perf_counter()
        try:
            overlay_bytes = 0
            strategy = "overlay_json"
            page_count = 0
            declared_overlay_count = 0
            overlays: list[dict] = []
            page_a = _int_value(self.viewer_pair.get("page_a", self.viewer_pair.get("page", 0)), 0)
            page_b = _int_value(self.viewer_pair.get("page_b", self.viewer_pair.get("page", 0)), 0)
            filter_to_pdf_page = _viewer_pair_is_pdf(self.viewer_pair)
            if (
                self.overlay_pages_manifest_path is not None
                and self.overlay_pages_manifest_path.exists()
            ):
                strategy = "paged_overlay_store"
                store = OverlayPageStore(self.overlay_pages_manifest_path)
                page_count = int(store.page_count)
                declared_overlay_count = int(store.overlay_count)
                overlay_bytes = int(store.total_bytes)
                source_iter = (
                    store.iter_visible_pdf_pages(page_a, page_b)
                    if filter_to_pdf_page
                    else store.iter_overlays()
                )
                for overlay in source_iter:
                    if self._cancelled:
                        return
                    overlays.append(overlay)
                page_files_read = int(store.last_page_files_read)
                page_files_skipped = int(store.last_page_files_skipped)
            else:
                page_files_read = 0
                page_files_skipped = 0
                payload = json.loads(self.overlay_path.read_text(encoding="utf-8"))
                loaded = payload.get("overlays", []) if isinstance(payload, dict) else []
                if isinstance(loaded, list):
                    overlays = [item for item in loaded if isinstance(item, dict)]
                declared_overlay_count = (
                    int(payload.get("overlay_total_count") or payload.get("overlay_count") or len(overlays))
                    if isinstance(payload, dict)
                    else len(overlays)
                )
                try:
                    overlay_bytes = int(self.overlay_path.stat().st_size)
                except OSError:
                    overlay_bytes = 0
            if self._cancelled:
                return
            visible = list(overlays)
            if filter_to_pdf_page and strategy != "paged_overlay_store":
                visible = _filter_overlays_by_pdf_pages(visible, page_a, page_b)
            load_ms = round((perf_counter() - started) * 1000.0, 3)
            self.loaded.emit(
                self.pair_id,
                self.generation,
                {
                    "overlays": overlays,
                    "visible": visible,
                    "overlay_load_ms": load_ms,
                    "overlay_json_bytes": overlay_bytes,
                    "overlay_load_worker": True,
                    "overlay_load_strategy": strategy,
                    "overlay_page_count": page_count,
                    "overlay_page_files_read": page_files_read,
                    "overlay_page_files_skipped": page_files_skipped,
                    "declared_overlay_count": max(declared_overlay_count, len(overlays)),
                    "materialized_overlay_count": len(overlays),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Full zone tree overlay load failed")
            self.failed.emit(self.pair_id, self.generation, str(exc))


class FullZoneTreePlanWorker(QThread):
    """Build the serialisable full-zone tree plan off the GUI thread."""

    planned = Signal(str, int, object)  # pair_id, generation, payload
    failed = Signal(str, int, str)

    def __init__(
        self,
        *,
        pair_id: str,
        generation: int,
        overlays: list[dict],
        dashboard_issues: list[dict],
        category_by_zone: Mapping[str, Any],
        active_zone_id: str,
        clustering_enabled: bool,
    ):
        super().__init__()
        self.pair_id = str(pair_id)
        self.generation = int(generation)
        self.overlays = list(overlays or [])
        self.dashboard_issues = list(dashboard_issues or [])
        self.category_by_zone = dict(category_by_zone or {})
        self.active_zone_id = str(active_zone_id or "")
        self.clustering_enabled = bool(clustering_enabled)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        started = perf_counter()
        try:
            plan, active_issue_by_zone = _build_zone_tree_plan_data_v2(
                dashboard_issues=self.dashboard_issues,
                overlays=self.overlays,
                preview_zones=[],
                category_by_zone=self.category_by_zone,
                active_zone_id=self.active_zone_id,
                allow_clustering=False,
                clustering_enabled=self.clustering_enabled,
                prefer_overlays=True,
            )
            if self._cancelled:
                return
            self.planned.emit(
                self.pair_id,
                self.generation,
                {
                    "plan": plan,
                    "active_issue_by_zone": active_issue_by_zone,
                    "plan_build_ms": round((perf_counter() - started) * 1000.0, 3),
                    "plan_build_worker": True,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Full zone tree plan build failed")
            self.failed.emit(self.pair_id, self.generation, str(exc))


class ZoneRenderProcessController(QObject):
    """Persistent JSONL render subprocess for one active drawing pair."""

    finished = Signal(str, str, object, object, object)
    error = Signal(str, str, str, str, str)

    def __init__(
        self,
        *,
        timeout_ms: int = 10_000,
        source_timeout_ms: int = 30_000,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.timeout_ms = int(timeout_ms)
        self.source_timeout_ms = max(int(source_timeout_ms), self.timeout_ms)
        self._process: Optional[QProcess] = None
        self._process_key = ""
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._active_context: Optional[dict] = None
        self._process_ready = False
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)

    def is_busy(self) -> bool:
        return self._active_context is not None

    def prewarm(self, process_key: str) -> None:
        if not self._process or self._process.state() == QProcess.NotRunning:
            self._ensure_process(process_key)

    def shutdown(self) -> None:
        self._timeout_timer.stop()
        self._active_context = None
        process = self._process
        if process:
            if process.state() != QProcess.NotRunning:
                process.kill()
                process.waitForFinished(1500)
            process.deleteLater()
        self._process = None
        self._process_key = ""
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._process_ready = False

    def render(
        self,
        *,
        process_key: str,
        request: dict,
        viewer_pair: dict,
        overlay: dict,
        overlays: list[dict],
    ) -> bool:
        if self.is_busy():
            return False
        if not self._ensure_process(process_key):
            self.error.emit(
                str(request.get("pair_uuid") or ""),
                str(request.get("zone_id") or ""),
                "렌더 프로세스를 시작할 수 없습니다.",
                "render_failed",
                str(request.get("request_id") or ""),
            )
            return False
        timeout_ms = self.source_timeout_ms if bool(request.get("prefer_source_render")) else self.timeout_ms
        self._active_context = {
            "request_id": str(request.get("request_id") or ""),
            "pair_id": str(request.get("pair_uuid") or ""),
            "zone_id": str(request.get("zone_id") or ""),
            "prefer_source_render": bool(request.get("prefer_source_render")),
            "timeout_ms": timeout_ms,
            "started_at": perf_counter(),
            "viewer_pair": dict(viewer_pair or {}),
            "overlay": dict(overlay or {}),
            "overlays": [dict(item) for item in overlays if isinstance(item, dict)],
        }
        line = json.dumps(request, ensure_ascii=False) + "\n"
        assert self._process is not None
        self._process.write(line.encode("utf-8"))
        self._process.waitForBytesWritten(1000)
        if self._process_ready:
            self._timeout_timer.start(timeout_ms)
        else:
            self._timeout_timer.start(max(timeout_ms, 30_000))
        return True

    def _ensure_process(self, process_key: str) -> bool:
        if self._process_key and self._process_key != process_key:
            self.shutdown()
        if self._process and self._process.state() != QProcess.NotRunning:
            return True
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._process_ready = False
        process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        process.setProcessEnvironment(env)
        program, args = worker_command_for_module(ZONE_RENDER_PROCESS_MODULE)
        process.setProgram(program)
        process.setArguments(args)
        process.setWorkingDirectory(str(_workbench_worker_cwd()))
        process.readyReadStandardOutput.connect(self._on_stdout)
        process.readyReadStandardError.connect(self._on_stderr)
        process.finished.connect(self._on_process_finished)
        process.start()
        if not process.waitForStarted(3000):
            process.deleteLater()
            return False
        self._process = process
        self._process_key = process_key
        return True

    def _on_stdout(self) -> None:
        if not self._process:
            return
        self._stdout_buffer += bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            self._handle_response(line.strip())

    def _on_stderr(self) -> None:
        if not self._process:
            return
        self._stderr_buffer += bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")

    def _handle_response(self, line: str) -> None:
        if not line:
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed zone render response: %s", line[:200])
            return
        if payload.get("event") == "ready":
            self._process_ready = True
            if self._active_context:
                timeout_ms = int(self._active_context.get("timeout_ms") or self.timeout_ms)
                self._timeout_timer.start(timeout_ms)
            return
        context = self._active_context
        if not context:
            return
        request_id = str(payload.get("request_id") or "")
        if request_id and request_id != context.get("request_id"):
            return
        self._timeout_timer.stop()
        self._active_context = None
        pair_id = str(context.get("pair_id") or payload.get("pair_uuid") or "")
        zone_id = str(context.get("zone_id") or payload.get("zone_id") or "")
        if not payload.get("ok"):
            status = str(
                payload.get("reason_code")
                or payload.get("fallback_reason_code")
                or payload.get("render_lifecycle")
                or "render_failed"
            )
            self.error.emit(pair_id, zone_id, str(payload.get("error") or "렌더 실패"), status, request_id)
            return
        result_payload = payload.get("result") or {}
        result_payload.setdefault("prefer_source_render", bool(context.get("prefer_source_render")))
        try:
            result_payload.setdefault(
                "elapsed_ms",
                round((perf_counter() - float(context.get("started_at") or perf_counter())) * 1000.0, 3),
            )
        except (TypeError, ValueError):
            pass
        viewer_pair = dict(context.get("viewer_pair") or {})
        viewer_pair.update(
            {
                "last_zone_crop": result_payload,
                "visual_fidelity": result_payload.get("visual_fidelity"),
                "render_lifecycle": result_payload.get("render_lifecycle"),
                "renderer_backend": result_payload.get("renderer_backend"),
                "cache_key": result_payload.get("cache_key"),
            }
        )
        local_overlays = _local_overlays_for_zone(
            context.get("overlays") or [],
            context.get("overlay") or {},
            zone_id,
            result_payload,
            viewer_pair=viewer_pair,
        )
        self.finished.emit(pair_id, zone_id, result_payload, viewer_pair, local_overlays)

    def _on_timeout(self) -> None:
        context = self._active_context
        if not context:
            return
        pair_id = str(context.get("pair_id") or "")
        zone_id = str(context.get("zone_id") or "")
        self._active_context = None
        process = self._process
        if process and process.state() != QProcess.NotRunning:
            process.kill()
            process.waitForFinished(1500)
            process.deleteLater()
        self._process = None
        self._process_key = ""
        self._process_ready = False
        self.error.emit(
            pair_id,
            zone_id,
            "렌더 시간 초과, 상대 위치만 표시합니다.",
            "full_detail_render_timeout" if context.get("prefer_source_render") else "render_timeout",
            str(context.get("request_id") or ""),
        )

    def _on_process_finished(self, *_args) -> None:
        if self._active_context:
            context = self._active_context
            self._active_context = None
            self._timeout_timer.stop()
            message = self._stderr_buffer.strip() or "렌더 프로세스가 예기치 않게 종료되었습니다."
            self.error.emit(
                str(context.get("pair_id") or ""),
                str(context.get("zone_id") or ""),
                message,
                "full_detail_render_failed" if context.get("prefer_source_render") else "render_failed",
                str(context.get("request_id") or ""),
            )
        self._process = None
        self._process_key = ""
        self._process_ready = False


class ZoneCropRenderWorker(QThread):
    """Render only the selected change-zone inspection crop."""

    finished = Signal(str, str, object, object, object)  # pair_id, zone_id, result, viewer_pair, local overlays
    error = Signal(str, str, str)  # pair_id, zone_id, message

    def __init__(
        self,
        *,
        pair_id: str,
        zone_id: str,
        viewer_pair: dict,
        overlay: dict,
        overlays: list[dict],
        dxf_cache_dir: Path,
        viewer_cache_root: Path,
    ):
        super().__init__()
        self.pair_id = pair_id
        self.zone_id = zone_id
        self.viewer_pair = dict(viewer_pair or {})
        self.overlay = dict(overlay or {})
        self.overlays = [dict(item) for item in overlays if isinstance(item, dict)]
        self.dxf_cache_dir = Path(dxf_cache_dir)
        self.viewer_cache_root = Path(viewer_cache_root)

    def run(self) -> None:
        try:
            source_a = self.viewer_pair.get("source_a")
            source_b = self.viewer_pair.get("source_b")
            if not source_a or not source_b:
                raise ValueError("source drawing paths are missing")
            bbox = union_bboxes(self.overlay.get("old_bbox"), self.overlay.get("bbox"))
            if not bbox:
                raise ValueError("selected zone has no CAD bbox for crop rendering")
            window = canonical_window_from_bbox(bbox, padding_ratio=0.18, min_size=250.0)
            result = render_zone_pair(
                RenderJob(
                    pair_uuid=self.pair_id,
                    zone_id=self.zone_id,
                    source_before=Path(source_a),
                    source_after=Path(source_b),
                    world_window=window,
                    cache_root=self.viewer_cache_root,
                    dxf_cache_dir=self.dxf_cache_dir,
                    alignment=self.viewer_pair.get("alignment"),  # P0-2b
                )
            )
            result_payload = result.to_dict()
            updated_pair = dict(self.viewer_pair)
            updated_pair.update(
                {
                    "last_zone_crop": result_payload,
                    "visual_fidelity": result.visual_fidelity,
                    "render_lifecycle": result.render_lifecycle,
                    "renderer_backend": result.renderer_backend,
                    "cache_key": result.cache_key,
                }
            )
            local_overlays = self._local_overlays(result_payload)
            self.finished.emit(self.pair_id, self.zone_id, result_payload, updated_pair, local_overlays)
        except Exception as exc:
            logger.exception("Selected zone crop render failed")
            self.error.emit(self.pair_id, self.zone_id, str(exc))

    def _local_overlays(self, result_payload: dict) -> list[dict]:
        return _local_overlays_for_zone(
            self.overlays,
            self.overlay,
            self.zone_id,
            result_payload,
            viewer_pair=self.viewer_pair,
        )


def _cad_boxes_intersect(a: object, b: object) -> bool:
    box_a = union_bboxes(a)
    box_b = union_bboxes(b)
    if not box_a or not box_b:
        return False
    return not (
        float(box_a[2]) < float(box_b[0])
        or float(box_a[0]) > float(box_b[2])
        or float(box_a[3]) < float(box_b[1])
        or float(box_a[1]) > float(box_b[3])
    )


def _bbox_for_zone_crop_transform(item: dict, viewer_pair: Optional[dict], *, before: bool) -> object:
    raw = item.get("old_bbox") if before else item.get("bbox")
    if not raw:
        raw = item.get("bbox") or item.get("old_bbox")
    if viewer_pair and _viewer_pair_is_pdf(viewer_pair):
        scaled = scale_pdf_bbox_to_render_pixels(raw, item, viewer_pair)
        if scaled:
            return scaled
    return raw


def _local_overlays_for_zone(
    overlays: list[dict],
    overlay: dict,
    zone_id: str,
    result_payload: dict,
    *,
    viewer_pair: Optional[dict] = None,
) -> list[dict]:
    before_transform = result_payload.get("before_transform") or {}
    after_transform = result_payload.get("after_transform") or {}
    from src.services.comparison.render_alignment import align_marker_bbox  # P0-2b
    after_marker_tf = result_payload.get("after_marker_world_transform")
    window = result_payload.get("world_window") or {}
    try:
        window_bbox = [window["xmin"], window["ymin"], window["xmax"], window["ymax"]]
    except Exception:
        window_bbox = None
    selected: list[dict] = []
    for item_source in overlays:
        item_zone_id = str(item_source.get("zone_id") or "")
        before_bbox = _bbox_for_zone_crop_transform(item_source, viewer_pair, before=True)
        after_bbox = align_marker_bbox(_bbox_for_zone_crop_transform(item_source, viewer_pair, before=False), after_marker_tf)
        bbox = union_bboxes(before_bbox, after_bbox)
        if item_zone_id != zone_id and window_bbox and bbox and not _cad_boxes_intersect(bbox, window_bbox):
            continue
        item = dict(item_source)
        before_px = zone_bbox_to_pixel_rect(before_bbox, before_transform)
        after_px = zone_bbox_to_pixel_rect(after_bbox, after_transform)
        if before_px:
            item["before_bbox_px"] = before_px
        if after_px:
            item["after_bbox_px"] = after_px
        item["visual_fidelity"] = result_payload.get("visual_fidelity") or "cad_render"
        item["render_lifecycle"] = result_payload.get("render_lifecycle") or "ready"
        selected.append(item)
        if len(selected) >= 300 and item_zone_id != zone_id:
            break
    if not any(str(item.get("zone_id") or "") == zone_id for item in selected):
        item = dict(overlay)
        before_bbox = _bbox_for_zone_crop_transform(item, viewer_pair, before=True)
        after_bbox = align_marker_bbox(_bbox_for_zone_crop_transform(item, viewer_pair, before=False), after_marker_tf)
        before_px = zone_bbox_to_pixel_rect(before_bbox, before_transform)
        after_px = zone_bbox_to_pixel_rect(after_bbox, after_transform)
        if before_px:
            item["before_bbox_px"] = before_px
        if after_px:
            item["after_bbox_px"] = after_px
        selected.insert(0, item)
    return selected


class ZonePreviewView(QGraphicsView):
    """Static drawing preview with zoom, pan, and zone rectangle overlays."""

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._rect_items: dict[str, QGraphicsRectItem] = {}
        self._selected_zone_id = ""
        self._overlay_opacity_scale: float = 1.0

    def set_overlay_opacity_scale(self, scale: float) -> None:
        """Fallback widget mirror of GpuDrawingViewport.set_overlay_opacity_scale."""

        try:
            value = float(scale)
        except (TypeError, ValueError):
            value = 1.0
        self._overlay_opacity_scale = max(0.3, min(1.0, value))
        for item in self._rect_items.values():
            try:
                item.setOpacity(self._overlay_opacity_scale)
            except Exception:
                continue

    def load_preview(
        self,
        image_path: str,
        overlays: list[dict],
        *,
        before: bool = False,
    ) -> None:
        self._scene.clear()
        self._rect_items = {}
        if not image_path or not Path(image_path).exists():
            self._scene.addText("미리보기를 만들 수 없습니다")
            return
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self._scene.addText("미리보기 이미지를 열 수 없습니다")
            return
        self._scene.addPixmap(pixmap)
        for overlay in overlays:
            bbox = overlay.get("before_bbox_px") if before else overlay.get("after_bbox_px")
            if not bbox or len(bbox) < 4:
                continue
            zone_id = str(overlay.get("zone_id") or "")
            color = self._overlay_color(str(overlay.get("change_type") or "mixed"))
            item = self._scene.addRect(
                float(bbox[0]),
                float(bbox[1]),
                max(1.0, float(bbox[2]) - float(bbox[0])),
                max(1.0, float(bbox[3]) - float(bbox[1])),
                QPen(color, 2.0),
            )
            self._rect_items[zone_id] = item
        self.setSceneRect(self._scene.itemsBoundingRect())
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
        # Reapply user-controlled opacity to fresh overlay items
        if self._overlay_opacity_scale != 1.0:
            for item in self._rect_items.values():
                try:
                    item.setOpacity(self._overlay_opacity_scale)
                except Exception:
                    continue

    def load_preview(
        self,
        image_path: str,
        overlays: list[dict],
        *,
        before: bool = False,
        fallback_message: str = "미리보기 준비 전 - 상대 위치로 변경구역을 표시합니다",
    ) -> None:
        """Load a rendered PNG when available, otherwise draw relative overlays."""
        self._scene.clear()
        self._rect_items = {}
        if not image_path or not Path(image_path).exists():
            self._draw_relative_canvas(overlays, before=before, message=fallback_message)
            return
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self._draw_relative_canvas(
                overlays,
                before=before,
                message="미리보기 이미지를 열 수 없어 상대 위치로 표시합니다",
            )
            return
        self._scene.addPixmap(pixmap)
        for overlay in overlays:
            bbox = overlay.get("before_bbox_px") if before else overlay.get("after_bbox_px")
            rect = self._bbox_rect(bbox)
            if not rect:
                continue
            zone_id = str(overlay.get("zone_id") or "")
            color = self._overlay_color(str(overlay.get("change_type") or "mixed"))
            item = self._scene.addRect(
                rect[0],
                rect[1],
                rect[2],
                rect[3],
                self._overlay_pen(str(overlay.get("change_type") or "mixed"), selected=zone_id == self._selected_zone_id),
            )
            if zone_id == self._selected_zone_id:
                item.setBrush(QBrush(QColor(0, 95, 204, 36)))
            self._rect_items[zone_id] = item
            self._add_zone_label(zone_id, rect, color)
        self.setSceneRect(self._scene.itemsBoundingRect())
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def _draw_relative_canvas(
        self,
        overlays: list[dict],
        *,
        before: bool = False,
        message: str = "미리보기 준비 전 - 상대 위치로 변경구역을 표시합니다",
    ) -> None:
        placeholder = QPixmap(str(_drawing_compare_asset_path("preview_placeholder.png")))
        if not placeholder.isNull():
            placeholder = placeholder.scaled(800, 500, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._scene.addPixmap(placeholder)
            width = float(placeholder.width())
            height = float(placeholder.height())
            self._scene.addRect(0, 0, width, height, QPen(QColor("#9CA3AF"), 1.0))
        else:
            width = 1600.0
            height = 1000.0
            self._scene.addRect(0, 0, width, height, QPen(QColor("#9CA3AF"), 1.0))
        text_item = self._scene.addText(message)
        text_item.setDefaultTextColor(QColor("#111827"))
        text_item.setPos(20, 18)
        for overlay in overlays:
            bbox = overlay.get("normalized_bbox") or {}
            if not isinstance(bbox, dict):
                continue
            try:
                x = float(bbox.get("x", 0.0)) * width
                y = float(bbox.get("y", 0.0)) * height
                w = max(2.0, float(bbox.get("width", 0.0)) * width)
                h = max(2.0, float(bbox.get("height", 0.0)) * height)
            except (TypeError, ValueError):
                continue
            zone_id = str(overlay.get("zone_id") or "")
            color = self._overlay_color(str(overlay.get("change_type") or "mixed"))
            item = self._scene.addRect(
                x,
                y,
                w,
                h,
                self._overlay_pen(str(overlay.get("change_type") or "mixed"), selected=zone_id == self._selected_zone_id),
            )
            if zone_id == self._selected_zone_id:
                item.setBrush(QBrush(QColor(0, 95, 204, 36)))
            self._rect_items[zone_id] = item
            self._add_zone_label(zone_id, (x, y, w, h), color)
        self.setSceneRect(0, 0, width, height)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def _bbox_rect(self, bbox) -> Optional[tuple[float, float, float, float]]:
        if isinstance(bbox, dict):
            try:
                return (
                    float(bbox.get("x", 0.0)),
                    float(bbox.get("y", 0.0)),
                    max(1.0, float(bbox.get("width", 0.0))),
                    max(1.0, float(bbox.get("height", 0.0))),
                )
            except (TypeError, ValueError):
                return None
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            try:
                left = float(bbox[0])
                top = float(bbox[1])
                right = float(bbox[2])
                bottom = float(bbox[3])
                return (left, top, max(1.0, right - left), max(1.0, bottom - top))
            except (TypeError, ValueError):
                return None
        return None

    def set_selected_zone(self, zone_id: str) -> None:
        self._selected_zone_id = zone_id
        for key, item in self._rect_items.items():
            pen = item.pen()
            pen.setWidthF(5.0 if key == zone_id else 2.0)
            item.setPen(pen)
            item.setBrush(QBrush(QColor(0, 95, 204, 36)) if key == zone_id else QBrush(Qt.NoBrush))

    def focus_zone(self, zone_id: str, padding_ratio: float = 0.25) -> None:
        self.set_selected_zone(zone_id)
        item = self._rect_items.get(zone_id)
        if item:
            rect = item.rect()
            padding = max(80.0, max(rect.width(), rect.height()) * padding_ratio)
            self.fitInView(rect.adjusted(-padding, -padding, padding, padding), Qt.KeepAspectRatio)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def _overlay_color(self, change_type: str) -> QColor:
        return {
            "added": QColor("#1f9d55"),
            "deleted": QColor("#d1242f"),
            "modified": QColor("#bf8700"),
            "moved": QColor("#0969da"),
            "mixed": QColor("#8250df"),
        }.get(change_type, QColor("#57606a"))

    def _overlay_pen(self, change_type: str, *, selected: bool = False) -> QPen:
        color = self._overlay_color(change_type)
        pen = QPen(color, 5.0 if selected else 2.0)
        normalized = (change_type or "").lower()
        if "delete" in normalized or "remove" in normalized:
            pen.setStyle(Qt.DashLine)
        elif "move" in normalized:
            pen.setStyle(Qt.DashDotLine)
        elif "modified" in normalized or "mod" in normalized:
            pen.setWidthF(3.0 if not selected else 5.0)
        else:
            pen.setStyle(Qt.SolidLine)
        return pen

    def _add_zone_label(self, zone_id: str, rect: tuple[float, float, float, float], color: QColor) -> None:
        if not zone_id:
            return
        label = self._scene.addText(zone_id)
        label.setDefaultTextColor(QColor("#111827"))
        label.setPos(rect[0], max(0.0, rect[1] - 22.0))
        background = self._scene.addRect(
            rect[0] - 3.0,
            max(0.0, rect[1] - 24.0),
            max(46.0, len(zone_id) * 8.0),
            20.0,
            QPen(color, 1.0),
            QBrush(QColor(255, 255, 255, 220)),
        )
        background.setZValue(10)
        label.setZValue(11)


class GpuDrawingViewport(QWidget):
    """Qt Quick accelerated drawing viewport with QGraphicsView fallback."""

    viewportChanged = Signal(float, float, float)
    # Phase I4 — emitted when user clicks an overlay marker in the QML
    # viewport. Forwarded from the QML root's overlayClicked signal so
    # the workbench can _select_zone_in_list_v2(zone_id).
    overlayClicked = Signal(str)
    tileWindowMissing = Signal(str, object, float)

    def __init__(self):
        super().__init__()
        self._fallback = ZonePreviewView()
        self._quick = None
        self._quick_ready = False
        self._syncing = False
        self._last_overlays: list[dict] = []
        self._last_image_path = ""
        self._last_before = False
        self._last_fallback_message = "미리보기 준비 전 - 상대 위치로 변경구역을 표시합니다."
        self._tile_manifest: dict = {}
        self._viewer_root: Optional[Path] = None
        self._pair_id = ""
        self._last_missing_tile_request_key = ""
        self._selected_zone_id = ""
        self._overlays_by_zone: dict[str, dict] = {}
        self._overlay_opacity_scale: float = 1.0
        # Phase F P0 — fidelity state pushed to the QML badge/watermark layer.
        # Defaults to ``relative_only`` so the orange watermark shows until the
        # workbench explicitly upgrades the state via ``set_fidelity_state``
        # using viewer_manifest_v2 data.
        self._background_fidelity: str = "relative_only"
        self._render_job_status: str = "idle"
        # Phase G2.7-DIAG: track per-side image DPI so overlay bbox
        # coordinates (which are in `image_pixels` at the comparison
        # `pdf_dpi`, typically 200) can be scaled to match the actual
        # rendered viewer image (`preview_dpi`, typically 400). Without
        # this scale the overlays appear at the wrong position
        # (e.g. half-position when image_dpi=400 vs bbox_dpi=200).
        self._image_dpi_before: float = 0.0
        self._image_dpi_after: float = 0.0
        self._pending_record_perf = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(45)
        self._refresh_timer.timeout.connect(self._flush_refresh_quick_model)
        self._layout = QStackedLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._init_quick_view()
        self._layout.addWidget(self._fallback)
        if self._quick_ready and self._quick:
            self._layout.setCurrentWidget(self._quick)
        else:
            self._layout.setCurrentWidget(self._fallback)

    @property
    def engine_status(self) -> str:
        return "gpu_ready" if self._quick_ready else "fallback_widgets"

    def load_preview(
        self,
        image_path: str,
        overlays: list[dict],
        *,
        before: bool = False,
        fallback_message: str = "미리보기 준비 전 - 상대 위치로 변경구역을 표시합니다.",
        tile_manifest: Optional[dict] = None,
        viewer_root: Optional[Path] = None,
        pair_id: str = "",
        image_dpi: float = 0.0,
    ) -> None:
        """Phase G2.7-DIAG — ``image_dpi`` is the DPI the background PNG
        was rendered at. Used to scale overlay bbox coordinates (which
        are computed at the comparison ``pdf_dpi``, typically 200) to
        match the actual viewer image (``preview_dpi``, typically 400)
        so cloud markers land at the correct visual position.
        """

        self._viewer_root = Path(viewer_root) if viewer_root else None
        resolved_image = _resolve_viewer_artifact_path(image_path, self._viewer_root)
        resolved_image_path = str(resolved_image) if resolved_image else ""
        self._last_overlays = list(overlays or [])
        self._last_image_path = resolved_image_path
        self._last_before = bool(before)
        self._last_fallback_message = fallback_message
        self._tile_manifest = dict(tile_manifest or {})
        self._pair_id = str(pair_id or self._pair_id or "")
        self._last_missing_tile_request_key = ""
        # Track the DPI for the side that just got loaded
        try:
            dpi_value = float(image_dpi or 0.0)
        except (TypeError, ValueError):
            dpi_value = 0.0
        if dpi_value > 0:
            if before:
                self._image_dpi_before = dpi_value
            else:
                self._image_dpi_after = dpi_value
        self._overlays_by_zone = {
            str(overlay.get("zone_id") or overlay.get("id") or ""): overlay
            for overlay in self._last_overlays
            if isinstance(overlay, dict) and (overlay.get("zone_id") or overlay.get("id"))
        }
        if not self._quick_ready:
            self._fallback.load_preview(
                self._last_image_path,
                self._last_overlays,
                before=before,
                fallback_message=fallback_message,
            )
            return
        self._refresh_quick_model(record_perf=True)

    def update_tile_manifest(self, tile_manifest: Optional[dict]) -> None:
        self._tile_manifest = dict(tile_manifest or {})
        if self._quick_ready and self._quick and self._quick.rootObject():
            self._refresh_quick_model(record_perf=False)

    def set_selected_zone(self, zone_id: str) -> None:
        self._selected_zone_id = zone_id
        if self._quick_ready and self._quick and self._quick.rootObject():
            self._quick.rootObject().setProperty("selectedZoneId", zone_id)
            self._refresh_quick_model(record_perf=False)
        self._fallback.set_selected_zone(zone_id)

    def set_overlay_opacity_scale(self, scale: float) -> None:
        """User-controlled overlay transparency.

        Clamped to [0.3, 1.0] so the underlying drawing stays visible without
        completely hiding the markers (full transparency would defeat the
        purpose of the viewer). Applied uniformly to cloud + focus overlays via
        a single QML property the QML side multiplies into every per-entry
        baseline opacity.
        """

        try:
            value = float(scale)
        except (TypeError, ValueError):
            value = 1.0
        clamped = max(0.3, min(1.0, value))
        self._overlay_opacity_scale = clamped
        if self._quick_ready and self._quick and self._quick.rootObject():
            self._quick.rootObject().setProperty("overlayOpacityScale", clamped)
        self._fallback.set_overlay_opacity_scale(clamped)

    @property
    def overlay_opacity_scale(self) -> float:
        return self._overlay_opacity_scale

    def set_fidelity_state(
        self, background_fidelity: str, render_job_status: str = "idle"
    ) -> None:
        """Phase F P0 — push the v2 manifest fidelity + job status to QML.

        ``background_fidelity`` drives the colour of the small badge in the
        top-right corner and forces the diagonal "상대 위치 모드" watermark
        when set to ``relative_only``. ``render_job_status`` adds an italic
        suffix ("· 렌더 중", "· 시간 초과", "· 실패") when not ``idle``.

        Both values are validated against the v2 enum so a typo from the
        translation layer fails silently (badge falls back to gray) rather
        than crashing the viewport.
        """

        valid_fidelity = {
            "exact_world_render",
            "exact_world_tile_sparse",
            "simplified_world_preview",
            "relative_only",
        }
        valid_status = {"idle", "queued", "rendering", "timed_out", "failed"}
        fidelity = background_fidelity if background_fidelity in valid_fidelity else "relative_only"
        status = render_job_status if render_job_status in valid_status else "idle"
        self._background_fidelity = fidelity
        self._render_job_status = status
        if self._quick_ready and self._quick and self._quick.rootObject():
            root = self._quick.rootObject()
            root.setProperty("backgroundFidelity", fidelity)
            root.setProperty("renderJobStatus", status)

    def set_vector_overlay(
        self,
        svg_path: str,
        x: float,
        y: float,
        width: float,
        height: float,
        opacity: float = 1.0,
    ) -> None:
        """Phase B1.5 — push the inline SVG vector overlay to the QML viewer.

        Called when zone_vector_renderer (or its subprocess) produces an
        SVG for the active zone. The overlay sits between the background
        PNG and the cloud/focus markers, scaled to the zone's pixel bbox
        so it visually replaces that PNG region with vector quality. As
        the user mouse-wheel-zooms in QML, the SVG raster sharpens via
        sourceSize, giving infinite-zoom-like behavior up to the chosen
        ``sourceSize`` ceiling (currently 4× displayed size).

        Pass empty ``svg_path`` to clear the overlay.
        """

        if not self._quick_ready or not self._quick or not self._quick.rootObject():
            return
        root = self._quick.rootObject()
        if svg_path and Path(svg_path).exists():
            root.setProperty("vectorSvgPath", str(Path(svg_path).resolve()).replace("\\", "/"))
        else:
            root.setProperty("vectorSvgPath", "")
        root.setProperty("vectorSvgX", float(x))
        root.setProperty("vectorSvgY", float(y))
        root.setProperty("vectorSvgW", float(width))
        root.setProperty("vectorSvgH", float(height))
        root.setProperty("vectorSvgOpacity", max(0.0, min(1.0, float(opacity))))

    def clear_vector_overlay(self) -> None:
        """Convenience wrapper used when the user switches zones; the
        prior zone's SVG is no longer relevant and would just visually
        clutter the new zone's PNG view."""

        self.set_vector_overlay("", 0, 0, 0, 0, opacity=1.0)

    def update_overlay_metadata(self, zone_id: str, overlay: dict) -> None:
        """Replace a single overlay entry in-place and refresh the QML model.

        Workbench uses this for the PDF page-level fallback path: when a zone has no
        CAD bbox, the workbench synthesizes a page-center bbox + ``pin_only`` flag and
        pushes it through this method so the focus pin renders without re-running
        ``load_preview`` (which would clear the background image).
        """

        if not zone_id or not isinstance(overlay, dict):
            return
        replacement = dict(overlay)
        replacement.setdefault("zone_id", zone_id)
        self._overlays_by_zone[zone_id] = replacement
        replaced = False
        for index, item in enumerate(self._last_overlays):
            if isinstance(item, dict) and str(item.get("zone_id") or item.get("id") or "") == zone_id:
                self._last_overlays[index] = replacement
                replaced = True
                break
        if not replaced:
            self._last_overlays.append(replacement)
        if self._quick_ready and self._quick and self._quick.rootObject():
            self._refresh_quick_model(record_perf=False)
        else:
            self._fallback.load_preview(
                self._last_image_path,
                self._last_overlays,
                before=self._last_before,
                fallback_message=self._last_fallback_message,
            )

    def focus_zone(self, zone_id: str, padding_ratio: float = 0.25) -> None:
        self.set_selected_zone(zone_id)
        if self._quick_ready and self._quick and self._quick.rootObject():
            root = self._quick.rootObject()
            root.setProperty("focusPaddingRatio", float(padding_ratio))
            root.setProperty("focusZoneId", "")
            root.setProperty("focusZoneId", zone_id)
            return
        self._fallback.focus_zone(zone_id, padding_ratio=padding_ratio)

    def apply_viewport(self, zoom: float, pan_x: float, pan_y: float) -> None:
        if not self._quick_ready or not self._quick or not self._quick.rootObject():
            return
        self._syncing = True
        try:
            root = self._quick.rootObject()
            root.setProperty("zoom", float(zoom))
            root.setProperty("panX", float(pan_x))
            root.setProperty("panY", float(pan_y))
            self._schedule_refresh_quick_model(record_perf=False)
        finally:
            self._syncing = False

    def sceneRect(self) -> QRectF:  # compatibility with ZonePreviewView call sites
        if self._quick_ready:
            return QRectF(0, 0, 1, 1)
        return self._fallback.sceneRect()

    def fitInView(self, *_args, **_kwargs) -> None:  # compatibility with ZonePreviewView call sites
        if self._quick_ready and self._quick and self._quick.rootObject():
            self._quick.rootObject().setProperty("fitRequest", self._quick.rootObject().property("fitRequest") + 1)
        else:
            self._fallback.fitInView(*_args, **_kwargs)

    def _init_quick_view(self) -> None:
        if not QT_QUICK_AVAILABLE or QQuickWidget is None:
            return
        qml_path = _drawing_compare_asset_path("DrawingGpuViewport.qml")
        if not qml_path.exists():
            return
        try:
            quick = QQuickWidget()
            quick.setResizeMode(QQuickWidget.SizeRootObjectToView)
            quick.setClearColor(QColor("#FFFFFF"))
            quick.statusChanged.connect(self._on_quick_status_changed)
            quick.setSource(QUrl.fromLocalFile(str(qml_path)))
            if quick.status() == QQuickWidget.Error:
                return
            self._quick = quick
            self._layout.addWidget(quick)
            root = quick.rootObject()
            if root is not None and hasattr(root, "viewportChanged"):
                root.viewportChanged.connect(self._on_qml_viewport_changed)
            # Phase I4 — wire QML overlayClicked → Qt Signal so the
            # workbench can drive list selection on overlay click.
            if root is not None and hasattr(root, "overlayClicked"):
                try:
                    root.overlayClicked.connect(self._on_qml_overlay_clicked)
                except Exception:
                    logger.debug(
                        "GpuDrawingViewport: overlayClicked signal not exposed",
                        exc_info=True,
                    )
            self._quick_ready = root is not None
        except Exception:
            logger.warning("Qt Quick drawing viewport unavailable; using widgets fallback", exc_info=True)
            self._quick_ready = False

    def _on_quick_status_changed(self, status) -> None:
        if QQuickWidget is not None and status == QQuickWidget.Error:
            self._quick_ready = False
            self._layout.setCurrentWidget(self._fallback)

    def _on_qml_viewport_changed(self, zoom: float, pan_x: float, pan_y: float) -> None:
        if not self._syncing:
            self._schedule_refresh_quick_model(record_perf=False)
            self.viewportChanged.emit(float(zoom), float(pan_x), float(pan_y))

    def _on_qml_overlay_clicked(self, zone_id: str) -> None:
        """Phase I4 — Re-emit QML overlay click as a Qt signal.

        The workbench connects this to ``_select_zone_in_list_v2`` so the
        zone tree auto-expands and selects the clicked zone. Empty
        zone_id (defensive) is silently ignored.
        """

        zid = str(zone_id or "").strip()
        if zid:
            self.overlayClicked.emit(zid)

    def _schedule_refresh_quick_model(self, *, record_perf: bool) -> None:
        self._pending_record_perf = self._pending_record_perf or bool(record_perf)
        self._refresh_timer.start()

    def _flush_refresh_quick_model(self) -> None:
        record_perf = self._pending_record_perf
        self._pending_record_perf = False
        self._refresh_quick_model(record_perf=record_perf)

    def _refresh_quick_model(self, *, record_perf: bool) -> None:
        root = self._quick.rootObject() if self._quick else None
        if not root:
            self._fallback.load_preview(
                self._last_image_path,
                self._last_overlays,
                before=self._last_before,
                fallback_message=self._last_fallback_message,
            )
            return
        started = perf_counter()
        image = Path(self._last_image_path) if self._last_image_path else None
        real_image = bool(image and image.exists())
        source = image if real_image else None
        viewport = self._viewport_rect(real_image=real_image)
        tile_result = self._visible_tiles(viewport)
        visible_tiles = list(tile_result.get("tiles") or [])
        if viewport:
            self._maybe_emit_missing_tile_request(tile_result, viewport)
        use_tiles = bool(visible_tiles)
        scene_width, scene_height = self._scene_size(real_image=real_image, source=source)
        focus_only_mode = should_use_focus_only_overlay_mode(len(self._last_overlays))
        model = self._overlay_model(
            self._last_overlays,
            before=self._last_before,
            real_image=real_image,
            viewport_rect=viewport if real_image else None,
        )
        cloud_entries, focus_entries = split_overlay_entries(model)
        root.setProperty("sceneWidth", scene_width)
        root.setProperty("sceneHeight", scene_height)
        root.setProperty("visibleTiles", visible_tiles)
        root.setProperty("useTiles", use_tiles)
        root.setProperty("imageSource", QUrl.fromLocalFile(str(source)).toString() if source and source.exists() else "")
        root.setProperty("overlays", cloud_entries)
        root.setProperty("overlaysCloud", cloud_entries)
        root.setProperty("overlaysFocus", focus_entries)
        root.setProperty("viewportSide", "before" if self._last_before else "after")
        root.setProperty("overlayOpacityScale", float(self._overlay_opacity_scale))
        # Phase F P0 — re-apply fidelity state on every model refresh so it
        # survives QML root reloads (e.g. when the user switches pairs).
        root.setProperty("backgroundFidelity", self._background_fidelity)
        root.setProperty("renderJobStatus", self._render_job_status)
        root.setProperty("hasBackground", bool(real_image or use_tiles))
        root.setProperty(
            "emptyNotice",
            "" if real_image or use_tiles else self._empty_preview_notice(self._last_fallback_message),
        )
        if use_tiles:
            status_text = "타일 미리보기"
        elif real_image:
            status_text = "실미리보기"
        else:
            status_text = self._last_fallback_message
        if focus_only_mode:
            if self._selected_zone_id:
                status_text = f"{status_text} | focus-only overlay mode ({len(self._last_overlays)} zones)"
            else:
                status_text = (
                    f"{status_text} | focus-only overlay mode: select a zone "
                    f"({len(self._last_overlays)} zones)"
                )
        root.setProperty("statusText", status_text)
        root.setProperty("selectedZoneId", self._selected_zone_id)
        self._layout.setCurrentWidget(self._quick)
        if record_perf:
            root.setProperty("fitRequest", int(root.property("fitRequest") or 0) + 1)
        if record_perf and self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "viewport_model",
                pair_uuid=self._pair_id,
                side="before" if self._last_before else "after",
                tile_count=len(visible_tiles),
                overlay_model_count=len(model),
                overlay_source_count=len(self._last_overlays),
                overlay_display_mode="focus_only" if focus_only_mode else "cloud",
                cull_ms=round((perf_counter() - started) * 1000.0, 3),
                tile_level=tile_result.get("level", -1),
            )

    def _maybe_emit_missing_tile_request(self, tile_result: dict, viewport_rect: dict[str, float]) -> None:
        if not self._pair_id or not self._viewer_root or not self._tile_manifest:
            return
        if str(tile_result.get("status") or "") != "tile_pending":
            return
        if self._tile_manifest.get("pyramid_complete") is not False:
            return
        window = tile_result.get("tile_window")
        if not isinstance(window, (list, tuple)) or len(window) < 4:
            return
        level = int(tile_result.get("level") or 0)
        zoom = self._current_zoom()
        request_key = f"{self._pair_id}:{level}:{','.join(str(int(v)) for v in window[:4])}:{zoom:.4f}"
        if request_key == self._last_missing_tile_request_key:
            return
        self._last_missing_tile_request_key = request_key
        self.tileWindowMissing.emit(self._pair_id, dict(viewport_rect), float(zoom))

    def _viewport_rect(self, *, real_image: bool) -> Optional[dict[str, float]]:
        if not real_image or not self._quick or not self._quick.rootObject():
            return None
        root = self._quick.rootObject()
        width = max(1, int(self.width() or self._quick.width() or 1))
        height = max(1, int(self.height() or self._quick.height() or 1))
        return viewport_rect_from_transform(
            zoom=float(root.property("zoom") or 1.0),
            pan_x=float(root.property("panX") or 0.0),
            pan_y=float(root.property("panY") or 0.0),
            viewport_width=width,
            viewport_height=height,
        )

    def _visible_tiles(self, viewport_rect: Optional[dict[str, float]]) -> dict:
        if not viewport_rect or not self._tile_manifest or not self._viewer_root:
            return {"tiles": [], "level": -1, "status": "no_tiles"}
        return visible_tile_model(
            pair_manifest=self._tile_manifest,
            side="before" if self._last_before else "after",
            viewer_root=self._viewer_root,
            viewport_rect=viewport_rect,
            zoom=self._current_zoom(),
            prefetch_radius=1,
        )

    def _current_zoom(self) -> float:
        if not self._quick or not self._quick.rootObject():
            return 1.0
        try:
            return float(self._quick.rootObject().property("zoom") or 1.0)
        except (TypeError, ValueError):
            return 1.0

    def _scene_size(self, *, real_image: bool, source: Optional[Path]) -> tuple[float, float]:
        side = "before" if self._last_before else "after"
        side_manifest = (self._tile_manifest.get("sides") or {}).get(side) if isinstance(self._tile_manifest, dict) else None
        if isinstance(side_manifest, dict):
            levels = [level for level in side_manifest.get("levels", []) if isinstance(level, dict)]
            if levels:
                width = 0.0
                height = 0.0
                for level in levels:
                    scale = max(0.0001, float(level.get("scale") or 1.0))
                    width = max(width, float(level.get("width") or 0.0) / scale)
                    height = max(height, float(level.get("height") or 0.0) / scale)
                if width > 0 and height > 0:
                    return width, height
        if real_image and source and source.exists():
            pixmap = QPixmap(str(source))
            if not pixmap.isNull():
                return float(pixmap.width()), float(pixmap.height())
        return 800.0, 500.0

    def _empty_preview_notice(self, fallback_message: str) -> str:
        message = str(fallback_message or "").strip()
        if "시간" in message or "timeout" in message.lower():
            return "원본 도면 렌더링 시간이 초과되었습니다.\n현재 화면은 실제 도면 배경이 아닌 상대 위치 오버레이입니다.\n변경구역 목록과 CSV/DXF 산출물은 계속 사용할 수 있습니다."
        if "실패" in message or "failed" in message.lower():
            return "원본 도면 미리보기를 만들지 못했습니다.\n현재 화면은 실제 도면 배경이 아닌 상대 위치 오버레이입니다.\n원본 DWG/DXF 또는 구름마크 DXF로 상세 위치를 확인하세요."
        return "원본 도면 미리보기 준비 전입니다.\n현재 화면은 실제 도면 배경이 아닌 상대 위치 오버레이입니다."

    def _overlay_model(
        self,
        overlays: list[dict],
        *,
        before: bool,
        real_image: bool,
        viewport_rect: Optional[dict[str, float]],
    ) -> list[dict]:
        selected = self._overlays_by_zone.get(self._selected_zone_id) if self._selected_zone_id else None
        focus_only_mode = should_use_focus_only_overlay_mode(len(overlays or []))
        if focus_only_mode:
            items = [selected] if isinstance(selected, dict) else []
            return self._overlay_entries_for_items(
                items,
                before=before,
                real_image=real_image,
                focus_only_mode=True,
            )

        lod = None
        if real_image and viewport_rect and self._tile_manifest and self._viewer_root:
            lod = visible_overlay_tile_items(
                pair_manifest=self._tile_manifest,
                viewer_root=self._viewer_root,
                viewport_rect=viewport_rect,
                zoom=self._current_zoom(),
                max_visible=GPU_VIEWER_MAX_VISIBLE_OVERLAYS,
                selected_overlay=selected,
                prefetch_radius=1,
            )
            if str(lod.get("status") or "").startswith("missing_"):
                lod = None
        if lod is None:
            lod = visible_or_clustered_overlays(
                overlays,
                max_visible=GPU_VIEWER_MAX_VISIBLE_OVERLAYS,
                zoom=self._current_zoom(),
                viewport_rect=viewport_rect,
            )
        items = [item for item in lod["items"] if isinstance(item, dict)]
        if selected and not any(str(item.get("zone_id") or item.get("id") or "") == self._selected_zone_id for item in items):
            items = items[: max(0, GPU_VIEWER_MAX_VISIBLE_OVERLAYS - 1)] + [selected]
        return self._overlay_entries_for_items(
            items[:GPU_VIEWER_MAX_VISIBLE_OVERLAYS],
            before=before,
            real_image=real_image,
            focus_only_mode=False,
        )

    def _overlay_entries_for_items(
        self,
        items: list[dict],
        *,
        before: bool,
        real_image: bool,
        focus_only_mode: bool,
    ) -> list[dict]:
        result: list[dict] = []
        for overlay in items:
            rect = self._overlay_rect(overlay, before=before, real_image=real_image)
            if not rect:
                continue
            zone_id = str(overlay.get("zone_id") or overlay.get("id") or "")
            is_selected = bool(self._selected_zone_id) and zone_id == self._selected_zone_id
            entries = build_overlay_entries(
                zone_id=zone_id,
                rect=rect,
                change_type=str(overlay.get("change_type") or "mixed"),
                label=self._overlay_label(overlay),
                raw_change_count=int(float(overlay.get("raw_change_count") or 0)),
                cluster_count=int(float(overlay.get("cluster_count") or 0)),
                selected=is_selected,
                before=before,
                pin_only=bool(overlay.get("pin_only")),
            )
            if focus_only_mode:
                entries = [entry for entry in entries if entry.get("role") == "focus"]
            result.extend(entries)
        return result

    def _overlay_rect(self, overlay: dict, *, before: bool, real_image: bool) -> Optional[tuple[float, float, float, float]]:
        if real_image:
            bbox = overlay.get("before_bbox_px") if before else overlay.get("after_bbox_px")
            rect = self._pixel_rect(bbox or overlay.get("bbox"))
            if rect:
                # Phase G2.7-DIAG: scale bbox to match the rendered
                # image DPI. The bbox is in `image_pixels` at the
                # comparison `pdf_dpi`; the on-screen image is rendered
                # at the viewer's `image_dpi`. Without this scale, e.g.
                # bbox at DPI 200 displayed on DPI 400 image lands at
                # half-position. Apply scale only for PDF overlays
                # (`bbox_coordinate_space == "image_pixels"`).
                space = str(overlay.get("bbox_coordinate_space") or "")
                if space == "image_pixels":
                    # Defensive parse — pdf_dpi could be a string from
                    # legacy manifests or corrupt JSON. Fall back to 0
                    # (which skips scaling) on any conversion error.
                    try:
                        bbox_dpi = float(overlay.get("pdf_dpi") or 0)
                    except (TypeError, ValueError):
                        bbox_dpi = 0.0
                    image_dpi = self._image_dpi_before if before else self._image_dpi_after
                    if bbox_dpi > 0 and image_dpi > 0 and bbox_dpi != image_dpi:
                        scale = image_dpi / bbox_dpi
                        x, y, w, h = rect
                        rect = (x * scale, y * scale, w * scale, h * scale)
                return rect
        bbox = overlay.get("normalized_bbox") or {}
        if isinstance(bbox, dict):
            try:
                return (
                    float(bbox.get("x", 0.0)) * 800.0,
                    float(bbox.get("y", 0.0)) * 500.0,
                    max(2.0, float(bbox.get("width", 0.0)) * 800.0),
                    max(2.0, float(bbox.get("height", 0.0)) * 500.0),
                )
            except (TypeError, ValueError):
                return None
        return None

    def _pixel_rect(self, bbox) -> Optional[tuple[float, float, float, float]]:
        if isinstance(bbox, dict):
            try:
                return (
                    float(bbox.get("x", 0.0)),
                    float(bbox.get("y", 0.0)),
                    max(1.0, float(bbox.get("width", 0.0))),
                    max(1.0, float(bbox.get("height", 0.0))),
                )
            except (TypeError, ValueError):
                return None
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            try:
                left = float(bbox[0])
                top = float(bbox[1])
                right = float(bbox[2])
                bottom = float(bbox[3])
                return (left, top, max(1.0, right - left), max(1.0, bottom - top))
            except (TypeError, ValueError):
                return None
        return None

    def _overlay_label(self, overlay: dict) -> str:
        zone_id = str(overlay.get("zone_id") or overlay.get("id") or "")
        cluster_count = int(float(overlay.get("cluster_count") or 0))
        if zone_id.startswith("cluster-") and cluster_count:
            return f"{cluster_count} zones"
        return zone_id


class DrawingCompareWorkbench(QMainWindow):
    """Dedicated Windows desktop workbench for A/B drawing comparison."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Drawing Compare Workbench")
        self.resize(1200, 760)
        self.setStyleSheet(get_stylesheet())

        self._scan_worker: Optional[ScanWorker] = None
        self._compare_worker: Optional[CompareWorker] = None
        self._descriptors_a: List[DrawingFileDescriptor] = []
        self._descriptors_b: List[DrawingFileDescriptor] = []
        self._candidates: List[MatchCandidate] = []
        self._summary: Optional[BatchCompareSummary] = None
        self._b_by_path = {}
        self._dxf_cache_dir = _workbench_data_dir() / "dxf_cache"
        self._compare_state_dir = _workbench_data_dir() / "compare_state"
        self._artifact_dir = _workbench_data_dir() / "review_artifacts"
        self._preview_dir = _workbench_data_dir() / "preview"
        self._review_project_path = _workbench_data_dir() / "review_project.json"
        self._review_state_path = _workbench_data_dir() / "review_state.json"
        self._review_records: dict[str, ReviewStateRecord] = load_review_state(self._review_state_path)
        self._review_zones_by_pair: dict[str, list] = {}
        self._preview_by_pair: dict[str, PreviewArtifact] = {}
        self._active_pair_id = ""
        self._active_zone_key = ""
        self._loading_review_selection = False

        self._init_ui()

    def _init_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel("Drawing Compare Workbench")
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(header)

        self.tabs = QTabWidget()
        match_tab = QWidget()
        match_layout = QVBoxLayout(match_tab)
        match_layout.setContentsMargins(0, 0, 0, 0)
        match_layout.setSpacing(10)

        input_group = QGroupBox("A/B Inputs")
        input_layout = QVBoxLayout(input_group)
        input_layout.addLayout(self._create_input_row("A", "source_a"))
        input_layout.addLayout(self._create_input_row("B", "source_b"))

        options_layout = QHBoxLayout()
        self.chk_recursive = QCheckBox("Include subfolders")
        self.chk_ocr = QCheckBox("OCR fallback for scanned PDFs")
        self.chk_cache = QCheckBox("Use descriptor cache")
        self.chk_cache.setChecked(True)
        self.chk_expand_blocks = QCheckBox("Expand CAD blocks")
        self.chk_expand_blocks.setChecked(False)
        options_layout.addWidget(self.chk_recursive)
        options_layout.addWidget(self.chk_ocr)
        options_layout.addWidget(self.chk_cache)
        options_layout.addWidget(self.chk_expand_blocks)
        options_layout.addStretch()
        input_layout.addLayout(options_layout)

        action_layout = QHBoxLayout()
        self.btn_scan = QPushButton("Scan and Match")
        self.btn_scan.clicked.connect(self._scan_and_match)
        self.btn_confirm = QPushButton("Confirm Selected")
        self.btn_confirm.clicked.connect(self._confirm_selected)
        self.btn_reject = QPushButton("Reject Selected")
        self.btn_reject.clicked.connect(self._reject_selected)
        self.btn_export_review = QPushButton("Export Review CSV")
        self.btn_export_review.clicked.connect(self._export_review_csv)
        self.btn_import_review = QPushButton("Import Review CSV")
        self.btn_import_review.clicked.connect(self._import_review_csv)
        self.btn_open_gate = QPushButton("Open Quality Gate")
        self.btn_open_gate.clicked.connect(self._open_quality_gate)
        self.btn_run = QPushButton("Run Confirmed Comparisons")
        self.btn_run.clicked.connect(self._run_comparisons)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._cancel_current_work)
        self.btn_cancel.setEnabled(False)
        action_layout.addWidget(self.btn_scan)
        action_layout.addWidget(self.btn_confirm)
        action_layout.addWidget(self.btn_reject)
        action_layout.addWidget(self.btn_export_review)
        action_layout.addWidget(self.btn_import_review)
        action_layout.addWidget(self.btn_open_gate)
        action_layout.addWidget(self.btn_run)
        action_layout.addWidget(self.btn_cancel)
        action_layout.addStretch()
        input_layout.addLayout(action_layout)
        match_layout.addWidget(input_group)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["A", "B", "Score", "Status", "Reasons", "Changes/Error"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        match_layout.addWidget(self.table, stretch=1)

        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.lbl_status = QLabel(self._backend_status_message())
        self.btn_export_html = QPushButton("Export HTML")
        self.btn_export_html.clicked.connect(self._export_html)
        self.btn_export_excel = QPushButton("Export Excel")
        self.btn_export_excel.clicked.connect(self._export_excel)
        self.btn_export_json = QPushButton("Export JSON")
        self.btn_export_json.clicked.connect(self._export_json)
        self.btn_export_cloud = QPushButton("Export Cloud Marks")
        self.btn_export_cloud.clicked.connect(self._export_cloud_marks)
        for button in (
            self.btn_export_html,
            self.btn_export_excel,
            self.btn_export_json,
            self.btn_export_cloud,
        ):
            button.setEnabled(False)
        bottom.addWidget(self.lbl_status, stretch=1)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.btn_export_html)
        bottom.addWidget(self.btn_export_excel)
        bottom.addWidget(self.btn_export_json)
        bottom.addWidget(self.btn_export_cloud)
        match_layout.addLayout(bottom)

        self.tabs.addTab(match_tab, "Match & Compare")
        self.tabs.addTab(self._create_review_tab(), "Change Review")
        layout.addWidget(self.tabs, stretch=1)

        self.setCentralWidget(root)

    def _create_review_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.cmb_drawing_filter = QComboBox()
        self.cmb_drawing_filter.addItems(["all", "changed", "warning", "failed", "review_required"])
        self.cmb_zone_filter = QComboBox()
        self.cmb_zone_filter.addItems(["all", "added", "deleted", "modified", "moved", "mixed"])
        self.cmb_review_filter = QComboBox()
        self.cmb_review_filter.addItems(["all", "needs_review", "confirmed", "hold", "false_positive"])
        self.cmb_zone_filter.currentTextChanged.connect(lambda _value: self._refresh_active_review_pair())
        self.cmb_review_filter.currentTextChanged.connect(lambda _value: self._refresh_active_review_pair())
        self.btn_build_review = QPushButton("Build Review Package")
        self.btn_build_review.clicked.connect(self._export_review_package)
        self.btn_build_review.setEnabled(False)
        self.btn_save_review_state = QPushButton("Save Review State")
        self.btn_save_review_state.clicked.connect(self._save_review_state)
        self.btn_open_executive_review = QPushButton("Open Executive Review")
        self.btn_open_executive_review.clicked.connect(self._open_executive_review)
        self.btn_open_executive_review.setEnabled(False)
        toolbar.addWidget(QLabel("Drawing"))
        toolbar.addWidget(self.cmb_drawing_filter)
        toolbar.addWidget(QLabel("Zone"))
        toolbar.addWidget(self.cmb_zone_filter)
        toolbar.addWidget(QLabel("Status"))
        toolbar.addWidget(self.cmb_review_filter)
        toolbar.addWidget(self.btn_build_review)
        toolbar.addWidget(self.btn_save_review_state)
        toolbar.addWidget(self.btn_open_executive_review)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        self.review_drawing_list = QListWidget()
        self.review_drawing_list.currentItemChanged.connect(self._on_review_drawing_selected)
        splitter.addWidget(self.review_drawing_list)

        preview_splitter = QSplitter(Qt.Vertical)
        self.preview_before = ZonePreviewView()
        self.preview_after = ZonePreviewView()
        preview_splitter.addWidget(self.preview_before)
        preview_splitter.addWidget(self.preview_after)
        splitter.addWidget(preview_splitter)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.review_zone_list = QListWidget()
        self.review_zone_list.currentItemChanged.connect(self._on_review_zone_selected)
        self.lbl_zone_detail = QLabel("Select a change zone.")
        self.lbl_zone_detail.setWordWrap(True)
        self.cmb_zone_status = QComboBox()
        self.cmb_zone_status.addItems(["needs_review", "confirmed", "hold", "false_positive"])
        self.cmb_zone_status.currentTextChanged.connect(self._on_zone_status_changed)
        self.txt_zone_note = QTextEdit()
        self.txt_zone_note.setPlaceholderText("Review note")
        self.txt_zone_note.textChanged.connect(self._on_zone_note_changed)
        right_layout.addWidget(QLabel("Zones"))
        right_layout.addWidget(self.review_zone_list, stretch=1)
        right_layout.addWidget(QLabel("Status"))
        right_layout.addWidget(self.cmb_zone_status)
        right_layout.addWidget(QLabel("Note"))
        right_layout.addWidget(self.txt_zone_note)
        right_layout.addWidget(self.lbl_zone_detail)
        splitter.addWidget(right)
        splitter.setSizes([220, 720, 300])
        layout.addWidget(splitter, stretch=1)
        return tab

    def _backend_status_message(self) -> str:
        messages = []
        try:
            from src.services.comparison.dwg_differ import DwgDiffer

            status = DwgDiffer.get_status()
            if not status.get("dwg_support"):
                messages.append("DWG importer unavailable; DWG inputs may fail before comparison.")
            else:
                supported = ", ".join(status.get("dwg_supported_versions") or [])
                planned = ", ".join(status.get("dwg_planned_versions") or [])
                if supported and planned:
                    messages.append(
                        f"DWG native import limited to {supported}; {planned} requires an approved adapter."
                    )
            if status.get("legacy_oda_required"):
                messages.append("Legacy ODA fallback requires explicit internal configuration.")
        except Exception as exc:
            messages.append(f"DWG backend status unavailable: {exc}")

        try:
            from src.services.comparison.spatial_index import RTREE_AVAILABLE

            if not RTREE_AVAILABLE:
                messages.append("rtree not installed; large CAD near-match uses grid fallback.")
        except Exception:
            pass

        return " ".join(messages) or "Select A and B file/folder inputs."

    def _create_input_row(self, label: str, attr: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(f"{label}:"))
        edit = QLineEdit()
        edit.setReadOnly(True)
        setattr(self, f"edit_{attr}", edit)
        row.addWidget(edit, stretch=1)

        btn_file = QPushButton("File")
        btn_file.clicked.connect(lambda: self._browse_file(attr))
        btn_folder = QPushButton("Folder")
        btn_folder.clicked.connect(lambda: self._browse_folder(attr))
        row.addWidget(btn_file)
        row.addWidget(btn_folder)
        return row

    def _browse_file(self, attr: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select drawing file",
            "",
            "Drawings (*.dwg *.dxf *.pdf);;DWG (*.dwg);;DXF (*.dxf);;PDF (*.pdf)",
        )
        if path:
            getattr(self, f"edit_{attr}").setText(path)

    def _browse_folder(self, attr: str) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select drawing folder")
        if path:
            getattr(self, f"edit_{attr}").setText(path)

    def _scan_and_match(self) -> None:
        source_a = self.edit_source_a.text().strip()
        source_b = self.edit_source_b.text().strip()
        if not source_a or not source_b:
            QMessageBox.warning(self, "Missing input", "Select both A and B inputs.")
            return
        if not Path(source_a).exists() or not Path(source_b).exists():
            QMessageBox.warning(self, "Invalid input", "One or both inputs do not exist.")
            return

        self._set_busy(True)
        self.lbl_status.setText("Scanning drawings...")
        self.progress.setValue(0)
        self._scan_worker = ScanWorker(
            source_a,
            source_b,
            recursive=self.chk_recursive.isChecked(),
            use_ocr=self.chk_ocr.isChecked(),
            enable_cache=self.chk_cache.isChecked(),
        )
        self._scan_worker.progress.connect(self.lbl_status.setText)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.cancelled.connect(self._on_worker_cancelled)
        self._scan_worker.error.connect(self._on_worker_error)
        self._scan_worker.start()

    def _on_scan_finished(self, candidates: list, descriptors_a: list, descriptors_b: list) -> None:
        self._set_busy(False)
        self._scan_worker = None
        self._candidates = list(candidates)
        self._descriptors_a = list(descriptors_a)
        self._descriptors_b = list(descriptors_b)
        self._b_by_path = {descriptor.path: descriptor for descriptor in self._descriptors_b}
        self._summary = None
        self._populate_table()
        auto_count = sum(1 for c in self._candidates if c.status == MatchStatus.AUTO_CONFIRMED)
        review_count = sum(1 for c in self._candidates if c.status == MatchStatus.REVIEW_REQUIRED)
        self.lbl_status.setText(self._match_summary_text())

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._candidates))
        for row, candidate in enumerate(self._candidates):
            self._set_item(row, 0, candidate.source_a.name if candidate.source_a else "")
            self._set_b_selector(row, candidate)
            self._set_item(row, 2, f"{candidate.score:.2f}" if candidate.score else "-")
            self._set_item(row, 3, candidate.status.value)
            self._set_item(row, 4, "; ".join(candidate.reasons[:4]))
            self._set_item(row, 5, "")
        self.table.resizeColumnsToContents()

    def _set_item(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, col, item)

    def _set_b_selector(self, row: int, candidate: MatchCandidate) -> None:
        if candidate.source_a is None:
            self._set_item(row, 1, candidate.source_b.name if candidate.source_b else "")
            return

        combo = QComboBox()
        combo.addItem("", "")
        for descriptor in self._descriptors_b:
            if descriptor.kind == candidate.source_a.kind:
                combo.addItem(descriptor.name, descriptor.path)
        if candidate.source_b:
            index = combo.findData(candidate.source_b.path)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.currentIndexChanged.connect(lambda _idx, r=row: self._on_b_changed(r))
        self.table.setCellWidget(row, 1, combo)

    def _on_b_changed(self, row: int) -> None:
        candidate = self._candidates[row]
        if candidate.source_a is None:
            return
        combo = self.table.cellWidget(row, 1)
        selected_path = combo.currentData() if isinstance(combo, QComboBox) else ""
        if not selected_path:
            candidate.source_b = None
            candidate.score = 0.0
            candidate.status = MatchStatus.UNMATCHED_A
            candidate.reasons = ["No B drawing selected"]
            candidate.component_scores = {}
        else:
            candidate.source_b = self._b_by_path[selected_path]
            scored = score_match(candidate.source_a, candidate.source_b)
            candidate.score = scored.score
            candidate.reasons = scored.reasons
            candidate.component_scores = scored.component_scores
            candidate.status = MatchStatus.MANUAL_CONFIRMED
        self._set_item(row, 2, f"{candidate.score:.2f}" if candidate.score else "-")
        self._set_item(row, 3, candidate.status.value)
        self._set_item(row, 4, "; ".join(candidate.reasons[:4]))

    def _confirm_selected(self) -> None:
        for row in self._selected_rows():
            candidate = self._candidates[row]
            if candidate.source_a and candidate.source_b:
                candidate.status = MatchStatus.MANUAL_CONFIRMED
                self._set_item(row, 3, candidate.status.value)
        self.lbl_status.setText(self._match_summary_text())

    def _reject_selected(self) -> None:
        for row in self._selected_rows():
            candidate = self._candidates[row]
            candidate.status = MatchStatus.REJECTED
            self._set_item(row, 3, candidate.status.value)
        self.lbl_status.setText(self._match_summary_text())

    def _run_comparisons(self) -> None:
        confirmed = [c for c in self._candidates if c.is_confirmed]
        if not confirmed:
            QMessageBox.warning(self, "No confirmed pairs", "Confirm at least one A/B pair first.")
            return
        violations = confirmed_pair_uniqueness_violations(confirmed)
        duplicate_a = violations.get("duplicate_a", [])
        duplicate_b = violations.get("duplicate_b", [])
        if duplicate_a or duplicate_b:
            QMessageBox.warning(
                self,
                "Duplicate assignments",
                "Confirmed pairs must be one-to-one.\n\n"
                + ("\nA is assigned more than once:\n" + "\n".join(duplicate_a[:5]) if duplicate_a else "")
                + ("\nB is assigned more than once:\n" + "\n".join(duplicate_b[:5]) if duplicate_b else ""),
            )
            return

        self._set_busy(True)
        self.progress.setValue(0)
        self.lbl_status.setText("Running comparisons... " + self._match_summary_text())
        self._dxf_cache_dir.mkdir(parents=True, exist_ok=True)
        self._compare_state_dir.mkdir(parents=True, exist_ok=True)
        self._compare_worker = CompareWorker(
            confirmed,
            expand_blocks=self.chk_expand_blocks.isChecked(),
            dxf_cache_dir=self._dxf_cache_dir,
            compare_state_dir=self._compare_state_dir,
        )
        self._compare_worker.progress.connect(self._on_compare_progress)
        self._compare_worker.finished.connect(self._on_compare_finished)
        self._compare_worker.error.connect(self._on_worker_error)
        self._compare_worker.start()

    def _on_compare_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.lbl_status.setText(message)

    def _on_compare_finished(self, summary: BatchCompareSummary) -> None:
        self._set_busy(False)
        self._compare_worker = None
        self._summary = summary
        for button in (
            self.btn_export_html,
            self.btn_export_excel,
            self.btn_export_json,
            self.btn_export_cloud,
        ):
            button.setEnabled(True)
        self.btn_build_review.setEnabled(True)

        result_by_pair = {
            (
                item.candidate.source_a.path if item.candidate.source_a else "",
                item.candidate.source_b.path if item.candidate.source_b else "",
            ): item
            for item in summary.items
        }
        for row, candidate in enumerate(self._candidates):
            key = (
                candidate.source_a.path if candidate.source_a else "",
                candidate.source_b.path if candidate.source_b else "",
            )
            item = result_by_pair.get(key)
            if not item:
                continue
            text = self._result_status_text(item)
            self._set_item(row, 5, text)

        index_backends = self._summary_metadata_values("index_backend")
        large_modes = self._summary_metadata_values("large_drawing_mode")
        backend_text = f" index={','.join(index_backends)};" if index_backends else ""
        large_text = f" large={','.join(large_modes)};" if large_modes else ""
        self.lbl_status.setText(
            f"Completed {summary.completed_pairs}/{summary.total_pairs}; "
            f"failed {summary.failed_pairs}; cancelled {summary.cancelled_pairs}; "
            f"changes {self._summary_change_count(summary)};"
            f"{backend_text}{large_text} cache={self._dxf_cache_dir}"
        )
        self._populate_review_from_summary()

    def _result_status_text(self, item) -> str:
        if not item.result:
            return item.error or item.status
        total = self._result_change_count(item.result)
        suffix = ""
        if item.result.metadata.get("truncated_changes"):
            suffix = " (details truncated)"
        return f"{total} changes{suffix}"

    def _result_change_count(self, result) -> int:
        counts = result.metadata.get("change_counts") if result.metadata else None
        if counts:
            return sum(int(counts.get(name, 0)) for name in ("added", "deleted", "modified"))
        return result.total_changes

    def _summary_change_count(self, summary: BatchCompareSummary) -> int:
        return sum(self._result_change_count(item.result) for item in summary.items if item.result)

    def _summary_metadata_values(self, key: str) -> List[str]:
        values = {
            str(item.result.metadata.get(key))
            for item in self._summary.items
            if item.result and item.result.metadata and item.result.metadata.get(key)
        }
        return sorted(values)

    def _export_html(self) -> None:
        self._export_summary("html")

    def _export_excel(self) -> None:
        self._export_summary("xlsx")

    def _export_json(self) -> None:
        self._export_summary("json")

    def _export_cloud_marks(self) -> None:
        if not self._summary:
            return
        path = QFileDialog.getExistingDirectory(
            self,
            "Select cloud-mark artifact folder",
            "drawing_compare_change_artifacts",
        )
        if not path:
            return
        try:
            self._artifact_dir = Path(path)
            package = BatchCompareJob.export_change_artifacts(
                self._summary,
                self._artifact_dir,
                dxf_cache_dir=self._dxf_cache_dir,
                compare_state_dir=self._compare_state_dir,
                export_cloud_marks=True,
            )
            executive = export_executive_review_from_artifacts(self._artifact_dir)
            self.btn_open_executive_review.setEnabled(True)
            self.lbl_status.setText(
                f"Exported {package.zone_count} zones / {package.cloud_region_count} cloud regions; "
                f"omitted {package.cloud_omitted_zone_count}; cache reused: {self._dxf_cache_dir}"
            )
            QMessageBox.information(
                self,
                "Cloud marks exported",
                "Saved review package:\n"
                f"{package.output_paths.get('review_index_html', path)}\n\n"
                f"Executive review:\n{executive.output_paths.get('executive_review_html')}\n\n"
                f"Cloud regions: {package.cloud_region_count}\n"
                f"Omitted zones: {package.cloud_omitted_zone_count}\n\n"
                f"DXF cache reused:\n{self._dxf_cache_dir}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Cloud mark export failed", str(exc))

    def _export_review_package(self) -> None:
        if not self._summary:
            QMessageBox.warning(self, "No comparison", "Run confirmed comparisons before building a review package.")
            return
        path = QFileDialog.getExistingDirectory(
            self,
            "Select review package folder",
            str(self._artifact_dir),
        )
        if not path:
            return
        self._artifact_dir = Path(path)
        try:
            self._artifact_dir.mkdir(parents=True, exist_ok=True)
            self._preview_dir = self._artifact_dir / "preview"
            self._save_review_state()
            package = BatchCompareJob.export_change_artifacts(
                self._summary,
                self._artifact_dir,
                dxf_cache_dir=self._dxf_cache_dir,
                compare_state_dir=self._compare_state_dir,
                cloud_options=CloudMarkOptions(export_mode="selected"),
                export_cloud_marks=True,
            )
            preview = export_preview_artifacts(
                self._summary,
                self._preview_dir,
                dxf_cache_dir=self._dxf_cache_dir,
                review_state_path=self._review_state_path,
            )
            write_review_project(
                self._review_project_path,
                source_a=self.edit_source_a.text().strip(),
                source_b=self.edit_source_b.text().strip(),
                dxf_cache_dir=self._dxf_cache_dir,
                compare_state_dir=self._compare_state_dir,
                artifact_dir=self._artifact_dir,
                review_state_path=self._review_state_path,
                preview_manifest_path=preview.manifest_path,
                options={"cloud_export_mode": "selected", "export_preview": True},
            )
            update_artifact_manifest(
                package.output_paths.get("artifact_manifest_json"),
                preview_manifest_path=preview.manifest_path,
                review_state_path=self._review_state_path,
                review_project_path=self._review_project_path,
            )
            executive = export_executive_review_from_artifacts(self._artifact_dir)
            self._preview_by_pair = {artifact.pair_id: artifact for artifact in preview.artifacts}
            self._populate_review_from_summary()
            self.btn_open_executive_review.setEnabled(True)
            self.lbl_status.setText(
                f"Review package ready: zones {package.zone_count}, previews {preview.preview_count}, "
                f"cloud regions {package.cloud_region_count}, omitted {package.cloud_omitted_zone_count}"
            )
            QMessageBox.information(
                self,
                "Review package exported",
                "Saved review package:\n"
                f"{package.output_paths.get('review_index_html', self._artifact_dir)}\n\n"
                f"Executive review:\n{executive.output_paths.get('executive_review_html')}\n\n"
                f"Preview manifest:\n{preview.manifest_path}\n\n"
                f"Review state:\n{self._review_state_path}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Review package export failed", str(exc))

    def _open_executive_review(self) -> None:
        try:
            manifest = self._artifact_dir / "artifact_manifest.json"
            zones = self._artifact_dir / "change_zones.csv"
            if not manifest.exists() or not zones.exists():
                QMessageBox.warning(
                    self,
                    "Executive review unavailable",
                    "Build a review package first. The executive dashboard needs artifact_manifest.json and change_zones.csv.",
                )
                return
            package = export_executive_review_from_artifacts(self._artifact_dir)
            html_path = package.output_paths.get("executive_review_html")
            if not html_path:
                raise RuntimeError("executive_review.html was not generated")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(html_path).resolve())))
            self.lbl_status.setText(f"Opened executive review: {html_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Executive review failed", str(exc))

    def _populate_review_from_summary(self) -> None:
        if not self._summary:
            return
        try:
            self._review_records = load_review_state(self._review_state_path)
            self._review_zones_by_pair = collect_review_zones(
                self._summary,
                review_records=self._review_records,
            )
        except Exception as exc:
            self.lbl_status.setText(f"Change Review unavailable: {exc}")
            return
        self.review_drawing_list.clear()
        for pair_id, zones in sorted(self._review_zones_by_pair.items()):
            raw_count = sum(zone.raw_change_count for zone in zones)
            item = QListWidgetItem(f"{pair_id}  zones={len(zones)}  raw={raw_count}")
            item.setData(Qt.UserRole, pair_id)
            self.review_drawing_list.addItem(item)
        if self.review_drawing_list.count():
            self.review_drawing_list.setCurrentRow(0)

    def _on_review_drawing_selected(self, current, _previous=None) -> None:
        if not current:
            return
        self._active_pair_id = str(current.data(Qt.UserRole) or "")
        self.review_zone_list.clear()
        zones = self._filtered_review_zones(self._review_zones_by_pair.get(self._active_pair_id, []))
        for zone in zones:
            item = QListWidgetItem(
                f"{zone.zone_id}  {zone.change_type}  {zone.severity}  raw={zone.raw_change_count}"
            )
            item.setData(Qt.UserRole, review_state_key(zone.pair_id, zone.zone_id))
            self.review_zone_list.addItem(item)
        preview = self._preview_by_pair.get(self._active_pair_id)
        overlays = [overlay.to_dict() for overlay in preview.zone_overlays] if preview else []
        self.preview_before.load_preview(preview.before_image if preview else "", overlays, before=True)
        self.preview_after.load_preview(preview.after_image if preview else "", overlays, before=False)
        if self.review_zone_list.count():
            self.review_zone_list.setCurrentRow(0)

    def _on_review_zone_selected(self, current, _previous=None) -> None:
        if not current:
            return
        self._loading_review_selection = True
        try:
            self._active_zone_key = str(current.data(Qt.UserRole) or "")
            zone = self._zone_for_key(self._active_zone_key)
            if not zone:
                return
            record = self._review_records.get(self._active_zone_key)
            status = normalize_review_status(record.status if record else zone.status)
            note = record.note if record else str(zone.metadata.get("review_note", ""))
            self.cmb_zone_status.setCurrentText(status)
            self.txt_zone_note.setPlainText(note)
            self.lbl_zone_detail.setText(
                f"{zone.pair_id} / {zone.zone_id}\n"
                f"type={zone.change_type}, severity={zone.severity}, raw={zone.raw_change_count}\n"
                f"added={zone.added_count}, deleted={zone.deleted_count}, modified={zone.modified_count}\n"
                f"layers={', '.join(zone.layers) or '-'}"
            )
            self.preview_before.focus_zone(zone.zone_id)
            self.preview_after.focus_zone(zone.zone_id)
        finally:
            self._loading_review_selection = False

    def _on_zone_status_changed(self, status: str) -> None:
        if self._loading_review_selection or not self._active_zone_key:
            return
        self._upsert_review_record(status=status)

    def _on_zone_note_changed(self) -> None:
        if self._loading_review_selection or not self._active_zone_key:
            return
        self._upsert_review_record(note=self.txt_zone_note.toPlainText())

    def _upsert_review_record(self, *, status: Optional[str] = None, note: Optional[str] = None) -> None:
        zone = self._zone_for_key(self._active_zone_key)
        if not zone:
            return
        record = self._review_records.get(self._active_zone_key) or ReviewStateRecord(
            pair_id=zone.pair_id,
            zone_id=zone.zone_id,
        )
        if status is not None:
            record.status = normalize_review_status(status)
            zone.status = record.status
        if note is not None:
            record.note = note
            zone.metadata["review_note"] = note
        from datetime import datetime

        record.updated_at = datetime.now().isoformat()
        self._review_records[self._active_zone_key] = record
        save_review_state(self._review_state_path, self._review_records)

    def _save_review_state(self) -> None:
        save_review_state(self._review_state_path, self._review_records)
        self.lbl_status.setText(f"Review state saved: {self._review_state_path}")

    def _zone_for_key(self, key: str):
        for zones in self._review_zones_by_pair.values():
            for zone in zones:
                if review_state_key(zone.pair_id, zone.zone_id) == key:
                    return zone
        return None

    def _filtered_review_zones(self, zones: list) -> list:
        zone_filter = self.cmb_zone_filter.currentText() if hasattr(self, "cmb_zone_filter") else "all"
        status_filter = self.cmb_review_filter.currentText() if hasattr(self, "cmb_review_filter") else "all"
        filtered = []
        for zone in zones:
            if zone_filter != "all" and zone.change_type != zone_filter:
                continue
            record = self._review_records.get(review_state_key(zone.pair_id, zone.zone_id))
            status = normalize_review_status(record.status if record else zone.status)
            if status_filter != "all" and status != status_filter:
                continue
            filtered.append(zone)
        return filtered

    def _refresh_active_review_pair(self) -> None:
        current = self.review_drawing_list.currentItem() if hasattr(self, "review_drawing_list") else None
        if current:
            self._on_review_drawing_selected(current)

    def _export_review_csv(self) -> None:
        if not self._candidates:
            QMessageBox.warning(self, "No matches", "Scan A/B drawings before exporting review CSV.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save review CSV",
            "drawing_compare_review.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            write_manual_match_csv(
                self._candidates,
                path,
                root_a=self._input_root("source_a"),
                root_b=self._input_root("source_b"),
            )
            QMessageBox.information(self, "Export complete", f"Saved review CSV:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def _import_review_csv(self) -> None:
        if not self._candidates:
            QMessageBox.warning(self, "No matches", "Scan A/B drawings before importing review CSV.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open review CSV",
            "",
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            rows = load_manual_match_csv(path)
            result = apply_manual_matches(
                rows,
                self._candidates,
                self._descriptors_a,
                self._descriptors_b,
                root_a=self._input_root("source_a"),
                root_b=self._input_root("source_b"),
            )
            self._populate_table()
            message = f"Imported {result['applied']}/{result['rows']} review rows."
            if result["errors"]:
                message += f" {len(result['errors'])} row(s) had errors."
            self.lbl_status.setText(self._match_summary_text())
            QMessageBox.information(self, "Import complete", message)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))

    def _open_quality_gate(self) -> None:
        if not self._candidates:
            QMessageBox.warning(self, "No matches", "Scan A/B drawings before opening a quality gate report.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open quality gate JSON",
            "",
            "JSON (*.json)",
        )
        if not path:
            return
        try:
            report = self._load_quality_gate_report(Path(path))
            visible_statuses = self._quality_gate_visible_statuses(report)
            self._apply_status_filter(visible_statuses)
            status = report.get("status", "unknown")
            issues = report.get("issues", [])
            QMessageBox.information(
                self,
                "Quality gate loaded",
                f"Status: {status}\nVisible rows filtered from {len(issues)} issue(s).",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Quality gate import failed", str(exc))

    def _export_summary(self, extension: str) -> None:
        if not self._summary:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save batch report",
            f"drawing_compare_report.{extension}",
            f"*.{extension}",
        )
        if not path:
            return
        try:
            if extension == "html":
                BatchCompareJob.export_html(self._summary, path)
            elif extension == "xlsx":
                BatchCompareJob.export_excel(self._summary, path)
            else:
                BatchCompareJob.export_json(self._summary, path)
            QMessageBox.information(self, "Export complete", f"Saved report:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def _selected_rows(self) -> List[int]:
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def _duplicate_b_paths(self, candidates: List[MatchCandidate]) -> List[str]:
        seen = set()
        duplicates = []
        for candidate in candidates:
            if not candidate.source_b:
                continue
            path = candidate.source_b.path
            if path in seen:
                duplicates.append(candidate.source_b.name)
            seen.add(path)
        return duplicates

    def _load_quality_gate_report(self, path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if "quality_gate" in payload and isinstance(payload["quality_gate"], dict):
            return payload["quality_gate"]
        if "status" in payload and "issues" in payload:
            return payload
        raise ValueError("Not a quality gate JSON report")

    def _quality_gate_visible_statuses(self, report: dict) -> Optional[set[MatchStatus]]:
        return quality_gate_visible_statuses(report)

    def _apply_status_filter(self, statuses: Optional[set[MatchStatus]]) -> None:
        for row, candidate in enumerate(self._candidates):
            hidden = statuses is not None and candidate.status not in statuses
            self.table.setRowHidden(row, hidden)
        if statuses is None:
            self.lbl_status.setText(self._match_summary_text())
        else:
            self.lbl_status.setText(
                self._match_summary_text()
                + f" Showing {len(statuses)} failed-status group(s)."
            )

    def _input_root(self, attr: str) -> Optional[Path]:
        raw = getattr(self, f"edit_{attr}").text().strip()
        if not raw:
            return None
        path = Path(raw)
        if path.is_dir():
            return path.resolve()
        if path.is_file():
            return path.resolve().parent
        return None

    def _match_summary_text(self) -> str:
        auto_count = sum(1 for c in self._candidates if c.status == MatchStatus.AUTO_CONFIRMED)
        manual_count = sum(1 for c in self._candidates if c.status == MatchStatus.MANUAL_CONFIRMED)
        review_count = sum(1 for c in self._candidates if c.status == MatchStatus.REVIEW_REQUIRED)
        unmatched_count = sum(
            1
            for c in self._candidates
            if c.status in {MatchStatus.UNMATCHED_A, MatchStatus.UNMATCHED_B}
        )
        rejected_count = sum(1 for c in self._candidates if c.status == MatchStatus.REJECTED)
        blocked_count = self._blocked_pair_count()
        return (
            f"Matched auto {auto_count}, manual {manual_count}, review {review_count}, "
            f"unmatched {unmatched_count}, rejected {rejected_count}, blocked {blocked_count}."
        )

    def _blocked_pair_count(self) -> int:
        count = 0
        for desc_a in self._descriptors_a:
            for desc_b in self._descriptors_b:
                if not are_compatible(desc_a, desc_b):
                    count += 1
        return count

    def _cancel_current_work(self) -> None:
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.cancel()
            self.lbl_status.setText("Cancelling scan...")
        if self._compare_worker and self._compare_worker.isRunning():
            self._compare_worker.cancel()
            self.lbl_status.setText("Cancelling comparisons...")
        self.btn_cancel.setEnabled(False)

    def _set_busy(self, busy: bool) -> None:
        self.btn_scan.setEnabled(not busy)
        self.btn_run.setEnabled(not busy)
        self.btn_confirm.setEnabled(not busy)
        self.btn_reject.setEnabled(not busy)
        self.btn_export_review.setEnabled(not busy)
        self.btn_import_review.setEnabled(not busy)
        self.btn_open_gate.setEnabled(not busy)
        if hasattr(self, "btn_export_cloud"):
            self.btn_export_cloud.setEnabled(not busy and self._summary is not None)
        if hasattr(self, "btn_build_review"):
            self.btn_build_review.setEnabled(not busy and self._summary is not None)
        if hasattr(self, "btn_save_review_state"):
            self.btn_save_review_state.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)

    def _on_worker_cancelled(self) -> None:
        self._set_busy(False)
        self._scan_worker = None
        self._compare_worker = None
        self.progress.setValue(0)
        self.lbl_status.setText("Cancelled")

    def _on_worker_error(self, message: str) -> None:
        self._set_busy(False)
        self._scan_worker = None
        self._compare_worker = None
        self.progress.setValue(0)
        self.lbl_status.setText("Error")
        QMessageBox.critical(self, "Drawing Compare Workbench", message)


def _ko_light_stylesheet() -> str:
    return """
    QMainWindow, QDialog { background-color: #F7F8FA; color: #111827; font-family: 'Segoe UI'; font-size: 13px; }
    QLabel { color: #111827; }
    QLabel[role="muted"] { color: #374151; }
    QLabel[role="brandTitle"] { color: #111827; font-size: 24px; font-weight: 800; }
    QLabel[role="brandSubtitle"] { color: #374151; font-size: 13px; }
    QLabel[role="brandBadge"] {
        background-color: #DCEBFF; color: #003B80; border: 1px solid #8DBBFF; border-radius: 10px;
        padding: 5px 9px; font-weight: 700;
    }
    QFrame#Card, QGroupBox { background-color: #FFFFFF; border: 1px solid #9CA3AF; border-radius: 8px; }
    QFrame#BrandHeader { background-color: #FFFFFF; border: 1px solid #9CA3AF; border-radius: 10px; }
    QLineEdit, QComboBox, QTextEdit, QListWidget, QTableWidget {
        background-color: #FFFFFF; color: #111827; border: 1px solid #9CA3AF; border-radius: 6px; padding: 6px;
        selection-background-color: #005FCC; selection-color: #FFFFFF;
    }
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QListWidget:focus { border: 2px solid #005FCC; }
    QPushButton {
        background-color: #FFFFFF; color: #111827; border: 1px solid #6B7280; border-radius: 6px;
        padding: 8px 12px; font-weight: 600;
    }
    QPushButton:hover { background-color: #E5E7EB; }
    QPushButton:disabled { background-color: #E5E7EB; color: #4B5563; border-color: #9CA3AF; }
    QPushButton[primary="true"] {
        background-color: #005FCC; color: #FFFFFF; border: 1px solid #004A9F; font-size: 16px; padding: 12px 18px;
    }
    QPushButton[primary="true"]:hover { background-color: #004A9F; }
    QPushButton[primary="true"]:disabled { background-color: #9CA3AF; color: #111827; border-color: #6B7280; }
    QPushButton[role="quiet"] { background-color: #F7F8FA; color: #111827; border: 1px solid #9CA3AF; }
    QPushButton[role="quiet"]:hover { background-color: #E5E7EB; }
    QProgressBar {
        background-color: #FFFFFF; border: 1px solid #9CA3AF; border-radius: 6px; color: #111827;
        text-align: center; min-height: 18px;
    }
    QProgressBar::chunk { background-color: #005FCC; border-radius: 5px; }
    QListWidget::item { padding: 8px; border-bottom: 1px solid #E5E7EB; }
    QListWidget::item:selected { background-color: #DCEBFF; color: #111827; border-left: 4px solid #005FCC; }
    QHeaderView::section { background-color: #E5E7EB; color: #111827; border: 1px solid #9CA3AF; padding: 6px; }
    QSplitter::handle { background-color: #D1D5DB; }
    """


def _run_output_dir() -> Path:
    return _workbench_data_dir() / "runs" / f"compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _int_value(value, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except Exception:
        return default


def _format_count(value) -> str:
    return f"{_int_value(value):,}"


def _change_grade(raw_count: int, failed: bool = False) -> str:
    if failed:
        return "실패"
    if raw_count >= 3000:
        return "큼"
    if raw_count >= 500:
        return "보통"
    if raw_count > 0:
        return "작음"
    return "변경 없음"


def _ko_change_type(value: str) -> str:
    return {
        "added": "+ 추가",
        "deleted": "- 삭제",
        "modified": "~ 수정",
        "moved": "이동",
        "mixed": "혼합",
    }.get(str(value or "").lower(), str(value or ""))


def _filter_overlays_by_pdf_pages(
    overlays: list,
    page_a: int,
    page_b: int,
) -> list:
    """Phase H multi-page navigation — keep only overlays whose Phase H
    page indices match (``page_a``, ``page_b``).

    For DXF/DWG runs and single-page PDFs every overlay falls through
    (no page metadata → kept). For multi-page PDFs the filter splits
    overlays into per-page-pair groups so the GUI can render only the
    overlays for the currently-displayed background.

    The helper inspects both top-level ``page_a``/``page_b`` keys and
    nested ``metadata`` (mirrors ``_primary_page_pair_for_pair`` in
    ``viewer_package``). An overlay missing page indices is treated as
    "applies to every page" (kept).

    Pure Python; module-level so the GUI tree-build path is testable
    without Qt.
    """

    target_a = int(page_a)
    target_b = int(page_b)
    out = []
    for overlay in overlays:
        if not isinstance(overlay, dict):
            continue
        # Probe top-level then nested metadata for page indices.
        pa = pb = None
        for source in (overlay, overlay.get("metadata") if isinstance(overlay.get("metadata"), dict) else None):
            if not isinstance(source, dict):
                continue
            if "page_a" in source or "page_b" in source:
                try:
                    pa = int(source.get("page_a", 0) or 0)
                    pb = int(source.get("page_b", 0) or 0)
                    break
                except (TypeError, ValueError):
                    continue
        # No page metadata at all → keep (DXF / single-page PDF / legacy)
        if pa is None or pb is None:
            out.append(overlay)
            continue
        if pa == target_a and pb == target_b:
            out.append(overlay)
    return out


def _zone_category_icon_v2(category_label: str) -> str:
    """Phase I2 — Pick the leading emoji for a category header row.

    Pure cosmetic — driven by the Korean labels from
    ``zone_classifier.py``. Unknown labels fall back to a neutral dot
    so the tree still renders cleanly even when classifier evolves.
    """

    label = str(category_label or "")
    if "구조 부재" in label:
        return "🏗️"
    if "그리드" in label:
        return "📐"
    if "치수" in label or "주석" in label:
        return "📝"
    if "상세" in label or "마킹" in label:
        return "🔍"
    if "레이어" in label or "표기" in label:
        return "🎨"
    return "•"


def _group_zones_by_category_v2(
    zones: list[dict],
    classify_fn,
    *,
    fallback_label: str = "기타 변경",
) -> list[tuple[str, int, list[dict]]]:
    """Phase I2 — Pure helper that groups zones by AI category.

    ``zones`` is a list of overlay / top_issue dicts (each must carry a
    ``zone_id`` key). ``classify_fn`` is a callable
    ``zone_id -> Optional[ZoneCategoryResult]`` (typically
    ``self._zone_category_for(pair_id, zone_id)``).

    Returns a list of ``(category_label, severity_boost, zones)`` tuples
    sorted by:

      1. severity_boost descending — so structural / grid changes float
         to the top of the tree
      2. category_label ascending — deterministic tie-break

    Zones with no classification (classify_fn returns None) are still
    surfaced in a synthetic ``fallback_label`` bucket. Zones missing a
    ``zone_id`` are silently skipped — they have no UI affordance to
    select anyway.

    Within each bucket the zones preserve the order they came in. The
    caller (``_populate_zone_list_v2``) is responsible for that pre-sort.

    Module-level so the GUI tree-build path is testable without Qt.
    """

    if not zones:
        return []

    # Tally zones per category, remember the boost so we can sort.
    grouped: dict[str, list[dict]] = {}
    boost_by_label: dict[str, int] = {}

    for zone in zones:
        if not isinstance(zone, dict):
            continue
        zone_id = str(zone.get("zone_id") or "").strip()
        if not zone_id:
            continue

        result = None
        try:
            result = classify_fn(zone_id)
        except Exception:  # noqa: BLE001 — defensive, classify must never break populate
            result = None

        if result is not None and getattr(result, "category", None):
            label = str(result.category)
            boost = int(getattr(result, "severity_boost", 0) or 0)
        else:
            label = fallback_label
            boost = 0

        bucket = grouped.setdefault(label, [])
        bucket.append(zone)
        # If the same label appears with different boosts (shouldn't
        # normally happen — boost is a property of the category itself,
        # not the zone — but defend), keep the highest so sorting still
        # honours the most-important interpretation.
        if label not in boost_by_label or boost > boost_by_label[label]:
            boost_by_label[label] = boost

    # Sort: -boost asc (highest first), then label asc
    ordered_labels = sorted(
        grouped.keys(),
        key=lambda lab: (-int(boost_by_label.get(lab, 0)), lab),
    )

    return [
        (label, int(boost_by_label.get(label, 0)), grouped[label])
        for label in ordered_labels
    ]


def _build_zone_tree_plan_data_v2(
    *,
    dashboard_issues: list[dict],
    overlays: list[dict],
    preview_zones: list[dict],
    category_by_zone: Mapping[str, Any],
    active_zone_id: str,
    allow_clustering: bool = True,
    clustering_enabled: bool = True,
    prefer_overlays: bool = False,
) -> tuple[list[dict], dict[str, dict]]:
    """Pure zone-tree plan builder safe to run off the GUI thread.

    The returned plan contains only serialisable row data. QTreeWidgetItem
    objects are still created on the GUI thread by the caller.
    """

    active_issue_by_zone: dict[str, dict] = {}
    zones_for_grouping: list[dict] = []
    row_label_fn = None

    use_overlays = bool(prefer_overlays and overlays)
    if dashboard_issues and not use_overlays:
        def _issue_sort_key(issue: dict) -> tuple:
            zid = str(issue.get("zone_id") or "")
            cat = category_by_zone.get(zid)
            boost = -(int(getattr(cat, "severity_boost", 0) or 0) if cat else 0)
            return (boost, -float(issue.get("priority_score") or 0.0), zid)

        zones_for_grouping = sorted(dashboard_issues, key=_issue_sort_key)
        for issue in zones_for_grouping:
            zone_id = str(issue.get("zone_id") or "")
            if zone_id:
                active_issue_by_zone[zone_id] = issue

        def _issue_label(issue: dict) -> str:
            zone_id = str(issue.get("zone_id") or "")
            return (
                f"{zone_id} · "
                f"{issue.get('change_type_ko') or _ko_change_type(issue.get('change_type'))} · "
                f"{issue.get('severity_ko') or issue.get('severity')} · "
                f"점수 {float(issue.get('priority_score') or 0.0):.1f} · "
                f"변경 {_format_count(issue.get('raw_change_count'))}"
            )

        row_label_fn = _issue_label
    elif overlays:
        def _overlay_sort_key(overlay: dict) -> tuple:
            zid = str(overlay.get("zone_id") or "")
            cat = category_by_zone.get(zid)
            boost = -(int(getattr(cat, "severity_boost", 0) or 0) if cat else 0)
            return (boost, -_int_value(overlay.get("raw_change_count")), zid)

        zones_for_grouping = sorted(overlays, key=_overlay_sort_key)

        def _overlay_label(overlay: dict) -> str:
            zone_id = str(overlay.get("zone_id") or "")
            return (
                f"{zone_id} · "
                f"{overlay.get('change_label') or _ko_change_type(overlay.get('change_type'))} · "
                f"{overlay.get('severity')} · "
                f"변경 {_format_count(overlay.get('raw_change_count'))}"
            )

        row_label_fn = _overlay_label
    elif preview_zones:
        zones_for_grouping = sorted(
            preview_zones,
            key=lambda item: (-_int_value(item.get("raw_change_count")), str(item.get("zone_id") or "")),
        )

        def _preview_label(zone: dict) -> str:
            return (
                f"{zone.get('zone_id') or ''} · "
                f"{_ko_change_type(zone.get('change_type'))} · "
                f"{zone.get('severity')} · "
                f"변경 {_format_count(zone.get('raw_change_count'))}"
            )

        row_label_fn = _preview_label

    if not zones_for_grouping or row_label_fn is None:
        return [], active_issue_by_zone

    groups = _group_zones_by_category_v2(
        zones_for_grouping,
        lambda zid: category_by_zone.get(str(zid or "")),
    )
    from src.services.comparison.zone_clusterer import (
        ClusterOptions, cluster_zones,
    )

    cluster_opts = ClusterOptions(enabled=bool(allow_clustering and clustering_enabled))
    plan: list[dict] = []
    active_zone_id = str(active_zone_id or "")
    for group_idx, (label, _boost, zones_in_group) in enumerate(groups):
        total_count = len(zones_in_group)
        clusters = cluster_zones(zones_in_group, options=cluster_opts)
        row_count = len(clusters)
        active_zone_in_group = bool(
            active_zone_id
            and any(str(z.get("zone_id") or "") == active_zone_id for z in zones_in_group)
        )
        items: list[dict] = []
        for cluster in clusters:
            if cluster.is_singleton:
                zone = cluster.representative
                zone_id = str(zone.get("zone_id") or "")
                if not zone_id:
                    continue
                items.append({
                    "kind": "leaf",
                    "label": row_label_fn(zone),
                    "zone_id": zone_id,
                })
            else:
                children: list[dict] = []
                has_active = False
                for zone in cluster.members:
                    zone_id = str(zone.get("zone_id") or "")
                    if not zone_id:
                        continue
                    children.append({
                        "kind": "leaf",
                        "label": row_label_fn(zone),
                        "zone_id": zone_id,
                    })
                    if active_zone_id and zone_id == active_zone_id:
                        has_active = True
                items.append({
                    "kind": "cluster",
                    "label": f"  {cluster.summary_label}",
                    "tooltip": (
                        f"{cluster.summary_label} — {cluster.size}개 변경구역 묶음. "
                        f"펼쳐서 개별 구역을 검토할 수 있습니다."
                    ),
                    "expanded": has_active,
                    "children": children,
                })
        plan.append({
            "header_text": f"{_zone_category_icon_v2(label)} {label}  ({total_count})",
            "tooltip": (
                f"{label} · {total_count}개 변경구역"
                + (f" ({row_count}행으로 묶임)" if row_count != total_count else "")
            ),
            "expanded": bool((group_idx == 0) or active_zone_in_group),
            "items": items,
        })
    return plan, active_issue_by_zone


def _format_zone_count_summary_v2(
    counts: dict[str, int],
    *,
    visible_total: Optional[int] = None,
    max_categories: int = 6,
) -> str:
    """Phase I1 — Build the "📋 47개 변경구역 (구조 12 · 치수 18 · …)" line.

    ``counts`` maps category name → count for the active drawing.
    ``visible_total`` defaults to ``sum(counts.values())`` but the caller
    can pass a different value when a filter is applied (e.g. category
    filter shows "47개 중 12개 표시 (구조 12)").

    Module-level so it's testable without spinning up Qt — used by
    ``_refresh_category_summary_v2``.

    Returns ``"-"`` when there are no zones at all so callers can drop
    it straight into ``QLabel.setText`` without conditional logic.
    """

    if not counts:
        return "📋 변경구역 없음"
    total = sum(int(v) for v in counts.values())
    if total <= 0:
        return "📋 변경구역 없음"

    # Sort by count desc, then category name asc for deterministic output.
    ordered = sorted(counts.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
    head = ordered[: max(1, max_categories)]
    tail = ordered[max(1, max_categories) :]

    parts = [f"{cat} {count}" for cat, count in head if int(count) > 0]
    if tail:
        rest = sum(int(c) for _, c in tail)
        if rest > 0:
            parts.append(f"기타 {rest}")

    breakdown = " · ".join(parts) if parts else ""

    shown = total if visible_total is None else int(visible_total)
    if shown == total:
        prefix = f"📋 {total}개 변경구역"
    else:
        prefix = f"📋 {total}개 중 {shown}개 표시"

    return f"{prefix} ({breakdown})" if breakdown else prefix


def _filter_zones_for_batch_v2(
    overlays: list[dict],
    selection: dict,
) -> list[str]:
    """Phase G3.7 — Pure helper for the batch dialog.

    Walks ``overlays`` and returns the ``zone_id`` of every entry that
    matches every active filter. The dialog calls this on every combo
    change to drive the live "적용 대상: N개" counter.

    ``selection`` keys (``str``):
        change_type, severity, entity_type, layer, current_status

    The literal ``selection["any_label"]`` (typically ``"(모두)"``) means
    "no filter for this dimension". An empty / missing key in an overlay
    counts as "no value" — only matched when the filter says ANY.

    Each overlay must carry ``zone_id`` and (when filtering by current
    status) ``_current_status`` injected by the dialog from
    ``_review_status_for_zone_v2``.

    Module-level so it's testable without spinning up Qt.
    """

    any_label = str(selection.get("any_label") or "(모두)")
    filter_keys = (
        ("change_type", "change_type"),
        ("severity", "severity"),
        ("entity_type", "entity_type"),
        ("layer", "layer"),
        ("current_status", "_current_status"),
    )
    selected: dict[str, str] = {
        sel_key: str(selection.get(sel_key) or "").strip()
        for sel_key, _data_key in filter_keys
    }

    matched: list[str] = []
    for overlay in overlays:
        if not isinstance(overlay, dict):
            continue
        zone_id = str(overlay.get("zone_id") or "").strip()
        if not zone_id:
            continue

        passes = True
        for sel_key, data_key in filter_keys:
            sel_val = selected[sel_key]
            if not sel_val or sel_val == any_label:
                continue
            actual = str(overlay.get(data_key) or "").strip()
            if actual != sel_val:
                passes = False
                break
        if passes:
            matched.append(zone_id)

    return matched


def _preview_status_label(status, available=None) -> str:
    normalized = str(status or "").lower()
    if normalized == "real_preview" or available is True:
        return "실미리보기"
    if normalized == "relative_only":
        return "상대위치"
    if normalized == "render_pending":
        return "렌더대기"
    if normalized == "render_failed" or available is False:
        return "미리보기 실패"
    return "렌더대기"


class QtQuickUnavailableLightweightViewport(QWidget):
    """No-op stand-in used when Qt Quick is disabled for startup stability."""

    viewportChanged = Signal(float, float, float)
    overlayClicked = Signal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        side: str = "after",
    ) -> None:
        super().__init__(parent)
        self._side = side
        self._pdf_render_state: Optional[dict] = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._notice = QLabel("Qt Quick viewer disabled; compatibility preview is active.")
        self._notice.setProperty("role", "muted")
        self._notice.setAlignment(Qt.AlignCenter)
        self._notice.setWordWrap(True)
        layout.addWidget(self._notice)

    def load_scene_pack(self, *_args, empty_notice: str = "", **_kwargs) -> bool:
        if empty_notice:
            self._notice.setText(empty_notice)
        return False

    def load_pdf_page(self, *_args, **_kwargs) -> bool:
        self._notice.setText("PDF lightweight viewer unavailable in compatibility mode.")
        return False

    def set_overlays(self, *_args, **_kwargs) -> None:
        return None

    def set_overlay_opacity_scale(self, *_args, **_kwargs) -> None:
        return None


class DrawingCompareWorkbenchV2(QMainWindow):
    """Korean, single-action drawing comparison UX."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE_KO)
        app_icon = QIcon(str(_drawing_compare_asset_path("app_icon.ico")))
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        # Responsive sizing — user report 2026-05-15: UI modules clipped on
        # smaller monitors because resize(1440, 860) was hardcoded without a
        # minimum or any awareness of the available screen real estate.
        # Now we (1) declare a 1000×640 minimum that fits 1366×768 laptops,
        # (2) compute an initial size that fits inside the primary screen
        # minus chrome, and (3) center the window on that screen.
        self.setMinimumSize(1000, 640)
        try:
            screen = QApplication.primaryScreen()
            geometry = screen.availableGeometry() if screen is not None else None
        except Exception:
            geometry = None
        if geometry is not None:
            ideal_w = max(1000, min(1440, geometry.width() - 80))
            ideal_h = max(640, min(860, geometry.height() - 120))
            self.resize(ideal_w, ideal_h)
            # Center inside the available screen rect so the title bar is
            # never spawned off-screen on multi-monitor setups.
            frame_geom = self.frameGeometry()
            frame_geom.moveCenter(geometry.center())
            self.move(frame_geom.topLeft())
        else:
            self.resize(1440, 860)
        self.setStyleSheet(_ko_light_stylesheet())

        self._source_a = ""
        self._source_b = ""
        self._worker: Optional[AutoFolderCompareWorker] = None
        self._result: Optional[FolderCompareRunResult] = None
        self._drawing_rows: list[dict] = []
        self._preview_by_pair: dict[str, PreviewArtifact] = {}
        self._dashboard: dict = {}
        self._viewer_manifest: dict = {}
        self._viewer_manifest_path: Optional[Path] = None
        self._viewer_root: Optional[Path] = None
        self._review_state_path_v2: Optional[Path] = None
        self._review_records_v2: dict[str, ReviewStateRecord] = {}
        self._viewer_pairs_by_id: dict[str, dict] = {}
        self._viewer_overlay_cache: dict[str, list[dict]] = {}
        self._viewer_overlay_cache_order_v2: list[str] = []
        self._viewer_overlay_cache_bytes_by_pair_v2: dict[str, int] = {}
        self._viewer_overlay_cache_total_bytes_v2: int = 0
        self._viewer_overlay_cache_evictions_v2: int = 0
        self._tile_manifest_cache_v2: dict[tuple[str, int, int, str, str], dict] = {}
        self._lightweight_raster_pairs: set[str] = set()
        self._render_status_by_pair: dict[str, str] = {}
        self._render_worker: Optional[PairPreviewRenderWorker] = None
        self._visible_tile_worker_v2: Optional[VisibleTileWindowWorker] = None
        self._visible_tile_pending_request_v2: Optional[dict[str, Any]] = None
        self._visible_tile_generation_v2: int = 0
        self._full_zone_tree_overlay_worker_v2: Optional[FullZoneTreeOverlayLoadWorker] = None
        self._full_zone_tree_plan_worker_v2: Optional[FullZoneTreePlanWorker] = None
        self._retired_qthreads_v2: list[QThread] = []
        self._pending_render_request_v2: Optional[tuple[str, dict, dict]] = None
        self._zone_tree_rebuild_generation_v2: int = 0
        self._pending_full_zone_tree_pair_id_v2: str = ""
        self._full_zone_tree_chunk_state_v2: Optional[dict] = None
        self._defer_next_initial_zone_heavy_render_v2: Optional[tuple[str, str]] = None
        self._initial_zone_heavy_render_generation_v2: int = 0
        self._lightweight_pair_load_generation_v2: int = 0
        self._pdf_prewarm_generation_v2: int = 0
        self._zone_render_controller_v2 = ZoneRenderProcessController(timeout_ms=10_000, parent=self)
        self._zone_render_controller_v2.finished.connect(self._on_zone_crop_render_finished_v2)
        self._zone_render_controller_v2.error.connect(self._on_zone_crop_render_error_v2)
        self._pending_zone_render_request_v2: Optional[tuple[str, str] | tuple[str, str, str]] = None
        self._selected_zone_render_generation_v2: int = 0
        self._active_zone_render_request_v2: Optional[tuple[str, str, str]] = None
        # ② deferred full-detail upgrade: last (pair, zone, request) for which a
        # prefer_source_render upgrade was already issued, so the upgrade fires
        # at most once per zone selection and never loops.
        self._zone_full_detail_started_request_v2: Optional[tuple[str, str, str]] = None
        self._active_issue_by_zone: dict[str, dict] = {}
        self._active_all_overlays_by_zone: dict[str, dict] = {}
        self._active_overlays_by_zone: dict[str, dict] = {}
        self._active_row: Optional[dict] = None
        self._active_zone_id = ""
        self._syncing_preview_viewports = False
        self._visible_tile_request_timer_v2 = QTimer(self)
        self._visible_tile_request_timer_v2.setSingleShot(True)
        self._visible_tile_request_timer_v2.setInterval(180)
        self._visible_tile_request_timer_v2.timeout.connect(self._run_pending_visible_tile_window_v2)
        self._viewer_perf_summary: dict = {}
        self._active_pattern_filter_v2: str = ""
        self._run_completion_v2: dict = {}
        self._auto_advance_v2: bool = True
        self._active_viewer_render_policy_v2: str = "top-issues"
        # E2 — heuristic category cache: pair_id → {zone_id → ZoneCategoryResult}
        self._zone_categories_v2: dict[str, dict[str, ZoneCategoryResult]] = {}
        self._active_category_filter_v2: str = "전체"
        # Phase G2.5 — track whether the user explicitly set the category
        # filter (vs the auto-noise filter we apply when zone count > 500).
        # Once True, the auto-filter never overwrites again for this session.
        self._user_picked_category_filter_v2: bool = False
        # Phase I3 — fold near-duplicate zones inside each AI category
        # into cluster rows so the user sees "[12] DIM-A · 수정 · TEXT"
        # once instead of 12 nearly-identical rows. Toggleable from the
        # 보기 menu via "반복 변경 묶기" (default ON).
        self._zone_clustering_enabled_v2: bool = True
        # Phase G3.1 — same pattern for the lightweight viewer toggle. We
        # auto-enable it for DXF/DWG sources (where the viewport is verified
        # to render skeleton primitives) but keep PDF on the legacy raster
        # viewer (PDF lightweight rendering ships in G2.6). Once the user
        # toggles it manually, we never auto-flip again this session.
        self._user_picked_lightweight_v2: bool = False
        self._dxf_cache_dir = _workbench_data_dir() / "dxf_cache"
        # Phase B1 — vector zone state. The QProcess holds the running
        # subprocess; the path cache survives across zone clicks so the
        # second click on a previously-rendered zone is instant.
        self._zone_vector_qprocess: Optional[QProcess] = None
        self._zone_vector_paths: dict[tuple[str, str], str] = {}
        self._zone_vector_pending: Optional[tuple[str, str, str]] = None  # (pair, zone, output_svg)
        self._zone_vector_result_json: Optional[Path] = None
        # Phase B1.5 — when the user clicks the explicit "벡터로 자세히
        # 보기" button we ALSO open the SVG externally (browser/Inkscape
        # for the user who wants infinite zoom outside the workbench).
        # When the auto-trigger fires from zone selection we skip the
        # external open — inline-only is enough for in-workbench review.
        self._zone_vector_button_external: bool = False

        self._build_v2_ui()
        self._build_menu_bar_v2()
        self._update_run_enabled()
        self._refresh_recent_menu_v2()
        # QW1 — first-run tutorial (skipped when the user has completed it once)
        QTimer.singleShot(400, self._maybe_show_first_run_tutorial_v2)
        # Defer prewarm via a method (not a lambda capturing ``self``) so
        # the deferred call can early-return if the workbench was already
        # destroyed (common in unit tests where workbench.deleteLater()
        # runs before the 500ms timer fires, leaving a dangling C++
        # ZoneRenderProcessController object behind).
        QTimer.singleShot(500, self._prewarm_zone_render_controller_v2)
        # Phase I — AI 분류기 백엔드 백그라운드 워밍업. 800 ms 지연으로
        # zone-render prewarm(500ms)이 완료된 후 호출되도록 직렬화 —
        # 단일 코어 머신에서 두 작업이 동시에 시작하면 cold-start 양쪽
        # 다 늦어짐. 모델 미설치 시는 silently skip + lbl_status_v2에 안내.
        QTimer.singleShot(800, self._kickoff_ai_prepare_v2)

    def _build_v2_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        layout.addWidget(self._build_brand_header())

        # B2 — wrap input/progress/summary in a single collapsible region so
        # the user can hide it after the comparison runs and give the viewer
        # nearly the full window height.
        self.header_region_v2 = QFrame()
        self.header_region_v2.setObjectName("HeaderRegion")
        header_region_layout = QVBoxLayout(self.header_region_v2)
        header_region_layout.setContentsMargins(0, 0, 0, 0)
        header_region_layout.setSpacing(12)

        top = QFrame()
        top.setObjectName("Card")
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(14, 12, 14, 12)
        top_layout.addLayout(self._input_row_v2("변경 전 파일/폴더", "source_a"))
        top_layout.addLayout(self._input_row_v2("변경 후 파일/폴더", "source_b"))
        # C3 — recent comparisons quick re-select
        recent_row = QHBoxLayout()
        from PySide6.QtWidgets import QToolButton  # local import
        self.btn_recent_v2 = QToolButton()
        self.btn_recent_v2.setText("▼ 최근 비교 불러오기")
        self.btn_recent_v2.setPopupMode(QToolButton.InstantPopup)
        self.btn_recent_v2.setToolTip("최근에 비교한 파일/폴더 쌍을 클릭 한 번으로 다시 불러옵니다.")
        recent_row.addWidget(self.btn_recent_v2)
        recent_row.addStretch()
        top_layout.addLayout(recent_row)
        action_row = QHBoxLayout()
        self.chk_recursive_v2 = QCheckBox("하위 폴더 포함")
        # Audit-gates §10 follow-up — quality selector defaults to "🤖 자동
        # (권장)". The auto path measures input size and chooses the best DPI
        # tier that fits inside the viewer memory cap, removing the S20-class
        # hang that occurred when DPI 400 was the default.
        quality_label = QLabel("화질")
        quality_label.setProperty("role", "muted")
        self.cmb_quality_v2 = QComboBox()
        for label, _dpi, _edge in PREVIEW_QUALITY_PRESETS:
            self.cmb_quality_v2.addItem(label)
        self.cmb_quality_v2.setCurrentIndex(PREVIEW_QUALITY_DEFAULT_INDEX)
        self.cmb_quality_v2.setToolTip(
            "기본값 '🤖 자동 (권장)' 은 입력 도면 크기를 측정해 최적 화질을 선택합니다. "
            "수동 선택 시 'DPI 400' 은 작은 도면(50MB 이하)에만 사용하세요 — "
            "S20급 평면도에서는 메모리 부족으로 작업이 중단될 수 있습니다."
        )
        # C3 — compare preset combo. Selecting a preset rewrites quality + recursive
        preset_label = QLabel("프리셋")
        preset_label.setProperty("role", "muted")
        self.cmb_preset_v2 = QComboBox()
        for entry in COMPARE_PRESETS:
            self.cmb_preset_v2.addItem(entry[0])
        self.cmb_preset_v2.setCurrentIndex(COMPARE_PRESET_DEFAULT_INDEX)
        self.cmb_preset_v2.setToolTip("자주 쓰는 화질 + 하위 폴더 조합을 한 번에 적용합니다.")
        self.cmb_preset_v2.currentIndexChanged.connect(self._on_compare_preset_changed_v2)
        self.btn_run_v2 = QPushButton("비교 실행")
        self.btn_run_v2.setProperty("primary", "true")
        self.btn_run_v2.clicked.connect(self._run_auto_compare)
        self.btn_cancel_v2 = QPushButton("취소")
        self.btn_cancel_v2.clicked.connect(self._cancel_auto_compare)
        self.btn_cancel_v2.setEnabled(False)
        action_row.addWidget(preset_label)
        action_row.addWidget(self.cmb_preset_v2)
        action_row.addSpacing(12)
        action_row.addWidget(self.chk_recursive_v2)
        action_row.addWidget(quality_label)
        action_row.addWidget(self.cmb_quality_v2)
        # Phase O Commit 3 [RV-20260508-009] — 정밀 텍스트 감지 토글.
        # 기본 ON: DXF/DWG 블록 attribute (ATTRIB) 와 블록 정의 내부
        # TEXT/MTEXT/ATTDEF 변경을 감지. OFF 면 INSERT hash 가 Phase
        # O Commit 1 이전 동작으로 회귀하여 사용자가 false positive 가
        # 부담스러울 때 끌 수 있음. ATTRIB 자체 추출은 hash 와 별개로
        # 항상 활성화됨 (extract 단계 기능; 비활성화 옵션 별도 미제공).
        self.chk_block_text_detection_v2 = QCheckBox("정밀 텍스트 감지 (블록)")
        self.chk_block_text_detection_v2.setChecked(True)
        self.chk_block_text_detection_v2.setToolTip(
            "DXF/DWG 블록 attribute (ATTRIB) 와 블록 정의 내부의 "
            "TEXT/MTEXT/ATTDEF 텍스트 변경을 감지합니다.\n"
            "사용자 사례 'DOWEL BAR (2)SHD13@100 → @200' 같은 변경이 "
            "보이게 합니다. 끄면 INSERT 비교가 좌표/스케일 기준으로 "
            "회귀합니다 (Phase O 이전 동작)."
        )
        action_row.addWidget(self.chk_block_text_detection_v2)
        self.chk_auto_structural_clouds_v2 = QCheckBox("구조 핵심 자동 구름마크 별도 추출")
        self.chk_auto_structural_clouds_v2.setChecked(False)
        self.chk_auto_structural_clouds_v2.setToolTip(
            "확인 상태와 무관하게 member/dimension/rebar/grid/mixed 후보만 "
            "auto_structural_clouds 폴더로 별도 추출합니다.\n"
            "기본 OFF이며, 확인된 변경 구름마크와 섞이지 않습니다."
        )
        action_row.addWidget(self.chk_auto_structural_clouds_v2)
        action_row.addStretch()
        action_row.addWidget(self.btn_cancel_v2)
        action_row.addWidget(self.btn_run_v2)
        top_layout.addLayout(action_row)
        header_region_layout.addWidget(top)

        self.progress_v2 = QProgressBar()
        self.lbl_status_v2 = QLabel("준비 중")
        self.lbl_status_v2.setProperty("role", "muted")
        header_region_layout.addWidget(self.progress_v2)
        header_region_layout.addWidget(self.lbl_status_v2)

        summary_header = QLabel("결과 요약")
        summary_header.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        header_region_layout.addWidget(summary_header)

        # Responsive 8-card KPI grid — used to be a single QHBoxLayout which
        # forced all eight cards onto one row and clipped the right-hand
        # cards on <1280 px monitors. QGridLayout with a column count derived
        # from the available width lets the cards wrap to 2 rows × 4 cols on
        # mid-size monitors and 4 rows × 2 cols on very narrow ones, without
        # bespoke resize logic.
        from PySide6.QtWidgets import QGridLayout  # local import — keeps top-of-file diff minimal

        summary = QGridLayout()
        summary.setHorizontalSpacing(8)
        summary.setVerticalSpacing(8)
        self.summary_labels = {}
        self._summary_card_widgets: list[QFrame] = []
        cards = (
            ("completed", "비교 완료"),
            ("failed", "실패"),
            ("raw", "총 변경"),
            ("zones", "변경구역"),
            ("issues", "우선 검토"),
            ("patterns", "반복 패턴"),
            ("cloud", "구름마크"),
            ("omitted", "생략"),
        )
        # Choose the initial column count from the current window width so
        # the first paint already wraps appropriately on small monitors.
        try:
            initial_w = self.width() or 1440
        except Exception:
            initial_w = 1440
        if initial_w >= 1280:
            columns = 8
        elif initial_w >= 900:
            columns = 4
        else:
            columns = 2
        self._summary_card_columns = columns
        for idx, (key, title_text) in enumerate(cards):
            card, value_label = self._summary_card(title_text, "-")
            self.summary_labels[key] = value_label
            self._summary_card_widgets.append(card)
            row, col = divmod(idx, columns)
            summary.addWidget(card, row, col)
        # Make all columns share the row width equally so the cards stay
        # uniform when the user drags the window edge.
        for col in range(columns):
            summary.setColumnStretch(col, 1)
        self._summary_grid_layout = summary
        header_region_layout.addLayout(summary)
        self.lbl_review_queue_v2 = QLabel("업무 큐: 비교 실행 후 자동 비교/우선 검토/미매칭/차단 상태를 표시합니다.")
        self.lbl_review_queue_v2.setProperty("role", "brandBadge")
        self.lbl_review_queue_v2.setWordWrap(True)
        header_region_layout.addWidget(self.lbl_review_queue_v2)
        self.lbl_viewer_perf_v2 = QLabel("뷰어 성능: 비교 실행 후 캐시 적중과 cull p95를 표시합니다.")
        self.lbl_viewer_perf_v2.setProperty("role", "muted")
        self.lbl_viewer_perf_v2.setWordWrap(True)
        header_region_layout.addWidget(self.lbl_viewer_perf_v2)

        layout.addWidget(self.header_region_v2)

        # B2 — toggle row stays visible even when the header region collapses
        toggle_row = QHBoxLayout()
        from PySide6.QtWidgets import QToolButton  # local import
        self.btn_compact_v2 = QToolButton()
        self.btn_compact_v2.setText("▲ 입력 영역 접기 (뷰어 최대화)")
        self.btn_compact_v2.setCheckable(True)
        self.btn_compact_v2.setProperty("role", "quiet")
        self.btn_compact_v2.toggled.connect(self._on_compact_toggle_v2)
        toggle_row.addWidget(self.btn_compact_v2)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)
        self._compact_mode_v2: bool = False

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.addWidget(self._build_detail_panel())
        # Responsive sizing — replace the absolute [300, 780, 340] pixels with
        # stretch factors so Qt re-balances on resize instead of clipping the
        # outer panels on sub-1440 monitors. Initial sizes are computed from
        # the central widget width so the preview keeps majority space.
        splitter.setStretchFactor(0, 1)  # left panel — review queue list
        splitter.setStretchFactor(1, 3)  # preview — gets the lion's share
        splitter.setStretchFactor(2, 1)  # right detail — review form
        # Sensible defaults at first paint (overridden by stretch factors on
        # subsequent resize events).
        splitter.setSizes([260, 760, 300])
        # Never let a panel collapse to 0px (Qt default allows double-click on
        # the handle to fully collapse a panel, which users mistake for a UI bug).
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setCollapsible(2, False)
        splitter.setChildrenCollapsible(False)
        # 4px handle stays clickable but does not visually compete with content.
        splitter.setHandleWidth(4)
        layout.addWidget(splitter, stretch=1)

        # User report 2026-05-15: "창을 초과할때 옆에 스크롤 바가 생기면서
        # 위 아래로 조절하여 확인할수있게 조정해줘. 지금도 너무 짤려서 보기
        # 어려워". Wrap the entire central content in a QScrollArea so when
        # the window is smaller than the natural size of the header region +
        # splitter + KPI grid + status rows, the user sees scroll bars on
        # the right/bottom and can pan to clipped content instead of having
        # the panels truncated. ``setWidgetResizable(True)`` keeps the
        # content snapped to the viewport when there's enough room, so the
        # scrollbars only appear when actually needed.
        root.setMinimumSize(960, 720)  # natural size below which we scroll
        scroll_host = QScrollArea()
        scroll_host.setWidget(root)
        scroll_host.setWidgetResizable(True)
        scroll_host.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_host.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # No outer frame so the scrollarea blends into the window chrome.
        scroll_host.setFrameShape(QFrame.NoFrame)
        # Inherit the workbench background so the scroll viewport doesn't
        # paint a different colour than the surrounding content.
        scroll_host.setStyleSheet("QScrollArea { background: transparent; }")
        self.setCentralWidget(scroll_host)
        self._install_review_hotkeys_v2()

    def _build_menu_bar_v2(self) -> None:
        """Top-level menu bar so file actions are reachable even when the
        input region is collapsed (compact mode auto-enables after a run, and
        users would otherwise lose the file-pick buttons until they toggled
        the collapse manually)."""

        from PySide6.QtGui import QAction, QKeySequence
        menu_bar = self.menuBar()
        menu_bar.clear()
        file_menu = menu_bar.addMenu("&파일")

        new_compare_action = QAction("🆕 새 비교 시작", self)
        new_compare_action.setShortcut(QKeySequence("Ctrl+N"))
        new_compare_action.setStatusTip("입력 영역을 펼치고 변경 전 파일을 선택할 수 있도록 준비합니다.")
        new_compare_action.triggered.connect(self._start_new_compare_v2)
        file_menu.addAction(new_compare_action)

        pick_a_action = QAction("변경 전 폴더 선택...", self)
        pick_a_action.setShortcut(QKeySequence("Ctrl+1"))
        pick_a_action.triggered.connect(lambda: self._start_new_compare_v2(pick="source_a"))
        file_menu.addAction(pick_a_action)

        pick_b_action = QAction("변경 후 폴더 선택...", self)
        pick_b_action.setShortcut(QKeySequence("Ctrl+2"))
        pick_b_action.triggered.connect(lambda: self._start_new_compare_v2(pick="source_b"))
        file_menu.addAction(pick_b_action)

        file_menu.addSeparator()

        # Recent submenu — populated dynamically each time it opens so it
        # always reflects the latest entries from disk.
        self._recent_menu_v2 = file_menu.addMenu("최근 비교 불러오기")
        self._recent_menu_v2.aboutToShow.connect(self._populate_recent_menu_actions_v2)

        file_menu.addSeparator()
        exit_action = QAction("종료", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menu_bar.addMenu("&보기")
        toggle_compact_action = QAction("입력 영역 접기/펼치기", self)
        toggle_compact_action.setShortcut(QKeySequence("F"))
        toggle_compact_action.triggered.connect(
            lambda: self._set_compact_mode_v2(not self._compact_mode_v2)
        )
        view_menu.addAction(toggle_compact_action)

        fit_action = QAction("뷰어 전체 보기", self)
        fit_action.setShortcut(QKeySequence("Ctrl+0"))
        fit_action.triggered.connect(self._on_fit_view_v2)
        view_menu.addAction(fit_action)

        view_menu.addSeparator()
        # Phase G2.2 — toggle the new lightweight (vector) viewport.
        # When ON, the preview area uses LightweightDrawingViewport which
        # renders skeleton primitives natively (sharp at any zoom, instant
        # first paint). When OFF, the legacy GpuDrawingViewport is used.
        self.act_lightweight_viewer_v2 = QAction(
            "🆕 신형 라이트웨이트 뷰어 사용 (Phase G)", self,
        )
        self.act_lightweight_viewer_v2.setCheckable(True)
        self.act_lightweight_viewer_v2.setChecked(DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY)
        if not QT_QUICK_AVAILABLE:
            self.act_lightweight_viewer_v2.setEnabled(False)
            self.act_lightweight_viewer_v2.setVisible(False)
        elif DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
            self.act_lightweight_viewer_v2.setEnabled(False)
            self.act_lightweight_viewer_v2.setVisible(False)
        else:
            self.act_lightweight_viewer_v2.setShortcut(QKeySequence("Ctrl+L"))
        self.act_lightweight_viewer_v2.setToolTip(
            "벡터 기반 신형 뷰어로 전환합니다. 줌 시 흐려짐 없음, 즉시 첫 페인트.\n"
            "끄면 기존 raster PNG 뷰어로 복귀합니다."
        )
        self.act_lightweight_viewer_v2.toggled.connect(
            self._on_toggle_lightweight_viewer_v2
        )
        view_menu.addAction(self.act_lightweight_viewer_v2)

        # Phase G3.5 — auto-advance to next unreviewed zone (and next
        # drawing) after status set. Always-on by default; user can flip
        # it off if they prefer to manually navigate. Toggle exposed in
        # the View menu so power users can find it.
        self.act_auto_advance_v2 = QAction(
            "⏭️ 검토 후 다음 zone 자동 이동", self,
        )
        self.act_auto_advance_v2.setCheckable(True)
        self.act_auto_advance_v2.setChecked(True)
        self.act_auto_advance_v2.setToolTip(
            "1/2/3/4 키로 검토 상태 설정 후 자동으로 다음 미검토 zone으로 이동합니다.\n"
            "현재 도면의 모든 zone 검토 후엔 다음 도면으로 자동 이동.\n"
            "끄면 직접 J/K 또는 클릭으로 이동."
        )
        self.act_auto_advance_v2.toggled.connect(
            self._on_toggle_auto_advance_v2
        )
        view_menu.addAction(self.act_auto_advance_v2)

        # Phase I3 — toggle for repeat-zone clustering. ON by default;
        # power users who want to see every individual zone can flip it
        # off to bypass the [N] cluster rows in the zone tree.
        self.act_zone_clustering_v2 = QAction(
            "🗂️ 반복 변경 묶기 (Phase I3)", self,
        )
        self.act_zone_clustering_v2.setCheckable(True)
        self.act_zone_clustering_v2.setChecked(True)
        self.act_zone_clustering_v2.setToolTip(
            "같은 카테고리 안에서 거의 동일한 변경을 한 줄로 묶고\n"
            "펼쳤을 때만 개별 zone을 보여줍니다 (47줄 → ~15줄).\n"
            "끄면 모든 zone을 평면으로 표시합니다."
        )
        self.act_zone_clustering_v2.toggled.connect(
            self._on_toggle_zone_clustering_v2
        )
        view_menu.addAction(self.act_zone_clustering_v2)

        self.act_region_match_results_v2 = QAction("Detail Region Matching", self)
        self.act_region_match_results_v2.setToolTip(
            "Review region_detection_summary.json and region_match_summary.json, "
            "then save manual_region_matches.json for the next gated comparison run."
        )
        self.act_region_match_results_v2.triggered.connect(
            self._show_region_match_dialog_v2
        )
        self.act_region_match_results_v2.setEnabled(False)
        view_menu.addAction(self.act_region_match_results_v2)

        # 설정 메뉴 (PDF 보고서 브랜딩 + 검토자 정보 + AI 분류기)
        settings_menu = menu_bar.addMenu("&설정")
        report_settings_action = QAction("📄 보고서 설정 (검토자/회사/도장)", self)
        report_settings_action.setShortcut(QKeySequence("Ctrl+,"))
        report_settings_action.triggered.connect(self._show_report_settings_dialog_v2)
        settings_menu.addAction(report_settings_action)

        # Phase J Step 3 (J1) — AI 분류기 모드 선택 다이얼로그
        ai_settings_action = QAction("🤖 AI 분류기 설정...", self)
        ai_settings_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        ai_settings_action.setStatusTip(
            "임베딩 백엔드 모드(Auto/Quality/Speed/Off) + cosine 임계값 + "
            "Matryoshka 차원 절단 설정"
        )
        ai_settings_action.triggered.connect(self._show_ai_settings_dialog_v2)
        settings_menu.addAction(ai_settings_action)

        # Phase O — 노이즈 필터 설정 다이얼로그
        noise_filter_action = QAction("🧹 노이즈 필터...", self)
        noise_filter_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        noise_filter_action.setStatusTip(
            "도면 비교 노이즈 필터 — global alignment / cosmetic 분리 / "
            "zone promote / PDF 강도 (Phase O)"
        )
        noise_filter_action.triggered.connect(
            self._show_noise_filter_dialog_v2
        )
        settings_menu.addAction(noise_filter_action)

        visual_ext.attach_visual_extensions(self, menu_bar)
        help_menu = menu_bar.addMenu("&도움말")
        tutorial_action = QAction("📘 시작 가이드 (5단계 튜토리얼)", self)
        tutorial_action.setShortcut(QKeySequence("F1"))
        tutorial_action.triggered.connect(lambda: self._show_tutorial_dialog_v2(force=True))
        help_menu.addAction(tutorial_action)
        about_action = QAction("정보", self)
        about_action.triggered.connect(self._show_about_v2)
        help_menu.addAction(about_action)

    def _populate_recent_menu_actions_v2(self) -> None:
        """Rebuild the 'Recent comparisons' submenu just before it opens."""

        from PySide6.QtGui import QAction
        if not hasattr(self, "_recent_menu_v2"):
            return
        self._recent_menu_v2.clear()
        entries = self._load_recent_paths_v2()
        if not entries:
            placeholder = QAction("(최근 비교 없음)", self)
            placeholder.setEnabled(False)
            self._recent_menu_v2.addAction(placeholder)
            return
        for entry in entries[:RECENT_PATHS_LIMIT]:
            if not isinstance(entry, dict):
                continue
            a = str(entry.get("a") or "")
            b = str(entry.get("b") or "")
            label = f"{Path(a).name} ↔ {Path(b).name}"
            if entry.get("ts"):
                label += f"  ({entry['ts'][:10]})"
            action = QAction(label, self)
            action.triggered.connect(
                lambda _checked=False, _a=a, _b=b: self._apply_recent_pair_and_show_v2(_a, _b)
            )
            self._recent_menu_v2.addAction(action)

    def _apply_recent_pair_and_show_v2(self, source_a: str, source_b: str) -> None:
        """Recent menu pick — load paths AND make sure the input row is visible."""

        if self._compact_mode_v2:
            self._set_compact_mode_v2(False)
        self._apply_recent_pair_v2(source_a, source_b)

    def _tutorial_completed_path_v2(self) -> Path:
        return _workbench_data_dir() / TUTORIAL_COMPLETED_FILENAME

    def _maybe_show_first_run_tutorial_v2(self) -> None:
        """QW1 — show the 5-step tutorial on the very first launch only.

        Wrapped in defensive try/except so a stale QTimer.singleShot delivered
        after the Workbench is being torn down (common in unit tests) doesn't
        crash. Any failure here is non-fatal — the user can always reach the
        tutorial via the 도움말 menu or F1.
        """

        try:
            # Check the underlying QObject is still alive; QTimer.singleShot
            # may fire on a partially-deleted instance during teardown.
            _ = self.windowTitle()
        except RuntimeError:
            return
        try:
            flag_path = self._tutorial_completed_path_v2()
            if flag_path.exists():
                return
            self._show_tutorial_dialog_v2(force=True)
        except Exception:
            logger.debug("First-run tutorial skipped due to runtime error", exc_info=True)

    def _prewarm_zone_render_controller_v2(self) -> None:
        """Defensive deferred-prewarm callback — same QObject-liveness check
        as ``_maybe_show_first_run_tutorial_v2``.

        The original implementation used a ``lambda: self._zone_render_…
        prewarm(...)`` queued via QTimer.singleShot(500ms). In unit tests
        the workbench is often deleted within the same event-loop tick
        (``workbench.deleteLater()`` in a finally block) so when the
        timer fires the underlying C++ ``ZoneRenderProcessController`` is
        already gone — leading to ``RuntimeError: Internal C++ object …
        already deleted`` cascading into pytest as
        ``Exceptions caught in Qt event loop`` and surfacing as a CALL
        ERROR on whichever test happens to be running. This wrapper
        early-returns when either the workbench OR the controller has
        been torn down, so the deferred prewarm becomes a true no-op
        instead of crashing late.
        """

        try:
            _ = self.windowTitle()
        except RuntimeError:
            return
        controller = getattr(self, "_zone_render_controller_v2", None)
        if controller is None:
            return
        # The controller is a QObject — same liveness check pattern.
        try:
            _ = controller.parent()
        except RuntimeError:
            return
        try:
            controller.prewarm(
                render_environment_signature(dxf_cache_dir=self._dxf_cache_dir)
            )
        except Exception:
            logger.debug("Zone render prewarm skipped due to runtime error", exc_info=True)

    # ---------------------------------------------------------------
    # Phase I — AI 분류기 dispatcher 백그라운드 워밍업
    # ---------------------------------------------------------------

    def _load_ai_config_v2(self):
        """Build the AiClassifierConfig used by Stage-2.

        Phase J Step 3 (J1): reads
        ``%LOCALAPPDATA%/DrawingCompareWorkbench/ai_config.json`` via
        ``config_io.load_ai_config()``. Returns ``auto_mode()`` when
        the file is missing / corrupt / schema-incompatible — that
        contract is enforced inside load_ai_config so this method
        never raises.

        The GUI settings dialog (``ai_settings_dialog.py``) is the
        only writer; the workbench reads on each call so a settings
        change immediately affects the next prepare_async / classify
        invocation.
        """

        from src.services.comparison.ai_classifier import load_ai_config
        return load_ai_config()

    def _kickoff_ai_prepare_v2(self) -> None:
        """Phase I — Stage-2 임베딩 dispatcher 백그라운드 warm-up.

        QTimer.singleShot(800ms)로 main thread에서 deferred 호출. 실제
        GGUF mmap / ONNX 세션 빌드는 dispatcher.prepare_async()가 daemon
        thread에서 처리. 사용자에게는 lbl_status_v2 텍스트로 진행 표시.

        Same defensive QObject-liveness check as
        ``_prewarm_zone_render_controller_v2`` so a stale QTimer
        delivered after Workbench teardown becomes a true no-op.
        """

        try:
            _ = self.windowTitle()  # QObject liveness check
        except RuntimeError:
            return
        try:
            from src.services.comparison.ai_classifier import (
                get_embedding_dispatcher,
            )
            cfg = self._load_ai_config_v2()
            if not cfg.use_embedding:
                return  # AI 비활성 — 사용자가 명시적으로 끔
            dispatcher = get_embedding_dispatcher(cfg)
            if dispatcher.is_ready():
                return  # 다른 경로에서 이미 warm
            if hasattr(self, "lbl_status_v2"):
                try:
                    self.lbl_status_v2.setText("AI 분류기 준비 중…")
                except RuntimeError:
                    return
            self._ai_prepare_thread_v2 = dispatcher.prepare_async()
            self._ai_prepare_poll_count_v2 = 0
            QTimer.singleShot(500, self._poll_ai_prepare_v2)
        except Exception:
            logger.debug("AI prepare kickoff skipped", exc_info=True)

    def _poll_ai_prepare_v2(self) -> None:
        """500ms-interval polling for the prepare_async daemon thread.

        Updates lbl_status_v2 with one of:
          * "✓ AI 준비 완료 (backend, NNNms)" — success
          * "⚠ AI 모델 미설치 — 휴리스틱 분류만 사용" — failure
          * (still showing "준비 중…") — in flight

        Polls up to 60 times (30 seconds total) before giving up.
        """

        try:
            _ = self.windowTitle()
        except RuntimeError:
            return
        try:
            from src.services.comparison.ai_classifier import (
                get_embedding_dispatcher,
            )
            cfg = self._load_ai_config_v2()
            d = get_embedding_dispatcher(cfg)
            if d.is_ready():
                backend_id = getattr(d, "_active_backend_id", "?") or "?"
                ms = d.prepare_ms() or 0.0
                if hasattr(self, "lbl_status_v2"):
                    try:
                        self.lbl_status_v2.setText(
                            f"✓ AI 준비 완료 ({backend_id}, {ms:.0f}ms)"
                        )
                    except RuntimeError:
                        pass
                return
            if d.last_error() is not None:
                if hasattr(self, "lbl_status_v2"):
                    try:
                        self.lbl_status_v2.setText(
                            "⚠ AI 모델 미설치 — 휴리스틱 분류만 사용"
                        )
                    except RuntimeError:
                        pass
                return
            self._ai_prepare_poll_count_v2 = (
                getattr(self, "_ai_prepare_poll_count_v2", 0) + 1
            )
            if self._ai_prepare_poll_count_v2 < 60:  # 30s timeout
                QTimer.singleShot(500, self._poll_ai_prepare_v2)
        except Exception:
            logger.debug("AI prepare poll skipped", exc_info=True)

    def _show_tutorial_dialog_v2(self, *, force: bool = False) -> None:
        """Modal 5-step onboarding tutorial.

        Built with a lightweight QDialog + QStackedWidget so it stays in the
        main thread and doesn't pull in QWizard chrome (which paints a fixed
        sidebar that crowds the small steps). The user can skip at any time
        and the flag file is written when they finish or close — either way
        the wizard never auto-shows again.
        """

        from PySide6.QtWidgets import (
            QDialog,
            QStackedWidget,
            QDialogButtonBox,
            QVBoxLayout,
            QHBoxLayout,
            QLabel,
            QPushButton,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("도면 비교 시작 가이드")
        dialog.setModal(True)
        dialog.resize(680, 480)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title_label = QLabel()
        title_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #111827;")
        outer.addWidget(title_label)

        progress_label = QLabel()
        progress_label.setProperty("role", "muted")
        outer.addWidget(progress_label)

        stack = QStackedWidget()
        for _title, body in TUTORIAL_PAGES:
            page = QLabel(body)
            page.setWordWrap(True)
            page.setStyleSheet("font-size: 14px; color: #1F2937; line-height: 1.65;")
            page.setMinimumHeight(280)
            stack.addWidget(page)
        outer.addWidget(stack, stretch=1)

        buttons_row = QHBoxLayout()
        skip_btn = QPushButton("건너뛰기")
        prev_btn = QPushButton("◀ 이전")
        next_btn = QPushButton("다음 ▶")
        finish_btn = QPushButton("시작하기 ✓")
        finish_btn.setProperty("primary", "true")
        buttons_row.addWidget(skip_btn)
        buttons_row.addStretch()
        buttons_row.addWidget(prev_btn)
        buttons_row.addWidget(next_btn)
        buttons_row.addWidget(finish_btn)
        outer.addLayout(buttons_row)

        def _refresh() -> None:
            idx = stack.currentIndex()
            title, _ = TUTORIAL_PAGES[idx]
            title_label.setText(title)
            progress_label.setText(f"{idx + 1} / {len(TUTORIAL_PAGES)}")
            prev_btn.setEnabled(idx > 0)
            is_last = idx == len(TUTORIAL_PAGES) - 1
            next_btn.setVisible(not is_last)
            finish_btn.setVisible(is_last)

        prev_btn.clicked.connect(lambda: (stack.setCurrentIndex(max(0, stack.currentIndex() - 1)), _refresh()))
        next_btn.clicked.connect(lambda: (stack.setCurrentIndex(min(len(TUTORIAL_PAGES) - 1, stack.currentIndex() + 1)), _refresh()))
        skip_btn.clicked.connect(dialog.reject)
        finish_btn.clicked.connect(dialog.accept)

        _refresh()
        result = dialog.exec()
        # Write the flag whether they finished or skipped — they've seen it.
        try:
            self._tutorial_completed_path_v2().parent.mkdir(parents=True, exist_ok=True)
            self._tutorial_completed_path_v2().write_text(
                f"completed_at={datetime.now().isoformat()}\nresult={'finished' if result else 'skipped'}\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _start_new_compare_v2(self, pick: str = "") -> None:
        """Ensure the input region is expanded, optionally launch a file picker.

        Triggered by the menu bar (Ctrl+N), the toggle button when collapsed,
        or directly via Ctrl+1 / Ctrl+2 to jump straight into a file dialog.
        Without this users had no obvious way to start a *second* comparison
        once the first run auto-collapsed the input region.
        """

        if self._compact_mode_v2:
            self._set_compact_mode_v2(False)
        if pick == "source_a":
            self._browse_folder_v2("source_a")
        elif pick == "source_b":
            self._browse_folder_v2("source_b")
        elif hasattr(self, "edit_source_a_v2"):
            self.edit_source_a_v2.setFocus()
            if hasattr(self, "lbl_status_v2"):
                self.lbl_status_v2.setText("새 비교 — 변경 전/후 파일 또는 폴더를 선택하세요")

    def _install_review_hotkeys_v2(self) -> None:
        """C1 — keyboard shortcuts for rapid zone triage.

        - J / Down arrow → next zone
        - K / Up arrow → previous zone
        - 1 → 확인 (confirmed)
        - 2 → 보류 (hold)
        - 3 → 오탐 (false_positive)
        - 4 → 추가 검토 (needs_review)
        - R → reset selected zone view (fit to selected)
        - F → toggle compact mode
        """

        from PySide6.QtGui import QShortcut, QKeySequence

        def _shortcut(key: str, slot) -> None:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(slot)

        _shortcut("J", lambda: self._move_zone_selection_v2(1))
        _shortcut("K", lambda: self._move_zone_selection_v2(-1))
        _shortcut("Down", lambda: self._move_zone_selection_v2(1))
        _shortcut("Up", lambda: self._move_zone_selection_v2(-1))
        _shortcut("1", lambda: self._set_zone_review_status_v2("confirmed"))
        _shortcut("2", lambda: self._set_zone_review_status_v2("hold"))
        _shortcut("3", lambda: self._set_zone_review_status_v2("false_positive"))
        _shortcut("4", lambda: self._set_zone_review_status_v2("needs_review"))
        _shortcut("R", self._reset_zone_focus_v2)
        _shortcut("F", lambda: self._set_compact_mode_v2(not self._compact_mode_v2))
        _shortcut("Ctrl+Return", self._save_current_zone_memo_v2)
        _shortcut("Ctrl+Enter", self._save_current_zone_memo_v2)
        # Quick "new comparison" hotkey so the user never gets stuck with a
        # collapsed input region and no obvious way to start another run.
        _shortcut("N", lambda: self._start_new_compare_v2())
        _shortcut("Ctrl+N", lambda: self._start_new_compare_v2())

    def _build_brand_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("BrandHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(14)

        icon_label = QLabel()
        icon = QPixmap(str(_drawing_compare_asset_path("app_icon.png")))
        if not icon.isNull():
            icon_label.setPixmap(icon.scaled(58, 58, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon_label.setFixedWidth(64)
        layout.addWidget(icon_label)

        copy = QVBoxLayout()
        copy.setSpacing(5)
        title = QLabel(APP_TITLE_KO)
        title.setProperty("role", "brandTitle")
        subtitle = QLabel(APP_SUBTITLE_KO)
        subtitle.setProperty("role", "brandSubtitle")
        subtitle.setWordWrap(True)
        ownership = QLabel(APP_OWNERSHIP_KO)
        ownership.setProperty("role", "brandBadge")
        ownership.setMaximumWidth(310)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        copy.addWidget(ownership)
        layout.addLayout(copy, stretch=1)

        info_button = QPushButton("정보")
        info_button.setProperty("role", "quiet")
        info_button.clicked.connect(self._show_about_v2)
        layout.addWidget(info_button)

        banner_label = QLabel()
        banner = QPixmap(str(_drawing_compare_asset_path("header_banner.png")))
        if not banner.isNull():
            banner_label.setPixmap(banner.scaled(420, 126, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        banner_label.setMinimumWidth(360)
        banner_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(banner_label)
        return header

    def _input_row_v2(self, label: str, attr: str) -> QHBoxLayout:
        row = QHBoxLayout()
        label_widget = QLabel(label)
        label_widget.setMinimumWidth(118)
        row.addWidget(label_widget)
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText("DWG/DXF/PDF 파일 또는 폴더를 선택하세요")
        setattr(self, f"edit_{attr}_v2", edit)
        file_button = QPushButton("파일 선택")
        file_button.setMinimumWidth(88)
        file_button.clicked.connect(lambda: self._browse_file_v2(attr))
        folder_button = QPushButton("폴더 선택")
        folder_button.setMinimumWidth(88)
        folder_button.clicked.connect(lambda: self._browse_folder_v2(attr))
        row.addWidget(file_button)
        row.addWidget(folder_button)
        row.addWidget(edit, stretch=1)
        return row

    def _summary_card(self, title: str, value: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("Card")
        card.setMinimumHeight(70)
        layout = QVBoxLayout(card)
        label = QLabel(title)
        label.setProperty("role", "muted")
        value_label = QLabel(value)
        value_label.setStyleSheet("font-size: 20px; font-weight: 700; color: #111827;")
        layout.addWidget(label)
        layout.addWidget(value_label)
        return card, value_label

    def _build_left_panel(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_drawings_tab_v2(), "우선 검토 도면")
        tabs.addTab(self._build_top_issues_tab_v2(), "프로젝트 Top 이슈")
        tabs.addTab(self._build_pattern_groups_tab_v2(), "반복 패턴")
        return tabs

    def _build_drawings_tab_v2(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("우선 검토 도면"))
        self.cmb_drawing_filter_v2 = QComboBox()
        self.cmb_drawing_filter_v2.addItems(["전체", "큼", "보통", "작음", "변경 없음", "실패"])
        self.cmb_drawing_filter_v2.currentTextChanged.connect(self._refresh_drawing_list_v2)
        layout.addWidget(self.cmb_drawing_filter_v2)
        self.drawing_list_v2 = QListWidget()
        self.drawing_list_v2.currentItemChanged.connect(self._on_drawing_selected_v2)
        layout.addWidget(self.drawing_list_v2, stretch=1)
        self.btn_detail_match_v2 = QPushButton("상세 매칭 보기")
        self.btn_detail_match_v2.setText("Detail Region Matching")
        self.btn_detail_match_v2.setToolTip(
            "Review detected detail regions, whole_modelspace fallbacks, review gates, and "
            "manual_region_matches.json overrides."
        )
        self.btn_detail_match_v2.clicked.connect(self._show_region_match_dialog_v2)
        self.btn_detail_match_v2.setEnabled(False)
        layout.addWidget(self.btn_detail_match_v2)
        return panel

    def _build_top_issues_tab_v2(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("프로젝트 Top 이슈 - 더블클릭하면 해당 도면/변경구역으로 이동합니다."))
        self.top_issues_list_v2 = QListWidget()
        self.top_issues_list_v2.itemActivated.connect(self._on_top_issue_activated_v2)
        layout.addWidget(self.top_issues_list_v2, stretch=1)
        self.lbl_top_issues_empty_v2 = QLabel("비교 실행 후 우선 검토 이슈가 표시됩니다.")
        self.lbl_top_issues_empty_v2.setProperty("role", "muted")
        self.lbl_top_issues_empty_v2.setWordWrap(True)
        layout.addWidget(self.lbl_top_issues_empty_v2)
        return panel

    def _build_pattern_groups_tab_v2(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("반복 패턴 - 더블클릭하면 해당 패턴의 변경구역만 필터링합니다."))
        self.pattern_group_list_v2 = QListWidget()
        self.pattern_group_list_v2.itemActivated.connect(self._on_pattern_group_activated_v2)
        layout.addWidget(self.pattern_group_list_v2, stretch=1)
        button_row = QHBoxLayout()
        self.btn_clear_pattern_filter_v2 = QPushButton("패턴 필터 해제")
        self.btn_clear_pattern_filter_v2.clicked.connect(self._clear_pattern_filter_v2)
        self.btn_clear_pattern_filter_v2.setEnabled(False)
        button_row.addStretch()
        button_row.addWidget(self.btn_clear_pattern_filter_v2)
        layout.addLayout(button_row)
        self.lbl_pattern_groups_empty_v2 = QLabel("비교 실행 후 반복 패턴이 표시됩니다.")
        self.lbl_pattern_groups_empty_v2.setProperty("role", "muted")
        self.lbl_pattern_groups_empty_v2.setWordWrap(True)
        layout.addWidget(self.lbl_pattern_groups_empty_v2)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        layout = QVBoxLayout(panel)
        header_row = QHBoxLayout()
        header = QLabel("도면 미리보기")
        header.setStyleSheet("font-size: 16px; font-weight: 700;")
        header_row.addWidget(header)
        header_row.addStretch()
        # C2 — viewer zoom controls so the user can quickly snap back to fit
        # or read off the current zoom level. R key shortcut also resets the
        # current zone view.
        from PySide6.QtWidgets import QSlider  # local import to keep top imports tidy
        self.btn_fit_view_v2 = QPushButton("전체 보기")
        self.btn_fit_view_v2.setToolTip("도면 전체가 뷰어에 맞도록 줌을 초기화합니다 (단축키 F는 입력 영역 토글이라 화면 더블클릭 또는 이 버튼 사용).")
        self.btn_fit_view_v2.clicked.connect(self._on_fit_view_v2)
        header_row.addWidget(self.btn_fit_view_v2)
        self.btn_reset_zoom_v2 = QPushButton("100%")
        self.btn_reset_zoom_v2.setToolTip("줌을 100%로 초기화합니다.")
        self.btn_reset_zoom_v2.clicked.connect(self._on_reset_zoom_v2)
        header_row.addWidget(self.btn_reset_zoom_v2)
        zoom_label = QLabel("줌")
        zoom_label.setProperty("role", "muted")
        header_row.addWidget(zoom_label)
        self.sld_zoom_v2 = QSlider(Qt.Horizontal)
        self.sld_zoom_v2.setRange(20, 800)  # 20%–800% (0.2x–8.0x)
        self.sld_zoom_v2.setValue(100)
        # Responsive width — let the slider grow with the header row instead
        # of locking it to 160 px (clipped the right-hand controls on
        # sub-1366 monitors).
        self.sld_zoom_v2.setMinimumWidth(120)
        self.sld_zoom_v2.setMaximumWidth(280)
        self.sld_zoom_v2.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.sld_zoom_v2.setToolTip("좌우로 이동하면 양쪽 뷰어의 줌이 동기화됩니다 (마우스 휠 + 드래그도 사용 가능).")
        self.sld_zoom_v2.valueChanged.connect(self._on_zoom_slider_changed_v2)
        header_row.addWidget(self.sld_zoom_v2, 1)
        self.lbl_zoom_value_v2 = QLabel("100%")
        self.lbl_zoom_value_v2.setFixedWidth(56)
        header_row.addWidget(self.lbl_zoom_value_v2)
        # B1 — overlay opacity slider so the user can see through the markers
        # to read coordinates and small features behind the cloud + focus.
        # Slider 30..100 maps to opacity scale 0.30..1.00 (clamped in viewport).
        opacity_label = QLabel("투명도")
        opacity_label.setProperty("role", "muted")
        header_row.addWidget(opacity_label)
        self.sld_overlay_opacity_v2 = QSlider(Qt.Horizontal)
        self.sld_overlay_opacity_v2.setRange(30, 100)
        self.sld_overlay_opacity_v2.setValue(100)
        # Responsive width — same rationale as the zoom slider above.
        self.sld_overlay_opacity_v2.setMinimumWidth(100)
        self.sld_overlay_opacity_v2.setMaximumWidth(240)
        self.sld_overlay_opacity_v2.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.sld_overlay_opacity_v2.setToolTip("좌측으로 이동하면 변경점 마커가 흐려져 가려진 좌표 정보가 잘 보입니다.")
        self.sld_overlay_opacity_v2.valueChanged.connect(self._on_overlay_opacity_changed_v2)
        header_row.addWidget(self.sld_overlay_opacity_v2, 1)
        self.lbl_overlay_opacity_value_v2 = QLabel("100%")
        self.lbl_overlay_opacity_value_v2.setFixedWidth(48)
        header_row.addWidget(self.lbl_overlay_opacity_value_v2)
        layout.addLayout(header_row)

        # Phase H + multi-page navigation — surfaced only when the
        # active viewer pair has more than one Phase-H-matched page
        # pair. Lets the user step between matched pages of a multi-
        # page PDF (e.g. A.page2 ↔ B.page5 → A.page0 ↔ B.page2 → ...).
        # All hidden by default; ``_update_page_nav_v2`` toggles them.
        page_nav_row = QHBoxLayout()
        self.btn_page_nav_prev_v2 = QPushButton("◀ 이전 페이지")
        self.btn_page_nav_prev_v2.setToolTip(
            "이전 매칭된 페이지 쌍으로 이동 (Phase H 멀티페이지 PDF)"
        )
        self.btn_page_nav_prev_v2.clicked.connect(
            lambda: self._step_pdf_page_pair_v2(-1)
        )
        self.btn_page_nav_prev_v2.setVisible(False)
        page_nav_row.addWidget(self.btn_page_nav_prev_v2)

        self.lbl_page_nav_v2 = QLabel("")
        self.lbl_page_nav_v2.setProperty("role", "muted")
        self.lbl_page_nav_v2.setVisible(False)
        page_nav_row.addWidget(self.lbl_page_nav_v2, stretch=1)

        self.btn_page_nav_next_v2 = QPushButton("다음 페이지 ▶")
        self.btn_page_nav_next_v2.setToolTip(
            "다음 매칭된 페이지 쌍으로 이동"
        )
        self.btn_page_nav_next_v2.clicked.connect(
            lambda: self._step_pdf_page_pair_v2(+1)
        )
        self.btn_page_nav_next_v2.setVisible(False)
        page_nav_row.addWidget(self.btn_page_nav_next_v2)
        layout.addLayout(page_nav_row)
        # State — current index into the active pair's page_match_pairs
        # list. Reset on _on_drawing_selected_v2.
        self._active_pdf_page_index_v2: int = 0

        views = QSplitter(Qt.Horizontal)
        before_box = QWidget()
        before_layout = QVBoxLayout(before_box)
        before_layout.addWidget(QLabel("변경 전"))
        self.preview_before_v2 = GpuDrawingViewport()
        self.preview_before_v2.setVisible(not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY)
        before_layout.addWidget(self.preview_before_v2)
        # Phase G2.2 — lightweight viewport (hidden by default; toggled
        # via Ctrl+L from the View menu). Created up-front so the toggle
        # action is instant and the layout doesn't shift.
        if QT_QUICK_AVAILABLE and QQuickWidget is not None:
            from src.gui.lightweight_viewport import LightweightDrawingViewport

            self.preview_before_lightweight_v2 = LightweightDrawingViewport(side="before")
        else:
            self.preview_before_lightweight_v2 = QtQuickUnavailableLightweightViewport(side="before")
        self.preview_before_lightweight_v2.setVisible(DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY)
        # Phase G2.3 — camera sync: when the user pans/zooms one side,
        # mirror the camera state to the other so before/after stay aligned
        # in the same world coords. The flag stops infinite recursion.
        self._lightweight_camera_sync_in_progress = False
        self.preview_before_lightweight_v2.viewportChanged.connect(
            lambda cx, cy, upp: self._on_lightweight_camera_changed_v2("before", cx, cy, upp)
        )
        before_layout.addWidget(self.preview_before_lightweight_v2)

        after_box = QWidget()
        after_layout = QVBoxLayout(after_box)
        after_layout.addWidget(QLabel("변경 후"))
        self.preview_after_v2 = GpuDrawingViewport()
        self.preview_after_v2.setVisible(not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY)
        after_layout.addWidget(self.preview_after_v2)
        if QT_QUICK_AVAILABLE and QQuickWidget is not None:
            self.preview_after_lightweight_v2 = LightweightDrawingViewport(side="after")
        else:
            self.preview_after_lightweight_v2 = QtQuickUnavailableLightweightViewport(side="after")
        self.preview_after_lightweight_v2.setVisible(DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY)
        self.preview_after_lightweight_v2.viewportChanged.connect(
            lambda cx, cy, upp: self._on_lightweight_camera_changed_v2("after", cx, cy, upp)
        )
        after_layout.addWidget(self.preview_after_lightweight_v2)
        from src.gui.failure_badge import FailureBadge, collect_viewport_failure_codes
        self.failure_badge = FailureBadge(self)
        self.statusBar().addPermanentWidget(self.failure_badge)
        self.failure_badge.set_failure_codes(collect_viewport_failure_codes(self.preview_before_lightweight_v2, self.preview_after_lightweight_v2))

        views.addWidget(before_box)
        views.addWidget(after_box)
        self.preview_before_v2.viewportChanged.connect(
            lambda zoom, pan_x, pan_y: self._sync_preview_viewport_v2(self.preview_after_v2, zoom, pan_x, pan_y)
        )
        self.preview_after_v2.viewportChanged.connect(
            lambda zoom, pan_x, pan_y: self._sync_preview_viewport_v2(self.preview_before_v2, zoom, pan_x, pan_y)
        )
        self.preview_before_v2.tileWindowMissing.connect(self._schedule_visible_tile_window_v2)
        self.preview_after_v2.tileWindowMissing.connect(self._schedule_visible_tile_window_v2)
        # Phase I4 — viewer-side overlay click → list auto-select. Both
        # legacy GPU viewports and lightweight viewports forward the QML
        # overlayClicked signal so a click on either side selects the
        # zone in the tree (which already auto-expands its category +
        # cluster ancestors via _select_zone_in_list_v2).
        for vp in (
            self.preview_before_v2, self.preview_after_v2,
            self.preview_before_lightweight_v2, self.preview_after_lightweight_v2,
        ):
            try:
                vp.overlayClicked.connect(self._on_viewer_overlay_clicked_v2)
            except Exception:
                logger.debug(
                    "Viewport %r has no overlayClicked signal — skipping",
                    type(vp).__name__,
                )
        layout.addWidget(views, stretch=1)
        return panel

    def _on_viewer_overlay_clicked_v2(self, zone_id: str) -> None:
        """Phase I4 — Bidirectional sync: viewer marker click → list select.

        ``_select_zone_in_list_v2`` already handles the tree-aware
        cascade: locate the leaf, expand its parent (cluster) and
        grandparent (category), scroll into view, fire selection. From
        here we just forward the zone_id and let that helper do the rest.
        Selection then triggers ``_on_zone_selected_v2`` via the
        ``currentItemChanged`` signal, refocusing both viewports on the
        clicked zone — closing the loop.
        """

        zid = str(zone_id or "").strip()
        if not zid:
            return
        # Avoid feedback loop: if the zone is already selected, no-op.
        if zid == str(self._active_zone_id or ""):
            return
        logger.debug("Viewer overlay clicked: zone_id=%s", zid)
        self._select_zone_in_list_v2(zid)

    def _on_compact_toggle_v2(self, checked: bool) -> None:
        """Manual toggle from the user — collapse/expand the input/summary region."""

        self._set_compact_mode_v2(bool(checked))

    def _set_compact_mode_v2(self, enabled: bool) -> None:
        """Hide or show the input/summary header region above the splitter.

        When enabled, the viewer/detail splitter expands to fill almost the
        entire window (apart from the brand header and the toggle row).
        """

        if not hasattr(self, "header_region_v2"):
            return
        self._compact_mode_v2 = bool(enabled)
        self.header_region_v2.setVisible(not self._compact_mode_v2)
        if hasattr(self, "btn_compact_v2"):
            label = (
                "📁 ▼ 새 파일/폴더 선택하려면 클릭 (또는 Ctrl+N)"
                if self._compact_mode_v2
                else "▲ 입력 영역 접기 (뷰어 최대화)"
            )
            self.btn_compact_v2.setText(label)
            # When collapsed, paint the toggle as the primary action so the
            # user immediately sees where to click for a new comparison.
            self.btn_compact_v2.setProperty("primary", "true" if self._compact_mode_v2 else "false")
            # Re-polish so the property change actually re-styles the button
            style = self.btn_compact_v2.style()
            if style is not None:
                style.unpolish(self.btn_compact_v2)
                style.polish(self.btn_compact_v2)
            # Block the toggled signal while syncing button state programmatically
            self.btn_compact_v2.blockSignals(True)
            self.btn_compact_v2.setChecked(self._compact_mode_v2)
            self.btn_compact_v2.blockSignals(False)

    def _on_overlay_opacity_changed_v2(self, value: int) -> None:
        """Apply slider-driven overlay opacity to every preview viewport.

        Both the legacy GPU viewports and the active lightweight viewports
        expose ``set_overlay_opacity_scale``; drive all four so the slider
        works regardless of which viewer is currently shown.
        """

        scale = max(30, min(100, int(value))) / 100.0
        for viewport in (
            getattr(self, "preview_before_v2", None),
            getattr(self, "preview_after_v2", None),
            getattr(self, "preview_before_lightweight_v2", None),
            getattr(self, "preview_after_lightweight_v2", None),
        ):
            if viewport is not None:
                viewport.set_overlay_opacity_scale(scale)
        if hasattr(self, "lbl_overlay_opacity_value_v2"):
            self.lbl_overlay_opacity_value_v2.setText(f"{int(round(scale * 100))}%")

    def _report_settings_path_v2(self) -> Path:
        return _workbench_data_dir() / REPORT_SETTINGS_FILENAME

    def _load_report_settings_v2(self) -> ReportSettings:
        return load_report_settings(self._report_settings_path_v2())

    def _save_report_settings_v2(self, settings: ReportSettings) -> None:
        save_report_settings(self._report_settings_path_v2(), settings)

    def _show_ai_settings_dialog_v2(self) -> None:
        """Phase J Step 3 (J1) — open the AI classifier settings dialog.

        Loads the current config (via ``_load_ai_config_v2`` →
        ``load_ai_config()``), opens the modal dialog, and on Accept:
          1. The dialog has already saved ai_config.json atomically
          2. Clear the dispatcher singleton cache so the new mode
             takes effect on the next classification
          3. Re-trigger ``_kickoff_ai_prepare_v2`` so the user sees
             the updated status in lbl_status_v2 without needing to
             restart the Workbench

        On Cancel: no-op (no config write, no dispatcher disturbance).
        """

        from src.gui.ai_settings_dialog import AiSettingsDialog
        # Phase L3 review fix: clear ALL three dispatcher caches, not
        # just embedding. Without llm + RAG cache invalidation, a user
        # switching LLM backend / RAG client gets the OLD cached
        # dispatcher on the next classification batch (silent revert).
        from src.services.comparison.ai_classifier import (
            clear_dispatcher_cache,
            clear_llm_dispatcher_cache,
        )

        try:
            current = self._load_ai_config_v2()
            dialog = AiSettingsDialog(current, parent=self)
        except Exception:
            logger.exception("Could not open AI settings dialog")
            return

        if dialog.exec() != dialog.DialogCode.Accepted:
            return  # Cancel

        # Force fresh dispatcher pickup of the new config + re-warm
        try:
            clear_dispatcher_cache()
            clear_llm_dispatcher_cache()
            # Phase N review fix: also invalidate the per-pair
            # category cache. Without this, pairs already classified
            # under the old config keep their old categories until
            # the user closes + re-opens the pair. With the cascade
            # now actually wired (Phase N), this cache is no longer
            # config-independent — it MUST be cleared on settings
            # change so the next pair selection re-classifies under
            # the new config.
            if hasattr(self, "_zone_categories_v2"):
                self._zone_categories_v2.clear()
            if hasattr(self, "lbl_status_v2"):
                self.lbl_status_v2.setText(
                    "설정 변경 — AI 분류기 재준비 중…"
                )
            # Kick off background warmup with new mode
            QTimer.singleShot(0, self._kickoff_ai_prepare_v2)
        except Exception:
            logger.exception("Could not re-prepare AI dispatcher after settings change")

    def _show_noise_filter_dialog_v2(self) -> None:
        """Phase O — open the noise filter settings dialog.

        Loads the current settings from noise_filter_config.json,
        opens the modal dialog, and on Accept the dialog has already
        atomically persisted the new values. The Workbench picks them
        up the next time it builds a comparison job — there's no
        in-process cache to invalidate (unlike the AI dispatcher),
        so the only side effect here is a status-bar acknowledgement.

        On Cancel: no-op.
        """
        from src.gui.noise_filter_dialog import NoiseFilterDialog
        from src.services.comparison.noise_filter_io import (
            load_noise_filter_settings,
        )

        try:
            current = load_noise_filter_settings()
            dialog = NoiseFilterDialog(current, parent=self)
        except Exception:
            logger.exception("Could not open noise filter dialog")
            return

        result = dialog.exec()
        # RV-20260508-001 #7 — schedule explicit Qt cleanup so the
        # dialog's child QObjects are reaped on the next event loop
        # tick rather than waiting for parent (the workbench) to
        # destruct. Prevents PySide6 ``RuntimeError: Internal C++
        # object ... deleted`` during fast test/teardown sequences.
        dialog.deleteLater()
        if result != dialog.DialogCode.Accepted:
            return

        if hasattr(self, "lbl_status_v2"):
            self.lbl_status_v2.setText(
                "노이즈 필터 설정 저장 — 다음 비교 실행부터 적용됩니다."
            )

    def _show_report_settings_dialog_v2(self) -> None:
        """Modal form to edit reviewer info + company branding for the PDF.

        Persists to ``report_settings.json`` under the Workbench data dir so
        the same profile applies to every future export. The form deliberately
        keeps fields short — defaults remain blank when not specified, and
        the PDF gracefully omits sections that aren't configured.
        """

        from PySide6.QtWidgets import (
            QDialog,
            QFormLayout,
            QLineEdit,
            QFileDialog,
            QPushButton,
            QHBoxLayout,
            QVBoxLayout,
            QDialogButtonBox,
            QLabel,
            QColorDialog,
        )
        from PySide6.QtGui import QColor

        settings = self._load_report_settings_v2()
        dialog = QDialog(self)
        dialog.setWindowTitle("보고서 설정")
        dialog.setModal(True)
        dialog.resize(620, 540)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(20, 16, 20, 12)

        intro = QLabel(
            "검토 보고서 PDF의 브랜딩과 검토자 정보입니다. 빈 항목은 PDF에서 자동 생략됩니다."
        )
        intro.setProperty("role", "muted")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        company_edit = QLineEdit(settings.company_name)
        form.addRow("회사명", company_edit)

        # Logo path picker
        logo_row = QHBoxLayout()
        logo_edit = QLineEdit(settings.company_logo_path)
        logo_browse = QPushButton("이미지 선택...")

        def _pick_logo():
            path, _ = QFileDialog.getOpenFileName(
                dialog,
                "회사 로고 이미지 선택",
                "",
                "이미지 파일 (*.png *.jpg *.jpeg *.bmp);;모든 파일 (*.*)",
            )
            if path:
                logo_edit.setText(path)

        logo_browse.clicked.connect(_pick_logo)
        logo_row.addWidget(logo_edit, stretch=1)
        logo_row.addWidget(logo_browse)
        form.addRow("회사 로고", logo_row)

        reviewer_edit = QLineEdit(settings.reviewer_name)
        form.addRow("검토자 이름", reviewer_edit)
        title_edit = QLineEdit(settings.reviewer_title)
        form.addRow("직급", title_edit)
        dept_edit = QLineEdit(settings.reviewer_department)
        form.addRow("부서", dept_edit)
        contact_edit = QLineEdit(settings.reviewer_contact)
        form.addRow("연락처 (이메일/전화)", contact_edit)

        # Signature/stamp picker
        sig_row = QHBoxLayout()
        sig_edit = QLineEdit(settings.reviewer_signature_path)
        sig_browse = QPushButton("이미지 선택...")

        def _pick_sig():
            path, _ = QFileDialog.getOpenFileName(
                dialog,
                "검토자 도장/사인 이미지 선택",
                "",
                "이미지 파일 (*.png *.jpg *.jpeg *.bmp);;모든 파일 (*.*)",
            )
            if path:
                sig_edit.setText(path)

        sig_browse.clicked.connect(_pick_sig)
        sig_row.addWidget(sig_edit, stretch=1)
        sig_row.addWidget(sig_browse)
        form.addRow("도장/사인 이미지", sig_row)

        # Accent color picker
        color_row = QHBoxLayout()
        color_edit = QLineEdit(settings.accent_color_hex)
        color_swatch = QPushButton(" ")
        color_swatch.setFixedWidth(36)
        color_swatch.setStyleSheet(f"background-color: {settings.accent_color_hex};")

        def _pick_color():
            initial = QColor(color_edit.text() or "#DC2626")
            chosen = QColorDialog.getColor(initial, dialog, "강조 색상 선택")
            if chosen.isValid():
                hex_text = chosen.name()
                color_edit.setText(hex_text)
                color_swatch.setStyleSheet(f"background-color: {hex_text};")

        color_swatch.clicked.connect(_pick_color)
        color_row.addWidget(color_edit, stretch=1)
        color_row.addWidget(color_swatch)
        form.addRow("강조 색상 (hex)", color_row)

        footer_edit = QLineEdit(settings.footer_note)
        form.addRow("푸터 보충 (계약번호 등)", footer_edit)

        outer.addLayout(form)

        button_box = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        button_box.button(QDialogButtonBox.Save).setText("저장")
        button_box.button(QDialogButtonBox.Cancel).setText("취소")
        outer.addWidget(button_box)

        def _accept():
            new_settings = ReportSettings(
                company_name=company_edit.text().strip() or settings.company_name,
                company_logo_path=logo_edit.text().strip(),
                reviewer_name=reviewer_edit.text().strip(),
                reviewer_title=title_edit.text().strip(),
                reviewer_department=dept_edit.text().strip(),
                reviewer_contact=contact_edit.text().strip(),
                reviewer_signature_path=sig_edit.text().strip(),
                accent_color_hex=color_edit.text().strip() or "#DC2626",
                footer_note=footer_edit.text().strip(),
            )
            try:
                self._save_report_settings_v2(new_settings)
            except Exception as exc:
                QMessageBox.warning(dialog, "보고서 설정", f"저장 실패:\n{exc}")
                return
            dialog.accept()
            QMessageBox.information(
                self,
                "보고서 설정",
                "저장 완료. 다음 보고서 생성부터 적용됩니다.",
            )

        button_box.accepted.connect(_accept)
        button_box.rejected.connect(dialog.reject)

        dialog.exec()

    def _export_review_report_pdf_v2(self) -> None:
        """QW3 — generate the single-PDF review report (cover + cloud + memos)."""

        if not self._result:
            QMessageBox.information(self, "검토 보고서 PDF", "먼저 비교를 실행한 뒤 변경구역을 검토하세요.")
            return
        artifact_dir = Path(self._result.artifact_dir)
        confirmed_cloud_dir = artifact_dir / "confirmed_clouds"
        # If no confirmed clouds yet, prompt the user to generate them first
        # so the per-pair pages have something to embed.
        if not confirmed_cloud_dir.exists() or not any(confirmed_cloud_dir.glob("*_confirmed.png")):
            answer = QMessageBox.question(
                self,
                "검토 보고서 PDF",
                "확인된 변경 구름마크가 아직 추출되지 않았습니다.\n"
                "지금 일괄 추출하고 보고서를 만들까요? (취소하면 보고서가 빈 cloud로 생성됩니다.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._export_confirmed_cloud_marks_v2(all_pairs=True)
        # Gather inputs
        overlays_by_pair: dict[str, list[dict]] = {}
        for pair_id in self._viewer_pairs_by_id:
            try:
                overlays_by_pair[pair_id] = self._viewer_overlays_for_pair_v2(pair_id) or []
            except Exception:
                overlays_by_pair[pair_id] = []
        request = self._result.request
        # Load (or initialise) the report branding/reviewer settings — applied
        # to cover, sign-off, and footer of every PDF generated.
        settings = self._load_report_settings_v2()
        inputs = ReviewReportInput(
            project_label=Path(str(request.source_b)).name or "도면 변경 비교",
            run_started_at=getattr(self._result, "started_at", "") or "",
            source_a=str(request.source_a),
            source_b=str(request.source_b),
            drawing_rows=list(self._drawing_rows or []),
            review_records=dict(self._review_records_v2),
            confirmed_cloud_dir=confirmed_cloud_dir if confirmed_cloud_dir.exists() else None,
            overlays_by_pair=overlays_by_pair,
            settings=settings,
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = artifact_dir / f"review_report_{timestamp}.pdf"
        try:
            result = generate_review_report_pdf(inputs=inputs, output_path=output_path)
        except Exception as exc:
            logger.exception("Review report PDF generation failed")
            QMessageBox.critical(
                self,
                "검토 보고서 PDF",
                f"보고서 생성 중 오류가 발생했습니다.\n\n{exc}",
            )
            return
        QMessageBox.information(
            self,
            "검토 보고서 PDF",
            f"보고서 생성 완료 ({result.page_count} 페이지)\n\n"
            f"확인 {result.confirmed_total} · 보류 {result.ignored_total} · "
            f"오탐 {result.false_positive_total} · 미검토 {result.needs_review_total}\n\n"
            f"저장 위치: {output_path}",
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_path.resolve())))
        if hasattr(self, "lbl_status_v2"):
            self.lbl_status_v2.setText(f"검토 보고서 PDF 생성 완료 ({result.page_count}쪽) — 열기")

    def _export_confirmed_cloud_marks_v2(self, all_pairs: bool = False) -> None:
        """Export PNG with cloud marks drawn ONLY around confirmed change zones.

        Default: current pair only. ``all_pairs=True`` walks every pair in the
        viewer manifest and emits one PNG per pair under
        ``<run>/artifacts/confirmed_clouds/``. The reviewer's ``confirmed``
        marks come from ``review_state.json`` (already maintained by the
        confirm/ignore/false-positive buttons + 1/2/3 hotkeys).
        """

        if not self._result:
            QMessageBox.information(self, "확인된 변경 구름마크", "먼저 비교를 실행한 뒤 변경구역을 검토하세요.")
            return
        artifact_dir = Path(self._result.artifact_dir) / "confirmed_clouds"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        results: list[ConfirmedCloudExportResult] = []
        if all_pairs:
            target_pairs = list(self._viewer_pairs_by_id.items())
        else:
            pair_id = str((self._active_row or {}).get("pair_id") or "")
            if not pair_id:
                QMessageBox.information(self, "확인된 변경 구름마크", "먼저 도면 목록에서 도면을 선택하세요.")
                return
            target_pairs = [(pair_id, self._viewer_pairs_by_id.get(pair_id, {}))]

        for pair_id, viewer_pair in target_pairs:
            overlays = self._viewer_overlays_for_pair_v2(pair_id) or list(
                self._active_overlays_by_zone.values()
                if pair_id == str((self._active_row or {}).get("pair_id") or "")
                else []
            )
            after_image_path = _resolve_viewer_artifact_path(
                viewer_pair.get("after_image"), self._viewer_root,
            )
            after_image = str(after_image_path) if after_image_path else ""
            is_pdf = _viewer_pair_is_pdf(viewer_pair)
            # G2.7-COORDFIX — pass the rendered PNG's DPI so the export
            # can scale image_pixels overlays from pdf_dpi (200) up to
            # preview_dpi (typically 400 in "구조도면 정밀" mode).
            after_transform = viewer_pair.get("after_transform") or {}
            after_image_dpi = (
                float(after_transform.get("dpi") or 0)
                if isinstance(after_transform, dict) else 0.0
            )
            try:
                result = export_confirmed_cloud_marks(
                    pair_id=pair_id,
                    after_image_path=after_image,
                    overlays=overlays,
                    review_records=self._review_records_v2,
                    output_dir=artifact_dir,
                    is_pdf_pair=is_pdf,
                    image_dpi=after_image_dpi,
                )
            except Exception as exc:
                logger.exception("Confirmed cloud export failed for %s", pair_id)
                result = ConfirmedCloudExportResult(
                    pair_id=pair_id,
                    output_path="",
                    confirmed_zone_count=0,
                    skipped_reason=f"내부 오류: {exc}",
                    is_pdf=is_pdf,
                )
            results.append(result)

            # Phase I — Also export a CAD-importable DXF for PDF pairs.
            # PNG cloud overlay (above) is the human-readable artifact;
            # the DXF lets a CAD professional drop it as a reference
            # layer over their working drawing for context. Skipped for
            # CAD pairs (those already have the after_marked.dxf path).
            if is_pdf and result.confirmed_zone_count > 0:
                try:
                    from src.services.comparison.pdf_cloud_dxf_export import (
                        export_cloud_marks_to_dxf,
                    )
                    # The PDF source side — prefer "after" as that's the
                    # revision the cloud refers to, fall back to before.
                    pdf_source = (
                        viewer_pair.get("source_b")
                        or viewer_pair.get("source_a")
                        or ""
                    )
                    pdf_pdf_dpi = float(
                        viewer_pair.get("compare_pdf_dpi")
                        or after_transform.get("pdf_dpi")
                        or 200.0
                    ) if isinstance(after_transform, dict) else 200.0
                    page_index = int(viewer_pair.get("page_b") or 0)
                    # 2nd-review fix (P0): pin allowed_output_root to
                    # the workbench's session artifact root so the
                    # security_validators check accepts the path.
                    dxf_result = export_cloud_marks_to_dxf(
                        pair_id=pair_id,
                        overlays=overlays,
                        review_records=self._review_records_v2,
                        output_dir=artifact_dir,
                        pdf_path=pdf_source,
                        pdf_dpi=pdf_pdf_dpi,
                        page_index=page_index,
                        allowed_output_root=Path(artifact_dir).parent,
                    )
                    if dxf_result.output_path:
                        logger.info(
                            "PDF cloud DXF export: %s zones → %s",
                            dxf_result.confirmed_zone_count,
                            dxf_result.output_path,
                        )
                except Exception:
                    logger.exception("PDF cloud DXF export failed for %s", pair_id)

        produced = [r for r in results if r.output_path]
        skipped = [r for r in results if not r.output_path]
        message_lines: list[str] = []
        if produced:
            message_lines.append(f"확인된 변경 구름마크 {len(produced)}개 도면 추출 완료:")
            for r in produced[:5]:
                message_lines.append(f"  • {r.pair_id} — {r.confirmed_zone_count} 변경 → {Path(r.output_path).name}")
            if len(produced) > 5:
                message_lines.append(f"  • ... 외 {len(produced) - 5}개")
        if skipped:
            if produced:
                message_lines.append("")
            message_lines.append(f"건너뛴 도면 {len(skipped)}개:")
            for r in skipped[:5]:
                message_lines.append(f"  • {r.pair_id} — {r.skipped_reason}")
            if len(skipped) > 5:
                message_lines.append(f"  • ... 외 {len(skipped) - 5}개")

        if produced:
            message_lines.append("")
            message_lines.append(f"저장 위치: {artifact_dir}")

        QMessageBox.information(self, "확인된 변경 구름마크", "\n".join(message_lines) or "내보낼 결과가 없습니다.")

        if produced:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(artifact_dir.resolve())))
            if hasattr(self, "lbl_status_v2"):
                self.lbl_status_v2.setText(f"확인된 구름마크 {len(produced)}개 PNG 생성 완료 — 폴더 자동 열기")

    def _save_current_zone_memo_v2(self) -> None:
        """C4 — persist the editor content as the active zone's memo.

        Reuses ReviewStateRecord.note so the timeline of edits also surfaces in
        the existing review_state.json artifact (each save updates ``updated_at``).
        """

        pair_id = str((self._active_row or {}).get("pair_id") or "")
        zone_id = str(self._active_zone_id or "")
        if not pair_id or not zone_id or not hasattr(self, "zone_memo_v2"):
            return
        if self._review_state_path_v2 is None:
            if not self._result:
                return
            self._review_state_path_v2 = Path(self._result.review_state_path)
        memo_text = self.zone_memo_v2.toPlainText().strip()
        existing = self._review_records_v2.get(
            ReviewStateRecord(
                pair_id=pair_id,
                pair_uuid=str((self._active_row or {}).get("pair_uuid") or pair_id),
                zone_id=zone_id,
                status="needs_review",
                note="",
            ).key
        )
        status = existing.status if existing else "needs_review"
        record = ReviewStateRecord(
            pair_id=pair_id,
            pair_uuid=str((self._active_row or {}).get("pair_uuid") or pair_id),
            zone_id=zone_id,
            status=status,
            note=memo_text,
            updated_at=datetime.now().isoformat(),
        )
        self._review_records_v2[record.key] = record
        save_review_state(self._review_state_path_v2, self._review_records_v2)
        if hasattr(self, "lbl_status_v2"):
            self.lbl_status_v2.setText(f"메모 저장 완료 ({zone_id})")

    def _load_current_zone_memo_v2(self) -> None:
        """Populate the memo editor with the saved note for the active zone."""

        if not hasattr(self, "zone_memo_v2"):
            return
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        zone_id = str(self._active_zone_id or "")
        if not pair_id or not zone_id:
            self.zone_memo_v2.clear()
            self.btn_save_memo_v2.setEnabled(False)
            return
        record_key = ReviewStateRecord(
            pair_id=pair_id,
            pair_uuid=str((self._active_row or {}).get("pair_uuid") or pair_id),
            zone_id=zone_id,
            status="needs_review",
            note="",
        ).key
        existing = self._review_records_v2.get(record_key)
        # Workbench V2 default note is "Workbench V2: <status_ko>" — show empty
        # editor in that case so the user types a real comment instead of editing
        # boilerplate.
        memo = ""
        if existing and existing.note and not existing.note.startswith("Workbench V2:"):
            memo = existing.note
        self.zone_memo_v2.setPlainText(memo)
        self.btn_save_memo_v2.setEnabled(True)

    def _missing_category_overlays_for_pair_v2(self, pair_id: str, overlays: list[dict]) -> list[dict]:
        existing_results = self._zone_categories_v2.setdefault(pair_id, {})
        missing_overlays: list[dict] = []
        for overlay in overlays or []:
            if not isinstance(overlay, dict):
                continue
            zone_id = str(overlay.get("zone_id") or overlay.get("id") or "")
            if zone_id and zone_id not in existing_results:
                missing_overlays.append(overlay)
        return missing_overlays

    def _zone_category_issues_by_zone_v2(self) -> dict[str, dict]:
        return {
            str(issue.get("zone_id") or ""): issue
            for issue in ((self._active_row or {}).get("top_issues") or [])
            if isinstance(issue, dict) and issue.get("zone_id")
        }

    def _classify_zone_category_record_v2(
        self,
        pair_id: str,
        overlay: dict,
        *,
        cfg: object,
        issues_by_zone: dict[str, dict],
    ) -> bool:
        existing_results = self._zone_categories_v2.setdefault(pair_id, {})
        zone_id = str(overlay.get("zone_id") or overlay.get("id") or "")
        if not zone_id or zone_id in existing_results:
            return False
        merged = dict(overlay)
        issue = issues_by_zone.get(zone_id)
        if isinstance(issue, dict):
            merged.update({k: v for k, v in issue.items() if v is not None})
        try:
            existing_results[zone_id] = classify_zone_with_cascade(merged, config=cfg)
        except Exception:  # noqa: BLE001
            logger.exception(
                "classify_zone_with_cascade crashed for zone %s — "
                "falling back to heuristic", zone_id,
            )
            existing_results[zone_id] = classify_zone(merged)
        return True

    def _compute_zone_categories_for_pair_v2(
        self,
        pair_id: str,
        overlays: list[dict],
        *,
        max_records: Optional[int] = None,
    ) -> int:
        """E2 + Phase N — zone classification cache for the active pair.

        Routes through ``classify_zone_with_cascade(zone, cfg)`` so that:
          * Heuristic-only users (use_embedding=False AND use_llm=False) →
            same fast path as before (zone_classifier.classify_zone)
          * Users with embedding / LLM / RAG enabled → cascade results
            (Stage-2 / Stage-3) adapted into ZoneCategoryResult so the
            existing UI panels keep working without schema changes

        Phase N (2026-05-07) review fix: previously this method called
        the heuristic ``classify_zone`` directly, bypassing the entire
        Phase H/I/J/K/L AI cascade. Users could enable Quality / LLM /
        RAG modes in the settings dialog but never saw any difference
        in the actual zone classifications because the cascade was
        wired to nothing in real comparison flow.

        Combines overlay records with the dashboard's ``top_issues`` (which
        carry severity + layer hints) so the classifier sees the richest
        possible context for each zone. Cached per-pair so repeated selection
        of the same drawing is instant.
        """

        missing_overlays = self._missing_category_overlays_for_pair_v2(pair_id, overlays)
        if not missing_overlays:
            return 0
        if max_records is not None:
            missing_overlays = missing_overlays[: max(0, int(max_records))]

        # Phase N: load AI config once per pair (cheap — just a JSON read)
        # so the cascade path is decided per-comparison, not per-zone.
        try:
            cfg = self._load_ai_config_v2()
        except Exception:  # noqa: BLE001
            logger.exception("AI config load failed — falling back to heuristic")
            cfg = None

        # Index dashboard top_issues by zone_id for quick lookup
        issues_by_zone = self._zone_category_issues_by_zone_v2()
        classified = 0
        for overlay in missing_overlays:
            if not isinstance(overlay, dict):
                continue
            if self._classify_zone_category_record_v2(
                pair_id,
                overlay,
                cfg=cfg,
                issues_by_zone=issues_by_zone,
            ):
                classified += 1
        return classified

    def _zone_category_for(self, pair_id: str, zone_id: str) -> Optional[ZoneCategoryResult]:
        return self._zone_categories_v2.get(pair_id, {}).get(zone_id)

    def _category_choices_for_active_pair_v2(self) -> list[str]:
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        cache = self._zone_categories_v2.get(pair_id, {})
        if not cache:
            return ["전체"]
        seen = sorted({r.category for r in cache.values()})
        return ["전체"] + seen

    def _refresh_category_filter_combo_v2(self) -> None:
        if not hasattr(self, "cmb_category_filter_v2"):
            return
        choices = self._category_choices_for_active_pair_v2()
        current = self.cmb_category_filter_v2.currentText()
        self.cmb_category_filter_v2.blockSignals(True)
        self.cmb_category_filter_v2.clear()
        for choice in choices:
            self.cmb_category_filter_v2.addItem(choice)
        if current in choices:
            self.cmb_category_filter_v2.setCurrentText(current)
        else:
            self.cmb_category_filter_v2.setCurrentIndex(0)
            self._active_category_filter_v2 = "전체"
        self.cmb_category_filter_v2.blockSignals(False)

    def _on_category_filter_changed_v2(self, text: str) -> None:
        self._active_category_filter_v2 = text or "전체"
        # Phase G2.5 — record that the user touched the filter so the
        # auto-default does not overwrite their explicit choice on the
        # next pair selection.
        self._user_picked_category_filter_v2 = True
        self._refresh_zone_list_filter_v2()

    def _update_category_summary_v2(self) -> None:
        """Refresh the per-pair category breakdown label + filter choices.

        Phase G2.5 — when a pair has > NOISE_AUTO_FILTER_THRESHOLD zones
        AND most are non-structural noise (그리드/치수/상세/마킹/오브젝트),
        AUTO-APPLY the "구조 부재" category filter so the user sees the
        few real changes immediately instead of being buried by 2000+
        text/dimension annotation diffs. The filter combo still lets them
        switch back to "전체" anytime.
        """

        self._refresh_category_filter_combo_v2()
        if not hasattr(self, "lbl_category_summary_v2"):
            return
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        cache = self._zone_categories_v2.get(pair_id, {})
        if not cache:
            self.lbl_category_summary_v2.setText(_format_zone_count_summary_v2({}))
            return
        counts = category_summary(cache.values())
        total_count = sum(counts.values())

        # Phase I1 — when a category filter is active, show "X 중 Y 표시"
        # so the user always knows the difference between "drawing has 47
        # zones" and "I'm currently viewing 12 of them". Computed from the
        # cache so it's correct even before the QListWidget rebuilds.
        active_filter = getattr(self, "_active_category_filter_v2", "전체")
        if active_filter and active_filter != "전체":
            visible_total = counts.get(active_filter, 0)
        else:
            visible_total = total_count

        summary_text = _format_zone_count_summary_v2(
            counts, visible_total=visible_total,
        )

        # Phase G2.5 — keep the noise:signal hint as an inline annotation
        # when structural zones are a small fraction of the total.
        from src.services.comparison.zone_classifier import CATEGORY_STRUCTURAL_MEMBER
        struct_count = counts.get(CATEGORY_STRUCTURAL_MEMBER, 0)
        if total_count > 0 and struct_count > 0 and struct_count < total_count:
            summary_text = (
                f"{summary_text}  ⭐ 구조 부재 {struct_count}건 핵심"
            )
        self.lbl_category_summary_v2.setText(summary_text)

        # Auto-filter: when there's a clear signal (some structural member
        # zones) drowning in noise (>500 total), default to showing only
        # structural zones so the reviewer sees the important diffs first.
        # Threshold of 500 was picked so small/medium drawings stay
        # unfiltered (typical S20-class structural floor plan = 2000+
        # zones, of which maybe 2 are structural = clear noise dominance).
        NOISE_AUTO_FILTER_THRESHOLD = 500
        if (
            total_count > NOISE_AUTO_FILTER_THRESHOLD
            and struct_count > 0
            and getattr(self, "_active_category_filter_v2", "전체") == "전체"
            and not getattr(self, "_user_picked_category_filter_v2", False)
        ):
            try:
                idx = self.cmb_category_filter_v2.findText(CATEGORY_STRUCTURAL_MEMBER)
                if idx >= 0:
                    # Block signal so this auto-set doesn't mark as user-picked
                    self.cmb_category_filter_v2.blockSignals(True)
                    self.cmb_category_filter_v2.setCurrentIndex(idx)
                    self.cmb_category_filter_v2.blockSignals(False)
                    self._active_category_filter_v2 = CATEGORY_STRUCTURAL_MEMBER
                    self._refresh_zone_list_filter_v2()
                    logger.info(
                        "Auto-applied category filter '%s' (%d struct / %d total zones)",
                        CATEGORY_STRUCTURAL_MEMBER, struct_count, total_count,
                    )
            except Exception as exc:
                logger.debug("auto category filter failed: %s", exc)

    def _on_compare_preset_changed_v2(self, index: int) -> None:
        """C3 — apply a preset's quality + recursive flag to the input row.

        QW2 — also stashes ``viewer_render_policy`` so the run uses lazy
        rendering when the speed preset is active.
        """

        if not (0 <= index < len(COMPARE_PRESETS)):
            return
        entry = COMPARE_PRESETS[index]
        # Tolerate both 3- and 4-element tuples for forward compat
        quality_idx = entry[1]
        recursive = entry[2]
        viewer_render_policy = entry[3] if len(entry) >= 4 else "top-issues"
        if hasattr(self, "cmb_quality_v2"):
            self.cmb_quality_v2.setCurrentIndex(quality_idx)
        if hasattr(self, "chk_recursive_v2"):
            self.chk_recursive_v2.setChecked(bool(recursive))
        self._active_viewer_render_policy_v2 = str(viewer_render_policy or "top-issues")

    def _recent_paths_path_v2(self) -> Path:
        return _workbench_data_dir() / RECENT_PATHS_FILENAME

    def _load_recent_paths_v2(self) -> list[dict]:
        path = self._recent_paths_path_v2()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _save_recent_paths_v2(self, entries: list[dict]) -> None:
        path = self._recent_paths_path_v2()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(entries[:RECENT_PATHS_LIMIT], ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _add_recent_path_v2(self, source_a: str, source_b: str) -> None:
        if not source_a or not source_b:
            return
        entries = self._load_recent_paths_v2()
        # Dedupe by (a, b) tuple, most recent first
        entries = [e for e in entries if not (
            isinstance(e, dict) and e.get("a") == source_a and e.get("b") == source_b
        )]
        entries.insert(0, {"a": source_a, "b": source_b, "ts": datetime.now().isoformat()})
        self._save_recent_paths_v2(entries)
        self._refresh_recent_menu_v2()

    def _refresh_recent_menu_v2(self) -> None:
        if not hasattr(self, "btn_recent_v2"):
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        entries = self._load_recent_paths_v2()
        if not entries:
            action = menu.addAction("(최근 비교 없음)")
            action.setEnabled(False)
        else:
            for entry in entries[:RECENT_PATHS_LIMIT]:
                if not isinstance(entry, dict):
                    continue
                a = str(entry.get("a") or "")
                b = str(entry.get("b") or "")
                label = f"{Path(a).name} ↔ {Path(b).name}"
                if entry.get("ts"):
                    label += f"  ({entry['ts'][:10]})"
                act = menu.addAction(label)
                act.triggered.connect(lambda _checked=False, _a=a, _b=b: self._apply_recent_pair_v2(_a, _b))
        self.btn_recent_v2.setMenu(menu)

    def _apply_recent_pair_v2(self, source_a: str, source_b: str) -> None:
        self._set_input_path_v2("source_a", source_a)
        self._set_input_path_v2("source_b", source_b)

    def _on_zoom_slider_changed_v2(self, value: int) -> None:
        """C2 — apply slider zoom to the active viewport pair.

        When the lightweight viewer is showing, drive its cameras
        (``apply_zoom_factor`` anchors 100 % to fit-to-view); otherwise fall
        back to the legacy GPU viewport's ``apply_viewport`` sync.
        """

        zoom = max(0.2, min(8.0, int(value) / 100.0))
        if self._is_lightweight_viewer_active_v2():
            for viewport in (
                getattr(self, "preview_before_lightweight_v2", None),
                getattr(self, "preview_after_lightweight_v2", None),
            ):
                if viewport is not None:
                    viewport.apply_zoom_factor(zoom)
        elif hasattr(self, "preview_before_v2") and self.preview_before_v2._quick_ready:
            root = self.preview_before_v2._quick.rootObject()
            if root is not None:
                pan_x = float(root.property("panX") or 0.0)
                pan_y = float(root.property("panY") or 0.0)
                self.preview_before_v2.apply_viewport(zoom, pan_x, pan_y)
                self._sync_preview_viewport_v2(self.preview_after_v2, zoom, pan_x, pan_y)
        if hasattr(self, "lbl_zoom_value_v2"):
            self.lbl_zoom_value_v2.setText(f"{int(round(zoom * 100))}%")

    def _on_fit_view_v2(self) -> None:
        """Fit-to-view both viewports."""

        if self._is_lightweight_viewer_active_v2():
            for viewport in (
                getattr(self, "preview_before_lightweight_v2", None),
                getattr(self, "preview_after_lightweight_v2", None),
            ):
                if viewport is not None:
                    viewport.fit_to_view()
            return
        if hasattr(self, "preview_before_v2"):
            self.preview_before_v2.fitInView()
        if hasattr(self, "preview_after_v2"):
            self.preview_after_v2.fitInView()
        # After fit, update zoom slider to reflect current zoom
        if hasattr(self, "preview_before_v2") and self.preview_before_v2._quick_ready:
            QTimer.singleShot(120, self._sync_zoom_slider_from_viewport_v2)

    def _on_reset_zoom_v2(self) -> None:
        """Snap both viewports to 100% zoom."""

        if hasattr(self, "sld_zoom_v2"):
            self.sld_zoom_v2.setValue(100)
        else:
            self._on_zoom_slider_changed_v2(100)

    def _sync_zoom_slider_from_viewport_v2(self) -> None:
        """Read current zoom from a viewport and update slider/label silently."""

        if not hasattr(self, "sld_zoom_v2") or not hasattr(self, "preview_before_v2"):
            return
        if not self.preview_before_v2._quick_ready or not self.preview_before_v2._quick:
            return
        root = self.preview_before_v2._quick.rootObject()
        if root is None:
            return
        try:
            zoom = float(root.property("zoom") or 1.0)
        except (TypeError, ValueError):
            zoom = 1.0
        slider_value = int(round(max(0.2, min(8.0, zoom)) * 100))
        # Block signals so this programmatic update doesn't trigger zoom apply
        self.sld_zoom_v2.blockSignals(True)
        self.sld_zoom_v2.setValue(slider_value)
        self.sld_zoom_v2.blockSignals(False)
        if hasattr(self, "lbl_zoom_value_v2"):
            self.lbl_zoom_value_v2.setText(f"{slider_value}%")

    def _build_detail_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        layout = QVBoxLayout(panel)
        title = QLabel("결과 확인")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title)
        self.lbl_selected_summary_v2 = QLabel("비교 실행 후 도면을 선택하세요.")
        self.lbl_selected_summary_v2.setWordWrap(True)
        layout.addWidget(self.lbl_selected_summary_v2)
        self.lbl_preview_status_v2 = QLabel("렌더 대기")
        self.lbl_preview_status_v2.setProperty("role", "brandBadge")
        layout.addWidget(self.lbl_preview_status_v2)
        # C1 — filter combo + progress label above the zone list
        zone_header_row = QHBoxLayout()
        zone_header_row.addWidget(QLabel("Top 변경구역"))
        zone_header_row.addStretch()
        self.cmb_zone_filter_v2 = QComboBox()
        self.cmb_zone_filter_v2.addItems(["전체", "미검토만", "확인", "보류", "오탐", "검토 완료(전체)"])
        self.cmb_zone_filter_v2.setToolTip("검토 상태별로 변경구역을 필터링합니다.")
        self.cmb_zone_filter_v2.currentTextChanged.connect(self._on_zone_filter_changed_v2)
        zone_header_row.addWidget(self.cmb_zone_filter_v2)
        layout.addLayout(zone_header_row)
        # E2 — AI 카테고리 필터 + 요약
        category_row = QHBoxLayout()
        category_row.addWidget(QLabel("AI 분류 필터"))
        self.cmb_category_filter_v2 = QComboBox()
        self.cmb_category_filter_v2.addItem("전체")
        self.cmb_category_filter_v2.setToolTip(
            "변경구역을 자동 분류한 카테고리(구조 부재/그리드/치수 등)별로 필터링합니다."
        )
        self.cmb_category_filter_v2.currentTextChanged.connect(self._on_category_filter_changed_v2)
        category_row.addWidget(self.cmb_category_filter_v2, stretch=1)
        layout.addLayout(category_row)
        self.lbl_category_summary_v2 = QLabel("AI 분류: -")
        self.lbl_category_summary_v2.setProperty("role", "muted")
        self.lbl_category_summary_v2.setWordWrap(True)
        layout.addWidget(self.lbl_category_summary_v2)
        self.lbl_zone_progress_v2 = QLabel("진행: -")
        self.lbl_zone_progress_v2.setProperty("role", "muted")
        self.lbl_zone_progress_v2.setWordWrap(True)
        layout.addWidget(self.lbl_zone_progress_v2)
        # Phase I2 — zone list is now a category tree:
        #   Root
        #   ├── 🏗️ 구조 부재 변경  (12)         ← top-level item (category)
        #   │   ├── z123 …                     ← leaf (selectable zone)
        #   │   ├── z145 …
        #   ├── 📐 그리드 변경  (5)
        #   …
        # Top-level items group zones by AI category (zone_classifier);
        # leaves are the actual zones the user selects. Built by
        # _populate_zone_list_v2; iteration helpers below abstract the
        # tree shape from the rest of the workbench.
        self.zone_list_v2 = QTreeWidget()
        self.zone_list_v2.setHeaderHidden(True)
        self.zone_list_v2.setRootIsDecorated(True)  # show expand/collapse arrows
        self.zone_list_v2.setColumnCount(1)
        # Selection: zones, not category headers. We DO let the user
        # click a category header (it just collapses/expands), but
        # _on_zone_selected_v2 ignores headers (no zone_id).
        self.zone_list_v2.currentItemChanged.connect(self._on_zone_selected_v2)
        # Perf — keep uniform row sizes for fast scroll over many zones.
        self.zone_list_v2.setUniformRowHeights(True)
        layout.addWidget(self.zone_list_v2, stretch=1)
        nav_row = QHBoxLayout()
        self.btn_prev_zone_v2 = QPushButton("이전 변경")
        self.btn_prev_zone_v2.clicked.connect(lambda: self._move_zone_selection_v2(-1))
        self.btn_next_zone_v2 = QPushButton("다음 변경")
        self.btn_next_zone_v2.clicked.connect(lambda: self._move_zone_selection_v2(1))
        self.btn_reset_zone_v2 = QPushButton("선택 구역 원위치")
        self.btn_reset_zone_v2.clicked.connect(self._reset_zone_focus_v2)
        nav_row.addWidget(self.btn_prev_zone_v2)
        nav_row.addWidget(self.btn_next_zone_v2)
        nav_row.addWidget(self.btn_reset_zone_v2)
        layout.addLayout(nav_row)
        review_row = QHBoxLayout()
        self.btn_zone_confirm_v2 = QPushButton("확인")
        self.btn_zone_confirm_v2.clicked.connect(lambda: self._set_zone_review_status_v2("confirmed"))
        self.btn_zone_ignore_v2 = QPushButton("보류")
        self.btn_zone_ignore_v2.clicked.connect(lambda: self._set_zone_review_status_v2("hold"))
        self.btn_zone_false_positive_v2 = QPushButton("오탐")
        self.btn_zone_false_positive_v2.clicked.connect(lambda: self._set_zone_review_status_v2("false_positive"))
        self.btn_zone_needs_review_v2 = QPushButton("추가 검토")
        self.btn_zone_needs_review_v2.clicked.connect(lambda: self._set_zone_review_status_v2("needs_review"))
        for button in (
            self.btn_zone_confirm_v2,
            self.btn_zone_ignore_v2,
            self.btn_zone_false_positive_v2,
            self.btn_zone_needs_review_v2,
        ):
            button.setEnabled(False)
            review_row.addWidget(button)
        layout.addLayout(review_row)
        # Phase G3.7 — Batch action button. Lets the reviewer mass-apply
        # a status to every zone in the active drawing matching a filter.
        # Hidden until a drawing is selected (enabled in
        # _on_drawing_selected_v2 alongside the per-zone status buttons).
        batch_row = QHBoxLayout()
        self.btn_zone_batch_apply_v2 = QPushButton("🚀 이 도면 일괄 처리...")
        self.btn_zone_batch_apply_v2.setToolTip(
            "현재 도면의 변경구역을 한 번에 같은 상태로 처리합니다.\n"
            "필터 (변경 유형 / 심각도 / 부재 종류 / 현재 상태) 후 일괄 적용."
        )
        self.btn_zone_batch_apply_v2.setEnabled(False)
        self.btn_zone_batch_apply_v2.clicked.connect(self._show_batch_zone_action_dialog_v2)
        batch_row.addStretch()
        batch_row.addWidget(self.btn_zone_batch_apply_v2)
        layout.addLayout(batch_row)
        self.zone_detail_v2 = QTextEdit()
        self.zone_detail_v2.setReadOnly(True)
        self.zone_detail_v2.setMinimumHeight(150)
        self.zone_detail_v2.setHtml(
            "<p style='color:#6B7280;'>변경구역을 선택하면 권장 조치와 변경 근거가 표시됩니다.</p>"
        )
        layout.addWidget(self.zone_detail_v2)
        # C4 — per-zone reviewer memo. Persists to review_state.json via the
        # existing ReviewStateRecord.note field.
        memo_label = QLabel("검토 메모 (Ctrl+Enter로 저장)")
        memo_label.setProperty("role", "muted")
        layout.addWidget(memo_label)
        self.zone_memo_v2 = QTextEdit()
        self.zone_memo_v2.setMinimumHeight(60)
        self.zone_memo_v2.setMaximumHeight(100)
        self.zone_memo_v2.setPlaceholderText("이 변경구역에 대한 메모를 입력하세요...")
        layout.addWidget(self.zone_memo_v2)
        memo_action_row = QHBoxLayout()
        self.btn_save_memo_v2 = QPushButton("메모 저장")
        self.btn_save_memo_v2.setEnabled(False)
        self.btn_save_memo_v2.clicked.connect(self._save_current_zone_memo_v2)
        memo_action_row.addStretch()
        memo_action_row.addWidget(self.btn_save_memo_v2)
        # Phase B1 — vector zoom for the selected zone. Spawns a subprocess
        # that renders ONLY the entities inside the zone bbox to SVG, then
        # opens the SVG in the user's default app (browser / Inkscape) where
        # they get infinite-zoom vector quality. The PNG overview stays as
        # the navigation/locator layer; this is the "actually read the
        # drawing" layer the user said is the commercialization core.
        self.btn_zone_vector_v2 = QPushButton("🔍 벡터로 자세히 보기")
        self.btn_zone_vector_v2.setToolTip(
            "선택한 변경구역만 벡터(SVG)로 다시 렌더해 외부 뷰어에서 무한 확대로 봅니다.\n"
            "첫 클릭은 도면 로딩 때문에 약 20초 정도 걸리고, 같은 도면의 다음 구역들은 더 빠릅니다."
        )
        self.btn_zone_vector_v2.setEnabled(False)
        self.btn_zone_vector_v2.clicked.connect(self._on_zone_vector_button_clicked_v2)
        memo_action_row.addWidget(self.btn_zone_vector_v2)
        layout.addLayout(memo_action_row)
        # Phase H3 — Show multi-page PDF page-match results dialog.
        # Hidden by default; surfaces only after a comparison run with at
        # least one PDF pair that ran the page auto-matcher.
        self.btn_page_match_results_v2 = QPushButton("📑 PDF 페이지 매칭 결과")
        self.btn_page_match_results_v2.setToolTip(
            "멀티페이지 PDF 비교 시 자동으로 매칭된 페이지 쌍 목록을 봅니다.\n"
            "검토 필요(REVIEW_REQUIRED) 페어를 사람이 검증할 수 있습니다."
        )
        self.btn_page_match_results_v2.clicked.connect(
            self._show_page_match_results_dialog_v2
        )
        self.btn_page_match_results_v2.setEnabled(False)
        self.btn_page_match_results_v2.setVisible(False)
        # Phase F P0 user-feedback fix — escape hatch when the rasterised
        # preview is too coarse for the structural detail. Opens the original
        # source file (DWG/DXF/PDF) in the user's default viewer so they can
        # always read the drawing, even when our renderer falls short.
        self.btn_open_source_external_v2 = QPushButton("📂 원본 도면 외부 뷰어로 열기")
        self.btn_open_source_external_v2.setToolTip(
            "선택한 도면의 원본 파일(DWG/DXF/PDF)을 Windows 기본 연결 프로그램에서 엽니다.\n"
            "구조도면 같이 정보가 많은 도면은 이 escape hatch 로 100 % 디테일 확인이 가능합니다."
        )
        self.btn_open_source_external_v2.clicked.connect(self._open_source_external_v2)
        self.btn_open_marked_v2 = QPushButton("구름마크 도면 열기")
        self.btn_open_marked_v2.clicked.connect(self._open_marked_dxf_v2)
        self.btn_open_executive_v2 = QPushButton("요약 대시보드 열기")
        self.btn_open_executive_v2.clicked.connect(self._open_executive_v2)
        self.btn_open_priority_csv_v2 = QPushButton("우선 검토 CSV 열기")
        self.btn_open_priority_csv_v2.clicked.connect(self._open_priority_csv_v2)
        self.btn_open_viewer_v2 = QPushButton("경량 뷰어 열기")
        self.btn_open_viewer_v2.clicked.connect(self._open_viewer_v2)
        self.btn_open_package_v2 = QPushButton("검토 패키지 열기")
        self.btn_open_package_v2.clicked.connect(self._open_artifact_dir_v2)
        self.btn_open_perf_diag_v2 = QPushButton("성능 진단")
        self.btn_open_perf_diag_v2.clicked.connect(self._show_viewer_perf_dialog_v2)
        # Phase Q2 (RV-20260509-002) — surface every silent-drop counter so
        # reviewers can answer "왜 이 변경이 안 보이나요?" without reading
        # JSON. Aggregates across all pairs (or focuses on the active row).
        self.btn_show_suppression_audit_v2 = QPushButton("🔍 변경 가시성 진단")
        self.btn_show_suppression_audit_v2.clicked.connect(
            self._show_suppression_audit_v2
        )
        # Confirmed-only cloud export (PDF + CAD universal — paints clouds on
        # the after PNG using PIL). Distinct from the always-on raw cloud DXF
        # so the user gets a clean "확인된 변경만" artefact.
        self.btn_export_confirmed_clouds_v2 = QPushButton("확인된 변경 구름마크 추출 (현재 도면)")
        self.btn_export_confirmed_clouds_v2.setProperty("primary", "true")
        self.btn_export_confirmed_clouds_v2.clicked.connect(self._export_confirmed_cloud_marks_v2)
        self.btn_export_confirmed_clouds_all_v2 = QPushButton("확인된 변경 구름마크 일괄 추출 (모든 도면)")
        self.btn_export_confirmed_clouds_all_v2.clicked.connect(
            lambda: self._export_confirmed_cloud_marks_v2(all_pairs=True)
        )
        # QW3 — one-click PDF review report (cover + per-pair confirmed cloud
        # + memo table + appendix). Recipient gets the full review summary as
        # a single shareable file.
        self.btn_export_review_pdf_v2 = QPushButton("📄 검토 보고서 PDF 만들기")
        self.btn_export_review_pdf_v2.setProperty("primary", "true")
        self.btn_export_review_pdf_v2.clicked.connect(self._export_review_report_pdf_v2)
        for button in (
            self.btn_export_confirmed_clouds_v2,
            self.btn_export_confirmed_clouds_all_v2,
            self.btn_export_review_pdf_v2,
            self.btn_page_match_results_v2,
            self.btn_open_source_external_v2,
            self.btn_open_marked_v2,
            self.btn_open_executive_v2,
            self.btn_open_priority_csv_v2,
            self.btn_open_viewer_v2,
            self.btn_open_package_v2,
            self.btn_open_perf_diag_v2,
            self.btn_show_suppression_audit_v2,
        ):
            button.setEnabled(False)
            layout.addWidget(button)
        return panel

    def _browse_folder_v2(self, attr: str) -> None:
        path = QFileDialog.getExistingDirectory(self, "도면 폴더 선택", "")
        if not path:
            return
        self._set_input_path_v2(attr, path)

    def _browse_file_v2(self, attr: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "도면 파일 선택",
            "",
            "도면 파일 (*.dwg *.dxf *.pdf);;DWG (*.dwg);;DXF (*.dxf);;PDF (*.pdf)",
        )
        if not path:
            return
        self._set_input_path_v2(attr, path)

    def _set_input_path_v2(self, attr: str, path: str) -> None:
        setattr(self, f"_{attr}", path)
        getattr(self, f"edit_{attr}_v2").setText(path)
        self._update_run_enabled()

    def _is_valid_input_path_v2(self, path_value: str) -> bool:
        if not path_value:
            return False
        path = Path(path_value)
        if path.is_dir():
            return True
        return path.is_file() and path.suffix.lower() in SUPPORTED_DRAWING_EXTENSIONS

    def _resolve_quality(
        self,
        quality_idx: int,
        source_a: str,
        source_b: str,
    ) -> tuple[int, int, Optional[Any]]:
        """Translate quality combo selection into (preview_dpi, max_edge, decision).

        - Index 0 ("🤖 자동") triggers adaptive_quality.select_quality() over
          the actual input files. Returns the picked DPI/edge plus the full
          ``QualityDecision`` so callers can log/display the rationale.
        - Any other index uses the legacy explicit DPI tuple from
          PREVIEW_QUALITY_PRESETS and returns ``decision=None``.
        - Out-of-range indices fall back to auto.
        """
        from src.services.comparison.adaptive_quality import (
            measure_inputs,
            select_quality,
        )

        # Out-of-range or auto sentinel → adaptive
        is_auto = (
            quality_idx == PREVIEW_QUALITY_AUTO_INDEX
            or quality_idx < 0
            or quality_idx >= len(PREVIEW_QUALITY_PRESETS)
        )
        if not is_auto:
            _, dpi, edge = PREVIEW_QUALITY_PRESETS[quality_idx]
            return dpi, edge, None

        try:
            paths_a = self._gather_drawing_paths(source_a)
            paths_b = self._gather_drawing_paths(source_b)
            inputs = measure_inputs(paths_a, paths_b)
        except Exception as exc:  # noqa: BLE001 — measurement is best-effort
            logger.warning("Auto-quality measurement failed: %s", exc)
            from src.services.comparison.adaptive_quality import InputCharacteristics

            inputs = InputCharacteristics(0, 0, 0, 0, 0)

        decision = select_quality(
            inputs,
            memory_cap_mb=float(GPU_VIEWER_MEMORY_BUDGET_MB) * 8.0,  # match worker default
        )
        return decision.dpi, decision.max_edge_px, decision

    # P0 perf fix (multi-agent audit 2026-05-15): rglob over a folder with
    # thousands of files is the slowest call on the auto-quality path; users
    # who tweak DPI and re-run see the same scan repeated 3-5x per session.
    # mtime-keyed cache invalidates whenever the folder is modified, so a stale
    # cache cannot silently hide newly added drawings.
    _drawing_path_cache: dict[tuple[str, int, int], list[Path]] = {}

    @classmethod
    def _gather_drawing_paths(cls, source: str) -> list[Path]:
        """Return drawing files under ``source`` (file → [file], dir → recursive scan)."""
        if not source:
            return []
        candidate = Path(source)
        if candidate.is_file():
            return [candidate]
        if candidate.is_dir():
            extensions = {".dwg", ".dxf", ".pdf"}
            try:
                stat = candidate.stat()
                cache_key = (str(candidate.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
                cached = cls._drawing_path_cache.get(cache_key)
                if cached is not None:
                    return list(cached)
            except OSError:
                cache_key = None  # filesystem hiccup — fall through, do not cache
            results = sorted(
                p
                for p in candidate.rglob("*")
                if p.is_file() and p.suffix.lower() in extensions
            )
            if cache_key is not None:
                # Bound the cache so a long session of folder hopping cannot
                # leak unbounded memory; LRU-style eviction of oldest entry.
                if len(cls._drawing_path_cache) >= 32:
                    try:
                        oldest = next(iter(cls._drawing_path_cache))
                        cls._drawing_path_cache.pop(oldest, None)
                    except StopIteration:
                        pass
                cls._drawing_path_cache[cache_key] = list(results)
            return results
        return []

    def _update_run_enabled(self) -> None:
        if hasattr(self, "btn_run_v2"):
            valid_inputs = self._is_valid_input_path_v2(self._source_a) and self._is_valid_input_path_v2(self._source_b)
            self.btn_run_v2.setEnabled(bool(valid_inputs and not self._worker))

    def _run_auto_compare(self) -> None:
        if not self._is_valid_input_path_v2(self._source_a) or not self._is_valid_input_path_v2(self._source_b):
            QMessageBox.warning(
                self,
                "입력 확인",
                "변경 전/후 입력은 DWG, DXF, PDF 파일이거나 도면 파일이 들어 있는 폴더여야 합니다.",
            )
            self._update_run_enabled()
            return
        # Audit-gates §10 follow-up — translate the quality combo into DPI/edge
        # values; index 0 ("🤖 자동") triggers adaptive_quality.select_quality()
        # so users no longer have to choose between DPI 80 and DPI 400 for
        # drawings whose memory footprint they cannot estimate.
        quality_idx = (
            self.cmb_quality_v2.currentIndex()
            if hasattr(self, "cmb_quality_v2")
            else PREVIEW_QUALITY_DEFAULT_INDEX
        )
        preview_dpi, preview_max_edge, quality_decision = self._resolve_quality(
            quality_idx, self._source_a, self._source_b
        )
        if quality_decision is not None and quality_decision.auto_selected:
            logger.info(
                "Auto-quality selected: %s — %s",
                quality_decision.tier.label,
                quality_decision.rationale,
            )
        request = FolderCompareRunRequest(
            source_a=self._source_a,
            source_b=self._source_b,
            output_dir=_run_output_dir(),
            recursive=self.chk_recursive_v2.isChecked(),
            enable_descriptor_cache=True,
            dxf_cache_dir=self._dxf_cache_dir,
            max_workers=1,
            preview_dpi=preview_dpi,
            preview_max_edge_px=preview_max_edge,
            # Phase G2.5 critical regression fix — pdf_compare_dpi is the
            # *comparison* algorithm DPI, NOT the preview rasterisation DPI.
            # Keep at 200 (the prior tested baseline that produced 90%+
            # accuracy on PDF runs) regardless of which "화질" preset the
            # user picks. The user's "구조도면 정밀 (DPI 400)" preset
            # affects PREVIEW sharpness; comparison accuracy stays anchored
            # at the proven baseline.
            pdf_compare_dpi=200,
            # Keep at least the first completed pair's static preview
            # available. 0 means "skip every preview" in
            # export_preview_artifacts(), which made successful GUI runs show
            # no drawing preview despite export_preview=True.
            max_preview_pairs=1,
            viewer_engine="auto",
            viewer_cache_dir=_workbench_data_dir() / "viewer_cache",
            tile_size=GPU_VIEWER_TILE_SIZE,
            max_visible_overlays=GPU_VIEWER_MAX_VISIBLE_OVERLAYS,
            viewer_memory_budget_mb=GPU_VIEWER_MEMORY_BUDGET_MB,
            render_selected_on_open=True,
            prefetch_neighbor_tiles=False,
            tile_prefetch_radius=0,
            overview_max_edge=2200,
            focus_tile_max_edge=1600,
            # GUI use case wants the PNG backgrounds available immediately so the
            # preview viewport shows the actual drawing on first selection. Lazy
            # would leave PDFs/DXFs as relative-only overlays until the user
            # picks a zone. ``top-issues`` renders the priority pairs the user
            # is most likely to inspect first.
            # QW2 — the "초고속 스캔" preset overrides this to "lazy" so the run
            # finishes in minutes for large folders; per-pair PNGs render on
            # demand when the user clicks a drawing.
            viewer_render_policy=getattr(self, "_active_viewer_render_policy_v2", "top-issues"),
            viewer_perf_log=True,
            export_profile="sharable",
            fast_first_review=True,
            max_zone_tiles=0,
            export_marked_pdf=False,
            marked_pdf_mode="off",
            dwg_backend_mode=default_gui_dwg_backend_mode(),
            auto_export_structural_clouds=(
                self.chk_auto_structural_clouds_v2.isChecked()
                if hasattr(self, "chk_auto_structural_clouds_v2") else False
            ),
            # Phase O Commit 3 [RV-20260508-009] — pipeline → batch
            # 으로 propagate. Default True; 사용자가 GUI 에서 끈 경우
            # 만 False.
            block_text_detection=(
                self.chk_block_text_detection_v2.isChecked()
                if hasattr(self, "chk_block_text_detection_v2") else True
            ),
        )
        self._result = None
        self._dashboard = {}
        self._drawing_rows = []
        self._preview_by_pair = {}
        self._viewer_perf_summary = {}
        self._run_completion_v2 = {}
        self._reset_compare_session_viewer_state_v2(clear_ui=True)
        self._set_v2_busy(True)
        self.progress_v2.setValue(0)
        self.lbl_status_v2.setText("준비 중")
        self._worker = AutoFolderCompareWorker(request)
        self._worker.progress.connect(self._on_auto_progress_v2)
        self._worker.review_ready.connect(self._on_auto_review_ready_v2)
        self._worker.finished.connect(self._on_auto_finished_v2)
        self._worker.error.connect(self._on_auto_error_v2)
        self._worker.start()
        # C3 — record this comparison in the recent list for one-click reuse
        self._add_recent_path_v2(self._source_a, self._source_b)

    def _cancel_auto_compare(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.lbl_status_v2.setText("취소 요청 중")
        self.btn_cancel_v2.setEnabled(False)

    def _retire_qthread_v2(self, worker: Optional[QThread]) -> None:
        """Keep a QThread wrapper alive until the native thread has stopped."""

        if worker is None:
            return
        if worker not in self._retired_qthreads_v2:
            self._retired_qthreads_v2.append(worker)
        QTimer.singleShot(100, lambda w=worker: self._cleanup_retired_qthread_v2(w))

    def _cleanup_retired_qthread_v2(self, worker: QThread) -> None:
        try:
            if worker.isRunning():
                QTimer.singleShot(250, lambda w=worker: self._cleanup_retired_qthread_v2(w))
                return
            worker.wait(0)
            worker.deleteLater()
        except RuntimeError:
            # The C++ object may already be gone during app shutdown.
            pass
        except Exception:
            logger.exception("QThread cleanup failed")
        finally:
            try:
                self._retired_qthreads_v2.remove(worker)
            except ValueError:
                pass

    def _retire_active_worker_v2(self) -> None:
        worker = self._worker
        self._worker = None
        self._retire_qthread_v2(worker)

    def _retire_render_worker_v2(self) -> None:
        worker = self._render_worker
        self._render_worker = None
        self._retire_qthread_v2(worker)

    def _retire_visible_tile_worker_v2(self, worker: Optional[QThread] = None) -> None:
        target = worker or self._visible_tile_worker_v2
        if worker is None or worker is self._visible_tile_worker_v2:
            self._visible_tile_worker_v2 = None
        self._retire_qthread_v2(target)

    def _retire_full_zone_tree_overlay_worker_v2(self, worker: Optional[QThread] = None) -> None:
        target = worker or self._full_zone_tree_overlay_worker_v2
        if worker is None or worker is self._full_zone_tree_overlay_worker_v2:
            self._full_zone_tree_overlay_worker_v2 = None
        self._retire_qthread_v2(target)

    def _retire_full_zone_tree_plan_worker_v2(self, worker: Optional[QThread] = None) -> None:
        target = worker or self._full_zone_tree_plan_worker_v2
        if worker is None or worker is self._full_zone_tree_plan_worker_v2:
            self._full_zone_tree_plan_worker_v2 = None
        self._retire_qthread_v2(target)

    def _stop_qthread_for_close_v2(self, worker: Optional[QThread], *, timeout_ms: int) -> bool:
        if worker is None:
            return True
        try:
            if hasattr(worker, "cancel"):
                worker.cancel()  # type: ignore[attr-defined]
            if worker.isRunning():
                return bool(worker.wait(timeout_ms))
            worker.wait(0)
            return True
        except RuntimeError:
            return True
        except Exception:
            logger.exception("QThread close wait failed")
            return False

    def _stop_background_threads_for_close_v2(self) -> bool:
        threads: list[QThread] = []
        for worker in (
            self._worker,
            self._render_worker,
            self._visible_tile_worker_v2,
            self._full_zone_tree_overlay_worker_v2,
            self._full_zone_tree_plan_worker_v2,
        ):
            if worker is not None:
                threads.append(worker)
        threads.extend(list(self._retired_qthreads_v2))
        ok = True
        for worker in threads:
            ok = self._stop_qthread_for_close_v2(worker, timeout_ms=5000) and ok
        return ok

    def _on_auto_progress_v2(self, percent: int, message: str) -> None:
        self.progress_v2.setValue(percent)
        self.lbl_status_v2.setText(message)

    def _on_auto_review_ready_v2(self, result: FolderCompareRunResult) -> None:
        if getattr(result, "package_complete", False):
            return
        self._result = result
        self.progress_v2.setValue(min(99, max(0, int(self.progress_v2.value() or 0), 97)))
        self.lbl_status_v2.setText("검토 가능 - 최종 패키지 정리 중")
        self._preview_by_pair = {
            artifact.pair_id: artifact
            for artifact in getattr(result.preview_package, "artifacts", [])
        }
        self._load_dashboard_v2()
        self._load_review_state_v2()
        self._load_viewer_manifest_v2()
        self._load_drawing_rows_v2()
        self._populate_summary_v2()
        self._populate_top_issues_v2()
        self._populate_pattern_groups_v2()
        self._refresh_drawing_list_v2()
        self.btn_detail_match_v2.setEnabled(True)
        if hasattr(self, "act_region_match_results_v2"):
            self.act_region_match_results_v2.setEnabled(True)
        self.btn_open_executive_v2.setEnabled(bool(self._dashboard))
        self.btn_open_priority_csv_v2.setEnabled(bool(self._dashboard))
        self.btn_open_viewer_v2.setEnabled(bool(self._viewer_manifest))
        if hasattr(self, "btn_open_source_external_v2"):
            self.btn_open_source_external_v2.setEnabled(bool(self._drawing_rows))
        if hasattr(self, "btn_open_perf_diag_v2"):
            self.btn_open_perf_diag_v2.setEnabled(bool(self._viewer_root))
        QApplication.processEvents()

    def _on_auto_finished_v2(self, result: FolderCompareRunResult) -> None:
        self._retire_active_worker_v2()
        self._result = result
        # Phase G2.7-DIAG: log compare completion with key counts so we
        # can correlate user-visible "preview failed" reports with the
        # actual viewer_pair / preview state.
        try:
            pair_count = len(result.preview_package.artifacts) if getattr(result, "preview_package", None) else 0
            output_dir = getattr(result, "output_dir", "?")
            logger.info(
                "[compare done] output_dir=%r pair_count=%d",
                output_dir, pair_count,
            )
        except Exception:  # noqa: BLE001
            logger.exception("[compare done] failed to log result summary")
        self._set_v2_busy(False)
        self.progress_v2.setValue(100)
        # Audit-gates §10 follow-up — the dashboard/viewer/drawing-list
        # population below runs synchronously on the GUI thread and can take
        # 5-30 seconds for S20-class results (3,000+ zones). Without this
        # status line the user thinks the bar froze at "완료" while the
        # screen seemingly hangs. Updating the status forces a paint event
        # and tells the user explicitly what is happening next.
        self.lbl_status_v2.setText("결과 적재 중 (대시보드 + 뷰어 + 도면 목록 — 잠시만 기다리세요)")
        QApplication.processEvents()
        completion = validate_run_completion(result.run_manifest_path, result.success_sentinel_path)
        self._run_completion_v2 = completion
        if completion.get("valid"):
            self.lbl_status_v2.setText("완료 - 결과를 확인하세요")
        else:
            self.lbl_status_v2.setText(f"부분 결과 - {completion.get('message') or '_SUCCESS 미생성'}")
            QMessageBox.warning(
                self,
                "결과 무결성 경고",
                f"이 실행 결과는 완료 검증에 실패했습니다.\n\n사유: {completion.get('message')}\n\n"
                "산출물을 사용하기 전에 재실행을 권장합니다.",
            )
        self._preview_by_pair = {artifact.pair_id: artifact for artifact in result.preview_package.artifacts}
        self._load_dashboard_v2()
        self._load_review_state_v2()
        self._load_viewer_manifest_v2()
        self._load_drawing_rows_v2()
        self._populate_summary_v2()
        self._populate_top_issues_v2()
        self._populate_pattern_groups_v2()
        self._refresh_drawing_list_v2()
        self.btn_detail_match_v2.setEnabled(True)
        if hasattr(self, "act_region_match_results_v2"):
            self.act_region_match_results_v2.setEnabled(True)
        self.btn_open_executive_v2.setEnabled(True)
        self.btn_open_priority_csv_v2.setEnabled(bool(self._dashboard))
        self.btn_open_viewer_v2.setEnabled(bool(self._viewer_manifest))
        self.btn_open_package_v2.setEnabled(True)
        if hasattr(self, "btn_open_source_external_v2"):
            # The escape hatch is per-row; enable when ANY row exists, the
            # actual file existence check happens at click time.
            self.btn_open_source_external_v2.setEnabled(True)
        if hasattr(self, "btn_open_perf_diag_v2"):
            self.btn_open_perf_diag_v2.setEnabled(bool(self._viewer_root))
        if hasattr(self, "btn_show_suppression_audit_v2"):
            # Always enable once we have a result — the audit helper handles
            # the empty-stats case gracefully ("가려진 변경 없음").
            self.btn_show_suppression_audit_v2.setEnabled(True)
        if hasattr(self, "btn_export_confirmed_clouds_v2"):
            self.btn_export_confirmed_clouds_v2.setEnabled(True)
        if hasattr(self, "btn_export_confirmed_clouds_all_v2"):
            self.btn_export_confirmed_clouds_all_v2.setEnabled(True)
        if hasattr(self, "btn_export_review_pdf_v2"):
            self.btn_export_review_pdf_v2.setEnabled(True)
        # B2 — auto collapse the input/summary region after a successful run so
        # the viewer + detail splitter gets nearly the full window. The user
        # can re-expand at any time with the toggle button above the splitter,
        # or use Ctrl+N / 파일 메뉴 to start a new comparison from any state.
        if hasattr(self, "_compact_mode_v2") and not self._compact_mode_v2:
            self._set_compact_mode_v2(True)
            if hasattr(self, "lbl_status_v2"):
                current = self.lbl_status_v2.text()
                hint = " · 새 비교는 Ctrl+N 또는 상단 [📁 새 파일/폴더 선택] 버튼"
                if hint not in current:
                    self.lbl_status_v2.setText(f"{current}{hint}")

        # Phase G3.1 — auto-enable the lightweight viewer for DXF/DWG runs.
        # The vector skeleton renders sharp at any zoom level (the user's
        # earlier complaint about raster mush at zoom). PDF runs stay on
        # the legacy raster viewer because PDF lightweight rendering is
        # G2.6 work. We respect the user's manual toggle: once they touch
        # the [보기] menu item, this auto-enable never fires again.
        #
        # G2.7-COORDFIX — auto-enable MUST run BEFORE the auto-selection
        # of the first row. Otherwise ``_on_drawing_selected_v2`` checks
        # ``act_lightweight_viewer_v2.isChecked()`` while the toggle is
        # still False, skips the PDF load, and the viewport stays on the
        # "도면을 선택하면 빠르게 표시됩니다" empty notice forever even
        # though the lightweight viewer activates milliseconds later.
        try:
            self._maybe_auto_enable_lightweight_viewer_v2()
        except Exception:
            logger.exception("Auto-enable lightweight viewer failed (non-fatal)")

        # Auto-select the first drawing AFTER the viewer toggle is
        # finalised so the drawing-selected handler sees the correct
        # toggle state and can dispatch to the right preview path.
        if self.drawing_list_v2.count():
            self.drawing_list_v2.setCurrentRow(0)

        # Phase H3 — surface multi-page PDF page-match results as a status
        # hint + enable the [페이지 매칭 결과] button so reviewers can
        # verify which pages were paired (and override REVIEW_REQUIRED
        # cases). Only fires when at least one comparison pair ran the
        # page matcher.
        try:
            self._update_page_match_status_v2()
        except Exception:
            logger.exception("Page-match status update failed (non-fatal)")

    def _collect_page_match_metadata_v2(self) -> list[dict]:
        """Phase H3 — Walk the compare_summary and return per-pair page
        match data for each PDF pair that ran the auto-matcher.

        Returns a list of:
            {
                "drawing_label": "01.3PG1.pdf vs 02.3PG1_R1.pdf",
                "pairs_total": 4,
                "auto_confirmed": 3,
                "review_required": 1,
                "pairs": [
                    {"page_a": 0, "page_b": 0, "status": "auto_confirmed", "score": 0.94},
                    ...
                ]
            }
        """

        from src.services.comparison.pair_identity import candidate_pair_uuid

        out: list[dict] = []
        if not self._result:
            return out
        compare_summary = getattr(self._result, "compare_summary", None)
        if compare_summary is None:
            return out
        for item in getattr(compare_summary, "items", []) or []:
            comparison = getattr(item, "result", None)
            if comparison is None:
                continue
            metadata = getattr(comparison, "metadata", {}) or {}
            if not metadata.get("page_match_enabled"):
                continue
            candidate = getattr(item, "candidate", None)
            pair_uuid = ""
            label_parts = []
            if candidate is not None:
                a_path = getattr(getattr(candidate, "descriptor_a", None), "path", "")
                b_path = getattr(getattr(candidate, "descriptor_b", None), "path", "")
                if a_path:
                    label_parts.append(Path(str(a_path)).name)
                if b_path:
                    label_parts.append(Path(str(b_path)).name)
                # Phase H4 — pair_uuid keys the manual_page_overrides JSON
                # so the dialog can save edits back per-pair.
                try:
                    pair_uuid = candidate_pair_uuid(candidate)
                except Exception:  # noqa: BLE001 — diagnostic helper only
                    pair_uuid = ""
            label = " vs ".join(label_parts) if label_parts else "(unnamed pair)"
            out.append({
                "drawing_label": label,
                "pair_uuid": pair_uuid,
                "page_count_a": int(metadata.get("page_count_a", 0) or 0),
                "page_count_b": int(metadata.get("page_count_b", 0) or 0),
                "pairs_total": int(metadata.get("page_match_pairs_total", 0) or 0),
                "auto_confirmed": int(metadata.get("page_match_auto_confirmed", 0) or 0),
                "review_required": int(metadata.get("page_match_review_required", 0) or 0),
                "manual_overrides": int(metadata.get("page_match_manual_overrides", 0) or 0),
                "pairs": list(metadata.get("page_match_pairs") or []),
            })
        return out

    def _update_page_match_status_v2(self) -> None:
        """Phase H3 — Append a one-line page-match summary to the status
        label and enable the [📑 페이지 매칭 결과] button when applicable.
        """

        info = self._collect_page_match_metadata_v2()
        if not info:
            # No PDF pairs ran the matcher — keep the button hidden / disabled.
            if hasattr(self, "btn_page_match_results_v2"):
                self.btn_page_match_results_v2.setEnabled(False)
                self.btn_page_match_results_v2.setVisible(False)
            return

        total = sum(d["pairs_total"] for d in info)
        auto = sum(d["auto_confirmed"] for d in info)
        review = sum(d["review_required"] for d in info)

        if hasattr(self, "lbl_status_v2"):
            current = self.lbl_status_v2.text()
            hint = (
                f" · 📑 PDF 페이지 매칭: {auto + review}/{total} 페어"
                f" ({auto} 자동 / {review} 검토필요)"
            )
            if hint not in current:
                self.lbl_status_v2.setText(f"{current}{hint}")
        if hasattr(self, "btn_page_match_results_v2"):
            self.btn_page_match_results_v2.setEnabled(True)
            self.btn_page_match_results_v2.setVisible(True)
        logger.info(
            "Page-match status: %d PDF pair(s), %d total page pairs (%d auto, %d review)",
            len(info), total, auto, review,
        )

    def _overrides_path_v2(self) -> Optional[Path]:
        """Phase H4 — Path to the per-run override JSON, or None if no
        result has been loaded yet."""

        if not self._result:
            return None
        out_dir = getattr(self._result, "output_dir", "")
        if not out_dir:
            return None
        return Path(str(out_dir)) / "manual_page_overrides.json"

    def _show_page_match_results_dialog_v2(self) -> None:
        """Phase H3 + H4 — Modal dialog showing every page-match pair
        grouped by file pair. The "B 측 페이지" column is editable via a
        combo box: pick a different page or "(매칭 안 함)" to override
        the auto-matched value. Saving writes
        ``output_dir/manual_page_overrides.json`` which the next compare
        run will pick up.
        """

        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
            QHeaderView, QPushButton, QHBoxLayout, QComboBox, QMessageBox,
        )

        from src.services.comparison.manual_page_overrides import (
            load_overrides as _load_overrides,
            new_entry as _new_override_entry,
            save_overrides as _save_overrides,
            upsert_override as _upsert_override,
        )

        info = self._collect_page_match_metadata_v2()
        dialog = QDialog(self)
        dialog.setWindowTitle("📑 PDF 페이지 자동 매칭 결과")
        dialog.resize(880, 540)
        layout = QVBoxLayout(dialog)

        # Load existing overrides up-front so combo-box defaults reflect
        # whatever the user saved on a previous open.
        overrides_path = self._overrides_path_v2()
        existing_overrides: dict[str, list] = {}
        if overrides_path and overrides_path.exists():
            try:
                existing_overrides = _load_overrides(overrides_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load existing overrides: %s", exc)

        if not info:
            layout.addWidget(QLabel(
                "이 비교 실행에서는 멀티페이지 PDF 페이지 매칭이 적용되지 않았습니다.\n"
                "(단일 페이지 PDF, DXF/DWG, 또는 페이지 매칭 비활성 옵션)"
            ))
            close_only_row = QHBoxLayout()
            close_only_row.addStretch()
            cb = QPushButton("닫기")
            cb.clicked.connect(dialog.accept)
            close_only_row.addWidget(cb)
            layout.addLayout(close_only_row)
            dialog.exec()
            return

        # Track pending edits per (pair_uuid, page_a) → new_page_b
        pending_edits: dict[tuple[str, int], int] = {}
        # Track combo widgets so save_handler can re-read on demand
        combo_registry: list[tuple[str, int, int, "QComboBox"]] = []
        # Each entry: (pair_uuid, page_a, original_page_b, combo)

        legend = QLabel(
            "<i>B 측 페이지 콤보를 변경하면 [💾 변경사항 저장] 버튼이 활성화됩니다. "
            "저장 후 다음 비교 실행에서 적용됩니다. "
            "<b>(매칭 안 함)</b> 선택 시 해당 A 페이지는 비교 대상에서 제외됩니다.</i>"
        )
        legend.setTextFormat(Qt.RichText)
        legend.setWordWrap(True)
        layout.addWidget(legend)

        save_btn = QPushButton("💾 변경사항 저장")
        save_btn.setEnabled(False)
        save_btn.setStyleSheet(
            "QPushButton { background-color: #16A34A; color: white; padding: 6px 14px; }"
            "QPushButton:disabled { background-color: #9CA3AF; }"
        )

        def on_combo_changed(_idx: int) -> None:
            # Walk all combos, pick up any whose current value differs
            # from original_page_b → store in pending_edits.
            pending_edits.clear()
            for pair_uuid, page_a, orig_pb, combo in combo_registry:
                cur_pb = combo.currentData()
                if cur_pb is None:
                    continue
                if int(cur_pb) != int(orig_pb):
                    pending_edits[(pair_uuid, page_a)] = int(cur_pb)
            save_btn.setEnabled(bool(pending_edits))
            save_btn.setText(
                f"💾 변경사항 저장 ({len(pending_edits)}개 변경)"
                if pending_edits else "💾 변경사항 저장"
            )

        # Build per-pair tables
        for record in info:
            pair_uuid = str(record.get("pair_uuid") or "")
            page_count_b = int(record.get("page_count_b") or 0)
            override_count = int(record.get("manual_overrides") or 0)
            override_hint = (
                f" · <span style='color:#7C3AED'>수동 {override_count}</span>"
                if override_count else ""
            )
            header = QLabel(
                f"<b>{record['drawing_label']}</b> — "
                f"총 {record['pairs_total']} 페어 · "
                f"<span style='color:#16A34A'>자동 {record['auto_confirmed']}</span> · "
                f"<span style='color:#0969DA'>검토필요 {record['review_required']}</span>"
                f"{override_hint}"
            )
            header.setTextFormat(Qt.RichText)
            layout.addWidget(header)

            # Index existing overrides for this pair so we can pre-select
            existing_for_pair = {
                int(getattr(e, "page_a", -1)): int(getattr(e, "page_b", -1))
                for e in existing_overrides.get(pair_uuid, [])
            }

            table = QTableWidget(len(record["pairs"]), 4)
            table.setHorizontalHeaderLabels([
                "A 측 페이지", "B 측 페이지 (수정 가능)", "신뢰도", "상태",
            ])
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectRows)

            for row_idx, pair in enumerate(record["pairs"]):
                pa = pair.get("page_a")
                pb = pair.get("page_b")
                sc = pair.get("score", 0.0)
                st = pair.get("status", "")

                table.setItem(row_idx, 0, QTableWidgetItem(
                    "—" if pa in (None, -1) else str(int(pa) + 1)
                ))

                # B-page combo box (Phase H4)
                if pa in (None, -1):
                    # UNMATCHED_B row — A side is empty. Display read-only B page.
                    table.setItem(row_idx, 1, QTableWidgetItem(
                        "—" if pb in (None, -1) else str(int(pb) + 1)
                    ))
                else:
                    combo = QComboBox()
                    combo.addItem("(매칭 안 함)", -1)
                    for b in range(page_count_b):
                        combo.addItem(str(b + 1), b)
                    # Pre-select the existing override if any, else current pb
                    page_a_int = int(pa)
                    if page_a_int in existing_for_pair:
                        target_pb = existing_for_pair[page_a_int]
                    else:
                        target_pb = -1 if pb in (None, -1) else int(pb)
                    target_idx = combo.findData(target_pb)
                    if target_idx >= 0:
                        combo.setCurrentIndex(target_idx)
                    table.setCellWidget(row_idx, 1, combo)
                    # Original page_b for diff detection — when an existing
                    # override is loaded, we treat IT as the baseline so the
                    # save button only re-fires on further edits.
                    original_pb = target_pb
                    combo_registry.append((pair_uuid, page_a_int, original_pb, combo))
                    combo.currentIndexChanged.connect(on_combo_changed)

                table.setItem(row_idx, 2, QTableWidgetItem(f"{float(sc):.3f}"))
                status_ko = {
                    "auto_confirmed": "✅ 자동 확정",
                    "review_required": "⚠️ 검토 필요",
                    "unmatched_a": "❌ A 전용 (B에 없음)",
                    "unmatched_b": "❌ B 전용 (A에 없음)",
                    "sequential": "▫ 순차 비교",
                    "manual_override": "✏️ 수동 매칭",
                }.get(st, st)
                table.setItem(row_idx, 3, QTableWidgetItem(status_ko))

            table.resizeRowsToContents()
            layout.addWidget(table)
            layout.addSpacing(10)

        # Save handler — flushes pending_edits to overrides file
        def save_handler() -> None:
            if not pending_edits:
                return
            if not overrides_path:
                QMessageBox.warning(
                    dialog, "저장 불가",
                    "비교 결과 폴더를 알 수 없어 저장할 수 없습니다.",
                )
                return
            # Merge pending edits with existing overrides
            merged: dict[str, list] = dict(existing_overrides)
            for (pair_uuid_, page_a_), new_pb in pending_edits.items():
                _upsert_override(
                    merged,
                    pair_uuid_,
                    _new_override_entry(
                        page_a=page_a_,
                        page_b=new_pb,
                        reason="GUI dialog edit",
                        user="user",
                    ),
                )
            try:
                _save_overrides(overrides_path, merged)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to save manual page overrides")
                QMessageBox.critical(
                    dialog, "저장 실패",
                    f"override JSON 저장 중 오류:\n{exc}",
                )
                return
            logger.info(
                "Saved %d manual page override(s) to %s",
                len(pending_edits), overrides_path,
            )
            QMessageBox.information(
                dialog, "저장 완료",
                f"{len(pending_edits)}개 수동 매칭이 저장되었습니다.\n"
                f"위치: {overrides_path}\n\n"
                "다음 비교 실행 시 자동으로 적용됩니다.",
            )
            dialog.accept()

        save_btn.clicked.connect(save_handler)

        button_row = QHBoxLayout()
        button_row.addWidget(save_btn)
        button_row.addStretch()
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(dialog.accept)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

        dialog.exec()

    def _is_lightweight_viewer_active_v2(self) -> bool:
        if not QT_QUICK_AVAILABLE:
            return False
        if DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
            return True
        action = getattr(self, "act_lightweight_viewer_v2", None)
        return bool(action is not None and action.isChecked())

    def _set_lightweight_viewer_visible_v2(self, enabled: bool) -> None:
        if not QT_QUICK_AVAILABLE:
            enabled = False
        elif DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
            enabled = True
        for widget, visible in (
            (getattr(self, "preview_before_v2", None), not enabled),
            (getattr(self, "preview_after_v2", None), not enabled),
            (getattr(self, "preview_before_lightweight_v2", None), enabled),
            (getattr(self, "preview_after_lightweight_v2", None), enabled),
        ):
            if widget is None:
                continue
            try:
                widget.setVisible(visible)
            except Exception:
                logger.debug("Failed to update viewer visibility", exc_info=True)

    def _maybe_auto_enable_lightweight_viewer_v2(self) -> None:
        """Phase G3.1 — Auto-enable lightweight viewer when sources are
        DXF/DWG and the user hasn't already picked a preference.

        Skipped for PDF (legacy viewer is the verified path) and for any
        session where the user has manually toggled.
        """

        if not hasattr(self, "act_lightweight_viewer_v2"):
            return
        if not QT_QUICK_AVAILABLE:
            self._set_lightweight_viewer_visible_v2(False)
            return
        if DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
            if not self.act_lightweight_viewer_v2.isChecked():
                self._auto_toggle_in_progress_v2 = True
                try:
                    self.act_lightweight_viewer_v2.setChecked(True)
                finally:
                    self._auto_toggle_in_progress_v2 = False
            self._set_lightweight_viewer_visible_v2(True)
            return
        if self._user_picked_lightweight_v2:
            logger.debug("Skipping auto-enable: user already picked lightweight pref")
            return
        if self.act_lightweight_viewer_v2.isChecked():
            return  # already on; nothing to do

        # Inspect the source kinds — DXF/DWG only
        if not self._result:
            return
        request = getattr(self._result, "request", None)
        if request is None:
            return
        sources_to_check = []
        for attr in ("source_a", "source_b"):
            val = getattr(request, attr, None)
            if val:
                sources_to_check.append(str(val))
        if not sources_to_check:
            return

        from pathlib import Path as _Path
        has_pdf = any(_Path(s).suffix.lower() == ".pdf" for s in sources_to_check if s)
        has_cad = any(
            _Path(s).suffix.lower() in {".dxf", ".dwg"} for s in sources_to_check if s
        )
        # For folder inputs the suffix isn't .pdf/.dxf — sniff the first few
        # files in the folder.
        if not has_pdf and not has_cad:
            for s in sources_to_check:
                p = _Path(s)
                if p.is_dir():
                    found_files = [f.suffix.lower() for f in p.iterdir() if f.is_file()][:20]
                    if any(sfx == ".pdf" for sfx in found_files):
                        has_pdf = True
                    if any(sfx in {".dxf", ".dwg"} for sfx in found_files):
                        has_cad = True

        # Decision matrix (Phase G2.7 — PDF support):
        #   CAD only        -> auto-enable lightweight (Canvas vector path)
        #   PDF only        -> auto-enable lightweight (Qt PDF background)
        #   mixed CAD + PDF -> auto-enable lightweight (it handles both)
        #   neither         -> stay on legacy
        if not has_pdf and not has_cad:
            logger.info("Lightweight auto-enable skipped: no recognised source kind")
            return
        # Probe Qt PDF availability before auto-enabling for PDF sources;
        # if missing fall back to the legacy raster viewer so the user
        # still sees something.
        if has_pdf and not has_cad:
            try:
                from src.services.comparison.qt_pdf_adapter import is_qt_pdf_available
                if not is_qt_pdf_available():
                    logger.info(
                        "Lightweight auto-enable skipped: PDF-only source "
                        "but PySide6.QtPdf is unavailable"
                    )
                    return
            except Exception:  # noqa: BLE001
                logger.exception("qt_pdf_adapter probe failed; staying on legacy")
                return

        logger.info(
            "Auto-enabling lightweight viewer (has_pdf=%s, has_cad=%s)",
            has_pdf, has_cad,
        )
        self._auto_toggle_in_progress_v2 = True
        try:
            self.act_lightweight_viewer_v2.setChecked(True)
        finally:
            self._auto_toggle_in_progress_v2 = False
        # Surface a one-line hint in the status bar so the user knows
        # WHY the viewport changed.
        if hasattr(self, "lbl_status_v2"):
            current = self.lbl_status_v2.text()
            hint = " · 신형 벡터 뷰어 자동 활성 (Ctrl+L로 끄기)"
            if hint not in current:
                self.lbl_status_v2.setText(f"{current}{hint}")

    def _on_auto_error_v2(self, message: str) -> None:
        self._retire_active_worker_v2()
        self._set_v2_busy(False)
        if hasattr(self, "act_region_match_results_v2"):
            self.act_region_match_results_v2.setEnabled(False)
        self.progress_v2.setValue(0)
        self.lbl_status_v2.setText("오류")
        QMessageBox.critical(self, "도면 변경 비교", message)

    def _set_v2_busy(self, busy: bool) -> None:
        self.btn_cancel_v2.setEnabled(busy)
        self.btn_run_v2.setEnabled(
            not busy and self._is_valid_input_path_v2(self._source_a) and self._is_valid_input_path_v2(self._source_b)
        )
        self.chk_recursive_v2.setEnabled(not busy)

    def _load_drawing_rows_v2(self) -> None:
        self._drawing_rows = []
        if not self._result:
            return
        if self._dashboard:
            for row in self._dashboard.get("drawings", []):
                raw = _int_value(row.get("raw_change_count"))
                item = dict(row)
                item["grade"] = _change_grade(raw)
                item["top_layers"] = item.get("major_layers") or item.get("top_layers") or ""
                self._drawing_rows.append(item)
            self._drawing_rows.sort(
                key=lambda row: (
                    -float(row.get("priority_score") or 0.0),
                    -_int_value(row.get("raw_change_count")),
                    str(row.get("drawing_number") or ""),
                )
            )
            return
        csv_path = Path(self._result.executive_package.output_paths.get("drawing_change_brief_csv", ""))
        if not csv_path.exists():
            return
        failed = {
            item.candidate.source_a.identity.drawing_number or item.candidate.source_a.name
            for item in self._result.compare_summary.items
            if item.status == "failed" and item.candidate.source_a
        }
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            raw = _int_value(row.get("raw_change_count"))
            row["grade"] = _change_grade(raw, row.get("drawing_number") in failed)
            self._drawing_rows.append(row)
        self._drawing_rows.sort(
            key=lambda row: (-_int_value(row.get("raw_change_count")), str(row.get("drawing_number") or ""))
        )

    def _load_dashboard_v2(self) -> None:
        self._dashboard = {}
        if not self._result:
            return
        dashboard_path = self._result.executive_package.output_paths.get("review_dashboard_json")
        if not dashboard_path:
            dashboard_path = self._result.artifact_package.output_paths.get("review_dashboard_json")
        if not dashboard_path:
            return
        path = Path(dashboard_path)
        if not path.exists():
            return
        try:
            self._dashboard = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._dashboard = {}

    def _load_review_state_v2(self) -> None:
        self._review_state_path_v2 = None
        self._review_records_v2 = {}
        if not self._result:
            return
        path_text = (
            getattr(self._result, "review_state_path", "")
            or self._result.artifact_package.output_paths.get("review_state_json", "")
        )
        if not path_text:
            return
        self._review_state_path_v2 = Path(path_text)
        self._review_records_v2 = load_review_state(self._review_state_path_v2)

    def _reset_compare_session_viewer_state_v2(self, *, clear_ui: bool = False) -> None:
        """Clear per-comparison viewer state before loading a new result.

        These fields are derived from the active comparison only. Keeping them
        across runs leaks the previous pair/category/overlay selection into a
        newly opened file set.
        """

        prior_session = getattr(self, "_viewer_session", None)
        if prior_session is not None:
            try:
                prior_session.shutdown(wait=False)
            except Exception:
                logger.debug("Previous viewer session shutdown failed", exc_info=True)

        self._viewer_session = None  # type: ignore[assignment]
        self._viewer_manifest = {}
        self._viewer_manifest_path = None
        self._viewer_root = None
        self._viewer_pairs_by_id = {}
        self._viewer_overlay_cache = {}
        self._viewer_overlay_cache_order_v2 = []
        self._viewer_overlay_cache_bytes_by_pair_v2 = {}
        self._viewer_overlay_cache_total_bytes_v2 = 0
        self._viewer_overlay_cache_evictions_v2 = 0
        self._tile_manifest_cache_v2 = {}
        self._lightweight_raster_pairs = set()
        self._render_status_by_pair = {}
        self._active_issue_by_zone = {}
        self._active_all_overlays_by_zone = {}
        self._active_overlays_by_zone = {}
        self._active_row = None
        self._active_zone_id = ""
        self._active_pattern_filter_v2 = ""
        self._cancel_full_zone_tree_rebuild_v2("session_reset", bump_generation=True)
        self._cancel_visible_tile_window_v2("session_reset", bump_generation=True)
        self._defer_next_initial_zone_heavy_render_v2 = None
        self._initial_zone_heavy_render_generation_v2 += 1
        self._lightweight_pair_load_generation_v2 += 1
        self._pdf_prewarm_generation_v2 += 1
        self._selected_zone_render_generation_v2 += 1
        self._active_zone_render_request_v2 = None
        self._zone_full_detail_started_request_v2 = None
        self._zone_categories_v2.clear()
        self._active_category_filter_v2 = "전체"
        self._user_picked_category_filter_v2 = False
        self._pending_render_request_v2 = None
        self._pending_zone_render_request_v2 = None
        proc = getattr(self, "_zone_vector_qprocess", None)
        if proc is not None:
            try:
                proc.finished.disconnect(self._on_zone_vector_finished_v2)
            except Exception:
                pass
            try:
                if proc.state() != QProcess.NotRunning:
                    proc.kill()
                    proc.waitForFinished(1000)
                proc.deleteLater()
            except Exception:
                logger.debug("Could not stop stale zone vector process", exc_info=True)
        self._zone_vector_qprocess = None
        self._zone_vector_paths.clear()
        self._zone_vector_pending = None
        self._zone_vector_result_json = None
        self._zone_vector_button_external = False
        self._v2_fidelity_by_pair_id = {}

        if not clear_ui:
            return

        for widget_name in (
            "drawing_list_v2",
            "top_issues_list_v2",
            "pattern_group_list_v2",
            "zone_list_v2",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                try:
                    widget.clear()
                except Exception:
                    logger.debug("Could not clear %s", widget_name, exc_info=True)

        if hasattr(self, "cmb_category_filter_v2"):
            previous = self.cmb_category_filter_v2.blockSignals(True)
            try:
                self.cmb_category_filter_v2.clear()
                self.cmb_category_filter_v2.addItem("전체")
                self.cmb_category_filter_v2.setCurrentIndex(0)
            finally:
                self.cmb_category_filter_v2.blockSignals(previous)
        if hasattr(self, "lbl_zone_progress_v2"):
            self.lbl_zone_progress_v2.setText("진행: -")
        if hasattr(self, "zone_detail_v2"):
            self.zone_detail_v2.setHtml("<p>Select a drawing and change zone.</p>")
        if hasattr(self, "zone_memo_v2"):
            self.zone_memo_v2.clear()
        if hasattr(self, "btn_save_memo_v2"):
            self.btn_save_memo_v2.setEnabled(False)
        if hasattr(self, "btn_zone_vector_v2"):
            self.btn_zone_vector_v2.setEnabled(False)
        if hasattr(self, "btn_export_selected_cloud_v2"):
            self.btn_export_selected_cloud_v2.setEnabled(False)

        for viewport_name in ("preview_before_v2", "preview_after_v2"):
            viewport = getattr(self, viewport_name, None)
            if viewport is not None:
                try:
                    viewport.load_preview("", [], before=viewport_name == "preview_before_v2")
                    viewport.set_fidelity_state("relative_only")
                except Exception:
                    logger.debug("Could not reset %s", viewport_name, exc_info=True)

        for viewport_name in (
            "preview_before_lightweight_v2",
            "preview_after_lightweight_v2",
        ):
            viewport = getattr(self, viewport_name, None)
            if viewport is not None:
                try:
                    viewport.load_scene_pack(None, empty_notice="Run a comparison to display drawings.")
                    viewport.set_overlays([], [])
                    viewport.set_fidelity_state("relative_only")
                except Exception:
                    logger.debug("Could not reset %s", viewport_name, exc_info=True)

    def _load_viewer_manifest_v2(self) -> None:
        self._reset_compare_session_viewer_state_v2(clear_ui=False)
        if not self._result:
            return
        viewer_path = ""
        if getattr(self._result, "viewer_package", None):
            viewer_path = self._result.viewer_package.output_paths.get("viewer_manifest_json", "")
        if not viewer_path:
            viewer_path = self._result.artifact_package.output_paths.get("viewer_manifest_json", "")
        if not viewer_path:
            return
        path = Path(viewer_path)
        if not path.exists():
            return
        try:
            self._viewer_manifest_path = path
            self._viewer_root = path.parent
            self._viewer_manifest = json.loads(path.read_text(encoding="utf-8"))
            self._viewer_pairs_by_id = {
                str(pair.get("pair_id") or ""): pair
                for pair in self._viewer_manifest.get("pairs", [])
                if isinstance(pair, dict) and pair.get("pair_id")
            }
            for pair_id, pair in self._viewer_pairs_by_id.items():
                status = "ready" if pair.get("after_image") and pair.get("after_transform") else "not_requested"
                if status == "ready" and _viewer_pair_is_pdf(pair):
                    status = "pdf_render"
                if pair.get("tile_manifest") or pair.get("lod_tile_count"):
                    status = "tile_ready"
                if str(pair.get("render_status") or "").lower() in {"render_failed", "failed"}:
                    status = "failed"
                self._render_status_by_pair[pair_id] = status
        except Exception:
            self._viewer_manifest = {}
            self._viewer_pairs_by_id = {}
            self._render_status_by_pair = {}
        # Phase F P0 — load the v2 manifest sidecar (if present) so the new
        # fidelity badge + watermark layer can be wired up. Failure here is
        # non-fatal; the viewport falls back to the implicit relative_only
        # default and the orange watermark warns the reviewer.
        self._v2_fidelity_by_pair_id: dict[str, tuple[str, str]] = {}
        try:
            from src.services.comparison.viewer_manifest_v2 import load_manifest_v2
            v2_path = path.with_name(path.stem + "_v2.json")
            if v2_path.exists():
                v2 = load_manifest_v2(v2_path)
                self._v2_fidelity_by_pair_id = {
                    p.pair_id: (p.background_fidelity, p.render_job_status)
                    for p in v2.pairs if p.pair_id
                }
                logger.info(
                    "Loaded viewer_manifest_v2: %d pairs, source_kind=%s, overlay_space=%s",
                    len(v2.pairs), v2.source_kind, v2.overlay_space,
                )
        except Exception as exc:
            logger.debug("v2 manifest sidecar not loaded: %s", exc)
            self._v2_fidelity_by_pair_id = {}

        # Phase G2.1 — load the v3 manifest sidecar and instantiate the
        # ViewerSession orchestrator. The session lazy-builds scene packs
        # in the background when the user selects pairs (G2.2 will wire
        # the lightweight viewport to consume the resulting RenderMode
        # state). Failure here is non-fatal; the v1/v2 paths still render.
        try:
            from src.services.comparison.viewer_manifest_v3 import (
                MANIFEST_FILENAME as V3_MANIFEST_FILENAME,
            )
            from src.services.comparison.viewer_session import ViewerSession
            v3_path = path.with_name(V3_MANIFEST_FILENAME)
            if v3_path.exists():
                self._viewer_session = ViewerSession(
                    cache_root=None,        # use default AppData preview cache
                    max_workers=2,
                    on_state_change=self._on_viewer_session_state_change_v2,
                    on_zone_evidence=self._on_viewer_session_zone_evidence_v2,
                    on_progress=self._on_viewer_session_progress_v2,
                )
                v3 = self._viewer_session.load_manifest(v3_path)
                logger.info(
                    "ViewerSession loaded: pair=%s, source_kind=%s, "
                    "before_pack=%s, after_pack=%s",
                    v3.pair_uuid, v3.source_kind,
                    "set" if v3.before_scene_pack else "none",
                    "set" if v3.after_scene_pack else "none",
                )
        except Exception as exc:
            logger.debug("v3 viewer_session not loaded: %s", exc)
            self._viewer_session = None

    def _on_viewer_session_state_change_v2(self, pair_id: str, side: str, mode: str) -> None:
        """Phase G2.1 — Worker callback when ScenePack state transitions.

        Runs on a ViewerSession worker thread. Re-marshals to the GUI
        thread via ``QTimer.singleShot(0, ...)`` so we can safely touch
        QQuickWidget properties.
        """

        logger.info(
            "ViewerSession state change: pair=%s side=%s -> %s", pair_id, side, mode,
        )
        # Marshal to GUI thread.
        try:
            QTimer.singleShot(
                0,
                lambda p=pair_id, s=side, m=mode: self._apply_session_state_to_viewport_v2(p, s, m),
            )
        except Exception:
            logger.exception("Failed to marshal viewer session state change")

    # -----------------------------------------------------------------
    # Phase H multi-page navigation
    # -----------------------------------------------------------------

    def _update_page_nav_v2(self, viewer_pair: dict) -> None:
        """Phase H — show/hide the page navigator based on the active
        viewer_pair's matched page count.

        - 0 or 1 page pairs (DXF / single-page PDF / single-match): hidden
        - >= 2 page pairs: visible with "1/N" label + prev/next buttons
        """

        pairs = list(viewer_pair.get("page_match_pairs") or [])
        is_multi = len(pairs) >= 2
        for w_name in (
            "btn_page_nav_prev_v2", "btn_page_nav_next_v2", "lbl_page_nav_v2",
        ):
            widget = getattr(self, w_name, None)
            if widget is not None:
                widget.setVisible(is_multi)
        if not is_multi:
            return
        # Clamp the active index in case a new pair has fewer matches
        idx = max(0, min(self._active_pdf_page_index_v2, len(pairs) - 1))
        self._active_pdf_page_index_v2 = idx
        cur = pairs[idx] if 0 <= idx < len(pairs) else {"page_a": 0, "page_b": 0}
        # 1-based labels for human readability ("Page 1 vs Page 3 (1/3)")
        label = (
            f"📄 페이지 A {int(cur.get('page_a', 0)) + 1} ↔ "
            f"B {int(cur.get('page_b', 0)) + 1} "
            f"({idx + 1}/{len(pairs)} 매칭)"
        )
        self.lbl_page_nav_v2.setText(label)
        # Disable prev/next at boundaries — wrap-around would surprise the user
        self.btn_page_nav_prev_v2.setEnabled(idx > 0)
        self.btn_page_nav_next_v2.setEnabled(idx < len(pairs) - 1)

    def _step_pdf_page_pair_v2(self, delta: int) -> None:
        """Phase H — Move ±1 through the matched page pairs and re-render.

        ``delta`` of -1 = previous page pair, +1 = next. Out-of-range
        steps are clamped (no wrap) so the UI doesn't surprise the user.
        """

        viewer_pair = self._viewer_pairs_by_id.get(
            str((self._active_row or {}).get("pair_id") or ""), {},
        )
        pairs = list(viewer_pair.get("page_match_pairs") or [])
        if len(pairs) < 2:
            return
        new_idx = max(0, min(len(pairs) - 1, self._active_pdf_page_index_v2 + int(delta)))
        if new_idx == self._active_pdf_page_index_v2:
            return  # at boundary
        self._active_pdf_page_index_v2 = new_idx
        target = pairs[new_idx]
        self._show_pdf_page_pair_v2(
            int(target.get("page_a", 0) or 0),
            int(target.get("page_b", 0) or 0),
        )

    def _apply_active_pdf_page_pair_to_viewer_pair_v2(self, viewer_pair: dict) -> None:
        if not isinstance(viewer_pair, dict):
            return
        pairs = list(viewer_pair.get("page_match_pairs") or [])
        if not pairs:
            return
        idx = max(0, min(int(self._active_pdf_page_index_v2 or 0), len(pairs) - 1))
        self._active_pdf_page_index_v2 = idx
        current = pairs[idx] if 0 <= idx < len(pairs) else {}
        try:
            viewer_pair["page_a"] = int(current.get("page_a", 0) or 0)
            viewer_pair["page_b"] = int(current.get("page_b", 0) or 0)
        except (TypeError, ValueError):
            viewer_pair["page_a"] = 0
            viewer_pair["page_b"] = 0

    def _visible_overlays_for_pdf_page_v2(
        self,
        pair_id: str,
        viewer_pair: dict,
        overlays: Optional[list[dict]] = None,
        *,
        load_when_empty: bool = True,
    ) -> list[dict]:
        base = list(overlays) if overlays is not None else list(
            self._active_all_overlays_by_zone.values()
        )
        if not base and load_when_empty:
            paged_visible = self._viewer_visible_overlays_from_page_store_v2(pair_id, viewer_pair)
            if paged_visible is not None:
                return paged_visible
            base = self._viewer_overlays_for_pair_v2(pair_id)
        if not _viewer_pair_is_pdf(viewer_pair):
            return base
        page_a = _int_value(viewer_pair.get("page_a", viewer_pair.get("page", 0)), 0)
        page_b = _int_value(viewer_pair.get("page_b", viewer_pair.get("page", 0)), 0)
        return _filter_overlays_by_pdf_pages(base, page_a, page_b)

    def _set_active_overlays_v2(self, overlays: list[dict]) -> None:
        self._active_overlays_by_zone = {
            str(overlay.get("zone_id") or ""): overlay
            for overlay in overlays
            if isinstance(overlay, dict) and overlay.get("zone_id")
        }

    def _top_issue_overlays_for_selection_v2(self, row: dict, pair_id: str) -> list[dict]:
        overlays: list[dict] = []
        for issue in list((row or {}).get("top_issues") or []):
            if not isinstance(issue, dict):
                continue
            zone_id = str(issue.get("zone_id") or "")
            if not zone_id:
                continue
            overlay = dict(issue)
            overlay.setdefault("pair_id", pair_id)
            overlay.setdefault("pair_uuid", pair_id)
            overlay.setdefault("label", zone_id)
            overlay.setdefault("selected_for_review", True)
            overlay.setdefault("change_type", issue.get("change_type") or "modified")
            overlay.setdefault("severity", issue.get("severity") or issue.get("severity_ko") or "")
            overlay.setdefault("raw_change_count", issue.get("raw_change_count") or issue.get("change_count") or 0)
            overlays.append(overlay)
        return overlays

    def _preview_overlays_for_selection_v2(
        self,
        preview: Optional[PreviewArtifact],
    ) -> list[dict]:
        if not preview:
            return []
        out: list[dict] = []
        for overlay in getattr(preview, "zone_overlays", []) or []:
            if hasattr(overlay, "to_dict"):
                try:
                    out.append(overlay.to_dict())
                    continue
                except Exception:
                    logger.debug("Failed to convert preview overlay", exc_info=True)
            if isinstance(overlay, dict):
                out.append(dict(overlay))
        return out

    def _initial_overlays_for_pair_selection_v2(
        self,
        pair_id: str,
        row: dict,
        preview: Optional[PreviewArtifact],
        viewer_pair: dict,
    ) -> tuple[list[dict], bool, str]:
        limit = max(1, int(GUI_FIRST_SELECTION_ZONE_LIMIT))
        top_issue_overlays = self._top_issue_overlays_for_selection_v2(row, pair_id)
        if top_issue_overlays:
            zone_count = max(
                _int_value(row.get("zone_count")),
                _int_value(viewer_pair.get("overlay_total_count")),
                len(top_issue_overlays),
            )
            return top_issue_overlays[:limit], zone_count > min(limit, len(top_issue_overlays)), "top_issues"

        preview_overlays = self._preview_overlays_for_selection_v2(preview)
        if preview_overlays:
            return preview_overlays[:limit], len(preview_overlays) > limit, "preview"

        cached = self._viewer_overlay_cache.get(pair_id)
        if cached is not None:
            self._touch_viewer_overlay_cache_v2(pair_id)
            return list(cached[:limit]), len(cached) > limit, "cache"

        paged_initial = self._viewer_initial_overlays_from_page_store_v2(pair_id, limit)
        if paged_initial is not None:
            declared = max(
                _int_value(row.get("zone_count")),
                _int_value(viewer_pair.get("overlay_total_count")),
                self._viewer_declared_overlay_count_for_pair_v2(pair_id, row=row, viewer_pair=viewer_pair),
                len(paged_initial),
            )
            return paged_initial, declared > len(paged_initial), "paged_overlay_store"

        declared_overlay_count = max(
            _int_value(row.get("zone_count")),
            _int_value(viewer_pair.get("overlay_total_count")),
        )
        if declared_overlay_count > limit:
            return [], True, "overlay_json_deferred"

        overlay_json_bytes = self._overlay_json_file_size_for_pair_v2(pair_id)
        if declared_overlay_count <= 0 and overlay_json_bytes > GUI_UNKNOWN_OVERLAY_JSON_DEFER_BYTES:
            return [], True, "overlay_json_deferred_large_unknown"

        overlays = self._viewer_overlays_for_pair_v2(pair_id)
        return overlays[:limit], len(overlays) > limit, "overlay_json"

    def _touch_viewer_overlay_cache_v2(self, pair_id: str) -> None:
        if not pair_id:
            return
        try:
            self._viewer_overlay_cache_order_v2.remove(pair_id)
        except ValueError:
            pass
        self._viewer_overlay_cache_order_v2.append(pair_id)

    def _estimate_overlay_cache_bytes_v2(self, overlays: list[dict]) -> int:
        total = 0
        for overlay in overlays:
            total += self._estimate_overlay_value_bytes_v2(overlay)
        return total

    @staticmethod
    def _estimate_overlay_value_bytes_v2(value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return 1
        if isinstance(value, (int, float)):
            return 8
        if isinstance(value, str):
            return len(value.encode("utf-8", errors="ignore"))
        if isinstance(value, dict):
            total = 256
            for key, item in value.items():
                total += len(str(key).encode("utf-8", errors="ignore"))
                total += DrawingCompareWorkbenchV2._estimate_overlay_value_bytes_v2(item)
            return total
        if isinstance(value, (list, tuple)):
            return 64 + sum(DrawingCompareWorkbenchV2._estimate_overlay_value_bytes_v2(item) for item in value)
        return len(str(value).encode("utf-8", errors="ignore"))

    def _cache_viewer_overlays_v2(self, pair_id: str, overlays: list[dict]) -> None:
        if not pair_id:
            return
        previous_bytes = int(self._viewer_overlay_cache_bytes_by_pair_v2.get(pair_id, 0))
        overlay_bytes = self._estimate_overlay_cache_bytes_v2(overlays)
        self._viewer_overlay_cache[pair_id] = overlays
        self._viewer_overlay_cache_bytes_by_pair_v2[pair_id] = overlay_bytes
        self._viewer_overlay_cache_total_bytes_v2 = max(
            0,
            int(self._viewer_overlay_cache_total_bytes_v2) - previous_bytes + overlay_bytes,
        )
        self._touch_viewer_overlay_cache_v2(pair_id)
        self._evict_viewer_overlay_cache_if_needed_v2()

    def _evict_viewer_overlay_cache_if_needed_v2(self) -> None:
        active_pair = str((self._active_row or {}).get("pair_id") or "")
        pair_limit = max(1, int(GUI_OVERLAY_CACHE_PAIR_LIMIT))
        byte_limit = max(1, int(GUI_OVERLAY_CACHE_BYTE_LIMIT))
        while self._viewer_overlay_cache_order_v2 and (
            len(self._viewer_overlay_cache_order_v2) > pair_limit
            or self._viewer_overlay_cache_total_bytes_v2 > byte_limit
        ):
            reason = (
                "pair_limit"
                if len(self._viewer_overlay_cache_order_v2) > pair_limit
                else "byte_limit"
            )
            evict_pair = self._viewer_overlay_cache_order_v2.pop(0)
            if evict_pair == active_pair and self._viewer_overlay_cache_order_v2:
                self._viewer_overlay_cache_order_v2.append(evict_pair)
                continue
            if evict_pair in self._viewer_overlay_cache:
                evicted_bytes = int(self._viewer_overlay_cache_bytes_by_pair_v2.pop(evict_pair, 0))
                self._viewer_overlay_cache.pop(evict_pair, None)
                self._viewer_overlay_cache_total_bytes_v2 = max(
                    0,
                    self._viewer_overlay_cache_total_bytes_v2 - evicted_bytes,
                )
                self._viewer_overlay_cache_evictions_v2 += 1
                if self._viewer_root:
                    append_viewer_perf_event(
                        self._viewer_root,
                        "viewer_overlay_cache_evict",
                        pair_uuid=evict_pair,
                        overlay_cache_pair_limit=pair_limit,
                        overlay_cache_byte_limit=byte_limit,
                        overlay_cache_evicted_bytes=evicted_bytes,
                        overlay_cache_total_bytes=self._viewer_overlay_cache_total_bytes_v2,
                        overlay_cache_pair_count=len(self._viewer_overlay_cache),
                        overlay_cache_eviction_reason=reason,
                        overlay_cache_eviction_count=self._viewer_overlay_cache_evictions_v2,
                    )
            if (
                len(self._viewer_overlay_cache_order_v2) == 1
                and self._viewer_overlay_cache_order_v2[0] == active_pair
                and self._viewer_overlay_cache_total_bytes_v2 > byte_limit
            ):
                break

    def _schedule_full_zone_tree_rebuild_v2(self, pair_id: str) -> None:
        if not pair_id:
            return
        self._cancel_full_zone_tree_rebuild_v2("superseded", bump_generation=True)
        generation = self._zone_tree_rebuild_generation_v2
        self._pending_full_zone_tree_pair_id_v2 = pair_id
        QTimer.singleShot(
            GUI_FULL_ZONE_TREE_IDLE_DELAY_MS,
            lambda p=pair_id, g=generation: self._run_full_zone_tree_rebuild_v2(p, g),
        )

    def _full_zone_tree_request_is_current_v2(self, pair_id: str, generation: int) -> bool:
        if int(generation) != int(self._zone_tree_rebuild_generation_v2):
            return False
        if str(pair_id or "") != str((self._active_row or {}).get("pair_id") or ""):
            return False
        pending = str(self._pending_full_zone_tree_pair_id_v2 or "")
        return not pending or pending == str(pair_id or "")

    def _cancel_full_zone_tree_rebuild_v2(self, reason: str, *, bump_generation: bool = True) -> None:
        had_pending = bool(
            self._pending_full_zone_tree_pair_id_v2
            or self._full_zone_tree_chunk_state_v2
            or self._full_zone_tree_overlay_worker_v2
            or self._full_zone_tree_plan_worker_v2
        )
        if bump_generation:
            self._zone_tree_rebuild_generation_v2 += 1
        pair_id = str(
            (self._full_zone_tree_chunk_state_v2 or {}).get("pair_id")
            or self._pending_full_zone_tree_pair_id_v2
            or ""
        )
        self._pending_full_zone_tree_pair_id_v2 = ""
        self._full_zone_tree_chunk_state_v2 = None
        if self._full_zone_tree_overlay_worker_v2 is not None:
            self._full_zone_tree_overlay_worker_v2.cancel()
            self._retire_full_zone_tree_overlay_worker_v2()
        if self._full_zone_tree_plan_worker_v2 is not None:
            self._full_zone_tree_plan_worker_v2.cancel()
            self._retire_full_zone_tree_plan_worker_v2()
        if had_pending and self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "full_zone_tree_rebuild_cancelled",
                pair_uuid=pair_id,
                reason_code=str(reason or "cancelled"),
                generation=int(self._zone_tree_rebuild_generation_v2),
            )

    def _run_full_zone_tree_rebuild_v2(self, pair_id: str, generation: int) -> None:
        if not self._full_zone_tree_request_is_current_v2(pair_id, generation):
            return
        started = perf_counter()
        self._pending_full_zone_tree_pair_id_v2 = pair_id
        viewer_pair = self._viewer_pairs_by_id.get(pair_id, {})
        if (
            str((self._active_row or {}).get("pair_id") or "") == str(pair_id)
            and isinstance(viewer_pair, dict)
            and _viewer_pair_is_pdf(viewer_pair)
        ):
            self._apply_active_pdf_page_pair_to_viewer_pair_v2(viewer_pair)
        if pair_id in self._viewer_overlay_cache:
            self._touch_viewer_overlay_cache_v2(pair_id)
            self._continue_full_zone_tree_rebuild_with_overlays_v2(
                pair_id=pair_id,
                generation=generation,
                viewer_pair=viewer_pair,
                full_overlays=list(self._viewer_overlay_cache.get(pair_id) or []),
                visible=None,
                started=started,
                overlay_load_ms=0.0,
                overlay_json_bytes=int(self._viewer_overlay_cache_bytes_by_pair_v2.get(pair_id, 0)),
                overlay_load_worker=False,
            )
            return
        overlay_path = self._viewer_overlay_json_path_for_pair_v2(pair_id)
        if overlay_path is not None and overlay_path.exists():
            self._start_full_zone_tree_overlay_load_worker_v2(
                pair_id=pair_id,
                generation=generation,
                viewer_pair=viewer_pair,
                overlay_path=overlay_path,
                overlay_pages_manifest_path=self._viewer_overlay_pages_manifest_path_for_pair_v2(pair_id),
                started=started,
            )
            return

        # Test/legacy fallback: synthetic rows may provide overlays only through
        # an overridden method and no overlay_json artifact path.
        full_overlays = self._viewer_overlays_for_pair_v2(pair_id)
        self._continue_full_zone_tree_rebuild_with_overlays_v2(
            pair_id=pair_id,
            generation=generation,
            viewer_pair=viewer_pair,
            full_overlays=full_overlays,
            visible=None,
            started=started,
            overlay_load_ms=round((perf_counter() - started) * 1000.0, 3),
            overlay_json_bytes=0,
            overlay_load_worker=False,
        )

    def _viewer_overlay_json_path_for_pair_v2(self, pair_id: str) -> Optional[Path]:
        pair = self._viewer_pairs_by_id.get(pair_id, {})
        overlay_path = str(pair.get("overlay_json") or "")
        if not overlay_path:
            return None
        return _resolve_viewer_artifact_path(overlay_path, self._viewer_root)

    def _viewer_overlay_pages_manifest_path_for_pair_v2(self, pair_id: str) -> Optional[Path]:
        pair = self._viewer_pairs_by_id.get(pair_id, {})
        manifest_path = str(pair.get("overlay_pages_manifest") or "")
        if not manifest_path:
            return None
        return _resolve_viewer_artifact_path(manifest_path, self._viewer_root)

    def _viewer_overlay_page_store_for_pair_v2(self, pair_id: str) -> Optional[OverlayPageStore]:
        manifest_path = self._viewer_overlay_pages_manifest_path_for_pair_v2(pair_id)
        if manifest_path is None or not manifest_path.exists():
            return None
        return OverlayPageStore(manifest_path)

    def _viewer_initial_overlays_from_page_store_v2(
        self, pair_id: str, limit: int,
    ) -> Optional[list[dict]]:
        store = self._viewer_overlay_page_store_for_pair_v2(pair_id)
        if store is None:
            return None
        return list(store.iter_initial(max(0, int(limit))))

    def _viewer_visible_overlays_from_page_store_v2(
        self, pair_id: str, viewer_pair: dict,
    ) -> Optional[list[dict]]:
        store = self._viewer_overlay_page_store_for_pair_v2(pair_id)
        if store is None or not _viewer_pair_is_pdf(viewer_pair):
            return None
        page_a = _int_value(viewer_pair.get("page_a", viewer_pair.get("page", 0)), 0)
        page_b = _int_value(viewer_pair.get("page_b", viewer_pair.get("page", 0)), 0)
        return list(store.iter_visible_pdf_pages(page_a, page_b))

    def _viewer_declared_overlay_count_for_pair_v2(
        self,
        pair_id: str,
        *,
        row: Optional[dict] = None,
        viewer_pair: Optional[dict] = None,
    ) -> int:
        if row is None:
            for candidate in list(getattr(self, "_drawing_rows", []) or []):
                if isinstance(candidate, dict) and str(candidate.get("pair_id") or "") == str(pair_id):
                    row = candidate
                    break
        pair = viewer_pair if isinstance(viewer_pair, dict) else self._viewer_pairs_by_id.get(pair_id, {})
        declared = max(
            _int_value((row or {}).get("zone_count")),
            _int_value((row or {}).get("raw_change_count")),
            _int_value((pair or {}).get("overlay_total_count")),
        )
        cache = getattr(self, "_viewer_overlay_cache", {}) or {}
        cached = cache.get(pair_id) or []
        declared = max(declared, len(cached))
        store = self._viewer_overlay_page_store_for_pair_v2(pair_id)
        if store is not None:
            declared = max(declared, int(store.overlay_count))
        return int(declared)

    def _review_record_counts_for_pair_v2(self, pair_id: str) -> tuple[int, int]:
        prefix = f"{pair_id}:"
        done = 0
        confirmed = 0
        for key, record in (self._review_records_v2 or {}).items():
            if not str(key).startswith(prefix):
                continue
            status = normalize_review_status(record.status)
            if status != "needs_review":
                done += 1
            if status == "confirmed":
                confirmed += 1
        return done, confirmed

    def _start_full_zone_tree_overlay_load_worker_v2(
        self,
        *,
        pair_id: str,
        generation: int,
        viewer_pair: dict,
        overlay_path: Path,
        overlay_pages_manifest_path: Optional[Path] = None,
        started: float,
    ) -> None:
        if self._full_zone_tree_overlay_worker_v2 is not None:
            self._full_zone_tree_overlay_worker_v2.cancel()
            self._retire_full_zone_tree_overlay_worker_v2()
        worker = FullZoneTreeOverlayLoadWorker(
            pair_id=pair_id,
            generation=generation,
            overlay_path=overlay_path,
            viewer_pair=viewer_pair,
            overlay_pages_manifest_path=overlay_pages_manifest_path,
        )
        worker.loaded.connect(
            lambda p, g, payload, s=started, vp=dict(viewer_pair): (
                self._on_full_zone_tree_overlay_loaded_v2(p, g, payload, vp, s)
            )
        )
        worker.failed.connect(self._on_full_zone_tree_overlay_failed_v2)
        self._full_zone_tree_overlay_worker_v2 = worker
        worker.start()

    def _on_full_zone_tree_overlay_loaded_v2(
        self,
        pair_id: str,
        generation: int,
        payload: object,
        viewer_pair: dict,
        started: float,
    ) -> None:
        sender = self.sender()
        self._retire_full_zone_tree_overlay_worker_v2(sender if isinstance(sender, QThread) else None)
        if not self._full_zone_tree_request_is_current_v2(pair_id, generation):
            return
        data = payload if isinstance(payload, dict) else {}
        full_overlays = data.get("overlays") if isinstance(data.get("overlays"), list) else []
        visible = data.get("visible") if isinstance(data.get("visible"), list) else None
        overlay_load_strategy = str(data.get("overlay_load_strategy") or "overlay_json")
        declared_overlay_count = max(
            int(data.get("declared_overlay_count") or 0),
            len(full_overlays),
        )
        materialized_overlay_count = int(data.get("materialized_overlay_count") or len(full_overlays))
        cache_is_complete = materialized_overlay_count >= declared_overlay_count
        if overlay_load_strategy != "paged_overlay_store" or cache_is_complete:
            self._cache_viewer_overlays_v2(pair_id, full_overlays)
        self._continue_full_zone_tree_rebuild_with_overlays_v2(
            pair_id=pair_id,
            generation=generation,
            viewer_pair=viewer_pair,
            full_overlays=full_overlays,
            visible=visible,
            started=started,
            overlay_load_ms=float(data.get("overlay_load_ms") or 0.0),
            overlay_json_bytes=int(data.get("overlay_json_bytes") or 0),
            overlay_load_worker=bool(data.get("overlay_load_worker")),
            overlay_load_strategy=overlay_load_strategy,
            overlay_page_count=int(data.get("overlay_page_count") or 0),
            overlay_page_files_read=int(data.get("overlay_page_files_read") or 0),
            overlay_page_files_skipped=int(data.get("overlay_page_files_skipped") or 0),
            declared_overlay_count=declared_overlay_count,
            materialized_overlay_count=materialized_overlay_count,
        )

    def _on_full_zone_tree_overlay_failed_v2(self, pair_id: str, generation: int, message: str) -> None:
        sender = self.sender()
        self._retire_full_zone_tree_overlay_worker_v2(sender if isinstance(sender, QThread) else None)
        if not self._full_zone_tree_request_is_current_v2(pair_id, generation):
            return
        self._pending_full_zone_tree_pair_id_v2 = ""
        if self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "full_zone_tree_rebuild",
                pair_uuid=pair_id,
                elapsed_ms=0.0,
                overlay_count=0,
                visible_overlay_count=0,
                chunked=False,
                chunk_count=0,
                max_chunk_elapsed_ms=0.0,
                overlay_load_worker=True,
                error_message=str(message or "overlay_load_failed"),
            )

    def _continue_full_zone_tree_rebuild_with_overlays_v2(
        self,
        *,
        pair_id: str,
        generation: int,
        viewer_pair: dict,
        full_overlays: list[dict],
        visible: Optional[list[dict]],
        started: float,
        overlay_load_ms: float,
        overlay_json_bytes: int,
        overlay_load_worker: bool,
        overlay_load_strategy: str = "overlay_json",
        overlay_page_count: int = 0,
        overlay_page_files_read: int = 0,
        overlay_page_files_skipped: int = 0,
        declared_overlay_count: int = 0,
        materialized_overlay_count: Optional[int] = None,
    ) -> None:
        if not self._full_zone_tree_request_is_current_v2(pair_id, generation):
            return
        if not full_overlays:
            self._pending_full_zone_tree_pair_id_v2 = ""
            return
        effective_declared_overlay_count = max(int(declared_overlay_count or 0), len(full_overlays))
        effective_materialized_overlay_count = (
            int(materialized_overlay_count)
            if materialized_overlay_count is not None
            else len(full_overlays)
        )
        self._active_all_overlays_by_zone = {
            str(overlay.get("zone_id") or ""): overlay
            for overlay in full_overlays
            if isinstance(overlay, dict) and overlay.get("zone_id")
        }
        if visible is None:
            visible = self._visible_overlays_for_pdf_page_v2(
                pair_id,
                viewer_pair,
                full_overlays,
                load_when_empty=False,
            )
        self._set_active_overlays_v2(visible)
        if self._is_lightweight_viewer_active_v2():
            try:
                self._push_overlays_to_lightweight_v2(pair_id, focus_zone_id=self._active_zone_id or "")
            except Exception:
                logger.debug("Failed to push refreshed full-tree overlays to lightweight viewer", exc_info=True)
        active_zone = self._active_zone_id
        if len(visible) >= int(GUI_FULL_ZONE_TREE_CHUNK_ZONE_THRESHOLD):
            self._start_full_zone_tree_chunked_rebuild_v2(
                pair_id=pair_id,
                generation=generation,
                visible=visible,
                full_overlay_count=effective_declared_overlay_count,
                active_zone=active_zone,
                started=started,
                overlay_load_ms=overlay_load_ms,
                overlay_json_bytes=overlay_json_bytes,
                overlay_load_worker=overlay_load_worker,
                overlay_load_strategy=overlay_load_strategy,
                overlay_page_count=overlay_page_count,
                overlay_page_files_read=overlay_page_files_read,
                overlay_page_files_skipped=overlay_page_files_skipped,
                materialized_overlay_count=effective_materialized_overlay_count,
            )
            return
        self._compute_zone_categories_for_pair_v2(pair_id, visible)
        plan_started = perf_counter()
        self._populate_zone_list_v2(self._preview_by_pair.get(pair_id), visible, prefer_overlays=True)
        plan_build_ms = round((perf_counter() - plan_started) * 1000.0, 3)
        self._refresh_zone_list_filter_v2()
        if active_zone:
            was_blocked = self.zone_list_v2.blockSignals(True)
            try:
                self._select_zone_in_list_v2(active_zone)
            finally:
                self.zone_list_v2.blockSignals(was_blocked)
        elif not self.zone_list_v2.currentItem():
            self._schedule_initial_zone_selection_v2(pair_id)
        self._update_review_progress_v2()
        self._update_category_summary_v2()
        self._pending_full_zone_tree_pair_id_v2 = ""
        if self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "full_zone_tree_rebuild",
                pair_uuid=pair_id,
                elapsed_ms=round((perf_counter() - started) * 1000.0, 3),
                overlay_count=effective_declared_overlay_count,
                visible_overlay_count=len(visible),
                materialized_overlay_count=effective_materialized_overlay_count,
                chunked=False,
                chunk_count=1,
                max_chunk_elapsed_ms=round((perf_counter() - started) * 1000.0, 3),
                overlay_load_ms=round(float(overlay_load_ms or 0.0), 3),
                overlay_json_bytes=int(overlay_json_bytes or 0),
                overlay_load_worker=bool(overlay_load_worker),
                overlay_load_strategy=str(overlay_load_strategy or ""),
                overlay_page_count=int(overlay_page_count or 0),
                overlay_page_files_read=int(overlay_page_files_read or 0),
                overlay_page_files_skipped=int(overlay_page_files_skipped or 0),
                plan_build_ms=plan_build_ms,
                plan_build_worker=False,
                worker_spawned=bool(overlay_load_worker),
            )

    def _start_full_zone_tree_chunked_rebuild_v2(
        self,
        *,
        pair_id: str,
        generation: int,
        visible: list[dict],
        full_overlay_count: int,
        active_zone: str,
        started: float,
        overlay_load_ms: float = 0.0,
        overlay_json_bytes: int = 0,
        overlay_load_worker: bool = False,
        overlay_load_strategy: str = "overlay_json",
        overlay_page_count: int = 0,
        overlay_page_files_read: int = 0,
        overlay_page_files_skipped: int = 0,
        materialized_overlay_count: int = 0,
    ) -> None:
        missing = self._missing_category_overlays_for_pair_v2(pair_id, visible)
        try:
            cfg = self._load_ai_config_v2()
        except Exception:  # noqa: BLE001
            logger.exception("AI config load failed — falling back to heuristic")
            cfg = None
        self._full_zone_tree_chunk_state_v2 = {
            "pair_id": pair_id,
            "generation": int(generation),
            "visible": list(visible),
            "full_overlay_count": int(full_overlay_count),
            "active_zone": str(active_zone or ""),
            "started": started,
            "stage": "classify",
            "missing": missing,
            "missing_index": 0,
            "cfg": cfg,
            "issues_by_zone": self._zone_category_issues_by_zone_v2(),
            "plan": [],
            "plan_group_index": 0,
            "plan_item_index": 0,
            "current_header": None,
            "chunk_count": 0,
            "max_chunk_elapsed_ms": 0.0,
            "classified_count": 0,
            "tree_item_count": 0,
            "overlay_load_ms": round(float(overlay_load_ms or 0.0), 3),
            "overlay_json_bytes": int(overlay_json_bytes or 0),
            "overlay_load_worker": bool(overlay_load_worker),
            "overlay_load_strategy": str(overlay_load_strategy or ""),
            "overlay_page_count": int(overlay_page_count or 0),
            "overlay_page_files_read": int(overlay_page_files_read or 0),
            "overlay_page_files_skipped": int(overlay_page_files_skipped or 0),
            "materialized_overlay_count": int(materialized_overlay_count or len(visible)),
            "plan_build_ms": 0.0,
            "plan_build_worker": False,
        }
        QTimer.singleShot(
            int(GUI_FULL_ZONE_TREE_CHUNK_DELAY_MS),
            lambda p=pair_id, g=generation: self._run_full_zone_tree_chunk_v2(p, g),
        )

    def _full_zone_tree_chunk_state_is_current_v2(self, pair_id: str, generation: int) -> bool:
        state = self._full_zone_tree_chunk_state_v2
        if not isinstance(state, dict):
            return False
        state_generation = state.get("generation")
        if state_generation is None or int(state_generation) != int(generation):
            return False
        if str(state.get("pair_id") or "") != str(pair_id or ""):
            return False
        if generation != self._zone_tree_rebuild_generation_v2:
            return False
        if pair_id != str((self._active_row or {}).get("pair_id") or ""):
            return False
        return True

    def _record_full_zone_tree_chunk_elapsed_v2(self, state: dict, elapsed_ms: float) -> None:
        state["chunk_count"] = int(state.get("chunk_count") or 0) + 1
        state["max_chunk_elapsed_ms"] = max(
            float(state.get("max_chunk_elapsed_ms") or 0.0),
            float(elapsed_ms),
        )

    def _run_full_zone_tree_chunk_v2(self, pair_id: str, generation: int) -> None:
        if not self._full_zone_tree_chunk_state_is_current_v2(pair_id, generation):
            return
        state = self._full_zone_tree_chunk_state_v2
        if not isinstance(state, dict):
            return
        chunk_started = perf_counter()
        budget_ms = max(1.0, float(GUI_FULL_ZONE_TREE_CHUNK_TIME_BUDGET_MS))
        item_limit = max(1, int(GUI_FULL_ZONE_TREE_CHUNK_ITEM_LIMIT))

        if state.get("stage") == "classify":
            missing = state.get("missing") if isinstance(state.get("missing"), list) else []
            idx = int(state.get("missing_index") or 0)
            processed = 0
            while idx < len(missing) and processed < item_limit:
                overlay = missing[idx]
                idx += 1
                processed += 1
                if isinstance(overlay, dict) and self._classify_zone_category_record_v2(
                    pair_id,
                    overlay,
                    cfg=state.get("cfg"),
                    issues_by_zone=state.get("issues_by_zone") if isinstance(state.get("issues_by_zone"), dict) else {},
                ):
                    state["classified_count"] = int(state.get("classified_count") or 0) + 1
                if (perf_counter() - chunk_started) * 1000.0 >= budget_ms:
                    break
            state["missing_index"] = idx
            elapsed_ms = round((perf_counter() - chunk_started) * 1000.0, 3)
            self._record_full_zone_tree_chunk_elapsed_v2(state, elapsed_ms)
            if idx < len(missing):
                QTimer.singleShot(
                    int(GUI_FULL_ZONE_TREE_CHUNK_DELAY_MS),
                    lambda p=pair_id, g=generation: self._run_full_zone_tree_chunk_v2(p, g),
                )
                return
            state["stage"] = "plan"
            self._start_full_zone_tree_plan_worker_v2(pair_id, generation, state)
            return

        if state.get("stage") == "tree":
            self._run_full_zone_tree_item_chunk_v2(pair_id, generation, state, chunk_started)

    def _start_full_zone_tree_plan_worker_v2(self, pair_id: str, generation: int, state: dict) -> None:
        if not self._full_zone_tree_chunk_state_is_current_v2(pair_id, generation):
            return
        if self._full_zone_tree_plan_worker_v2 is not None:
            self._full_zone_tree_plan_worker_v2.cancel()
            self._retire_full_zone_tree_plan_worker_v2()
        category_snapshot = dict(self._zone_categories_v2.get(pair_id, {}))
        worker = FullZoneTreePlanWorker(
            pair_id=pair_id,
            generation=generation,
            overlays=state.get("visible") if isinstance(state.get("visible"), list) else [],
            dashboard_issues=list((self._active_row or {}).get("top_issues") or []),
            category_by_zone=category_snapshot,
            active_zone_id=str(state.get("active_zone") or ""),
            clustering_enabled=bool(getattr(self, "_zone_clustering_enabled_v2", True)),
        )
        worker.planned.connect(self._on_full_zone_tree_plan_ready_v2)
        worker.failed.connect(self._on_full_zone_tree_plan_failed_v2)
        self._full_zone_tree_plan_worker_v2 = worker
        worker.start()

    def _on_full_zone_tree_plan_ready_v2(self, pair_id: str, generation: int, payload: object) -> None:
        sender = self.sender()
        self._retire_full_zone_tree_plan_worker_v2(sender if isinstance(sender, QThread) else None)
        if not self._full_zone_tree_chunk_state_is_current_v2(pair_id, generation):
            return
        state = self._full_zone_tree_chunk_state_v2
        if not isinstance(state, dict):
            return
        data = payload if isinstance(payload, dict) else {}
        state["plan"] = data.get("plan") if isinstance(data.get("plan"), list) else []
        if isinstance(data.get("active_issue_by_zone"), dict):
            self._active_issue_by_zone = data["active_issue_by_zone"]
        state["plan_build_ms"] = round(float(data.get("plan_build_ms") or 0.0), 3)
        state["plan_build_worker"] = bool(data.get("plan_build_worker"))
        state["stage"] = "tree"
        was_blocked = self.zone_list_v2.blockSignals(True)
        try:
            self.zone_list_v2.clear()
            self._set_zone_action_buttons_enabled_v2(False)
        finally:
            self.zone_list_v2.blockSignals(was_blocked)
        QTimer.singleShot(
            int(GUI_FULL_ZONE_TREE_CHUNK_DELAY_MS),
            lambda p=pair_id, g=generation: self._run_full_zone_tree_chunk_v2(p, g),
        )

    def _on_full_zone_tree_plan_failed_v2(self, pair_id: str, generation: int, message: str) -> None:
        sender = self.sender()
        self._retire_full_zone_tree_plan_worker_v2(sender if isinstance(sender, QThread) else None)
        if not self._full_zone_tree_chunk_state_is_current_v2(pair_id, generation):
            return
        self._full_zone_tree_chunk_state_v2 = None
        self._pending_full_zone_tree_pair_id_v2 = ""
        if self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "full_zone_tree_rebuild",
                pair_uuid=pair_id,
                elapsed_ms=0.0,
                overlay_count=0,
                visible_overlay_count=0,
                chunked=True,
                chunk_count=0,
                max_chunk_elapsed_ms=0.0,
                plan_build_worker=True,
                error_message=str(message or "plan_build_failed"),
            )

    def _run_full_zone_tree_item_chunk_v2(
        self,
        pair_id: str,
        generation: int,
        state: dict,
        chunk_started: float,
    ) -> None:
        plan = state.get("plan") if isinstance(state.get("plan"), list) else []
        budget_ms = max(1.0, float(GUI_FULL_ZONE_TREE_CHUNK_TIME_BUDGET_MS))
        item_limit = max(1, int(GUI_FULL_ZONE_TREE_CHUNK_ITEM_LIMIT))
        group_idx = int(state.get("plan_group_index") or 0)
        item_idx = int(state.get("plan_item_index") or 0)
        current_header = state.get("current_header")
        added = 0
        was_blocked = self.zone_list_v2.blockSignals(True)
        try:
            while group_idx < len(plan) and added < item_limit:
                group = plan[group_idx]
                if current_header is None:
                    current_header = self._make_zone_tree_header_item_v2(group)
                    self.zone_list_v2.addTopLevelItem(current_header)
                    state["tree_item_count"] = int(state.get("tree_item_count") or 0) + 1
                    added += 1
                items = group.get("items") if isinstance(group, dict) else []
                if not isinstance(items, list):
                    items = []
                while item_idx < len(items) and added < item_limit:
                    added_count = self._append_zone_tree_plan_item_v2(current_header, items[item_idx])
                    added += added_count
                    item_idx += 1
                    state["tree_item_count"] = int(state.get("tree_item_count") or 0) + added_count
                    if (perf_counter() - chunk_started) * 1000.0 >= budget_ms:
                        break
                if item_idx >= len(items):
                    current_header.setExpanded(bool(group.get("expanded")))
                    group_idx += 1
                    item_idx = 0
                    current_header = None
                if (perf_counter() - chunk_started) * 1000.0 >= budget_ms:
                    break
        finally:
            self.zone_list_v2.blockSignals(was_blocked)
        state["plan_group_index"] = group_idx
        state["plan_item_index"] = item_idx
        state["current_header"] = current_header
        elapsed_ms = round((perf_counter() - chunk_started) * 1000.0, 3)
        self._record_full_zone_tree_chunk_elapsed_v2(state, elapsed_ms)
        if group_idx < len(plan):
            QTimer.singleShot(
                int(GUI_FULL_ZONE_TREE_CHUNK_DELAY_MS),
                lambda p=pair_id, g=generation: self._run_full_zone_tree_chunk_v2(p, g),
            )
            return
        self._finish_full_zone_tree_chunked_rebuild_v2(pair_id, generation)

    def _finish_full_zone_tree_chunked_rebuild_v2(self, pair_id: str, generation: int) -> None:
        if not self._full_zone_tree_chunk_state_is_current_v2(pair_id, generation):
            return
        state = self._full_zone_tree_chunk_state_v2 or {}
        active_zone = str(state.get("active_zone") or "")
        self._refresh_zone_list_filter_v2()
        if active_zone:
            was_blocked = self.zone_list_v2.blockSignals(True)
            try:
                self._select_zone_in_list_v2(active_zone)
            finally:
                self.zone_list_v2.blockSignals(was_blocked)
        elif not self.zone_list_v2.currentItem():
            self._schedule_initial_zone_selection_v2(pair_id)
        self._update_review_progress_v2()
        self._update_category_summary_v2()
        self._pending_full_zone_tree_pair_id_v2 = ""
        self._full_zone_tree_chunk_state_v2 = None
        if self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "full_zone_tree_rebuild",
                pair_uuid=pair_id,
                elapsed_ms=round((perf_counter() - float(state.get("started") or perf_counter())) * 1000.0, 3),
                overlay_count=int(state.get("full_overlay_count") or 0),
                visible_overlay_count=len(state.get("visible") if isinstance(state.get("visible"), list) else []),
                materialized_overlay_count=int(state.get("materialized_overlay_count") or 0),
                classified_count=int(state.get("classified_count") or 0),
                tree_item_count=int(state.get("tree_item_count") or 0),
                chunked=True,
                chunk_count=int(state.get("chunk_count") or 0),
                max_chunk_elapsed_ms=round(float(state.get("max_chunk_elapsed_ms") or 0.0), 3),
                overlay_load_ms=round(float(state.get("overlay_load_ms") or 0.0), 3),
                overlay_json_bytes=int(state.get("overlay_json_bytes") or 0),
                overlay_load_worker=bool(state.get("overlay_load_worker")),
                overlay_load_strategy=str(state.get("overlay_load_strategy") or ""),
                overlay_page_count=int(state.get("overlay_page_count") or 0),
                overlay_page_files_read=int(state.get("overlay_page_files_read") or 0),
                overlay_page_files_skipped=int(state.get("overlay_page_files_skipped") or 0),
                plan_build_ms=round(float(state.get("plan_build_ms") or 0.0), 3),
                plan_build_worker=bool(state.get("plan_build_worker")),
                worker_spawned=bool(state.get("overlay_load_worker") or state.get("plan_build_worker")),
            )

    def _schedule_initial_zone_selection_v2(self, pair_id: str) -> None:
        if not pair_id:
            return
        QTimer.singleShot(
            GUI_INITIAL_ZONE_SELECT_DELAY_MS,
            lambda p=pair_id: self._select_initial_zone_after_paint_v2(p),
        )

    def _select_initial_zone_after_paint_v2(self, pair_id: str) -> None:
        if pair_id != str((self._active_row or {}).get("pair_id") or ""):
            return
        if self.zone_list_v2.currentItem():
            return
        leaf_items = self._zone_leaf_items_v2()
        if leaf_items:
            zone_id = str(leaf_items[0].data(0, Qt.UserRole) or "")
            if zone_id:
                self._defer_next_initial_zone_heavy_render_v2 = (pair_id, zone_id)
            self._select_zone_leaf_v2(leaf_items[0])

    def _consume_initial_zone_heavy_render_defer_v2(self, pair_id: str, zone_id: str) -> bool:
        expected = self._defer_next_initial_zone_heavy_render_v2
        if expected != (pair_id, zone_id):
            return False
        self._defer_next_initial_zone_heavy_render_v2 = None
        return True

    def _begin_selected_zone_render_request_v2(self, pair_id: str, zone_id: str) -> str:
        self._selected_zone_render_generation_v2 += 1
        request_id = f"{pair_id}:{zone_id}:{self._selected_zone_render_generation_v2}"
        self._active_zone_render_request_v2 = (pair_id, zone_id, request_id)
        return request_id

    def _active_zone_render_request_id_v2(self, pair_id: str, zone_id: str) -> str:
        active = self._active_zone_render_request_v2
        if active and active[0] == pair_id and active[1] == zone_id:
            return str(active[2] or "")
        return ""

    def _is_current_zone_render_request_v2(
        self, pair_id: str, zone_id: str, request_id: str = "",
    ) -> bool:
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        current_zone = str(self._active_zone_id or "")
        if pair_id != current_pair or zone_id != current_zone:
            return False
        if not request_id:
            return True
        active = self._active_zone_render_request_v2
        return bool(active and active == (pair_id, zone_id, request_id))

    def _schedule_initial_zone_heavy_render_v2(self, pair_id: str, zone_id: str) -> None:
        self._initial_zone_heavy_render_generation_v2 += 1
        generation = self._initial_zone_heavy_render_generation_v2
        if self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "zone_heavy_render_deferred",
                pair_uuid=pair_id,
                zone_id=zone_id,
                delay_ms=GUI_INITIAL_ZONE_HEAVY_RENDER_DELAY_MS,
            )
        QTimer.singleShot(
            GUI_INITIAL_ZONE_HEAVY_RENDER_DELAY_MS,
            lambda p=pair_id, z=zone_id, g=generation: self._run_initial_zone_heavy_render_v2(p, z, g),
        )

    def _run_initial_zone_heavy_render_v2(self, pair_id: str, zone_id: str, generation: int) -> None:
        if generation != self._initial_zone_heavy_render_generation_v2:
            return
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        current_zone = str(self._active_zone_id or "")
        if pair_id != current_pair or zone_id != current_zone:
            self._record_zone_render_perf_event_v2(
                "zone_render_pending_dropped",
                pair_id,
                zone_id,
                current_pair_uuid=current_pair,
                current_zone_id=current_zone,
                reason_code="initial_zone_heavy_render_stale",
            )
            return
        try:
            self._focus_lightweight_on_zone_v2(zone_id)
        except Exception:
            logger.exception("Deferred lightweight zone focus failed for %s", zone_id)
        self._start_zone_crop_render_v2(zone_id)
        self._refresh_zone_vector_button_state_v2()

    def _schedule_lightweight_pair_load_v2(self, pair_id: str, viewer_pair: dict) -> None:
        if not pair_id or not self._is_lightweight_viewer_active_v2():
            return
        self._lightweight_pair_load_generation_v2 += 1
        generation = self._lightweight_pair_load_generation_v2
        if self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "lightweight_pair_load_deferred",
                pair_uuid=pair_id,
                delay_ms=GUI_LIGHTWEIGHT_PAIR_LOAD_DELAY_MS,
                input_format="pdf" if _viewer_pair_is_pdf(viewer_pair) else "raster",
            )
        QTimer.singleShot(
            GUI_LIGHTWEIGHT_PAIR_LOAD_DELAY_MS,
            lambda p=pair_id, vp=dict(viewer_pair), g=generation: self._run_lightweight_pair_load_v2(p, vp, g),
        )

    def _run_lightweight_pair_load_v2(self, pair_id: str, viewer_pair: dict, generation: int) -> None:
        if generation != self._lightweight_pair_load_generation_v2:
            return
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        if pair_id != current_pair or not self._is_lightweight_viewer_active_v2():
            if self._viewer_root:
                append_viewer_perf_event(
                    self._viewer_root,
                    "lightweight_pair_load_dropped",
                    pair_uuid=pair_id,
                    current_pair_uuid=current_pair,
                    reason_code="stale_pair_selection",
                )
            return

        started = perf_counter()
        try:
            if _viewer_pair_is_pdf(viewer_pair):
                stats = self._load_lightweight_pdf_v2(pair_id, viewer_pair)
            else:
                stats = self._load_lightweight_raster_preview_v2(pair_id, viewer_pair)
        except Exception:
            logger.exception("Deferred lightweight pair load failed for %s", pair_id)
            stats = {
                "input_format": "pdf" if _viewer_pair_is_pdf(viewer_pair) else "raster",
                "loaded_before": False,
                "loaded_after": False,
                "cache_state": "error",
            }
        if self._viewer_root:
            elapsed_ms = round((perf_counter() - started) * 1000.0, 3)
            append_viewer_perf_event(
                self._viewer_root,
                "lightweight_pair_load",
                pair_uuid=pair_id,
                elapsed_ms=elapsed_ms,
                load_ms=elapsed_ms,
                input_format=str(stats.get("input_format") or ""),
                pdf_cache_state=str(stats.get("cache_state") or ""),
                loaded_before=bool(stats.get("loaded_before")),
                loaded_after=bool(stats.get("loaded_after")),
                before_cache_hit=stats.get("before_cache_hit"),
                after_cache_hit=stats.get("after_cache_hit"),
                before_metadata_hit=stats.get("before_metadata_hit"),
                after_metadata_hit=stats.get("after_metadata_hit"),
                before_effective_dpi=stats.get("before_effective_dpi"),
                after_effective_dpi=stats.get("after_effective_dpi"),
                before_dpi_capped=stats.get("before_dpi_capped"),
                after_dpi_capped=stats.get("after_dpi_capped"),
            )
        if (
            stats.get("input_format") == "pdf"
            and (bool(stats.get("loaded_before")) or bool(stats.get("loaded_after")))
            and generation == self._lightweight_pair_load_generation_v2
        ):
            self._schedule_adjacent_pdf_prewarm_v2(pair_id, viewer_pair, generation)

    def _adjacent_pdf_prewarm_targets_v2(self, viewer_pair: dict) -> list[dict[str, int]]:
        page_pairs = list(viewer_pair.get("page_match_pairs") or [])
        if len(page_pairs) <= 1:
            return []
        try:
            current_a = int(viewer_pair.get("page_a", viewer_pair.get("page", 0)) or 0)
            current_b = int(viewer_pair.get("page_b", viewer_pair.get("page", 0)) or 0)
        except (TypeError, ValueError):
            current_a = 0
            current_b = 0
        current_idx = int(getattr(self, "_active_pdf_page_index_v2", 0) or 0)
        for idx, page_pair in enumerate(page_pairs):
            if not isinstance(page_pair, dict):
                continue
            try:
                if (
                    int(page_pair.get("page_a", 0) or 0) == current_a
                    and int(page_pair.get("page_b", 0) or 0) == current_b
                ):
                    current_idx = idx
                    break
            except (TypeError, ValueError):
                continue
        targets: list[dict[str, int]] = []
        for idx in (current_idx + 1, current_idx - 1):
            if idx < 0 or idx >= len(page_pairs):
                continue
            page_pair = page_pairs[idx]
            if not isinstance(page_pair, dict):
                continue
            try:
                targets.append(
                    {
                        "index": int(idx),
                        "page_a": int(page_pair.get("page_a", 0) or 0),
                        "page_b": int(page_pair.get("page_b", 0) or 0),
                    }
                )
            except (TypeError, ValueError):
                continue
        return targets[:2]

    def _schedule_adjacent_pdf_prewarm_v2(
        self,
        pair_id: str,
        viewer_pair: dict,
        lightweight_generation: int,
    ) -> None:
        if not pair_id or not self._is_lightweight_viewer_active_v2():
            return
        if not _viewer_pair_is_pdf(viewer_pair):
            return
        targets = self._adjacent_pdf_prewarm_targets_v2(viewer_pair)
        if not targets:
            return
        self._pdf_prewarm_generation_v2 += 1
        prewarm_generation = self._pdf_prewarm_generation_v2
        if self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "lightweight_pdf_prewarm_deferred",
                pair_uuid=pair_id,
                delay_ms=GUI_PDF_ADJACENT_PREWARM_DELAY_MS,
                target_count=len(targets),
                lightweight_generation=int(lightweight_generation),
                prewarm_generation=int(prewarm_generation),
            )
        QTimer.singleShot(
            GUI_PDF_ADJACENT_PREWARM_DELAY_MS,
            lambda p=pair_id, vp=dict(viewer_pair), targets=list(targets), lg=int(lightweight_generation), pg=int(prewarm_generation): (
                self._run_adjacent_pdf_prewarm_v2(p, vp, targets, lg, pg)
            ),
        )

    def _run_adjacent_pdf_prewarm_v2(
        self,
        pair_id: str,
        viewer_pair: dict,
        targets: list[dict[str, int]],
        lightweight_generation: int,
        prewarm_generation: int,
    ) -> None:
        if lightweight_generation != self._lightweight_pair_load_generation_v2:
            return
        if prewarm_generation != self._pdf_prewarm_generation_v2:
            return
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        if pair_id != current_pair or not self._is_lightweight_viewer_active_v2():
            return
        started = perf_counter()
        from src.gui.lightweight_viewport import prewarm_pdf_page_cache
        from src.services.comparison.safe_unicode import safe_unicode

        viewer_pair_for_pdf = dict(viewer_pair)
        viewer_pair_for_pdf["source_a"] = safe_unicode(str(viewer_pair_for_pdf.get("source_a") or ""))
        viewer_pair_for_pdf["source_b"] = safe_unicode(str(viewer_pair_for_pdf.get("source_b") or ""))
        before_pdf, before_pdf_key = _resolve_pdf_viewer_source_path(
            viewer_pair_for_pdf, "before", self._viewer_root,
        )
        after_pdf, after_pdf_key = _resolve_pdf_viewer_source_path(
            viewer_pair_for_pdf, "after", self._viewer_root,
        )
        result_items: list[dict[str, object]] = []
        for target in targets:
            page_index = int(target.get("index", -1))
            page_a = int(target.get("page_a", -1))
            page_b = int(target.get("page_b", -1))
            if before_pdf is not None and page_a >= 0:
                result = prewarm_pdf_page_cache(
                    before_pdf,
                    page_index=page_a,
                    target_dpi=150.0,
                    max_render_pixels=GUI_PDF_INITIAL_RENDER_MAX_PIXELS,
                )
                result_items.append(
                    {
                        "side": "before",
                        "source_key": before_pdf_key,
                        "page_pair_index": page_index,
                        "page_index": page_a,
                        **result,
                    }
                )
            if after_pdf is not None and page_b >= 0:
                result = prewarm_pdf_page_cache(
                    after_pdf,
                    page_index=page_b,
                    target_dpi=150.0,
                    max_render_pixels=GUI_PDF_INITIAL_RENDER_MAX_PIXELS,
                )
                result_items.append(
                    {
                        "side": "after",
                        "source_key": after_pdf_key,
                        "page_pair_index": page_index,
                        "page_index": page_b,
                        **result,
                    }
                )
        if not self._viewer_root:
            return
        ok_count = sum(1 for item in result_items if bool(item.get("ok")))
        cache_hit_count = sum(1 for item in result_items if bool(item.get("cache_hit")))
        metadata_hit_count = sum(1 for item in result_items if bool(item.get("metadata_hit")))
        rendered_count = sum(
            1
            for item in result_items
            if bool(item.get("ok")) and not bool(item.get("cache_hit"))
        )
        append_viewer_perf_event(
            self._viewer_root,
            "lightweight_pdf_prewarm",
            pair_uuid=pair_id,
            elapsed_ms=round((perf_counter() - started) * 1000.0, 3),
            target_count=len(targets),
            item_count=len(result_items),
            ok_count=ok_count,
            failed_count=max(0, len(result_items) - ok_count),
            cache_hit_count=cache_hit_count,
            rendered_count=rendered_count,
            metadata_hit_count=metadata_hit_count,
            lightweight_generation=int(lightweight_generation),
            prewarm_generation=int(prewarm_generation),
            results=result_items,
        )

    def _record_pair_selection_event_v2(
        self,
        pair_id: str,
        started: float,
        *,
        initial_overlay_count: int,
        full_tree_deferred: bool,
        initial_source: str,
        viewer_pair: Optional[dict] = None,
    ) -> None:
        if not self._viewer_root:
            return
        elapsed_ms = round((perf_counter() - started) * 1000.0, 3)
        pair = viewer_pair or {}
        declared_overlay_count = max(
            _int_value((self._active_row or {}).get("zone_count")),
            _int_value(pair.get("overlay_total_count")),
            int(initial_overlay_count or 0),
        )
        append_viewer_perf_event(
            self._viewer_root,
            "pair_selection_initial_load",
            pair_uuid=pair_id,
            elapsed_ms=elapsed_ms,
            gui_block_ms=elapsed_ms,
            initial_overlay_count=initial_overlay_count,
            materialized_overlay_count=initial_overlay_count,
            declared_overlay_count=declared_overlay_count,
            overlay_json_bytes=self._overlay_json_file_size_for_pair_v2(pair_id),
            overlay_json_read_for_first_paint=initial_source == "overlay_json",
            full_tree_deferred=bool(full_tree_deferred),
            initial_source=initial_source,
            input_format="pdf" if _viewer_pair_is_pdf(pair) else "raster",
            lightweight_load_deferred=self._is_lightweight_viewer_active_v2(),
            overlay_cache_pair_count=len(self._viewer_overlay_cache),
            overlay_cache_evictions=self._viewer_overlay_cache_evictions_v2,
            overlay_cache_total_bytes=self._viewer_overlay_cache_total_bytes_v2,
            overlay_cache_byte_limit=GUI_OVERLAY_CACHE_BYTE_LIMIT,
        )

    def _record_zone_selection_event_v2(self, zone_id: str, started: float) -> None:
        if not self._viewer_root:
            return
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        if not pair_id or not zone_id:
            return
        append_viewer_perf_event(
            self._viewer_root,
            "zone_selection",
            pair_uuid=pair_id,
            zone_id=zone_id,
            gui_block_ms=round((perf_counter() - started) * 1000.0, 3),
            overlay_count=len(self._active_overlays_by_zone),
            vector_cache_hit=bool(self._zone_vector_paths.get((pair_id, zone_id))),
        )

    def _record_zone_render_perf_event_v2(self, event: str, pair_id: str, zone_id: str, **metrics: object) -> None:
        if not self._viewer_root:
            return
        append_viewer_perf_event(
            self._viewer_root,
            event,
            pair_uuid=pair_id,
            zone_id=zone_id,
            **metrics,
        )

    def _selection_build_lod_tiles_enabled_v2(self) -> bool:
        if getattr(self._result, "package_complete", True) is False:
            return False
        metadata = getattr(self._result, "first_review_metadata", {}) if self._result else {}
        deferred = metadata.get("deferred_outputs", {}) if isinstance(metadata, dict) else {}
        if isinstance(deferred, dict) and deferred.get("lod_tiles") == "deferred":
            return False
        if self._viewer_manifest.get("build_lod_tiles") is False:
            return False
        return True

    def _show_pdf_page_pair_v2(self, page_a: int, page_b: int) -> None:
        """Phase H — Re-render backgrounds + filter overlays + re-push.

        Called when the user navigates to a different matched page pair.
        Mutates the active viewer_pair in-memory (page_a/page_b) so
        subsequent calls (e.g. zone selection focus) honor the new page,
        then re-invokes the load-lightweight path which renders Qt PDF
        at the new page indices.
        """

        pair_id = str((self._active_row or {}).get("pair_id") or "")
        if not pair_id:
            return
        viewer_pair = self._viewer_pairs_by_id.get(pair_id)
        if not isinstance(viewer_pair, dict):
            return

        started = perf_counter()
        # Mutate page indices so downstream consumers (lightweight load,
        # overlay filter, etc.) see the new page pair.
        viewer_pair["page_a"] = int(page_a)
        viewer_pair["page_b"] = int(page_b)
        page_pairs = list(viewer_pair.get("page_match_pairs") or [])
        for idx, page_pair in enumerate(page_pairs):
            if not isinstance(page_pair, dict):
                continue
            try:
                matches_page = (
                    int(page_pair.get("page_a", 0) or 0) == int(page_a)
                    and int(page_pair.get("page_b", 0) or 0) == int(page_b)
                )
                if matches_page:
                    self._active_pdf_page_index_v2 = idx
                    break
            except (TypeError, ValueError):
                continue

        # Re-render lightweight PDF backgrounds for the new pages immediately.
        # Overlay/tree refresh below is allowed to defer so page navigation
        # never cold-reads a large full_overlays JSON on the click path.
        lightweight_deferred = self._is_lightweight_viewer_active_v2()
        if lightweight_deferred:
            self._schedule_lightweight_pair_load_v2(pair_id, viewer_pair)
        self._update_page_nav_v2(viewer_pair)

        if _viewer_pair_is_pdf(viewer_pair) and self._viewer_overlay_pages_manifest_path_for_pair_v2(pair_id):
            all_overlays = []
            self._active_all_overlays_by_zone = {}
        else:
            all_overlays = list(self._active_all_overlays_by_zone.values())
        if not all_overlays:
            self._set_active_overlays_v2([])
            was_blocked = self.zone_list_v2.blockSignals(True)
            try:
                self.zone_list_v2.clear()
                self._set_zone_action_buttons_enabled_v2(False)
            finally:
                self.zone_list_v2.blockSignals(was_blocked)
            try:
                self._update_category_summary_v2()
            except Exception:
                logger.debug("Failed to refresh category summary on deferred page switch", exc_info=True)
            self._schedule_full_zone_tree_rebuild_v2(pair_id)
            self._record_pdf_page_navigation_event_v2(
                pair_id=pair_id,
                page_a=page_a,
                page_b=page_b,
                started=started,
                visible_overlay_count=0,
                overlay_load_deferred=True,
                lightweight_load_deferred=lightweight_deferred,
            )
            logger.info(
                "Page navigation: switched to A.page %d ↔ B.page %d (overlay load deferred)",
                page_a, page_b,
            )
            return

        self._cancel_full_zone_tree_rebuild_v2("page_navigation", bump_generation=True)
        generation = self._zone_tree_rebuild_generation_v2
        self._pending_full_zone_tree_pair_id_v2 = pair_id
        QTimer.singleShot(
            0,
            lambda p=pair_id, g=generation, vp=dict(viewer_pair), overlays=list(all_overlays), s=started: (
                self._continue_full_zone_tree_rebuild_with_overlays_v2(
                    pair_id=p,
                    generation=g,
                    viewer_pair=vp,
                    full_overlays=overlays,
                    visible=None,
                    started=s,
                    overlay_load_ms=0.0,
                    overlay_json_bytes=int(self._viewer_overlay_cache_bytes_by_pair_v2.get(p, 0)),
                    overlay_load_worker=False,
                )
            ),
        )
        self._record_pdf_page_navigation_event_v2(
            pair_id=pair_id,
            page_a=page_a,
            page_b=page_b,
            started=started,
            visible_overlay_count=0,
            overlay_load_deferred=False,
            lightweight_load_deferred=lightweight_deferred,
        )

        logger.info(
            "Page navigation: switched to A.page %d ↔ B.page %d (tree rebuild scheduled)",
            page_a, page_b,
        )

    def _record_pdf_page_navigation_event_v2(
        self,
        *,
        pair_id: str,
        page_a: int,
        page_b: int,
        started: float,
        visible_overlay_count: int,
        overlay_load_deferred: bool,
        lightweight_load_deferred: bool,
    ) -> None:
        if not self._viewer_root:
            return
        append_viewer_perf_event(
            self._viewer_root,
            "pdf_page_navigation",
            pair_uuid=pair_id,
            page_a=int(page_a),
            page_b=int(page_b),
            gui_block_ms=round((perf_counter() - started) * 1000.0, 3),
            visible_overlay_count=int(visible_overlay_count),
            overlay_load_deferred=bool(overlay_load_deferred),
            lightweight_load_deferred=bool(lightweight_load_deferred),
            trigger_reason="page_navigation",
        )

    def _load_lightweight_pdf_v2(self, pair_id: str, viewer_pair: dict) -> dict[str, object]:
        """Phase G2.7 — Push a PDF pair into both lightweight viewports.

        Qt PDF (QPdfDocument) renders the page at the requested DPI and
        the result is cached on disk; subsequent zooms in the same
        viewport pick the cached PNG up instantly. Falls back silently
        when the source paths are missing or Qt PDF is unavailable —
        the legacy viewport is still showing in that case.
        """

        # Phase G2.7-DIAG: detailed entry logging so we can diagnose
        # silent preview failures from user reports without needing to
        # set the root logger to DEBUG.
        logger.info(
            "[PDF lightweight] enter _load_lightweight_pdf_v2 pair_id=%s "
            "viewer_pair_keys=%s",
            pair_id, sorted(viewer_pair.keys()) if isinstance(viewer_pair, dict) else "<not-dict>",
        )
        stats: dict[str, object] = {
            "input_format": "pdf",
            "loaded_before": False,
            "loaded_after": False,
            "before_cache_hit": None,
            "after_cache_hit": None,
            "before_metadata_hit": None,
            "after_metadata_hit": None,
            "before_effective_dpi": None,
            "after_effective_dpi": None,
            "before_dpi_capped": None,
            "after_dpi_capped": None,
            "cache_state": "unavailable",
        }

        if (
            self.preview_before_lightweight_v2 is None
            or self.preview_after_lightweight_v2 is None
        ):
            logger.warning(
                "[PDF lightweight] viewport widgets not initialised "
                "(before=%s, after=%s) — skipping PDF load",
                self.preview_before_lightweight_v2 is not None,
                self.preview_after_lightweight_v2 is not None,
            )
            return stats

        # Verify Qt PDF backend is actually usable in this runtime
        try:
            from src.services.comparison.qt_pdf_adapter import is_qt_pdf_available
            qt_pdf_ok = is_qt_pdf_available()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[PDF lightweight] qt_pdf_adapter import failed: %s", exc,
            )
            qt_pdf_ok = False
        if not qt_pdf_ok:
            logger.warning(
                "[PDF lightweight] Qt PDF (PySide6.QtPdf) NOT available — "
                "lightweight PDF rendering will fall back to empty notice"
            )

        # Audit-gates §12.3 A1 — sanitise source paths through safe_unicode()
        # so lone CP949↔UTF-16 surrogates do not propagate into Path() and
        # then into QPdfDocument. The 2026-05-15 Qt6Core 0xc0000409 BEX64
        # crash was triggered exactly here: corrupted Korean filename →
        # invalid Path → QPdfDocument fast-fail.
        from src.services.comparison.safe_unicode import safe_unicode
        source_a = safe_unicode(str(viewer_pair.get("source_a") or ""))
        source_b = safe_unicode(str(viewer_pair.get("source_b") or ""))
        # Multi-page Phase H matchers attach (page_a, page_b) to the
        # viewer_pair; legacy single-page runs default both to "page".
        page_a = int(viewer_pair.get("page_a", viewer_pair.get("page", 0)) or 0)
        page_b = int(viewer_pair.get("page_b", viewer_pair.get("page", 0)) or 0)

        # Path-existence diagnostics — Korean filenames sometimes get
        # mangled across Windows ↔ utf-8 boundaries; logging the exact
        # bytes (repr) helps catch surrogate-pair issues fast.
        viewer_pair_for_pdf = dict(viewer_pair)
        viewer_pair_for_pdf["source_a"] = source_a
        viewer_pair_for_pdf["source_b"] = source_b
        before_pdf, before_pdf_key = _resolve_pdf_viewer_source_path(
            viewer_pair_for_pdf, "before", self._viewer_root,
        )
        after_pdf, after_pdf_key = _resolve_pdf_viewer_source_path(
            viewer_pair_for_pdf, "after", self._viewer_root,
        )
        logger.info(
            "[PDF lightweight] sources: A=%r resolved=%r via=%s | "
            "B=%r resolved=%r via=%s | page_a=%d page_b=%d",
            source_a,
            str(before_pdf) if before_pdf else "",
            before_pdf_key,
            source_b,
            str(after_pdf) if after_pdf else "",
            after_pdf_key,
            page_a,
            page_b,
        )

        loaded_before = False
        loaded_after = False
        if page_a < 0:
            try:
                self.preview_before_lightweight_v2.load_scene_pack(
                    None,
                    empty_notice="No before-side PDF page for this unmatched page.",
                )
                self.preview_before_lightweight_v2.set_overlays([], [])
            except Exception:
                logger.debug("Could not clear unmatched before PDF side", exc_info=True)
            logger.info("[PDF lightweight] before-side blank: page_a=%d", page_a)
        elif before_pdf is not None:
            try:
                loaded_before = self.preview_before_lightweight_v2.load_pdf_page(
                    before_pdf,
                    page_index=page_a,
                    target_dpi=150.0,
                    max_render_pixels=GUI_PDF_INITIAL_RENDER_MAX_PIXELS,
                )
                stats["loaded_before"] = bool(loaded_before)
                state = getattr(self.preview_before_lightweight_v2, "_pdf_render_state", {}) or {}
                if loaded_before and isinstance(state, dict):
                    stats["before_cache_hit"] = bool(state.get("cache_hit"))
                    stats["before_metadata_hit"] = bool(state.get("metadata_hit"))
                    stats["before_effective_dpi"] = state.get("effective_dpi") or state.get("current_dpi")
                    stats["before_dpi_capped"] = bool(state.get("dpi_capped"))
                logger.info(
                    "[PDF lightweight] before-side load_pdf_page returned %s "
                    "(source=%r via=%s page=%d)",
                    loaded_before, before_pdf.name, before_pdf_key, page_a,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[PDF lightweight] before-side load_pdf_page raised "
                    "exception for source=%r via=%s page=%d",
                    str(before_pdf), before_pdf_key, page_a,
                )
        else:
            logger.warning(
                "[PDF lightweight] before-side skipped: no usable PDF "
                "(source_a=%r before_page_pdf=%r page_pdf=%r)",
                source_a,
                viewer_pair.get("before_page_pdf"),
                viewer_pair.get("page_pdf"),
            )
        if page_b < 0:
            try:
                self.preview_after_lightweight_v2.load_scene_pack(
                    None,
                    empty_notice="No after-side PDF page for this unmatched page.",
                )
                self.preview_after_lightweight_v2.set_overlays([], [])
            except Exception:
                logger.debug("Could not clear unmatched after PDF side", exc_info=True)
            logger.info("[PDF lightweight] after-side blank: page_b=%d", page_b)
        elif after_pdf is not None:
            try:
                loaded_after = self.preview_after_lightweight_v2.load_pdf_page(
                    after_pdf,
                    page_index=page_b,
                    target_dpi=150.0,
                    max_render_pixels=GUI_PDF_INITIAL_RENDER_MAX_PIXELS,
                )
                stats["loaded_after"] = bool(loaded_after)
                state = getattr(self.preview_after_lightweight_v2, "_pdf_render_state", {}) or {}
                if loaded_after and isinstance(state, dict):
                    stats["after_cache_hit"] = bool(state.get("cache_hit"))
                    stats["after_metadata_hit"] = bool(state.get("metadata_hit"))
                    stats["after_effective_dpi"] = state.get("effective_dpi") or state.get("current_dpi")
                    stats["after_dpi_capped"] = bool(state.get("dpi_capped"))
                logger.info(
                    "[PDF lightweight] after-side load_pdf_page returned %s "
                    "(source=%r via=%s page=%d)",
                    loaded_after, after_pdf.name, after_pdf_key, page_b,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[PDF lightweight] after-side load_pdf_page raised "
                    "exception for source=%r via=%s page=%d",
                    str(after_pdf), after_pdf_key, page_b,
                )
        else:
            logger.warning(
                "[PDF lightweight] after-side skipped: no usable PDF "
                "(source_b=%r after_page_pdf=%r page_pdf=%r)",
                source_b,
                viewer_pair.get("after_page_pdf"),
                viewer_pair.get("page_pdf"),
            )
        # Push a v2-style fidelity state so the badge reads as "사용 가능"
        # rather than the default "relative_only" watermark.
        if loaded_before or loaded_after:
            # Audit-gates §12.3 A2 — replace the silent ``except: pass`` with
            # a logged warning and a graceful fallback to ``relative_only``
            # state. The previous code masked Qt6Core invariant errors that
            # would later resurface as a BEX64 fast-fail (0xc0000409).
            for side, vp, loaded in (
                ("before", self.preview_before_lightweight_v2, loaded_before),
                ("after", self.preview_after_lightweight_v2, loaded_after),
            ):
                try:
                    fidelity_mode = "raster_refined" if loaded else "relative_only"
                    effective_dpi = stats.get(f"{side}_effective_dpi")
                    dpi_text = int(float(effective_dpi)) if effective_dpi else 150
                    fidelity_text = (
                        f"PDF DPI {dpi_text}"
                        if loaded
                        else "PDF preview unavailable on this side"
                    )
                    vp.set_fidelity_state(
                        fidelity_mode,
                        status_text=fidelity_text,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[PDF lightweight] %s-side set_fidelity_state(%s) "
                        "failed (%s); falling back to relative_only state",
                        side,
                        fidelity_mode,
                        exc,
                    )
                    try:
                        vp.set_fidelity_state(
                            "relative_only",
                            status_text="PDF · 미리보기 사용 불가",
                        )
                    except Exception:
                        # Final fallback — even the relative_only state
                        # rejected; just log and continue. Better to leave
                        # the previous state than crash the GUI.
                        logger.exception(
                            "[PDF lightweight] %s-side fallback fidelity "
                            "state also failed — viewport left in prior state",
                            side,
                        )
            # Also push the current overlays so cloud markers appear on
            # top of the PDF background. Wrapped in try/except because the
            # viewer state may be partially loaded if one side failed above.
            try:
                self._push_overlays_to_lightweight_v2(
                    pair_id, focus_zone_id=self._active_zone_id or "",
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[PDF lightweight] _push_overlays_to_lightweight_v2 "
                    "failed for pair=%s; overlays will not render but the "
                    "viewport remains usable",
                    pair_id,
                )
        logger.info(
            "Lightweight PDF load: pair=%s before=%s after=%s",
            pair_id, loaded_before, loaded_after,
        )
        cache_values = [
            value
            for value in (stats.get("before_cache_hit"), stats.get("after_cache_hit"))
            if value is not None
        ]
        if cache_values and all(bool(value) for value in cache_values):
            stats["cache_state"] = "all_cached"
        elif cache_values and any(bool(value) for value in cache_values):
            stats["cache_state"] = "mixed"
        elif cache_values:
            stats["cache_state"] = "all_cold"
        return stats

    def _transform_world_bbox_v2(
        self,
        transform: Any,
    ) -> Optional[tuple[float, float, float, float]]:
        if not isinstance(transform, dict):
            return None
        try:
            x0 = float(transform.get("min_x"))
            y0 = float(transform.get("min_y"))
            x1 = float(transform.get("max_x"))
            y1 = float(transform.get("max_y"))
        except (TypeError, ValueError):
            return None
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1, y1)

    def _load_lightweight_raster_preview_v2(
        self,
        pair_id: str,
        viewer_pair: dict,
    ) -> dict[str, object]:
        """Load rendered PNG previews into the lightweight viewer."""

        stats: dict[str, object] = {
            "input_format": "raster",
            "loaded_before": False,
            "loaded_after": False,
            "cache_state": "not_pdf",
        }
        if (
            self.preview_before_lightweight_v2 is None
            or self.preview_after_lightweight_v2 is None
        ):
            return stats
        loaded_before = False
        loaded_after = False
        specs = (
            (
                "before",
                self.preview_before_lightweight_v2,
                viewer_pair.get("before_image"),
                viewer_pair.get("before_transform"),
            ),
            (
                "after",
                self.preview_after_lightweight_v2,
                viewer_pair.get("after_image"),
                viewer_pair.get("after_transform"),
            ),
        )
        for side, viewport, image_value, transform in specs:
            image_path = _resolve_viewer_artifact_path(image_value, self._viewer_root)
            world_bbox = self._transform_world_bbox_v2(transform)
            try:
                loaded = viewport.load_raster_image(
                    image_path,
                    world_bbox=world_bbox,
                    empty_notice="Rendered drawing preview is not available.",
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[raster lightweight] %s-side load failed for pair=%s image=%r",
                    side,
                    pair_id,
                    image_value,
                )
                loaded = False
            loaded_before = loaded_before or (side == "before" and loaded)
            loaded_after = loaded_after or (side == "after" and loaded)
            if side == "before":
                stats["loaded_before"] = bool(loaded)
            else:
                stats["loaded_after"] = bool(loaded)
        if loaded_before or loaded_after:
            self._lightweight_raster_pairs.add(pair_id)
            visual_ext.apply_shared_lightweight_camera_frame(self, viewer_pair)
            for viewport, loaded in (
                (self.preview_before_lightweight_v2, loaded_before),
                (self.preview_after_lightweight_v2, loaded_after),
            ):
                try:
                    fidelity_mode = "raster_refined" if loaded else "relative_only"
                    fidelity_text = (
                        "raster preview"
                        if loaded
                        else "raster preview unavailable on this side"
                    )
                    viewport.set_fidelity_state(
                        fidelity_mode,
                        status_text=fidelity_text,
                    )
                except Exception:
                    logger.debug("Failed to set raster fidelity state", exc_info=True)
            self._push_overlays_to_lightweight_v2(
                pair_id, focus_zone_id=self._active_zone_id or "",
            )
        logger.info(
            "Lightweight raster preview load: pair=%s before=%s after=%s",
            pair_id,
            loaded_before,
            loaded_after,
        )
        return stats

    def _apply_session_state_to_viewport_v2(
        self, pair_id: str, side: str, mode: str,
    ) -> None:
        """Phase G2.2 — Push a session state into the lightweight viewport.

        Runs on the GUI thread. When the lightweight viewer is enabled and
        the state has reached ``skeleton_preview`` (or higher), load the
        scene pack's LOD0 primitives into the viewport for the matching
        side.
        """

        if not self._is_lightweight_viewer_active_v2():
            return

        # Pick the matching viewport.
        viewport = (
            self.preview_before_lightweight_v2
            if side == "before"
            else self.preview_after_lightweight_v2
        )
        if viewport is None:
            return

        session = getattr(self, "_viewer_session", None)
        if session is None:
            viewport.set_fidelity_state("relative_only")
            return

        state = session.get_pair_state(pair_id, side=side)  # type: ignore[arg-type]
        if not state.scene_pack_ref and pair_id in self._lightweight_raster_pairs:
            viewport.set_fidelity_state(
                "raster_refined",
                status_text="raster preview",
            )
            return
        # Push fidelity badge regardless of mode.
        status = ""
        if state.last_build_ms > 0 and state.scene_pack_ref:
            count = state.scene_pack_ref.primitive_count
            status = f"{count}개 / {state.last_build_ms:.0f}ms{' · 캐시' if state.cache_hit else ''}"
        viewport.set_fidelity_state(state.render_mode, status_text=status)

        # Load primitives when a pack is available.
        if state.scene_pack_ref and state.scene_pack_ref.overview_lod0_path:
            try:
                viewport.load_scene_pack(state.scene_pack_ref)
                # Phase G2.3 — also push v1 change overlays + active focus
                # so the user sees both the drawing and the change cloud.
                self._push_overlays_to_lightweight_v2(
                    pair_id, focus_zone_id=self._active_zone_id or "",
                )
            except Exception as exc:
                logger.exception("Failed to load scene pack into viewport: %s", exc)
        elif mode == "render_pending":
            # Show a friendlier notice while waiting.
            try:
                viewport.set_fidelity_state("render_pending", status_text="빌드 중")
            except Exception:
                pass

    def _on_viewer_session_progress_v2(
        self, pair_id: str, side: str, stage: str,
        percent: Optional[float], message: str,
    ) -> None:
        """Phase G2.4 — Multi-stage build progress from ViewerSession.

        Marshalled to GUI thread; updates the lightweight viewport's badge
        with stage-specific status (e.g. "DWG → DXF 변환 중") so the user
        knows what's happening during the long build for big DWG drawings.
        """

        try:
            QTimer.singleShot(
                0,
                lambda p=pair_id, s=side, st=stage, pct=percent, msg=message:
                    self._apply_session_progress_to_viewport_v2(p, s, st, pct, msg),
            )
        except Exception:
            logger.exception("Failed to marshal viewer session progress")

    def _apply_session_progress_to_viewport_v2(
        self, pair_id: str, side: str, stage: str,
        percent: Optional[float], message: str,
    ) -> None:
        """Update the lightweight viewport's badge with current build stage."""

        if not self._is_lightweight_viewer_active_v2():
            return
        if pair_id != str((self._active_row or {}).get("pair_id") or ""):
            return
        viewport = (
            self.preview_before_lightweight_v2
            if side == "before"
            else self.preview_after_lightweight_v2
        )
        if viewport is None:
            return
        # Map the stage to a render mode for badge colour.
        # All in-progress stages use ``render_pending``; only ``done`` /
        # ``failed`` flip to a terminal mode (handled by state_change cb).
        if stage in {"starting", "resolving_dwg", "reading_dxf",
                     "flattening", "indexing", "writing"}:
            try:
                pct_text = f"{int(percent * 100)}%" if percent is not None else "..."
                viewport.set_fidelity_state(
                    "render_pending", status_text=f"{message} ({pct_text})",
                )
            except Exception:
                pass

    def _on_viewer_session_zone_evidence_v2(
        self, pair_id: str, zone_id: str, evidence,
    ) -> None:
        """Phase G2.3 — Worker callback when a zone-focus pack is ready.

        Marshalled to the GUI thread; pushes the focus primitives to the
        active lightweight viewport(s) so the user sees full-detail vector
        for the selected zone.
        """

        try:
            QTimer.singleShot(
                0,
                lambda p=pair_id, z=zone_id, e=evidence:
                    self._apply_zone_evidence_to_lightweight_v2(p, z, e),
            )
        except Exception:
            logger.exception("Failed to marshal zone evidence")

    def _apply_zone_evidence_to_lightweight_v2(
        self, pair_id: str, zone_id: str, evidence,
    ) -> None:
        """Push a freshly-built zone focus pack to the lightweight viewports.

        Runs on the GUI thread. No-op when:
          * lightweight toggle is OFF
          * the active pair/zone changed since the request was submitted
            (the user clicked something else; old result is discarded)
          * evidence carries no usable file path
        """

        if not self._is_lightweight_viewer_active_v2():
            return
        if pair_id != str((self._active_row or {}).get("pair_id") or ""):
            return
        if zone_id != self._active_zone_id:
            return
        focus_path = getattr(evidence, "raster_uri", "") or ""
        if not focus_path:
            return
        path = Path(focus_path)
        if not path.exists():
            return
        for vp in (
            self.preview_before_lightweight_v2,
            self.preview_after_lightweight_v2,
        ):
            if vp is None:
                continue
            try:
                vp.push_zone_focus_pack(path)
            except Exception:
                logger.exception("push_zone_focus_pack failed for %s", path)

    def _on_toggle_auto_advance_v2(self, checked: bool) -> None:
        """Phase G3.5 — Toggle the auto-advance feature for review hotkeys.

        Stored on the workbench instance as ``_auto_advance_v2`` which the
        existing review-status setter checks before calling
        ``_advance_to_next_unreviewed_zone_v2``.
        """

        self._auto_advance_v2 = bool(checked)
        if hasattr(self, "lbl_status_v2"):
            self.lbl_status_v2.setText(
                "자동 이동 ON — 1/2/3/4 후 다음 zone 자동 선택"
                if checked
                else "자동 이동 OFF — J/K로 직접 이동"
            )
        logger.info("Auto-advance toggled: %s", checked)

    def _on_toggle_zone_clustering_v2(self, checked: bool) -> None:
        """Phase I3 — Toggle near-duplicate zone clustering.

        When ON, cluster_zones folds N>=3 same-key zones into one row;
        when OFF, every zone gets its own row (flat per-category list).
        Rebuilds the active drawing's zone tree to apply the change.
        """

        self._zone_clustering_enabled_v2 = bool(checked)
        if hasattr(self, "lbl_status_v2"):
            self.lbl_status_v2.setText(
                "반복 변경 묶기 ON — 같은 카테고리의 유사 변경을 한 줄로"
                if checked
                else "반복 변경 묶기 OFF — 모든 zone 평면 표시"
            )
        logger.info("Zone clustering toggled: %s", checked)
        # Rebuild the tree for the currently-active drawing so the toggle
        # takes effect immediately.
        if not self._active_row:
            return
        pair_id = str(self._active_row.get("pair_id") or "")
        if not pair_id:
            return
        preview = self._preview_by_pair.get(pair_id)
        overlays = list(self._active_overlays_by_zone.values())
        if not overlays:
            overlays = self._viewer_visible_overlays_from_page_store_v2(
                pair_id,
                self._viewer_pairs_by_id.get(pair_id, {}),
            ) or []
        if overlays:
            self._populate_zone_list_v2(preview, overlays)
            # Restore selection if the active zone is still in the new tree.
            if self._active_zone_id:
                self._select_zone_in_list_v2(self._active_zone_id)
            return
        if preview:
            self._populate_zone_list_v2(preview, [overlay.to_dict() for overlay in preview.zone_overlays])
            return
        self._set_active_overlays_v2([])
        self.zone_list_v2.clear()
        self._schedule_full_zone_tree_rebuild_v2(pair_id)

    def _on_lightweight_camera_changed_v2(
        self, source_side: str, center_x: float, center_y: float, upp: float,
    ) -> None:
        """Phase G2.3 — Mirror pan/zoom across before/after lightweight
        viewports. The flag breaks the recursion when the mirror call
        itself emits ``viewportChanged``.
        """

        if self._lightweight_camera_sync_in_progress:
            return
        target = (
            self.preview_after_lightweight_v2
            if source_side == "before"
            else self.preview_before_lightweight_v2
        )
        if target is None:
            return
        self._lightweight_camera_sync_in_progress = True
        try:
            target.set_camera(center_x, center_y, upp)
        finally:
            self._lightweight_camera_sync_in_progress = False
        self._schedule_lightweight_visible_tile_window_v2(source_side, center_x, center_y, upp)

    def _schedule_lightweight_visible_tile_window_v2(
        self,
        source_side: str,
        center_x: Optional[float] = None,
        center_y: Optional[float] = None,
        upp: Optional[float] = None,
    ) -> None:
        if not self._is_lightweight_viewer_active_v2() or not self._viewer_root:
            return
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        if not pair_id:
            return
        viewer_pair = self._viewer_pairs_by_id.get(pair_id, {})
        if not isinstance(viewer_pair, dict):
            return
        transform = viewer_pair.get("after_transform") or viewer_pair.get("before_transform") or {}
        if not isinstance(transform, dict):
            return
        viewport = (
            self.preview_before_lightweight_v2
            if source_side == "before"
            else self.preview_after_lightweight_v2
        )
        if viewport is None or not hasattr(viewport, "visible_world_rect"):
            return
        try:
            visible_world = viewport.visible_world_rect(center_x, center_y, upp)
        except Exception:
            logger.debug("Lightweight visible-world rect unavailable", exc_info=True)
            return
        pixel_rect = _world_bbox_to_pixel_rect(visible_world, transform)
        if not pixel_rect:
            return
        try:
            effective_upp = float(upp) if upp is not None else abs(float(visible_world[2]) - float(visible_world[0])) / max(1.0, float(viewport.width() or 1.0))
        except (TypeError, ValueError, IndexError):
            effective_upp = 1.0
        zoom = _lightweight_tile_zoom_from_transform(transform, effective_upp)
        self._schedule_visible_tile_window_v2(pair_id, pixel_rect, zoom)

    def _push_overlays_to_lightweight_v2(
        self, pair_id: str, focus_zone_id: str = "",
    ) -> None:
        """Phase G2.3 — Push the v1 overlay list onto both lightweight
        viewports (using the existing ``_viewer_overlays_for_pair_v2``
        cache so we don't re-read JSON per call).

        ``focus_zone_id`` (optional) marks one zone as the focus marker
        (highlighted blue/4 px border + non-dimmed); other zones become
        cloud overlays (red/dimmed).
        """

        if not pair_id:
            return
        active_pair = ""
        if isinstance(self._active_row, dict):
            active_pair = str(self._active_row.get("pair_id") or "")
        try:
            if active_pair == pair_id and self._active_overlays_by_zone:
                overlays = list(self._active_overlays_by_zone.values())
            elif active_pair == pair_id:
                return
            else:
                viewer_pair = self._viewer_pairs_by_id.get(pair_id, {})
                overlays = (
                    self._viewer_visible_overlays_from_page_store_v2(pair_id, viewer_pair)
                    if isinstance(viewer_pair, dict)
                    else None
                )
                if overlays is None:
                    return
        except Exception as exc:
            logger.debug("overlay fetch failed for %s: %s", pair_id, exc)
            return
        if not overlays:
            return

        # PDF coordinate-metadata backfill. The marker bbox is in PDF
        # ``image_pixels`` (at the compare DPI), but some overlay sources
        # (dashboard ``top_issues``) drop ``bbox_coordinate_space``/``pdf_dpi``.
        # Without them push_change_overlays_from_v1 -> convert_bbox_to_world_space
        # treats the bbox as already-world and passes the RAW PIXELS through, so
        # markers land in pixel space (x~1859) while the PDF background is in
        # points (0..842) — the page renders off-screen/tiny and only the
        # markers show ("상대 위치 모드 — 실배경 아님"). Backfill from the pair.
        overlays = self._backfill_pdf_overlay_coord_space_v2(pair_id, overlays)

        for vp, side in (
            (self.preview_before_lightweight_v2, "before"),
            (self.preview_after_lightweight_v2, "after"),
        ):
            if vp is None:
                continue
            try:
                vp.push_change_overlays_from_v1(
                    overlays, side=side, focus_zone_id=focus_zone_id,
                )
            except Exception as exc:
                logger.debug("push_change_overlays_from_v1 failed: %s", exc)

    def _backfill_pdf_overlay_coord_space_v2(
        self, pair_id: str, overlays: list[dict],
    ) -> list[dict]:
        """Ensure PDF overlay records carry ``bbox_coordinate_space`` +
        ``pdf_dpi`` so the image_pixels->PDF-points conversion fires.

        Returns the original list unchanged for non-PDF pairs or when every
        record already carries the metadata.
        """

        viewer_pair = self._viewer_pairs_by_id.get(pair_id) or {}
        if not _viewer_pair_is_pdf(viewer_pair):
            return overlays
        # The pdf_dpi + image_pixels space live on the per-overlay records (the
        # manifest pair record has them as None for raster PDF pairs). Source
        # them WITHOUT touching the caching ``_viewer_overlays_for_pair_v2``
        # (which would defeat the paged-overlay-store memory path), in order:
        #   1) any pushed overlay that already carries the metadata,
        #   2) the pair / transform fields,
        #   3) a lightweight direct peek at the overlay JSON file.
        # Without a positive dpi we cannot convert, so leave overlays untouched.
        pair_dpi = 0.0
        pair_space = ""
        for ov in overlays:
            if not isinstance(ov, dict):
                continue
            if pair_dpi <= 0:
                try:
                    pair_dpi = float(ov.get("pdf_dpi") or 0.0)
                except (TypeError, ValueError):
                    pair_dpi = 0.0
            if not pair_space:
                pair_space = str(ov.get("bbox_coordinate_space") or "")
            if pair_dpi > 0 and pair_space:
                break
        if pair_dpi <= 0:
            for src in (
                viewer_pair,
                viewer_pair.get("before_transform") or {},
                viewer_pair.get("after_transform") or {},
            ):
                try:
                    pair_dpi = float(
                        (src or {}).get("compare_pdf_dpi") or (src or {}).get("pdf_dpi") or 0.0
                    )
                except (TypeError, ValueError):
                    pair_dpi = 0.0
                if pair_dpi > 0:
                    break
        if pair_dpi <= 0:
            pair_dpi = self._peek_overlay_json_pdf_dpi_v2(viewer_pair)
        if not pair_space:
            pair_space = "image_pixels"
        if pair_dpi <= 0:
            return overlays
        out: list[dict] = []
        for ov in overlays:
            if not isinstance(ov, dict):
                out.append(ov)
                continue
            space = str(ov.get("bbox_coordinate_space") or "")
            try:
                dpi = float(ov.get("pdf_dpi") or 0.0)
            except (TypeError, ValueError):
                dpi = 0.0
            if space and dpi > 0:
                out.append(ov)
                continue
            patched = dict(ov)
            if not space:
                patched["bbox_coordinate_space"] = pair_space
            if dpi <= 0 and pair_dpi > 0:
                patched["pdf_dpi"] = pair_dpi
            out.append(patched)
        return out

    def _peek_overlay_json_pdf_dpi_v2(self, viewer_pair: dict) -> float:
        """Return the first ``pdf_dpi`` found in the pair's overlay JSON, or 0.0.

        Lightweight, non-caching last resort for the PDF coord backfill — reads
        the overlay JSON file directly (does NOT populate ``_viewer_overlay_cache``
        so the paged-overlay-store memory path is preserved).
        """

        # Paged-store pairs: the pushed overlays already come FROM the page store,
        # so if pdf_dpi wasn't on them it isn't in the store either — reading the
        # (potentially huge) legacy overlay JSON would only re-confirm that while
        # breaking the paged-store perf invariant (a page-pair tree refresh must
        # read the legacy overlay JSON 0×). Skip the legacy read for paged pairs.
        if viewer_pair.get("overlay_pages_manifest"):
            return 0.0

        raw = viewer_pair.get("overlay_json")
        path = _resolve_viewer_artifact_path(raw, getattr(self, "_viewer_root", None))
        if path is None or not path.exists():
            return 0.0
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="surrogatepass"))
        except Exception:  # noqa: BLE001
            return 0.0
        records = data.get("overlays") if isinstance(data, dict) else data
        if not isinstance(records, list):
            return 0.0
        for rec in records:
            if not isinstance(rec, dict):
                continue
            try:
                dpi = float(rec.get("pdf_dpi") or 0.0)
            except (TypeError, ValueError):
                dpi = 0.0
            if dpi > 0:
                return dpi
        return 0.0

    def _request_zone_focus_v2(self, zone_id: str) -> None:
        """Phase G2.3 — Submit a zone-focus build to the ViewerSession.

        Cache hits fire synchronously via the on_zone_evidence callback;
        cold builds run on a worker thread and notify when done. No-op
        when the lightweight toggle is OFF or the session is unavailable.
        """

        if not zone_id:
            return
        if not self._is_lightweight_viewer_active_v2():
            return
        session = getattr(self, "_viewer_session", None)
        if session is None:
            return
        overlay = self._active_overlays_by_zone.get(zone_id)
        if not overlay:
            return
        active_pair = ""
        if isinstance(self._active_row, dict):
            active_pair = str(self._active_row.get("pair_id") or "")
        if not active_pair:
            return
        # NOTE: the native scene-pack zone vector (ensure_pair_source +
        # request_zone build) is intentionally NOT wired here yet. render_zone_focus
        # explodes INSERT blocks WITHOUT applying the block's insert transform, so
        # for normalized-DWG sources the vector primitives land at block-definition
        # coordinates (observed ~376k,-161k) instead of the insert location
        # (~494k,-109k) — far outside the zone/overlay frame. Pushed into the
        # viewer it dragged the view to a tiny corner (the change went invisible).
        # Until render_zone_focus applies the insert transform, the correctly
        # placed CAD-background crop (_apply_zone_crop_to_lightweight_v2) is the
        # surface we show. (ViewerSession.ensure_pair_source is kept + tested for
        # when that worker fix lands.)
        # Phase G2.4 — bbox normalisation (production overlays use the
        # ``{"min_x","min_y","max_x","max_y"}`` dict shape; legacy/test
        # fixtures use ``[x0, y0, x1, y1]``). Accept both.
        from src.gui.lightweight_viewport import _normalise_bbox
        for side, key in (("after", "bbox"), ("before", "old_bbox")):
            raw = overlay.get(key) or overlay.get("bbox") or overlay.get("old_bbox")
            coords = _normalise_bbox(raw)
            if coords is None:
                continue
            try:
                session.request_zone(
                    pair_id=active_pair,
                    zone_id=zone_id,
                    side=side,
                    bbox_world=coords,
                )
            except Exception:
                logger.exception("request_zone failed for %s/%s", active_pair, zone_id)

    def _focus_lightweight_on_zone_v2(self, zone_id: str) -> None:
        """Phase G2.3 — Pan/zoom both lightweight viewports to the bbox of
        the selected change zone. Reads bbox from the active overlay cache.
        """

        if not zone_id:
            return
        if not getattr(self, "act_lightweight_viewer_v2", None):
            logger.debug("[ZONE FOCUS DBG] no act_lightweight_viewer_v2 attribute")
            return
        if not self._is_lightweight_viewer_active_v2():
            logger.debug("[ZONE FOCUS DBG] lightweight viewer NOT checked → skip")
            return  # legacy viewport active — skip

        overlay = self._active_overlays_by_zone.get(zone_id)
        if not overlay:
            logger.debug(
                "[ZONE FOCUS DBG] zone_id=%r NOT in _active_overlays_by_zone "
                "(have %d zones: %s)",
                zone_id, len(self._active_overlays_by_zone),
                list(self._active_overlays_by_zone.keys())[:5],
            )
            return

        active_pair = ""
        if isinstance(self._active_row, dict):
            active_pair = str(self._active_row.get("pair_id") or "")
        # Phase G2.7-COORDFIX2 — active-zone overlays come from the dashboard
        # ``top_issues`` list, which skips ``_push_overlays_to_lightweight_v2``'s
        # coord backfill. Without ``bbox_coordinate_space``/``pdf_dpi`` a PDF
        # bbox (image_pixels) passes through convert_bbox_to_world_space
        # UNCHANGED → camera zooms to pixel coords off the points-space page,
        # so the zone-focus zoom never matched the list pick. Backfill first.
        if active_pair:
            try:
                overlay = self._backfill_pdf_overlay_coord_space_v2(
                    active_pair, [overlay]
                )[0]
            except Exception:
                logger.debug("zone-focus coord backfill failed", exc_info=True)

        # Phase G2.7-COORDFIX — for PDF overlays, the bbox is in
        # ``image_pixels`` at ``pdf_dpi`` while the lightweight viewport's
        # world space is in PDF points. Without this conversion the camera
        # would zoom to a position several times outside the page bounds.
        from src.gui.lightweight_viewport import (
            _page_height_points_from_world_bbox,
            convert_bbox_to_world_space,
        )
        bbox_space = str(overlay.get("bbox_coordinate_space") or "")
        try:
            pdf_dpi_val = float(overlay.get("pdf_dpi") or 0.0)
        except (TypeError, ValueError):
            pdf_dpi_val = 0.0
        logger.debug(
            "[ZONE FOCUS DBG] zone_id=%r overlay keys=%s bbox_space=%r "
            "pdf_dpi=%s bbox=%s old_bbox=%s",
            zone_id,
            sorted(overlay.keys())[:15],
            bbox_space, pdf_dpi_val,
            overlay.get("bbox"), overlay.get("old_bbox"),
        )

        match_side = resolve_overlay_match_side(str(overlay.get("change_type") or ""))
        any_focused = False
        for vp, key in (
            (self.preview_before_lightweight_v2, "old_bbox"),
            (self.preview_after_lightweight_v2, "bbox"),
        ):
            side_label = "before" if key == "old_bbox" else "after"
            if vp is None:
                logger.debug("[ZONE FOCUS DBG]   side=%s viewport=None", side_label)
                continue
            if side_label == "before" and match_side == "b_only":
                logger.debug("[ZONE FOCUS DBG]   side=before skipped for added-only zone")
                continue
            if side_label == "after" and match_side == "a_only":
                logger.debug("[ZONE FOCUS DBG]   side=after skipped for deleted-only zone")
                continue
            raw = overlay.get(key)
            if raw is None and match_side in {"matched", "mixed"}:
                raw = overlay.get("bbox") or overlay.get("old_bbox")
            if not raw:
                logger.debug(
                    "[ZONE FOCUS DBG]   side=%s NO raw bbox (key=%r)", side_label, key,
                )
                continue
            world_bbox = convert_bbox_to_world_space(
                raw,
                coordinate_space=bbox_space,
                pdf_dpi=pdf_dpi_val,
                page_height_points=_page_height_points_from_world_bbox(
                    getattr(vp, "world_bbox", (0.0, 0.0, 0.0, 0.0))
                ),
            )
            if world_bbox is None:
                logger.debug(
                    "[ZONE FOCUS DBG]   side=%s convert_bbox_to_world_space "
                    "returned None for raw=%s", side_label, raw,
                )
                continue
            self._lightweight_camera_sync_in_progress = True
            try:
                vp.set_camera_to_world_bbox(world_bbox, padding_ratio=0.4)
                any_focused = True
                logger.debug(
                    "[ZONE FOCUS DBG]   side=%s set_camera_to_world_bbox "
                    "OK world_bbox=%s", side_label, world_bbox,
                )
                self._schedule_lightweight_visible_tile_window_v2(side_label)
            except Exception as exc:
                logger.warning(
                    "[ZONE FOCUS DBG]   side=%s set_camera_to_world_bbox "
                    "raised: %s", side_label, exc,
                )
            finally:
                self._lightweight_camera_sync_in_progress = False
        logger.debug(
            "[ZONE FOCUS DBG] zone_id=%r any_focused=%s", zone_id, any_focused,
        )

        # Refresh overlays so the picked zone shows as focus, others as cloud.
        if active_pair:
            self._push_overlays_to_lightweight_v2(active_pair, focus_zone_id=zone_id)

    def _set_lightweight_zone_side_messages_v2(self, zone_id: str = "") -> None:
        before_msg = ""
        after_msg = ""
        overlay = self._active_overlays_by_zone.get(str(zone_id or ""), {})
        if isinstance(overlay, dict):
            match_side = resolve_overlay_match_side(str(overlay.get("change_type") or ""))
            if match_side == "b_only":
                before_msg = "이전 도면에는 대응 요소가 없습니다"
            elif match_side == "a_only":
                after_msg = "변경 도면에는 대응 요소가 없습니다"
            elif match_side == "mixed":
                before_msg = "혼합 변경: 양쪽 위치를 함께 확인"
                after_msg = "혼합 변경: 양쪽 위치를 함께 확인"
        for viewport, message in (
            (getattr(self, "preview_before_lightweight_v2", None), before_msg),
            (getattr(self, "preview_after_lightweight_v2", None), after_msg),
        ):
            if viewport is None or not hasattr(viewport, "set_side_message"):
                continue
            try:
                viewport.set_side_message(message)
            except Exception:
                logger.debug("Could not set lightweight side message", exc_info=True)

    def _on_toggle_lightweight_viewer_v2(self, checked: bool) -> None:
        """Phase G2.2 — Toggle visibility of the lightweight viewport.

        Hides the legacy raster viewport and shows the new vector viewport
        (or vice versa). When enabling, immediately requests session state
        for any currently selected pair so the user sees data without
        having to re-click.

        Phase G3.1 — also records that the user touched the toggle so the
        auto-enable logic for DXF/DWG runs doesn't overwrite their explicit
        choice on the next pair selection.
        """

        if not QT_QUICK_AVAILABLE:
            checked = False
            action = getattr(self, "act_lightweight_viewer_v2", None)
            if action is not None and action.isChecked():
                previous = action.blockSignals(True)
                try:
                    action.setChecked(False)
                finally:
                    action.blockSignals(previous)
            self._user_picked_lightweight_v2 = False
            self._set_lightweight_viewer_visible_v2(False)
            return

        if DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
            checked = True
            action = getattr(self, "act_lightweight_viewer_v2", None)
            if action is not None and not action.isChecked():
                previous = action.blockSignals(True)
                try:
                    action.setChecked(True)
                finally:
                    action.blockSignals(previous)
            self._user_picked_lightweight_v2 = False

        # Distinguish user click from programmatic auto-flip via the
        # ``_auto_toggle_in_progress`` flag the auto-enabler sets while
        # invoking ``setChecked``.
        if (
            not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY
            and not getattr(self, "_auto_toggle_in_progress_v2", False)
        ):
            self._user_picked_lightweight_v2 = True
            logger.debug("User manually toggled lightweight viewer (checked=%s)", checked)

        # Swap visibility of the two viewport widgets on each side.
        self._set_lightweight_viewer_visible_v2(checked)

        if not checked:
            return

        # On enable: re-trigger the session for the active pair so the
        # lightweight viewport gets its primitives without requiring the
        # user to re-click the row.
        active_pair = ""
        if isinstance(self._active_row, dict):
            active_pair = str(self._active_row.get("pair_id") or "")
        if not active_pair:
            return

        session = getattr(self, "_viewer_session", None)
        if session is None:
            return
        for side in ("before", "after"):
            try:
                state = session.get_pair_state(active_pair, side=side)  # type: ignore[arg-type]
            except Exception:
                continue
            self._apply_session_state_to_viewport_v2(
                active_pair, side, state.render_mode,
            )
            # Also (re-)trigger the build in case it never started.
            try:
                session.select_pair(active_pair, side=side)  # type: ignore[arg-type]
            except Exception:
                pass

        logger.info(
            "Lightweight viewport ENABLED (toggle ON) for pair=%s",
            active_pair or "<none>",
        )

    def _populate_summary_v2(self) -> None:
        if not self._result:
            return
        summary = self._result.compare_summary
        artifact = self._result.artifact_package
        self.summary_labels["completed"].setText(_format_count(summary.completed_pairs))
        self.summary_labels["failed"].setText(_format_count(summary.failed_pairs))
        self.summary_labels["raw"].setText(_format_count(artifact.raw_change_count))
        self.summary_labels["zones"].setText(_format_count(artifact.zone_count))
        totals = self._dashboard.get("totals", {}) if self._dashboard else {}
        self.summary_labels["issues"].setText(_format_count(totals.get("review_issue_count")))
        self.summary_labels["patterns"].setText(_format_count(totals.get("folded_pattern_count")))
        self.summary_labels["cloud"].setText(_format_count(artifact.cloud_region_count))
        self.summary_labels["omitted"].setText(_format_count(artifact.cloud_omitted_zone_count))
        self._update_review_queue_summary_v2()
        self._update_viewer_perf_summary_v2()
        from src.gui.failure_badge import badge_codes_for_run  # P0-1 honesty badge refresh
        self.failure_badge.set_failure_codes(badge_codes_for_run(
            (self.preview_before_lightweight_v2, self.preview_after_lightweight_v2),
            self._result.compare_summary.items))

    def _update_viewer_perf_summary_v2(self) -> None:
        if not hasattr(self, "lbl_viewer_perf_v2"):
            return
        perf = summarize_viewer_perf(self._viewer_root)
        self._viewer_perf_summary = perf
        self.lbl_viewer_perf_v2.setText(format_viewer_perf_summary_korean(perf))
        # Phase G3 — keep the label live. Viewport interactions
        # (pan/zoom) append events to viewer_perf.json continuously, so
        # a one-shot update at compare time becomes stale within
        # seconds of opening a drawing. Refresh every 5s when a result
        # is loaded; the timer is created lazily on first call.
        timer = getattr(self, "_viewer_perf_refresh_timer_v2", None)
        if timer is None and self._result is not None:
            timer = QTimer(self)
            timer.setInterval(5000)  # 5 s
            timer.timeout.connect(self._refresh_viewer_perf_summary_only)
            timer.start()
            self._viewer_perf_refresh_timer_v2 = timer

    def _refresh_viewer_perf_summary_only(self) -> None:
        """Lightweight refresh — only re-reads viewer_perf.json + sets
        the label. Doesn't trigger the full _populate_summary_v2 chain.
        """

        if not hasattr(self, "lbl_viewer_perf_v2"):
            return
        try:
            perf = summarize_viewer_perf(self._viewer_root)
            self._viewer_perf_summary = perf
            self.lbl_viewer_perf_v2.setText(format_viewer_perf_summary_korean(perf))
        except Exception:
            logger.debug("viewer_perf refresh failed", exc_info=True)

    def _populate_top_issues_v2(self) -> None:
        if not hasattr(self, "top_issues_list_v2"):
            return
        self.top_issues_list_v2.clear()
        queue = self._dashboard.get("review_queue", {}) if self._dashboard else {}
        issues = queue.get("items") if isinstance(queue, dict) else None
        if not isinstance(issues, list) or not issues:
            issues = self._dashboard.get("top_project_issues") if self._dashboard else None
        if not isinstance(issues, list):
            issues = self._dashboard.get("top_issues") if self._dashboard else None
        issues = issues if isinstance(issues, list) else []
        for issue in issues[:200]:
            if not isinstance(issue, dict):
                continue
            item = QListWidgetItem(format_top_issue_label(issue))
            item.setData(Qt.UserRole, (str(issue.get("pair_id") or ""), str(issue.get("zone_id") or "")))
            self.top_issues_list_v2.addItem(item)
        if hasattr(self, "lbl_top_issues_empty_v2"):
            self.lbl_top_issues_empty_v2.setVisible(self.top_issues_list_v2.count() == 0)

    def _populate_pattern_groups_v2(self) -> None:
        if not hasattr(self, "pattern_group_list_v2"):
            return
        self.pattern_group_list_v2.clear()
        patterns = self._dashboard.get("layer_patterns") if self._dashboard else None
        if not isinstance(patterns, list):
            patterns = self._dashboard.get("pattern_groups") if self._dashboard else None
        patterns = patterns if isinstance(patterns, list) else []
        for pattern in patterns[:200]:
            if not isinstance(pattern, dict):
                continue
            item = QListWidgetItem(format_pattern_group_label(pattern))
            item.setData(Qt.UserRole, str(pattern.get("pattern") or ""))
            self.pattern_group_list_v2.addItem(item)
        if hasattr(self, "lbl_pattern_groups_empty_v2"):
            self.lbl_pattern_groups_empty_v2.setVisible(self.pattern_group_list_v2.count() == 0)

    def _on_top_issue_activated_v2(self, item: QListWidgetItem) -> None:
        if not item:
            return
        payload = item.data(Qt.UserRole) or ("", "")
        try:
            pair_id, zone_id = payload
        except (TypeError, ValueError):
            return
        self._jump_to_pair_zone_v2(str(pair_id or ""), str(zone_id or ""))

    def _on_pattern_group_activated_v2(self, item: QListWidgetItem) -> None:
        if not item:
            return
        pattern = str(item.data(Qt.UserRole) or "")
        if not pattern:
            return
        self._active_pattern_filter_v2 = pattern
        if hasattr(self, "btn_clear_pattern_filter_v2"):
            self.btn_clear_pattern_filter_v2.setEnabled(True)
        self._refresh_drawing_list_v2()

    def _clear_pattern_filter_v2(self) -> None:
        if not self._active_pattern_filter_v2:
            return
        self._active_pattern_filter_v2 = ""
        if hasattr(self, "btn_clear_pattern_filter_v2"):
            self.btn_clear_pattern_filter_v2.setEnabled(False)
        self._refresh_drawing_list_v2()

    def _jump_to_pair_zone_v2(self, pair_id: str, zone_id: str) -> None:
        """Activate the row for ``pair_id`` and the zone list entry for ``zone_id``."""

        if not pair_id or not hasattr(self, "drawing_list_v2"):
            return
        for index in range(self.drawing_list_v2.count()):
            item = self.drawing_list_v2.item(index)
            row_data = item.data(Qt.UserRole) or {}
            if str(row_data.get("pair_id") or "") == pair_id:
                self.drawing_list_v2.setCurrentItem(item)
                break
        if zone_id and hasattr(self, "zone_list_v2"):
            QTimer.singleShot(0, lambda: self._select_zone_in_list_v2(zone_id))

    def _row_matches_pattern_v2(self, row: dict, pattern: str) -> bool:
        """Return True when a drawing row participates in the named layer pattern.

        Prefers the per-drawing ``top_issues`` from the dashboard (each issue has
        a ``pattern_group``). Falls back to a layer-name substring check so rows
        loaded from the priority CSV (which lack ``top_issues``) still respond
        when the pattern label matches a layer keyword.
        """

        if not isinstance(row, dict) or not pattern:
            return False
        for issue in row.get("top_issues") or []:
            if isinstance(issue, dict) and str(issue.get("pattern_group") or "") == pattern:
                return True
        layers = str(row.get("top_layers") or row.get("major_layers") or "")
        return pattern in layers

    def _update_review_queue_summary_v2(self) -> None:
        if not hasattr(self, "lbl_review_queue_v2"):
            return
        queue = self._dashboard.get("review_queue", {}) if self._dashboard else {}
        action_counts = self._local_action_counts_v2()
        if not action_counts.get("total") and self._dashboard:
            dashboard_actions = self._dashboard.get("action_counts") or {}
            action_counts = {
                "total": sum(_int_value(value) for value in dashboard_actions.values()),
                "confirmed": _int_value(dashboard_actions.get("confirmed")),
                "hold": _int_value(dashboard_actions.get("hold") or dashboard_actions.get("ignored")),
                "false_positive": _int_value(dashboard_actions.get("false_positive")),
                "needs_review": _int_value(dashboard_actions.get("needs_review")),
            }
        preview_counts = self._dashboard.get("preview_status_counts", {}) if self._dashboard else {}
        unmatched = _int_value(queue.get("unmatched_count"))
        blocked = _int_value(queue.get("blocked_count"))
        failed = _int_value(queue.get("failed_count"))
        structural_core = _int_value(queue.get("structural_core_issue_count"))
        queue_items = queue.get("items") if isinstance(queue, dict) else []
        queue_count = len(queue_items) if isinstance(queue_items, list) else structural_core
        text = (
            "업무 큐: "
            f"자동 비교 완료 {_format_count(queue.get('auto_completed_count') or (self._result.confirmed_pairs if self._result else 0))} · "
            f"구조 핵심 {_format_count(structural_core)} · "
            f"우선 검토 표시 {_format_count(queue_count)} · "
            f"반복 패턴 {_format_count(queue.get('pattern_group_count'))} · "
            f"미매칭 {_format_count(unmatched)} · 차단 {_format_count(blocked)} · 실패 {_format_count(failed)}\n"
            "검토 상태: "
            f"확인 {_format_count(action_counts.get('confirmed'))} · "
            f"보류 {_format_count(action_counts.get('hold'))} · "
            f"오탐 {_format_count(action_counts.get('false_positive'))} · "
            f"미검토 {_format_count(action_counts.get('needs_review'))} / "
            f"미리보기: 실도면 {_format_count(preview_counts.get('real_preview'))}, "
            f"상대 위치 {_format_count(preview_counts.get('relative_only'))}, "
            f"실패 {_format_count(preview_counts.get('render_failed'))}"
        )
        self.lbl_review_queue_v2.setText(text)

    def _local_action_counts_v2(self) -> dict[str, int]:
        counts = {"confirmed": 0, "hold": 0, "false_positive": 0, "needs_review": 0, "total": 0}
        for record in self._review_records_v2.values():
            status = normalize_review_status(record.status)
            counts[status] += 1
            counts["total"] += 1
        return counts

    def _refresh_drawing_list_v2(self) -> None:
        current_filter = self.cmb_drawing_filter_v2.currentText() if hasattr(self, "cmb_drawing_filter_v2") else "전체"
        pattern_filter = getattr(self, "_active_pattern_filter_v2", "") or ""
        self.drawing_list_v2.clear()
        for row in self._drawing_rows:
            if current_filter != "전체" and row.get("grade") != current_filter:
                continue
            if pattern_filter and not self._row_matches_pattern_v2(row, pattern_filter):
                continue
            # Phase G3.8 — per-row review progress badge so the user can see
            # which drawings still need attention without clicking through.
            #   ✅ 완료    — every zone reviewed
            #   ⏳ N/M    — N reviewed of M total
            #   ▫ 미시작  — nothing reviewed yet
            pair_id = str(row.get("pair_id") or "")
            progress_badge = self._drawing_progress_badge_v2(pair_id)
            text = (
                f"{row.get('drawing_number') or row.get('pair_id')}  {progress_badge}\n"
                f"{row.get('grade')} · 우선순위 {float(row.get('priority_score') or 0.0):.1f} · "
                f"변경 {_format_count(row.get('raw_change_count'))} · "
                f"구역 {_format_count(row.get('zone_count'))} · "
                f"검토 {_format_count(row.get('review_issue_count'))} · "
                f"접힘 {_format_count(row.get('folded_issue_count'))}"
            )
            # G2.7-COORDFIX-2 — PDF pairs always have preview_available=False
            # (PNG-to-screen path doesn't apply; lightweight viewer renders
            # Qt PDF directly). Don't mis-label that as failure.
            pair_id_for_check = str(row.get("pair_id") or "")
            viewer_pair_for_check = self._viewer_pairs_by_id.get(
                pair_id_for_check, {}
            )
            is_pdf_row = (
                _viewer_pair_is_pdf(viewer_pair_for_check)
                if viewer_pair_for_check else False
            )
            if is_pdf_row:
                text += " · 신형 뷰어 PDF"
            else:
                if row.get("preview_available") is False:
                    text += " · 미리보기 실패"
                text += f" - {_preview_status_label(row.get('preview_status'), row.get('preview_available'))}"
            if row.get("after_marked_dxf"):
                text += " · 구름마크 있음"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, row)
            self.drawing_list_v2.addItem(item)

    def _drawing_progress_badge_v2(self, pair_id: str) -> str:
        """Phase G3.8 — Compute the per-drawing progress badge string.

        Returns a compact prefix like ``"✅ 완료"``, ``"⏳ 3/12"``, or
        ``"▫ 미시작"`` based on the cached overlay zones + review records
        for ``pair_id``. Empty pairs return ``""`` (no badge).
        """

        if not pair_id:
            return ""
        cache = getattr(self, "_viewer_overlay_cache", {}) or {}
        overlays = cache.get(pair_id) or []
        if not overlays:
            total = self._viewer_declared_overlay_count_for_pair_v2(pair_id)
            if total <= 0:
                return ""
            done, confirmed = self._review_record_counts_for_pair_v2(pair_id)
            if done <= 0:
                return "▫ 미시작"
            if done >= total:
                return f"✅ 완료 (확인 {confirmed}건)"
            return f"⏳ {done}/{total}"
        total = 0
        done = 0
        confirmed = 0
        for ov in overlays:
            if not isinstance(ov, dict):
                continue
            zid = str(ov.get("zone_id") or "")
            if not zid:
                continue
            total += 1
            status = self._review_status_for_zone_v2(pair_id, zid)
            if status != "needs_review":
                done += 1
            if status == "confirmed":
                confirmed += 1
        if total == 0:
            return ""
        if done == 0:
            return "▫ 미시작"
        if done >= total:
            return f"✅ 완료 (확인 {confirmed}건)"
        return f"⏳ {done}/{total}"

    def _on_drawing_selected_v2(self, current, _previous=None) -> None:
        if not current:
            return
        selection_started = perf_counter()
        row = current.data(Qt.UserRole) or {}
        self._active_row = row
        self._active_zone_id = ""
        # Phase H multi-page nav — reset page index whenever the user
        # switches to a different drawing pair.
        self._active_pdf_page_index_v2 = 0
        self._set_zone_action_buttons_enabled_v2(False)
        pair_id = str(row.get("pair_id") or "")
        self._cancel_full_zone_tree_rebuild_v2("pair_selection", bump_generation=True)
        self._cancel_visible_tile_window_v2("pair_selection", bump_generation=True)
        # Phase G2.7-DIAG: log drawing-selection so we can trace the
        # silent preview-failure path. Captures pair_id, source kind
        # (pdf/dxf), preview availability, and viewer_pair completeness.
        logger.info(
            "[drawing selected] pair_id=%s row_keys=%s",
            pair_id, sorted(row.keys()) if isinstance(row, dict) else "<not-dict>",
        )
        preview = self._preview_by_pair.get(pair_id)
        viewer_pair = self._viewer_pairs_by_id.get(pair_id, {})
        if not viewer_pair:
            viewer_pair = self._viewer_pair_from_row_v2(pair_id, row)
            logger.info(
                "[drawing selected] viewer_pair NOT in cache — synthesised "
                "from row (pair_id=%s, keys=%s)",
                pair_id,
                sorted(viewer_pair.keys()) if isinstance(viewer_pair, dict) else "<empty>",
            )
        self._apply_active_pdf_page_pair_to_viewer_pair_v2(viewer_pair)
        logger.info(
            "[drawing selected] preview=%s before_image=%r after_image=%r "
            "is_pdf=%s render_status=%r",
            "present" if preview else "MISSING",
            str(viewer_pair.get("before_image") or "")[:80],
            str(viewer_pair.get("after_image") or "")[:80],
            _viewer_pair_is_pdf(viewer_pair),
            viewer_pair.get("render_status") or "<unset>",
        )
        render_message = (
            "PDF 배경 생성 중 - 변경구역 위치를 준비하고 있습니다."
            if _viewer_pair_is_pdf(viewer_pair)
            else "렌더 중 - 변경구역 위치를 준비하고 있습니다."
        )
        self._set_preview_status_v2(pair_id, "rendering", render_message)
        overlays, full_tree_deferred, initial_overlay_source = (
            self._initial_overlays_for_pair_selection_v2(
                pair_id,
                row,
                preview,
                viewer_pair,
            )
        )
        self._active_all_overlays_by_zone = {
            str(overlay.get("zone_id") or ""): overlay
            for overlay in overlays
            if isinstance(overlay, dict) and overlay.get("zone_id")
        }
        overlays = self._visible_overlays_for_pdf_page_v2(
            pair_id,
            viewer_pair,
            overlays,
            load_when_empty=False,
        )
        self._set_active_overlays_v2(overlays)
        self._set_preview_status_v2(pair_id, self._render_status_by_pair.get(pair_id, "not_requested"))
        before_image = str(viewer_pair.get("before_image") or (preview.before_image if preview else ""))
        after_image = str(viewer_pair.get("after_image") or (preview.after_image if preview else ""))
        tile_manifest = (
            self._tile_manifest_for_pair_v2(pair_id, viewer_pair)
            if self._selection_build_lod_tiles_enabled_v2()
            else {}
        )
        # Phase G2.7-DIAG — extract the rendered image DPI from the
        # transform metadata so the viewport can scale overlay bboxes
        # (which are at the comparison `pdf_dpi`, typically 200) to
        # match the rendered image (`preview_dpi`, typically 400).
        before_transform = viewer_pair.get("before_transform") or {}
        after_transform = viewer_pair.get("after_transform") or {}
        before_image_dpi = (
            float(before_transform.get("dpi") or 0)
            if isinstance(before_transform, dict) else 0.0
        )
        after_image_dpi = (
            float(after_transform.get("dpi") or 0)
            if isinstance(after_transform, dict) else 0.0
        )
        status = self._render_status_by_pair.get(pair_id, "not_requested")
        message = "상대위치 표시 - 실제 미리보기가 준비되면 정확한 도면 위치로 확대됩니다."
        if status == "rendering":
            message = "렌더 중 - 변경구역 위치를 준비하고 있습니다."
        elif status == "render_timeout":
            message = "원본 도면 렌더링 시간이 초과되었습니다. 현재 화면은 실제 도면 배경이 아닌 상대 위치 오버레이입니다."
        elif status == "failed":
            message = "미리보기 실패 - 실제 도면 배경 없이 상대위치 표시로 변경구역을 확인합니다."
        if not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
            self.preview_before_v2.load_preview(
                before_image,
                overlays,
                before=True,
                fallback_message=message,
                tile_manifest=tile_manifest,
                viewer_root=self._viewer_root,
                pair_id=pair_id,
                image_dpi=before_image_dpi,
            )
            self.preview_after_v2.load_preview(
                after_image,
                overlays,
                before=False,
                fallback_message=message,
                tile_manifest=tile_manifest,
                viewer_root=self._viewer_root,
                pair_id=pair_id,
                image_dpi=after_image_dpi,
            )
        # Phase F P0 — push the v2 fidelity + job status to both viewports so
        # the badge + relative_only watermark reflect what the user actually
        # sees. When no v2 entry exists (e.g. legacy run), the viewport keeps
        # its safe default of relative_only.
        # Phase G3.5 fix — defensive getattr because _v2_fidelity_by_pair_id
        # is only created inside _load_viewer_manifest_v2; if that load
        # path was skipped (no result loaded yet, or v2 sidecar missing),
        # the attribute doesn't exist and the click would AttributeError.
        fidelity_map = getattr(self, "_v2_fidelity_by_pair_id", {}) or {}
        fidelity, job_status = fidelity_map.get(
            pair_id, ("relative_only", "idle")
        )
        if not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
            try:
                self.preview_before_v2.set_fidelity_state(fidelity, job_status)
                self.preview_after_v2.set_fidelity_state(fidelity, job_status)
            except Exception as exc:
                logger.debug("Failed to push fidelity state: %s", exc)

        # Phase G2.7 — when lightweight viewer is active AND the pair is
        # PDF, also push the source PDF into the lightweight viewports
        # (they re-render at zoom-appropriate DPI via Qt PDF, sharper
        # than the legacy fixed-DPI PNG). For DXF/DWG the lightweight
        # viewport is populated by _apply_session_state_to_viewport_v2
        # via the ViewerSession scene_pack pipeline, not here.
        if self._is_lightweight_viewer_active_v2():
            self._schedule_lightweight_pair_load_v2(pair_id, viewer_pair)

        # Phase H multi-page nav — toggle the page navigator widget
        # based on whether this pair has > 1 matched page pairs. For
        # single-page PDFs / DXF / DWG it stays hidden.
        self._update_page_nav_v2(viewer_pair)

        # Phase G2.1 — trigger lazy scene pack build for this pair via the
        # ViewerSession orchestrator. The build runs on a background thread
        # pool; first cache hit is instant, cold builds take 1-10 s for big
        # DWG. Currently log-only — G2.2 will wire the QML lightweight
        # viewport to consume the resulting RenderMode + ScenePackRef and
        # render the skeleton/vector/raster layers.
        session = getattr(self, "_viewer_session", None)
        if session is not None:
            try:
                # Both sides — before/after — get scheduled. The session
                # de-dupes when two sides share the same source.
                for side in ("before", "after"):
                    session.select_pair(pair_id, side=side)  # type: ignore[arg-type]
            except Exception as exc:
                logger.debug("ViewerSession.select_pair failed: %s", exc)
        self.lbl_selected_summary_v2.setText(
            f"{row.get('drawing_number') or pair_id}\n"
            f"변경량: {row.get('grade')} / 우선순위 {float(row.get('priority_score') or 0.0):.1f} / 총 변경 {_format_count(row.get('raw_change_count'))}\n"
            f"변경구역 {_format_count(row.get('zone_count'))}, "
            f"우선 검토 {_format_count(row.get('review_issue_count'))}, "
            f"반복 접힘 {_format_count(row.get('folded_issue_count'))}, "
            f"구름마크 {_format_count(row.get('cloud_region_count'))}, "
            f"생략 {_format_count(row.get('cloud_omitted_zone_count'))}\n"
            f"주요 레이어: {row.get('top_layers') or '-'}\n"
            f"{self._preview_status_label_v2(row, viewer_pair)}"
        )
        self.btn_open_marked_v2.setEnabled(bool(row.get("after_marked_dxf")))
        # E2 — populate the per-zone category cache before the list builds so
        # the zone label can prefix the heuristic category badge.
        self._compute_zone_categories_for_pair_v2(pair_id, overlays)
        self._populate_zone_list_v2(preview, overlays)
        self._refresh_zone_list_filter_v2()
        self._update_review_progress_v2()
        self._update_category_summary_v2()
        if full_tree_deferred:
            self._schedule_full_zone_tree_rebuild_v2(pair_id)
        # Always trigger background rendering on selection when the pair lacks a
        # ready PNG, regardless of input type. Both PDF (PyMuPDF) and DWG/DXF
        # (ezdxf+matplotlib) feed the same GpuDrawingViewport, so the user sees
        # the actual drawing instead of a relative-position overlay placeholder.
        if self._pair_needs_render_v2(pair_id, viewer_pair, overlays):
            self._start_pair_render_v2(pair_id, viewer_pair, row)
        # Phase I2 — pick the first leaf (zone) item in the tree, not
        # the first top-level (which is a category header without zone_id).
        # R6-C defers this until after the pair background/list has had a
        # chance to paint; otherwise drawing selection immediately fans out into
        # crop + vector render work before the user sees the selected pair.
        if not self.zone_list_v2.currentItem():
            self._schedule_initial_zone_selection_v2(pair_id)
        self._record_pair_selection_event_v2(
            pair_id,
            selection_started,
            initial_overlay_count=len(overlays),
            full_tree_deferred=full_tree_deferred,
            initial_source=initial_overlay_source,
            viewer_pair=viewer_pair,
        )

    def _viewer_overlays_for_pair_v2(self, pair_id: str) -> list[dict]:
        if not pair_id:
            return []
        if pair_id in self._viewer_overlay_cache:
            self._touch_viewer_overlay_cache_v2(pair_id)
            return self._viewer_overlay_cache[pair_id]
        pages_manifest_path = self._viewer_overlay_pages_manifest_path_for_pair_v2(pair_id)
        if pages_manifest_path is not None and pages_manifest_path.exists():
            overlays = list(iter_overlay_page_store(pages_manifest_path))
            self._cache_viewer_overlays_v2(pair_id, overlays)
            return overlays
        pair = self._viewer_pairs_by_id.get(pair_id, {})
        overlay_path = str(pair.get("overlay_json") or "")
        if not overlay_path:
            return []
        path = _resolve_viewer_artifact_path(overlay_path, self._viewer_root)
        if not path or not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            overlays = payload.get("overlays", [])
            if not isinstance(overlays, list):
                overlays = []
        except Exception:
            overlays = []
        self._cache_viewer_overlays_v2(pair_id, overlays)
        return overlays

    def _tile_manifest_for_pair_v2(self, pair_id: str, viewer_pair: Optional[dict] = None) -> dict:
        if not pair_id:
            return {}
        candidates: list[Path] = []
        pair = viewer_pair or self._viewer_pairs_by_id.get(pair_id, {})
        expected_cache_key = str(pair.get("tile_cache_key") or "") if isinstance(pair, dict) else ""
        if isinstance(pair, dict) and pair.get("tile_manifest"):
            candidate = _resolve_viewer_artifact_path(pair.get("tile_manifest"), self._viewer_root)
            if candidate:
                candidates.append(candidate)
        if self._viewer_manifest.get("tiles_manifest"):
            candidate = _resolve_viewer_artifact_path(
                self._viewer_manifest.get("tiles_manifest"), self._viewer_root,
            )
            if candidate:
                candidates.append(candidate)
        if self._viewer_root:
            candidates.append(self._viewer_root / "tiles_manifest.json")
        for path in candidates:
            try:
                if not path.exists():
                    continue
                stat = path.stat()
                cache_key = (
                    str(path.resolve()),
                    int(stat.st_mtime_ns),
                    int(stat.st_size),
                    pair_id,
                    expected_cache_key,
                )
                cached = self._tile_manifest_cache_v2.get(cache_key)
                if cached is not None:
                    return dict(cached)
                payload = json.loads(path.read_text(encoding="utf-8"))
                manifest = None
                if isinstance(payload, dict) and str(payload.get("pair_uuid") or "") == pair_id:
                    manifest = payload
                else:
                    pairs = payload.get("pairs", {}) if isinstance(payload, dict) else {}
                    manifest = pairs.get(pair_id) if isinstance(pairs, dict) else None
                if isinstance(manifest, dict):
                    if expected_cache_key and str(manifest.get("cache_key") or "") != expected_cache_key:
                        continue
                    self._tile_manifest_cache_v2[cache_key] = dict(manifest)
                    return dict(manifest)
            except Exception:
                logger.debug("Failed to read tile manifest for %s from %s", pair_id, path, exc_info=True)
        return {}

    def _overlay_json_file_size_for_pair_v2(self, pair_id: str) -> int:
        path = self._viewer_overlay_json_path_for_pair_v2(pair_id)
        if path is None:
            return 0
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0

    def _set_preview_status_v2(self, pair_id: str, status: str, message: str = "") -> None:
        normalized = status if status in RENDER_STATUS_LABELS else "not_requested"
        self._render_status_by_pair[pair_id] = normalized
        label = RENDER_STATUS_LABELS.get(normalized, "렌더 대기")
        if normalized not in {"ready", "gpu_ready", "tile_ready", "pdf_render"}:
            label = f"{label} · 상대위치 표시"
        if message:
            label = f"{label}\n{message}"
        self.lbl_preview_status_v2.setText(label)

    def _sync_preview_viewport_v2(self, target: GpuDrawingViewport, zoom: float, pan_x: float, pan_y: float) -> None:
        if self._syncing_preview_viewports:
            return
        self._syncing_preview_viewports = True
        try:
            target.apply_viewport(zoom, pan_x, pan_y)
        finally:
            self._syncing_preview_viewports = False
        # C2 — keep the zoom slider/label in sync with mouse-wheel zoom changes
        if hasattr(self, "sld_zoom_v2"):
            slider_value = int(round(max(0.2, min(8.0, float(zoom))) * 100))
            self.sld_zoom_v2.blockSignals(True)
            self.sld_zoom_v2.setValue(slider_value)
            self.sld_zoom_v2.blockSignals(False)
            if hasattr(self, "lbl_zoom_value_v2"):
                self.lbl_zoom_value_v2.setText(f"{slider_value}%")

    def _cancel_visible_tile_window_v2(self, reason: str, *, bump_generation: bool = True) -> None:
        self._visible_tile_request_timer_v2.stop()
        self._visible_tile_pending_request_v2 = None
        if bump_generation:
            self._visible_tile_generation_v2 += 1
        if self._visible_tile_worker_v2 is not None:
            self._retire_visible_tile_worker_v2()
        if reason and self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "visible_tile_window_cancel",
                reason=reason,
                generation=self._visible_tile_generation_v2,
            )

    def _schedule_visible_tile_window_v2(self, pair_id: str, viewport_rect: object, zoom: float) -> None:
        pair_id = str(pair_id or "")
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        if not pair_id or pair_id != current_pair or not self._viewer_root:
            return
        if not self._selection_build_lod_tiles_enabled_v2():
            return
        pair = self._viewer_pairs_by_id.get(pair_id, {})
        if not isinstance(pair, dict):
            return
        manifest = self._tile_manifest_for_pair_v2(pair_id, pair)
        if manifest.get("pyramid_complete") is not False:
            return
        if not isinstance(viewport_rect, dict):
            return
        try:
            request_rect = {
                "x": float(viewport_rect.get("x", 0.0)),
                "y": float(viewport_rect.get("y", 0.0)),
                "width": max(1.0, float(viewport_rect.get("width", 1.0))),
                "height": max(1.0, float(viewport_rect.get("height", 1.0))),
            }
            request_zoom = float(zoom or 1.0)
        except (TypeError, ValueError):
            return
        self._visible_tile_pending_request_v2 = {
            "pair_id": pair_id,
            "viewer_pair": dict(pair),
            "viewport_rect": request_rect,
            "zoom": request_zoom,
            "cache_key": str(pair.get("tile_cache_key") or manifest.get("cache_key") or ""),
            "generation": self._visible_tile_generation_v2,
        }
        self._visible_tile_request_timer_v2.start()

    def _run_pending_visible_tile_window_v2(self) -> None:
        request = self._visible_tile_pending_request_v2
        if not isinstance(request, dict):
            return
        if self._visible_tile_worker_v2 is not None and self._visible_tile_worker_v2.isRunning():
            return
        self._visible_tile_pending_request_v2 = None
        pair_id = str(request.get("pair_id") or "")
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        if not pair_id or pair_id != current_pair or not self._viewer_root:
            return
        overlays = list(self._active_all_overlays_by_zone.values()) or list(self._active_overlays_by_zone.values())
        worker = VisibleTileWindowWorker(
            pair_id=pair_id,
            generation=int(request.get("generation") or 0),
            viewer_pair=dict(request.get("viewer_pair") or {}),
            overlays=overlays,
            viewer_root=self._viewer_root,
            viewer_cache_root=self._viewer_cache_root_v2(),
            viewport_rect=dict(request.get("viewport_rect") or {}),
            zoom=float(request.get("zoom") or 1.0),
            cache_key=str(request.get("cache_key") or ""),
        )
        worker.finished.connect(self._on_visible_tile_window_finished_v2)
        worker.error.connect(self._on_visible_tile_window_error_v2)
        self._visible_tile_worker_v2 = worker
        worker.start()

    def _on_visible_tile_window_finished_v2(self, pair_id: str, generation: int, manifest: object) -> None:
        worker = self.sender()
        if isinstance(worker, QThread):
            self._retire_visible_tile_worker_v2(worker)
        if int(generation) != self._visible_tile_generation_v2:
            return
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        if str(pair_id or "") != current_pair or not isinstance(manifest, dict):
            return
        pair = dict(self._viewer_pairs_by_id.get(pair_id, {}))
        pair["tile_manifest"] = str(pair_tile_manifest_path(self._viewer_cache_root_v2() / "tiles", pair_id))
        pair["tile_cache_key"] = str(manifest.get("cache_key") or pair.get("tile_cache_key") or "")
        pair["lod_tile_count"] = int(manifest.get("tile_count") or 0)
        pair["overlay_tile_count"] = int(manifest.get("overlay_tile_count") or 0)
        self._viewer_pairs_by_id[pair_id] = pair
        self._tile_manifest_cache_v2.clear()
        if not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
            self.preview_before_v2.update_tile_manifest(manifest)
            self.preview_after_v2.update_tile_manifest(manifest)
        if self._visible_tile_pending_request_v2:
            self._visible_tile_request_timer_v2.start()

    def _on_visible_tile_window_error_v2(self, pair_id: str, generation: int, message: str) -> None:
        worker = self.sender()
        if isinstance(worker, QThread):
            self._retire_visible_tile_worker_v2(worker)
        if int(generation) == self._visible_tile_generation_v2 and self._viewer_root:
            append_viewer_perf_event(
                self._viewer_root,
                "visible_tile_window_error",
                pair_uuid=str(pair_id or ""),
                generation=int(generation),
                message=str(message or ""),
            )
        if self._visible_tile_pending_request_v2:
            self._visible_tile_request_timer_v2.start()

    def _pair_needs_render_v2(self, pair_id: str, viewer_pair: dict, overlays: list[dict]) -> bool:
        if not pair_id or not viewer_pair:
            return False
        if self._render_status_by_pair.get(pair_id) in {"ready", "gpu_ready", "tile_ready", "pdf_render", "rendering", "failed", "render_timeout"}:
            return False
        if str(viewer_pair.get("render_status") or "") == "render_timeout":
            return False
        has_images = bool(viewer_pair.get("before_image")) and bool(viewer_pair.get("after_image"))
        has_transforms = bool(viewer_pair.get("before_transform")) and bool(viewer_pair.get("after_transform"))
        has_pixel_boxes = any(
            isinstance(overlay, dict) and (overlay.get("before_bbox_px") or overlay.get("after_bbox_px"))
            for overlay in overlays
        )
        if _viewer_pair_is_pdf(viewer_pair):
            return not (has_images and has_transforms and has_pixel_boxes)
        has_tiles = bool(viewer_pair.get("tile_manifest") or viewer_pair.get("lod_tile_count"))
        if not self._selection_build_lod_tiles_enabled_v2():
            has_tiles = True
        return not (has_images and has_transforms and has_pixel_boxes and has_tiles)

    def _preview_status_label_v2(self, row: dict, viewer_pair: dict) -> str:
        """G2.7-COORDFIX — produce a friendly per-pair preview-status line
        for the right-panel summary. Distinguishes:

        - PDF pair (lightweight viewer renders Qt PDF directly)
          → "신형 뷰어로 PDF 직접 표시 중"
          (PDF pairs typically have preview_available=False because the
          PNG path doesn't apply — the lightweight viewer takes over.
          So PDF detection MUST run BEFORE the preview_available check
          or PDFs are mis-reported as failed.)
        - PNG preview rendered (DXF/DWG, after_image populated)
          → "미리보기 가능"
        - DXF/DWG with explicit failure flag
          → "미리보기 실패 - CSV/DXF 링크로 확인"
        - DXF/DWG without PNG yet (still rendering)
          → "미리보기 준비 중 — 신형 뷰어가 위치 표시"

        The previous version checked ``preview_available`` first which
        incorrectly fired for every PDF pair (since PNG-not-rendered is
        normal for PDFs).
        """

        is_pdf = _viewer_pair_is_pdf(viewer_pair) if viewer_pair else False
        if is_pdf:
            # For PDFs, preview_available may be False even on success
            # because the PNG-to-screen path doesn't apply. The
            # lightweight Qt PDF viewer renders the page directly —
            # report that, not failure.
            return "신형 뷰어로 PDF 직접 표시 중"

        # Non-PDF: explicit failure flag wins
        if row.get("preview_available") is False:
            return "미리보기 실패 — CSV/DXF 링크로 확인"

        # DXF/DWG: distinguish rendered vs pending
        has_after_image = bool(viewer_pair.get("after_image")) if viewer_pair else False
        if has_after_image:
            return "미리보기 가능"
        return "미리보기 준비 중 — 신형 뷰어가 변경구역 위치 표시"

    def _viewer_pair_from_row_v2(self, pair_id: str, row: dict) -> dict:
        viewer_pair = dict(self._viewer_pairs_by_id.get(pair_id, {}))
        if viewer_pair:
            viewer_pair = self._repair_viewer_pair_source_paths_v2(pair_id, viewer_pair, row)
            self._viewer_pairs_by_id[pair_id] = viewer_pair
            return viewer_pair
        issue = (row.get("top_issues") or [{}])[0] if isinstance(row.get("top_issues"), list) else {}
        source_a = issue.get("source_a") or row.get("source_a") or ""
        source_b = issue.get("source_b") or row.get("source_b") or ""
        viewer_pair = {
            "pair_id": pair_id,
            "source_a": source_a,
            "source_b": source_b,
            "render_status": "render_pending",
        }
        return self._repair_viewer_pair_source_paths_v2(pair_id, viewer_pair, row)

    def _repair_viewer_pair_source_paths_v2(self, pair_id: str, viewer_pair: dict, row: dict) -> dict:
        """Restore local source paths when sharable manifests redact them.

        The viewer package redacts ``source_a``/``source_b`` for sharable
        artifacts.  That is correct for exported files, but the live Workbench
        still needs real local paths when selected-zone crop rendering falls
        back from overview PNG cropping to source DXF/DWG rendering.
        """

        repaired = dict(viewer_pair or {})
        for key in ("source_a", "source_b"):
            if self._is_usable_zone_render_source_v2(repaired.get(key)):
                continue
            replacement = self._source_path_replacement_v2(pair_id, row, key)
            if replacement:
                repaired[key] = replacement
        return repaired

    def _source_path_replacement_v2(self, pair_id: str, row: dict, key: str) -> str:
        # Single-file runs can safely derive registered DXF fallbacks from
        # the current GUI inputs before consulting redacted package metadata.
        if len(self._viewer_pairs_by_id) <= 1:
            value = self._source_a if key == "source_a" else self._source_b
            side = "before" if key == "source_a" else "after"
            for candidate in (registered_dxf_fallback_for_source(value, side), value):
                if self._is_usable_zone_render_source_v2(candidate):
                    return str(candidate)

        issue = (row.get("top_issues") or [{}])[0] if isinstance(row.get("top_issues"), list) else {}
        for value in (issue.get(key), row.get(key)):
            if self._is_usable_zone_render_source_v2(value):
                return str(value)

        result = getattr(self, "_result", None)
        summary = getattr(result, "summary", None) or getattr(result, "compare_summary", None)
        try:
            from src.services.comparison.pair_identity import candidate_pair_uuid

            for item in getattr(summary, "items", []) or []:
                candidate = getattr(item, "candidate", None)
                if not candidate or candidate_pair_uuid(candidate) != pair_id:
                    continue
                descriptor = getattr(candidate, key, None)
                value = getattr(descriptor, "path", "")
                if self._is_usable_zone_render_source_v2(value):
                    return str(value)
        except Exception:
            logger.debug("Could not restore source path from comparison summary", exc_info=True)

        return ""

    @staticmethod
    def _is_usable_zone_render_source_v2(value: Any) -> bool:
        text = str(value or "").strip()
        if not text or _is_redacted_artifact_path(text) or has_lossy_path_text(text):
            return False
        try:
            path = Path(text)
            return path.is_file() and path.suffix.lower() in SUPPORTED_DRAWING_EXTENSIONS
        except (OSError, ValueError, RuntimeError):
            return False

    def _start_pair_render_v2(self, pair_id: str, viewer_pair: dict, row: dict) -> None:
        if not self._viewer_root:
            return
        if self._render_worker and self._render_worker.isRunning():
            self._pending_render_request_v2 = (pair_id, dict(viewer_pair or {}), dict(row or {}))
            self._set_preview_status_v2(pair_id, "rendering", "현재 도면 렌더가 끝나면 이어서 준비합니다.")
            return
        viewer_pair = self._viewer_pair_from_row_v2(pair_id, row) if not viewer_pair else viewer_pair
        if not viewer_pair.get("source_a") or not viewer_pair.get("source_b"):
            self._set_preview_status_v2(pair_id, "failed", "원본 도면 경로를 찾을 수 없어 미리보기를 만들 수 없습니다.")
            return
        self._set_preview_status_v2(pair_id, "rendering", "렌더 중 - 변경구역 위치를 준비하고 있습니다.")
        if not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
            overlays = self._viewer_overlays_for_pair_v2(pair_id)
            self.preview_before_v2.load_preview(
                "",
                overlays,
                before=True,
                fallback_message="렌더 중 - 변경구역 위치를 준비하고 있습니다.",
            )
            self.preview_after_v2.load_preview(
                "",
                overlays,
                before=False,
                fallback_message="렌더 중 - 변경구역 위치를 준비하고 있습니다.",
            )
        self._render_worker = PairPreviewRenderWorker(
            pair_id=pair_id,
            viewer_pair=viewer_pair,
            dxf_cache_dir=self._dxf_cache_dir,
            viewer_root=self._viewer_root,
            viewer_cache_root=Path(str(self._viewer_manifest.get("viewer_cache_dir") or self._viewer_root)),
            build_lod_tiles=self._selection_build_lod_tiles_enabled_v2(),
        )
        self._render_worker.finished.connect(self._on_pair_render_finished_v2)
        self._render_worker.error.connect(self._on_pair_render_error_v2)
        self._render_worker.start()

    def _on_pair_render_finished_v2(self, pair_id: str, viewer_pair: dict, overlays: list[dict]) -> None:
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        existing_pair = self._viewer_pairs_by_id.get(pair_id, {})
        if isinstance(existing_pair, dict):
            merged_pair = dict(existing_pair)
            merged_pair.update(viewer_pair or {})
        else:
            merged_pair = dict(viewer_pair or {})
        if current_pair == pair_id and _viewer_pair_is_pdf(merged_pair):
            if not merged_pair.get("page_match_pairs") and isinstance(existing_pair, dict):
                merged_pair["page_match_pairs"] = list(existing_pair.get("page_match_pairs") or [])
            self._apply_active_pdf_page_pair_to_viewer_pair_v2(merged_pair)
        viewer_pair = merged_pair
        self._viewer_pairs_by_id[pair_id] = viewer_pair
        overlay_scope = str(viewer_pair.get("_overlay_materialization_scope") or "")
        if overlay_scope != "visible_pdf_page":
            self._cache_viewer_overlays_v2(pair_id, overlays)
        render_status = str(viewer_pair.get("render_status") or "")
        render_warning = str(viewer_pair.get("render_warning") or "")
        if render_status == "rendered":
            status = "tile_ready" if int(viewer_pair.get("lod_tile_count") or 0) > 0 else "ready"
            message = "선택 도면의 실제 미리보기와 타일 캐시를 준비했습니다."
            tile_manifest = self._tile_manifest_for_pair_v2(pair_id, viewer_pair) if int(viewer_pair.get("lod_tile_count") or 0) > 0 else {}
            if tile_manifest and tile_manifest.get("pyramid_complete") is False:
                message = "선택 도면의 실제 미리보기와 현재 화면 주변 타일을 우선 준비했습니다."
            if _viewer_pair_is_pdf(viewer_pair):
                status = "pdf_render"
                message = "PDF 시각 배경을 준비했습니다."
        elif render_status == "render_timeout":
            status = "render_timeout"
            message = "원본 도면 렌더링 시간이 초과되어 실제 도면 배경 없이 상대 위치 오버레이로 표시합니다."
        else:
            status = "failed"
            message = render_warning or "원본 도면 미리보기 렌더에 실패해 실제 도면 배경 없이 상대 위치 오버레이로 표시합니다."
        self._set_preview_status_v2(pair_id, status, message)
        self._update_viewer_manifest_pair_v2(pair_id, viewer_pair)
        if current_pair == pair_id:
            self._active_all_overlays_by_zone = {
                str(overlay.get("zone_id") or ""): overlay
                for overlay in overlays
                if isinstance(overlay, dict) and overlay.get("zone_id")
            }
            visible_overlays = self._visible_overlays_for_pdf_page_v2(
                pair_id,
                viewer_pair,
                overlays,
                load_when_empty=False,
            )
            tile_manifest = (
                self._tile_manifest_for_pair_v2(pair_id, viewer_pair)
                if self._selection_build_lod_tiles_enabled_v2()
                else {}
            )
            self._active_overlays_by_zone = {
                str(overlay.get("zone_id") or ""): overlay
                for overlay in visible_overlays
                if isinstance(overlay, dict) and overlay.get("zone_id")
            }
            if not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
                self.preview_before_v2.load_preview(
                    str(viewer_pair.get("before_image") or ""),
                    visible_overlays,
                    before=True,
                    tile_manifest=tile_manifest,
                    viewer_root=self._viewer_root,
                    pair_id=pair_id,
                )
                self.preview_after_v2.load_preview(
                    str(viewer_pair.get("after_image") or ""),
                    visible_overlays,
                    before=False,
                    tile_manifest=tile_manifest,
                    viewer_root=self._viewer_root,
                    pair_id=pair_id,
                )
            active_zone = self._active_zone_id
            self._populate_zone_list_v2(self._preview_by_pair.get(pair_id), visible_overlays, prefer_overlays=True)
            if active_zone:
                self._select_zone_in_list_v2(active_zone)
        self._retire_render_worker_v2()
        self._start_pending_zone_render_v2()
        self._start_pending_render_v2()

    def _on_pair_render_error_v2(self, pair_id: str, message: str) -> None:
        self._set_preview_status_v2(pair_id, "failed", message or "미리보기 렌더에 실패했습니다.")
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        if current_pair == pair_id:
            if not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
                overlays = self._viewer_overlays_for_pair_v2(pair_id)
                self.preview_before_v2.load_preview(
                    "",
                    overlays,
                    before=True,
                    fallback_message="미리보기 실패 - 상대위치 표시로 변경구역을 확인합니다.",
                )
                self.preview_after_v2.load_preview(
                    "",
                    overlays,
                    before=False,
                    fallback_message="미리보기 실패 - 상대위치 표시로 변경구역을 확인합니다.",
                )
        self._retire_render_worker_v2()
        self._start_pending_render_v2()

    def _start_pending_render_v2(self) -> None:
        pending = self._pending_render_request_v2
        self._pending_render_request_v2 = None
        if not pending:
            return
        pair_id, viewer_pair, row = pending
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        if pair_id != current_pair:
            return
        self._start_pair_render_v2(pair_id, viewer_pair, row)

    def _apply_pdf_page_pin_fallback_v2(
        self, pair_id: str, zone_id: str, viewer_pair: dict
    ) -> bool:
        """Render a focus pin at the PDF page center when the zone has no bbox.

        Returns ``True`` when the fallback was applied so the caller can stop further
        crop-render handling. Returns ``False`` when ``pdf_page_size`` is missing —
        in that case the caller should keep the relative-only behavior.
        """

        page_size = viewer_pair.get("pdf_page_size") if isinstance(viewer_pair, dict) else None
        base_overlay = self._active_overlays_by_zone.get(zone_id, {})
        synthesized = compute_pdf_page_pin_overlay(base_overlay, page_size or {})
        if not synthesized:
            return False
        self._active_overlays_by_zone[zone_id] = synthesized
        if hasattr(self, "preview_before_v2"):
            self.preview_before_v2.update_overlay_metadata(zone_id, synthesized)
            self.preview_before_v2.set_selected_zone(zone_id)
        if hasattr(self, "preview_after_v2"):
            self.preview_after_v2.update_overlay_metadata(zone_id, synthesized)
            self.preview_after_v2.set_selected_zone(zone_id)
        self._set_preview_status_v2(
            pair_id,
            "pdf_render",
            "PDF 페이지 중앙 핀 - 정확 좌표 없음. 원본 PDF에서 위치를 확인하세요.",
        )
        return True

    def _viewer_cache_root_v2(self) -> Path:
        value = str(self._viewer_manifest.get("viewer_cache_dir") or "").strip()
        if value and not _is_redacted_artifact_path(value):
            path = _resolve_viewer_artifact_path(value, self._viewer_root)
            if path:
                return path
        if self._viewer_root:
            return self._viewer_root
        return _workbench_data_dir() / "viewer_cache"

    def _start_zone_crop_render_v2(
        self, zone_id: str, *, prefer_source_render: bool = False
    ) -> None:
        # prefer_source_render=True is the ② full-detail (text/dims/blocks)
        # deferred upgrade: render the zone window from the source via the ezdxf
        # Frontend instead of cropping the simplified fast raster. Issued by
        # _maybe_start_zone_full_detail_v2 AFTER the fast crop is already shown,
        # only when the controller is free, so it never disturbs the fast paint.
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        if not pair_id or not zone_id:
            return
        overlay = self._active_overlays_by_zone.get(zone_id, {})
        if not overlay:
            return
        viewer_pair = self._viewer_pair_from_row_v2(pair_id, self._active_row or {})
        if _viewer_pair_is_pdf(viewer_pair):
            overlay = self._backfill_pdf_overlay_coord_space_v2(pair_id, [overlay])[0]
            self._active_overlays_by_zone[zone_id] = overlay
        bbox = union_bboxes(overlay.get("old_bbox"), overlay.get("bbox"))
        if not bbox:
            viewer_pair_for_fallback = self._viewer_pair_from_row_v2(pair_id, self._active_row or {})
            if _viewer_pair_is_pdf(viewer_pair_for_fallback) and self._apply_pdf_page_pin_fallback_v2(
                pair_id, zone_id, viewer_pair_for_fallback
            ):
                self._record_zone_render_perf_event_v2(
                    "zone_render_fallback",
                    pair_id,
                    zone_id,
                    reason_code="pdf_page_pin_fallback",
                    visual_fidelity="relative_overlay",
                )
                return
            self._record_zone_render_perf_event_v2(
                "zone_render_fallback",
                pair_id,
                zone_id,
                reason_code="missing_cad_bbox",
                visual_fidelity="relative_overlay",
            )
            self._set_preview_status_v2(
                pair_id,
                "relative_only",
                "선택 변경구역에 CAD bbox가 없어 상대 위치 표시만 유지합니다.",
            )
            return
        request_id = self._active_zone_render_request_id_v2(pair_id, zone_id)
        if not request_id:
            request_id = self._begin_selected_zone_render_request_v2(pair_id, zone_id)
        if self._zone_render_controller_v2.is_busy():
            previous = self._pending_zone_render_request_v2
            if previous and previous[:2] != (pair_id, zone_id):
                self._record_zone_render_perf_event_v2(
                    "zone_render_pending_replaced",
                    str(previous[0]),
                    str(previous[1]),
                    replacement_pair_uuid=pair_id,
                    replacement_zone_id=zone_id,
                )
            self._pending_zone_render_request_v2 = (pair_id, zone_id, request_id)
            self._set_preview_status_v2(pair_id, "rendering", "현재 구역 렌더가 끝나면 이어서 준비합니다.")
            return
        if not viewer_pair.get("source_a") or not viewer_pair.get("source_b"):
            self._record_zone_render_perf_event_v2(
                "zone_render_fallback",
                pair_id,
                zone_id,
                reason_code="missing_source_path",
                visual_fidelity="relative_overlay",
            )
            self._set_preview_status_v2(pair_id, "relative_only", "원본 도면 경로를 찾을 수 없어 상대 위치만 표시합니다.")
            return
        if not prefer_source_render:
            # Deferred full-detail upgrade keeps the fast crop on screen (no
            # "rendering" reset) and replaces it silently when it completes.
            self._set_preview_status_v2(pair_id, "rendering", "선택 변경구역 실도면 crop을 렌더 중입니다.")
        if _viewer_pair_is_pdf(viewer_pair):
            has_pdf_background = bool(viewer_pair.get("before_image")) and bool(viewer_pair.get("after_image"))
            has_pdf_transform = bool(viewer_pair.get("before_transform")) and bool(viewer_pair.get("after_transform"))
            if not (has_pdf_background and has_pdf_transform):
                previous = self._pending_zone_render_request_v2
                if previous and previous[:2] != (pair_id, zone_id):
                    self._record_zone_render_perf_event_v2(
                        "zone_render_pending_replaced",
                        str(previous[0]),
                        str(previous[1]),
                        replacement_pair_uuid=pair_id,
                        replacement_zone_id=zone_id,
                    )
                self._pending_zone_render_request_v2 = (pair_id, zone_id, request_id)
                if self._render_status_by_pair.get(pair_id) != "rendering":
                    self._start_pair_render_v2(pair_id, viewer_pair, self._active_row or {})
                else:
                    self._set_preview_status_v2(pair_id, "rendering", "PDF 배경 생성 중 - 선택 구역 렌더를 대기합니다.")
                return
        render_environment_hash = render_environment_signature(dxf_cache_dir=self._dxf_cache_dir)
        before_background = _resolve_viewer_artifact_path(viewer_pair.get("before_image"), self._viewer_root)
        after_background = _resolve_viewer_artifact_path(viewer_pair.get("after_image"), self._viewer_root)
        render_bbox = (
            scale_pdf_bbox_to_render_pixels(bbox, overlay, viewer_pair)
            if _viewer_pair_is_pdf(viewer_pair)
            else bbox
        )
        if not render_bbox:
            render_bbox = bbox
        before_render_bbox = _bbox_for_zone_crop_transform(overlay, viewer_pair, before=True) or render_bbox
        after_render_bbox = _bbox_for_zone_crop_transform(overlay, viewer_pair, before=False) or render_bbox
        request = {
            "request_id": request_id,
            "pair_uuid": pair_id,
            "zone_id": zone_id,
            "source_before": str(viewer_pair.get("source_a") or ""),
            "source_after": str(viewer_pair.get("source_b") or ""),
            "world_window": canonical_window_from_bbox(render_bbox, padding_ratio=0.18, min_size=250.0).to_dict(),
            "before_world_window": canonical_window_from_bbox(before_render_bbox, padding_ratio=0.18, min_size=250.0).to_dict(),
            "after_world_window": canonical_window_from_bbox(after_render_bbox, padding_ratio=0.18, min_size=250.0).to_dict(),
            "cache_root": str(self._viewer_cache_root_v2()),
            "dxf_cache_dir": str(self._dxf_cache_dir),
            "renderer_backend": "ezdxf-matplotlib-zone",
            "render_environment_hash": render_environment_hash,
            "font_manifest_hash": render_environment_hash,
            "before_background_image": str(before_background or ""),
            "after_background_image": str(after_background or ""),
            "before_background_transform": viewer_pair.get("before_transform") or {},
            "after_background_transform": viewer_pair.get("after_transform") or {},
            "prefer_source_render": bool(prefer_source_render),
        }
        process_key = render_environment_hash
        started = self._zone_render_controller_v2.render(
            process_key=process_key,
            request=request,
            viewer_pair=viewer_pair,
            overlay=overlay,
            overlays=list(self._active_overlays_by_zone.values()),
        )
        if not started:
            self._record_zone_render_perf_event_v2(
                "zone_render_pending_deferred",
                pair_id,
                zone_id,
                reason_code="controller_busy_or_unavailable",
            )
            self._pending_zone_render_request_v2 = (pair_id, zone_id, request_id)

    def _start_selected_zone_deferred_enhancement_v2(
        self, pair_id: str, zone_id: str, request_id: str = "",
    ) -> None:
        if not self._is_current_zone_render_request_v2(pair_id, zone_id, request_id):
            self._record_zone_render_perf_event_v2(
                "zone_render_stale",
                pair_id,
                zone_id,
                reason_code="deferred_enhancement_stale",
                request_id=request_id,
                active_request_id=self._active_zone_render_request_id_v2(pair_id, zone_id),
            )
            return
        try:
            self._request_zone_focus_v2(zone_id)
        except Exception:
            logger.exception("Deferred lightweight zone focus failed for %s", zone_id)
        viewer_pair = self._viewer_pair_from_row_v2(pair_id, self._active_row or {})
        if not _viewer_pair_is_pdf(viewer_pair):
            self._apply_or_start_zone_vector_render_v2(pair_id, zone_id)
        self._refresh_zone_vector_button_state_v2()

    def _maybe_start_zone_full_detail_v2(
        self, pair_id: str, zone_id: str, request_id: str
    ) -> None:
        """② Re-render the on-screen fast crop from source for full detail.

        Issued on a timer after a cad-background fast crop is shown. Re-renders
        the same zone window via the ezdxf Frontend (prefer_source_render=True)
        so text/dims/blocks/hatch appear, then swaps it in via the normal render
        result path. Bails out (leaving the perfectly good fast crop in place) if
        the selection moved on, the controller is busy, a different render is
        queued, or this exact request was already upgraded — so the upgrade
        never loops and never disturbs the fast paint.
        """
        if not self._is_lightweight_viewer_active_v2():
            return
        if not self._is_current_zone_render_request_v2(pair_id, zone_id, request_id):
            return
        if self._zone_render_controller_v2.is_busy() or self._pending_zone_render_request_v2:
            return
        viewer_pair = self._viewer_pair_from_row_v2(pair_id, self._active_row or {})
        if any(
            _is_redacted_artifact_path(viewer_pair.get(k)) or has_lossy_path_text(viewer_pair.get(k))
            for k in ("source_a", "source_b")
        ):
            return
        key = (pair_id, zone_id, request_id)
        if self._zone_full_detail_started_request_v2 == key:
            return
        self._zone_full_detail_started_request_v2 = key
        self._record_zone_render_perf_event_v2(
            "zone_full_detail_upgrade",
            pair_id,
            zone_id,
            request_id=request_id,
        )
        self._start_zone_crop_render_v2(zone_id, prefer_source_render=True)

    def _on_zone_crop_render_finished_v2(
        self,
        pair_id: str,
        zone_id: str,
        result_payload: dict,
        viewer_pair: dict,
        local_overlays: list[dict],
    ) -> None:
        if pair_id not in self._viewer_pairs_by_id:
            self._record_zone_render_perf_event_v2(
                "zone_render_stale",
                pair_id,
                zone_id,
                reason_code="inactive_pair_result",
            )
            logger.debug(
                "Ignoring stale zone crop render result for inactive pair=%s zone=%s",
                pair_id,
                zone_id,
            )
            return
        result_request_id = str(result_payload.get("request_id") or "")
        if not self._is_current_zone_render_request_v2(pair_id, zone_id, result_request_id):
            self._record_zone_render_perf_event_v2(
                "zone_render_stale",
                pair_id,
                zone_id,
                reason_code="superseded_request",
                request_id=result_request_id,
                active_request_id=self._active_zone_render_request_id_v2(pair_id, zone_id),
            )
            logger.debug(
                "Ignoring stale zone crop render result for superseded request pair=%s zone=%s request=%s",
                pair_id,
                zone_id,
                result_request_id,
            )
            self._start_pending_zone_render_v2()
            return
        self._viewer_pairs_by_id[pair_id] = viewer_pair
        if self._viewer_root:
            try:
                elapsed_ms = float(result_payload.get("elapsed_ms") or 0.0)
            except (TypeError, ValueError):
                elapsed_ms = 0.0
            pdf_cache_metrics = {}
            for key in (
                "pdf_display_list_render_count",
                "pdf_display_list_cache_lookup_count",
                "pdf_display_list_cache_hit_count",
                "pdf_display_list_cache_miss_count",
                "pdf_display_list_cache_hit_rate",
                "pdf_display_list_cache_eviction_count",
                "pdf_display_list_cache_evicted_estimated_bytes",
                "pdf_display_list_cache_total_estimated_bytes",
                "pdf_display_list_cache_byte_limit",
                "pdf_display_list_cache_entry_estimated_bytes_max",
                "pdf_display_list_worker_rss_mb",
                "pdf_pil_fallback_count",
            ):
                if key in result_payload:
                    pdf_cache_metrics[key] = result_payload.get(key)
            nested_pdf_cache = result_payload.get("pdf_display_list_cache")
            if isinstance(nested_pdf_cache, dict):
                pdf_cache_metrics["pdf_display_list_cache"] = nested_pdf_cache
            dxf_index_metrics = {}
            for key in (
                "dxf_index_cache_entries",
                "dxf_index_cache_capacity_entries",
                "dxf_index_cache_byte_limit",
                "dxf_index_cache_entry_estimated_bytes_max",
                "dxf_index_cache_total_estimated_bytes",
                "dxf_index_cache_lookup_count",
                "dxf_index_cache_hit_count",
                "dxf_index_cache_miss_count",
                "dxf_index_cache_hit_rate",
                "dxf_index_cache_eviction_count",
                "dxf_index_cache_evicted_estimated_bytes",
                "dxf_index_cache_last_eviction_reason",
                "dxf_index_cache_worker_rss_mb",
            ):
                if key in result_payload:
                    dxf_index_metrics[key] = result_payload.get(key)
            nested_dxf_index_cache = result_payload.get("dxf_index_cache")
            if isinstance(nested_dxf_index_cache, dict):
                dxf_index_metrics["dxf_index_cache"] = nested_dxf_index_cache
            warning_payload = result_payload.get("warnings")
            if isinstance(warning_payload, list):
                warning_items = list(warning_payload)
            elif warning_payload:
                warning_items = [str(warning_payload)]
            else:
                warning_items = []
            is_failed_full_detail_upgrade = bool(result_payload.get("prefer_source_render")) and (
                str(result_payload.get("render_lifecycle") or "").lower() != "ready"
                or str(result_payload.get("visual_fidelity") or "").lower() != "cad_render"
            )
            append_viewer_perf_event(
                self._viewer_root,
                "zone_full_detail_upgrade_failed" if is_failed_full_detail_upgrade else "zone_crop_render",
                pair_uuid=pair_id,
                zone_id=zone_id,
                render_ms=elapsed_ms,
                cache_hit=bool(result_payload.get("cache_hit")),
                render_lifecycle=str(result_payload.get("render_lifecycle") or ""),
                visual_fidelity=str(result_payload.get("visual_fidelity") or ""),
                reason_code=str(result_payload.get("reason_code") or ""),
                renderer_backend=str(result_payload.get("renderer_backend") or ""),
                warnings=warning_items,
                **pdf_cache_metrics,
                **dxf_index_metrics,
            )
            self._refresh_viewer_perf_summary_only()
            if is_failed_full_detail_upgrade:
                self._start_pending_zone_render_v2()
                return
        for overlay in local_overlays:
            key = str(overlay.get("zone_id") or "")
            if key:
                self._active_overlays_by_zone[key] = overlay
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        current_zone = str(self._active_zone_id or "")
        if current_pair == pair_id and current_zone == zone_id:
            before_image = str(result_payload.get("before_image") or "")
            after_image = str(result_payload.get("after_image") or "")
            lifecycle = str(result_payload.get("render_lifecycle") or "ready")
            reason_code = str(result_payload.get("reason_code") or "")
            message = "실도면 렌더 - 선택 구역 주변만 빠르게 표시합니다."
            status = "ready"
            if str(result_payload.get("visual_fidelity") or "") == "pdf_render":
                status = "pdf_render"
                message = "PDF 시각 배경 - 선택 구역 주변만 빠르게 표시합니다."
            if lifecycle == "skipped_missing_page_bbox" or reason_code == "missing_page_bbox":
                status = "relative_only"
                message = self._zone_render_reason_message_ko("missing_page_bbox")
            elif lifecycle == "fallback_visible":
                status = "relative_only"
                message = self._zone_render_reason_message_ko(reason_code)
            elif result_payload.get("cache_hit"):
                message = "실도면 렌더 - 캐시된 선택 구역 crop을 표시합니다."
            self._set_preview_status_v2(pair_id, status, message)
            if not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
                self.preview_before_v2.load_preview(
                    before_image,
                    local_overlays,
                    before=True,
                    fallback_message="렌더 실패 시 상대 위치로 표시합니다.",
                    viewer_root=self._viewer_root,
                    pair_id=pair_id,
                )
                self.preview_after_v2.load_preview(
                    after_image,
                    local_overlays,
                    before=False,
                    fallback_message="렌더 실패 시 상대 위치로 표시합니다.",
                    viewer_root=self._viewer_root,
                    pair_id=pair_id,
                )
                self.preview_before_v2.set_selected_zone(zone_id)
                self.preview_after_v2.set_selected_zone(zone_id)
                self.preview_before_v2.focus_zone(zone_id, padding_ratio=0.25)
                self.preview_after_v2.focus_zone(zone_id, padding_ratio=0.25)
            # Lightweight viewer is the active surface on QtQuick machines. The
            # legacy block above is skipped in lightweight-only mode, so without
            # this the crisp zone crop renders but is never shown — DWG/CAD zones
            # stayed pixel-mush (full-drawing raster magnified) or off-frame.
            # Surface the crop here, framed by its world_window.
            if self._is_lightweight_viewer_active_v2():
                self._apply_zone_crop_to_lightweight_v2(
                    pair_id, zone_id, result_payload, status
                )
            self.zone_detail_v2.setHtml(self._zone_detail_text_v2(zone_id))
            self._start_selected_zone_deferred_enhancement_v2(pair_id, zone_id, result_request_id)
            # ② full-detail upgrade. The fast crop just shown is the simplified
            # whole-drawing raster (LINE/ARC only — no text/dims/blocks/hatch).
            # Now that it is on screen, schedule a silent re-render of the same
            # zone window from source via the ezdxf Frontend, which swaps the
            # full-detail crop in when ready. Only for the cad-background fast
            # crop (PDF/relative fallbacks already carry their own fidelity), and
            # only via a timer so the fast paint is never delayed.
            if (
                self._is_lightweight_viewer_active_v2()
                and status == "ready"
                and str(result_payload.get("renderer_backend") or "")
                == "cad-background-image-crop"
            ):
                QTimer.singleShot(
                    GUI_ZONE_FULL_DETAIL_UPGRADE_DELAY_MS,
                    lambda p=pair_id, z=zone_id, r=result_request_id: self._maybe_start_zone_full_detail_v2(
                        p, z, r
                    ),
                )
        self._start_pending_zone_render_v2()

    def _apply_zone_crop_to_lightweight_v2(
        self,
        pair_id: str,
        zone_id: str,
        result_payload: dict,
        status: str,
    ) -> None:
        """Surface a finished zone-crop render in the lightweight viewer.

        The crop worker produces a per-zone PNG (``before_image`` /
        ``after_image``) framed by a CAD-world window (``before_transform`` /
        ``after_transform``, ``min_x/min_y/max_x/max_y`` keys). Because the full
        drawing background is rendered at high resolution (~8000 px), the crop of
        a selected zone is effectively 1:1 — far crisper than the lightweight
        viewer magnifying the whole-drawing raster (which the fallback renderer
        downsamples, so zoom looks like pixel-mush). In lightweight-only mode the
        legacy block in ``_on_zone_crop_render_finished_v2`` is skipped, so
        without this the crisp crop rendered but was never shown.

        Per-side blank guard: when the before/after sources are in disjoint
        world-coordinate ranges (observed on a revised DWG re-originated to a
        different datum), the zone window falls OUTSIDE one side's background and
        the worker writes a blank white crop for it. We detect that (the zone
        ``world_window`` does not overlap that side's full-background bbox) and
        degrade that side honestly to ``relative_only`` instead of painting a
        white panel — the change still shows crisp on the in-bounds side.

        Only ``ready`` (real CAD) crops are surfaced here: PDF crops
        (``status == "pdf_render"``) keep their own working full-page +
        re-render-on-zoom path (their crop transform is in ``image_pixels`` while
        PDF overlays are in points — framing the raster by it would misalign the
        markers). On a non-``ready`` outcome, if a prior crop from THIS pair is
        on screen we restore the full-drawing raster so a relative-only zone is
        not left sitting on a stale neighbouring crop (honest background).
        """

        before_vp = self.preview_before_lightweight_v2
        after_vp = self.preview_after_lightweight_v2
        if before_vp is None or after_vp is None:
            return
        if status != "ready":
            # Crop fell back (or this is a PDF pair, handled by its own path).
            # Only act if a crisp crop from this pair is currently displayed —
            # that crop frames a different zone, so leaving it under a
            # relative-only focus would show the wrong region (silent
            # misinformation). Restore the full-drawing raster instead.
            if getattr(self, "_lightweight_zone_crop_pair_v2", "") == pair_id:
                self._lightweight_zone_crop_pair_v2 = ""
                viewer_pair = self._viewer_pairs_by_id.get(pair_id)
                if isinstance(viewer_pair, dict) and not _viewer_pair_is_pdf(viewer_pair):
                    self._load_lightweight_raster_preview_v2(pair_id, viewer_pair)
                    try:
                        self._focus_lightweight_on_zone_v2(zone_id)
                    except Exception:
                        logger.exception(
                            "Lightweight full-raster restore focus failed for %s", zone_id
                        )
            return

        viewer_pair = self._viewer_pairs_by_id.get(pair_id) or {}
        # Zone window (CAD world) — used to detect a side whose crop is blank
        # because the window lies outside that side's full-drawing background.
        ww = result_payload.get("world_window") or {}
        try:
            zone_window = (
                float(ww["xmin"]), float(ww["ymin"]),
                float(ww["xmax"]), float(ww["ymax"]),
            )
        except (KeyError, TypeError, ValueError):
            zone_window = None

        def _overlaps(a, b) -> bool:
            return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

        before_crop_bbox = self._transform_world_bbox_v2(
            result_payload.get("before_transform")
        )
        after_crop_bbox = self._transform_world_bbox_v2(
            result_payload.get("after_transform")
        )
        shared_crop_bbox = zone_align.union_bboxes(
            before_crop_bbox,
            after_crop_bbox,
            zone_window,
        )

        specs = (
            ("before", before_vp, result_payload.get("before_image"),
             before_crop_bbox, viewer_pair.get("before_transform")),
            ("after", after_vp, result_payload.get("after_image"),
             after_crop_bbox, viewer_pair.get("after_transform")),
        )
        loaded_before = False
        loaded_after = False
        loaded_frames: list[tuple] = []  # (viewport, crop world_bbox) for camera fit
        for side, viewport, image_value, world_bbox, bg_transform in specs:
            bg_bbox = self._transform_world_bbox_v2(bg_transform)
            if zone_window and bg_bbox and not _overlaps(zone_window, bg_bbox):
                # Blank crop on this side (zone window outside its background).
                empty_bbox = shared_crop_bbox or zone_window
                shown_empty = zone_align.show_empty_side_frame(viewport, empty_bbox)
                try:
                    viewport.set_fidelity_state(
                        "relative_only",
                        status_text=zone_align.EMPTY_SIDE_NOTICE,
                    )
                except Exception:
                    logger.debug("zone crop blank-side fidelity failed", exc_info=True)
                if shown_empty:
                    loaded_frames.append((viewport, empty_bbox))
                    if side == "before":
                        loaded_before = True
                    else:
                        loaded_after = True
                continue
            image_path = _resolve_viewer_artifact_path(image_value, self._viewer_root)
            try:
                loaded = viewport.load_raster_image(
                    image_path,
                    world_bbox=world_bbox,
                    empty_notice="선택 구역 crop을 불러오지 못했습니다.",
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[zone crop lightweight] %s-side load failed pair=%s zone=%s image=%r",
                    side, pair_id, zone_id, image_value,
                )
                loaded = False
            try:
                viewport.set_fidelity_state(
                    "raster_refined" if loaded else "relative_only",
                    status_text=(
                        "선택 구역 실도면 crop"
                        if loaded
                        else "이 면은 crop을 불러오지 못했습니다."
                    ),
                )
            except Exception:
                logger.debug("zone crop fidelity state failed", exc_info=True)
            if loaded and world_bbox is not None:
                loaded_frames.append((viewport, world_bbox))
            if side == "before":
                loaded_before = bool(loaded)
            else:
                loaded_after = bool(loaded)
        if not (loaded_before or loaded_after):
            # Neither side resolved — keep the existing relative-only background.
            return
        self._lightweight_raster_pairs.add(pair_id)
        self._lightweight_zone_crop_pair_v2 = pair_id
        self._push_overlays_to_lightweight_v2(pair_id, focus_zone_id=zone_id)
        # Frame the camera on the loaded crop WINDOW (not the tiny change bbox).
        # The crop window is already zone-centred (canonical_window_from_bbox), so
        # fitting it shows the change in context AND is guaranteed visible. Using
        # the change-bbox focus here instead left the freshly-loaded crop off-frame
        # (the after pane rendered blank/white in live DWG runs) — load_raster_image
        # had just swapped the world frame under a camera fit to the old frame.
        self._lightweight_camera_sync_in_progress = True
        try:
            for viewport, world_bbox in loaded_frames:
                try:
                    # Use the QML-native fitToView (the "전체 보기" button's path):
                    # it computes the fit IN QML from the live root size + the
                    # crop's worldBbox (set by load_raster_image). The Python-side
                    # set_camera_to_world_bbox read root width/height right after
                    # the world-frame swap and got a stale value live, so the crop
                    # rendered zoomed-out/tiny in the corner. fitToView reads the
                    # size at QML-execution time and frames the crop reliably.
                    zone_align.sync_crop_camera(
                        viewport,
                        shared_bbox=shared_crop_bbox,
                        loaded_frame_count=len(loaded_frames),
                    )
                    zone_align.maybe_log_camera_state(self, viewport, zone_id)
                except Exception:
                    logger.exception("zone crop fit_to_view failed for %s", zone_id)
        finally:
            self._lightweight_camera_sync_in_progress = False
        logger.info(
            "[zone crop lightweight] pair=%s zone=%s before=%s after=%s",
            pair_id, zone_id, loaded_before, loaded_after,
        )

    @staticmethod
    def _log_zone_crop_camera_state_v2(viewport, zone_id: str) -> None:
        """Diagnostic: log the post-fit camera/world state of a lightweight
        viewport. Lets a live run reveal WHY a crop renders zoomed-out/tiny
        (e.g. root width 0, huge unitsPerPixel) without an interactive session.
        Best-effort; never raises."""

        try:
            root = viewport._quick.rootObject()
            if root is None:
                return

            def _p(name):
                try:
                    return root.property(name)
                except Exception:
                    return None

            logger.info(
                "[zone crop fit] %s zone=%s root=%sx%s cam=(%s,%s) upp=%s worldBbox=%s",
                getattr(viewport, "_side", "?"), zone_id,
                _p("width"), _p("height"),
                _p("cameraCenterX"), _p("cameraCenterY"),
                _p("unitsPerPixel"), _p("worldBbox"),
            )
        except Exception:
            logger.debug("zone crop camera-state log failed", exc_info=True)

    @staticmethod
    def _zone_render_reason_message_ko(reason_code: str) -> str:
        messages = {
            "missing_page_bbox": "PDF 위치 좌표가 없어 실제 crop 대신 상대 위치 캔버스로 표시합니다.",
            "source_render_failed": "선택 구역 렌더가 실패해 실제 도면 배경 대신 상대 위치 캔버스로 표시합니다.",
            "outside_background_bounds": "선택 구역이 렌더된 배경 범위 밖이라 상대 위치 캔버스로 표시합니다.",
            "outside_output_bounds": "선택 구역을 출력 이미지 좌표로 매핑하지 못해 상대 위치 캔버스로 표시합니다.",
        }
        return messages.get(
            str(reason_code or "").strip(),
            "선택 구역을 실제 배경으로 렌더하지 못해 상대 위치 캔버스로 표시합니다.",
        )

    def _on_zone_crop_render_error_v2(
        self,
        pair_id: str,
        zone_id: str,
        message: str,
        status: str = "relative_only",
        request_id: str = "",
    ) -> None:
        if pair_id not in self._viewer_pairs_by_id:
            self._record_zone_render_perf_event_v2(
                "zone_render_stale",
                pair_id,
                zone_id,
                reason_code="inactive_pair_error",
                status=status,
            )
            logger.debug(
                "Ignoring stale zone crop render error for inactive pair=%s zone=%s",
                pair_id,
                zone_id,
            )
            return
        request_id = str(request_id or "")
        if not self._is_current_zone_render_request_v2(pair_id, zone_id, request_id):
            self._record_zone_render_perf_event_v2(
                "zone_render_stale",
                pair_id,
                zone_id,
                reason_code="superseded_error",
                status=status,
                request_id=request_id,
                active_request_id=self._active_zone_render_request_id_v2(pair_id, zone_id),
            )
            logger.debug(
                "Ignoring stale zone crop render error for superseded request pair=%s zone=%s request=%s",
                pair_id,
                zone_id,
                request_id,
            )
            self._start_pending_zone_render_v2()
            return
        if str(status or "").startswith("full_detail_"):
            self._record_zone_render_perf_event_v2(
                "zone_full_detail_upgrade_failed",
                pair_id,
                zone_id,
                render_lifecycle="timeout" if status == "full_detail_render_timeout" else "failed",
                visual_fidelity="cad_render",
                reason_code=status,
                renderer_backend="ezdxf-matplotlib-zone",
                error_message=str(message or "")[:500],
                request_id=request_id,
            )
            self._refresh_viewer_perf_summary_only()
            self._start_pending_zone_render_v2()
            return
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        if current_pair == pair_id:
            preview_status = "render_timeout" if status == "render_timeout" else "relative_only"
            self._set_preview_status_v2(
                pair_id,
                preview_status,
                f"선택 구역 렌더 실패 - 상대 위치 표시를 유지합니다.\n{message}",
            )
            if str(self._active_zone_id or "") == zone_id:
                self.zone_detail_v2.setHtml(self._zone_detail_text_v2(zone_id))
        self._start_pending_zone_render_v2()

    def _start_pending_zone_render_v2(self) -> None:
        pending = self._pending_zone_render_request_v2
        self._pending_zone_render_request_v2 = None
        if not pending:
            return
        pair_id, zone_id = str(pending[0]), str(pending[1])
        request_id = str(pending[2]) if len(pending) >= 3 else ""
        current_pair = str((self._active_row or {}).get("pair_id") or "")
        current_zone = str(self._active_zone_id or "")
        if (
            pair_id == current_pair
            and zone_id == current_zone
            and self._is_current_zone_render_request_v2(pair_id, zone_id, request_id)
        ):
            self._start_zone_crop_render_v2(zone_id)
        else:
            self._record_zone_render_perf_event_v2(
                "zone_render_pending_dropped",
                pair_id,
                zone_id,
                current_pair_uuid=current_pair,
                current_zone_id=current_zone,
                request_id=request_id,
                active_request_id=self._active_zone_render_request_id_v2(pair_id, zone_id),
            )

    def _update_viewer_manifest_pair_v2(self, pair_id: str, viewer_pair: dict) -> None:
        if not self._viewer_manifest_path or not self._viewer_manifest:
            return
        viewer_pair = {
            key: value
            for key, value in dict(viewer_pair or {}).items()
            if not str(key).startswith("_overlay_")
        }
        pairs = self._viewer_manifest.get("pairs")
        if not isinstance(pairs, list):
            return
        replaced = False
        for index, pair in enumerate(pairs):
            if isinstance(pair, dict) and str(pair.get("pair_id") or "") == pair_id:
                pairs[index] = viewer_pair
                replaced = True
                break
        if not replaced:
            pairs.append(viewer_pair)
        valid_pairs = [pair for pair in pairs if isinstance(pair, dict)]
        rendered_pairs = sum(1 for pair in valid_pairs if pair.get("after_image") and pair.get("after_transform"))
        self._viewer_manifest.update(
            {
                "pair_count": len(valid_pairs),
                "rendered_pair_count": rendered_pairs,
                "lazy_pair_count": max(0, len(valid_pairs) - rendered_pairs),
                "tile_count": sum(int(pair.get("tile_count") or 0) for pair in valid_pairs),
                "lod_tile_count": sum(int(pair.get("lod_tile_count") or 0) for pair in valid_pairs),
                "overlay_tile_count": sum(int(pair.get("overlay_tile_count") or 0) for pair in valid_pairs),
            }
        )
        try:
            _write_json_file(self._viewer_manifest_path, self._viewer_manifest)
            _write_index_html(self._viewer_manifest_path.parent / "index.html", self._viewer_manifest)
        except Exception:
            logger.warning("Failed to update viewer manifest after lazy render", exc_info=True)

    def _set_zone_action_buttons_enabled_v2(self, enabled: bool) -> None:
        for name in (
            "btn_zone_confirm_v2",
            "btn_zone_ignore_v2",
            "btn_zone_false_positive_v2",
            "btn_zone_needs_review_v2",
        ):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(bool(enabled))

    def _set_batch_action_button_enabled_v2(self, enabled: bool) -> None:
        """Phase G3.7 — enable the "이 도면 일괄 처리" button.

        Distinct from the per-zone status buttons because the batch
        action only needs the active pair to have at least one zone,
        not a currently-selected zone.
        """

        button = getattr(self, "btn_zone_batch_apply_v2", None)
        if button is not None:
            button.setEnabled(bool(enabled))

    # ---------------------------------------------------------------
    # Phase I2 — zone tree iteration helpers
    # ---------------------------------------------------------------
    # The zone widget is now a QTreeWidget grouped by AI category.
    # Top-level items are category headers (no zone_id); leaves are
    # selectable zones. These helpers hide the tree shape from the
    # rest of the workbench so callers can keep using "iterate zones"
    # / "find zone" / "next zone" semantics.

    def _zone_leaf_items_v2(self) -> list:
        """Yield every leaf (zone) item in the tree, top-down.

        Phase I3 — the tree is now up to 3 levels deep when clustering
        is enabled (category → cluster → zone). This walks recursively
        so callers ("next unreviewed", "find by zone_id", filter, etc.)
        keep the same flat-list semantics regardless of cluster depth.
        A node is a leaf iff it has zero children AND carries a zone_id
        in column-0 UserRole.
        """

        if not hasattr(self, "zone_list_v2"):
            return []
        out: list = []

        def _walk(node) -> None:
            if node is None:
                return
            if node.childCount() == 0:
                # Leaf if it has a zone_id (skip empty category placeholders)
                if str(node.data(0, Qt.UserRole) or ""):
                    out.append(node)
                return
            for child_idx in range(node.childCount()):
                _walk(node.child(child_idx))

        for top_idx in range(self.zone_list_v2.topLevelItemCount()):
            _walk(self.zone_list_v2.topLevelItem(top_idx))
        return out

    def _zone_leaf_count_v2(self) -> int:
        return len(self._zone_leaf_items_v2())

    def _find_zone_leaf_item_v2(self, zone_id: str):
        """Return the QTreeWidgetItem for ``zone_id`` or None."""

        target = str(zone_id or "").strip()
        if not target:
            return None
        for leaf in self._zone_leaf_items_v2():
            if str(leaf.data(0, Qt.UserRole) or "") == target:
                return leaf
        return None

    def _select_zone_leaf_v2(self, leaf) -> None:
        """Select the given leaf, auto-expanding every ancestor.

        Phase I3 — the tree is now up to 3 levels deep, so we walk all
        the way up to the top-level category instead of expanding one
        parent only.
        """

        if leaf is None:
            return
        node = leaf.parent()
        while node is not None:
            if not node.isExpanded():
                node.setExpanded(True)
            node = node.parent()
        self.zone_list_v2.setCurrentItem(leaf)
        self.zone_list_v2.scrollToItem(leaf)

    def _review_record_key_v2(self, pair_id: str, zone_id: str) -> str:
        return review_state_key(pair_id, zone_id)

    def _review_status_for_zone_v2(self, pair_id: str, zone_id: str) -> str:
        record = self._review_records_v2.get(self._review_record_key_v2(pair_id, zone_id))
        if record:
            return normalize_review_status(record.status)
        issue = self._active_issue_by_zone.get(zone_id, {})
        overlay = self._active_overlays_by_zone.get(zone_id, {})
        status = str(issue.get("status") or overlay.get("status") or "needs_review")
        return normalize_review_status(status)

    def _review_status_ko_v2(self, status: str) -> str:
        return {
            "needs_review": "추가 검토",
            "confirmed": "확인",
            "hold": "보류",
            "false_positive": "오탐",
        }.get(normalize_review_status(status), "추가 검토")

    def _show_batch_zone_action_dialog_v2(self) -> None:
        """Phase G3.7 — Modal dialog for mass-applying a status to many
        zones at once. Surfaced via "🚀 이 도면 일괄 처리..." button.

        Filters: change_type, severity, entity_type, current status.
        Target status: confirmed / hold / false_positive / needs_review.
        Live count shows how many zones will be affected; explicit
        confirm dialog prevents accidents when count > 10.
        """

        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
            QPushButton, QButtonGroup, QRadioButton, QMessageBox, QFrame,
        )

        if not self._active_overlays_by_zone:
            QMessageBox.information(
                self, "일괄 처리 불가",
                "현재 도면에 일괄 처리할 변경구역이 없습니다.",
            )
            return

        pair_id = str((self._active_row or {}).get("pair_id") or "")
        if not pair_id:
            QMessageBox.information(
                self, "일괄 처리 불가",
                "도면 페어가 선택되지 않았습니다.",
            )
            return

        overlays = list(self._active_overlays_by_zone.values())
        # Inject current review status into each overlay copy so the
        # filter can operate uniformly. Don't mutate the originals.
        enriched: list[dict] = []
        for overlay in overlays:
            zone_id = str(overlay.get("zone_id") or "")
            current_status = self._review_status_for_zone_v2(pair_id, zone_id)
            entry = dict(overlay)
            entry["_current_status"] = current_status
            enriched.append(entry)

        dialog = QDialog(self)
        dialog.setWindowTitle("🚀 일괄 변경구역 처리")
        dialog.resize(540, 460)
        layout = QVBoxLayout(dialog)

        # Header — drawing label + total count
        drawing_label = str((self._active_row or {}).get("drawing_label") or pair_id)
        header = QLabel(
            f"<b>현재 도면: {drawing_label}</b><br>"
            f"총 변경구역: {len(enriched)}개"
        )
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        # Filter controls — populated dynamically from the overlay pool
        layout.addWidget(QLabel("<b>필터</b>"))

        ANY_LABEL = "(모두)"

        def _unique_values(key: str) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for entry in enriched:
                val = str(entry.get(key) or "").strip()
                if val and val not in seen:
                    seen.add(val)
                    out.append(val)
            return sorted(out)

        change_type_values = _unique_values("change_type")
        severity_values = _unique_values("severity")
        entity_type_values = _unique_values("entity_type")
        layer_values = _unique_values("layer")
        status_values = sorted({entry["_current_status"] for entry in enriched})

        # Default to "needs_review" when present — that's the most
        # common bulk-confirm scenario.
        default_status = (
            "needs_review" if "needs_review" in status_values else ANY_LABEL
        )

        def _make_combo(label: str, values: list[str], default: str = ANY_LABEL) -> QComboBox:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            cmb = QComboBox()
            cmb.addItem(ANY_LABEL)
            for v in values:
                cmb.addItem(v)
            if default != ANY_LABEL:
                idx = cmb.findText(default)
                if idx >= 0:
                    cmb.setCurrentIndex(idx)
            row.addWidget(cmb)
            row.addStretch()
            layout.addLayout(row)
            return cmb

        cmb_change_type = _make_combo("변경 유형:", change_type_values)
        cmb_severity = _make_combo("심각도:", severity_values)
        cmb_entity_type = _make_combo("부재 종류:", entity_type_values)
        cmb_layer = _make_combo("레이어:", layer_values)
        cmb_status = _make_combo("현재 상태:", status_values, default=default_status)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep2)

        # Live count label — recomputed on any filter change
        count_label = QLabel("적용 대상: -")
        count_label.setStyleSheet("font-weight: bold; color: #0969DA;")
        layout.addWidget(count_label)

        # Target status — radio buttons
        layout.addWidget(QLabel("<b>변경 후 상태</b>"))
        status_group = QButtonGroup(dialog)
        rb_confirmed = QRadioButton("✅ 확인 (confirmed)")
        rb_ignored = QRadioButton("⏸ 보류 (hold)")
        rb_false = QRadioButton("❌ 오탐 (false_positive)")
        rb_needs = QRadioButton("🔍 추가 검토 (needs_review)")
        rb_confirmed.setChecked(True)
        for i, rb in enumerate((rb_confirmed, rb_ignored, rb_false, rb_needs)):
            status_group.addButton(rb, i)
            layout.addWidget(rb)

        status_map = {
            id(rb_confirmed): "confirmed",
            id(rb_ignored): "hold",
            id(rb_false): "false_positive",
            id(rb_needs): "needs_review",
        }

        def _selected_target_status() -> str:
            for rb in (rb_confirmed, rb_ignored, rb_false, rb_needs):
                if rb.isChecked():
                    return status_map[id(rb)]
            return "confirmed"

        def _current_filter() -> dict:
            return {
                "change_type": cmb_change_type.currentText(),
                "severity": cmb_severity.currentText(),
                "entity_type": cmb_entity_type.currentText(),
                "layer": cmb_layer.currentText(),
                "current_status": cmb_status.currentText(),
                "any_label": ANY_LABEL,
            }

        def _matched_zones() -> list[str]:
            return _filter_zones_for_batch_v2(enriched, _current_filter())

        def _refresh_count() -> None:
            count = len(_matched_zones())
            count_label.setText(f"적용 대상: <b>{count}</b>개 변경구역")
            apply_btn.setEnabled(count > 0)
            apply_btn.setText(f"💾 {count}개 일괄 적용" if count else "💾 일괄 적용")

        for cmb in (cmb_change_type, cmb_severity, cmb_entity_type, cmb_layer, cmb_status):
            cmb.currentIndexChanged.connect(_refresh_count)

        # Action buttons
        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(dialog.reject)
        apply_btn = QPushButton("💾 일괄 적용")
        apply_btn.setStyleSheet(
            "QPushButton { background-color: #0969DA; color: white; padding: 6px 14px; }"
            "QPushButton:disabled { background-color: #9CA3AF; }"
        )

        def _apply_clicked() -> None:
            zones = _matched_zones()
            target = _selected_target_status()
            if not zones:
                return
            # Confirmation dialog when the count is non-trivial.
            if len(zones) >= 10:
                reply = QMessageBox.question(
                    dialog, "일괄 적용 확인",
                    f"{len(zones)}개 변경구역을 '{self._review_status_ko_v2(target)}' "
                    f"상태로 변경합니다. 계속하시겠습니까?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            applied = self._apply_batch_zone_status_v2(pair_id, zones, target)
            QMessageBox.information(
                dialog, "일괄 처리 완료",
                f"{applied}개 변경구역이 '{self._review_status_ko_v2(target)}' "
                f"상태로 변경되었습니다.",
            )
            dialog.accept()

        apply_btn.clicked.connect(_apply_clicked)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(apply_btn)
        layout.addLayout(button_row)

        # Initial count
        _refresh_count()
        dialog.exec()

    def _apply_batch_zone_status_v2(
        self, pair_id: str, zone_ids: list[str], target_status: str,
    ) -> int:
        """Phase G3.7 — Apply ``target_status`` to every zone in ``zone_ids``
        for ``pair_id``. Persists once at the end (single I/O call instead
        of one save per zone). Refreshes the GUI affordances afterwards.

        Returns the number of zones successfully written.
        """

        target_status = normalize_review_status(target_status)
        if not zone_ids:
            return 0
        if self._review_state_path_v2 is None:
            if not self._result:
                return 0
            self._review_state_path_v2 = Path(self._result.review_state_path)

        applied = 0
        timestamp = datetime.now().isoformat()
        pair_uuid = str((self._active_row or {}).get("pair_uuid") or pair_id)
        ko_label = self._review_status_ko_v2(target_status)
        for zone_id in zone_ids:
            zid = str(zone_id or "").strip()
            if not zid:
                continue
            record = ReviewStateRecord(
                pair_id=pair_id,
                pair_uuid=pair_uuid,
                zone_id=zid,
                status=target_status,
                note=f"Workbench V2 batch: {ko_label}",
                updated_at=timestamp,
            )
            self._review_records_v2[record.key] = record
            applied += 1
            # Mirror the status into the active issue/overlay caches so
            # the zone list rendering reflects the new status without a
            # full reload.
            if zid in self._active_issue_by_zone:
                self._active_issue_by_zone[zid]["status"] = target_status
            if zid in self._active_overlays_by_zone:
                self._active_overlays_by_zone[zid]["status"] = target_status

        # Single persist instead of N writes
        save_review_state(self._review_state_path_v2, self._review_records_v2)

        # Refresh UI affordances (same surfaces as single-zone update).
        self._update_review_queue_summary_v2()
        self._refresh_zone_list_filter_v2()
        self._update_review_progress_v2()
        self._refresh_drawing_progress_badges_v2()
        # Refresh detail pane if the active zone was part of the batch.
        if str(self._active_zone_id or "") in zone_ids:
            self.zone_detail_v2.setHtml(
                self._zone_detail_text_v2(str(self._active_zone_id))
            )
        logger.info(
            "Batch applied: %d zones → %s on pair %s",
            applied, target_status, pair_id,
        )
        return applied

    def _set_zone_review_status_v2(self, status: str) -> None:
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        zone_id = str(self._active_zone_id or "")
        if not pair_id or not zone_id:
            return
        status = normalize_review_status(status)
        if self._review_state_path_v2 is None:
            if not self._result:
                return
            self._review_state_path_v2 = Path(self._result.review_state_path)
        record = ReviewStateRecord(
            pair_id=pair_id,
            pair_uuid=str((self._active_row or {}).get("pair_uuid") or pair_id),
            zone_id=zone_id,
            status=status,
            note=f"Workbench V2: {self._review_status_ko_v2(status)}",
            updated_at=datetime.now().isoformat(),
        )
        self._review_records_v2[record.key] = record
        save_review_state(self._review_state_path_v2, self._review_records_v2)
        if zone_id in self._active_issue_by_zone:
            self._active_issue_by_zone[zone_id]["status"] = status
        if zone_id in self._active_overlays_by_zone:
            self._active_overlays_by_zone[zone_id]["status"] = status
        self.zone_detail_v2.setHtml(self._zone_detail_text_v2(zone_id))
        self._update_review_queue_summary_v2()
        self._refresh_zone_list_filter_v2()
        self._update_review_progress_v2()
        # Phase G3.8 — refresh the per-row drawing progress badges so the
        # current row's "⏳ N/M" or "✅ 완료" updates immediately. Cheap:
        # walks _drawing_rows once. Preserves the user's current selection.
        self._refresh_drawing_progress_badges_v2()
        # C1 — auto-advance to the next zone after marking so the reviewer
        # can keep their hand on the keyboard. Skips already-reviewed zones
        # (status != "needs_review") to make rapid triage feel natural.
        if getattr(self, "_auto_advance_v2", True):
            self._advance_to_next_unreviewed_zone_v2()

    def _refresh_drawing_progress_badges_v2(self) -> None:
        """Phase G3.8 — Refresh the badge text on every visible drawing row
        without triggering selection change or rebuilding the list.

        Walks each item in ``drawing_list_v2``, recomputes the badge prefix
        for that row's pair_id, and rewrites the item text. Keeps current
        selection intact so the user's view doesn't jump.
        """

        if not hasattr(self, "drawing_list_v2"):
            return
        for idx in range(self.drawing_list_v2.count()):
            item = self.drawing_list_v2.item(idx)
            if item is None:
                continue
            row = item.data(Qt.UserRole)
            if not isinstance(row, dict):
                continue
            pair_id = str(row.get("pair_id") or "")
            new_badge = self._drawing_progress_badge_v2(pair_id)
            current_text = item.text()
            # Replace the badge in the FIRST line. Old format:
            #   "{drawing_number}  {old_badge}\n..." or just "{drawing_number}\n..."
            lines = current_text.split("\n", 1)
            head = lines[0]
            tail = lines[1] if len(lines) > 1 else ""
            # Strip prior badge by splitting on double-space
            base = head.split("  ", 1)[0]
            new_head = f"{base}  {new_badge}".rstrip()
            item.setText(f"{new_head}\n{tail}" if tail else new_head)

    def _advance_to_next_unreviewed_zone_v2(self) -> None:
        """Select the next zone in the list whose review status is unset/needs_review.

        Phase G3.5 — when no unreviewed zone remains in the current pair,
        scan the drawing list for the next pair that DOES have unreviewed
        zones and auto-select it (so the reviewer can keep pressing 1/2/3/4
        across drawings without ever touching the mouse). When every pair
        is fully reviewed, surface a completion banner instead of silently
        no-op-ing.
        """

        if not hasattr(self, "zone_list_v2"):
            return
        # Phase I2 — operate on flattened leaf items (tree-aware).
        leaf_items = self._zone_leaf_items_v2()
        count = len(leaf_items)
        pair_id = str((self._active_row or {}).get("pair_id") or "")

        # 1. Look for next unreviewed zone within the current pair.
        if count > 0:
            current_item = self.zone_list_v2.currentItem()
            try:
                current_idx = leaf_items.index(current_item) if current_item in leaf_items else -1
            except ValueError:
                current_idx = -1
            for offset in range(1, count + 1):
                idx = (current_idx + offset) % count
                item = leaf_items[idx]
                if item is None or item.isHidden():
                    continue
                zone_id = str(item.data(0, Qt.UserRole) or "")
                status = self._review_status_for_zone_v2(pair_id, zone_id)
                if status == "needs_review":
                    self._select_zone_leaf_v2(item)
                    return

        # 2. Phase G3.5 — current pair done, jump to next pair with work.
        #    Walk drawing_list_v2 forward, then wrap; for each pair check
        #    the cached overlays to find one with unreviewed zones.
        if not hasattr(self, "drawing_list_v2"):
            return
        dl_count = self.drawing_list_v2.count()
        if dl_count <= 0:
            return
        cur_drawing_row = self.drawing_list_v2.currentRow()

        def _pair_has_unreviewed(target_pair_id: str, target_row: dict) -> bool:
            declared = self._viewer_declared_overlay_count_for_pair_v2(
                target_pair_id,
                row=target_row,
                viewer_pair=self._viewer_pairs_by_id.get(target_pair_id, {}),
            )
            done, _confirmed = self._review_record_counts_for_pair_v2(target_pair_id)
            if declared > 0:
                return done < declared
            overlays = self._viewer_initial_overlays_from_page_store_v2(target_pair_id, 1)
            if overlays is None:
                if self._overlay_json_file_size_for_pair_v2(target_pair_id) > GUI_UNKNOWN_OVERLAY_JSON_DEFER_BYTES:
                    return done == 0
                overlays = self._viewer_overlays_for_pair_v2(target_pair_id) or []
            for ov in overlays:
                zid = str(ov.get("zone_id") or "")
                if not zid:
                    continue
                status = self._review_status_for_zone_v2(target_pair_id, zid)
                if status == "needs_review":
                    return True
            return False

        for offset in range(1, dl_count + 1):
            idx = (cur_drawing_row + offset) % dl_count
            item = self.drawing_list_v2.item(idx)
            if item is None or item.isHidden():
                continue
            row = item.data(Qt.UserRole)
            if not isinstance(row, dict):
                continue
            target_pair_id = str(row.get("pair_id") or "")
            if not target_pair_id or target_pair_id == pair_id:
                continue
            if _pair_has_unreviewed(target_pair_id, row):
                logger.info(
                    "Auto-advance: current pair done, jumping to drawing row %d (%s)",
                    idx, target_pair_id,
                )
                self.drawing_list_v2.setCurrentRow(idx)
                if hasattr(self, "lbl_status_v2"):
                    self.lbl_status_v2.setText(
                        f"검토 완료 — 다음 도면으로 이동: {row.get('drawing_number') or target_pair_id}"
                    )
                return

        # 3. Everything is reviewed — show the celebratory completion banner.
        # (Phase G3.5 — removed the duplicate older "이 도면의 모든 변경구역
        # 검토 완료" setText that was overwriting the new banner.)
        logger.info("Auto-advance: every pair fully reviewed")
        if hasattr(self, "lbl_status_v2"):
            self.lbl_status_v2.setText(
                "🎉 모든 검토구역이 처리되었습니다 — 보고서 생성 또는 결과 확인 가능"
            )

    def _on_zone_filter_changed_v2(self, _text: str) -> None:
        self._refresh_zone_list_filter_v2()

    def _refresh_zone_list_filter_v2(self) -> None:
        """Show/hide zone_tree leaves based on review-status + AI category filter.

        Phase I2 + I3 fix — operates on the QTreeWidget recursively so the
        3-level layout (category → cluster → zone) is handled correctly.
        Leaf nodes (those carrying ``zone_id`` in column-0 UserRole) get
        their visibility evaluated against the filter; intermediate nodes
        (categories, clusters) are hidden iff every descendant leaf is
        hidden. Without recursion the cluster nodes (zone_id="") were
        being treated as zones with status "needs_review", which caused
        the entire cluster (and all its grandchildren) to disappear when
        the user selected any non-default status filter.
        """

        if not hasattr(self, "zone_list_v2") or not hasattr(self, "cmb_zone_filter_v2"):
            return
        wanted = self.cmb_zone_filter_v2.currentText()
        category_filter = getattr(self, "_active_category_filter_v2", "전체")
        pair_id = str((self._active_row or {}).get("pair_id") or "")

        def _zone_passes_filter(zone_id: str) -> bool:
            status = self._review_status_for_zone_v2(pair_id, zone_id)
            if wanted == "미검토만":
                visible = status == "needs_review"
            elif wanted == "확인":
                visible = status == "confirmed"
            elif wanted == "보류":
                visible = status == "hold"
            elif wanted == "오탐":
                visible = status == "false_positive"
            elif wanted == "검토 완료(전체)":
                visible = status != "needs_review"
            else:
                visible = True
            if visible and category_filter and category_filter != "전체":
                category = self._zone_category_for(pair_id, zone_id)
                if category is None or category.category != category_filter:
                    visible = False
            return visible

        def _walk(node) -> bool:
            """Return True if ``node`` should be visible.

            Leaf (zone_id present): visibility = filter result.
            Intermediate (category/cluster): visibility = OR of children's
            visibility. An intermediate node with no descendants stays
            visible (same as the empty-tree case — nothing to filter).
            """

            if node is None:
                return False
            zone_id = str(node.data(0, Qt.UserRole) or "")
            if node.childCount() == 0:
                # Leaf node — only counts if it carries a zone_id
                if not zone_id:
                    # Empty placeholder (shouldn't happen but be safe)
                    return False
                visible = _zone_passes_filter(zone_id)
                node.setHidden(not visible)
                return visible
            # Intermediate node — recurse into children, OR the result.
            any_visible = False
            for child_idx in range(node.childCount()):
                if _walk(node.child(child_idx)):
                    any_visible = True
            node.setHidden(not any_visible)
            return any_visible

        for top_idx in range(self.zone_list_v2.topLevelItemCount()):
            _walk(self.zone_list_v2.topLevelItem(top_idx))

    def _update_review_progress_v2(self) -> None:
        """Refresh the per-pair + project-level review progress labels.

        Phase G3.6 — upgraded from plain text to HTML with:
          - Coloured per-status counts (matching the chip palette)
          - Unicode block progress bar (20 cells) for current drawing
          - Project-level total ("X of Y across all drawings")

        The widget is the same QLabel — we now use ``setTextFormat(RichText)``
        + setText so the existing layout is unchanged.
        """

        if not hasattr(self, "lbl_zone_progress_v2"):
            return
        if not hasattr(self, "zone_list_v2"):
            return
        # Phase I2 — count leaf items only (tree, not flat list)
        leaf_items = self._zone_leaf_items_v2()
        total = len(leaf_items)
        if total == 0:
            self.lbl_zone_progress_v2.setText("진행: -")
            return
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        confirmed = hold = false_positive = pending = 0
        for item in leaf_items:
            zone_id = str(item.data(0, Qt.UserRole) or "")
            status = self._review_status_for_zone_v2(pair_id, zone_id)
            if status == "confirmed":
                confirmed += 1
            elif status == "hold":
                hold += 1
            elif status == "false_positive":
                false_positive += 1
            else:
                pending += 1
        done = confirmed + hold + false_positive
        pct = int(round(done / total * 100)) if total else 0

        # Phase G3.6/P5 — project-wide totals from declared row/package counts.
        # The full overlay cache is intentionally bounded and may be empty for
        # paged stores, so it cannot be the source of project progress truth.
        proj_total = 0
        proj_done = 0
        proj_confirmed = 0
        seen_pairs: set[str] = set()
        for row in list(getattr(self, "_drawing_rows", []) or []):
            if not isinstance(row, dict):
                continue
            row_pair_id = str(row.get("pair_id") or "")
            if not row_pair_id or row_pair_id in seen_pairs:
                continue
            seen_pairs.add(row_pair_id)
            declared = self._viewer_declared_overlay_count_for_pair_v2(
                row_pair_id,
                row=row,
                viewer_pair=self._viewer_pairs_by_id.get(row_pair_id, {}),
            )
            row_done, row_confirmed = self._review_record_counts_for_pair_v2(row_pair_id)
            proj_total += max(0, int(declared))
            proj_done += min(max(0, int(row_done)), max(0, int(declared))) if declared > 0 else 0
            proj_confirmed += min(max(0, int(row_confirmed)), max(0, int(declared))) if declared > 0 else 0
        # Use the visible-zone count when project cache hasn't loaded yet.
        if proj_total < total:
            proj_total = total
            proj_done = done
            proj_confirmed = confirmed
        proj_pct = int(round(proj_done / max(1, proj_total) * 100))

        # Unicode block bar — 20 cells, 5 % each
        bar_filled = int(round(pct / 5))
        bar = "█" * bar_filled + "░" * (20 - bar_filled)

        from html import escape as _e

        html = (
            f'<div style="font-size:12px; line-height:1.4;">'
            f'<b>현재 도면</b>: {done}/{total} '
            f'<span style="color:#16A34A; font-weight:bold;">✓{confirmed}</span> · '
            f'<span style="color:#9CA3AF;">⏸{hold}</span> · '
            f'<span style="color:#F97316;">⊘{false_positive}</span> · '
            f'<span style="color:#0969DA;">⊙{pending}</span>'
            f' <span style="color:#6B7280;">({pct}%)</span><br>'
            f'<span style="font-family:Consolas,monospace; color:#16A34A;">{_e(bar)}</span><br>'
            f'<b>전체 프로젝트</b>: {proj_done}/{proj_total} '
            f'<span style="color:#6B7280;">({proj_pct}%)</span> · '
            f'<span style="color:#16A34A;">✓{proj_confirmed}건 확인 완료</span>'
            f'</div>'
        )
        self.lbl_zone_progress_v2.setTextFormat(Qt.RichText)
        self.lbl_zone_progress_v2.setText(html)

    def _build_zone_tree_plan_v2(
        self,
        preview: Optional[PreviewArtifact],
        overlays: Optional[list[dict]] = None,
        *,
        allow_clustering: bool = True,
        prefer_overlays: bool = False,
    ) -> list[dict]:
        pair_id_for_cat = str((self._active_row or {}).get("pair_id") or "")
        preview_zones: list[dict] = []
        if preview:
            for zone in sorted(
                getattr(preview, "zone_overlays", []) or [],
                key=lambda item: (-int(getattr(item, "raw_change_count", 0) or 0), str(getattr(item, "zone_id", ""))),
            ):
                preview_zones.append({
                    "zone_id": getattr(zone, "zone_id", ""),
                    "change_type": getattr(zone, "change_type", ""),
                    "severity": getattr(zone, "severity", ""),
                    "raw_change_count": int(getattr(zone, "raw_change_count", 0) or 0),
                })
        plan, active_issue_by_zone = _build_zone_tree_plan_data_v2(
            dashboard_issues=list((self._active_row or {}).get("top_issues") or []),
            overlays=list(overlays or []),
            preview_zones=preview_zones,
            category_by_zone=dict(self._zone_categories_v2.get(pair_id_for_cat, {})),
            active_zone_id=str(self._active_zone_id or ""),
            allow_clustering=allow_clustering,
            clustering_enabled=bool(getattr(self, "_zone_clustering_enabled_v2", True)),
            prefer_overlays=prefer_overlays,
        )
        self._active_issue_by_zone = active_issue_by_zone
        return plan

    def _make_zone_tree_header_item_v2(self, group: dict) -> QTreeWidgetItem:
        header = QTreeWidgetItem([str(group.get("header_text") or "")])
        header.setData(0, Qt.UserRole, "")
        tooltip = str(group.get("tooltip") or "")
        if tooltip:
            header.setToolTip(0, tooltip)
        return header

    def _append_zone_tree_plan_item_v2(self, parent: QTreeWidgetItem, item: dict) -> int:
        kind = str(item.get("kind") or "")
        if kind == "cluster":
            cluster_node = QTreeWidgetItem([str(item.get("label") or "")])
            cluster_node.setData(0, Qt.UserRole, "")
            tooltip = str(item.get("tooltip") or "")
            if tooltip:
                cluster_node.setToolTip(0, tooltip)
            parent.addChild(cluster_node)
            added = 1
            for child in item.get("children") or []:
                added += self._append_zone_tree_plan_item_v2(cluster_node, child)
            cluster_node.setExpanded(bool(item.get("expanded")))
            return added
        zone_id = str(item.get("zone_id") or "")
        if not zone_id:
            return 0
        leaf = QTreeWidgetItem([str(item.get("label") or "")])
        leaf.setData(0, Qt.UserRole, zone_id)
        parent.addChild(leaf)
        return 1

    def _append_zone_tree_plan_immediate_v2(self, plan: list[dict]) -> None:
        for group in plan:
            header = self._make_zone_tree_header_item_v2(group)
            self.zone_list_v2.addTopLevelItem(header)
            for item in group.get("items") or []:
                self._append_zone_tree_plan_item_v2(header, item)
            header.setExpanded(bool(group.get("expanded")))

    def _populate_zone_list_v2(
        self,
        preview: Optional[PreviewArtifact],
        overlays: Optional[list[dict]] = None,
        *,
        prefer_overlays: bool = False,
    ) -> None:
        """Phase I2 — Build the category-grouped zone tree.

        The tree is two levels deep:
          - top-level: AI category header (e.g. "🏗️ 구조 부재 변경 (12)")
          - leaves: selectable zone rows

        The data source is one of (in priority order):
          1. ``self._active_row['top_issues']`` — dashboard's pre-ranked
             list with priority_score (preferred when available)
          2. ``overlays`` parameter — full overlay dicts from the
             change_zones export
          3. ``preview.zone_overlays`` — fallback for older runs without
             dashboard / overlay data
        """

        self.zone_list_v2.clear()
        self._set_zone_action_buttons_enabled_v2(False)
        self._active_overlays_by_zone = {
            str(overlay.get("zone_id") or ""): overlay
            for overlay in (overlays or [])
            if isinstance(overlay, dict) and overlay.get("zone_id")
        }
        # Phase G3.7 — batch action available whenever the active pair
        # has at least one zone, regardless of per-zone selection.
        self._set_batch_action_button_enabled_v2(bool(self._active_overlays_by_zone))
        plan = self._build_zone_tree_plan_v2(preview, overlays, prefer_overlays=prefer_overlays)
        self._append_zone_tree_plan_immediate_v2(plan)

    def _on_zone_selected_v2(self, current, _previous=None) -> None:
        selection_started = perf_counter()
        if not current:
            return
        # Phase I2 — QTreeWidgetItem.data takes (column, role); category
        # headers and cluster nodes have empty UserRole so we early-
        # return on those (the click already expanded/collapsed the
        # node which is enough behaviour).
        # Also clear ``_active_zone_id`` so a follow-up review hotkey
        # (1/2/3/4) doesn't accidentally apply to the previously-active
        # zone — the user's visible focus is now the header, not a zone.
        zone_id = str(current.data(0, Qt.UserRole) or "")
        if not zone_id:
            self._active_zone_id = ""
            self._selected_zone_render_generation_v2 += 1
            self._active_zone_render_request_v2 = None
            self._set_zone_action_buttons_enabled_v2(False)
            self._set_lightweight_zone_side_messages_v2("")
            return
        self._active_zone_id = zone_id
        self._set_zone_action_buttons_enabled_v2(bool(zone_id))
        if not DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY:
            self.preview_before_v2.focus_zone(zone_id, padding_ratio=0.25)
            self.preview_after_v2.focus_zone(zone_id, padding_ratio=0.25)
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        self._begin_selected_zone_render_request_v2(pair_id, zone_id)
        defer_heavy_render = self._consume_initial_zone_heavy_render_defer_v2(pair_id, zone_id)
        # Phase G2.3 — also focus the lightweight viewport when active.
        # No-op when the legacy viewport is showing (toggle OFF).
        try:
            self._set_lightweight_zone_side_messages_v2(zone_id)
            self._focus_lightweight_on_zone_v2(zone_id)
        except Exception:
            logger.exception("Lightweight zone focus failed for %s", zone_id)
        self.zone_detail_v2.setHtml(self._zone_detail_text_v2(zone_id))
        self._load_current_zone_memo_v2()
        # Phase B1.5 — clear any prior zone's vector overlay before we
        # decide whether to push a new one. Stale overlay would visually
        # misalign with the new zone's PNG focus.
        if hasattr(self, "preview_before_v2"):
            self.preview_before_v2.clear_vector_overlay()
        if hasattr(self, "preview_after_v2"):
            self.preview_after_v2.clear_vector_overlay()
        if defer_heavy_render:
            self._schedule_initial_zone_heavy_render_v2(pair_id, zone_id)
        else:
            self._start_zone_crop_render_v2(zone_id)
        self._refresh_zone_vector_button_state_v2()
        self._record_zone_selection_event_v2(zone_id, selection_started)

    # ------------------------------------------------------------------
    # Phase B1 — Vector zone inspection
    # ------------------------------------------------------------------

    def _apply_or_start_zone_vector_render_v2(self, pair_id: str, zone_id: str) -> None:
        cached = self._zone_vector_paths.get((pair_id, zone_id)) if pair_id else None
        if cached and Path(cached).exists():
            self._apply_zone_vector_to_qml_v2(pair_id, zone_id, cached)
        elif pair_id and zone_id:
            # Vector SVG is an enhancement. It may be slow on malformed or
            # block-heavy CAD, so P2 keeps it out of the immediate initial
            # pair-paint path and only runs it from the explicit heavy phase.
            self._start_zone_vector_render_v2(pair_id, zone_id)

    def _refresh_zone_vector_button_state_v2(self) -> None:
        """Enable the vector button only when we have a valid zone bbox to
        render. Disabled state during in-flight render or when bbox is
        missing prevents double-spawn / nonsense subprocess invocations."""

        btn = getattr(self, "btn_zone_vector_v2", None)
        if btn is None:
            return
        zone_id = str(self._active_zone_id or "")
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        overlay = self._active_overlays_by_zone.get(zone_id, {}) if zone_id else {}
        # Use the same world-bbox logic as the render path so the button
        # state precisely reflects whether render will succeed.
        bbox = self._world_bbox_from_overlay_v2(pair_id, overlay) if overlay else None
        in_flight = (
            self._zone_vector_qprocess is not None
            and self._zone_vector_qprocess.state() != QProcess.NotRunning
        )
        btn.setEnabled(bool(zone_id and pair_id and bbox) and not in_flight)
        if (pair_id, zone_id) in self._zone_vector_paths:
            btn.setText("🔍 벡터로 자세히 보기 (캐시됨)")
        elif in_flight:
            btn.setText("⏳ 벡터 렌더 중…")
        else:
            btn.setText("🔍 벡터로 자세히 보기")

    def _on_zone_vector_button_clicked_v2(self) -> None:
        """User explicitly requested external open of vector zoom for the
        active zone. Also pushes the SVG inline if not already shown.

        Auto-trigger from zone selection produces the inline overlay; the
        button explicitly adds external open on top so users with a
        preferred viewer (Inkscape, browser, AutoCAD) get one click to
        their tool of choice. ``_zone_vector_button_external`` latches
        the next finish to also open externally.
        """

        zone_id = str(self._active_zone_id or "")
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        if not zone_id or not pair_id:
            return
        cached = self._zone_vector_paths.get((pair_id, zone_id))
        if cached and Path(cached).exists():
            # Already rendered → push inline AND open externally.
            self._apply_zone_vector_to_qml_v2(pair_id, zone_id, cached)
            self._open_svg_externally_v2(cached)
            return
        # Not rendered yet → spawn worker; finish handler will both
        # push inline (always) and open externally (because of the flag).
        self._zone_vector_button_external = True
        self._start_zone_vector_render_v2(pair_id, zone_id)

    def _zone_vector_cache_dir(self) -> Path:
        return self._viewer_cache_root_v2() / "zone_vector"

    def _zone_vector_output_path(self, pair_id: str, zone_id: str) -> Path:
        from hashlib import sha1

        safe_pair = sha1(pair_id.encode("utf-8", "replace")).hexdigest()[:12]
        safe_zone = sha1(zone_id.encode("utf-8", "replace")).hexdigest()[:12]
        return self._zone_vector_cache_dir() / f"{safe_pair}_{safe_zone}.svg"

    def _world_bbox_from_overlay_v2(
        self, pair_id: str, overlay: dict
    ) -> Optional[tuple[float, float, float, float]]:
        """Compute the world-coordinate bbox for an overlay.

        Why this is non-trivial: the manifest's ``bbox`` / ``old_bbox``
        fields are the entity's *local* extents (e.g. an INSERT block
        reference's ±80 internal box) — same value for every column
        marker in the drawing — NOT the world location of the change.
        ``after_bbox_px`` carries the actual location, but in PNG pixel
        coordinates from the Phase A3 fast renderer.

        The reverse-mapping uses the transform stored in the manifest:
        ``after_transform`` has ``min_x/max_x/min_y/max_y`` (world extents)
        plus ``img_width/img_height`` (PNG pixel size). Inverting gives
        us the world bbox the vector renderer needs.

        Falls back to the local bbox if transform/pixel data is missing
        — at least the renderer will then use its 250-unit min_size
        floor and produce something rather than crashing.
        """

        viewer_pair = self._viewer_pair_from_row_v2(pair_id, self._active_row or {})
        after_tx = viewer_pair.get("after_transform") or {}
        # Prefer after_bbox_px (post-change location); before is fallback.
        px_bbox = overlay.get("after_bbox_px") or overlay.get("before_bbox_px")
        if not after_tx or not isinstance(px_bbox, dict):
            return union_bboxes(overlay.get("old_bbox"), overlay.get("bbox"))
        try:
            img_w = float(after_tx.get("img_width", 0))
            img_h = float(after_tx.get("img_height", 0))
            min_x = float(after_tx.get("min_x", 0))
            max_x = float(after_tx.get("max_x", 0))
            min_y = float(after_tx.get("min_y", 0))
            max_y = float(after_tx.get("max_y", 0))
            px = float(px_bbox.get("x", 0))
            py = float(px_bbox.get("y", 0))
            pw = float(px_bbox.get("width", 0))
            ph = float(px_bbox.get("height", 0))
        except (TypeError, ValueError):
            return union_bboxes(overlay.get("old_bbox"), overlay.get("bbox"))
        width_w = max_x - min_x
        height_w = max_y - min_y
        if img_w <= 0 or img_h <= 0 or width_w == 0 or height_w == 0:
            return union_bboxes(overlay.get("old_bbox"), overlay.get("bbox"))
        # Pixel y is top-down (PNG); world y is bottom-up (CAD). Swap.
        x0 = min_x + (px / img_w) * width_w
        x1 = min_x + ((px + pw) / img_w) * width_w
        y1 = max_y - (py / img_h) * height_w
        y0 = max_y - ((py + ph) / img_h) * height_w
        # Enforce a minimum world size so a 1×1 pixel marker doesn't
        # produce a degenerate bbox the renderer has to also defend
        # against. Calibrated against the customer 71 MB industrial
        # drawings (extents ~410 k × 174 k mm): a 5 k box renders
        # almost no structural geometry — it's mostly column-tag glyphs
        # that the reviewer can't navigate from. 50 k (~12 % of long
        # edge) reliably captures grid lines, dimensions, and the
        # surrounding columns so the reviewer sees the change in
        # structural context. Browsers + Inkscape can zoom in further
        # losslessly because the SVG is vector — bigger floor here is
        # cheap on the visual side, beneficial for the navigation side.
        MIN_WORLD_SIZE = 50000.0
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        half_w = max(abs(x1 - x0) / 2.0, MIN_WORLD_SIZE / 2.0)
        half_h = max(abs(y1 - y0) / 2.0, MIN_WORLD_SIZE / 2.0)
        return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)

    def _start_zone_vector_render_v2(self, pair_id: str, zone_id: str) -> None:
        """Spawn the vector subprocess for one zone. Subsequent calls
        while one is in flight queue the latest request and replace any
        prior pending one."""

        overlay = self._active_overlays_by_zone.get(zone_id, {})
        bbox = self._world_bbox_from_overlay_v2(pair_id, overlay) if overlay else None
        if not bbox:
            self._set_preview_status_v2(
                pair_id,
                "relative_only",
                "선택 변경구역에 CAD bbox가 없어 벡터 렌더를 건너뜁니다.",
            )
            return
        viewer_pair = self._viewer_pair_from_row_v2(pair_id, self._active_row or {})
        if _viewer_pair_is_pdf(viewer_pair):
            self._set_preview_status_v2(
                pair_id,
                "pdf_render",
                "PDF 비교는 벡터 SVG 렌더 대신 PDF 배경과 핀/구름마크로 표시합니다.",
            )
            return
        # Prefer the AFTER source for the vector render — that's the
        # post-change state the reviewer is judging. before/after symmetry
        # would double the cost for marginal benefit on first delivery.
        dxf_path = str(viewer_pair.get("source_b") or viewer_pair.get("source_a") or "")
        if not dxf_path:
            return
        if _is_redacted_artifact_path(dxf_path) or not Path(dxf_path).exists():
            self._set_preview_status_v2(
                pair_id,
                "relative_only",
                "고객 공유 패키지에서 원본 CAD 경로가 제거되어 벡터 렌더를 건너뜁니다.",
            )
            return

        cache_dir = self._zone_vector_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        output_svg = self._zone_vector_output_path(pair_id, zone_id)
        result_json = output_svg.with_suffix(".result.json")

        # Use '=' separator on every flag whose value can plausibly start
        # with '-' (negative DXF world coords are common: drawings frequently
        # span ranges like -800000..-400000). argparse treats a bare value
        # starting with '-' as a flag and aborts with exit 2 — which silently
        # broke the worker spawn the first time we tested on the customer's
        # 71 MB DXF.
        program, worker_args = worker_command_for_module(ZONE_VECTOR_WORKER_MODULE)
        cmd_args = [
            *worker_args,
            f"--dxf-path={dxf_path}",
            f"--zone-bbox={bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f}",
            f"--output-svg={output_svg}",
            f"--result-json={result_json}",
        ]

        if self._zone_vector_qprocess is not None and self._zone_vector_qprocess.state() != QProcess.NotRunning:
            # Existing render still in flight — let it finish; new request
            # can be retried after it finishes. Do not rewrite the running
            # request metadata; that lets a stale SVG masquerade as the new
            # selected zone.
            return
        self._zone_vector_result_json = result_json
        self._zone_vector_pending = (pair_id, zone_id, str(output_svg))

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.finished.connect(self._on_zone_vector_finished_v2)
        proc.errorOccurred.connect(
            lambda err: logger.warning("zone_vector_worker QProcess error: %s", err)
        )
        self._zone_vector_qprocess = proc
        self._refresh_zone_vector_button_state_v2()
        proc.setWorkingDirectory(str(_workbench_worker_cwd()))
        proc.start(program, cmd_args)

    def _on_zone_vector_finished_v2(self, exit_code: int, exit_status) -> None:
        proc = self._zone_vector_qprocess
        self._zone_vector_qprocess = None
        pending = self._zone_vector_pending
        self._zone_vector_pending = None
        if proc is None or pending is None:
            self._refresh_zone_vector_button_state_v2()
            return
        pair_id, zone_id, expected_svg = pending
        result_json = self._zone_vector_result_json
        self._zone_vector_result_json = None
        payload: dict = {}
        if result_json and result_json.exists():
            try:
                payload = json.loads(result_json.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("zone_vector result.json parse failed: %s", exc)
        svg_path = str(payload.get("svg_path") or "")
        svg_matches_request = False
        if svg_path:
            try:
                svg_matches_request = Path(svg_path).resolve() == Path(expected_svg).resolve()
            except Exception:
                svg_matches_request = str(svg_path) == str(expected_svg)
        if svg_path and not svg_matches_request:
            logger.debug(
                "Ignoring stale zone vector result for pair=%s zone=%s svg=%s expected=%s",
                pair_id,
                zone_id,
                svg_path,
                expected_svg,
            )
            self._refresh_zone_vector_button_state_v2()
            return
        if exit_code == 0 and svg_path and svg_matches_request and Path(svg_path).exists():
            self._zone_vector_paths[(pair_id, zone_id)] = svg_path
            # If the user is still on the same zone, push the SVG INLINE
            # to the QML viewer (Phase B1.5). Inline overlay replaces the
            # need to open externally for most reviewers — they can mouse-
            # wheel zoom directly inside the workbench without losing
            # context. The button-triggered external-open path is still
            # available for users who prefer their browser/Inkscape.
            if (
                str(self._active_zone_id or "") == zone_id
                and str((self._active_row or {}).get("pair_id") or "") == pair_id
            ):
                self._apply_zone_vector_to_qml_v2(pair_id, zone_id, svg_path)
                # Only auto-open externally when the user explicitly
                # asked via button click; ``_zone_vector_button_external``
                # latches True for one render then resets, so the next
                # auto-trigger from zone selection stays inline-only.
                if self._zone_vector_button_external:
                    self._zone_vector_button_external = False
                    self._open_svg_externally_v2(svg_path)
        else:
            reason = payload.get("skipped_reason") or f"exit code {exit_code}"
            # G2.7-COORDFIX — Distinguish "expected skip" (PDF, no bbox)
            # from real failures so the user doesn't see a red "벡터 렌더
            # 실패" banner when the lightweight viewer is actually
            # rendering the PDF correctly. The Korean reason strings come
            # from zone_vector_renderer.render_zone_svg() and are stable.
            reason_text = str(reason)
            is_expected_skip = (
                "PDF는 벡터 SVG로 변환하지 않습니다" in reason_text
                or "CAD bbox" in reason_text
                or "변경구역에 좌표 정보가 없" in reason_text
            )
            if is_expected_skip:
                self._set_preview_status_v2(
                    pair_id,
                    "preview_reused",
                    f"신형(경량) 뷰어로 표시 중 — {reason_text}",
                )
            else:
                self._set_preview_status_v2(
                    pair_id,
                    "relative_only",
                    f"벡터 렌더 실패 — {reason_text}",
                )
        self._refresh_zone_vector_button_state_v2()

    def _apply_zone_vector_to_qml_v2(self, pair_id: str, zone_id: str, svg_path: str) -> None:
        """Compute pixel-coord bbox for the SVG overlay and push to both
        before/after preview QML viewports.

        The SVG was rendered for the AFTER source's world bbox; we map
        that bbox through the same after_transform the manifest stored
        for the PNG, giving us the pixel rectangle to overlay. The
        before viewport gets the SAME pixel rectangle — slightly
        misaligned for zones where geometry shifted between revisions,
        but acceptable for first delivery (the cloud markers already
        carry the precise per-side bbox).
        """

        viewer_pair = self._viewer_pair_from_row_v2(pair_id, self._active_row or {})
        overlay = self._active_overlays_by_zone.get(zone_id, {})
        world_bbox = self._world_bbox_from_overlay_v2(pair_id, overlay)
        if not world_bbox:
            return
        after_tx = viewer_pair.get("after_transform") or {}
        before_tx = viewer_pair.get("before_transform") or after_tx

        def _world_to_pixel(tx: dict, wbb) -> Optional[tuple[float, float, float, float]]:
            try:
                iw = float(tx.get("img_width", 0))
                ih = float(tx.get("img_height", 0))
                minx = float(tx.get("min_x", 0))
                maxx = float(tx.get("max_x", 0))
                maxy = float(tx.get("max_y", 0))
                miny = float(tx.get("min_y", 0))
                ww = maxx - minx
                hw = maxy - miny
                if iw <= 0 or ih <= 0 or ww == 0 or hw == 0:
                    return None
                wx0, wy0, wx1, wy1 = wbb
                px0 = (wx0 - minx) / ww * iw
                py0 = (maxy - wy1) / hw * ih  # CAD top → PNG top
                px1 = (wx1 - minx) / ww * iw
                py1 = (maxy - wy0) / hw * ih
                return (min(px0, px1), min(py0, py1), abs(px1 - px0), abs(py1 - py0))
            except (TypeError, ValueError, ZeroDivisionError):
                return None

        after_px = _world_to_pixel(after_tx, world_bbox)
        before_px = _world_to_pixel(before_tx, world_bbox) or after_px
        if not after_px:
            return
        # The opacity defaults to 0.95 so the cloud/focus markers above
        # remain visible; the user can still see the change indicator
        # while inspecting the underlying vector geometry.
        if hasattr(self, "preview_after_v2") and after_px:
            self.preview_after_v2.set_vector_overlay(svg_path, *after_px, opacity=0.95)
        if hasattr(self, "preview_before_v2") and before_px:
            self.preview_before_v2.set_vector_overlay(svg_path, *before_px, opacity=0.95)

    def _open_svg_externally_v2(self, svg_path: str) -> None:
        """Open the rendered SVG in the OS default app (browser, Inkscape).

        Browsers render SVG natively with infinite zoom — exactly the
        commercialization core capability we want without rebuilding the
        QML viewer. Phase B2 follow-up may bring this inline as a layered
        QML Image with vector zoom; for now external open is enough to
        validate the underlying SVG quality."""

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        path = Path(svg_path)
        if not path.exists():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _select_zone_in_list_v2(self, zone_id: str) -> None:
        # Phase I2 — locate by zone_id across all leaves (tree-aware);
        # _select_zone_leaf_v2 auto-expands the parent category.
        leaf = self._find_zone_leaf_item_v2(zone_id)
        if leaf is not None:
            self._select_zone_leaf_v2(leaf)

    def _move_zone_selection_v2(self, delta: int) -> None:
        # Phase I2 — operate on flattened leaf list so prev/next still
        # behave like a flat list across category boundaries.
        leaf_items = self._zone_leaf_items_v2()
        count = len(leaf_items)
        if count <= 0:
            return
        current_item = self.zone_list_v2.currentItem()
        try:
            current_idx = leaf_items.index(current_item) if current_item in leaf_items else -1
        except ValueError:
            current_idx = -1
        if current_idx < 0:
            self._select_zone_leaf_v2(leaf_items[0])
            return
        next_idx = (current_idx + delta) % count
        self._select_zone_leaf_v2(leaf_items[next_idx])

    def _reset_zone_focus_v2(self) -> None:
        """Reset both viewports to fit the entire drawing.

        G2.7-COORDFIX — previously only the legacy GpuDrawingViewport's
        ``preview_before_v2`` / ``preview_after_v2`` got reset; the
        lightweight Qt PDF viewport (``preview_before_lightweight_v2`` /
        ``preview_after_lightweight_v2``) was ignored, so the button was
        a no-op when the lightweight viewer toggle was active. Now we
        reset whichever viewport is currently visible.
        """

        # Legacy viewport — fitInView on its scene rect
        for vp in (self.preview_before_v2, self.preview_after_v2):
            if vp is None:
                continue
            try:
                if vp.sceneRect().isValid():
                    vp.fitInView(vp.sceneRect(), Qt.KeepAspectRatio)
            except (AttributeError, RuntimeError):
                # GpuDrawingViewport may not expose sceneRect when QML
                # is in fallback mode; skip without crashing.
                pass
        # Lightweight viewport — call its QML fitToView() function
        for vp in (
            getattr(self, "preview_before_lightweight_v2", None),
            getattr(self, "preview_after_lightweight_v2", None),
        ):
            if vp is None:
                continue
            try:
                root = vp._quick.rootObject() if hasattr(vp, "_quick") else None
                if root is not None and hasattr(root, "fitToView"):
                    # invokeMethod for QML function call
                    from PySide6.QtCore import QMetaObject
                    QMetaObject.invokeMethod(root, "fitToView")
                elif root is not None:
                    # Fallback: call the Python helper if it exists
                    if hasattr(vp, "fit_to_view"):
                        vp.fit_to_view()
            except Exception as exc:
                logger.debug("Lightweight fitToView failed: %s", exc)

    def _zone_recommended_action_v2(
        self, change_type: str, severity: str, ai_category,
    ) -> tuple[str, str, str, str]:
        """Phase G3.2 — Compute the prominent action banner shown atop the
        detail panel.

        Returns ``(emoji, hex_bg_color, headline_ko, rationale_ko)``. The
        UI layer wraps these into an HTML banner so the reviewer sees the
        recommended next step at a glance instead of having to read the
        full detail block.

        Decision matrix (priority order):
          1. Structural-member changes → red/orange (immediate review)
          2. Grid changes → amber (coordinate impact)
          3. Dimension/text changes → blue (visual-only review)
          4. Detail markings → gray (low priority)
          5. Fallback: severity-based
        """

        from src.services.comparison.zone_classifier import (
            CATEGORY_DETAIL,
            CATEGORY_DIMENSION,
            CATEGORY_GRID,
            CATEGORY_STRUCTURAL_MEMBER,
        )

        ct = (change_type or "").lower()
        sev = (severity or "").lower()
        cat = ai_category.category if ai_category else ""

        # 1. Structural members — highest concern
        if cat == CATEGORY_STRUCTURAL_MEMBER:
            if "delete" in ct or "remove" in ct:
                return (
                    "🔴", "#DC2626", "즉시 확인 필요",
                    "구조 부재가 삭제되었습니다. 시공 안전·구조 강도 영향이 큽니다. 변경 사유와 대체 부재를 확인하세요.",
                )
            if "add" in ct:
                return (
                    "🔴", "#DC2626", "즉시 확인 필요",
                    "신규 구조 부재가 추가되었습니다. 시공 도면 + 자재 명세 + 관련 부재 정합성을 확인하세요.",
                )
            return (
                "🟠", "#F97316", "검토 권장",
                "구조 부재가 변경(이동/속성)되었습니다. 단면 크기 / 위치 / 연결 영향을 확인하세요.",
            )

        # 2. Grid — coordinate system impact
        if cat == CATEGORY_GRID:
            return (
                "🟡", "#F59E0B", "주의 검토",
                "그리드(축선) 변경입니다. 다른 부재의 위치 정합성과 도면 전체 좌표 일관성을 확인하세요.",
            )

        # 3. Dimension/annotation — usually visual-only
        if cat == CATEGORY_DIMENSION:
            return (
                "🔵", "#0969DA", "표기 검토",
                "치수/주석 변경입니다. 시공 영향은 적으나 도면 표기 정확성 / 단위 일관성은 확인하세요.",
            )

        # 4. Detail markings — usually noise
        if cat == CATEGORY_DETAIL:
            return (
                "⚪", "#6B7280", "낮은 우선순위",
                "상세 / 마킹 / 주석 표시 변경입니다. 시공 영향 미미. 누락 여부만 확인하면 충분합니다.",
            )

        # 5. Severity-based fallback (no AI category)
        if sev == "high":
            return (
                "🟠", "#F97316", "검토 권장",
                "심각도 높음 — 변경 범위와 영향을 확인하세요.",
            )
        if sev in ("medium", "med"):
            return (
                "🔵", "#0969DA", "검토 가능",
                "심각도 보통 — 영향 확인 후 결정하세요.",
            )
        return (
            "⚪", "#6B7280", "낮은 우선순위",
            "심각도 낮음 / 분류 미정 — 빠른 시각 확인만으로도 충분합니다.",
        )

    def _zone_detail_text_v2(self, zone_id: str) -> str:
        """Build the HTML detail panel. Phase G3.2 — added a coloured
        action banner at the top so reviewers see the recommended next
        step at a glance, before reading the technical details below.
        """

        from html import escape as _e

        issue = self._active_issue_by_zone.get(zone_id, {})
        overlay = self._active_overlays_by_zone.get(zone_id, {})
        data = {**overlay, **issue}
        change_type = str(data.get("change_type") or "").lower()
        severity_raw = str(data.get("severity") or "")
        counts = data.get("change_counts") or data.get("counts") or {}
        layers = data.get("top_layers") or data.get("layers") or data.get("layer") or "-"
        entities = data.get("entity_types") or data.get("top_entity_types") or "-"
        bbox = data.get("bbox") or data.get("after_bbox_px") or data.get("normalized_bbox") or "-"
        preview_status = RENDER_STATUS_LABELS.get(
            self._render_status_by_pair.get(str((self._active_row or {}).get("pair_id") or ""), "not_requested"),
            "렌더 대기",
        )
        pair_id = str((self._active_row or {}).get("pair_id") or "")
        review_status = self._review_status_for_zone_v2(pair_id, zone_id)
        reason = data.get("reason_ko") or data.get("priority_reason_ko") or data.get("reason") or "변경량과 위치 기준으로 우선 검토 대상으로 분류되었습니다."
        added = _int_value(counts.get("added") if isinstance(counts, dict) else data.get("added_count"))
        deleted = _int_value(counts.get("deleted") if isinstance(counts, dict) else data.get("deleted_count"))
        modified = _int_value(counts.get("modified") if isinstance(counts, dict) else data.get("modified_count"))
        moved = _int_value(counts.get("moved") if isinstance(counts, dict) else data.get("moved_count"))
        bbox_label = "PDF 페이지 중앙 핀(정확 좌표 없음)" if data.get("pdf_page_pin") else f"{bbox}"
        match_side_label = match_side_ko(change_type)
        # E2 — heuristic category line (AI 분류 결과)
        ai_category = self._zone_category_for(pair_id, zone_id)
        if ai_category is not None:
            category_block = (
                f"AI 분류: {_e(ai_category.category)} (신뢰도 {ai_category.confidence:.0%})\n"
                f"분류 근거: {_e(ai_category.rationale_ko)}\n"
            )
        else:
            category_block = ""
        natural = natural_change_summary(
            data,
            added=added,
            deleted=deleted,
            modified=modified,
            moved=moved,
            top_layers=layers if isinstance(layers, str) else "",
        )
        natural = str(data.get("change_summary_ko") or natural)
        pattern_group = str(data.get("pattern_group") or "")
        notice_lines = [
            "표시 방식: 큰 구름마크는 검토 영역, 현재 선택 변경점은 굵은 포커스 박스와 라벨로 따로 강조합니다.",
        ]
        if pattern_group:
            notice_lines.append(
                f"이 변경은 '{pattern_group}' 그룹에 속합니다 - 좌측 '반복 패턴' 탭에서 함께 보기"
            )
        if resolve_overlay_match_side(change_type) == "b_only":
            notice_lines.append(
                "추가 영역: 이전 도면에는 같은 요소가 없어 왼쪽 뷰가 비어 보일 수 있습니다."
            )
        elif resolve_overlay_match_side(change_type) == "a_only":
            notice_lines.append(
                "삭제 영역: 변경 도면에는 같은 요소가 없어 오른쪽 뷰가 비어 보일 수 있습니다."
            )
        notice_block = "\n".join(notice_lines) + "\n"

        # Phase G3.2 — recommended action banner
        emoji, bg_color, headline, rationale = self._zone_recommended_action_v2(
            change_type, severity_raw, ai_category,
        )

        # Phase G3.3 — review status overlay. Once the reviewer marks a
        # zone (확인 / 보류 / 오탐 / 추가검토) we surface that decision
        # *prominently* in the banner so they know at a glance what they
        # already decided about this zone (especially useful when scrolling
        # back through a long zone list).
        status_chip_html = ""
        review_status_lower = str(review_status or "").lower()
        review_chip_map = {
            "confirmed":      ("✓", "#16A34A", "확인됨"),
            "hold":           ("⏸", "#9CA3AF", "보류"),
            "false_positive": ("⊘", "#F97316", "오탐 표시"),
            "needs_review":   ("⊙", "#0969DA", "추가 검토"),
        }
        if review_status_lower in review_chip_map:
            chip_emoji, chip_color, chip_label = review_chip_map[review_status_lower]
            status_chip_html = (
                f'<div style="background-color:{chip_color}; color:#FFFFFF; '
                f'padding:6px 10px; margin:0 0 8px 0; border-radius:4px; '
                f'font-weight:bold; font-size:12px; display:inline-block;">'
                f'{_e(chip_emoji)} 검토 결과: {_e(chip_label)}'
                f'</div>'
            )

        # The detail body is plain text wrapped in <pre> so existing line
        # breaks survive the HTML transition without manual <br> insertion.
        body_text = (
            f"도면번호: {(self._active_row or {}).get('drawing_number') or '-'}\n"
            f"변경구역: {zone_id}\n"
            f"검토 상태: {self._review_status_ko_v2(review_status)}\n"
            f"변경 유형: {data.get('change_type_ko') or _ko_change_type(change_type)}\n"
            f"매칭 상태: {match_side_label}\n"
            f"요약: {natural}\n"
            f"{category_block}"
            f"설명: {self._change_type_explanation_v2(change_type)}\n\n"
            f"{notice_block}\n"
            f"+ 추가 {added} / - 삭제 {deleted} / ~ 수정 {modified} / 이동 {moved}\n"
            f"주요 layer: {layers}\n"
            f"entity type: {entities}\n"
            f"raw 변경 수: {_format_count(data.get('raw_change_count'))}\n"
            f"severity: {data.get('severity_ko') or data.get('severity') or '-'}\n"
            f"priority score: {float(data.get('priority_score') or 0.0):.1f}\n"
            f"우선검토 사유: {reason}\n\n"
            f"bbox/위치: {bbox_label}\n"
            f"preview 상태: {preview_status}"
        )

        # Phase G3.3 — keyboard hint footer. Surfaces the review hotkeys
        # the workbench has had since C1 but never advertised in the UI.
        # Users were confirming zones via mouse clicks even though 1/2/3/4
        # have been wired up the whole time.
        hotkey_hint_html = (
            '<div style="margin:8px 0 0 0; padding:6px 8px; '
            'background-color:#F3F4F6; color:#4B5563; font-size:11px; '
            'border-radius:3px; border-left:3px solid #9CA3AF;">'
            '⌨️ 단축키: '
            '<b>1</b>=확인 · <b>2</b>=보류 · <b>3</b>=오탐 · <b>4</b>=추가검토 '
            '· <b>J/↓</b>=다음 zone · <b>K/↑</b>=이전 zone '
            '· <b>R</b>=현재 zone 다시 보기'
            '</div>'
        )

        # Build the HTML. Banner uses inline styles since QTextEdit's
        # built-in style sheet support varies. Body kept as <pre> so all
        # the existing whitespace remains predictable.
        banner_html = (
            f'<div style="background-color:{bg_color}; color:#FFFFFF; '
            f'padding:8px 12px; margin:0 0 8px 0; border-radius:4px; '
            f'font-weight:bold; font-size:13px;">'
            f'{_e(emoji)} {_e(headline)}'
            f'</div>'
            f'<div style="margin:0 0 12px 0; padding:0 4px; color:#374151; '
            f'font-size:12px;">{_e(rationale)}</div>'
            f'{status_chip_html}'
        )
        body_html = (
            f'<pre style="margin:0; font-family:Consolas,monospace; '
            f'font-size:12px; color:#111827; white-space:pre-wrap;">'
            f'{_e(body_text)}'
            f'</pre>'
        )
        return banner_html + body_html + hotkey_hint_html

    def _change_type_explanation_v2(self, change_type: str) -> str:
        normalized = str(change_type or "").lower()
        if "add" in normalized:
            return "변경 후 도면에 새 요소가 생겼습니다."
        if "delete" in normalized or "remove" in normalized:
            return "변경 전 도면에 있던 요소가 사라졌습니다."
        if "move" in normalized:
            return "요소가 기존 위치에서 다른 위치로 이동한 것으로 추정됩니다."
        if "mod" in normalized:
            return "같은 위치 또는 가까운 위치의 요소 속성/형상이 달라졌습니다."
        if "mixed" in normalized:
            return "추가/삭제/수정이 같은 구역에 함께 발생했습니다."
        return "변경 유형이 혼합되어 있어 전후 위치와 layer를 함께 확인해야 합니다."

    def _open_marked_dxf_v2(self) -> None:
        if self._active_row and self._active_row.get("after_marked_dxf"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._active_row["after_marked_dxf"]).resolve())))

    def _open_executive_v2(self) -> None:
        if self._result:
            path = self._result.executive_package.output_paths.get("executive_review_html")
            if path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def _open_priority_csv_v2(self) -> None:
        if not self._result:
            return
        path = (
            self._result.executive_package.output_paths.get("review_priority_csv")
            or self._result.artifact_package.output_paths.get("review_priority_csv")
        )
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def _open_viewer_v2(self) -> None:
        if not self._result:
            return
        path = ""
        if getattr(self._result, "viewer_package", None):
            path = self._result.viewer_package.output_paths.get("viewer_index_html", "")
        if not path:
            path = self._result.artifact_package.output_paths.get("viewer_index_html", "")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def _open_artifact_dir_v2(self) -> None:
        if self._result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._result.artifact_dir))

    def _open_source_external_v2(self) -> None:
        """Phase F P0 escape hatch — open the original DWG/DXF/PDF in the
        user's default viewer.

        The current rasterised preview cannot match the readability of the
        user's CAD viewer (AutoCAD, BricsCAD, Adobe Acrobat) on dense
        structural sheets. Rather than ship a half-broken in-app renderer,
        give the reviewer a one-click path to read the actual file at full
        fidelity. Tries the after (B) source first, then the before (A)
        source, then warns if neither is reachable.
        """

        if not self._active_row:
            QMessageBox.information(
                self, "원본 도면 열기",
                "먼저 좌측에서 도면을 선택하세요."
            )
            return
        candidates: list[Path] = []
        for key in ("after_source_path", "after_path", "after_marked_dxf",
                    "before_source_path", "before_path"):
            raw = self._active_row.get(key)
            if not raw:
                continue
            try:
                p = Path(str(raw))
            except Exception:
                continue
            if p.exists():
                candidates.append(p)
        # Fallback to the viewer manifest pair entry which carries source_a/b
        pair_id = str(self._active_row.get("pair_id") or "")
        if not candidates and pair_id:
            pair = self._viewer_pairs_by_id.get(pair_id, {})
            for key in ("source_b", "source_a"):
                raw = pair.get(key)
                if not raw:
                    continue
                try:
                    p = Path(str(raw))
                except Exception:
                    continue
                if p.exists():
                    candidates.append(p)
        if not candidates:
            QMessageBox.warning(
                self, "원본 도면 열기",
                "원본 파일 경로를 찾을 수 없거나, 파일이 이동/삭제된 것 같습니다.\n"
                "도면 행의 source_a/source_b 경로를 확인해 주세요."
            )
            return
        target = candidates[0]
        try:
            opened = QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(target.resolve()))
            )
        except Exception as exc:
            opened = False
            logger.exception("Failed to open source via QDesktopServices: %s", exc)
        if not opened:
            QMessageBox.warning(
                self, "원본 도면 열기",
                f"기본 연결 프로그램으로 파일을 열지 못했습니다:\n{target}\n\n"
                "DWG/DXF는 AutoCAD/BricsCAD, PDF는 Acrobat/Edge 등의 기본 프로그램이 필요합니다."
            )

    def _show_viewer_perf_dialog_v2(self) -> None:
        """Show a read-only dialog with the parsed ``viewer_perf.json`` summary.

        Re-summarizes on each open so users see the latest data after recent zone
        crops. The dialog is intentionally minimal — a JSON dump in monospace —
        because customers care about the headline numbers, not full event drill-downs.
        """

        summary = summarize_viewer_perf(self._viewer_root)
        self._viewer_perf_summary = summary
        self.lbl_viewer_perf_v2.setText(format_viewer_perf_summary_korean(summary))
        body_lines = [format_viewer_perf_summary_korean(summary), ""]
        body_lines.append(json.dumps(summary, ensure_ascii=False, indent=2))
        if self._viewer_root:
            body_lines.extend(
                [
                    "",
                    f"원본 로그(JSONL): {self._viewer_root / 'viewer_perf.jsonl'}",
                    f"호환 인덱스(JSON): {self._viewer_root / 'viewer_perf.json'}",
                ]
            )
        QMessageBox.information(self, "성능 진단", "\n".join(body_lines))

    def _show_suppression_audit_v2(self) -> None:
        """Phase Q2 (RV-20260509-002) — show every silent-drop counter so
        the reviewer can answer "왜 이 변경이 안 보이나요?".

        Strategy:
        - If a row is selected, focus on that pair (most actionable).
        - Otherwise aggregate across the whole batch so the reviewer sees the
          overall noise picture.
        """

        if not self._result:
            QMessageBox.information(
                self, "변경 가시성 진단",
                "비교 실행 후 사용 가능합니다."
            )
            return

        active_pair_id = str((self._active_row or {}).get("pair_id") or "")
        items = list(self._result.compare_summary.items)
        target_item = None
        if active_pair_id:
            for it in items:
                cand = getattr(it, "candidate", None)
                if cand and getattr(cand, "pair_id", "") == active_pair_id:
                    target_item = it
                    break

        sections: list[str] = []
        if target_item is not None and target_item.result is not None:
            cand = target_item.candidate
            label = getattr(cand, "pair_id", "") or active_pair_id
            report = audit_from_comparison_result(
                target_item.result, pair_id=label,
            )
            sections.append(f"=== {label} (선택된 도면) ===")
            sections.append(report.format_text())
        else:
            agg_report = self._build_aggregate_suppression_audit_v2(items)
            sections.append("=== 전체 비교 결과 (집계) ===")
            sections.append(agg_report.format_text())
            sections.append("")
            sections.append(
                "특정 도면 선택 후 다시 열면 해당 도면의 상세 진단이 표시됩니다."
            )

        body = "\n".join(sections)
        QMessageBox.information(self, "🔍 변경 가시성 진단", body)

    def _build_aggregate_suppression_audit_v2(
        self, items: list,
    ) -> SuppressionAuditReport:
        """Sum all per-pair counters into a single ``SuppressionAuditReport``.

        We rebuild via ``build_suppression_audit`` rather than concatenating
        entries so the resulting report keeps the canonical 4-stage layout
        (extraction / comparison / zone / result) and unified labels.
        """
        agg_extract_a: dict[str, int] = {}
        agg_extract_b: dict[str, int] = {}
        agg_extract_a_total = 0
        agg_extract_b_total = 0
        # Phase Q2 Codex follow-up (RV-20260509-002): track A/B limit flags
        # independently — the aggregate previously OR'd both into A only.
        agg_a_limit_exceeded = False
        agg_b_limit_exceeded = False
        agg_a_max_entities = 0
        agg_b_max_entities = 0

        agg_comp_stats: dict[str, int] = {
            "modified_ignored": 0,
            "alignment_suppressed": 0,
            "cosmetic_suppressed": 0,
        }
        agg_meta: dict[str, Any] = {
            "alignment_suppressed_count": 0,
            "change_zone_noise_suppressed_count": 0,
            "change_zone_skipped_record_count": 0,
            "truncated_changes": False,
            # Phase Q2 Codex round-2 follow-up — sum omitted_change_counts
            # so the aggregate truncation entry reports actual omissions
            # instead of falling back to sentinel=1.
            "omitted_change_counts": {"added": 0, "deleted": 0, "modified": 0},
            "max_change_records_in_memory": 0,
        }

        total_visible = 0

        for it in items:
            if it.result is None:
                continue
            res = it.result
            metadata = getattr(res, "metadata", {}) or {}
            stats = getattr(res, "stats", {}) or {}
            ext = metadata.get("extraction_stats") or {}
            ea = ext.get("a") or {}
            eb = ext.get("b") or {}
            for k, v in (ea.get("unsupported_counts") or {}).items():
                agg_extract_a[k] = agg_extract_a.get(k, 0) + int(v)
            for k, v in (eb.get("unsupported_counts") or {}).items():
                agg_extract_b[k] = agg_extract_b.get(k, 0) + int(v)
            agg_extract_a_total += int(ea.get("unsupported_total", 0))
            agg_extract_b_total += int(eb.get("unsupported_total", 0))
            if ea.get("limit_exceeded"):
                agg_a_limit_exceeded = True
                agg_a_max_entities = max(
                    agg_a_max_entities, int(ea.get("max_entities", 0) or 0)
                )
            if eb.get("limit_exceeded"):
                agg_b_limit_exceeded = True
                agg_b_max_entities = max(
                    agg_b_max_entities, int(eb.get("max_entities", 0) or 0)
                )

            # Phase Q2 Codex round-2 follow-up (RV-20260509-002):
            # ComparisonResult (DwgDiffer) has no .stats — the comparator
            # counters are surfaced into metadata["comparison_suppression"]
            # instead. Pull from both sources so DXF (.stats present) and
            # CAD batch (.stats absent) paths both aggregate correctly.
            comp_suppress = metadata.get("comparison_suppression") or {}
            for k in agg_comp_stats:
                from_stats = int(stats.get(k, 0) or 0)
                from_meta = (
                    int(comp_suppress.get(k, 0) or 0)
                    if isinstance(comp_suppress, Mapping)
                    else 0
                )
                # max() avoids double-count when DwgDiffer surfaces the
                # same value to both .stats (test stub) and metadata.
                agg_comp_stats[k] += max(from_stats, from_meta)
            agg_meta["alignment_suppressed_count"] = (
                int(agg_meta["alignment_suppressed_count"])
                + int(metadata.get("alignment_suppressed_count", 0) or 0)
            )
            agg_meta["change_zone_noise_suppressed_count"] = (
                int(agg_meta["change_zone_noise_suppressed_count"])
                + int(metadata.get("change_zone_noise_suppressed_count", 0) or 0)
            )
            agg_meta["change_zone_skipped_record_count"] = (
                int(agg_meta["change_zone_skipped_record_count"])
                + int(metadata.get("change_zone_skipped_record_count", 0) or 0)
            )
            if metadata.get("truncated_changes") or stats.get("truncated_changes"):
                agg_meta["truncated_changes"] = True
                # Sum actual omitted records so the aggregate truncation
                # entry shows real hidden count, not the sentinel=1
                # fallback.
                omitted = (
                    metadata.get("omitted_change_counts")
                    or stats.get("omitted_change_counts")
                    or {}
                )
                if isinstance(omitted, Mapping):
                    for k in ("added", "deleted", "modified"):
                        agg_meta["omitted_change_counts"][k] = int(
                            agg_meta["omitted_change_counts"].get(k, 0)
                        ) + int(omitted.get(k, 0) or 0)
                cap = int(
                    metadata.get("max_change_records_in_memory")
                    or stats.get("max_change_records_in_memory")
                    or 0
                )
                if cap and cap > int(agg_meta["max_change_records_in_memory"]):
                    agg_meta["max_change_records_in_memory"] = cap

            changes = getattr(res, "changes", None) or []
            try:
                total_visible += len(changes)
            except TypeError:
                pass

        ext_a_payload: dict = {
            "unsupported_counts": agg_extract_a,
            "unsupported_total": agg_extract_a_total,
        }
        ext_b_payload: dict = {
            "unsupported_counts": agg_extract_b,
            "unsupported_total": agg_extract_b_total,
        }
        if agg_a_limit_exceeded:
            ext_a_payload["limit_exceeded"] = True
            ext_a_payload["max_entities"] = agg_a_max_entities
        if agg_b_limit_exceeded:
            ext_b_payload["limit_exceeded"] = True
            ext_b_payload["max_entities"] = agg_b_max_entities
        return build_suppression_audit(
            extraction_stats_a=ext_a_payload,
            extraction_stats_b=ext_b_payload,
            comparison_stats=agg_comp_stats,
            comparison_metadata=agg_meta,
            visible_change_count=total_visible,
            pair_id="(전체 집계)",
        )

    def _show_about_v2(self) -> None:
        QMessageBox.information(
            self,
            "정보",
            f"{APP_TITLE_KO}\n\n"
            f"{APP_OWNERSHIP_KO}\n"
            "용도: 내부 Pilot용 도면 변경 검토 Workbench\n\n"
            f"DXF cache:\n{self._dxf_cache_dir}",
        )

    def closeEvent(self, event) -> None:
        if not self._stop_background_threads_for_close_v2():
            event.ignore()
            if hasattr(self, "lbl_status_v2"):
                self.lbl_status_v2.setText(
                    "Background compare/render is still stopping. Please wait and close again."
                )
            logger.warning("Close ignored because a background QThread is still running")
            return
        self._zone_render_controller_v2.shutdown()
        # Phase G2.1 — clean up the ViewerSession executor so its worker
        # threads don't outlive the QApplication and corrupt the next
        # session's cache files.
        session = getattr(self, "_viewer_session", None)
        if session is not None:
            try:
                session.shutdown(wait=True, timeout=5.0)
            except Exception:
                logger.exception("ViewerSession shutdown raised")
        super().closeEvent(event)

    def _region_match_artifact_paths_v2(self) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
        """Resolve region-aware review artifacts for the current run."""

        if not self._result:
            return None, None, None
        artifact_dir_value = getattr(self._result, "artifact_dir", "")
        artifact_dir = Path(str(artifact_dir_value)) if artifact_dir_value else None
        output_dir_value = getattr(self._result, "output_dir", "")
        output_dir = (
            Path(str(output_dir_value))
            if output_dir_value
            else artifact_dir.parent if artifact_dir is not None else None
        )
        artifact_package = getattr(self._result, "artifact_package", None)
        output_paths = getattr(artifact_package, "output_paths", {}) or {}

        def from_output_paths(key: str, fallback_name: str) -> Optional[Path]:
            value = output_paths.get(key) if isinstance(output_paths, dict) else ""
            if value:
                return Path(str(value))
            if artifact_dir is None:
                return None
            return artifact_dir / fallback_name

        detection_path = from_output_paths(
            "region_detection_summary_json",
            "region_detection_summary.json",
        )
        match_path = from_output_paths(
            "region_match_summary_json",
            "region_match_summary.json",
        )
        manual_value = (
            output_paths.get("manual_region_matches_json")
            if isinstance(output_paths, dict)
            else ""
        )
        manual_path = (
            Path(str(manual_value))
            if manual_value
            else output_dir / "manual_region_matches.json" if output_dir is not None else None
        )
        return detection_path, match_path, manual_path

    def _manual_region_matches_path_v2(self) -> Optional[Path]:
        """Path used by the region review dialog and the next compare run."""

        _detection_path, _match_path, manual_path = self._region_match_artifact_paths_v2()
        return manual_path

    def _show_region_match_dialog_v2(self) -> None:
        if not self._result:
            return
        detection_path, match_path, manual_path = self._region_match_artifact_paths_v2()
        missing = [
            str(path)
            for path in (detection_path, match_path)
            if path is None or not path.exists()
        ]
        if missing or manual_path is None:
            QMessageBox.warning(
                self,
                "Detail Region Matching",
                "Region-aware artifacts are not available for this run.\n\n"
                "Expected region_detection_summary.json and region_match_summary.json.\n"
                "Enable multi-detail region detection and run comparison again.\n\n"
                f"Missing: {', '.join(missing) if missing else 'manual_region_matches.json path'}",
            )
            return
        try:
            from src.gui.region_match_dialog import RegionMatchReviewDialog

            dialog = RegionMatchReviewDialog(
                artifact_dir=detection_path.parent,
                overrides_path=manual_path,
                parent=self,
            )
            dialog.exec()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to open detail region matching dialog")
            QMessageBox.critical(
                self,
                "Detail Region Matching",
                f"Failed to open region match review dialog:\n{exc}",
            )

    def _show_matching_detail_v2(self) -> None:
        if not self._result:
            return
        QMessageBox.information(
            self,
            "상세 매칭",
            "자동 매칭 결과\n\n"
            f"비교 실행: {self._result.confirmed_pairs}쌍\n"
            f"검토 필요: {self._result.review_required_pairs}쌍\n"
            f"변경 전만 있음: {self._result.unmatched_a}개\n"
            f"변경 후만 있음: {self._result.unmatched_b}개\n\n"
            "검토 필요 항목은 잘못된 자동 비교를 막기 위해 이번 비교에서 제외했습니다.",
        )


def _consume_smoke_exit_ms(argv: list[str]) -> Optional[int]:
    flag = "--smoke-exit-ms"
    value: Optional[str] = None
    if flag in argv:
        index = argv.index(flag)
        argv.pop(index)
        if index < len(argv):
            value = argv.pop(index)
    value = value or os.environ.get("DRAWING_COMPARE_SMOKE_EXIT_MS")
    if not value:
        return None
    try:
        return max(100, int(value))
    except ValueError:
        return 1000


def main() -> int:
    smoke_exit_ms = _consume_smoke_exit_ms(sys.argv)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE_KO)
    app.setOrganizationName("센엔지니어링 그룹 AI 동아리")
    app_icon = QIcon(str(_drawing_compare_asset_path("app_icon.ico")))
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    window = DrawingCompareWorkbenchV2()
    window.show()
    if smoke_exit_ms is not None:
        QTimer.singleShot(smoke_exit_ms, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
