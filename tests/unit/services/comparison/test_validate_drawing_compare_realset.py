"""Tests for the real-set drawing comparison validation runner."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import pytest

from scripts import validate_drawing_compare_realset as runner
from src.services.comparison.base import ChangeRecord, ChangeType, ComparisonResult
from src.services.comparison.drawing_batch import (
    BatchCompareItemResult,
    BatchCompareSummary,
    DrawingFileDescriptor,
    DrawingKind,
    FilenameIdentity,
    MatchCandidate,
    MatchStatus,
    parse_filename_identity,
    quality_gate_visible_statuses,
    write_compare_state,
)


def _args(
    a: Path,
    b: Path,
    out: Path,
    *,
    ground_truth: Path | None = None,
    review_ground_truth: Path | None = None,
    manual_matches: Path | None = None,
    write_ground_truth_template: bool = False,
    baseline: Path | None = None,
    update_baseline: bool = False,
    quality_gate: bool = False,
    skip_compare: bool = True,
    no_cache: bool = True,
    no_expand_blocks: bool = False,
    no_block_text_detection: bool = False,
    reuse_match_candidates: Path | None = None,
    dxf_cache_dir: Path | None = None,
    compare_state_dir: Path | None = None,
    reuse_compare_state: Path | None = None,
    export_cloud_marks: bool = False,
    export_before_cloud_marks: bool = False,
    change_zone_report: bool = False,
    artifact_dir: Path | None = None,
    review_state: Path | None = None,
    export_preview: bool = False,
    preview_dpi: int = 80,
    preview_max_edge_px: int = 2400,
    executive_review: bool = False,
    executive_top_drawings: int = 15,
    executive_top_zones: int = 30,
    review_dashboard: bool = False,
    top_review_issues: int = 100,
    top_issues_per_drawing: int = 20,
    fold_repetitive_layers: bool = True,
    export_viewer_package: bool = False,
    viewer_mode: str = "image-tiles",
    viewer_render_policy: str = "lazy",
    viewer_engine: str = "auto",
    viewer_cache_dir: Path | None = None,
    tile_size: int = 512,
    max_visible_overlays: int = 500,
    viewer_memory_budget_mb: int = 512,
    render_selected_on_open: bool = False,
    prefetch_neighbor_tiles: bool = True,
    tile_prefetch_radius: int = 1,
    overview_max_edge: int = 2200,
    focus_tile_max_edge: int = 1600,
    viewer_perf_log: bool = False,
    render_selected_zone_evidence: bool = False,
    selected_zone_evidence_per_pair: int = 1,
    max_viewer_pages: int = 30,
    max_zone_tiles: int = 300,
    export_marked_pdf: bool = False,
    marked_pdf_mode: str = "selected",
    export_profile: str = "internal",
    cloud_export_mode: str = "selected",
    cloud_selection_csv: Path | None = None,
    cloud_region_distance: float = 1000.0,
    max_cloud_regions_per_pair: int = 150,
    max_cloud_regions_total: int = 3000,
) -> argparse.Namespace:
    return argparse.Namespace(
        a=a,
        b=b,
        recursive=False,
        out=out,
        ground_truth=ground_truth,
        review_ground_truth=review_ground_truth,
        manual_matches=manual_matches,
        write_ground_truth_template=write_ground_truth_template,
        skip_compare=skip_compare,
        max_workers=None,
        no_cache=no_cache,
        no_expand_blocks=no_expand_blocks,
        no_block_text_detection=no_block_text_detection,
        reuse_match_candidates=reuse_match_candidates,
        dxf_cache_dir=dxf_cache_dir,
        compare_state_dir=compare_state_dir,
        reuse_compare_state=reuse_compare_state,
        export_cloud_marks=export_cloud_marks,
        export_before_cloud_marks=export_before_cloud_marks,
        change_zone_report=change_zone_report,
        artifact_dir=artifact_dir,
        review_state=review_state,
        export_preview=export_preview,
        preview_dpi=preview_dpi,
        preview_max_edge_px=preview_max_edge_px,
        executive_review=executive_review,
        executive_top_drawings=executive_top_drawings,
        executive_top_zones=executive_top_zones,
        review_dashboard=review_dashboard,
        top_review_issues=top_review_issues,
        top_issues_per_drawing=top_issues_per_drawing,
        fold_repetitive_layers=fold_repetitive_layers,
        export_viewer_package=export_viewer_package,
        viewer_mode=viewer_mode,
        viewer_render_policy=viewer_render_policy,
        viewer_engine=viewer_engine,
        viewer_cache_dir=viewer_cache_dir,
        tile_size=tile_size,
        max_visible_overlays=max_visible_overlays,
        viewer_memory_budget_mb=viewer_memory_budget_mb,
        render_selected_on_open=render_selected_on_open,
        prefetch_neighbor_tiles=prefetch_neighbor_tiles,
        tile_prefetch_radius=tile_prefetch_radius,
        overview_max_edge=overview_max_edge,
        focus_tile_max_edge=focus_tile_max_edge,
        viewer_perf_log=viewer_perf_log,
        render_selected_zone_evidence=render_selected_zone_evidence,
        selected_zone_evidence_per_pair=selected_zone_evidence_per_pair,
        max_viewer_pages=max_viewer_pages,
        max_zone_tiles=max_zone_tiles,
        export_marked_pdf=export_marked_pdf,
        marked_pdf_mode=marked_pdf_mode,
        cloud_export_mode=cloud_export_mode,
        cloud_selection_csv=cloud_selection_csv,
        cloud_region_distance=cloud_region_distance,
        max_cloud_regions_per_pair=max_cloud_regions_per_pair,
        max_cloud_regions_total=max_cloud_regions_total,
        export_profile=export_profile,
        baseline=baseline,
        update_baseline=update_baseline,
        quality_gate=quality_gate,
        min_auto_precision=0.99,
        min_recall=0.95,
        max_match_time_regression=0.30,
    )


def _write_placeholder_dxf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("invalid dxf placeholder", encoding="utf-8")


def _descriptor(name: str, root: Path) -> DrawingFileDescriptor:
    identity = parse_filename_identity(name)
    return DrawingFileDescriptor(
        path=str(root / name),
        kind=DrawingKind.CAD,
        extension=".dxf",
        relative_path=name,
        identity=FilenameIdentity(
            original_stem=identity.original_stem,
            match_key=identity.match_key,
            tokens=identity.tokens,
            revision=identity.revision,
            drawing_number=identity.drawing_number,
            sheet=identity.sheet,
        ),
    )


def _pdf_descriptor(name: str, root: Path) -> DrawingFileDescriptor:
    identity = parse_filename_identity(name)
    return DrawingFileDescriptor(
        path=str(root / name),
        kind=DrawingKind.PDF,
        extension=".pdf",
        relative_path=name,
        identity=FilenameIdentity(
            original_stem=identity.original_stem,
            match_key=identity.match_key,
            tokens=identity.tokens,
            revision=identity.revision,
            drawing_number=identity.drawing_number,
            sheet=identity.sheet,
        ),
    )


def _confirmed_candidate(root: Path) -> MatchCandidate:
    return MatchCandidate(
        source_a=_descriptor("S-301_REV0.dxf", root),
        source_b=_descriptor("S-301_REV1.dxf", root),
        score=0.91,
        status=MatchStatus.AUTO_CONFIRMED,
    )


def test_validation_runner_writes_reports_and_respects_no_cache(tmp_path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    _write_placeholder_dxf(old_dir / "S-101_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-101_REV1.dxf")

    payload = runner.run_validation(_args(old_dir, new_dir, out_dir))

    assert payload["matching"]["auto_confirmed"] == 1
    assert payload["comparison"]["compare_skipped"] is True
    assert (out_dir / "validation_summary.json").exists()
    assert (out_dir / "validation_report.html").exists()
    assert (out_dir / "match_candidates.csv").exists()
    assert (out_dir / "compare_results.csv").exists()
    assert (out_dir / "ai_policy.json").exists()
    assert not (old_dir / ".drawing_compare_cache").exists()
    assert not (new_dir / ".drawing_compare_cache").exists()

    summary = json.loads((out_dir / "validation_summary.json").read_text(encoding="utf-8"))
    assert summary["matching"]["confirmed_pairs"] == 1
    assert summary["ai_policy"]["status"] == "passed"
    assert summary["ai_policy"]["ai_required"] is False
    assert summary["ai_policy"]["fallback_without_model"]["classifier_used"] == "heuristic"
    assert summary["outputs"]["ai_policy_json"].endswith("ai_policy.json")
    with open(out_dir / "compare_results.csv", "r", encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle)) == []


def test_validation_runner_can_reuse_match_candidates_without_scan(tmp_path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    reuse_out = tmp_path / "reuse_out"
    _write_placeholder_dxf(old_dir / "S-151_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-151_REV1.dxf")

    runner.run_validation(_args(old_dir, new_dir, out_dir))

    def fail_scan(*args, **kwargs):
        raise AssertionError("scan should not run when match candidates are reused")

    monkeypatch.setattr(runner, "scan_drawing_inputs", fail_scan)
    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            reuse_out,
            reuse_match_candidates=out_dir / "match_candidates.csv",
        )
    )

    assert payload["matching"]["auto_confirmed"] == 1
    assert payload["input"]["reuse_match_candidates"].endswith("match_candidates.csv")
    assert payload["timings"]["scan_s"] < 0.1


def test_validation_runner_calculates_ground_truth_precision_recall(tmp_path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    ground_truth = tmp_path / "truth.csv"
    _write_placeholder_dxf(old_dir / "S-201_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-201_REV1.dxf")
    ground_truth.write_text(
        "a_path,b_path,expected_status\n"
        "S-201_REV0.dxf,S-201_REV1.dxf,match\n",
        encoding="utf-8",
    )

    payload = runner.run_validation(
        _args(old_dir, new_dir, out_dir, ground_truth=ground_truth)
    )

    metrics = payload["ground_truth"]
    assert metrics["rows"] == 1
    assert metrics["passed_rows"] == 1
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["row_accuracy"] == 1.0


def test_runner_reports_full_change_counts_when_details_are_truncated(tmp_path) -> None:
    candidate = _confirmed_candidate(tmp_path)
    result = ComparisonResult(
        source_a=candidate.source_a.path,
        source_b=candidate.source_b.path,
    )
    result.add_change(ChangeRecord(key="detail_1", change_type=ChangeType.DELETED))
    result.add_change(ChangeRecord(key="detail_2", change_type=ChangeType.DELETED))
    result.metadata.update(
        {
            "change_counts": {"added": 4, "deleted": 3, "modified": 2},
            "change_records_in_memory": 2,
            "large_drawing_mode": "active",
            "truncated_changes": True,
            "omitted_change_counts": {"added": 4, "deleted": 1, "modified": 2},
            "index_backend": "grid",
        }
    )
    summary = BatchCompareSummary(
        started_at=datetime.now(),
        requested_pairs=1,
        items=[
            BatchCompareItemResult(
                candidate=candidate,
                result=result,
                status="completed",
            )
        ],
    )

    payload = runner._build_summary_payload(
        args=_args(tmp_path, tmp_path, tmp_path / "out", skip_compare=False),
        output_dir=tmp_path / "out",
        descriptors_a=[candidate.source_a],
        descriptors_b=[candidate.source_b],
        candidates=[candidate],
        compare_summary=summary,
        timings={"scan_s": 0.0, "match_s": 0.0, "compare_s": 0.0, "total_s": 0.0},
        memory={"current_mb": 0.0, "peak_mb": 0.0},
        ground_truth=None,
        manual_metrics=None,
        outputs={
            "summary_json": tmp_path / "out" / "validation_summary.json",
            "html_report": tmp_path / "out" / "validation_report.html",
            "match_candidates_csv": tmp_path / "out" / "match_candidates.csv",
            "compare_results_csv": tmp_path / "out" / "compare_results.csv",
        },
    )

    assert result.total_changes == 2
    assert payload["comparison"]["total_changes"] == 9
    assert payload["stability"]["large_mode_pairs"] == 1
    assert payload["stability"]["truncated_pairs"][0]["change_records_in_memory"] == 2

    csv_path = tmp_path / "out" / "compare_results.csv"
    runner._write_compare_csv(csv_path, summary)
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["changes"] == "9"
    assert rows[0]["change_records_in_memory"] == "2"
    assert rows[0]["truncated_changes"] == "True"


def test_validation_summary_includes_first_interactive_readiness(tmp_path) -> None:
    class ViewerPackageStub:
        viewer_dir = ""

        def to_dict(self) -> dict[str, object]:
            return {"manifest_path": "viewer/manifest.json", "index_html": "viewer/index.html"}

    candidate = _confirmed_candidate(tmp_path)
    summary = BatchCompareSummary(
        started_at=datetime.now(),
        requested_pairs=1,
        items=[BatchCompareItemResult(candidate=candidate, result=None, status="skipped")],
    )

    payload = runner._build_summary_payload(
        args=_args(
            tmp_path,
            tmp_path,
            tmp_path / "out",
            skip_compare=False,
            review_dashboard=True,
            export_viewer_package=True,
            viewer_render_policy="top-issues",
        ),
        output_dir=tmp_path / "out",
        descriptors_a=[candidate.source_a],
        descriptors_b=[candidate.source_b],
        candidates=[candidate],
        compare_summary=summary,
        timings={"scan_s": 1.0, "match_s": 2.0, "compare_s": 3.0, "artifact_s": 4.0, "total_s": 12.0},
        memory={"current_mb": 0.0, "peak_mb": 0.0},
        ground_truth=None,
        manual_metrics=None,
        outputs={
            "summary_json": tmp_path / "out" / "validation_summary.json",
            "html_report": tmp_path / "out" / "validation_report.html",
            "match_candidates_csv": tmp_path / "out" / "match_candidates.csv",
            "compare_results_csv": tmp_path / "out" / "compare_results.csv",
        },
        review_dashboard_package={"review_queue": {"items": [{"zone_id": "z-1"}]}},
        viewer_package=ViewerPackageStub(),
    )

    readiness = payload["first_interactive_ready"]
    assert readiness["status"] == "passed"
    assert readiness["review_dashboard_ready_s"] == 10.0
    assert readiness["first_top_issue_ready_s"] == 10.0
    assert readiness["viewer_metadata_ready_s"] == 12.0
    assert readiness["thresholds"]["review_dashboard_ready_s"] == 600.0


def test_manual_matches_can_promote_review_candidate_to_compare(tmp_path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    manual_csv = tmp_path / "manual.csv"
    _write_placeholder_dxf(old_dir / "S-501_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-501_REV1.dxf")
    _write_placeholder_dxf(new_dir / "S-501_COPY_REV2.dxf")
    manual_csv.write_text(
        "a_path,b_path,status\n"
        "S-501_REV0.dxf,S-501_REV1.dxf,manual_confirmed\n",
        encoding="utf-8",
    )

    def fake_compare(candidate, options, is_cancelled=None):
        result = ComparisonResult(source_a=candidate.source_a.path, source_b=candidate.source_b.path)
        result.add_change(ChangeRecord(key="line_1", change_type=ChangeType.MODIFIED))
        return result

    monkeypatch.setattr("src.services.comparison.drawing_batch.compare_candidate", fake_compare)

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            manual_matches=manual_csv,
            skip_compare=False,
        )
    )

    assert payload["manual_matches"]["applied"] == 1
    assert payload["matching"]["manual_confirmed"] == 1
    assert payload["comparison"]["completed_pairs"] == 1


def test_validation_runner_records_cad_block_policy_and_passes_to_compare(tmp_path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    _write_placeholder_dxf(old_dir / "S-545_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-545_REV1.dxf")
    observed: dict[str, bool] = {}

    def fake_compare(candidate, options, is_cancelled=None):
        observed["expand_blocks"] = options.comparison_config.expand_blocks
        observed["block_text_detection"] = options.block_text_detection
        result = ComparisonResult(source_a=candidate.source_a.path, source_b=candidate.source_b.path)
        result.add_change(ChangeRecord(key="insert_1", change_type=ChangeType.MODIFIED))
        return result

    monkeypatch.setattr("src.services.comparison.drawing_batch.compare_candidate", fake_compare)

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            skip_compare=False,
            no_expand_blocks=True,
        )
    )

    assert observed == {"expand_blocks": False, "block_text_detection": True}
    assert payload["input"]["cad_policy"] == {
        "expand_blocks": False,
        "block_text_detection": True,
    }


def test_validation_runner_writes_change_zone_report(tmp_path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    _write_placeholder_dxf(old_dir / "S-551_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-551_REV1.dxf")

    def fake_compare(candidate, options, is_cancelled=None):
        result = ComparisonResult(source_a=candidate.source_a.path, source_b=candidate.source_b.path)
        result.add_change(
            ChangeRecord(
                key="line_1",
                change_type=ChangeType.ADDED,
                new_value={"start": (0, 0), "end": (100, 0)},
                metadata={"layer": "BEAM", "entity_type": "LINE", "change_type": "added"},
            )
        )
        return result

    monkeypatch.setattr("src.services.comparison.drawing_batch.compare_candidate", fake_compare)

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            skip_compare=False,
            change_zone_report=True,
        )
    )

    artifacts = payload["change_artifacts"]
    assert artifacts["zone_count"] == 1
    assert Path(artifacts["output_paths"]["change_zones_csv"]).exists()
    assert Path(artifacts["output_paths"]["review_index_html"]).exists()
    assert payload["comparison"]["completed_pairs"] == 1


def test_validation_runner_writes_executive_review_from_existing_artifacts(tmp_path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    artifact_dir = tmp_path / "artifacts"
    _write_placeholder_dxf(old_dir / "S-553_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-553_REV1.dxf")
    artifact_dir.mkdir()
    (artifact_dir / "artifact_manifest.json").write_text(
        json.dumps(
            {
                "output_dir": str(artifact_dir),
                "pair_count": 1,
                "zone_count": 1,
                "raw_change_count": 7,
                "zone_coverage_complete": True,
                "cloud_region_count": 1,
                "cloud_omitted_zone_count": 0,
                "artifacts": [
                    {
                        "pair_id": "S-553",
                        "drawing_number": "S-553",
                        "after_marked_dxf": str(artifact_dir / "cloud_marked" / "S-553_after_marked.dxf"),
                        "cloud_region_count": 1,
                    }
                ],
                "output_paths": {},
            }
        ),
        encoding="utf-8",
    )
    with open(artifact_dir / "change_zones.csv", "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "pair_id",
                "zone_id",
                "drawing_number",
                "change_type",
                "severity",
                "raw_change_count",
                "added",
                "deleted",
                "modified",
                "bbox_min_x",
                "bbox_min_y",
                "bbox_max_x",
                "bbox_max_y",
                "layers",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": "S-553",
                "zone_id": "C-001",
                "drawing_number": "S-553",
                "change_type": "added",
                "severity": "high",
                "raw_change_count": 7,
                "added": 7,
                "deleted": 0,
                "modified": 0,
                "bbox_min_x": 0,
                "bbox_min_y": 0,
                "bbox_max_x": 100,
                "bbox_max_y": 100,
                "layers": "AA-DETL-PCN8",
            }
        )

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            artifact_dir=artifact_dir,
            executive_review=True,
        )
    )

    assert payload["comparison"]["compare_skipped"] is True
    assert payload["executive_review"]["raw_change_count"] == 7
    assert Path(payload["outputs"]["executive_review_html"]).exists()
    assert Path(payload["outputs"]["drawing_change_brief_md"]).exists()
    assert Path(payload["outputs"]["drawing_change_brief_csv"]).exists()
    updated_manifest = json.loads((artifact_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert updated_manifest["output_paths"]["executive_review_html"].endswith("executive_review.html")


def test_validation_runner_reuses_compare_state_without_compare(tmp_path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    state_dir = tmp_path / "state"
    old_path = old_dir / "S-552_REV0.dxf"
    new_path = new_dir / "S-552_REV1.dxf"
    _write_placeholder_dxf(old_path)
    _write_placeholder_dxf(new_path)
    stream_path = state_dir / "streams" / "S-552.jsonl"
    stream_path.parent.mkdir(parents=True)
    stream_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pair_id": "S-552",
                "key": "line_1",
                "change_type": "added",
                "layer": "BEAM",
                "entity_type": "LINE",
                "bbox": [0, 0, 100, 50],
                "old_bbox": None,
                "location": [50, 25],
                "old_location": None,
                "change_category": None,
                "change_detail": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source_a = DrawingFileDescriptor(
        path=str(old_path),
        kind=DrawingKind.CAD,
        extension=".dxf",
        identity=parse_filename_identity(old_path),
    )
    source_b = DrawingFileDescriptor(
        path=str(new_path),
        kind=DrawingKind.CAD,
        extension=".dxf",
        identity=parse_filename_identity(new_path),
    )
    candidate = MatchCandidate(
        source_a=source_a,
        source_b=source_b,
        score=0.99,
        status=MatchStatus.AUTO_CONFIRMED,
    )
    result = ComparisonResult(source_a=str(old_path), source_b=str(new_path))
    result.metadata.update(
        {
            "change_counts": {"added": 1, "deleted": 0, "modified": 0},
            "change_zone_stream_path": str(stream_path),
            "change_zone_record_count": 1,
            "change_zone_stream_complete": True,
            "change_zone_stream_schema_version": 1,
        }
    )
    summary = BatchCompareSummary(
        started_at=datetime.now(),
        finished_at=datetime.now(),
        requested_pairs=1,
        items=[
            BatchCompareItemResult(
                candidate=candidate,
                result=result,
                status="completed",
            )
        ],
    )
    write_compare_state(summary, state_dir)

    def fail_compare(*_args, **_kwargs):
        raise AssertionError("compare should not run when compare state is reused")

    monkeypatch.setattr("src.services.comparison.drawing_batch.compare_candidate", fail_compare)

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            skip_compare=True,
            reuse_compare_state=state_dir,
            change_zone_report=True,
        )
    )

    assert payload["timings"]["compare_s"] == 0.0
    assert payload["change_artifacts"]["zone_count"] == 1
    assert payload["change_artifacts"]["zone_input_count"] == 1
    assert Path(payload["change_artifacts"]["output_paths"]["change_zones_csv"]).exists()


def test_validation_runner_exports_preview_from_compare_state(tmp_path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    state_dir = tmp_path / "state"
    old_path = old_dir / "S-701_REV0.dxf"
    new_path = new_dir / "S-701_REV1.dxf"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("0\nEOF\n", encoding="utf-8")
    new_path.write_text("0\nEOF\n", encoding="utf-8")
    stream_path = state_dir / "streams" / "S701.jsonl"
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        '{"schema_version":1,"pair_id":"S701","key":"k1","change_type":"added",'
        '"layer":"BEAM","entity_type":"LINE","bbox":[0,0,10,10],"old_bbox":null,'
        '"location":[5,5],"old_location":null}\n',
        encoding="utf-8",
    )
    candidate = MatchCandidate(
        source_a=DrawingFileDescriptor(
            path=str(old_path),
            kind=DrawingKind.CAD,
            extension=".dxf",
            identity=parse_filename_identity(old_path),
        ),
        source_b=DrawingFileDescriptor(
            path=str(new_path),
            kind=DrawingKind.CAD,
            extension=".dxf",
            identity=parse_filename_identity(new_path),
        ),
        score=0.99,
        status=MatchStatus.AUTO_CONFIRMED,
    )
    result = ComparisonResult(source_a=str(old_path), source_b=str(new_path))
    result.metadata.update(
        {
            "change_counts": {"added": 1, "deleted": 0, "modified": 0},
            "change_zone_stream_path": str(stream_path),
            "change_zone_record_count": 1,
            "change_zone_stream_complete": True,
            "change_zone_stream_schema_version": 1,
        }
    )
    write_compare_state(
        BatchCompareSummary(
            started_at=datetime.now(),
            finished_at=datetime.now(),
            requested_pairs=1,
            items=[BatchCompareItemResult(candidate=candidate, result=result, status="completed")],
        ),
        state_dir,
    )

    def fake_render(_dxf_path, output_path, *, dpi, max_edge_px):
        output_path.write_bytes(b"fake png")
        return {
            "min_x": 0,
            "min_y": 0,
            "img_width": 100,
            "img_height": 100,
            "scale_x": 1,
            "scale_y": 1,
        }

    monkeypatch.setattr("src.services.comparison.review_project._render_dxf_to_png", fake_render)

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            skip_compare=True,
            reuse_compare_state=state_dir,
            export_preview=True,
        )
    )

    assert payload["timings"]["compare_s"] == 0.0
    assert payload["preview_artifacts"]["preview_count"] == 1
    assert payload["preview_artifacts"]["zone_overlay_count"] == 1
    assert Path(payload["outputs"]["preview_manifest_json"]).exists()
    assert Path(payload["outputs"]["review_state_json"]).exists()


def test_manual_rejected_match_is_excluded_from_compare(tmp_path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    manual_csv = tmp_path / "manual.csv"
    _write_placeholder_dxf(old_dir / "S-601_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-601_REV1.dxf")
    manual_csv.write_text(
        "a_path,b_path,status\n"
        "S-601_REV0.dxf,S-601_REV1.dxf,rejected\n",
        encoding="utf-8",
    )
    called = {"value": False}

    def fake_compare(candidate, options, is_cancelled=None):
        called["value"] = True
        return ComparisonResult(source_a=candidate.source_a.path, source_b=candidate.source_b.path)

    monkeypatch.setattr("src.services.comparison.drawing_batch.compare_candidate", fake_compare)

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            manual_matches=manual_csv,
            skip_compare=False,
        )
    )

    assert payload["matching"]["rejected"] == 1
    assert payload["comparison"]["requested_pairs"] == 0
    assert called["value"] is False


def test_ground_truth_template_is_written(tmp_path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    _write_placeholder_dxf(old_dir / "S-701_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-701_REV1.dxf")

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            write_ground_truth_template=True,
        )
    )

    template_path = Path(payload["outputs"]["ground_truth_template_csv"])
    assert template_path.exists()
    with open(template_path, "r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["expected_status"] == "match"


def test_quality_gate_without_ground_truth_allows_unknown_precision(tmp_path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    _write_placeholder_dxf(old_dir / "S-801_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-801_REV1.dxf")

    payload = runner.run_validation(
        _args(old_dir, new_dir, out_dir, quality_gate=True)
    )

    assert payload["quality_gate"]["status"] == "passed"
    assert "auto_precision" in payload["quality_gate"]["unknown_metrics"]
    assert (out_dir / "quality_gate.json").exists()


def test_quality_gate_fails_on_match_time_regression(tmp_path) -> None:
    payload = {
        "quality": {"auto_precision": 1.0},
        "ground_truth": {"recall": 1.0},
        "matching": {"duplicate_b_assignments": 0},
        "comparison": {"failed_pairs": 0},
        "timings": {"match_s": 2.0},
    }
    previous_baseline = {"performance": {"match_s": 1.0}}

    quality_gate = runner._evaluate_quality_gate(
        payload,
        previous_baseline,
        _args(tmp_path, tmp_path, tmp_path / "out", quality_gate=True),
    )

    assert quality_gate["status"] == "failed"
    assert any(
        issue["metric"] == "match_time_regression"
        for issue in quality_gate["issues"]
    )


def test_update_baseline_writes_baseline_record(tmp_path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    baseline = tmp_path / "baseline.json"
    _write_placeholder_dxf(old_dir / "S-1001_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-1001_REV1.dxf")

    payload = runner.run_validation(
        _args(old_dir, new_dir, out_dir, baseline=baseline, update_baseline=True)
    )

    assert baseline.exists()
    written = json.loads(baseline.read_text(encoding="utf-8"))
    assert written["schema_version"] == runner.QUALITY_SCHEMA_VERSION
    assert written["matching"]["auto_confirmed"] == payload["matching"]["auto_confirmed"]


def test_manifest_runs_multiple_sets_and_writes_index(tmp_path) -> None:
    first_old = tmp_path / "first_old"
    first_new = tmp_path / "first_new"
    second_old = tmp_path / "second_old"
    second_new = tmp_path / "second_new"
    _write_placeholder_dxf(first_old / "S-1101_REV0.dxf")
    _write_placeholder_dxf(first_new / "S-1101_REV1.dxf")
    _write_placeholder_dxf(second_old / "S-1201_REV0.dxf")
    _write_placeholder_dxf(second_new / "S-1201_REV1.dxf")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "datasets": [
                    {
                        "name": "first",
                        "a": str(first_old),
                        "b": str(first_new),
                        "skip_compare": True,
                    },
                    {
                        "name": "second",
                        "a": str(second_old),
                        "b": str(second_new),
                        "skip_compare": True,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        manifest=manifest,
        a=None,
        b=None,
        recursive=False,
        out=tmp_path / "out",
        ground_truth=None,
        manual_matches=None,
        write_ground_truth_template=False,
        skip_compare=True,
        max_workers=None,
        no_cache=True,
        reuse_match_candidates=None,
        dxf_cache_dir=None,
        baseline=None,
        update_baseline=False,
        quality_gate=True,
        min_auto_precision=0.99,
        min_recall=0.95,
        max_match_time_regression=0.30,
    )

    payload = runner.run_manifest_validation(args)

    assert payload["dataset_count"] == 2
    assert Path(payload["outputs"]["index_html"]).exists()
    assert Path(payload["outputs"]["quality_gate_json"]).exists()
    assert payload["quality_gate"]["status"] == "passed"


def test_operational_csv_package_is_written(tmp_path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    _write_placeholder_dxf(old_dir / "S-1301_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-1301_REV1.dxf")
    _write_placeholder_dxf(new_dir / "S-1301_COPY_REV2.dxf")

    payload = runner.run_validation(_args(old_dir, new_dir, out_dir))

    for key in (
        "review_queue_csv",
        "unmatched_csv",
        "blocked_pairs_csv",
        "manual_matches_template_csv",
        "ground_truth_template_csv",
    ):
        assert Path(payload["outputs"][key]).exists()
    with open(payload["outputs"]["review_queue_csv"], "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert "a_drawing_number" in (reader.fieldnames or [])
        assert "alternate_1_drawing_number" in (reader.fieldnames or [])


def test_validation_runner_sharable_profile_redacts_csv_html_and_summary_paths(tmp_path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    _write_placeholder_dxf(old_dir / "S-1401_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-1401_REV1.dxf")

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            export_profile="sharable",
        )
    )

    assert payload["sharable_audit"]["leak_count"] == 0
    assert runner.audit_sharable_paths(out_dir) == []
    match_csv = (out_dir / "match_candidates.csv").read_text(encoding="utf-8-sig")
    summary_json = (out_dir / "validation_summary.json").read_text(encoding="utf-8")
    html_report = (out_dir / "validation_report.html").read_text(encoding="utf-8")
    assert "<redacted>/S-1401_REV0.dxf" in match_csv
    assert str(tmp_path) not in match_csv
    assert str(tmp_path) not in summary_json
    assert str(tmp_path) not in html_report


def test_validation_runner_sharable_profile_removes_raw_jsonl_streams(tmp_path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    stream_path = out_dir / "compare_state" / "streams" / "pair_raw.jsonl"
    _write_placeholder_dxf(old_dir / "S-1401_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-1401_REV1.dxf")
    stream_path.parent.mkdir(parents=True)
    stream_path.write_text('{"raw": true}\n', encoding="utf-8")

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            export_profile="sharable",
        )
    )

    assert payload["sharable_raw_streams"]["removed_count"] == 1
    assert payload["sharable_raw_streams"]["removed"] == ["compare_state/streams/pair_raw.jsonl"]
    assert not stream_path.exists()
    assert list(out_dir.rglob("*.jsonl")) == []


def test_compare_metrics_does_not_require_cad_stream_for_pdf_pairs(tmp_path) -> None:
    candidate = MatchCandidate(
        source_a=_pdf_descriptor("S-2401_REV0.pdf", tmp_path),
        source_b=_pdf_descriptor("S-2401_REV1.pdf", tmp_path),
        score=0.99,
        status=MatchStatus.AUTO_CONFIRMED,
    )
    result = ComparisonResult(source_a=str(tmp_path / "S-2401_REV0.pdf"), source_b=str(tmp_path / "S-2401_REV1.pdf"))
    result.metadata.update(
        {
            "comparison_type": "PDF",
            "change_counts": {"added": 0, "deleted": 0, "modified": 2},
        }
    )
    summary = BatchCompareSummary(
        started_at=datetime.now(),
        finished_at=datetime.now(),
        requested_pairs=1,
        items=[BatchCompareItemResult(candidate=candidate, result=result, status="completed")],
    )

    metrics = runner._compare_metrics(summary)["summary"]

    assert metrics["change_zone_stream_records"] == 0
    assert metrics["change_zone_stream_mismatch_pairs"] == 0


def test_pdf_pdf_validation_runner_builds_review_queue_and_sharable_viewer_package(tmp_path) -> None:
    fitz = pytest.importorskip("fitz")
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    review_truth = tmp_path / "review_truth.csv"

    def write_pdf(path: Path, spacing: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), "DRAWING NO S-2401 STRUCTURAL PLAN 2F", fontsize=12)
        page.insert_text((72, 120), f"SLAB REBAR D13@{spacing}", fontsize=14)
        page.insert_text((72, 160), "GRID A-B / 1-5", fontsize=12)
        page.draw_rect(fitz.Rect(70, 100, 310, 180), color=(0, 0, 0), width=1)
        doc.save(path)
        doc.close()

    write_pdf(old_dir / "S-2401_REV0.pdf", "100")
    write_pdf(new_dir / "S-2401_REV1.pdf", "200")
    review_truth.write_text(
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status\n"
        "S2401,mixed,D13@100;D13@200,pdf,pdf_text|pdf_visual|pdf_ocr|hybrid,exact\n",
        encoding="utf-8",
    )

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            skip_compare=False,
            change_zone_report=True,
            executive_review=True,
            review_dashboard=True,
            export_viewer_package=True,
            viewer_render_policy="top-issues",
            viewer_perf_log=True,
            render_selected_zone_evidence=True,
            export_marked_pdf=True,
            marked_pdf_mode="selected",
            preview_dpi=72,
            export_profile="sharable",
            review_ground_truth=review_truth,
            quality_gate=True,
        )
    )

    assert payload["quality_gate"]["status"] == "passed"
    assert payload["selected_zone_evidence"]["status"] == "passed"
    assert payload["selected_zone_evidence"]["event_count"] >= 2
    # Plan §15 Phase A-1 (HIGH-1 wire) — actual_crop_stats must land in
    # validation_summary.json so audits / dashboards can read the metric
    # without invoking the exit-gate script. The dict shape is the
    # ZoneOutcomeStats.to_dict() schema (see zone_render_outcome.py:105).
    assert "actual_crop_stats" in payload["selected_zone_evidence"]
    assert payload["selected_zone_evidence"]["actual_crop_stats"]["total"] >= 2
    assert (
        payload["selected_zone_evidence"]["actual_crop_stats"][
            "actual_crop_available_rate"
        ]
        is not None
    )
    assert payload["viewer_perf_summary"]["zone_crop_count"] >= 2
    assert payload["viewer_perf_summary"]["zone_crop_cold_ms"]["p95"] > 0
    assert payload["viewer_perf_summary"]["zone_crop_cache_hit_ms"]["p95"] >= 0
    assert payload["review_ground_truth"]["recall"] == 1.0
    assert payload["quality"]["structural_review_recall"] == 1.0
    assert payload["comparison"]["completed_pairs"] == 1
    assert payload["comparison"]["change_zone_stream_mismatch_pairs"] == 0
    assert payload["change_artifacts"]["zone_coverage_complete"] is True
    assert payload["sharable_audit"]["leak_count"] == 0

    dashboard = json.loads((out_dir / "change_artifacts" / "review_dashboard.json").read_text(encoding="utf-8"))
    queue = dashboard["review_queue"]
    item = queue["items"][0]
    assert queue["mode"] == "structural_core"
    assert item["source_format"] == "pdf"
    assert item["detection_source"] in {"pdf_text", "pdf_visual", "pdf_ocr"}
    assert item["bbox_status"] == "exact"
    assert "D13@100" in item["change_summary_ko"]
    assert "D13@200" in item["change_summary_ko"]
    assert item["review_status"] == "needs_review"

    viewer_manifest = json.loads((out_dir / "viewer" / "viewer_manifest.json").read_text(encoding="utf-8"))
    pair = viewer_manifest["pairs"][0]
    assert viewer_manifest["marked_pdf_count"] == 1
    assert pair["visual_fidelity"] == "pdf_render"
    assert pair["render_lifecycle"] == "ready"
    assert pair["marked_pdf_status"] == "created_raster_review_pdf"
    assert Path(out_dir / pair["marked_pdf"]).exists()
    overlay = json.loads((out_dir / pair["overlay_json"]).read_text(encoding="utf-8"))["overlays"][0]
    assert overlay["bbox_coordinate_space"] == "image_pixels"
    assert overlay["after_bbox_px"]


def test_pdf_pdf_review_ground_truth_covers_section_dimension_and_grid_changes(tmp_path) -> None:
    fitz = pytest.importorskip("fitz")
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    review_truth = tmp_path / "review_truth.csv"

    def write_pdf(path: Path, title: str, line1: str, line2: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), title, fontsize=12)
        page.insert_text((72, 120), line1, fontsize=14)
        page.insert_text((72, 160), line2, fontsize=14)
        page.draw_rect(fitz.Rect(70, 100, 430, 180), color=(0, 0, 0), width=1)
        doc.save(path)
        doc.close()

    write_pdf(
        old_dir / "S-2501_REV0.pdf",
        "DRAWING NO S-2501 STRUCTURAL PLAN",
        "COLUMN SECTION DIMENSION 400x400",
        "GRID A-B / 1-5",
    )
    write_pdf(
        new_dir / "S-2501_REV1.pdf",
        "DRAWING NO S-2501 STRUCTURAL PLAN",
        "COLUMN SECTION DIMENSION 500x500",
        "GRID A-B / 1-5",
    )
    write_pdf(
        old_dir / "S-2502_REV0.pdf",
        "DRAWING NO S-2502 STRUCTURAL GRID PLAN",
        "COLUMN SECTION DIMENSION 400x400",
        "GRID A-B / 1-5",
    )
    write_pdf(
        new_dir / "S-2502_REV1.pdf",
        "DRAWING NO S-2502 STRUCTURAL GRID PLAN",
        "COLUMN SECTION DIMENSION 400x400",
        "GRID A-C / 1-6",
    )
    review_truth.write_text(
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status\n"
        "S2501,mixed,COLUMN SECTION DIMENSION;400x400;500x500,pdf,pdf_text|pdf_visual|pdf_ocr|hybrid,exact\n"
        "S2502,grid,GRID A-B;GRID A-C,pdf,pdf_text|pdf_visual|pdf_ocr|hybrid,exact\n",
        encoding="utf-8",
    )

    payload = runner.run_validation(
        _args(
            old_dir,
            new_dir,
            out_dir,
            skip_compare=False,
            change_zone_report=True,
            review_dashboard=True,
            export_profile="sharable",
            review_ground_truth=review_truth,
            quality_gate=True,
        )
    )

    assert payload["quality_gate"]["status"] == "passed"
    assert payload["review_ground_truth"]["recall"] == 1.0
    assert payload["quality"]["structural_review_recall"] == 1.0
    dashboard = json.loads((out_dir / "change_artifacts" / "review_dashboard.json").read_text(encoding="utf-8"))
    summaries = {item["drawing_label"]: item for item in dashboard["review_queue"]["items"]}
    assert "COLUMN SECTION DIMENSION 400x400" in summaries["S2501"]["change_summary_ko"]
    assert "COLUMN SECTION DIMENSION 500x500" in summaries["S2501"]["change_summary_ko"]
    assert summaries["S2502"]["category"] == "grid"
    assert "GRID A-B" in summaries["S2502"]["change_summary_ko"]
    assert "GRID A-C" in summaries["S2502"]["change_summary_ko"]


def test_review_ground_truth_recall_fails_quality_gate_when_missing() -> None:
    payload = {
        "quality": {"auto_precision": 1.0, "structural_review_recall": 0.0},
        "ground_truth": {"recall": 1.0},
        "matching": {},
        "comparison": {},
    }

    gate = runner._evaluate_quality_gate(
        payload,
        None,
        _args(Path("old"), Path("new"), Path("out"), quality_gate=True),
    )

    assert gate["status"] == "failed"
    assert any(issue["metric"] == "structural_review_recall" for issue in gate["issues"])


def test_validation_report_handles_candidates_without_drawing_number(tmp_path) -> None:
    candidate = MatchCandidate(
        source_a=_descriptor("before.dxf", tmp_path),
        source_b=_descriptor("after.dxf", tmp_path),
        score=0.5,
        status=MatchStatus.REVIEW_REQUIRED,
    )

    table = runner._candidate_table([candidate], {MatchStatus.REVIEW_REQUIRED})

    assert "review_required" in table
    assert "None" not in table


def test_quality_gate_fails_duplicate_b_assignments(tmp_path) -> None:
    candidate = _confirmed_candidate(tmp_path)
    duplicate = MatchCandidate(
        source_a=_descriptor("S-302_REV0.dxf", tmp_path),
        source_b=candidate.source_b,
        score=0.9,
        status=MatchStatus.MANUAL_CONFIRMED,
    )

    payload = runner._build_summary_payload(
        args=_args(tmp_path, tmp_path, tmp_path / "out", quality_gate=True),
        output_dir=tmp_path / "out",
        descriptors_a=[candidate.source_a, duplicate.source_a],
        descriptors_b=[candidate.source_b],
        candidates=[candidate, duplicate],
        compare_summary=None,
        timings={"scan_s": 0.0, "match_s": 0.0, "compare_s": 0.0, "total_s": 0.0},
        memory={"current_mb": 0.0, "peak_mb": 0.0},
        ground_truth=None,
        manual_metrics=None,
        outputs={
            "summary_json": tmp_path / "out" / "validation_summary.json",
            "html_report": tmp_path / "out" / "validation_report.html",
            "match_candidates_csv": tmp_path / "out" / "match_candidates.csv",
            "compare_results_csv": tmp_path / "out" / "compare_results.csv",
            "quality_gate_json": tmp_path / "out" / "quality_gate.json",
        },
    )
    gate = runner._evaluate_quality_gate(payload, None, _args(tmp_path, tmp_path, tmp_path / "out", quality_gate=True))

    assert gate["status"] == "failed"
    assert any(issue["metric"] == "duplicate_b_assignments" for issue in gate["issues"])


def test_quality_gate_fails_when_change_zone_stream_counts_mismatch(tmp_path) -> None:
    payload = {
        "quality": {"auto_precision": 1.0},
        "ground_truth": {"recall": 1.0},
        "matching": {"duplicate_b_assignments": 0},
        "comparison": {"failed_pairs": 0, "change_zone_stream_mismatch_pairs": 1},
        "timings": {"match_s": 0.0},
    }

    gate = runner._evaluate_quality_gate(
        payload,
        None,
        _args(tmp_path, tmp_path, tmp_path / "out", quality_gate=True),
    )

    assert gate["status"] == "failed"
    assert any(
        issue["metric"] == "change_zone_stream_mismatch_pairs"
        for issue in gate["issues"]
    )


def test_quality_gate_fails_when_artifact_zone_input_is_incomplete(tmp_path) -> None:
    payload = {
        "quality": {"auto_precision": 1.0},
        "ground_truth": {"recall": 1.0},
        "matching": {"duplicate_b_assignments": 0},
        "comparison": {"failed_pairs": 0, "change_zone_stream_mismatch_pairs": 0},
        "change_artifacts": {
            "raw_change_count": 5,
            "zone_input_count": 2,
            "zone_coverage_complete": False,
        },
        "timings": {"match_s": 0.0},
    }

    gate = runner._evaluate_quality_gate(
        payload,
        None,
        _args(tmp_path, tmp_path, tmp_path / "out", quality_gate=True),
    )

    assert gate["status"] == "failed"
    metrics = {issue["metric"] for issue in gate["issues"]}
    assert {"zone_coverage_complete", "zone_input_count"} <= metrics


def test_workbench_quality_gate_filter_helper_maps_failed_statuses() -> None:
    statuses = quality_gate_visible_statuses(
        {
            "status": "failed",
            "issues": [{"metric": "duplicate_b_assignments"}],
        },
    )

    assert statuses == {MatchStatus.AUTO_CONFIRMED, MatchStatus.MANUAL_CONFIRMED}


def test_cli_main_returns_success_and_outputs_json(tmp_path, capsys) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    out_dir = tmp_path / "out"
    _write_placeholder_dxf(old_dir / "S-401_REV0.dxf")
    _write_placeholder_dxf(new_dir / "S-401_REV1.dxf")

    exit_code = runner.main(
        [
            "--a",
            str(old_dir),
            "--b",
            str(new_dir),
            "--out",
            str(out_dir),
            "--skip-compare",
            "--no-cache",
        ]
    )

    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "passed"
    assert Path(printed["summary_json"]).exists()
