from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.services.comparison.dwg_importer import DwgVersionInfo
from src.services.comparison.native_cad_bridge import (
    BRIDGE_FAILURE_CODES,
    NativeCadBridgeCode,
    NativeCadBridgeRunner,
    parse_bridge_payload,
)
from src.services.comparison.native_scene_pack import (
    BRIDGE_RESULT_SCHEMA_VERSION,
    NATIVE_SCENE_PACK_SCHEMA_VERSION,
)


ROOT = Path(__file__).resolve().parents[4]
FIXTURE_BRIDGE = ROOT / "tools" / "native_cad_fixture_bridge.py"
VALIDATOR = ROOT / "scripts" / "validate_native_cad_bridge_contract.py"


def _version(code: str = "AC1032") -> DwgVersionInfo:
    return DwgVersionInfo(code=code, family="AutoCAD 2018", release="AutoCAD 2018+", supported=False)


def _write_dwg(path: Path, code: str = "AC1032") -> Path:
    path.write_bytes(code.encode("ascii") + b"\nfixture\n")
    return path


def test_bridge_failure_codes_are_explicit_contract() -> None:
    assert BRIDGE_FAILURE_CODES == {
        "SDK_UNAVAILABLE",
        "LICENSE_NOT_ALLOWED",
        "UNSUPPORTED_VERSION",
        "TIMEOUT",
        "CANCELLED",
        "CORRUPTED_INPUT",
        "ENCRYPTED_INPUT",
        "ADAPTER_FAILED",
        "CONTRACT_INVALID",
    }


def test_runner_missing_command_reports_sdk_unavailable(tmp_path: Path) -> None:
    path = _write_dwg(tmp_path / "sample.dwg")

    result = NativeCadBridgeRunner(command="definitely-missing-native-cad-bridge").run(path, _version())

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code == NativeCadBridgeCode.SDK_UNAVAILABLE
    assert result.failure.diagnostics["dwg_version"]["code"] == "AC1032"


def test_runner_diagnostics_include_command_and_template_hashes() -> None:
    runner = NativeCadBridgeRunner(
        command=sys.executable,
        args_template=(str(FIXTURE_BRIDGE), "{input}", "{acadver}"),
    )

    diagnostics = runner.diagnostics()

    assert len(diagnostics["command_sha256"]) == 64
    assert diagnostics["args_template_file_sha256"][str(FIXTURE_BRIDGE)]


def test_parse_bridge_payload_rejects_invalid_scene_pack_schema() -> None:
    result = parse_bridge_payload(
        {
            "schema_version": BRIDGE_RESULT_SCHEMA_VERSION,
            "scene_pack": {
                "schema_version": "native-scene-pack/v999",
                "source": {},
                "adapter": {},
            },
        }
    )

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code == NativeCadBridgeCode.CONTRACT_INVALID


def test_fixture_bridge_emits_valid_contract_payload(tmp_path: Path) -> None:
    path = _write_dwg(tmp_path / "sample.dwg")

    completed = subprocess.run(
        [sys.executable, str(FIXTURE_BRIDGE), str(path), "AC1032"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    result = parse_bridge_payload(payload)
    assert result.ok is True
    assert result.scene_pack is not None
    assert result.scene_pack.schema_version == NATIVE_SCENE_PACK_SCHEMA_VERSION
    assert result.scene_pack.overview_lod0_payload()["primitive_count"] == 2
    assert (result.drawing or {})["model_space"][1]["type"] == "TEXT"


def test_validator_accepts_fixture_bridge(tmp_path: Path) -> None:
    path = _write_dwg(tmp_path / "sample.dwg")
    args_json = json.dumps([str(FIXTURE_BRIDGE), "{input}", "{acadver}"])

    completed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            str(path),
            "--bridge-command",
            sys.executable,
            "--bridge-args-json",
            args_json,
            "--acadver",
            "AC1032",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "PASS"
    assert payload["primitive_count"] == 2
    assert payload["drawing_entity_count"] == 3
