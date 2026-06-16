"""ODA-free DWG import boundary for CanonicalDrawing.

This module does not parse proprietary DWG sections directly.  It provides the
stable boundary we need before adopting any permissively licensed reader:

* Detect DWG version from the first 6 bytes.
* Reject unsupported, corrupted, encrypted, unavailable, or license-forbidden
  inputs with explicit error codes.
* Map an adapter-provided 2D DWG model into CanonicalDrawing.

Adapters can be injected directly or selected through the DWG backend boundary.
The bundled fixture adapter is intentionally small and exists for tests/samples;
production adapters must pass the same contract.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .cad_stability import CadLimitCode, CancelCallback
from .dxf_importer import (
    _arc_bbox,
    _bbox_from_points,
    _block_id,
    _circle_bbox,
    _default_tolerances,
    _dimension_type,
    _ellipse_points,
    _drawing_id,
    _geometry_hash_payload,
    _hash_payload,
    _layer_id,
    _make_point,
    _normalize_angle,
    _normalize_key,
    _plain_mtext,
    _text_bbox,
    _union_bbox,
    _unit_policy,
)


Point3 = Dict[str, float]
BBox = Dict[str, float | str]


class DwgFailureCode:
    """Stable DWG import failure/error codes."""

    CORRUPTED = "DWG_CORRUPTED"
    ENCRYPTED = "DWG_ENCRYPTED"
    UNSUPPORTED_VERSION = "DWG_UNSUPPORTED_VERSION"
    ADAPTER_UNAVAILABLE = "DWG_ADAPTER_UNAVAILABLE"
    ADAPTER_FAILED = "DWG_ADAPTER_FAILED"
    FORBIDDEN_LICENSE = "DWG_FORBIDDEN_LICENSE"
    NO_READABLE_ENTITIES = "DWG_NO_READABLE_ENTITIES"
    UNSUPPORTED_ENTITY = "DWG_UNSUPPORTED_ENTITY"
    IMPORT_TIMEOUT = "DWG_IMPORT_TIMEOUT"
    IMPORT_CANCELLED = "DWG_IMPORT_CANCELLED"
    ENTITY_LIMIT_EXCEEDED = "DWG_ENTITY_LIMIT_EXCEEDED"


class DwgImportError(RuntimeError):
    """Raised by DWG adapters when import cannot continue."""

    def __init__(self, code: str, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class DwgVersionInfo:
    code: str
    family: str
    release: str
    supported: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "family": self.family,
            "release": self.release,
            "supported": self.supported,
        }


class DwgVersionDetector:
    """Detect DWG version from the first six bytes."""

    SUPPORTED_CODES = {
        "AC1015": ("AutoCAD 2000", "AutoCAD 2000/2000i/2002"),
    }

    KNOWN_UNSUPPORTED_CODES = {
        "AC1009": ("AutoCAD R12", "AutoCAD R12"),
        "AC1012": ("AutoCAD R13", "AutoCAD R13"),
        "AC1014": ("AutoCAD R14", "AutoCAD R14"),
        "AC1018": ("AutoCAD 2004", "AutoCAD 2004/2005/2006"),
        "AC1021": ("AutoCAD 2007", "AutoCAD 2007/2008/2009"),
        "AC1024": ("AutoCAD 2010", "AutoCAD 2010/2011/2012"),
        "AC1027": ("AutoCAD 2013", "AutoCAD 2013/2014/2015/2016/2017"),
        "AC1032": ("AutoCAD 2018", "AutoCAD 2018+"),
    }

    @classmethod
    def detect_file(cls, path: str | Path) -> DwgVersionInfo:
        data = Path(path).read_bytes()[:6]
        return cls.detect_bytes(data)

    @classmethod
    def detect_bytes(cls, data: bytes) -> DwgVersionInfo:
        if len(data) < 6:
            raise DwgImportError(
                DwgFailureCode.CORRUPTED,
                "DWG header is shorter than six bytes.",
                details={"header_length": len(data)},
            )
        try:
            code = data[:6].decode("ascii")
        except UnicodeDecodeError as exc:
            raise DwgImportError(
                DwgFailureCode.CORRUPTED,
                "DWG header is not ASCII.",
                details={"header_hex": data[:6].hex()},
            ) from exc
        if code in cls.SUPPORTED_CODES:
            family, release = cls.SUPPORTED_CODES[code]
            return DwgVersionInfo(code=code, family=family, release=release, supported=True)
        if code in cls.KNOWN_UNSUPPORTED_CODES:
            family, release = cls.KNOWN_UNSUPPORTED_CODES[code]
            return DwgVersionInfo(code=code, family=family, release=release, supported=False)
        if code.startswith("AC") and code[2:].isdigit():
            return DwgVersionInfo(code=code, family="Unknown AutoCAD DWG", release="unsupported", supported=False)
        raise DwgImportError(
            DwgFailureCode.CORRUPTED,
            "DWG header does not contain an ACAD version signature.",
            details={"header": code, "header_hex": data[:6].hex()},
        )


@dataclass
class DwgAdapterEntity:
    raw_type: str
    geometry: Dict[str, Any]
    layer: str = "0"
    handle: Optional[str] = None
    owner_handle: Optional[str] = None
    style: Dict[str, Any] = field(default_factory=dict)
    layout_name: Optional[str] = None
    attributes: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DwgAdapterBlock:
    name: str
    origin: Point3 = field(default_factory=lambda: _make_point(0.0, 0.0, 0.0))
    entities: List[DwgAdapterEntity] = field(default_factory=list)
    is_external_reference: bool = False
    source_path: Optional[str] = None


@dataclass
class DwgAdapterDrawing:
    header: Dict[str, Any] = field(default_factory=dict)
    layers: List[Dict[str, Any]] = field(default_factory=list)
    blocks: List[DwgAdapterBlock] = field(default_factory=list)
    model_space: List[DwgAdapterEntity] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class DwgImporterAdapter:
    """Adapter boundary for permissively licensed DWG readers."""

    name = "base"
    version = "0"
    license_id = "MIT"
    backend_mode = "custom_adapter"
    implementation_status = "custom"
    approval_required = False

    def is_available(self) -> bool:
        return False

    def supports_version(self, version: DwgVersionInfo) -> bool:
        """Return whether this adapter can decode the detected DWG version."""

        return bool(version.supported)

    def read_file(self, path: str | Path, version: DwgVersionInfo) -> DwgAdapterDrawing:
        raise DwgImportError(
            DwgFailureCode.ADAPTER_UNAVAILABLE,
            f"DWG adapter {self.name!r} is not available.",
        )


class DwgJsonFixtureAdapter(DwgImporterAdapter):
    """MIT-safe sample adapter for tests and contract fixtures.

    Files still start with a real DWG six-byte version code, followed by the
    marker ``CANONICAL_DWG_FIXTURE_V1`` and a JSON adapter payload.
    """

    name = "json-fixture"
    version = "1"
    license_id = "MIT"
    backend_mode = "json_fixture"
    implementation_status = "fixture"
    approval_required = False
    MARKER = b"\nCANONICAL_DWG_FIXTURE_V1\n"

    def is_available(self) -> bool:
        return True

    def read_file(self, path: str | Path, version: DwgVersionInfo) -> DwgAdapterDrawing:
        data = Path(path).read_bytes()
        if self.MARKER not in data:
            raise DwgImportError(
                DwgFailureCode.ADAPTER_UNAVAILABLE,
                "Fixture adapter can only read CANONICAL_DWG_FIXTURE_V1 samples.",
            )
        payload_bytes = data.split(self.MARKER, 1)[1]
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DwgImportError(
                DwgFailureCode.CORRUPTED,
                "Fixture DWG payload is not valid JSON.",
            ) from exc
        if payload.get("encrypted"):
            raise DwgImportError(DwgFailureCode.ENCRYPTED, "DWG file is encrypted.")
        return _adapter_drawing_from_dict(payload)


class DwgImporter:
    """Import DWG files into CanonicalDrawing through an injected adapter."""

    SCHEMA_VERSION = "canonical-drawing/v1"
    DEFAULT_ALLOWED_LICENSES = ("MIT", "INTERNAL")

    def __init__(
        self,
        adapter: Optional[DwgImporterAdapter] = None,
        *,
        backend_mode: Optional[str] = None,
        allowed_license_ids: Sequence[str] = DEFAULT_ALLOWED_LICENSES,
        max_entities: int = 0,
        timeout_seconds: Optional[float] = None,
        cancel_callback: Optional[CancelCallback] = None,
    ):
        backend_selection = None
        if adapter is None:
            from .dwg_backend import create_dwg_backend_selection

            backend_selection = create_dwg_backend_selection(backend_mode)
            adapter = backend_selection.adapter
        self.adapter = adapter
        self.backend_selection = backend_selection
        self.allowed_license_ids = tuple(allowed_license_ids)
        self.max_entities = max(0, int(max_entities or 0))
        self.timeout_seconds = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        self.cancel_callback = cancel_callback
        self.detector = DwgVersionDetector()
        self._entity_seq = 0
        self._layers_by_name: Dict[str, Dict[str, Any]] = {}
        self._warnings: List[Dict[str, Any]] = []
        self._unsupported: Dict[str, int] = {}

    def import_file(self, path: str | Path) -> Dict[str, Any]:
        start = time.perf_counter()
        path = Path(path)
        try:
            version = self.detector.detect_file(path)
            if not self.adapter.supports_version(version):
                raise DwgImportError(
                    DwgFailureCode.UNSUPPORTED_VERSION,
                    f"DWG version {version.code} is not supported by adapter {self.adapter.name!r}.",
                    details={
                        "dwg_version": version.to_dict(),
                        "adapter": self.adapter.name,
                        "adapter_version": self.adapter.version,
                        "backend_mode": getattr(self.adapter, "backend_mode", None),
                        "implementation_status": getattr(self.adapter, "implementation_status", None),
                    },
                )
            if self.adapter.license_id not in self.allowed_license_ids:
                raise DwgImportError(
                    DwgFailureCode.FORBIDDEN_LICENSE,
                    f"DWG adapter license {self.adapter.license_id!r} is not allowed.",
                    details={
                        "adapter": self.adapter.name,
                        "license_id": self.adapter.license_id,
                        "backend_mode": getattr(self.adapter, "backend_mode", None),
                        "allowed_license_ids": list(self.allowed_license_ids),
                    },
                )
            if not self.adapter.is_available():
                raise DwgImportError(
                    DwgFailureCode.ADAPTER_UNAVAILABLE,
                    f"DWG adapter {self.adapter.name!r} is not available.",
                )
            adapter_drawing = self.adapter.read_file(path, version)
            return self.import_adapter_drawing(
                adapter_drawing,
                version=version,
                source_path=path,
                elapsed_ms=(time.perf_counter() - start) * 1000.0,
            )
        except DwgImportError as exc:
            version_info = None
            try:
                version_info = self.detector.detect_file(path).to_dict()
            except DwgImportError:
                version_info = None
            return self._failed_document(
                source_path=path,
                version_info=version_info,
                error_code=exc.code,
                message=str(exc),
                details=exc.details,
                elapsed_ms=(time.perf_counter() - start) * 1000.0,
            )

    def import_adapter_drawing(
        self,
        drawing: DwgAdapterDrawing,
        *,
        version: DwgVersionInfo,
        source_path: str | Path | None = None,
        file_name: Optional[str] = None,
        elapsed_ms: float = 0.0,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        self._reset()
        self._check_runtime_limits(started, 0)
        source = self._source_file(source_path, file_name, version)
        header = dict(drawing.header or {})
        header.setdefault("$ACADVER", version.code)
        self._ensure_layer("0", None)
        for layer in drawing.layers:
            self._ensure_layer(str(layer.get("name") or "0"), layer)

        entities: List[Dict[str, Any]] = []
        blocks: List[Dict[str, Any]] = []
        for block in drawing.blocks:
            block_id = _block_id(block.name)
            block_entities = []
            for adapter_entity in block.entities:
                self._check_runtime_limits(started, len(entities))
                mapped = self._map_entity(
                    adapter_entity,
                    space="block",
                    block_id=block_id,
                    block_name=block.name,
                    source_path=["BLOCKS", block.name],
                )
                if mapped:
                    block_entities.append(mapped)
                    self._append_entity(entities, mapped, started)
            blocks.append(
                {
                    "id": block_id,
                    "name": block.name,
                    "normalized_name": _normalize_key(block.name),
                    "origin": block.origin,
                    "entity_ids": [entity["id"] for entity in block_entities],
                    "bbox": _union_bbox([entity["bbox"] for entity in block_entities]),
                    "is_external_reference": bool(block.is_external_reference),
                    "source_path": block.source_path,
                    "metadata": {},
                }
            )

        for adapter_entity in drawing.model_space:
            self._check_runtime_limits(started, len(entities))
            mapped = self._map_entity(
                adapter_entity,
                space="model",
                block_id=None,
                block_name=None,
                source_path=["MODEL_SPACE"],
            )
            if mapped:
                self._append_entity(entities, mapped, started)

        unsupported_reports = self._unsupported_reports()
        status = "partial" if self._warnings or unsupported_reports else "ok"
        canonical = {
            "schema_version": self.SCHEMA_VERSION,
            "drawing": {
                "id": _drawing_id(source),
                "title": source.get("file_name") or "",
                "source": source,
                "importer": {
                    "name": "DwgImporter",
                    "version": "1.0",
                    "backend": self.adapter.name,
                    "backend_version": self.adapter.version,
                },
                "metadata": {
                    "header": {
                        "$ACADVER": header.get("$ACADVER"),
                        "$INSUNITS": header.get("$INSUNITS"),
                    }
                },
            },
            "units": _unit_policy(header),
            "coordinate_system": {
                "space": "WCS",
                "axis_order": ["x", "y", "z"],
                "origin": _make_point(0.0, 0.0, 0.0),
                "z_policy": "preserve",
                "layout_policy": "modelspace",
            },
            "tolerances": _default_tolerances(),
            "extents": _union_bbox([entity["bbox"] for entity in entities]),
            "layers": list(self._layers_by_name.values()),
            "blocks": blocks,
            "entities": entities,
            "import_report": {
                "status": status,
                "warnings": self._warnings,
                "unsupported_entities": unsupported_reports,
                "error_code": None,
                "dwg_version": version.to_dict(),
                "adapter": self._adapter_report(),
                "stats": {
                    "raw_entity_count": len(drawing.model_space)
                    + sum(len(block.entities) for block in drawing.blocks),
                    "canonical_entity_count": len(entities),
                    "unsupported_entity_count": sum(self._unsupported.values()),
                    "approximated_entity_count": 0,
                    "elapsed_ms": elapsed_ms,
                },
            },
            "metadata": {
                "adapter_metadata": drawing.metadata,
                "dwg_version": version.to_dict(),
                "stability_limits": {
                    "max_entities": self.max_entities,
                    "timeout_seconds": self.timeout_seconds,
                },
            },
        }
        return canonical

    def _map_entity(
        self,
        adapter_entity: DwgAdapterEntity,
        *,
        space: str,
        block_id: Optional[str],
        block_name: Optional[str],
        source_path: List[str],
    ) -> Optional[Dict[str, Any]]:
        raw_type = (adapter_entity.raw_type or "").strip().upper()
        geometry = adapter_entity.geometry or {}
        try:
            if raw_type == "LINE":
                start = _point(geometry.get("start"))
                end = _point(geometry.get("end"))
                mapped_geometry = {"type": "line", "start": start, "end": end}
                return self._common(adapter_entity, "line", mapped_geometry, _bbox_from_points([start, end], "exact"), space, block_id, block_name, source_path)
            if raw_type == "CIRCLE":
                center = _point(geometry.get("center"))
                radius = max(0.0, float(geometry.get("radius") or 0.0))
                mapped_geometry = {
                    "type": "circle",
                    "center": center,
                    "radius": radius,
                    "normal": _point(geometry.get("normal"), default=(0.0, 0.0, 1.0)),
                }
                return self._common(adapter_entity, "circle", mapped_geometry, _circle_bbox(center, radius, "exact"), space, block_id, block_name, source_path)
            if raw_type == "ARC":
                center = _point(geometry.get("center"))
                radius = max(0.0, float(geometry.get("radius") or 0.0))
                start_angle = _normalize_angle(float(geometry.get("start_angle_deg") or 0.0))
                end_angle = _normalize_angle(float(geometry.get("end_angle_deg") or 0.0))
                mapped_geometry = {
                    "type": "arc",
                    "center": center,
                    "radius": radius,
                    "start_angle_deg": start_angle,
                    "end_angle_deg": end_angle,
                    "normal": _point(geometry.get("normal"), default=(0.0, 0.0, 1.0)),
                    "sweep_direction": "ccw",
                }
                return self._common(adapter_entity, "arc", mapped_geometry, _arc_bbox(center, radius, start_angle, end_angle), space, block_id, block_name, source_path)
            if raw_type == "ELLIPSE":
                # Tessellate to a canonical polyline, exactly like the DXF importer,
                # so the ellipse renders + diffs as a curve (source raw_type kept).
                center = _point(geometry.get("center"))
                major = _point(geometry.get("major_axis"))
                ratio = float(geometry.get("ratio") or 1.0)
                start = float(geometry.get("start_param") or 0.0)
                end = geometry.get("end_param")
                end = float(end) if end is not None else math.tau
                points = _ellipse_points(center, major, ratio, start, end)
                mapped_geometry = {
                    "type": "polyline",
                    "vertices": [{"point": pt, "bulge": 0.0} for pt in points],
                    "closed": abs((end - start) - math.tau) < 1e-6,
                    "polyline_kind": "2d_polyline",
                }
                return self._common(adapter_entity, "polyline", mapped_geometry, _bbox_from_points(points, "control_points"), space, block_id, block_name, source_path)
            if raw_type in {"LWPOLYLINE", "POLYLINE"}:
                vertices = [_vertex(item) for item in geometry.get("vertices") or []]
                mapped_geometry = {
                    "type": "polyline",
                    "vertices": vertices,
                    "closed": bool(geometry.get("closed")),
                    "polyline_kind": "lwpolyline" if raw_type == "LWPOLYLINE" else "2d_polyline",
                }
                return self._common(adapter_entity, "polyline", mapped_geometry, _bbox_from_points([v["point"] for v in vertices], "control_points"), space, block_id, block_name, source_path)
            if raw_type == "TEXT":
                raw_text = str(geometry.get("text") or "")
                canonical_text = _canonical_text(raw_text)
                insert = _point(geometry.get("insert"))
                height = float(geometry.get("height") or 0.0)
                mapped_geometry = {
                    "type": "text",
                    "insert": insert,
                    "text": raw_text,
                    "canonical_text": canonical_text,
                    "height": height,
                    "rotation_deg": _normalize_angle(float(geometry.get("rotation_deg") or 0.0)),
                    "alignment": str(geometry.get("alignment") or "0:0"),
                }
                return self._common(adapter_entity, "text", mapped_geometry, _text_bbox(insert, height, raw_text, "estimated"), space, block_id, block_name, source_path, semantic_payload={"text": canonical_text})
            if raw_type == "MTEXT":
                raw_content = str(geometry.get("raw_content") or geometry.get("plain_text") or geometry.get("text") or "")
                plain_text = _plain_mtext(raw_content)
                canonical_text = _canonical_text(plain_text)
                insert = _point(geometry.get("insert"))
                height = float(geometry.get("height") or 0.0)
                mapped_geometry = {
                    "type": "mtext",
                    "insert": insert,
                    "plain_text": plain_text,
                    "canonical_text": canonical_text,
                    "raw_content": raw_content,
                    "height": height,
                    "box_width": geometry.get("box_width"),
                    "rotation_deg": _normalize_angle(float(geometry.get("rotation_deg") or 0.0)),
                }
                return self._common(adapter_entity, "mtext", mapped_geometry, _text_bbox(insert, height, plain_text, "estimated"), space, block_id, block_name, source_path, semantic_payload={"text": canonical_text})
            if raw_type == "INSERT":
                insert = _point(geometry.get("insert"))
                scale = _point(geometry.get("scale"), default=(1.0, 1.0, 1.0))
                rotation = _normalize_angle(float(geometry.get("rotation_deg") or 0.0))
                target_block_name = str(geometry.get("block_name") or "")
                mapped_geometry = {
                    "type": "block_reference",
                    "block_id": _block_id(target_block_name),
                    "block_name": target_block_name,
                    "insert": insert,
                    "scale": scale,
                    "rotation_deg": rotation,
                    "matrix": _matrix_2d(insert, scale, rotation),
                    "attributes": [_attribute(attr) for attr in adapter_entity.attributes or geometry.get("attributes") or []],
                    "expanded_entity_ids": [],
                }
                return self._common(adapter_entity, "block_reference", mapped_geometry, _bbox_from_points([insert], "estimated"), space, block_id, block_name, source_path, semantic_payload={"attributes": mapped_geometry["attributes"]})
            if raw_type == "POINT":
                location = _point(geometry.get("location"))
                mapped_geometry = {"type": "point", "location": location}
                return self._common(adapter_entity, "point", mapped_geometry, _bbox_from_points([location], "exact"), space, block_id, block_name, source_path)
            if raw_type == "DIMENSION":
                # Mirror the DXF importer's canonical dimension shape so the diff /
                # structural analysers read the same fields regardless of source.
                text_midpoint = _point(geometry.get("text_midpoint"))
                dim_type = _dimension_type(int(geometry.get("dimtype") or 0))
                text_override = str(geometry.get("text") or "") or None
                canonical_text = _canonical_text(text_override) if text_override else None
                measurement = geometry.get("measurement")
                mapped_geometry = {
                    "type": "dimension",
                    "dimension_type": dim_type,
                    "measurement": float(measurement) if measurement is not None else None,
                    "text_override": text_override,
                    "canonical_text": canonical_text,
                    "defpoints": [],  # subtype def points are not decoded by the native reader
                    "text_midpoint": text_midpoint,
                }
                return self._common(adapter_entity, "dimension", mapped_geometry, _bbox_from_points([text_midpoint], "estimated"), space, block_id, block_name, source_path, semantic_payload={"dimension_type": dim_type, "measurement": mapped_geometry["measurement"], "canonical_text": canonical_text})
            if raw_type == "HATCH":
                pattern_name = str(geometry.get("pattern") or "SOLID").upper()
                box = geometry.get("bbox") or {}
                corners = [
                    _make_point(float(box.get("min_x") or 0.0), float(box.get("min_y") or 0.0), 0.0),
                    _make_point(float(box.get("max_x") or 0.0), float(box.get("max_y") or 0.0), 0.0),
                ]
                mapped_geometry = {
                    "type": "hatch",
                    "pattern_name": pattern_name,
                    "solid_fill": bool(geometry.get("solid")),
                    "pattern_scale": None,
                    "pattern_angle_deg": None,
                    "boundaries": [],  # boundary vertices are summarised to a bbox by the native reader
                    "is_gradient": bool(geometry.get("is_gradient")),
                    "gradient_name": str(geometry.get("gradient_name") or ""),
                }
                return self._common(adapter_entity, "hatch", mapped_geometry, _bbox_from_points(corners, "control_points"), space, block_id, block_name, source_path, semantic_payload={"pattern_name": pattern_name, "pattern_scale": None, "pattern_angle_deg": None})
        except Exception as exc:
            self._warning(
                "DWG_ENTITY_MAP_FAILED",
                "warning",
                f"Failed to map DWG entity {raw_type}: {exc}",
                raw_type=raw_type,
                source_handle=adapter_entity.handle,
            )
            return None

        self._unsupported[raw_type or "UNKNOWN"] = self._unsupported.get(raw_type or "UNKNOWN", 0) + 1
        self._warning(
            DwgFailureCode.UNSUPPORTED_ENTITY,
            "warning",
            f"Unsupported DWG entity {raw_type or 'UNKNOWN'} was skipped.",
            raw_type=raw_type or "UNKNOWN",
            source_handle=adapter_entity.handle,
        )
        return None

    def _common(
        self,
        adapter_entity: DwgAdapterEntity,
        canonical_type: str,
        geometry: Dict[str, Any],
        bbox: BBox,
        space: str,
        block_id: Optional[str],
        block_name: Optional[str],
        source_path: List[str],
        *,
        semantic_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        layer_name = adapter_entity.layer or "0"
        layer_id = self._ensure_layer(layer_name, None)
        style = {
            "color": adapter_entity.style.get("color"),
            "lineweight": adapter_entity.style.get("lineweight"),
            "linetype": adapter_entity.style.get("linetype"),
            "text_style": adapter_entity.style.get("text_style"),
            "dimension_style": adapter_entity.style.get("dimension_style"),
        }
        source = {
            "format": "dwg",
            "raw_type": adapter_entity.raw_type.strip().upper(),
            "handle": adapter_entity.handle,
            "owner_handle": adapter_entity.owner_handle,
            "layout_name": adapter_entity.layout_name,
            "block_name": block_name,
            "path": list(source_path),
        }
        entity = {
            "id": self._next_id(canonical_type),
            "type": canonical_type,
            "source": source,
            "layer_id": layer_id,
            "block_id": block_id,
            "space": space,
            "layout_name": adapter_entity.layout_name or ("Model" if space == "model" else None),
            "geometry": geometry,
            "bbox": bbox,
            "style": style,
            "visible": True,
            "metadata": {},
        }
        entity["hashes"] = {
            "geometry_hash": _hash_payload("geom", _geometry_hash_payload(canonical_type, geometry)),
            "semantic_hash": _hash_payload("sem", semantic_payload) if semantic_payload else None,
            "style_hash": _hash_payload("style", style) if any(value is not None for value in style.values()) else None,
            "source_fingerprint": _hash_payload("src", source),
        }
        return entity

    def _source_file(
        self,
        source_path: str | Path | None,
        file_name: Optional[str],
        version: DwgVersionInfo,
    ) -> Dict[str, Any]:
        path_str = str(source_path) if source_path is not None else None
        name = file_name or (Path(path_str).name if path_str else None)
        sha = None
        if source_path is not None and Path(source_path).exists():
            sha = hashlib.sha256(Path(source_path).read_bytes()).hexdigest()
        return {
            key: value
            for key, value in {
                "format": "dwg",
                "path": path_str,
                "file_name": name,
                "sha256": sha,
                "acad_version": version.code,
                "application": "DwgImporterAdapter",
            }.items()
            if value is not None
        }

    def _adapter_report(self) -> Dict[str, Any]:
        report = {
            "name": self.adapter.name,
            "version": self.adapter.version,
            "license_id": self.adapter.license_id,
            "backend_mode": getattr(self.adapter, "backend_mode", self.adapter.name),
            "implementation_status": getattr(self.adapter, "implementation_status", "custom"),
            "approval_required": bool(getattr(self.adapter, "approval_required", False)),
        }
        if self.backend_selection is not None:
            report["selection"] = self.backend_selection.to_dict()
        diagnostics = getattr(self.adapter, "diagnostics", None)
        if callable(diagnostics):
            try:
                report["diagnostics"] = diagnostics()
            except Exception as exc:
                report["diagnostics_error"] = f"{type(exc).__name__}: {exc}"
        return report

    def _failed_document(
        self,
        *,
        source_path: Path,
        version_info: Optional[Dict[str, Any]],
        error_code: str,
        message: str,
        details: Optional[Dict[str, Any]],
        elapsed_ms: float,
    ) -> Dict[str, Any]:
        fallback_version = DwgVersionInfo(
            code=(version_info or {}).get("code") or "UNKNOWN",
            family=(version_info or {}).get("family") or "unknown",
            release=(version_info or {}).get("release") or "unknown",
            supported=bool((version_info or {}).get("supported")),
        )
        source = self._source_file(source_path, None, fallback_version)
        warning = {
            "code": error_code,
            "severity": "error",
            "message": message,
            "details": details or {},
        }
        return {
            "schema_version": self.SCHEMA_VERSION,
            "drawing": {
                "id": _drawing_id(source),
                "title": source.get("file_name") or "",
                "source": source,
                "importer": {
                    "name": "DwgImporter",
                    "version": "1.0",
                    "backend": self.adapter.name,
                    "backend_version": self.adapter.version,
                },
                "metadata": {"header": {"$ACADVER": source.get("acad_version")}},
            },
            "units": {
                "canonical_unit": "mm",
                "source_unit": "unknown",
                "scale_to_mm": 1.0,
                "unit_source": "importer_default",
            },
            "coordinate_system": {
                "space": "WCS",
                "axis_order": ["x", "y", "z"],
                "origin": _make_point(0.0, 0.0, 0.0),
                "z_policy": "preserve",
                "layout_policy": "modelspace",
            },
            "tolerances": _default_tolerances(),
            "extents": _bbox_from_points([], "missing"),
            "layers": [self._make_layer("0")],
            "blocks": [],
            "entities": [],
            "import_report": {
                "status": "failed",
                "warnings": [warning],
                "unsupported_entities": [],
                "error_code": error_code,
                "dwg_version": version_info,
                "adapter": self._adapter_report(),
                "stats": {
                    "raw_entity_count": 0,
                    "canonical_entity_count": 0,
                    "unsupported_entity_count": 0,
                    "approximated_entity_count": 0,
                    "elapsed_ms": elapsed_ms,
                },
            },
            "metadata": {
                "dwg_import_failure_code": error_code,
                "dwg_version": version_info,
            },
        }

    def _ensure_layer(self, name: str, attrs: Optional[Dict[str, Any]]) -> str:
        normalized = _normalize_key(name or "0")
        if normalized not in self._layers_by_name:
            self._layers_by_name[normalized] = self._make_layer(name or "0", attrs or {})
        return self._layers_by_name[normalized]["id"]

    def _make_layer(self, name: str, attrs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        attrs = attrs or {}
        color = attrs.get("color")
        return {
            "id": _layer_id(name or "0"),
            "name": name or "0",
            "normalized_name": _normalize_key(name or "0"),
            "color": color,
            "linetype": attrs.get("linetype"),
            "lineweight": attrs.get("lineweight"),
            "visible": not (isinstance(color, str) and color.startswith("-")),
            "locked": bool(attrs.get("locked", False)),
            "frozen": bool(attrs.get("frozen", False)),
            "plot": bool(attrs.get("plot", True)),
            "source_handle": attrs.get("source_handle"),
            "metadata": {},
        }

    def _warning(
        self,
        code: str,
        severity: str,
        message: str,
        *,
        raw_type: Optional[str] = None,
        source_handle: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._warnings.append(
            {
                "code": code,
                "severity": severity,
                "message": message,
                "raw_type": raw_type,
                "source_handle": source_handle,
                "details": details or {},
            }
        )

    def _unsupported_reports(self) -> List[Dict[str, Any]]:
        return [
            {
                "raw_type": raw_type,
                "count": count,
                "policy": "skipped",
                "impact": "comparison_incomplete",
                "examples": [],
            }
            for raw_type, count in sorted(self._unsupported.items())
        ]

    def _next_id(self, prefix: str) -> str:
        self._entity_seq += 1
        return f"{prefix.replace('_', '-')}:{self._entity_seq:08d}"

    def _append_entity(self, entities: List[Dict[str, Any]], entity: Dict[str, Any], started: float) -> None:
        self._check_runtime_limits(started, len(entities))
        next_count = len(entities) + 1
        if self.max_entities and next_count > self.max_entities:
            raise DwgImportError(
                DwgFailureCode.ENTITY_LIMIT_EXCEEDED,
                f"DWG entity limit exceeded: {next_count} > {self.max_entities}",
                details={"entity_count": next_count, "max_entities": self.max_entities},
            )
        entities.append(entity)

    def _check_runtime_limits(self, started: float, entity_count: int) -> None:
        if self.cancel_callback and self.cancel_callback():
            raise DwgImportError(
                DwgFailureCode.IMPORT_CANCELLED,
                "DWG import cancelled.",
                details={"entity_count": entity_count, "limit_code": CadLimitCode.IMPORT_CANCELLED},
            )
        if self.timeout_seconds is not None and (time.perf_counter() - started) > self.timeout_seconds:
            raise DwgImportError(
                DwgFailureCode.IMPORT_TIMEOUT,
                f"DWG import timed out after {self.timeout_seconds:.3f}s.",
                details={
                    "entity_count": entity_count,
                    "timeout_seconds": self.timeout_seconds,
                    "limit_code": CadLimitCode.IMPORT_TIMEOUT,
                },
            )

    def _reset(self) -> None:
        self._entity_seq = 0
        self._layers_by_name = {}
        self._warnings = []
        self._unsupported = {}


def _adapter_drawing_from_dict(payload: Dict[str, Any]) -> DwgAdapterDrawing:
    return DwgAdapterDrawing(
        header=dict(payload.get("header") or {}),
        layers=[dict(layer) for layer in payload.get("layers") or []],
        blocks=[
            DwgAdapterBlock(
                name=str(block.get("name") or ""),
                origin=_point(block.get("origin")),
                entities=[_adapter_entity_from_dict(item) for item in block.get("entities") or []],
                is_external_reference=bool(block.get("is_external_reference")),
                source_path=block.get("source_path"),
            )
            for block in payload.get("blocks") or []
        ],
        model_space=[_adapter_entity_from_dict(item) for item in payload.get("model_space") or payload.get("entities") or []],
        metadata=dict(payload.get("metadata") or {}),
    )


def _adapter_entity_from_dict(payload: Dict[str, Any]) -> DwgAdapterEntity:
    return DwgAdapterEntity(
        raw_type=str(payload.get("type") or payload.get("raw_type") or ""),
        geometry=dict(payload.get("geometry") or {}),
        layer=str(payload.get("layer") or "0"),
        handle=payload.get("handle"),
        owner_handle=payload.get("owner_handle"),
        style=dict(payload.get("style") or {}),
        layout_name=payload.get("layout_name"),
        attributes=[dict(item) for item in payload.get("attributes") or []],
    )


def _point(value: Any, *, default: Tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Point3:
    if isinstance(value, dict):
        return _make_point(
            float(value.get("x", default[0]) or 0.0),
            float(value.get("y", default[1]) or 0.0),
            float(value.get("z", default[2]) or 0.0),
        )
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _make_point(
            float(value[0]),
            float(value[1]),
            float(value[2]) if len(value) > 2 else default[2],
        )
    return _make_point(*default)


def _vertex(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        point = _point(value.get("point", value))
        return {
            "point": point,
            "bulge": float(value.get("bulge") or 0.0),
            "start_width": value.get("start_width"),
            "end_width": value.get("end_width"),
        }
    return {"point": _point(value), "bulge": 0.0, "start_width": None, "end_width": None}


def _attribute(value: Dict[str, Any]) -> Dict[str, Any]:
    raw_text = str(value.get("text") or value.get("canonical_text") or "")
    return {
        "tag": str(value.get("tag") or ""),
        "text": raw_text,
        "canonical_text": _canonical_text(raw_text),
        "insert": _point(value.get("insert")),
        "source_handle": value.get("source_handle"),
    }


def _canonical_text(value: str) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


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
