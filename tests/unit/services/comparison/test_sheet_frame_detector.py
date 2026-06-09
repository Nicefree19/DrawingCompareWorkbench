# -*- coding: utf-8 -*-
"""Unit tests for the outer drawing-frame (도곽) producer.

These exercise the REAL detector against synthesized ezdxf modelspaces (not
injected metadata), guarding against the "infrastructure built + synthetic test
only" trap that previously left sheet-frame alignment dead in production.
"""

from __future__ import annotations

import pytest

from src.services.comparison.sheet_frame_detector import (
    SheetFrameResult,
    detect_sheet_frame_bbox,
    detect_sheet_frame_from_modelspace,
)
from src.services.comparison.sheet_frame_alignment import build_sheet_frame_alignment

ezdxf = pytest.importorskip("ezdxf")


def _rect(msp, x, y, w, h, *, layer="0", close=True):
    msp.add_lwpolyline(
        [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
        close=close,
        dxfattribs={"layer": layer},
    )


def test_detects_outer_frame_covering_extents():
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _rect(msp, 0.0, 0.0, 1000.0, 700.0, layer="0")

    result = detect_sheet_frame_from_modelspace(msp)

    assert isinstance(result, SheetFrameResult)
    assert result.bbox == pytest.approx((0.0, 0.0, 1000.0, 700.0))
    assert result.coverage_ratio == pytest.approx(1.0, abs=1e-3)
    assert result.method == "cad_polyline_frame"
    assert 0.8 <= result.confidence <= 0.95


def test_outer_frame_wins_over_inner_detail():
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _rect(msp, 0.0, 0.0, 3000.0, 1800.0, layer="FRAME")  # 도곽
    _rect(msp, 500.0, 400.0, 600.0, 400.0, layer="DETAIL")  # inner detail box

    result = detect_sheet_frame_from_modelspace(msp)

    assert result is not None
    # The large outer rectangle, not the inner detail, is the sheet frame.
    assert result.bbox == pytest.approx((0.0, 0.0, 3000.0, 1800.0))


def test_rejects_small_rectangle_below_coverage():
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _rect(msp, 0.0, 0.0, 200.0, 300.0, layer="DETAIL")  # tiny box
    # Far-away geometry blows up the extents so the box covers << 50%.
    msp.add_line((0.0, 0.0), (10000.0, 10000.0), dxfattribs={"layer": "REF"})

    assert detect_sheet_frame_from_modelspace(msp) is None


def test_returns_none_without_closed_rectangle():
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (1000.0, 0.0), dxfattribs={"layer": "BEAM"})
    msp.add_line((0.0, 0.0), (0.0, 700.0), dxfattribs={"layer": "BEAM"})
    text = msp.add_text("NOTE", dxfattribs={"height": 50})
    text.set_placement((100.0, 100.0))

    assert detect_sheet_frame_from_modelspace(msp) is None


def test_open_polyline_is_not_a_frame():
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _rect(msp, 0.0, 0.0, 1000.0, 700.0, layer="0", close=False)

    assert detect_sheet_frame_from_modelspace(msp) is None


def test_frame_layer_bonus_increases_confidence():
    doc_plain = ezdxf.new("R2010")
    _rect(doc_plain.modelspace(), 0.0, 0.0, 1200.0, 800.0, layer="0")
    plain = detect_sheet_frame_from_modelspace(doc_plain.modelspace())

    doc_border = ezdxf.new("R2010")
    _rect(doc_border.modelspace(), 0.0, 0.0, 1200.0, 800.0, layer="BORDER")
    border = detect_sheet_frame_from_modelspace(doc_border.modelspace())

    assert plain is not None and border is not None
    assert border.confidence > plain.confidence


def test_detect_from_path_roundtrip(tmp_path):
    doc = ezdxf.new("R2010")
    _rect(doc.modelspace(), 10.0, 20.0, 2000.0, 1400.0, layer="TITLE")
    dxf_path = tmp_path / "sheet.dxf"
    doc.saveas(str(dxf_path))

    result = detect_sheet_frame_bbox(dxf_path)

    assert result is not None
    assert result.bbox == pytest.approx((10.0, 20.0, 2010.0, 1420.0))


def test_non_cad_path_returns_none(tmp_path):
    pdf_path = tmp_path / "drawing.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    assert detect_sheet_frame_bbox(pdf_path) is None


def test_producer_output_feeds_consumer_alignment():
    """End-to-end contract: detector bbox -> consumer alignment (no synthetic keys).

    Two revisions of the same sheet with different origins must align through the
    shared sheet-local frame, which is exactly the dead-in-production path this
    work revives.
    """

    doc_before = ezdxf.new("R2010")
    _rect(doc_before.modelspace(), 0.0, 0.0, 3000.0, 2000.0, layer="FRAME")
    before = detect_sheet_frame_from_modelspace(doc_before.modelspace())

    # After revision re-origined by (+128000, +5000); same frame size/aspect.
    doc_after = ezdxf.new("R2010")
    _rect(doc_after.modelspace(), 128000.0, 5000.0, 3000.0, 2000.0, layer="FRAME")
    after = detect_sheet_frame_from_modelspace(doc_after.modelspace())

    assert before is not None and after is not None

    before_transform = {"cad_frame_bbox": list(before.bbox)}
    after_transform = {"cad_frame_bbox": list(after.bbox)}
    alignment = build_sheet_frame_alignment(before_transform, after_transform)

    assert alignment is not None and alignment.is_usable
    # Same sheet-local full window maps back to each side's own native frame.
    assert alignment.native_bbox_for_local("before") == pytest.approx((0.0, 0.0, 3000.0, 2000.0))
    assert alignment.native_bbox_for_local("after") == pytest.approx((128000.0, 5000.0, 131000.0, 7000.0))
