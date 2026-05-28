# -*- coding: utf-8 -*-
"""Prepare a customer-grade Drawing Compare evidence manifest.

The script does not run comparisons and does not certify synthetic data as final
evidence. It reads completed validation outputs plus operator dry-run artifacts,
writes the manifest consumed by ``audit_drawing_compare_mvp_exit.py``, and exits
non-zero when the evidence still cannot satisfy the customer-grade gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

try:  # Running as ``python scripts/prepare_...``.
    from audit_drawing_compare_mvp_exit import (
        CAD_INPUT_EXTENSIONS,
        CONFIRMED_EXPORT_SUFFIXES,
        APPROVED_OPERATOR_REVIEWER_ROLES,
        PDF_INPUT_EXTENSIONS,
        REQUIRED_OPERATOR_WORKFLOW_CHECKS,
        REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS,
        STRICT_MAX_CACHE_HIT_ZONE_RENDER_MS,
        STRICT_MAX_COLD_ZONE_RENDER_MS,
        STRUCTURAL_COVERAGE_TERMS,
        dataset_strata_csv_issues,
        operator_notes_have_substantive_review_notes,
        read_operator_notes_text,
        review_decision_truth_csv_issues,
        review_ground_truth_csv_issues,
        summarize_bbox_quality,
        summarize_dataset_strata_csv,
        summarize_first_interactive_readiness,
        summarize_large_dwg_resource_probe,
        summarize_review_decision_truth_csv,
        summarize_viewer_perf,
        _resolve_viewer_manifest_ref,
        _visual_asset_manifest_refs,
        _visual_asset_policy_issues,
    )
except ImportError:  # Imported as ``scripts.prepare_...`` in tests.
    from scripts.audit_drawing_compare_mvp_exit import (
        CAD_INPUT_EXTENSIONS,
        CONFIRMED_EXPORT_SUFFIXES,
        APPROVED_OPERATOR_REVIEWER_ROLES,
        PDF_INPUT_EXTENSIONS,
        REQUIRED_OPERATOR_WORKFLOW_CHECKS,
        REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS,
        STRICT_MAX_CACHE_HIT_ZONE_RENDER_MS,
        STRICT_MAX_COLD_ZONE_RENDER_MS,
        STRUCTURAL_COVERAGE_TERMS,
        dataset_strata_csv_issues,
        operator_notes_have_substantive_review_notes,
        read_operator_notes_text,
        review_decision_truth_csv_issues,
        review_ground_truth_csv_issues,
        summarize_bbox_quality,
        summarize_dataset_strata_csv,
        summarize_first_interactive_readiness,
        summarize_large_dwg_resource_probe,
        summarize_review_decision_truth_csv,
        summarize_viewer_perf,
        _resolve_viewer_manifest_ref,
        _visual_asset_manifest_refs,
        _visual_asset_policy_issues,
    )

# Plan §17 F6 (GPT Pro deep-research review) — manifest provenance helpers.
# Add the repo root to sys.path so ``src.services.comparison.manifest_provenance``
# resolves whether the script is invoked from the worktree or imported as
# ``scripts.prepare_drawing_compare_customer_evidence`` from a test runner.
P5_G26_BENCHMARK_ID = "p5_g26_selection_latency_soak"
P5_G26_PROFILE = "selection_latency_hard_gate"
P5_G26_REQUIRED_GATES = {
    "p5_g26_wp_a_gui_hot_path_contract",
    "p5_g26_wp_b_pdf_first_responsiveness_contract",
    "p5_g26_event_loop_gap_max_ms",
    "p5_g26_click_hot_path_full_work_count",
    "p5_g26_cached_page_navigation_render_call_count",
    "p5_g26_repeat_cache_hit_rate",
    "p5_g26_blank_viewer_count",
    "p5_g26_cad_to_pdf_hot_path_count",
    "p5_g26_zone_selection_count",
    "p5_g26_zone_selection_telemetry_count",
    "p5_g26_zone_selection_p95_ms",
    "p5_g26_zone_selection_worker_spawn_count",
    "p5_g26_zone_selection_background_work_count",
    "p5_g26_zone_selection_stale_visible_count",
}
P5_G27_BENCHMARK_ID = "p5_g27_selected_zone_crop_soak"
P5_G27_PROFILE = "selected_zone_crop_first_lifecycle"
P5_G27_REQUIRED_GATES = {
    "p5_g27_crop_first_result_visible",
    "p5_g27_crop_visible_before_vector_focus",
    "p5_g27_crop_visible_p95_ms",
    "p5_g27_vector_failure_does_not_clear_background",
    "p5_g27_blank_selected_zone_count",
    "p5_g27_stale_result_visible_count",
    "p5_g27_cancel_without_visible_regression_count",
    "p5_g27_timeout_count",
    "p5_g27_fallback_missing_reason_count",
    "p5_g27_event_loop_gap_max_ms",
    "p5_g27_worker_cleanup_ok",
    "p5_g27_orphan_worker_count",
}
P5_G28_BENCHMARK_ID = "p5_g28_cache_plateau_soak"
P5_G28_PROFILE = "tile_cache_plateau_lifecycle_seed"
P5_G28_CACHE_CATEGORY_NAMES = {
    "display_list",
    "dxf_index",
    "visual_asset",
    "overlay",
    "spool",
}
P5_G28_REQUIRED_GATES = {
    "p5_g28_tile_retention_completed",
    "p5_g28_tile_cache_byte_plateau",
    "p5_g28_tile_cache_eviction_observed",
    "p5_g28_tile_cache_eviction_reason_present",
    "p5_g28_tile_cache_orphan_payloads_zero",
    "p5_g28_tile_cache_stale_manifest_zero",
    "p5_g28_hot_pair_retained",
    "p5_g28_evicted_pair_cache_miss",
    "p5_g28_single_entry_over_cap_count",
    "p5_g28_prune_p95_ms",
    "p5_g28_event_loop_gap_p95_ms",
    "p5_g28_event_loop_over_500ms_count",
    "p5_g28_cache_category_breakdown_present",
    "p5_g28_display_list_cache_plateau",
    "p5_g28_dxf_index_cache_plateau",
    "p5_g28_visual_asset_cache_plateau",
    "p5_g28_overlay_cache_plateau",
    "p5_g28_spool_namespace_plateau",
    "p5_g28_cache_category_orphans_zero",
    "p5_g28_cache_category_stale_entries_zero",
    "p5_g28_cache_plateau_tail_slope",
}

_PREPARE_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_PREPARE_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PREPARE_REPO_ROOT))
try:
    from src.services.comparison.manifest_provenance import (
        TEMPLATE_DETECTION_CLEAN,
        TEMPLATE_DETECTION_FOUND,
        build_provenance,
        compute_file_sha256,
    )

    _PROVENANCE_AVAILABLE = True
except Exception:
    # Released-tree fallback: when the scripts ship without ``src/`` (e.g.,
    # the customer-shareable bundle audited by
    # ``test_release_drawing_compare_workbench``), the import fails but the
    # prepare script must still run. Manifests then carry no ``provenance``
    # block; the customer-grade audit gate will reject them as expected,
    # which is the intended behaviour for old/release-trimmed bundles.
    _PROVENANCE_AVAILABLE = False


def _resolve_tool_version() -> str:
    """Return a short version identifier for the manifest provenance.

    Tries (in order): ``git rev-parse --short HEAD`` in the repo root,
    then a ``VERSION`` file at the repo root, then the literal ``"unknown"``.
    Never raises — the value is informational and the SHA-256 chain
    detects content tampering even if this string is wrong.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
            cwd=str(_PREPARE_REPO_ROOT),
            encoding="utf-8",
        )
        value = out.stdout.strip()
        if value:
            return value
    except Exception:
        pass
    try:
        version_file = _PREPARE_REPO_ROOT / "VERSION"
        if version_file.exists():
            value = version_file.read_text(encoding="utf-8").strip()
            if value:
                return value
    except Exception:
        pass
    return "unknown"


CUSTOMER_GRADE_SOURCE_KINDS = {"customer", "customer_grade"}
APPROVED_DATASET_STATUS = "approved_for_mvp_exit"
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
CAD_STRUCTURAL_TEXT_ENTITY_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "INSERT"}
TILE_CACHE_MB_ENV_VAR = "DRAWING_COMPARE_TILE_CACHE_MB"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        type=Path,
        action="append",
        required=True,
        help="Completed validation output folder. Repeat for CAD, PDF, and CAD-PDF block evidence.",
    )
    parser.add_argument("--out", type=Path, required=True, help="Output customer_evidence_manifest.json")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument(
        "--dataset-source-kind",
        required=True,
        choices=("customer", "customer_grade", "synthetic"),
        help="Declared validation-set source. synthetic is allowed for gap probes but never ready.",
    )
    parser.add_argument("--dataset-source-description", required=True)
    parser.add_argument(
        "--dataset-approval-status",
        required=True,
        choices=("approved_for_mvp_exit", "draft", "synthetic_probe"),
    )
    parser.add_argument("--dataset-approver", required=True)
    parser.add_argument("--ground-truth-owner", required=True)
    parser.add_argument("--review-ground-truth", type=Path, required=True)
    parser.add_argument("--review-decision-truth", type=Path)
    parser.add_argument("--dataset-strata", type=Path)
    parser.add_argument(
        "--large-dwg-probe",
        type=Path,
        required=False,
        help="Large-DWG probe JSON with RSS, progress heartbeat, and cancel recovery metrics.",
    )
    parser.add_argument(
        "--ground-truth-status",
        choices=("reviewed", "approved"),
        default="approved",
    )
    parser.add_argument("--validation-date", default=date.today().isoformat())
    parser.add_argument("--sheet-count", type=int, help="Override sheet count. Defaults to completed pairs.")
    parser.add_argument("--operator-reviewer-role", required=True)
    parser.add_argument("--operator-notes-file", type=Path)
    parser.add_argument("--operator-screenshots-dir", type=Path)
    parser.add_argument("--confirmed-export-artifact", type=Path, required=True)
    parser.add_argument(
        "--audit-json",
        type=Path,
        help="Output path for the generated path-leakage audit summary. Defaults next to --out.",
    )
    parser.add_argument("--min-total-pairs", type=int, default=20)
    parser.add_argument("--max-total-pairs", type=int, default=50)
    parser.add_argument(
        "--max-first-review-ready-s",
        type=float,
        default=1_800.0,
        help="Maximum validation total_s allowed for customer-grade first review-ready evidence.",
    )
    parser.add_argument(
        "--max-cold-zone-render-ms",
        type=float,
        default=10_000.0,
        help="Maximum selected-zone cold render p95 allowed for customer-grade evidence.",
    )
    parser.add_argument(
        "--max-cache-hit-zone-render-ms",
        type=float,
        default=2_000.0,
        help="Maximum selected-zone cache-hit render p95 allowed for customer-grade evidence.",
    )
    parser.add_argument(
        "--strict-zone-render-budget",
        action="store_true",
        help=(
            "Apply the tightened selected-zone latency thresholds "
            "(cold p95 <= 2000 ms, cache-hit p95 <= 500 ms) per Plan §17 "
            "Phase B-5 (GPT Pro deep-research review 2026-05-17). Mirrors "
            "the audit script's flag so the prepare-time readiness check "
            "uses the same thresholds. Without this flag the legacy "
            "10000/2000 ms defaults apply with a deprecation warning."
        ),
    )
    parser.add_argument(
        "--p5-g7-tile-eviction-proof-dir",
        type=Path,
        action="append",
        default=[],
        help=(
            "Validation output folder from a forced tile-cache eviction proof run. "
            "Repeat as needed. These runs are preserved as supporting evidence and "
            "are not counted as customer corpus sheets."
        ),
    )
    parser.add_argument(
        "--p5-g7-tile-eviction-release-manifest",
        type=Path,
        action="append",
        default=[],
        help="Release manifest from the forced tile-eviction proof run.",
    )
    parser.add_argument(
        "--require-p5-g7-tile-eviction-proof",
        action="store_true",
        help="Require a passing P5-G7 forced tile-eviction proof before manifest readiness can pass.",
    )
    parser.add_argument(
        "--p5-g6-tile-cache-mb",
        type=float,
        help="Expected DRAWING_COMPARE_TILE_CACHE_MB cap used by the P5-G7 forced tile-eviction proof.",
    )
    parser.add_argument(
        "--p5-g16-benchmark-json",
        "--p5-g16-real-corpus-replay",
        dest="p5_g16_benchmark_json",
        type=Path,
        action="append",
        default=[],
        help=(
            "P5-G16 real-corpus replay JSON. Repeat as needed. The path is "
            "recorded in the customer evidence manifest so the final audit can "
            "discover replay evidence without a separate manual path handoff."
        ),
    )
    parser.add_argument(
        "--p5-g22-gui-soak-json",
        "--p5-g22-actual-gui-soak",
        dest="p5_g22_gui_soak_json",
        type=Path,
        action="append",
        default=[],
        help=(
            "P5-G22 actual GUI soak JSON. Repeat as needed. The path is "
            "recorded in the customer evidence manifest so the final audit can "
            "discover live Qt/QML navigation evidence without a manual handoff."
        ),
    )
    parser.add_argument(
        "--p5-g26-selection-latency-json",
        "--p5-g26-selection-latency-soak",
        dest="p5_g26_selection_latency_json",
        type=Path,
        action="append",
        default=[],
        help=(
            "P5-G26 selection latency JSON. Repeat as needed. The path is "
            "recorded in the customer evidence manifest so the final audit can "
            "discover GUI/PDF hot-path contract evidence without a manual handoff."
        ),
    )
    parser.add_argument(
        "--p5-g27-selected-zone-crop-json",
        "--p5-g27-selected-zone-crop-soak",
        dest="p5_g27_selected_zone_crop_json",
        type=Path,
        action="append",
        default=[],
        help=(
            "P5-G27 selected-zone crop-first JSON. Repeat as needed. The path is "
            "recorded in the customer evidence manifest so the final audit can "
            "discover crop-first lifecycle evidence without a manual handoff."
        ),
    )
    parser.add_argument(
        "--p5-g28-cache-plateau-json",
        "--p5-g28-cache-plateau-soak",
        dest="p5_g28_cache_plateau_json",
        type=Path,
        action="append",
        default=[],
        help=(
            "P5-G28 cache plateau JSON. Repeat as needed. The path is recorded "
            "in the customer evidence manifest for standalone plateau audit "
            "discovery without making P5-G28 a default customer-grade gate."
        ),
    )
    parser.add_argument(
        "--required-structural-coverage",
        action="append",
        choices=tuple(STRUCTURAL_COVERAGE_TERMS),
        help="Required structural bucket. Defaults to all MVP structural buckets.",
    )
    return parser.parse_args(argv)


def _resolve_zone_render_budget(
    args: argparse.Namespace,
) -> tuple[float, float]:
    """Return (max_cold_ms, max_cache_hit_ms) per Plan §17 Phase B-5.

    Mirrors ``audit_drawing_compare_mvp_exit._resolve_zone_render_budget``
    so the prepare-time readiness check uses the same strict/advisory
    thresholds as the audit. Strict mode (``--strict-zone-render-budget``)
    returns the tightened pair (2000/500 ms); legacy mode keeps the
    existing 10000/2000 ms defaults and emits a one-time deprecation
    notice on stderr.
    """
    if getattr(args, "strict_zone_render_budget", False):
        return (
            STRICT_MAX_COLD_ZONE_RENDER_MS,
            STRICT_MAX_CACHE_HIT_ZONE_RENDER_MS,
        )
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
    result = prepare_manifest(
        result_dirs=[path.resolve() for path in args.results_dir],
        out_path=args.out.resolve(),
        dataset_id=args.dataset_id,
        dataset_source_kind=args.dataset_source_kind,
        dataset_source_description=args.dataset_source_description,
        dataset_approval_status=args.dataset_approval_status,
        dataset_approver=args.dataset_approver,
        validation_date=args.validation_date,
        ground_truth_owner=args.ground_truth_owner,
        review_ground_truth=args.review_ground_truth.resolve(),
        review_decision_truth=args.review_decision_truth.resolve() if args.review_decision_truth else None,
        dataset_strata=args.dataset_strata.resolve() if args.dataset_strata else None,
        large_dwg_probe=args.large_dwg_probe.resolve() if args.large_dwg_probe else None,
        ground_truth_status=args.ground_truth_status,
        operator_reviewer_role=args.operator_reviewer_role,
        operator_notes_file=args.operator_notes_file.resolve() if args.operator_notes_file else None,
        operator_screenshots_dir=(
            args.operator_screenshots_dir.resolve() if args.operator_screenshots_dir else None
        ),
        confirmed_export_artifact=args.confirmed_export_artifact.resolve(),
        audit_json=(args.audit_json.resolve() if args.audit_json else None),
        sheet_count=args.sheet_count,
        min_total_pairs=args.min_total_pairs,
        max_total_pairs=args.max_total_pairs,
        max_first_review_ready_s=args.max_first_review_ready_s,
        max_cold_zone_render_ms=resolved_max_cold_zone_render_ms,
        max_cache_hit_zone_render_ms=resolved_max_cache_hit_zone_render_ms,
        required_structural_coverage=args.required_structural_coverage,
        p5_g7_tile_eviction_proof_dirs=[
            path.resolve() for path in args.p5_g7_tile_eviction_proof_dir
        ],
        p5_g7_tile_eviction_release_manifests=[
            path.resolve() for path in args.p5_g7_tile_eviction_release_manifest
        ],
        require_p5_g7_tile_eviction_proof=args.require_p5_g7_tile_eviction_proof,
        p5_g6_tile_cache_mb=args.p5_g6_tile_cache_mb,
        p5_g16_benchmark_json=[path.resolve() for path in args.p5_g16_benchmark_json],
        p5_g22_gui_soak_json=[path.resolve() for path in args.p5_g22_gui_soak_json],
        p5_g26_selection_latency_json=[
            path.resolve() for path in args.p5_g26_selection_latency_json
        ],
        p5_g27_selected_zone_crop_json=[
            path.resolve() for path in args.p5_g27_selected_zone_crop_json
        ],
        p5_g28_cache_plateau_json=[
            path.resolve() for path in args.p5_g28_cache_plateau_json
        ],
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["status"] == "ready" else 1


def prepare_manifest(
    *,
    result_dirs: Sequence[Path],
    out_path: Path,
    dataset_id: str,
    dataset_source_kind: str,
    dataset_source_description: str,
    dataset_approval_status: str,
    dataset_approver: str,
    validation_date: str,
    ground_truth_owner: str,
    review_ground_truth: Path,
    ground_truth_status: str,
    operator_reviewer_role: str,
    operator_notes_file: Path | None,
    operator_screenshots_dir: Path | None,
    confirmed_export_artifact: Path,
    review_decision_truth: Path | None = None,
    dataset_strata: Path | None = None,
    large_dwg_probe: Path | None = None,
    audit_json: Path | None = None,
    sheet_count: int | None = None,
    min_total_pairs: int = 20,
    max_total_pairs: int = 50,
    max_first_review_ready_s: float = 1_800.0,
    max_cold_zone_render_ms: float = 10_000.0,
    max_cache_hit_zone_render_ms: float = 2_000.0,
    required_structural_coverage: Sequence[str] | None = None,
    p5_g7_tile_eviction_proof_dirs: Sequence[Path] | None = None,
    p5_g7_tile_eviction_release_manifests: Sequence[Path] | None = None,
    require_p5_g7_tile_eviction_proof: bool = False,
    p5_g6_tile_cache_mb: float | None = None,
    p5_g16_benchmark_json: Sequence[Path] | None = None,
    p5_g22_gui_soak_json: Sequence[Path] | None = None,
    p5_g26_selection_latency_json: Sequence[Path] | None = None,
    p5_g27_selected_zone_crop_json: Sequence[Path] | None = None,
    p5_g28_cache_plateau_json: Sequence[Path] | None = None,
) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    audit_json = audit_json or out_path.with_name("sharable_path_audit_summary.json")
    review_decision_truth = review_decision_truth or review_ground_truth.with_name("review_decision_truth.csv")
    dataset_strata = dataset_strata or review_ground_truth.with_name("dataset_strata.csv")
    large_dwg_probe = large_dwg_probe or review_ground_truth.with_name("large_dwg_probe.json")
    required = list(required_structural_coverage or STRUCTURAL_COVERAGE_TERMS.keys())
    loaded = [_load_result_dir(path) for path in result_dirs]
    result_dir_forced_tile_eviction_proofs = [
        summarize_p5_g7_forced_tile_eviction_proof(
            item["summary"] if isinstance(item.get("summary"), dict) else {},
            result_dir=item["path"],
            summary_path=Path(item["path"]) / "validation_summary.json",
            expected_tile_cache_mb=p5_g6_tile_cache_mb,
        )
        for item in loaded
    ]
    summaries = [item["summary"] for item in loaded if isinstance(item.get("summary"), dict)]
    queue_items = [item for summary in summaries for item in _queue_items(summary)]

    completed_pairs = sum(_int(_nested(summary, "comparison", "completed_pairs")) for summary in summaries)
    manifest_sheet_count = sheet_count if sheet_count is not None else completed_pairs
    truth_rows = _csv_data_row_count(review_ground_truth)
    audited_truth_rows = sum(_int(_nested(summary, "review_ground_truth", "rows")) for summary in summaries)
    missing_sharable_audits = _missing_sharable_audits(loaded)
    leak_count = sum(_int(_nested(summary, "sharable_audit", "leak_count")) for summary in summaries)
    detected_coverage = _detect_structural_coverage(summaries, queue_items, required)
    cad_pdf_block_evidence = _cad_pdf_block_evidence(loaded, summaries)
    ai_policy = _ai_policy_summary(summaries)
    selected_zone_performance = _selected_zone_perf_summary(
        loaded,
        max_cold_zone_render_ms=max_cold_zone_render_ms,
        max_cache_hit_zone_render_ms=max_cache_hit_zone_render_ms,
    )
    cad_policy_evidence = {
        "block_text_detection_without_expansion": _has_cad_block_text_no_expand_evidence(summaries)
    }
    workbench_acceptance = _workbench_acceptance_summary(loaded, reference_base=out_path.parent)
    operator_workflow_checks = _operator_workflow_checks_from_notes(operator_notes_file)
    review_decision_quality = summarize_review_decision_truth_csv(review_decision_truth)
    dataset_strata_summary = summarize_dataset_strata_csv(
        dataset_strata,
        expected_sheet_count=manifest_sheet_count,
    )
    first_interactive_readiness = summarize_first_interactive_readiness(summaries)
    bbox_quality = summarize_bbox_quality(summaries)
    large_dwg_resource_probe = summarize_large_dwg_resource_probe(_load_json(large_dwg_probe))
    p5_g7_forced_tile_eviction = summarize_p5_g7_forced_tile_eviction(
        p5_g7_tile_eviction_proof_dirs or [],
        expected_tile_cache_mb=p5_g6_tile_cache_mb,
        release_manifests=p5_g7_tile_eviction_release_manifests or [],
        reference_base=out_path.parent,
        required=require_p5_g7_tile_eviction_proof,
    )
    p5_g16_real_corpus_replay = summarize_p5_g16_real_corpus_replay(
        p5_g16_benchmark_json or [],
        reference_base=out_path.parent,
    )
    p5_g16_refs = [
        item["benchmark_json"]
        for item in p5_g16_real_corpus_replay["artifacts"]
        if item.get("benchmark_json")
    ]
    p5_g22_actual_gui_soak = summarize_p5_g22_actual_gui_soak(
        p5_g22_gui_soak_json or [],
        reference_base=out_path.parent,
    )
    p5_g22_refs = [
        item["benchmark_json"]
        for item in p5_g22_actual_gui_soak["artifacts"]
        if item.get("benchmark_json")
    ]
    p5_g26_selection_latency = summarize_p5_g26_selection_latency(
        p5_g26_selection_latency_json or [],
        reference_base=out_path.parent,
    )
    p5_g26_refs = [
        item["benchmark_json"]
        for item in p5_g26_selection_latency["artifacts"]
        if item.get("benchmark_json")
    ]
    p5_g27_selected_zone_crop = summarize_p5_g27_selected_zone_crop(
        p5_g27_selected_zone_crop_json or [],
        reference_base=out_path.parent,
    )
    p5_g27_refs = [
        item["benchmark_json"]
        for item in p5_g27_selected_zone_crop["artifacts"]
        if item.get("benchmark_json")
    ]
    p5_g28_cache_plateau = summarize_p5_g28_cache_plateau(
        p5_g28_cache_plateau_json or [],
        reference_base=out_path.parent,
    )
    p5_g28_refs = [
        item["benchmark_json"]
        for item in p5_g28_cache_plateau["artifacts"]
        if item.get("benchmark_json")
    ]
    evidence_level = (
        "customer_grade" if dataset_source_kind in CUSTOMER_GRADE_SOURCE_KINDS else "synthetic"
    )
    p5_g24_visual_asset_policy = summarize_p5_g24_visual_asset_policy(
        loaded,
        evidence_level=evidence_level,
        reference_base=out_path.parent,
    )

    path_audit = {
        "schema_version": 1,
        "status": "passed" if leak_count == 0 and summaries and not missing_sharable_audits else "failed",
        "leak_count": leak_count,
        "missing_sharable_audit": missing_sharable_audits,
        "sources": [
            {
                "result_dir": _manifest_ref(audit_json.parent, Path(item["path"])),
                "leak_count": _int(_nested(item.get("summary") or {}, "sharable_audit", "leak_count")),
                "audited_at": str(_nested(item.get("summary") or {}, "sharable_audit", "audited_at") or ""),
            }
            for item in loaded
        ],
    }
    audit_json.parent.mkdir(parents=True, exist_ok=True)
    audit_json.write_text(json.dumps(path_audit, ensure_ascii=True, indent=2), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "evidence_level": evidence_level,
        "dataset_id": dataset_id,
        "dataset_provenance": {
            "source_kind": dataset_source_kind,
            "source_description": dataset_source_description,
            "approval_status": dataset_approval_status,
            "approver": dataset_approver,
        },
        "validation_date": validation_date,
        "sheet_count": manifest_sheet_count,
        "ground_truth_owner": ground_truth_owner,
        "format_coverage": {
            "dwg_dxf": _has_dwg_dxf_cad_evidence(summaries),
            "pdf_pdf": _has_pdf_pdf_evidence(summaries),
            "cad_pdf_blocked": bool(cad_pdf_block_evidence),
        },
        "structural_coverage": detected_coverage,
        "ground_truth": {
            "status": ground_truth_status,
            "row_count": truth_rows,
            "review_ground_truth_csv": _manifest_ref(out_path.parent, review_ground_truth),
        },
        "review_decision_quality": {
            **review_decision_quality,
            "path": _manifest_ref(out_path.parent, review_decision_truth),
            "review_decision_truth_csv": _manifest_ref(out_path.parent, review_decision_truth),
        },
        "dataset_strata": {
            **dataset_strata_summary,
            "path": _manifest_ref(out_path.parent, dataset_strata),
            "dataset_strata_csv": _manifest_ref(out_path.parent, dataset_strata),
        },
        "first_interactive_readiness": first_interactive_readiness,
        "bbox_quality": bbox_quality,
        "large_dwg_resource_probe": {
            **large_dwg_resource_probe,
            "path": _manifest_ref(out_path.parent, large_dwg_probe),
        },
        "operator_dry_run": {
            "status": "passed",
            "reviewer_role": operator_reviewer_role,
            "confirmed_export_checked": True,
            "workflow_checks": operator_workflow_checks,
            "artifacts": {
                "notes_file": _manifest_ref(out_path.parent, operator_notes_file) if operator_notes_file else "",
                "screenshots_dir": (
                    _manifest_ref(out_path.parent, operator_screenshots_dir)
                    if operator_screenshots_dir
                    else ""
                ),
                "confirmed_export_artifact": _manifest_ref(out_path.parent, confirmed_export_artifact),
            },
            "notes": "Generated from completed validation outputs and operator dry-run artifacts.",
        },
        "path_leakage_audit": {
            "status": path_audit["status"],
            "leak_count": leak_count,
            "audit_json": _manifest_ref(out_path.parent, audit_json),
        },
        "artifacts": {
            "p5_g16_real_corpus_replay_json": p5_g16_refs[0] if p5_g16_refs else "",
            "p5_g16_real_corpus_replay_jsons": p5_g16_refs,
            "p5_g22_actual_gui_soak_json": p5_g22_refs[0] if p5_g22_refs else "",
            "p5_g22_actual_gui_soak_jsons": p5_g22_refs,
            "p5_g26_selection_latency_json": p5_g26_refs[0] if p5_g26_refs else "",
            "p5_g26_selection_latency_jsons": p5_g26_refs,
            "p5_g27_selected_zone_crop_json": p5_g27_refs[0] if p5_g27_refs else "",
            "p5_g27_selected_zone_crop_jsons": p5_g27_refs,
            "p5_g28_cache_plateau_json": p5_g28_refs[0] if p5_g28_refs else "",
            "p5_g28_cache_plateau_jsons": p5_g28_refs,
        },
        "performance_benchmarks": {
            "p5_g16_real_corpus_replay": p5_g16_real_corpus_replay,
            "p5_g22_actual_gui_soak": p5_g22_actual_gui_soak,
            "p5_g26_selection_latency": p5_g26_selection_latency,
            "p5_g27_selected_zone_crop": p5_g27_selected_zone_crop,
            "p5_g28_cache_plateau": p5_g28_cache_plateau,
        },
        "cad_policy_evidence": cad_policy_evidence,
        "selected_zone_performance": selected_zone_performance,
        "p5_g7_forced_tile_eviction": p5_g7_forced_tile_eviction,
        "p5_g24_visual_asset_policy": p5_g24_visual_asset_policy,
        "workbench_acceptance": workbench_acceptance,
        "ai_policy": ai_policy,
    }

    issues = _readiness_issues(
        manifest=manifest,
        loaded=loaded,
        required_structural_coverage=required,
        completed_pairs=completed_pairs,
        audited_truth_rows=audited_truth_rows,
        min_total_pairs=min_total_pairs,
        max_total_pairs=max_total_pairs,
        operator_notes_file=operator_notes_file,
        operator_screenshots_dir=operator_screenshots_dir,
        operator_reviewer_role=operator_reviewer_role,
        confirmed_export_artifact=confirmed_export_artifact,
        review_ground_truth=review_ground_truth,
        review_decision_truth=review_decision_truth,
        dataset_strata=dataset_strata,
        large_dwg_probe=large_dwg_probe,
        missing_sharable_audits=missing_sharable_audits,
        operator_workflow_checks=operator_workflow_checks,
        workbench_acceptance=workbench_acceptance,
        max_first_review_ready_s=max_first_review_ready_s,
        max_cold_zone_render_ms=max_cold_zone_render_ms,
        max_cache_hit_zone_render_ms=max_cache_hit_zone_render_ms,
    )
    forbidden_result_dir_proofs = [
        proof for proof in result_dir_forced_tile_eviction_proofs if proof.get("candidate") is True
    ]
    for proof in forbidden_result_dir_proofs:
        label = proof.get("result_dir") or proof.get("validation_summary") or "<results-dir>"
        issues.append(
            f"{label}: P5-G7 forced tile-eviction proof must be passed via "
            "--p5-g7-tile-eviction-proof-dir, not --results-dir"
        )
    status = "ready" if not issues else "incomplete"
    manifest["readiness"] = {
        "status": status,
        "issue_count": len(issues),
        "issues": issues,
        "warning": (
            "Do not use this manifest as final MVP completion evidence unless "
            "readiness.status is ready and the customer_grade exit audit passes."
        ),
    }
    # Plan §17 F6 (GPT Pro deep-research review) — attach manifest provenance
    # block AFTER all content fields are finalised (readiness etc.) so the
    # SHA-256 covers everything the customer-grade auditor will see. Any
    # post-write mutation of the manifest will fail the audit's
    # ``verify_manifest_integrity`` check.
    if _PROVENANCE_AVAILABLE:
        input_file_hashes: dict[str, str] = {}
        try:
            if review_ground_truth and Path(review_ground_truth).exists():
                input_file_hashes["review_ground_truth_csv"] = compute_file_sha256(
                    Path(review_ground_truth)
                )
        except Exception:
            pass
        try:
            if review_decision_truth and Path(review_decision_truth).exists():
                input_file_hashes["review_decision_truth_csv"] = compute_file_sha256(
                    Path(review_decision_truth)
                )
        except Exception:
            pass
        try:
            if dataset_strata and Path(dataset_strata).exists():
                input_file_hashes["dataset_strata_csv"] = compute_file_sha256(
                    Path(dataset_strata)
                )
        except Exception:
            pass
        try:
            if large_dwg_probe and Path(large_dwg_probe).exists():
                input_file_hashes["large_dwg_probe_json"] = compute_file_sha256(
                    Path(large_dwg_probe)
                )
        except Exception:
            pass
        try:
            if operator_notes_file and Path(operator_notes_file).exists():
                input_file_hashes["operator_notes_file"] = compute_file_sha256(
                    Path(operator_notes_file)
                )
        except Exception:
            pass
        try:
            if confirmed_export_artifact and Path(confirmed_export_artifact).exists():
                input_file_hashes["confirmed_export_artifact"] = compute_file_sha256(
                    Path(confirmed_export_artifact)
                )
        except Exception:
            pass
        for index, proof_dir in enumerate(p5_g7_tile_eviction_proof_dirs or [], start=1):
            try:
                proof_summary = Path(proof_dir) / "validation_summary.json"
                if proof_summary.exists():
                    input_file_hashes[f"p5_g7_tile_eviction_proof_{index}"] = compute_file_sha256(
                        proof_summary
                    )
            except Exception:
                pass
        for index, release_manifest in enumerate(
            p5_g7_tile_eviction_release_manifests or [],
            start=1,
        ):
            try:
                if release_manifest.exists():
                    input_file_hashes[f"p5_g7_tile_eviction_release_manifest_{index}"] = compute_file_sha256(
                        Path(release_manifest)
                    )
            except Exception:
                pass
        for index, benchmark_json in enumerate(p5_g16_benchmark_json or [], start=1):
            try:
                if benchmark_json.exists():
                    input_file_hashes[f"p5_g16_real_corpus_replay_{index}"] = compute_file_sha256(
                        Path(benchmark_json)
                    )
            except Exception:
                pass
        for index, benchmark_json in enumerate(p5_g22_gui_soak_json or [], start=1):
            try:
                if benchmark_json.exists():
                    input_file_hashes[f"p5_g22_actual_gui_soak_{index}"] = compute_file_sha256(
                        Path(benchmark_json)
                    )
            except Exception:
                pass
        for index, benchmark_json in enumerate(p5_g26_selection_latency_json or [], start=1):
            try:
                if benchmark_json.exists():
                    input_file_hashes[f"p5_g26_selection_latency_{index}"] = compute_file_sha256(
                        Path(benchmark_json)
                    )
            except Exception:
                pass
        for index, benchmark_json in enumerate(p5_g27_selected_zone_crop_json or [], start=1):
            try:
                if benchmark_json.exists():
                    input_file_hashes[f"p5_g27_selected_zone_crop_{index}"] = compute_file_sha256(
                        Path(benchmark_json)
                    )
            except Exception:
                pass
        for index, benchmark_json in enumerate(p5_g28_cache_plateau_json or [], start=1):
            try:
                if benchmark_json.exists():
                    input_file_hashes[f"p5_g28_cache_plateau_{index}"] = compute_file_sha256(
                        Path(benchmark_json)
                    )
            except Exception:
                pass
        manifest["provenance"] = build_provenance(
            manifest,
            input_file_hashes=input_file_hashes,
            tool_version=_resolve_tool_version(),
        )
    out_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
    return {
        "schema_version": 1,
        "status": status,
        "manifest": str(out_path),
        "path_leakage_audit": str(audit_json),
        "issues": issues,
        "summary": {
            "completed_pairs": completed_pairs,
            "sheet_count": manifest_sheet_count,
            "review_ground_truth_csv_rows": truth_rows,
            "audited_review_ground_truth_rows": audited_truth_rows,
            "structural_coverage": detected_coverage,
            "format_coverage": manifest["format_coverage"],
            "cad_policy_evidence": cad_policy_evidence,
            "cad_pdf_block_evidence": cad_pdf_block_evidence,
            "missing_sharable_audit": missing_sharable_audits,
            "path_leak_count": leak_count,
            "first_review_ready": _first_review_ready_summary(
                summaries,
                max_first_review_ready_s=max_first_review_ready_s,
            ),
            "selected_zone_performance": selected_zone_performance,
            "p5_g16_real_corpus_replay": p5_g16_real_corpus_replay,
            "p5_g22_actual_gui_soak": p5_g22_actual_gui_soak,
            "p5_g26_selection_latency": p5_g26_selection_latency,
            "p5_g27_selected_zone_crop": p5_g27_selected_zone_crop,
            "p5_g28_cache_plateau": p5_g28_cache_plateau,
            "p5_g24_visual_asset_policy": p5_g24_visual_asset_policy,
            "p5_g7_forced_tile_eviction": p5_g7_forced_tile_eviction,
            "p5_g7_forced_tile_eviction_results_dir_rejections": forbidden_result_dir_proofs,
            "workbench_acceptance": workbench_acceptance,
            "review_decision_quality": review_decision_quality,
            "dataset_strata": dataset_strata_summary,
            "first_interactive_readiness": first_interactive_readiness,
            "bbox_quality": bbox_quality,
            "large_dwg_resource_probe": large_dwg_resource_probe,
            "ai_policy": ai_policy,
            "sheet_count_range": {
                "min": min_total_pairs,
                "max": max_total_pairs,
            },
        },
    }


def summarize_p5_g7_forced_tile_eviction(
    proof_dirs: Sequence[Path],
    *,
    expected_tile_cache_mb: float | None = None,
    release_manifests: Sequence[Path] | None = None,
    reference_base: Path | None = None,
    required: bool = False,
) -> dict[str, Any]:
    """Summarize P5-G7 forced tile-cache eviction proof runs without corpus mixing."""
    proofs: list[dict[str, Any]] = []
    for proof_dir in proof_dirs:
        summary_path = proof_dir / "validation_summary.json"
        summary = _load_json(summary_path)
        proof = summarize_p5_g7_forced_tile_eviction_proof(
            summary if isinstance(summary, dict) else {},
            result_dir=proof_dir,
            summary_path=summary_path,
            expected_tile_cache_mb=expected_tile_cache_mb,
            reference_base=reference_base,
        )
        if proof["status"] == "not_provided":
            proof["status"] = "failed"
            proof["issues"] = [
                "validation_summary.json does not contain forced tile-eviction proof evidence"
            ]
        if not summary_path.exists():
            proof["issues"].append("validation_summary.json does not exist")
            proof["status"] = "failed"
        proofs.append(proof)

    release_summaries = [
        _summarize_p5_g7_release_manifest(
            path,
            expected_tile_cache_mb=expected_tile_cache_mb,
            reference_base=reference_base,
        )
        for path in release_manifests or []
    ]
    issues: list[str] = []
    for proof in proofs:
        if proof["status"] != "passed":
            label = proof.get("result_dir") or proof.get("validation_summary") or "<p5_g7_proof>"
            issues.append(f"{label}: " + "; ".join(proof.get("issues") or ["proof failed"]))
    for release in release_summaries:
        if release["status"] != "passed":
            label = release.get("path") or "<release_manifest>"
            issues.append(f"{label}: " + "; ".join(release.get("issues") or ["release manifest failed"]))
    passed = [proof for proof in proofs if proof["status"] == "passed"]
    if required and not passed:
        issues.append("required P5-G7 forced tile-eviction proof is missing or failed")
    if proofs:
        status = "passed" if passed and not issues else "failed"
    else:
        status = "failed" if required else "not_provided"
    return {
        "schema_version": 1,
        "status": status,
        "required": required,
        "expected_tile_cache_mb": expected_tile_cache_mb,
        "proof_count": len(proofs),
        "passed_proof_count": len(passed),
        "proofs": proofs,
        "release_manifests": release_summaries,
        "issues": issues,
    }


def summarize_p5_g16_real_corpus_replay(
    benchmark_jsons: Sequence[Path],
    *,
    reference_base: Path,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for path in benchmark_jsons:
        payload = _load_json(path)
        payload_dict = payload if isinstance(payload, dict) else {}
        summary = payload_dict.get("summary") if isinstance(payload_dict.get("summary"), dict) else {}
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
        artifacts.append(
            {
                "benchmark_json": _manifest_ref(reference_base, path),
                "exists": path.exists(),
                "status": str(payload_dict.get("status") or "planned"),
                "benchmark_id": str(payload_dict.get("benchmark_id") or ""),
                "profile": str(payload_dict.get("profile") or ""),
                "native_resource_summary": native_summary,
                "worker_tree_summary": worker_summary,
                "shared_summaries_present": bool(native_summary and worker_summary),
            }
        )
    passed = [
        item
        for item in artifacts
        if item["exists"]
        and item["status"] == "passed"
        and item["benchmark_id"] == "p5_g16_real_corpus_replay"
        and item["profile"] == "real_corpus_artifact_replay"
    ]
    status = "passed" if passed else ("planned" if artifacts else "missing")
    return {
        "schema_version": 1,
        "status": status,
        "required_for_customer_grade": True,
        "artifact_count": len(artifacts),
        "passed_count": len(passed),
        "benchmark_json": artifacts[0]["benchmark_json"] if artifacts else "",
        "benchmark_jsons": [item["benchmark_json"] for item in artifacts],
        "native_resource_summary": (
            artifacts[0]["native_resource_summary"] if artifacts else {}
        ),
        "worker_tree_summary": (
            artifacts[0]["worker_tree_summary"] if artifacts else {}
        ),
        "shared_summary_count": len(
            [item for item in artifacts if item.get("shared_summaries_present")]
        ),
        "artifacts": artifacts,
    }


def summarize_p5_g22_actual_gui_soak(
    benchmark_jsons: Sequence[Path],
    *,
    reference_base: Path,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for path in benchmark_jsons:
        payload = _load_json(path)
        payload_dict = payload if isinstance(payload, dict) else {}
        summary = payload_dict.get("summary") if isinstance(payload_dict.get("summary"), dict) else {}
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
        issues: list[str] = []
        if path.exists() and payload_dict.get("status") == "passed":
            if not native_summary:
                issues.append("summary.native_resource_summary missing")
            elif native_summary.get("measurement_available") is not True:
                issues.append("summary.native_resource_summary.measurement_available must be true")
            if not worker_summary:
                issues.append("summary.worker_tree_summary missing")
            elif worker_summary.get("cleanup_ok") is not True or _int(worker_summary.get("orphan_worker_count")) != 0:
                issues.append("summary.worker_tree_summary cleanup/orphan check failed")
        artifacts.append(
            {
                "benchmark_json": _manifest_ref(reference_base, path),
                "exists": path.exists(),
                "status": str(payload_dict.get("status") or "planned"),
                "benchmark_id": str(payload_dict.get("benchmark_id") or ""),
                "profile": str(payload_dict.get("profile") or ""),
                "native_resource_summary": native_summary,
                "worker_tree_summary": worker_summary,
                "shared_summaries_present": bool(native_summary and worker_summary),
                "issues": issues,
            }
        )
    passed = [
        item
        for item in artifacts
        if item["exists"]
        and item["status"] == "passed"
        and item["benchmark_id"] == "p5_g22_actual_gui_soak"
        and item["profile"] == "actual_gui_customer_corpus_soak"
        and item["shared_summaries_present"]
        and not item["issues"]
    ]
    status = "passed" if passed else ("failed" if any(item["exists"] for item in artifacts) else ("planned" if artifacts else "missing"))
    return {
        "schema_version": 1,
        "status": status,
        "required_for_customer_grade": True,
        "artifact_count": len(artifacts),
        "passed_count": len(passed),
        "benchmark_json": artifacts[0]["benchmark_json"] if artifacts else "",
        "benchmark_jsons": [item["benchmark_json"] for item in artifacts],
        "native_resource_summary": (
            artifacts[0]["native_resource_summary"] if artifacts else {}
        ),
        "worker_tree_summary": (
            artifacts[0]["worker_tree_summary"] if artifacts else {}
        ),
        "shared_summary_count": len(
            [item for item in artifacts if item.get("shared_summaries_present")]
        ),
        "artifacts": artifacts,
    }


def summarize_p5_g26_selection_latency(
    benchmark_jsons: Sequence[Path],
    *,
    reference_base: Path,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for path in benchmark_jsons:
        payload = _load_json(path)
        payload_dict = payload if isinstance(payload, dict) else {}
        contract = payload_dict.get("p5_g26_contract")
        if not isinstance(contract, dict):
            contract = payload_dict.get("p5_g26_evidence")
        if not isinstance(contract, dict):
            contract = {}
        gates = payload_dict.get("gates")
        gate_by_name = (
            {
                str(gate.get("name") or ""): gate
                for gate in gates
                if isinstance(gate, dict) and str(gate.get("name") or "")
            }
            if isinstance(gates, list)
            else {}
        )
        declared_required = payload_dict.get("p5_g26_required_gate_names")
        declared_required_set = {
            str(item)
            for item in declared_required
            if str(item or "")
        } if isinstance(declared_required, list) else set()
        issues: list[str] = []
        if not path.exists() or not isinstance(payload, dict):
            issues.append("p5_g26_selection_latency JSON is missing or unreadable")
        else:
            if payload_dict.get("benchmark_id") != P5_G26_BENCHMARK_ID:
                issues.append(f"benchmark_id must be {P5_G26_BENCHMARK_ID}")
            if payload_dict.get("profile") != P5_G26_PROFILE:
                issues.append(f"profile must be {P5_G26_PROFILE}")
            if payload_dict.get("status") != "passed":
                issues.append(f"status={payload_dict.get('status') or '<missing>'}")
            if not contract:
                issues.append("p5_g26_contract missing")
            else:
                if contract.get("wp_a_passed") is not True:
                    issues.append("p5_g26_contract.wp_a_passed must be true")
                if contract.get("wp_b_passed") is not True:
                    issues.append("p5_g26_contract.wp_b_passed must be true")
                if contract.get("has_zone_selection_evidence") is not True:
                    issues.append("p5_g26_contract.has_zone_selection_evidence must be true")
            if declared_required_set and not P5_G26_REQUIRED_GATES <= declared_required_set:
                missing_declared = sorted(P5_G26_REQUIRED_GATES - declared_required_set)
                issues.append("p5_g26_required_gate_names missing: " + ", ".join(missing_declared))
            missing_gates = sorted(P5_G26_REQUIRED_GATES - set(gate_by_name))
            if missing_gates:
                issues.append("required gates missing: " + ", ".join(missing_gates))
            failed_gates = sorted(
                gate_name
                for gate_name, gate in gate_by_name.items()
                if gate_name in P5_G26_REQUIRED_GATES
                and gate.get("required") is not False
                and gate.get("passed") is not True
            )
            if failed_gates:
                issues.append("required gates failed: " + ", ".join(failed_gates))
        artifacts.append(
            {
                "benchmark_json": _manifest_ref(reference_base, path),
                "exists": path.exists(),
                "status": str(payload_dict.get("status") or "planned"),
                "benchmark_id": str(payload_dict.get("benchmark_id") or ""),
                "profile": str(payload_dict.get("profile") or ""),
                "wp_a_passed": contract.get("wp_a_passed") is True,
                "wp_b_passed": contract.get("wp_b_passed") is True,
                "has_zone_selection_evidence": contract.get("has_zone_selection_evidence") is True,
                "required_gate_count": len(P5_G26_REQUIRED_GATES),
                "passed_required_gate_count": len(
                    [
                        gate
                        for name, gate in gate_by_name.items()
                        if name in P5_G26_REQUIRED_GATES
                        and gate.get("required") is not False
                        and gate.get("passed") is True
                    ]
                ),
                "issues": issues,
            }
        )
    passed = [
        item
        for item in artifacts
        if item["exists"]
        and item["status"] == "passed"
        and item["benchmark_id"] == P5_G26_BENCHMARK_ID
        and item["profile"] == P5_G26_PROFILE
        and item["wp_a_passed"]
        and item["wp_b_passed"]
        and item["has_zone_selection_evidence"]
        and item["passed_required_gate_count"] == item["required_gate_count"]
        and not item["issues"]
    ]
    status = "passed" if passed else ("failed" if any(item["exists"] for item in artifacts) else ("planned" if artifacts else "missing"))
    return {
        "schema_version": 1,
        "status": status,
        "required_for_customer_grade": True,
        "artifact_count": len(artifacts),
        "passed_count": len(passed),
        "benchmark_json": artifacts[0]["benchmark_json"] if artifacts else "",
        "benchmark_jsons": [item["benchmark_json"] for item in artifacts],
        "required_gate_count": len(P5_G26_REQUIRED_GATES),
        "artifacts": artifacts,
    }


def summarize_p5_g27_selected_zone_crop(
    benchmark_jsons: Sequence[Path],
    *,
    reference_base: Path,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for path in benchmark_jsons:
        payload = _load_json(path)
        payload_dict = payload if isinstance(payload, dict) else {}
        contract = payload_dict.get("p5_g27_contract")
        if not isinstance(contract, dict):
            contract = payload_dict.get("p5_g27_evidence")
        if not isinstance(contract, dict):
            contract = {}
        gates = payload_dict.get("gates")
        gate_by_name = (
            {
                str(gate.get("name") or ""): gate
                for gate in gates
                if isinstance(gate, dict) and str(gate.get("name") or "")
            }
            if isinstance(gates, list)
            else {}
        )
        declared_required = payload_dict.get("p5_g27_required_gate_names")
        declared_required_set = {
            str(item)
            for item in declared_required
            if str(item or "")
        } if isinstance(declared_required, list) else set()
        issues: list[str] = []
        if not path.exists() or not isinstance(payload, dict):
            issues.append("p5_g27_selected_zone_crop JSON is missing or unreadable")
        else:
            if payload_dict.get("benchmark_id") != P5_G27_BENCHMARK_ID:
                issues.append(f"benchmark_id must be {P5_G27_BENCHMARK_ID}")
            if payload_dict.get("profile") != P5_G27_PROFILE:
                issues.append(f"profile must be {P5_G27_PROFILE}")
            if payload_dict.get("status") != "passed":
                issues.append(f"status={payload_dict.get('status') or '<missing>'}")
            if not contract:
                issues.append("p5_g27_contract missing")
            else:
                if contract.get("crop_first_result_visible") is not True:
                    issues.append("p5_g27_contract.crop_first_result_visible must be true")
                if contract.get("crop_visible_before_vector_focus") is not True:
                    issues.append("p5_g27_contract.crop_visible_before_vector_focus must be true")
                if contract.get("vector_failure_does_not_clear_background") is not True:
                    issues.append(
                        "p5_g27_contract.vector_failure_does_not_clear_background must be true"
                    )
                if contract.get("has_selected_zone_crop_first_evidence") is not True:
                    issues.append(
                        "p5_g27_contract.has_selected_zone_crop_first_evidence must be true"
                    )
                if contract.get("worker_cleanup_ok") is not True:
                    issues.append("p5_g27_contract.worker_cleanup_ok must be true")
                for key in (
                    "blank_selected_zone_count",
                    "stale_result_visible_count",
                    "cancel_without_visible_regression_count",
                    "timeout_count",
                    "fallback_missing_reason_count",
                    "orphan_worker_count",
                ):
                    if _int(contract.get(key)) != 0:
                        issues.append(f"p5_g27_contract.{key} must be 0")
            if declared_required_set and not P5_G27_REQUIRED_GATES <= declared_required_set:
                missing_declared = sorted(P5_G27_REQUIRED_GATES - declared_required_set)
                issues.append("p5_g27_required_gate_names missing: " + ", ".join(missing_declared))
            missing_gates = sorted(P5_G27_REQUIRED_GATES - set(gate_by_name))
            if missing_gates:
                issues.append("required gates missing: " + ", ".join(missing_gates))
            failed_gates = sorted(
                gate_name
                for gate_name, gate in gate_by_name.items()
                if gate_name in P5_G27_REQUIRED_GATES
                and gate.get("required") is not False
                and gate.get("passed") is not True
            )
            if failed_gates:
                issues.append("required gates failed: " + ", ".join(failed_gates))
        artifacts.append(
            {
                "benchmark_json": _manifest_ref(reference_base, path),
                "exists": path.exists(),
                "status": str(payload_dict.get("status") or "planned"),
                "benchmark_id": str(payload_dict.get("benchmark_id") or ""),
                "profile": str(payload_dict.get("profile") or ""),
                "crop_first_result_visible": contract.get("crop_first_result_visible") is True,
                "crop_visible_before_vector_focus": (
                    contract.get("crop_visible_before_vector_focus") is True
                ),
                "vector_failure_does_not_clear_background": (
                    contract.get("vector_failure_does_not_clear_background") is True
                ),
                "has_selected_zone_crop_first_evidence": (
                    contract.get("has_selected_zone_crop_first_evidence") is True
                ),
                "worker_cleanup_ok": contract.get("worker_cleanup_ok") is True,
                "required_gate_count": len(P5_G27_REQUIRED_GATES),
                "passed_required_gate_count": len(
                    [
                        gate
                        for name, gate in gate_by_name.items()
                        if name in P5_G27_REQUIRED_GATES
                        and gate.get("required") is not False
                        and gate.get("passed") is True
                    ]
                ),
                "issues": issues,
            }
        )
    passed = [
        item
        for item in artifacts
        if item["exists"]
        and item["status"] == "passed"
        and item["benchmark_id"] == P5_G27_BENCHMARK_ID
        and item["profile"] == P5_G27_PROFILE
        and item["crop_first_result_visible"]
        and item["crop_visible_before_vector_focus"]
        and item["vector_failure_does_not_clear_background"]
        and item["has_selected_zone_crop_first_evidence"]
        and item["worker_cleanup_ok"]
        and item["passed_required_gate_count"] == item["required_gate_count"]
        and not item["issues"]
    ]
    status = "passed" if passed else ("failed" if any(item["exists"] for item in artifacts) else ("planned" if artifacts else "missing"))
    return {
        "schema_version": 1,
        "status": status,
        "required_for_customer_grade": True,
        "artifact_count": len(artifacts),
        "passed_count": len(passed),
        "benchmark_json": artifacts[0]["benchmark_json"] if artifacts else "",
        "benchmark_jsons": [item["benchmark_json"] for item in artifacts],
        "required_gate_count": len(P5_G27_REQUIRED_GATES),
        "artifacts": artifacts,
    }


def _p5_g28_live_cache_counter_issues(contract: dict[str, Any]) -> list[str]:
    live = contract.get("live_cache_counters")
    if not isinstance(live, dict) or live.get("supplied") is not True:
        return []
    issues: list[str] = []
    prefix = "p5_g28_contract.live_cache_counters"
    if _int(live.get("source_count")) <= 0:
        issues.append(f"{prefix}.source_count must be > 0 when supplied")
    if _int(live.get("observed_category_count")) <= 0:
        issues.append(f"{prefix}.observed_category_count must be > 0 when supplied")
    if live.get("passed") is not True:
        issues.append(f"{prefix}.passed must be true when supplied")
    if live.get("within_limits") is not True:
        issues.append(f"{prefix}.within_limits must be true when supplied")
    if _int(live.get("invalid_counter_count")) != 0:
        issues.append(f"{prefix}.invalid_counter_count must be 0 when supplied")
    min_source_count = max(1, _int(live.get("min_source_count")) or 1)
    if _int(live.get("source_count")) < min_source_count:
        issues.append(f"{prefix}.source_count must be >= min_source_count when supplied")
    if "tail_slope_ok" in live and live.get("tail_slope_ok") is not True:
        issues.append(f"{prefix}.tail_slope_ok must be true when supplied")
    if _int(live.get("tail_slope_invalid_category_count")) != 0:
        issues.append(f"{prefix}.tail_slope_invalid_category_count must be 0 when supplied")
    for issue in live.get("issues") or []:
        if str(issue or "").strip():
            issues.append(f"{prefix}.issues: {issue}")
    categories = live.get("categories")
    if not isinstance(categories, dict):
        issues.append(f"{prefix}.categories missing")
        return issues
    for category in sorted(P5_G28_CACHE_CATEGORY_NAMES):
        item = categories.get(category)
        if not isinstance(item, dict) or item.get("observed") is not True:
            continue
        category_prefix = f"{prefix}.categories.{category}"
        retained = _int(item.get("retained_bytes"))
        limit = _int(item.get("byte_limit"))
        eviction_count = _int(item.get("eviction_count"))
        evicted_bytes = _int(item.get("evicted_estimated_bytes"))
        if retained < 0 or limit < 0 or eviction_count < 0 or evicted_bytes < 0:
            issues.append(f"{category_prefix} counters must not be negative")
        if limit > 0 and retained > limit:
            issues.append(f"{category_prefix}.retained_bytes must be <= byte_limit")
        if item.get("within_limit") is not True:
            issues.append(f"{category_prefix}.within_limit must be true")
        if _int(item.get("sample_count")) >= 2:
            if item.get("tail_slope_ok") is not True:
                issues.append(f"{category_prefix}.tail_slope_ok must be true")
            if _int(item.get("tail_slope_bytes_per_run")) > _int(
                item.get("tail_slope_target_bytes_per_run")
            ):
                issues.append(
                    f"{category_prefix}.tail_slope_bytes_per_run must be <= "
                    "tail_slope_target_bytes_per_run"
                )
    return issues


def summarize_p5_g28_cache_plateau(
    benchmark_jsons: Sequence[Path],
    *,
    reference_base: Path,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for path in benchmark_jsons:
        payload = _load_json(path)
        payload_dict = payload if isinstance(payload, dict) else {}
        contract = payload_dict.get("p5_g28_contract")
        if not isinstance(contract, dict):
            contract = payload_dict.get("p5_g28_evidence")
        if not isinstance(contract, dict):
            contract = {}
        gates = payload_dict.get("gates")
        gate_by_name = (
            {
                str(gate.get("name") or ""): gate
                for gate in gates
                if isinstance(gate, dict) and str(gate.get("name") or "")
            }
            if isinstance(gates, list)
            else {}
        )
        declared_required = payload_dict.get("p5_g28_required_gate_names")
        declared_required_set = {
            str(item)
            for item in declared_required
            if str(item or "")
        } if isinstance(declared_required, list) else set()
        reasons = contract.get("eviction_reason_counts")
        reasons = reasons if isinstance(reasons, dict) else {}
        issues: list[str] = []

        if not path.exists() or not isinstance(payload, dict):
            issues.append("p5_g28_cache_plateau JSON is missing or unreadable")
        else:
            if payload_dict.get("schema_version") != "workbench-gui-hotpath-benchmark/v1":
                issues.append("schema_version must be workbench-gui-hotpath-benchmark/v1")
            if payload_dict.get("benchmark_id") != P5_G28_BENCHMARK_ID:
                issues.append(f"benchmark_id must be {P5_G28_BENCHMARK_ID}")
            if payload_dict.get("profile") != P5_G28_PROFILE:
                issues.append(f"profile must be {P5_G28_PROFILE}")
            if payload_dict.get("status") != "passed":
                issues.append(f"status={payload_dict.get('status') or '<missing>'}")
            if not contract:
                issues.append("p5_g28_contract missing")
            else:
                if contract.get("passed") is not True:
                    issues.append("p5_g28_contract.passed must be true")
                for key in (
                    "tile_retention_completed",
                    "tile_byte_plateau_ok",
                    "tile_eviction_observed",
                    "tile_byte_limit_eviction_reason_present",
                    "tile_orphan_payloads_zero",
                    "tile_stale_manifest_zero",
                    "tile_hot_pair_retained",
                    "tile_evicted_pair_cache_miss",
                    "single_entry_over_cap_zero",
                    "event_loop_over_500ms_zero",
                    "cache_category_breakdown_present",
                    "display_list_cache_plateau",
                    "dxf_index_cache_plateau",
                    "visual_asset_cache_plateau",
                    "overlay_cache_plateau",
                    "spool_namespace_plateau",
                    "cache_category_orphans_zero",
                    "cache_category_stale_entries_zero",
                    "cache_plateau_tail_slope_ok",
                ):
                    if contract.get(key) is not True:
                        issues.append(f"p5_g28_contract.{key} must be true")
                if _int(reasons.get("byte_limit")) <= 0:
                    issues.append("p5_g28_contract.eviction_reason_counts.byte_limit must be > 0")
                if _int(contract.get("tile_retained_bytes")) > _int(contract.get("tile_byte_limit")):
                    issues.append("p5_g28_contract.tile_retained_bytes must be <= tile_byte_limit")
                if _int(contract.get("tile_eviction_count")) <= 0:
                    issues.append("p5_g28_contract.tile_eviction_count must be > 0")
                if _int(contract.get("tile_evicted_estimated_bytes")) <= 0:
                    issues.append("p5_g28_contract.tile_evicted_estimated_bytes must be > 0")
                for key in (
                    "tile_orphan_bytes",
                    "tile_orphan_pair_count",
                    "tile_stale_manifest_count",
                    "single_entry_over_cap_count",
                    "event_loop_over_500ms_count",
                ):
                    if _int(contract.get(key)) != 0:
                        issues.append(f"p5_g28_contract.{key} must be 0")
                if _float(contract.get("prune_p95_ms")) > _float(contract.get("prune_p95_target_ms")):
                    issues.append("p5_g28_contract.prune_p95_ms must be <= prune_p95_target_ms")
                if _float(contract.get("event_loop_gap_p95_ms")) > _float(
                    contract.get("event_loop_gap_p95_target_ms")
                ):
                    issues.append(
                        "p5_g28_contract.event_loop_gap_p95_ms must be <= "
                        "event_loop_gap_p95_target_ms"
                    )
                breakdown = contract.get("cache_category_breakdown")
                if not isinstance(breakdown, dict):
                    issues.append("p5_g28_contract.cache_category_breakdown missing")
                    breakdown = {}
                missing_categories = sorted(P5_G28_CACHE_CATEGORY_NAMES - set(breakdown))
                if missing_categories:
                    issues.append(
                        "p5_g28_contract.cache_category_breakdown missing: "
                        + ", ".join(missing_categories)
                    )
                for category in sorted(P5_G28_CACHE_CATEGORY_NAMES):
                    item = breakdown.get(category)
                    if not isinstance(item, dict):
                        continue
                    prefix = f"p5_g28_contract.cache_category_breakdown.{category}"
                    if _int(item.get("retained_bytes")) > _int(item.get("byte_limit")):
                        issues.append(f"{prefix}.retained_bytes must be <= byte_limit")
                    if _int(item.get("evicted_entry_count")) <= 0:
                        issues.append(f"{prefix}.evicted_entry_count must be > 0")
                    if _int(item.get("orphan_bytes")) != 0:
                        issues.append(f"{prefix}.orphan_bytes must be 0")
                    if _int(item.get("orphan_entry_count")) != 0:
                        issues.append(f"{prefix}.orphan_entry_count must be 0")
                    if _int(item.get("stale_entry_count")) != 0:
                        issues.append(f"{prefix}.stale_entry_count must be 0")
                    if _int(item.get("tail_slope_bytes_per_run")) > _int(
                        item.get("tail_slope_target_bytes_per_run")
                    ):
                        issues.append(
                            f"{prefix}.tail_slope_bytes_per_run must be <= "
                            "tail_slope_target_bytes_per_run"
                        )
                issues.extend(_p5_g28_live_cache_counter_issues(contract))
            if not declared_required_set:
                issues.append("p5_g28_required_gate_names missing")
            elif not P5_G28_REQUIRED_GATES <= declared_required_set:
                missing_declared = sorted(P5_G28_REQUIRED_GATES - declared_required_set)
                issues.append("p5_g28_required_gate_names missing: " + ", ".join(missing_declared))
            if not isinstance(gates, list):
                issues.append("gates[] missing")
            else:
                missing_gates = sorted(P5_G28_REQUIRED_GATES - set(gate_by_name))
                if missing_gates:
                    issues.append("required gates missing: " + ", ".join(missing_gates))
                failed_gates = sorted(
                    gate_name
                    for gate_name, gate in gate_by_name.items()
                    if gate_name in P5_G28_REQUIRED_GATES
                    and gate.get("passed") is not True
                )
                if failed_gates:
                    issues.append("required gates failed: " + ", ".join(failed_gates))

        live_counters = (
            contract.get("live_cache_counters")
            if isinstance(contract.get("live_cache_counters"), dict)
            else {}
        )
        artifacts.append(
            {
                "benchmark_json": _manifest_ref(reference_base, path),
                "exists": path.exists(),
                "status": str(payload_dict.get("status") or "planned"),
                "benchmark_id": str(payload_dict.get("benchmark_id") or ""),
                "profile": str(payload_dict.get("profile") or ""),
                "contract_passed": contract.get("passed") is True,
                "tile_retention_completed": contract.get("tile_retention_completed") is True,
                "tile_byte_plateau_ok": contract.get("tile_byte_plateau_ok") is True,
                "tile_eviction_observed": contract.get("tile_eviction_observed") is True,
                "tile_byte_limit_eviction_reason_present": (
                    contract.get("tile_byte_limit_eviction_reason_present") is True
                ),
                "tile_orphan_payloads_zero": contract.get("tile_orphan_payloads_zero") is True,
                "tile_stale_manifest_zero": contract.get("tile_stale_manifest_zero") is True,
                "tile_hot_pair_retained": contract.get("tile_hot_pair_retained") is True,
                "tile_evicted_pair_cache_miss": contract.get("tile_evicted_pair_cache_miss") is True,
                "single_entry_over_cap_zero": contract.get("single_entry_over_cap_zero") is True,
                "event_loop_over_500ms_zero": contract.get("event_loop_over_500ms_zero") is True,
                "cache_category_breakdown_present": (
                    contract.get("cache_category_breakdown_present") is True
                ),
                "cache_category_names": list(contract.get("cache_category_names") or []),
                "cache_category_retained_bytes_total": _int(
                    contract.get("cache_category_retained_bytes_total")
                ),
                "cache_category_byte_limit_total": _int(
                    contract.get("cache_category_byte_limit_total")
                ),
                "cache_category_evicted_entry_count": _int(
                    contract.get("cache_category_evicted_entry_count")
                ),
                "cache_category_orphan_bytes_total": _int(
                    contract.get("cache_category_orphan_bytes_total")
                ),
                "cache_category_stale_entry_count": _int(
                    contract.get("cache_category_stale_entry_count")
                ),
                "cache_category_tail_slope_max_bytes_per_run": _int(
                    contract.get("cache_category_tail_slope_max_bytes_per_run")
                ),
                "live_cache_counters_supplied": (
                    contract.get("live_cache_counters_supplied") is True
                    or live_counters.get("supplied") is True
                ),
                "live_cache_counters_source_count": _int(
                    contract.get("live_cache_counters_source_count")
                    if contract.get("live_cache_counters_source_count") is not None
                    else live_counters.get("source_count")
                ),
                "live_cache_counters_observed_category_count": _int(
                    contract.get("live_cache_counters_observed_category_count")
                    if contract.get("live_cache_counters_observed_category_count") is not None
                    else live_counters.get("observed_category_count")
                ),
                "live_cache_counters_within_limits": (
                    contract.get("live_cache_counters_within_limits") is True
                    or live_counters.get("within_limits") is True
                ),
                "live_cache_counters_invalid_counter_count": _int(
                    contract.get("live_cache_counters_invalid_counter_count")
                    if contract.get("live_cache_counters_invalid_counter_count") is not None
                    else live_counters.get("invalid_counter_count")
                ),
                "live_cache_counters_tail_slope_ok": (
                    contract.get("live_cache_counters_tail_slope_ok") is True
                    or live_counters.get("tail_slope_ok") is True
                ),
                "live_cache_counters_tail_slope_max_bytes_per_run": _int(
                    contract.get("live_cache_counters_tail_slope_max_bytes_per_run")
                    if contract.get("live_cache_counters_tail_slope_max_bytes_per_run") is not None
                    else live_counters.get("tail_slope_max_bytes_per_run")
                ),
                "live_cache_counters_tail_slope_target_bytes_per_run": _int(
                    contract.get("live_cache_counters_tail_slope_target_bytes_per_run")
                    if contract.get("live_cache_counters_tail_slope_target_bytes_per_run") is not None
                    else live_counters.get("tail_slope_target_bytes_per_run")
                ),
                "live_cache_counters_tail_slope_invalid_category_count": _int(
                    contract.get("live_cache_counters_tail_slope_invalid_category_count")
                    if contract.get("live_cache_counters_tail_slope_invalid_category_count") is not None
                    else live_counters.get("tail_slope_invalid_category_count")
                ),
                "tile_retained_bytes": _int(contract.get("tile_retained_bytes")),
                "tile_byte_limit": _int(contract.get("tile_byte_limit")),
                "tile_eviction_count": _int(contract.get("tile_eviction_count")),
                "tile_evicted_estimated_bytes": _int(contract.get("tile_evicted_estimated_bytes")),
                "tile_byte_limit_eviction_reason_count": _int(reasons.get("byte_limit")),
                "tile_orphan_bytes": _int(contract.get("tile_orphan_bytes")),
                "tile_orphan_pair_count": _int(contract.get("tile_orphan_pair_count")),
                "tile_stale_manifest_count": _int(contract.get("tile_stale_manifest_count")),
                "single_entry_over_cap_count": _int(contract.get("single_entry_over_cap_count")),
                "event_loop_over_500ms_count": _int(contract.get("event_loop_over_500ms_count")),
                "prune_p95_ms": _float(contract.get("prune_p95_ms")),
                "prune_p95_target_ms": _float(contract.get("prune_p95_target_ms")),
                "event_loop_gap_p95_ms": _float(contract.get("event_loop_gap_p95_ms")),
                "event_loop_gap_p95_target_ms": _float(
                    contract.get("event_loop_gap_p95_target_ms")
                ),
                "required_gate_count": len(P5_G28_REQUIRED_GATES),
                "passed_required_gate_count": len(
                    [
                        gate
                        for name, gate in gate_by_name.items()
                        if name in P5_G28_REQUIRED_GATES
                        and gate.get("passed") is True
                    ]
                ),
                "issues": issues,
            }
        )

    passed = [
        item
        for item in artifacts
        if item["exists"]
        and item["status"] == "passed"
        and item["benchmark_id"] == P5_G28_BENCHMARK_ID
        and item["profile"] == P5_G28_PROFILE
        and item["contract_passed"]
        and item["passed_required_gate_count"] == item["required_gate_count"]
        and not item["issues"]
    ]
    if not artifacts:
        status = "missing"
    elif not any(item["exists"] for item in artifacts):
        status = "planned"
    elif len(passed) == len(artifacts):
        status = "passed"
    else:
        status = "failed"
    return {
        "schema_version": 1,
        "status": status,
        "required_for_customer_grade": False,
        "artifact_count": len(artifacts),
        "passed_count": len(passed),
        "benchmark_json": artifacts[0]["benchmark_json"] if artifacts else "",
        "benchmark_jsons": [item["benchmark_json"] for item in artifacts],
        "required_gate_count": len(P5_G28_REQUIRED_GATES),
        "artifacts": artifacts,
    }


def summarize_p5_g24_visual_asset_policy(
    loaded: Sequence[dict[str, Any]],
    *,
    evidence_level: str,
    reference_base: Path | None = None,
) -> dict[str, Any]:
    completed_output_count = 0
    outputs_with_manifests = 0
    manifest_count = 0
    issues: list[str] = []
    evidence: list[str] = []

    for item in loaded:
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_output_count += 1
        result_root = item.get("path")
        label = (
            _manifest_ref(reference_base, result_root)
            if reference_base and isinstance(result_root, Path)
            else str(result_root or "<validation_output>")
        )
        viewer_manifest_path = _p5_g24_viewer_manifest_path_for_loaded_item(item)
        if viewer_manifest_path is None:
            issues.append(f"{label}: viewer_manifest_json missing or unreadable")
            evidence.append(f"{label}: visual_asset_manifests=missing_viewer_manifest")
            continue
        viewer_manifest = _load_json(viewer_manifest_path)
        if not isinstance(viewer_manifest, dict):
            issues.append(f"{viewer_manifest_path}: viewer manifest JSON missing or unreadable")
            evidence.append(f"{label}: visual_asset_manifests=missing_viewer_manifest")
            continue
        manifest_refs = _visual_asset_manifest_refs(viewer_manifest)
        if not manifest_refs:
            issues.append(f"{viewer_manifest_path}: no visual asset manifest references")
            evidence.append(f"{label}: visual_asset_manifests=0")
            continue
        outputs_with_manifests += 1
        evidence.append(
            f"{label}: visual_asset_manifests={len(manifest_refs)}, "
            f"viewer_manifest={_manifest_ref(reference_base, viewer_manifest_path) if reference_base else str(viewer_manifest_path)}"
        )
        for ref in manifest_refs:
            manifest_path = _resolve_viewer_manifest_ref(
                ref,
                result_root=result_root,
                viewer_root=viewer_manifest_path.parent,
            )
            manifest_count += 1
            if manifest_path is None:
                issues.append(f"{viewer_manifest_path}: visual asset manifest not found: {ref}")
                continue
            payload = _load_json(manifest_path)
            if not isinstance(payload, dict):
                issues.append(f"{manifest_path}: visual asset manifest JSON missing or unreadable")
                continue
            policy_issues = _visual_asset_policy_issues(
                payload,
                customer_grade=(evidence_level == "customer_grade"),
                manifest_path=manifest_path,
            )
            if policy_issues:
                manifest_label = (
                    _manifest_ref(reference_base, manifest_path)
                    if reference_base
                    else str(manifest_path)
                )
                issues.append(f"{manifest_label}: " + "; ".join(policy_issues))

    if evidence_level == "customer_grade":
        if completed_output_count <= 0:
            issues.append("customer_grade visual asset policy requires completed validation outputs")
        if outputs_with_manifests < completed_output_count:
            issues.append("customer_grade visual asset manifests must be present for every completed validation output")

    status = "passed" if completed_output_count > 0 and manifest_count > 0 and not issues else "failed"
    if evidence_level != "customer_grade" and completed_output_count <= 0:
        status = "advisory"
    return {
        "schema_version": 1,
        "status": status,
        "required_for_customer_grade": evidence_level == "customer_grade",
        "completed_output_count": completed_output_count,
        "outputs_with_manifests": outputs_with_manifests,
        "manifest_count": manifest_count,
        "issues": issues,
        "evidence": evidence[:20],
    }


def _p5_g24_viewer_manifest_path_for_loaded_item(item: dict[str, Any]) -> Path | None:
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    root = item.get("path")
    for value in (
        _nested(summary, "outputs", "viewer_manifest_json"),
        _nested(summary, "viewer_package", "viewer_manifest"),
    ):
        text = str(value or "").strip()
        if not text:
            continue
        path = Path(text)
        candidates = [path] if path.is_absolute() else ([root / path] if isinstance(root, Path) else [])
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return None


def summarize_p5_g7_forced_tile_eviction_proof(
    summary: dict[str, Any],
    *,
    result_dir: Path | None = None,
    summary_path: Path | None = None,
    expected_tile_cache_mb: float | None = None,
    reference_base: Path | None = None,
    strict_candidate: bool = True,
) -> dict[str, Any]:
    gate = summary.get("p5_g3_realset_gate") if isinstance(summary, dict) else None
    gate = gate if isinstance(gate, dict) else {}
    gate_evidence = gate.get("evidence") if isinstance(gate.get("evidence"), dict) else {}
    tile = gate_evidence.get("tile_manifest") if isinstance(gate_evidence, dict) else None
    tile = tile if isinstance(tile, dict) else {}

    configured_mb = _optional_float(tile.get("configured_tile_cache_mb"))
    env_mb = _optional_float(tile.get("tile_cache_env_mb"))
    byte_limit = _int(tile.get("byte_limit"))
    evicted_pair_count = _int(tile.get("evicted_pair_count"))
    evicted_estimated_bytes = _int(tile.get("evicted_estimated_bytes"))
    min_evicted_pairs = max(1, _int(tile.get("min_evicted_pair_count")) or 1)
    min_evicted_bytes = max(1, _int(tile.get("min_evicted_estimated_bytes")) or 1)
    require_eviction = tile.get("require_eviction") is True
    requested = gate.get("requested") is True
    forced_markers = requested and (
        require_eviction
        if strict_candidate
        else (
            require_eviction
            or configured_mb is not None
            or env_mb is not None
            or evicted_pair_count > 0
            or evicted_estimated_bytes > 0
        )
    )
    if not forced_markers:
        return {
            "status": "not_provided",
            "candidate": False,
            "result_dir": _manifest_ref(reference_base, result_dir) if reference_base else str(result_dir or ""),
            "validation_summary": (
                _manifest_ref(reference_base, summary_path)
                if reference_base
                else str(summary_path or "")
            ),
            "issues": [],
        }

    issues: list[str] = []
    gate_status = str(gate.get("status") or "")
    tile_status = str(tile.get("status") or "")
    if not requested:
        issues.append("p5_g3_realset_gate.requested is not true")
    if gate_status != "passed":
        issues.append(f"p5_g3_realset_gate.status={gate_status or '<missing>'}")
    if tile_status and tile_status != "passed":
        issues.append(f"p5_g3_realset_gate.tile_manifest.status={tile_status}")
    if not require_eviction:
        issues.append("p5_g3_realset_gate.tile_manifest.require_eviction is not true")
    if evicted_pair_count < min_evicted_pairs:
        issues.append(
            "p5_g3_realset_gate.tile_manifest.evicted_pair_count="
            f"{evicted_pair_count} < {min_evicted_pairs}"
        )
    if evicted_estimated_bytes < min_evicted_bytes:
        issues.append(
            "p5_g3_realset_gate.tile_manifest.evicted_estimated_bytes="
            f"{evicted_estimated_bytes} < {min_evicted_bytes}"
        )
    if configured_mb is None or configured_mb <= 0:
        issues.append("p5_g3_realset_gate.tile_manifest.configured_tile_cache_mb missing")
    if env_mb is None or env_mb <= 0:
        issues.append("p5_g3_realset_gate.tile_manifest.tile_cache_env_mb missing")
    if configured_mb is not None and env_mb is not None and not _float_close(env_mb, configured_mb):
        issues.append(
            "p5_g3_realset_gate.tile_manifest.tile_cache_env_mb="
            f"{env_mb} != configured_tile_cache_mb={configured_mb}"
        )
    if byte_limit <= 0:
        issues.append("p5_g3_realset_gate.tile_manifest.byte_limit missing")
    elif configured_mb is not None:
        configured_bytes = int(configured_mb * 1024 * 1024)
        if abs(byte_limit - configured_bytes) > 1:
            issues.append(
                f"p5_g3_realset_gate.tile_manifest.byte_limit={byte_limit} != {configured_bytes}"
            )
    if expected_tile_cache_mb is not None:
        expected_bytes = int(float(expected_tile_cache_mb) * 1024 * 1024)
        if not _float_close(configured_mb, expected_tile_cache_mb):
            issues.append(
                "p5_g3_realset_gate.tile_manifest.configured_tile_cache_mb="
                f"{configured_mb} != {expected_tile_cache_mb}"
            )
        if not _float_close(env_mb, expected_tile_cache_mb):
            issues.append(
                "p5_g3_realset_gate.tile_manifest.tile_cache_env_mb="
                f"{env_mb} != {expected_tile_cache_mb}"
            )
        if byte_limit <= 0:
            issues.append(
                "p5_g3_realset_gate.tile_manifest.byte_limit missing for "
                f"expected tile cache cap {expected_tile_cache_mb} MB"
            )
        elif abs(byte_limit - expected_bytes) > 1:
            issues.append(
                f"p5_g3_realset_gate.tile_manifest.byte_limit={byte_limit} != {expected_bytes}"
            )

    stale_manifest_count = _int(tile.get("stale_manifest_count"))
    missing_pair_payload_count = _int(tile.get("missing_pair_payload_count"))
    retained_estimated_bytes = _int(tile.get("retained_estimated_bytes"))
    orphan_payload_bytes = _int(tile.get("orphan_payload_bytes"))
    max_orphan_payload_bytes = _int(tile.get("max_orphan_payload_bytes"))
    if stale_manifest_count:
        issues.append(f"p5_g3_realset_gate.tile_manifest.stale_manifest_count={stale_manifest_count}")
    if missing_pair_payload_count:
        issues.append(
            "p5_g3_realset_gate.tile_manifest.missing_pair_payload_count="
            f"{missing_pair_payload_count}"
        )
    if byte_limit > 0 and retained_estimated_bytes > byte_limit:
        issues.append(
            "p5_g3_realset_gate.tile_manifest.retained_estimated_bytes="
            f"{retained_estimated_bytes} > byte_limit={byte_limit}"
        )
    if orphan_payload_bytes > max_orphan_payload_bytes:
        issues.append(
            "p5_g3_realset_gate.tile_manifest.orphan_payload_bytes="
            f"{orphan_payload_bytes} > {max_orphan_payload_bytes}"
        )

    return {
        "status": "passed" if not issues else "failed",
        "candidate": True,
        "result_dir": _manifest_ref(reference_base, result_dir) if reference_base else str(result_dir or ""),
        "validation_summary": (
            _manifest_ref(reference_base, summary_path)
            if reference_base
            else str(summary_path or "")
        ),
        "p5_g3_realset_gate_status": gate_status,
        "requested": requested,
        "tile_manifest_status": tile_status,
        "require_eviction": require_eviction,
        "configured_tile_cache_mb": configured_mb,
        "tile_cache_env_mb": env_mb,
        "byte_limit": byte_limit,
        "evicted_pair_count": evicted_pair_count,
        "min_evicted_pair_count": min_evicted_pairs,
        "evicted_estimated_bytes": evicted_estimated_bytes,
        "min_evicted_estimated_bytes": min_evicted_bytes,
        "retained_estimated_bytes": retained_estimated_bytes,
        "stale_manifest_count": stale_manifest_count,
        "missing_pair_payload_count": missing_pair_payload_count,
        "orphan_payload_bytes": orphan_payload_bytes,
        "max_orphan_payload_bytes": max_orphan_payload_bytes,
        "issues": issues,
    }


def _summarize_p5_g7_release_manifest(
    path: Path,
    *,
    expected_tile_cache_mb: float | None,
    reference_base: Path | None,
) -> dict[str, Any]:
    payload = _load_json(path)
    issues: list[str] = []
    env_values: list[float] = []
    if not path.exists():
        issues.append("release_manifest.json does not exist")
    if not isinstance(payload, dict):
        issues.append("release_manifest.json is missing or unreadable")
        payload = {}
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        env_overrides = step.get("env_overrides")
        if not isinstance(env_overrides, dict):
            continue
        value = _optional_float(env_overrides.get(TILE_CACHE_MB_ENV_VAR))
        if value is not None:
            env_values.append(value)
    if expected_tile_cache_mb is not None:
        if not env_values:
            issues.append(f"release manifest missing {TILE_CACHE_MB_ENV_VAR} env override")
        for value in env_values:
            if not _float_close(value, expected_tile_cache_mb):
                issues.append(
                    f"release manifest {TILE_CACHE_MB_ENV_VAR}={value} != {expected_tile_cache_mb}"
                )
    return {
        "status": "passed" if not issues else "failed",
        "path": _manifest_ref(reference_base, path) if reference_base else str(path),
        "tile_cache_env_var": TILE_CACHE_MB_ENV_VAR,
        "tile_cache_env_mb_values": env_values,
        "issues": issues,
    }


def _readiness_issues(
    *,
    manifest: dict[str, Any],
    loaded: Sequence[dict[str, Any]],
    required_structural_coverage: Sequence[str],
    completed_pairs: int,
    audited_truth_rows: int,
    min_total_pairs: int,
    max_total_pairs: int,
    operator_notes_file: Path | None,
    operator_screenshots_dir: Path | None,
    operator_reviewer_role: str,
    confirmed_export_artifact: Path,
    review_ground_truth: Path,
    review_decision_truth: Path,
    dataset_strata: Path,
    large_dwg_probe: Path,
    missing_sharable_audits: Sequence[str],
    operator_workflow_checks: Sequence[str],
    workbench_acceptance: dict[str, Any],
    max_first_review_ready_s: float,
    max_cold_zone_render_ms: float,
    max_cache_hit_zone_render_ms: float,
) -> list[str]:
    issues: list[str] = []
    if not loaded or any(not isinstance(item.get("summary"), dict) for item in loaded):
        issues.append("all --results-dir values must contain readable validation_summary.json")
    provenance = manifest.get("dataset_provenance") or {}
    if provenance.get("source_kind") not in CUSTOMER_GRADE_SOURCE_KINDS:
        issues.append("dataset_provenance.source_kind must be customer or customer_grade")
    if not str(provenance.get("source_description") or "").strip():
        issues.append("dataset_provenance.source_description is required")
    if provenance.get("approval_status") != APPROVED_DATASET_STATUS:
        issues.append(f"dataset_provenance.approval_status must be {APPROVED_DATASET_STATUS}")
    if not str(provenance.get("approver") or "").strip():
        issues.append("dataset_provenance.approver is required")
    if _int(manifest.get("sheet_count")) < min_total_pairs:
        issues.append(f"sheet_count must be >= {min_total_pairs}")
    if _int(manifest.get("sheet_count")) > max_total_pairs:
        issues.append(f"sheet_count must be <= {max_total_pairs}")
    if _int(manifest.get("sheet_count")) > completed_pairs:
        issues.append("sheet_count cannot exceed audited completed_pairs")
    first_review_failures = _first_review_ready_failures(
        [item["summary"] for item in loaded if isinstance(item.get("summary"), dict)],
        max_first_review_ready_s=max_first_review_ready_s,
    )
    issues.extend(first_review_failures)
    selected_zone_failures = _selected_zone_perf_failures(
        loaded,
        max_cold_zone_render_ms=max_cold_zone_render_ms,
        max_cache_hit_zone_render_ms=max_cache_hit_zone_render_ms,
    )
    issues.extend(selected_zone_failures)
    if _nested(manifest, "ai_policy", "status") != "passed":
        issues.append("ai_policy must show optional AI with missing-model heuristic fallback")
    for key, value in (manifest.get("format_coverage") or {}).items():
        if value is not True:
            issues.append(f"format_coverage.{key} is missing from audited outputs")
    missing = sorted(set(required_structural_coverage) - set(_string_list(manifest.get("structural_coverage"))))
    if missing:
        issues.append(f"structural_coverage missing {missing}")
    if _nested(manifest, "cad_policy_evidence", "block_text_detection_without_expansion") is not True:
        issues.append(
            "cad_policy_evidence.block_text_detection_without_expansion is missing from audited outputs"
        )
    truth_rows = _int(_nested(manifest, "ground_truth", "row_count"))
    if _nested(manifest, "ground_truth", "status") != "approved":
        issues.append("ground_truth.status must be approved")
    if not review_ground_truth.exists() or truth_rows <= 0:
        issues.append("review_ground_truth CSV must exist and contain data rows")
    else:
        issues.extend(review_ground_truth_csv_issues(review_ground_truth))
    if _is_template_or_handoff_evidence(review_ground_truth):
        issues.append(
            "review_ground_truth CSV must be a customer-approved evidence artifact, "
            "not a template or handoff document"
        )
    if truth_rows > audited_truth_rows:
        issues.append("review_ground_truth CSV rows exceed audited review_ground_truth rows")
    review_decision_quality = manifest.get("review_decision_quality") or {}
    if not review_decision_truth.exists():
        issues.append("review_decision_truth CSV must exist")
    else:
        issues.extend(review_decision_truth_csv_issues(review_decision_truth))
    if _is_template_or_handoff_evidence(review_decision_truth):
        issues.append(
            "review_decision_truth CSV must be a customer-approved evidence artifact, "
            "not a template or handoff document"
        )
    if review_decision_quality.get("status") != "passed":
        issues.append("review_decision_quality.status must be passed")
    dataset_strata_summary = manifest.get("dataset_strata") or {}
    if not dataset_strata.exists():
        issues.append("dataset_strata CSV must exist")
    else:
        issues.extend(dataset_strata_csv_issues(dataset_strata, expected_sheet_count=_int(manifest.get("sheet_count"))))
    if _is_template_or_handoff_evidence(dataset_strata):
        issues.append(
            "dataset_strata CSV must be a customer-approved evidence artifact, "
            "not a template or handoff document"
        )
    if dataset_strata_summary.get("status") != "passed":
        issues.append("dataset_strata.status must be passed")
    if _nested(manifest, "first_interactive_readiness", "status") != "passed":
        issues.append("first_interactive_readiness.status must be passed")
    if _nested(manifest, "bbox_quality", "status") != "passed":
        issues.append("bbox_quality.status must be passed")
    if not large_dwg_probe.exists():
        issues.append("large_dwg_probe JSON must exist")
    if _nested(manifest, "large_dwg_resource_probe", "status") != "passed":
        issues.append("large_dwg_resource_probe.status must be passed")
    p5_g7_tile_eviction = manifest.get("p5_g7_forced_tile_eviction")
    if (
        isinstance(p5_g7_tile_eviction, dict)
        and p5_g7_tile_eviction.get("required") is True
        and p5_g7_tile_eviction.get("status") != "passed"
    ):
        issues.append("p5_g7_forced_tile_eviction.status must be passed when required")
    p5_g24 = manifest.get("p5_g24_visual_asset_policy")
    if manifest.get("evidence_level") == "customer_grade":
        if not isinstance(p5_g24, dict) or p5_g24.get("status") != "passed":
            issues.append("p5_g24_visual_asset_policy.status must be passed for customer-grade visual asset evidence")
            if isinstance(p5_g24, dict):
                issues.extend(str(issue) for issue in p5_g24.get("issues", []) if str(issue))
    p5_g22 = _nested(manifest, "performance_benchmarks", "p5_g22_actual_gui_soak")
    if isinstance(p5_g22, dict) and _int(p5_g22.get("artifact_count")) > 0:
        if p5_g22.get("status") != "passed":
            issues.append("p5_g22_actual_gui_soak.status must be passed when provided")
        if _int(p5_g22.get("shared_summary_count")) < _int(p5_g22.get("artifact_count")):
            issues.append(
                "p5_g22_actual_gui_soak shared native/worker summaries are required for all provided artifacts"
            )
        native_summary = p5_g22.get("native_resource_summary") if isinstance(p5_g22.get("native_resource_summary"), dict) else {}
        worker_summary = p5_g22.get("worker_tree_summary") if isinstance(p5_g22.get("worker_tree_summary"), dict) else {}
        if native_summary.get("measurement_available") is not True:
            issues.append("p5_g22_actual_gui_soak.native_resource_summary.measurement_available must be true")
        if worker_summary.get("cleanup_ok") is not True or _int(worker_summary.get("orphan_worker_count")) != 0:
            issues.append("p5_g22_actual_gui_soak.worker_tree_summary cleanup/orphan check must pass")
    p5_g26 = _nested(manifest, "performance_benchmarks", "p5_g26_selection_latency")
    if isinstance(p5_g26, dict) and _int(p5_g26.get("artifact_count")) > 0:
        if p5_g26.get("status") != "passed":
            issues.append("p5_g26_selection_latency.status must be passed when provided")
            for item in p5_g26.get("artifacts", []):
                if isinstance(item, dict):
                    issues.extend(str(issue) for issue in item.get("issues", []) if str(issue))
    p5_g27 = _nested(manifest, "performance_benchmarks", "p5_g27_selected_zone_crop")
    if isinstance(p5_g27, dict) and _int(p5_g27.get("artifact_count")) > 0:
        if p5_g27.get("status") != "passed":
            issues.append("p5_g27_selected_zone_crop.status must be passed when provided")
            for item in p5_g27.get("artifacts", []):
                if isinstance(item, dict):
                    issues.extend(str(issue) for issue in item.get("issues", []) if str(issue))
    p5_g28 = _nested(manifest, "performance_benchmarks", "p5_g28_cache_plateau")
    if isinstance(p5_g28, dict) and _int(p5_g28.get("artifact_count")) > 0:
        if p5_g28.get("status") != "passed":
            issues.append("p5_g28_cache_plateau.status must be passed when provided")
            for item in p5_g28.get("artifacts", []):
                if isinstance(item, dict):
                    issues.extend(str(issue) for issue in item.get("issues", []) if str(issue))
    if not operator_notes_file:
        issues.append("operator notes_file with workflow checklist is required")
    if not operator_notes_file and not operator_screenshots_dir:
        issues.append("operator notes_file or screenshots_dir is required")
    if operator_notes_file and not operator_notes_file.exists():
        issues.append(f"operator notes file not found: {operator_notes_file}")
    if operator_notes_file and _is_template_or_handoff_evidence(operator_notes_file):
        issues.append(
            "operator notes file must be a completed operator dry-run artifact, "
            "not a template or handoff document"
        )
    if (
        operator_notes_file
        and operator_notes_file.exists()
        and not _is_template_or_handoff_evidence(operator_notes_file)
        and not operator_notes_have_substantive_review_notes(operator_notes_file)
    ):
        issues.append(
            "operator notes file must include substantive dry-run review notes "
            "beyond role and checklist"
        )
    if _normalize_operator_role(operator_reviewer_role) not in APPROVED_OPERATOR_REVIEWER_ROLES:
        issues.append(
            "operator reviewer role must be a structural review lead/team lead role "
            f"({sorted(APPROVED_OPERATOR_REVIEWER_ROLES)})"
        )
    elif operator_notes_file and not _operator_notes_has_reviewer_role(operator_notes_file, operator_reviewer_role):
        issues.append(
            "operator notes file must include matching structural reviewer role "
            f"({operator_reviewer_role})"
        )
    if operator_screenshots_dir and not operator_screenshots_dir.exists():
        issues.append(f"operator screenshots dir not found: {operator_screenshots_dir}")
    missing_workflow = sorted(set(REQUIRED_OPERATOR_WORKFLOW_CHECKS) - set(operator_workflow_checks))
    if missing_workflow:
        issues.append(f"operator workflow checklist missing {missing_workflow}")
    if workbench_acceptance.get("status") != "passed":
        issues.append(
            "workbench_acceptance_summary.json with passed items 5/8/8b/9b/9c/10 is required "
            "for customer-grade confirmed/false_positive/hold workflow evidence"
        )
    if not confirmed_export_artifact.exists():
        issues.append(f"confirmed export artifact not found: {confirmed_export_artifact}")
    elif not _is_audited_confirmed_export_artifact(confirmed_export_artifact, loaded):
        issues.append(
            "confirmed export artifact must be a *_confirmed.* file (.png/.pdf/.dxf) under an audited validation output"
        )
    if missing_sharable_audits:
        issues.append(
            "all validation outputs must include sharable_audit.leak_count evidence; "
            f"missing={list(missing_sharable_audits)}"
        )
    if _int(_nested(manifest, "path_leakage_audit", "leak_count")) != 0:
        issues.append("path leakage audit must report leak_count=0")
    return issues


def _load_result_dir(path: Path) -> dict[str, Any]:
    return {
        "path": path,
        "summary": _load_json(path / "validation_summary.json"),
    }


def _first_review_ready_summary(
    summaries: Sequence[dict[str, Any]],
    *,
    max_first_review_ready_s: float,
) -> dict[str, Any]:
    completed = [
        summary
        for summary in summaries
        if _int(_nested(summary, "comparison", "completed_pairs")) > 0
    ]
    totals = [
        _float(_nested(summary, "timings", "total_s"))
        for summary in completed
        if _float(_nested(summary, "timings", "total_s")) > 0
    ]
    return {
        "status": "passed" if not _first_review_ready_failures(
            summaries,
            max_first_review_ready_s=max_first_review_ready_s,
        ) else "failed",
        "max_first_review_ready_s": max_first_review_ready_s,
        "completed_outputs": len(completed),
        "timed_outputs": len(totals),
        "max_total_s": max(totals) if totals else 0.0,
    }


def _first_review_ready_failures(
    summaries: Sequence[dict[str, Any]],
    *,
    max_first_review_ready_s: float,
) -> list[str]:
    failures: list[str] = []
    completed_count = 0
    for summary in summaries:
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_count += 1
        label = str(summary.get("output_dir") or "<validation_summary>")
        total_s = _float(_nested(summary, "timings", "total_s"))
        review_dashboard_path = str(_nested(summary, "outputs", "review_dashboard_json") or "").strip()
        viewer_manifest_path = str(_nested(summary, "outputs", "viewer_manifest_json") or "").strip()
        review_queue = _nested(summary, "review_dashboard", "review_queue")
        queue_mode = str(_nested(summary, "review_dashboard", "review_queue", "mode") or "").strip()
        top_per_drawing = _int(_nested(summary, "review_dashboard", "review_queue", "top_per_drawing"))
        queue_items = _nested(summary, "review_dashboard", "review_queue", "items")
        item_count = len(queue_items) if isinstance(queue_items, list) else 0
        if total_s <= 0:
            failures.append(f"{label}: missing timings.total_s for first review-ready evidence")
        elif total_s > max_first_review_ready_s:
            failures.append(f"{label}: first review-ready total_s={total_s} exceeds {max_first_review_ready_s}")
        if not (review_dashboard_path and isinstance(review_queue, dict) and item_count > 0):
            failures.append(f"{label}: missing review_dashboard/review_queue first-screen evidence")
        if queue_mode != "structural_core":
            failures.append(f"{label}: review_queue.mode must be structural_core")
        if not (3 <= top_per_drawing <= 5):
            failures.append(f"{label}: review_queue.top_per_drawing must be 3..5")
        if not (viewer_manifest_path or _nested(summary, "viewer_package", "viewer_manifest")):
            failures.append(f"{label}: missing viewer metadata evidence")
    if completed_count <= 0:
        failures.append("at least one completed validation output is required for first review-ready evidence")
    return failures


def _selected_zone_perf_summary(
    loaded: Sequence[dict[str, Any]],
    *,
    max_cold_zone_render_ms: float,
    max_cache_hit_zone_render_ms: float,
) -> dict[str, Any]:
    completed_outputs = 0
    telemetry_outputs = 0
    cold_values: list[float] = []
    hit_values: list[float] = []
    for item in loaded:
        summary = item.get("summary") or {}
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_outputs += 1
        perf = _selected_zone_perf_for_item(item)
        zone_count = _int(perf.get("zone_crop_count")) if isinstance(perf, dict) else 0
        if zone_count <= 0:
            continue
        telemetry_outputs += 1
        cold_values.append(_float(_nested(perf, "zone_crop_cold_ms", "p95")))
        hit_values.append(_float(_nested(perf, "zone_crop_cache_hit_ms", "p95")))
    return {
        "status": "passed"
        if not _selected_zone_perf_failures(
            loaded,
            max_cold_zone_render_ms=max_cold_zone_render_ms,
            max_cache_hit_zone_render_ms=max_cache_hit_zone_render_ms,
        )
        else "failed",
        "completed_outputs": completed_outputs,
        "telemetry_outputs": telemetry_outputs,
        "max_cold_zone_render_ms": max_cold_zone_render_ms,
        "max_cache_hit_zone_render_ms": max_cache_hit_zone_render_ms,
        "max_cold_p95_ms": max(cold_values) if cold_values else 0.0,
        "max_cache_hit_p95_ms": max(hit_values) if hit_values else 0.0,
    }


def _selected_zone_perf_failures(
    loaded: Sequence[dict[str, Any]],
    *,
    max_cold_zone_render_ms: float,
    max_cache_hit_zone_render_ms: float,
) -> list[str]:
    failures: list[str] = []
    completed_count = 0
    telemetry_count = 0
    for item in loaded:
        summary = item.get("summary") or {}
        completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
        if completed_pairs <= 0:
            continue
        completed_count += 1
        label = str(item.get("path") or summary.get("output_dir") or "<validation_summary>")
        perf = _selected_zone_perf_for_item(item)
        zone_count = _int(perf.get("zone_crop_count")) if isinstance(perf, dict) else 0
        if zone_count <= 0:
            failures.append(f"{label}: missing selected-zone telemetry")
            continue
        telemetry_count += 1
        cold_p95 = _float(_nested(perf, "zone_crop_cold_ms", "p95"))
        hit_p95 = _float(_nested(perf, "zone_crop_cache_hit_ms", "p95"))
        if cold_p95 and cold_p95 > max_cold_zone_render_ms:
            failures.append(f"{label}: selected-zone cold_p95={cold_p95} exceeds {max_cold_zone_render_ms}")
        if hit_p95 and hit_p95 > max_cache_hit_zone_render_ms:
            failures.append(f"{label}: selected-zone cache_hit_p95={hit_p95} exceeds {max_cache_hit_zone_render_ms}")
    if completed_count <= 0:
        failures.append("at least one completed validation output is required for selected-zone telemetry")
    elif telemetry_count < completed_count:
        failures.append(
            f"selected-zone telemetry must exist for every completed output ({telemetry_count}/{completed_count})"
        )
    return failures


def _selected_zone_perf_for_item(item: dict[str, Any]) -> dict[str, Any]:
    summary = item.get("summary") or {}
    perf = summary.get("viewer_perf_summary")
    if isinstance(perf, dict) and _int(perf.get("zone_crop_count")) > 0:
        return perf
    root = item.get("path")
    if isinstance(root, Path):
        return summarize_viewer_perf(root / "viewer")
    return {}


def _workbench_acceptance_summary(loaded: Sequence[dict[str, Any]], *, reference_base: Path) -> dict[str, Any]:
    summary_files: list[str] = []
    failures: list[str] = []
    passed_files: list[str] = []
    for item in loaded:
        root = item.get("path")
        if not isinstance(root, Path):
            continue
        for summary_path in root.rglob("workbench_acceptance_summary.json"):
            if not summary_path.is_file():
                continue
            summary_ref = _manifest_ref(reference_base, summary_path)
            summary_files.append(summary_ref)
            passed, detail = _workbench_acceptance_required_items_passed(summary_path)
            if passed:
                passed_files.append(summary_ref)
            else:
                failures.append(f"{summary_ref}: {detail}")
    return {
        "status": "passed" if passed_files and not failures else "failed",
        "summary_count": len(summary_files),
        "passed_summary_count": len(passed_files),
        "required_items": list(REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS),
        "summaries": summary_files,
        "failures": failures,
    }


def _workbench_acceptance_required_items_passed(summary_path: Path) -> tuple[bool, str]:
    payload = _load_json(summary_path)
    if not isinstance(payload, dict):
        return False, "summary JSON is missing or unreadable"
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return False, "summary checks[] is missing"
    required = tuple(REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS)
    found: dict[str, bool] = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name") or "")
        for prefix in required:
            if name.startswith(prefix):
                found[prefix] = bool(check.get("passed"))
    missing_or_failed = [prefix for prefix in required if found.get(prefix) is not True]
    if missing_or_failed:
        return False, f"missing_or_failed={missing_or_failed}"
    return True, "required Workbench acceptance items passed"


def _ai_policy_summary(summaries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    completed = [
        summary
        for summary in summaries
        if _int(_nested(summary, "comparison", "completed_pairs")) > 0
    ]
    passed: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    for summary in completed:
        label = str(summary.get("output_dir") or "<validation_summary>")
        policy = summary.get("ai_policy")
        if not isinstance(policy, dict):
            missing.append(label)
            continue
        if _ai_policy_passed(policy):
            passed.append(label)
        else:
            failed.append(label)
    return {
        "status": "passed" if completed and passed and not missing and not failed else "failed",
        "completed_outputs": len(completed),
        "passed_outputs": len(passed),
        "missing_outputs": len(missing),
        "failed_outputs": failed[:10],
        "policy": "AI embedding/LLM are optional; missing models must warn and keep heuristic classification active.",
    }


def _ai_policy_passed(policy: dict[str, Any]) -> bool:
    fallback = policy.get("fallback_without_model")
    heuristic = policy.get("heuristic_only")
    return (
        policy.get("status") == "passed"
        and policy.get("ai_required") is False
        and policy.get("embedding_optional") is True
        and policy.get("llm_optional") is True
        and policy.get("heuristic_fallback_available") is True
        and str(policy.get("model_missing_handling") or "") == "warning"
        and isinstance(fallback, dict)
        and fallback.get("configured_embedding") is True
        and str(fallback.get("classifier_used") or "") == "heuristic"
        and isinstance(heuristic, dict)
        and str(heuristic.get("classifier_used") or "") == "heuristic"
    )


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _missing_sharable_audits(loaded: Sequence[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for item in loaded:
        summary = item.get("summary")
        label = str(item["path"])
        sharable_audit = summary.get("sharable_audit") if isinstance(summary, dict) else None
        if not isinstance(sharable_audit, dict) or "leak_count" not in sharable_audit:
            missing.append(label)
    return missing


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


def _detect_structural_coverage(
    summaries: Sequence[dict[str, Any]],
    queue_items: Sequence[dict[str, Any]],
    required: Sequence[str],
) -> list[str]:
    corpus = _structural_coverage_corpus(summaries, queue_items)
    return [
        bucket
        for bucket in required
        if any(term.lower() in corpus for term in STRUCTURAL_COVERAGE_TERMS.get(bucket, ()))
    ]


def _has_cad_block_text_no_expand_evidence(summaries: Sequence[dict[str, Any]]) -> bool:
    for summary in summaries:
        if not (
            _int(_nested(summary, "files", "a_kind_counts", "cad")) > 0
            and _int(_nested(summary, "files", "b_kind_counts", "cad")) > 0
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
            entity_types = {value.upper() for value in _string_list(item.get("entity_types"))}
            if not (entity_types & CAD_STRUCTURAL_TEXT_ENTITY_TYPES):
                continue
            corpus = f"{item.get('change_summary_ko') or ''} {item.get('reason_ko') or ''}"
            grouped_as_modified = (
                _int(item.get("modified_count")) > 0
                and _int(item.get("added_count")) == 0
                and _int(item.get("deleted_count")) == 0
            )
            if "@100" in corpus and "@200" in corpus and grouped_as_modified:
                return True
    return False


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
            if isinstance(detail, dict):
                chunks.extend(str(detail.get(key) or "") for key in ("category", "summary_contains", "drawing_label"))
    return " ".join(chunks).lower()


def _has_pair(summaries: Sequence[dict[str, Any]], kind: str) -> bool:
    return any(
        _int(_nested(summary, "files", "a_kind_counts", kind)) > 0
        and _int(_nested(summary, "files", "b_kind_counts", kind)) > 0
        and _int(_nested(summary, "comparison", "completed_pairs")) > 0
        for summary in summaries
    )


def _has_dwg_dxf_cad_evidence(summaries: Sequence[dict[str, Any]]) -> bool:
    extensions: set[str] = set()
    for summary in summaries:
        if (
            _int(_nested(summary, "files", "a_kind_counts", "cad")) <= 0
            or _int(_nested(summary, "files", "b_kind_counts", "cad")) <= 0
            or _int(_nested(summary, "comparison", "completed_pairs")) <= 0
        ):
            continue
        extensions.update(_source_extensions(summary))
    return all(extension in extensions for extension in CAD_INPUT_EXTENSIONS)


def _has_pdf_pdf_evidence(summaries: Sequence[dict[str, Any]]) -> bool:
    for summary in summaries:
        if (
            _int(_nested(summary, "files", "a_kind_counts", "pdf")) <= 0
            or _int(_nested(summary, "files", "b_kind_counts", "pdf")) <= 0
            or _int(_nested(summary, "comparison", "completed_pairs")) <= 0
        ):
            continue
        if _summary_has_pdf_pdf_sources(summary):
            return True
    return False


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


def _cad_pdf_block_evidence(
    loaded: Sequence[dict[str, Any]],
    summaries: Sequence[dict[str, Any]],
) -> list[str]:
    evidence: list[str] = []
    for item in loaded:
        blocked_csv = item["path"] / "blocked_pairs.csv"
        if _blocked_csv_has_cad_pdf(blocked_csv):
            evidence.append(str(blocked_csv))
    return evidence


def _blocked_csv_has_cad_pdf(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                kinds = {
                    str(row.get("a_kind") or "").strip().lower(),
                    str(row.get("b_kind") or "").strip().lower(),
                }
                reason = str(row.get("reason") or "").lower()
                if {"cad", "pdf"}.issubset(kinds) and _has_clear_cad_pdf_block_reason(reason):
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


def _manifest_ref(base: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        try:
            return os.path.relpath(path, base).replace("\\", "/")
        except ValueError:
            return path.name


def _is_template_or_handoff_evidence(path: Path | None) -> bool:
    if path is None:
        return False
    normalized = str(path).lower().replace("\\", "/")
    name = path.name.lower()
    return any(marker in name or marker in normalized for marker in DISALLOWED_EVIDENCE_PATH_MARKERS)


def _normalize_operator_role(value: str) -> str:
    return str(value or "").strip().lstrip("\ufeff").lower().replace("-", "_").replace(" ", "_")


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


def _operator_workflow_checks_from_notes(notes_file: Path | None) -> list[str]:
    text = read_operator_notes_text(notes_file)
    if not text:
        return []
    return [
        check_id
        for check_id in REQUIRED_OPERATOR_WORKFLOW_CHECKS
        if _workflow_check_is_checked(text, check_id)
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


def _csv_data_row_count(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return sum(1 for _ in csv.DictReader(handle))
    except Exception:
        return 0


def _nested(payload: Any, *keys: str) -> Any:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_close(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return False
    return abs(float(actual) - float(expected)) <= 1e-6


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


if __name__ == "__main__":
    sys.exit(main())
