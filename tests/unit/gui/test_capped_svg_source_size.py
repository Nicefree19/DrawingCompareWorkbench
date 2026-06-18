# -*- coding: utf-8 -*-
"""SVG overlay raster-grid cap — keeps the change overlay under Qt's 256 MB limit.

Live-test 2026-06-17 (POT BEARING zones): the SVG vector overlay was rasterised
at ``displayed * 4`` and, for zones filling a large fraction of the 8000 px
render, the grid exceeded Qt's default 256 MB ``QImageIOHandler`` limit. Qt
silently rejected the image ("exceeds the current allocation limit of 256
megabytes") and the change cloud vanished from the viewer. ``capped_svg_source_size``
scales the grid down under that limit so the overlay always decodes.
"""
from __future__ import annotations

from src.gui.workbench_render_decisions import (
    SVG_SOURCE_BUDGET_PX,
    SVG_SOURCE_FLOOR,
    capped_svg_source_size,
)

#: Qt's hard ceiling: 256 MB / 4 bytes-per-RGBA-px.
QT_LIMIT_PX = 256 * 1024 * 1024 // 4  # 67,108,864 px


def test_raw_grid_would_have_been_rejected():
    """Pins the pre-fix failure: the naive displayed*4 grid exceeds Qt's limit."""
    assert (5256 * 4) * (1414 * 4) > QT_LIMIT_PX


def test_large_zone_capped_below_qt_limit():
    """POT BEARING zone (~5256x1414 displayed) decodes after the cap."""
    sw, sh = capped_svg_source_size(5256, 1414)
    assert sw * sh <= SVG_SOURCE_BUDGET_PX
    assert sw * sh < QT_LIMIT_PX  # would decode instead of being rejected
    # aspect ratio preserved through the uniform down-scale
    assert abs((sw / sh) - (5256 / 1414)) < 0.02


def test_moderate_zone_keeps_full_4x_grid():
    """A zone comfortably under budget keeps the full 4x sharpness grid."""
    sw, sh = capped_svg_source_size(640, 1365)
    assert (sw, sh) == (2560, 5460)  # 640*4, 1365*4 — unscaled
    assert sw * sh <= SVG_SOURCE_BUDGET_PX


def test_tiny_zone_uses_floor():
    """SPLICE C-007-style tiny zone floors at SVG_SOURCE_FLOOR, well under budget."""
    sw, sh = capped_svg_source_size(13, 60)
    assert (sw, sh) == (SVG_SOURCE_FLOOR, SVG_SOURCE_FLOOR)


def test_extreme_aspect_still_under_budget():
    """Even a pathological wide zone stays under budget."""
    sw, sh = capped_svg_source_size(8000, 4135)
    assert sw * sh <= SVG_SOURCE_BUDGET_PX
    assert sw * sh < QT_LIMIT_PX
