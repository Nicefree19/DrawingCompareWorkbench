# -*- coding: utf-8 -*-
"""Unit tests for the Korean match-side label and natural-language summary helpers.

These appear in the right-hand detail panel above the change-count breakdown so
the reviewer can immediately see whether a zone exists on both drawings or only
on one side, plus a one-line natural summary derived from layer + counts.
"""

from __future__ import annotations

import pytest

from src.gui.drawing_compare_workbench import match_side_ko, natural_change_summary


@pytest.mark.parametrize(
    "change_type,expected",
    [
        ("deleted", "변경 전(A)에만 존재"),
        ("removed", "변경 전(A)에만 존재"),
        ("added", "변경 후(B)에만 존재"),
        ("modified", "양쪽 매칭됨"),
        ("moved", "양쪽 매칭됨"),
        ("mixed", "혼합 (A/B 모두에 일부)"),
        ("", "양쪽 매칭됨"),
    ],
)
def test_match_side_ko_describes_change_origin(change_type, expected) -> None:
    assert match_side_ko(change_type) == expected


def test_natural_summary_combines_counts_and_top_layer() -> None:
    summary = natural_change_summary(
        {},
        added=5,
        deleted=0,
        modified=2,
        moved=0,
        top_layers="GRID | DIM",
    )
    assert summary == "GRID 레이어에 추가 5건, 수정 2건"


def test_natural_summary_falls_back_to_counts_only_when_layers_missing() -> None:
    summary = natural_change_summary({}, added=0, deleted=3, modified=0, moved=1)
    assert summary == "삭제 3건, 이동 1건"


def test_natural_summary_handles_zero_change_zone() -> None:
    summary = natural_change_summary({}, added=0, deleted=0, modified=0, moved=0)
    assert summary == "변경 없음"


def test_natural_summary_uses_first_layer_only_when_pipe_separated() -> None:
    summary = natural_change_summary(
        {},
        added=1,
        deleted=0,
        modified=0,
        moved=0,
        top_layers="S-AXIS | S-DIM | S-TXT",
    )
    assert summary == "S-AXIS 레이어에 추가 1건"


def test_natural_summary_handles_invalid_input_gracefully() -> None:
    summary = natural_change_summary(None, added=0, deleted=0, modified=4, moved=0)  # type: ignore[arg-type]
    assert summary == "수정 4건"


# ---------------------------------------------------------------------------
# Phase O Commit 4 [RV-20260508-010] — block text change suffix
# ---------------------------------------------------------------------------


def test_natural_summary_appends_block_text_suffix_for_attrib_zone() -> None:
    """ATTRIB 가 entity_types 에 등장하면 reviewer 가 즉시 '블록
    텍스트 변경' 사례임을 인지하도록 한국어 suffix 가 추가됨.
    """
    summary = natural_change_summary(
        {"entity_types": ["ATTRIB"]},
        added=0, deleted=0, modified=1, moved=0,
        top_layers="TEXT_LAYER",
    )
    assert "블록 텍스트 변경 포함" in summary
    assert "수정 1건" in summary


def test_natural_summary_appends_suffix_for_attdef_in_entity_types() -> None:
    summary = natural_change_summary(
        {"entity_types": ["LINE", "ATTDEF"]},
        added=0, deleted=0, modified=2, moved=0,
    )
    assert "블록 텍스트 변경 포함" in summary


def test_natural_summary_handles_pipe_separated_entity_types_string() -> None:
    """Workbench V2 가 entity_types 를 'A | B' 문자열로 평탄화한 경우도
    감지."""
    summary = natural_change_summary(
        {"top_entity_types": "INSERT | ATTRIB | TEXT"},
        added=0, deleted=0, modified=1, moved=0,
    )
    assert "블록 텍스트 변경 포함" in summary


def test_natural_summary_no_suffix_when_no_attrib_in_zone() -> None:
    """LINE/CIRCLE 만 변경된 zone 은 suffix 없음 (회귀)."""
    summary = natural_change_summary(
        {"entity_types": ["LINE", "CIRCLE"]},
        added=2, deleted=0, modified=0, moved=0,
        top_layers="BEAM",
    )
    assert "블록 텍스트" not in summary
    assert summary == "BEAM 레이어에 추가 2건"


def test_natural_summary_zero_count_with_attrib_uses_special_phrase() -> None:
    """변경 0건 + ATTRIB 가 있는 (필터링 후 빈 zone 같은) 케이스."""
    summary = natural_change_summary(
        {"entity_types": ["ATTRIB"]},
        added=0, deleted=0, modified=0, moved=0,
    )
    assert summary == "변경 없음 (블록 텍스트 영역만 포함)"
