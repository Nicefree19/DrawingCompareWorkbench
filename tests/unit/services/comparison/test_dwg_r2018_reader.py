# -*- coding: utf-8 -*-
"""Spike S1 tests: AC1032 (R2018) container diagnostic navigator.

Diagnostic-only — these tests prove the R2004+ container is navigable from the
public ODA spec (header de-obfuscation + section-page-map location). They make
no object-decoding or support claim.

CI coverage note: the algorithm tests (LCG, decompression, synthetic fixtures)
run everywhere. The ``test_real_ac1032_*`` integration tests require the local
git-ignored AC1032 corpus and SKIP in CI — real-file verification is local-only.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from src.services.comparison.dwg_r2018_reader import (
    R2004_HEADER_LENGTH,
    R2004_HEADER_MAGIC,
    R2004_HEADER_OFFSET,
    decompress_r2004,
    deobfuscate_r2004_header,
    inspect_r2018_container,
    parse_r2018_handle_map,
    read_r2004_data_section,
    read_r2004_section_map,
    read_r2004_section_page_map,
    read_r2018_handle_map,
    read_r2018_object_run,
    read_r2018_object_table,
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


# ---- Spike S2: R2004 decompression + section-page-map ----


def test_decompress_r2004_handcrafted_stream() -> None:
    # 0x05 -> initial literal length 8 ("ABCDEFGH"); 0x4C/0x01 -> back-copy 3
    # ("ABC") from offset 7; trailing literal length 0x01 -> 4 ("WXYZ"); 0x11 end.
    stream = bytes(
        [0x05]
        + list(b"ABCDEFGH")
        + [0x4C, 0x01, 0x01]
        + list(b"WXYZ")
        + [0x11]
    )

    out = decompress_r2004(stream, decompressed_size=15)

    assert out == b"ABCDEFGHABCWXYZ"


def test_decompress_r2004_pure_literal_run() -> None:
    # 0x02 -> literal length 5; copy 5 literal bytes; terminator.
    stream = bytes([0x02] + list(b"HELLO") + [0x11])
    assert decompress_r2004(stream, decompressed_size=5) == b"HELLO"


def test_decompress_r2004_stream_ending_exactly_at_boundary() -> None:
    # Regression: a stream that completes the output with no trailing opcode/
    # terminator must not over-read past the end (the boundary guard stops
    # instead of consuming another byte).
    assert decompress_r2004(bytes([0x02]) + b"HELLO", decompressed_size=5) == b"HELLO"


def test_decompress_r2004_raises_on_underrun() -> None:
    with pytest.raises(ValueError):
        decompress_r2004(bytes([0x05, 0x41, 0x42]), decompressed_size=8)


def test_decompress_r2004_literal_length_byte_with_high_nibble_is_next_opcode() -> None:
    # Regression: a literal-length byte with any high-nibble bit set is NOT a
    # length (count 0) but IS the next opcode. 0x02 -> 5 literals ("HELLO");
    # 0x21 copies 3 from offset 0 ("OOO"); then 0x11 (high nibble set) is the
    # next opcode (terminator), not a 0x14-byte literal run.
    stream = bytes([0x02]) + b"HELLO" + bytes([0x21, 0x00, 0x00, 0x11])
    assert decompress_r2004(stream, decompressed_size=8) == b"HELLOOOO"


@pytest.mark.parametrize("sample", REAL_AC1032_SAMPLES, ids=lambda p: p.name)
def test_real_ac1032_section_page_map_decodes(sample: Path) -> None:
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    page_map = read_r2004_section_page_map(sample.read_bytes())

    assert page_map.status == "decoded", page_map.message
    assert page_map.page_count > 0
    # A correctly decompressed page map starts at page 1 with strictly
    # increasing, bounded page numbers (gaps from unwritten zero-pages are
    # allowed). A wrong back-reference offset yields garbage page numbers
    # (~537M), which both checks below reject.
    assert page_map.first_page_number == 1
    assert page_map.positive_pages_increasing is True
    assert page_map.max_page_number <= page_map.page_count + 8
    assert all(size > 0 for _page, size in page_map.entries if _page > 0)


@pytest.mark.parametrize("sample", REAL_AC1032_SAMPLES, ids=lambda p: p.name)
def test_real_ac1032_section_map_enumerates_object_sections(sample: Path) -> None:
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    section_map = read_r2004_section_map(sample.read_bytes())

    assert section_map.status == "decoded", section_map.message
    assert section_map.section_count > 0
    # The whole point of S2: locate the object/handle data sections by name.
    assert section_map.has_acdbobjects is True
    assert section_map.has_handles is True
    assert "AcDb:Header" in section_map.section_names
    objects = next(s for s in section_map.sections if s.name == "AcDb:AcDbObjects")
    assert objects.page_count >= 1
    assert objects.size > 0
    assert len(objects.pages) == objects.page_count


@pytest.mark.parametrize("sample", REAL_AC1032_SAMPLES, ids=lambda p: p.name)
def test_real_ac1032_reads_acdbobjects_data_section(sample: Path) -> None:
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    raw = sample.read_bytes()
    section_map = read_r2004_section_map(raw)
    objects = next(s for s in section_map.sections if s.name == "AcDb:AcDbObjects")

    # S3a: reach the object bytes — decrypt + decompress every AcDb:AcDbObjects
    # page and assemble them to the exact section size.
    buffer = read_r2004_data_section(raw, section_name="AcDb:AcDbObjects")
    assert len(buffer) == objects.size
    assert objects.size > 1000  # the object database is substantial

    # AcDb:Handles is reachable the same way.
    handles = read_r2004_data_section(raw, section_name="AcDb:Handles")
    handle_section = next(s for s in section_map.sections if s.name == "AcDb:Handles")
    assert len(handles) == handle_section.size


@pytest.mark.parametrize("sample", REAL_AC1032_SAMPLES, ids=lambda p: p.name)
def test_real_ac1032_object_run_decodes_object_types(sample: Path) -> None:
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    objects = read_r2004_data_section(sample.read_bytes(), section_name="AcDb:AcDbObjects")
    # The R18+ object buffer starts with the RL 0x0dca, then objects.
    assert int.from_bytes(objects[0:4], "little") == 0x0DCA

    run = read_r2018_object_run(objects)

    # S3b: the object framing (MS/MC stride) + object-type decode (BB + 1-2
    # bytes) must yield a clean run of real DWG object types, not garbage.
    assert run.object_count >= 30, run.to_dict()
    assert run.stopped_at_gap is True  # a contiguous run, not the whole section
    # Every decoded type is a plausible fixed DWG object type (garbage framing
    # produces large/random type ids).
    assert all(0 <= t < 1024 for t in run.type_counts), run.type_counts
    # The run contains real geometry/structure (LINE=19, CIRCLE=18, ARC=17,
    # LWPOLYLINE=77, POINT=27).
    assert any(t in run.type_counts for t in (17, 18, 19, 27, 77)), run.type_counts
    # First object decodes at the section start.
    assert run.objects[0][0] == 4


# ---- Spike S3 (part 1): AcDb:Handles index -> full object enumeration ----


def _mc_unsigned(value: int) -> bytes:
    """Encode an unsigned modular char (matches DwgBinaryReader.read_modular_char)."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _mc_signed_small(value: int) -> bytes:
    """Encode a single-byte signed modular char (test values in [-63, 63])."""
    assert -63 <= value <= 63
    return bytes([(abs(value) | 0x40) if value < 0 else value])


def _handle_map_section(pair_deltas: list[tuple[int, int]]) -> bytes:
    data = bytearray()
    for handle_delta, offset_delta in pair_deltas:
        data += _mc_unsigned(handle_delta) + _mc_signed_small(offset_delta)
    size = 2 + len(data)
    return struct.pack(">H", size) + bytes(data) + b"\x00\x00"  # + 2 CRC bytes


def _handle_map_buffer(sections: list[list[tuple[int, int]]]) -> bytes:
    out = bytearray()
    for section in sections:
        out += _handle_map_section(section)
    out += struct.pack(">H", 2)  # empty terminator section
    return bytes(out)


def test_parse_handle_map_accumulates_handle_and_signed_offset_deltas() -> None:
    # Handles accumulate as unsigned deltas; locations as signed deltas (note
    # the negative third delta moves the offset backwards).
    buf = _handle_map_buffer([[(1, 4), (5, 50), (2, -10)]])

    pairs, info = parse_r2018_handle_map(buf)

    assert pairs == [(1, 4), (6, 54), (8, 44)]
    assert info["section_count"] == 1
    assert info["clean_terminator"] is True


def test_parse_handle_map_spans_multiple_sections() -> None:
    buf = _handle_map_buffer([[(1, 4), (3, 20)], [(2, 30)]])

    pairs, info = parse_r2018_handle_map(buf)

    assert pairs == [(1, 4), (4, 24), (6, 54)]
    assert info["section_count"] == 2
    assert info["clean_terminator"] is True


def test_parse_handle_map_tolerates_missing_terminator() -> None:
    # A complete section with no trailing terminator decodes its pairs and
    # reports an unclean terminator instead of raising.
    buf = _handle_map_section([(1, 4), (2, 8)])  # no terminator section appended

    pairs, info = parse_r2018_handle_map(buf)

    assert pairs == [(1, 4), (3, 12)]
    assert info["clean_terminator"] is False


@pytest.mark.parametrize("sample", REAL_AC1032_SAMPLES, ids=lambda p: p.name)
def test_real_ac1032_handle_map_decodes_and_locates_run(sample: Path) -> None:
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    raw = sample.read_bytes()
    objects = read_r2004_data_section(raw, section_name="AcDb:AcDbObjects")
    run = read_r2018_object_run(objects)
    handle_map = read_r2018_handle_map(raw)

    assert handle_map.status == "decoded", handle_map.message
    # Handles are stored as strictly accumulating deltas; a structural decode
    # error would scramble them.
    assert handle_map.handles_increasing is True
    # The map lists far more objects than a single contiguous run.
    assert handle_map.entry_count >= run.object_count
    # The map's offsets index the decompressed object buffer.
    assert 0 < handle_map.in_bounds_count <= handle_map.entry_count
    # Most objects the independent contiguous run found are located by the map
    # (proves the handle->offset decode is real, not coincidental framing).
    run_offsets = {offset for offset, _s, _t in run.objects}
    map_offsets = {offset for _h, offset in handle_map.entries}
    covered = run_offsets & map_offsets
    assert len(covered) >= 0.85 * len(run_offsets), (
        f"{len(covered)}/{len(run_offsets)} run objects located by the handle map"
    )


def test_real_ac1032_handle_map_fully_covers_run_on_primary_sample() -> None:
    # The primary acadsharp sample is small enough that EVERY contiguous-run
    # object is located by the handle map (exact 1:1 offset coverage).
    sample = REAL_AC1032_SAMPLES[0]
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    raw = sample.read_bytes()
    objects = read_r2004_data_section(raw, section_name="AcDb:AcDbObjects")
    run = read_r2018_object_run(objects)
    handle_map = read_r2018_handle_map(raw)

    run_offsets = {offset for offset, _s, _t in run.objects}
    map_offsets = {offset for _h, offset in handle_map.entries}
    missing = sorted(run_offsets - map_offsets)
    assert not missing, f"run objects missing from the handle map: {missing[:8]}"
    assert handle_map.clean_terminator is True


def test_real_ac1032_object_table_frames_beyond_one_run() -> None:
    sample = REAL_AC1032_SAMPLES[0]
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    raw = sample.read_bytes()
    run = read_r2018_object_run(
        read_r2004_data_section(raw, section_name="AcDb:AcDbObjects")
    )
    table = read_r2018_object_table(raw)

    assert table.status == "decoded", table.message
    # Crossing free-space gaps via the handle map frames more objects than the
    # single contiguous run can reach.
    assert table.framed_count > run.object_count
    # Real geometry is among them (LINE == 19).
    assert 19 in table.type_counts, table.type_counts
    # Framed + unframed accounting stays within the in-bounds handle entries.
    assert table.framed_count + table.unframed_count <= table.handle_entry_count
