# -*- coding: utf-8 -*-
"""Audit closeout_readiness.json before running customer closeout.

This script is intentionally read-only. It does not rerun validation, inventory,
manifest preparation, or final MVP audit commands. It verifies that the dry-run
readiness packet and command plan are internally consistent and that proof runs
cannot contaminate the final customer corpus audit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


TILE_CACHE_MB_ENV_VAR = "DRAWING_COMPARE_TILE_CACHE_MB"
REQUIRED_CORE_STEPS = [
    "inventory",
    "prepare_customer_evidence_manifest",
    "final_customer_grade_audit",
]


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    detail: str
    evidence: list[str] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "evidence": list(self.evidence or []),
        }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness-json", type=Path, required=True)
    parser.add_argument(
        "--plan-json",
        type=Path,
        help=(
            "Optional closeout_plan.json. When omitted, the path is resolved "
            "from readiness.outputs.plan_json."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Audit JSON output. Defaults to <readiness-json parent>/closeout_readiness_audit.json.",
    )
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Require readiness.status=ready_for_closeout and preflight.status=passed.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_audit(
        readiness_json=args.readiness_json,
        plan_json=args.plan_json,
        require_ready=bool(args.require_ready),
    )
    output = args.out or args.readiness_json.resolve().parent / "closeout_readiness_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1


def run_audit(
    *,
    readiness_json: Path,
    plan_json: Path | None = None,
    require_ready: bool = False,
) -> dict[str, Any]:
    readiness_path = readiness_json.resolve()
    readiness = _load_json(readiness_path)
    checks: list[ReadinessCheck] = []
    checks.append(_check_json_object("readiness_json_loadable", readiness, readiness_path))
    if not isinstance(readiness, dict):
        return _report(readiness_path, None, require_ready, checks)

    resolved_plan = _resolve_plan_path(readiness, plan_json)
    plan = _load_json(resolved_plan) if resolved_plan else None
    checks.append(_check_json_object("plan_json_loadable", plan, resolved_plan))

    checks.extend(
        [
            _check_schema_version(readiness, "readiness_schema_version"),
            _check_readiness_status(readiness, require_ready=require_ready),
            _check_preflight(readiness, require_ready=require_ready),
            _check_outputs(readiness, readiness_path, resolved_plan),
            _check_plan_summary(readiness, plan),
        ]
    )
    if isinstance(plan, dict):
        checks.extend(_check_plan(readiness, plan))

    return _report(readiness_path, resolved_plan, require_ready, checks)


def _check_json_object(name: str, payload: Any, path: Path | None) -> ReadinessCheck:
    if isinstance(payload, dict):
        return ReadinessCheck(name, True, "JSON object loaded", [str(path) if path else ""])
    if path is None:
        return ReadinessCheck(name, False, "path missing")
    return ReadinessCheck(name, False, f"JSON object missing or unreadable: {path}", [str(path)])


def _check_schema_version(payload: dict[str, Any], name: str) -> ReadinessCheck:
    value = payload.get("schema_version")
    return ReadinessCheck(
        name,
        value == 1,
        "schema_version=1" if value == 1 else f"schema_version must be 1, got {value!r}",
        [str(value)],
    )


def _check_readiness_status(payload: dict[str, Any], *, require_ready: bool) -> ReadinessCheck:
    status = str(payload.get("status", ""))
    if require_ready:
        passed = status == "ready_for_closeout"
        detail = "readiness is ready_for_closeout" if passed else f"status must be ready_for_closeout, got {status!r}"
    else:
        passed = status in {"ready_for_closeout", "preflight_failed"}
        detail = "readiness status is recognized" if passed else f"unrecognized readiness status: {status!r}"
    return ReadinessCheck("readiness_status", passed, detail, [status])


def _check_preflight(payload: dict[str, Any], *, require_ready: bool) -> ReadinessCheck:
    preflight = payload.get("preflight")
    if not isinstance(preflight, dict):
        return ReadinessCheck("preflight_status", False, "preflight block missing")
    status = str(preflight.get("status", ""))
    issue_count = _as_int(preflight.get("issue_count"))
    issues = preflight.get("issues")
    issue_list = issues if isinstance(issues, list) else []
    if require_ready:
        passed = status == "passed" and issue_count == 0 and issue_list == []
        detail = (
            "preflight passed with zero issues"
            if passed
            else f"preflight must pass with zero issues, got status={status!r}, issue_count={issue_count}, issues={issue_list!r}"
        )
    else:
        passed = status in {"passed", "failed"} and issue_count == len(issue_list)
        detail = "preflight block is internally consistent" if passed else "preflight block is inconsistent"
    return ReadinessCheck("preflight_status", passed, detail, [json.dumps(preflight, ensure_ascii=True, sort_keys=True)])


def _check_outputs(
    payload: dict[str, Any],
    readiness_path: Path,
    plan_path: Path | None,
) -> ReadinessCheck:
    outputs = payload.get("outputs")
    required = [
        "plan_json",
        "readiness_json",
        "failure_json",
        "inventory_json",
        "customer_evidence_manifest",
        "audit_json",
    ]
    failures: list[str] = []
    if not isinstance(outputs, dict):
        return ReadinessCheck("required_outputs_present", False, "outputs block missing")
    for key in required:
        if not str(outputs.get(key, "")).strip():
            failures.append(f"outputs.{key} is missing")
    readiness_output = str(outputs.get("readiness_json", ""))
    if readiness_output and _path_key(Path(readiness_output)) != _path_key(readiness_path):
        failures.append("outputs.readiness_json does not match audited readiness path")
    plan_output = str(outputs.get("plan_json", ""))
    if plan_path is not None and plan_output and _path_key(Path(plan_output)) != _path_key(plan_path):
        failures.append("outputs.plan_json does not match audited plan path")
    return ReadinessCheck(
        "required_outputs_present",
        not failures,
        "required output paths are present and consistent" if not failures else "; ".join(failures),
        [json.dumps(outputs, ensure_ascii=True, sort_keys=True)],
    )


def _check_plan_summary(readiness: dict[str, Any], plan: Any) -> ReadinessCheck:
    summary = readiness.get("plan")
    if not isinstance(summary, dict):
        return ReadinessCheck("readiness_plan_summary", False, "readiness.plan summary missing")
    failures: list[str] = []
    if summary.get("available") is not True:
        failures.append("readiness.plan.available must be true")
    if not isinstance(plan, dict):
        failures.append("plan JSON must be loadable")
    else:
        expected_count = len(plan.get("steps", [])) if isinstance(plan.get("steps"), list) else -1
        if _as_int(summary.get("step_count")) != expected_count:
            failures.append(
                f"readiness.plan.step_count must match plan steps ({summary.get('step_count')!r} != {expected_count})"
            )
        summary_names = [
            str(step.get("name", ""))
            for step in summary.get("steps", [])
            if isinstance(step, dict)
        ]
        plan_names = [
            str(step.get("name", ""))
            for step in plan.get("steps", [])
            if isinstance(step, dict)
        ]
        if summary_names and summary_names != plan_names:
            failures.append("readiness.plan.steps do not match closeout_plan steps")
        summary_invariants = summary.get("invariants")
        plan_invariants = plan.get("invariants")
        if summary_invariants != plan_invariants:
            failures.append("readiness.plan.invariants do not match closeout_plan invariants")
    return ReadinessCheck(
        "readiness_plan_summary",
        not failures,
        "readiness plan summary matches closeout plan" if not failures else "; ".join(failures),
    )


def _check_plan(readiness: dict[str, Any], plan: dict[str, Any]) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = [_check_schema_version(plan, "plan_schema_version")]
    steps = plan.get("steps")
    if not isinstance(steps, list):
        checks.append(ReadinessCheck("plan_steps_present", False, "plan.steps must be a list"))
        return checks
    checks.append(ReadinessCheck("plan_steps_present", True, "plan.steps present", [str(len(steps))]))

    checks.append(_check_required_step_order(steps))
    checks.append(_check_plan_invariants(plan))
    checks.append(_check_final_audit_results(plan, steps))
    checks.append(_check_inventory_and_prepare_routing(plan, steps))
    checks.append(_check_p5_g16_replay_routing(plan, steps))
    checks.append(_check_p5_g22_gui_soak_routing(plan, steps))
    checks.append(_check_p5_g27_selected_zone_crop_routing(plan, steps))
    checks.append(_check_p5_g28_cache_plateau_routing(plan, steps))
    checks.append(_check_tile_cache_env_isolation(readiness, plan, steps))
    checks.append(_check_final_audit_command(plan, steps))
    return checks


def _check_required_step_order(steps: list[Any]) -> ReadinessCheck:
    names = [str(step.get("name", "")) for step in steps if isinstance(step, dict)]
    failures: list[str] = []
    positions: dict[str, int] = {}
    for required in REQUIRED_CORE_STEPS:
        if required not in names:
            failures.append(f"{required} step missing")
        else:
            positions[required] = names.index(required)
    if not failures:
        seed_position = (
            names.index("prepare_customer_evidence_manifest_seed")
            if "prepare_customer_evidence_manifest_seed" in names
            else None
        )
        if not (
            positions["inventory"]
            < positions["prepare_customer_evidence_manifest"]
            < positions["final_customer_grade_audit"]
        ):
            failures.append("inventory, prepare, and final audit are out of order")
        if seed_position is not None and not (
            positions["inventory"] < seed_position < positions["prepare_customer_evidence_manifest"]
        ):
            failures.append("prepare_customer_evidence_manifest_seed must run after inventory and before final prepare")
        if positions["final_customer_grade_audit"] != len(names) - 1:
            failures.append("final_customer_grade_audit must be the final step")
        seed_required_generated_positions = [
            index
            for index, name in enumerate(names)
            if name.startswith("p5_g16_real_corpus_replay_")
            or name.startswith("p5_g22_actual_gui_soak_")
            or name.startswith("p5_g27_selected_zone_crop_")
        ]
        if seed_required_generated_positions and seed_position is None:
            failures.append(
                "prepare_customer_evidence_manifest_seed is required before generated evidence steps "
                "that need the customer manifest"
            )
        for index, name in enumerate(names):
            if name.startswith("p5_g28_cache_plateau_soak_"):
                if not (
                    positions["inventory"]
                    < index
                    < positions["prepare_customer_evidence_manifest"]
                ):
                    failures.append(f"{name} must run after inventory and before final prepare")
                continue
            if (
                name.startswith("p5_g16_real_corpus_replay_")
                or name.startswith("p5_g22_actual_gui_soak_")
                or name.startswith("p5_g27_selected_zone_crop_")
            ) and not (
                (seed_position if seed_position is not None else positions["inventory"])
                < index
                < positions["prepare_customer_evidence_manifest"]
            ):
                failures.append(f"{name} must run after seed manifest and before final prepare")
        p5_g16_positions = [
            index
            for index, name in enumerate(names)
            if name.startswith("p5_g16_real_corpus_replay_")
        ]
        if p5_g16_positions:
            first_p5_g16 = min(p5_g16_positions)
            for index, name in enumerate(names):
                if name.startswith("p5_g27_selected_zone_crop_") and index <= first_p5_g16:
                    failures.append(f"{name} must run after P5-G16 replay generation")
    return ReadinessCheck(
        "required_step_order",
        not failures,
        "inventory, optional seed manifest, P5-G16/P5-G27/P5-G22/P5-G28 evidence, final prepare, and final audit are ordered"
        if not failures
        else "; ".join(failures),
        names,
    )


def _check_plan_invariants(plan: dict[str, Any]) -> ReadinessCheck:
    invariants = plan.get("invariants")
    required_true = [
        "proof_dirs_excluded_from_final_audit_results_dir",
        "final_audit_results_dirs_equal_standard_result_dirs",
    ]
    failures: list[str] = []
    if not isinstance(invariants, dict):
        return ReadinessCheck("plan_invariants", False, "plan.invariants missing")
    for key in required_true:
        if invariants.get(key) is not True:
            failures.append(f"plan.invariants.{key} must be true")
    if invariants.get("final_audit_p5_g16_benchmark_jsons_equal_plan") is not True:
        failures.append("plan.invariants.final_audit_p5_g16_benchmark_jsons_equal_plan must be true")
    if invariants.get("final_audit_p5_g22_gui_soak_jsons_equal_plan") is not True:
        failures.append("plan.invariants.final_audit_p5_g22_gui_soak_jsons_equal_plan must be true")
    if invariants.get("final_audit_p5_g27_selected_zone_crop_jsons_equal_plan") is not True:
        failures.append(
            "plan.invariants.final_audit_p5_g27_selected_zone_crop_jsons_equal_plan must be true"
        )
    if invariants.get("final_audit_p5_g28_cache_plateau_jsons_equal_plan") is not True:
        failures.append(
            "plan.invariants.final_audit_p5_g28_cache_plateau_jsons_equal_plan must be true"
        )
    if invariants.get("final_audit_p5_g28_cache_plateau_require_matches_plan") is not True:
        failures.append(
            "plan.invariants.final_audit_p5_g28_cache_plateau_require_matches_plan must be true"
        )
    if _as_int(invariants.get("final_audit_results_dir_count")) < 1:
        failures.append("plan.invariants.final_audit_results_dir_count must be >= 1")
    return ReadinessCheck(
        "plan_invariants",
        not failures,
        "required plan invariants are true" if not failures else "; ".join(failures),
        [json.dumps(invariants, ensure_ascii=True, sort_keys=True)],
    )


def _check_final_audit_results(plan: dict[str, Any], steps: list[Any]) -> ReadinessCheck:
    standard_dirs = _path_key_list(plan.get("standard_result_dirs"))
    proof_dirs = _path_key_list(plan.get("p5_g7_tile_eviction_proof_dirs"))
    p5_g28_lifecycle_dirs = _path_key_list(plan.get("p5_g28_cache_plateau_lifecycle_dirs"))
    final_step = _step_by_name(steps, "final_customer_grade_audit")
    final_results = _context_values(final_step, "results_dir")
    failures: list[str] = []
    if not standard_dirs:
        failures.append("plan.standard_result_dirs must not be empty")
    if set(standard_dirs) & set(proof_dirs):
        failures.append("standard_result_dirs and proof dirs overlap")
    if [_path_key(Path(value)) for value in final_results] != standard_dirs:
        failures.append("final audit --results-dir values must exactly equal standard_result_dirs")
    proof_in_final = sorted(set(_path_key(Path(value)) for value in final_results) & set(proof_dirs))
    if proof_in_final:
        failures.append(f"proof dirs appear in final audit --results-dir: {proof_in_final}")
    p5_g28_in_final = sorted(
        set(_path_key(Path(value)) for value in final_results) & set(p5_g28_lifecycle_dirs)
    )
    if p5_g28_in_final:
        failures.append(
            f"P5-G28 lifecycle dirs appear in final audit --results-dir: {p5_g28_in_final}"
        )
    return ReadinessCheck(
        "final_audit_results_dir_purity",
        not failures,
        "final audit results-dir list is standard corpus only" if not failures else "; ".join(failures),
        final_results,
    )


def _check_inventory_and_prepare_routing(plan: dict[str, Any], steps: list[Any]) -> ReadinessCheck:
    standard_dirs = [str(Path(path).resolve()) for path in plan.get("standard_result_dirs", []) if isinstance(path, str)]
    proof_dirs = [str(Path(path).resolve()) for path in plan.get("p5_g7_tile_eviction_proof_dirs", []) if isinstance(path, str)]
    inventory = _step_by_name(steps, "inventory")
    seed_prepare = _step_by_name(steps, "prepare_customer_evidence_manifest_seed")
    prepare = _step_by_name(steps, "prepare_customer_evidence_manifest")
    inventory_roots = _context_values(inventory, "root")
    seed_results = _context_values(seed_prepare, "results_dir")
    seed_proofs = _context_values(seed_prepare, "proof_dir")
    prepare_results = _context_values(prepare, "results_dir")
    prepare_proofs = _context_values(prepare, "proof_dir")
    failures: list[str] = []
    for path in standard_dirs:
        if path not in inventory_roots:
            failures.append(f"standard result missing from inventory roots: {path}")
        if seed_prepare and path not in seed_results:
            failures.append(f"standard result missing from seed prepare --results-dir: {path}")
        if path not in prepare_results:
            failures.append(f"standard result missing from prepare --results-dir: {path}")
    for path in proof_dirs:
        if path not in inventory_roots:
            failures.append(f"proof result missing from inventory roots: {path}")
        if seed_prepare and path not in seed_proofs:
            failures.append(f"proof result missing from seed prepare proof channel: {path}")
        if seed_prepare and path in seed_results:
            failures.append(f"proof result appears in seed prepare --results-dir: {path}")
        if path not in prepare_proofs:
            failures.append(f"proof result missing from prepare proof channel: {path}")
        if path in prepare_results:
            failures.append(f"proof result appears in prepare --results-dir: {path}")
    seed_command = _command_values(seed_prepare)
    for flag in (
        "--p5-g16-benchmark-json",
        "--p5-g22-gui-soak-json",
        "--p5-g27-selected-zone-crop-json",
        "--p5-g28-cache-plateau-json",
    ):
        if flag in seed_command:
            failures.append(f"seed prepare command must not include generated evidence flag {flag}")
    return ReadinessCheck(
        "proof_and_corpus_routing",
        not failures,
        "standard corpus and proof outputs are routed to the expected channels"
        if not failures
        else "; ".join(failures),
    )


def _check_p5_g16_replay_routing(plan: dict[str, Any], steps: list[Any]) -> ReadinessCheck:
    planned_jsons = [str(path) for path in plan.get("p5_g16_benchmark_jsons", []) if isinstance(path, str)]
    generated_jsons = [
        str(path)
        for path in plan.get("generated_p5_g16_benchmark_jsons", [])
        if isinstance(path, str)
    ]
    prepare = _step_by_name(steps, "prepare_customer_evidence_manifest")
    final_step = _step_by_name(steps, "final_customer_grade_audit")
    prepare_jsons = _values_after(_command_values(prepare), "--p5-g16-benchmark-json")
    final_jsons = _values_after(_command_values(final_step), "--p5-g16-benchmark-json")
    replay_outputs = [
        value
        for step in steps
        if isinstance(step, dict)
        and str(step.get("name", "")).startswith("p5_g16_real_corpus_replay_")
        for value in _context_values(step, "output_json")
    ]
    failures: list[str] = []
    if planned_jsons and prepare_jsons != planned_jsons:
        failures.append("prepare --p5-g16-benchmark-json values must equal plan.p5_g16_benchmark_jsons")
    if planned_jsons and final_jsons != planned_jsons:
        failures.append("final audit --p5-g16-benchmark-json values must equal plan.p5_g16_benchmark_jsons")
    missing_generated = sorted(set(generated_jsons) - set(replay_outputs))
    if missing_generated:
        failures.append(f"generated P5-G16 outputs missing replay steps: {missing_generated}")
    return ReadinessCheck(
        "p5_g16_replay_routing",
        not failures,
        "P5-G16 replay JSON is routed through prepare, replay, and final audit"
        if not failures
        else "; ".join(failures),
        [*planned_jsons, *replay_outputs],
    )


def _check_p5_g22_gui_soak_routing(plan: dict[str, Any], steps: list[Any]) -> ReadinessCheck:
    planned_jsons = [str(path) for path in plan.get("p5_g22_gui_soak_jsons", []) if isinstance(path, str)]
    generated_jsons = [
        str(path)
        for path in plan.get("generated_p5_g22_gui_soak_jsons", [])
        if isinstance(path, str)
    ]
    prepare = _step_by_name(steps, "prepare_customer_evidence_manifest")
    final_step = _step_by_name(steps, "final_customer_grade_audit")
    prepare_jsons = _values_after(_command_values(prepare), "--p5-g22-gui-soak-json")
    final_jsons = _values_after(_command_values(final_step), "--p5-g22-gui-soak-json")
    soak_outputs = [
        value
        for step in steps
        if isinstance(step, dict)
        and str(step.get("name", "")).startswith("p5_g22_actual_gui_soak_")
        for value in _context_values(step, "output_json")
    ]
    failures: list[str] = []
    if planned_jsons and prepare_jsons != planned_jsons:
        failures.append("prepare --p5-g22-gui-soak-json values must equal plan.p5_g22_gui_soak_jsons")
    if planned_jsons and final_jsons != planned_jsons:
        failures.append("final audit --p5-g22-gui-soak-json values must equal plan.p5_g22_gui_soak_jsons")
    missing_generated = sorted(set(generated_jsons) - set(soak_outputs))
    if missing_generated:
        failures.append(f"generated P5-G22 outputs missing GUI soak steps: {missing_generated}")
    return ReadinessCheck(
        "p5_g22_actual_gui_soak_routing",
        not failures,
        "P5-G22 GUI soak JSON is routed through prepare, soak, and final audit"
        if not failures
        else "; ".join(failures),
        [*planned_jsons, *soak_outputs],
    )


def _check_p5_g27_selected_zone_crop_routing(plan: dict[str, Any], steps: list[Any]) -> ReadinessCheck:
    planned_jsons = [
        str(path)
        for path in plan.get("p5_g27_selected_zone_crop_jsons", [])
        if isinstance(path, str)
    ]
    generated_jsons = [
        str(path)
        for path in plan.get("generated_p5_g27_selected_zone_crop_jsons", [])
        if isinstance(path, str)
    ]
    generated_bridges = {
        str(item.get("output_json") or ""): str(item.get("bridge_json") or "")
        for item in plan.get("generated_p5_g27_selected_zone_crop_bridges", [])
        if isinstance(item, dict)
    }
    planned_p5_g16_jsons = {
        str(path)
        for path in plan.get("p5_g16_benchmark_jsons", [])
        if isinstance(path, str)
    }
    prepare = _step_by_name(steps, "prepare_customer_evidence_manifest")
    final_step = _step_by_name(steps, "final_customer_grade_audit")
    prepare_jsons = _values_after(
        _command_values(prepare),
        "--p5-g27-selected-zone-crop-json",
    )
    final_jsons = _values_after(
        _command_values(final_step),
        "--p5-g27-selected-zone-crop-json",
    )
    generation_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("name", "")).startswith("p5_g27_selected_zone_crop_")
    ]
    generated_step_outputs = [
        value
        for step in generation_steps
        for value in _values_after(_command_values(step), "--output")
    ]
    failures: list[str] = []
    if planned_jsons and prepare_jsons != planned_jsons:
        failures.append(
            "prepare --p5-g27-selected-zone-crop-json values must equal "
            "plan.p5_g27_selected_zone_crop_jsons"
        )
    if planned_jsons and final_jsons != planned_jsons:
        failures.append(
            "final audit --p5-g27-selected-zone-crop-json values must equal "
            "plan.p5_g27_selected_zone_crop_jsons"
        )
    if generated_jsons and generated_step_outputs != generated_jsons:
        failures.append(
            "P5-G27 generation step --output values must equal "
            "plan.generated_p5_g27_selected_zone_crop_jsons"
        )
    for step in generation_steps:
        command = _command_values(step)
        output_values = _values_after(command, "--output")
        bridge_values = _values_after(command, "--p5-g27-real-renderer-bridge-json")
        if "--include-p5-g27-selected-zone-crop-first" not in command:
            failures.append(f"{step.get('name')} must include --include-p5-g27-selected-zone-crop-first")
        if "--p5-g27-require-real-renderer-bridge" not in command:
            failures.append(f"{step.get('name')} must include --p5-g27-require-real-renderer-bridge")
        if not bridge_values:
            failures.append(f"{step.get('name')} must include --p5-g27-real-renderer-bridge-json")
        for output in output_values:
            bridge = generated_bridges.get(str(output), "")
            if bridge and bridge not in bridge_values:
                failures.append(f"{step.get('name')} bridge JSON must match plan for {output}")
            if bridge and planned_p5_g16_jsons and bridge not in planned_p5_g16_jsons:
                failures.append(f"{step.get('name')} bridge JSON must be listed in plan.p5_g16_benchmark_jsons")
    return ReadinessCheck(
        "p5_g27_selected_zone_crop_routing",
        not failures,
        "P5-G27 selected-zone crop-first JSON is generated with a P5-G16 bridge and routed through prepare/final audit"
        if not failures
        else "; ".join(failures),
        [*planned_jsons, *generated_step_outputs],
    )


def _check_p5_g28_cache_plateau_routing(plan: dict[str, Any], steps: list[Any]) -> ReadinessCheck:
    planned_jsons = [
        str(path)
        for path in plan.get("p5_g28_cache_plateau_jsons", [])
        if isinstance(path, str)
    ]
    generated_jsons = [
        str(path)
        for path in plan.get("generated_p5_g28_cache_plateau_jsons", [])
        if isinstance(path, str)
    ]
    lifecycle_dirs = [
        str(path)
        for path in plan.get("p5_g28_cache_plateau_lifecycle_dirs", [])
        if isinstance(path, str)
    ]
    validation_summaries = [
        str(path)
        for path in plan.get("p5_g28_validation_summaries", [])
        if isinstance(path, str)
    ]
    expected_min_sources = str(plan.get("p5_g28_live_counter_min_sources", "")).strip()
    expected_tail_slope_target = str(
        plan.get("p5_g28_live_counter_tail_slope_target_bytes", "")
    ).strip()
    expected_min_source_count = _as_int(plan.get("p5_g28_live_counter_min_sources"))
    prepare = _step_by_name(steps, "prepare_customer_evidence_manifest")
    final_step = _step_by_name(steps, "final_customer_grade_audit")
    prepare_jsons = _values_after(
        _command_values(prepare),
        "--p5-g28-cache-plateau-json",
    )
    final_jsons = _values_after(
        _command_values(final_step),
        "--p5-g28-cache-plateau-json",
    )
    final_command = _command_values(final_step)
    require_present = "--require-p5-g28-cache-plateau-soak" in final_command
    failures: list[str] = []
    if prepare_jsons != planned_jsons:
        failures.append(
            "prepare --p5-g28-cache-plateau-json values must equal "
            "plan.p5_g28_cache_plateau_jsons"
        )
    if final_jsons != planned_jsons:
        failures.append(
            "final audit --p5-g28-cache-plateau-json values must equal "
            "plan.p5_g28_cache_plateau_jsons"
        )
    if planned_jsons and not require_present:
        failures.append(
            "final audit command must include --require-p5-g28-cache-plateau-soak "
            "when plan.p5_g28_cache_plateau_jsons is non-empty"
        )
    if not planned_jsons and require_present:
        failures.append(
            "final audit command must not include --require-p5-g28-cache-plateau-soak "
            "without planned P5-G28 cache plateau JSONs"
        )
    missing_generated_from_plan = [
        path for path in generated_jsons if path not in planned_jsons
    ]
    if missing_generated_from_plan:
        failures.append(
            "plan.generated_p5_g28_cache_plateau_jsons must be included in "
            "plan.p5_g28_cache_plateau_jsons"
        )
    if generated_jsons and expected_min_source_count > len(validation_summaries):
        failures.append(
            "generated P5-G28 cache plateau evidence must have at least "
            "plan.p5_g28_live_counter_min_sources validation summaries"
        )
    generated_steps = [
        step
        for step in steps
        if str((step or {}).get("name", "")).startswith("p5_g28_cache_plateau_soak_")
    ]
    lifecycle_steps = [
        step
        for step in steps
        if str((step or {}).get("name", "")).startswith("p5_g28_cache_plateau_validation_")
    ]
    lifecycle_outputs = [
        value
        for step in lifecycle_steps
        for value in _values_after(_command_values(step), "--out")
    ]
    expected_lifecycle_summaries = [
        str(Path(path) / "validation_summary.json") for path in lifecycle_dirs
    ]
    if sorted(lifecycle_outputs) != sorted(lifecycle_dirs):
        failures.append(
            "generated P5-G28 lifecycle validation step outputs must equal "
            "plan.p5_g28_cache_plateau_lifecycle_dirs"
        )
    if lifecycle_dirs and validation_summaries != expected_lifecycle_summaries:
        failures.append(
            "plan.p5_g28_validation_summaries must be derived from "
            "plan.p5_g28_cache_plateau_lifecycle_dirs"
        )
    if len(generated_steps) != len(generated_jsons):
        failures.append(
            "generated P5-G28 cache plateau step count must equal "
            "plan.generated_p5_g28_cache_plateau_jsons"
        )
    for index, expected_json in enumerate(generated_jsons):
        step = generated_steps[index] if index < len(generated_steps) else None
        command = _command_values(step)
        if "--include-p5-g28-cache-plateau" not in command:
            failures.append("generated P5-G28 command must include --include-p5-g28-cache-plateau")
        if _values_after(command, "--output") != [expected_json]:
            failures.append("generated P5-G28 command --output must match the generated JSON")
        if _values_after(command, "--p5-g28-validation-summary") != validation_summaries:
            failures.append(
                "generated P5-G28 command validation summaries must equal "
                "plan.p5_g28_validation_summaries"
            )
        if _values_after(command, "--p5-g28-live-counter-min-sources") != [
            expected_min_sources
        ]:
            failures.append(
                "generated P5-G28 command min-source target must equal "
                "plan.p5_g28_live_counter_min_sources"
            )
        if _values_after(command, "--p5-g28-live-counter-tail-slope-target-bytes") != [
            expected_tail_slope_target
        ]:
            failures.append(
                "generated P5-G28 command tail-slope target must equal "
                "plan.p5_g28_live_counter_tail_slope_target_bytes"
            )
    return ReadinessCheck(
        "p5_g28_cache_plateau_routing",
        not failures,
        "P5-G28 cache plateau JSON is routed through prepare/final audit when planned"
        if not failures
        else "; ".join(failures),
        planned_jsons or [*prepare_jsons, *final_jsons, *generated_jsons],
    )


def _check_tile_cache_env_isolation(
    readiness: dict[str, Any],
    plan: dict[str, Any],
    steps: list[Any],
) -> ReadinessCheck:
    routing = readiness.get("routing_expectations") if isinstance(readiness.get("routing_expectations"), dict) else {}
    expected_cap = str(routing.get("p5_g6_tile_cache_mb", "")).strip()
    failures: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name", ""))
        env = step.get("env_overrides") if isinstance(step.get("env_overrides"), dict) else {}
        tile_value = str(env.get(TILE_CACHE_MB_ENV_VAR, "")).strip()
        if name.startswith("p5_g7_tile_eviction_proof_"):
            if expected_cap and tile_value != expected_cap:
                failures.append(
                    f"{name} must set {TILE_CACHE_MB_ENV_VAR}={expected_cap}, got {tile_value!r}"
                )
        elif tile_value:
            failures.append(f"{name} must not set {TILE_CACHE_MB_ENV_VAR}")
    return ReadinessCheck(
        "tile_cache_env_isolation",
        not failures,
        "tile-cache env override is isolated to forced proof validation steps"
        if not failures
        else "; ".join(failures),
    )


def _check_final_audit_command(plan: dict[str, Any], steps: list[Any]) -> ReadinessCheck:
    final_step = _step_by_name(steps, "final_customer_grade_audit")
    command_values = _command_values(final_step)
    failures: list[str] = []
    if "--evidence-level" not in command_values or "customer_grade" not in _values_after(command_values, "--evidence-level"):
        failures.append("final audit command must use --evidence-level customer_grade")
    if "--require-p5-g3-realset-gate" not in command_values:
        failures.append("final audit command must include --require-p5-g3-realset-gate")
    if "--require-large-dwg-probe" not in command_values:
        failures.append("final audit command must include --require-large-dwg-probe")
    if "--customer-evidence-manifest" not in command_values:
        failures.append("final audit command must include --customer-evidence-manifest")
    if plan.get("p5_g16_benchmark_jsons") and "--p5-g16-benchmark-json" not in command_values:
        failures.append("final audit command must include --p5-g16-benchmark-json")
    if plan.get("p5_g22_gui_soak_jsons") and "--p5-g22-gui-soak-json" not in command_values:
        failures.append("final audit command must include --p5-g22-gui-soak-json")
    if plan.get("p5_g27_selected_zone_crop_jsons") and "--p5-g27-selected-zone-crop-json" not in command_values:
        failures.append("final audit command must include --p5-g27-selected-zone-crop-json")
    return ReadinessCheck(
        "final_customer_grade_audit_command",
        not failures,
        "final audit command carries customer-grade gates" if not failures else "; ".join(failures),
        command_values,
    )


def _report(
    readiness_path: Path,
    plan_path: Path | None,
    require_ready: bool,
    checks: Sequence[ReadinessCheck],
) -> dict[str, Any]:
    failed = [check for check in checks if not check.passed]
    return {
        "schema_version": 1,
        "status": "passed" if not failed else "failed",
        "readiness_json": str(readiness_path),
        "plan_json": str(plan_path) if plan_path else "",
        "require_ready": bool(require_ready),
        "summary": {
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
        },
        "checks": [check.to_json() for check in checks],
    }


def _resolve_plan_path(readiness: dict[str, Any], plan_json: Path | None) -> Path | None:
    if plan_json is not None:
        return plan_json.resolve()
    outputs = readiness.get("outputs")
    if isinstance(outputs, dict):
        value = str(outputs.get("plan_json", "")).strip()
        if value:
            return Path(value).resolve()
    return None


def _load_json(path: Path | None) -> Any:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _step_by_name(steps: Sequence[Any], name: str) -> dict[str, Any]:
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    return {}


def _context_values(step: dict[str, Any], key: str) -> list[str]:
    context = step.get("command_context")
    if isinstance(context, dict):
        values = context.get(key)
        if isinstance(values, list):
            return [str(value) for value in values]
    command = step.get("command")
    if isinstance(command, list):
        option = {
            "results_dir": "--results-dir",
            "proof_dir": "--p5-g7-tile-eviction-proof-dir",
            "root": "--root",
            "output_json": "--output-json",
            "validation_summary": "--validation-summary",
            "p5_g16_benchmark_json": "--p5-g16-benchmark-json",
            "p5_g22_gui_soak_json": "--p5-g22-gui-soak-json",
            "p5_g27_selected_zone_crop_json": "--p5-g27-selected-zone-crop-json",
            "p5_g28_cache_plateau_json": "--p5-g28-cache-plateau-json",
        }.get(key)
        if option:
            return _values_after([str(value) for value in command], option)
    return []


def _command_values(step: dict[str, Any]) -> list[str]:
    command = step.get("command") if isinstance(step, dict) else None
    return [str(value) for value in command] if isinstance(command, list) else []


def _values_after(command: Sequence[str], option: str) -> list[str]:
    return [
        str(command[index + 1])
        for index, value in enumerate(command[:-1])
        if value == option
    ]


def _path_key_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_path_key(Path(str(value))) for value in values]


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return -1


if __name__ == "__main__":
    raise SystemExit(main())
