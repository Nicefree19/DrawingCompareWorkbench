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
