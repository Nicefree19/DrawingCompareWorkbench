# -*- coding: utf-8 -*-
"""Tests for GPU/tile viewer cache helpers."""

from __future__ import annotations

import json
import time
from pathlib import Path

from PIL import Image

from src.services.comparison.viewer_tile_cache import (
    ViewerTileCacheOptions,
    append_pair_to_tiles_manifest_jsonl,
    append_viewer_perf_event,
    file_signature,
    materialise_tiles_manifest_from_jsonl,
    merge_tiles_manifest,
    pair_tile_manifest_path,
    rect_from_overlay,
    tile_coord_for_rect,
    tiles_manifest_is_current,
    viewer_cache_key,
    viewport_rect_from_transform,
    visible_overlay_tile_items,
    visible_tile_model,
    visible_or_clustered_overlays,
    write_pair_tile_cache,
    write_pair_visible_tile_cache,
)


def test_pixel_bbox_maps_to_tile_coordinates() -> None:
    overlay = {"after_bbox_px": {"x": 600, "y": 700, "width": 120, "height": 80}}

    rect = rect_from_overlay(overlay)

    assert rect == {"x": 600.0, "y": 700.0, "width": 120.0, "height": 80.0}
    assert tile_coord_for_rect(rect, tile_size=512) == (1, 1)


def test_cache_key_changes_when_source_signature_changes(tmp_path: Path) -> None:
    a = tmp_path / "a.dxf"
    b = tmp_path / "b.dxf"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    options = ViewerTileCacheOptions(tile_size=512)

    key1 = viewer_cache_key(pair_uuid="pair", source_a=a, source_b=b, options=options)
    b.write_text("changed", encoding="utf-8")
    key2 = viewer_cache_key(pair_uuid="pair", source_a=a, source_b=b, options=options)

    assert key1 != key2


def test_cache_key_changes_when_rendered_background_signature_changes(tmp_path: Path) -> None:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    options = ViewerTileCacheOptions(tile_size=512)

    key1 = viewer_cache_key(
        pair_uuid="pair",
        source_a=a,
        source_b=b,
        options=options,
        rendered_background_signature="page-0-dpi-80",
    )
    key2 = viewer_cache_key(
        pair_uuid="pair",
        source_a=a,
        source_b=b,
        options=options,
        rendered_background_signature="page-1-dpi-80",
    )

    assert key1 != key2


def test_tile_file_signature_uses_shared_source_hash(tmp_path: Path) -> None:
    source = tmp_path / "tile-source.pdf"
    source.write_bytes(b"%PDF")

    signature = file_signature(source)

    assert signature["source_hash"]
    assert signature["schema_version"] == 1
    assert signature["size"] == 4


def test_visible_overlays_are_capped_or_clustered() -> None:
    overlays = [
        {
            "zone_id": f"C-{idx:03d}",
            "raw_change_count": idx,
            "after_bbox_px": {"x": idx * 10, "y": idx * 10, "width": 5, "height": 5},
        }
        for idx in range(100)
    ]

    clustered = visible_or_clustered_overlays(overlays, zoom=0.2, max_visible=10)
    capped = visible_or_clustered_overlays(overlays, zoom=2.0, max_visible=10)

    assert clustered["mode"] == "cluster"
    assert len(clustered["items"]) <= 10
    assert capped["mode"] == "overlay"
    assert len(capped["items"]) == 10


def test_write_pair_tile_cache_and_merge_manifest(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (1024, 1024), "white").save(before)
    Image.new("RGB", (1024, 1024), "white").save(after)
    overlays = [
        {
            "zone_id": "C-001",
            "raw_change_count": 5,
            "after_bbox_px": {"x": 600, "y": 100, "width": 120, "height": 80},
        }
    ]
    options = ViewerTileCacheOptions(tile_size=512, max_levels=2)

    manifest = write_pair_tile_cache(
        pair_uuid="S21-0001",
        before_image=str(before),
        after_image=str(after),
        overlays=overlays,
        tile_root=tmp_path / "viewer" / "tiles",
        overlay_tile_root=tmp_path / "viewer" / "overlay_tiles",
        options=options,
        cache_key="abc",
    )
    manifest_path = merge_tiles_manifest(tmp_path / "viewer", manifest)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["status"] == "tile_ready"
    assert manifest["tile_count"] > 0
    assert manifest["overlay_tile_count"] == 1
    assert pair_tile_manifest_path(tmp_path / "viewer" / "tiles", "S21-0001").exists()
    assert manifest["tile_root"] == str((tmp_path / "viewer" / "tiles").resolve())
    assert manifest["overlay_tile_root"] == str((tmp_path / "viewer" / "overlay_tiles").resolve())
    assert manifest["tile_pyramid_ms"] >= 0
    assert manifest["overlay_tile_ms"] >= 0
    assert manifest["tile_cache_write_ms"] >= manifest["overlay_tile_ms"]
    assert manifest["tile_payload_bytes"] > 0
    assert manifest["overlay_tile_payload_bytes"] > 0
    assert manifest["cache_total_estimated_bytes"] == (
        manifest["tile_payload_bytes"] + manifest["overlay_tile_payload_bytes"]
    )
    assert manifest["cache_byte_limit"] == options.normalized().viewer_memory_budget_mb * 1024 * 1024
    assert manifest["eviction_count"] == 0
    assert payload["pair_count"] == 1
    assert payload["pairs"]["S21-0001"]["cache_total_estimated_bytes"] == manifest["cache_total_estimated_bytes"]
    assert tiles_manifest_is_current(manifest_path, "S21-0001", "abc")


def test_visible_tile_model_uses_viewport_and_prefetch(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (2048, 2048), "white").save(before)
    Image.new("RGB", (2048, 2048), "white").save(after)
    manifest = write_pair_tile_cache(
        pair_uuid="S21-0002",
        before_image=str(before),
        after_image=str(after),
        overlays=[],
        tile_root=tmp_path / "viewer" / "tiles",
        overlay_tile_root=tmp_path / "viewer" / "overlay_tiles",
        options=ViewerTileCacheOptions(tile_size=512, max_levels=2),
        cache_key="abc",
    )
    viewport = viewport_rect_from_transform(zoom=1.0, pan_x=-512, pan_y=-512, viewport_width=512, viewport_height=512)

    model = visible_tile_model(
        pair_manifest=manifest,
        side="after",
        viewer_root=tmp_path / "viewer",
        viewport_rect=viewport,
        zoom=1.0,
        prefetch_radius=0,
    )

    assert model["status"] == "tile_ready"
    assert model["level"] == 0
    assert model["missing_tile_count"] == 0
    assert [tile["tileX"] for tile in model["tiles"]] == [1]
    assert [tile["tileY"] for tile in model["tiles"]] == [1]


def test_visible_tile_model_honors_manifest_cache_root(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (1024, 1024), "white").save(before)
    Image.new("RGB", (1024, 1024), "white").save(after)
    cache_root = tmp_path / "persistent_cache"
    manifest = write_pair_tile_cache(
        pair_uuid="S21-0003",
        before_image=str(before),
        after_image=str(after),
        overlays=[],
        tile_root=cache_root / "tiles",
        overlay_tile_root=cache_root / "overlay_tiles",
        options=ViewerTileCacheOptions(tile_size=512, max_levels=1),
        cache_key="abc",
    )
    viewport = viewport_rect_from_transform(zoom=1.0, pan_x=0, pan_y=0, viewport_width=512, viewport_height=512)

    model = visible_tile_model(
        pair_manifest=manifest,
        side="after",
        viewer_root=tmp_path / "viewer_without_tiles",
        viewport_rect=viewport,
        zoom=1.0,
        prefetch_radius=0,
    )

    assert model["status"] == "tile_ready"
    assert model["tiles"][0]["source"].startswith((cache_root / "tiles").resolve().as_uri())


def test_write_visible_pair_tile_cache_materializes_only_visible_window(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (4096, 4096), "white").save(before)
    Image.new("RGB", (4096, 4096), "white").save(after)
    options = ViewerTileCacheOptions(tile_size=512, max_levels=1)
    viewport = {"x": 1024.0, "y": 1024.0, "width": 512.0, "height": 512.0}

    manifest = write_pair_visible_tile_cache(
        pair_uuid="P4B-visible",
        before_image=str(before),
        after_image=str(after),
        overlays=[],
        tile_root=tmp_path / "viewer" / "tiles",
        overlay_tile_root=tmp_path / "viewer" / "overlay_tiles",
        options=options,
        viewport_rect=viewport,
        zoom=1.0,
        prefetch_radius=0,
        cache_key="same-source",
    )

    assert manifest["status"] == "tile_ready"
    assert manifest["generation_mode"] == "visible_first"
    assert manifest["pyramid_complete"] is False
    assert manifest["tile_count"] == 2
    assert manifest["materialized_tile_count"] == 2
    assert manifest["planned_tile_count"] == 128
    assert manifest["omitted_tile_count"] == 126
    assert manifest["deferred_lod_tiles"] is True
    assert manifest["tile_payload_bytes"] > 0
    assert manifest["overlay_tile_payload_bytes"] == 0
    assert manifest["cache_total_estimated_bytes"] == manifest["tile_payload_bytes"]
    assert manifest["cache_byte_limit"] == options.normalized().viewer_memory_budget_mb * 1024 * 1024
    assert manifest["eviction_count"] == 0
    assert pair_tile_manifest_path(tmp_path / "viewer" / "tiles", "P4B-visible").exists()
    assert (tmp_path / "viewer" / "tiles" / "P4B-visible" / "after" / "0" / "2_2.png").exists()
    assert not (tmp_path / "viewer" / "tiles" / "P4B-visible" / "after" / "0" / "0_0.png").exists()
    manifest_path = merge_tiles_manifest(tmp_path / "viewer", manifest)
    assert tiles_manifest_is_current(manifest_path, "P4B-visible", "same-source")
    assert not tiles_manifest_is_current(manifest_path, "P4B-visible", "same-source", require_complete=True)

    visible = visible_tile_model(
        pair_manifest=manifest,
        side="after",
        viewer_root=tmp_path / "viewer",
        viewport_rect=viewport,
        zoom=1.0,
        prefetch_radius=0,
    )
    outside = visible_tile_model(
        pair_manifest=manifest,
        side="after",
        viewer_root=tmp_path / "viewer",
        viewport_rect={"x": 0.0, "y": 0.0, "width": 512.0, "height": 512.0},
        zoom=1.0,
        prefetch_radius=0,
    )

    assert visible["status"] == "tile_ready"
    assert visible["missing_tile_count"] == 0
    assert [(tile["tileX"], tile["tileY"]) for tile in visible["tiles"]] == [(2, 2)]
    assert outside["status"] == "tile_pending"
    assert outside["missing_tile_count"] == 1
    assert outside["tiles"] == []


def test_visible_pair_tile_cache_accumulates_windows_under_same_cache_key(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (4096, 4096), "white").save(before)
    Image.new("RGB", (4096, 4096), "white").save(after)
    options = ViewerTileCacheOptions(tile_size=512, max_levels=1)
    common = {
        "pair_uuid": "P4B-pan",
        "before_image": str(before),
        "after_image": str(after),
        "overlays": [],
        "tile_root": tmp_path / "viewer" / "tiles",
        "overlay_tile_root": tmp_path / "viewer" / "overlay_tiles",
        "options": options,
        "zoom": 1.0,
        "prefetch_radius": 0,
        "cache_key": "same-source",
    }

    first = write_pair_visible_tile_cache(
        **common,
        viewport_rect={"x": 0.0, "y": 0.0, "width": 512.0, "height": 512.0},
    )
    second = write_pair_visible_tile_cache(
        **common,
        viewport_rect={"x": 1536.0, "y": 1536.0, "width": 512.0, "height": 512.0},
    )

    assert first["tile_count"] == 2
    assert second["tile_count"] == 4
    assert second["tile_payload_bytes"] >= first["tile_payload_bytes"]
    assert second["cache_total_estimated_bytes"] >= first["cache_total_estimated_bytes"]
    assert second["eviction_count"] == 0
    assert second["planned_tile_count"] == 128
    assert len(second["visible_tile_windows"]) == 4
    assert (tmp_path / "viewer" / "tiles" / "P4B-pan" / "after" / "0" / "0_0.png").exists()
    assert (tmp_path / "viewer" / "tiles" / "P4B-pan" / "after" / "0" / "3_3.png").exists()

    first_window = visible_tile_model(
        pair_manifest=second,
        side="after",
        viewer_root=tmp_path / "viewer",
        viewport_rect={"x": 0.0, "y": 0.0, "width": 512.0, "height": 512.0},
        zoom=1.0,
        prefetch_radius=0,
    )
    second_window = visible_tile_model(
        pair_manifest=second,
        side="after",
        viewer_root=tmp_path / "viewer",
        viewport_rect={"x": 1536.0, "y": 1536.0, "width": 512.0, "height": 512.0},
        zoom=1.0,
        prefetch_radius=0,
    )
    unmaterialized_window = visible_tile_model(
        pair_manifest=second,
        side="after",
        viewer_root=tmp_path / "viewer",
        viewport_rect={"x": 512.0, "y": 512.0, "width": 512.0, "height": 512.0},
        zoom=1.0,
        prefetch_radius=0,
    )

    assert first_window["status"] == "tile_ready"
    assert second_window["status"] == "tile_ready"
    assert unmaterialized_window["status"] == "tile_pending"
    assert unmaterialized_window["missing_tile_count"] == 1


def test_tile_cache_eviction_deletes_old_pair_payload_and_filters_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (128, 128), "white").save(before)
    Image.new("RGB", (128, 128), "white").save(after)
    viewer_root = tmp_path / "viewer"
    tile_root = viewer_root / "tiles"
    overlay_tile_root = viewer_root / "overlay_tiles"
    options = ViewerTileCacheOptions(tile_size=128, max_levels=1)

    first = write_pair_tile_cache(
        pair_uuid="evict-old",
        before_image=str(before),
        after_image=str(after),
        overlays=[],
        tile_root=tile_root,
        overlay_tile_root=overlay_tile_root,
        options=options,
        cache_key="key-old",
    )
    append_pair_to_tiles_manifest_jsonl(viewer_root, first)
    manifest_path = merge_tiles_manifest(viewer_root, first)
    assert tiles_manifest_is_current(manifest_path, "evict-old", "key-old")

    limit_bytes = int(first["cache_total_estimated_bytes"]) + 64
    monkeypatch.setenv("DRAWING_COMPARE_TILE_CACHE_MB", f"{limit_bytes / (1024 * 1024):.9f}")
    second = write_pair_tile_cache(
        pair_uuid="evict-new",
        before_image=str(before),
        after_image=str(after),
        overlays=[],
        tile_root=tile_root,
        overlay_tile_root=overlay_tile_root,
        options=options,
        cache_key="key-new",
    )
    append_pair_to_tiles_manifest_jsonl(viewer_root, second)

    assert second["eviction_count"] == 1
    assert second["evicted_pair_count"] == 1
    assert second["evicted_estimated_bytes"] >= first["cache_total_estimated_bytes"]
    assert second["eviction_reason"] == "byte_limit"
    assert second["cache_retained_estimated_bytes"] <= second["cache_byte_limit"]
    assert "evict-old" in second["evicted_pairs"]
    assert not (tile_root / "evict-old").exists()
    assert not (overlay_tile_root / "evict-old").exists()
    assert (tile_root / "evict-new" / "tile_manifest.json").exists()
    assert not tiles_manifest_is_current(manifest_path, "evict-old", "key-old")

    merged_path = merge_tiles_manifest(viewer_root, second)
    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    assert set(merged["pairs"]) == {"evict-new"}

    materialized = json.loads(materialise_tiles_manifest_from_jsonl(viewer_root).read_text(encoding="utf-8"))
    assert set(materialized["pairs"]) == {"evict-new"}


def test_tile_cache_eviction_keeps_recently_accessed_hot_pair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (128, 128), "white").save(before)
    Image.new("RGB", (128, 128), "white").save(after)
    viewer_root = tmp_path / "viewer"
    tile_root = viewer_root / "tiles"
    overlay_tile_root = viewer_root / "overlay_tiles"
    options = ViewerTileCacheOptions(tile_size=128, max_levels=1)

    hot = write_pair_tile_cache(
        pair_uuid="hot",
        before_image=str(before),
        after_image=str(after),
        overlays=[],
        tile_root=tile_root,
        overlay_tile_root=overlay_tile_root,
        options=options,
        cache_key="hot-key",
    )
    manifest_path = merge_tiles_manifest(viewer_root, hot)
    time.sleep(0.01)
    cold = write_pair_tile_cache(
        pair_uuid="cold",
        before_image=str(before),
        after_image=str(after),
        overlays=[],
        tile_root=tile_root,
        overlay_tile_root=overlay_tile_root,
        options=options,
        cache_key="cold-key",
    )
    manifest_path = merge_tiles_manifest(viewer_root, cold)
    time.sleep(0.01)
    assert tiles_manifest_is_current(manifest_path, "hot", "hot-key")

    limit_bytes = int(hot["cache_total_estimated_bytes"]) + int(cold["cache_total_estimated_bytes"]) + 64
    monkeypatch.setenv("DRAWING_COMPARE_TILE_CACHE_MB", f"{limit_bytes / (1024 * 1024):.9f}")
    newest = write_pair_tile_cache(
        pair_uuid="newest",
        before_image=str(before),
        after_image=str(after),
        overlays=[],
        tile_root=tile_root,
        overlay_tile_root=overlay_tile_root,
        options=options,
        cache_key="newest-key",
    )
    merge_tiles_manifest(viewer_root, newest)

    assert (tile_root / "hot").exists()
    assert not (tile_root / "cold").exists()
    assert (tile_root / "newest").exists()
    assert "cold" in newest["evicted_pairs"]


def test_visible_pair_tile_cache_filters_overlay_tiles_to_viewport(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (1024, 1024), "white").save(before)
    Image.new("RGB", (1024, 1024), "white").save(after)
    overlays = [
        {"zone_id": "inside", "after_bbox_px": {"x": 10, "y": 10, "width": 20, "height": 20}},
        {"zone_id": "outside", "after_bbox_px": {"x": 800, "y": 800, "width": 20, "height": 20}},
    ]

    manifest = write_pair_visible_tile_cache(
        pair_uuid="P4B-overlay",
        before_image=str(before),
        after_image=str(after),
        overlays=overlays,
        tile_root=tmp_path / "viewer" / "tiles",
        overlay_tile_root=tmp_path / "viewer" / "overlay_tiles",
        options=ViewerTileCacheOptions(tile_size=512, max_levels=1),
        viewport_rect={"x": 0.0, "y": 0.0, "width": 512.0, "height": 512.0},
        zoom=1.0,
        prefetch_radius=0,
        cache_key="abc",
    )
    payload = json.loads(
        (tmp_path / "viewer" / "overlay_tiles" / "P4B-overlay" / "0" / "0_0.json").read_text(encoding="utf-8")
    )

    assert manifest["overlay_count"] == 2
    assert manifest["materialized_overlay_count"] == 1
    assert manifest["overlay_omitted_count"] == 1
    assert manifest["outside_viewport_overlay_count"] == 1
    assert [item["zone_id"] for item in payload["overlays"]] == ["inside"]


def test_visible_overlay_tile_items_limits_to_visible_tiles_and_keeps_selected(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (2048, 2048), "white").save(before)
    Image.new("RGB", (2048, 2048), "white").save(after)
    selected = {"zone_id": "outside", "raw_change_count": 99, "after_bbox_px": {"x": 1800, "y": 1800, "width": 20, "height": 20}}
    overlays = [
        {"zone_id": f"z-{idx}", "raw_change_count": idx, "after_bbox_px": {"x": 50 + idx, "y": 60, "width": 10, "height": 10}}
        for idx in range(60)
    ] + [selected]
    manifest = write_pair_tile_cache(
        pair_uuid="S21-0004",
        before_image=str(before),
        after_image=str(after),
        overlays=overlays,
        tile_root=tmp_path / "viewer" / "tiles",
        overlay_tile_root=tmp_path / "viewer" / "overlay_tiles",
        options=ViewerTileCacheOptions(tile_size=512, max_levels=1, max_visible_overlays=20),
        cache_key="abc",
    )
    viewport = viewport_rect_from_transform(zoom=1.0, pan_x=0, pan_y=0, viewport_width=512, viewport_height=512)

    model = visible_overlay_tile_items(
        pair_manifest=manifest,
        viewer_root=tmp_path / "viewer",
        viewport_rect=viewport,
        zoom=2.0,
        max_visible=20,
        selected_overlay=selected,
        prefetch_radius=0,
    )

    zone_ids = {item["zone_id"] for item in model["items"]}
    assert model["status"] == "overlay_tiles"
    assert len(model["items"]) <= 20
    assert "outside" in zone_ids


def test_write_overlay_tiles_caps_materialized_records_per_tile(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    Image.new("RGB", (512, 512), "white").save(before)
    Image.new("RGB", (512, 512), "white").save(after)
    overlays = [
        {
            "zone_id": f"z-{idx}",
            "raw_change_count": idx,
            "after_bbox_px": {"x": 10, "y": 10, "width": 5, "height": 5},
        }
        for idx in range(1000)
    ]

    manifest = write_pair_tile_cache(
        pair_uuid="P4-100K-SHAPE",
        before_image=str(before),
        after_image=str(after),
        overlays=overlays,
        tile_root=tmp_path / "viewer" / "tiles",
        overlay_tile_root=tmp_path / "viewer" / "overlay_tiles",
        options=ViewerTileCacheOptions(tile_size=512, max_levels=1, max_visible_overlays=20),
        cache_key="abc",
    )
    overlay_tile = tmp_path / "viewer" / "overlay_tiles" / "P4-100K-SHAPE" / "0" / "0_0.json"
    payload = json.loads(overlay_tile.read_text(encoding="utf-8"))

    assert manifest["overlay_count"] == 1000
    assert manifest["materialized_overlay_count"] == 25
    assert manifest["overlay_omitted_count"] == 975
    assert len(payload["overlays"]) == 25


def test_viewer_perf_event_is_written(tmp_path: Path) -> None:
    path = append_viewer_perf_event(
        tmp_path / "viewer",
        "focus",
        pair_uuid="pair",
        render_ms=12.5,
        tile_payload_bytes=100,
        overlay_tile_payload_bytes=25,
        cache_total_estimated_bytes=125,
        cache_byte_limit=512,
        eviction_count=0,
    )
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    pointer = json.loads((tmp_path / "viewer" / "viewer_perf.json").read_text(encoding="utf-8"))

    assert path.name == "viewer_perf.jsonl"
    assert events[0]["event"] == "focus"
    assert events[0]["render_ms"] == 12.5
    assert events[0]["cache_total_estimated_bytes"] == 125
    assert events[0]["cache_byte_limit"] == 512
    assert events[0]["eviction_count"] == 0
    assert pointer["schema_version"] == 2
    assert pointer["storage"] == "jsonl"
    assert pointer["last_event"] == "focus"


def test_viewer_perf_event_append_does_not_rewrite_event_history_json(tmp_path: Path) -> None:
    viewer_root = tmp_path / "viewer"

    for idx in range(3):
        append_viewer_perf_event(viewer_root, "viewport_model", pair_uuid="pair", tile_count=idx)

    pointer = json.loads((viewer_root / "viewer_perf.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (viewer_root / "viewer_perf.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert "events" not in pointer
    assert len(events) == 3
    assert [event["tile_count"] for event in events] == [0, 1, 2]
