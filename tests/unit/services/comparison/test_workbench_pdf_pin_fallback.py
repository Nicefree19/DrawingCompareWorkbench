# -*- coding: utf-8 -*-
"""Tests for the PDF page-level pin fallback used when zone bbox is missing.

PDF-PDF comparison can produce change zones that lack precise ``image_pixels``
coordinates (older renders, OCR-only diffs, etc.). The workbench compensates by
synthesizing a small bbox at the page center and pushing it through the GPU
viewport with ``pin_only=True`` so the focus marker still renders. These tests
exercise the pure helpers that build the synthetic overlay; the QML rendering is
covered by the cloud/focus split tests.
"""

from __future__ import annotations

import pytest

from src.gui.drawing_compare_workbench import (
    _is_redacted_artifact_path,
    _resolve_pdf_viewer_source_path,
    _resolve_viewer_artifact_path,
    build_overlay_entries,
    compute_pdf_page_pin_overlay,
    scale_pdf_bbox_to_render_pixels,
)
from src.services.comparison.viewer_package import _pdf_page_size_from_transforms


def test_compute_pin_overlay_centers_pin_on_page() -> None:
    base = {"zone_id": "z1", "change_type": "modified"}
    page = {"width": 1200.0, "height": 900.0}
    enriched = compute_pdf_page_pin_overlay(base, page)
    assert enriched is not None
    assert enriched["pin_only"] is True
    assert enriched["pdf_page_pin"] is True
    bbox = enriched["bbox"]
    # Center pin is small (default 200x150) and centered on the 1200x900 page.
    assert bbox["min_x"] == pytest.approx(500.0)
    assert bbox["max_x"] == pytest.approx(700.0)
    assert bbox["min_y"] == pytest.approx(375.0)
    assert bbox["max_y"] == pytest.approx(525.0)
    after_px = enriched["after_bbox_px"]
    assert after_px["x"] == pytest.approx(500.0)
    assert after_px["y"] == pytest.approx(375.0)
    assert after_px["width"] == pytest.approx(200.0)
    assert after_px["height"] == pytest.approx(150.0)


def test_compute_pin_overlay_returns_none_for_invalid_page_size() -> None:
    base = {"zone_id": "z1", "change_type": "added"}
    assert compute_pdf_page_pin_overlay(base, {"width": 0, "height": 0}) is None
    assert compute_pdf_page_pin_overlay(base, {}) is None
    assert compute_pdf_page_pin_overlay(base, None) is None  # type: ignore[arg-type]


def test_compute_pin_overlay_clamps_to_half_page_for_tiny_pages() -> None:
    base = {"zone_id": "z1", "change_type": "added"}
    page = {"width": 80.0, "height": 60.0}
    enriched = compute_pdf_page_pin_overlay(base, page)
    assert enriched is not None
    bbox = enriched["bbox"]
    # Pin width should clamp to 40 (page * 0.5 = 40), height to 30 — the explicit
    # minimums in the helper guarantee a visible marker even on small pages.
    assert bbox["max_x"] - bbox["min_x"] == pytest.approx(40.0)
    assert bbox["max_y"] - bbox["min_y"] == pytest.approx(30.0)


def test_pin_only_overlay_emits_focus_marker_only() -> None:
    base = {"zone_id": "z1", "change_type": "added"}
    enriched = compute_pdf_page_pin_overlay(base, {"width": 600.0, "height": 400.0})
    assert enriched is not None
    rect = (
        enriched["after_bbox_px"]["x"],
        enriched["after_bbox_px"]["y"],
        enriched["after_bbox_px"]["width"],
        enriched["after_bbox_px"]["height"],
    )
    entries = build_overlay_entries(
        zone_id="z1",
        rect=rect,
        change_type="added",
        label="z1",
        selected=True,
        before=False,
        pin_only=True,
    )
    assert len(entries) == 1
    only = entries[0]
    assert only["role"] == "focus"
    assert only["pinOnly"] is True
    assert only["matchSide"] == "b_only"
    assert only["pinX"] == pytest.approx(rect[0] + rect[2] / 2.0)


def test_pdf_page_size_from_transforms_uses_img_dimensions() -> None:
    after_transform = {"img_width": 1240, "img_height": 1754, "max_x": 1240.0, "max_y": 1754.0}
    size = _pdf_page_size_from_transforms(after_transform, None)
    assert size == {"width": 1240.0, "height": 1754.0}


def test_pdf_page_size_falls_back_to_max_extent_when_img_dims_missing() -> None:
    transform = {"min_x": 0, "min_y": 0, "max_x": 800.0, "max_y": 600.0}
    size = _pdf_page_size_from_transforms(transform, None)
    assert size == {"width": 800.0, "height": 600.0}


def test_pdf_page_size_returns_none_when_no_render_transform_available() -> None:
    assert _pdf_page_size_from_transforms(None, None) is None
    assert _pdf_page_size_from_transforms({}, {}) is None
    assert _pdf_page_size_from_transforms({"max_x": 0}, {"max_y": 0}) is None


def test_pdf_page_size_prefers_after_transform_when_both_present() -> None:
    after = {"img_width": 1000, "img_height": 700}
    before = {"img_width": 800, "img_height": 600}
    size = _pdf_page_size_from_transforms(after, before)
    assert size == {"width": 1000.0, "height": 700.0}


def test_redacted_artifact_paths_are_detected_before_filesystem_use() -> None:
    assert _is_redacted_artifact_path("<redacted>/S-2401.pdf") is True
    assert _is_redacted_artifact_path("<redacted>\\viewer\\zone_vector") is True
    assert _is_redacted_artifact_path("/redacted/S-2401.pdf") is True
    assert _is_redacted_artifact_path("viewer/marked_pdf/pair.pdf") is False


def test_sharable_viewer_paths_resolve_against_package_root(tmp_path) -> None:
    viewer_root = tmp_path / "viewer"
    image = viewer_root / "images" / "pair_after.png"
    overlay = viewer_root / "overlays" / "pair.json"
    image.parent.mkdir(parents=True)
    overlay.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    overlay.write_text("{}", encoding="utf-8")

    assert _resolve_viewer_artifact_path("viewer/images/pair_after.png", viewer_root) == image
    assert _resolve_viewer_artifact_path("images/pair_after.png", viewer_root) == image
    assert _resolve_viewer_artifact_path("viewer/overlays/pair.json", viewer_root) == overlay
    assert _resolve_viewer_artifact_path("<redacted>/pair.pdf", viewer_root) is None


def test_pdf_lightweight_uses_packaged_pdf_when_sources_are_redacted(tmp_path) -> None:
    viewer_root = tmp_path / "viewer"
    pages = viewer_root / "pages"
    pages.mkdir(parents=True)
    before_pdf = pages / "pair_before.pdf"
    after_pdf = pages / "pair_after.pdf"
    before_pdf.write_bytes(b"%PDF-1.4 before")
    after_pdf.write_bytes(b"%PDF-1.4 after")
    viewer_pair = {
        "source_a": "<redacted>/01.3PG1.pdf",
        "source_b": "<redacted>/02.3PG1_R1.pdf",
        "before_page_pdf": "viewer/pages/pair_before.pdf",
        "after_page_pdf": "viewer/pages/pair_after.pdf",
        "page_pdf": "viewer/pages/pair_after.pdf",
    }

    before, before_key = _resolve_pdf_viewer_source_path(viewer_pair, "before", viewer_root)
    after, after_key = _resolve_pdf_viewer_source_path(viewer_pair, "after", viewer_root)

    assert before == before_pdf
    assert before_key == "before_page_pdf"
    assert after == after_pdf
    assert after_key == "after_page_pdf"


def test_pdf_lightweight_keeps_legacy_page_pdf_fallback_for_redacted_sources(tmp_path) -> None:
    viewer_root = tmp_path / "viewer"
    page_pdf = viewer_root / "pages" / "pair.pdf"
    page_pdf.parent.mkdir(parents=True)
    page_pdf.write_bytes(b"%PDF-1.4")
    viewer_pair = {
        "source_a": "<redacted>/old.pdf",
        "source_b": "<redacted>/new.pdf",
        "page_pdf": "viewer/pages/pair.pdf",
    }

    before, before_key = _resolve_pdf_viewer_source_path(viewer_pair, "before", viewer_root)
    after, after_key = _resolve_pdf_viewer_source_path(viewer_pair, "after", viewer_root)

    assert before == page_pdf
    assert before_key == "page_pdf"
    assert after == page_pdf
    assert after_key == "page_pdf"


def test_pdf_bbox_scales_to_render_background_dpi() -> None:
    overlay = {
        "bbox_coordinate_space": "image_pixels",
        "pdf_dpi": 200,
    }
    viewer_pair = {
        "coordinate_source": "image_pixels",
        "after_transform": {"coordinate_space": "image_pixels", "dpi": 400},
    }

    assert scale_pdf_bbox_to_render_pixels(
        [1304, 1267, 1414, 1442],
        overlay,
        viewer_pair,
    ) == pytest.approx((2608, 2534, 2828, 2884))
