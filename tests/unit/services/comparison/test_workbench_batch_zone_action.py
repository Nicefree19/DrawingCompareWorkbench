# -*- coding: utf-8 -*-
"""Unit tests for the Phase G3.7 batch zone action filter helper.

Covers ``_filter_zones_for_batch_v2`` which powers the live "적용 대상"
count in the batch zone-status dialog. The helper is module-level
specifically so it can be tested without spinning up Qt / the workbench
window.
"""

from __future__ import annotations

import pytest

from src.gui.drawing_compare_workbench import _filter_zones_for_batch_v2


ANY = "(모두)"


def _z(zid: str, **kwargs) -> dict:
    """Shorthand for an overlay dict with the keys the helper looks at."""

    base = {
        "zone_id": zid,
        "change_type": "modified",
        "severity": "minor",
        "entity_type": "PDF_TEXT",
        "layer": "TEXT",
        "_current_status": "needs_review",
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Empty / no-filter / pass-all
# ---------------------------------------------------------------------------


def test_empty_overlays_returns_empty() -> None:
    assert _filter_zones_for_batch_v2([], {"any_label": ANY}) == []


def test_no_filters_returns_all_zone_ids() -> None:
    overlays = [_z("z1"), _z("z2"), _z("z3")]
    selection = {
        "any_label": ANY,
        "change_type": ANY,
        "severity": ANY,
        "entity_type": ANY,
        "layer": ANY,
        "current_status": ANY,
    }
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1", "z2", "z3"]


def test_missing_filter_keys_treated_as_any() -> None:
    """Selection dict without a key for a dimension → that dimension is
    not filtered (defensive: dialog might evolve to drop a combo)."""

    overlays = [_z("z1", change_type="added"), _z("z2", change_type="deleted")]
    # Only severity provided, others missing → no filter on change_type
    selection = {"any_label": ANY, "severity": ANY}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1", "z2"]


# ---------------------------------------------------------------------------
# Single-dimension filters
# ---------------------------------------------------------------------------


def test_filter_by_change_type() -> None:
    overlays = [
        _z("z1", change_type="added"),
        _z("z2", change_type="deleted"),
        _z("z3", change_type="added"),
    ]
    selection = {"any_label": ANY, "change_type": "added"}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1", "z3"]


def test_filter_by_severity() -> None:
    overlays = [
        _z("z1", severity="major"),
        _z("z2", severity="minor"),
        _z("z3", severity="major"),
    ]
    selection = {"any_label": ANY, "severity": "minor"}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z2"]


def test_filter_by_entity_type() -> None:
    overlays = [
        _z("z1", entity_type="STRUCTURAL_MEMBER"),
        _z("z2", entity_type="PDF_TEXT"),
        _z("z3", entity_type="STRUCTURAL_MEMBER"),
    ]
    selection = {"any_label": ANY, "entity_type": "STRUCTURAL_MEMBER"}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1", "z3"]


def test_filter_by_layer() -> None:
    overlays = [
        _z("z1", layer="GRID"),
        _z("z2", layer="DIM"),
        _z("z3", layer="GRID"),
    ]
    selection = {"any_label": ANY, "layer": "DIM"}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z2"]


def test_filter_by_current_status() -> None:
    overlays = [
        _z("z1", _current_status="confirmed"),
        _z("z2", _current_status="needs_review"),
        _z("z3", _current_status="needs_review"),
    ]
    selection = {"any_label": ANY, "current_status": "needs_review"}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z2", "z3"]


# ---------------------------------------------------------------------------
# Multi-dimension filters (AND semantics)
# ---------------------------------------------------------------------------


def test_two_filters_combine_with_AND() -> None:
    overlays = [
        _z("z1", change_type="added", severity="major"),
        _z("z2", change_type="added", severity="minor"),
        _z("z3", change_type="deleted", severity="major"),
    ]
    selection = {"any_label": ANY, "change_type": "added", "severity": "major"}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1"]


def test_all_filters_combine_with_AND() -> None:
    overlays = [
        _z("z1", change_type="added", severity="major",
           entity_type="STRUCTURAL_MEMBER", layer="GRID",
           _current_status="needs_review"),
        # Differs by status only
        _z("z2", change_type="added", severity="major",
           entity_type="STRUCTURAL_MEMBER", layer="GRID",
           _current_status="confirmed"),
    ]
    selection = {
        "any_label": ANY,
        "change_type": "added",
        "severity": "major",
        "entity_type": "STRUCTURAL_MEMBER",
        "layer": "GRID",
        "current_status": "needs_review",
    }
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1"]


def test_no_zones_match_returns_empty() -> None:
    overlays = [_z("z1", change_type="added"), _z("z2", change_type="deleted")]
    selection = {"any_label": ANY, "change_type": "modified"}
    assert _filter_zones_for_batch_v2(overlays, selection) == []


# ---------------------------------------------------------------------------
# Defensive — bad / missing data
# ---------------------------------------------------------------------------


def test_overlay_missing_zone_id_skipped() -> None:
    overlays = [
        _z("z1"),
        {"change_type": "added"},  # no zone_id
        _z("z3"),
    ]
    selection = {"any_label": ANY}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1", "z3"]


def test_non_dict_overlay_entry_skipped() -> None:
    overlays = [_z("z1"), "garbage", None, _z("z2")]  # type: ignore[list-item]
    selection = {"any_label": ANY}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1", "z2"]


def test_overlay_with_missing_filter_field_excluded_when_filtered() -> None:
    """An overlay missing the field we're filtering on → not included
    (matches the dialog UX: the user picked a specific value, so absence
    means non-match, not "show everything")."""

    overlays = [
        _z("z1", change_type="added"),
        {"zone_id": "z2"},  # change_type missing
    ]
    selection = {"any_label": ANY, "change_type": "added"}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1"]


def test_filter_value_is_case_sensitive() -> None:
    """Filter values come straight from the combo (which got them from
    the data) so case must match exactly. We don't lowercase-normalise."""

    overlays = [_z("z1", change_type="added")]
    selection = {"any_label": ANY, "change_type": "ADDED"}
    assert _filter_zones_for_batch_v2(overlays, selection) == []


def test_zone_id_whitespace_normalised() -> None:
    overlays = [_z("  z1  "), _z("z2")]
    selection = {"any_label": ANY}
    # zone_id is .strip()'d, so "  z1  " → "z1"
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1", "z2"]


def test_empty_zone_id_skipped() -> None:
    overlays = [_z(""), _z("z1")]
    selection = {"any_label": ANY}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1"]


def test_custom_any_label_honoured() -> None:
    """If the dialog later changes the ANY sentinel, the helper should
    follow whatever ``selection["any_label"]`` says."""

    overlays = [_z("z1", change_type="added"), _z("z2", change_type="deleted")]
    selection = {"any_label": "ALL", "change_type": "ALL"}
    assert _filter_zones_for_batch_v2(overlays, selection) == ["z1", "z2"]
