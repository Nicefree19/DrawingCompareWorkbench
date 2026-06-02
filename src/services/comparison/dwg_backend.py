"""DWG backend selection boundary.

This module is intentionally a selector/contract layer, not a converter or a
native parser.  It lets callers choose a DWG backend mode while keeping
unapproved commercial SDKs and user converters as fail-closed placeholders.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .dwg_importer import (
    DwgAdapterDrawing,
    DwgFailureCode,
    DwgImportError,
    DwgImporterAdapter,
    DwgJsonFixtureAdapter,
    DwgVersionInfo,
)


DWG_BACKEND_ENV = "DRAWING_COMPARE_DWG_BACKEND"

DWG_BACKEND_CLEANROOM_NATIVE = "cleanroom_native"
DWG_BACKEND_DISABLED = "disabled"
DWG_BACKEND_COMMERCIAL_SDK = "commercial_sdk"
DWG_BACKEND_USER_CONVERTER = "user_converter"

_BACKEND_ALIASES = {
    "": DWG_BACKEND_CLEANROOM_NATIVE,
    "native": DWG_BACKEND_CLEANROOM_NATIVE,
    "cleanroom": DWG_BACKEND_CLEANROOM_NATIVE,
    "cleanroom-native": DWG_BACKEND_CLEANROOM_NATIVE,
    "cleanroom_native": DWG_BACKEND_CLEANROOM_NATIVE,
    "internal": DWG_BACKEND_CLEANROOM_NATIVE,
    "off": DWG_BACKEND_DISABLED,
    "none": DWG_BACKEND_DISABLED,
    "disable": DWG_BACKEND_DISABLED,
    "disabled": DWG_BACKEND_DISABLED,
    "commercial": DWG_BACKEND_COMMERCIAL_SDK,
    "commercial-sdk": DWG_BACKEND_COMMERCIAL_SDK,
    "commercial_sdk": DWG_BACKEND_COMMERCIAL_SDK,
    "sdk": DWG_BACKEND_COMMERCIAL_SDK,
    "converter": DWG_BACKEND_USER_CONVERTER,
    "user-converter": DWG_BACKEND_USER_CONVERTER,
    "user_converter": DWG_BACKEND_USER_CONVERTER,
}


@dataclass(frozen=True)
class DwgBackendSelection:
    """Selected DWG backend plus provenance suitable for import reports."""

    mode: str
    adapter: DwgImporterAdapter
    source: str
    implementation_status: str
    approval_required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "source": self.source,
            "adapter": self.adapter.name,
            "adapter_version": self.adapter.version,
            "implementation_status": self.implementation_status,
            "approval_required": self.approval_required,
        }


class _UnavailableBackendAdapter(DwgImporterAdapter):
    backend_mode = "unavailable"
    implementation_status = "unavailable"
    approval_required = False

    unavailable_message = "DWG backend is unavailable."

    def is_available(self) -> bool:
        return False

    def supports_version(self, version: DwgVersionInfo) -> bool:
        return False

    def read_file(self, path: str | Path, version: DwgVersionInfo) -> DwgAdapterDrawing:
        raise DwgImportError(
            DwgFailureCode.ADAPTER_UNAVAILABLE,
            self.unavailable_message,
            details={
                "adapter": self.name,
                "backend_mode": self.backend_mode,
                "implementation_status": self.implementation_status,
                "approval_required": self.approval_required,
            },
        )


class DisabledDwgBackendAdapter(_UnavailableBackendAdapter):
    """Explicitly disabled DWG backend."""

    name = "disabled-dwg-backend"
    version = "0"
    license_id = "INTERNAL"
    backend_mode = DWG_BACKEND_DISABLED
    implementation_status = "disabled"
    approval_required = False
    unavailable_message = "DWG backend is disabled by configuration."


class CommercialSdkPlaceholderAdapter(_UnavailableBackendAdapter):
    """Fail-closed placeholder for a future approved commercial SDK."""

    name = "commercial-sdk-placeholder"
    version = "0"
    license_id = "COMMERCIAL-SDK-PENDING"
    backend_mode = DWG_BACKEND_COMMERCIAL_SDK
    implementation_status = "placeholder"
    approval_required = True
    unavailable_message = "Commercial DWG SDK backend is not approved or wired."


class UserConverterPlaceholderAdapter(_UnavailableBackendAdapter):
    """Fail-closed placeholder for a user-provided converter backend."""

    name = "user-converter-placeholder"
    version = "0"
    license_id = "USER-CONVERTER-PENDING"
    backend_mode = DWG_BACKEND_USER_CONVERTER
    implementation_status = "placeholder"
    approval_required = True
    unavailable_message = "User-provided DWG converter backend is not configured."


def normalize_dwg_backend_mode(value: Optional[str]) -> str:
    """Normalize a backend mode or alias into a stable backend id."""

    key = (value or "").strip().casefold().replace(" ", "_")
    key = key.replace("-", "_")
    if key in _BACKEND_ALIASES:
        return _BACKEND_ALIASES[key]
    raise ValueError(
        f"Unknown DWG backend mode {value!r}. "
        f"Expected one of: {', '.join(sorted(set(_BACKEND_ALIASES.values())))}"
    )


def create_dwg_backend_selection(mode: Optional[str] = None) -> DwgBackendSelection:
    """Create the selected DWG backend adapter.

    If *mode* is omitted, ``DRAWING_COMPARE_DWG_BACKEND`` may override the
    default clean-room AC1015 preview.  Placeholder modes never claim version
    support until an approved implementation replaces them.
    """

    raw_mode = mode
    source = "configured"
    if raw_mode is None:
        raw_mode = os.environ.get(DWG_BACKEND_ENV)
        source = "env" if raw_mode is not None else "default"

    normalized = normalize_dwg_backend_mode(raw_mode)
    if normalized == DWG_BACKEND_CLEANROOM_NATIVE:
        from .dwg_native_reader import DwgNativeAc1015Adapter

        adapter = DwgNativeAc1015Adapter(fallback_adapter=DwgJsonFixtureAdapter())
        return DwgBackendSelection(
            mode=normalized,
            adapter=adapter,
            source=source,
            implementation_status=adapter.implementation_status,
            approval_required=adapter.approval_required,
        )
    if normalized == DWG_BACKEND_DISABLED:
        adapter = DisabledDwgBackendAdapter()
    elif normalized == DWG_BACKEND_COMMERCIAL_SDK:
        adapter = CommercialSdkPlaceholderAdapter()
    elif normalized == DWG_BACKEND_USER_CONVERTER:
        adapter = UserConverterPlaceholderAdapter()
    else:  # pragma: no cover - normalize_dwg_backend_mode exhausts known values.
        raise ValueError(f"Unhandled DWG backend mode {normalized!r}.")

    return DwgBackendSelection(
        mode=normalized,
        adapter=adapter,
        source=source,
        implementation_status=adapter.implementation_status,
        approval_required=adapter.approval_required,
    )


def create_dwg_backend_adapter(mode: Optional[str] = None) -> DwgImporterAdapter:
    """Return only the adapter for callers that do not need selection metadata."""

    return create_dwg_backend_selection(mode).adapter


__all__ = [
    "DWG_BACKEND_CLEANROOM_NATIVE",
    "DWG_BACKEND_COMMERCIAL_SDK",
    "DWG_BACKEND_DISABLED",
    "DWG_BACKEND_ENV",
    "DWG_BACKEND_USER_CONVERTER",
    "CommercialSdkPlaceholderAdapter",
    "DisabledDwgBackendAdapter",
    "DwgBackendSelection",
    "UserConverterPlaceholderAdapter",
    "create_dwg_backend_adapter",
    "create_dwg_backend_selection",
    "normalize_dwg_backend_mode",
]
