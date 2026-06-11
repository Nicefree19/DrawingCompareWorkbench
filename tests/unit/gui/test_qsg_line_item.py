# -*- coding: utf-8 -*-
"""Pure-function contracts for the (experimental) QSG line module + the
skeleton ink normalisation that shipped with it (T2-B, 2026-06-11).

The QSGLineItem scene-graph item itself is env-gated experimental
(WORKBENCH_QSG=qsg) after live bisection showed nondeterministic native
behaviour under PySide6 6.10 + QQuickWidget — these tests cover the
Qt-free data layer that is exercised regardless.
"""

from __future__ import annotations

import struct

from src.gui.lightweight_viewport import normalize_skeleton_ink
from src.gui.qsg_line_item import (
    DEFAULT_INK_RGBA,
    flatten_primitives,
)

_VERTEX = struct.Struct("<ffBBBB")


def _vertices(buf: bytearray) -> list[tuple]:
    return [
        _VERTEX.unpack_from(buf, i)
        for i in range(0, len(buf), _VERTEX.size)
    ]


def test_flatten_lines_packs_two_vertices_per_segment_with_color() -> None:
    buf, segments = flatten_primitives([
        {"type": "lines",
         "geometry": [[0.0, 1.0, 2.0, 3.0], [4.0, 5.0, 6.0, 7.0]],
         "properties": {"color": "#112233"}},
    ])
    assert segments == 2
    verts = _vertices(buf)
    assert len(verts) == 4
    assert verts[0] == (0.0, 1.0, 0x11, 0x22, 0x33, 255)
    assert verts[1][:2] == (2.0, 3.0)


def test_flatten_path_commands_and_close() -> None:
    buf, segments = flatten_primitives([
        {"type": "path",
         "geometry": [["M", 0.0, 0.0], ["L", 10.0, 0.0],
                      ["L", 10.0, 10.0], ["Z"]],
         "properties": {}},
    ])
    # L + L + Z-close = 3 segments, default ink colour.
    assert segments == 3
    verts = _vertices(buf)
    assert verts[0][2:] == DEFAULT_INK_RGBA
    assert verts[-1][:2] == (0.0, 0.0)  # close returns to subpath start


def test_flatten_curves_are_sampled_and_garbage_skipped() -> None:
    buf, segments = flatten_primitives([
        {"type": "path",
         "geometry": [["M", 0.0, 0.0],
                      ["C", 0.0, 10.0, 10.0, 10.0, 10.0, 0.0]],
         "properties": {"color": "#ff0000"}},
        {"type": "lines", "geometry": [[1.0, 2.0]], "properties": {}},  # short seg
        "not-a-dict",
        {"type": "lines", "geometry": None},
    ])
    from src.gui.qsg_line_item import BEZIER_STEPS

    assert segments == BEZIER_STEPS  # cubic sampled into chords
    verts = _vertices(buf)
    assert verts[-1][:2] == (10.0, 0.0)  # curve endpoint exact
    assert verts[0][2:5] == (255, 0, 0)


def test_normalize_skeleton_ink_maps_near_white_only() -> None:
    prims = [
        {"type": "lines", "geometry": [], "properties": {"color": "#ffffff"}},
        {"type": "lines", "geometry": [], "properties": {"color": "#F5F5F5"}},
        {"type": "lines", "geometry": [], "properties": {"color": "#cccccc"}},
        {"type": "lines", "geometry": [], "properties": {"color": "#ff0000"}},
        {"type": "lines", "geometry": [], "properties": {}},
        {"type": "lines", "geometry": []},
    ]
    out = normalize_skeleton_ink(prims)
    assert out[0]["properties"]["color"] == "#0F172A"  # white → ink
    assert out[1]["properties"]["color"] == "#0F172A"  # near-white → ink
    assert out[2]["properties"]["color"] == "#cccccc"  # mid grey stays
    assert out[3]["properties"]["color"] == "#ff0000"  # red stays
    assert "color" not in out[4]["properties"]
