"""Tile/LOD helpers for the drawing comparison viewer.

This module is intentionally display-only.  CAD comparison and change-zone
generation remain the source of truth; these helpers only make large viewer
payloads cheap to open, pan, zoom, and focus.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

# Plan §19 A-2 (Agent A finding A1) — per-process lock guarding the
# tile-manifest JSONL writer. On POSIX ``O_APPEND`` writes up to
# PIPE_BUF (4 KiB) are kernel-atomic, but Python on Windows opens a
# fresh file descriptor per call and the kernel does NOT guarantee
# atomic positioning across distinct FDs, so 4-thread benchmarks
# observed dropped/truncated records. The module-level lock makes
# same-process writes safe; cross-process safety (e.g. multiple
# Python workers on a shared SMB share) still requires portalocker
# — flagged as Plan §18 Phase B-1.
_TILES_MANIFEST_WRITE_LOCK = threading.Lock()
from typing import Any, Iterable, Iterator, Optional, Sequence


@dataclass(frozen=True)
class ViewerTileCacheOptions:
    tile_size: int = 512
    max_edge_overview: int = 2200
    max_visible_overlays: int = 500
    viewer_memory_budget_mb: int = 512
    max_levels: int = 4

    def normalized(self) -> "ViewerTileCacheOptions":
        return ViewerTileCacheOptions(
            tile_size=max(128, int(self.tile_size)),
            max_edge_overview=max(800, int(self.max_edge_overview)),
            max_visible_overlays=max(25, int(self.max_visible_overlays)),
            viewer_memory_budget_mb=max(128, int(self.viewer_memory_budget_mb)),
            max_levels=max(1, int(self.max_levels)),
        )


def file_signature(path: Optional[Path]) -> dict[str, Any]:
    if not path:
        return {"path": "", "size": 0, "mtime_ns": 0}
    try:
        stat = Path(path).stat()
        return {"path": str(Path(path).resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        return {"path": str(path), "size": 0, "mtime_ns": 0}


def viewer_cache_key(
    *,
    pair_uuid: str,
    source_a: Optional[Path],
    source_b: Optional[Path],
    options: ViewerTileCacheOptions,
    transform_version: str = "viewer-v2",
) -> str:
    payload = {
        "pair_uuid": pair_uuid,
        "source_a": file_signature(source_a),
        "source_b": file_signature(source_b),
        "options": options.normalized().__dict__,
        "transform_version": transform_version,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def rect_from_overlay(overlay: dict[str, Any], *, before: bool = False) -> Optional[dict[str, float]]:
    value = overlay.get("before_bbox_px") if before else overlay.get("after_bbox_px")
    if isinstance(value, dict):
        try:
            return {
                "x": float(value.get("x", 0.0)),
                "y": float(value.get("y", 0.0)),
                "width": max(1.0, float(value.get("width", 0.0))),
                "height": max(1.0, float(value.get("height", 0.0))),
            }
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            left = float(value[0])
            top = float(value[1])
            right = float(value[2])
            bottom = float(value[3])
            return {"x": left, "y": top, "width": max(1.0, right - left), "height": max(1.0, bottom - top)}
        except (TypeError, ValueError):
            return None
    return None


def tile_coord_for_rect(rect: dict[str, float], *, tile_size: int, scale: float = 1.0) -> tuple[int, int]:
    cx = (float(rect["x"]) + float(rect["width"]) / 2.0) * scale
    cy = (float(rect["y"]) + float(rect["height"]) / 2.0) * scale
    return max(0, int(cx // tile_size)), max(0, int(cy // tile_size))


def viewport_rect_from_transform(
    *,
    zoom: float,
    pan_x: float,
    pan_y: float,
    viewport_width: float,
    viewport_height: float,
) -> dict[str, float]:
    """Return the visible scene rectangle in level-0 image coordinates."""

    safe_zoom = max(0.0001, float(zoom or 1.0))
    return {
        "x": -float(pan_x or 0.0) / safe_zoom,
        "y": -float(pan_y or 0.0) / safe_zoom,
        "width": max(1.0, float(viewport_width or 1.0) / safe_zoom),
        "height": max(1.0, float(viewport_height or 1.0) / safe_zoom),
    }


def _tile_root_from_manifest(pair_manifest: dict[str, Any], viewer_root: Path, field: str, default_name: str) -> Path:
    value = pair_manifest.get(field)
    if value:
        return Path(str(value))
    return Path(viewer_root) / default_name


def _tile_range(
    viewport_rect: dict[str, float],
    *,
    tile_size: int,
    scale: float = 1.0,
    cols: Optional[int] = None,
    rows: Optional[int] = None,
    prefetch_radius: int = 1,
) -> tuple[int, int, int, int]:
    safe_scale = max(0.0001, float(scale or 1.0))
    left = max(0, int(math.floor(float(viewport_rect.get("x", 0.0)) * safe_scale / tile_size)) - prefetch_radius)
    top = max(0, int(math.floor(float(viewport_rect.get("y", 0.0)) * safe_scale / tile_size)) - prefetch_radius)
    right = (
        int(
            math.floor(
                max(0.0, float(viewport_rect.get("x", 0.0)) + float(viewport_rect.get("width", 0.0)) - 0.001)
                * safe_scale
                / tile_size
            )
        )
        + prefetch_radius
    )
    bottom = (
        int(
            math.floor(
                max(0.0, float(viewport_rect.get("y", 0.0)) + float(viewport_rect.get("height", 0.0)) - 0.001)
                * safe_scale
                / tile_size
            )
        )
        + prefetch_radius
    )
    if cols is not None:
        right = min(max(0, int(cols) - 1), right)
    if rows is not None:
        bottom = min(max(0, int(rows) - 1), bottom)
    return left, top, max(left, right), max(top, bottom)


def visible_tile_model(
    *,
    pair_manifest: dict[str, Any],
    side: str,
    viewer_root: Path,
    viewport_rect: dict[str, float],
    zoom: float,
    prefetch_radius: int = 1,
) -> dict[str, Any]:
    """Build a QML-friendly list of visible tile images for one side."""

    tile_size = max(128, int(pair_manifest.get("tile_size") or 512))
    side_manifest = (pair_manifest.get("sides") or {}).get(side) if isinstance(pair_manifest, dict) else None
    if not isinstance(side_manifest, dict):
        return {"tiles": [], "level": -1, "status": "missing_side"}
    levels = [level for level in side_manifest.get("levels", []) if isinstance(level, dict)]
    if not levels:
        return {"tiles": [], "level": -1, "status": side_manifest.get("status") or "missing_tiles"}

    selected = _select_level(levels, zoom)
    scale = max(0.0001, float(selected.get("scale") or 1.0))
    level = int(selected.get("level") or 0)
    cols = int(selected.get("cols") or 0)
    rows = int(selected.get("rows") or 0)
    if cols <= 0 or rows <= 0:
        return {"tiles": [], "level": level, "status": "empty_level"}

    left, top, right, bottom = _tile_range(
        viewport_rect,
        tile_size=tile_size,
        scale=scale,
        cols=cols,
        rows=rows,
        prefetch_radius=prefetch_radius,
    )

    pair_uuid = _safe_name(pair_manifest.get("pair_uuid") or "")
    tiles: list[dict[str, Any]] = []
    tile_root = _tile_root_from_manifest(pair_manifest, viewer_root, "tile_root", "tiles")
    base = tile_root / pair_uuid / side / str(level)
    level_width = float(selected.get("width") or 0)
    level_height = float(selected.get("height") or 0)
    for tile_y in range(top, bottom + 1):
        for tile_x in range(left, right + 1):
            path = base / f"{tile_x}_{tile_y}.png"
            if not path.exists():
                continue
            level_x = tile_x * tile_size
            level_y = tile_y * tile_size
            level_w = min(tile_size, max(1.0, level_width - level_x))
            level_h = min(tile_size, max(1.0, level_height - level_y))
            tiles.append(
                {
                    "source": path.resolve().as_uri(),
                    "x": level_x / scale,
                    "y": level_y / scale,
                    "width": level_w / scale,
                    "height": level_h / scale,
                    "tileX": tile_x,
                    "tileY": tile_y,
                    "level": level,
                }
            )
    return {"tiles": tiles, "level": level, "status": "tile_ready" if tiles else "tile_pending"}


def visible_overlay_tile_items(
    *,
    pair_manifest: dict[str, Any],
    viewer_root: Path,
    viewport_rect: dict[str, float],
    zoom: float,
    max_visible: int = 500,
    selected_overlay: Optional[dict[str, Any]] = None,
    prefetch_radius: int = 1,
) -> dict[str, Any]:
    """Load only overlay records that belong to the visible level-0 tile window."""

    if not isinstance(pair_manifest, dict):
        return {"mode": "missing_tiles", "items": [], "omitted": 0, "status": "missing_manifest"}
    pair_uuid = _safe_name(pair_manifest.get("pair_uuid") or "")
    if not pair_uuid:
        return {"mode": "missing_tiles", "items": [], "omitted": 0, "status": "missing_pair"}
    tile_size = max(128, int(pair_manifest.get("tile_size") or 512))
    side_manifest = (pair_manifest.get("sides") or {}).get("after") if isinstance(pair_manifest.get("sides"), dict) else {}
    levels = [level for level in (side_manifest or {}).get("levels", []) if isinstance(level, dict)]
    level0 = next((level for level in levels if int(level.get("level") or 0) == 0), levels[0] if levels else {})
    cols = int(level0.get("cols") or 0) or None
    rows = int(level0.get("rows") or 0) or None
    left, top, right, bottom = _tile_range(
        viewport_rect,
        tile_size=tile_size,
        cols=cols,
        rows=rows,
        prefetch_radius=prefetch_radius,
    )
    overlay_root = _tile_root_from_manifest(pair_manifest, viewer_root, "overlay_tile_root", "overlay_tiles")
    collected: dict[str, dict[str, Any]] = {}
    for tile_y in range(top, bottom + 1):
        for tile_x in range(left, right + 1):
            payload = _read_json(overlay_root / pair_uuid / "0" / f"{tile_x}_{tile_y}.json")
            for overlay in payload.get("overlays", []):
                if not isinstance(overlay, dict):
                    continue
                key = str(overlay.get("zone_id") or overlay.get("id") or f"{tile_x}:{tile_y}:{len(collected)}")
                collected[key] = overlay

    selected_key = str((selected_overlay or {}).get("zone_id") or (selected_overlay or {}).get("id") or "")
    lod = visible_or_clustered_overlays(
        list(collected.values()),
        viewport_rect=viewport_rect,
        zoom=zoom,
        max_visible=max_visible,
    )
    items = [item for item in lod.get("items", []) if isinstance(item, dict)]
    if selected_overlay and selected_key and not any(str(item.get("zone_id") or item.get("id") or "") == selected_key for item in items):
        items = items[: max(0, max_visible - 1)] + [selected_overlay]
    lod["items"] = items[:max_visible]
    lod["status"] = "overlay_tiles" if collected else "overlay_tiles_empty"
    lod["tile_window"] = [left, top, right, bottom]
    return lod


def visible_or_clustered_overlays(
    overlays: Sequence[dict[str, Any]],
    *,
    viewport_rect: Optional[dict[str, float]] = None,
    zoom: float = 1.0,
    max_visible: int = 500,
) -> dict[str, Any]:
    candidates = [overlay for overlay in overlays if isinstance(overlay, dict)]
    if viewport_rect:
        candidates = [
            overlay
            for overlay in candidates
            if _intersects(rect_from_overlay(overlay) or _normalized_rect(overlay), viewport_rect)
        ]
    candidates.sort(key=lambda item: (-_number(item.get("raw_change_count")), str(item.get("zone_id") or "")))
    if len(candidates) <= max_visible:
        return {"mode": "overlay", "items": candidates, "omitted": 0}
    if zoom < 0.6:
        clusters = _cluster_overlays(candidates, max_visible=max_visible)
        return {"mode": "cluster", "items": clusters, "omitted": len(candidates) - len(clusters)}
    return {"mode": "overlay", "items": candidates[:max_visible], "omitted": len(candidates) - max_visible}


def append_viewer_perf_event(viewer_root: Path, event: str, **metrics: Any) -> Path:
    """Append a compact performance event to viewer/viewer_perf.json."""

    path = Path(viewer_root) / "viewer_perf.json"
    payload = _read_json(path)
    events = payload.get("events", [])
    if not isinstance(events, list):
        events = []
    events.append(
        {
            "timestamp": datetime.now().isoformat(),
            "event": event,
            **metrics,
        }
    )
    if len(events) > 1000:
        events = events[-1000:]
    payload.update(
        {
            "schema_version": 1,
            "event_count": len(events),
            "events": events,
        }
    )
    _write_json(path, payload)
    return path


def write_pair_tile_cache(
    *,
    pair_uuid: str,
    before_image: str,
    after_image: str,
    overlays: Sequence[dict[str, Any]],
    tile_root: Path,
    overlay_tile_root: Path,
    options: ViewerTileCacheOptions,
    cache_key: str = "",
) -> dict[str, Any]:
    opts = options.normalized()
    tile_root.mkdir(parents=True, exist_ok=True)
    overlay_tile_root.mkdir(parents=True, exist_ok=True)
    pair_dir = tile_root / _safe_name(pair_uuid)
    pair_dir.mkdir(parents=True, exist_ok=True)
    overlay_dir = overlay_tile_root / _safe_name(pair_uuid)
    overlay_dir.mkdir(parents=True, exist_ok=True)

    sides: dict[str, Any] = {}
    total_tiles = 0
    for side, image_path in (("before", before_image), ("after", after_image)):
        if not image_path or not Path(image_path).exists():
            sides[side] = {"image": image_path or "", "levels": [], "tile_count": 0, "status": "missing_image"}
            continue
        side_manifest = _write_image_pyramid(Path(image_path), pair_dir / side, opts)
        sides[side] = side_manifest
        total_tiles += int(side_manifest.get("tile_count", 0))

    overlay_tile_count = _write_overlay_tiles(overlays, overlay_dir, opts)
    manifest = {
        "schema_version": 1,
        "pair_uuid": pair_uuid,
        "cache_key": cache_key,
        "tile_root": str(Path(tile_root).resolve()),
        "overlay_tile_root": str(Path(overlay_tile_root).resolve()),
        "tile_size": opts.tile_size,
        "max_visible_overlays": opts.max_visible_overlays,
        "viewer_memory_budget_mb": opts.viewer_memory_budget_mb,
        "tile_root": str(tile_root),
        "overlay_tile_root": str(overlay_tile_root),
        "sides": sides,
        "tile_count": total_tiles,
        "overlay_tile_count": overlay_tile_count,
        "overlay_count": len([item for item in overlays if isinstance(item, dict)]),
        "status": "tile_ready" if total_tiles else "relative_only",
    }
    _write_json(pair_dir / "tile_manifest.json", manifest)
    return manifest


def merge_tiles_manifest(viewer_root: Path, pair_manifest: dict[str, Any]) -> Path:
    path = Path(viewer_root) / "tiles_manifest.json"
    payload = _read_json(path)
    pairs = payload.get("pairs", {})
    if not isinstance(pairs, dict):
        pairs = {}
    pair_uuid = str(pair_manifest.get("pair_uuid") or "")
    if pair_uuid:
        pairs[pair_uuid] = pair_manifest
    payload.update(
        {
            "schema_version": 1,
            "tile_size": pair_manifest.get("tile_size", 512),
            "pairs": pairs,
            "pair_count": len(pairs),
            "tile_count": sum(int(item.get("tile_count", 0)) for item in pairs.values() if isinstance(item, dict)),
            "overlay_tile_count": sum(
                int(item.get("overlay_tile_count", 0)) for item in pairs.values() if isinstance(item, dict)
            ),
        }
    )
    _write_json(path, payload)
    return path


def tiles_manifest_is_current(path: Path, pair_uuid: str, cache_key: str) -> bool:
    payload = _read_json(path)
    pair = (payload.get("pairs") or {}).get(pair_uuid) if isinstance(payload.get("pairs"), dict) else None
    return isinstance(pair, dict) and str(pair.get("cache_key") or "") == cache_key


# ---------------------------------------------------------------------------
# Audit-gates §10.5 Phase B — Streaming tile manifest API
# ---------------------------------------------------------------------------
#
# The legacy ``merge_tiles_manifest`` reads the full ``tiles_manifest.json``,
# mutates a single pair, and rewrites the whole file. For S20-class drawings
# this means O(N²) JSON parsing and a manifest dict that scales with the
# number of pairs in memory.
#
# The streaming API below appends one JSON line per pair to
# ``tiles_manifest.jsonl`` and only materialises the consolidated
# ``tiles_manifest.json`` once at the end of the run. Memory point usage is
# bounded by a single pair record regardless of total pair count.
#
# Backward compatibility: the legacy ``merge_tiles_manifest`` continues to
# work; callers may opt-in to the streaming API per call site.

TILES_MANIFEST_JSONL = "tiles_manifest.jsonl"
TILES_MANIFEST_JSON = "tiles_manifest.json"


def append_pair_to_tiles_manifest_jsonl(
    viewer_root: Path, pair_manifest: dict[str, Any]
) -> Path:
    """Append a single pair manifest record to ``tiles_manifest.jsonl``.

    The streaming counterpart to ``merge_tiles_manifest``: O(1) memory and
    O(append) disk per call. Callers should invoke
    ``materialise_tiles_manifest_from_jsonl`` once after all pairs have been
    written to produce the consolidated JSON file the viewer expects.

    The JSONL file is plain text with one JSON object per line. Lines that
    target the same ``pair_uuid`` overwrite earlier ones logically — the
    consolidator keeps the last occurrence so callers can re-emit a pair
    after a cache invalidation without manual dedup.

    Plan §19 A-2 (Agent A finding A1) — atomic append. Two workers
    appending concurrently to the same JSONL file would interleave
    bytes from their respective ``handle.write`` calls (the buffered
    write isn't a single ``os.write`` syscall on Windows), corrupting
    JSON records mid-line. Mitigation: serialise + encode the line
    upfront, then issue a single ``os.write`` to the file descriptor
    opened with ``O_APPEND``. POSIX guarantees ``O_APPEND`` writes
    are atomic up to ``PIPE_BUF`` (4 KiB); pair manifests are well
    under that. Windows lacks the same guarantee, but the single
    write call still removes the worst interleaving window.
    """
    viewer_root = Path(viewer_root)
    viewer_root.mkdir(parents=True, exist_ok=True)
    jsonl_path = viewer_root / TILES_MANIFEST_JSONL
    payload = (json.dumps(pair_manifest, ensure_ascii=False) + "\n").encode("utf-8")
    with _TILES_MANIFEST_WRITE_LOCK:
        fd = os.open(
            str(jsonl_path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
    return jsonl_path


def iter_tiles_manifest_jsonl(viewer_root: Path) -> Iterator[dict[str, Any]]:
    """Stream ``tiles_manifest.jsonl`` records one at a time.

    Yields the dict for each JSONL line in file order. Skips blank lines and
    silently ignores malformed lines so a single bad write cannot abort the
    consolidator.
    """
    jsonl_path = Path(viewer_root) / TILES_MANIFEST_JSONL
    if not jsonl_path.exists():
        return
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def materialise_tiles_manifest_from_jsonl(
    viewer_root: Path,
    *,
    keep_jsonl: bool = True,
) -> Path:
    """Consolidate ``tiles_manifest.jsonl`` into ``tiles_manifest.json``.

    Memory bound: O(unique pair count) — only the latest record per pair is
    held when the consolidated JSON is rewritten. For S20-class data this is
    far smaller than holding every (pair × tile count × overlay) field that
    the legacy ``merge_tiles_manifest`` accumulated across N updates.

    Args:
        viewer_root: directory containing the JSONL stream
        keep_jsonl: when False, remove the JSONL after a successful merge
    """
    viewer_root = Path(viewer_root)
    json_path = viewer_root / TILES_MANIFEST_JSON
    pairs: dict[str, dict[str, Any]] = {}
    tile_size = 512
    for pair_record in iter_tiles_manifest_jsonl(viewer_root):
        pair_uuid = str(pair_record.get("pair_uuid") or "")
        if not pair_uuid:
            continue
        pairs[pair_uuid] = pair_record
        if "tile_size" in pair_record:
            try:
                tile_size = int(pair_record["tile_size"])
            except (TypeError, ValueError):
                pass
    payload = {
        "schema_version": 1,
        "tile_size": tile_size,
        "pairs": pairs,
        "pair_count": len(pairs),
        "tile_count": sum(
            int(item.get("tile_count", 0))
            for item in pairs.values()
            if isinstance(item, dict)
        ),
        "overlay_tile_count": sum(
            int(item.get("overlay_tile_count", 0))
            for item in pairs.values()
            if isinstance(item, dict)
        ),
    }
    _write_json(json_path, payload)
    if not keep_jsonl:
        jsonl_path = viewer_root / TILES_MANIFEST_JSONL
        try:
            jsonl_path.unlink()
        except OSError:
            pass
    return json_path


def _write_image_pyramid(image_path: Path, output_dir: Path, options: ViewerTileCacheOptions) -> dict[str, Any]:
    from PIL import Image

    output_dir.mkdir(parents=True, exist_ok=True)
    levels: list[dict[str, Any]] = []
    with Image.open(image_path) as original:
        image = original.convert("RGB")
        level = 0
        scale = 1.0
        while level < options.max_levels:
            level_dir = output_dir / str(level)
            level_dir.mkdir(parents=True, exist_ok=True)
            width, height = image.size
            tiles = 0
            for y in range(0, height, options.tile_size):
                for x in range(0, width, options.tile_size):
                    tile = image.crop((x, y, min(x + options.tile_size, width), min(y + options.tile_size, height)))
                    tile.save(level_dir / f"{x // options.tile_size}_{y // options.tile_size}.png")
                    tiles += 1
            levels.append(
                {
                    "level": level,
                    "scale": scale,
                    "width": width,
                    "height": height,
                    "cols": math.ceil(width / options.tile_size),
                    "rows": math.ceil(height / options.tile_size),
                    "tile_count": tiles,
                }
            )
            if max(width, height) <= options.tile_size or level + 1 >= options.max_levels:
                break
            next_width = max(1, width // 2)
            next_height = max(1, height // 2)
            image = image.resize((next_width, next_height), Image.Resampling.LANCZOS)
            scale *= 0.5
            level += 1
    return {"image": str(image_path), "levels": levels, "tile_count": sum(int(level["tile_count"]) for level in levels)}


def _write_overlay_tiles(overlays: Sequence[dict[str, Any]], output_dir: Path, options: ViewerTileCacheOptions) -> int:
    buckets: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for overlay in overlays:
        if not isinstance(overlay, dict):
            continue
        rect = rect_from_overlay(overlay)
        if not rect:
            continue
        key = tile_coord_for_rect(rect, tile_size=options.tile_size)
        buckets.setdefault(key, []).append(overlay)
    for (tile_x, tile_y), items in buckets.items():
        items = list(visible_or_clustered_overlays(items, max_visible=options.max_visible_overlays)["items"])
        _write_json(output_dir / "0" / f"{tile_x}_{tile_y}.json", {"tile": [tile_x, tile_y], "overlays": items})
    return len(buckets)


def _cluster_overlays(overlays: Sequence[dict[str, Any]], *, max_visible: int) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    grid = max(8, int(math.sqrt(max_visible)))
    for overlay in overlays:
        rect = rect_from_overlay(overlay) or _normalized_rect(overlay)
        if not rect:
            continue
        key = f"{int(float(rect['x']) // grid)}:{int(float(rect['y']) // grid)}"
        cluster = clusters.setdefault(
            key,
            {
                "zone_id": f"cluster-{key}",
                "change_type": "mixed",
                "raw_change_count": 0,
                "cluster_count": 0,
                "bbox": dict(rect),
                "after_bbox_px": dict(rect),
                "normalized_bbox": dict(rect),
            },
        )
        cluster["raw_change_count"] += _number(overlay.get("raw_change_count"))
        cluster["cluster_count"] += 1
        cluster["bbox"] = _merge_rect(cluster["bbox"], rect)
        cluster["after_bbox_px"] = dict(cluster["bbox"])
    values = list(clusters.values())
    values.sort(key=lambda item: (-_number(item.get("raw_change_count")), str(item.get("zone_id"))))
    return values[:max_visible]


def _select_level(levels: Sequence[dict[str, Any]], zoom: float) -> dict[str, Any]:
    safe_zoom = max(0.0001, float(zoom or 1.0))
    ordered = sorted(levels, key=lambda item: int(item.get("level") or 0))
    return min(
        ordered,
        key=lambda item: abs(math.log(max(0.0001, float(item.get("scale") or 1.0)) / safe_zoom)),
    )


def _merge_rect(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    left = min(float(a["x"]), float(b["x"]))
    top = min(float(a["y"]), float(b["y"]))
    right = max(float(a["x"]) + float(a["width"]), float(b["x"]) + float(b["width"]))
    bottom = max(float(a["y"]) + float(a["height"]), float(b["y"]) + float(b["height"]))
    return {"x": left, "y": top, "width": max(1.0, right - left), "height": max(1.0, bottom - top)}


def _normalized_rect(overlay: dict[str, Any]) -> Optional[dict[str, float]]:
    value = overlay.get("normalized_bbox")
    if isinstance(value, dict):
        try:
            return {
                "x": float(value.get("x", 0.0)),
                "y": float(value.get("y", 0.0)),
                "width": max(0.001, float(value.get("width", 0.0))),
                "height": max(0.001, float(value.get("height", 0.0))),
            }
        except (TypeError, ValueError):
            return None
    return None


def _intersects(a: Optional[dict[str, float]], b: Optional[dict[str, float]]) -> bool:
    if not a or not b:
        return False
    return not (
        a["x"] + a["width"] < b["x"]
        or b["x"] + b["width"] < a["x"]
        or a["y"] + a["height"] < b["y"]
        or b["y"] + b["height"] < a["y"]
    )


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_name(value: Any) -> str:
    text = str(value or "pair").strip() or "pair"
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
