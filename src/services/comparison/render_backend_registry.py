# -*- coding: utf-8 -*-
"""Registry for optional visual render/conversion backends."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .cad_visual_backend import (
    CAD_VISUAL_BACKEND_DISABLED,
    CAD_VISUAL_BACKEND_UNAVAILABLE,
    CadVisualBackend,
    CadVisualBackendCapabilities,
    CadVisualConversionRequest,
    CadVisualConversionResult,
    DisabledCadVisualBackend,
)

CAD_VISUAL_BACKEND_ENV = "DRAWING_COMPARE_CAD_VISUAL_BACKEND"
DEFAULT_DISABLED_BACKEND_ID = "disabled"


@dataclass
class RenderBackendRegistry:
    """Small explicit registry for CAD visual backends."""

    backends: dict[str, CadVisualBackend] = field(default_factory=dict)

    def register(self, backend: CadVisualBackend, *, replace: bool = False) -> None:
        backend_id = backend.capabilities.backend_id
        if not backend_id:
            raise ValueError("backend_id is required")
        if not replace and backend_id in self.backends:
            raise ValueError(f"backend already registered: {backend_id}")
        self.backends[backend_id] = backend

    def capabilities(self) -> list[CadVisualBackendCapabilities]:
        return [backend.capabilities for backend in self.backends.values()]

    def get(self, backend_id: str) -> Optional[CadVisualBackend]:
        return self.backends.get(str(backend_id or ""))

    def resolve_cad_visual_backend(
        self,
        backend_id: str = "",
        *,
        allow_env: bool = True,
    ) -> CadVisualBackend:
        requested = str(backend_id or "").strip()
        if not requested and allow_env:
            requested = str(os.environ.get(CAD_VISUAL_BACKEND_ENV) or "").strip()
        if not requested:
            requested = DEFAULT_DISABLED_BACKEND_ID
        backend = self.get(requested)
        if backend is not None:
            return backend
        return DisabledCadVisualBackend(requested or DEFAULT_DISABLED_BACKEND_ID)

    def convert_cad_visual(
        self,
        request: CadVisualConversionRequest,
        *,
        allow_env: bool = True,
    ) -> CadVisualConversionResult:
        backend = self.resolve_cad_visual_backend(request.backend_id, allow_env=allow_env)
        caps = backend.probe()
        if request.output_format == "pdf" and not caps.can_convert_to_pdf:
            reason = (
                CAD_VISUAL_BACKEND_DISABLED
                if caps.backend_id == DEFAULT_DISABLED_BACKEND_ID or not caps.enabled_by_default
                else CAD_VISUAL_BACKEND_UNAVAILABLE
            )
            return CadVisualConversionResult(
                status="skipped",
                reason_code=reason,
                source_path=str(request.source_path),
                output_format=request.output_format,
                backend_id=caps.backend_id,
                backend_version=caps.backend_version,
                license_id=caps.license_id,
                warnings=list(caps.notes) or ["CAD visual PDF conversion unavailable."],
            )
        if request.output_format == "image" and not caps.can_convert_to_image:
            return CadVisualConversionResult(
                status="skipped",
                reason_code=CAD_VISUAL_BACKEND_UNAVAILABLE,
                source_path=str(request.source_path),
                output_format=request.output_format,
                backend_id=caps.backend_id,
                backend_version=caps.backend_version,
                license_id=caps.license_id,
                warnings=list(caps.notes) or ["CAD visual image conversion unavailable."],
            )
        return backend.convert(request)


def get_default_render_backend_registry() -> RenderBackendRegistry:
    registry = RenderBackendRegistry()
    registry.register(DisabledCadVisualBackend(DEFAULT_DISABLED_BACKEND_ID))
    for backend in _planned_disabled_backends():
        registry.register(backend)
    return registry


def _planned_disabled_backends() -> Iterable[CadVisualBackend]:
    # Descriptors only: no imports, no executable probing, no runtime enablement.
    for backend_id, license_id, notes in (
        ("local_autocad_plot", "external_enterprise_license", ["Requires approved local CAD installation."]),
        ("qcad_professional_cli", "external_commercial_license", ["Requires separately licensed CLI."]),
        ("aspose_cad", "external_commercial_license", ["Requires commercial library approval."]),
        ("autodesk_aps_design_automation", "external_cloud_service", ["Requires cloud upload approval."]),
    ):
        backend = DisabledCadVisualBackend(backend_id)
        object.__setattr__(
            backend.capabilities,
            "license_id",
            license_id,
        )
        object.__setattr__(
            backend.capabilities,
            "notes",
            notes,
        )
        yield backend


__all__ = [
    "CAD_VISUAL_BACKEND_ENV",
    "DEFAULT_DISABLED_BACKEND_ID",
    "RenderBackendRegistry",
    "get_default_render_backend_registry",
]
