# -*- coding: utf-8 -*-
"""Tests for GPU/tile viewer cache helpers."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from src.services.comparison.viewer_tile_cache import (
    ViewerTileCacheOptions,
    append_viewer_perf_event,
    merge_tiles_manifest,
    rect_from_overlay,
    tile_coord_for_rect,
    tiles_manifest_is_current,
    viewer_cache_key,
    viewport_rect_from_transform,
    visible_overlay_tile_items,
    visible_tile_model,
    visible_or_clustered_overlays,
    write_pair_tile_cache,
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
    assert manifest["tile_root"] == str((tmp_path / "viewer" / "tiles").resolve())
    assert manifest["overlay_tile_root"] == str((tmp_path / "viewer" / "overlay_tiles").resolve())
    assert payload["pair_count"] == 1
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


def test_viewer_perf_event_is_written(tmp_path: Path) -> None:
    path = append_viewer_perf_event(tmp_path / "viewer", "focus", pair_uuid="pair", render_ms=12.5)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["event_count"] == 1
    assert payload["events"][0]["event"] == "focus"
    assert payload["events"][0]["render_ms"] == 12.5
