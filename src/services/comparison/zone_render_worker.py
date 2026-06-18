# -*- coding: utf-8 -*-
"""Zone vector-focus renderer — full-fidelity primitive pack for one zone.

Phase G2.3. Complements the LOD0 ``overview`` pack:

* ``overview_lod0.json`` (Phase G1) — *every* primitive in the drawing,
  but **lines + paths only** (no text, no hatch). Cheap to load. Used as
  the navigation/locator background.

* ``zone_focus.json`` (this module) — *every* primitive overlapping the
  zone bbox + padding, **including text/hatch/MTEXT/etc**. Lazy-built
  per zone click. Lets the reviewer read tags + dimensions inside the
  selected change zone without paying full-drawing cost.

Both files share the same JSON schema (``CustomJSONBackend.get_json_data``
output) so the QML Canvas pipeline draws them identically — the
lightweight viewport just stacks the focus pack on top of the overview
when a zone is active.

Spatial filter: we use ``ezdxf.bbox.extents([entity])`` per entity at
``Frontend.draw_layout`` time via the ``filter_func`` parameter — that's
the same trick ``zone_vector_renderer`` (Phase B1) already uses to keep
the render bounded. The cap on accepted entities (``max_entities``) stops
INSERT explosions from becoming a memory bomb.

This module has **no Qt dependency** — it can run in a worker thread or
a subprocess. The ViewerSession (G2.1) calls it from its ThreadPoolExecutor.
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from src.services.comparison.viewer_primitive_source import RENDER_CONTRACT_VERSION

logger = logging.getLogger(__name__)

# ezdxf imports are deferred to the build function so module import stays
# cheap (matches scene_pack_builder pattern).

#: World-coords bbox: ``(min_x, min_y, max_x, max_y)``.
Bbox = Tuple[float, float, float, float]

ZONE_FOCUS_FILENAME = "zone_focus.json"

#: Part of ``zone_focus_cache_key`` so RENDERER changes (not just source
#: changes) invalidate cached focus builds.
_ZONE_RENDER_VERSION = RENDER_CONTRACT_VERSION

#: Max accepted entities per zone — same default as ``zone_vector_renderer``
#: (Phase B1). Above this we truncate + flag the result.
DEFAULT_MAX_ENTITIES = 1500


@dataclass(frozen=True)
class ZoneFocusResult:
    """Outcome of one zone-focus build."""

    output_path: str
    primitive_count: int
    entity_count: int
    truncated: bool
    elapsed_ms: float
    world_bbox: Bbox
    skipped_reason: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "output_path": self.output_path,
            "primitive_count": int(self.primitive_count),
            "entity_count": int(self.entity_count),
            "truncated": bool(self.truncated),
            "elapsed_ms": float(self.elapsed_ms),
            "world_bbox": list(self.world_bbox),
            "skipped_reason": self.skipped_reason,
            "warnings": list(self.warnings),
        }


def _pad_bbox(bbox: Bbox, ratio: float) -> Bbox:
    """Expand bbox outward by ``ratio`` on each side."""

    x0, y0, x1, y1 = bbox
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    w = x1 - x0
    h = y1 - y0
    px = w * ratio
    py = h * ratio
    return (x0 - px, y0 - py, x1 + px, y1 + py)


def _write_json_atomic(path: Path, payload: object) -> None:
    """Stream-write atomic JSON. Phase G2.4 — same MemoryError fix as
    scene_pack_builder._write_json_atomic.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


# Process-local cache of the parsed + legibility-patched ezdxf doc, keyed by
# (resolved DXF path, mtime_ns, size). The zone-render worker is PERSISTENT
# (zone_render_process.main reads requests in a loop), but render_zone_focus
# opened the doc with mutable=True on EVERY call (a fresh parse, to avoid
# mutating the shared read-cache during the font remap) — so every zone the
# user clicked re-parsed the multi-MB converted DXF, costing 4-5 s/zone
# (live-test 2026-06-17). Caching the patched doc here lets the first zone pay
# the parse and every later zone for the same source reuse it. Rendering only
# READS the doc (draw + bbox), so cross-zone reuse is safe; the font patch is
# applied once at parse time. Bounded so before+after of the current pair fit.
_ZONE_DOC_CACHE: "OrderedDict[tuple, object]" = OrderedDict()
_ZONE_DOC_CACHE_MAX = 3


def _zone_doc_cache_key(dxf_path: Path) -> Optional[tuple]:
    try:
        st = dxf_path.stat()
        return (str(dxf_path), st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def render_zone_focus(
    source_path: Path,
    zone_world_bbox: Bbox,
    output_dir: Path,
    *,
    padding_ratio: float = 0.1,
    max_entities: int = DEFAULT_MAX_ENTITIES,
) -> ZoneFocusResult:
    """Render a zone-bounded primitive pack to ``output_dir / zone_focus.json``.

    Args:
        source_path: A DXF or DWG file. DWG is resolved through
            ``resolve_dxf_path`` as a canonical debug DXF export.
        zone_world_bbox: ``(min_x, min_y, max_x, max_y)`` in CAD world coords.
            Typically the bbox of one change zone.
        output_dir: Directory that will receive ``zone_focus.json``.
        padding_ratio: Expand bbox outward by this fraction on each side
            so the rendered zone has context around the change.
        max_entities: Cap on accepted entities. Above this the result is
            truncated and ``truncated=True`` is set.

    Returns:
        :class:`ZoneFocusResult`. ``output_path`` is empty when nothing was
        rendered (e.g. ezdxf import error).
    """

    start = time.perf_counter()
    warnings: List[str] = []

    try:
        import ezdxf
        from ezdxf import bbox as ezdxf_bbox
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.json import CustomJSONBackend
        from ezdxf.math import BoundingBox2d
    except ImportError as exc:
        return ZoneFocusResult(
            output_path="",
            primitive_count=0,
            entity_count=0,
            truncated=False,
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
            world_bbox=zone_world_bbox,
            skipped_reason=f"ezdxf import failed: {exc}",
            warnings=[],
        )

    from src.services.comparison.dxf_read import read_dxf_document_result
    from src.services.comparison.zone_vector_renderer import (
        patch_text_styles_for_legibility,
        resolve_dxf_path,
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. DWG → DXF if needed (reuses Phase F P0 cache).
    try:
        dxf_path = resolve_dxf_path(Path(source_path))
    except (FileNotFoundError, OSError) as exc:
        return ZoneFocusResult(
            output_path="",
            primitive_count=0,
            entity_count=0,
            truncated=False,
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
            world_bbox=zone_world_bbox,
            skipped_reason=f"DXF resolution failed: {exc}",
        )

    # 2. Open the doc. Reuse a process-local cache of the parsed + patched doc
    # across zones (the worker is persistent); only the first zone for a source
    # pays the multi-MB parse. mutable=True still avoids mutating the SHARED
    # read-cache during the font remap — we keep our own patched copy instead.
    cache_key = _zone_doc_cache_key(dxf_path)
    cached_entry = _ZONE_DOC_CACHE.get(cache_key) if cache_key is not None else None
    if cached_entry is not None:
        doc, bbox_cache = cached_entry
        _ZONE_DOC_CACHE.move_to_end(cache_key)
    else:
        try:
            read_result = read_dxf_document_result(
                dxf_path, ezdxf_module=ezdxf, mutable=True
            )
            doc = read_result.doc
            patch_text_styles_for_legibility(doc)
            read_warning = read_result.diagnostics.warning()
            if read_warning:
                warnings.append(read_warning)
        except Exception as exc:
            return ZoneFocusResult(
                output_path="",
                primitive_count=0,
                entity_count=0,
                truncated=False,
                elapsed_ms=(time.perf_counter() - start) * 1000.0,
                world_bbox=zone_world_bbox,
                skipped_reason=f"ezdxf.readfile failed: {exc}",
            )
        # Persist the per-entity bbox cache ALONGSIDE the doc (speed, 2026-06-18):
        # the zone filter sweeps EVERY entity's bbox, and a fresh ezdxf bbox Cache
        # per zone cost ~5 s/zone on the AC1027 pair (measured: 5.67 s to build vs
        # 0.13 s when the same Cache is reused — 43x). One Cache per cached doc
        # makes zone 2..N near-instant. Lazy-fill only; concurrent fills across the
        # 2 zone-focus worker threads are benign (independent keys under the GIL;
        # same-key recompute is idempotent), matching the existing lockless doc cache.
        bbox_cache = ezdxf_bbox.Cache()
        if cache_key is not None:
            _ZONE_DOC_CACHE[cache_key] = (doc, bbox_cache)
            _ZONE_DOC_CACHE.move_to_end(cache_key)
            while len(_ZONE_DOC_CACHE) > _ZONE_DOC_CACHE_MAX:
                _ZONE_DOC_CACHE.popitem(last=False)

    # 3. Build the zone bbox + spatial filter.
    padded = _pad_bbox(zone_world_bbox, padding_ratio)
    zone_bbox_2d = BoundingBox2d(
        [(padded[0], padded[1]), (padded[2], padded[3])]
    )

    accepted_count = [0]
    truncated = [False]
    # ``bbox_cache`` is the per-doc shared ezdxf bbox Cache created/reused above
    # (FIX 2a) — NOT a fresh per-zone cache, so the entity-bbox sweep is paid once.

    def _entity_overlaps_zone(entity) -> Optional[bool]:
        try:
            ent_bbox = ezdxf_bbox.extents([entity], cache=bbox_cache, fast=True)
        except Exception:
            return None
        if not getattr(ent_bbox, "has_data", False):
            return None
        try:
            ebox = BoundingBox2d([
                (ent_bbox.extmin.x, ent_bbox.extmin.y),
                (ent_bbox.extmax.x, ent_bbox.extmax.y),
            ])
        except Exception:
            return None
        return zone_bbox_2d.has_intersection(ebox)

    def _zone_filter(entity) -> bool:
        if accepted_count[0] >= max_entities:
            truncated[0] = True
            return False
        overlap = _entity_overlaps_zone(entity)
        if overlap is None:
            # Be conservative — keep entities we can't measure (rare)
            accepted_count[0] += 1
            return True
        if not overlap:
            return False
        accepted_count[0] += 1
        return True

    # 4. Flatten via CustomJSONBackend with the spatial filter.
    backend = CustomJSONBackend(orient_paths=False)
    ctx = RenderContext(doc)
    fe = Frontend(ctx, backend)
    # Resilient per-entity draw (L2, 2026-06-17): a single ``draw_layout`` aborts
    # the WHOLE zone at the first un-renderable entity — e.g. an INSERT raising
    # "'Glyph' object has no attribute 'data'" — keeping only what was drawn
    # before it (the partial, hard-to-read zone the user saw on the SPLICE pair,
    # logged as "draw_layout raised mid-stream"). Draw one entity at a time
    # through the same zone filter so one bad entity is skipped instead of
    # truncating the rest. Same resilience the scene-pack builder already uses.
    skipped_entities = 0
    first_skip_sample = ""
    for entity in doc.modelspace():
        try:
            if not _zone_filter(entity):
                continue
        except Exception:  # noqa: BLE001 — an unmeasurable entity must not abort the zone
            continue
        try:
            fe.draw_entities([entity])
        except Exception as exc:  # noqa: BLE001 — one bad entity must not blank the zone
            skipped_entities += 1
            if not first_skip_sample:
                etype = getattr(entity, "dxftype", lambda: "?")()
                first_skip_sample = f"{etype}: {exc}"
    try:
        fe.pipeline.finalize()
    except Exception as exc:  # noqa: BLE001 — finalize must not crash the zone build
        warnings.append(f"Frontend finalize raised: {exc}")
        logger.warning("Zone focus finalize failed: %s", exc)
    if skipped_entities:
        warnings.append(
            f"Skipped {skipped_entities} un-renderable entit"
            f"{'y' if skipped_entities == 1 else 'ies'} in zone; e.g. {first_skip_sample}"
        )
        logger.warning(
            "Zone focus: skipped %d un-renderable entities; e.g. %s",
            skipped_entities, first_skip_sample,
        )

    primitives = backend.get_json_data() or []

    # 5. Persist.
    output_path = output_dir / ZONE_FOCUS_FILENAME
    payload = {
        "format_version": 1,
        "source_path": str(dxf_path),
        "zone_world_bbox": list(zone_world_bbox),
        "padded_world_bbox": list(padded),
        "primitive_count": len(primitives),
        "entity_count": int(accepted_count[0]),
        "truncated": bool(truncated[0]),
        "primitives": primitives,
    }
    _write_json_atomic(output_path, payload)

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    logger.info(
        "Zone focus build done: source=%s zone_bbox=%s "
        "primitives=%d entities=%d truncated=%s elapsed_ms=%.0f",
        Path(source_path).name, zone_world_bbox,
        len(primitives), accepted_count[0], truncated[0], elapsed_ms,
    )

    return ZoneFocusResult(
        output_path=str(output_path),
        primitive_count=len(primitives),
        entity_count=int(accepted_count[0]),
        truncated=bool(truncated[0]),
        elapsed_ms=elapsed_ms,
        world_bbox=tuple(padded),
        warnings=warnings,
    )


def zone_focus_cache_key(
    source_path: Path,
    zone_world_bbox: Bbox,
    *,
    padding_ratio: float = 0.1,
) -> str:
    """Deterministic cache key for one zone-focus build.

    Combines source mtime + size + zone bbox (rounded to 1 mm) + padding.
    Stable across sessions; invalidates automatically when the source
    changes.

    Phase G2.4 fix — ASCII-fold the stem to avoid Windows path issues
    with Korean structural drawing names (rtree + some os APIs choke on
    non-ASCII). Stable hash suffix prevents stem collisions.
    """

    src = Path(source_path)
    try:
        st = src.stat()
        sig_src = f"{int(st.st_mtime_ns)}_{st.st_size}_{_ZONE_RENDER_VERSION}"
    except OSError:
        sig_src = f"nostat_{_ZONE_RENDER_VERSION}"
    bbox_sig = "_".join(f"{round(v):.0f}" for v in zone_world_bbox)

    raw_stem = src.stem
    ascii_stem = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_"
                         for ch in raw_stem) or "src"
    if ascii_stem != raw_stem:
        import hashlib
        h = hashlib.sha1(raw_stem.encode("utf-8")).hexdigest()[:8]
        return f"{ascii_stem[:48]}__{h}__{sig_src}__b{bbox_sig}__p{padding_ratio:.2f}"
    return f"{ascii_stem}__{sig_src}__b{bbox_sig}__p{padding_ratio:.2f}"


__all__ = [
    "Bbox",
    "ZONE_FOCUS_FILENAME",
    "DEFAULT_MAX_ENTITIES",
    "ZoneFocusResult",
    "render_zone_focus",
    "zone_focus_cache_key",
]
