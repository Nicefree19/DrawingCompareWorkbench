# -*- coding: utf-8 -*-
"""Viewer manifest v3 — diff-steered scene-pack-driven schema.

Phase G evolution of v2. The major shift:

* v2 stored ``before/after`` as raster artifacts with affine transforms.
* v3 stores ``ScenePackRef`` per-source (one per drawing), plus
  ``ZoneRequestRef`` / ``EvidenceRef`` per-zone. The viewer composes the
  display from these references on demand instead of consuming a pre-baked
  background per pair.

Schema sketch (full table in plan §1.7 + 보고서 §6):

    {
      "schema_version": "viewer_manifest.v3",
      "pair_uuid": "...",
      "package_version": "...",
      "source_kind": "normalized_dxf | pdf | mixed",
      "before_source_signature": SourceSignature,
      "after_source_signature":  SourceSignature,
      "compare_sig": "...",
      "renderer_capabilities": { ... },
      "coordinate_space": "world_xy_2d",
      "before_world_bbox": [...],
      "after_world_bbox":  [...],
      "shared_world_bbox": [...],
      "alignment_before_to_shared": [a,b,c,d,e,f],
      "alignment_after_to_shared":  [a,b,c,d,e,f],
      "overlay_space": "world | relative_only",
      "default_focus_padding_world": 1500.0,
      "before_scene_pack": ScenePackRef|None,
      "after_scene_pack":  ScenePackRef|None,
      "zone_requests": [ZoneRequestRef, ...],   # populated lazily
      "evidence": [EvidenceRef, ...],            # raster crops; lazy
      "current_render_mode": RenderMode,         # 7-state
      "font_signature": "...",
      "dependency_signature": "...",
      "created_at_utc": "..."
    }

The module is **pure Python + json + dataclasses** (imports
``render_modes`` for enum validation only). Validation is strict — a v3
manifest carrying an unknown render mode raises rather than silently
showing an ambiguous UI state.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, List, Literal, Optional, Tuple

from src.services.comparison.render_modes import (
    ALL_RENDER_MODES,
    RENDER_MODE_STYLES,
    RenderMode,
    is_valid_mode,
)
from src.services.comparison.transform import (
    Affine6,
    Bbox,
    COORDINATE_CONTRACT_VERSION,
    COORD_CAD_WCS_MM,
    SOURCE_TRUTH_CAD_ENTITY,
    Y_AXIS_UP,
    coordinate_space_y_axis,
    normalize_coordinate_space,
    source_truth_for_coordinate_space,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION: Final[str] = "viewer_manifest.v3"
MANIFEST_FILENAME: Final[str] = "viewer_manifest_v3.json"
COORDINATE_SPACE: Final[str] = "world_xy_2d"

#: Source kind enum (re-used from v2 — kept stable so v2/v3 callers agree).
SourceKind = Literal["normalized_dxf", "pdf", "mixed"]
OverlaySpace = Literal["world", "relative_only"]

_VALID_SOURCE_KIND: Final[frozenset[str]] = frozenset({"normalized_dxf", "pdf", "mixed"})
_VALID_OVERLAY_SPACE: Final[frozenset[str]] = frozenset({"world", "relative_only"})

#: Identity affine for ``relative_only`` artifacts that have no real transform.
IDENTITY_AFFINE: Final[Affine6] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


class ManifestV3ValidationError(ValueError):
    """Raised when a v3 manifest fails schema validation."""


# ---------------------------------------------------------------------------
# Sub-records
# ---------------------------------------------------------------------------


@dataclass
class SourceSignature:
    """Fingerprint of one source drawing — used as cache invalidation key."""

    source_path: str = ""
    file_hash: str = ""              # sha256 of the file
    source_hash: str = ""            # lightweight path/size/mtime source identity
    file_size: int = 0
    mtime_ns: int = 0
    signature_schema_version: str = ""
    dxf_version: str = ""            # e.g. "AC1027"
    font_sig: str = ""               # sha256 over resolved font paths
    backend_sig: str = ""            # "ezdxf-1.4.3|qt-6.10|qtpdf-6.10"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "SourceSignature":
        if not isinstance(data, dict):
            return cls()
        return cls(
            source_path=str(data.get("source_path", "")),
            file_hash=str(data.get("file_hash", "")),
            source_hash=str(data.get("source_hash", "")),
            file_size=_safe_int(data.get("file_size")),
            mtime_ns=_safe_int(data.get("mtime_ns")),
            signature_schema_version=str(data.get("signature_schema_version", "")),
            dxf_version=str(data.get("dxf_version", "")),
            font_sig=str(data.get("font_sig", "")),
            backend_sig=str(data.get("backend_sig", "")),
        )


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class ScenePackRef:
    """Pointer to one drawing's ScenePack on disk + summary stats.

    The pack itself lives at ``json_path``; the spatial index at
    ``index_path``; an optional skeleton-LOD subset at ``overview_lod0_path``
    (used as the immediate first paint on pair selection).
    """

    json_path: str = ""
    index_path: str = ""
    overview_lod0_path: str = ""
    primitive_count: int = 0
    drawing_world_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    elapsed_build_ms: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "json_path": self.json_path,
            "index_path": self.index_path,
            "overview_lod0_path": self.overview_lod0_path,
            "primitive_count": int(self.primitive_count),
            "drawing_world_bbox": list(self.drawing_world_bbox),
            "elapsed_build_ms": float(self.elapsed_build_ms),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ScenePackRef":
        if not isinstance(data, dict):
            return cls()
        bbox_raw = data.get("drawing_world_bbox") or (0.0, 0.0, 0.0, 0.0)
        try:
            bbox: Bbox = (
                float(bbox_raw[0]),
                float(bbox_raw[1]),
                float(bbox_raw[2]),
                float(bbox_raw[3]),
            )
        except (TypeError, ValueError, IndexError):
            bbox = (0.0, 0.0, 0.0, 0.0)
        return cls(
            json_path=str(data.get("json_path", "")),
            index_path=str(data.get("index_path", "")),
            overview_lod0_path=str(data.get("overview_lod0_path", "")),
            primitive_count=int(data.get("primitive_count", 0) or 0),
            drawing_world_bbox=bbox,
            elapsed_build_ms=float(data.get("elapsed_build_ms", 0.0) or 0.0),
            notes=str(data.get("notes", "")),
        )


@dataclass
class ZoneRequestRef:
    """Per-zone render request descriptor — stored in the manifest so the
    GUI can resume / debug / replay requests. Updated as the worker pool
    progresses.
    """

    zone_id: str = ""
    side: Literal["before", "after"] = "after"
    bbox_world: Bbox = (0.0, 0.0, 0.0, 0.0)
    coordinate_contract_version: str = COORDINATE_CONTRACT_VERSION
    bbox_coordinate_space: str = COORD_CAD_WCS_MM
    source_truth: str = SOURCE_TRUTH_CAD_ENTITY
    y_axis: str = Y_AXIS_UP
    pad_world: float = 0.0
    target_px_w: int = 0
    target_px_h: int = 0
    lod: int = 0
    layer_mask_hash: str = ""
    view_theme: str = "light"
    cache_key: str = ""              # SHA-256 derived from above fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "side": self.side,
            "bbox_world": list(self.bbox_world),
            "coordinate_contract_version": self.coordinate_contract_version,
            "bbox_coordinate_space": normalize_coordinate_space(self.bbox_coordinate_space),
            "source_truth": _effective_source_truth(
                self.source_truth,
                normalize_coordinate_space(self.bbox_coordinate_space),
            ),
            "y_axis": _effective_y_axis(
                self.y_axis,
                normalize_coordinate_space(self.bbox_coordinate_space),
            ),
            "pad_world": float(self.pad_world),
            "target_px_w": int(self.target_px_w),
            "target_px_h": int(self.target_px_h),
            "lod": int(self.lod),
            "layer_mask_hash": self.layer_mask_hash,
            "view_theme": self.view_theme,
            "cache_key": self.cache_key,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ZoneRequestRef":
        if not isinstance(data, dict):
            return cls()
        bbox_raw = data.get("bbox_world") or (0.0, 0.0, 0.0, 0.0)
        try:
            bbox: Bbox = (
                float(bbox_raw[0]),
                float(bbox_raw[1]),
                float(bbox_raw[2]),
                float(bbox_raw[3]),
            )
        except (TypeError, ValueError, IndexError):
            bbox = (0.0, 0.0, 0.0, 0.0)
        side_raw = str(data.get("side", "after"))
        side: Literal["before", "after"] = side_raw if side_raw in {"before", "after"} else "after"  # type: ignore[assignment]
        bbox_space = normalize_coordinate_space(
            data.get("bbox_coordinate_space", data.get("coordinate_space", COORD_CAD_WCS_MM))
        )
        return cls(
            zone_id=str(data.get("zone_id", "")),
            side=side,
            bbox_world=bbox,
            coordinate_contract_version=str(
                data.get("coordinate_contract_version", COORDINATE_CONTRACT_VERSION)
            ),
            bbox_coordinate_space=bbox_space,
            source_truth=_effective_source_truth(data.get("source_truth"), bbox_space),
            y_axis=_effective_y_axis(data.get("y_axis"), bbox_space),
            pad_world=float(data.get("pad_world", 0.0) or 0.0),
            target_px_w=int(data.get("target_px_w", 0) or 0),
            target_px_h=int(data.get("target_px_h", 0) or 0),
            lod=int(data.get("lod", 0) or 0),
            layer_mask_hash=str(data.get("layer_mask_hash", "")),
            view_theme=str(data.get("view_theme", "light")),
            cache_key=str(data.get("cache_key", "")),
        )


@dataclass
class EvidenceRef:
    """Pointer to one rendered raster crop — the `raster_refined` evidence layer.

    Exists only when a worker has actually completed a raster render for the
    matching ZoneRequestRef. The viewer fades this in over the vector layer
    when present.
    """

    zone_id: str = ""
    side: Literal["before", "after"] = "after"
    raster_uri: str = ""
    world_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    pixel_size: Tuple[int, int] = (0, 0)
    world_to_pixel: Affine6 = IDENTITY_AFFINE
    pixel_to_world: Affine6 = IDENTITY_AFFINE
    transform_quality: Literal["exact", "estimated", "relative_only"] = "exact"
    coordinate_contract_version: str = COORDINATE_CONTRACT_VERSION
    bbox_coordinate_space: str = COORD_CAD_WCS_MM
    source_truth: str = SOURCE_TRUTH_CAD_ENTITY
    y_axis: str = Y_AXIS_UP
    render_ms: float = 0.0
    cache_hit: bool = False
    request_cache_key: str = ""      # link back to ZoneRequestRef.cache_key
    visual_fidelity: str = ""
    render_lifecycle: str = ""
    fallback_reason_code: str = ""
    warnings: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "side": self.side,
            "raster_uri": self.raster_uri,
            "world_bbox": list(self.world_bbox),
            "pixel_size": list(self.pixel_size),
            "world_to_pixel": list(self.world_to_pixel),
            "pixel_to_world": list(self.pixel_to_world),
            "transform_quality": self.transform_quality,
            "coordinate_contract_version": self.coordinate_contract_version,
            "bbox_coordinate_space": normalize_coordinate_space(self.bbox_coordinate_space),
            "source_truth": _effective_source_truth(
                self.source_truth,
                normalize_coordinate_space(self.bbox_coordinate_space),
            ),
            "y_axis": _effective_y_axis(
                self.y_axis,
                normalize_coordinate_space(self.bbox_coordinate_space),
            ),
            "render_ms": float(self.render_ms),
            "cache_hit": bool(self.cache_hit),
            "request_cache_key": self.request_cache_key,
            "visual_fidelity": self.visual_fidelity,
            "render_lifecycle": self.render_lifecycle,
            "fallback_reason_code": self.fallback_reason_code,
            "warnings": list(self.warnings),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "EvidenceRef":
        if not isinstance(data, dict):
            return cls()
        bbox_raw = data.get("world_bbox") or (0.0, 0.0, 0.0, 0.0)
        ps_raw = data.get("pixel_size") or (0, 0)
        try:
            bbox: Bbox = (
                float(bbox_raw[0]), float(bbox_raw[1]),
                float(bbox_raw[2]), float(bbox_raw[3]),
            )
        except (TypeError, ValueError, IndexError):
            bbox = (0.0, 0.0, 0.0, 0.0)
        try:
            ps = (int(ps_raw[0]), int(ps_raw[1]))
        except (TypeError, ValueError, IndexError):
            ps = (0, 0)
        side_raw = str(data.get("side", "after"))
        side: Literal["before", "after"] = side_raw if side_raw in {"before", "after"} else "after"  # type: ignore[assignment]
        bbox_space = normalize_coordinate_space(
            data.get("bbox_coordinate_space", data.get("coordinate_space", COORD_CAD_WCS_MM))
        )
        return cls(
            zone_id=str(data.get("zone_id", "")),
            side=side,
            raster_uri=str(data.get("raster_uri", "")),
            world_bbox=bbox,
            pixel_size=ps,
            world_to_pixel=tuple(data.get("world_to_pixel", IDENTITY_AFFINE)),  # type: ignore[arg-type]
            pixel_to_world=tuple(data.get("pixel_to_world", IDENTITY_AFFINE)),  # type: ignore[arg-type]
            transform_quality=data.get("transform_quality", "exact"),
            coordinate_contract_version=str(
                data.get("coordinate_contract_version", COORDINATE_CONTRACT_VERSION)
            ),
            bbox_coordinate_space=bbox_space,
            source_truth=_effective_source_truth(data.get("source_truth"), bbox_space),
            y_axis=_effective_y_axis(data.get("y_axis"), bbox_space),
            render_ms=float(data.get("render_ms", 0.0) or 0.0),
            cache_hit=bool(data.get("cache_hit", False)),
            request_cache_key=str(data.get("request_cache_key", "")),
            visual_fidelity=str(data.get("visual_fidelity", "")),
            render_lifecycle=str(data.get("render_lifecycle", "")),
            fallback_reason_code=str(
                data.get("fallback_reason_code", data.get("reason_code", ""))
            ),
            warnings=[str(item) for item in data.get("warnings", [])]
            if isinstance(data.get("warnings"), list)
            else [],
            notes=str(data.get("notes", "")),
        )


# ---------------------------------------------------------------------------
# Top-level manifest
# ---------------------------------------------------------------------------


@dataclass
class ViewerManifestV3:
    """Top-level v3 manifest for one comparison pair (or a package of pairs)."""

    pair_uuid: str
    package_version: str
    source_kind: SourceKind
    before_source_signature: SourceSignature = field(default_factory=SourceSignature)
    after_source_signature: SourceSignature = field(default_factory=SourceSignature)
    compare_sig: str = ""
    renderer_capabilities: dict[str, Any] = field(default_factory=dict)
    coordinate_space: str = COORDINATE_SPACE
    before_world_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    after_world_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    shared_world_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    alignment_before_to_shared: Affine6 = IDENTITY_AFFINE
    alignment_after_to_shared: Affine6 = IDENTITY_AFFINE
    overlay_space: OverlaySpace = "world"
    # ADR-003 H3 — display overlay coordinate space when it differs from
    # the detection space. Empty = same as overlay_space/coordinate_space
    # (legacy, unchanged behaviour). Set to e.g. "image_pixels_tl" for the
    # PDF-first hybrid viewer, where DWG diffs (detected in cad_wcs_mm) are
    # overlaid on a rendered PDF page in image pixels. This lets the
    # detection space and the display space differ without forcing a new
    # source_kind (ADR-003 §3, the "less invasive" option).
    display_overlay_space: str = ""
    default_focus_padding_world: float = 1500.0
    before_scene_pack: Optional[ScenePackRef] = None
    after_scene_pack: Optional[ScenePackRef] = None
    zone_requests: List[ZoneRequestRef] = field(default_factory=list)
    evidence: List[EvidenceRef] = field(default_factory=list)
    current_render_mode: RenderMode = "relative_only"
    font_signature: str = ""
    dependency_signature: str = ""
    created_at_utc: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.source_kind not in _VALID_SOURCE_KIND:
            raise ManifestV3ValidationError(
                f"Unknown source_kind: {self.source_kind!r}"
            )
        if self.overlay_space not in _VALID_OVERLAY_SPACE:
            raise ManifestV3ValidationError(
                f"Unknown overlay_space: {self.overlay_space!r}"
            )
        if not is_valid_mode(self.current_render_mode):
            raise ManifestV3ValidationError(
                f"Unknown current_render_mode: {self.current_render_mode!r}"
            )
        # ADR-003 H3 — normalise display_overlay_space when set; empty
        # stays empty (means "use detection space", legacy behaviour).
        if self.display_overlay_space:
            self.display_overlay_space = normalize_coordinate_space(
                self.display_overlay_space
            )
        if not self.created_at_utc:
            self.created_at_utc = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pair_uuid": self.pair_uuid,
            "package_version": self.package_version,
            "source_kind": self.source_kind,
            "before_source_signature": self.before_source_signature.to_dict(),
            "after_source_signature": self.after_source_signature.to_dict(),
            "compare_sig": self.compare_sig,
            "renderer_capabilities": dict(self.renderer_capabilities),
            "coordinate_space": self.coordinate_space,
            "before_world_bbox": list(self.before_world_bbox),
            "after_world_bbox": list(self.after_world_bbox),
            "shared_world_bbox": list(self.shared_world_bbox),
            "alignment_before_to_shared": list(self.alignment_before_to_shared),
            "alignment_after_to_shared": list(self.alignment_after_to_shared),
            "overlay_space": self.overlay_space,
            "display_overlay_space": self.display_overlay_space,
            "default_focus_padding_world": float(self.default_focus_padding_world),
            "before_scene_pack": self.before_scene_pack.to_dict() if self.before_scene_pack else None,
            "after_scene_pack": self.after_scene_pack.to_dict() if self.after_scene_pack else None,
            "zone_requests": [r.to_dict() for r in self.zone_requests],
            "evidence": [e.to_dict() for e in self.evidence],
            "current_render_mode": self.current_render_mode,
            "font_signature": self.font_signature,
            "dependency_signature": self.dependency_signature,
            "created_at_utc": self.created_at_utc,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ViewerManifestV3":
        if not isinstance(data, dict):
            raise ManifestV3ValidationError(
                f"Manifest expects a dict, got {type(data).__name__}"
            )
        sv = data.get("schema_version")
        if sv != SCHEMA_VERSION:
            raise ManifestV3ValidationError(
                f"Unsupported schema_version: {sv!r} (expected {SCHEMA_VERSION!r})"
            )
        before_pack = data.get("before_scene_pack")
        after_pack = data.get("after_scene_pack")
        return cls(
            pair_uuid=str(data.get("pair_uuid", "")),
            package_version=str(data.get("package_version", "")),
            source_kind=data.get("source_kind", "normalized_dxf"),
            before_source_signature=SourceSignature.from_dict(
                data.get("before_source_signature")
            ),
            after_source_signature=SourceSignature.from_dict(
                data.get("after_source_signature")
            ),
            compare_sig=str(data.get("compare_sig", "")),
            renderer_capabilities=dict(data.get("renderer_capabilities", {})),
            coordinate_space=str(data.get("coordinate_space", COORDINATE_SPACE)),
            before_world_bbox=tuple(data.get("before_world_bbox", (0.0, 0.0, 0.0, 0.0))),  # type: ignore[arg-type]
            after_world_bbox=tuple(data.get("after_world_bbox", (0.0, 0.0, 0.0, 0.0))),  # type: ignore[arg-type]
            shared_world_bbox=tuple(data.get("shared_world_bbox", (0.0, 0.0, 0.0, 0.0))),  # type: ignore[arg-type]
            alignment_before_to_shared=tuple(  # type: ignore[arg-type]
                data.get("alignment_before_to_shared", IDENTITY_AFFINE)
            ),
            alignment_after_to_shared=tuple(  # type: ignore[arg-type]
                data.get("alignment_after_to_shared", IDENTITY_AFFINE)
            ),
            overlay_space=data.get("overlay_space", "world"),
            display_overlay_space=str(data.get("display_overlay_space", "")),
            default_focus_padding_world=float(
                data.get("default_focus_padding_world", 1500.0)
            ),
            before_scene_pack=ScenePackRef.from_dict(before_pack) if isinstance(before_pack, dict) else None,
            after_scene_pack=ScenePackRef.from_dict(after_pack) if isinstance(after_pack, dict) else None,
            zone_requests=[
                ZoneRequestRef.from_dict(r)
                for r in (data.get("zone_requests") or [])
                if isinstance(r, dict)
            ],
            evidence=[
                EvidenceRef.from_dict(e)
                for e in (data.get("evidence") or [])
                if isinstance(e, dict)
            ],
            current_render_mode=data.get("current_render_mode", "relative_only"),
            font_signature=str(data.get("font_signature", "")),
            dependency_signature=str(data.get("dependency_signature", "")),
            created_at_utc=str(data.get("created_at_utc", "")),
            schema_version=sv,
        )


def write_manifest_v3(path: Path, manifest: ViewerManifestV3) -> None:
    """Persist a v3 manifest as pretty-printed UTF-8 JSON (atomic write)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def load_manifest_v3(path: Path) -> ViewerManifestV3:
    """Load a v3 manifest from disk. Raises :class:`ManifestV3ValidationError`."""

    if not path.exists():
        raise ManifestV3ValidationError(f"Manifest not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestV3ValidationError(
            f"Cannot read manifest {path}: {exc}"
        ) from exc
    return ViewerManifestV3.from_dict(data)


def is_v3_manifest(path: Path) -> bool:
    """Return True if ``path`` looks like a v3 manifest (does not raise)."""

    if not path.exists():
        return False
    try:
        head = path.read_text(encoding="utf-8")[:512]
    except OSError:
        return False
    return f'"schema_version": "{SCHEMA_VERSION}"' in head


def _effective_source_truth(value: Any, bbox_space: str) -> str:
    inferred = source_truth_for_coordinate_space(bbox_space)
    raw = str(value or "").strip()
    if raw and not (raw == SOURCE_TRUTH_CAD_ENTITY and inferred != SOURCE_TRUTH_CAD_ENTITY):
        return raw
    return inferred


def _effective_y_axis(value: Any, bbox_space: str) -> str:
    inferred = coordinate_space_y_axis(bbox_space)
    raw = str(value or "").strip()
    if raw and not (raw == Y_AXIS_UP and inferred != Y_AXIS_UP):
        return raw
    return inferred


__all__ = [
    "SCHEMA_VERSION",
    "MANIFEST_FILENAME",
    "COORDINATE_SPACE",
    "SourceKind",
    "OverlaySpace",
    "IDENTITY_AFFINE",
    "SourceSignature",
    "ScenePackRef",
    "ZoneRequestRef",
    "EvidenceRef",
    "ViewerManifestV3",
    "ManifestV3ValidationError",
    "write_manifest_v3",
    "load_manifest_v3",
    "is_v3_manifest",
]
