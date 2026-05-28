"""Tests for the multi-detail region compare pilot harness."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import validate_multi_detail_region_compare as pilot


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_run(
    root: Path,
    *,
    whole_modelspace: int = 0,
    primary_zones: int = 2,
    review_required: int = 0,
    unmatched_before: int = 0,
    write_success_manifest: bool = True,
) -> Path:
    artifact_dir = root / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    if write_success_manifest:
        (root / "_SUCCESS").write_text("", encoding="utf-8")
        _write_json(root / "run_manifest.json", {"status": "completed"})
    regions = [
        {"region_id": f"r{i}", "detection_method": "whole_modelspace" if i < whole_modelspace else "frame"}
        for i in range(6)
    ]
    _write_json(
        artifact_dir / "region_detection_summary.json",
        {
            "source_count": 2,
            "region_count": len(regions),
            "results": [
                {"side": "before", "regions": regions[:3]},
                {"side": "after", "regions": regions[3:]},
            ],
        },
    )
    _write_json(
        artifact_dir / "region_match_summary.json",
        {
            "pair_count": 1,
            "summaries": [
                {
                    "auto_matched_count": 3,
                    "manual_matched_count": 0,
                    "review_required_count": review_required,
                    "unmatched_before_count": unmatched_before,
                    "unmatched_after_count": 0,
                }
            ],
        },
    )
    _write_json(
        artifact_dir / "localized_compare_summary.json",
        {"pair_count": 1, "summaries": [{"total_zones": 5}]},
    )
    _write_json(
        artifact_dir / "localized_change_zones_v2.json",
        {
            "primary_enabled": True,
            "status": "passed",
            "zones": [{"zone_id": f"z{i}"} for i in range(primary_zones)],
        },
    )
    _write_json(
        artifact_dir / "region_aware_status.json",
        {
            "region_local_primary_enabled": True,
            "region_local_primary_status": "passed",
            "region_local_primary_zone_count": primary_zones,
        },
    )
    _write_json(
        artifact_dir / "region_viewer" / "region_viewer_manifest.json",
        {
            "entry_count": 1,
            "entries": [
                {
                    "entry_id": "z0",
                    "before": {"render_status": "rendered"},
                    "after": {"render_status": "rendered"},
                }
            ],
        },
    )
    _write_json(
        artifact_dir / "change_zones.json",
        {"zones": [{"zone_id": f"global-{i}"} for i in range(8)]},
    )
    return root


def test_run_pilot_collects_existing_runs_and_passes_with_review_evidence(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a")
    screenshots = []
    for index in range(3):
        path = tmp_path / f"shot-{index}.png"
        path.write_bytes(b"png")
        screenshots.append(str(path))
    manifest = tmp_path / "pilot-manifest.json"
    _write_json(
        manifest,
        {
            "runs": [
                {
                    "case_id": "case-a",
                    "output_dir": str(run_dir),
                    "expected_region_count": 6,
                    "expected_match_count": 3,
                    "review_evidence": {
                        "reviewed_region_matches": 4,
                        "correct_region_matches": 4,
                        "global_false_positive_count": 8,
                        "region_local_false_positive_count": 2,
                    },
                    "screenshots": screenshots,
                }
            ]
        },
    )

    payload = pilot.run_pilot(manifest, tmp_path / "out", collect_only=True)

    assert payload["overall_status"] == "passed"
    assert payload["totals"]["detected_region_rate"] == 1.0
    assert payload["totals"]["whole_modelspace_fallback_rate"] == 0.0
    assert payload["acceptance"]["artifact_integrity"]["status"] == "passed"
    assert payload["acceptance"]["approved_region_match_rate"]["status"] == "passed"
    assert payload["acceptance"]["unresolved_region_matches"]["status"] == "passed"
    assert payload["acceptance"]["region_local_primary_compare"]["status"] == "passed"
    assert payload["totals"]["false_positive_reduction"] == 0.75
    assert Path(payload["summary_json"]).exists()
    assert Path(payload["report_md"]).exists()


def test_run_pilot_needs_review_evidence_when_human_labels_are_missing(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a")
    screenshots = []
    for index in range(3):
        path = tmp_path / f"shot-{index}.png"
        path.write_bytes(b"png")
        screenshots.append(str(path))
    manifest = tmp_path / "pilot-manifest.json"
    _write_json(
        manifest,
        {
            "runs": [
                {
                    "case_id": "case-a",
                    "output_dir": str(run_dir),
                    "expected_region_count": 6,
                    "expected_match_count": 3,
                    "screenshots": screenshots,
                }
            ]
        },
    )

    payload = pilot.run_pilot(manifest, tmp_path / "out", collect_only=True)

    assert payload["overall_status"] == "needs_review_evidence"
    assert payload["acceptance"]["user_approved_match_accuracy"]["status"] == "not_evaluable"
    assert payload["acceptance"]["false_positive_reduction"]["status"] == "not_evaluable"


def test_run_pilot_fails_when_whole_modelspace_rate_is_too_high(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a", whole_modelspace=1)
    screenshots = []
    for index in range(3):
        path = tmp_path / f"shot-{index}.png"
        path.write_bytes(b"png")
        screenshots.append(str(path))
    manifest = tmp_path / "pilot-manifest.json"
    _write_json(
        manifest,
        {
            "runs": [
                {
                    "case_id": "case-a",
                    "output_dir": str(run_dir),
                    "expected_region_count": 6,
                    "expected_match_count": 3,
                    "review_evidence": {
                        "reviewed_region_matches": 4,
                        "correct_region_matches": 4,
                        "global_false_positive_count": 8,
                        "region_local_false_positive_count": 2,
                    },
                    "screenshots": screenshots,
                }
            ]
        },
    )

    payload = pilot.run_pilot(manifest, tmp_path / "out", collect_only=True)

    assert payload["overall_status"] == "failed"
    assert payload["acceptance"]["whole_modelspace_fallback_rate"]["status"] == "failed"


def test_run_pilot_fails_when_region_matches_are_unresolved(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a", review_required=1, unmatched_before=1)
    screenshots = []
    for index in range(3):
        path = tmp_path / f"shot-{index}.png"
        path.write_bytes(b"png")
        screenshots.append(str(path))
    manifest = tmp_path / "pilot-manifest.json"
    _write_json(
        manifest,
        {
            "runs": [
                {
                    "case_id": "case-a",
                    "output_dir": str(run_dir),
                    "expected_region_count": 6,
                    "expected_match_count": 3,
                    "review_evidence": {
                        "reviewed_region_matches": 3,
                        "correct_region_matches": 3,
                        "global_false_positive_count": 8,
                        "region_local_false_positive_count": 2,
                    },
                    "screenshots": screenshots,
                }
            ]
        },
    )

    payload = pilot.run_pilot(manifest, tmp_path / "out", collect_only=True)

    assert payload["overall_status"] == "failed"
    assert payload["acceptance"]["unresolved_region_matches"]["status"] == "failed"


def test_run_pilot_rejects_sidecar_only_without_success_manifest(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a", write_success_manifest=False)
    screenshots = []
    for index in range(3):
        path = tmp_path / f"shot-{index}.png"
        path.write_bytes(b"png")
        screenshots.append(str(path))
    manifest = tmp_path / "pilot-manifest.json"
    _write_json(
        manifest,
        {
            "runs": [
                {
                    "case_id": "case-a",
                    "output_dir": str(run_dir),
                    "expected_region_count": 6,
                    "expected_match_count": 3,
                    "review_evidence": {
                        "reviewed_region_matches": 3,
                        "correct_region_matches": 3,
                        "global_false_positive_count": 8,
                        "region_local_false_positive_count": 2,
                    },
                    "screenshots": screenshots,
                }
            ]
        },
    )

    payload = pilot.run_pilot(manifest, tmp_path / "out", collect_only=True)

    assert payload["overall_status"] == "failed"
    assert payload["acceptance"]["artifact_integrity"]["status"] == "failed"


def test_collect_only_requires_existing_run_output(tmp_path: Path) -> None:
    manifest = tmp_path / "pilot-manifest.json"
    _write_json(
        manifest,
        {"pairs": [{"case_id": "case-a", "source_a": "before.dxf", "source_b": "after.dxf"}]},
    )

    try:
        pilot.run_pilot(manifest, tmp_path / "out", collect_only=True)
    except ValueError as exc:
        assert "output_dir" in str(exc)
    else:
        raise AssertionError("expected collect-only manifest validation failure")
