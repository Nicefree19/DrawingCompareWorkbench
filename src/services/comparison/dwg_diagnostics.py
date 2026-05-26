"""Native DWG reader diagnostics.

The diagnostics layer records exactly where the ODA-free native reader can and
cannot proceed.  It is intentionally read-only and does not attempt to infer
undocumented DWG structures from customer files.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .dwg_importer import DwgFailureCode, DwgImportError, DwgVersionDetector
from .dwg_section_reader import DwgSectionReader, DwgVersionedSectionMapReader


NATIVE_SUPPORTED_VERSIONS = ("AC1015",)
NATIVE_PLANNED_VERSIONS = ("AC1018", "AC1021", "AC1024", "AC1027", "AC1032")


@dataclass(frozen=True)
class DwgDiagnosticStage:
    name: str
    status: str
    detail: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "metrics": self.metrics,
        }


@dataclass
class DwgNativeDiagnostic:
    path: str
    exists: bool
    status: str
    error_code: Optional[str] = None
    message: str = ""
    file_size_bytes: Optional[int] = None
    header_hex: str = ""
    sha256: Optional[str] = None
    version: Optional[Dict[str, Any]] = None
    native_supported_versions: List[str] = field(default_factory=lambda: list(NATIVE_SUPPORTED_VERSIONS))
    planned_versions: List[str] = field(default_factory=lambda: list(NATIVE_PLANNED_VERSIONS))
    blocking_stage: Optional[str] = None
    stages: List[DwgDiagnosticStage] = field(default_factory=list)

    def add_stage(self, name: str, status: str, detail: str = "", **metrics: Any) -> None:
        self.stages.append(DwgDiagnosticStage(name=name, status=status, detail=detail, metrics=metrics))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "status": self.status,
            "error_code": self.error_code,
            "message": self.message,
            "file_size_bytes": self.file_size_bytes,
            "header_hex": self.header_hex,
            "sha256": self.sha256,
            "version": self.version,
            "native_supported_versions": self.native_supported_versions,
            "planned_versions": self.planned_versions,
            "blocking_stage": self.blocking_stage,
            "stages": [stage.to_dict() for stage in self.stages],
        }


def diagnose_dwg_file(path: str | Path, *, include_sha256: bool = False) -> DwgNativeDiagnostic:
    path = Path(path)
    diagnostic = DwgNativeDiagnostic(
        path=str(path),
        exists=path.is_file(),
        status="missing" if not path.is_file() else "unknown",
    )
    if not path.is_file():
        diagnostic.error_code = DwgFailureCode.CORRUPTED
        diagnostic.message = "DWG file does not exist."
        diagnostic.blocking_stage = "file"
        diagnostic.add_stage("file", "failed", "file does not exist")
        return diagnostic

    data = path.read_bytes()
    diagnostic.file_size_bytes = len(data)
    diagnostic.header_hex = data[:16].hex()
    if include_sha256:
        diagnostic.sha256 = hashlib.sha256(data).hexdigest()
    diagnostic.add_stage("file", "ok", "file loaded", size_bytes=len(data))

    try:
        version = DwgVersionDetector.detect_bytes(data[:6])
    except DwgImportError as exc:
        diagnostic.status = "failed"
        diagnostic.error_code = exc.code
        diagnostic.message = str(exc)
        diagnostic.blocking_stage = "version"
        diagnostic.add_stage("version", "failed", str(exc), **exc.details)
        return diagnostic

    diagnostic.version = version.to_dict()
    diagnostic.add_stage(
        "version",
        "ok",
        version.release,
        code=version.code,
        detector_supported=version.supported,
        native_supported=version.code in NATIVE_SUPPORTED_VERSIONS,
    )

    if version.code in DwgVersionedSectionMapReader.SUPPORTED_SHELL_VERSIONS:
        try:
            section_map = DwgVersionedSectionMapReader(data, version.code).inspect()
        except Exception as exc:  # noqa: BLE001
            diagnostic.status = "failed"
            diagnostic.error_code = DwgFailureCode.ADAPTER_FAILED
            diagnostic.message = str(exc)
            diagnostic.blocking_stage = "section_locator"
            diagnostic.add_stage("section_locator", "failed", str(exc))
            return diagnostic

        diagnostic.status = "unsupported_version"
        diagnostic.error_code = DwgFailureCode.UNSUPPORTED_VERSION
        diagnostic.message = section_map.message
        diagnostic.blocking_stage = section_map.blocking_stage
        diagnostic.add_stage(
            "section_locator",
            "ok",
            f"{version.code} section-map reader shell selected",
            **section_map.to_metrics(),
        )
        diagnostic.add_stage(
            section_map.blocking_stage,
            "blocked",
            section_map.message,
            **section_map.to_metrics(),
        )
        return diagnostic

    if version.code not in NATIVE_SUPPORTED_VERSIONS:
        diagnostic.status = "unsupported_version"
        diagnostic.error_code = DwgFailureCode.UNSUPPORTED_VERSION
        diagnostic.blocking_stage = "section_locator"
        if version.code in NATIVE_PLANNED_VERSIONS:
            diagnostic.message = (
                f"{version.code} is detected but the native reader only has an AC1015 "
                "section/object-map decoder. Implement the versioned section map before "
                "object decoding."
            )
        else:
            diagnostic.message = f"{version.code} is not in the native reader roadmap."
        diagnostic.add_stage(
            "section_locator",
            "blocked",
            "versioned section locator reader is not implemented",
            required_version=version.code,
        )
        return diagnostic

    try:
        section_reader = DwgSectionReader(data)
        header = section_reader.read_header()
        diagnostic.add_stage(
            "section_header",
            "ok",
            "AC1015 section locators decoded",
            locator_count=header.locator_count,
            codepage=header.codepage,
        )
        object_map = section_reader.read_object_map(header)
        diagnostic.add_stage(
            "object_map",
            "ok",
            "AC1015 object map decoded",
            entry_count=len(object_map),
        )
    except Exception as exc:  # noqa: BLE001
        diagnostic.status = "failed"
        diagnostic.error_code = DwgFailureCode.ADAPTER_FAILED
        diagnostic.message = str(exc)
        diagnostic.blocking_stage = "section_reader"
        diagnostic.add_stage("section_reader", "failed", str(exc))
        return diagnostic

    diagnostic.status = "ok"
    diagnostic.message = "Native AC1015 section diagnostics completed."
    return diagnostic
