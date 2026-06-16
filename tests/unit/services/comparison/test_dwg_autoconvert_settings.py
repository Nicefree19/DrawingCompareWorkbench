# -*- coding: utf-8 -*-
"""Tests for the persistent DWG auto-convert (ODA) opt-in/out settings."""

from __future__ import annotations

from pathlib import Path

from src.services.comparison.dwg_autoconvert_settings import (
    detect_oda_installation,
    load_ac1032_native_enabled,
    load_dwg_autoconvert_enabled,
    save_ac1032_native_enabled,
    save_dwg_autoconvert_enabled,
)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "dwg_autoconvert_settings.json"
    save_dwg_autoconvert_enabled(True, target)
    assert load_dwg_autoconvert_enabled(target) is True
    save_dwg_autoconvert_enabled(False, target)
    assert load_dwg_autoconvert_enabled(target) is False


def test_ac1032_native_and_autoconvert_settings_coexist(tmp_path: Path) -> None:
    # The two settings share one JSON file; saving either must preserve the other.
    target = tmp_path / "dwg_autoconvert_settings.json"
    assert load_ac1032_native_enabled(target) is None  # never chosen
    save_dwg_autoconvert_enabled(True, target)
    save_ac1032_native_enabled(True, target)
    assert load_dwg_autoconvert_enabled(target) is True
    assert load_ac1032_native_enabled(target) is True
    # toggling one leaves the other intact
    save_dwg_autoconvert_enabled(False, target)
    assert load_ac1032_native_enabled(target) is True
    assert load_dwg_autoconvert_enabled(target) is False


def test_load_missing_file_means_no_decision(tmp_path: Path) -> None:
    assert load_dwg_autoconvert_enabled(tmp_path / "absent.json") is None


def test_load_corrupt_or_nonbool_means_no_decision(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert load_dwg_autoconvert_enabled(corrupt) is None

    nonbool = tmp_path / "nonbool.json"
    nonbool.write_text('{"dwg_auto_convert": "yes"}', encoding="utf-8")
    assert load_dwg_autoconvert_enabled(nonbool) is None

    nondict = tmp_path / "nondict.json"
    nondict.write_text("[true]", encoding="utf-8")
    assert load_dwg_autoconvert_enabled(nondict) is None


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "settings.json"
    save_dwg_autoconvert_enabled(True, target)
    assert load_dwg_autoconvert_enabled(target) is True


def test_detect_oda_installation_reports_install(monkeypatch) -> None:
    from src.services.comparison import dwg_converter

    monkeypatch.setattr(
        dwg_converter.DwgConverter,
        "check_installation",
        classmethod(lambda cls: {"installed": True, "path": r"C:\oda\ODAFileConverter.exe"}),
    )
    assert detect_oda_installation() == (True, r"C:\oda\ODAFileConverter.exe")


def test_detect_oda_installation_never_raises(monkeypatch) -> None:
    from src.services.comparison import dwg_converter

    def _boom(cls):  # noqa: ANN001
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(
        dwg_converter.DwgConverter, "check_installation", classmethod(_boom)
    )
    assert detect_oda_installation() == (False, None)


def test_get_status_reports_actual_oda_installation(monkeypatch) -> None:
    # The 2026-06-11 dialog lied ("available: no") because get_status
    # hardcoded oda_converter=False from the ODA-removal era.
    import src.services.comparison.dwg_autoconvert_settings as das
    from src.services.comparison.dwg_differ import DwgDiffer

    monkeypatch.setattr(
        das, "detect_oda_installation", lambda: (True, r"C:\oda\ODAFileConverter.exe")
    )
    status = DwgDiffer.get_status()
    assert status["oda_converter"] is True
    assert status["oda_path"] == r"C:\oda\ODAFileConverter.exe"

    monkeypatch.setattr(das, "detect_oda_installation", lambda: (False, None))
    status = DwgDiffer.get_status()
    assert status["oda_converter"] is False
    assert status["oda_path"] is None
