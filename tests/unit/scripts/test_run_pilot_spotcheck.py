"""Deterministic tests for the pilot spotcheck runner.

Two layers:
  * pure transform tests on a fixed detection fixture (fast, no pipeline) — they
    lock the spotcheck-sheet / ground-truth shape and the no-fabrication contract;
  * one real-pipeline golden integration (skipif-guarded, mirroring the e2e
    smoke) proving the runner wires to ``FolderComparePipeline`` and lists the
    known change.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.run_pilot_spotcheck import (
    GROUND_TRUTH_HEADER,
    build_ground_truth_rows,
    build_spotcheck_md,
    run_pilot_spotcheck,
)

# A fixed slice of a real golden-02 ``top_issues`` row (captured 2026-06-27),
# so the transform tests are deterministic without running the heavy pipeline.
_FIXTURE_ISSUE = {
    "display_label": "after",
    "category": "member",
    "change_type": "mixed",
    "change_type_ko": "혼합",
    "severity_ko": "높음",
    "change_summary_ko": "구조 부재 표기 변경: 구조 검토가 필요한 변경 후보입니다.",
    "major_layers": "BEAM",
    "bbox_text": "-30.0, 345.0 - 1030.0, 460.0",
    "added_count": 1,
    "deleted_count": 1,
    "modified_count": 0,
    "source_format": "cad",
    "detection_source": "cad_entity",
    "bbox_status": "exact",
}

_GOLDEN = (
    Path(__file__).resolve().parents[3] / "tests/data/comparison/golden/dxf/02_single_modification"
)


def test_spotcheck_md_lists_detected_change_with_operator_columns() -> None:
    md = build_spotcheck_md("before → after", [_FIXTURE_ISSUE])
    assert "총 검출 변경(top_issues): **1**" in md
    # detected row carries location + type + Korean summary + add/delete/modify
    assert "BEAM" in md
    assert "혼합·높음" in md
    assert "구조 부재 표기 변경" in md
    assert "+1/-1/~0" in md
    # blank operator columns are present for the dry-run
    for column in ("아는변경?", "검출됨?(Y/N)", "위치정확?(Y/N)", "비고"):
        assert column in md
    # judgement criterion + miss-capture section both exist
    assert "누락" in md


def test_spotcheck_md_handles_zero_detections() -> None:
    md = build_spotcheck_md("a → b", [])
    assert "총 검출 변경(top_issues): **0**" in md
    assert "검출 0" in md


def test_ground_truth_rows_use_existing_schema_facts_only() -> None:
    rows = build_ground_truth_rows([_FIXTURE_ISSUE])
    assert len(rows) == 1
    row = dict(zip(GROUND_TRUTH_HEADER, rows[0]))
    assert row["drawing_label"] == "after"
    assert row["category"] == "member"
    # detection-derived match tokens (facts only), not fabricated semantics
    assert row["summary_contains"] == "BEAM;mixed"
    assert row["source_format"] == "cad"
    assert row["detection_source"] == "cad_entity"
    assert row["bbox_status"] == "exact"
    # honesty: provenance flagged so the row is not mistaken for approved truth
    assert "스켈레톤" in row["notes"]


def test_ground_truth_header_matches_canonical_schema() -> None:
    assert GROUND_TRUTH_HEADER[:6] == [
        "drawing_label",
        "category",
        "summary_contains",
        "source_format",
        "detection_source",
        "bbox_status",
    ]


@pytest.mark.skipif(
    not (_GOLDEN / "before.dxf").exists() or not (_GOLDEN / "after.dxf").exists(),
    reason="golden pair 02_single_modification not present",
)
def test_real_pipeline_golden_emits_spotcheck_and_truth(tmp_path: Path) -> None:
    summary = run_pilot_spotcheck(_GOLDEN / "before.dxf", _GOLDEN / "after.dxf", tmp_path / "run")
    out = Path(summary["output_dir"])

    assert summary["detected_count"] >= 1
    md = (out / "pilot_spotcheck.md").read_text(encoding="utf-8")
    # the known single modification sits on the BEAM layer
    assert "BEAM" in md

    csv_path = out / "review_ground_truth.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        data_rows = list(reader)
    assert header == GROUND_TRUTH_HEADER
    assert data_rows  # at least one detection-derived skeleton row
    assert any("BEAM" in cell for row in data_rows for cell in row)
