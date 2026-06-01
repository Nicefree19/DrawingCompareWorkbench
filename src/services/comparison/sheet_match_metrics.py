"""Sheet matching accuracy metrics.

This module is deliberately independent from the region detector and GUI. It
scores a set of predicted before/after sheet pairs against a ground-truth
manifest and reports precision/recall/F1 plus manual-review burden. Synthetic
fixtures can use the same contract without being promoted to customer-grade
evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


MATCHED_STATUSES = {"auto_confirmed", "review_required", "matched"}
MANUAL_STATUSES = {"review_required", "manual_required", "hold"}


@dataclass(frozen=True)
class SheetMatchPrediction:
    before_id: str
    after_id: str
    status: str = "auto_confirmed"
    confidence: float = 0.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SheetMatchPrediction":
        return cls(
            before_id=str(data.get("before_id") or data.get("before") or "").strip(),
            after_id=str(data.get("after_id") or data.get("after") or "").strip(),
            status=str(data.get("status") or "auto_confirmed").strip(),
            confidence=_float(data.get("confidence", data.get("score", 0.0))),
        )

    @property
    def is_matched(self) -> bool:
        return bool(self.before_id and self.after_id and self.status in MATCHED_STATUSES)

    @property
    def requires_manual_review(self) -> bool:
        return self.status in MANUAL_STATUSES


@dataclass(frozen=True)
class SheetMatchTruth:
    before_id: str
    after_id: str
    manual_required: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SheetMatchTruth":
        return cls(
            before_id=str(data.get("before_id") or data.get("before") or "").strip(),
            after_id=str(data.get("after_id") or data.get("after") or "").strip(),
            manual_required=bool(data.get("manual_required") or data.get("manual_match_required")),
        )

    @property
    def pair(self) -> tuple[str, str]:
        return (self.before_id, self.after_id)


@dataclass(frozen=True)
class SheetMatchMetrics:
    precision: float
    recall: float
    f1: float
    manual_match_required_count: int
    false_match_count: int
    unmatched_count: int
    true_positive_count: int
    predicted_match_count: int
    expected_auto_match_count: int
    confidence_distribution: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision": round(self.precision, 6),
            "recall": round(self.recall, 6),
            "f1": round(self.f1, 6),
            "manual_match_required_count": self.manual_match_required_count,
            "false_match_count": self.false_match_count,
            "unmatched_count": self.unmatched_count,
            "true_positive_count": self.true_positive_count,
            "predicted_match_count": self.predicted_match_count,
            "expected_auto_match_count": self.expected_auto_match_count,
            "confidence_distribution": dict(self.confidence_distribution),
        }


def compute_sheet_match_metrics(
    predictions: Sequence[SheetMatchPrediction | Mapping[str, Any]],
    ground_truth: Sequence[SheetMatchTruth | Mapping[str, Any]],
) -> SheetMatchMetrics:
    """Compute precision/recall/F1 for sheet pairing.

    Truth rows marked ``manual_required`` are excluded from the automatic
    recall denominator: they are expected to require operator confirmation.
    They still contribute to ``manual_match_required_count``.
    """

    preds = [_as_prediction(p) for p in predictions]
    truths = [_as_truth(t) for t in ground_truth]

    expected_auto = {truth.pair for truth in truths if not truth.manual_required}
    manual_truth = {truth.pair for truth in truths if truth.manual_required}
    expected_all = expected_auto | manual_truth

    matched_preds = [pred for pred in preds if pred.is_matched]
    predicted_pairs = [(pred.before_id, pred.after_id) for pred in matched_preds]
    predicted_set = set(predicted_pairs)

    true_positive_pairs = predicted_set & expected_auto
    false_pairs = {pair for pair in predicted_set if pair not in expected_all}
    unmatched_auto = expected_auto - predicted_set

    precision = _ratio(len(true_positive_pairs), len(predicted_set - manual_truth))
    recall = _ratio(len(true_positive_pairs), len(expected_auto))
    f1 = _ratio(2.0 * precision * recall, precision + recall)
    manual_pairs = set(manual_truth)
    manual_pairs.update(
        (pred.before_id, pred.after_id)
        for pred in matched_preds
        if pred.requires_manual_review
    )

    return SheetMatchMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        manual_match_required_count=len(manual_pairs),
        false_match_count=len(false_pairs),
        unmatched_count=len(unmatched_auto),
        true_positive_count=len(true_positive_pairs),
        predicted_match_count=len(predicted_set),
        expected_auto_match_count=len(expected_auto),
        confidence_distribution=_confidence_distribution(pred.confidence for pred in matched_preds),
    )


def _as_prediction(value: SheetMatchPrediction | Mapping[str, Any]) -> SheetMatchPrediction:
    if isinstance(value, SheetMatchPrediction):
        return value
    return SheetMatchPrediction.from_mapping(value)


def _as_truth(value: SheetMatchTruth | Mapping[str, Any]) -> SheetMatchTruth:
    if isinstance(value, SheetMatchTruth):
        return value
    return SheetMatchTruth.from_mapping(value)


def _confidence_distribution(values: Iterable[float]) -> dict[str, int]:
    buckets = {
        "lt_0_60": 0,
        "0_60_to_0_85": 0,
        "gte_0_85": 0,
    }
    for value in values:
        if value < 0.60:
            buckets["lt_0_60"] += 1
        elif value < 0.85:
            buckets["0_60_to_0_85"] += 1
        else:
            buckets["gte_0_85"] += 1
    return buckets


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 1.0
    return max(0.0, min(1.0, numerator / denominator))


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "SheetMatchMetrics",
    "SheetMatchPrediction",
    "SheetMatchTruth",
    "compute_sheet_match_metrics",
]
