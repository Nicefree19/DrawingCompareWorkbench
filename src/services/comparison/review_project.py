# -*- coding: utf-8 -*-
"""Review-state and static preview artifacts for drawing change review."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from .base import ComparisonResult
from .change_zones import ChangeZoneOptions, DrawingChangeZone, build_change_zones
from .export_profiles import normalize_export_profile, profile_path_value
from .pair_identity import candidate_display_label, candidate_pair_uuid

REVIEW_STATE_SCHEMA_VERSION = 1
PREVIEW_MANIFEST_SCHEMA_VERSION = 1
REVIEW_PROJECT_SCHEMA_VERSION = 1
REVIEW_STATUSES = {"needs_review", "confirmed", "false_positive", "hold"}
LEGACY_REVIEW_STATUS_ALIASES = {
    "ignored": "hold",
    "pending": "needs_review",
    "review_required": "needs_review",
    "unreviewed": "needs_review",
}


def normalize_review_status(value: Any) -> str:
    """Return the canonical review status used by the MVP review queue."""

    status = str(value or "").strip().lower()
    status = LEGACY_REVIEW_STATUS_ALIASES.get(status, status)
    return status if status in REVIEW_STATUSES else "needs_review"


@dataclass
class ReviewStateRecord:
    """One local review-state row for a change zone."""

    pair_id: str
    zone_id: str
    pair_uuid: str = ""
    status: str = "needs_review"
    note: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def key(self) -> str:
        return review_state_key(self.pair_id, self.zone_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "pair_uuid": self.pair_uuid or self.pair_id,
            "zone_id": self.zone_id,
            "status": normalize_review_status(self.status),
            "note": self.note,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewStateRecord":
        return cls(
            pair_id=str(data.get("pair_id") or ""),
            pair_uuid=str(data.get("pair_uuid") or data.get("pair_id") or ""),
            zone_id=str(data.get("zone_id") or ""),
            status=normalize_review_status(data.get("status")),
            note=str(data.get("note") or ""),
            updated_at=str(data.get("updated_at") or datetime.now().isoformat()),
        )


@dataclass
class ZoneOverlay:
    """Pixel-space overlay metadata for one review zone."""

    pair_id: str
    zone_id: str
    pair_uuid: str = ""
    display_label: str = ""
    drawing_number: str = ""
    change_type: str = "mixed"
    severity: str = "medium"
    status: str = "needs_review"
    raw_change_count: int = 0
    bbox: list[float] = field(default_factory=list)
    old_bbox: Optional[list[float]] = None
    after_bbox_px: Optional[list[float]] = None
    before_bbox_px: Optional[list[float]] = None
    layers: list[str] = field(default_factory=list)
    entity_types: list[str] = field(default_factory=list)
    note: str = ""
    # B안 — entity geometry (CAD-world mm, e.g. {"type": "LINE", "points": [...]})
    # so the live viewer can draw a revision cloud along the actual leader line
    # instead of its bbox. This is the path a fresh DWG/DXF compare uses
    # (DrawingChangeZone → ZoneOverlay → overlay dict), so geometry must be
    # carried here, not only in DrawingChangeZone.to_dict().
    geometry: Optional[dict] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "pair_uuid": self.pair_uuid or self.pair_id,
            "display_label": self.display_label or self.drawing_number or self.pair_id,
            "zone_id": self.zone_id,
            "drawing_number": self.drawing_number,
            "change_type": self.change_type,
            "severity": self.severity,
            "status": self.status,
            "raw_change_count": self.raw_change_count,
            "bbox": self.bbox,
            "old_bbox": self.old_bbox,
            "after_bbox_px": self.after_bbox_px,
            "before_bbox_px": self.before_bbox_px,
            "layers": self.layers,
            "entity_types": self.entity_types,
            "note": self.note,
            "geometry": self.geometry,
        }


@dataclass
class PreviewArtifact:
    """Static before/after preview output for one compared pair."""

    pair_id: str
    pair_uuid: str = ""
    display_label: str = ""
    drawing_number: str = ""
    source_a: str = ""
    source_b: str = ""
    before_image: str = ""
    after_image: str = ""
    before_transform: dict[str, Any] = field(default_factory=dict)
    after_transform: dict[str, Any] = field(default_factory=dict)
    zone_overlays: list[ZoneOverlay] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "pair_uuid": self.pair_uuid or self.pair_id,
            "display_label": self.display_label or self.drawing_number or self.pair_id,
            "drawing_number": self.drawing_number,
            "source_a": self.source_a,
            "source_b": self.source_b,
            "before_image": self.before_image,
            "after_image": self.after_image,
            "before_transform": self.before_transform,
            "after_transform": self.after_transform,
            "zone_overlays": [overlay.to_dict() for overlay in self.zone_overlays],
            "warnings": self.warnings,
        }


@dataclass
class PreviewPackage:
    """Aggregate preview manifest output."""

    output_dir: str
    generated_at: str
    pair_count: int
    preview_count: int
    zone_overlay_count: int
    manifest_path: str
    artifacts: list[PreviewArtifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    max_preview_pairs: Optional[int] = None
    preview_skipped_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PREVIEW_MANIFEST_SCHEMA_VERSION,
            "output_dir": self.output_dir,
            "generated_at": self.generated_at,
            "pair_count": self.pair_count,
            "preview_count": self.preview_count,
            "zone_overlay_count": self.zone_overlay_count,
            "manifest_path": self.manifest_path,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "warnings": self.warnings,
            "max_preview_pairs": self.max_preview_pairs,
            "preview_skipped_count": self.preview_skipped_count,
        }


def review_state_key(pair_id: str, zone_id: str) -> str:
    return f"{pair_id}:{zone_id}"


def load_review_state(path: Union[str, Path, None]) -> dict[str, ReviewStateRecord]:
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {}
    records: dict[str, ReviewStateRecord] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        record = ReviewStateRecord.from_dict(row)
        if record.pair_id and record.zone_id:
            records[record.key] = record
    return records


def save_review_state(
    path: Union[str, Path],
    records: Union[dict[str, ReviewStateRecord], Sequence[ReviewStateRecord]],
) -> Path:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    values = list(records.values()) if isinstance(records, dict) else list(records)
    payload = {
        "schema_version": REVIEW_STATE_SCHEMA_VERSION,
        "updated_at": datetime.now().isoformat(),
        "records": [record.to_dict() for record in sorted(values, key=lambda item: item.key)],
    }
    _write_json_atomic(path, payload)
    return path


def apply_review_state(
    zones: Sequence[DrawingChangeZone],
    records: dict[str, ReviewStateRecord],
) -> None:
    for zone in zones:
        record = records.get(review_state_key(zone.pair_id, zone.zone_id))
        if record:
            zone.status = record.status
            zone.metadata["review_note"] = record.note
            zone.metadata["review_updated_at"] = record.updated_at
        elif not zone.status:
            zone.status = "needs_review"


def collect_review_zones(
    summary: Any,
    *,
    review_records: Optional[dict[str, ReviewStateRecord]] = None,
    zone_options: Optional[ChangeZoneOptions] = None,
) -> dict[str, list[DrawingChangeZone]]:
    """Build zones for completed summary items and apply local review state."""

    records = review_records or {}
    by_pair: dict[str, list[DrawingChangeZone]] = {}
    for index, item in enumerate(getattr(summary, "items", []), start=1):
        if getattr(item, "status", "") != "completed" or not getattr(item, "result", None):
            continue
        candidate = getattr(item, "candidate", None)
        result: ComparisonResult = item.result
        pair_id = pair_id_for_candidate(candidate, index)
        display_label = drawing_label_for_candidate(candidate, pair_id)
        drawing_number = drawing_number_for_candidate(candidate)
        zones = _zones_from_result_metadata(
            result,
            pair_id=pair_id,
            display_label=display_label,
            drawing_number=drawing_number,
        )
        if zones is None:
            zones = build_change_zones(
                result,
                pair_id=pair_id,
                drawing_number=drawing_number,
                options=zone_options,
            )
        for zone in zones:
            zone.pair_uuid = pair_id
            zone.display_label = display_label
            zone.metadata.update(
                {
                    "pair_uuid": pair_id,
                    "display_label": display_label,
                    "source_a": candidate.source_a.path if candidate and candidate.source_a else "",
                    "source_b": candidate.source_b.path if candidate and candidate.source_b else "",
                }
            )
        apply_review_state(zones, records)
        by_pair[pair_id] = zones
    return by_pair


def _zones_from_result_metadata(
    result: ComparisonResult,
    *,
    pair_id: str,
    display_label: str,
    drawing_number: str,
) -> Optional[list[DrawingChangeZone]]:
    """Reuse zones already built by export_change_artifacts.

    FolderComparePipeline exports the main artifacts before preview generation.
    Rebuilding zones from tens of thousands of CAD changes in the preview step
    is pure duplicate work and was a major large-drawing slowdown.
    """

    metadata = getattr(result, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    raw_zones = metadata.get("change_zones")
    if not isinstance(raw_zones, list) or not raw_zones:
        return None
    zones: list[DrawingChangeZone] = []
    try:
        for raw in raw_zones:
            if not isinstance(raw, dict):
                return None
            bbox = _tuple4(raw.get("bbox"))
            centroid = _tuple2(raw.get("centroid"))
            if bbox is None or centroid is None:
                return None
            old_bbox = _tuple4(raw.get("old_bbox"))
            zones.append(
                DrawingChangeZone(
                    zone_id=str(raw.get("zone_id") or ""),
                    pair_id=str(raw.get("pair_id") or pair_id),
                    pair_uuid=str(raw.get("pair_uuid") or pair_id),
                    display_label=str(raw.get("display_label") or display_label),
                    drawing_number=str(raw.get("drawing_number") or drawing_number),
                    change_type=str(raw.get("change_type") or "mixed"),
                    severity=str(raw.get("severity") or "medium"),
                    bbox=bbox,
                    old_bbox=old_bbox,
                    centroid=centroid,
                    raw_change_count=int(raw.get("raw_change_count") or 0),
                    added_count=int(raw.get("added_count") or 0),
                    deleted_count=int(raw.get("deleted_count") or 0),
                    modified_count=int(raw.get("modified_count") or 0),
                    layers=tuple(str(item) for item in (raw.get("layers") or [])),
                    entity_types=tuple(str(item) for item in (raw.get("entity_types") or [])),
                    representative_change_keys=tuple(
                        str(item) for item in (raw.get("representative_change_keys") or [])
                    ),
                    status=str(raw.get("status") or "needs_review"),
                    reasons=tuple(str(item) for item in (raw.get("reasons") or [])),
                    metadata=dict(raw.get("metadata") or {}),
                )
            )
    except (TypeError, ValueError):
        return None
    return zones


def _tuple4(value: Any) -> Optional[tuple[float, float, float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError):
        return None


def _tuple2(value: Any) -> Optional[tuple[float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return None


def export_preview_artifacts(
    summary: Any,
    output_dir: Union[str, Path],
    *,
    dxf_cache_dir: Optional[Union[str, Path]] = None,
    review_state_path: Optional[Union[str, Path]] = None,
    zone_options: Optional[ChangeZoneOptions] = None,
    dpi: int = 80,
    max_edge_px: int = 2400,
    max_preview_pairs: Optional[int] = None,
) -> PreviewPackage:
    """Render static before/after preview images with zone overlay metadata."""

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = output_dir / "preview_images"
    image_dir.mkdir(parents=True, exist_ok=True)

    review_records = load_review_state(review_state_path)
    zones_by_pair = collect_review_zones(
        summary,
        review_records=review_records,
        zone_options=zone_options,
    )

    artifacts: list[PreviewArtifact] = []
    warnings: list[str] = []
    preview_limit = None
    if max_preview_pairs is not None and int(max_preview_pairs) >= 0:
        preview_limit = int(max_preview_pairs)
    rendered_attempts = 0
    skipped_count = 0
    for index, item in enumerate(getattr(summary, "items", []), start=1):
        if getattr(item, "status", "") != "completed" or not getattr(item, "result", None):
            continue
        candidate = getattr(item, "candidate", None)
        pair_id = pair_id_for_candidate(candidate, index)
        display_label = drawing_label_for_candidate(candidate, pair_id)
        artifact = PreviewArtifact(
            pair_id=pair_id,
            pair_uuid=pair_id,
            display_label=display_label,
            drawing_number=drawing_number_for_candidate(candidate),
            source_a=candidate.source_a.path if candidate and candidate.source_a else "",
            source_b=candidate.source_b.path if candidate and candidate.source_b else "",
        )
        zones = zones_by_pair.get(pair_id, [])
        try:
            if preview_limit is not None and rendered_attempts >= preview_limit:
                skipped_count += 1
                message = f"preview skipped by max_preview_pairs={preview_limit}"
                artifact.warnings.append(message)
                warnings.append(f"{pair_id}: {message}")
                artifacts.append(artifact)
                artifact.zone_overlays = [
                    _zone_overlay(zone, artifact.before_transform, artifact.after_transform)
                    for zone in zones
                ]
                continue
            rendered_attempts += 1
            if not candidate or not candidate.source_a or not candidate.source_b:
                raise ValueError("missing source path")
            if candidate.source_a.kind.value != "cad" or candidate.source_b.kind.value != "cad":
                raise ValueError("static preview is CAD-only")
            before_dxf = _ensure_preview_dxf(candidate.source_a.path_obj, dxf_cache_dir)
            after_dxf = _ensure_preview_dxf(candidate.source_b.path_obj, dxf_cache_dir)
            before_image = image_dir / f"{pair_id}_before.png"
            after_image = image_dir / f"{pair_id}_after.png"
            artifact.before_transform = _render_dxf_to_png(
                before_dxf,
                before_image,
                dpi=dpi,
                max_edge_px=max_edge_px,
            )
            artifact.after_transform = _render_dxf_to_png(
                after_dxf,
                after_image,
                dpi=dpi,
                max_edge_px=max_edge_px,
            )
            artifact.before_image = str(before_image)
            artifact.after_image = str(after_image)
        except Exception as exc:
            artifact.warnings.append(str(exc))
            warnings.append(f"{pair_id}: {exc}")

        artifact.zone_overlays = [
            _zone_overlay(zone, artifact.before_transform, artifact.after_transform)
            for zone in zones
        ]
        artifacts.append(artifact)

    manifest_path = output_dir / "preview_manifest.json"
    package = PreviewPackage(
        output_dir=str(output_dir),
        generated_at=datetime.now().isoformat(),
        pair_count=len(artifacts),
        preview_count=sum(1 for artifact in artifacts if artifact.before_image and artifact.after_image),
        zone_overlay_count=sum(len(artifact.zone_overlays) for artifact in artifacts),
        manifest_path=str(manifest_path),
        artifacts=artifacts,
        warnings=warnings,
        max_preview_pairs=preview_limit,
        preview_skipped_count=skipped_count,
    )
    _write_json_atomic(manifest_path, package.to_dict())
    return package


def write_review_project(
    path: Union[str, Path],
    *,
    source_a: Union[str, Path, None] = None,
    source_b: Union[str, Path, None] = None,
    dxf_cache_dir: Union[str, Path, None] = None,
    compare_state_dir: Union[str, Path, None] = None,
    artifact_dir: Union[str, Path, None] = None,
    review_state_path: Union[str, Path, None] = None,
    preview_manifest_path: Union[str, Path, None] = None,
    options: Optional[dict[str, Any]] = None,
    export_profile: str = "internal",
) -> Path:
    path = Path(path).resolve()
    export_profile = normalize_export_profile(export_profile)
    package_root = path.parent
    payload = {
        "schema_version": REVIEW_PROJECT_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(),
        "export_profile": export_profile,
        "source_a": profile_path_value(Path(source_a).resolve() if source_a else "", profile=export_profile, package_root=package_root, sensitive=True),
        "source_b": profile_path_value(Path(source_b).resolve() if source_b else "", profile=export_profile, package_root=package_root, sensitive=True),
        "dxf_cache_dir": profile_path_value(Path(dxf_cache_dir).resolve() if dxf_cache_dir else "", profile=export_profile, package_root=package_root, sensitive=True),
        "compare_state_dir": profile_path_value(Path(compare_state_dir).resolve() if compare_state_dir else "", profile=export_profile, package_root=package_root, sensitive=True),
        "artifact_dir": profile_path_value(Path(artifact_dir).resolve() if artifact_dir else "", profile=export_profile, package_root=package_root),
        "review_state": profile_path_value(Path(review_state_path).resolve() if review_state_path else "", profile=export_profile, package_root=package_root),
        "preview_manifest": profile_path_value(Path(preview_manifest_path).resolve() if preview_manifest_path else "", profile=export_profile, package_root=package_root),
        "options": {**(options or {}), "export_profile": export_profile},
    }
    _write_json_atomic(path, payload)
    return path


def update_artifact_manifest(
    manifest_path: Union[str, Path, None],
    *,
    preview_manifest_path: Union[str, Path, None] = None,
    review_state_path: Union[str, Path, None] = None,
    review_project_path: Union[str, Path, None] = None,
    export_profile: str = "internal",
) -> None:
    if not manifest_path:
        return
    path = Path(manifest_path)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    output_paths = payload.setdefault("output_paths", {})
    export_profile = normalize_export_profile(export_profile)
    package_root = path.parent
    payload["export_profile"] = export_profile
    if preview_manifest_path:
        payload["preview_manifest"] = profile_path_value(Path(preview_manifest_path).resolve(), profile=export_profile, package_root=package_root)
        output_paths["preview_manifest_json"] = payload["preview_manifest"]
    if review_state_path:
        payload["review_state"] = profile_path_value(Path(review_state_path).resolve(), profile=export_profile, package_root=package_root)
        output_paths["review_state_json"] = payload["review_state"]
    if review_project_path:
        payload["review_project"] = profile_path_value(Path(review_project_path).resolve(), profile=export_profile, package_root=package_root)
        output_paths["review_project_json"] = payload["review_project"]
    _write_json_atomic(path, payload)


def pair_id_for_candidate(candidate: Any, index: int = 1) -> str:
    if candidate:
        try:
            return candidate_pair_uuid(candidate)
        except Exception:
            pass
    return f"pair_{index:03d}"


def drawing_label_for_candidate(candidate: Any, fallback: str = "") -> str:
    if candidate:
        return candidate_display_label(candidate, fallback or "pair")
    return fallback


def drawing_number_for_candidate(candidate: Any) -> str:
    for descriptor in (getattr(candidate, "source_b", None), getattr(candidate, "source_a", None)):
        identity = getattr(descriptor, "identity", None)
        if identity and identity.drawing_number:
            return str(identity.drawing_number)
    return ""


def _zone_overlay(
    zone: DrawingChangeZone,
    before_transform: dict[str, Any],
    after_transform: dict[str, Any],
) -> ZoneOverlay:
    record_status = normalize_review_status(zone.status)
    note = str(zone.metadata.get("review_note", ""))
    return ZoneOverlay(
        pair_id=zone.pair_id,
        zone_id=zone.zone_id,
        pair_uuid=zone.pair_uuid or zone.pair_id,
        display_label=zone.display_label or zone.drawing_number or zone.pair_id,
        drawing_number=zone.drawing_number,
        change_type=zone.change_type,
        severity=zone.severity,
        status=record_status,
        raw_change_count=zone.raw_change_count,
        bbox=[float(value) for value in zone.bbox],
        old_bbox=[float(value) for value in zone.old_bbox] if zone.old_bbox else None,
        after_bbox_px=_bbox_to_pixel_bbox(zone.bbox, after_transform) if after_transform else None,
        before_bbox_px=_bbox_to_pixel_bbox(zone.old_bbox or zone.bbox, before_transform)
        if before_transform
        else None,
        layers=list(zone.layers),
        entity_types=list(zone.entity_types),
        note=note,
        geometry=zone.geometry,
    )


def _bbox_to_pixel_bbox(
    bbox: Sequence[float],
    transform: dict[str, Any],
) -> list[float]:
    if str(transform.get("coordinate_space") or "").lower() == "image_pixels":
        width = float(transform.get("img_width", 0.0))
        height = float(transform.get("img_height", 0.0))
        left = max(0.0, min(width, min(float(bbox[0]), float(bbox[2]))))
        right = max(0.0, min(width, max(float(bbox[0]), float(bbox[2]))))
        top = max(0.0, min(height, min(float(bbox[1]), float(bbox[3]))))
        bottom = max(0.0, min(height, max(float(bbox[1]), float(bbox[3]))))
        return [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)]
    min_x = float(transform.get("min_x", 0.0))
    min_y = float(transform.get("min_y", 0.0))
    scale_x = float(transform.get("scale_x", 1.0))
    scale_y = float(transform.get("scale_y", 1.0))
    height = float(transform.get("img_height", 0.0))
    width = float(transform.get("img_width", 0.0))
    x1 = (float(bbox[0]) - min_x) * scale_x
    x2 = (float(bbox[2]) - min_x) * scale_x
    y1 = height - ((float(bbox[3]) - min_y) * scale_y)
    y2 = height - ((float(bbox[1]) - min_y) * scale_y)
    left = max(0.0, min(width, min(x1, x2)))
    right = max(0.0, min(width, max(x1, x2)))
    top = max(0.0, min(height, min(y1, y2)))
    bottom = max(0.0, min(height, max(y1, y2)))
    return [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)]


def _ensure_preview_dxf(
    path: Path,
    dxf_cache_dir: Optional[Union[str, Path]],
) -> Path:
    if path.suffix.lower() == ".dxf":
        return path
    from .dwg_differ import DwgDiffer

    differ = DwgDiffer(dxf_cache_dir=dxf_cache_dir)
    try:
        return differ._ensure_dxf(path)
    finally:
        differ._cleanup_temp()


def _render_dxf_to_png(
    dxf_path: Path,
    output_path: Path,
    *,
    dpi: int,
    max_edge_px: int,
) -> dict[str, Any]:
    from .dxf_renderer import DxfRenderer

    renderer = DxfRenderer(dpi=dpi)
    image, transform = renderer.render_with_transform(
        dxf_path,
        dpi=dpi,
        max_edge_px=max_edge_px,
    )
    _write_rgb_png(output_path, image)
    return transform


def _write_rgb_png(path: Path, image: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        Image.fromarray(image).save(path)
        return
    except Exception:
        pass
    try:
        import cv2

        cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        return
    except Exception as exc:
        raise RuntimeError("PIL or OpenCV is required to write preview PNG files") from exc


def _safe_stem(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return safe.strip("._") or "drawing"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    # Sanitize lone surrogate codepoints (Korean filenames from Windows can
    # carry CP949↔UTF-16 leftovers that crash utf-8 encode here). Surfaces
    # in the GUI as "선택 구역 렌더 실패 ... surrogates not allowed".
    from .safe_unicode import safe_unicode

    try:
        temp_path.write_text(
            json.dumps(safe_unicode(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
