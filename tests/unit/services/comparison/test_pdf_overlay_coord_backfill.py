# -*- coding: utf-8 -*-
"""PDF overlay coordinate-metadata backfill.

Root cause of "도면은 보이나 흐리게/엉뚱한 위치 · 실배경 아님": the change-marker
bbox is in PDF image_pixels (e.g. x=1859 @ 200 DPI), but dashboard ``top_issues``
overlays drop ``bbox_coordinate_space``/``pdf_dpi``. push_change_overlays_from_v1
then passes the RAW PIXELS through (no conversion), so markers land in pixel space
while the PDF background is in points (0..842) — the page renders off-screen and
only relative markers show. The backfill restores the metadata so the
image_pixels -> PDF-points conversion fires. It must NOT use the caching
``_viewer_overlays_for_pair_v2`` (that would defeat the paged-overlay-store path).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2


def _boom(*_a, **_k):  # ensures the caching path is never taken
    raise AssertionError("_viewer_overlays_for_pair_v2 must not be called by backfill")


def _fake(pair, *, viewer_root=None):
    ns = SimpleNamespace(
        _viewer_pairs_by_id={"p": pair},
        _viewer_overlays_for_pair_v2=_boom,
        _viewer_root=viewer_root,
    )
    # bind the real (non-caching) peek helper so the last-resort path is exercised
    ns._peek_overlay_json_pdf_dpi_v2 = (
        lambda vp: DrawingCompareWorkbenchV2._peek_overlay_json_pdf_dpi_v2(ns, vp)
    )
    return ns


_PDF_PAIR = {"coordinate_source": "image_pixels", "source_a": "a.pdf",
             "source_b": "b.pdf", "compare_pdf_dpi": 200.0}
_BBOX = {"min_x": 1859.0, "min_y": 1286.0, "max_x": 1969.0, "max_y": 1452.0}


def test_backfill_stamps_space_and_dpi_from_pair_without_caching():
    fake = _fake(_PDF_PAIR)
    out = DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(
        fake, "p", [{"zone_id": "C-004", "bbox": _BBOX}]
    )
    assert out[0]["bbox_coordinate_space"] == "image_pixels"
    assert out[0]["pdf_dpi"] == 200.0


def test_backfilled_overlay_converts_to_pdf_points():
    from src.gui.lightweight_viewport import convert_bbox_to_world_space

    fake = _fake(_PDF_PAIR)
    ov = DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(
        fake, "p", [{"zone_id": "C-004", "bbox": _BBOX}]
    )[0]
    coords = convert_bbox_to_world_space(
        ov["bbox"], coordinate_space=ov["bbox_coordinate_space"],
        pdf_dpi=float(ov["pdf_dpi"]), page_height_points=1190.52,
    )
    assert coords is not None
    x0, _y0, _x1, y1 = coords
    assert x0 == pytest.approx(669.24, abs=1.0)  # 1859*72/200, inside page (was 1859 raw)
    assert 0.0 <= y1 <= 1191.0


def test_backfill_sources_dpi_from_overlay_json_peek(tmp_path):
    """When the pair record lacks dpi, the backfill peeks the overlay JSON file
    directly (non-caching) instead of the caching overlay loader."""
    overlay_json = tmp_path / "ov.json"
    overlay_json.write_text(
        json.dumps({"overlays": [{"zone_id": "C-004", "pdf_dpi": 200.0,
                                  "bbox_coordinate_space": "image_pixels"}]}),
        encoding="utf-8",
    )
    pair = {"coordinate_source": "image_pixels", "source_a": "a.pdf", "source_b": "b.pdf",
            "compare_pdf_dpi": None, "overlay_json": str(overlay_json)}
    fake = _fake(pair, viewer_root=tmp_path)
    out = DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(
        fake, "p", [{"zone_id": "C-004", "bbox": _BBOX}]
    )
    assert out[0]["pdf_dpi"] == 200.0


def test_backfill_preserves_existing_metadata():
    fake = _fake(_PDF_PAIR)
    out = DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(
        fake, "p", [{"zone_id": "C-004", "bbox": _BBOX,
                     "bbox_coordinate_space": "image_pixels", "pdf_dpi": 144.0}]
    )
    assert out[0]["pdf_dpi"] == 144.0  # not overwritten


def test_backfill_noop_for_non_pdf_pair():
    fake = _fake({"coordinate_source": "world", "source_a": "a.dxf", "source_b": "b.dxf"})
    out = DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(
        fake, "p", [{"zone_id": "Z", "bbox": _BBOX}]
    )
    assert "bbox_coordinate_space" not in out[0]  # DXF overlays untouched


def test_backfill_noop_when_no_dpi_available():
    """No dpi anywhere -> leave overlays untouched (don't fabricate a wrong scale)."""
    pair = {"coordinate_source": "image_pixels", "source_a": "a.pdf", "source_b": "b.pdf",
            "compare_pdf_dpi": None}
    fake = _fake(pair)
    out = DrawingCompareWorkbenchV2._backfill_pdf_overlay_coord_space_v2(
        fake, "p", [{"zone_id": "C-004", "bbox": _BBOX}]
    )
    assert "pdf_dpi" not in out[0]


def test_peek_skips_legacy_overlay_json_for_paged_store_pair(tmp_path):
    """Paged-store perf invariant: a pair with a paged overlay store must NOT read
    the legacy overlay JSON for dpi (the benchmark gate
    p5_overlay_page_store_query_probe.legacy_overlay_json_read_count == 0 enforces
    this). The pushed overlays already come FROM the page store, so the legacy JSON
    is redundant — the peek returns 0.0 WITHOUT reading it, even when that JSON
    contains a dpi. Regression guard for the page-pair tree-refresh leak."""
    overlay_json = tmp_path / "ov.json"
    overlay_json.write_text(
        json.dumps({"overlays": [{"pdf_dpi": 200.0, "bbox_coordinate_space": "image_pixels"}]}),
        encoding="utf-8",
    )
    pair = {"coordinate_source": "image_pixels", "source_a": "a.pdf", "source_b": "b.pdf",
            "compare_pdf_dpi": None, "overlay_json": str(overlay_json),
            "overlay_pages_manifest": str(tmp_path / "pages" / "manifest.json")}
    fake = _fake(pair, viewer_root=tmp_path)
    # Despite the legacy JSON having dpi=200, the paged-store pair skips it -> 0.0
    assert DrawingCompareWorkbenchV2._peek_overlay_json_pdf_dpi_v2(fake, pair) == 0.0


def test_peek_still_reads_legacy_overlay_json_for_non_paged_pair(tmp_path):
    """Contrast: a pair WITHOUT a paged store still reads the legacy overlay JSON
    (the original PDF coord-backfill path is preserved for legacy/raster pairs)."""
    overlay_json = tmp_path / "ov.json"
    overlay_json.write_text(
        json.dumps({"overlays": [{"pdf_dpi": 200.0}]}), encoding="utf-8",
    )
    pair = {"coordinate_source": "image_pixels", "source_a": "a.pdf", "source_b": "b.pdf",
            "compare_pdf_dpi": None, "overlay_json": str(overlay_json)}  # no paged manifest
    fake = _fake(pair, viewer_root=tmp_path)
    assert DrawingCompareWorkbenchV2._peek_overlay_json_pdf_dpi_v2(fake, pair) == 200.0
