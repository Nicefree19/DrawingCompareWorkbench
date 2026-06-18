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


# --- #3 P1: outlier-resistant content extent --------------------------------
# Multi-detail sheets have a few far-flung stray entities that inflate the raw
# extent many-fold (AC1027 PSRC: 693k mm raw vs ~140-300k of real content). The
# render/camera then frame the inflated space and content is sub-pixel. The
# content extent drops the sparsest cells (the strays) while PROTECTING every
# dense cluster by an entity budget, then only overrides when materially smaller.


def _grid_boxes(x0, y0, x1, y1, nx, ny, *, size=1.0):
    """nx*ny unit boxes whose centres tile [x0,x1] x [y0,y1] deterministically."""
    boxes = []
    for i in range(nx):
        for j in range(ny):
            cx = x0 + (x1 - x0) * (i / max(nx - 1, 1))
            cy = y0 + (y1 - y0) * (j / max(ny - 1, 1))
            boxes.append((cx, cy, cx + size, cy + size))
    return boxes


def test_content_extent_excludes_far_strays():
    """A dense cluster + a few far strays -> strays dropped, extent collapses."""
    boxes = _grid_boxes(0.0, 0.0, 60.0, 40.0, 33, 30)  # 990 dense, ~60x40
    boxes += [(800.0, 800.0, 801.0, 801.0), (750.0, 5.0, 751.0, 6.0),
              (5.0, 700.0, 6.0, 701.0), (790.0, 790.0, 791.0, 791.0)]
    ext = dr._content_extent_excluding_outliers(boxes)
    assert ext is not None
    min_x, min_y, max_x, max_y = ext
    # strays at 700-800 must be excluded; content stays near the 60x41 cluster
    assert max_x <= 100.0, max_x
    assert max_y <= 100.0, max_y


def test_content_extent_keeps_dense_secondary_cluster():
    """A real (dense) secondary cluster is KEPT; only sparse strays are dropped.

    This is the multi-detail invariant: a second detail view must not be mistaken
    for an outlier just because it is spatially separated from the main view.
    """
    boxes = _grid_boxes(0.0, 0.0, 60.0, 40.0, 30, 20)      # 600 dense, main view
    boxes += _grid_boxes(500.0, 0.0, 560.0, 40.0, 20, 15)  # 300 dense, 2nd view
    boxes += [(5000.0, 5000.0, 5001.0, 5001.0),
              (4000.0, 10.0, 4001.0, 11.0)]                # 2 far strays
    ext = dr._content_extent_excluding_outliers(boxes)
    assert ext is not None
    min_x, min_y, max_x, max_y = ext
    assert max_x >= 560.0          # secondary cluster retained
    assert max_x <= 600.0          # but the 4000-5000 strays excluded
    assert min_x <= 1.0


def test_content_extent_noop_when_uniform():
    """A uniformly dense drawing with no strays is returned unchanged."""
    boxes = _grid_boxes(0.0, 0.0, 200.0, 150.0, 40, 25)  # 1000 uniform
    full = (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))
    ext = dr._content_extent_excluding_outliers(boxes)
    assert ext == full  # inflate-trigger guard -> no change


def test_content_extent_too_few_boxes_returns_full():
    boxes = _grid_boxes(0.0, 0.0, 60.0, 40.0, 6, 5)  # 30 < 50
    full = (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))
    assert dr._content_extent_excluding_outliers(boxes) == full


def test_content_extent_empty_returns_none():
    assert dr._content_extent_excluding_outliers([]) is None


def test_content_extent_degenerate_zero_span_returns_full():
    boxes = [(5.0, 5.0, 5.0, 5.0)] * 60  # all the same point, zero span
    ext = dr._content_extent_excluding_outliers(boxes)
    assert ext == (5.0, 5.0, 5.0, 5.0)  # no crash, returns full


def test_render_uses_content_extent_with_far_strays(tmp_path):
    """End-to-end: the renderer's primary path frames content, not the strays."""
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for i in range(200):  # dense cluster in a ~100x60 region
        x = (i % 20) * 5.0
        y = (i // 20) * 6.0
        msp.add_line((x, y), (x + 4.0, y + 4.0))
    msp.add_line((800_000.0, 800_000.0), (800_001.0, 800_001.0))  # 2 far strays
    msp.add_line((-700_000.0, 5.0), (-700_001.0, 6.0))
    p = tmp_path / "strays.dxf"
    doc.saveas(str(p))

    img, tf = dr.DxfRenderer(backend="fast").render_with_transform(
        str(p), dpi=100, max_edge_px=500
    )
    assert tf.get("extent_source") == "ezdxf_bbox_content"
    # the 800k / -700k strays must NOT define the framed extent
    assert tf["max_x"] < 10_000.0
    assert tf["min_x"] > -10_000.0
