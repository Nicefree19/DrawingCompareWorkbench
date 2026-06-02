from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_adr004_phase0c_baselines import build_report, render_markdown


def _summary(path: Path, versions: list[dict]) -> Path:
    payload = {
        "schema_version": "adr004-version-sample-pack-validation/v1",
        "status": "partial",
        "sample_pack": str(path.parent),
        "summary": {"version_count": len(versions)},
        "manifest_errors": [],
        "validation_errors": [],
        "versions": versions,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _version(
    code: str,
    *,
    pair_kind: str,
    compare_status: str,
    entities: tuple[int, int] = (10, 11),
    diff: dict | None = None,
) -> dict:
    return {
        "version": code,
        "pair_kind": pair_kind,
        "outputs": {
            "before": [{"exists": True, "header_matches_expected": True, "actual_size": 100}],
            "after": [{"exists": True, "header_matches_expected": True, "actual_size": 110}],
        },
        "imports": {
            "before": {
                "status": "partial",
                "entity_count": entities[0],
                "warning_codes": ["UNSUPPORTED_ENTITY"],
            },
            "after": {
                "status": "partial",
                "entity_count": entities[1],
                "warning_codes": ["ENTITY_APPROXIMATED"],
            },
        },
        "compare": {
            "status": compare_status,
            "elapsed_ms": 1234.0 if compare_status in {"ok", "partial"} else None,
            "summary": diff,
            "warning_codes": [],
        },
    }


def test_phase0c_selects_compare_ready_record_over_timeout(tmp_path: Path) -> None:
    timeout = _summary(
        tmp_path / "timeout.json",
        [
            _version(
                "AC1032",
                pair_kind="compact_likely_revision_pair",
                compare_status="timeout",
                entities=(100, 100),
            )
        ],
    )
    registered = _summary(
        tmp_path / "registered.json",
        [
            _version(
                "AC1032",
                pair_kind="confirmed_revision_pair_existing_registered_dxf",
                compare_status="partial",
                entities=(7, 8),
                diff={"added": 1, "removed": 2, "modified": 3, "unchanged": 4, "total_changes": 6},
            )
        ],
    )

    report = build_report([timeout, registered], target_versions=("AC1032",), root=Path.cwd())

    assert report["status"] == "ok"
    assert report["summary"]["compare_ready_versions"] == ["AC1032"]
    assert report["versions"]["AC1032"]["pair_kind"] == "confirmed_revision_pair_existing_registered_dxf"
    assert report["versions"]["AC1032"]["diff_summary"]["total_changes"] == 6


def test_phase0c_marks_duplicated_pair_as_import_only_gap(tmp_path: Path) -> None:
    summary = _summary(
        tmp_path / "summary.json",
        [
            _version(
                "AC1018",
                pair_kind="single_file_duplicated_import_baseline_small",
                compare_status="partial",
            )
        ],
    )

    report = build_report([summary], target_versions=("AC1018",), root=Path.cwd())

    assert report["status"] == "partial"
    record = report["versions"]["AC1018"]
    assert record["phase0c_status"] == "import_only_duplicate"
    assert record["compare_baseline_ready"] is False
    assert record["blocking_reason"] == "real_before_after_revision_pair_missing"
    assert report["summary"]["missing_compare_versions"] == ["AC1018"]


def test_phase0c_render_markdown_includes_matrix(tmp_path: Path) -> None:
    summary = _summary(
        tmp_path / "summary.json",
        [
            _version(
                "AC1024",
                pair_kind="compact_likely_revision_pair",
                compare_status="partial",
                diff={"added": 1, "removed": 0, "modified": 0, "unchanged": 9, "total_changes": 1},
            )
        ],
    )

    report = build_report([summary], target_versions=("AC1024",), root=Path.cwd())
    markdown = render_markdown(report)

    assert "ADR-004 Phase 0-C Baseline Metrics" in markdown
    assert "compare_baseline_ready" in markdown
    assert "added 1" in markdown
