from __future__ import annotations

import json
from pathlib import Path

from scripts.evaluate_structural_evidence_fixtures import (
    build_report,
    load_matrix,
    render_markdown,
    validate_matrix,
)


ROOT = Path(__file__).resolve().parents[3]


def test_fixture_matrix_is_valid_and_source_control_safe() -> None:
    matrix = load_matrix(root=ROOT)

    assert validate_matrix(matrix) == []
    assert matrix["schema_version"] == "structural-evidence-fixture-matrix/v0.1"
    matrix_text = json.dumps(matrix, ensure_ascii=False)
    assert "D:/00.Work_AI_Tool/07.Dwg_diff" not in matrix_text
    assert "canonical_drawing" not in matrix_text


def test_structural_fixture_matrix_runs_expected_quality_gate(tmp_path: Path) -> None:
    matrix = load_matrix(root=ROOT)

    report = build_report(matrix, root=ROOT, artifact_dir=tmp_path)

    assert report["status"] == "ok"
    assert report["summary"]["passed_count"] == report["summary"]["case_count"]
    assert report["summary"]["pass_rate"] == 1.0
    by_case = {case["case_id"]: case for case in report["cases"]}
    assert by_case["single_text_reference"]["source_health"] == "parsed"
    assert by_case["single_text_reference"]["evidence_source_kinds"] == ["drawing_anchor"]
    assert by_case["large_generated_grid"]["evidence_count"] == 30
    assert by_case["large_generated_grid"]["output_json_bytes"] <= 220000
    assert by_case["unsupported_objects_partial"]["source_health"] == "partial"
    assert by_case["missing_path_fail_closed"]["source_health"] == "missing"
    assert by_case["missing_path_fail_closed"]["exit_code"] == 2
    assert by_case["unsupported_dwg_fail_closed"]["source_health"] == "unsupported"
    assert by_case["comparison_changed_pair"]["evidence_source_kinds"] == ["comparison_diff"]
    assert by_case["comparison_identical_pair"]["evidence_count"] == 0
    assert all(not case["safety_findings"] for case in report["cases"])


def test_render_markdown_records_pass_rate_and_backlog(tmp_path: Path) -> None:
    matrix = load_matrix(root=ROOT)
    report = build_report(matrix, root=ROOT, artifact_dir=tmp_path)

    markdown = render_markdown(report)

    assert "Pass rate: `7/7`" in markdown
    assert "False Positive Backlog" in markdown
    assert "False Negative Backlog" in markdown
    assert "comparison_identical_pair" in markdown
