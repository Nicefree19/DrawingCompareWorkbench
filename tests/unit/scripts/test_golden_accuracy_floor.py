# -*- coding: utf-8 -*-
"""CI golden-accuracy floor gate — measure_golden_accuracy_baseline.floor_failures.

Locks the regression-gate semantics wired into cad-format-regression.yml: flag a
drop below the floor, stay silent within it. The floor sits just under the
measured baseline (p=0.556 / r=0.714 / noise_fp=0) so it catches real detection
regressions without re-enabling the aspirational release thresholds (0.90/0.85)
the synthetic corpus cannot meet.
"""
from __future__ import annotations

from scripts.measure_golden_accuracy_baseline import floor_failures


def _agg(precision, recall, noise_fp):
    return {
        "micro_precision": precision,
        "micro_recall": recall,
        "noise_fixture_fp_total": noise_fp,
    }


def test_within_floor_passes():
    agg = _agg(0.556, 0.714, 0)
    assert floor_failures(agg, min_precision=0.50, min_recall=0.68, max_noise_fp=0) == []


def test_noise_fp_regression_fails():
    agg = _agg(0.556, 0.714, 1)
    failures = floor_failures(agg, min_precision=0.50, min_recall=0.68, max_noise_fp=0)
    assert any("noise-fixture FP" in f for f in failures)


def test_precision_and_recall_drop_fail():
    agg = _agg(0.40, 0.55, 0)
    failures = floor_failures(agg, min_precision=0.50, min_recall=0.68, max_noise_fp=0)
    assert any("precision" in f for f in failures)
    assert any("recall" in f for f in failures)


def test_none_metrics_fail_when_floor_set():
    agg = _agg(None, None, 0)
    failures = floor_failures(agg, min_precision=0.50, min_recall=0.68, max_noise_fp=0)
    assert len(failures) == 2  # precision + recall both None -> both fail


def test_no_floors_means_no_failures():
    agg = _agg(0.0, 0.0, 999)
    assert floor_failures(agg, min_precision=None, min_recall=None, max_noise_fp=None) == []
