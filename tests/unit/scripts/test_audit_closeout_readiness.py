"""Tests for the closeout readiness audit gate."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import audit_closeout_readiness as audit
from scripts import closeout_drawing_compare_customer_evidence as closeout


def test_audit_passes_closeout_dry_run_readiness(tmp_path: Path, monkeypatch) -> None:
    readiness_json, plan_json = _write_closeout_readiness_packet(tmp_path, monkeypatch)

    report = audit.run_audit(
        readiness_json=readiness_json,
        plan_json=plan_json,
        require_ready=True,
    )

    assert report["status"] == "passed"
    assert report["summary"]["failed"] == 0
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["final_audit_results_dir_purity"]["passed"] is True
    assert checks["proof_and_corpus_routing"]["passed"] is True
    assert checks["p5_g16_replay_routing"]["passed"] is True
    assert checks["p5_g27_selected_zone_crop_routing"]["passed"] is True
    assert checks["tile_cache_env_isolation"]["passed"] is True


def test_audit_fails_preflight_failed_readiness(tmp_path: Path) -> None:
    readiness_json = tmp_path / "closeout_readiness.json"
    readiness_json.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "preflight_failed",
                "preflight": {
                    "status": "failed",
                    "issue_count": 1,
                    "issues": ["missing validation_summary.json"],
                },
                "outputs": {
                    "plan_json": "",
                    "readiness_json": str(readiness_json),
                    "failure_json": str(tmp_path / "failure.json"),
                    "inventory_json": str(tmp_path / "inventory.json"),
                    "customer_evidence_manifest": str(tmp_path / "customer_manifest.json"),
                    "audit_json": str(tmp_path / "audit.json"),
                },
                "plan": {
                    "available": False,
                    "step_count": 0,
                    "steps": [],
                    "invariants": {},
                },
            }
        ),
        encoding="utf-8",
    )

    report = audit.run_audit(readiness_json=readiness_json, require_ready=True)

    assert report["status"] == "failed"
    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "readiness_status" in failed
    assert "preflight_status" in failed
    assert "plan_json_loadable" in failed


def test_audit_rejects_proof_dir_in_final_audit_results(
    tmp_path: Path,
    monkeypatch,
) -> None:
    readiness_json, plan_json = _write_closeout_readiness_packet(tmp_path, monkeypatch)
    plan = json.loads(plan_json.read_text(encoding="utf-8"))
    proof_dir = plan["p5_g7_tile_eviction_proof_dirs"][0]
    final_step = next(
        step for step in plan["steps"] if step["name"] == "final_customer_grade_audit"
    )
    final_step["command"].extend(["--results-dir", proof_dir])
    plan["invariants"]["proof_dirs_excluded_from_final_audit_results_dir"] = False
    plan_json.write_text(json.dumps(plan), encoding="utf-8")

    report = audit.run_audit(
        readiness_json=readiness_json,
        plan_json=plan_json,
        require_ready=True,
    )

    assert report["status"] == "failed"
    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "final_audit_results_dir_purity" in failed
    assert "proof dirs appear in final audit" in failed["final_audit_results_dir_purity"]["detail"]
    assert "plan_invariants" in failed


def test_audit_rejects_tile_cache_env_on_non_proof_step(
    tmp_path: Path,
    monkeypatch,
) -> None:
    readiness_json, plan_json = _write_closeout_readiness_packet(
        tmp_path,
        monkeypatch,
        generated_proof=True,
    )
    plan = json.loads(plan_json.read_text(encoding="utf-8"))
    inventory = next(step for step in plan["steps"] if step["name"] == "inventory")
    inventory["env_overrides"] = {audit.TILE_CACHE_MB_ENV_VAR: "0.25"}
    plan_json.write_text(json.dumps(plan), encoding="utf-8")

    report = audit.run_audit(
        readiness_json=readiness_json,
        plan_json=plan_json,
        require_ready=True,
    )

    assert report["status"] == "failed"
    check = next(
        check for check in report["checks"] if check["name"] == "tile_cache_env_isolation"
    )
    assert check["passed"] is False
    assert "inventory must not set DRAWING_COMPARE_TILE_CACHE_MB" in check["detail"]


def test_audit_rejects_standard_and_proof_dir_overlap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    readiness_json, plan_json = _write_closeout_readiness_packet(tmp_path, monkeypatch)
    plan = json.loads(plan_json.read_text(encoding="utf-8"))
    plan["p5_g7_tile_eviction_proof_dirs"] = [plan["standard_result_dirs"][0]]
    plan_json.write_text(json.dumps(plan), encoding="utf-8")

    report = audit.run_audit(
        readiness_json=readiness_json,
        plan_json=plan_json,
        require_ready=True,
    )

    assert report["status"] == "failed"
    check = next(
        check for check in report["checks"] if check["name"] == "final_audit_results_dir_purity"
    )
    assert check["passed"] is False
    assert "standard_result_dirs and proof dirs overlap" in check["detail"]


def test_audit_validates_explicit_p5_g27_selected_zone_crop_routing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    readiness_json, plan_json = _write_closeout_readiness_packet(
        tmp_path,
        monkeypatch,
        include_p5_g27=True,
    )
    plan = json.loads(plan_json.read_text(encoding="utf-8"))
    expected = plan["p5_g27_selected_zone_crop_jsons"]

    report = audit.run_audit(
        readiness_json=readiness_json,
        plan_json=plan_json,
        require_ready=True,
    )

    checks = {check["name"]: check for check in report["checks"]}
    assert report["status"] == "passed"
    assert checks["p5_g27_selected_zone_crop_routing"]["passed"] is True
    assert checks["p5_g27_selected_zone_crop_routing"]["evidence"] == expected


def test_audit_rejects_mismatched_p5_g27_selected_zone_crop_routing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    readiness_json, plan_json = _write_closeout_readiness_packet(
        tmp_path,
        monkeypatch,
        include_p5_g27=True,
    )
    plan = json.loads(plan_json.read_text(encoding="utf-8"))
    final_step = next(
        step for step in plan["steps"] if step["name"] == "final_customer_grade_audit"
    )
    flag_index = final_step["command"].index("--p5-g27-selected-zone-crop-json")
    final_step["command"][flag_index + 1] = str(tmp_path / "wrong_p5_g27.json")
    plan["invariants"]["final_audit_p5_g27_selected_zone_crop_jsons_equal_plan"] = False
    plan_json.write_text(json.dumps(plan), encoding="utf-8")

    report = audit.run_audit(
        readiness_json=readiness_json,
        plan_json=plan_json,
        require_ready=True,
    )

    checks = {check["name"]: check for check in report["checks"]}
    assert report["status"] == "failed"
    assert checks["p5_g27_selected_zone_crop_routing"]["passed"] is False
    assert "final audit --p5-g27-selected-zone-crop-json values must equal" in checks[
        "p5_g27_selected_zone_crop_routing"
    ]["detail"]
    assert checks["plan_invariants"]["passed"] is False
    assert "final_audit_p5_g27_selected_zone_crop_jsons_equal_plan" in checks[
        "plan_invariants"
    ]["detail"]


def test_audit_rejects_mismatched_tile_cache_env_on_generated_proof_step(
    tmp_path: Path,
    monkeypatch,
) -> None:
    readiness_json, plan_json = _write_closeout_readiness_packet(
        tmp_path,
        monkeypatch,
        generated_proof=True,
    )
    plan = json.loads(plan_json.read_text(encoding="utf-8"))
    proof_step = next(
        step for step in plan["steps"] if step["name"] == "p5_g7_tile_eviction_proof_1"
    )
    proof_step["env_overrides"] = {audit.TILE_CACHE_MB_ENV_VAR: "0.50"}
    plan_json.write_text(json.dumps(plan), encoding="utf-8")

    report = audit.run_audit(
        readiness_json=readiness_json,
        plan_json=plan_json,
        require_ready=True,
    )

    assert report["status"] == "failed"
    check = next(
        check for check in report["checks"] if check["name"] == "tile_cache_env_isolation"
    )
    assert check["passed"] is False
    assert "must set DRAWING_COMPARE_TILE_CACHE_MB=0.25" in check["detail"]


def test_audit_cli_writes_report_and_returns_nonzero_for_bad_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    readiness_json, plan_json = _write_closeout_readiness_packet(tmp_path, monkeypatch)
    plan = json.loads(plan_json.read_text(encoding="utf-8"))
    plan["steps"] = plan["steps"][:-1]
    plan_json.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "audit.json"

    code = audit.main(
        [
            "--readiness-json",
            str(readiness_json),
            "--plan-json",
            str(plan_json),
            "--require-ready",
            "--out",
            str(out),
        ]
    )

    assert code == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "required_step_order" in failed


def _write_closeout_readiness_packet(
    tmp_path: Path,
    monkeypatch,
    *,
    generated_proof: bool = False,
    include_p5_g27: bool = False,
) -> tuple[Path, Path]:
    source = _write_source_checkout(tmp_path / "source")
    standard_dir = tmp_path / "standard_validation"
    _write_validation_output(standard_dir)
    proof_dir = tmp_path / "proof_validation"
    _write_validation_output(proof_dir, forced_tile_eviction=True)
    common = _write_common_inputs(tmp_path)
    plan_json = tmp_path / "closeout" / "closeout_plan.json"
    readiness_json = tmp_path / "closeout" / "closeout_readiness.json"

    def fail_run(*args, **kwargs):
        raise AssertionError("dry-run must not execute subprocesses")

    monkeypatch.setattr(closeout.subprocess, "run", fail_run)
    argv = [
        "--dry-run",
        "--plan-json",
        str(plan_json),
        "--readiness-json",
        str(readiness_json),
        "--source-checkout",
        str(source),
        "--out",
        str(tmp_path / "closeout"),
        "--standard-results-dir",
        str(standard_dir),
        "--require-p5-g7-tile-eviction-proof",
        "--p5-g6-tile-cache-mb",
        "0.25",
        *common,
    ]
    if generated_proof:
        proof_manifest = tmp_path / "proof_manifest.json"
        proof_manifest.write_text("{}", encoding="utf-8")
        argv.extend(["--p5-g7-proof-validation-manifest", str(proof_manifest)])
    else:
        argv.extend(["--p5-g7-tile-eviction-proof-dir", str(proof_dir)])
    if include_p5_g27:
        p5_g27 = tmp_path / "p5_g27_selected_zone_crop_soak.json"
        p5_g27.write_text("{}", encoding="utf-8")
        argv.extend(["--p5-g27-selected-zone-crop-json", str(p5_g27)])
    assert closeout.main(argv) == 0
    return readiness_json, plan_json


def _write_source_checkout(path: Path) -> Path:
    scripts_dir = path / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in (
        "validate_drawing_compare_realset.py",
        "inventory_drawing_compare_customer_evidence.py",
        "prepare_drawing_compare_customer_evidence.py",
        "audit_drawing_compare_mvp_exit.py",
        "benchmark_real_corpus_replay.py",
        "benchmark_actual_gui_soak.py",
    ):
        (scripts_dir / name).write_text("# placeholder\n", encoding="utf-8")
    provenance = path / "src" / "services" / "comparison" / "manifest_provenance.py"
    provenance.parent.mkdir(parents=True)
    provenance.write_text("# placeholder\n", encoding="utf-8")
    return path


def _write_validation_output(path: Path, *, forced_tile_eviction: bool = False) -> None:
    path.mkdir(parents=True)
    summary = {"comparison": {"completed_pairs": 20}}
    if forced_tile_eviction:
        summary = {
            "p5_g3_realset_gate": {
                "requested": True,
                "evidence": {
                    "tile_manifest": {
                        "require_eviction": True,
                        "evicted_pair_count": 1,
                        "evicted_estimated_bytes": 4096,
                    }
                },
            }
        }
    (path / "validation_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (path / "_SUCCESS").write_text("{}", encoding="utf-8")


def _write_common_inputs(tmp_path: Path) -> list[str]:
    release_manifest = tmp_path / "release_manifest.json"
    release_manifest.write_text("{}", encoding="utf-8")
    large_probe = tmp_path / "large_dwg_probe.json"
    large_probe.write_text("{}", encoding="utf-8")
    truth = tmp_path / "review_ground_truth.csv"
    truth.write_text(
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status\n",
        encoding="utf-8",
    )
    decision = tmp_path / "review_decision_truth.csv"
    decision.write_text(
        "pair_uuid,zone_id,drawing_label,structural_bucket,human_label,source_format,detection_source,bbox_status,notes\n",
        encoding="utf-8",
    )
    strata = tmp_path / "dataset_strata.csv"
    strata.write_text(
        "pair_uuid,drawing_label,format_pair,sheet_type,risk_class,large_dwg,block_text_case,negative_control,notes\n",
        encoding="utf-8",
    )
    notes = tmp_path / "operator_notes.md"
    notes.write_text("reviewer_role: structural_review_lead\n", encoding="utf-8")
    confirmed = tmp_path / "confirmed.png"
    confirmed.write_bytes(b"png")
    return [
        "--customer-evidence-manifest",
        str(tmp_path / "customer_evidence_manifest.json"),
        "--release-manifest",
        str(release_manifest),
        "--large-dwg-probe",
        str(large_probe),
        "--review-ground-truth",
        str(truth),
        "--review-decision-truth",
        str(decision),
        "--dataset-strata",
        str(strata),
        "--operator-notes-file",
        str(notes),
        "--confirmed-export-artifact",
        str(confirmed),
        "--dataset-id",
        "customer-set-001",
        "--dataset-source-description",
        "20-50 sheet customer-grade validation set approved for MVP exit",
        "--dataset-approver",
        "lead",
        "--ground-truth-owner",
        "owner",
    ]
