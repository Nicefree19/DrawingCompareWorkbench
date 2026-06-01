from __future__ import annotations

import pytest

from scripts.benchmark_sheet_match_accuracy import run_benchmark
from scripts.build_multi_sheet_fixtures import build_multi_sheet_fixtures
from src.services.comparison.sheet_match_metrics import (
    SheetMatchPrediction,
    SheetMatchTruth,
    compute_sheet_match_metrics,
)


def test_perfect_predictions_score_one() -> None:
    metrics = compute_sheet_match_metrics(
        [
            SheetMatchPrediction("before-1", "after-1", confidence=0.95),
            SheetMatchPrediction("before-2", "after-2", confidence=0.91),
        ],
        [
            SheetMatchTruth("before-1", "after-1"),
            SheetMatchTruth("before-2", "after-2"),
        ],
    )

    assert metrics.precision == pytest.approx(1.0)
    assert metrics.recall == pytest.approx(1.0)
    assert metrics.f1 == pytest.approx(1.0)
    assert metrics.false_match_count == 0
    assert metrics.unmatched_count == 0
    assert metrics.confidence_distribution["gte_0_85"] == 2


def test_false_match_and_missing_expected_pair_are_counted() -> None:
    metrics = compute_sheet_match_metrics(
        [
            {"before_id": "before-1", "after_id": "after-1", "score": 0.96},
            {"before_id": "before-2", "after_id": "wrong-after", "score": 0.88},
        ],
        [
            {"before_id": "before-1", "after_id": "after-1"},
            {"before_id": "before-2", "after_id": "after-2"},
        ],
    )

    assert metrics.precision == pytest.approx(0.5)
    assert metrics.recall == pytest.approx(0.5)
    assert metrics.f1 == pytest.approx(0.5)
    assert metrics.false_match_count == 1
    assert metrics.unmatched_count == 1


def test_manual_required_truth_is_not_auto_recall_debt() -> None:
    metrics = compute_sheet_match_metrics(
        [
            {
                "before": "manual-before",
                "after": "manual-after",
                "status": "review_required",
                "confidence": 0.72,
            },
            {
                "before": "auto-before",
                "after": "auto-after",
                "status": "auto_confirmed",
                "confidence": 0.93,
            },
        ],
        [
            {
                "before_id": "manual-before",
                "after_id": "manual-after",
                "manual_required": True,
            },
            {"before_id": "auto-before", "after_id": "auto-after"},
        ],
    )

    assert metrics.precision == pytest.approx(1.0)
    assert metrics.recall == pytest.approx(1.0)
    assert metrics.manual_match_required_count == 1
    assert metrics.expected_auto_match_count == 1
    assert metrics.predicted_match_count == 2
    assert metrics.confidence_distribution["0_60_to_0_85"] == 1
    assert metrics.confidence_distribution["gte_0_85"] == 1


def test_hold_prediction_counts_as_manual_review_not_match() -> None:
    metrics = compute_sheet_match_metrics(
        [{"before_id": "before-1", "after_id": "after-1", "status": "hold"}],
        [{"before_id": "before-1", "after_id": "after-1"}],
    )

    assert metrics.precision == pytest.approx(1.0)
    assert metrics.recall == pytest.approx(0.0)
    assert metrics.manual_match_required_count == 0
    assert metrics.predicted_match_count == 0
    assert metrics.unmatched_count == 1


def test_synthetic_fixture_benchmark_passes_sheet_match_thresholds(tmp_path) -> None:
    fixture_root = tmp_path / "multi_sheet"
    out = tmp_path / "sheet_match_accuracy_synthetic.json"

    build_multi_sheet_fixtures(fixture_root)
    payload = run_benchmark(fixture_root, out)

    assert out.exists()
    assert payload["synthetic"] is True
    assert payload["status"] == "passed"
    assert payload["message"] == "ready to gate real fixtures"
    assert payload["precision"] >= 0.95
    assert payload["recall"] >= 0.90
    assert payload["false_match_count"] == 0
    assert payload["manual_match_required_count"] == 2
