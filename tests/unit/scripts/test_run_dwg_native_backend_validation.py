from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts import validate_dwg_native_backend as runner
from src.services.comparison.commercial_dwg_json_adapter import (
    ARGS_JSON_ENV,
    COMMAND_ENV,
    LICENSE_ID_ENV,
    SUPPORTED_VERSIONS_ENV,
)
from src.services.comparison.dwg_backend import COMMERCIAL_SDK_ADAPTER_ENV


def test_native_backend_runner_fails_closed_without_commercial_adapter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(COMMERCIAL_SDK_ADAPTER_ENV, raising=False)

    report = runner.run_validation(
        tmp_path / "sample-pack",
        validation_json=tmp_path / "validation.json",
        validation_md=tmp_path / "validation.md",
        evidence_json=tmp_path / "evidence.json",
        audit_json=tmp_path / "native-audit.json",
    )

    validation = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    audit = json.loads((tmp_path / "native-audit.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["backend_check"]["passed"] is False
    assert "adapter_unavailable" in report["backend_check"]["errors"]
    assert validation["status"] == "failed"
    assert audit["status"] == "failed"


def test_native_backend_runner_builds_native_audit_with_approved_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_approved_adapter(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    calls = []

    def fake_build_report(sample_pack: Path, **kwargs):
        calls.append((sample_pack, kwargs))
        return {
            "schema_version": "adr004-version-sample-pack-validation/v1",
            "status": "ok",
            "sample_pack": str(sample_pack),
            "limits": {
                "compare_source": kwargs["compare_source"],
                "dwg_backend_mode": kwargs["dwg_backend_mode"],
                "allowed_dwg_license_ids": list(kwargs["allowed_dwg_license_ids"]),
            },
            "summary": {
                "version_count": 2,
                "manifest_error_count": 0,
                "validation_error_count": 0,
                "header_mismatch_count": 0,
                "import_status_counts": {"ok": 4},
                "compare_status_counts": {"ok": 2},
            },
            "manifest_errors": [],
            "validation_errors": [],
            "versions": [
                _native_record("AC1032", "before-a.dwg", "after-a.dwg"),
                _native_record("AC1032", "before-b.dwg", "after-b.dwg"),
            ],
        }

    monkeypatch.setattr(runner.validate_sample_pack, "build_report", fake_build_report)
    monkeypatch.setattr(runner.validate_sample_pack, "render_markdown", lambda report: "# validation\n")

    report = runner.run_validation(
        tmp_path / "sample-pack",
        adapter_spec="approved_runner_adapter:create_adapter",
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        validation_json=tmp_path / "validation.json",
        validation_md=tmp_path / "validation.md",
        evidence_json=tmp_path / "evidence.json",
        audit_json=tmp_path / "native-audit.json",
        only_versions={"AC1032"},
    )

    evidence = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    audit = json.loads((tmp_path / "native-audit.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["backend_check"]["license_id"] == "COMMERCIAL-APPROVED"
    assert report["native_ready_versions"] == ["AC1032"]
    assert evidence["versions"]["AC1032"]["native_baseline_count"] == 2
    assert audit["status"] == "passed"
    assert audit["summary"]["native_missing_versions"] == []
    assert calls[0][1]["compare_source"] == "dwg"
    assert calls[0][1]["dwg_backend_mode"] == "commercial_sdk"
    assert calls[0][1]["allowed_dwg_license_ids"] == (
        "MIT",
        "INTERNAL",
        "COMMERCIAL-APPROVED",
    )


def test_native_backend_runner_records_json_bridge_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(COMMAND_ENV, sys.executable)
    monkeypatch.setenv(LICENSE_ID_ENV, "COMMERCIAL-APPROVED")
    monkeypatch.setenv(SUPPORTED_VERSIONS_ENV, "AC1032")

    def fake_build_report(sample_pack: Path, **kwargs):
        return {
            "schema_version": "adr004-version-sample-pack-validation/v1",
            "status": "ok",
            "sample_pack": str(sample_pack),
            "limits": {
                "compare_source": kwargs["compare_source"],
                "dwg_backend_mode": kwargs["dwg_backend_mode"],
                "allowed_dwg_license_ids": list(kwargs["allowed_dwg_license_ids"]),
            },
            "summary": {
                "version_count": 2,
                "manifest_error_count": 0,
                "validation_error_count": 0,
                "header_mismatch_count": 0,
                "import_status_counts": {"ok": 4},
                "compare_status_counts": {"ok": 2},
            },
            "manifest_errors": [],
            "validation_errors": [],
            "versions": [
                _native_record("AC1032", "before-a.dwg", "after-a.dwg"),
                _native_record("AC1032", "before-b.dwg", "after-b.dwg"),
            ],
        }

    monkeypatch.setattr(runner.validate_sample_pack, "build_report", fake_build_report)
    monkeypatch.setattr(runner.validate_sample_pack, "render_markdown", lambda report: "# validation\n")

    report = runner.run_validation(
        tmp_path / "sample-pack",
        adapter_spec="src.services.comparison.commercial_dwg_json_adapter:create_adapter",
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        validation_json=tmp_path / "validation.json",
        validation_md=tmp_path / "validation.md",
        evidence_json=tmp_path / "evidence.json",
        audit_json=tmp_path / "native-audit.json",
        only_versions={"AC1032"},
    )

    validation = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    diagnostics = validation["backend_check"]["adapter_diagnostics"]
    assert report["status"] == "passed"
    assert diagnostics["kind"] == "commercial_dwg_json_bridge"
    assert diagnostics["command_exists"] is True
    assert diagnostics["command_sha256"]
    assert diagnostics["supported_versions"] == ["AC1032"]


def test_native_backend_runner_writes_json_bridge_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sample_pack = _write_sample_pack(tmp_path)
    bridge = _write_bridge(tmp_path)
    monkeypatch.delenv(COMMAND_ENV, raising=False)
    monkeypatch.delenv(ARGS_JSON_ENV, raising=False)
    monkeypatch.delenv(LICENSE_ID_ENV, raising=False)
    monkeypatch.delenv(SUPPORTED_VERSIONS_ENV, raising=False)

    def fake_build_report(sample_pack: Path, **kwargs):
        return {
            "schema_version": "adr004-version-sample-pack-validation/v1",
            "status": "ok",
            "sample_pack": str(sample_pack),
            "limits": {
                "compare_source": kwargs["compare_source"],
                "dwg_backend_mode": kwargs["dwg_backend_mode"],
                "allowed_dwg_license_ids": list(kwargs["allowed_dwg_license_ids"]),
            },
            "summary": {
                "version_count": 2,
                "manifest_error_count": 0,
                "validation_error_count": 0,
                "header_mismatch_count": 0,
                "import_status_counts": {"ok": 4},
                "compare_status_counts": {"ok": 2},
            },
            "manifest_errors": [],
            "validation_errors": [],
            "versions": [
                _native_record("AC1032", "before-a.dwg", "after-a.dwg"),
                _native_record("AC1032", "before-b.dwg", "after-b.dwg"),
            ],
        }

    monkeypatch.setattr(runner.validate_sample_pack, "build_report", fake_build_report)
    monkeypatch.setattr(runner.validate_sample_pack, "render_markdown", lambda report: "# validation\n")
    contract_json = tmp_path / "bridge-contract.json"

    report = runner.run_validation(
        sample_pack,
        adapter_spec="src.services.comparison.commercial_dwg_json_adapter:create_adapter",
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        validation_json=tmp_path / "validation.json",
        validation_md=tmp_path / "validation.md",
        evidence_json=tmp_path / "evidence.json",
        audit_json=tmp_path / "native-audit.json",
        bridge_contract_json=contract_json,
        bridge_command=sys.executable,
        bridge_args_json=json.dumps([str(bridge), "{input}", "{acadver}"]),
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        only_versions={"AC1032"},
    )

    contract = json.loads(contract_json.read_text(encoding="utf-8"))
    validation = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["bridge_contract"]["status"] == "passed"
    assert report["paths"]["bridge_contract_json"] == str(contract_json)
    assert contract["summary"]["input_count"] == 2
    assert contract["summary"]["accepted_import_count"] == 2
    assert validation["bridge_contract"]["path"] == str(contract_json)


def test_native_backend_runner_writes_product_bridge_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sample_pack = _write_sample_pack(tmp_path)
    bridge = _write_bridge(tmp_path)
    monkeypatch.delenv(COMMAND_ENV, raising=False)
    monkeypatch.delenv(ARGS_JSON_ENV, raising=False)
    monkeypatch.delenv(LICENSE_ID_ENV, raising=False)
    monkeypatch.delenv(SUPPORTED_VERSIONS_ENV, raising=False)

    def fake_build_report(sample_pack: Path, **kwargs):
        return {
            "schema_version": "adr004-version-sample-pack-validation/v1",
            "status": "ok",
            "sample_pack": str(sample_pack),
            "limits": {
                "compare_source": kwargs["compare_source"],
                "dwg_backend_mode": kwargs["dwg_backend_mode"],
                "allowed_dwg_license_ids": list(kwargs["allowed_dwg_license_ids"]),
            },
            "summary": {
                "version_count": 2,
                "manifest_error_count": 0,
                "validation_error_count": 0,
                "header_mismatch_count": 0,
                "import_status_counts": {"ok": 4},
                "compare_status_counts": {"ok": 2},
            },
            "manifest_errors": [],
            "validation_errors": [],
            "versions": [
                _native_record("AC1032", "before-a.dwg", "after-a.dwg"),
                _native_record("AC1032", "before-b.dwg", "after-b.dwg"),
            ],
        }

    monkeypatch.setattr(runner.validate_sample_pack, "build_report", fake_build_report)
    monkeypatch.setattr(runner.validate_sample_pack, "render_markdown", lambda report: "# validation\n")
    product_json = tmp_path / "product-evidence.json"

    report = runner.run_validation(
        sample_pack,
        adapter_spec="src.services.comparison.commercial_dwg_json_adapter:create_adapter",
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        validation_json=tmp_path / "validation.json",
        validation_md=tmp_path / "validation.md",
        evidence_json=tmp_path / "evidence.json",
        audit_json=tmp_path / "native-audit.json",
        bridge_contract_json=tmp_path / "bridge-contract.json",
        bridge_command=sys.executable,
        bridge_args_json=json.dumps([str(bridge), "{input}", "{acadver}"]),
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        product_evidence_json=product_json,
        product_evidence_output_dir=tmp_path / "product-evidence",
        product_pair_timeout_seconds=60,
        only_versions={"AC1032"},
    )

    product = json.loads(product_json.read_text(encoding="utf-8"))
    validation = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    adapter = product["bridge_adapter_reports"][0]
    assert report["status"] == "passed"
    assert report["product_evidence"]["status"] == "passed"
    assert report["product_evidence_status"] == "passed"
    assert report["paths"]["product_evidence_json"] == str(product_json)
    assert product["status"] == "passed"
    assert product["mode"] == "cad_compare"
    assert product["summary"]["bridge_evidence_pair_count"] == 1
    assert adapter["diagnostics"]["kind"] == "commercial_dwg_json_bridge"
    assert validation["product_evidence"]["path"] == str(product_json)


def test_native_backend_runner_cli_accepts_bridge_options() -> None:
    args = runner.parse_args(
        [
            "sample-pack",
            "--adapter-spec",
            "src.services.comparison.commercial_dwg_json_adapter:create_adapter",
            "--dwg-allowed-license-id",
            "COMMERCIAL-APPROVED",
            "--bridge-contract-json",
            "bridge-contract.json",
            "--bridge-command",
            "dwg-wrapper",
            "--bridge-args-json",
            '["{input}","{acadver}"]',
            "--bridge-license-id",
            "COMMERCIAL-APPROVED",
            "--bridge-supported-versions",
            "AC1032",
            "--bridge-timeout-seconds",
            "30",
            "--product-evidence-json",
            "product-evidence.json",
            "--product-evidence-output-dir",
            "product-evidence",
            "--product-pair-timeout-seconds",
            "60",
            "--product-max-pairs-per-version",
            "1",
        ]
    )

    assert args.bridge_contract_json == Path("bridge-contract.json")
    assert args.bridge_command == "dwg-wrapper"
    assert args.bridge_args_json == '["{input}","{acadver}"]'
    assert args.bridge_license_id == "COMMERCIAL-APPROVED"
    assert args.bridge_supported_versions == "AC1032"
    assert args.bridge_timeout_seconds == 30
    assert args.product_evidence_json == Path("product-evidence.json")
    assert args.product_evidence_output_dir == Path("product-evidence")
    assert args.product_pair_timeout_seconds == 60
    assert args.product_max_pairs_per_version == 1


def test_native_backend_runner_records_adapter_availability_probe_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_broken_adapter(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    report = runner.run_validation(
        tmp_path / "sample-pack",
        adapter_spec="broken_runner_adapter:create_adapter",
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        validation_json=tmp_path / "validation.json",
        validation_md=tmp_path / "validation.md",
        evidence_json=tmp_path / "evidence.json",
        audit_json=tmp_path / "native-audit.json",
    )

    assert report["status"] == "failed"
    assert "adapter_availability_check_failed" in report["backend_check"]["errors"]
    assert "RuntimeError: license probe failed" == report["backend_check"]["availability_error"]


def test_native_backend_runner_fails_before_validation_when_adapter_misses_target_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_limited_adapter(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    calls = []

    def fake_build_report(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ok", "versions": []}

    monkeypatch.setattr(runner.validate_sample_pack, "build_report", fake_build_report)

    report = runner.run_validation(
        tmp_path / "sample-pack",
        adapter_spec="limited_runner_adapter:create_adapter",
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        validation_json=tmp_path / "validation.json",
        validation_md=tmp_path / "validation.md",
        evidence_json=tmp_path / "evidence.json",
        audit_json=tmp_path / "native-audit.json",
        only_versions={"AC1032"},
    )

    validation = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert calls == []
    assert "adapter_missing_required_versions" in report["backend_check"]["errors"]
    assert report["backend_check"]["unsupported_required_versions"] == ["AC1032"]
    assert validation["status"] == "failed"


def _write_approved_adapter(tmp_path: Path) -> None:
    plugin = tmp_path / "approved_runner_adapter.py"
    plugin.write_text(
        "\n".join(
            [
                "from src.services.comparison.dwg_backend import DWG_BACKEND_COMMERCIAL_SDK",
                "from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter",
                "",
                "class ApprovedRunnerAdapter(DwgJsonFixtureAdapter):",
                "    name = 'approved-runner-fixture'",
                "    version = '2026.1'",
                "    license_id = 'COMMERCIAL-APPROVED'",
                "    backend_mode = DWG_BACKEND_COMMERCIAL_SDK",
                "    implementation_status = 'approved_plugin'",
                "    approval_required = True",
                "",
                "    def supports_version(self, version):",
                "        return True",
                "",
                "def create_adapter():",
                "    return ApprovedRunnerAdapter()",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_limited_adapter(tmp_path: Path) -> None:
    plugin = tmp_path / "limited_runner_adapter.py"
    plugin.write_text(
        "\n".join(
            [
                "from src.services.comparison.dwg_backend import DWG_BACKEND_COMMERCIAL_SDK",
                "from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter",
                "",
                "class LimitedRunnerAdapter(DwgJsonFixtureAdapter):",
                "    name = 'limited-runner-fixture'",
                "    version = '2026.1'",
                "    license_id = 'COMMERCIAL-APPROVED'",
                "    backend_mode = DWG_BACKEND_COMMERCIAL_SDK",
                "    implementation_status = 'approved_plugin'",
                "    approval_required = True",
                "",
                "    def supports_version(self, version):",
                "        return version.code == 'AC1015'",
                "",
                "def create_adapter():",
                "    return LimitedRunnerAdapter()",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_broken_adapter(tmp_path: Path) -> None:
    plugin = tmp_path / "broken_runner_adapter.py"
    plugin.write_text(
        "\n".join(
            [
                "from src.services.comparison.dwg_backend import DWG_BACKEND_COMMERCIAL_SDK",
                "from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter",
                "",
                "class BrokenRunnerAdapter(DwgJsonFixtureAdapter):",
                "    name = 'broken-runner-fixture'",
                "    version = '2026.1'",
                "    license_id = 'COMMERCIAL-APPROVED'",
                "    backend_mode = DWG_BACKEND_COMMERCIAL_SDK",
                "    implementation_status = 'approved_plugin'",
                "    approval_required = True",
                "",
                "    def is_available(self):",
                "        raise RuntimeError('license probe failed')",
                "",
                "def create_adapter():",
                "    return BrokenRunnerAdapter()",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_sample_pack(tmp_path: Path) -> Path:
    sample_pack = tmp_path / "sample-pack"
    sample_pack.mkdir()
    before = sample_pack / "before.dwg"
    after = sample_pack / "after.dwg"
    before.write_bytes(b"AC1032 before")
    after.write_bytes(b"AC1032 after")
    (sample_pack / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "versions": {
                    "AC1032": {
                        "dwg_code": "AC1032",
                        "sample_before_dwg": str(before),
                        "sample_after_dwg": str(after),
                        "outputs": {
                            "before": [
                                {"path": str(sample_pack / "before.dxf"), "size": 0, "sha256": "", "acadver": "AC1032"}
                            ],
                            "after": [
                                {"path": str(sample_pack / "after.dxf"), "size": 0, "sha256": "", "acadver": "AC1032"}
                            ],
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return sample_pack


def _write_bridge(tmp_path: Path) -> Path:
    bridge = tmp_path / "dwg_json_bridge.py"
    bridge.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "",
                "path = sys.argv[1]",
                "acadver = sys.argv[2]",
                "print(json.dumps({",
                "    'schema_version': 'dwg-adapter-drawing-json/v1',",
                "    'drawing': {",
                "        'header': {'$ACADVER': acadver},",
                "        'layers': [{'name': 'A-WALL'}],",
                "        'entities': [",
                "            {",
                "                'type': 'LINE',",
                "                'layer': 'A-WALL',",
                "                'handle': '10',",
                "                'geometry': {'start': [0, 0, 0], 'end': [1, 1, 0]},",
                "            }",
                "        ],",
                "        'metadata': {",
                "            'source_path': path,",
                "            'commercial_dwg_json_bridge': {",
                "                'evidence_scope': 'native_dwg_bridge',",
                "                'uses_native_dwg': True,",
                "                'uses_converted_dxf': False,",
                "            },",
                "        },",
                "    },",
                "}))",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return bridge


def _native_record(code: str, before: str, after: str) -> dict:
    return {
        "version": code,
        "pair_kind": "confirmed_revision_pair",
        "dwg_inputs": {
            "before": {
                "path": before,
                "exists": True,
                "detected_header": code,
                "header_matches_version": True,
            },
            "after": {
                "path": after,
                "exists": True,
                "detected_header": code,
                "header_matches_version": True,
            },
        },
        "outputs": {
            "before": [
                {
                    "exists": True,
                    "detected_acadver": code,
                    "header_matches_expected": True,
                }
            ],
            "after": [
                {
                    "exists": True,
                    "detected_acadver": code,
                    "header_matches_expected": True,
                }
            ],
        },
        "imports": {
            "before": {"status": "ok"},
            "after": {"status": "ok"},
        },
        "compare": {
            "status": "ok",
            "imports": {"a": {"status": "ok"}, "b": {"status": "ok"}},
        },
        "validation_errors": [],
    }
