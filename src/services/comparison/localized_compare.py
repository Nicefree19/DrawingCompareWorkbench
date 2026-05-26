"""Region-aware side-car summaries for drawing comparison outputs."""

from __future__ import annotations

import json
import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .detail_region_matcher import RegionMatchSummary
from .sheet_region_detector import BBox, SheetRegion


@dataclass(frozen=True)
class LocalizedZone:
    """One change-zone annotated with logical before/after region context."""

    zone_id: str
    pair_id: str
    change_type: str
    before_region_id: str = ""
    after_region_id: str = ""
    region_match_id: str = ""
    localized_status: str = "unassigned"
    before_local_bbox: Optional[BBox] = None
    after_local_bbox: Optional[BBox] = None
    review_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "pair_id": self.pair_id,
            "change_type": self.change_type,
            "before_region_id": self.before_region_id,
            "after_region_id": self.after_region_id,
            "region_match_id": self.region_match_id,
            "localized_status": self.localized_status,
            "before_local_bbox": list(self.before_local_bbox) if self.before_local_bbox else None,
            "after_local_bbox": list(self.after_local_bbox) if self.after_local_bbox else None,
            "review_hint": self.review_hint,
        }


@dataclass(frozen=True)
class LocalizedCompareSummary:
    """Region-aware summary for one drawing pair."""

    pair_id: str
    total_zones: int
    assigned_zones: int
    unassigned_zone_count: int = 0
    cross_region_zone_count: int = 0
    review_required_zone_count: int = 0
    gate_status: str = "passed"
    gate_reasons: tuple[str, ...] = tuple()
    localized_zones: tuple[LocalizedZone, ...] = tuple()
    status_counts: dict[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = tuple()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "total_zones": self.total_zones,
            "assigned_zones": self.assigned_zones,
            "assignment_rate": (
                self.assigned_zones / self.total_zones if self.total_zones else 0.0
            ),
            "unassigned_zone_count": self.unassigned_zone_count,
            "cross_region_zone_count": self.cross_region_zone_count,
            "review_required_zone_count": self.review_required_zone_count,
            "gate_status": self.gate_status,
            "gate_reasons": list(self.gate_reasons),
            "status_counts": dict(self.status_counts),
            "localized_zones": [zone.to_dict() for zone in self.localized_zones],
            "warnings": list(self.warnings),
        }


def localize_change_zones(
    zones: Sequence[dict[str, Any]],
    *,
    before_regions: Sequence[SheetRegion],
    after_regions: Sequence[SheetRegion],
    match_summary: RegionMatchSummary,
    pair_id: str = "",
) -> LocalizedCompareSummary:
    """Annotate existing change zones with detail-region context."""

    before_by_id = {region.region_id: region for region in before_regions}
    after_by_id = {region.region_id: region for region in after_regions}
    match_by_pair: dict[tuple[str, str], str] = {}
    match_status_by_pair: dict[tuple[str, str], str] = {}
    for match in match_summary.matches:
        if match.before_region_id and match.after_region_id:
            key = (match.before_region_id, match.after_region_id)
            match_by_pair[key] = match.match_id
            match_status_by_pair[key] = match.status

    localized: list[LocalizedZone] = []
    for zone in zones:
        if pair_id and str(zone.get("pair_id") or "") != pair_id:
            continue
        change_type = str(zone.get("change_type") or "")
        before_bbox = _parse_bbox(zone.get("old_bbox"))
        after_bbox = _parse_bbox(zone.get("bbox"))

        before_region = _region_for_bbox(before_bbox, before_regions)
        after_region = _region_for_bbox(after_bbox, after_regions)

        before_id = before_region.region_id if before_region else ""
        after_id = after_region.region_id if after_region else ""
        match_id = match_by_pair.get((before_id, after_id), "")
        matched_status = match_status_by_pair.get((before_id, after_id), "")

        status = "unassigned"
        hint = "구역을 상세 영역에 배정하지 못했습니다."
        if change_type == "added":
            if after_id:
                status = "added_in_after_region"
                hint = "변경 후 도면에만 존재하는 추가 상세/요소입니다."
        elif change_type == "deleted":
            if before_id:
                status = "deleted_from_before_region"
                hint = "기존 도면에서 삭제된 상세/요소입니다."
        elif before_id and after_id and match_id:
            status = "matched_region_change"
            if matched_status == "review_required":
                status = "matched_region_review_required"
            hint = "동일 상세 영역 안의 변경으로 검토할 수 있습니다."
        elif before_id and after_id:
            status = "cross_region_or_unmatched_detail"
            hint = "두 bbox가 서로 매칭되지 않은 상세 영역에 있어 수동 확인이 필요합니다."
        elif before_id:
            status = "before_region_only"
            hint = "기존 도면 쪽 상세 영역만 식별되었습니다."
        elif after_id:
            status = "after_region_only"
            hint = "변경 도면 쪽 상세 영역만 식별되었습니다."
        hint = _localized_review_hint(status)

        localized.append(
            LocalizedZone(
                zone_id=str(zone.get("zone_id") or ""),
                pair_id=str(zone.get("pair_id") or pair_id),
                change_type=change_type,
                before_region_id=before_id,
                after_region_id=after_id,
                region_match_id=match_id,
                localized_status=status,
                before_local_bbox=_local_bbox(before_bbox, before_region) if before_region else None,
                after_local_bbox=_local_bbox(after_bbox, after_region) if after_region else None,
                review_hint=hint,
            )
        )

    counts = Counter(zone.localized_status for zone in localized)
    assigned = sum(1 for zone in localized if zone.localized_status != "unassigned")
    unassigned_count = counts.get("unassigned", 0)
    cross_region_count = counts.get("cross_region_or_unmatched_detail", 0)
    review_required_count = counts.get("matched_region_review_required", 0)
    warnings: list[str] = []
    if localized and assigned / len(localized) < 0.5:
        warnings.append("less than half of zones could be assigned to detected regions")
    if cross_region_count > 0:
        warnings.append("some zones span unmatched detail regions")
    gate_reasons: list[str] = []
    if unassigned_count:
        gate_reasons.append("one or more change bboxes are outside detected detail regions")
    if cross_region_count:
        gate_reasons.append("one or more changes span unmatched before/after regions")
    if review_required_count:
        gate_reasons.append("one or more changes are in region matches that require manual review")
    return LocalizedCompareSummary(
        pair_id=pair_id,
        total_zones=len(localized),
        assigned_zones=assigned,
        unassigned_zone_count=unassigned_count,
        cross_region_zone_count=cross_region_count,
        review_required_zone_count=review_required_count,
        gate_status="review_required" if gate_reasons else "passed",
        gate_reasons=tuple(gate_reasons),
        localized_zones=tuple(localized),
        status_counts=dict(counts),
        warnings=tuple(warnings),
    )


def _localized_review_hint(status: str) -> str:
    return {
        "added_in_after_region": "The added change is inside an after-side detail region.",
        "deleted_from_before_region": "The deleted change is inside a before-side detail region.",
        "matched_region_change": "The change is inside a matched detail region.",
        "matched_region_review_required": "The change is inside a region match that requires manual review.",
        "cross_region_or_unmatched_detail": (
            "The before/after bboxes are in regions that are not matched to each other."
        ),
        "before_region_only": "Only a before-side detail region was detected for this change.",
        "after_region_only": "Only an after-side detail region was detected for this change.",
    }.get(status, "The change bbox could not be assigned to a detected detail region.")


def read_change_zones(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    zones = payload.get("zones") if isinstance(payload, dict) else None
    return list(zones or [])


def write_localized_compare_summary(
    summaries: Sequence[LocalizedCompareSummary],
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "pair_count": len(summaries),
        "summaries": [summary.to_dict() for summary in summaries],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def serialize_localized_region_result(
    result: Any,
    *,
    match_id: str,
    before_region: SheetRegion,
    after_region: SheetRegion,
) -> dict[str, Any]:
    """Serialize one region-local comparison result with world-space metadata."""

    return {
        "match_id": match_id,
        "before_region_id": before_region.region_id,
        "after_region_id": after_region.region_id,
        "before_region_bbox": list(before_region.bbox),
        "after_region_bbox": list(after_region.bbox),
        "before_local_origin": [before_region.bbox[0], before_region.bbox[1]],
        "after_local_origin": [after_region.bbox[0], after_region.bbox[1]],
        "total_changes": int(getattr(result, "total_changes", 0)),
        "added_count": int(getattr(result, "added_count", 0)),
        "deleted_count": int(getattr(result, "deleted_count", 0)),
        "modified_count": int(getattr(result, "modified_count", 0)),
        "metadata": dict(getattr(result, "metadata", {}) or {}),
        "changes": [_serialize_change(change) for change in getattr(result, "changes", [])],
    }


def write_localized_region_compare_results(
    payload: Mapping[str, Any],
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def compare_localized_region_entities(
    entities_before: Mapping[str, Sequence[Any]],
    entities_after: Mapping[str, Sequence[Any]],
    *,
    before_region: SheetRegion,
    after_region: SheetRegion,
    match_id: str = "",
    comparator: Any = None,
    near_match_tolerance: Optional[float] = None,
) -> Any:
    """Compare entities inside one matched region after local-origin normalization.

    This is the testable core for the future automatic multi-frame compare
    path. It does not replace the global comparison pipeline yet; callers must
    opt in after region matching has passed review.
    """

    from .dxf_comparator import DxfChangeType, DxfComparator, DxfComparisonResult

    before_selected = _entities_in_region(entities_before, before_region.bbox)
    after_selected = _entities_in_region(entities_after, after_region.bbox)
    before_local = _translate_entity_map(before_selected, -before_region.bbox[0], -before_region.bbox[1])
    after_local = _translate_entity_map(after_selected, -after_region.bbox[0], -after_region.bbox[1])

    if not before_local and not after_local:
        result = DxfComparisonResult()
    else:
        cmp = comparator or DxfComparator()
        result = cmp.compare_with_modified_detection(
            before_local,
            after_local,
            near_match_tolerance=near_match_tolerance,
        )

    for change in result.changes:
        if change.old_data is not None:
            change.old_data = _translate_entity_data(
                change.entity_type,
                change.old_data,
                before_region.bbox[0],
                before_region.bbox[1],
            )
        if change.new_data is not None:
            change.new_data = _translate_entity_data(
                change.entity_type,
                change.new_data,
                after_region.bbox[0],
                after_region.bbox[1],
            )
        if change.location is not None:
            origin = before_region.bbox if change.change_type == DxfChangeType.DELETED else after_region.bbox
            change.location = _translate_point(change.location, origin[0], origin[1])
        if change.old_location is not None:
            change.old_location = _translate_point(change.old_location, before_region.bbox[0], before_region.bbox[1])
        world_data = change.old_data if change.change_type == DxfChangeType.DELETED else change.new_data
        world_bbox = _entity_bbox_from_data(change.entity_type, world_data)
        metadata = getattr(change, "metadata", None)
        if metadata is None:
            metadata = {}
            setattr(change, "metadata", metadata)
        metadata.update(
            {
                "localized_compare": True,
                "bbox": list(world_bbox) if world_bbox else None,
                "bbox_coordinate_space": "world_from_region_local",
                "match_id": match_id,
                "before_region_id": before_region.region_id,
                "after_region_id": after_region.region_id,
                "before_local_origin": [before_region.bbox[0], before_region.bbox[1]],
                "after_local_origin": [after_region.bbox[0], after_region.bbox[1]],
            }
        )

    result.metadata.update(
        {
            "localized_compare": True,
            "localized_compare_status": "passed",
            "match_id": match_id,
            "before_region_id": before_region.region_id,
            "after_region_id": after_region.region_id,
            "before_region_bbox": list(before_region.bbox),
            "after_region_bbox": list(after_region.bbox),
            "before_local_entity_count": sum(len(items) for items in before_local.values()),
            "after_local_entity_count": sum(len(items) for items in after_local.values()),
        }
    )
    return result


def _serialize_change(change: Any) -> dict[str, Any]:
    if hasattr(change, "to_dict"):
        try:
            payload = dict(change.to_dict())
        except Exception:
            payload = {}
    else:
        payload = {}
    for key in (
        "entity_type",
        "layer",
        "old_data",
        "new_data",
        "location",
        "old_location",
        "change_detail",
        "change_category",
    ):
        if key not in payload and hasattr(change, key):
            payload[key] = getattr(change, key)
    change_type = getattr(change, "change_type", None)
    if "change_type" not in payload and change_type is not None:
        payload["change_type"] = getattr(change_type, "value", str(change_type))
    metadata = getattr(change, "metadata", None)
    if isinstance(metadata, Mapping):
        payload["metadata"] = dict(metadata)
        if metadata.get("bbox") is not None:
            payload.setdefault("bbox", metadata.get("bbox"))
        if metadata.get("bbox_coordinate_space") is not None:
            payload.setdefault("bbox_coordinate_space", metadata.get("bbox_coordinate_space"))
    return payload


def _parse_bbox(raw: Any) -> Optional[BBox]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        values = [raw.get("min_x"), raw.get("min_y"), raw.get("max_x"), raw.get("max_y")]
    elif isinstance(raw, (list, tuple)) and len(raw) >= 4:
        values = list(raw[:4])
    else:
        return None
    try:
        x0, y0, x1, y1 = [float(value) for value in values]
    except Exception:
        return None
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _region_for_bbox(
    bbox: Optional[BBox],
    regions: Sequence[SheetRegion],
) -> Optional[SheetRegion]:
    if bbox is None or not regions:
        return None
    center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
    containing = [region for region in regions if _contains(region.bbox, center)]
    if containing:
        return min(containing, key=lambda region: region.area or 0.0)
    overlap_candidates = [
        (region, _bbox_intersection_area(bbox, region.bbox))
        for region in regions
    ]
    overlap_candidates = [
        (region, overlap)
        for region, overlap in overlap_candidates
        if overlap > 0.0
    ]
    if overlap_candidates:
        region, overlap = max(overlap_candidates, key=lambda item: item[1])
        bbox_area = max(_bbox_area(bbox), 1e-9)
        if overlap / bbox_area >= 0.20:
            return region

    nearest = min(regions, key=lambda region: _distance_to_bbox(center, region.bbox))
    max_near_distance = max(25.0, math.hypot(nearest.width, nearest.height) * 0.02)
    if _distance_to_bbox(center, nearest.bbox) <= max_near_distance:
        return nearest
    return None


def _local_bbox(bbox: Optional[BBox], region: SheetRegion) -> Optional[BBox]:
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    rx0, ry0, _rx1, _ry1 = region.bbox
    return (x0 - rx0, y0 - ry0, x1 - rx0, y1 - ry0)


def _contains(bbox: BBox, point: tuple[float, float]) -> bool:
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5


def _distance_to_bbox(point: tuple[float, float], bbox: BBox) -> float:
    x, y = point
    dx = max(bbox[0] - x, 0.0, x - bbox[2])
    dy = max(bbox[1] - y, 0.0, y - bbox[3])
    return (dx * dx + dy * dy) ** 0.5


def _bbox_intersection_area(left: BBox, right: BBox) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    return _bbox_area((x0, y0, x1, y1))


def _bbox_area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _entities_in_region(
    entities: Mapping[str, Sequence[Any]],
    region_bbox: BBox,
) -> dict[str, list[Any]]:
    selected: dict[str, list[Any]] = {}
    for entity_type, items in entities.items():
        kept: list[Any] = []
        for entity in items:
            bbox = _entity_bbox_from_data(str(getattr(entity, "entity_type", entity_type)), getattr(entity, "data", {}))
            location = _point_from_any(getattr(entity, "location", None))
            if bbox is not None:
                if _bboxes_overlap(bbox, region_bbox) or _contains(region_bbox, _bbox_center(bbox)):
                    kept.append(entity)
                continue
            if location is not None and _contains(region_bbox, location):
                kept.append(entity)
        if kept:
            selected[str(entity_type)] = kept
    return selected


def _translate_entity_map(
    entities: Mapping[str, Sequence[Any]],
    dx: float,
    dy: float,
) -> dict[str, list[Any]]:
    translated: dict[str, list[Any]] = {}
    for entity_type, items in entities.items():
        translated_items: list[Any] = []
        for entity in items:
            local_data = _translate_entity_data(str(getattr(entity, "entity_type", entity_type)), getattr(entity, "data", {}), dx, dy)
            local_location = _translate_point(getattr(entity, "location", None), dx, dy) or getattr(entity, "location", None)
            local_hash = hashlib.sha256(
                json.dumps(
                    {
                        "entity_type": str(getattr(entity, "entity_type", entity_type)),
                        "data": local_data,
                    },
                    sort_keys=True,
                    default=list,
                ).encode("utf-8")
            ).hexdigest()
            translated_items.append(
                replace(
                    entity,
                    hash=local_hash,
                    data=local_data,
                    location=local_location,
                )
            )
        if translated_items:
            translated[str(entity_type)] = translated_items
    return translated


def _entity_bbox_from_data(entity_type: str, data: Any) -> Optional[BBox]:
    if not isinstance(data, Mapping):
        return None
    raw_bbox = _parse_bbox(data.get("bbox"))
    if raw_bbox is not None:
        return raw_bbox
    entity_type = entity_type.upper()
    if entity_type == "LINE":
        return _bbox_from_points([data.get("start"), data.get("end")])
    if entity_type in {"CIRCLE", "ARC"}:
        center = _point_from_any(data.get("center"))
        radius = _float_value(data.get("radius"))
        if center is None or radius is None:
            return None
        return (center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius)
    if entity_type in {"LWPOLYLINE", "POLYLINE"}:
        return _bbox_from_points(data.get("points") or data.get("vertices") or [])
    if entity_type in {"TEXT", "MTEXT"}:
        point = _point_from_any(data.get("position") or data.get("insert"))
        return _point_bbox(point, 1.0) if point is not None else None
    if entity_type == "DIMENSION":
        point = _point_from_any(data.get("defpoint") or data.get("position"))
        return _point_bbox(point, 1.0) if point is not None else None
    if entity_type == "INSERT":
        point = _point_from_any(data.get("insert_point") or data.get("insert"))
        return _point_bbox(point, 1.0) if point is not None else None
    point = _point_from_any(data.get("location"))
    return _point_bbox(point, 1.0) if point is not None else None


def _translate_entity_data(entity_type: str, data: Any, dx: float, dy: float) -> Any:
    if not isinstance(data, Mapping):
        return data
    out = dict(data)
    entity_type = entity_type.upper()
    if "bbox" in out:
        out["bbox"] = _translate_bbox(_parse_bbox(out.get("bbox")), dx, dy)
    if entity_type == "LINE":
        out["start"] = _translate_point(out.get("start"), dx, dy)
        out["end"] = _translate_point(out.get("end"), dx, dy)
    elif entity_type in {"CIRCLE", "ARC"}:
        out["center"] = _translate_point(out.get("center"), dx, dy)
    elif entity_type in {"LWPOLYLINE", "POLYLINE"}:
        points = out.get("points")
        if points is not None:
            out["points"] = [_translate_point(point, dx, dy) for point in points]
        vertices = out.get("vertices")
        if vertices is not None:
            out["vertices"] = [_translate_point(point, dx, dy) for point in vertices]
    elif entity_type in {"TEXT", "MTEXT"}:
        if "position" in out:
            out["position"] = _translate_point(out.get("position"), dx, dy)
        if "insert" in out:
            out["insert"] = _translate_point(out.get("insert"), dx, dy)
    elif entity_type == "DIMENSION":
        if "defpoint" in out:
            out["defpoint"] = _translate_point(out.get("defpoint"), dx, dy)
        if "position" in out:
            out["position"] = _translate_point(out.get("position"), dx, dy)
    elif entity_type == "INSERT":
        if "insert_point" in out:
            out["insert_point"] = _translate_point(out.get("insert_point"), dx, dy)
        if "insert" in out:
            out["insert"] = _translate_point(out.get("insert"), dx, dy)
    elif "location" in out:
        out["location"] = _translate_point(out.get("location"), dx, dy)
    return out


def _translate_point(raw: Any, dx: float, dy: float) -> Optional[tuple[float, float]]:
    point = _point_from_any(raw)
    if point is None:
        return None
    return (point[0] + dx, point[1] + dy)


def _translate_bbox(bbox: Optional[BBox], dx: float, dy: float) -> Optional[BBox]:
    if bbox is None:
        return None
    return (bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy)


def _bbox_from_points(points: Sequence[Any]) -> Optional[BBox]:
    parsed = [_point_from_any(point) for point in points]
    valid = [point for point in parsed if point is not None]
    if not valid:
        return None
    return (
        min(point[0] for point in valid),
        min(point[1] for point in valid),
        max(point[0] for point in valid),
        max(point[1] for point in valid),
    )


def _point_bbox(point: tuple[float, float], radius: float) -> BBox:
    return (point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius)


def _bbox_center(bbox: BBox) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _point_from_any(raw: Any) -> Optional[tuple[float, float]]:
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        x_value = raw.get("x")
        y_value = raw.get("y")
    elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
        x_value, y_value = raw[0], raw[1]
    else:
        return None
    try:
        return (float(x_value), float(y_value))
    except (TypeError, ValueError):
        return None


def _float_value(raw: Any) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _bboxes_overlap(left: BBox, right: BBox) -> bool:
    return left[0] <= right[2] and left[2] >= right[0] and left[1] <= right[3] and left[3] >= right[1]
