# -*- coding: utf-8 -*-
"""Build and verify an internal pilot release for Drawing Compare Workbench.

The script is intentionally orchestration-only. It does not modify source files
and keeps validation/cache/state artifacts under the selected release output
directory unless explicit paths are supplied.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tmp" / "drawing_compare_pilot_release"
TILE_CACHE_MB_ENV_VAR = "DRAWING_COMPARE_TILE_CACHE_MB"
CUSTOMER_EVIDENCE_REQUEST_KO_SOURCE = (
    ROOT / "docs" / "collab" / "DRAWING_COMPARE_CUSTOMER_EVIDENCE_REQUEST_KO.md"
)
COMPILE_TARGETS = [
    "start_drawing_compare_workbench.py",
    "src/gui/drawing_compare_workbench.py",
    "src/services/comparison/change_zones.py",
    "src/services/comparison/drawing_batch.py",
    "src/services/comparison/dwg_differ.py",
    "src/services/comparison/dxf_comparator.py",
    "src/services/comparison/export_profiles.py",
    "src/services/comparison/folder_compare_pipeline.py",
    "src/services/comparison/preflight.py",
    "scripts/validate_drawing_compare_realset.py",
    "scripts/workbench_acceptance_smoke.py",
    "scripts/audit_drawing_compare_mvp_exit.py",
    "scripts/prepare_drawing_compare_customer_evidence.py",
    "scripts/inventory_drawing_compare_customer_evidence.py",
    "scripts/closeout_drawing_compare_customer_evidence.py",
    "scripts/audit_closeout_readiness.py",
    "scripts/benchmark_real_corpus_replay.py",
    "scripts/benchmark_actual_gui_soak.py",
    "scripts/benchmark_workbench_gui_hotpath.py",
    "scripts/release_drawing_compare_workbench.py",
]
CUSTOMER_PACKAGE_TEXT_SUFFIXES = {".csv", ".json", ".md", ".ps1", ".py", ".txt"}
CUSTOMER_PACKAGE_APP_TEXT_SUFFIXES = CUSTOMER_PACKAGE_TEXT_SUFFIXES | {".css", ".html", ".js", ".qml", ".svg", ".yaml", ".yml"}
CUSTOMER_PACKAGE_EXCLUDED_DIRS = {"__pycache__"}
CUSTOMER_PACKAGE_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--a", type=Path, help="Real-set A folder/file for acceptance validation")
    parser.add_argument("--b", type=Path, help="Real-set B folder/file for acceptance validation")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-realset", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-packaged-launch-smoke", action="store_true")
    parser.add_argument("--pytest-target", default=r"tests\unit\services\comparison")
    parser.add_argument("--dxf-cache-dir", type=Path)
    parser.add_argument("--compare-state-dir", type=Path)
    parser.add_argument("--reuse-compare-state", type=Path)
    parser.add_argument("--review-state", type=Path)
    parser.add_argument("--review-ground-truth", type=Path)
    parser.add_argument("--customer-evidence-manifest", type=Path)
    parser.add_argument("--exit-audit-results-dir", type=Path, action="append")
    parser.add_argument("--run-mvp-exit-audit", action="store_true")
    parser.add_argument(
        "--p5-g16-benchmark-json",
        "--p5-g16-real-corpus-replay",
        dest="p5_g16_benchmark_json",
        type=Path,
        action="append",
        default=[],
        help="P5-G16 real-corpus replay JSON to forward to the customer-grade MVP exit audit.",
    )
    parser.add_argument(
        "--p5-g22-gui-soak-json",
        "--p5-g22-actual-gui-soak",
        dest="p5_g22_gui_soak_json",
        type=Path,
        action="append",
        default=[],
        help="P5-G22 actual GUI soak JSON to forward to the customer-grade MVP exit audit.",
    )
    parser.add_argument(
        "--p5-g27-selected-zone-crop-json",
        "--p5-g27-selected-zone-crop-soak",
        dest="p5_g27_selected_zone_crop_json",
        type=Path,
        action="append",
        default=[],
        help="P5-G27 selected-zone crop-first JSON to forward to the customer-grade MVP exit audit.",
    )
    parser.add_argument("--large-dwg-probe", type=Path)
    parser.add_argument("--require-large-dwg-probe", action="store_true")
    parser.add_argument("--min-total-pairs", type=int, default=20)
    parser.add_argument("--max-total-pairs", type=int, default=50)
    parser.add_argument("--max-first-review-ready-s", type=float, default=1_800.0)
    parser.add_argument("--max-cold-zone-render-ms", type=float, default=10_000.0)
    parser.add_argument("--max-cache-hit-zone-render-ms", type=float, default=2_000.0)
    parser.add_argument("--export-profile", choices=("internal", "sharable"), default="sharable")
    parser.add_argument("--viewer-render-policy", choices=("lazy", "top-issues", "all"), default="top-issues")
    parser.add_argument("--preview-dpi", type=int, default=72)
    parser.add_argument("--skip-selected-zone-evidence", action="store_true")
    parser.add_argument("--selected-zone-evidence-per-pair", type=int, default=1)
    parser.add_argument("--skip-workbench-acceptance", action="store_true")
    parser.add_argument("--skip-marked-pdf", action="store_true")
    parser.add_argument(
        "--require-p5-g3-tile-eviction",
        "--require-p5-g6-tile-eviction",
        dest="require_p5_g3_tile_eviction",
        action="store_true",
        help=(
            "Require observed tile-cache eviction in the P5-G3 realset gate. "
            "Use for controlled low-byte-cap release-candidate probes."
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
            "Set DRAWING_COMPARE_TILE_CACHE_MB for realset validation and "
            "workbench smoke so forced tile eviction is reproducible."
        ),
    )
    parser.add_argument("--cloud-selection-csv", type=Path)
    parser.add_argument("--max-cloud-regions-per-pair", type=int, default=150)
    parser.add_argument("--max-cloud-regions-total", type=int, default=3000)
    parser.add_argument("--cloud-region-distance", type=float, default=1000.0)
    parser.add_argument("--pyinstaller-spec", type=Path, default=ROOT / "DrawingCompareWorkbench.spec")
    args = parser.parse_args(argv)
    if args.run_mvp_exit_audit and not args.customer_evidence_manifest:
        parser.error("--run-mvp-exit-audit requires --customer-evidence-manifest for customer-grade final audit")
    if args.run_mvp_exit_audit and args.customer_evidence_manifest and not args.customer_evidence_manifest.exists():
        parser.error(f"--customer-evidence-manifest does not exist: {args.customer_evidence_manifest}")
    if args.run_mvp_exit_audit and args.require_large_dwg_probe and not args.large_dwg_probe:
        parser.error("--require-large-dwg-probe requires --large-dwg-probe")
    if args.run_mvp_exit_audit and args.large_dwg_probe and not args.large_dwg_probe.exists():
        parser.error(f"--large-dwg-probe does not exist: {args.large_dwg_probe}")
    for path in args.p5_g16_benchmark_json:
        if not path.exists():
            parser.error(f"--p5-g16-benchmark-json does not exist: {path}")
    for path in args.p5_g22_gui_soak_json:
        if not path.exists():
            parser.error(f"--p5-g22-gui-soak-json does not exist: {path}")
    for path in args.p5_g27_selected_zone_crop_json:
        if not path.exists():
            parser.error(f"--p5-g27-selected-zone-crop-json does not exist: {path}")
    if args.require_p5_g3_tile_eviction and args.skip_selected_zone_evidence:
        parser.error(
            "--require-p5-g3-tile-eviction/--require-p5-g6-tile-eviction requires selected-zone/P5-G3 "
            "evidence; remove --skip-selected-zone-evidence"
        )
    if args.p5_g6_tile_cache_mb is not None and args.p5_g6_tile_cache_mb <= 0:
        parser.error("--p5-g6-tile-cache-mb must be greater than 0")
    if args.run_mvp_exit_audit:
        for result_dir in args.exit_audit_results_dir or []:
            if not result_dir.exists():
                parser.error(f"--exit-audit-results-dir does not exist: {result_dir}")
            if not result_dir.is_dir():
                parser.error(f"--exit-audit-results-dir is not a directory: {result_dir}")
            if not _validation_summary_path(result_dir).exists():
                parser.error(
                    "--exit-audit-results-dir is not a validation output "
                    f"(missing validation_summary.json): {result_dir}"
                )
        realset_will_run = not args.skip_realset and args.a is not None and args.b is not None
        if realset_will_run and args.skip_selected_zone_evidence:
            parser.error(
                "--run-mvp-exit-audit cannot generate P5-G3 customer-grade "
                "evidence with --skip-selected-zone-evidence"
            )
        realset_validation_dir = args.out / "realset_validation"
        realset_reusable = _validation_summary_path(realset_validation_dir).exists()
        if realset_validation_dir.exists() and not realset_will_run and not realset_reusable:
            parser.error(
                "existing <out>/realset_validation is not a validation output "
                f"(missing validation_summary.json): {realset_validation_dir}"
            )
        if not args.exit_audit_results_dir and not realset_will_run and not realset_reusable:
            parser.error(
                "--run-mvp-exit-audit requires a validation result source: provide --a/--b "
                "to generate realset_validation, reuse an existing <out>/realset_validation, "
                "or pass --exit-audit-results-dir"
            )
    return args


def _validation_summary_path(result_dir: Path) -> Path:
    return result_dir / "validation_summary.json"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now().isoformat(),
        "root": str(ROOT),
        "out_dir": str(out_dir),
        "steps": [],
        "artifacts": {},
        "preflight": {},
    }

    _write_release_templates(out_dir, args)
    manifest["artifacts"]["readme"] = str(out_dir / "README_INTERNAL_PILOT.md")
    manifest["artifacts"]["sample_manifest"] = str(out_dir / "sample_manifest.json")
    manifest["artifacts"]["customer_evidence_manifest_template"] = str(
        out_dir / "customer_evidence_manifest_template.json"
    )
    manifest["artifacts"]["operator_dry_run_checklist_template"] = str(
        out_dir / "operator_dry_run_checklist_template.md"
    )
    manifest["artifacts"]["review_ground_truth_template"] = str(
        out_dir / "review_ground_truth_template.csv"
    )
    manifest["artifacts"]["review_decision_truth_template"] = str(
        out_dir / "review_decision_truth_template.csv"
    )
    manifest["artifacts"]["dataset_strata_template"] = str(
        out_dir / "dataset_strata_template.csv"
    )
    manifest["artifacts"]["mvp_exit_prompt_to_artifact_checklist"] = str(
        out_dir / "mvp_exit_prompt_to_artifact_checklist.md"
    )
    manifest["artifacts"]["customer_evidence_closeout_packet"] = str(
        out_dir / "customer_evidence_closeout_packet.md"
    )
    manifest["artifacts"]["customer_evidence_request_ko"] = str(
        out_dir / "customer_evidence_request_ko.md"
    )
    manifest["artifacts"]["mvp_exit_audit_tool"] = str(out_dir / "cli" / "audit_drawing_compare_mvp_exit.py")
    manifest["artifacts"]["customer_evidence_manifest_tool"] = str(
        out_dir / "cli" / "prepare_drawing_compare_customer_evidence.py"
    )
    manifest["artifacts"]["customer_evidence_inventory_tool"] = str(
        out_dir / "cli" / "inventory_drawing_compare_customer_evidence.py"
    )
    manifest["artifacts"]["customer_evidence_closeout_tool"] = str(
        out_dir / "cli" / "closeout_drawing_compare_customer_evidence.py"
    )
    manifest["artifacts"]["closeout_readiness_audit_tool"] = str(
        out_dir / "cli" / "audit_closeout_readiness.py"
    )
    manifest["artifacts"]["p5_g16_real_corpus_replay_tool"] = str(
        out_dir / "cli" / "benchmark_real_corpus_replay.py"
    )
    manifest["artifacts"]["p5_g22_actual_gui_soak_tool"] = str(
        out_dir / "cli" / "benchmark_actual_gui_soak.py"
    )
    manifest["artifacts"]["p5_g26_selection_latency_tool"] = str(
        out_dir / "cli" / "benchmark_workbench_gui_hotpath.py"
    )
    if args.p5_g16_benchmark_json:
        p5_g16_paths = [str(path.resolve()) for path in args.p5_g16_benchmark_json]
        manifest["artifacts"]["p5_g16_real_corpus_replay_json"] = p5_g16_paths[0]
        manifest["artifacts"]["p5_g16_real_corpus_replay_jsons"] = p5_g16_paths
    if args.p5_g22_gui_soak_json:
        p5_g22_paths = [str(path.resolve()) for path in args.p5_g22_gui_soak_json]
        manifest["artifacts"]["p5_g22_actual_gui_soak_json"] = p5_g22_paths[0]
        manifest["artifacts"]["p5_g22_actual_gui_soak_jsons"] = p5_g22_paths
    if args.p5_g27_selected_zone_crop_json:
        p5_g27_paths = [
            str(path.resolve()) for path in args.p5_g27_selected_zone_crop_json
        ]
        manifest["artifacts"]["p5_g27_selected_zone_crop_json"] = p5_g27_paths[0]
        manifest["artifacts"]["p5_g27_selected_zone_crop_jsons"] = p5_g27_paths
    manifest["preflight"]["oda_converter"] = _oda_preflight(args.python)

    failures = 0
    failures += _run_step(
        manifest,
        "compile",
        [args.python, "-m", "py_compile", *COMPILE_TARGETS],
    )

    if not args.skip_tests:
        failures += _run_step(
            manifest,
            "comparison_tests",
            [
                args.python,
                "-m",
                "pytest",
                args.pytest_target,
                "-q",
                "-o",
                "log_cli=false",
                "--disable-warnings",
            ],
        )

    if not args.skip_realset:
        if args.a and args.b:
            validation_dir = out_dir / "realset_validation"
            tile_cache_env_overrides = _tile_cache_env_overrides(args)
            failures += _run_step(
                manifest,
                "realset_validation",
                _realset_command(args, validation_dir),
                timeout=60 * 60,
                env_overrides=tile_cache_env_overrides,
            )
            manifest["artifacts"]["realset_validation"] = str(validation_dir)
            if not args.skip_workbench_acceptance:
                screenshots_dir = out_dir / "workbench_acceptance_screenshots"
                failures += _run_step(
                    manifest,
                    "workbench_acceptance_smoke",
                    _workbench_acceptance_command(args, validation_dir, screenshots_dir),
                    timeout=20 * 60,
                    env_overrides=tile_cache_env_overrides,
                )
                manifest["artifacts"]["workbench_acceptance_screenshots"] = str(screenshots_dir)
        else:
            manifest["steps"].append(
                {
                    "name": "realset_validation",
                    "status": "skipped",
                    "reason": "--a and --b were not provided",
                }
            )

    if not args.skip_build:
        dist_dir = out_dir / "dist"
        build_dir = out_dir / "build"
        failures += _run_step(
            manifest,
            "pyinstaller_build",
            [
                args.python,
                "-m",
                "PyInstaller",
                str(args.pyinstaller_spec),
                "--noconfirm",
                "--distpath",
                str(dist_dir),
                "--workpath",
                str(build_dir),
            ],
            timeout=60 * 60,
        )
        exe_path = dist_dir / "DrawingCompareWorkbench" / "DrawingCompareWorkbench.exe"
        manifest["artifacts"]["workbench_exe"] = str(exe_path)
        manifest["steps"].append(
            {
                "name": "packaged_app_smoke",
                "status": "passed" if exe_path.exists() else "failed",
                "exe": str(exe_path),
                "note": "Smoke checks packaged executable presence. Launch manually for GUI interaction.",
            }
        )
        if not exe_path.exists():
            failures += 1
        elif not args.skip_packaged_launch_smoke:
            failures += _run_step(
                manifest,
                "packaged_app_launch_smoke",
                _packaged_launch_command(exe_path),
                timeout=30,
                env_overrides={
                    "QT_QPA_PLATFORM": "offscreen",
                    "DRAWING_COMPARE_SMOKE_EXIT_MS": "1000",
                },
            )

    package_result = _write_customer_shareable_package(out_dir)
    manifest["artifacts"]["customer_shareable_package_dir"] = str(package_result["package_dir"])
    manifest["artifacts"]["customer_shareable_package_zip"] = str(package_result["zip_path"])
    manifest["artifacts"]["customer_shareable_package_manifest"] = str(package_result["manifest_path"])
    manifest["artifacts"]["customer_shareable_package_path_audit"] = str(package_result["audit_path"])
    manifest["steps"].append(
        {
            "name": "customer_shareable_package_path_audit",
            "status": package_result["audit_status"],
            "leak_count": package_result["leak_count"],
            "audit": str(package_result["audit_path"]),
            "note": "Audits customer-shareable text metadata, first-party app text, and selected binaries; internal release_manifest.json and Python bytecode/cache files are excluded.",
        }
    )
    if package_result["audit_status"] != "passed":
        failures += 1

    manifest_path = out_dir / "release_manifest.json"
    if args.run_mvp_exit_audit:
        manifest["status"] = "failed" if failures else "passed"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        audit_json = out_dir / "mvp_exit_audit.json"
        failures += _run_step(
            manifest,
            "mvp_exit_audit",
            _mvp_exit_audit_command(args, out_dir, manifest_path, audit_json),
            timeout=10 * 60,
        )
        manifest["artifacts"]["mvp_exit_audit"] = str(audit_json)

    manifest["status"] = "failed" if failures else "passed"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "manifest": str(manifest_path)}, ensure_ascii=False))
    return 1 if failures else 0


def _run_step(
    manifest: dict[str, Any],
    name: str,
    command: Sequence[str],
    *,
    timeout: int | None = None,
    env_overrides: dict[str, str] | None = None,
) -> int:
    print(f"[{name}] {' '.join(command)}")
    started = time.perf_counter()
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(ROOT))
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        list(command),
        cwd=str(ROOT),
        env=env,
        timeout=timeout,
    )
    elapsed = round(time.perf_counter() - started, 3)
    step = {
        "name": name,
        "status": "passed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "elapsed_s": elapsed,
        "command": list(map(str, command)),
    }
    if env_overrides:
        step["env_overrides"] = dict(env_overrides)
    manifest["steps"].append(step)
    return 0 if result.returncode == 0 else 1


def _packaged_launch_command(exe_path: Path) -> list[str]:
    return [str(exe_path), "--smoke-exit-ms", "1000"]


def _tile_cache_env_overrides(args: argparse.Namespace) -> dict[str, str] | None:
    value = getattr(args, "p5_g6_tile_cache_mb", None)
    if value is None:
        return None
    return {TILE_CACHE_MB_ENV_VAR: _format_number_arg(value)}


def _oda_preflight(python: str) -> dict[str, Any]:
    command = [
        python,
        "-c",
        (
            "import json; "
            "from src.services.comparison.dwg_differ import DwgDiffer; "
            "print(json.dumps(DwgDiffer.get_status(), ensure_ascii=False, default=str))"
        ),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=60,
        )
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    payload: dict[str, Any] = {
        "status": "passed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
    try:
        payload["dependencies"] = json.loads(result.stdout)
    except Exception:
        pass
    return payload


def _realset_command(args: argparse.Namespace, validation_dir: Path) -> list[str]:
    dxf_cache_dir = (args.dxf_cache_dir or (args.out / "dxf_cache")).resolve()
    compare_state_dir = (args.compare_state_dir or (args.out / "compare_state")).resolve()
    command = [
        args.python,
        "scripts/validate_drawing_compare_realset.py",
        "--a",
        str(args.a.resolve()),
        "--b",
        str(args.b.resolve()),
        "--out",
        str(validation_dir),
        "--dxf-cache-dir",
        str(dxf_cache_dir),
        "--export-profile",
        args.export_profile,
        "--quality-gate",
        "--change-zone-report",
        "--executive-review",
        "--review-dashboard",
        "--export-viewer-package",
        "--viewer-render-policy",
        args.viewer_render_policy,
        "--viewer-perf-log",
        "--preview-dpi",
        str(args.preview_dpi),
        "--cloud-region-distance",
        str(args.cloud_region_distance),
        "--max-cloud-regions-per-pair",
        str(args.max_cloud_regions_per_pair),
        "--max-cloud-regions-total",
        str(args.max_cloud_regions_total),
    ]
    if not args.skip_selected_zone_evidence:
        command += [
            "--p5-g3-realset-gate",
            "--render-selected-zone-evidence",
            "--selected-zone-evidence-per-pair",
            str(args.selected_zone_evidence_per_pair),
        ]
    if args.require_p5_g3_tile_eviction:
        command += [
            "--p5-g3-require-tile-eviction",
            "--p5-g3-min-tile-evicted-pairs",
            str(args.p5_g3_min_tile_evicted_pairs),
            "--p5-g3-min-tile-evicted-bytes",
            str(args.p5_g3_min_tile_evicted_bytes),
        ]
    if args.p5_g6_tile_cache_mb is not None:
        command += ["--p5-g6-tile-cache-mb", _format_number_arg(args.p5_g6_tile_cache_mb)]
    if not args.skip_marked_pdf:
        command += ["--export-marked-pdf", "--marked-pdf-mode", "selected"]
    if args.cloud_selection_csv:
        command += [
            "--export-cloud-marks",
            "--cloud-export-mode",
            "csv",
            "--cloud-selection-csv",
            str(args.cloud_selection_csv.resolve()),
        ]
    if args.review_state:
        command += ["--review-state", str(args.review_state.resolve())]
    if args.review_ground_truth:
        command += ["--review-ground-truth", str(args.review_ground_truth.resolve())]
    if args.reuse_compare_state:
        command += ["--reuse-compare-state", str(args.reuse_compare_state.resolve()), "--skip-compare"]
    else:
        command += ["--compare-state-dir", str(compare_state_dir), "--max-workers", "1"]
    return command


def _workbench_acceptance_command(
    args: argparse.Namespace,
    validation_dir: Path,
    screenshots_dir: Path,
) -> list[str]:
    return [
        args.python,
        "scripts/workbench_acceptance_smoke.py",
        "--results-dir",
        str(validation_dir),
        "--a",
        str(args.a.resolve()),
        "--b",
        str(args.b.resolve()),
        "--screenshots-dir",
        str(screenshots_dir),
    ]


def _mvp_exit_audit_command(
    args: argparse.Namespace,
    out_dir: Path,
    release_manifest: Path,
    audit_json: Path,
) -> list[str]:
    result_dirs = _mvp_exit_audit_result_dirs(args, out_dir)
    command = [args.python, "scripts/audit_drawing_compare_mvp_exit.py"]
    for result_dir in result_dirs:
        command += ["--results-dir", str(result_dir)]
    command += [
        "--release-manifest",
        str(release_manifest),
        "--min-total-pairs",
        str(args.min_total_pairs),
        "--max-total-pairs",
        str(args.max_total_pairs),
        "--max-first-review-ready-s",
        _format_number_arg(args.max_first_review_ready_s),
        "--max-cold-zone-render-ms",
        _format_number_arg(args.max_cold_zone_render_ms),
        "--max-cache-hit-zone-render-ms",
        _format_number_arg(args.max_cache_hit_zone_render_ms),
        "--out",
        str(audit_json),
    ]
    if args.customer_evidence_manifest:
        command += [
            "--customer-evidence-manifest",
            str(args.customer_evidence_manifest.resolve()),
            "--evidence-level",
            "customer_grade",
            "--require-p5-g3-realset-gate",
        ]
    if args.require_p5_g3_tile_eviction:
        command += [
            "--require-p5-g3-tile-eviction",
            "--p5-g3-min-tile-evicted-pairs",
            str(args.p5_g3_min_tile_evicted_pairs),
            "--p5-g3-min-tile-evicted-bytes",
            str(args.p5_g3_min_tile_evicted_bytes),
        ]
    if args.p5_g6_tile_cache_mb is not None:
        command += ["--p5-g6-tile-cache-mb", _format_number_arg(args.p5_g6_tile_cache_mb)]
    if args.large_dwg_probe:
        command += ["--large-dwg-probe", str(args.large_dwg_probe.resolve())]
    if args.require_large_dwg_probe:
        command += ["--require-large-dwg-probe"]
    for benchmark_json in args.p5_g16_benchmark_json:
        command += ["--p5-g16-benchmark-json", str(benchmark_json.resolve())]
    for soak_json in args.p5_g22_gui_soak_json:
        command += ["--p5-g22-gui-soak-json", str(soak_json.resolve())]
    for crop_json in args.p5_g27_selected_zone_crop_json:
        command += ["--p5-g27-selected-zone-crop-json", str(crop_json.resolve())]
    return command


def _format_number_arg(value: float | int) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _mvp_exit_audit_result_dirs(args: argparse.Namespace, out_dir: Path) -> list[Path]:
    result_dirs: list[Path] = []
    for path in args.exit_audit_results_dir or []:
        result_dirs.append(path.resolve())
    validation_dir = out_dir / "realset_validation"
    if validation_dir.exists():
        result_dirs.append(validation_dir.resolve())

    unique: list[Path] = []
    seen: set[str] = set()
    for path in result_dirs:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _write_release_templates(out_dir: Path, args: argparse.Namespace) -> None:
    cli_dir = out_dir / "cli"
    cli_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "scripts" / "audit_drawing_compare_mvp_exit.py", cli_dir / "audit_drawing_compare_mvp_exit.py")
    shutil.copy2(
        ROOT / "scripts" / "prepare_drawing_compare_customer_evidence.py",
        cli_dir / "prepare_drawing_compare_customer_evidence.py",
    )
    shutil.copy2(
        ROOT / "scripts" / "inventory_drawing_compare_customer_evidence.py",
        cli_dir / "inventory_drawing_compare_customer_evidence.py",
    )
    shutil.copy2(
        ROOT / "scripts" / "closeout_drawing_compare_customer_evidence.py",
        cli_dir / "closeout_drawing_compare_customer_evidence.py",
    )
    shutil.copy2(
        ROOT / "scripts" / "audit_closeout_readiness.py",
        cli_dir / "audit_closeout_readiness.py",
    )
    shutil.copy2(
        ROOT / "scripts" / "benchmark_real_corpus_replay.py",
        cli_dir / "benchmark_real_corpus_replay.py",
    )
    shutil.copy2(
        ROOT / "scripts" / "benchmark_actual_gui_soak.py",
        cli_dir / "benchmark_actual_gui_soak.py",
    )
    shutil.copy2(
        ROOT / "scripts" / "benchmark_workbench_gui_hotpath.py",
        cli_dir / "benchmark_workbench_gui_hotpath.py",
    )

    readme = out_dir / "README_INTERNAL_PILOT.md"
    readme.write_text(
        "\n".join(
            [
                "# Drawing Compare Workbench Internal Pilot",
                "",
                "## Preflight",
                "- ODA Converter is an external dependency and is not bundled.",
                "- `rtree`, `ezdxf`, `openpyxl`, and PySide6 must be available in the Python environment used for the release.",
                "- Cache/state/report files are written under the release output folder unless explicit paths are supplied.",
                "",
                "## Validation (source checkout)",
                "Run the real-set validator from the source checkout in customer-shareable review-queue mode:",
                "",
                "```powershell",
                "python scripts\\validate_drawing_compare_realset.py --a <A> --b <B> --out <out> --export-profile sharable --quality-gate --p5-g3-realset-gate --change-zone-report --executive-review --review-dashboard --export-viewer-package --viewer-render-policy top-issues --viewer-perf-log --render-selected-zone-evidence --selected-zone-evidence-per-pair 1 --export-marked-pdf --marked-pdf-mode selected",
                "```",
                "",
                "For the CAD block attribute/text policy gate, include at least one CAD validation run with `--no-expand-blocks` on a set where block attribute/text changes such as `@100 -> @200` are expected; do not use `--no-block-text-detection` for that evidence run.",
                "For the large-DWG performance/progress gate, include the S20 or equivalent large-DWG probe JSON with `--large-dwg-probe <large_dwg_probe.json> --require-large-dwg-probe` in the final audit command. The probe must show bounded elapsed time, streamed change-zone output, capped in-memory records, and forwarded DXF compare progress.",
                "For the P5-G16 real-corpus replay gate, run `python cli\\benchmark_real_corpus_replay.py --validation-summary <validation>\\validation_summary.json --customer-evidence-manifest <customer_evidence_manifest.json> --output-json <validation>\\p5_g16_real_corpus_replay.json --require-customer-corpus` after manifest generation and before the final audit. The closeout runner plans this automatically for replayable standard validation outputs and forwards `--p5-g16-benchmark-json` to both manifest preparation and the final audit.",
                "For the P5-G22 actual GUI soak gate, run `python cli\\benchmark_actual_gui_soak.py --validation-summary <validation>\\validation_summary.json --customer-evidence-manifest <customer_evidence_manifest.json> --output-json <validation>\\p5_g22_actual_gui_soak.json --require-customer-corpus` after manifest generation and before the final audit. The closeout runner plans this automatically for replayable standard validation outputs and forwards `--p5-g22-gui-soak-json` to both manifest preparation and the final audit.",
                "For the P5-G26 selection-latency gate, run `python cli\\benchmark_workbench_gui_hotpath.py --include-p5-g26-contract --include-zone-selection-hotpath --output <validation>\\p5_g26_selection_latency_soak.json` and keep the JSON beside the audited `validation_summary.json`, or pass it explicitly to manifest preparation/final audit with `--p5-g26-selection-latency-json`. The final audit also discovers `<results-dir>\\p5_g26_selection_latency_soak.json` automatically.",
                "For the P5-G27 crop-first gate, the supplied `p5_g27_selected_zone_crop_soak.json` must include `p5_g27_real_renderer_bridge` to a passed `p5_g16_real_corpus_replay.json`, so crop-first lifecycle safety is tied to nonblank real selected-zone render artifacts.",
                "The P5-G30 composite customer visual-performance release gate is the final blocker in `mvp_exit_audit.json`: `p5_g30_customer_visual_performance_release_gate` must pass only after P5-G3, P5-G16, P5-G22, P5-G24, P5-G26, and P5-G27 audit checks are all present and passing.",
                "For a controlled P5-G6 tile-eviction probe, run the validator/release orchestrator with a deliberately low tile-cache byte cap and pass `--p5-g6-tile-cache-mb 0.25 --require-p5-g6-tile-eviction --p5-g6-min-tile-evicted-pairs 1 --p5-g6-min-tile-evicted-bytes 1` (P5-G3 spellings such as `--require-p5-g3-tile-eviction` are kept as compatibility aliases). The command records the `DRAWING_COMPARE_TILE_CACHE_MB` override in release steps and validation evidence. This is optional for routine customer-grade evidence but required when claiming realset tile eviction, not only synthetic P5-G2 soak coverage.",
                "When a release claim includes realset tile eviction, keep the forced P5-G7 proof separate from the 20-50 sheet customer corpus. Pass proof outputs only through `--p5-g7-tile-eviction-proof-dir` and never as final audit `--results-dir`; use `closeout_drawing_compare_customer_evidence.py` to enforce that routing.",
                "",
                "For structural-core recall validation, copy `review_ground_truth_template.csv` to a customer evidence filename such as `review_ground_truth.csv`, replace every example row with approved customer/customer-grade expected changes, and pass the copy as `--review-ground-truth <csv>`. Do not pass the template file itself or a copy that still contains example/sample/template markers; inventory, manifest preparation, and final audit reject template/handoff paths and copied example rows as customer evidence. The completed CSV must keep the required schema `drawing_label,category,summary_contains,source_format,detection_source,bbox_status`; pipe-separated values mean 'any of these accepted values'. Each expected structural change should have enough fields to match one Top review_queue item.",
                "Customer-grade exit also requires 20-50 completed sheets/pairs, direct first-interactive evidence (`first_interactive_ready.review_dashboard_ready_s <= 600`, speed/fast profiles <= 300, `first_top_issue_ready_s <= 600`, `viewer_metadata_ready_s <= 900`), selected-zone render telemetry for every completed output (`cold p95 <= 10000ms`, `cache-hit p95 <= 2000ms`), and a passed P5-G3 realset gate covering runtime budget, viewer perf, selected-zone, nonblank, and tile-manifest evidence.",
                "`timings.total_s <= 1800` remains an operational batch bound, but customer UX pass/fail is based on the direct first-interactive metrics above.",
                "Precision and dataset representativeness are hard gates: provide `review_decision_truth.csv` via `--review-decision-truth` and `dataset_strata.csv` via `--dataset-strata`; inventory/prepare/audit reject templates, copied examples, low precision, high false-positive rate, and insufficient strata.",
                "",
                "For customer-grade exit, use `customer_evidence_manifest_template.json` only as a schema reference; generate the real `customer_evidence_manifest.json` with `prepare_drawing_compare_customer_evidence.py` so claims are cross-checked against validation outputs.",
                "Copy `operator_dry_run_checklist_template.md` to a real operator notes filename such as `operator_dry_run_notes.md` and complete that copy during the dry run. Keep `reviewer_role: structural_review_lead` or replace it with another approved structural review lead/team lead role such as `구조검토책임자`, `구조검토팀장`, `구조도면검토책임자`, or `구조도면검토팀장`, check each workflow row as `[x]`, and write at least one concrete observation under `Operator notes:` covering the reviewed drawing/zone, synchronized Before/After review, decisions, confirmed-only export, and path audit result; the template file itself is rejected as evidence, and placeholder notes or copied checklist-only files are rejected too.",
                "Use `mvp_exit_prompt_to_artifact_checklist.md` as the prompt-to-artifact audit map before declaring the MVP complete.",
                "Use `customer_evidence_closeout_packet.md` as the handoff sheet for the remaining external artifacts, the required inventory status, manifest generation command, and final customer-grade audit command.",
                "Use `cli\\closeout_drawing_compare_customer_evidence.py` as the preferred one-command closeout runner when chaining corpus validation, optional P5-G7 forced tile-eviction proof validation, manifest generation, P5-G16 real-corpus replay, P5-G22 actual GUI soak, optional P5-G27 selected-zone crop-first evidence routing, and the final audit.",
                "Use `customer_evidence_request_ko.md` as the Korean request sheet for the structural review lead/team lead who must provide the approved ground truth and dry-run notes.",
                "The manifest's `review_ground_truth_csv`, `review_decision_truth_csv`, `dataset_strata_csv`, `audit_json`, large-DWG probe, and operator dry-run artifact fields must point to real artifacts, not release templates, handoff docs, or quick references; the exit audit cross-checks dataset provenance, row counts, sheet counts, format coverage, precision, strata, first-interactive readiness, bbox fallback quality, operator workflow evidence, visual-performance gates, and path leakage against the validation outputs.",
                "",
                "Recommended one-command closeout from a release folder:",
                "",
                "```powershell",
                "python cli\\closeout_drawing_compare_customer_evidence.py --source-checkout <source_checkout> --out <closeout_out> --standard-results-dir <dwg_validation> --standard-results-dir <pdf_validation> --standard-results-dir <cad_pdf_block_validation> --standard-results-dir <cad_block_text_no_expand_validation> --customer-evidence-manifest <customer_evidence_manifest.json> --release-manifest <release_manifest.json> --large-dwg-probe <large_dwg_probe.json> --review-ground-truth <review_ground_truth.csv> --review-decision-truth <review_decision_truth.csv> --dataset-strata <dataset_strata.csv> --operator-notes-file <operator_dry_run_notes.md> --confirmed-export-artifact <artifacts\\confirmed_clouds\\pair_confirmed.png> --p5-g27-selected-zone-crop-json <p5_g27_selected_zone_crop_soak.json> --dataset-id <dataset_id> --dataset-source-description \"20-50 sheet customer-grade validation set approved for MVP exit\" --dataset-approver <approver> --ground-truth-owner <owner> --min-total-pairs 20 --max-total-pairs 50 --max-first-review-ready-s 1800 --max-cold-zone-render-ms 10000 --max-cache-hit-zone-render-ms 2000",
                "```",
                "",
                "Before running the closeout command for real, add `--dry-run --plan-json <closeout_plan.json> --readiness-json <closeout_readiness.json>` to validate source-checkout prerequisites, customer evidence inputs, existing validation output sentinels, P5-G16 replay routing, P5-G22 GUI soak routing, P5-G27 crop-first JSON routing when supplied, and the proof/corpus routing plan without launching subprocesses. Then run `python cli\\audit_closeout_readiness.py --readiness-json <closeout_readiness.json> --plan-json <closeout_plan.json> --require-ready --out <closeout_readiness_audit.json>` and require `status=passed` before launching full closeout. The supplied P5-G27 JSON must include `p5_g27_real_renderer_bridge` to the passed P5-G16 replay so final audit can bind crop-first lifecycle safety to real nonblank selected-zone render artifacts. The source checkout must include `scripts/` plus `src/services/comparison/manifest_provenance.py` so the generated manifest can pass provenance verification. The readiness report records `status=ready_for_closeout` or `status=preflight_failed`, `preflight.issue_count`, `preflight.issues`, `outputs.plan_json`, `outputs.readiness_json`, `outputs.failure_json`, `outputs.inventory_json`, `outputs.customer_evidence_manifest`, `outputs.audit_json`, `routing_expectations.require_p5_g7_tile_eviction_proof`, `routing_expectations.p5_g16_real_corpus_replay_generation_enabled`, `routing_expectations.p5_g22_actual_gui_soak_generation_enabled`, `plan.available=true`, `plan.step_count`, `plan.invariants.proof_dirs_excluded_from_final_audit_results_dir=true`, `plan.invariants.final_audit_p5_g16_benchmark_jsons_equal_plan=true`, `plan.invariants.final_audit_p5_g22_gui_soak_jsons_equal_plan=true`, and `plan.invariants.final_audit_p5_g27_selected_zone_crop_jsons_equal_plan=true`.",
                "Retain `closeout_readiness.json` with `closeout_plan.json` and `closeout_readiness_audit.json`, plus `inventory.json`, `customer_evidence_manifest.json`, `p5_g16_real_corpus_replay.json`, `p5_g22_actual_gui_soak.json`, `p5_g27_selected_zone_crop_soak.json`, and `mvp_exit_audit.json` as the final closeout evidence packet. Do not run full closeout unless the readiness audit has `status=passed`, readiness `status=ready_for_closeout`, `preflight.status=passed`, `preflight.issue_count=0`, and the plan invariants are true. If `status=preflight_failed`, attach `closeout_readiness.json` as the preflight failure report and resolve `preflight.issues` before rerun.",
                "",
                "If any closeout subprocess returns non-zero, the closeout runner writes `<closeout_out>\\closeout_failure.json` by default, or the path supplied with `--failure-json <closeout_failure.json>`. Retain it with `closeout_plan.json` and the parent console log before rerunning. The report records `failure_kind=subprocess_nonzero_exit`, `failed_step.name`, `failed_step.returncode`, `failed_step.command_context`, `completed_steps`, `remaining_steps`, `plan_invariants`, `triage_hints`, and `stdout_stderr.capture_mode=inherited_console`.",
                "",
                "If the release includes a P5-G7 realset tile-eviction claim, add `--p5-g7-proof-validation-manifest <proof_manifest.json>` or `--p5-g7-tile-eviction-proof-dir <proof_validation>`, plus `--require-p5-g7-tile-eviction-proof --p5-g6-tile-cache-mb 0.25 --p5-g6-min-tile-evicted-pairs 1 --p5-g6-min-tile-evicted-bytes 1`. The closeout runner forwards the proof only to inventory/manifest proof fields and excludes it from the final audit corpus `--results-dir` list.",
                "",
                "Before manifest generation, inventory the evidence folder to see missing customer-grade blockers and suggested commands:",
                "",
                "```powershell",
                "python cli\\inventory_drawing_compare_customer_evidence.py --root <customer_evidence_root> --large-dwg-probe <large_dwg_probe.json> --portable-paths --out <inventory.json>",
                "```",
                "",
                "Use `--portable-paths` for inventory JSON that may be attached to customer evidence; it replaces absolute local paths with `root_N` aliases. For a local-only inventory whose suggested commands will be run directly, omit `--portable-paths` and do not share that file.",
                "",
                "Do not proceed to manifest generation until `inventory.json` has `status=ready_for_manifest`. If it is incomplete, inspect `diagnostics.validation_outputs_missing_selected_zone_telemetry`, `diagnostics.validation_outputs_missing_top_review_queue_first`, `diagnostics.validation_outputs_missing_sharable_path_leakage_zero`, `diagnostics.missing_format_coverage`, `diagnostics.validation_outputs_missing_cad_block_text_no_expand`, `diagnostics.validation_outputs_with_cad_block_text_no_expand`, `diagnostics.audited_review_ground_truth_rows`, `diagnostics.valid_review_ground_truth_csv_candidates`, `diagnostics.valid_review_decision_truth_csv_candidates`, `diagnostics.valid_dataset_strata_csv_candidates`, `diagnostics.first_interactive_readiness`, `diagnostics.bbox_quality`, `diagnostics.required_operator_workflow_checks`, `diagnostics.missing_operator_workflow_checks`, `diagnostics.operator_notes_missing_required_checks`, `diagnostics.operator_notes_with_approved_structural_role`, `diagnostics.operator_notes_with_substantive_review_notes`, `diagnostics.operator_notes_missing_approved_structural_role`, `diagnostics.operator_notes_missing_substantive_review_notes`, `diagnostics.approved_operator_reviewer_roles`, `diagnostics.large_dwg_probe_passed`, `diagnostics.large_dwg_probe_issues`, `customer_evidence_manifest_summaries`, `diagnostics.customer_evidence_manifests_not_ready`, and `diagnostics.customer_evidence_manifests_missing_approved_ground_truth` to identify the exact output, truth CSV, decision CSV, strata CSV, operator artifact, large-DWG probe, or stale manifest that must be rerun or filled.",
                "",
                "You can generate the manifest from completed validation outputs and operator artifacts:",
                "",
                "```powershell",
                "python cli\\prepare_drawing_compare_customer_evidence.py --results-dir <dwg_validation> --results-dir <pdf_validation> --results-dir <cad_pdf_block_validation> --results-dir <cad_block_text_no_expand_validation> --out <customer_evidence_manifest.json> --dataset-id <dataset_id> --dataset-source-kind customer_grade --dataset-source-description \"20-50 sheet customer-grade validation set approved for MVP exit\" --dataset-approval-status approved_for_mvp_exit --dataset-approver <approver> --ground-truth-owner <owner> --review-ground-truth <review_ground_truth.csv> --ground-truth-status approved --review-decision-truth <review_decision_truth.csv> --dataset-strata <dataset_strata.csv> --large-dwg-probe <large_dwg_probe.json> --p5-g16-benchmark-json <p5_g16_real_corpus_replay.json> --operator-reviewer-role structural_review_lead --operator-notes-file <operator_notes.md> --confirmed-export-artifact <artifacts\\confirmed_clouds\\pair_confirmed.png> --min-total-pairs 20 --max-total-pairs 50 --max-first-review-ready-s 1800 --max-cold-zone-render-ms 10000 --max-cache-hit-zone-render-ms 2000",
                "```",
                "",
                "After validation, run the MVP exit audit across the evidence folders:",
                "",
                "```powershell",
                "python cli\\audit_drawing_compare_mvp_exit.py --results-dir <dwg_validation> --results-dir <pdf_validation> --results-dir <cad_pdf_block_validation> --results-dir <cad_block_text_no_expand_validation> --release-manifest <release_manifest.json> --large-dwg-probe <large_dwg_probe.json> --require-large-dwg-probe --customer-evidence-manifest <customer_evidence_manifest.json> --evidence-level customer_grade --require-p5-g3-realset-gate --p5-g16-benchmark-json <p5_g16_real_corpus_replay.json> --min-total-pairs 20 --max-total-pairs 50 --max-first-review-ready-s 1800 --max-cold-zone-render-ms 10000 --max-cache-hit-zone-render-ms 2000 --out <audit.json>",
                "```",
                "",
                "The release orchestrator can run the same gate with `--run-mvp-exit-audit --customer-evidence-manifest <customer_evidence_manifest.json> --exit-audit-results-dir <extra_validation_dir>`. Include the no-expand CAD block-text validation output as one of the final audit result sources. `--run-mvp-exit-audit` requires a customer evidence manifest and a validation result source with `validation_summary.json` (`--a/--b` creating `<out>\\realset_validation`, an existing `<out>\\realset_validation`, or `--exit-audit-results-dir`) so the release flow cannot silently run a synthetic final gate.",
                "",
                "Confirmed-only CAD cloud export is performed from reviewer state in the Workbench.",
                "For non-interactive validation, pass `--cloud-selection-csv <csv>` to export only explicit reviewed zones.",
                "",
                "## Resume (source checkout)",
                "Recreate zone/register/cloud artifacts from saved compare state:",
                "",
                "```powershell",
                "python scripts\\validate_drawing_compare_realset.py --a <A> --b <B> --out <out> --reuse-compare-state <state> --skip-compare --export-profile sharable --change-zone-report --executive-review --review-dashboard --export-viewer-package",
                "```",
            ]
        ),
        encoding="utf-8",
    )

    sample = {
        "datasets": [
            {
                "name": "sample_project",
                "a": "<before_folder_or_file>",
                "b": "<after_folder_or_file>",
                "recursive": False,
                "skip_compare": False,
                "max_workers": 1,
                "export_profile": args.export_profile,
                "change_zone_report": True,
                "executive_review": True,
                "review_dashboard": True,
                "export_viewer_package": True,
                "viewer_render_policy": args.viewer_render_policy,
                "p5_g3_realset_gate": True,
                "export_marked_pdf": not args.skip_marked_pdf,
                "marked_pdf_mode": "selected",
                "cloud_selection_csv": str(args.cloud_selection_csv) if args.cloud_selection_csv else None,
                "review_ground_truth": str(args.review_ground_truth) if args.review_ground_truth else None,
                "max_cloud_regions_per_pair": args.max_cloud_regions_per_pair,
                "max_cloud_regions_total": args.max_cloud_regions_total,
            }
        ]
    }
    (out_dir / "sample_manifest.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    review_truth_template = [
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status,notes",
        "S-001,member|mixed,BEAM;added,cad,cad_entity,exact,member add/delete/move example",
        "S-002,dimension|mixed,400x400;500x500,pdf,pdf_text|pdf_ocr|pdf_visual|hybrid,exact|page_fallback,section/dimension change example",
        "S-003,rebar|mixed,D13@100;D13@200,pdf,pdf_text|pdf_ocr|pdf_visual|hybrid,exact|page_fallback,D13 spacing change example",
        "S-004,rebar|mixed,SHD13@100;SHD13@200,cad,cad_entity,exact,SHD13 spacing change example",
        "S-005,grid,GRID A-B;GRID A-C,pdf,pdf_text|pdf_ocr|pdf_visual|hybrid,exact|page_fallback,grid change example",
    ]
    (out_dir / "review_ground_truth_template.csv").write_text(
        "\n".join(review_truth_template) + "\n",
        encoding="utf-8",
    )
    review_decision_truth_template = [
        "pair_uuid,zone_id,drawing_label,structural_bucket,human_label,source_format,detection_source,bbox_status,notes",
        "pair-001,zone-001,S-001,member_add_delete_move,true_positive,cad,cad_entity,exact,replace with customer-reviewed decision",
        "pair-002,zone-002,S-002,section_dimension_change,false_positive,pdf,pdf_text,exact,replace with customer-reviewed decision",
    ]
    (out_dir / "review_decision_truth_template.csv").write_text(
        "\n".join(review_decision_truth_template) + "\n",
        encoding="utf-8",
    )
    dataset_strata_template = [
        "pair_uuid,drawing_label,format_pair,sheet_type,risk_class,large_dwg,block_text_case,negative_control,notes",
        "pair-001,S-001,dwg_dxf,plan,standard,false,true,false,replace with customer dataset strata",
        "pair-002,S-002,pdf_pdf,section,raster_pdf,false,false,false,replace with customer dataset strata",
    ]
    (out_dir / "dataset_strata_template.csv").write_text(
        "\n".join(dataset_strata_template) + "\n",
        encoding="utf-8",
    )

    customer_evidence_manifest_template = {
        "schema_version": 1,
        "evidence_level": "customer_grade",
        "dataset_id": "",
        "dataset_provenance": {
            "source_kind": "",
            "source_description": "",
            "approval_status": "",
            "approver": "",
        },
        "validation_date": "",
        "sheet_count": 0,
        "ground_truth_owner": "",
        "format_coverage": {
            "dwg_dxf": False,
            "pdf_pdf": False,
            "cad_pdf_blocked": False,
        },
        "structural_coverage": [
            "member_add_delete_move",
            "section_dimension_change",
            "d13_spacing_change",
            "shd13_spacing_change",
            "grid_change",
            "structural_text_change",
        ],
        "ground_truth": {
            "status": "",
            "row_count": 0,
            "review_ground_truth_csv": "",
        },
        "review_decision_quality": {
            "status": "",
            "review_decision_truth_csv": "",
            "labeled_rows": 0,
            "overall_precision": 0.0,
            "false_positive_rate": 0.0,
            "bucket_labeled_rows": {},
            "bucket_precision": {},
        },
        "dataset_strata": {
            "status": "",
            "dataset_strata_csv": "",
            "rows": 0,
            "format_pair_counts": {},
            "sheet_type_counts": {},
            "cad_rows": 0,
            "raster_or_low_quality_rows": 0,
            "large_dwg_rows": 0,
            "block_text_rows": 0,
            "negative_control_rows": 0,
        },
        "first_interactive_readiness": {
            "status": "",
            "max_review_dashboard_ready_s": 0.0,
            "max_first_top_issue_ready_s": 0.0,
            "max_viewer_metadata_ready_s": 0.0,
        },
        "bbox_quality": {
            "status": "",
            "relative_only_ratio": 0.0,
            "page_fallback_ratio": 0.0,
            "top_priority_relative_only": False,
        },
        "large_dwg_resource_probe": {
            "status": "",
            "path": "",
            "peak_rss_mb": 0.0,
            "progress_max_gap_s": 0.0,
            "cancel_probe": {},
        },
        "p5_g7_forced_tile_eviction": {
            "schema_version": 1,
            "status": "not_provided",
            "required": False,
            "expected_tile_cache_mb": None,
            "proof_count": 0,
            "passed_proof_count": 0,
            "proofs": [],
            "release_manifests": [],
            "issues": [],
        },
        "operator_dry_run": {
            "status": "",
            "reviewer_role": "",
            "confirmed_export_checked": False,
            "workflow_checks": [
                "input_selection",
                "automatic_compare_completed",
                "top_structural_review_queue_seen",
                "selected_zone_before_after_sync_zoom",
                "korean_reason_summary_reviewed",
                "confirmed_false_positive_hold_used",
                "confirmed_only_export_checked",
                "sharable_path_leakage_checked",
            ],
            "artifacts": {
                "notes_file": "",
                "screenshots_dir": "",
                "confirmed_export_artifact": "",
            },
            "notes": "",
        },
        "path_leakage_audit": {
            "status": "",
            "leak_count": None,
            "audit_json": "",
        },
        "cad_policy_evidence": {
            "block_text_detection_without_expansion": False,
        },
        "selected_zone_performance": {
            "status": "",
            "completed_outputs": 0,
            "telemetry_outputs": 0,
            "max_cold_zone_render_ms": 10000.0,
            "max_cache_hit_zone_render_ms": 2000.0,
            "max_cold_p95_ms": 0.0,
            "max_cache_hit_p95_ms": 0.0,
        },
        "workbench_acceptance": {
            "status": "",
            "summary_count": 0,
            "passed_summary_count": 0,
            "required_items": ["5.", "8.", "8b.", "9b.", "9c.", "10."],
            "summaries": [],
            "failures": [],
        },
        "readiness": {
            "status": "",
            "issue_count": 0,
            "issues": [],
            "warning": (
                "Do not use this template as final MVP completion evidence. "
                "Generate customer_evidence_manifest.json with cli/prepare_drawing_compare_customer_evidence.py."
            ),
        },
    }
    (out_dir / "customer_evidence_manifest_template.json").write_text(
        json.dumps(customer_evidence_manifest_template, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    operator_checklist = [
        "# Drawing Compare Workbench Operator Dry-Run Checklist",
        "",
        "Fill this out on the customer/customer-grade validation run. Keep the check ids unchanged.",
        "",
        "reviewer_role: structural_review_lead",
        "",
        "- [ ] input_selection - selected two DWG/DXF files/folders or two PDF files/folders.",
        "- [ ] automatic_compare_completed - automatic comparison completed and `_SUCCESS` exists.",
        "- [ ] top_structural_review_queue_seen - first screen showed structural-core Top 3-5 review queue before raw counts.",
        "- [ ] selected_zone_before_after_sync_zoom - selected one change zone and verified Before/After zoomed to the same review window.",
        "- [ ] korean_reason_summary_reviewed - reviewed Korean change summary and reason text.",
        "- [ ] confirmed_false_positive_hold_used - marked at least one applicable zone across confirmed / false_positive / hold during the dry run.",
        "- [ ] confirmed_only_export_checked - verified confirmed-only cloud mark/report export excludes false_positive and hold zones.",
        "- [ ] sharable_path_leakage_checked - verified customer-shareable package path leakage audit reports 0.",
        "",
        "Operator notes:",
        "- Replace this line with concrete dry-run observations: reviewed drawing/zone ids, synchronized Before/After result, Korean summary/reason check, decisions made, confirmed-only export artifact, and path leakage audit result.",
    ]
    (out_dir / "operator_dry_run_checklist_template.md").write_text(
        "\n".join(operator_checklist),
        encoding="utf-8",
    )

    prompt_to_artifact_checklist = [
        "# Drawing Compare MVP Prompt-to-Artifact Checklist",
        "",
        "Use this file before any customer MVP exit decision. A green release step is not enough; every row needs concrete evidence from the customer/customer-grade run.",
        "",
        "| Requirement | Required artifact or gate | Completion evidence |",
        "| --- | --- | --- |",
        "| DWG/DXF comparison supported | `validation_summary.json`, `review_dashboard.json`, `review_queue.csv` from DWG/DXF validation outputs | Completed CAD pairs with `_SUCCESS`, both `.dwg` and `.dxf` source-extension evidence, `source_format=cad`, and `detection_source=cad_entity` review_queue items |",
        "| PDF-PDF comparison supported with CAD-equivalent UX | PDF validation output with `review_dashboard`, viewer package, and selected-zone evidence | Completed PDF pairs with `_SUCCESS`, `.pdf` source-extension evidence on both sides, image-pixel PDF change zones, and Top review_queue items |",
        "| CAD-PDF cross comparison blocked | `blocked_pairs.csv` | At least one CAD/PDF pair row has CAD/PDF kinds and a clear blocked/cross-family reason, not only a summary count |",
        "| Two files/folders are selected and automatic comparison completes | Completed operator dry-run notes plus validation output | Operator workflow ids `input_selection` and `automatic_compare_completed` are checked; notes confirm two DWG/DXF files/folders or two PDF files/folders were selected and comparison completed with `_SUCCESS` |",
        "| First screen shows structural-core Top 3-5 before raw counts | `change_artifacts/review_dashboard.json` and Workbench acceptance Item 5 | `review_queue.mode=structural_core`, `top_per_drawing=3..5`, non-empty items for every completed output |",
        "| `review_queue` is a first-class object keyed by `pair_uuid + zone_id` | `review_queue.csv`, `review_dashboard.json`, strict audit `review_queue_required_fields` | Required fields present, canonical status/domain values, unique non-empty `pair_uuid:zone_id` units |",
        "| CAD truth layer uses entity diff and structural text candidates | Strict audit `cad_structural_text_modified_grouping` plus CAD validation output | `TEXT/MTEXT/ATTRIB/ATTDEF/INSERT` evidence and `@100 -> @200` grouped as modified when possible |",
        "| CAD block attribute/text changes work with blocks unexpanded | CAD validation output generated with `--no-expand-blocks`, strict audit `cad_block_text_detection_without_expansion` | `input.cad_policy.expand_blocks=false`, `block_text_detection=true`, and a CAD `TEXT/MTEXT/ATTRIB/ATTDEF/INSERT` `@100 -> @200` review_queue item grouped as modified |",
        "| PDF truth source and bbox policy are explicit | `change_artifacts/change_zones.csv`, strict audit `pdf_bbox_image_pixels_policy` | PDF rows use `bbox_coordinate_space=image_pixels`, PDF detection source, and explicit bbox status |",
        "| Selected zone synchronizes Before/After zoom | `viewer/selected_zone_evidence.json`, `viewer/viewer_perf.json`, Workbench acceptance Item 9b, screenshots | Selected-zone telemetry plus programmatic proof that Before/After use the same selected zone and crop window |",
        "| UI remains responsive during selected-zone rendering | Workbench acceptance Item 9c, `viewer/viewer_perf.json` | Selected-zone crop rendering is QProcess-backed, timeout-bounded, and leaves the Qt event loop live |",
        "| Korean summary and reason explain the change | `review_queue.csv`, `review_dashboard.json`, operator checklist | Non-empty `change_summary_ko` and `reason_ko`; operator checked Korean reason summary review |",
        "| Review decisions support confirmed / false_positive / hold | Workbench review state, acceptance summary, operator checklist | Operator used relevant decisions and confirmed non-confirmed decisions are excluded from confirmed export |",
        "| Confirmed-only cloud/report export | `artifacts/confirmed_clouds/*_confirmed.{png,pdf,dxf}`, report PDF, strict audit `confirmed_only_cloud_and_report_export` | Confirmed artifacts exist and no non-confirmed files appear under confirmed export folder |",
        "| Sharable export leaks no absolute/cache/state/temp paths | `sharable_path_audit.json`, `validation_summary.sharable_audit.leak_count`, strict audit `sharable_path_leakage_zero` | Explicit `leak_count=0` for every customer-shareable validation output |",
        "| Raw JSONL/NDJSON streams excluded from sharable output | Strict audit `sharable_raw_jsonl_streams_absent` | No `.jsonl` or `.ndjson` raw streams remain in audited sharable outputs |",
        "| AI embedding/LLM is optional | `ai_policy.json`, `validation_summary.ai_policy`, strict audit `ai_optional_heuristic_fallback` | Missing models are warning-only and classifier fallback is heuristic |",
        "| Large DWG performance/progress probe remains within budget | `large_dwg_probe.json`, strict audit `large_dwg_performance_probe` | Probe shows bounded elapsed time, streamed change-zone output, capped in-memory records, and forwarded DXF compare progress |",
        "| Large-DWG resource and cancel recovery are proven | `large_dwg_probe.json`, manifest `large_dwg_resource_probe`, strict audit `large_dwg_resource_and_cancel_probe` | peak_rss_mb<=4096, progress_max_gap_s<=10, cancel status passed, cancel_to_idle_s<=10, partial outputs cleaned, worker_processes_left=0 |",
        "| First interactive screen is directly measured | `validation_summary.first_interactive_ready`, Workbench acceptance `runtime_metrics`, manifest `first_interactive_readiness` | Dashboard <=600s or speed <=300s; first top issue <=600s; viewer metadata <=900s; app open <=15000ms and first zone open <=5000ms where Workbench smoke is available |",
        "| Selected-zone performance meets MVP budgets | `viewer/viewer_perf.json`, `selected_zone_performance` in customer manifest | Telemetry covers every completed output; cold p95 <= 10000ms and cache-hit p95 <= 2000ms |",
        "| P5-G3 realset performance gate passes | `validation_summary.p5_g3_realset_gate`, strict audit `p5_g3_realset_release_gate` | Runtime budget, viewer perf, selected-zone render, nonblank visual output, and tile-manifest payload consistency all pass for every completed validation output |",
        "| P5-G16 real-corpus replay passes | `p5_g16_real_corpus_replay.json`, customer manifest `performance_benchmarks.p5_g16_real_corpus_replay`, strict audit `p5_g16_real_corpus_replay` | Replay JSON has `status=passed`, hashes match the audited validation summary and customer manifest, and final audit receives `--p5-g16-benchmark-json` or discovers the manifest reference |",
        "| P5-G22 actual GUI soak passes | `p5_g22_actual_gui_soak.json`, customer manifest `performance_benchmarks.p5_g22_actual_gui_soak`, strict audit `p5_g22_actual_gui_soak` | GUI soak JSON has `status=passed`, hashes match the audited validation summary and customer manifest, final audit receives `--p5-g22-gui-soak-json`, and blank/stale/RSS/native-resource/worker cleanup gates pass |",
        "| P5-G26 selection-latency soak passes | `p5_g26_selection_latency_soak.json`, strict audit `p5_g26_selection_latency_soak` | Selection-latency JSON has `status=passed`, declares all required P5-G26 gate names, final audit receives `--p5-g26-selection-latency-json` or discovers the result-dir sibling/manifest reference, GUI/PDF hot-path contracts pass, zone-selection p95 is within budget, and background-work/stale/CAD-to-PDF hot-path counts remain zero |",
        "| P5-G27 selected-zone crop-first soak passes | `p5_g27_selected_zone_crop_soak.json`, customer manifest `performance_benchmarks.p5_g27_selected_zone_crop`, strict audit `p5_g27_selected_zone_crop_soak` | Crop-first JSON has `status=passed`, includes `p5_g27_real_renderer_bridge` to passed `p5_g16_real_corpus_replay.json`, final audit receives `--p5-g27-selected-zone-crop-json` or discovers the manifest/result-dir reference, crop is visible before vector focus, vector failure preserves background, real selected-zone render artifacts are nonblank/present, and blank/stale/cancel/timeout/fallback/orphan gates pass |",
        "| P5-G30 composite customer visual-performance release gate passes | `mvp_exit_audit.json`, strict audit `p5_g30_customer_visual_performance_release_gate` | Composite check passes and its evidence shows `p5_g3_realset_release_gate`, `p5_g16_real_corpus_replay`, `p5_g22_actual_gui_soak`, `p5_g24_visual_asset_policy`, `p5_g26_selection_latency_soak`, and `p5_g27_selected_zone_crop_soak` all passed |",
        "| P5-G7 forced tile-eviction proof is preserved when claimed | `p5_g7_forced_tile_eviction` in customer manifest, optional proof validation output | If realset tile eviction is claimed, proof output is passed via `--p5-g7-tile-eviction-proof-dir`, not counted in the 20-50 sheet corpus and not included in final audit `--results-dir` |",
        "| Closeout pre-execution readiness is captured and independently audited | `closeout_readiness.json` and `closeout_plan.json` from `cli\\closeout_drawing_compare_customer_evidence.py --dry-run --plan-json <closeout_plan.json> --readiness-json <closeout_readiness.json>`, then `closeout_readiness_audit.json` from `cli\\audit_closeout_readiness.py --readiness-json <closeout_readiness.json> --plan-json <closeout_plan.json> --require-ready` | readiness audit `status=passed`; readiness `status=ready_for_closeout`; `preflight.status=passed`; `preflight.issue_count=0`; output paths are set; `plan.available=true`; `plan.invariants.proof_dirs_excluded_from_final_audit_results_dir=true`; `plan.invariants.final_audit_results_dirs_equal_standard_result_dirs=true`; `plan.invariants.final_audit_p5_g16_benchmark_jsons_equal_plan=true`; `plan.invariants.final_audit_p5_g22_gui_soak_jsons_equal_plan=true`; `plan.invariants.final_audit_p5_g27_selected_zone_crop_jsons_equal_plan=true`; tile-cache env is isolated to proof validation steps |",
        "| PDF selected-zone fallback quality is bounded | review queue and selected-zone evidence `bbox_status`, manifest `bbox_quality`, strict audit `pdf_selected_zone_bbox_quality` | Top-priority relative_only is forbidden; relative_only ratio<=0.10; page_fallback ratio<=0.30 |",
        "| Operational preflight passed | `preflight_report.json`, strict audit `preflight_passed` | Legacy ODA fallback not required for customer builds; PyMuPDF, rtree, cache/state/output, disk/temp, long path, font, PDF support checks passed |",
        "| Pre-final inventory has no stale customer manifest warnings | `inventory.json` from `inventory_drawing_compare_customer_evidence.py --large-dwg-probe <large_dwg_probe.json>` | `status=ready_for_manifest`, `diagnostics.large_dwg_probe_passed=true`, `customer_evidence_manifest_summaries` checked, and no entries in `diagnostics.customer_evidence_manifests_not_ready` or `diagnostics.customer_evidence_manifests_missing_approved_ground_truth` |",
        "| Customer/customer-grade evidence is declared and approved | `customer_evidence_manifest.json` generated by `prepare_drawing_compare_customer_evidence.py` | `dataset_provenance.source_kind` is `customer` or `customer_grade`, `approval_status=approved_for_mvp_exit`, approver set |",
        "| Structural-core recall is proven by approved ground truth | `review_ground_truth.csv`, validation `review_ground_truth` metrics, strict audit `structural_review_queue_recall` and `structural_core_coverage` | `ground_truth.status=approved`; required buckets are represented; `review_ground_truth.csv` keeps required columns `drawing_label,category,summary_contains,source_format,detection_source,bbox_status`; expected changes match Top review_queue items |",
        "| Review queue precision and false-positive burden are bounded | `review_decision_truth.csv`, manifest `review_decision_quality`, strict audit `review_queue_precision` | Labeled rows >=20, overall precision >=0.85, bucket precision >=0.75, false-positive rate <=0.15, bucket rows >=2 |",
        "| Dataset is risk-stratified, not count-only | `dataset_strata.csv`, manifest `dataset_strata`, strict audit `dataset_strata_coverage` | Rows equal sheet_count; CAD>=8, PDF-PDF>=8, raster/low-quality>=2, large-DWG>=2, block-text>=2, each sheet type>=2, negative/control>=2 |",
        "| Operator dry-run exercised the review-lead workflow | Completed `operator_dry_run_notes.md` copied from the checklist template, screenshots, manifest `operator_dry_run` | `reviewer_role` is an approved structural review lead/team lead role; required workflow ids are checked: input selection, auto compare, Top queue, sync zoom, Korean summary, decisions, confirmed-only export, path audit; substantive observed notes are present; template/handoff paths are rejected as evidence; checklist-only copies are rejected as evidence |",
        "| Final release audit is customer-grade | `mvp_exit_audit.json` from `audit_drawing_compare_mvp_exit.py --evidence-level customer_grade` | Audit status is `passed`; no failed checks remain; `p5_g30_customer_visual_performance_release_gate` is present and passed |",
        "",
        "Minimum final command:",
        "",
        "```powershell",
        "python cli\\audit_drawing_compare_mvp_exit.py --results-dir <dwg_validation> --results-dir <pdf_validation> --results-dir <cad_pdf_block_validation> --results-dir <cad_block_text_no_expand_validation> --release-manifest <release_manifest.json> --large-dwg-probe <large_dwg_probe.json> --require-large-dwg-probe --customer-evidence-manifest <customer_evidence_manifest.json> --evidence-level customer_grade --require-p5-g3-realset-gate --p5-g16-benchmark-json <p5_g16_real_corpus_replay.json> --p5-g22-gui-soak-json <p5_g22_actual_gui_soak.json> --p5-g27-selected-zone-crop-json <p5_g27_selected_zone_crop_soak.json> --min-total-pairs 20 --max-total-pairs 50 --max-first-review-ready-s 1800 --max-cold-zone-render-ms 10000 --max-cache-hit-zone-render-ms 2000 --out <mvp_exit_audit.json>",
        "```",
    ]
    (out_dir / "mvp_exit_prompt_to_artifact_checklist.md").write_text(
        "\n".join(prompt_to_artifact_checklist) + "\n",
        encoding="utf-8",
    )

    closeout_packet = [
        "# Drawing Compare Customer Evidence Closeout Packet",
        "",
        "Purpose: capture the final customer-grade evidence needed before the Drawing Compare Workbench MVP can be declared complete. This file is guidance only; it is rejected as customer evidence if supplied as an operator notes or ground-truth artifact.",
        "",
        "## Required External Artifacts",
        "",
        "| Artifact | Required contents | Must not be |",
        "| --- | --- | --- |",
        "| `review_ground_truth.csv` | Non-empty customer/customer-grade truth rows using `drawing_label,category,summary_contains,source_format,detection_source,bbox_status`; status supplied as `--ground-truth-status approved` | `review_ground_truth_template.csv`, a renamed template with example/sample/template markers, or a handoff/checklist document |",
        "| `review_decision_truth.csv` | >=20 labeled queue decisions using `pair_uuid,zone_id,drawing_label,structural_bucket,human_label,source_format,detection_source,bbox_status,notes`; precision >=0.85, bucket precision >=0.75, false-positive rate <=0.15 | `review_decision_truth_template.csv`, copied examples, templates, or handoff/checklist documents |",
        "| `dataset_strata.csv` | One row per manifest sheet using `pair_uuid,drawing_label,format_pair,sheet_type,risk_class,large_dwg,block_text_case,negative_control,notes`; satisfies CAD/PDF/risk/sheet-type/control minima | `dataset_strata_template.csv`, copied examples, templates, or handoff/checklist documents |",
        "| `large_dwg_probe.json` | Includes elapsed/stream proof plus `peak_rss_mb`, `progress_max_gap_s`, and passed `cancel_probe` cleanup metrics | Probe without RSS/progress gap/cancel recovery fields |",
        "| `operator_dry_run_notes.md` | Completed by an approved structural review lead/team lead; includes an explicit reviewer-role key line, every required workflow id checked, and concrete observations covering drawing/zone reviewed, synced Before/After zoom, Korean summary/reason, decisions, confirmed-only export, and path audit result | `operator_dry_run_checklist_template.md`, this closeout packet, a checklist-only copy, or placeholder notes |",
        "",
        "## Inventory Must Be Ready",
        "",
        "```powershell",
        "python cli\\inventory_drawing_compare_customer_evidence.py --root <customer_evidence_root> --large-dwg-probe <large_dwg_probe.json> --portable-paths --out <inventory.json>",
        "```",
        "",
        "Include the current release output folder as one inventory root so `release_manifest.json` is discovered and the recommended final audit command points at the audited package. Use `--portable-paths` only for shareable inventory JSON; omit it for local-only inventory when you want directly runnable absolute-path commands.",
        "",
        "Do not generate the final manifest until `inventory.json` reports `status=ready_for_manifest` and these diagnostics are clean:",
        "",
        "- `diagnostics.valid_review_ground_truth_csv_candidates` contains the completed truth CSV.",
        "- `diagnostics.valid_review_decision_truth_csv_candidates` contains a passing precision/false-positive CSV.",
        "- `diagnostics.valid_dataset_strata_csv_candidates` contains a passing stratified dataset CSV.",
        "- `diagnostics.first_interactive_readiness.status=passed`.",
        "- `diagnostics.bbox_quality.status=passed`.",
        "- `diagnostics.operator_notes_with_approved_structural_role=true`.",
        "- `diagnostics.operator_notes_with_substantive_review_notes=true`.",
        "- `diagnostics.large_dwg_probe_passed=true` and `diagnostics.large_dwg_probe_issues=[]`.",
        "- `customer_evidence_manifest_summaries` has no stale manifest that will be reused accidentally.",
        "- `diagnostics.customer_evidence_manifests_not_ready=[]`.",
        "- `diagnostics.customer_evidence_manifests_missing_approved_ground_truth=[]`.",
        "",
        "## One-Command Closeout Runner",
        "",
        "```powershell",
        "python cli\\closeout_drawing_compare_customer_evidence.py --source-checkout <source_checkout> --out <closeout_out> --standard-results-dir <dwg_validation> --standard-results-dir <pdf_validation> --standard-results-dir <cad_pdf_block_validation> --standard-results-dir <cad_block_text_no_expand_validation> --customer-evidence-manifest <customer_evidence_manifest.json> --release-manifest <release_manifest.json> --large-dwg-probe <large_dwg_probe.json> --review-ground-truth <review_ground_truth.csv> --review-decision-truth <review_decision_truth.csv> --dataset-strata <dataset_strata.csv> --operator-notes-file <operator_dry_run_notes.md> --confirmed-export-artifact <artifacts\\confirmed_clouds\\pair_confirmed.png> --p5-g27-selected-zone-crop-json <p5_g27_selected_zone_crop_soak.json> --dataset-id <dataset_id> --dataset-source-description \"20-50 sheet customer-grade validation set approved for MVP exit\" --dataset-approver <approver> --ground-truth-owner <owner> --min-total-pairs 20 --max-total-pairs 50",
        "```",
        "",
        "Run the same command first with `--dry-run --plan-json <closeout_plan.json> --readiness-json <closeout_readiness.json>`. Then run `python cli\\audit_closeout_readiness.py --readiness-json <closeout_readiness.json> --plan-json <closeout_plan.json> --require-ready --out <closeout_readiness_audit.json>`. The dry run fails early when `--source-checkout` lacks `scripts/` or `src/services/comparison/manifest_provenance.py`, when required customer evidence files are missing, or when existing validation outputs do not contain both `validation_summary.json` and `_SUCCESS`. The readiness audit fails if the plan and readiness summary disagree, P5-G16 replay JSON, P5-G22 GUI soak JSON, or supplied P5-G27 crop-first JSON is not routed through prepare/evidence/final-audit steps, proof dirs enter final audit `--results-dir`, or `DRAWING_COMPARE_TILE_CACHE_MB` leaks outside P5-G7 proof validation steps. Keep `p5_g26_selection_latency_soak.json` beside the audited `validation_summary.json` or pass it explicitly to manifest preparation/final audit so customer-grade P5-G26 can be discovered. The supplied P5-G27 JSON must include `p5_g27_real_renderer_bridge` to the passed P5-G16 replay so final audit can bind crop-first lifecycle safety to real nonblank selected-zone render artifacts. The final audit must then pass `p5_g30_customer_visual_performance_release_gate`, proving P5-G3, P5-G16, P5-G22, P5-G24, P5-G26, and P5-G27 were all discovered and passed together. The readiness report records `status=ready_for_closeout` or `status=preflight_failed`, `preflight.issue_count`, `preflight.issues`, `outputs.plan_json`, `outputs.readiness_json`, `outputs.failure_json`, `outputs.inventory_json`, `outputs.customer_evidence_manifest`, `outputs.audit_json`, `routing_expectations.require_p5_g7_tile_eviction_proof`, `routing_expectations.p5_g16_real_corpus_replay_generation_enabled`, `routing_expectations.p5_g22_actual_gui_soak_generation_enabled`, `plan.available=true`, `plan.step_count`, `plan.invariants.proof_dirs_excluded_from_final_audit_results_dir=true`, `plan.invariants.final_audit_p5_g16_benchmark_jsons_equal_plan=true`, `plan.invariants.final_audit_p5_g22_gui_soak_jsons_equal_plan=true`, and `plan.invariants.final_audit_p5_g27_selected_zone_crop_jsons_equal_plan=true`.",
        "",
        "Retain `closeout_readiness.json` with `closeout_plan.json` and `closeout_readiness_audit.json`, plus `inventory.json`, `customer_evidence_manifest.json`, `p5_g16_real_corpus_replay.json`, `p5_g22_actual_gui_soak.json`, `p5_g26_selection_latency_soak.json`, `p5_g27_selected_zone_crop_soak.json`, and `mvp_exit_audit.json` as the final closeout evidence packet. Do not run full closeout unless the readiness audit has `status=passed`, readiness `status=ready_for_closeout`, `preflight.status=passed`, `preflight.issue_count=0`, and the plan invariants are true. If `status=preflight_failed`, attach `closeout_readiness.json` as the preflight failure report and resolve `preflight.issues` before rerun.",
        "",
        "If any closeout subprocess returns non-zero, retain `<closeout_out>\\closeout_failure.json` or the file supplied with `--failure-json <closeout_failure.json>` together with `closeout_plan.json` and the parent console log before rerunning. The failure report includes `failure_kind=subprocess_nonzero_exit`, `failed_step.name`, `failed_step.returncode`, `failed_step.command_context`, `completed_steps`, `remaining_steps`, `plan_invariants`, `triage_hints`, and `stdout_stderr.capture_mode=inherited_console`.",
        "",
        "For a release that claims realset tile-cache eviction, append `--p5-g7-proof-validation-manifest <proof_manifest.json>` or `--p5-g7-tile-eviction-proof-dir <proof_validation>`, plus `--require-p5-g7-tile-eviction-proof --p5-g6-tile-cache-mb 0.25 --p5-g6-min-tile-evicted-pairs 1 --p5-g6-min-tile-evicted-bytes 1`. The closeout runner preserves proof evidence in `p5_g7_forced_tile_eviction` and keeps it out of the final audit `--results-dir` corpus.",
        "",
        "## Generate Customer Evidence Manifest",
        "",
        "```powershell",
        "python cli\\prepare_drawing_compare_customer_evidence.py --results-dir <dwg_validation> --results-dir <pdf_validation> --results-dir <cad_pdf_block_validation> --results-dir <cad_block_text_no_expand_validation> --out <customer_evidence_manifest.json> --dataset-id <dataset_id> --dataset-source-kind customer_grade --dataset-source-description \"20-50 sheet customer-grade validation set approved for MVP exit\" --dataset-approval-status approved_for_mvp_exit --dataset-approver <approver> --ground-truth-owner <owner> --review-ground-truth <review_ground_truth.csv> --ground-truth-status approved --review-decision-truth <review_decision_truth.csv> --dataset-strata <dataset_strata.csv> --large-dwg-probe <large_dwg_probe.json> --p5-g16-benchmark-json <p5_g16_real_corpus_replay.json> --p5-g22-gui-soak-json <p5_g22_actual_gui_soak.json> --p5-g27-selected-zone-crop-json <p5_g27_selected_zone_crop_soak.json> --operator-reviewer-role structural_review_lead --operator-notes-file <operator_dry_run_notes.md> --confirmed-export-artifact <artifacts\\confirmed_clouds\\pair_confirmed.png> --min-total-pairs 20 --max-total-pairs 50 --max-first-review-ready-s 1800 --max-cold-zone-render-ms 10000 --max-cache-hit-zone-render-ms 2000",
        "```",
        "",
        "The generated manifest must have `readiness.status=ready`, `readiness.issue_count=0`, `ground_truth.status=approved`, `review_decision_quality.status=passed`, `dataset_strata.status=passed`, `first_interactive_readiness.status=passed`, `bbox_quality.status=passed`, `large_dwg_resource_probe.status=passed`, `dataset_provenance.approval_status=approved_for_mvp_exit`, and `cad_policy_evidence.block_text_detection_without_expansion=true`.",
        "",
        "## Final Customer-Grade Audit",
        "",
        "```powershell",
        "python cli\\audit_drawing_compare_mvp_exit.py --results-dir <dwg_validation> --results-dir <pdf_validation> --results-dir <cad_pdf_block_validation> --results-dir <cad_block_text_no_expand_validation> --release-manifest <release_manifest.json> --large-dwg-probe <large_dwg_probe.json> --require-large-dwg-probe --customer-evidence-manifest <customer_evidence_manifest.json> --evidence-level customer_grade --require-p5-g3-realset-gate --p5-g16-benchmark-json <p5_g16_real_corpus_replay.json> --p5-g22-gui-soak-json <p5_g22_actual_gui_soak.json> --p5-g27-selected-zone-crop-json <p5_g27_selected_zone_crop_soak.json> --min-total-pairs 20 --max-total-pairs 50 --max-first-review-ready-s 1800 --max-cold-zone-render-ms 10000 --max-cache-hit-zone-render-ms 2000 --out <mvp_exit_audit.json>",
        "```",
        "",
        "Completion can be declared only when `<mvp_exit_audit.json>` has `status=passed`, zero failed checks, and `p5_g30_customer_visual_performance_release_gate` passed.",
    ]
    (out_dir / "customer_evidence_closeout_packet.md").write_text(
        "\n".join(closeout_packet) + "\n",
        encoding="utf-8",
    )

    (out_dir / "customer_evidence_request_ko.md").write_bytes(
        _customer_evidence_request_ko_bytes()
    )


def _customer_evidence_request_ko_bytes() -> bytes:
    data = CUSTOMER_EVIDENCE_REQUEST_KO_SOURCE.read_bytes()
    text = data.decode("utf-8")
    required_markers = (
        "Drawing Compare 고객급 증거 요청서",
        "review_ground_truth.csv",
        "review_decision_truth.csv",
        "dataset_strata.csv",
        "operator_dry_run_notes.md",
        "customer_grade",
        "status=passed",
    )
    missing = [marker for marker in required_markers if marker not in text]
    if missing:
        raise ValueError(
            f"{CUSTOMER_EVIDENCE_REQUEST_KO_SOURCE} is missing required markers: {missing}"
        )
    return data


def _write_customer_shareable_package(out_dir: Path) -> dict[str, Any]:
    package_dir = out_dir / "customer_shareable_package"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    for filename in (
        "README_INTERNAL_PILOT.md",
        "sample_manifest.json",
        "customer_evidence_manifest_template.json",
        "operator_dry_run_checklist_template.md",
        "review_ground_truth_template.csv",
        "review_decision_truth_template.csv",
        "dataset_strata_template.csv",
        "mvp_exit_prompt_to_artifact_checklist.md",
        "customer_evidence_closeout_packet.md",
        "customer_evidence_request_ko.md",
    ):
        source = out_dir / filename
        if source.exists():
            shutil.copy2(source, package_dir / filename)

    cli_source = out_dir / "cli"
    if cli_source.exists():
        shutil.copytree(cli_source, package_dir / "cli", ignore=_customer_package_ignore)

    app_source = out_dir / "dist" / "DrawingCompareWorkbench"
    if app_source.exists():
        app_target = package_dir / "app" / "DrawingCompareWorkbench"
        app_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app_source, app_target, ignore=_customer_package_ignore)

    contents = sorted(
        str(path.relative_to(package_dir)).replace("\\", "/")
        for path in package_dir.rglob("*")
        if path.is_file()
    )
    contents = sorted({*contents, "customer_package_manifest.json", "customer_package_path_audit.json"})
    package_manifest = {
        "schema_version": 1,
        "package_type": "customer_shareable",
        "internal_release_manifest_included": False,
        "path_leakage_audit": "customer_package_path_audit.json",
        "contents": contents,
        "notes": [
            "Internal release_manifest.json is intentionally excluded because it contains build-machine paths.",
            "Python bytecode/cache files are intentionally excluded because they can embed build-machine paths.",
            "Use customer_package_path_audit.json to verify text, selected binary, and disallowed-file leakage before sharing.",
        ],
    }
    manifest_path = package_dir / "customer_package_manifest.json"
    manifest_path.write_text(
        json.dumps(package_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    audit = _audit_customer_shareable_package(package_dir)
    audit_path = package_dir / "customer_package_path_audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_base = out_dir / "DrawingCompareWorkbench_customer_shareable"
    zip_path = zip_base.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(zip_base), "zip", root_dir=package_dir)

    return {
        "package_dir": package_dir,
        "zip_path": zip_path,
        "manifest_path": manifest_path,
        "audit_path": audit_path,
        "audit_status": audit["status"],
        "leak_count": audit["leak_count"],
    }


def _audit_customer_shareable_package(package_dir: Path) -> dict[str, Any]:
    leaks: list[dict[str, Any]] = []
    disallowed_files: list[str] = []
    scanned_files = 0
    scanned_app_first_party_files = 0
    scanned_binary_files = 0
    skipped_app_internal_files = 0
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(package_dir)
        if _is_customer_package_disallowed_bytecode(relative_path):
            disallowed_files.append(str(relative_path).replace("\\", "/"))
            continue
        if relative_path.parts and relative_path.parts[0] == "app":
            if _is_first_party_app_text(relative_path, path):
                scanned_app_first_party_files += 1
                leaks.extend(
                    _path_leaks_in_file(
                        path,
                        relative_path,
                        CUSTOMER_PACKAGE_APP_BUILD_PATH_LEAK_RE,
                    )
                )
            elif _is_customer_package_binary_scan_candidate(relative_path, path):
                scanned_binary_files += 1
                leaks.extend(
                    _path_leaks_in_binary_file(
                        path,
                        relative_path,
                        CUSTOMER_PACKAGE_BINARY_BUILD_PATH_LEAK_RE,
                    )
                )
            else:
                skipped_app_internal_files += 1
            continue
        if path.suffix.lower() not in CUSTOMER_PACKAGE_TEXT_SUFFIXES:
            continue
        scanned_files += 1
        leaks.extend(_path_leaks_in_file(path, relative_path, CUSTOMER_PACKAGE_PATH_LEAK_RE))
    return {
        "schema_version": 1,
        "status": "passed" if not leaks and not disallowed_files else "failed",
        "scanned_files": scanned_files,
        "scanned_app_first_party_files": scanned_app_first_party_files,
        "scanned_binary_files": scanned_binary_files,
        "skipped_app_internal_files": skipped_app_internal_files,
        "disallowed_file_count": len(disallowed_files),
        "disallowed_files": disallowed_files,
        "leak_count": len(leaks),
        "leaks": leaks,
    }


def _customer_package_ignore(_directory: str, names: Sequence[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(name)
        if name in CUSTOMER_PACKAGE_EXCLUDED_DIRS or path.suffix.lower() in CUSTOMER_PACKAGE_EXCLUDED_SUFFIXES:
            ignored.add(name)
    return ignored


def _is_customer_package_disallowed_bytecode(relative_path: Path) -> bool:
    parts = set(relative_path.parts)
    return (
        bool(parts & CUSTOMER_PACKAGE_EXCLUDED_DIRS)
        or relative_path.suffix.lower() in CUSTOMER_PACKAGE_EXCLUDED_SUFFIXES
    )


def _is_first_party_app_text(relative_path: Path, path: Path) -> bool:
    parts = relative_path.parts
    return (
        len(parts) >= 5
        and parts[0] == "app"
        and parts[1] == "DrawingCompareWorkbench"
        and parts[2] == "_internal"
        and parts[3] == "src"
        and path.suffix.lower() in CUSTOMER_PACKAGE_APP_TEXT_SUFFIXES
    )


def _is_customer_package_binary_scan_candidate(relative_path: Path, path: Path) -> bool:
    parts = relative_path.parts
    normalized = str(relative_path).replace("\\", "/")
    if normalized == "app/DrawingCompareWorkbench/DrawingCompareWorkbench.exe":
        return True
    return (
        len(parts) >= 5
        and parts[0] == "app"
        and parts[1] == "DrawingCompareWorkbench"
        and parts[2] == "_internal"
        and parts[3] == "src"
        and path.suffix.lower() not in CUSTOMER_PACKAGE_APP_TEXT_SUFFIXES
    )


def _path_leaks_in_file(path: Path, relative_path: Path, pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for line_number, line in enumerate(text.splitlines(), start=1):
        matches = [match.group(0) for match in pattern.finditer(line)]
        if matches:
            leaks.append(
                {
                    "path": str(relative_path).replace("\\", "/"),
                    "line": line_number,
                    "matches": matches,
                }
            )
    return leaks


def _path_leaks_in_binary_file(
    path: Path,
    relative_path: Path,
    pattern: re.Pattern[bytes],
) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    data = path.read_bytes()
    matches = [
        match.group(0).decode("utf-8", errors="ignore")[:240]
        for match in pattern.finditer(data)
    ]
    if matches:
        leaks.append(
            {
                "path": str(relative_path).replace("\\", "/"),
                "line": None,
                "matches": sorted(set(matches)),
            }
        )
    return leaks


if __name__ == "__main__":
    raise SystemExit(main())
