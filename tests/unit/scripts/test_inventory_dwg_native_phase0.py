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

    payload = inventory_dwg_native_phase0([tmp_path], out=out)

    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 1
    assert payload["dwg_count"] == 2
    assert payload["version_counts"] == {"AC1032": 2}
    assert payload["unsupported_count"] == 2
    assert payload["converted_dxf_fallback_ready_count"] == 1
    assert payload["folder_fallbacks"][0]["fallback_used"] is True
    assert payload["folder_fallbacks"][0]["fallback_kind"] == "dxf_registered/before_after_dirs"


def test_inventory_dwg_native_phase0_respects_sample_limit(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"sample_{index}.dwg").write_bytes(b"AC1015" + b"\0" * 32)

    payload = inventory_dwg_native_phase0([tmp_path], max_dwg_samples=2)

    assert payload["dwg_count"] == 2
    assert payload["version_counts"] == {"AC1015": 2}
    assert payload["unsupported_count"] == 0


def test_inventory_dwg_native_phase0_handles_corrupt_dwg(tmp_path: Path) -> None:
    (tmp_path / "bad.dwg").write_bytes(b"BAD")

    payload = inventory_dwg_native_phase0([tmp_path])

    assert payload["dwg_count"] == 1
    assert payload["unsupported_count"] == 1
    assert payload["dwg_files"][0]["error_code"] == "DWG_CORRUPTED"
