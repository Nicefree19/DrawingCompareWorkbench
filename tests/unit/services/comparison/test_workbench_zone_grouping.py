# -*- coding: utf-8 -*-
"""Unit tests for the Phase I2.1 zone-grouping helper.

Covers ``_group_zones_by_category_v2`` which powers the upcoming
collapsible category tree (replacing the flat 30-row list). The helper
is module-level so it tests without spinning up Qt / a workbench window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from src.gui.drawing_compare_workbench import _group_zones_by_category_v2


@dataclass
class _FakeResult:
    """Stand-in for ZoneCategoryResult — only the fields the helper reads."""

    category: str
    severity_boost: int = 0


def _classify_fn_from_dict(mapping: dict[str, Optional[_FakeResult]]):
    """Build a classify_fn callable from a zone_id → result dict."""

    def _fn(zone_id: str) -> Optional[_FakeResult]:
        return mapping.get(zone_id)

    return _fn


# ---------------------------------------------------------------------------
# Empty / boundary
# ---------------------------------------------------------------------------


def test_empty_zones_returns_empty_groups() -> None:
    assert _group_zones_by_category_v2([], _classify_fn_from_dict({})) == []


def test_zones_without_zone_id_skipped() -> None:
    classify = _classify_fn_from_dict({"z1": _FakeResult("A", 5)})
    out = _group_zones_by_category_v2(
        [{"zone_id": "z1"}, {"no_id": True}, {}],
        classify,
    )
    assert out == [("A", 5, [{"zone_id": "z1"}])]


def test_non_dict_zones_skipped() -> None:
    classify = _classify_fn_from_dict({"z1": _FakeResult("A", 5)})
    out = _group_zones_by_category_v2(
        [{"zone_id": "z1"}, "garbage", None, 42],  # type: ignore[list-item]
        classify,
    )
    assert out == [("A", 5, [{"zone_id": "z1"}])]


# ---------------------------------------------------------------------------
# Grouping correctness
# ---------------------------------------------------------------------------


def test_zones_in_same_category_grouped_together() -> None:
    classify = _classify_fn_from_dict({
        "z1": _FakeResult("구조 부재 변경", 10),
        "z2": _FakeResult("구조 부재 변경", 10),
        "z3": _FakeResult("그리드 변경", 8),
    })
    zones = [{"zone_id": z} for z in ("z1", "z2", "z3")]
    out = _group_zones_by_category_v2(zones, classify)

    assert len(out) == 2
    label_to_ids = {label: [z["zone_id"] for z in zs] for label, _, zs in out}
    assert label_to_ids["구조 부재 변경"] == ["z1", "z2"]
    assert label_to_ids["그리드 변경"] == ["z3"]


def test_input_order_preserved_within_category() -> None:
    """Zones in the same bucket appear in the order they were passed."""

    classify = _classify_fn_from_dict({
        "z1": _FakeResult("X", 5),
        "z2": _FakeResult("X", 5),
        "z3": _FakeResult("X", 5),
    })
    zones = [{"zone_id": "z3"}, {"zone_id": "z1"}, {"zone_id": "z2"}]
    out = _group_zones_by_category_v2(zones, classify)
    assert len(out) == 1
    _, _, group_zones = out[0]
    assert [z["zone_id"] for z in group_zones] == ["z3", "z1", "z2"]


# ---------------------------------------------------------------------------
# Sort order — by severity_boost desc, then label asc
# ---------------------------------------------------------------------------


def test_groups_sorted_by_severity_desc() -> None:
    classify = _classify_fn_from_dict({
        "z1": _FakeResult("low", 1),
        "z2": _FakeResult("high", 10),
        "z3": _FakeResult("mid", 5),
    })
    zones = [{"zone_id": z} for z in ("z1", "z2", "z3")]
    out = _group_zones_by_category_v2(zones, classify)
    assert [label for label, _, _ in out] == ["high", "mid", "low"]


def test_tie_broken_by_label_asc() -> None:
    """When two categories share the same boost, alphabetic order wins."""

    classify = _classify_fn_from_dict({
        "z1": _FakeResult("zeta", 5),
        "z2": _FakeResult("alpha", 5),
        "z3": _FakeResult("middle", 5),
    })
    zones = [{"zone_id": z} for z in ("z1", "z2", "z3")]
    out = _group_zones_by_category_v2(zones, classify)
    assert [label for label, _, _ in out] == ["alpha", "middle", "zeta"]


# ---------------------------------------------------------------------------
# Fallback for unclassified zones
# ---------------------------------------------------------------------------


def test_unclassified_zones_go_to_fallback_bucket() -> None:
    classify = _classify_fn_from_dict({
        "z1": _FakeResult("A", 5),
        # z2 missing → classify returns None → fallback
    })
    zones = [{"zone_id": "z1"}, {"zone_id": "z2"}]
    out = _group_zones_by_category_v2(zones, classify)

    labels = [label for label, _, _ in out]
    assert "기타 변경" in labels  # default fallback
    fallback_zones = next(zs for label, _, zs in out if label == "기타 변경")
    assert [z["zone_id"] for z in fallback_zones] == ["z2"]


def test_custom_fallback_label_honoured() -> None:
    classify = _classify_fn_from_dict({})  # everything unclassified
    zones = [{"zone_id": "z1"}, {"zone_id": "z2"}]
    out = _group_zones_by_category_v2(zones, classify, fallback_label="(기타)")

    assert len(out) == 1
    label, boost, zs = out[0]
    assert label == "(기타)"
    assert boost == 0
    assert [z["zone_id"] for z in zs] == ["z1", "z2"]


def test_classify_fn_exception_treated_as_unclassified() -> None:
    """When classify_fn blows up for one zone, helper must still group
    the survivors and bucket the broken one as fallback."""

    def _classify(zone_id: str) -> Optional[_FakeResult]:
        if zone_id == "z2":
            raise RuntimeError("classify exploded")
        return _FakeResult("A", 5)

    zones = [{"zone_id": "z1"}, {"zone_id": "z2"}, {"zone_id": "z3"}]
    out = _group_zones_by_category_v2(zones, _classify)

    labels = [label for label, _, _ in out]
    assert "A" in labels
    assert "기타 변경" in labels
    a_zones = next(zs for label, _, zs in out if label == "A")
    fallback_zones = next(zs for label, _, zs in out if label == "기타 변경")
    assert [z["zone_id"] for z in a_zones] == ["z1", "z3"]
    assert [z["zone_id"] for z in fallback_zones] == ["z2"]


# ---------------------------------------------------------------------------
# Boost conflict — same label with different boosts
# ---------------------------------------------------------------------------


def test_inconsistent_boost_for_same_label_keeps_max() -> None:
    """If the classify_fn returns different boosts for the same label
    across different zones (defensive — shouldn't normally happen),
    the helper records the highest so sorting still favours the most
    important interpretation."""

    classify_map = {
        "z1": _FakeResult("A", 5),
        "z2": _FakeResult("A", 8),
        "z3": _FakeResult("B", 6),
    }
    zones = [{"zone_id": z} for z in ("z1", "z2", "z3")]
    out = _group_zones_by_category_v2(zones, _classify_fn_from_dict(classify_map))

    label_to_boost = {label: boost for label, boost, _ in out}
    assert label_to_boost["A"] == 8  # max wins
    assert label_to_boost["B"] == 6
    # And A (boost 8) sorted before B (boost 6)
    assert [label for label, _, _ in out] == ["A", "B"]


# ---------------------------------------------------------------------------
# Realistic Korean structural categories
# ---------------------------------------------------------------------------


def test_realistic_structural_drawing() -> None:
    """End-to-end: 5 categories from the real classifier, mixed zones."""

    classify_map = {
        f"z{i}": _FakeResult("구조 부재 변경", 10) for i in range(1, 13)  # 12 structural
    }
    classify_map.update({
        f"z{i}": _FakeResult("그리드 변경", 8) for i in range(13, 18)  # 5 grid
    })
    classify_map.update({
        f"z{i}": _FakeResult("치수/주석 변경", 2) for i in range(18, 36)  # 18 dim
    })
    classify_map.update({
        f"z{i}": _FakeResult("상세/마킹 변경", 4) for i in range(36, 44)  # 8 detail
    })
    classify_map.update({
        f"z{i}": _FakeResult("기타 변경", 0) for i in range(44, 48)  # 4 other
    })
    zones = [{"zone_id": z} for z in classify_map]

    out = _group_zones_by_category_v2(
        zones, _classify_fn_from_dict(classify_map),
    )

    # Order: 구조(10) > 그리드(8) > 상세(4) > 치수(2) > 기타(0)
    expected_order = [
        "구조 부재 변경", "그리드 변경", "상세/마킹 변경",
        "치수/주석 변경", "기타 변경",
    ]
    assert [label for label, _, _ in out] == expected_order

    # Counts
    counts = {label: len(zs) for label, _, zs in out}
    assert counts == {
        "구조 부재 변경": 12,
        "그리드 변경": 5,
        "상세/마킹 변경": 8,
        "치수/주석 변경": 18,
        "기타 변경": 4,
    }
    assert sum(counts.values()) == 47
