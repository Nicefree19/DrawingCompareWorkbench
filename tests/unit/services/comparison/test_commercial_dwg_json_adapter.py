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
                "print('ZWCAD LISP COM extractor exceeded wall timeout after 30s during open_document.', file=sys.stderr)",
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
    assert exc_info.value.details["timeout_stage"] == "open_document"


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
    # poll 1: nothing new; poll 2: late PID 200 appears and is killed; poll 3: quiet
    # (200 gone) -> settled and returns. The extra poll proves it no longer early-returns
    # on the first spawn (finding 10) yet still settles without waiting the full grace.
    snapshots = iter([{100}, {100, 200}, {100}])
    killed: list[int] = []

    monkeypatch.setattr(adapter_module, "_process_ids_for_image", lambda image_name: next(snapshots))
    monkeypatch.setattr(adapter_module, "_kill_process_tree", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(adapter_module.time, "sleep", lambda seconds: None)

    assert adapter_module._cleanup_spawned_images({"ZWCAD.exe": {100}}, grace_seconds=1.0) == {"ZWCAD.exe": [200]}
    assert killed == [200]


def test_timeout_cleanup_settles_without_waiting_full_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once spawns are killed and a quiet poll confirms none remain, cleanup returns
    without blocking the (here very large) grace window (finding 9)."""
    snapshots = iter([{100, 200}, {100}])  # kill 200, then quiet -> settle
    killed: list[int] = []
    monkeypatch.setattr(adapter_module, "_process_ids_for_image", lambda image_name: next(snapshots))
    monkeypatch.setattr(adapter_module, "_kill_process_tree", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(adapter_module.time, "sleep", lambda seconds: None)

    # grace=3600 would loop forever (sleep is a no-op, deadline never reached) unless
    # the settle path returns -- so reaching this assert proves finding 9.
    assert adapter_module._cleanup_spawned_images({"ZWCAD.exe": {100}}, grace_seconds=3600.0) == {"ZWCAD.exe": [200]}
    assert killed == [200]


def test_timeout_cleanup_retries_failed_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """A kill that fails on the first poll is retried on the next instead of leaving
    the process alive (finding 10b)."""
    attempts: list[int] = []

    def flaky_kill(pid: int) -> bool:
        attempts.append(pid)
        return attempts.count(pid) >= 2  # fails first time, succeeds second

    monkeypatch.setattr(adapter_module, "_process_ids_for_image", lambda image_name: {100, 200})
    monkeypatch.setattr(adapter_module, "_kill_process_tree", flaky_kill)
    monkeypatch.setattr(adapter_module.time, "sleep", lambda seconds: None)

    result = adapter_module._cleanup_spawned_images({"ZWCAD.exe": {100}}, grace_seconds=3600.0)
    assert result == {"ZWCAD.exe": [200]}
    assert attempts == [200, 200]  # retried after the first failure


def test_commercial_json_bridge_maps_timeout_exit_code_without_stderr_text(tmp_path: Path) -> None:
    """A bridge that exits with the structured timeout code is classified as
    IMPORT_TIMEOUT even when stderr carries no 'timeout' wording (finding 12b)."""
    bridge = tmp_path / "exit124_bridge.py"
    bridge.write_text(
        "\n".join(
            [
                "import sys",
                "print('extractor aborted (stage=open_document)', file=sys.stderr)",
                "raise SystemExit(124)",
            ]
        ),
        encoding="utf-8",
    )
    dwg = tmp_path / "sample_ac1032.dwg"
    dwg.write_bytes(b"AC1032 exit code timeout test")
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
    assert exc_info.value.details["timeout_signal"] == "exit_code"
    assert exc_info.value.details["exit_code"] == 124


def test_commercial_json_bridge_nonzero_nontimeout_is_adapter_failed(tmp_path: Path) -> None:
    """A plain non-zero exit with no timeout code/text stays ADAPTER_FAILED."""
    bridge = tmp_path / "fail_bridge.py"
    bridge.write_text("import sys\nprint('bad input', file=sys.stderr)\nraise SystemExit(2)\n", encoding="utf-8")
    dwg = tmp_path / "sample_ac1032.dwg"
    dwg.write_bytes(b"AC1032 fail test")
    adapter = CommercialDwgJsonBridgeAdapter(
        command=sys.executable,
        args_template=(str(bridge), "{input}", "{acadver}"),
        license_id="COMMERCIAL-APPROVED",
        supported_versions=("AC1032",),
        timeout_seconds=10,
    )

    with pytest.raises(DwgImportError) as exc_info:
        adapter.read_file(dwg, DwgVersionDetector.detect_bytes(dwg.read_bytes()))

    assert exc_info.value.code == DwgFailureCode.ADAPTER_FAILED
