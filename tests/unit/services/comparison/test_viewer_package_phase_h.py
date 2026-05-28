# -*- coding: utf-8 -*-
"""Unit tests for the Phase G2.7-FU + Phase H integration in viewer_package.

Covers:
    - ``_render_pdf_to_png`` honours ``page_index`` (renders the right page)
    - ``_render_pdf_to_png`` clamps out-of-range page_index to 0 + warns
    - ``_render_pair_backgrounds`` threads page_a/page_b to the renderer
    - ``_primary_page_pair_for_pair`` extracts (page_a, page_b) from
      change-zone rows (top-level + nested metadata; defaults to 0,0)

Pure Python — no Qt needed (uses PyMuPDF directly to render and inspect).
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def multipage_pdf(tmp_path: Path) -> Path:
    """3-page PDF with a distinctive marker per page so the test can
    verify which page was rendered (via pixel inspection)."""

    import fitz
    pdf_path = tmp_path / "multi.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=200, height=100)
        # Each page filled with a different gray level so we can detect
        # which page got rendered by sampling a pixel.
        page.insert_text(
            (10, 50), f"PAGE {i}",
            fontsize=24, color=(0.2 * (i + 1), 0.2 * (i + 1), 0.2 * (i + 1)),
        )
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


# ---------------------------------------------------------------------------
# _render_pdf_to_png — page_index parameter
# ---------------------------------------------------------------------------


def test_render_pdf_to_png_default_page_zero(multipage_pdf: Path, tmp_path: Path) -> None:
    """Default behavior unchanged: page 0 rendered."""

    from src.services.comparison.viewer_package import _render_pdf_to_png
    out = _render_pdf_to_png(multipage_pdf, tmp_path / "p0.png", dpi=72, max_edge_px=500)
    assert out["page"] == 0
    assert out["img_width"] > 0


def test_render_pdf_to_png_explicit_page_index(multipage_pdf: Path, tmp_path: Path) -> None:
    """Passing page_index=2 should render page 2."""

    from src.services.comparison.viewer_package import _render_pdf_to_png
    out = _render_pdf_to_png(multipage_pdf, tmp_path / "p2.png", dpi=72, max_edge_px=500, page_index=2)
    assert out["page"] == 2


def test_render_pdf_to_png_records_effective_dpi_when_edge_capped(
    multipage_pdf: Path, tmp_path: Path,
) -> None:
    """Transforms must expose the actual raster DPI, not just the requested DPI."""

    from src.services.comparison.viewer_package import _render_pdf_to_png

    out = _render_pdf_to_png(
        multipage_pdf,
        tmp_path / "capped.png",
        dpi=144,
        max_edge_px=100,
        page_index=0,
    )

    assert out["requested_dpi"] == pytest.approx(144.0)
    assert out["effective_dpi"] == pytest.approx(36.0)
    assert out["dpi"] == pytest.approx(out["effective_dpi"])
    assert out["pdf_dpi"] == pytest.approx(out["effective_dpi"])
    assert out["render_scale"] == pytest.approx(0.5)


def test_worker_render_pdf_to_png_matches_effective_dpi_contract(
    multipage_pdf: Path, tmp_path: Path,
) -> None:
    """The killable worker must emit the same PDF transform schema."""

    from src.services.comparison.viewer_render_worker import (
        _render_pdf_to_png as _render_pdf_to_png_worker,
    )

    out = _render_pdf_to_png_worker(
        multipage_pdf,
        tmp_path / "worker_capped.png",
        dpi=144,
        max_edge_px=100,
        page_index=0,
    )

    assert out["requested_dpi"] == pytest.approx(144.0)
    assert out["effective_dpi"] == pytest.approx(36.0)
    assert out["dpi"] == pytest.approx(out["effective_dpi"])
    assert out["pdf_dpi"] == pytest.approx(out["effective_dpi"])
    assert out["render_scale"] == pytest.approx(0.5)


def test_render_pdf_to_png_clamps_oor_index(multipage_pdf: Path, tmp_path: Path) -> None:
    """Out-of-range page_index should clamp to 0 with a warning."""

    from src.services.comparison.viewer_package import _render_pdf_to_png
    out = _render_pdf_to_png(multipage_pdf, tmp_path / "oor.png", dpi=72, max_edge_px=500, page_index=99)
    assert out["page"] == 0


def test_render_pdf_to_png_negative_index_clamps(multipage_pdf: Path, tmp_path: Path) -> None:
    """Negative page_index should also clamp to 0."""

    from src.services.comparison.viewer_package import _render_pdf_to_png
    out = _render_pdf_to_png(multipage_pdf, tmp_path / "neg.png", dpi=72, max_edge_px=500, page_index=-5)
    assert out["page"] == 0


def test_render_pdf_to_png_renders_different_pages_differently(
    multipage_pdf: Path, tmp_path: Path,
) -> None:
    """Pages with different content should produce different PNG files
    (sanity that page_index actually influences output, not just metadata)."""

    from src.services.comparison.viewer_package import _render_pdf_to_png
    p0 = tmp_path / "p0.png"
    p2 = tmp_path / "p2.png"
    _render_pdf_to_png(multipage_pdf, p0, dpi=120, max_edge_px=500, page_index=0)
    _render_pdf_to_png(multipage_pdf, p2, dpi=120, max_edge_px=500, page_index=2)
    # Same file size or pixel content rules out (1) wrong page renders
    assert p0.read_bytes() != p2.read_bytes(), \
        "different pages should produce different PNG content"


# ---------------------------------------------------------------------------
# _render_pair_backgrounds — page_a/page_b plumbing
# ---------------------------------------------------------------------------


def test_render_pair_backgrounds_default_pages(multipage_pdf: Path, tmp_path: Path) -> None:
    """Default page_a/page_b = 0,0 — both backgrounds render page 0."""

    from src.services.comparison.viewer_package import _render_pair_backgrounds
    out = _render_pair_backgrounds(
        pair_id="test_default",
        source_a=multipage_pdf,
        source_b=multipage_pdf,
        image_dir=tmp_path,
        dxf_cache_dir=tmp_path / "dxf",
        dpi=72, max_edge_px=500,
    )
    assert out["render_status"] == "rendered"
    assert out["before_transform"]["page"] == 0
    assert out["after_transform"]["page"] == 0


def test_render_pair_backgrounds_distinct_pages(multipage_pdf: Path, tmp_path: Path) -> None:
    """page_a=1, page_b=2 → before renders page 1, after renders page 2."""

    from src.services.comparison.viewer_package import _render_pair_backgrounds
    out = _render_pair_backgrounds(
        pair_id="test_distinct",
        source_a=multipage_pdf,
        source_b=multipage_pdf,
        image_dir=tmp_path,
        dxf_cache_dir=tmp_path / "dxf",
        dpi=72, max_edge_px=500,
        page_a=1, page_b=2,
    )
    assert out["before_transform"]["page"] == 1
    assert out["after_transform"]["page"] == 2


def test_render_pair_backgrounds_pdf_oor_clamps(multipage_pdf: Path, tmp_path: Path) -> None:
    """page_a out of range still produces a render (clamped to 0) and
    doesn't raise."""

    from src.services.comparison.viewer_package import _render_pair_backgrounds
    out = _render_pair_backgrounds(
        pair_id="test_oor",
        source_a=multipage_pdf,
        source_b=multipage_pdf,
        image_dir=tmp_path,
        dxf_cache_dir=tmp_path / "dxf",
        dpi=72, max_edge_px=500,
        page_a=99, page_b=2,
    )
    assert out["render_status"] == "rendered"
    assert out["before_transform"]["page"] == 0  # clamped
    assert out["after_transform"]["page"] == 2  # unaffected


# ---------------------------------------------------------------------------
# _primary_page_pair_for_pair — extract from change-zone rows
# ---------------------------------------------------------------------------


def test_render_pair_backgrounds_keeps_one_sided_pdf_blank(
    multipage_pdf: Path, tmp_path: Path,
) -> None:
    from src.services.comparison.viewer_package import _render_pair_backgrounds

    out = _render_pair_backgrounds(
        pair_id="one-sided",
        source_a=multipage_pdf,
        source_b=multipage_pdf,
        image_dir=tmp_path,
        dxf_cache_dir=tmp_path / "dxf",
        dpi=72, max_edge_px=500,
        page_a=1, page_b=-1,
    )

    assert out["render_status"] == "rendered"
    assert out["before_transform"]["page"] == 1
    assert out["after_image"] == ""
    assert out["after_transform"] is None


def test_primary_page_pair_empty_rows() -> None:
    from src.services.comparison.viewer_package import _primary_page_pair_for_pair
    assert _primary_page_pair_for_pair([]) == (0, 0)


def test_primary_page_pair_no_metadata() -> None:
    """Rows without page_a/page_b → default (0, 0)."""

    from src.services.comparison.viewer_package import _primary_page_pair_for_pair
    rows = [{"zone_id": "z1"}, {"zone_id": "z2"}]
    assert _primary_page_pair_for_pair(rows) == (0, 0)


def test_primary_page_pair_top_level_keys() -> None:
    """page_a/page_b at the top level of a row dict are picked up."""

    from src.services.comparison.viewer_package import _primary_page_pair_for_pair
    rows = [{"zone_id": "z1", "page_a": 2, "page_b": 5}]
    assert _primary_page_pair_for_pair(rows) == (2, 5)


def test_primary_page_pair_nested_metadata() -> None:
    """page_a/page_b nested under metadata are picked up too."""

    from src.services.comparison.viewer_package import _primary_page_pair_for_pair
    rows = [{"zone_id": "z1", "metadata": {"page_a": 1, "page_b": 4}}]
    assert _primary_page_pair_for_pair(rows) == (1, 4)


def test_primary_page_pair_returns_first_match() -> None:
    """Multiple rows with different page pairs → take the first."""

    from src.services.comparison.viewer_package import _primary_page_pair_for_pair
    rows = [
        {"zone_id": "z1", "page_a": 0, "page_b": 2},
        {"zone_id": "z2", "page_a": 1, "page_b": 0},
        {"zone_id": "z3", "page_a": 2, "page_b": 1},
    ]
    assert _primary_page_pair_for_pair(rows) == (0, 2)


def test_primary_page_pair_preserves_negative_sentinel() -> None:
    """Negative sentinels model one-sided PDF page matches."""

    from src.services.comparison.viewer_package import _primary_page_pair_for_pair
    rows = [{"zone_id": "z1", "page_a": -1, "page_b": 3}]
    assert _primary_page_pair_for_pair(rows) == (-1, 3)


def test_primary_page_pair_skips_invalid_then_finds_valid() -> None:
    """Bad rows (non-dict, missing keys, non-int values) get skipped;
    walker keeps looking for the first usable pair."""

    from src.services.comparison.viewer_package import _primary_page_pair_for_pair
    rows = [
        "garbage",  # type: ignore[list-item]
        {"zone_id": "z0"},  # no page info
        {"zone_id": "z1", "page_a": "not-an-int"},  # bad value
        {"zone_id": "z2", "page_a": 1, "page_b": 2},
    ]
    assert _primary_page_pair_for_pair(rows) == (1, 2)


# ---------------------------------------------------------------------------
# _all_page_pairs_for_pair — multi-page navigation list
# ---------------------------------------------------------------------------


def test_all_page_pairs_empty_rows() -> None:
    from src.services.comparison.viewer_package import _all_page_pairs_for_pair
    assert _all_page_pairs_for_pair([]) == []


def test_all_page_pairs_no_metadata_returns_empty() -> None:
    """Rows without page_a/page_b → empty list (DXF / single-page)."""

    from src.services.comparison.viewer_package import _all_page_pairs_for_pair
    assert _all_page_pairs_for_pair([{"zone_id": "z1"}, {"zone_id": "z2"}]) == []


def test_all_page_pairs_dedupes_and_sorts() -> None:
    """Multiple zones on the same page should collapse to one entry."""

    from src.services.comparison.viewer_package import _all_page_pairs_for_pair
    rows = [
        {"zone_id": "z1", "page_a": 0, "page_b": 2},
        {"zone_id": "z2", "page_a": 0, "page_b": 2},  # dup
        {"zone_id": "z3", "page_a": 1, "page_b": 0},
        {"zone_id": "z4", "page_a": 2, "page_b": 1},
    ]
    out = _all_page_pairs_for_pair(rows)
    assert out == [
        {"page_a": 0, "page_b": 2},
        {"page_a": 1, "page_b": 0},
        {"page_a": 2, "page_b": 1},
    ]


def test_all_page_pairs_preserves_negative_sentinels() -> None:
    """Unmatched-side sentinels are first-class page navigation targets."""

    from src.services.comparison.viewer_package import _all_page_pairs_for_pair
    rows = [
        {"zone_id": "z1", "page_a": 0, "page_b": -1},  # unmatched B side
        {"zone_id": "z2", "page_a": 1, "page_b": 2},
    ]
    out = _all_page_pairs_for_pair(rows)
    assert out == [{"page_a": 0, "page_b": -1}, {"page_a": 1, "page_b": 2}]


def test_all_page_pairs_nested_metadata() -> None:
    from src.services.comparison.viewer_package import _all_page_pairs_for_pair
    rows = [
        {"zone_id": "z1", "metadata": {"page_a": 1, "page_b": 4}},
        {"zone_id": "z2", "metadata": {"page_a": 0, "page_b": 0}},
    ]
    out = _all_page_pairs_for_pair(rows)
    assert out == [
        {"page_a": 0, "page_b": 0},
        {"page_a": 1, "page_b": 4},
    ]


def test_overlay_from_zone_row_carries_pdf_page_contract() -> None:
    from src.services.comparison.viewer_package import _overlay_from_zone_row

    row = {
        "pair_id": "pdf-pair",
        "zone_id": "C-001",
        "change_type": "modified",
        "raw_change_count": "1",
        "bbox_min_x": "10",
        "bbox_min_y": "20",
        "bbox_max_x": "30",
        "bbox_max_y": "40",
        "page_a": "2",
        "page_b": "5",
        "page_match_status": "auto_confirmed",
        "page_match_score": "0.94",
    }

    overlay = _overlay_from_zone_row(
        row,
        (0, 0, 100, 100),
        priority=None,
        selected=False,
        before_transform=None,
        after_transform=None,
        bbox_coordinate_space="image_pixels",
        pdf_dpi=150.0,
    )

    assert overlay["page_a"] == 2
    assert overlay["page_b"] == 5
    assert overlay["pdf_page"] == 2
    assert overlay["page_match_status"] == "auto_confirmed"
    assert overlay["page_match_score"] == "0.94"
