# -*- coding: utf-8 -*-
"""Diagnostic-only navigator for the AC1032 (R2018) DWG container — Spike S1.

This module ONLY navigates the R2004+ container: it de-obfuscates the R2004
file header and locates the section-page-map / section-map. It decodes NO
objects, makes NO support claim, and does not enable AC1032 import. AC1032
object/entity decoding stays gated by ``dwg_cleanroom_contract.py``.

Clean-room provenance: the R2004 file-header LCG de-obfuscation, the
``AcFssFcAJMB`` header magic, and the header field layout are implemented from
the public DWG format specification and verified against locally held real
AC1032 samples. The approved public-spec reference and the license/reference
posture (no third-party reader source copied) are recorded in
``docs/collab/AC1032_CLEANROOM_PROVENANCE.md``.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Dict

R2018_VERSION_CODE = "AC1032"

#: The R2004+ file header is a 0x6C-byte LCG-obfuscated block at this offset.
R2004_HEADER_OFFSET = 0x80
R2004_HEADER_LENGTH = 0x6C

#: Correct de-obfuscation reproduces this 12-byte magic at the block start.
R2004_HEADER_MAGIC = b"AcFssFcAJMB\x00"

#: Section-page addresses in the header are file offsets minus this base.
SECTION_PAGE_ADDRESS_BASE = 0x100

# Verified decrypted-header field offsets (little-endian; RL = u32, RLL = u64).
_OFF_LAST_SECTION_PAGE_ID = 0x28          # RL
_OFF_LAST_SECTION_PAGE_END_ADDRESS = 0x2C  # RLL
_OFF_SECOND_HEADER_ADDRESS = 0x34          # RLL
_OFF_SECTION_PAGE_MAP_ID = 0x50            # RL
_OFF_SECTION_PAGE_MAP_ADDRESS = 0x54       # RLL (+ SECTION_PAGE_ADDRESS_BASE)
_OFF_SECTION_MAP_ID = 0x5C                 # RL
_OFF_SECTION_PAGE_ARRAY_SIZE = 0x60        # RL
_OFF_GAP_ARRAY_SIZE = 0x64                 # RL


@dataclass(frozen=True)
class DwgR2018ContainerDiagnostic:
    """Result of navigating (not decoding) an AC1032 container."""

    version_code: str
    status: str  # navigable | page_map_out_of_bounds | magic_mismatch | wrong_version | too_short
    magic_ok: bool
    file_size_bytes: int
    message: str
    fields: Dict[str, Any] = field(default_factory=dict)
    decrypted_header_hex: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_code": self.version_code,
            "status": self.status,
            "magic_ok": self.magic_ok,
            "file_size_bytes": self.file_size_bytes,
            "message": self.message,
            "fields": dict(self.fields),
            "decrypted_header_hex": self.decrypted_header_hex,
        }


def deobfuscate_r2004_header(encrypted: bytes) -> bytes:
    """Reverse the documented R2004 file-header LCG obfuscation.

    Per the public DWG format spec, each byte is XOR'd with the high byte of a
    32-bit Microsoft-style LCG sequence (multiplier 0x343FD, increment
    0x269EC3). The keystream depends only on byte position, so it is symmetric.
    """

    out = bytearray(len(encrypted))
    seed = 1
    for index, byte in enumerate(encrypted):
        seed = (seed * 0x343FD + 0x269EC3) & 0xFFFFFFFF
        out[index] = byte ^ ((seed >> 0x10) & 0xFF)
    return bytes(out)


def inspect_r2018_container(
    data: bytes | bytearray | memoryview,
    *,
    version_code: str = R2018_VERSION_CODE,
) -> DwgR2018ContainerDiagnostic:
    """Navigate (do NOT decode) the AC1032 container and return a diagnostic."""

    raw = bytes(data)
    size = len(raw)
    if size < R2004_HEADER_OFFSET + R2004_HEADER_LENGTH:
        return DwgR2018ContainerDiagnostic(
            version_code=version_code,
            status="too_short",
            magic_ok=False,
            file_size_bytes=size,
            message="file is too short to contain an R2004 file header",
        )

    actual_version = raw[:6].decode("ascii", errors="replace")
    if actual_version != version_code:
        return DwgR2018ContainerDiagnostic(
            version_code=version_code,
            status="wrong_version",
            magic_ok=False,
            file_size_bytes=size,
            message=f"expected {version_code}, got {actual_version!r}",
            fields={"actual_version": actual_version},
        )

    decrypted = deobfuscate_r2004_header(
        raw[R2004_HEADER_OFFSET : R2004_HEADER_OFFSET + R2004_HEADER_LENGTH]
    )
    magic = decrypted[: len(R2004_HEADER_MAGIC)]
    if magic != R2004_HEADER_MAGIC:
        return DwgR2018ContainerDiagnostic(
            version_code=version_code,
            status="magic_mismatch",
            magic_ok=False,
            file_size_bytes=size,
            message="R2004 header de-obfuscation did not reproduce the AcFssFcAJMB magic",
            fields={"decrypted_magic_hex": magic.hex()},
            decrypted_header_hex=decrypted.hex(),
        )

    section_page_map_address = struct.unpack_from("<Q", decrypted, _OFF_SECTION_PAGE_MAP_ADDRESS)[0]
    section_page_map_file_offset = section_page_map_address + SECTION_PAGE_ADDRESS_BASE
    in_bounds = 0 < section_page_map_file_offset < size
    fields: Dict[str, Any] = {
        "last_section_page_id": struct.unpack_from("<I", decrypted, _OFF_LAST_SECTION_PAGE_ID)[0],
        "last_section_page_end_address": struct.unpack_from("<Q", decrypted, _OFF_LAST_SECTION_PAGE_END_ADDRESS)[0],
        "second_header_address": struct.unpack_from("<Q", decrypted, _OFF_SECOND_HEADER_ADDRESS)[0],
        "section_page_map_id": struct.unpack_from("<I", decrypted, _OFF_SECTION_PAGE_MAP_ID)[0],
        "section_page_map_address": section_page_map_address,
        "section_page_map_file_offset": section_page_map_file_offset,
        "section_page_map_in_bounds": in_bounds,
        "section_map_id": struct.unpack_from("<I", decrypted, _OFF_SECTION_MAP_ID)[0],
        "section_page_array_size": struct.unpack_from("<I", decrypted, _OFF_SECTION_PAGE_ARRAY_SIZE)[0],
        "gap_array_size": struct.unpack_from("<I", decrypted, _OFF_GAP_ARRAY_SIZE)[0],
    }
    if in_bounds:
        return DwgR2018ContainerDiagnostic(
            version_code=version_code,
            status="navigable",
            magic_ok=True,
            file_size_bytes=size,
            message="R2018 container navigable: header de-obfuscated, section-page-map located in bounds",
            fields=fields,
            decrypted_header_hex=decrypted.hex(),
        )
    return DwgR2018ContainerDiagnostic(
        version_code=version_code,
        status="page_map_out_of_bounds",
        magic_ok=True,
        file_size_bytes=size,
        message="header de-obfuscated but the section-page-map offset is out of bounds",
        fields=fields,
        decrypted_header_hex=decrypted.hex(),
    )


# ---------------------------------------------------------------------------
# Spike S2: R2004 decompression + section-page-map navigation.
# ---------------------------------------------------------------------------

#: System/data section page type magics (public DWG spec sections 4.5/4.6).
SECTION_PAGE_MAP_TYPE = 0x41630E3B
SECTION_MAP_TYPE = 0x4163003B
DATA_SECTION_PAGE_TYPE = 0x4163043B

#: A system section page header is five little-endian u32 fields:
#: type, decompressed size, compressed size, compression type, header checksum.
SYSTEM_PAGE_HEADER_LENGTH = 0x14


def decompress_r2004(src: bytes, decompressed_size: int) -> bytes:
    """Decompress an R2004 section payload (public-spec LZ77 variant).

    The opcode / literal-length / two-byte-offset / long-offset encodings are
    implemented from the public DWG format spec ("4.7 Compression"); the
    back-reference source is ``len(out) - compOffset - 1``. Raises
    ``ValueError`` on a malformed stream so the spike fails visibly rather than
    emitting silent garbage.
    """

    out = bytearray()
    pos = 0
    size = len(src)

    def take() -> int:
        nonlocal pos
        if pos >= size:
            raise ValueError("R2004 stream underrun")
        value = src[pos]
        pos += 1
        return value

    def literal_length() -> int:
        byte = take()
        if byte != 0x00:
            return byte + 3
        total = 0x0F
        while True:
            nxt = take()
            if nxt == 0x00:
                total += 0xFF
            else:
                return total + nxt + 3

    def long_offset() -> int:
        byte = take()
        if byte != 0x00:
            return byte
        total = 0xFF
        while True:
            nxt = take()
            if nxt == 0x00:
                total += 0xFF
            else:
                return total + nxt

    def two_byte_offset() -> tuple[int, int]:
        first = take()
        offset = (first >> 2) | (take() << 6)
        return offset, first & 0x03

    def copy_back(count: int, comp_offset: int) -> None:
        start = len(out) - comp_offset - 1
        if start < 0:
            raise ValueError(f"R2004 back-reference before start (offset {comp_offset})")
        for index in range(count):
            out.append(out[start + index])

    def copy_literals(count: int) -> None:
        for _ in range(count):
            out.append(take())

    opcode = take()
    if (opcode & 0xF0) == 0:
        pos -= 1
        copy_literals(literal_length())
        opcode = take()

    while len(out) < decompressed_size:
        if opcode == 0x11:
            break
        if opcode == 0x10:
            comp_bytes = long_offset() + 9
            comp_offset, literal = two_byte_offset()
            comp_offset += 0x3FFF
        elif 0x12 <= opcode <= 0x1F:
            comp_bytes = (opcode & 0x0F) + 2
            comp_offset, literal = two_byte_offset()
            comp_offset += 0x3FFF
        elif opcode == 0x20:
            comp_bytes = long_offset() + 0x21
            comp_offset, literal = two_byte_offset()
        elif 0x21 <= opcode <= 0x3F:
            comp_bytes = opcode - 0x1E
            comp_offset, literal = two_byte_offset()
        elif opcode >= 0x40:
            comp_bytes = ((opcode & 0xF0) >> 4) - 1
            opcode2 = take()
            comp_offset = (opcode2 << 2) | ((opcode & 0x0C) >> 2)
            literal = opcode & 0x03
        else:
            raise ValueError(f"R2004 unexpected opcode {opcode:#04x} at output {len(out)}")
        copy_back(comp_bytes, comp_offset)
        copy_literals(literal if literal else literal_length())
        opcode = take()

    if len(out) > decompressed_size:
        del out[decompressed_size:]
    return bytes(out)


@dataclass(frozen=True)
class R2004SectionPageMapDiagnostic:
    """Decoded section-page-map (page id -> compressed size + gaps)."""

    version_code: str
    status: str  # decoded | page_map_type_mismatch | <container status>
    page_count: int
    decompressed_size: int
    first_page_number: int
    positive_pages_increasing: bool
    max_page_number: int
    entries: list = field(default_factory=list)  # [(page_number, size)]; page<0 = gap
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_code": self.version_code,
            "status": self.status,
            "page_count": self.page_count,
            "decompressed_size": self.decompressed_size,
            "first_page_number": self.first_page_number,
            "positive_pages_increasing": self.positive_pages_increasing,
            "max_page_number": self.max_page_number,
            "entries": [list(item) for item in self.entries],
            "message": self.message,
        }


def read_r2004_section_page_map(
    data: bytes | bytearray | memoryview,
    *,
    version_code: str = R2018_VERSION_CODE,
) -> R2004SectionPageMapDiagnostic:
    """Decompress and parse the section-page-map (navigation, not decoding)."""

    container = inspect_r2018_container(data, version_code=version_code)
    if container.status != "navigable":
        return R2004SectionPageMapDiagnostic(
            version_code=version_code,
            status=container.status,
            page_count=0,
            decompressed_size=0,
            first_page_number=0,
            positive_pages_increasing=False,
            max_page_number=0,
            message=container.message,
        )

    raw = bytes(data)
    offset = int(container.fields["section_page_map_file_offset"])
    section_type, decompressed_size, compressed_size, _ctype, _hcrc = struct.unpack_from(
        "<IIIII", raw, offset
    )
    if section_type != SECTION_PAGE_MAP_TYPE:
        return R2004SectionPageMapDiagnostic(
            version_code=version_code,
            status="page_map_type_mismatch",
            page_count=0,
            decompressed_size=0,
            first_page_number=0,
            positive_pages_increasing=False,
            max_page_number=0,
            message=f"expected section-page-map type {SECTION_PAGE_MAP_TYPE:#010x}, got {section_type:#010x}",
        )

    data_offset = offset + SYSTEM_PAGE_HEADER_LENGTH
    decompressed = decompress_r2004(
        raw[data_offset : data_offset + compressed_size], decompressed_size
    )
    entries = []
    for index in range(0, len(decompressed) - 7, 8):
        page_number = struct.unpack_from("<i", decompressed, index)[0]
        page_size = struct.unpack_from("<I", decompressed, index + 4)[0]
        entries.append((page_number, page_size))

    positives = [page for page, _ in entries if page > 0]
    increasing = all(positives[i] < positives[i + 1] for i in range(len(positives) - 1))
    max_page = max(positives) if positives else 0
    return R2004SectionPageMapDiagnostic(
        version_code=version_code,
        status="decoded",
        page_count=len(entries),
        decompressed_size=len(decompressed),
        first_page_number=entries[0][0] if entries else 0,
        positive_pages_increasing=increasing,
        max_page_number=max_page,
        entries=entries,
        message=f"decoded {len(entries)} section-page-map entries",
    )


__all__ = [
    "R2018_VERSION_CODE",
    "R2004_HEADER_OFFSET",
    "R2004_HEADER_LENGTH",
    "R2004_HEADER_MAGIC",
    "SECTION_PAGE_ADDRESS_BASE",
    "SECTION_PAGE_MAP_TYPE",
    "SECTION_MAP_TYPE",
    "DATA_SECTION_PAGE_TYPE",
    "SYSTEM_PAGE_HEADER_LENGTH",
    "DwgR2018ContainerDiagnostic",
    "R2004SectionPageMapDiagnostic",
    "deobfuscate_r2004_header",
    "inspect_r2018_container",
    "decompress_r2004",
    "read_r2004_section_page_map",
]
