from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.validate_dwg_json_bridge_contract import validate_contract


ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "tools" / "dwg_converted_dxf_json_bridge.py"


def test_dwg_converted_dxf_json_bridge_emits_fallback_marked_adapter_payload(tmp_path: Path) -> None:
    converter = _write_converter(tmp_path)
    dwg = tmp_path / "sample.dwg"
    dwg.write_bytes(b"AC1032 converted bridge")

    completed = subprocess.run(
        [
            sys.executable,
            str(BRIDGE),
            str(dwg),
            "AC1032",
            "--converter-command",
            sys.executable,
            "--converter-args-json",
            json.dumps([str(converter), "{input}", "{output}"]),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    drawing = payload["drawing"]
    bridge_metadata = drawing["metadata"]["commercial_dwg_json_bridge"]
    assert payload["schema_version"] == "dwg-adapter-drawing-json/v1"
    assert drawing["entities"][0]["type"] == "LINE"
    assert drawing["entities"][0]["layer"] == "A-WALL"
    assert bridge_metadata["evidence_scope"] == "converted_dxf_bridge"
    assert bridge_metadata["uses_converted_dxf"] is True


def test_dwg_converted_dxf_json_bridge_satisfies_json_bridge_contract_as_fallback(
    tmp_path: Path,
) -> None:
    converter = _write_converter(tmp_path)
    dwg = tmp_path / "sample.dwg"
    dwg.write_bytes(b"AC1032 converted bridge")
    args_json = json.dumps(
        [
            str(BRIDGE),
            "{input}",
            "{acadver}",
            "--converter-command",
            sys.executable,
            "--converter-args-json",
            json.dumps([str(converter), "{{input}}", "{{output}}"]),
        ]
    )

    report = validate_contract(
        [dwg],
        bridge_command=sys.executable,
        bridge_args_json=args_json,
        bridge_license_id="INTERNAL",
        bridge_supported_versions="AC1032",
        json_report=tmp_path / "contract.json",
    )

    assert report["status"] == "passed"
    assert report["records"][0]["import_status"] == "ok"
    assert report["records"][0]["entity_count"] == 1


def _write_converter(tmp_path: Path) -> Path:
    script = tmp_path / "fake_converter.py"
    script.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import sys",
                "output = Path(sys.argv[2])",
                "output.write_text(",
                "    '0\\nSECTION\\n2\\nHEADER\\n9\\n$ACADVER\\n1\\nAC1032\\n0\\nENDSEC\\n'",
                "    '0\\nSECTION\\n2\\nENTITIES\\n'",
                "    '0\\nLINE\\n8\\nA-WALL\\n10\\n0\\n20\\n0\\n30\\n0\\n11\\n10\\n21\\n5\\n31\\n0\\n'",
                "    '0\\nENDSEC\\n0\\nEOF\\n',",
                "    encoding='utf-8',",
                ")",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return script
