# -*- coding: utf-8 -*-
"""Resilient VECTOR zone focus: one un-renderable entity must not truncate the zone.

The raster zone crop (zone_render_service) was already resilient; the vector
zone-focus path (zone_render_worker.render_zone_focus) still used a single
``draw_layout`` that aborted at the first un-renderable entity — e.g. an INSERT
raising "'Glyph' object has no attribute 'data'" — keeping only the primitives
drawn before it (logged "Zone focus draw_layout raised mid-stream", live-test
2026-06-17 SPLICE pair). It now draws per-entity and skips failures.
"""
from __future__ import annotations

import pytest

from src.services.comparison.zone_render_worker import render_zone_focus

ezdxf = pytest.importorskip("ezdxf")
pytest.importorskip("ezdxf.addons.drawing.json")


def _doc_with_lines(tmp_path, n=3):
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for i in range(n):
        msp.add_line((0.0, float(i)), (10.0, float(i)))
    path = tmp_path / "lines.dxf"
    doc.saveas(str(path))
    return path


def test_happy_path_renders_and_reports_no_skips(tmp_path):
    src = _doc_with_lines(tmp_path)
    out = tmp_path / "ok"
    out.mkdir()
    res = render_zone_focus(src, (-1.0, -1.0, 11.0, 4.0), out)
    assert res.primitive_count > 0
    assert not any("un-renderable" in w for w in res.warnings)


def test_raising_entities_are_skipped_not_fatal(tmp_path, monkeypatch):
    """If entity draws raise, the zone build completes and surfaces the skip
    count instead of aborting mid-stream and truncating the rest."""
    from ezdxf.addons.drawing import Frontend

    src = _doc_with_lines(tmp_path, n=3)

    def _boom(self, entity, properties):  # noqa: ANN001
        raise RuntimeError("synthetic draw failure")

    monkeypatch.setattr(Frontend, "draw_entity", _boom, raising=True)
    out = tmp_path / "skipped"
    out.mkdir()
    res = render_zone_focus(src, (-1.0, -1.0, 11.0, 4.0), out)
    # No exception escaped; the failure was contained and surfaced honestly.
    assert any("un-renderable" in w for w in res.warnings)


def test_parsed_doc_cached_across_zones(tmp_path, monkeypatch):
    """Speed lever (L-speed, 2026-06-17): the persistent zone worker re-parsed the
    multi-MB DXF on every zone (4-5 s/zone). The doc is now parsed once and reused
    for later zones of the same source."""
    import src.services.comparison.dxf_read as dxf_read
    import src.services.comparison.zone_render_worker as zrw

    src = _doc_with_lines(tmp_path, n=4)

    calls = {"n": 0}
    real = dxf_read.read_dxf_document_result

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(dxf_read, "read_dxf_document_result", _counting)
    zrw._ZONE_DOC_CACHE.clear()

    out = tmp_path / "out"
    out.mkdir()
    zrw.render_zone_focus(src, (-1.0, -1.0, 11.0, 5.0), out / "z1")
    zrw.render_zone_focus(src, (2.0, 0.0, 11.0, 5.0), out / "z2")  # different zone, same source

    assert calls["n"] == 1  # parsed once; the second zone reused the cached doc


def test_bbox_cache_persisted_and_reused_across_zones(tmp_path):
    """Speed (FIX 2a, 2026-06-18): the zone filter sweeps EVERY entity's bbox.
    A fresh ezdxf bbox Cache per zone cost ~5 s/zone on a real AC1027 pair
    (measured 5.67 s build vs 0.13 s reused). The per-doc bbox Cache is now stored
    alongside the cached doc and reused so zone 2..N skip the rebuild."""
    import src.services.comparison.zone_render_worker as zrw

    src = _doc_with_lines(tmp_path, n=6)
    zrw._ZONE_DOC_CACHE.clear()
    out = tmp_path / "out"
    out.mkdir()

    zrw.render_zone_focus(src, (-1.0, -1.0, 11.0, 7.0), out / "z1")
    (entry,) = list(zrw._ZONE_DOC_CACHE.values())
    assert isinstance(entry, tuple) and len(entry) == 2  # (doc, bbox_cache)
    doc1, cache1 = entry
    hits_before = cache1.hits

    # Different zone, same source -> must reuse the SAME doc AND bbox cache.
    zrw.render_zone_focus(src, (2.0, 0.0, 11.0, 7.0), out / "z2")
    (entry2,) = list(zrw._ZONE_DOC_CACHE.values())
    doc2, cache2 = entry2

    assert doc2 is doc1          # cached doc reused
    assert cache2 is cache1      # SAME bbox cache reused, not rebuilt per zone
    assert cache1.hits > hits_before  # the 2nd sweep hit cached entity bboxes
