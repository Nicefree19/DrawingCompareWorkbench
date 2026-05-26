from __future__ import annotations

import json
from pathlib import Path

from scripts.dwg_native_diagnostics import build_report, render_markdown
from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter


def test_dwg_native_diagnostics_script_reports_planned_version_block(tmp_path: Path) -> None:
    source_root = tmp_path / "samples"
    source_root.mkdir()
    future = source_root / "future.dwg"
    future.write_bytes(
        b"AC1032"
        + DwgJsonFixtureAdapter.MARKER
        + json.dumps({"model_space": []}).encode("utf-8")
    )
    ac1024 = source_root / "ac1024.dwg"
    ac1024.write_bytes(
        b"AC1024"
        + DwgJsonFixtureAdapter.MARKER
        + json.dumps({"model_space": []}).encode("utf-8")
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "cad-real-world-local/v1",
                "source_root": str(source_root),
                "samples": [
                    {
                        "id": "future",
                        "path": "future.dwg",
                        "format": "dwg",
                        "expected_version": "AC1032",
                        "expected_size_bytes": future.stat().st_size,
                    },
                    {
                        "id": "ac1024",
                        "path": "ac1024.dwg",
                        "format": "dwg",
                        "expected_version": "AC1024",
                        "expected_size_bytes": ac1024.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_report(manifest, root=Path.cwd())

    assert report["status"] == "ok"
    assert report["summary"]["blocking_stage_counts"] == {"section_map_decoder": 2}
    diagnostics_by_id = {item["sample_id"]: item for item in report["diagnostics"]}
    assert diagnostics_by_id["future"]["status"] == "unsupported_version"
    assert diagnostics_by_id["future"]["blocking_stage"] == "section_map_decoder"
    assert diagnostics_by_id["ac1024"]["status"] == "unsupported_version"
    assert diagnostics_by_id["ac1024"]["blocking_stage"] == "section_map_decoder"
    for item in diagnostics_by_id.values():
        stages = {stage["name"]: stage for stage in item["stages"]}
        metrics = stages["section_map_decoder"]["metrics"]
        assert metrics["blocking_stage_detail"] == "approved_format_contract_required"
        assert metrics["approved_reference_available"] is False
    markdown = render_markdown(report)
    assert "DWG Native Diagnostics" in markdown
    assert "approved_format_contract_required" in markdown
