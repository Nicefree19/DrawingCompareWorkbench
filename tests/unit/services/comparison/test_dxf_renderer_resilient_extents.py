# -*- coding: utf-8 -*-
"""Resilient render extents: a malformed entity must not corrupt the frame.

Observed bug: a converted-DWG BEFORE drawing had a MULTILEADER whose virtual
MTEXT carries an empty style name, so ``ezdxf.bbox.extents(msp)`` raised
DXFTableEntryError. The renderer then fell back to ``_simple_entity_extents``,
which reads raw block insert points and picked up a stray base point at
y = -34,891,598 mm -> the BEFORE raster frame was garbage and overlay/marker
world coords no longer mapped onto it (AFTER was fine, so the two frames
disagreed). The fix recomputes the extent entity-by-entity, skipping the few
that raise, so the true extent is recovered.
"""
from __future__ import annotations

import pytest

from src.services.comparison import dxf_renderer as dr

pytestmark = pytest.mark.skipif(
    not getattr(dr, "RENDERER_AVAILABLE", False),
    reason="DXF renderer dependencies unavailable",
)


def _doc(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (100.0, 40.0))
    msp.add_circle((50.0, 50.0), radius=10.0)
    p = tmp_path / "d.dxf"
    doc.saveas(str(p))
    return p


def test_resilient_msp_extents_basic(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.readfile(str(_doc(tmp_path)))
    ext = dr._resilient_msp_extents(doc.modelspace())
    assert ext is not None
    min_x, min_y, max_x, max_y = ext
    assert min_x <= 0.0 and min_y <= 0.0
    assert max_x >= 100.0 and max_y >= 60.0  # circle top at 60


def test_render_uses_resilient_when_full_bbox_raises(tmp_path, monkeypatch):
    """When the whole-modelspace bbox raises, the renderer recovers the extent
    entity-by-entity (NOT via the lossy simple-entity fallback)."""
    path = _doc(tmp_path)
    real_extents = dr.ezdxf_bbox.extents

    def fake_extents(arg, *a, **k):
        # delegate the per-entity calls (list arg); raise on the whole msp call
        if isinstance(arg, list):
            return real_extents(arg, *a, **k)
        raise RuntimeError("synthetic whole-msp bbox failure")

    monkeypatch.setattr(dr.ezdxf_bbox, "extents", fake_extents)
    img, tf = dr.DxfRenderer(backend="fast").render_with_transform(
        str(path), dpi=100, max_edge_px=500
    )
    assert tf.get("extent_source") == "ezdxf_bbox_resilient"
    assert tf["min_x"] <= 0.0 and tf["max_x"] >= 100.0
    assert tf["min_y"] <= 0.0 and tf["max_y"] >= 60.0


def test_resilient_skips_entity_whose_bbox_raises(tmp_path, monkeypatch):
    """One un-boundable entity is skipped; the extent comes from the rest."""
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.readfile(str(_doc(tmp_path)))
    msp = doc.modelspace()
    real_extents = dr.ezdxf_bbox.extents
    calls = {"n": 0}

    def fake_extents(arg, *a, **k):
        if isinstance(arg, list):
            calls["n"] += 1
            if calls["n"] == 1:  # first entity raises -> must be skipped
                raise RuntimeError("synthetic per-entity failure")
            return real_extents(arg, *a, **k)
        return real_extents(arg, *a, **k)

    monkeypatch.setattr(dr.ezdxf_bbox, "extents", fake_extents)
    ext = dr._resilient_msp_extents(msp)
    assert ext is not None  # survived the one raising entity
