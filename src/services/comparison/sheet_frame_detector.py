# -*- coding: utf-8 -*-
"""Detect a single outer drawing frame (도곽) bbox for sheet-frame viewer alignment.

This is the **producer** side of the sheet-frame alignment feature. The consumer
(:mod:`sheet_frame_alignment`) and the viewer-package propagation already know how
to map both before/after panes through a shared sheet-local window *once a frame
bbox is present in the compare artifacts*. Until this module existed, nothing
wrote that frame bbox, so the viewer always fell back to world-union framing.

Design goals:
- **Conservative**: return ``None`` whenever a confident outer 도곽 cannot be
  found, so the caller keeps the existing world-union camera frame. Never guess.
- **Cheap**: scan only polyline entities (the usual 도곽 carrier) instead of
  signaturing the whole modelspace (which expands INSERT virtual entities and is
  expensive on large drawings).
- **Reuse**: borrow the rectangle/closed-polyline geometry tests already proven in
  :mod:`sheet_region_detector` rather than re-deriving them.

The "outer frame" is the largest closed rectangular polyline whose bbox covers a
large fraction of the drawing extents. That is almost always the drawing border /
title-block frame. Detail sub-frames cover only a small fraction and are rejected
by the coverage gate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

from .dxf_read import read_dxf_document_result
from .sheet_region_detector import (
    _bbox_area,
    _ensure_dxf_path,
    _is_closed_rectangular_entity,
    _entity_bbox,
)

logger = logging.getLogger(__name__)

BBox = Tuple[float, float, float, float]

# A closed rectangle must cover at least this fraction of the drawing extents to
# be trusted as the outer sheet 도곽 (smaller rectangles are detail sub-frames).
DEFAULT_MIN_COVERAGE = 0.5
# Frame area can never exceed the extents (extents are the union of all entities),
# but allow a tiny epsilon for floating point before treating a value as invalid.
_MAX_COVERAGE = 1.0 + 1e-6


@dataclass(frozen=True)
class SheetFrameResult:
    """A detected outer drawing-frame bbox with provenance for the manifest."""

    bbox: BBox
    method: str
    confidence: float
    coverage_ratio: float
    layer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "bbox": [float(v) for v in self.bbox],
            "method": self.method,
            "confidence": self.confidence,
            "coverage_ratio": self.coverage_ratio,
            "layer": self.layer,
        }


def _frame_layer_bonus(layer: str) -> float:
    """Small confidence boost when the rectangle sits on a border/title layer.

    Kept dependency-free (no RegionProfile load) and case-insensitive. Matching is
    a *bonus*, never a requirement — many drawings keep the 도곽 on layer ``0``.
    """

    token = (layer or "").strip().lower()
    if not token:
        return 0.0
    keywords = ("도곽", "border", "frame", "title", "sheet", "outline", "표제")
    return 0.04 if any(key in token for key in keywords) else 0.0


def _modelspace_extents(msp: Any) -> Optional[BBox]:
    """True drawing extents; ``None`` when unavailable.

    Tries the fast whole-modelspace ezdxf bbox first, then the renderer's
    entity-by-entity resilient extents. The resilient path matters because a
    single malformed entity (observed: a stray MULTILEADER base point at
    y=-34,891,599) both aborts the fast bbox AND, if naively unioned, corrupts
    the extents — which would make every real rectangle's coverage ratio ~0 and
    silently defeat detection. The caller falls back to the union of frame
    candidates only when both paths fail.
    """

    try:
        from ezdxf import bbox as ezdxf_bbox  # local import; ezdxf is optional

        cache = ezdxf_bbox.Cache()
        box = ezdxf_bbox.extents(msp, cache=cache)
        if box.has_data:
            return (float(box.extmin.x), float(box.extmin.y), float(box.extmax.x), float(box.extmax.y))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("fast modelspace extents failed for sheet-frame detection: %s", exc)

    # Resilient fallback — skips the few entities that raise / sit at absurd
    # coordinates, matching the renderer's own extent recovery so the coverage
    # denominator is the TRUE drawing extent, not a contaminated one.
    try:
        from .dxf_renderer import _resilient_msp_extents

        resilient = _resilient_msp_extents(msp)
        if resilient is not None:
            return (float(resilient[0]), float(resilient[1]), float(resilient[2]), float(resilient[3]))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("resilient modelspace extents failed for sheet-frame detection: %s", exc)
    return None


def _rectangular_frame_candidates(msp: Any) -> List[Tuple[BBox, str]]:
    """Closed rectangular polylines in modelspace as (bbox, layer) candidates."""

    candidates: List[Tuple[BBox, str]] = []
    try:
        entities = msp.query("LWPOLYLINE POLYLINE")
    except Exception:  # pragma: no cover - defensive
        entities = list(msp)
    for entity in entities:
        try:
            bbox = _entity_bbox(entity)
            if bbox is None:
                continue
            if not _is_closed_rectangular_entity(entity, bbox):
                continue
            layer = str(getattr(getattr(entity, "dxf", None), "layer", "") or "")
            candidates.append((bbox, layer))
        except Exception:  # pragma: no cover - skip one bad entity, keep scanning
            continue
    return candidates


def detect_sheet_frame_from_modelspace(
    msp: Any,
    *,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    extents: Optional[BBox] = None,
) -> Optional[SheetFrameResult]:
    """Detect the outer 도곽 bbox from an open modelspace. Pure / headless.

    ``extents`` lets a caller that already computed clean drawing extents (e.g.
    the renderer's outlier-filtered bbox) pass them in, avoiding a recompute and
    guaranteeing the coverage denominator is the TRUE extent. Returns ``None``
    when no closed rectangle covers at least ``min_coverage`` of the extents —
    the caller then keeps the world-union fallback.
    """

    candidates = _rectangular_frame_candidates(msp)
    if not candidates:
        return None

    if extents is None:
        extents = _modelspace_extents(msp)
    if extents is None:
        # Fall back to the union of candidate frames as the coverage denominator.
        xs0 = min(b[0] for b, _ in candidates)
        ys0 = min(b[1] for b, _ in candidates)
        xs1 = max(b[2] for b, _ in candidates)
        ys1 = max(b[3] for b, _ in candidates)
        extents = (xs0, ys0, xs1, ys1)

    extents_area = _bbox_area(extents)
    if extents_area <= 0:
        return None

    # The outer 도곽 is the largest-area closed rectangle.
    best_bbox, best_layer = max(candidates, key=lambda item: _bbox_area(item[0]))
    coverage = _bbox_area(best_bbox) / extents_area
    if coverage < min_coverage or coverage > _MAX_COVERAGE:
        return None
    coverage = min(coverage, 1.0)

    # Confidence: 0.5 coverage -> ~0.62, 1.0 coverage -> ~0.85, + layer bonus.
    confidence = min(0.95, 0.5 + 0.35 * coverage + _frame_layer_bonus(best_layer))
    return SheetFrameResult(
        bbox=best_bbox,
        method="cad_polyline_frame",
        confidence=round(confidence, 4),
        coverage_ratio=round(coverage, 4),
        layer=best_layer,
    )


def detect_sheet_frame_bbox(
    source_path: str | Path,
    *,
    dxf_cache_dir: str | Path | None = None,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
) -> Optional[SheetFrameResult]:
    """Detect the outer 도곽 bbox for a DXF/DWG source path.

    Reuses the shared DXF cache (so a DWG already converted for comparison is not
    re-converted). Any failure degrades to ``None`` (world-union fallback).
    """

    path = Path(source_path)
    suffix = path.suffix.lower()
    if suffix not in {".dxf", ".dwg"}:
        return None
    try:
        dxf_path = _ensure_dxf_path(path, dxf_cache_dir=dxf_cache_dir)
        read_result = read_dxf_document_result(dxf_path)
    except Exception as exc:  # noqa: BLE001 - detection is best-effort
        logger.debug("sheet-frame detection could not read %s: %s", path, exc)
        return None
    try:
        msp = read_result.doc.modelspace()
    except Exception:  # pragma: no cover - defensive
        return None
    return detect_sheet_frame_from_modelspace(msp, min_coverage=min_coverage)


__all__ = [
    "BBox",
    "SheetFrameResult",
    "detect_sheet_frame_bbox",
    "detect_sheet_frame_from_modelspace",
]
