from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scripts import build_accuracy_synthetic_controls as builder
from scripts import evaluate_accuracy_evidence as evaluator


def test_evaluate_synthetic_controls_reports_structural_metrics(tmp_path: Path) -> None:
    output_dir = tmp_path / "controls"
    manifest_path = tmp_path / "synthetic_manifest.json"
    truth_path = tmp_path / "synthetic_truth.json"
    builder.build_controls(
        output_dir,
        manifest_path=manifest_path,
        truth_path=truth_path,
        clean=True,
    )

    report = evaluator.evaluate_evidence(manifest_path, truth_path)

    assert report["status"] == "blocked"
    assert report["summary"]["active_pair_count"] == 7
    assert report["summary"]["evaluated_pair_count"] == 7
    assert report["summary"]["skipped_pair_count"] == 0
    assert report["summary"]["tp_count"] == 4
    assert report["summary"]["tn_count"] == 3
    assert report["summary"]["fp_count"] == 0
    assert report["summary"]["fn_count"] == 0
    assert report["summary"]["precision"] == 1.0
    assert report["summary"]["recall"] == 1.0
    assert report["by_pair_type"]["non_structural_noise"]["tn_count"] == 3
    assert report["by_pair_type"]["block_transform_case"]["tp_count"] == 2
    assert report["by_pair_type"]["import_edge_case"]["tp_count"] == 2
    assert report["target_assessment"]["internal_pilot_accuracy"]["status"] == "blocked"
    assert "active_pair_count=7/50" in report["target_assessment"]["internal_pilot_accuracy"]["blockers"]


def test_evaluate_skips_non_fixture_dwg_without_marking_failure(tmp_path: Path) -> None:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015plain")
    after.write_bytes(b"AC1015plain")
    manifest = tmp_path / "manifest.json"
    truth = tmp_path / "truth.json"
    manifest.write_text(
        json.dumps(
            {
                "files": [
                    _file("before", before),
                    _file("after", after),
                ]
            }
        ),
        encoding="utf-8",
    )
    truth.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "plain",
                        "before_file_id": "before",
                        "after_file_id": "after",
                        "pair_type": "small_geometry_change",
                        "expected_changed": True,
                        "expected_change_count": 1,
                        "expected_changes": [],
                        "reviewer_status": "agent_draft",
                        "confidence": "low",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = evaluator.evaluate_evidence(manifest, truth)

    assert report["status"] == "skipped"
    assert report["summary"]["evaluated_pair_count"] == 0
    assert report["summary"]["skipped_pair_count"] == 1
    assert report["pairs"][0]["skip_reason"] == "requires_non_fixture_dwg_backend"


def test_evaluate_reuses_import_cache_for_duplicate_file_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_dir = tmp_path / "controls"
    manifest_path = tmp_path / "synthetic_manifest.json"
    truth_path = tmp_path / "synthetic_truth.json"
    builder.build_controls(
        output_dir,
        manifest_path=manifest_path,
        truth_path=truth_path,
        clean=True,
    )
    truth_payload = json.loads(truth_path.read_text(encoding="utf-8"))
    first_pair = dict(truth_payload["pairs"][0])
    duplicate_pair = dict(first_pair)
    duplicate_pair["pair_id"] = f"{first_pair['pair_id']}_duplicate"
    truth_payload["pairs"] = [first_pair, duplicate_pair]
    truth_path.write_text(json.dumps(truth_payload), encoding="utf-8")

    calls: Counter[str] = Counter()
    original_import_file = evaluator.DwgImporter.import_file

    def counting_import(self, path):
        calls[str(path)] += 1
        return original_import_file(self, path)

    monkeypatch.setattr(evaluator.DwgImporter, "import_file", counting_import)

    report = evaluator.evaluate_evidence(manifest_path, truth_path)

    assert report["summary"]["evaluated_pair_count"] == 2
    assert sum(calls.values()) == 2
    assert report["pairs"][0]["import_cache"] == {"before_hit": False, "after_hit": False}
    assert report["pairs"][1]["import_cache"] == {"before_hit": True, "after_hit": True}


def test_evaluate_non_fixture_dwg_reports_unavailable_backend(tmp_path: Path) -> None:
    manifest, truth = _plain_dwg_manifest_and_truth(tmp_path)

    report = evaluator.evaluate_evidence(manifest, truth, dwg_backend="commercial_sdk")

    assert report["status"] == "skipped"
    assert report["summary"]["evaluated_pair_count"] == 0
    assert report["summary"]["skipped_pair_count"] == 1
    assert report["pairs"][0]["skip_reason"] == "dwg_backend_unavailable"
    assert report["pairs"][0]["dwg_backend"]["mode"] == "commercial_sdk"


def test_evaluate_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    output_dir = tmp_path / "controls"
    manifest_path = tmp_path / "synthetic_manifest.json"
    truth_path = tmp_path / "synthetic_truth.json"
    report_json = tmp_path / "metric.json"
    report_md = tmp_path / "metric.md"
    builder.build_controls(
        output_dir,
        manifest_path=manifest_path,
        truth_path=truth_path,
        clean=True,
    )

    exit_code = evaluator.main(
        [
            "--manifest",
            str(manifest_path),
            "--truth",
            str(truth_path),
            "--report-json",
            str(report_json),
            "--report-md",
            str(report_md),
            "--allow-blocked",
        ]
    )

    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["summary"]["evaluated_pair_count"] == 7
    assert report_md.read_text(encoding="utf-8").startswith("# Accuracy Metric Report")


def test_evaluate_cli_returns_nonzero_when_target_blocked(tmp_path: Path) -> None:
    output_dir = tmp_path / "controls"
    manifest_path = tmp_path / "synthetic_manifest.json"
    truth_path = tmp_path / "synthetic_truth.json"
    report_json = tmp_path / "metric.json"
    report_md = tmp_path / "metric.md"
    builder.build_controls(
        output_dir,
        manifest_path=manifest_path,
        truth_path=truth_path,
        clean=True,
    )

    exit_code = evaluator.main(
        [
            "--manifest",
            str(manifest_path),
            "--truth",
            str(truth_path),
            "--report-json",
            str(report_json),
            "--report-md",
            str(report_md),
        ]
    )

    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["status"] == "blocked"


def _file(file_id: str, path: Path) -> dict[str, object]:
    return {
        "file_id": file_id,
        "absolute_path": str(path),
        "sha256": "0" * 64,
        "file_size_bytes": path.stat().st_size,
        "dwg_version": "AC1015",
        "source_type": "generated",
        "confidentiality": "public",
    }


def _plain_dwg_manifest_and_truth(tmp_path: Path) -> tuple[Path, Path]:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_bytes(b"AC1015plain")
    after.write_bytes(b"AC1015plain")
    manifest = tmp_path / "manifest.json"
    truth = tmp_path / "truth.json"
    manifest.write_text(
        json.dumps(
            {
                "files": [
                    _file("before", before),
                    _file("after", after),
                ]
            }
        ),
        encoding="utf-8",
    )
    truth.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "plain",
                        "before_file_id": "before",
                        "after_file_id": "after",
                        "pair_type": "small_geometry_change",
                        "expected_changed": True,
                        "expected_change_count": 1,
                        "expected_changes": [],
                        "reviewer_status": "agent_draft",
                        "confidence": "low",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest, truth
