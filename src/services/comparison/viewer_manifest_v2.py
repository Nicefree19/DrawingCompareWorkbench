# -*- coding: utf-8 -*-
"""Viewer manifest v2 — explicit fidelity, world coords, and per-artifact transforms.

Phase F replaces the implicit, single-enum render state of viewer_manifest v1
with a structured schema that separates **what the user is looking at**
(``background_fidelity``) from **what the renderer is doing**
(``render_job_status``). It also stores the affine transform on every artifact
so the viewer can confidently overlay change-zone markers without drift.

Schema sketch (see plan doc Phase F §1.7 for full table):

    {
      "schema_version": "viewer_manifest.v2",
      "pair_uuid": "...",
      "package_version": "...",
      "source_kind": "normalized_dxf | pdf | mixed",
      "before_doc_sig": "...", "after_doc_sig": "...", "compare_sig": "...",
      "renderer_capabilities": { ... },
      "coordinate_space": "world_xy_2d",
      "before_world_bbox": [...], "after_world_bbox": [...],
      "shared_world_bbox": [...],
      "alignment_before_to_shared": [a,b,c,d,e,f],
      "alignment_after_to_shared":  [a,b,c,d,e,f],
      "overlay_space": "world | relative_only",
      "default_focus_padding_world": 1500.0,
      "zone_index_uri": "...", "cluster_index_uri": "...",
      "preview_asset_uri": "...", "overview_tileset_uri": "...",
      "font_signature": "...", "dependency_signature": "...",
      "created_at_utc": "...",
      "pairs": [
        {
          "pair_id": "...",
          "background_fidelity": "exact_world_render | exact_world_tile_sparse | simplified_world_preview | relative_only",
          "render_job_status": "idle | queued | rendering | timed_out | failed",
          "before": ArtifactRef, "after": ArtifactRef,
          "notes": "..."
        }, ...
      ]
    }

The module is **pure Python + json + dataclasses** — no Qt, no ezdxf, no
PIL — so it imports in milliseconds and is unit-testable in any environment.
Validation is intentionally strict: a manifest missing ``transform_quality``
or carrying an unknown enum will raise on load. This is by design — the whole
point of Phase F is to refuse to display ambiguous fidelity to the user.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Literal, Optional

from src.services.comparison.transform import (
    Affine6,
    AffineParams,
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

SCHEMA_VERSION: Final[str] = "viewer_manifest.v2"
MANIFEST_FILENAME: Final[str] = "viewer_manifest.json"
TILE_MANIFEST_FILENAME: Final[str] = "tile_manifest.json"
COORDINATE_SPACE: Final[str] = "world_xy_2d"

#: Background fidelity enum (4 values). The viewer renders a coloured badge
#: matching the value and refuses to enable measurement tools when set to
#: ``relative_only``.
BackgroundFidelity = Literal[
    "exact_world_render",       # 🟢 full exact background
    "exact_world_tile_sparse",  # 🔵 sparse exact tiles
    "simplified_world_preview", # ⚪ simplified preview, world-correct
    "relative_only",            # 🟠 normalized overlay only — NOT real background
]

#: Render job status enum (5 values). Drives spinner/badge UI.
RenderJobStatus = Literal[
    "idle",
    "queued",
    "rendering",
    "timed_out",
    "failed",
]

#: Source kind — drives which RenderBackend handles the artifact.
SourceKind = Literal["normalized_dxf", "pdf", "mixed"]

#: Overlay space — ``world`` means change-zone bboxes are in world units;
#: ``relative_only`` means they are relative to the displayed bitmap and must
#: not be claimed as exact world positions.
OverlaySpace = Literal["world", "relative_only"]

#: Transform quality (re-exported for convenience).
TransformQuality = Literal["exact", "estimated", "relative_only"]

_VALID_FIDELITY: Final[frozenset[str]] = frozenset({
    "exact_world_render",
    "exact_world_tile_sparse",
    "simplified_world_preview",
    "relative_only",
})
_VALID_JOB_STATUS: Final[frozenset[str]] = frozenset({
    "idle", "queued", "rendering", "timed_out", "failed",
})
_VALID_SOURCE_KIND: Final[frozenset[str]] = frozenset({
    "normalized_dxf", "pdf", "mixed",
})
_VALID_OVERLAY_SPACE: Final[frozenset[str]] = frozenset({
    "world", "relative_only",
})
_VALID_TRANSFORM_QUALITY: Final[frozenset[str]] = frozenset({
    "exact", "estimated", "relative_only",
})

#: Identity affine — used as a placeholder when an artifact is ``relative_only``
#: and has no meaningful world transform. Callers should always check
#: ``transform_quality`` before using the matrix.
IDENTITY_AFFINE: Final[Affine6] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


class ManifestValidationError(ValueError):
    """Raised when a manifest fails schema validation."""


@dataclass
class ArtifactRef:
    """One rendered side (before or after) of one pair.

    Carries enough metadata for the viewer to re-load the image, place change
    overlays, and badge the fidelity correctly.
    """

    image_uri: str
    world_bbox: Bbox
    pixel_size: tuple[int, int]
    world_to_pixel: Affine6 = IDENTITY_AFFINE
    pixel_to_world: Affine6 = IDENTITY_AFFINE
    transform_quality: TransformQuality = "exact"
    coordinate_contract_version: str = COORDINATE_CONTRACT_VERSION
    bbox_coordinate_space: str = COORD_CAD_WCS_MM
    source_truth: str = SOURCE_TRUTH_CAD_ENTITY
    y_axis: str = Y_AXIS_UP
    layer_visibility_sig: str = ""
    render_profile_sig: str = ""
    renderer_id: str = ""
    renderer_version: str = ""
    render_ms: Optional[float] = None
    artifact_bytes: Optional[int] = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Tuples → lists for clean JSON.
        d["world_bbox"] = list(self.world_bbox)
        d["pixel_size"] = list(self.pixel_size)
        d["world_to_pixel"] = list(self.world_to_pixel)
        d["pixel_to_world"] = list(self.pixel_to_world)
        bbox_space = normalize_coordinate_space(self.bbox_coordinate_space)
        d["bbox_coordinate_space"] = bbox_space
        d["source_truth"] = _effective_source_truth(self.source_truth, bbox_space)
        d["y_axis"] = _effective_y_axis(self.y_axis, bbox_space)
        return d

    @classmethod
    def from_affine_params(
        cls,
        *,
        image_uri: str,
        params: AffineParams,
        layer_visibility_sig: str = "",
        render_profile_sig: str = "",
        renderer_id: str = "",
        renderer_version: str = "",
        render_ms: Optional[float] = None,
        artifact_bytes: Optional[int] = None,
        notes: str = "",
    ) -> "ArtifactRef":
        """Convenience constructor sourced from a :class:`AffineParams`."""

        return cls(
            image_uri=image_uri,
            world_bbox=params.world_bbox,
            pixel_size=params.pixel_size,
            world_to_pixel=params.world_to_pixel,
            pixel_to_world=params.pixel_to_world,
            transform_quality=params.quality,
            coordinate_contract_version=COORDINATE_CONTRACT_VERSION,
            bbox_coordinate_space=params.coordinate_space,
            source_truth=params.source_truth,
            y_axis=params.y_axis,
            layer_visibility_sig=layer_visibility_sig,
            render_profile_sig=render_profile_sig,
            renderer_id=renderer_id,
            renderer_version=renderer_version,
            render_ms=render_ms,
            artifact_bytes=artifact_bytes,
            notes=notes,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactRef":
        if not isinstance(data, dict):
            raise ManifestValidationError(
                f"ArtifactRef expects a dict, got {type(data).__name__}"
            )
        tq = data.get("transform_quality", "exact")
        if tq not in _VALID_TRANSFORM_QUALITY:
            raise ManifestValidationError(
                f"Unknown transform_quality: {tq!r}"
            )
        bbox_space = normalize_coordinate_space(
            data.get("bbox_coordinate_space", data.get("coordinate_space", COORD_CAD_WCS_MM))
        )
        try:
            return cls(
                image_uri=str(data.get("image_uri", "")),
                world_bbox=tuple(data.get("world_bbox", (0.0, 0.0, 0.0, 0.0))),  # type: ignore[arg-type]
                pixel_size=tuple(data.get("pixel_size", (0, 0))),  # type: ignore[arg-type]
                world_to_pixel=tuple(data.get("world_to_pixel", IDENTITY_AFFINE)),  # type: ignore[arg-type]
                pixel_to_world=tuple(data.get("pixel_to_world", IDENTITY_AFFINE)),  # type: ignore[arg-type]
                transform_quality=tq,
                coordinate_contract_version=str(
                    data.get("coordinate_contract_version", COORDINATE_CONTRACT_VERSION)
                ),
                bbox_coordinate_space=bbox_space,
                source_truth=_effective_source_truth(data.get("source_truth"), bbox_space),
                y_axis=_effective_y_axis(data.get("y_axis"), bbox_space),
                layer_visibility_sig=str(data.get("layer_visibility_sig", "")),
                render_profile_sig=str(data.get("render_profile_sig", "")),
                renderer_id=str(data.get("renderer_id", "")),
                renderer_version=str(data.get("renderer_version", "")),
                render_ms=_opt_float(data.get("render_ms")),
                artifact_bytes=_opt_int(data.get("artifact_bytes")),
                notes=str(data.get("notes", "")),
            )
        except (TypeError, ValueError) as exc:
            raise ManifestValidationError(f"Malformed ArtifactRef: {exc}") from exc


@dataclass
class PairEntry:
    """One ``(before, after)`` pair tracked in the manifest."""

    pair_id: str
    background_fidelity: BackgroundFidelity = "relative_only"
    render_job_status: RenderJobStatus = "idle"
    before: Optional[ArtifactRef] = None
    after: Optional[ArtifactRef] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.background_fidelity not in _VALID_FIDELITY:
            raise ManifestValidationError(
                f"Unknown background_fidelity: {self.background_fidelity!r}"
            )
        if self.render_job_status not in _VALID_JOB_STATUS:
            raise ManifestValidationError(
                f"Unknown render_job_status: {self.render_job_status!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "background_fidelity": self.background_fidelity,
            "render_job_status": self.render_job_status,
            "before": self.before.to_dict() if self.before else None,
            "after": self.after.to_dict() if self.after else None,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PairEntry":
        if not isinstance(data, dict):
            raise ManifestValidationError(
                f"PairEntry expects a dict, got {type(data).__name__}"
            )
        before_raw = data.get("before")
        after_raw = data.get("after")
        return cls(
            pair_id=str(data.get("pair_id", "")),
            background_fidelity=data.get("background_fidelity", "relative_only"),
            render_job_status=data.get("render_job_status", "idle"),
            before=ArtifactRef.from_dict(before_raw) if isinstance(before_raw, dict) else None,
            after=ArtifactRef.from_dict(after_raw) if isinstance(after_raw, dict) else None,
            notes=str(data.get("notes", "")),
        )


@dataclass
class ViewerManifestV2:
    """Top-level v2 manifest carrying global + per-pair state."""

    pair_uuid: str
    package_version: str
    source_kind: SourceKind
    before_doc_sig: str = ""
    after_doc_sig: str = ""
    compare_sig: str = ""
    renderer_capabilities: dict[str, Any] = field(default_factory=dict)
    coordinate_space: str = COORDINATE_SPACE
    before_world_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    after_world_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    shared_world_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    alignment_before_to_shared: Affine6 = IDENTITY_AFFINE
    alignment_after_to_shared: Affine6 = IDENTITY_AFFINE
    overlay_space: OverlaySpace = "world"
    default_focus_padding_world: float = 1500.0
    zone_index_uri: str = ""
    cluster_index_uri: str = ""
    preview_asset_uri: str = ""
    overview_tileset_uri: str = ""
    font_signature: str = ""
    dependency_signature: str = ""
    created_at_utc: str = ""
    pairs: list[PairEntry] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Validate enums up front so a programmer mistake fails loud, not silent.
        if self.source_kind not in _VALID_SOURCE_KIND:
            raise ManifestValidationError(
                f"Unknown source_kind: {self.source_kind!r}"
            )
        if self.overlay_space not in _VALID_OVERLAY_SPACE:
            raise ManifestValidationError(
                f"Unknown overlay_space: {self.overlay_space!r}"
            )
        if not self.created_at_utc:
            self.created_at_utc = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pair_uuid": self.pair_uuid,
            "package_version": self.package_version,
            "source_kind": self.source_kind,
            "before_doc_sig": self.before_doc_sig,
            "after_doc_sig": self.after_doc_sig,
            "compare_sig": self.compare_sig,
            "renderer_capabilities": dict(self.renderer_capabilities),
            "coordinate_space": self.coordinate_space,
            "before_world_bbox": list(self.before_world_bbox),
            "after_world_bbox": list(self.after_world_bbox),
            "shared_world_bbox": list(self.shared_world_bbox),
            "alignment_before_to_shared": list(self.alignment_before_to_shared),
            "alignment_after_to_shared": list(self.alignment_after_to_shared),
            "overlay_space": self.overlay_space,
            "default_focus_padding_world": float(self.default_focus_padding_world),
            "zone_index_uri": self.zone_index_uri,
            "cluster_index_uri": self.cluster_index_uri,
            "preview_asset_uri": self.preview_asset_uri,
            "overview_tileset_uri": self.overview_tileset_uri,
            "font_signature": self.font_signature,
            "dependency_signature": self.dependency_signature,
            "created_at_utc": self.created_at_utc,
            "pairs": [p.to_dict() for p in self.pairs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ViewerManifestV2":
        if not isinstance(data, dict):
            raise ManifestValidationError(
                f"Manifest expects a dict, got {type(data).__name__}"
            )
        sv = data.get("schema_version")
        if sv != SCHEMA_VERSION:
            raise ManifestValidationError(
                f"Unsupported schema_version: {sv!r} (expected {SCHEMA_VERSION!r})"
            )
        pairs_raw = data.get("pairs", [])
        if not isinstance(pairs_raw, list):
            raise ManifestValidationError("pairs must be a list")

        return cls(
            pair_uuid=str(data.get("pair_uuid", "")),
            package_version=str(data.get("package_version", "")),
            source_kind=data.get("source_kind", "normalized_dxf"),
            before_doc_sig=str(data.get("before_doc_sig", "")),
            after_doc_sig=str(data.get("after_doc_sig", "")),
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
            default_focus_padding_world=float(
                data.get("default_focus_padding_world", 1500.0)
            ),
            zone_index_uri=str(data.get("zone_index_uri", "")),
            cluster_index_uri=str(data.get("cluster_index_uri", "")),
            preview_asset_uri=str(data.get("preview_asset_uri", "")),
            overview_tileset_uri=str(data.get("overview_tileset_uri", "")),
            font_signature=str(data.get("font_signature", "")),
            dependency_signature=str(data.get("dependency_signature", "")),
            created_at_utc=str(data.get("created_at_utc", "")),
            pairs=[PairEntry.from_dict(p) for p in pairs_raw],
            schema_version=sv,
        )


def write_manifest_v2(path: Path, manifest: ViewerManifestV2) -> None:
    """Persist a v2 manifest as pretty-printed UTF-8 JSON.

    Creates parent directories. Atomic-ish: writes to ``{path}.tmp`` then
    replaces, so a crash mid-write does not corrupt the existing file.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def load_manifest_v2(path: Path) -> ViewerManifestV2:
    """Load a v2 manifest from disk. Raises :class:`ManifestValidationError`."""

    if not path.exists():
        raise ManifestValidationError(f"Manifest not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestValidationError(f"Cannot read manifest {path}: {exc}") from exc
    return ViewerManifestV2.from_dict(data)


def is_v2_manifest(path: Path) -> bool:
    """Return True if ``path`` looks like a v2 manifest (does not raise)."""

    if not path.exists():
        return False
    try:
        head = path.read_text(encoding="utf-8")[:512]
    except OSError:
        return False
    return f'"schema_version": "{SCHEMA_VERSION}"' in head


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _opt_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    "TILE_MANIFEST_FILENAME",
    "COORDINATE_SPACE",
    "BackgroundFidelity",
    "RenderJobStatus",
    "SourceKind",
    "OverlaySpace",
    "TransformQuality",
    "IDENTITY_AFFINE",
    "ArtifactRef",
    "PairEntry",
    "ViewerManifestV2",
    "ManifestValidationError",
    "write_manifest_v2",
    "load_manifest_v2",
    "is_v2_manifest",
]
