# -*- coding: utf-8 -*-
"""Unit tests for the Workbench top-issues / pattern-groups label formatters.

The Workbench reads ``top_project_issues`` and ``layer_patterns`` from the
review_dashboard.json output and renders each entry as a two-line list label.
Formatting is a pure function so we can test it without instantiating Qt.
"""

from __future__ import annotations

from src.gui.drawing_compare_workbench import (
    format_pattern_group_label,
    format_top_issue_label,
)


def test_top_issue_label_includes_drawing_zone_severity_and_score() -> None:
    issue = {
        "pair_id": "pair-1",
        "drawing_number": "S-101",
        "zone_id": "zone-3",
        "severity": "critical",
        "severity_ko": "긴급",
        "priority_score": 12.5,
        "priority_rank": 4,
        "raw_change_count": 87,
    }
    label = format_top_issue_label(issue)
    assert "S-101" in label
    assert "zone-3" in label
    assert "긴급" in label
    assert "#4" in label
    assert "점수 12.5" in label
    assert "raw 87" in label


def test_top_issue_label_falls_back_to_pair_id_when_drawing_missing() -> None:
    issue = {"pair_id": "pair-2", "zone_id": "zone-1", "severity_ko": "보통"}
    label = format_top_issue_label(issue)
    assert "pair-2" in label
    assert "보통" in label
    assert "#?" in label  # missing rank renders as #?


def test_top_issue_label_handles_invalid_inputs_gracefully() -> None:
    assert format_top_issue_label(None) == "(이슈 정보 없음)"  # type: ignore[arg-type]
    label = format_top_issue_label({"priority_score": "not a number", "raw_change_count": "x"})
    assert "점수 0.0" in label
    assert "raw 0" in label


def test_pattern_group_label_renders_drawings_zones_changes_and_layers() -> None:
    pattern = {
        "pattern": "축선/그리드",
        "affected_drawing_count": 12,
        "raw_change_count": 540,
        "zone_count": 87,
        "top_layers": "S-AXIS | S-GRID",
    }
    label = format_pattern_group_label(pattern)
    assert "축선/그리드" in label
    assert "도면 12" in label
    assert "변경구역 87" in label
    assert "변경 540" in label
    assert "S-AXIS | S-GRID" in label


def test_pattern_group_label_handles_missing_fields() -> None:
    label = format_pattern_group_label({})
    assert "(이름 없음)" in label
    assert "도면 0" in label
    assert "변경구역 0" in label
    assert "변경 0" in label


def test_pattern_group_label_handles_invalid_input() -> None:
    assert format_pattern_group_label(None) == "(패턴 정보 없음)"  # type: ignore[arg-type]
