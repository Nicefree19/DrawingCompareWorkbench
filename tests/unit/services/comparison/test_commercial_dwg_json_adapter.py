from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.services.comparison import commercial_dwg_json_adapter as adapter_module
from src.services.comparison.commercial_dwg_json_adapter import (
    ARGS_JSON_ENV,
    COMMAND_ENV,
    LICENSE_ID_ENV,
    SUPPORTED_VERSIONS_ENV,
    CommercialDwgJsonBridgeAdapter,
)
from src.services.comparison.dwg_backend import (
    COMMERCIAL_SDK_ADAPTER_ENV,
    DWG_BACKEND_COMMERCIAL_SDK,
)
from src.services.comparison.dwg_importer import DwgImporter, DwgVersionDetector
from src.services.comparison.dwg_importer import DwgFailureCode, DwgImportError


def test_commercial_json_bridge_fails_closed_without_command() -> None:
    adapter = CommercialDwgJsonBridgeAdapter(
        command="",
        license_id="COMMERCIAL-APPROVED",
        supported_versions=("AC1032",),
    )

    assert adapter.is_available() is False
    assert adapter.supports_version(DwgVersionDetector.detect_bytes(b"AC1032")) is True


def test_commercial_json_bridge_requires_explicit_supported_versions(tmp_path: Path) -> None:
    adapter = CommercialDwgJsonBridgeAdapter(
        command=sys.executable,
        license_id="COMMERCIAL-APPROVED",
        supported_versions=(),
    )

    assert adapter.is_available() is True
    assert adapter.supports_version(DwgVersionDetector.detect_bytes(b"AC1032")) is False


def test_commercial_json_bridge_imports_dwg_through_backend_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
                "                'geometry': {'start': [0, 0, 0], 'end': [10, 5, 0]},",
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
    dwg = tmp_path / "sample_ac1032.dwg"
    dwg.write_bytes(b"AC1032" + b" commercial bridge test")
    monkeypatch.setenv(
        COMMERCIAL_SDK_ADAPTER_ENV,
        "src.services.comparison.commercial_dwg_json_adapter:create_adapter",
    )
    monkeypatch.setenv(COMMAND_ENV, sys.executable)
    monkeypatch.setenv(ARGS_JSON_ENV, json.dumps([str(bridge), "{input}", "{acadver}"]))
    monkeypatch.setenv(LICENSE_ID_ENV, "COMMERCIAL-APPROVED")
    monkeypatch.setenv(SUPPORTED_VERSIONS_ENV, "AC1009,AC1012,AC1014,AC1015,AC1018,AC1021,AC1024,AC1027,AC1032")

    doc = DwgImporter(
        backend_mode=DWG_BACKEND_COMMERCIAL_SDK,
        allowed_license_ids=("MIT", "INTERNAL", "COMMERCIAL-APPROVED"),
    ).import_file(dwg)

    assert doc["import_report"]["status"] == "ok"
    assert doc["drawing"]["source"]["acad_version"] == "AC1032"
    assert doc["import_report"]["adapter"]["backend_mode"] == DWG_BACKEND_COMMERCIAL_SDK
    assert doc["import_report"]["adapter"]["license_id"] == "COMMERCIAL-APPROVED"
    adapter_diagnostics = doc["import_report"]["adapter"]["diagnostics"]
    assert adapter_diagnostics["kind"] == "commercial_dwg_json_bridge"
    assert adapter_diagnostics["command_exists"] is True
    assert adapter_diagnostics["command_sha256"]
    assert "AC1032" in adapter_diagnostics["supported_versions"]
    assert doc["entities"][0]["type"] == "line"
    bridge_metadata = doc["metadata"]["adapter_metadata"]["commercial_dwg_json_bridge"]
    assert bridge_metadata["dwg_version"] == "AC1032"
    assert bridge_metadata["diagnostics"]["kind"] == "commercial_dwg_json_bridge"


def test_commercial_json_bridge_maps_timeout_stderr_to_import_timeout(tmp_path: Path) -> None:
    bridge = tmp_path / "timeout_bridge.py"
    bridge.write_text(
        "\n".join(
            [
                "import sys",
                "print('ZWCAD LISP COM extractor did not produce JSON within 1s', file=sys.stderr)",
                "raise SystemExit(1)",
            ]
        ),
        encoding="utf-8",
    )
    dwg = tmp_path / "sample_ac1032.dwg"
    dwg.write_bytes(b"AC1032" + b" commercial bridge timeout test")
    adapter = CommercialDwgJsonBridgeAdapter(
        command=sys.executable,
        args_template=(str(bridge), "{input}", "{acadver}"),
        license_id="COMMERCIAL-APPROVED",
        supported_versions=("AC1032",),
        timeout_seconds=10,
    )

    with pytest.raises(DwgImportError) as exc_info:
        adapter.read_file(dwg, DwgVersionDetector.detect_bytes(dwg.read_bytes()))

    assert exc_info.value.code == DwgFailureCode.IMPORT_TIMEOUT


def test_commercial_json_bridge_subprocess_timeout_cleans_configured_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dwg = tmp_path / "sample_ac1032.dwg"
    dwg.write_bytes(b"AC1032" + b" commercial bridge subprocess timeout test")
    snapshots: list[tuple[str, ...]] = []

    def timeout_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout"))

    def snapshot(image_names):
        snapshots.append(tuple(image_names))
        return {"ZWCAD.exe": {100}}

    monkeypatch.setattr(adapter_module.subprocess, "run", timeout_run)
    monkeypatch.setattr(adapter_module, "_process_snapshot", snapshot)
    monkeypatch.setattr(adapter_module, "_cleanup_spawned_images", lambda snapshot, **kwargs: {"ZWCAD.exe": [200]})
    adapter = CommercialDwgJsonBridgeAdapter(
        command=sys.executable,
        args_template=("-c", "print('unused')"),
        license_id="COMMERCIAL-APPROVED",
        supported_versions=("AC1032",),
        timeout_seconds=1,
        timeout_cleanup_image_names=("ZWCAD",),
    )

    with pytest.raises(DwgImportError) as exc_info:
        adapter.read_file(dwg, DwgVersionDetector.detect_bytes(dwg.read_bytes()))

    assert snapshots == [("ZWCAD.exe",)]
    assert exc_info.value.code == DwgFailureCode.IMPORT_TIMEOUT
    assert exc_info.value.details["timeout_cleanup_pids"] == {"ZWCAD.exe": [200]}


def test_timeout_cleanup_only_kills_new_image_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []

    def fake_run(command, **kwargs):
        killed.append(int(command[2]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(adapter_module, "_process_ids_for_image", lambda image_name: {100, 200, 300})
    monkeypatch.setattr(adapter_module.subprocess, "run", fake_run)

    assert adapter_module._cleanup_spawned_images({"ZWCAD.exe": {100}}) == {"ZWCAD.exe": [200, 300]}
    assert killed == [200, 300]


def test_timeout_cleanup_waits_for_late_spawned_image_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshots = iter([{100}, {100, 200}])
    killed: list[int] = []

    monkeypatch.setattr(adapter_module, "_process_ids_for_image", lambda image_name: next(snapshots))
    monkeypatch.setattr(adapter_module, "_kill_process_tree", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(adapter_module.time, "sleep", lambda seconds: None)

    assert adapter_module._cleanup_spawned_images({"ZWCAD.exe": {100}}, grace_seconds=1.0) == {"ZWCAD.exe": [200]}
    assert killed == [200]


def test_kill_process_tree_falls_back_to_terminate_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_taskkill(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="denied")

    monkeypatch.setattr(adapter_module.subprocess, "run", failed_taskkill)
    monkeypatch.setattr(adapter_module, "_terminate_process", lambda pid: pid == 200)

    assert adapter_module._kill_process_tree(200) is True


def test_process_ids_for_image_fallback_timeout_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(adapter_module, "_process_ids_for_image_toolhelp", lambda image_name: None)
    monkeypatch.setattr(adapter_module.subprocess, "run", timeout_run)

    assert adapter_module._process_ids_for_image("ZWCAD.exe") == set()
