"""Tests for the customer-evidence closeout runner."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import closeout_drawing_compare_customer_evidence as closeout


def test_closeout_routes_p5_g7_proof_outside_final_audit_corpus(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source_checkout(tmp_path / "source")
    standard_dir = tmp_path / "standard_validation"
    _write_validation_output(standard_dir)
    proof_manifest = tmp_path / "p5_g7_proof_manifest.json"
    proof_manifest.write_text("{}", encoding="utf-8")
    release_manifest = tmp_path / "release_manifest.json"
    release_manifest.write_text("{}", encoding="utf-8")
    large_probe = tmp_path / "large_dwg_probe.json"
    large_probe.write_text("{}", encoding="utf-8")
    truth = tmp_path / "review_ground_truth.csv"
    truth.write_text("drawing_label,category,summary_contains,source_format,detection_source,bbox_status\n", encoding="utf-8")
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

    observed: list[tuple[list[str], str | None]] = []

    def fake_run(command, cwd=None, env=None, check=False):
        observed.append(([str(part) for part in command], (env or {}).get(closeout.TILE_CACHE_MB_ENV_VAR)))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(closeout.subprocess, "run", fake_run)

    code = closeout.main(
        [
            "--source-checkout",
            str(source),
            "--out",
            str(tmp_path / "closeout"),
            "--standard-results-dir",
            str(standard_dir),
            "--p5-g7-proof-validation-manifest",
            str(proof_manifest),
            "--require-p5-g7-tile-eviction-proof",
            "--p5-g6-tile-cache-mb",
            "0.25",
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
    )

    assert code == 0
    commands = [entry[0] for entry in observed]
    proof_output = (tmp_path / "closeout" / "p5_g7_tile_eviction_proof_1").resolve()
    proof_validation = next(command for command in commands if command[1].endswith("validate_drawing_compare_realset.py"))
    assert proof_validation[1] == str(source / "scripts" / "validate_drawing_compare_realset.py")
    assert "--p5-g3-require-tile-eviction" in proof_validation
    assert "--p5-g6-tile-cache-mb" in proof_validation
    assert observed[0][1] == "0.25"

    prepare_command = next(command for command in commands if command[1].endswith("prepare_drawing_compare_customer_evidence.py"))
    assert prepare_command[1] == str(source / "scripts" / "prepare_drawing_compare_customer_evidence.py")
    prepare_results = _values_after(prepare_command, "--results-dir")
    assert str(standard_dir.resolve()) in prepare_results
    assert str(proof_output) not in prepare_results
    assert str(proof_output) in _values_after(prepare_command, "--p5-g7-tile-eviction-proof-dir")

    audit_command = next(command for command in commands if command[1].endswith("audit_drawing_compare_mvp_exit.py"))
    audit_results = _values_after(audit_command, "--results-dir")
    assert audit_results == [str(standard_dir.resolve())]
    assert str(proof_output) not in audit_results


def test_closeout_dry_run_writes_plan_without_running_subprocesses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source_checkout(tmp_path / "source")
    standard_dir = tmp_path / "standard_validation"
    proof_dir = tmp_path / "proof_validation"
    _write_validation_output(standard_dir)
    _write_validation_output(proof_dir, forced_tile_eviction=True)
    common = _write_common_inputs(tmp_path)
    plan_json = tmp_path / "closeout" / "plan.json"

    def fail_run(*args, **kwargs):
        raise AssertionError("dry-run must not execute subprocesses")

    monkeypatch.setattr(closeout.subprocess, "run", fail_run)

    code = closeout.main(
        [
            "--dry-run",
            "--plan-json",
            str(plan_json),
            "--source-checkout",
            str(source),
            "--out",
            str(tmp_path / "closeout"),
            "--standard-results-dir",
            str(standard_dir),
            "--p5-g7-tile-eviction-proof-dir",
            str(proof_dir),
            "--require-p5-g7-tile-eviction-proof",
            "--p5-g6-tile-cache-mb",
            "0.25",
            *common,
        ]
    )

    assert code == 0
    plan = closeout.json.loads(plan_json.read_text(encoding="utf-8"))
    step_names = [step["name"] for step in plan["steps"]]
    assert step_names[-4:] == [
        "prepare_customer_evidence_manifest",
        "p5_g16_real_corpus_replay_1",
        "p5_g22_actual_gui_soak_1",
        "final_customer_grade_audit",
    ]
    readiness = closeout.json.loads(
        (tmp_path / "closeout" / "closeout_readiness.json").read_text(encoding="utf-8")
    )
    assert readiness["status"] == "ready_for_closeout"
    assert readiness["preflight"] == {
        "status": "passed",
        "issue_count": 0,
        "issues": [],
    }
    assert readiness["plan"]["available"] is True
    assert readiness["plan"]["invariants"]["proof_dirs_excluded_from_final_audit_results_dir"] is True
    assert readiness["routing_expectations"]["require_p5_g7_tile_eviction_proof"] is True
    assert readiness["routing_expectations"]["p5_g6_tile_cache_mb"] == "0.25"
    assert readiness["routing_expectations"]["standard_result_count"] == 1
    assert readiness["routing_expectations"]["proof_result_count"] == 1
    assert readiness["routing_expectations"]["p5_g16_real_corpus_replay_generation_enabled"] is True
    assert readiness["routing_expectations"]["p5_g22_actual_gui_soak_generation_enabled"] is True
    readiness_steps = {
        step["name"]: step["command_context"]
        for step in readiness["plan"]["steps"]
    }
    assert readiness_steps["prepare_customer_evidence_manifest"]["proof_dir"] == [
        str(proof_dir.resolve())
    ]
    assert readiness_steps["final_customer_grade_audit"]["results_dir"] == [
        str(standard_dir.resolve())
    ]
    assert str(standard_dir.resolve()) in readiness_steps["inventory"]["root"]
    assert str(proof_dir.resolve()) in readiness_steps["inventory"]["root"]
    assert plan["invariants"]["final_audit_results_dir_count"] == 1
    assert plan["invariants"]["proof_dirs_excluded_from_final_audit_results_dir"] is True
    assert plan["invariants"]["final_audit_results_dirs_equal_standard_result_dirs"] is True
    assert plan["invariants"]["final_audit_p5_g16_benchmark_jsons_equal_plan"] is True
    assert plan["invariants"]["final_audit_p5_g22_gui_soak_jsons_equal_plan"] is True
    prepare_command = next(
        step["command"]
        for step in plan["steps"]
        if step["name"] == "prepare_customer_evidence_manifest"
    )
    assert str(proof_dir.resolve()) in _values_after(
        prepare_command,
        "--p5-g7-tile-eviction-proof-dir",
    )
    audit_command = next(
        step["command"]
        for step in plan["steps"]
        if step["name"] == "final_customer_grade_audit"
    )
    assert _values_after(audit_command, "--results-dir") == [str(standard_dir.resolve())]
    p5_g16_json = str(standard_dir.resolve() / "p5_g16_real_corpus_replay.json")
    assert _values_after(prepare_command, "--p5-g16-benchmark-json") == [p5_g16_json]
    assert _values_after(audit_command, "--p5-g16-benchmark-json") == [p5_g16_json]
    p5_g22_json = str(standard_dir.resolve() / "p5_g22_actual_gui_soak.json")
    assert _values_after(prepare_command, "--p5-g22-gui-soak-json") == [p5_g22_json]
    assert _values_after(audit_command, "--p5-g22-gui-soak-json") == [p5_g22_json]


def test_closeout_dry_run_plans_manifest_validation_steps_without_running(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source_checkout(tmp_path / "source")
    standard_manifest = tmp_path / "standard_manifest.json"
    proof_manifest = tmp_path / "proof_manifest.json"
    standard_manifest.write_text("{}", encoding="utf-8")
    proof_manifest.write_text("{}", encoding="utf-8")
    plan_json = tmp_path / "plan.json"
    common = _write_common_inputs(tmp_path)

    monkeypatch.setattr(
        closeout.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dry-run must not run")),
    )

    code = closeout.main(
        [
            "--dry-run",
            "--plan-json",
            str(plan_json),
            "--source-checkout",
            str(source),
            "--out",
            str(tmp_path / "closeout"),
            "--standard-validation-manifest",
            str(standard_manifest),
            "--p5-g7-proof-validation-manifest",
            str(proof_manifest),
            "--require-p5-g7-tile-eviction-proof",
            "--p5-g6-tile-cache-mb",
            "0.25",
            *common,
        ]
    )

    assert code == 0
    plan = closeout.json.loads(plan_json.read_text(encoding="utf-8"))
    assert [step["name"] for step in plan["steps"][:2]] == [
        "standard_validation_1",
        "p5_g7_tile_eviction_proof_1",
    ]
    assert plan["invariants"]["proof_dirs_excluded_from_final_audit_results_dir"] is True


def test_closeout_applies_tile_cache_env_only_to_p5_g7_proof_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source_checkout(tmp_path / "source")
    standard_manifest = tmp_path / "standard_manifest.json"
    proof_manifest = tmp_path / "proof_manifest.json"
    standard_manifest.write_text("{}", encoding="utf-8")
    proof_manifest.write_text("{}", encoding="utf-8")
    common = _write_common_inputs(tmp_path)
    observed: list[tuple[str, str | None]] = []

    def fake_run(command, cwd=None, env=None, check=False):
        observed.append((str(command[3]) if "--manifest" in command else str(command[1]), (env or {}).get(closeout.TILE_CACHE_MB_ENV_VAR)))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(closeout.subprocess, "run", fake_run)

    code = closeout.main(
        [
            "--source-checkout",
            str(source),
            "--out",
            str(tmp_path / "closeout"),
            "--standard-validation-manifest",
            str(standard_manifest),
            "--p5-g7-proof-validation-manifest",
            str(proof_manifest),
            "--require-p5-g7-tile-eviction-proof",
            "--p5-g6-tile-cache-mb",
            "0.25",
            *common,
        ]
    )

    assert code == 0
    assert observed[0] == (str(standard_manifest), None)
    assert observed[1] == (str(proof_manifest), "0.25")
    assert all(value is None for _, value in observed[2:])


def test_closeout_writes_failure_report_when_subprocess_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source_checkout(tmp_path / "source")
    standard_dir = tmp_path / "standard_validation"
    _write_validation_output(standard_dir)
    proof_manifest = tmp_path / "proof_manifest.json"
    proof_manifest.write_text("{}", encoding="utf-8")
    common = _write_common_inputs(tmp_path)
    failure_json = tmp_path / "closeout" / "failure.json"

    def fake_run(command, cwd=None, env=None, check=False):
        return SimpleNamespace(
            returncode=9,
            stdout="proof stdout tail",
            stderr=b"proof stderr tail",
        )

    monkeypatch.setattr(closeout.subprocess, "run", fake_run)

    _assert_exits(
        [
            "--source-checkout",
            str(source),
            "--out",
            str(tmp_path / "closeout"),
            "--standard-results-dir",
            str(standard_dir),
            "--p5-g7-proof-validation-manifest",
            str(proof_manifest),
            "--require-p5-g7-tile-eviction-proof",
            "--p5-g6-tile-cache-mb",
            "0.25",
            "--failure-json",
            str(failure_json),
            *common,
        ],
        code=9,
    )

    report = closeout.json.loads(failure_json.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failure_kind"] == "subprocess_nonzero_exit"
    assert report["failed_step_name"] == "p5_g7_tile_eviction_proof_1"
    assert report["failed_returncode"] == 9
    assert report["completed_steps"] == []
    assert report["failed_step"]["index"] == 1
    assert report["failed_step"]["name"] == "p5_g7_tile_eviction_proof_1"
    assert report["failed_step"]["returncode"] == 9
    assert report["failed_step"]["failure_kind"] == "subprocess_nonzero_exit"
    assert report["failed_step"]["stdout_tail"] == "proof stdout tail"
    assert report["failed_step"]["stderr_tail"] == "proof stderr tail"
    assert report["failed_step"]["env_overrides"] == {
        closeout.TILE_CACHE_MB_ENV_VAR: "0.25",
    }
    context = report["failed_step"]["command_context"]
    assert context["manifest"] == [str(proof_manifest)]
    assert context["out"] == [str((tmp_path / "closeout" / "p5_g7_tile_eviction_proof_1").resolve())]
    assert report["remaining_steps"] == [
        "inventory",
        "prepare_customer_evidence_manifest",
        "p5_g16_real_corpus_replay_1",
        "p5_g22_actual_gui_soak_1",
        "final_customer_grade_audit",
    ]
    assert "tile eviction" in " ".join(report["triage_hints"])


def test_closeout_writes_spawn_error_failure_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source_checkout(tmp_path / "source")
    standard_dir = tmp_path / "standard_validation"
    _write_validation_output(standard_dir)
    common = _write_common_inputs(tmp_path)
    failure_json = tmp_path / "closeout" / "spawn_failure.json"

    def fail_spawn(command, cwd=None, env=None, check=False):
        raise OSError("missing python")

    monkeypatch.setattr(closeout.subprocess, "run", fail_spawn)

    _assert_exits(
        [
            "--source-checkout",
            str(source),
            "--out",
            str(tmp_path / "closeout"),
            "--standard-results-dir",
            str(standard_dir),
            "--failure-json",
            str(failure_json),
            *common,
        ],
        code=1,
    )

    report = closeout.json.loads(failure_json.read_text(encoding="utf-8"))
    assert report["failure_kind"] == "spawn_error"
    assert report["failed_step_name"] == "inventory"
    assert report["failed_step"]["exception_type"] == "OSError"
    assert report["failed_step"]["exception_message"] == "missing python"


def test_closeout_rejects_duplicate_standard_results_dirs_before_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source_checkout(tmp_path / "source")
    standard_dir = tmp_path / "standard_validation"
    _write_validation_output(standard_dir)
    common = _write_common_inputs(tmp_path)
    readiness_json = tmp_path / "closeout" / "duplicate_readiness.json"
    monkeypatch.setattr(
        closeout.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("preflight must not run")),
    )

    _assert_exits(
        [
            "--dry-run",
            "--source-checkout",
            str(source),
            "--out",
            str(tmp_path / "closeout"),
            "--readiness-json",
            str(readiness_json),
            "--standard-results-dir",
            str(standard_dir),
            "--standard-results-dir",
            str(standard_dir),
            *common,
        ],
        code=2,
    )
    readiness = closeout.json.loads(readiness_json.read_text(encoding="utf-8"))
    assert readiness["status"] == "preflight_failed"
    assert readiness["preflight"]["issue_count"] == len(readiness["preflight"]["issues"])
    assert any("duplicated" in issue for issue in readiness["preflight"]["issues"])


def test_closeout_rejects_standard_results_dir_colliding_with_generated_output(
    tmp_path: Path,
) -> None:
    source = _write_source_checkout(tmp_path / "source")
    closeout_dir = tmp_path / "closeout"
    colliding_dir = closeout_dir / "standard_validation_1"
    _write_validation_output(colliding_dir)
    standard_manifest = tmp_path / "standard_manifest.json"
    standard_manifest.write_text("{}", encoding="utf-8")
    common = _write_common_inputs(tmp_path)

    _assert_exits(
        [
            "--dry-run",
            "--source-checkout",
            str(source),
            "--out",
            str(closeout_dir),
            "--standard-results-dir",
            str(colliding_dir),
            "--standard-validation-manifest",
            str(standard_manifest),
            *common,
        ],
        code=2,
    )


def test_closeout_rejects_forced_proof_passed_as_standard_results_dir(
    tmp_path: Path,
) -> None:
    source = _write_source_checkout(tmp_path / "source")
    forced_proof_dir = tmp_path / "forced_proof"
    _write_validation_output(forced_proof_dir, forced_tile_eviction=True)
    common = _write_common_inputs(tmp_path)

    _assert_exits(
        [
            "--dry-run",
            "--source-checkout",
            str(source),
            "--out",
            str(tmp_path / "closeout"),
            "--standard-results-dir",
            str(forced_proof_dir),
            *common,
        ],
        code=2,
    )


def test_closeout_rejects_non_forced_dir_as_required_p5_g7_proof(
    tmp_path: Path,
) -> None:
    source = _write_source_checkout(tmp_path / "source")
    standard_dir = tmp_path / "standard_validation"
    proof_dir = tmp_path / "proof_validation"
    _write_validation_output(standard_dir)
    _write_validation_output(proof_dir)
    common = _write_common_inputs(tmp_path)

    _assert_exits(
        [
            "--dry-run",
            "--source-checkout",
            str(source),
            "--out",
            str(tmp_path / "closeout"),
            "--standard-results-dir",
            str(standard_dir),
            "--p5-g7-tile-eviction-proof-dir",
            str(proof_dir),
            "--require-p5-g7-tile-eviction-proof",
            "--p5-g6-tile-cache-mb",
            "0.25",
            *common,
        ],
        code=2,
    )


def test_closeout_preflight_rejects_missing_source_checkout_provenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    standard_dir = tmp_path / "standard_validation"
    _write_validation_output(standard_dir)
    common = _write_common_inputs(tmp_path)
    readiness_json = tmp_path / "closeout" / "readiness.json"

    try:
        closeout.main(
            [
                "--dry-run",
                "--readiness-json",
                str(readiness_json),
                "--source-checkout",
                str(source),
                "--out",
                str(tmp_path / "closeout"),
                "--standard-results-dir",
                str(standard_dir),
                *common,
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("missing manifest_provenance.py must fail preflight")
    readiness = closeout.json.loads(readiness_json.read_text(encoding="utf-8"))
    assert readiness["status"] == "preflight_failed"
    assert readiness["preflight"]["status"] == "failed"
    assert readiness["preflight"]["issue_count"] >= 1
    assert any(
        "manifest_provenance.py" in issue
        for issue in readiness["preflight"]["issues"]
    )
    assert readiness["plan"] == {
        "available": False,
        "step_count": 0,
        "steps": [],
        "invariants": {},
    }
    assert readiness["outputs"]["readiness_json"] == str(readiness_json)


def _values_after(command: list[str], option: str) -> list[str]:
    return [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == option
    ]


def _assert_exits(argv: list[str], *, code: int) -> None:
    try:
        closeout.main(argv)
    except SystemExit as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"expected SystemExit({code})")


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
    (path / "validation_summary.json").write_text(
        closeout.json.dumps(summary),
        encoding="utf-8",
    )
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
