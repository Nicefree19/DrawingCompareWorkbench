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
        "pymupdf",
        "pdf_support",
        "font_support",
        "preview_dependencies",
    }.issubset(names)
    assert any(check.name == "windows_long_path" for check in result.checks)
