# -*- coding: utf-8 -*-
"""Drawing Compare operational preflight checks."""

from pathlib import Path

from src.services.comparison.preflight import run_preflight


def test_preflight_surfaces_mvp_operational_checks(tmp_path: Path) -> None:
    source_a = tmp_path / "old"
    source_b = tmp_path / "new"
    output = tmp_path / "out"
    source_a.mkdir()
    source_b.mkdir()

    result = run_preflight(source_a=source_a, source_b=source_b, output_dir=output)
    names = {check.name for check in result.checks}

    assert {
        "source_a",
        "source_b",
        "output_dir",
        "dxf_cache_dir",
        "compare_state_dir",
        "disk_space",
        "temp_dir",
        "rtree",
        "oda_converter",
        "dwg_version_support",
        "pymupdf",
        "pdf_support",
        "font_support",
        "preview_dependencies",
    }.issubset(names)
    assert any(check.name == "windows_long_path" for check in result.checks)


def test_preflight_rejects_unsupported_dwg_version_before_compare(tmp_path: Path) -> None:
    source_a = tmp_path / "old.dwg"
    source_b = tmp_path / "new.dwg"
    output = tmp_path / "out"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)

    result = run_preflight(source_a=source_a, source_b=source_b, output_dir=output)
    check = next(item for item in result.checks if item.name == "dwg_version_support")

    assert result.status == "failed"
    assert check.status == "error"
    assert "AC1032" in check.message
    assert "converted DXF" in check.message
    assert {item["code"] for item in check.details["unsupported"]} == {"AC1032"}
