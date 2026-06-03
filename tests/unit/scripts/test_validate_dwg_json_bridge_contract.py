from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts import validate_dwg_json_bridge_contract as validator


def test_dwg_json_bridge_contract_passes_for_valid_wrapper(tmp_path: Path) -> None:
    bridge = _write_bridge(tmp_path)
    dwg = tmp_path / "sample_ac1032.dwg"
    dwg.write_bytes(b"AC1032" + b" json bridge contract")

    report = validator.validate_contract(
        [dwg],
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        bridge_command=sys.executable,
        bridge_args_json=json.dumps([str(bridge), "{input}", "{acadver}"]),
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        json_report=tmp_path / "contract.json",
    )

    written = json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))
    diagnostics = report["adapter"]["diagnostics"]
    assert report["status"] == "passed"
    assert written["status"] == "passed"
    assert diagnostics["kind"] == "commercial_dwg_json_bridge"
    assert diagnostics["command_exists"] is True
    assert diagnostics["command_sha256"]
    assert diagnostics["supported_versions"] == ["AC1032"]
    assert report["records"][0]["detected_version"]["code"] == "AC1032"
    assert report["records"][0]["import_status"] == "ok"
    assert report["records"][0]["entity_count"] == 1


def test_dwg_json_bridge_contract_fails_without_license_allowlist(tmp_path: Path) -> None:
    bridge = _write_bridge(tmp_path)
    dwg = tmp_path / "sample_ac1032.dwg"
    dwg.write_bytes(b"AC1032" + b" json bridge contract")

    report = validator.validate_contract(
        [dwg],
        bridge_command=sys.executable,
        bridge_args_json=json.dumps([str(bridge), "{input}", "{acadver}"]),
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        json_report=tmp_path / "contract.json",
    )

    assert report["status"] == "failed"
    assert "bridge_license_not_allowed" in report["diagnostic_errors"]
    assert report["records"][0]["import_status"] == "failed"
    assert report["records"][0]["error_code"] == "DWG_FORBIDDEN_LICENSE"


def test_dwg_json_bridge_contract_fails_without_inputs(tmp_path: Path) -> None:
    report = validator.validate_contract([], json_report=tmp_path / "contract.json")

    assert report["status"] == "failed"
    assert "bridge_input_missing" in report["diagnostic_errors"]
    assert report["summary"]["input_count"] == 0


def test_dwg_json_bridge_contract_cli_writes_report(tmp_path: Path, capsys) -> None:
    bridge = _write_bridge(tmp_path)
    dwg = tmp_path / "sample_ac1032.dwg"
    dwg.write_bytes(b"AC1032" + b" json bridge contract")
    out = tmp_path / "contract.json"

    exit_code = validator.main(
        [
            str(dwg),
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
            "--json-report",
            str(out),
        ]
    )

    printed = json.loads(capsys.readouterr().out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert printed["status"] == "passed"
    assert written["records"][0]["import_status"] == "ok"


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
                "        'metadata': {'source_path': path},",
                "    },",
                "}))",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return bridge
