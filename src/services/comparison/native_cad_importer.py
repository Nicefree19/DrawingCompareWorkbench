"""DWG importer adapter for explicit native CAD bridge contracts."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .dwg_importer import (
    DwgAdapterDrawing,
    DwgFailureCode,
    DwgImportError,
    DwgImporterAdapter,
    DwgVersionDetector,
    DwgVersionInfo,
    _adapter_drawing_from_dict,
)
from .native_cad_bridge import NativeCadBridgeCode, NativeCadBridgeFailure, NativeCadBridgeRunner


COMMAND_ENV = "DRAWING_COMPARE_NATIVE_CAD_BRIDGE_COMMAND"
ARGS_JSON_ENV = "DRAWING_COMPARE_NATIVE_CAD_BRIDGE_ARGS_JSON"
SUPPORTED_VERSIONS_ENV = "DRAWING_COMPARE_NATIVE_CAD_BRIDGE_SUPPORTED_VERSIONS"
ADAPTER_NAME_ENV = "DRAWING_COMPARE_NATIVE_CAD_BRIDGE_ADAPTER_NAME"
ADAPTER_VERSION_ENV = "DRAWING_COMPARE_NATIVE_CAD_BRIDGE_ADAPTER_VERSION"
TIMEOUT_SECONDS_ENV = "DRAWING_COMPARE_NATIVE_CAD_BRIDGE_TIMEOUT_SECONDS"


class NativeCadBridgeAdapter(DwgImporterAdapter):
    """Adapter that imports only versions explicitly delegated to a bridge."""

    name = "native-cad-bridge"
    version = "1"
    license_id = "INTERNAL"
    backend_mode = "native_cad_bridge"
    implementation_status = "bridge_contract"
    approval_required = True

    def __init__(
        self,
        *,
        command: str | Path | None = None,
        args_template: Sequence[str] | None = None,
        supported_versions: Sequence[str] | str | None = None,
        name: str | None = None,
        version: str | None = None,
        timeout_seconds: float | None = None,
        adapter_id: str = "native-cad-json-bridge",
    ) -> None:
        self.name = str(name or os.environ.get(ADAPTER_NAME_ENV) or self.name).strip()
        self.version = str(version or os.environ.get(ADAPTER_VERSION_ENV) or self.version).strip()
        self.supported_versions = _normalize_supported_versions(supported_versions)
        self.runner = NativeCadBridgeRunner(
            command=command or os.environ.get(COMMAND_ENV),
            args_template=args_template if args_template is not None else _args_template_from_env(),
            timeout_seconds=_timeout(timeout_seconds),
            adapter_id=adapter_id,
        )

    def is_available(self) -> bool:
        return True

    def supports_version(self, version: DwgVersionInfo) -> bool:
        if not self.supported_versions:
            return False
        return "*" in self.supported_versions or version.code.upper() in self.supported_versions

    def read_file(self, path: str | Path, version: DwgVersionInfo) -> DwgAdapterDrawing:
        result = self.runner.run(path, version)
        if not result.ok:
            failure = result.failure or NativeCadBridgeFailure(
                code=NativeCadBridgeCode.CONTRACT_INVALID,
                message="Native CAD bridge returned neither scene_pack nor failure.",
            )
            raise DwgImportError(
                _dwg_failure_code(failure.code),
                failure.message,
                details={
                    "native_cad_bridge": {
                        "failure": failure.to_dict(),
                        "diagnostics": self.diagnostics(),
                    }
                },
            )
        if result.scene_pack is None:
            raise DwgImportError(
                DwgFailureCode.ADAPTER_FAILED,
                "Native CAD bridge result is missing scene_pack.",
                details={"native_cad_bridge": {"diagnostics": self.diagnostics()}},
            )

        payload = (
            result.drawing
            if isinstance(result.drawing, dict)
            else _drawing_payload_from_scene_pack(result.scene_pack, version)
        )
        drawing = _adapter_drawing_from_dict(payload)
        drawing.metadata = dict(drawing.metadata or {})
        drawing.metadata["native_scene_pack"] = result.scene_pack.to_dict()
        drawing.metadata["native_scene_overview_lod0"] = result.scene_pack.overview_lod0_payload()
        drawing.metadata["native_cad_bridge"] = {
            "adapter": self.name,
            "adapter_version": self.version,
            "backend_mode": self.backend_mode,
            "implementation_status": self.implementation_status,
            "approval_required": self.approval_required,
            "supported_versions": sorted(self.supported_versions),
            "dwg_version": version.code,
            "cache_identity": self.cache_identity(path, version, result.scene_pack),
            "diagnostics": self.diagnostics(),
        }
        return drawing

    def diagnostics(self) -> dict[str, Any]:
        return {
            "kind": "native_cad_bridge_adapter",
            "supported_versions": sorted(self.supported_versions),
            "runner": self.runner.diagnostics(),
            "env": {
                "command": COMMAND_ENV,
                "args_json": ARGS_JSON_ENV,
                "supported_versions": SUPPORTED_VERSIONS_ENV,
                "adapter_name": ADAPTER_NAME_ENV,
                "adapter_version": ADAPTER_VERSION_ENV,
                "timeout_seconds": TIMEOUT_SECONDS_ENV,
            },
        }

    def cache_identity(
        self,
        path: str | Path,
        version: DwgVersionInfo,
        scene_pack: Any | None = None,
    ) -> dict[str, Any]:
        source = dict(getattr(scene_pack, "source", {}) or {})
        payload = {
            "schema_version": "native-cad-cache-identity/v1",
            "source": {
                "path": str(path),
                "size": source.get("size"),
                "sha256": source.get("sha256"),
            },
            "dwg_version": version.to_dict(),
            "adapter": {
                "name": self.name,
                "version": self.version,
                "backend_mode": self.backend_mode,
                "implementation_status": self.implementation_status,
                "supported_versions": sorted(self.supported_versions),
            },
            "bridge": self.runner.diagnostics(),
            "scene_pack_schema": getattr(scene_pack, "schema_version", None),
        }
        payload["fingerprint"] = _stable_sha256(payload)
        return payload


def create_adapter() -> NativeCadBridgeAdapter:
    return NativeCadBridgeAdapter()


def _dwg_failure_code(code: str) -> str:
    return {
        NativeCadBridgeCode.SDK_UNAVAILABLE: DwgFailureCode.ADAPTER_UNAVAILABLE,
        NativeCadBridgeCode.LICENSE_NOT_ALLOWED: DwgFailureCode.FORBIDDEN_LICENSE,
        NativeCadBridgeCode.UNSUPPORTED_VERSION: DwgFailureCode.UNSUPPORTED_VERSION,
        NativeCadBridgeCode.TIMEOUT: DwgFailureCode.IMPORT_TIMEOUT,
        NativeCadBridgeCode.CANCELLED: DwgFailureCode.IMPORT_CANCELLED,
        NativeCadBridgeCode.CORRUPTED_INPUT: DwgFailureCode.CORRUPTED,
        NativeCadBridgeCode.ENCRYPTED_INPUT: DwgFailureCode.ENCRYPTED,
        NativeCadBridgeCode.CONTRACT_INVALID: DwgFailureCode.ADAPTER_FAILED,
    }.get(code, DwgFailureCode.ADAPTER_FAILED)


def _drawing_payload_from_scene_pack(scene_pack: Any, version: DwgVersionInfo) -> dict[str, Any]:
    payload = {
        "header": {"$ACADVER": version.code, "$INSUNITS": 4},
        "layers": copy.deepcopy(scene_pack.layers),
        "blocks": copy.deepcopy(scene_pack.blocks),
        "model_space": copy.deepcopy(scene_pack.entities),
        "metadata": {"derived_from_native_scene_pack": True},
    }
    return payload


def _args_template_from_env() -> tuple[str, ...]:
    raw = os.environ.get(ARGS_JSON_ENV)
    if not raw:
        return ("{input}", "{acadver}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{ARGS_JSON_ENV} must be a JSON array of argument templates.") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"{ARGS_JSON_ENV} must be a JSON array of strings.")
    return tuple(parsed)


def _normalize_supported_versions(value: Sequence[str] | str | None) -> frozenset[str]:
    if value is None:
        raw_items: Sequence[str] = (os.environ.get(SUPPORTED_VERSIONS_ENV) or "").replace(";", ",").split(",")
    elif isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    else:
        raw_items = value
    codes = {str(item).strip().upper() for item in raw_items if str(item or "").strip()}
    if "*" in codes or "ALL" in codes:
        return frozenset({"*"})
    known = set(DwgVersionDetector.SUPPORTED_CODES) | set(DwgVersionDetector.KNOWN_UNSUPPORTED_CODES)
    return frozenset(code for code in codes if code in known)


def _timeout(value: float | None) -> float:
    if value is not None:
        return max(1.0, float(value))
    raw = os.environ.get(TIMEOUT_SECONDS_ENV)
    if not raw:
        return 120.0
    try:
        return max(1.0, float(raw))
    except ValueError as exc:
        raise ValueError(f"{TIMEOUT_SECONDS_ENV} must be numeric.") from exc


def _stable_sha256(payload: Mapping[str, Any]) -> str:
    serializable = {key: value for key, value in payload.items() if key != "fingerprint"}
    encoded = json.dumps(serializable, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ADAPTER_NAME_ENV",
    "ADAPTER_VERSION_ENV",
    "ARGS_JSON_ENV",
    "COMMAND_ENV",
    "NativeCadBridgeAdapter",
    "SUPPORTED_VERSIONS_ENV",
    "TIMEOUT_SECONDS_ENV",
    "create_adapter",
]
