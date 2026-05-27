# -*- coding: utf-8 -*-
"""Tests for drawing change review project state and previews."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.services.comparison.base import ChangeRecord, ChangeType, ComparisonResult
from src.services.comparison.drawing_batch import (
    BatchCompareItemResult,
    BatchCompareSummary,
    DrawingFileDescriptor,
    DrawingKind,
    MatchCandidate,
    MatchStatus,
    parse_filename_identity,
)
from src.services.comparison.review_project import (
    ReviewStateRecord,
    _bbox_to_pixel_bbox,
    export_preview_artifacts,
    load_review_state,
    save_review_state,
    update_artifact_manifest,
    write_review_project,
)
from src.services.comparison.change_zones import write_change_zone_stream


def _descriptor(path: Path) -> DrawingFileDescriptor:
    return DrawingFileDescriptor(
        path=str(path),
        kind=DrawingKind.CAD,
        extension=path.suffix.lower(),
        identity=parse_filename_identity(path),
    )


def _line_change(key: str, x: float) -> ChangeRecord:
    return ChangeRecord(
        key=key,
        change_type=ChangeType.ADDED,
        new_value={"start": (x, 0), "end": (x + 10, 0)},
        metadata={"layer": "BEAM", "entity_type": "LINE", "change_type": "added"},
    )


def _summary(tmp_path: Path, result: ComparisonResult) -> BatchCompareSummary:
    old_path = tmp_path / "S21-7001_old.dxf"
    new_path = tmp_path / "S21-7001_new.dxf"
    old_path.write_text("0\nEOF\n", encoding="utf-8")
    new_path.write_text("0\nEOF\n", encoding="utf-8")
    candidate = MatchCandidate(
        source_a=_descriptor(old_path),
        source_b=_descriptor(new_path),
        score=0.99,
        status=MatchStatus.AUTO_CONFIRMED,
    )
    return BatchCompareSummary(
        started_at=datetime.now(),
        requested_pairs=1,
        items=[BatchCompareItemResult(candidate=candidate, result=result, status="completed")],
    )


def test_review_state_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "review_state.json"
    save_review_state(
        path,
        {
            "S21-7001:C-001": ReviewStateRecord(
                pair_id="S21-7001",
                zone_id="C-001",
                status="confirmed",
                note="checked",
            )
        },
    )

    records = load_review_state(path)

    assert records["S21-7001:C-001"].status == "confirmed"
    assert records["S21-7001:C-001"].note == "checked"


def test_review_state_normalizes_legacy_ignored_to_hold(tmp_path: Path) -> None:
    path = tmp_path / "review_state.json"
    path.write_text(
        '{"records":[{"pair_id":"p1","zone_id":"z1","status":"ignored"}]}',
        encoding="utf-8",
    )

    records = load_review_state(path)

    assert records["p1:z1"].status == "hold"
    save_review_state(path, records)
    assert '"status": "hold"' in path.read_text(encoding="utf-8")


def test_bbox_to_pixel_bbox_uses_dxf_transform() -> None:
    transform = {
        "min_x": 0,
        "min_y": 0,
        "img_width": 1000,
        "img_height": 500,
        "scale_x": 1,
        "scale_y": 1,
    }

    assert _bbox_to_pixel_bbox((100, 50, 200, 150), transform) == [100.0, 350.0, 200.0, 450.0]


def test_preview_uses_stream_records_when_memory_details_are_truncated(tmp_path: Path, monkeypatch) -> None:
    changes = [_line_change(f"line_{index}", index * 1000.0) for index in range(5)]
    result = ComparisonResult(source_a="old.dxf", source_b="new.dxf")
    result.add_change(changes[0])
    result.add_change(changes[1])
    result.metadata["change_counts"] = {"added": 5, "deleted": 0, "modified": 0}
    result.metadata["truncated_changes"] = True
    result.metadata.update(write_change_zone_stream(changes, tmp_path / "zones.jsonl", pair_id="S21-7001"))

    def fake_render(_dxf_path, output_path, *, dpi, max_edge_px):
        output_path.write_bytes(b"fake png")
        return {
            "min_x": 0,
            "min_y": 0,
            "img_width": 1000,
            "img_height": 500,
            "scale_x": 1,
            "scale_y": 1,
        }

    monkeypatch.setattr(
        "src.services.comparison.review_project._render_dxf_to_png",
        fake_render,
    )

    package = export_preview_artifacts(_summary(tmp_path, result), tmp_path / "preview")

    assert package.preview_count == 1
    assert package.zone_overlay_count == 5
    assert len(package.artifacts[0].zone_overlays) == 5
    assert Path(package.manifest_path).exists()


def test_preview_reuses_prebuilt_change_zones_without_reclustering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    result = ComparisonResult(source_a="old.dxf", source_b="new.dxf")
    result.add_change(_line_change("line_1", 1000.0))
    result.metadata["change_zones"] = [
        {
            "zone_id": "C-001",
            "pair_id": "placeholder",
            "bbox": [1000.0, 0.0, 1010.0, 10.0],
            "old_bbox": None,
            "centroid": [1005.0, 5.0],
            "raw_change_count": 1,
            "added_count": 1,
            "deleted_count": 0,
            "modified_count": 0,
            "layers": ["BEAM"],
            "entity_types": ["LINE"],
            "representative_change_keys": ["line_1"],
            "status": "needs_review",
            "reasons": ["prebuilt"],
            "metadata": {"source": "artifact"},
        }
    ]

    def fail_build(*_args, **_kwargs):
        raise AssertionError("preview should reuse prebuilt zones")

    def fake_render(_dxf_path, output_path, *, dpi, max_edge_px):
        output_path.write_bytes(b"fake png")
        return {
            "min_x": 0,
            "min_y": 0,
            "img_width": 2000,
            "img_height": 1000,
            "scale_x": 1,
            "scale_y": 1,
        }

    monkeypatch.setattr("src.services.comparison.review_project.build_change_zones", fail_build)
    monkeypatch.setattr(
        "src.services.comparison.review_project._render_dxf_to_png",
        fake_render,
    )

    package = export_preview_artifacts(_summary(tmp_path, result), tmp_path / "preview")

    assert package.zone_overlay_count == 1
    assert package.artifacts[0].zone_overlays[0].zone_id == "C-001"


def test_preview_limit_can_skip_png_render_but_keep_zone_metadata(tmp_path: Path, monkeypatch) -> None:
    changes = [_line_change(f"line_{index}", index * 1000.0) for index in range(5)]
    result = ComparisonResult(source_a="old.dxf", source_b="new.dxf")
    result.metadata["change_counts"] = {"added": 5, "deleted": 0, "modified": 0}
    result.metadata["truncated_changes"] = True
    result.metadata.update(write_change_zone_stream(changes, tmp_path / "zones.jsonl", pair_id="S21-7001"))

    def fail_render(*_args, **_kwargs):
        raise AssertionError("preview renderer should not run when max_preview_pairs=0")

    monkeypatch.setattr(
        "src.services.comparison.review_project._render_dxf_to_png",
        fail_render,
    )

    package = export_preview_artifacts(
        _summary(tmp_path, result),
        tmp_path / "preview",
        max_preview_pairs=0,
    )

    assert package.preview_count == 0
    assert package.preview_skipped_count == 1
    assert package.max_preview_pairs == 0
    assert package.zone_overlay_count == 5
    assert len(package.artifacts[0].zone_overlays) == 5
    assert "max_preview_pairs=0" in package.warnings[0]
    assert Path(package.manifest_path).exists()


def test_artifact_manifest_can_reference_preview_and_review_state(tmp_path: Path) -> None:
    manifest = tmp_path / "artifact_manifest.json"
    manifest.write_text('{"output_paths":{}}', encoding="utf-8")
    preview = tmp_path / "preview_manifest.json"
    review = tmp_path / "review_state.json"
    project = tmp_path / "review_project.json"

    update_artifact_manifest(
        manifest,
        preview_manifest_path=preview,
        review_state_path=review,
        review_project_path=project,
    )

    text = manifest.read_text(encoding="utf-8")
    assert "preview_manifest" in text
    assert "review_state" in text
    assert "review_project" in text


def test_sharable_review_project_redacts_source_and_cache_paths(tmp_path: Path) -> None:
    project = tmp_path / "review_project.json"
    source_a = tmp_path / "client_name" / "old"
    source_b = tmp_path / "client_name" / "new"
    cache_dir = tmp_path / "local_cache"
    state_dir = tmp_path / "compare_state"

    write_review_project(
        project,
        source_a=source_a,
        source_b=source_b,
        dxf_cache_dir=cache_dir,
        compare_state_dir=state_dir,
        artifact_dir=tmp_path / "artifacts",
        review_state_path=tmp_path / "review_state.json",
        preview_manifest_path=tmp_path / "preview" / "preview_manifest.json",
        export_profile="sharable",
    )

    text = project.read_text(encoding="utf-8")
    assert '"export_profile": "sharable"' in text
    assert str(source_a) not in text
    assert str(source_b) not in text
    assert str(cache_dir) not in text
    assert str(state_dir) not in text
