from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from src.services.comparison.perf_events import summarize_perf_events
from src.services.comparison.zone_render_service import (
    RenderJob,
    WorldWindow,
    bbox_to_pixel_rect,
    canonical_window_from_bbox,
    clear_render_index_cache,
    file_signature,
    get_drawing_render_index,
    render_cache_key,
    render_environment_signature,
    render_index_cache_stats,
    render_zone_pair,
    transform_for_window,
    union_bboxes,
    visible_handles_for_window,
)


def test_canonical_window_expands_tiny_bbox_to_stable_16_9_view() -> None:
    window = canonical_window_from_bbox([10.0, 20.0, 11.0, 21.0], min_size=250.0)

    assert window.width >= 250.0
    assert window.height >= 140.0
    assert abs((window.width / window.height) - (16 / 9)) < 0.01


def test_canonical_window_accepts_dict_bbox() -> None:
    window = canonical_window_from_bbox(
        {"min_x": 6970.0, "min_y": 6945.0, "max_x": 7230.0, "max_y": 7055.0},
        min_size=250.0,
    )

    assert window.xmin < 6970.0
    assert window.xmax > 7230.0
    assert window.ymin < 6945.0
    assert window.ymax > 7055.0


def test_transform_round_trip_for_window_center() -> None:
    window = canonical_window_from_bbox([100.0, 200.0, 300.0, 260.0], min_size=100.0)
    transform = transform_for_window(window, output_width=1600, output_height=900)
    rect = bbox_to_pixel_rect([window.xmin, window.ymin, window.xmax, window.ymax], transform)

    assert rect == {"x": 0.0, "y": 0.0, "width": 1600.0, "height": 900.0}

    world_to_pixel = transform["world_to_pixel"]
    pixel_to_world = transform["pixel_to_world"]
    world_x = (window.xmin + window.xmax) / 2.0
    world_y = (window.ymin + window.ymax) / 2.0
    px = world_x * world_to_pixel["a"] + world_to_pixel["e"]
    py = world_y * world_to_pixel["d"] + world_to_pixel["f"]
    round_trip_x = px * pixel_to_world["a"] + pixel_to_world["e"]
    round_trip_y = py * pixel_to_world["d"] + pixel_to_world["f"]

    assert abs(round_trip_x - world_x) < 1e-6
    assert abs(round_trip_y - world_y) < 1e-6


def test_cad_render_reuses_viewer_background_crop(tmp_path: Path) -> None:
    image_mod = pytest.importorskip("PIL.Image")

    before_source = tmp_path / "before.dxf"
    after_source = tmp_path / "after.dxf"
    before_source.write_text("stub", encoding="utf-8")
    after_source.write_text("stub", encoding="utf-8")

    before_bg = tmp_path / "before.png"
    after_bg = tmp_path / "after.png"
    image_mod.new("RGB", (100, 100), "white").save(before_bg)
    image_mod.new("RGB", (100, 100), "white").save(after_bg)

    background_transform = {
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": 100.0,
        "max_y": 100.0,
        "img_width": 100,
        "img_height": 100,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "backend_used": "fast",
    }
    job = RenderJob(
        pair_uuid="pair-bg",
        zone_id="Z-1",
        request_id="r-bg",
        source_before=before_source,
        source_after=after_source,
        world_window=WorldWindow(20.0, 20.0, 80.0, 80.0),
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf_cache",
        output_width=120,
        output_height=80,
        before_background_image=str(before_bg),
        after_background_image=str(after_bg),
        before_background_transform=background_transform,
        after_background_transform=background_transform,
        perf_event_root=tmp_path,
        perf_run_id="run-zone",
    )

    result = render_zone_pair(job)
    cached_result = render_zone_pair(job)

    assert result.cache_hit is False
    assert cached_result.cache_hit is True
    assert cached_result.cache_key == result.cache_key
    assert result.renderer_backend == "cad-background-image-crop"
    assert result.visual_fidelity == "cad_render"
    assert result.render_lifecycle == "ready"
    assert Path(result.before_image).exists()
    assert Path(result.after_image).exists()
    assert result.before_transform["renderer_backend"] == "cad-background-image-crop"
    assert "cad_background_crop:source=viewer_background" in result.warnings
    assert not any(w.startswith("dxf_prefilter:") for w in result.warnings)
    perf_summary = summarize_perf_events(tmp_path)
    assert perf_summary["stage_counts"]["zone_render"] == 2
    assert perf_summary["cache_hit_count"] == 1
    assert perf_summary["cache_miss_count"] == 1
    assert perf_summary["cache_hit_reasons"]["existing_render_result"] == 1
    assert perf_summary["cache_miss_reasons"]["artifact_missing"] == 1


def test_cad_background_crop_outside_image_returns_blank_without_source_fallback(
    tmp_path: Path,
) -> None:
    image_mod = pytest.importorskip("PIL.Image")

    before_source = tmp_path / "before.dxf"
    after_source = tmp_path / "after.dxf"
    before_source.write_text("not a real dxf", encoding="utf-8")
    after_source.write_text("not a real dxf", encoding="utf-8")

    before_bg = tmp_path / "before.png"
    after_bg = tmp_path / "after.png"
    image_mod.new("RGB", (100, 100), "white").save(before_bg)
    image_mod.new("RGB", (100, 100), "white").save(after_bg)

    background_transform = {
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": 100.0,
        "max_y": 100.0,
        "img_width": 100,
        "img_height": 100,
        "scale_x": 1.0,
        "scale_y": 1.0,
    }
    job = RenderJob(
        pair_uuid="pair-bg",
        zone_id="Z-outside",
        source_before=before_source,
        source_after=after_source,
        world_window=WorldWindow(500.0, 500.0, 700.0, 700.0),
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf_cache",
        output_width=120,
        output_height=80,
        before_background_image=str(before_bg),
        after_background_image=str(after_bg),
        before_background_transform=background_transform,
        after_background_transform=background_transform,
    )

    result = render_zone_pair(job)

    assert result.renderer_backend == "cad-background-image-crop"
    assert result.reason_code == "outside_background_bounds"
    assert Path(result.before_image).exists()
    assert Path(result.after_image).exists()
    assert "cad_background_crop:outside_background_bounds" in result.warnings
    assert not any(w.startswith("dxf_prefilter:") for w in result.warnings)


def test_union_bboxes_accepts_old_and_new_shapes() -> None:
    bbox = union_bboxes(
        {"min_x": 10, "min_y": 20, "max_x": 30, "max_y": 40},
        {"x": 25, "y": 35, "width": 50, "height": 10},
    )

    assert bbox == (10.0, 20.0, 75.0, 45.0)


def test_render_cache_key_changes_when_source_signature_changes(tmp_path: Path) -> None:
    before = tmp_path / "a.dxf"
    after = tmp_path / "b.dxf"
    before.write_text("a", encoding="utf-8")
    after.write_text("b", encoding="utf-8")
    window = canonical_window_from_bbox([0, 0, 100, 100])
    job = RenderJob(
        pair_uuid="pair-1",
        zone_id="C-001",
        source_before=before,
        source_after=after,
        world_window=window,
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf",
    )
    first = render_cache_key(job)

    time.sleep(0.001)
    after.write_text("changed", encoding="utf-8")
    if os.name == "nt":
        os.utime(after, None)

    assert render_cache_key(job) != first


def test_zone_file_signature_uses_shared_source_hash(tmp_path: Path) -> None:
    source = tmp_path / "zone-source.dxf"
    source.write_text("0\nEOF\n", encoding="utf-8")

    signature = file_signature(source)

    assert signature["source_hash"]
    assert signature["schema_version"] == 1
    assert signature["size"] == source.stat().st_size


def test_render_cache_key_changes_when_render_environment_changes(tmp_path: Path) -> None:
    before = tmp_path / "a.dxf"
    after = tmp_path / "b.dxf"
    before.write_text("a", encoding="utf-8")
    after.write_text("b", encoding="utf-8")
    window = canonical_window_from_bbox([0, 0, 100, 100])
    base = RenderJob(
        pair_uuid="pair-1",
        zone_id="C-001",
        source_before=before,
        source_after=after,
        world_window=window,
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf",
        render_environment_hash="env-a",
    )
    changed = RenderJob(
        pair_uuid="pair-1",
        zone_id="C-001",
        source_before=before,
        source_after=after,
        world_window=window,
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf",
        render_environment_hash="env-b",
    )

    assert render_cache_key(base) != render_cache_key(changed)


def test_render_environment_signature_includes_font_support_dir(tmp_path: Path) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    first = render_environment_signature(font_support_dirs=[font_dir])
    (font_dir / "sample.shx").write_text("font", encoding="utf-8")

    assert render_environment_signature(font_support_dirs=[font_dir]) != first


def test_drawing_render_index_reuses_parsed_source(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    dxf_path = tmp_path / "sample.dxf"
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.saveas(dxf_path)

    clear_render_index_cache()
    first = get_drawing_render_index(dxf_path, "env-a")
    second = get_drawing_render_index(dxf_path, "env-a")

    assert first is second
    stats = render_index_cache_stats()
    assert stats["entries"] == 1
    assert stats["lookup_count"] == 2
    assert stats["miss_count"] == 1
    assert stats["hit_count"] == 1
    assert stats["hit_rate"] == pytest.approx(0.5)
    assert stats["total_estimated_bytes"] > 0


def test_visible_handles_use_precomputed_envelopes(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    dxf_path = tmp_path / "sample.dxf"
    doc = ezdxf.new()
    near = doc.modelspace().add_line((0, 0), (50, 0))
    far = doc.modelspace().add_line((500, 500), (550, 500))
    doc.saveas(dxf_path)

    clear_render_index_cache()
    index = get_drawing_render_index(dxf_path, "env-a")
    handles = visible_handles_for_window(index, canonical_window_from_bbox([0, -10, 60, 10], min_size=20))

    assert str(near.dxf.handle) in handles
    assert str(far.dxf.handle) not in handles


def test_pdf_without_page_bbox_skips_exact_crop(tmp_path: Path) -> None:
    before = tmp_path / "a.pdf"
    after = tmp_path / "b.pdf"
    before.write_bytes(b"%PDF-1.4\n")
    after.write_bytes(b"%PDF-1.4\n")
    result = render_zone_pair(
        RenderJob(
            pair_uuid="pair-pdf",
            zone_id="C-001",
            source_before=before,
            source_after=after,
            world_window=canonical_window_from_bbox([0, 0, 100, 100]),
            cache_root=tmp_path / "cache",
            dxf_cache_dir=tmp_path / "dxf",
        )
    )

    assert result.render_lifecycle == "skipped_missing_page_bbox"
    assert result.visual_fidelity == "relative_overlay"
    assert result.reason_code == "missing_page_bbox"
    assert Path(result.before_image).exists()
    assert Path(result.after_image).exists()
    assert result.before_transform["renderer_backend"] == "relative-overlay-fallback"
    assert result.warnings
    cached = render_zone_pair(
        RenderJob(
            pair_uuid="pair-pdf",
            zone_id="C-001",
            source_before=before,
            source_after=after,
            world_window=canonical_window_from_bbox([0, 0, 100, 100]),
            cache_root=tmp_path / "cache",
            dxf_cache_dir=tmp_path / "dxf",
        )
    )
    assert cached.cache_hit is True
    assert cached.render_lifecycle == "skipped_missing_page_bbox"
    assert cached.reason_code == "missing_page_bbox"


def test_source_render_failure_returns_visible_relative_fallback(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    before = tmp_path / "bad-before.dxf"
    after = tmp_path / "bad-after.dxf"
    before.write_text("not a dxf", encoding="utf-8")
    after.write_text("not a dxf", encoding="utf-8")

    result = render_zone_pair(
        RenderJob(
            pair_uuid="pair-bad",
            zone_id="C-bad",
            source_before=before,
            source_after=after,
            world_window=canonical_window_from_bbox([0, 0, 100, 100]),
            cache_root=tmp_path / "cache",
            dxf_cache_dir=tmp_path / "dxf",
        )
    )

    assert result.visual_fidelity == "relative_overlay"
    assert result.render_lifecycle == "fallback_visible"
    assert result.reason_code == "source_render_failed"
    assert Path(result.before_image).exists()
    assert Path(result.after_image).exists()
    assert result.renderer_backend == "relative-overlay-fallback"


def test_pdf_with_rendered_background_crops_from_image_pixels(tmp_path: Path) -> None:
    Image = pytest.importorskip("PIL.Image")
    before = tmp_path / "a.pdf"
    after = tmp_path / "b.pdf"
    before.write_bytes(b"%PDF-1.4\n")
    after.write_bytes(b"%PDF-1.4\n")
    before_background = tmp_path / "before.png"
    after_background = tmp_path / "after.png"
    Image.new("RGB", (200, 120), "white").save(before_background)
    Image.new("RGB", (200, 120), "white").save(after_background)
    background_transform = {
        "coordinate_space": "image_pixels",
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": 200.0,
        "max_y": 120.0,
        "img_width": 200,
        "img_height": 120,
        "scale_x": 1.0,
        "scale_y": 1.0,
    }

    job = RenderJob(
        pair_uuid="pair-pdf",
        zone_id="C-001",
        source_before=before,
        source_after=after,
        world_window=WorldWindow(20.0, 20.0, 120.0, 80.0),
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf",
        before_background_image=str(before_background),
        after_background_image=str(after_background),
        before_background_transform=background_transform,
        after_background_transform=background_transform,
    )
    result = render_zone_pair(job)

    assert result.render_lifecycle == "ready"
    assert result.visual_fidelity == "pdf_render"
    assert Path(result.before_image).exists()
    assert Path(result.after_image).exists()
    assert result.before_transform["coordinate_space"] == "image_pixels"
    assert bbox_to_pixel_rect([20, 20, 120, 80], result.before_transform) == {
        "x": 0.0,
        "y": 0.0,
        "width": 1600.0,
        "height": 900.0,
    }
    meta_path = job.cache_root / "zone_crops" / job.pair_uuid / render_cache_key(job) / "render_result.json"
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["before_image"].startswith("zone_crops/")
    assert payload["after_image"].startswith("zone_crops/")
    assert not Path(payload["before_image"]).is_absolute()
    assert not Path(payload["after_image"]).is_absolute()


def test_cache_hit_keeps_current_request_id(tmp_path: Path) -> None:
    before = tmp_path / "a.dxf"
    after = tmp_path / "b.dxf"
    before.write_text("a", encoding="utf-8")
    after.write_text("b", encoding="utf-8")
    job = RenderJob(
        pair_uuid="pair-1",
        zone_id="C-001",
        request_id="new-request",
        source_before=before,
        source_after=after,
        world_window=canonical_window_from_bbox([0, 0, 100, 100]),
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf",
    )
    cache_key = render_cache_key(job)
    pair_dir = job.cache_root / "zone_crops" / job.pair_uuid / cache_key
    pair_dir.mkdir(parents=True)
    before_image = pair_dir / "C-001_before.png"
    after_image = pair_dir / "C-001_after.png"
    before_image.write_text("", encoding="utf-8")
    after_image.write_text("", encoding="utf-8")
    (pair_dir / "render_result.json").write_text(
        json.dumps(
            {
                "request_id": "old-request",
                "before_transform": {},
                "after_transform": {},
                "warnings": [],
                "dxf_index_cache": {
                    "lookup_count": 2,
                    "hit_count": 1,
                    "miss_count": 1,
                    "total_estimated_bytes": 1234,
                },
            }
        ),
        encoding="utf-8",
    )

    result = render_zone_pair(job)

    assert result.cache_hit is True
    assert result.request_id == "new-request"
    assert result.dxf_index_cache["lookup_count"] == 2
    assert result.dxf_index_cache["hit_count"] == 1


# ---------------------------------------------------------------------------
# Plan §17 Phase B-1 (GPT Pro F3 follow-up) — elapsed_ms must be populated
# on every render path so the GUI's per-event render_ms telemetry carries
# truth instead of always reading 0 from a never-set key.
# ---------------------------------------------------------------------------


def test_cache_hit_path_populates_elapsed_ms(tmp_path: Path) -> None:
    """Cache-hit returns must surface a non-zero elapsed_ms even though they
    skip rendering — the value reflects the metadata read + JSON parse cost.
    """
    before = tmp_path / "a.dxf"
    after = tmp_path / "b.dxf"
    before.write_text("a", encoding="utf-8")
    after.write_text("b", encoding="utf-8")
    job = RenderJob(
        pair_uuid="pair-elapsed",
        zone_id="Z-1",
        request_id="elapsed-1",
        source_before=before,
        source_after=after,
        world_window=canonical_window_from_bbox([0, 0, 100, 100]),
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf",
    )
    cache_key = render_cache_key(job)
    pair_dir = job.cache_root / "zone_crops" / job.pair_uuid / cache_key
    pair_dir.mkdir(parents=True)
    (pair_dir / "Z-1_before.png").write_text("", encoding="utf-8")
    (pair_dir / "Z-1_after.png").write_text("", encoding="utf-8")
    (pair_dir / "render_result.json").write_text(
        '{"before_transform":{},"after_transform":{},"warnings":[]}',
        encoding="utf-8",
    )

    result = render_zone_pair(job)

    assert result.cache_hit is True
    assert result.elapsed_ms > 0.0, (
        "cache-hit path must measure wall time, not return the dataclass default 0.0"
    )
    # Sanity ceiling — cache-hit should never plausibly take > 1 s for a tiny
    # synthetic JSON read. Catches accidental hot-loop regressions.
    assert result.elapsed_ms < 1000.0


def test_render_result_to_dict_includes_elapsed_ms() -> None:
    """JSONL forwarding path (zone_render_process.py:68) calls .to_dict();
    the GUI handler reads result_payload.get("elapsed_ms"). Without this
    assertion the wire format silently regresses.
    """
    from src.services.comparison.zone_render_service import RenderResult

    result = RenderResult(
        pair_uuid="x",
        zone_id="z",
        before_image="",
        after_image="",
        before_transform={},
        after_transform={},
        world_window={},
        renderer_backend="test",
        cache_key="k",
        cache_hit=False,
        visual_fidelity="cad_render",
        render_lifecycle="ready",
        warnings=[],
        request_id="r",
        elapsed_ms=42.5,
        dxf_index_cache={
            "entries": 1,
            "lookup_count": 2,
            "hit_count": 1,
            "miss_count": 1,
            "hit_rate": 0.5,
            "total_estimated_bytes": 1234,
            "byte_limit": 9999,
        },
    )
    payload = result.to_dict()
    assert "elapsed_ms" in payload
    assert payload["elapsed_ms"] == 42.5
    assert payload["fallback_reason_code"] == ""
    assert payload["dxf_index_cache_lookup_count"] == 2
    assert payload["dxf_index_cache_hit_count"] == 1
    assert payload["dxf_index_cache_total_estimated_bytes"] == 1234


# ---------------------------------------------------------------------------
# Plan §17 Phase B-3 (GPT Pro F3 follow-up) — DXF envelope-filter telemetry.
# Renders attach ``dxf_prefilter:applied:...`` or ``dxf_prefilter:skipped:...``
# warnings so the validator's perf summary and the GUI can show how much
# work the envelope filter is actually saving on each draw.
# ---------------------------------------------------------------------------


def test_safe_name_rejects_path_traversal_dot_dot() -> None:
    """Plan §19 A-4 (Agent T finding T4) — ``pair_uuid=".."`` previously
    survived ``_safe_name`` because ``.`` was in the allowed set. The
    output then escaped ``cache_root`` when joined into a path. The
    hardened sanitiser drops leading dots/hyphens and removes ``.``
    from the allowed character set.
    """
    from src.services.comparison.zone_render_service import _safe_name

    # Pure traversal string must NOT survive as ``..``.
    assert _safe_name("..") != ".."
    assert "/" not in _safe_name("..")
    # Combined traversal must collapse to a flat safe identifier.
    sanitised = _safe_name("../../etc/passwd")
    assert ".." not in sanitised
    assert "/" not in sanitised
    # Leading dot must not produce a hidden file name.
    assert not _safe_name(".hidden").startswith(".")
    # Empty string falls back to "item".
    assert _safe_name("") == "item"
    # A real UUID-like value must survive.
    assert _safe_name("pair-abc-123") == "pair-abc-123"


def test_dxf_prefilter_applied_above_threshold(tmp_path: Path, monkeypatch) -> None:
    """Plan §18 A-4 (verification agent gap) — the applied-path warning
    must also be exercised, not just the skipped path.

    Monkey-patch the threshold to 2 so the existing 3-entity fixture
    crosses the boundary and triggers ``dxf_prefilter:applied:...``.
    """
    ezdxf = pytest.importorskip("ezdxf")
    pytest.importorskip("matplotlib")
    import src.services.comparison.zone_render_service as zrs

    dxf_path = tmp_path / "small.dxf"
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.modelspace().add_line((10, 0), (10, 10))
    doc.modelspace().add_line((10, 10), (0, 10))
    doc.saveas(dxf_path)

    # Force the applied path on a tiny modelspace by lowering the threshold.
    monkeypatch.setattr(zrs, "_DXF_PREFILTER_THRESHOLD", 2)
    clear_render_index_cache()
    job = RenderJob(
        pair_uuid="pair-applied",
        zone_id="Z-1",
        request_id="r-applied",
        source_before=dxf_path,
        source_after=dxf_path,
        world_window=canonical_window_from_bbox([0, 0, 10, 10]),
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf_cache",
    )
    result = render_zone_pair(job)

    applied_warnings = [
        w for w in result.warnings if w.startswith("dxf_prefilter:applied")
    ]
    assert applied_warnings, (
        f"expected dxf_prefilter:applied warning, got {result.warnings!r}"
    )
    # The applied warning should report visible_entities=K/N where N is
    # the total entity count seen by the envelope filter.
    assert "visible_entities" in applied_warnings[0]


def test_dxf_prefilter_skipped_for_small_modelspace(tmp_path: Path) -> None:
    """When modelspace is below the skip threshold, the filter is bypassed
    and a ``dxf_prefilter:skipped`` warning is recorded.
    """
    ezdxf = pytest.importorskip("ezdxf")
    pytest.importorskip("matplotlib")
    dxf_path = tmp_path / "small.dxf"
    doc = ezdxf.new()
    # 3 entities — well below _DXF_PREFILTER_THRESHOLD (200).
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.modelspace().add_line((10, 0), (10, 10))
    doc.modelspace().add_line((10, 10), (0, 10))
    doc.saveas(dxf_path)

    clear_render_index_cache()
    job = RenderJob(
        pair_uuid="pair-small",
        zone_id="Z-1",
        request_id="r-small",
        source_before=dxf_path,
        source_after=dxf_path,
        world_window=canonical_window_from_bbox([0, 0, 10, 10]),
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf_cache",
        perf_event_root=tmp_path / "perf",
        perf_run_id="run-dxf-cache",
    )
    result = render_zone_pair(job)

    assert result.dxf_index_cache["lookup_count"] == 2
    assert result.dxf_index_cache["miss_count"] == 1
    assert result.dxf_index_cache["hit_count"] == 1
    assert result.dxf_index_cache["total_estimated_bytes"] > 0
    payload = result.to_dict()
    assert payload["dxf_index_cache_lookup_count"] == 2
    assert payload["dxf_index_cache_hit_count"] == 1
    perf_lines = [
        line
        for line in (tmp_path / "perf" / "perf_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    perf_event = json.loads(perf_lines[-1])
    assert perf_event["dxf_index_cache_lookup_count"] == 2
    assert perf_event["dxf_index_cache_miss_count"] == 1
    assert perf_event["dxf_index_cache_total_estimated_bytes"] > 0

    # The skip warning must appear at least once (once per side).
    skip_warnings = [w for w in result.warnings if w.startswith("dxf_prefilter:skipped")]
    assert skip_warnings, (
        f"expected dxf_prefilter:skipped warning, got {result.warnings!r}"
    )
    # And no "applied" warning when below the threshold.
    applied_warnings = [w for w in result.warnings if w.startswith("dxf_prefilter:applied")]
    assert not applied_warnings


def test_source_render_uses_side_specific_windows_for_reorigin_pair(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    pytest.importorskip("matplotlib")

    before_path = tmp_path / "before.dxf"
    after_path = tmp_path / "after.dxf"

    before_doc = ezdxf.new()
    before_msp = before_doc.modelspace()
    before_msp.add_line((0, 0), (10, 0))
    before_msp.add_line((10, 0), (10, 10))
    before_msp.add_text("B").set_placement((2, 4))
    before_doc.saveas(before_path)

    after_doc = ezdxf.new()
    after_msp = after_doc.modelspace()
    after_msp.add_line((1000, 0), (1010, 0))
    after_msp.add_line((1010, 0), (1010, 10))
    after_msp.add_text("A").set_placement((1002, 4))
    after_doc.saveas(after_path)

    result = render_zone_pair(
        RenderJob(
            pair_uuid="pair-reorigin",
            zone_id="Z-reorigin",
            request_id="r-reorigin",
            source_before=before_path,
            source_after=after_path,
            world_window=canonical_window_from_bbox([0, 0, 1010, 10]),
            before_world_window=canonical_window_from_bbox([0, 0, 10, 10], min_size=20),
            after_world_window=canonical_window_from_bbox([1000, 0, 1010, 10], min_size=20),
            cache_root=tmp_path / "cache",
            dxf_cache_dir=tmp_path / "dxf_cache",
            output_width=160,
            output_height=90,
        )
    )

    assert result.renderer_backend == "ezdxf-matplotlib-zone"
    assert result.render_lifecycle == "ready"
    assert result.reason_code == ""
    assert Path(result.before_image).exists()
    assert Path(result.after_image).exists()
    assert result.before_transform["min_x"] < 0
    assert result.after_transform["min_x"] > result.before_transform["min_x"] + 900
    assert render_cache_key(
        RenderJob(
            pair_uuid="pair-reorigin",
            zone_id="Z-reorigin",
            source_before=before_path,
            source_after=after_path,
            world_window=canonical_window_from_bbox([0, 0, 1010, 10]),
            cache_root=tmp_path / "cache",
            dxf_cache_dir=tmp_path / "dxf_cache",
            output_width=160,
            output_height=90,
        )
    ) != render_cache_key(
        RenderJob(
            pair_uuid="pair-reorigin",
            zone_id="Z-reorigin",
            source_before=before_path,
            source_after=after_path,
            world_window=canonical_window_from_bbox([0, 0, 1010, 10]),
            before_world_window=canonical_window_from_bbox([0, 0, 10, 10], min_size=20),
            after_world_window=canonical_window_from_bbox([1000, 0, 1010, 10], min_size=20),
            cache_root=tmp_path / "cache",
            dxf_cache_dir=tmp_path / "dxf_cache",
            output_width=160,
            output_height=90,
        )
    )


def test_render_result_elapsed_ms_defaults_to_zero_for_legacy_construction() -> None:
    """Backward compatibility — legacy callers that don't supply the new
    field must still produce a usable dataclass with elapsed_ms = 0.0.
    JSONL consumers that already expected the key see 0 instead of a
    KeyError.
    """
    from src.services.comparison.zone_render_service import RenderResult

    result = RenderResult(
        pair_uuid="x",
        zone_id="z",
        before_image="",
        after_image="",
        before_transform={},
        after_transform={},
        world_window={},
        renderer_backend="test",
        cache_key="k",
        cache_hit=False,
        visual_fidelity="cad_render",
        render_lifecycle="ready",
        warnings=[],
    )
    assert result.elapsed_ms == 0.0
    assert result.to_dict()["elapsed_ms"] == 0.0


# ---------------------------------------------------------------------------
# Plan §17 Phase B-1b (GPT Pro F3 follow-up) — PDF zone crops must use
# the PyMuPDF DisplayList cache when the source PDF is available and
# fall back to the legacy PIL full-page read otherwise. Each branch
# appends a marker warning so telemetry can distinguish them.
# ---------------------------------------------------------------------------


def _write_synthetic_pdf(path: Path, *, page_count: int = 1, page_size=(612, 792)) -> Path:
    """Create a small valid PDF using PyMuPDF.

    Local helper duplicated from test_pdf_display_list_cache to keep
    these zone_render_service tests self-contained.
    """
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    try:
        for i in range(max(1, int(page_count))):
            page = doc.new_page(width=float(page_size[0]), height=float(page_size[1]))
            page.insert_text((60, 90 + 20 * i), f"zone-page-{i}", fontsize=14)
            page.draw_rect(
                fitz.Rect(50, 50, page.rect.width - 50, page.rect.height - 50),
                color=(0, 0, 0),
            )
        doc.save(str(path))
    finally:
        doc.close()
    return path


def test_pdf_crop_uses_display_list_when_source_available(tmp_path: Path) -> None:
    """When ``RenderJob.source_before/after`` point at real PDFs, the
    crop helper must take the DisplayList path and emit the
    ``renderer:pdf-display-list-clip`` marker. Without this assertion
    a silent regression to the slow PIL path would be invisible.
    """
    Image = pytest.importorskip("PIL.Image")
    fitz = pytest.importorskip("fitz")

    # Real source PDFs.
    before_pdf = _write_synthetic_pdf(tmp_path / "before.pdf")
    after_pdf = _write_synthetic_pdf(tmp_path / "after.pdf")

    # Pre-rendered backgrounds matching the synthetic page rect (612x792)
    # at 144 DPI for plausibility. The crop helper only needs the
    # ``img_width`` / ``img_height`` to map back to PDF points.
    bg_w, bg_h = 1224, 1584  # 612*2, 792*2 = 144 DPI equivalent
    before_bg = tmp_path / "before.png"
    after_bg = tmp_path / "after.png"
    Image.new("RGB", (bg_w, bg_h), "white").save(before_bg)
    Image.new("RGB", (bg_w, bg_h), "white").save(after_bg)

    background_transform = {
        "coordinate_space": "image_pixels",
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": float(bg_w),
        "max_y": float(bg_h),
        "img_width": bg_w,
        "img_height": bg_h,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "page": 0,
        "dpi": 144,
    }

    # Clear any cached DisplayLists from earlier tests so this run
    # exercises the cold path on the production helper.
    from src.services.comparison import pdf_display_list_cache as _cache_mod

    _cache_mod._clear_cache()

    job = RenderJob(
        pair_uuid="pair-displaylist",
        zone_id="Z-DL-1",
        source_before=before_pdf,
        source_after=after_pdf,
        world_window=WorldWindow(200.0, 200.0, 800.0, 800.0),
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf",
        before_background_image=str(before_bg),
        after_background_image=str(after_bg),
        before_background_transform=background_transform,
        after_background_transform=background_transform,
    )

    result = render_zone_pair(job)

    assert result.render_lifecycle == "ready"
    assert result.visual_fidelity == "pdf_render"
    assert Path(result.before_image).exists()
    assert Path(result.after_image).exists()
    # The whole point of B-1b: the new fast path's marker must appear.
    assert "renderer:pdf-display-list-clip" in result.warnings, (
        f"expected DisplayList marker; warnings={result.warnings}"
    )
    # And the legacy slow-path marker must NOT appear when source PDFs
    # are usable. Otherwise we're paying both costs.
    assert "renderer:pdf-pil-fallback" not in result.warnings
    assert result.pdf_display_list_cache["render_count"] == 2
    assert result.pdf_display_list_cache["cache_lookup_count"] == 2
    assert result.pdf_display_list_cache["cache_miss_count"] == 2
    assert result.pdf_display_list_cache["cache_total_estimated_bytes"] > 0
    payload = result.to_dict()
    assert payload["pdf_display_list_render_count"] == 2
    assert payload["pdf_display_list_cache_miss_count"] == 2
    assert payload["pdf_pil_fallback_count"] == 0


def test_pdf_crop_falls_back_to_pil_when_source_missing(tmp_path: Path) -> None:
    """When the source PDF was moved/deleted after the background
    render (or never existed — synthetic tests, etc.), the crop must
    fall back to the PIL full-page path and emit
    ``renderer:pdf-pil-fallback`` so telemetry records the slow path.
    """
    Image = pytest.importorskip("PIL.Image")

    # Source PDFs that are MISSING (file paths point at non-existent
    # files; the background PNG below is what actually gets cropped).
    before_pdf = tmp_path / "missing-before.pdf"
    after_pdf = tmp_path / "missing-after.pdf"
    # Create stub files so the page-bbox check in render_zone_pair
    # passes — but the cache will fail on resolve.
    before_pdf.write_bytes(b"%PDF-1.4\n")
    after_pdf.write_bytes(b"%PDF-1.4\n")

    before_bg = tmp_path / "before.png"
    after_bg = tmp_path / "after.png"
    Image.new("RGB", (200, 120), "white").save(before_bg)
    Image.new("RGB", (200, 120), "white").save(after_bg)

    background_transform = {
        "coordinate_space": "image_pixels",
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": 200.0,
        "max_y": 120.0,
        "img_width": 200,
        "img_height": 120,
        "scale_x": 1.0,
        "scale_y": 1.0,
    }

    job = RenderJob(
        pair_uuid="pair-pil-fallback",
        zone_id="Z-PIL-1",
        source_before=before_pdf,
        source_after=after_pdf,
        world_window=WorldWindow(20.0, 20.0, 120.0, 80.0),
        cache_root=tmp_path / "cache",
        dxf_cache_dir=tmp_path / "dxf",
        before_background_image=str(before_bg),
        after_background_image=str(after_bg),
        before_background_transform=background_transform,
        after_background_transform=background_transform,
    )

    result = render_zone_pair(job)

    assert result.render_lifecycle == "ready"
    assert Path(result.before_image).exists()
    assert Path(result.after_image).exists()
    # Stub PDFs cannot be parsed by PyMuPDF -> caller hits the
    # exception path -> PIL fallback runs.
    assert "renderer:pdf-pil-fallback" in result.warnings, (
        f"expected PIL fallback marker; warnings={result.warnings}"
    )
    assert "renderer:pdf-display-list-clip" not in result.warnings
    assert result.reason_code == "pdf_pil_fallback"
    assert result.pdf_display_list_cache["render_count"] == 0
    assert result.pdf_display_list_cache["pil_fallback_count"] == 2
    assert result.to_dict()["pdf_pil_fallback_count"] == 2


def test_prefer_source_render_skips_background_crop_for_full_detail(tmp_path: Path) -> None:
    """② full-detail upgrade: with prefer_source_render=True, render_zone_pair
    bypasses the fast cad-background-image-crop (which drops TEXT/DIMENSION/
    INSERT/HATCH) even when backgrounds exist, and renders the zone from the
    source via the ezdxf Frontend instead — with a distinct cache key so the
    full render does not collide with the fast crop's cache entry."""
    ezdxf = pytest.importorskip("ezdxf")
    image_mod = pytest.importorskip("PIL.Image")
    from src.services.comparison.zone_render_service import render_cache_key

    for name in ("before.dxf", "after.dxf"):
        doc = ezdxf.new()
        msp = doc.modelspace()
        for i in range(6):
            msp.add_line((20 + i * 8, 20), (20 + i * 8, 80))
        msp.add_text("DIM 1234").set_placement((40, 50))
        doc.saveas(tmp_path / name)

    before_bg = tmp_path / "before.png"
    after_bg = tmp_path / "after.png"
    image_mod.new("RGB", (100, 100), "white").save(before_bg)
    image_mod.new("RGB", (100, 100), "white").save(after_bg)
    bgt = {
        "min_x": 0.0, "min_y": 0.0, "max_x": 100.0, "max_y": 100.0,
        "img_width": 100, "img_height": 100, "scale_x": 1.0, "scale_y": 1.0,
    }
    common = dict(
        pair_uuid="p", zone_id="Z",
        source_before=tmp_path / "before.dxf", source_after=tmp_path / "after.dxf",
        world_window=WorldWindow(20.0, 20.0, 80.0, 80.0),
        cache_root=tmp_path / "cache", dxf_cache_dir=tmp_path / "dxfcache",
        output_width=120, output_height=80,
        before_background_image=str(before_bg), after_background_image=str(after_bg),
        before_background_transform=bgt, after_background_transform=bgt,
    )

    # Default: backgrounds present -> fast crop.
    fast = render_zone_pair(RenderJob(**common))
    assert fast.renderer_backend == "cad-background-image-crop"

    # prefer_source_render -> bypass the crop, render from source (full detail).
    full = render_zone_pair(RenderJob(**common, prefer_source_render=True))
    assert full.renderer_backend == "ezdxf-matplotlib-zone"
    assert any(w.startswith("dxf_prefilter:") for w in full.warnings)

    # Distinct cache keys -> no collision between fast and full.
    assert render_cache_key(RenderJob(**common)) != render_cache_key(
        RenderJob(**common, prefer_source_render=True)
    )
