# -*- coding: utf-8 -*-
"""Build a :class:`NativeScenePack` from clean-room native-reader output.

This is the producer that was missing for ``viewer_lod0_real_evidence_pending``.
The AC1015 clean-room native reader emits *canonical* entities only (no ezdxf,
no scene pack, no display primitives), so the existing native-CAD viewer
evidence path could only run on the bridge/fixture adapter. This module closes
that gap by flattening already-decoded canonical LINE/CIRCLE/ARC/POLYLINE
geometry into the viewer ``lines`` primitive vocabulary, producing a real
:class:`NativeScenePack` (and therefore a real
``native-cad-viewer-evidence/v1`` LOD0 packet) directly from a native import.

Scope and honesty boundaries:

* It is **read-only** with respect to the import document — it only reads
  ``doc["entities"]`` / ``doc["extents"]`` and never mutates them.
* It does **not** broaden default DWG support: it flattens geometry the
  clean-room reader already decoded and *visibly records* any entity type it
  cannot flatten (no silent drops).
* It does **not** use ezdxf, ODA, or any bridge — the real product viewer
  pipeline (``scene_pack_builder.build_scene_pack``) still uses ezdxf; this is
  the parallel native-evidence producer only.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

from .aci_palette import aci_to_hex
from .native_scene_pack import BBox, NativeScenePack, write_native_scene_pack_artifacts

#: Straight segments used to approximate a full circle. Arcs subdivide
#: proportionally to their swept angle. 64 keeps the LOD0 outline smooth while
#: staying far under the viewer primitive budget for the small AC1015 slice.
DEFAULT_CIRCLE_SEGMENTS = 64

#: Canonical entity types this producer can flatten into ``lines`` primitives.
#: ``ellipse`` tessellates from its full canonical geometry (full fidelity).
#: ``hatch`` renders only its decoded boundary-extent rectangle (the canonical
#: doc does not carry boundary loops) and is therefore tagged ``partial`` — an
#: honest, visible degraded render rather than a silent drop ([[silent_fallback_pattern]]).
SUPPORTED_ENTITY_TYPES = frozenset(
    {"line", "circle", "arc", "polyline", "ellipse", "hatch",
     "text", "mtext", "dimension", "insert"}
)

#: Types emitted as a ``text`` primitive (positioned label) rather than ``lines``.
TEXT_LIKE_ENTITY_TYPES = frozenset({"text", "mtext", "dimension"})

#: Types rendered as a deliberately partial representation (boundary box / marker
#: / measurement label) because the canonical document does not carry enough
#: geometry for full fidelity: HATCH boundary loops, INSERT block definitions,
#: and DIMENSION definition lines are not in canonical. Honest, visibly flagged,
#: never a silent drop ([[silent_fallback_pattern]]).
PARTIAL_RENDER_ENTITY_TYPES = frozenset({"hatch", "insert", "dimension"})

PRODUCER_ID = "native_scene_pack_builder/v1"

_Segment = List[float]


def build_native_scene_pack(
    doc: Mapping[str, Any],
    *,
    circle_segments: int = DEFAULT_CIRCLE_SEGMENTS,
) -> NativeScenePack:
    """Flatten one native-import canonical document into a scene pack.

    Args:
        doc: A canonical drawing document produced by ``DwgImporter`` with the
            clean-room native adapter (``doc["entities"]`` + ``doc["extents"]``).
        circle_segments: Tessellation budget for a full circle; arcs scale down.

    Returns:
        A :class:`NativeScenePack` whose ``display_primitives`` are viewer
        ``lines`` primitives. Entity types outside
        :data:`SUPPORTED_ENTITY_TYPES` are counted in ``warnings`` /
        ``metadata`` rather than dropped silently.
    """

    entities = [e for e in (doc.get("entities") or []) if isinstance(e, Mapping)]
    # Block definitions let an INSERT expand its block geometry (transformed)
    # instead of rendering a marker box. Pre-flatten each block to local segments.
    block_segments = _block_local_segments(doc.get("blocks"), circle_segments)
    display_primitives: List[dict[str, Any]] = []
    unsupported: dict[str, int] = {}
    partial: dict[str, int] = {}

    for entity in entities:
        etype = str(entity.get("type") or "").lower()
        primitive = _entity_to_primitive(entity, etype, circle_segments, block_segments)
        if primitive is None:
            unsupported[etype or "?"] = unsupported.get(etype or "?", 0) + 1
            continue
        if primitive.get("properties", {}).get("partial"):
            partial[etype or "?"] = partial.get(etype or "?", 0) + 1
        display_primitives.append(primitive)

    bbox = _bbox_from_extents(doc.get("extents")) or _bbox_from_primitives(display_primitives)

    drawing = doc.get("drawing") if isinstance(doc.get("drawing"), Mapping) else {}
    source = dict(drawing.get("source") or {}) if isinstance(drawing, Mapping) else {}
    importer = dict(drawing.get("importer") or {}) if isinstance(drawing, Mapping) else {}
    report = doc.get("import_report") if isinstance(doc.get("import_report"), Mapping) else {}
    report_adapter = report.get("adapter") if isinstance(report.get("adapter"), Mapping) else {}

    warnings: List[dict[str, Any]] = []
    if unsupported:
        warnings.append(
            {
                "code": "native_scene_pack_unsupported_entity_type",
                "unsupported_type_counts": dict(unsupported),
            }
        )
    if partial:
        warnings.append(
            {
                "code": "native_scene_pack_partial_render",
                "partial_type_counts": dict(partial),
            }
        )

    return NativeScenePack(
        source=source,
        adapter={
            "name": report_adapter.get("name") or importer.get("name") or "native-ac1015",
            "backend": importer.get("backend"),
            "backend_version": importer.get("backend_version"),
            "producer": PRODUCER_ID,
        },
        layers=[dict(layer) for layer in (doc.get("layers") or []) if isinstance(layer, Mapping)],
        blocks=[dict(block) for block in (doc.get("blocks") or []) if isinstance(block, Mapping)],
        entities=[dict(entity) for entity in entities],
        display_primitives=display_primitives,
        bbox=bbox,
        warnings=warnings,
        metadata={
            "producer": PRODUCER_ID,
            "entity_count": len(entities),
            "primitive_count": len(display_primitives),
            "unsupported_entity_type_counts": dict(unsupported),
            "partial_render_entity_type_counts": dict(partial),
        },
    )


def build_native_scene_pack_ref(
    doc: Mapping[str, Any],
    output_dir: "str | Path",
    *,
    circle_segments: int = DEFAULT_CIRCLE_SEGMENTS,
):
    """Build a native scene pack and persist it as a viewer ``ScenePackRef``.

    This is the foundation that lets a clean-room native import drive the SAME
    lightweight viewport seam (``resolve_viewer_primitive_source``) the ezdxf
    scene pack uses, with no ezdxf/ODA. The returned ``ScenePackRef`` points at a
    native ``overview_lod0.json`` whose ``source_kind`` is ``native_cad``.

    Honest scope: the clean-room reader decodes only LINE/CIRCLE/ARC/POLYLINE on
    AC1015 today, so the resulting preview is a strict subset of an ezdxf render.
    Callers must treat it as a partial/fallback preview, never as a replacement
    for the ezdxf scene pack on drawings that carry richer entity types.
    """

    pack = build_native_scene_pack(doc, circle_segments=circle_segments)
    return write_native_scene_pack_artifacts(pack, Path(output_dir))


def _entity_to_primitive(
    entity: Mapping[str, Any],
    etype: str,
    circle_segments: int,
    block_segments: Optional[Mapping[str, List[_Segment]]] = None,
) -> Optional[dict[str, Any]]:
    geometry = entity.get("geometry")
    if not isinstance(geometry, Mapping):
        return None
    if etype in TEXT_LIKE_ENTITY_TYPES:
        return _text_primitive(entity, etype, geometry)
    partial = False
    render = ""
    expanded_block = ""
    if etype == "line":
        segments = _line_segments(geometry)
    elif etype == "polyline":
        segments = _polyline_segments(geometry)
    elif etype == "circle":
        segments = _circle_segments(geometry, circle_segments)
    elif etype == "arc":
        segments = _arc_segments(geometry, circle_segments)
    elif etype == "ellipse":
        segments = _ellipse_segments(geometry, circle_segments)
    elif etype == "hatch":
        # Prefer the real decoded boundary loops; fall back to the boundary-extent
        # rectangle only when canonical carries no loops (honest partial). A loop
        # with an inexact edge (elliptical-arc / spline / bulge) keeps the partial
        # flag even though the lines render — never claims exact when it is not.
        segments, full_boundary = _hatch_boundary_segments(geometry)
        if segments:
            if not full_boundary:
                partial, render = True, "boundary_loops_approx"
        else:
            segments = _bbox_rectangle_segments(entity)
            partial, render = True, "boundary_box"
    elif etype == "insert":
        # Expand the referenced block's geometry (transformed by insert/scale/
        # rotation) when a block definition is available; otherwise fall back to
        # the insert marker box (honest partial — block defs absent).
        block_name = str(geometry.get("block_name") or "")
        local = block_segments.get(block_name) if block_segments else None
        if local:
            segments = _transform_insert_segments(local, geometry)
            expanded_block = block_name
        else:
            segments = _bbox_rectangle_segments(entity)
            partial, render = True, "insert_marker"
    else:
        return None
    if not segments:
        return None
    primitive: dict[str, Any] = {
        "id": str(entity.get("id") or ""),
        "type": "lines",
        "geometry": segments,
        "layer": str(entity.get("layer_id") or ""),
        "source_entity_type": etype,
    }
    properties = _style_properties(entity)
    if partial:
        properties["partial"] = True
        properties["render"] = render
    if expanded_block:
        properties["expanded_block"] = expanded_block
    if properties:
        primitive["properties"] = properties
    return primitive


def _text_primitive(
    entity: Mapping[str, Any], etype: str, geometry: Mapping[str, Any]
) -> Optional[dict[str, Any]]:
    """Build a ``text`` primitive (positioned label) for TEXT/MTEXT/DIMENSION.

    The QML viewport renders ``type: "text"`` primitives with ``fillText`` at the
    world insertion point (font in world units). DIMENSION renders only its
    measurement label at the text midpoint (definition lines are not carried in
    canonical) and is flagged ``partial``.
    """

    if etype == "dimension":
        xy = _point_xy(geometry.get("text_midpoint"))
        value = geometry.get("text") or geometry.get("canonical_text") or ""
        if not str(value).strip():
            measurement = geometry.get("measurement")
            value = f"{float(measurement):.4g}" if isinstance(measurement, (int, float)) else ""
        height, rotation = 2.5, 0.0
    else:  # text, mtext
        xy = _point_xy(geometry.get("insert"))
        value = geometry.get("text") or geometry.get("canonical_text") or ""
        try:
            height = float(geometry.get("height"))
        except (TypeError, ValueError):
            height = 2.5
        try:
            rotation = float(geometry.get("rotation_deg") or 0.0)
        except (TypeError, ValueError):
            rotation = 0.0
    value = str(value)
    if xy is None or not value.strip():
        return None
    if not math.isfinite(height) or height <= 0:
        height = 2.5
    primitive: dict[str, Any] = {
        "id": str(entity.get("id") or ""),
        "type": "text",
        "x": xy[0],
        "y": xy[1],
        "height": height,
        "rotation": rotation,
        "text": value,
        "layer": str(entity.get("layer_id") or ""),
        "source_entity_type": etype,
    }
    properties = _style_properties(entity)
    if etype == "dimension":
        properties["partial"] = True
        properties["render"] = "measurement_text"
    if properties:
        primitive["properties"] = properties
    return primitive


def _style_properties(entity: Mapping[str, Any]) -> dict[str, Any]:
    """Carry canonical ``style`` (ACI color + linetype) into render properties.

    The QML viewport already reads ``prim.properties.color`` per primitive, so
    emitting it switches the native render from a single monochrome ink to
    per-entity color. ``color`` is omitted for BYLAYER/BYBLOCK/theme-ink indices
    (the viewport then uses its default ink). ``linetype`` carries the name;
    dash-pattern rendering is a documented follow-up.
    """

    style = entity.get("style")
    if not isinstance(style, Mapping):
        return {}
    properties: dict[str, Any] = {}
    color_hex = aci_to_hex(style.get("color"))
    if color_hex:
        properties["color"] = color_hex
    linetype = style.get("linetype")
    if linetype not in (None, "", "BYLAYER", "BYBLOCK", "CONTINUOUS"):
        properties["linetype"] = str(linetype)
    return properties


def _point_xy(value: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(value, Mapping):
        return None
    try:
        return (float(value["x"]), float(value["y"]))
    except (KeyError, TypeError, ValueError):
        return None


def _line_segments(geometry: Mapping[str, Any]) -> List[_Segment]:
    start = _point_xy(geometry.get("start"))
    end = _point_xy(geometry.get("end"))
    if start is None or end is None:
        return []
    return [[start[0], start[1], end[0], end[1]]]


def _polyline_segments(geometry: Mapping[str, Any]) -> List[_Segment]:
    points: List[Tuple[float, float]] = []
    for vertex in geometry.get("vertices") or []:
        if not isinstance(vertex, Mapping):
            continue
        point = _point_xy(vertex.get("point"))
        if point is not None:
            points.append(point)
    if len(points) < 2:
        return []
    segments = [
        [points[i][0], points[i][1], points[i + 1][0], points[i + 1][1]]
        for i in range(len(points) - 1)
    ]
    if geometry.get("closed") and len(points) >= 3:
        segments.append([points[-1][0], points[-1][1], points[0][0], points[0][1]])
    return segments


def _circle_segments(geometry: Mapping[str, Any], circle_segments: int) -> List[_Segment]:
    center = _point_xy(geometry.get("center"))
    try:
        radius = float(geometry.get("radius"))
    except (TypeError, ValueError):
        return []
    if center is None or radius <= 0 or circle_segments < 3:
        return []
    points = [
        (
            center[0] + radius * math.cos(2.0 * math.pi * i / circle_segments),
            center[1] + radius * math.sin(2.0 * math.pi * i / circle_segments),
        )
        for i in range(circle_segments)
    ]
    points.append(points[0])
    return [
        [points[i][0], points[i][1], points[i + 1][0], points[i + 1][1]]
        for i in range(len(points) - 1)
    ]


def _arc_segments(geometry: Mapping[str, Any], circle_segments: int) -> List[_Segment]:
    center = _point_xy(geometry.get("center"))
    try:
        radius = float(geometry.get("radius"))
        start = math.radians(float(geometry.get("start_angle_deg")))
        end = math.radians(float(geometry.get("end_angle_deg")))
    except (TypeError, ValueError):
        return []
    if center is None or radius <= 0:
        return []
    sweep = end - start
    if str(geometry.get("sweep_direction") or "ccw").lower() == "cw":
        while sweep >= 0:
            sweep -= 2.0 * math.pi
    else:
        while sweep <= 0:
            sweep += 2.0 * math.pi
    steps = max(2, int(abs(sweep) / (2.0 * math.pi) * circle_segments) + 1)
    points = [
        (
            center[0] + radius * math.cos(start + sweep * k / steps),
            center[1] + radius * math.sin(start + sweep * k / steps),
        )
        for k in range(steps + 1)
    ]
    return [
        [points[i][0], points[i][1], points[i + 1][0], points[i + 1][1]]
        for i in range(len(points) - 1)
    ]


def _ellipse_segments(geometry: Mapping[str, Any], circle_segments: int) -> List[_Segment]:
    """Tessellate a canonical ellipse arc (center/major_axis/ratio/params).

    ``major_axis`` is the vector from the center to the major-axis endpoint
    (length = semi-major a); the minor semi-axis is ``a * ratio`` along the
    perpendicular. Parameters are in radians; a full ellipse is start==end.
    """

    center = _point_xy(geometry.get("center"))
    major = _point_xy(geometry.get("major_axis"))
    if center is None or major is None:
        return []
    try:
        ratio = float(geometry.get("ratio"))
        start = float(geometry.get("start_param"))
        end = float(geometry.get("end_param"))
    except (TypeError, ValueError):
        return []
    a = math.hypot(major[0], major[1])
    if a <= 0 or circle_segments < 3:
        return []
    ux, uy = major[0] / a, major[1] / a  # major-axis unit vector
    vx, vy = -uy, ux  # minor-axis unit vector (+90 degrees)
    b = a * ratio
    sweep = end - start
    if abs(sweep) < 1e-12:
        sweep = 2.0 * math.pi
    steps = max(2, int(abs(sweep) / (2.0 * math.pi) * circle_segments) + 1)
    points = []
    for k in range(steps + 1):
        t = start + sweep * k / steps
        ca, sa = math.cos(t), math.sin(t)
        x = center[0] + a * ca * ux + b * sa * vx
        y = center[1] + a * ca * uy + b * sa * vy
        points.append((x, y))
    return [
        [points[i][0], points[i][1], points[i + 1][0], points[i + 1][1]]
        for i in range(len(points) - 1)
    ]


def _block_local_segments(
    blocks: Any, circle_segments: int
) -> dict[str, List[_Segment]]:
    """Pre-flatten each block definition's entities into block-LOCAL line segments.

    Keyed by block name. A block entity that does not flatten to ``lines`` (TEXT/
    DIMENSION/nested INSERT/unsupported) is skipped here — block expansion renders
    only the directly-flattenable line geometry (an honest subset; the marker-box
    fallback still applies when a block yields no segments). Returns ``{}`` when no
    blocks are present."""

    out: dict[str, List[_Segment]] = {}
    if not isinstance(blocks, (list, tuple)):
        return out
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        name = str(block.get("name") or "")
        if not name:
            continue
        segments: List[_Segment] = []
        for entity in block.get("entities") or []:
            if not isinstance(entity, Mapping):
                continue
            etype = str(entity.get("type") or "").lower()
            geometry = entity.get("geometry")
            if not isinstance(geometry, Mapping):
                continue
            if etype == "line":
                segments.extend(_line_segments(geometry))
            elif etype == "polyline":
                segments.extend(_polyline_segments(geometry))
            elif etype == "circle":
                segments.extend(_circle_segments(geometry, circle_segments))
            elif etype == "arc":
                segments.extend(_arc_segments(geometry, circle_segments))
            elif etype == "ellipse":
                segments.extend(_ellipse_segments(geometry, circle_segments))
            # TEXT/DIMENSION/nested-INSERT/HATCH/unsupported are not expanded here.
        if segments:
            out[name] = segments
    return out


def _transform_insert_segments(
    local_segments: List[_Segment], geometry: Mapping[str, Any]
) -> List[_Segment]:
    """Transform block-local segments by an INSERT's insertion/scale/rotation.

    World = insertion + R(rotation) · S(scale) · local. Matches the standard DWG
    INSERT placement (uniform 2D transform; the Z scale is irrelevant for the 2D
    line skeleton). Non-finite / zero transforms fall back to identity-ish safe
    values so a degenerate INSERT still renders its block at the insertion point."""

    insert = _point_xy(geometry.get("insert")) or (0.0, 0.0)
    scale = geometry.get("scale")
    sx = sy = 1.0
    if isinstance(scale, Mapping):
        try:
            sx = float(scale.get("x"))
            sy = float(scale.get("y"))
        except (TypeError, ValueError):
            sx = sy = 1.0
    if not math.isfinite(sx) or sx == 0.0:
        sx = 1.0
    if not math.isfinite(sy) or sy == 0.0:
        sy = 1.0
    try:
        rotation = math.radians(float(geometry.get("rotation_deg") or 0.0))
    except (TypeError, ValueError):
        rotation = 0.0
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    ox, oy = insert

    def xf(x: float, y: float) -> Tuple[float, float]:
        lx, ly = x * sx, y * sy
        return (ox + lx * cos_r - ly * sin_r, oy + lx * sin_r + ly * cos_r)

    out: List[_Segment] = []
    for seg in local_segments:
        if len(seg) < 4:
            continue
        x0, y0 = xf(seg[0], seg[1])
        x1, y1 = xf(seg[2], seg[3])
        out.append([x0, y0, x1, y1])
    return out


def _hatch_boundary_segments(
    geometry: Mapping[str, Any]
) -> Tuple[List[_Segment], bool]:
    """Build line segments from a HATCH's decoded boundary loops.

    Each loop is a vertex polyline; consecutive vertices become segments and a
    closed loop adds the closing segment. Returns ``(segments, full_boundary)``
    where ``full_boundary`` is True only when at least one loop rendered AND every
    rendered loop is ``exact`` (LINE/ARC/polyline edges). A loop with an inexact
    edge (elliptical-arc / spline / bulged) still renders its vertex chain but
    marks the boundary not-full so the primitive stays flagged ``partial`` — an
    honest degraded render, never a silent claim of fidelity
    ([[silent_fallback_pattern]]).
    """

    loops = geometry.get("boundary_loops")
    if not isinstance(loops, (list, tuple)) or not loops:
        return [], False
    segments: List[_Segment] = []
    all_exact = True
    rendered_any = False
    for loop in loops:
        if not isinstance(loop, Mapping):
            continue
        points: List[Tuple[float, float]] = []
        for vertex in loop.get("vertices") or []:
            point = _point_xy(vertex)
            if point is not None:
                points.append(point)
        if len(points) < 2:
            continue
        rendered_any = True
        if not loop.get("exact", True):
            all_exact = False
        for i in range(len(points) - 1):
            segments.append([points[i][0], points[i][1], points[i + 1][0], points[i + 1][1]])
        if loop.get("closed") and len(points) >= 3:
            segments.append([points[-1][0], points[-1][1], points[0][0], points[0][1]])
    if not rendered_any:
        return [], False
    return segments, all_exact


def _bbox_rectangle_segments(entity: Mapping[str, Any]) -> List[_Segment]:
    """Closed rectangle of an entity's decoded world bbox (``entity['bbox']``).

    Used for HATCH (boundary extent) and INSERT (marker box) partial renders.
    """

    bbox = entity.get("bbox")
    if not isinstance(bbox, Mapping):
        return []
    try:
        x0 = float(bbox["min_x"])
        y0 = float(bbox["min_y"])
        x1 = float(bbox["max_x"])
        y1 = float(bbox["max_y"])
    except (KeyError, TypeError, ValueError):
        return []
    if x1 <= x0 or y1 <= y0:
        # Degenerate in EITHER axis (zero width or height) -> no rectangle; the
        # entity is then counted unsupported (not silently rendered as a line).
        return []
    return [
        [x0, y0, x1, y0],
        [x1, y0, x1, y1],
        [x1, y1, x0, y1],
        [x0, y1, x0, y0],
    ]


def _bbox_from_extents(extents: Any) -> Optional[BBox]:
    if not isinstance(extents, Mapping):
        return None
    try:
        return (
            float(extents["min_x"]),
            float(extents["min_y"]),
            float(extents["max_x"]),
            float(extents["max_y"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _bbox_from_primitives(primitives: List[Mapping[str, Any]]) -> BBox:
    xs: List[float] = []
    ys: List[float] = []
    for primitive in primitives:
        for segment in primitive.get("geometry") or []:
            if isinstance(segment, (list, tuple)) and len(segment) >= 4:
                xs.extend((segment[0], segment[2]))
                ys.extend((segment[1], segment[3]))
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


__all__ = [
    "DEFAULT_CIRCLE_SEGMENTS",
    "SUPPORTED_ENTITY_TYPES",
    "PRODUCER_ID",
    "build_native_scene_pack",
    "build_native_scene_pack_ref",
]
