# -*- coding: utf-8 -*-
"""Tests for unsupported DWG converted-DXF fallback resolution."""

from pathlib import Path

from src.services.comparison.dwg_dxf_fallback import resolve_dwg_dxf_fallback_pair


def test_resolves_registered_dxf_pair_for_unsupported_dwg(tmp_path: Path) -> None:
    source_a = tmp_path / "240111_P5_detail.dwg"
    source_b = tmp_path / "240111_P5_detail_r1.dwg"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)

    before_dir = tmp_path / "dxf_registered" / "before"
    after_dir = tmp_path / "dxf_registered" / "after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    fallback_a = before_dir / "240111_P5_detail.dxf"
    fallback_b = after_dir / "240111_P5_detail_r1.dxf"
    fallback_a.write_text("0\nEOF\n", encoding="utf-8")
    fallback_b.write_text("0\nEOF\n", encoding="utf-8")

    resolution = resolve_dwg_dxf_fallback_pair(source_a, source_b)

    assert resolution.used is True
    assert resolution.reason == "unsupported_dwg_version_with_converted_dxf"
    assert resolution.effective_source_a == fallback_a.resolve()
    assert resolution.effective_source_b == fallback_b.resolve()
    assert resolution.diagnostics["dwg_versions"]["a"]["code"] == "AC1032"
    assert resolution.diagnostics["fallback_kind"] == "dxf_registered/before_after"


def test_keeps_original_pair_when_no_converted_dxf_exists(tmp_path: Path) -> None:
    source_a = tmp_path / "old.dwg"
    source_b = tmp_path / "new.dwg"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)

    resolution = resolve_dwg_dxf_fallback_pair(source_a, source_b)

    assert resolution.used is False
    assert resolution.effective_source_a == source_a.resolve()
    assert resolution.effective_source_b == source_b.resolve()


def test_resolves_from_sibling_before_after_work_root(tmp_path: Path) -> None:
    source_a_dir = tmp_path / "before"
    source_b_dir = tmp_path / "after"
    source_a_dir.mkdir()
    source_b_dir.mkdir()
    source_a = source_a_dir / "detail.dwg"
    source_b = source_b_dir / "detail_r1.dwg"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)

    fallback_a = tmp_path / "dxf_registered" / "before" / "detail.dxf"
    fallback_b = tmp_path / "dxf_registered" / "after" / "detail_r1.dxf"
    fallback_a.parent.mkdir(parents=True)
    fallback_b.parent.mkdir(parents=True)
    fallback_a.write_text("0\nEOF\n", encoding="utf-8")
    fallback_b.write_text("0\nEOF\n", encoding="utf-8")

    resolution = resolve_dwg_dxf_fallback_pair(source_a, source_b)

    assert resolution.used is True
    assert resolution.effective_source_a == fallback_a.resolve()
    assert resolution.effective_source_b == fallback_b.resolve()


def test_resolves_same_folder_selection_to_registered_before_after_dirs(tmp_path: Path) -> None:
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

    resolution = resolve_dwg_dxf_fallback_pair(tmp_path, tmp_path)

    assert resolution.used is True
    assert resolution.reason == "unsupported_dwg_folder_with_converted_dxf_dirs"
    assert resolution.effective_source_a == before_dir.resolve()
    assert resolution.effective_source_b == after_dir.resolve()


def test_keeps_supported_dwg_pair_even_when_dxf_exists(tmp_path: Path) -> None:
    source_a = tmp_path / "old.dwg"
    source_b = tmp_path / "new.dwg"
    source_a.write_bytes(b"AC1015" + b"\0" * 32)
    source_b.write_bytes(b"AC1015" + b"\0" * 32)
    source_a.with_suffix(".dxf").write_text("0\nEOF\n", encoding="utf-8")
    source_b.with_suffix(".dxf").write_text("0\nEOF\n", encoding="utf-8")

    resolution = resolve_dwg_dxf_fallback_pair(source_a, source_b)

    assert resolution.used is False
    assert resolution.effective_source_a == source_a.resolve()
    assert resolution.effective_source_b == source_b.resolve()
