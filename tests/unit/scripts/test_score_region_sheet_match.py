from __future__ import annotations

import json
from pathlib import Path

from scripts.score_region_sheet_match import score_region_match_summary


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_score_region_match_summary_maps_frame_ids_to_sheet_metrics(tmp_path: Path) -> None:
    ground_truth = tmp_path / "multi_sheet_ground_truth.json"
    region_summary = tmp_path / "region_match_summary.json"
    out = tmp_path / "score.json"

    _write_json(
        ground_truth,
        {
            "schema_version": 1,
            "synthetic": True,
            "fixtures": [
                {
                    "name": "fixture-a",
                    "synthetic": True,
                    "before_sheets": [
                        {"id": "before-01"},
                        {"id": "before-02"},
                        {"id": "before-03"},
                    ],
                    "after_sheets": [
                        {"id": "after-01"},
                        {"id": "after-02"},
                        {"id": "after-03"},
                    ],
                    "ground_truth": [
                        {"before_id": "before-01", "after_id": "after-01"},
                        {"before_id": "before-02", "after_id": "after-02"},
                        {
                            "before_id": "before-03",
                            "after_id": "after-03",
                            "manual_required": True,
                        },
                    ],
                }
            ],
        },
    )
    _write_json(
        region_summary,
        {
            "schema_version": 1,
            "summaries": [
                {
                    "matches": [
                        {
                            "before_region_id": "before-frame-1",
                            "after_region_id": "after-frame-1",
                            "status": "auto_matched",
                            "score": 0.99,
                        },
                        {
                            "before_region_id": "before-frame-2",
                            "after_region_id": "after-frame-2",
                            "status": "auto_matched",
                            "score": 0.98,
                        },
                        {
                            "before_region_id": "before-frame-3",
                            "after_region_id": "",
                            "status": "unmatched_before",
                            "score": 0.0,
                        },
                    ]
                }
            ],
        },
    )

    payload = score_region_match_summary(
        region_summary_path=region_summary,
        ground_truth_path=ground_truth,
        fixture_name="fixture-a",
        out=out,
    )

    assert out.exists()
    assert payload["status"] == "passed"
    assert payload["precision"] == 1.0
    assert payload["recall"] == 1.0
    assert payload["false_match_count"] == 0
    assert payload["manual_match_required_count"] == 1
    assert payload["prediction_count"] == 2
    assert payload["predictions"] == [
        {
            "before_id": "before-01",
            "after_id": "after-01",
            "status": "auto_confirmed",
            "confidence": 0.99,
        },
        {
            "before_id": "before-02",
            "after_id": "after-02",
            "status": "auto_confirmed",
            "confidence": 0.98,
        },
    ]
