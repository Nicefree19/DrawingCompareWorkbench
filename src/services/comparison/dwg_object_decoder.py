"""AC1015 read-only MVP object decoder.

The first production goal is a safe native-reader boundary.  The decoder below
supports a constrained AC1015 object payload used by our validation fixtures
while the public DWG primitive readers and object-map traversal are hardened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .dwg_binary_reader import DwgBinaryReadError, DwgBinaryReader
from .dwg_importer import DwgAdapterBlock, DwgAdapterDrawing, DwgAdapterEntity
from .dwg_section_reader import DwgFileHeader, DwgObjectMapEntry
from .dxf_importer import _make_point


class DwgObjectDecodeError(DwgBinaryReadError):
    """Raised when an AC1015 object payload cannot be decoded."""


class DwgMvpObjectType:
    LAYER = 1
    BLOCK = 2
    LINE = 3
    CIRCLE = 4
    ARC = 5
    TEXT = 6
    INSERT = 7
    LWPOLYLINE = 8


@dataclass
class DwgDecodedObject:
    object_type: int
    handle: int
    owner_handle: int = 0
    layer_handle: int = 0
    name: str = ""
    color: Optional[int] = None
    linetype: Optional[str] = None
    lineweight: Optional[int] = None
    geometry: Dict[str, Any] = field(default_factory=dict)

    @property
    def handle_hex(self) -> str:
        return f"{self.handle:X}" if self.handle else None


class DwgObjectDecoder:
    """Decode AC1015 MVP objects into the existing adapter drawing contract."""

    MVP_OBJECT_MAGIC = b"CWBAC15O"
    MVP_OBJECT_VERSION = 1

    def __init__(
        self,
        data: bytes | bytearray | memoryview,
        header: DwgFileHeader,
        object_map: List[DwgObjectMapEntry],
    ):
        self.data = bytes(data)
        self.header = header
        self.object_map = list(object_map)

    def decode(self) -> DwgAdapterDrawing:
        objects = [self.decode_object(entry) for entry in self.object_map]
        layers_by_handle = {
            item.handle: item
            for item in objects
            if item.object_type == DwgMvpObjectType.LAYER
        }
        blocks_by_handle = {
            item.handle: item
            for item in objects
            if item.object_type == DwgMvpObjectType.BLOCK
        }

        layers = [
            {
                "name": layer.name or f"LAYER_{layer.handle_hex}",
                "color": layer.color,
                "linetype": layer.linetype,
                "lineweight": layer.lineweight,
                "source_handle": layer.handle_hex,
            }
            for layer in sorted(layers_by_handle.values(), key=lambda item: item.handle)
        ]

        block_entities: Dict[int, List[DwgAdapterEntity]] = {handle: [] for handle in blocks_by_handle}
        model_space: List[DwgAdapterEntity] = []
        for item in objects:
            if item.object_type in {DwgMvpObjectType.LAYER, DwgMvpObjectType.BLOCK}:
                continue
            entity = self._to_adapter_entity(item, layers_by_handle, blocks_by_handle)
            if item.owner_handle in block_entities:
                block_entities[item.owner_handle].append(entity)
            else:
                model_space.append(entity)

        blocks = [
            DwgAdapterBlock(
                name=block.name or f"BLOCK_{block.handle_hex}",
                origin=_point3(block.geometry.get("origin")),
                entities=block_entities.get(block.handle, []),
            )
            for block in sorted(blocks_by_handle.values(), key=lambda item: item.handle)
        ]

        return DwgAdapterDrawing(
            header={"$ACADVER": self.header.version_code, "$INSUNITS": 4},
            layers=layers,
            blocks=blocks,
            model_space=model_space,
            metadata={
                "native_reader": "DwgObjectDecoder",
                "native_reader_mvp": True,
                "object_count": len(objects),
            },
        )

    def decode_object(self, entry: DwgObjectMapEntry) -> DwgDecodedObject:
        reader = DwgBinaryReader(self.data)
        reader.seek(entry.offset)
        magic = reader.read_bytes(len(self.MVP_OBJECT_MAGIC))
        if magic != self.MVP_OBJECT_MAGIC:
            raise DwgObjectDecodeError(
                f"unsupported AC1015 object payload at {entry.offset}: {magic!r}"
            )
        payload_version = reader.read_u8()
        if payload_version != self.MVP_OBJECT_VERSION:
            raise DwgObjectDecodeError(f"unsupported MVP object payload version {payload_version}")

        object_type = reader.read_u8()
        handle = reader.read_u32_le()
        owner_handle = reader.read_u32_le()
        layer_handle = reader.read_u32_le()
        color = reader.read_i16_le()
        lineweight = reader.read_i16_le()
        name = reader.read_string_u16()
        linetype = reader.read_string_u16()

        decoded = DwgDecodedObject(
            object_type=object_type,
            handle=handle,
            owner_handle=owner_handle,
            layer_handle=layer_handle,
            name=name,
            color=color if color >= 0 else None,
            linetype=linetype or None,
            lineweight=lineweight if lineweight >= 0 else None,
        )
        decoded.geometry = self._read_geometry(reader, decoded)
        return decoded

    def _read_geometry(self, reader: DwgBinaryReader, decoded: DwgDecodedObject) -> Dict[str, Any]:
        object_type = decoded.object_type
        if object_type == DwgMvpObjectType.LAYER:
            return {}
        if object_type == DwgMvpObjectType.BLOCK:
            return {"origin": _read_point3(reader)}
        if object_type == DwgMvpObjectType.LINE:
            return {"start": _read_point3(reader), "end": _read_point3(reader)}
        if object_type == DwgMvpObjectType.CIRCLE:
            return {"center": _read_point3(reader), "radius": reader.read_f64_le()}
        if object_type == DwgMvpObjectType.ARC:
            return {
                "center": _read_point3(reader),
                "radius": reader.read_f64_le(),
                "start_angle_deg": reader.read_f64_le(),
                "end_angle_deg": reader.read_f64_le(),
            }
        if object_type == DwgMvpObjectType.TEXT:
            return {
                "insert": _read_point3(reader),
                "height": reader.read_f64_le(),
                "rotation_deg": reader.read_f64_le(),
                "text": reader.read_string_u16(),
            }
        if object_type == DwgMvpObjectType.INSERT:
            return {
                "block_handle": reader.read_u32_le(),
                "insert": _read_point3(reader),
                "scale": _read_point3(reader),
                "rotation_deg": reader.read_f64_le(),
            }
        if object_type == DwgMvpObjectType.LWPOLYLINE:
            flags = reader.read_u16_le()
            count = reader.read_u16_le()
            return {
                "closed": bool(flags & 1),
                "vertices": [{"point": _read_point3(reader)} for _ in range(count)],
            }
        raise DwgObjectDecodeError(f"unsupported AC1015 MVP object type {object_type}")

    def _to_adapter_entity(
        self,
        decoded: DwgDecodedObject,
        layers_by_handle: Dict[int, DwgDecodedObject],
        blocks_by_handle: Dict[int, DwgDecodedObject],
    ) -> DwgAdapterEntity:
        raw_type = {
            DwgMvpObjectType.LINE: "LINE",
            DwgMvpObjectType.CIRCLE: "CIRCLE",
            DwgMvpObjectType.ARC: "ARC",
            DwgMvpObjectType.TEXT: "TEXT",
            DwgMvpObjectType.INSERT: "INSERT",
            DwgMvpObjectType.LWPOLYLINE: "LWPOLYLINE",
        }.get(decoded.object_type)
        if raw_type is None:
            raise DwgObjectDecodeError(f"object type {decoded.object_type} is not an entity")

        layer = layers_by_handle.get(decoded.layer_handle)
        geometry = dict(decoded.geometry)
        if decoded.object_type == DwgMvpObjectType.INSERT:
            block = blocks_by_handle.get(int(geometry.get("block_handle") or 0))
            geometry["block_name"] = block.name if block else str(geometry.get("block_handle") or "")

        return DwgAdapterEntity(
            raw_type=raw_type,
            geometry=geometry,
            layer=(layer.name if layer else "0"),
            handle=decoded.handle_hex,
            owner_handle=f"{decoded.owner_handle:X}" if decoded.owner_handle else None,
            style={
                "color": decoded.color,
                "linetype": decoded.linetype,
                "lineweight": decoded.lineweight,
            },
        )


def _read_point3(reader: DwgBinaryReader) -> Dict[str, float]:
    return _make_point(reader.read_f64_le(), reader.read_f64_le(), reader.read_f64_le())


def _point3(value: Any) -> Dict[str, float]:
    if isinstance(value, dict):
        return _make_point(
            float(value.get("x", 0.0) or 0.0),
            float(value.get("y", 0.0) or 0.0),
            float(value.get("z", 0.0) or 0.0),
        )
    return _make_point(0.0, 0.0, 0.0)
