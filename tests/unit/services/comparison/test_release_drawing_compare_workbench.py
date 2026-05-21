"""Tests for the Drawing Compare Workbench release orchestrator."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import release_drawing_compare_workbench as release


def test_release_realset_command_uses_customer_mvp_validation_profile(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    old_dir.mkdir()
    new_dir.mkdir()

    args = release.parse_args(
        [
            "--a",
            str(old_dir),
            "--b",
            str(new_dir),
            "--out",
            str(out_dir),
            "--skip-build",
            "--skip-tests",
        ]
    )

    command = release._realset_command(args, out_dir / "realset_validation")

    assert "--export-profile" in command
    assert command[command.index("--export-profile") + 1] == "sharable"
    for flag in (
        "--quality-gate",
        "--change-zone-report",
        "--executive-review",
        "--review-dashboard",
        "--export-viewer-package",
        "--viewer-perf-log",
        "--render-selected-zone-evidence",
        "--export-marked-pdf",
    ):
        assert flag in command
    assert command[command.index("--viewer-render-policy") + 1] == "top-issues"
    assert command[command.index("--selected-zone-evidence-per-pair") + 1] == "1"
    assert "--export-cloud-marks" not in command


def test_release_realset_command_exports_only_explicit_cloud_selection(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    selection = tmp_path / "confirmed_zones.csv"
    old_dir.mkdir()
    new_dir.mkdir()
    selection.write_text("pair_id,zone_id\npair_a,C-001\n", encoding="utf-8")

    args = release.parse_args(
        [
            "--a",
            str(old_dir),
            "--b",
            str(new_dir),
            "--out",
            str(out_dir),
            "--cloud-selection-csv",
            str(selection),
        ]
    )

    command = release._realset_command(args, out_dir / "realset_validation")

    assert "--export-cloud-marks" in command
    assert command[command.index("--cloud-export-mode") + 1] == "csv"
    assert command[command.index("--cloud-selection-csv") + 1] == str(selection.resolve())


def test_release_realset_command_passes_review_ground_truth(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    review_truth = tmp_path / "review_truth.csv"
    old_dir.mkdir()
    new_dir.mkdir()
    review_truth.write_text(
        "drawing_label,category,summary_contains\nS2401,mixed,D13@100;D13@200\n",
        encoding="utf-8",
    )

    args = release.parse_args(
        [
            "--a",
            str(old_dir),
            "--b",
            str(new_dir),
            "--out",
            str(out_dir),
            "--review-ground-truth",
            str(review_truth),
        ]
    )

    command = release._realset_command(args, out_dir / "realset_validation")

    assert "--review-ground-truth" in command
    assert command[command.index("--review-ground-truth") + 1] == str(review_truth.resolve())


def test_release_acceptance_command_points_at_realset_outputs(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    old_dir.mkdir()
    new_dir.mkdir()

    args = release.parse_args(["--a", str(old_dir), "--b", str(new_dir), "--out", str(out_dir)])
    command = release._workbench_acceptance_command(
        args,
        out_dir / "realset_validation",
        out_dir / "screenshots",
    )

    assert command[:2] == [args.python, "scripts/workbench_acceptance_smoke.py"]
    assert command[command.index("--results-dir") + 1] == str(out_dir / "realset_validation")
    assert command[command.index("--a") + 1] == str(old_dir.resolve())
    assert command[command.index("--b") + 1] == str(new_dir.resolve())
    assert command[command.index("--screenshots-dir") + 1] == str(out_dir / "screenshots")


def test_release_packaged_launch_command_uses_smoke_exit_flag(tmp_path: Path) -> None:
    exe_path = tmp_path / "dist" / "DrawingCompareWorkbench.exe"

    command = release._packaged_launch_command(exe_path)

    assert command == [str(exe_path), "--smoke-exit-ms", "1000"]


def test_release_mvp_exit_audit_requires_customer_manifest(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    old_dir.mkdir()
    new_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        release.parse_args(
            [
                "--a",
                str(old_dir),
                "--b",
                str(new_dir),
                "--out",
                str(out_dir),
                "--run-mvp-exit-audit",
            ]
        )

    assert exc_info.value.code == 2


def test_release_mvp_exit_audit_requires_existing_customer_manifest(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    missing_manifest = tmp_path / "missing_customer_evidence_manifest.json"
    old_dir.mkdir()
    new_dir.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        release.parse_args(
            [
                "--a",
                str(old_dir),
                "--b",
                str(new_dir),
                "--out",
                str(out_dir),
                "--run-mvp-exit-audit",
                "--customer-evidence-manifest",
                str(missing_manifest),
            ]
        )

    assert exc_info.value.code == 2


def test_release_mvp_exit_audit_requires_validation_result_source(tmp_path: Path) -> None:
    out_dir = tmp_path / "release"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    customer_manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        release.parse_args(
            [
                "--out",
                str(out_dir),
                "--run-mvp-exit-audit",
                "--customer-evidence-manifest",
                str(customer_manifest),
                "--skip-realset",
            ]
        )

    assert exc_info.value.code == 2


def test_release_mvp_exit_audit_requires_existing_extra_result_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "release"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    missing_results = tmp_path / "missing_validation"
    customer_manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        release.parse_args(
            [
                "--out",
                str(out_dir),
                "--run-mvp-exit-audit",
                "--customer-evidence-manifest",
                str(customer_manifest),
                "--exit-audit-results-dir",
                str(missing_results),
            ]
        )

    assert exc_info.value.code == 2


def test_release_mvp_exit_audit_requires_validation_summary_in_extra_result_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "release"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    empty_results = tmp_path / "empty_validation"
    empty_results.mkdir()
    customer_manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        release.parse_args(
            [
                "--out",
                str(out_dir),
                "--run-mvp-exit-audit",
                "--customer-evidence-manifest",
                str(customer_manifest),
                "--exit-audit-results-dir",
                str(empty_results),
            ]
        )

    assert exc_info.value.code == 2


def test_release_mvp_exit_audit_accepts_existing_realset_validation_source(tmp_path: Path) -> None:
    out_dir = tmp_path / "release"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    (out_dir / "realset_validation").mkdir(parents=True)
    (out_dir / "realset_validation" / "validation_summary.json").write_text("{}", encoding="utf-8")
    customer_manifest.write_text("{}", encoding="utf-8")

    args = release.parse_args(
        [
            "--out",
            str(out_dir),
            "--run-mvp-exit-audit",
            "--customer-evidence-manifest",
            str(customer_manifest),
            "--skip-realset",
        ]
    )

    assert args.run_mvp_exit_audit is True


def test_release_mvp_exit_audit_command_uses_customer_manifest_and_extra_dirs(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    extra_dir = tmp_path / "cad_pdf_block"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    release_manifest = out_dir / "release_manifest.json"
    audit_json = out_dir / "mvp_exit_audit.json"
    old_dir.mkdir()
    new_dir.mkdir()
    extra_dir.mkdir()
    (extra_dir / "validation_summary.json").write_text("{}", encoding="utf-8")
    (out_dir / "realset_validation").mkdir(parents=True)
    customer_manifest.write_text("{}", encoding="utf-8")
    large_dwg_probe.write_text("{}", encoding="utf-8")

    args = release.parse_args(
        [
            "--a",
            str(old_dir),
            "--b",
            str(new_dir),
            "--out",
            str(out_dir),
            "--run-mvp-exit-audit",
            "--customer-evidence-manifest",
            str(customer_manifest),
            "--large-dwg-probe",
            str(large_dwg_probe),
            "--require-large-dwg-probe",
            "--exit-audit-results-dir",
            str(extra_dir),
        ]
    )

    command = release._mvp_exit_audit_command(args, out_dir, release_manifest, audit_json)

    assert command[:2] == [args.python, "scripts/audit_drawing_compare_mvp_exit.py"]
    result_dirs = [
        command[index + 1]
        for index, token in enumerate(command)
        if token == "--results-dir"
    ]
    assert str(extra_dir.resolve()) in result_dirs
    assert str((out_dir / "realset_validation").resolve()) in result_dirs
    assert command[command.index("--release-manifest") + 1] == str(release_manifest)
    assert command[command.index("--min-total-pairs") + 1] == "20"
    assert command[command.index("--max-total-pairs") + 1] == "50"
    assert command[command.index("--max-first-review-ready-s") + 1] == "1800"
    assert command[command.index("--max-cold-zone-render-ms") + 1] == "10000"
    assert command[command.index("--max-cache-hit-zone-render-ms") + 1] == "2000"
    assert command[command.index("--out") + 1] == str(audit_json)
    assert command[command.index("--customer-evidence-manifest") + 1] == str(customer_manifest.resolve())
    assert command[command.index("--evidence-level") + 1] == "customer_grade"
    assert command[command.index("--large-dwg-probe") + 1] == str(large_dwg_probe.resolve())
    assert "--require-large-dwg-probe" in command


def test_release_mvp_exit_audit_sees_customer_package_audit_artifact(tmp_path: Path, monkeypatch) -> None:
    out_dir = tmp_path / "release"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    (out_dir / "realset_validation").mkdir(parents=True)
    (out_dir / "realset_validation" / "validation_summary.json").write_text("{}", encoding="utf-8")
    customer_manifest.write_text("{}", encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_run_step(manifest, name, command, **kwargs):
        if name == "mvp_exit_audit":
            release_manifest = Path(command[command.index("--release-manifest") + 1])
            payload = json.loads(release_manifest.read_text(encoding="utf-8"))
            observed["has_package_audit_artifact"] = (
                "customer_shareable_package_path_audit" in payload["artifacts"]
            )
            observed["package_step_before_audit"] = any(
                step["name"] == "customer_shareable_package_path_audit"
                and step["status"] == "passed"
                for step in payload["steps"]
            )
        return 0

    monkeypatch.setattr(release, "_oda_preflight", lambda python: {"status": "skipped"})
    monkeypatch.setattr(release, "_run_step", fake_run_step)

    code = release.main(
        [
            "--out",
            str(out_dir),
            "--run-mvp-exit-audit",
            "--customer-evidence-manifest",
            str(customer_manifest),
            "--skip-tests",
            "--skip-realset",
            "--skip-build",
            "--skip-packaged-launch-smoke",
        ]
    )

    assert code == 0
    assert observed == {
        "has_package_audit_artifact": True,
        "package_step_before_audit": True,
    }


def test_release_templates_include_mvp_exit_audit_tool(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    old_dir.mkdir()
    new_dir.mkdir()
    args = release.parse_args(["--a", str(old_dir), "--b", str(new_dir), "--out", str(out_dir)])

    release._write_release_templates(out_dir, args)

    assert not (out_dir / "cli" / "validate_drawing_compare_realset.py").exists()
    assert (out_dir / "cli" / "audit_drawing_compare_mvp_exit.py").exists()
    assert (out_dir / "cli" / "prepare_drawing_compare_customer_evidence.py").exists()
    assert (out_dir / "cli" / "inventory_drawing_compare_customer_evidence.py").exists()
    readme = (out_dir / "README_INTERNAL_PILOT.md").read_text(encoding="utf-8")
    assert "## Validation (source checkout)" in readme
    assert "## Resume (source checkout)" in readme
    assert "python scripts\\validate_drawing_compare_realset.py" in readme
    assert "python cli\\validate_drawing_compare_realset.py" not in readme
    assert "--no-expand-blocks" in readme
    assert "<cad_block_text_no_expand_validation>" in readme
    assert (
        "--results-dir <cad_block_text_no_expand_validation> --out <customer_evidence_manifest.json>"
        in readme
    )
    assert (
        "--results-dir <cad_block_text_no_expand_validation> --release-manifest <release_manifest.json>"
        in readme
    )
    assert "Include the no-expand CAD block-text validation output" in readme
    assert "cad_block_text_detection_without_expansion" in (
        out_dir / "mvp_exit_prompt_to_artifact_checklist.md"
    ).read_text(encoding="utf-8")
    assert "audit_drawing_compare_mvp_exit.py" in readme
    assert "prepare_drawing_compare_customer_evidence.py" in readme
    assert "inventory_drawing_compare_customer_evidence.py" in readme
    assert "--large-dwg-probe <large_dwg_probe.json>" in readme
    assert "diagnostics.large_dwg_probe_passed" in readme
    assert "diagnostics.large_dwg_probe_issues" in readme
    assert "--portable-paths" in readme
    assert "`root_N` aliases" in readme
    assert "status=ready_for_manifest" in readme
    assert "diagnostics.validation_outputs_missing_selected_zone_telemetry" in readme
    assert "diagnostics.validation_outputs_missing_cad_block_text_no_expand" in readme
    assert "diagnostics.validation_outputs_with_cad_block_text_no_expand" in readme
    assert "diagnostics.audited_review_ground_truth_rows" in readme
    assert "diagnostics.valid_review_ground_truth_csv_candidates" in readme
    assert "diagnostics.required_operator_workflow_checks" in readme
    assert "diagnostics.missing_operator_workflow_checks" in readme
    assert "diagnostics.operator_notes_missing_required_checks" in readme
    assert "diagnostics.operator_notes_with_approved_structural_role" in readme
    assert "diagnostics.operator_notes_missing_approved_structural_role" in readme
    assert "diagnostics.approved_operator_reviewer_roles" in readme
    assert "customer_evidence_manifest_summaries" in readme
    assert "diagnostics.customer_evidence_manifests_not_ready" in readme
    assert "diagnostics.customer_evidence_manifests_missing_approved_ground_truth" in readme
    assert "--customer-evidence-manifest" in readme
    assert "--dataset-source-kind customer_grade" in readme
    assert "--dataset-approval-status approved_for_mvp_exit" in readme
    assert "--ground-truth-status approved" in readme
    assert "--operator-reviewer-role structural_review_lead" in readme
    assert "--min-total-pairs 20" in readme
    assert "--max-total-pairs 50" in readme
    assert "--max-first-review-ready-s 1800" in readme
    assert "--max-cold-zone-render-ms 10000" in readme
    assert "--max-cache-hit-zone-render-ms 2000" in readme
    assert "20-50 completed sheets/pairs" in readme
    assert "timings.total_s <= 1800" in readme
    assert "selected-zone render telemetry for every completed output" in readme
    assert "operator_dry_run_checklist_template.md" in readme
    assert "reviewer_role: structural_review_lead" in readme
    assert "Do not pass the template file itself" in readme
    assert "operator_dry_run_notes.md" in readme
    assert "the template file itself is rejected as evidence" in readme
    assert "not release templates, handoff docs, or quick references" in readme
    assert "mvp_exit_prompt_to_artifact_checklist.md" in readme
    assert "customer_evidence_closeout_packet.md" in readme
    assert "customer_evidence_request_ko.md" in readme
    assert "review_ground_truth_template.csv" in readme
    assert "--run-mvp-exit-audit" in readme
    assert "--run-mvp-exit-audit` requires a customer evidence manifest" in readme
    assert "validation result source" in readme
    assert "validation_summary.json" in readme
    assert "both `.dwg` and `.dxf` source-extension evidence" in (
        out_dir / "mvp_exit_prompt_to_artifact_checklist.md"
    ).read_text(encoding="utf-8")
    assert "`.pdf` source-extension evidence on both sides" in (
        out_dir / "mvp_exit_prompt_to_artifact_checklist.md"
    ).read_text(encoding="utf-8")
    assert "clear blocked/cross-family reason" in (
        out_dir / "mvp_exit_prompt_to_artifact_checklist.md"
    ).read_text(encoding="utf-8")
    prompt_checklist = (out_dir / "mvp_exit_prompt_to_artifact_checklist.md").read_text(
        encoding="utf-8"
    )
    assert "Two files/folders are selected and automatic comparison completes" in prompt_checklist
    assert "input_selection" in prompt_checklist
    assert "automatic_compare_completed" in prompt_checklist
    assert "Pre-final inventory has no stale customer manifest warnings" in prompt_checklist
    assert "diagnostics.large_dwg_probe_passed=true" in prompt_checklist
    assert "diagnostics.customer_evidence_manifests_not_ready" in prompt_checklist
    assert "diagnostics.customer_evidence_manifests_missing_approved_ground_truth" in prompt_checklist
    closeout_packet = (out_dir / "customer_evidence_closeout_packet.md").read_text(
        encoding="utf-8"
    )
    assert "Drawing Compare Customer Evidence Closeout Packet" in closeout_packet
    assert "guidance only" in closeout_packet
    assert "review_ground_truth.csv" in closeout_packet
    assert "operator_dry_run_notes.md" in closeout_packet
    assert "status=ready_for_manifest" in closeout_packet
    assert "--large-dwg-probe <large_dwg_probe.json>" in closeout_packet
    assert "diagnostics.large_dwg_probe_passed=true" in closeout_packet
    assert "`release_manifest.json` is discovered" in closeout_packet
    assert "omit it for local-only inventory" in closeout_packet
    assert "diagnostics.customer_evidence_manifests_not_ready" in closeout_packet
    assert "--evidence-level customer_grade" in closeout_packet
    evidence_request_path = out_dir / "customer_evidence_request_ko.md"
    assert evidence_request_path.read_bytes() == release.CUSTOMER_EVIDENCE_REQUEST_KO_SOURCE.read_bytes()
    evidence_request_ko = evidence_request_path.read_text(encoding="utf-8")
    assert "Drawing Compare 고객급 증거 요청서" in evidence_request_ko
    assert "review_ground_truth.csv" in evidence_request_ko
    assert "operator_dry_run_notes.md" in evidence_request_ko
    assert "input_selection" in evidence_request_ko
    assert "customer_grade" in evidence_request_ko
    assert "status=passed" in evidence_request_ko

    review_truth_template = (out_dir / "review_ground_truth_template.csv").read_text(
        encoding="utf-8"
    )
    assert review_truth_template.startswith(
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status,notes"
    )
    assert "member|mixed" in review_truth_template
    assert "dimension|mixed" in review_truth_template
    assert "rebar|mixed" in review_truth_template
    assert "D13@100;D13@200" in review_truth_template
    assert "SHD13@100;SHD13@200" in review_truth_template
    assert "GRID A-B;GRID A-C" in review_truth_template

    evidence_template = json.loads(
        (out_dir / "customer_evidence_manifest_template.json").read_text(encoding="utf-8")
    )
    assert evidence_template["evidence_level"] == "customer_grade"
    assert evidence_template["dataset_provenance"] == {
        "source_kind": "",
        "source_description": "",
        "approval_status": "",
        "approver": "",
    }
    assert evidence_template["format_coverage"] == {
        "dwg_dxf": False,
        "pdf_pdf": False,
        "cad_pdf_blocked": False,
    }
    assert evidence_template["cad_policy_evidence"] == {
        "block_text_detection_without_expansion": False,
    }
    assert "section_dimension_change" in evidence_template["structural_coverage"]
    assert "grid_change" in evidence_template["structural_coverage"]
    assert evidence_template["selected_zone_performance"]["status"] == ""
    assert evidence_template["selected_zone_performance"]["max_cold_zone_render_ms"] == 10000.0
    assert evidence_template["selected_zone_performance"]["max_cache_hit_zone_render_ms"] == 2000.0
    assert evidence_template["workbench_acceptance"]["required_items"] == ["5.", "8.", "8b.", "9b.", "9c.", "10."]
    assert evidence_template["readiness"]["status"] == ""
    assert "Do not use this template as final MVP completion evidence" in evidence_template["readiness"]["warning"]
    assert "confirmed_false_positive_hold_used" in evidence_template["operator_dry_run"]["workflow_checks"]
    assert "notes_file" in evidence_template["operator_dry_run"]["artifacts"]
    assert "confirmed_export_artifact" in evidence_template["operator_dry_run"]["artifacts"]
    checklist = (out_dir / "operator_dry_run_checklist_template.md").read_text(encoding="utf-8")
    assert "reviewer_role: structural_review_lead" in checklist
    assert "selected_zone_before_after_sync_zoom" in checklist
    assert "confirmed_only_export_checked" in checklist
    prompt_checklist = (out_dir / "mvp_exit_prompt_to_artifact_checklist.md").read_text(
        encoding="utf-8"
    )
    assert "Prompt-to-Artifact Checklist" in prompt_checklist
    assert "DWG/DXF comparison supported" in prompt_checklist
    assert "PDF-PDF comparison supported" in prompt_checklist
    assert "CAD-PDF cross comparison blocked" in prompt_checklist
    assert "review_queue.mode=structural_core" in prompt_checklist
    assert "Workbench acceptance Item 9b" in prompt_checklist
    assert "Workbench acceptance Item 9c" in prompt_checklist
    assert "customer_evidence_manifest.json" in prompt_checklist
    assert "--evidence-level customer_grade" in prompt_checklist
    assert "`ground_truth.status=approved`" in prompt_checklist
    assert "--results-dir <cad_block_text_no_expand_validation>" in prompt_checklist
    assert "template/handoff paths are rejected as evidence" in prompt_checklist
    assert "`reviewer_role` is an approved structural review lead/team lead role" in prompt_checklist


def test_release_manifest_lists_operator_checklist_template(tmp_path: Path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    old_dir.mkdir()
    new_dir.mkdir()
    monkeypatch.setattr(release, "_oda_preflight", lambda python: {"status": "skipped"})
    monkeypatch.setattr(release, "_run_step", lambda *args, **kwargs: 0)

    code = release.main(
        [
            "--a",
            str(old_dir),
            "--b",
            str(new_dir),
            "--out",
            str(out_dir),
            "--skip-tests",
            "--skip-realset",
            "--skip-build",
            "--skip-workbench-acceptance",
            "--skip-packaged-launch-smoke",
        ]
    )

    assert code == 0
    manifest = json.loads((out_dir / "release_manifest.json").read_text(encoding="utf-8"))
    assert "cli_runner" not in manifest["artifacts"]
    checklist = Path(manifest["artifacts"]["operator_dry_run_checklist_template"])
    assert checklist.name == "operator_dry_run_checklist_template.md"
    assert checklist.exists()
    review_truth_template = Path(manifest["artifacts"]["review_ground_truth_template"])
    assert review_truth_template.name == "review_ground_truth_template.csv"
    assert review_truth_template.exists()
    prompt_checklist = Path(manifest["artifacts"]["mvp_exit_prompt_to_artifact_checklist"])
    assert prompt_checklist.name == "mvp_exit_prompt_to_artifact_checklist.md"
    assert prompt_checklist.exists()
    closeout_packet = Path(manifest["artifacts"]["customer_evidence_closeout_packet"])
    assert closeout_packet.name == "customer_evidence_closeout_packet.md"
    assert closeout_packet.exists()
    evidence_request_ko = Path(manifest["artifacts"]["customer_evidence_request_ko"])
    assert evidence_request_ko.name == "customer_evidence_request_ko.md"
    assert evidence_request_ko.exists()
    inventory_tool = Path(manifest["artifacts"]["customer_evidence_inventory_tool"])
    assert inventory_tool.name == "inventory_drawing_compare_customer_evidence.py"
    assert inventory_tool.exists()


def test_release_cli_evidence_tools_start_without_source_tree_imports(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    old_dir.mkdir()
    new_dir.mkdir()
    args = release.parse_args(["--a", str(old_dir), "--b", str(new_dir), "--out", str(out_dir)])
    release._write_release_templates(out_dir, args)

    for script in (
        out_dir / "cli" / "audit_drawing_compare_mvp_exit.py",
        out_dir / "cli" / "prepare_drawing_compare_customer_evidence.py",
        out_dir / "cli" / "inventory_drawing_compare_customer_evidence.py",
    ):
        result = subprocess.run(
            [sys.executable, str(script.relative_to(out_dir)), "--help"],
            cwd=out_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_release_customer_shareable_package_excludes_internal_paths(tmp_path: Path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "release"
    old_dir.mkdir()
    new_dir.mkdir()
    monkeypatch.setattr(release, "_oda_preflight", lambda python: {"status": "skipped"})
    monkeypatch.setattr(release, "_run_step", lambda *args, **kwargs: 0)

    code = release.main(
        [
            "--a",
            str(old_dir),
            "--b",
            str(new_dir),
            "--out",
            str(out_dir),
            "--skip-tests",
            "--skip-realset",
            "--skip-build",
            "--skip-workbench-acceptance",
            "--skip-packaged-launch-smoke",
        ]
    )

    assert code == 0
    manifest = json.loads((out_dir / "release_manifest.json").read_text(encoding="utf-8"))
    package_dir = Path(manifest["artifacts"]["customer_shareable_package_dir"])
    package_zip = Path(manifest["artifacts"]["customer_shareable_package_zip"])
    package_manifest = json.loads(
        (package_dir / "customer_package_manifest.json").read_text(encoding="utf-8")
    )
    package_audit = json.loads(
        (package_dir / "customer_package_path_audit.json").read_text(encoding="utf-8")
    )

    assert package_dir.exists()
    assert package_zip.exists()
    assert not (package_dir / "release_manifest.json").exists()
    assert package_manifest["package_type"] == "customer_shareable"
    assert package_manifest["internal_release_manifest_included"] is False
    assert "customer_package_manifest.json" in package_manifest["contents"]
    assert "customer_package_path_audit.json" in package_manifest["contents"]
    assert "customer_evidence_closeout_packet.md" in package_manifest["contents"]
    assert "customer_evidence_request_ko.md" in package_manifest["contents"]
    assert "release_manifest.json" not in package_manifest["contents"]
    assert package_audit["status"] == "passed"
    assert package_audit["leak_count"] == 0
    assert "scanned_app_first_party_files" in package_audit
    assert "scanned_binary_files" in package_audit
    package_step = next(
        step for step in manifest["steps"] if step["name"] == "customer_shareable_package_path_audit"
    )
    assert package_step["status"] == "passed"

    sample = json.loads((package_dir / "sample_manifest.json").read_text(encoding="utf-8"))
    assert sample["datasets"][0]["a"] == "<before_folder_or_file>"
    assert sample["datasets"][0]["b"] == "<after_folder_or_file>"
    with zipfile.ZipFile(package_zip) as archive:
        names = set(archive.namelist())
        request_bytes = archive.read("customer_evidence_request_ko.md")
    assert "release_manifest.json" not in names
    assert "customer_package_manifest.json" in names
    assert "customer_package_path_audit.json" in names
    assert "customer_evidence_closeout_packet.md" in names
    assert "customer_evidence_request_ko.md" in names
    assert request_bytes == release.CUSTOMER_EVIDENCE_REQUEST_KO_SOURCE.read_bytes()


def test_customer_shareable_package_audit_detects_absolute_paths(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "leaky.json").write_text(
        '{"path": "C:\\\\Users\\\\user\\\\AppData\\\\Local\\\\Temp\\\\drawing"}',
        encoding="utf-8",
    )

    audit = release._audit_customer_shareable_package(package_dir)

    assert audit["status"] == "failed"
    assert audit["leak_count"] == 1
    assert audit["leaks"][0]["path"] == "leaky.json"


def test_customer_shareable_package_audit_scans_first_party_app_paths(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    app_src = package_dir / "app" / "DrawingCompareWorkbench" / "_internal" / "src"
    app_src.mkdir(parents=True)
    (app_src / "leaky.py").write_text(
        'BUILD_ROOT = "C:\\\\Users\\\\user\\\\.codex\\\\worktrees\\\\45ea\\\\02.TEKLA_MCP"\n',
        encoding="utf-8",
    )
    third_party = package_dir / "app" / "DrawingCompareWorkbench" / "_internal" / "ezdxf"
    third_party.mkdir(parents=True)
    (third_party / "defaults.py").write_text(
        'TEKLA_PATH = r"C:\\\\Program Files\\\\Tekla Structures\\\\2025.0"\n',
        encoding="utf-8",
    )

    audit = release._audit_customer_shareable_package(package_dir)

    assert audit["status"] == "failed"
    assert audit["scanned_app_first_party_files"] == 1
    assert audit["skipped_app_internal_files"] == 1
    assert audit["leak_count"] == 1
    assert audit["leaks"][0]["path"] == "app/DrawingCompareWorkbench/_internal/src/leaky.py"


def test_customer_shareable_package_audit_rejects_python_bytecode(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    pycache = (
        package_dir
        / "app"
        / "DrawingCompareWorkbench"
        / "_internal"
        / "src"
        / "services"
        / "__pycache__"
    )
    pycache.mkdir(parents=True)
    (pycache / "leaky.cpython-312.pyc").write_bytes(b"C:\\Users\\user\\.codex\\worktrees\\45ea\\02.TEKLA_MCP")

    audit = release._audit_customer_shareable_package(package_dir)

    assert audit["status"] == "failed"
    assert audit["leak_count"] == 0
    assert audit["disallowed_file_count"] == 1
    assert audit["disallowed_files"] == [
        "app/DrawingCompareWorkbench/_internal/src/services/__pycache__/leaky.cpython-312.pyc"
    ]


def test_customer_shareable_package_audit_scans_exe_binary_paths(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    exe = package_dir / "app" / "DrawingCompareWorkbench" / "DrawingCompareWorkbench.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"\x00C:\\Users\\user\\.codex\\worktrees\\45ea\\02.TEKLA_MCP\\build\x00")

    audit = release._audit_customer_shareable_package(package_dir)

    assert audit["status"] == "failed"
    assert audit["scanned_binary_files"] == 1
    assert audit["leak_count"] == 1
    assert audit["leaks"][0]["path"] == "app/DrawingCompareWorkbench/DrawingCompareWorkbench.exe"


def test_customer_shareable_package_excludes_python_bytecode_from_zip(tmp_path: Path) -> None:
    out_dir = tmp_path / "release"
    for filename, content in {
        "README_INTERNAL_PILOT.md": "readme",
        "sample_manifest.json": "{}",
        "customer_evidence_manifest_template.json": "{}",
        "operator_dry_run_checklist_template.md": "checklist",
        "review_ground_truth_template.csv": "drawing_label,category,summary_contains,source_format,detection_source,bbox_status\n",
        "mvp_exit_prompt_to_artifact_checklist.md": "checklist",
        "customer_evidence_closeout_packet.md": "closeout",
        "customer_evidence_request_ko.md": "korean request",
    }.items():
        path = out_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    cli_pycache = out_dir / "cli" / "__pycache__"
    cli_pycache.mkdir(parents=True)
    (out_dir / "cli" / "audit_drawing_compare_mvp_exit.py").write_text("print('ok')\n", encoding="utf-8")
    (cli_pycache / "audit.cpython-312.pyc").write_bytes(b"bytecode")
    app_src = out_dir / "dist" / "DrawingCompareWorkbench" / "_internal" / "src"
    app_src.mkdir(parents=True)
    (out_dir / "dist" / "DrawingCompareWorkbench" / "DrawingCompareWorkbench.exe").write_bytes(b"exe")
    (app_src / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    app_pycache = app_src / "__pycache__"
    app_pycache.mkdir()
    (app_pycache / "module.cpython-312.pyc").write_bytes(
        b"C:\\Users\\user\\.codex\\worktrees\\45ea\\02.TEKLA_MCP"
    )

    result = release._write_customer_shareable_package(out_dir)

    assert result["audit_status"] == "passed"
    assert json.loads(Path(result["audit_path"]).read_text(encoding="utf-8"))["scanned_binary_files"] == 1
    package_dir = Path(result["package_dir"])
    assert not list(package_dir.rglob("*.pyc"))
    assert not list(package_dir.rglob("__pycache__"))
    with zipfile.ZipFile(result["zip_path"]) as archive:
        names = archive.namelist()
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)


def test_customer_shareable_package_audit_allows_benign_first_party_program_files_defaults(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "package"
    app_src = package_dir / "app" / "DrawingCompareWorkbench" / "_internal" / "src"
    app_src.mkdir(parents=True)
    (app_src / "defaults.py").write_text(
        'ODA_PATH = r"C:\\\\Program Files\\\\ODA\\\\ODAFileConverter\\\\ODAFileConverter.exe"\n',
        encoding="utf-8",
    )

    audit = release._audit_customer_shareable_package(package_dir)

    assert audit["status"] == "passed"
    assert audit["scanned_app_first_party_files"] == 1
    assert audit["leak_count"] == 0
