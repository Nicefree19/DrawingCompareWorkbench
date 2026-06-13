# -*- coding: utf-8 -*-
"""Primitive-source contract for lightweight CAD viewer first paint.

The viewport consumes a small ``overview_lod0.json`` payload regardless of
which producer built it.  This module owns the common source selection,
provenance, and render-contract version so native scene packs, ezdxf scene
packs, and explicit fail-closed fallbacks do not grow separate ladders.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.services.comparison.source_signature import build_source_signature
from src.services.comparison.viewer_manifest_v3 import ScenePackRef

RENDER_CONTRACT_VERSION = "r3"
OVERVIEW_LOD0_SCHEMA_VERSION = "overview-lod0/v1"

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class ViewerPrimitiveSource:
    """Resolved primitive source for the lightweight viewport."""

    ok: bool
    primitives: list[dict[str, Any]] = field(default_factory=list)
    world_bbox: BBox = (0.0, 0.0, 1.0, 1.0)
    provenance: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    render_mode: str = "relative_only"
    status_text: str = ""
    empty_notice: str = ""
    error_code: str = ""
    degraded: bool = False
    overview_lod0_path: str = ""


def render_contract_schema_version(version: str = RENDER_CONTRACT_VERSION) -> int:
    """Return the numeric schema version represented by ``rN``."""

    token = str(version or "").strip().lower()
    if token.startswith("r"):
        token = token[1:]
    try:
        return max(1, int(token))
    except ValueError:
        return 1


def primitive_source_provenance(
    producer_id: str,
    *,
    source_path: str | Path | None = None,
    overview_lod0_path: str | Path | None = None,
    scene_pack_path: str | Path | None = None,
    index_path: str | Path | None = None,
    render_mode: str = "",
    degraded: bool = False,
    fallback_code: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable provenance block for any viewer primitive source."""

    signature_path = source_path or overview_lod0_path or scene_pack_path
    provenance: dict[str, Any] = {
        "producer_id": str(producer_id or "unknown"),
        "render_contract_version": RENDER_CONTRACT_VERSION,
        "render_contract_schema_version": render_contract_schema_version(),
        "render_mode": str(render_mode or ""),
        "degraded": bool(degraded),
        "fallback_code": str(fallback_code or ""),
        "source_signature": build_source_signature(
            signature_path,
            render_backend_id=RENDER_CONTRACT_VERSION,
        ),
        "overview_lod0_signature": build_source_signature(
            overview_lod0_path,
            render_backend_id=RENDER_CONTRACT_VERSION,
        ),
        "paths": {
            "source_path": str(source_path or ""),
            "overview_lod0_path": str(overview_lod0_path or ""),
            "scene_pack_path": str(scene_pack_path or ""),
            "index_path": str(index_path or ""),
        },
    }
    if fallback_code:
        provenance["failure_badge"] = str(fallback_code)
    if extra:
        provenance.update(copy.deepcopy(dict(extra)))
    return provenance


def resolve_viewer_primitive_source(
    pack_ref: ScenePackRef | None,
    *,
    empty_notice: str = "",
) -> ViewerPrimitiveSource:
    """Resolve a ``ScenePackRef`` into the single viewport primitive contract."""

    if pack_ref is None or not pack_ref.overview_lod0_path:
        provenance = primitive_source_provenance(
            "relative_only",
            render_mode="relative_only",
            degraded=True,
            fallback_code="NO_SCENE_PACK",
        )
        notice = empty_notice or "Vector preview is not available."
        return ViewerPrimitiveSource(
            ok=False,
            provenance=provenance,
            render_mode="relative_only",
            status_text=notice,
            empty_notice=notice,
            error_code="NO_SCENE_PACK",
            degraded=True,
        )

    overview_path = Path(pack_ref.overview_lod0_path)
    if not overview_path.exists():
        provenance = primitive_source_provenance(
            "overview_lod0_missing",
            overview_lod0_path=overview_path,
            scene_pack_path=pack_ref.json_path,
            index_path=pack_ref.index_path,
            render_mode="render_failed",
            degraded=True,
            fallback_code="OVERVIEW_LOD0_MISSING",
        )
        return ViewerPrimitiveSource(
            ok=False,
            provenance=provenance,
            render_mode="render_failed",
            status_text="Vector preview data has not been built.",
            empty_notice="Vector preview data has not been built.",
            error_code="OVERVIEW_LOD0_MISSING",
            degraded=True,
            overview_lod0_path=str(overview_path),
        )

    try:
        payload = json.loads(overview_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        provenance = primitive_source_provenance(
            "overview_lod0_read_failed",
            overview_lod0_path=overview_path,
            scene_pack_path=pack_ref.json_path,
            index_path=pack_ref.index_path,
            render_mode="render_failed",
            degraded=True,
            fallback_code="OVERVIEW_LOD0_READ_FAILED",
            extra={"error_type": type(exc).__name__, "error": str(exc)},
        )
        return ViewerPrimitiveSource(
            ok=False,
            provenance=provenance,
            render_mode="render_failed",
            status_text=f"Vector preview data load failed: {exc}",
            empty_notice=f"Vector preview data load failed:\n{exc}",
            error_code="OVERVIEW_LOD0_READ_FAILED",
            degraded=True,
            overview_lod0_path=str(overview_path),
        )

    data = payload if isinstance(payload, Mapping) else {}
    producer_id = _producer_id(data)
    source_path = _payload_source_path(data)
    primitives = _primitive_dicts(data.get("primitives"))
    world_bbox = _coerce_bbox(
        data.get("world_bbox"),
        fallback=pack_ref.drawing_world_bbox,
    )
    provenance = primitive_source_provenance(
        producer_id,
        source_path=source_path,
        overview_lod0_path=overview_path,
        scene_pack_path=pack_ref.json_path,
        index_path=pack_ref.index_path,
        render_mode="skeleton_preview",
        degraded=False,
        extra={
            "source_kind": str(data.get("source_kind") or ""),
            "primitive_count": len(primitives),
        },
    )
    resolved_payload = copy.deepcopy(dict(data))
    resolved_payload.setdefault("schema_version", OVERVIEW_LOD0_SCHEMA_VERSION)
    resolved_payload["render_contract_version"] = RENDER_CONTRACT_VERSION
    resolved_payload["primitive_source_provenance"] = copy.deepcopy(provenance)
    return ViewerPrimitiveSource(
        ok=True,
        primitives=primitives,
        world_bbox=world_bbox,
        provenance=provenance,
        payload=resolved_payload,
        render_mode="skeleton_preview",
        status_text=_status_text_for(producer_id),
        empty_notice="",
        error_code="",
        degraded=False,
        overview_lod0_path=str(overview_path),
    )


def _producer_id(payload: Mapping[str, Any]) -> str:
    source_kind = str(payload.get("source_kind") or "").strip().lower()
    if source_kind == "native_cad" or isinstance(payload.get("native_scene_pack"), Mapping):
        return "native_scene_pack"
    if source_kind in {"converted_dxf", "normalized_dxf"}:
        return "ezdxf_scene_pack"
    if "format_version" in payload or "source_path" in payload:
        return "ezdxf_scene_pack"
    return "overview_lod0"


def _payload_source_path(payload: Mapping[str, Any]) -> str:
    native_pack = payload.get("native_scene_pack")
    if isinstance(native_pack, Mapping):
        source = native_pack.get("source")
        if isinstance(source, Mapping):
            path = source.get("path") or source.get("source_path")
            if path:
                return str(path)
    source_path = payload.get("source_path")
    return str(source_path or "")


def _primitive_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _coerce_bbox(value: object, *, fallback: object = None) -> BBox:
    for candidate in (value, fallback):
        bbox = _try_bbox(candidate)
        if bbox is not None:
            return bbox
    return (0.0, 0.0, 1.0, 1.0)


def _try_bbox(value: object) -> BBox | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if len(value) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    width = x1 - x0
    height = y1 - y0
    if width <= 0.0 and height <= 0.0:
        x0 -= 0.5
        x1 += 0.5
        y0 -= 0.5
        y1 += 0.5
    elif width <= 0.0:
        pad = max(height * 0.01, 0.5)
        x0 -= pad
        x1 += pad
    elif height <= 0.0:
        pad = max(width * 0.01, 0.5)
        y0 -= pad
        y1 += pad
    return (x0, y0, x1, y1)


def _status_text_for(producer_id: str) -> str:
    if producer_id == "native_scene_pack":
        return "NativeScenePack preview"
    if producer_id == "ezdxf_scene_pack":
        return "Scene-pack preview"
    return "Overview preview"


__all__ = [
    "BBox",
    "OVERVIEW_LOD0_SCHEMA_VERSION",
    "RENDER_CONTRACT_VERSION",
    "ViewerPrimitiveSource",
    "primitive_source_provenance",
    "render_contract_schema_version",
    "resolve_viewer_primitive_source",
]
