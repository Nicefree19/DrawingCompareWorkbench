# -*- coding: utf-8 -*-
"""Tests for the simplified Korean folder compare pipeline."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from PIL import Image

from src.services.comparison import folder_compare_pipeline as pipeline
from src.services.comparison.drawing_batch import (
    BatchCompareItemResult,
    BatchCompareSummary,
    DrawingFileDescriptor,
    DrawingKind,
    MatchCandidate,
    MatchStatus,
    parse_filename_identity,
)


def _descriptor(path: Path, kind: DrawingKind = DrawingKind.CAD) -> DrawingFileDescriptor:
    return DrawingFileDescriptor(
        path=str(path),
        kind=kind,
        extension=path.suffix,
        identity=parse_filename_identity(path),
    )


def test_folder_compare_pipeline_runs_all_outputs_without_input_cache(tmp_path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old = old_dir / "S-100_REV0.dxf"
    new = new_dir / "S-100_REV1.dxf"
    old.write_text("0\nEOF\n", encoding="utf-8")
    new.write_text("0\nEOF\n", encoding="utf-8")
    desc_a = _descriptor(old)
    desc_b = _descriptor(new)
    candidate = MatchCandidate(desc_a, desc_b, score=0.95, status=MatchStatus.AUTO_CONFIRMED)

    captured = {}

    def fake_scan(source, options):
        captured.setdefault("cache_flags", []).append(options.enable_cache)
        captured.setdefault("scan_dxf_cache_dirs", []).append(options.dxf_cache_dir)
        return [desc_a] if Path(source) == old_dir else [desc_b]

    def fake_match(a, b, options):
        return [candidate]

    class FakeJob:
        def __init__(self, candidates, options):
            captured["dxf_cache_dir"] = options.dxf_cache_dir
            captured["compare_state_dir"] = options.compare_state_dir
            self.candidates = candidates
            self.options = options

        def run(self, progress_callback=None, is_cancelled=None):
            summary = BatchCompareSummary(started_at=datetime.now(), requested_pairs=1)
            summary.items.append(BatchCompareItemResult(candidate=candidate, status="completed"))
            summary.finished_at = datetime.now()
            state_path = Path(self.options.compare_state_dir) / "compare_state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(
                    {
                        "summary": {
                            "items": [
                                {
                                    "candidate": {
                                        "source_a": {"path": str(old.resolve())},
                                        "source_b": {"path": str(new.resolve())},
                                    }
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            return summary

    def fake_artifacts(summary, output_dir, **kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            output_paths={"artifact_manifest_json": str(Path(output_dir) / "artifact_manifest.json")},
            raw_change_count=10,
            zone_count=3,
            cloud_region_count=2,
            cloud_omitted_zone_count=1,
            to_dict=lambda: {"raw_change_count": 10},
        )

    def fake_preview(summary, output_dir, **kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        manifest = Path(output_dir) / "preview_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return SimpleNamespace(
            manifest_path=str(manifest),
            artifacts=[],
            preview_count=0,
            to_dict=lambda: {"preview_count": 0},
        )

    def fake_executive(output_dir, **kwargs):
        html = Path(output_dir) / "executive_review.html"
        html.write_text("<html></html>", encoding="utf-8")
        return SimpleNamespace(
            output_paths={
                "executive_review_html": str(html),
                "drawing_change_brief_csv": str(Path(output_dir) / "drawing_change_brief.csv"),
                "review_dashboard_json": str(Path(output_dir) / "review_dashboard.json"),
            },
            to_dict=lambda: {"output_paths": {"executive_review_html": str(html)}},
        )

    def fake_viewer(artifact_dir, output_dir, **kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        manifest = Path(output_dir) / "viewer_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        image_dir = Path(output_dir) / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        render_result = image_dir / "pair.render_result.json"
        render_result.write_text(
            json.dumps(
                {
                    "before_image": str((tmp_path / "cache" / "before.png").resolve()),
                    "after_image": str((tmp_path / "cache" / "after.png").resolve()),
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(
            output_paths={"viewer_manifest_json": str(manifest)},
            pair_count=1,
            overlay_count=0,
            to_dict=lambda: {"viewer_manifest_json": str(manifest)},
        )

    monkeypatch.setattr(pipeline, "scan_drawing_inputs", fake_scan)
    monkeypatch.setattr(pipeline, "match_drawing_sets", fake_match)
    monkeypatch.setattr(pipeline, "BatchCompareJob", FakeJob)
    monkeypatch.setattr(pipeline, "export_change_artifacts", fake_artifacts)
    monkeypatch.setattr(pipeline, "export_preview_artifacts", fake_preview)
    monkeypatch.setattr(pipeline, "export_executive_review_from_artifacts", fake_executive)
    monkeypatch.setattr(pipeline, "export_viewer_package", fake_viewer)

    # Audit-gates §11.4 — pipeline now invokes the subprocess proxy; stub it
    # to wrap fake_viewer so this in-tree test does not need real fixtures.
    def _fake_viewer_isolated(
        artifact_dir,
        *,
        options=None,
        memory_cap_mb=None,
        timeout_s=None,
        progress_callback=None,
        python_executable=None,
        allow_inprocess_fallback=False,
        fault_log_dir=None,  # §13.4 Phase B-2 — child fault log dir passthrough
    ):
        opts = dict(options or {})
        viewer_dir_arg = opts.pop("viewer_dir", None)
        result_obj = fake_viewer(artifact_dir, viewer_dir_arg, **opts)
        return (
            {
                "output_paths": dict(result_obj.output_paths),
                "pair_count": result_obj.pair_count,
                "overlay_count": result_obj.overlay_count,
            },
            pipeline.SubprocessRunReport(exit_code=0, elapsed_s=0.0),
        )

    monkeypatch.setattr(pipeline, "export_viewer_package_isolated", _fake_viewer_isolated)

    request = pipeline.FolderCompareRunRequest(old_dir, new_dir, tmp_path / "out")
    result = pipeline.FolderComparePipeline(request).run()

    assert result.confirmed_pairs == 1
    assert result.artifact_dir.endswith("artifacts")
    assert captured["cache_flags"] == [True, True]
    assert all(path for path in captured["scan_dxf_cache_dirs"])
    assert not (old_dir / ".drawing_compare_cache").exists()
    assert not (new_dir / ".drawing_compare_cache").exists()
    assert Path(result.executive_package.output_paths["executive_review_html"]).exists()
    assert Path(result.viewer_package.output_paths["viewer_manifest_json"]).exists()
    assert Path(result.run_manifest_path).exists()
    assert Path(result.success_sentinel_path).exists()
    assert Path(result.preflight_report_path).exists()
    run_manifest = json.loads(Path(result.run_manifest_path).read_text(encoding="utf-8"))
    assert run_manifest["status"] == "completed"
    assert run_manifest["stages"]["artifact"]["status"] == "completed"
    assert all(
        stage.get("status") != "running"
        for stage in run_manifest["stages"].values()
        if isinstance(stage, dict)
    )
    assert request.export_profile == "sharable"
    compare_state_text = (Path(result.compare_state_dir) / "compare_state.json").read_text(encoding="utf-8")
    assert str(old.resolve()) not in compare_state_text
    render_result_text = (Path(result.output_dir) / "viewer" / "images" / "pair.render_result.json").read_text(
        encoding="utf-8"
    )
    assert str((tmp_path / "cache").resolve()) not in render_result_text
    assert pipeline.audit_sharable_paths(Path(result.output_dir)) == []


def test_auto_structural_cloud_export_uses_review_queue_and_separate_folder(tmp_path) -> None:
    viewer_dir = tmp_path / "viewer"
    overlay_dir = viewer_dir / "overlays"
    image_dir = viewer_dir / "images"
    overlay_dir.mkdir(parents=True)
    image_dir.mkdir(parents=True)
    after_png = image_dir / "pair_after.png"
    Image.new("RGB", (500, 500), color=(255, 255, 255)).save(after_png)
    overlay_json = overlay_dir / "pair.json"
    overlay_json.write_text(
        json.dumps(
            {
                "overlays": [
                    {"zone_id": "z_rebar", "after_bbox_px": {"x": 40, "y": 60, "width": 100, "height": 80}},
                    {"zone_id": "z_other", "after_bbox_px": {"x": 240, "y": 260, "width": 80, "height": 70}},
                ]
            }
        ),
        encoding="utf-8",
    )
    viewer_manifest = viewer_dir / "viewer_manifest.json"
    viewer_manifest.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "pair_test",
                        "source_b": "after.pdf",
                        "coordinate_source": "image_pixels",
                        "after_image": str(after_png),
                        "after_transform": {"dpi": 200},
                        "overlay_json": str(overlay_json),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    dashboard = tmp_path / "review_dashboard.json"
    dashboard.write_text(
        json.dumps(
            {
                "review_queue": {
                    "items": [
                        {"pair_id": "pair_test", "zone_id": "z_rebar", "category": "rebar"},
                        {"pair_id": "pair_test", "zone_id": "z_other", "category": "other"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    outputs = pipeline._export_auto_structural_clouds(
        review_dashboard_path=dashboard,
        viewer_manifest_path=viewer_manifest,
        output_dir=tmp_path / "artifacts" / "auto_structural_clouds",
    )

    assert outputs["auto_structural_cloud_count"] == 1
    manifest = json.loads(Path(outputs["auto_structural_cloud_manifest_json"]).read_text(encoding="utf-8"))
    assert manifest["results"][0]["selected_zone_ids"] == ["z_rebar"]
    assert Path(manifest["results"][0]["output_path"]).name == "pair_test_auto_structural.png"


def test_file_pair_selection_confirms_pdf_pair_without_filename_match(tmp_path, monkeypatch) -> None:
    old_pdf = tmp_path / "old_submission.pdf"
    new_pdf = tmp_path / "permit_set_sheet_01.pdf"
    old_pdf.write_bytes(b"%PDF-1.4\n")
    new_pdf.write_bytes(b"%PDF-1.4\n")
    desc_a = _descriptor(old_pdf, DrawingKind.PDF)
    desc_b = _descriptor(new_pdf, DrawingKind.PDF)
    captured = {}

    def fake_scan(source, options):
        return [desc_a] if Path(source) == old_pdf else [desc_b]

    def fail_match(*args, **kwargs):
        raise AssertionError("explicit file pair should not use heuristic matching")

    class FakeJob:
        def __init__(self, candidates, options):
            captured["candidates"] = list(candidates)

        def run(self, progress_callback=None, is_cancelled=None):
            summary = BatchCompareSummary(started_at=datetime.now(), requested_pairs=1)
            summary.items.append(BatchCompareItemResult(candidate=captured["candidates"][0], status="completed"))
            summary.finished_at = datetime.now()
            return summary

    monkeypatch.setattr(pipeline, "scan_drawing_inputs", fake_scan)
    monkeypatch.setattr(pipeline, "match_drawing_sets", fail_match)
    monkeypatch.setattr(pipeline, "BatchCompareJob", FakeJob)
    monkeypatch.setattr(
        pipeline,
        "export_change_artifacts",
        lambda summary, output_dir, **kwargs: SimpleNamespace(
            output_paths={"artifact_manifest_json": str(Path(output_dir) / "artifact_manifest.json")},
            raw_change_count=0,
            zone_count=0,
            cloud_region_count=0,
            cloud_omitted_zone_count=0,
            to_dict=lambda: {},
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "export_preview_artifacts",
        lambda summary, output_dir, **kwargs: SimpleNamespace(
            manifest_path=str(Path(output_dir) / "preview_manifest.json"),
            artifacts=[],
            preview_count=0,
            to_dict=lambda: {},
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "export_executive_review_from_artifacts",
        lambda output_dir, **kwargs: SimpleNamespace(output_paths={"executive_review_html": str(Path(output_dir) / "executive_review.html")}, to_dict=lambda: {}),
    )
    monkeypatch.setattr(
        pipeline,
        "export_viewer_package",
        lambda artifact_dir, output_dir, **kwargs: SimpleNamespace(
            output_paths={"viewer_manifest_json": str(Path(output_dir) / "viewer_manifest.json")},
            overlay_count=0,
            to_dict=lambda: {},
        ),
    )
    # Audit-gates §11.4 — pipeline now invokes the subprocess proxy too.
    monkeypatch.setattr(
        pipeline,
        "export_viewer_package_isolated",
        lambda artifact_dir, *, options=None, **kw: (
            {
                "output_paths": {
                    "viewer_manifest_json": str(
                        Path((options or {}).get("viewer_dir") or artifact_dir)
                        / "viewer_manifest.json"
                    )
                },
                "overlay_count": 0,
                "pair_count": 0,
            },
            pipeline.SubprocessRunReport(exit_code=0, elapsed_s=0.0),
        ),
    )

    result = pipeline.FolderComparePipeline(
        pipeline.FolderCompareRunRequest(old_pdf, new_pdf, tmp_path / "out")
    ).run()

    candidate = captured["candidates"][0]
    assert candidate.status == MatchStatus.MANUAL_CONFIRMED
    assert candidate.source_a == desc_a
    assert candidate.source_b == desc_b
    assert result.compare_summary.requested_pairs == 1


def test_review_required_candidate_is_not_counted_as_confirmed(tmp_path, monkeypatch) -> None:
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    desc_a = _descriptor(tmp_path / "A-100.dxf")
    desc_b = _descriptor(tmp_path / "B-100.dxf")
    candidate = MatchCandidate(desc_a, desc_b, score=0.65, status=MatchStatus.REVIEW_REQUIRED)

    monkeypatch.setattr(pipeline, "scan_drawing_inputs", lambda source, options: [desc_a] if "old" in str(source) else [desc_b])
    monkeypatch.setattr(pipeline, "match_drawing_sets", lambda a, b, options: [candidate])

    class FakeJob:
        def __init__(self, candidates, options):
            self.candidates = candidates

        def run(self, progress_callback=None, is_cancelled=None):
            return BatchCompareSummary(started_at=datetime.now(), requested_pairs=0)

    monkeypatch.setattr(pipeline, "BatchCompareJob", FakeJob)
    monkeypatch.setattr(
        pipeline,
        "export_change_artifacts",
        lambda summary, output_dir, **kwargs: SimpleNamespace(
            output_paths={"artifact_manifest_json": str(Path(output_dir) / "artifact_manifest.json")},
            raw_change_count=0,
            zone_count=0,
            cloud_region_count=0,
            cloud_omitted_zone_count=0,
            to_dict=lambda: {},
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "export_preview_artifacts",
        lambda summary, output_dir, **kwargs: SimpleNamespace(
            manifest_path=str(Path(output_dir) / "preview_manifest.json"),
            artifacts=[],
            preview_count=0,
            to_dict=lambda: {},
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "export_executive_review_from_artifacts",
        lambda output_dir, **kwargs: SimpleNamespace(output_paths={"executive_review_html": str(Path(output_dir) / "executive_review.html")}, to_dict=lambda: {}),
    )
    monkeypatch.setattr(
        pipeline,
        "export_viewer_package",
        lambda artifact_dir, output_dir, **kwargs: SimpleNamespace(
            output_paths={"viewer_manifest_json": str(Path(output_dir) / "viewer_manifest.json")},
            overlay_count=0,
            to_dict=lambda: {},
        ),
    )
    # Audit-gates §11.4 — pipeline now invokes the subprocess proxy too.
    monkeypatch.setattr(
        pipeline,
        "export_viewer_package_isolated",
        lambda artifact_dir, *, options=None, **kw: (
            {
                "output_paths": {
                    "viewer_manifest_json": str(
                        Path((options or {}).get("viewer_dir") or artifact_dir)
                        / "viewer_manifest.json"
                    )
                },
                "overlay_count": 0,
                "pair_count": 0,
            },
            pipeline.SubprocessRunReport(exit_code=0, elapsed_s=0.0),
        ),
    )

    result = pipeline.FolderComparePipeline(
        pipeline.FolderCompareRunRequest(tmp_path / "old", tmp_path / "new", tmp_path / "out")
    ).run()

    assert result.confirmed_pairs == 0
    assert result.review_required_pairs == 1
    assert result.compare_summary.requested_pairs == 0


def test_folder_compare_pipeline_writes_failed_sentinel_on_exception(tmp_path, monkeypatch) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    def fail_scan(source, options):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(pipeline, "scan_drawing_inputs", fail_scan)

    request = pipeline.FolderCompareRunRequest(old_dir, new_dir, tmp_path / "out")
    with pytest.raises(RuntimeError, match="scan exploded"):
        pipeline.FolderComparePipeline(request).run()

    assert (tmp_path / "out" / "_FAILED").exists()
    assert not (tmp_path / "out" / "_SUCCESS").exists()
    assert "scan exploded" in (tmp_path / "out" / "_FAILED").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# RV-20260502-001 hot-fix: render_timeout_seconds wiring + progress emits
# ---------------------------------------------------------------------------
#
# These two tests pin the contract that prevents the GUI from freezing at
# 88% on large industrial DXFs:
#
# - render_timeout_seconds must flow from FolderCompareRunRequest through
#   export_viewer_package so the per-pair render runs in a killable
#   subprocess instead of hanging matplotlib synchronously.
# - The 88→100 emit gap must be filled with intermediate stages so the
#   progress bar (and the 'active stage' label) advances even when the
#   underlying step is slow. Without this, even a slow-but-eventually-
#   succeeding render would leave the user looking at "88%" for minutes
#   and concluding the app crashed.


def _build_pipeline_with_capturing_doubles(tmp_path, monkeypatch):
    """Set up a working folder-compare pipeline with mocked downstream
    exporters and return a captured-kwargs dict the caller can assert on."""

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old = old_dir / "S-100_REV0.dxf"
    new = new_dir / "S-100_REV1.dxf"
    old.write_text("0\nEOF\n", encoding="utf-8")
    new.write_text("0\nEOF\n", encoding="utf-8")
    desc_a = _descriptor(old)
    desc_b = _descriptor(new)
    candidate = MatchCandidate(desc_a, desc_b, score=0.95, status=MatchStatus.AUTO_CONFIRMED)

    captured: dict = {
        "artifact_kwargs": None,
        "viewer_kwargs": None,
        "write_compare_state_json": None,
    }

    def fake_scan(source, options):
        return [desc_a] if Path(source) == old_dir else [desc_b]

    class FakeJob:
        def __init__(self, candidates, options):
            self.candidates = candidates
            self.options = options
            captured["write_compare_state_json"] = options.write_compare_state_json

        def run(self, progress_callback=None, is_cancelled=None):
            state_dir = Path(self.options.compare_state_dir)
            stream_dir = state_dir / "streams"
            stream_dir.mkdir(parents=True, exist_ok=True)
            (stream_dir / "pair.jsonl").write_text('{"key":"line_1"}\n', encoding="utf-8")
            if self.options.write_compare_state_json:
                (state_dir / "compare_state.json").write_text("{}", encoding="utf-8")
            summary = BatchCompareSummary(started_at=datetime.now(), requested_pairs=1)
            summary.items.append(BatchCompareItemResult(candidate=candidate, status="completed"))
            summary.finished_at = datetime.now()
            return summary

    def fake_artifacts(summary, output_dir, **kwargs):
        captured["artifact_kwargs"] = dict(kwargs)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            output_paths={"artifact_manifest_json": str(Path(output_dir) / "artifact_manifest.json")},
            raw_change_count=0,
            zone_count=0,
            cloud_region_count=0,
            cloud_omitted_zone_count=0,
            to_dict=lambda: {},
        )

    def fake_preview(summary, output_dir, **kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        manifest = Path(output_dir) / "preview_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return SimpleNamespace(
            manifest_path=str(manifest),
            artifacts=[],
            preview_count=0,
            to_dict=lambda: {},
        )

    def fake_executive(output_dir, **kwargs):
        return SimpleNamespace(
            output_paths={
                "executive_review_html": str(Path(output_dir) / "executive_review.html"),
                "review_dashboard_json": str(Path(output_dir) / "review_dashboard.json"),
            },
            to_dict=lambda: {},
        )

    def fake_viewer(artifact_dir, output_dir, **kwargs):
        captured["viewer_kwargs"] = dict(kwargs)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        manifest = Path(output_dir) / "viewer_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return SimpleNamespace(
            output_paths={"viewer_manifest_json": str(manifest)},
            pair_count=1,
            overlay_count=0,
            to_dict=lambda: {},
        )

    monkeypatch.setattr(pipeline, "scan_drawing_inputs", fake_scan)
    monkeypatch.setattr(pipeline, "match_drawing_sets", lambda a, b, options: [candidate])
    monkeypatch.setattr(pipeline, "BatchCompareJob", FakeJob)
    monkeypatch.setattr(pipeline, "export_change_artifacts", fake_artifacts)
    monkeypatch.setattr(pipeline, "export_preview_artifacts", fake_preview)
    monkeypatch.setattr(pipeline, "export_executive_review_from_artifacts", fake_executive)
    monkeypatch.setattr(pipeline, "export_viewer_package", fake_viewer)

    # Audit-gates §11.4 — pipeline now invokes the viewer subprocess proxy.
    # Stub it to call the same fake_viewer in-process so existing tests do
    # not need real subprocess fixtures. The proxy returns ``(dict, report)``
    # so we wrap the SimpleNamespace produced by fake_viewer accordingly.
    def fake_viewer_isolated(
        artifact_dir,
        *,
        options=None,
        memory_cap_mb=None,
        timeout_s=None,
        progress_callback=None,
        python_executable=None,
        allow_inprocess_fallback=False,
        fault_log_dir=None,  # §13.4 Phase B-2 — child fault log dir passthrough
    ):
        opts = dict(options or {})
        viewer_dir_arg = opts.pop("viewer_dir", None)
        result = fake_viewer(artifact_dir, viewer_dir_arg, **opts)
        # Forward a single memory_sample heartbeat so progress tests that
        # assert progress_callback fires during viewer build keep working.
        if callable(progress_callback):
            try:
                progress_callback({"event": "memory_sample", "peak_working_set_mb": 100.0})
            except Exception:
                pass
        return (
            {
                "output_paths": dict(result.output_paths),
                "pair_count": result.pair_count,
                "overlay_count": result.overlay_count,
                "viewer_dir": str(viewer_dir_arg) if viewer_dir_arg else "",
            },
            pipeline.SubprocessRunReport(exit_code=0, elapsed_s=0.0),
        )

    monkeypatch.setattr(pipeline, "export_viewer_package_isolated", fake_viewer_isolated)

    return old_dir, new_dir, captured


def test_render_timeout_seconds_flows_into_export_viewer_package(tmp_path, monkeypatch) -> None:
    """RV-20260502-001 hot-fix: per-pair render timeout must reach the viewer
    exporter so the unbounded matplotlib synchronous render at the heart of
    the 88% freeze runs inside a killable subprocess.

    Default value (60s) covers the normal case; explicit overrides also flow
    through.
    """

    old_dir, new_dir, captured = _build_pipeline_with_capturing_doubles(tmp_path, monkeypatch)

    # 1) Default — no field override — should land as 180.
    #    Phase A bumped 60→180 so the chained PyMuPDF+Matplotlib fallback fits
    #    inside one subprocess timeout budget (RV-20260502-001 §3.1 follow-up).
    request_default = pipeline.FolderCompareRunRequest(old_dir, new_dir, tmp_path / "out_default")
    assert request_default.render_timeout_seconds == 180
    pipeline.FolderComparePipeline(request_default).run()
    assert captured["viewer_kwargs"]["render_timeout_seconds"] == 180

    # 2) Explicit override — must propagate verbatim, including 0 (advanced
    #    callers can opt out of the safety belt for diagnostic runs)
    captured["viewer_kwargs"] = None
    request_disabled = pipeline.FolderCompareRunRequest(
        old_dir, new_dir, tmp_path / "out_disabled", render_timeout_seconds=0
    )
    pipeline.FolderComparePipeline(request_disabled).run()
    assert captured["viewer_kwargs"]["render_timeout_seconds"] == 0

    # 3) Explicit large override
    captured["viewer_kwargs"] = None
    request_long = pipeline.FolderCompareRunRequest(
        old_dir, new_dir, tmp_path / "out_long", render_timeout_seconds=300
    )
    pipeline.FolderComparePipeline(request_long).run()
    assert captured["viewer_kwargs"]["render_timeout_seconds"] == 300


def test_fast_first_review_defers_heavy_exports_and_tiles(tmp_path, monkeypatch) -> None:
    old_dir, new_dir, captured = _build_pipeline_with_capturing_doubles(tmp_path, monkeypatch)

    request = pipeline.FolderCompareRunRequest(
        old_dir,
        new_dir,
        tmp_path / "out",
        fast_first_review=True,
        viewer_render_policy="lazy",
        max_zone_tiles=300,
        export_marked_pdf=True,
        marked_pdf_mode="selected",
        prefetch_neighbor_tiles=True,
        tile_prefetch_radius=1,
        auto_export_structural_clouds=True,
    )

    result = pipeline.FolderComparePipeline(request).run()
    run_manifest = json.loads(Path(result.run_manifest_path).read_text(encoding="utf-8"))

    assert captured["artifact_kwargs"]["export_cloud_marks"] is False
    assert captured["viewer_kwargs"]["render_policy"] == "top-issues"
    assert captured["viewer_kwargs"]["max_zone_tiles"] == 0
    assert captured["viewer_kwargs"]["export_marked_pdf"] is False
    assert captured["viewer_kwargs"]["marked_pdf_mode"] == "off"
    assert captured["viewer_kwargs"]["prefetch_neighbor_tiles"] is False
    assert captured["viewer_kwargs"]["tile_prefetch_radius"] == 0
    assert captured["viewer_kwargs"]["build_lod_tiles"] is False
    assert captured["write_compare_state_json"] is False
    assert run_manifest["inputs"]["fast_first_review"] is True
    assert run_manifest["stages"]["first_review_ready"]["status"] == "completed"
    assert run_manifest["stages"]["first_review_ready"]["cloud_marks_deferred"] is True
    assert run_manifest["stages"]["first_review_ready"]["marked_pdf_deferred"] is True
    assert run_manifest["stages"]["first_review_ready"]["build_lod_tiles"] is False
    assert run_manifest["stages"]["fast_state_cleanup"]["status"] == "completed"
    assert run_manifest["stages"]["fast_state_cleanup"]["removed_file_count"] == 1
    assert not (Path(result.compare_state_dir) / "streams").exists()
    assert not (Path(result.compare_state_dir) / "compare_state.json").exists()


def test_progress_callback_breaks_silent_block_between_88_and_100(tmp_path, monkeypatch) -> None:
    """RV-20260502-001 hot-fix: the previously-silent block between the 88%
    'preview' emit and the 100% 'done' emit now contains intermediate emits
    so the GUI bar advances during the long preview/viewer step. Without
    this, even a slow-but-eventually-succeeding render leaves the user
    staring at 88% for minutes.

    This test does not check exact percentages (those may be tuned later);
    it pins the *shape*: at least three intermediate stages between the
    88-emit and the 100-emit, each with a distinct stage label."""

    old_dir, new_dir, _captured = _build_pipeline_with_capturing_doubles(tmp_path, monkeypatch)

    progress_log: list[tuple[str, int, str]] = []

    def progress_cb(stage: str, percent: int, message: str) -> None:
        progress_log.append((stage, percent, message))

    request = pipeline.FolderCompareRunRequest(old_dir, new_dir, tmp_path / "out")
    pipeline.FolderComparePipeline(request).run(progress_callback=progress_cb)

    percents = [p for _, p, _ in progress_log]
    stages = [s for s, _, _ in progress_log]

    # Bookends are present and in the right order
    assert 88 in percents, f"missing 88% emit in {percents}"
    assert 100 in percents, f"missing 100% emit in {percents}"
    assert percents.index(88) < percents.index(100)

    # At least three intermediate emits in (88, 100) — these are what kept
    # the previously-silent block visible to the user.
    intermediates = [p for p in percents if 88 < p < 100]
    assert len(intermediates) >= 3, (
        f"expected ≥3 intermediate emits between 88 and 100, got {intermediates}; "
        "RV-20260502-001 hot-fix: the silent block at 88% must now report sub-stages."
    )

    # Stages should be monotonic in percent
    pairs_with_percent = [(s, p) for s, p, _ in progress_log if 88 <= p <= 100]
    sorted_pairs = sorted(pairs_with_percent, key=lambda sp: sp[1])
    assert pairs_with_percent == sorted_pairs, (
        f"progress percentages went backwards: {pairs_with_percent}"
    )

    # Distinct stage labels for the intermediates (not all the same "preview")
    intermediate_stages = [s for s, p, _ in progress_log if 88 < p < 100]
    assert len(set(intermediate_stages)) >= 2, (
        f"intermediate stages should be labelled distinctly so the user knows what's "
        f"actually happening; got {intermediate_stages}"
    )


def test_progress_callback_breaks_silent_block_between_96_and_100(tmp_path, monkeypatch) -> None:
    """Audit-gates §10 follow-up — sub-progress between 96% (viewer build
    started) and 100% (done) was previously absent. On S20-class drawings
    that gap reads as ~12 minutes of frozen "97%" to the user (the GUI
    progress bar paints 96 but rounds visually to 97). This test pins the
    new emits at 97/98/99 plus the 96 → 100 bookends."""

    old_dir, new_dir, _captured = _build_pipeline_with_capturing_doubles(tmp_path, monkeypatch)

    progress_log: list[tuple[str, int, str]] = []

    def progress_cb(stage: str, percent: int, message: str) -> None:
        progress_log.append((stage, percent, message))

    request = pipeline.FolderCompareRunRequest(old_dir, new_dir, tmp_path / "out")
    pipeline.FolderComparePipeline(request).run(progress_callback=progress_cb)

    percents = [p for _, p, _ in progress_log]
    assert 96 in percents, f"missing 96% emit (viewer build start): {percents}"
    assert 100 in percents, f"missing 100% emit: {percents}"

    # Audit-gates §10 follow-up — 96/97/98/99 are now all explicitly emitted
    # so the GUI bar advances during viewer post-processing instead of
    # appearing frozen.
    intermediate_after_viewer = [p for p in percents if 96 < p < 100]
    assert 97 in intermediate_after_viewer, (
        f"missing 97% sub-stage (post-viewer cleanup): {percents}"
    )
    assert 98 in intermediate_after_viewer, (
        f"missing 98% sub-stage (export profile): {percents}"
    )
    assert 99 in intermediate_after_viewer, (
        f"missing 99% sub-stage (sharable audit): {percents}"
    )

    # The 96% message must include zone count hint when zones are present
    # so the user understands the pause is expected.
    viewer_messages = [m for _, p, m in progress_log if p == 96]
    assert viewer_messages, "expected at least one 96% emit message"
    # The doubles in this test produce a small zone count, so the hint is
    # optional; but the message must still mention "뷰어 패키지" so the
    # user identifies the stage.
    assert all("뷰어" in m for m in viewer_messages), (
        f"96% message must mention viewer stage; got {viewer_messages}"
    )

    # 100% emit message reads as a transition into result loading, not a
    # final "all done" — the GUI dashboard population still needs seconds.
    done_messages = [m for _, p, m in progress_log if p == 100]
    assert done_messages, "expected exactly one 100% emit"
    assert any("적재" in m or "완료" in m for m in done_messages), (
        f"100% message should hint at post-pipeline loading; got {done_messages}"
    )
