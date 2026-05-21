# -*- coding: utf-8 -*-
"""Tests for the Phase I PDF → DXF cloud-mark exporter.

Pure-Python — uses ezdxf to inspect the generated DXF and asserts
geometric correctness of the page boundary + per-zone rectangles +
labels.

Coordinate convention:
    PDF pixels at pdf_dpi (top-left origin, Y down)
        ↓ converted by _bbox_pdf_pixels_to_mm
    DXF mm (bottom-left origin, Y up)

Tests pin both the conversion math AND the DXF entity layout so a
future refactor can't silently shift coordinates.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Coordinate conversion math
# ---------------------------------------------------------------------------


def test_pixel_to_mm_basic_conversion() -> None:
    from src.services.comparison.pdf_cloud_dxf_export import _bbox_pdf_pixels_to_mm
    # 200 px at 200 DPI = 1 inch = 25.4 mm
    out = _bbox_pdf_pixels_to_mm(
        [0, 0, 200, 200], pdf_dpi=200, page_height_px=2000,
    )
    # Y flip: page_height (2000) - y (0) = bottom edge at 2000 px = 254 mm
    #         page_height (2000) - y (200) = 1800 px = 228.6 mm
    # X: 0 → 0 mm, 200 → 25.4 mm
    assert out[0] == pytest.approx(0.0)
    assert out[2] == pytest.approx(25.4)
    assert out[1] == pytest.approx(228.6)
    assert out[3] == pytest.approx(254.0)


def test_pixel_to_mm_handles_zero_dpi_with_default() -> None:
    from src.services.comparison.pdf_cloud_dxf_export import _bbox_pdf_pixels_to_mm
    # pdf_dpi=0 → falls back to 200
    out = _bbox_pdf_pixels_to_mm(
        [0, 0, 100, 100], pdf_dpi=0, page_height_px=1000,
    )
    # 100 px @ 200 DPI = 12.7 mm
    assert out[0] == pytest.approx(0.0)
    assert out[2] == pytest.approx(12.7, rel=1e-3)


def test_pixel_to_mm_y_flip_correctness() -> None:
    """A bbox at the TOP of the PDF page (small y) maps to the TOP of
    the DXF (large y). Vice versa for bottom."""

    from src.services.comparison.pdf_cloud_dxf_export import _bbox_pdf_pixels_to_mm
    page_h_px = 1000
    # PDF top-left bbox
    top = _bbox_pdf_pixels_to_mm(
        [0, 0, 100, 100], pdf_dpi=200, page_height_px=page_h_px,
    )
    # PDF bottom-left bbox
    bottom = _bbox_pdf_pixels_to_mm(
        [0, page_h_px - 100, 100, page_h_px], pdf_dpi=200, page_height_px=page_h_px,
    )
    # In DXF (y-up), the top PDF bbox should have HIGHER y values
    assert top[1] > bottom[1]
    assert top[3] > bottom[3]


# ---------------------------------------------------------------------------
# Bbox parsing
# ---------------------------------------------------------------------------


def test_resolve_bbox_dict_form() -> None:
    from src.services.comparison.pdf_cloud_dxf_export import _resolve_pixel_bbox_for_dxf
    overlay = {"after_bbox_px": {"x": 10, "y": 20, "width": 100, "height": 50}}
    out = _resolve_pixel_bbox_for_dxf(overlay)
    assert out == (10.0, 20.0, 110.0, 70.0)


def test_resolve_bbox_list_form() -> None:
    from src.services.comparison.pdf_cloud_dxf_export import _resolve_pixel_bbox_for_dxf
    overlay = {"after_bbox_px": [10, 20, 110, 70]}
    out = _resolve_pixel_bbox_for_dxf(overlay)
    assert out == (10.0, 20.0, 110.0, 70.0)


def test_resolve_bbox_falls_back_to_generic() -> None:
    from src.services.comparison.pdf_cloud_dxf_export import _resolve_pixel_bbox_for_dxf
    overlay = {"bbox": [10, 20, 110, 70]}
    out = _resolve_pixel_bbox_for_dxf(overlay)
    assert out == (10.0, 20.0, 110.0, 70.0)


def test_resolve_bbox_returns_none_for_garbage() -> None:
    from src.services.comparison.pdf_cloud_dxf_export import _resolve_pixel_bbox_for_dxf
    assert _resolve_pixel_bbox_for_dxf({}) is None
    assert _resolve_pixel_bbox_for_dxf({"after_bbox_px": "garbage"}) is None
    assert _resolve_pixel_bbox_for_dxf({"after_bbox_px": [1, 2]}) is None


# ---------------------------------------------------------------------------
# Confirmed zone selector
# ---------------------------------------------------------------------------


class _FakeRecord:
    def __init__(self, pair_id, zone_id, status):
        self.pair_id = pair_id
        self.zone_id = zone_id
        self.status = status


def test_confirmed_zone_selector_filters_by_pair_and_status() -> None:
    from src.services.comparison.pdf_cloud_dxf_export import _confirmed_zone_ids_for_pair
    records = {
        "k1": _FakeRecord("p1", "z1", "confirmed"),
        "k2": _FakeRecord("p1", "z2", "needs_review"),
        "k3": _FakeRecord("p2", "z3", "confirmed"),  # different pair
        "k4": _FakeRecord("p1", "z4", "confirmed"),
    }
    out = _confirmed_zone_ids_for_pair("p1", records)
    assert out == {"z1", "z4"}


def test_confirmed_zone_selector_empty_records() -> None:
    from src.services.comparison.pdf_cloud_dxf_export import _confirmed_zone_ids_for_pair
    assert _confirmed_zone_ids_for_pair("p1", {}) == set()
    assert _confirmed_zone_ids_for_pair("p1", None) == set()


# ---------------------------------------------------------------------------
# Skip paths
# ---------------------------------------------------------------------------


def test_export_skips_when_no_confirmed_zones(tmp_path: Path) -> None:
    from src.services.comparison.pdf_cloud_dxf_export import export_cloud_marks_to_dxf
    out = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=[],
        review_records={},
        output_dir=tmp_path,
        pdf_path=None,
        pdf_dpi=200.0,
    allowed_output_root=tmp_path,
    )
    assert out.output_path == ""
    assert out.confirmed_zone_count == 0
    assert "확인" in out.skipped_reason


def test_export_skips_when_pdf_missing(tmp_path: Path) -> None:
    """No PDF path → can't compute Y flip → graceful skip."""

    from src.services.comparison.pdf_cloud_dxf_export import export_cloud_marks_to_dxf
    overlays = [{"zone_id": "z1", "after_bbox_px": [10, 10, 100, 100]}]
    records = {"k1": _FakeRecord("p1", "z1", "confirmed")}
    out = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path,
        pdf_path=None,
        pdf_dpi=200.0,
    allowed_output_root=tmp_path,
    )
    assert out.output_path == ""
    assert "페이지 크기" in out.skipped_reason or "DXF" in out.skipped_reason


# ---------------------------------------------------------------------------
# End-to-end DXF generation
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Build a minimal 1-page PDF the export can read for page size."""

    import fitz
    doc = fitz.open()
    # A4 portrait: 595 × 842 pt
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 100), "Sample")
    p = tmp_path / "sample.pdf"
    doc.save(str(p))
    doc.close()
    return p


def test_export_writes_dxf_with_confirmed_zones(
    sample_pdf: Path, tmp_path: Path,
) -> None:
    from src.services.comparison.pdf_cloud_dxf_export import export_cloud_marks_to_dxf
    overlays = [
        {"zone_id": "z1", "after_bbox_px": [100, 100, 300, 200]},
        {"zone_id": "z2", "after_bbox_px": [400, 500, 600, 700]},
    ]
    records = {
        "k1": _FakeRecord("p1", "z1", "confirmed"),
        "k2": _FakeRecord("p1", "z2", "confirmed"),
    }
    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path,
        pdf_path=sample_pdf,
        pdf_dpi=200.0,
    allowed_output_root=tmp_path,
    )
    assert result.skipped_reason == ""
    assert result.output_path
    assert result.confirmed_zone_count == 2
    assert Path(result.output_path).exists()

    # Verify DXF contents
    import ezdxf
    doc = ezdxf.readfile(result.output_path)
    msp = doc.modelspace()
    layers = {l.dxf.name for l in doc.layers}
    assert "CLOUD_MARKS" in layers
    assert "CLOUD_LABELS" in layers
    assert "PDF_PAGE_BOUNDS" in layers

    # Count entities per layer
    # Phase P (RV-20260508-014) — revcloud + revision triangle 도입.
    # CLOUD_MARKS layer 에는 cloud polyline 만 (1 per zone). CLOUD_LABELS
    # 에는 triangle (LWPolyline + Text) + zone label Text. 표준화로 인한
    # 의도된 변경.
    cloud_polylines = list(msp.query("LWPOLYLINE[layer==\"CLOUD_MARKS\"]"))
    assert len(cloud_polylines) == 2  # 2 confirmed zones, 1 revcloud each
    page_bounds = list(msp.query("LWPOLYLINE[layer==\"PDF_PAGE_BOUNDS\"]"))
    assert len(page_bounds) == 1  # page boundary
    # CLOUD_LABELS: 2 triangles (LWPolyline) + 2 triangle digits (Text) +
    # 2 zone labels (Text) = 2 polylines + 4 text
    label_texts = list(msp.query("TEXT[layer==\"CLOUD_LABELS\"]"))
    assert len(label_texts) == 4  # 2 triangle digit + 2 zone label per zone
    triangles = list(msp.query("LWPOLYLINE[layer==\"CLOUD_LABELS\"]"))
    assert len(triangles) == 2  # 1 triangle per zone


def test_export_skips_zones_without_bbox(sample_pdf: Path, tmp_path: Path) -> None:
    from src.services.comparison.pdf_cloud_dxf_export import export_cloud_marks_to_dxf
    overlays = [
        {"zone_id": "z1"},  # no bbox
        {"zone_id": "z2", "after_bbox_px": [10, 10, 100, 100]},
    ]
    records = {
        "k1": _FakeRecord("p1", "z1", "confirmed"),
        "k2": _FakeRecord("p1", "z2", "confirmed"),
    }
    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path,
        pdf_path=sample_pdf,
        pdf_dpi=200.0,
    allowed_output_root=tmp_path,
    )
    # 1 zone written (z2) — z1 silently skipped
    assert result.confirmed_zone_count == 1


def test_export_skips_when_all_zones_lack_bbox(
    sample_pdf: Path, tmp_path: Path,
) -> None:
    from src.services.comparison.pdf_cloud_dxf_export import export_cloud_marks_to_dxf
    overlays = [{"zone_id": "z1"}, {"zone_id": "z2"}]
    records = {
        "k1": _FakeRecord("p1", "z1", "confirmed"),
        "k2": _FakeRecord("p1", "z2", "confirmed"),
    }
    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path,
        pdf_path=sample_pdf,
        pdf_dpi=200.0,
    allowed_output_root=tmp_path,
    )
    assert result.output_path == ""
    assert "좌표 정보가 없" in result.skipped_reason
