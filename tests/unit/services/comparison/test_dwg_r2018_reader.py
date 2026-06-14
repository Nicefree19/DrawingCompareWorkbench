# -*- coding: utf-8 -*-
"""Spike S1 tests: AC1032 (R2018) container diagnostic navigator.

Diagnostic-only — these tests prove the R2004+ container is navigable from the
public ODA spec (header de-obfuscation + section-page-map location). They make
no object-decoding or support claim.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from src.services.comparison.dwg_r2018_reader import (
    R2004_HEADER_LENGTH,
    R2004_HEADER_MAGIC,
    R2004_HEADER_OFFSET,
    deobfuscate_r2004_header,
    inspect_r2018_container,
)

# Local-only real AC1032 corpus (git-ignored). The integration test runs the
# real container navigation when present and skips visibly otherwise.
REAL_AC1032_SAMPLES = (
    Path(".local/native_cad_real_samples/acadsharp/sample_AC1032.dwg"),
    Path(".local/native_cad_real_samples/calpoly_floor_plans/Building001-0_Floor2.dwg"),
)


def _synthetic_ac1032(
    *,
    page_map_address: int,
    page_map_id: int,
    section_map_id: int,
    total_size: int,
) -> bytes:
    """Build a minimal AC1032 file whose de-obfuscated header carries known fields."""

    header = bytearray(R2004_HEADER_LENGTH)
    header[0 : len(R2004_HEADER_MAGIC)] = R2004_HEADER_MAGIC
    struct.pack_into("<I", header, 0x50, page_map_id)
    struct.pack_into("<Q", header, 0x54, page_map_address)
    struct.pack_into("<I", header, 0x5C, section_map_id)
    # The LCG XOR is symmetric, so obfuscating uses the same transform.
    obfuscated = deobfuscate_r2004_header(bytes(header))

    data = bytearray(max(total_size, R2004_HEADER_OFFSET + R2004_HEADER_LENGTH))
    data[0:6] = b"AC1032"
    data[R2004_HEADER_OFFSET : R2004_HEADER_OFFSET + R2004_HEADER_LENGTH] = obfuscated
    return bytes(data)


def test_lcg_deobfuscation_is_symmetric() -> None:
    plain = bytes(range(R2004_HEADER_LENGTH))
    assert deobfuscate_r2004_header(deobfuscate_r2004_header(plain)) == plain


def test_inspect_r2018_round_trips_header_fields() -> None:
    data = _synthetic_ac1032(
        page_map_address=0x1000, page_map_id=58, section_map_id=57, total_size=0x4000
    )

    diag = inspect_r2018_container(data)

    assert diag.magic_ok is True
    assert diag.status == "navigable"
    assert diag.fields["section_page_map_id"] == 58
    assert diag.fields["section_page_map_address"] == 0x1000
    assert diag.fields["section_page_map_file_offset"] == 0x1100
    assert diag.fields["section_page_map_in_bounds"] is True
    assert diag.fields["section_map_id"] == 57


def test_inspect_r2018_flags_out_of_bounds_page_map() -> None:
    data = _synthetic_ac1032(
        page_map_address=0xFFFFFF, page_map_id=1, section_map_id=2, total_size=0x4000
    )

    diag = inspect_r2018_container(data)

    assert diag.magic_ok is True
    assert diag.status == "page_map_out_of_bounds"
    assert diag.fields["section_page_map_in_bounds"] is False


def test_inspect_r2018_rejects_wrong_version() -> None:
    diag = inspect_r2018_container(b"AC1015" + b"\x00" * 0x200)
    assert diag.magic_ok is False
    assert diag.status == "wrong_version"
    assert diag.fields["actual_version"] == "AC1015"


def test_inspect_r2018_flags_magic_mismatch_on_garbage() -> None:
    data = bytearray(0x200)
    data[0:6] = b"AC1032"
    data[R2004_HEADER_OFFSET : R2004_HEADER_OFFSET + R2004_HEADER_LENGTH] = b"\x11" * R2004_HEADER_LENGTH

    diag = inspect_r2018_container(bytes(data))

    assert diag.magic_ok is False
    assert diag.status == "magic_mismatch"


def test_inspect_r2018_too_short() -> None:
    diag = inspect_r2018_container(b"AC1032" + b"\x00" * 4)
    assert diag.status == "too_short"
    assert diag.magic_ok is False


@pytest.mark.parametrize("sample", REAL_AC1032_SAMPLES, ids=lambda p: p.name)
def test_real_ac1032_container_is_navigable(sample: Path) -> None:
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    diag = inspect_r2018_container(sample.read_bytes())

    assert diag.magic_ok is True, diag.message
    assert diag.status == "navigable", diag.to_dict()
    assert diag.fields["section_page_map_in_bounds"] is True
    # The located section-page-map must sit inside the real file.
    assert 0 < diag.fields["section_page_map_file_offset"] < diag.file_size_bytes
