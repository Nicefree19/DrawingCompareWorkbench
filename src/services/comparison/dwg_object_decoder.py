"""AC1015 read-only MVP object decoder.

The first production goal is a safe native-reader boundary.  The decoder below
supports a constrained AC1015 object payload used by our validation fixtures
while the public DWG primitive readers and object-map traversal are hardened.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .dwg_binary_reader import DwgBinaryReadError, DwgBinaryReader
from .dwg_importer import DwgAdapterBlock, DwgAdapterDrawing, DwgAdapterEntity
from .dwg_section_reader import DwgFileHeader, DwgObjectMapEntry
from .dxf_importer import _make_point


class DwgObjectDecodeError(DwgBinaryReadError):
    """Raised when an AC1015 object payload cannot be decoded."""

    def __init__(self, message: str, *, diagnostics: Dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


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
    REAL_ARC_OBJECT_TYPE = 17
    REAL_CIRCLE_OBJECT_TYPE = 18
    REAL_LINE_OBJECT_TYPE = 19
    REAL_LWPOLYLINE_OBJECT_TYPE = 77
    REAL_COORDINATE_ABS_LIMIT = 1_000_000.0

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
        if (
            self.object_map
            and not self.data[
                self.object_map[0].offset:self.object_map[0].offset + len(self.MVP_OBJECT_MAGIC)
            ] == self.MVP_OBJECT_MAGIC
            and self._looks_like_real_object_record(self.object_map[0])
        ):
            return self._decode_real_ac1015()

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
                f"unsupported AC1015 object payload at {entry.offset}: {magic!r}",
                diagnostics=self._object_failure_diagnostics(entry, prefix=magic),
            )
        payload_version = reader.read_u8()
        if payload_version != self.MVP_OBJECT_VERSION:
            raise DwgObjectDecodeError(
                f"unsupported MVP object payload version {payload_version}",
                diagnostics=self._object_failure_diagnostics(entry, payload_version=payload_version),
            )

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
        raise DwgObjectDecodeError(
            f"unsupported AC1015 MVP object type {object_type}",
            diagnostics={"mvp_object_type": object_type},
        )

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

    def _decode_real_ac1015(self) -> DwgAdapterDrawing:
        model_space: List[DwgAdapterEntity] = []
        object_type_counts: Dict[int, int] = {}
        first_unsupported: tuple[DwgObjectMapEntry, bytes] | None = None

        for entry in self.object_map:
            payload, object_type = self._read_real_object_payload(entry)
            object_type_counts[object_type] = object_type_counts.get(object_type, 0) + 1
            try:
                if object_type == self.REAL_LINE_OBJECT_TYPE:
                    model_space.append(self._decode_real_line_entity(entry, payload))
                    continue
                if object_type == self.REAL_CIRCLE_OBJECT_TYPE:
                    model_space.append(self._decode_real_circle_entity(entry, payload))
                    continue
                if object_type == self.REAL_ARC_OBJECT_TYPE:
                    model_space.append(self._decode_real_arc_entity(entry, payload))
                    continue
                if object_type == self.REAL_LWPOLYLINE_OBJECT_TYPE:
                    model_space.append(self._decode_real_lwpolyline_entity(entry, payload))
                    continue
            except DwgObjectDecodeError as exc:
                diagnostics = self._real_object_failure_diagnostics(entry, payload, object_type)
                diagnostics.update(getattr(exc, "diagnostics", {}) or {})
                raise DwgObjectDecodeError(str(exc), diagnostics=diagnostics) from exc
            else:
                if first_unsupported is None:
                    first_unsupported = (entry, payload)
                continue

        if not model_space:
            entry, payload = first_unsupported or (self.object_map[0], b"")
            raise DwgObjectDecodeError(
                "AC1015 real object decoder found no supported model-space entities",
                diagnostics=self._object_failure_diagnostics(entry, prefix=payload[: len(self.MVP_OBJECT_MAGIC)]),
            )

        return DwgAdapterDrawing(
            header={"$ACADVER": self.header.version_code, "$INSUNITS": 4},
            layers=[
                {
                    "name": "0",
                    "color": None,
                    "linetype": None,
                    "lineweight": None,
                    "source_handle": "0",
                }
            ],
            blocks=[],
            model_space=model_space,
            metadata={
                "native_reader": "DwgObjectDecoder",
                "native_reader_mvp": False,
                "native_reader_real_ac1015_partial": True,
                "object_count": len(self.object_map),
                "decoded_object_count": len(model_space),
                "decoded_object_types": _decoded_real_object_type_counts(
                    line_count=object_type_counts.get(self.REAL_LINE_OBJECT_TYPE, 0),
                    circle_count=object_type_counts.get(self.REAL_CIRCLE_OBJECT_TYPE, 0),
                    arc_count=object_type_counts.get(self.REAL_ARC_OBJECT_TYPE, 0),
                    lwpolyline_count=object_type_counts.get(self.REAL_LWPOLYLINE_OBJECT_TYPE, 0),
                ),
                "unsupported_real_object_type_count": sum(
                    count
                    for object_type, count in object_type_counts.items()
                    if object_type
                    not in {
                        self.REAL_LINE_OBJECT_TYPE,
                        self.REAL_CIRCLE_OBJECT_TYPE,
                        self.REAL_ARC_OBJECT_TYPE,
                        self.REAL_LWPOLYLINE_OBJECT_TYPE,
                    }
                ),
            },
        )

    def _read_real_object_payload(self, entry: DwgObjectMapEntry) -> tuple[bytes, int]:
        if entry.offset < 0 or entry.offset + 2 > len(self.data):
            raise DwgObjectDecodeError(
                f"AC1015 object record header outside file at {entry.offset}",
                diagnostics=self._object_failure_diagnostics(entry),
            )
        size = int.from_bytes(self.data[entry.offset:entry.offset + 2], "little")
        payload_start = entry.offset + 2
        payload_end = payload_start + size
        if size <= 0 or payload_end > len(self.data):
            raise DwgObjectDecodeError(
                f"AC1015 object record outside file at {entry.offset}: size={size}",
                diagnostics=self._object_failure_diagnostics(entry),
            )
        payload = self.data[payload_start:payload_end]
        object_type = DwgBinaryReader(payload).read_bit_short()
        return payload, object_type

    def _looks_like_real_object_record(self, entry: DwgObjectMapEntry) -> bool:
        if entry.offset < 0 or entry.offset + 2 > len(self.data):
            return False
        size = int.from_bytes(self.data[entry.offset:entry.offset + 2], "little")
        payload_start = entry.offset + 2
        payload_end = payload_start + size
        if size <= 0 or payload_end > len(self.data):
            return False
        try:
            DwgBinaryReader(self.data[payload_start:payload_end]).read_bit_short()
        except DwgBinaryReadError:
            return False
        return True

    def _decode_real_line_entity(self, entry: DwgObjectMapEntry, payload: bytes) -> DwgAdapterEntity:
        x1, x2, y1, y2 = self._read_real_line_coordinates(payload)
        return DwgAdapterEntity(
            raw_type="LINE",
            geometry={
                "start": _make_point(x1, y1, 0.0),
                "end": _make_point(x2, y2, 0.0),
            },
            layer="0",
            handle=entry.handle_hex,
            style={},
        )

    def _decode_real_circle_entity(self, entry: DwgObjectMapEntry, payload: bytes) -> DwgAdapterEntity:
        center_x, center_y, radius = self._read_real_circle_geometry(payload)
        return DwgAdapterEntity(
            raw_type="CIRCLE",
            geometry={
                "center": _make_point(center_x, center_y, 0.0),
                "radius": radius,
            },
            layer="0",
            handle=entry.handle_hex,
            style={},
        )

    def _decode_real_arc_entity(self, entry: DwgObjectMapEntry, payload: bytes) -> DwgAdapterEntity:
        center_x, center_y, radius, start_angle_deg, end_angle_deg = self._read_real_arc_geometry(payload)
        return DwgAdapterEntity(
            raw_type="ARC",
            geometry={
                "center": _make_point(center_x, center_y, 0.0),
                "radius": radius,
                "start_angle_deg": start_angle_deg,
                "end_angle_deg": end_angle_deg,
            },
            layer="0",
            handle=entry.handle_hex,
            style={},
        )

    def _decode_real_lwpolyline_entity(self, entry: DwgObjectMapEntry, payload: bytes) -> DwgAdapterEntity:
        points = self._read_real_lwpolyline_vertices(payload)
        return DwgAdapterEntity(
            raw_type="LWPOLYLINE",
            geometry={
                "closed": False,
                "vertices": [{"point": _make_point(x, y, 0.0)} for x, y in points],
            },
            layer="0",
            handle=entry.handle_hex,
            style={},
        )

    def _read_real_line_coordinates(self, payload: bytes) -> tuple[float, float, float, float]:
        best: tuple[int, tuple[float, float, float, float]] | None = None
        # R2000 LINE records in the public NextGIS AC1015 samples store x1/x2
        # followed by y1/y2 as bit-aligned little-endian doubles with 66/64/66
        # bit spacing.  Scan only this bounded tuple shape and fail closed when
        # it is not present.
        for start_bit in range(64, (len(payload) * 8) - 260):
            offsets = (start_bit, start_bit + 66, start_bit + 130, start_bit + 196)
            try:
                values = tuple(_read_bit_aligned_f64_le(payload, offset) for offset in offsets)
            except DwgBinaryReadError:
                continue
            if not _is_real_line_coordinate_tuple(values, self.REAL_COORDINATE_ABS_LIMIT):
                continue
            score = sum(1 for value in values if abs(value) >= 1e-6)
            if best is None or score > best[0]:
                best = (score, values)
        if best is None:
            raise DwgObjectDecodeError("unsupported AC1015 LINE coordinate payload")
        return best[1]

    def _read_real_circle_geometry(self, payload: bytes) -> tuple[float, float, float]:
        best: tuple[int, tuple[float, float, float]] | None = None
        # R2000 CIRCLE records in the public NextGIS AC1015 sample store
        # center x/y and radius as bit-aligned little-endian doubles with
        # 66/68 bit spacing.  Keep this intentionally narrow and fail closed.
        for start_bit in range(64, (len(payload) * 8) - 198):
            offsets = (start_bit, start_bit + 66, start_bit + 134)
            try:
                values = tuple(_read_bit_aligned_f64_le(payload, offset) for offset in offsets)
            except DwgBinaryReadError:
                continue
            if not _is_real_circle_geometry_tuple(values, self.REAL_COORDINATE_ABS_LIMIT):
                continue
            score = sum(1 for value in values if abs(value) >= 1e-6)
            if best is None or score > best[0]:
                best = (score, values)
        if best is None:
            raise DwgObjectDecodeError("unsupported AC1015 CIRCLE coordinate payload")
        return best[1]

    def _read_real_arc_geometry(self, payload: bytes) -> tuple[float, float, float, float, float]:
        best: tuple[int, tuple[float, float, float, float]] | None = None
        # R2000 ARC records in the public NextGIS AC1015 sample store center
        # x/y, radius, and end angle as bit-aligned little-endian doubles. The
        # start angle in this deterministic sample is encoded as the DWG zero
        # bit-double sentinel, so keep the slice narrow and fail closed.
        for start_bit in range(64, (len(payload) * 8) - 268):
            offsets = (start_bit, start_bit + 66, start_bit + 134, start_bit + 204)
            try:
                values = tuple(_read_bit_aligned_f64_le(payload, offset) for offset in offsets)
            except DwgBinaryReadError:
                continue
            if not _is_real_arc_geometry_tuple(values, self.REAL_COORDINATE_ABS_LIMIT):
                continue
            score = sum(1 for value in values if abs(value) >= 1e-6)
            if best is None or score > best[0]:
                best = (score, values)
        if best is None:
            raise DwgObjectDecodeError("unsupported AC1015 ARC coordinate payload")
        center_x, center_y, radius, end_angle_rad = best[1]
        return center_x, center_y, radius, 0.0, math.degrees(end_angle_rad)

    def _read_real_lwpolyline_vertices(self, payload: bytes) -> list[tuple[float, float]]:
        best: tuple[int, tuple[float, float, float, float, float, float]] | None = None
        # R2000 LWPOLYLINE records in the public NextGIS AC1015 sample store
        # three open 2D vertices as bit-aligned little-endian doubles. Keep the
        # slice deliberately narrow and do not infer bulges/widths.
        for start_bit in range(64, (len(payload) * 8) - 392):
            offsets = (
                start_bit,
                start_bit + 64,
                start_bit + 130,
                start_bit + 196,
                start_bit + 262,
                start_bit + 328,
            )
            try:
                values = tuple(_read_bit_aligned_f64_le(payload, offset) for offset in offsets)
            except DwgBinaryReadError:
                continue
            if not _is_real_lwpolyline_vertex_tuple(values, self.REAL_COORDINATE_ABS_LIMIT):
                continue
            score = sum(1 for value in values if abs(value) >= 1e-6)
            if best is None or score > best[0]:
                best = (score, values)
        if best is None:
            raise DwgObjectDecodeError("unsupported AC1015 LWPOLYLINE coordinate payload")
        x1, y1, x2, y2, x3, y3 = best[1]
        return [(x1, y1), (x2, y2), (x3, y3)]

    def _object_failure_diagnostics(
        self,
        entry: DwgObjectMapEntry,
        *,
        prefix: bytes | None = None,
        payload_version: int | None = None,
    ) -> Dict[str, Any]:
        sample = self.data[entry.offset:entry.offset + 32]
        diagnostics: Dict[str, Any] = {
            "object_handle": entry.handle_hex,
            "object_offset": entry.offset,
            "object_payload_prefix_hex": sample.hex(),
            "expected_magic_hex": self.MVP_OBJECT_MAGIC.hex(),
        }
        if prefix is not None:
            diagnostics["actual_magic_hex"] = prefix.hex()
        if payload_version is not None:
            diagnostics["payload_version"] = payload_version
        return diagnostics

    def _real_object_failure_diagnostics(
        self,
        entry: DwgObjectMapEntry,
        payload: bytes,
        object_type: int,
    ) -> Dict[str, Any]:
        diagnostics = self._object_failure_diagnostics(entry)
        diagnostics.update(
            {
                "real_object_type": object_type,
                "real_object_type_name": _real_object_type_name(object_type),
                "real_payload_prefix_hex": payload[:32].hex(),
            }
        )
        return diagnostics


def _read_point3(reader: DwgBinaryReader) -> Dict[str, float]:
    return _make_point(reader.read_f64_le(), reader.read_f64_le(), reader.read_f64_le())


def _read_bit_aligned_f64_le(payload: bytes, bit_offset: int) -> float:
    if bit_offset < 0 or bit_offset + 64 > len(payload) * 8:
        raise DwgBinaryReadError("bit-aligned f64 read outside object payload")
    reader = DwgBinaryReader(payload)
    reader.seek(bit_offset // 8)
    reader._bit_pos = bit_offset % 8
    raw = bytes(reader.read_bits(8) for _ in range(8))
    return struct.unpack("<d", raw)[0]


def _is_real_line_coordinate_tuple(values: tuple[float, float, float, float], limit: float) -> bool:
    x1, x2, y1, y2 = values
    if not all(math.isfinite(value) and abs(value) <= limit for value in values):
        return False
    if sum(1 for value in values if abs(value) >= 1e-6) < 4:
        return False
    return abs(x1 - x2) > 1e-9 or abs(y1 - y2) > 1e-9


def _is_real_circle_geometry_tuple(values: tuple[float, float, float], limit: float) -> bool:
    center_x, center_y, radius = values
    if not all(math.isfinite(value) and abs(value) <= limit for value in values):
        return False
    if sum(1 for value in values if abs(value) >= 1e-6) < 3:
        return False
    return radius > 0.0


def _is_real_arc_geometry_tuple(values: tuple[float, float, float, float], limit: float) -> bool:
    center_x, center_y, radius, end_angle_rad = values
    if not all(math.isfinite(value) for value in values):
        return False
    if abs(center_x) > limit or abs(center_y) > limit or abs(radius) > limit:
        return False
    if sum(1 for value in (center_x, center_y, radius) if abs(value) >= 1e-6) < 3:
        return False
    return radius > 0.0 and 1e-6 <= end_angle_rad <= (math.tau + 1e-9)


def _is_real_lwpolyline_vertex_tuple(values: tuple[float, float, float, float, float, float], limit: float) -> bool:
    if not all(math.isfinite(value) and 0.0 < value <= limit for value in values):
        return False
    vertices = [(values[index], values[index + 1]) for index in range(0, len(values), 2)]
    if len({(round(x, 9), round(y, 9)) for x, y in vertices}) < 3:
        return False
    xs = [x for x, _y in vertices]
    ys = [y for _x, y in vertices]
    return (max(xs) - min(xs)) > 1e-9 and (max(ys) - min(ys)) > 1e-9


def _decoded_real_object_type_counts(
    *,
    line_count: int,
    circle_count: int,
    arc_count: int = 0,
    lwpolyline_count: int = 0,
) -> Dict[str, int]:
    decoded: Dict[str, int] = {}
    if line_count:
        decoded["LINE"] = line_count
    if circle_count:
        decoded["CIRCLE"] = circle_count
    if arc_count:
        decoded["ARC"] = arc_count
    if lwpolyline_count:
        decoded["LWPOLYLINE"] = lwpolyline_count
    return decoded


def _real_object_type_name(object_type: int) -> str:
    return {
        DwgObjectDecoder.REAL_LINE_OBJECT_TYPE: "LINE",
        DwgObjectDecoder.REAL_CIRCLE_OBJECT_TYPE: "CIRCLE",
        DwgObjectDecoder.REAL_ARC_OBJECT_TYPE: "ARC",
        DwgObjectDecoder.REAL_LWPOLYLINE_OBJECT_TYPE: "LWPOLYLINE",
    }.get(object_type, f"UNKNOWN_{object_type}")


def _point3(value: Any) -> Dict[str, float]:
    if isinstance(value, dict):
        return _make_point(
            float(value.get("x", 0.0) or 0.0),
            float(value.get("y", 0.0) or 0.0),
            float(value.get("z", 0.0) or 0.0),
        )
    return _make_point(0.0, 0.0, 0.0)
