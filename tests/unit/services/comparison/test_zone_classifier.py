# -*- coding: utf-8 -*-
"""Unit tests for the heuristic zone classifier (Phase E2)."""

from __future__ import annotations

import pytest

from src.services.comparison.zone_classifier import (
    CATEGORY_DETAIL,
    CATEGORY_DIMENSION,
    CATEGORY_GRID,
    CATEGORY_LAYER,
    CATEGORY_OTHER,
    CATEGORY_STRUCTURAL_MEMBER,
    category_summary,
    classify_zone,
    classify_zones,
)


def test_structural_member_match_takes_priority() -> None:
    result = classify_zone({"layer": "S-BEAM-MAIN", "change_type": "modified", "raw_change_count": 12})
    assert result.category == CATEGORY_STRUCTURAL_MEMBER
    assert result.confidence >= 0.8
    assert result.severity_boost >= 10
    assert "구조 부재" in result.rationale_ko


def test_grid_pattern_classifies_as_grid() -> None:
    result = classify_zone({"layer": "S-XGRID", "change_type": "added", "raw_change_count": 4})
    assert result.category == CATEGORY_GRID
    assert result.severity_boost >= 9  # base 8 + change_type bonus


def test_dimension_layer_classifies_as_dimension() -> None:
    result = classify_zone({"layer": "S-DIMS-PRINCIPAL", "change_type": "modified", "raw_change_count": 2})
    assert result.category == CATEGORY_DIMENSION


def test_detail_marker_layer_classifies_as_detail() -> None:
    result = classify_zone({"layer": "S-DETL-MARKING", "change_type": "added", "raw_change_count": 1})
    assert result.category == CATEGORY_DETAIL


def test_korean_layer_keywords_recognised() -> None:
    """Layer names mixing Korean structural terms should still classify."""
    result = classify_zone({"layer": "1F_보_MAIN", "change_type": "added", "raw_change_count": 1})
    assert result.category == CATEGORY_STRUCTURAL_MEMBER


def test_no_layer_falls_back_to_entity_type_text_dimension() -> None:
    result = classify_zone({"entity_types": ["TEXT"], "change_type": "modified", "raw_change_count": 3})
    assert result.category == CATEGORY_DIMENSION
    assert "엔티티 타입" in result.rationale_ko


def test_no_layer_no_known_entity_falls_back_to_other() -> None:
    result = classify_zone({"entity_types": ["LINE"], "change_type": "added", "raw_change_count": 1})
    assert result.category == CATEGORY_OTHER
    assert result.confidence <= 0.3


def test_large_raw_count_increases_severity_boost() -> None:
    small = classify_zone({"layer": "S-COL", "change_type": "modified", "raw_change_count": 5})
    large = classify_zone({"layer": "S-COL", "change_type": "modified", "raw_change_count": 800})
    assert large.severity_boost > small.severity_boost
    assert "대량 변경" in large.rationale_ko


def test_added_or_deleted_change_types_get_extra_boost() -> None:
    modified = classify_zone({"layer": "S-BEAM", "change_type": "modified", "raw_change_count": 5})
    deleted = classify_zone({"layer": "S-BEAM", "change_type": "deleted", "raw_change_count": 5})
    assert deleted.severity_boost == modified.severity_boost + 1


def test_classify_zones_preserves_order() -> None:
    inputs = [
        {"layer": "S-BEAM", "change_type": "modified", "raw_change_count": 1},
        {"layer": "S-DIMS", "change_type": "added", "raw_change_count": 1},
    ]
    results = classify_zones(inputs)
    assert [r.category for r in results] == [CATEGORY_STRUCTURAL_MEMBER, CATEGORY_DIMENSION]


def test_category_summary_counts_per_category() -> None:
    results = classify_zones(
        [
            {"layer": "S-BEAM", "change_type": "modified", "raw_change_count": 1},
            {"layer": "S-COL", "change_type": "modified", "raw_change_count": 1},
            {"layer": "S-DIMS", "change_type": "modified", "raw_change_count": 1},
        ]
    )
    summary = category_summary(results)
    assert summary[CATEGORY_STRUCTURAL_MEMBER] == 2
    assert summary[CATEGORY_DIMENSION] == 1


def test_invalid_input_returns_other_category() -> None:
    assert classify_zone(None).category == CATEGORY_OTHER  # type: ignore[arg-type]
    assert classify_zone("not a dict").category == CATEGORY_OTHER  # type: ignore[arg-type]


def test_top_layers_pipe_separated_string_recognised() -> None:
    """Dashboard top_layers field uses 'A | B | C' format — should still match."""
    result = classify_zone(
        {"top_layers": "S-AXIS | S-DIMS", "change_type": "modified", "raw_change_count": 4}
    )
    # Order matters: AXIS pattern fires first → grid wins
    assert result.category == CATEGORY_GRID


def test_classify_zone_uses_workbench_v2_field_layout(qapp_unused=None) -> None:
    """Smoke check the realistic input shape the Workbench passes in."""
    overlay_like = {
        "zone_id": "z1",
        "change_type": "added",
        "raw_change_count": 87,
        "top_layers": "S-COL-MAIN",
        "entity_types": ["LINE", "TEXT"],
        "severity": "high",
    }
    result = classify_zone(overlay_like)
    assert result.category == CATEGORY_STRUCTURAL_MEMBER
    assert result.severity_boost >= 10
