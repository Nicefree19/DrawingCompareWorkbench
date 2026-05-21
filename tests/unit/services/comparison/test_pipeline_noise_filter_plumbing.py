# -*- coding: utf-8 -*-
"""Phase O — pipeline plumbing for noise filter settings.

Pins that ``FolderComparePipeline`` actually reads the saved
``NoiseFilterSettings`` and threads them into:
  * ``BatchCompareOptions.comparison_config.sensitivity`` (O2/O3)
  * ``export_preview_artifacts(zone_options=...)``  (O4)

If we don't pin this, the dialog's "save" button is purely cosmetic
because nothing applies the user's preferences during a real run.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

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
from src.services.comparison.noise_filter_io import NoiseFilterSettings


def _descriptor(path: Path) -> DrawingFileDescriptor:
    return DrawingFileDescriptor(
        path=str(path),
        kind=DrawingKind.CAD,
        extension=path.suffix,
        identity=parse_filename_identity(path),
    )


def _build_pipeline_with_fakes(
    tmp_path: Path,
    monkeypatch,
    noise_filter_settings: NoiseFilterSettings,
    *,
    use_ocr: bool = False,
) -> tuple[pipeline.FolderComparePipeline, dict]:
    """Build a FolderComparePipeline whose stages are all monkeypatched
    fakes. Returns the pipeline and a ``captured`` dict that records
    BatchCompareOptions and export_preview_artifacts kwargs."""

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
    candidate = MatchCandidate(
        desc_a, desc_b, score=0.95, status=MatchStatus.AUTO_CONFIRMED,
    )

    captured: dict = {}

    def fake_scan(source, options):
        return [desc_a] if Path(source) == old_dir else [desc_b]

    def fake_match(a, b, options):
        return [candidate]

    class FakeJob:
        def __init__(self, candidates, options):
            captured["batch_options"] = options
            self.candidates = candidates

        def run(self, progress_callback=None, is_cancelled=None):
            summary = BatchCompareSummary(
                started_at=datetime.now(), requested_pairs=1,
            )
            summary.items.append(
                BatchCompareItemResult(candidate=candidate, status="completed"),
            )
            summary.finished_at = datetime.now()
            return summary

    def fake_artifacts(summary, output_dir, **kwargs):
        captured["artifacts_kwargs"] = kwargs
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            output_paths={
                "artifact_manifest_json":
                    str(Path(output_dir) / "artifact_manifest.json"),
            },
            raw_change_count=0,
            zone_count=0,
            cloud_region_count=0,
            cloud_omitted_zone_count=0,
            to_dict=lambda: {"raw_change_count": 0},
        )

    def fake_preview(summary, output_dir, **kwargs):
        captured["preview_kwargs"] = kwargs
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
                "drawing_change_brief_csv":
                    str(Path(output_dir) / "drawing_change_brief.csv"),
                "review_dashboard_json":
                    str(Path(output_dir) / "review_dashboard.json"),
            },
            to_dict=lambda: {"output_paths": {"executive_review_html": str(html)}},
        )

    def fake_viewer(artifact_dir, output_dir, **kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        manifest = Path(output_dir) / "viewer_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
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
    monkeypatch.setattr(
        pipeline, "export_executive_review_from_artifacts", fake_executive,
    )
    monkeypatch.setattr(pipeline, "export_viewer_package", fake_viewer)

    request = pipeline.FolderCompareRunRequest(
        source_a=str(old_dir),
        source_b=str(new_dir),
        output_dir=str(tmp_path / "out"),
        noise_filter_settings=noise_filter_settings,
        use_ocr=use_ocr,
    )
    return pipeline.FolderComparePipeline(request), captured


def test_settings_threaded_to_sensitivity_config(tmp_path, monkeypatch):
    """O2/O3 fields land on ``BatchCompareOptions.comparison_config.sensitivity``."""
    settings = NoiseFilterSettings(
        global_alignment_enabled=False,
        hungarian_max_subset=400,
        cosmetic_detection_enabled=True,
        suppress_cosmetic_only=True,
        cosmetic_attributes=("color",),
        min_changes_per_zone=2,
        single_entity_noise_score_threshold=0.6,
        noise_filter_strength="high",
    )
    p, captured = _build_pipeline_with_fakes(tmp_path, monkeypatch, settings)
    p.run()

    assert "batch_options" in captured
    sens = captured["batch_options"].comparison_config.sensitivity
    assert sens.global_alignment_enabled is False
    assert sens.hungarian_max_subset == 400
    assert sens.cosmetic_detection_enabled is True
    assert sens.suppress_cosmetic_only is True
    assert sens.cosmetic_attributes == ("color",)


def test_settings_threaded_to_zone_options(tmp_path, monkeypatch):
    """O4 fields land on ``export_preview_artifacts(zone_options=...)``."""
    settings = NoiseFilterSettings(
        min_changes_per_zone=3,
        single_entity_noise_score_threshold=0.55,
    )
    p, captured = _build_pipeline_with_fakes(tmp_path, monkeypatch, settings)
    p.run()

    assert "preview_kwargs" in captured
    zone_options = captured["preview_kwargs"].get("zone_options")
    assert zone_options is not None
    assert zone_options.min_changes_per_zone == 3
    assert zone_options.single_entity_noise_score_threshold == pytest.approx(0.55)


def test_default_settings_preserve_legacy_behaviour(tmp_path, monkeypatch):
    """Default settings = no behaviour change for existing callers."""
    settings = NoiseFilterSettings.default()
    p, captured = _build_pipeline_with_fakes(tmp_path, monkeypatch, settings)
    p.run()

    sens = captured["batch_options"].comparison_config.sensitivity
    # Default = legacy behaviour preserved
    assert sens.global_alignment_enabled is True
    assert sens.suppress_cosmetic_only is False
    zone_options = captured["preview_kwargs"]["zone_options"]
    assert zone_options.min_changes_per_zone == 1
    assert zone_options.single_entity_noise_score_threshold == pytest.approx(0.7)


def test_settings_threaded_to_change_artifacts(tmp_path, monkeypatch):
    """Codex review RV-20260507-003 #1 fix — same zone_options used for
    artifact export so change_zones.csv / artifact_manifest.json /
    cloud_marked DXFs / dashboard JSON respect min_changes_per_zone."""
    settings = NoiseFilterSettings(
        min_changes_per_zone=4,
        single_entity_noise_score_threshold=0.65,
    )
    p, captured = _build_pipeline_with_fakes(tmp_path, monkeypatch, settings)
    p.run()

    assert "artifacts_kwargs" in captured
    zone_options = captured["artifacts_kwargs"].get("zone_options")
    assert zone_options is not None, (
        "export_change_artifacts must receive zone_options or noise-suppressed "
        "zones leak into artifact outputs (Codex RV-20260507-003 #1)"
    )
    assert zone_options.min_changes_per_zone == 4
    # 同一 ChangeZoneOptions 인스턴스가 preview 와 artifacts 양쪽 모두에 전달돼야
    # 한 번에 일관된 zone 집합을 만들 수 있다.
    assert (
        captured["preview_kwargs"]["zone_options"]
        is captured["artifacts_kwargs"]["zone_options"]
    )


def test_pdf_noise_filter_strength_threaded_to_batch_options(
    tmp_path, monkeypatch,
):
    """Codex review RV-20260507-003 #2 fix — O5 strength reaches
    BatchCompareOptions.pdf_noise_filter_strength (and thus the
    DrawingDiffer config inside compare_pdf_documents)."""
    settings = NoiseFilterSettings(noise_filter_strength="high")
    p, captured = _build_pipeline_with_fakes(tmp_path, monkeypatch, settings)
    p.run()

    assert "batch_options" in captured
    assert captured["batch_options"].pdf_noise_filter_strength == "high"


def test_ocr_fallback_threaded_to_batch_options(tmp_path, monkeypatch):
    settings = NoiseFilterSettings()
    p, captured = _build_pipeline_with_fakes(
        tmp_path, monkeypatch, settings, use_ocr=True,
    )
    p.run()

    assert "batch_options" in captured
    assert captured["batch_options"].use_ocr_fallback is True


def test_boundary_min_changes_max_threads_through_pipeline(
    tmp_path, monkeypatch,
):
    """RV-20260508-001 #8 — max boundary value (10) for
    min_changes_per_zone must round-trip through the pipeline into
    ChangeZoneOptions, not get clamped at 1 or coerced to default."""
    settings = NoiseFilterSettings(min_changes_per_zone=10)
    p, captured = _build_pipeline_with_fakes(tmp_path, monkeypatch, settings)
    p.run()
    zo = captured["preview_kwargs"]["zone_options"]
    assert zo.min_changes_per_zone == 10


def test_boundary_threshold_zero_threads_through_pipeline(
    tmp_path, monkeypatch,
):
    """RV-20260508-001 #8 — extreme value 0.0 (block all single-entity
    zones regardless of noise_score) must reach ChangeZoneOptions."""
    settings = NoiseFilterSettings(
        min_changes_per_zone=2,
        single_entity_noise_score_threshold=0.0,
    )
    p, captured = _build_pipeline_with_fakes(tmp_path, monkeypatch, settings)
    p.run()
    zo = captured["preview_kwargs"]["zone_options"]
    assert zo.single_entity_noise_score_threshold == pytest.approx(0.0)


def test_boundary_threshold_one_threads_through_pipeline(
    tmp_path, monkeypatch,
):
    """RV-20260508-001 #8 — extreme value 1.0 (never block, all
    single-entity zones promoted) must reach ChangeZoneOptions."""
    settings = NoiseFilterSettings(
        min_changes_per_zone=2,
        single_entity_noise_score_threshold=1.0,
    )
    p, captured = _build_pipeline_with_fakes(tmp_path, monkeypatch, settings)
    p.run()
    zo = captured["preview_kwargs"]["zone_options"]
    assert zo.single_entity_noise_score_threshold == pytest.approx(1.0)


def test_missing_settings_falls_back_to_disk_then_defaults(
    tmp_path, monkeypatch,
):
    """When request.noise_filter_settings is None and disk file is
    missing, the pipeline must fall back to NoiseFilterSettings.default()
    rather than raising."""
    # Point the disk loader at a non-existent path so it returns default()
    fake_path = tmp_path / "absent_noise_filter_config.json"
    monkeypatch.setattr(
        pipeline,
        "load_noise_filter_settings",
        lambda path=None: pipeline.NoiseFilterSettings.default()
        if not fake_path.exists()
        else None,
    )
    # Build pipeline with explicit None
    p, captured = _build_pipeline_with_fakes(tmp_path, monkeypatch, None)
    p.run()

    sens = captured["batch_options"].comparison_config.sensitivity
    assert sens.global_alignment_enabled is True  # default
    assert captured["preview_kwargs"]["zone_options"].min_changes_per_zone == 1
