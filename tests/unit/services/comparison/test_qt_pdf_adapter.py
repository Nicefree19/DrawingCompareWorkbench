# -*- coding: utf-8 -*-
"""Unit tests for the Phase G2.7 Qt PDF adapter.

Covers ``qt_pdf_adapter`` which wraps QPdfDocument so the lightweight
viewport can re-render PDF pages at zoom-appropriate DPI without going
through PyMuPDF.

These tests need a live QApplication / QCoreApplication for PySide6
QPdfDocument to function. We auto-bootstrap one at module load.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _pyside6_is_mocked() -> bool:
    """Detect when an earlier test in the suite (notably
    ``test_dwg_differ_cleanup.py``) replaced PySide6 modules with
    ``MagicMock`` at import time. We can't safely recover by clearing
    ``sys.modules`` — that triggers a recursive import / stack overflow
    on Windows because PySide6's package init isn't reentrant — so the
    pragmatic answer is to skip with a clear reason.

    Run ``pytest tests/unit/services/comparison/test_qt_pdf_adapter.py``
    in isolation to exercise the real Qt PDF backend. The proper long-
    term fix is in ``test_dwg_differ_cleanup.py`` (move its module-level
    sys.modules mocking into a fixture with proper teardown).
    """

    from unittest.mock import MagicMock
    for name in ("PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
        mod = sys.modules.get(name)
        if mod is not None and isinstance(mod, MagicMock):
            return True
    return False


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """Bootstrap a QCoreApplication for the QPdfDocument backend."""

    if _pyside6_is_mocked():
        pytest.skip(
            "PySide6 is mocked by an earlier test in the same suite "
            "(test_dwg_differ_cleanup polluted sys.modules); run "
            "test_qt_pdf_adapter.py in isolation to exercise the real "
            "Qt PDF backend",
            allow_module_level=True,
        )
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance()
    if app is None:
        # Use QApplication for safety — some PySide6 builds wire QtPdf
        # through the GUI module even though it doesn't render to screen.
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Build a tiny single-page PDF via PyMuPDF for the renderer to load."""

    import fitz
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 in PDF points
    page.insert_text((50, 100), "Sample PDF for adapter test", fontsize=14)
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def multipage_pdf(tmp_path: Path) -> Path:
    """3-page PDF — covers page-index variation."""

    import fitz
    pdf_path = tmp_path / "multi.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 100), f"Page {i + 1}", fontsize=14)
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


# ---------------------------------------------------------------------------
# is_qt_pdf_available — environment probe
# ---------------------------------------------------------------------------


def test_is_qt_pdf_available_returns_bool() -> None:
    from src.services.comparison.qt_pdf_adapter import is_qt_pdf_available
    out = is_qt_pdf_available()
    assert isinstance(out, bool)
    # In our test runtime PySide6.QtPdf is installed → expect True
    assert out is True


# ---------------------------------------------------------------------------
# PdfPageRenderer — happy path
# ---------------------------------------------------------------------------


def test_renderer_loads_and_reports_page_count(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(sample_pdf) as r:
        assert r.is_loaded
        assert r.is_available
        assert r.page_count() == 1


def test_renderer_reports_page_size_in_points_and_inches(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer, PDF_BASE_DPI
    with PdfPageRenderer(sample_pdf) as r:
        w_pts, h_pts = r.page_size_points(0)
        # A4 ≈ 595 × 842 points
        assert 590 <= w_pts <= 600
        assert 835 <= h_pts <= 850

        w_in, h_in = r.page_size_inches(0)
        assert pytest.approx(w_in, rel=0.001) == w_pts / PDF_BASE_DPI
        assert pytest.approx(h_in, rel=0.001) == h_pts / PDF_BASE_DPI
        # A4 ≈ 8.27 × 11.69 inches
        assert 8.2 < w_in < 8.4
        assert 11.6 < h_in < 11.8


def test_render_page_at_100_dpi_produces_expected_pixel_size(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(sample_pdf) as r:
        img = r.render_page(0, target_dpi=100)
        assert img is not None
        assert not img.isNull()
        # A4 at 100 DPI ≈ 827 × 1169 px (rounding tolerance)
        assert 820 <= img.width() <= 830
        assert 1160 <= img.height() <= 1175


def test_render_page_higher_dpi_produces_proportionally_larger_image(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(sample_pdf) as r:
        small = r.render_page(0, target_dpi=100)
        large = r.render_page(0, target_dpi=300)
        # 3× DPI → ~3× pixels per dimension
        assert large.width() / small.width() == pytest.approx(3.0, rel=0.02)
        assert large.height() / small.height() == pytest.approx(3.0, rel=0.02)


def test_render_caps_extreme_dpi_within_safety_bound(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(sample_pdf) as r:
        img = r.render_page(0, target_dpi=10000)  # would be ~83000 px otherwise
        assert max(img.width(), img.height()) <= 8192


def test_render_minimum_dpi_clamped_to_10(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(sample_pdf) as r:
        # DPI 0 / negative should clamp to 10 internally — never zero size
        img = r.render_page(0, target_dpi=0)
        assert not img.isNull()
        assert img.width() > 0 and img.height() > 0


# ---------------------------------------------------------------------------
# Multi-page handling
# ---------------------------------------------------------------------------


def test_multipage_pdf_reports_correct_page_count(multipage_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(multipage_pdf) as r:
        assert r.page_count() == 3


def test_render_each_page_independently(multipage_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(multipage_pdf) as r:
        for idx in range(3):
            img = r.render_page(idx, target_dpi=72)
            assert not img.isNull(), f"page {idx} render returned null"
            assert img.width() > 0 and img.height() > 0


# ---------------------------------------------------------------------------
# Error / boundary handling — must not raise
# ---------------------------------------------------------------------------


def test_missing_pdf_returns_zero_pages(tmp_path: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    missing = tmp_path / "nope.pdf"
    with PdfPageRenderer(missing) as r:
        assert r.page_count() == 0
        assert r.page_size_inches(0) == (0.0, 0.0)
        img = r.render_page(0, target_dpi=100)
        assert img is None or img.isNull()


def test_negative_page_index_returns_empty_image(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(sample_pdf) as r:
        img = r.render_page(-1, target_dpi=100)
        assert img.isNull()


def test_out_of_range_page_index_returns_empty_image(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(sample_pdf) as r:
        img = r.render_page(99, target_dpi=100)
        assert img.isNull()


def test_close_releases_document_handle(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    r = PdfPageRenderer(sample_pdf)
    assert r.is_available  # triggers load
    assert r.is_loaded
    r.close()
    assert not r.is_loaded


def test_context_manager_calls_close(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(sample_pdf) as r:
        assert r.is_loaded
    # After __exit__, doc handle should be released
    assert not r.is_loaded


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def test_render_pdf_page_once_renders_in_one_call(sample_pdf: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import render_pdf_page_once
    img = render_pdf_page_once(sample_pdf, 0, target_dpi=100)
    assert not img.isNull()
    assert img.width() > 0


def test_render_pdf_page_once_handles_missing_file(tmp_path: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import render_pdf_page_once
    img = render_pdf_page_once(tmp_path / "missing.pdf", 0, target_dpi=100)
    assert img is None or img.isNull()


# ---------------------------------------------------------------------------
# Re-render correctness — same input, same output (deterministic at fixed DPI)
# ---------------------------------------------------------------------------


def test_repeated_render_is_deterministic(sample_pdf: Path) -> None:
    """Re-rendering the same page at the same DPI should give identical
    pixel dimensions (Qt may differ in pixel content slightly across
    runs due to anti-aliasing, but dimensions are stable)."""

    from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
    with PdfPageRenderer(sample_pdf) as r:
        a = r.render_page(0, target_dpi=120)
        b = r.render_page(0, target_dpi=120)
        assert a.width() == b.width()
        assert a.height() == b.height()


# ---------------------------------------------------------------------------
# select_pdf_render_dpi — pure helper, no Qt needed but lives in same module
# ---------------------------------------------------------------------------


def test_select_dpi_no_change_at_zoom_1() -> None:
    """Initial fit-to-view: no re-render needed."""

    from src.services.comparison.qt_pdf_adapter import select_pdf_render_dpi
    assert select_pdf_render_dpi(
        base_upp=1.0, current_upp=1.0, base_dpi=150, current_dpi=150,
    ) is None


def test_select_dpi_no_change_below_threshold() -> None:
    """Tiny zoom (≤1.3×) should NOT trigger a re-render — stay smooth."""

    from src.services.comparison.qt_pdf_adapter import select_pdf_render_dpi
    # zoom 1.2× — within the no-thrash threshold
    out = select_pdf_render_dpi(
        base_upp=1.0, current_upp=1.0 / 1.2, base_dpi=150, current_dpi=150,
    )
    assert out is None


def test_select_dpi_upgrades_above_threshold() -> None:
    """Zoom > 1.3× should pick a higher-DPI bucket."""

    from src.services.comparison.qt_pdf_adapter import select_pdf_render_dpi
    out = select_pdf_render_dpi(
        base_upp=1.0, current_upp=0.5, base_dpi=150, current_dpi=150,
    )
    assert out is not None
    assert out > 150
    # 2× zoom × 150 base × 1.5 oversample = 450 → bucket 450
    assert out == 450.0


def test_select_dpi_extreme_zoom_capped() -> None:
    """Very high zoom should clamp at max_dpi (default 600)."""

    from src.services.comparison.qt_pdf_adapter import select_pdf_render_dpi
    out = select_pdf_render_dpi(
        base_upp=1.0, current_upp=0.001, base_dpi=150,
        current_dpi=150, max_dpi=600,
    )
    assert out == 600.0


def test_select_dpi_already_at_higher_dpi_returns_none() -> None:
    """If a previous re-render already pushed to a high DPI, don't
    downgrade when the user zooms back out a little."""

    from src.services.comparison.qt_pdf_adapter import select_pdf_render_dpi
    out = select_pdf_render_dpi(
        base_upp=1.0, current_upp=0.5, base_dpi=150, current_dpi=600,
    )
    # 2× zoom would target 450, but current 600 is already higher → no change
    assert out is None


def test_select_dpi_zoom_out_returns_none() -> None:
    """Zooming below fit-to-view never down-rezzes the existing render."""

    from src.services.comparison.qt_pdf_adapter import select_pdf_render_dpi
    out = select_pdf_render_dpi(
        base_upp=1.0, current_upp=2.0, base_dpi=150, current_dpi=150,
    )
    assert out is None


def test_select_dpi_invalid_inputs_return_none() -> None:
    from src.services.comparison.qt_pdf_adapter import select_pdf_render_dpi
    # Negative / zero base_upp
    assert select_pdf_render_dpi(base_upp=0, current_upp=1, base_dpi=150) is None
    assert select_pdf_render_dpi(base_upp=-1, current_upp=1, base_dpi=150) is None
    # Negative / zero current_upp
    assert select_pdf_render_dpi(base_upp=1, current_upp=0, base_dpi=150) is None
    # Zero base_dpi
    assert select_pdf_render_dpi(base_upp=1, current_upp=0.5, base_dpi=0) is None


def test_select_dpi_snaps_to_buckets() -> None:
    """Targets always snap to one of the predefined buckets, never to
    arbitrary values, so cache keys stay reusable."""

    from src.services.comparison.qt_pdf_adapter import (
        select_pdf_render_dpi, PDF_DPI_BUCKETS,
    )
    # Try several zooms; the result must be in PDF_DPI_BUCKETS
    for zoom_factor in (1.4, 1.7, 2.0, 2.5, 3.0, 4.0, 5.0):
        out = select_pdf_render_dpi(
            base_upp=1.0, current_upp=1.0 / zoom_factor,
            base_dpi=150, current_dpi=150, max_dpi=1200,
        )
        if out is not None:
            assert out in PDF_DPI_BUCKETS, (
                f"zoom={zoom_factor}× returned non-bucket DPI {out}"
            )


def test_select_dpi_respects_max_dpi_below_buckets() -> None:
    """A custom low max_dpi should still cap correctly even when the
    snapped bucket would exceed it."""

    from src.services.comparison.qt_pdf_adapter import select_pdf_render_dpi
    out = select_pdf_render_dpi(
        base_upp=1.0, current_upp=0.1,  # extreme zoom
        base_dpi=150, current_dpi=150, max_dpi=300,
    )
    assert out is not None
    assert out <= 300.0


# ---------------------------------------------------------------------------
# prune_pdf_cache — disk LRU
# ---------------------------------------------------------------------------


def test_prune_returns_zero_when_dir_missing(tmp_path: Path) -> None:
    from src.services.comparison.qt_pdf_adapter import prune_pdf_cache
    deleted = prune_pdf_cache(tmp_path / "nonexistent", max_bytes=1024)
    assert deleted == 0


def test_prune_returns_zero_when_under_cap(tmp_path: Path) -> None:
    """No deletes when total size already under the cap."""

    from src.services.comparison.qt_pdf_adapter import prune_pdf_cache
    (tmp_path / "qtpdf_a.png").write_bytes(b"x" * 100)
    (tmp_path / "qtpdf_b.png").write_bytes(b"x" * 100)
    deleted = prune_pdf_cache(tmp_path, max_bytes=1024)
    assert deleted == 0
    # Files still present
    assert (tmp_path / "qtpdf_a.png").exists()
    assert (tmp_path / "qtpdf_b.png").exists()


def test_prune_evicts_oldest_first(tmp_path: Path) -> None:
    """When over cap, oldest-mtime files go first."""

    import os
    import time
    from src.services.comparison.qt_pdf_adapter import prune_pdf_cache

    old_file = tmp_path / "qtpdf_old.png"
    new_file = tmp_path / "qtpdf_new.png"
    old_file.write_bytes(b"x" * 600)
    time.sleep(0.05)  # ensure mtime distinguishable
    new_file.write_bytes(b"x" * 600)
    # Force old_file mtime well in the past
    old_mtime = time.time() - 10_000
    os.utime(old_file, (old_mtime, old_mtime))

    # Cap at 800 bytes — the 1200 total exceeds, must evict the older
    deleted = prune_pdf_cache(tmp_path, max_bytes=800)
    assert deleted == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_prune_only_targets_glob_pattern(tmp_path: Path) -> None:
    """Files outside the qtpdf_*.png pattern are never deleted."""

    from src.services.comparison.qt_pdf_adapter import prune_pdf_cache
    (tmp_path / "qtpdf_a.png").write_bytes(b"x" * 5000)
    (tmp_path / "other.png").write_bytes(b"x" * 5000)
    (tmp_path / "config.json").write_bytes(b"x" * 5000)
    # Cap forces eviction of both qtpdf entries — but only qtpdf is touched
    prune_pdf_cache(tmp_path, max_bytes=100)
    assert not (tmp_path / "qtpdf_a.png").exists()
    assert (tmp_path / "other.png").exists()
    assert (tmp_path / "config.json").exists()


def test_prune_handles_unreadable_files_gracefully(tmp_path: Path) -> None:
    """Permission errors must not crash the prune call."""

    from src.services.comparison.qt_pdf_adapter import prune_pdf_cache
    # Just ensure the function returns an int and doesn't raise
    (tmp_path / "qtpdf_a.png").write_bytes(b"x" * 5000)
    deleted = prune_pdf_cache(tmp_path, max_bytes=100)
    assert isinstance(deleted, int)
    assert deleted >= 0
