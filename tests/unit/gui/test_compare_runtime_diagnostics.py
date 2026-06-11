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
from src.services.comparison.dwg_autoconvert_settings import save_dwg_autoconvert_enabled

_ODA_EXE = r"C:\Program Files\ODA\ODAFileConverter 26.10.0\ODAFileConverter.exe"


def _no_oda():
    return (False, None)


def _oda_installed():
    return (True, _ODA_EXE)


def test_default_gui_dwg_backend_mode_propagates_explicit_oda_env(tmp_path: Path) -> None:
    assert (
        default_gui_dwg_backend_mode(
            {"DRAWING_COMPARE_DWG_BACKEND": "oda"},
            settings_path=tmp_path / "s.json",
            detect=_no_oda,
        )
        == "oda_converter"
    )


def test_default_gui_dwg_backend_mode_explicit_native_env_wins_over_everything(
    tmp_path: Path,
) -> None:
    # Explicit non-ODA env is an opt-out: it beats both a saved "on" choice
    # and an installed converter.
    settings = tmp_path / "s.json"
    save_dwg_autoconvert_enabled(True, settings)
    assert (
        default_gui_dwg_backend_mode(
            {"DRAWING_COMPARE_DWG_BACKEND": "native"},
            settings_path=settings,
            detect=_oda_installed,
        )
        is None
    )


def test_default_gui_dwg_backend_mode_autodetects_installed_oda(tmp_path: Path) -> None:
    # THE 2026-06-11 fix: launches that bypass the packaged .bat (no env var)
    # must still use an installed ODA instead of silently failing on AC1018+.
    assert (
        default_gui_dwg_backend_mode(
            {}, settings_path=tmp_path / "absent.json", detect=_oda_installed
        )
        == "oda_converter"
    )


def test_default_gui_dwg_backend_mode_stays_native_without_oda(tmp_path: Path) -> None:
    assert (
        default_gui_dwg_backend_mode(
            {}, settings_path=tmp_path / "absent.json", detect=_no_oda
        )
        is None
    )


def test_default_gui_dwg_backend_mode_saved_choice_beats_detection(tmp_path: Path) -> None:
    settings = tmp_path / "s.json"
    save_dwg_autoconvert_enabled(False, settings)
    assert (
        default_gui_dwg_backend_mode({}, settings_path=settings, detect=_oda_installed)
        is None
    )
    save_dwg_autoconvert_enabled(True, settings)
    assert (
        default_gui_dwg_backend_mode({}, settings_path=settings, detect=_no_oda)
        == "oda_converter"
    )


def test_default_gui_dwg_backend_mode_invalid_env_falls_through(tmp_path: Path) -> None:
    assert (
        default_gui_dwg_backend_mode(
            {"DRAWING_COMPARE_DWG_BACKEND": "banana"},
            settings_path=tmp_path / "absent.json",
            detect=_oda_installed,
        )
        == "oda_converter"
    )


def _write_dwg_pair(tmp_path: Path) -> SimpleNamespace:
    source_a = tmp_path / "detail.dwg"
    source_b = tmp_path / "detail_r1.dwg"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)
    return SimpleNamespace(source_a=source_a, source_b=source_b, output_dir=tmp_path / "run")


_UNSUPPORTED_EXC = RuntimeError(
    "Preflight failed: DWG input version is unsupported by the native adapter: AC1032. "
    "Compare converted DXF files or supported AC1015 DWG files."
)


def test_format_auto_compare_error_explains_unsupported_dwg_without_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    import src.gui.compare_runtime_diagnostics as crd

    monkeypatch.setattr(crd, "detect_oda_installation", _no_oda)
    monkeypatch.setattr(
        crd, "_dwg_status", lambda: {"dwg_supported_versions": ["AC1015"]}
    )
    request = _write_dwg_pair(tmp_path)

    message = crd.format_auto_compare_error(_UNSUPPORTED_EXC, request)
    first_line = message.splitlines()[0]

    assert "AC1032" in first_line
    assert "AC1015" not in first_line
    assert "AC1015" in message
    assert "변환된 DXF 폴백 후보: 없음." in message
    assert "dxf_registered/before" in message
    assert str(tmp_path / "run") in message


def test_format_auto_compare_error_reports_detected_fallback_layout(
    tmp_path: Path, monkeypatch
) -> None:
    import src.gui.compare_runtime_diagnostics as crd

    monkeypatch.setattr(crd, "detect_oda_installation", _no_oda)
    request = _write_dwg_pair(tmp_path)
    before_dxf = tmp_path / "dxf_registered" / "before" / "detail.dxf"
    after_dxf = tmp_path / "dxf_registered" / "after" / "detail_r1.dxf"
    before_dxf.parent.mkdir(parents=True)
    after_dxf.parent.mkdir(parents=True)
    before_dxf.write_text("0\nEOF\n", encoding="utf-8")
    after_dxf.write_text("0\nEOF\n", encoding="utf-8")

    message = crd.format_auto_compare_error(_UNSUPPORTED_EXC, request)

    assert "변환된 DXF 폴백을 찾았습니다" in message


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
    assert has_lossy_path_text("D:/bad/�name.dxf") is True
    assert has_lossy_path_text("D:/bad/占쏙옙name.dxf") is True
    assert has_lossy_path_text("D:/ok/detail.dxf") is False


def test_unsupported_dwg_error_explains_disabled_autoconvert_when_oda_installed(
    tmp_path: Path, monkeypatch
) -> None:
    # ODA installed but auto-convert explicitly off (env opt-out): the
    # message must say conversion is OFF and name BOTH re-enable paths
    # (settings menu + env var) instead of pretending ODA is absent —
    # the 2026-06-11 live dialog claimed "available: no" on a machine
    # where it was installed.
    import src.gui.compare_runtime_diagnostics as crd

    monkeypatch.setenv("DRAWING_COMPARE_DWG_BACKEND", "native")
    monkeypatch.setattr(crd, "detect_oda_installation", _oda_installed)
    monkeypatch.setattr(
        crd, "_dwg_status", lambda: {"dwg_supported_versions": ["AC1015"]}
    )
    request = _write_dwg_pair(tmp_path)

    message = crd.format_auto_compare_error(_UNSUPPORTED_EXC, request)

    assert "자동 변환이 꺼져 있습니다" in message
    assert "DRAWING_COMPARE_DWG_BACKEND=oda_converter" in message
    assert "설정 메뉴" in message
    assert _ODA_EXE in message


def test_unsupported_dwg_error_reports_conversion_failure_when_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    # Auto-convert was active yet the run still failed on version → the
    # conversion itself failed; say that, not "enable the opt-in".
    import src.gui.compare_runtime_diagnostics as crd

    monkeypatch.setenv("DRAWING_COMPARE_DWG_BACKEND", "oda_converter")
    monkeypatch.setattr(crd, "detect_oda_installation", _oda_installed)
    monkeypatch.setattr(
        crd, "_dwg_status", lambda: {"dwg_supported_versions": ["AC1015"]}
    )
    request = _write_dwg_pair(tmp_path)

    message = crd.format_auto_compare_error(_UNSUPPORTED_EXC, request)

    assert "자동 변환이 실패했습니다" in message
    assert "꺼져 있습니다" not in message


def test_unsupported_dwg_error_links_oda_download_when_not_installed(
    tmp_path: Path, monkeypatch
) -> None:
    import src.gui.compare_runtime_diagnostics as crd

    monkeypatch.setattr(crd, "detect_oda_installation", _no_oda)
    monkeypatch.setattr(
        crd, "_dwg_status", lambda: {"dwg_supported_versions": ["AC1015"]}
    )
    request = _write_dwg_pair(tmp_path)

    message = crd.format_auto_compare_error(_UNSUPPORTED_EXC, request)

    assert "설치되어 있지 않습니다" in message
    assert "opendesign.com" in message
    assert "DXF(R2018)" in message
