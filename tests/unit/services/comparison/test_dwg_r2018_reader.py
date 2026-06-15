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

import copy
import struct
from pathlib import Path

import pytest

from src.services.comparison.dwg_r2018_reader import (
    R2004_HEADER_LENGTH,
    R2004_HEADER_MAGIC,
    R2004_HEADER_OFFSET,
    R2018Entity,
    build_r2018_canonical_document,
    decode_r2018_entity,
    decompress_r2004,
    deobfuscate_r2004_header,
    inspect_r2018_container,
    parse_r2018_handle_map,
    r2018_entity_to_canonical,
    read_r2004_data_section,
    read_r2004_section_map,
    read_r2004_section_page_map,
    read_r2018_entities,
    read_r2018_handle_map,
    read_r2018_object_run,
    read_r2018_object_table,
)
from src.services.comparison.base import ComparisonResult
from src.services.comparison.change_zones import ChangeZoneOptions, build_change_zones
from src.services.comparison.drawing_compare_engine import (
    DrawingCompareEngine,
    DrawingCompareOptions,
)
from src.services.comparison.native_scene_pack_builder import (
    build_native_scene_pack,
    build_native_scene_pack_ref,
)
from src.services.comparison.revision_marker import revcloud_geometry_from_bbox
from src.services.comparison.viewer_primitive_source import resolve_viewer_primitive_source

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
    # The contiguous walk is a heuristic: on the gap-free assembled buffer it can
    # over-walk a little into free-space leftovers, so the VAST MAJORITY (not
    # every) decoded type must be a plausible fixed/custom DWG type id. The
    # handle-map object table is the authoritative enumeration.
    plausible = sum(1 for _offset, _size, t in run.objects if 0 <= t < 1024)
    assert plausible >= 0.98 * run.object_count, run.type_counts
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
def test_real_ac1032_handle_map_decodes_cleanly(sample: Path) -> None:
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    handle_map = read_r2018_handle_map(sample.read_bytes())

    assert handle_map.status == "decoded", handle_map.message
    # Handles are stored as strictly accumulating deltas; a structural decode
    # error would scramble them or leave the map without its terminator.
    assert handle_map.handles_increasing is True
    assert handle_map.clean_terminator is True
    assert handle_map.entry_count >= 100
    # The map's offsets index the decompressed object buffer.
    assert 0 < handle_map.in_bounds_count <= handle_map.entry_count


@pytest.mark.parametrize("sample", REAL_AC1032_SAMPLES, ids=lambda p: p.name)
def test_real_ac1032_object_table_frames_majority(sample: Path) -> None:
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    table = read_r2018_object_table(sample.read_bytes())

    assert table.status == "decoded", table.message
    in_bounds = table.framed_count + table.unframed_count
    assert in_bounds > 100
    # The corrected multi-page assembly frames the majority of handle-mapped
    # objects (the remainder are non-framable handle entries: stale / deleted /
    # other-section). Before the assembly fix this was ~55%.
    coverage = table.framed_count / in_bounds
    assert coverage >= 0.70, f"only {coverage:.1%} of in-bounds handle entries framed"
    # Real geometry is among them (LINE == 19).
    assert 19 in table.type_counts, table.type_counts


def test_real_ac1032_object_table_high_coverage_on_primary_sample() -> None:
    # Interior-placement regression guard for the multi-page assembly fix: on the
    # primary acadsharp sample the corrected slot-sized decompression frames a
    # high fraction of handle-mapped objects. A regression to the old per-page
    # field decompression (zero gaps) drops this back toward ~55%.
    sample = REAL_AC1032_SAMPLES[0]
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    table = read_r2018_object_table(sample.read_bytes())
    in_bounds = table.framed_count + table.unframed_count
    coverage = table.framed_count / in_bounds
    assert coverage >= 0.85, f"only {coverage:.1%} framed (assembly regression?)"


# ---- Spike S3 (part 2): basic entity geometry decode ----

# Ground truth for the primary acadsharp sample: the exact geometry an
# ODA-converted DXF reports for these handles (recorded once, as constants — no
# ODA dependency at test time). The native clean-room decoder must reproduce it.
_PRIMARY_SAMPLE = REAL_AC1032_SAMPLES[0]
_GT_LINES = {
    0x2C7: ((3.592533998909389, 1.477241896180196, 0.0),
            (6.863547033979557, 1.477241896180196, 0.0)),
    0x517: ((330.2890594765796, 2.941179455067987, 0.0),
            (364.4872644138525, 37.13938439234094, 0.0)),
    # true colour (0x8000) + non-zero Z flag — exercises the complex-colour and
    # Z-coordinate branches.
    0x99E: ((18.96130506894906, -124.7749383365533, 0.0),
            (23.96130506894906, -119.7749383365533, 0.0)),
}
_GT_CIRCLES = {
    0x51D: ((569.6764940374901, 25.73998274658328), 11.39940164575765),
    # AcDbColor reference colour (0x4000) — handle in the handle stream, no RGB
    # bytes in the data stream.
    0x99F: ((30.20357222512345, -119.9668664522385), 2.382841759818497),
}
_GT_ARC = {
    0x320: ((56.35179242595231, 4.697601732518876), 3.044494390598889,
            341.0435453511963, 161.0435453511958),
}
_GT_POINT = {
    0x28E: (1.494404150136852, 1.491325898678436, 0.0),
}


def _approx(actual, expected, tol=1e-6):
    return abs(actual - expected) <= tol


def test_real_ac1032_decodes_entity_geometry_matches_ground_truth() -> None:
    if not _PRIMARY_SAMPLE.exists():
        pytest.skip(f"local AC1032 sample not present: {_PRIMARY_SAMPLE}")

    table = read_r2018_entities(_PRIMARY_SAMPLE.read_bytes())

    assert table.status == "decoded", table.message
    # A substantial number of entities decode across all four supported types.
    assert table.decoded_count >= 50, table.type_counts
    for kind in ("LINE", "CIRCLE", "ARC", "POINT"):
        assert table.type_counts.get(kind, 0) > 0, table.type_counts

    by_handle = {e.handle: e for e in table.entities}

    for handle, (start, end) in _GT_LINES.items():
        entity = by_handle[handle]
        assert entity.type_name == "LINE"
        assert all(_approx(a, b) for a, b in zip(entity.geometry["start"], start)), entity.geometry
        assert all(_approx(a, b) for a, b in zip(entity.geometry["end"], end)), entity.geometry

    for handle, (center, radius) in _GT_CIRCLES.items():
        entity = by_handle[handle]
        assert entity.type_name == "CIRCLE"
        assert _approx(entity.geometry["center"][0], center[0])
        assert _approx(entity.geometry["center"][1], center[1])
        assert _approx(entity.geometry["radius"], radius)

    for handle, (center, radius, start_deg, end_deg) in _GT_ARC.items():
        entity = by_handle[handle]
        assert entity.type_name == "ARC"
        assert _approx(entity.geometry["center"][0], center[0])
        assert _approx(entity.geometry["center"][1], center[1])
        assert _approx(entity.geometry["radius"], radius)
        assert _approx(entity.geometry["start_angle_deg"], start_deg, tol=1e-4)
        assert _approx(entity.geometry["end_angle_deg"], end_deg, tol=1e-4)

    for handle, location in _GT_POINT.items():
        entity = by_handle[handle]
        assert entity.type_name == "POINT"
        assert _approx(entity.geometry["location"][0], location[0])
        assert _approx(entity.geometry["location"][1], location[1])


def test_real_ac1032_decoded_entity_handle_matches_handle_map() -> None:
    # Each decoded object's own handle (from the common-entity-data H field) must
    # equal the handle the AcDb:Handles index recorded for that offset.
    if not _PRIMARY_SAMPLE.exists():
        pytest.skip(f"local AC1032 sample not present: {_PRIMARY_SAMPLE}")

    raw = _PRIMARY_SAMPLE.read_bytes()
    handle_map = read_r2018_handle_map(raw)
    objects = read_r2004_data_section(raw, section_name="AcDb:AcDbObjects")

    checked = 0
    for handle, offset in handle_map.entries:
        if not 0 <= offset < len(objects):
            continue
        entity = decode_r2018_entity(objects, offset)
        if entity is None:
            continue
        assert entity.handle == handle, f"{entity.handle:#x} != {handle:#x} at {offset}"
        checked += 1
    assert checked >= 50


def test_decode_r2018_entity_returns_none_on_unframable_offset() -> None:
    # Fail-closed: a buffer of zeros frames no object, so geometry decode returns
    # None rather than raising or inventing geometry.
    assert decode_r2018_entity(b"\x00" * 256, 4) is None


# ---- Spike S4: decoded geometry -> canonical -> native scene pack -> viewport ----


def test_r2018_entity_to_canonical_maps_geometry_to_viewer_points() -> None:
    line = r2018_entity_to_canonical(
        R2018Entity(0x2C7, 0x13, "LINE", {"start": (1.0, 2.0, 0.0), "end": (3.0, 4.0, 0.0)})
    )
    assert line["type"] == "line" and line["handle"] == "2C7"
    assert line["geometry"]["start"] == {"x": 1.0, "y": 2.0, "z": 0.0}
    assert line["geometry"]["end"] == {"x": 3.0, "y": 4.0, "z": 0.0}

    circle = r2018_entity_to_canonical(
        R2018Entity(0x10, 0x12, "CIRCLE", {"center": (5.0, 6.0, 0.0), "radius": 2.5})
    )
    assert circle["type"] == "circle"
    assert circle["geometry"]["center"] == {"x": 5.0, "y": 6.0, "z": 0.0}
    assert circle["geometry"]["radius"] == 2.5

    arc = r2018_entity_to_canonical(
        R2018Entity(0x11, 0x11, "ARC",
                    {"center": (0.0, 0.0, 0.0), "radius": 5.0,
                     "start_angle_deg": 0.0, "end_angle_deg": 90.0})
    )
    assert arc["type"] == "arc"
    assert arc["geometry"]["start_angle_deg"] == 0.0 and arc["geometry"]["end_angle_deg"] == 90.0

    point = r2018_entity_to_canonical(
        R2018Entity(0x12, 0x1B, "POINT", {"location": (7.0, 8.0, 0.0)})
    )
    assert point["type"] == "point"  # producer counts this as unsupported (visible)
    assert point["geometry"]["location"] == {"x": 7.0, "y": 8.0, "z": 0.0}


def test_real_ac1032_canonical_document_renders_through_viewport_seam(tmp_path: Path) -> None:
    # S4: the own clean-room reader drives the SAME viewport seam the ezdxf path
    # uses — decoded geometry -> canonical doc -> native scene pack -> primitives
    # + bbox — with ZERO ODA/ezdxf calls.
    sample = REAL_AC1032_SAMPLES[0]
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    raw = sample.read_bytes()
    table = read_r2018_entities(raw)
    expected_primitives = sum(table.type_counts.get(k, 0) for k in ("LINE", "CIRCLE", "ARC"))

    document = build_r2018_canonical_document(raw, source_path=sample.name)
    assert document["schema_version"] == "canonical-drawing/v1"
    assert len(document["entities"]) == table.decoded_count
    assert "extents" in document

    pack = build_native_scene_pack(document)
    # LINE/CIRCLE/ARC flatten to viewport line primitives; POINT is counted
    # unsupported, never silently dropped.
    assert pack.metadata["primitive_count"] == expected_primitives
    assert pack.metadata["unsupported_entity_type_counts"].get("point") == table.type_counts.get("POINT")
    # The scene bbox spans the decoded geometry.
    min_x, min_y, max_x, max_y = pack.bbox
    assert max_x > min_x and max_y > min_y

    ref = build_native_scene_pack_ref(document, tmp_path)
    assert Path(ref.overview_lod0_path).exists()
    assert ref.primitive_count == expected_primitives

    source = resolve_viewer_primitive_source(ref)
    assert source.ok is True
    assert source.degraded is False
    assert source.render_mode == "skeleton_preview"
    assert source.provenance["producer_id"] == "native_scene_pack"
    assert len(source.primitives) == expected_primitives
    assert source.world_bbox == pack.bbox


# ---- Spike S5: own-reader canonical -> existing diff + revision clouds ----


def test_real_ac1032_canonical_pair_diffs_and_clouds_the_change() -> None:
    # S5 / GO criterion: a before/after pair built from the own clean-room reader
    # flows through the EXISTING canonical diff + revision-cloud engine, with ZERO
    # ODA/ezdxf calls, and a revision cloud lands on the change.
    sample = REAL_AC1032_SAMPLES[0]
    if not sample.exists():
        pytest.skip(f"local AC1032 sample not present: {sample}")

    before = build_r2018_canonical_document(sample.read_bytes(), source_path="before.dwg")
    after = copy.deepcopy(before)
    after["drawing"]["source"]["path"] = "after.dwg"

    # Synthetic edit: move the first LINE's end point and update its bbox.
    moved = next(e for e in after["entities"] if e["type"] == "line")
    moved["geometry"]["end"]["x"] += 40.0
    moved["geometry"]["end"]["y"] += 30.0
    sx, ex = moved["geometry"]["start"]["x"], moved["geometry"]["end"]["x"]
    sy, ey = moved["geometry"]["start"]["y"], moved["geometry"]["end"]["y"]
    moved["bbox"] = {"min_x": min(sx, ex), "min_y": min(sy, ey),
                     "max_x": max(sx, ex), "max_y": max(sy, ey)}
    moved_centroid = ((min(sx, ex) + max(sx, ex)) / 2.0, (min(sy, ey) + max(sy, ey)) / 2.0)

    # Existing canonical diff engine on own-reader canonical.
    diff = DrawingCompareEngine(DrawingCompareOptions(include_unchanged=False)).compare(before, after)
    # The matcher pairs the unchanged majority (proves own canonical diffs cleanly)
    # and isolates the single edit.
    assert diff.summary_counts["unchanged"] >= len(before["entities"]) - 5
    edited = diff.summary_counts["added"] + diff.summary_counts["removed"] + diff.summary_counts["modified"]
    assert 1 <= edited <= 4, diff.summary_counts

    # Diff -> change records -> change zones (existing engine).
    result = ComparisonResult(source_a="before.dwg", source_b="after.dwg")
    for record in diff.to_change_records():
        result.add_change(record)
    zones = build_change_zones(result, pair_id="P1", drawing_number="P1",
                               options=ChangeZoneOptions(cluster_distance=120.0))
    assert zones, "the edit must produce at least one change zone"

    # The zone that covers the edit anchors a revision cloud.
    covering = [
        z for z in zones
        if z.bbox[0] <= moved_centroid[0] <= z.bbox[2]
        and z.bbox[1] <= moved_centroid[1] <= z.bbox[3]
    ]
    assert covering, f"no change zone covers the moved line at {moved_centroid}"

    cloud = revcloud_geometry_from_bbox(covering[0].bbox)
    assert len(cloud.vertices) >= 4
    assert len(cloud.vertices) == len(cloud.bulges)
    xs = [v[0] for v in cloud.vertices]
    ys = [v[1] for v in cloud.vertices]
    assert min(xs) <= moved_centroid[0] <= max(xs)
    assert min(ys) <= moved_centroid[1] <= max(ys)
