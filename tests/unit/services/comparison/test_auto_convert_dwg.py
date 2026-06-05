# -*- coding: utf-8 -*-
"""Auto-convert an unsupported DWG (e.g. AC1032) via ODA so "just give a DWG" works.

The real ODA File Converter is mocked here; an end-to-end run against an installed
ODA is verified separately. These guard the dispatch + caching contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison import dwg_dxf_fallback as f


class _Ver:
    def __init__(self, supported: bool):
        self.supported = supported


def test_non_dwg_is_left_untouched(tmp_path):
    p = tmp_path / "a.dxf"
    p.write_text("x")
    out, converted, note = f.auto_convert_unsupported_dwg(p, tmp_path / "cache")
    assert out == Path(p) and converted is False and note == "not_dwg"


def test_supported_dwg_left_for_native(tmp_path, monkeypatch):
    p = tmp_path / "a.dwg"
    p.write_bytes(b"AC1015 stub")
    monkeypatch.setattr(f.DwgVersionDetector, "detect_file", staticmethod(lambda x: _Ver(True)))
    out, converted, note = f.auto_convert_unsupported_dwg(p, tmp_path / "cache")
    assert converted is False and note == "native_supported"


def test_unsupported_dwg_without_oda_is_noop(tmp_path, monkeypatch):
    p = tmp_path / "a.dwg"
    p.write_bytes(b"AC1032 stub")
    monkeypatch.setattr(f.DwgVersionDetector, "detect_file", staticmethod(lambda x: _Ver(False)))
    import src.services.comparison.dwg_converter as dc

    def _raise(*a, **k):
        raise dc.ODAConverterNotFoundError("no oda")

    monkeypatch.setattr(dc, "DwgConverter", _raise)
    out, converted, note = f.auto_convert_unsupported_dwg(p, tmp_path / "cache")
    assert out == Path(p) and converted is False and note == "oda_unavailable"


def test_unsupported_dwg_converts_then_caches(tmp_path, monkeypatch):
    p = tmp_path / "drawing.dwg"
    p.write_bytes(b"AC1032 stub")
    monkeypatch.setattr(f.DwgVersionDetector, "detect_file", staticmethod(lambda x: _Ver(False)))
    import src.services.comparison.dwg_converter as dc

    calls = {"n": 0}

    class _FakeConverter:
        def __init__(self, *a, **k):
            pass

        def convert(self, src, output_version="ACAD2018", timeout=180):
            calls["n"] += 1
            outdir = tmp_path / "oda_tmp"
            outdir.mkdir(exist_ok=True)
            outp = outdir / (Path(src).stem + ".dxf")
            outp.write_text("0\nSECTION\n")  # dummy DXF content
            return outp

    monkeypatch.setattr(dc, "DwgConverter", _FakeConverter)
    cache = tmp_path / "cache"

    out1, conv1, note1 = f.auto_convert_unsupported_dwg(p, cache)
    assert conv1 is True and note1 == "oda_converted"
    assert out1.exists() and out1.suffix == ".dxf"
    assert "oda_auto" in out1.parts  # landed in the cache namespace

    out2, conv2, note2 = f.auto_convert_unsupported_dwg(p, cache)
    assert conv2 is True and note2 == "oda_cache_hit"
    assert out2 == out1
    assert calls["n"] == 1  # cached: ODA not re-run


def test_conversion_failure_is_non_fatal(tmp_path, monkeypatch):
    p = tmp_path / "drawing.dwg"
    p.write_bytes(b"AC1032 stub")
    monkeypatch.setattr(f.DwgVersionDetector, "detect_file", staticmethod(lambda x: _Ver(False)))
    import src.services.comparison.dwg_converter as dc

    class _BoomConverter:
        def __init__(self, *a, **k):
            pass

        def convert(self, *a, **k):
            raise RuntimeError("synthetic ODA failure")

    monkeypatch.setattr(dc, "DwgConverter", _BoomConverter)
    out, converted, note = f.auto_convert_unsupported_dwg(p, tmp_path / "cache")
    assert out == Path(p) and converted is False and note.startswith("oda_failed:")
