# -*- coding: utf-8 -*-
"""Audit Drawing Compare Workbench MVP exit evidence.

This script does not run comparisons. It inspects one or more completed
validation output folders and an optional release manifest, then maps the
customer MVP requirements to concrete artifacts. It returns a non-zero exit code
when any required exit criterion is missing.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from src.services.comparison.run_contract import validate_run_completion
    from src.services.comparison.viewer_perf_summary import summarize_viewer_perf
except ImportError:
    def validate_run_completion(
        run_manifest_path: str | None,
        success_sentinel_path: str | None,
    ) -> dict[str, Any]:
        sentinel = Path(success_sentinel_path or "")
        manifest = Path(run_manifest_path or "")
        if not sentinel.exists():
            return {"valid": False, "status": "missing_sentinel", "run_id": ""}
        try:
            sentinel_payload = json.loads(sentinel.read_text(encoding="utf-8"))
        except Exception:
            return {"valid": False, "status": "missing_sentinel", "run_id": ""}
        sentinel_run_id = str(sentinel_payload.get("run_id") or "")
        if not manifest.exists():
            return {"valid": False, "status": "manifest_missing", "run_id": sentinel_run_id}
        try:
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            return {"valid": False, "status": "manifest_unreadable", "run_id": sentinel_run_id}
        manifest_run_id = str(manifest_payload.get("run_id") or "")
        if sentinel_run_id and manifest_run_id and sentinel_run_id != manifest_run_id:
            return {"valid": False, "status": "run_id_mismatch", "run_id": sentinel_run_id}
        return {"valid": True, "status": "ok", "run_id": sentinel_run_id or manifest_run_id}

    def summarize_viewer_perf(viewer_root: Path | None) -> dict[str, Any]:
        summary = _empty_perf_summary()
        if viewer_root is None:
            return summary
        path = Path(viewer_root) / "viewer_perf.json"
        if not path.exists():
            return summary
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return summary
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list) or not events:
            summary["status"] = "empty"
            return summary
        zone_events = [event for event in events if isinstance(event, dict) and event.get("event") == "zone_crop_render"]
        summary["status"] = "ready" if zone_events else "package_only"
        summary["event_count"] = len(events)
        summary["zone_crop_count"] = len(zone_events)
        if zone_events:
            hit = [event for event in zone_events if _to_bool_local(event.get("cache_hit"))]
            cold = [event for event in zone_events if not _to_bool_local(event.get("cache_hit"))]
            all_ms = [_to_float_local(event.get("render_ms")) for event in zone_events if event.get("render_ms") is not None]
            hit_ms = [_to_float_local(event.get("render_ms")) for event in hit if event.get("render_ms") is not None]
            cold_ms = [_to_float_local(event.get("render_ms")) for event in cold if event.get("render_ms") is not None]
            summary["zone_crop_cache_hit_rate"] = round(len(hit) / len(zone_events), 4)
            summary["render_lifecycle_counts"] = _count_values_local(
                event.get("render_lifecycle") for event in zone_events
            )
            summary["fidelity_counts"] = _count_values_local(
                event.get("visual_fidelity") for event in zone_events
            )
            summary["renderer_backend_counts"] = _count_values_local(
                event.get("renderer_backend") for event in zone_events
            )
            summary["reason_code_counts"] = _count_values_local(
                event.get("reason_code") for event in zone_events
            )
            if all_ms:
                summary["zone_crop_ms"] = _percentile_summary_local([value for value in all_ms if value >= 0])
            if hit_ms:
                summary["zone_crop_cache_hit_ms"] = _percentile_summary_local([value for value in hit_ms if value >= 0])
            if cold_ms:
                summary["zone_crop_cold_ms"] = _percentile_summary_local([value for value in cold_ms if value >= 0])
        return summary

    def _empty_perf_summary() -> dict[str, Any]:
        empty_latency = {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0}
        return {
            "schema_version": 1,
            "status": "missing",
            "event_count": 0,
            "zone_crop_count": 0,
            "zone_crop_cache_hit_rate": 0.0,
            "zone_crop_ms": dict(empty_latency),
            "zone_crop_cold_ms": dict(empty_latency),
            "zone_crop_cache_hit_ms": dict(empty_latency),
            "render_lifecycle_counts": {},
            "fidelity_counts": {},
            "renderer_backend_counts": {},
            "reason_code_counts": {},
        }

    def _percentile_summary_local(values: Iterable[float]) -> dict[str, float]:
        samples = sorted(float(value) for value in values)
        if not samples:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0}
        return {
            "p50": round(_percentile_local(samples, 0.50), 3),
            "p95": round(_percentile_local(samples, 0.95), 3),
            "p99": round(_percentile_local(samples, 0.99), 3),
            "mean": round(sum(samples) / len(samples), 3),
        }

    def _percentile_local(samples: list[float], q: float) -> float:
        if not samples:
            return 0.0
        if len(samples) == 1:
            return samples[0]
        rank = q * (len(samples) - 1)
        lower = int(math.floor(rank))
        upper = int(math.ceil(rank))
        if lower == upper:
            return samples[lower]
        return samples[lower] + (samples[upper] - samples[lower]) * (rank - lower)

    def _to_float_local(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _to_bool_local(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "y", "hit"}

    def _count_values_local(values: Iterable[Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for value in values:
            key = str(value or "").strip()
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
        return counts


# Plan §17 F6 (GPT Pro deep-research review) — manifest integrity verifier.
# Released-tree bundles that ship the scripts without ``src/`` will fail
# this import; the audit gate then reports the absence as a customer-grade
# failure ("manifest_provenance helper unavailable"), which is the intended
# behaviour for stripped bundles where the auditor cannot verify integrity.
try:
    from src.services.comparison.manifest_provenance import (
        verify_manifest_integrity as _verify_manifest_integrity,
    )
    _MANIFEST_PROVENANCE_AVAILABLE = True
except ImportError:
    _MANIFEST_PROVENANCE_AVAILABLE = False
    _verify_manifest_integrity = None  # type: ignore[assignment]


REQUIRED_QUEUE_FIELDS = {
    "pair_uuid",
    "zone_id",
    "drawing_label",
    "category",
    "priority_score",
    "reason_ko",
    "change_summary_ko",
    "source_format",
    "detection_source",
    "bbox_status",
    "review_status",
}
SOURCE_FORMAT_VALUES = {"cad", "pdf"}
REVIEW_STATUS_VALUES = {"needs_review", "confirmed", "false_positive", "hold"}
BBOX_STATUS_VALUES = {"exact", "page_fallback", "relative_only"}
CAD_INPUT_EXTENSIONS = {"dwg", "dxf"}
PDF_INPUT_EXTENSIONS = {"pdf"}

PDF_DETECTION_SOURCES = {"pdf_text", "pdf_ocr", "pdf_visual", "hybrid"}
CAD_DETECTION_SOURCES = {"cad_entity"}
CAD_STRUCTURAL_TEXT_ENTITY_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "INSERT"}
REQUIRED_REVIEW_GROUND_TRUTH_COLUMNS = (
    "drawing_label",
    "category",
    "summary_contains",
    "source_format",
    "detection_source",
    "bbox_status",
)
REQUIRED_REVIEW_DECISION_TRUTH_COLUMNS = (
    "pair_uuid",
    "zone_id",
    "drawing_label",
    "structural_bucket",
    "human_label",
    "source_format",
    "detection_source",
    "bbox_status",
    "notes",
)
REVIEW_DECISION_LABEL_VALUES = {"true_positive", "false_positive", "hold"}
REQUIRED_DATASET_STRATA_COLUMNS = (
    "pair_uuid",
    "drawing_label",
    "format_pair",
    "sheet_type",
    "risk_class",
    "large_dwg",
    "block_text_case",
    "negative_control",
    "notes",
)
FORMAT_PAIR_VALUES = {"dwg_dxf", "pdf_pdf", "cad_pdf_blocked"}
SHEET_TYPE_VALUES = {"plan", "section", "detail", "schedule_like"}
RASTER_LOW_QUALITY_RISK_VALUES = {
    "raster",
    "raster_pdf",
    "low_quality",
    "low-quality",
    "scan",
    "scanned",
    "scanned_pdf",
}
DISALLOWED_REVIEW_GROUND_TRUTH_MARKERS = ("example", "sample", "template")

REQUIRED_PREFLIGHT_CHECKS = {
    "source_a",
    "source_b",
    "output_dir",
    "dxf_cache_dir",
    "compare_state_dir",
    "output_dir_outside_inputs",
    "dxf_cache_dir_outside_inputs",
    "compare_state_dir_outside_inputs",
    "disk_space",
    "temp_dir",
    "windows_long_path",
    "rtree",
    "oda_converter",
    "pymupdf",
    "pdf_support",
    "font_support",
    "preview_dependencies",
}

CUSTOMER_GRADE_SOURCE_KINDS = {"customer", "customer_grade"}
APPROVED_DATASET_STATUS = "approved_for_mvp_exit"
CONFIRMED_EXPORT_SUFFIXES = {".png", ".pdf", ".dxf"}
DISALLOWED_EVIDENCE_PATH_MARKERS = (
    "template",
    "quick_reference",
    "quick-reference",
    "operator_handoff",
    "operator-handoff",
    "pilot_ops_operator_handoff",
    "closeout",
    "customer_evidence_closeout_packet",
    "customer_evidence_request",
)
REQUIRED_OPERATOR_WORKFLOW_CHECKS = (
    "input_selection",
    "automatic_compare_completed",
    "top_structural_review_queue_seen",
    "selected_zone_before_after_sync_zoom",
    "korean_reason_summary_reviewed",
    "confirmed_false_positive_hold_used",
    "confirmed_only_export_checked",
    "sharable_path_leakage_checked",
)
MIN_OPERATOR_DRY_RUN_NOTE_CHARS = 30
OPERATOR_DRY_RUN_NOTE_BOILERPLATE_MARKERS = (
    "fill this out",
    "keep the check ids unchanged",
    "operator dry run passed",
    "operator dry-run passed",
    "operator notes",
    "replace this line",
    "template",
    "sample",
    "example",
)
APPROVED_OPERATOR_REVIEWER_ROLES = {
    "structural_review_lead",
    "structural_team_lead",
    "structural_review_manager",
    "structural_checker_lead",
    "구조검토책임자",
    "구조검토팀장",
    "구조도면검토책임자",
    "구조도면검토팀장",
    "구조_검토_책임자",
    "구조_검토_팀장",
    "구조_도면_검토_책임자",
    "구조_도면_검토_팀장",
}
REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS = {
    "5.": "review_queue first-screen Top issues",
    "8.": "confirmed-only cloud export",
    "8b.": "hold/false_positive excluded from confirmed-only export",
    "9b.": "selected-zone Before/After synchronized focus/window",
    "9c.": "selected-zone render subprocess timeout and responsive UI loop",
    "10.": "confirmed-only review report and path audit",
}
REQUIRED_PROMPT_TO_ARTIFACT_CHECKLIST_TERMS = (
    "DWG/DXF comparison supported",
    "PDF-PDF comparison supported",
    "CAD-PDF cross comparison blocked",
    "input_selection",
    "automatic_compare_completed",
    "review_queue.mode=structural_core",
    "expand_blocks=False",
    "customer_evidence_manifest.json",
    "--evidence-level customer_grade",
    "confirmed-only",
    "sharable",
    "selected-zone",
    "preflight",
    "large_dwg_performance_probe",
    "--large-dwg-probe",
    "customer_evidence_manifest_summaries",
    "diagnostics.customer_evidence_manifests_not_ready",
    "diagnostics.customer_evidence_manifests_missing_approved_ground_truth",
)
REQUIRED_CUSTOMER_EVIDENCE_REQUEST_KO_TERMS = (
    "Drawing Compare 고객급 증거 요청서",
    "review_ground_truth.csv",
    "review_decision_truth.csv",
    "dataset_strata.csv",
    "operator_dry_run_notes.md",
    "customer_grade",
    "status=passed",
)
DEFAULT_MAX_LARGE_DWG_ELAPSED_S = 120.0
DEFAULT_MIN_LARGE_DWG_CHANGE_RECORDS = 100_000
DEFAULT_MAX_LARGE_DWG_IN_MEMORY_RECORDS = 50_000
DEFAULT_MIN_LARGE_DWG_PROGRESS_EVENTS = 5
DEFAULT_MIN_REVIEW_DECISION_ROWS = 20
DEFAULT_MIN_REVIEW_DECISION_OVERALL_PRECISION = 0.85
DEFAULT_MIN_REVIEW_DECISION_BUCKET_PRECISION = 0.75
DEFAULT_MAX_REVIEW_DECISION_FALSE_POSITIVE_RATE = 0.15
DEFAULT_MIN_REVIEW_DECISION_BUCKET_ROWS = 2
DEFAULT_MIN_DATASET_STRATA_CAD_ROWS = 8
DEFAULT_MIN_DATASET_STRATA_PDF_ROWS = 8
DEFAULT_MIN_DATASET_STRATA_RASTER_ROWS = 2
DEFAULT_MIN_DATASET_STRATA_LARGE_DWG_ROWS = 2
DEFAULT_MIN_DATASET_STRATA_BLOCK_TEXT_ROWS = 2
DEFAULT_MIN_DATASET_STRATA_SHEET_TYPE_ROWS = 2
DEFAULT_MIN_DATASET_STRATA_NEGATIVE_CONTROL_ROWS = 2
DEFAULT_MAX_REVIEW_DASHBOARD_READY_S = 600.0
DEFAULT_MAX_SPEED_REVIEW_DASHBOARD_READY_S = 300.0
DEFAULT_MAX_FIRST_TOP_ISSUE_READY_S = 600.0
DEFAULT_MAX_VIEWER_METADATA_READY_S = 900.0
DEFAULT_MAX_LARGE_DWG_PEAK_RSS_MB = 4096.0
DEFAULT_MAX_LARGE_DWG_PROGRESS_GAP_S = 10.0
DEFAULT_MAX_LARGE_DWG_CANCEL_TO_IDLE_S = 10.0
DEFAULT_MAX_BBOX_RELATIVE_ONLY_RATIO = 0.10
DEFAULT_MAX_BBOX_PAGE_FALLBACK_RATIO = 0.30

# Plan §17 Phase B-5 (GPT Pro F3 follow-up) -- tightened thresholds
# enabled via --strict-zone-render-budget. The reviewer's
# recommendation is cold p95 <= 2000 ms and cache-hit p95 <= 500 ms.
# Default (10000/2000) preserved for one release so existing pipelines
# don't fail; a deprecation warning fires when those defaults are in
# use. Phase B-2 (PyMuPDF DisplayList) + B-3 (DXF pre-filter) + B-4
# (prefetch) provide the engine needed to meet the strict gate.
STRICT_MAX_COLD_ZONE_RENDER_MS = 2000.0
STRICT_MAX_CACHE_HIT_ZONE_RENDER_MS = 500.0
# Plan §18 A-1 (GPT Pro F2/F5 follow-up) — customer_grade auto-default
# constants. When evidence_level=customer_grade, run_audit promotes
# these to active gates so a customer running the audit with default
# flags actually gets enforcement (not just help-text recommendations).
STRICT_REQUIRE_PRECISION_THRESHOLD = 0.85
STRICT_REQUIRE_BURDEN_THRESHOLD = 2.0
STRICT_MAX_PEAK_RSS_MB = 2048.0
STRICT_MAX_PERF_SUMMARY_ELAPSED_MS = 1000.0
STRICT_MAX_PROCESS_HANDLE_POSITIVE_DELTA = 32
STRICT_MAX_OPEN_FILE_DESCRIPTOR_POSITIVE_DELTA = 32
STRICT_MAX_GDI_HANDLE_POSITIVE_DELTA = 16
STRICT_MAX_USER_HANDLE_POSITIVE_DELTA = 16
STRICT_MAX_WORKER_PROCESS_POSITIVE_DELTA = 0
STRICT_MAX_FINAL_WORKER_PROCESS_COUNT = 0
REQUIRED_CUSTOMER_PACKAGE_CONTENTS = (
    "app/DrawingCompareWorkbench/DrawingCompareWorkbench.exe",
    "cli/audit_drawing_compare_mvp_exit.py",
    "cli/prepare_drawing_compare_customer_evidence.py",
    "cli/inventory_drawing_compare_customer_evidence.py",
    "README_INTERNAL_PILOT.md",
    "mvp_exit_prompt_to_artifact_checklist.md",
    "operator_dry_run_checklist_template.md",
    "review_ground_truth_template.csv",
    "review_decision_truth_template.csv",
    "dataset_strata_template.csv",
    "customer_evidence_closeout_packet.md",
    "customer_evidence_request_ko.md",
    "customer_package_manifest.json",
    "customer_package_path_audit.json",
)
DISALLOWED_CUSTOMER_PACKAGE_ENTRY_SUFFIXES = (".pyc", ".pyo")
DISALLOWED_CUSTOMER_PACKAGE_ENTRY_PARTS = {"__pycache__"}
CUSTOMER_PACKAGE_TEXT_SUFFIXES = {".csv", ".json", ".md", ".ps1", ".py", ".txt"}
CUSTOMER_PACKAGE_APP_TEXT_SUFFIXES = CUSTOMER_PACKAGE_TEXT_SUFFIXES | {".css", ".html", ".js", ".qml", ".svg", ".yaml", ".yml"}
CUSTOMER_PACKAGE_PATH_LEAK_RE = re.compile(
    r"(?i)(?:(?<![a-z0-9])[a-z]:[\\/][^\s\"'<>|]+|\\\\[a-z0-9_.-]+[\\/][^\s\"'<>|]+|"
    r"(?:^|[\s\"'])/(?:users|home|tmp|var/tmp|private/tmp)/[^\s\"'<>|]+|"
    r"[\\/]\\.codex[\\/])"
)
CUSTOMER_PACKAGE_APP_BUILD_PATH_LEAK_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/][^\s\"'<>|]*(?:[\\/]\\.codex[\\/]|[\\/]worktrees[\\/]|"
    r"[\\/]appdata[\\/]local[\\/]temp[\\/]|[\\/]pytest-of-[^\\/]+[\\/]|"
    r"[\\/]tmp[\\/]drawing_compare)[^\s\"'<>|]*|"
    r"(?:^|[\s\"'])/(?:users|home|tmp|var/tmp|private/tmp)/[^\s\"'<>|]*"
    r"(?:[\\/]\\.codex[\\/]|[\\/]worktrees[\\/]|[\\/]drawing_compare)[^\s\"'<>|]*)"
)
CUSTOMER_PACKAGE_BINARY_BUILD_PATH_LEAK_RE = re.compile(
    rb"(?i)(?:[a-z]:[\\/][^\x00\r\n\t \"'<>|]*(?:[\\/]\\.codex[\\/]|[\\/]worktrees[\\/]|"
    rb"[\\/]appdata[\\/]local[\\/]temp[\\/]|[\\/]pytest-of-[^\\/]+[\\/]|"
    rb"[\\/]tmp[\\/]drawing_compare)[^\x00\r\n\t \"'<>|]*|"
    rb"(?:^|[\s\"'])/(?:users|home|tmp|var/tmp|private/tmp)/[^\x00\r\n\t \"'<>|]*"
    rb"(?:[\\/]\\.codex[\\/]|[\\/]worktrees[\\/]|[\\/]drawing_compare)[^\x00\r\n\t \"'<>|]*)"
)

STRUCTURAL_COVERAGE_TERMS = {
    "member_add_delete_move": ("member", "beam", "column", "wall", "girder", "부재", "보", "기둥", "벽", "이동", "추가", "삭제"),
    "section_dimension_change": ("section", "dimension", "size", "단면", "치수", "크기"),
    "d13_spacing_change": ("d13@100", "d13@200", "d13", "@100", "@200"),
    "shd13_spacing_change": ("shd13@100", "shd13@200", "shd13"),
    "grid_change": ("grid", "axis", "그리드", "축선"),
    "structural_text_change": ("structural", "note", "text", "구조", "일람", "주기", "텍스트"),
}


@dataclass
class AuditCheck:
    name: str
    passed: bool
    detail: str
    evidence: list[str]


P5_G3_REALSET_GATE_REQUIRED_DOMAINS = (
    "comparison",
    "runtime_budget",
    "viewer_perf_summary",
    "selected_zone_evidence",
    "nonblank",
    "tile_manifest",
)

P5_G3_REALSET_GATE_TRIAGE = {
    "comparison": "realset matching/comparison completion",
    "runtime_budget": "RuntimeBudgetSampler or long-running pipeline budget",
    "viewer_perf_summary": "viewer telemetry JSONL/summary emission",
    "selected_zone_evidence": "selected-zone render worker/fallback pipeline",
    "nonblank": "screenshot/nonblank pixel evidence capture",
    "tile_manifest": "viewer package manifest materialisation or disk tile cache retention",
}

P5_G16_BENCHMARK_ID = "p5_g16_real_corpus_replay"
P5_G16_PROFILE = "real_corpus_artifact_replay"
P5_G16_REQUIRED_GATES = {
    "validation_summary_present",
    "viewer_root_present",
    "p5_g16_real_corpus_declared",
    "p5_g16_customer_manifest_present",
    "p5_g16_customer_sheet_count_min",
    "p5_g16_customer_sheet_count_max",
    "p5_g16_customer_format_dwg_dxf",
    "p5_g16_customer_format_pdf_pdf",
    "zone_render_artifact_count",
    "replay_completed",
    "artifact_replay_p95_ms",
    "artifact_replay_gap_max_ms",
    "blank_zone_output_count",
    "missing_zone_image_count",
    "stale_result_visible_count",
    "fallback_missing_reason_count",
    "timeout_count",
    "cancel_count",
    "rss_measurement_available",
    "rss_slope_mb_per_100_visits",
    "rss_positive_end_delta_mb",
    "rss_tail_peak_delta_mb",
}

P5_G22_BENCHMARK_ID = "p5_g22_actual_gui_soak"
P5_G22_PROFILE = "actual_gui_customer_corpus_soak"
P5_G22_REQUIRED_GATES = {
    "p5_g22_validation_summary_present",
    "p5_g22_viewer_root_present",
    "p5_g22_viewer_manifest_present",
    "p5_g22_customer_manifest_present",
    "p5_g22_real_corpus_declared",
    "p5_g22_customer_sheet_count_min",
    "p5_g22_customer_sheet_count_max",
    "p5_g22_pair_count",
    "p5_g22_gui_soak_completed",
    "p5_g22_drawing_selection_p95_ms",
    "p5_g22_page_navigation_count",
    "p5_g22_zone_selection_count",
    "p5_g22_zone_selection_p95_ms",
    "p5_g22_event_loop_gap_max_ms",
    "p5_g22_blank_view_count",
    "p5_g22_stale_active_pair_count",
    "p5_g22_stale_active_zone_count",
    "p5_g22_viewer_perf_stale_count",
    "p5_g22_worker_cleanup_ok",
    "p5_g22_orphan_worker_count",
    "p5_g22_rss_measurement_available",
    "p5_g22_rss_slope_mb_per_100_visits",
    "p5_g22_rss_positive_end_delta_mb",
    "p5_g22_rss_tail_peak_delta_mb",
    "p5_g22_native_resource_measurement_available",
    "p5_g22_process_handle_positive_end_delta",
    "p5_g22_open_file_descriptor_positive_end_delta",
    "p5_g22_gdi_handle_positive_end_delta",
    "p5_g22_user_handle_positive_end_delta",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        type=Path,
        action="append",
        required=True,
        help="Validation output folder containing validation_summary.json and _SUCCESS. Repeatable.",
    )
    parser.add_argument(
        "--release-manifest",
        type=Path,
        help="Optional release_manifest.json from release_drawing_compare_workbench.py",
    )
    parser.add_argument(
        "--large-dwg-probe",
        type=Path,
        help=(
            "Optional JSON probe proving the large-DWG performance/progress fix. "
            "Use with --require-large-dwg-probe for final completion audits."
        ),
    )
    parser.add_argument(
        "--require-large-dwg-probe",
        action="store_true",
        help="Fail the audit unless --large-dwg-probe is supplied and passes.",
    )
    parser.add_argument("--max-large-dwg-elapsed-s", type=float, default=DEFAULT_MAX_LARGE_DWG_ELAPSED_S)
    parser.add_argument(
        "--min-large-dwg-change-records",
        type=int,
        default=DEFAULT_MIN_LARGE_DWG_CHANGE_RECORDS,
    )
    parser.add_argument(
        "--max-large-dwg-in-memory-records",
        type=int,
        default=DEFAULT_MAX_LARGE_DWG_IN_MEMORY_RECORDS,
    )
    parser.add_argument(
        "--min-large-dwg-progress-events",
        type=int,
        default=DEFAULT_MIN_LARGE_DWG_PROGRESS_EVENTS,
    )
    parser.add_argument(
        "--customer-evidence-manifest",
        type=Path,
        help=(
            "Required with --evidence-level customer_grade. JSON manifest describing "
            "customer/customer-grade dataset provenance, ground truth, operator dry run, "
            "path audit, and structural coverage."
        ),
    )
    parser.add_argument("--out", type=Path, help="Write audit JSON to this path")
    parser.add_argument("--min-total-pairs", type=int, default=20)
    parser.add_argument("--max-total-pairs", type=int, default=50)
    parser.add_argument("--min-structural-review-recall", type=float, default=0.95)
    parser.add_argument(
        "--evidence-level",
        choices=("synthetic", "customer_grade"),
        default="synthetic",
        help="Use customer_grade only for real/customer-grade 20-50 sheet validation evidence.",
    )
    parser.add_argument(
        "--required-structural-coverage",
        action="append",
        choices=tuple(STRUCTURAL_COVERAGE_TERMS),
        help="Required structural coverage bucket. Defaults to all MVP structural-core buckets.",
    )
    parser.add_argument("--max-cold-zone-render-ms", type=float, default=10_000.0)
    parser.add_argument("--max-cache-hit-zone-render-ms", type=float, default=2_000.0)
    parser.add_argument(
        "--strict-zone-render-budget",
        action="store_true",
        help=(
            "Apply the tightened selected-zone latency thresholds "
            f"(cold p95 <= {STRICT_MAX_COLD_ZONE_RENDER_MS} ms, "
            f"cache-hit p95 <= {STRICT_MAX_CACHE_HIT_ZONE_RENDER_MS} ms) "
            "per Plan §17 Phase B-5 (GPT Pro deep-research review "
            "2026-05-17). Without this flag the legacy 10000/2000 ms "
            "defaults apply with a deprecation warning so a future release "
            "can flip the defaults."
        ),
    )
    parser.add_argument(
        "--max-first-review-ready-s",
        type=float,
        default=1_800.0,
        help=(
            "Maximum validation elapsed seconds allowed for the first review-ready "
            "screen evidence (review_dashboard + review_queue + viewer metadata)."
        ),
    )
    parser.add_argument(
        "--min-review-decision-precision",
        type=float,
        default=DEFAULT_MIN_REVIEW_DECISION_OVERALL_PRECISION,
        help="Minimum customer-grade review_decision_truth overall precision.",
    )
    parser.add_argument(
        "--min-review-decision-bucket-precision",
        type=float,
        default=DEFAULT_MIN_REVIEW_DECISION_BUCKET_PRECISION,
        help="Minimum customer-grade precision per structural bucket.",
    )
    parser.add_argument(
        "--max-review-decision-false-positive-rate",
        type=float,
        default=DEFAULT_MAX_REVIEW_DECISION_FALSE_POSITIVE_RATE,
        help="Maximum false-positive share among true_positive/false_positive decisions.",
    )
    parser.add_argument(
        "--max-large-dwg-peak-rss-mb",
        type=float,
        default=DEFAULT_MAX_LARGE_DWG_PEAK_RSS_MB,
        help="Maximum peak RSS MiB for the customer-grade large-DWG resource probe.",
    )
    parser.add_argument(
        "--max-large-dwg-progress-gap-s",
        type=float,
        default=DEFAULT_MAX_LARGE_DWG_PROGRESS_GAP_S,
        help="Maximum progress heartbeat gap seconds for the large-DWG resource probe.",
    )
    parser.add_argument(
        "--max-large-dwg-cancel-to-idle-s",
        type=float,
        default=DEFAULT_MAX_LARGE_DWG_CANCEL_TO_IDLE_S,
        help="Maximum cancel-to-idle seconds for the large-DWG cancel probe.",
    )
    parser.add_argument(
        "--max-bbox-relative-only-ratio",
        type=float,
        default=DEFAULT_MAX_BBOX_RELATIVE_ONLY_RATIO,
        help="Maximum relative_only bbox ratio for customer-grade PDF selected-zone evidence.",
    )
    parser.add_argument(
        "--max-bbox-page-fallback-ratio",
        type=float,
        default=DEFAULT_MAX_BBOX_PAGE_FALLBACK_RATIO,
        help="Maximum page_fallback bbox ratio for customer-grade PDF selected-zone evidence.",
    )
    parser.add_argument(
        "--require-runtime-budget",
        action="store_true",
        help=(
            "Fail the audit unless validation_summary contains a runtime_budget "
            "block measured by RuntimeBudgetSampler (recommendation #1 from the "
            "external audit review -- proxy metric reinforcement)."
        ),
    )
    parser.add_argument(
        "--require-perf-events-summary",
        action="store_true",
        help=(
            "Fail the audit unless validation_summary contains a non-empty "
            "perf_events_summary block. Enabled automatically for customer_grade."
        ),
    )
    parser.add_argument(
        "--require-p5-g3-realset-gate",
        action="store_true",
        help=(
            "Fail the audit unless every completed validation_summary contains "
            "a passed p5_g3_realset_gate block. Enabled automatically for "
            "customer_grade."
        ),
    )
    parser.add_argument(
        "--p5-g16-benchmark-json",
        "--p5-g16-real-corpus-replay",
        dest="p5_g16_benchmark_json",
        action="append",
        type=Path,
        default=[],
        help=(
            "Optional p5_g16_real_corpus_replay.json artifact. Repeatable. "
            "If omitted, the audit searches release/customer manifest "
            "references, validation summary fields, and each results-dir."
        ),
    )
    parser.add_argument(
        "--require-p5-g16-real-corpus-replay",
        action="store_true",
        help=(
            "Fail the audit unless a passed P5-G16 real-corpus replay benchmark "
            "is available. Enabled automatically for customer_grade."
        ),
    )
    parser.add_argument(
        "--p5-g22-gui-soak-json",
        "--p5-g22-actual-gui-soak",
        dest="p5_g22_gui_soak_json",
        action="append",
        type=Path,
        default=[],
        help=(
            "Optional p5_g22_actual_gui_soak.json artifact. Repeatable. "
            "If omitted, the audit searches release/customer manifest "
            "references, validation summary fields, and each results-dir."
        ),
    )
    parser.add_argument(
        "--require-p5-g22-actual-gui-soak",
        action="store_true",
        help=(
            "Fail the audit unless a passed P5-G22 actual GUI soak benchmark "
            "is available. Enabled automatically for customer_grade."
        ),
    )
    parser.add_argument(
        "--require-p5-g3-tile-eviction",
        "--require-p5-g6-tile-eviction",
        dest="require_p5_g3_tile_eviction",
        action="store_true",
        help=(
            "Fail the audit unless P5-G3 evidence proves tile-cache eviction "
            "was explicitly required and observed. Use for controlled "
            "low-byte-cap release-candidate probes, not routine customer-grade."
        ),
    )
    parser.add_argument(
        "--p5-g3-min-tile-evicted-pairs",
        "--p5-g6-min-tile-evicted-pairs",
        dest="p5_g3_min_tile_evicted_pairs",
        type=int,
        default=1,
    )
    parser.add_argument(
        "--p5-g3-min-tile-evicted-bytes",
        "--p5-g6-min-tile-evicted-bytes",
        dest="p5_g3_min_tile_evicted_bytes",
        type=int,
        default=1,
    )
    parser.add_argument(
        "--p5-g6-tile-cache-mb",
        type=float,
        default=None,
        help=(
            "When tile eviction is required, also require validation evidence "
            "to show this exact DRAWING_COMPARE_TILE_CACHE_MB cap."
        ),
    )
    parser.add_argument(
        "--max-perf-summary-elapsed-ms",
        type=float,
        default=None,
        help=(
            "Maximum allowed perf_events summary aggregation time in milliseconds. "
            "customer_grade defaults to 1000 ms when the value is omitted."
        ),
    )
    parser.add_argument(
        "--max-peak-working-set-mb",
        type=float,
        default=None,
        help=(
            "Maximum peak working-set memory in MiB. Strict gate when supplied; "
            "ignored otherwise. Recommended ceiling: 4096 for S20-class evidence."
        ),
    )
    parser.add_argument(
        "--max-peak-rss-mb",
        type=float,
        default=None,
        help=(
            "Maximum peak RSS (resident set size) in MiB measured via psutil. "
            "Cross-platform fallback for --max-peak-working-set-mb (which is "
            "Windows wset-specific). Strict gate when supplied. "
            "Recommended ceiling: 2048 (per GPT Pro deep-research review "
            "2026-05-17, F5). Use both gates together for full cross-platform "
            "coverage on customer-grade evidence."
        ),
    )
    parser.add_argument(
        "--max-runtime-first-review-ready-s",
        type=float,
        default=None,
        help=(
            "Maximum measured wall time from pipeline start to first review-ready "
            "zone (RuntimeBudget.first_review_ready_s). Distinct from the legacy "
            "--max-first-review-ready-s which compares timings.total_s."
        ),
    )
    parser.add_argument(
        "--max-peak-disk-spool-mb",
        type=float,
        default=None,
        help=(
            "Maximum peak disk-spool tempdir size in MiB measured during the "
            "validation run. Default unset; recommend 1024 with S20 probes."
        ),
    )
    parser.add_argument(
        "--max-peak-comparator-changes",
        type=int,
        default=None,
        help=(
            "Maximum allowed in-flight change-record peak inside "
            "DxfComparator.compare() (Plan §16 Phase C-2.3). Closes the "
            "external auditor CRITICAL finding that peak_compare_state_bytes "
            "is a post-hoc proxy. Strict gate when supplied; ignored otherwise."
        ),
    )
    parser.add_argument(
        "--max-time-to-first-stream-record-ms",
        type=int,
        default=None,
        help=(
            "Maximum allowed wall time in milliseconds between "
            "DxfComparator.compare() entry and the first streamed change "
            "record (Plan §16 Phase C-2.3 / C-3.1). Detects stalled "
            "comparators where accumulation runs long before streaming begins."
        ),
    )
    parser.add_argument(
        "--require-actual-crop-rate-pdf",
        type=float,
        default=None,
        help=(
            "Minimum actual_crop_available_rate for PDF-source zones "
            "(recommendation #2 from the external audit review -- separates "
            "synchronized_relative_fallback from real page-space crops)."
        ),
    )
    parser.add_argument(
        "--require-actual-crop-rate-cad",
        type=float,
        default=None,
        help=(
            "Minimum actual_crop_available_rate for CAD-source zones. "
            "Recommended >= 0.95 since CAD world-window crops should always "
            "succeed when the renderer dependencies are available."
        ),
    )
    parser.add_argument(
        "--require-actual-crop-rate-overall",
        type=float,
        default=None,
        help=(
            "Minimum actual_crop_available_rate across all zones (CAD + PDF "
            "combined). Useful when --require-actual-crop-rate-pdf / "
            "--require-actual-crop-rate-cad are not separately enforced."
        ),
    )
    parser.add_argument(
        "--require-precision-threshold",
        type=float,
        default=None,
        help=(
            "Minimum top_queue_precision (recommendation #3 from the external "
            "audit review). Computed from operator decisions vs review ground "
            "truth. Recommended >= 0.85 for customer-grade evidence "
            "(tightened from 0.80 per GPT Pro deep-research review 2026-05-17, "
            "F2). The Round-2 auditor's 0.80 floor is the minimum; 0.85 "
            "reflects the reviewer-burden bar a real operator would tolerate."
        ),
    )
    parser.add_argument(
        "--require-burden-threshold",
        type=float,
        default=None,
        help=(
            "Maximum false_positive_burden_per_sheet allowed. Caps the number "
            "of false positives a reviewer must process per sheet on average. "
            "Recommended <= 2.0 for customer-grade evidence (tightened from "
            "3.0 per GPT Pro deep-research review 2026-05-17, F2). The 2.0 "
            "ceiling means a reviewer rejects at most 1 of every 3 queue items "
            "as a false positive, which is the empirical cut-off above which "
            "operators report tool fatigue."
        ),
    )
    parser.add_argument(
        "--require-burden-minutes-threshold",
        type=float,
        default=None,
        help=(
            "Maximum review_burden_minutes_per_sheet allowed. Caps average "
            "reviewer time per sheet at the configured ceiling. Recommended "
            "<= 5 minutes for customer-grade evidence."
        ),
    )
    parser.add_argument(
        "--require-dataset-composition",
        action="store_true",
        help=(
            "Require dataset stratification compliance per recommendation #4. "
            "customer_evidence_manifest must include a dataset_composition "
            "block satisfying the default thresholds (CAD>=8, PDF>=8, "
            "blocked>=1, no_expand>=2, large_drawing>=2, plus coverage "
            "buckets). Use --composition-mode advisory to monitor only."
        ),
    )
    parser.add_argument(
        "--composition-mode",
        choices=("strict", "advisory"),
        default="strict",
        help=(
            "Stratification enforcement mode. strict (default) fails the "
            "audit on shortfalls; advisory only records the report without "
            "failing. Has no effect when --require-dataset-composition is off."
        ),
    )
    args = parser.parse_args(argv)
    if args.p5_g6_tile_cache_mb is not None and args.p5_g6_tile_cache_mb <= 0:
        parser.error("--p5-g6-tile-cache-mb must be greater than 0")
    return args


def _resolve_zone_render_budget(
    args: argparse.Namespace,
) -> tuple[float, float]:
    """Return (max_cold_ms, max_cache_hit_ms) per Plan §17 Phase B-5.

    Strict mode (--strict-zone-render-budget) returns the tightened
    pair; legacy mode returns the existing defaults AND emits a
    one-time deprecation notice on stderr so operators know the
    defaults will flip in a future release.
    """
    if getattr(args, "strict_zone_render_budget", False):
        return (
            STRICT_MAX_COLD_ZONE_RENDER_MS,
            STRICT_MAX_CACHE_HIT_ZONE_RENDER_MS,
        )
    # Emit a deprecation marker into stderr exactly once per process.
    if not getattr(_resolve_zone_render_budget, "_warned", False):
        print(
            "[deprecation] zone-render thresholds still using legacy "
            "10000/2000 ms; pass --strict-zone-render-budget to opt "
            "into 2000/500 ms (Plan §17 Phase B-5, GPT Pro F3).",
            file=sys.stderr,
        )
        _resolve_zone_render_budget._warned = True  # type: ignore[attr-defined]
    return args.max_cold_zone_render_ms, args.max_cache_hit_zone_render_ms


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    (
        resolved_max_cold_zone_render_ms,
        resolved_max_cache_hit_zone_render_ms,
    ) = _resolve_zone_render_budget(args)
    report = run_audit(
        result_dirs=[path.resolve() for path in args.results_dir],
        release_manifest=args.release_manifest.resolve() if args.release_manifest else None,
        large_dwg_probe=args.large_dwg_probe.resolve() if args.large_dwg_probe else None,
        require_large_dwg_probe=args.require_large_dwg_probe,
        max_large_dwg_elapsed_s=args.max_large_dwg_elapsed_s,
        min_large_dwg_change_records=args.min_large_dwg_change_records,
        max_large_dwg_in_memory_records=args.max_large_dwg_in_memory_records,
        min_large_dwg_progress_events=args.min_large_dwg_progress_events,
        customer_evidence_manifest=(
            args.customer_evidence_manifest.resolve() if args.customer_evidence_manifest else None
        ),
        min_total_pairs=args.min_total_pairs,
        max_total_pairs=args.max_total_pairs,
        min_structural_review_recall=args.min_structural_review_recall,
        evidence_level=args.evidence_level,
        required_structural_coverage=args.required_structural_coverage,
        max_cold_zone_render_ms=resolved_max_cold_zone_render_ms,
        max_cache_hit_zone_render_ms=resolved_max_cache_hit_zone_render_ms,
        max_first_review_ready_s=args.max_first_review_ready_s,
        require_runtime_budget=args.require_runtime_budget,
        max_peak_working_set_mb=args.max_peak_working_set_mb,
        max_peak_rss_mb=getattr(args, "max_peak_rss_mb", None),
        max_runtime_first_review_ready_s=args.max_runtime_first_review_ready_s,
        max_peak_disk_spool_mb=args.max_peak_disk_spool_mb,
        require_perf_events_summary=getattr(args, "require_perf_events_summary", False),
        require_p5_g3_realset_gate=getattr(args, "require_p5_g3_realset_gate", False),
        p5_g16_benchmark_json=[path.resolve() for path in args.p5_g16_benchmark_json],
        require_p5_g16_real_corpus_replay=getattr(args, "require_p5_g16_real_corpus_replay", False),
        p5_g22_gui_soak_json=[path.resolve() for path in args.p5_g22_gui_soak_json],
        require_p5_g22_actual_gui_soak=getattr(args, "require_p5_g22_actual_gui_soak", False),
        require_p5_g3_tile_eviction=getattr(args, "require_p5_g3_tile_eviction", False),
        p5_g3_min_tile_evicted_pairs=getattr(args, "p5_g3_min_tile_evicted_pairs", 1),
        p5_g3_min_tile_evicted_bytes=getattr(args, "p5_g3_min_tile_evicted_bytes", 1),
        p5_g6_tile_cache_mb=getattr(args, "p5_g6_tile_cache_mb", None),
        max_perf_summary_elapsed_ms=getattr(args, "max_perf_summary_elapsed_ms", None),
        # Plan §16 Phase C-2.3 — comparator-derived gates (getattr keeps older
        # CLI configurations working when these flags aren't supplied).
        max_peak_comparator_changes=getattr(args, "max_peak_comparator_changes", None),
        max_time_to_first_stream_record_ms=getattr(
            args, "max_time_to_first_stream_record_ms", None
        ),
        require_actual_crop_rate_pdf=args.require_actual_crop_rate_pdf,
        require_actual_crop_rate_cad=args.require_actual_crop_rate_cad,
        require_actual_crop_rate_overall=args.require_actual_crop_rate_overall,
        min_review_decision_precision=args.min_review_decision_precision,
        min_review_decision_bucket_precision=args.min_review_decision_bucket_precision,
        max_review_decision_false_positive_rate=args.max_review_decision_false_positive_rate,
        max_large_dwg_peak_rss_mb=args.max_large_dwg_peak_rss_mb,
        max_large_dwg_progress_gap_s=args.max_large_dwg_progress_gap_s,
        max_large_dwg_cancel_to_idle_s=args.max_large_dwg_cancel_to_idle_s,
        max_bbox_relative_only_ratio=args.max_bbox_relative_only_ratio,
        max_bbox_page_fallback_ratio=args.max_bbox_page_fallback_ratio,
        require_precision_threshold=args.require_precision_threshold,
        require_burden_threshold=args.require_burden_threshold,
        require_burden_minutes_threshold=args.require_burden_minutes_threshold,
        require_dataset_composition=args.require_dataset_composition,
        composition_mode=args.composition_mode,
    )
    text = json.dumps(report, ensure_ascii=True, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["status"] == "passed" else 1


def run_audit(
    *,
    result_dirs: Sequence[Path],
    release_manifest: Path | None = None,
    large_dwg_probe: Path | None = None,
    require_large_dwg_probe: bool = False,
    max_large_dwg_elapsed_s: float = DEFAULT_MAX_LARGE_DWG_ELAPSED_S,
    min_large_dwg_change_records: int = DEFAULT_MIN_LARGE_DWG_CHANGE_RECORDS,
    max_large_dwg_in_memory_records: int = DEFAULT_MAX_LARGE_DWG_IN_MEMORY_RECORDS,
    min_large_dwg_progress_events: int = DEFAULT_MIN_LARGE_DWG_PROGRESS_EVENTS,
    customer_evidence_manifest: Path | None = None,
    min_total_pairs: int = 20,
    max_total_pairs: int = 50,
    min_structural_review_recall: float = 0.95,
    evidence_level: str = "synthetic",
    required_structural_coverage: Sequence[str] | None = None,
    max_cold_zone_render_ms: float = 10_000.0,
    max_cache_hit_zone_render_ms: float = 2_000.0,
    max_first_review_ready_s: float = 1_800.0,
    require_runtime_budget: bool = False,
    max_peak_working_set_mb: float | None = None,
    # Plan §17 A-3 (GPT Pro F5) — cross-platform RSS ceiling. Used together
    # with max_peak_working_set_mb (Windows wset) for full platform coverage.
    max_peak_rss_mb: float | None = None,
    max_runtime_first_review_ready_s: float | None = None,
    max_peak_disk_spool_mb: float | None = None,
    require_perf_events_summary: bool = False,
    require_p5_g3_realset_gate: bool = False,
    p5_g16_benchmark_json: Sequence[Path] | None = None,
    p5_g16_real_corpus_replay: Path | None = None,
    require_p5_g16_real_corpus_replay: bool = False,
    p5_g22_gui_soak_json: Sequence[Path] | None = None,
    p5_g22_actual_gui_soak: Path | None = None,
    require_p5_g22_actual_gui_soak: bool = False,
    require_p5_g3_tile_eviction: bool = False,
    p5_g3_min_tile_evicted_pairs: int = 1,
    p5_g3_min_tile_evicted_bytes: int = 1,
    p5_g6_tile_cache_mb: float | None = None,
    max_perf_summary_elapsed_ms: float | None = None,
    # Plan §16 Phase C-2.3 — comparator-derived gates
    max_peak_comparator_changes: int | None = None,
    max_time_to_first_stream_record_ms: float | None = None,
    require_actual_crop_rate_pdf: float | None = None,
    require_actual_crop_rate_cad: float | None = None,
    require_actual_crop_rate_overall: float | None = None,
    min_review_decision_precision: float = DEFAULT_MIN_REVIEW_DECISION_OVERALL_PRECISION,
    min_review_decision_bucket_precision: float = DEFAULT_MIN_REVIEW_DECISION_BUCKET_PRECISION,
    max_review_decision_false_positive_rate: float = DEFAULT_MAX_REVIEW_DECISION_FALSE_POSITIVE_RATE,
    max_large_dwg_peak_rss_mb: float = DEFAULT_MAX_LARGE_DWG_PEAK_RSS_MB,
    max_large_dwg_progress_gap_s: float = DEFAULT_MAX_LARGE_DWG_PROGRESS_GAP_S,
    max_large_dwg_cancel_to_idle_s: float = DEFAULT_MAX_LARGE_DWG_CANCEL_TO_IDLE_S,
    max_bbox_relative_only_ratio: float = DEFAULT_MAX_BBOX_RELATIVE_ONLY_RATIO,
    max_bbox_page_fallback_ratio: float = DEFAULT_MAX_BBOX_PAGE_FALLBACK_RATIO,
    require_precision_threshold: float | None = None,
    require_burden_threshold: float | None = None,
    require_burden_minutes_threshold: float | None = None,
    require_dataset_composition: bool = False,
    composition_mode: str = "strict",
) -> dict[str, Any]:
    loaded = [_load_result_dir(path) for path in result_dirs]
    summaries = [item["summary"] for item in loaded if item.get("summary")]
    queue_items = [item for summary in summaries for item in _queue_items(summary)]
    release = _load_json(release_manifest) if release_manifest else None
    large_dwg_probe_payload = _load_json(large_dwg_probe) if large_dwg_probe else None
    customer_manifest = _load_json(customer_evidence_manifest) if customer_evidence_manifest else None
    structural_coverage = list(required_structural_coverage or STRUCTURAL_COVERAGE_TERMS.keys())

    # Plan §18 A-1 (GPT Pro F2/F5 follow-up) — customer_grade evidence
    # level auto-activates the precision/burden + peak_rss_mb gates that
    # were previously opt-in only. Plan §17 added the gates but defaulted
    # them to None, which meant a customer running the audit with default
    # flags got zero enforcement. The verification agent (2026-05-17)
    # flagged this as a FALSE "5 of 6 findings closed" claim.
    #
    # Data-aware activation: cascade promotes the reviewer-recommended
    # threshold only when the corresponding metric is ACTUALLY present
    # in at least one summary. This avoids the false-fail mode where a
    # legacy summary without peak_rss_mb / review_burden would suddenly
    # fail the new gate when no measurement actually exists. The cascade
    # still closes the F2/F5 closure gap for any customer whose pipeline
    # already produces the metric, which is the intended behaviour.
    if evidence_level == "customer_grade":
        if require_precision_threshold is None and any(
            isinstance(_nested(summary, "review_burden"), dict)
            and _nested(summary, "review_burden")
            for summary in summaries
        ):
            require_precision_threshold = STRICT_REQUIRE_PRECISION_THRESHOLD
        if require_burden_threshold is None and any(
            isinstance(_nested(summary, "review_burden"), dict)
            and _nested(summary, "review_burden")
            for summary in summaries
        ):
            require_burden_threshold = STRICT_REQUIRE_BURDEN_THRESHOLD
        if max_peak_rss_mb is None and any(
            (_nested(summary, "runtime_budget") or {}).get("peak_rss_mb") is not None
            for summary in summaries
        ):
            max_peak_rss_mb = STRICT_MAX_PEAK_RSS_MB
        require_runtime_budget = True
        require_perf_events_summary = True
        require_p5_g3_realset_gate = True
        require_p5_g16_real_corpus_replay = True
        require_p5_g22_actual_gui_soak = True
        if max_perf_summary_elapsed_ms is None:
            max_perf_summary_elapsed_ms = STRICT_MAX_PERF_SUMMARY_ELAPSED_MS
    if require_p5_g3_tile_eviction:
        require_p5_g3_realset_gate = True

    checks = [
        _check_customer_grade_evidence(
            evidence_level,
            customer_manifest,
            customer_evidence_manifest,
            summaries,
            loaded,
            min_total_pairs=min_total_pairs,
            max_total_pairs=max_total_pairs,
            max_cold_zone_render_ms=max_cold_zone_render_ms,
            max_cache_hit_zone_render_ms=max_cache_hit_zone_render_ms,
            required_structural_coverage=structural_coverage,
        ),
        # Plan §17 F6 (GPT Pro deep-research review) — content-addressed
        # integrity check for the customer-evidence manifest. Detects
        # post-generation manual edits, missing provenance blocks, and
        # bad input-file hashes. Only fires when a manifest was supplied.
        _check_manifest_provenance(
            evidence_level=evidence_level,
            customer_manifest=customer_manifest,
            manifest_path=customer_evidence_manifest,
        ),
        _check_p5_g7_forced_tile_eviction_manifest(
            evidence_level=evidence_level,
            customer_manifest=customer_manifest,
        ),
        _check_review_queue_precision(
            evidence_level,
            customer_manifest,
            min_overall_precision=min_review_decision_precision,
            min_bucket_precision=min_review_decision_bucket_precision,
            max_false_positive_rate=max_review_decision_false_positive_rate,
        ),
        _check_dataset_strata_coverage(evidence_level, customer_manifest),
        _check_first_interactive_ready(evidence_level, customer_manifest, summaries),
        _check_pdf_selected_zone_bbox_quality(
            evidence_level,
            customer_manifest,
            summaries,
            max_relative_only_ratio=max_bbox_relative_only_ratio,
            max_page_fallback_ratio=max_bbox_page_fallback_ratio,
        ),
        _check_large_dwg_resource_and_cancel_probe(
            evidence_level,
            customer_manifest,
            large_dwg_probe_payload,
            max_peak_rss_mb=max_large_dwg_peak_rss_mb,
            max_progress_gap_s=max_large_dwg_progress_gap_s,
            max_cancel_to_idle_s=max_large_dwg_cancel_to_idle_s,
        ),
        _check_validation_outputs_loaded(loaded),
        _check_success_sentinels(loaded),
        _check_quality_gates(summaries),
        _check_p5_g3_realset_gate(
            summaries,
            require_p5_g3_realset_gate=require_p5_g3_realset_gate,
            require_p5_g3_tile_eviction=require_p5_g3_tile_eviction,
            min_tile_evicted_pairs=p5_g3_min_tile_evicted_pairs,
            min_tile_evicted_bytes=p5_g3_min_tile_evicted_bytes,
            expected_tile_cache_mb=p5_g6_tile_cache_mb,
        ),
        _check_p5_g16_real_corpus_replay(
            explicit_paths=[
                *(p5_g16_benchmark_json or []),
                *([p5_g16_real_corpus_replay] if p5_g16_real_corpus_replay else []),
            ],
            customer_manifest=customer_manifest,
            customer_manifest_path=customer_evidence_manifest,
            release=release,
            release_manifest=release_manifest,
            loaded=loaded,
            evidence_level=evidence_level,
            require_p5_g16_real_corpus_replay=require_p5_g16_real_corpus_replay,
            min_total_pairs=min_total_pairs,
            max_total_pairs=max_total_pairs,
        ),
        _check_p5_g22_actual_gui_soak(
            explicit_paths=[
                *(p5_g22_gui_soak_json or []),
                *([p5_g22_actual_gui_soak] if p5_g22_actual_gui_soak else []),
            ],
            customer_manifest=customer_manifest,
            customer_manifest_path=customer_evidence_manifest,
            release=release,
            release_manifest=release_manifest,
            loaded=loaded,
            evidence_level=evidence_level,
            require_p5_g22_actual_gui_soak=require_p5_g22_actual_gui_soak,
            min_total_pairs=min_total_pairs,
            max_total_pairs=max_total_pairs,
        ),
        _check_preflight(summaries),
        _check_ai_optional_fallback(summaries),
        _check_sharable_audit(summaries),
        _check_raw_streams_absent(loaded),
        _check_review_queue_schema(queue_items),
        _check_top_issue_policy(summaries),
        _check_korean_review_text(queue_items),
        _check_cad_support(summaries, queue_items),
        _check_cad_structural_text_policy(queue_items),
        _check_cad_block_text_without_expansion(summaries),
        _check_pdf_support(summaries, queue_items),
        _check_pdf_bbox_coordinate_policy(loaded),
        _check_cad_pdf_blocking(summaries, loaded),
        _check_structural_review_recall(
            summaries,
            min_structural_review_recall=min_structural_review_recall,
        ),
        _check_structural_coverage(summaries, queue_items, required=structural_coverage),
        _check_scale(
            summaries,
            min_total_pairs=min_total_pairs,
            max_total_pairs=max_total_pairs,
        ),
        _check_first_review_ready_perf(
            summaries,
            max_first_review_ready_s=max_first_review_ready_s,
        ),
        _check_viewer_metadata_first_policy(summaries),
        _check_visual_asset_policy(loaded, evidence_level=evidence_level),
        _check_selected_zone_perf(
            loaded,
            summaries,
            evidence_level=evidence_level,
            max_cold_zone_render_ms=max_cold_zone_render_ms,
            max_cache_hit_zone_render_ms=max_cache_hit_zone_render_ms,
        ),
        _check_confirmed_exports(loaded, release, evidence_level=evidence_level),
        _check_release_manifest(release, release_manifest, evidence_level=evidence_level),
    ]
    if require_large_dwg_probe or large_dwg_probe:
        checks.append(
            _check_large_dwg_probe(
                large_dwg_probe_payload,
                large_dwg_probe,
                max_elapsed_s=max_large_dwg_elapsed_s,
                min_change_records=min_large_dwg_change_records,
                max_in_memory_records=max_large_dwg_in_memory_records,
                min_progress_events=min_large_dwg_progress_events,
            )
        )
    if (
        require_runtime_budget
        or max_peak_working_set_mb is not None
        or max_peak_rss_mb is not None
        or max_runtime_first_review_ready_s is not None
        or max_peak_disk_spool_mb is not None
        # Plan §16 Phase C-2.3 — activate the gate when comparator thresholds
        # are supplied (otherwise the new flags would be silently ignored).
        or max_peak_comparator_changes is not None
        or max_time_to_first_stream_record_ms is not None
    ):
        checks.append(
            _check_runtime_budget(
                summaries,
                require_runtime_budget=require_runtime_budget,
                max_peak_working_set_mb=max_peak_working_set_mb,
                max_peak_rss_mb=max_peak_rss_mb,
                max_runtime_first_review_ready_s=max_runtime_first_review_ready_s,
                max_peak_disk_spool_mb=max_peak_disk_spool_mb,
                max_peak_comparator_changes=max_peak_comparator_changes,
                max_time_to_first_stream_record_ms=max_time_to_first_stream_record_ms,
            )
        )
    if require_perf_events_summary or max_perf_summary_elapsed_ms is not None:
        checks.append(
            _check_perf_events_summary(
                summaries,
                require_perf_events_summary=require_perf_events_summary,
                max_perf_summary_elapsed_ms=max_perf_summary_elapsed_ms,
            )
        )
    if (
        require_actual_crop_rate_pdf is not None
        or require_actual_crop_rate_cad is not None
        or require_actual_crop_rate_overall is not None
    ):
        checks.append(
            _check_actual_crop_rate(
                summaries,
                loaded,
                require_actual_crop_rate_pdf=require_actual_crop_rate_pdf,
                require_actual_crop_rate_cad=require_actual_crop_rate_cad,
                require_actual_crop_rate_overall=require_actual_crop_rate_overall,
            )
        )
    if (
        require_precision_threshold is not None
        or require_burden_threshold is not None
        or require_burden_minutes_threshold is not None
    ):
        checks.append(
            _check_review_burden(
                summaries,
                require_precision_threshold=require_precision_threshold,
                require_burden_threshold=require_burden_threshold,
                require_burden_minutes_threshold=require_burden_minutes_threshold,
            )
        )
    if require_dataset_composition:
        checks.append(
            _check_dataset_composition(
                customer_manifest,
                customer_evidence_manifest,
                composition_mode=composition_mode,
            )
        )
    passed = all(check.passed for check in checks)
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "result_dirs": [str(path) for path in result_dirs],
        "release_manifest": str(release_manifest) if release_manifest else "",
        "large_dwg_probe": str(large_dwg_probe) if large_dwg_probe else "",
        "customer_evidence_manifest": str(customer_evidence_manifest) if customer_evidence_manifest else "",
        "evidence_level": evidence_level,
        "checks": [asdict(check) for check in checks],
        "summary": {
            "passed": sum(1 for check in checks if check.passed),
            "failed": sum(1 for check in checks if not check.passed),
            "completed_pairs": sum(_int(_nested(summary, "comparison", "completed_pairs")) for summary in summaries),
            "queue_items": len(queue_items),
        },
    }


def _load_result_dir(path: Path) -> dict[str, Any]:
    summary_path = path / "validation_summary.json"
    summary = _load_json(summary_path)
    return {
        "path": path,
        "summary_path": summary_path,
        "summary": summary,
        "success_path": path / "_SUCCESS",
        "run_manifest_path": path / "run_manifest.json",
        "quality_gate_path": path / "quality_gate.json",
    }


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _check_validation_outputs_loaded(loaded: Sequence[dict[str, Any]]) -> AuditCheck:
    missing = [str(item["summary_path"]) for item in loaded if not item.get("summary")]
    return AuditCheck(
        name="validation_outputs_loaded",
        passed=not missing and bool(loaded),
        detail="all validation_summary.json files loaded" if not missing else f"missing/unreadable: {missing}",
        evidence=[str(item["summary_path"]) for item in loaded],
    )


def _check_large_dwg_probe(
    probe: dict[str, Any] | None,
    probe_path: Path | None,
    *,
    max_elapsed_s: float,
    min_change_records: int,
    max_in_memory_records: int,
    min_progress_events: int,
) -> AuditCheck:
    failures: list[str] = []
    evidence = [str(probe_path) if probe_path else ""]
    if not probe_path:
        failures.append("--large-dwg-probe is required")
    elif not probe_path.exists():
        failures.append(f"{probe_path}: missing")
    if not isinstance(probe, dict):
        failures.append(f"{probe_path}: missing or unreadable JSON")
        return AuditCheck(
            name="large_dwg_performance_probe",
            passed=False,
            detail="; ".join(failures),
            evidence=evidence,
        )

    elapsed_s = _float(probe.get("elapsed_s"))
    if elapsed_s <= 0:
        failures.append("elapsed_s must be > 0")
    elif elapsed_s > max_elapsed_s:
        failures.append(f"elapsed_s={elapsed_s} exceeds {max_elapsed_s}")

    total = _int(probe.get("total"))
    stream_count = _int(_nested(probe, "metadata", "change_zone_record_count"))
    if total < min_change_records:
        failures.append(f"total={total} below required {min_change_records}")
    if stream_count < min_change_records:
        failures.append(
            f"metadata.change_zone_record_count={stream_count} below required {min_change_records}"
        )
    if total and stream_count and total != stream_count:
        failures.append(
            f"metadata.change_zone_record_count={stream_count} does not match total={total}"
        )

    in_memory = _int(
        probe.get("change_records_in_memory")
        or _nested(probe, "metadata", "change_records_in_memory")
    )
    if in_memory <= 0:
        failures.append("change_records_in_memory must be > 0")
    elif in_memory > max_in_memory_records:
        failures.append(
            f"change_records_in_memory={in_memory} exceeds {max_in_memory_records}"
        )

    if _nested(probe, "metadata", "large_drawing_mode") != "active":
        failures.append("metadata.large_drawing_mode must be active")
    if _nested(probe, "metadata", "change_zone_stream_complete") is not True:
        failures.append("metadata.change_zone_stream_complete must be true")
    if probe.get("stream_exists") is not True:
        failures.append("stream_exists must be true")
    if _int(probe.get("stream_bytes")) <= 0:
        failures.append("stream_bytes must be > 0")

    progress_event_count = _int(probe.get("progress_event_count"))
    if progress_event_count < min_progress_events:
        failures.append(
            f"progress_event_count={progress_event_count} below required {min_progress_events}"
        )
    progress_messages = [
        str(event.get("message") or "")
        for event in probe.get("progress_events_tail") or []
        if isinstance(event, dict)
    ]
    if not any("DXF" in message and ("compare" in message.lower() or "비교" in message) for message in progress_messages):
        failures.append("progress_events_tail must include forwarded DXF compare progress")

    return AuditCheck(
        name="large_dwg_performance_probe",
        passed=not failures,
        detail=(
            "large DWG probe meets elapsed, stream, memory, and progress budgets"
            if not failures
            else "; ".join(failures)
        ),
        evidence=evidence
        + [
            f"elapsed_s={elapsed_s}",
            f"total={total}",
            f"change_zone_record_count={stream_count}",
            f"change_records_in_memory={in_memory}",
            f"progress_event_count={progress_event_count}",
        ],
    )


def _check_manifest_provenance(
    *,
    evidence_level: str,
    customer_manifest: dict[str, Any] | None,
    manifest_path: Path | None,
) -> AuditCheck:
    """Plan §17 F6 (GPT Pro deep-research review) — content-addressed
    integrity check for the customer-evidence manifest.

    Verifies:
    - The ``provenance`` block exists and has the required fields
    - ``manifest_sha256`` matches a recomputed hash of the manifest minus
      its provenance (detects post-generation manual edits)
    - ``generated_at_utc`` parses as ISO-8601
    - ``tool_version`` is non-empty
    - ``input_file_hashes`` is a dict of 64-char hex digests

    Behaviour:
    - synthetic evidence level: returns PASS with a non-blocking note
      (the synthetic gate already prevents MVP completion certification)
    - customer_grade level: any violation fails the gate
    - manifest absent: skipped (a separate ``customer_grade_evidence
      _declared`` check enforces presence)
    - manifest_provenance import unavailable (released-tree bundles
      without ``src/``): fails customer_grade with a clear message
    """
    label = f"manifest={manifest_path}" if manifest_path else "manifest=<none>"

    if evidence_level != "customer_grade":
        return AuditCheck(
            name="customer_grade_manifest_provenance",
            passed=True,
            detail=(
                f"evidence_level={evidence_level} — manifest provenance check "
                "is advisory outside customer_grade"
            ),
            evidence=[label],
        )

    if customer_manifest is None or manifest_path is None:
        # The separate customer_grade_evidence_declared check handles the
        # "missing manifest" failure; don't double-fail here.
        return AuditCheck(
            name="customer_grade_manifest_provenance",
            passed=True,
            detail="no manifest supplied — covered by customer_grade_evidence_declared",
            evidence=[label],
        )

    if not _MANIFEST_PROVENANCE_AVAILABLE or _verify_manifest_integrity is None:
        return AuditCheck(
            name="customer_grade_manifest_provenance",
            passed=False,
            detail=(
                "manifest_provenance helper unavailable — released-tree "
                "bundle cannot verify integrity. Re-run the audit from a "
                "tree that includes src/services/comparison/"
                "manifest_provenance.py"
            ),
            evidence=[label],
        )

    violations = _verify_manifest_integrity(customer_manifest)
    if violations:
        return AuditCheck(
            name="customer_grade_manifest_provenance",
            passed=False,
            detail="; ".join(violations),
            evidence=[label] + [f"violation:{v}" for v in violations],
        )
    return AuditCheck(
        name="customer_grade_manifest_provenance",
        passed=True,
        detail="manifest provenance intact (SHA-256 matches recomputed hash)",
        evidence=[label],
    )


def _customer_gate_not_required(name: str, evidence_level: str) -> AuditCheck:
    return AuditCheck(
        name=name,
        passed=True,
        detail=f"not required for evidence_level={evidence_level}",
        evidence=[evidence_level],
    )


def _customer_gate_missing_manifest(name: str, manifest: dict[str, Any] | None) -> AuditCheck | None:
    if isinstance(manifest, dict):
        return None
    return AuditCheck(
        name=name,
        passed=True,
        detail="deferred: customer_grade_evidence_declared reports the missing manifest",
        evidence=[],
    )


def _check_p5_g7_forced_tile_eviction_manifest(
    *,
    evidence_level: str,
    customer_manifest: dict[str, Any] | None,
) -> AuditCheck:
    name = "p5_g7_forced_tile_eviction_manifest"
    if evidence_level != "customer_grade":
        return _customer_gate_not_required(name, evidence_level)
    missing = _customer_gate_missing_manifest(name, customer_manifest)
    if missing:
        return missing

    block = customer_manifest.get("p5_g7_forced_tile_eviction")
    if not isinstance(block, dict):
        return AuditCheck(
            name=name,
            passed=True,
            detail=(
                "P5-G7 forced tile-eviction proof not supplied; optional for "
                "routine customer_grade evidence unless the release claims realset tile eviction"
            ),
            evidence=["p5_g7_forced_tile_eviction=<missing>"],
        )

    status = str(block.get("status") or "").strip()
    required = block.get("required") is True
    proof_count = _int(block.get("proof_count"))
    passed_proof_count = _int(block.get("passed_proof_count"))
    expected_tile_cache_mb = _optional_float(block.get("expected_tile_cache_mb"))
    issues = [
        str(issue)
        for issue in block.get("issues") or []
        if str(issue).strip()
    ]
    failures: list[str] = []
    if required and status != "passed":
        failures.append(
            "p5_g7_forced_tile_eviction.required=true requires status=passed"
        )
    if status == "passed":
        if proof_count < 1:
            failures.append("p5_g7_forced_tile_eviction.proof_count must be >= 1 when status=passed")
        if passed_proof_count < 1:
            failures.append(
                "p5_g7_forced_tile_eviction.passed_proof_count must be >= 1 when status=passed"
            )
        if issues:
            failures.append("p5_g7_forced_tile_eviction.issues must be empty when status=passed")
    if proof_count > 0 and passed_proof_count > proof_count:
        failures.append(
            "p5_g7_forced_tile_eviction.passed_proof_count cannot exceed proof_count"
        )

    evidence = [
        f"status={status or '<missing>'}",
        f"required={required}",
        f"proof_count={proof_count}",
        f"passed_proof_count={passed_proof_count}",
    ]
    if expected_tile_cache_mb is not None:
        evidence.append(f"expected_tile_cache_mb={expected_tile_cache_mb:g}")
    evidence.extend(f"issue:{issue}" for issue in issues[:5])
    return AuditCheck(
        name=name,
        passed=not failures,
        detail=(
            "P5-G7 forced tile-eviction manifest block is consistent"
            if not failures
            else "; ".join(failures)
        ),
        evidence=evidence,
    )


def _check_review_queue_precision(
    evidence_level: str,
    manifest: dict[str, Any] | None,
    *,
    min_overall_precision: float,
    min_bucket_precision: float,
    max_false_positive_rate: float,
) -> AuditCheck:
    name = "review_queue_precision"
    if evidence_level != "customer_grade":
        return _customer_gate_not_required(name, evidence_level)
    missing = _customer_gate_missing_manifest(name, manifest)
    if missing:
        return missing
    quality = manifest.get("review_decision_quality") if isinstance(manifest, dict) else None
    failures: list[str] = []
    if not isinstance(quality, dict):
        failures.append("manifest.review_decision_quality block is required")
        quality = {}
    if quality.get("status") != "passed":
        failures.append("manifest.review_decision_quality.status must be passed")
    labeled_rows = _int(quality.get("labeled_rows"))
    if labeled_rows < DEFAULT_MIN_REVIEW_DECISION_ROWS:
        failures.append(
            f"review_decision_quality.labeled_rows={labeled_rows} below {DEFAULT_MIN_REVIEW_DECISION_ROWS}"
        )
    overall_precision = _float(quality.get("overall_precision"))
    if overall_precision < min_overall_precision:
        failures.append(
            f"review_decision_quality.overall_precision={overall_precision} below {min_overall_precision}"
        )
    fp_rate = _float(quality.get("false_positive_rate"))
    if fp_rate > max_false_positive_rate:
        failures.append(
            f"review_decision_quality.false_positive_rate={fp_rate} exceeds {max_false_positive_rate}"
        )
    bucket_precision = quality.get("bucket_precision")
    bucket_rows = quality.get("bucket_labeled_rows")
    if not isinstance(bucket_precision, dict):
        failures.append("review_decision_quality.bucket_precision is required")
        bucket_precision = {}
    if not isinstance(bucket_rows, dict):
        failures.append("review_decision_quality.bucket_labeled_rows is required")
        bucket_rows = {}
    for bucket in STRUCTURAL_COVERAGE_TERMS:
        rows = _int(bucket_rows.get(bucket))
        precision = _float(bucket_precision.get(bucket))
        if rows < DEFAULT_MIN_REVIEW_DECISION_BUCKET_ROWS:
            failures.append(
                f"review_decision_quality.bucket_labeled_rows.{bucket}={rows} below "
                f"{DEFAULT_MIN_REVIEW_DECISION_BUCKET_ROWS}"
            )
        if precision < min_bucket_precision:
            failures.append(
                f"review_decision_quality.bucket_precision.{bucket}={precision} below "
                f"{min_bucket_precision}"
            )
    return AuditCheck(
        name=name,
        passed=not failures,
        detail="review decision precision and false-positive burden passed" if not failures else "; ".join(failures),
        evidence=[
            f"labeled_rows={labeled_rows}",
            f"overall_precision={overall_precision}",
            f"false_positive_rate={fp_rate}",
        ],
    )


def _check_dataset_strata_coverage(
    evidence_level: str,
    manifest: dict[str, Any] | None,
) -> AuditCheck:
    name = "dataset_strata_coverage"
    if evidence_level != "customer_grade":
        return _customer_gate_not_required(name, evidence_level)
    missing = _customer_gate_missing_manifest(name, manifest)
    if missing:
        return missing
    strata = manifest.get("dataset_strata") if isinstance(manifest, dict) else None
    failures: list[str] = []
    if not isinstance(strata, dict):
        failures.append("manifest.dataset_strata block is required")
        strata = {}
    if strata.get("status") != "passed":
        failures.append("manifest.dataset_strata.status must be passed")
    rows = _int(strata.get("rows"))
    sheet_count = _int(manifest.get("sheet_count")) if isinstance(manifest, dict) else 0
    if sheet_count and rows != sheet_count:
        failures.append(f"dataset_strata.rows={rows} must equal manifest.sheet_count={sheet_count}")
    format_counts = strata.get("format_pair_counts") if isinstance(strata.get("format_pair_counts"), dict) else {}
    sheet_counts = strata.get("sheet_type_counts") if isinstance(strata.get("sheet_type_counts"), dict) else {}
    checks = {
        "cad_rows": (strata.get("cad_rows"), DEFAULT_MIN_DATASET_STRATA_CAD_ROWS),
        "pdf_pdf_rows": (format_counts.get("pdf_pdf"), DEFAULT_MIN_DATASET_STRATA_PDF_ROWS),
        "raster_or_low_quality_rows": (
            strata.get("raster_or_low_quality_rows"),
            DEFAULT_MIN_DATASET_STRATA_RASTER_ROWS,
        ),
        "large_dwg_rows": (strata.get("large_dwg_rows"), DEFAULT_MIN_DATASET_STRATA_LARGE_DWG_ROWS),
        "block_text_rows": (strata.get("block_text_rows"), DEFAULT_MIN_DATASET_STRATA_BLOCK_TEXT_ROWS),
        "negative_control_rows": (
            strata.get("negative_control_rows"),
            DEFAULT_MIN_DATASET_STRATA_NEGATIVE_CONTROL_ROWS,
        ),
    }
    for label, (value, minimum) in checks.items():
        count = _int(value)
        if count < minimum:
            failures.append(f"dataset_strata.{label}={count} below {minimum}")
    for sheet_type in SHEET_TYPE_VALUES:
        count = _int(sheet_counts.get(sheet_type))
        if count < DEFAULT_MIN_DATASET_STRATA_SHEET_TYPE_ROWS:
            failures.append(
                f"dataset_strata.sheet_type_counts.{sheet_type}={count} below "
                f"{DEFAULT_MIN_DATASET_STRATA_SHEET_TYPE_ROWS}"
            )
    return AuditCheck(
        name=name,
        passed=not failures,
        detail="dataset strata cover required customer risk buckets" if not failures else "; ".join(failures),
        evidence=[
            f"rows={rows}",
            f"sheet_count={sheet_count}",
            f"format_pair_counts={format_counts}",
            f"sheet_type_counts={sheet_counts}",
        ],
    )


def _check_first_interactive_ready(
    evidence_level: str,
    manifest: dict[str, Any] | None,
    summaries: Sequence[dict[str, Any]],
) -> AuditCheck:
    name = "first_interactive_ready"
    if evidence_level != "customer_grade":
        return _customer_gate_not_required(name, evidence_level)
    missing = _customer_gate_missing_manifest(name, manifest)
    if missing:
        return missing
    readiness = manifest.get("first_interactive_readiness") if isinstance(manifest, dict) else None
    summary_readiness = summarize_first_interactive_readiness(summaries)
    failures: list[str] = []
    if not isinstance(readiness, dict):
        failures.append("manifest.first_interactive_readiness block is required")
        readiness = {}
    if readiness.get("status") != "passed":
        failures.append("manifest.first_interactive_readiness.status must be passed")
    for key in (
        "max_review_dashboard_ready_s",
        "max_first_top_issue_ready_s",
        "max_viewer_metadata_ready_s",
    ):
        if _float(readiness.get(key)) <= 0:
            failures.append(f"manifest.first_interactive_readiness.{key} is required")
    if summary_readiness.get("status") != "passed":
        failures.extend(
            f"validation_summary.{issue}"
            for issue in (summary_readiness.get("issues") or [])[:5]
        )
    return AuditCheck(
        name=name,
        passed=not failures,
        detail="first interactive readiness budgets passed" if not failures else "; ".join(failures),
        evidence=[
            f"manifest={readiness}",
            f"validation_summary={summary_readiness}",
        ],
    )


def _check_pdf_selected_zone_bbox_quality(
    evidence_level: str,
    manifest: dict[str, Any] | None,
    summaries: Sequence[dict[str, Any]],
    *,
    max_relative_only_ratio: float,
    max_page_fallback_ratio: float,
) -> AuditCheck:
    name = "pdf_selected_zone_bbox_quality"
    if evidence_level != "customer_grade":
        return _customer_gate_not_required(name, evidence_level)
    missing = _customer_gate_missing_manifest(name, manifest)
    if missing:
        return missing
    bbox_quality = manifest.get("bbox_quality") if isinstance(manifest, dict) else None
    summary_quality = summarize_bbox_quality(
        summaries,
        max_relative_only_ratio=max_relative_only_ratio,
        max_page_fallback_ratio=max_page_fallback_ratio,
    )
    failures: list[str] = []
    if not isinstance(bbox_quality, dict):
        failures.append("manifest.bbox_quality block is required")
        bbox_quality = {}
    if bbox_quality.get("status") != "passed":
        failures.append("manifest.bbox_quality.status must be passed")
    relative_ratio = _float(bbox_quality.get("relative_only_ratio"))
    page_ratio = _float(bbox_quality.get("page_fallback_ratio"))
    if bbox_quality.get("top_priority_relative_only") is True:
        failures.append("bbox_quality.top_priority_relative_only must be false")
    if relative_ratio > max_relative_only_ratio:
        failures.append(f"bbox_quality.relative_only_ratio={relative_ratio} exceeds {max_relative_only_ratio}")
    if page_ratio > max_page_fallback_ratio:
        failures.append(f"bbox_quality.page_fallback_ratio={page_ratio} exceeds {max_page_fallback_ratio}")
    if summary_quality.get("status") != "passed":
        failures.extend(f"validation_summary.{issue}" for issue in (summary_quality.get("issues") or [])[:5])
    return AuditCheck(
        name=name,
        passed=not failures,
        detail="PDF selected-zone bbox fallback quality passed" if not failures else "; ".join(failures),
        evidence=[f"manifest={bbox_quality}", f"validation_summary={summary_quality}"],
    )


def _check_large_dwg_resource_and_cancel_probe(
    evidence_level: str,
    manifest: dict[str, Any] | None,
    probe: dict[str, Any] | None,
    *,
    max_peak_rss_mb: float,
    max_progress_gap_s: float,
    max_cancel_to_idle_s: float,
) -> AuditCheck:
    name = "large_dwg_resource_and_cancel_probe"
    if evidence_level != "customer_grade":
        return _customer_gate_not_required(name, evidence_level)
    missing = _customer_gate_missing_manifest(name, manifest)
    if missing:
        return missing
    resource = manifest.get("large_dwg_resource_probe") if isinstance(manifest, dict) else None
    summary = resource if isinstance(resource, dict) else summarize_large_dwg_resource_probe(
        probe,
        max_peak_rss_mb=max_peak_rss_mb,
        max_progress_gap_s=max_progress_gap_s,
        max_cancel_to_idle_s=max_cancel_to_idle_s,
    )
    failures: list[str] = []
    if not isinstance(resource, dict):
        failures.append("manifest.large_dwg_resource_probe block is required")
    if summary.get("status") != "passed":
        failures.append("manifest.large_dwg_resource_probe.status must be passed")
    if _float(summary.get("peak_rss_mb")) > max_peak_rss_mb:
        failures.append(f"large_dwg_resource_probe.peak_rss_mb exceeds {max_peak_rss_mb}")
    if _float(summary.get("progress_max_gap_s")) > max_progress_gap_s:
        failures.append(f"large_dwg_resource_probe.progress_max_gap_s exceeds {max_progress_gap_s}")
    cancel_probe = summary.get("cancel_probe") if isinstance(summary.get("cancel_probe"), dict) else {}
    if cancel_probe.get("status") != "passed":
        failures.append("large_dwg_resource_probe.cancel_probe.status must be passed")
    if _float(cancel_probe.get("cancel_to_idle_s")) > max_cancel_to_idle_s:
        failures.append(
            f"large_dwg_resource_probe.cancel_probe.cancel_to_idle_s exceeds {max_cancel_to_idle_s}"
        )
    if cancel_probe.get("partial_outputs_cleaned") is not True:
        failures.append("large_dwg_resource_probe.cancel_probe.partial_outputs_cleaned must be true")
    if _int(cancel_probe.get("worker_processes_left")) != 0:
        failures.append("large_dwg_resource_probe.cancel_probe.worker_processes_left must be 0")
    return AuditCheck(
        name=name,
        passed=not failures,
        detail="large-DWG resource and cancel probe passed" if not failures else "; ".join(failures),
        evidence=[json.dumps(summary, ensure_ascii=True, sort_keys=True)],
    )


def _check_customer_grade_evidence(
    evidence_level: str,
    manifest: dict[str, Any] | None,
    manifest_path: Path | None,
    summaries: Sequence[dict[str, Any]],
    loaded: Sequence[dict[str, Any]],
    *,
    min_total_pairs: int,
    max_total_pairs: int,
    max_cold_zone_render_ms: float,
    max_cache_hit_zone_render_ms: float,
    required_structural_coverage: Sequence[str],
) -> AuditCheck:
    if evidence_level != "customer_grade":
        return AuditCheck(
            name="customer_grade_evidence_declared",
            passed=False,
            detail="evidence_level is synthetic; do not use this audit as final MVP completion evidence",
            evidence=[evidence_level],
        )

    failures: list[str] = []
    evidence = [str(manifest_path) if manifest_path else ""]
    if not manifest_path:
        failures.append("--customer-evidence-manifest is required for customer_grade evidence")
    if manifest_path and manifest is None:
        failures.append(f"{manifest_path}: missing or unreadable JSON")
    if not isinstance(manifest, dict):
        return AuditCheck(
            name="customer_grade_evidence_declared",
            passed=False,
            detail="; ".join(failures),
            evidence=evidence,
        )
    manifest_path_leaks = _customer_evidence_manifest_path_leak_count(manifest_path)
    if manifest_path_leaks:
        failures.append(
            "manifest customer_evidence_manifest.json must not contain absolute/cache/temp path leakage "
            f"({manifest_path_leaks} match(es))"
        )

    if manifest.get("evidence_level") != "customer_grade":
        failures.append("manifest.evidence_level must be customer_grade")
    readiness = manifest.get("readiness")
    if not isinstance(readiness, dict):
        failures.append("manifest.readiness block is required")
    else:
        readiness_status = str(readiness.get("status") or "").strip()
        if readiness_status != "ready":
            failures.append("manifest.readiness.status must be ready")
        readiness_issues = readiness.get("issues")
        if isinstance(readiness_issues, list) and readiness_issues:
            failures.append("manifest.readiness.issues must be empty")
    for key in ("dataset_id", "ground_truth_owner", "validation_date"):
        if not str(manifest.get(key) or "").strip():
            failures.append(f"manifest.{key} is required")
    provenance = manifest.get("dataset_provenance") or {}
    if provenance.get("source_kind") not in CUSTOMER_GRADE_SOURCE_KINDS:
        failures.append("manifest.dataset_provenance.source_kind must be customer or customer_grade")
    if not str(provenance.get("source_description") or "").strip():
        failures.append("manifest.dataset_provenance.source_description is required")
    if provenance.get("approval_status") != APPROVED_DATASET_STATUS:
        failures.append(
            f"manifest.dataset_provenance.approval_status must be {APPROVED_DATASET_STATUS}"
        )
    if not str(provenance.get("approver") or "").strip():
        failures.append("manifest.dataset_provenance.approver is required")

    sheet_count = _int(manifest.get("sheet_count"))
    if sheet_count < min_total_pairs:
        failures.append(f"manifest.sheet_count={sheet_count}, required>={min_total_pairs}")
    if sheet_count > max_total_pairs:
        failures.append(f"manifest.sheet_count={sheet_count}, required<={max_total_pairs}")
    completed_pairs = sum(_int(_nested(summary, "comparison", "completed_pairs")) for summary in summaries)
    if sheet_count > completed_pairs:
        failures.append(
            f"manifest.sheet_count={sheet_count} exceeds audited completed_pairs={completed_pairs}"
        )

    format_coverage = manifest.get("format_coverage") or {}
    for key in ("dwg_dxf", "pdf_pdf", "cad_pdf_blocked"):
        if format_coverage.get(key) is not True:
            failures.append(f"manifest.format_coverage.{key} must be true")
    if format_coverage.get("dwg_dxf") is True and not _has_dwg_dxf_cad_evidence(summaries):
        failures.append(
            "manifest.format_coverage.dwg_dxf has no audited DWG and DXF completed CAD evidence"
        )
    if format_coverage.get("pdf_pdf") is True and not _has_pdf_pdf_evidence(summaries):
        failures.append("manifest.format_coverage.pdf_pdf has no audited PDF-PDF source evidence")
    if format_coverage.get("cad_pdf_blocked") is True and not _has_cad_pdf_block(summaries, loaded):
        failures.append("manifest.format_coverage.cad_pdf_blocked has no audited block evidence")

    cad_policy_evidence = manifest.get("cad_policy_evidence") or {}
    if cad_policy_evidence.get("block_text_detection_without_expansion") is not True:
        failures.append(
            "manifest.cad_policy_evidence.block_text_detection_without_expansion must be true"
        )
    elif not _check_cad_block_text_without_expansion(summaries).passed:
        failures.append(
            "manifest.cad_policy_evidence.block_text_detection_without_expansion has no audited evidence"
        )

    declared_buckets = set(_string_list(manifest.get("structural_coverage")))
    missing_buckets = sorted(set(required_structural_coverage) - declared_buckets)
    if missing_buckets:
        failures.append(f"manifest.structural_coverage missing {missing_buckets}")

    ground_truth = manifest.get("ground_truth") or {}
    manifest_truth_rows = _int(ground_truth.get("row_count"))
    if manifest_truth_rows <= 0:
        failures.append("manifest.ground_truth.row_count must be > 0")
    audited_truth_rows = sum(_int(_nested(summary, "review_ground_truth", "rows")) for summary in summaries)
    if manifest_truth_rows > audited_truth_rows:
        failures.append(
            f"manifest.ground_truth.row_count={manifest_truth_rows} exceeds audited review_ground_truth rows={audited_truth_rows}"
        )
    if ground_truth.get("status") != "approved":
        failures.append("manifest.ground_truth.status must be approved")
    truth_csv = str(ground_truth.get("review_ground_truth_csv") or "").strip()
    truth_csv_path = _manifest_reference_path(manifest_path, truth_csv)
    if not truth_csv_path:
        failures.append("manifest.ground_truth.review_ground_truth_csv must point to an existing file")
    else:
        if _is_template_or_handoff_evidence(truth_csv_path):
            failures.append(
                "manifest.ground_truth.review_ground_truth_csv must not reference "
                "a template or handoff document"
            )
        truth_csv_rows = _csv_data_row_count(truth_csv_path)
        if manifest_truth_rows > truth_csv_rows:
            failures.append(
                f"manifest.ground_truth.row_count={manifest_truth_rows} exceeds CSV rows={truth_csv_rows}"
            )
        failures.extend(
            f"manifest.ground_truth.{issue}"
            for issue in review_ground_truth_csv_issues(truth_csv_path)
        )

    operator = manifest.get("operator_dry_run") or {}
    if operator.get("status") != "passed":
        failures.append("manifest.operator_dry_run.status must be passed")
    operator_role = str(operator.get("reviewer_role") or "").strip()
    if not operator_role:
        failures.append("manifest.operator_dry_run.reviewer_role is required")
    elif _normalize_operator_role(operator_role) not in APPROVED_OPERATOR_REVIEWER_ROLES:
        failures.append(
            "manifest.operator_dry_run.reviewer_role must be a structural review lead/team lead role "
            f"({sorted(APPROVED_OPERATOR_REVIEWER_ROLES)})"
        )
    if operator.get("confirmed_export_checked") is not True:
        failures.append("manifest.operator_dry_run.confirmed_export_checked must be true")
    operator_artifacts = operator.get("artifacts") or {}
    notes_file = _manifest_reference_path(
        manifest_path,
        str(operator_artifacts.get("notes_file") or "").strip(),
    )
    screenshots_dir = _manifest_reference_path(
        manifest_path,
        str(operator_artifacts.get("screenshots_dir") or "").strip(),
    )
    if not notes_file:
        failures.append("manifest.operator_dry_run.artifacts.notes_file must point to an existing checklist")
    elif _is_template_or_handoff_evidence(notes_file):
        failures.append(
            "manifest.operator_dry_run.artifacts.notes_file must not reference "
            "a template or handoff document"
        )
    elif not operator_notes_have_substantive_review_notes(notes_file):
        failures.append(
            "manifest.operator_dry_run.artifacts.notes_file must include substantive "
            "operator dry-run review notes beyond role and checklist"
        )
    if not (notes_file or screenshots_dir):
        failures.append(
            "manifest.operator_dry_run.artifacts.notes_file or screenshots_dir must point to an existing artifact"
        )
    declared_workflow_checks = set(_string_list(operator.get("workflow_checks")))
    missing_declared_checks = sorted(set(REQUIRED_OPERATOR_WORKFLOW_CHECKS) - declared_workflow_checks)
    if missing_declared_checks:
        failures.append(
            "manifest.operator_dry_run.workflow_checks missing "
            f"{missing_declared_checks}"
        )
    missing_note_checks = _operator_notes_missing_workflow_checks(notes_file)
    if missing_note_checks:
        failures.append(
            "manifest.operator_dry_run.artifacts.notes_file missing checklist ids "
            f"{missing_note_checks}"
        )
    if (
        notes_file
        and _normalize_operator_role(operator_role) in APPROVED_OPERATOR_REVIEWER_ROLES
        and not _operator_notes_has_reviewer_role(notes_file, operator_role)
    ):
        failures.append(
            "manifest.operator_dry_run.artifacts.notes_file must include matching reviewer_role "
            f"({operator_role})"
        )
    confirmed_export_artifact = _manifest_reference_path(
        manifest_path,
        str(operator_artifacts.get("confirmed_export_artifact") or "").strip(),
    )
    if operator.get("confirmed_export_checked") is True and not confirmed_export_artifact:
        failures.append(
            "manifest.operator_dry_run.artifacts.confirmed_export_artifact must point to an existing artifact"
        )
    elif confirmed_export_artifact and not _is_audited_confirmed_export_artifact(
        confirmed_export_artifact,
        loaded,
    ):
        failures.append(
            "manifest.operator_dry_run.artifacts.confirmed_export_artifact must be a *_confirmed.* file "
            "(.png/.pdf/.dxf) under an audited validation output"
        )

    path_audit = manifest.get("path_leakage_audit") or {}
    if path_audit.get("status") != "passed":
        failures.append("manifest.path_leakage_audit.status must be passed")
    if _int(path_audit.get("leak_count")) != 0:
        failures.append("manifest.path_leakage_audit.leak_count must be 0")
    audit_json = str(path_audit.get("audit_json") or "").strip()
    audit_json_path = _manifest_reference_path(manifest_path, audit_json)
    if not audit_json_path:
        failures.append("manifest.path_leakage_audit.audit_json must point to an existing file")
    else:
        audit_json_path_leaks = _customer_evidence_manifest_path_leak_count(audit_json_path)
        if audit_json_path_leaks:
            failures.append(
                "manifest.path_leakage_audit.audit_json must not contain absolute/cache/temp path leakage "
                f"({audit_json_path_leaks} match(es))"
            )
        audit_payload = _load_json(audit_json_path) or {}
        if audit_payload.get("status") not in {None, "passed"}:
            failures.append("manifest.path_leakage_audit.audit_json status must be passed when present")
        if _int(audit_payload.get("leak_count")) != 0:
            failures.append("manifest.path_leakage_audit.audit_json leak_count must be 0")

    selected_perf = manifest.get("selected_zone_performance") or {}
    if selected_perf.get("status") != "passed":
        failures.append("manifest.selected_zone_performance.status must be passed")
    selected_completed = _int(selected_perf.get("completed_outputs"))
    selected_telemetry = _int(selected_perf.get("telemetry_outputs"))
    if selected_completed <= 0:
        failures.append("manifest.selected_zone_performance.completed_outputs must be > 0")
    if selected_telemetry < selected_completed:
        failures.append(
            "manifest.selected_zone_performance.telemetry_outputs must cover every completed output"
        )
    selected_cold = _float(selected_perf.get("max_cold_p95_ms"))
    selected_hit = _float(selected_perf.get("max_cache_hit_p95_ms"))
    if selected_cold and selected_cold > max_cold_zone_render_ms:
        failures.append(
            "manifest.selected_zone_performance.max_cold_p95_ms "
            f"{selected_cold} exceeds {max_cold_zone_render_ms}"
        )
    if selected_hit and selected_hit > max_cache_hit_zone_render_ms:
        failures.append(
            "manifest.selected_zone_performance.max_cache_hit_p95_ms "
            f"{selected_hit} exceeds {max_cache_hit_zone_render_ms}"
        )

    workbench_acceptance = manifest.get("workbench_acceptance") or {}
    if workbench_acceptance.get("status") != "passed":
        failures.append("manifest.workbench_acceptance.status must be passed")
    required_acceptance = set(_string_list(workbench_acceptance.get("required_items")))
    missing_acceptance = sorted(set(REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS) - required_acceptance)
    if missing_acceptance:
        failures.append(
            "manifest.workbench_acceptance.required_items missing "
            f"{missing_acceptance}"
        )

    passed = not failures
    return AuditCheck(
        name="customer_grade_evidence_declared",
        passed=passed,
        detail=(
            "customer-grade evidence manifest is present and complete"
            if passed
            else "; ".join(failures)
        ),
        evidence=evidence,
    )


def _check_success_sentinels(loaded: Sequence[dict[str, Any]]) -> AuditCheck:
    failures: list[str] = []
    evidence: list[str] = []
    for item in loaded:
        success = item["success_path"]
        manifest = item["run_manifest_path"]
        evidence.append(str(success))
        if not success.exists():
            failures.append(f"{success}: missing")
            continue
        try:
            completion = validate_run_completion(str(manifest), str(success))
        except Exception as exc:
            failures.append(f"{success}: {exc}")
            continue
        if not completion.get("valid"):
            failures.append(f"{success}: invalid completion contract")
    return AuditCheck(
        name="_SUCCESS_completion_contract",
        passed=not failures,
        detail="all outputs have valid _SUCCESS completion contracts" if not failures else "; ".join(failures),
        evidence=evidence,
    )


def _check_quality_gates(summaries: Sequence[dict[str, Any]]) -> AuditCheck:
    failures = [
        str(summary.get("output_dir") or "<unknown>")
        for summary in summaries
        if _nested(summary, "quality_gate", "status") != "passed"
    ]
    return AuditCheck(
        name="quality_gate_passed",
        passed=not failures and bool(summaries),
        detail="all quality gates passed" if not failures else f"failed quality gates: {failures}",
        evidence=[str(_nested(summary, "outputs", "quality_gate_json") or "") for summary in summaries],
    )


def _check_p5_g3_realset_gate(
    summaries: Sequence[dict[str, Any]],
    *,
    require_p5_g3_realset_gate: bool,
    require_p5_g3_tile_eviction: bool,
    min_tile_evicted_pairs: int,
    min_tile_evicted_bytes: int,
    expected_tile_cache_mb: float | None,
) -> AuditCheck:
    failures: list[str] = []
    evidence: list[str] = []
    completed_outputs = 0
    gated_outputs = 0

    for summary in summaries:
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_outputs += 1
        label = str(summary.get("output_dir") or "<validation_summary>")
        gate = summary.get("p5_g3_realset_gate")
        if not isinstance(gate, dict) or not gate:
            if require_p5_g3_realset_gate:
                failures.append(f"{label}: p5_g3_realset_gate block missing")
            evidence.append(f"{label}: p5_g3_realset_gate=missing")
            continue

        status = str(gate.get("status") or "")
        requested = gate.get("requested") is True
        gate_failures = [str(item) for item in (gate.get("failures") or [])]
        if not requested:
            if require_p5_g3_realset_gate:
                failures.append(f"{label}: p5_g3_realset_gate.requested is not true")
            evidence.append(
                f"{label}: requested={requested}, status={status or '<missing>'}"
            )
            continue

        gated_outputs += 1
        gate_evidence = gate.get("evidence")
        if not isinstance(gate_evidence, dict):
            gate_evidence = {}
        domain_statuses: dict[str, str] = {}
        failed_domains: set[str] = set()
        for domain in P5_G3_REALSET_GATE_REQUIRED_DOMAINS:
            domain_payload = gate_evidence.get(domain)
            domain_status = (
                str(domain_payload.get("status") or "")
                if isinstance(domain_payload, dict)
                else ""
            )
            domain_statuses[domain] = domain_status or "missing"
            if domain_status != "passed":
                failed_domains.add(domain)
                failures.append(
                    f"{label}: p5_g3_realset_gate.{domain}.status="
                    f"{domain_status or '<missing>'}"
                )
        evidence.append(
            f"{label}: requested={requested}, status={status or '<missing>'}, "
            f"domains={domain_statuses}, failures={gate_failures}"
        )
        if status != "passed":
            failures.append(
                f"{label}: p5_g3_realset_gate.status={status or '<missing>'}"
            )
        failures.extend(f"{label}: {item}" for item in gate_failures)
        tile_evidence = gate_evidence.get("tile_manifest")
        if require_p5_g3_tile_eviction:
            if not isinstance(tile_evidence, dict):
                failures.append(f"{label}: p5_g3_realset_gate.tile_manifest missing")
                failed_domains.add("tile_manifest")
            else:
                evidence.append(
                    f"{label}: tile_eviction_evidence="
                    f"require_eviction={tile_evidence.get('require_eviction')}, "
                    f"evicted_pairs={_int(tile_evidence.get('evicted_pair_count'))}, "
                    f"evicted_bytes={_int(tile_evidence.get('evicted_estimated_bytes'))}, "
                    f"byte_limit={_int(tile_evidence.get('byte_limit'))}, "
                    f"configured_tile_cache_mb={tile_evidence.get('configured_tile_cache_mb')}, "
                    f"tile_cache_env_mb={tile_evidence.get('tile_cache_env_mb')}"
                )
                if tile_evidence.get("require_eviction") is not True:
                    failures.append(
                        f"{label}: p5_g3_realset_gate.tile_manifest.require_eviction "
                        "is not true"
                    )
                    failed_domains.add("tile_manifest")
                evicted_pairs = _int(tile_evidence.get("evicted_pair_count"))
                evicted_bytes = _int(tile_evidence.get("evicted_estimated_bytes"))
                if evicted_pairs < max(1, min_tile_evicted_pairs):
                    failures.append(
                        f"{label}: p5_g3_realset_gate.tile_manifest.evicted_pair_count="
                        f"{evicted_pairs} < {max(1, min_tile_evicted_pairs)}"
                    )
                    failed_domains.add("tile_manifest")
                if evicted_bytes < max(1, min_tile_evicted_bytes):
                    failures.append(
                        f"{label}: p5_g3_realset_gate.tile_manifest.evicted_estimated_bytes="
                        f"{evicted_bytes} < {max(1, min_tile_evicted_bytes)}"
                    )
                    failed_domains.add("tile_manifest")
                if expected_tile_cache_mb is not None:
                    configured_mb = _optional_float(tile_evidence.get("configured_tile_cache_mb"))
                    env_mb = _optional_float(tile_evidence.get("tile_cache_env_mb"))
                    byte_limit = _int(tile_evidence.get("byte_limit"))
                    expected_bytes = int(float(expected_tile_cache_mb) * 1024 * 1024)
                    if not _float_close(configured_mb, expected_tile_cache_mb):
                        failures.append(
                            f"{label}: p5_g3_realset_gate.tile_manifest.configured_tile_cache_mb="
                            f"{configured_mb} != {expected_tile_cache_mb}"
                        )
                        failed_domains.add("tile_manifest")
                    if not _float_close(env_mb, expected_tile_cache_mb):
                        failures.append(
                            f"{label}: p5_g3_realset_gate.tile_manifest.tile_cache_env_mb="
                            f"{env_mb} != {expected_tile_cache_mb}"
                        )
                        failed_domains.add("tile_manifest")
                    if byte_limit <= 0:
                        failures.append(
                            f"{label}: p5_g3_realset_gate.tile_manifest.byte_limit missing for "
                            f"expected tile cache cap {expected_tile_cache_mb} MB"
                        )
                        failed_domains.add("tile_manifest")
                    elif abs(byte_limit - expected_bytes) > 1:
                        failures.append(
                            f"{label}: p5_g3_realset_gate.tile_manifest.byte_limit="
                            f"{byte_limit} != {expected_bytes}"
                        )
                        failed_domains.add("tile_manifest")
        if failed_domains or gate_failures:
            triage = {
                domain: P5_G3_REALSET_GATE_TRIAGE.get(domain, "unknown")
                for domain in sorted(failed_domains)
            }
            evidence.append(f"{label}: triage_hints={triage}")

    if require_p5_g3_realset_gate and completed_outputs == 0:
        failures.append("require_p5_g3_realset_gate enforced but no completed outputs found")

    detail_parts = [
        f"completed_outputs={completed_outputs}",
        f"with_p5_g3_realset_gate={gated_outputs}",
    ]
    if require_p5_g3_tile_eviction:
        detail_parts.append(
            "require_tile_eviction="
            f"pairs>={max(1, min_tile_evicted_pairs)}, "
            f"bytes>={max(1, min_tile_evicted_bytes)}"
        )
    if expected_tile_cache_mb is not None:
        detail_parts.append(f"expected_tile_cache_mb={expected_tile_cache_mb}")
    if failures:
        detail_parts.append("failures=" + "; ".join(failures))
    ok = (
        not failures
        and (
            gated_outputs > 0
            or not require_p5_g3_realset_gate
        )
    )
    return AuditCheck(
        name="p5_g3_realset_release_gate",
        passed=ok,
        detail=", ".join(detail_parts),
        evidence=evidence,
    )


def _check_p5_g16_real_corpus_replay(
    *,
    explicit_paths: Sequence[Path],
    customer_manifest: dict[str, Any] | None,
    customer_manifest_path: Path | None,
    release: dict[str, Any] | None,
    release_manifest: Path | None,
    loaded: Sequence[dict[str, Any]],
    evidence_level: str,
    require_p5_g16_real_corpus_replay: bool,
    min_total_pairs: int,
    max_total_pairs: int,
) -> AuditCheck:
    name = "p5_g16_real_corpus_replay"
    candidates = _p5_g16_candidate_paths(
        explicit_paths=explicit_paths,
        customer_manifest=customer_manifest,
        customer_manifest_path=customer_manifest_path,
        release=release,
        release_manifest=release_manifest,
        loaded=loaded,
    )
    evidence = [str(path) for path in candidates]
    if not require_p5_g16_real_corpus_replay and not explicit_paths and not candidates:
        return AuditCheck(
            name=name,
            passed=True,
            detail="P5-G16 replay evidence is advisory outside customer_grade",
            evidence=[],
        )
    if not candidates:
        detail = "p5_g16_real_corpus_replay artifact missing"
        return AuditCheck(
            name=name,
            passed=not require_p5_g16_real_corpus_replay,
            detail=detail,
            evidence=[],
        )

    benchmark_path = candidates[0]
    payload = _load_json(benchmark_path)
    failures = _p5_g16_payload_failures(
        payload,
        loaded=loaded,
        customer_manifest_path=customer_manifest_path,
        min_total_pairs=min_total_pairs,
        max_total_pairs=max_total_pairs,
        require_customer_grade=evidence_level == "customer_grade" or require_p5_g16_real_corpus_replay,
    )
    detail_parts = [f"path={benchmark_path}"]
    if isinstance(payload, dict):
        detail_parts.append(f"status={payload.get('status') or '<missing>'}")
        detail_parts.append(f"benchmark_id={payload.get('benchmark_id') or '<missing>'}")
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        detail_parts.append(f"visits={_int(summary.get('completed_visit_count'))}/{_int(summary.get('visit_count'))}")
    if failures:
        detail_parts.append("failures=" + "; ".join(failures))
    return AuditCheck(
        name=name,
        passed=not failures,
        detail=", ".join(detail_parts),
        evidence=evidence,
    )


def _p5_g16_candidate_paths(
    *,
    explicit_paths: Sequence[Path],
    customer_manifest: dict[str, Any] | None,
    customer_manifest_path: Path | None,
    release: dict[str, Any] | None,
    release_manifest: Path | None,
    loaded: Sequence[dict[str, Any]],
) -> list[Path]:
    candidates: list[Path] = []

    def add(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.resolve() if path.exists() else path
        if resolved not in candidates:
            candidates.append(resolved)

    for path in explicit_paths:
        add(path)
    if isinstance(release, dict):
        artifacts = release.get("artifacts") if isinstance(release.get("artifacts"), dict) else {}
        for key in ("p5_g16_real_corpus_replay_json", "p5_g16_real_corpus_replay", "real_corpus_replay"):
            add(_manifest_reference_path(release_manifest, str(artifacts.get(key) or "").strip()))
        jsons = artifacts.get("p5_g16_real_corpus_replay_jsons")
        if isinstance(jsons, list):
            for value in jsons:
                add(_manifest_reference_path(release_manifest, str(value or "").strip()))
    if isinstance(customer_manifest, dict):
        for value in _p5_g16_manifest_path_values(customer_manifest):
            add(_manifest_reference_path(customer_manifest_path, value))
    for item in loaded:
        summary = item.get("summary")
        if isinstance(summary, dict):
            add(_result_reference_path(item, str(_nested(summary, "outputs", "p5_g16_real_corpus_replay_json") or "")))
            add(_result_reference_path(item, str(_nested(summary, "benchmarks", "p5_g16_real_corpus_replay", "output_json") or "")))
        root = item.get("path")
        if isinstance(root, Path):
            candidate = root / "p5_g16_real_corpus_replay.json"
            if candidate.exists():
                add(candidate)
    return candidates


def _p5_g16_manifest_path_values(manifest: dict[str, Any]) -> list[str]:
    values: list[str] = []
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    for key in ("p5_g16_real_corpus_replay_json", "p5_g16_real_corpus_replay", "real_corpus_replay"):
        text = str(artifacts.get(key) or "").strip()
        if text:
            values.append(text)
    jsons = artifacts.get("p5_g16_real_corpus_replay_jsons")
    if isinstance(jsons, list):
        values.extend(str(item).strip() for item in jsons if str(item or "").strip())
    for key in ("p5_g16_real_corpus_replay", "real_corpus_replay", "performance_replay"):
        section = manifest.get(key)
        if isinstance(section, dict):
            text = str(section.get("path") or section.get("json") or section.get("artifact") or "").strip()
            if text:
                values.append(text)
    performance = manifest.get("performance_benchmarks")
    if isinstance(performance, dict):
        section = performance.get("p5_g16_real_corpus_replay")
        if isinstance(section, dict):
            text = str(
                section.get("benchmark_json")
                or section.get("path")
                or section.get("json")
                or section.get("artifact")
                or ""
            ).strip()
            if text:
                values.append(text)
            benchmark_jsons = section.get("benchmark_jsons")
            if isinstance(benchmark_jsons, list):
                values.extend(
                    str(item).strip()
                    for item in benchmark_jsons
                    if str(item or "").strip()
                )
        elif isinstance(section, str) and section.strip():
            values.append(section.strip())
    return values


def _p5_g16_payload_failures(
    payload: dict[str, Any] | None,
    *,
    loaded: Sequence[dict[str, Any]],
    customer_manifest_path: Path | None,
    min_total_pairs: int,
    max_total_pairs: int,
    require_customer_grade: bool,
) -> list[str]:
    if not isinstance(payload, dict):
        return ["p5_g16_real_corpus_replay JSON missing or unreadable"]
    failures: list[str] = []
    if payload.get("schema_version") != 1:
        failures.append("schema_version must be 1")
    if payload.get("benchmark_id") != P5_G16_BENCHMARK_ID:
        failures.append(f"benchmark_id must be {P5_G16_BENCHMARK_ID}")
    if payload.get("profile") != P5_G16_PROFILE:
        failures.append(f"profile must be {P5_G16_PROFILE}")
    if payload.get("status") != "passed":
        failures.append(f"status={payload.get('status') or '<missing>'}")

    gates = payload.get("gates")
    if not isinstance(gates, list):
        failures.append("gates[] missing")
        gate_by_name: dict[str, dict[str, Any]] = {}
    else:
        gate_by_name = {
            str(gate.get("name") or ""): gate
            for gate in gates
            if isinstance(gate, dict) and str(gate.get("name") or "")
        }
        missing = sorted(P5_G16_REQUIRED_GATES - set(gate_by_name))
        if missing:
            failures.append("required gates missing: " + ", ".join(missing))
        failed = sorted(
            gate_name
            for gate_name, gate in gate_by_name.items()
            if gate.get("required") is not False and gate.get("passed") is not True
        )
        if failed:
            failures.append("required gates failed: " + ", ".join(failed))

    args_payload = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    environment = payload.get("environment") if isinstance(payload.get("environment"), dict) else {}
    corpus = payload.get("corpus") if isinstance(payload.get("corpus"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    native_summary = (
        summary.get("native_resource_summary")
        if isinstance(summary.get("native_resource_summary"), dict)
        else {}
    )
    worker_summary = (
        summary.get("worker_tree_summary")
        if isinstance(summary.get("worker_tree_summary"), dict)
        else {}
    )

    if require_customer_grade:
        if args_payload.get("require_customer_corpus") is not True:
            failures.append("args.require_customer_corpus must be true")
        if corpus.get("evidence_level") != "customer_grade":
            failures.append("corpus.evidence_level must be customer_grade")
        sheet_count = _int(corpus.get("sheet_count"))
        if sheet_count < int(min_total_pairs) or sheet_count > int(max_total_pairs):
            failures.append(f"corpus.sheet_count={sheet_count} outside {min_total_pairs}-{max_total_pairs}")
        if corpus.get("has_dwg_dxf") is not True:
            failures.append("corpus.has_dwg_dxf must be true")
        if corpus.get("has_pdf_pdf") is not True:
            failures.append("corpus.has_pdf_pdf must be true")
        if environment.get("allow_missing_psutil") is True:
            failures.append("environment.allow_missing_psutil must be false")
        if environment.get("psutil_available") is not True:
            failures.append("environment.psutil_available must be true")
        source_validation = source.get("validation_summary") if isinstance(source.get("validation_summary"), dict) else {}
        validation_sha = str(source_validation.get("sha256") or "").strip()
        current_validation_hashes = {
            _file_sha256(item.get("summary_path"))
            for item in loaded
            if isinstance(item.get("summary_path"), Path)
        }
        current_validation_hashes.discard("")
        if not validation_sha:
            failures.append("source.validation_summary.sha256 missing")
        elif validation_sha not in current_validation_hashes:
            failures.append("source.validation_summary.sha256 does not match audited validation_summary.json")
        manifest_sha = str(corpus.get("manifest_sha256") or "").strip()
        current_manifest_sha = _file_sha256(customer_manifest_path)
        if current_manifest_sha:
            if not manifest_sha:
                failures.append("corpus.manifest_sha256 missing")
            elif manifest_sha != current_manifest_sha:
                failures.append("corpus.manifest_sha256 does not match customer evidence manifest")

    if summary.get("rss_measurement_available") is not True:
        failures.append("summary.rss_measurement_available must be true")
    for key in (
        "blank_zone_output_count",
        "missing_zone_image_count",
        "stale_result_visible_count",
        "fallback_missing_reason_count",
        "timeout_count",
        "cancel_count",
    ):
        value = _int(summary.get(key))
        if value != 0:
            failures.append(f"summary.{key}={value} must be 0")
    if summary.get("replay_completed") is not True:
        failures.append("summary.replay_completed must be true")
    if _int(summary.get("zone_render_artifact_count")) <= 0:
        failures.append("summary.zone_render_artifact_count must be > 0")
    return failures


def _check_p5_g22_actual_gui_soak(
    *,
    explicit_paths: Sequence[Path],
    customer_manifest: dict[str, Any] | None,
    customer_manifest_path: Path | None,
    release: dict[str, Any] | None,
    release_manifest: Path | None,
    loaded: Sequence[dict[str, Any]],
    evidence_level: str,
    require_p5_g22_actual_gui_soak: bool,
    min_total_pairs: int,
    max_total_pairs: int,
) -> AuditCheck:
    name = "p5_g22_actual_gui_soak"
    candidates = _p5_g22_candidate_paths(
        explicit_paths=explicit_paths,
        customer_manifest=customer_manifest,
        customer_manifest_path=customer_manifest_path,
        release=release,
        release_manifest=release_manifest,
        loaded=loaded,
    )
    evidence = [str(path) for path in candidates]
    if not require_p5_g22_actual_gui_soak and not explicit_paths and not candidates:
        return AuditCheck(
            name=name,
            passed=True,
            detail="P5-G22 actual GUI soak evidence is advisory outside customer_grade",
            evidence=[],
        )
    if not candidates:
        return AuditCheck(
            name=name,
            passed=not require_p5_g22_actual_gui_soak,
            detail="p5_g22_actual_gui_soak artifact missing",
            evidence=[],
        )

    benchmark_path = candidates[0]
    payload = _load_json(benchmark_path)
    failures = _p5_g22_payload_failures(
        payload,
        loaded=loaded,
        customer_manifest_path=customer_manifest_path,
        min_total_pairs=min_total_pairs,
        max_total_pairs=max_total_pairs,
        require_customer_grade=evidence_level == "customer_grade" or require_p5_g22_actual_gui_soak,
    )
    detail_parts = [f"path={benchmark_path}"]
    if isinstance(payload, dict):
        detail_parts.append(f"status={payload.get('status') or '<missing>'}")
        detail_parts.append(f"benchmark_id={payload.get('benchmark_id') or '<missing>'}")
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        detail_parts.append(f"visits={_int(summary.get('completed_visit_count'))}/{_int(summary.get('visit_count'))}")
        detail_parts.append(f"blank_views={_int(summary.get('blank_view_count'))}")
        detail_parts.append(f"orphan_workers={_int(summary.get('orphan_worker_count'))}")
    if failures:
        detail_parts.append("failures=" + "; ".join(failures))
    return AuditCheck(
        name=name,
        passed=not failures,
        detail=", ".join(detail_parts),
        evidence=evidence,
    )


def _p5_g22_candidate_paths(
    *,
    explicit_paths: Sequence[Path],
    customer_manifest: dict[str, Any] | None,
    customer_manifest_path: Path | None,
    release: dict[str, Any] | None,
    release_manifest: Path | None,
    loaded: Sequence[dict[str, Any]],
) -> list[Path]:
    candidates: list[Path] = []

    def add(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.resolve() if path.exists() else path
        if resolved not in candidates:
            candidates.append(resolved)

    for path in explicit_paths:
        add(path)
    if isinstance(release, dict):
        artifacts = release.get("artifacts") if isinstance(release.get("artifacts"), dict) else {}
        for key in ("p5_g22_actual_gui_soak_json", "p5_g22_actual_gui_soak", "actual_gui_soak"):
            add(_manifest_reference_path(release_manifest, str(artifacts.get(key) or "").strip()))
        jsons = artifacts.get("p5_g22_actual_gui_soak_jsons")
        if isinstance(jsons, list):
            for value in jsons:
                add(_manifest_reference_path(release_manifest, str(value or "").strip()))
    if isinstance(customer_manifest, dict):
        for value in _p5_g22_manifest_path_values(customer_manifest):
            add(_manifest_reference_path(customer_manifest_path, value))
    for item in loaded:
        summary = item.get("summary")
        if isinstance(summary, dict):
            add(_result_reference_path(item, str(_nested(summary, "outputs", "p5_g22_actual_gui_soak_json") or "")))
            add(_result_reference_path(item, str(_nested(summary, "benchmarks", "p5_g22_actual_gui_soak", "output_json") or "")))
        root = item.get("path")
        if isinstance(root, Path):
            candidate = root / "p5_g22_actual_gui_soak.json"
            if candidate.exists():
                add(candidate)
    return candidates


def _p5_g22_manifest_path_values(manifest: dict[str, Any]) -> list[str]:
    values: list[str] = []
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    for key in ("p5_g22_actual_gui_soak_json", "p5_g22_actual_gui_soak", "actual_gui_soak"):
        text = str(artifacts.get(key) or "").strip()
        if text:
            values.append(text)
    jsons = artifacts.get("p5_g22_actual_gui_soak_jsons")
    if isinstance(jsons, list):
        values.extend(str(item).strip() for item in jsons if str(item or "").strip())
    for key in ("p5_g22_actual_gui_soak", "actual_gui_soak", "gui_soak"):
        section = manifest.get(key)
        if isinstance(section, dict):
            text = str(section.get("path") or section.get("json") or section.get("artifact") or "").strip()
            if text:
                values.append(text)
        elif isinstance(section, str) and section.strip():
            values.append(section.strip())
    performance = manifest.get("performance_benchmarks")
    if isinstance(performance, dict):
        section = performance.get("p5_g22_actual_gui_soak")
        if isinstance(section, dict):
            text = str(
                section.get("benchmark_json")
                or section.get("path")
                or section.get("json")
                or section.get("artifact")
                or ""
            ).strip()
            if text:
                values.append(text)
            benchmark_jsons = section.get("benchmark_jsons")
            if isinstance(benchmark_jsons, list):
                values.extend(
                    str(item).strip()
                    for item in benchmark_jsons
                    if str(item or "").strip()
                )
        elif isinstance(section, str) and section.strip():
            values.append(section.strip())
    return values


def _p5_g22_payload_failures(
    payload: dict[str, Any] | None,
    *,
    loaded: Sequence[dict[str, Any]],
    customer_manifest_path: Path | None,
    min_total_pairs: int,
    max_total_pairs: int,
    require_customer_grade: bool,
) -> list[str]:
    if not isinstance(payload, dict):
        return ["p5_g22_actual_gui_soak JSON missing or unreadable"]
    failures: list[str] = []
    if payload.get("schema_version") != 1:
        failures.append("schema_version must be 1")
    if payload.get("benchmark_id") != P5_G22_BENCHMARK_ID:
        failures.append(f"benchmark_id must be {P5_G22_BENCHMARK_ID}")
    if payload.get("profile") != P5_G22_PROFILE:
        failures.append(f"profile must be {P5_G22_PROFILE}")
    if payload.get("status") != "passed":
        failures.append(f"status={payload.get('status') or '<missing>'}")

    gates = payload.get("gates")
    if not isinstance(gates, list):
        failures.append("gates[] missing")
        gate_by_name: dict[str, dict[str, Any]] = {}
    else:
        gate_by_name = {
            str(gate.get("name") or ""): gate
            for gate in gates
            if isinstance(gate, dict) and str(gate.get("name") or "")
        }
        missing = sorted(P5_G22_REQUIRED_GATES - set(gate_by_name))
        if missing:
            failures.append("required gates missing: " + ", ".join(missing))
        failed = sorted(
            gate_name
            for gate_name, gate in gate_by_name.items()
            if gate.get("required") is not False and gate.get("passed") is not True
        )
        if failed:
            failures.append("required gates failed: " + ", ".join(failed))

    args_payload = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    environment = payload.get("environment") if isinstance(payload.get("environment"), dict) else {}
    corpus = payload.get("corpus") if isinstance(payload.get("corpus"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    native_summary = (
        summary.get("native_resource_summary")
        if isinstance(summary.get("native_resource_summary"), dict)
        else {}
    )
    worker_summary = (
        summary.get("worker_tree_summary")
        if isinstance(summary.get("worker_tree_summary"), dict)
        else {}
    )

    if require_customer_grade:
        if args_payload.get("require_customer_corpus") is not True:
            failures.append("args.require_customer_corpus must be true")
        if args_payload.get("skip_zone_render_workers") is True:
            failures.append("args.skip_zone_render_workers must be false for customer_grade")
        if corpus.get("evidence_level") != "customer_grade":
            failures.append("corpus.evidence_level must be customer_grade")
        sheet_count = _int(corpus.get("sheet_count"))
        if sheet_count < int(min_total_pairs) or sheet_count > int(max_total_pairs):
            failures.append(f"corpus.sheet_count={sheet_count} outside {min_total_pairs}-{max_total_pairs}")
        if environment.get("allow_missing_psutil") is True:
            failures.append("environment.allow_missing_psutil must be false")
        if environment.get("psutil_available") is not True:
            failures.append("environment.psutil_available must be true")
        if environment.get("allow_missing_native_resources") is True:
            failures.append("environment.allow_missing_native_resources must be false")
        if not native_summary:
            failures.append("summary.native_resource_summary missing")
        elif native_summary.get("measurement_available") is not True:
            failures.append("summary.native_resource_summary.measurement_available must be true")
        if not worker_summary:
            failures.append("summary.worker_tree_summary missing")
        else:
            if worker_summary.get("cleanup_ok") is not True:
                failures.append("summary.worker_tree_summary.cleanup_ok must be true")
            if _int(worker_summary.get("orphan_worker_count")) != 0:
                failures.append(
                    "summary.worker_tree_summary.orphan_worker_count="
                    f"{_int(worker_summary.get('orphan_worker_count'))} must be 0"
                )
        source_validation = source.get("validation_summary") if isinstance(source.get("validation_summary"), dict) else {}
        validation_sha = str(source_validation.get("sha256") or "").strip()
        current_validation_hashes = {
            _file_sha256(item.get("summary_path"))
            for item in loaded
            if isinstance(item.get("summary_path"), Path)
        }
        current_validation_hashes.discard("")
        if not validation_sha:
            failures.append("source.validation_summary.sha256 missing")
        elif validation_sha not in current_validation_hashes:
            failures.append("source.validation_summary.sha256 does not match audited validation_summary.json")
        manifest_sha = str(corpus.get("manifest_sha256") or "").strip()
        current_manifest_sha = _file_sha256(customer_manifest_path)
        if current_manifest_sha:
            if not manifest_sha:
                failures.append("corpus.manifest_sha256 missing")
            elif manifest_sha != current_manifest_sha:
                failures.append("corpus.manifest_sha256 does not match customer evidence manifest")

    if summary.get("gui_soak_completed") is not True:
        failures.append("summary.gui_soak_completed must be true")
    if summary.get("rss_measurement_available") is not True:
        failures.append("summary.rss_measurement_available must be true")
    if summary.get("native_resource_measurement_available") is not True:
        failures.append("summary.native_resource_measurement_available must be true")
    if native_summary and native_summary.get("measurement_available") is not summary.get("native_resource_measurement_available"):
        failures.append("summary.native_resource_summary contradicts native_resource_measurement_available")
    if summary.get("worker_cleanup_ok") is not True:
        failures.append("summary.worker_cleanup_ok must be true")
    if worker_summary and worker_summary.get("cleanup_ok") is not summary.get("worker_cleanup_ok"):
        failures.append("summary.worker_tree_summary.cleanup_ok contradicts worker_cleanup_ok")
    if _int(summary.get("completed_visit_count")) < _int(summary.get("visit_count")):
        failures.append("summary.completed_visit_count must be >= summary.visit_count")
    for key in (
        "blank_view_count",
        "stale_active_pair_count",
        "stale_active_zone_count",
        "viewer_perf_stale_count",
        "orphan_worker_count",
    ):
        value = _int(summary.get(key))
        if value != 0:
            failures.append(f"summary.{key}={value} must be 0")
    if worker_summary and _int(worker_summary.get("orphan_worker_count")) != _int(summary.get("orphan_worker_count")):
        failures.append("summary.worker_tree_summary.orphan_worker_count contradicts orphan_worker_count")
    if _int(summary.get("zone_selection_count")) <= 0:
        failures.append("summary.zone_selection_count must be > 0")
    event_loop_gap = summary.get("event_loop_gap_ms") if isinstance(summary.get("event_loop_gap_ms"), dict) else {}
    if _int(event_loop_gap.get("over_500ms_count")) != 0:
        failures.append(f"summary.event_loop_gap_ms.over_500ms_count={_int(event_loop_gap.get('over_500ms_count'))} must be 0")
    return failures


def _result_reference_path(item: dict[str, Any], value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        root = item.get("path")
        if not isinstance(root, Path):
            return None
        path = root / path
    return path if path.exists() else None


def _file_sha256(path: Any) -> str:
    if not isinstance(path, Path) or not path.exists():
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _check_preflight(summaries: Sequence[dict[str, Any]]) -> AuditCheck:
    failures: list[str] = []
    for summary in summaries:
        label = str(summary.get("output_dir") or "<unknown>")
        result = summary.get("preflight_result")
        if not isinstance(result, dict):
            failures.append(f"{label}: missing preflight_result")
            continue
        if result.get("status") != "passed":
            failures.append(f"{label}: preflight status={result.get('status') or '<missing>'}")
        checks = result.get("checks")
        if not isinstance(checks, list):
            failures.append(f"{label}: missing preflight checks[]")
            continue
        present = {str(check.get("name") or "") for check in checks if isinstance(check, dict)}
        missing = sorted(REQUIRED_PREFLIGHT_CHECKS - present)
        if missing:
            failures.append(f"{label}: missing preflight checks {missing}")
    return AuditCheck(
        name="preflight_passed",
        passed=not failures and bool(summaries),
        detail=(
            "all validation outputs include passed preflight with required operational checks"
            if not failures
            else "; ".join(failures)
        ),
        evidence=[str(_nested(summary, "outputs", "preflight_report_json") or "") for summary in summaries],
    )


def _check_ai_optional_fallback(summaries: Sequence[dict[str, Any]]) -> AuditCheck:
    passed_labels: list[str] = []
    missing_labels: list[str] = []
    failures: list[str] = []
    evidence: list[str] = []
    completed_seen = 0
    for summary in summaries:
        if _int(_nested(summary, "comparison", "completed_pairs")) <= 0:
            continue
        completed_seen += 1
        label = str(summary.get("output_dir") or "<validation_summary>")
        evidence_path = str(_nested(summary, "outputs", "ai_policy_json") or "").strip()
        if evidence_path:
            evidence.append(evidence_path)
        policy = summary.get("ai_policy")
        if not isinstance(policy, dict):
            missing_labels.append(label)
            continue
        issues = _ai_policy_issues(policy)
        if issues:
            failures.append(f"{label}: " + ", ".join(issues))
        else:
            passed_labels.append(label)
    ok = completed_seen > 0 and bool(passed_labels) and not failures
    if ok and missing_labels:
        failures.append(f"missing_ai_policy_on_other_completed_outputs={len(missing_labels)}")
    return AuditCheck(
        name="ai_optional_heuristic_fallback",
        passed=ok,
        detail=(
            f"AI is optional; missing models fall back to heuristic with warning "
            f"for completed_outputs={len(passed_labels)}"
            + (f", {failures[-1]}" if missing_labels else "")
            if ok
            else (
                "no completed validation outputs for AI policy evidence"
                if completed_seen <= 0
                else "; ".join(failures or [f"missing ai_policy evidence on {len(missing_labels)} completed outputs"])
            )
        ),
        evidence=evidence[:10],
    )


def _ai_policy_issues(policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if policy.get("status") != "passed":
        issues.append(f"status={policy.get('status') or '<missing>'}")
    if policy.get("ai_required") is not False:
        issues.append("ai_required must be false")
    if policy.get("embedding_optional") is not True:
        issues.append("embedding_optional must be true")
    if policy.get("llm_optional") is not True:
        issues.append("llm_optional must be true")
    if policy.get("heuristic_fallback_available") is not True:
        issues.append("heuristic_fallback_available must be true")
    if str(policy.get("model_missing_handling") or "") != "warning":
        issues.append("model_missing_handling must be warning")
    warning = str(policy.get("warning_ko") or "")
    if "모델 없음" not in warning or "휴리스틱" not in warning:
        issues.append("warning_ko must state missing model heuristic fallback")
    fallback = policy.get("fallback_without_model")
    if not isinstance(fallback, dict):
        issues.append("fallback_without_model missing")
    else:
        if fallback.get("configured_embedding") is not True:
            issues.append("fallback_without_model.configured_embedding must be true")
        if str(fallback.get("classifier_used") or "") != "heuristic":
            issues.append("fallback_without_model.classifier_used must be heuristic")
        if _int(fallback.get("result_count")) <= 0:
            issues.append("fallback_without_model.result_count must be > 0")
    heuristic = policy.get("heuristic_only")
    if not isinstance(heuristic, dict):
        issues.append("heuristic_only missing")
    elif str(heuristic.get("classifier_used") or "") != "heuristic":
        issues.append("heuristic_only.classifier_used must be heuristic")
    return issues


def _check_sharable_audit(summaries: Sequence[dict[str, Any]]) -> AuditCheck:
    failures: list[str] = []
    for summary in summaries:
        label = str(summary.get("output_dir") or "<unknown>")
        sharable_audit = summary.get("sharable_audit")
        if not isinstance(sharable_audit, dict) or "leak_count" not in sharable_audit:
            failures.append(f"{label}: missing sharable_audit.leak_count")
            continue
        leak_count = _int(sharable_audit.get("leak_count"))
        if leak_count != 0:
            failures.append(f"{label}: leak_count={leak_count}")
    return AuditCheck(
        name="sharable_path_leakage_zero",
        passed=not failures and bool(summaries),
        detail=(
            "all validation outputs include sharable_audit.leak_count=0"
            if not failures
            else "; ".join(failures)
        ),
        evidence=[str(_nested(summary, "sharable_audit", "audited_at") or "") for summary in summaries],
    )


def _check_raw_streams_absent(loaded: Sequence[dict[str, Any]]) -> AuditCheck:
    raw_streams: list[str] = []
    for item in loaded:
        root = item.get("path") if isinstance(item, dict) else None
        if not root:
            continue
        root_path = Path(root)
        for path in root_path.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".jsonl", ".ndjson"}:
                raw_streams.append(str(path))
    return AuditCheck(
        name="sharable_raw_jsonl_streams_absent",
        passed=not raw_streams and bool(loaded),
        detail=(
            "no raw JSONL/NDJSON streams found in audited sharable outputs"
            if not raw_streams
            else f"raw stream files found: {len(raw_streams)}"
        ),
        evidence=raw_streams[:10],
    )


def _check_review_queue_schema(queue_items: Sequence[dict[str, Any]]) -> AuditCheck:
    issues: list[str] = []
    seen_unit_keys: dict[tuple[str, str], str] = {}
    for item in queue_items:
        absent = sorted(field for field in REQUIRED_QUEUE_FIELDS if field not in item)
        if absent:
            issues.append(f"{item.get('queue_key') or item.get('zone_id')}: {absent}")
            continue
        pair_uuid = str(item.get("pair_uuid") or "").strip()
        zone_id = str(item.get("zone_id") or "").strip()
        queue_key = str(item.get("queue_key") or "").strip()
        label = queue_key or f"{pair_uuid}:{zone_id}"
        if not pair_uuid:
            issues.append(f"{label}: pair_uuid is empty")
        if not zone_id:
            issues.append(f"{label}: zone_id is empty")
        expected_queue_key = f"{pair_uuid}:{zone_id}" if pair_uuid and zone_id else ""
        if queue_key and expected_queue_key and queue_key != expected_queue_key:
            issues.append(f"{label}: queue_key must equal pair_uuid:zone_id ({expected_queue_key})")
        review_status = str(item.get("review_status") or "").strip()
        if review_status not in REVIEW_STATUS_VALUES:
            issues.append(f"{label}: review_status must be one of {sorted(REVIEW_STATUS_VALUES)}")
        source_format = str(item.get("source_format") or "").strip().lower()
        detection_source = str(item.get("detection_source") or "").strip().lower()
        bbox_status = str(item.get("bbox_status") or "").strip().lower()
        if source_format not in SOURCE_FORMAT_VALUES:
            issues.append(f"{label}: source_format must be one of {sorted(SOURCE_FORMAT_VALUES)}")
        elif source_format == "cad" and detection_source not in CAD_DETECTION_SOURCES:
            issues.append(f"{label}: CAD detection_source must be cad_entity")
        elif source_format == "pdf" and detection_source not in PDF_DETECTION_SOURCES:
            issues.append(f"{label}: PDF detection_source must be one of {sorted(PDF_DETECTION_SOURCES)}")
        if bbox_status not in BBOX_STATUS_VALUES:
            issues.append(f"{label}: bbox_status must be one of {sorted(BBOX_STATUS_VALUES)}")
        if pair_uuid and zone_id:
            unit_key = (pair_uuid, zone_id)
            previous = seen_unit_keys.get(unit_key)
            if previous:
                issues.append(f"{label}: duplicate review_queue unit {pair_uuid}:{zone_id} (previous={previous})")
            else:
                seen_unit_keys[unit_key] = label
    return AuditCheck(
        name="review_queue_required_fields",
        passed=bool(queue_items) and not issues,
        detail=(
            f"{len(queue_items)} queue item(s) include required MVP fields, canonical "
            "pair_uuid:zone_id unit keys, unique units, and valid metadata/status values"
            if not issues
            else "; ".join(issues[:10])
        ),
        evidence=[
            str(item.get("queue_key") or f"{item.get('pair_uuid') or ''}:{item.get('zone_id') or ''}")
            for item in queue_items[:10]
        ],
    )


def _check_top_issue_policy(summaries: Sequence[dict[str, Any]]) -> AuditCheck:
    failures: list[str] = []
    evidence: list[str] = []
    completed_count = 0
    for summary in summaries:
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_count += 1
        label = str(summary.get("output_dir") or "<validation_summary>")
        queue = _nested(summary, "review_dashboard", "review_queue") or {}
        items = queue.get("items") if isinstance(queue, dict) else None
        top_per = _int(queue.get("top_per_drawing"))
        mode = str(queue.get("mode") or "").strip()
        item_count = len(items) if isinstance(items, list) else 0
        evidence.append(
            f"{label}: completed_pairs={completed_pairs}, mode={mode or '<missing>'}, "
            f"top_per_drawing={top_per}, items={item_count}"
        )
        if mode != "structural_core":
            failures.append(f"{label}: review_queue.mode={mode or '<missing>'}")
        if not (3 <= top_per <= 5):
            failures.append(f"{label}: top_per_drawing={top_per}, expected 3..5")
        if item_count <= 0:
            failures.append(f"{label}: first-screen review_queue items are empty")
    ok = completed_count > 0 and not failures
    return AuditCheck(
        name="top_structural_review_queue_first",
        passed=ok,
        detail=(
            f"{completed_count} completed output(s) expose structural_core Top 3-5 review_queue first"
            if ok
            else ("; ".join(failures[:10]) if failures else "no completed output top review_queue evidence")
        ),
        evidence=evidence,
    )


def _check_korean_review_text(queue_items: Sequence[dict[str, Any]]) -> AuditCheck:
    ok_items = [
        item
        for item in queue_items
        if str(item.get("reason_ko") or "").strip()
        and str(item.get("change_summary_ko") or "").strip()
    ]
    return AuditCheck(
        name="korean_reason_and_summary",
        passed=bool(ok_items),
        detail=f"{len(ok_items)} queue item(s) have reason_ko and change_summary_ko",
        evidence=[str(item.get("queue_key") or "") for item in ok_items[:10]],
    )


def _check_cad_support(summaries: Sequence[dict[str, Any]], queue_items: Sequence[dict[str, Any]]) -> AuditCheck:
    cad_pair = _has_cad_pair(summaries)
    extension_evidence = _cad_extension_evidence(summaries)
    dwg_evidence = bool(extension_evidence.get("dwg"))
    dxf_evidence = bool(extension_evidence.get("dxf"))
    cad_queue = any(
        str(item.get("source_format") or "").lower() == "cad"
        and str(item.get("detection_source") or "").lower() == "cad_entity"
        for item in queue_items
    )
    return AuditCheck(
        name="dwg_dxf_cad_support",
        passed=cad_pair and cad_queue and dwg_evidence and dxf_evidence,
        detail=(
            f"cad_pair={cad_pair}, cad_review_queue={cad_queue}, "
            f"dwg_evidence={dwg_evidence}, dxf_evidence={dxf_evidence}"
        ),
        evidence=[
            f".{extension}: {', '.join(paths[:5])}"
            for extension, paths in sorted(extension_evidence.items())
            if paths
        ],
    )


def _check_cad_structural_text_policy(queue_items: Sequence[dict[str, Any]]) -> AuditCheck:
    evidence_items: list[dict[str, Any]] = []
    for item in queue_items:
        if str(item.get("source_format") or "").lower() != "cad":
            continue
        if str(item.get("detection_source") or "").lower() != "cad_entity":
            continue
        entity_types = _entity_types(item.get("entity_types"))
        if not (entity_types & CAD_STRUCTURAL_TEXT_ENTITY_TYPES):
            continue
        summary = str(item.get("change_summary_ko") or "").lower()
        reason = str(item.get("reason_ko") or "").lower()
        corpus = f"{summary} {reason}"
        has_spacing_pair = "@100" in corpus and "@200" in corpus
        grouped_as_modified = (
            _int(item.get("modified_count")) > 0
            and _int(item.get("added_count")) == 0
            and _int(item.get("deleted_count")) == 0
        )
        if has_spacing_pair and grouped_as_modified:
            evidence_items.append(item)

    return AuditCheck(
        name="cad_structural_text_modified_grouping",
        passed=bool(evidence_items),
        detail=(
            "CAD structural text spacing evidence is cad_entity and grouped as modified"
            if evidence_items
            else (
                "missing CAD cad_entity review_queue item with TEXT/MTEXT/ATTRIB/ATTDEF/INSERT "
                "@100->@200 evidence grouped as modified_count>0 with added/deleted=0"
            )
        ),
        evidence=[
            f"{item.get('queue_key') or item.get('zone_id')}: {item.get('entity_types')}"
            for item in evidence_items[:10]
        ],
    )


def _check_cad_block_text_without_expansion(summaries: Sequence[dict[str, Any]]) -> AuditCheck:
    evidence: list[str] = []
    for summary in summaries:
        if not (
            _kind_count(summary, "a_kind_counts", "cad") > 0
            and _kind_count(summary, "b_kind_counts", "cad") > 0
            and _int(_nested(summary, "comparison", "completed_pairs")) > 0
        ):
            continue
        cad_policy = _nested(summary, "input", "cad_policy")
        if not isinstance(cad_policy, dict):
            continue
        if cad_policy.get("expand_blocks") is not False:
            continue
        if cad_policy.get("block_text_detection") is not True:
            continue
        for item in _queue_items(summary):
            if str(item.get("source_format") or "").lower() != "cad":
                continue
            if str(item.get("detection_source") or "").lower() != "cad_entity":
                continue
            entity_types = _entity_types(item.get("entity_types"))
            if not (entity_types & CAD_STRUCTURAL_TEXT_ENTITY_TYPES):
                continue
            corpus = f"{item.get('change_summary_ko') or ''} {item.get('reason_ko') or ''}"
            grouped_as_modified = (
                _int(item.get("modified_count")) > 0
                and _int(item.get("added_count")) == 0
                and _int(item.get("deleted_count")) == 0
            )
            if "@100" in corpus and "@200" in corpus and grouped_as_modified:
                label = str(summary.get("output_dir") or "<validation_summary>")
                evidence.append(
                    f"{label}: {item.get('queue_key') or item.get('zone_id')} "
                    f"entity_types={item.get('entity_types')}"
                )
                break
    return AuditCheck(
        name="cad_block_text_detection_without_expansion",
        passed=bool(evidence),
        detail=(
            "CAD block attribute/text spacing change is detected with expand_blocks=False and block_text_detection=True"
            if evidence
            else (
                "missing completed CAD validation where input.cad_policy.expand_blocks=false, "
                "input.cad_policy.block_text_detection=true, and a CAD structural text @100->@200 "
                "review_queue item is grouped as modified"
            )
        ),
        evidence=evidence[:10],
    )


def _check_pdf_support(summaries: Sequence[dict[str, Any]], queue_items: Sequence[dict[str, Any]]) -> AuditCheck:
    pdf_pair = _has_pdf_pair(summaries)
    pdf_source_evidence = _pdf_source_evidence(summaries)
    pdf_extension_evidence = bool(pdf_source_evidence)
    pdf_queue = any(
        str(item.get("source_format") or "").lower() == "pdf"
        and str(item.get("detection_source") or "").lower() in PDF_DETECTION_SOURCES
        for item in queue_items
    )
    return AuditCheck(
        name="pdf_pdf_support",
        passed=pdf_pair and pdf_queue and pdf_extension_evidence,
        detail=(
            f"pdf_pair={pdf_pair}, pdf_review_queue={pdf_queue}, "
            f"pdf_source_evidence={pdf_extension_evidence}"
        ),
        evidence=pdf_source_evidence[:10],
    )


def _check_pdf_bbox_coordinate_policy(loaded: Sequence[dict[str, Any]]) -> AuditCheck:
    evidence: list[str] = []
    failures: list[str] = []
    pdf_output_count = 0
    pdf_row_count = 0
    allowed_status = {"exact", "page_fallback", "relative_only"}
    for item in loaded:
        summary = item.get("summary") or {}
        if not (
            _kind_count(summary, "a_kind_counts", "pdf") > 0
            and _kind_count(summary, "b_kind_counts", "pdf") > 0
            and _int(_nested(summary, "comparison", "completed_pairs")) > 0
        ):
            continue
        pdf_output_count += 1
        csv_path = _change_zones_csv_path(item)
        rows = _read_csv_rows(csv_path)
        pdf_rows = [row for row in rows if _row_is_pdf_change_zone(row)]
        if not pdf_rows:
            failures.append(f"{csv_path}: no PDF change-zone rows")
            continue
        for row in pdf_rows:
            pdf_row_count += 1
            label = str(row.get("zone_id") or row.get("pair_id") or csv_path)
            source = str(row.get("detection_source") or "").lower()
            status = str(row.get("bbox_status") or "").lower()
            space = str(row.get("bbox_coordinate_space") or "").lower()
            if source not in PDF_DETECTION_SOURCES:
                failures.append(f"{csv_path}:{label}: detection_source={source or '<missing>'}")
            if status not in allowed_status:
                failures.append(f"{csv_path}:{label}: bbox_status={status or '<missing>'}")
            if space != "image_pixels":
                failures.append(f"{csv_path}:{label}: bbox_coordinate_space={space or '<missing>'}")
        evidence.append(f"{csv_path}: pdf_rows={len(pdf_rows)}")

    ok = pdf_output_count > 0 and pdf_row_count > 0 and not failures
    return AuditCheck(
        name="pdf_bbox_image_pixels_policy",
        passed=ok,
        detail=(
            f"{pdf_row_count} PDF change-zone row(s) use image_pixels bbox coordinates"
            if ok
            else (
                "; ".join(failures[:10])
                if failures
                else "no completed PDF output with PDF change-zone bbox coordinate evidence"
            )
        ),
        evidence=evidence,
    )


def _check_cad_pdf_blocking(summaries: Sequence[dict[str, Any]], loaded: Sequence[dict[str, Any]]) -> AuditCheck:
    summary_evidence = any(_int(_nested(summary, "matching", "cad_pdf_blocked_pairs")) for summary in summaries)
    csv_evidence = [
        str(item["path"] / "blocked_pairs.csv")
        for item in loaded
        if _blocked_csv_has_cad_pdf(item["path"] / "blocked_pairs.csv")
    ]
    blocked = bool(csv_evidence)
    return AuditCheck(
        name="cad_pdf_cross_compare_blocked",
        passed=blocked,
        detail=(
            f"explicit CAD-PDF blocked_pairs.csv evidence exists, summary_count_present={summary_evidence}"
            if blocked
            else "no explicit CAD-PDF blocked_pairs.csv row with CAD/PDF kinds and clear blocked reason found"
        ),
        evidence=csv_evidence,
    )


def _check_structural_review_recall(
    summaries: Sequence[dict[str, Any]],
    *,
    min_structural_review_recall: float,
) -> AuditCheck:
    recall_values = [
        float(_nested(summary, "quality", "structural_review_recall"))
        for summary in summaries
        if _nested(summary, "quality", "structural_review_recall") is not None
    ]
    ok = bool(recall_values) and max(recall_values) >= min_structural_review_recall
    return AuditCheck(
        name="structural_review_queue_recall",
        passed=ok,
        detail=(
            f"max structural_review_recall={max(recall_values):.3f}"
            if recall_values
            else "no --review-ground-truth recall evidence"
        ),
        evidence=[str(value) for value in recall_values],
    )


def _check_structural_coverage(
    summaries: Sequence[dict[str, Any]],
    queue_items: Sequence[dict[str, Any]],
    *,
    required: Sequence[str],
) -> AuditCheck:
    corpus = _structural_coverage_corpus(summaries, queue_items)
    present: list[str] = []
    missing: list[str] = []
    for bucket in required:
        terms = STRUCTURAL_COVERAGE_TERMS.get(bucket, ())
        if any(term.lower() in corpus for term in terms):
            present.append(bucket)
        else:
            missing.append(bucket)
    return AuditCheck(
        name="structural_core_coverage",
        passed=not missing,
        detail=(
            f"covered={present}"
            if not missing
            else f"missing={missing}; covered={present}"
        ),
        evidence=present,
    )


def _structural_coverage_corpus(
    summaries: Sequence[dict[str, Any]],
    queue_items: Sequence[dict[str, Any]],
) -> str:
    chunks: list[str] = []
    for item in queue_items:
        chunks.extend(
            str(item.get(key) or "")
            for key in (
                "category",
                "reason_ko",
                "change_summary_ko",
                "major_layers",
                "entity_types",
                "detection_source",
            )
        )
    for summary in summaries:
        truth = summary.get("review_ground_truth") or {}
        for detail in truth.get("details") or []:
            if not isinstance(detail, dict):
                continue
            chunks.extend(str(detail.get(key) or "") for key in ("category", "summary_contains", "drawing_label"))
    return " ".join(chunks).lower()


def _check_scale(
    summaries: Sequence[dict[str, Any]],
    *,
    min_total_pairs: int,
    max_total_pairs: int,
) -> AuditCheck:
    completed = sum(_int(_nested(summary, "comparison", "completed_pairs")) for summary in summaries)
    passed = min_total_pairs <= completed <= max_total_pairs
    return AuditCheck(
        name="twenty_to_fifty_sheet_scale",
        passed=passed,
        detail=f"completed_pairs={completed}, required={min_total_pairs}..{max_total_pairs}",
        evidence=[str(summary.get("output_dir") or "") for summary in summaries],
    )


def _check_first_review_ready_perf(
    summaries: Sequence[dict[str, Any]],
    *,
    max_first_review_ready_s: float,
) -> AuditCheck:
    evidence: list[str] = []
    failures: list[str] = []
    completed_outputs = 0
    for summary in summaries:
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_outputs += 1
        label = str(summary.get("output_dir") or "<validation_summary>")
        total_s = _float(_nested(summary, "timings", "total_s"))
        review_dashboard_path = str(_nested(summary, "outputs", "review_dashboard_json") or "").strip()
        viewer_manifest_path = str(_nested(summary, "outputs", "viewer_manifest_json") or "").strip()
        review_queue = _nested(summary, "review_dashboard", "review_queue")
        queue_ready = isinstance(review_queue, dict) and bool(review_queue.get("items"))
        viewer_ready = bool(viewer_manifest_path or _nested(summary, "viewer_package", "viewer_manifest"))
        dashboard_ready = bool(review_dashboard_path and queue_ready)
        evidence.append(
            f"{label}: completed_pairs={completed_pairs}, total_s={total_s}, "
            f"review_dashboard={dashboard_ready}, viewer_metadata={viewer_ready}"
        )
        if total_s <= 0:
            failures.append(f"{label}: missing timings.total_s")
        elif total_s > max_first_review_ready_s:
            failures.append(f"{label}: total_s={total_s} > {max_first_review_ready_s}")
        if not dashboard_ready:
            failures.append(f"{label}: missing first review dashboard/review_queue metadata")
        if not viewer_ready:
            failures.append(f"{label}: missing viewer metadata")

    ok = completed_outputs > 0 and not failures
    return AuditCheck(
        name="first_review_ready_within_30min",
        passed=ok,
        detail=(
            f"{completed_outputs} completed output(s) produced review dashboard and viewer metadata "
            f"within {max_first_review_ready_s}s"
            if ok
            else ("; ".join(failures) if failures else "no completed output timing evidence")
        ),
        evidence=evidence,
    )


def _check_runtime_budget(
    summaries: Sequence[dict[str, Any]],
    *,
    require_runtime_budget: bool,
    max_peak_working_set_mb: float | None,
    max_runtime_first_review_ready_s: float | None,
    max_peak_disk_spool_mb: float | None,
    max_peak_comparator_changes: int | None = None,
    max_time_to_first_stream_record_ms: float | None = None,
    # Plan §17 A-3 (GPT Pro F5) — cross-platform RSS ceiling, distinct from
    # max_peak_working_set_mb which is Windows wset-specific.
    max_peak_rss_mb: float | None = None,
) -> AuditCheck:
    """RuntimeBudget 측정값 직접 검증 (외부 감사 권고 #1).

    기존 ``timings.total_s`` / ``change_records_in_memory`` 가 proxy 라는
    지적에 대응. ``runtime_budget`` 블록은 validator 가
    ``RuntimeBudgetSampler.stop()`` 결과를 그대로 직렬화한다고 가정한다.

    동작:
    - ``require_runtime_budget=True`` 이면 모든 completed output 이
      runtime_budget 블록을 보유해야 함.
    - ``max_peak_working_set_mb`` / ``max_runtime_first_review_ready_s`` /
      ``max_peak_disk_spool_mb`` 는 ``None`` 일 때 비활성, 명시 시 strict.
    - 측정 자체가 실패한 case (sampler_active=False 인데 require=True) 도
      실패로 처리.
    """

    failures: list[str] = []
    evidence: list[str] = []
    completed_outputs_with_budget = 0
    completed_outputs_total = 0

    for summary in summaries:
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_outputs_total += 1
        label = str(summary.get("output_dir") or "<validation_summary>")
        budget = _nested(summary, "runtime_budget")
        if not isinstance(budget, dict) or not budget:
            if require_runtime_budget:
                failures.append(f"{label}: runtime_budget block missing")
            evidence.append(f"{label}: runtime_budget=missing")
            continue

        completed_outputs_with_budget += 1
        peak_ws = _optional_float_value(budget.get("peak_working_set_mb"))
        peak_rss = _optional_float_value(budget.get("peak_rss_mb"))
        peak_spool = _optional_float_value(budget.get("peak_disk_spool_mb"))
        first_ready = _optional_float_value(budget.get("first_review_ready_s"))
        sampler_active = bool(budget.get("sampler_active"))
        sample_count = _int(budget.get("sample_count"))
        # Plan §16 Phase C-2.3 — new comparator-derived metrics
        peak_comparator_changes_raw = budget.get("peak_comparator_changes")
        peak_comparator_changes: int | None
        try:
            peak_comparator_changes = (
                int(peak_comparator_changes_raw)
                if peak_comparator_changes_raw is not None
                else None
            )
        except (TypeError, ValueError):
            peak_comparator_changes = None
        time_to_first_stream_record_ms = _optional_float_value(
            budget.get("time_to_first_stream_record_ms")
        )
        native_available = budget.get("native_resource_available") is True
        native_sample_count = _int(budget.get("native_resource_sample_count"))
        native_platform = str(budget.get("native_resource_platform") or "").lower()
        peak_process_handle_count = _optional_int_value(budget.get("peak_process_handle_count"))
        peak_open_fd_count = _optional_int_value(budget.get("peak_open_file_descriptor_count"))
        peak_gdi_handle_count = _optional_int_value(budget.get("peak_gdi_handle_count"))
        peak_user_handle_count = _optional_int_value(budget.get("peak_user_handle_count"))
        final_worker_process_count = _optional_int_value(budget.get("final_worker_process_count"))
        process_handle_delta = _optional_int_value(budget.get("process_handle_positive_delta"))
        open_fd_delta = _optional_int_value(budget.get("open_file_descriptor_positive_delta"))
        gdi_delta = _optional_int_value(budget.get("gdi_handle_positive_delta"))
        user_delta = _optional_int_value(budget.get("user_handle_positive_delta"))
        worker_delta = _optional_int_value(budget.get("worker_process_positive_delta"))

        evidence.append(
            f"{label}: peak_ws_mb={peak_ws}, peak_rss_mb={peak_rss}, "
            f"peak_spool_mb={peak_spool}, first_review_ready_s={first_ready}, "
            f"sampler_active={sampler_active}, sample_count={sample_count}, "
            f"peak_comparator_changes={peak_comparator_changes}, "
            f"time_to_first_stream_record_ms={time_to_first_stream_record_ms}, "
            f"native_available={native_available}, native_sample_count={native_sample_count}, "
            f"peak_process_handles={peak_process_handle_count}, peak_fds={peak_open_fd_count}, "
            f"peak_gdi={peak_gdi_handle_count}, peak_user={peak_user_handle_count}, "
            f"final_worker_process_count={final_worker_process_count}"
        )

        if require_runtime_budget and not sampler_active:
            failures.append(
                f"{label}: runtime_budget.sampler_active=false (require_runtime_budget enforced)"
            )
        if require_runtime_budget and sample_count <= 0:
            failures.append(
                f"{label}: runtime_budget.sample_count={sample_count} (require >0 when enforced)"
            )
        if require_runtime_budget:
            if not native_available:
                failures.append(
                    f"{label}: runtime_budget.native_resource_available=false "
                    "(require_runtime_budget enforced)"
                )
            if native_sample_count <= 0:
                failures.append(
                    f"{label}: runtime_budget.native_resource_sample_count="
                    f"{native_sample_count} (require >0 when enforced)"
                )
            if peak_process_handle_count is None and peak_open_fd_count is None:
                failures.append(
                    f"{label}: runtime_budget peak process handle/fd metric missing"
                )
            if native_platform == "windows":
                if peak_process_handle_count is None:
                    failures.append(f"{label}: peak_process_handle_count missing on Windows")
                if peak_gdi_handle_count is None:
                    failures.append(f"{label}: peak_gdi_handle_count missing on Windows")
                if peak_user_handle_count is None:
                    failures.append(f"{label}: peak_user_handle_count missing on Windows")
            if final_worker_process_count is None:
                failures.append(f"{label}: final_worker_process_count missing")
            elif final_worker_process_count > STRICT_MAX_FINAL_WORKER_PROCESS_COUNT:
                failures.append(
                    f"{label}: final_worker_process_count={final_worker_process_count} > "
                    f"{STRICT_MAX_FINAL_WORKER_PROCESS_COUNT}"
                )

        for metric_name, value, limit in (
            ("process_handle_positive_delta", process_handle_delta, STRICT_MAX_PROCESS_HANDLE_POSITIVE_DELTA),
            ("open_file_descriptor_positive_delta", open_fd_delta, STRICT_MAX_OPEN_FILE_DESCRIPTOR_POSITIVE_DELTA),
            ("gdi_handle_positive_delta", gdi_delta, STRICT_MAX_GDI_HANDLE_POSITIVE_DELTA),
            ("user_handle_positive_delta", user_delta, STRICT_MAX_USER_HANDLE_POSITIVE_DELTA),
            ("worker_process_positive_delta", worker_delta, STRICT_MAX_WORKER_PROCESS_POSITIVE_DELTA),
        ):
            if value is not None and value > limit:
                failures.append(f"{label}: {metric_name}={value} > {limit}")

        if max_peak_working_set_mb is not None:
            effective_ws = peak_ws if peak_ws is not None else peak_rss
            if effective_ws is None:
                failures.append(
                    f"{label}: peak_working_set_mb missing (required <= {max_peak_working_set_mb})"
                )
            elif effective_ws > max_peak_working_set_mb:
                failures.append(
                    f"{label}: peak_working_set_mb={effective_ws} > {max_peak_working_set_mb}"
                )

        if max_runtime_first_review_ready_s is not None:
            if first_ready is None:
                failures.append(
                    f"{label}: first_review_ready_s missing (required <= {max_runtime_first_review_ready_s})"
                )
            elif first_ready > max_runtime_first_review_ready_s:
                failures.append(
                    f"{label}: first_review_ready_s={first_ready} > {max_runtime_first_review_ready_s}"
                )

        if max_peak_disk_spool_mb is not None and peak_spool is not None:
            if peak_spool > max_peak_disk_spool_mb:
                failures.append(
                    f"{label}: peak_disk_spool_mb={peak_spool} > {max_peak_disk_spool_mb}"
                )

        # Plan §17 A-3 (GPT Pro F5) — cross-platform RSS ceiling. Distinct
        # from max_peak_working_set_mb so an operator can apply both gates
        # together on Windows (wset + rss) or use rss alone on Linux/Mac.
        if max_peak_rss_mb is not None:
            if peak_rss is None:
                failures.append(
                    f"{label}: peak_rss_mb missing (required <= {max_peak_rss_mb})"
                )
            elif peak_rss > max_peak_rss_mb:
                failures.append(
                    f"{label}: peak_rss_mb={peak_rss} > {max_peak_rss_mb}"
                )

        # Plan §16 Phase C-2.3 — comparator peak in-flight changes gate
        # Mirrors the working_set "missing when required" pattern at 1879-1888.
        if max_peak_comparator_changes is not None:
            if peak_comparator_changes is None:
                failures.append(
                    f"{label}: peak_comparator_changes missing (required <= "
                    f"{max_peak_comparator_changes})"
                )
            elif peak_comparator_changes > max_peak_comparator_changes:
                failures.append(
                    f"{label}: peak_comparator_changes={peak_comparator_changes} > "
                    f"{max_peak_comparator_changes}"
                )

        # Plan §16 Phase C-2.3 — streaming first-write latency gate
        if max_time_to_first_stream_record_ms is not None:
            if time_to_first_stream_record_ms is None:
                failures.append(
                    f"{label}: time_to_first_stream_record_ms missing (required <= "
                    f"{max_time_to_first_stream_record_ms})"
                )
            elif time_to_first_stream_record_ms > max_time_to_first_stream_record_ms:
                failures.append(
                    f"{label}: time_to_first_stream_record_ms="
                    f"{time_to_first_stream_record_ms} > "
                    f"{max_time_to_first_stream_record_ms}"
                )

    if require_runtime_budget and completed_outputs_total == 0:
        failures.append("require_runtime_budget enforced but no completed outputs found")

    ok = (
        not failures
        and (
            completed_outputs_with_budget > 0
            or not require_runtime_budget
        )
    )
    detail_chunks: list[str] = [
        f"completed_outputs={completed_outputs_total}",
        f"with_runtime_budget={completed_outputs_with_budget}",
    ]
    if max_peak_working_set_mb is not None:
        detail_chunks.append(f"max_peak_working_set_mb={max_peak_working_set_mb}")
    if max_runtime_first_review_ready_s is not None:
        detail_chunks.append(
            f"max_runtime_first_review_ready_s={max_runtime_first_review_ready_s}"
        )
    if max_peak_disk_spool_mb is not None:
        detail_chunks.append(f"max_peak_disk_spool_mb={max_peak_disk_spool_mb}")
    # Plan §16 Phase C-2.3 — surface configured comparator thresholds
    if max_peak_comparator_changes is not None:
        detail_chunks.append(
            f"max_peak_comparator_changes={max_peak_comparator_changes}"
        )
    if max_time_to_first_stream_record_ms is not None:
        detail_chunks.append(
            f"max_time_to_first_stream_record_ms={max_time_to_first_stream_record_ms}"
        )
    if failures:
        detail_chunks.append("failures=" + "; ".join(failures))
    return AuditCheck(
        name="runtime_budget_measurement",
        passed=ok,
        detail=", ".join(detail_chunks),
        evidence=evidence,
    )


def _check_perf_events_summary(
    summaries: Sequence[dict[str, Any]],
    *,
    require_perf_events_summary: bool,
    max_perf_summary_elapsed_ms: float | None,
) -> AuditCheck:
    failures: list[str] = []
    evidence: list[str] = []
    completed_outputs_total = 0
    completed_outputs_with_perf = 0

    for summary in summaries:
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_outputs_total += 1
        label = str(summary.get("output_dir") or "<validation_summary>")
        perf = _nested(summary, "perf_events_summary")
        if not isinstance(perf, dict) or not perf:
            if require_perf_events_summary:
                failures.append(f"{label}: perf_events_summary block missing")
            evidence.append(f"{label}: perf_events_summary=missing")
            continue

        status = str(perf.get("status") or "")
        event_count = _int(perf.get("event_count"))
        summary_elapsed_ms = _optional_float_value(perf.get("summary_elapsed_ms"))
        summary_input_bytes = _int(perf.get("summary_input_bytes"))
        evidence.append(
            f"{label}: status={status}, event_count={event_count}, "
            f"summary_elapsed_ms={summary_elapsed_ms}, "
            f"summary_input_bytes={summary_input_bytes}"
        )

        if status != "ready":
            failures.append(f"{label}: perf_events_summary.status={status or '<missing>'}")
            continue
        if event_count <= 0:
            failures.append(f"{label}: perf_events_summary.event_count must be > 0")
            continue
        completed_outputs_with_perf += 1
        if max_perf_summary_elapsed_ms is not None:
            if summary_elapsed_ms is None:
                failures.append(
                    f"{label}: perf_events_summary.summary_elapsed_ms missing "
                    f"(required <= {max_perf_summary_elapsed_ms})"
                )
            elif summary_elapsed_ms > max_perf_summary_elapsed_ms:
                failures.append(
                    f"{label}: perf_events_summary.summary_elapsed_ms="
                    f"{summary_elapsed_ms} > {max_perf_summary_elapsed_ms}"
                )

    if require_perf_events_summary and completed_outputs_total == 0:
        failures.append("require_perf_events_summary enforced but no completed outputs found")

    detail_chunks = [
        f"completed_outputs={completed_outputs_total}",
        f"with_perf_events_summary={completed_outputs_with_perf}",
    ]
    if max_perf_summary_elapsed_ms is not None:
        detail_chunks.append(f"max_perf_summary_elapsed_ms={max_perf_summary_elapsed_ms}")
    if failures:
        detail_chunks.append("failures=" + "; ".join(failures))
    ok = (
        not failures
        and (
            completed_outputs_with_perf > 0
            or not require_perf_events_summary
        )
    )
    return AuditCheck(
        name="perf_events_summary_measurement",
        passed=ok,
        detail=", ".join(detail_chunks),
        evidence=evidence,
    )


def _optional_float_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _check_actual_crop_rate(
    summaries: Sequence[dict[str, Any]],
    loaded: Sequence[dict[str, Any]],
    *,
    require_actual_crop_rate_pdf: float | None,
    require_actual_crop_rate_cad: float | None,
    require_actual_crop_rate_overall: float | None,
) -> AuditCheck:
    """Selected-zone actual_crop rate 게이트 (외부 감사 권고 #2).

    기존 acceptance 9b 가 ``synchronized_relative_fallback`` 만 있어도
    pass 가능했던 약점을 해소. zone_render_outcome.classify_render_result()
    가 derived 분류를 수행하고 본 게이트가 비율을 강제한다.

    Source: ``summary["selected_zone_evidence"]["renders"]`` 또는 디스크의
    ``selected_zone_evidence.json``. 둘 다 부재하고 게이트가 enforced 일
    때 실패.
    """
    from src.services.comparison.zone_render_outcome import aggregate_zone_outcomes

    failures: list[str] = []
    evidence: list[str] = []
    aggregated_total = 0
    aggregated_actual = 0
    aggregated_pdf_total = 0
    aggregated_pdf_actual = 0
    aggregated_cad_total = 0
    aggregated_cad_actual = 0

    pairs = list(zip(summaries, loaded[: len(summaries)]))
    if len(loaded) > len(summaries):
        # Some loaded entries may have no summary — pair them with an empty
        # summary so we still scan for selected_zone_evidence.json on disk.
        for extra in loaded[len(summaries) :]:
            pairs.append(({}, extra))

    seen_outputs = 0
    for summary, item in pairs:
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0 and not summary:
            # Skip purely-missing outputs so they don't bias the gate.
            continue

        renders = _resolve_zone_renders(summary, item)
        if not renders:
            label = str(summary.get("output_dir") or item.get("path") or "<validation>")
            evidence.append(f"{label}: selected_zone_evidence.renders=missing")
            failures.append(f"{label}: selected_zone_evidence renders missing")
            continue

        seen_outputs += 1
        stats = aggregate_zone_outcomes(renders)
        aggregated_total += stats.total
        aggregated_actual += stats.actual_crop
        aggregated_pdf_total += stats.pdf_total
        aggregated_pdf_actual += stats.pdf_actual_crop
        aggregated_cad_total += stats.cad_total
        aggregated_cad_actual += stats.cad_actual_crop

        label = str(summary.get("output_dir") or item.get("path") or "<validation>")
        evidence.append(
            f"{label}: total={stats.total}, actual_crop={stats.actual_crop}, "
            f"cad={stats.cad_actual_crop}/{stats.cad_total}, "
            f"pdf={stats.pdf_actual_crop}/{stats.pdf_total}"
        )

    overall_rate = (
        aggregated_actual / aggregated_total if aggregated_total > 0 else None
    )
    pdf_rate = (
        aggregated_pdf_actual / aggregated_pdf_total
        if aggregated_pdf_total > 0
        else None
    )
    cad_rate = (
        aggregated_cad_actual / aggregated_cad_total
        if aggregated_cad_total > 0
        else None
    )

    if require_actual_crop_rate_overall is not None:
        if overall_rate is None:
            failures.append(
                f"overall actual_crop rate unmeasurable (total={aggregated_total})"
            )
        elif overall_rate < require_actual_crop_rate_overall:
            failures.append(
                f"overall actual_crop_rate={overall_rate:.4f} < "
                f"{require_actual_crop_rate_overall}"
            )

    if require_actual_crop_rate_pdf is not None:
        if pdf_rate is None:
            failures.append(
                f"pdf actual_crop rate unmeasurable (pdf_total={aggregated_pdf_total})"
            )
        elif pdf_rate < require_actual_crop_rate_pdf:
            failures.append(
                f"pdf actual_crop_rate={pdf_rate:.4f} < {require_actual_crop_rate_pdf}"
            )

    if require_actual_crop_rate_cad is not None:
        if cad_rate is None:
            failures.append(
                f"cad actual_crop rate unmeasurable (cad_total={aggregated_cad_total})"
            )
        elif cad_rate < require_actual_crop_rate_cad:
            failures.append(
                f"cad actual_crop_rate={cad_rate:.4f} < {require_actual_crop_rate_cad}"
            )

    detail_parts = [
        f"outputs_scanned={seen_outputs}",
        f"total_zones={aggregated_total}",
        f"actual_crop={aggregated_actual}",
    ]
    if overall_rate is not None:
        detail_parts.append(f"overall_rate={overall_rate:.4f}")
    if pdf_rate is not None:
        detail_parts.append(f"pdf_rate={pdf_rate:.4f}")
    if cad_rate is not None:
        detail_parts.append(f"cad_rate={cad_rate:.4f}")
    if failures:
        detail_parts.append("failures=" + "; ".join(failures))
    return AuditCheck(
        name="selected_zone_actual_crop_rate",
        passed=not failures and seen_outputs > 0,
        detail=", ".join(detail_parts),
        evidence=evidence,
    )


def _check_review_burden(
    summaries: Sequence[dict[str, Any]],
    *,
    require_precision_threshold: float | None,
    require_burden_threshold: float | None,
    require_burden_minutes_threshold: float | None,
) -> AuditCheck:
    """Review queue precision/burden 게이트 (외부 감사 권고 #3).

    기존 recall + coverage 만으로는 reviewer 가 처리해야 할
    false-positive 부담이 보이지 않음. summary 의 ``review_burden`` 블록
    (validator 가 operator decisions 와 ground truth 를 합쳐 계산) 을
    검사한다.

    summary["review_burden"] schema (review_burden.ReviewBurdenStats):
    - top_queue_precision: float | None
    - false_positive_burden_per_sheet: float | None
    - review_burden_minutes_per_sheet: float | None
    - sheet_count, confirmed_count, false_positive_count, ...
    """
    failures: list[str] = []
    evidence: list[str] = []
    aggregated_precision_num = 0
    aggregated_precision_den = 0
    aggregated_fp = 0
    aggregated_decisions = 0
    aggregated_sheets = 0
    minutes_per_decision: float | None = None
    seen_outputs = 0

    for summary in summaries:
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        burden = _nested(summary, "review_burden")
        label = str(summary.get("output_dir") or "<validation>")
        if not isinstance(burden, dict):
            failures.append(f"{label}: review_burden block missing")
            evidence.append(f"{label}: review_burden=missing")
            continue

        seen_outputs += 1
        confirmed = _int(burden.get("confirmed_count"))
        false_pos = _int(burden.get("false_positive_count"))
        sheets = _int(burden.get("sheet_count"))
        top_precision = _optional_float_value(burden.get("top_queue_precision"))
        fp_burden = _optional_float_value(burden.get("false_positive_burden_per_sheet"))
        burden_minutes = _optional_float_value(
            burden.get("review_burden_minutes_per_sheet")
        )
        if minutes_per_decision is None:
            minutes_per_decision = _optional_float_value(
                burden.get("minutes_per_decision")
            )

        aggregated_precision_num += confirmed
        aggregated_precision_den += confirmed + false_pos
        aggregated_fp += false_pos
        aggregated_decisions += confirmed + false_pos
        aggregated_sheets += sheets

        evidence.append(
            f"{label}: top_precision={top_precision}, "
            f"fp_burden_per_sheet={fp_burden}, "
            f"burden_minutes_per_sheet={burden_minutes}, "
            f"confirmed={confirmed}, fp={false_pos}, sheets={sheets}"
        )

        if require_precision_threshold is not None:
            if top_precision is None:
                failures.append(
                    f"{label}: top_queue_precision missing (required >= "
                    f"{require_precision_threshold})"
                )
            elif top_precision < require_precision_threshold:
                failures.append(
                    f"{label}: top_queue_precision={top_precision:.4f} < "
                    f"{require_precision_threshold}"
                )

        if require_burden_threshold is not None:
            if fp_burden is None:
                failures.append(
                    f"{label}: false_positive_burden_per_sheet missing"
                )
            elif fp_burden > require_burden_threshold:
                failures.append(
                    f"{label}: false_positive_burden_per_sheet={fp_burden:.4f} > "
                    f"{require_burden_threshold}"
                )

        if require_burden_minutes_threshold is not None:
            if burden_minutes is None:
                failures.append(
                    f"{label}: review_burden_minutes_per_sheet missing"
                )
            elif burden_minutes > require_burden_minutes_threshold:
                failures.append(
                    f"{label}: review_burden_minutes_per_sheet={burden_minutes:.4f} > "
                    f"{require_burden_minutes_threshold}"
                )

    aggregated_precision = (
        aggregated_precision_num / aggregated_precision_den
        if aggregated_precision_den > 0
        else None
    )
    aggregated_fp_per_sheet = (
        aggregated_fp / aggregated_sheets if aggregated_sheets > 0 else None
    )

    detail_parts = [
        f"outputs_scanned={seen_outputs}",
        f"sheets_total={aggregated_sheets}",
        f"decisions_total={aggregated_decisions}",
    ]
    if aggregated_precision is not None:
        detail_parts.append(f"agg_precision={aggregated_precision:.4f}")
    if aggregated_fp_per_sheet is not None:
        detail_parts.append(f"agg_fp_per_sheet={aggregated_fp_per_sheet:.4f}")
    if failures:
        detail_parts.append("failures=" + "; ".join(failures))

    return AuditCheck(
        name="review_queue_precision_and_burden",
        passed=not failures and seen_outputs > 0,
        detail=", ".join(detail_parts),
        evidence=evidence,
    )


def _check_dataset_composition(
    customer_manifest: dict[str, Any] | None,
    manifest_path: Path | None,
    *,
    composition_mode: str = "strict",
) -> AuditCheck:
    """Dataset stratification 게이트 (외부 감사 권고 #4).

    customer_evidence_manifest 의 dataset_composition block 을
    dataset_composition.evaluate_dataset_composition() 으로 평가하고
    strict 모드에서는 shortfall 발견 시 fail.

    advisory 모드는 평가 결과만 detail 에 기록하고 항상 pass.
    """
    from src.services.comparison.dataset_composition import evaluate_dataset_composition

    label = str(manifest_path) if manifest_path else "<customer_evidence_manifest>"
    composition = None
    if isinstance(customer_manifest, dict):
        composition = customer_manifest.get("dataset_composition")
    report = evaluate_dataset_composition(composition)

    advisory = composition_mode == "advisory"
    detail_parts = [
        f"manifest={label}",
        f"composition_mode={composition_mode}",
        f"total_pairs={report.total_pairs}",
        f"compliant={report.compliant}",
        f"stratification_compliant={report.stratification_compliant}",
        f"coverage_compliant={report.coverage_compliant}",
    ]
    if report.shortfalls:
        shortfall_summary = "; ".join(
            f"{s.bucket}={s.actual}/{s.required}({s.category})"
            for s in report.shortfalls
        )
        detail_parts.append(f"shortfalls=[{shortfall_summary}]")
    if report.notes:
        detail_parts.append("notes=" + ",".join(report.notes))

    evidence = [
        f"applied_requirements={report.applied_requirements}",
        f"applied_coverage={report.applied_coverage}",
    ]
    if report.shortfalls:
        evidence.extend(str(s.to_dict()) for s in report.shortfalls)

    passed = report.compliant if not advisory else True
    return AuditCheck(
        name="dataset_composition_stratified",
        passed=passed,
        detail=", ".join(detail_parts),
        evidence=evidence,
    )


def _resolve_zone_renders(
    summary: dict[str, Any], loaded_item: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return zone render telemetry from summary or disk fallback."""
    inline = _nested(summary, "selected_zone_evidence", "renders")
    if isinstance(inline, list) and inline:
        return [item for item in inline if isinstance(item, dict)]
    outputs = summary.get("outputs") or {}
    evidence_path = outputs.get("selected_zone_evidence_json") if isinstance(outputs, dict) else None
    candidate_paths: list[Path] = []
    if isinstance(evidence_path, str) and evidence_path:
        candidate_paths.append(Path(evidence_path))
    base = loaded_item.get("path")
    if isinstance(base, Path):
        candidate_paths.append(base / "selected_zone_evidence.json")
        candidate_paths.append(base / "viewer" / "selected_zone_evidence.json")
    for path in candidate_paths:
        if not path.exists():
            continue
        payload = _load_json(path)
        if isinstance(payload, dict):
            disk_renders = payload.get("renders")
            if isinstance(disk_renders, list):
                return [item for item in disk_renders if isinstance(item, dict)]
    return []


def _check_viewer_metadata_first_policy(summaries: Sequence[dict[str, Any]]) -> AuditCheck:
    failures: list[str] = []
    evidence: list[str] = []
    completed_count = 0
    allowed_policies = {"lazy", "top-issues"}
    for summary in summaries:
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_count += 1
        label = str(summary.get("output_dir") or "<validation_summary>")
        policy = str(
            _nested(summary, "input", "viewer_render_policy")
            or _nested(summary, "viewer_package", "viewer_render_policy")
            or ""
        ).strip()
        viewer_manifest = str(
            _nested(summary, "outputs", "viewer_manifest_json")
            or _nested(summary, "viewer_package", "viewer_manifest")
            or ""
        ).strip()
        evidence.append(
            f"{label}: viewer_render_policy={policy or '<missing>'}, viewer_metadata={bool(viewer_manifest)}"
        )
        if policy not in allowed_policies:
            failures.append(
                f"{label}: viewer_render_policy={policy or '<missing>'} must be one of {sorted(allowed_policies)}"
            )
        if not viewer_manifest:
            failures.append(f"{label}: missing viewer metadata for metadata-first viewer policy")
    ok = completed_count > 0 and not failures
    return AuditCheck(
        name="viewer_metadata_first_render_policy",
        passed=ok,
        detail=(
            f"{completed_count} completed output(s) use lazy/top-issues viewer policy with viewer metadata"
            if ok
            else (
                "no completed validation outputs for viewer policy evidence"
                if completed_count <= 0
                else "; ".join(failures)
            )
        ),
        evidence=evidence[:10],
    )


def _check_visual_asset_policy(
    loaded: Sequence[dict[str, Any]],
    *,
    evidence_level: str,
) -> AuditCheck:
    if evidence_level != "customer_grade":
        return AuditCheck(
            name="p5_g24_visual_asset_policy",
            passed=True,
            detail="P5-G24 visual asset policy is advisory outside customer_grade",
            evidence=[],
        )

    failures: list[str] = []
    evidence: list[str] = []
    completed_output_count = 0
    outputs_with_manifests = 0
    manifest_count = 0
    for item in loaded:
        summary = item.get("summary") or {}
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_output_count += 1
        label = str(item.get("path") or "<validation_output>")
        viewer_manifest_path = _viewer_manifest_path_for_loaded_item(item)
        if viewer_manifest_path is None:
            failures.append(f"{label}: viewer_manifest_json missing or unreadable")
            evidence.append(f"{label}: visual_asset_manifests=missing_viewer_manifest")
            continue
        viewer_manifest = _load_json(viewer_manifest_path)
        if not isinstance(viewer_manifest, dict):
            failures.append(f"{viewer_manifest_path}: viewer manifest JSON missing or unreadable")
            evidence.append(f"{label}: visual_asset_manifests=missing_viewer_manifest")
            continue
        manifest_refs = _visual_asset_manifest_refs(viewer_manifest)
        if not manifest_refs:
            failures.append(f"{viewer_manifest_path}: no visual asset manifest references")
            evidence.append(f"{label}: visual_asset_manifests=0")
            continue
        outputs_with_manifests += 1
        evidence.append(
            f"{label}: visual_asset_manifests={len(manifest_refs)}, viewer_manifest={viewer_manifest_path}"
        )
        for ref in manifest_refs:
            manifest_path = _resolve_viewer_manifest_ref(
                ref,
                result_root=item.get("path"),
                viewer_root=viewer_manifest_path.parent,
            )
            manifest_count += 1
            if manifest_path is None:
                failures.append(f"{viewer_manifest_path}: visual asset manifest not found: {ref}")
                continue
            payload = _load_json(manifest_path)
            if not isinstance(payload, dict):
                failures.append(f"{manifest_path}: visual asset manifest JSON missing or unreadable")
                continue
            policy_issues = _visual_asset_policy_issues(payload, customer_grade=True)
            if policy_issues:
                failures.append(f"{manifest_path}: " + "; ".join(policy_issues))

    if completed_output_count <= 0:
        failures.append("customer_grade visual asset policy requires completed validation outputs")
    if outputs_with_manifests < completed_output_count:
        failures.append(
            "customer_grade visual asset manifests must be present for every completed validation output"
        )
    ok = completed_output_count > 0 and manifest_count > 0 and not failures
    return AuditCheck(
        name="p5_g24_visual_asset_policy",
        passed=ok,
        detail=(
            f"P5-G24 visual asset policy passed for {manifest_count} manifest(s)"
            if ok
            else "; ".join(failures)
        ),
        evidence=evidence[:20],
    )


def _viewer_manifest_path_for_loaded_item(item: dict[str, Any]) -> Path | None:
    summary = item.get("summary") or {}
    for value in (
        _nested(summary, "outputs", "viewer_manifest_json"),
        _nested(summary, "viewer_package", "viewer_manifest"),
    ):
        path = _result_reference_path(item, str(value or ""))
        if path is not None:
            return path
    return None


def _visual_asset_manifest_refs(viewer_manifest: dict[str, Any]) -> list[str]:
    refs: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in refs:
            refs.append(text)

    manifest_paths = viewer_manifest.get("visual_asset_manifest_paths")
    if isinstance(manifest_paths, list):
        for value in manifest_paths:
            add(value)
    pairs = viewer_manifest.get("pairs")
    if isinstance(pairs, list):
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            pair_paths = pair.get("visual_asset_manifest_paths")
            if isinstance(pair_paths, list):
                for value in pair_paths:
                    add(value)
            visual_assets = pair.get("visual_assets")
            if isinstance(visual_assets, dict):
                _collect_visual_asset_refs(visual_assets, add)
    return refs


def _collect_visual_asset_refs(node: Any, add: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"manifest_path", "manifest"}:
                add(value)
            else:
                _collect_visual_asset_refs(value, add)
    elif isinstance(node, list):
        for value in node:
            _collect_visual_asset_refs(value, add)


def _resolve_viewer_manifest_ref(
    value: str,
    *,
    result_root: Any,
    viewer_root: Path,
) -> Path | None:
    if not value:
        return None
    path = Path(value)
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    else:
        if isinstance(result_root, Path):
            candidates.append(result_root / path)
        candidates.append(viewer_root / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _visual_asset_policy_issues(
    payload: dict[str, Any],
    *,
    customer_grade: bool,
) -> list[str]:
    try:
        from src.services.comparison.visual_asset import validate_visual_asset_policy
    except Exception as exc:
        return [f"visual asset policy validator unavailable: {exc}"]
    try:
        return validate_visual_asset_policy(payload, customer_grade=customer_grade)
    except Exception as exc:
        return [f"visual asset policy validation failed: {exc}"]


def _check_selected_zone_perf(
    loaded: Sequence[dict[str, Any]],
    summaries: Sequence[dict[str, Any]],
    *,
    evidence_level: str,
    max_cold_zone_render_ms: float,
    max_cache_hit_zone_render_ms: float,
) -> AuditCheck:
    evidence: list[str] = []
    failures: list[str] = []
    telemetry_count = 0
    completed_output_count = 0
    for item in loaded:
        summary = item.get("summary") or {}
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs > 0:
            completed_output_count += 1
        perf = summary.get("viewer_perf_summary")
        if not isinstance(perf, dict) or _int(perf.get("zone_crop_count")) <= 0:
            perf = summarize_viewer_perf(item["path"] / "viewer")
        zone_count = _int(perf.get("zone_crop_count")) if isinstance(perf, dict) else 0
        cold_p95 = _float(_nested(perf, "zone_crop_cold_ms", "p95"))
        hit_p95 = _float(_nested(perf, "zone_crop_cache_hit_ms", "p95"))
        if zone_count:
            telemetry_count += 1
            reason_counts = perf.get("reason_code_counts") if isinstance(perf, dict) else {}
            renderer_counts = perf.get("renderer_backend_counts") if isinstance(perf, dict) else {}
            reason_suffix = f", reason_codes={reason_counts}" if reason_counts else ""
            renderer_suffix = f", renderers={renderer_counts}" if renderer_counts else ""
            evidence.append(
                f"{item['path']}: completed_pairs={completed_pairs}, "
                f"zone_count={zone_count}, cold_p95={cold_p95}, hit_p95={hit_p95}"
                f"{reason_suffix}{renderer_suffix}"
            )
            if cold_p95 and cold_p95 > max_cold_zone_render_ms:
                failures.append(f"{item['path']}: cold_p95={cold_p95} > {max_cold_zone_render_ms}")
            if hit_p95 and hit_p95 > max_cache_hit_zone_render_ms:
                failures.append(f"{item['path']}: hit_p95={hit_p95} > {max_cache_hit_zone_render_ms}")
        elif evidence_level == "customer_grade" and completed_pairs > 0:
            failures.append(f"{item['path']}: missing selected-zone telemetry for completed_pairs={completed_pairs}")
    customer_grade_has_all_completed_output_telemetry = (
        evidence_level != "customer_grade"
        or (completed_output_count > 0 and telemetry_count >= completed_output_count)
    )
    ok = telemetry_count > 0 and customer_grade_has_all_completed_output_telemetry and not failures
    return AuditCheck(
        name="selected_zone_render_perf",
        passed=ok,
        detail=(
            (
                f"selected-zone render telemetry meets MVP budgets for {telemetry_count} output(s); "
                f"completed_outputs={completed_output_count}"
            )
            if ok
            else (
                "; ".join(failures)
                if failures
                else "no selected-zone render telemetry within MVP budgets"
            )
        ),
        evidence=evidence,
    )


def _check_confirmed_exports(
    loaded: Sequence[dict[str, Any]],
    release: dict[str, Any] | None,
    *,
    evidence_level: str,
) -> AuditCheck:
    confirmed_files: list[str] = []
    report_files: list[str] = []
    unexpected_confirmed_cloud_files: list[str] = []
    summary_files: list[str] = []
    summary_failures: list[str] = []
    summary_passed = False
    for item in loaded:
        path = item["path"]
        for file in path.rglob("*_confirmed.*"):
            if file.is_file() and _is_confirmed_export_filename(file):
                confirmed_files.append(str(file))
        report_files.extend(str(file) for file in path.rglob("review_report_*.pdf") if file.is_file())

        for cloud_dir in path.rglob("confirmed_clouds"):
            if not cloud_dir.is_dir():
                continue
            unexpected_confirmed_cloud_files.extend(
                str(file)
                for file in cloud_dir.iterdir()
                if file.is_file() and not _is_confirmed_export_filename(file)
            )

        for summary_path in path.rglob("workbench_acceptance_summary.json"):
            if not summary_path.is_file():
                continue
            summary_files.append(str(summary_path))
            passed, detail = _workbench_acceptance_confirmed_items_passed(summary_path)
            if passed:
                summary_passed = True
            else:
                summary_failures.append(f"{summary_path}: {detail}")

    summary_required = evidence_level == "customer_grade"
    summary_ok = summary_passed if summary_files else not summary_required
    ok = bool(confirmed_files and report_files) and summary_ok and not unexpected_confirmed_cloud_files
    release_step_passed = _step_passed(release, "workbench_acceptance_smoke") if release else False
    detail_parts = [
        f"confirmed_files={len(confirmed_files)}",
        f"report_files={len(report_files)}",
        f"acceptance_summaries={len(summary_files)}",
        f"acceptance_items_5_8_8b_9b_9c_10_passed={summary_passed if summary_files else 'missing_required' if summary_required else 'not_required'}",
        f"unexpected_confirmed_cloud_files={len(unexpected_confirmed_cloud_files)}",
    ]
    if summary_required and not summary_files:
        detail_parts.append("customer_grade_requires_workbench_acceptance_summary=true")
    if release_step_passed:
        detail_parts.append("release_manifest_workbench_acceptance_smoke=ignored_for_artifact_gate")
    if summary_failures and not summary_passed:
        detail_parts.append("summary_failures=" + " | ".join(summary_failures[:3]))
    return AuditCheck(
        name="confirmed_only_cloud_and_report_export",
        passed=ok,
        detail=", ".join(detail_parts),
        evidence=(confirmed_files + report_files + summary_files + unexpected_confirmed_cloud_files)[:10],
    )


def _workbench_acceptance_confirmed_items_passed(summary_path: Path) -> tuple[bool, str]:
    payload = _load_json(summary_path)
    if not isinstance(payload, dict):
        return False, "summary JSON is missing or unreadable"
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return False, "summary checks[] is missing"

    required = {
        "5.": "review_queue first-screen Top 이슈 + 점프/필터",
        "8.": "Workbench confirmed 판정 → confirmed-only 구름마크 export",
        "8b.": "Workbench 보류/오탐 판정 → confirmed-only export 제외",
        "10.": "confirmed-only 검토 보고서 PDF 생성 + path leakage audit",
    }
    required = REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS
    found: dict[str, bool] = {}
    details: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name") or "")
        for prefix in required:
            if name.startswith(prefix):
                found[prefix] = check.get("passed") is True
                details.append(f"{prefix}{check.get('passed')!r}")

    missing = [label for prefix, label in required.items() if prefix not in found]
    failed = [prefix for prefix, passed in found.items() if not passed]
    if missing or failed:
        parts: list[str] = []
        if missing:
            parts.append("missing required check(s): " + ", ".join(missing))
        if failed:
            parts.append("failed required check(s): " + ", ".join(failed))
        if details:
            parts.append("observed=" + ", ".join(details))
        return False, "; ".join(parts)
    return True, "items 5, 8, 8b, 9b, 9c, and 10 passed"


def _check_release_manifest(
    release: dict[str, Any] | None,
    release_manifest: Path | None,
    *,
    evidence_level: str = "synthetic",
) -> AuditCheck:
    if release_manifest is None:
        return AuditCheck(
            name="packaged_release_manifest",
            passed=False,
            detail="release_manifest.json was not provided",
            evidence=[],
        )
    failures: list[str] = []
    evidence = [str(release_manifest)]
    if not isinstance(release, dict):
        failures.append("release_manifest.json is missing or unreadable")
    else:
        if release.get("status") != "passed":
            failures.append("release_manifest.status must be passed")
        for step in ("pyinstaller_build", "packaged_app_launch_smoke", "customer_shareable_package_path_audit"):
            if not _step_passed(release, step):
                failures.append(f"release step {step} must be passed")
        package_zip_path = _manifest_reference_path(
            release_manifest,
            str(_nested(release, "artifacts", "customer_shareable_package_zip") or ""),
        )
        if package_zip_path is None:
            failures.append("release artifacts must include existing customer_shareable_package_zip")
        else:
            evidence.append(str(package_zip_path))
            if package_zip_path.suffix.lower() != ".zip":
                failures.append("customer_shareable_package_zip must be a .zip file")
            else:
                zip_entries = _zip_entries(package_zip_path)
                missing_zip_entries = (
                    list(REQUIRED_CUSTOMER_PACKAGE_CONTENTS)
                    if zip_entries is None
                    else [entry for entry in REQUIRED_CUSTOMER_PACKAGE_CONTENTS if entry not in zip_entries]
                )
                if missing_zip_entries:
                    failures.append(
                        "customer_shareable_package_zip missing required entries: "
                        + ", ".join(missing_zip_entries)
                    )
                if zip_entries and "release_manifest.json" in zip_entries:
                    failures.append("customer_shareable_package_zip must not include internal release_manifest.json")
                if zip_entries:
                    disallowed_zip_entries = _customer_package_disallowed_entries(zip_entries)
                    if disallowed_zip_entries:
                        failures.append(
                            "customer_shareable_package_zip contains disallowed bytecode/cache entries: "
                            + ", ".join(disallowed_zip_entries[:10])
                        )
                zip_scan = _scan_customer_shareable_zip_for_path_leaks(package_zip_path)
                if zip_scan is None:
                    failures.append("customer_shareable_package_zip actual payload path scan failed")
                else:
                    if int(zip_scan.get("leak_count") or 0) != 0:
                        leaked_entries = sorted(
                            {
                                str(leak.get("path") or "")
                                for leak in zip_scan.get("leaks") or []
                                if isinstance(leak, dict)
                            }
                        )
                        detail = "; leaked_entries=" + ", ".join(leaked_entries[:10]) if leaked_entries else ""
                        failures.append(
                            "customer_shareable_package_zip actual payload leak_count must be 0"
                            + detail
                        )
                    if int(zip_scan.get("scanned_files") or 0) <= 0:
                        failures.append("customer_shareable_package_zip actual payload scanned_files must be > 0")
                    if int(zip_scan.get("scanned_app_first_party_files") or 0) <= 0:
                        failures.append(
                            "customer_shareable_package_zip actual payload scanned_app_first_party_files must be > 0"
                        )
                    if int(zip_scan.get("scanned_binary_files") or 0) <= 0:
                        failures.append(
                            "customer_shareable_package_zip actual payload scanned_binary_files must be > 0"
                        )
                zip_manifest = _zip_json_payload(package_zip_path, "customer_package_manifest.json")
                if zip_manifest is None:
                    failures.append(
                        "customer_shareable_package_zip customer_package_manifest.json is missing or unreadable"
                    )
                else:
                    failures.extend(
                        _customer_package_manifest_failures(
                            zip_manifest,
                            "customer_shareable_package_zip customer_package_manifest.json",
                        )
                    )
                zip_audit = _zip_json_payload(package_zip_path, "customer_package_path_audit.json")
                if zip_audit is None:
                    failures.append(
                        "customer_shareable_package_zip customer_package_path_audit.json is missing or unreadable"
                    )
                else:
                    failures.extend(
                        _customer_package_audit_failures(
                            zip_audit,
                            "customer_shareable_package_zip customer_package_path_audit.json",
                        )
                    )
                missing_request_terms = _missing_zip_customer_request_terms(
                    package_zip_path,
                    "customer_evidence_request_ko.md",
                )
                if missing_request_terms:
                    failures.append(
                        "customer_shareable_package_zip "
                        "customer_evidence_request_ko.md missing required term(s): "
                        + ", ".join(missing_request_terms)
                    )
                if evidence_level == "customer_grade":
                    missing_zip_checklist_terms = _missing_zip_prompt_to_artifact_terms(
                        package_zip_path,
                        "mvp_exit_prompt_to_artifact_checklist.md",
                    )
                    if missing_zip_checklist_terms:
                        failures.append(
                            "customer_shareable_package_zip "
                            "mvp_exit_prompt_to_artifact_checklist.md missing required term(s): "
                            + ", ".join(missing_zip_checklist_terms)
                        )
        package_manifest_path = _manifest_reference_path(
            release_manifest,
            str(_nested(release, "artifacts", "customer_shareable_package_manifest") or ""),
        )
        if package_manifest_path is None:
            failures.append("release artifacts must include existing customer_shareable_package_manifest")
        else:
            evidence.append(str(package_manifest_path))
            package_manifest = _load_json(package_manifest_path)
            if not isinstance(package_manifest, dict):
                failures.append("customer_shareable_package_manifest is missing or unreadable")
            else:
                failures.extend(
                    _customer_package_manifest_failures(
                        package_manifest,
                        "customer_shareable_package_manifest",
                    )
                )
        package_audit_path = _manifest_reference_path(
            release_manifest,
            str(_nested(release, "artifacts", "customer_shareable_package_path_audit") or ""),
        )
        if package_audit_path is None:
            failures.append("release artifacts must include existing customer_shareable_package_path_audit")
        else:
            evidence.append(str(package_audit_path))
            package_audit = _load_json(package_audit_path)
            if not isinstance(package_audit, dict):
                failures.append("customer_shareable_package_path_audit is missing or unreadable")
            else:
                failures.extend(
                    _customer_package_audit_failures(
                        package_audit,
                        "customer_shareable_package_path_audit",
                    )
                )
        if evidence_level == "customer_grade":
            checklist_path = _manifest_reference_path(
                release_manifest,
                str(_nested(release, "artifacts", "mvp_exit_prompt_to_artifact_checklist") or ""),
            )
            if checklist_path is None:
                failures.append(
                    "release artifacts must include existing mvp_exit_prompt_to_artifact_checklist"
                )
            else:
                evidence.append(str(checklist_path))
                missing_terms = _missing_prompt_to_artifact_terms(checklist_path)
                if missing_terms:
                    failures.append(
                        "mvp_exit_prompt_to_artifact_checklist missing required term(s): "
                        + ", ".join(missing_terms)
                    )
    ok = not failures
    return AuditCheck(
        name="packaged_release_manifest",
        passed=ok,
        detail=(
            "PyInstaller build, packaged launch smoke, and customer package path audit passed"
            if ok
            else "; ".join(failures)
        ),
        evidence=evidence,
    )


def _missing_prompt_to_artifact_terms(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return list(REQUIRED_PROMPT_TO_ARTIFACT_CHECKLIST_TERMS)
    return _missing_prompt_to_artifact_terms_from_text(text)


def _missing_zip_prompt_to_artifact_terms(zip_path: Path, entry: str) -> list[str]:
    text = _zip_text_payload(zip_path, entry)
    if text is None:
        return list(REQUIRED_PROMPT_TO_ARTIFACT_CHECKLIST_TERMS)
    return _missing_prompt_to_artifact_terms_from_text(text)


def _missing_prompt_to_artifact_terms_from_text(text: str) -> list[str]:
    lower_text = text.lower()
    return [
        term
        for term in REQUIRED_PROMPT_TO_ARTIFACT_CHECKLIST_TERMS
        if term.lower() not in lower_text
    ]


def _missing_zip_customer_request_terms(zip_path: Path, entry: str) -> list[str]:
    text = _zip_text_payload(zip_path, entry)
    if text is None:
        return list(REQUIRED_CUSTOMER_EVIDENCE_REQUEST_KO_TERMS)
    lower_text = text.lower()
    return [
        term
        for term in REQUIRED_CUSTOMER_EVIDENCE_REQUEST_KO_TERMS
        if term.lower() not in lower_text
    ]


def _customer_package_manifest_failures(package_manifest: dict[str, Any], label: str) -> list[str]:
    failures: list[str] = []
    if package_manifest.get("package_type") != "customer_shareable":
        failures.append(f"{label}.package_type must be customer_shareable")
    if package_manifest.get("internal_release_manifest_included") is not False:
        failures.append(f"{label}.internal_release_manifest_included must be false")
    contents = {str(item).replace("\\", "/") for item in package_manifest.get("contents") or []}
    missing_manifest_entries = [
        entry for entry in REQUIRED_CUSTOMER_PACKAGE_CONTENTS if entry not in contents
    ]
    if missing_manifest_entries:
        failures.append(
            f"{label}.contents missing required entries: "
            + ", ".join(missing_manifest_entries)
        )
    disallowed_entries = _customer_package_disallowed_entries(contents)
    if disallowed_entries:
        failures.append(
            f"{label}.contents contains disallowed bytecode/cache entries: "
            + ", ".join(disallowed_entries[:10])
        )
    return failures


def _customer_package_audit_failures(package_audit: dict[str, Any], label: str) -> list[str]:
    failures: list[str] = []
    if package_audit.get("status") != "passed":
        failures.append(f"{label}.status must be passed")
    if int(package_audit.get("leak_count") or 0) != 0:
        failures.append(f"{label}.leak_count must be 0")
    if int(package_audit.get("scanned_files") or 0) <= 0:
        failures.append(f"{label}.scanned_files must be > 0")
    if int(package_audit.get("scanned_app_first_party_files") or 0) <= 0:
        failures.append(f"{label}.scanned_app_first_party_files must be > 0")
    if int(package_audit.get("scanned_binary_files") or 0) <= 0:
        failures.append(f"{label}.scanned_binary_files must be > 0")
    if int(package_audit.get("disallowed_file_count") or 0) != 0:
        failures.append(f"{label}.disallowed_file_count must be 0")
    return failures


def _customer_package_disallowed_entries(entries: Iterable[str]) -> list[str]:
    disallowed: list[str] = []
    for entry in sorted(str(item).replace("\\", "/") for item in entries):
        parts = set(entry.split("/"))
        if (
            parts & DISALLOWED_CUSTOMER_PACKAGE_ENTRY_PARTS
            or entry.lower().endswith(DISALLOWED_CUSTOMER_PACKAGE_ENTRY_SUFFIXES)
        ):
            disallowed.append(entry)
    return disallowed


def _scan_customer_shareable_zip_for_path_leaks(zip_path: Path) -> dict[str, Any] | None:
    leaks: list[dict[str, Any]] = []
    scanned_files = 0
    scanned_app_first_party_files = 0
    scanned_binary_files = 0
    skipped_app_internal_files = 0
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                entry = info.filename.rstrip("/")
                relative_path = PurePosixPath(entry)
                data = archive.read(info)
                if relative_path.parts and relative_path.parts[0] == "app":
                    if _is_first_party_app_text_entry(relative_path):
                        scanned_app_first_party_files += 1
                        leaks.extend(
                            _path_leaks_in_text(
                                data.decode("utf-8", errors="ignore"),
                                entry,
                                CUSTOMER_PACKAGE_APP_BUILD_PATH_LEAK_RE,
                            )
                        )
                    elif _is_customer_package_binary_scan_candidate_entry(relative_path):
                        scanned_binary_files += 1
                        leaks.extend(
                            _path_leaks_in_binary_data(
                                data,
                                entry,
                                CUSTOMER_PACKAGE_BINARY_BUILD_PATH_LEAK_RE,
                            )
                        )
                    else:
                        skipped_app_internal_files += 1
                    continue
                if relative_path.suffix.lower() not in CUSTOMER_PACKAGE_TEXT_SUFFIXES:
                    continue
                scanned_files += 1
                leaks.extend(
                    _path_leaks_in_text(
                        data.decode("utf-8", errors="ignore"),
                        entry,
                        CUSTOMER_PACKAGE_PATH_LEAK_RE,
                    )
                )
    except Exception:
        return None
    return {
        "schema_version": 1,
        "status": "passed" if not leaks else "failed",
        "scanned_files": scanned_files,
        "scanned_app_first_party_files": scanned_app_first_party_files,
        "scanned_binary_files": scanned_binary_files,
        "skipped_app_internal_files": skipped_app_internal_files,
        "leak_count": len(leaks),
        "leaks": leaks,
    }


def _is_first_party_app_text_entry(relative_path: PurePosixPath) -> bool:
    parts = relative_path.parts
    return (
        len(parts) >= 5
        and parts[0] == "app"
        and parts[1] == "DrawingCompareWorkbench"
        and parts[2] == "_internal"
        and parts[3] == "src"
        and relative_path.suffix.lower() in CUSTOMER_PACKAGE_APP_TEXT_SUFFIXES
    )


def _is_customer_package_binary_scan_candidate_entry(relative_path: PurePosixPath) -> bool:
    parts = relative_path.parts
    if relative_path.as_posix() == "app/DrawingCompareWorkbench/DrawingCompareWorkbench.exe":
        return True
    return (
        len(parts) >= 5
        and parts[0] == "app"
        and parts[1] == "DrawingCompareWorkbench"
        and parts[2] == "_internal"
        and parts[3] == "src"
        and relative_path.suffix.lower() not in CUSTOMER_PACKAGE_APP_TEXT_SUFFIXES
    )


def _path_leaks_in_text(
    text: str,
    relative_path: str,
    pattern: re.Pattern[str],
) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        matches = [match.group(0) for match in pattern.finditer(line)]
        if matches:
            leaks.append(
                {
                    "path": relative_path,
                    "line": line_number,
                    "matches": matches,
                }
            )
    return leaks


def _path_leaks_in_binary_data(
    data: bytes,
    relative_path: str,
    pattern: re.Pattern[bytes],
) -> list[dict[str, Any]]:
    matches = [
        match.group(0).decode("utf-8", errors="ignore")[:240]
        for match in pattern.finditer(data)
    ]
    if not matches:
        return []
    return [
        {
            "path": relative_path,
            "line": None,
            "matches": sorted(set(matches)),
        }
    ]


def _zip_entries(zip_path: Path) -> set[str] | None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            return {name.rstrip("/") for name in archive.namelist()}
    except Exception:
        return None


def _zip_json_payload(zip_path: Path, entry: str) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            payload = json.loads(archive.read(entry).decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _zip_text_payload(zip_path: Path, entry: str) -> str | None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            return archive.read(entry).decode("utf-8", errors="ignore")
    except Exception:
        return None


def _missing_zip_entries(zip_path: Path, required_entries: Sequence[str]) -> list[str]:
    names = _zip_entries(zip_path)
    if names is None:
        return list(required_entries)
    return [entry for entry in required_entries if entry not in names]


def _queue_items(summary: dict[str, Any]) -> list[dict[str, Any]]:
    queue = _nested(summary, "review_dashboard", "review_queue")
    if isinstance(queue, dict):
        items = queue.get("items") or queue.get("top_structural_items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    top_issues = _nested(summary, "review_dashboard", "top_issues")
    if isinstance(top_issues, list):
        return [item for item in top_issues if isinstance(item, dict)]
    return []


def _step_passed(release: dict[str, Any] | None, name: str) -> bool:
    if not isinstance(release, dict):
        return False
    for step in release.get("steps") or []:
        if isinstance(step, dict) and step.get("name") == name and step.get("status") == "passed":
            return True
    return False


def _kind_count(summary: dict[str, Any], side_key: str, kind: str) -> int:
    return _int(_nested(summary, "files", side_key, kind))


def _has_cad_pair(summaries: Sequence[dict[str, Any]]) -> bool:
    return any(
        _kind_count(summary, "a_kind_counts", "cad") > 0
        and _kind_count(summary, "b_kind_counts", "cad") > 0
        and _int(_nested(summary, "comparison", "completed_pairs")) > 0
        for summary in summaries
    )


def _has_dwg_dxf_cad_evidence(summaries: Sequence[dict[str, Any]]) -> bool:
    extension_evidence = _cad_extension_evidence(summaries)
    return _has_cad_pair(summaries) and all(extension_evidence.get(ext) for ext in CAD_INPUT_EXTENSIONS)


def _cad_extension_evidence(summaries: Sequence[dict[str, Any]]) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {extension: [] for extension in sorted(CAD_INPUT_EXTENSIONS)}
    for summary in summaries:
        if (
            _kind_count(summary, "a_kind_counts", "cad") <= 0
            or _kind_count(summary, "b_kind_counts", "cad") <= 0
            or _int(_nested(summary, "comparison", "completed_pairs")) <= 0
        ):
            continue
        output_dir = str(summary.get("output_dir") or "<validation_summary>")
        for extension in _source_extensions(summary):
            if extension in evidence:
                evidence[extension].append(output_dir)
    return evidence


def _source_extensions(summary: dict[str, Any]) -> set[str]:
    extensions: set[str] = set()
    artifacts = _nested(summary, "change_artifacts", "artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            for key in ("source_a", "source_b"):
                extensions.update(_known_extensions(str(artifact.get(key) or "")))
    for key in ("a", "b"):
        extensions.update(_known_extensions(str(_nested(summary, "input", key) or "")))
    return extensions


def _known_extensions(value: str) -> set[str]:
    lower = value.lower()
    return {
        extension
        for extension in CAD_INPUT_EXTENSIONS | PDF_INPUT_EXTENSIONS
        if f".{extension}" in lower
    }


def _has_pdf_pair(summaries: Sequence[dict[str, Any]]) -> bool:
    return any(
        _kind_count(summary, "a_kind_counts", "pdf") > 0
        and _kind_count(summary, "b_kind_counts", "pdf") > 0
        and _int(_nested(summary, "comparison", "completed_pairs")) > 0
        for summary in summaries
    )


def _has_pdf_pdf_evidence(summaries: Sequence[dict[str, Any]]) -> bool:
    return bool(_pdf_source_evidence(summaries))


def _pdf_source_evidence(summaries: Sequence[dict[str, Any]]) -> list[str]:
    evidence: list[str] = []
    for summary in summaries:
        if (
            _kind_count(summary, "a_kind_counts", "pdf") <= 0
            or _kind_count(summary, "b_kind_counts", "pdf") <= 0
            or _int(_nested(summary, "comparison", "completed_pairs")) <= 0
        ):
            continue
        if _summary_has_pdf_pdf_sources(summary):
            evidence.append(str(summary.get("output_dir") or "<validation_summary>"))
    return evidence


def _summary_has_pdf_pdf_sources(summary: dict[str, Any]) -> bool:
    artifacts = _nested(summary, "change_artifacts", "artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            source_a_exts = _known_extensions(str(artifact.get("source_a") or ""))
            source_b_exts = _known_extensions(str(artifact.get("source_b") or ""))
            if "pdf" in source_a_exts and "pdf" in source_b_exts:
                return True
    input_a_exts = _known_extensions(str(_nested(summary, "input", "a") or ""))
    input_b_exts = _known_extensions(str(_nested(summary, "input", "b") or ""))
    return "pdf" in input_a_exts and "pdf" in input_b_exts


def _has_cad_pdf_block(
    summaries: Sequence[dict[str, Any]],
    loaded: Sequence[dict[str, Any]] | None = None,
) -> bool:
    return any(
        _blocked_csv_has_cad_pdf(item["path"] / "blocked_pairs.csv")
        for item in (loaded or [])
        if isinstance(item, dict) and item.get("path")
    )


def _blocked_csv_has_cad_pdf(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                kinds = {str(row.get("a_kind") or "").lower(), str(row.get("b_kind") or "").lower()}
                reason = str(row.get("reason") or "").lower()
                if kinds == {"cad", "pdf"} and _has_clear_cad_pdf_block_reason(reason):
                    return True
    except Exception:
        return False
    return False


def _has_clear_cad_pdf_block_reason(reason: str) -> bool:
    text = reason.lower()
    has_cad_pdf = "cad/pdf" in text or "cad-pdf" in text or ("cad" in text and "pdf" in text)
    has_block_word = any(
        token in text
        for token in (
            "blocked",
            "block",
            "not supported",
            "unsupported",
            "cross-family",
            "cross comparison",
            "cross-compare",
            "차단",
            "미지원",
            "지원하지",
            "비교 불가",
            "교차 비교",
        )
    )
    return has_cad_pdf and has_block_word


def _change_zones_csv_path(item: dict[str, Any]) -> Path:
    summary = item.get("summary") or {}
    output = _nested(summary, "outputs", "change_zones_csv")
    if output:
        candidate = Path(str(output))
        if candidate.is_absolute():
            return candidate
        return item["path"] / candidate
    return item["path"] / "change_artifacts" / "change_zones.csv"


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _row_is_pdf_change_zone(row: dict[str, str]) -> bool:
    source_format = str(row.get("source_format") or "").lower()
    detection_source = str(row.get("detection_source") or "").lower()
    entity_types = str(row.get("entity_types") or "").upper()
    layers = str(row.get("layers") or "").upper()
    return (
        source_format == "pdf"
        or detection_source in PDF_DETECTION_SOURCES
        or "PDF_" in entity_types
        or "PDF_" in layers
    )


def _nested(payload: Any, *keys: str) -> Any:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _float_close(actual: float | None, expected: float, tolerance: float = 1e-9) -> bool:
    return actual is not None and abs(float(actual) - float(expected)) <= tolerance


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _entity_types(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip().upper() for item in value if str(item).strip()}
    text = str(value or "")
    if not text.strip():
        return set()
    parts = text.replace("|", ",").replace(";", ",").split(",")
    return {part.strip().upper() for part in parts if part.strip()}


def _manifest_reference_path(manifest_path: Path | None, value: str) -> Path | None:
    if not value or manifest_path is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path if path.exists() else None


def _customer_evidence_manifest_path_leak_count(manifest_path: Path | None) -> int:
    if not manifest_path or not manifest_path.exists():
        return 0
    try:
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0
    return sum(1 for _match in CUSTOMER_PACKAGE_PATH_LEAK_RE.finditer(text))


def _is_template_or_handoff_evidence(path: Path | None) -> bool:
    if path is None:
        return False
    normalized = str(path).lower().replace("\\", "/")
    name = path.name.lower()
    return any(marker in name or marker in normalized for marker in DISALLOWED_EVIDENCE_PATH_MARKERS)


def _normalize_operator_role(value: str) -> str:
    return str(value or "").strip().lstrip("\ufeff").lower().replace("-", "_").replace(" ", "_")


def read_operator_notes_text(notes_file: Path | None) -> str:
    if not notes_file or not notes_file.exists():
        return ""
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return notes_file.read_text(encoding=encoding)
        except UnicodeError:
            continue
        except Exception:
            return ""
    try:
        return notes_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _operator_notes_has_reviewer_role(notes_file: Path | None, reviewer_role: str) -> bool:
    text = read_operator_notes_text(notes_file)
    if not text:
        return False
    return _operator_role_from_notes_text(text) == _normalize_operator_role(reviewer_role)


def _operator_role_from_notes_text(text: str) -> str:
    role_keys = {"reviewer_role", "operator_reviewer_role", "structural_reviewer_role"}
    for line in text.splitlines():
        stripped = line.strip().lstrip("-*").strip()
        if ":" in stripped:
            key, value = stripped.split(":", 1)
        elif "=" in stripped:
            key, value = stripped.split("=", 1)
        else:
            continue
        if _normalize_operator_role(key) in role_keys:
            return _normalize_operator_role(value)
    return ""


def operator_notes_have_substantive_review_notes(notes_file: Path | None) -> bool:
    text = read_operator_notes_text(notes_file)
    if not text:
        return False
    substantive = _operator_notes_substantive_review_text(text)
    return len(substantive) >= MIN_OPERATOR_DRY_RUN_NOTE_CHARS


def _operator_notes_substantive_review_text(text: str) -> str:
    retained: list[str] = []
    role_keys = {"reviewer_role", "operator_reviewer_role", "structural_reviewer_role"}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ":" in line:
            maybe_key, maybe_value = line.split(":", 1)
            if _normalize_operator_role(maybe_key) in {"operator_notes", "notes"}:
                line = maybe_value.strip()
                if not line:
                    continue
        lowered = line.lower()
        if any(marker in lowered for marker in OPERATOR_DRY_RUN_NOTE_BOILERPLATE_MARKERS):
            continue
        if any(check_id.lower() in lowered for check_id in REQUIRED_OPERATOR_WORKFLOW_CHECKS):
            continue
        stripped = line.lstrip("-*").strip()
        if ":" in stripped:
            key, _value = stripped.split(":", 1)
        elif "=" in stripped:
            key, _value = stripped.split("=", 1)
        else:
            key = ""
        if key and _normalize_operator_role(key) in role_keys:
            continue
        if re.match(r"^\s*[-*]?\s*\[(?:x| |done|pass)\]", lowered):
            continue
        retained.append(re.sub(r"^\s*[-*]\s*", "", line))
    return " ".join(retained).strip()


def _operator_notes_missing_workflow_checks(notes_file: Path | None) -> list[str]:
    text = read_operator_notes_text(notes_file)
    if not text:
        return list(REQUIRED_OPERATOR_WORKFLOW_CHECKS)
    return [
        check_id
        for check_id in REQUIRED_OPERATOR_WORKFLOW_CHECKS
        if not _workflow_check_is_checked(text, check_id)
    ]


def _workflow_check_is_checked(text: str, check_id: str) -> bool:
    needle = check_id.lower()
    for line in text.splitlines():
        lowered = line.strip().lower()
        if needle in lowered and ("[x]" in lowered or "[done]" in lowered or "[pass]" in lowered):
            return True
    return False


def _is_confirmed_export_filename(path: Path) -> bool:
    return path.suffix.lower() in CONFIRMED_EXPORT_SUFFIXES and path.stem.endswith("_confirmed")


def _is_audited_confirmed_export_artifact(path: Path, loaded: Sequence[dict[str, Any]]) -> bool:
    if not path.is_file() or not _is_confirmed_export_filename(path):
        return False
    try:
        artifact_path = path.resolve()
    except Exception:
        artifact_path = path
    for item in loaded:
        root = item.get("path") if isinstance(item, dict) else None
        if not root:
            continue
        try:
            artifact_path.relative_to(Path(root).resolve())
            return True
        except Exception:
            continue
    return False


def review_decision_truth_csv_issues(path: Path) -> list[str]:
    return summarize_review_decision_truth_csv(path).get("issues", [])


def summarize_review_decision_truth_csv(
    path: Path,
    *,
    min_labeled_rows: int = DEFAULT_MIN_REVIEW_DECISION_ROWS,
    min_overall_precision: float = DEFAULT_MIN_REVIEW_DECISION_OVERALL_PRECISION,
    min_bucket_precision: float = DEFAULT_MIN_REVIEW_DECISION_BUCKET_PRECISION,
    max_false_positive_rate: float = DEFAULT_MAX_REVIEW_DECISION_FALSE_POSITIVE_RATE,
    min_bucket_rows: int = DEFAULT_MIN_REVIEW_DECISION_BUCKET_ROWS,
    required_buckets: Sequence[str] | None = None,
) -> dict[str, Any]:
    required = list(required_buckets or STRUCTURAL_COVERAGE_TERMS.keys())
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    fieldnames: list[str] = []
    if not path.exists():
        issues.append("review_decision_truth CSV is missing")
    else:
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = [str(name or "").strip() for name in (reader.fieldnames or [])]
                normalized = {name.lower(): name for name in fieldnames}
                missing = [column for column in REQUIRED_REVIEW_DECISION_TRUTH_COLUMNS if column not in normalized]
                if missing:
                    issues.append(f"review_decision_truth CSV missing required columns {missing}")
                for line_no, row in enumerate(reader, start=2):
                    if not any(str(value or "").strip() for value in row.values()):
                        continue
                    rows.append(row)
                    if missing:
                        continue
                    row_issues = _review_decision_truth_row_issues(row, normalized, line_no)
                    row_issues.extend(_template_marker_issues("review_decision_truth CSV", row, normalized, line_no))
                    issues.extend(row_issues[:5])
        except Exception as exc:
            issues.append(f"review_decision_truth CSV is unreadable: {exc}")
    if not rows and not any("unreadable" in issue for issue in issues):
        issues.append("review_decision_truth CSV must contain at least one data row")

    counts = {"true_positive": 0, "false_positive": 0, "hold": 0}
    bucket_rows = {bucket: 0 for bucket in required}
    bucket_tp = {bucket: 0 for bucket in required}
    bucket_fp = {bucket: 0 for bucket in required}
    normalized_headers = {name.lower(): name for name in fieldnames}
    for row in rows:
        if not all(column in normalized_headers for column in REQUIRED_REVIEW_DECISION_TRUTH_COLUMNS):
            continue
        label = _csv_cell(row, normalized_headers, "human_label").lower()
        bucket = _csv_cell(row, normalized_headers, "structural_bucket").lower()
        if label in counts:
            counts[label] += 1
        if bucket in bucket_rows:
            bucket_rows[bucket] += 1
            if label == "true_positive":
                bucket_tp[bucket] += 1
            elif label == "false_positive":
                bucket_fp[bucket] += 1

    labeled_rows = sum(counts.values())
    precision_denominator = counts["true_positive"] + counts["false_positive"]
    overall_precision = (
        round(counts["true_positive"] / precision_denominator, 4)
        if precision_denominator
        else 0.0
    )
    false_positive_rate = (
        round(counts["false_positive"] / precision_denominator, 4)
        if precision_denominator
        else 0.0
    )
    bucket_precision: dict[str, float] = {}
    for bucket in required:
        denominator = bucket_tp[bucket] + bucket_fp[bucket]
        bucket_precision[bucket] = round(bucket_tp[bucket] / denominator, 4) if denominator else 0.0

    if labeled_rows < min_labeled_rows:
        issues.append(f"review_decision_truth labeled rows {labeled_rows} below {min_labeled_rows}")
    if overall_precision < min_overall_precision:
        issues.append(
            f"review_decision_truth overall precision {overall_precision} below {min_overall_precision}"
        )
    if false_positive_rate > max_false_positive_rate:
        issues.append(
            f"review_decision_truth false-positive rate {false_positive_rate} exceeds {max_false_positive_rate}"
        )
    for bucket in required:
        if bucket_rows[bucket] < min_bucket_rows:
            issues.append(
                f"review_decision_truth bucket {bucket} labeled rows {bucket_rows[bucket]} below {min_bucket_rows}"
            )
        if bucket_precision[bucket] < min_bucket_precision:
            issues.append(
                f"review_decision_truth bucket {bucket} precision {bucket_precision[bucket]} below "
                f"{min_bucket_precision}"
            )

    return {
        "status": "passed" if not issues else "failed",
        "path": str(path),
        "rows": len(rows),
        "labeled_rows": labeled_rows,
        "true_positive_count": counts["true_positive"],
        "false_positive_count": counts["false_positive"],
        "hold_count": counts["hold"],
        "overall_precision": overall_precision,
        "false_positive_rate": false_positive_rate,
        "bucket_labeled_rows": bucket_rows,
        "bucket_precision": bucket_precision,
        "thresholds": {
            "min_labeled_rows": min_labeled_rows,
            "min_overall_precision": min_overall_precision,
            "min_bucket_precision": min_bucket_precision,
            "max_false_positive_rate": max_false_positive_rate,
            "min_bucket_rows": min_bucket_rows,
        },
        "issues": issues,
    }


def _review_decision_truth_row_issues(
    row: dict[str, Any],
    normalized_headers: dict[str, str],
    line_no: int,
) -> list[str]:
    issues: list[str] = []
    for column in REQUIRED_REVIEW_DECISION_TRUTH_COLUMNS:
        if not _csv_cell(row, normalized_headers, column):
            issues.append(f"review_decision_truth CSV row {line_no} missing {column}")
    bucket = _csv_cell(row, normalized_headers, "structural_bucket").lower()
    label = _csv_cell(row, normalized_headers, "human_label").lower()
    source_formats = _truth_csv_values(row.get(normalized_headers["source_format"]))
    detection_sources = _truth_csv_values(row.get(normalized_headers["detection_source"]))
    bbox_statuses = _truth_csv_values(row.get(normalized_headers["bbox_status"]))
    if bucket and bucket not in STRUCTURAL_COVERAGE_TERMS:
        issues.append(f"review_decision_truth CSV row {line_no} invalid structural_bucket {bucket}")
    if label and label not in REVIEW_DECISION_LABEL_VALUES:
        issues.append(f"review_decision_truth CSV row {line_no} invalid human_label {label}")
    invalid_sources = sorted(source_formats - SOURCE_FORMAT_VALUES)
    if invalid_sources:
        issues.append(f"review_decision_truth CSV row {line_no} invalid source_format {invalid_sources}")
    invalid_detections = sorted(detection_sources - (CAD_DETECTION_SOURCES | PDF_DETECTION_SOURCES))
    if invalid_detections:
        issues.append(f"review_decision_truth CSV row {line_no} invalid detection_source {invalid_detections}")
    invalid_bbox = sorted(bbox_statuses - BBOX_STATUS_VALUES)
    if invalid_bbox:
        issues.append(f"review_decision_truth CSV row {line_no} invalid bbox_status {invalid_bbox}")
    return issues


def dataset_strata_csv_issues(path: Path, *, expected_sheet_count: int | None = None) -> list[str]:
    return summarize_dataset_strata_csv(path, expected_sheet_count=expected_sheet_count).get("issues", [])


def summarize_dataset_strata_csv(
    path: Path,
    *,
    expected_sheet_count: int | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    fieldnames: list[str] = []
    if not path.exists():
        issues.append("dataset_strata CSV is missing")
    else:
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = [str(name or "").strip() for name in (reader.fieldnames or [])]
                normalized = {name.lower(): name for name in fieldnames}
                missing = [column for column in REQUIRED_DATASET_STRATA_COLUMNS if column not in normalized]
                if missing:
                    issues.append(f"dataset_strata CSV missing required columns {missing}")
                for line_no, row in enumerate(reader, start=2):
                    if not any(str(value or "").strip() for value in row.values()):
                        continue
                    rows.append(row)
                    if missing:
                        continue
                    row_issues = _dataset_strata_row_issues(row, normalized, line_no)
                    row_issues.extend(_template_marker_issues("dataset_strata CSV", row, normalized, line_no))
                    issues.extend(row_issues[:5])
        except Exception as exc:
            issues.append(f"dataset_strata CSV is unreadable: {exc}")
    if not rows and not any("unreadable" in issue for issue in issues):
        issues.append("dataset_strata CSV must contain at least one data row")

    normalized_headers = {name.lower(): name for name in fieldnames}
    format_counts = {value: 0 for value in FORMAT_PAIR_VALUES}
    sheet_counts = {value: 0 for value in SHEET_TYPE_VALUES}
    large_dwg_rows = 0
    block_text_rows = 0
    negative_control_rows = 0
    raster_rows = 0
    for row in rows:
        if not all(column in normalized_headers for column in REQUIRED_DATASET_STRATA_COLUMNS):
            continue
        format_pair = _csv_cell(row, normalized_headers, "format_pair").lower()
        sheet_type = _csv_cell(row, normalized_headers, "sheet_type").lower()
        risk_class = _normalize_csv_enum(_csv_cell(row, normalized_headers, "risk_class"))
        if format_pair in format_counts:
            format_counts[format_pair] += 1
        if sheet_type in sheet_counts:
            sheet_counts[sheet_type] += 1
        if _csv_bool(_csv_cell(row, normalized_headers, "large_dwg")):
            large_dwg_rows += 1
        if _csv_bool(_csv_cell(row, normalized_headers, "block_text_case")):
            block_text_rows += 1
        if _csv_bool(_csv_cell(row, normalized_headers, "negative_control")):
            negative_control_rows += 1
        if risk_class in RASTER_LOW_QUALITY_RISK_VALUES:
            raster_rows += 1

    row_count = len(rows)
    cad_rows = format_counts["dwg_dxf"]
    if expected_sheet_count is not None and row_count != expected_sheet_count:
        issues.append(f"dataset_strata rows {row_count} must equal sheet_count {expected_sheet_count}")
    threshold_checks = (
        ("CAD rows", cad_rows, DEFAULT_MIN_DATASET_STRATA_CAD_ROWS),
        ("PDF-PDF rows", format_counts["pdf_pdf"], DEFAULT_MIN_DATASET_STRATA_PDF_ROWS),
        ("raster/low-quality risk rows", raster_rows, DEFAULT_MIN_DATASET_STRATA_RASTER_ROWS),
        ("large-DWG rows", large_dwg_rows, DEFAULT_MIN_DATASET_STRATA_LARGE_DWG_ROWS),
        ("block-text rows", block_text_rows, DEFAULT_MIN_DATASET_STRATA_BLOCK_TEXT_ROWS),
        ("negative/control rows", negative_control_rows, DEFAULT_MIN_DATASET_STRATA_NEGATIVE_CONTROL_ROWS),
    )
    for label, value, minimum in threshold_checks:
        if value < minimum:
            issues.append(f"dataset_strata {label} {value} below {minimum}")
    for sheet_type, count in sheet_counts.items():
        if count < DEFAULT_MIN_DATASET_STRATA_SHEET_TYPE_ROWS:
            issues.append(
                f"dataset_strata sheet_type {sheet_type} rows {count} below "
                f"{DEFAULT_MIN_DATASET_STRATA_SHEET_TYPE_ROWS}"
            )

    return {
        "status": "passed" if not issues else "failed",
        "path": str(path),
        "rows": row_count,
        "expected_sheet_count": expected_sheet_count,
        "format_pair_counts": format_counts,
        "sheet_type_counts": sheet_counts,
        "cad_rows": cad_rows,
        "raster_or_low_quality_rows": raster_rows,
        "large_dwg_rows": large_dwg_rows,
        "block_text_rows": block_text_rows,
        "negative_control_rows": negative_control_rows,
        "thresholds": {
            "min_cad_rows": DEFAULT_MIN_DATASET_STRATA_CAD_ROWS,
            "min_pdf_pdf_rows": DEFAULT_MIN_DATASET_STRATA_PDF_ROWS,
            "min_raster_or_low_quality_rows": DEFAULT_MIN_DATASET_STRATA_RASTER_ROWS,
            "min_large_dwg_rows": DEFAULT_MIN_DATASET_STRATA_LARGE_DWG_ROWS,
            "min_block_text_rows": DEFAULT_MIN_DATASET_STRATA_BLOCK_TEXT_ROWS,
            "min_sheet_type_rows": DEFAULT_MIN_DATASET_STRATA_SHEET_TYPE_ROWS,
            "min_negative_control_rows": DEFAULT_MIN_DATASET_STRATA_NEGATIVE_CONTROL_ROWS,
        },
        "issues": issues,
    }


def _dataset_strata_row_issues(
    row: dict[str, Any],
    normalized_headers: dict[str, str],
    line_no: int,
) -> list[str]:
    issues: list[str] = []
    for column in REQUIRED_DATASET_STRATA_COLUMNS:
        if not _csv_cell(row, normalized_headers, column):
            issues.append(f"dataset_strata CSV row {line_no} missing {column}")
    format_pair = _csv_cell(row, normalized_headers, "format_pair").lower()
    sheet_type = _csv_cell(row, normalized_headers, "sheet_type").lower()
    if format_pair and format_pair not in FORMAT_PAIR_VALUES:
        issues.append(f"dataset_strata CSV row {line_no} invalid format_pair {format_pair}")
    if sheet_type and sheet_type not in SHEET_TYPE_VALUES:
        issues.append(f"dataset_strata CSV row {line_no} invalid sheet_type {sheet_type}")
    for column in ("large_dwg", "block_text_case", "negative_control"):
        value = _csv_cell(row, normalized_headers, column)
        if value and not _csv_boolish(value):
            issues.append(f"dataset_strata CSV row {line_no} invalid boolean {column}={value}")
    return issues


def summarize_first_interactive_readiness(
    summaries: Sequence[dict[str, Any]],
    *,
    max_review_dashboard_ready_s: float = DEFAULT_MAX_REVIEW_DASHBOARD_READY_S,
    max_speed_review_dashboard_ready_s: float = DEFAULT_MAX_SPEED_REVIEW_DASHBOARD_READY_S,
    max_first_top_issue_ready_s: float = DEFAULT_MAX_FIRST_TOP_ISSUE_READY_S,
    max_viewer_metadata_ready_s: float = DEFAULT_MAX_VIEWER_METADATA_READY_S,
) -> dict[str, Any]:
    completed = [
        summary for summary in summaries if _int(_nested(summary, "comparison", "completed_pairs")) > 0
    ]
    issues: list[str] = []
    max_dashboard = 0.0
    max_top_issue = 0.0
    max_viewer = 0.0
    measured = 0
    for summary in completed:
        label = str(summary.get("output_dir") or "<validation_summary>")
        block = summary.get("first_interactive_ready") or summary.get("first_interactive_readiness")
        if not isinstance(block, dict):
            issues.append(f"{label}: missing first_interactive_ready block")
            continue
        measured += 1
        dashboard_s = _float(block.get("review_dashboard_ready_s"))
        top_issue_s = _float(block.get("first_top_issue_ready_s"))
        viewer_s = _float(block.get("viewer_metadata_ready_s"))
        speed_profile = block.get("speed_profile") is True or str(block.get("profile") or "").lower() in {
            "speed",
            "fast",
            "ultra_fast",
        }
        dashboard_budget = max_speed_review_dashboard_ready_s if speed_profile else max_review_dashboard_ready_s
        max_dashboard = max(max_dashboard, dashboard_s)
        max_top_issue = max(max_top_issue, top_issue_s)
        max_viewer = max(max_viewer, viewer_s)
        if dashboard_s <= 0:
            issues.append(f"{label}: review_dashboard_ready_s is required")
        elif dashboard_s > dashboard_budget:
            issues.append(f"{label}: review_dashboard_ready_s={dashboard_s} exceeds {dashboard_budget}")
        if top_issue_s <= 0:
            issues.append(f"{label}: first_top_issue_ready_s is required")
        elif top_issue_s > max_first_top_issue_ready_s:
            issues.append(f"{label}: first_top_issue_ready_s={top_issue_s} exceeds {max_first_top_issue_ready_s}")
        if viewer_s <= 0:
            issues.append(f"{label}: viewer_metadata_ready_s is required")
        elif viewer_s > max_viewer_metadata_ready_s:
            issues.append(f"{label}: viewer_metadata_ready_s={viewer_s} exceeds {max_viewer_metadata_ready_s}")
        if block.get("status") not in {None, "passed"}:
            issues.append(f"{label}: first_interactive_ready.status must be passed")
    if not completed:
        issues.append("at least one completed validation output is required for first interactive readiness")
    elif measured < len(completed):
        issues.append(f"first_interactive_ready block required for every completed output ({measured}/{len(completed)})")
    return {
        "status": "passed" if not issues else "failed",
        "completed_outputs": len(completed),
        "measured_outputs": measured,
        "max_review_dashboard_ready_s": max_dashboard,
        "max_first_top_issue_ready_s": max_top_issue,
        "max_viewer_metadata_ready_s": max_viewer,
        "thresholds": {
            "max_review_dashboard_ready_s": max_review_dashboard_ready_s,
            "max_speed_review_dashboard_ready_s": max_speed_review_dashboard_ready_s,
            "max_first_top_issue_ready_s": max_first_top_issue_ready_s,
            "max_viewer_metadata_ready_s": max_viewer_metadata_ready_s,
        },
        "issues": issues,
    }


def summarize_bbox_quality(
    summaries: Sequence[dict[str, Any]],
    *,
    max_relative_only_ratio: float = DEFAULT_MAX_BBOX_RELATIVE_ONLY_RATIO,
    max_page_fallback_ratio: float = DEFAULT_MAX_BBOX_PAGE_FALLBACK_RATIO,
) -> dict[str, Any]:
    statuses: list[str] = []
    top_priority_relative_only = False
    for summary in summaries:
        items = _queue_items(summary)
        for index, item in enumerate(items):
            status = str(item.get("bbox_status") or "").strip().lower()
            if status in BBOX_STATUS_VALUES:
                statuses.append(status)
                priority_rank = _int(item.get("priority_rank") or item.get("rank") or (index + 1))
                if status == "relative_only" and priority_rank <= 3:
                    top_priority_relative_only = True
        _collect_bbox_statuses(summary.get("selected_zone_evidence"), statuses)
        _collect_bbox_statuses(summary.get("selected_zone_render_evidence"), statuses)
    total = len(statuses)
    relative_count = statuses.count("relative_only")
    page_count = statuses.count("page_fallback")
    relative_ratio = round(relative_count / total, 4) if total else 0.0
    page_ratio = round(page_count / total, 4) if total else 0.0
    issues: list[str] = []
    if total <= 0:
        issues.append("no bbox_status evidence found in review queue or selected-zone evidence")
    if top_priority_relative_only:
        issues.append("top-priority review queue item uses bbox_status=relative_only")
    if relative_ratio > max_relative_only_ratio:
        issues.append(f"relative_only ratio {relative_ratio} exceeds {max_relative_only_ratio}")
    if page_ratio > max_page_fallback_ratio:
        issues.append(f"page_fallback ratio {page_ratio} exceeds {max_page_fallback_ratio}")
    return {
        "status": "passed" if not issues else "failed",
        "bbox_status_count": total,
        "exact_count": statuses.count("exact"),
        "relative_only_count": relative_count,
        "page_fallback_count": page_count,
        "relative_only_ratio": relative_ratio,
        "page_fallback_ratio": page_ratio,
        "top_priority_relative_only": top_priority_relative_only,
        "thresholds": {
            "max_relative_only_ratio": max_relative_only_ratio,
            "max_page_fallback_ratio": max_page_fallback_ratio,
        },
        "issues": issues,
    }


def _collect_bbox_statuses(payload: Any, statuses: list[str]) -> None:
    if isinstance(payload, dict):
        status = str(payload.get("bbox_status") or "").strip().lower()
        if status in BBOX_STATUS_VALUES:
            statuses.append(status)
        for value in payload.values():
            _collect_bbox_statuses(value, statuses)
    elif isinstance(payload, list):
        for item in payload:
            _collect_bbox_statuses(item, statuses)


def summarize_large_dwg_resource_probe(
    probe: dict[str, Any] | None,
    *,
    max_peak_rss_mb: float = DEFAULT_MAX_LARGE_DWG_PEAK_RSS_MB,
    max_progress_gap_s: float = DEFAULT_MAX_LARGE_DWG_PROGRESS_GAP_S,
    max_cancel_to_idle_s: float = DEFAULT_MAX_LARGE_DWG_CANCEL_TO_IDLE_S,
) -> dict[str, Any]:
    issues: list[str] = []
    payload = probe if isinstance(probe, dict) else {}
    peak_rss = _float(payload.get("peak_rss_mb"))
    progress_gap = _float(payload.get("progress_max_gap_s"))
    cancel_probe = payload.get("cancel_probe") if isinstance(payload.get("cancel_probe"), dict) else {}
    cancel_to_idle = _float(cancel_probe.get("cancel_to_idle_s"))
    worker_processes_left = _int(cancel_probe.get("worker_processes_left"))
    if not payload:
        issues.append("large-DWG resource probe JSON is required")
    if peak_rss <= 0:
        issues.append("peak_rss_mb must be > 0")
    elif peak_rss > max_peak_rss_mb:
        issues.append(f"peak_rss_mb={peak_rss} exceeds {max_peak_rss_mb}")
    if progress_gap <= 0:
        issues.append("progress_max_gap_s must be > 0")
    elif progress_gap > max_progress_gap_s:
        issues.append(f"progress_max_gap_s={progress_gap} exceeds {max_progress_gap_s}")
    if cancel_probe.get("status") != "passed":
        issues.append("cancel_probe.status must be passed")
    if cancel_to_idle <= 0:
        issues.append("cancel_probe.cancel_to_idle_s must be > 0")
    elif cancel_to_idle > max_cancel_to_idle_s:
        issues.append(f"cancel_probe.cancel_to_idle_s={cancel_to_idle} exceeds {max_cancel_to_idle_s}")
    if cancel_probe.get("partial_outputs_cleaned") is not True:
        issues.append("cancel_probe.partial_outputs_cleaned must be true")
    if worker_processes_left != 0:
        issues.append(f"cancel_probe.worker_processes_left={worker_processes_left} must be 0")
    return {
        "status": "passed" if not issues else "failed",
        "peak_rss_mb": peak_rss,
        "progress_max_gap_s": progress_gap,
        "cancel_probe": {
            "status": str(cancel_probe.get("status") or ""),
            "cancel_to_idle_s": cancel_to_idle,
            "partial_outputs_cleaned": cancel_probe.get("partial_outputs_cleaned") is True,
            "worker_processes_left": worker_processes_left,
        },
        "thresholds": {
            "max_peak_rss_mb": max_peak_rss_mb,
            "max_progress_gap_s": max_progress_gap_s,
            "max_cancel_to_idle_s": max_cancel_to_idle_s,
        },
        "issues": issues,
    }


def _csv_cell(row: dict[str, Any], normalized_headers: dict[str, str], column: str) -> str:
    return str(row.get(normalized_headers.get(column, column)) or "").strip()


def _template_marker_issues(
    label: str,
    row: dict[str, Any],
    normalized_headers: dict[str, str],
    line_no: int,
) -> list[str]:
    issues: list[str] = []
    for column, original_header in normalized_headers.items():
        value = str(row.get(original_header) or "").strip().lower()
        if value and any(marker in value for marker in DISALLOWED_REVIEW_GROUND_TRUTH_MARKERS):
            issues.append(f"{label} row {line_no} contains template/example marker in {column}")
    return issues


def _normalize_csv_enum(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _csv_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "x", "checked"}


def _csv_boolish(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "0",
        "1",
        "true",
        "false",
        "yes",
        "no",
        "y",
        "n",
        "x",
        "checked",
    }


def _csv_data_row_count(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return sum(1 for _ in csv.DictReader(handle))
    except Exception:
        return 0


def review_ground_truth_csv_issues(path: Path) -> list[str]:
    if not path.exists():
        return ["review_ground_truth CSV is missing"]
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = [str(name or "").strip() for name in (reader.fieldnames or [])]
            normalized = {name.lower(): name for name in fieldnames}
            issues: list[str] = []
            missing_columns = [
                column for column in REQUIRED_REVIEW_GROUND_TRUTH_COLUMNS if column not in normalized
            ]
            if missing_columns:
                issues.append(f"review_ground_truth CSV missing required columns {missing_columns}")
            row_count = 0
            for line_no, row in enumerate(reader, start=2):
                if not any(str(value or "").strip() for value in row.values()):
                    continue
                row_count += 1
                if missing_columns:
                    continue
                row_issues = _review_ground_truth_row_issues(row, normalized, line_no)
                row_issues.extend(_review_ground_truth_template_marker_issues(row, normalized, line_no))
                issues.extend(row_issues[:5])
            if row_count <= 0:
                issues.append("review_ground_truth CSV must contain at least one data row")
            return issues
    except Exception as exc:
        return [f"review_ground_truth CSV is unreadable: {exc}"]


def _review_ground_truth_row_issues(
    row: dict[str, Any],
    normalized_headers: dict[str, str],
    line_no: int,
) -> list[str]:
    issues: list[str] = []
    for column in REQUIRED_REVIEW_GROUND_TRUTH_COLUMNS:
        value = str(row.get(normalized_headers[column]) or "").strip()
        if not value:
            issues.append(f"review_ground_truth CSV row {line_no} missing {column}")
    source_formats = _truth_csv_values(row.get(normalized_headers["source_format"]))
    detection_sources = _truth_csv_values(row.get(normalized_headers["detection_source"]))
    bbox_statuses = _truth_csv_values(row.get(normalized_headers["bbox_status"]))
    invalid_sources = sorted(source_formats - SOURCE_FORMAT_VALUES)
    if invalid_sources:
        issues.append(f"review_ground_truth CSV row {line_no} invalid source_format {invalid_sources}")
    allowed_detection_sources = CAD_DETECTION_SOURCES | PDF_DETECTION_SOURCES
    invalid_detections = sorted(detection_sources - allowed_detection_sources)
    if invalid_detections:
        issues.append(
            f"review_ground_truth CSV row {line_no} invalid detection_source {invalid_detections}"
        )
    invalid_bbox = sorted(bbox_statuses - BBOX_STATUS_VALUES)
    if invalid_bbox:
        issues.append(f"review_ground_truth CSV row {line_no} invalid bbox_status {invalid_bbox}")
    return issues


def _review_ground_truth_template_marker_issues(
    row: dict[str, Any],
    normalized_headers: dict[str, str],
    line_no: int,
) -> list[str]:
    issues: list[str] = []
    for column, original_header in normalized_headers.items():
        value = str(row.get(original_header) or "").strip().lower()
        if not value:
            continue
        if any(marker in value for marker in DISALLOWED_REVIEW_GROUND_TRUTH_MARKERS):
            issues.append(
                f"review_ground_truth CSV row {line_no} contains template/example marker in {column}"
            )
    return issues


def _truth_csv_values(value: Any) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    parts = text.replace("|", ";").split(";")
    return {part.strip() for part in parts if part.strip()}


if __name__ == "__main__":
    raise SystemExit(main())
