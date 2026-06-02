"""Read-only native DWG adapter for the AC1015 MVP."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .dwg_importer import (
    DwgAdapterDrawing,
    DwgFailureCode,
    DwgImportError,
    DwgImporterAdapter,
    DwgJsonFixtureAdapter,
    DwgVersionInfo,
)
from .dwg_object_decoder import DwgObjectDecodeError, DwgObjectDecoder
from .dwg_section_reader import DwgSectionReadError, DwgSectionReader


class DwgNativeAc1015Adapter(DwgImporterAdapter):
    """Internal, read-only AC1015 adapter.

    The adapter can fall back to the existing JSON fixture adapter in tests and
    CI.  Production callers may inject it without a fallback to validate exactly
    what the native reader can decode.
    """

    name = "native-ac1015"
    version = "0.1"
    license_id = "INTERNAL"
    backend_mode = "cleanroom_native"
    implementation_status = "ac1015_preview"
    approval_required = False

    def __init__(self, fallback_adapter: Optional[DwgImporterAdapter] = None):
        self.fallback_adapter = fallback_adapter

    def is_available(self) -> bool:
        return True

    def supports_version(self, version: DwgVersionInfo) -> bool:
        return version.code == "AC1015"

    def read_file(self, path: str | Path, version: DwgVersionInfo) -> DwgAdapterDrawing:
        path = Path(path)
        data = path.read_bytes()
        if DwgJsonFixtureAdapter.MARKER in data:
            return self._fallback(path, version)
        if version.code != "AC1015":
            return self._fallback(
                path,
                version,
                code=DwgFailureCode.UNSUPPORTED_VERSION,
                message=f"Native DWG reader currently supports AC1015 only, got {version.code}.",
            )
        try:
            section_reader = DwgSectionReader(data)
            header = section_reader.read_header()
            object_map = section_reader.read_object_map(header)
            if not object_map:
                raise DwgImportError(
                    DwgFailureCode.NO_READABLE_ENTITIES,
                    "AC1015 object map is empty.",
                )
            return DwgObjectDecoder(data, header, object_map).decode()
        except DwgImportError:
            raise
        except (DwgSectionReadError, DwgObjectDecodeError) as exc:
            raise DwgImportError(
                DwgFailureCode.ADAPTER_FAILED,
                f"Native AC1015 DWG reader failed: {exc}",
                details={"adapter": self.name, "path": str(path)},
            ) from exc

    def _fallback(
        self,
        path: Path,
        version: DwgVersionInfo,
        *,
        code: str = DwgFailureCode.ADAPTER_UNAVAILABLE,
        message: str = "Native AC1015 reader cannot decode this DWG.",
    ) -> DwgAdapterDrawing:
        if self.fallback_adapter is not None and self.fallback_adapter.is_available():
            return self.fallback_adapter.read_file(path, version)
        raise DwgImportError(code, message, details={"adapter": self.name, "path": str(path)})
