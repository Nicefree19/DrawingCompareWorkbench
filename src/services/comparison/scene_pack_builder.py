# -*- coding: utf-8 -*-
"""ScenePack builder — flatten one CAD source into a primitive JSON pack.

Phase G core service. Replaces "render the whole drawing as a PNG" with
"flatten the drawing into primitives once, build a spatial index, write
both to disk". The viewer then consults the pack on demand:

* on pair selection → load the LOD0 skeleton subset → first paint < 1 s
* on zone selection → query the spatial index for primitives overlapping
  the zone bbox → assemble a vector micro-pack → render to QML

Pipeline:

    DXF (or DWG → DXF via Phase F P0 resolve_dxf_path)
      ↓
    ezdxf.RenderContext + Frontend(CustomJSONBackend)
      ↓
    backend.draw_layout(modelspace) — flattens INSERT/HATCH/MTEXT etc.
      ↓
    backend.get_json_data() → list[dict] of primitives
      ↓
    Per-primitive bbox extraction
      ↓
    build_primitive_index(primitives)  — R-tree (or grid fallback)
      ↓
    Write three artifacts:
      • {pack_dir}/scene_pack.json       (full primitive list)
      • {pack_dir}/primitive_index.*     (rtree + .meta.json, OR grid .json)
      • {pack_dir}/overview_lod0.json    (skeleton subset — lines only, no
                                          text/hatch — for instant pair open)
      ↓
    Return ScenePackRef for the manifest

The module is **read-only with respect to the source DXF**; the only side
effect is writing the three pack files.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, List, Literal, Optional, Tuple

from src.services.comparison.viewer_manifest_v3 import ScenePackRef
from src.services.comparison.viewer_spatial_index import (
    Bbox,
    PrimitiveBbox,
    PrimitiveIndex,
    build_primitive_index,
)

logger = logging.getLogger(__name__)

# Output filenames (kept stable so the loader doesn't need a manifest).
SCENE_PACK_FILENAME = "scene_pack.json"
OVERVIEW_LOD0_FILENAME = "overview_lod0.json"
INDEX_FILENAME = "primitive_index"  # extension varies by backend (see viewer_spatial_index)

# Hard cap on flattened primitive count. Above this we truncate + log a warning.
# Phase G2.4 — bumped from 200K to 600K so production-size structural DWG
# (S20-class 평면도, ~5-10 MB DWG that explodes to ~80 MB DXF and 200K-500K
# primitives after ezdxf flatten) fit without truncation. The cap still
# protects against pathological INSERT explosions (the 71 MB DXF that took
# 22 GB of memory in Phase A2 testing).
DEFAULT_MAX_PRIMITIVES = 600_000

# Phase G2.4 — multi-stage build progress signal.
#
# The build pipeline is dominated by I/O and ezdxf compute, both of which
# can take seconds to minutes for large DWG. We surface intermediate stages
# so the GUI can update its badge from "render_pending — DWG 변환 중" to
# "render_pending — Primitive 추출 중" etc. instead of leaving the user
# guessing whether the worker is still alive.
#
# Stage values are strings (Literal) — kept unique + JSON-serialisable so
# the same enum can travel through subprocess pipes if we move to QProcess
# in the future.
BuildStage = Literal[
    "starting",
    "resolving_dwg",
    "reading_dxf",
    "flattening",
    "indexing",
    "writing",
    "done",
    "failed",
]

#: Progress callback signature: ``(stage, percent_estimate, message_ko)``.
#: All three arguments are best-effort — percent is None when unknown.
ProgressCallback = Callable[[BuildStage, Optional[float], str], None]


def _suggest_max_primitives(source_size_bytes: int) -> int:
    """Auto-tune the primitive cap based on source file size.

    Empirical heuristic — DXF/DWG files hold roughly 4-12 primitives per
    KB after ezdxf flattens INSERT references. We aim for about 8x the
    file size in KB, with a floor at the global default and a ceiling at
    1.5x default to keep memory bounded.
    """

    if source_size_bytes <= 0:
        return DEFAULT_MAX_PRIMITIVES
    estimate = max(DEFAULT_MAX_PRIMITIVES, (source_size_bytes // 1024) * 8)
    return min(estimate, int(DEFAULT_MAX_PRIMITIVES * 1.5))

# Phase G2.4 — LOD0 skeleton filter.
#
# Originally we kept both "lines" and "path" in the skeleton subset, but
# real Korean structural DWG (S20-class) explode INSERT references into
# "path" primitives carrying THOUSANDS of bezier control points each.
# A 235K-primitive drawing can produce a 7+ GB overview JSON because of
# this. So LOD0 is now "lines only" — the cheapest possible skeleton
# that gives the reviewer enough wayfinding context to pick a zone.
# The "path" primitives still appear in zone_focus packs (per-zone
# vector micro-pack), where they are bbox-clipped to the zone area.
SKELETON_TYPES = frozenset({"lines"})

# Hard cap on LOD0 primitive count. Above this we subsample uniformly so
# the QML Canvas paint stays under ~50ms per frame. 10K primitives is
# empirically the sweet spot for Canvas — beyond that, paint time and
# memory dominate. Subsampling is even (every Nth) which preserves the
# overall visual density without bias.
LOD0_MAX_PRIMITIVES = 10_000

# Phase G2.4 — per-primitive complexity guard. Real Korean structural
# DWG (S20-class) explode INSERT references such that single "lines"
# primitives carry HUNDREDS of segments (text-rendered-as-lines, block
# expansions, hatch patterns). We reject any primitive above this segment
# count when building the LOD0 overview — those are "noise detail" the
# reviewer doesn't need for wayfinding. They still appear in the per-zone
# focus packs (built by zone_render_worker on demand) where the spatial
# filter keeps them bounded.
LOD0_MAX_SEGMENTS_PER_PRIMITIVE = 100

# Phase G2.4 — total LOD0 file size budget. Even after the per-primitive
# segment cap, a pathological drawing could still produce a multi-megabyte
# LOD0. We stop including primitives once the in-memory size reaches this
# cap. Empirically 5 MB renders in <100 ms on Canvas — a comfortable
# first-paint budget.
LOD0_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

ALL_KNOWN_TYPES = frozenset({
    "lines", "path", "filled-paths", "filled-polygon",
    "points", "image", "text", "filled_path", "polyline",
})


@dataclass(frozen=True)
class SceneBuildResult:
    """Outcome of one ``build_scene_pack`` call."""

    scene_pack_ref: ScenePackRef
    primitive_count: int
    truncated: bool
    elapsed_ms: float
    backend_used: str          # "rtree" | "grid"
    skipped_types: dict[str, int]   # bbox extraction failures by type
    warnings: List[str]


def _bbox_from_lines_geometry(geom: Any) -> Optional[Bbox]:
    """``lines`` primitive: list of ``[x0, y0, x1, y1]`` segments."""

    if not isinstance(geom, list) or not geom:
        return None
    xs: List[float] = []
    ys: List[float] = []
    for seg in geom:
        if not isinstance(seg, (list, tuple)) or len(seg) < 4:
            continue
        try:
            x0, y0, x1, y1 = float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3])
        except (TypeError, ValueError):
            continue
        xs.extend((x0, x1))
        ys.extend((y0, y1))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_from_path_geometry(geom: Any) -> Optional[Bbox]:
    """``path`` primitive: SVG-style commands ``["M", x, y]``, ``["L", x, y]``,
    ``["C", c1x, c1y, c2x, c2y, x, y]``, ``["Q", cx, cy, x, y]``, ``["Z"]``.

    For bezier control points (C/Q) the control coords ARE included in the
    bbox — this overestimates the visual bounds slightly for high-curvature
    bezier segments, but never underestimates (which is what matters for
    spatial queries).
    """

    if not isinstance(geom, list) or not geom:
        return None
    xs: List[float] = []
    ys: List[float] = []
    for cmd in geom:
        if not isinstance(cmd, (list, tuple)) or len(cmd) < 1:
            continue
        # cmd[0] is the letter; the rest are floats in (x, y) pairs.
        coords = cmd[1:]
        # If we got an odd count or non-numeric, skip this command but keep going.
        if len(coords) % 2 != 0:
            continue
        for i in range(0, len(coords), 2):
            try:
                x = float(coords[i])
                y = float(coords[i + 1])
            except (TypeError, ValueError):
                continue
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_from_filled_paths_geometry(geom: Any) -> Optional[Bbox]:
    """``filled-paths``: list of path command lists (one per loop)."""

    if not isinstance(geom, list) or not geom:
        return None
    xs: List[float] = []
    ys: List[float] = []
    for sub in geom:
        # Each sub is itself a list of SVG commands.
        b = _bbox_from_path_geometry(sub)
        if b is None:
            continue
        xs.extend((b[0], b[2]))
        ys.extend((b[1], b[3]))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_from_filled_polygon_geometry(geom: Any) -> Optional[Bbox]:
    """``filled-polygon``: list of ``[x, y]`` vertices."""

    if not isinstance(geom, list) or not geom:
        return None
    xs: List[float] = []
    ys: List[float] = []
    for pt in geom:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
        except (TypeError, ValueError):
            continue
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_from_points_geometry(geom: Any) -> Optional[Bbox]:
    """``points``: list of ``[x, y]`` (degenerate bbox = the point itself)."""

    return _bbox_from_filled_polygon_geometry(geom)


# Dispatch table: primitive type → bbox extractor.
_BBOX_EXTRACTORS = {
    "lines": _bbox_from_lines_geometry,
    "path": _bbox_from_path_geometry,
    "filled-paths": _bbox_from_filled_paths_geometry,
    "filled_path": _bbox_from_path_geometry,
    "filled-polygon": _bbox_from_filled_polygon_geometry,
    "filled_polygon": _bbox_from_filled_polygon_geometry,
    "polyline": _bbox_from_path_geometry,
    "points": _bbox_from_points_geometry,
}


def primitive_bbox(primitive: dict) -> Optional[Bbox]:
    """Best-effort bbox for one ``CustomJSONBackend`` primitive dict.

    Returns ``None`` for unknown types or malformed geometry. Callers
    should treat ``None`` as "skip indexing this primitive" — the
    primitive still ends up in ``scene_pack.json`` but spatial queries
    won't return it.

    Public so unit tests can drive the dispatch table directly.
    """

    if not isinstance(primitive, dict):
        return None
    extractor = _BBOX_EXTRACTORS.get(str(primitive.get("type", "")))
    if extractor is None:
        return None
    return extractor(primitive.get("geometry"))


def _iter_primitives_with_bbox(
    primitives: Iterable[dict],
) -> Iterable[Tuple[int, Bbox, dict, bool]]:
    """Walk primitives, yielding ``(primitive_id, bbox, primitive, indexable)``.

    ``indexable`` is False when bbox extraction failed — caller still emits
    the primitive into the pack but skips inserting it into the spatial
    index.
    """

    for pid, prim in enumerate(primitives):
        bbox = primitive_bbox(prim)
        if bbox is None:
            yield (pid, (0.0, 0.0, 0.0, 0.0), prim, False)
        else:
            yield (pid, bbox, prim, True)


def _write_json_atomic(path: Path, payload: object) -> None:
    """Atomic JSON write that streams large payloads to disk.

    Phase G2.4 fix — the prior implementation called ``json.dumps(payload)``
    which builds the *entire* serialised string in memory before writing.
    For large scene packs (235K+ primitives produced by typical structural
    DWG) that string is 100+ MB and Python raises ``MemoryError`` on
    Windows 32-bit-ish allocations.

    Use ``json.dump(payload, fp)`` instead — that streams the output
    one fragment at a time, no giant intermediate string. We also drop
    ``indent=2`` and use compact separators (``(",",":")``) for the
    same memory + disk reason.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def _emit(progress: Optional[ProgressCallback], stage: BuildStage,
          percent: Optional[float], message: str) -> None:
    """Best-effort progress emission — never raises into the caller."""

    if progress is None:
        return
    try:
        progress(stage, percent, message)
    except Exception:
        logger.exception("scene_pack progress callback raised at stage %s", stage)


def build_scene_pack(
    source_path: Path,
    output_dir: Path,
    *,
    max_primitives: Optional[int] = None,
    prefer_index_backend: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
) -> SceneBuildResult:
    """Build the three scene-pack artifacts for one source file.

    Args:
        source_path: A DXF or DWG file. DWG inputs are routed through the
            Phase F P0 ``resolve_dxf_path`` helper (DwgConverter cache).
        output_dir: Directory that will receive ``scene_pack.json`` +
            ``primitive_index.*`` + ``overview_lod0.json``. Created if missing.
        max_primitives: Hard cap on flattened primitive count. ``None``
            (default) auto-tunes based on source file size; an explicit
            number overrides. Above this cap the result is truncated and
            a warning is logged.
        prefer_index_backend: ``"grid"`` to force the grid fallback (used
            in tests). ``None`` (default) → rtree if installed, else grid.
        progress: Optional callback receiving ``(stage, percent, message_ko)``
            tuples as the build advances through stages. Stages:
            ``resolving_dwg`` → ``reading_dxf`` → ``flattening`` → ``indexing``
            → ``writing`` → ``done``. ``percent`` is best-effort (None when
            unknown). The callback runs on the worker thread; subscribers
            must not call back into ezdxf.

    Returns:
        :class:`SceneBuildResult` with the populated :class:`ScenePackRef`
        plus diagnostic counts.
    """

    start = time.perf_counter()
    _emit(progress, "starting", 0.0, "준비 중")

    # Lazy imports — keep module import cheap.
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.json import CustomJSONBackend

    from src.services.comparison.zone_vector_renderer import resolve_dxf_path

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    warnings: List[str] = []

    # Auto-tune primitive cap when caller didn't specify.
    src = Path(source_path)
    try:
        src_size = src.stat().st_size if src.exists() else 0
    except OSError:
        src_size = 0
    if max_primitives is None:
        max_primitives = _suggest_max_primitives(src_size)

    # 1. Normalise DWG to DXF if needed (reuses Phase F P0 cache).
    is_dwg = src.suffix.lower() == ".dwg"
    if is_dwg:
        _emit(progress, "resolving_dwg", 0.05,
              f"DWG → DXF 변환 중 ({src_size // 1024} KB)")
    try:
        dxf_path = resolve_dxf_path(src)
    except (FileNotFoundError, OSError) as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _emit(progress, "failed", None, f"DXF 변환 실패: {exc}")
        return SceneBuildResult(
            scene_pack_ref=ScenePackRef(notes=f"resolve_dxf_path failed: {exc}"),
            primitive_count=0,
            truncated=False,
            elapsed_ms=elapsed_ms,
            backend_used="none",
            skipped_types={},
            warnings=[f"DXF resolution failed: {exc}"],
        )

    # 2. Open + flatten via CustomJSONBackend.
    _emit(progress, "reading_dxf", 0.20, "DXF 읽는 중")
    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _emit(progress, "failed", None, f"DXF 읽기 실패: {exc}")
        return SceneBuildResult(
            scene_pack_ref=ScenePackRef(notes=f"ezdxf.readfile failed: {exc}"),
            primitive_count=0,
            truncated=False,
            elapsed_ms=elapsed_ms,
            backend_used="none",
            skipped_types={},
            warnings=[f"ezdxf.readfile failed: {exc}"],
        )

    _emit(progress, "flattening", 0.40,
          "Primitive 추출 중 (entity → 라인/곡선 변환)")
    backend = CustomJSONBackend(orient_paths=False)
    ctx = RenderContext(doc)
    fe = Frontend(ctx, backend)
    try:
        fe.draw_layout(doc.modelspace(), finalize=True)
    except Exception as exc:
        # ezdxf occasionally raises mid-draw for proxy graphics / corrupt
        # entities. We keep what's been recorded so far — it's still useful
        # for navigation. Surface the warning so the GUI can show it.
        warnings.append(f"Frontend.draw_layout raised mid-stream: {exc}")
        logger.warning("Frontend.draw_layout raised mid-stream: %s", exc)

    primitives = backend.get_json_data() or []
    truncated = False
    if len(primitives) > max_primitives:
        warnings.append(
            f"Truncated {len(primitives)} primitives down to {max_primitives}"
        )
        primitives = primitives[:max_primitives]
        truncated = True

    _emit(progress, "indexing", 0.70,
          f"공간 인덱스 빌드 중 ({len(primitives)}개 primitive)")
    # 3. Per-primitive bbox + spatial index build.
    indexable: List[PrimitiveBbox] = []
    overview_indices: List[int] = []
    skipped_types: dict[str, int] = {}
    skipped_complex_count = 0
    for pid, bbox, prim, ok in _iter_primitives_with_bbox(primitives):
        if not ok:
            t = str(prim.get("type", "?"))
            skipped_types[t] = skipped_types.get(t, 0) + 1
            continue
        indexable.append((pid, bbox))
        if prim.get("type") in SKELETON_TYPES:
            # Phase G2.4 — reject overly complex "lines" primitives from
            # LOD0 (block-exploded text/hatch noise). They stay in the
            # full pack (or zone_focus) but skip the wayfinding overview.
            geom = prim.get("geometry") or []
            seg_count = len(geom) if isinstance(geom, list) else 0
            if seg_count > LOD0_MAX_SEGMENTS_PER_PRIMITIVE:
                skipped_complex_count += 1
                continue
            overview_indices.append(pid)
    if skipped_complex_count:
        warnings.append(
            f"LOD0 dropped {skipped_complex_count} complex primitives "
            f"(>{LOD0_MAX_SEGMENTS_PER_PRIMITIVE} segments — likely block-explosion noise)"
        )

    spatial: PrimitiveIndex = build_primitive_index(
        indexable, prefer_backend=prefer_index_backend
    )

    # 4. Compute overall world bbox (use index bbox if available, else fall
    #    back to scanning).
    if hasattr(spatial, "world_bbox"):
        world_bbox = getattr(spatial, "world_bbox")
    elif indexable:
        xs0 = min(b[1][0] for b in indexable)
        ys0 = min(b[1][1] for b in indexable)
        xs1 = max(b[1][2] for b in indexable)
        ys1 = max(b[1][3] for b in indexable)
        world_bbox = (xs0, ys0, xs1, ys1)
    else:
        world_bbox = (0.0, 0.0, 0.0, 0.0)

    # 5. Persist artifacts.
    pack_path = output_dir / SCENE_PACK_FILENAME
    overview_path = output_dir / OVERVIEW_LOD0_FILENAME
    # Spatial index path: the backend chooses its actual on-disk format.
    # For rtree we pass a base name like ``primitive_index.rtree`` and the
    # backend writes ``.idx + .dat + .meta.json``. For grid, we write
    # ``primitive_index.json`` directly. Loader auto-detects via the
    # ``.meta.json`` sidecar's presence.
    index_path = output_dir / (
        f"{INDEX_FILENAME}.rtree" if spatial.backend == "rtree"
        else f"{INDEX_FILENAME}.json"
    )

    _emit(progress, "writing", 0.90, "디스크에 쓰는 중")
    try:
        spatial.save_to_disk(index_path)
    except Exception as exc:
        warnings.append(f"Spatial index save failed: {exc}")
        logger.warning("Spatial index save failed: %s", exc)

    # Phase G2.4 — for very large drawings (DWG that flatten to 50K+
    # primitives), the full scene_pack.json balloons to multiple GB
    # because each path/filled-paths primitive holds thousands of bezier
    # control points. The lightweight viewer NEVER loads this full file
    # (it consumes overview_lod0 + per-zone focus packs only), so we
    # intentionally skip writing it above the threshold and write only
    # a lightweight metadata stub. The full pack is rebuilt on demand
    # by anyone who genuinely needs it (zone_focus already does this).
    SKIP_FULL_PACK_THRESHOLD = 50_000
    if len(primitives) > SKIP_FULL_PACK_THRESHOLD:
        warnings.append(
            f"Full scene_pack.json skipped: {len(primitives)} primitives "
            f"exceeds {SKIP_FULL_PACK_THRESHOLD} threshold. "
            f"Viewer uses overview_lod0 + per-zone focus packs."
        )
        logger.info(
            "Skipping full scene_pack.json for %d primitives "
            "(threshold %d). Lightweight viewer uses LOD0 + zone_focus.",
            len(primitives), SKIP_FULL_PACK_THRESHOLD,
        )
        # Write a small stub so consumers know the pack exists conceptually.
        stub_payload = {
            "format_version": 1,
            "source_path": str(dxf_path),
            "primitive_count": len(primitives),
            "truncated": truncated,
            "world_bbox": list(world_bbox),
            "primitives_inline": False,
            "primitives_note": (
                "Full primitive list not inlined to save disk. "
                "Use overview_lod0.json for skeleton view, "
                "zone_render_worker.render_zone_focus() for per-zone detail."
            ),
        }
        _write_json_atomic(pack_path, stub_payload)
    else:
        pack_payload = {
            "format_version": 1,
            "source_path": str(dxf_path),
            "primitive_count": len(primitives),
            "truncated": truncated,
            "world_bbox": list(world_bbox),
            "primitives": primitives,
        }
        _write_json_atomic(pack_path, pack_payload)

    # Phase G2.4 — Overview LOD0 = lines-only subset, capped via three
    # independent guards:
    #   1. Per-primitive segment count (LOD0_MAX_SEGMENTS_PER_PRIMITIVE)
    #      — applied above when populating overview_indices
    #   2. Total primitive count (LOD0_MAX_PRIMITIVES) — even subsample
    #   3. Total bytes (LOD0_MAX_BYTES) — early-stop when budget reached
    #
    # Without these guards, real structural DWG balloon LOD0 to 400+ MB
    # (10K primitives x ~44 KB each) and the QML Canvas first-paint
    # takes 30+ seconds. With the guards, LOD0 stays <5 MB and Canvas
    # paint <100 ms.
    if len(overview_indices) > LOD0_MAX_PRIMITIVES:
        step = max(1, len(overview_indices) // LOD0_MAX_PRIMITIVES)
        sampled_indices = overview_indices[::step][:LOD0_MAX_PRIMITIVES]
        warnings.append(
            f"LOD0 count subsampled: {len(overview_indices)} → {len(sampled_indices)} "
            f"(step {step})"
        )
        overview_indices_to_save = sampled_indices
    else:
        overview_indices_to_save = list(overview_indices)

    # Bytes budget — accumulate primitives until we hit LOD0_MAX_BYTES.
    # We measure the byte cost via json.dumps(prim) per primitive (not
    # ideal but accurate). For 10K primitives this costs ~500 ms total
    # which is acceptable as a one-time pack-build cost.
    selected_overview_primitives: List[dict] = []
    selected_indices: List[int] = []
    cumulative_bytes = 0
    bytes_budget_hit = False
    for idx in overview_indices_to_save:
        prim = primitives[idx]
        prim_bytes = len(json.dumps(prim, ensure_ascii=False, separators=(",", ":")))
        if cumulative_bytes + prim_bytes > LOD0_MAX_BYTES:
            bytes_budget_hit = True
            break
        cumulative_bytes += prim_bytes
        selected_overview_primitives.append(prim)
        selected_indices.append(idx)
    if bytes_budget_hit:
        warnings.append(
            f"LOD0 bytes budget hit at {cumulative_bytes // 1024} KB / "
            f"{LOD0_MAX_BYTES // 1024} KB — stopped at "
            f"{len(selected_overview_primitives)} of {len(overview_indices_to_save)} primitives"
        )
    logger.info(
        "LOD0 final: %d primitives, %.1f KB on disk (from %d candidates)",
        len(selected_overview_primitives),
        cumulative_bytes / 1024,
        len(overview_indices),
    )

    overview_payload = {
        "format_version": 1,
        "source_path": str(dxf_path),
        "world_bbox": list(world_bbox),
        "primitive_indices": selected_indices,
        "primitives": selected_overview_primitives,
        "subsampled_from": (
            len(overview_indices) if len(overview_indices) > len(selected_indices)
            else None
        ),
    }
    _write_json_atomic(overview_path, overview_payload)

    elapsed_ms = (time.perf_counter() - start) * 1000.0

    ref = ScenePackRef(
        json_path=str(pack_path),
        index_path=str(index_path),
        overview_lod0_path=str(overview_path),
        primitive_count=len(primitives),
        drawing_world_bbox=world_bbox,
        elapsed_build_ms=elapsed_ms,
        notes="; ".join(warnings) if warnings else "",
    )

    _emit(
        progress, "done", 1.0,
        f"완료 ({len(primitives)}개, {elapsed_ms:.0f}ms)",
    )
    return SceneBuildResult(
        scene_pack_ref=ref,
        primitive_count=len(primitives),
        truncated=truncated,
        elapsed_ms=elapsed_ms,
        backend_used=spatial.backend,
        skipped_types=skipped_types,
        warnings=warnings,
    )


__all__ = [
    "SCENE_PACK_FILENAME",
    "OVERVIEW_LOD0_FILENAME",
    "INDEX_FILENAME",
    "DEFAULT_MAX_PRIMITIVES",
    "SKELETON_TYPES",
    "BuildStage",
    "ProgressCallback",
    "SceneBuildResult",
    "primitive_bbox",
    "build_scene_pack",
]
