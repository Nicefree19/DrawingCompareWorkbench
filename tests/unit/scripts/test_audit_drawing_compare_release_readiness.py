from __future__ import annotations

import json
from pathlib import Path

from scripts import audit_drawing_compare_release_readiness as audit


def test_release_readiness_audit_accepts_valid_partial_manifest(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(tmp_path)

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
    )

    assert report["status"] == "partial"
    assert report["summary"]["hard_failed"] == 0
    assert "AC1018 real before/after compare baseline gap remains" in report["partial_reasons"]
    assert "All DWG versions supported." in report["forbidden_release_claims"]


def test_release_readiness_audit_fails_missing_evidence_counts(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        evidence_overrides={"pdf_pairs": 9},
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
    )

    assert report["status"] == "failed"
    failed = _failed_checks(report)
    assert "evidence_minimum_counts" in failed
    assert "pdf_pairs=9/10" in failed["evidence_minimum_counts"]["detail"]


def test_release_readiness_audit_fails_forbidden_wording(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        manifest_overrides={"release_claims": ["All DWG versions supported."]},
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
    )

    assert report["status"] == "failed"
    failed = _failed_checks(report)
    assert "release_wording_claims" in failed
    assert "forbidden release wording" in failed["release_wording_claims"]["detail"]


def test_release_readiness_audit_fails_default_customer_oda_call(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        run_overrides={"customer_path": True, "dwg_backend_mode": "oda_converter", "explicit": False},
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
    )

    assert report["status"] == "failed"
    failed = _failed_checks(report)
    assert "customer_runtime_policy" in failed
    assert "oda_converter used" in failed["customer_runtime_policy"]["detail"]


def test_release_readiness_audit_fails_unapproved_commercial_sdk_runtime(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        run_overrides={
            "dwg_backend_mode": "commercial_sdk",
            "explicit": True,
            "implementation_status": "placeholder",
            "license_id": "COMMERCIAL-SDK-PENDING",
        },
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
    )

    assert report["status"] == "failed"
    failed = _failed_checks(report)
    assert "customer_runtime_policy" in failed
    assert "commercial_sdk implementation_status=placeholder" in failed["customer_runtime_policy"]["detail"]
    assert "commercial_sdk license_id=COMMERCIAL-SDK-PENDING" in failed["customer_runtime_policy"]["detail"]


def test_release_readiness_audit_fails_missing_provenance(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        result_overrides={"import_report": None},
        run_overrides={"provenance": None},
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
    )

    assert report["status"] == "failed"
    failed = _failed_checks(report)
    assert "fallback_provenance" in failed
    assert "original/source input provenance missing" in failed["fallback_provenance"]["detail"]


def test_release_readiness_audit_fails_partial_without_warning(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        result_overrides={"status": "partial", "warnings": [], "unsupported_entities": []},
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
    )

    assert report["status"] == "failed"
    failed = _failed_checks(report)
    assert "partial_import_warnings" in failed
    assert "warnings/unsupported evidence is missing" in failed["partial_import_warnings"]["detail"]


def test_release_readiness_audit_loads_utf8_bom_json(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(tmp_path)
    customer_payload = customer_manifest.read_text(encoding="utf-8")
    metrics_payload = metrics_json.read_text(encoding="utf-8")
    customer_manifest.write_text(customer_payload, encoding="utf-8-sig")
    metrics_json.write_text(metrics_payload, encoding="utf-8-sig")

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
    )

    checks = _checks(report)
    assert checks["customer_evidence_manifest_loadable"]["passed"] is True
    assert checks["baseline_metrics_loadable"]["passed"] is True
    assert checks["evidence_minimum_counts"]["passed"] is True
    assert checks["metric_release_thresholds"]["passed"] is True


def test_release_readiness_audit_reads_partial_warning_from_referenced_result_json(tmp_path: Path) -> None:
    runner_summary = _write_product_bridge_runner_summary(tmp_path)
    pair_output = tmp_path / "ac1032_001.json"
    _write_json(
        pair_output,
        {
            "status": "partial",
            "result": {
                "warnings": ["unsupported HATCH approximated"],
                "metadata": {"partial_imports": [{"entity": "HATCH", "count": 1}]},
            },
        },
    )
    summary_payload = json.loads(runner_summary.read_text(encoding="utf-8"))
    summary_payload["pairs"][0]["cad_compare_status"] = "partial"
    summary_payload["pairs"][0]["output_json"] = str(pair_output)
    summary_payload["pairs"][0]["provenance"]["effective_result_json"] = str(pair_output)
    _write_json(runner_summary, summary_payload)
    _, _, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")

    report = audit.run_audit(
        result_json=runner_summary,
        run_manifest=runner_summary,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
    )

    checks = _checks(report)
    assert report["status"] == "passed"
    assert checks["partial_import_warnings"]["passed"] is True


def test_release_readiness_audit_fails_missing_timeout_cleanup(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        run_overrides={"process_cleanup": None},
        metrics_overrides={"orphan_processes": None},
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
    )

    assert report["status"] == "failed"
    failed = _failed_checks(report)
    assert "timeout_cleanup_evidence" in failed
    assert "cleanup evidence missing" in failed["timeout_cleanup_evidence"]["detail"]


def test_release_readiness_audit_passes_with_all_version_fallback_gate(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
    )

    assert report["status"] == "passed"
    assert report["inputs"]["dwg_all_version_audit"] == str(all_version_audit)
    checks = _checks(report)
    assert checks["dwg_all_version_fallback_audit"]["passed"] is True
    assert checks["native_dwg_claim_gate"]["passed"] is True


def test_release_readiness_audit_cli_accepts_all_version_fallback_gate(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")
    out = tmp_path / "release-readiness.json"

    exit_code = audit.main(
        [
            "--result-json",
            str(result_json),
            "--run-manifest",
            str(run_manifest),
            "--customer-evidence-manifest",
            str(customer_manifest),
            "--baseline-metrics",
            str(metrics_json),
            "--dwg-all-version-audit",
            str(all_version_audit),
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["inputs"]["dwg_all_version_audit"] == str(all_version_audit)


def test_release_readiness_audit_fails_failed_all_version_fallback_gate(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(
        tmp_path,
        claim_scope="fallback",
        status="failed",
        fallback_missing_versions=["AC1032"],
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
    )

    assert report["status"] == "failed"
    failed = _failed_checks(report)
    assert "dwg_all_version_fallback_audit" in failed
    assert "fallback_missing_versions=AC1032" in failed["dwg_all_version_fallback_audit"]["detail"]


def test_release_readiness_audit_surfaces_native_gap_without_requiring_it(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")
    native_audit = _write_all_version_audit(
        tmp_path,
        claim_scope="native",
        status="failed",
        native_missing_versions=list(audit.DWG_TARGET_VERSION_CODES),
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
        native_dwg_audit=native_audit,
    )

    assert report["status"] == "partial"
    checks = _checks(report)
    assert checks["native_dwg_claim_gate"]["severity"] == "warning"
    assert "native_missing_versions=AC1009" in checks["native_dwg_claim_gate"]["detail"]


def test_release_readiness_audit_fails_when_native_dwg_is_required(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")
    native_audit = _write_all_version_audit(
        tmp_path,
        claim_scope="native",
        status="failed",
        native_missing_versions=list(audit.DWG_TARGET_VERSION_CODES),
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
        native_dwg_audit=native_audit,
        require_native_dwg=True,
    )

    assert report["status"] == "failed"
    failed = _failed_checks(report)
    assert failed["native_dwg_claim_gate"]["severity"] == "hard"
    assert "native_missing_versions=AC1009" in failed["native_dwg_claim_gate"]["detail"]


def test_release_readiness_audit_requires_bridge_contract_for_json_bridge_native_audit(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")
    native_audit = _write_all_version_audit(
        tmp_path,
        claim_scope="native",
        status="passed",
        native_missing_versions=[],
        adapter_diagnostics=_bridge_diagnostics(),
    )

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
        native_dwg_audit=native_audit,
        require_native_dwg=True,
    )

    failed = _failed_checks(report)
    assert report["status"] == "failed"
    assert failed["dwg_json_bridge_contract"]["severity"] == "hard"
    assert "bridge contract required" in failed["dwg_json_bridge_contract"]["detail"]


def test_release_readiness_audit_requires_product_bridge_evidence_for_json_bridge_native_claim(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")
    native_audit = _write_all_version_audit(
        tmp_path,
        claim_scope="native",
        status="passed",
        native_missing_versions=[],
        adapter_diagnostics=_bridge_diagnostics(),
    )
    bridge_contract = _write_bridge_contract(tmp_path)

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
        native_dwg_audit=native_audit,
        dwg_json_bridge_contract=bridge_contract,
        require_native_dwg=True,
    )

    failed = _failed_checks(report)
    assert report["status"] == "failed"
    assert failed["dwg_json_bridge_product_evidence"]["severity"] == "hard"
    assert "product result/run evidence" in failed["dwg_json_bridge_product_evidence"]["detail"]


def test_release_readiness_audit_rejects_converted_dxf_product_bridge_for_native_claim(tmp_path: Path) -> None:
    bridge_metadata = _bridge_metadata(
        evidence_scope="converted_dxf_bridge",
        uses_native_dwg=False,
        uses_converted_dxf=True,
    )
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        result_overrides=_bridge_product_result(bridge_metadata=bridge_metadata),
        run_overrides=_bridge_product_run(bridge_metadata=bridge_metadata),
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")
    native_audit = _write_all_version_audit(
        tmp_path,
        claim_scope="native",
        status="passed",
        native_missing_versions=[],
        adapter_diagnostics=_bridge_diagnostics(),
    )
    bridge_contract = _write_bridge_contract(tmp_path)

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
        native_dwg_audit=native_audit,
        dwg_json_bridge_contract=bridge_contract,
        require_native_dwg=True,
    )

    failed = _failed_checks(report)
    assert report["status"] == "failed"
    detail = failed["dwg_json_bridge_product_evidence"]["detail"]
    assert "bridge_metadata[0].uses_converted_dxf=true" in detail
    assert "bridge_metadata[0].native_evidence_scope=converted_dxf_bridge" in detail


def test_release_readiness_audit_rejects_unspecified_product_bridge_for_native_claim(tmp_path: Path) -> None:
    bridge_metadata = _bridge_metadata(evidence_scope="", uses_native_dwg=False)
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        result_overrides=_bridge_product_result(bridge_metadata=bridge_metadata),
        run_overrides=_bridge_product_run(bridge_metadata=bridge_metadata),
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")
    native_audit = _write_all_version_audit(
        tmp_path,
        claim_scope="native",
        status="passed",
        native_missing_versions=[],
        adapter_diagnostics=_bridge_diagnostics(),
    )
    bridge_contract = _write_bridge_contract(tmp_path)

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
        native_dwg_audit=native_audit,
        dwg_json_bridge_contract=bridge_contract,
        require_native_dwg=True,
    )

    failed = _failed_checks(report)
    assert report["status"] == "failed"
    assert "bridge_metadata[0].native_evidence_scope=missing" in failed["dwg_json_bridge_product_evidence"]["detail"]


def test_release_readiness_audit_accepts_bridge_contract_for_json_bridge_native_audit(tmp_path: Path) -> None:
    result_json, run_manifest, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        result_overrides=_bridge_product_result(),
        run_overrides=_bridge_product_run(),
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")
    native_audit = _write_all_version_audit(
        tmp_path,
        claim_scope="native",
        status="passed",
        native_missing_versions=[],
        adapter_diagnostics=_bridge_diagnostics(),
    )
    bridge_contract = _write_bridge_contract(tmp_path)

    report = audit.run_audit(
        result_json=result_json,
        run_manifest=run_manifest,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
        native_dwg_audit=native_audit,
        dwg_json_bridge_contract=bridge_contract,
        require_native_dwg=True,
    )

    checks = _checks(report)
    assert report["status"] == "passed"
    assert report["inputs"]["dwg_json_bridge_contract"] == str(bridge_contract)
    assert checks["native_dwg_claim_gate"]["passed"] is True
    assert checks["dwg_json_bridge_contract"]["passed"] is True
    assert checks["dwg_json_bridge_product_evidence"]["passed"] is True


def test_release_readiness_audit_accepts_product_bridge_evidence_runner_summary(tmp_path: Path) -> None:
    runner_summary = _write_product_bridge_runner_summary(tmp_path)
    _, _, customer_manifest, metrics_json = _write_packet(
        tmp_path,
        manifest_overrides={"known_gaps": None},
    )
    all_version_audit = _write_all_version_audit(tmp_path, claim_scope="fallback", status="passed")
    native_audit = _write_all_version_audit(
        tmp_path,
        claim_scope="native",
        status="passed",
        native_missing_versions=[],
        adapter_diagnostics=_bridge_diagnostics(),
    )
    bridge_contract = _write_bridge_contract(tmp_path)

    report = audit.run_audit(
        result_json=runner_summary,
        run_manifest=runner_summary,
        customer_evidence_manifest=customer_manifest,
        baseline_metrics=metrics_json,
        dwg_all_version_audit=all_version_audit,
        native_dwg_audit=native_audit,
        dwg_json_bridge_contract=bridge_contract,
        require_native_dwg=True,
    )

    checks = _checks(report)
    assert report["status"] == "passed"
    assert checks["fallback_provenance"]["passed"] is True
    assert checks["timeout_cleanup_evidence"]["passed"] is True
    assert checks["dwg_json_bridge_product_evidence"]["passed"] is True


def _write_packet(
    tmp_path: Path,
    *,
    result_overrides: dict | None = None,
    run_overrides: dict | None = None,
    manifest_overrides: dict | None = None,
    evidence_overrides: dict | None = None,
    metrics_overrides: dict | None = None,
) -> tuple[Path, Path, Path, Path]:
    result = {
        "status": "partial",
        "warnings": ["unsupported HATCH approximated"],
        "unsupported_entities": [{"type": "HATCH", "count": 1}],
        "import_report": {
            "provenance": {
                "original_inputs": ["before.dwg", "after.dwg"],
                "effective_inputs": ["before.dxf", "after.dxf"],
                "backend_mode": "user_converter",
            }
        },
    }
    run = {
        "mode": "customer",
        "dwg_backend_mode": "user_converter",
        "explicit": True,
        "provenance": {
            "source_paths": ["before.dwg", "after.dwg"],
            "effective_paths": ["before.dxf", "after.dxf"],
        },
        "process_cleanup": {"orphan_processes": 0},
    }
    evidence_counts = {
        "pdf_pairs": 10,
        "dxf_pairs": 10,
        "large_cad_dxf_pairs": 3,
        "ac1015_native_baselines": 3,
        "ac1024_converted_dxf_fallback_pairs": 2,
        "ac1027_converted_dxf_fallback_pairs": 2,
        "ac1032_converted_dxf_fallback_pairs": 2,
        "negative_failure_samples": 5,
        "partial_import_samples": 3,
        "block_text_dimension_pairs": 5,
    }
    evidence_counts.update(evidence_overrides or {})
    manifest = {
        "evidence_counts": evidence_counts,
        "release_claims": [
            "Modern DWGs can be compared through user-provided converted DXF where matching converted files are available."
        ],
        "known_gaps": {
            "AC1018": "real before/after compare baseline missing",
            "AC1021": "real before/after compare baseline missing",
        },
    }
    metrics = {
        "metrics": {
            "recall": 0.92,
            "precision": 0.88,
            "false_positive_zone_rate": 0.12,
            "duplicate_zone_rate": 0.07,
            "overlay_error_px_150dpi": 8,
            "small_drawing_seconds": 20,
            "medium_drawing_seconds": 90,
            "large_drawing_seconds": 580,
            "progress_max_gap_s": 8,
            "cancel_response_s": 7,
            "orphan_processes": 0,
            "customer_path_oda_calls": 0,
            "exported_sensitive_path_leaks": 0,
        }
    }
    _deep_update(result, result_overrides or {})
    _deep_update(run, run_overrides or {})
    _deep_update(manifest, manifest_overrides or {})
    _deep_update(metrics["metrics"], metrics_overrides or {})

    result_json = tmp_path / "result.json"
    run_manifest = tmp_path / "run_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    metrics_json = tmp_path / "baseline_metrics.json"
    _write_json(result_json, result)
    _write_json(run_manifest, run)
    _write_json(customer_manifest, manifest)
    _write_json(metrics_json, metrics)
    return result_json, run_manifest, customer_manifest, metrics_json


def _write_all_version_audit(
    tmp_path: Path,
    *,
    claim_scope: str,
    status: str,
    fallback_missing_versions: list[str] | None = None,
    native_missing_versions: list[str] | None = None,
    adapter_diagnostics: dict | None = None,
) -> Path:
    targets = list(audit.DWG_TARGET_VERSION_CODES)
    fallback_missing = set(fallback_missing_versions or [])
    native_missing = set(native_missing_versions if native_missing_versions is not None else targets)
    payload = {
        "schema_version": audit.DWG_ALL_VERSION_AUDIT_SCHEMA,
        "status": status,
        "claim_scope": claim_scope,
        "target_versions": targets,
        "summary": {
            "fallback_ready_versions": [code for code in targets if code not in fallback_missing],
            "fallback_missing_versions": sorted(fallback_missing),
            "native_ready_versions": [code for code in targets if code not in native_missing],
            "native_missing_versions": sorted(native_missing),
            "claim_violation_count": 0,
        },
        "claim_violations": [],
        "versions": [
            {
                "code": code,
                "fallback_ready": code not in fallback_missing,
                "native_ready": code not in native_missing,
                "default_customer_oda_calls": 0,
            }
            for code in targets
        ],
    }
    if adapter_diagnostics is not None:
        payload["backend_check"] = {"adapter_diagnostics": adapter_diagnostics}
    path = tmp_path / f"{claim_scope}-all-version-audit.json"
    _write_json(path, payload)
    return path


def _write_bridge_contract(tmp_path: Path, *, status: str = "passed") -> Path:
    payload = {
        "schema_version": audit.DWG_JSON_BRIDGE_CONTRACT_SCHEMA,
        "status": status,
        "summary": {
            "input_count": 1,
            "accepted_import_count": 1,
            "failed_import_count": 0,
            "missing_input_count": 0,
            "diagnostic_error_count": 0,
        },
        "adapter": {
            "license_id": "COMMERCIAL-APPROVED",
            "diagnostics": _bridge_diagnostics(),
        },
        "allowed_dwg_license_ids": ["MIT", "INTERNAL", "COMMERCIAL-APPROVED"],
        "diagnostic_errors": [],
        "records": [
            {
                "path": "sample.dwg",
                "exists": True,
                "detected_version": {"code": "AC1032"},
                "import_status": "ok",
                "entity_count": 1,
            }
        ],
    }
    path = tmp_path / "dwg-json-bridge-contract.json"
    _write_json(path, payload)
    return path


def _bridge_product_result(*, bridge_metadata: dict | None = None) -> dict:
    adapter_report = _bridge_adapter_report()
    metadata = bridge_metadata or _bridge_metadata()
    return {
        "mode": "file",
        "status": "ok",
        "result": {
            "metadata": {
                "commercial_dwg_runtime": {
                    "dwg_backend_mode": "commercial_sdk",
                    "explicit": True,
                    "implementation_status": "json_bridge_configured",
                    "license_id": "COMMERCIAL-APPROVED",
                    "allowed_license_ids": ["MIT", "INTERNAL", "COMMERCIAL-APPROVED"],
                },
                "imports": {
                    "a": {
                        "import_report": {
                            "adapter": adapter_report,
                            "metadata": {
                                "adapter_metadata": {
                                    "commercial_dwg_json_bridge": metadata,
                                }
                            },
                        }
                    },
                    "b": {
                        "import_report": {
                            "adapter": adapter_report,
                            "metadata": {
                                "adapter_metadata": {
                                    "commercial_dwg_json_bridge": metadata,
                                }
                            },
                        }
                    },
                },
            }
        },
    }


def _bridge_product_run(*, bridge_metadata: dict | None = None) -> dict:
    return {
        "mode": "file",
        "dwg_backend_mode": "commercial_sdk",
        "explicit": True,
        "implementation_status": "json_bridge_configured",
        "license_id": "COMMERCIAL-APPROVED",
        "allowed_license_ids": ["MIT", "INTERNAL", "COMMERCIAL-APPROVED"],
        "bridge_adapter_metadata": [bridge_metadata or _bridge_metadata()],
    }


def _bridge_adapter_report() -> dict:
    return {
        "name": "commercial-dwg-json-bridge",
        "version": "2026.1",
        "license_id": "COMMERCIAL-APPROVED",
        "backend_mode": "commercial_sdk",
        "implementation_status": "json_bridge_configured",
        "approval_required": True,
        "diagnostics": _bridge_diagnostics(),
    }


def _bridge_diagnostics() -> dict:
    return {
        "kind": "commercial_dwg_json_bridge",
        "command": "approved-sdk-wrapper.exe",
        "resolved_command": "C:/tools/approved-sdk-wrapper.exe",
        "command_exists": True,
        "command_sha256": "a" * 64,
        "args_template": ["{input}", "{acadver}"],
        "license_id": "COMMERCIAL-APPROVED",
        "supported_versions": list(audit.DWG_TARGET_VERSION_CODES),
        "timeout_seconds": 120,
    }


def _bridge_metadata(
    *,
    evidence_scope: str = "native_dwg_bridge",
    uses_native_dwg: bool = True,
    uses_converted_dxf: bool = False,
) -> dict:
    return {
        "adapter": "commercial-dwg-json-bridge",
        "adapter_version": "2026.1",
        "license_id": "COMMERCIAL-APPROVED",
        "backend_mode": "commercial_sdk",
        "implementation_status": "json_bridge_configured",
        "approval_required": True,
        "dwg_version": "AC1032",
        "evidence_scope": evidence_scope,
        "uses_native_dwg": uses_native_dwg,
        "uses_converted_dxf": uses_converted_dxf,
        "diagnostics": _bridge_diagnostics(),
    }


def _write_product_bridge_runner_summary(tmp_path: Path) -> Path:
    adapter_report = _bridge_adapter_report()
    bridge_metadata = _bridge_metadata()
    payload = {
        "schema_version": "dwg-product-bridge-evidence-run/v1",
        "status": "passed",
        "mode": "cad_compare",
        "command": "cad_compare",
        "entrypoint": "src.cli.cad_compare",
        "dwg_backend_mode": "commercial_sdk",
        "explicit": True,
        "customer_path": False,
        "implementation_status": "json_bridge_configured",
        "license_id": "COMMERCIAL-APPROVED",
        "allowed_license_ids": ["MIT", "INTERNAL", "COMMERCIAL-APPROVED"],
        "provenance": {
            "original_sample_pack": "sample-pack",
            "effective_output_dir": "build/reports/dwg-product-bridge-evidence",
            "effective_summary_json": "build/reports/dwg-product-bridge-evidence.json",
            "selected_dwg_backend_mode": "commercial_sdk",
        },
        "process_cleanup": {"orphan_processes": 0, "pair_timeout_seconds": 300},
        "pairs": [
            {
                "version": "AC1032",
                "status": "passed",
                "cad_compare_status": "ok",
                "exit_code": 0,
                "source_a": "before.dwg",
                "source_b": "after.dwg",
                "provenance": {
                    "original_before_dwg": "before.dwg",
                    "original_after_dwg": "after.dwg",
                    "effective_result_json": "build/reports/dwg-product-bridge-evidence/ac1032_001.json",
                    "selected_dwg_backend_mode": "commercial_sdk",
                },
                "bridge_evidence_present": True,
                "bridge_native_provenance_present": True,
                "bridge_adapter_metadata": [bridge_metadata],
                "bridge_adapter_reports": [adapter_report],
                "diagnostic_errors": [],
            }
        ],
        "bridge_adapter_reports": [adapter_report],
        "bridge_adapter_metadata": [bridge_metadata],
    }
    path = tmp_path / "dwg-product-bridge-evidence.json"
    _write_json(path, payload)
    return path


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _deep_update(target: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _failed_checks(report: dict) -> dict[str, dict]:
    return {check["name"]: check for check in report["checks"] if not check["passed"]}


def _checks(report: dict) -> dict[str, dict]:
    return {check["name"]: check for check in report["checks"]}
