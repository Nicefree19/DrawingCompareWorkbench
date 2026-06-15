"""DWG section locator and object-map readers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .dwg_binary_reader import DwgBinaryReadError, DwgBinaryReader
from .dwg_cleanroom_contract import contract_for_version


class DwgSectionReadError(DwgBinaryReadError):
    """Raised when DWG section metadata is malformed."""

    def __init__(self, message: str, *, diagnostics: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


@dataclass(frozen=True)
class DwgSectionLocator:
    record_number: int
    seeker: int
    size: int

    @property
    def end(self) -> int:
        return self.seeker + self.size


@dataclass(frozen=True)
class DwgFileHeader:
    version_code: str
    codepage: int
    locator_count: int
    locators: Dict[int, DwgSectionLocator]

    def locator(self, record_number: int) -> Optional[DwgSectionLocator]:
        return self.locators.get(record_number)


@dataclass(frozen=True)
class DwgObjectMapEntry:
    handle: int
    offset: int

    @property
    def handle_hex(self) -> str:
        return f"{self.handle:X}" if self.handle else "0"


@dataclass(frozen=True)
class DwgVersionedSectionMapDiagnostic:
    version_code: str
    family: str
    contract_id: str
    approval_status: str
    implemented: bool
    blocking_stage: str
    blocking_stage_detail: str
    message: str
    file_size_bytes: int
    header_hex: str
    required_decoders: Tuple[str, ...]
    decoder_provenance: str
    approved_reference_available: bool
    required_approval_evidence: Tuple[str, ...]
    next_safe_step: str

    def to_metrics(self) -> Dict[str, Any]:
        return {
            "version_code": self.version_code,
            "family": self.family,
            "contract_id": self.contract_id,
            "approval_status": self.approval_status,
            "implemented": self.implemented,
            "blocking_stage_detail": self.blocking_stage_detail,
            "file_size_bytes": self.file_size_bytes,
            "header_hex": self.header_hex,
            "required_decoders": list(self.required_decoders),
            "decoder_provenance": self.decoder_provenance,
            "approved_reference_available": self.approved_reference_available,
            "required_approval_evidence": list(self.required_approval_evidence),
            "next_safe_step": self.next_safe_step,
        }


class DwgVersionedSectionMapReader:
    """Versioned DWG section-map reader shell for post-AC1015 layouts.

    This class intentionally does not decode undocumented payloads.  It gives
    diagnostics a stable handoff point so newer DWG files can move from a broad
    version block to a precise section-map decoder block.
    """

    SUPPORTED_SHELL_VERSIONS = {
        "AC1024": "AutoCAD 2010/2011/2012",
        "AC1032": "AutoCAD 2018+",
    }
    REQUIRED_DECODERS = (
        "versioned file header metadata",
        "section page map",
        "section page metadata",
        "compression/encryption guards",
        "CRC validation",
    )

    def __init__(self, data: bytes | bytearray | memoryview, version_code: str):
        self.data = bytes(data)
        self.version_code = version_code

    def inspect(self) -> DwgVersionedSectionMapDiagnostic:
        if len(self.data) < 6:
            raise DwgSectionReadError("DWG header is too short for versioned section map diagnostics")
        try:
            actual_version = self.data[:6].decode("ascii")
        except UnicodeDecodeError as exc:
            raise DwgSectionReadError("DWG version signature is not ASCII") from exc
        if actual_version != self.version_code:
            raise DwgSectionReadError(
                f"versioned section reader expected {self.version_code}, got {actual_version}"
            )
        family = self.SUPPORTED_SHELL_VERSIONS.get(actual_version)
        if family is None:
            raise DwgSectionReadError(f"versioned section reader shell is not registered for {actual_version}")
        contract = contract_for_version(actual_version)
        if contract is None:
            raise DwgSectionReadError(f"clean-room section-map contract is not registered for {actual_version}")

        return DwgVersionedSectionMapDiagnostic(
            version_code=actual_version,
            family=contract.family,
            contract_id=contract.contract_id,
            approval_status=contract.approval_status,
            implemented=False,
            blocking_stage="section_map_decoder",
            blocking_stage_detail=contract.blocking_stage_detail,
            message=(
                f"{actual_version} section-map reader shell is selected, but "
                f"{contract.contract_id} is still {contract.approval_status}. "
                "Decoding is intentionally blocked until approved public-reference "
                "guidance and clean-room implementation evidence are recorded."
            ),
            file_size_bytes=len(self.data),
            header_hex=self.data[:16].hex(),
            required_decoders=self.REQUIRED_DECODERS,
            decoder_provenance=contract.decoder_provenance,
            approved_reference_available=contract.approved_reference_available,
            required_approval_evidence=contract.required_approval_evidence,
            next_safe_step=contract.next_safe_step,
        )


class DwgSectionReader:
    """Read AC1015 section locators and R13-R15 object map entries."""

    HEADER_LOCATOR_COUNT_OFFSET = 0x15
    HEADER_CODEPAGE_OFFSET = 0x13
    OBJECT_MAP_SECTION = 2
    MAX_SECTION_LOCATORS = 128
    MAX_OBJECT_MAP_ENTRIES = 1_000_000

    def __init__(self, data: bytes | bytearray | memoryview):
        self.data = bytes(data)

    def read_header(self) -> DwgFileHeader:
        if len(self.data) < self.HEADER_LOCATOR_COUNT_OFFSET + 4:
            raise DwgSectionReadError("AC1015 header is too short for section locators")
        try:
            version_code = self.data[:6].decode("ascii")
        except UnicodeDecodeError as exc:
            raise DwgSectionReadError("DWG version signature is not ASCII") from exc
        if version_code != "AC1015":
            raise DwgSectionReadError(f"native MVP supports AC1015 only, got {version_code!r}")

        reader = DwgBinaryReader(self.data)
        reader.seek(self.HEADER_CODEPAGE_OFFSET)
        codepage = reader.read_u16_le()
        locator_count = reader.read_u32_le()
        if locator_count < 0 or locator_count > self.MAX_SECTION_LOCATORS:
            raise DwgSectionReadError(f"unreasonable AC1015 section locator count: {locator_count}")

        locators: Dict[int, DwgSectionLocator] = {}
        for _ in range(locator_count):
            record_number = reader.read_u8()
            seeker = reader.read_u32_le()
            size = reader.read_u32_le()
            if seeker < 0 or size < 0 or seeker + size > len(self.data):
                raise DwgSectionReadError(
                    f"section locator {record_number} points outside file: offset={seeker}, size={size}"
                )
            locators[record_number] = DwgSectionLocator(
                record_number=record_number,
                seeker=seeker,
                size=size,
            )

        return DwgFileHeader(
            version_code=version_code,
            codepage=codepage,
            locator_count=locator_count,
            locators=locators,
        )

    def read_object_map(self, header: Optional[DwgFileHeader] = None) -> List[DwgObjectMapEntry]:
        header = header or self.read_header()
        locator = header.locator(self.OBJECT_MAP_SECTION)
        if locator is None:
            raise DwgSectionReadError("AC1015 object map section locator is missing")

        reader = DwgBinaryReader(self.data, offset=locator.seeker, length=locator.size)
        entries: List[DwgObjectMapEntry] = []
        last_handle = 0
        last_offset = 0
        while reader.byte_pos < reader.size:
            section_size = reader.read_u16_be()
            if section_size < 2:
                raise DwgSectionReadError(f"invalid object map subsection size: {section_size}")
            if section_size == 2:
                if reader.size - reader.byte_pos >= 2:
                    reader.read_bytes(2)
                break

            data_size = section_size - 2
            if reader.byte_pos + data_size + 2 > reader.size:
                raise DwgSectionReadError("object map subsection extends past section boundary")
            subsection = DwgBinaryReader(reader.read_bytes(data_size))
            while subsection.byte_pos < subsection.size:
                handle_delta = subsection.read_modular_char(signed=False)
                offset_delta = subsection.read_modular_char(signed=True)
                last_handle += handle_delta
                last_offset += offset_delta
                if last_handle < 0 or last_offset < 0 or last_offset >= len(self.data):
                    raise DwgSectionReadError(
                        f"object map entry outside file: handle={last_handle:X}, offset={last_offset}",
                        diagnostics={
                            "object_handle": f"{last_handle:X}",
                            "object_offset": last_offset,
                            "object_map_entry_index": len(entries),
                            "object_map_decoded_entries": len(entries),
                        },
                    )
                entries.append(DwgObjectMapEntry(handle=last_handle, offset=last_offset))
                if len(entries) > self.MAX_OBJECT_MAP_ENTRIES:
                    raise DwgSectionReadError("object map entry limit exceeded")
            reader.read_bytes(2)
        return entries

    @staticmethod
    def entries_by_handle(entries: Iterable[DwgObjectMapEntry]) -> Dict[int, DwgObjectMapEntry]:
        return {entry.handle: entry for entry in entries}
