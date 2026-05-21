# -*- coding: utf-8 -*-
"""Tests for the dataset_composition audit gate (recommendation #4)."""

from __future__ import annotations

from pathlib import Path

from scripts import audit_drawing_compare_mvp_exit as audit


def _passing_manifest() -> dict:
    return {
        "evidence_level": "customer_grade",
        "dataset_composition": {
            "total_pairs": 20,
            "stratification": {
                "cad_pairs": 8,
                "pdf_pairs": 8,
                "blocked_pairs": 1,
                "no_expand_pairs": 2,
                "large_drawing_pairs": 2,
                "coverage_buckets": {
                    "member": 4,
                    "section_dimension": 3,
                    "d13_shd13": 3,
                    "grid": 3,
                    "structural_text": 2,
                },
            },
        },
    }


class TestStrictMode:
    def test_passes_when_all_thresholds_met(self):
        result = audit._check_dataset_composition(
            _passing_manifest(),
            Path("/tmp/manifest.json"),
            composition_mode="strict",
        )
        assert result.passed is True
        assert result.name == "dataset_composition_stratified"

    def test_fails_when_pdf_under_threshold(self):
        manifest = _passing_manifest()
        manifest["dataset_composition"]["stratification"]["pdf_pairs"] = 5
        result = audit._check_dataset_composition(
            manifest,
            Path("/tmp/manifest.json"),
            composition_mode="strict",
        )
        assert result.passed is False
        assert "pdf_pairs=5/8" in result.detail

    def test_fails_when_coverage_under_threshold(self):
        manifest = _passing_manifest()
        manifest["dataset_composition"]["stratification"]["coverage_buckets"]["member"] = 1
        result = audit._check_dataset_composition(
            manifest,
            Path("/tmp/manifest.json"),
            composition_mode="strict",
        )
        assert result.passed is False
        assert "member=1/4(coverage)" in result.detail

    def test_fails_when_composition_block_missing(self):
        result = audit._check_dataset_composition(
            {"evidence_level": "customer_grade"},  # no dataset_composition
            Path("/tmp/manifest.json"),
            composition_mode="strict",
        )
        assert result.passed is False
        assert "composition_block_missing" in result.detail

    def test_fails_when_manifest_is_none(self):
        result = audit._check_dataset_composition(
            None,
            None,
            composition_mode="strict",
        )
        assert result.passed is False


class TestAdvisoryMode:
    def test_advisory_passes_even_with_shortfalls(self):
        manifest = _passing_manifest()
        manifest["dataset_composition"]["stratification"]["pdf_pairs"] = 0
        result = audit._check_dataset_composition(
            manifest,
            Path("/tmp/manifest.json"),
            composition_mode="advisory",
        )
        assert result.passed is True
        # Detail still records the shortfall for visibility.
        assert "pdf_pairs=0/8" in result.detail

    def test_advisory_passes_when_block_missing(self):
        result = audit._check_dataset_composition(
            None,
            None,
            composition_mode="advisory",
        )
        assert result.passed is True
        assert "composition_mode=advisory" in result.detail


class TestRunAuditIntegration:
    def test_run_audit_accepts_composition_kwargs(self):
        report = audit.run_audit(
            result_dirs=[],
            require_dataset_composition=True,
            composition_mode="strict",
        )
        check_names = {check["name"] for check in report["checks"]}
        assert "dataset_composition_stratified" in check_names
