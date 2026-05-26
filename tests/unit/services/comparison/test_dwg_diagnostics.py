from __future__ import annotations

import json
import struct
from pathlib import Path

from src.services.comparison.dwg_diagnostics import diagnose_dwg_file
from src.services.comparison.dwg_importer import DwgFailureCode, DwgJsonFixtureAdapter
from src.services.comparison.dwg_object_decoder import DwgObjectDecoder, DwgMvpObjectType


def _string(value: str) -> bytes:
    payload = value.encode("utf-8")
    return struct.pack("<H", len(payload)) + payload


def _mchar(value: int, *, signed: bool = False) -> bytes:
    negative = signed and value < 0
    value = abs(value)
    chunks = []
    while True:
        chunks.append(value & 0x7F)
        value >>= 7
        if not value:
            break
    if signed and not negative and (chunks[-1] & 0x40):
        chunks.append(0)
    if negative:
        chunks[-1] |= 0x40
    for index in range(len(chunks) - 1):
        chunks[index] |= 0x80
    return bytes(chunks)


def _object_prefix(kind: int, handle: int, *, layer: int = 0, name: str = "") -> bytes:
    return (
        DwgObjectDecoder.MVP_OBJECT_MAGIC
        + bytes([DwgObjectDecoder.MVP_OBJECT_VERSION, kind])
        + struct.pack("<IIIhh", handle, 0, layer, -1, -1)
        + _string(name)
        + _string("Continuous")
    )


def _object_map(entries: list[tuple[int, int]]) -> bytes:
    body = bytearray()
    last_handle = 0
    last_offset = 0
    for handle, offset in sorted(entries):
        body += _mchar(handle - last_handle)
        body += _mchar(offset - last_offset, signed=True)
        last_handle = handle
        last_offset = offset
    return struct.pack(">H", len(body) + 2) + body + b"\x00\x00" + b"\x00\x02\x00\x00"


def _minimal_ac1015_fixture() -> bytes:
    records = [
        (0x10, _object_prefix(DwgMvpObjectType.LAYER, 0x10, name="0")),
        (
            0x20,
            _object_prefix(DwgMvpObjectType.LINE, 0x20, layer=0x10)
            + struct.pack("<dddddd", 0, 0, 0, 10, 0, 0),
        ),
    ]
    locator_count = 3
    header_size = 0x15 + 4 + locator_count * 9
    data = bytearray(b"\x00" * header_size)
    data[:6] = b"AC1015"
    struct.pack_into("<H", data, 0x13, 30)
    struct.pack_into("<I", data, 0x15, locator_count)
    offsets = []
    for handle, payload in records:
        offsets.append((handle, len(data)))
        data += payload
    object_map_offset = len(data)
    object_map = _object_map(offsets)
    data += object_map
    cursor = 0x15 + 4
    for record_number, seeker, size in [
        (0, header_size, 0),
        (1, header_size, 0),
        (2, object_map_offset, len(object_map)),
    ]:
        struct.pack_into("<BII", data, cursor, record_number, seeker, size)
        cursor += 9
    return bytes(data)


def test_diagnose_missing_file_reports_blocking_file(tmp_path: Path) -> None:
    diagnostic = diagnose_dwg_file(tmp_path / "missing.dwg").to_dict()

    assert diagnostic["status"] == "missing"
    assert diagnostic["blocking_stage"] == "file"


def test_diagnose_unregistered_planned_version_reports_section_locator_block(tmp_path: Path) -> None:
    path = tmp_path / "planned.dwg"
    path.write_bytes(
        b"AC1027"
        + DwgJsonFixtureAdapter.MARKER
        + json.dumps({"model_space": []}).encode("utf-8")
    )

    diagnostic = diagnose_dwg_file(path).to_dict()

    assert diagnostic["status"] == "unsupported_version"
    assert diagnostic["error_code"] == DwgFailureCode.UNSUPPORTED_VERSION
    assert diagnostic["version"]["code"] == "AC1027"
    assert diagnostic["blocking_stage"] == "section_locator"
    assert diagnostic["stages"][-1]["status"] == "blocked"


def test_diagnose_versioned_shell_advances_to_section_map_decoder_block(tmp_path: Path) -> None:
    diagnostics = []
    for code in ("AC1024", "AC1032"):
        path = tmp_path / f"{code}.dwg"
        path.write_bytes(
            code.encode("ascii")
            + DwgJsonFixtureAdapter.MARKER
            + json.dumps({"model_space": []}).encode("utf-8")
        )
        diagnostics.append(diagnose_dwg_file(path).to_dict())

    assert {item["status"] for item in diagnostics} == {"unsupported_version"}
    assert {item["error_code"] for item in diagnostics} == {DwgFailureCode.UNSUPPORTED_VERSION}
    assert {item["version"]["code"] for item in diagnostics} == {"AC1024", "AC1032"}
    assert {item["blocking_stage"] for item in diagnostics} == {"section_map_decoder"}
    for diagnostic in diagnostics:
        stages = {stage["name"]: stage for stage in diagnostic["stages"]}
        assert stages["section_locator"]["status"] == "ok"
        assert stages["section_map_decoder"]["status"] == "blocked"
        assert stages["section_map_decoder"]["metrics"]["implemented"] is False
        assert (
            stages["section_map_decoder"]["metrics"]["contract_id"]
            == "DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1"
        )
        assert stages["section_map_decoder"]["metrics"]["approval_status"] == "blocked"
        assert (
            stages["section_map_decoder"]["metrics"]["blocking_stage_detail"]
            == "approved_format_contract_required"
        )
        assert stages["section_map_decoder"]["metrics"]["approved_reference_available"] is False
        assert (
            stages["section_map_decoder"]["metrics"]["decoder_provenance"]
            == "internal/public-approved-only"
        )
        assert "section page map" in stages["section_map_decoder"]["metrics"]["required_decoders"]
        assert (
            "approved public-reference citation with license/provenance notes"
            in stages["section_map_decoder"]["metrics"]["required_approval_evidence"]
        )


def test_diagnose_ac1015_fixture_reads_object_map(tmp_path: Path) -> None:
    path = tmp_path / "native.dwg"
    path.write_bytes(_minimal_ac1015_fixture())

    diagnostic = diagnose_dwg_file(path).to_dict()

    assert diagnostic["status"] == "ok"
    assert diagnostic["version"]["code"] == "AC1015"
    assert diagnostic["blocking_stage"] is None
    object_stage = next(stage for stage in diagnostic["stages"] if stage["name"] == "object_map")
    assert object_stage["metrics"]["entry_count"] == 2
