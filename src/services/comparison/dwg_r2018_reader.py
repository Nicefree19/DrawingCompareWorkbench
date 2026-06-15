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
from typing import Any, Dict, List, Tuple

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
]
