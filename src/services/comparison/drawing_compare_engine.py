"""Compare two normalized CanonicalDrawing documents.

The engine assumes format-specific import and DrawingNormalizer have already
produced stable CanonicalDrawing dictionaries.  Matching is spatially scoped and
then scored with deterministic tie-breaks so tolerance changes produce
repeatable output.
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from .base import ChangeRecord, ChangeType
from .dxf_importer import _hash_payload


BBox2D = Dict[str, float]
Point2D = Dict[str, float]


@dataclass(frozen=True)
class CompareTolerance:
    """Tolerance values used by matching and entity-specific diffs."""

    position_tolerance_mm: float = 1.0
    bbox_tolerance_mm: float = 1.0
    numeric_tolerance: float = 1e-6
    angle_tolerance_deg: float = 0.001
    text_case_sensitive: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_tolerance_mm": self.position_tolerance_mm,
            "bbox_tolerance_mm": self.bbox_tolerance_mm,
            "numeric_tolerance": self.numeric_tolerance,
            "angle_tolerance_deg": self.angle_tolerance_deg,
            "text_case_sensitive": self.text_case_sensitive,
        }


@dataclass(frozen=True)
class DrawingCompareOptions:
    """Options for matching and result materialization."""

    tolerance: CompareTolerance = field(default_factory=CompareTolerance)
    search_radius_mm: float = 5.0
    match_threshold: float = 0.62
    spatial_cell_size_mm: Optional[float] = None
    max_spatial_cells_per_entity: int = 4096
    include_unchanged: bool = True
    include_entity_snapshots: bool = True
    include_match_candidates: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tolerance": self.tolerance.to_dict(),
            "search_radius_mm": self.search_radius_mm,
            "match_threshold": self.match_threshold,
            "spatial_cell_size_mm": self.spatial_cell_size_mm,
            "max_spatial_cells_per_entity": self.max_spatial_cells_per_entity,
            "include_unchanged": self.include_unchanged,
            "include_entity_snapshots": self.include_entity_snapshots,
            "include_match_candidates": self.include_match_candidates,
        }


@dataclass(frozen=True)
class FieldDiff:
    """One field-level delta inside a geometry diff."""

    path: str
    old: Any
    new: Any
    delta: Optional[float] = None
    tolerance: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"path": self.path, "old": self.old, "new": self.new}
        if self.delta is not None:
            out["delta"] = self.delta
        if self.tolerance is not None:
            out["tolerance"] = self.tolerance
        return out


@dataclass
class GeometryDiff:
    """Entity-specific geometry and semantic diff result."""

    entity_type: str
    changed: bool = False
    categories: List[str] = field(default_factory=list)
    fields: List[FieldDiff] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def compare(
        cls,
        old_entity: Dict[str, Any],
        new_entity: Dict[str, Any],
        tolerance: CompareTolerance,
    ) -> "GeometryDiff":
        entity_type = str(new_entity.get("type") or old_entity.get("type") or "unknown")
        diff = cls(entity_type=entity_type)
        old_geom = old_entity.get("geometry") or {}
        new_geom = new_entity.get("geometry") or {}

        if entity_type == "line":
            diff._compare_line(old_geom, new_geom, tolerance)
        elif entity_type == "circle":
            diff._compare_circle(old_geom, new_geom, tolerance)
        elif entity_type == "arc":
            diff._compare_arc(old_geom, new_geom, tolerance)
        elif entity_type == "polyline":
            diff._compare_polyline(old_geom, new_geom, tolerance)
        elif entity_type in {"text", "mtext"}:
            diff._compare_text(old_geom, new_geom, tolerance)
        elif entity_type == "block_reference":
            diff._compare_block_reference(old_geom, new_geom, tolerance)
        else:
            if _hash(old_entity, "geometry_hash") != _hash(new_entity, "geometry_hash"):
                diff.add(
                    "geometry.hash",
                    _hash(old_entity, "geometry_hash"),
                    _hash(new_entity, "geometry_hash"),
                    category="geometry",
                )

        diff.changed = bool(diff.fields)
        diff.categories = sorted(set(diff.categories))
        return diff

    def add(
        self,
        path: str,
        old: Any,
        new: Any,
        *,
        delta: Optional[float] = None,
        tolerance: Optional[float] = None,
        category: str = "geometry",
    ) -> None:
        self.fields.append(FieldDiff(path=path, old=old, new=new, delta=delta, tolerance=tolerance))
        self.categories.append(category)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "changed": self.changed,
            "categories": list(self.categories),
            "fields": [field_diff.to_dict() for field_diff in self.fields],
            "metrics": self.metrics,
        }

    def _compare_line(
        self,
        old_geom: Dict[str, Any],
        new_geom: Dict[str, Any],
        tolerance: CompareTolerance,
    ) -> None:
        old_start = old_geom.get("start")
        old_end = old_geom.get("end")
        new_start = new_geom.get("start")
        new_end = new_geom.get("end")
        direct = _point_distance_2d(old_start, new_start) + _point_distance_2d(old_end, new_end)
        reversed_distance = _point_distance_2d(old_start, new_end) + _point_distance_2d(old_end, new_start)
        if reversed_distance < direct:
            new_start, new_end = new_end, new_start
        self._compare_point("geometry.start", old_start, new_start, tolerance.position_tolerance_mm)
        self._compare_point("geometry.end", old_end, new_end, tolerance.position_tolerance_mm)
        self.metrics["line_endpoint_distance_mm"] = round(min(direct, reversed_distance), 6)

    def _compare_circle(
        self,
        old_geom: Dict[str, Any],
        new_geom: Dict[str, Any],
        tolerance: CompareTolerance,
    ) -> None:
        self._compare_point("geometry.center", old_geom.get("center"), new_geom.get("center"), tolerance.position_tolerance_mm)
        self._compare_number("geometry.radius", old_geom.get("radius"), new_geom.get("radius"), tolerance.numeric_tolerance)

    def _compare_arc(
        self,
        old_geom: Dict[str, Any],
        new_geom: Dict[str, Any],
        tolerance: CompareTolerance,
    ) -> None:
        self._compare_circle(old_geom, new_geom, tolerance)
        self._compare_angle("geometry.start_angle_deg", old_geom.get("start_angle_deg"), new_geom.get("start_angle_deg"), tolerance.angle_tolerance_deg)
        self._compare_angle("geometry.end_angle_deg", old_geom.get("end_angle_deg"), new_geom.get("end_angle_deg"), tolerance.angle_tolerance_deg)

    def _compare_polyline(
        self,
        old_geom: Dict[str, Any],
        new_geom: Dict[str, Any],
        tolerance: CompareTolerance,
    ) -> None:
        old_vertices = old_geom.get("vertices") or []
        new_vertices = new_geom.get("vertices") or []
        if bool(old_geom.get("closed")) != bool(new_geom.get("closed")):
            self.add("geometry.closed", bool(old_geom.get("closed")), bool(new_geom.get("closed")), category="geometry")
        if len(old_vertices) != len(new_vertices):
            self.add("geometry.vertex_count", len(old_vertices), len(new_vertices), delta=float(len(new_vertices) - len(old_vertices)), category="geometry")

        max_distance = 0.0
        for index, (old_vertex, new_vertex) in enumerate(zip(old_vertices, new_vertices)):
            distance = _point_distance_2d(old_vertex.get("point"), new_vertex.get("point"))
            max_distance = max(max_distance, distance)
            if distance > tolerance.position_tolerance_mm:
                self.add(
                    f"geometry.vertices[{index}].point",
                    _point2(old_vertex.get("point")),
                    _point2(new_vertex.get("point")),
                    delta=round(distance, 6),
                    tolerance=tolerance.position_tolerance_mm,
                    category="geometry",
                )
            self._compare_number(
                f"geometry.vertices[{index}].bulge",
                old_vertex.get("bulge"),
                new_vertex.get("bulge"),
                tolerance.numeric_tolerance,
            )
        self.metrics["polyline_max_vertex_distance_mm"] = round(max_distance, 6)

    def _compare_text(
        self,
        old_geom: Dict[str, Any],
        new_geom: Dict[str, Any],
        tolerance: CompareTolerance,
    ) -> None:
        self._compare_point("geometry.insert", old_geom.get("insert"), new_geom.get("insert"), tolerance.position_tolerance_mm)
        self._compare_number("geometry.height", old_geom.get("height"), new_geom.get("height"), tolerance.numeric_tolerance)
        self._compare_angle("geometry.rotation_deg", old_geom.get("rotation_deg"), new_geom.get("rotation_deg"), tolerance.angle_tolerance_deg)
        old_text = old_geom.get("canonical_text") or ""
        new_text = new_geom.get("canonical_text") or ""
        if not tolerance.text_case_sensitive:
            old_cmp = str(old_text).casefold()
            new_cmp = str(new_text).casefold()
        else:
            old_cmp = str(old_text)
            new_cmp = str(new_text)
        if old_cmp != new_cmp:
            self.add("geometry.canonical_text", old_text, new_text, category="text")

    def _compare_block_reference(
        self,
        old_geom: Dict[str, Any],
        new_geom: Dict[str, Any],
        tolerance: CompareTolerance,
    ) -> None:
        if old_geom.get("block_name") != new_geom.get("block_name"):
            self.add("geometry.block_name", old_geom.get("block_name"), new_geom.get("block_name"), category="block")
        self._compare_point("geometry.insert", old_geom.get("insert"), new_geom.get("insert"), tolerance.position_tolerance_mm)
        self._compare_point("geometry.scale", old_geom.get("scale"), new_geom.get("scale"), tolerance.numeric_tolerance)
        self._compare_angle("geometry.rotation_deg", old_geom.get("rotation_deg"), new_geom.get("rotation_deg"), tolerance.angle_tolerance_deg)
        old_attrs = _attributes_key(old_geom.get("attributes") or [])
        new_attrs = _attributes_key(new_geom.get("attributes") or [])
        if old_attrs != new_attrs:
            self.add("geometry.attributes", old_attrs, new_attrs, category="text")

    def _compare_point(self, path: str, old: Any, new: Any, tolerance: float) -> None:
        distance = _point_distance_2d(old, new)
        if distance > tolerance:
            self.add(
                path,
                _point2(old),
                _point2(new),
                delta=round(distance, 6),
                tolerance=tolerance,
                category="geometry",
            )

    def _compare_number(self, path: str, old: Any, new: Any, tolerance: float) -> None:
        old_num = _float_or_none(old)
        new_num = _float_or_none(new)
        if old_num is None or new_num is None:
            if old != new:
                self.add(path, old, new, category="geometry")
            return
        delta = abs(new_num - old_num)
        if delta > tolerance:
            self.add(path, old_num, new_num, delta=round(delta, 6), tolerance=tolerance, category="geometry")

    def _compare_angle(self, path: str, old: Any, new: Any, tolerance: float) -> None:
        old_num = _float_or_none(old)
        new_num = _float_or_none(new)
        if old_num is None or new_num is None:
            if old != new:
                self.add(path, old, new, category="geometry")
            return
        delta = _angle_delta(old_num, new_num)
        if delta > tolerance:
            self.add(path, old_num, new_num, delta=round(delta, 6), tolerance=tolerance, category="geometry")


@dataclass(frozen=True)
class MatchCandidate:
    old_entity_id: str
    new_entity_id: str
    score: float
    components: Dict[str, float]
    centroid_distance_mm: float
    bbox_iou: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "old_entity_id": self.old_entity_id,
            "new_entity_id": self.new_entity_id,
            "score": round(self.score, 6),
            "components": {key: round(value, 6) for key, value in sorted(self.components.items())},
            "centroid_distance_mm": round(self.centroid_distance_mm, 6),
            "bbox_iou": round(self.bbox_iou, 6),
        }


@dataclass(frozen=True)
class EntityMatch:
    old_entity: Dict[str, Any]
    new_entity: Dict[str, Any]
    candidate: MatchCandidate

    def to_dict(self) -> Dict[str, Any]:
        return self.candidate.to_dict()


@dataclass
class EntityMatchResult:
    matches: List[EntityMatch]
    unmatched_old: List[Dict[str, Any]]
    unmatched_new: List[Dict[str, Any]]
    candidates: List[MatchCandidate]


class EntityMatcher:
    """Spatial-index based matcher for normalized canonical entities."""

    def __init__(self, options: Optional[DrawingCompareOptions] = None):
        self.options = options or DrawingCompareOptions()

    def match(
        self,
        old_entities: Sequence[Dict[str, Any]],
        new_entities: Sequence[Dict[str, Any]],
    ) -> EntityMatchResult:
        old_sorted = sorted(old_entities, key=_entity_sort_key)
        new_sorted = sorted(new_entities, key=_entity_sort_key)
        candidate_radius = self._candidate_radius()
        index = _CanonicalSpatialIndex(
            cell_size=self.options.spatial_cell_size_mm
            or max(candidate_radius, 1.0),
            max_cells_per_entity=self.options.max_spatial_cells_per_entity,
        )
        for entity in new_sorted:
            index.insert(entity)

        candidate_by_pair: Dict[Tuple[str, str], MatchCandidate] = {}
        new_by_exact_hash: Dict[Tuple[str, str, str], Deque[Dict[str, Any]]] = defaultdict(deque)
        for new_entity in new_sorted:
            exact_key = _exact_hash_key(new_entity)
            if exact_key:
                new_by_exact_hash[exact_key].append(new_entity)

        exact_used_old: set[str] = set()
        exact_used_new: set[str] = set()
        for old_entity in old_sorted:
            exact_key = _exact_hash_key(old_entity)
            old_id = str(old_entity.get("id") or "")
            if not exact_key:
                continue
            bucket = new_by_exact_hash.get(exact_key)
            if not bucket:
                continue
            new_entity = bucket.popleft()
            new_id = str(new_entity.get("id") or "")
            candidate = self.score(old_entity, new_entity)
            candidate_by_pair[(candidate.old_entity_id, candidate.new_entity_id)] = candidate
            exact_used_old.add(old_id)
            exact_used_new.add(new_id)

        for old_entity in old_sorted:
            old_id = str(old_entity.get("id") or "")
            if old_id in exact_used_old:
                continue
            for new_entity in index.query(old_entity, radius=candidate_radius):
                new_id = str(new_entity.get("id") or "")
                if new_id in exact_used_new:
                    continue
                candidate = self.score(old_entity, new_entity)
                if candidate.score >= self.options.match_threshold:
                    candidate_by_pair[(candidate.old_entity_id, candidate.new_entity_id)] = candidate

        candidates = sorted(
            candidate_by_pair.values(),
            key=lambda item: (-item.score, item.centroid_distance_mm, item.old_entity_id, item.new_entity_id),
        )
        old_by_id = {str(entity.get("id")): entity for entity in old_sorted}
        new_by_id = {str(entity.get("id")): entity for entity in new_sorted}
        used_old: set[str] = set()
        used_new: set[str] = set()
        matches: List[EntityMatch] = []
        for candidate in candidates:
            if candidate.old_entity_id in used_old or candidate.new_entity_id in used_new:
                continue
            old_entity = old_by_id[candidate.old_entity_id]
            new_entity = new_by_id[candidate.new_entity_id]
            matches.append(EntityMatch(old_entity=old_entity, new_entity=new_entity, candidate=candidate))
            used_old.add(candidate.old_entity_id)
            used_new.add(candidate.new_entity_id)

        unmatched_old = [entity for entity in old_sorted if str(entity.get("id")) not in used_old]
        unmatched_new = [entity for entity in new_sorted if str(entity.get("id")) not in used_new]
        matches.sort(key=lambda match: _entity_sort_key(match.old_entity))
        return EntityMatchResult(
            matches=matches,
            unmatched_old=unmatched_old,
            unmatched_new=unmatched_new,
            candidates=candidates,
        )

    def score(self, old_entity: Dict[str, Any], new_entity: Dict[str, Any]) -> MatchCandidate:
        old_type = str(old_entity.get("type") or "")
        new_type = str(new_entity.get("type") or "")
        old_bbox = _bbox2(old_entity.get("bbox"))
        new_bbox = _bbox2(new_entity.get("bbox"))
        old_centroid = _centroid(old_bbox)
        new_centroid = _centroid(new_bbox)
        centroid_distance = _point_distance_2d(old_centroid, new_centroid)
        bbox_iou = _bbox_iou(old_bbox, new_bbox)
        bbox_distance = _bbox_distance(old_bbox, new_bbox)
        radius = self._candidate_radius()
        type_score = 1.0 if old_type == new_type and old_type else 0.0
        layer_score = 1.0 if _layer_key(old_entity) == _layer_key(new_entity) else 0.35
        centroid_score = _distance_score(centroid_distance, radius)
        bbox_score = max(bbox_iou, _distance_score(bbox_distance, radius))
        old_geometry_hash = _hash(old_entity, "geometry_hash")
        new_geometry_hash = _hash(new_entity, "geometry_hash")
        geometry_hash_score = 1.0 if old_geometry_hash and old_geometry_hash == new_geometry_hash else 0.0
        components = {
            "type": type_score,
            "layer": layer_score,
            "bbox": bbox_score,
            "centroid": centroid_score,
            "geometry_hash": geometry_hash_score,
        }
        score = (
            0.25 * type_score
            + 0.15 * layer_score
            + 0.20 * bbox_score
            + 0.20 * centroid_score
            + 0.20 * geometry_hash_score
        )
        if type_score == 0.0:
            score *= 0.25
        return MatchCandidate(
            old_entity_id=str(old_entity.get("id") or ""),
            new_entity_id=str(new_entity.get("id") or ""),
            score=round(score, 9),
            components=components,
            centroid_distance_mm=centroid_distance,
            bbox_iou=bbox_iou,
        )

    def _candidate_radius(self) -> float:
        return max(
            float(self.options.search_radius_mm or 0.0),
            float(self.options.tolerance.position_tolerance_mm or 0.0),
            float(self.options.tolerance.bbox_tolerance_mm or 0.0),
            1e-9,
        )


@dataclass
class DrawingDiffChange:
    change_id: str
    change_type: str
    entity_type: str
    layer_id: Optional[str]
    layer_name: Optional[str]
    old_entity_id: Optional[str]
    new_entity_id: Optional[str]
    location: Point2D
    bbox: BBox2D
    old_bbox: Optional[BBox2D] = None
    new_bbox: Optional[BBox2D] = None
    match: Optional[MatchCandidate] = None
    geometry_diff: Optional[GeometryDiff] = None
    attribute_diffs: List[FieldDiff] = field(default_factory=list)
    old_entity: Optional[Dict[str, Any]] = None
    new_entity: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        old_snapshot = _entity_snapshot(self.old_entity) if self.old_entity else None
        new_snapshot = _entity_snapshot(self.new_entity) if self.new_entity else None
        out = {
            "change_id": self.change_id,
            "change_type": self.change_type,
            "entity_type": self.entity_type,
            "layer_id": self.layer_id,
            "layer_name": self.layer_name,
            "old_entity_id": self.old_entity_id,
            "new_entity_id": self.new_entity_id,
            "location": self.location,
            "bbox": self.bbox,
            "old_bbox": self.old_bbox,
            "new_bbox": self.new_bbox,
            "match": self.match.to_dict() if self.match else None,
            "geometry_diff": self.geometry_diff.to_dict() if self.geometry_diff else None,
            "attribute_diffs": [diff.to_dict() for diff in self.attribute_diffs],
            "old_entity": old_snapshot,
            "new_entity": new_snapshot,
            "visualization": {
                "side": _visual_side(self.change_type),
                "color": _visual_color(self.change_type),
                "bbox": self.bbox,
                "old_bbox": self.old_bbox,
                "new_bbox": self.new_bbox,
            },
        }
        return out

    def to_change_record(self) -> ChangeRecord:
        change_type = {
            "added": ChangeType.ADDED,
            "removed": ChangeType.DELETED,
            "modified": ChangeType.MODIFIED,
            "unchanged": ChangeType.UNCHANGED,
        }[self.change_type]
        metadata = {
            "entity_type": self.entity_type,
            "layer": self.layer_name or self.layer_id or "",
            "layer_id": self.layer_id,
            "bbox": self.bbox,
            "old_bbox": self.old_bbox,
            "new_bbox": self.new_bbox,
            "x": self.location["x"],
            "y": self.location["y"],
            "change_category": ",".join((self.geometry_diff.categories if self.geometry_diff else []) or []),
            "detection_source": "canonical-drawing-compare",
        }
        return ChangeRecord(
            key=self.change_id,
            change_type=change_type,
            field_name=self.entity_type,
            old_value=self.old_entity,
            new_value=self.new_entity,
            location=f"{self.location['x']},{self.location['y']}",
            metadata=metadata,
        )


@dataclass
class DrawingDiffResult:
    schema_version: str
    source_a: Dict[str, Any]
    source_b: Dict[str, Any]
    options: DrawingCompareOptions
    changes: List[DrawingDiffChange]
    match_candidates: List[MatchCandidate] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def summary(self) -> Dict[str, int]:
        counts = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
        for change in self.changes:
            counts[change.change_type] += 1
        counts["total_changes"] = counts["added"] + counts["removed"] + counts["modified"]
        counts["total_records"] = len(self.changes)
        return counts

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "source_a": self.source_a,
            "source_b": self.source_b,
            "options": self.options.to_dict(),
            "summary": self.summary,
            "changes": [change.to_dict() for change in self.changes],
            "warnings": list(self.warnings),
            "metadata": {
                "deterministic": True,
                "match_candidate_count": len(self.match_candidates),
            },
        }
        if self.options.include_match_candidates:
            payload["match_candidates"] = [candidate.to_dict() for candidate in self.match_candidates]
        return payload

    def to_change_records(self, *, include_unchanged: bool = False) -> List[ChangeRecord]:
        return [
            change.to_change_record()
            for change in self.changes
            if include_unchanged or change.change_type != "unchanged"
        ]


class DrawingCompareEngine:
    """Compare two normalized CanonicalDrawing dictionaries."""

    SCHEMA_VERSION = "drawing-diff/v1"

    def __init__(self, options: Optional[DrawingCompareOptions] = None):
        self.options = options or DrawingCompareOptions()
        self.matcher = EntityMatcher(self.options)

    def compare(self, old_drawing: Dict[str, Any], new_drawing: Dict[str, Any]) -> DrawingDiffResult:
        old_entities = list(old_drawing.get("entities") or [])
        new_entities = list(new_drawing.get("entities") or [])
        old_layers = _layers_by_id(old_drawing)
        new_layers = _layers_by_id(new_drawing)
        match_result = self.matcher.match(old_entities, new_entities)

        changes: List[DrawingDiffChange] = []
        for match in match_result.matches:
            geometry_diff = GeometryDiff.compare(match.old_entity, match.new_entity, self.options.tolerance)
            attribute_diffs = _attribute_diffs(match.old_entity, match.new_entity)
            changed = geometry_diff.changed or bool(attribute_diffs)
            if changed:
                change_type = "modified"
            else:
                change_type = "unchanged"
            if change_type == "unchanged" and not self.options.include_unchanged:
                continue
            changes.append(
                self._matched_change(
                    change_type,
                    match,
                    geometry_diff,
                    attribute_diffs,
                    old_layers,
                    new_layers,
                )
            )

        for entity in match_result.unmatched_old:
            changes.append(self._single_entity_change("removed", entity, old_layers))
        for entity in match_result.unmatched_new:
            changes.append(self._single_entity_change("added", entity, new_layers))

        changes.sort(key=_change_sort_key)
        for index, change in enumerate(changes, start=1):
            change.change_id = f"diff:{index:08d}"

        return DrawingDiffResult(
            schema_version=self.SCHEMA_VERSION,
            source_a=_drawing_source(old_drawing),
            source_b=_drawing_source(new_drawing),
            options=self.options,
            changes=changes,
            match_candidates=match_result.candidates,
        )

    def _matched_change(
        self,
        change_type: str,
        match: EntityMatch,
        geometry_diff: GeometryDiff,
        attribute_diffs: List[FieldDiff],
        old_layers: Dict[str, Dict[str, Any]],
        new_layers: Dict[str, Dict[str, Any]],
    ) -> DrawingDiffChange:
        old_bbox = _bbox2(match.old_entity.get("bbox"))
        new_bbox = _bbox2(match.new_entity.get("bbox"))
        bbox = _bbox_union([old_bbox, new_bbox])
        location = _centroid(bbox)
        layer_id = match.new_entity.get("layer_id") or match.old_entity.get("layer_id")
        layer_name = _layer_name(layer_id, new_layers) or _layer_name(match.old_entity.get("layer_id"), old_layers)
        return DrawingDiffChange(
            change_id="",
            change_type=change_type,
            entity_type=str(match.new_entity.get("type") or match.old_entity.get("type") or ""),
            layer_id=str(layer_id) if layer_id is not None else None,
            layer_name=layer_name,
            old_entity_id=str(match.old_entity.get("id") or ""),
            new_entity_id=str(match.new_entity.get("id") or ""),
            location=location,
            bbox=bbox,
            old_bbox=old_bbox,
            new_bbox=new_bbox,
            match=match.candidate,
            geometry_diff=geometry_diff,
            attribute_diffs=attribute_diffs,
            old_entity=copy.deepcopy(match.old_entity) if self.options.include_entity_snapshots else None,
            new_entity=copy.deepcopy(match.new_entity) if self.options.include_entity_snapshots else None,
        )

    def _single_entity_change(
        self,
        change_type: str,
        entity: Dict[str, Any],
        layers: Dict[str, Dict[str, Any]],
    ) -> DrawingDiffChange:
        bbox = _bbox2(entity.get("bbox"))
        location = _centroid(bbox)
        layer_id = entity.get("layer_id")
        return DrawingDiffChange(
            change_id="",
            change_type=change_type,
            entity_type=str(entity.get("type") or ""),
            layer_id=str(layer_id) if layer_id is not None else None,
            layer_name=_layer_name(layer_id, layers),
            old_entity_id=str(entity.get("id") or "") if change_type == "removed" else None,
            new_entity_id=str(entity.get("id") or "") if change_type == "added" else None,
            location=location,
            bbox=bbox,
            old_bbox=bbox if change_type == "removed" else None,
            new_bbox=bbox if change_type == "added" else None,
            old_entity=copy.deepcopy(entity) if change_type == "removed" and self.options.include_entity_snapshots else None,
            new_entity=copy.deepcopy(entity) if change_type == "added" and self.options.include_entity_snapshots else None,
        )


class _CanonicalSpatialIndex:
    def __init__(self, cell_size: float, *, max_cells_per_entity: int = 4096):
        self.cell_size = max(float(cell_size or 1.0), 1e-9)
        self.max_cells_per_entity = max(0, int(max_cells_per_entity or 0))
        self._buckets: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
        self._overflow: List[Dict[str, Any]] = []

    def insert(self, entity: Dict[str, Any]) -> None:
        bbox = _bbox2(entity.get("bbox"))
        if self._cell_count_for_bbox(bbox) > self.max_cells_per_entity > 0:
            self._overflow.append(entity)
            return
        for cell in self._cells_for_bbox(bbox):
            self._buckets.setdefault(cell, []).append(entity)

    def query(self, entity: Dict[str, Any], *, radius: float) -> List[Dict[str, Any]]:
        query_bbox = _inflate_bbox(_bbox2(entity.get("bbox")), radius)
        seen: set[str] = set()
        candidates: List[Dict[str, Any]] = []
        for candidate in self._overflow:
            candidate_id = str(candidate.get("id") or "")
            if candidate_id in seen:
                continue
            if _bbox_distance(query_bbox, _bbox2(candidate.get("bbox"))) <= radius:
                seen.add(candidate_id)
                candidates.append(candidate)
        if self._cell_count_for_bbox(query_bbox) > self.max_cells_per_entity > 0:
            for bucket in self._buckets.values():
                for candidate in bucket:
                    candidate_id = str(candidate.get("id") or "")
                    if candidate_id in seen:
                        continue
                    if _bbox_distance(query_bbox, _bbox2(candidate.get("bbox"))) <= radius:
                        seen.add(candidate_id)
                        candidates.append(candidate)
            candidates.sort(key=_entity_sort_key)
            return candidates
        for cell in self._cells_for_bbox(query_bbox):
            for candidate in self._buckets.get(cell, []):
                candidate_id = str(candidate.get("id") or "")
                if candidate_id in seen:
                    continue
                if _bbox_distance(query_bbox, _bbox2(candidate.get("bbox"))) <= radius:
                    seen.add(candidate_id)
                    candidates.append(candidate)
        candidates.sort(key=_entity_sort_key)
        return candidates

    def _cell_count_for_bbox(self, bbox: BBox2D) -> int:
        min_x, max_x, min_y, max_y = self._cell_range(bbox)
        return (max_x - min_x + 1) * (max_y - min_y + 1)

    def _cells_for_bbox(self, bbox: BBox2D) -> Iterable[Tuple[int, int]]:
        min_x, max_x, min_y, max_y = self._cell_range(bbox)
        for gx in range(min_x, max_x + 1):
            for gy in range(min_y, max_y + 1):
                yield (gx, gy)

    def _cell_range(self, bbox: BBox2D) -> Tuple[int, int, int, int]:
        min_x = math.floor(bbox["min_x"] / self.cell_size)
        max_x = math.floor(bbox["max_x"] / self.cell_size)
        min_y = math.floor(bbox["min_y"] / self.cell_size)
        max_y = math.floor(bbox["max_y"] / self.cell_size)
        return min_x, max_x, min_y, max_y


def _attribute_diffs(old_entity: Dict[str, Any], new_entity: Dict[str, Any]) -> List[FieldDiff]:
    diffs: List[FieldDiff] = []
    if old_entity.get("type") != new_entity.get("type"):
        diffs.append(FieldDiff("type", old_entity.get("type"), new_entity.get("type")))
    if old_entity.get("layer_id") != new_entity.get("layer_id"):
        diffs.append(FieldDiff("layer_id", old_entity.get("layer_id"), new_entity.get("layer_id")))
    if _hash(old_entity, "semantic_hash") != _hash(new_entity, "semantic_hash"):
        diffs.append(FieldDiff("hashes.semantic_hash", _hash(old_entity, "semantic_hash"), _hash(new_entity, "semantic_hash")))
    old_style_hash = _hash(old_entity, "style_hash")
    new_style_hash = _hash(new_entity, "style_hash")
    if old_style_hash != new_style_hash:
        diffs.append(FieldDiff("hashes.style_hash", _hash(old_entity, "style_hash"), _hash(new_entity, "style_hash")))
    elif old_style_hash is None and new_style_hash is None and (old_entity.get("style") or {}) != (new_entity.get("style") or {}):
        diffs.append(FieldDiff("style", old_entity.get("style") or {}, new_entity.get("style") or {}))
    return diffs


def _drawing_source(drawing: Dict[str, Any]) -> Dict[str, Any]:
    metadata = drawing.get("drawing") or {}
    source = metadata.get("source") or {}
    return {
        "drawing_id": metadata.get("id"),
        "title": metadata.get("title"),
        "format": source.get("format"),
        "path": source.get("path"),
        "file_name": source.get("file_name"),
        "sha256": source.get("sha256"),
    }


def _layers_by_id(drawing: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(layer.get("id")): layer for layer in drawing.get("layers") or [] if layer.get("id")}


def _layer_name(layer_id: Any, layers: Dict[str, Dict[str, Any]]) -> Optional[str]:
    if layer_id is None:
        return None
    layer = layers.get(str(layer_id)) or {}
    return layer.get("name") or str(layer_id)


def _layer_key(entity: Dict[str, Any]) -> str:
    return str(entity.get("layer_id") or "")


def _hash(entity: Dict[str, Any], name: str) -> Optional[str]:
    return (entity.get("hashes") or {}).get(name)


def _exact_hash_key(entity: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    geometry_hash = _hash(entity, "geometry_hash")
    if not geometry_hash:
        return None
    return (str(entity.get("type") or ""), _layer_key(entity), str(geometry_hash))


def _bbox2(value: Any) -> BBox2D:
    def finite(raw: Any, default: float = 0.0) -> float:
        try:
            result = float(raw)
        except (TypeError, ValueError, OverflowError):
            return default
        return result if math.isfinite(result) else default

    if isinstance(value, dict):
        min_x = finite(value.get("min_x", 0.0))
        min_y = finite(value.get("min_y", 0.0))
        max_x = finite(value.get("max_x", min_x), min_x)
        max_y = finite(value.get("max_y", min_y), min_y)
        if max_x < min_x:
            min_x, max_x = max_x, min_x
        if max_y < min_y:
            min_y, max_y = max_y, min_y
        return {
            "min_x": min_x,
            "min_y": min_y,
            "max_x": max_x,
            "max_y": max_y,
        }
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        min_x = finite(value[0])
        min_y = finite(value[1])
        max_x = finite(value[2], min_x)
        max_y = finite(value[3], min_y)
        if max_x < min_x:
            min_x, max_x = max_x, min_x
        if max_y < min_y:
            min_y, max_y = max_y, min_y
        return {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y}
    return {"min_x": 0.0, "min_y": 0.0, "max_x": 0.0, "max_y": 0.0}


def _centroid(bbox: BBox2D) -> Point2D:
    return {
        "x": round((bbox["min_x"] + bbox["max_x"]) / 2.0, 6),
        "y": round((bbox["min_y"] + bbox["max_y"]) / 2.0, 6),
    }


def _bbox_union(boxes: Sequence[BBox2D]) -> BBox2D:
    valid = [box for box in boxes if box is not None]
    if not valid:
        return _bbox2(None)
    return {
        "min_x": min(box["min_x"] for box in valid),
        "min_y": min(box["min_y"] for box in valid),
        "max_x": max(box["max_x"] for box in valid),
        "max_y": max(box["max_y"] for box in valid),
    }


def _inflate_bbox(bbox: BBox2D, amount: float) -> BBox2D:
    return {
        "min_x": bbox["min_x"] - amount,
        "min_y": bbox["min_y"] - amount,
        "max_x": bbox["max_x"] + amount,
        "max_y": bbox["max_y"] + amount,
    }


def _bbox_iou(a: BBox2D, b: BBox2D) -> float:
    inter_w = max(0.0, min(a["max_x"], b["max_x"]) - max(a["min_x"], b["min_x"]))
    inter_h = max(0.0, min(a["max_y"], b["max_y"]) - max(a["min_y"], b["min_y"]))
    inter = inter_w * inter_h
    area_a = max(0.0, a["max_x"] - a["min_x"]) * max(0.0, a["max_y"] - a["min_y"])
    area_b = max(0.0, b["max_x"] - b["min_x"]) * max(0.0, b["max_y"] - b["min_y"])
    union = area_a + area_b - inter
    if union <= 0:
        return 1.0 if a == b else 0.0
    return inter / union


def _bbox_distance(a: BBox2D, b: BBox2D) -> float:
    dx = max(a["min_x"] - b["max_x"], b["min_x"] - a["max_x"], 0.0)
    dy = max(a["min_y"] - b["max_y"], b["min_y"] - a["max_y"], 0.0)
    return math.hypot(dx, dy)


def _distance_score(distance: float, radius: float) -> float:
    if not math.isfinite(distance):
        return 0.0
    return max(0.0, 1.0 - distance / max(radius, 1e-9))


def _point_distance_2d(a: Any, b: Any) -> float:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return math.inf
    try:
        return math.hypot(float(a.get("x", 0.0)) - float(b.get("x", 0.0)), float(a.get("y", 0.0)) - float(b.get("y", 0.0)))
    except Exception:
        return math.inf


def _point2(value: Any) -> Optional[Point2D]:
    if not isinstance(value, dict):
        return None
    return {"x": float(value.get("x", 0.0) or 0.0), "y": float(value.get("y", 0.0) or 0.0)}


def _float_or_none(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _angle_delta(old: float, new: float) -> float:
    delta = abs((new - old) % 360.0)
    return min(delta, 360.0 - delta)


def _attributes_key(attributes: Sequence[Dict[str, Any]]) -> List[Tuple[str, str]]:
    return sorted((str(attr.get("tag") or ""), str(attr.get("canonical_text") or attr.get("text") or "")) for attr in attributes)


def _entity_snapshot(entity: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if entity is None:
        return None
    bbox = _bbox2(entity.get("bbox"))
    snapshot = {
        "id": entity.get("id"),
        "type": entity.get("type"),
        "layer_id": entity.get("layer_id"),
        "bbox": bbox,
        "centroid": _centroid(bbox),
        "hashes": copy.deepcopy(entity.get("hashes") or {}),
        "source": copy.deepcopy(entity.get("source") or {}),
    }
    geometry = entity.get("geometry") or {}
    if entity.get("type") in {"text", "mtext"}:
        snapshot["text"] = geometry.get("canonical_text")
    elif entity.get("type") == "block_reference":
        snapshot["block_name"] = geometry.get("block_name")
    return snapshot


def _entity_sort_key(entity: Dict[str, Any]) -> Tuple[Any, ...]:
    bbox = _bbox2(entity.get("bbox"))
    centroid = _centroid(bbox)
    return (
        str(entity.get("type") or ""),
        str(entity.get("layer_id") or ""),
        round(centroid["x"], 6),
        round(centroid["y"], 6),
        str(entity.get("id") or ""),
    )


def _change_sort_key(change: DrawingDiffChange) -> Tuple[Any, ...]:
    order = {"removed": 0, "added": 1, "modified": 2, "unchanged": 3}
    return (
        order.get(change.change_type, 9),
        change.entity_type,
        change.layer_id or "",
        round(change.location["x"], 6),
        round(change.location["y"], 6),
        change.old_entity_id or "",
        change.new_entity_id or "",
    )


def _visual_side(change_type: str) -> str:
    return {
        "added": "b_only",
        "removed": "a_only",
        "modified": "matched",
        "unchanged": "matched",
    }.get(change_type, "matched")


def _visual_color(change_type: str) -> str:
    return {
        "added": "#00b050",
        "removed": "#d93636",
        "modified": "#f59e0b",
        "unchanged": "#808080",
    }.get(change_type, "#808080")


def result_fingerprint(result: DrawingDiffResult) -> str:
    """Stable fingerprint helper for regression tests and cache keys."""

    return _hash_payload("drawing-diff", result.to_dict())
