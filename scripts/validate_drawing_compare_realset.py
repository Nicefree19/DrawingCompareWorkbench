"""Validate drawing compare matching and batch compare on real A/B sets."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import tracemalloc
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.base import ComparisonResult
from src.services.comparison.change_zones import (
    CloudMarkOptions,
    export_change_artifacts,
    export_executive_review_from_artifacts,
)
from src.services.comparison.comparison_config import ComparisonConfig
from src.services.comparison.drawing_batch import (
    BatchCompareJob,
    BatchCompareOptions,
    BatchCompareSummary,
    DescriptorBuildOptions,
    DrawingFileDescriptor,
    DrawingKind,
    FilenameIdentity,
    MatchAlternative,
    MatchCandidate,
    MatchStatus,
    apply_manual_matches,
    are_compatible,
    confirmed_pair_uniqueness_violations,
    load_compare_state,
    load_manual_match_csv,
    match_drawing_sets,
    parse_filename_identity,
    scan_drawing_inputs,
)
from src.services.comparison.folder_compare_pipeline import (
    FolderComparePipeline,
    FolderCompareRunRequest,
)
from src.services.comparison.export_profiles import (
    SENSITIVE_PATH_KEYS,
    apply_export_profile_to_file,
    audit_sharable_paths as audit_package_sharable_paths,
    normalize_export_profile,
    redact_payload_paths,
)
from src.services.comparison.preflight import run_preflight
from src.services.comparison.review_project import (
    export_preview_artifacts,
    save_review_state,
    update_artifact_manifest,
    write_review_project,
)
from src.services.comparison.review_dashboard import (
    export_review_dashboard,
)
from src.services.comparison.run_contract import RunManifestWriter
from src.services.comparison.runtime_budget import RuntimeBudgetSampler
from src.services.comparison.viewer_package import export_viewer_package
from src.services.comparison.viewer_perf_summary import summarize_viewer_perf
from src.services.comparison.viewer_tile_cache import append_viewer_perf_event
from src.services.comparison.zone_render_outcome import aggregate_zone_outcomes
from src.services.comparison.zone_render_service import (
    RenderJob,
    canonical_window_from_bbox,
    render_zone_pair,
    union_bboxes,
)


MATCH_CSV_COLUMNS = [
    "status",
    "score",
    "a_path",
    "b_path",
    "a_kind",
    "b_kind",
    "a_extension",
    "b_extension",
    "a_drawing_number",
    "b_drawing_number",
    "a_sheet",
    "b_sheet",
    "reasons",
    "component_scores_json",
    "alternate_count",
    "alternates_json",
]

COMPARE_CSV_COLUMNS = [
    "status",
    "a_path",
    "b_path",
    "match_status",
    "match_score",
    "changes",
    "added",
    "deleted",
    "modified",
    "change_records_in_memory",
    "truncated_changes",
    "large_drawing_mode",
    "index_backend",
    "change_zone_stream_path",
    "change_zone_record_count",
    "change_zone_stream_complete",
    "error",
    "warnings",
    "change_zones",
    "after_marked_dxf",
    "before_marked_dxf",
]

QUALITY_SCHEMA_VERSION = 1
DEFAULT_MIN_AUTO_PRECISION = 0.99
DEFAULT_MIN_RECALL = 0.95
DEFAULT_MAX_MATCH_TIME_REGRESSION = 0.30

REVIEW_QUEUE_CSV_COLUMNS = [
    "status",
    "score",
    "a_path",
    "b_path",
    "a_drawing_number",
    "b_drawing_number",
    "reasons",
    "alternate_1_path",
    "alternate_1_drawing_number",
    "alternate_1_score",
    "alternate_2_path",
    "alternate_2_drawing_number",
    "alternate_2_score",
    "alternate_3_path",
    "alternate_3_drawing_number",
    "alternate_3_score",
]

PAIR_CSV_COLUMNS = ["status", "a_path", "b_path", "score", "reasons"]
BLOCKED_CSV_COLUMNS = ["a_path", "b_path", "a_kind", "b_kind", "reason"]
MANUAL_TEMPLATE_COLUMNS = ["a_path", "b_path", "status"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", type=Path, help="Old/A file or folder")
    parser.add_argument("--b", type=Path, help="New/B file or folder")
    parser.add_argument("--a-folder", type=Path, help="Korean UX simple mode: before folder")
    parser.add_argument("--b-folder", type=Path, help="Korean UX simple mode: after folder")
    parser.add_argument("--auto-run", action="store_true", help="Run the simplified folder compare pipeline")
    parser.add_argument("--open-result", action="store_true", help="Open executive_review.html after --auto-run")
    parser.add_argument("--manifest", type=Path, help="YAML/JSON manifest with multiple A/B sets")
    parser.add_argument("--recursive", action="store_true", help="Scan folders recursively")
    parser.add_argument("--out", required=True, type=Path, help="Output folder")
    parser.add_argument(
        "--measure-runtime-budget",
        action="store_true",
        help=(
            "Activate RuntimeBudgetSampler to record peak working-set memory, "
            "first-review-ready timing, and tempdir spool size. Required when "
            "the audit script is run with --require-runtime-budget."
        ),
    )
    parser.add_argument("--ground-truth", type=Path, help="CSV: a_path,b_path,expected_status")
    parser.add_argument(
        "--review-ground-truth",
        type=Path,
        help=(
            "CSV for structural review queue recall: drawing_label,category,"
            "summary_contains,source_format,detection_source,bbox_status"
        ),
    )
    parser.add_argument("--manual-matches", type=Path, help="CSV: a_path,b_path,status")
    parser.add_argument(
        "--write-ground-truth-template",
        action="store_true",
        help="Write ground_truth_template.csv next to validation reports",
    )
    parser.add_argument("--skip-compare", action="store_true", help="Only scan and match")
    parser.add_argument("--max-workers", type=int, help="Batch compare worker limit")
    parser.add_argument("--no-cache", action="store_true", help="Disable descriptor cache")
    parser.add_argument(
        "--no-expand-blocks",
        action="store_true",
        help=(
            "Compare CAD INSERTs without expanding block geometry. Block "
            "attribute/text fingerprint detection remains enabled unless "
            "--no-block-text-detection is also supplied."
        ),
    )
    parser.add_argument(
        "--no-block-text-detection",
        action="store_true",
        help="Disable CAD INSERT block attribute/text fingerprint detection.",
    )
    parser.add_argument(
        "--reuse-match-candidates",
        type=Path,
        help="Use a previous match_candidates.csv and skip scan/match",
    )
    parser.add_argument(
        "--dxf-cache-dir",
        type=Path,
        help="Persistent DWG->DXF conversion cache folder",
    )
    parser.add_argument(
        "--export-cloud-marks",
        action="store_true",
        help="Generate cloud-marked DXF files from grouped change zones after compare",
    )
    parser.add_argument(
        "--export-before-cloud-marks",
        action="store_true",
        help="Also generate before-DXF marks for deleted or moved-origin regions",
    )
    parser.add_argument(
        "--cloud-export-mode",
        choices=("selected", "all", "csv", "off"),
        default="selected",
        help="Cloud-mark export policy when --export-cloud-marks is used",
    )
    parser.add_argument(
        "--cloud-selection-csv",
        type=Path,
        help="CSV with pair_id,zone_id rows for --cloud-export-mode csv",
    )
    parser.add_argument("--cloud-region-distance", type=float, default=1000.0)
    parser.add_argument("--max-cloud-regions-per-pair", type=int, default=150)
    parser.add_argument("--max-cloud-regions-total", type=int, default=3000)
    parser.add_argument(
        "--change-zone-report",
        action="store_true",
        help="Generate change_zones JSON/CSV, review_index HTML, and change register",
    )
    parser.add_argument(
        "--executive-review",
        action="store_true",
        help="Generate lightweight executive_review.html and drawing brief outputs",
    )
    parser.add_argument("--executive-top-drawings", type=int, default=15)
    parser.add_argument("--executive-top-zones", type=int, default=30)
    parser.add_argument(
        "--review-dashboard",
        action="store_true",
        help="Generate review_dashboard.json, review_priority.csv, and layer_pattern_summary.csv",
    )
    parser.add_argument("--top-review-issues", type=int, default=100)
    parser.add_argument("--top-issues-per-drawing", type=int, default=20)
    fold_group = parser.add_mutually_exclusive_group()
    fold_group.add_argument(
        "--fold-repetitive-layers",
        dest="fold_repetitive_layers",
        action="store_true",
        default=True,
        help="Fold repetitive layer-pattern changes in review dashboard",
    )
    fold_group.add_argument(
        "--no-fold-repetitive-layers",
        dest="fold_repetitive_layers",
        action="store_false",
        help="Do not fold repetitive layer-pattern changes in review dashboard",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="Output folder for change-zone reports and cloud-marked artifacts",
    )
    parser.add_argument(
        "--review-state",
        type=Path,
        help="Local review_state.json path shared by CLI and Workbench",
    )
    parser.add_argument(
        "--export-preview",
        action="store_true",
        help="Render static before/after preview PNGs and preview_manifest.json",
    )
    parser.add_argument("--preview-dpi", type=int, default=80)
    parser.add_argument("--preview-max-edge-px", type=int, default=2400)
    parser.add_argument(
        "--max-preview-pairs",
        type=int,
        default=5,
        help="Maximum completed pairs to render as PNG previews; 0 writes metadata only",
    )
    parser.add_argument(
        "--export-viewer-package",
        action="store_true",
        help="Generate lightweight viewer manifest and per-pair overlay JSON",
    )
    parser.add_argument(
        "--viewer-mode",
        choices=("pdf-overlay", "image-tiles"),
        default="image-tiles",
        help="Viewer background mode; overlays are always CAD-derived",
    )
    parser.add_argument(
        "--viewer-render-policy",
        choices=("lazy", "top-issues", "all"),
        default="lazy",
        help="Viewer render policy; lazy writes overlay metadata without full renders",
    )
    parser.add_argument("--viewer-engine", choices=("auto", "qtquick", "qtquick-widget", "qtquick-window", "widgets"), default="auto")
    parser.add_argument("--viewer-cache-dir", type=Path, help="Persistent GPU/tile viewer cache folder")
    parser.add_argument("--tile-size", type=int, default=512)
    parser.add_argument("--max-visible-overlays", type=int, default=500)
    parser.add_argument("--viewer-memory-budget-mb", type=int, default=512)
    parser.add_argument("--render-selected-on-open", action="store_true")
    parser.add_argument("--prefetch-neighbor-tiles", action="store_true", default=True)
    parser.add_argument("--tile-prefetch-radius", type=int, default=1)
    parser.add_argument("--overview-max-edge", type=int, default=2200)
    parser.add_argument("--focus-tile-max-edge", type=int, default=1600)
    parser.add_argument("--viewer-perf-log", action="store_true")
    parser.add_argument(
        "--render-selected-zone-evidence",
        action="store_true",
        help="Render selected/top review zones during validation and append cold/cache-hit telemetry to viewer_perf.json",
    )
    parser.add_argument(
        "--selected-zone-evidence-per-pair",
        type=int,
        default=1,
        help="Maximum selected/top zones to render per pair when --render-selected-zone-evidence is enabled",
    )
    parser.add_argument(
        "--viewer-render-timeout-seconds",
        type=int,
        default=0,
        help="Timebox each batch viewer background render; 0 disables timeout",
    )
    parser.add_argument("--max-viewer-pages", type=int, default=30)
    parser.add_argument("--max-zone-tiles", type=int, default=300)
    parser.add_argument(
        "--export-marked-pdf",
        action="store_true",
        help="Generate marked PDF annotations where source B is already a PDF",
    )
    parser.add_argument(
        "--marked-pdf-mode",
        choices=("selected", "all", "csv", "off"),
        default="selected",
        help="Marked PDF annotation selection policy",
    )
    parser.add_argument(
        "--compare-state-dir",
        type=Path,
        help="Folder for persisted compare metadata and change-zone streams",
    )
    parser.add_argument(
        "--reuse-compare-state",
        type=Path,
        help="Reuse a previous compare state and regenerate reports/artifacts without compare",
    )
    parser.add_argument("--baseline", type=Path, help="Baseline JSON for quality gate comparison")
    parser.add_argument("--update-baseline", action="store_true", help="Write current metrics as baseline")
    parser.add_argument("--quality-gate", action="store_true", help="Fail the process when quality gate fails")
    parser.add_argument("--min-auto-precision", type=float, default=DEFAULT_MIN_AUTO_PRECISION)
    parser.add_argument("--min-recall", type=float, default=DEFAULT_MIN_RECALL)
    parser.add_argument(
        "--export-profile",
        choices=("internal", "sharable"),
        default="internal",
        help="Artifact path policy; sharable redacts source/cache/state paths",
    )
    parser.add_argument("--preflight-only", action="store_true", help="Only run operational preflight checks")
    parser.add_argument(
        "--allow-long-path-warning",
        action="store_true",
        help="Treat Windows long-path findings as warnings instead of errors",
    )
    parser.add_argument(
        "--max-match-time-regression",
        type=float,
        default=DEFAULT_MAX_MATCH_TIME_REGRESSION,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if getattr(args, "preflight_only", False):
            payload = run_preflight_only(args)
        elif getattr(args, "auto_run", False):
            payload = run_simple_folder_compare(args)
        elif args.manifest:
            payload = run_manifest_validation(args)
        else:
            if not args.a or not args.b:
                raise SystemExit("--a and --b are required unless --manifest is provided")
            payload = run_validation(args)
    except Exception as exc:
        _write_failed_sentinel_from_args(args, exc)
        raise
    status = "failed" if _payload_quality_failed(payload) else "passed"
    print(
        json.dumps(
            {
                "status": status,
                "out": payload["output_dir"],
                "summary_json": payload["outputs"]["summary_json"],
                "html_report": payload["outputs"].get("html_report")
                or payload["outputs"].get("index_html"),
                "quality_gate": payload["outputs"].get("quality_gate_json"),
            },
            ensure_ascii=False,
        )
    )
    return 1 if status == "failed" else 0


def _write_failed_sentinel_from_args(args: argparse.Namespace, exc: BaseException) -> None:
    out_dir = getattr(args, "out", None)
    if out_dir is None:
        return
    try:
        run_manifest = RunManifestWriter(Path(out_dir).resolve())
        if not run_manifest.path.exists():
            run_manifest.start(
                inputs={
                    "source_a": getattr(args, "a", None) or getattr(args, "a_folder", None),
                    "source_b": getattr(args, "b", None) or getattr(args, "b_folder", None),
                    "recursive": bool(getattr(args, "recursive", False)),
                },
                paths={"output_dir": Path(out_dir).resolve()},
            )
        run_manifest.fail("validation", exc)
    except Exception:
        return


def run_preflight_only(args: argparse.Namespace) -> dict[str, Any]:
    source_a = getattr(args, "a_folder", None) or getattr(args, "a", None)
    source_b = getattr(args, "b_folder", None) or getattr(args, "b", None)
    if not source_a or not source_b:
        raise SystemExit("--a/--b or --a-folder/--b-folder are required with --preflight-only")
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dxf_cache_dir = _dxf_cache_dir_for_args(args, out_dir) or out_dir / "dxf_cache"
    compare_state_dir = _compare_state_dir_for_args(args, out_dir) or out_dir / "compare_state"
    result = run_preflight(
        source_a=source_a,
        source_b=source_b,
        output_dir=out_dir,
        dxf_cache_dir=dxf_cache_dir,
        compare_state_dir=compare_state_dir,
        allow_long_path_warning=bool(getattr(args, "allow_long_path_warning", False)),
    )
    summary_path = out_dir / "validation_summary.json"
    preflight_path = out_dir / "preflight_report.json"
    quality_path = out_dir / "quality_gate.json"
    ai_policy_path = out_dir / "ai_policy.json"
    ai_policy = _build_ai_policy_evidence(out_dir)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "mode": "preflight-only",
        "input": {
            "source_a": str(Path(source_a).resolve()),
            "source_b": str(Path(source_b).resolve()),
        },
        "output_dir": str(out_dir),
        "outputs": {
            "summary_json": str(summary_path),
            "preflight_report_json": str(preflight_path),
            "quality_gate_json": str(quality_path),
            "ai_policy_json": str(ai_policy_path),
        },
        "preflight_result": result.to_dict(),
        "ai_policy": ai_policy,
        "quality_gate": {
            "status": "failed" if result.errors else "passed",
            "issues": [
                {"metric": check.name, "actual": check.status, "threshold": "ok", "message": check.message}
                for check in result.errors
            ],
            "requested": bool(getattr(args, "quality_gate", False)),
        },
    }
    _write_json(preflight_path, result.to_dict())
    _write_json(ai_policy_path, ai_policy)
    _write_json(summary_path, payload)
    _write_quality_gate(quality_path, payload["quality_gate"])
    return payload


def run_simple_folder_compare(args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "a_folder", None) or not getattr(args, "b_folder", None):
        raise SystemExit("--a-folder and --b-folder are required with --auto-run")
    request = FolderCompareRunRequest(
        source_a=args.a_folder,
        source_b=args.b_folder,
        output_dir=args.out,
        recursive=bool(getattr(args, "recursive", False)),
        enable_descriptor_cache=not bool(getattr(args, "no_cache", False)),
        dxf_cache_dir=getattr(args, "dxf_cache_dir", None),
        compare_state_dir=getattr(args, "compare_state_dir", None),
        max_workers=getattr(args, "max_workers", None),
        max_preview_pairs=int(getattr(args, "max_preview_pairs", 5)),
        top_review_issues=int(getattr(args, "top_review_issues", 100) or 100),
        top_issues_per_drawing=int(getattr(args, "top_issues_per_drawing", 20) or 20),
        fold_repetitive_layers=bool(getattr(args, "fold_repetitive_layers", True)),
        viewer_mode=str(getattr(args, "viewer_mode", "image-tiles")),
        viewer_render_policy=str(getattr(args, "viewer_render_policy", "lazy")),
        viewer_engine=str(getattr(args, "viewer_engine", "auto")),
        viewer_cache_dir=getattr(args, "viewer_cache_dir", None),
        tile_size=int(getattr(args, "tile_size", 512)),
        max_visible_overlays=int(getattr(args, "max_visible_overlays", 500)),
        viewer_memory_budget_mb=int(getattr(args, "viewer_memory_budget_mb", 512)),
        render_selected_on_open=bool(getattr(args, "render_selected_on_open", False)),
        prefetch_neighbor_tiles=bool(getattr(args, "prefetch_neighbor_tiles", True)),
        tile_prefetch_radius=int(getattr(args, "tile_prefetch_radius", 1)),
        overview_max_edge=int(getattr(args, "overview_max_edge", 2200)),
        focus_tile_max_edge=int(getattr(args, "focus_tile_max_edge", 1600)),
        viewer_perf_log=bool(getattr(args, "viewer_perf_log", False)),
        max_viewer_pages=int(getattr(args, "max_viewer_pages", 30)),
        max_zone_tiles=int(getattr(args, "max_zone_tiles", 300)),
        export_marked_pdf=bool(getattr(args, "export_marked_pdf", False)),
        marked_pdf_mode=str(getattr(args, "marked_pdf_mode", "selected")),
        export_profile=str(getattr(args, "export_profile", "internal")),
        allow_long_path_warning=bool(getattr(args, "allow_long_path_warning", False)),
    )
    result = FolderComparePipeline(request).run()
    summary_path = Path(result.output_dir) / "validation_summary.json"
    quality_path = Path(result.output_dir) / "quality_gate.json"
    ai_policy_path = Path(result.output_dir) / "ai_policy.json"
    ai_policy = _build_ai_policy_evidence(Path(result.output_dir))
    executive_path = result.executive_package.output_paths.get("executive_review_html")
    payload = {
        "generated_at": datetime.now().isoformat(),
        "mode": "ko-simple-v2",
        "input": {
            "a_folder": str(Path(args.a_folder).resolve()),
            "b_folder": str(Path(args.b_folder).resolve()),
            "recursive": bool(getattr(args, "recursive", False)),
        },
        "output_dir": result.output_dir,
        "outputs": {
            "summary_json": str(summary_path),
            "html_report": executive_path,
            "quality_gate_json": str(quality_path),
            "run_manifest_json": result.run_manifest_path,
            "success_sentinel_json": result.success_sentinel_path,
            "preflight_report_json": result.preflight_report_path,
            "ai_policy_json": str(ai_policy_path),
            "artifact_dir": result.artifact_dir,
            "preview_manifest_json": result.preview_package.manifest_path,
            "review_dashboard_json": result.executive_package.output_paths.get("review_dashboard_json"),
            "review_priority_csv": result.executive_package.output_paths.get("review_priority_csv"),
            "layer_pattern_summary_csv": result.executive_package.output_paths.get("layer_pattern_summary_csv"),
            "viewer_manifest_json": result.viewer_package.output_paths.get("viewer_manifest_json"),
            "viewer_index_html": result.viewer_package.output_paths.get("viewer_index_html"),
        },
        "matching": {
            "confirmed_pairs": result.confirmed_pairs,
            "review_required": result.review_required_pairs,
            "unmatched_a": result.unmatched_a,
            "unmatched_b": result.unmatched_b,
        },
        "comparison": result.compare_summary.to_dict(),
        "change_artifacts": result.artifact_package.to_dict(),
        "preview_artifacts": result.preview_package.to_dict(),
        "executive_review": result.executive_package.to_dict(),
        "viewer_package": result.viewer_package.to_dict(),
        "preflight_result": result.preflight_result.to_dict(),
        "ai_policy": ai_policy,
        "run_manifest": result.run_manifest_path,
        "quality_gate": {
            "status": "failed" if result.compare_summary.failed_pairs else "passed",
            "issues": [],
        },
    }
    _write_json(ai_policy_path, ai_policy)
    _write_json(summary_path, payload)
    _write_quality_gate(quality_path, payload["quality_gate"])
    if getattr(args, "open_result", False) and executive_path:
        os.startfile(executive_path)  # type: ignore[attr-defined]
    return payload


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tracemalloc.start()
    total_started = time.perf_counter()
    runtime_sampler: RuntimeBudgetSampler | None = None
    if bool(getattr(args, "measure_runtime_budget", False)):
        spool_dirs: list[Path] = []
        for candidate in (out_dir, getattr(args, "dxf_cache_dir", None)):
            if candidate is None:
                continue
            try:
                spool_dirs.append(Path(candidate))
            except (TypeError, ValueError):
                continue
        runtime_sampler = RuntimeBudgetSampler(spool_dirs=spool_dirs)
        runtime_sampler.start_sampling()
    root_a = _scan_root(args.a)
    root_b = _scan_root(args.b)
    dxf_cache_dir = _dxf_cache_dir_for_args(args, out_dir)
    compare_state_dir = _compare_state_dir_for_args(args, out_dir)
    review_state_path = _review_state_path_for_args(args, out_dir)
    preflight_path = out_dir / "preflight_report.json"
    preflight_result = run_preflight(
        source_a=args.a,
        source_b=args.b,
        output_dir=out_dir,
        dxf_cache_dir=dxf_cache_dir or out_dir / "dxf_cache",
        compare_state_dir=compare_state_dir or out_dir / "compare_state",
        allow_long_path_warning=bool(getattr(args, "allow_long_path_warning", False)),
    )
    _write_json(preflight_path, preflight_result.to_dict())
    run_manifest = RunManifestWriter(out_dir)
    run_manifest.start(
        inputs={"source_a": args.a, "source_b": args.b, "recursive": bool(args.recursive)},
        paths={
            "output_dir": out_dir,
            "dxf_cache_dir": dxf_cache_dir,
            "compare_state_dir": compare_state_dir,
            "preflight_report_json": preflight_path,
        },
        preflight=preflight_result.to_dict(),
    )
    run_manifest.stage("preflight", "completed", preflight_status=preflight_result.status)
    if preflight_result.errors:
        message = "Preflight failed: " + "; ".join(check.message for check in preflight_result.errors[:3])
        run_manifest.fail("preflight", message)
        raise RuntimeError(message)
    scan_options = DescriptorBuildOptions(
        recursive=bool(args.recursive),
        enable_cache=not bool(args.no_cache),
        dxf_cache_dir=dxf_cache_dir,
    )

    compare_summary: BatchCompareSummary | None = None
    scan_started = time.perf_counter()
    if getattr(args, "reuse_compare_state", None):
        compare_summary = load_compare_state(args.reuse_compare_state)
        candidates = _candidates_from_compare_summary(compare_summary)
        descriptors_a, descriptors_b = _descriptors_from_candidates(candidates, compare_summary)
    elif getattr(args, "reuse_match_candidates", None):
        candidates, descriptors_a, descriptors_b = _load_match_candidates_csv(
            args.reuse_match_candidates,
            root_a=root_a,
            root_b=root_b,
        )
    else:
        descriptors_a = scan_drawing_inputs(args.a, scan_options, root=root_a)
        descriptors_b = scan_drawing_inputs(args.b, scan_options, root=root_b)
        candidates = []
    scan_elapsed = time.perf_counter() - scan_started

    match_started = time.perf_counter()
    if not getattr(args, "reuse_match_candidates", None) and not getattr(args, "reuse_compare_state", None):
        candidates = match_drawing_sets(descriptors_a, descriptors_b)
    manual_metrics = None
    if getattr(args, "manual_matches", None) and not getattr(args, "reuse_compare_state", None):
        manual_rows = load_manual_match_csv(args.manual_matches)
        manual_metrics = apply_manual_matches(
            manual_rows,
            candidates,
            descriptors_a,
            descriptors_b,
            root_a=root_a,
            root_b=root_b,
        )
    match_elapsed = time.perf_counter() - match_started

    compare_elapsed = 0.0
    artifact_package = None
    preview_package = None
    executive_package = None
    review_dashboard_package = None
    viewer_package = None
    artifact_elapsed = 0.0
    if not args.skip_compare and not getattr(args, "reuse_compare_state", None):
        compare_started = time.perf_counter()
        comparison_config = ComparisonConfig.get_default()
        comparison_config.expand_blocks = not bool(getattr(args, "no_expand_blocks", False))
        compare_options = BatchCompareOptions(
            comparison_config=comparison_config,
            max_workers=args.max_workers,
            dxf_cache_dir=dxf_cache_dir,
            compare_state_dir=compare_state_dir,
            pdf_dpi=int(getattr(args, "preview_dpi", 80) or 80),
            block_text_detection=not bool(getattr(args, "no_block_text_detection", False)),
        )
        compare_summary = BatchCompareJob(candidates, compare_options).run()
        compare_elapsed = time.perf_counter() - compare_started

        # Plan §16 Phase C-2.2 — harvest comparator-derived metrics from each
        # per-pair ComparisonResult.metadata and forward them into the runtime
        # budget. ``peak_comparator_changes`` keeps a monotonic max across the
        # batch (worst-case in-flight memory pressure). ``time_to_first_stream_record_ms``
        # takes the *minimum* observed wall time — the fastest pair to begin
        # streaming represents the operator-visible "first byte" latency.
        # ``compare_summary.items`` is a list of BatchCompareItemResult; each
        # ``.result`` is an Optional[ComparisonResult] with a ``.metadata`` dict.
        if runtime_sampler is not None and compare_summary is not None:
            items_list = list(getattr(compare_summary, "items", []) or [])
            peak_changes_values: list[int] = []
            first_stream_ms_values: list[float] = []
            for item in items_list:
                comparison_result = getattr(item, "result", None)
                if comparison_result is None:
                    continue
                meta = getattr(comparison_result, "metadata", None) or {}
                peak_raw = meta.get("peak_changes_pre_truncate", 0)
                try:
                    peak_int = int(peak_raw or 0)
                except (TypeError, ValueError):
                    peak_int = 0
                if peak_int > 0:
                    peak_changes_values.append(peak_int)
                stream_raw = meta.get("time_to_first_stream_record_ms")
                if isinstance(stream_raw, (int, float)) and stream_raw > 0:
                    first_stream_ms_values.append(float(stream_raw))
            if peak_changes_values:
                runtime_sampler.record_comparator_peak_changes(
                    max(peak_changes_values)
                )
            if first_stream_ms_values:
                runtime_sampler.record_time_to_first_stream_record_ms(
                    min(first_stream_ms_values)
                )

    if compare_summary is not None and _should_export_change_artifacts(args):
        if getattr(args, "reuse_compare_state", None):
            _ensure_compare_state_has_streams(compare_summary)
        artifact_started = time.perf_counter()
        artifact_package = export_change_artifacts(
            compare_summary,
            _artifact_dir_for_args(args, out_dir),
            dxf_cache_dir=dxf_cache_dir,
            compare_state_dir=compare_state_dir,
            cloud_options=_cloud_options_for_args(args),
            export_cloud_marks=bool(getattr(args, "export_cloud_marks", False)),
            export_before_marks=bool(getattr(args, "export_before_cloud_marks", False)),
        )
        artifact_elapsed = time.perf_counter() - artifact_started

    if compare_summary is not None and bool(getattr(args, "export_preview", False)):
        if getattr(args, "reuse_compare_state", None):
            _ensure_compare_state_has_streams(compare_summary)
        preview_started = time.perf_counter()
        if review_state_path is not None and not review_state_path.exists():
            save_review_state(review_state_path, [])
        preview_package = export_preview_artifacts(
            compare_summary,
            _preview_dir_for_args(args, out_dir),
            dxf_cache_dir=dxf_cache_dir,
            review_state_path=review_state_path,
            dpi=int(getattr(args, "preview_dpi", 80) or 80),
            max_edge_px=int(getattr(args, "preview_max_edge_px", 2400) or 2400),
            max_preview_pairs=int(getattr(args, "max_preview_pairs", 5)),
        )
        if artifact_package is not None:
            update_artifact_manifest(
                artifact_package.output_paths.get("artifact_manifest_json"),
                preview_manifest_path=preview_package.manifest_path,
                review_state_path=review_state_path,
            )
        artifact_elapsed += time.perf_counter() - preview_started

    if bool(getattr(args, "review_dashboard", False)) and not bool(getattr(args, "executive_review", False)):
        dashboard_started = time.perf_counter()
        artifact_dir = _artifact_dir_for_args(args, out_dir)
        if artifact_package is None and not (artifact_dir / "artifact_manifest.json").exists():
            raise FileNotFoundError(
                "--review-dashboard requires existing change artifacts or a compare run that can generate them"
            )
        review_dashboard_package = export_review_dashboard(
            artifact_dir,
            preview_manifest_path=preview_package.manifest_path if preview_package else None,
            top_review_issues=int(getattr(args, "top_review_issues", 100) or 100),
            top_issues_per_drawing=int(getattr(args, "top_issues_per_drawing", 20) or 20),
            fold_repetitive_layers=bool(getattr(args, "fold_repetitive_layers", True)),
        )
        if artifact_package is not None:
            artifact_package.output_paths.update(review_dashboard_package.output_paths)
        artifact_elapsed += time.perf_counter() - dashboard_started
        if runtime_sampler is not None:
            runtime_sampler.mark_first_review_ready()

    if bool(getattr(args, "executive_review", False)):
        executive_started = time.perf_counter()
        artifact_dir = _artifact_dir_for_args(args, out_dir)
        if artifact_package is None and not (artifact_dir / "artifact_manifest.json").exists():
            raise FileNotFoundError(
                "--executive-review requires existing change artifacts or a compare run that can generate them"
            )
        executive_package = export_executive_review_from_artifacts(
            artifact_dir,
            top_drawings=int(getattr(args, "executive_top_drawings", 15) or 15),
            top_zones=int(getattr(args, "executive_top_zones", 30) or 30),
            top_review_issues=int(getattr(args, "top_review_issues", 100) or 100),
            top_issues_per_drawing=int(getattr(args, "top_issues_per_drawing", 20) or 20),
            fold_repetitive_layers=bool(getattr(args, "fold_repetitive_layers", True)),
        )
        if artifact_package is not None:
            artifact_package.output_paths.update(executive_package.output_paths)
        dashboard_path = executive_package.output_paths.get("review_dashboard_json")
        if dashboard_path and Path(dashboard_path).exists():
            try:
                review_dashboard_package = json.loads(Path(dashboard_path).read_text(encoding="utf-8"))
            except Exception:
                review_dashboard_package = None
        artifact_elapsed += time.perf_counter() - executive_started

    if bool(getattr(args, "export_viewer_package", False)) or bool(getattr(args, "export_marked_pdf", False)):
        viewer_started = time.perf_counter()
        artifact_dir = _artifact_dir_for_args(args, out_dir)
        if artifact_package is None and not (artifact_dir / "artifact_manifest.json").exists():
            raise FileNotFoundError(
                "--export-viewer-package requires existing change artifacts or a compare run that can generate them"
            )
        dashboard_path = None
        if executive_package is not None:
            dashboard_path = executive_package.output_paths.get("review_dashboard_json")
        if not dashboard_path and artifact_package is not None:
            dashboard_path = artifact_package.output_paths.get("review_dashboard_json")
        if not dashboard_path and not (artifact_dir / "review_dashboard.json").exists():
            generated_dashboard = export_review_dashboard(
                artifact_dir,
                preview_manifest_path=preview_package.manifest_path if preview_package else None,
                top_review_issues=int(getattr(args, "top_review_issues", 100) or 100),
                top_issues_per_drawing=int(getattr(args, "top_issues_per_drawing", 20) or 20),
                fold_repetitive_layers=bool(getattr(args, "fold_repetitive_layers", True)),
            )
            dashboard_path = generated_dashboard.output_paths.get("review_dashboard_json")
            review_dashboard_package = generated_dashboard
            if artifact_package is not None:
                artifact_package.output_paths.update(generated_dashboard.output_paths)
            if runtime_sampler is not None:
                runtime_sampler.mark_first_review_ready()
        viewer_package = export_viewer_package(
            artifact_dir,
            _viewer_dir_for_args(args, out_dir),
            review_dashboard_path=dashboard_path,
            preview_manifest_path=preview_package.manifest_path if preview_package else None,
            viewer_mode=str(getattr(args, "viewer_mode", "image-tiles")),
            render_policy=str(getattr(args, "viewer_render_policy", "lazy")),
            viewer_engine=str(getattr(args, "viewer_engine", "auto")),
            viewer_cache_dir=getattr(args, "viewer_cache_dir", None),
            tile_size=int(getattr(args, "tile_size", 512)),
            max_visible_overlays=int(getattr(args, "max_visible_overlays", 500)),
            viewer_memory_budget_mb=int(getattr(args, "viewer_memory_budget_mb", 512)),
            render_selected_on_open=bool(getattr(args, "render_selected_on_open", False)),
            prefetch_neighbor_tiles=bool(getattr(args, "prefetch_neighbor_tiles", True)),
            tile_prefetch_radius=int(getattr(args, "tile_prefetch_radius", 1)),
            overview_max_edge=int(getattr(args, "overview_max_edge", 2200)),
            focus_tile_max_edge=int(getattr(args, "focus_tile_max_edge", 1600)),
            viewer_perf_log=bool(getattr(args, "viewer_perf_log", False)),
            render_timeout_seconds=int(getattr(args, "viewer_render_timeout_seconds", 0)),
            max_viewer_pages=int(getattr(args, "max_viewer_pages", 30)),
            max_zone_tiles=int(getattr(args, "max_zone_tiles", 300)),
            export_marked_pdf=bool(getattr(args, "export_marked_pdf", False)),
            marked_pdf_mode=str(getattr(args, "marked_pdf_mode", "selected")),
            marked_pdf_selection_csv=getattr(args, "cloud_selection_csv", None),
            dxf_cache_dir=dxf_cache_dir,
            preview_dpi=int(getattr(args, "preview_dpi", 80) or 80),
            preview_max_edge_px=int(getattr(args, "preview_max_edge_px", 2400) or 2400),
        )
        if artifact_package is not None:
            artifact_package.output_paths.update(viewer_package.output_paths)
        artifact_elapsed += time.perf_counter() - viewer_started

    selected_zone_evidence = None
    if viewer_package is not None and bool(getattr(args, "render_selected_zone_evidence", False)):
        evidence_started = time.perf_counter()
        selected_zone_evidence = _render_selected_zone_evidence(
            viewer_package=viewer_package,
            dxf_cache_dir=dxf_cache_dir or (out_dir / "dxf_cache"),
            zones_per_pair=int(getattr(args, "selected_zone_evidence_per_pair", 1) or 1),
        )
        artifact_elapsed += time.perf_counter() - evidence_started

    current_memory, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    ground_truth_rows = _load_ground_truth(args.ground_truth) if args.ground_truth else []
    ground_truth_metrics = (
        _evaluate_ground_truth(
            ground_truth_rows,
            candidates,
            descriptors_a,
            descriptors_b,
            root_a,
            root_b,
        )
        if ground_truth_rows
        else None
    )
    review_ground_truth_rows = (
        _load_review_ground_truth(args.review_ground_truth)
        if getattr(args, "review_ground_truth", None)
        else []
    )

    output_paths = {
        "summary_json": out_dir / "validation_summary.json",
        "html_report": out_dir / "validation_report.html",
        "match_candidates_csv": out_dir / "match_candidates.csv",
        "compare_results_csv": out_dir / "compare_results.csv",
        "quality_gate_json": out_dir / "quality_gate.json",
        "ai_policy_json": out_dir / "ai_policy.json",
        "review_queue_csv": out_dir / "review_queue.csv",
        "unmatched_csv": out_dir / "unmatched.csv",
        "blocked_pairs_csv": out_dir / "blocked_pairs.csv",
        "manual_matches_template_csv": out_dir / "manual_matches_template.csv",
        "ground_truth_template_csv": out_dir / "ground_truth_template.csv",
        "preflight_report_json": preflight_path,
        "run_manifest_json": run_manifest.path,
        "success_sentinel_json": run_manifest.success_path,
        "failed_sentinel_json": run_manifest.failed_path,
    }
    if dxf_cache_dir is not None:
        output_paths["dxf_cache_dir"] = dxf_cache_dir
    if compare_state_dir is not None:
        output_paths["compare_state_dir"] = compare_state_dir
    if review_state_path is not None:
        output_paths["review_state_json"] = review_state_path
    if artifact_package is not None:
        output_paths.update(
            {name: Path(path) for name, path in artifact_package.output_paths.items()}
        )
    if preview_package is not None:
        output_paths["preview_manifest_json"] = Path(preview_package.manifest_path)
        output_paths["preview_dir"] = Path(preview_package.output_dir)
    if executive_package is not None:
        output_paths.update(
            {name: Path(path) for name, path in executive_package.output_paths.items()}
        )
    if review_dashboard_package is not None and not isinstance(review_dashboard_package, dict):
        output_paths.update(
            {name: Path(path) for name, path in review_dashboard_package.output_paths.items()}
        )
    if viewer_package is not None:
        output_paths.update({name: Path(path) for name, path in viewer_package.output_paths.items()})
    if isinstance(selected_zone_evidence, dict) and selected_zone_evidence.get("output_path"):
        output_paths["selected_zone_evidence_json"] = Path(str(selected_zone_evidence["output_path"]))
    review_project_path = None
    if artifact_package is not None or preview_package is not None:
        review_project_path = out_dir / "review_project.json"
        write_review_project(
            review_project_path,
            source_a=args.a,
            source_b=args.b,
            dxf_cache_dir=dxf_cache_dir,
            compare_state_dir=compare_state_dir,
            artifact_dir=_artifact_dir_for_args(args, out_dir),
            review_state_path=review_state_path,
            preview_manifest_path=preview_package.manifest_path if preview_package else None,
            options={
                "export_preview": bool(getattr(args, "export_preview", False)),
                "max_preview_pairs": int(getattr(args, "max_preview_pairs", 5)),
                "export_viewer_package": bool(getattr(args, "export_viewer_package", False)),
                "viewer_mode": str(getattr(args, "viewer_mode", "image-tiles")),
                "viewer_render_policy": str(getattr(args, "viewer_render_policy", "lazy")),
                "export_marked_pdf": bool(getattr(args, "export_marked_pdf", False)),
                "marked_pdf_mode": str(getattr(args, "marked_pdf_mode", "selected")),
                "export_cloud_marks": bool(getattr(args, "export_cloud_marks", False)),
                "cloud_export_mode": getattr(args, "cloud_export_mode", "selected"),
            },
            export_profile=str(getattr(args, "export_profile", "internal")),
        )
        output_paths["review_project_json"] = review_project_path
        if artifact_package is not None:
            update_artifact_manifest(
                artifact_package.output_paths.get("artifact_manifest_json"),
                preview_manifest_path=preview_package.manifest_path if preview_package else None,
                review_state_path=review_state_path,
                review_project_path=review_project_path,
                export_profile=str(getattr(args, "export_profile", "internal")),
            )
    baseline_path = _baseline_path_for_args(args, out_dir)
    if baseline_path is not None:
        output_paths["baseline_json"] = baseline_path

    runtime_budget_payload: dict[str, Any] | None = None
    if runtime_sampler is not None:
        runtime_budget_payload = runtime_sampler.stop().to_dict()
    payload = _build_summary_payload(
        args=args,
        output_dir=out_dir,
        descriptors_a=descriptors_a,
        descriptors_b=descriptors_b,
        candidates=candidates,
        compare_summary=compare_summary,
        timings={
            "scan_s": scan_elapsed,
            "match_s": match_elapsed,
            "compare_s": compare_elapsed,
            "artifact_s": artifact_elapsed,
            "total_s": time.perf_counter() - total_started,
        },
        memory={
            "current_mb": round(current_memory / (1024 * 1024), 3),
            "peak_mb": round(peak_memory / (1024 * 1024), 3),
        },
        ground_truth=ground_truth_metrics,
        manual_metrics=manual_metrics,
        outputs=output_paths,
        artifact_package=artifact_package,
        preview_package=preview_package,
        executive_package=executive_package,
        review_dashboard_package=review_dashboard_package,
        viewer_package=viewer_package,
        selected_zone_evidence=selected_zone_evidence,
        ai_policy=_build_ai_policy_evidence(out_dir),
        runtime_budget=runtime_budget_payload,
    )
    review_ground_truth_metrics = (
        _evaluate_review_ground_truth(
            review_ground_truth_rows,
            payload.get("review_dashboard") if isinstance(payload, dict) else None,
        )
        if review_ground_truth_rows
        else None
    )
    if review_ground_truth_metrics is not None:
        payload["review_ground_truth"] = review_ground_truth_metrics
        quality = payload.setdefault("quality", {})
        quality["structural_review_recall"] = review_ground_truth_metrics.get("recall")
    baseline_record = _build_baseline_record(payload, args)
    previous_baseline = _previous_baseline_for_args(args, baseline_path)
    quality_gate = _evaluate_quality_gate(payload, previous_baseline, args)
    payload["baseline_record"] = baseline_record
    payload["quality_gate"] = quality_gate
    payload["preflight_result"] = preflight_result.to_dict()
    payload["run_manifest"] = str(run_manifest.path)

    _write_json(output_paths["summary_json"], payload)
    _write_json(output_paths["ai_policy_json"], payload["ai_policy"])
    _write_match_csv(output_paths["match_candidates_csv"], candidates)
    _write_compare_csv(output_paths["compare_results_csv"], compare_summary)
    _write_quality_gate(output_paths["quality_gate_json"], quality_gate)
    _write_review_queue_csv(output_paths["review_queue_csv"], candidates, root_a, root_b)
    _write_unmatched_csv(output_paths["unmatched_csv"], candidates, root_a, root_b)
    _write_blocked_pairs_csv(output_paths["blocked_pairs_csv"], descriptors_a, descriptors_b, root_a, root_b)
    _write_manual_matches_template(output_paths["manual_matches_template_csv"], candidates, root_a, root_b)
    _write_ground_truth_template(
        output_paths["ground_truth_template_csv"],
        candidates,
        root_a=root_a,
        root_b=root_b,
    )
    if getattr(args, "update_baseline", False) and baseline_path is not None and not getattr(args, "_defer_baseline_write", False):
        _write_json(baseline_path, baseline_record)
    _write_html_report(output_paths["html_report"], payload, candidates, compare_summary)
    run_manifest.complete(
        counts={
            "descriptors_a": len(descriptors_a),
            "descriptors_b": len(descriptors_b),
            "confirmed_pairs": payload.get("matching", {}).get("confirmed_pairs", 0),
            "completed_pairs": (compare_summary.completed_pairs if compare_summary else 0),
            "failed_pairs": (compare_summary.failed_pairs if compare_summary else 0),
            "raw_change_count": payload.get("comparison", {}).get("total_changes", 0),
        },
        outputs={name: str(path) for name, path in output_paths.items()},
        warnings=[check.message for check in preflight_result.warnings],
    )
    if normalize_export_profile(getattr(args, "export_profile", "internal")) == "sharable":
        # Redact every supported artifact under the output directory, not just
        # the named output_paths entries. The viewer subtree and operational
        # reports can carry absolute image/source paths across JSON, CSV, XLSX,
        # HTML, and text outputs.
        removed_raw_streams = _remove_sharable_raw_streams(out_dir)
        payload["sharable_raw_streams"] = {
            "removed_count": len(removed_raw_streams),
            "removed": removed_raw_streams[:50],
        }
        for path in sorted(item for item in out_dir.rglob("*") if item.is_file()):
            apply_export_profile_to_file(path, profile="sharable", package_root=out_dir)
        leaks = audit_sharable_paths(out_dir)
        sharable_audit = {
            "leak_count": len(leaks),
            "leaks": leaks[:50],
            "audited_at": datetime.now().isoformat(),
        }
        payload["sharable_audit"] = sharable_audit
        if leaks:
            quality_gate = payload.get("quality_gate") if isinstance(payload.get("quality_gate"), dict) else {}
            failures = list(quality_gate.get("failures") or [])
            failures.append(
                {
                    "code": "sharable_path_leak",
                    "leak_count": len(leaks),
                    "first_leak": leaks[0],
                }
            )
            quality_gate["failures"] = failures
            quality_gate["passed"] = False
            quality_gate["status"] = "failed"
            issues = list(quality_gate.get("issues") or [])
            issues.append(
                {
                    "metric": "sharable_path_leak",
                    "actual": len(leaks),
                    "threshold": 0,
                    "message": "Sharable package contains absolute path leaks.",
                }
            )
            quality_gate["issues"] = issues
            payload["quality_gate"] = quality_gate
            _write_quality_gate(
                output_paths["quality_gate_json"],
                redact_payload_paths(quality_gate, profile="sharable", package_root=out_dir),
            )
        # Re-write the summary so sharable_audit is durably persisted to disk.
        # Without this, the in-memory payload has the audit but validation_summary.json
        # on disk is stale (it was written before redaction ran).
        _write_json(
            output_paths["summary_json"],
            redact_payload_paths(payload, profile="sharable", package_root=out_dir),
        )
    return payload


def _remove_sharable_raw_streams(out_dir: Path) -> list[str]:
    """Remove raw line-delimited stream artifacts from a sharable package."""

    removed: list[str] = []
    root = out_dir.resolve()
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".jsonl", ".ndjson"}:
            continue
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(root)
        except Exception:
            continue
        path.unlink()
        removed.append(relative.as_posix())
    return removed


def run_manifest_validation(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.manifest.resolve()
    manifest = _load_manifest(manifest_path)
    datasets = _manifest_datasets(manifest)
    if not datasets:
        raise ValueError("Manifest does not contain any drawing validation datasets")

    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    previous_baselines = _load_manifest_baselines(args.baseline)

    dataset_payloads: list[dict[str, Any]] = []
    baseline_records: dict[str, Any] = {}
    for index, dataset in enumerate(datasets, start=1):
        name = str(dataset.get("name") or f"set_{index}")
        dataset_id = _safe_dataset_id(name)
        child_args = _manifest_child_args(args, dataset, manifest_path.parent, out_dir / dataset_id, name)
        child_args._baseline_payload = previous_baselines.get(dataset_id) or previous_baselines.get(name)
        child_args._defer_baseline_write = True
        payload = run_validation(child_args)
        dataset_payloads.append(payload)
        baseline_records[dataset_id] = payload["baseline_record"]

    aggregate_gate = _aggregate_quality_gate(dataset_payloads, args)
    output_paths = {
        "summary_json": out_dir / "validation_summary.json",
        "index_html": out_dir / "validation_index.html",
        "quality_gate_json": out_dir / "quality_gate.json",
    }
    baseline_path = _baseline_path_for_args(args, out_dir)
    if baseline_path is not None:
        output_paths["baseline_json"] = baseline_path

    payload = {
        "generated_at": datetime.now().isoformat(),
        "manifest": str(manifest_path),
        "output_dir": str(out_dir),
        "outputs": {name: str(path) for name, path in output_paths.items()},
        "dataset_count": len(dataset_payloads),
        "datasets": [_manifest_dataset_summary(item) for item in dataset_payloads],
        "quality_gate": aggregate_gate,
        "baseline_record": {
            "schema_version": QUALITY_SCHEMA_VERSION,
            "created_at": datetime.now().isoformat(),
            "manifest": str(manifest_path),
            "datasets": baseline_records,
            "thresholds": _thresholds(args),
        },
    }
    _write_json(output_paths["summary_json"], payload)
    _write_quality_gate(output_paths["quality_gate_json"], aggregate_gate)
    _write_manifest_index(output_paths["index_html"], payload)
    if getattr(args, "update_baseline", False) and baseline_path is not None:
        _write_json(baseline_path, payload["baseline_record"])
    return payload


def _load_manifest(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required for YAML manifests; use JSON instead") from exc
    return yaml.safe_load(text)


def _manifest_datasets(manifest: Any) -> list[dict[str, Any]]:
    if isinstance(manifest, list):
        return [dict(item) for item in manifest]
    if isinstance(manifest, dict):
        values = manifest.get("datasets") or manifest.get("sets") or manifest.get("items")
        if isinstance(values, list):
            return [dict(item) for item in values]
    return []


def _manifest_child_args(
    parent: argparse.Namespace,
    dataset: dict[str, Any],
    manifest_dir: Path,
    out_dir: Path,
    name: str,
) -> argparse.Namespace:
    def resolve_optional_path(value: Any) -> Path | None:
        if not value:
            return None
        path = Path(str(value))
        return path if path.is_absolute() else manifest_dir / path

    a_path = resolve_optional_path(dataset.get("a"))
    b_path = resolve_optional_path(dataset.get("b"))
    if not a_path or not b_path:
        raise ValueError(f"Manifest dataset '{name}' requires 'a' and 'b'")

    return argparse.Namespace(
        a=a_path,
        b=b_path,
        manifest=None,
        recursive=bool(dataset.get("recursive", getattr(parent, "recursive", False))),
        out=out_dir,
        ground_truth=resolve_optional_path(dataset.get("ground_truth")),
        review_ground_truth=resolve_optional_path(dataset.get("review_ground_truth"))
        or getattr(parent, "review_ground_truth", None),
        manual_matches=resolve_optional_path(dataset.get("manual_matches")),
        reuse_match_candidates=resolve_optional_path(dataset.get("reuse_match_candidates")),
        dxf_cache_dir=resolve_optional_path(dataset.get("dxf_cache_dir"))
        or getattr(parent, "dxf_cache_dir", None),
        compare_state_dir=resolve_optional_path(dataset.get("compare_state_dir"))
        or getattr(parent, "compare_state_dir", None),
        reuse_compare_state=resolve_optional_path(dataset.get("reuse_compare_state")),
        export_cloud_marks=bool(
            dataset.get("export_cloud_marks", getattr(parent, "export_cloud_marks", False))
        ),
        export_before_cloud_marks=bool(
            dataset.get(
                "export_before_cloud_marks",
                getattr(parent, "export_before_cloud_marks", False),
            )
        ),
        cloud_export_mode=str(
            dataset.get("cloud_export_mode", getattr(parent, "cloud_export_mode", "selected"))
        ),
        cloud_selection_csv=resolve_optional_path(dataset.get("cloud_selection_csv"))
        or getattr(parent, "cloud_selection_csv", None),
        cloud_region_distance=float(
            dataset.get("cloud_region_distance", getattr(parent, "cloud_region_distance", 1000.0))
        ),
        max_cloud_regions_per_pair=int(
            dataset.get(
                "max_cloud_regions_per_pair",
                getattr(parent, "max_cloud_regions_per_pair", 150),
            )
        ),
        max_cloud_regions_total=int(
            dataset.get(
                "max_cloud_regions_total",
                getattr(parent, "max_cloud_regions_total", 3000),
            )
        ),
        change_zone_report=bool(
            dataset.get("change_zone_report", getattr(parent, "change_zone_report", False))
        ),
        executive_review=bool(
            dataset.get("executive_review", getattr(parent, "executive_review", False))
        ),
        executive_top_drawings=int(
            dataset.get("executive_top_drawings", getattr(parent, "executive_top_drawings", 15))
        ),
        executive_top_zones=int(
            dataset.get("executive_top_zones", getattr(parent, "executive_top_zones", 30))
        ),
        review_dashboard=bool(
            dataset.get("review_dashboard", getattr(parent, "review_dashboard", False))
        ),
        top_review_issues=int(
            dataset.get("top_review_issues", getattr(parent, "top_review_issues", 100))
        ),
        top_issues_per_drawing=int(
            dataset.get(
                "top_issues_per_drawing",
                getattr(parent, "top_issues_per_drawing", 20),
            )
        ),
        fold_repetitive_layers=bool(
            dataset.get(
                "fold_repetitive_layers",
                getattr(parent, "fold_repetitive_layers", True),
            )
        ),
        artifact_dir=resolve_optional_path(dataset.get("artifact_dir")),
        review_state=resolve_optional_path(dataset.get("review_state"))
        or getattr(parent, "review_state", None),
        export_preview=bool(dataset.get("export_preview", getattr(parent, "export_preview", False))),
        preview_dpi=int(dataset.get("preview_dpi", getattr(parent, "preview_dpi", 80))),
        preview_max_edge_px=int(
            dataset.get("preview_max_edge_px", getattr(parent, "preview_max_edge_px", 2400))
        ),
        max_preview_pairs=int(
            dataset.get("max_preview_pairs", getattr(parent, "max_preview_pairs", 5))
        ),
        export_viewer_package=bool(
            dataset.get("export_viewer_package", getattr(parent, "export_viewer_package", False))
        ),
        viewer_mode=str(dataset.get("viewer_mode", getattr(parent, "viewer_mode", "image-tiles"))),
        viewer_render_policy=str(
            dataset.get("viewer_render_policy", getattr(parent, "viewer_render_policy", "lazy"))
        ),
        viewer_engine=str(dataset.get("viewer_engine", getattr(parent, "viewer_engine", "auto"))),
        viewer_cache_dir=resolve_optional_path(dataset.get("viewer_cache_dir"))
        or getattr(parent, "viewer_cache_dir", None),
        tile_size=int(dataset.get("tile_size", getattr(parent, "tile_size", 512))),
        max_visible_overlays=int(
            dataset.get("max_visible_overlays", getattr(parent, "max_visible_overlays", 500))
        ),
        viewer_memory_budget_mb=int(
            dataset.get("viewer_memory_budget_mb", getattr(parent, "viewer_memory_budget_mb", 512))
        ),
        render_selected_on_open=bool(
            dataset.get("render_selected_on_open", getattr(parent, "render_selected_on_open", False))
        ),
        prefetch_neighbor_tiles=bool(
            dataset.get("prefetch_neighbor_tiles", getattr(parent, "prefetch_neighbor_tiles", True))
        ),
        tile_prefetch_radius=int(
            dataset.get("tile_prefetch_radius", getattr(parent, "tile_prefetch_radius", 1))
        ),
        overview_max_edge=int(dataset.get("overview_max_edge", getattr(parent, "overview_max_edge", 2200))),
        focus_tile_max_edge=int(
            dataset.get("focus_tile_max_edge", getattr(parent, "focus_tile_max_edge", 1600))
        ),
        viewer_perf_log=bool(dataset.get("viewer_perf_log", getattr(parent, "viewer_perf_log", False))),
        render_selected_zone_evidence=bool(
            dataset.get(
                "render_selected_zone_evidence",
                getattr(parent, "render_selected_zone_evidence", False),
            )
        ),
        selected_zone_evidence_per_pair=int(
            dataset.get(
                "selected_zone_evidence_per_pair",
                getattr(parent, "selected_zone_evidence_per_pair", 1),
            )
        ),
        max_viewer_pages=int(dataset.get("max_viewer_pages", getattr(parent, "max_viewer_pages", 30))),
        max_zone_tiles=int(dataset.get("max_zone_tiles", getattr(parent, "max_zone_tiles", 300))),
        export_marked_pdf=bool(
            dataset.get("export_marked_pdf", getattr(parent, "export_marked_pdf", False))
        ),
        marked_pdf_mode=str(
            dataset.get("marked_pdf_mode", getattr(parent, "marked_pdf_mode", "selected"))
        ),
        write_ground_truth_template=bool(
            dataset.get(
                "write_ground_truth_template",
                getattr(parent, "write_ground_truth_template", False),
            )
        ),
        skip_compare=bool(dataset.get("skip_compare", getattr(parent, "skip_compare", False))),
        max_workers=dataset.get("max_workers", getattr(parent, "max_workers", None)),
        no_cache=bool(dataset.get("no_cache", getattr(parent, "no_cache", False))),
        baseline=None,
        update_baseline=False,
        quality_gate=bool(getattr(parent, "quality_gate", False)),
        min_auto_precision=float(getattr(parent, "min_auto_precision", DEFAULT_MIN_AUTO_PRECISION)),
        min_recall=float(getattr(parent, "min_recall", DEFAULT_MIN_RECALL)),
        max_match_time_regression=float(
            getattr(parent, "max_match_time_regression", DEFAULT_MAX_MATCH_TIME_REGRESSION)
        ),
        export_profile=str(dataset.get("export_profile", getattr(parent, "export_profile", "internal"))),
        preflight_only=False,
        allow_long_path_warning=bool(
            dataset.get("allow_long_path_warning", getattr(parent, "allow_long_path_warning", False))
        ),
        dataset_name=name,
    )


def _load_manifest_baselines(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    datasets = data.get("datasets") if isinstance(data, dict) else None
    return dict(datasets) if isinstance(datasets, dict) else {}


def _manifest_dataset_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_id": payload["baseline_record"]["dataset_id"],
        "output_dir": payload["output_dir"],
        "summary_json": payload["outputs"]["summary_json"],
        "html_report": payload["outputs"]["html_report"],
        "quality_gate": payload["quality_gate"]["status"],
        "matching": payload["matching"],
        "comparison": payload["comparison"],
        "timings": payload["timings"],
    }


def _aggregate_quality_gate(
    dataset_payloads: Sequence[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for payload in dataset_payloads:
        gate = payload.get("quality_gate") or {}
        for issue in gate.get("issues", []):
            issue = dict(issue)
            issue["dataset_id"] = payload["baseline_record"]["dataset_id"]
            issues.append(issue)
    requested = bool(getattr(args, "quality_gate", False))
    status = "not_requested"
    if requested:
        status = "failed" if issues else "passed"
    return {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "status": status,
        "requested": requested,
        "dataset_count": len(dataset_payloads),
        "failed_dataset_count": len(
            [payload for payload in dataset_payloads if payload.get("quality_gate", {}).get("status") == "failed"]
        ),
        "issues": issues,
        "thresholds": _thresholds(args),
    }


def _safe_dataset_id(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return safe.strip("._") or "dataset"


def _payload_quality_failed(payload: dict[str, Any]) -> bool:
    return (payload.get("quality_gate") or {}).get("status") == "failed"


def _scan_root(path: Path) -> Path | None:
    path = Path(path)
    if path.is_dir():
        return path.resolve()
    if path.is_file():
        return path.resolve().parent
    return None


def _dxf_cache_dir_for_args(args: argparse.Namespace, out_dir: Path) -> Path | None:
    cache_dir = getattr(args, "dxf_cache_dir", None)
    if cache_dir:
        return Path(cache_dir).resolve()
    if not bool(getattr(args, "skip_compare", False)) or bool(
        getattr(args, "export_cloud_marks", False)
        or getattr(args, "export_preview", False)
        or getattr(args, "export_viewer_package", False)
        or getattr(args, "export_marked_pdf", False)
    ):
        return (out_dir / "dxf_cache").resolve()
    return None


def _compare_state_dir_for_args(args: argparse.Namespace, out_dir: Path) -> Path | None:
    if getattr(args, "reuse_compare_state", None):
        return Path(args.reuse_compare_state).resolve()
    state_dir = getattr(args, "compare_state_dir", None)
    if state_dir:
        return Path(state_dir).resolve()
    if not bool(getattr(args, "skip_compare", False)):
        return (out_dir / "compare_state").resolve()
    return None


def _should_export_change_artifacts(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "export_cloud_marks", False)
        or getattr(args, "change_zone_report", False)
        or getattr(args, "executive_review", False)
        or getattr(args, "review_dashboard", False)
        or getattr(args, "export_viewer_package", False)
        or getattr(args, "export_marked_pdf", False)
        or getattr(args, "artifact_dir", None)
    )


def _artifact_dir_for_args(args: argparse.Namespace, out_dir: Path) -> Path:
    artifact_dir = getattr(args, "artifact_dir", None)
    if artifact_dir:
        return Path(artifact_dir).resolve()
    return (out_dir / "change_artifacts").resolve()


def _review_state_path_for_args(args: argparse.Namespace, out_dir: Path) -> Path | None:
    review_state = getattr(args, "review_state", None)
    if review_state:
        return Path(review_state).resolve()
    if bool(getattr(args, "export_preview", False)) or _should_export_change_artifacts(args):
        return (out_dir / "review_state.json").resolve()
    return None


def _preview_dir_for_args(args: argparse.Namespace, out_dir: Path) -> Path:
    artifact_dir = getattr(args, "artifact_dir", None)
    if artifact_dir:
        return (Path(artifact_dir).resolve() / "preview").resolve()
    return (out_dir / "preview").resolve()


def _viewer_dir_for_args(args: argparse.Namespace, out_dir: Path) -> Path:
    artifact_dir = getattr(args, "artifact_dir", None)
    if artifact_dir:
        return (Path(artifact_dir).resolve() / "viewer").resolve()
    return (out_dir / "viewer").resolve()


def _cloud_options_for_args(args: argparse.Namespace) -> CloudMarkOptions:
    selected_keys: tuple[str, ...] = tuple()
    if getattr(args, "cloud_selection_csv", None):
        selected_keys = tuple(_load_cloud_selection_keys(args.cloud_selection_csv))
    return CloudMarkOptions(
        export_mode=str(getattr(args, "cloud_export_mode", "selected") or "selected"),
        region_distance=float(getattr(args, "cloud_region_distance", 1000.0) or 1000.0),
        max_regions_per_pair=int(getattr(args, "max_cloud_regions_per_pair", 150) or 150),
        max_regions_total=int(getattr(args, "max_cloud_regions_total", 3000) or 3000),
        selected_zone_keys=selected_keys,
    )


def _load_cloud_selection_keys(path: Path) -> list[str]:
    keys: list[str] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        if "zone_id" not in fieldnames:
            raise ValueError("cloud selection CSV requires a zone_id column")
        for row in reader:
            zone_id = (row.get("zone_id") or "").strip()
            if not zone_id:
                continue
            pair_id = (row.get("pair_id") or "").strip()
            drawing_number = (row.get("drawing_number") or "").strip()
            keys.append(zone_id)
            if pair_id:
                keys.append(f"{pair_id}:{zone_id}")
            if drawing_number:
                keys.append(f"{drawing_number}:{zone_id}")
    return keys


def _candidates_from_compare_summary(summary: BatchCompareSummary) -> list[MatchCandidate]:
    candidates = [item.candidate for item in summary.items if item.candidate]
    for descriptor in summary.unmatched_a:
        candidates.append(
            MatchCandidate(
                source_a=descriptor,
                source_b=None,
                score=0.0,
                status=MatchStatus.UNMATCHED_A,
            )
        )
    for descriptor in summary.unmatched_b:
        candidates.append(
            MatchCandidate(
                source_a=None,
                source_b=descriptor,
                score=0.0,
                status=MatchStatus.UNMATCHED_B,
            )
        )
    return candidates


def _descriptors_from_candidates(
    candidates: Sequence[MatchCandidate],
    summary: BatchCompareSummary,
) -> tuple[list[DrawingFileDescriptor], list[DrawingFileDescriptor]]:
    descriptors_a: dict[str, DrawingFileDescriptor] = {
        descriptor.path: descriptor for descriptor in summary.unmatched_a
    }
    descriptors_b: dict[str, DrawingFileDescriptor] = {
        descriptor.path: descriptor for descriptor in summary.unmatched_b
    }
    for candidate in candidates:
        if candidate.source_a:
            descriptors_a[candidate.source_a.path] = candidate.source_a
        if candidate.source_b:
            descriptors_b[candidate.source_b.path] = candidate.source_b
    return list(descriptors_a.values()), list(descriptors_b.values())


def _ensure_compare_state_has_streams(summary: BatchCompareSummary) -> None:
    missing: list[str] = []
    for item in summary.items:
        if item.status != "completed" or not item.result:
            continue
        metadata = item.result.metadata or {}
        stream_path = metadata.get("change_zone_stream_path")
        if not stream_path:
            missing.append(item.candidate.source_b.path if item.candidate.source_b else item.status)
            continue
        if not Path(stream_path).exists():
            missing.append(str(stream_path))
    if missing:
        raise RuntimeError(
            "Compare state cannot regenerate change zones because change-zone "
            f"streams are missing: {missing[:5]}"
        )


def _load_match_candidates_csv(
    path: Path,
    *,
    root_a: Path | None,
    root_b: Path | None,
) -> tuple[list[MatchCandidate], list[DrawingFileDescriptor], list[DrawingFileDescriptor]]:
    descriptors_a: dict[str, DrawingFileDescriptor] = {}
    descriptors_b: dict[str, DrawingFileDescriptor] = {}
    candidates: list[MatchCandidate] = []

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = {"status", "score", "a_path", "b_path"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"match_candidates CSV missing columns: {sorted(missing)}")
        for row in reader:
            source_a = _descriptor_from_match_row(row, "a", root_a, descriptors_a)
            source_b = _descriptor_from_match_row(row, "b", root_b, descriptors_b)
            status = MatchStatus(row.get("status") or MatchStatus.REVIEW_REQUIRED.value)
            candidate = MatchCandidate(
                source_a=source_a,
                source_b=source_b,
                score=_float_or_zero(row.get("score")),
                status=status,
                reasons=_split_reasons(row.get("reasons")),
                component_scores=_json_dict(row.get("component_scores_json")),
            )
            candidate.alternates = _alternates_from_match_row(row, root_b, descriptors_b)
            candidates.append(candidate)

    return candidates, list(descriptors_a.values()), list(descriptors_b.values())


def _descriptor_from_match_row(
    row: dict[str, str],
    side: str,
    root: Path | None,
    cache: dict[str, DrawingFileDescriptor],
) -> DrawingFileDescriptor | None:
    raw_path = (row.get(f"{side}_path") or "").strip()
    if not raw_path:
        return None
    path = _resolve_candidate_path(raw_path, root)
    key = str(path).lower()
    if key in cache:
        return cache[key]

    extension = (row.get(f"{side}_extension") or path.suffix).lower()
    kind_value = row.get(f"{side}_kind") or ("pdf" if extension == ".pdf" else "cad")
    identity = parse_filename_identity(path)
    drawing_number = row.get(f"{side}_drawing_number") or identity.drawing_number
    sheet = row.get(f"{side}_sheet") or identity.sheet
    identity = FilenameIdentity(
        original_stem=identity.original_stem,
        match_key=identity.match_key,
        tokens=identity.tokens,
        revision=identity.revision,
        drawing_number=drawing_number,
        sheet=sheet,
    )
    descriptor = DrawingFileDescriptor(
        path=str(path),
        kind=DrawingKind(kind_value),
        extension=extension,
        relative_path=_relative_candidate_path(path, root),
        identity=identity,
    )
    cache[key] = descriptor
    return descriptor


def _alternates_from_match_row(
    row: dict[str, str],
    root_b: Path | None,
    descriptors_b: dict[str, DrawingFileDescriptor],
) -> list[MatchAlternative]:
    payload = row.get("alternates_json")
    if not payload:
        return []
    try:
        raw_alternates = json.loads(payload)
    except Exception:
        return []
    alternatives: list[MatchAlternative] = []
    for raw in (raw_alternates if isinstance(raw_alternates, list) else []):
        b_path = raw.get("b_path") if isinstance(raw, dict) else None
        if not b_path:
            continue
        descriptor = _descriptor_from_match_row(
            {
                "b_path": b_path,
                "b_kind": "cad",
                "b_extension": Path(b_path).suffix.lower() or ".dwg",
                "b_drawing_number": raw.get("b_drawing_number", ""),
                "b_sheet": "",
            },
            "b",
            root_b,
            descriptors_b,
        )
        if descriptor is None:
            continue
        alternatives.append(
            MatchAlternative(
                source_b=descriptor,
                score=_float_or_zero(raw.get("score")),
                reasons=list(raw.get("reasons") or []),
                component_scores=dict(raw.get("component_scores") or {}),
            )
        )
    return alternatives


def _resolve_candidate_path(value: str, root: Path | None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    if root is not None:
        return (root / path).resolve()
    return path.resolve()


def _relative_candidate_path(path: Path, root: Path | None) -> str:
    if root is None:
        return path.name
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _split_reasons(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


_ABSOLUTE_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[A-Za-z]:[\\/]"),  # Windows drive letter
    re.compile(r"^/[A-Za-z]"),  # POSIX absolute path (excluding /redacted/)
    re.compile(r"^\\\\"),  # UNC \\server\share
)
_REDACTED_PREFIX = "<redacted>/"


def _looks_like_absolute_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text.startswith(_REDACTED_PREFIX):
        return False
    for pattern in _ABSOLUTE_PATH_PATTERNS:
        if pattern.match(text):
            # POSIX absolute starting with /redacted is acceptable
            if text.startswith("/redacted/"):
                return False
            return True
    return False


def _walk_for_path_leaks(
    payload: Any,
    *,
    file_path: str,
    key_path: str,
    leaks: list[dict[str, str]],
    parent_key: str = "",
) -> None:
    """Walk a JSON-like payload and append leak descriptors for absolute paths.

    A "leak" is an absolute path (Windows drive, POSIX root, or UNC) appearing in
    any string value. Sensitive keys (source_a, dxf_cache_dir, etc.) are checked
    even more strictly: any non-redacted, non-empty value is treated as a leak so
    we never ship raw filesystem paths to customers via the sharable profile.

    ``parent_key`` carries the dict key that contained the current value so leak
    detection still applies to strings inside lists (e.g., ``output_paths`` arrays).
    """

    if isinstance(payload, dict):
        for key, value in payload.items():
            child_key = f"{key_path}.{key}" if key_path else str(key)
            _walk_for_path_leaks(
                value,
                file_path=file_path,
                key_path=child_key,
                leaks=leaks,
                parent_key=str(key),
            )
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            child_key = f"{key_path}[{index}]"
            _walk_for_path_leaks(
                item,
                file_path=file_path,
                key_path=child_key,
                leaks=leaks,
                parent_key=parent_key,
            )
    elif isinstance(payload, str):
        if not payload or payload.startswith(_REDACTED_PREFIX):
            return
        if not _looks_like_absolute_path(payload):
            return
        # An absolute path inside a known-sensitive key is the strongest signal
        # of a leak; otherwise it's just a stray absolute path that still leaks
        # but with a less targeted reason. Both cases get reported.
        reason = (
            "sensitive_key_not_redacted"
            if parent_key in SENSITIVE_PATH_KEYS
            else "absolute_path_in_string_value"
        )
        leaks.append(
            {
                "file": file_path,
                "key": key_path,
                "value": payload,
                "reason": reason,
            }
        )


def audit_sharable_paths(out_dir: Path) -> list[dict[str, str]]:
    """Scan sharable artifacts under ``out_dir`` for absolute-path leaks.

    Returns a flat list of leak descriptors. Empty list means the package is
    safe to share. Used by ``run_validation`` when ``--export-profile sharable``
    is selected; failures are also rolled into ``quality_gate.json`` so CI can
    block on path leaks.
    """

    return audit_package_sharable_paths(out_dir)


def _render_selected_zone_evidence(
    *,
    viewer_package: Any,
    dxf_cache_dir: Path,
    zones_per_pair: int = 1,
) -> dict[str, Any]:
    """Render top selected zones now so validation has cold/cache-hit evidence."""

    viewer_root = Path(getattr(viewer_package, "viewer_dir", "") or "")
    manifest_path = Path(getattr(viewer_package, "manifest_path", "") or "")
    output_path = viewer_root / "selected_zone_evidence.json"
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "status": "missing",
        "requested": True,
        "zones_per_pair": max(1, int(zones_per_pair or 1)),
        "render_count": 0,
        "event_count": 0,
        "failure_count": 0,
        "output_path": str(output_path),
        "renders": [],
        "failures": [],
    }
    if not viewer_root or not manifest_path.exists():
        evidence["status"] = "failed"
        evidence["failures"].append({"reason": "viewer_manifest_missing", "path": str(manifest_path)})
        _write_json(output_path, evidence)
        return evidence

    package_root = viewer_root.parent
    manifest = _load_json_dict(manifest_path)
    pair_entries = [item for item in manifest.get("pairs", []) if isinstance(item, dict)]
    dxf_cache_dir = Path(dxf_cache_dir)
    dxf_cache_dir.mkdir(parents=True, exist_ok=True)

    for pair in pair_entries:
        pair_id = str(pair.get("pair_uuid") or pair.get("pair_id") or "")
        overlay_path = _resolve_artifact_path(package_root, viewer_root, pair.get("overlay_json"))
        overlay_payload = _load_json_dict(overlay_path) if overlay_path and overlay_path.exists() else {}
        overlays = [
            item
            for item in overlay_payload.get("overlays", [])
            if isinstance(item, dict) and str(item.get("zone_id") or "")
        ]
        for overlay in _selected_overlays_for_evidence(overlays)[: evidence["zones_per_pair"]]:
            zone_id = str(overlay.get("zone_id") or "")
            bbox = union_bboxes(overlay.get("old_bbox"), overlay.get("bbox"))
            if not bbox:
                evidence["failures"].append(
                    {"pair_uuid": pair_id, "zone_id": zone_id, "reason": "bbox_missing"}
                )
                continue
            try:
                window = canonical_window_from_bbox(bbox, padding_ratio=0.18, min_size=250.0)
                before_image = _resolve_artifact_path(package_root, viewer_root, pair.get("before_image"))
                after_image = _resolve_artifact_path(package_root, viewer_root, pair.get("after_image"))
                job = RenderJob(
                    pair_uuid=pair_id,
                    zone_id=zone_id,
                    request_id="validation_selected_zone_evidence",
                    source_before=_resolve_source_path(pair.get("source_a")),
                    source_after=_resolve_source_path(pair.get("source_b")),
                    world_window=window,
                    cache_root=viewer_root,
                    dxf_cache_dir=dxf_cache_dir,
                    before_background_image=str(before_image or ""),
                    after_background_image=str(after_image or ""),
                    before_background_transform=(
                        pair.get("before_transform") if isinstance(pair.get("before_transform"), dict) else None
                    ),
                    after_background_transform=(
                        pair.get("after_transform") if isinstance(pair.get("after_transform"), dict) else None
                    ),
                )
                for phase in ("cold", "cache_hit_probe"):
                    started = time.perf_counter()
                    result = render_zone_pair(job)
                    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
                    append_viewer_perf_event(
                        viewer_root,
                        "zone_crop_render",
                        pair_uuid=pair_id,
                        zone_id=zone_id,
                        render_ms=elapsed_ms,
                        cache_hit=bool(result.cache_hit),
                        render_lifecycle=result.render_lifecycle,
                        visual_fidelity=result.visual_fidelity,
                        evidence_source="validation_runner",
                        probe_phase=phase,
                    )
                    evidence["event_count"] += 1
                    evidence["renders"].append(
                        {
                            "pair_uuid": pair_id,
                            "zone_id": zone_id,
                            "phase": phase,
                            "render_ms": elapsed_ms,
                            "cache_hit": bool(result.cache_hit),
                            "render_lifecycle": result.render_lifecycle,
                            "visual_fidelity": result.visual_fidelity,
                        }
                    )
                evidence["render_count"] += 1
            except Exception as exc:
                evidence["failures"].append(
                    {
                        "pair_uuid": pair_id,
                        "zone_id": zone_id,
                        "reason": type(exc).__name__,
                        "message": str(exc),
                    }
                )

    evidence["failure_count"] = len(evidence["failures"])
    evidence["status"] = "passed" if evidence["render_count"] > 0 and not evidence["failures"] else "failed"

    # Plan §15 Phase A-1 (HIGH-1 wire) — auditor #2 finding §1.1.2:
    # ``render_zone_pair`` may return ``skipped_missing_page_bbox`` /
    # ``relative_overlay`` when the PDF has no page-space bbox or background
    # image. The §3.2 ``aggregate_zone_outcomes`` infrastructure was added
    # but only invoked from ``audit_drawing_compare_mvp_exit.py``, so the
    # ``actual_crop_rate`` metric never landed in ``validation_summary.json``.
    # This block computes the stats once during validation so audits that
    # consume ``validation_summary.json`` directly (without re-running the
    # exit-gate script) see the same metric.
    try:
        evidence["actual_crop_stats"] = aggregate_zone_outcomes(
            evidence["renders"]
        ).to_dict()
    except Exception as exc:  # noqa: BLE001 — best-effort aggregation
        evidence["actual_crop_stats"] = {
            "error": type(exc).__name__,
            "message": str(exc),
        }

    _write_json(output_path, evidence)
    return evidence


def _selected_overlays_for_evidence(overlays: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[int, float, str]:
        selected = 0 if bool(item.get("selected_for_review")) else 1
        try:
            rank = float(item.get("priority_rank") or 999999.0)
        except (TypeError, ValueError):
            rank = 999999.0
        return (selected, rank, str(item.get("zone_id") or ""))

    return sorted(overlays, key=sort_key)


def _load_json_dict(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_artifact_path(package_root: Path, viewer_root: Path, value: Any) -> Path | None:
    if not value:
        return None
    text = str(value)
    if text.startswith("<redacted>"):
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    candidates = [package_root / path, viewer_root / path, Path.cwd() / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return package_root / path


def _resolve_source_path(value: Any) -> Path:
    text = str(value or "")
    if not text or text.startswith("<redacted>"):
        return Path(".")
    return Path(text)


def _viewer_perf_summary_for_summary(
    viewer_package: Any | None,
    output_dir: Path,
) -> dict[str, Any]:
    """Resolve the viewer root and return a viewer_perf summary, never raising.

    The viewer root is taken from the viewer package output paths when available
    (it carries the canonical viewer/ subdirectory). Falls back to ``<output>/viewer``
    so the field is still present for downstream consumers when the package object
    is missing.
    """

    viewer_root: Path | None = None
    if viewer_package is not None:
        candidate = getattr(viewer_package, "viewer_dir", None)
        if candidate:
            viewer_root = Path(str(candidate))
    if viewer_root is None:
        candidate = output_dir / "viewer"
        viewer_root = candidate if candidate.exists() else None
    return summarize_viewer_perf(viewer_root)


def _build_ai_policy_evidence(output_dir: Path | None = None) -> dict[str, Any]:
    """Record that AI is optional and missing models fall back to heuristics."""

    probe_cache = (output_dir / "ai_policy_probe_cache") if output_dir else None
    sample_zone = {
        "zone_id": "ai_policy_probe",
        "layer": "S-BEAM-REBAR",
        "entity_type": "TEXT",
        "change_type": "modified",
        "text_snippet": "보 단면 및 철근 간격 D13@100 -> D13@200",
        "raw_change_count": 1,
    }
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "ai_required": False,
        "embedding_optional": True,
        "llm_optional": True,
        "model_missing_handling": "warning",
        "warning_ko": "모델 없음 - 휴리스틱 분류만 사용",
        "heuristic_fallback_available": False,
        "fallback_without_model": {},
        "heuristic_only": {},
        "checks": [],
    }
    try:
        from src.services.comparison.ai_classifier import (
            AiClassifierConfig,
            classify_zones,
            clear_dispatcher_cache,
        )

        heuristic = classify_zones(
            [sample_zone],
            config=AiClassifierConfig.heuristic_only(),
        )
        clear_dispatcher_cache()
        missing_model_cfg = AiClassifierConfig(
            enabled=True,
            use_embedding=True,
            use_llm=False,
            embedding_backend_id="auto",
            embedding_backend_fallbacks=["__missing_optional_model__"],
            cache_dir=str(probe_cache) if probe_cache else None,
        )
        fallback = classify_zones([sample_zone], config=missing_model_cfg)
        clear_dispatcher_cache()

        heuristic_result = heuristic[0] if heuristic else None
        fallback_result = fallback[0] if fallback else None
        heuristic_used = str(getattr(heuristic_result, "classifier_used", "") or "")
        fallback_used = str(getattr(fallback_result, "classifier_used", "") or "")
        evidence["heuristic_only"] = {
            "result_count": len(heuristic),
            "classifier_used": heuristic_used,
            "summary_ko": str(getattr(heuristic_result, "summary_ko", "") or ""),
        }
        evidence["fallback_without_model"] = {
            "result_count": len(fallback),
            "configured_embedding": True,
            "embedding_backend_id": "auto",
            "classifier_used": fallback_used,
            "summary_ko": str(getattr(fallback_result, "summary_ko", "") or ""),
        }
        evidence["heuristic_fallback_available"] = (
            len(heuristic) == 1
            and len(fallback) == 1
            and heuristic_used == "heuristic"
            and fallback_used == "heuristic"
        )
        evidence["checks"] = [
            {
                "name": "heuristic_only_classifies_without_model",
                "status": "passed" if heuristic_used == "heuristic" else "failed",
            },
            {
                "name": "missing_embedding_model_falls_back_to_heuristic",
                "status": "passed" if fallback_used == "heuristic" else "failed",
            },
            {
                "name": "model_missing_is_warning_not_error",
                "status": "passed",
            },
        ]
        evidence["status"] = (
            "passed" if evidence["heuristic_fallback_available"] else "failed"
        )
    except Exception as exc:  # noqa: BLE001
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        evidence["checks"].append(
            {
                "name": "ai_policy_probe_exception",
                "status": "failed",
                "message": evidence["error"],
            }
        )
    finally:
        if probe_cache is not None:
            try:
                probe_cache.rmdir()
            except OSError:
                pass
    return evidence


def _build_summary_payload(
    *,
    args: argparse.Namespace,
    output_dir: Path,
    descriptors_a: Sequence[DrawingFileDescriptor],
    descriptors_b: Sequence[DrawingFileDescriptor],
    candidates: Sequence[MatchCandidate],
    compare_summary: BatchCompareSummary | None,
    timings: dict[str, float],
    memory: dict[str, float],
    ground_truth: dict[str, Any] | None,
    manual_metrics: dict[str, Any] | None,
    outputs: dict[str, Path],
    artifact_package: Any | None = None,
    preview_package: Any | None = None,
    executive_package: Any | None = None,
    review_dashboard_package: Any | None = None,
    viewer_package: Any | None = None,
    selected_zone_evidence: dict[str, Any] | None = None,
    ai_policy: dict[str, Any] | None = None,
    runtime_budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status_counts = Counter(candidate.status.value for candidate in candidates)
    confirmed = [candidate for candidate in candidates if candidate.is_confirmed]
    descriptor_warnings = _descriptor_warnings(descriptors_a, descriptors_b)
    compare_metrics = _compare_metrics(compare_summary)
    blocked_pairs = _blocked_pair_count(descriptors_a, descriptors_b)
    alternate_candidate_count = sum(len(candidate.alternates) for candidate in candidates)

    payload: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "input": {
            "a": str(Path(args.a).resolve()),
            "b": str(Path(args.b).resolve()),
            "recursive": bool(args.recursive),
            "cache_enabled": not bool(args.no_cache),
            "skip_compare": bool(args.skip_compare),
            "max_workers": getattr(args, "max_workers", None),
            "cad_policy": {
                "expand_blocks": not bool(getattr(args, "no_expand_blocks", False)),
                "block_text_detection": not bool(getattr(args, "no_block_text_detection", False)),
            },
            "ground_truth": (
                str(args.ground_truth.resolve())
                if getattr(args, "ground_truth", None)
                else None
            ),
            "review_ground_truth": (
                str(args.review_ground_truth.resolve())
                if getattr(args, "review_ground_truth", None)
                else None
            ),
            "manual_matches": (
                str(args.manual_matches.resolve())
                if getattr(args, "manual_matches", None)
                else None
            ),
            "reuse_match_candidates": (
                str(args.reuse_match_candidates.resolve())
                if getattr(args, "reuse_match_candidates", None)
                else None
            ),
            "dxf_cache_dir": (
                str(args.dxf_cache_dir.resolve())
                if getattr(args, "dxf_cache_dir", None)
                else None
            ),
            "compare_state_dir": (
                str(getattr(args, "compare_state_dir").resolve())
                if getattr(args, "compare_state_dir", None)
                else None
            ),
            "reuse_compare_state": (
                str(getattr(args, "reuse_compare_state").resolve())
                if getattr(args, "reuse_compare_state", None)
                else None
            ),
            "export_cloud_marks": bool(getattr(args, "export_cloud_marks", False)),
            "export_before_cloud_marks": bool(
                getattr(args, "export_before_cloud_marks", False)
            ),
            "cloud_export_mode": getattr(args, "cloud_export_mode", "selected"),
            "cloud_selection_csv": (
                str(args.cloud_selection_csv.resolve())
                if getattr(args, "cloud_selection_csv", None)
                else None
            ),
            "cloud_region_distance": float(getattr(args, "cloud_region_distance", 1000.0)),
            "max_cloud_regions_per_pair": int(
                getattr(args, "max_cloud_regions_per_pair", 150)
            ),
            "max_cloud_regions_total": int(getattr(args, "max_cloud_regions_total", 3000)),
            "change_zone_report": bool(getattr(args, "change_zone_report", False)),
            "executive_review": bool(getattr(args, "executive_review", False)),
            "executive_top_drawings": int(getattr(args, "executive_top_drawings", 15)),
            "executive_top_zones": int(getattr(args, "executive_top_zones", 30)),
            "review_dashboard": bool(getattr(args, "review_dashboard", False)),
            "top_review_issues": int(getattr(args, "top_review_issues", 100)),
            "top_issues_per_drawing": int(getattr(args, "top_issues_per_drawing", 20)),
            "fold_repetitive_layers": bool(getattr(args, "fold_repetitive_layers", True)),
            "artifact_dir": (
                str(args.artifact_dir.resolve())
                if getattr(args, "artifact_dir", None)
                else None
            ),
            "review_state": (
                str(args.review_state.resolve())
                if getattr(args, "review_state", None)
                else None
            ),
            "export_preview": bool(getattr(args, "export_preview", False)),
            "preview_dpi": int(getattr(args, "preview_dpi", 80)),
            "preview_max_edge_px": int(getattr(args, "preview_max_edge_px", 2400)),
            "max_preview_pairs": int(getattr(args, "max_preview_pairs", 5)),
            "export_viewer_package": bool(getattr(args, "export_viewer_package", False)),
            "viewer_mode": str(getattr(args, "viewer_mode", "image-tiles")),
            "viewer_render_policy": str(getattr(args, "viewer_render_policy", "lazy")),
            "viewer_engine": str(getattr(args, "viewer_engine", "auto")),
            "viewer_cache_dir": (
                str(args.viewer_cache_dir.resolve())
                if getattr(args, "viewer_cache_dir", None)
                else None
            ),
            "tile_size": int(getattr(args, "tile_size", 512)),
            "max_visible_overlays": int(getattr(args, "max_visible_overlays", 500)),
            "viewer_memory_budget_mb": int(getattr(args, "viewer_memory_budget_mb", 512)),
            "render_selected_on_open": bool(getattr(args, "render_selected_on_open", False)),
            "prefetch_neighbor_tiles": bool(getattr(args, "prefetch_neighbor_tiles", True)),
            "tile_prefetch_radius": int(getattr(args, "tile_prefetch_radius", 1)),
            "overview_max_edge": int(getattr(args, "overview_max_edge", 2200)),
            "focus_tile_max_edge": int(getattr(args, "focus_tile_max_edge", 1600)),
            "viewer_perf_log": bool(getattr(args, "viewer_perf_log", False)),
            "render_selected_zone_evidence": bool(
                getattr(args, "render_selected_zone_evidence", False)
            ),
            "selected_zone_evidence_per_pair": int(
                getattr(args, "selected_zone_evidence_per_pair", 1)
            ),
            "max_viewer_pages": int(getattr(args, "max_viewer_pages", 30)),
            "max_zone_tiles": int(getattr(args, "max_zone_tiles", 300)),
            "export_marked_pdf": bool(getattr(args, "export_marked_pdf", False)),
            "marked_pdf_mode": str(getattr(args, "marked_pdf_mode", "selected")),
            "write_ground_truth_template": bool(
                getattr(args, "write_ground_truth_template", False)
            ),
            "quality_gate": bool(getattr(args, "quality_gate", False)),
            "baseline": (
                str(args.baseline.resolve())
                if getattr(args, "baseline", None)
                else None
            ),
        },
        "output_dir": str(output_dir),
        "outputs": {name: str(path) for name, path in outputs.items()},
        "timings": {key: round(value, 3) for key, value in timings.items()},
        "memory": memory,
        "runtime_budget": runtime_budget if runtime_budget is not None else None,
        "files": {
            "a_count": len(descriptors_a),
            "b_count": len(descriptors_b),
            "total_count": len(descriptors_a) + len(descriptors_b),
            "a_size_bytes": _descriptor_total_size(descriptors_a),
            "b_size_bytes": _descriptor_total_size(descriptors_b),
            "a_kind_counts": _kind_counts(descriptors_a),
            "b_kind_counts": _kind_counts(descriptors_b),
        },
        "matching": {
            "total_candidates": len(candidates),
            "confirmed_pairs": len(confirmed),
            "auto_confirmed": status_counts.get(MatchStatus.AUTO_CONFIRMED.value, 0),
            "manual_confirmed": status_counts.get(MatchStatus.MANUAL_CONFIRMED.value, 0),
            "review_required": status_counts.get(MatchStatus.REVIEW_REQUIRED.value, 0),
            "unmatched_a": status_counts.get(MatchStatus.UNMATCHED_A.value, 0),
            "unmatched_b": status_counts.get(MatchStatus.UNMATCHED_B.value, 0),
            "rejected": status_counts.get(MatchStatus.REJECTED.value, 0),
            "ambiguous_candidates": status_counts.get(MatchStatus.REVIEW_REQUIRED.value, 0),
            "duplicate_a_assignments": _duplicate_a_assignments(candidates),
            "duplicate_b_assignments": _duplicate_b_assignments(candidates),
            "cad_pdf_blocked_pairs": blocked_pairs,
            "blocked_pairs": blocked_pairs,
            "alternate_candidate_count": alternate_candidate_count,
            "score_distribution": _score_distribution(candidates),
        },
        "stability": {
            "descriptor_warning_count": len(descriptor_warnings),
            "descriptor_warnings": descriptor_warnings[:100],
            "compare_failed": compare_metrics["failed"],
            "compare_errors": compare_metrics["errors"],
            "large_mode_pairs": compare_metrics["large_mode_pairs"],
            "truncated_pairs": compare_metrics["truncated_pairs"],
        },
        "comparison": compare_metrics["summary"],
        "change_artifacts": artifact_package.to_dict() if artifact_package else None,
        "preview_artifacts": preview_package.to_dict() if preview_package else None,
        "executive_review": executive_package.to_dict() if executive_package else None,
        "review_dashboard": (
            review_dashboard_package
            if isinstance(review_dashboard_package, dict)
            else review_dashboard_package.to_dict()
            if review_dashboard_package
            else None
        ),
        "viewer_package": viewer_package.to_dict() if viewer_package else None,
        "viewer_perf_summary": _viewer_perf_summary_for_summary(viewer_package, output_dir),
        "selected_zone_evidence": selected_zone_evidence,
        "ai_policy": ai_policy,
        "ground_truth": ground_truth,
        "manual_matches": manual_metrics,
        "quality": {
            "auto_precision": ground_truth.get("auto_precision") if ground_truth else None,
            "manual_precision": ground_truth.get("manual_precision") if ground_truth else None,
            "review_recall": ground_truth.get("review_recall") if ground_truth else None,
            "blocked_pairs": blocked_pairs,
            "alternate_candidate_count": alternate_candidate_count,
        },
    }
    payload["first_interactive_ready"] = _first_interactive_ready_payload(
        args=args,
        timings=timings,
        payload=payload,
    )
    return payload


def _first_interactive_ready_payload(
    *,
    args: argparse.Namespace,
    timings: dict[str, float],
    payload: dict[str, Any],
) -> dict[str, Any]:
    review_dashboard_ready_s = round(
        float(timings.get("scan_s", 0.0))
        + float(timings.get("match_s", 0.0))
        + float(timings.get("compare_s", 0.0))
        + float(timings.get("artifact_s", 0.0)),
        3,
    )
    total_s = round(float(timings.get("total_s", review_dashboard_ready_s)), 3)
    review_dashboard = payload.get("review_dashboard")
    queue_items: list[Any] = []
    if isinstance(review_dashboard, dict):
        queue = review_dashboard.get("review_queue")
        if isinstance(queue, dict):
            items = queue.get("items") or queue.get("top_structural_items")
            queue_items = items if isinstance(items, list) else []
        elif isinstance(review_dashboard.get("top_issues"), list):
            queue_items = review_dashboard["top_issues"]
    first_top_issue_ready_s = review_dashboard_ready_s if queue_items else 0.0
    viewer_metadata_ready_s = total_s if payload.get("viewer_package") else 0.0
    profile = str(
        getattr(args, "preset", "")
        or getattr(args, "profile", "")
        or getattr(args, "validation_profile", "")
        or getattr(args, "viewer_render_policy", "")
    ).strip().lower()
    speed_profile = profile in {"speed", "fast", "ultra_fast", "ultrafast", "quick", "scan"}
    dashboard_budget = 300.0 if speed_profile else 600.0
    issues: list[str] = []
    if review_dashboard_ready_s <= 0 or review_dashboard_ready_s > dashboard_budget:
        issues.append(f"review_dashboard_ready_s exceeds {dashboard_budget}")
    if first_top_issue_ready_s <= 0 or first_top_issue_ready_s > 600.0:
        issues.append("first_top_issue_ready_s exceeds 600")
    if viewer_metadata_ready_s <= 0 or viewer_metadata_ready_s > 900.0:
        issues.append("viewer_metadata_ready_s exceeds 900")
    return {
        "schema_version": 1,
        "status": "passed" if not issues else "failed",
        "profile": profile or "standard",
        "speed_profile": speed_profile,
        "review_dashboard_ready_s": review_dashboard_ready_s,
        "first_top_issue_ready_s": first_top_issue_ready_s,
        "viewer_metadata_ready_s": viewer_metadata_ready_s,
        "thresholds": {
            "review_dashboard_ready_s": dashboard_budget,
            "first_top_issue_ready_s": 600.0,
            "viewer_metadata_ready_s": 900.0,
        },
        "issues": issues,
    }


def _baseline_path_for_args(args: argparse.Namespace, out_dir: Path) -> Path | None:
    if getattr(args, "baseline", None):
        return Path(args.baseline).resolve()
    if getattr(args, "update_baseline", False):
        return out_dir / "validation_baseline.json"
    return None


def _previous_baseline_for_args(
    args: argparse.Namespace,
    baseline_path: Path | None,
) -> dict[str, Any] | None:
    override = getattr(args, "_baseline_payload", None)
    if isinstance(override, dict):
        return override
    if not baseline_path or not baseline_path.exists():
        return None
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _build_baseline_record(payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    dataset_id = str(
        getattr(args, "dataset_name", None)
        or Path(payload["input"]["a"]).stem
        or Path(payload["output_dir"]).name
    )
    return {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(),
        "dataset_id": _safe_dataset_id(dataset_id),
        "file_counts": {
            "a_count": payload["files"]["a_count"],
            "b_count": payload["files"]["b_count"],
            "total_count": payload["files"]["total_count"],
            "a_size_bytes": payload["files"]["a_size_bytes"],
            "b_size_bytes": payload["files"]["b_size_bytes"],
        },
        "matching": payload["matching"],
        "quality": payload["quality"],
        "performance": {
            "scan_s": payload["timings"].get("scan_s", 0.0),
            "match_s": payload["timings"].get("match_s", 0.0),
            "compare_s": payload["timings"].get("compare_s", 0.0),
            "total_s": payload["timings"].get("total_s", 0.0),
            "peak_memory_mb": payload["memory"].get("peak_mb", 0.0),
        },
        "failures": {
            "compare_failed": payload["stability"]["compare_failed"],
            "compare_errors": payload["stability"]["compare_errors"],
            "descriptor_warning_count": payload["stability"]["descriptor_warning_count"],
        },
        "thresholds": _thresholds(args),
    }


def _thresholds(args: argparse.Namespace) -> dict[str, float]:
    return {
        "min_auto_precision": float(
            getattr(args, "min_auto_precision", DEFAULT_MIN_AUTO_PRECISION)
        ),
        "min_recall": float(getattr(args, "min_recall", DEFAULT_MIN_RECALL)),
        "max_match_time_regression": float(
            getattr(args, "max_match_time_regression", DEFAULT_MAX_MATCH_TIME_REGRESSION)
        ),
    }


def _evaluate_quality_gate(
    payload: dict[str, Any],
    previous_baseline: dict[str, Any] | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    thresholds = _thresholds(args)
    issues: list[dict[str, Any]] = []
    unknown: list[str] = []
    quality = payload.get("quality") or {}
    matching = payload.get("matching") or {}
    comparison = payload.get("comparison") or {}

    auto_precision = quality.get("auto_precision")
    if auto_precision is None:
        unknown.append("auto_precision")
    elif float(auto_precision) < thresholds["min_auto_precision"]:
        issues.append(
            _gate_issue(
                "auto_precision",
                auto_precision,
                thresholds["min_auto_precision"],
                "Auto precision is below threshold",
            )
        )

    recall = payload.get("ground_truth", {}).get("recall") if payload.get("ground_truth") else None
    if recall is None:
        unknown.append("recall")
    elif float(recall) < thresholds["min_recall"]:
        issues.append(
            _gate_issue(
                "recall",
                recall,
                thresholds["min_recall"],
                "Recall is below threshold",
            )
        )

    structural_review_recall = quality.get("structural_review_recall")
    if structural_review_recall is not None and float(structural_review_recall) < thresholds["min_recall"]:
        issues.append(
            _gate_issue(
                "structural_review_recall",
                structural_review_recall,
                thresholds["min_recall"],
                "Structural review queue recall is below threshold",
            )
        )

    compare_failed = int(comparison.get("failed_pairs", 0) or 0)
    if compare_failed > 0:
        issues.append(
            _gate_issue("compare_failed", compare_failed, 0, "One or more comparisons failed")
        )

    duplicate_a = int(matching.get("duplicate_a_assignments", 0) or 0)
    if duplicate_a > 0:
        issues.append(
            _gate_issue("duplicate_a_assignments", duplicate_a, 0, "An A drawing is assigned more than once")
        )

    duplicate_b = int(matching.get("duplicate_b_assignments", 0) or 0)
    if duplicate_b > 0:
        issues.append(
            _gate_issue("duplicate_b_assignments", duplicate_b, 0, "A B drawing is assigned more than once")
        )

    stream_mismatches = int(comparison.get("change_zone_stream_mismatch_pairs", 0) or 0)
    if stream_mismatches > 0:
        issues.append(
            _gate_issue(
                "change_zone_stream_mismatch_pairs",
                stream_mismatches,
                0,
                "Change-zone stream records do not match full change counts",
            )
        )

    artifacts = payload.get("change_artifacts") or {}
    if artifacts:
        if not bool(artifacts.get("zone_coverage_complete", True)):
            issues.append(
                _gate_issue(
                    "zone_coverage_complete",
                    artifacts.get("zone_coverage_complete"),
                    True,
                    "Change-zone artifact coverage is incomplete",
                )
            )
        raw_changes = int(artifacts.get("raw_change_count", 0) or 0)
        zone_input = int(artifacts.get("zone_input_count", 0) or 0)
        if raw_changes and zone_input != raw_changes:
            issues.append(
                _gate_issue(
                    "zone_input_count",
                    zone_input,
                    raw_changes,
                    "Change-zone input count does not cover all raw changes",
                )
            )

    selected_zone_evidence = payload.get("selected_zone_evidence") or {}
    if bool(getattr(args, "render_selected_zone_evidence", False)):
        if selected_zone_evidence.get("status") != "passed":
            issues.append(
                _gate_issue(
                    "selected_zone_evidence",
                    selected_zone_evidence.get("status") or "missing",
                    "passed",
                    "Selected-zone render evidence was requested but did not pass",
                    failure_count=int(selected_zone_evidence.get("failure_count") or 0),
                )
            )

    baseline_match_s = _baseline_match_time(previous_baseline)
    current_match_s = float(payload.get("timings", {}).get("match_s", 0.0) or 0.0)
    if baseline_match_s and current_match_s > baseline_match_s * (1.0 + thresholds["max_match_time_regression"]):
        issues.append(
            _gate_issue(
                "match_time_regression",
                current_match_s,
                round(baseline_match_s * (1.0 + thresholds["max_match_time_regression"]), 3),
                "Match time regressed versus baseline",
                baseline=baseline_match_s,
            )
        )

    requested = bool(getattr(args, "quality_gate", False))
    status = "not_requested"
    if requested:
        status = "failed" if issues else "passed"

    return {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "status": status,
        "requested": requested,
        "issues": issues,
        "unknown_metrics": unknown,
        "thresholds": thresholds,
        "baseline_present": previous_baseline is not None,
        "evaluated_at": datetime.now().isoformat(),
    }


def _gate_issue(
    metric: str,
    value: Any,
    threshold: Any,
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    issue = {
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "message": message,
    }
    issue.update(extra)
    return issue


def _baseline_match_time(previous_baseline: dict[str, Any] | None) -> float | None:
    if not previous_baseline:
        return None
    try:
        return float((previous_baseline.get("performance") or {}).get("match_s") or 0.0)
    except Exception:
        return None


def _write_quality_gate(path: Path, quality_gate: dict[str, Any]) -> None:
    _write_json(path, quality_gate)


def _compare_metrics(summary: BatchCompareSummary | None) -> dict[str, Any]:
    if summary is None:
        return {
            "failed": 0,
            "errors": [],
            "large_mode_pairs": 0,
            "truncated_pairs": [],
            "summary": {
                "compare_skipped": True,
                "requested_pairs": 0,
                "completed_pairs": 0,
                "failed_pairs": 0,
                "cancelled_pairs": 0,
                "cancelled": False,
                "total_changes": 0,
            },
        }

    errors: list[dict[str, Any]] = []
    truncated_pairs: list[dict[str, Any]] = []
    stream_mismatch_pairs: list[dict[str, Any]] = []
    stream_record_count = 0
    large_mode_pairs = 0
    for item in summary.items:
        candidate = item.candidate
        if item.status == "failed":
            errors.append(
                {
                    "a": candidate.source_a.path if candidate.source_a else "",
                    "b": candidate.source_b.path if candidate.source_b else "",
                    "error": item.error or "",
                }
            )
        result = item.result
        if not result:
            continue
        metadata = result.metadata or {}
        expected_changes = _result_change_count(result)
        stream_count = int(metadata.get("change_zone_record_count") or 0)
        stream_record_count += stream_count
        if expected_changes and _requires_change_zone_stream(candidate, metadata) and (
            stream_count != expected_changes
            or not bool(metadata.get("change_zone_stream_complete", False))
        ):
            stream_mismatch_pairs.append(
                {
                    "a": candidate.source_a.path if candidate.source_a else "",
                    "b": candidate.source_b.path if candidate.source_b else "",
                    "expected_changes": expected_changes,
                    "stream_records": stream_count,
                    "stream_complete": bool(metadata.get("change_zone_stream_complete", False)),
                    "stream_path": metadata.get("change_zone_stream_path", ""),
                }
            )
        if _is_large_mode(metadata):
            large_mode_pairs += 1
        if metadata.get("truncated_changes"):
            truncated_pairs.append(
                {
                    "a": candidate.source_a.path if candidate.source_a else "",
                    "b": candidate.source_b.path if candidate.source_b else "",
                    "change_counts": metadata.get("change_counts", {}),
                    "change_records_in_memory": metadata.get(
                        "change_records_in_memory", len(result.changes)
                    ),
                    "omitted_change_counts": metadata.get("omitted_change_counts", {}),
                }
            )

    return {
        "failed": summary.failed_pairs,
        "errors": errors,
        "large_mode_pairs": large_mode_pairs,
        "truncated_pairs": truncated_pairs,
        "summary": {
            "compare_skipped": False,
            "requested_pairs": summary.requested_pairs,
            "total_pairs": summary.total_pairs,
            "completed_pairs": summary.completed_pairs,
            "failed_pairs": summary.failed_pairs,
            "cancelled_pairs": summary.cancelled_pairs,
            "cancelled": summary.cancelled,
            "total_changes": summary.total_changes,
            "change_zone_stream_records": stream_record_count,
            "change_zone_stream_mismatch_pairs": len(stream_mismatch_pairs),
            "change_zone_stream_mismatches": stream_mismatch_pairs[:20],
        },
    }


def _requires_change_zone_stream(
    candidate: MatchCandidate,
    metadata: dict[str, Any],
) -> bool:
    """Return True when missing stream metadata is a validation failure.

    CAD/DWG validation runs write JSONL change-zone streams so large-mode
    comparisons cannot silently drop raw entity changes before artifact export.
    PDF comparisons do not use that CAD stream path; their coverage gate is the
    generated change artifact's ``zone_coverage_complete`` and input-count
    fields. Requiring stream metadata for PDF-PDF pairs incorrectly fails valid
    PDF customer packages.
    """

    if str(metadata.get("comparison_type") or "").upper() == "PDF":
        return False
    if str(metadata.get("source_format") or "").lower() == "pdf":
        return False

    source_a = getattr(candidate, "source_a", None)
    source_b = getattr(candidate, "source_b", None)
    if source_a is not None and source_b is not None:
        return source_a.kind == DrawingKind.CAD and source_b.kind == DrawingKind.CAD

    return any(
        key in metadata
        for key in (
            "change_zone_stream_path",
            "change_zone_record_count",
            "change_zone_stream_complete",
        )
    )


def _is_large_mode(metadata: dict[str, Any]) -> bool:
    value = metadata.get("large_drawing_mode")
    return value in {True, "active", "force", "auto"}


def _descriptor_total_size(descriptors: Sequence[DrawingFileDescriptor]) -> int:
    total = 0
    for descriptor in descriptors:
        try:
            total += descriptor.path_obj.stat().st_size
        except OSError:
            continue
    return total


def _kind_counts(descriptors: Sequence[DrawingFileDescriptor]) -> dict[str, int]:
    return dict(Counter(descriptor.kind.value for descriptor in descriptors))


def _descriptor_warnings(
    descriptors_a: Sequence[DrawingFileDescriptor],
    descriptors_b: Sequence[DrawingFileDescriptor],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for side, descriptors in (("A", descriptors_a), ("B", descriptors_b)):
        for descriptor in descriptors:
            for warning in descriptor.warnings:
                warnings.append({"side": side, "path": descriptor.path, "warning": warning})
    return warnings


def _duplicate_b_assignments(candidates: Sequence[MatchCandidate]) -> int:
    return len(confirmed_pair_uniqueness_violations(candidates)["duplicate_b"])


def _duplicate_a_assignments(candidates: Sequence[MatchCandidate]) -> int:
    return len(confirmed_pair_uniqueness_violations(candidates)["duplicate_a"])


def _blocked_pair_count(
    descriptors_a: Sequence[DrawingFileDescriptor],
    descriptors_b: Sequence[DrawingFileDescriptor],
) -> int:
    a_cad = sum(1 for descriptor in descriptors_a if descriptor.kind == DrawingKind.CAD)
    a_pdf = sum(1 for descriptor in descriptors_a if descriptor.kind == DrawingKind.PDF)
    b_cad = sum(1 for descriptor in descriptors_b if descriptor.kind == DrawingKind.CAD)
    b_pdf = sum(1 for descriptor in descriptors_b if descriptor.kind == DrawingKind.PDF)
    return a_cad * b_pdf + a_pdf * b_cad


def _score_distribution(candidates: Sequence[MatchCandidate]) -> dict[str, int]:
    paired_scores = [
        candidate.score
        for candidate in candidates
        if candidate.source_a and candidate.source_b
    ]
    return {
        "gte_0_95": sum(1 for score in paired_scores if score >= 0.95),
        "gte_0_85": sum(1 for score in paired_scores if 0.85 <= score < 0.95),
        "gte_0_60": sum(1 for score in paired_scores if 0.60 <= score < 0.85),
        "lt_0_60": sum(1 for score in paired_scores if score < 0.60),
    }


def _result_change_count(result: ComparisonResult) -> int:
    counts = result.metadata.get("change_counts") if result.metadata else None
    if counts:
        return sum(int(counts.get(name, 0) or 0) for name in ("added", "deleted", "modified"))
    return result.total_changes


def _result_counts(result: ComparisonResult) -> dict[str, int]:
    counts = result.metadata.get("change_counts") if result.metadata else None
    if counts:
        return {
            "added": int(counts.get("added", 0) or 0),
            "deleted": int(counts.get("deleted", 0) or 0),
            "modified": int(counts.get("modified", 0) or 0),
        }
    return {
        "added": result.added_count,
        "deleted": result.deleted_count,
        "modified": result.modified_count,
    }


def _load_ground_truth(path: Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    rows: list[dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"a_path", "b_path", "expected_status"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Ground truth CSV missing columns: {sorted(missing)}")
        for row in reader:
            rows.append(
                {
                    "a_path": (row.get("a_path") or "").strip(),
                    "b_path": (row.get("b_path") or "").strip(),
                    "expected_status": (row.get("expected_status") or "").strip().lower(),
                }
            )
    return rows


def _load_review_ground_truth(path: Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    rows: list[dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        supported = {
            "drawing_label",
            "drawing_number",
            "category",
            "expected_category",
            "summary_contains",
            "expected_summary_contains",
            "source_format",
            "expected_source_format",
            "detection_source",
            "expected_detection_source",
            "bbox_status",
            "expected_bbox_status",
            "review_status",
            "expected_review_status",
            "notes",
        }
        if not fieldnames & supported:
            raise ValueError(
                "Review ground truth CSV must include at least one supported expectation column"
            )
        for row in reader:
            rows.append({key: (value or "").strip() for key, value in row.items()})
    return rows


def _evaluate_review_ground_truth(
    rows: Sequence[dict[str, str]],
    review_dashboard: dict[str, Any] | None,
) -> dict[str, Any]:
    queue_items = _review_queue_items_from_dashboard(review_dashboard)
    details: list[dict[str, Any]] = []
    passed_rows = 0
    for index, row in enumerate(rows, start=1):
        match = next((item for item in queue_items if _review_truth_row_matches(row, item)), None)
        passed = match is not None
        if passed:
            passed_rows += 1
        details.append(
            {
                "row_number": index,
                "drawing_label": row.get("drawing_label") or row.get("drawing_number") or "",
                "category": row.get("category") or row.get("expected_category") or "",
                "summary_contains": row.get("summary_contains")
                or row.get("expected_summary_contains")
                or "",
                "passed": passed,
                "actual_status": "matched" if passed else "missing",
                "matched_queue_key": str(match.get("queue_key") or "") if match else "",
            }
        )

    return {
        "schema_version": 1,
        "rows": len(rows),
        "passed_rows": passed_rows,
        "recall": _safe_ratio(passed_rows, len(rows)),
        "queue_item_count": len(queue_items),
        "details": details,
    }


def _review_queue_items_from_dashboard(review_dashboard: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(review_dashboard, dict):
        return []
    queue = review_dashboard.get("review_queue")
    if isinstance(queue, dict):
        items = queue.get("items") or queue.get("top_structural_items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    top_issues = review_dashboard.get("top_issues")
    if isinstance(top_issues, list):
        return [item for item in top_issues if isinstance(item, dict)]
    return []


def _review_truth_row_matches(row: dict[str, str], item: dict[str, Any]) -> bool:
    drawing_label = row.get("drawing_label") or row.get("drawing_number") or ""
    if drawing_label:
        labels = [
            item.get("drawing_label"),
            item.get("drawing_number"),
            item.get("display_label"),
            item.get("pair_id"),
            item.get("pair_uuid"),
        ]
        if _norm_review_truth_text(drawing_label) not in {
            _norm_review_truth_text(str(label or "")) for label in labels
        }:
            return False

    exact_fields = (
        ("category", "expected_category", "category"),
        ("source_format", "expected_source_format", "source_format"),
        ("detection_source", "expected_detection_source", "detection_source"),
        ("bbox_status", "expected_bbox_status", "bbox_status"),
        ("review_status", "expected_review_status", "review_status"),
    )
    for primary, alternate, item_key in exact_fields:
        expected = row.get(primary) or row.get(alternate) or ""
        if expected and not _value_matches_any(item.get(item_key), expected):
            return False

    contains = row.get("summary_contains") or row.get("expected_summary_contains") or ""
    if contains:
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("change_summary_ko", "reason_ko", "major_layers", "entity_types")
        )
        for token in _truth_contains_tokens(contains):
            if token.lower() not in haystack.lower():
                return False

    return True


def _value_matches_any(actual: Any, expected: str) -> bool:
    actual_norm = _norm_review_truth_text(str(actual or ""))
    return actual_norm in {_norm_review_truth_text(value) for value in expected.split("|") if value.strip()}


def _truth_contains_tokens(value: str) -> list[str]:
    return [token.strip() for token in value.split(";") if token.strip()]


def _norm_review_truth_text(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def _evaluate_ground_truth(
    rows: Sequence[dict[str, str]],
    candidates: Sequence[MatchCandidate],
    descriptors_a: Sequence[DrawingFileDescriptor],
    descriptors_b: Sequence[DrawingFileDescriptor],
    root_a: Path | None,
    root_b: Path | None,
) -> dict[str, Any]:
    pair_status: dict[tuple[str, str], str] = {}
    actual_confirmed: set[tuple[str, str]] = set()
    actual_auto: set[tuple[str, str]] = set()
    actual_manual: set[tuple[str, str]] = set()
    actual_review: set[tuple[str, str]] = set()
    unmatched_a: set[str] = set()
    unmatched_b: set[str] = set()

    for candidate in candidates:
        if candidate.source_a and candidate.source_b:
            key = (_norm_existing(candidate.source_a.path), _norm_existing(candidate.source_b.path))
            pair_status[key] = candidate.status.value
            if candidate.is_confirmed:
                actual_confirmed.add(key)
            if candidate.status == MatchStatus.AUTO_CONFIRMED:
                actual_auto.add(key)
            if candidate.status == MatchStatus.MANUAL_CONFIRMED:
                actual_manual.add(key)
            if candidate.status == MatchStatus.REVIEW_REQUIRED:
                actual_review.add(key)
        elif candidate.source_a and candidate.status == MatchStatus.UNMATCHED_A:
            unmatched_a.add(_norm_existing(candidate.source_a.path))
        elif candidate.source_b and candidate.status == MatchStatus.UNMATCHED_B:
            unmatched_b.add(_norm_existing(candidate.source_b.path))

    descriptors_by_a = {_norm_existing(descriptor.path): descriptor for descriptor in descriptors_a}
    descriptors_by_b = {_norm_existing(descriptor.path): descriptor for descriptor in descriptors_b}

    expected_matches: set[tuple[str, str]] = set()
    expected_reviews: set[tuple[str, str]] = set()
    row_results: list[dict[str, Any]] = []
    passed_rows = 0

    for row in rows:
        expected = row["expected_status"]
        a_key = _norm_truth_path(row["a_path"], root_a)
        b_key = _norm_truth_path(row["b_path"], root_b)
        actual = "missing"
        passed = False

        if expected == "match" and a_key and b_key:
            expected_matches.add((a_key, b_key))
            actual = pair_status.get((a_key, b_key), "missing")
            passed = actual in {
                MatchStatus.AUTO_CONFIRMED.value,
                MatchStatus.MANUAL_CONFIRMED.value,
            }
        elif expected == "review" and a_key and b_key:
            expected_reviews.add((a_key, b_key))
            actual = pair_status.get((a_key, b_key), "missing")
            passed = actual == MatchStatus.REVIEW_REQUIRED.value
        elif expected == "unmatched_a" and a_key:
            actual = MatchStatus.UNMATCHED_A.value if a_key in unmatched_a else pair_status_for_a(a_key, pair_status)
            passed = actual == MatchStatus.UNMATCHED_A.value
        elif expected == "unmatched_b" and b_key:
            actual = MatchStatus.UNMATCHED_B.value if b_key in unmatched_b else pair_status_for_b(b_key, pair_status)
            passed = actual == MatchStatus.UNMATCHED_B.value
        elif expected == "blocked" and a_key and b_key:
            actual = pair_status.get((a_key, b_key), "blocked")
            desc_a = descriptors_by_a.get(a_key)
            desc_b = descriptors_by_b.get(b_key)
            passed = actual == "blocked" and bool(desc_a and desc_b and not are_compatible(desc_a, desc_b))
        else:
            actual = "invalid_ground_truth_row"

        if passed:
            passed_rows += 1
        row_results.append(
            {
                "a_path": row["a_path"],
                "b_path": row["b_path"],
                "expected_status": expected,
                "actual_status": actual,
                "passed": passed,
            }
        )

    true_positive = len(actual_confirmed & expected_matches)
    auto_true_positive = len(actual_auto & expected_matches)
    manual_true_positive = len(actual_manual & expected_matches)
    precision = _safe_ratio(true_positive, len(actual_confirmed))
    recall = _safe_ratio(true_positive, len(expected_matches))
    review_hits = len(actual_review & expected_reviews)
    review_recall = _safe_ratio(review_hits, len(expected_reviews))

    return {
        "rows": len(rows),
        "passed_rows": passed_rows,
        "row_accuracy": _safe_ratio(passed_rows, len(rows)),
        "expected_matches": len(expected_matches),
        "actual_confirmed_matches": len(actual_confirmed),
        "true_positive_matches": true_positive,
        "precision": precision,
        "auto_precision": _safe_ratio(auto_true_positive, len(actual_auto)),
        "manual_precision": _safe_ratio(manual_true_positive, len(actual_manual)),
        "recall": recall,
        "expected_reviews": len(expected_reviews),
        "review_recall": review_recall,
        "details": row_results,
    }


def pair_status_for_a(a_key: str, pair_status: dict[tuple[str, str], str]) -> str:
    for pair, status in pair_status.items():
        if pair[0] == a_key:
            return status
    return "missing"


def pair_status_for_b(b_key: str, pair_status: dict[tuple[str, str], str]) -> str:
    for pair, status in pair_status.items():
        if pair[1] == b_key:
            return status
    return "missing"


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _norm_existing(path: str | Path) -> str:
    return str(Path(path).resolve()).casefold()


def _norm_truth_path(value: str, root: Path | None) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        return _norm_existing(path)
    if root is not None:
        return _norm_existing(root / path)
    return str(path).replace("\\", "/").casefold()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_match_csv(path: Path, candidates: Sequence[MatchCandidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_CSV_COLUMNS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(_match_csv_row(candidate))


def _match_csv_row(candidate: MatchCandidate) -> dict[str, Any]:
    source_a = candidate.source_a
    source_b = candidate.source_b
    return {
        "status": candidate.status.value,
        "score": round(candidate.score, 4),
        "a_path": source_a.path if source_a else "",
        "b_path": source_b.path if source_b else "",
        "a_kind": source_a.kind.value if source_a else "",
        "b_kind": source_b.kind.value if source_b else "",
        "a_extension": source_a.extension if source_a else "",
        "b_extension": source_b.extension if source_b else "",
        "a_drawing_number": source_a.identity.drawing_number if source_a else "",
        "b_drawing_number": source_b.identity.drawing_number if source_b else "",
        "a_sheet": source_a.identity.sheet if source_a else "",
        "b_sheet": source_b.identity.sheet if source_b else "",
        "reasons": " | ".join(candidate.reasons),
        "component_scores_json": json.dumps(candidate.component_scores, ensure_ascii=False),
        "alternate_count": len(candidate.alternates),
        "alternates_json": json.dumps(
            [
                {
                    "b_path": alternate.source_b.path,
                    "b_drawing_number": alternate.source_b.identity.drawing_number,
                    "score": round(alternate.score, 4),
                    "reasons": alternate.reasons,
                    "component_scores": alternate.component_scores,
                }
                for alternate in candidate.alternates
            ],
            ensure_ascii=False,
        ),
    }


def _write_compare_csv(path: Path, summary: BatchCompareSummary | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMPARE_CSV_COLUMNS)
        writer.writeheader()
        if summary is None:
            return
        for item in summary.items:
            writer.writerow(_compare_csv_row(item))


def _write_ground_truth_template(
    path: Path,
    candidates: Sequence[MatchCandidate],
    root_a: Path | None,
    root_b: Path | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["a_path", "b_path", "expected_status", "notes"],
        )
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                {
                    "a_path": _template_path(candidate.source_a.path, root_a)
                    if candidate.source_a
                    else "",
                    "b_path": _template_path(candidate.source_b.path, root_b)
                    if candidate.source_b
                    else "",
                    "expected_status": _ground_truth_status(candidate),
                    "notes": " | ".join(candidate.reasons[:3]),
                }
            )


def _write_review_queue_csv(
    path: Path,
    candidates: Sequence[MatchCandidate],
    root_a: Path | None,
    root_b: Path | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_QUEUE_CSV_COLUMNS)
        writer.writeheader()
        for candidate in candidates:
            if candidate.status != MatchStatus.REVIEW_REQUIRED:
                continue
            row = {
                "status": candidate.status.value,
                "score": round(candidate.score, 4),
                "a_path": _candidate_path(candidate.source_a, root_a),
                "b_path": _candidate_path(candidate.source_b, root_b),
                "a_drawing_number": (
                    candidate.source_a.identity.drawing_number if candidate.source_a else ""
                ),
                "b_drawing_number": (
                    candidate.source_b.identity.drawing_number if candidate.source_b else ""
                ),
                "reasons": " | ".join(candidate.reasons),
            }
            for index in range(3):
                alternate = candidate.alternates[index] if index < len(candidate.alternates) else None
                row[f"alternate_{index + 1}_path"] = (
                    _template_path(alternate.source_b.path, root_b) if alternate else ""
                )
                row[f"alternate_{index + 1}_drawing_number"] = (
                    alternate.source_b.identity.drawing_number if alternate else ""
                )
                row[f"alternate_{index + 1}_score"] = (
                    round(alternate.score, 4) if alternate else ""
                )
            writer.writerow(row)


def _write_unmatched_csv(
    path: Path,
    candidates: Sequence[MatchCandidate],
    root_a: Path | None,
    root_b: Path | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PAIR_CSV_COLUMNS)
        writer.writeheader()
        for candidate in candidates:
            if candidate.status not in {MatchStatus.UNMATCHED_A, MatchStatus.UNMATCHED_B}:
                continue
            writer.writerow(
                {
                    "status": candidate.status.value,
                    "a_path": _candidate_path(candidate.source_a, root_a),
                    "b_path": _candidate_path(candidate.source_b, root_b),
                    "score": round(candidate.score, 4),
                    "reasons": " | ".join(candidate.reasons),
                }
            )


def _write_manual_matches_template(
    path: Path,
    candidates: Sequence[MatchCandidate],
    root_a: Path | None,
    root_b: Path | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANUAL_TEMPLATE_COLUMNS)
        writer.writeheader()
        for candidate in candidates:
            if not candidate.source_a or not candidate.source_b:
                continue
            status = candidate.status.value
            if candidate.status == MatchStatus.AUTO_CONFIRMED:
                status = MatchStatus.MANUAL_CONFIRMED.value
            writer.writerow(
                {
                    "a_path": _template_path(candidate.source_a.path, root_a),
                    "b_path": _template_path(candidate.source_b.path, root_b),
                    "status": status,
                }
            )


def _write_blocked_pairs_csv(
    path: Path,
    descriptors_a: Sequence[DrawingFileDescriptor],
    descriptors_b: Sequence[DrawingFileDescriptor],
    root_a: Path | None,
    root_b: Path | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BLOCKED_CSV_COLUMNS)
        writer.writeheader()
        for desc_a in descriptors_a:
            for desc_b in descriptors_b:
                if are_compatible(desc_a, desc_b):
                    continue
                writer.writerow(
                    {
                        "a_path": _template_path(desc_a.path, root_a),
                        "b_path": _template_path(desc_b.path, root_b),
                        "a_kind": desc_a.kind.value,
                        "b_kind": desc_b.kind.value,
                        "reason": "CAD/PDF cross-family comparison is blocked",
                    }
                )


def _candidate_path(
    descriptor: DrawingFileDescriptor | None,
    root: Path | None,
) -> str:
    return _template_path(descriptor.path, root) if descriptor else ""


def _template_path(value: str, root: Path | None) -> str:
    path = Path(value).resolve()
    if root is not None:
        try:
            return str(path.relative_to(root))
        except ValueError:
            pass
    return str(path)


def _ground_truth_status(candidate: MatchCandidate) -> str:
    if candidate.status in {MatchStatus.AUTO_CONFIRMED, MatchStatus.MANUAL_CONFIRMED}:
        return "match"
    if candidate.status == MatchStatus.REVIEW_REQUIRED:
        return "review"
    if candidate.status == MatchStatus.UNMATCHED_A:
        return "unmatched_a"
    if candidate.status == MatchStatus.UNMATCHED_B:
        return "unmatched_b"
    if candidate.status == MatchStatus.REJECTED:
        return "blocked"
    return ""


def _compare_csv_row(item: Any) -> dict[str, Any]:
    candidate = item.candidate
    result = item.result
    counts = _result_counts(result) if result else {"added": 0, "deleted": 0, "modified": 0}
    metadata = result.metadata if result else {}
    return {
        "status": item.status,
        "a_path": candidate.source_a.path if candidate.source_a else "",
        "b_path": candidate.source_b.path if candidate.source_b else "",
        "match_status": candidate.status.value,
        "match_score": round(candidate.score, 4),
        "changes": _result_change_count(result) if result else "",
        "added": counts["added"] if result else "",
        "deleted": counts["deleted"] if result else "",
        "modified": counts["modified"] if result else "",
        "change_records_in_memory": metadata.get("change_records_in_memory", len(result.changes) if result else ""),
        "truncated_changes": metadata.get("truncated_changes", ""),
        "large_drawing_mode": metadata.get("large_drawing_mode", ""),
        "index_backend": metadata.get("index_backend", ""),
        "change_zone_stream_path": metadata.get("change_zone_stream_path", ""),
        "change_zone_record_count": metadata.get("change_zone_record_count", ""),
        "change_zone_stream_complete": metadata.get("change_zone_stream_complete", ""),
        "error": item.error or "",
        "warnings": " | ".join(result.warnings) if result else "",
        "change_zones": metadata.get("change_zone_count", ""),
        "after_marked_dxf": (metadata.get("marked_artifacts") or {}).get("after_marked_dxf", ""),
        "before_marked_dxf": (metadata.get("marked_artifacts") or {}).get("before_marked_dxf", ""),
    }


def _write_html_report(
    path: Path,
    payload: dict[str, Any],
    candidates: Sequence[MatchCandidate],
    summary: BatchCompareSummary | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    matching = payload["matching"]
    comparison = payload["comparison"]
    stability = payload["stability"]

    document = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Drawing Compare Realset Validation</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #222; }}
    h1, h2 {{ margin: 0 0 12px; }}
    section {{ margin: 24px 0; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 7px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .metric {{ border: 1px solid #d0d7de; padding: 10px; border-radius: 6px; }}
    .metric strong {{ display: block; font-size: 18px; margin-top: 4px; }}
    .muted {{ color: #57606a; }}
  </style>
</head>
<body>
  <h1>Drawing Compare Realset Validation</h1>
  <p class="muted">Generated at {html.escape(payload["generated_at"])}</p>
  <section>
    <h2>Summary</h2>
    <div class="grid">
      {_metric("A files", payload["files"]["a_count"])}
      {_metric("B files", payload["files"]["b_count"])}
      {_metric("Auto confirmed", matching["auto_confirmed"])}
      {_metric("Manual confirmed", matching["manual_confirmed"])}
      {_metric("Review required", matching["review_required"])}
      {_metric("Unmatched A", matching["unmatched_a"])}
      {_metric("Unmatched B", matching["unmatched_b"])}
      {_metric("Blocked pairs", matching["blocked_pairs"])}
      {_metric("Alternate candidates", matching["alternate_candidate_count"])}
      {_metric("Completed compares", comparison["completed_pairs"])}
      {_metric("Total changes", comparison["total_changes"])}
      {_metric("Change zones", _artifact_metric(payload, "zone_count"))}
    </div>
  </section>
  <section>
    <h2>Quality Gate</h2>
    {_quality_gate_table(payload.get("quality_gate", {}))}
  </section>
  <section>
    <h2>Performance</h2>
    {_dict_table(payload["timings"] | {"peak_memory_mb": payload["memory"]["peak_mb"]})}
  </section>
  <section>
    <h2>Auto Confirmed</h2>
    {_candidate_table(candidates, {MatchStatus.AUTO_CONFIRMED, MatchStatus.MANUAL_CONFIRMED})}
  </section>
  <section>
    <h2>Review Required</h2>
    {_candidate_table(candidates, {MatchStatus.REVIEW_REQUIRED})}
  </section>
  <section>
    <h2>Unmatched</h2>
    {_candidate_table(candidates, {MatchStatus.UNMATCHED_A, MatchStatus.UNMATCHED_B})}
  </section>
  <section>
    <h2>Errors</h2>
    {_errors_table(stability["compare_errors"], stability["descriptor_warnings"])}
  </section>
  <section>
    <h2>Large Mode / Truncation</h2>
    {_truncation_table(stability["truncated_pairs"], stability["large_mode_pairs"])}
  </section>
  <section>
    <h2>Compare Results</h2>
    {_compare_table(summary)}
  </section>
  <section>
    <h2>Change Artifacts</h2>
    {_change_artifacts_table(payload.get("change_artifacts"))}
  </section>
  <section>
    <h2>Executive Review</h2>
    {_executive_review_table(payload.get("executive_review"))}
  </section>
  <section>
    <h2>Preview Artifacts</h2>
    {_preview_artifacts_table(payload.get("preview_artifacts"))}
  </section>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def _metric(label: str, value: Any) -> str:
    return f'<div class="metric"><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></div>'


def _artifact_metric(payload: dict[str, Any], key: str) -> Any:
    artifacts = payload.get("change_artifacts") or {}
    return artifacts.get(key, 0)


def _dict_table(data: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in data.items()
    )
    return f"<table><tbody>{rows}</tbody></table>"


def _quality_gate_table(quality_gate: dict[str, Any]) -> str:
    if not quality_gate:
        return '<p class="muted">Not evaluated.</p>'
    rows = [
        f"<tr><th>Status</th><td>{html.escape(str(quality_gate.get('status', '')))}</td></tr>",
        f"<tr><th>Requested</th><td>{html.escape(str(quality_gate.get('requested', '')))}</td></tr>",
    ]
    for issue in quality_gate.get("issues", []):
        rows.append(
            "<tr>"
            f"<th>{html.escape(str(issue.get('metric', 'issue')))}</th>"
            f"<td>{html.escape(str(issue.get('message', '')))} "
            f"(value={html.escape(str(issue.get('value', '')))}, "
            f"threshold={html.escape(str(issue.get('threshold', '')))} )</td>"
            "</tr>"
        )
    if quality_gate.get("unknown_metrics"):
        rows.append(
            "<tr><th>Unknown metrics</th>"
            f"<td>{html.escape(', '.join(quality_gate.get('unknown_metrics', [])))}</td></tr>"
        )
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def _candidate_table(candidates: Sequence[MatchCandidate], statuses: set[MatchStatus]) -> str:
    rows = []
    for candidate in candidates:
        if candidate.status not in statuses:
            continue
        rows.append(
            "<tr>"
            f"<td>{_html_text(candidate.status.value)}</td>"
            f"<td>{candidate.score:.3f}</td>"
            f"<td>{_html_text(candidate.source_a.path if candidate.source_a else '')}</td>"
            f"<td>{_html_text(candidate.source_b.path if candidate.source_b else '')}</td>"
            f"<td>{_html_text(candidate.source_a.identity.drawing_number if candidate.source_a else '')}</td>"
            f"<td>{_html_text(candidate.source_b.identity.drawing_number if candidate.source_b else '')}</td>"
            f"<td>{_html_text(' | '.join(candidate.reasons))}</td>"
            f"<td>{_html_text(_alternates_text(candidate))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="8" class="muted">None</td></tr>')
    return (
        "<table><thead><tr><th>Status</th><th>Score</th><th>A</th><th>B</th>"
        "<th>A Drawing No.</th><th>B Drawing No.</th>"
        "<th>Reasons</th><th>Alternates</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _html_text(value: Any) -> str:
    return html.escape(str(value or ""))


def _alternates_text(candidate: MatchCandidate) -> str:
    return " | ".join(
        f"{Path(alternate.source_b.path).name} ({alternate.score:.2f})"
        for alternate in candidate.alternates
    )


def _errors_table(errors: Sequence[dict[str, Any]], warnings: Sequence[dict[str, Any]]) -> str:
    rows = []
    for error in errors:
        rows.append(
            "<tr>"
            "<td>compare</td>"
            f"<td>{html.escape(error.get('a', ''))}</td>"
            f"<td>{html.escape(error.get('b', ''))}</td>"
            f"<td>{html.escape(error.get('error', ''))}</td>"
            "</tr>"
        )
    for warning in warnings[:50]:
        rows.append(
            "<tr>"
            f"<td>descriptor {html.escape(warning.get('side', ''))}</td>"
            f"<td>{html.escape(warning.get('path', ''))}</td>"
            "<td></td>"
            f"<td>{html.escape(warning.get('warning', ''))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="4" class="muted">None</td></tr>')
    return (
        "<table><thead><tr><th>Type</th><th>A / Path</th><th>B</th><th>Message</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _truncation_table(truncated_pairs: Sequence[dict[str, Any]], large_mode_pairs: int) -> str:
    if not truncated_pairs:
        return f"<p>Large mode pairs: {large_mode_pairs}. No truncated change records.</p>"
    rows = []
    for item in truncated_pairs:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.get('a', ''))}</td>"
            f"<td>{html.escape(item.get('b', ''))}</td>"
            f"<td>{html.escape(json.dumps(item.get('change_counts', {}), ensure_ascii=False))}</td>"
            f"<td>{html.escape(str(item.get('change_records_in_memory', '')))}</td>"
            f"<td>{html.escape(json.dumps(item.get('omitted_change_counts', {}), ensure_ascii=False))}</td>"
            "</tr>"
        )
    return (
        f"<p>Large mode pairs: {large_mode_pairs}.</p>"
        "<table><thead><tr><th>A</th><th>B</th><th>Full Counts</th>"
        "<th>Records In Memory</th><th>Omitted Counts</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _compare_table(summary: BatchCompareSummary | None) -> str:
    if summary is None:
        return '<p class="muted">Skipped.</p>'
    rows = []
    for item in summary.items:
        changes = _result_change_count(item.result) if item.result else ""
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.status)}</td>"
            f"<td>{html.escape(item.candidate.source_a.path if item.candidate.source_a else '')}</td>"
            f"<td>{html.escape(item.candidate.source_b.path if item.candidate.source_b else '')}</td>"
            f"<td>{html.escape(str(changes))}</td>"
            f"<td>{html.escape(item.error or '')}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="5" class="muted">None</td></tr>')
    return (
        "<table><thead><tr><th>Status</th><th>A</th><th>B</th><th>Changes</th>"
        "<th>Error</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _change_artifacts_table(artifact_payload: dict[str, Any] | None) -> str:
    if not artifact_payload:
        return '<p class="muted">Not generated.</p>'
    paths = artifact_payload.get("output_paths", {})
    rows = [
        f"<tr><th>Pairs</th><td>{html.escape(str(artifact_payload.get('pair_count', 0)))}</td></tr>",
        f"<tr><th>Change zones</th><td>{html.escape(str(artifact_payload.get('zone_count', 0)))}</td></tr>",
        f"<tr><th>Raw changes</th><td>{html.escape(str(artifact_payload.get('raw_change_count', 0)))}</td></tr>",
        f"<tr><th>Zone input records</th><td>{html.escape(str(artifact_payload.get('zone_input_count', 0)))}</td></tr>",
        f"<tr><th>Coverage complete</th><td>{html.escape(str(artifact_payload.get('zone_coverage_complete', '')))}</td></tr>",
        f"<tr><th>Cloud mode</th><td>{html.escape(str(artifact_payload.get('cloud_export_mode', '')))}</td></tr>",
        f"<tr><th>Cloud regions</th><td>{html.escape(str(artifact_payload.get('cloud_region_count', 0)))}</td></tr>",
        f"<tr><th>Cloud omitted zones</th><td>{html.escape(str(artifact_payload.get('cloud_omitted_zone_count', 0)))}</td></tr>",
    ]
    for name in (
        "review_index_html",
        "change_zones_csv",
        "change_zones_json",
        "change_register_xlsx",
        "artifact_manifest_json",
        "cloud_omitted_zones_csv",
        "cloud_marked_dir",
    ):
        value = paths.get(name)
        if value:
            rows.append(f"<tr><th>{html.escape(name)}</th><td>{html.escape(str(value))}</td></tr>")
    warnings = artifact_payload.get("warnings") or []
    if warnings:
        rows.append(
            "<tr><th>Warnings</th>"
            f"<td>{html.escape(' | '.join(str(item) for item in warnings))}</td></tr>"
        )
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def _executive_review_table(executive_payload: dict[str, Any] | None) -> str:
    if not executive_payload:
        return '<p class="muted">Not generated.</p>'
    paths = executive_payload.get("output_paths", {})
    rows = [
        f"<tr><th>Drawings summarized</th><td>{html.escape(str(executive_payload.get('drawing_count', 0)))}</td></tr>",
        f"<tr><th>Top drawings</th><td>{html.escape(str(len(executive_payload.get('top_drawings') or [])))}</td></tr>",
        f"<tr><th>Top zones</th><td>{html.escape(str(len(executive_payload.get('top_zones') or [])))}</td></tr>",
        f"<tr><th>Repeated patterns</th><td>{html.escape(str(len(executive_payload.get('repeated_patterns') or [])))}</td></tr>",
    ]
    for name in (
        "executive_review_html",
        "drawing_change_brief_md",
        "drawing_change_brief_csv",
        "review_dashboard_json",
        "review_priority_csv",
        "layer_pattern_summary_csv",
    ):
        value = paths.get(name)
        if value:
            rows.append(f"<tr><th>{html.escape(name)}</th><td>{html.escape(str(value))}</td></tr>")
    warnings = executive_payload.get("warnings") or []
    if warnings:
        rows.append(
            "<tr><th>Warnings</th>"
            f"<td>{html.escape(' | '.join(str(item) for item in warnings))}</td></tr>"
        )
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def _preview_artifacts_table(preview_payload: dict[str, Any] | None) -> str:
    if not preview_payload:
        return '<p class="muted">Not generated.</p>'
    rows = [
        f"<tr><th>Pairs</th><td>{html.escape(str(preview_payload.get('pair_count', 0)))}</td></tr>",
        f"<tr><th>Rendered previews</th><td>{html.escape(str(preview_payload.get('preview_count', 0)))}</td></tr>",
        f"<tr><th>Skipped previews</th><td>{html.escape(str(preview_payload.get('preview_skipped_count', 0)))}</td></tr>",
        f"<tr><th>Max preview pairs</th><td>{html.escape(str(preview_payload.get('max_preview_pairs', '')))}</td></tr>",
        f"<tr><th>Zone overlays</th><td>{html.escape(str(preview_payload.get('zone_overlay_count', 0)))}</td></tr>",
        f"<tr><th>Manifest</th><td>{html.escape(str(preview_payload.get('manifest_path', '')))}</td></tr>",
    ]
    warnings = preview_payload.get("warnings") or []
    if warnings:
        rows.append(
            "<tr><th>Warnings</th>"
            f"<td>{html.escape(' | '.join(str(item) for item in warnings[:10]))}</td></tr>"
        )
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def _write_manifest_index(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for dataset in payload.get("datasets", []):
        rows.append(
            "<tr>"
            f"<td>{html.escape(dataset.get('dataset_id', ''))}</td>"
            f"<td>{html.escape(dataset.get('quality_gate', ''))}</td>"
            f"<td>{html.escape(str(dataset.get('matching', {}).get('auto_confirmed', 0)))}</td>"
            f"<td>{html.escape(str(dataset.get('matching', {}).get('review_required', 0)))}</td>"
            f"<td>{html.escape(str(dataset.get('matching', {}).get('unmatched_a', 0)))}</td>"
            f"<td>{html.escape(str(dataset.get('matching', {}).get('unmatched_b', 0)))}</td>"
            f"<td>{html.escape(str(dataset.get('comparison', {}).get('failed_pairs', 0)))}</td>"
            f"<td>{html.escape(str(dataset.get('timings', {}).get('match_s', 0)))}</td>"
            f"<td><a href='{html.escape(_relative_link(path, dataset.get('html_report', '')))}'>report</a></td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="9" class="muted">No datasets</td></tr>')
    document = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Drawing Compare Validation Index</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #222; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d0d7de; padding: 7px; text-align: left; }}
    th {{ background: #f6f8fa; }}
    .muted {{ color: #57606a; }}
  </style>
</head>
<body>
  <h1>Drawing Compare Validation Index</h1>
  <p>Quality gate: {html.escape(payload.get("quality_gate", {}).get("status", ""))}</p>
  <table>
    <thead>
      <tr><th>Dataset</th><th>Gate</th><th>Auto</th><th>Review</th>
      <th>Unmatched A</th><th>Unmatched B</th><th>Failed</th><th>Match s</th><th>Report</th></tr>
    </thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def _relative_link(index_path: Path, target: str) -> str:
    if not target:
        return ""
    try:
        return str(Path(target).resolve().relative_to(index_path.parent.resolve())).replace("\\", "/")
    except Exception:
        return str(target)


if __name__ == "__main__":
    raise SystemExit(main())
