from __future__ import annotations

import json
from pathlib import Path

from src.cli.cad_compare import main
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
