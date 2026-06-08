# -*- coding: utf-8 -*-
"""Tests for GUI compare runtime diagnostics."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.gui.compare_runtime_diagnostics import (
    default_gui_dwg_backend_mode,
    format_auto_compare_error,
)
from src.gui.source_path_repair import has_lossy_path_text, registered_dxf_fallback_for_source


def test_default_gui_dwg_backend_mode_propagates_explicit_oda_env() -> None:
    assert default_gui_dwg_backend_mode({"DRAWING_COMPARE_DWG_BACKEND": "oda"}) == "oda_converter"


def test_default_gui_dwg_backend_mode_leaves_native_default_unset() -> None:
    assert default_gui_dwg_backend_mode({}) is None
    assert default_gui_dwg_backend_mode({"DRAWING_COMPARE_DWG_BACKEND": "native"}) is None


def test_format_auto_compare_error_explains_unsupported_dwg_without_fallback(tmp_path: Path) -> None:
    source_a = tmp_path / "detail.dwg"
    source_b = tmp_path / "detail_r1.dwg"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)
    request = SimpleNamespace(source_a=source_a, source_b=source_b, output_dir=tmp_path / "run")
    exc = RuntimeError(
        "Preflight failed: DWG input version is unsupported by the native adapter: AC1032. "
        "Compare converted DXF files or supported AC1015 DWG files."
    )

    message = format_auto_compare_error(exc, request)
    first_line = message.splitlines()[0]

    assert "AC1032" in first_line
    assert "AC1015" not in first_line
    assert "AC1015" in message
    assert "Converted DXF fallback candidates: none." in message
    assert "dxf_registered/before" in message
    assert str(tmp_path / "run") in message


def test_format_auto_compare_error_reports_detected_fallback_layout(tmp_path: Path) -> None:
    source_a = tmp_path / "detail.dwg"
    source_b = tmp_path / "detail_r1.dwg"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)
    before_dxf = tmp_path / "dxf_registered" / "before" / "detail.dxf"
    after_dxf = tmp_path / "dxf_registered" / "after" / "detail_r1.dxf"
    before_dxf.parent.mkdir(parents=True)
    after_dxf.parent.mkdir(parents=True)
    before_dxf.write_text("0\nEOF\n", encoding="utf-8")
    after_dxf.write_text("0\nEOF\n", encoding="utf-8")
    request = SimpleNamespace(source_a=source_a, source_b=source_b, output_dir=tmp_path / "run")

    message = format_auto_compare_error(
        RuntimeError("Preflight failed: DWG input version is unsupported by the native adapter: AC1032."),
        request,
    )

    assert "Converted DXF fallback was found" in message


def test_format_auto_compare_error_preserves_unrelated_exception() -> None:
    exc = RuntimeError("something else failed")

    assert format_auto_compare_error(exc, SimpleNamespace()) == "something else failed"


def test_registered_dxf_fallback_for_source_uses_side_folder(tmp_path: Path) -> None:
    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1032")
    before_dxf = tmp_path / "dxf_registered" / "before" / "detail.dxf"
    before_dxf.parent.mkdir(parents=True)
    before_dxf.write_text("0\nEOF\n", encoding="utf-8")

    assert registered_dxf_fallback_for_source(source, "before") == before_dxf
    assert registered_dxf_fallback_for_source(source, "after") is None


def test_has_lossy_path_text_detects_surrogate_and_replacement_chars() -> None:
    assert has_lossy_path_text("D:/bad/\udcebname.dxf") is True
    assert has_lossy_path_text("D:/bad/\ufffdname.dxf") is True
    assert has_lossy_path_text("D:/bad/\u5360\uc3d9\uc619name.dxf") is True
    assert has_lossy_path_text("D:/ok/detail.dxf") is False
