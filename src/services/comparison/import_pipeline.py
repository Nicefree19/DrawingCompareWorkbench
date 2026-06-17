"""Canonical CAD import and compare pipeline.

This is the ODA-free path for drawing comparison:

    source file -> ImportPipeline -> CanonicalDrawing
                -> DrawingNormalizer -> DrawingCompareEngine

ODA conversion is intentionally not imported or invoked unless the caller
explicitly enables the isolated fallback option.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .base import ComparisonResult
from .cad_stability import CadLimitCode, CadStabilityLimits, CancelCallback
from .drawing_compare_engine import (
    DrawingCompareEngine,
    DrawingCompareOptions,
    DrawingDiffResult,
)
from .drawing_normalizer import (
    DrawingNormalizer,
    NormalizationOptions,
    NormalizationReport,
)
from .dwg_importer import (
    DwgFailureCode,
    DwgImporter,
    DwgImporterAdapter,
    DwgVersionDetector,
)
from .dwg_backend import (
    DWG_BACKEND_ENV,
    DWG_BACKEND_ODA_CONVERTER,
    DWG_BACKEND_USER_CONVERTER,
    create_dwg_backend_selection,
    normalize_dwg_backend_mode,
)
from .dwg_dxf_fallback import resolve_dwg_dxf_fallback_pair
from .dxf_importer import DxfImporter, DxfImportLimitError, DxfParseError


class CadPipelineStatus:
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class CadPipelineErrorCode:
    UNSUPPORTED_FORMAT = "CAD_UNSUPPORTED_FORMAT"
    READ_FAILED = "CAD_READ_FAILED"
    DXF_PARSE_ERROR = "DXF_PARSE_ERROR"
    DWG_IMPORT_FAILED = "DWG_IMPORT_FAILED"
    ODA_FALLBACK_DISABLED = "ODA_FALLBACK_DISABLED"
    ODA_FALLBACK_FAILED = "ODA_FALLBACK_FAILED"
    USER_CONVERTER_FAILED = "USER_CONVERTER_FAILED"
    COMPARE_IMPORT_FAILED = "COMPARE_IMPORT_FAILED"
    COMPARE_FAILED = "COMPARE_FAILED"
    COMPARE_UNSUPPORTED_FORMAT_PAIR = "COMPARE_UNSUPPORTED_FORMAT_PAIR"
    IMPORT_TIMEOUT = CadLimitCode.IMPORT_TIMEOUT
    IMPORT_CANCELLED = CadLimitCode.IMPORT_CANCELLED
    ENTITY_LIMIT_EXCEEDED = CadLimitCode.ENTITY_LIMIT_EXCEEDED
    TOKEN_LIMIT_EXCEEDED = CadLimitCode.TOKEN_LIMIT_EXCEEDED


USER_CONVERTED_DXF_DEFAULT_MAX_TOKENS = 12_000_000
USER_CONVERTER_CACHE_NAMESPACE = "dwg-user-converter-v1"
ODA_FALLBACK_CACHE_NAMESPACE = "dwg-oda-fallback-v1"
ODA_FALLBACK_DEFAULT_OUTPUT_VERSION = "ACAD2018"
ODA_FALLBACK_DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class ImportPipelineOptions:
    """Options for selecting importers and normalizing CanonicalDrawing."""

    expand_blocks: bool = True
    normalize: bool = True
    normalization_options: NormalizationOptions = field(default_factory=NormalizationOptions)
    dwg_adapter: Optional[DwgImporterAdapter] = None
    dwg_backend_mode: Optional[str] = None
    allowed_dwg_license_ids: Sequence[str] = ("MIT", "INTERNAL")
    allow_oda_fallback: bool = False
    user_converter_path: Optional[str] = None
    user_conversion_args: Sequence[str] = ()
    user_conversion_timeout_seconds: Optional[float] = ODA_FALLBACK_DEFAULT_TIMEOUT_SECONDS
    oda_converter_path: Optional[str] = None
    oda_conversion_timeout_seconds: Optional[float] = ODA_FALLBACK_DEFAULT_TIMEOUT_SECONDS
    dwg_conversion_cache_dir: Optional[str | Path] = None
    stability_limits: CadStabilityLimits = field(default_factory=CadStabilityLimits)
    cancel_callback: Optional[CancelCallback] = None


@dataclass
class ImportPipelineResult:
    source_path: str
    source_format: str
    status: str
    importer: str
    error_code: Optional[str] = None
    message: str = ""
    version: Optional[Dict[str, Any]] = None
    canonical_drawing: Optional[Dict[str, Any]] = None
    normalized_drawing: Optional[Dict[str, Any]] = None
    import_report: Dict[str, Any] = field(default_factory=dict)
    normalization_report: Optional[Dict[str, Any]] = None
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def is_failed(self) -> bool:
        return self.status == CadPipelineStatus.FAILED

    @property
    def is_partial(self) -> bool:
        return self.status == CadPipelineStatus.PARTIAL

    @property
    def user_message(self) -> str:
        if self.message:
            return self.message
        if self.status == CadPipelineStatus.OK:
            return "Import completed."
        if self.status == CadPipelineStatus.PARTIAL:
            return "Import completed with warnings. Some entities may be missing."
        return "Import failed."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_format": self.source_format,
            "status": self.status,
            "importer": self.importer,
            "error_code": self.error_code,
            "message": self.user_message,
            "version": self.version,
            "import_report": self.import_report,
            "normalization_report": self.normalization_report,
            "warnings": self.warnings,
            "elapsed_ms": self.elapsed_ms,
            "entity_count": len((self.normalized_drawing or self.canonical_drawing or {}).get("entities") or []),
            "layer_count": len((self.normalized_drawing or self.canonical_drawing or {}).get("layers") or []),
            "bbox": (self.normalized_drawing or self.canonical_drawing or {}).get("extents"),
        }


@dataclass(frozen=True)
class ComparePipelineOptions:
    import_options: ImportPipelineOptions = field(default_factory=ImportPipelineOptions)
    compare_options: DrawingCompareOptions = field(default_factory=DrawingCompareOptions)
    require_same_format_family: bool = False
    include_layers: Optional[Sequence[str]] = None
    exclude_layers: Optional[Sequence[str]] = None


@dataclass
class ComparePipelineResult:
    source_a: str
    source_b: str
    status: str
    imports: Dict[str, ImportPipelineResult]
    diff: Optional[DrawingDiffResult] = None
    error_code: Optional[str] = None
    message: str = ""
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    input_resolution: Dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    @property
    def is_failed(self) -> bool:
        return self.status == CadPipelineStatus.FAILED

    @property
    def is_partial(self) -> bool:
        return self.status == CadPipelineStatus.PARTIAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_a": self.source_a,
            "source_b": self.source_b,
            "status": self.status,
            "error_code": self.error_code,
            "message": self.message,
            "imports": {
                key: value.to_dict()
                for key, value in self.imports.items()
            },
            "summary": self.diff.summary if self.diff else None,
            "diff": self.diff.to_dict() if self.diff else None,
            "warnings": self.warnings,
            "input_resolution": self.input_resolution,
            "elapsed_ms": self.elapsed_ms,
            "partial_imports": [
                key for key, value in self.imports.items()
                if value.status == CadPipelineStatus.PARTIAL
            ],
        }

    def to_comparison_result(self) -> ComparisonResult:
        result = ComparisonResult(source_a=self.source_a, source_b=self.source_b)
        if self.diff:
            for change in self.diff.to_change_records(include_unchanged=False):
                result.add_change(change)
            result.unchanged_count = int((self.diff.summary or {}).get("unchanged") or 0)
        result.warnings.extend(_warning_messages(self.warnings))
        result.metadata = {
            "comparison_type": "CAD_CANONICAL",
            "pipeline_status": self.status,
            "error_code": self.error_code,
            "message": self.message,
            "partial_imports": [
                key for key, value in self.imports.items()
                if value.status == CadPipelineStatus.PARTIAL
            ],
            "imports": {
                key: value.to_dict()
                for key, value in self.imports.items()
            },
            "input_resolution": self.input_resolution,
            "dwg_dxf_fallback": self.input_resolution,
            "diff_summary": self.diff.summary if self.diff else None,
        }
        return result


class ImportPipeline:
    """Choose DXF/DWG importer, build CanonicalDrawing, and normalize it."""

    def __init__(self, options: Optional[ImportPipelineOptions] = None):
        self.options = options or ImportPipelineOptions()

    def select_importer(self, path: str | Path) -> Dict[str, Any]:
        """Return the importer decision without reading full drawing content."""

        path = Path(path)
        suffix = path.suffix.lower()
        if suffix == ".dxf":
            return {
                "source_format": "dxf",
                "importer": "DxfImporter",
                "version": _detect_dxf_version(path),
                "supported": True,
                "error_code": None,
            }
        if suffix == ".dwg":
            try:
                version_info = DwgVersionDetector.detect_file(path)
                supported = _dwg_adapter_supports_version(
                    self.options.dwg_adapter,
                    version_info,
                    backend_mode=self.options.dwg_backend_mode,
                )
                version = version_info.to_dict()
                return {
                    "source_format": "dwg",
                    "importer": "DwgImporter",
                    "version": version,
                    "supported": supported,
                    "error_code": None if supported else DwgFailureCode.UNSUPPORTED_VERSION,
                }
            except Exception as exc:
                return {
                    "source_format": "dwg",
                    "importer": "DwgImporter",
                    "version": None,
                    "supported": False,
                    "error_code": DwgFailureCode.CORRUPTED,
                    "message": str(exc),
                }
        return {
            "source_format": suffix.lstrip(".") or "unknown",
            "importer": None,
            "version": None,
            "supported": False,
            "error_code": CadPipelineErrorCode.UNSUPPORTED_FORMAT,
        }

    def import_file(self, path: str | Path) -> ImportPipelineResult:
        started = time.perf_counter()
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix == ".dxf":
            result = self._import_dxf(path)
        elif suffix == ".dwg":
            result = self._import_dwg(path)
        else:
            result = ImportPipelineResult(
                source_path=str(path),
                source_format=suffix.lstrip(".") or "unknown",
                status=CadPipelineStatus.FAILED,
                importer="none",
                error_code=CadPipelineErrorCode.UNSUPPORTED_FORMAT,
                message=f"Unsupported CAD format: {suffix or '(none)'}",
            )
        result.elapsed_ms = (time.perf_counter() - started) * 1000.0
        return result

    def _import_dxf(self, path: Path) -> ImportPipelineResult:
        try:
            limits = _effective_stability_limits(self.options)
            importer = DxfImporter(
                expand_blocks=self.options.expand_blocks,
                max_block_depth=limits.max_block_depth,
                max_entities=limits.max_entities,
                max_tokens=limits.max_dxf_tokens,
                timeout_seconds=limits.import_timeout_seconds,
                cancel_callback=self.options.cancel_callback,
            )
            canonical = importer.import_file(path)
        except DxfImportLimitError as exc:
            return self._failed(
                path,
                "dxf",
                "DxfImporter",
                exc.code,
                str(exc),
                details=exc.details,
            )
        except DxfParseError as exc:
            return self._failed(
                path,
                "dxf",
                "DxfImporter",
                CadPipelineErrorCode.DXF_PARSE_ERROR,
                f"DXF parse failed: {exc}",
            )
        except OSError as exc:
            return self._failed(
                path,
                "dxf",
                "DxfImporter",
                CadPipelineErrorCode.READ_FAILED,
                f"DXF read failed: {exc}",
            )
        return self._finalize(path, "dxf", "DxfImporter", canonical)

    def _import_dwg(self, path: Path) -> ImportPipelineResult:
        version = None
        try:
            version = DwgVersionDetector.detect_file(path).to_dict()
        except Exception:
            version = None

        limits = _effective_stability_limits(self.options)
        importer = DwgImporter(
            adapter=self.options.dwg_adapter,
            backend_mode=self.options.dwg_backend_mode,
            allowed_license_ids=self.options.allowed_dwg_license_ids,
            max_entities=limits.max_entities,
            timeout_seconds=limits.import_timeout_seconds,
            cancel_callback=self.options.cancel_callback,
        )
        canonical = importer.import_file(path)
        report = canonical.get("import_report") or {}
        if (
            report.get("status") == CadPipelineStatus.FAILED
            and _uses_user_converter_backend(self.options)
            and self.options.user_converter_path
        ):
            return self._import_dwg_via_user_converter(path, version)
        if report.get("status") == CadPipelineStatus.FAILED and self.options.allow_oda_fallback:
            return self._import_dwg_via_oda_fallback(path, version)
        result = self._finalize(path, "dwg", "DwgImporter", canonical)
        result.version = version or report.get("dwg_version")
        if result.status == CadPipelineStatus.FAILED and not self.options.allow_oda_fallback:
            dwg_error = report.get("error_code")
            result.error_code = dwg_error or CadPipelineErrorCode.DWG_IMPORT_FAILED
            result.message = _dwg_user_message(dwg_error)
        return result

    def _import_dwg_via_user_converter(
        self,
        path: Path,
        version: Optional[Dict[str, Any]],
    ) -> ImportPipelineResult:
        cache_details: Dict[str, Any] = {}
        try:
            cached_dxf, metadata_path, cache_details = _user_converter_cache_paths(path, self.options)
            cache_hit = cached_dxf.exists()
            cache_details["hit"] = cache_hit
            cache_details["metadata_path"] = str(metadata_path)
            if not cache_hit:
                from .user_dwg_converter import UserDwgConverter

                converter_started = time.perf_counter()
                converter = UserDwgConverter(
                    self.options.user_converter_path,
                    args_template=self.options.user_conversion_args,
                )
                converted = converter.convert(
                    path,
                    timeout=_user_conversion_timeout_seconds(self.options),
                )
                try:
                    _copy_converted_dxf_to_cache(converted, cached_dxf)
                finally:
                    converter.cleanup_converted_output(converted)
                cache_details["conversion_elapsed_ms"] = (time.perf_counter() - converter_started) * 1000.0
                cache_details["converter_path"] = str(converter.converter_path)
                _write_converter_cache_metadata(metadata_path, USER_CONVERTER_CACHE_NAMESPACE, cache_details)

            _slim_converted_dxf_before_budget(cached_dxf, cache_details)
            budget_failure = _converted_dxf_budget_failure(cached_dxf, self.options)
            if budget_failure is not None:
                failed = self._failed(
                    path,
                    "dwg",
                    "DwgImporter:user-converter",
                    budget_failure["error_code"],
                    budget_failure["message"],
                    version=version,
                    details={
                        "fallback": {
                            "user_converter": True,
                            "cache": cache_details,
                        },
                        **budget_failure["details"],
                    },
                )
                failed.import_report.setdefault("fallback", {}).update(
                    {
                        "user_converter": True,
                        "cache": cache_details,
                    }
                )
                return failed

            result = self._import_dxf(cached_dxf)
            result.source_path = str(path)
            result.source_format = "dwg"
            result.importer = "DwgImporter:user-converter"
            result.version = version
            if result.import_report:
                result.import_report.setdefault("fallback", {}).update(
                    {
                        "user_converter": True,
                        "cache": cache_details,
                    }
                )
            return result
        except Exception as exc:
            failed = self._failed(
                path,
                "dwg",
                "DwgImporter:user-converter",
                CadPipelineErrorCode.USER_CONVERTER_FAILED,
                f"User converter failed: {exc}",
                version=version,
                details={
                    "fallback": {
                        "user_converter": True,
                        "cache": cache_details,
                    },
                    "exception_type": type(exc).__name__,
                },
            )
            failed.import_report.setdefault("fallback", {}).update(
                {
                    "user_converter": True,
                    "cache": cache_details,
                }
            )
            return failed

    def _import_dwg_via_oda_fallback(
        self,
        path: Path,
        version: Optional[Dict[str, Any]],
    ) -> ImportPipelineResult:
        cache_details: Dict[str, Any] = {}
        try:
            cached_dxf, metadata_path, cache_details = _oda_fallback_cache_paths(path, self.options)
            cache_hit = cached_dxf.exists()
            cache_details["hit"] = cache_hit
            cache_details["metadata_path"] = str(metadata_path)
            if not cache_hit:
                from .dwg_converter import DwgConverter

                converter_started = time.perf_counter()
                converter = DwgConverter(self.options.oda_converter_path)
                converted = converter.convert(
                    path,
                    output_version=ODA_FALLBACK_DEFAULT_OUTPUT_VERSION,
                    timeout=_oda_conversion_timeout_seconds(self.options),
                )
                try:
                    _copy_converted_dxf_to_cache(converted, cached_dxf)
                finally:
                    _cleanup_oda_converter_output(converted, cached_dxf.parent)
                cache_details["conversion_elapsed_ms"] = (time.perf_counter() - converter_started) * 1000.0
                cache_details["converter_path"] = str(getattr(converter, "oda_path", "") or "")
                _write_converter_cache_metadata(metadata_path, ODA_FALLBACK_CACHE_NAMESPACE, cache_details)

            _slim_converted_dxf_before_budget(cached_dxf, cache_details)
            budget_failure = _converted_dxf_budget_failure(cached_dxf, self.options)
            if budget_failure is not None:
                failed = self._failed(
                    path,
                    "dwg",
                    "DwgImporter:oda-fallback",
                    budget_failure["error_code"],
                    budget_failure["message"],
                    version=version,
                    details={
                        "fallback": {
                            "oda_converter": True,
                            "cache": cache_details,
                        },
                        **budget_failure["details"],
                    },
                )
                failed.import_report.setdefault("fallback", {}).update(
                    {
                        "oda_converter": True,
                        "cache": cache_details,
                    }
                )
                return failed

            result = self._import_dxf(cached_dxf)
            result.source_path = str(path)
            result.source_format = "dwg"
            result.importer = "DwgImporter:oda-fallback"
            result.version = version
            if result.import_report:
                result.import_report.setdefault("fallback", {}).update(
                    {
                        "oda_converter": True,
                        "cache": cache_details,
                    }
                )
            return result
        except Exception as exc:
            failed = self._failed(
                path,
                "dwg",
                "DwgImporter:oda-fallback",
                CadPipelineErrorCode.ODA_FALLBACK_FAILED,
                f"ODA fallback failed: {exc}",
                version=version,
                details={
                    "fallback": {
                        "oda_converter": True,
                        "cache": cache_details,
                    },
                    "exception_type": type(exc).__name__,
                },
            )
            failed.import_report.setdefault("fallback", {}).update(
                {
                    "oda_converter": True,
                    "cache": cache_details,
                }
            )
            return failed

    def _finalize(
        self,
        path: Path,
        source_format: str,
        importer_name: str,
        canonical: Dict[str, Any],
    ) -> ImportPipelineResult:
        report = dict(canonical.get("import_report") or {})
        metadata = canonical.get("metadata") if isinstance(canonical.get("metadata"), dict) else {}
        adapter_metadata = metadata.get("adapter_metadata") if isinstance(metadata, dict) else None
        if isinstance(adapter_metadata, dict) and adapter_metadata:
            report_metadata = report.setdefault("metadata", {})
            if isinstance(report_metadata, dict):
                report_metadata.setdefault("adapter_metadata", adapter_metadata)
        status = str(report.get("status") or CadPipelineStatus.OK)
        error_code = report.get("error_code")
        warnings = list(report.get("warnings") or [])
        normalized = None
        normalization_report = None

        if status != CadPipelineStatus.FAILED and self.options.normalize:
            normalized, report_obj = DrawingNormalizer(self.options.normalization_options).normalize(canonical)
            normalization_report = report_obj.to_dict()

        return ImportPipelineResult(
            source_path=str(path),
            source_format=source_format,
            status=status,
            importer=importer_name,
            error_code=error_code,
            message=_status_message(source_format, status, error_code, warnings),
            version=report.get("dwg_version") or _acad_version(canonical),
            canonical_drawing=canonical,
            normalized_drawing=normalized,
            import_report=report,
            normalization_report=normalization_report,
            warnings=warnings,
        )

    def _failed(
        self,
        path: Path,
        source_format: str,
        importer_name: str,
        error_code: str,
        message: str,
        *,
        version: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> ImportPipelineResult:
        warning = {
            "code": error_code,
            "severity": "error",
            "message": message,
            "details": details or {},
        }
        return ImportPipelineResult(
            source_path=str(path),
            source_format=source_format,
            status=CadPipelineStatus.FAILED,
            importer=importer_name,
            error_code=error_code,
            message=message,
            version=version,
            warnings=[warning],
            import_report={
                "status": CadPipelineStatus.FAILED,
                "error_code": error_code,
                "warnings": [warning],
                "unsupported_entities": [],
                "stats": {
                    "raw_entity_count": 0,
                    "canonical_entity_count": 0,
                    "unsupported_entity_count": 0,
                },
            },
        )


class ComparePipeline:
    """Run ODA-free canonical CAD comparison for DXF/DWG sources."""

    def __init__(self, options: Optional[ComparePipelineOptions] = None):
        self.options = options or ComparePipelineOptions()
        self.import_pipeline = ImportPipeline(self.options.import_options)

    def compare(self, source_a: str | Path, source_b: str | Path) -> ComparePipelineResult:
        started = time.perf_counter()
        source_a = Path(source_a)
        source_b = Path(source_b)
        effective_source_a = source_a
        effective_source_b = source_b
        input_resolution: Dict[str, Any] = {}
        input_resolution_warning: Optional[Dict[str, Any]] = None

        if _uses_user_converter_backend(self.options.import_options):
            resolution = resolve_dwg_dxf_fallback_pair(source_a, source_b)
            input_resolution = resolution.to_dict()
            effective_source_a = resolution.effective_source_a
            effective_source_b = resolution.effective_source_b
            if resolution.used:
                input_resolution_warning = {
                    "code": "DWG_CONVERTED_DXF_FALLBACK",
                    "severity": "info",
                    "message": (
                        "Original DWG inputs were compared through user-provided "
                        "converted DXF files."
                    ),
                    "details": input_resolution,
                }
        imports = {
            "a": self.import_pipeline.import_file(effective_source_a),
            "b": self.import_pipeline.import_file(effective_source_b),
        }
        warnings = [
            *imports["a"].warnings,
            *imports["b"].warnings,
        ]
        if input_resolution_warning is not None:
            warnings.append(input_resolution_warning)

        if self.options.require_same_format_family and imports["a"].source_format != imports["b"].source_format:
            return self._failed_compare(
                source_a,
                source_b,
                imports,
                CadPipelineErrorCode.COMPARE_UNSUPPORTED_FORMAT_PAIR,
                "CAD compare requires matching source formats.",
                warnings,
                started,
                input_resolution=input_resolution,
            )

        failed = {key: value for key, value in imports.items() if value.is_failed}
        if failed:
            failed_codes = {
                key: value.error_code
                for key, value in failed.items()
            }
            return self._failed_compare(
                source_a,
                source_b,
                imports,
                CadPipelineErrorCode.COMPARE_IMPORT_FAILED,
                f"CAD compare import failed: {failed_codes}",
                warnings,
                started,
                input_resolution=input_resolution,
            )

        old_drawing = _filter_drawing_layers(
            imports["a"].normalized_drawing or imports["a"].canonical_drawing or {},
            include_layers=self.options.include_layers,
            exclude_layers=self.options.exclude_layers,
        )
        new_drawing = _filter_drawing_layers(
            imports["b"].normalized_drawing or imports["b"].canonical_drawing or {},
            include_layers=self.options.include_layers,
            exclude_layers=self.options.exclude_layers,
        )
        try:
            diff = DrawingCompareEngine(self.options.compare_options).compare(
                old_drawing,
                new_drawing,
            )
        except Exception as exc:
            return self._failed_compare(
                source_a,
                source_b,
                imports,
                CadPipelineErrorCode.COMPARE_FAILED,
                f"CAD compare failed: {exc}",
                warnings,
                started,
                input_resolution=input_resolution,
            )

        status = (
            CadPipelineStatus.PARTIAL
            if any(value.is_partial for value in imports.values())
            else CadPipelineStatus.OK
        )
        return ComparePipelineResult(
            source_a=str(source_a),
            source_b=str(source_b),
            status=status,
            imports=imports,
            diff=diff,
            error_code=None,
            message=_compare_status_message(status, imports),
            warnings=warnings,
            input_resolution=input_resolution,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _failed_compare(
        self,
        source_a: Path,
        source_b: Path,
        imports: Dict[str, ImportPipelineResult],
        error_code: str,
        message: str,
        warnings: List[Dict[str, Any]],
        started: float,
        *,
        input_resolution: Optional[Dict[str, Any]] = None,
    ) -> ComparePipelineResult:
        return ComparePipelineResult(
            source_a=str(source_a),
            source_b=str(source_b),
            status=CadPipelineStatus.FAILED,
            imports=imports,
            diff=None,
            error_code=error_code,
            message=message,
            warnings=warnings,
            input_resolution=input_resolution or {},
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )


def _oda_fallback_cache_paths(path: Path, options: ImportPipelineOptions) -> tuple[Path, Path, Dict[str, Any]]:
    from .cache_paths import normalize_cache_dir
    from .source_signature import build_source_signature, source_cache_filename

    cache_root = (
        Path(options.dwg_conversion_cache_dir)
        if options.dwg_conversion_cache_dir
        else normalize_cache_dir() / "oda_converter"
    )
    cache_root.mkdir(parents=True, exist_ok=True)
    config_fingerprint = _oda_cache_config_fingerprint(options)
    filename = source_cache_filename(
        path,
        namespace=ODA_FALLBACK_CACHE_NAMESPACE,
        extension=".dxf",
        importer_version=ODA_FALLBACK_CACHE_NAMESPACE,
        render_backend_id=DWG_BACKEND_ODA_CONVERTER,
        config_fingerprint=config_fingerprint,
        include_sample_hash=False,
        digest_length=24,
    )
    cached_dxf = cache_root / filename
    metadata_path = cached_dxf.with_suffix(".json")
    source_signature = build_source_signature(
        path,
        importer_version=ODA_FALLBACK_CACHE_NAMESPACE,
        render_backend_id=DWG_BACKEND_ODA_CONVERTER,
        config_fingerprint=config_fingerprint,
        include_sample_hash=False,
    )
    details: Dict[str, Any] = {
        "cache_namespace": ODA_FALLBACK_CACHE_NAMESPACE,
        "cache_path": str(cached_dxf),
        "converted_dxf_path": str(cached_dxf),
        "cache_key": cached_dxf.stem,
        "source_dwg": str(path),
        "source_signature": source_signature,
        "output_version": ODA_FALLBACK_DEFAULT_OUTPUT_VERSION,
    }
    return cached_dxf, metadata_path, details


def _user_converter_cache_paths(path: Path, options: ImportPipelineOptions) -> tuple[Path, Path, Dict[str, Any]]:
    from .cache_paths import normalize_cache_dir
    from .source_signature import build_source_signature, source_cache_filename

    cache_root = (
        Path(options.dwg_conversion_cache_dir)
        if options.dwg_conversion_cache_dir
        else normalize_cache_dir() / "user_converter"
    )
    cache_root.mkdir(parents=True, exist_ok=True)
    config_fingerprint = _user_converter_cache_config_fingerprint(options)
    filename = source_cache_filename(
        path,
        namespace=USER_CONVERTER_CACHE_NAMESPACE,
        extension=".dxf",
        importer_version=USER_CONVERTER_CACHE_NAMESPACE,
        render_backend_id=DWG_BACKEND_USER_CONVERTER,
        config_fingerprint=config_fingerprint,
        include_sample_hash=False,
        digest_length=24,
    )
    cached_dxf = cache_root / filename
    metadata_path = cached_dxf.with_suffix(".json")
    source_signature = build_source_signature(
        path,
        importer_version=USER_CONVERTER_CACHE_NAMESPACE,
        render_backend_id=DWG_BACKEND_USER_CONVERTER,
        config_fingerprint=config_fingerprint,
        include_sample_hash=False,
    )
    details: Dict[str, Any] = {
        "cache_namespace": USER_CONVERTER_CACHE_NAMESPACE,
        "cache_path": str(cached_dxf),
        "converted_dxf_path": str(cached_dxf),
        "cache_key": cached_dxf.stem,
        "source_dwg": str(path),
        "source_signature": source_signature,
        "args_template": list(options.user_conversion_args or ()),
    }
    return cached_dxf, metadata_path, details


def _oda_cache_config_fingerprint(options: ImportPipelineOptions) -> str:
    payload = {
        "converter_path": str(options.oda_converter_path or ""),
        "output_version": ODA_FALLBACK_DEFAULT_OUTPUT_VERSION,
        "schema": ODA_FALLBACK_CACHE_NAMESPACE,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _user_converter_cache_config_fingerprint(options: ImportPipelineOptions) -> str:
    payload = {
        "converter_path": str(options.user_converter_path or ""),
        "args_template": list(options.user_conversion_args or ()),
        "schema": USER_CONVERTER_CACHE_NAMESPACE,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _oda_conversion_timeout_seconds(options: ImportPipelineOptions) -> int:
    value = options.oda_conversion_timeout_seconds
    if value is None or value <= 0:
        return int(ODA_FALLBACK_DEFAULT_TIMEOUT_SECONDS)
    return max(1, int(value))


def _user_conversion_timeout_seconds(options: ImportPipelineOptions) -> int:
    value = options.user_conversion_timeout_seconds
    if value is None or value <= 0:
        return int(ODA_FALLBACK_DEFAULT_TIMEOUT_SECONDS)
    return max(1, int(value))


def _copy_converted_dxf_to_cache(converted: str | Path, cached_dxf: Path) -> None:
    cached_dxf.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cached_dxf.with_suffix(f"{cached_dxf.suffix}.tmp")
    try:
        shutil.copy2(converted, temp_path)
        temp_path.replace(cached_dxf)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_converter_cache_metadata(metadata_path: Path, schema: str, details: Dict[str, Any]) -> None:
    payload = {
        "schema": schema,
        "created_at_unix": time.time(),
        **details,
    }
    temp_path = metadata_path.with_suffix(f"{metadata_path.suffix}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(metadata_path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _cleanup_oda_converter_output(converted: str | Path, cache_dir: Path) -> None:
    converted_path = Path(converted)
    output_dir = converted_path.parent
    if output_dir == cache_dir or not output_dir.name.startswith("dwg_out_"):
        return
    try:
        output_dir.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    except (OSError, ValueError):
        return
    shutil.rmtree(output_dir, ignore_errors=True)


def _slim_converted_dxf_before_budget(cached_dxf: Path, cache_details: Dict[str, Any]) -> None:
    """Strip the dead-weight OBJECTS section before the token-budget gate.

    ODA/user converters emit DXFs whose OBJECTS section is mostly proxy-
    dictionary dead weight — measured at ~83% of a real AC1027 detail sheet,
    which pushed a 2.58M-token file 0.03% over the 2.5M budget and fail-closed
    the entire compare with zero output. The comparison pipeline reads
    ENTITIES/BLOCKS/TABLES only, so the budget gate must measure the slimmed
    file. ``slim_converted_dxf`` is in-place, parity-verified, idempotent
    (re-slim short-circuits on the 8 MB size gate), and never fatal.
    """
    try:
        from .dxf_slim import slim_converted_dxf

        _, slim_note = slim_converted_dxf(cached_dxf)
        cache_details["slim_note"] = slim_note
    except Exception:  # noqa: BLE001 — slimming is best-effort, never fatal
        cache_details["slim_note"] = "slim_errored"


def _converted_dxf_budget_failure(
    cached_dxf: Path,
    options: ImportPipelineOptions,
) -> Optional[Dict[str, Any]]:
    limits = _effective_stability_limits(options)
    max_tokens = max(0, int(limits.max_dxf_tokens or 0))
    if max_tokens <= 0:
        return None
    try:
        estimated_tokens = _estimate_dxf_tokens_from_file(cached_dxf, stop_after=max_tokens + 1)
    except OSError as exc:
        return {
            "error_code": CadPipelineErrorCode.READ_FAILED,
            "message": f"Converted DXF read failed before import: {exc}",
            "details": {"converted_dxf": str(cached_dxf)},
        }
    if estimated_tokens <= max_tokens:
        return None
    return {
        "error_code": CadPipelineErrorCode.TOKEN_LIMIT_EXCEEDED,
        "message": (
            f"Converted DXF token budget exceeded before import: "
            f"{estimated_tokens} > {max_tokens}"
        ),
        "details": {
            "converted_dxf": str(cached_dxf),
            "estimated_token_count": estimated_tokens,
            "max_dxf_tokens": max_tokens,
        },
    }


def _estimate_dxf_tokens_from_file(path: Path, *, stop_after: int) -> int:
    line_count = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            line_count += chunk.count(b"\n")
            if stop_after > 0 and (line_count // 2) > stop_after:
                break
    return max(0, line_count // 2)


def _status_message(
    source_format: str,
    status: str,
    error_code: Optional[str],
    warnings: List[Dict[str, Any]],
) -> str:
    if status == CadPipelineStatus.OK:
        return f"{source_format.upper()} import completed."
    if status == CadPipelineStatus.PARTIAL:
        return (
            f"{source_format.upper()} import completed with {len(warnings)} warning(s). "
            "Comparison may be incomplete."
        )
    if source_format == "dwg":
        return _dwg_user_message(error_code)
    return f"{source_format.upper()} import failed: {error_code or CadPipelineErrorCode.READ_FAILED}"


def _dwg_user_message(error_code: Optional[str]) -> str:
    messages = {
        DwgFailureCode.CORRUPTED: "DWG import failed because the file is corrupted or not a valid DWG.",
        DwgFailureCode.ENCRYPTED: "DWG import failed because the file is encrypted.",
        DwgFailureCode.UNSUPPORTED_VERSION: "DWG version is not supported by the configured ODA-free DWG adapter.",
        DwgFailureCode.ADAPTER_UNAVAILABLE: "DWG import adapter is unavailable. Install or configure an approved adapter.",
        DwgFailureCode.ADAPTER_FAILED: "DWG import adapter failed.",
        DwgFailureCode.FORBIDDEN_LICENSE: "DWG import adapter license is not allowed for embedded product use.",
        DwgFailureCode.NO_READABLE_ENTITIES: "DWG import produced no readable entities.",
        DwgFailureCode.IMPORT_TIMEOUT: "DWG import timed out before completion.",
        DwgFailureCode.IMPORT_CANCELLED: "DWG import was cancelled.",
        DwgFailureCode.ENTITY_LIMIT_EXCEEDED: "DWG import stopped because the entity limit was exceeded.",
    }
    return messages.get(str(error_code), "DWG import failed.")


def _compare_status_message(status: str, imports: Dict[str, ImportPipelineResult]) -> str:
    if status == CadPipelineStatus.PARTIAL:
        partial = ", ".join(key for key, value in imports.items() if value.is_partial)
        return f"CAD compare completed with partial import on side(s): {partial}."
    return "CAD compare completed."


def _warning_messages(warnings: List[Dict[str, Any]]) -> List[str]:
    messages = []
    for warning in warnings:
        code = warning.get("code")
        message = warning.get("message") or code
        messages.append(f"{code}: {message}" if code else str(message))
    return messages


def _filter_drawing_layers(
    drawing: Dict[str, Any],
    *,
    include_layers: Optional[Sequence[str]] = None,
    exclude_layers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    include = _layer_filter_set(include_layers)
    exclude = _layer_filter_set(exclude_layers)
    if not include and not exclude:
        return drawing

    layers_by_id = _layers_by_id(drawing)
    kept_entities = []
    for entity in drawing.get("entities") or []:
        layer_id = str(entity.get("layer_id") or "")
        layer_name = str((layers_by_id.get(layer_id) or {}).get("name") or layer_id)
        keys = {layer_id.casefold(), layer_name.casefold()}
        if include and not (keys & include):
            continue
        if exclude and (keys & exclude):
            continue
        kept_entities.append(entity)

    filtered = dict(drawing)
    filtered["entities"] = kept_entities
    return filtered


def _layer_filter_set(values: Optional[Sequence[str]]) -> set[str]:
    return {
        str(value).strip().casefold()
        for value in values or ()
        if str(value).strip()
    }


def _layers_by_id(drawing: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(layer.get("id")): layer
        for layer in drawing.get("layers") or []
        if layer.get("id")
    }


def _acad_version(canonical: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    source = (((canonical.get("drawing") or {}).get("source")) or {})
    version = source.get("acad_version")
    if not version:
        return None
    return {
        "code": version,
        "family": "DXF",
        "release": version,
        "supported": True,
    }


def _detect_dxf_version(path: Path) -> Optional[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "$ACADVER" and index + 2 < len(lines):
            code = lines[index + 2].strip()
            if code:
                return {
                    "code": code,
                    "family": "DXF",
                    "release": code,
                    "supported": True,
                }
    return None


def _dwg_adapter_supports_version(
    adapter: Optional[DwgImporterAdapter],
    version_info: Any,
    *,
    backend_mode: Optional[str] = None,
) -> bool:
    if adapter is not None:
        return bool(adapter.supports_version(version_info))
    if backend_mode is not None:
        return bool(create_dwg_backend_selection(backend_mode).adapter.supports_version(version_info))
    return bool(getattr(version_info, "supported", False))


def _uses_user_converter_backend(options: ImportPipelineOptions) -> bool:
    return _selected_dwg_backend_mode(options) == DWG_BACKEND_USER_CONVERTER


def _selected_dwg_backend_mode(options: ImportPipelineOptions) -> Optional[str]:
    if options.dwg_adapter is not None:
        return None
    backend_mode = options.dwg_backend_mode
    if backend_mode is None:
        backend_mode = os.environ.get(DWG_BACKEND_ENV)
    if not backend_mode:
        return None
    return normalize_dwg_backend_mode(backend_mode)


def _effective_stability_limits(options: ImportPipelineOptions) -> CadStabilityLimits:
    limits = options.stability_limits
    default_tokens = CadStabilityLimits().max_dxf_tokens
    if _uses_user_converter_backend(options) and limits.max_dxf_tokens == default_tokens:
        return replace(limits, max_dxf_tokens=USER_CONVERTED_DXF_DEFAULT_MAX_TOKENS)
    return limits
