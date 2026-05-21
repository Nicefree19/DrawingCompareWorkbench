# -*- coding: utf-8 -*-
"""Tests for the shared cloud-export helpers.

Phase I review fix #5 — the confirmed-zone selector + bbox parser
were duplicated across two exporters. This test set pins the SHARED
implementation so a future regression on either side surfaces here
first.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# confirmed_zone_ids_for_pair
# ---------------------------------------------------------------------------


class _FakeRecord:
    def __init__(self, pair_id, zone_id, status):
        self.pair_id = pair_id
        self.zone_id = zone_id
        self.status = status


def test_filters_by_pair_and_status() -> None:
    from src.services.comparison.review_helpers import confirmed_zone_ids_for_pair
    records = {
        "k1": _FakeRecord("p1", "z1", "confirmed"),
        "k2": _FakeRecord("p1", "z2", "needs_review"),  # wrong status
        "k3": _FakeRecord("p2", "z3", "confirmed"),     # wrong pair
        "k4": _FakeRecord("p1", "z4", "confirmed"),
    }
    out = confirmed_zone_ids_for_pair("p1", records)
    assert out == {"z1", "z4"}


def test_accepts_dict_records_too() -> None:
    """Some test fixtures pass plain dicts instead of dataclass records."""

    from src.services.comparison.review_helpers import confirmed_zone_ids_for_pair
    records = {
        "k1": {"pair_id": "p1", "zone_id": "z1", "status": "confirmed"},
        "k2": {"pair_id": "p1", "zone_id": "z2", "status": "ignored"},
    }
    assert confirmed_zone_ids_for_pair("p1", records) == {"z1"}


def test_empty_records_returns_empty_set() -> None:
    from src.services.comparison.review_helpers import confirmed_zone_ids_for_pair
    assert confirmed_zone_ids_for_pair("p1", {}) == set()
    assert confirmed_zone_ids_for_pair("p1", None) == set()


def test_skips_records_missing_zone_id() -> None:
    from src.services.comparison.review_helpers import confirmed_zone_ids_for_pair
    records = {
        "k1": _FakeRecord("p1", "", "confirmed"),  # blank zone_id
        "k2": _FakeRecord("p1", "z2", "confirmed"),
    }
    out = confirmed_zone_ids_for_pair("p1", records)
    assert out == {"z2"}


# ---------------------------------------------------------------------------
# safe_pair_name
# ---------------------------------------------------------------------------


def test_safe_pair_name_alphanumeric_passthrough() -> None:
    from src.services.comparison.review_helpers import safe_pair_name
    assert safe_pair_name("pair_123") == "pair_123"
    assert safe_pair_name("S20-0001") == "S20-0001"


def test_safe_pair_name_replaces_unsafe_chars() -> None:
    from src.services.comparison.review_helpers import safe_pair_name
    assert safe_pair_name("path/with\\separators") == "path_with_separators"
    assert safe_pair_name("A:B*C?") == "A_B_C_"


def test_safe_pair_name_empty_falls_back() -> None:
    from src.services.comparison.review_helpers import safe_pair_name
    assert safe_pair_name("") == "pair"
    assert safe_pair_name(None) == "pair"


# ---------------------------------------------------------------------------
# resolve_pixel_bbox
# ---------------------------------------------------------------------------


def test_resolve_bbox_dict_form() -> None:
    from src.services.comparison.review_helpers import resolve_pixel_bbox
    out = resolve_pixel_bbox(
        {"after_bbox_px": {"x": 10, "y": 20, "width": 100, "height": 50}}
    )
    assert out == (10.0, 20.0, 110.0, 70.0)


def test_resolve_bbox_list_form() -> None:
    from src.services.comparison.review_helpers import resolve_pixel_bbox
    out = resolve_pixel_bbox({"after_bbox_px": [10, 20, 110, 70]})
    assert out == (10.0, 20.0, 110.0, 70.0)


def test_resolve_bbox_normalises_reversed_input() -> None:
    """Phase I fix: reversed bboxes (x0 > x1, y0 > y1) come out
    in canonical (min, min, max, max) order so downstream consumers
    don't draw inverted rectangles."""

    from src.services.comparison.review_helpers import resolve_pixel_bbox
    out = resolve_pixel_bbox({"after_bbox_px": [110, 70, 10, 20]})
    assert out == (10.0, 20.0, 110.0, 70.0)


def test_resolve_bbox_rejects_degenerate() -> None:
    """Zero-area bbox returns None — caller skips the zone."""

    from src.services.comparison.review_helpers import resolve_pixel_bbox
    assert resolve_pixel_bbox({"after_bbox_px": [10, 10, 10, 10]}) is None
    assert resolve_pixel_bbox({"after_bbox_px": [10.0, 10.0, 10.4, 10.4]}) is None


def test_resolve_bbox_falls_back_to_generic() -> None:
    from src.services.comparison.review_helpers import resolve_pixel_bbox
    out = resolve_pixel_bbox({"bbox": {"min_x": 0, "min_y": 0, "max_x": 5, "max_y": 5}})
    assert out == (0.0, 0.0, 5.0, 5.0)


def test_resolve_bbox_returns_none_for_garbage() -> None:
    from src.services.comparison.review_helpers import resolve_pixel_bbox
    assert resolve_pixel_bbox({}) is None
    assert resolve_pixel_bbox({"after_bbox_px": "garbage"}) is None
    assert resolve_pixel_bbox({"after_bbox_px": [1, 2]}) is None
