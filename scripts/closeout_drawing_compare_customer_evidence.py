# -*- coding: utf-8 -*-
"""Run Drawing Compare customer-evidence closeout without mixing proof runs.

The closeout flow chains the customer corpus validation outputs, optional
P5-G7 forced tile-cache eviction proof runs, manifest generation, and final
customer-grade audit. P5-G7 proof outputs are passed only through the explicit
proof channels, never as final audit corpus ``--results-dir`` values.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence


TILE_CACHE_MB_ENV_VAR = "DRAWING_COMPARE_TILE_CACHE_MB"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-checkout",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=(
            "Source checkout containing scripts/ and src/services/comparison/"
            "manifest_provenance.py. Required for customer-grade closeout, "
            "especially when invoked from a packaged cli/ folder."
        ),
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--out", type=Path, required=True, help="Closeout working output folder")
    parser.add_argument(
        "--standard-validation-manifest",
        type=Path,
        action="append",
        default=[],
        help=(
            "Manifest for a standard 20-50 sheet customer/customer-grade validation run. "
            "Each run is written under --out and counted as corpus evidence."
        ),
    )
    parser.add_argument(
        "--standard-results-dir",
        type=Path,
        action="append",
        default=[],
        help="Existing completed customer/customer-grade validation output. Repeatable.",
    )
    parser.add_argument(
        "--p5-g7-proof-validation-manifest",
        type=Path,
        action="append",
        default=[],
        help=(
            "Manifest for a controlled P5-G7 forced tile-cache eviction proof run. "
            "Outputs are preserved as proof only, not counted as corpus evidence."
        ),
    )
    parser.add_argument(
        "--p5-g7-tile-eviction-proof-dir",
        "--p5-g7-proof-dir",
        dest="p5_g7_tile_eviction_proof_dir",
        type=Path,
        action="append",
        default=[],
        help="Existing P5-G7 forced tile-cache eviction proof output. Repeatable.",
    )
    parser.add_argument(
        "--p5-g7-tile-eviction-release-manifest",
        type=Path,
        action="append",
        default=[],
        help="Release manifest from a forced tile-eviction proof run. Repeatable.",
    )
    parser.add_argument(
        "--require-p5-g7-tile-eviction-proof",
        action="store_true",
        help="Require a passing P5-G7 forced tile-eviction proof in inventory and manifest readiness.",
    )
    parser.add_argument(
        "--p5-g6-tile-cache-mb",
        type=float,
        help="Expected DRAWING_COMPARE_TILE_CACHE_MB cap for P5-G7 proof validation.",
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
    parser.add_argument("--inventory-json", type=Path, help="Inventory JSON output path")
    parser.add_argument("--customer-evidence-manifest", type=Path, required=True)
    parser.add_argument("--audit-json", type=Path, help="Final MVP exit audit JSON output path")
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--large-dwg-probe", type=Path, required=True)
    parser.add_argument("--review-ground-truth", type=Path, required=True)
    parser.add_argument("--review-decision-truth", type=Path, required=True)
    parser.add_argument("--dataset-strata", type=Path, required=True)
    parser.add_argument("--operator-notes-file", type=Path, required=True)
    parser.add_argument("--operator-screenshots-dir", type=Path)
    parser.add_argument("--confirmed-export-artifact", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument(
        "--dataset-source-kind",
        choices=("customer", "customer_grade"),
        default="customer_grade",
    )
    parser.add_argument("--dataset-source-description", required=True)
    parser.add_argument(
        "--dataset-approval-status",
        choices=("approved_for_mvp_exit", "draft"),
        default="approved_for_mvp_exit",
    )
    parser.add_argument("--dataset-approver", required=True)
    parser.add_argument("--ground-truth-owner", required=True)
    parser.add_argument(
        "--ground-truth-status",
        choices=("reviewed", "approved"),
        default="approved",
    )
    parser.add_argument("--operator-reviewer-role", default="structural_review_lead")
    parser.add_argument("--min-total-pairs", type=int, default=20)
    parser.add_argument("--max-total-pairs", type=int, default=50)
    parser.add_argument("--max-first-review-ready-s", type=float, default=1_800.0)
    parser.add_argument("--max-cold-zone-render-ms", type=float, default=10_000.0)
    parser.add_argument("--max-cache-hit-zone-render-ms", type=float, default=2_000.0)
    parser.add_argument("--strict-zone-render-budget", action="store_true")
    parser.add_argument("--export-profile", choices=("internal", "sharable"), default="sharable")
    parser.add_argument("--viewer-render-policy", choices=("lazy", "top-issues", "all"), default="top-issues")
    parser.add_argument("--selected-zone-evidence-per-pair", type=int, default=1)
    parser.add_argument(
        "--p5-g16-benchmark-json",
        "--p5-g16-real-corpus-replay",
        dest="p5_g16_benchmark_json",
        type=Path,
        action="append",
        default=[],
        help="Existing P5-G16 real-corpus replay JSON to forward to manifest preparation and final audit.",
    )
    parser.add_argument(
        "--skip-p5-g16-real-corpus-replay",
        action="store_true",
        help=(
            "Do not generate P5-G16 replay JSON before the final audit. "
            "Use only when valid --p5-g16-benchmark-json artifacts are supplied elsewhere."
        ),
    )
    parser.add_argument("--p5-g16-visits", type=int, default=100)
    parser.add_argument("--p5-g16-warmup-visits", type=int, default=20)
    parser.add_argument("--p5-g16-timeout-s", type=float, default=60.0)
    parser.add_argument(
        "--p5-g22-gui-soak-json",
        "--p5-g22-actual-gui-soak",
        dest="p5_g22_gui_soak_json",
        type=Path,
        action="append",
        default=[],
        help="Existing P5-G22 actual GUI soak JSON to forward to manifest preparation and final audit.",
    )
    parser.add_argument(
        "--skip-p5-g22-actual-gui-soak",
        action="store_true",
        help=(
            "Do not generate P5-G22 actual GUI soak JSON before the final audit. "
            "Use only when valid --p5-g22-gui-soak-json artifacts are supplied elsewhere."
        ),
    )
    parser.add_argument("--p5-g22-visits", type=int, default=100)
    parser.add_argument("--p5-g22-warmup-visits", type=int, default=20)
    parser.add_argument("--p5-g22-timeout-s", type=float, default=120.0)
    parser.add_argument("--p5-g22-zone-render-wait-ms", type=float, default=250.0)
    parser.add_argument("--p5-g22-min-page-navigation-count", type=int, default=0)
    parser.add_argument(
        "--p5-g27-selected-zone-crop-json",
        "--p5-g27-selected-zone-crop-soak",
        dest="p5_g27_selected_zone_crop_json",
        type=Path,
        action="append",
        default=[],
        help="Existing P5-G27 selected-zone crop-first JSON to forward to manifest preparation and final audit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print/write the command plan without running subprocesses.",
    )
    parser.add_argument(
        "--plan-json",
        type=Path,
        help="Write a JSON closeout command plan. Useful with --dry-run and for release review.",
    )
    parser.add_argument(
        "--readiness-json",
        type=Path,
        help=(
            "Write a machine-readable preflight/readiness report. Defaults to "
            "<out>/closeout_readiness.json."
        ),
    )
    parser.add_argument(
        "--failure-json",
        type=Path,
        help=(
            "Write a machine-readable failure report when a closeout subprocess "
            "returns non-zero. Defaults to <out>/closeout_failure.json."
        ),
    )
    parser.add_argument(
        "--portable-inventory-paths",
        action="store_true",
        help="Emit root_N path aliases in inventory JSON.",
    )

    args = parser.parse_args(argv)
    if not args.standard_validation_manifest and not args.standard_results_dir:
        parser.error(
            "provide at least one --standard-validation-manifest or --standard-results-dir "
            "for the customer corpus"
        )
    if args.p5_g6_tile_cache_mb is not None and args.p5_g6_tile_cache_mb <= 0:
        parser.error("--p5-g6-tile-cache-mb must be greater than 0")
    if args.p5_g16_visits <= 0:
        parser.error("--p5-g16-visits must be greater than 0")
    if args.p5_g16_warmup_visits < 0:
        parser.error("--p5-g16-warmup-visits must be >= 0")
    if args.p5_g16_timeout_s <= 0:
        parser.error("--p5-g16-timeout-s must be greater than 0")
    if args.p5_g22_visits <= 0:
        parser.error("--p5-g22-visits must be greater than 0")
    if args.p5_g22_warmup_visits < 0:
        parser.error("--p5-g22-warmup-visits must be >= 0")
    if args.p5_g22_timeout_s <= 0:
        parser.error("--p5-g22-timeout-s must be greater than 0")
    if args.p5_g22_zone_render_wait_ms < 0:
        parser.error("--p5-g22-zone-render-wait-ms must be >= 0")
    if args.p5_g22_min_page_navigation_count < 0:
        parser.error("--p5-g22-min-page-navigation-count must be >= 0")
    if args.p5_g7_proof_validation_manifest and args.p5_g6_tile_cache_mb is None:
        parser.error("--p5-g7-proof-validation-manifest requires --p5-g6-tile-cache-mb")
    if (
        args.require_p5_g7_tile_eviction_proof
        and not args.p5_g7_proof_validation_manifest
        and not args.p5_g7_tile_eviction_proof_dir
    ):
        parser.error(
            "--require-p5-g7-tile-eviction-proof requires a proof manifest or proof dir"
        )
    return args


def run_closeout(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    source_checkout = args.source_checkout.resolve()
    cli_dir = Path(__file__).resolve().parent
    readiness_json = (args.readiness_json or out_dir / "closeout_readiness.json").resolve()

    _preflight_or_exit(
        args,
        source_checkout=source_checkout,
        cli_dir=cli_dir,
        readiness_json=readiness_json,
    )

    leading_steps: list[dict[str, Any]] = []
    standard_result_dirs = [path.resolve() for path in args.standard_results_dir]
    for index, manifest in enumerate(args.standard_validation_manifest, start=1):
        validation_out = out_dir / f"standard_validation_{index}"
        leading_steps.append(
            {
                "name": f"standard_validation_{index}",
                "cwd": str(source_checkout),
                "command": _standard_validation_command(args, source_checkout, manifest, validation_out),
                "env_overrides": {},
            }
        )
        standard_result_dirs.append(validation_out.resolve())

    proof_dirs = [path.resolve() for path in args.p5_g7_tile_eviction_proof_dir]
    for index, manifest in enumerate(args.p5_g7_proof_validation_manifest, start=1):
        proof_out = out_dir / f"p5_g7_tile_eviction_proof_{index}"
        leading_steps.append(
            {
                "name": f"p5_g7_tile_eviction_proof_{index}",
                "cwd": str(source_checkout),
                "command": _p5_g7_proof_validation_command(args, source_checkout, manifest, proof_out),
                "env_overrides": _tile_cache_env_overrides(args),
            }
        )
        proof_dirs.append(proof_out.resolve())

    generated_p5_g16_benchmark_jsons = _planned_p5_g16_benchmark_jsons(
        standard_result_dirs,
        skip=args.skip_p5_g16_real_corpus_replay,
    )
    p5_g16_benchmark_jsons = _unique_paths(
        [*[path.resolve() for path in args.p5_g16_benchmark_json], *generated_p5_g16_benchmark_jsons]
    )
    generated_p5_g22_gui_soak_jsons = _planned_p5_g22_gui_soak_jsons(
        standard_result_dirs,
        skip=args.skip_p5_g22_actual_gui_soak,
    )
    p5_g22_gui_soak_jsons = _unique_paths(
        [*[path.resolve() for path in args.p5_g22_gui_soak_json], *generated_p5_g22_gui_soak_jsons]
    )
    p5_g27_selected_zone_crop_jsons = _unique_paths(
        [path.resolve() for path in args.p5_g27_selected_zone_crop_json]
    )

    inventory_json = (args.inventory_json or out_dir / "inventory.json").resolve()
    audit_json = (args.audit_json or out_dir / "mvp_exit_audit.json").resolve()
    failure_json = (args.failure_json or out_dir / "closeout_failure.json").resolve()

    plan = _build_command_plan(
        args,
        cli_dir=cli_dir,
        source_checkout=source_checkout,
        standard_result_dirs=standard_result_dirs,
        proof_dirs=proof_dirs,
        p5_g16_benchmark_jsons=p5_g16_benchmark_jsons,
        generated_p5_g16_benchmark_jsons=generated_p5_g16_benchmark_jsons,
        p5_g22_gui_soak_jsons=p5_g22_gui_soak_jsons,
        generated_p5_g22_gui_soak_jsons=generated_p5_g22_gui_soak_jsons,
        p5_g27_selected_zone_crop_jsons=p5_g27_selected_zone_crop_jsons,
        inventory_json=inventory_json,
        audit_json=audit_json,
        failure_json=failure_json,
        leading_steps=leading_steps,
    )
    _write_json(
        readiness_json,
        _build_readiness_report(
            args,
            status="ready_for_closeout",
            issues=[],
            source_checkout=source_checkout,
            cli_dir=cli_dir,
            readiness_json=readiness_json,
            plan=plan,
        ),
    )
    if args.plan_json:
        _write_json(args.plan_json, plan)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=True, indent=2))
        return {
            "schema_version": 1,
            "status": "dry_run_passed",
            "standard_result_dirs": [str(path) for path in standard_result_dirs],
            "p5_g7_tile_eviction_proof_dirs": [str(path) for path in proof_dirs],
            "p5_g16_benchmark_jsons": [str(path) for path in p5_g16_benchmark_jsons],
            "p5_g22_gui_soak_jsons": [str(path) for path in p5_g22_gui_soak_jsons],
            "p5_g27_selected_zone_crop_jsons": [
                str(path) for path in p5_g27_selected_zone_crop_jsons
            ],
            "inventory_json": str(inventory_json),
            "customer_evidence_manifest": str(args.customer_evidence_manifest.resolve()),
            "audit_json": str(audit_json),
            "plan_json": str(args.plan_json.resolve()) if args.plan_json else "",
            "readiness_json": str(readiness_json),
            "failure_json": str(failure_json),
        }

    completed_steps: list[str] = []
    if failure_json.is_file():
        failure_json.unlink()
    for index, step in enumerate(plan["steps"], start=1):
        run_result = _run(
            step["command"],
            cwd=Path(step["cwd"]),
            env_overrides=step.get("env_overrides") or {},
        )
        if run_result["returncode"] != 0:
            report = _build_failure_report(
                args,
                plan=plan,
                failed_step=step,
                failed_step_index=index,
                run_result=run_result,
                completed_steps=completed_steps,
                failure_json=failure_json,
            )
            _write_json(failure_json, report)
            print(
                f"Closeout subprocess failed at step {index} ({step['name']}): "
                f"{run_result['failure_kind']}; "
                f"failure report: {failure_json}",
                file=sys.stderr,
            )
            raise SystemExit(run_result["returncode"])
        completed_steps.append(str(step["name"]))

    return {
        "schema_version": 1,
        "status": "passed",
        "standard_result_dirs": [str(path) for path in standard_result_dirs],
        "p5_g7_tile_eviction_proof_dirs": [str(path) for path in proof_dirs],
        "p5_g16_benchmark_jsons": [str(path) for path in p5_g16_benchmark_jsons],
        "p5_g22_gui_soak_jsons": [str(path) for path in p5_g22_gui_soak_jsons],
        "p5_g27_selected_zone_crop_jsons": [
            str(path) for path in p5_g27_selected_zone_crop_jsons
        ],
        "inventory_json": str(inventory_json),
        "customer_evidence_manifest": str(args.customer_evidence_manifest.resolve()),
        "audit_json": str(audit_json),
        "plan_json": str(args.plan_json.resolve()) if args.plan_json else "",
        "readiness_json": str(readiness_json),
        "failure_json": str(failure_json),
    }


def _preflight_or_exit(
    args: argparse.Namespace,
    *,
    source_checkout: Path,
    cli_dir: Path,
    readiness_json: Path,
) -> None:
    issues = _preflight_issues(args, source_checkout=source_checkout, cli_dir=cli_dir)
    if issues:
        _write_json_best_effort(
            readiness_json,
            _build_readiness_report(
                args,
                status="preflight_failed",
                issues=issues,
                source_checkout=source_checkout,
                cli_dir=cli_dir,
                readiness_json=readiness_json,
                plan=None,
            ),
        )
        print("Closeout preflight failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        print(f"Readiness report: {readiness_json}", file=sys.stderr)
        raise SystemExit(2)


def _preflight_issues(
    args: argparse.Namespace,
    *,
    source_checkout: Path,
    cli_dir: Path,
) -> list[str]:
    issues: list[str] = []
    if not source_checkout.exists():
        issues.append(f"--source-checkout does not exist: {source_checkout}")
    elif not source_checkout.is_dir():
        issues.append(f"--source-checkout is not a directory: {source_checkout}")
    source_required = [
        source_checkout / "scripts" / "validate_drawing_compare_realset.py",
        source_checkout / "scripts" / "inventory_drawing_compare_customer_evidence.py",
        source_checkout / "scripts" / "prepare_drawing_compare_customer_evidence.py",
        source_checkout / "scripts" / "audit_drawing_compare_mvp_exit.py",
        source_checkout / "scripts" / "benchmark_real_corpus_replay.py",
        source_checkout / "scripts" / "benchmark_actual_gui_soak.py",
        source_checkout / "src" / "services" / "comparison" / "manifest_provenance.py",
    ]
    for path in source_required:
        if not path.exists():
            issues.append(f"source checkout missing required file: {path}")
    cli_required = [
        cli_dir / "inventory_drawing_compare_customer_evidence.py",
        cli_dir / "prepare_drawing_compare_customer_evidence.py",
        cli_dir / "audit_drawing_compare_mvp_exit.py",
        cli_dir / "benchmark_real_corpus_replay.py",
        cli_dir / "benchmark_actual_gui_soak.py",
    ]
    for path in cli_required:
        if not path.exists():
            issues.append(f"cli evidence script missing: {path}")
    for label, path in (
        ("--release-manifest", args.release_manifest),
        ("--large-dwg-probe", args.large_dwg_probe),
        ("--review-ground-truth", args.review_ground_truth),
        ("--review-decision-truth", args.review_decision_truth),
        ("--dataset-strata", args.dataset_strata),
        ("--operator-notes-file", args.operator_notes_file),
        ("--confirmed-export-artifact", args.confirmed_export_artifact),
    ):
        _require_file(issues, label, path)
    if args.operator_screenshots_dir:
        _require_dir(issues, "--operator-screenshots-dir", args.operator_screenshots_dir)
    for path in args.standard_validation_manifest:
        _require_file(issues, "--standard-validation-manifest", path)
    for path in args.p5_g7_proof_validation_manifest:
        _require_file(issues, "--p5-g7-proof-validation-manifest", path)
    for path in args.p5_g7_tile_eviction_release_manifest:
        _require_file(issues, "--p5-g7-tile-eviction-release-manifest", path)
    for path in args.p5_g16_benchmark_json:
        _require_file(issues, "--p5-g16-benchmark-json", path)
    for path in args.p5_g22_gui_soak_json:
        _require_file(issues, "--p5-g22-gui-soak-json", path)
    for path in args.p5_g27_selected_zone_crop_json:
        _require_file(issues, "--p5-g27-selected-zone-crop-json", path)
    for path in args.standard_results_dir:
        _require_validation_output(issues, "--standard-results-dir", path)
        if _is_forced_tile_eviction_output(path):
            issues.append(
                "--standard-results-dir appears to be a forced P5-G7 tile-eviction proof; "
                f"use --p5-g7-tile-eviction-proof-dir instead: {path}"
            )
    for path in args.p5_g7_tile_eviction_proof_dir:
        _require_validation_output(issues, "--p5-g7-tile-eviction-proof-dir", path)
        if not _is_forced_tile_eviction_output(path):
            issues.append(
                "--p5-g7-tile-eviction-proof-dir does not contain forced tile-eviction evidence: "
                f"{path}"
            )
    issues.extend(_result_dir_collision_issues(args))
    for label, path in (
        ("--customer-evidence-manifest parent", args.customer_evidence_manifest.parent),
        ("--inventory-json parent", args.inventory_json.parent if args.inventory_json else args.out),
        ("--audit-json parent", args.audit_json.parent if args.audit_json else args.out),
        ("--plan-json parent", args.plan_json.parent if args.plan_json else args.out),
        ("--readiness-json parent", args.readiness_json.parent if args.readiness_json else args.out),
        ("--failure-json parent", args.failure_json.parent if args.failure_json else args.out),
    ):
        if path and path.exists() and not path.is_dir():
            issues.append(f"{label} exists but is not a directory: {path}")
    return issues


def _build_readiness_report(
    args: argparse.Namespace,
    *,
    status: str,
    issues: Sequence[str],
    source_checkout: Path,
    cli_dir: Path,
    readiness_json: Path,
    plan: dict[str, Any] | None,
) -> dict[str, Any]:
    out_dir = args.out.resolve()
    inventory_json = (args.inventory_json or out_dir / "inventory.json").resolve()
    audit_json = (args.audit_json or out_dir / "mvp_exit_audit.json").resolve()
    failure_json = (args.failure_json or out_dir / "closeout_failure.json").resolve()
    plan_json = args.plan_json.resolve() if args.plan_json else None
    return {
        "schema_version": 1,
        "status": status,
        "readiness_json": str(readiness_json),
        "source_checkout": str(source_checkout),
        "cli_dir": str(cli_dir),
        "out": str(out_dir),
        "preflight": {
            "status": "passed" if not issues else "failed",
            "issue_count": len(issues),
            "issues": list(issues),
        },
        "inputs": {
            "standard_validation_manifests": _path_strings(args.standard_validation_manifest),
            "standard_results_dirs": _path_strings(args.standard_results_dir),
            "p5_g7_proof_validation_manifests": _path_strings(args.p5_g7_proof_validation_manifest),
            "p5_g7_tile_eviction_proof_dirs": _path_strings(args.p5_g7_tile_eviction_proof_dir),
            "p5_g7_tile_eviction_release_manifests": _path_strings(
                args.p5_g7_tile_eviction_release_manifest
            ),
            "p5_g16_benchmark_jsons": _path_strings(args.p5_g16_benchmark_json),
            "p5_g22_gui_soak_jsons": _path_strings(args.p5_g22_gui_soak_json),
            "p5_g27_selected_zone_crop_jsons": _path_strings(
                args.p5_g27_selected_zone_crop_json
            ),
            "release_manifest": str(args.release_manifest.resolve()),
            "large_dwg_probe": str(args.large_dwg_probe.resolve()),
            "review_ground_truth": str(args.review_ground_truth.resolve()),
            "review_decision_truth": str(args.review_decision_truth.resolve()),
            "dataset_strata": str(args.dataset_strata.resolve()),
            "operator_notes_file": str(args.operator_notes_file.resolve()),
            "operator_screenshots_dir": (
                str(args.operator_screenshots_dir.resolve())
                if args.operator_screenshots_dir
                else ""
            ),
            "confirmed_export_artifact": str(args.confirmed_export_artifact.resolve()),
        },
        "outputs": {
            "plan_json": str(plan_json) if plan_json else "",
            "readiness_json": str(readiness_json),
            "failure_json": str(failure_json),
            "inventory_json": str(inventory_json),
            "customer_evidence_manifest": str(args.customer_evidence_manifest.resolve()),
            "audit_json": str(audit_json),
        },
        "routing_expectations": {
            "require_p5_g7_tile_eviction_proof": bool(args.require_p5_g7_tile_eviction_proof),
            "p5_g6_tile_cache_mb": (
                _format_number(args.p5_g6_tile_cache_mb)
                if args.p5_g6_tile_cache_mb is not None
                else ""
            ),
            "standard_result_count": len(args.standard_results_dir)
            + len(args.standard_validation_manifest),
            "proof_result_count": len(args.p5_g7_tile_eviction_proof_dir)
            + len(args.p5_g7_proof_validation_manifest),
            "p5_g16_real_corpus_replay_generation_enabled": not bool(
                args.skip_p5_g16_real_corpus_replay
            ),
            "p5_g16_visits": args.p5_g16_visits,
            "p5_g16_warmup_visits": args.p5_g16_warmup_visits,
            "p5_g16_timeout_s": args.p5_g16_timeout_s,
            "p5_g22_actual_gui_soak_generation_enabled": not bool(
                args.skip_p5_g22_actual_gui_soak
            ),
            "p5_g22_visits": args.p5_g22_visits,
            "p5_g22_warmup_visits": args.p5_g22_warmup_visits,
            "p5_g22_timeout_s": args.p5_g22_timeout_s,
            "p5_g22_zone_render_wait_ms": args.p5_g22_zone_render_wait_ms,
            "p5_g22_min_page_navigation_count": args.p5_g22_min_page_navigation_count,
            "p5_g27_selected_zone_crop_json_count": len(args.p5_g27_selected_zone_crop_json),
        },
        "plan": _readiness_plan_summary(plan),
    }


def _readiness_plan_summary(plan: dict[str, Any] | None) -> dict[str, Any]:
    if not plan:
        return {
            "available": False,
            "step_count": 0,
            "steps": [],
            "invariants": {},
        }
    return {
        "available": True,
        "step_count": len(plan["steps"]),
        "steps": [
            {
                "name": str(step["name"]),
                "cwd": str(step["cwd"]),
                "env_overrides": dict(step.get("env_overrides") or {}),
                "command_context": _command_context(step["command"]),
            }
            for step in plan["steps"]
        ],
        "invariants": plan.get("invariants", {}),
    }


def _path_strings(paths: Sequence[Path]) -> list[str]:
    return [str(path.resolve()) for path in paths]


def _result_dir_collision_issues(args: argparse.Namespace) -> list[str]:
    issues: list[str] = []
    standard_keys: dict[str, str] = {}
    proof_keys: dict[str, str] = {}
    generated_standard = [
        args.out / f"standard_validation_{index}"
        for index, _ in enumerate(args.standard_validation_manifest, start=1)
    ]
    generated_proof = [
        args.out / f"p5_g7_tile_eviction_proof_{index}"
        for index, _ in enumerate(args.p5_g7_proof_validation_manifest, start=1)
    ]
    for label, paths, seen in (
        ("--standard-results-dir", args.standard_results_dir, standard_keys),
        ("--p5-g7-tile-eviction-proof-dir", args.p5_g7_tile_eviction_proof_dir, proof_keys),
    ):
        for path in paths:
            key = _path_key(path)
            if key in seen:
                issues.append(f"{label} duplicated: {seen[key]} and {path}")
            seen[key] = str(path)
    for path in generated_standard:
        key = _path_key(path)
        if key in standard_keys:
            issues.append(
                "--standard-results-dir collides with generated --standard-validation-manifest output: "
                f"{standard_keys[key]} and {path}"
            )
        if key in proof_keys:
            issues.append(
                "--p5-g7-tile-eviction-proof-dir collides with generated standard validation output: "
                f"{proof_keys[key]} and {path}"
            )
    for path in generated_proof:
        key = _path_key(path)
        if key in standard_keys:
            issues.append(
                "--standard-results-dir collides with generated P5-G7 proof output: "
                f"{standard_keys[key]} and {path}"
            )
        if key in proof_keys:
            issues.append(
                "--p5-g7-tile-eviction-proof-dir collides with generated proof validation output: "
                f"{proof_keys[key]} and {path}"
            )
    overlap = set(standard_keys) & set(proof_keys)
    for key in sorted(overlap):
        issues.append(
            "validation output cannot be both standard corpus and P5-G7 proof: "
            f"{standard_keys[key]} and {proof_keys[key]}"
        )
    return issues


def _require_file(issues: list[str], label: str, path: Path) -> None:
    if not path.exists():
        issues.append(f"{label} does not exist: {path}")
    elif not path.is_file():
        issues.append(f"{label} is not a file: {path}")


def _require_dir(issues: list[str], label: str, path: Path) -> None:
    if not path.exists():
        issues.append(f"{label} does not exist: {path}")
    elif not path.is_dir():
        issues.append(f"{label} is not a directory: {path}")


def _require_validation_output(issues: list[str], label: str, path: Path) -> None:
    _require_dir(issues, label, path)
    if not path.exists() or not path.is_dir():
        return
    if not (path / "validation_summary.json").is_file():
        issues.append(f"{label} missing validation_summary.json: {path}")
    if not (path / "_SUCCESS").is_file():
        issues.append(f"{label} missing _SUCCESS sentinel: {path}")


def _is_forced_tile_eviction_output(path: Path) -> bool:
    summary = _load_json(path / "validation_summary.json")
    if not isinstance(summary, dict):
        return False
    gate = summary.get("p5_g3_realset_gate")
    if not isinstance(gate, dict):
        return False
    evidence = gate.get("evidence")
    if not isinstance(evidence, dict):
        return False
    tile = evidence.get("tile_manifest")
    return (
        gate.get("requested") is True
        and isinstance(tile, dict)
        and tile.get("require_eviction") is True
    )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _unique_paths(paths: Sequence[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.resolve()
        key = _path_key(resolved)
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _planned_p5_g16_benchmark_jsons(
    standard_result_dirs: Sequence[Path],
    *,
    skip: bool,
) -> list[Path]:
    if skip:
        return []
    return [
        path.resolve() / "p5_g16_real_corpus_replay.json"
        for path in standard_result_dirs
        if _should_generate_p5_g16_replay(path)
    ]


def _planned_p5_g22_gui_soak_jsons(
    standard_result_dirs: Sequence[Path],
    *,
    skip: bool,
) -> list[Path]:
    if skip:
        return []
    return [
        path.resolve() / "p5_g22_actual_gui_soak.json"
        for path in standard_result_dirs
        if _should_generate_p5_g22_gui_soak(path)
    ]


def _should_generate_p5_g16_replay(result_dir: Path) -> bool:
    summary_path = result_dir / "validation_summary.json"
    if not summary_path.exists():
        return True
    summary = _load_json(summary_path)
    if not isinstance(summary, dict):
        return False
    completed_pairs = _safe_int(
        ((summary.get("comparison") or {}).get("completed_pairs"))
        if isinstance(summary.get("comparison"), dict)
        else None
    )
    return completed_pairs > 0


def _should_generate_p5_g22_gui_soak(result_dir: Path) -> bool:
    return _should_generate_p5_g16_replay(result_dir)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _standard_validation_command(
    args: argparse.Namespace,
    source_checkout: Path,
    manifest: Path,
    output_dir: Path,
) -> list[str]:
    return [
        args.python,
        str(source_checkout / "scripts" / "validate_drawing_compare_realset.py"),
        "--manifest",
        str(manifest),
        "--out",
        str(output_dir),
        "--export-profile",
        args.export_profile,
        "--quality-gate",
        "--p5-g3-realset-gate",
        "--change-zone-report",
        "--executive-review",
        "--review-dashboard",
        "--export-viewer-package",
        "--viewer-render-policy",
        args.viewer_render_policy,
        "--viewer-perf-log",
        "--render-selected-zone-evidence",
        "--selected-zone-evidence-per-pair",
        str(args.selected_zone_evidence_per_pair),
        "--export-marked-pdf",
        "--marked-pdf-mode",
        "selected",
    ]


def _build_command_plan(
    args: argparse.Namespace,
    *,
    cli_dir: Path,
    source_checkout: Path,
    standard_result_dirs: Sequence[Path],
    proof_dirs: Sequence[Path],
    p5_g16_benchmark_jsons: Sequence[Path],
    generated_p5_g16_benchmark_jsons: Sequence[Path],
    p5_g22_gui_soak_jsons: Sequence[Path],
    generated_p5_g22_gui_soak_jsons: Sequence[Path],
    p5_g27_selected_zone_crop_jsons: Sequence[Path],
    inventory_json: Path,
    audit_json: Path,
    failure_json: Path,
    leading_steps: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    steps = [
        *leading_steps,
        {
            "name": "inventory",
            "cwd": str(source_checkout),
            "env_overrides": {},
            "command": _inventory_command(
                args,
                cli_dir,
                source_checkout,
                standard_result_dirs=standard_result_dirs,
                proof_dirs=proof_dirs,
                inventory_json=inventory_json,
            ),
        },
        {
            "name": "prepare_customer_evidence_manifest",
            "cwd": str(source_checkout),
            "env_overrides": {},
            "command": _prepare_command(
                args,
                cli_dir,
                source_checkout,
                standard_result_dirs=standard_result_dirs,
                proof_dirs=proof_dirs,
                p5_g16_benchmark_jsons=p5_g16_benchmark_jsons,
                p5_g22_gui_soak_jsons=p5_g22_gui_soak_jsons,
                p5_g27_selected_zone_crop_jsons=p5_g27_selected_zone_crop_jsons,
            ),
        },
        *[
            {
                "name": f"p5_g16_real_corpus_replay_{index}",
                "cwd": str(source_checkout),
                "env_overrides": {},
                "command": _p5_g16_replay_command(
                    args,
                    cli_dir,
                    source_checkout,
                    result_dir=output_json.parent,
                    output_json=output_json,
                ),
            }
            for index, output_json in enumerate(generated_p5_g16_benchmark_jsons, start=1)
        ],
        *[
            {
                "name": f"p5_g22_actual_gui_soak_{index}",
                "cwd": str(source_checkout),
                "env_overrides": {},
                "command": _p5_g22_gui_soak_command(
                    args,
                    cli_dir,
                    source_checkout,
                    result_dir=output_json.parent,
                    output_json=output_json,
                ),
            }
            for index, output_json in enumerate(generated_p5_g22_gui_soak_jsons, start=1)
        ],
        {
            "name": "final_customer_grade_audit",
            "cwd": str(source_checkout),
            "env_overrides": {},
            "command": _audit_command(
                args,
                cli_dir,
                source_checkout,
                standard_result_dirs=standard_result_dirs,
                p5_g16_benchmark_jsons=p5_g16_benchmark_jsons,
                p5_g22_gui_soak_jsons=p5_g22_gui_soak_jsons,
                p5_g27_selected_zone_crop_jsons=p5_g27_selected_zone_crop_jsons,
                audit_json=audit_json,
            ),
        },
    ]
    audit_results = _values_after(steps[-1]["command"], "--results-dir")
    return {
        "schema_version": 1,
        "source_checkout": str(source_checkout),
        "standard_result_dirs": [str(path) for path in standard_result_dirs],
        "p5_g7_tile_eviction_proof_dirs": [str(path) for path in proof_dirs],
        "p5_g16_benchmark_jsons": [str(path) for path in p5_g16_benchmark_jsons],
        "generated_p5_g16_benchmark_jsons": [
            str(path) for path in generated_p5_g16_benchmark_jsons
        ],
        "p5_g22_gui_soak_jsons": [str(path) for path in p5_g22_gui_soak_jsons],
        "generated_p5_g22_gui_soak_jsons": [
            str(path) for path in generated_p5_g22_gui_soak_jsons
        ],
        "p5_g27_selected_zone_crop_jsons": [
            str(path) for path in p5_g27_selected_zone_crop_jsons
        ],
        "inventory_json": str(inventory_json),
        "customer_evidence_manifest": str(args.customer_evidence_manifest.resolve()),
        "audit_json": str(audit_json),
        "failure_json": str(failure_json),
        "invariants": {
            "final_audit_results_dir_count": len(audit_results),
            "proof_dirs_excluded_from_final_audit_results_dir": all(
                str(path) not in audit_results for path in proof_dirs
            ),
            "final_audit_results_dirs_equal_standard_result_dirs": audit_results
            == [str(path) for path in standard_result_dirs],
            "final_audit_p5_g16_benchmark_jsons_equal_plan": _values_after(
                steps[-1]["command"],
                "--p5-g16-benchmark-json",
            )
            == [str(path) for path in p5_g16_benchmark_jsons],
            "final_audit_p5_g22_gui_soak_jsons_equal_plan": _values_after(
                steps[-1]["command"],
                "--p5-g22-gui-soak-json",
            )
            == [str(path) for path in p5_g22_gui_soak_jsons],
            "final_audit_p5_g27_selected_zone_crop_jsons_equal_plan": _values_after(
                steps[-1]["command"],
                "--p5-g27-selected-zone-crop-json",
            )
            == [str(path) for path in p5_g27_selected_zone_crop_jsons],
        },
        "steps": steps,
    }


def _p5_g7_proof_validation_command(
    args: argparse.Namespace,
    source_checkout: Path,
    manifest: Path,
    output_dir: Path,
) -> list[str]:
    command = _standard_validation_command(args, source_checkout, manifest, output_dir)
    command.extend(
        [
            "--p5-g3-require-tile-eviction",
            "--p5-g3-min-tile-evicted-pairs",
            str(args.p5_g3_min_tile_evicted_pairs),
            "--p5-g3-min-tile-evicted-bytes",
            str(args.p5_g3_min_tile_evicted_bytes),
            "--p5-g6-tile-cache-mb",
            _format_number(args.p5_g6_tile_cache_mb),
        ]
    )
    return command


def _p5_g16_replay_command(
    args: argparse.Namespace,
    cli_dir: Path,
    source_checkout: Path,
    *,
    result_dir: Path,
    output_json: Path,
) -> list[str]:
    return [
        args.python,
        str(_evidence_script(source_checkout, cli_dir, "benchmark_real_corpus_replay.py")),
        "--validation-summary",
        str(result_dir / "validation_summary.json"),
        "--output-json",
        str(output_json),
        "--customer-evidence-manifest",
        str(args.customer_evidence_manifest),
        "--require-customer-corpus",
        "--min-customer-sheet-count",
        str(args.min_total_pairs),
        "--max-customer-sheet-count",
        str(args.max_total_pairs),
        "--visits",
        str(args.p5_g16_visits),
        "--warmup-visits",
        str(args.p5_g16_warmup_visits),
        "--timeout-s",
        _format_number(args.p5_g16_timeout_s),
    ]


def _p5_g22_gui_soak_command(
    args: argparse.Namespace,
    cli_dir: Path,
    source_checkout: Path,
    *,
    result_dir: Path,
    output_json: Path,
) -> list[str]:
    return [
        args.python,
        str(_evidence_script(source_checkout, cli_dir, "benchmark_actual_gui_soak.py")),
        "--validation-summary",
        str(result_dir / "validation_summary.json"),
        "--output-json",
        str(output_json),
        "--customer-evidence-manifest",
        str(args.customer_evidence_manifest),
        "--require-customer-corpus",
        "--min-customer-sheet-count",
        str(args.min_total_pairs),
        "--max-customer-sheet-count",
        str(args.max_total_pairs),
        "--visits",
        str(args.p5_g22_visits),
        "--warmup-visits",
        str(args.p5_g22_warmup_visits),
        "--timeout-s",
        _format_number(args.p5_g22_timeout_s),
        "--zone-render-wait-ms",
        _format_number(args.p5_g22_zone_render_wait_ms),
        "--min-page-navigation-count",
        str(args.p5_g22_min_page_navigation_count),
    ]


def _inventory_command(
    args: argparse.Namespace,
    cli_dir: Path,
    source_checkout: Path,
    *,
    standard_result_dirs: Sequence[Path],
    proof_dirs: Sequence[Path],
    inventory_json: Path,
) -> list[str]:
    command = [
        args.python,
        str(_evidence_script(source_checkout, cli_dir, "inventory_drawing_compare_customer_evidence.py")),
        "--large-dwg-probe",
        str(args.large_dwg_probe),
        "--min-total-pairs",
        str(args.min_total_pairs),
        "--max-total-pairs",
        str(args.max_total_pairs),
        "--out",
        str(inventory_json),
    ]
    for root in [*standard_result_dirs, *proof_dirs, args.release_manifest.parent]:
        command.extend(["--root", str(root)])
    if args.require_p5_g7_tile_eviction_proof:
        command.append("--require-p5-g7-tile-eviction-proof")
    if args.p5_g6_tile_cache_mb is not None:
        command.extend(["--p5-g6-tile-cache-mb", _format_number(args.p5_g6_tile_cache_mb)])
    if args.portable_inventory_paths:
        command.append("--portable-paths")
    return command


def _prepare_command(
    args: argparse.Namespace,
    cli_dir: Path,
    source_checkout: Path,
    *,
    standard_result_dirs: Sequence[Path],
    proof_dirs: Sequence[Path],
    p5_g16_benchmark_jsons: Sequence[Path],
    p5_g22_gui_soak_jsons: Sequence[Path],
    p5_g27_selected_zone_crop_jsons: Sequence[Path],
) -> list[str]:
    command = [
        args.python,
        str(_evidence_script(source_checkout, cli_dir, "prepare_drawing_compare_customer_evidence.py")),
        "--out",
        str(args.customer_evidence_manifest),
        "--dataset-id",
        args.dataset_id,
        "--dataset-source-kind",
        args.dataset_source_kind,
        "--dataset-source-description",
        args.dataset_source_description,
        "--dataset-approval-status",
        args.dataset_approval_status,
        "--dataset-approver",
        args.dataset_approver,
        "--ground-truth-owner",
        args.ground_truth_owner,
        "--review-ground-truth",
        str(args.review_ground_truth),
        "--ground-truth-status",
        args.ground_truth_status,
        "--review-decision-truth",
        str(args.review_decision_truth),
        "--dataset-strata",
        str(args.dataset_strata),
        "--large-dwg-probe",
        str(args.large_dwg_probe),
        "--operator-reviewer-role",
        args.operator_reviewer_role,
        "--operator-notes-file",
        str(args.operator_notes_file),
        "--confirmed-export-artifact",
        str(args.confirmed_export_artifact),
        "--min-total-pairs",
        str(args.min_total_pairs),
        "--max-total-pairs",
        str(args.max_total_pairs),
        "--max-first-review-ready-s",
        _format_number(args.max_first_review_ready_s),
        "--max-cold-zone-render-ms",
        _format_number(args.max_cold_zone_render_ms),
        "--max-cache-hit-zone-render-ms",
        _format_number(args.max_cache_hit_zone_render_ms),
    ]
    for result_dir in standard_result_dirs:
        command.extend(["--results-dir", str(result_dir)])
    for proof_dir in proof_dirs:
        command.extend(["--p5-g7-tile-eviction-proof-dir", str(proof_dir)])
    for benchmark_json in p5_g16_benchmark_jsons:
        command.extend(["--p5-g16-benchmark-json", str(benchmark_json)])
    for soak_json in p5_g22_gui_soak_jsons:
        command.extend(["--p5-g22-gui-soak-json", str(soak_json)])
    for crop_json in p5_g27_selected_zone_crop_jsons:
        command.extend(["--p5-g27-selected-zone-crop-json", str(crop_json)])
    for release_manifest in args.p5_g7_tile_eviction_release_manifest:
        command.extend(["--p5-g7-tile-eviction-release-manifest", str(release_manifest)])
    if args.operator_screenshots_dir:
        command.extend(["--operator-screenshots-dir", str(args.operator_screenshots_dir)])
    if args.require_p5_g7_tile_eviction_proof:
        command.append("--require-p5-g7-tile-eviction-proof")
    if args.p5_g6_tile_cache_mb is not None:
        command.extend(["--p5-g6-tile-cache-mb", _format_number(args.p5_g6_tile_cache_mb)])
    if args.strict_zone_render_budget:
        command.append("--strict-zone-render-budget")
    return command


def _audit_command(
    args: argparse.Namespace,
    cli_dir: Path,
    source_checkout: Path,
    *,
    standard_result_dirs: Sequence[Path],
    p5_g16_benchmark_jsons: Sequence[Path],
    p5_g22_gui_soak_jsons: Sequence[Path],
    p5_g27_selected_zone_crop_jsons: Sequence[Path],
    audit_json: Path,
) -> list[str]:
    command = [
        args.python,
        str(_evidence_script(source_checkout, cli_dir, "audit_drawing_compare_mvp_exit.py")),
        "--release-manifest",
        str(args.release_manifest),
        "--large-dwg-probe",
        str(args.large_dwg_probe),
        "--require-large-dwg-probe",
        "--customer-evidence-manifest",
        str(args.customer_evidence_manifest),
        "--evidence-level",
        "customer_grade",
        "--require-p5-g3-realset-gate",
        "--min-total-pairs",
        str(args.min_total_pairs),
        "--max-total-pairs",
        str(args.max_total_pairs),
        "--max-first-review-ready-s",
        _format_number(args.max_first_review_ready_s),
        "--max-cold-zone-render-ms",
        _format_number(args.max_cold_zone_render_ms),
        "--max-cache-hit-zone-render-ms",
        _format_number(args.max_cache_hit_zone_render_ms),
        "--out",
        str(audit_json),
    ]
    for result_dir in standard_result_dirs:
        command.extend(["--results-dir", str(result_dir)])
    for benchmark_json in p5_g16_benchmark_jsons:
        command.extend(["--p5-g16-benchmark-json", str(benchmark_json)])
    for soak_json in p5_g22_gui_soak_jsons:
        command.extend(["--p5-g22-gui-soak-json", str(soak_json)])
    for crop_json in p5_g27_selected_zone_crop_jsons:
        command.extend(["--p5-g27-selected-zone-crop-json", str(crop_json)])
    if args.strict_zone_render_budget:
        command.append("--strict-zone-render-budget")
    return command


def _evidence_script(source_checkout: Path, cli_dir: Path, name: str) -> Path:
    source_script = source_checkout / "scripts" / name
    if source_script.exists():
        return source_script
    return cli_dir / name


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    printable = " ".join(str(part) for part in command)
    print(f"+ {printable}")
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd),
            env=env,
            check=False,
        )
    except Exception as exc:
        elapsed_s = time.perf_counter() - started
        return {
            "returncode": 1,
            "elapsed_s": round(elapsed_s, 6),
            "failure_kind": "spawn_error",
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "stdout_tail": "",
            "stderr_tail": "",
        }
    elapsed_s = time.perf_counter() - started
    return {
        "returncode": int(completed.returncode),
        "elapsed_s": round(elapsed_s, 6),
        "failure_kind": "subprocess_nonzero_exit",
        "stdout_tail": _tail_text(getattr(completed, "stdout", None)),
        "stderr_tail": _tail_text(getattr(completed, "stderr", None)),
    }


def _build_failure_report(
    args: argparse.Namespace,
    *,
    plan: dict[str, Any],
    failed_step: dict[str, Any],
    failed_step_index: int,
    run_result: dict[str, Any],
    completed_steps: Sequence[str],
    failure_json: Path,
) -> dict[str, Any]:
    command = [str(part) for part in failed_step["command"]]
    steps = list(plan["steps"])
    remaining_steps = [str(step["name"]) for step in steps[failed_step_index:]]
    failure_kind = str(run_result.get("failure_kind", "subprocess_nonzero_exit"))
    failed_step_report: dict[str, Any] = {
        "index": failed_step_index,
        "name": str(failed_step["name"]),
        "cwd": str(failed_step["cwd"]),
        "command": command,
        "returncode": run_result["returncode"],
        "elapsed_s": run_result["elapsed_s"],
        "failure_kind": failure_kind,
        "env_overrides": dict(failed_step.get("env_overrides") or {}),
        "command_context": _command_context(command),
        "stdout_tail": run_result.get("stdout_tail", ""),
        "stderr_tail": run_result.get("stderr_tail", ""),
    }
    if "exception_type" in run_result:
        failed_step_report["exception_type"] = run_result["exception_type"]
        failed_step_report["exception_message"] = run_result.get("exception_message", "")
    return {
        "schema_version": 1,
        "status": "failed",
        "error": "closeout subprocess failed",
        "failure_kind": failure_kind,
        "failed_step_index": failed_step_index,
        "failed_step_name": str(failed_step["name"]),
        "failed_returncode": run_result["returncode"],
        "failure_json": str(failure_json),
        "plan_json": str(args.plan_json.resolve()) if args.plan_json else "",
        "source_checkout": plan.get("source_checkout", ""),
        "plan_invariants": plan.get("invariants", {}),
        "completed_steps": list(completed_steps),
        "remaining_steps": remaining_steps,
        "failed_step": failed_step_report,
        "triage_hints": _triage_hints_for_step(str(failed_step["name"])),
        "stdout_stderr": {
            "capture_mode": "inherited_console",
            "note": "Inspect the console or parent runner log for subprocess stdout/stderr.",
        },
    }


def _command_context(command: Sequence[str]) -> dict[str, Any]:
    return {
        "python": str(command[0]) if command else "",
        "script": str(command[1]) if len(command) > 1 else "",
        "manifest": _values_after(command, "--manifest"),
        "out": _values_after(command, "--out"),
        "output_json": _values_after(command, "--output-json"),
        "root": _values_after(command, "--root"),
        "results_dir": _values_after(command, "--results-dir"),
        "proof_dir": _values_after(command, "--p5-g7-tile-eviction-proof-dir"),
        "validation_summary": _values_after(command, "--validation-summary"),
        "p5_g16_benchmark_json": _values_after(command, "--p5-g16-benchmark-json"),
        "p5_g22_gui_soak_json": _values_after(command, "--p5-g22-gui-soak-json"),
        "p5_g27_selected_zone_crop_json": _values_after(
            command,
            "--p5-g27-selected-zone-crop-json",
        ),
    }


def _triage_hints_for_step(step_name: str) -> list[str]:
    if step_name.startswith("standard_validation_"):
        return [
            "Inspect the validation output folder named by command_context.out.",
            "Check validation_summary.json, viewer_perf_summary, selected-zone evidence, and _SUCCESS.",
        ]
    if step_name.startswith("p5_g7_tile_eviction_proof_"):
        return [
            "Confirm the proof manifest is intentionally separate from the standard corpus.",
            "Verify DRAWING_COMPARE_TILE_CACHE_MB and tile eviction thresholds match the release claim.",
        ]
    if step_name.startswith("p5_g16_real_corpus_replay_"):
        return [
            "Inspect the validation_summary path and generated p5_g16_real_corpus_replay.json.",
            "Confirm the customer evidence manifest was generated before replay and that replay gates passed.",
        ]
    if step_name.startswith("p5_g22_actual_gui_soak_"):
        return [
            "Inspect the validation_summary path and generated p5_g22_actual_gui_soak.json.",
            "Check GUI event-loop, blank/stale, RSS/native-resource, and worker cleanup gates.",
        ]
    if step_name == "inventory":
        return [
            "Inspect inventory roots, large_dwg_probe.json, and readiness diagnostics.",
            "Confirm proof roots are attached only through P5-G7 proof fields.",
        ]
    if step_name == "prepare_customer_evidence_manifest":
        return [
            "Inspect customer evidence inputs, truth CSVs, dataset strata, and operator notes.",
            "Confirm the generated manifest readiness issue list before rerunning final audit.",
        ]
    if step_name == "final_customer_grade_audit":
        return [
            "Inspect mvp_exit_audit.json if it was written and review failed checks.",
            "Confirm only standard corpus result dirs are present in the final audit command.",
        ]
    return ["Inspect command_context and rerun the failed step after fixing its inputs."]


def _tail_text(value: object, *, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    if len(text) <= limit:
        return text
    return text[-limit:]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_json_best_effort(path: Path, payload: dict[str, Any]) -> None:
    try:
        _write_json(path, payload)
    except Exception as exc:
        print(f"Could not write readiness report {path}: {exc}", file=sys.stderr)


def _values_after(command: Sequence[str], option: str) -> list[str]:
    return [
        str(command[index + 1])
        for index, value in enumerate(command[:-1])
        if value == option
    ]


def _format_number(value: float | int | None) -> str:
    if value is None:
        return ""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _tile_cache_env_overrides(args: argparse.Namespace) -> dict[str, str]:
    if args.p5_g6_tile_cache_mb is None:
        return {}
    return {TILE_CACHE_MB_ENV_VAR: _format_number(args.p5_g6_tile_cache_mb)}


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_closeout(args)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
