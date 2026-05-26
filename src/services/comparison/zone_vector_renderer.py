# -*- coding: utf-8 -*-
"""Zone-only SVG vector renderer for the Drawing Compare Workbench.

Phase B1 — Hybrid LOD viewer: keep the fast PNG skeleton (Phase A3) for the
overview, ADD a vector-quality SVG render for the *single* selected change
zone. Because each zone bbox is small (typically <500 world units on a
side), the entity count after spatial filtering is two orders of magnitude
below the full drawing — full fidelity (including INSERT block references,
HATCH fills, MTEXT) becomes affordable in 1-2 seconds.

Output: a single ``.svg`` file. The QML viewer renders it as a layered
``Image { source: "*.svg" }`` over the existing PNG, giving infinite zoom
without re-render. Reviewer can read beam tags, dimension values, and grid
labels at any zoom level — the gap that the Phase A3 raster overview can't
close on its own.

Key design choices:

- **Spatial filter at Frontend level**: ``filter_func(entity)`` returns
  ``True`` only for entities whose bbox overlaps the zone bbox + padding.
  This stops INSERT block references *outside* the zone from being exploded
  (the same explosion that took 22 GB / 16 minutes on a 71 MB customer
  DXF in Phase A2 testing).
- **Full fidelity inside zone**: We do NOT use the ``light_mode`` filter
  here. INSERT/HATCH/MTEXT *inside the zone* should be drawn — that's the
  whole point of zone vector inspection.
- **Background/color policy**: Reuses the same ``BackgroundPolicy.WHITE``
  config that Phase A established for the raster path. Without it, color
  index 7 entities render white-on-white invisible.
- **Padding**: 10 % default so the reviewer sees a small ring of context
  around the change, not a tightly-clipped extract.

The implementation is read-only with respect to the source DXF; the only
side effect is writing the SVG file.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)


try:
    import ezdxf
    from ezdxf import bbox as ezdxf_bbox
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing import config as _ezdxf_config
    from ezdxf.addons.drawing import layout as _ezdxf_layout
    from ezdxf.addons.drawing.svg import SVGBackend
    from ezdxf.math import BoundingBox2d

    SVG_RENDERER_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - optional dependency guard
    SVG_RENDERER_AVAILABLE = False
    _IMPORT_ERROR = exc
    logger.warning("Zone vector SVG renderer dependencies unavailable: %s", exc)


# Hard cap on accepted entity count. Above this, the SVG output starts
# becoming large enough that QML SVG load time + Qt rasterization dominates
# the perceived "instant" behavior we want from a zone click. The cap is
# conservative — even the densest customer drawings I measured had <300
# top-level entities per zone after spatial filter. If a zone genuinely
# needs more, we surface the truncation in the result dict so the GUI can
# warn the reviewer rather than silently lose detail.
_MAX_ACCEPTED_ENTITIES = 1500


def resolve_dxf_path(
    source_path: Path,
    *,
    cache_dir: Optional[Path] = None,
) -> Path:
    """Return a DXF path usable by ``ezdxf.readfile``.

    The call site may pass a raw DWG path.  DWG inputs are normalized through
    the ODA-free CanonicalDrawing import pipeline and exported to a temporary
    R2000 DXF for the existing ezdxf SVG renderer.  The converted artifact is
    cached by ``(stem, mtime, size)``.

    Args:
        source_path: Original path the workbench had on hand. May be DWG or DXF.
        cache_dir: Where converted DXFs live. When ``None``, the workbench's
            standard ``%LOCALAPPDATA%\\DrawingCompareWorkbench\\dxf_cache`` is
            used so converted artifacts survive between sessions.

    Returns:
        A path to a usable DXF. For DXF inputs this is the input itself; for
        DWG inputs it is a cached canonical debug-export artifact.

    Raises:
        FileNotFoundError: source doesn't exist.
        OSError: DWG import/export failed and no cached fallback exists.
    """

    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src}")

    suffix = src.suffix.lower()
    if suffix == ".dxf":
        return src
    if suffix != ".dwg":
        # Defensive: callers may pass PDFs or unknown formats. Let the
        # downstream renderer raise a descriptive error rather than guess.
        return src

    # DWG input: import through the native canonical pipeline, then export DXF.
    if cache_dir is None:
        from .cache_paths import normalize_cache_dir
        cache_dir = normalize_cache_dir()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        stat = src.stat()
        cache_key = f"{src.stem}__{int(stat.st_mtime_ns)}__{stat.st_size}.dxf"
    except OSError:
        cache_key = f"{src.stem}__nostat.dxf"
    cached = cache_dir / cache_key
    if cached.exists() and cached.stat().st_size > 0:
        logger.info("Reusing cached DXF for %s -> %s", src.name, cached)
        return cached

    try:
        from .dxf_writer import DxfExportOptions, DxfWriter
        from .import_pipeline import CadPipelineStatus, ImportPipeline, ImportPipelineOptions

        result = ImportPipeline(
            ImportPipelineOptions(
                normalize=False,
                allow_oda_fallback=False,
            )
        ).import_file(src)
        if result.status == CadPipelineStatus.FAILED or not result.canonical_drawing:
            raise OSError(
                f"{result.error_code or 'DWG_IMPORT_FAILED'}: {result.user_message or result.message}"
            )
        DxfWriter(DxfExportOptions(acad_version="AC1015")).write_file(
            result.canonical_drawing,
            cached,
        )
        logger.info("Cached canonical DWG debug DXF: %s -> %s", src.name, cached)
        return cached
    except Exception as exc:
        raise OSError(
            f"DWG canonical import/export failed for {src.name}: {exc}"
        ) from exc


@dataclass(frozen=True)
class ZoneVectorRenderResult:
    """Outcome of one zone SVG render — surfaced back to the GUI."""

    svg_path: str
    entity_count: int
    elapsed_ms: float
    world_bbox: Tuple[float, float, float, float]
    truncated: bool = False
    skipped_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "svg_path": self.svg_path,
            "entity_count": self.entity_count,
            "elapsed_ms": self.elapsed_ms,
            "world_bbox": list(self.world_bbox),
            "truncated": self.truncated,
            "skipped_reason": self.skipped_reason,
        }


def render_zone_svg(
    dxf_path: Path,
    zone_world_bbox: Tuple[float, float, float, float],
    output_svg: Path,
    *,
    padding_ratio: float = 0.1,
    background_color: str = "#FFFFFF",
    max_entities: int = _MAX_ACCEPTED_ENTITIES,
) -> ZoneVectorRenderResult:
    """Render the entities overlapping ``zone_world_bbox`` to an SVG file.

    Args:
        dxf_path: Path to a DXF file (already converted from DWG by the
            upstream pipeline; this function does not handle DWG).
        zone_world_bbox: ``(min_x, min_y, max_x, max_y)`` in DXF world coords.
            The bbox typically comes from ``viewer_manifest.json``'s
            per-zone ``bbox`` field.
        output_svg: Destination file path. Parent dirs are created if missing.
        padding_ratio: Expand bbox outward by this fraction on each side
            so the rendered zone has a context ring around the change.
            0.1 = 10 % — empirically reads well in QML at zoom 1.0.
        background_color: SVG background fill. White by default to match
            Phase A's raster policy.
        max_entities: Cap on accepted entities. When exceeded, the renderer
            stops collecting more and returns ``truncated=True`` so the GUI
            can flag it; the partial SVG still renders.

    Returns:
        A ``ZoneVectorRenderResult`` dataclass. ``svg_path`` is empty
        when nothing was rendered (e.g. SVG dependency missing, no
        entities in zone).
    """

    if not SVG_RENDERER_AVAILABLE:
        return ZoneVectorRenderResult(
            svg_path="",
            entity_count=0,
            elapsed_ms=0.0,
            world_bbox=zone_world_bbox,
            skipped_reason=f"SVG renderer dependencies missing: {_IMPORT_ERROR}",
        )

    dxf_path = Path(dxf_path)
    if not dxf_path.exists():
        return ZoneVectorRenderResult(
            svg_path="",
            entity_count=0,
            elapsed_ms=0.0,
            world_bbox=zone_world_bbox,
            skipped_reason=f"DXF source not found: {dxf_path}",
        )

    # Phase G2.7-COORDFIX — PDF inputs are NOT vector-DXF and must take
    # the Qt PDF / PyMuPDF path on the GUI side. Without this guard,
    # ezdxf.readfile() raises ``OSError: '...pdf' is not a DXF file``
    # for every PDF zone — surfaced to users as "벡터 랜더 실패" with
    # no way forward. Returning a clean ``skipped_reason`` lets the
    # workbench display the friendlier "PDF는 신형 뷰어로 표시" notice
    # instead of an OS-level error message.
    if dxf_path.suffix.lower() == ".pdf":
        return ZoneVectorRenderResult(
            svg_path="",
            entity_count=0,
            elapsed_ms=0.0,
            world_bbox=zone_world_bbox,
            skipped_reason=(
                "PDF는 벡터 SVG로 변환하지 않습니다 — 신형(경량) 뷰어가 "
                "Qt PDF로 직접 표시합니다."
            ),
        )

    # DWG inputs are resolved to a cached canonical DXF debug export first.
    try:
        dxf_path = resolve_dxf_path(dxf_path)
    except (FileNotFoundError, OSError) as exc:
        return ZoneVectorRenderResult(
            svg_path="",
            entity_count=0,
            elapsed_ms=0.0,
            world_bbox=zone_world_bbox,
            skipped_reason=f"DWG normalisation failed: {exc}",
        )

    start = time.perf_counter()

    # Pad bbox outward so the reviewer sees context around the change.
    padded = _pad_bbox(zone_world_bbox, padding_ratio)
    zone_bbox = BoundingBox2d([(padded[0], padded[1]), (padded[2], padded[3])])

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    # Stateful entity counter so the filter_func can enforce the cap and
    # ALSO propagate a truncated flag back without raising. Frontends
    # that respect filter_func will skip the rest of the iteration once
    # we start returning False.
    accepted_count = [0]
    truncated = [False]

    # Per-entity bbox is computed via ezdxf.bbox.extents([entity]) — entity
    # objects don't expose .bbox() directly. We share a Cache across calls so
    # the same entity isn't measured twice if Frontend probes it more than
    # once during the draw pass.
    #
    # Performance note (measured on a 71 MB customer DXF, 2,143 top-level
    # entities, INSERTs that explode to 178 K virtual entities):
    #   ezdxf.readfile          ~ 8 s
    #   bbox compute (per-entity, lazy, fast=True)  ~ 13 s total amortized
    #   Frontend.draw_layout (with filter_func)     ~ 0.1 s
    #   SVGBackend.get_string                       ~ 0.01 s
    # → total first-zone cost ~ 22 s; subsequent zones in the same process
    # would reuse the loaded doc and the bbox cache and finish in <1 s.
    # Phase B1 ships the per-call subprocess for safety; persistent worker
    # (one DXF load amortized across many zone clicks) is queued for B2.
    _bbox_cache = ezdxf_bbox.Cache()

    def _entity_bbox_2d(entity):
        try:
            ent_bbox = ezdxf_bbox.extents([entity], cache=_bbox_cache, fast=True)
        except Exception:
            return None
        if not getattr(ent_bbox, "has_data", False):
            return None
        try:
            return BoundingBox2d(
                [
                    (ent_bbox.extmin.x, ent_bbox.extmin.y),
                    (ent_bbox.extmax.x, ent_bbox.extmax.y),
                ]
            )
        except Exception:
            return None

    def _zone_filter(entity) -> bool:
        if accepted_count[0] >= max_entities:
            truncated[0] = True
            return False
        ent_bbox_2d = _entity_bbox_2d(entity)
        if ent_bbox_2d is None:
            # Some entities (proxy graphics, malformed text, etc.) refuse
            # bbox computation. Be inclusive — better to render an extra
            # entity than silently lose change geometry.
            accepted_count[0] += 1
            return True
        if zone_bbox.has_overlap(ent_bbox_2d):
            accepted_count[0] += 1
            return True
        return False

    cfg = _ezdxf_config.Configuration(
        background_policy=_ezdxf_config.BackgroundPolicy.WHITE,
    )
    backend = SVGBackend()
    try:
        backend.set_background(background_color)
    except Exception:
        # Older ezdxf versions might not expose this; the WHITE config
        # policy will do the right thing anyway.
        pass

    Frontend(ctx=RenderContext(doc), out=backend, config=cfg).draw_layout(
        msp, finalize=True, filter_func=_zone_filter
    )

    if accepted_count[0] == 0:
        # filter_func rejected every entity → SVGBackend.get_string would
        # raise ValueError("empty bounding box") because there's no content
        # to fit_page against. Surface this as a graceful "no content"
        # outcome the GUI can handle (e.g. fall back to PNG view) instead
        # of crashing the subprocess.
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return ZoneVectorRenderResult(
            svg_path="",
            entity_count=0,
            elapsed_ms=elapsed_ms,
            world_bbox=padded,
            skipped_reason="No DXF entities overlap the requested zone bbox.",
        )

    # Page sizing. We size the Page to match the zone bbox aspect so
    # ezdxf's fit_page (default ON) maps the content into the viewBox
    # cleanly. Critically we do NOT pass `render_box` — initial testing
    # showed that combining render_box with the filter_func skipped the
    # automatic content→viewBox transform, leaving path coordinates at
    # raw world values (e.g. 477262,-98442) outside the 0..1000000
    # viewBox space, which made the SVG render visually empty in
    # browsers / Qt SVG. Filter_func already restricts content to the
    # zone, so the default "content bounding box" behavior of get_string
    # produces the right framing without extra parameters.
    width_world = max(padded[2] - padded[0], 1.0)
    height_world = max(padded[3] - padded[1], 1.0)
    aspect = width_world / height_world
    # Use a 200 mm long-edge page sized to the zone aspect — gives a
    # reasonable physical size for both browser display (where mm maps to
    # px @ 96 dpi → ~756 px wide) and Inkscape / SVG editor open.
    if aspect >= 1.0:
        page_width_mm = 200.0
        page_height_mm = 200.0 / aspect
    else:
        page_height_mm = 200.0
        page_width_mm = 200.0 * aspect
    page = _ezdxf_layout.Page(
        width=page_width_mm,
        height=page_height_mm,
        units=_ezdxf_layout.Units.mm,
        margins=_ezdxf_layout.Margins(0, 0, 0, 0),
    )

    svg_text = backend.get_string(page=page)

    output_svg = Path(output_svg)
    output_svg.parent.mkdir(parents=True, exist_ok=True)
    # SVG payloads can contain Korean entity text; safe_unicode keeps the
    # write path consistent with the JSON manifest sanitization landed in
    # the previous commit.
    from .safe_unicode import safe_unicode

    output_svg.write_text(safe_unicode(svg_text), encoding="utf-8")

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    logger.info(
        "Zone SVG rendered: %d entities, %.0f ms, %s",
        accepted_count[0],
        elapsed_ms,
        output_svg.name,
    )
    return ZoneVectorRenderResult(
        svg_path=str(output_svg),
        entity_count=accepted_count[0],
        elapsed_ms=elapsed_ms,
        world_bbox=padded,
        truncated=truncated[0],
    )


def _pad_bbox(
    bbox: Tuple[float, float, float, float], ratio: float
) -> Tuple[float, float, float, float]:
    """Expand ``bbox`` outward by ``ratio`` of its width/height on each side.

    A degenerate (zero-area) bbox is padded to a 1.0-unit square so the
    SVG viewBox computation never divides by zero.
    """

    x0, y0, x1, y1 = bbox
    width = max(x1 - x0, 0.0)
    height = max(y1 - y0, 0.0)
    if width == 0 and height == 0:
        return (x0 - 0.5, y0 - 0.5, x0 + 0.5, y0 + 0.5)
    pad_x = max(width * float(ratio), 0.5)
    pad_y = max(height * float(ratio), 0.5)
    return (x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y)


__all__ = [
    "ZoneVectorRenderResult",
    "render_zone_svg",
    "SVG_RENDERER_AVAILABLE",
]
