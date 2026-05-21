# -*- coding: utf-8 -*-
"""Unit tests for the Phase I1 zone count summary helper.

Covers ``_format_zone_count_summary_v2`` which powers the prominent
"📋 47개 변경구역 (구조 12 · 치수 18 · 그리드 5 · …)" label sitting
above the zone list. The helper is module-level specifically so it can
be tested without spinning up Qt.
"""

from __future__ import annotations

import pytest

from src.gui.drawing_compare_workbench import _format_zone_count_summary_v2


# ---------------------------------------------------------------------------
# Empty / boundary cases
# ---------------------------------------------------------------------------


def test_empty_counts_returns_neutral_label() -> None:
    assert _format_zone_count_summary_v2({}) == "📋 변경구역 없음"


def test_zero_total_returns_neutral_label() -> None:
    """A counts dict that sums to zero (all zero) is functionally empty."""

    assert _format_zone_count_summary_v2({"A": 0, "B": 0}) == "📋 변경구역 없음"


def test_single_category_renders() -> None:
    out = _format_zone_count_summary_v2({"구조 부재 변경": 12})
    assert "📋 12개 변경구역" in out
    assert "구조 부재 변경 12" in out


# ---------------------------------------------------------------------------
# Sorting and rendering
# ---------------------------------------------------------------------------


def test_categories_sorted_by_count_desc() -> None:
    out = _format_zone_count_summary_v2({"A": 5, "B": 12, "C": 3})
    # B (12) should appear before A (5) which should appear before C (3)
    b_idx = out.index("B 12")
    a_idx = out.index("A 5")
    c_idx = out.index("C 3")
    assert b_idx < a_idx < c_idx


def test_total_count_reflects_sum() -> None:
    out = _format_zone_count_summary_v2({"A": 5, "B": 12, "C": 3})
    assert "📋 20개 변경구역" in out


def test_zero_count_categories_excluded_from_breakdown() -> None:
    """Categories with count 0 should not appear in the breakdown line
    even though they're in the dict (e.g. category exists but no zones)."""

    out = _format_zone_count_summary_v2({"A": 5, "B": 0, "C": 3})
    assert "B " not in out  # no "B 0" anywhere
    assert "A 5" in out
    assert "C 3" in out


# ---------------------------------------------------------------------------
# visible_total variant — used when a filter is active
# ---------------------------------------------------------------------------


def test_visible_total_equals_total_uses_simple_form() -> None:
    out = _format_zone_count_summary_v2({"A": 5, "B": 12}, visible_total=17)
    assert "📋 17개 변경구역" in out
    assert "중" not in out  # no "X 중 Y 표시" form when shown == total


def test_visible_total_less_than_total_shows_filter_form() -> None:
    out = _format_zone_count_summary_v2(
        {"A": 5, "B": 12, "C": 3}, visible_total=12,
    )
    assert "📋 20개 중 12개 표시" in out


def test_visible_total_zero_renders_as_zero_shown() -> None:
    """When the filter excludes everything, label still shows total."""

    out = _format_zone_count_summary_v2({"A": 5, "B": 12}, visible_total=0)
    assert "📋 17개 중 0개 표시" in out


def test_visible_total_default_falls_back_to_sum() -> None:
    """When visible_total is omitted, we use sum(counts.values())."""

    out_default = _format_zone_count_summary_v2({"A": 5, "B": 12})
    out_explicit = _format_zone_count_summary_v2({"A": 5, "B": 12}, visible_total=17)
    assert out_default == out_explicit


# ---------------------------------------------------------------------------
# max_categories — folding the long tail
# ---------------------------------------------------------------------------


def test_long_tail_folded_into_etc() -> None:
    counts = {"A": 1, "B": 1, "C": 1, "D": 1, "E": 1, "F": 1, "G": 1, "H": 1}
    out = _format_zone_count_summary_v2(counts, max_categories=3)
    # First 3 categories sorted by count (ties → name asc) → A, B, C
    assert "A 1" in out
    assert "B 1" in out
    assert "C 1" in out
    # The remaining 5 (D,E,F,G,H = 5×1 = 5) get folded into "기타 5"
    assert "기타 5" in out


def test_max_categories_one_minimum_enforced() -> None:
    """``max_categories=0`` should still show at least one category."""

    counts = {"A": 5, "B": 3, "C": 2}
    out = _format_zone_count_summary_v2(counts, max_categories=0)
    # A is the largest, so it appears as the head; B+C fold into "기타 5"
    assert "A 5" in out
    assert "기타 5" in out


def test_no_fold_when_within_max_categories() -> None:
    """When count of categories ≤ max_categories, no '기타' label."""

    counts = {"A": 1, "B": 2, "C": 3}
    out = _format_zone_count_summary_v2(counts, max_categories=6)
    assert "기타" not in out


# ---------------------------------------------------------------------------
# Realistic Korean category names — sanity check
# ---------------------------------------------------------------------------


def test_realistic_korean_categories_render_cleanly() -> None:
    counts = {
        "구조 부재 변경": 12,
        "치수/주석 변경": 18,
        "그리드 변경": 5,
        "상세/마킹 변경": 8,
        "기타 변경": 4,
    }
    out = _format_zone_count_summary_v2(counts)
    # Total
    assert "📋 47개 변경구역" in out
    # All 5 categories visible (within default max_categories=6)
    assert "구조 부재 변경 12" in out
    assert "치수/주석 변경 18" in out
    assert "그리드 변경 5" in out
    assert "상세/마킹 변경 8" in out
    assert "기타 변경 4" in out
    # Format: prefix · cat1 · cat2 · …
    assert " · " in out


def test_separator_uses_middle_dot() -> None:
    """Korean convention — middle dot ' · ' separates breakdown items."""

    out = _format_zone_count_summary_v2({"A": 5, "B": 12})
    assert " · " in out
