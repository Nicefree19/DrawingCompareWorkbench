# -*- coding: utf-8 -*-
"""Unit tests for the golden accuracy-baseline harness (no compare runs)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from scripts.measure_golden_accuracy_baseline import (
    adapt_prediction,
    aggregate,
    load_truth,
)


@dataclass
class _FakeRecord:
    key: str = "diff:1"
    change_type: str = "modified"
    location: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def test_adapt_parses_bare_xy_string_location():
    # The canonical pipeline emits "x,y" WITHOUT parentheses — the form
    # accuracy_metrics' own regex does not parse (it requires "(x, y)").
    rec = _FakeRecord(location="500.0,400.25", metadata={"entity_type": "LINE"})
    adapted = adapt_prediction(rec)
    assert adapted.location == (500.0, 400.25)


def test_adapt_falls_back_to_bbox_centroid():
    rec = _FakeRecord(
        location=None,
        metadata={"bbox": {"min_x": 0.0, "min_y": 10.0, "max_x": 100.0, "max_y": 20.0}},
    )
    assert adapt_prediction(rec).location == (50.0, 15.0)


def test_adapt_lowercases_entity_type_to_canonical_vocabulary():
    # Golden truth files use uppercase DXF names ("LINE"); the canonical
    # engine emits lowercase ("line"). Without folding, the matcher's exact
    # entity filter rejected every typed truth (verified at distance 0.0).
    rec = _FakeRecord(metadata={"entity_type": "LINE", "layer": "BEAM"})
    adapted = adapt_prediction(rec)
    assert adapted.entity_type == "line"
    assert adapted.layer == "BEAM"


def test_load_truth_lowercases_entity_type_and_parses_location(tmp_path):
    truth = {
        "comment": "one typed truth",
        "expected_changes": [
            {
                "location": [302.5, 400.0],
                "change_type": "modified",
                "layer": "기둥-1F",
                "entity_type": "LINE",
                "tolerance_mm": 50.0,
            },
            {"change_type": "added"},  # location-free entry stays location-free
        ],
    }
    p = tmp_path / "truth.json"
    p.write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    expected, comment = load_truth(p)
    assert comment == "one typed truth"
    assert expected[0].entity_type == "line"
    assert expected[0].location == (302.5, 400.0)
    assert expected[0].tolerance_mm == 50.0
    assert expected[1].location is None and expected[1].entity_type is None


def test_aggregate_micro_metrics_and_noise_fixtures():
    rows = [
        {"tp": 1, "fp": 1, "fn": 0, "expected_count": 1, "cosmetic_fp": 0},
        {"tp": 1, "fp": 0, "fn": 1, "expected_count": 2, "cosmetic_fp": 0},
        {"tp": 0, "fp": 3, "fn": 0, "expected_count": 0, "cosmetic_fp": 2},  # noise fixture
    ]
    agg = aggregate(rows)
    assert (agg["tp"], agg["fp"], agg["fn"]) == (2, 4, 1)
    assert abs(agg["micro_precision"] - 2 / 6) < 1e-9
    assert abs(agg["micro_recall"] - 2 / 3) < 1e-9
    assert agg["noise_fixture_count"] == 1
    assert agg["noise_fixture_fp_total"] == 3
    assert agg["cosmetic_fp"] == 2


def test_aggregate_handles_empty_denominators():
    agg = aggregate([{"tp": 0, "fp": 0, "fn": 0, "expected_count": 0, "cosmetic_fp": 0}])
    assert agg["micro_precision"] is None
    assert agg["micro_recall"] is None
    assert agg["micro_f1"] is None
