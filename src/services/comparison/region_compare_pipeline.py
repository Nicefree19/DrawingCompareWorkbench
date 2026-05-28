"""Region-local primary change-zone builder for multi-detail CAD compares."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .base import ChangeRecord, ChangeType, ComparisonResult
from .change_zones import ChangeZoneOptions, DrawingChangeZone, build_change_zones
from .localized_compare import compare_localized_region_entities


APPROVED_REGION_MATCH_STATUSES = {"auto_matched", "manual_matched"}


def build_region_local_primary_change_zones(
    pair_contexts: Sequence[Mapping[str, Any]],
    *,
    extractor: Any,
    comparator: Callable[..., Any] = compare_localized_region_entities,
    zone_options: ChangeZoneOptions | None = None,
) -> dict[str, Any]:
    """Build a primary region-local zone stream without using global zones."""

    zones: list[DrawingChangeZone] = []
    pair_summaries: list[dict[str, Any]] = []
    gate_reasons: list[str] = []
    next_zone_index = 1
    approved_match_count = 0
    compared_region_count = 0
    unmatched_detail_zone_count = 0
    review_required_match_count = 0
    unsupported_pair_count = 0

    for context in pair_contexts:
        pair_id = str(context.get("pair_id") or "")
        original_source_a = Path(context.get("source_a") or "")
        original_source_b = Path(context.get("source_b") or "")
        source_a = Path(context.get("region_compare_source_a") or original_source_a)
        source_b = Path(context.get("region_compare_source_b") or original_source_b)
        match_summary = context.get("match_summary")
        before_result = context.get("before_result")
        after_result = context.get("after_result")
        pair_reasons: list[str] = []
        pair_zone_count_before = len(zones)

        if source_a.suffix.lower() != ".dxf" or source_b.suffix.lower() != ".dxf":
            unsupported_pair_count += 1
            pair_reasons.append(
                "region-local primary compare requires resolved DXF sources "
                f"(before={original_source_a.suffix.lower() or '<none>'}:"
                f"{context.get('region_compare_source_a_reason') or 'unresolved'}, "
                f"after={original_source_b.suffix.lower() or '<none>'}:"
                f"{context.get('region_compare_source_b_reason') or 'unresolved'})"
            )

        if int(getattr(match_summary, "review_required_count", 0) or 0) > 0:
            review_required_match_count += int(getattr(match_summary, "review_required_count", 0) or 0)
            pair_reasons.append("one or more region matches require manual review")

        if pair_reasons:
            gate_reasons.extend(f"{pair_id}: {reason}" for reason in pair_reasons)
            pair_summaries.append(
                {
                    "pair_id": pair_id,
                    "status": "review_required",
                    "source_a": str(original_source_a),
                    "source_b": str(original_source_b),
                    "region_compare_source_a": str(source_a),
                    "region_compare_source_b": str(source_b),
                    "gate_reasons": pair_reasons,
                    "approved_match_count": 0,
                    "localized_zone_count": 0,
                    "unmatched_detail_zone_count": 0,
                }
            )
            continue

        before_regions = {
            region.region_id: region
            for region in getattr(before_result, "regions", ()) or ()
        }
        after_regions = {
            region.region_id: region
            for region in getattr(after_result, "regions", ()) or ()
        }
        try:
            entities_before = extractor.extract_from_file(source_a)
            entities_after = extractor.extract_from_file(source_b)
        except Exception as exc:  # noqa: BLE001
            reason = f"DXF entity extraction failed: {exc}"
            gate_reasons.append(f"{pair_id}: {reason}")
            pair_summaries.append(
                {
                    "pair_id": pair_id,
                    "status": "review_required",
                    "source_a": str(original_source_a),
                    "source_b": str(original_source_b),
                    "region_compare_source_a": str(source_a),
                    "region_compare_source_b": str(source_b),
                    "gate_reasons": [reason],
                    "approved_match_count": 0,
                    "localized_zone_count": 0,
                    "unmatched_detail_zone_count": 0,
                }
            )
            continue

        pair_approved = 0
        pair_unmatched = 0
        for match in getattr(match_summary, "matches", ()) or ():
            status = str(getattr(match, "status", "") or "")
            if status in APPROVED_REGION_MATCH_STATUSES:
                before_region = before_regions.get(str(getattr(match, "before_region_id", "") or ""))
                after_region = after_regions.get(str(getattr(match, "after_region_id", "") or ""))
                if before_region is None or after_region is None:
                    gate_reasons.append(f"{pair_id}: approved region match references a missing region")
                    continue
                pair_approved += 1
                approved_match_count += 1
                result = comparator(
                    entities_before,
                    entities_after,
                    before_region=before_region,
                    after_region=after_region,
                    match_id=str(getattr(match, "match_id", "") or ""),
                )
                region_zones = build_change_zones(
                    _comparison_result_from_localized_result(result),
                    pair_id=pair_id,
                    drawing_number=getattr(before_region, "drawing_number", "") or getattr(after_region, "drawing_number", ""),
                    options=zone_options,
                )
                for zone in region_zones:
                    zone.zone_id = f"R-{next_zone_index:03d}"
                    next_zone_index += 1
                    zone.metadata.update(
                        {
                            "primary_compare_source": "region_local",
                            "region_local_primary": True,
                            "region_match_id": str(getattr(match, "match_id", "") or ""),
                            "before_region_id": before_region.region_id,
                            "after_region_id": after_region.region_id,
                        }
                    )
                    zones.append(zone)
                compared_region_count += 1
            elif status == "unmatched_before":
                region = before_regions.get(str(getattr(match, "before_region_id", "") or ""))
                if region is not None:
                    zones.append(
                        _detail_region_zone(
                            zone_id=f"R-{next_zone_index:03d}",
                            pair_id=pair_id,
                            region=region,
                            change_type="deleted",
                            match_id=str(getattr(match, "match_id", "") or ""),
                        )
                    )
                    next_zone_index += 1
                    pair_unmatched += 1
                    unmatched_detail_zone_count += 1
            elif status == "unmatched_after":
                region = after_regions.get(str(getattr(match, "after_region_id", "") or ""))
                if region is not None:
                    zones.append(
                        _detail_region_zone(
                            zone_id=f"R-{next_zone_index:03d}",
                            pair_id=pair_id,
                            region=region,
                            change_type="added",
                            match_id=str(getattr(match, "match_id", "") or ""),
                        )
                    )
                    next_zone_index += 1
                    pair_unmatched += 1
                    unmatched_detail_zone_count += 1

        pair_zone_count = len(zones) - pair_zone_count_before
        pair_summaries.append(
            {
                "pair_id": pair_id,
                "status": "passed" if pair_zone_count else "skipped",
                "source_a": str(original_source_a),
                "source_b": str(original_source_b),
                "region_compare_source_a": str(source_a),
                "region_compare_source_b": str(source_b),
                "gate_reasons": [],
                "approved_match_count": pair_approved,
                "localized_zone_count": pair_zone_count - pair_unmatched,
                "unmatched_detail_zone_count": pair_unmatched,
            }
        )

    raw_change_count = sum(int(zone.raw_change_count or 0) for zone in zones)
    primary_enabled = bool(zones) and not gate_reasons
    status = "passed" if primary_enabled else "review_required" if gate_reasons else "skipped"
    return {
        "schema_version": 2,
        "mode": "region_local_primary",
        "primary_enabled": primary_enabled,
        "status": status,
        "gate_reasons": gate_reasons,
        "pair_count": len(pair_contexts),
        "pair_summaries": pair_summaries,
        "approved_match_count": approved_match_count,
        "review_required_match_count": review_required_match_count,
        "compared_region_count": compared_region_count,
        "unmatched_detail_zone_count": unmatched_detail_zone_count,
        "unsupported_pair_count": unsupported_pair_count,
        "zone_count": len(zones),
        "raw_change_count": raw_change_count,
        "zones": [zone.to_dict() for zone in zones],
        "global_compare_preserved_as_fallback": True,
    }


def write_region_local_primary_change_zones(
    payload: Mapping[str, Any],
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _comparison_result_from_localized_result(result: Any) -> ComparisonResult:
    converted = ComparisonResult(
        source_a=str((getattr(result, "metadata", {}) or {}).get("before_region_id") or ""),
        source_b=str((getattr(result, "metadata", {}) or {}).get("after_region_id") or ""),
    )
    converted.metadata.update(dict(getattr(result, "metadata", {}) or {}))
    for index, change in enumerate(getattr(result, "changes", []) or [], start=1):
        metadata = dict(getattr(change, "metadata", {}) or {})
        entity_type = str(getattr(change, "entity_type", "") or metadata.get("entity_type") or "")
        layer = str(getattr(change, "layer", "") or metadata.get("layer") or "")
        if entity_type:
            metadata.setdefault("entity_type", entity_type)
        if layer:
            metadata.setdefault("layer", layer)
        metadata.setdefault("localized_compare", True)
        converted.add_change(
            ChangeRecord(
                key=str(getattr(change, "key", "") or f"region-local-{index}"),
                change_type=_base_change_type(getattr(change, "change_type", "")),
                field_name=str(getattr(change, "change_category", "") or ""),
                old_value=getattr(change, "old_data", None),
                new_value=getattr(change, "new_data", None),
                location=getattr(change, "location", None),
                metadata=metadata,
            )
        )
    return converted


def _base_change_type(value: Any) -> ChangeType:
    text = str(getattr(value, "value", value) or "").lower()
    if text == ChangeType.ADDED.value:
        return ChangeType.ADDED
    if text == ChangeType.DELETED.value:
        return ChangeType.DELETED
    return ChangeType.MODIFIED


def _detail_region_zone(
    *,
    zone_id: str,
    pair_id: str,
    region: Any,
    change_type: str,
    match_id: str,
) -> DrawingChangeZone:
    entity_count = max(1, int(getattr(region, "entity_count", 0) or 0))
    bbox = tuple(getattr(region, "bbox", (0.0, 0.0, 0.0, 0.0)))
    centroid = (
        (float(bbox[0]) + float(bbox[2])) / 2.0,
        (float(bbox[1]) + float(bbox[3])) / 2.0,
    )
    is_added = change_type == "added"
    return DrawingChangeZone(
        zone_id=zone_id,
        pair_id=pair_id,
        pair_uuid=pair_id,
        display_label=str(getattr(region, "drawing_number", "") or pair_id),
        drawing_number=str(getattr(region, "drawing_number", "") or ""),
        change_type=change_type,
        severity="high",
        bbox=bbox,
        old_bbox=bbox if not is_added else None,
        centroid=centroid,
        raw_change_count=entity_count,
        added_count=entity_count if is_added else 0,
        deleted_count=0 if is_added else entity_count,
        modified_count=0,
        layers=tuple(sorted((getattr(region, "layer_histogram", {}) or {}).keys())),
        entity_types=tuple(sorted((getattr(region, "entity_histogram", {}) or {}).keys())),
        status="review_required",
        reasons=(f"detail region {change_type}",),
        metadata={
            "primary_compare_source": "region_local",
            "region_local_primary": True,
            "region_match_id": match_id,
            "region_id": str(getattr(region, "region_id", "") or ""),
            "region_kind": str(getattr(region, "region_kind", "") or ""),
            "detection_method": str(getattr(region, "detection_method", "") or ""),
        },
    )


__all__ = [
    "APPROVED_REGION_MATCH_STATUSES",
    "build_region_local_primary_change_zones",
    "write_region_local_primary_change_zones",
]
