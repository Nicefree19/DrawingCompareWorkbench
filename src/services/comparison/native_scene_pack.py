"""Native CAD scene-pack contract for bridge-provided CAD semantics.

The contract is deliberately independent from any DWG SDK. A bridge process can
emit this JSON shape, and the Python side can validate, serialize, and map it
into existing viewer primitive payloads without claiming new DWG support.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


NATIVE_SCENE_PACK_SCHEMA_VERSION = "native-scene-pack/v1"
BRIDGE_RESULT_SCHEMA_VERSION = "native-cad-bridge-result/v1"
NATIVE_CAD_VIEWER_EVIDENCE_SCHEMA_VERSION = "native-cad-viewer-evidence/v1"

BBox = tuple[float, float, float, float]


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _bbox(value: object) -> BBox:
    items = _list(value)
    if len(items) >= 4:
        try:
            return (float(items[0]), float(items[1]), float(items[2]), float(items[3]))
        except (TypeError, ValueError):
            pass
    return (0.0, 0.0, 0.0, 0.0)


def source_signature(path: str | Path | None) -> dict[str, Any]:
    """Return stable source metadata without reading whole huge files eagerly."""

    if path is None:
        return {"path": "", "exists": False, "size": 0, "sha256": ""}
    candidate = Path(path)
    if not candidate.exists() or not candidate.is_file():
        return {"path": str(candidate), "exists": False, "size": 0, "sha256": ""}
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(candidate),
        "exists": True,
        "size": candidate.stat().st_size,
        "sha256": digest.hexdigest(),
    }


@dataclass(frozen=True)
class NativeScenePack:
    """Versioned native CAD scene graph plus viewer-compatible primitives."""

    source: dict[str, Any]
    adapter: dict[str, Any]
    layouts: list[dict[str, Any]] = field(default_factory=list)
    layers: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    display_primitives: list[dict[str, Any]] = field(default_factory=list)
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    text_runs: list[dict[str, Any]] = field(default_factory=list)
    xrefs: list[dict[str, Any]] = field(default_factory=list)
    coordinate_spaces: dict[str, Any] = field(default_factory=dict)
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = NATIVE_SCENE_PACK_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NativeScenePack":
        """Parse a scene pack while tolerating unknown future fields."""

        source = _dict(payload.get("source"))
        adapter = _dict(payload.get("adapter"))
        metadata = _dict(payload.get("metadata"))
        unknown = {
            key: copy.deepcopy(value)
            for key, value in payload.items()
            if key
            not in {
                "schema_version",
                "source",
                "adapter",
                "layouts",
                "layers",
                "blocks",
                "entities",
                "display_primitives",
                "dimensions",
                "text_runs",
                "xrefs",
                "coordinate_spaces",
                "bbox",
                "warnings",
                "metadata",
            }
        }
        if unknown:
            metadata.setdefault("unknown_fields", unknown)
        return cls(
            schema_version=str(payload.get("schema_version") or NATIVE_SCENE_PACK_SCHEMA_VERSION),
            source=source,
            adapter=adapter,
            layouts=[_dict(item) for item in _list(payload.get("layouts"))],
            layers=[_dict(item) for item in _list(payload.get("layers"))],
            blocks=[_dict(item) for item in _list(payload.get("blocks"))],
            entities=[_dict(item) for item in _list(payload.get("entities"))],
            display_primitives=[_dict(item) for item in _list(payload.get("display_primitives"))],
            dimensions=[_dict(item) for item in _list(payload.get("dimensions"))],
            text_runs=[_dict(item) for item in _list(payload.get("text_runs"))],
            xrefs=[_dict(item) for item in _list(payload.get("xrefs"))],
            coordinate_spaces=_dict(payload.get("coordinate_spaces")),
            bbox=_bbox(payload.get("bbox")),
            warnings=[_dict(item) for item in _list(payload.get("warnings"))],
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": copy.deepcopy(self.source),
            "adapter": copy.deepcopy(self.adapter),
            "layouts": copy.deepcopy(self.layouts),
            "layers": copy.deepcopy(self.layers),
            "blocks": copy.deepcopy(self.blocks),
            "entities": copy.deepcopy(self.entities),
            "display_primitives": copy.deepcopy(self.display_primitives),
            "dimensions": copy.deepcopy(self.dimensions),
            "text_runs": copy.deepcopy(self.text_runs),
            "xrefs": copy.deepcopy(self.xrefs),
            "coordinate_spaces": copy.deepcopy(self.coordinate_spaces),
            "bbox": list(self.bbox),
            "warnings": copy.deepcopy(self.warnings),
            "metadata": copy.deepcopy(self.metadata),
        }

    def overview_lod0_payload(self) -> dict[str, Any]:
        """Return the shape consumed by the existing lightweight viewport."""

        return {
            "schema_version": "overview-lod0/v1",
            "source_kind": "native_cad",
            "world_bbox": list(self.bbox),
            "primitive_count": len(self.display_primitives),
            "primitives": copy.deepcopy(self.display_primitives),
            "native_scene_pack": {
                "schema_version": self.schema_version,
                "adapter": copy.deepcopy(self.adapter),
                "source": copy.deepcopy(self.source),
            },
        }


def bridge_payload_from_scene_pack(
    scene_pack: NativeScenePack,
    *,
    drawing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": BRIDGE_RESULT_SCHEMA_VERSION,
        "scene_pack": scene_pack.to_dict(),
    }
    if drawing is not None:
        payload["drawing"] = copy.deepcopy(drawing)
    return payload


def write_native_scene_pack_artifacts(
    scene_pack: NativeScenePack,
    output_dir: str | Path,
) -> "ScenePackRef":
    """Persist native pack artifacts and return a viewer ``ScenePackRef``."""

    from src.services.comparison.viewer_manifest_v3 import ScenePackRef

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    pack_path = target_dir / "native_scene_pack.json"
    overview_path = target_dir / "overview_lod0.json"
    pack_path.write_text(
        json.dumps(scene_pack.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    overview_path.write_text(
        json.dumps(scene_pack.overview_lod0_payload(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ScenePackRef(
        json_path=str(pack_path),
        overview_lod0_path=str(overview_path),
        primitive_count=len(scene_pack.display_primitives),
        drawing_world_bbox=scene_pack.bbox,
        notes="native_scene_pack",
    )


def native_scene_viewer_evidence_payload(
    scene_pack: NativeScenePack | Mapping[str, Any],
    *,
    change_overlays: Sequence[Mapping[str, Any]] = (),
    to_world: Any | None = None,
    import_report: Mapping[str, Any] | None = None,
    tile_manifest: Mapping[str, Any] | None = None,
    primitive_budget: int = 5000,
    payload_byte_budget: int = 2_000_000,
) -> dict[str, Any]:
    """Build the headless evidence packet consumed by native-CAD viewer checks."""

    pack = scene_pack if isinstance(scene_pack, NativeScenePack) else NativeScenePack.from_dict(scene_pack)
    overview = pack.overview_lod0_payload()
    overview_payload_bytes = len(
        json.dumps(overview, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    overlays = [_dict(item) for item in _list(change_overlays)]
    frame_status: dict[str, Any] = {
        "status": "no_change_overlays" if not overlays else "no_usable_change_bbox",
        "world_bbox": None,
        "frame_source": "content_frame_from_zone_bboxes",
    }
    if overlays:
        try:
            from .content_frame import content_frame_from_zone_bboxes
            from .transform import normalise_bbox

            def _default_to_world(raw: Any, _space: str, _dpi: float):
                return normalise_bbox(raw)

            frame = content_frame_from_zone_bboxes(overlays, to_world or _default_to_world)
            if frame is not None:
                frame_status.update({"status": "framed", "world_bbox": list(frame)})
        except Exception as exc:  # noqa: BLE001 - evidence should fail closed.
            frame_status.update(
                {
                    "status": "frame_error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    payload: dict[str, Any] = {
        "schema_version": NATIVE_CAD_VIEWER_EVIDENCE_SCHEMA_VERSION,
        "source_kind": "native_cad",
        "overview_lod0": overview,
        "viewer": {
            "world_bbox": copy.deepcopy(overview["world_bbox"]),
            "primitive_count": overview["primitive_count"],
            "bounded_payload": True,
            "primitive_budget": int(primitive_budget),
            "within_primitive_budget": int(overview["primitive_count"]) <= int(primitive_budget),
            "overview_payload_bytes": overview_payload_bytes,
            "payload_byte_budget": int(payload_byte_budget),
            "within_payload_byte_budget": overview_payload_bytes <= int(payload_byte_budget),
        },
        "change_overlay_count": len(overlays),
        "primary_change_frame": frame_status,
        "native_scene_pack": copy.deepcopy(overview["native_scene_pack"]),
    }
    if import_report is not None:
        payload["import_report"] = _import_report_evidence(import_report)
    if tile_manifest is not None:
        payload["tile_manifest"] = _tile_manifest_evidence(tile_manifest)
    return payload


def _import_report_evidence(report: Mapping[str, Any]) -> dict[str, Any]:
    warning = _dict((_list(report.get("warnings")) or [{}])[0])
    details = _dict(warning.get("details"))
    return {
        "status": report.get("status"),
        "error_code": report.get("error_code"),
        "adapter": _dict(report.get("adapter")).get("name"),
        "backend_mode": _dict(report.get("adapter")).get("backend_mode"),
        "implementation_status": _dict(report.get("adapter")).get("implementation_status"),
        "failure_stage": details.get("failure_stage"),
        "reader_error_type": details.get("reader_error_type"),
        "object_handle": details.get("object_handle"),
        "object_offset": details.get("object_offset"),
        "object_payload_prefix_hex": details.get("object_payload_prefix_hex"),
    }


def _tile_manifest_evidence(manifest: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_version",
        "status",
        "generation_mode",
        "tile_count",
        "materialized_tile_count",
        "planned_tile_count",
        "overlay_tile_count",
        "cache_total_estimated_bytes",
        "cache_byte_limit",
        "pyramid_complete",
        "deferred_lod_tiles",
    )
    return {key: copy.deepcopy(manifest.get(key)) for key in keys if key in manifest}


__all__ = [
    "BRIDGE_RESULT_SCHEMA_VERSION",
    "NATIVE_CAD_VIEWER_EVIDENCE_SCHEMA_VERSION",
    "NATIVE_SCENE_PACK_SCHEMA_VERSION",
    "NativeScenePack",
    "bridge_payload_from_scene_pack",
    "native_scene_viewer_evidence_payload",
    "source_signature",
    "write_native_scene_pack_artifacts",
]
