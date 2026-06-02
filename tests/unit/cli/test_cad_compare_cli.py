from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from src.cli.cad_compare import main
from src.services.comparison.base import ComparisonResult
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
