# -*- coding: utf-8 -*-
"""P0-0 — Reliability-core test harness (RELIABILITY_FIRST_ROADMAP_2026 §P0).

Headless safety net for the reliability-core work. Reproduces the real-data
failure shape (a multi-region modelspace where far-flung review markup inflates
the frame / extents) with a tiny synthetic DXF, so the P0 fixes can be verified
without the 80MB customer file, without Qt, and without touching the golden
snapshot fixtures.

Two test kinds:
  * CHARACTERIZATION (pass now): documents the *current* buggy behaviour with a
    concrete number, so the bug is pinned and the later fix is provably a change.
  * TARGET (xfail now): expresses the desired post-fix contract. Flips to xpass
    when the corresponding P0 task lands — that is the signal to drop the xfail.

This file deliberately builds DXF in-memory (ezdxf) and asserts at the
normalizer/extents level, which is fully headless.
"""

from __future__ import annotations

import io

import ezdxf

from src.services.comparison.dxf_importer import DxfImporter
from src.services.comparison.drawing_normalizer import (
    DrawingNormalizer,
    NormalizationOptions,
)

# Far-flung review markup sits ~50,000 units from the real drawing — mirroring
# the customer file (main 도곽 ~12.6k wide, markup out to x≈-81,846 / +377,977).
_STRUCTURAL_LAYER = "BEAM"
_MARKUP_LAYER = "!검토_마킹"
_REGION_W, _REGION_H = 1000.0, 700.0   # the real 도곽 we care about
_MARKUP_X = 50000.0                    # far-flung markup origin


def _multiregion_dxf(*, with_markup: bool) -> str:
    """A tiny structural region near origin + (optionally) far-flung markup."""
    doc = ezdxf.new("R2000")  # AC1015 — matches the native-reader scope
    for layer in (_STRUCTURAL_LAYER, _MARKUP_LAYER):
        if layer not in doc.layers:
            doc.layers.add(layer)
    msp = doc.modelspace()
    # Structural 도곽 rectangle near origin.
    corners = [(0, 0), (_REGION_W, 0), (_REGION_W, _REGION_H), (0, _REGION_H), (0, 0)]
    for (x1, y1), (x2, y2) in zip(corners, corners[1:]):
        msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": _STRUCTURAL_LAYER})
    if with_markup:
        # A revision-markup cloud parked far to the right (other-sheet noise).
        msp.add_line((_MARKUP_X, 0), (_MARKUP_X + 100, 100),
                     dxfattribs={"layer": _MARKUP_LAYER})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue()


def _extents_width(dxf_text: str, options: NormalizationOptions | None = None) -> float:
    drawing = DxfImporter(expand_blocks=True).import_text(dxf_text, file_name="p0.dxf")
    normalized, _ = DrawingNormalizer(options or NormalizationOptions()).normalize(drawing)
    ext = normalized.get("extents")
    if isinstance(ext, dict):
        return float(ext["max_x"]) - float(ext["min_x"])
    # 4-seq fallback
    return float(ext[2]) - float(ext[0])


# ---------------------------------------------------------------------------
# Characterization — pins the current bug with a number (passes today)
# ---------------------------------------------------------------------------


def test_extents_without_markup_match_the_real_region() -> None:
    """Sanity: with no far markup, extents == the structural region width."""
    width = _extents_width(_multiregion_dxf(with_markup=False))
    assert abs(width - _REGION_W) < 1.0


def test_extents_are_currently_inflated_by_far_markup() -> None:
    """BUG (pinned): far markup inflates the frame ~50x, so the real 도곽
    renders as a speck. This is the root of '동떨어진 정보 / 도곽이 점'."""
    width = _extents_width(_multiregion_dxf(with_markup=True))
    assert width > _MARKUP_X  # ~50,100 vs the 1,000-wide real region
    # The real region is <2% of the framed width — the "speck" effect.
    assert (_REGION_W / width) < 0.05


# ---------------------------------------------------------------------------
# Target — desired post-fix contract (xfail until P0-4 lands)
# ---------------------------------------------------------------------------


def test_extents_exclude_ignore_layer_markup_P0_4() -> None:
    """P0-4 (DONE): given ignore-layer patterns, the normalizer extents exclude
    markup layers, so the frame collapses back to the real structural region.

    Contract: ``NormalizationOptions(ignore_layer_patterns=[...])`` drops
    matching-layer entities from the extents union (drawing_normalizer.py:722),
    default empty = no-op (golden-safe)."""
    options = NormalizationOptions(ignore_layer_patterns=("!*", "*검토*"))
    width = _extents_width(_multiregion_dxf(with_markup=True), options=options)
    # Frame collapses back to the real region, not the far markup.
    assert abs(width - _REGION_W) < 50.0


def test_renderer_extents_exclude_ignore_layer_markup_P0_4() -> None:
    """P0-4 (DONE, viewer side): the raster renderer's extent computation also
    excludes ignore-layer markup, so the viewer frame is the real 도곽."""
    import ezdxf as _ezdxf
    from src.services.comparison.dxf_renderer import _simple_entity_extents

    doc = _ezdxf.new("R2000")
    for layer in (_STRUCTURAL_LAYER, _MARKUP_LAYER):
        if layer not in doc.layers:
            doc.layers.add(layer)
    msp = doc.modelspace()
    msp.add_line((0, 0), (_REGION_W, _REGION_H), dxfattribs={"layer": _STRUCTURAL_LAYER})
    msp.add_line((_MARKUP_X, 0), (_MARKUP_X + 100, 100), dxfattribs={"layer": _MARKUP_LAYER})

    # No filter → inflated by far markup.
    raw = _simple_entity_extents(msp)
    assert raw is not None and (raw[2] - raw[0]) > _MARKUP_X
    # With ignore patterns → real region only.
    filtered = _simple_entity_extents(msp, ignore_layer_patterns=("!*", "*검토*"))
    assert filtered is not None and abs((filtered[2] - filtered[0]) - _REGION_W) < 50.0
