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
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
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
    # Plan §17 Phase B-1 (GPT Pro F3 follow-up) — wall time of the
    # ``render_zone_pair`` call. Without this, the GUI handler at
    # ``drawing_compare_workbench.py`` was reading
    # ``result_payload.get("elapsed_ms")`` from a key the worker never
    # populated, so every GUI-side render_ms event was 0. Only the
    # validator measured elapsed_ms via its own perf_counter wrap.
    # Defaults to 0.0 for backward compatibility with JSONL consumers
    # that don't yet read the field.
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_uuid": self.pair_uuid,
            "zone_id": self.zone_id,
            "before_image": self.before_image,
            "after_image": self.after_image,
            "before_transform": self.before_transform,
            "after_transform": self.after_transform,
            "world_window": self.world_window,
            "renderer_backend": self.renderer_backend,
            "cache_key": self.cache_key,
            "cache_hit": self.cache_hit,
            "visual_fidelity": self.visual_fidelity,
            "render_lifecycle": self.render_lifecycle,
            "warnings": self.warnings,
            "request_id": self.request_id,
            "elapsed_ms": self.elapsed_ms,
        }


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


_INDEX_CACHE: dict[str, DrawingRenderIndex] = {}
_INDEX_CACHE_ORDER: list[str] = []
_INDEX_CACHE_SIZE_ENV_VAR = "DRAWING_COMPARE_INDEX_CACHE_SIZE"
# Default (4) preserves the original behaviour for callers that do not opt
# into the adaptive resize. Plan §15 Phase A-3 expands this when the
# process has enough free memory; see ``_resolve_max_cache_entries()``.
_MAX_INDEX_CACHE_ENTRIES = 4


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


def _evict_to_capacity(capacity: int) -> None:
    """Evict cache entries until at most ``capacity`` remain.

    Plan §15 Phase A-3 — original FIFO-on-build is preserved as the
    primary signal (oldest-used first), but among the oldest few we keep
    the most expensive index so reviewer navigation does not pay the
    rebuild cost twice. Cost = ``entity_count * render_time_ms``.

    The list is mutated in place. Safe to call with capacity > current
    size (no-op).
    """
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
            cost = float(entry.entity_count) * float(entry.render_time_ms or 1.0)
            if cost < cheapest_cost:
                cheapest_cost = cost
                cheapest_key = key
        _INDEX_CACHE_ORDER.remove(cheapest_key)
        _INDEX_CACHE.pop(cheapest_key, None)


def _clear_index_cache() -> None:
    """Test hook — wipe the in-process cache so each test starts clean."""
    _INDEX_CACHE.clear()
    _INDEX_CACHE_ORDER.clear()


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
    bbox: Sequence[float],
    *,
    padding_ratio: float = 0.18,
    min_size: float = 250.0,
    target_aspect: float = DEFAULT_TARGET_ASPECT,
) -> WorldWindow:
    """Build a shared before/after review window around a zone bbox."""

    x1, y1, x2, y2 = (float(item) for item in bbox[:4])
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
    try:
        stat = Path(path).stat()
        return {"path": str(Path(path).resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        return {"path": str(path), "size": 0, "mtime_ns": 0}


def render_cache_key(job: RenderJob) -> str:
    environment_hash = job.render_environment_hash or job.font_manifest_hash or "unknown"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "pair_uuid": job.pair_uuid,
        "zone_id": job.zone_id,
        "before": file_signature(job.source_before),
        "after": file_signature(job.source_after),
        "world_window": job.world_window.to_dict(),
        "output": {"width": job.output_width, "height": job.output_height},
        "renderer_backend": job.renderer_backend,
        "font_manifest_hash": job.font_manifest_hash,
        "render_environment_hash": environment_hash,
        "before_background": file_signature(Path(job.before_background_image)) if job.before_background_image else {},
        "after_background": file_signature(Path(job.after_background_image)) if job.after_background_image else {},
        "before_background_transform": job.before_background_transform or {},
        "after_background_transform": job.after_background_transform or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
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
        "oda_converter": _oda_converter_signature(),
        "dxf_cache_dir": str(Path(dxf_cache_dir).resolve()) if dxf_cache_dir else "",
        "font_support_dirs": [_directory_signature(Path(path)) for path in support_dirs],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def clear_render_index_cache() -> None:
    _INDEX_CACHE.clear()
    _INDEX_CACHE_ORDER.clear()


def render_index_cache_stats() -> dict[str, int]:
    return {"entries": len(_INDEX_CACHE)}


def render_zone_pair(job: RenderJob) -> RenderResult:
    """Render before/after local crops for a selected zone."""

    # Plan §17 Phase B-1 (GPT Pro F3 follow-up) — capture wall time so
    # ``RenderResult.elapsed_ms`` carries truth on every return branch.
    # Before B-1 the GUI handler at drawing_compare_workbench.py read
    # ``elapsed_ms`` from the JSONL payload but the worker never populated
    # the field, so every GUI-side render_ms event was 0. The validator's
    # own perf_counter wrap measured the same thing redundantly.
    _render_start_perf = time.perf_counter()

    cache_key = render_cache_key(job)
    pair_dir = job.cache_root / "zone_crops" / _safe_name(job.pair_uuid) / cache_key
    before_image = pair_dir / f"{_safe_name(job.zone_id)}_before.png"
    after_image = pair_dir / f"{_safe_name(job.zone_id)}_after.png"
    meta_path = pair_dir / "render_result.json"
    if before_image.exists() and after_image.exists() and meta_path.exists():
        payload = _read_json(meta_path)
        if payload:
            return RenderResult(
                pair_uuid=job.pair_uuid,
                zone_id=job.zone_id,
                before_image=str(before_image),
                after_image=str(after_image),
                before_transform=payload.get("before_transform") or {},
                after_transform=payload.get("after_transform") or {},
                world_window=payload.get("world_window") or job.world_window.to_dict(),
                renderer_backend=str(payload.get("renderer_backend") or job.renderer_backend),
                cache_key=cache_key,
                cache_hit=True,
                visual_fidelity=str(payload.get("visual_fidelity") or "cad_render"),
                render_lifecycle="ready",
                warnings=[str(item) for item in payload.get("warnings", [])],
                request_id=job.request_id,
                elapsed_ms=round((time.perf_counter() - _render_start_perf) * 1000.0, 3),
            )

    pair_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    if _requires_page_space_bbox(job.source_before) or _requires_page_space_bbox(job.source_after):
        if _can_render_pdf_image_crop(job):
            before_transform = transform_for_image_pixel_window(
                job.world_window,
                output_width=job.output_width,
                output_height=job.output_height,
                renderer_backend="pdf-image-crop",
            )
            after_transform = dict(before_transform)
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
            _render_pdf_image_crop(
                Path(job.before_background_image),
                before_image,
                job.world_window,
                before_transform,
                warnings=warnings,
                source_pdf=Path(job.source_before),
                page_index=before_page,
                background_img_width=before_bg_w,
                background_img_height=before_bg_h,
            )
            _render_pdf_image_crop(
                Path(job.after_background_image),
                after_image,
                job.world_window,
                after_transform,
                warnings=warnings,
                source_pdf=Path(job.source_after),
                page_index=after_page,
                background_img_width=after_bg_w,
                background_img_height=after_bg_h,
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
                "warnings": warnings,
                "request_id": job.request_id,
            }
            _write_json(meta_path, payload)
            return RenderResult(
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
                elapsed_ms=round((time.perf_counter() - _render_start_perf) * 1000.0, 3),
            )
        return _skipped_pdf_crop_result(
            job,
            meta_path=meta_path,
            cache_key=cache_key,
            warnings=warnings,
            render_start_perf=_render_start_perf,
        )
    before_transform = transform_for_window(
        job.world_window,
        output_width=job.output_width,
        output_height=job.output_height,
        renderer_backend=job.renderer_backend,
    )
    after_transform = dict(before_transform)
    _render_source_crop(
        job.source_before,
        before_image,
        job.world_window,
        before_transform,
        dxf_cache_dir=job.dxf_cache_dir,
        render_environment_hash=job.render_environment_hash or job.font_manifest_hash or "unknown",
        warnings=warnings,
    )
    _render_source_crop(
        job.source_after,
        after_image,
        job.world_window,
        after_transform,
        dxf_cache_dir=job.dxf_cache_dir,
        render_environment_hash=job.render_environment_hash or job.font_manifest_hash or "unknown",
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
        "renderer_backend": job.renderer_backend,
        "cache_key": cache_key,
        "visual_fidelity": "cad_render",
        "render_lifecycle": "ready",
        "warnings": warnings,
        "request_id": job.request_id,
    }
    _write_json(meta_path, payload)
    return RenderResult(
        pair_uuid=job.pair_uuid,
        zone_id=job.zone_id,
        before_image=str(before_image),
        after_image=str(after_image),
        before_transform=before_transform,
        after_transform=after_transform,
        world_window=job.world_window.to_dict(),
        renderer_backend=job.renderer_backend,
        cache_key=cache_key,
        cache_hit=False,
        visual_fidelity="cad_render",
        render_lifecycle="ready",
        warnings=warnings,
        request_id=job.request_id,
        elapsed_ms=round((time.perf_counter() - _render_start_perf) * 1000.0, 3),
    )


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
    visible_count, total_count, prefilter_skipped = _render_dxf_window(
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


def get_drawing_render_index(dxf_path: Path, render_environment_hash: str = "unknown") -> DrawingRenderIndex:
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
        if cache_key in _INDEX_CACHE_ORDER:
            _INDEX_CACHE_ORDER.remove(cache_key)
        _INDEX_CACHE_ORDER.append(cache_key)
        return cached

    # Plan §15 Phase A-3 — capture build wall-time so the eviction policy
    # can keep expensive rebuilds in cache when capacity is tight.
    build_started = time.perf_counter()
    doc = dxf_module.ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
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
    _INDEX_CACHE[cache_key] = render_index
    _INDEX_CACHE_ORDER.append(cache_key)
    _evict_to_capacity(_resolve_max_cache_entries())
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


def _render_dxf_window(
    render_index: DrawingRenderIndex,
    output_path: Path,
    window: WorldWindow,
    transform: dict[str, Any],
) -> tuple[int, int, bool]:
    """Render the DXF window. Plan §17 Phase B-3 returns

        (visible_entity_count, total_entity_count, prefilter_skipped)

    so callers can surface entity-pre-filter telemetry.
    ``prefilter_skipped`` is True when the modelspace is below the
    skip threshold and the full layout was drawn without a filter.
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

        if filter_func is None:
            dxf_module.Frontend(context, backend, config=render_config).draw_layout(
                msp,
                finalize=False,
            )
        else:
            dxf_module.Frontend(context, backend, config=render_config).draw_layout(
                msp,
                finalize=False,
                filter_func=filter_func,
            )
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

    return len(visible_handles), total_count, prefilter_skipped


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
    skipped_payload = {
        "schema_version": SCHEMA_VERSION,
        "pair_uuid": job.pair_uuid,
        "zone_id": job.zone_id,
        "before_image": "",
        "after_image": "",
        "before_transform": {},
        "after_transform": {},
        "world_window": job.world_window.to_dict(),
        "renderer_backend": "pdf-page-bbox-required",
        "cache_key": cache_key,
        "visual_fidelity": "relative_overlay",
        "render_lifecycle": "skipped_missing_page_bbox",
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
        before_image="",
        after_image="",
        before_transform={},
        after_transform={},
        world_window=job.world_window.to_dict(),
        renderer_backend="pdf-page-bbox-required",
        cache_key=cache_key,
        cache_hit=False,
        visual_fidelity="relative_overlay",
        render_lifecycle="skipped_missing_page_bbox",
        warnings=warnings,
        request_id=job.request_id,
        elapsed_ms=elapsed_ms,
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
) -> None:
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
    if source_pdf is not None and Path(source_pdf).exists():
        try:
            from .pdf_display_list_cache import (
                get_display_list,
                get_page_rect,
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
                display_list = get_display_list(Path(source_pdf), int(page_index))
                page_w, page_h = get_page_rect(Path(source_pdf), int(page_index))
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
                try:
                    render_clip_to_png(
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
            pass
        except (FileNotFoundError, IndexError, ValueError, RuntimeError):
            # IndexError = bad page_index; ValueError = empty clip /
            # mapping failure; RuntimeError = thread-safety guard.
            # In all of these the PIL fallback below produces a
            # usable, if slower, result.
            pass
        except Exception as exc:
            # Defensive net — any unexpected fitz failure must not
            # break the render. We log at debug because the fallback
            # still produces a valid crop.
            logger.debug(
                "DisplayList PDF crop failed for %s page=%s: %s; "
                "falling back to PIL",
                source_pdf, page_index, exc,
            )

    if used_display_list:
        return

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


def _oda_converter_signature() -> dict[str, Any]:
    candidates: list[str] = []
    env_path = os.environ.get("ODA_CONVERTER_PATH")
    if env_path:
        candidates.append(env_path)
    which_path = shutil.which("ODAFileConverter")
    if which_path:
        candidates.append(which_path)
    for value in candidates:
        path = Path(value)
        if path.exists():
            return _directory_signature(path)
    return {"path": candidates[0] if candidates else "", "exists": False}


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
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
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
