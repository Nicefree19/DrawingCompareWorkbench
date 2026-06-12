# -*- coding: utf-8 -*-
"""OBJECTS-strip slimming for ODA-converted DXF (2026-06-12).

Real-pair measurements that motivated this: a 1 MB DWG converted to a
65.7 MB ASCII DXF whose OBJECTS section was 94% of the bytes; stripping
it left extraction signatures and scene-pack primitives IDENTICAL while
cutting parse time 10x. These tests lock the safety contract: slimming
is verified by shape parity and anything suspicious keeps the original.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

import src.services.comparison.dxf_slim as slim_mod
from src.services.comparison.dxf_slim import (
    slim_converted_dxf,
    strip_objects_section,
)


def _write_sample_dxf(path: Path, *, lines: int = 5) -> None:
    doc = ezdxf.new()
    msp = doc.modelspace()
    for i in range(lines):
        msp.add_line((0.0, float(i)), (100.0, float(i) + 5.0))
    block = doc.blocks.new(name="DETAIL_A")
    block.add_circle((5.0, 5.0), 2.5)
    msp.add_blockref("DETAIL_A", (50.0, 50.0))
    doc.saveas(path)


def _shape(path: Path):
    doc = ezdxf.readfile(str(path))
    return (len(doc.modelspace()), len(doc.blocks), sum(len(b) for b in doc.blocks))


def test_strip_objects_removes_section_and_preserves_drawing(tmp_path: Path) -> None:
    src = tmp_path / "converted.dxf"
    _write_sample_dxf(src)
    text = src.read_text(encoding="utf-8")
    assert "OBJECTS" in text, "sample must carry an OBJECTS section"
    before_shape = _shape(src)

    dst = tmp_path / "slim.dxf"
    stats = strip_objects_section(src, dst)

    assert stats["dropped_lines"] > 0
    assert dst.stat().st_size < src.stat().st_size
    slim_text = dst.read_text(encoding="utf-8")
    assert "\nOBJECTS\n" not in slim_text
    assert _shape(dst) == before_shape  # drawing content identical


def test_slim_converted_dxf_replaces_in_place_when_verified(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(slim_mod, "SLIM_MIN_BYTES", 0)
    target = tmp_path / "converted.dxf"
    _write_sample_dxf(target)
    original_size = target.stat().st_size
    shape_before = _shape(target)

    result, note = slim_converted_dxf(target)

    assert note == "slimmed"
    assert result == target
    assert target.stat().st_size < original_size
    assert _shape(target) == shape_before
    assert not list(tmp_path.glob("*.slim.tmp.dxf"))  # no temp leftovers


def test_slim_skips_small_files(tmp_path: Path) -> None:
    target = tmp_path / "small.dxf"
    _write_sample_dxf(target)  # well under the 8 MB gate

    result, note = slim_converted_dxf(target)

    assert note == "skipped_small"
    assert result == target


def test_slim_opt_out_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DRAWING_COMPARE_SLIM_CONVERTED_DXF", "0")
    target = tmp_path / "converted.dxf"
    _write_sample_dxf(target)

    _, note = slim_converted_dxf(target)

    assert note == "skipped_disabled"


def test_slim_keeps_original_on_shape_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(slim_mod, "SLIM_MIN_BYTES", 0)
    shapes = iter([(10, 2, 5), (9, 2, 5)])  # original vs slim disagree
    monkeypatch.setattr(slim_mod, "_doc_shape", lambda _p: next(shapes))
    target = tmp_path / "converted.dxf"
    _write_sample_dxf(target)
    original_size = target.stat().st_size

    _, note = slim_converted_dxf(target)

    assert note == "kept_original:shape_mismatch"
    assert target.stat().st_size == original_size  # untouched
    assert not list(tmp_path.glob("*.slim.tmp.dxf"))


def test_slim_no_gain_short_circuits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(slim_mod, "SLIM_MIN_BYTES", 0)
    target = tmp_path / "no_objects.dxf"
    src = tmp_path / "seed.dxf"
    _write_sample_dxf(src)
    strip_objects_section(src, target)  # already slim → second pass drops 0

    _, note = slim_converted_dxf(target)

    assert note in ("skipped_no_gain", "slimmed")
    if note == "skipped_no_gain":
        assert _shape(target)  # still readable
