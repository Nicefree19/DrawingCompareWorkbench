# -*- coding: utf-8 -*-
"""Inventory customer-grade Drawing Compare MVP evidence readiness.

This script is intentionally pre-audit tooling. It does not certify a release
and does not turn synthetic outputs into customer evidence. It scans candidate
folders, summarizes validation outputs and operator artifacts, and emits the
next manifest/audit commands once the required evidence is present.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

try:  # Running from release ``cli/`` next to the evidence tools.
    from prepare_drawing_compare_customer_evidence import (
        APPROVED_OPERATOR_REVIEWER_ROLES,
        P5_G26_BENCHMARK_ID,
        P5_G26_PROFILE,
        P5_G26_REQUIRED_GATES,
        P5_G27_BENCHMARK_ID,
        P5_G27_PROFILE,
        P5_G27_REQUIRED_GATES,
        REQUIRED_OPERATOR_WORKFLOW_CHECKS,
        REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS,
        _has_cad_block_text_no_expand_evidence,
        operator_notes_have_substantive_review_notes,
        read_operator_notes_text,
        review_ground_truth_csv_issues,
        summarize_bbox_quality,
        summarize_dataset_strata_csv,
        summarize_first_interactive_readiness,
        summarize_large_dwg_resource_probe,
        summarize_p5_g24_visual_asset_policy,
        summarize_p5_g7_forced_tile_eviction_proof,
        summarize_review_decision_truth_csv,
    )
except ImportError:  # Running from source checkout.
    from scripts.prepare_drawing_compare_customer_evidence import (
        APPROVED_OPERATOR_REVIEWER_ROLES,
        P5_G26_BENCHMARK_ID,
        P5_G26_PROFILE,
        P5_G26_REQUIRED_GATES,
        P5_G27_BENCHMARK_ID,
        P5_G27_PROFILE,
        P5_G27_REQUIRED_GATES,
        REQUIRED_OPERATOR_WORKFLOW_CHECKS,
        REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS,
        _has_cad_block_text_no_expand_evidence,
        operator_notes_have_substantive_review_notes,
        read_operator_notes_text,
        review_ground_truth_csv_issues,
        summarize_bbox_quality,
        summarize_dataset_strata_csv,
        summarize_first_interactive_readiness,
        summarize_large_dwg_resource_probe,
        summarize_p5_g24_visual_asset_policy,
        summarize_p5_g7_forced_tile_eviction_proof,
        summarize_review_decision_truth_csv,
    )


SUPPORTED_DRAWING_EXTENSIONS = {".dwg", ".dxf", ".pdf"}
CONFIRMED_EXPORT_SUFFIXES = {".png", ".pdf", ".dxf"}
DEFAULT_IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "_internal",
    "node_modules",
    "site-packages",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        default=[],
        help="Folder to scan. Repeat as needed. Defaults to the current directory.",
    )
    parser.add_argument("--out", type=Path, help="Optional JSON report output path.")
    parser.add_argument(
        "--large-dwg-probe",
        type=Path,
        help="Large-DWG performance/progress probe JSON required by the final MVP exit audit.",
    )
    parser.add_argument("--min-total-pairs", type=int, default=20)
    parser.add_argument("--max-total-pairs", type=int, default=50)
    parser.add_argument(
        "--portable-paths",
        action="store_true",
        help=(
            "Emit root_N-relative path aliases instead of absolute paths. "
            "Use this for inventory JSON that may be attached to customer evidence."
        ),
    )
    parser.add_argument(
        "--include-ignored-dirs",
        action="store_true",
        help="Do not skip large generated/runtime directories such as _internal or site-packages.",
    )
    parser.add_argument(
        "--require-p5-g7-forced-tile-eviction",
        "--require-p5-g7-tile-eviction-proof",
        dest="require_p5_g7_forced_tile_eviction",
        action="store_true",
        help="Require a passing P5-G7 forced tile-cache eviction proof in the inventory.",
    )
    parser.add_argument(
        "--p5-g6-tile-cache-mb",
        type=float,
        help="Expected DRAWING_COMPARE_TILE_CACHE_MB cap for the P5-G7 forced tile-eviction proof.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    roots = [path.resolve() for path in args.root] or [Path.cwd().resolve()]
    report = inventory_roots(
        roots,
        min_total_pairs=args.min_total_pairs,
        max_total_pairs=args.max_total_pairs,
        large_dwg_probe=args.large_dwg_probe,
        include_ignored_dirs=args.include_ignored_dirs,
        portable_paths=args.portable_paths,
        require_p5_g7_forced_tile_eviction=args.require_p5_g7_forced_tile_eviction,
        p5_g6_tile_cache_mb=args.p5_g6_tile_cache_mb,
    )
    text = json.dumps(report, ensure_ascii=True, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["status"] == "ready_for_manifest" else 1


def inventory_roots(
    roots: Sequence[Path],
    *,
    min_total_pairs: int = 20,
    max_total_pairs: int = 50,
    large_dwg_probe: Path | None = None,
    include_ignored_dirs: bool = False,
    portable_paths: bool = False,
    require_p5_g7_forced_tile_eviction: bool = False,
    p5_g6_tile_cache_mb: float | None = None,
) -> dict[str, Any]:
    scan = _scan_paths(roots, include_ignored_dirs=include_ignored_dirs)
    validations = [
        _summarize_validation(path, expected_tile_cache_mb=p5_g6_tile_cache_mb)
        for path in sorted(scan["validation_summaries"])
    ]
    p5_g7_forced_tile_eviction_outputs = [
        item
        for item in validations
        if item["p5_g7_forced_tile_eviction"].get("candidate") is True
    ]
    p5_g7_forced_tile_eviction_passed_outputs = [
        item
        for item in p5_g7_forced_tile_eviction_outputs
        if item["p5_g7_forced_tile_eviction"].get("status") == "passed"
    ]
    customer_validations = [
        item
        for item in validations
        if item["p5_g7_forced_tile_eviction"].get("candidate") is not True
    ]
    completed_validations = [item for item in customer_validations if item["completed_pairs"] > 0]

    completed_pairs = sum(int(item["completed_pairs"]) for item in customer_validations)
    cad_source_extensions = {
        ext
        for item in customer_validations
        if item["format"] == "cad" and item["completed_pairs"] > 0
        for ext in item["source_extensions"]
    }
    has_dwg_dxf = {"dwg", "dxf"}.issubset(cad_source_extensions)
    has_pdf_pdf = any(item["format"] == "pdf" and item["has_pdf_pdf"] for item in customer_validations)
    has_cad_pdf_block = any(item["cad_pdf_blocked"] for item in customer_validations)
    has_cad_block_text_no_expand = any(item["cad_block_text_no_expand"] for item in customer_validations)
    selected_zone_complete = bool(completed_validations) and all(
        item["selected_zone_telemetry"] for item in completed_validations
    )
    first_screen_complete = bool(completed_validations) and all(
        item["top_review_queue_first"] for item in completed_validations
    )
    sharable_audit_complete = bool(customer_validations) and all(
        item["sharable_path_leakage_zero"] for item in customer_validations
    )
    has_workbench_acceptance = any(item["workbench_acceptance_passed"] for item in customer_validations)
    ground_truths = [_summarize_truth_csv(path) for path in sorted(scan["review_ground_truth_csvs"])]
    audited_truth_rows = sum(int(item["review_ground_truth_rows"]) for item in customer_validations)
    non_empty_ground_truths = [item for item in ground_truths if int(item["rows"]) > 0]
    schema_valid_ground_truths = [
        item for item in non_empty_ground_truths if bool(item.get("schema_valid"))
    ]
    valid_ground_truths = [
        item
        for item in schema_valid_ground_truths
        if audited_truth_rows > 0 and int(item["rows"]) <= audited_truth_rows
    ]
    review_decision_truths = [
        _summarize_review_decision_truth_csv(path)
        for path in sorted(scan["review_decision_truth_csvs"])
    ]
    valid_review_decision_truths = [
        item for item in review_decision_truths if item.get("status") == "passed"
    ]
    dataset_strata = [
        _summarize_dataset_strata_csv(path, expected_sheet_count=completed_pairs)
        for path in sorted(scan["dataset_strata_csvs"])
    ]
    valid_dataset_strata = [item for item in dataset_strata if item.get("status") == "passed"]
    first_interactive_readiness = _summarize_first_interactive_from_validations(customer_validations)
    bbox_quality = _summarize_bbox_from_validations(customer_validations)
    operator_notes = [_summarize_operator_notes(path) for path in sorted(scan["operator_notes"])]
    operator_notes_all_required = any(item["all_required_checked"] for item in operator_notes)
    operator_notes_with_approved_role = any(
        item["all_required_checked"] and item["approved_structural_reviewer_role"] for item in operator_notes
    )
    operator_notes_with_substantive_notes = any(
        item["all_required_checked"]
        and item["approved_structural_reviewer_role"]
        and item["substantive_review_notes"]
        for item in operator_notes
    )
    confirmed_artifacts = sorted(str(path) for path in scan["confirmed_artifacts"])
    release_manifests = sorted(str(path) for path in scan["release_manifests"])
    p5_g16_replay_jsons = sorted(str(path) for path in scan["p5_g16_replay_jsons"])
    p5_g16_replays = [
        _summarize_p5_g16_replay(path) for path in sorted(scan["p5_g16_replay_jsons"])
    ]
    p5_g22_gui_soak_jsons = sorted(str(path) for path in scan["p5_g22_gui_soak_jsons"])
    p5_g22_gui_soaks = [
        _summarize_p5_g22_gui_soak(path) for path in sorted(scan["p5_g22_gui_soak_jsons"])
    ]
    p5_g26_selection_latency_jsons = sorted(str(path) for path in scan["p5_g26_selection_latency_jsons"])
    p5_g26_selection_latency = [
        _summarize_p5_g26_selection_latency(path)
        for path in sorted(scan["p5_g26_selection_latency_jsons"])
    ]
    p5_g27_selected_zone_crop_jsons = sorted(
        str(path) for path in scan["p5_g27_selected_zone_crop_jsons"]
    )
    p5_g27_selected_zone_crop = [
        _summarize_p5_g27_selected_zone_crop(path)
        for path in sorted(scan["p5_g27_selected_zone_crop_jsons"])
    ]
    p5_g24_visual_asset_policy = summarize_p5_g24_visual_asset_policy(
        [
            {
                "path": Path(item["summary_path"]).parent,
                "summary": _load_json(Path(item["summary_path"])) or {},
            }
            for item in customer_validations
        ],
        evidence_level="customer_grade",
        reference_base=roots[0] if roots else None,
    )
    customer_manifests = sorted(str(path) for path in scan["customer_manifests"])
    customer_manifest_summaries = [
        _summarize_customer_manifest(path) for path in sorted(scan["customer_manifests"])
    ]
    large_dwg_probe_summary = _summarize_large_dwg_probe(large_dwg_probe)

    issues: list[str] = []
    if not (min_total_pairs <= completed_pairs <= max_total_pairs):
        issues.append(f"completed_pairs={completed_pairs} outside required range {min_total_pairs}..{max_total_pairs}")
    if not has_dwg_dxf:
        issues.append("missing completed CAD validation evidence containing both .dwg and .dxf sources")
    if not has_pdf_pdf:
        issues.append("missing completed PDF-PDF validation evidence")
    if not has_cad_pdf_block:
        issues.append("missing CAD-PDF blocked_pairs.csv or clear blocked summary evidence")
    if not has_cad_block_text_no_expand:
        issues.append("missing CAD block attribute/text no-expand validation evidence")
    if not first_screen_complete:
        issues.append("not every completed validation exposes structural_core Top 3-5 review_queue first-screen evidence")
    if not selected_zone_complete:
        issues.append("not every completed validation has selected-zone render telemetry")
    if not sharable_audit_complete:
        issues.append("missing explicit sharable_audit.leak_count=0 on one or more validation outputs")
    if not non_empty_ground_truths:
        issues.append("missing non-empty review_ground_truth CSV")
    elif not schema_valid_ground_truths:
        issues.append("review_ground_truth CSV missing required schema columns or row values")
    elif audited_truth_rows <= 0:
        issues.append("missing audited review_ground_truth metrics on validation outputs")
    elif not valid_ground_truths:
        max_csv_rows = max(int(item["rows"]) for item in non_empty_ground_truths)
        issues.append(
            "review_ground_truth CSV rows exceed audited review_ground_truth rows "
            f"({max_csv_rows}>{audited_truth_rows})"
        )
    if not review_decision_truths:
        issues.append("missing review_decision_truth CSV")
    elif not valid_review_decision_truths:
        issues.append("review_decision_truth CSV fails precision, false-positive, bucket, or schema gates")
    if not dataset_strata:
        issues.append("missing dataset_strata CSV")
    elif not valid_dataset_strata:
        issues.append("dataset_strata CSV fails stratification, sheet-count, or schema gates")
    if first_interactive_readiness.get("status") != "passed":
        issues.append("first interactive readiness metrics are missing or over budget")
    if bbox_quality.get("status") != "passed":
        issues.append("PDF selected-zone bbox fallback quality is missing or over budget")
    if not operator_notes_all_required:
        issues.append("missing operator dry-run notes with all required workflow IDs checked")
    elif not operator_notes_with_approved_role:
        issues.append("missing operator dry-run notes with approved structural review lead/team lead role")
    elif not operator_notes_with_substantive_notes:
        issues.append("missing operator dry-run notes with substantive review notes")
    if not confirmed_artifacts:
        issues.append("missing *_confirmed.{png,pdf,dxf} export artifact")
    if not has_workbench_acceptance:
        issues.append(
            "missing Workbench acceptance summary with required items "
            + "/".join(REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS)
            + " passed"
        )
    if not large_dwg_probe_summary["passed"]:
        issues.append("missing passing large-DWG performance/progress probe")
    if require_p5_g7_forced_tile_eviction and not p5_g7_forced_tile_eviction_passed_outputs:
        issues.append("missing passing P5-G7 forced tile-eviction proof validation output")
    if p5_g24_visual_asset_policy.get("status") != "passed":
        issues.append("P5-G24 visual asset policy is missing or failing for one or more completed validation outputs")

    status = "ready_for_manifest" if not issues else "incomplete"
    commands = _recommended_commands(
        validations=customer_validations,
        ground_truths=ground_truths,
        valid_ground_truths=valid_ground_truths,
        review_decision_truths=review_decision_truths,
        valid_review_decision_truths=valid_review_decision_truths,
        dataset_strata=dataset_strata,
        valid_dataset_strata=valid_dataset_strata,
        operator_notes=operator_notes,
        confirmed_artifacts=confirmed_artifacts,
        release_manifests=release_manifests,
        status=status,
        large_dwg_probe=large_dwg_probe_summary["path"],
        min_total_pairs=min_total_pairs,
        max_total_pairs=max_total_pairs,
        p5_g7_forced_tile_eviction_outputs=p5_g7_forced_tile_eviction_outputs,
        p5_g7_forced_tile_eviction_passed_outputs=p5_g7_forced_tile_eviction_passed_outputs,
        require_p5_g7_forced_tile_eviction=require_p5_g7_forced_tile_eviction,
        p5_g6_tile_cache_mb=p5_g6_tile_cache_mb,
        p5_g16_replay_jsons=p5_g16_replay_jsons,
        p5_g22_gui_soak_jsons=p5_g22_gui_soak_jsons,
        p5_g26_selection_latency_jsons=p5_g26_selection_latency_jsons,
        p5_g27_selected_zone_crop_jsons=p5_g27_selected_zone_crop_jsons,
    )
    diagnostics = _diagnostics(
        validations=customer_validations,
        ground_truths=ground_truths,
        audited_truth_rows=audited_truth_rows,
        valid_ground_truths=valid_ground_truths,
        review_decision_truths=review_decision_truths,
        valid_review_decision_truths=valid_review_decision_truths,
        dataset_strata=dataset_strata,
        valid_dataset_strata=valid_dataset_strata,
        first_interactive_readiness=first_interactive_readiness,
        bbox_quality=bbox_quality,
        operator_notes=operator_notes,
        confirmed_artifacts=confirmed_artifacts,
        has_dwg_dxf=has_dwg_dxf,
        has_pdf_pdf=has_pdf_pdf,
        has_cad_pdf_block=has_cad_pdf_block,
        has_cad_block_text_no_expand=has_cad_block_text_no_expand,
        has_workbench_acceptance=has_workbench_acceptance,
        customer_manifest_summaries=customer_manifest_summaries,
        large_dwg_probe_summary=large_dwg_probe_summary,
        p5_g7_forced_tile_eviction_outputs=p5_g7_forced_tile_eviction_outputs,
        p5_g7_forced_tile_eviction_passed_outputs=p5_g7_forced_tile_eviction_passed_outputs,
        require_p5_g7_forced_tile_eviction=require_p5_g7_forced_tile_eviction,
        p5_g6_tile_cache_mb=p5_g6_tile_cache_mb,
        p5_g16_replays=p5_g16_replays,
        p5_g22_gui_soaks=p5_g22_gui_soaks,
        p5_g26_selection_latency=p5_g26_selection_latency,
        p5_g27_selected_zone_crop=p5_g27_selected_zone_crop,
        p5_g24_visual_asset_policy=p5_g24_visual_asset_policy,
    )
    report = {
        "schema_version": 1,
        "status": status,
        "roots": [str(path) for path in roots],
        "summary": {
            "completed_pairs": completed_pairs,
            "validation_output_count": len(validations),
            "customer_validation_output_count": len(customer_validations),
            "completed_validation_output_count": len(completed_validations),
            "audited_review_ground_truth_rows": audited_truth_rows,
            "has_dwg_dxf": has_dwg_dxf,
            "has_pdf_pdf": has_pdf_pdf,
            "has_cad_pdf_block": has_cad_pdf_block,
            "has_cad_block_text_no_expand": has_cad_block_text_no_expand,
            "selected_zone_telemetry_all_completed": selected_zone_complete,
            "top_review_queue_first_all_completed": first_screen_complete,
            "sharable_path_leakage_zero_all": sharable_audit_complete,
            "workbench_acceptance_passed": has_workbench_acceptance,
            "review_decision_truth_passed": bool(valid_review_decision_truths),
            "dataset_strata_passed": bool(valid_dataset_strata),
            "first_interactive_ready_passed": first_interactive_readiness.get("status") == "passed",
            "bbox_quality_passed": bbox_quality.get("status") == "passed",
            "large_dwg_probe_passed": large_dwg_probe_summary["passed"],
            "p5_g7_forced_tile_eviction_required": require_p5_g7_forced_tile_eviction,
            "p5_g7_forced_tile_eviction_proof_count": len(p5_g7_forced_tile_eviction_outputs),
            "p5_g7_forced_tile_eviction_passed_count": len(p5_g7_forced_tile_eviction_passed_outputs),
            "p5_g7_forced_tile_eviction_passed": bool(p5_g7_forced_tile_eviction_passed_outputs),
            "p5_g6_tile_cache_mb": p5_g6_tile_cache_mb,
            "p5_g16_real_corpus_replay_count": len(p5_g16_replays),
            "p5_g16_real_corpus_replay_passed_count": len(
                [item for item in p5_g16_replays if item.get("status") == "passed"]
            ),
            "p5_g22_actual_gui_soak_count": len(p5_g22_gui_soaks),
            "p5_g22_actual_gui_soak_passed_count": len(
                [item for item in p5_g22_gui_soaks if item.get("status") == "passed"]
            ),
            "p5_g26_selection_latency_count": len(p5_g26_selection_latency),
            "p5_g26_selection_latency_passed_count": len(
                [item for item in p5_g26_selection_latency if item.get("status") == "passed"]
            ),
            "p5_g27_selected_zone_crop_count": len(p5_g27_selected_zone_crop),
            "p5_g27_selected_zone_crop_passed_count": len(
                [item for item in p5_g27_selected_zone_crop if item.get("status") == "passed"]
            ),
            "p5_g24_visual_asset_policy_passed": p5_g24_visual_asset_policy.get("status") == "passed",
        },
        "validation_outputs": validations,
        "drawing_file_groups": _summarize_drawing_groups(scan["drawing_files"]),
        "review_ground_truth_csvs": ground_truths,
        "review_decision_truth_csvs": review_decision_truths,
        "dataset_strata_csvs": dataset_strata,
        "operator_notes": operator_notes,
        "confirmed_export_artifacts": confirmed_artifacts,
        "customer_evidence_manifests": customer_manifests,
        "customer_evidence_manifest_summaries": customer_manifest_summaries,
        "release_manifests": release_manifests,
        "p5_g16_real_corpus_replay_jsons": p5_g16_replay_jsons,
        "p5_g16_real_corpus_replays": p5_g16_replays,
        "p5_g22_actual_gui_soak_jsons": p5_g22_gui_soak_jsons,
        "p5_g22_actual_gui_soaks": p5_g22_gui_soaks,
        "p5_g26_selection_latency_jsons": p5_g26_selection_latency_jsons,
        "p5_g26_selection_latency": p5_g26_selection_latency,
        "p5_g27_selected_zone_crop_jsons": p5_g27_selected_zone_crop_jsons,
        "p5_g27_selected_zone_crop": p5_g27_selected_zone_crop,
        "p5_g24_visual_asset_policy": p5_g24_visual_asset_policy,
        "large_dwg_probe": large_dwg_probe_summary,
        "first_interactive_readiness": first_interactive_readiness,
        "bbox_quality": bbox_quality,
        "issues": issues,
        "diagnostics": diagnostics,
        "recommended_commands": commands,
    }
    if portable_paths:
        return _with_portable_paths(report, roots)
    return report


def _with_portable_paths(report: dict[str, Any], roots: Sequence[Path]) -> dict[str, Any]:
    """Return a copy of report with absolute root paths replaced by stable aliases."""
    aliases = [
        (str(path.resolve()), f"root_{index}", index)
        for index, path in enumerate(roots, start=1)
    ]
    aliases.sort(key=lambda item: len(item[0]), reverse=True)

    def transform(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: transform(item) for key, item in value.items()}
        if isinstance(value, list):
            return [transform(item) for item in value]
        if isinstance(value, str):
            text = value
            for absolute, alias, _index in aliases:
                variants = {absolute, absolute.replace("\\", "/")}
                for variant in variants:
                    text = text.replace(variant, alias)
            return text.replace("\\", "/")
        return value

    portable = transform(report)
    portable["path_policy"] = {
        "portable_paths": True,
        "root_aliases": [
            {"alias": alias, "description": f"input root {index}"}
            for _absolute, alias, index in sorted(aliases, key=lambda item: item[2])
        ],
        "note": "Path references are root_N-relative aliases for customer-safe evidence review.",
    }
    return portable


def _scan_paths(roots: Sequence[Path], *, include_ignored_dirs: bool) -> dict[str, set[Path]]:
    found: dict[str, set[Path]] = {
        "validation_summaries": set(),
        "drawing_files": set(),
        "review_ground_truth_csvs": set(),
        "review_decision_truth_csvs": set(),
        "dataset_strata_csvs": set(),
        "operator_notes": set(),
        "confirmed_artifacts": set(),
        "customer_manifests": set(),
        "release_manifests": set(),
        "p5_g16_replay_jsons": set(),
        "p5_g22_gui_soak_jsons": set(),
        "p5_g26_selection_latency_jsons": set(),
        "p5_g27_selected_zone_crop_jsons": set(),
    }
    for root in roots:
        if root.is_file():
            _classify_file(root, found)
            continue
        if not root.exists():
            continue
        for current, dirs, files in os.walk(root):
            if not include_ignored_dirs:
                dirs[:] = [name for name in dirs if name not in DEFAULT_IGNORED_DIRS]
            base = Path(current)
            for name in files:
                _classify_file(base / name, found)
    return found


def _classify_file(path: Path, found: dict[str, set[Path]]) -> None:
    lower = path.name.lower()
    suffix = path.suffix.lower()
    if lower == "validation_summary.json":
        found["validation_summaries"].add(path)
    elif suffix in SUPPORTED_DRAWING_EXTENSIONS:
        found["drawing_files"].add(path)
    elif suffix == ".csv" and "review_ground_truth" in lower and not _is_non_customer_evidence_artifact(path):
        found["review_ground_truth_csvs"].add(path)
    elif suffix == ".csv" and "review_decision_truth" in lower and not _is_non_customer_evidence_artifact(path):
        found["review_decision_truth_csvs"].add(path)
    elif suffix == ".csv" and "dataset_strata" in lower and not _is_non_customer_evidence_artifact(path):
        found["dataset_strata_csvs"].add(path)
    elif (
        suffix == ".md"
        and ("operator" in lower or "dry_run" in lower or "dry-run" in lower)
        and not _is_non_customer_evidence_artifact(path)
    ):
        found["operator_notes"].add(path)
    elif lower == "customer_evidence_manifest.json" and not _is_non_customer_evidence_artifact(path):
        found["customer_manifests"].add(path)
    elif lower == "release_manifest.json":
        found["release_manifests"].add(path)
    elif lower == "p5_g16_real_corpus_replay.json":
        found["p5_g16_replay_jsons"].add(path)
    elif lower == "p5_g22_actual_gui_soak.json":
        found["p5_g22_gui_soak_jsons"].add(path)
    elif lower == "p5_g26_selection_latency_soak.json":
        found["p5_g26_selection_latency_jsons"].add(path)
    elif lower == "p5_g27_selected_zone_crop_soak.json":
        found["p5_g27_selected_zone_crop_jsons"].add(path)
    elif path.stem.endswith("_confirmed") and suffix in CONFIRMED_EXPORT_SUFFIXES:
        found["confirmed_artifacts"].add(path)


def _is_template_or_handoff(path: Path) -> bool:
    """Return True for packaged guidance files that are not customer evidence."""
    normalized = str(path).lower().replace("\\", "/")
    name = path.name.lower()
    template_markers = (
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
    return any(marker in name or marker in normalized for marker in template_markers)


def _is_non_customer_evidence_artifact(path: Path) -> bool:
    """Return True for guidance/probe files that must not seed customer evidence."""
    if _is_template_or_handoff(path):
        return True
    return any(_is_probe_artifact_part(part) for part in path.parts)


def _is_probe_artifact_part(part: str) -> bool:
    """Return True for generated probe path segments, excluding probe-filtered releases."""
    normalized = part.lower()
    if "probe_filter" in normalized:
        return False
    if not normalized.startswith("drawing_compare_"):
        return False
    return (
        normalized.endswith("_probe")
        or "_probe_" in normalized
        or normalized.endswith("-probe")
        or "-probe-" in normalized
    )


def _summarize_validation(
    summary_path: Path,
    *,
    expected_tile_cache_mb: float | None = None,
) -> dict[str, Any]:
    root = summary_path.parent
    summary = _load_json(summary_path) or {}
    completed_pairs = _int(_nested(summary, "comparison", "completed_pairs"))
    source_exts = _source_extensions(summary)
    kind = _validation_kind(summary)
    workbench_path = root / "workbench_acceptance_summary.json"
    workbench_acceptance_passed = _workbench_acceptance_passed(workbench_path)
    return {
        "path": str(root),
        "summary_path": str(summary_path),
        "completed_pairs": completed_pairs,
        "success": (root / "_SUCCESS").exists(),
        "format": kind,
        "source_extensions": sorted(source_exts),
        "has_dwg": "dwg" in source_exts,
        "has_dxf": "dxf" in source_exts,
        "has_pdf_pdf": _has_pdf_pdf(summary),
        "cad_pdf_blocked": _has_cad_pdf_block(root, summary),
        "cad_block_text_no_expand": _has_cad_block_text_no_expand_evidence([summary]),
        "top_review_queue_first": _top_review_queue_first(summary),
        "selected_zone_telemetry": _selected_zone_telemetry(summary, root),
        "sharable_path_leakage_zero": _nested(summary, "sharable_audit", "leak_count") == 0,
        "review_ground_truth_rows": _int(_nested(summary, "review_ground_truth", "rows")),
        "workbench_acceptance_summary": str(workbench_path) if workbench_path.exists() else "",
        "workbench_acceptance_passed": workbench_acceptance_passed,
        "p5_g7_forced_tile_eviction": summarize_p5_g7_forced_tile_eviction_proof(
            summary,
            result_dir=root,
            summary_path=summary_path,
            expected_tile_cache_mb=expected_tile_cache_mb,
        ),
    }


def _recommended_commands(
    *,
    validations: Sequence[dict[str, Any]],
    ground_truths: Sequence[dict[str, Any]],
    valid_ground_truths: Sequence[dict[str, Any]],
    review_decision_truths: Sequence[dict[str, Any]],
    valid_review_decision_truths: Sequence[dict[str, Any]],
    dataset_strata: Sequence[dict[str, Any]],
    valid_dataset_strata: Sequence[dict[str, Any]],
    operator_notes: Sequence[dict[str, Any]],
    confirmed_artifacts: Sequence[str],
    release_manifests: Sequence[str],
    status: str,
    large_dwg_probe: str,
    min_total_pairs: int,
    max_total_pairs: int,
    p5_g7_forced_tile_eviction_outputs: Sequence[dict[str, Any]],
    p5_g7_forced_tile_eviction_passed_outputs: Sequence[dict[str, Any]],
    require_p5_g7_forced_tile_eviction: bool,
    p5_g6_tile_cache_mb: float | None,
    p5_g16_replay_jsons: Sequence[str],
    p5_g22_gui_soak_jsons: Sequence[str],
    p5_g26_selection_latency_jsons: Sequence[str],
    p5_g27_selected_zone_crop_jsons: Sequence[str],
) -> dict[str, str]:
    result_args = " ".join(f'--results-dir "{item["path"]}"' for item in validations)
    p5_g7_proof_args = " ".join(
        f'--p5-g7-tile-eviction-proof-dir "{item["path"]}"'
        for item in p5_g7_forced_tile_eviction_passed_outputs
    )
    truth = next(
        (item["path"] for item in valid_ground_truths),
        next(
            (
                item["path"]
                for item in ground_truths
                if item["rows"] > 0 and item.get("schema_valid")
            ),
            "<review_ground_truth.csv>",
        ),
    )
    decision_truth = next(
        (item["path"] for item in valid_review_decision_truths),
        next((item["path"] for item in review_decision_truths if item.get("rows", 0) > 0), "<review_decision_truth.csv>"),
    )
    strata = next(
        (item["path"] for item in valid_dataset_strata),
        next((item["path"] for item in dataset_strata if item.get("rows", 0) > 0), "<dataset_strata.csv>"),
    )
    notes = next(
        (
            item["path"]
            for item in operator_notes
            if (
                item["all_required_checked"]
                and item["approved_structural_reviewer_role"]
                and item["substantive_review_notes"]
            )
        ),
        next(
            (
                item["path"]
                for item in operator_notes
                if item["all_required_checked"] and item["approved_structural_reviewer_role"]
            ),
            next((item["path"] for item in operator_notes if item["all_required_checked"]), "<operator_notes.md>"),
        ),
    )
    confirmed = confirmed_artifacts[0] if confirmed_artifacts else r"<artifacts\confirmed_clouds\pair_confirmed.png>"
    release_manifest = release_manifests[0] if release_manifests else "<release_manifest.json>"
    large_probe = large_dwg_probe or "<large_dwg_probe.json>"
    manifest = "<customer_evidence_manifest.json>"
    p5_g7_require_arg = (
        "--require-p5-g7-tile-eviction-proof "
        if require_p5_g7_forced_tile_eviction
        else ""
    )
    p5_g6_tile_cache_arg = (
        f"--p5-g6-tile-cache-mb {_format_number_arg(p5_g6_tile_cache_mb)} "
        if p5_g6_tile_cache_mb is not None
        else ""
    )
    p5_g16_args = " ".join(
        f'--p5-g16-benchmark-json "{path}"' for path in p5_g16_replay_jsons
    )
    if p5_g16_args:
        p5_g16_args = p5_g16_args + " "
    p5_g22_args = " ".join(
        f'--p5-g22-gui-soak-json "{path}"' for path in p5_g22_gui_soak_jsons
    )
    if p5_g22_args:
        p5_g22_args = p5_g22_args + " "
    p5_g26_prepare_args = " ".join(
        f'--p5-g26-selection-latency-json "{path}"'
        for path in p5_g26_selection_latency_jsons
    )
    if p5_g26_prepare_args:
        p5_g26_prepare_args = p5_g26_prepare_args + " "
    p5_g26_audit_args = " ".join(
        f'--p5-g26-selection-latency-json "{path}"'
        for path in p5_g26_selection_latency_jsons
    )
    if p5_g26_audit_args:
        p5_g26_audit_args = p5_g26_audit_args + " "
    p5_g27_prepare_args = " ".join(
        f'--p5-g27-selected-zone-crop-json "{path}"'
        for path in p5_g27_selected_zone_crop_jsons
    )
    if p5_g27_prepare_args:
        p5_g27_prepare_args = p5_g27_prepare_args + " "
    p5_g27_audit_args = " ".join(
        f'--p5-g27-selected-zone-crop-json "{path}"'
        for path in p5_g27_selected_zone_crop_jsons
    )
    if p5_g27_audit_args:
        p5_g27_audit_args = p5_g27_audit_args + " "
    # Plan §17 Phase B-5 (GPT Pro F3): the legacy 10000/2000 ms defaults
    # below trigger a deprecation warning on stderr. Once Phase B-2
    # (PyMuPDF DisplayList) + B-3 (DXF pre-filter) + B-4 (prefetch) have
    # delivered the tighter measured latency, append
    # --strict-zone-render-budget to both commands to opt into the
    # 2000/500 ms strict gate before the defaults flip in a future
    # release.
    prepare = (
        _tool_command("prepare_drawing_compare_customer_evidence.py") + " "
        f"{result_args} --out \"{manifest}\" --dataset-id <dataset_id> "
        "--dataset-source-kind customer_grade "
        "--dataset-source-description \"20-50 sheet customer-grade validation set approved for MVP exit\" "
        "--dataset-approval-status approved_for_mvp_exit --dataset-approver <approver> "
        "--ground-truth-owner <owner> "
        f"--review-ground-truth \"{truth}\" --ground-truth-status approved "
        f"--review-decision-truth \"{decision_truth}\" --dataset-strata \"{strata}\" "
        f"--large-dwg-probe \"{large_probe}\" "
        f"{p5_g7_proof_args} "
        f"{p5_g16_args}"
        f"{p5_g22_args}"
        f"{p5_g26_prepare_args}"
        f"{p5_g27_prepare_args}"
        f"{p5_g7_require_arg}{p5_g6_tile_cache_arg}"
        "--operator-reviewer-role structural_review_lead "
        f"--operator-notes-file \"{notes}\" --confirmed-export-artifact \"{confirmed}\" "
        f"--min-total-pairs {min_total_pairs} --max-total-pairs {max_total_pairs} "
        "--max-first-review-ready-s 1800 --max-cold-zone-render-ms 10000 "
        "--max-cache-hit-zone-render-ms 2000 "
        "# add --strict-zone-render-budget for Plan §17 Phase B-5 strict gate (2000/500 ms)"
    )
    audit = (
        _tool_command("audit_drawing_compare_mvp_exit.py") + " "
        f"{result_args} --release-manifest \"{release_manifest}\" "
        f"--large-dwg-probe \"{large_probe}\" --require-large-dwg-probe "
        f"--customer-evidence-manifest \"{manifest}\" --evidence-level customer_grade "
        f"{p5_g16_args}"
        f"{p5_g22_args}"
        f"{p5_g26_audit_args}"
        f"{p5_g27_audit_args}"
        f"--min-total-pairs {min_total_pairs} --max-total-pairs {max_total_pairs} "
        "--max-first-review-ready-s 1800 --max-cold-zone-render-ms 10000 "
        "--max-cache-hit-zone-render-ms 2000 --out <mvp_exit_audit.json> "
        "# add --strict-zone-render-budget for Plan §17 Phase B-5 strict gate (2000/500 ms)"
    )
    if status != "ready_for_manifest":
        return {
            "next_step": "Resolve issues[] first; then run prepare_manifest_command and final_audit_command.",
            "prepare_manifest_command": prepare,
            "final_audit_command": audit,
        }
    return {
        "next_step": "Evidence inventory is ready for manifest generation; run prepare_manifest_command, then final_audit_command.",
        "prepare_manifest_command": prepare,
        "final_audit_command": audit,
    }


def _tool_command(script_name: str) -> str:
    container = Path(__file__).resolve().parent.name.lower()
    if container == "scripts":
        folder = "scripts"
    elif container == "cli":
        folder = "cli"
    else:
        folder = "cli"
    return f"python {folder}\\{script_name}"


def _summarize_p5_g16_replay(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return {
            "path": str(path),
            "readable": False,
            "status": "failed",
            "benchmark_id": "",
            "profile": "",
            "issues": ["p5_g16_real_corpus_replay JSON is missing or unreadable"],
        }
    issues: list[str] = []
    if payload.get("benchmark_id") != "p5_g16_real_corpus_replay":
        issues.append("benchmark_id must be p5_g16_real_corpus_replay")
    if payload.get("profile") != "real_corpus_artifact_replay":
        issues.append("profile must be real_corpus_artifact_replay")
    if payload.get("status") != "passed":
        issues.append(f"status={payload.get('status') or '<missing>'}")
    return {
        "path": str(path),
        "readable": True,
        "status": "passed" if not issues else "failed",
        "benchmark_id": str(payload.get("benchmark_id") or ""),
        "profile": str(payload.get("profile") or ""),
        "customer_manifest": str(_nested(payload, "artifacts", "customer_evidence_manifest") or ""),
        "validation_summary": str(_nested(payload, "artifacts", "validation_summary") or ""),
        "issues": issues,
    }


def _summarize_p5_g22_gui_soak(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return {
            "path": str(path),
            "readable": False,
            "status": "failed",
            "benchmark_id": "",
            "profile": "",
            "issues": ["p5_g22_actual_gui_soak JSON is missing or unreadable"],
        }
    issues: list[str] = []
    if payload.get("benchmark_id") != "p5_g22_actual_gui_soak":
        issues.append("benchmark_id must be p5_g22_actual_gui_soak")
    if payload.get("profile") != "actual_gui_customer_corpus_soak":
        issues.append("profile must be actual_gui_customer_corpus_soak")
    if payload.get("status") != "passed":
        issues.append(f"status={payload.get('status') or '<missing>'}")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
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
    if not native_summary:
        issues.append("summary.native_resource_summary missing")
    elif native_summary.get("measurement_available") is not True:
        issues.append("summary.native_resource_summary.measurement_available must be true")
    if not worker_summary:
        issues.append("summary.worker_tree_summary missing")
    elif worker_summary.get("cleanup_ok") is not True or _int(worker_summary.get("orphan_worker_count")) != 0:
        issues.append("summary.worker_tree_summary cleanup/orphan check failed")
    return {
        "path": str(path),
        "readable": True,
        "status": "passed" if not issues else "failed",
        "benchmark_id": str(payload.get("benchmark_id") or ""),
        "profile": str(payload.get("profile") or ""),
        "customer_manifest": str(_nested(payload, "args", "customer_evidence_manifest") or ""),
        "validation_summary": str(_nested(payload, "args", "validation_summary") or ""),
        "completed_visit_count": _int(summary.get("completed_visit_count")),
        "blank_view_count": _int(summary.get("blank_view_count")),
        "orphan_worker_count": _int(summary.get("orphan_worker_count")),
        "native_resource_summary": native_summary,
        "worker_tree_summary": worker_summary,
        "shared_summaries_present": bool(native_summary and worker_summary),
        "issues": issues,
    }


def _summarize_p5_g26_selection_latency(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return {
            "path": str(path),
            "readable": False,
            "status": "failed",
            "benchmark_id": "",
            "profile": "",
            "issues": ["p5_g26_selection_latency JSON is missing or unreadable"],
        }
    contract = payload.get("p5_g26_contract")
    if not isinstance(contract, dict):
        contract = payload.get("p5_g26_evidence")
    if not isinstance(contract, dict):
        contract = {}
    gates = payload.get("gates")
    gate_by_name = (
        {
            str(gate.get("name") or ""): gate
            for gate in gates
            if isinstance(gate, dict) and str(gate.get("name") or "")
        }
        if isinstance(gates, list)
        else {}
    )
    issues: list[str] = []
    if payload.get("benchmark_id") != P5_G26_BENCHMARK_ID:
        issues.append(f"benchmark_id must be {P5_G26_BENCHMARK_ID}")
    if payload.get("profile") != P5_G26_PROFILE:
        issues.append(f"profile must be {P5_G26_PROFILE}")
    if payload.get("status") != "passed":
        issues.append(f"status={payload.get('status') or '<missing>'}")
    if not contract:
        issues.append("p5_g26_contract missing")
    else:
        if contract.get("wp_a_passed") is not True:
            issues.append("p5_g26_contract.wp_a_passed must be true")
        if contract.get("wp_b_passed") is not True:
            issues.append("p5_g26_contract.wp_b_passed must be true")
        if contract.get("has_zone_selection_evidence") is not True:
            issues.append("p5_g26_contract.has_zone_selection_evidence must be true")
    missing = sorted(P5_G26_REQUIRED_GATES - set(gate_by_name))
    if missing:
        issues.append("required gates missing: " + ", ".join(missing))
    failed = sorted(
        gate_name
        for gate_name, gate in gate_by_name.items()
        if gate_name in P5_G26_REQUIRED_GATES
        and gate.get("required") is not False
        and gate.get("passed") is not True
    )
    if failed:
        issues.append("required gates failed: " + ", ".join(failed))
    return {
        "path": str(path),
        "readable": True,
        "status": "passed" if not issues else "failed",
        "benchmark_id": str(payload.get("benchmark_id") or ""),
        "profile": str(payload.get("profile") or ""),
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


def _summarize_p5_g27_selected_zone_crop(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return {
            "path": str(path),
            "readable": False,
            "status": "failed",
            "benchmark_id": "",
            "profile": "",
            "issues": ["p5_g27_selected_zone_crop JSON is missing or unreadable"],
        }
    contract = payload.get("p5_g27_contract")
    if not isinstance(contract, dict):
        contract = payload.get("p5_g27_evidence")
    if not isinstance(contract, dict):
        contract = {}
    gates = payload.get("gates")
    gate_by_name = (
        {
            str(gate.get("name") or ""): gate
            for gate in gates
            if isinstance(gate, dict) and str(gate.get("name") or "")
        }
        if isinstance(gates, list)
        else {}
    )
    issues: list[str] = []
    if payload.get("benchmark_id") != P5_G27_BENCHMARK_ID:
        issues.append(f"benchmark_id must be {P5_G27_BENCHMARK_ID}")
    if payload.get("profile") != P5_G27_PROFILE:
        issues.append(f"profile must be {P5_G27_PROFILE}")
    if payload.get("status") != "passed":
        issues.append(f"status={payload.get('status') or '<missing>'}")
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
            issues.append("p5_g27_contract.has_selected_zone_crop_first_evidence must be true")
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
    missing = sorted(P5_G27_REQUIRED_GATES - set(gate_by_name))
    if missing:
        issues.append("required gates missing: " + ", ".join(missing))
    failed = sorted(
        gate_name
        for gate_name, gate in gate_by_name.items()
        if gate_name in P5_G27_REQUIRED_GATES
        and gate.get("required") is not False
        and gate.get("passed") is not True
    )
    if failed:
        issues.append("required gates failed: " + ", ".join(failed))
    return {
        "path": str(path),
        "readable": True,
        "status": "passed" if not issues else "failed",
        "benchmark_id": str(payload.get("benchmark_id") or ""),
        "profile": str(payload.get("profile") or ""),
        "crop_first_result_visible": contract.get("crop_first_result_visible") is True,
        "crop_visible_before_vector_focus": contract.get("crop_visible_before_vector_focus") is True,
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


def _diagnostics(
    *,
    validations: Sequence[dict[str, Any]],
    ground_truths: Sequence[dict[str, Any]],
    audited_truth_rows: int,
    valid_ground_truths: Sequence[dict[str, Any]],
    review_decision_truths: Sequence[dict[str, Any]],
    valid_review_decision_truths: Sequence[dict[str, Any]],
    dataset_strata: Sequence[dict[str, Any]],
    valid_dataset_strata: Sequence[dict[str, Any]],
    first_interactive_readiness: dict[str, Any],
    bbox_quality: dict[str, Any],
    operator_notes: Sequence[dict[str, Any]],
    confirmed_artifacts: Sequence[str],
    has_dwg_dxf: bool,
    has_pdf_pdf: bool,
    has_cad_pdf_block: bool,
    has_cad_block_text_no_expand: bool,
    has_workbench_acceptance: bool,
    customer_manifest_summaries: Sequence[dict[str, Any]],
    large_dwg_probe_summary: dict[str, Any],
    p5_g7_forced_tile_eviction_outputs: Sequence[dict[str, Any]],
    p5_g7_forced_tile_eviction_passed_outputs: Sequence[dict[str, Any]],
    require_p5_g7_forced_tile_eviction: bool,
    p5_g6_tile_cache_mb: float | None,
    p5_g16_replays: Sequence[dict[str, Any]],
    p5_g22_gui_soaks: Sequence[dict[str, Any]],
    p5_g26_selection_latency: Sequence[dict[str, Any]],
    p5_g27_selected_zone_crop: Sequence[dict[str, Any]],
    p5_g24_visual_asset_policy: dict[str, Any],
) -> dict[str, Any]:
    completed = [item for item in validations if int(item["completed_pairs"]) > 0]
    missing_format_coverage: list[str] = []
    if not has_dwg_dxf:
        missing_format_coverage.append("dwg_dxf")
    if not has_pdf_pdf:
        missing_format_coverage.append("pdf_pdf")
    if not has_cad_pdf_block:
        missing_format_coverage.append("cad_pdf_blocked")
    return {
        "validation_outputs_missing_success_sentinel": [
            item["path"] for item in validations if not item["success"]
        ],
        "validation_outputs_missing_top_review_queue_first": [
            item["path"] for item in completed if not item["top_review_queue_first"]
        ],
        "validation_outputs_missing_selected_zone_telemetry": [
            item["path"] for item in completed if not item["selected_zone_telemetry"]
        ],
        "validation_outputs_missing_sharable_path_leakage_zero": [
            item["path"] for item in validations if not item["sharable_path_leakage_zero"]
        ],
        "missing_format_coverage": missing_format_coverage,
        "validation_outputs_with_cad_block_text_no_expand": [
            item["path"] for item in validations if item["cad_block_text_no_expand"]
        ],
        "validation_outputs_missing_cad_block_text_no_expand": (
            [] if has_cad_block_text_no_expand else [item["path"] for item in completed if item["format"] == "cad"]
        ),
        "review_ground_truth_csv_candidates": [
            item for item in ground_truths if int(item["rows"]) > 0
        ],
        "valid_review_ground_truth_csv_candidates": list(valid_ground_truths),
        "review_ground_truth_csv_schema_issues": [
            item for item in ground_truths if item.get("schema_issues")
        ],
        "review_decision_truth_csv_candidates": list(review_decision_truths),
        "valid_review_decision_truth_csv_candidates": list(valid_review_decision_truths),
        "review_decision_truth_csv_issues": [
            item for item in review_decision_truths if item.get("issues")
        ],
        "dataset_strata_csv_candidates": list(dataset_strata),
        "valid_dataset_strata_csv_candidates": list(valid_dataset_strata),
        "dataset_strata_csv_issues": [
            item for item in dataset_strata if item.get("issues")
        ],
        "first_interactive_readiness": first_interactive_readiness,
        "bbox_quality": bbox_quality,
        "audited_review_ground_truth_rows": audited_truth_rows,
        "required_operator_workflow_checks": list(REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        "operator_notes_candidate_count": len(operator_notes),
        "operator_notes_all_required_checked": any(item["all_required_checked"] for item in operator_notes),
        "operator_notes_with_approved_structural_role": any(
            item["all_required_checked"] and item["approved_structural_reviewer_role"]
            for item in operator_notes
        ),
        "operator_notes_with_substantive_review_notes": any(
            item["all_required_checked"]
            and item["approved_structural_reviewer_role"]
            and item["substantive_review_notes"]
            for item in operator_notes
        ),
        "approved_operator_reviewer_roles": sorted(APPROVED_OPERATOR_REVIEWER_ROLES),
        "missing_operator_workflow_checks": (
            []
            if any(item["all_required_checked"] for item in operator_notes)
            else list(REQUIRED_OPERATOR_WORKFLOW_CHECKS)
        ),
        "operator_notes_missing_required_checks": [
            item for item in operator_notes if item["missing_checks"]
        ],
        "operator_notes_missing_approved_structural_role": [
            item for item in operator_notes if item["all_required_checked"] and not item["approved_structural_reviewer_role"]
        ],
        "operator_notes_missing_substantive_review_notes": [
            item
            for item in operator_notes
            if item["all_required_checked"]
            and item["approved_structural_reviewer_role"]
            and not item["substantive_review_notes"]
        ],
        "confirmed_export_artifact_count": len(confirmed_artifacts),
        "workbench_acceptance_summary_found": has_workbench_acceptance,
        "large_dwg_probe_passed": large_dwg_probe_summary["passed"],
        "large_dwg_probe_issues": list(large_dwg_probe_summary["issues"]),
        "p5_g7_forced_tile_eviction_required": require_p5_g7_forced_tile_eviction,
        "p5_g7_forced_tile_eviction_expected_tile_cache_mb": p5_g6_tile_cache_mb,
        "p5_g7_forced_tile_eviction_candidates": [
            item["p5_g7_forced_tile_eviction"]
            for item in p5_g7_forced_tile_eviction_outputs
        ],
        "p5_g7_forced_tile_eviction_passed_outputs": [
            item["path"] for item in p5_g7_forced_tile_eviction_passed_outputs
        ],
        "p5_g7_forced_tile_eviction_missing_outputs": (
            []
            if (not require_p5_g7_forced_tile_eviction or p5_g7_forced_tile_eviction_passed_outputs)
            else ["p5_g7_forced_tile_eviction"]
        ),
        "p5_g7_forced_tile_eviction_issues": [
            {
                "path": item["path"],
                "issues": item["p5_g7_forced_tile_eviction"].get("issues", []),
            }
            for item in p5_g7_forced_tile_eviction_outputs
            if item["p5_g7_forced_tile_eviction"].get("status") != "passed"
        ],
        "p5_g16_real_corpus_replay_candidates": list(p5_g16_replays),
        "p5_g16_real_corpus_replay_passed": [
            item for item in p5_g16_replays if item.get("status") == "passed"
        ],
        "p5_g22_actual_gui_soak_candidates": list(p5_g22_gui_soaks),
        "p5_g22_actual_gui_soak_passed": [
            item for item in p5_g22_gui_soaks if item.get("status") == "passed"
        ],
        "p5_g22_native_resource_summary_passed": [
            item
            for item in p5_g22_gui_soaks
            if (item.get("native_resource_summary") or {}).get("measurement_available") is True
        ],
        "p5_g22_worker_tree_summary_passed": [
            item
            for item in p5_g22_gui_soaks
            if (item.get("worker_tree_summary") or {}).get("cleanup_ok") is True
            and _int((item.get("worker_tree_summary") or {}).get("orphan_worker_count")) == 0
        ],
        "p5_g22_actual_gui_soak_missing_shared_summaries": [
            item for item in p5_g22_gui_soaks if not item.get("shared_summaries_present")
        ],
        "p5_g26_selection_latency_candidates": list(p5_g26_selection_latency),
        "p5_g26_selection_latency_passed": [
            item for item in p5_g26_selection_latency if item.get("status") == "passed"
        ],
        "p5_g26_selection_latency_failed": [
            item for item in p5_g26_selection_latency if item.get("status") != "passed"
        ],
        "p5_g27_selected_zone_crop_candidates": list(p5_g27_selected_zone_crop),
        "p5_g27_selected_zone_crop_passed": [
            item for item in p5_g27_selected_zone_crop if item.get("status") == "passed"
        ],
        "p5_g27_selected_zone_crop_failed": [
            item for item in p5_g27_selected_zone_crop if item.get("status") != "passed"
        ],
        "p5_g24_visual_asset_policy_status": p5_g24_visual_asset_policy.get("status"),
        "p5_g24_visual_asset_policy_issues": list(p5_g24_visual_asset_policy.get("issues") or []),
        "p5_g24_visual_asset_policy_evidence": list(p5_g24_visual_asset_policy.get("evidence") or []),
        "customer_evidence_manifest_count": len(customer_manifest_summaries),
        "customer_evidence_manifests_not_ready": [
            item for item in customer_manifest_summaries if not item["self_check_ready"]
        ],
        "customer_evidence_manifests_missing_approved_ground_truth": [
            item
            for item in customer_manifest_summaries
            if item["ground_truth_status"] != "approved"
        ],
        "selected_zone_evidence_hint": (
            "Run validation with --render-selected-zone-evidence and "
            "--selected-zone-evidence-per-pair 1 for every completed customer output."
        ),
    }


def _summarize_customer_manifest(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return {
            "path": str(path),
            "readable": False,
            "evidence_level": "",
            "readiness_status": "",
            "readiness_issue_count": 0,
            "ground_truth_status": "",
            "path_leakage_status": "",
            "path_leakage_count": None,
            "self_check_ready": False,
            "issues": ["customer_evidence_manifest.json is missing or unreadable"],
        }

    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    path_audit = (
        payload.get("path_leakage_audit")
        if isinstance(payload.get("path_leakage_audit"), dict)
        else {}
    )
    issues: list[str] = []
    evidence_level = str(payload.get("evidence_level") or "").strip()
    readiness_status = str(readiness.get("status") or "").strip()
    readiness_issues = readiness.get("issues")
    readiness_issue_count = len(readiness_issues) if isinstance(readiness_issues, list) else 0
    ground_truth_status = str(_nested(payload, "ground_truth", "status") or "").strip()
    path_leakage_status = str(path_audit.get("status") or "").strip()
    path_leakage_count = _int(path_audit.get("leak_count")) if "leak_count" in path_audit else None

    if evidence_level != "customer_grade":
        issues.append("manifest.evidence_level must be customer_grade")
    if readiness_status != "ready":
        issues.append("manifest.readiness.status must be ready")
    if readiness_issue_count:
        issues.append("manifest.readiness.issues must be empty")
    if ground_truth_status != "approved":
        issues.append("manifest.ground_truth.status must be approved")
    if path_leakage_status != "passed":
        issues.append("manifest.path_leakage_audit.status must be passed")
    if path_leakage_count != 0:
        issues.append("manifest.path_leakage_audit.leak_count must be 0")

    return {
        "path": str(path),
        "readable": True,
        "evidence_level": evidence_level,
        "readiness_status": readiness_status,
        "readiness_issue_count": readiness_issue_count,
        "ground_truth_status": ground_truth_status,
        "path_leakage_status": path_leakage_status,
        "path_leakage_count": path_leakage_count,
        "self_check_ready": not issues,
        "issues": issues,
    }


def _summarize_truth_csv(path: Path) -> dict[str, Any]:
    schema_issues = review_ground_truth_csv_issues(path)
    return {
        "path": str(path),
        "rows": _csv_row_count(path),
        "schema_valid": not schema_issues,
        "schema_issues": schema_issues,
    }


def _summarize_review_decision_truth_csv(path: Path) -> dict[str, Any]:
    summary = summarize_review_decision_truth_csv(path)
    summary["path"] = str(path)
    return summary


def _summarize_dataset_strata_csv(path: Path, *, expected_sheet_count: int) -> dict[str, Any]:
    summary = summarize_dataset_strata_csv(path, expected_sheet_count=expected_sheet_count)
    summary["path"] = str(path)
    return summary


def _summarize_first_interactive_from_validations(validations: Sequence[dict[str, Any]]) -> dict[str, Any]:
    summaries = [
        _load_json(Path(item["summary_path"])) or {}
        for item in validations
        if item.get("completed_pairs", 0) > 0
    ]
    return summarize_first_interactive_readiness(summaries)


def _summarize_bbox_from_validations(validations: Sequence[dict[str, Any]]) -> dict[str, Any]:
    summaries = [
        _load_json(Path(item["summary_path"])) or {}
        for item in validations
        if item.get("completed_pairs", 0) > 0
    ]
    return summarize_bbox_quality(summaries)


def _summarize_operator_notes(path: Path) -> dict[str, Any]:
    text = read_operator_notes_text(path)
    missing = [
        check_id for check_id in REQUIRED_OPERATOR_WORKFLOW_CHECKS if not _workflow_check_is_checked(text, check_id)
    ]
    matched_role = _matched_operator_role(text)
    return {
        "path": str(path),
        "all_required_checked": not missing,
        "missing_checks": missing,
        "approved_structural_reviewer_role": bool(matched_role),
        "matched_reviewer_role": matched_role,
        "substantive_review_notes": operator_notes_have_substantive_review_notes(path),
    }


def _summarize_large_dwg_probe(path: Path | None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": str(path) if path else "",
        "readable": False,
        "passed": False,
        "elapsed_s": None,
        "total": 0,
        "change_zone_record_count": 0,
        "change_records_in_memory": 0,
        "progress_event_count": 0,
        "issues": [],
    }
    issues: list[str] = summary["issues"]
    if path is None:
        issues.append("--large-dwg-probe is required for customer-grade readiness")
        return summary
    if not path.exists():
        issues.append("large-DWG probe JSON does not exist")
        return summary
    payload = _load_json(path)
    if not isinstance(payload, dict):
        issues.append("large-DWG probe JSON is missing or unreadable")
        return summary

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    elapsed_s = _float(payload.get("elapsed_s"))
    total = _int(payload.get("total"))
    stream_count = _int(metadata.get("change_zone_record_count"))
    in_memory = _int(payload.get("change_records_in_memory") or metadata.get("change_records_in_memory"))
    progress_count = _int(payload.get("progress_event_count"))
    stream_bytes = _int(payload.get("stream_bytes"))
    progress_messages = [
        str(item)
        for item in payload.get("progress_messages", [])
        if isinstance(item, str)
    ]
    progress_messages.extend(
        str(event.get("message") or "")
        for event in payload.get("progress_events_tail") or []
        if isinstance(event, dict)
    )

    summary.update(
        {
            "readable": True,
            "elapsed_s": elapsed_s,
            "total": total,
            "change_zone_record_count": stream_count,
            "change_records_in_memory": in_memory,
            "large_drawing_mode": str(metadata.get("large_drawing_mode") or ""),
            "change_zone_stream_complete": metadata.get("change_zone_stream_complete") is True,
            "stream_exists": payload.get("stream_exists") is True,
            "stream_bytes": stream_bytes,
            "progress_event_count": progress_count,
        }
    )
    resource_summary = summarize_large_dwg_resource_probe(payload)
    summary.update(
        {
            "peak_rss_mb": resource_summary.get("peak_rss_mb"),
            "progress_max_gap_s": resource_summary.get("progress_max_gap_s"),
            "cancel_probe": resource_summary.get("cancel_probe"),
            "resource_probe_status": resource_summary.get("status"),
            "resource_probe_issues": resource_summary.get("issues", []),
        }
    )

    if elapsed_s <= 0 or elapsed_s > 120.0:
        issues.append("large-DWG probe elapsed_s must be >0 and <=120")
    if total < 100_000:
        issues.append("large-DWG probe total change records must be >=100000")
    if stream_count < 100_000:
        issues.append("large-DWG probe streamed record count must be >=100000")
    if total != stream_count:
        issues.append("large-DWG probe total must match streamed record count")
    if in_memory <= 0 or in_memory > 50_000:
        issues.append("large-DWG probe in-memory records must be >0 and <=50000")
    if metadata.get("large_drawing_mode") != "active":
        issues.append("large-DWG probe metadata.large_drawing_mode must be active")
    if metadata.get("change_zone_stream_complete") is not True:
        issues.append("large-DWG probe stream must be complete")
    if payload.get("stream_exists") is not True:
        issues.append("large-DWG probe stream file must exist")
    if stream_bytes <= 0:
        issues.append("large-DWG probe stream_bytes must be >0")
    if progress_count < 5:
        issues.append("large-DWG probe progress_event_count must be >=5")
    if not any(
        ("DXF_COMPARE_PROGRESS" in message)
        or ("DXF" in message and "compare" in message.lower())
        for message in progress_messages
    ):
        issues.append("large-DWG probe must include forwarded DXF compare progress")
    if resource_summary.get("status") != "passed":
        issues.extend(f"large-DWG resource probe: {issue}" for issue in resource_summary.get("issues", []))

    summary["passed"] = not issues
    return summary


def _summarize_drawing_groups(paths: set[Path]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for path in paths:
        parent = str(path.parent)
        group = groups.setdefault(parent, {"directory": parent, "dwg": 0, "dxf": 0, "pdf": 0, "total": 0})
        ext = path.suffix.lower().lstrip(".")
        if ext in group:
            group[ext] += 1
        group["total"] += 1
    return sorted(groups.values(), key=lambda item: (-int(item["total"]), str(item["directory"])))[:50]


def _workbench_acceptance_passed(path: Path) -> bool:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return False
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return False
    found: dict[str, bool] = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name") or "")
        for prefix in REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS:
            if name.startswith(prefix):
                found[prefix] = check.get("passed") is True
    return all(found.get(prefix) is True for prefix in REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS)


def _selected_zone_telemetry(summary: dict[str, Any], root: Path) -> bool:
    perf = summary.get("viewer_perf_summary")
    if isinstance(perf, dict) and _int(perf.get("zone_crop_count")) > 0:
        return True
    perf_path = root / "viewer" / "viewer_perf.json"
    payload = _load_json(perf_path)
    events = payload.get("events") if isinstance(payload, dict) else None
    return isinstance(events, list) and any(
        isinstance(event, dict) and event.get("event") == "zone_crop_render" for event in events
    )


def _top_review_queue_first(summary: dict[str, Any]) -> bool:
    queue = _nested(summary, "review_dashboard", "review_queue")
    if not isinstance(queue, dict):
        return False
    try:
        top_per_drawing = int(queue.get("top_per_drawing") or 0)
    except Exception:
        top_per_drawing = 0
    items = queue.get("items")
    return queue.get("mode") == "structural_core" and 3 <= top_per_drawing <= 5 and isinstance(items, list) and bool(items)


def _has_cad_pdf_block(root: Path, summary: dict[str, Any]) -> bool:
    if _int(_nested(summary, "matching", "cad_pdf_blocked_pairs")) > 0:
        return True
    blocked_csv = root / "blocked_pairs.csv"
    if not blocked_csv.exists():
        return False
    try:
        with open(blocked_csv, "r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                kinds = {str(row.get("a_kind") or "").lower(), str(row.get("b_kind") or "").lower()}
                reason = str(row.get("reason") or "").lower()
                if {"cad", "pdf"}.issubset(kinds) and ("block" in reason or "unsupported" in reason):
                    return True
    except Exception:
        return False
    return False


def _validation_kind(summary: dict[str, Any]) -> str:
    if _int(_nested(summary, "files", "a_kind_counts", "cad")) > 0 and _int(_nested(summary, "files", "b_kind_counts", "cad")) > 0:
        return "cad"
    if _int(_nested(summary, "files", "a_kind_counts", "pdf")) > 0 and _int(_nested(summary, "files", "b_kind_counts", "pdf")) > 0:
        return "pdf"
    return "mixed_or_blocked"


def _has_pdf_pdf(summary: dict[str, Any]) -> bool:
    return (
        _int(_nested(summary, "files", "a_kind_counts", "pdf")) > 0
        and _int(_nested(summary, "files", "b_kind_counts", "pdf")) > 0
        and "pdf" in _source_extensions(summary)
    )


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
    return {ext.lstrip(".") for ext in SUPPORTED_DRAWING_EXTENSIONS if ext in lower}


def _matched_operator_role(text: str) -> str:
    notes_role = _operator_role_from_notes_text(text)
    if not notes_role:
        return ""
    for role in sorted(APPROVED_OPERATOR_REVIEWER_ROLES, key=len, reverse=True):
        normalized_role = _normalize_operator_role(role)
        if normalized_role and normalized_role == notes_role:
            return role
    return ""


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


def _normalize_operator_role(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .lstrip("\ufeff")
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def _workflow_check_is_checked(text: str, check_id: str) -> bool:
    needle = check_id.lower()
    for line in text.splitlines():
        lowered = line.strip().lower()
        if needle in lowered and ("[x]" in lowered or "[done]" in lowered or "[pass]" in lowered):
            return True
    return False


def _csv_row_count(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return sum(1 for _ in csv.DictReader(handle))
    except Exception:
        return 0


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


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


def _format_number_arg(value: float) -> str:
    text = f"{float(value):.12g}"
    return text.rstrip("0").rstrip(".") if "." in text else text


if __name__ == "__main__":
    raise SystemExit(main())
