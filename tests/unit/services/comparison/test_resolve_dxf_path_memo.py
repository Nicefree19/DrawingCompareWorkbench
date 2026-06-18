# -*- coding: utf-8 -*-
"""resolve_dxf_path memoises native-normalisation failure (L4, 2026-06-17).

AC1018+ DWGs fail the native canonical import on every zone/skeleton render and
fall back to a cached DXF. The live log showed the same "DWG vector
normalisation failed ... using cached DXF" 24x in one session — the failing
import was re-run each time. The memo records the failure so later renders skip
straight to the cached fallback.
"""
from __future__ import annotations

import src.services.comparison.zone_vector_renderer as zvr
from src.services.comparison import import_pipeline as ip


def test_native_failure_is_memoised_and_not_retried(tmp_path, monkeypatch):
    src = tmp_path / "detail.dwg"
    src.write_bytes(b"AC1027 dummy unsupported dwg")
    fallback = tmp_path / "fallback.dxf"
    fallback.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")

    calls = {"native": 0}

    def _failing_import(self, path):  # noqa: ANN001
        calls["native"] += 1
        raise OSError("DWG_UNSUPPORTED_VERSION: native reader cannot read AC1027")

    monkeypatch.setattr(ip.ImportPipeline, "import_file", _failing_import, raising=True)
    monkeypatch.setattr(zvr, "_exact_dwg_differ_cache", lambda s, c: None)
    monkeypatch.setattr(zvr, "_oda_autoconvert_cache", lambda s, c: fallback)
    zvr._NATIVE_NORMALISE_FALLBACK_MEMO.clear()

    cache_dir = tmp_path / "cache"
    out1 = zvr.resolve_dxf_path(src, cache_dir=cache_dir)
    out2 = zvr.resolve_dxf_path(src, cache_dir=cache_dir)

    assert out1 == fallback
    assert out2 == fallback
    # The failing native import ran once; the memo skipped it on the second call.
    assert calls["native"] == 1


def test_memo_reattempts_when_source_changes(tmp_path, monkeypatch):
    """An edited source (new mtime/size) must re-attempt the native path."""
    src = tmp_path / "detail.dwg"
    src.write_bytes(b"AC1027 v1")
    fallback = tmp_path / "fallback.dxf"
    fallback.write_text("0\nEOF\n", encoding="utf-8")

    calls = {"native": 0}

    def _failing_import(self, path):  # noqa: ANN001
        calls["native"] += 1
        raise OSError("DWG_UNSUPPORTED_VERSION")

    monkeypatch.setattr(ip.ImportPipeline, "import_file", _failing_import, raising=True)
    monkeypatch.setattr(zvr, "_exact_dwg_differ_cache", lambda s, c: None)
    monkeypatch.setattr(zvr, "_oda_autoconvert_cache", lambda s, c: fallback)
    zvr._NATIVE_NORMALISE_FALLBACK_MEMO.clear()

    cache_dir = tmp_path / "cache"
    zvr.resolve_dxf_path(src, cache_dir=cache_dir)
    src.write_bytes(b"AC1027 v2 edited longer content")  # changes size + mtime
    zvr.resolve_dxf_path(src, cache_dir=cache_dir)

    assert calls["native"] == 2  # re-attempted after the source changed
