# -*- coding: utf-8 -*-
"""Resilient zone render: one un-renderable entity must NOT blank the whole crop.

Root cause this guards against: a converted-DWG DXF MULTILEADER whose virtual
MTEXT has an empty style name makes ezdxf raise DXFTableEntryError inside
draw_layout, which (before the fix) aborted the entire render -> blank PNG ->
source_render_failed. The resilient Frontend skips only the offending entity and
surfaces the count (honest degradation).
"""
from __future__ import annotations

import pytest

from src.services.comparison import dxf_renderer as dxf_module
from src.services.comparison.zone_render_service import (
    WorldWindow,
    _render_dxf_window,
    _render_source_crop,
    get_drawing_render_index,
    transform_for_window,
)

pytestmark = pytest.mark.skipif(
    not getattr(dxf_module, "RENDERER_AVAILABLE", False),
    reason="DXF renderer dependencies unavailable",
)


def _doc_with_lines(tmp_path, n=3):
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for i in range(n):
        msp.add_line((0.0, float(i)), (10.0, float(i)))
    path = tmp_path / "lines.dxf"
    doc.saveas(str(path))
    return path


def test_happy_path_reports_zero_skipped_and_renders(tmp_path):
    path = _doc_with_lines(tmp_path)
    idx = get_drawing_render_index(path, "test-env")
    win = WorldWindow(-1.0, -1.0, 11.0, 4.0)
    tf = transform_for_window(win, output_width=120, output_height=120)
    out = tmp_path / "ok.png"
    visible, total, prefilter_skipped, entities_skipped = _render_dxf_window(idx, out, win, tf)
    assert entities_skipped == 0
    assert total == 3
    assert out.exists()


def test_raising_entities_are_skipped_not_fatal(tmp_path, monkeypatch):
    """If every entity draw raises, the crop is produced anyway and the skip
    count reflects it — no exception escapes, no blank-on-first-bad-entity abort."""
    path = _doc_with_lines(tmp_path, n=3)
    idx = get_drawing_render_index(path, "test-env2")
    win = WorldWindow(-1.0, -1.0, 11.0, 4.0)
    tf = transform_for_window(win, output_width=120, output_height=120)

    # Force ezdxf's base draw_entity to raise so the resilient override engages.
    base = dxf_module.Frontend

    def _boom(self, entity, properties):  # noqa: ANN001
        raise RuntimeError("synthetic draw failure")

    monkeypatch.setattr(base, "draw_entity", _boom, raising=True)
    out = tmp_path / "skipped.png"
    visible, total, prefilter_skipped, entities_skipped = _render_dxf_window(idx, out, win, tf)
    assert entities_skipped >= 1  # entities were skipped rather than aborting
    assert out.exists()  # a (blank) PNG is still produced — no crash


def test_render_source_crop_surfaces_skip_warning(tmp_path, monkeypatch):
    import src.services.comparison.zone_render_service as zrs

    monkeypatch.setattr(zrs, "_normalize_dxf_source", lambda src, cache: src)
    monkeypatch.setattr(zrs, "get_drawing_render_index", lambda p, h: object())
    monkeypatch.setattr(zrs, "_render_dxf_window", lambda *a, **k: (5, 7, False, 2))
    warnings: list = []
    zrs._render_source_crop(
        tmp_path / "x.dxf", tmp_path / "x.png", WorldWindow(0, 0, 1, 1), {},
        dxf_cache_dir=tmp_path, render_environment_hash="h", warnings=warnings,
    )
    assert any(w == "dxf_render:entities_skipped:2" for w in warnings)


def test_render_source_crop_no_warning_when_zero_skipped(tmp_path, monkeypatch):
    import src.services.comparison.zone_render_service as zrs

    monkeypatch.setattr(zrs, "_normalize_dxf_source", lambda src, cache: src)
    monkeypatch.setattr(zrs, "get_drawing_render_index", lambda p, h: object())
    monkeypatch.setattr(zrs, "_render_dxf_window", lambda *a, **k: (5, 7, False, 0))
    warnings: list = []
    zrs._render_source_crop(
        tmp_path / "x.dxf", tmp_path / "x.png", WorldWindow(0, 0, 1, 1), {},
        dxf_cache_dir=tmp_path, render_environment_hash="h", warnings=warnings,
    )
    assert not any("entities_skipped" in w for w in warnings)


# --------------------------------------------------------------------------- #
# frozen/off layers: a detected change on a hidden layer must still render in
# the inspection crop (otherwise the zoom is blank — the diff sees all layers).
# --------------------------------------------------------------------------- #


def _frozen_layer_doc(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new("R2010")
    frozen = doc.layers.add("REV_FROZEN")
    frozen.freeze()
    off = doc.layers.add("REV_OFF")
    off.off()
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (100.0, 80.0), dxfattribs={"layer": "REV_FROZEN"})
    msp.add_lwpolyline([(10, 10), (90, 10), (90, 70), (10, 70)], close=True,
                       dxfattribs={"layer": "REV_OFF"})
    path = tmp_path / "frozen.dxf"
    doc.saveas(str(path))
    return path


def test_make_all_layers_visible_thaws_and_turns_on(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    from src.services.comparison.zone_render_service import _make_all_layers_visible

    doc = ezdxf.readfile(str(_frozen_layer_doc(tmp_path)))
    changed = _make_all_layers_visible(doc)
    assert changed >= 2
    for layer in doc.layers:
        assert not layer.is_frozen()
        assert not layer.is_off()


def test_zone_crop_renders_frozen_and_off_layer_changes(tmp_path):
    """End-to-end: an entity on a frozen layer and one on an off layer both
    render in the zone crop (ink > 0) instead of a blank zoom."""
    import numpy as np
    from PIL import Image

    from src.services.comparison.zone_render_service import (
        RenderJob,
        render_zone_pair,
    )

    pytest.importorskip("numpy")
    pytest.importorskip("PIL.Image")
    path = _frozen_layer_doc(tmp_path)
    job = RenderJob(
        pair_uuid="fz", zone_id="z", source_before=path, source_after=path,
        world_window=WorldWindow(-10.0, -10.0, 110.0, 90.0),
        cache_root=tmp_path / "c", dxf_cache_dir=tmp_path / "d",
        output_width=300, output_height=300,
    )
    res = render_zone_pair(job)
    img = np.asarray(Image.open(res.before_image).convert("L"))
    assert int((img < 200).sum()) > 0  # frozen/off-layer geometry is visible
