from __future__ import annotations

import json
from pathlib import Path

from scripts import build_accuracy_synthetic_controls as builder
from scripts import validate_accuracy_evidence as evidence
from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter


def test_build_controls_writes_manifest_truth_and_fixture_dwgs(tmp_path: Path) -> None:
    output_dir = tmp_path / "controls"
    manifest_path = tmp_path / "synthetic_manifest.json"
    truth_path = tmp_path / "synthetic_truth.json"

    result = builder.build_controls(
        output_dir,
        manifest_path=manifest_path,
        truth_path=truth_path,
        clean=True,
    )

    manifest = result["manifest"]
    truth = result["truth"]
    assert manifest_path.exists()
    assert truth_path.exists()
    assert manifest["summary"]["file_count"] == 14
    assert truth["summary"]["pair_count"] == 7
    assert truth["summary"]["pair_type_counts"] == {
        "block_transform_case": 2,
        "import_edge_case": 2,
        "non_structural_noise": 3,
    }
    first_file = Path(manifest["files"][0]["absolute_path"])
    assert first_file.read_bytes().startswith(b"AC1015" + DwgJsonFixtureAdapter.MARKER)


def test_synthetic_controls_can_be_validated_as_extra_evidence(tmp_path: Path) -> None:
    output_dir = tmp_path / "controls"
    synthetic_manifest = tmp_path / "synthetic_manifest.json"
    synthetic_truth = tmp_path / "synthetic_truth.json"
    builder.build_controls(
        output_dir,
        manifest_path=synthetic_manifest,
        truth_path=synthetic_truth,
        clean=True,
    )
    base_manifest = tmp_path / "base_manifest.json"
    base_truth = tmp_path / "base_truth.json"
    normalized_manifest = tmp_path / "manifest_normalized.json"
    normalized_truth = tmp_path / "truth_normalized.json"
    report_json = tmp_path / "report.json"
    report_md = tmp_path / "report.md"
    base_manifest.write_text(
        json.dumps(
            [
                {
                    "file_id": "base_before",
                    "absolute_path": str(output_dir / "synth_noise_title_text" / "before.dwg"),
                    "sha256": evidence.sha256_file(output_dir / "synth_noise_title_text" / "before.dwg"),
                    "file_size_bytes": (output_dir / "synth_noise_title_text" / "before.dwg").stat().st_size,
                    "dwg_version": "AC1015",
                    "source_type": "generated",
                    "confidentiality": "public",
                    "license_or_permission": "MIT",
                }
            ]
        ),
        encoding="utf-8",
    )
    base_truth.write_text("[]", encoding="utf-8")

    exit_code = evidence.main(
        [
            "--manifest",
            str(base_manifest),
            "--extra-manifest",
            str(synthetic_manifest),
            "--write-normalized-manifest",
            str(normalized_manifest),
            "--truth",
            str(base_truth),
            "--extra-truth",
            str(synthetic_truth),
            "--write-normalized",
            str(normalized_truth),
            "--report-json",
            str(report_json),
            "--report-md",
            str(report_md),
            "--verify-file-hashes",
            "--no-identical-controls",
            "--no-version-resave-controls",
        ]
    )

    report = json.loads(report_json.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["summary"]["pair_type_counts"] == {
        "block_transform_case": 2,
        "import_edge_case": 2,
        "non_structural_noise": 3,
    }
    assert "active pair count is below pilot target: 7/50" in report["warnings"]
    assert "non_structural_noise controls are missing" not in report["warnings"]
    assert "block_transform_case coverage is missing" not in report["warnings"]
    assert "import_edge_case coverage is missing" not in report["warnings"]
