"""CanonicalDrawing normalization utilities.

The normalizer operates after format-specific importers have produced the
CanonicalDrawing dictionary model.  It removes save-version noise while keeping
raw source metadata intact enough for diagnostics.
"""
from __future__ import annotations

import copy
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .dxf_importer import (
    _arc_bbox,
    _bbox_from_points,
    _circle_bbox,
    _ellipse_points,
    _geometry_hash_payload,
    _hash_payload,
    _make_point,
    _normalize_angle,
    _text_bbox,
    _union_bbox,
)


Point3 = Dict[str, float]
BBox = Dict[str, float | str]


@dataclass(frozen=True)
class NormalizationOptions:
    """Options controlling geometry, style, and text normalization."""

    coordinate_quantum_mm: float = 0.01
    bbox_quantum_mm: float = 0.01
    angle_quantum_deg: float = 0.001
    scale_quantum: float = 1e-9
    vertex_merge_tolerance_mm: float = 0.01
    near_zero_length_mm: float = 0.01
    near_zero_area_mm2: float = 0.0001
    flatten_curves: bool = False
    flatten_tolerance_mm: float = 0.1
    max_flatten_segments: int = 128
    resolve_bylayer_byblock: bool = True
    normalize_text: bool = True
    strip_mtext_formatting: bool = True
    normalize_polyline_direction: bool = True
    normalize_polyline_vertices: bool = True
    remove_near_zero_geometry: bool = True
    update_hashes: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coordinate_quantum_mm": self.coordinate_quantum_mm,
            "bbox_quantum_mm": self.bbox_quantum_mm,
            "angle_quantum_deg": self.angle_quantum_deg,
            "scale_quantum": self.scale_quantum,
            "vertex_merge_tolerance_mm": self.vertex_merge_tolerance_mm,
            "near_zero_length_mm": self.near_zero_length_mm,
            "near_zero_area_mm2": self.near_zero_area_mm2,
            "flatten_curves": self.flatten_curves,
            "flatten_tolerance_mm": self.flatten_tolerance_mm,
            "max_flatten_segments": self.max_flatten_segments,
            "resolve_bylayer_byblock": self.resolve_bylayer_byblock,
            "normalize_text": self.normalize_text,
            "strip_mtext_formatting": self.strip_mtext_formatting,
            "normalize_polyline_direction": self.normalize_polyline_direction,
            "normalize_polyline_vertices": self.normalize_polyline_vertices,
            "remove_near_zero_geometry": self.remove_near_zero_geometry,
            "update_hashes": self.update_hashes,
        }


@dataclass
class NormalizationChange:
    """One tracked normalization change."""

    entity_id: str
    code: str
    message: str
    before: Any = None
    after: Any = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "entity_id": self.entity_id,
            "code": self.code,
            "message": self.message,
        }
        if self.before is not None:
            out["before"] = self.before
        if self.after is not None:
            out["after"] = self.after
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class NormalizationReport:
    """Summary and audit trail for a normalization run."""

    input_entity_count: int = 0
    output_entity_count: int = 0
    changed_entity_count: int = 0
    removed_entity_count: int = 0
    rounded_coordinate_count: int = 0
    removed_near_zero_count: int = 0
    normalized_polyline_count: int = 0
    flattened_curve_count: int = 0
    resolved_style_count: int = 0
    normalized_text_count: int = 0
    recomputed_hash_count: int = 0
    changes: List[NormalizationChange] = field(default_factory=list)
    _changed_entity_ids: set[str] = field(default_factory=set, repr=False)

    def add_change(
        self,
        entity_id: str,
        code: str,
        message: str,
        *,
        before: Any = None,
        after: Any = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.changes.append(
            NormalizationChange(
                entity_id=entity_id,
                code=code,
                message=message,
                before=before,
                after=after,
                details=details or {},
            )
        )
        self._changed_entity_ids.add(entity_id)
        self.changed_entity_count = len(self._changed_entity_ids)

    def to_dict(self) -> Dict[str, Any]:
        counts_by_code = Counter(change.code for change in self.changes)
        return {
            "input_entity_count": self.input_entity_count,
            "output_entity_count": self.output_entity_count,
            "changed_entity_count": self.changed_entity_count,
            "removed_entity_count": self.removed_entity_count,
            "rounded_coordinate_count": self.rounded_coordinate_count,
            "removed_near_zero_count": self.removed_near_zero_count,
            "normalized_polyline_count": self.normalized_polyline_count,
            "flattened_curve_count": self.flattened_curve_count,
            "resolved_style_count": self.resolved_style_count,
            "normalized_text_count": self.normalized_text_count,
            "recomputed_hash_count": self.recomputed_hash_count,
            "counts_by_code": dict(sorted(counts_by_code.items())),
            "changes": [change.to_dict() for change in self.changes],
        }


class DrawingNormalizer:
    """Normalize CanonicalDrawing dictionaries for format-agnostic comparison."""

    def __init__(self, options: Optional[NormalizationOptions] = None):
        self.options = options or NormalizationOptions()

    def normalize(self, drawing: Dict[str, Any]) -> Tuple[Dict[str, Any], NormalizationReport]:
        normalized = copy.deepcopy(drawing)
        entities = list(normalized.get("entities") or [])
        report = NormalizationReport(input_entity_count=len(entities))

        layer_by_id = {layer.get("id"): layer for layer in normalized.get("layers") or []}
        entity_by_id = {entity.get("id"): entity for entity in entities}
        style_cache: Dict[str, Dict[str, Any]] = {}

        kept_entities: List[Dict[str, Any]] = []
        for entity in entities:
            entity_id = str(entity.get("id") or "")
            if self.options.flatten_curves and self._flatten_curve_entity(entity):
                report.flattened_curve_count += 1
                report.add_change(
                    entity_id,
                    "CURVE_FLATTENED",
                    "Spline or ellipse geometry was flattened to canonical polyline.",
                )

            rounded = self._round_entity_geometry(entity)
            if rounded:
                report.rounded_coordinate_count += rounded
                report.add_change(
                    entity_id,
                    "COORDINATES_ROUNDED",
                    "Geometry coordinates were rounded to normalization quantum.",
                    details={"rounded_value_count": rounded},
                )

            if self.options.normalize_polyline_vertices and self._normalize_polyline(entity):
                report.normalized_polyline_count += 1
                report.add_change(
                    entity_id,
                    "POLYLINE_VERTICES_NORMALIZED",
                    "Polyline duplicate vertices, start point, or direction were normalized.",
                )

            if self.options.normalize_text and self._normalize_text(entity):
                report.normalized_text_count += 1
                report.add_change(
                    entity_id,
                    "TEXT_NORMALIZED",
                    "Text whitespace or MTEXT formatting was normalized.",
                )

            if self.options.resolve_bylayer_byblock:
                before_style = copy.deepcopy(entity.get("style") or {})
                resolved = self._resolve_entity_style(
                    entity,
                    layer_by_id=layer_by_id,
                    entity_by_id=entity_by_id,
                    style_cache=style_cache,
                )
                if resolved and (entity.get("style") or {}) != before_style:
                    report.resolved_style_count += 1
                    report.add_change(
                        entity_id,
                        "STYLE_RESOLVED",
                        "BYLAYER/BYBLOCK style values were resolved to effective values.",
                        before=before_style,
                        after=copy.deepcopy(entity.get("style") or {}),
                    )

            if self.options.remove_near_zero_geometry and self._is_near_zero_geometry(entity):
                report.removed_entity_count += 1
                report.removed_near_zero_count += 1
                report.add_change(
                    entity_id,
                    "NEAR_ZERO_GEOMETRY_REMOVED",
                    "Entity geometry was below the configured near-zero threshold.",
                    before={
                        "type": entity.get("type"),
                        "bbox": entity.get("bbox"),
                    },
                )
                continue

            if self.options.update_hashes and self._recompute_entity(entity):
                report.recomputed_hash_count += 1

            kept_entities.append(entity)

        normalized["entities"] = kept_entities
        self._refresh_references_and_extents(normalized)
        report.output_entity_count = len(kept_entities)

        metadata = normalized.setdefault("metadata", {})
        metadata["normalization"] = {
            "normalizer": "DrawingNormalizer",
            "version": "1.0",
            "options": self.options.to_dict(),
            "report": report.to_dict(),
        }
        return normalized, report

    def _round_entity_geometry(self, entity: Dict[str, Any]) -> int:
        geometry = entity.get("geometry")
        if not isinstance(geometry, dict):
            return 0
        count = self._round_value_tree(geometry)
        bbox = entity.get("bbox")
        if isinstance(bbox, dict):
            count += self._round_bbox(bbox)
        return count

    def _round_value_tree(self, value: Any, key: Optional[str] = None) -> int:
        if isinstance(value, dict):
            if _is_point_dict(value):
                return self._round_point_dict(value)
            count = 0
            for child_key, child_value in list(value.items()):
                if child_key == "matrix" and isinstance(child_value, list):
                    count += self._round_matrix(child_value)
                elif isinstance(child_value, (dict, list)):
                    count += self._round_value_tree(child_value, child_key)
                elif isinstance(child_value, float):
                    quantum = self._quantum_for_key(child_key)
                    new_value = _quantize(child_value, quantum)
                    if new_value != child_value:
                        value[child_key] = new_value
                        count += 1
                elif isinstance(child_value, int) and child_key in _NUMERIC_GEOMETRY_KEYS:
                    new_value = _quantize(float(child_value), self._quantum_for_key(child_key))
                    if new_value != child_value:
                        value[child_key] = new_value
                        count += 1
            return count
        if isinstance(value, list):
            count = 0
            for item in value:
                count += self._round_value_tree(item, key)
            return count
        return 0

    def _round_point_dict(self, point: Dict[str, Any]) -> int:
        count = 0
        for axis in ("x", "y", "z"):
            if axis not in point:
                continue
            try:
                old_value = float(point[axis])
            except (TypeError, ValueError):
                continue
            new_value = _quantize(old_value, self.options.coordinate_quantum_mm)
            if new_value != point[axis]:
                point[axis] = new_value
                count += 1
        return count

    def _round_matrix(self, matrix: List[Any]) -> int:
        count = 0
        for idx, old in enumerate(list(matrix)):
            if not isinstance(old, (int, float)):
                continue
            quantum = self.options.coordinate_quantum_mm if idx in {3, 7, 11} else self.options.scale_quantum
            new_value = _quantize(float(old), quantum)
            if new_value != old:
                matrix[idx] = new_value
                count += 1
        return count

    def _round_bbox(self, bbox: Dict[str, Any]) -> int:
        count = 0
        for key in ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z"):
            if key not in bbox:
                continue
            try:
                old_value = float(bbox[key])
            except (TypeError, ValueError):
                continue
            new_value = _quantize(old_value, self.options.bbox_quantum_mm)
            if new_value != bbox[key]:
                bbox[key] = new_value
                count += 1
        return count

    def _quantum_for_key(self, key: Optional[str]) -> float:
        if key and ("angle" in key or key in {"rotation_deg", "start_angle_deg", "end_angle_deg"}):
            return self.options.angle_quantum_deg
        if key in _SCALE_KEYS:
            return self.options.scale_quantum
        return self.options.coordinate_quantum_mm

    def _normalize_polyline(self, entity: Dict[str, Any]) -> bool:
        if entity.get("type") != "polyline":
            return False
        geometry = entity.get("geometry") or {}
        vertices = geometry.get("vertices")
        if not isinstance(vertices, list):
            return False

        before = _vertex_sequence_key(vertices)
        cleaned = self._remove_duplicate_polyline_vertices(vertices, bool(geometry.get("closed")))
        changed = _vertex_sequence_key(cleaned) != before

        if geometry.get("closed") and self.options.normalize_polyline_direction and cleaned:
            canonical = self._canonical_closed_polyline_vertices(cleaned)
            if _vertex_sequence_key(canonical) != _vertex_sequence_key(cleaned):
                changed = True
            cleaned = canonical

        if changed:
            geometry["vertices"] = cleaned
            if geometry.get("closed") and cleaned:
                geometry["closed"] = True
            entity["bbox"] = _bbox_from_points([v["point"] for v in cleaned], "control_points")
        return changed

    def _remove_duplicate_polyline_vertices(
        self,
        vertices: Sequence[Dict[str, Any]],
        closed: bool,
    ) -> List[Dict[str, Any]]:
        cleaned: List[Dict[str, Any]] = []
        for vertex in vertices:
            if not isinstance(vertex, dict) or not isinstance(vertex.get("point"), dict):
                continue
            candidate = copy.deepcopy(vertex)
            if cleaned and _point_distance(cleaned[-1]["point"], candidate["point"]) <= self.options.vertex_merge_tolerance_mm:
                continue
            cleaned.append(candidate)
        if closed and len(cleaned) > 1:
            if _point_distance(cleaned[0]["point"], cleaned[-1]["point"]) <= self.options.vertex_merge_tolerance_mm:
                cleaned.pop()
        return cleaned

    def _canonical_closed_polyline_vertices(
        self,
        vertices: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if len(vertices) <= 1:
            return [copy.deepcopy(vertex) for vertex in vertices]

        forward = [copy.deepcopy(vertex) for vertex in vertices]
        reverse = _reverse_closed_vertices(forward)
        forward_keys = _vertex_sequence_key(forward)
        reverse_keys = _vertex_sequence_key(reverse)
        forward_offset = _minimal_rotation_offset(forward_keys)
        reverse_offset = _minimal_rotation_offset(reverse_keys)
        forward_key = _rotate_key_sequence(forward_keys, forward_offset)
        reverse_key = _rotate_key_sequence(reverse_keys, reverse_offset)
        if reverse_key < forward_key:
            return _rotate(reverse, reverse_offset)
        return _rotate(forward, forward_offset)

    def _flatten_curve_entity(self, entity: Dict[str, Any]) -> bool:
        entity_type = str(entity.get("type") or "").lower()
        geometry = entity.get("geometry") or {}
        geometry_type = str(geometry.get("type") or entity_type).lower()
        if geometry_type == "ellipse" or entity_type == "ellipse":
            polyline = self._flatten_ellipse_geometry(geometry)
        elif geometry_type == "spline" or entity_type == "spline":
            polyline = self._flatten_spline_geometry(geometry)
        else:
            return False
        if polyline is None:
            return False
        entity.setdefault("metadata", {})["pre_normalization_geometry"] = copy.deepcopy(geometry)
        entity["type"] = "polyline"
        entity["geometry"] = polyline
        entity["bbox"] = _bbox_from_points([v["point"] for v in polyline["vertices"]], "control_points")
        return True

    def _flatten_ellipse_geometry(self, geometry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        center = _point_from_any(geometry.get("center"))
        major = _point_from_any(
            geometry.get("major_axis")
            or geometry.get("major")
            or geometry.get("major_vector")
            or geometry.get("major_radius_vector")
        )
        if center is None or major is None:
            return None
        ratio = float(geometry.get("minor_to_major_ratio") or geometry.get("ratio") or 1.0)
        start = float(geometry.get("start_param") or geometry.get("start") or 0.0)
        end = float(geometry.get("end_param") or geometry.get("end") or math.tau)
        segment_count = self._curve_segment_count(max(_point_norm(major), 1.0) * abs(end - start))
        points = _ellipse_points(center, major, ratio, start, end, segments=segment_count)
        return {
            "type": "polyline",
            "vertices": [_polyline_vertex(point) for point in points],
            "closed": abs((end - start) - math.tau) <= 1e-6,
            "polyline_kind": "flattened_ellipse",
            "flattened_from": "ellipse",
            "flatten_tolerance_mm": self.options.flatten_tolerance_mm,
        }

    def _flatten_spline_geometry(self, geometry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        raw_points = (
            geometry.get("fit_points")
            or geometry.get("control_points")
            or geometry.get("points")
            or geometry.get("vertices")
        )
        points = _points_from_any(raw_points)
        if len(points) < 2:
            return None
        flattened: List[Point3] = []
        for start, end in zip(points, points[1:]):
            if not flattened:
                flattened.append(start)
            segment_len = _point_distance(start, end)
            steps = max(1, min(self.options.max_flatten_segments, math.ceil(segment_len / max(self.options.flatten_tolerance_mm, 1e-9))))
            for index in range(1, steps + 1):
                t = index / steps
                flattened.append(
                    _make_point(
                        start["x"] + (end["x"] - start["x"]) * t,
                        start["y"] + (end["y"] - start["y"]) * t,
                        start.get("z", 0.0) + (end.get("z", 0.0) - start.get("z", 0.0)) * t,
                    )
                )
        return {
            "type": "polyline",
            "vertices": [_polyline_vertex(point) for point in flattened],
            "closed": bool(geometry.get("closed")),
            "polyline_kind": "flattened_spline",
            "flattened_from": "spline",
            "flatten_tolerance_mm": self.options.flatten_tolerance_mm,
        }

    def _curve_segment_count(self, approx_length: float) -> int:
        tolerance = max(self.options.flatten_tolerance_mm, 1e-9)
        return max(8, min(self.options.max_flatten_segments, math.ceil(approx_length / tolerance)))

    def _normalize_text(self, entity: Dict[str, Any]) -> bool:
        entity_type = entity.get("type")
        geometry = entity.get("geometry") or {}
        changed = False

        if entity_type == "text":
            raw_text = str(geometry.get("text") or geometry.get("canonical_text") or "")
            canonical = _normalize_plain_text(raw_text)
            if geometry.get("canonical_text") != canonical:
                geometry["canonical_text"] = canonical
                changed = True
        elif entity_type == "mtext":
            raw_content = str(
                geometry.get("raw_content")
                or geometry.get("plain_text")
                or geometry.get("canonical_text")
                or ""
            )
            plain = _strip_mtext_formatting(raw_content) if self.options.strip_mtext_formatting else raw_content
            canonical = _normalize_plain_text(plain)
            if geometry.get("plain_text") != plain:
                geometry["plain_text"] = plain
                changed = True
            if geometry.get("canonical_text") != canonical:
                geometry["canonical_text"] = canonical
                changed = True
        elif entity_type == "dimension":
            text_override = geometry.get("text_override")
            if text_override is not None:
                canonical = _normalize_plain_text(str(text_override))
                if geometry.get("canonical_text") != canonical:
                    geometry["canonical_text"] = canonical
                    changed = True
        elif entity_type == "block_reference":
            for attribute in geometry.get("attributes") or []:
                raw_text = str(attribute.get("text") or attribute.get("canonical_text") or "")
                canonical = _normalize_plain_text(raw_text)
                if attribute.get("canonical_text") != canonical:
                    attribute["canonical_text"] = canonical
                    changed = True
        return changed

    def _resolve_entity_style(
        self,
        entity: Dict[str, Any],
        *,
        layer_by_id: Dict[str, Dict[str, Any]],
        entity_by_id: Dict[str, Dict[str, Any]],
        style_cache: Dict[str, Dict[str, Any]],
    ) -> bool:
        entity_id = str(entity.get("id") or "")
        if entity_id in style_cache:
            entity["style"] = copy.deepcopy(style_cache[entity_id])
            return False

        style = entity.setdefault("style", {})
        layer = layer_by_id.get(entity.get("layer_id")) or {}
        parent_effective: Optional[Dict[str, Any]] = None
        parent_id = (entity.get("metadata") or {}).get("expanded_from_insert_id")
        if parent_id and parent_id in entity_by_id and parent_id != entity_id:
            parent = entity_by_id[parent_id]
            self._resolve_entity_style(
                parent,
                layer_by_id=layer_by_id,
                entity_by_id=entity_by_id,
                style_cache=style_cache,
            )
            parent_effective = parent.get("style") or {}

        effective_color, color_source = _resolve_style_component(
            style.get("color"),
            layer.get("color"),
            parent_effective.get("effective_color") if parent_effective else None,
            kind="color",
        )
        effective_linetype, linetype_source = _resolve_style_component(
            style.get("linetype"),
            layer.get("linetype"),
            parent_effective.get("effective_linetype") if parent_effective else None,
            kind="linetype",
        )
        effective_lineweight, lineweight_source = _resolve_style_component(
            style.get("lineweight"),
            layer.get("lineweight"),
            parent_effective.get("effective_lineweight") if parent_effective else None,
            kind="lineweight",
        )
        before = copy.deepcopy(style)
        style["effective_color"] = effective_color
        style["effective_linetype"] = effective_linetype
        style["effective_lineweight"] = effective_lineweight
        style["resolution"] = {
            "color": color_source,
            "linetype": linetype_source,
            "lineweight": lineweight_source,
        }
        style_cache[entity_id] = copy.deepcopy(style)
        return style != before

    def _is_near_zero_geometry(self, entity: Dict[str, Any]) -> bool:
        entity_type = entity.get("type")
        geometry = entity.get("geometry") or {}
        threshold = max(0.0, self.options.near_zero_length_mm)
        if entity_type == "line":
            return _point_distance(geometry.get("start"), geometry.get("end")) <= threshold
        if entity_type == "circle":
            return abs(float(geometry.get("radius") or 0.0)) <= threshold
        if entity_type == "arc":
            radius = abs(float(geometry.get("radius") or 0.0))
            if radius <= threshold:
                return True
            sweep = _arc_sweep_degrees(
                float(geometry.get("start_angle_deg") or 0.0),
                float(geometry.get("end_angle_deg") or 0.0),
            )
            return math.radians(sweep) * radius <= threshold
        if entity_type == "polyline":
            vertices = geometry.get("vertices") or []
            points = [vertex.get("point") for vertex in vertices if isinstance(vertex, dict)]
            unique = _unique_points(points, self.options.vertex_merge_tolerance_mm)
            if len(unique) < 2:
                return True
            if geometry.get("closed"):
                return abs(_polygon_area(unique)) <= self.options.near_zero_area_mm2
            return _polyline_length(unique) <= threshold
        if entity_type == "hatch":
            points = [
                vertex.get("point")
                for boundary in geometry.get("boundaries") or []
                for vertex in boundary.get("vertices") or []
                if isinstance(vertex, dict)
            ]
            if not points:
                return False
            unique = _unique_points(points, self.options.vertex_merge_tolerance_mm)
            return len(unique) < 3 or abs(_polygon_area(unique)) <= self.options.near_zero_area_mm2
        return False

    def _recompute_entity(self, entity: Dict[str, Any]) -> bool:
        old_hashes = copy.deepcopy(entity.get("hashes") or {})
        entity["bbox"] = self._entity_bbox(entity)
        self._round_bbox(entity["bbox"])
        entity["hashes"] = self._entity_hashes(entity)
        return entity["hashes"] != old_hashes

    def _entity_bbox(self, entity: Dict[str, Any]) -> BBox:
        entity_type = entity.get("type")
        geometry = entity.get("geometry") or {}
        if entity_type == "line":
            return _bbox_from_points([geometry.get("start"), geometry.get("end")], "exact")
        if entity_type == "circle":
            return _circle_bbox(geometry.get("center") or _make_point(0, 0, 0), float(geometry.get("radius") or 0.0), "exact")
        if entity_type == "arc":
            return _arc_bbox(
                geometry.get("center") or _make_point(0, 0, 0),
                float(geometry.get("radius") or 0.0),
                float(geometry.get("start_angle_deg") or 0.0),
                float(geometry.get("end_angle_deg") or 0.0),
            )
        if entity_type == "polyline":
            return _bbox_from_points(
                [vertex.get("point") for vertex in geometry.get("vertices") or [] if isinstance(vertex, dict)],
                "control_points",
            )
        if entity_type in {"text", "mtext"}:
            text = geometry.get("canonical_text") or geometry.get("plain_text") or geometry.get("text") or ""
            return _text_bbox(
                geometry.get("insert") or _make_point(0, 0, 0),
                float(geometry.get("height") or 0.0),
                str(text),
                "estimated",
            )
        if entity_type == "block_reference":
            return _bbox_from_points([geometry.get("insert") or _make_point(0, 0, 0)], "estimated")
        if entity_type == "hatch":
            points = [
                vertex.get("point")
                for boundary in geometry.get("boundaries") or []
                for vertex in boundary.get("vertices") or []
                if isinstance(vertex, dict)
            ]
            return _bbox_from_points(points, "control_points" if points else "missing")
        if entity_type == "dimension":
            points = list(geometry.get("defpoints") or [])
            if geometry.get("text_midpoint"):
                points.append(geometry["text_midpoint"])
            return _bbox_from_points(points, "estimated")
        return copy.deepcopy(entity.get("bbox") or _bbox_from_points([], "missing"))

    def _entity_hashes(self, entity: Dict[str, Any]) -> Dict[str, Optional[str]]:
        geometry = entity.get("geometry") or {}
        entity_type = str(entity.get("type") or geometry.get("type") or "unknown")
        style = entity.get("style") or {}
        style_payload = _style_hash_payload(style)
        source = entity.get("source") or {}
        semantic_payload = _semantic_payload(entity_type, geometry)
        return {
            "geometry_hash": _hash_payload("geom", _geometry_hash_payload(entity_type, geometry)),
            "semantic_hash": _hash_payload("sem", semantic_payload) if semantic_payload else None,
            "style_hash": _hash_payload("style", style_payload) if _has_non_null_value(style_payload) else None,
            "source_fingerprint": _hash_payload("src", source) if source else None,
        }

    def _refresh_references_and_extents(self, drawing: Dict[str, Any]) -> None:
        entities = drawing.get("entities") or []
        entity_by_id = {entity.get("id"): entity for entity in entities}
        existing_ids = set(entity_by_id)

        for entity in entities:
            if entity.get("type") != "block_reference":
                continue
            geometry = entity.get("geometry") or {}
            expanded_ids = [eid for eid in geometry.get("expanded_entity_ids") or [] if eid in existing_ids]
            geometry["expanded_entity_ids"] = expanded_ids
            if expanded_ids:
                entity["bbox"] = _union_bbox([entity_by_id[eid].get("bbox") for eid in expanded_ids])
                if self.options.update_hashes:
                    entity["hashes"] = self._entity_hashes(entity)

        for block in drawing.get("blocks") or []:
            block["entity_ids"] = [eid for eid in block.get("entity_ids") or [] if eid in existing_ids]
            block["bbox"] = _union_bbox([entity_by_id[eid].get("bbox") for eid in block["entity_ids"]])

        drawing["extents"] = _union_bbox([entity.get("bbox") for entity in entities])
        if isinstance(drawing.get("extents"), dict):
            self._round_bbox(drawing["extents"])


_NUMERIC_GEOMETRY_KEYS = {
    "radius",
    "height",
    "box_width",
    "measurement",
    "bulge",
    "start_width",
    "end_width",
    "pattern_scale",
    "pattern_angle_deg",
    "rotation_deg",
    "start_angle_deg",
    "end_angle_deg",
    "flatten_tolerance_mm",
}

_SCALE_KEYS = {
    "scale",
    "scale_x",
    "scale_y",
    "scale_z",
    "ratio",
    "minor_to_major_ratio",
    "start_param",
    "end_param",
}


def _is_point_dict(value: Dict[str, Any]) -> bool:
    return "x" in value and "y" in value


def _quantize(value: float, quantum: float) -> float:
    if not math.isfinite(value):
        return 0.0
    if quantum <= 0:
        return 0.0 if value == 0 else value
    rounded = round(value / quantum) * quantum
    rounded = round(rounded, max(0, min(12, _decimal_places(quantum) + 3)))
    return 0.0 if abs(rounded) < quantum / 2 else rounded


def _decimal_places(value: float) -> int:
    text = f"{value:.12f}".rstrip("0")
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])


def _polyline_vertex(point: Point3) -> Dict[str, Any]:
    return {"point": copy.deepcopy(point), "bulge": 0.0, "start_width": None, "end_width": None}


def _vertex_sequence_key(vertices: Sequence[Dict[str, Any]]) -> Tuple[Tuple[Any, ...], ...]:
    return tuple(_vertex_key(vertex) for vertex in vertices)


def _vertex_key(vertex: Dict[str, Any]) -> Tuple[Any, ...]:
    point = vertex.get("point") or {}
    return (
        _stable_number(point.get("x")),
        _stable_number(point.get("y")),
        _stable_number(point.get("z", 0.0)),
        _stable_number(vertex.get("bulge", 0.0)),
        _stable_number(vertex.get("start_width")),
        _stable_number(vertex.get("end_width")),
    )


def _stable_number(value: Any) -> Any:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return 0.0 if abs(number) < 1e-12 else round(number, 12)


def _rotate(vertices: Sequence[Dict[str, Any]], offset: int) -> List[Dict[str, Any]]:
    return [copy.deepcopy(vertex) for vertex in list(vertices)[offset:] + list(vertices)[:offset]]


def _rotate_key_sequence(keys: Tuple[Tuple[Any, ...], ...], offset: int) -> Tuple[Tuple[Any, ...], ...]:
    if not keys or offset <= 0:
        return keys
    return keys[offset:] + keys[:offset]


def _minimal_rotation_offset(keys: Tuple[Tuple[Any, ...], ...]) -> int:
    count = len(keys)
    if count <= 1:
        return 0
    left = 0
    right = 1
    matched = 0
    while left < count and right < count and matched < count:
        a_key = keys[(left + matched) % count]
        b_key = keys[(right + matched) % count]
        if a_key == b_key:
            matched += 1
            continue
        if a_key > b_key:
            left = left + matched + 1
            if left <= right:
                left = right + 1
        else:
            right = right + matched + 1
            if right <= left:
                right = left + 1
        matched = 0
    return min(left, right) % count


def _reverse_closed_vertices(vertices: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    count = len(vertices)
    reversed_vertices: List[Dict[str, Any]] = []
    for old_index in reversed(range(count)):
        old_vertex = vertices[old_index]
        previous = vertices[(old_index - 1) % count]
        new_vertex = copy.deepcopy(old_vertex)
        new_vertex["bulge"] = -float(previous.get("bulge") or 0.0)
        new_vertex["start_width"] = previous.get("end_width")
        new_vertex["end_width"] = previous.get("start_width")
        reversed_vertices.append(new_vertex)
    return reversed_vertices


def _point_distance(a: Any, b: Any) -> float:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return math.inf
    return math.sqrt(
        (float(a.get("x", 0.0)) - float(b.get("x", 0.0))) ** 2
        + (float(a.get("y", 0.0)) - float(b.get("y", 0.0))) ** 2
        + (float(a.get("z", 0.0)) - float(b.get("z", 0.0))) ** 2
    )


def _point_norm(point: Point3) -> float:
    return math.sqrt(point["x"] ** 2 + point["y"] ** 2 + point.get("z", 0.0) ** 2)


def _unique_points(points: Iterable[Any], tolerance: float) -> List[Point3]:
    unique: List[Point3] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        if all(_point_distance(point, other) > tolerance for other in unique):
            unique.append(point)
    return unique


def _polyline_length(points: Sequence[Point3]) -> float:
    return sum(_point_distance(start, end) for start, end in zip(points, points[1:]))


def _polygon_area(points: Sequence[Point3]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for start, end in zip(points, list(points[1:]) + [points[0]]):
        area += float(start.get("x", 0.0)) * float(end.get("y", 0.0))
        area -= float(end.get("x", 0.0)) * float(start.get("y", 0.0))
    return area / 2.0


def _arc_sweep_degrees(start: float, end: float) -> float:
    start = _normalize_angle(start)
    end = _normalize_angle(end)
    sweep = end - start
    if sweep < 0:
        sweep += 360.0
    return sweep


def _point_from_any(value: Any) -> Optional[Point3]:
    if isinstance(value, dict):
        if "point" in value and isinstance(value["point"], dict):
            return _point_from_any(value["point"])
        if "x" in value and "y" in value:
            return _make_point(
                float(value.get("x") or 0.0),
                float(value.get("y") or 0.0),
                float(value.get("z") or 0.0),
            )
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _make_point(
            float(value[0]),
            float(value[1]),
            float(value[2]) if len(value) > 2 else 0.0,
        )
    return None


def _points_from_any(value: Any) -> List[Point3]:
    if not isinstance(value, list):
        return []
    points = []
    for item in value:
        point = _point_from_any(item)
        if point is not None:
            points.append(point)
    return points


def _normalize_plain_text(value: str) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(text.split())


def _strip_mtext_formatting(value: str) -> str:
    text = value or ""
    text = re.sub(r"\\[Pp]", "\n", text)
    text = re.sub(r"\\~", " ", text)
    text = re.sub(r"\\S([^;]*);", lambda match: _normalize_stacked_fraction(match.group(1)), text)
    text = re.sub(r"\\[A-Za-z][^;\\{}]*;", "", text)
    text = re.sub(r"\\[LlOoKk]", "", text)
    text = text.replace("{", "").replace("}", "")
    text = text.replace("\\\\", "\\")
    return _normalize_plain_text(text)


def _normalize_stacked_fraction(value: str) -> str:
    return value.replace("#", "/").replace("^", "/").strip()


def _resolve_style_component(
    raw: Any,
    layer_value: Any,
    parent_value: Any,
    *,
    kind: str,
) -> Tuple[Any, str]:
    if _is_byblock(raw, kind):
        if parent_value is not None:
            return parent_value, "byblock"
        return _style_default(layer_value, kind), "byblock_layer_fallback"
    if _is_bylayer(raw, kind):
        return _style_default(layer_value, kind), "bylayer"
    return raw, "explicit"


def _is_bylayer(value: Any, kind: str) -> bool:
    if value is None:
        return True
    if kind == "color":
        try:
            return int(float(value)) == 256
        except (TypeError, ValueError):
            return str(value).strip().upper() == "BYLAYER"
    if kind == "lineweight":
        try:
            return int(float(value)) in {-1, -3}
        except (TypeError, ValueError):
            return str(value).strip().upper() in {"BYLAYER", "DEFAULT"}
    return str(value).strip().upper() in {"", "BYLAYER"}


def _is_byblock(value: Any, kind: str) -> bool:
    if kind == "color":
        try:
            return int(float(value)) == 0
        except (TypeError, ValueError):
            return str(value).strip().upper() == "BYBLOCK"
    if kind == "lineweight":
        try:
            return int(float(value)) == -2
        except (TypeError, ValueError):
            return str(value).strip().upper() == "BYBLOCK"
    return str(value).strip().upper() == "BYBLOCK"


def _style_default(value: Any, kind: str) -> Any:
    if value is not None:
        return value
    if kind == "color":
        return 7
    if kind == "lineweight":
        return 0
    return "Continuous"


def _semantic_payload(entity_type: str, geometry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if entity_type in {"text", "mtext"}:
        return {"text": geometry.get("canonical_text")}
    if entity_type == "block_reference":
        return {"attributes": geometry.get("attributes") or []}
    if entity_type == "dimension":
        return {
            "dimension_type": geometry.get("dimension_type"),
            "measurement": geometry.get("measurement"),
            "canonical_text": geometry.get("canonical_text"),
        }
    if entity_type == "hatch":
        return {
            "pattern_name": geometry.get("pattern_name"),
            "pattern_scale": geometry.get("pattern_scale"),
            "pattern_angle_deg": geometry.get("pattern_angle_deg"),
        }
    return None


def _style_hash_payload(style: Dict[str, Any]) -> Dict[str, Any]:
    if any(key in style for key in ("effective_color", "effective_linetype", "effective_lineweight")):
        return {
            "color": style.get("effective_color"),
            "linetype": style.get("effective_linetype"),
            "lineweight": style.get("effective_lineweight"),
            "text_style": style.get("text_style"),
            "dimension_style": style.get("dimension_style"),
        }
    return {
        "color": style.get("color"),
        "linetype": style.get("linetype"),
        "lineweight": style.get("lineweight"),
        "text_style": style.get("text_style"),
        "dimension_style": style.get("dimension_style"),
    }


def _has_non_null_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_non_null_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_non_null_value(item) for item in value)
    return value is not None
