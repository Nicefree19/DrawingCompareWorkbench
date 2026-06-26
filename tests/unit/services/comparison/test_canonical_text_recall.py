"""Regression guards for the canonical-pipeline recall fixes (2026-06-26).

Two honest recall levers, both verified against the golden corpus:

* ``_entity_anchor`` — change location is the entity insertion point
  (``geometry.insert``) when available, not the bbox centroid, so a text/label
  change marker lands at the entity origin (where truth/the reader expects it).
* text-aware candidate radius — a TEXT/MTEXT/DIMENSION label that shifts more
  than the base ``search_radius_mm`` while its content also changes merges into a
  single MODIFIED instead of splitting into added+deleted ("치수가 두 개로" bug).

Golden fixture 10 (``10_dimension_text_shifted``) exercises both end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison.drawing_compare_engine import (
    DrawingCompareOptions,
    EntityMatcher,
    _entity_anchor,
)

_GOLDEN = (
    Path(__file__).resolve().parents[4]
    / "tests/data/comparison/golden/dxf"
)


def test_entity_anchor_prefers_insert_point() -> None:
    entity = {
        "geometry": {"type": "text", "insert": {"x": 200.0, "y": 100.0, "z": 0.0}},
        "bbox": {"min_x": 200.0, "min_y": 100.0, "max_x": 320.0, "max_y": 150.0},
    }
    assert _entity_anchor(entity) == {"x": 200.0, "y": 100.0}


def test_entity_anchor_returns_none_without_insert() -> None:
    # A line has no insert point → caller falls back to the bbox centroid.
    assert _entity_anchor({"geometry": {"type": "line"}}) is None
    assert _entity_anchor({"bbox": {"min_x": 0, "min_y": 0, "max_x": 1, "max_y": 1}}) is None


def test_text_like_radius_widens_only_for_text_types() -> None:
    options = DrawingCompareOptions(search_radius_mm=10.0, text_search_radius_mm=50.0)
    matcher = EntityMatcher(options)
    assert matcher._radius_for_type("text") == 50.0
    assert matcher._radius_for_type("mtext") == 50.0
    assert matcher._radius_for_type("dimension") == 50.0
    # Non-text entities keep the base radius (no over-eager merging).
    assert matcher._radius_for_type("line") == matcher._candidate_radius()
    assert matcher._radius_for_type("line") < 50.0


def test_text_radius_zero_falls_back_to_base() -> None:
    matcher = EntityMatcher(DrawingCompareOptions(search_radius_mm=10.0, text_search_radius_mm=0.0))
    assert matcher._radius_for_type("text") == matcher._candidate_radius()


@pytest.mark.skipif(
    not (_GOLDEN / "10_dimension_text_shifted" / "before.dxf").exists(),
    reason="golden fixture 10 not present",
)
def test_shifted_text_with_content_change_is_single_modified() -> None:
    """End-to-end: a 30 mm-shifted DIM text whose content changes (1500→1550)
    surfaces as ONE modified text at its anchor, not an added+deleted split."""
    from src.services.comparison.dwg_differ import DwgDiffer

    pair = _GOLDEN / "10_dimension_text_shifted"
    result = DwgDiffer().compare(pair / "before.dxf", pair / "after.dxf")
    changes = result.changes or []

    assert len(changes) == 1, [getattr(c, "change_type", None) for c in changes]
    change = changes[0]
    assert str(getattr(change, "change_type", "")).lower().endswith("modified")
    meta = getattr(change, "metadata", {}) or {}
    assert meta.get("entity_type") == "text"

    # Location is the text anchor (~200,100), within the fixture's 50 mm truth
    # tolerance of (215,100) — NOT the bbox centroid (~260,125) which is >50 mm.
    loc = getattr(change, "location", None)
    if isinstance(loc, str):
        x, y = (float(v) for v in loc.split(","))
    elif isinstance(loc, dict):
        x, y = float(loc["x"]), float(loc["y"])
    else:
        x, y = float(loc[0]), float(loc[1])
    assert (x - 215.0) ** 2 + (y - 100.0) ** 2 <= 50.0 ** 2
