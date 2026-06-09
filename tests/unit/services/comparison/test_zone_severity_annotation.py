# -*- coding: utf-8 -*-
"""Severity honesty: pure annotation/dimension repositioning must NOT be flagged
critical (it is review noise), but real structural / value / add / delete changes
must keep their severity. Demote, never drop."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from src.services.comparison.change_zones import _zone_severity


def _env(category: str = ""):
    return SimpleNamespace(
        change=SimpleNamespace(change_category=category, metadata={}),
        bbox=(0.0, 0.0, 1.0, 1.0),
        old_bbox=(0.0, 0.0, 1.0, 1.0),
    )


def _sev(entity, layer, change_type, *, category="", type_counts=None):
    return _zone_severity(
        [_env(category)],
        Counter(type_counts or {"modified": 1}),
        Counter({layer: 1}),
        Counter({entity: 1}),
        change_type=change_type,
    )[0]


def test_moved_dimension_is_demoted_not_critical():
    # The bug: a DIMENSION that merely moved was flagged critical.
    assert _sev("DIMENSION", "A-DIM", "moved") == "low"


def test_moved_annotation_text_is_demoted():
    assert _sev("TEXT", "TXT3", "moved") == "low"
    assert _sev("MULTILEADER", "A-ANNO", "moved") == "low"


def test_moved_annotation_on_structural_layer_is_not_demoted():
    # Conservative: a dimension move on a structural layer stays critical.
    assert _sev("DIMENSION", "BEAM", "moved") == "critical"
    assert _sev("TEXT", "COLUMN-1F", "moved") != "low"


def test_moved_annotation_with_content_change_is_not_demoted():
    # A text that moved AND whose content changed is a real edit — preserved.
    assert _sev("TEXT", "TXT3", "moved", category="content") != "low"


def test_value_changed_dimension_stays_critical():
    # A dimension value change (not a move) is genuinely significant.
    assert _sev("DIMENSION", "A-DIM", "modified") == "critical"


def test_added_or_deleted_annotation_does_not_use_the_reposition_demotion():
    # The demotion fires ONLY for change_type=="moved". deleted keeps its existing
    # medium; added is independently "low" by the existing added-only rule (NOT by
    # the demotion), so the annotation-reposition reason must be absent for both.
    sev_d, reasons_d = _zone_severity(
        [_env()], Counter({"deleted": 1}), Counter({"TXT3": 1}), Counter({"TEXT": 1}), change_type="deleted"
    )
    assert sev_d == "medium"
    assert "annotation reposition" not in " ".join(reasons_d)
    _sev_a, reasons_a = _zone_severity(
        [_env()], Counter({"added": 1}), Counter({"TXT3": 1}), Counter({"TEXT": 1}), change_type="added"
    )
    assert "annotation reposition" not in " ".join(reasons_a)


def test_moved_non_annotation_geometry_is_not_demoted():
    # A moved LINE/structural primitive is not annotation noise.
    assert _sev("LINE", "GEOM", "moved") != "low"
