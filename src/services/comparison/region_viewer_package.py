"""Region viewer manifest builder for region-local compare artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .sheet_region_detector import BBox
from .zone_render_worker import render_zone_focus


REGION_VIEWER_MANIFEST_NAME = "region_viewer_manifest.json"


def export_region_viewer_package(
    artifact_dir: str | Path,
    output_dir: str | Path | None = None,
    *,
    max_entries: int = 100,
    renderer: Callable[..., Any] = render_zone_focus,
) -> Path:
    """Create a manifest that lets the GUI load region-local crop packs."""

    artifact_root = Path(artifact_dir)
    output_root = Path(output_dir) if output_dir else artifact_root / "region_viewer"
    output_root.mkdir(parents=True, exist_ok=True)

    detection_path = artifact_root / "region_detection_summary.json"
    match_path = artifact_root / "region_match_summary.json"
    localized_path = artifact_root / "localized_change_zones_v2.json"
    detections = _read_json(detection_path)
    matches = _read_json(match_path) if match_path.exists() else {}
    localized = _read_json(localized_path) if localized_path.exists() else {}

    regions = _regions_by_id(detections)
    match_status = _match_status_by_id(matches)
    entries = []
    zones = list(localized.get("zones") or [])[: max(0, int(max_entries))]
    for zone in zones:
        if not isinstance(zone, Mapping):
            continue
        entry = _build_entry(
            zone,
            regions=regions,
            match_status=match_status,
            output_root=output_root,
            renderer=renderer,
        )
        if entry:
            entries.append(entry)

    manifest = {
        "schema_version": 1,
        "mode": "region_viewer",
        "source_artifacts": {
            "region_detection_summary_json": str(detection_path),
            "region_match_summary_json": str(match_path),
            "localized_change_zones_v2_json": str(localized_path),
        },
        "entry_count": len(entries),
        "localized_zone_count": len(localized.get("zones") or []),
        "truncated": len(localized.get("zones") or []) > len(entries),
        "coordinate_source": "cad_world",
        "entries": entries,
    }
    manifest_path = output_root / REGION_VIEWER_MANIFEST_NAME
    _write_json(manifest_path, manifest)
    return manifest_path


def _build_entry(
    zone: Mapping[str, Any],
    *,
    regions: Mapping[str, Mapping[str, Any]],
    match_status: Mapping[str, str],
    output_root: Path,
    renderer: Callable[..., Any],
) -> Optional[dict[str, Any]]:
    zone_id = str(zone.get("zone_id") or "")
    if not zone_id:
        return None
    metadata = zone.get("metadata") if isinstance(zone.get("metadata"), Mapping) else {}
    before_region_id = str(metadata.get("before_region_id") or "")
    after_region_id = str(metadata.get("after_region_id") or "")
    region_id = str(metadata.get("region_id") or "")
    change_type = str(zone.get("change_type") or "")
    if not before_region_id and change_type == "deleted":
        before_region_id = region_id
    if not after_region_id and change_type == "added":
        after_region_id = region_id
    match_id = str(metadata.get("region_match_id") or "")
    entry_dir = output_root / _safe_name(zone_id)
    before = _side_payload(
        region=regions.get(before_region_id),
        side="before",
        entry_dir=entry_dir,
        renderer=renderer,
    )
    after = _side_payload(
        region=regions.get(after_region_id),
        side="after",
        entry_dir=entry_dir,
        renderer=renderer,
    )
    return {
        "entry_id": zone_id,
        "zone_id": zone_id,
        "pair_id": str(zone.get("pair_id") or ""),
        "change_type": change_type,
        "region_match_id": match_id,
        "region_match_status": match_status.get(match_id, ""),
        "zone_bbox": zone.get("bbox"),
        "old_zone_bbox": zone.get("old_bbox"),
        "before": before,
        "after": after,
    }


def _side_payload(
    *,
    region: Optional[Mapping[str, Any]],
    side: str,
    entry_dir: Path,
    renderer: Callable[..., Any],
) -> Optional[dict[str, Any]]:
    if not region:
        return None
    bbox = _parse_bbox(region.get("bbox"))
    source_path = str(region.get("source_path") or "")
    payload: dict[str, Any] = {
        "side": side,
        "region_id": str(region.get("region_id") or ""),
        "source_path": source_path,
        "source_format": str(region.get("source_format") or ""),
        "region_bbox": list(bbox) if bbox else None,
        "region_local_origin": [bbox[0], bbox[1]] if bbox else None,
        "world_to_region_local": {
            "translate_x": -bbox[0],
            "translate_y": -bbox[1],
            "scale": 1.0,
            "rotation_deg": 0.0,
        } if bbox else None,
    }
    if not source_path or bbox is None:
        payload.update({"render_status": "skipped", "skipped_reason": "missing source path or bbox"})
        return payload
    if Path(source_path).suffix.lower() not in {".dxf", ".dwg"}:
        payload.update({"render_status": "skipped", "skipped_reason": "region focus rendering supports CAD sources only"})
        return payload
    try:
        result = renderer(
            Path(source_path),
            bbox,
            entry_dir / side,
            padding_ratio=0.08,
        )
    except Exception as exc:  # noqa: BLE001
        payload.update({"render_status": "failed", "skipped_reason": str(exc)})
        return payload
    result_payload = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
    payload.update(
        {
            "render_status": "rendered" if result_payload.get("output_path") else "skipped",
            "focus_pack_json": result_payload.get("output_path") or "",
            "focus_primitive_count": int(result_payload.get("primitive_count") or 0),
            "focus_entity_count": int(result_payload.get("entity_count") or 0),
            "focus_truncated": bool(result_payload.get("truncated")),
            "skipped_reason": str(result_payload.get("skipped_reason") or ""),
        }
    )
    return payload


def _regions_by_id(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for result in payload.get("results") or []:
        if not isinstance(result, Mapping):
            continue
        for region in result.get("regions") or []:
            if not isinstance(region, Mapping):
                continue
            region_id = str(region.get("region_id") or "")
            if region_id:
                out[region_id] = region
    return out


def _match_status_by_id(payload: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for summary in payload.get("summaries") or []:
        if not isinstance(summary, Mapping):
            continue
        for match in summary.get("matches") or []:
            if not isinstance(match, Mapping):
                continue
            match_id = str(match.get("match_id") or "")
            if match_id:
                out[match_id] = str(match.get("status") or "")
    return out


def _parse_bbox(raw: Any) -> Optional[BBox]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        try:
            x0, y0, x1, y1 = [float(value) for value in raw[:4]]
        except Exception:
            return None
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return None


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "region"


__all__ = [
    "REGION_VIEWER_MANIFEST_NAME",
    "export_region_viewer_package",
]
