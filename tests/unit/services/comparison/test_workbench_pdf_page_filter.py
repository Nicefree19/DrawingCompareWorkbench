# -*- coding: utf-8 -*-
"""Unit tests for the Phase H multi-page navigation overlay filter.

Covers ``_filter_overlays_by_pdf_pages`` — the pure helper that drives
the workbench's per-page-pair filtering when the user navigates between
matched page pairs of a multi-page PDF comparison.

Pure Python; no Qt needed.
"""

from __future__ import annotations

import pytest

from src.gui.drawing_compare_workbench import _filter_overlays_by_pdf_pages


def _ov(zid: str, *, page_a=None, page_b=None, nested=False) -> dict:
    base = {"zone_id": zid}
    if page_a is not None and page_b is not None:
        if nested:
            base["metadata"] = {"page_a": page_a, "page_b": page_b}
        else:
            base["page_a"] = page_a
            base["page_b"] = page_b
    return base


# ---------------------------------------------------------------------------
# Empty / fallback
# ---------------------------------------------------------------------------


def test_empty_overlays_returns_empty() -> None:
    assert _filter_overlays_by_pdf_pages([], 0, 0) == []


def test_overlay_without_page_metadata_kept_for_any_page() -> None:
    """DXF / single-page PDF overlays carry no page indices — they
    should pass through to every page (acts as legacy fallback)."""

    overlays = [_ov("z1"), _ov("z2")]
    result = _filter_overlays_by_pdf_pages(overlays, 5, 7)
    assert len(result) == 2
    assert [o["zone_id"] for o in result] == ["z1", "z2"]


def test_non_dict_overlay_entries_skipped() -> None:
    overlays = [_ov("z1"), "garbage", None, 42, _ov("z2")]  # type: ignore[list-item]
    result = _filter_overlays_by_pdf_pages(overlays, 0, 0)
    assert [o["zone_id"] for o in result] == ["z1", "z2"]


# ---------------------------------------------------------------------------
# Top-level page metadata
# ---------------------------------------------------------------------------


def test_filter_keeps_matching_top_level_pages() -> None:
    overlays = [
        _ov("z1", page_a=0, page_b=2),
        _ov("z2", page_a=1, page_b=0),
        _ov("z3", page_a=0, page_b=2),
    ]
    result = _filter_overlays_by_pdf_pages(overlays, 0, 2)
    assert [o["zone_id"] for o in result] == ["z1", "z3"]


def test_filter_drops_non_matching_pages() -> None:
    overlays = [
        _ov("z1", page_a=0, page_b=2),
        _ov("z2", page_a=1, page_b=3),
    ]
    result = _filter_overlays_by_pdf_pages(overlays, 5, 5)
    assert result == []


# ---------------------------------------------------------------------------
# Nested metadata path
# ---------------------------------------------------------------------------


def test_filter_keeps_nested_metadata_match() -> None:
    overlays = [
        _ov("z1", page_a=0, page_b=2, nested=True),
        _ov("z2", page_a=1, page_b=0, nested=True),
    ]
    result = _filter_overlays_by_pdf_pages(overlays, 0, 2)
    assert [o["zone_id"] for o in result] == ["z1"]


def test_top_level_takes_precedence_over_nested_when_both_present() -> None:
    """Top-level keys are checked first; nested only used as fallback."""

    overlays = [
        {"zone_id": "z1", "page_a": 0, "page_b": 2, "metadata": {"page_a": 99, "page_b": 99}},
    ]
    result = _filter_overlays_by_pdf_pages(overlays, 0, 2)
    assert [o["zone_id"] for o in result] == ["z1"]


# ---------------------------------------------------------------------------
# Realistic multi-page Phase H scenario
# ---------------------------------------------------------------------------


def test_realistic_3page_pdf_navigation() -> None:
    """3-page PDF with Phase H reordering: A.page0↔B.page2,
    A.page1↔B.page0, A.page2↔B.page1. User navigates to each pair."""

    overlays = [
        _ov("p0_z1", page_a=0, page_b=2),
        _ov("p0_z2", page_a=0, page_b=2),
        _ov("p1_z1", page_a=1, page_b=0),
        _ov("p2_z1", page_a=2, page_b=1),
        _ov("p2_z2", page_a=2, page_b=1),
        _ov("p2_z3", page_a=2, page_b=1),
    ]

    # Navigate to (0, 2) — should see 2 zones
    page0 = _filter_overlays_by_pdf_pages(overlays, 0, 2)
    assert len(page0) == 2
    assert [o["zone_id"] for o in page0] == ["p0_z1", "p0_z2"]

    # Navigate to (1, 0) — should see 1 zone
    page1 = _filter_overlays_by_pdf_pages(overlays, 1, 0)
    assert [o["zone_id"] for o in page1] == ["p1_z1"]

    # Navigate to (2, 1) — should see 3 zones
    page2 = _filter_overlays_by_pdf_pages(overlays, 2, 1)
    assert len(page2) == 3
    assert [o["zone_id"] for o in page2] == ["p2_z1", "p2_z2", "p2_z3"]

    # Sum of all per-page filters = total overlays
    assert len(page0) + len(page1) + len(page2) == len(overlays)


# ---------------------------------------------------------------------------
# Defensive — bad metadata
# ---------------------------------------------------------------------------


def test_invalid_page_value_overlay_skipped() -> None:
    """An overlay with non-int page values is treated as 'no metadata'
    → kept (legacy fallback) rather than crashing the filter."""

    overlays = [
        {"zone_id": "z1", "page_a": "abc", "page_b": "def"},
        _ov("z2", page_a=0, page_b=0),
    ]
    # Both kept (z1 because filter couldn't parse, z2 because match)
    result = _filter_overlays_by_pdf_pages(overlays, 0, 0)
    assert {o["zone_id"] for o in result} == {"z1", "z2"}


def test_partial_page_metadata_uses_zero_default() -> None:
    """Missing one of page_a/page_b → defaults to 0."""

    overlays = [
        {"zone_id": "z1", "page_a": 2},  # page_b missing → 0
        {"zone_id": "z2", "page_b": 5},  # page_a missing → 0
    ]
    # Filter for (2, 0) keeps z1; filter for (0, 5) keeps z2
    assert [o["zone_id"] for o in _filter_overlays_by_pdf_pages(overlays, 2, 0)] == ["z1"]
    assert [o["zone_id"] for o in _filter_overlays_by_pdf_pages(overlays, 0, 5)] == ["z2"]
