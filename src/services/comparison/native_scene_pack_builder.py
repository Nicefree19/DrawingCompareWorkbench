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
from typing import Any, List, Mapping, Optional, Tuple

from .native_scene_pack import BBox, NativeScenePack

#: Straight segments used to approximate a full circle. Arcs subdivide
#: proportionally to their swept angle. 64 keeps the LOD0 outline smooth while
#: staying far under the viewer primitive budget for the small AC1015 slice.
DEFAULT_CIRCLE_SEGMENTS = 64

#: Canonical entity types this producer can flatten into ``lines`` primitives.
SUPPORTED_ENTITY_TYPES = frozenset({"line", "circle", "arc", "polyline"})

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
    display_primitives: List[dict[str, Any]] = []
    unsupported: dict[str, int] = {}

    for entity in entities:
        etype = str(entity.get("type") or "").lower()
        primitive = _entity_to_primitive(entity, etype, circle_segments)
        if primitive is None:
            unsupported[etype or "?"] = unsupported.get(etype or "?", 0) + 1
            continue
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
        },
    )


def _entity_to_primitive(
    entity: Mapping[str, Any], etype: str, circle_segments: int
) -> Optional[dict[str, Any]]:
    geometry = entity.get("geometry")
    if not isinstance(geometry, Mapping):
        return None
    if etype == "line":
        segments = _line_segments(geometry)
    elif etype == "polyline":
        segments = _polyline_segments(geometry)
    elif etype == "circle":
        segments = _circle_segments(geometry, circle_segments)
    elif etype == "arc":
        segments = _arc_segments(geometry, circle_segments)
    else:
        return None
    if not segments:
        return None
    return {
        "id": str(entity.get("id") or ""),
        "type": "lines",
        "geometry": segments,
        "layer": str(entity.get("layer_id") or ""),
        "source_entity_type": etype,
    }


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
]
