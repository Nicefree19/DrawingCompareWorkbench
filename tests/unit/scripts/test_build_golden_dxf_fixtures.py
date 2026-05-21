# -*- coding: utf-8 -*-
"""Regression tests for deterministic DXF golden fixture generation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("ezdxf")


def _read_dxf_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*.dxf"))
    }


def test_build_golden_dxf_fixtures_is_byte_stable(tmp_path: Path) -> None:
    from scripts.build_golden_dxf_fixtures import build

    output_dir = tmp_path / "golden"

    assert build(output_dir) == 0
    first = _read_dxf_bytes(output_dir)

    assert build(output_dir) == 0
    second = _read_dxf_bytes(output_dir)

    assert second == first


def test_build_golden_dxf_fixtures_normalizes_dynamic_metadata(tmp_path: Path) -> None:
    from scripts.build_golden_dxf_fixtures import build

    output_dir = tmp_path / "golden"

    assert build(output_dir) == 0
    sample = (output_dir / "dxf" / "01_identical" / "before.dxf").read_text(encoding="utf-8")

    assert "2451545.0000000000" in sample
    assert "{00000000-0000-0000-0000-000000000001}" in sample
    assert "{00000000-0000-0000-0000-000000000002}" in sample
    assert "fixture @ 2000-01-01T00:00:00+00:00" in sample
    assert not re.search(r"\d+(?:\.\d+){1,3} @ 20\d\d-", sample)


def test_build_golden_dxf_fixtures_writes_text_fixtures_with_lf(tmp_path: Path) -> None:
    from scripts.build_golden_dxf_fixtures import build

    output_dir = tmp_path / "golden"

    assert build(output_dir) == 0

    truth = output_dir / "dxf" / "01_identical" / "truth.json"
    manifest = output_dir / "manifest.yaml"

    assert b"\r\n" not in truth.read_bytes()
    assert b"\r\n" not in manifest.read_bytes()
