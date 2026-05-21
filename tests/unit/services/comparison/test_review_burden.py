# -*- coding: utf-8 -*-
"""Unit tests for review_burden.compute_review_burden (recommendation #3)."""

from __future__ import annotations

from src.services.comparison.review_burden import (
    DEFAULT_MINUTES_PER_DECISION,
    OperatorDecision,
    ReviewBurdenStats,
    compute_review_burden,
    normalize_decision,
    operator_decision_from_dict,
    review_burden_from_dict,
)


class TestNormalizeDecision:
    def test_canonical_forms(self):
        assert normalize_decision("confirmed") == "confirmed"
        assert normalize_decision("false_positive") == "false_positive"
        assert normalize_decision("hold") == "hold"

    def test_aliases_supported(self):
        assert normalize_decision("CONFIRM") == "confirmed"
        assert normalize_decision("tp") == "confirmed"
        assert normalize_decision("fp") == "false_positive"
        assert normalize_decision("rejected") == "false_positive"
        assert normalize_decision("pending") == "hold"

    def test_unknown_falls_back_to_hold(self):
        assert normalize_decision("garbage") == "hold"
        assert normalize_decision(None) == "hold"


class TestOperatorDecisionFromDict:
    def test_extracts_fields(self):
        d = operator_decision_from_dict(
            {"zone_id": "z1", "sheet_id": "S-100", "decision": "confirmed", "rank": 3}
        )
        assert d.zone_id == "z1"
        assert d.sheet_id == "S-100"
        assert d.decision == "confirmed"
        assert d.rank == 3

    def test_drawing_label_alias_for_sheet_id(self):
        d = operator_decision_from_dict(
            {"zone_id": "z1", "drawing_label": "S-200", "decision": "fp"}
        )
        assert d.sheet_id == "S-200"
        assert d.decision == "false_positive"

    def test_status_alias_for_decision(self):
        d = operator_decision_from_dict({"zone_id": "z", "status": "hold"})
        assert d.decision == "hold"


class TestComputeReviewBurden:
    def test_empty_decisions_yields_none_metrics(self):
        stats = compute_review_burden([])
        assert stats.total_decisions == 0
        assert stats.overall_precision is None
        assert stats.top_queue_precision is None
        assert stats.false_positive_burden_per_sheet is None
        assert stats.review_burden_minutes_per_sheet is None

    def test_basic_precision_calculation(self):
        decisions = [
            OperatorDecision("z1", "S1", "confirmed", rank=1),
            OperatorDecision("z2", "S1", "confirmed", rank=2),
            OperatorDecision("z3", "S1", "false_positive", rank=3),
        ]
        stats = compute_review_burden(decisions)
        assert stats.confirmed_count == 2
        assert stats.false_positive_count == 1
        assert stats.overall_precision == 2 / 3

    def test_hold_decisions_excluded_from_precision(self):
        decisions = [
            OperatorDecision("z1", "S1", "confirmed"),
            OperatorDecision("z2", "S1", "false_positive"),
            OperatorDecision("z3", "S1", "hold"),
            OperatorDecision("z4", "S1", "hold"),
        ]
        stats = compute_review_burden(decisions)
        assert stats.hold_count == 2
        # Precision = 1/(1+1) = 0.5 — hold 제외
        assert stats.overall_precision == 0.5

    def test_top_queue_precision_filters_by_rank(self):
        decisions = [
            OperatorDecision("z1", "S1", "confirmed", rank=1),
            OperatorDecision("z2", "S1", "confirmed", rank=2),
            OperatorDecision("z3", "S1", "false_positive", rank=3),
            OperatorDecision("z4", "S1", "false_positive", rank=10),
            OperatorDecision("z5", "S1", "false_positive", rank=11),
        ]
        # top_n=3 → 2 confirmed + 1 fp → precision 0.667
        stats = compute_review_burden(decisions, top_n=3)
        assert stats.top_queue_size == 3
        assert stats.top_queue_confirmed == 2
        assert stats.top_queue_false_positive == 1
        assert stats.top_queue_precision == 2 / 3
        # overall precision should still include rank 10/11
        assert stats.overall_precision == 2 / 5

    def test_top_n_with_no_ranked_items_emits_note(self):
        decisions = [
            OperatorDecision("z1", "S1", "confirmed"),  # rank=None
        ]
        stats = compute_review_burden(decisions, top_n=5)
        assert stats.top_queue_precision is None
        assert any("top_queue_window_empty" in n for n in stats.notes)

    def test_per_sheet_burden(self):
        decisions = [
            OperatorDecision("z1", "S1", "confirmed"),
            OperatorDecision("z2", "S1", "false_positive"),
            OperatorDecision("z3", "S1", "false_positive"),
            OperatorDecision("z4", "S2", "confirmed"),
            OperatorDecision("z5", "S2", "false_positive"),
        ]
        stats = compute_review_burden(decisions)
        assert stats.sheet_count == 2
        # 3 false_positive total / 2 sheets = 1.5
        assert stats.false_positive_burden_per_sheet == 1.5
        # (2 confirmed + 3 fp) * 0.5 / 2 sheets = 1.25
        assert stats.review_burden_minutes_per_sheet == 1.25

    def test_minutes_per_decision_is_configurable(self):
        decisions = [
            OperatorDecision("z1", "S1", "confirmed"),
            OperatorDecision("z2", "S1", "false_positive"),
        ]
        stats = compute_review_burden(decisions, minutes_per_decision=2.0)
        # 2 decisions * 2 min / 1 sheet = 4 min
        assert stats.review_burden_minutes_per_sheet == 4.0
        assert stats.minutes_per_decision == 2.0

    def test_zero_sheet_id_excludes_sheet_from_count(self):
        decisions = [
            OperatorDecision("z1", "", "confirmed"),
            OperatorDecision("z2", "", "confirmed"),
        ]
        stats = compute_review_burden(decisions)
        assert stats.sheet_count == 0
        assert stats.false_positive_burden_per_sheet is None

    def test_dict_inputs_normalised(self):
        decisions = [
            {"zone_id": "z1", "sheet_id": "S1", "decision": "confirmed", "rank": 1},
            {"zone_id": "z2", "sheet_id": "S1", "decision": "false_positive", "rank": 2},
        ]
        stats = compute_review_burden(decisions, top_n=2)
        assert stats.total_decisions == 2
        assert stats.top_queue_precision == 0.5

    def test_invalid_input_logged_as_note(self):
        decisions = [
            "not a decision",
            None,
            OperatorDecision("z", "S", "confirmed"),
        ]
        stats = compute_review_burden(decisions)
        assert stats.total_decisions == 1
        assert any("non_decision_skipped" in n for n in stats.notes)


class TestReviewBurdenFromDict:
    def test_round_trip(self):
        original = compute_review_burden(
            [
                OperatorDecision("z1", "S1", "confirmed", rank=1),
                OperatorDecision("z2", "S1", "false_positive", rank=2),
            ],
            top_n=2,
        )
        roundtripped = review_burden_from_dict(original.to_dict())
        assert roundtripped.total_decisions == original.total_decisions
        assert roundtripped.confirmed_count == original.confirmed_count
        assert roundtripped.false_positive_count == original.false_positive_count
        assert roundtripped.top_queue_precision == original.top_queue_precision

    def test_invalid_input_returns_default(self):
        assert review_burden_from_dict(None).total_decisions == 0
        assert review_burden_from_dict("not a dict").total_decisions == 0


class TestToDict:
    def test_zero_decisions_round_trip_safe(self):
        stats = ReviewBurdenStats()
        payload = stats.to_dict()
        assert payload["overall_precision"] is None
        assert payload["false_positive_burden_per_sheet"] is None
        assert payload["minutes_per_decision"] == DEFAULT_MINUTES_PER_DECISION
