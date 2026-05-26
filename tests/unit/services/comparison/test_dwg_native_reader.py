from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Iterable

from jsonschema import Draft202012Validator

from src.services.comparison.dwg_binary_reader import DwgBinaryReader
from src.services.comparison.dwg_importer import DwgImporter, DwgJsonFixtureAdapter
from src.services.comparison.dwg_native_reader import DwgNativeAc1015Adapter
from src.services.comparison.dwg_object_decoder import DwgMvpObjectType, DwgObjectDecoder
from src.services.comparison.dwg_section_reader import DwgSectionReader
from src.services.comparison.import_pipeline import CadPipelineStatus, ImportPipeline


def _schema() -> dict:
    return json.loads(Path("docs/canonical-drawing.schema.json").read_text(encoding="utf-8"))


def _validate_schema(doc: dict) -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=str)
    assert not errors, "\n".join(f"{list(error.path)}: {error.message}" for error in errors[:10])


def _bits_to_bytes(bits: str) -> bytes:
    padded = bits + ("0" * ((8 - len(bits) % 8) % 8))
    return bytes(int(padded[index:index + 8], 2) for index in range(0, len(padded), 8))


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


def _string(value: str) -> bytes:
    payload = value.encode("utf-8")
    return struct.pack("<H", len(payload)) + payload


def _point(x: float, y: float, z: float = 0.0) -> bytes:
    return struct.pack("<ddd", x, y, z)


def _object_prefix(
    kind: int,
    handle: int,
    *,
    owner: int = 0,
    layer: int = 0,
    color: int = -1,
    lineweight: int = -1,
    name: str = "",
    linetype: str = "",
) -> bytes:
    return (
        DwgObjectDecoder.MVP_OBJECT_MAGIC
        + bytes([DwgObjectDecoder.MVP_OBJECT_VERSION, kind])
        + struct.pack("<IIIhh", handle, owner, layer, color, lineweight)
        + _string(name)
        + _string(linetype)
    )


def _layer(handle: int, name: str, *, color: int) -> tuple[int, bytes]:
    return (
        handle,
        _object_prefix(
            DwgMvpObjectType.LAYER,
            handle,
            color=color,
            lineweight=25,
            name=name,
            linetype="Continuous",
        ),
    )


def _block(handle: int, name: str) -> tuple[int, bytes]:
    return (
        handle,
        _object_prefix(DwgMvpObjectType.BLOCK, handle, name=name) + _point(0, 0, 0),
    )


def _line(handle: int, layer: int, start: tuple[float, float, float], end: tuple[float, float, float], *, owner: int = 0) -> tuple[int, bytes]:
    return (
        handle,
        _object_prefix(DwgMvpObjectType.LINE, handle, owner=owner, layer=layer)
        + _point(*start)
        + _point(*end),
    )


def _circle(handle: int, layer: int) -> tuple[int, bytes]:
    return (
        handle,
        _object_prefix(DwgMvpObjectType.CIRCLE, handle, layer=layer)
        + _point(50, 50, 0)
        + struct.pack("<d", 10.0),
    )


def _text(handle: int, layer: int) -> tuple[int, bytes]:
    return (
        handle,
        _object_prefix(DwgMvpObjectType.TEXT, handle, layer=layer)
        + _point(0, 20, 0)
        + struct.pack("<dd", 2.5, 0.0)
        + _string(" H-400 "),
    )


def _insert(handle: int, layer: int, block: int) -> tuple[int, bytes]:
    return (
        handle,
        _object_prefix(DwgMvpObjectType.INSERT, handle, layer=layer)
        + struct.pack("<I", block)
        + _point(10, 10, 0)
        + _point(1, 1, 1)
        + struct.pack("<d", 0.0),
    )


def _polyline(handle: int, layer: int) -> tuple[int, bytes]:
    return (
        handle,
        _object_prefix(DwgMvpObjectType.LWPOLYLINE, handle, layer=layer)
        + struct.pack("<HH", 1, 4)
        + _point(0, 0, 0)
        + _point(10, 0, 0)
        + _point(10, 10, 0)
        + _point(0, 10, 0),
    )


def _object_map(entries: Iterable[tuple[int, int]]) -> bytes:
    body = bytearray()
    last_handle = 0
    last_offset = 0
    for handle, offset in sorted(entries):
        body += _mchar(handle - last_handle)
        body += _mchar(offset - last_offset, signed=True)
        last_handle = handle
        last_offset = offset
    return struct.pack(">H", len(body) + 2) + body + b"\x00\x00" + b"\x00\x02\x00\x00"


def _native_ac1015_fixture() -> bytes:
    records = [
        _layer(0x10, "BEAM", color=3),
        _layer(0x11, "ANNO", color=7),
        _block(0x20, "B1"),
        _line(0x21, 0x10, (0, 0, 0), (10, 0, 0), owner=0x20),
        _line(0x30, 0x10, (0, 0, 0), (100, 0, 0)),
        _circle(0x31, 0x10),
        _text(0x32, 0x11),
        _insert(0x33, 0x10, 0x20),
        _polyline(0x34, 0x10),
    ]
    locator_count = 3
    header_size = 0x15 + 4 + locator_count * 9
    data = bytearray(b"\x00" * header_size)
    data[:6] = b"AC1015"
    struct.pack_into("<H", data, 0x13, 30)
    struct.pack_into("<I", data, 0x15, locator_count)

    object_offsets: list[tuple[int, int]] = []
    for handle, payload in records:
        object_offsets.append((handle, len(data)))
        data += payload

    object_map_offset = len(data)
    object_map = _object_map(object_offsets)
    data += object_map

    locators = [
        (0, header_size, 0),
        (1, header_size, 0),
        (2, object_map_offset, len(object_map)),
    ]
    cursor = 0x15 + 4
    for record_number, seeker, size in locators:
        struct.pack_into("<BII", data, cursor, record_number, seeker, size)
        cursor += 9
    return bytes(data)


def _adapter_payload() -> dict:
    return {
        "header": {"$INSUNITS": 4},
        "layers": [
            {"name": "BEAM", "color": 3, "linetype": "Continuous", "lineweight": 25},
            {"name": "ANNO", "color": 7, "linetype": "Continuous", "lineweight": 25},
        ],
        "blocks": [
            {
                "name": "B1",
                "origin": {"x": 0, "y": 0, "z": 0},
                "entities": [
                    {
                        "type": "LINE",
                        "handle": "21",
                        "layer": "BEAM",
                        "geometry": {
                            "start": {"x": 0, "y": 0, "z": 0},
                            "end": {"x": 10, "y": 0, "z": 0},
                        },
                    }
                ],
            }
        ],
        "model_space": [
            {
                "type": "LINE",
                "handle": "30",
                "layer": "BEAM",
                "geometry": {
                    "start": {"x": 0, "y": 0, "z": 0},
                    "end": {"x": 100, "y": 0, "z": 0},
                },
            },
            {
                "type": "CIRCLE",
                "handle": "31",
                "layer": "BEAM",
                "geometry": {"center": {"x": 50, "y": 50, "z": 0}, "radius": 10},
            },
            {
                "type": "TEXT",
                "handle": "32",
                "layer": "ANNO",
                "geometry": {
                    "insert": {"x": 0, "y": 20, "z": 0},
                    "height": 2.5,
                    "rotation_deg": 0,
                    "text": " H-400 ",
                },
            },
            {
                "type": "INSERT",
                "handle": "33",
                "layer": "BEAM",
                "geometry": {
                    "block_name": "B1",
                    "insert": {"x": 10, "y": 10, "z": 0},
                    "scale": {"x": 1, "y": 1, "z": 1},
                    "rotation_deg": 0,
                },
            },
            {
                "type": "LWPOLYLINE",
                "handle": "34",
                "layer": "BEAM",
                "geometry": {
                    "closed": True,
                    "vertices": [
                        {"point": {"x": 0, "y": 0, "z": 0}},
                        {"point": {"x": 10, "y": 0, "z": 0}},
                        {"point": {"x": 10, "y": 10, "z": 0}},
                        {"point": {"x": 0, "y": 10, "z": 0}},
                    ],
                },
            },
        ],
    }


def test_dwg_binary_reader_reads_public_primitive_encodings() -> None:
    bits = "01" + "00001111" + "10" + "11"
    reader = DwgBinaryReader(_bits_to_bytes(bits))
    assert reader.read_bit_short() == 15
    assert reader.read_bit_short() == 0
    assert reader.read_bit_short() == 256

    assert DwgBinaryReader(bytes([0x82, 0x24])).read_modular_char() == 4610
    assert DwgBinaryReader(bytes([0x85, 0x4B])).read_modular_char(signed=True) == -1413

    handle = DwgBinaryReader(bytes([0x52, 0x05, 0xE7])).read_handle()
    assert handle.code == 5
    assert handle.counter == 2
    assert handle.hex_value == "5E7"


def test_ac1015_section_reader_reads_header_and_object_map() -> None:
    data = _native_ac1015_fixture()
    reader = DwgSectionReader(data)

    header = reader.read_header()
    object_map = reader.read_object_map(header)

    assert header.version_code == "AC1015"
    assert header.locator_count == 3
    assert header.locator(2) is not None
    assert [entry.handle_hex for entry in object_map[:3]] == ["10", "11", "20"]
    assert len(object_map) == 9


def test_native_ac1015_adapter_imports_simple_2d_dwg_without_fixture_adapter(tmp_path: Path) -> None:
    path = tmp_path / "native-ac1015.dwg"
    path.write_bytes(_native_ac1015_fixture())

    doc = DwgImporter(adapter=DwgNativeAc1015Adapter()).import_file(path)

    _validate_schema(doc)
    assert doc["drawing"]["source"]["acad_version"] == "AC1015"
    assert doc["drawing"]["importer"]["backend"] == "native-ac1015"
    assert doc["import_report"]["status"] == "ok"
    assert doc["import_report"]["adapter"]["license_id"] == "INTERNAL"
    assert {layer["name"] for layer in doc["layers"]} >= {"0", "BEAM", "ANNO"}
    assert [block["name"] for block in doc["blocks"]] == ["B1"]
    assert {entity["type"] for entity in doc["entities"]} >= {
        "line",
        "circle",
        "text",
        "block_reference",
        "polyline",
    }


def test_native_ac1015_adapter_matches_json_adapter_for_same_payload(tmp_path: Path) -> None:
    native_path = tmp_path / "native.dwg"
    native_path.write_bytes(_native_ac1015_fixture())
    json_path = tmp_path / "adapter.dwg"
    json_path.write_bytes(
        b"AC1015"
        + DwgJsonFixtureAdapter.MARKER
        + json.dumps(_adapter_payload(), ensure_ascii=False).encode("utf-8")
    )

    native_doc = DwgImporter(adapter=DwgNativeAc1015Adapter()).import_file(native_path)
    adapter_doc = DwgImporter(adapter=DwgJsonFixtureAdapter()).import_file(json_path)

    _validate_schema(native_doc)
    _validate_schema(adapter_doc)
    assert _entity_signature(native_doc) == _entity_signature(adapter_doc)
    assert [block["name"] for block in native_doc["blocks"]] == [block["name"] for block in adapter_doc["blocks"]]


def test_import_pipeline_uses_native_ac1015_reader_by_default(tmp_path: Path) -> None:
    path = tmp_path / "pipeline-native.dwg"
    path.write_bytes(_native_ac1015_fixture())

    result = ImportPipeline().import_file(path)

    assert result.status == CadPipelineStatus.OK
    assert result.canonical_drawing is not None
    assert result.canonical_drawing["drawing"]["importer"]["backend"] == "native-ac1015"
    assert result.to_dict()["entity_count"] == 6


def _entity_signature(doc: dict) -> list[tuple[str, str, str]]:
    return [
        (
            entity["type"],
            entity["layer_id"],
            entity["hashes"]["geometry_hash"],
        )
        for entity in doc["entities"]
    ]
