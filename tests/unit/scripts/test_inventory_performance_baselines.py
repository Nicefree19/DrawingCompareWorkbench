from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import inventory_performance_baselines as inventory


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_run(
    root: Path,
    *,
    ext_a: str,
    ext_b: str,
    kind: str,
    large: bool = False,
    multi_detail: bool = False,
    complete: bool = True,
) -> Path:
    run_dir = root
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "_SUCCESS").write_text("", encoding="utf-8")
    _write_json(
        run_dir / "run_manifest.json",
        {
            "run_id": f"run-{run_dir.name}",
            "status": "completed",
            "counts": {"completed_pairs": 1, "raw_change_count": 100_000 if large else 2},
        },
    )
    _write_json(
        run_dir / "validation_summary.json",
        {
            "input": {"a": f"before.{ext_a}", "b": f"after.{ext_b}"},
            "outputs": {
                "viewer_perf_json": "viewer/viewer_perf.json",
                "selected_zone_evidence_json": "viewer/selected_zone_evidence.json",
                "perf_events_summary_json": "perf_events_summary.json",
            },
            "files": {
                "a_kind_counts": {kind: 1},
                "b_kind_counts": {kind: 1},
                "a_size_bytes": 8 * 1024 * 1024 if large else 1024,
                "b_size_bytes": 8 * 1024 * 1024 if large else 1024,
            },
            "comparison": {"completed_pairs": 1, "failed_pairs": 0, "total_changes": 3},
            "change_artifacts": {
                "zone_count": 1,
                "artifacts": [
                    {
                        "source_a": f"<redacted>/before.{ext_a}",
                        "source_b": f"<redacted>/after.{ext_b}",
                    }
                ],
            },
            "runtime_budget": {"peak_rss_mb": 1200.0 if large else 100.0},
            "timings": {"total_s": 1.2},
            "memory": {"peak_mb": 64.0},
        },
    )
    if complete:
        _write_json(run_dir / "perf_events_summary.json", {"event_count": 5})
        _write_json(run_dir / "viewer" / "viewer_perf.json", {"event_count": 2})
        _write_json(
            run_dir / "viewer" / "selected_zone_evidence.json",
            {
                "status": "passed",
                "event_count": 2,
                "actual_crop_stats": {"actual_crop_available_rate": 1.0},
            },
        )
        screenshot = run_dir / "screenshots" / "pair.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        screenshot.write_bytes(b"png")
    if multi_detail:
        _write_json(
            run_dir / "artifacts" / "region_detection_summary.json",
            {
                "region_count": 4,
                "results": [
                    {"side": "before", "status": "passed", "region_count": 2},
                    {"side": "after", "status": "passed", "region_count": 2},
                ],
            },
        )
        _write_json(
            run_dir / "artifacts" / "region_match_summary.json",
            {
                "summaries": [
                    {
                        "auto_matched_count": 2,
                        "manual_matched_count": 0,
                        "review_required_count": 0,
                    }
                ]
            },
        )
        _write_json(
            run_dir / "artifacts" / "localized_compare_summary.json",
            {"summaries": [{"total_zones": 2, "assigned_zones": 2}]},
        )
        _write_json(
            run_dir / "artifacts" / "localized_change_zones_v2.json",
            {"status": "passed", "primary_enabled": True, "zones": [{"zone_id": "R-001"}]},
        )
        _write_json(
            run_dir / "viewer" / "viewer_manifest.json",
            {"pairs": [{"pair_id": "pair-1", "render_status": "rendered"}], "rendered_pair_count": 1},
        )
    return run_dir


def _write_viewer_image(path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (2, 1), (255, 255, 255))
    image.putpixel((1, 0), (0, 0, 0))
    image.save(path)


def test_inventory_discovers_partial_cad_run_and_reports_missing_evidence(tmp_path: Path) -> None:
    _write_run(
        tmp_path / "run-cad",
        ext_a="dxf",
        ext_b="dxf",
        kind="cad",
        complete=False,
    )

    report = inventory.build_inventory([tmp_path])

    assert report["overall_status"] == "needs_more_evidence"
    cad = report["coverage"]["cad"]
    assert cad["status"] == "partial"
    assert "baseline performance metrics" not in cad["missing_required_evidence"]
    assert "viewer_perf.json/jsonl" in cad["missing_required_evidence"]
    assert "screenshots or nonblank pixel evidence" in cad["missing_required_evidence"]
    assert "perf_events_summary.json" in cad["candidate"]["p1_instrumentation_gaps"]


def test_inventory_passes_when_all_required_baselines_exist(tmp_path: Path) -> None:
    _write_run(tmp_path / "run-pdf", ext_a="pdf", ext_b="pdf", kind="pdf")
    _write_run(tmp_path / "run-cad", ext_a="dxf", ext_b="dxf", kind="cad")
    _write_run(tmp_path / "run-large", ext_a="dwg", ext_b="dwg", kind="cad", large=True)
    _write_run(
        tmp_path / "run-multi",
        ext_a="dxf",
        ext_b="dxf",
        kind="cad",
        multi_detail=True,
    )

    report = inventory.build_inventory([tmp_path])

    assert report["overall_status"] == "ready_for_p0_review"
    assert report["coverage"]["pdf"]["status"] == "passed"
    assert report["coverage"]["cad"]["status"] == "passed"
    assert report["coverage"]["large_cad"]["status"] == "passed"
    assert report["coverage"]["multi_detail"]["status"] == "passed"


def test_inventory_uses_existing_viewer_images_as_nonblank_visual_evidence(tmp_path: Path) -> None:
    run_dir = _write_run(
        tmp_path / "run-cad",
        ext_a="dxf",
        ext_b="dxf",
        kind="cad",
        complete=True,
    )
    screenshot = run_dir / "screenshots" / "pair.png"
    screenshot.unlink()
    _write_viewer_image(run_dir / "viewer" / "images" / "pair_before.png")

    report = inventory.build_inventory([tmp_path])
    candidate = report["coverage"]["cad"]["candidate"]

    assert candidate["evidence"]["nonblank_image_probe"] is True
    assert candidate["nonblank_image_probe"]["status"] == "passed"
    assert "screenshots or nonblank pixel evidence" not in candidate["missing_required_evidence"]


def test_inventory_reports_multi_detail_detection_and_render_failures(tmp_path: Path) -> None:
    run_dir = _write_run(
        tmp_path / "run-multi",
        ext_a="dxf",
        ext_b="dxf",
        kind="cad",
        multi_detail=True,
    )
    _write_json(
        run_dir / "artifacts" / "region_detection_summary.json",
        {
            "region_count": 0,
            "results": [
                {
                    "side": "before",
                    "status": "failed",
                    "region_count": 0,
                    "warnings": ["DXF read failed"],
                }
            ],
        },
    )
    _write_json(
        run_dir / "viewer" / "viewer_manifest.json",
        {"pairs": [{"pair_id": "pair-1", "render_status": "render_failed"}], "warnings": ["render failed"]},
    )

    report = inventory.build_inventory([tmp_path])
    candidate = report["coverage"]["multi_detail"]["candidate"]

    assert report["coverage"]["multi_detail"]["status"] == "partial"
    assert "detected detail regions" in candidate["missing_required_evidence"]
    assert "successful region detection" in candidate["missing_required_evidence"]
    assert "region viewer rendered background" in candidate["missing_required_evidence"]
    assert candidate["multi_detail"]["detection_failed_count"] == 1
    assert candidate["multi_detail"]["render_failed_count"] == 1


def test_inventory_rejects_failed_nonblank_pixel_probe(tmp_path: Path) -> None:
    run_dir = _write_run(
        tmp_path / "run-cad",
        ext_a="dxf",
        ext_b="dxf",
        kind="cad",
        complete=True,
    )
    (run_dir / "screenshots" / "pair.png").unlink()
    _write_json(
        run_dir / "nonblank_pixel_probe.json",
        {"status": "failed", "passed": False, "checked": 1},
    )

    report = inventory.build_inventory([tmp_path])
    candidate = report["coverage"]["cad"]["candidate"]

    assert candidate["evidence"]["nonblank_pixel_probe"] is False
    assert "screenshots or nonblank pixel evidence" in candidate["missing_required_evidence"]


def test_main_writes_json_and_markdown_outputs(tmp_path: Path) -> None:
    _write_run(tmp_path / "run-pdf", ext_a="pdf", ext_b="pdf", kind="pdf")
    json_path = tmp_path / "inventory.json"
    md_path = tmp_path / "inventory.md"

    exit_code = inventory.main(
        [
            "--root",
            str(tmp_path),
            "--output-json",
            str(json_path),
            "--output-md",
            str(md_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == inventory.SCHEMA_VERSION
    assert "Performance Baseline Inventory" in md_path.read_text(encoding="utf-8")
