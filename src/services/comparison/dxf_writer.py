"""CanonicalDrawing -> ASCII DXF R2000 writer.

The writer is intended for validation, debugging, and compatibility export.  It
emits a conservative ASCII DXF subset that the in-repo DxfImporter can read
back and common CAD viewers can inspect.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


Point3 = Dict[str, float]


@dataclass(frozen=True)
class DxfExportOptions:
    """DXF export controls."""

    acad_version: str = "AC1015"
    insunits: Optional[int] = None
    precision: int = 6
    include_blocks: bool = True
    include_expanded_insert_entities: bool = False
    include_entity_handles: bool = True
    use_effective_style: bool = False
    newline: str = "\n"


class DxfWriter:
    """Write CanonicalDrawing dictionaries as ASCII DXF R2000."""

    def __init__(self, options: Optional[DxfExportOptions] = None):
        self.options = options or DxfExportOptions()
        self._handle = 0x100
        self._out = _DxfPairBuffer(precision=self.options.precision)

    def write_string(self, drawing: Dict[str, Any]) -> str:
        self._handle = 0x100
        self._out = _DxfPairBuffer(precision=self.options.precision)
        layer_name_by_id = self._layer_name_by_id(drawing)
        entities_by_id = {
            str(entity.get("id")): entity
            for entity in drawing.get("entities") or []
            if entity.get("id") is not None
        }
        block_entity_ids = self._block_entity_ids(drawing)

        self._write_header(drawing)
        self._write_tables(drawing, layer_name_by_id)
        self._write_blocks(drawing, entities_by_id, layer_name_by_id)
        self._write_entities(drawing, layer_name_by_id, block_entity_ids)
        self._out.add(0, "EOF")
        return self.options.newline.join(self._out.lines) + self.options.newline

    def write_file(self, drawing: Dict[str, Any], path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(self.write_string(drawing))
        return path

    def _write_header(self, drawing: Dict[str, Any]) -> None:
        self._section("HEADER")
        self._out.add(9, "$ACADVER")
        self._out.add(1, self.options.acad_version)
        self._out.add(9, "$INSUNITS")
        self._out.add(70, self._insunits(drawing))
        self._end_section()

    def _write_tables(self, drawing: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> None:
        self._section("TABLES")
        self._write_ltype_table(drawing)
        self._write_layer_table(drawing, layer_name_by_id)
        self._end_section()

    def _write_ltype_table(self, drawing: Dict[str, Any]) -> None:
        linetypes = {"Continuous"}
        for layer in drawing.get("layers") or []:
            if layer.get("linetype"):
                linetypes.add(str(layer["linetype"]))
        for entity in drawing.get("entities") or []:
            style = entity.get("style") or {}
            linetype = style.get("effective_linetype") if self.options.use_effective_style else style.get("linetype")
            if linetype:
                linetypes.add(str(linetype))

        self._out.add(0, "TABLE")
        self._out.add(2, "LTYPE")
        self._out.add(70, len(linetypes))
        for name in sorted(linetypes, key=str.upper):
            self._out.add(0, "LTYPE")
            self._out.add(2, _safe_name(name, "Continuous"))
            self._out.add(70, 0)
            self._out.add(3, "Solid line")
            self._out.add(72, 65)
            self._out.add(73, 0)
            self._out.add(40, 0.0)
        self._out.add(0, "ENDTAB")

    def _write_layer_table(self, drawing: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> None:
        layers = list(drawing.get("layers") or [])
        if not any(_safe_name(layer.get("name"), "0") == "0" for layer in layers):
            layers.insert(0, {"name": "0", "color": 7, "linetype": "Continuous"})

        known_names = {_safe_name(layer.get("name"), "0") for layer in layers}
        for name in layer_name_by_id.values():
            if name not in known_names:
                layers.append({"name": name, "color": 7, "linetype": "Continuous"})
                known_names.add(name)

        self._out.add(0, "TABLE")
        self._out.add(2, "LAYER")
        self._out.add(70, len(layers))
        for layer in layers:
            self._out.add(0, "LAYER")
            self._out.add(2, _safe_name(layer.get("name"), "0"))
            self._out.add(70, 0)
            self._out.add(62, _aci_color(layer.get("color"), default=7))
            self._out.add(6, _safe_name(layer.get("linetype"), "Continuous"))
            if layer.get("lineweight") is not None:
                self._out.add(370, _int_or_default(layer.get("lineweight"), -1))
        self._out.add(0, "ENDTAB")

    def _write_blocks(
        self,
        drawing: Dict[str, Any],
        entities_by_id: Dict[str, Dict[str, Any]],
        layer_name_by_id: Dict[str, str],
    ) -> None:
        self._section("BLOCKS")
        if self.options.include_blocks:
            for block in drawing.get("blocks") or []:
                name = _safe_name(block.get("name"), "")
                if not name:
                    continue
                origin = _point(block.get("origin"))
                self._out.add(0, "BLOCK")
                self._maybe_handle()
                self._out.add(8, "0")
                self._out.add(2, name)
                self._out.add(70, 0)
                self._point_codes(origin, x_code=10, y_code=20, z_code=30)
                self._out.add(3, name)
                self._out.add(1, block.get("source_path") or "")
                for entity_id in block.get("entity_ids") or []:
                    entity = entities_by_id.get(str(entity_id))
                    if entity:
                        self._write_entity(entity, layer_name_by_id)
                self._out.add(0, "ENDBLK")
                self._maybe_handle()
                self._out.add(8, "0")
        self._end_section()

    def _write_entities(
        self,
        drawing: Dict[str, Any],
        layer_name_by_id: Dict[str, str],
        block_entity_ids: Set[str],
    ) -> None:
        self._section("ENTITIES")
        for entity in drawing.get("entities") or []:
            if entity.get("space") == "block" or str(entity.get("id")) in block_entity_ids:
                continue
            if (
                not self.options.include_expanded_insert_entities
                and (entity.get("metadata") or {}).get("expanded_from_insert_id")
            ):
                continue
            self._write_entity(entity, layer_name_by_id)
        self._end_section()

    def _write_entity(self, entity: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> None:
        entity_type = str(entity.get("type") or "").lower()
        if entity_type == "line":
            self._write_line(entity, layer_name_by_id)
        elif entity_type == "polyline":
            self._write_polyline(entity, layer_name_by_id)
        elif entity_type == "circle":
            self._write_circle(entity, layer_name_by_id)
        elif entity_type == "arc":
            self._write_arc(entity, layer_name_by_id)
        elif entity_type == "text":
            self._write_text(entity, layer_name_by_id)
        elif entity_type == "mtext":
            self._write_mtext(entity, layer_name_by_id)
        elif entity_type == "block_reference":
            self._write_insert(entity, layer_name_by_id)

    def _write_line(self, entity: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> None:
        geometry = entity.get("geometry") or {}
        self._entity_start("LINE", entity, layer_name_by_id)
        self._point_codes(_point(geometry.get("start")), x_code=10, y_code=20, z_code=30)
        self._point_codes(_point(geometry.get("end")), x_code=11, y_code=21, z_code=31)

    def _write_polyline(self, entity: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> None:
        geometry = entity.get("geometry") or {}
        vertices = list(geometry.get("vertices") or [])
        points = [_point(vertex.get("point")) for vertex in vertices if isinstance(vertex, dict)]
        if not points:
            return
        z_values = {round(point.get("z", 0.0), self.options.precision) for point in points}
        if len(z_values) <= 1:
            self._entity_start("LWPOLYLINE", entity, layer_name_by_id, subclass="AcDbPolyline")
            self._out.add(90, len(vertices))
            self._out.add(70, 1 if geometry.get("closed") else 0)
            elevation = points[0].get("z", 0.0)
            if elevation:
                self._out.add(38, elevation)
            emit_widths = any(
                (vertex.get("start_width") is not None or vertex.get("end_width") is not None)
                for vertex in vertices
                if isinstance(vertex, dict)
            )
            for vertex in vertices:
                if not isinstance(vertex, dict):
                    continue
                point = _point(vertex.get("point"))
                self._out.add(10, point["x"])
                self._out.add(20, point["y"])
                if emit_widths:
                    self._out.add(40, vertex.get("start_width") or 0.0)
                    self._out.add(41, vertex.get("end_width") or 0.0)
                self._out.add(42, vertex.get("bulge") or 0.0)
            return

        self._entity_start("POLYLINE", entity, layer_name_by_id)
        self._out.add(66, 1)
        self._out.add(70, 8 | (1 if geometry.get("closed") else 0))
        for vertex in vertices:
            if not isinstance(vertex, dict):
                continue
            self._out.add(0, "VERTEX")
            self._maybe_handle()
            self._out.add(8, self._layer_name(entity, layer_name_by_id))
            self._point_codes(_point(vertex.get("point")), x_code=10, y_code=20, z_code=30)
            self._out.add(42, vertex.get("bulge") or 0.0)
            if vertex.get("start_width") is not None:
                self._out.add(40, vertex.get("start_width"))
            if vertex.get("end_width") is not None:
                self._out.add(41, vertex.get("end_width"))
        self._out.add(0, "SEQEND")
        self._maybe_handle()
        self._out.add(8, self._layer_name(entity, layer_name_by_id))

    def _write_circle(self, entity: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> None:
        geometry = entity.get("geometry") or {}
        self._entity_start("CIRCLE", entity, layer_name_by_id)
        self._point_codes(_point(geometry.get("center")), x_code=10, y_code=20, z_code=30)
        self._out.add(40, geometry.get("radius") or 0.0)

    def _write_arc(self, entity: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> None:
        geometry = entity.get("geometry") or {}
        self._entity_start("ARC", entity, layer_name_by_id)
        self._point_codes(_point(geometry.get("center")), x_code=10, y_code=20, z_code=30)
        self._out.add(40, geometry.get("radius") or 0.0)
        self._out.add(50, geometry.get("start_angle_deg") or 0.0)
        self._out.add(51, geometry.get("end_angle_deg") or 0.0)

    def _write_text(self, entity: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> None:
        geometry = entity.get("geometry") or {}
        self._entity_start("TEXT", entity, layer_name_by_id)
        self._point_codes(_point(geometry.get("insert")), x_code=10, y_code=20, z_code=30)
        self._out.add(40, geometry.get("height") or 2.5)
        self._out.add(1, geometry.get("text") or geometry.get("canonical_text") or "")
        self._out.add(50, geometry.get("rotation_deg") or 0.0)
        alignment = str(geometry.get("alignment") or "0:0").split(":")
        if len(alignment) == 2:
            self._out.add(72, _int_or_default(alignment[0], 0))
            self._out.add(73, _int_or_default(alignment[1], 0))

    def _write_mtext(self, entity: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> None:
        geometry = entity.get("geometry") or {}
        self._entity_start("MTEXT", entity, layer_name_by_id, subclass="AcDbMText")
        self._point_codes(_point(geometry.get("insert")), x_code=10, y_code=20, z_code=30)
        self._out.add(40, geometry.get("height") or 2.5)
        if geometry.get("box_width") is not None:
            self._out.add(41, geometry.get("box_width"))
        if geometry.get("attachment") is not None:
            self._out.add(71, _int_or_default(geometry.get("attachment"), 1))
        self._out.add(50, geometry.get("rotation_deg") or 0.0)
        text = geometry.get("raw_content") or geometry.get("plain_text") or geometry.get("canonical_text") or ""
        self._out.add(1, str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\P"))

    def _write_insert(self, entity: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> None:
        geometry = entity.get("geometry") or {}
        attributes = list(geometry.get("attributes") or [])
        insert = _point(geometry.get("insert"))
        self._entity_start("INSERT", entity, layer_name_by_id)
        self._out.add(2, _safe_name(geometry.get("block_name"), ""))
        self._point_codes(insert, x_code=10, y_code=20, z_code=30)
        scale = _point(geometry.get("scale"), default=(1.0, 1.0, 1.0))
        self._out.add(41, scale["x"])
        self._out.add(42, scale["y"])
        self._out.add(43, scale["z"])
        self._out.add(50, geometry.get("rotation_deg") or 0.0)
        if attributes:
            self._out.add(66, 1)
            for attribute in attributes:
                self._out.add(0, "ATTRIB")
                self._maybe_handle()
                self._out.add(8, self._layer_name(entity, layer_name_by_id))
                self._point_codes(
                    _point(attribute.get("insert"), default=(insert["x"], insert["y"], insert.get("z", 0.0))),
                    x_code=10,
                    y_code=20,
                    z_code=30,
                )
                self._out.add(40, attribute.get("height") or geometry.get("height") or 2.5)
                self._out.add(1, attribute.get("text") or attribute.get("canonical_text") or "")
                self._out.add(2, attribute.get("tag") or "")
            self._out.add(0, "SEQEND")
            self._maybe_handle()
            self._out.add(8, self._layer_name(entity, layer_name_by_id))

    def _entity_start(
        self,
        raw_type: str,
        entity: Dict[str, Any],
        layer_name_by_id: Dict[str, str],
        *,
        subclass: Optional[str] = None,
    ) -> None:
        # When *subclass* is given, emit a valid R2000 subclass chain
        # (AcDbEntity -> <subclass>) so strict readers like ezdxf accept the
        # R2000-only entities (LWPOLYLINE/MTEXT). When None (default), the
        # output is byte-identical to the legacy R12-style form that the
        # in-repo DxfImporter and the R12-compatible entities rely on.
        self._out.add(0, raw_type)
        self._maybe_handle(entity)
        if subclass is not None:
            self._out.add(100, "AcDbEntity")
        self._out.add(8, self._layer_name(entity, layer_name_by_id))
        self._write_style(entity)
        if subclass is not None:
            self._out.add(100, subclass)

    def _write_style(self, entity: Dict[str, Any]) -> None:
        style = entity.get("style") or {}
        color_key = "effective_color" if self.options.use_effective_style else "color"
        linetype_key = "effective_linetype" if self.options.use_effective_style else "linetype"
        lineweight_key = "effective_lineweight" if self.options.use_effective_style else "lineweight"
        if style.get(color_key) is not None:
            self._out.add(62, _aci_color(style.get(color_key), default=256))
        if style.get(linetype_key):
            self._out.add(6, _safe_name(style.get(linetype_key), "Continuous"))
        if style.get(lineweight_key) is not None:
            self._out.add(370, _int_or_default(style.get(lineweight_key), -1))

    def _section(self, name: str) -> None:
        self._out.add(0, "SECTION")
        self._out.add(2, name)

    def _end_section(self) -> None:
        self._out.add(0, "ENDSEC")

    def _point_codes(self, point: Point3, *, x_code: int, y_code: int, z_code: int) -> None:
        self._out.add(x_code, point["x"])
        self._out.add(y_code, point["y"])
        self._out.add(z_code, point.get("z", 0.0))

    def _maybe_handle(self, entity: Optional[Dict[str, Any]] = None) -> None:
        if not self.options.include_entity_handles:
            return
        handle = ((entity or {}).get("source") or {}).get("handle")
        if not handle:
            handle = f"{self._handle:X}"
            self._handle += 1
        self._out.add(5, str(handle))

    def _layer_name(self, entity: Dict[str, Any], layer_name_by_id: Dict[str, str]) -> str:
        return layer_name_by_id.get(str(entity.get("layer_id"))) or "0"

    def _layer_name_by_id(self, drawing: Dict[str, Any]) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for layer in drawing.get("layers") or []:
            if layer.get("id"):
                mapping[str(layer["id"])] = _safe_name(layer.get("name"), "0")
        return mapping

    def _block_entity_ids(self, drawing: Dict[str, Any]) -> Set[str]:
        ids: Set[str] = set()
        for block in drawing.get("blocks") or []:
            ids.update(str(entity_id) for entity_id in block.get("entity_ids") or [])
        return ids

    def _insunits(self, drawing: Dict[str, Any]) -> int:
        if self.options.insunits is not None:
            return int(self.options.insunits)
        units = drawing.get("units") or {}
        return _UNIT_NAME_TO_INSUNITS.get(str(units.get("source_unit") or units.get("canonical_unit") or "mm"), 4)


class _DxfPairBuffer:
    def __init__(self, *, precision: int):
        self.precision = precision
        self.lines: List[str] = []

    def add(self, code: int, value: Any) -> None:
        self.lines.append(str(int(code)))
        self.lines.append(self._format_value(value))

    def _format_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            text = f"{value:.{self.precision}f}".rstrip("0").rstrip(".")
            return "0" if text in {"", "-0"} else text
        return str(value)


_UNIT_NAME_TO_INSUNITS = {
    "unitless": 0,
    "inch": 1,
    "foot": 2,
    "mile": 3,
    "mm": 4,
    "cm": 5,
    "m": 6,
    "km": 7,
    "microinch": 8,
    "mil": 9,
    "yard": 10,
    "angstrom": 11,
    "nanometer": 12,
    "micron": 13,
    "decimeter": 14,
    "decameter": 15,
    "hectometer": 16,
    "gigameter": 17,
    "au": 18,
    "light_year": 19,
    "parsec": 20,
}


def _point(value: Any, *, default: Sequence[float] = (0.0, 0.0, 0.0)) -> Point3:
    if isinstance(value, dict):
        return {
            "x": float(value.get("x", default[0]) or 0.0),
            "y": float(value.get("y", default[1]) or 0.0),
            "z": float(value.get("z", default[2]) or 0.0),
        }
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return {
            "x": float(value[0]),
            "y": float(value[1]),
            "z": float(value[2]) if len(value) > 2 else float(default[2]),
        }
    return {"x": float(default[0]), "y": float(default[1]), "z": float(default[2])}


def _safe_name(value: Any, default: str) -> str:
    text = str(value or default).strip()
    if not text:
        return default
    return (
        text.replace("\r", " ")
        .replace("\n", " ")
        .replace("/", "_")
        .replace("\\", "_")
    )


def _aci_color(value: Any, *, default: int) -> int:
    try:
        color = int(float(value))
    except (TypeError, ValueError):
        return default
    if color < -255 or color > 256:
        return default
    return color


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default
