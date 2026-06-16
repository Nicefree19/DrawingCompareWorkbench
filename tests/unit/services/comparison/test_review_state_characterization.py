"""Characterization tests for the V2 review-state record logic.

SAFETY NET for the planned MONO-4 #6 extraction (docs/MONO_DECOMPOSITION_PLAN.md):
a future ReviewStateController would own ``_review_records_v2`` + the pure record
operations. These methods currently have ZERO direct test coverage, so this file
pins their behavior before any extraction:

  * ``_review_record_key_v2`` delegates to ``review_state_key``,
  * ``_review_status_ko_v2`` maps each status (and unknowns) to its Korean label,
  * ``_review_record_counts_for_pair_v2`` counts (done, confirmed) per pair,
  * ``_review_status_for_zone_v2`` prefers a stored record, else the active
    issue/overlay status, else ``needs_review``.

NOTE (scope): unlike the overlay cache, the review-state cluster is UI-entangled
— ``_review_status_for_zone_v2`` reads ``_active_issue_by_zone`` /
``_active_overlays_by_zone`` and ``_set_zone_review_status_v2`` orchestrates ~7
widgets. Only the pure record ops above are cleanly extractable; this net pins
exactly those. Exercises a real ``DrawingCompareWorkbenchV2`` (no rebinding).
"""

from __future__ import annotations

import pytest

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
from src.services.comparison.review_project import (
    ReviewStateRecord,
    review_state_key,
)


@pytest.fixture
def workbench(qapp):
    wb = DrawingCompareWorkbenchV2()
    try:
        yield wb
    finally:
        wb.deleteLater()


def _record(pair_id: str, zone_id: str, status: str) -> ReviewStateRecord:
    return ReviewStateRecord(pair_id=pair_id, zone_id=zone_id, status=status)


def test_review_record_key_delegates_to_review_state_key(workbench):
    assert workbench._review_record_key_v2("pair_a", "C-001") == review_state_key(
        "pair_a", "C-001"
    )


def test_review_status_ko_maps_each_status(workbench):
    assert workbench._review_status_ko_v2("needs_review") == "추가 검토"
    assert workbench._review_status_ko_v2("confirmed") == "확인"
    assert workbench._review_status_ko_v2("hold") == "보류"
    assert workbench._review_status_ko_v2("false_positive") == "오탐"
    # unknown / empty falls back to the needs-review label
    assert workbench._review_status_ko_v2("???") == "추가 검토"
    assert workbench._review_status_ko_v2("") == "추가 검토"


def test_review_record_counts_for_pair_counts_done_and_confirmed(workbench):
    records = [
        _record("pair_a", "z1", "confirmed"),
        _record("pair_a", "z2", "hold"),
        _record("pair_a", "z3", "needs_review"),
        _record("pair_b", "z1", "confirmed"),  # other pair, must be ignored
    ]
    workbench._review_records_v2 = {r.key: r for r in records}

    done, confirmed = workbench._review_record_counts_for_pair_v2("pair_a")
    # done = anything not needs_review (confirmed + hold); confirmed = confirmed only
    assert (done, confirmed) == (2, 1)
    # the other pair is scoped out
    assert workbench._review_record_counts_for_pair_v2("pair_b") == (1, 1)
    assert workbench._review_record_counts_for_pair_v2("absent") == (0, 0)


def test_review_status_for_zone_prefers_stored_record(workbench):
    rec = _record("pair_a", "C-001", "confirmed")
    workbench._review_records_v2 = {rec.key: rec}
    assert workbench._review_status_for_zone_v2("pair_a", "C-001") == "confirmed"


def test_review_status_for_zone_falls_back_to_issue_then_default(workbench):
    workbench._review_records_v2 = {}
    # no record -> falls back to the active issue's status
    workbench._active_issue_by_zone = {"C-002": {"status": "hold"}}
    workbench._active_overlays_by_zone = {}
    assert workbench._review_status_for_zone_v2("pair_a", "C-002") == "hold"

    # nothing anywhere -> needs_review default
    workbench._active_issue_by_zone = {}
    assert workbench._review_status_for_zone_v2("pair_a", "C-999") == "needs_review"
