from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.cad_compare import main
from src.services.comparison.base import ComparisonResult
from src.services.comparison.commercial_dwg_json_adapter import (
    ARGS_JSON_ENV,
    COMMAND_ENV,
    LICENSE_ID_ENV,
    SUPPORTED_VERSIONS_ENV,
    TIMEOUT_SECONDS_ENV,
)
from src.services.comparison.dwg_backend import COMMERCIAL_SDK_ADAPTER_ENV
from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter


ROOT = Path(__file__).resolve().parents[3]
CAD_SAMPLES = ROOT / "tests" / "data" / "comparison" / "cad_samples" / "dxf"


def test_file_compare_cli_writes_json(tmp_path, capsys):
    output = tmp_path / "result.json"

    exit_code = main(
        [
            "file",
            str(CAD_SAMPLES / "simple_base.dxf"),
            str(CAD_SAMPLES / "simple_modified.dxf"),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mode"] == "file"
    assert payload["status"] in {"ok", "partial"}
    assert payload["result"]["summary"]["total_changes"] >= 1
    assert "file compare:" in capsys.readouterr().out


def test_file_compare_cli_reports_unsupported_dwg_version(tmp_path, capsys):
    payload = {
        "layers": [{"name": "0"}],
        "model_space": [],
    }
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    for path in (before, after):
        path.write_bytes(
            b"AC1032"
            + DwgJsonFixtureAdapter.MARKER
            + json.dumps(payload).encode("utf-8")
        )

    exit_code = main(["file", str(before), str(after)])

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "status=failed" in output
    assert "COMPARE_IMPORT_FAILED" in output


def test_file_compare_cli_user_converter_backend_uses_registered_dxf(tmp_path, capsys):
    before = tmp_path / "detail.dwg"
    after = tmp_path / "detail_r1.dwg"
    before.write_bytes(b"AC1032" + b"\0" * 32)
    after.write_bytes(b"AC1032" + b"\0" * 32)

    before_dir = tmp_path / "dxf_registered" / "before"
    after_dir = tmp_path / "dxf_registered" / "after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    fallback_a = before_dir / "detail.dxf"
    fallback_b = after_dir / "detail_r1.dxf"
    fallback_a.write_text((CAD_SAMPLES / "simple_base.dxf").read_text(encoding="utf-8"), encoding="utf-8")
    fallback_b.write_text((CAD_SAMPLES / "simple_modified.dxf").read_text(encoding="utf-8"), encoding="utf-8")
    output = tmp_path / "result.json"

    exit_code = main(
        [
            "file",
            str(before),
            str(after),
            "--dwg-backend",
            "user_converter",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    metadata = payload["result"]["metadata"]
    assert exit_code == 0
    assert payload["status"] in {"ok", "partial"}
    assert metadata["dwg_dxf_fallback"]["used"] is True
    assert metadata["dwg_dxf_fallback"]["diagnostics"]["dwg_versions"]["a"]["code"] == "AC1032"
    assert metadata["imports"]["a"]["source_path"] == str(fallback_a.resolve())
    assert metadata["imports"]["b"]["source_path"] == str(fallback_b.resolve())
    assert "status=" in capsys.readouterr().out


def test_file_compare_cli_user_converter_backend_accepts_converter_options(tmp_path, capsys):
    converter_path = tmp_path / "customer-converter.exe"
    cache_dir = tmp_path / "cache"
    converter_path.write_text("", encoding="utf-8")
    result = ComparisonResult(
        source_a=str(CAD_SAMPLES / "simple_base.dxf"),
        source_b=str(CAD_SAMPLES / "simple_modified.dxf"),
    )
    result.metadata = {"pipeline_status": "ok"}

    with patch("src.services.comparison.dwg_differ.DwgDiffer") as differ_class:
        differ_class.return_value.compare.return_value = result

        exit_code = main(
            [
                "file",
                str(CAD_SAMPLES / "simple_base.dxf"),
                str(CAD_SAMPLES / "simple_modified.dxf"),
                "--dwg-backend",
                "user_converter",
                "--user-converter-path",
                str(converter_path),
                "--user-converter-arg",
                "{input}",
                "--user-converter-arg",
                "{output_dir}",
                "--user-conversion-timeout",
                "17",
                "--dwg-conversion-cache-dir",
                str(cache_dir),
            ]
        )

    config = differ_class.call_args.kwargs["config"]
    assert exit_code == 0
    assert config["dwg_backend_mode"] == "user_converter"
    assert config["allow_oda_fallback"] is False
    assert config["user_converter_path"] == str(converter_path)
    assert config["user_conversion_args"] == ["{input}", "{output_dir}"]
    assert config["user_conversion_timeout_seconds"] == 17.0
    assert config["dwg_conversion_cache_dir"] == str(cache_dir)
    assert "file compare:" in capsys.readouterr().out


def test_file_compare_cli_commercial_sdk_requires_explicit_license_allowlist(
    tmp_path,
    monkeypatch,
    capsys,
):
    _write_commercial_plugin(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(COMMERCIAL_SDK_ADAPTER_ENV, "approved_cli_commercial_adapter:create_adapter")
    before = _write_dwg_fixture(tmp_path / "before.dwg", version="AC1032")
    after = _write_dwg_fixture(tmp_path / "after.dwg", version="AC1032")
    blocked_out = tmp_path / "blocked.json"
    approved_out = tmp_path / "approved.json"

    blocked_exit = main(
        [
            "file",
            str(before),
            str(after),
            "--dwg-backend",
            "commercial_sdk",
            "--output",
            str(blocked_out),
        ]
    )
    approved_exit = main(
        [
            "file",
            str(before),
            str(after),
            "--dwg-backend",
            "commercial_sdk",
            "--dwg-allowed-license-id",
            "COMMERCIAL-APPROVED",
            "--output",
            str(approved_out),
        ]
    )

    blocked = json.loads(blocked_out.read_text(encoding="utf-8"))
    approved = json.loads(approved_out.read_text(encoding="utf-8"))
    assert blocked_exit == 2
    assert blocked["status"] == "failed"
    assert blocked["result"]["metadata"]["imports"]["a"]["error_code"] == "DWG_FORBIDDEN_LICENSE"
    assert approved_exit == 0
    assert approved["status"] in {"ok", "partial"}
    assert approved["result"]["metadata"]["imports"]["a"]["import_report"]["adapter"]["license_id"] == "COMMERCIAL-APPROVED"
    assert approved["result"]["metadata"]["imports"]["a"]["version"]["code"] == "AC1032"
    assert "file compare:" in capsys.readouterr().out


def test_file_compare_cli_commercial_json_bridge_options_configure_adapter(
    tmp_path,
    monkeypatch,
    capsys,
):
    for env_name in _commercial_bridge_env_names():
        monkeypatch.delenv(env_name, raising=False)
    bridge = _write_json_bridge(tmp_path)
    before = _write_plain_dwg(tmp_path / "before.dwg", version="AC1032")
    after = _write_plain_dwg(tmp_path / "after.dwg", version="AC1032")
    output = tmp_path / "bridge.json"

    exit_code = main(
        [
            "file",
            str(before),
            str(after),
            "--dwg-backend",
            "commercial_sdk",
            "--dwg-commercial-adapter-spec",
            "src.services.comparison.commercial_dwg_json_adapter:create_adapter",
            "--dwg-allowed-license-id",
            "COMMERCIAL-APPROVED",
            "--dwg-bridge-command",
            sys.executable,
            "--dwg-bridge-args-json",
            json.dumps([str(bridge), "{input}", "{acadver}"]),
            "--dwg-bridge-license-id",
            "COMMERCIAL-APPROVED",
            "--dwg-bridge-supported-versions",
            "AC1032",
            "--dwg-bridge-timeout-seconds",
            "30",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    adapter = payload["result"]["metadata"]["imports"]["a"]["import_report"]["adapter"]
    assert exit_code == 0
    assert payload["status"] in {"ok", "partial"}
    assert adapter["license_id"] == "COMMERCIAL-APPROVED"
    assert adapter["diagnostics"]["kind"] == "commercial_dwg_json_bridge"
    assert adapter["diagnostics"]["command_exists"] is True
    assert adapter["diagnostics"]["supported_versions"] == ["AC1032"]
    assert "file compare:" in capsys.readouterr().out
    for env_name in _commercial_bridge_env_names():
        assert os.environ.get(env_name) is None


def test_file_compare_cli_oda_backend_enables_converter_fallback(tmp_path, capsys):
    converter_path = tmp_path / "converter.exe"
    cache_dir = tmp_path / "cache"
    converter_path.write_text("", encoding="utf-8")
    result = ComparisonResult(
        source_a=str(CAD_SAMPLES / "simple_base.dxf"),
        source_b=str(CAD_SAMPLES / "simple_modified.dxf"),
    )
    result.metadata = {"pipeline_status": "ok"}

    with patch("src.services.comparison.dwg_differ.DwgDiffer") as differ_class:
        differ_class.return_value.compare.return_value = result

        exit_code = main(
            [
                "file",
                str(CAD_SAMPLES / "simple_base.dxf"),
                str(CAD_SAMPLES / "simple_modified.dxf"),
                "--dwg-backend",
                "oda_converter",
                "--oda-converter-path",
                str(converter_path),
                "--oda-conversion-timeout",
                "7",
                "--dwg-conversion-cache-dir",
                str(cache_dir),
                "--import-timeout",
                "11",
                "--max-dxf-tokens",
                "12345",
                "--max-entities",
                "678",
            ]
        )

    config = differ_class.call_args.kwargs["config"]
    assert exit_code == 0
    assert config["dwg_backend_mode"] == "oda_converter"
    assert config["allow_oda_fallback"] is True
    assert config["oda_converter_path"] == str(converter_path)
    assert config["oda_conversion_timeout_seconds"] == 7.0
    assert config["dwg_conversion_cache_dir"] == str(cache_dir)
    assert config["import_timeout_seconds"] == 11.0
    assert config["max_dxf_tokens"] == 12345
    assert config["max_entities"] == 678
    assert "file compare:" in capsys.readouterr().out


def test_folder_compare_cli_accepts_user_converter_options(tmp_path, capsys):
    source_a = tmp_path / "old"
    source_b = tmp_path / "new"
    output_dir = tmp_path / "out"
    converter_path = tmp_path / "customer-converter.exe"
    cache_dir = tmp_path / "dwg-cache"
    source_a.mkdir()
    source_b.mkdir()
    converter_path.write_text("", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, request):
            captured["request"] = request

        def run(self, progress_callback=None):
            return SimpleNamespace(
                output_dir=str(output_dir),
                artifact_dir=str(output_dir / "artifacts"),
                review_project_path=str(output_dir / "review_project.json"),
                review_state_path=str(output_dir / "review_state.json"),
                run_manifest_path=str(output_dir / "run_manifest.json"),
                preflight_report_path=str(output_dir / "preflight_report.json"),
                confirmed_pairs=0,
                review_required_pairs=0,
                unmatched_a=0,
                unmatched_b=0,
                compare_summary=SimpleNamespace(to_dict=lambda: {"requested_pairs": 0}),
            )

    with patch("src.services.comparison.folder_compare_pipeline.FolderComparePipeline", FakePipeline):
        exit_code = main(
            [
                "folder",
                str(source_a),
                str(source_b),
                "--output-dir",
                str(output_dir),
                "--dwg-backend",
                "user_converter",
                "--user-converter-path",
                str(converter_path),
                "--user-converter-arg",
                "{input}",
                "--user-converter-arg",
                "{output_dir}",
                "--user-conversion-timeout",
                "19",
                "--dwg-conversion-cache-dir",
                str(cache_dir),
                "--dwg-allowed-license-id",
                "COMMERCIAL-APPROVED",
            ]
        )

    request = captured["request"]
    assert exit_code == 0
    assert request.dwg_backend_mode == "user_converter"
    assert request.allowed_dwg_license_ids == ("MIT", "INTERNAL", "COMMERCIAL-APPROVED")
    assert request.user_converter_path == converter_path
    assert request.user_conversion_args == ("{input}", "{output_dir}")
    assert request.user_conversion_timeout_seconds == 19.0
    assert request.dwg_conversion_cache_dir == cache_dir
    assert "folder compare:" in capsys.readouterr().out


def test_folder_compare_cli_commercial_json_bridge_options_are_scoped(
    tmp_path,
    monkeypatch,
    capsys,
):
    for env_name in _commercial_bridge_env_names():
        monkeypatch.delenv(env_name, raising=False)
    source_a = tmp_path / "old"
    source_b = tmp_path / "new"
    output_dir = tmp_path / "out"
    source_a.mkdir()
    source_b.mkdir()
    captured: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, request):
            captured["request"] = request
            captured["env"] = {name: os.environ.get(name) for name in _commercial_bridge_env_names()}

        def run(self, progress_callback=None):
            return SimpleNamespace(
                output_dir=str(output_dir),
                artifact_dir=str(output_dir / "artifacts"),
                review_project_path=str(output_dir / "review_project.json"),
                review_state_path=str(output_dir / "review_state.json"),
                run_manifest_path=str(output_dir / "run_manifest.json"),
                preflight_report_path=str(output_dir / "preflight_report.json"),
                confirmed_pairs=0,
                review_required_pairs=0,
                unmatched_a=0,
                unmatched_b=0,
                compare_summary=SimpleNamespace(to_dict=lambda: {"requested_pairs": 0}),
            )

    with patch("src.services.comparison.folder_compare_pipeline.FolderComparePipeline", FakePipeline):
        exit_code = main(
            [
                "folder",
                str(source_a),
                str(source_b),
                "--output-dir",
                str(output_dir),
                "--dwg-backend",
                "commercial_sdk",
                "--dwg-commercial-adapter-spec",
                "src.services.comparison.commercial_dwg_json_adapter:create_adapter",
                "--dwg-allowed-license-id",
                "COMMERCIAL-APPROVED",
                "--dwg-bridge-command",
                "dwg-wrapper",
                "--dwg-bridge-args-json",
                '["{input}","{acadver}"]',
                "--dwg-bridge-license-id",
                "COMMERCIAL-APPROVED",
                "--dwg-bridge-supported-versions",
                "AC1032",
                "--dwg-bridge-timeout-seconds",
                "45",
            ]
        )

    request = captured["request"]
    env = captured["env"]
    assert exit_code == 0
    assert request.dwg_backend_mode == "commercial_sdk"
    assert request.allowed_dwg_license_ids == ("MIT", "INTERNAL", "COMMERCIAL-APPROVED")
    assert env[COMMERCIAL_SDK_ADAPTER_ENV] == "src.services.comparison.commercial_dwg_json_adapter:create_adapter"
    assert env[COMMAND_ENV] == "dwg-wrapper"
    assert env[ARGS_JSON_ENV] == '["{input}","{acadver}"]'
    assert env[LICENSE_ID_ENV] == "COMMERCIAL-APPROVED"
    assert env[SUPPORTED_VERSIONS_ENV] == "AC1032"
    assert env[TIMEOUT_SECONDS_ENV] == "45.0"
    assert "folder compare:" in capsys.readouterr().out
    for env_name in _commercial_bridge_env_names():
        assert os.environ.get(env_name) is None


def _write_commercial_plugin(tmp_path: Path) -> None:
    plugin = tmp_path / "approved_cli_commercial_adapter.py"
    plugin.write_text(
        "\n".join(
            [
                "from src.services.comparison.dwg_backend import DWG_BACKEND_COMMERCIAL_SDK",
                "from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter",
                "",
                "class ApprovedCliCommercialAdapter(DwgJsonFixtureAdapter):",
                "    name = 'approved-cli-commercial-fixture'",
                "    version = '2026.1'",
                "    license_id = 'COMMERCIAL-APPROVED'",
                "    backend_mode = DWG_BACKEND_COMMERCIAL_SDK",
                "    implementation_status = 'approved_plugin'",
                "    approval_required = True",
                "",
                "    def supports_version(self, version):",
                "        return version.code in {'AC1009', 'AC1012', 'AC1014', 'AC1015', 'AC1018', 'AC1021', 'AC1024', 'AC1027', 'AC1032'}",
                "",
                "def create_adapter():",
                "    return ApprovedCliCommercialAdapter()",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_dwg_fixture(path: Path, *, version: str) -> Path:
    payload = {
        "layers": [{"name": "0"}],
        "model_space": [
            {
                "type": "LINE",
                "handle": "10",
                "layer": "0",
                "geometry": {
                    "start": {"x": 0, "y": 0, "z": 0},
                    "end": {"x": 100, "y": 0, "z": 0},
                },
            }
        ],
    }
    path.write_bytes(
        version.encode("ascii")
        + DwgJsonFixtureAdapter.MARKER
        + json.dumps(payload).encode("utf-8")
    )
    return path


def _write_plain_dwg(path: Path, *, version: str) -> Path:
    path.write_bytes(version.encode("ascii") + b" bridge input")
    return path


def _write_json_bridge(tmp_path: Path) -> Path:
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
                "        'metadata': {'source_path': path},",
                "    },",
                "}))",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return bridge


def _commercial_bridge_env_names() -> tuple[str, ...]:
    return (
        COMMERCIAL_SDK_ADAPTER_ENV,
        COMMAND_ENV,
        ARGS_JSON_ENV,
        LICENSE_ID_ENV,
        SUPPORTED_VERSIONS_ENV,
        TIMEOUT_SECONDS_ENV,
    )
