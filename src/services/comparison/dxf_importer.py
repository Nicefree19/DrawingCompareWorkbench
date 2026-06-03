"""ASCII DXF -> CanonicalDrawing importer.

This module intentionally does not use ODA or ezdxf.  It implements the small
DXF group-code reader needed to normalize common 2D CAD drawings into the
format-neutral CanonicalDrawing contract documented in ``docs/``.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple

from .cad_stability import CadLimitCode, CancelCallback


Point3 = Dict[str, float]
BBox = Dict[str, float | str]


class DxfParseError(ValueError):
    """Raised when the ASCII DXF token stream is structurally invalid."""


class DxfImportLimitError(RuntimeError):
    """Raised when DXF import exceeds configured stability limits."""

    def __init__(self, code: str, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class DxfToken:
    """One DXF group-code/value pair."""

    code: int
    value: str
    line_no: int


class DxfTokenizer:
    """Tokenize ASCII DXF group-code/value pairs.

    DXF stores records as two-line pairs: an integer group code line followed
    by a value line.  The tokenizer preserves values as strings; entity mappers
    convert them according to group code semantics.
    """

    def __init__(
        self,
        text: str,
        *,
        max_tokens: int = 0,
        timeout_seconds: Optional[float] = None,
        cancel_callback: Optional[CancelCallback] = None,
    ):
        self._text = text
        self.max_tokens = max(0, int(max_tokens or 0))
        self.timeout_seconds = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        self.cancel_callback = cancel_callback

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        encoding: str = "utf-8",
        *,
        max_tokens: int = 0,
        timeout_seconds: Optional[float] = None,
        cancel_callback: Optional[CancelCallback] = None,
    ) -> "DxfTokenizer":
        data = Path(path).read_bytes()
        if b"\x00" in data[:4096]:
            raise DxfParseError("Binary DXF is not supported by DxfTokenizer")
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            text = data.decode("cp949", errors="replace")
        return cls(
            text,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            cancel_callback=cancel_callback,
        )

    def tokenize(self) -> List[DxfToken]:
        started = time.perf_counter()
        raw_lines = self._text.splitlines()
        tokens: List[DxfToken] = []
        i = 0
        while i < len(raw_lines):
            if len(tokens) % 4096 == 0:
                self._check_limits(started, len(tokens))
            code_line_no = i + 1
            code_text = raw_lines[i].strip()
            i += 1
            if code_text == "":
                continue
            if i >= len(raw_lines):
                raise DxfParseError(
                    f"Missing DXF value line after group code at line {code_line_no}"
                )
            value = raw_lines[i].rstrip("\r\n")
            i += 1
            try:
                code = int(code_text)
            except ValueError as exc:
                raise DxfParseError(
                    f"Invalid DXF group code {code_text!r} at line {code_line_no}"
                ) from exc
            tokens.append(DxfToken(code=code, value=value, line_no=code_line_no))
            if self.max_tokens and len(tokens) > self.max_tokens:
                raise DxfImportLimitError(
                    CadLimitCode.TOKEN_LIMIT_EXCEEDED,
                    f"DXF token limit exceeded: {len(tokens)} > {self.max_tokens}",
                    details={"token_count": len(tokens), "max_tokens": self.max_tokens},
                )
        return tokens

    def _check_limits(self, started: float, token_count: int) -> None:
        if self.cancel_callback and self.cancel_callback():
            raise DxfImportLimitError(
                CadLimitCode.IMPORT_CANCELLED,
                "DXF import cancelled during tokenization.",
                details={"token_count": token_count},
            )
        if self.timeout_seconds is not None and (time.perf_counter() - started) > self.timeout_seconds:
            raise DxfImportLimitError(
                CadLimitCode.IMPORT_TIMEOUT,
                f"DXF import timed out after {self.timeout_seconds:.3f}s during tokenization.",
                details={"token_count": token_count, "timeout_seconds": self.timeout_seconds},
            )


@dataclass
class DxfRecord:
    """DXF record starting with group code 0."""

    type: str
    pairs: List[DxfToken] = field(default_factory=list)
    children: List["DxfRecord"] = field(default_factory=list)


@dataclass
class MapperContext:
    source_format: str
    space: str
    ensure_layer: Callable[[str, Optional[Dict[str, Any]]], str]
    next_id: Callable[[str], str]
    warnings: List[Dict[str, Any]]
    block_id: Optional[str] = None
    block_name: Optional[str] = None
    layout_name: Optional[str] = None
    source_path: List[str] = field(default_factory=list)
    coordinate_quantum_mm: float = 0.01
    angle_quantum_deg: float = 0.001


class DxfEntityMapper:
    """Map raw DXF entity records to CanonicalEntity dictionaries."""

    SUPPORTED_TYPES = {
        "LINE",
        "LWPOLYLINE",
        "POLYLINE",
        "CIRCLE",
        "ARC",
        "ELLIPSE",
        "SPLINE",
        "TEXT",
        "MTEXT",
        "INSERT",
        "HATCH",
        "DIMENSION",
    }

    def map_record(self, record: DxfRecord, context: MapperContext) -> Optional[Dict[str, Any]]:
        raw_type = _norm_type(record.type)
        try:
            if raw_type == "LINE":
                entity = self._map_line(record, context)
            elif raw_type == "CIRCLE":
                entity = self._map_circle(record, context)
            elif raw_type == "ARC":
                entity = self._map_arc(record, context)
            elif raw_type == "LWPOLYLINE":
                entity = self._map_lwpolyline(record, context)
            elif raw_type == "POLYLINE":
                entity = self._map_polyline(record, context)
            elif raw_type == "TEXT":
                entity = self._map_text(record, context)
            elif raw_type == "MTEXT":
                entity = self._map_mtext(record, context)
            elif raw_type == "INSERT":
                entity = self._map_insert(record, context)
            elif raw_type == "ELLIPSE":
                entity = self._map_ellipse(record, context)
            elif raw_type == "SPLINE":
                entity = self._map_spline(record, context)
            elif raw_type == "HATCH":
                entity = self._map_hatch(record, context)
            elif raw_type == "DIMENSION":
                entity = self._map_dimension(record, context)
            else:
                self._warn_unsupported(record, context, "skipped", "comparison_incomplete")
                return None
        except Exception as exc:
            _append_warning(
                context.warnings,
                code="ENTITY_MAP_FAILED",
                severity="warning",
                message=f"Failed to map DXF entity {raw_type}: {exc}",
                raw_type=raw_type,
                source_handle=_first(record.pairs, 5),
            )
            return None

        return entity

    def _common(
        self,
        record: DxfRecord,
        context: MapperContext,
        canonical_type: str,
        geometry: Dict[str, Any],
        bbox: BBox,
        *,
        layer_name: Optional[str] = None,
        semantic_payload: Optional[Dict[str, Any]] = None,
        source_raw_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        raw_type = source_raw_type or _norm_type(record.type)
        layer = layer_name if layer_name is not None else (_first(record.pairs, 8) or "0")
        layer_id = context.ensure_layer(layer or "0", None)
        style = _style_from_pairs(record.pairs)
        source = {
            "format": context.source_format,
            "raw_type": raw_type,
            "handle": _first(record.pairs, 5),
            "owner_handle": _first(record.pairs, 330),
            "layout_name": context.layout_name,
            "block_name": context.block_name,
            "path": list(context.source_path),
        }
        entity = {
            "id": context.next_id(canonical_type),
            "type": canonical_type,
            "source": source,
            "layer_id": layer_id,
            "block_id": context.block_id,
            "space": context.space,
            "layout_name": context.layout_name,
            "geometry": geometry,
            "bbox": bbox,
            "style": style,
            "visible": True,
            "metadata": {},
        }
        geometry_payload = _geometry_hash_payload(canonical_type, geometry)
        entity["hashes"] = {
            "geometry_hash": _hash_payload("geom", geometry_payload),
            "semantic_hash": (
                _hash_payload("sem", semantic_payload)
                if semantic_payload
                else None
            ),
            "style_hash": _hash_payload("style", style) if _has_style(style) else None,
            "source_fingerprint": _hash_payload("src", source),
        }
        return entity

    def _map_line(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        start = _point(groups, 10, 20, 30)
        end = _point(groups, 11, 21, 31)
        geometry = {"type": "line", "start": start, "end": end}
        return self._common(record, context, "line", geometry, _bbox_from_points([start, end], "exact"))

    def _map_circle(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        center = _point(groups, 10, 20, 30)
        radius = max(0.0, _float(groups, 40, 0.0))
        normal = _point(groups, 210, 220, 230, default=(0.0, 0.0, 1.0))
        geometry = {"type": "circle", "center": center, "radius": radius, "normal": normal}
        return self._common(record, context, "circle", geometry, _circle_bbox(center, radius, "exact"))

    def _map_arc(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        center = _point(groups, 10, 20, 30)
        radius = max(0.0, _float(groups, 40, 0.0))
        start_angle = _normalize_angle(_float(groups, 50, 0.0))
        end_angle = _normalize_angle(_float(groups, 51, 0.0))
        normal = _point(groups, 210, 220, 230, default=(0.0, 0.0, 1.0))
        geometry = {
            "type": "arc",
            "center": center,
            "radius": radius,
            "start_angle_deg": start_angle,
            "end_angle_deg": end_angle,
            "normal": normal,
            "sweep_direction": "ccw",
        }
        return self._common(record, context, "arc", geometry, _arc_bbox(center, radius, start_angle, end_angle))

    def _map_lwpolyline(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        xs = groups.get(10, [])
        ys = groups.get(20, [])
        bulges = groups.get(42, [])
        start_widths = groups.get(40, [])
        end_widths = groups.get(41, [])
        vertices = []
        for idx, (x, y) in enumerate(zip(xs, ys)):
            vertices.append(
                {
                    "point": _make_point(_to_float(x), _to_float(y), _float(groups, 38, 0.0)),
                    "bulge": _to_float(bulges[idx]) if idx < len(bulges) else 0.0,
                    "start_width": _to_float(start_widths[idx]) if idx < len(start_widths) else None,
                    "end_width": _to_float(end_widths[idx]) if idx < len(end_widths) else None,
                }
            )
        closed = bool(_int(groups, 70, 0) & 1)
        geometry = {
            "type": "polyline",
            "vertices": vertices,
            "closed": closed,
            "polyline_kind": "lwpolyline",
        }
        return self._common(
            record,
            context,
            "polyline",
            geometry,
            _bbox_from_points([v["point"] for v in vertices], "control_points"),
        )

    def _map_polyline(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        vertices = []
        for child in record.children:
            if _norm_type(child.type) != "VERTEX":
                continue
            child_groups = _groups(child.pairs)
            vertices.append(
                {
                    "point": _point(child_groups, 10, 20, 30),
                    "bulge": _float(child_groups, 42, 0.0),
                    "start_width": _nullable_float(child_groups, 40),
                    "end_width": _nullable_float(child_groups, 41),
                }
            )
        closed = bool(_int(groups, 70, 0) & 1)
        flags = _int(groups, 70, 0)
        kind = "3d_polyline" if flags & 8 else "2d_polyline"
        geometry = {
            "type": "polyline",
            "vertices": vertices,
            "closed": closed,
            "polyline_kind": kind,
        }
        return self._common(
            record,
            context,
            "polyline",
            geometry,
            _bbox_from_points([v["point"] for v in vertices], "control_points"),
        )

    def _map_text(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        raw_text = _first(record.pairs, 1) or ""
        canonical_text = _canonical_text(raw_text)
        geometry = {
            "type": "text",
            "insert": _point(groups, 10, 20, 30),
            "text": raw_text,
            "canonical_text": canonical_text,
            "height": _float(groups, 40, 0.0),
            "rotation_deg": _normalize_angle(_float(groups, 50, 0.0)),
            "alignment": _text_alignment(groups),
        }
        bbox = _text_bbox(geometry["insert"], geometry["height"], raw_text, "estimated")
        return self._common(
            record,
            context,
            "text",
            geometry,
            bbox,
            semantic_payload={"text": canonical_text},
        )

    def _map_mtext(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        raw_content = "".join(
            token.value for token in record.pairs if token.code in {1, 3}
        )
        plain_text = _plain_mtext(raw_content)
        canonical_text = _canonical_text(plain_text)
        geometry = {
            "type": "mtext",
            "insert": _point(groups, 10, 20, 30),
            "plain_text": plain_text,
            "canonical_text": canonical_text,
            "raw_content": raw_content,
            "height": _float(groups, 40, 0.0),
            "box_width": _nullable_float(groups, 41),
            "rotation_deg": _normalize_angle(_float(groups, 50, 0.0)),
        }
        if 71 in groups:
            geometry["attachment"] = str(_int(groups, 71, 0))
        bbox = _text_bbox(geometry["insert"], geometry["height"], plain_text, "estimated")
        return self._common(
            record,
            context,
            "mtext",
            geometry,
            bbox,
            semantic_payload={"text": canonical_text},
        )

    def _map_insert(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        block_name = _first(record.pairs, 2) or ""
        block_id = _block_id(block_name)
        insert = _point(groups, 10, 20, 30)
        scale = _make_point(
            _float(groups, 41, 1.0),
            _float(groups, 42, 1.0),
            _float(groups, 43, 1.0),
        )
        rotation = _normalize_angle(_float(groups, 50, 0.0))
        matrix = _matrix_2d(insert, scale, rotation)
        attributes = []
        for child in record.children:
            if _norm_type(child.type) != "ATTRIB":
                continue
            child_groups = _groups(child.pairs)
            raw_text = _first(child.pairs, 1) or ""
            attributes.append(
                {
                    "tag": _first(child.pairs, 2) or "",
                    "text": raw_text,
                    "canonical_text": _canonical_text(raw_text),
                    "insert": _point(child_groups, 10, 20, 30),
                    "source_handle": _first(child.pairs, 5),
                }
            )
        geometry = {
            "type": "block_reference",
            "block_id": block_id,
            "block_name": block_name,
            "insert": insert,
            "scale": scale,
            "rotation_deg": rotation,
            "matrix": matrix,
            "attributes": attributes,
            "expanded_entity_ids": [],
        }
        return self._common(
            record,
            context,
            "block_reference",
            geometry,
            _bbox_from_points([insert], "estimated"),
            semantic_payload={"attributes": attributes},
        )

    def _map_ellipse(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        center = _point(groups, 10, 20, 30)
        major = _point(groups, 11, 21, 31)
        ratio = _float(groups, 40, 1.0)
        start = _float(groups, 41, 0.0)
        end = _float(groups, 42, math.tau)
        points = _ellipse_points(center, major, ratio, start, end)
        vertices = [{"point": point, "bulge": 0.0, "start_width": None, "end_width": None} for point in points]
        geometry = {
            "type": "polyline",
            "vertices": vertices,
            "closed": abs((end - start) - math.tau) < 1e-6,
            "polyline_kind": "2d_polyline",
        }
        _append_warning(
            context.warnings,
            code="ENTITY_APPROXIMATED",
            severity="warning",
            message="ELLIPSE was approximated as canonical polyline.",
            raw_type="ELLIPSE",
            source_handle=_first(record.pairs, 5),
        )
        return self._common(
            record,
            context,
            "polyline",
            geometry,
            _bbox_from_points(points, "control_points"),
            source_raw_type="ELLIPSE",
        )

    def _map_spline(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        xs = groups.get(10, []) or groups.get(11, [])
        ys = groups.get(20, []) or groups.get(21, [])
        zs = groups.get(30, []) or groups.get(31, [])
        points = [
            _make_point(
                _to_float(x),
                _to_float(y),
                _to_float(zs[idx]) if idx < len(zs) else 0.0,
            )
            for idx, (x, y) in enumerate(zip(xs, ys))
        ]
        if len(points) < 2:
            self._warn_unsupported(record, context, "skipped", "comparison_incomplete")
            return None  # type: ignore[return-value]
        vertices = [{"point": point, "bulge": 0.0, "start_width": None, "end_width": None} for point in points]
        geometry = {
            "type": "polyline",
            "vertices": vertices,
            "closed": bool(_int(groups, 70, 0) & 1),
            "polyline_kind": "3d_polyline" if any(abs(p["z"]) > 1e-9 for p in points) else "2d_polyline",
        }
        _append_warning(
            context.warnings,
            code="ENTITY_APPROXIMATED",
            severity="warning",
            message="SPLINE control/fit points were approximated as canonical polyline.",
            raw_type="SPLINE",
            source_handle=_first(record.pairs, 5),
        )
        return self._common(
            record,
            context,
            "polyline",
            geometry,
            _bbox_from_points(points, "control_points"),
            source_raw_type="SPLINE",
        )

    def _map_hatch(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        xs = groups.get(10, [])
        ys = groups.get(20, [])
        vertices = [
            {"point": _make_point(_to_float(x), _to_float(y), 0.0), "bulge": 0.0}
            for x, y in zip(xs, ys)
        ]
        boundaries = [{"role": "unknown", "vertices": vertices}] if vertices else []
        geometry = {
            "type": "hatch",
            "pattern_name": (_first(record.pairs, 2) or "SOLID").upper(),
            "solid_fill": _int(groups, 70, 0) == 1,
            "pattern_scale": _nullable_float(groups, 41),
            "pattern_angle_deg": _nullable_float(groups, 52),
            "boundaries": boundaries,
        }
        if not vertices:
            _append_warning(
                context.warnings,
                code="HATCH_EDGE_APPROXIMATED",
                severity="warning",
                message="HATCH has no simple vertex boundary in ASCII importer.",
                raw_type="HATCH",
                source_handle=_first(record.pairs, 5),
            )
        return self._common(
            record,
            context,
            "hatch",
            geometry,
            _bbox_from_points([v["point"] for v in vertices], "control_points" if vertices else "missing"),
            semantic_payload={
                "pattern_name": geometry["pattern_name"],
                "pattern_scale": geometry["pattern_scale"],
                "pattern_angle_deg": geometry["pattern_angle_deg"],
            },
        )

    def _map_dimension(self, record: DxfRecord, context: MapperContext) -> Dict[str, Any]:
        groups = _groups(record.pairs)
        defpoints = [
            _point(groups, 10, 20, 30),
            _point(groups, 13, 23, 33),
            _point(groups, 14, 24, 34),
        ]
        text_override = _first(record.pairs, 1)
        canonical_text = _canonical_text(text_override or "") if text_override is not None else None
        dim_type = _dimension_type(_int(groups, 70, 0))
        text_midpoint = _point(groups, 11, 21, 31) if 11 in groups or 21 in groups else None
        geometry = {
            "type": "dimension",
            "dimension_type": dim_type,
            "measurement": _nullable_float(groups, 42),
            "text_override": text_override,
            "canonical_text": canonical_text,
            "defpoints": defpoints,
            "text_midpoint": text_midpoint,
        }
        bbox_points = list(defpoints)
        if text_midpoint:
            bbox_points.append(text_midpoint)
        return self._common(
            record,
            context,
            "dimension",
            geometry,
            _bbox_from_points(bbox_points, "estimated"),
            semantic_payload={
                "dimension_type": dim_type,
                "measurement": geometry["measurement"],
                "canonical_text": canonical_text,
            },
        )

    def _warn_unsupported(
        self,
        record: DxfRecord,
        context: MapperContext,
        policy: str,
        impact: str,
    ) -> None:
        _append_warning(
            context.warnings,
            code="UNSUPPORTED_ENTITY",
            severity="warning",
            message=f"Unsupported DXF entity {record.type} was {policy}.",
            raw_type=_norm_type(record.type),
            source_handle=_first(record.pairs, 5),
            details={"policy": policy, "impact": impact},
        )


class DxfImporter:
    """Import ASCII DXF files into the CanonicalDrawing dictionary model."""

    SCHEMA_VERSION = "canonical-drawing/v1"

    def __init__(
        self,
        *,
        expand_blocks: bool = True,
        max_block_depth: int = 2,
        max_entities: int = 0,
        max_tokens: int = 0,
        timeout_seconds: Optional[float] = None,
        cancel_callback: Optional[CancelCallback] = None,
    ):
        self.expand_blocks = bool(expand_blocks)
        self.max_block_depth = max(0, int(max_block_depth))
        self.max_entities = max(0, int(max_entities or 0))
        self.max_tokens = max(0, int(max_tokens or 0))
        self.timeout_seconds = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        self.cancel_callback = cancel_callback
        self.mapper = DxfEntityMapper()
        self._entity_seq = 0
        self._layers_by_name: Dict[str, Dict[str, Any]] = {}
        self._blocks_by_name: Dict[str, Dict[str, Any]] = {}
        self._block_entities_by_name: Dict[str, List[Dict[str, Any]]] = {}
        self._warnings: List[Dict[str, Any]] = []
        self._unsupported: Counter[str] = Counter()
        self._approximated_count = 0

    def import_file(self, path: str | Path) -> Dict[str, Any]:
        path = Path(path)
        tokenizer = DxfTokenizer.from_file(
            path,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
            cancel_callback=self.cancel_callback,
        )
        return self.import_tokens(tokenizer.tokenize(), source_path=path)

    def import_text(
        self,
        text: str,
        *,
        source_path: str | Path | None = None,
        file_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.import_tokens(
            DxfTokenizer(
                text,
                max_tokens=self.max_tokens,
                timeout_seconds=self.timeout_seconds,
                cancel_callback=self.cancel_callback,
            ).tokenize(),
            source_path=source_path,
            file_name=file_name,
        )

    def import_tokens(
        self,
        tokens: Sequence[DxfToken],
        *,
        source_path: str | Path | None = None,
        file_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        start_time = time.perf_counter()
        self._reset()
        self._check_runtime_limits(start_time, 0)
        if self.max_tokens and len(tokens) > self.max_tokens:
            raise DxfImportLimitError(
                CadLimitCode.TOKEN_LIMIT_EXCEEDED,
                f"DXF token limit exceeded: {len(tokens)} > {self.max_tokens}",
                details={"token_count": len(tokens), "max_tokens": self.max_tokens},
            )
        sections = self._parse_sections(tokens)
        self._check_runtime_limits(start_time, 0)
        header = self._parse_header(sections.get("HEADER", []))
        tables = self._parse_tables(sections.get("TABLES", []))
        self._layers_by_name.update(tables.get("layers", {}))
        self._ensure_layer("0", None)

        source = _source_file(source_path, file_name, header)
        units = _unit_policy(header)
        tolerances = _default_tolerances()
        coordinate_system = {
            "space": "WCS",
            "axis_order": ["x", "y", "z"],
            "origin": _make_point(0.0, 0.0, 0.0),
            "z_policy": "preserve",
            "layout_policy": "all_layouts_namespaced",
        }

        entities: List[Dict[str, Any]] = []
        block_parse = self._parse_blocks(sections.get("BLOCKS", []))
        for block in block_parse:
            block_entities: List[Dict[str, Any]] = []
            block_id = _block_id(block["name"])
            context = MapperContext(
                source_format="dxf",
                space="block",
                ensure_layer=self._ensure_layer,
                next_id=self._next_id,
                warnings=self._warnings,
                block_id=block_id,
                block_name=block["name"],
                source_path=["BLOCKS", block["name"]],
            )
            for record in block["records"]:
                self._check_runtime_limits(start_time, len(entities))
                mapped = self.mapper.map_record(record, context)
                if mapped:
                    block_entities.append(mapped)
                    self._append_entity(entities, mapped, start_time)
            block_def = {
                "id": block_id,
                "name": block["name"],
                "normalized_name": _normalize_key(block["name"]),
                "origin": block["origin"],
                "entity_ids": [entity["id"] for entity in block_entities],
                "bbox": _union_bbox([entity["bbox"] for entity in block_entities]),
                "is_external_reference": False,
                "source_path": None,
                "metadata": {},
            }
            self._blocks_by_name[_normalize_key(block["name"])] = block_def
            self._block_entities_by_name[_normalize_key(block["name"])] = block_entities

        entity_records = self._parse_entities(sections.get("ENTITIES", []))
        model_context = MapperContext(
            source_format="dxf",
            space="model",
            ensure_layer=self._ensure_layer,
            next_id=self._next_id,
            warnings=self._warnings,
            layout_name="Model",
            source_path=["ENTITIES"],
        )
        for record in entity_records:
            self._check_runtime_limits(start_time, len(entities))
            mapped = self.mapper.map_record(record, model_context)
            if not mapped:
                self._unsupported[_norm_type(record.type)] += 1
                continue
            self._append_entity(entities, mapped, start_time)
            if mapped["type"] == "block_reference":
                expanded = self._expand_insert(mapped, depth=0)
                if expanded:
                    self._check_entity_capacity(len(entities) + len(expanded))
                    mapped["geometry"]["expanded_entity_ids"] = [
                        entity["id"] for entity in expanded
                    ]
                    mapped["bbox"] = _union_bbox([entity["bbox"] for entity in expanded])
                    entities.extend(expanded)

        objects = self._parse_objects(sections.get("OBJECTS", []))
        unsupported_reports = self._build_unsupported_reports()
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        status = (
            "partial"
            if unsupported_reports or self._has_warning_or_error()
            else "ok"
        )
        canonical = {
            "schema_version": self.SCHEMA_VERSION,
            "drawing": {
                "id": _drawing_id(source),
                "title": source.get("file_name") or "",
                "source": source,
                "importer": {
                    "name": "DxfImporter",
                    "version": "1.0",
                    "backend": "ascii-group-code",
                    "backend_version": "1",
                },
                "metadata": {
                    "header": {
                        "$ACADVER": header.get("$ACADVER"),
                        "$INSUNITS": header.get("$INSUNITS"),
                    }
                },
            },
            "units": units,
            "coordinate_system": coordinate_system,
            "tolerances": tolerances,
            "extents": _union_bbox([entity["bbox"] for entity in entities]),
            "layers": list(self._layers_by_name.values()),
            "blocks": list(self._blocks_by_name.values()),
            "entities": entities,
            "import_report": {
                "status": status,
                "warnings": self._warnings,
                "unsupported_entities": unsupported_reports,
                "stats": {
                    "raw_entity_count": len(entity_records) + sum(
                        len(block["records"]) for block in block_parse
                    ),
                    "canonical_entity_count": len(entities),
                    "unsupported_entity_count": sum(self._unsupported.values()),
                    "approximated_entity_count": self._approximated_count,
                    "elapsed_ms": elapsed_ms,
                },
            },
            "metadata": {
                "section_names": sorted(sections.keys()),
                "object_counts": objects,
                "stability_limits": {
                    "max_entities": self.max_entities,
                    "max_tokens": self.max_tokens,
                    "timeout_seconds": self.timeout_seconds,
                    "max_block_depth": self.max_block_depth,
                },
            },
        }
        return canonical

    def _reset(self) -> None:
        self._entity_seq = 0
        self._layers_by_name = {}
        self._blocks_by_name = {}
        self._block_entities_by_name = {}
        self._warnings = []
        self._unsupported = Counter()
        self._approximated_count = 0

    def _parse_sections(self, tokens: Sequence[DxfToken]) -> Dict[str, List[DxfToken]]:
        sections: Dict[str, List[DxfToken]] = {}
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token.code == 0 and _norm_type(token.value) == "SECTION":
                section_name = "UNKNOWN"
                if i + 1 < len(tokens) and tokens[i + 1].code == 2:
                    section_name = _norm_type(tokens[i + 1].value)
                    i += 2
                else:
                    i += 1
                body: List[DxfToken] = []
                while i < len(tokens):
                    if tokens[i].code == 0 and _norm_type(tokens[i].value) == "ENDSEC":
                        i += 1
                        break
                    body.append(tokens[i])
                    i += 1
                sections.setdefault(section_name, []).extend(body)
            else:
                i += 1
        return sections

    def _parse_header(self, tokens: Sequence[DxfToken]) -> Dict[str, Any]:
        header: Dict[str, Any] = {}
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token.code != 9:
                i += 1
                continue
            name = token.value.strip()
            i += 1
            values: List[DxfToken] = []
            while i < len(tokens) and tokens[i].code != 9:
                values.append(tokens[i])
                i += 1
            if values:
                header[name] = _parse_group_value(values[0])
        return header

    def _parse_tables(self, tokens: Sequence[DxfToken]) -> Dict[str, Any]:
        layers: Dict[str, Dict[str, Any]] = {}
        records = _split_records(tokens)
        in_layer_table = False
        for record in records:
            record_type = _norm_type(record.type)
            pairs = _groups(record.pairs)
            if record_type == "TABLE":
                in_layer_table = _norm_type(_first(record.pairs, 2) or "") == "LAYER"
                continue
            if record_type == "ENDTAB":
                in_layer_table = False
                continue
            if not in_layer_table or record_type != "LAYER":
                continue
            name = _first(record.pairs, 2) or "0"
            layer = self._make_layer(
                name=name,
                source_handle=_first(record.pairs, 5),
                color=_first(record.pairs, 62),
                linetype=_first(record.pairs, 6),
                lineweight=_nullable_float(pairs, 370),
                flags=_int(pairs, 70, 0),
            )
            layers[_normalize_key(name)] = layer
        return {"layers": layers}

    def _parse_blocks(self, tokens: Sequence[DxfToken]) -> List[Dict[str, Any]]:
        records = _normalize_complex_records(_split_records(tokens))
        blocks: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        for record in records:
            record_type = _norm_type(record.type)
            if record_type == "BLOCK":
                groups = _groups(record.pairs)
                name = _first(record.pairs, 2) or _first(record.pairs, 3) or "*unnamed"
                current = {
                    "name": name,
                    "origin": _point(groups, 10, 20, 30),
                    "records": [],
                }
                continue
            if record_type == "ENDBLK":
                if current is not None:
                    blocks.append(current)
                current = None
                continue
            if current is not None and record_type not in {"VERTEX", "SEQEND", "ATTRIB"}:
                current["records"].append(record)
        return blocks

    def _parse_entities(self, tokens: Sequence[DxfToken]) -> List[DxfRecord]:
        return [
            record
            for record in _normalize_complex_records(_split_records(tokens))
            if _norm_type(record.type) not in {"SEQEND", "VERTEX", "ATTRIB"}
        ]

    def _parse_objects(self, tokens: Sequence[DxfToken]) -> Dict[str, int]:
        counts = Counter(_norm_type(record.type) for record in _split_records(tokens))
        counts.pop("", None)
        return dict(counts)

    def _ensure_layer(
        self,
        name: str,
        attrs: Optional[Dict[str, Any]] = None,
    ) -> str:
        normalized = _normalize_key(name or "0")
        if normalized not in self._layers_by_name:
            self._layers_by_name[normalized] = self._make_layer(name or "0", **(attrs or {}))
        return self._layers_by_name[normalized]["id"]

    def _make_layer(
        self,
        name: str,
        *,
        source_handle: Optional[str] = None,
        color: Optional[Any] = None,
        linetype: Optional[str] = None,
        lineweight: Optional[float] = None,
        flags: int = 0,
    ) -> Dict[str, Any]:
        normalized = _normalize_key(name or "0")
        return {
            "id": _layer_id(name or "0"),
            "name": name or "0",
            "normalized_name": normalized,
            "color": _to_int_or_str(color),
            "linetype": linetype,
            "lineweight": lineweight,
            "visible": not (isinstance(color, str) and color.strip().startswith("-")),
            "locked": bool(flags & 4),
            "frozen": bool(flags & 1),
            "plot": True,
            "source_handle": source_handle,
            "metadata": {},
        }

    def _next_id(self, prefix: str) -> str:
        self._entity_seq += 1
        safe_prefix = prefix.replace("_", "-")
        return f"{safe_prefix}:{self._entity_seq:08d}"

    def _append_entity(self, entities: List[Dict[str, Any]], entity: Dict[str, Any], started: float) -> None:
        self._check_runtime_limits(started, len(entities))
        self._check_entity_capacity(len(entities) + 1)
        entities.append(entity)

    def _check_entity_capacity(self, next_count: int) -> None:
        if self.max_entities and next_count > self.max_entities:
            raise DxfImportLimitError(
                CadLimitCode.ENTITY_LIMIT_EXCEEDED,
                f"DXF entity limit exceeded: {next_count} > {self.max_entities}",
                details={"entity_count": next_count, "max_entities": self.max_entities},
            )

    def _check_runtime_limits(self, started: float, entity_count: int) -> None:
        if self.cancel_callback and self.cancel_callback():
            raise DxfImportLimitError(
                CadLimitCode.IMPORT_CANCELLED,
                "DXF import cancelled.",
                details={"entity_count": entity_count},
            )
        if self.timeout_seconds is not None and (time.perf_counter() - started) > self.timeout_seconds:
            raise DxfImportLimitError(
                CadLimitCode.IMPORT_TIMEOUT,
                f"DXF import timed out after {self.timeout_seconds:.3f}s.",
                details={"entity_count": entity_count, "timeout_seconds": self.timeout_seconds},
            )

    def _expand_insert(self, insert_entity: Dict[str, Any], *, depth: int) -> List[Dict[str, Any]]:
        if not self.expand_blocks:
            return []
        if depth >= self.max_block_depth:
            _append_warning(
                self._warnings,
                code=CadLimitCode.BLOCK_RECURSION_LIMIT,
                severity="warning",
                message=f"Block expansion stopped at max depth {self.max_block_depth}.",
                entity_id=insert_entity.get("id"),
                raw_type="INSERT",
                details={"max_block_depth": self.max_block_depth},
            )
            return []
        geometry = insert_entity.get("geometry", {})
        block_name = _normalize_key(str(geometry.get("block_name") or ""))
        block_entities = self._block_entities_by_name.get(block_name)
        if not block_entities:
            _append_warning(
                self._warnings,
                code="XREF_NOT_RESOLVED",
                severity="warning",
                message=f"Block {geometry.get('block_name')!r} was not found for INSERT.",
                entity_id=insert_entity.get("id"),
                raw_type="INSERT",
            )
            self._unsupported["INSERT:UNRESOLVED_BLOCK"] += 1
            return []
        matrix = geometry.get("matrix") or _identity_matrix()
        parent_layer_id = insert_entity["layer_id"]
        expanded: List[Dict[str, Any]] = []
        for child in block_entities:
            transformed = _transform_entity(
                child,
                matrix,
                self._next_id,
                parent_layer_id=parent_layer_id,
                insert_id=insert_entity["id"],
            )
            if transformed is None:
                continue
            expanded.append(transformed)
        return expanded

    def _build_unsupported_reports(self) -> List[Dict[str, Any]]:
        warning_counts = Counter(
            str(warning.get("raw_type") or "UNKNOWN")
            for warning in self._warnings
            if warning.get("code") in {"UNSUPPORTED_ENTITY", "ENTITY_MAP_FAILED"}
        )
        for raw_type, count in warning_counts.items():
            if self._unsupported[raw_type] < count:
                self._unsupported[raw_type] = count
        for warning in self._warnings:
            if warning.get("code") == "UNSUPPORTED_ENTITY":
                raw_type = str(warning.get("raw_type") or "UNKNOWN")
                self._unsupported[raw_type] += 0
            if warning.get("code") == "ENTITY_APPROXIMATED":
                self._approximated_count += 1
        reports = []
        warning_examples: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
        for warning in self._warnings:
            raw_type = str(warning.get("raw_type") or "")
            if not raw_type:
                continue
            if warning.get("code") in {"UNSUPPORTED_ENTITY", "ENTITY_MAP_FAILED"}:
                warning_examples[raw_type].append(
                    {
                        "handle": warning.get("source_handle"),
                        "layer_name": None,
                        "layout_name": None,
                        "reason": warning.get("message"),
                    }
                )
        for raw_type, count in sorted(self._unsupported.items()):
            examples = warning_examples.get(raw_type, [])[:5]
            reports.append(
                {
                    "raw_type": raw_type,
                    "count": int(count),
                    "policy": "skipped",
                    "impact": "comparison_incomplete",
                    "examples": examples,
                }
            )
        return reports

    def _has_warning_or_error(self) -> bool:
        return any(w.get("severity") in {"warning", "error"} for w in self._warnings)


def _split_records(tokens: Sequence[DxfToken]) -> List[DxfRecord]:
    records: List[DxfRecord] = []
    current: Optional[DxfRecord] = None
    for token in tokens:
        if token.code == 0:
            if current is not None:
                records.append(current)
            current = DxfRecord(type=token.value.strip(), pairs=[])
        elif current is not None:
            current.pairs.append(token)
    if current is not None:
        records.append(current)
    return records


def _normalize_complex_records(records: Sequence[DxfRecord]) -> List[DxfRecord]:
    out: List[DxfRecord] = []
    i = 0
    while i < len(records):
        record = records[i]
        record_type = _norm_type(record.type)
        if record_type in {"POLYLINE", "INSERT"}:
            children: List[DxfRecord] = []
            i += 1
            while i < len(records):
                child_type = _norm_type(records[i].type)
                if child_type == "SEQEND":
                    i += 1
                    break
                if record_type == "POLYLINE" and child_type == "VERTEX":
                    children.append(records[i])
                    i += 1
                    continue
                if record_type == "INSERT" and child_type == "ATTRIB":
                    children.append(records[i])
                    i += 1
                    continue
                break
            record.children = children
            out.append(record)
            continue
        if record_type in {"VERTEX", "ATTRIB", "SEQEND"}:
            i += 1
            continue
        out.append(record)
        i += 1
    return out


def _groups(pairs: Sequence[DxfToken]) -> DefaultDict[int, List[str]]:
    grouped: DefaultDict[int, List[str]] = defaultdict(list)
    for pair in pairs:
        grouped[pair.code].append(pair.value)
    return grouped


def _first(pairs: Sequence[DxfToken], code: int) -> Optional[str]:
    for pair in pairs:
        if pair.code == code:
            return pair.value.strip()
    return None


def _float(groups: Dict[int, List[str]], code: int, default: float) -> float:
    values = groups.get(code)
    if not values:
        return default
    return _to_float(values[0], default=default)


def _nullable_float(groups: Dict[int, List[str]], code: int) -> Optional[float]:
    values = groups.get(code)
    if not values:
        return None
    return _to_float(values[0])


def _int(groups: Dict[int, List[str]], code: int, default: int) -> int:
    values = groups.get(code)
    if not values:
        return default
    try:
        return int(float(values[0].strip()))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    if result == 0:
        return 0.0
    return result


def _point(
    groups: Dict[int, List[str]],
    x_code: int,
    y_code: int,
    z_code: int,
    *,
    default: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Point3:
    return _make_point(
        _float(groups, x_code, default[0]),
        _float(groups, y_code, default[1]),
        _float(groups, z_code, default[2]),
    )


def _make_point(x: float, y: float, z: float) -> Point3:
    return {"x": float(x), "y": float(y), "z": float(z)}


def _bbox_from_points(points: Sequence[Point3], quality: str) -> BBox:
    valid = [p for p in points if p is not None]
    if not valid:
        return {
            "min_x": 0.0,
            "min_y": 0.0,
            "min_z": 0.0,
            "max_x": 0.0,
            "max_y": 0.0,
            "max_z": 0.0,
            "quality": "missing",
        }
    xs = [float(p["x"]) for p in valid]
    ys = [float(p["y"]) for p in valid]
    zs = [float(p.get("z", 0.0)) for p in valid]
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "min_z": min(zs),
        "max_x": max(xs),
        "max_y": max(ys),
        "max_z": max(zs),
        "quality": quality,
    }


def _circle_bbox(center: Point3, radius: float, quality: str) -> BBox:
    return {
        "min_x": center["x"] - radius,
        "min_y": center["y"] - radius,
        "min_z": center.get("z", 0.0),
        "max_x": center["x"] + radius,
        "max_y": center["y"] + radius,
        "max_z": center.get("z", 0.0),
        "quality": quality,
    }


def _arc_bbox(center: Point3, radius: float, start_deg: float, end_deg: float) -> BBox:
    angles = [start_deg, end_deg]
    for cardinal in (0.0, 90.0, 180.0, 270.0):
        if _angle_in_ccw_sweep(cardinal, start_deg, end_deg):
            angles.append(cardinal)
    points = [
        _make_point(
            center["x"] + radius * math.cos(math.radians(angle)),
            center["y"] + radius * math.sin(math.radians(angle)),
            center.get("z", 0.0),
        )
        for angle in angles
    ]
    return _bbox_from_points(points, "exact")


def _angle_in_ccw_sweep(angle: float, start: float, end: float) -> bool:
    angle = _normalize_angle(angle)
    start = _normalize_angle(start)
    end = _normalize_angle(end)
    if start <= end:
        return start <= angle <= end
    return angle >= start or angle <= end


def _text_bbox(insert: Point3, height: float, text: str, quality: str) -> BBox:
    width = max(height, len(text or "") * height * 0.6)
    return {
        "min_x": insert["x"],
        "min_y": insert["y"],
        "min_z": insert.get("z", 0.0),
        "max_x": insert["x"] + width,
        "max_y": insert["y"] + max(height, 1.0),
        "max_z": insert.get("z", 0.0),
        "quality": quality,
    }


def _union_bbox(bboxes: Sequence[Optional[BBox]]) -> BBox:
    valid = [bbox for bbox in bboxes if bbox and bbox.get("quality") != "missing"]
    if not valid:
        return _bbox_from_points([], "missing")
    return {
        "min_x": min(float(b["min_x"]) for b in valid),
        "min_y": min(float(b["min_y"]) for b in valid),
        "min_z": min(float(b.get("min_z", 0.0)) for b in valid),
        "max_x": max(float(b["max_x"]) for b in valid),
        "max_y": max(float(b["max_y"]) for b in valid),
        "max_z": max(float(b.get("max_z", 0.0)) for b in valid),
        "quality": "estimated" if any(b.get("quality") != "exact" for b in valid) else "exact",
    }


def _normalize_angle(value: float) -> float:
    result = value % 360.0
    return 0.0 if result == 0 else result


def _norm_type(value: str) -> str:
    return (value or "").strip().upper()


def _normalize_key(value: str) -> str:
    return unicodedata.normalize("NFC", value or "").strip().upper() or "0"


def _layer_id(name: str) -> str:
    return "layer:" + hashlib.sha1(_normalize_key(name).encode("utf-8")).hexdigest()[:16]


def _block_id(name: str) -> str:
    return "block:" + hashlib.sha1(_normalize_key(name).encode("utf-8")).hexdigest()[:16]


def _drawing_id(source: Dict[str, Any]) -> str:
    source_key = source.get("sha256") or source.get("path") or source.get("file_name") or "memory"
    return "drawing:" + hashlib.sha1(str(source_key).encode("utf-8")).hexdigest()[:16]


def _source_file(
    source_path: str | Path | None,
    file_name: Optional[str],
    header: Dict[str, Any],
) -> Dict[str, Any]:
    path_str = str(source_path) if source_path is not None else None
    name = file_name or (Path(path_str).name if path_str else None)
    sha = None
    if source_path is not None and Path(source_path).exists():
        sha = hashlib.sha256(Path(source_path).read_bytes()).hexdigest()
    source = {
        "format": "dxf",
        "path": path_str,
        "file_name": name,
        "sha256": sha,
        "acad_version": header.get("$ACADVER"),
        "codepage": header.get("$DWGCODEPAGE"),
        "application": header.get("$ACADMAINTVER"),
    }
    return {key: value for key, value in source.items() if value is not None}


def _parse_group_value(token: DxfToken) -> Any:
    if token.code in {60, 62, 66, 70, 71, 72, 73, 74, 90, 91, 92, 93, 94, 95, 280, 281, 370}:
        try:
            return int(float(token.value.strip()))
        except ValueError:
            return token.value.strip()
    if token.code in {
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        30,
        31,
        32,
        33,
        34,
        35,
        36,
        38,
        39,
        40,
        41,
        42,
        43,
        44,
        45,
        46,
        48,
        50,
        51,
        52,
    }:
        return _to_float(token.value)
    return token.value.strip()


def _unit_policy(header: Dict[str, Any]) -> Dict[str, Any]:
    insunits = int(header.get("$INSUNITS") or 0)
    unit_name, scale = _INSUNITS_TO_MM.get(insunits, ("unknown", 1.0))
    return {
        "canonical_unit": "mm",
        "source_unit": unit_name,
        "scale_to_mm": scale,
        "unit_source": "header" if insunits else "importer_default",
    }


_INSUNITS_TO_MM = {
    0: ("unitless", 1.0),
    1: ("inch", 25.4),
    2: ("foot", 304.8),
    3: ("mile", 1609344.0),
    4: ("mm", 1.0),
    5: ("cm", 10.0),
    6: ("m", 1000.0),
    7: ("km", 1000000.0),
    8: ("microinch", 0.0000254),
    9: ("mil", 0.0254),
    10: ("yard", 914.4),
    11: ("angstrom", 0.0000001),
    12: ("nanometer", 0.000001),
    13: ("micron", 0.001),
    14: ("decimeter", 100.0),
    15: ("decameter", 10000.0),
    16: ("hectometer", 100000.0),
    17: ("gigameter", 1000000000000.0),
    18: ("au", 149597870700000.0),
    19: ("light_year", 9.4607e18),
    20: ("parsec", 3.0857e19),
}


def _default_tolerances() -> Dict[str, Any]:
    return {
        "coordinate_quantum_mm": 0.01,
        "bbox_quantum_mm": 0.01,
        "text_anchor_quantum_mm": 0.1,
        "angle_quantum_deg": 0.001,
        "position_match_tolerance_mm": 1.0,
        "structural_position_tolerance_mm": 0.1,
        "text_near_match_radius_mm": 50.0,
        "geometry_hash_version": "geom-hash/v1",
    }


def _style_from_pairs(pairs: Sequence[DxfToken]) -> Dict[str, Any]:
    groups = _groups(pairs)
    return {
        "color": _to_int_or_str(_first(pairs, 62)),
        "lineweight": _nullable_float(groups, 370),
        "linetype": _first(pairs, 6),
        "text_style": _first(pairs, 7),
        "dimension_style": _first(pairs, 3),
    }


def _has_style(style: Dict[str, Any]) -> bool:
    return any(value is not None for value in style.values())


def _to_int_or_str(value: Any) -> int | str | None:
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return str(value)


def _text_alignment(groups: Dict[int, List[str]]) -> str:
    h = _int(groups, 72, 0)
    v = _int(groups, 73, 0)
    return f"{h}:{v}"


def _canonical_text(value: str) -> str:
    text = unicodedata.normalize("NFC", value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _plain_mtext(value: str) -> str:
    text = (value or "").replace("\\P", "\n")
    # Strip common one-level MTEXT formatting wrappers conservatively.
    text = text.replace("{", "").replace("}", "")
    return text


def _dimension_type(flags: int) -> str:
    base = flags & 7
    return {
        0: "linear",
        1: "aligned",
        2: "angular",
        3: "diameter",
        4: "radius",
        5: "angular",
        6: "ordinate",
    }.get(base, "unknown")


def _ellipse_points(
    center: Point3,
    major: Point3,
    ratio: float,
    start: float,
    end: float,
    segments: int = 32,
) -> List[Point3]:
    if end < start:
        end += math.tau
    major_vec = (major["x"], major["y"], major.get("z", 0.0))
    minor_vec = (-major_vec[1] * ratio, major_vec[0] * ratio, major_vec[2] * ratio)
    steps = max(4, segments)
    return [
        _make_point(
            center["x"] + major_vec[0] * math.cos(t) + minor_vec[0] * math.sin(t),
            center["y"] + major_vec[1] * math.cos(t) + minor_vec[1] * math.sin(t),
            center.get("z", 0.0) + major_vec[2] * math.cos(t) + minor_vec[2] * math.sin(t),
        )
        for t in [start + (end - start) * i / steps for i in range(steps + 1)]
    ]


def _matrix_2d(insert: Point3, scale: Point3, rotation_deg: float) -> List[float]:
    theta = math.radians(rotation_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    sx, sy, sz = scale["x"], scale["y"], scale["z"]
    return [
        sx * cos_t,
        -sy * sin_t,
        0.0,
        insert["x"],
        sx * sin_t,
        sy * cos_t,
        0.0,
        insert["y"],
        0.0,
        0.0,
        sz,
        insert.get("z", 0.0),
        0.0,
        0.0,
        0.0,
        1.0,
    ]


def _identity_matrix() -> List[float]:
    return [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    ]


def _transform_point(point: Point3, matrix: Sequence[float]) -> Point3:
    x, y, z = point["x"], point["y"], point.get("z", 0.0)
    return _make_point(
        matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
        matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
        matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
    )


def _transform_entity(
    entity: Dict[str, Any],
    matrix: Sequence[float],
    next_id: Callable[[str], str],
    *,
    parent_layer_id: str,
    insert_id: str,
) -> Optional[Dict[str, Any]]:
    transformed = json.loads(json.dumps(entity, ensure_ascii=False))
    transformed["id"] = next_id(entity["type"])
    transformed["space"] = "model"
    transformed["source"]["path"] = list(transformed["source"].get("path") or []) + [
        f"expanded_from:{insert_id}"
    ]
    if transformed["layer_id"].endswith(hashlib.sha1("0".encode("utf-8")).hexdigest()[:16]):
        transformed["layer_id"] = parent_layer_id
    geom = transformed["geometry"]
    etype = transformed["type"]
    if etype == "line":
        geom["start"] = _transform_point(geom["start"], matrix)
        geom["end"] = _transform_point(geom["end"], matrix)
        bbox = _bbox_from_points([geom["start"], geom["end"]], "exact")
    elif etype == "circle":
        geom["center"] = _transform_point(geom["center"], matrix)
        geom["radius"] = geom["radius"] * _average_xy_scale(matrix)
        bbox = _circle_bbox(geom["center"], geom["radius"], "estimated")
    elif etype == "arc":
        geom["center"] = _transform_point(geom["center"], matrix)
        geom["radius"] = geom["radius"] * _average_xy_scale(matrix)
        geom["start_angle_deg"] = _normalize_angle(geom["start_angle_deg"] + _matrix_rotation(matrix))
        geom["end_angle_deg"] = _normalize_angle(geom["end_angle_deg"] + _matrix_rotation(matrix))
        bbox = _arc_bbox(geom["center"], geom["radius"], geom["start_angle_deg"], geom["end_angle_deg"])
    elif etype == "polyline":
        for vertex in geom["vertices"]:
            vertex["point"] = _transform_point(vertex["point"], matrix)
        bbox = _bbox_from_points([v["point"] for v in geom["vertices"]], "control_points")
    elif etype in {"text", "mtext"}:
        geom["insert"] = _transform_point(geom["insert"], matrix)
        geom["height"] = geom["height"] * _average_xy_scale(matrix)
        geom["rotation_deg"] = _normalize_angle(geom["rotation_deg"] + _matrix_rotation(matrix))
        text = geom.get("text") or geom.get("plain_text") or ""
        bbox = _text_bbox(geom["insert"], geom["height"], text, "estimated")
    elif etype == "hatch":
        for boundary in geom.get("boundaries", []):
            for vertex in boundary.get("vertices", []):
                vertex["point"] = _transform_point(vertex["point"], matrix)
        points = [
            vertex["point"]
            for boundary in geom.get("boundaries", [])
            for vertex in boundary.get("vertices", [])
        ]
        bbox = _bbox_from_points(points, "control_points")
    elif etype == "dimension":
        geom["defpoints"] = [_transform_point(point, matrix) for point in geom.get("defpoints", [])]
        if geom.get("text_midpoint"):
            geom["text_midpoint"] = _transform_point(geom["text_midpoint"], matrix)
        bbox_points = list(geom.get("defpoints", []))
        if geom.get("text_midpoint"):
            bbox_points.append(geom["text_midpoint"])
        bbox = _bbox_from_points(bbox_points, "estimated")
    else:
        return None
    transformed["bbox"] = bbox
    transformed["hashes"]["geometry_hash"] = _hash_payload(
        "geom", _geometry_hash_payload(etype, geom)
    )
    transformed["hashes"]["source_fingerprint"] = _hash_payload("src", transformed["source"])
    transformed["metadata"]["expanded_from_insert_id"] = insert_id
    return transformed


def _average_xy_scale(matrix: Sequence[float]) -> float:
    sx = math.hypot(matrix[0], matrix[4])
    sy = math.hypot(matrix[1], matrix[5])
    return (abs(sx) + abs(sy)) / 2.0 or 1.0


def _matrix_rotation(matrix: Sequence[float]) -> float:
    return math.degrees(math.atan2(matrix[4], matrix[0]))


def _geometry_hash_payload(entity_type: str, geometry: Dict[str, Any]) -> Dict[str, Any]:
    if entity_type == "text":
        return {
            "type": "text",
            "insert": geometry.get("insert"),
            "height": geometry.get("height"),
            "rotation_deg": geometry.get("rotation_deg"),
            "alignment": geometry.get("alignment"),
        }
    if entity_type == "mtext":
        return {
            "type": "mtext",
            "insert": geometry.get("insert"),
            "height": geometry.get("height"),
            "box_width": geometry.get("box_width"),
            "rotation_deg": geometry.get("rotation_deg"),
            "attachment": geometry.get("attachment"),
        }
    if entity_type == "block_reference":
        return {
            "type": "block_reference",
            "block_id": geometry.get("block_id"),
            "block_name": geometry.get("block_name"),
            "insert": geometry.get("insert"),
            "scale": geometry.get("scale"),
            "rotation_deg": geometry.get("rotation_deg"),
            "matrix": geometry.get("matrix"),
        }
    if entity_type == "dimension":
        return {
            "type": "dimension",
            "dimension_type": geometry.get("dimension_type"),
            "defpoints": geometry.get("defpoints"),
            "text_midpoint": geometry.get("text_midpoint"),
        }
    if entity_type == "line":
        start = geometry.get("start")
        end = geometry.get("end")
        ordered = sorted([start, end], key=lambda p: (p["x"], p["y"], p.get("z", 0.0)))
        return {"type": "line", "points": ordered}
    return geometry


def _hash_payload(prefix: str, payload: Any) -> str:
    normalized = _normalize_for_hash(payload)
    data = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:v1:sha256:{hashlib.sha256(data).hexdigest()}"


def _normalize_for_hash(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return 0
        rounded = round(value, 6)
        return 0 if rounded == 0 else rounded
    if isinstance(value, dict):
        return {
            key: _normalize_for_hash(val)
            for key, val in value.items()
            if val is not None
        }
    if isinstance(value, list):
        return [_normalize_for_hash(item) for item in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value


def _append_warning(
    warnings: List[Dict[str, Any]],
    *,
    code: str,
    severity: str,
    message: str,
    entity_id: Optional[str] = None,
    source_handle: Optional[str] = None,
    raw_type: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    warnings.append(
        {
            "code": code,
            "severity": severity,
            "message": message,
            "entity_id": entity_id,
            "source_handle": source_handle,
            "raw_type": raw_type,
            "details": details or {},
        }
    )


__all__ = [
    "DxfImporter",
    "DxfImportLimitError",
    "DxfParseError",
    "DxfTokenizer",
    "DxfToken",
    "DxfEntityMapper",
    "DxfRecord",
]
