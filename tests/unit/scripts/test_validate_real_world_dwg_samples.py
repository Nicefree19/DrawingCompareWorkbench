from __future__ import annotations

import json
from pathlib import Path

from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter

from scripts.validate_real_world_dwg_samples import (
    build_golden_results,
    build_report,
    compare_golden,
    render_markdown,
    validate_manifest,
)


def test_validate_manifest_rejects_duplicate_sample_ids() -> None:
    manifest = {
        "schema_version": "cad-real-world-local/v1",
        "source_root": "D:/missing",
        "samples": [
            {
                "id": "same",
                "path": "a.dwg",
                "format": "dwg",
                "expected_version": "AC1032",
                "expected_size_bytes": 1,
            },
            {
                "id": "same",
                "path": "b.dwg",
                "format": "dwg",
                "expected_version": "AC1032",
                "expected_size_bytes": 1,
            },
        ],
    }

    assert "sample ids must be unique" in validate_manifest(manifest)


def test_build_report_skips_when_local_source_missing() -> None:
    manifest = {
        "schema_version": "cad-real-world-local/v1",
        "source_root": "D:/definitely/missing/dwg/samples",
        "samples": [
            {
                "id": "a",
                "path": "a.dwg",
                "format": "dwg",
                "expected_version": "AC1032",
                "expected_size_bytes": 1,
            }
        ],
        "pairs": [],
    }

    report = build_report(manifest)

    assert report["status"] == "skipped"
    assert report["summary"]["validated_sample_count"] == 0
    assert "Real-World DWG Validation Report" in render_markdown(report)


def test_build_report_marks_planned_dwg_versions_as_unsupported(tmp_path: Path) -> None:
    source_root = tmp_path / "samples"
    source_root.mkdir()
    path = source_root / "future.dwg"
    path.write_bytes(
        b"AC1032"
        + DwgJsonFixtureAdapter.MARKER
        + json.dumps({"model_space": []}).encode("utf-8")
    )
    manifest = {
        "schema_version": "cad-real-world-local/v1",
        "source_root": str(source_root),
        "samples": [
            {
                "id": "future",
                "path": "future.dwg",
                "format": "dwg",
                "expected_version": "AC1032",
                "expected_size_bytes": path.stat().st_size,
            }
        ],
        "pairs": [],
    }

    report = build_report(manifest)

    assert report["status"] == "ok"
    assert report["summary"]["unsupported_versions"] == ["AC1032"]
    assert report["samples"][0]["detected_supported"] is False
    assert report["samples"][0]["import_error_code"] == "DWG_UNSUPPORTED_VERSION"


def test_golden_results_are_compact_and_detect_mismatch() -> None:
    report = {
        "summary": {"unsupported_versions": ["AC1032"]},
        "samples": [
            {
                "id": "sample",
                "expected_version": "AC1032",
                "expected_size_bytes": 10,
                "detected_version": "AC1032",
                "detected_supported": False,
                "import_status": "failed",
                "import_error_code": "DWG_UNSUPPORTED_VERSION",
                "path": "D:/customer/private.dwg",
                "import_message": "private diagnostic",
            }
        ],
        "pairs": [
            {
                "id": "pair",
                "current_import_expectation": "unsupported_version_until_native_reader_expands_beyond_AC1015",
                "descriptor_cache_status": "ok",
                "descriptor_delta": {
                    "old_entity_count": 1,
                    "new_entity_count": 2,
                    "entity_count_delta": 1,
                    "entity_type_delta": {"LINE": 1},
                    "old_layer_count": 3,
                    "new_layer_count": 4,
                    "content_fingerprint_changed": True,
                },
            }
        ],
    }

    golden = build_golden_results(report)
    assert "private.dwg" not in json.dumps(golden)
    assert compare_golden(report, golden) == []

    changed = json.loads(json.dumps(golden))
    changed["pairs"][0]["entity_count_delta"] = 99
    assert compare_golden(report, changed) == ["golden.pairs.pair mismatch"]
