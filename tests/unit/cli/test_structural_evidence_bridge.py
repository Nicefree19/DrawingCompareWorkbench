from __future__ import annotations

import json
from pathlib import Path

from src.cli.structural_evidence_bridge import main


ROOT = Path(__file__).resolve().parents[3]
DXF_DIR = ROOT / "tests" / "data" / "comparison" / "cad_samples" / "dxf"


def test_analyze_cli_writes_artifacts_and_prints_compact_json(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--json",
            "analyze",
            "--path",
            str(DXF_DIR / "text_centered.dxf"),
            "--question",
            "Find GRID-A1",
            "--artifact-dir",
            str(tmp_path),
            "--max-evidence",
            "2",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["schema_version"] == "structural-drawing-evidence/v0.1"
    assert payload["source"]["source_health"] == "parsed"
    assert len(payload["evidence"]) <= 2
    assert "canonical_drawing" not in payload

    artifact_paths = payload["artifact_paths"]
    assert Path(artifact_paths["compact"]).is_file()
    assert Path(artifact_paths["full_evidence"]).is_file()
    assert Path(artifact_paths["canonical_summary"]).is_file()
    assert Path(artifact_paths["diagnostics"]).is_file()
    assert Path(artifact_paths["manifest"]).is_file()

    full_payload = json.loads(Path(artifact_paths["full_evidence"]).read_text(encoding="utf-8"))
    assert full_payload["run_id"] == payload["run_id"]
    assert len(full_payload["evidence"]) >= len(payload["evidence"])
    manifest = json.loads(Path(artifact_paths["manifest"]).read_text(encoding="utf-8"))
    assert manifest["payload_kind"] == "structural_evidence"
    assert manifest["raw_payload_included"] is False
    assert manifest["judgment_level"] == "issue_suggestion_only"
    assert manifest["max_evidence"] == 2


def test_draft_cli_writes_review_draft_artifacts_from_compact_packet(tmp_path, capsys) -> None:
    analyze_exit = main(
        [
            "--json",
            "analyze",
            "--path",
            str(DXF_DIR / "text_centered.dxf"),
            "--question",
            "Check missing section reference for GRID-A1 and C1",
            "--artifact-dir",
            str(tmp_path),
            "--max-evidence",
            "3",
        ]
    )
    compact = json.loads(capsys.readouterr().out)["artifact_paths"]["compact"]

    draft_exit = main(
        [
            "--json",
            "draft",
            "--packet",
            compact,
            "--artifact-dir",
            str(tmp_path),
            "--language",
            "en",
            "--draft-type",
            "rfi_reply",
        ]
    )
    draft = json.loads(capsys.readouterr().out)

    assert analyze_exit == 0
    assert draft_exit == 0
    assert draft["schema_version"] == "structural-review-draft/v0.1"
    assert draft["source_packet_schema"] == "structural-drawing-evidence/v0.1"
    assert draft["status"] == "drafted"
    assert draft["draft_type"] == "rfi_reply"
    assert draft["safety"]["auto_submit_allowed"] is False
    assert draft["safety"]["human_review_required"] is True
    assert Path(draft["artifact_paths"]["draft_json"]).is_file()
    assert Path(draft["artifact_paths"]["draft_markdown"]).is_file()
    manifest = json.loads(Path(draft["artifact_paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["payload_kind"] == "structural_review_draft"
    assert manifest["draft_type"] == "rfi_reply"
    assert manifest["judgment_level"] == "issue_suggestion_only"
    assert manifest["human_review_required"] is True
    assert manifest["raw_payload_included"] is False
    assert "canonical_drawing" not in json.dumps(draft, ensure_ascii=False)


def test_draft_cli_blocks_invalid_draft_type_as_json(tmp_path, capsys) -> None:
    analyze_exit = main(
        [
            "--json",
            "analyze",
            "--path",
            str(DXF_DIR / "text_centered.dxf"),
            "--artifact-dir",
            str(tmp_path),
        ]
    )
    compact = json.loads(capsys.readouterr().out)["artifact_paths"]["compact"]

    draft_exit = main(
        [
            "--json",
            "draft",
            "--packet",
            compact,
            "--artifact-dir",
            str(tmp_path),
            "--language",
            "en",
            "--draft-type",
            "send_approval",
        ]
    )
    draft = json.loads(capsys.readouterr().out)

    assert analyze_exit == 0
    assert draft_exit == 2
    assert draft["schema_version"] == "structural-review-draft/v0.1"
    assert draft["status"] == "blocked"
    assert draft["draft_type"] == "review_note"
    assert draft["safety"]["auto_submit_allowed"] is False
    assert "Unsupported draft type" in draft["draft"]["body"]


def test_compare_cli_writes_comparison_evidence_packet(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--json",
            "compare",
            "--before",
            str(DXF_DIR / "simple_base.dxf"),
            "--after",
            str(DXF_DIR / "simple_modified.dxf"),
            "--question",
            "Review drawing changes",
            "--artifact-dir",
            str(tmp_path),
            "--max-evidence",
            "10",
        ]
    )

    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema_version"] == "structural-drawing-evidence/v0.1"
    assert payload["source"]["source_health"] == "parsed"
    assert payload["diagnostics"]["comparison"]["schema_version"] == "drawing-diff/v1"
    assert payload["evidence"]
    assert payload["evidence"][0]["source_kind"] == "comparison_diff"
    assert payload["issue_suggestions"][0]["kind"] == "comparison_diff_review"
    assert "canonical_drawing" not in json.dumps(payload, ensure_ascii=False)
    assert Path(payload["artifact_paths"]["compact"]).is_file()
    assert Path(payload["artifact_paths"]["comparison_diff"]).is_file()
    manifest = json.loads(Path(payload["artifact_paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["payload_kind"] == "structural_comparison_evidence"
    assert manifest["raw_payload_included"] is False


def test_analyze_cli_reports_failed_missing_path_as_json(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--json",
            "analyze",
            "--path",
            str(tmp_path / "missing.dxf"),
            "--artifact-dir",
            str(tmp_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "failed"
    assert payload["source"]["source_health"] == "missing"
    assert payload["diagnostics"]["error_code"] == "CAD_PATH_INVALID"


def test_analyze_cli_reports_unsupported_dwg_diagnostics(tmp_path, capsys) -> None:
    sample = tmp_path / "blocked_ac1032.dwg"
    sample.write_bytes(b"AC1032" + (b"0" * 100))

    exit_code = main(
        [
            "--json",
            "analyze",
            "--path",
            str(sample),
            "--artifact-dir",
            str(tmp_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "failed"
    assert payload["source"]["source_health"] == "unsupported"
    assert payload["diagnostics"]["dwg_native"]["blocking_stage"] == "section_map_decoder"
    assert payload["diagnostics"]["dwg_native"]["status"] == "unsupported_version"
