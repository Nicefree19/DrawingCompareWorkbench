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


__all__ = [
    "R2018_VERSION_CODE",
    "R2004_HEADER_OFFSET",
    "R2004_HEADER_LENGTH",
    "R2004_HEADER_MAGIC",
    "SECTION_PAGE_ADDRESS_BASE",
    "DwgR2018ContainerDiagnostic",
    "deobfuscate_r2004_header",
    "inspect_r2018_container",
]
