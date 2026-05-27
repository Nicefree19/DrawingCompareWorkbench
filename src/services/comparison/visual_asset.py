# -*- coding: utf-8 -*-
"""Visual asset manifest for PDF-first drawing viewer artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .cad_visual_backend import CadVisualConversionResult
from .source_signature import build_source_signature
from .transform import COORDINATE_CONTRACT_VERSION


VisualAssetKind = Literal[
    "source_pdf",
    "sidecar_pdf",
    "cad_to_pdf",
    "cad_to_image",
    "raster_fallback",
    "relative_only",
]


class VisualAssetManifestValidationError(ValueError):
    """Raised when a visual asset manifest is incomplete."""


@dataclass(frozen=True)
class VisualAssetManifest:
    """Serializable provenance for one viewer visual asset."""

    visual_asset_id: str
    source_path: str
    asset_path: str
    asset_kind: VisualAssetKind
    status: str = "ready"
    reason_code: str = ""
    source_hash: str = ""
    source_signature: dict[str, Any] = field(default_factory=dict)
    cache_key_hash: str = ""
    plot_profile_hash: str = ""
    layout_name: str = ""
    coordinate_contract_version: str = COORDINATE_CONTRACT_VERSION
    bbox_coordinate_space: str = ""
    page_index: int = 0
    dpi: int = 0
    page_size_pt: list[float] = field(default_factory=list)
    pixel_size: list[int] = field(default_factory=list)
    visual_backend_id: str = ""
    visual_backend_version: str = ""
    visual_backend_license_id: str = ""
    visual_fidelity: str = ""
    render_lifecycle: str = "ready"
    transform_quality: str = ""
    nonblank_probe_status: str = ""
    requires_network: bool = False
    conversion_invoked_from_hot_path: bool = False
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_visual_asset(self)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: Any) -> "VisualAssetManifest":
        if not isinstance(data, dict):
            raise VisualAssetManifestValidationError("visual asset manifest must be a JSON object")
        asset_kind = str(data.get("asset_kind") or "relative_only")
        if asset_kind not in _VALID_ASSET_KINDS:
            asset_kind = "relative_only"
        return cls(
            visual_asset_id=str(data.get("visual_asset_id") or ""),
            source_path=str(data.get("source_path") or ""),
            asset_path=str(data.get("asset_path") or ""),
            asset_kind=asset_kind,  # type: ignore[arg-type]
            status=str(data.get("status") or "ready"),
            reason_code=str(data.get("reason_code") or ""),
            source_hash=str(data.get("source_hash") or ""),
            source_signature=dict(data.get("source_signature") or {}),
            cache_key_hash=str(data.get("cache_key_hash") or ""),
            plot_profile_hash=str(data.get("plot_profile_hash") or ""),
            layout_name=str(data.get("layout_name") or ""),
            coordinate_contract_version=str(
                data.get("coordinate_contract_version") or COORDINATE_CONTRACT_VERSION
            ),
            bbox_coordinate_space=str(data.get("bbox_coordinate_space") or ""),
            page_index=_safe_int(data.get("page_index")),
            dpi=_safe_int(data.get("dpi")),
            page_size_pt=[
                float(item)
                for item in data.get("page_size_pt", [])
                if isinstance(item, (int, float))
            ],
            pixel_size=[
                _safe_int(item)
                for item in data.get("pixel_size", [])
                if _safe_int(item) > 0
            ],
            visual_backend_id=str(data.get("visual_backend_id") or ""),
            visual_backend_version=str(data.get("visual_backend_version") or ""),
            visual_backend_license_id=str(data.get("visual_backend_license_id") or ""),
            visual_fidelity=str(data.get("visual_fidelity") or ""),
            render_lifecycle=str(data.get("render_lifecycle") or "ready"),
            transform_quality=str(data.get("transform_quality") or ""),
            nonblank_probe_status=str(data.get("nonblank_probe_status") or ""),
            requires_network=bool(data.get("requires_network")),
            conversion_invoked_from_hot_path=bool(data.get("conversion_invoked_from_hot_path")),
            warnings=[str(item) for item in data.get("warnings", []) if str(item)],
            metadata=dict(data.get("metadata") or {}),
        )

    @classmethod
    def from_conversion_result(
        cls,
        result: CadVisualConversionResult,
        *,
        visual_asset_id: str = "",
        asset_kind: VisualAssetKind | None = None,
        source_hash: str = "",
    ) -> "VisualAssetManifest":
        resolved_kind = asset_kind or ("cad_to_image" if result.output_format == "image" else "cad_to_pdf")
        metadata = dict(result.metadata)
        resolved_source_hash = source_hash or _source_hash_for_path(result.source_path)
        cache_key_hash = str(metadata.get("cache_key_hash") or "") or build_visual_asset_cache_key(
            source_hash=resolved_source_hash,
            backend_id=result.backend_id,
            backend_version=result.backend_version,
            license_id=result.license_id,
            plot_profile_hash=str(metadata.get("plot_profile_hash") or ""),
            layout_name=str(metadata.get("layout_name") or ""),
            page_index=_safe_int(metadata.get("page_index")),
            dpi=_safe_int(metadata.get("dpi")),
            coordinate_contract_version=COORDINATE_CONTRACT_VERSION,
        )
        return cls(
            visual_asset_id=visual_asset_id or _default_visual_asset_id(result.source_path, result.output_path),
            source_path=result.source_path,
            asset_path=result.output_path,
            asset_kind=resolved_kind,
            status="ready" if result.ok else result.status,
            reason_code=result.reason_code,
            source_hash=resolved_source_hash,
            source_signature={"source_hash": resolved_source_hash} if resolved_source_hash else {},
            cache_key_hash=cache_key_hash,
            plot_profile_hash=str(metadata.get("plot_profile_hash") or ""),
            layout_name=str(metadata.get("layout_name") or ""),
            visual_backend_id=result.backend_id,
            visual_backend_version=result.backend_version,
            visual_backend_license_id=result.license_id,
            visual_fidelity="pdf_visual_background" if result.output_format == "pdf" and result.ok else "relative_only",
            render_lifecycle="ready" if result.ok else "fallback_visible",
            transform_quality="estimated" if result.ok else "relative_only",
            page_index=_safe_int(metadata.get("page_index")),
            dpi=_safe_int(metadata.get("dpi")),
            page_size_pt=_float_list(metadata.get("page_size_pt")),
            pixel_size=_int_list(metadata.get("pixel_size")),
            nonblank_probe_status=str(metadata.get("nonblank_probe_status") or ""),
            requires_network=bool(metadata.get("requires_network")),
            conversion_invoked_from_hot_path=bool(metadata.get("conversion_invoked_from_hot_path")),
            warnings=list(result.warnings),
            metadata=metadata,
        )


_VALID_ASSET_KINDS = {
    "source_pdf",
    "sidecar_pdf",
    "cad_to_pdf",
    "cad_to_image",
    "raster_fallback",
    "relative_only",
}


def write_visual_asset_manifest(path: Path, manifest: VisualAssetManifest) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.to_json(), encoding="utf-8")
    return path


def read_visual_asset_manifest(path: Path) -> VisualAssetManifest:
    return VisualAssetManifest.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def build_visual_asset_cache_key(
    *,
    source_hash: str = "",
    source_signature: dict[str, Any] | None = None,
    backend_id: str = "",
    backend_version: str = "",
    license_id: str = "",
    plot_profile_hash: str = "",
    layout_name: str = "",
    page_index: int = 0,
    dpi: int = 0,
    coordinate_contract_version: str = COORDINATE_CONTRACT_VERSION,
) -> str:
    signature = source_signature if isinstance(source_signature, dict) else {}
    resolved_source_hash = source_hash or str(signature.get("source_hash") or "")
    components = {
        "schema": 1,
        "source_hash": resolved_source_hash,
        "signature_schema_version": str(signature.get("schema_version") or ""),
        "backend_id": str(backend_id or ""),
        "backend_version": str(backend_version or ""),
        "license_id": str(license_id or ""),
        "plot_profile_hash": str(plot_profile_hash or ""),
        "layout_name": str(layout_name or ""),
        "page_index": int(page_index or 0),
        "dpi": int(dpi or 0),
        "coordinate_contract_version": str(coordinate_contract_version or ""),
    }
    encoded = json.dumps(components, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_visual_asset_policy(
    asset: VisualAssetManifest | dict[str, Any],
    *,
    customer_grade: bool = False,
) -> list[str]:
    raw_asset_kind = ""
    if isinstance(asset, dict):
        raw_asset_kind = str(asset.get("asset_kind") or "")
    manifest = asset if isinstance(asset, VisualAssetManifest) else VisualAssetManifest.from_dict(asset)
    issues: list[str] = []
    if raw_asset_kind and raw_asset_kind not in _VALID_ASSET_KINDS:
        issues.append(f"unsupported asset_kind: {raw_asset_kind}")
    ready_visual = manifest.status == "ready" and manifest.asset_kind != "relative_only"
    converted_asset = manifest.asset_kind in {"cad_to_pdf", "cad_to_image"}
    pdf_like_asset = manifest.asset_kind in {"source_pdf", "sidecar_pdf", "cad_to_pdf"}

    if ready_visual:
        if not manifest.source_hash and not manifest.source_signature.get("source_hash"):
            issues.append("source_hash or source_signature.source_hash is required")
        if not manifest.cache_key_hash:
            issues.append("cache_key_hash is required")
        else:
            expected_cache_key = build_visual_asset_cache_key(
                source_hash=manifest.source_hash,
                source_signature=manifest.source_signature,
                backend_id=manifest.visual_backend_id,
                backend_version=manifest.visual_backend_version,
                license_id=manifest.visual_backend_license_id,
                plot_profile_hash=manifest.plot_profile_hash,
                layout_name=manifest.layout_name,
                page_index=manifest.page_index,
                dpi=manifest.dpi,
                coordinate_contract_version=manifest.coordinate_contract_version,
            )
            if expected_cache_key != manifest.cache_key_hash:
                issues.append("cache_key_hash does not match visual asset provenance fields")
        if not manifest.coordinate_contract_version:
            issues.append("coordinate_contract_version is required")
        if not manifest.transform_quality:
            issues.append("transform_quality is required")
        if customer_grade and manifest.nonblank_probe_status != "passed":
            issues.append("nonblank_probe_status must be passed")

    if pdf_like_asset and ready_visual and customer_grade:
        if len(manifest.page_size_pt) != 2 or any(float(value) <= 0 for value in manifest.page_size_pt):
            issues.append("page_size_pt must contain positive width and height")

    if converted_asset:
        if not manifest.visual_backend_id:
            issues.append("visual_backend_id is required for CAD visual conversion")
        if not manifest.visual_backend_version:
            issues.append("visual_backend_version is required for CAD visual conversion")
        if not manifest.visual_backend_license_id:
            issues.append("visual_backend_license_id is required for CAD visual conversion")
        if not manifest.plot_profile_hash:
            issues.append("plot_profile_hash is required for CAD visual conversion")
        if manifest.conversion_invoked_from_hot_path:
            issues.append("CAD visual conversion must not run in the GUI or viewer hot path")
        if manifest.requires_network and customer_grade and manifest.metadata.get("network_approval_status") != "approved":
            issues.append("network CAD visual conversion requires explicit approval")

    if manifest.metadata.get("exact_overlay_allowed") is True and manifest.transform_quality != "exact":
        issues.append("exact overlay cannot be allowed when transform_quality is not exact")

    return issues


def _validate_visual_asset(asset: VisualAssetManifest) -> None:
    if not asset.visual_asset_id:
        raise VisualAssetManifestValidationError("visual_asset_id is required")
    if asset.asset_kind not in _VALID_ASSET_KINDS:
        raise VisualAssetManifestValidationError(f"unsupported asset_kind: {asset.asset_kind}")
    if asset.asset_kind not in {"relative_only"} and not asset.source_path:
        raise VisualAssetManifestValidationError("source_path is required")
    if asset.status == "ready" and asset.asset_kind not in {"relative_only"} and not asset.asset_path:
        raise VisualAssetManifestValidationError("asset_path is required for ready visual assets")
    if asset.visual_backend_id and not asset.visual_backend_license_id:
        raise VisualAssetManifestValidationError("visual_backend_license_id is required")


def _source_hash_for_path(path: str) -> str:
    if not path:
        return ""
    try:
        return str(build_source_signature(Path(path)).get("source_hash") or "")
    except Exception:
        return ""


def _default_visual_asset_id(source_path: str, asset_path: str) -> str:
    source_name = Path(source_path).stem if source_path else "source"
    asset_name = Path(asset_path).stem if asset_path else "asset"
    return f"{source_name}-{asset_name}".strip("-") or "visual-asset"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_list(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[float] = []
    for item in value:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            continue
    return result


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[int] = []
    for item in value:
        parsed = _safe_int(item)
        if parsed > 0:
            result.append(parsed)
    return result


__all__ = [
    "build_visual_asset_cache_key",
    "VisualAssetKind",
    "VisualAssetManifest",
    "VisualAssetManifestValidationError",
    "read_visual_asset_manifest",
    "validate_visual_asset_policy",
    "write_visual_asset_manifest",
]
