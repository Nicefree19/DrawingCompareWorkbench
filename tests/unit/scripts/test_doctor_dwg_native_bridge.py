from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts import doctor_dwg_native_bridge as doctor


def test_dwg_native_bridge_doctor_passes_native_marked_probe(tmp_path: Path) -> None:
    bridge = _write_bridge(tmp_path)
    probe = _write_probe_dwg(tmp_path)

    report = doctor.run_doctor(
        bridge_command=sys.executable,
        bridge_args_json=json.dumps([str(bridge), "{input}", "{acadver}"]),
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        target_versions=("AC1032",),
        probe_inputs=(probe,),
        require_probe=True,
    )

    assert report["status"] == "passed"
    assert report["summary"]["missing_versions"] == []
    assert _checks(report)["native_provenance_probe[0]"]["passed"] is True
    assert _checks(report)["bridge_command"]["passed"] is True


def test_dwg_native_bridge_doctor_fails_missing_command(tmp_path: Path) -> None:
    report = doctor.run_doctor(
        bridge_command=str(tmp_path / "missing-wrapper.exe"),
        bridge_args_json=json.dumps(["{input}", "{acadver}"]),
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        target_versions=("AC1032",),
    )

    failed = _failed_checks(report)
    assert report["status"] == "failed"
    assert "bridge_command" in failed
    assert "bridge_adapter_available" in failed
    assert report["next_actions"][0]["action"] == "configure_available_bridge_command"


def test_dwg_native_bridge_doctor_rejects_converted_dxf_provenance(tmp_path: Path) -> None:
    bridge = _write_bridge(tmp_path, evidence_scope="converted_dxf_bridge", uses_native_dwg=False, uses_converted_dxf=True)
    probe = _write_probe_dwg(tmp_path)

    report = doctor.run_doctor(
        bridge_command=sys.executable,
        bridge_args_json=json.dumps([str(bridge), "{input}", "{acadver}"]),
        bridge_license_id="COMMERCIAL-APPROVED",
        bridge_supported_versions="AC1032",
        allowed_dwg_license_ids=("COMMERCIAL-APPROVED",),
        target_versions=("AC1032",),
        probe_inputs=(probe,),
        require_probe=True,
    )

    failed = _failed_checks(report)
    assert report["status"] == "failed"
    detail = failed["native_provenance_probe[0]"]["detail"]
    assert "uses_converted_dxf=true" in detail
    assert "native_evidence_scope=converted_dxf_bridge" in detail


def test_dwg_native_bridge_doctor_cli_writes_report(tmp_path: Path) -> None:
    bridge = _write_bridge(tmp_path)
    probe = _write_probe_dwg(tmp_path)
    out = tmp_path / "doctor.json"

    exit_code = doctor.main(
        [
            "--bridge-command",
            sys.executable,
            "--bridge-args-json",
            json.dumps([str(bridge), "{input}", "{acadver}"]),
            "--bridge-license-id",
            "COMMERCIAL-APPROVED",
            "--bridge-supported-versions",
            "AC1032",
            "--dwg-allowed-license-id",
            "COMMERCIAL-APPROVED",
            "--target-version",
            "AC1032",
            "--probe-input",
            str(probe),
            "--require-probe",
            "--out",
            str(out),
        ]
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["schema_version"] == doctor.SCHEMA_VERSION
    assert payload["status"] == "passed"


def _write_probe_dwg(tmp_path: Path) -> Path:
    path = tmp_path / "probe.dwg"
    path.write_bytes(b"AC1032 probe")
    return path


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


def _checks(report: dict) -> dict[str, dict]:
    return {check["name"]: check for check in report["checks"]}


def _failed_checks(report: dict) -> dict[str, dict]:
    return {check["name"]: check for check in report["checks"] if not check["passed"]}
