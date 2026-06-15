# -*- coding: utf-8 -*-
"""Diagnostic AC1032 (R2018) DWG container reader — own-viewer spike.

This module navigates the R2004+ container (header de-obfuscation, section-page
/ section maps, page assembly), enumerates objects via the AcDb:Handles index,
and decodes basic entity geometry (LINE/CIRCLE/ARC/POINT) for the spike. It is
DIAGNOSTIC-only: it makes NO support claim, does not enable AC1032 import, and
does not feed the product pipeline. AC1032 decoding stays gated by
``dwg_cleanroom_contract.py`` (which remains ``blocked``).

Clean-room provenance: every algorithm (R2004 LCG de-obfuscation, the
``AcFssFcAJMB`` magic, R2004 LZ77 decompression, the section/handle map layout,
the R2010+ common-entity-data field order, and the per-entity formats) is
implemented from the public ODA "Open Design Specification for .dwg files" and
verified by observation against locally held real AC1032 samples (geometry
cross-checked against ODA-converted ground truth). No third-party reader source
is copied. See ``docs/collab/AC1032_CLEANROOM_PROVENANCE.md``.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .dwg_binary_reader import DwgBinaryReader, DwgBinaryReadError

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

    def extended_literal_length() -> int:
        # Called only when the literal-length byte was 0x00 (running total).
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

    def read_literal_run() -> int:
        # Read one literal-length byte, copy that many literal bytes, and return
        # the next opcode. Per spec, a literal-length byte with ANY high-nibble
        # bit set is not a length (count 0) but is itself the next opcode.
        byte = take()
        if (byte & 0xF0) != 0:
            return byte
        copy_literals(extended_literal_length() if byte == 0x00 else byte + 3)
        if len(out) >= decompressed_size:
            return 0x11  # output complete; do not consume another opcode byte
        return take()

    # Initial literal run (a leading byte with a high-nibble bit set is already
    # the first compression opcode).
    opcode = read_literal_run()

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
        if len(out) >= decompressed_size:
            break  # complete; do not read the trailing literals / next opcode
        if literal != 0:
            copy_literals(literal)
            if len(out) >= decompressed_size:
                break
            opcode = take()
        else:
            opcode = read_literal_run()

    return bytes(out[:decompressed_size])


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


def section_page_file_offsets(page_map: "R2004SectionPageMapDiagnostic") -> Dict[int, int]:
    """Compute each section page's file offset from its size sequence.

    Per the public spec, page 1 starts at 0x100 and each subsequent page starts
    at the previous page's offset plus the previous page's size (gaps included).
    """

    offsets: Dict[int, int] = {}
    address = SECTION_PAGE_ADDRESS_BASE
    for page_number, page_size in page_map.entries:
        offsets[page_number] = address
        address += page_size
    return offsets


@dataclass(frozen=True)
class R2004Section:
    """One entry of the data-section map (a named logical section)."""

    name: str
    section_id: int
    page_count: int
    size: int
    max_decompressed_size: int = 0  # per-page logical slot size (spec: section "page size")
    pages: list = field(default_factory=list)  # [(page_number, data_size, start_offset)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "section_id": self.section_id,
            "page_count": self.page_count,
            "size": self.size,
            "max_decompressed_size": self.max_decompressed_size,
            "pages": [list(page) for page in self.pages],
        }


@dataclass(frozen=True)
class R2004SectionMapDiagnostic:
    """Decoded data-section map (section name -> pages)."""

    version_code: str
    status: str  # decoded | section_map_type_mismatch | section_map_page_missing | <page-map status>
    section_count: int
    section_names: list = field(default_factory=list)
    has_acdbobjects: bool = False
    has_handles: bool = False
    sections: list = field(default_factory=list)  # [R2004Section]
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_code": self.version_code,
            "status": self.status,
            "section_count": self.section_count,
            "section_names": list(self.section_names),
            "has_acdbobjects": self.has_acdbobjects,
            "has_handles": self.has_handles,
            "sections": [section.to_dict() for section in self.sections],
            "message": self.message,
        }


def read_r2004_section_map(
    data: bytes | bytearray | memoryview,
    *,
    version_code: str = R2018_VERSION_CODE,
    page_map: "R2004SectionPageMapDiagnostic | None" = None,
) -> R2004SectionMapDiagnostic:
    """Decompress + parse the data-section map (navigation, not decoding).

    Locates the section-map system page via ``section_map_id`` + the section
    page directory, decompresses it, and enumerates the named logical sections
    (``AcDb:AcDbObjects``, ``AcDb:Handles``, ``AcDb:Header`` ...) with their
    page references. No object/entity bytes are decoded. Pass a pre-decoded
    ``page_map`` to avoid re-decoding the page directory.
    """

    if page_map is None:
        page_map = read_r2004_section_page_map(data, version_code=version_code)
    if page_map.status != "decoded":
        return R2004SectionMapDiagnostic(
            version_code=version_code,
            status=page_map.status,
            section_count=0,
            message=page_map.message,
        )

    container = inspect_r2018_container(data, version_code=version_code)
    section_map_id = int(container.fields["section_map_id"])
    offsets = section_page_file_offsets(page_map)
    if section_map_id not in offsets:
        return R2004SectionMapDiagnostic(
            version_code=version_code,
            status="section_map_page_missing",
            section_count=0,
            message=f"section-map page {section_map_id} is absent from the page directory",
        )

    raw = bytes(data)
    offset = offsets[section_map_id]
    section_type, decompressed_size, compressed_size, _ctype, _hcrc = struct.unpack_from(
        "<IIIII", raw, offset
    )
    if section_type != SECTION_MAP_TYPE:
        return R2004SectionMapDiagnostic(
            version_code=version_code,
            status="section_map_type_mismatch",
            section_count=0,
            message=f"expected section-map type {SECTION_MAP_TYPE:#010x}, got {section_type:#010x}",
        )

    data_offset = offset + SYSTEM_PAGE_HEADER_LENGTH
    decompressed = decompress_r2004(
        raw[data_offset : data_offset + compressed_size], decompressed_size
    )

    description_count = struct.unpack_from("<I", decompressed, 0)[0]
    sections: list = []
    cursor = 0x14  # NumDescriptions header is 5 u32 fields.
    for _ in range(description_count):
        if cursor + 0x60 > len(decompressed):
            break
        size = struct.unpack_from("<Q", decompressed, cursor)[0]
        page_count = struct.unpack_from("<I", decompressed, cursor + 0x08)[0]
        max_decompressed = struct.unpack_from("<I", decompressed, cursor + 0x0C)[0]
        section_id = struct.unpack_from("<I", decompressed, cursor + 0x18)[0]
        name = (
            decompressed[cursor + 0x20 : cursor + 0x20 + 64]
            .split(b"\x00")[0]
            .decode("ascii", "replace")
        )
        pages: list = []
        page_cursor = cursor + 0x60
        for _page in range(page_count):
            if page_cursor + 16 > len(decompressed):
                break
            page_number = struct.unpack_from("<i", decompressed, page_cursor)[0]
            page_size = struct.unpack_from("<I", decompressed, page_cursor + 4)[0]
            start_offset = struct.unpack_from("<Q", decompressed, page_cursor + 8)[0]
            pages.append((page_number, page_size, start_offset))
            page_cursor += 16
        sections.append(
            R2004Section(
                name=name,
                section_id=section_id,
                page_count=page_count,
                size=size,
                max_decompressed_size=max_decompressed,
                pages=pages,
            )
        )
        cursor += 0x60 + page_count * 16

    names = [section.name for section in sections]
    return R2004SectionMapDiagnostic(
        version_code=version_code,
        status="decoded",
        section_count=len(sections),
        section_names=names,
        has_acdbobjects="AcDb:AcDbObjects" in names,
        has_handles="AcDb:Handles" in names,
        sections=sections,
        message=f"decoded {len(sections)} data-section descriptions",
    )


#: A data-section page begins with a 32-byte XOR-encrypted page header.
DATA_PAGE_HEADER_LENGTH = 0x20


def decrypt_r2004_data_page_header(encrypted: bytes, file_offset: int) -> bytes:
    """Decrypt a 32-byte data-section page header (public spec section 4.6).

    Each of the eight little-endian u32 words is XOR'd with
    ``0x4164536B ^ file_offset``.
    """

    out = bytearray(encrypted[:DATA_PAGE_HEADER_LENGTH])
    sec_mask = 0x4164536B ^ (file_offset & 0xFFFFFFFF)
    for index in range(8):
        word = struct.unpack_from("<I", out, index * 4)[0] ^ sec_mask
        struct.pack_into("<I", out, index * 4, word)
    return bytes(out)


def read_r2004_data_section(
    data: bytes | bytearray | memoryview,
    *,
    section_name: str,
    version_code: str = R2018_VERSION_CODE,
) -> bytes:
    """Assemble one named data section's decompressed bytes (navigation only).

    Reads every page of ``section_name`` (e.g. ``AcDb:AcDbObjects``): decrypts
    its 32-byte data page header, decompresses the page to its logical slot size,
    and places it at its ``start_offset`` in the section buffer. Each page fills a
    ``max_decompressed_size`` slot (from the section descriptor); the page
    header's own size word UNDER-reports the page's logical content, so decoding
    to that word leaves zero gaps between pages (and mis-locates every later
    object). Decoding to the slot size (capped by the section end) tiles the
    pages gap-free. Raises ``ValueError`` on any structural mismatch. This reaches
    the object bytes; it does NOT bit-decode objects.
    """

    page_map = read_r2004_section_page_map(data, version_code=version_code)
    section_map = read_r2004_section_map(data, version_code=version_code, page_map=page_map)
    if section_map.status != "decoded":
        raise ValueError(f"section map not decoded: {section_map.status}")
    section = next((item for item in section_map.sections if item.name == section_name), None)
    if section is None:
        raise ValueError(f"section {section_name!r} not present")

    offsets = section_page_file_offsets(page_map)
    raw = bytes(data)
    buffer = bytearray(section.size)
    for page_number, _data_size, start_offset in sorted(section.pages, key=lambda page: page[2]):
        if page_number not in offsets:
            raise ValueError(f"{section_name!r} page {page_number} absent from the page directory")
        file_offset = offsets[page_number]
        header = decrypt_r2004_data_page_header(
            raw[file_offset : file_offset + DATA_PAGE_HEADER_LENGTH], file_offset
        )
        page_type, _section_number, compressed_size, decompressed_field = struct.unpack_from(
            "<IIII", header, 0
        )
        if page_type != DATA_SECTION_PAGE_TYPE:
            raise ValueError(
                f"{section_name!r} page {page_number} type {page_type:#010x} is not a data page"
            )
        body_offset = file_offset + DATA_PAGE_HEADER_LENGTH
        # Decode the page to its logical slot size (capped by the section end),
        # NOT the page header's under-reporting size word. Fall back to the page
        # word only if the section slot size is unavailable.
        slot = section.max_decompressed_size or decompressed_field
        target = min(slot, section.size - start_offset)
        if target <= 0:
            continue
        decompressed = decompress_r2004(
            raw[body_offset : body_offset + compressed_size], target
        )
        buffer[start_offset : start_offset + len(decompressed)] = decompressed
    return bytes(buffer)


# ---------------------------------------------------------------------------
# Spike S3b: object-stream framing + object-type decode (diagnostic-only).
# ---------------------------------------------------------------------------

#: For R18+, the AcDb:AcDbObjects buffer starts with this RL value.
OBJECT_SECTION_LEADING_RL = 0x0DCA
#: First object begins right after the 4-byte leading RL.
OBJECT_RUN_START_OFFSET = 4
#: An object never exceeds one decompressed page; used as a sanity bound when
#: walking (a too-large MS means we have walked into free space / a gap).
MAX_R2004_OBJECT_BYTES = 0x7400


def read_r2018_object_type(reader: DwgBinaryReader) -> int:
    """Decode an R2010+ object type (public spec 2.12): a bit pair + 1-2 bytes."""

    pair = reader.read_bit_pair()
    if pair == 0:
        return reader.read_bits(8)
    if pair == 1:
        return reader.read_bits(8) + 0x1F0
    # pair 2/3: a 2-byte raw short. Fixed entities (LINE/CIRCLE/... < 0x1F0) use
    # pair 0/1; the raw-short byte order for high custom-object type ids is not
    # yet verified against a custom-object sample.
    return reader.read_bits(16)


def _frame_r2018_object(
    objects_buffer: bytes, offset: int
) -> "Tuple[int, int, int] | None":
    """Frame one object at ``offset`` without decoding its geometry.

    Returns ``(object_size, header_bytes, object_type)`` when a sane object
    frames at ``offset`` (``[MS object-size][MC handle-stream-bits][type ...]``),
    or ``None`` at a free-space gap / malformed framing. ``header_bytes`` is the
    MS+MC field width, so the next object begins at ``offset + header_bytes +
    object_size + 2`` (the trailing RS CRC).
    """

    if offset < 0 or offset >= len(objects_buffer) - 2:
        return None
    try:
        reader = DwgBinaryReader(objects_buffer, offset=offset)
        object_size = reader.read_modular_short()
        if object_size <= 0 or object_size > MAX_R2004_OBJECT_BYTES:
            return None
        reader.read_modular_char()  # MC handle-stream size; advances byte_pos
        header_bytes = reader.byte_pos
        object_type = read_r2018_object_type(reader)
    except DwgBinaryReadError:
        return None
    return object_size, header_bytes, object_type


@dataclass(frozen=True)
class R2018ObjectRun:
    """One contiguous run of decoded object (offset, size, type) tuples.

    A contiguous walk stops at the first free-space gap; full traversal of every
    object across gaps is provided by ``read_r2018_object_table`` (which uses the
    decoded AcDb:Handles index). This walks a contiguous run from a known object
    start and stops at the first free-space gap or the buffer end.
    """

    start_offset: int
    object_count: int
    end_offset: int
    stopped_at_gap: bool
    type_counts: Dict[int, int] = field(default_factory=dict)
    objects: List[Tuple[int, int, int]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_offset": self.start_offset,
            "object_count": self.object_count,
            "end_offset": self.end_offset,
            "stopped_at_gap": self.stopped_at_gap,
            "type_counts": {str(k): v for k, v in self.type_counts.items()},
            "objects": [list(item) for item in self.objects],
        }


def read_r2018_object_run(
    objects_buffer: bytes | bytearray | memoryview,
    *,
    start_offset: int = OBJECT_RUN_START_OFFSET,
    max_objects: int = 1_000_000,
) -> R2018ObjectRun:
    """Walk a contiguous run of objects from ``start_offset``.

    Each object is ``[MS object-size][MC handle-stream-bits][bit stream: MS
    bytes][RS CRC]``; the next object is ``offset + (MS+MC field bytes) + MS +
    2``. Reads only the object TYPE (no geometry yet). Stops at a free-space gap
    (``MS <= 0`` or ``MS`` beyond a page) or the buffer end. This is a heuristic
    contiguous walk: on a gap-free assembled buffer it can over-walk slightly
    into free-space leftovers before stopping, so a few trailing entries may be
    spurious. ``read_r2018_object_table`` (handle-map driven) is the
    authoritative enumeration.
    """

    buffer = bytes(objects_buffer)
    size = len(buffer)
    objects: List[Tuple[int, int, int]] = []
    type_counts: Dict[int, int] = {}
    offset = start_offset
    stopped_at_gap = False

    while offset < size - 2 and len(objects) < max_objects:
        frame = _frame_r2018_object(buffer, offset)
        if frame is None:
            stopped_at_gap = True
            break
        object_size, header_bytes, object_type = frame
        objects.append((offset, object_size, object_type))
        type_counts[object_type] = type_counts.get(object_type, 0) + 1
        offset = offset + header_bytes + object_size + 2

    return R2018ObjectRun(
        start_offset=start_offset,
        object_count=len(objects),
        end_offset=offset,
        stopped_at_gap=stopped_at_gap,
        type_counts=type_counts,
        objects=objects,
    )


# ---------------------------------------------------------------------------
# Spike S3 (part 1): AcDb:Handles index -> full object enumeration across gaps.
# ---------------------------------------------------------------------------

#: The handle map groups (handle, location) delta pairs into sections, each
#: prefixed by a big-endian u16 byte size (counting the 2 size bytes) and
#: followed by a 2-byte CRC. A section whose size is <= 2 terminates the map.
HANDLE_MAP_SECTION_SIZE_BYTES = 2
HANDLE_MAP_SECTION_CRC_BYTES = 2


def parse_r2018_handle_map(
    handles_buffer: bytes | bytearray | memoryview,
) -> Tuple[List[Tuple[int, int]], Dict[str, Any]]:
    """Decode an ``AcDb:Handles`` buffer into ``(handle, object_offset)`` pairs.

    The R2004+ handle map (public DWG spec) is a sequence of sections; each is a
    big-endian u16 size, then ``(handle-delta MC, location-delta signed MC)``
    pairs filling ``size - 2`` bytes, then a 2-byte CRC. A section with size
    ``<= 2`` ends the map. Handles accumulate as unsigned deltas (always
    increasing); locations accumulate as signed deltas into the decompressed
    ``AcDb:AcDbObjects`` buffer. Returns the pairs plus an info dict
    (``section_count``, ``clean_terminator``, ``consumed``). Pure decode: takes
    the already-extracted handle buffer so it is unit-testable in isolation.
    """

    buffer = bytes(handles_buffer)
    size = len(buffer)
    pairs: List[Tuple[int, int]] = []
    pos = 0
    last_handle = 0
    last_offset = 0
    section_count = 0
    clean_terminator = False
    while pos + HANDLE_MAP_SECTION_SIZE_BYTES <= size:
        section_size = struct.unpack_from(">H", buffer, pos)[0]
        pos += HANDLE_MAP_SECTION_SIZE_BYTES
        if section_size <= HANDLE_MAP_SECTION_SIZE_BYTES:
            clean_terminator = True
            break
        data_size = section_size - HANDLE_MAP_SECTION_SIZE_BYTES
        if pos + data_size + HANDLE_MAP_SECTION_CRC_BYTES > size:
            break
        sub = DwgBinaryReader(buffer, offset=pos, length=data_size)
        pos += data_size + HANDLE_MAP_SECTION_CRC_BYTES
        section_count += 1
        try:
            # A minimal (handle, location) pair needs at least two bytes; fewer
            # trailing bytes are section padding.
            while sub.remaining() >= 2:
                last_handle += sub.read_modular_char(signed=False)
                last_offset += sub.read_modular_char(signed=True)
                pairs.append((last_handle, last_offset))
        except DwgBinaryReadError:
            break
    return pairs, {
        "section_count": section_count,
        "clean_terminator": clean_terminator,
        "consumed": pos,
    }


@dataclass(frozen=True)
class R2018HandleMapDiagnostic:
    """Decoded AcDb:Handles index (handle -> object-buffer offset)."""

    version_code: str
    status: str  # decoded | <upstream section-map status>
    entry_count: int
    in_bounds_count: int  # entries whose offset lands in the object buffer
    handles_increasing: bool
    clean_terminator: bool
    section_count: int
    object_buffer_size: int
    entries: List[Tuple[int, int]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_code": self.version_code,
            "status": self.status,
            "entry_count": self.entry_count,
            "in_bounds_count": self.in_bounds_count,
            "handles_increasing": self.handles_increasing,
            "clean_terminator": self.clean_terminator,
            "section_count": self.section_count,
            "object_buffer_size": self.object_buffer_size,
            "entries": [list(item) for item in self.entries],
            "message": self.message,
        }


def read_r2018_handle_map(
    data: bytes | bytearray | memoryview,
    *,
    version_code: str = R2018_VERSION_CODE,
) -> R2018HandleMapDiagnostic:
    """Decode the AcDb:Handles index into (handle, object-offset) entries.

    Extracts the ``AcDb:Handles`` and ``AcDb:AcDbObjects`` sections, decodes the
    handle-map delta pairs, and reports honest coverage: ``in_bounds_count``
    (offsets that land inside the object buffer) vs ``entry_count``. This is
    navigation only — it locates objects, it does not decode their geometry.
    """

    section_map = read_r2004_section_map(data, version_code=version_code)
    if section_map.status != "decoded":
        return R2018HandleMapDiagnostic(
            version_code=version_code,
            status=section_map.status,
            entry_count=0,
            in_bounds_count=0,
            handles_increasing=False,
            clean_terminator=False,
            section_count=0,
            object_buffer_size=0,
            message=section_map.message,
        )

    objects = read_r2004_data_section(
        data, section_name="AcDb:AcDbObjects", version_code=version_code
    )
    handles = read_r2004_data_section(
        data, section_name="AcDb:Handles", version_code=version_code
    )
    pairs, info = parse_r2018_handle_map(handles)
    object_size = len(objects)
    in_bounds = sum(1 for _handle, offset in pairs if 0 <= offset < object_size)
    increasing = all(pairs[i][0] <= pairs[i + 1][0] for i in range(len(pairs) - 1))
    return R2018HandleMapDiagnostic(
        version_code=version_code,
        status="decoded",
        entry_count=len(pairs),
        in_bounds_count=in_bounds,
        handles_increasing=increasing,
        clean_terminator=bool(info["clean_terminator"]),
        section_count=int(info["section_count"]),
        object_buffer_size=object_size,
        entries=pairs,
        message=(
            f"decoded {len(pairs)} handle-map entries "
            f"({in_bounds} within the {object_size}-byte object buffer)"
        ),
    )


@dataclass(frozen=True)
class R2018ObjectTable:
    """Object enumeration framed from the AcDb:Handles index, across gaps.

    Unlike ``read_r2018_object_run`` (one contiguous run), this frames objects
    the handle map points at anywhere in the buffer, not just the first run.
    ``framed_count`` objects framed sanely; ``unframed_count`` in-bounds handle
    entries did NOT frame (surfaced, never silently dropped). NOTE: a handle
    entry can fail to frame because the multi-page ``AcDb:AcDbObjects`` logical
    assembly leaves zero gaps at its offset (the page ``start_offset`` slots are
    0x7400-aligned but decompressed page sizes differ) — so ``unframed_count``
    currently also measures that assembly gap, not only genuine free space.
    """

    version_code: str
    status: str  # decoded | <upstream status>
    handle_entry_count: int
    framed_count: int
    unframed_count: int
    type_counts: Dict[int, int] = field(default_factory=dict)
    objects: List[Tuple[int, int, int, int]] = field(default_factory=list)  # (handle, offset, size, type)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_code": self.version_code,
            "status": self.status,
            "handle_entry_count": self.handle_entry_count,
            "framed_count": self.framed_count,
            "unframed_count": self.unframed_count,
            "type_counts": {str(k): v for k, v in self.type_counts.items()},
            "objects": [list(item) for item in self.objects],
            "message": self.message,
        }


def read_r2018_object_table(
    data: bytes | bytearray | memoryview,
    *,
    version_code: str = R2018_VERSION_CODE,
) -> R2018ObjectTable:
    """Enumerate the objects the AcDb:Handles index locates (beyond one run).

    For each in-bounds handle entry, frame the object (size + type) at its
    offset. Out-of-buffer handle entries (stale handles / other sections) are
    skipped; in-bounds entries that fail to frame are counted as
    ``unframed_count`` rather than dropped (see ``R2018ObjectTable``: this also
    catches the multi-page assembly gap, not only free space). Diagnostic-only:
    no geometry is decoded.
    """

    handle_map = read_r2018_handle_map(data, version_code=version_code)
    if handle_map.status != "decoded":
        return R2018ObjectTable(
            version_code=version_code,
            status=handle_map.status,
            handle_entry_count=0,
            framed_count=0,
            unframed_count=0,
            message=handle_map.message,
        )

    objects = read_r2004_data_section(
        data, section_name="AcDb:AcDbObjects", version_code=version_code
    )
    object_size = len(objects)
    framed: List[Tuple[int, int, int, int]] = []
    type_counts: Dict[int, int] = {}
    unframed = 0
    for handle, offset in handle_map.entries:
        if not 0 <= offset < object_size:
            continue
        frame = _frame_r2018_object(objects, offset)
        if frame is None:
            unframed += 1
            continue
        object_bytes, _header_bytes, object_type = frame
        framed.append((handle, offset, object_bytes, object_type))
        type_counts[object_type] = type_counts.get(object_type, 0) + 1

    return R2018ObjectTable(
        version_code=version_code,
        status="decoded",
        handle_entry_count=handle_map.entry_count,
        framed_count=len(framed),
        unframed_count=unframed,
        type_counts=type_counts,
        objects=framed,
        message=(
            f"framed {len(framed)} objects from {handle_map.entry_count} "
            f"handle-map entries ({unframed} in-bounds entries did not frame)"
        ),
    )


# ---------------------------------------------------------------------------
# Spike S3 (part 2): basic entity geometry decode (LINE/CIRCLE/ARC/POINT).
# Diagnostic-only; geometry validated 1:1 against ODA-converted ground truth.
# ---------------------------------------------------------------------------


def _read_raw_double(reader: DwgBinaryReader) -> float:
    """RD: a raw little-endian f64 read bit-aligned from the data stream."""

    return struct.unpack("<d", bytes(reader.read_bits(8) for _ in range(8)))[0]


def _read_bit_thickness(reader: DwgBinaryReader) -> float:
    """BT: one flag bit; if set the value is 0, otherwise a BitDouble follows."""

    return 0.0 if reader.read_bit() else reader.read_bit_double()


def _read_bit_extrusion(reader: DwgBinaryReader) -> Tuple[float, float, float]:
    """BE: one flag bit; if set the extrusion is the (0,0,1) default, else 3BD."""

    if reader.read_bit():
        return (0.0, 0.0, 1.0)
    return (reader.read_bit_double(), reader.read_bit_double(), reader.read_bit_double())


def _parse_common_entity_header(reader: DwgBinaryReader) -> int:
    """Consume the R2010+ Common Entity Data, positioning at entity geometry.

    Implements ODA spec 20.4.1 (Common Entity Data) for R2018; the field order
    is validated 1:1 against ODA ground truth on real AC1032 entities. Returns
    the object's own handle value. The reader must already be positioned past
    the object type.

    Caveat: the R2013+ "has data-store binary data" bit is intentionally omitted
    — it is absent on files without a data-store section (confirmed on the
    validation corpus); a data-store sample would need it reinstated. Entities
    that carry a proxy graphic image raise ``DwgBinaryReadError`` (unsupported).
    """

    handle = reader.read_handle()  # H: object's own handle
    while True:  # EED: BS size, the owning APPID handle, then that many bytes
        eed_size = reader.read_bit_short()
        if eed_size <= 0:
            break
        reader.read_handle()  # EED block's owning APPID handle (inline)
        for _ in range(eed_size):
            reader.read_bits(8)
    if reader.read_bit():  # B: graphic image present
        raise DwgBinaryReadError("entity proxy graphic image is not supported")
    reader.read_bits(2)            # BB: entity mode
    reader.read_bit_long()         # BL: number of reactors
    reader.read_bit()              # B: XDictionary-missing flag (R2004+)
    reader.read_bit()              # B: no-links flag (R2004+ always 1)
    color = reader.read_bit_short() & 0xFFFF  # ENC: entity colour number + flags
    if (color & 0x8000) and not (color & 0x4000):
        reader.read_bit_long()     # complex colour: RGB value in the data stream
    if color & 0x2000:
        reader.read_bit_long()     # colour transparency
    reader.read_bit_double()       # BD: linetype scale
    reader.read_bits(2)            # BB: linetype flags
    reader.read_bits(2)            # BB: plotstyle flags
    reader.read_bits(2)            # BB: material flags (R2007+)
    reader.read_bits(8)            # RC: shadow flags
    reader.read_bit()              # B: has full visual style (R2010+)
    reader.read_bit()              # B: has face visual style (R2010+)
    reader.read_bit()              # B: has edge visual style (R2010+)
    reader.read_bit_short()        # BS: invisibility flag
    reader.read_bits(8)            # RC: lineweight
    return handle.value


def _decode_line_geometry(reader: DwgBinaryReader) -> Dict[str, Any]:
    z_is_zero = reader.read_bit()  # B: Z coordinates are both zero
    x1 = _read_raw_double(reader)
    x2 = reader.read_bit_double_with_default(x1)  # DD, default = start x
    y1 = _read_raw_double(reader)
    y2 = reader.read_bit_double_with_default(y1)  # DD, default = start y
    if z_is_zero:
        z1 = z2 = 0.0
    else:
        z1 = _read_raw_double(reader)
        z2 = reader.read_bit_double_with_default(z1)
    return {"start": (x1, y1, z1), "end": (x2, y2, z2)}


def _decode_circle_geometry(reader: DwgBinaryReader) -> Dict[str, Any]:
    center = (reader.read_bit_double(), reader.read_bit_double(), reader.read_bit_double())
    return {"center": center, "radius": reader.read_bit_double()}


def _decode_arc_geometry(reader: DwgBinaryReader) -> Dict[str, Any]:
    center = (reader.read_bit_double(), reader.read_bit_double(), reader.read_bit_double())
    radius = reader.read_bit_double()
    _read_bit_thickness(reader)
    _read_bit_extrusion(reader)
    start = reader.read_bit_double()
    end = reader.read_bit_double()
    return {
        "center": center,
        "radius": radius,
        "start_angle_deg": math.degrees(start),
        "end_angle_deg": math.degrees(end),
    }


def _decode_point_geometry(reader: DwgBinaryReader) -> Dict[str, Any]:
    location = (reader.read_bit_double(), reader.read_bit_double(), reader.read_bit_double())
    return {"location": location}


#: LWPOLYLINE flag bits (ODA spec 20.4.85) for optional fields + the closed bit.
_LWPOLY_HAS_CONST_WIDTH = 0x04
_LWPOLY_HAS_ELEVATION = 0x08
_LWPOLY_HAS_THICKNESS = 0x02
_LWPOLY_HAS_EXTRUSION = 0x01
_LWPOLY_HAS_BULGES = 0x10
_LWPOLY_HAS_VERTEX_IDS = 0x400  # R2010+
_LWPOLY_HAS_WIDTHS = 0x20
_LWPOLY_CLOSED = 0x200  # verified against ODA ground truth (closed flags 0x200/0x210)


def _decode_lwpolyline_geometry(reader: DwgBinaryReader) -> Dict[str, Any]:
    flag = reader.read_bit_short()
    if flag & _LWPOLY_HAS_CONST_WIDTH:
        reader.read_bit_double()           # constant width
    if flag & _LWPOLY_HAS_ELEVATION:
        reader.read_bit_double()           # elevation
    if flag & _LWPOLY_HAS_THICKNESS:
        reader.read_bit_double()           # thickness
    if flag & _LWPOLY_HAS_EXTRUSION:       # extrusion 3BD
        reader.read_bit_double(); reader.read_bit_double(); reader.read_bit_double()
    numpoints = reader.read_bit_long()
    if numpoints <= 0:
        raise DwgBinaryReadError(f"LWPOLYLINE vertex count {numpoints} is not positive")
    numbulges = reader.read_bit_long() if (flag & _LWPOLY_HAS_BULGES) else 0
    vertex_id_count = reader.read_bit_long() if (flag & _LWPOLY_HAS_VERTEX_IDS) else 0
    numwidths = reader.read_bit_long() if (flag & _LWPOLY_HAS_WIDTHS) else 0
    # First vertex is a raw 2RD; the rest are 2DD with the previous coordinate
    # as the default (why a naive scan misses repeated coordinates).
    x = _read_raw_double(reader)
    y = _read_raw_double(reader)
    vertices: List[Tuple[float, float]] = [(x, y)]
    for _ in range(numpoints - 1):
        x = reader.read_bit_double_with_default(x)
        y = reader.read_bit_double_with_default(y)
        vertices.append((x, y))
    bulges = [reader.read_bit_double() for _ in range(max(0, numbulges))]
    # (vertex ids + widths follow but are not needed for outline geometry.)
    return {"vertices": vertices, "bulges": bulges, "closed": bool(flag & _LWPOLY_CLOSED)}


#: TEXT (spec type 0x01) needs the R2007+ string stream for its value, so it is
#: decoded on a separate path (not in the geometry-only decoder map).
TEXT_OBJECT_TYPE = 0x01


def _read_text_string_value(
    objects_buffer: bytes,
    data_start_byte: int,
    object_size: int,
    handle_stream_bits: int,
) -> str:
    """Read the first text value (TV) from the R2007+ string stream.

    Layout (ODA spec p.103): the object body is data-stream + string-stream +
    handle-stream. The handle stream is the last ``handle_stream_bits`` bits, so
    ``end_bit = object_size*8 - handle_stream_bits`` ends the pre-handles section.
    Its last bit is the string-stream-present flag; if set, a 16-bit
    ``strDataSize`` (optionally extended via the 0x8000 bit) precedes it and the
    string stream is ``[end_bit-17-strDataSize, end_bit-17)``. Each string there
    is a BS char-count followed by that many UTF-16LE characters. Returns ``''``
    when no string stream is present or on any malformed read (fail-closed).
    """

    end_bit = object_size * 8 - handle_stream_bits
    if end_bit < 1:
        return ""

    def reader_at(bit: int) -> DwgBinaryReader:
        reader = DwgBinaryReader(objects_buffer, offset=data_start_byte, length=object_size)
        reader.seek_bits(bit)
        return reader

    def u16_at(bit: int) -> int:
        reader = reader_at(bit)
        return reader.read_bits(8) | (reader.read_bits(8) << 8)

    try:
        if not reader_at(end_bit - 1).read_bit():
            return ""
        str_data_size = u16_at(end_bit - 17)
        size_fields_end = end_bit - 17
        if str_data_size & 0x8000:
            str_data_size = (str_data_size & 0x7FFF) | (u16_at(end_bit - 33) << 15)
            size_fields_end = end_bit - 33
        string_stream_start = size_fields_end - str_data_size
        if string_stream_start < 0:
            return ""
        reader = reader_at(string_stream_start)
        char_count = reader.read_bit_short()
        if char_count <= 0 or char_count > object_size * 4:
            return ""
        return "".join(
            chr(reader.read_bits(8) | (reader.read_bits(8) << 8)) for _ in range(char_count)
        )
    except DwgBinaryReadError:
        return ""


def _decode_text_geometry(reader: DwgBinaryReader, text_value: str) -> Dict[str, Any]:
    """Decode TEXT (spec 20.4.3, R2000+) geometry; the value comes from the
    string stream (read separately and passed in)."""

    flags = reader.read_bits(8)  # RC DataFlags (presence bits for optional fields)
    if not (flags & 0x01):
        _read_raw_double(reader)  # elevation
    ix = _read_raw_double(reader)
    iy = _read_raw_double(reader)  # insertion point 2RD
    if not (flags & 0x02):
        reader.read_bit_double_with_default(ix)
        reader.read_bit_double_with_default(iy)  # alignment point 2DD
    _read_bit_extrusion(reader)
    _read_bit_thickness(reader)
    if not (flags & 0x04):
        _read_raw_double(reader)  # oblique angle
    rotation = 0.0
    if not (flags & 0x08):
        rotation = _read_raw_double(reader)  # rotation angle
    height = _read_raw_double(reader)
    return {
        "insert": (ix, iy, 0.0),
        "height": height,
        "rotation_deg": math.degrees(rotation),
        "text": text_value,
    }


#: object type -> (canonical name, geometry decoder). Spec 20.3 fixed type ids.
_ENTITY_GEOMETRY_DECODERS = {
    0x11: ("ARC", _decode_arc_geometry),
    0x12: ("CIRCLE", _decode_circle_geometry),
    0x13: ("LINE", _decode_line_geometry),
    0x1B: ("POINT", _decode_point_geometry),
    0x4D: ("LWPOLYLINE", _decode_lwpolyline_geometry),
}


@dataclass(frozen=True)
class R2018Entity:
    """One decoded entity: type + geometry (diagnostic, pre-canonical)."""

    handle: int
    object_type: int
    type_name: str
    geometry: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handle": self.handle,
            "object_type": self.object_type,
            "type_name": self.type_name,
            "geometry": self.geometry,
        }


def decode_r2018_entity(
    objects_buffer: bytes | bytearray | memoryview, offset: int
) -> "Optional[R2018Entity]":
    """Decode the geometry of a supported entity at ``offset`` in the buffer.

    Returns an ``R2018Entity`` for LINE/CIRCLE/ARC/POINT, or ``None`` for an
    unsupported type, a free-space gap, or any malformed/unsupported field
    (fail-closed: never returns partial/garbage geometry).
    """

    frame = _frame_r2018_object(objects_buffer, offset)
    if frame is None:
        return None
    object_size, header_bytes, object_type = frame
    is_text = object_type == TEXT_OBJECT_TYPE
    decoder = _ENTITY_GEOMETRY_DECODERS.get(object_type)
    if decoder is None and not is_text:
        return None
    data_start_byte = offset + header_bytes
    reader = DwgBinaryReader(objects_buffer, offset=data_start_byte, length=object_size)
    try:
        read_r2018_object_type(reader)  # advance past the object type
        handle = _parse_common_entity_header(reader)
        if is_text:
            # the value lives in the string stream; the handle-stream size (the
            # MC consumed at framing) locates it, so re-read it from the framing.
            framing = DwgBinaryReader(objects_buffer, offset=offset)
            framing.read_modular_short()
            handle_stream_bits = framing.read_modular_char()
            text_value = _read_text_string_value(
                objects_buffer, data_start_byte, object_size, handle_stream_bits
            )
            type_name = "TEXT"
            geometry = _decode_text_geometry(reader, text_value)
        else:
            type_name, decode = decoder
            geometry = decode(reader)
    except DwgBinaryReadError:
        return None
    return R2018Entity(
        handle=handle, object_type=object_type, type_name=type_name, geometry=geometry
    )


@dataclass(frozen=True)
class R2018EntityTable:
    """All supported entity geometry decoded from a real AC1032 file."""

    version_code: str
    status: str  # decoded | <upstream status>
    decoded_count: int
    type_counts: Dict[str, int] = field(default_factory=dict)
    entities: List[R2018Entity] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_code": self.version_code,
            "status": self.status,
            "decoded_count": self.decoded_count,
            "type_counts": dict(self.type_counts),
            "entities": [entity.to_dict() for entity in self.entities],
            "message": self.message,
        }


def read_r2018_entities(
    data: bytes | bytearray | memoryview,
    *,
    version_code: str = R2018_VERSION_CODE,
) -> R2018EntityTable:
    """Decode every supported entity (LINE/CIRCLE/ARC/POINT) the handle map locates.

    Enumerates objects via the AcDb:Handles index and decodes the geometry of
    supported entity types. Diagnostic-only — the result is pre-canonical
    geometry, not wired to the product diff/render pipeline.
    """

    handle_map = read_r2018_handle_map(data, version_code=version_code)
    if handle_map.status != "decoded":
        return R2018EntityTable(
            version_code=version_code,
            status=handle_map.status,
            decoded_count=0,
            message=handle_map.message,
        )

    objects = read_r2004_data_section(
        data, section_name="AcDb:AcDbObjects", version_code=version_code
    )
    object_size = len(objects)
    entities: List[R2018Entity] = []
    type_counts: Dict[str, int] = {}
    for _handle, offset in handle_map.entries:
        if not 0 <= offset < object_size:
            continue
        entity = decode_r2018_entity(objects, offset)
        if entity is None:
            continue
        entities.append(entity)
        type_counts[entity.type_name] = type_counts.get(entity.type_name, 0) + 1

    return R2018EntityTable(
        version_code=version_code,
        status="decoded",
        decoded_count=len(entities),
        type_counts=type_counts,
        entities=entities,
        message=f"decoded geometry for {len(entities)} entities {type_counts}",
    )


# ---------------------------------------------------------------------------
# Spike S4 (render bridge): decoded entities -> canonical-drawing/v1 document.
# The document feeds the existing native scene-pack producer
# (native_scene_pack_builder.build_native_scene_pack) and the viewport seam,
# with ZERO ODA/ezdxf calls. Diagnostic-only; not wired to product import.
# ---------------------------------------------------------------------------

_CANONICAL_ENTITY_TYPE_NAMES = {
    "LINE": "line",
    "CIRCLE": "circle",
    "ARC": "arc",
    "LWPOLYLINE": "polyline",
    # POINT/TEXT are not rendered by the scene-pack producer (counted unsupported,
    # visible) but TEXT carries the value the structural diff cares about.
    "POINT": "point",
    "TEXT": "text",
}


def _canonical_point(xyz: Tuple[float, float, float]) -> Dict[str, float]:
    return {"x": xyz[0], "y": xyz[1], "z": xyz[2]}


def _r2018_entity_bbox(entity: R2018Entity) -> Dict[str, float]:
    """A 2D world bbox (min_x/min_y/max_x/max_y) for one decoded entity."""

    geometry = entity.geometry
    if entity.type_name == "LINE":
        xs = [geometry["start"][0], geometry["end"][0]]
        ys = [geometry["start"][1], geometry["end"][1]]
    elif entity.type_name in ("CIRCLE", "ARC"):
        cx, cy, radius = geometry["center"][0], geometry["center"][1], geometry["radius"]
        xs = [cx - radius, cx + radius]
        ys = [cy - radius, cy + radius]
    elif entity.type_name == "LWPOLYLINE":
        xs = [vertex[0] for vertex in geometry["vertices"]]
        ys = [vertex[1] for vertex in geometry["vertices"]]
    elif entity.type_name == "TEXT":
        ix, iy = geometry["insert"][0], geometry["insert"][1]
        height = geometry["height"]
        width = height * max(1, len(geometry["text"])) * 0.6  # rough advance width
        xs = [ix, ix + width]
        ys = [iy, iy + height]
    else:  # POINT
        xs = [geometry["location"][0]]
        ys = [geometry["location"][1]]
    return {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)}


def r2018_entity_to_canonical(entity: R2018Entity) -> Dict[str, Any]:
    """Convert a decoded ``R2018Entity`` to a ``canonical-drawing/v1`` entity dict.

    The shape matches what ``native_scene_pack_builder.build_native_scene_pack``
    consumes (lowercased ``type`` + a ``geometry`` mapping of ``{x, y, z}``
    points). LINE/CIRCLE/ARC flatten to viewer line primitives; POINT is emitted
    as ``type: "point"`` which the producer records as unsupported, not dropped.
    """

    geometry = entity.geometry
    canonical_type = _CANONICAL_ENTITY_TYPE_NAMES.get(entity.type_name, entity.type_name.lower())
    out: Dict[str, Any] = {
        "id": f"{canonical_type}:{entity.handle:X}",
        "type": canonical_type,
        "layer_id": "",
        "space": "model",
        "handle": f"{entity.handle:X}",
        "bbox": _r2018_entity_bbox(entity),
    }
    if entity.type_name == "LINE":
        out["geometry"] = {
            "type": "line",
            "start": _canonical_point(geometry["start"]),
            "end": _canonical_point(geometry["end"]),
        }
    elif entity.type_name == "CIRCLE":
        out["geometry"] = {
            "type": "circle",
            "center": _canonical_point(geometry["center"]),
            "radius": geometry["radius"],
        }
    elif entity.type_name == "ARC":
        out["geometry"] = {
            "type": "arc",
            "center": _canonical_point(geometry["center"]),
            "radius": geometry["radius"],
            "start_angle_deg": geometry["start_angle_deg"],
            "end_angle_deg": geometry["end_angle_deg"],
        }
    elif entity.type_name == "LWPOLYLINE":
        out["geometry"] = {
            "type": "polyline",
            "vertices": [
                {"point": {"x": vx, "y": vy, "z": 0.0}, "bulge": bulge}
                for (vx, vy), bulge in zip(
                    geometry["vertices"],
                    list(geometry["bulges"]) + [0.0] * len(geometry["vertices"]),
                )
            ],
            "closed": geometry["closed"],
        }
    elif entity.type_name == "TEXT":
        out["geometry"] = {
            "type": "text",
            "insert": _canonical_point(geometry["insert"]),
            "height": geometry["height"],
            "rotation_deg": geometry["rotation_deg"],
            "text": geometry["text"],
            "canonical_text": geometry["text"],
        }
    else:  # POINT
        out["geometry"] = {"type": "point", "location": _canonical_point(geometry["location"])}
    return out


def _r2018_entities_extents(entities: List[R2018Entity]) -> "Optional[Dict[str, float]]":
    boxes = [_r2018_entity_bbox(entity) for entity in entities]
    if not boxes:
        return None
    return {
        "min_x": min(box["min_x"] for box in boxes),
        "min_y": min(box["min_y"] for box in boxes),
        "max_x": max(box["max_x"] for box in boxes),
        "max_y": max(box["max_y"] for box in boxes),
    }


def build_r2018_canonical_document(
    data: bytes | bytearray | memoryview,
    *,
    version_code: str = R2018_VERSION_CODE,
    source_path: str = "",
) -> Dict[str, Any]:
    """Decode AC1032 entities into a ``canonical-drawing/v1`` document.

    The returned document (``entities`` + ``extents``) is exactly what the
    existing ``build_native_scene_pack`` producer flattens into viewport line
    primitives — so the own clean-room reader can drive the same render seam the
    ezdxf path uses, with ZERO ODA/ezdxf calls. Diagnostic-only: it is NOT wired
    to the product import pipeline and carries no support claim.
    """

    table = read_r2018_entities(data, version_code=version_code)
    entities = [r2018_entity_to_canonical(entity) for entity in table.entities]
    extents = _r2018_entities_extents(table.entities)
    document: Dict[str, Any] = {
        "schema_version": "canonical-drawing/v1",
        "drawing": {
            "source": {"path": source_path, "acad_version": version_code, "format": "dwg"},
            "importer": {"name": "native-ac1032-spike", "backend": "native"},
        },
        "layers": [],
        "blocks": [],
        "entities": entities,
        "import_report": {
            "status": table.status,
            "error_code": None,
            "adapter": {"name": "native-ac1032-spike"},
        },
    }
    if extents is not None:
        document["extents"] = extents
    return document


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
    "DATA_PAGE_HEADER_LENGTH",
    "OBJECT_SECTION_LEADING_RL",
    "OBJECT_RUN_START_OFFSET",
    "MAX_R2004_OBJECT_BYTES",
    "HANDLE_MAP_SECTION_SIZE_BYTES",
    "HANDLE_MAP_SECTION_CRC_BYTES",
    "DwgR2018ContainerDiagnostic",
    "R2004SectionPageMapDiagnostic",
    "R2004Section",
    "R2004SectionMapDiagnostic",
    "R2018ObjectRun",
    "R2018HandleMapDiagnostic",
    "R2018ObjectTable",
    "R2018Entity",
    "R2018EntityTable",
    "deobfuscate_r2004_header",
    "inspect_r2018_container",
    "decompress_r2004",
    "read_r2004_section_page_map",
    "section_page_file_offsets",
    "read_r2004_section_map",
    "decrypt_r2004_data_page_header",
    "read_r2004_data_section",
    "read_r2018_object_type",
    "read_r2018_object_run",
    "parse_r2018_handle_map",
    "read_r2018_handle_map",
    "read_r2018_object_table",
    "decode_r2018_entity",
    "read_r2018_entities",
    "r2018_entity_to_canonical",
    "build_r2018_canonical_document",
]
