from __future__ import annotations

import json
import sys
from pathlib import Path

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
