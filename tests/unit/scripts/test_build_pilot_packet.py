"""Deterministic tests for the pilot packet assembler.

Uses a STUB app directory (a fake exe + _internal) so the packet structure is
verified without a real PyInstaller build — the exe build is out of scope; this
script only assembles around an already-built app dir.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_pilot_packet import PacketBuildError, build_pilot_packet


def _stub_app_dir(tmp_path: Path) -> Path:
    app = tmp_path / "DrawingCompareWorkbench"
    app.mkdir()
    (app / "DrawingCompareWorkbench.exe").write_bytes(b"MZstub")
    internal = app / "_internal"
    internal.mkdir()
    (internal / "base_library.zip").write_bytes(b"x")
    return app


def test_build_packet_structure(tmp_path: Path) -> None:
    summary = build_pilot_packet(_stub_app_dir(tmp_path), tmp_path / "out", version="v0.9.3-test")
    pkt = Path(summary["packet_dir"])
    assert pkt.name == "DrawingCompare_v0.9.3-test_internal_pilot"
    # launcher + guides + copied app + sample + manifest
    assert (pkt / "DrawingCompare_실행.bat").exists()
    assert (pkt / "사용가이드.md").exists()
    assert (pkt / "스팟체크_기록양식.md").exists()
    app_exe = pkt / "app" / "DrawingCompareWorkbench" / "DrawingCompareWorkbench.exe"
    assert app_exe.exists()
    assert (pkt / "app" / "DrawingCompareWorkbench" / "_internal" / "base_library.zip").exists()
    # PK3 — a runnable sample pair so the engineer's first compare needs no data
    assert (pkt / "샘플도면" / "before.dxf").exists()
    assert (pkt / "샘플도면" / "after.dxf").exists()
    # provenance manifest (reproducible, not hand-assembled)
    manifest = json.loads((pkt / "packet_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "v0.9.3-test"
    assert manifest["exe"].endswith("DrawingCompareWorkbench.exe")
    assert manifest["sample_pair"]


def test_bat_references_exe_and_dwg_backend(tmp_path: Path) -> None:
    summary = build_pilot_packet(_stub_app_dir(tmp_path), tmp_path / "out", version="x")
    bat = (Path(summary["packet_dir"]) / "DrawingCompare_실행.bat").read_text(encoding="utf-8")
    assert "DrawingCompareWorkbench.exe" in bat
    assert "DRAWING_COMPARE_DWG_BACKEND=oda_converter" in bat


def test_packed_guide_describes_auto_sheet(tmp_path: Path) -> None:
    # PK2 — the packed guide describes the auto pilot_spotcheck.md + fill-and-return,
    # and flags the human dry-run as still OPEN (not a blank manual form).
    summary = build_pilot_packet(_stub_app_dir(tmp_path), tmp_path / "out")
    guide = (Path(summary["packet_dir"]) / "사용가이드.md").read_text(encoding="utf-8")
    assert "pilot_spotcheck.md" in guide
    assert "반송" in guide
    assert "OPEN" in guide


def test_zip_option_produces_archive(tmp_path: Path) -> None:
    summary = build_pilot_packet(_stub_app_dir(tmp_path), tmp_path / "out", make_zip=True)
    assert summary["zip_path"] is not None
    assert Path(summary["zip_path"]).exists()


def test_missing_exe_fails_loud(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PacketBuildError) as excinfo:
        build_pilot_packet(empty, tmp_path / "out")
    assert "exe" in str(excinfo.value).lower()
