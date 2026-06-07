"""Selected-zone render service for the drawing comparison viewer.

This module is display-only.  CAD entity comparison and change-zone streams
remain the source of truth; this service only renders a local inspection crop
for the currently selected change zone.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from .cache_budget import process_rss_mb, resolve_cache_byte_limit
from .perf_events import append_perf_event
from .source_signature import build_source_signature

logger = logging.getLogger(__name__)

# v2: zone crops now render frozen/off layers (a detected change on a frozen
# layer was producing a blank zoom). Bump invalidates stale blank-crop caches.
SCHEMA_VERSION = 2
DEFAULT_OUTPUT_SIZE = (1600, 900)
DEFAULT_TARGET_ASPECT = DEFAULT_OUTPUT_SIZE[0] / DEFAULT_OUTPUT_SIZE[1]


@dataclass(frozen=True)
class WorldWindow:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def width(self) -> float:
        return max(1e-9, self.xmax - self.xmin)

    @property
    def height(self) -> float:
        return max(1e-9, self.ymax - self.ymin)

    def to_dict(self) -> dict[str, float]:
        return {
            "xmin": round(self.xmin, 6),
            "ymin": round(self.ymin, 6),
            "xmax": round(self.xmax, 6),
            "ymax": round(self.ymax, 6),
        }


@dataclass(frozen=True)
class RenderJob:
    pair_uuid: str
    zone_id: str
    source_before: Path
    source_after: Path
    world_window: WorldWindow
    cache_root: Path
    dxf_cache_dir: Path
    before_world_window: Optional[WorldWindow] = None
    after_world_window: Optional[WorldWindow] = None
    request_id: str = ""
    output_width: int = DEFAULT_OUTPUT_SIZE[0]
    output_height: int = DEFAULT_OUTPUT_SIZE[1]
    renderer_backend: str = "ezdxf-matplotlib-zone"
    font_manifest_hash: str = "unknown"
    render_environment_hash: str = ""
    before_background_image: str = ""
    after_background_image: str = ""
    before_background_transform: Optional[dict[str, Any]] = None
    after_background_transform: Optional[dict[str, Any]] = None
    perf_event_root: Optional[Path] = None
    perf_run_id: str = ""
    # P0-2b — RigidTransform.to_dict() (after->before, B->A) when the diff
    # pipeline found a significant alignment; None = no visual alignment (the
    # historical path). Drives the after-raster warp + marker transform so they
    # stay in lockstep. Part of the cache key (aligned != unaligned render).
    alignment: Optional[dict[str, Any]] = None
    # ② full-detail deferred upgrade — when True, skip the fast
    # cad-background-image-crop (which crops the simplified whole-drawing raster,
    # dropping TEXT/DIMENSION/INSERT/HATCH) and render the zone window directly
    # from the source via the ezdxf Frontend (text/dims/blocks). Slower (~seconds)
    # so the GUI issues it as a background upgrade AFTER the fast crop.
    prefer_source_render: bool = False


@dataclass(frozen=True)
class RenderResult:
    pair_uuid: str
    zone_id: str
    before_image: str
    after_image: str
    before_transform: dict[str, Any]
    after_transform: dict[str, Any]
    world_window: dict[str, float]
    renderer_backend: str
    cache_key: str
    cache_hit: bool
    visual_fidelity: str
    render_lifecycle: str
    warnings: list[str]
    request_id: str = ""
    reason_code: str = ""
    # P0-2b — RigidTransform.to_dict() (after->before, B->A) emitted when the
    # after raster was warped into the before frame, so the monolith can move the
    # after-side change markers by the SAME transform (lockstep). None = no warp.
    after_marker_world_transform: Optional[dict[str, Any]] = None
    # Plan §17 Phase B-1 (GPT Pro F3 follow-up) — wall time of the
    # ``render_zone_pair`` call. Without this, the GUI handler at
    # ``drawing_compare_workbench.py`` was reading
    # ``result_payload.get("elapsed_ms")`` from a key the worker never
    # populated, so every GUI-side render_ms event was 0. Only the
    # validator measured elapsed_ms via its own perf_counter wrap.
    # Defaults to 0.0 for backward compatibility with JSONL consumers
    # that don't yet read the field.
    elapsed_ms: float = 0.0
    pdf_display_list_cache: dict[str, Any] = field(default_factory=dict)
    dxf_index_cache: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "pair_uuid": self.pair_uuid,
            "zone_id": self.zone_id,
            "before_image": self.before_image,
            "after_image": self.after_image,
            "before_transform": self.before_transform,
            "after_transform": self.after_transform,
            "after_marker_world_transform": self.after_marker_world_transform,
            "world_window": self.world_window,
            "renderer_backend": self.renderer_backend,
            "cache_key": self.cache_key,
            "cache_hit": self.cache_hit,
            "visual_fidelity": self.visual_fidelity,
            "render_lifecycle": self.render_lifecycle,
            "warnings": self.warnings,
            "request_id": self.request_id,
            "reason_code": self.reason_code,
            "fallback_reason_code": self.reason_code,
            "elapsed_ms": self.elapsed_ms,
        }
        payload.update(_flatten_pdf_display_list_cache(self.pdf_display_list_cache))
        payload.update(_flatten_dxf_index_cache(self.dxf_index_cache))
        return payload


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _flatten_pdf_display_list_cache(stats: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(stats, dict) or not stats:
        return {}
    flattened: dict[str, Any] = {"pdf_display_list_cache": dict(stats)}
    for key, value in stats.items():
        if key == "pil_fallback_count":
            flattened["pdf_pil_fallback_count"] = _safe_int(value)
        elif key == "worker_rss_mb":
            flattened["pdf_display_list_worker_rss_mb"] = _safe_float(value)
        else:
            flattened[f"pdf_display_list_{key}"] = value
    return flattened


def _flatten_dxf_index_cache(stats: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(stats, dict) or not stats:
        return {}
    flattened: dict[str, Any] = {"dxf_index_cache": dict(stats)}
    for key, value in stats.items():
        if key == "worker_rss_mb":
            flattened["dxf_index_cache_worker_rss_mb"] = _safe_float(value)
        else:
            flattened[f"dxf_index_cache_{key}"] = value
    return flattened


def _aggregate_pdf_display_list_cache(*items: dict[str, Any] | None) -> dict[str, Any]:
    telemetry = [item for item in items if isinstance(item, dict)]
    if not telemetry:
        return {}
    display_items = [item for item in telemetry if bool(item.get("used_display_list"))]
    lookup_hit_count = sum(1 for item in display_items if item.get("lookup_cache_hit") is True)
    lookup_miss_count = sum(1 for item in display_items if item.get("lookup_cache_hit") is False)
    lookup_count = lookup_hit_count + lookup_miss_count
    pil_fallback_count = sum(1 for item in telemetry if bool(item.get("pil_fallback")))
    cache_total = max((_safe_int(item.get("total_estimated_bytes")) for item in telemetry), default=0)
    byte_limit = max((_safe_int(item.get("byte_limit")) for item in telemetry), default=0)
    entry_bytes_max = max((_safe_int(item.get("entry_estimated_bytes")) for item in telemetry), default=0)
    worker_rss = max((_safe_float(item.get("process_rss_mb")) for item in telemetry), default=0.0)
    result: dict[str, Any] = {
        "render_count": len(display_items),
        "cache_lookup_count": lookup_count,
        "cache_hit_count": lookup_hit_count,
        "cache_miss_count": lookup_miss_count,
        "cache_hit_rate": round(lookup_hit_count / lookup_count, 4) if lookup_count else 0.0,
        "cache_eviction_count": max(
            (_safe_int(item.get("eviction_count")) for item in telemetry),
            default=0,
        ),
        "cache_evicted_estimated_bytes": max(
            (_safe_int(item.get("evicted_estimated_bytes")) for item in telemetry),
            default=0,
        ),
        "cache_total_estimated_bytes": cache_total,
        "cache_byte_limit": byte_limit,
        "cache_entry_estimated_bytes_max": entry_bytes_max,
        "pil_fallback_count": pil_fallback_count,
    }
    if worker_rss > 0:
        result["worker_rss_mb"] = round(worker_rss, 3)
    return result


@dataclass(frozen=True)
class EntityEnvelope:
    handle: str
    bbox: Optional[tuple[float, float, float, float]]
    layer: str
    entity_type: str


@dataclass
class DrawingRenderIndex:
    dxf_path: Path
    source_signature: dict[str, Any]
    render_environment_hash: str
    doc: Any
    modelspace: Any
    bbox_cache: Any
    envelopes: list[EntityEnvelope]
    # Plan §15 Phase A-3 (M3 adaptive cache) — these two fields let the
    # eviction policy prefer keeping expensive-to-rebuild indexes when the
    # cache is full. ``entity_count`` is the cheapest proxy for how much
    # work was needed; ``render_time_ms`` is the truth (only known on
    # build, not on cache-hit).
    entity_count: int = 0
    render_time_ms: float = 0.0
    estimated_bytes: int = 0


_INDEX_CACHE: dict[str, DrawingRenderIndex] = {}
_INDEX_CACHE_ORDER: list[str] = []
_INDEX_CACHE_SIZE_ENV_VAR = "DRAWING_COMPARE_INDEX_CACHE_SIZE"
_INDEX_CACHE_MB_ENV_VAR = "DRAWING_COMPARE_DXF_INDEX_CACHE_MB"
_DEFAULT_INDEX_CACHE_MB = 512
# Default (4) preserves the original behaviour for callers that do not opt
# into the adaptive resize. Plan §15 Phase A-3 expands this when the
# process has enough free memory; see ``_resolve_max_cache_entries()``.
_MAX_INDEX_CACHE_ENTRIES = 4
_INDEX_CACHE_TOTAL_ESTIMATED_BYTES = 0
_INDEX_CACHE_HIT_COUNT = 0
_INDEX_CACHE_MISS_COUNT = 0
_INDEX_CACHE_EVICTION_COUNT = 0
_INDEX_CACHE_EVICTED_ESTIMATED_BYTES = 0
_INDEX_CACHE_LAST_EVICTION_REASON = ""


def _resolve_max_cache_entries() -> int:
    """Return the runtime cap on cached drawing render indexes.

    Plan §15 Phase A-3 (M3 adaptive cache, addresses external auditor #2
    finding): the original hardcoded ``_MAX_INDEX_CACHE_ENTRIES = 4``
    forced thrash whenever a reviewer navigated through more than four
    pairs in a session. This helper resolves the cap from, in order:

    1. ``DRAWING_COMPARE_INDEX_CACHE_SIZE`` environment variable — operator
       override for tuning. Must parse as an int >= 1; otherwise ignored.
    2. Available system memory via ``psutil`` — 8 entries when >= 8 GiB
       available, 16 entries when >= 16 GiB. Caps at 32 to bound worst-
       case resident set growth.
    3. Default ``_MAX_INDEX_CACHE_ENTRIES`` (4) — original behaviour.

    Failures (psutil missing, env value malformed) silently fall back to
    the default so the render path is never blocked by cache sizing.
    """
    env_value = os.environ.get(_INDEX_CACHE_SIZE_ENV_VAR)
    if env_value:
        try:
            override = int(env_value)
            if override >= 1:
                return min(override, 64)
        except (TypeError, ValueError):
            pass

    try:
        import psutil  # type: ignore[import-not-found]

        available_gib = psutil.virtual_memory().available / (1024 ** 3)
        if available_gib >= 16:
            return 16
        if available_gib >= 8:
            return 8
    except Exception:
        pass

    return _MAX_INDEX_CACHE_ENTRIES


def _resolve_index_cache_byte_limit() -> int:
    return resolve_cache_byte_limit(
        specific_env_var=_INDEX_CACHE_MB_ENV_VAR,
        default_mb=_DEFAULT_INDEX_CACHE_MB,
    )


def _estimate_render_index_bytes(index: DrawingRenderIndex) -> int:
    """Estimate retained bytes for a cached DXF render index."""

    try:
        source_size = int((index.source_signature or {}).get("size") or 0)
    except (TypeError, ValueError):
        source_size = 0
    entity_count = max(0, int(index.entity_count or len(index.envelopes or [])))
    envelope_count = max(entity_count, len(index.envelopes or []))
    # Envelope objects retain bbox/layer/type strings and the index also
    # keeps ezdxf doc/modelspace/bbox-cache references. This is a
    # conservative accounting proxy, not an exact native heap measurement.
    return max(
        256 * 1024,
        source_size,
        envelope_count * 768 + entity_count * 256,
    )


def _index_cache_cost(index: DrawingRenderIndex) -> float:
    return float(index.entity_count or 0) * float(index.render_time_ms or 0.0)


def _evict_index_cache_key(key: str, *, reason: str) -> None:
    global _INDEX_CACHE_TOTAL_ESTIMATED_BYTES
    global _INDEX_CACHE_EVICTION_COUNT
    global _INDEX_CACHE_EVICTED_ESTIMATED_BYTES
    global _INDEX_CACHE_LAST_EVICTION_REASON

    entry = _INDEX_CACHE.pop(key, None)
    if entry is None:
        return
    _INDEX_CACHE_TOTAL_ESTIMATED_BYTES = max(
        0,
        _INDEX_CACHE_TOTAL_ESTIMATED_BYTES - int(entry.estimated_bytes or 0),
    )
    _INDEX_CACHE_EVICTION_COUNT += 1
    _INDEX_CACHE_EVICTED_ESTIMATED_BYTES += int(entry.estimated_bytes or 0)
    _INDEX_CACHE_LAST_EVICTION_REASON = str(reason or "unknown")


def _evict_to_capacity(capacity: int, byte_limit: Optional[int] = None) -> None:
    """Evict cache entries until at most ``capacity`` remain.

    Plan §15 Phase A-3 — original FIFO-on-build is preserved as the
    primary signal (oldest-used first), but among the oldest few we keep
    the most expensive index so reviewer navigation does not pay the
    rebuild cost twice. Cost = ``entity_count * render_time_ms``.

    The list is mutated in place. Safe to call with capacity > current
    size (no-op).
    """
    byte_limit = max(1, int(byte_limit or _resolve_index_cache_byte_limit()))
    while len(_INDEX_CACHE_ORDER) > capacity:
        # Examine up to the 3 oldest entries; evict the cheapest.
        # If any have unknown cost (legacy entries), evict them first.
        candidates = _INDEX_CACHE_ORDER[: min(3, len(_INDEX_CACHE_ORDER))]
        cheapest_key = candidates[0]
        cheapest_cost = float("inf")
        for key in candidates:
            entry = _INDEX_CACHE.get(key)
            if entry is None:
                cheapest_key = key
                break
            cost = _index_cache_cost(entry)
            if cost < cheapest_cost:
                cheapest_cost = cost
                cheapest_key = key
        _INDEX_CACHE_ORDER.remove(cheapest_key)
        _evict_index_cache_key(cheapest_key, reason="entry_capacity")
    while len(_INDEX_CACHE_ORDER) > 1 and _INDEX_CACHE_TOTAL_ESTIMATED_BYTES > byte_limit:
        oldest = _INDEX_CACHE_ORDER.pop(0)
        _evict_index_cache_key(oldest, reason="byte_capacity")


def _clear_index_cache() -> None:
    """Test hook — wipe the in-process cache so each test starts clean."""
    global _INDEX_CACHE_TOTAL_ESTIMATED_BYTES
    global _INDEX_CACHE_HIT_COUNT
    global _INDEX_CACHE_MISS_COUNT
    global _INDEX_CACHE_EVICTION_COUNT
    global _INDEX_CACHE_EVICTED_ESTIMATED_BYTES
    global _INDEX_CACHE_LAST_EVICTION_REASON

    _INDEX_CACHE.clear()
    _INDEX_CACHE_ORDER.clear()
    _INDEX_CACHE_TOTAL_ESTIMATED_BYTES = 0
    _INDEX_CACHE_HIT_COUNT = 0
    _INDEX_CACHE_MISS_COUNT = 0
    _INDEX_CACHE_EVICTION_COUNT = 0
    _INDEX_CACHE_EVICTED_ESTIMATED_BYTES = 0
    _INDEX_CACHE_LAST_EVICTION_REASON = ""


def bbox_from_value(value: object) -> Optional[tuple[float, float, float, float]]:
    """Normalize common bbox shapes into (min_x, min_y, max_x, max_y)."""

    if isinstance(value, dict):
        try:
            if {"min_x", "min_y", "max_x", "max_y"}.issubset(value):
                coords = (value["min_x"], value["min_y"], value["max_x"], value["max_y"])
            elif {"x", "y", "width", "height"}.issubset(value):
                x = float(value["x"])
                y = float(value["y"])
                coords = (x, y, x + float(value["width"]), y + float(value["height"]))
            else:
                return None
        except (TypeError, ValueError):
            return None
    elif isinstance(value, (list, tuple)) and len(value) >= 4:
        coords = tuple(value[:4])
    else:
        return None
    try:
        x1, y1, x2, y2 = (float(item) for item in coords)
    except (TypeError, ValueError):
        return None
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def union_bboxes(*values: object) -> Optional[tuple[float, float, float, float]]:
    normalized = [bbox_from_value(value) for value in values if value is not None]
    boxes = [box for box in normalized if box is not None]
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def canonical_window_from_bbox(
    bbox: object,
    *,
    padding_ratio: float = 0.18,
    min_size: float = 250.0,
    target_aspect: float = DEFAULT_TARGET_ASPECT,
) -> WorldWindow:
    """Build a shared before/after review window around a zone bbox."""

    normalized = bbox_from_value(bbox)
    if normalized is None:
        raise ValueError(f"Invalid bbox for canonical window: {bbox!r}")
    x1, y1, x2, y2 = normalized
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    width = max(abs(x2 - x1), float(min_size))
    height = max(abs(y2 - y1), float(min_size))
    width *= 1.0 + max(0.0, float(padding_ratio)) * 2.0
    height *= 1.0 + max(0.0, float(padding_ratio)) * 2.0
    target = max(0.1, float(target_aspect or DEFAULT_TARGET_ASPECT))
    current = width / height if height else target
    if current < target:
        width = height * target
    elif current > target:
        height = width / target
    return WorldWindow(
        xmin=cx - width / 2.0,
        ymin=cy - height / 2.0,
        xmax=cx + width / 2.0,
        ymax=cy + height / 2.0,
    )


def transform_for_window(
    window: WorldWindow,
    *,
    output_width: int = DEFAULT_OUTPUT_SIZE[0],
    output_height: int = DEFAULT_OUTPUT_SIZE[1],
    renderer_backend: str = "ezdxf-matplotlib-zone",
) -> dict[str, Any]:
    width = max(1, int(output_width))
    height = max(1, int(output_height))
    scale_x = width / window.width
    scale_y = height / window.height
    return {
        "schema_version": SCHEMA_VERSION,
        "coordinate_space": "cad_world_window",
        "min_x": window.xmin,
        "min_y": window.ymin,
        "max_x": window.xmax,
        "max_y": window.ymax,
        "img_width": width,
        "img_height": height,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "offset_x": window.xmin,
        "offset_y": window.ymin,
        "renderer_backend": renderer_backend,
        "world_to_pixel": {
            "a": scale_x,
            "b": 0.0,
            "c": 0.0,
            "d": -scale_y,
            "e": -window.xmin * scale_x,
            "f": height + window.ymin * scale_y,
        },
        "pixel_to_world": {
            "a": 1.0 / scale_x if scale_x else 1.0,
            "b": 0.0,
            "c": 0.0,
            "d": -1.0 / scale_y if scale_y else -1.0,
            "e": window.xmin,
            "f": window.ymin + height / scale_y if scale_y else window.ymax,
        },
    }


def transform_for_image_pixel_window(
    window: WorldWindow,
    *,
    output_width: int = DEFAULT_OUTPUT_SIZE[0],
    output_height: int = DEFAULT_OUTPUT_SIZE[1],
    renderer_backend: str = "pdf-image-crop",
) -> dict[str, Any]:
    width = max(1, int(output_width))
    height = max(1, int(output_height))
    scale_x = width / window.width
    scale_y = height / window.height
    return {
        "schema_version": SCHEMA_VERSION,
        "coordinate_space": "image_pixels",
        "min_x": window.xmin,
        "min_y": window.ymin,
        "max_x": window.xmax,
        "max_y": window.ymax,
        "img_width": width,
        "img_height": height,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "offset_x": window.xmin,
        "offset_y": window.ymin,
        "renderer_backend": renderer_backend,
        "world_to_pixel": {
            "a": scale_x,
            "b": 0.0,
            "c": 0.0,
            "d": scale_y,
            "e": -window.xmin * scale_x,
            "f": -window.ymin * scale_y,
        },
        "pixel_to_world": {
            "a": 1.0 / scale_x if scale_x else 1.0,
            "b": 0.0,
            "c": 0.0,
            "d": 1.0 / scale_y if scale_y else 1.0,
            "e": window.xmin,
            "f": window.ymin,
        },
    }


def bbox_to_pixel_rect(bbox: object, transform: dict[str, Any]) -> Optional[dict[str, float]]:
    box = bbox_from_value(bbox)
    if not box:
        return None
    min_x = float(transform.get("min_x", 0.0))
    min_y = float(transform.get("min_y", 0.0))
    width = float(transform.get("img_width", 0.0))
    height = float(transform.get("img_height", 0.0))
    scale_x = float(transform.get("scale_x", 1.0))
    scale_y = float(transform.get("scale_y", 1.0))
    x1 = (box[0] - min_x) * scale_x
    x2 = (box[2] - min_x) * scale_x
    if str(transform.get("coordinate_space") or "").lower() == "image_pixels":
        y1 = (box[1] - min_y) * scale_y
        y2 = (box[3] - min_y) * scale_y
    else:
        y1 = height - ((box[3] - min_y) * scale_y)
        y2 = height - ((box[1] - min_y) * scale_y)
    left = max(0.0, min(width, min(x1, x2)))
    right = max(0.0, min(width, max(x1, x2)))
    top = max(0.0, min(height, min(y1, y2)))
    bottom = max(0.0, min(height, max(y1, y2)))
    if right <= left or bottom <= top:
        return None
    return {
        "x": round(left, 2),
        "y": round(top, 2),
        "width": max(1.0, round(right - left, 2)),
        "height": max(1.0, round(bottom - top, 2)),
    }


def file_signature(path: Path) -> dict[str, Any]:
    signature = build_source_signature(path)
    return {
        "path": signature["source_path"],
        "size": signature["file_size"],
        "mtime_ns": signature["mtime_ns"],
        "source_hash": signature["source_hash"],
        "schema_version": signature["schema_version"],
    }


def render_cache_key(job: RenderJob) -> str:
    environment_hash = job.render_environment_hash or job.font_manifest_hash or "unknown"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "pair_uuid": job.pair_uuid,
        "zone_id": job.zone_id,
        "before": file_signature(job.source_before),
        "after": file_signature(job.source_after),
        "world_window": job.world_window.to_dict(),
        "before_world_window": job.before_world_window.to_dict() if job.before_world_window else {},
        "after_world_window": job.after_world_window.to_dict() if job.after_world_window else {},
        "output": {"width": job.output_width, "height": job.output_height},
        "renderer_backend": job.renderer_backend,
        "font_manifest_hash": job.font_manifest_hash,
        "render_environment_hash": environment_hash,
        "before_background": file_signature(Path(job.before_background_image)) if job.before_background_image else {},
        "after_background": file_signature(Path(job.after_background_image)) if job.after_background_image else {},
        "before_background_transform": job.before_background_transform or {},
        "after_background_transform": job.after_background_transform or {},
        "alignment": job.alignment or {},
        # ② full-detail upgrade renders a DIFFERENT image (source ezdxf render vs
        # the cropped fast raster) for the same zone, so it must not collide with
        # the fast crop's cache entry.
        "prefer_source_render": bool(job.prefer_source_render),
    }
    # surrogatepass: Korean Windows paths can carry lone CP949<->UTF-16 surrogate
    # codepoints (build_source_signature stores str(resolved)). A plain
    # .encode("utf-8") raises "surrogates not allowed" here, which surfaced as
    # "선택 구역 렌더 실패 - 상대 위치 표시를 유지합니다" — the zoom crop never rendered.
    # Windows still opens the file via the surrogate path, so keep it for access
    # and only make the hash encoding tolerant.
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8", "surrogatepass")
    return hashlib.sha256(raw).hexdigest()[:32]


def render_environment_signature(
    *,
    renderer_backend: str = "ezdxf-matplotlib-zone",
    dxf_cache_dir: Optional[Path] = None,
    font_support_dirs: Optional[Sequence[Path]] = None,
) -> str:
    """Return a stable signature for render-affecting local dependencies."""

    package_versions: dict[str, str] = {}
    for package in ("ezdxf", "matplotlib", "Pillow", "numpy"):
        try:
            package_versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            package_versions[package] = "missing"
    support_dirs = list(font_support_dirs or _font_support_dirs_from_env())
    payload = {
        "schema_version": SCHEMA_VERSION,
        "renderer_backend": renderer_backend,
        "packages": package_versions,
        "legacy_dwg_converter": {"enabled": False},
        "dxf_cache_dir": str(Path(dxf_cache_dir).resolve()) if dxf_cache_dir else "",
        "font_support_dirs": [_directory_signature(Path(path)) for path in support_dirs],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8", "surrogatepass")
    return hashlib.sha256(raw).hexdigest()[:32]


def clear_render_index_cache() -> None:
    _clear_index_cache()


def render_index_cache_stats() -> dict[str, Any]:
    lookup_count = int(_INDEX_CACHE_HIT_COUNT + _INDEX_CACHE_MISS_COUNT)
    entry_estimated_bytes_max = max(
        (int(entry.estimated_bytes or 0) for entry in _INDEX_CACHE.values()),
        default=0,
    )
    stats: dict[str, Any] = {
        "entries": len(_INDEX_CACHE),
        "capacity_entries": _resolve_max_cache_entries(),
        "byte_limit": _resolve_index_cache_byte_limit(),
        "entry_estimated_bytes_max": entry_estimated_bytes_max,
        "total_estimated_bytes": int(_INDEX_CACHE_TOTAL_ESTIMATED_BYTES),
        "lookup_count": lookup_count,
        "hit_count": int(_INDEX_CACHE_HIT_COUNT),
        "miss_count": int(_INDEX_CACHE_MISS_COUNT),
        "hit_rate": round(_INDEX_CACHE_HIT_COUNT / lookup_count, 4) if lookup_count else 0.0,
        "eviction_count": int(_INDEX_CACHE_EVICTION_COUNT),
        "evicted_estimated_bytes": int(_INDEX_CACHE_EVICTED_ESTIMATED_BYTES),
        "last_eviction_reason": _INDEX_CACHE_LAST_EVICTION_REASON,
    }
    rss_mb = process_rss_mb()
    if rss_mb > 0:
        stats["worker_rss_mb"] = rss_mb
    return stats


def _diff_index_cache_stats(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(after, dict) or not after:
        return {}
    before_payload = before if isinstance(before, dict) else {}
    hit_count = max(
        0,
        _safe_int(after.get("hit_count")) - _safe_int(before_payload.get("hit_count")),
    )
    miss_count = max(
        0,
        _safe_int(after.get("miss_count")) - _safe_int(before_payload.get("miss_count")),
    )
    lookup_count = hit_count + miss_count
    eviction_count = max(
        0,
        _safe_int(after.get("eviction_count")) - _safe_int(before_payload.get("eviction_count")),
    )
    evicted_estimated_bytes = max(
        0,
        _safe_int(after.get("evicted_estimated_bytes"))
        - _safe_int(before_payload.get("evicted_estimated_bytes")),
    )
    result: dict[str, Any] = {
        "entries": _safe_int(after.get("entries")),
        "capacity_entries": _safe_int(after.get("capacity_entries")),
        "byte_limit": _safe_int(after.get("byte_limit")),
        "entry_estimated_bytes_max": _safe_int(after.get("entry_estimated_bytes_max")),
        "total_estimated_bytes": _safe_int(after.get("total_estimated_bytes")),
        "lookup_count": lookup_count,
        "hit_count": hit_count,
        "miss_count": miss_count,
        "hit_rate": round(hit_count / lookup_count, 4) if lookup_count else 0.0,
        "eviction_count": eviction_count,
        "evicted_estimated_bytes": evicted_estimated_bytes,
        "last_eviction_reason": str(after.get("last_eviction_reason") or "") if eviction_count else "",
    }
    worker_rss = _safe_float(after.get("worker_rss_mb"))
    if worker_rss > 0:
        result["worker_rss_mb"] = worker_rss
    return result


def _apply_after_alignment(
    job: RenderJob,
    before_transform: dict[str, Any],
    after_transform: dict[str, Any],
    after_image: Path,
    warnings: list[str],
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """P0-2b — warp the after PNG into the before frame when ``job.alignment`` is
    a significant rigid transform (after->before, B->A).

    Returns ``(after_transform, marker_world_transform)``:
      - on success: the before-frame transform + ``rigid.to_dict()`` (the SAME T
        the monolith applies to after-side marker world coords -> lockstep);
      - when no/insignificant alignment, or on ANY failure: the original
        after_transform + ``None`` (honest fallback; a warning is appended so the
        degradation is visible rather than silent). Never raises into the render.
    """
    if not job.alignment:
        return after_transform, None
    try:
        from .global_alignment import RigidTransform
        from . import render_alignment as ra
    except ImportError:
        return after_transform, None
    try:
        rigid = RigidTransform.from_dict(job.alignment)
    except (TypeError, ValueError):
        return after_transform, None
    if not ra.is_alignment_active(rigid):
        return after_transform, None
    pixel_affine = ra.compose_after_pixel_affine(before_transform, after_transform, rigid)
    if pixel_affine is None:
        return after_transform, None
    try:
        import numpy as np
        from PIL import Image

        out_w = int(after_transform.get("img_width") or DEFAULT_OUTPUT_SIZE[0])
        out_h = int(after_transform.get("img_height") or DEFAULT_OUTPUT_SIZE[1])
        with Image.open(after_image) as im:
            arr = np.asarray(im.convert("RGB"))
        warped = ra.warp_after_image(arr, pixel_affine, (out_w, out_h))
        Image.fromarray(warped).save(after_image)
    except Exception as exc:  # honest fallback — keep the unaligned render
        warnings.append(f"after_alignment_warp_failed:{type(exc).__name__}")
        return after_transform, None
    warnings.append("after_alignment_applied")
    return (
        ra.aligned_after_transform(before_transform, after_transform, rigid),
        rigid.to_dict(),
    )


def render_zone_pair(job: RenderJob) -> RenderResult:
    """Render before/after local crops for a selected zone."""

    # Plan §17 Phase B-1 (GPT Pro F3 follow-up) — capture wall time so
    # ``RenderResult.elapsed_ms`` carries truth on every return branch.
    # Before B-1 the GUI handler at drawing_compare_workbench.py read
    # ``elapsed_ms`` from the JSONL payload but the worker never populated
    # the field, so every GUI-side render_ms event was 0. The validator's
    # own perf_counter wrap measured the same thing redundantly.
    _render_start_perf = time.perf_counter()
    before_window = job.before_world_window or job.world_window
    after_window = job.after_world_window or job.world_window

    cache_key = render_cache_key(job)
    def _with_perf(result: RenderResult) -> RenderResult:
        _append_zone_perf_event(job, result)
        return result

    pair_dir = job.cache_root / "zone_crops" / _safe_name(job.pair_uuid) / cache_key
    before_image = pair_dir / f"{_safe_name(job.zone_id)}_before.png"
    after_image = pair_dir / f"{_safe_name(job.zone_id)}_after.png"
    meta_path = pair_dir / "render_result.json"
    if before_image.exists() and after_image.exists() and meta_path.exists():
        payload = _read_json(meta_path)
        if payload:
            return _with_perf(RenderResult(
                pair_uuid=job.pair_uuid,
                zone_id=job.zone_id,
                before_image=str(before_image),
                after_image=str(after_image),
                before_transform=payload.get("before_transform") or {},
                after_transform=payload.get("after_transform") or {},
                after_marker_world_transform=payload.get("after_marker_world_transform"),
                world_window=payload.get("world_window") or job.world_window.to_dict(),
                renderer_backend=str(payload.get("renderer_backend") or job.renderer_backend),
                cache_key=cache_key,
                cache_hit=True,
                visual_fidelity=str(payload.get("visual_fidelity") or "cad_render"),
                render_lifecycle=str(payload.get("render_lifecycle") or "ready"),
                warnings=[str(item) for item in payload.get("warnings", [])],
                request_id=job.request_id,
                reason_code=str(payload.get("reason_code") or ""),
                elapsed_ms=round((time.perf_counter() - _render_start_perf) * 1000.0, 3),
                pdf_display_list_cache=(
                    payload.get("pdf_display_list_cache")
                    if isinstance(payload.get("pdf_display_list_cache"), dict)
                    else {}
                ),
                dxf_index_cache=(
                    payload.get("dxf_index_cache")
                    if isinstance(payload.get("dxf_index_cache"), dict)
                    else {}
                ),
            ))

    pair_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    if _requires_page_space_bbox(job.source_before) or _requires_page_space_bbox(job.source_after):
        if _can_render_pdf_image_crop(job):
            before_transform = transform_for_image_pixel_window(
                before_window,
                output_width=job.output_width,
                output_height=job.output_height,
                renderer_backend="pdf-image-crop",
            )
            after_transform = transform_for_image_pixel_window(
                after_window,
                output_width=job.output_width,
                output_height=job.output_height,
                renderer_backend="pdf-image-crop",
            )
            # Plan §17 Phase B-1b — extract source PDF + page index from
            # the background transforms so the DisplayList path can be
            # used instead of opening the full-page PNG via PIL. The
            # ``page`` key is populated by
            # ``viewer_package._render_pdf_to_png`` (single source of
            # truth for the background render). When absent (legacy
            # callers / synthetic fixtures) we default to page 0 — the
            # only behavioural effect is that multi-page PDFs without
            # a page hint still render page 0, matching the legacy
            # ``viewer_render_worker`` default.
            before_bg_xform = job.before_background_transform or {}
            after_bg_xform = job.after_background_transform or {}
            before_page = int(before_bg_xform.get("page", 0) or 0)
            after_page = int(after_bg_xform.get("page", 0) or 0)
            before_bg_w = int(before_bg_xform.get("img_width", 0) or 0)
            before_bg_h = int(before_bg_xform.get("img_height", 0) or 0)
            after_bg_w = int(after_bg_xform.get("img_width", 0) or 0)
            after_bg_h = int(after_bg_xform.get("img_height", 0) or 0)
            before_pdf_cache = _render_pdf_image_crop(
                Path(job.before_background_image),
                before_image,
                before_window,
                before_transform,
                warnings=warnings,
                source_pdf=Path(job.source_before),
                page_index=before_page,
                background_img_width=before_bg_w,
                background_img_height=before_bg_h,
            )
            after_pdf_cache = _render_pdf_image_crop(
                Path(job.after_background_image),
                after_image,
                after_window,
                after_transform,
                warnings=warnings,
                source_pdf=Path(job.source_after),
                page_index=after_page,
                background_img_width=after_bg_w,
                background_img_height=after_bg_h,
            )
            reason_code = _zone_reason_code_from_warnings(warnings)
            pdf_display_list_cache = _aggregate_pdf_display_list_cache(
                before_pdf_cache,
                after_pdf_cache,
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "pair_uuid": job.pair_uuid,
                "zone_id": job.zone_id,
                "before_image": _cache_relative_path(before_image, job.cache_root),
                "after_image": _cache_relative_path(after_image, job.cache_root),
                "before_transform": before_transform,
                "after_transform": after_transform,
                "world_window": job.world_window.to_dict(),
                "renderer_backend": "pdf-image-crop",
                "cache_key": cache_key,
                "visual_fidelity": "pdf_render",
                "render_lifecycle": "ready",
                "reason_code": reason_code,
                "warnings": warnings,
                "request_id": job.request_id,
            }
            payload.update(_flatten_pdf_display_list_cache(pdf_display_list_cache))
            _write_json(meta_path, payload)
            return _with_perf(RenderResult(
                pair_uuid=job.pair_uuid,
                zone_id=job.zone_id,
                before_image=str(before_image),
                after_image=str(after_image),
                before_transform=before_transform,
                after_transform=after_transform,
                world_window=job.world_window.to_dict(),
                renderer_backend="pdf-image-crop",
                cache_key=cache_key,
                cache_hit=False,
                visual_fidelity="pdf_render",
                render_lifecycle="ready",
                warnings=warnings,
                request_id=job.request_id,
                reason_code=reason_code,
                elapsed_ms=round((time.perf_counter() - _render_start_perf) * 1000.0, 3),
                pdf_display_list_cache=pdf_display_list_cache,
            ))
        return _with_perf(
            _skipped_pdf_crop_result(
                job,
                meta_path=meta_path,
                cache_key=cache_key,
                warnings=warnings,
                render_start_perf=_render_start_perf,
            )
        )
    if not job.prefer_source_render and _can_render_background_image_crop(job):
        before_transform = transform_for_window(
            before_window,
            output_width=job.output_width,
            output_height=job.output_height,
            renderer_backend="cad-background-image-crop",
        )
        after_transform = transform_for_window(
            after_window,
            output_width=job.output_width,
            output_height=job.output_height,
            renderer_backend="cad-background-image-crop",
        )
        try:
            _render_background_image_crop(
                Path(job.before_background_image),
                before_image,
                before_window,
                before_transform,
                job.before_background_transform or {},
                warnings=warnings,
            )
            _render_background_image_crop(
                Path(job.after_background_image),
                after_image,
                after_window,
                after_transform,
                job.after_background_transform or {},
                warnings=warnings,
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "pair_uuid": job.pair_uuid,
                "zone_id": job.zone_id,
                "before_image": _cache_relative_path(before_image, job.cache_root),
                "after_image": _cache_relative_path(after_image, job.cache_root),
                "before_transform": before_transform,
                "after_transform": after_transform,
                "world_window": job.world_window.to_dict(),
                "renderer_backend": "cad-background-image-crop",
                "cache_key": cache_key,
                "visual_fidelity": "cad_render",
                "render_lifecycle": "ready",
                "reason_code": _zone_reason_code_from_warnings(warnings),
                "warnings": warnings,
                "request_id": job.request_id,
            }
            _write_json(meta_path, payload)
            return _with_perf(RenderResult(
                pair_uuid=job.pair_uuid,
                zone_id=job.zone_id,
                before_image=str(before_image),
                after_image=str(after_image),
                before_transform=before_transform,
                after_transform=after_transform,
                world_window=job.world_window.to_dict(),
                renderer_backend="cad-background-image-crop",
                cache_key=cache_key,
                cache_hit=False,
                visual_fidelity="cad_render",
                render_lifecycle="ready",
                warnings=warnings,
                request_id=job.request_id,
                reason_code=_zone_reason_code_from_warnings(warnings),
                elapsed_ms=round((time.perf_counter() - _render_start_perf) * 1000.0, 3),
            ))
        except Exception as exc:
            warnings.append(
                f"cad_background_crop:fallback_to_source:{type(exc).__name__}:{exc}"
            )
    before_transform = transform_for_window(
        before_window,
        output_width=job.output_width,
        output_height=job.output_height,
        renderer_backend=job.renderer_backend,
    )
    after_transform = transform_for_window(
        after_window,
        output_width=job.output_width,
        output_height=job.output_height,
        renderer_backend=job.renderer_backend,
    )
    index_cache_before = render_index_cache_stats()
    try:
        _render_source_crop(
            job.source_before,
            before_image,
            before_window,
            before_transform,
            dxf_cache_dir=job.dxf_cache_dir,
            render_environment_hash=job.render_environment_hash or job.font_manifest_hash or "unknown",
            warnings=warnings,
        )
        _render_source_crop(
            job.source_after,
            after_image,
            after_window,
            after_transform,
            dxf_cache_dir=job.dxf_cache_dir,
            render_environment_hash=job.render_environment_hash or job.font_manifest_hash or "unknown",
            warnings=warnings,
        )
    except Exception as exc:
        message = str(exc).replace("\n", " ").strip()
        suffix = f":{message}" if message else ""
        warnings.append(f"zone_render_fallback:source_render_failed:{type(exc).__name__}{suffix}")
        return _with_perf(
            _visible_fallback_result(
                job,
                before_image=before_image,
                after_image=after_image,
                before_transform=before_transform,
                after_transform=after_transform,
                meta_path=meta_path,
                cache_key=cache_key,
                warnings=warnings,
                reason_code="source_render_failed",
                render_start_perf=_render_start_perf,
                dxf_index_cache=_diff_index_cache_stats(
                    index_cache_before,
                    render_index_cache_stats(),
                ),
            )
        )
    dxf_index_cache = _diff_index_cache_stats(index_cache_before, render_index_cache_stats())
    # P0-2b — when a significant rigid alignment was supplied, warp the after
    # raster into the before frame and emit the marker-side transform. On any
    # failure this returns the unaligned after_transform + None (honest fallback,
    # warning appended) so the render never silently corrupts.
    after_transform, after_marker_world_transform = _apply_after_alignment(
        job, before_transform, after_transform, after_image, warnings
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "pair_uuid": job.pair_uuid,
        "zone_id": job.zone_id,
        "before_image": _cache_relative_path(before_image, job.cache_root),
        "after_image": _cache_relative_path(after_image, job.cache_root),
        "before_transform": before_transform,
        "after_transform": after_transform,
        "after_marker_world_transform": after_marker_world_transform,
        "world_window": job.world_window.to_dict(),
        "renderer_backend": job.renderer_backend,
        "cache_key": cache_key,
        "visual_fidelity": "cad_render",
        "render_lifecycle": "ready",
        "reason_code": "",
        "warnings": warnings,
        "request_id": job.request_id,
    }
    payload.update(_flatten_dxf_index_cache(dxf_index_cache))
    _write_json(meta_path, payload)
    return _with_perf(RenderResult(
        pair_uuid=job.pair_uuid,
        zone_id=job.zone_id,
        before_image=str(before_image),
        after_image=str(after_image),
        before_transform=before_transform,
        after_transform=after_transform,
        after_marker_world_transform=after_marker_world_transform,
        world_window=job.world_window.to_dict(),
        renderer_backend=job.renderer_backend,
        cache_key=cache_key,
        cache_hit=False,
        visual_fidelity="cad_render",
        render_lifecycle="ready",
        warnings=warnings,
        request_id=job.request_id,
        reason_code="",
        elapsed_ms=round((time.perf_counter() - _render_start_perf) * 1000.0, 3),
        dxf_index_cache=dxf_index_cache,
    ))


def _append_zone_perf_event(job: RenderJob, result: RenderResult) -> None:
    if job.perf_event_root is None:
        return
    try:
        append_perf_event(
            Path(job.perf_event_root) / "perf_events.jsonl",
            run_id=job.perf_run_id,
            pair_id=job.pair_uuid,
            stage="zone_render",
            event="completed",
            elapsed_ms=result.elapsed_ms,
            cache_namespace="zone_render",
            cache_key=result.cache_key,
            cache_hit=result.cache_hit,
            cache_hit_reason="existing_render_result" if result.cache_hit else "",
            cache_miss_reason="" if result.cache_hit else _zone_cache_miss_reason(result),
            warning_count=len(result.warnings),
            render_mode=result.render_lifecycle,
            fidelity=result.visual_fidelity,
            zone_id=job.zone_id,
            renderer_backend=result.renderer_backend,
            reason_code=result.reason_code,
            **_flatten_pdf_display_list_cache(result.pdf_display_list_cache),
            **_flatten_dxf_index_cache(result.dxf_index_cache),
        )
    except Exception:
        logger.debug("Failed to append zone render perf event", exc_info=True)


def _zone_cache_miss_reason(result: RenderResult) -> str:
    if result.reason_code:
        return result.reason_code
    if result.render_lifecycle.startswith("skipped_"):
        return result.render_lifecycle
    if any("outside_background_bounds" in warning for warning in result.warnings):
        return "outside_background_bounds"
    if any("outside_output_bounds" in warning for warning in result.warnings):
        return "outside_output_bounds"
    return "artifact_missing"


def _zone_reason_code_from_warnings(warnings: Sequence[str]) -> str:
    if any("renderer:pdf-pil-fallback" in warning for warning in warnings):
        return "pdf_pil_fallback"
    if any("outside_background_bounds" in warning for warning in warnings):
        return "outside_background_bounds"
    if any("outside_output_bounds" in warning for warning in warnings):
        return "outside_output_bounds"
    return ""


def _render_source_crop(
    source: Path,
    output_path: Path,
    window: WorldWindow,
    transform: dict[str, Any],
    *,
    dxf_cache_dir: Path,
    render_environment_hash: str,
    warnings: list[str],
) -> None:
    dxf_path = _normalize_dxf_source(source, dxf_cache_dir)
    render_index = get_drawing_render_index(dxf_path, render_environment_hash)
    # Plan §17 Phase B-3 (GPT Pro F3 follow-up) — capture entity-pre-filter
    # telemetry so reviewers see how much work the envelope filter is
    # actually saving on each render. ``_render_dxf_window`` returns
    # ``(visible_count, total_count, prefilter_skipped)`` so the caller
    # can surface them as warnings consumed by the validator + GUI.
    visible_count, total_count, prefilter_skipped, entities_skipped = _render_dxf_window(
        render_index, output_path, window, transform
    )
    if prefilter_skipped:
        warnings.append(
            f"dxf_prefilter:skipped:total_entities={total_count}"
        )
    else:
        warnings.append(
            f"dxf_prefilter:applied:visible_entities={visible_count}/"
            f"{total_count}"
        )
    if entities_skipped > 0:
        # Honest degradation — some entities could not be drawn (e.g. malformed
        # MULTILEADER from a DWG->DXF conversion). The crop still renders the rest
        # instead of blanking; surface the count so it is visible, not silent.
        warnings.append(f"dxf_render:entities_skipped:{entities_skipped}")
        logger.warning(
            "zone render skipped %d un-renderable entit%s in %s",
            entities_skipped,
            "y" if entities_skipped == 1 else "ies",
            output_path.name,
        )


def _can_render_background_image_crop(job: RenderJob) -> bool:
    """Return True when a pre-rendered CAD viewer background can be cropped.

    The viewer package already renders full before/after CAD PNG backgrounds.
    Reusing those images for selected-zone crops avoids re-opening a large DWG
    or DXF for every first zone click while keeping the same visual source the
    reviewer sees in the overview viewer.
    """

    if _requires_page_space_bbox(job.source_before) or _requires_page_space_bbox(job.source_after):
        return False
    if not job.before_background_image or not job.after_background_image:
        return False
    if not isinstance(job.before_background_transform, dict) or not isinstance(
        job.after_background_transform, dict
    ):
        return False
    try:
        return Path(job.before_background_image).exists() and Path(job.after_background_image).exists()
    except OSError:
        return False


def _render_background_image_crop(
    background_image: Path,
    output_path: Path,
    window: WorldWindow,
    output_transform: dict[str, Any],
    background_transform: dict[str, Any],
    *,
    warnings: list[str],
) -> None:
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Pillow is required for CAD background image crops") from exc

    with Image.open(background_image) as image:
        source = image.convert("RGB")
        source_width, source_height = source.size
        requested = _window_to_background_pixel_rect(window, background_transform, source_width, source_height)
        req_left, req_top, req_right, req_bottom = requested
        req_width = max(1e-9, req_right - req_left)
        req_height = max(1e-9, req_bottom - req_top)
        clip_left = max(0.0, min(float(source_width), req_left))
        clip_top = max(0.0, min(float(source_height), req_top))
        clip_right = max(0.0, min(float(source_width), req_right))
        clip_bottom = max(0.0, min(float(source_height), req_bottom))
        if clip_right <= clip_left or clip_bottom <= clip_top:
            _write_blank_crop(output_path, output_transform)
            warnings.append("cad_background_crop:outside_background_bounds")
            return

        output_width = max(1, int(output_transform.get("img_width") or DEFAULT_OUTPUT_SIZE[0]))
        output_height = max(1, int(output_transform.get("img_height") or DEFAULT_OUTPUT_SIZE[1]))
        dest_left = int(round((clip_left - req_left) / req_width * output_width))
        dest_top = int(round((clip_top - req_top) / req_height * output_height))
        dest_right = int(round((clip_right - req_left) / req_width * output_width))
        dest_bottom = int(round((clip_bottom - req_top) / req_height * output_height))
        dest_left = max(0, min(output_width, dest_left))
        dest_top = max(0, min(output_height, dest_top))
        dest_right = max(0, min(output_width, dest_right))
        dest_bottom = max(0, min(output_height, dest_bottom))
        if dest_right <= dest_left or dest_bottom <= dest_top:
            _write_blank_crop(output_path, output_transform)
            warnings.append("cad_background_crop:outside_output_bounds")
            return

        crop = source.crop(
            (
                int(round(clip_left)),
                int(round(clip_top)),
                int(round(clip_right)),
                int(round(clip_bottom)),
            )
        )
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        crop = crop.resize((dest_right - dest_left, dest_bottom - dest_top), resampling)
        canvas = Image.new("RGB", (output_width, output_height), "white")
        canvas.paste(crop, (dest_left, dest_top))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path)
        warnings.append("cad_background_crop:source=viewer_background")
        if (
            clip_left > req_left
            or clip_top > req_top
            or clip_right < req_right
            or clip_bottom < req_bottom
        ):
            warnings.append("cad_background_crop:clipped_to_background_bounds")


def _write_blank_crop(output_path: Path, output_transform: dict[str, Any]) -> None:
    from PIL import Image

    output_width = max(1, int(output_transform.get("img_width") or DEFAULT_OUTPUT_SIZE[0]))
    output_height = max(1, int(output_transform.get("img_height") or DEFAULT_OUTPUT_SIZE[1]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (output_width, output_height), "white").save(output_path)


def _window_to_background_pixel_rect(
    window: WorldWindow,
    transform: dict[str, Any],
    image_width: int,
    image_height: int,
) -> tuple[float, float, float, float]:
    min_x = float(transform.get("min_x", 0.0) or 0.0)
    min_y = float(transform.get("min_y", 0.0) or 0.0)
    scale_x = float(transform.get("scale_x") or 1.0)
    scale_y = float(transform.get("scale_y") or 1.0)
    height = float(transform.get("img_height") or image_height or 1)
    x1 = (window.xmin - min_x) * scale_x
    x2 = (window.xmax - min_x) * scale_x
    if str(transform.get("coordinate_space") or "").lower() == "image_pixels":
        y1 = (window.ymin - min_y) * scale_y
        y2 = (window.ymax - min_y) * scale_y
    else:
        y1 = height - ((window.ymax - min_y) * scale_y)
        y2 = height - ((window.ymin - min_y) * scale_y)
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _make_all_layers_visible(doc: Any) -> int:
    """Thaw and turn on every layer of a render-only doc.

    The comparison extracts entities regardless of layer on/off/freeze, so the
    zone inspection crop must render them too — a detected change on a frozen or
    off layer would otherwise produce a blank zoom. Returns the number of layers
    that were hidden (frozen or off) and have been made visible.
    """

    try:
        layers = doc.layers
    except Exception:  # pragma: no cover - defensive
        return 0
    changed = 0
    for layer in layers:
        try:
            hidden = bool(layer.is_frozen()) or bool(layer.is_off())
            if layer.is_frozen():
                layer.thaw()
            if layer.is_off():
                layer.on()
            if hidden:
                changed += 1
        except Exception:  # noqa: BLE001 — never let one bad layer abort the index
            continue
    return changed


def get_drawing_render_index(dxf_path: Path, render_environment_hash: str = "unknown") -> DrawingRenderIndex:
    global _INDEX_CACHE_TOTAL_ESTIMATED_BYTES
    global _INDEX_CACHE_HIT_COUNT
    global _INDEX_CACHE_MISS_COUNT

    from .dxf_renderer import RENDERER_AVAILABLE

    if not RENDERER_AVAILABLE:
        raise RuntimeError("DXF renderer dependencies are not available")

    from . import dxf_renderer as dxf_module

    dxf_path = Path(dxf_path)
    signature = file_signature(dxf_path)
    cache_key_payload = {
        "schema_version": SCHEMA_VERSION,
        "source": signature,
        "render_environment_hash": render_environment_hash,
    }
    cache_key = hashlib.sha256(json.dumps(cache_key_payload, sort_keys=True).encode("utf-8")).hexdigest()
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None:
        _INDEX_CACHE_HIT_COUNT += 1
        if cache_key in _INDEX_CACHE_ORDER:
            _INDEX_CACHE_ORDER.remove(cache_key)
        _INDEX_CACHE_ORDER.append(cache_key)
        return cached
    _INDEX_CACHE_MISS_COUNT += 1

    # Plan §15 Phase A-3 — capture build wall-time so the eviction policy
    # can keep expensive rebuilds in cache when capacity is tight.
    build_started = time.perf_counter()
    from .dxf_read import read_dxf_document_result

    read_result = read_dxf_document_result(dxf_path, ezdxf_module=dxf_module.ezdxf)
    read_warning = read_result.diagnostics.warning()
    if read_warning:
        logger.warning("Render index using sanitized DXF %s: %s", dxf_path, read_warning)
    doc = read_result.doc
    msp = doc.modelspace()
    # The diff extracts entities on ALL layers (incl. frozen/off), so the zone
    # inspection crop must draw them too. Otherwise a change on a frozen layer
    # (observed: a frozen revision layer "-230726 Rev.02") is detected and gets a
    # change zone, but the zoomed render is blank — "변경된 부분 확대가 안 보임".
    # This is a render-only doc (separate from the diff extraction), so making
    # every layer visible is safe and aligns the render with what the diff saw.
    _thawed = _make_all_layers_visible(doc)
    if _thawed:
        logger.info("zone render: made %d hidden layer(s) visible for %s", _thawed, dxf_path.name)
    bbox_cache = dxf_module.ezdxf_bbox.Cache()
    envelopes = _build_entity_envelopes(msp, bbox_cache)
    build_elapsed_ms = (time.perf_counter() - build_started) * 1000.0
    render_index = DrawingRenderIndex(
        dxf_path=dxf_path,
        source_signature=signature,
        render_environment_hash=render_environment_hash,
        doc=doc,
        modelspace=msp,
        bbox_cache=bbox_cache,
        envelopes=envelopes,
        entity_count=len(envelopes),
        render_time_ms=build_elapsed_ms,
    )
    render_index.estimated_bytes = _estimate_render_index_bytes(render_index)
    _INDEX_CACHE[cache_key] = render_index
    _INDEX_CACHE_ORDER.append(cache_key)
    _INDEX_CACHE_TOTAL_ESTIMATED_BYTES += int(render_index.estimated_bytes or 0)
    _evict_to_capacity(_resolve_max_cache_entries(), _resolve_index_cache_byte_limit())
    return render_index


def _build_entity_envelopes(msp: Any, cache: Any) -> list[EntityEnvelope]:
    from . import dxf_renderer as dxf_module

    envelopes: list[EntityEnvelope] = []
    for entity in msp:
        handle = str(getattr(getattr(entity, "dxf", None), "handle", "") or "")
        layer = str(getattr(getattr(entity, "dxf", None), "layer", "") or "")
        entity_type = str(entity.dxftype() if hasattr(entity, "dxftype") else type(entity).__name__)
        bbox: Optional[tuple[float, float, float, float]] = None
        try:
            box = dxf_module.ezdxf_bbox.extents([entity], cache=cache)
            if getattr(box, "has_data", False):
                min_pt, max_pt = box.extmin, box.extmax
                bbox = (float(min_pt.x), float(min_pt.y), float(max_pt.x), float(max_pt.y))
        except Exception:
            bbox = None
        envelopes.append(EntityEnvelope(handle=handle, bbox=bbox, layer=layer, entity_type=entity_type))
    return envelopes


# Plan §17 Phase B-3 — modelspaces smaller than this threshold render
# the entire layout without building the ``visible_handles`` set. The
# envelope-filter overhead exceeds the saving on tiny drawings; for
# anything larger the filter saves Matplotlib draw cost.
_DXF_PREFILTER_THRESHOLD = 200


_RESILIENT_FRONTEND_CLS: Any = None


def _resilient_frontend_class(dxf_module: Any) -> Any:
    """Memoized ``Frontend`` subclass that never lets one malformed entity blank
    the whole render.

    A converted-DWG DXF can contain entities ezdxf cannot draw (e.g. a
    MULTILEADER whose virtual MTEXT has an empty style name ->
    ``doc.styles.get("")`` -> ``DXFTableEntryError``). ``draw_layout`` is a single
    call, so without this the first such entity aborts the entire crop. The
    per-entity exception propagates through ``draw_entity``, so guarding that one
    method renders everything else and records what was skipped (surfaced as a
    warning + failure code — honest degradation, never a silent blank).
    """
    global _RESILIENT_FRONTEND_CLS
    if _RESILIENT_FRONTEND_CLS is None:

        class _ResilientFrontend(dxf_module.Frontend):  # type: ignore[name-defined,misc]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.skipped_handles: list[str] = []

            def draw_entity(self, entity: Any, properties: Any) -> None:  # type: ignore[override]
                try:
                    super().draw_entity(entity, properties)
                except Exception as exc:  # noqa: BLE001 — render-resilience guard
                    handle = getattr(getattr(entity, "dxf", None), "handle", None)
                    self.skipped_handles.append(str(handle) if handle is not None else "?")
                    logger.debug(
                        "skipped un-renderable %s (handle=%s): %s",
                        getattr(entity, "dxftype", lambda: "?")(),
                        handle,
                        type(exc).__name__,
                    )

        _RESILIENT_FRONTEND_CLS = _ResilientFrontend
    return _RESILIENT_FRONTEND_CLS


def _render_dxf_window(
    render_index: DrawingRenderIndex,
    output_path: Path,
    window: WorldWindow,
    transform: dict[str, Any],
) -> tuple[int, int, bool, int]:
    """Render the DXF window. Returns

        (visible_entity_count, total_entity_count, prefilter_skipped, entities_skipped)

    so callers can surface entity-pre-filter telemetry. ``prefilter_skipped`` is
    True when the modelspace is below the skip threshold and the full layout was
    drawn without a filter. ``entities_skipped`` counts entities that raised while
    drawing and were skipped to avoid blanking the whole crop (see
    :func:`_resilient_frontend_class`).
    """
    from .dxf_renderer import RENDERER_AVAILABLE

    if not RENDERER_AVAILABLE:
        raise RuntimeError("DXF renderer dependencies are not available")

    import numpy as np
    from PIL import Image

    from . import dxf_renderer as dxf_module

    doc = render_index.doc
    msp = render_index.modelspace
    total_count = len(render_index.envelopes)
    # Plan §17 Phase B-3 — skip the envelope filter when the modelspace
    # is small. The filter callback's per-entity overhead exceeds the
    # Matplotlib draw saving below the threshold; above it the filter
    # cuts the draw cost roughly proportional to visible/total.
    if total_count < _DXF_PREFILTER_THRESHOLD:
        visible_handles: set[str] = set()
        prefilter_skipped = True
        filter_func = None
    else:
        visible_handles = visible_handles_for_window(render_index, window)
        prefilter_skipped = False

        def filter_func(entity: Any) -> bool:  # type: ignore[misc]
            handle = getattr(getattr(entity, "dxf", None), "handle", None)
            return str(handle) in visible_handles

    fig = dxf_module.plt.figure(
        figsize=(int(transform["img_width"]) / 100.0, int(transform["img_height"]) / 100.0),
        dpi=100,
    )
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("#FFFFFF")
    try:
        context = dxf_module.RenderContext(doc)
        backend = dxf_module.MatplotlibBackend(ax)
        from ezdxf.addons.drawing import config as drawing_config

        render_config = drawing_config.Configuration(
            color_policy=drawing_config.ColorPolicy.BLACK,
            background_policy=drawing_config.BackgroundPolicy.WHITE,
        )

        frontend = _resilient_frontend_class(dxf_module)(
            context, backend, config=render_config
        )
        if filter_func is None:
            frontend.draw_layout(msp, finalize=False)
        else:
            frontend.draw_layout(msp, finalize=False, filter_func=filter_func)
        entities_skipped = len(getattr(frontend, "skipped_handles", ()))
        ax.set_xlim(window.xmin, window.xmax)
        ax.set_ylim(window.ymin, window.ymax)
        ax.set_aspect("equal", adjustable="box")
        ax.axis("off")
        fig.canvas.draw()
        image = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(image).save(output_path)
    finally:
        dxf_module.plt.close(fig)

    return len(visible_handles), total_count, prefilter_skipped, entities_skipped


def visible_handles_for_window(render_index: DrawingRenderIndex, window: WorldWindow) -> set[str]:
    handles: set[str] = set()
    window_tuple = (window.xmin, window.ymin, window.xmax, window.ymax)
    for envelope in render_index.envelopes:
        if envelope.bbox is None:
            # Keep difficult entities visible rather than dropping possible context.
            handles.add(envelope.handle)
            continue
        if _boxes_intersect(envelope.bbox, window_tuple):
            handles.add(envelope.handle)
    return handles


def _boxes_intersect(a: Sequence[float], b: Sequence[float]) -> bool:
    return not (float(a[2]) < float(b[0]) or float(a[0]) > float(b[2]) or float(a[3]) < float(b[1]) or float(a[1]) > float(b[3]))


def _can_render_pdf_image_crop(job: RenderJob) -> bool:
    if not job.before_background_image or not job.after_background_image:
        return False
    if not Path(job.before_background_image).exists() or not Path(job.after_background_image).exists():
        return False
    before_space = str((job.before_background_transform or {}).get("coordinate_space") or "").lower()
    after_space = str((job.after_background_transform or {}).get("coordinate_space") or "").lower()
    return before_space == "image_pixels" and after_space == "image_pixels"


def _skipped_pdf_crop_result(
    job: RenderJob,
    *,
    meta_path: Path,
    cache_key: str,
    warnings: list[str],
    # Plan §17 Phase B-1 — forwarded so the skipped branch still produces
    # a real elapsed_ms (defaults to 0.0 for legacy callers that did not
    # measure their entry point).
    render_start_perf: float | None = None,
) -> RenderResult:
    warnings.append("PDF page-space bbox/background is unavailable; selected-zone PDF crop was skipped.")
    before_image = meta_path.parent / f"{_safe_name(job.zone_id)}_before.png"
    after_image = meta_path.parent / f"{_safe_name(job.zone_id)}_after.png"
    before_transform = transform_for_window(
        job.world_window,
        output_width=job.output_width,
        output_height=job.output_height,
        renderer_backend="relative-overlay-fallback",
    )
    after_transform = dict(before_transform)
    _write_blank_crop(before_image, before_transform)
    _write_blank_crop(after_image, after_transform)
    skipped_payload = {
        "schema_version": SCHEMA_VERSION,
        "pair_uuid": job.pair_uuid,
        "zone_id": job.zone_id,
        "before_image": _cache_relative_path(before_image, job.cache_root),
        "after_image": _cache_relative_path(after_image, job.cache_root),
        "before_transform": before_transform,
        "after_transform": after_transform,
        "world_window": job.world_window.to_dict(),
        "renderer_backend": "pdf-page-bbox-required",
        "cache_key": cache_key,
        "visual_fidelity": "relative_overlay",
        "render_lifecycle": "skipped_missing_page_bbox",
        "reason_code": "missing_page_bbox",
        "warnings": warnings,
        "request_id": job.request_id,
    }
    _write_json(meta_path, skipped_payload)
    elapsed_ms = (
        round((time.perf_counter() - render_start_perf) * 1000.0, 3)
        if render_start_perf is not None
        else 0.0
    )
    return RenderResult(
        pair_uuid=job.pair_uuid,
        zone_id=job.zone_id,
        before_image=str(before_image),
        after_image=str(after_image),
        before_transform=before_transform,
        after_transform=after_transform,
        world_window=job.world_window.to_dict(),
        renderer_backend="pdf-page-bbox-required",
        cache_key=cache_key,
        cache_hit=False,
        visual_fidelity="relative_overlay",
        render_lifecycle="skipped_missing_page_bbox",
        warnings=warnings,
        request_id=job.request_id,
        reason_code="missing_page_bbox",
        elapsed_ms=elapsed_ms,
    )


def _visible_fallback_result(
    job: RenderJob,
    *,
    before_image: Path,
    after_image: Path,
    before_transform: dict[str, Any],
    after_transform: dict[str, Any],
    meta_path: Path,
    cache_key: str,
    warnings: list[str],
    reason_code: str,
    render_start_perf: float,
    dxf_index_cache: Optional[dict[str, Any]] = None,
) -> RenderResult:
    fallback_before_transform = dict(before_transform)
    fallback_after_transform = dict(after_transform)
    fallback_before_transform["renderer_backend"] = "relative-overlay-fallback"
    fallback_after_transform["renderer_backend"] = "relative-overlay-fallback"
    _write_blank_crop(before_image, fallback_before_transform)
    _write_blank_crop(after_image, fallback_after_transform)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "pair_uuid": job.pair_uuid,
        "zone_id": job.zone_id,
        "before_image": _cache_relative_path(before_image, job.cache_root),
        "after_image": _cache_relative_path(after_image, job.cache_root),
        "before_transform": fallback_before_transform,
        "after_transform": fallback_after_transform,
        "world_window": job.world_window.to_dict(),
        "renderer_backend": "relative-overlay-fallback",
        "cache_key": cache_key,
        "visual_fidelity": "relative_overlay",
        "render_lifecycle": "fallback_visible",
        "reason_code": reason_code,
        "warnings": warnings,
        "request_id": job.request_id,
    }
    payload.update(_flatten_dxf_index_cache(dxf_index_cache))
    _write_json(meta_path, payload)
    return RenderResult(
        pair_uuid=job.pair_uuid,
        zone_id=job.zone_id,
        before_image=str(before_image),
        after_image=str(after_image),
        before_transform=fallback_before_transform,
        after_transform=fallback_after_transform,
        world_window=job.world_window.to_dict(),
        renderer_backend="relative-overlay-fallback",
        cache_key=cache_key,
        cache_hit=False,
        visual_fidelity="relative_overlay",
        render_lifecycle="fallback_visible",
        warnings=warnings,
        request_id=job.request_id,
        reason_code=reason_code,
        elapsed_ms=round((time.perf_counter() - render_start_perf) * 1000.0, 3),
        dxf_index_cache=dxf_index_cache or {},
    )


def _render_pdf_image_crop(
    background_image: Path,
    output_path: Path,
    window: WorldWindow,
    transform: dict[str, Any],
    *,
    warnings: list[str],
    source_pdf: Optional[Path] = None,
    page_index: int = 0,
    background_img_width: Optional[int] = None,
    background_img_height: Optional[int] = None,
) -> dict[str, Any]:
    """Render the requested zone crop into ``output_path``.

    Plan §17 Phase B-1b — GPT Pro F3 (HIGH) follow-up. When the source
    PDF is available we read the clip region directly from the cached
    per-page :class:`fitz.DisplayList` via
    ``src.services.comparison.pdf_display_list_cache``. The legacy
    full-page PIL read remains as a fallback for the case where the
    source PDF has been moved/renamed (and for the synthetic
    fixtures used by the existing pdf-image-crop tests, which create a
    blank PNG but no real PDF).

    Parameters
    ----------
    background_image:
        Pre-rendered PNG of the full page. Used as the PIL fallback
        source and as a sanity check that the page was actually
        rendered earlier in the pipeline.
    output_path:
        Destination PNG for the zone crop.
    window:
        The crop window in image-pixel coordinates of the pre-rendered
        page (matches the ``image_pixels`` ``coordinate_space`` on the
        background transform).
    transform:
        The new output transform built by
        :func:`transform_for_image_pixel_window`.
    warnings:
        In-place list. We append a single ``renderer:`` marker so
        downstream telemetry can distinguish the DisplayList path from
        the PIL fallback.
    source_pdf:
        Path to the source PDF on disk. Required for the DisplayList
        path; ``None`` forces the PIL fallback.
    page_index:
        Zero-based PDF page index for the DisplayList lookup. Defaults
        to ``0`` for single-page PDFs and for callers that don't track
        page indices.
    background_img_width, background_img_height:
        Dimensions of the pre-rendered background PNG in pixels.
        Required when ``source_pdf`` is supplied so we can map the
        image-pixel ``window`` into PDF page-space points. When
        absent, the function probes the background PNG via PIL — a
        small extra cost vs. the PIL fallback but still avoided in the
        normal call path which threads the dimensions in from
        ``before_background_transform`` / ``after_background_transform``.
    """

    # -----------------------------------------------------------------
    # DisplayList fast path — only attempted when:
    #   1. source_pdf is provided AND exists,
    #   2. PyMuPDF imports successfully,
    #   3. we can compute the background pixel dimensions (so we can
    #      map image-pixel window -> page-space clip).
    # On any failure we fall through to the legacy PIL path with a
    # warning so the caller can still surface the result.
    # -----------------------------------------------------------------
    used_display_list = False
    fallback_reason = ""
    if source_pdf is not None and Path(source_pdf).exists():
        try:
            from .pdf_display_list_cache import (
                get_display_list_entry,
                render_clip_to_png,
            )

            bg_w = int(background_img_width or 0)
            bg_h = int(background_img_height or 0)
            if bg_w <= 0 or bg_h <= 0:
                # Slow path — probe the background. Still cheaper than
                # decoding the full RGB image and cropping it because
                # PIL only reads the header here.
                try:
                    from PIL import Image as _PILImage

                    with _PILImage.open(background_image) as _probe:
                        bg_w, bg_h = int(_probe.width), int(_probe.height)
                except Exception:
                    bg_w = bg_h = 0

            if bg_w > 0 and bg_h > 0:
                display_list, page_w, page_h, cache_stats = get_display_list_entry(
                    Path(source_pdf),
                    int(page_index),
                )
                # image_pixels -> page_points scale. The pre-rendered
                # PNG covers exactly the page rect, so the scale is
                # uniform on each axis. We don't assume the two axes
                # share a scale (handles non-square pixmaps).
                scale_x = page_w / float(bg_w)
                scale_y = page_h / float(bg_h)

                # Clamp window to background bounds in image-pixel
                # space first, then map to PDF points. Mirrors the
                # PIL path's clip-to-bounds behaviour.
                clip_left_px = max(0.0, float(window.xmin))
                clip_top_px = max(0.0, float(window.ymin))
                clip_right_px = min(float(bg_w), float(window.xmax))
                clip_bottom_px = min(float(bg_h), float(window.ymax))
                if clip_right_px <= clip_left_px or clip_bottom_px <= clip_top_px:
                    raise ValueError(
                        "PDF crop window is outside the rendered page image"
                    )

                clip_left = clip_left_px * scale_x
                clip_top = clip_top_px * scale_y
                clip_right = clip_right_px * scale_x
                clip_bottom = clip_bottom_px * scale_y

                # Pick a render scale that produces roughly the same
                # number of output pixels as the destination so the
                # downstream paste does not waste work upscaling /
                # downscaling. ``transform["img_width"]`` is the
                # destination canvas, ``clip_right_px - clip_left_px``
                # is the source span; the ratio recovers the effective
                # display scale.
                dest_w_px = max(1.0, float(transform.get("img_width", 1)))
                source_w_px = max(1.0, clip_right_px - clip_left_px)
                # Multiply by (bg_pixels per page_point) to convert the
                # destination-per-source ratio into a page-points scale
                # PyMuPDF expects.
                effective_dpi_scale = max(0.25, (dest_w_px / source_w_px) * (float(bg_w) / page_w))
                # Hard-cap to keep memory bounded on absurdly large
                # zooms; 4.0 ≈ 288 DPI which is plenty for inspection.
                effective_dpi_scale = min(effective_dpi_scale, 4.0)

                # Render the clip directly. The output is the raw clip
                # at the resolved scale — we still need to letterbox
                # it onto the requested output_width x output_height
                # canvas to preserve the destination coordinate space,
                # which the existing PIL path does via ``target.paste``.
                tmp_clip_png = output_path.with_suffix(".clip.tmp.png")
                clip_result: dict[str, Any] = {}
                try:
                    clip_result = render_clip_to_png(
                        display_list,
                        (clip_left, clip_top, clip_right, clip_bottom),
                        tmp_clip_png,
                        scale=effective_dpi_scale,
                    )

                    from PIL import Image

                    # Letterbox the clip onto the destination canvas
                    # using the same dest rectangle the PIL path
                    # produces. We re-use ``bbox_to_pixel_rect`` to
                    # stay symmetric with the legacy mapping.
                    target = Image.new(
                        "RGB",
                        (int(transform["img_width"]), int(transform["img_height"])),
                        "white",
                    )
                    dest = bbox_to_pixel_rect(
                        [clip_left_px, clip_top_px, clip_right_px, clip_bottom_px],
                        transform,
                    )
                    if not dest:
                        raise ValueError(
                            "PDF crop window could not be mapped into output pixels"
                        )
                    dest_width = max(1, int(round(dest["width"])))
                    dest_height = max(1, int(round(dest["height"])))
                    resampling = getattr(
                        getattr(Image, "Resampling", Image), "LANCZOS"
                    )
                    with Image.open(tmp_clip_png) as raw_clip:
                        clip_rgb = raw_clip.convert("RGB")
                        target.paste(
                            clip_rgb.resize((dest_width, dest_height), resampling),
                            (int(round(dest["x"])), int(round(dest["y"]))),
                        )
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    target.save(output_path)
                finally:
                    try:
                        tmp_clip_png.unlink()
                    except OSError:
                        pass

                warnings.append("renderer:pdf-display-list-clip")
                used_display_list = True
                if (
                    clip_left_px > window.xmin
                    or clip_top_px > window.ymin
                    or clip_right_px < window.xmax
                    or clip_bottom_px < window.ymax
                ):
                    warnings.append("PDF crop was clipped to rendered page bounds.")
        except ImportError:
            # PyMuPDF unavailable — silently fall through to PIL.
            fallback_reason = "ImportError"
        except (FileNotFoundError, IndexError, ValueError, RuntimeError) as exc:
            # IndexError = bad page_index; ValueError = empty clip /
            # mapping failure; RuntimeError = thread-safety guard.
            # In all of these the PIL fallback below produces a
            # usable, if slower, result.
            fallback_reason = type(exc).__name__
        except Exception as exc:
            # Defensive net — any unexpected fitz failure must not
            # break the render. We log at debug because the fallback
            # still produces a valid crop.
            fallback_reason = type(exc).__name__
            logger.debug(
                "DisplayList PDF crop failed for %s page=%s: %s; "
                "falling back to PIL",
                source_pdf, page_index, exc,
            )

    if used_display_list:
        return {
            "used_display_list": True,
            "pil_fallback": False,
            "lookup_cache_hit": bool(cache_stats.get("lookup_cache_hit")),
            "entries": _safe_int(cache_stats.get("entries")),
            "capacity_entries": _safe_int(cache_stats.get("capacity_entries", cache_stats.get("capacity"))),
            "byte_limit": _safe_int(cache_stats.get("byte_limit")),
            "total_estimated_bytes": _safe_int(cache_stats.get("total_estimated_bytes")),
            "entry_estimated_bytes": _safe_int(cache_stats.get("entry_estimated_bytes")),
            "hit_count": _safe_int(cache_stats.get("hit_count")),
            "miss_count": _safe_int(cache_stats.get("miss_count")),
            "eviction_count": _safe_int(cache_stats.get("eviction_count")),
            "evicted_estimated_bytes": _safe_int(cache_stats.get("evicted_estimated_bytes")),
            "process_rss_mb": _safe_float(cache_stats.get("process_rss_mb")),
            "render_wall_ms": _safe_float(clip_result.get("wall_ms")),
            "output_bytes": _safe_int(clip_result.get("output_bytes")),
        }

    # -----------------------------------------------------------------
    # Legacy PIL fallback — open the full-page PNG, crop, paste.
    # Kept untouched (modulo the renderer warning) so existing tests
    # that fabricate a background PNG without a real source PDF keep
    # passing.
    # -----------------------------------------------------------------
    from PIL import Image

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(background_image) as raw_image:
        image = raw_image.convert("RGB")
        left = max(0, int(window.xmin))
        top = max(0, int(window.ymin))
        right = min(image.width, int(window.xmax + 0.999999))
        bottom = min(image.height, int(window.ymax + 0.999999))
        if right <= left or bottom <= top:
            raise ValueError("PDF crop window is outside the rendered page image")
        crop = image.crop((left, top, right, bottom))
        target = Image.new("RGB", (int(transform["img_width"]), int(transform["img_height"])), "white")
        dest = bbox_to_pixel_rect([left, top, right, bottom], transform)
        if not dest:
            raise ValueError("PDF crop window could not be mapped into output pixels")
        dest_width = max(1, int(round(dest["width"])))
        dest_height = max(1, int(round(dest["height"])))
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        target.paste(
            crop.resize((dest_width, dest_height), resampling),
            (int(round(dest["x"])), int(round(dest["y"]))),
        )
        target.save(output_path)
    warnings.append("renderer:pdf-pil-fallback")
    if left > window.xmin or top > window.ymin or right < window.xmax or bottom < window.ymax:
        warnings.append("PDF crop was clipped to rendered page bounds.")
    return {
        "used_display_list": False,
        "pil_fallback": True,
        "fallback_reason": fallback_reason or "display_list_unavailable",
    }


def _render_pdf_placeholder(source: Path, output_path: Path, transform: dict[str, Any], *, warnings: list[str]) -> None:
    """PDF is a direct visual path; use full first-page render until page-space ROI is available."""

    from .viewer_package import _render_pdf_to_png

    warnings.append("PDF zone crop uses first-page visual fallback; CAD world crop is not applied.")
    _render_pdf_to_png(
        source,
        output_path,
        dpi=96,
        max_edge_px=max(int(transform.get("img_width", 1600)), int(transform.get("img_height", 900))),
    )
    transform.update({"coordinate_space": "image_pixels", "renderer_backend": "pdf-first-page-fallback"})


def _normalize_dxf_source(source: Path, dxf_cache_dir: Path) -> Path:
    suffix = source.suffix.lower()
    if suffix == ".dwg":
        from .review_project import _ensure_preview_dxf

        return _ensure_preview_dxf(source, dxf_cache_dir)
    if suffix == ".dxf":
        return source
    raise ValueError(f"Unsupported selected-zone CAD render source: {source}")


def _requires_page_space_bbox(source: Path) -> bool:
    return Path(source).suffix.lower() == ".pdf"


def _font_support_dirs_from_env() -> list[Path]:
    values: list[str] = []
    for key in ("DRAWING_COMPARE_FONT_DIRS", "EZDXF_FONT_DIRS", "EZDXF_SHX_PATH"):
        raw = str(os.environ.get(key, "") or "")
        if raw:
            values.extend(part for part in raw.split(os.pathsep) if part)
    return [Path(value) for value in values]


def _directory_signature(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    if path.is_file():
        try:
            stat = path.stat()
            return {"path": str(path.resolve()), "exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        except OSError:
            return {"path": str(path), "exists": True, "error": "stat_failed"}
    entries: list[dict[str, Any]] = []
    try:
        for child in sorted(path.iterdir(), key=lambda item: item.name.lower()):
            if child.suffix.lower() not in {".shx", ".ttf", ".otf", ".ctb", ".stb"}:
                continue
            try:
                stat = child.stat()
                entries.append({"name": child.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
            except OSError:
                entries.append({"name": child.name, "error": "stat_failed"})
    except OSError:
        return {"path": str(path), "exists": True, "error": "list_failed"}
    return {"path": str(path.resolve()), "exists": True, "entries": entries[:500]}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="surrogatepass"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # surrogatepass keeps Korean Windows paths (lone CP949<->UTF-16 surrogates)
    # from raising "surrogates not allowed" on write; _read_json matches it.
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", errors="surrogatepass"
    )
    tmp.replace(path)


def _cache_relative_path(path: Path, cache_root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(cache_root).resolve()).as_posix()
    except Exception:
        return Path(path).name


def _safe_name(value: str) -> str:
    """Sanitise a user-supplied identifier into a safe filesystem name.

    Plan §19 A-4 (Agent T finding T4) — the previous implementation
    allowed ``.`` and ``-`` characters, which meant ``pair_uuid=".."``
    survived as-is and could escape ``cache_root`` when joined into a
    path. This version:

    1. Maps any non ``[a-zA-Z0-9_-]`` char to ``_`` (drop ``.``).
    2. Strips leading dots/hyphens so the result cannot be a
       Unix-hidden file or look like a CLI flag to downstream tools.
    3. Caps the length at 120 chars.
    4. Falls back to ``"item"`` if the result is empty after the
       sanitisation above.
    """
    raw = str(value or "")
    cleaned = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in raw)
    cleaned = cleaned.lstrip("_-")
    cleaned = cleaned[:120]
    return cleaned or "item"
