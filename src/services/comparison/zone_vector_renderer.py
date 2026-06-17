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
from typing import Optional, Tuple

from .dxf_read import read_dxf_document_result
from .render_failure_codes import RenderFailureCode
from .source_signature import source_cache_filename

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

# ezdxf's SVG frontend can crash while expanding malformed MULTILEADER
# entities from customer DWGs converted through third-party tools. These
# annotations are common in construction drawings, so the zone viewer renders
# a simplified line/text fallback instead of letting one bad entity blank the
# whole selected-zone view.
_FRAGILE_VECTOR_ENTITY_TYPES = {"MULTILEADER", "MLEADER"}


def _is_fragile_vector_entity(entity) -> bool:
    try:
        return str(entity.dxftype()).upper() in _FRAGILE_VECTOR_ENTITY_TYPES
    except Exception:
        return False


def resolve_dxf_path(
    source_path: Path,
    *,
    cache_dir: Optional[Path] = None,
    failure_codes: Optional[list] = None,
) -> Path:
    """Return a DXF path usable by ``ezdxf.readfile``.

    S1.3.2: when ``failure_codes`` is a list, the function appends
    ``"dwg_using_cached_dxf"`` (info) for normal cache reuse and
    ``"dwg_vector_normalise_failed"`` (warn) when the live conversion
    failed and a stale cache was substituted. The caller is responsible
    for forwarding these codes into ``ZoneVectorRenderResult.failure_codes``
    so the GUI badge can surface them.

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

    shared_cached = _exact_dwg_differ_cache(src, cache_dir)
    if shared_cached is not None:
        if failure_codes is not None:
            failure_codes.append("dwg_using_cached_dxf")
        logger.info("Reusing shared DWG DXF cache for %s -> %s", src.name, shared_cached)
        return shared_cached

    cache_key = source_cache_filename(
        src,
        namespace="preview_dxf",
        extension=".dxf",
        # Bumped 2026-06-16: the DxfWriter now emits R2000 subclass markers
        # (AcDbPolyline/AcDbMText) so ezdxf can read the export. Invalidate any
        # stale pre-fix cached DXF that lacks them (else the viewer keeps
        # reusing an export ezdxf rejects -> empty render).
        importer_version="canonical_debug_dxf:AC1015-r2000subclass",
        config_fingerprint="zone_vector_renderer:v1",
        digest_length=16,
    )
    cached = cache_dir / cache_key
    if cached.exists() and cached.stat().st_size > 0:
        if failure_codes is not None:
            failure_codes.append("dwg_using_cached_dxf")
        logger.info("Reusing cached DXF for %s -> %s", src.name, cached)
        return cached

    # L4 (2026-06-17): once native canonical normalisation has failed for this
    # exact source and we fell back to a cached DXF, skip the failing import on
    # every later zone/skeleton render. The live log showed the same "DWG vector
    # normalisation failed ... using cached DXF" 24x in one session, re-running
    # the unsupported-version import each time.
    _fail_key = _native_normalise_fail_key(src)
    _memo_fallback = _NATIVE_NORMALISE_FALLBACK_MEMO.get(_fail_key)
    if _memo_fallback:
        _fb = Path(_memo_fallback)
        if _fb.exists() and _fb.stat().st_size > 0:
            if failure_codes is not None:
                failure_codes.append("dwg_vector_normalise_failed")
            return _fb
        _NATIVE_NORMALISE_FALLBACK_MEMO.pop(_fail_key, None)

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
        # ODA auto-convert cache first (2026-06-12): the comparison pipeline
        # already converted this exact DWG via ODA (and OBJECTS-slimmed it).
        # Reusing that artifact makes zone vector renders share the SAME
        # effective drawing as the compare. Without this, every AC1018+ DWG
        # zone render failed native normalisation and the user saw the red
        # "벡터 렌더링 실패" badge — the 4-day recurring "미리보기 뷰어
        # 실패" report.
        fallback = _oda_autoconvert_cache(src, cache_dir)
        if fallback is None:
            fallback = _cached_dxf_fallback(src, cache_dir)
        if fallback is None:
            try:
                from .cache_paths import workbench_data_root

                legacy_cache_dir = workbench_data_root() / "dxf_cache"
                if legacy_cache_dir != cache_dir:
                    fallback = _cached_dxf_fallback(src, legacy_cache_dir)
            except Exception:
                logger.debug(
                    "Could not inspect legacy DXF cache for %s",
                    src,
                    exc_info=True,
                )
        if fallback is None:
            # Active ODA conversion (2026-06-18): every cache READ above missed —
            # e.g. the compare resolved this pair via a ``dxf_registered`` sibling
            # (dwg_dxf_fallback) so the ``oda_auto`` cache was never populated.
            # Convert the DWG NOW with the configured converter — the same
            # effective path the compare uses — instead of failing. Without this
            # the viewer raised DWG_UNSUPPORTED_VERSION on AC1018-1027 DWGs and the
            # user saw "미리보기 실패" even though the compare itself had succeeded
            # (live test 2026-06-18, AC1027 pair). ODA-unavailable returns
            # converted=False so the honest error below still fires — never a stub.
            try:
                from .dwg_dxf_fallback import auto_convert_unsupported_dwg

                converted, did_convert, _convert_note = auto_convert_unsupported_dwg(
                    src, cache_dir
                )
                converted_path = Path(converted)
                if (
                    did_convert
                    and converted_path.exists()
                    and converted_path.stat().st_size > 0
                ):
                    fallback = converted_path
            except Exception:
                logger.debug(
                    "Active ODA auto-convert fallback failed for %s",
                    src,
                    exc_info=True,
                )
        if fallback is not None:
            if failure_codes is not None:
                failure_codes.append("dwg_vector_normalise_failed")
            # Memo the failure so later renders skip the failing native import.
            _NATIVE_NORMALISE_FALLBACK_MEMO[_fail_key] = str(fallback)
            logger.warning(
                "DWG vector normalisation failed for %s; using cached DXF %s: %s",
                src.name,
                fallback.name,
                exc,
            )
            return fallback
        raise OSError(
            f"DWG canonical import/export failed for {src.name}: {exc}"
        ) from exc


# Per-source memo of "native canonical normalisation failed -> use this cached
# DXF fallback" (L4, 2026-06-17). Keyed by (path, mtime_ns, size) so an edited
# source re-attempts the native path. Process-local; cleared on restart.
_NATIVE_NORMALISE_FALLBACK_MEMO: dict[str, str] = {}


def _native_normalise_fail_key(src: Path) -> str:
    try:
        st = src.stat()
        return f"{src}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        return str(src)


def _oda_autoconvert_cache(source_path: Path, cache_dir: Path) -> Optional[Path]:
    """Locate the comparison pipeline's ODA-converted DXF for this DWG.

    auto_convert_unsupported_dwg caches conversions under
    ``<dxf_cache>/oda_auto/{stem}__{signature16}.dxf``. The zone renderer's
    ``cache_dir`` may or may not be that same root, so both the given dir
    and the workbench-standard dxf_cache are probed. Returns None quietly —
    this is an opportunistic reuse, never a requirement.
    """

    try:
        from .cache_paths import workbench_data_root
        from .dwg_dxf_fallback import source_cache_stem, source_signature_hash

        name = f"{source_cache_stem(source_path)}__{source_signature_hash(source_path)[:16]}.dxf"
        roots = [Path(cache_dir)]
        try:
            roots.append(workbench_data_root() / "dxf_cache")
        except Exception:  # noqa: BLE001
            pass
        for root in roots:
            candidate = root / "oda_auto" / name
            try:
                if candidate.exists() and candidate.stat().st_size > 0:
                    return candidate
            except OSError:
                continue
    except Exception:  # noqa: BLE001 - opportunistic probe stays silent
        logger.debug(
            "Could not probe ODA auto-convert cache for %s",
            source_path, exc_info=True,
        )
    return None


def _cached_dxf_fallback(source_path: Path, cache_dir: Path) -> Optional[Path]:
    """Return a non-empty compatible DXF cache for DWG vector rendering."""

    try:
        from .dwg_differ import DwgDiffer

        differ = DwgDiffer(
            config={
                "use_canonical_pipeline": False,
                "use_legacy_ezdxf_pipeline": True,
            },
            dxf_cache_dir=cache_dir,
        )
        exact = differ._dxf_cache_path(source_path)
        return differ._compatible_dxf_cache_path(source_path, exact_path=exact)
    except Exception:
        logger.debug("Could not resolve cached DXF fallback for %s", source_path, exc_info=True)
        return None


def _exact_dwg_differ_cache(source_path: Path, cache_dir: Path) -> Optional[Path]:
    """Return the strict shared DWG differ cache entry, never same-stem fallback."""

    try:
        from .dwg_differ import DwgDiffer

        differ = DwgDiffer(
            config={
                "use_canonical_pipeline": False,
                "use_legacy_ezdxf_pipeline": True,
            },
            dxf_cache_dir=cache_dir,
        )
        exact = differ._dxf_cache_path(source_path)
        if exact.exists() and exact.stat().st_size > 0:
            return exact
    except Exception:
        logger.debug("Could not resolve exact shared DXF cache for %s", source_path, exc_info=True)
    return None


def _fragile_skip_reason(skipped_count: int) -> str:
    if skipped_count <= 0:
        return ""
    return (
        f"Rendered {skipped_count} fragile MULTILEADER/MLEADER entities as "
        "simplified safe primitives because the ezdxf SVG renderer cannot "
        "safely expand their native representation."
    )


def _empty_zone_reason(skipped_fragile_count: int) -> str:
    reason = "No renderable DXF entities overlap the requested zone bbox."
    fragile_reason = _fragile_skip_reason(skipped_fragile_count)
    if fragile_reason:
        return f"{reason} {fragile_reason}"
    return reason


def _vec2_tuple(value) -> Optional[tuple[float, float]]:
    try:
        return (float(value.x), float(value.y))
    except Exception:
        pass
    try:
        return (float(value[0]), float(value[1]))
    except Exception:
        return None


def _distance2(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _mleader_plain_text(entity) -> str:
    try:
        text = str(entity.context.mtext.default_content or "").strip()
    except Exception:
        return ""
    # MText stores paragraph/control escapes. Keep this intentionally simple:
    # the fallback exists to preserve visual context, not to typeset MText.
    return (
        text.replace("\\P", " ")
        .replace("{", "")
        .replace("}", "")
        .strip()
    )


def _append_safe_mleader_primitives(entity, scratch_msp) -> int:
    """Append a simplified MULTILEADER representation to ``scratch_msp``.

    The native ezdxf MULTILEADER renderer can fail on missing style records in
    converted customer DXFs. A few line segments plus optional plain text is
    enough to keep the selected-zone viewer from going blank while avoiding
    the fragile native expansion path entirely.
    """

    try:
        context = entity.context
    except Exception:
        return 0

    added = 0
    attribs = {"color": 7}
    try:
        layer = str(entity.dxf.layer or "")
        if layer:
            attribs["layer"] = layer
    except Exception:
        pass

    for leader in getattr(context, "leaders", []) or []:
        last = _vec2_tuple(getattr(leader, "last_leader_point", None))
        for leader_line in getattr(leader, "lines", []) or []:
            points = [
                point
                for point in (
                    _vec2_tuple(vertex)
                    for vertex in (getattr(leader_line, "vertices", []) or [])
                )
                if point is not None
            ]
            if len(points) >= 2:
                for start, end in zip(points, points[1:]):
                    scratch_msp.add_line(start, end, dxfattribs=attribs)
                    added += 1
            elif len(points) == 1 and last is not None and _distance2(points[0], last) > 1e-9:
                scratch_msp.add_line(points[0], last, dxfattribs=attribs)
                added += 1
        dogleg_vector = _vec2_tuple(getattr(leader, "dogleg_vector", None))
        try:
            dogleg_length = float(getattr(leader, "dogleg_length", 0.0) or 0.0)
        except Exception:
            dogleg_length = 0.0
        if last is not None and dogleg_vector is not None and abs(dogleg_length) > 1e-9:
            end = (
                last[0] + dogleg_vector[0] * dogleg_length,
                last[1] + dogleg_vector[1] * dogleg_length,
            )
            if _distance2(last, end) > 1e-9:
                scratch_msp.add_line(last, end, dxfattribs=attribs)
                added += 1

    insert = None
    try:
        insert = _vec2_tuple(context.mtext.insert)
    except Exception:
        insert = _vec2_tuple(getattr(context, "base_point", None))
    try:
        char_height = max(float(getattr(context, "char_height", 0.0) or 0.0), 1.0)
    except Exception:
        char_height = 1.0
    text = _mleader_plain_text(entity)
    if insert is not None and text:
        scratch_msp.add_text(
            text,
            dxfattribs={
                **attribs,
                "height": char_height,
                "insert": insert,
            },
        )
        added += 1
    elif insert is not None and added == 0:
        # Empty-content leaders still need a visible anchor so annotation-only
        # zones do not render as an empty SVG.
        radius = max(char_height * 0.25, 1.0)
        scratch_msp.add_circle(insert, radius=radius, dxfattribs=attribs)
        added += 1

    return added


@dataclass(frozen=True)
class ZoneVectorRenderResult:
    """Outcome of one zone SVG render — surfaced back to the GUI.

    S1.3.2: ``failure_codes`` carries RenderFailureCode values accumulated
    during the render — DWG cache reuse, vector normalisation fallback,
    SVG draw failure, truncation, etc. The GUI badge (S1.4) reads this
    tuple to colour itself by the highest severity.
    """

    svg_path: str
    entity_count: int
    elapsed_ms: float
    world_bbox: Tuple[float, float, float, float]
    truncated: bool = False
    skipped_reason: str = ""
    failure_codes: Tuple[RenderFailureCode, ...] = ()

    def to_dict(self) -> dict:
        return {
            "svg_path": self.svg_path,
            "entity_count": self.entity_count,
            "elapsed_ms": self.elapsed_ms,
            "world_bbox": list(self.world_bbox),
            "truncated": self.truncated,
            "skipped_reason": self.skipped_reason,
            "failure_codes": list(self.failure_codes),
        }


KOREAN_SAFE_FONT = "malgun.ttf"


def patch_text_styles_for_legibility(doc) -> int:
    """Remap unrenderable text-style fonts to Malgun Gothic, in place.

    Live failure (2026-06-12, "텍스트가 제대로 렌더링되지 않는다"): real
    drawings carry STYLE entries with EMPTY font names ('돋움', '굴림',
    'SAMOO' …), SHX big-font pairs (romans.shx + whtgtxt.shx), or
    latin-only arial. ezdxf substitutes a latin fallback without Hangul
    glyph metrics, and the outlined text collapses into overlapping black
    blobs in the zone SVG (measured: 539 of ~870 text uses on the rebar
    sheet had an empty font). Malgun Gothic ships with every Windows
    10/11 and has full Hangul coverage — readability beats face fidelity
    for review crops. Returns the number of styles remapped.

    Call ONLY on a private/mutable document — never on the shared
    read-cache instance.
    """

    patched = 0
    for style in doc.styles:
        try:
            font = str(style.dxf.font or "").strip().lower()
            bigfont = str(getattr(style.dxf, "bigfont", "") or "").strip()
            if (not font) or font.endswith(".shx") or font.startswith("arial") or bigfont:
                style.dxf.font = KOREAN_SAFE_FONT
                try:
                    style.dxf.bigfont = ""
                except Exception:  # noqa: BLE001 - attribute may be absent
                    pass
                patched += 1
        except Exception:  # noqa: BLE001 - one broken style must not stop the rest
            continue
    return patched


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
            failure_codes=("vector_draw_failed",),
        )

    dxf_path = Path(dxf_path)
    if not dxf_path.exists():
        return ZoneVectorRenderResult(
            svg_path="",
            entity_count=0,
            elapsed_ms=0.0,
            world_bbox=zone_world_bbox,
            skipped_reason=f"DXF source not found: {dxf_path}",
            failure_codes=("vector_draw_failed",),
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
    # S1.3.2: resolve_dxf_path appends DWG-cache-related codes to
    # ``collected_codes`` so we can forward them into the result.
    collected_codes: list[RenderFailureCode] = []
    try:
        dxf_path = resolve_dxf_path(dxf_path, failure_codes=collected_codes)
    except (FileNotFoundError, OSError) as exc:
        return ZoneVectorRenderResult(
            svg_path="",
            entity_count=0,
            elapsed_ms=0.0,
            world_bbox=zone_world_bbox,
            skipped_reason=f"DWG normalisation failed: {exc}",
            failure_codes=tuple(collected_codes) + ("vector_draw_failed",),
        )

    start = time.perf_counter()

    # Pad bbox outward so the reviewer sees context around the change.
    padded = _pad_bbox(zone_world_bbox, padding_ratio)
    zone_bbox = BoundingBox2d([(padded[0], padded[1]), (padded[2], padded[3])])

    try:
        # mutable=True: we remap text-style fonts below, which must never
        # touch the shared read-cache document other callers reuse.
        read_result = read_dxf_document_result(
            dxf_path, ezdxf_module=ezdxf, mutable=True
        )
        doc = read_result.doc
        patched_styles = patch_text_styles_for_legibility(doc)
        if patched_styles:
            logger.info(
                "Zone render: remapped %d text styles to %s for legibility",
                patched_styles, KOREAN_SAFE_FONT,
            )
    except Exception as exc:
        return ZoneVectorRenderResult(
            svg_path="",
            entity_count=0,
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
            world_bbox=zone_world_bbox,
            skipped_reason=f"ezdxf.readfile failed: {exc}",
            failure_codes=tuple(collected_codes) + ("vector_draw_failed",),
        )
    msp = doc.modelspace()
    scratch_doc = ezdxf.new("R2010")
    scratch_msp = scratch_doc.modelspace()

    # Stateful counters so recursive INSERT expansion can enforce the cap and
    # propagate a truncated flag back without raising.
    accepted_count = [0]
    truncated = [False]
    skipped_fragile_count = [0]
    render_entities = []

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
        if _is_fragile_vector_entity(entity):
            skipped_fragile_count[0] += 1
            return False
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

    def _is_insert(entity) -> bool:
        try:
            return str(entity.dxftype()).upper() == "INSERT"
        except Exception:
            return False

    def _collect_zone_entity(entity, depth: int = 0) -> None:
        if accepted_count[0] >= max_entities:
            truncated[0] = True
            return
        if _is_fragile_vector_entity(entity):
            added = _append_safe_mleader_primitives(entity, scratch_msp)
            if added:
                render_entities.extend(list(scratch_msp)[-added:])
                accepted_count[0] += added
                skipped_fragile_count[0] += 1
                if accepted_count[0] >= max_entities:
                    truncated[0] = True
            else:
                skipped_fragile_count[0] += 1
            return
        ent_bbox_2d = _entity_bbox_2d(entity)
        if ent_bbox_2d is not None and not zone_bbox.has_overlap(ent_bbox_2d):
            return
        if _is_insert(entity) and depth < 8:
            try:
                for child in entity.virtual_entities():
                    _collect_zone_entity(child, depth + 1)
                    if accepted_count[0] >= max_entities:
                        break
            except Exception as exc:
                skipped_fragile_count[0] += 1
                logger.debug(
                    "Skipping fragile INSERT during zone SVG render: %s",
                    exc,
                    exc_info=True,
                )
            return
        # Some entities (proxy graphics, malformed text, etc.) refuse bbox
        # computation. Be inclusive: better to render an extra entity than
        # silently lose change geometry.
        render_entities.append(entity)
        accepted_count[0] += 1
        if accepted_count[0] >= max_entities:
            truncated[0] = True

    for entity in msp:
        _collect_zone_entity(entity)
        if accepted_count[0] >= max_entities:
            break

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

    try:
        frontend = Frontend(ctx=RenderContext(doc), out=backend, config=cfg)
        try:
            frontend.set_background(background_color)
        except Exception:
            pass
        frontend.draw_entities(render_entities)
        frontend.pipeline.finalize()
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.warning(
            "Zone SVG draw failed after %d accepted entities for %s: %s",
            accepted_count[0],
            dxf_path.name,
            exc,
            exc_info=True,
        )
        return ZoneVectorRenderResult(
            svg_path="",
            entity_count=accepted_count[0],
            elapsed_ms=elapsed_ms,
            world_bbox=padded,
            truncated=truncated[0],
            skipped_reason=(
                f"SVG draw failed: {type(exc).__name__}: {exc}. "
                "The raster/background viewer should remain available."
            ),
            failure_codes=tuple(collected_codes) + ("vector_draw_failed",),
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
            skipped_reason=_empty_zone_reason(skipped_fragile_count[0]),
            # S1.3.2: no-content is a normal outcome, not a failure — only
            # forward DWG-cache codes accumulated by resolve_dxf_path.
            failure_codes=tuple(collected_codes),
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
    # S1.3.2: success path — forward DWG-cache codes and add
    # vector_draw_partial when the entity cap truncated the output.
    success_codes = tuple(collected_codes)
    if truncated[0]:
        success_codes = success_codes + ("vector_draw_partial",)
    return ZoneVectorRenderResult(
        svg_path=str(output_svg),
        entity_count=accepted_count[0],
        elapsed_ms=elapsed_ms,
        world_bbox=padded,
        truncated=truncated[0],
        skipped_reason=_fragile_skip_reason(skipped_fragile_count[0]),
        failure_codes=success_codes,
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
