from __future__ import annotations

import json
from pathlib import Path

from scripts import validate_dwg_product_release_gate as gate


def test_product_release_gate_runs_native_validation_and_release_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_run_validation(sample_pack: Path, **kwargs):
        calls["native"] = {"sample_pack": sample_pack, "kwargs": kwargs}
        for key in (
            "validation_json",
            "evidence_json",
            "bridge_contract_json",
            "product_evidence_json",
        ):
            path = kwargs[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        kwargs["audit_json"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["audit_json"].write_text(
            json.dumps(
                {
                    "status": "passed",
                    "claim_scope": "native",
                    "summary": {
                        "native_ready_versions": ["AC1032"],
                        "native_missing_versions": [],
                    },
                    "versions": [
                        {
                            "code": "AC1032",
                            "native_ready": True,
                            "native_blockers": [],
                            "native_next_actions": [],
                        }
                    ],
                    "next_actions": [],
                }
            ),
            encoding="utf-8",
        )
        kwargs["validation_md"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["validation_md"].write_text("# validation\n", encoding="utf-8")
        return {
            "status": "passed",
            "target_versions": ["AC1032"],
            "validation_status": "ok",
            "native_audit_status": "passed",
            "product_evidence_status": "passed",
            "native_ready_versions": ["AC1032"],
            "native_missing_versions": [],
            "backend_check": {
                "passed": True,
                "backend_mode": "commercial_sdk",
                "implementation_status": "json_bridge_configured",
                "license_id": "COMMERCIAL-APPROVED",
                "errors": [],
            },
            "bridge_contract": {"status": "passed"},
            "product_evidence": {"status": "passed"},
        }

    def fake_run_audit(**kwargs):
        calls["release"] = kwargs
        return {
            "status": "passed",
            "summary": {"passed": 18, "failed": 0, "hard_failed": 0, "warnings": 0},
            "checks": [{"name": "native_dwg_claim_gate", "passed": True, "detail": "ok"}],
        }

    monkeypatch.setattr(gate.native_validator, "run_validation", fake_run_validation)
    monkeypatch.setattr(gate.release_audit, "run_audit", fake_run_audit)
    inputs = _write_release_inputs(tmp_path)
    summary_json = tmp_path / "summary.json"
    release_audit_json = tmp_path / "release-audit.json"

    report = gate.run_gate(
        tmp_path / "sample-pack",
        customer_evidence_manifest=inputs["customer_manifest"],
        baseline_metrics=inputs["metrics"],
        dwg_all_version_audit=inputs["fallback_audit"],
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        bridge_command="dwg-wrapper",
        bridge_args_json='["{input}", "{acadver}"]',
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        validation_json=tmp_path / "validation.json",
        validation_md=tmp_path / "validation.md",
        evidence_json=tmp_path / "evidence.json",
        native_audit_json=tmp_path / "native-audit.json",
        bridge_contract_json=tmp_path / "bridge-contract.json",
        product_evidence_json=tmp_path / "product-evidence.json",
        product_evidence_output_dir=tmp_path / "product-evidence",
        release_audit_json=release_audit_json,
        summary_json=summary_json,
        only_versions={"AC1032"},
    )

    native_kwargs = calls["native"]["kwargs"]
    release_kwargs = calls["release"]
    assert report["status"] == "passed"
    assert report["native_validation"]["native_ready_versions"] == ["AC1032"]
    assert report["native_audit_matrix"]["status"] == "passed"
    assert report["native_audit_matrix"]["blocked_versions"] == []
    assert report["next_actions"] == []
    assert report["release_audit"]["status"] == "passed"
    assert json.loads(summary_json.read_text(encoding="utf-8"))["status"] == "passed"
    assert json.loads(release_audit_json.read_text(encoding="utf-8"))["status"] == "passed"
    assert native_kwargs["product_evidence_json"] == tmp_path / "product-evidence.json"
    assert native_kwargs["bridge_contract_json"] == tmp_path / "bridge-contract.json"
    assert native_kwargs["allowed_dwg_license_ids"] == (
        "MIT",
        "INTERNAL",
        "COMMERCIAL-APPROVED",
    )
    assert release_kwargs["result_json"] == tmp_path / "product-evidence.json"
    assert release_kwargs["run_manifest"] == tmp_path / "product-evidence.json"
    assert release_kwargs["native_dwg_audit"] == tmp_path / "native-audit.json"
    assert release_kwargs["dwg_json_bridge_contract"] == tmp_path / "bridge-contract.json"
    assert release_kwargs["require_native_dwg"] is True


def test_product_release_gate_skips_release_audit_when_native_validation_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run_validation(sample_pack: Path, **kwargs):
        kwargs["audit_json"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["audit_json"].write_text(
            json.dumps(
                {
                    "status": "failed",
                    "claim_scope": "native",
                    "summary": {
                        "native_ready_versions": [],
                        "native_missing_versions": ["AC1032"],
                    },
                    "versions": [
                        {
                            "code": "AC1032",
                            "native_ready": False,
                            "native_blockers": ["native_supported=false"],
                            "native_next_actions": [
                                {
                                    "code": "AC1032",
                                    "scope": "native",
                                    "priority": "P2",
                                    "action": "implement_or_license_native_backend",
                                }
                            ],
                        }
                    ],
                    "next_actions": [
                        {
                            "code": "AC1032",
                            "scope": "native",
                            "priority": "P2",
                            "action": "implement_or_license_native_backend",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "failed",
            "target_versions": ["AC1032"],
            "native_ready_versions": [],
            "native_missing_versions": ["AC1032"],
            "backend_check": {"passed": False, "errors": ["adapter_unavailable"]},
            "bridge_contract": {"status": "skipped"},
            "product_evidence": {"status": "skipped"},
        }

    def fail_release_audit(**kwargs):
        raise AssertionError("release audit should be skipped")

    monkeypatch.setattr(gate.native_validator, "run_validation", fake_run_validation)
    monkeypatch.setattr(gate.release_audit, "run_audit", fail_release_audit)
    inputs = _write_release_inputs(tmp_path)
    release_audit_json = tmp_path / "release-audit.json"

    report = gate.run_gate(
        tmp_path / "sample-pack",
        customer_evidence_manifest=inputs["customer_manifest"],
        baseline_metrics=inputs["metrics"],
        dwg_all_version_audit=inputs["fallback_audit"],
        bridge_command="dwg-wrapper",
        bridge_args_json='["{input}", "{acadver}"]',
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        release_audit_json=release_audit_json,
        summary_json=tmp_path / "summary.json",
    )

    skipped = json.loads(release_audit_json.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["release_audit"]["status"] == "skipped"
    assert report["native_audit_matrix"]["blocked_versions"][0]["code"] == "AC1032"
    assert report["native_audit_matrix"]["blocked_versions"][0]["native_blockers"] == ["native_supported=false"]
    assert report["next_actions"][0]["action"] == "configure_approved_dwg_bridge"
    assert report["next_actions"][1]["action"] == "make_native_validation_pass"
    assert report["next_actions"][2]["action"] == "implement_or_license_native_backend"
    assert skipped["reason"] == "native_validation_failed"


def test_product_release_gate_reconciles_native_blockers_with_fallback_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run_validation(sample_pack: Path, **kwargs):
        kwargs["audit_json"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["audit_json"].write_text(
            json.dumps(
                {
                    "status": "failed",
                    "claim_scope": "native",
                    "summary": {
                        "native_ready_versions": [],
                        "native_missing_versions": ["AC1032"],
                    },
                    "versions": [
                        {
                            "code": "AC1032",
                            "native_ready": False,
                            "native_blockers": [
                                "sample_count=0/2",
                                "real_pair_count=0/2",
                                "converted_dxf_baseline_count=0/2",
                                "native_supported=false",
                                "native_baseline_count=0/2",
                            ],
                            "native_next_actions": [],
                        }
                    ],
                    "next_actions": [
                        {
                            "code": "AC1032",
                            "scope": "native",
                            "priority": "P1",
                            "action": "collect_native_gate_samples",
                        },
                        {
                            "code": "AC1032",
                            "scope": "native",
                            "priority": "P1",
                            "action": "confirm_native_compare_pairs",
                        },
                        {
                            "code": "AC1032",
                            "scope": "native",
                            "priority": "P1",
                            "action": "capture_native_oracle_baselines",
                        },
                        {
                            "code": "AC1032",
                            "scope": "native",
                            "priority": "P2",
                            "action": "implement_or_license_native_backend",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "failed",
            "target_versions": ["AC1032"],
            "native_ready_versions": [],
            "native_missing_versions": ["AC1032"],
            "backend_check": {
                "passed": False,
                "backend_mode": "commercial_sdk",
                "implementation_status": "json_bridge_configured",
                "license_id": "INTERNAL",
                "errors": ["adapter_unavailable"],
            },
            "bridge_contract": {"status": "skipped"},
            "product_evidence": {"status": "skipped"},
        }

    def fail_release_audit(**kwargs):
        raise AssertionError("release audit should be skipped")

    monkeypatch.setattr(gate.native_validator, "run_validation", fake_run_validation)
    monkeypatch.setattr(gate.release_audit, "run_audit", fail_release_audit)
    inputs = _write_release_inputs(
        tmp_path,
        fallback_audit_payload={
            "status": "passed",
            "claim_scope": "fallback",
            "summary": {
                "fallback_ready_versions": ["AC1032"],
                "fallback_missing_versions": [],
                "native_ready_versions": [],
                "native_missing_versions": ["AC1032"],
            },
            "versions": [
                {
                    "code": "AC1032",
                    "sample_count": 2,
                    "real_pair_count": 2,
                    "converted_dxf_baseline_count": 2,
                    "fallback_ready": True,
                    "fallback_blockers": [],
                }
            ],
        },
    )

    report = gate.run_gate(
        tmp_path / "sample-pack",
        customer_evidence_manifest=inputs["customer_manifest"],
        baseline_metrics=inputs["metrics"],
        dwg_all_version_audit=inputs["fallback_audit"],
        bridge_command="dwg-wrapper",
        bridge_args_json='["{input}", "{acadver}"]',
        bridge_license_id="INTERNAL",
        bridge_supported_versions="AC1032",
        summary_json=tmp_path / "summary.json",
    )

    blocked = report["native_audit_matrix"]["blocked_versions"][0]
    action_names = [action["action"] for action in report["next_actions"]]
    assert blocked["fallback_evidence"]["fallback_ready"] is True
    assert blocked["effective_native_blockers"] == ["native_supported=false", "native_baseline_count=0/2"]
    assert action_names[:2] == ["configure_approved_dwg_bridge", "make_native_validation_pass"]
    assert "collect_native_gate_samples" not in action_names
    assert "confirm_native_compare_pairs" not in action_names
    assert "capture_native_oracle_baselines" not in action_names
    assert "implement_or_license_native_backend" in action_names


def test_product_release_gate_aggregates_split_sample_packs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []
    legacy_pack = _write_sample_pack(tmp_path / "legacy-pack", ("AC1009",))
    modern_pack = _write_sample_pack(tmp_path / "modern-pack", ("AC1032",))

    def fake_run_validation(sample_pack: Path, **kwargs):
        calls.append({"sample_pack": sample_pack, "kwargs": kwargs})
        version = next(iter(kwargs["only_versions"]))
        for key in ("validation_json", "evidence_json", "audit_json"):
            payload = {
                "status": "passed",
                "limits": {"compare_source": "dwg", "dwg_backend_mode": "commercial_sdk"},
                "versions": [],
            }
            kwargs[key].parent.mkdir(parents=True, exist_ok=True)
            kwargs[key].write_text(json.dumps(payload), encoding="utf-8")
        kwargs["validation_md"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["validation_md"].write_text("# validation\n", encoding="utf-8")
        kwargs["bridge_contract_json"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["bridge_contract_json"].write_text(
            json.dumps(_bridge_contract_payload(version)),
            encoding="utf-8",
        )
        kwargs["product_evidence_json"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["product_evidence_json"].write_text(
            json.dumps(_product_evidence_payload(version)),
            encoding="utf-8",
        )
        return {
            "status": "failed",
            "target_versions": [version],
            "validation_status": "passed",
            "native_audit_status": "failed",
            "product_evidence_status": "passed",
            "native_ready_versions": [],
            "native_missing_versions": [version],
            "backend_check": {
                "passed": True,
                "backend_mode": "commercial_sdk",
                "implementation_status": "json_bridge_configured",
                "license_id": "COMMERCIAL-APPROVED",
                "errors": [],
            },
            "bridge_contract": {"status": "passed"},
            "product_evidence": {"status": "passed"},
        }

    def fake_build_report(summary_paths):
        assert len(summary_paths) == 2
        return {
            "schema_version": "dwg-all-version-support-evidence/v1",
            "versions": {},
            "summary": {"source_summary_count": 2},
        }

    def fake_run_native_audit(**kwargs):
        assert kwargs["target_versions"] == ("AC1009", "AC1032")
        evidence = json.loads(kwargs["evidence_manifest"].read_text(encoding="utf-8"))
        for version in ("AC1009", "AC1032"):
            item = evidence["versions"][version]
            assert item["native_supported"] is True
            assert item["native_baseline_count"] == 1
            assert item["native_backend_modes"] == ["commercial_sdk"]
        return {
            "status": "passed",
            "claim_scope": "native",
            "target_versions": ["AC1009", "AC1032"],
            "summary": {
                "native_ready_versions": ["AC1009", "AC1032"],
                "native_missing_versions": [],
            },
            "versions": [
                {"code": "AC1009", "native_ready": True},
                {"code": "AC1032", "native_ready": True},
            ],
            "next_actions": [],
        }

    def fake_run_release_audit(**kwargs):
        product = json.loads(kwargs["result_json"].read_text(encoding="utf-8"))
        contract = json.loads(kwargs["dwg_json_bridge_contract"].read_text(encoding="utf-8"))
        assert product["status"] == "passed"
        assert product["summary"]["versions"] == ["AC1009", "AC1032"]
        assert product["process_cleanup"]["orphan_processes"] == 0
        assert product["process_cleanup"]["pair_timeout_seconds"] == 60
        assert contract["status"] == "passed"
        assert contract["summary"]["input_count"] == 2
        return {
            "status": "passed",
            "summary": {"passed": 18, "failed": 0, "hard_failed": 0, "warnings": 0},
            "checks": [],
        }

    monkeypatch.setattr(gate.native_validator, "run_validation", fake_run_validation)
    monkeypatch.setattr(gate.native_validator.evidence_builder, "build_report", fake_build_report)
    monkeypatch.setattr(gate.native_validator.native_audit, "run_audit", fake_run_native_audit)
    monkeypatch.setattr(gate.release_audit, "run_audit", fake_run_release_audit)
    inputs = _write_release_inputs(tmp_path)

    report = gate.run_gate(
        legacy_pack,
        extra_sample_packs=(modern_pack,),
        customer_evidence_manifest=inputs["customer_manifest"],
        baseline_metrics=inputs["metrics"],
        dwg_all_version_audit=inputs["fallback_audit"],
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        bridge_command="dwg-wrapper",
        bridge_args_json='["{input}", "{acadver}"]',
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1009,AC1032",
        validation_json=tmp_path / "combined-validation.json",
        validation_md=tmp_path / "combined-validation.md",
        evidence_json=tmp_path / "combined-evidence.json",
        native_audit_json=tmp_path / "combined-native-audit.json",
        bridge_contract_json=tmp_path / "combined-bridge-contract.json",
        product_evidence_json=tmp_path / "combined-product-evidence.json",
        product_evidence_output_dir=tmp_path / "combined-product-evidence",
        release_audit_json=tmp_path / "release-audit.json",
        summary_json=tmp_path / "summary.json",
        only_versions={"AC1009", "AC1032"},
    )

    assert report["status"] == "passed"
    assert [call["sample_pack"] for call in calls] == [legacy_pack.resolve(), modern_pack.resolve()]
    assert {next(iter(call["kwargs"]["only_versions"])) for call in calls} == {"AC1009", "AC1032"}
    assert report["sample_packs"] == [str(legacy_pack.resolve()), str(modern_pack.resolve())]
    assert report["native_validation"]["native_ready_versions"] == ["AC1009", "AC1032"]
    combined_validation = json.loads((tmp_path / "combined-validation.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in combined_validation["pack_reports"]] == ["passed", "passed"]
    assert [item["native_run_status"] for item in combined_validation["pack_reports"]] == ["failed", "failed"]
    combined_product = json.loads((tmp_path / "combined-product-evidence.json").read_text(encoding="utf-8"))
    assert combined_product["process_cleanup"]["orphan_processes"] == 0


def test_product_release_gate_cli_accepts_required_options() -> None:
    args = gate.parse_args(
        [
            "sample-pack",
            "--extra-sample-pack",
            "modern-pack",
            "--customer-evidence-manifest",
            "customer.json",
            "--baseline-metrics",
            "metrics.json",
            "--dwg-all-version-audit",
            "fallback-audit.json",
            "--dwg-allowed-license-id",
            "COMMERCIAL-APPROVED",
            "--bridge-command",
            "dwg-wrapper",
            "--bridge-args-json",
            '["{input}", "{acadver}"]',
            "--bridge-license-id",
            "COMMERCIAL-APPROVED",
            "--bridge-supported-versions",
            "AC1032",
            "--product-evidence-json",
            "product-evidence.json",
            "--release-audit-json",
            "release-audit.json",
            "--summary-json",
            "summary.json",
        ]
    )

    assert args.sample_pack == Path("sample-pack")
    assert args.extra_sample_pack == [Path("modern-pack")]
    assert args.customer_evidence_manifest == Path("customer.json")
    assert args.baseline_metrics == Path("metrics.json")
    assert args.dwg_all_version_audit == Path("fallback-audit.json")
    assert args.dwg_allowed_license_id == ["COMMERCIAL-APPROVED"]
    assert args.bridge_command == "dwg-wrapper"
    assert args.bridge_license_id == "COMMERCIAL-APPROVED"
    assert args.product_evidence_json == Path("product-evidence.json")
    assert args.release_audit_json == Path("release-audit.json")
    assert args.summary_json == Path("summary.json")


def _write_release_inputs(tmp_path: Path, *, fallback_audit_payload: dict[str, object] | None = None) -> dict[str, Path]:
    customer_manifest = tmp_path / "customer.json"
    metrics = tmp_path / "metrics.json"
    fallback_audit = tmp_path / "fallback-audit.json"
    customer_manifest.write_text("{}", encoding="utf-8")
    metrics.write_text("{}", encoding="utf-8")
    fallback_audit.write_text(json.dumps(fallback_audit_payload or {}), encoding="utf-8")
    return {
        "customer_manifest": customer_manifest,
        "metrics": metrics,
        "fallback_audit": fallback_audit,
    }


def _write_sample_pack(path: Path, versions: tuple[str, ...]) -> Path:
    path.mkdir()
    manifest = {
        "versions": {
            version: {
                "sample_before_dwg": f"{version.lower()}_before.dwg",
                "sample_after_dwg": f"{version.lower()}_after.dwg",
            }
            for version in versions
        }
    }
    for version in versions:
        (path / f"{version.lower()}_before.dwg").write_text(version, encoding="ascii")
        (path / f"{version.lower()}_after.dwg").write_text(version, encoding="ascii")
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _bridge_contract_payload(version: str) -> dict[str, object]:
    return {
        "schema_version": "dwg-json-bridge-contract-validation/v1",
        "status": "passed",
        "summary": {
            "input_count": 1,
            "accepted_import_count": 1,
            "failed_import_count": 0,
            "missing_input_count": 0,
            "diagnostic_error_count": 0,
        },
        "adapter": {
            "backend_mode": "commercial_sdk",
            "implementation_status": "json_bridge_configured",
            "license_id": "COMMERCIAL-APPROVED",
            "diagnostics": {
                "kind": "commercial_dwg_json_bridge",
                "command_exists": True,
                "command_sha256": "abc",
                "supported_versions": [version],
                "license_id": "COMMERCIAL-APPROVED",
            },
        },
        "allowed_dwg_license_ids": ["MIT", "INTERNAL", "COMMERCIAL-APPROVED"],
        "diagnostic_errors": [],
        "records": [{"exists": True, "import_status": "ok", "version": version}],
    }


def _product_evidence_payload(version: str) -> dict[str, object]:
    return {
        "schema_version": "dwg-product-bridge-evidence-run/v1",
        "status": "passed",
        "mode": "cad_compare",
        "command": "cad_compare",
        "dwg_backend_mode": "commercial_sdk",
        "explicit": True,
        "customer_path": False,
        "implementation_status": "json_bridge_configured",
        "license_id": "COMMERCIAL-APPROVED",
        "allowed_license_ids": ["MIT", "INTERNAL", "COMMERCIAL-APPROVED"],
        "summary": {"versions": [version], "pair_count": 1},
        "process_cleanup": {"orphan_processes": 0, "pair_timeout_seconds": 60},
        "diagnostic_errors": [],
        "pairs": [
            {
                "version": version,
                "status": "passed",
                "bridge_evidence_present": True,
                "bridge_native_provenance_present": True,
                "source_a": f"{version}_before.dwg",
                "source_b": f"{version}_after.dwg",
                "bridge_adapter_metadata": [
                    {
                        "backend_mode": "commercial_sdk",
                        "evidence_scope": "native_dwg_bridge",
                        "uses_native_dwg": True,
                        "uses_converted_dxf": False,
                    }
                ],
            }
        ],
        "bridge_adapter_reports": [
            {
                "backend_mode": "commercial_sdk",
                "implementation_status": "json_bridge_configured",
                "license_id": "COMMERCIAL-APPROVED",
                "diagnostics": {
                    "kind": "commercial_dwg_json_bridge",
                    "command_exists": True,
                    "command_sha256": "abc",
                    "supported_versions": [version],
                    "license_id": "COMMERCIAL-APPROVED",
                },
            }
        ],
    }
