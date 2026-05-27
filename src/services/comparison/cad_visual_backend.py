# -*- coding: utf-8 -*-
"""CAD visual conversion backend contracts.

The PDF-first viewer can use CAD-to-PDF/image conversion as a visual layer, but
the customer build must not silently enable a licensed or cloud backend. This
module defines the contract and a disabled default backend; concrete adapters
must be registered explicitly elsewhere.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional


CAD_VISUAL_BACKEND_DISABLED = "cad_visual_backend_disabled"
CAD_VISUAL_BACKEND_UNAVAILABLE = "cad_visual_backend_unavailable"
CAD_VISUAL_CONVERSION_CANCELLED = "cad_visual_conversion_cancelled"
CAD_VISUAL_CONVERSION_FAILED = "cad_visual_conversion_failed"
CAD_VISUAL_TIMEOUT = "cad_visual_timeout"

ConversionStatus = Literal["converted", "skipped", "failed", "cancelled"]
OutputFormat = Literal["pdf", "image"]


@dataclass(frozen=True)
class CadVisualBackendCapabilities:
    """Serializable capabilities exposed by one visual backend."""

    backend_id: str
    backend_version: str = ""
    license_id: str = ""
    can_convert_to_pdf: bool = False
    can_convert_to_image: bool = False
    enabled_by_default: bool = False
    requires_network: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "CadVisualBackendCapabilities":
        if not isinstance(data, dict):
            return cls(backend_id="unknown")
        return cls(
            backend_id=str(data.get("backend_id") or "unknown"),
            backend_version=str(data.get("backend_version") or ""),
            license_id=str(data.get("license_id") or ""),
            can_convert_to_pdf=bool(data.get("can_convert_to_pdf")),
            can_convert_to_image=bool(data.get("can_convert_to_image")),
            enabled_by_default=bool(data.get("enabled_by_default")),
            requires_network=bool(data.get("requires_network")),
            notes=[str(item) for item in data.get("notes", []) if str(item)],
        )


@dataclass(frozen=True)
class CadVisualConversionRequest:
    """Input for a single CAD visual conversion."""

    source_path: Path
    output_dir: Path
    output_format: OutputFormat = "pdf"
    backend_id: str = ""
    pair_id: str = ""
    side: str = ""
    page_index: int = 0
    dpi: int = 150
    timeout_s: float = 180.0
    plot_profile_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "output_dir": str(self.output_dir),
            "output_format": self.output_format,
            "backend_id": self.backend_id,
            "pair_id": self.pair_id,
            "side": self.side,
            "page_index": int(self.page_index),
            "dpi": int(self.dpi),
            "timeout_s": float(self.timeout_s),
            "plot_profile_hash": self.plot_profile_hash,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "CadVisualConversionRequest":
        if not isinstance(data, dict):
            raise ValueError("cad visual conversion request must be a JSON object")
        output_format = str(data.get("output_format") or "pdf").lower()
        if output_format not in {"pdf", "image"}:
            output_format = "pdf"
        return cls(
            source_path=Path(str(data.get("source_path") or "")),
            output_dir=Path(str(data.get("output_dir") or "")),
            output_format=output_format,  # type: ignore[arg-type]
            backend_id=str(data.get("backend_id") or ""),
            pair_id=str(data.get("pair_id") or ""),
            side=str(data.get("side") or ""),
            page_index=_safe_int(data.get("page_index")),
            dpi=max(20, _safe_int(data.get("dpi"), default=150)),
            timeout_s=max(1.0, _safe_float(data.get("timeout_s"), default=180.0)),
            plot_profile_hash=str(data.get("plot_profile_hash") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CadVisualConversionResult:
    """Serializable conversion result for manifests and worker JSONL."""

    status: ConversionStatus
    reason_code: str
    source_path: str
    output_path: str = ""
    output_format: OutputFormat = "pdf"
    backend_id: str = ""
    backend_version: str = ""
    license_id: str = ""
    elapsed_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "converted" and bool(self.output_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "ok": self.ok,
            "reason_code": self.reason_code,
            "source_path": self.source_path,
            "output_path": self.output_path,
            "output_format": self.output_format,
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "license_id": self.license_id,
            "elapsed_ms": round(float(self.elapsed_ms), 3),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "CadVisualConversionResult":
        if not isinstance(data, dict):
            return cls(
                status="failed",
                reason_code=CAD_VISUAL_CONVERSION_FAILED,
                source_path="",
                warnings=["invalid conversion result payload"],
            )
        status = str(data.get("status") or "failed").lower()
        if status not in {"converted", "skipped", "failed", "cancelled"}:
            status = "failed"
        output_format = str(data.get("output_format") or "pdf").lower()
        if output_format not in {"pdf", "image"}:
            output_format = "pdf"
        return cls(
            status=status,  # type: ignore[arg-type]
            reason_code=str(data.get("reason_code") or ""),
            source_path=str(data.get("source_path") or ""),
            output_path=str(data.get("output_path") or ""),
            output_format=output_format,  # type: ignore[arg-type]
            backend_id=str(data.get("backend_id") or ""),
            backend_version=str(data.get("backend_version") or ""),
            license_id=str(data.get("license_id") or ""),
            elapsed_ms=_safe_float(data.get("elapsed_ms"), default=0.0),
            warnings=[str(item) for item in data.get("warnings", []) if str(item)],
            metadata=dict(data.get("metadata") or {}),
        )


class CadVisualBackend(ABC):
    """Abstract CAD visual backend."""

    @property
    @abstractmethod
    def capabilities(self) -> CadVisualBackendCapabilities:
        """Return backend capabilities and license metadata."""

    def probe(self) -> CadVisualBackendCapabilities:
        return self.capabilities

    def convert_to_pdf(self, request: CadVisualConversionRequest) -> CadVisualConversionResult:
        return self._unsupported(request, "pdf")

    def convert_to_image(self, request: CadVisualConversionRequest) -> CadVisualConversionResult:
        return self._unsupported(request, "image")

    def convert(self, request: CadVisualConversionRequest) -> CadVisualConversionResult:
        if request.output_format == "image":
            return self.convert_to_image(request)
        return self.convert_to_pdf(request)

    def _unsupported(
        self,
        request: CadVisualConversionRequest,
        output_format: OutputFormat,
    ) -> CadVisualConversionResult:
        caps = self.capabilities
        return CadVisualConversionResult(
            status="skipped",
            reason_code=CAD_VISUAL_BACKEND_UNAVAILABLE,
            source_path=str(request.source_path),
            output_format=output_format,
            backend_id=caps.backend_id,
            backend_version=caps.backend_version,
            license_id=caps.license_id,
            warnings=[f"backend {caps.backend_id} does not support {output_format} conversion"],
        )


class DisabledCadVisualBackend(CadVisualBackend):
    """Default backend used when no approved converter is configured."""

    def __init__(self, backend_id: str = "disabled") -> None:
        self._capabilities = CadVisualBackendCapabilities(
            backend_id=backend_id,
            backend_version="0",
            license_id="none",
            can_convert_to_pdf=False,
            can_convert_to_image=False,
            enabled_by_default=False,
            requires_network=False,
            notes=["No approved CAD visual conversion backend is configured."],
        )

    @property
    def capabilities(self) -> CadVisualBackendCapabilities:
        return self._capabilities

    def convert(self, request: CadVisualConversionRequest) -> CadVisualConversionResult:
        caps = self.capabilities
        return CadVisualConversionResult(
            status="skipped",
            reason_code=CAD_VISUAL_BACKEND_DISABLED,
            source_path=str(request.source_path),
            output_format=request.output_format,
            backend_id=caps.backend_id,
            backend_version=caps.backend_version,
            license_id=caps.license_id,
            warnings=["CAD visual conversion backend is disabled by default."],
        )


def conversion_result_from_exception(
    request: CadVisualConversionRequest,
    exc: BaseException,
    *,
    backend_id: str = "",
    backend_version: str = "",
    license_id: str = "",
    elapsed_ms: float = 0.0,
) -> CadVisualConversionResult:
    return CadVisualConversionResult(
        status="failed",
        reason_code=CAD_VISUAL_CONVERSION_FAILED,
        source_path=str(request.source_path),
        output_format=request.output_format,
        backend_id=backend_id,
        backend_version=backend_version,
        license_id=license_id,
        elapsed_ms=elapsed_ms,
        warnings=[f"{type(exc).__name__}: {exc}"],
    )


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "CAD_VISUAL_BACKEND_DISABLED",
    "CAD_VISUAL_BACKEND_UNAVAILABLE",
    "CAD_VISUAL_CONVERSION_CANCELLED",
    "CAD_VISUAL_CONVERSION_FAILED",
    "CAD_VISUAL_TIMEOUT",
    "CadVisualBackend",
    "CadVisualBackendCapabilities",
    "CadVisualConversionRequest",
    "CadVisualConversionResult",
    "DisabledCadVisualBackend",
    "conversion_result_from_exception",
]
