# -*- coding: utf-8 -*-
"""Tests for the review_burden audit gate (recommendation #3)."""

from __future__ import annotations

from typing import Any

from scripts import audit_drawing_compare_mvp_exit as audit


def _summary_with_burden(
    *,
    completed_pairs: int = 5,
    burden: dict[str, Any] | None = None,
    output_dir: str = "/tmp/test_run",
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "output_dir": output_dir,
        "comparison": {"completed_pairs": completed_pairs, "failed_pairs": 0},
    }
    if burden is not None:
        summary["review_burden"] = burden
    return summary


def _passing_burden() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "total_decisions": 10,
        "confirmed_count": 8,
        "false_positive_count": 2,
        "hold_count": 0,
        "sheet_count": 5,
        "top_queue_size": 5,
        "top_queue_confirmed": 4,
        "top_queue_false_positive": 1,
        "top_queue_precision": 0.8,
        "overall_precision": 0.8,
        "false_positive_burden_per_sheet": 0.4,
        "review_burden_minutes_per_sheet": 1.0,
        "minutes_per_decision": 0.5,
    }


class TestPrecisionThreshold:
    def test_passes_when_precision_meets_threshold(self):
        summary = _summary_with_burden(burden=_passing_burden())
        result = audit._check_review_burden(
            [summary],
            require_precision_threshold=0.80,
            require_burden_threshold=None,
            require_burden_minutes_threshold=None,
        )
        assert result.passed is True
        assert result.name == "review_queue_precision_and_burden"

    def test_fails_when_precision_below_threshold(self):
        burden = _passing_burden()
        burden["top_queue_precision"] = 0.5
        summary = _summary_with_burden(burden=burden)
        result = audit._check_review_burden(
            [summary],
            require_precision_threshold=0.80,
            require_burden_threshold=None,
            require_burden_minutes_threshold=None,
        )
        assert result.passed is False
        assert "top_queue_precision=0.5000" in result.detail

    def test_fails_when_precision_missing(self):
        burden = _passing_burden()
        burden["top_queue_precision"] = None
        summary = _summary_with_burden(burden=burden)
        result = audit._check_review_burden(
            [summary],
            require_precision_threshold=0.80,
            require_burden_threshold=None,
            require_burden_minutes_threshold=None,
        )
        assert result.passed is False
        assert "top_queue_precision missing" in result.detail


class TestBurdenThreshold:
    def test_passes_when_burden_under_cap(self):
        summary = _summary_with_burden(burden=_passing_burden())
        result = audit._check_review_burden(
            [summary],
            require_precision_threshold=None,
            require_burden_threshold=3.0,
            require_burden_minutes_threshold=None,
        )
        assert result.passed is True

    def test_fails_when_fp_burden_exceeds_cap(self):
        burden = _passing_burden()
        burden["false_positive_burden_per_sheet"] = 5.5
        summary = _summary_with_burden(burden=burden)
        result = audit._check_review_burden(
            [summary],
            require_precision_threshold=None,
            require_burden_threshold=3.0,
            require_burden_minutes_threshold=None,
        )
        assert result.passed is False
        assert "false_positive_burden_per_sheet=5.5000" in result.detail

    def test_fails_when_fp_burden_missing(self):
        burden = _passing_burden()
        burden["false_positive_burden_per_sheet"] = None
        summary = _summary_with_burden(burden=burden)
        result = audit._check_review_burden(
            [summary],
            require_precision_threshold=None,
            require_burden_threshold=3.0,
            require_burden_minutes_threshold=None,
        )
        assert result.passed is False
        assert "false_positive_burden_per_sheet missing" in result.detail


class TestBurdenMinutesThreshold:
    def test_passes_when_minutes_under_cap(self):
        summary = _summary_with_burden(burden=_passing_burden())
        result = audit._check_review_burden(
            [summary],
            require_precision_threshold=None,
            require_burden_threshold=None,
            require_burden_minutes_threshold=5.0,
        )
        assert result.passed is True

    def test_fails_when_minutes_exceed_cap(self):
        burden = _passing_burden()
        burden["review_burden_minutes_per_sheet"] = 7.5
        summary = _summary_with_burden(burden=burden)
        result = audit._check_review_burden(
            [summary],
            require_precision_threshold=None,
            require_burden_threshold=None,
            require_burden_minutes_threshold=5.0,
        )
        assert result.passed is False
        assert "review_burden_minutes_per_sheet=7.5000" in result.detail


class TestMissingReviewBurden:
    def test_fails_when_review_burden_block_missing(self):
        summary = _summary_with_burden(burden=None)
        result = audit._check_review_burden(
            [summary],
            require_precision_threshold=0.80,
            require_burden_threshold=None,
            require_burden_minutes_threshold=None,
        )
        assert result.passed is False
        assert "review_burden block missing" in result.detail

    def test_fails_when_no_outputs(self):
        result = audit._check_review_burden(
            [],
            require_precision_threshold=0.80,
            require_burden_threshold=None,
            require_burden_minutes_threshold=None,
        )
        assert result.passed is False


class TestRunAuditIntegration:
    def test_run_audit_accepts_burden_kwargs(self):
        report = audit.run_audit(
            result_dirs=[],
            require_precision_threshold=0.80,
            require_burden_threshold=3.0,
            require_burden_minutes_threshold=5.0,
        )
        check_names = {check["name"] for check in report["checks"]}
        assert "review_queue_precision_and_burden" in check_names
