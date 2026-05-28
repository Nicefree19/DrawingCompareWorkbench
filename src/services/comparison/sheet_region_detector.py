"""Detect logical drawing/detail regions inside CAD and PDF sheets.

The initial implementation is intentionally conservative: it does not change
the main comparison algorithm.  It produces a side-car summary that later
stages can use to explain why a drawing with several details needs localized
matching instead of one global model-space comparison.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .dxf_read import read_dxf_document_result
from .region_profile import RegionProfile

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class SheetRegion:
    """One detected page, layout, title-frame, or clustered detail area."""

    region_id: str
    source_path: str
    source_format: str
    side: str = ""
    page_index: Optional[int] = None
    layout_name: str = ""
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)
    centroid: tuple[float, float] = (0.0, 0.0)
    width: float = 0.0
    height: float = 0.0
    area: float = 0.0
    entity_count: int = 0
    layer_histogram: dict[str, int] = field(default_factory=dict)
    entity_histogram: dict[str, int] = field(default_factory=dict)
    title_text: str = ""
    drawing_number: str = ""
    title_block_bbox: Optional[BBox] = None
    confidence: float = 0.0
    detection_method: str = "unknown"
    region_kind: str = "detail"
    frame_score: float = 0.0
    identity_evidence: tuple[str, ...] = tuple()
    confidence_reasons: tuple[str, ...] = tuple()
    warnings: tuple[str, ...] = tuple()

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "source_path": self.source_path,
            "source_format": self.source_format,
            "side": self.side,
            "page_index": self.page_index,
            "layout_name": self.layout_name,
            "bbox": list(self.bbox),
            "centroid": list(self.centroid),
            "width": self.width,
            "height": self.height,
            "area": self.area,
            "entity_count": self.entity_count,
            "layer_histogram": dict(self.layer_histogram),
            "entity_histogram": dict(self.entity_histogram),
            "title_text": self.title_text,
            "drawing_number": self.drawing_number,
            "title_block_bbox": list(self.title_block_bbox) if self.title_block_bbox else None,
            "confidence": self.confidence,
            "detection_method": self.detection_method,
            "region_kind": self.region_kind,
            "frame_score": self.frame_score,
            "identity_evidence": list(self.identity_evidence),
            "confidence_reasons": list(self.confidence_reasons),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class RegionDetectionResult:
    """Detection output for one source file."""

    source_path: str
    source_format: str
    side: str = ""
    regions: tuple[SheetRegion, ...] = tuple()
    status: str = "passed"
    warnings: tuple[str, ...] = tuple()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_format": self.source_format,
            "side": self.side,
            "status": self.status,
            "region_count": len(self.regions),
            "regions": [region.to_dict() for region in self.regions],
            "warnings": list(self.warnings),
        }


def detect_sheet_regions(
    source_path: str | Path,
    *,
    side: str = "",
    dxf_cache_dir: str | Path | None = None,
    max_regions: int = 80,
    region_profile: RegionProfile | str | Path | None = None,
) -> RegionDetectionResult:
    """Detect review regions for a PDF, DXF, or DWG source.

    PDF detection maps each page to a region. CAD detection first tries large
    rectangular frames, then falls back to spatial clusters, then to the whole
    modelspace bbox if no better segmentation is available.
    """

    path = Path(source_path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _detect_pdf_regions(path, side=side, max_regions=max_regions)
    if suffix in {".dxf", ".dwg"}:
        return _detect_cad_regions(
            path,
            side=side,
            dxf_cache_dir=dxf_cache_dir,
            max_regions=max_regions,
            region_profile=region_profile,
        )
    return RegionDetectionResult(
        source_path=str(path),
        source_format=suffix.lstrip(".") or "unknown",
        side=side,
        status="unsupported",
        warnings=(f"unsupported source format: {suffix}",),
    )


def write_region_detection_summary(
    results: Sequence[RegionDetectionResult],
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "source_count": len(results),
        "region_count": sum(len(result.regions) for result in results),
        "results": [result.to_dict() for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _detect_pdf_regions(
    path: Path,
    *,
    side: str,
    max_regions: int,
) -> RegionDetectionResult:
    warnings: list[str] = []
    try:
        import fitz
    except ImportError:
        return RegionDetectionResult(
            source_path=str(path),
            source_format="pdf",
            side=side,
            status="fallback",
            regions=(
                _make_region(
                    region_id=f"{side or 'pdf'}-page-1",
                    path=path,
                    source_format="pdf",
                    side=side,
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    entity_count=1,
                    page_index=0,
                    detection_method="pdf_page_fallback",
                    confidence=0.55,
                    warnings=("PyMuPDF unavailable; page size unknown",),
                ),
            ),
            warnings=("PyMuPDF unavailable; PDF regions use fallback page bbox",),
        )

    regions: list[SheetRegion] = []
    try:
        doc = fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001
        return RegionDetectionResult(
            source_path=str(path),
            source_format="pdf",
            side=side,
            status="failed",
            warnings=(f"PDF open failed: {exc}",),
        )
    try:
        for page_index in range(min(len(doc), max_regions)):
            rect = doc[page_index].rect
            bbox = (0.0, 0.0, float(rect.width), float(rect.height))
            regions.append(
                _make_region(
                    region_id=f"{side or 'pdf'}-page-{page_index + 1}",
                    path=path,
                    source_format="pdf",
                    side=side,
                    bbox=bbox,
                    entity_count=1,
                    page_index=page_index,
                    detection_method="pdf_page",
                    confidence=0.95,
                )
            )
        if len(doc) > max_regions:
            warnings.append(f"PDF page regions capped at {max_regions}")
    finally:
        doc.close()
    return RegionDetectionResult(
        source_path=str(path),
        source_format="pdf",
        side=side,
        regions=tuple(regions),
        status="passed" if regions else "empty",
        warnings=tuple(warnings),
    )


def _detect_cad_regions(
    path: Path,
    *,
    side: str,
    dxf_cache_dir: str | Path | None,
    max_regions: int,
    region_profile: RegionProfile | str | Path | None = None,
) -> RegionDetectionResult:
    profile = RegionProfile.load(region_profile)
    try:
        dxf_path = _ensure_dxf_path(path, dxf_cache_dir=dxf_cache_dir)
    except Exception as exc:  # noqa: BLE001
        return RegionDetectionResult(
            source_path=str(path),
            source_format=path.suffix.lower().lstrip(".") or "cad",
            side=side,
            status="failed",
            warnings=(f"DXF conversion failed: {exc}",),
        )

    try:
        read_result = read_dxf_document_result(dxf_path)
    except ImportError:
        return RegionDetectionResult(
            source_path=str(path),
            source_format=path.suffix.lower().lstrip(".") or "cad",
            side=side,
            status="failed",
            warnings=("ezdxf unavailable",),
        )
    except Exception as exc:  # noqa: BLE001
        return RegionDetectionResult(
            source_path=str(path),
            source_format=path.suffix.lower().lstrip(".") or "cad",
            side=side,
            status="failed",
            warnings=(f"DXF read failed: {exc}",),
        )
    doc = read_result.doc

    entities = _collect_entity_signatures(doc.modelspace(), layout_name="Model")
    regions = _regions_from_frames(
        path,
        side,
        entities,
        max_regions=max_regions,
        region_profile=profile,
    )
    if not regions:
        paper_regions: list[SheetRegion] = []
        for layout_name, layout in _paper_space_layouts(doc):
            layout_entities = _collect_entity_signatures(layout, layout_name=layout_name)
            viewport_regions = _regions_from_viewports(
                path,
                side,
                layout_entities,
                layout_name=layout_name,
                start_index=len(paper_regions) + 1,
                max_regions=max_regions - len(paper_regions),
                region_profile=profile,
            )
            paper_regions.extend(viewport_regions)
            if len(paper_regions) >= max_regions:
                break
        regions = paper_regions
    if not regions:
        regions = _regions_from_spatial_clusters(
            path,
            side,
            entities,
            max_regions=max_regions,
            region_profile=profile,
        )
    if not regions and entities:
        bbox = _union_bbox([entity["bbox"] for entity in entities])
        if bbox is not None:
            regions = [
                _region_from_entities(
                    path,
                    side,
                    region_id=f"{side or 'cad'}-whole-1",
                    bbox=bbox,
                    entities=entities,
                    detection_method="whole_modelspace",
                    confidence=0.45,
                    region_profile=profile,
                )
            ]
    warnings: list[str] = []
    read_warning = read_result.diagnostics.warning()
    if read_warning:
        warnings.append(read_warning)
    if not regions:
        warnings.append("no renderable CAD entities found")
    elif len(regions) >= max_regions:
        warnings.append(f"CAD regions capped at {max_regions}")
    return RegionDetectionResult(
        source_path=str(path),
        source_format=path.suffix.lower().lstrip(".") or "cad",
        side=side,
        regions=tuple(regions),
        status="passed" if regions else "empty",
        warnings=tuple(warnings),
    )


def _ensure_dxf_path(path: Path, *, dxf_cache_dir: str | Path | None) -> Path:
    if path.suffix.lower() == ".dxf":
        return path
    if path.suffix.lower() != ".dwg":
        raise ValueError(f"unsupported CAD format: {path.suffix}")
    from .dwg_differ import DwgDiffer

    return DwgDiffer(dxf_cache_dir=dxf_cache_dir)._ensure_dxf(path)  # noqa: SLF001


def _paper_space_layouts(doc: Any) -> list[tuple[str, Any]]:
    layouts: list[tuple[str, Any]] = []
    try:
        iterator = list(doc.layouts)
    except Exception:
        return layouts
    for layout in iterator:
        name = str(getattr(layout, "name", "") or "")
        if name.lower() == "model":
            continue
        try:
            if bool(getattr(layout, "is_modelspace", False)):
                continue
        except Exception:
            pass
        layouts.append((name or f"Layout{len(layouts) + 1}", layout))
    return layouts


def _collect_entity_signatures(layout: Any, *, layout_name: str = "") -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for entity in layout:
        signature = _entity_signature(entity, layout_name=layout_name)
        if signature is None:
            continue
        entities.append(signature)
        if signature["entity_type"].upper() == "INSERT":
            entities.extend(_insert_virtual_signatures(entity, signature, layout_name=layout_name))
    return entities


def _entity_signature(
    entity: Any,
    *,
    layout_name: str = "",
    source: str = "layout",
    block_name: str = "",
    parent_layer: str = "",
) -> Optional[dict[str, Any]]:
    bbox = _entity_bbox(entity)
    if bbox is None:
        return None
    layer = str(getattr(getattr(entity, "dxf", None), "layer", "") or "0")
    if layer == "0" and parent_layer:
        layer = parent_layer
    entity_type = str(entity.dxftype())
    text = _entity_text(entity)
    signature = {
        "bbox": bbox,
        "layer": layer,
        "entity_type": entity_type,
        "text": text,
        "is_frame": _is_closed_rectangular_entity(entity, bbox),
        "layout_name": layout_name,
        "source": source,
        "block_name": block_name,
    }
    if entity_type.upper() == "LINE":
        line_points = _line_endpoints(entity)
        if line_points is not None:
            start, end = line_points
            signature["line_start"] = start
            signature["line_end"] = end
            signature["line_length"] = _distance(start, end)
    if entity_type.upper() == "INSERT":
        try:
            signature["block_name"] = str(getattr(entity.dxf, "name", "") or "")
        except Exception:
            signature["block_name"] = ""
    return signature


def _insert_virtual_signatures(
    entity: Any,
    parent_signature: dict[str, Any],
    *,
    layout_name: str,
    max_virtual_entities: int = 2000,
) -> list[dict[str, Any]]:
    try:
        virtual_entities = entity.virtual_entities()
    except Exception:
        return []
    results: list[dict[str, Any]] = []
    block_name = str(parent_signature.get("block_name") or "")
    parent_layer = str(parent_signature.get("layer") or "")
    for index, virtual in enumerate(virtual_entities):
        if index >= max_virtual_entities:
            logger.info("INSERT block %s virtual entity expansion capped at %s", block_name, max_virtual_entities)
            break
        signature = _entity_signature(
            virtual,
            layout_name=layout_name,
            source="insert_virtual",
            block_name=block_name,
            parent_layer=parent_layer,
        )
        if signature is None:
            continue
        signature["insert_parent_bbox"] = parent_signature.get("bbox")
        results.append(signature)
    return results


def _entity_bbox(entity: Any) -> Optional[BBox]:
    dxftype = str(entity.dxftype()).upper()
    try:
        if dxftype == "LINE":
            start = entity.dxf.start
            end = entity.dxf.end
            return _clean_bbox((start.x, start.y, end.x, end.y))
        if dxftype in {"LWPOLYLINE", "POLYLINE"}:
            points = _polyline_points(entity)
            return _bbox_from_points(points)
        if dxftype in {"CIRCLE", "ARC"}:
            center = entity.dxf.center
            radius = abs(float(entity.dxf.radius))
            return _clean_bbox(
                (center.x - radius, center.y - radius, center.x + radius, center.y + radius)
            )
        if dxftype == "ELLIPSE":
            center = entity.dxf.center
            major = entity.dxf.major_axis
            ratio = abs(float(entity.dxf.ratio or 1.0))
            rx = math.hypot(float(major.x), float(major.y))
            ry = rx * ratio
            return _clean_bbox((center.x - rx, center.y - ry, center.x + rx, center.y + ry))
        if dxftype in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
            insert = entity.dxf.insert
            height = float(getattr(entity.dxf, "height", 1.0) or 1.0)
            text = _entity_text(entity)
            width = max(height, len(text) * height * 0.55)
            return _clean_bbox((insert.x, insert.y, insert.x + width, insert.y + height))
        if dxftype == "INSERT":
            insert = entity.dxf.insert
            return _clean_bbox((insert.x, insert.y, insert.x, insert.y))
        if dxftype == "VIEWPORT":
            center = entity.dxf.center
            width = float(getattr(entity.dxf, "width", 0.0) or 0.0)
            height = float(getattr(entity.dxf, "height", 0.0) or 0.0)
            if width > 0 and height > 0:
                return _clean_bbox(
                    (
                        center.x - width / 2.0,
                        center.y - height / 2.0,
                        center.x + width / 2.0,
                        center.y + height / 2.0,
                    )
                )
        if dxftype == "SOLID":
            points = [getattr(entity.dxf, name) for name in ("vtx0", "vtx1", "vtx2", "vtx3")]
            return _bbox_from_points(points)
    except Exception:
        return None
    return None


def _polyline_points(entity: Any) -> list[Any]:
    try:
        if str(entity.dxftype()).upper() == "LWPOLYLINE":
            return [tuple(point[:2]) for point in entity.get_points()]
        return [vertex.dxf.location for vertex in entity.vertices]
    except Exception:
        return []


def _line_endpoints(entity: Any) -> Optional[tuple[tuple[float, float], tuple[float, float]]]:
    try:
        start = entity.dxf.start
        end = entity.dxf.end
        return ((float(start.x), float(start.y)), (float(end.x), float(end.y)))
    except Exception:
        return None


def _bbox_from_points(points: Iterable[Any]) -> Optional[BBox]:
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        try:
            xs.append(float(point[0] if isinstance(point, tuple) else point.x))
            ys.append(float(point[1] if isinstance(point, tuple) else point.y))
        except Exception:
            continue
    if not xs or not ys:
        return None
    return _clean_bbox((min(xs), min(ys), max(xs), max(ys)))


def _clean_bbox(raw: Sequence[Any]) -> Optional[BBox]:
    try:
        x0, y0, x1, y1 = [float(value) for value in raw[:4]]
    except Exception:
        return None
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
        return None
    left, right = min(x0, x1), max(x0, x1)
    bottom, top = min(y0, y1), max(y0, y1)
    width = right - left
    height = top - bottom
    if width <= 0 and height <= 0:
        pad = 1.0
        return (left - pad, bottom - pad, right + pad, top + pad)
    if width <= 0:
        left -= 0.5
        right += 0.5
    if height <= 0:
        bottom -= 0.5
        top += 0.5
    return (left, bottom, right, top)


def _entity_text(entity: Any) -> str:
    try:
        if str(entity.dxftype()).upper() == "INSERT":
            values: list[str] = []
            for attrib in getattr(entity, "attribs", []) or []:
                tag = str(getattr(attrib.dxf, "tag", "") or "").strip()
                text = str(getattr(attrib.dxf, "text", "") or "").strip()
                if tag and text:
                    values.append(f"{tag}:{text}")
                elif text:
                    values.append(text)
            return " ".join(values)
        if hasattr(entity, "plain_text"):
            return str(entity.plain_text() or "")
        return str(getattr(entity.dxf, "text", "") or "")
    except Exception:
        return ""


def _is_closed_rectangular_entity(entity: Any, bbox: BBox) -> bool:
    dxftype = str(entity.dxftype()).upper()
    if dxftype not in {"LWPOLYLINE", "POLYLINE"}:
        return False
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if width <= 0 or height <= 0:
        return False
    points = _polyline_points(entity)
    try:
        closed = bool(getattr(entity, "closed", False)) or bool(entity.is_closed)
    except Exception:
        closed = False
    if not closed and not _polyline_points_are_nearly_closed(points, bbox):
        return False
    ratio = max(width, height) / max(1.0, min(width, height))
    if not 1.1 <= ratio <= 12.0:
        return False
    return _polyline_points_form_rectangle(points, bbox)


def _polyline_points_are_nearly_closed(points: Sequence[Any], bbox: BBox) -> bool:
    if len(points) < 5:
        return False
    first = _point_xy(points[0])
    last = _point_xy(points[-1])
    if first is None or last is None:
        return False
    diag = math.hypot(bbox[2] - bbox[0], bbox[3] - bbox[1])
    return _distance(first, last) <= max(diag * 0.002, 1e-6)


def _point_xy(point: Any) -> Optional[tuple[float, float]]:
    try:
        return (
            float(point[0] if isinstance(point, tuple) else point.x),
            float(point[1] if isinstance(point, tuple) else point.y),
        )
    except Exception:
        return None


def _polyline_points_form_rectangle(points: Sequence[Any], bbox: BBox) -> bool:
    if len(points) < 4:
        return False
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    height = y1 - y0
    diag = math.hypot(width, height)
    tol = max(diag * 0.002, 1e-6)
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    matched_corners: set[int] = set()
    corner_sequence: list[int] = []
    cleaned: list[tuple[float, float]] = []
    for point in points:
        xy = _point_xy(point)
        if xy is None:
            continue
        px, py = xy
        cleaned.append((px, py))
        if not (
            abs(px - x0) <= tol
            or abs(px - x1) <= tol
            or abs(py - y0) <= tol
            or abs(py - y1) <= tol
        ):
            return False
        for index, corner in enumerate(corners):
            if abs(px - corner[0]) <= tol and abs(py - corner[1]) <= tol:
                matched_corners.add(index)
                if not corner_sequence or corner_sequence[-1] != index:
                    corner_sequence.append(index)
                break
    if len(cleaned) < 4 or len(matched_corners) < 4:
        return False
    return _corner_sequence_is_rectangular(corner_sequence)


def _corner_sequence_is_rectangular(corner_sequence: Sequence[int]) -> bool:
    sequence = list(corner_sequence)
    if len(sequence) >= 2 and sequence[0] == sequence[-1]:
        sequence.pop()
    if len(sequence) < 4 or set(sequence) != {0, 1, 2, 3}:
        return False
    deltas = [
        (sequence[(index + 1) % len(sequence)] - sequence[index]) % 4
        for index in range(len(sequence))
    ]
    return all(delta == 1 for delta in deltas) or all(delta == 3 for delta in deltas)


@dataclass(frozen=True)
class _FrameCandidate:
    bbox: BBox
    detection_method: str
    confidence: float
    reasons: tuple[str, ...] = tuple()


def _score_frame_candidate(
    entity: dict[str, Any],
    *,
    whole_area: float,
    region_profile: RegionProfile,
    base_confidence: float = 0.82,
) -> Optional[_FrameCandidate]:
    if not entity.get("is_frame"):
        return None
    bbox = entity["bbox"]
    area = _bbox_area(bbox)
    if whole_area > 0 and area < whole_area * 0.01:
        return None
    confidence = base_confidence
    reasons = ["closed rectangular polyline"]
    if region_profile.matches_frame_layer(str(entity.get("layer") or "")):
        confidence = min(0.95, confidence + 0.04)
        reasons.append("frame layer profile match")
    if _has_table_keywords([entity], region_profile=region_profile):
        confidence = max(0.0, confidence - 0.15)
        reasons.append("table/title keyword penalty")
    return _FrameCandidate(
        bbox=bbox,
        detection_method="cad_frame",
        confidence=confidence,
        reasons=tuple(reasons),
    )


def _regions_from_frames(
    path: Path,
    side: str,
    entities: Sequence[dict[str, Any]],
    *,
    max_regions: int,
    region_profile: RegionProfile,
) -> list[SheetRegion]:
    if not entities:
        return []
    whole = _union_bbox([entity["bbox"] for entity in entities])
    if whole is None:
        return []
    whole_area = _bbox_area(whole)
    candidates: list[_FrameCandidate] = []
    for entity in entities:
        candidate = _score_frame_candidate(
            entity,
            whole_area=whole_area,
            region_profile=region_profile,
        )
        if candidate is not None:
            candidates.append(candidate)
    candidates.extend(_line_frame_candidates(entities, whole, region_profile=region_profile))
    regions: list[SheetRegion] = []
    seen: list[BBox] = []
    for candidate in sorted(candidates, key=lambda item: _bbox_area(item.bbox), reverse=True):
        bbox = candidate.bbox
        if any(_bbox_iou(bbox, other) > 0.85 for other in seen):
            continue
        inside = [entity for entity in entities if _bbox_contains(bbox, _bbox_center(entity["bbox"]))]
        if len(inside) < 3:
            continue
        if _is_probable_table_region(
            bbox,
            inside,
            whole_area=whole_area,
            region_profile=region_profile,
        ):
            continue
        seen.append(bbox)
        regions.append(
            _region_from_entities(
                path,
                side,
                region_id=f"{side or 'cad'}-frame-{len(regions) + 1}",
                bbox=bbox,
                entities=inside,
                detection_method=candidate.detection_method,
                confidence=candidate.confidence,
                region_kind="detail_frame",
                frame_score=candidate.confidence,
                confidence_reasons=candidate.reasons,
                region_profile=region_profile,
            )
        )
        if len(regions) >= max_regions:
            break
    return regions


def _regions_from_viewports(
    path: Path,
    side: str,
    entities: Sequence[dict[str, Any]],
    *,
    layout_name: str,
    start_index: int,
    max_regions: int,
    region_profile: RegionProfile,
) -> list[SheetRegion]:
    if max_regions <= 0:
        return []
    viewport_entities = [
        entity
        for entity in entities
        if str(entity.get("entity_type") or "").upper() == "VIEWPORT"
        and _bbox_area(entity["bbox"]) > 0
    ]
    if not viewport_entities:
        return []
    whole = _union_bbox([entity["bbox"] for entity in entities])
    whole_area = _bbox_area(whole) if whole else 0.0
    regions: list[SheetRegion] = []
    seen: list[BBox] = []
    for entity in sorted(viewport_entities, key=lambda item: _bbox_area(item["bbox"]), reverse=True):
        bbox = entity["bbox"]
        if any(_bbox_iou(bbox, other) > 0.85 for other in seen):
            continue
        inside = [candidate for candidate in entities if _bbox_contains(bbox, _bbox_center(candidate["bbox"]))]
        if _is_probable_table_region(
            bbox,
            inside,
            whole_area=max(whole_area, _bbox_area(bbox)),
            region_profile=region_profile,
        ):
            continue
        seen.append(bbox)
        regions.append(
            _region_from_entities(
                path,
                side,
                region_id=f"{side or 'cad'}-viewport-{start_index + len(regions)}",
                bbox=bbox,
                entities=inside or [entity],
                detection_method="viewport_frame",
                confidence=0.86,
                region_kind="layout_viewport",
                frame_score=0.86,
                confidence_reasons=(f"paperspace viewport in {layout_name}",),
                layout_name=layout_name,
                region_profile=region_profile,
            )
        )
        if len(regions) >= max_regions:
            break
    return regions


def _line_frame_candidates(
    entities: Sequence[dict[str, Any]],
    whole: BBox,
    *,
    region_profile: RegionProfile,
) -> list[_FrameCandidate]:
    """Recover rectangular frames drawn as four or more LINE entities."""

    diag = math.hypot(whole[2] - whole[0], whole[3] - whole[1])
    coord_tol = max(diag * 0.0005, 2.0)
    min_side = max(diag * 0.01, 10.0)
    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    for entity in entities:
        start = entity.get("line_start")
        end = entity.get("line_end")
        if not start or not end:
            continue
        x0, y0 = start
        x1, y1 = end
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        length = math.hypot(dx, dy)
        if length < min_side:
            continue
        if dy <= coord_tol and dx > min_side:
            horizontal.append(((y0 + y1) / 2.0, min(x0, x1), max(x0, x1)))
        elif dx <= coord_tol and dy > min_side:
            vertical.append(((x0 + x1) / 2.0, min(y0, y1), max(y0, y1)))
    h_groups = _merge_axis_segments(horizontal, coord_tol)
    v_groups = _merge_axis_segments(vertical, coord_tol)
    if len(h_groups) > 160 or len(v_groups) > 160:
        logger.info(
            "Skipping LINE frame recovery: too many axis groups h=%s v=%s",
            len(h_groups),
            len(v_groups),
        )
        return []

    candidates: list[_FrameCandidate] = []
    seen: list[BBox] = []
    for bottom_index, bottom in enumerate(h_groups):
        for top in h_groups[bottom_index + 1 :]:
            y0, y1 = sorted((bottom[0], top[0]))
            height = y1 - y0
            if height < min_side:
                continue
            for bottom_interval in bottom[1]:
                for top_interval in top[1]:
                    x0 = max(bottom_interval[0], top_interval[0])
                    x1 = min(bottom_interval[1], top_interval[1])
                    width = x1 - x0
                    if width < min_side:
                        continue
                    left_coverage = _axis_coverage_at(v_groups, x0, y0, y1, coord_tol)
                    right_coverage = _axis_coverage_at(v_groups, x1, y0, y1, coord_tol)
                    if left_coverage < 0.90 or right_coverage < 0.90:
                        continue
                    bbox = (x0, y0, x1, y1)
                    ratio = max(width, height) / max(1.0, min(width, height))
                    if ratio > 20.0:
                        continue
                    if any(_bbox_iou(bbox, other) > 0.85 for other in seen):
                        continue
                    candidate_entities = [
                        entity
                        for entity in entities
                        if _bbox_contains(bbox, _bbox_center(entity["bbox"]))
                    ]
                    confidence = 0.78
                    reasons = ["assembled from LINE border segments"]
                    virtual_entities = [
                        entity
                        for entity in candidate_entities
                        if str(entity.get("source") or "") == "insert_virtual"
                    ]
                    if virtual_entities:
                        reasons.append("expanded from INSERT block virtual entities")
                        block_names = sorted(
                            {
                                str(entity.get("block_name") or "").strip()
                                for entity in virtual_entities
                                if str(entity.get("block_name") or "").strip()
                            }
                        )
                        if block_names:
                            reasons.append(f"insert block {', '.join(block_names[:3])}")
                    if any(
                        region_profile.matches_frame_layer(str(entity.get("layer") or ""))
                        for entity in candidate_entities
                    ):
                        confidence = min(0.95, confidence + 0.04)
                        reasons.append("frame layer profile match")
                    seen.append(bbox)
                    candidates.append(
                        _FrameCandidate(
                            bbox=bbox,
                            detection_method="cad_line_frame",
                            confidence=confidence,
                            reasons=tuple(reasons),
                        )
                    )
    return candidates


def _merge_axis_segments(
    segments: Sequence[tuple[float, float, float]],
    coord_tol: float,
) -> list[tuple[float, list[tuple[float, float]]]]:
    if not segments:
        return []
    groups: list[tuple[float, list[tuple[float, float]]]] = []
    for coord, start, end in sorted(segments, key=lambda item: item[0]):
        if groups and abs(groups[-1][0] - coord) <= coord_tol:
            old_coord, intervals = groups[-1]
            merged_coord = (old_coord * len(intervals) + coord) / (len(intervals) + 1)
            intervals.append((start, end))
            groups[-1] = (merged_coord, _merge_intervals(intervals, coord_tol))
        else:
            groups.append((coord, [(start, end)]))
    return groups


def _merge_intervals(
    intervals: Sequence[tuple[float, float]],
    gap_tol: float,
) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted((min(a, b), max(a, b)) for a, b in intervals):
        if not merged or start > merged[-1][1] + gap_tol:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _axis_coverage_at(
    groups: Sequence[tuple[float, list[tuple[float, float]]]],
    coord: float,
    start: float,
    end: float,
    coord_tol: float,
) -> float:
    intervals: list[tuple[float, float]] = []
    for group_coord, group_intervals in groups:
        if abs(group_coord - coord) <= coord_tol:
            intervals.extend(group_intervals)
    return _coverage_ratio(intervals, start, end, coord_tol)


def _coverage_ratio(
    intervals: Sequence[tuple[float, float]],
    start: float,
    end: float,
    gap_tol: float,
) -> float:
    target_start, target_end = min(start, end), max(start, end)
    target_len = target_end - target_start
    if target_len <= 0:
        return 0.0
    clipped = [
        (max(target_start, a), min(target_end, b))
        for a, b in intervals
        if min(target_end, b) > max(target_start, a)
    ]
    merged = _merge_intervals(clipped, gap_tol)
    covered = sum(max(0.0, b - a) for a, b in merged)
    return min(1.0, covered / target_len)


def _is_probable_table_region(
    bbox: BBox,
    entities: Sequence[dict[str, Any]],
    *,
    whole_area: float,
    region_profile: RegionProfile | None = None,
) -> bool:
    return bool(
        _table_rejection_reasons(
            bbox,
            entities,
            whole_area=whole_area,
            region_profile=region_profile,
        )
    )


def _table_rejection_reasons(
    bbox: BBox,
    entities: Sequence[dict[str, Any]],
    *,
    whole_area: float,
    region_profile: RegionProfile | None = None,
) -> tuple[str, ...]:
    if not entities:
        return tuple()
    profile = region_profile or RegionProfile.default()
    area = _bbox_area(bbox)
    text_count = sum(1 for entity in entities if str(entity.get("text") or "").strip())
    line_like_count = sum(
        1
        for entity in entities
        if str(entity.get("entity_type") or "").upper() in {"LINE", "LWPOLYLINE", "POLYLINE"}
    )
    structural_count = sum(
        1
        for entity in entities
        if _looks_structural_entity(entity, region_profile=profile)
    )
    keyword_hit = _has_table_keywords(entities, region_profile=profile)
    small_relative = whole_area > 0 and area <= whole_area * 0.08
    text_heavy = text_count >= max(4, int(len(entities) * 0.35))
    grid_like = line_like_count >= 6 and structural_count <= max(2, int(len(entities) * 0.20))
    structural_dominant = (
        structural_count >= max(10, int(len(entities) * 0.35))
        and not text_heavy
        and not grid_like
    )
    if structural_dominant:
        return tuple()
    reasons: list[str] = []
    if keyword_hit:
        reasons.append("table keyword")
    if small_relative:
        reasons.append("small relative area")
    if text_heavy:
        reasons.append("text heavy")
    if grid_like:
        reasons.append("grid-like table")
    if keyword_hit and (small_relative or text_heavy or grid_like):
        return tuple(reasons)
    if small_relative and text_heavy and grid_like:
        return tuple(reasons)
    return tuple()


def _looks_structural_entity(
    entity: dict[str, Any],
    *,
    region_profile: RegionProfile | None = None,
) -> bool:
    profile = region_profile or RegionProfile.default()
    layer = str(entity.get("layer") or "").upper()
    entity_type = str(entity.get("entity_type") or "").upper()
    if entity_type in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
        return False
    if (
        profile.matches_title_layer(layer)
        or profile.matches_table_layer(layer)
        or profile.contains_nonstructural_token(layer)
    ):
        return False
    if profile.contains_structural_token(layer):
        return True
    return entity_type in {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE"}


def _has_table_keywords(
    entities: Sequence[dict[str, Any]],
    *,
    region_profile: RegionProfile | None = None,
) -> bool:
    profile = region_profile or RegionProfile.default()
    values: list[str] = []
    for entity in entities:
        layer = str(entity.get("layer") or "")
        block_name = str(entity.get("block_name") or "")
        values.append(layer)
        values.append(str(entity.get("text") or ""))
        values.append(block_name)
        if (
            profile.matches_table_layer(layer)
            or profile.matches_title_layer(layer)
            or profile.matches_table_layer(block_name)
        ):
            return True
    combined = " ".join(values).upper()
    return any(keyword.upper() in combined for keyword in profile.table_reject_keywords)


def _regions_from_spatial_clusters(
    path: Path,
    side: str,
    entities: Sequence[dict[str, Any]],
    *,
    max_regions: int,
    region_profile: RegionProfile,
) -> list[SheetRegion]:
    if not entities:
        return []
    whole = _union_bbox([entity["bbox"] for entity in entities])
    if whole is None:
        return []
    diag = math.hypot(whole[2] - whole[0], whole[3] - whole[1])
    gap = max(diag * 0.035, 100.0)
    buckets, diagnostics = _spatial_cluster_buckets(entities, gap=gap)

    min_entities = max(3, min(12, len(entities) // 60))
    cluster_items: list[tuple[BBox, list[dict[str, Any]]]] = []
    for bucket in buckets.values():
        bbox = _union_bbox([entity["bbox"] for entity in bucket])
        if bbox is None or len(bucket) < min_entities:
            continue
        if _bbox_area(bbox) <= 0:
            continue
        cluster_items.append((bbox, bucket))
    cluster_items.sort(key=lambda item: (item[0][1], item[0][0]))
    if diagnostics["capped_entity_count"] or len(cluster_items) > max_regions:
        logger.info(
            "CAD spatial grid clustering diagnostics: entities=%s cells=%s buckets=%s candidates=%s capped_entities=%s max_regions=%s",
            len(entities),
            diagnostics["cell_count"],
            len(buckets),
            len(cluster_items),
            diagnostics["capped_entity_count"],
            max_regions,
        )
    regions: list[SheetRegion] = []
    for bbox, bucket in cluster_items[:max_regions]:
        if _is_probable_table_region(
            bbox,
            bucket,
            whole_area=_bbox_area(whole),
            region_profile=region_profile,
        ):
            continue
        regions.append(
            _region_from_entities(
                path,
                side,
                region_id=f"{side or 'cad'}-cluster-{len(regions) + 1}",
                bbox=bbox,
                entities=bucket,
                detection_method="cad_spatial_cluster",
                confidence=0.68,
                confidence_reasons=("grid spatial clustering",),
                region_profile=region_profile,
            )
        )
    return regions


def _spatial_cluster_buckets(
    entities: Sequence[dict[str, Any]],
    *,
    gap: float,
    max_cells_per_entity: int = 64,
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, int]]:
    parent = list(range(len(entities)))
    cell_representatives: dict[tuple[int, int], int] = {}
    capped_entity_count = 0
    cell_size = max(float(gap), 1.0)

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for index, entity in enumerate(entities):
        expanded = _expand_bbox(entity["bbox"], gap)
        cells = _grid_cells_for_bbox(
            expanded,
            cell_size=cell_size,
            max_cells=max_cells_per_entity,
        )
        if cells is None:
            capped_entity_count += 1
            center = _bbox_center(expanded)
            cells = (
                (
                    math.floor(center[0] / cell_size),
                    math.floor(center[1] / cell_size),
                ),
            )
        for cell in cells:
            representative = cell_representatives.get(cell)
            if representative is None:
                cell_representatives[cell] = index
            else:
                union(index, representative)

    buckets: dict[int, list[dict[str, Any]]] = {}
    for index, entity in enumerate(entities):
        buckets.setdefault(find(index), []).append(entity)
    return buckets, {
        "cell_count": len(cell_representatives),
        "capped_entity_count": capped_entity_count,
    }


def _grid_cells_for_bbox(
    bbox: BBox,
    *,
    cell_size: float,
    max_cells: int,
) -> Optional[tuple[tuple[int, int], ...]]:
    x0 = math.floor(bbox[0] / cell_size)
    y0 = math.floor(bbox[1] / cell_size)
    x1 = math.floor(bbox[2] / cell_size)
    y1 = math.floor(bbox[3] / cell_size)
    cell_count = (x1 - x0 + 1) * (y1 - y0 + 1)
    if cell_count > max_cells:
        return None
    return tuple((x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


@dataclass(frozen=True)
class _RegionIdentity:
    title_text: str = ""
    drawing_number: str = ""
    title_block_bbox: Optional[BBox] = None
    evidence: tuple[str, ...] = tuple()


def _region_from_entities(
    path: Path,
    side: str,
    *,
    region_id: str,
    bbox: BBox,
    entities: Sequence[dict[str, Any]],
    detection_method: str,
    confidence: float,
    region_kind: str = "detail",
    frame_score: float = 0.0,
    confidence_reasons: Sequence[str] = (),
    layout_name: str = "",
    region_profile: RegionProfile | None = None,
) -> SheetRegion:
    layer_hist = Counter(str(entity.get("layer") or "0") for entity in entities)
    entity_hist = Counter(str(entity.get("entity_type") or "") for entity in entities)
    profile = region_profile or RegionProfile.default()
    identity = _extract_region_identity(
        bbox,
        entities,
        region_profile=profile,
    )
    adjusted_confidence = confidence
    if not identity.title_text:
        adjusted_confidence = max(0.0, confidence - 0.05)
    merged_reasons = tuple(confidence_reasons) + identity.evidence
    return _make_region(
        region_id=region_id,
        path=path,
        source_format=path.suffix.lower().lstrip(".") or "cad",
        side=side,
        bbox=bbox,
        entity_count=len(entities),
        layer_histogram=dict(layer_hist.most_common(12)),
        entity_histogram=dict(entity_hist.most_common(12)),
        title_text=identity.title_text,
        drawing_number=identity.drawing_number,
        title_block_bbox=identity.title_block_bbox,
        detection_method=detection_method,
        confidence=adjusted_confidence,
        region_kind=region_kind,
        frame_score=frame_score,
        identity_evidence=identity.evidence,
        confidence_reasons=merged_reasons,
        layout_name=layout_name,
    )


def _extract_region_identity(
    bbox: BBox,
    entities: Sequence[dict[str, Any]],
    *,
    region_profile: RegionProfile,
) -> _RegionIdentity:
    text_entities = [
        entity
        for entity in entities
        if str(entity.get("text") or "").strip()
        and str(entity.get("entity_type") or "").upper() in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "INSERT"}
    ]
    if not text_entities:
        return _RegionIdentity(evidence=("no title text found",))

    title_bboxes = _title_area_bboxes(bbox, region_profile.title_area_policy)
    preferred = [
        entity
        for entity in text_entities
        if any(_bbox_contains(title_bbox, _bbox_center(entity["bbox"])) for title_bbox in title_bboxes)
    ]
    evidence = [f"title area policy: {region_profile.title_area_policy}"]
    source_entities = preferred or text_entities
    if preferred:
        evidence.append("title text from title area")
        title_bbox = _union_bbox([entity["bbox"] for entity in preferred])
    else:
        evidence.append("title area empty; used all region text")
        title_bbox = None

    source_texts = [
        str(entity.get("text") or "").strip()
        for entity in source_entities
        if str(entity.get("text") or "").strip()
    ]
    all_texts = [
        str(entity.get("text") or "").strip()
        for entity in text_entities
        if str(entity.get("text") or "").strip()
    ]
    drawing_number = _extract_drawing_number(source_texts, region_profile=region_profile)
    if drawing_number:
        evidence.append("drawing number from title area" if preferred else "drawing number from region text")
    elif preferred:
        drawing_number = _extract_drawing_number(all_texts, region_profile=region_profile)
        if drawing_number:
            evidence.append("drawing number fallback to full region text")

    title_text = " | ".join(source_texts[:8])[:240]
    return _RegionIdentity(
        title_text=title_text,
        drawing_number=drawing_number,
        title_block_bbox=title_bbox,
        evidence=tuple(evidence),
    )


def _title_area_bboxes(bbox: BBox, policy: str) -> tuple[BBox, ...]:
    x0, y0, x1, y1 = bbox
    width = max(0.0, x1 - x0)
    height = max(0.0, y1 - y0)
    if width <= 0 or height <= 0:
        return tuple()
    normalized = str(policy or "").lower()
    bottom = (x0, y0, x1, y0 + height * 0.25)
    right = (x1 - width * 0.35, y0, x1, y1)
    top = (x0, y1 - height * 0.25, x1, y1)
    if normalized == "right_title_band":
        return (right,)
    if normalized == "bottom_title_band":
        return (bottom,)
    if normalized == "top_title_band":
        return (top,)
    if normalized == "bottom_or_right_title_band":
        return (bottom, right)
    return tuple()


def _make_region(
    *,
    region_id: str,
    path: Path,
    source_format: str,
    side: str,
    bbox: BBox,
    entity_count: int,
    page_index: Optional[int] = None,
    layout_name: str = "",
    layer_histogram: Optional[dict[str, int]] = None,
    entity_histogram: Optional[dict[str, int]] = None,
    title_text: str = "",
    drawing_number: str = "",
    title_block_bbox: Optional[BBox] = None,
    detection_method: str,
    confidence: float,
    region_kind: str = "detail",
    frame_score: float = 0.0,
    identity_evidence: Sequence[str] = (),
    confidence_reasons: Sequence[str] = (),
    warnings: Sequence[str] = (),
) -> SheetRegion:
    x0, y0, x1, y1 = bbox
    width = max(0.0, x1 - x0)
    height = max(0.0, y1 - y0)
    return SheetRegion(
        region_id=region_id,
        source_path=str(path),
        source_format=source_format,
        side=side,
        page_index=page_index,
        layout_name=layout_name,
        bbox=bbox,
        centroid=((x0 + x1) / 2.0, (y0 + y1) / 2.0),
        width=width,
        height=height,
        area=width * height,
        entity_count=entity_count,
        layer_histogram=layer_histogram or {},
        entity_histogram=entity_histogram or {},
        title_text=title_text,
        drawing_number=drawing_number,
        title_block_bbox=title_block_bbox,
        detection_method=detection_method,
        confidence=confidence,
        region_kind=region_kind,
        frame_score=frame_score,
        identity_evidence=tuple(identity_evidence),
        confidence_reasons=tuple(confidence_reasons),
        warnings=tuple(warnings),
    )


def _extract_drawing_number(
    texts: Sequence[str],
    *,
    region_profile: RegionProfile | None = None,
) -> str:
    profile = region_profile or RegionProfile.default()
    patterns = []
    for raw_pattern in profile.drawing_number_patterns:
        try:
            patterns.append(re.compile(raw_pattern, re.I))
        except re.error as exc:
            logger.warning(
                "Skipping invalid drawing number pattern in region profile %s: %s (%s)",
                profile.name,
                raw_pattern,
                exc,
            )
    for text in texts:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return _normalize_drawing_number(match.group(0))
    return ""


def _normalize_drawing_number(value: str) -> str:
    normalized = re.sub(r"[\s._]+", "-", str(value or "").strip().upper())
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized.strip("-")


def _union_bbox(boxes: Iterable[BBox]) -> Optional[BBox]:
    values = list(boxes)
    if not values:
        return None
    return (
        min(box[0] for box in values),
        min(box[1] for box in values),
        max(box[2] for box in values),
        max(box[3] for box in values),
    )


def _bbox_area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_center(bbox: BBox) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _bbox_contains(bbox: BBox, point: tuple[float, float]) -> bool:
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def _bbox_intersects(left: BBox, right: BBox) -> bool:
    return not (
        left[2] < right[0]
        or right[2] < left[0]
        or left[3] < right[1]
        or right[3] < left[1]
    )


def _bbox_iou(left: BBox, right: BBox) -> float:
    ix0 = max(left[0], right[0])
    iy0 = max(left[1], right[1])
    ix1 = min(left[2], right[2])
    iy1 = min(left[3], right[3])
    inter = _bbox_area((ix0, iy0, ix1, iy1))
    if inter <= 0:
        return 0.0
    union = _bbox_area(left) + _bbox_area(right) - inter
    return inter / union if union > 0 else 0.0


def _expand_bbox(bbox: BBox, amount: float) -> BBox:
    return (bbox[0] - amount, bbox[1] - amount, bbox[2] + amount, bbox[3] + amount)
