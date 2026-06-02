from __future__ import annotations

import json
from pathlib import Path

from scripts.inventory_dwg_native_phase0 import inventory_dwg_native_phase0


def test_inventory_dwg_native_phase0_records_versions_and_fallback(tmp_path: Path) -> None:
    source_a = tmp_path / "detail.dwg"
    source_b = tmp_path / "detail_r1.dwg"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)
    before_dir = tmp_path / "dxf_registered" / "before"
    after_dir = tmp_path / "dxf_registered" / "after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    (before_dir / "detail.dxf").write_text("0\nEOF\n", encoding="utf-8")
    (after_dir / "detail_r1.dxf").write_text("0\nEOF\n", encoding="utf-8")
    out = tmp_path / "phase0.json"

    report = tmp_path / "phase0.md"

    payload = inventory_dwg_native_phase0([tmp_path], out=out, report_md=report)

    assert out.exists()
    assert report.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 2
    assert payload["dwg_count"] == 2
    assert payload["dwg_sample_limit_per_root"] == 200
    assert payload["version_counts"] == {"AC1032": 2}
    assert payload["unsupported_count"] == 2
    assert payload["converted_dxf_fallback_ready_count"] == 1
    assert payload["missing_target_versions"] == ["AC1018", "AC1021", "AC1024", "AC1027"]
    assert payload["root_summaries"][0]["converted_dxf_fallback_ready"] is True
    assert payload["root_summaries"][0]["missing_converted_dxf_baseline"] is False
    assert payload["folder_fallbacks"][0]["fallback_used"] is True
    assert payload["folder_fallbacks"][0]["fallback_kind"] == "dxf_registered/before_after_dirs"
    report_text = report.read_text(encoding="utf-8")
    assert "ADR-004 Phase 0 DWG Corpus Report" in report_text
    assert "dxf_registered/before_after_dirs" in report_text


def test_inventory_dwg_native_phase0_respects_sample_limit(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"sample_{index}.dwg").write_bytes(b"AC1015" + b"\0" * 32)

    payload = inventory_dwg_native_phase0([tmp_path], max_dwg_samples=2)

    assert payload["dwg_count"] == 2
    assert payload["version_counts"] == {"AC1015": 2}
    assert payload["unsupported_count"] == 0
    assert payload["root_summaries"][0]["sample_limit_reached"] is True


def test_inventory_dwg_native_phase0_handles_corrupt_dwg(tmp_path: Path) -> None:
    (tmp_path / "bad.dwg").write_bytes(b"BAD")

    payload = inventory_dwg_native_phase0([tmp_path])

    assert payload["dwg_count"] == 1
    assert payload["unsupported_count"] == 1
    assert payload["dwg_files"][0]["error_code"] == "DWG_CORRUPTED"
    assert payload["root_summaries"][0]["missing_converted_dxf_baseline"] is True
    assert any(
        gap["gap"] == "unsupported_without_converted_dxf_baseline"
        for gap in payload["corpus_gaps"]
    )


def test_inventory_dwg_native_phase0_records_empty_and_missing_roots(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    missing_root = tmp_path / "missing"

    payload = inventory_dwg_native_phase0([empty_root, missing_root])

    assert payload["root_summaries"][0]["dwg_count"] == 0
    assert payload["root_summaries"][1]["exists"] is False
    assert any(gap["gap"] == "no_dwg_samples" for gap in payload["corpus_gaps"])
    assert any(gap["gap"] == "missing_root" for gap in payload["corpus_gaps"])
