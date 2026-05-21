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
    )

# Plan §17 F6 (GPT Pro deep-research review) — manifest provenance helpers.
# Add the repo root to sys.path so ``src.services.comparison.manifest_provenance``
# resolves whether the script is invoked from the worktree or imported as
# ``scripts.prepare_drawing_compare_customer_evidence`` from a test runner.
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
) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    audit_json = audit_json or out_path.with_name("sharable_path_audit_summary.json")
    review_decision_truth = review_decision_truth or review_ground_truth.with_name("review_decision_truth.csv")
    dataset_strata = dataset_strata or review_ground_truth.with_name("dataset_strata.csv")
    large_dwg_probe = large_dwg_probe or review_ground_truth.with_name("large_dwg_probe.json")
    required = list(required_structural_coverage or STRUCTURAL_COVERAGE_TERMS.keys())
    loaded = [_load_result_dir(path) for path in result_dirs]
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
        "evidence_level": (
            "customer_grade" if dataset_source_kind in CUSTOMER_GRADE_SOURCE_KINDS else "synthetic"
        ),
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
        "cad_policy_evidence": cad_policy_evidence,
        "selected_zone_performance": selected_zone_performance,
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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


if __name__ == "__main__":
    sys.exit(main())
