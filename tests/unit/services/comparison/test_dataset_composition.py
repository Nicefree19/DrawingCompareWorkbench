# -*- coding: utf-8 -*-
"""Unit tests for dataset_composition (recommendation #4)."""

from __future__ import annotations

from src.services.comparison.dataset_composition import (
    COVERAGE_KEY,
    DEFAULT_COVERAGE_REQUIREMENTS,
    DEFAULT_STRATIFICATION_REQUIREMENTS,
    DatasetCompositionReport,
    evaluate_dataset_composition,
    render_composition_summary,
)


def _passing_composition() -> dict:
    return {
        "total_pairs": 20,
        "stratification": {
            "cad_pairs": 8,
            "pdf_pairs": 8,
            "blocked_pairs": 1,
            "no_expand_pairs": 2,
            "large_drawing_pairs": 2,
            COVERAGE_KEY: {
                "member": 4,
                "section_dimension": 3,
                "d13_shd13": 3,
                "grid": 3,
                "structural_text": 2,
            },
        },
    }


class TestEvaluateDatasetComposition:
    def test_passing_composition_compliant(self):
        report = evaluate_dataset_composition(_passing_composition())
        assert report.compliant is True
        assert report.stratification_compliant is True
        assert report.coverage_compliant is True
        assert report.shortfalls == ()
        assert report.total_pairs == 20

    def test_missing_composition_block_fails(self):
        report = evaluate_dataset_composition(None)
        assert report.compliant is False
        assert "composition_block_missing" in report.notes
        # All buckets fail with actual=0
        assert all(s.actual == 0 for s in report.shortfalls)

    def test_under_threshold_pdf_pairs_fails(self):
        composition = _passing_composition()
        composition["stratification"]["pdf_pairs"] = 5
        report = evaluate_dataset_composition(composition)
        assert report.stratification_compliant is False
        assert report.coverage_compliant is True
        assert report.compliant is False
        shortfalls = {s.bucket: s for s in report.shortfalls}
        assert "pdf_pairs" in shortfalls
        assert shortfalls["pdf_pairs"].actual == 5
        assert shortfalls["pdf_pairs"].required == 8

    def test_under_threshold_coverage_member_fails(self):
        composition = _passing_composition()
        composition["stratification"][COVERAGE_KEY]["member"] = 2
        report = evaluate_dataset_composition(composition)
        assert report.stratification_compliant is True
        assert report.coverage_compliant is False
        assert report.compliant is False
        shortfalls = {s.bucket: s for s in report.shortfalls}
        assert "member" in shortfalls
        assert shortfalls["member"].category == "coverage"
        assert shortfalls["member"].actual == 2

    def test_invalid_stratification_block_type(self):
        composition = {"total_pairs": 20, "stratification": "garbage"}
        report = evaluate_dataset_composition(composition)
        assert report.compliant is False
        assert "stratification_block_invalid_type" in report.notes
        # All stratification + coverage buckets should fail
        assert len(report.shortfalls) >= len(DEFAULT_STRATIFICATION_REQUIREMENTS)

    def test_custom_requirements_override(self):
        composition = _passing_composition()
        composition["stratification"]["cad_pairs"] = 6
        # Default cad_pairs=8 would fail, but override to 5 → pass
        report = evaluate_dataset_composition(
            composition,
            requirements={**DEFAULT_STRATIFICATION_REQUIREMENTS, "cad_pairs": 5},
        )
        assert report.compliant is True
        assert report.applied_requirements["cad_pairs"] == 5

    def test_invalid_coverage_block_type(self):
        composition = _passing_composition()
        composition["stratification"][COVERAGE_KEY] = "garbage"
        report = evaluate_dataset_composition(composition)
        assert report.coverage_compliant is False
        assert "coverage_buckets_invalid_type" in report.notes


class TestDatasetCompositionReportToDict:
    def test_serialise_passing_report(self):
        report = evaluate_dataset_composition(_passing_composition())
        payload = report.to_dict()
        assert payload["compliant"] is True
        assert payload["total_pairs"] == 20
        assert payload["shortfalls"] == []
        assert "applied_requirements" in payload
        assert "applied_coverage" in payload

    def test_serialise_failing_report(self):
        report = evaluate_dataset_composition(None)
        payload = report.to_dict()
        assert payload["compliant"] is False
        # shortfalls list with all 0/required entries
        assert len(payload["shortfalls"]) > 0
        first = payload["shortfalls"][0]
        assert "bucket" in first
        assert "actual" in first
        assert "required" in first
        assert "shortfall" in first


class TestRenderCompositionSummary:
    def test_basic_summary_from_pairs(self):
        pairs = [
            {"source_format": "cad", "coverage_buckets": ["member", "grid"]},
            {"source_format": "cad", "coverage_buckets": ["section_dimension"]},
            {"source_format": "pdf", "no_expand": True, "coverage_buckets": ["member"]},
            {
                "source_format": "blocked",
                "is_large_drawing": True,
                "coverage_buckets": ["d13_shd13"],
            },
        ]
        summary = render_composition_summary({}, pairs)
        assert summary["total_pairs"] == 4
        strat = summary["stratification"]
        assert strat["cad_pairs"] == 2
        assert strat["pdf_pairs"] == 1
        assert strat["blocked_pairs"] == 1
        assert strat["no_expand_pairs"] == 1
        assert strat["large_drawing_pairs"] == 1
        assert strat[COVERAGE_KEY]["member"] == 2
        assert strat[COVERAGE_KEY]["section_dimension"] == 1
        assert strat[COVERAGE_KEY]["grid"] == 1
        assert strat[COVERAGE_KEY]["d13_shd13"] == 1

    def test_skipped_invalid_pair_entries(self):
        pairs = [None, "garbage", {"source_format": "cad"}]
        summary = render_composition_summary({}, pairs)
        assert summary["total_pairs"] == 1
        assert summary["stratification"]["cad_pairs"] == 1
