from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from scripts import run_dwg_product_bridge_evidence as runner


def test_product_bridge_evidence_runner_runs_cad_compare_with_json_bridge(tmp_path: Path) -> None:
    sample_pack = _write_sample_pack(tmp_path)
    bridge = _write_bridge(tmp_path)
    summary_json = tmp_path / "summary.json"

    report = runner.run_evidence(
        sample_pack,
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        bridge_command=sys.executable,
        bridge_args_json=json.dumps([str(bridge), "{input}", "{acadver}"]),
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        bridge_timeout_seconds=30,
        pair_timeout_seconds=60,
        output_dir=tmp_path / "product-evidence",
        summary_json=summary_json,
        only_versions={"AC1032"},
    )

    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    pair = report["pairs"][0]
    adapter = report["bridge_adapter_reports"][0]
    assert report["status"] == "passed"
    assert summary["status"] == "passed"
    assert report["summary"]["pair_count"] == 1
    assert pair["exit_code"] == 0
    assert pair["cad_compare_status"] in {"ok", "partial"}
    assert pair["bridge_evidence_present"] is True
    assert Path(pair["output_json"]).exists()
    assert adapter["backend_mode"] == "commercial_sdk"
    assert adapter["license_id"] == "COMMERCIAL-APPROVED"
    assert adapter["implementation_status"] == "json_bridge_configured"
    assert adapter["diagnostics"]["kind"] == "commercial_dwg_json_bridge"
    assert adapter["diagnostics"]["command_exists"] is True
    assert adapter["diagnostics"]["command_sha256"]
    assert adapter["diagnostics"]["supported_versions"] == ["AC1032"]
    assert pair["bridge_native_provenance_present"] is True
    assert pair["bridge_adapter_metadata"][0]["evidence_scope"] == "native_dwg_bridge"
    assert pair["bridge_adapter_metadata"][0]["uses_native_dwg"] is True


def test_product_bridge_evidence_runner_fails_without_bridge_config(tmp_path: Path) -> None:
    sample_pack = _write_sample_pack(tmp_path)
    summary_json = tmp_path / "summary.json"

    report = runner.run_evidence(
        sample_pack,
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        output_dir=tmp_path / "product-evidence",
        summary_json=summary_json,
        only_versions={"AC1032"},
    )

    assert report["status"] == "failed"
    assert report["summary"]["executed_pair_count"] == 0
    assert "bridge_command_missing" in report["diagnostic_errors"]
    assert "bridge_args_json_missing" in report["diagnostic_errors"]
    assert "bridge_license_id_missing" in report["diagnostic_errors"]
    assert "bridge_supported_versions_missing" in report["diagnostic_errors"]
    assert json.loads(summary_json.read_text(encoding="utf-8"))["status"] == "failed"


def test_product_bridge_evidence_runner_rejects_converted_dxf_bridge_as_native(tmp_path: Path) -> None:
    sample_pack = _write_sample_pack(tmp_path)
    bridge = _write_bridge(tmp_path, evidence_scope="converted_dxf_bridge", uses_native_dwg=False, uses_converted_dxf=True)
    summary_json = tmp_path / "summary.json"

    report = runner.run_evidence(
        sample_pack,
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        bridge_command=sys.executable,
        bridge_args_json=json.dumps([str(bridge), "{input}", "{acadver}"]),
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        bridge_timeout_seconds=30,
        pair_timeout_seconds=60,
        output_dir=tmp_path / "product-evidence",
        summary_json=summary_json,
        only_versions={"AC1032"},
    )

    pair = report["pairs"][0]
    assert report["status"] == "failed"
    assert pair["bridge_native_provenance_present"] is False
    assert "bridge_metadata[0].uses_converted_dxf=true" in pair["diagnostic_errors"]
    assert "bridge_metadata[0].native_evidence_scope=converted_dxf_bridge" in pair["diagnostic_errors"]


def test_product_bridge_evidence_cli_writes_summary(tmp_path: Path) -> None:
    sample_pack = _write_sample_pack(tmp_path)
    bridge = _write_bridge(tmp_path)
    summary_json = tmp_path / "summary.json"

    with patch("builtins.print"):
        exit_code = runner.main(
            [
                str(sample_pack),
                "--dwg-allowed-license-id",
                "COMMERCIAL-APPROVED",
                "--bridge-command",
                sys.executable,
                "--bridge-args-json",
                json.dumps([str(bridge), "{input}", "{acadver}"]),
                "--bridge-license-id",
                "COMMERCIAL-APPROVED",
                "--bridge-supported-versions",
                "AC1032",
                "--bridge-timeout-seconds",
                "30",
                "--pair-timeout-seconds",
                "60",
                "--output-dir",
                str(tmp_path / "product-evidence"),
                "--summary-json",
                str(summary_json),
                "--version",
                "AC1032",
            ]
        )

    report = json.loads(summary_json.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["mode"] == "cad_compare"
    assert report["dwg_backend_mode"] == "commercial_sdk"
    assert report["summary"]["bridge_evidence_pair_count"] == 1


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
                                {
                                    "path": str(sample_pack / "before.dxf"),
                                    "size": 0,
                                    "sha256": "",
                                    "acadver": "AC1032",
                                }
                            ],
                            "after": [
                                {
                                    "path": str(sample_pack / "after.dxf"),
                                    "size": 0,
                                    "sha256": "",
                                    "acadver": "AC1032",
                                }
                            ],
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return sample_pack


def _write_bridge(
    tmp_path: Path,
    *,
    evidence_scope: str = "native_dwg_bridge",
    uses_native_dwg: bool = True,
    uses_converted_dxf: bool = False,
) -> Path:
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
                "        'layers': [{'name': '0'}],",
                "        'entities': [",
                "            {",
                "                'type': 'LINE',",
                "                'layer': '0',",
                "                'handle': '10',",
                "                'geometry': {'start': [0, 0, 0], 'end': [100, 0, 0]},",
                "            }",
                "        ],",
                "        'metadata': {",
                "            'source_path': path,",
                "            'commercial_dwg_json_bridge': {",
                f"                'evidence_scope': {evidence_scope!r},",
                f"                'uses_native_dwg': {uses_native_dwg!r},",
                f"                'uses_converted_dxf': {uses_converted_dxf!r},",
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
