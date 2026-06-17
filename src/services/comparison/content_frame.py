# -*- coding: utf-8 -*-
"""Content-aware viewer framing for multi-detail drawing sheets.

ROOT CAUSE (verified 2026-06-09): the lightweight viewer frames BOTH cameras to
the union of the two sides' FULL render extents — the entire multi-detail sheet —
at load, even though the world-space bbox of every change zone is already in
``workbench._active_overlays_by_zone``. On a 상세도 whose ink is a tiny fraction
of a huge modelspace, fit-to-whole-sheet forces every detail sub-pixel on load
(near-blank overview) and makes the per-zone cold crop render the ONLY way to see
a change.

This module computes a single shared frame around the CHANGED content (the union
of the change-zone bboxes) so the GUI can frame to where the changes are. It keeps
ONE shared frame for both panes (so the before/after shared-origin alignment is
preserved) — it just shrinks that frame from the whole sheet to the changed
content. This is NOT the per-side content-extents overlay (which was
contraindicated); both viewports still receive the same world window.

Pure / headless. The caller injects ``to_world`` so the SAME coordinate-space
gating the per-zone focus path uses (``convert_bbox_to_world_space`` +
``bbox_coordinate_space``/``pdf_dpi``) is reused without importing the GUI here.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence, Tuple

from .transform import normalise_bbox

BBox = Tuple[float, float, float, float]
# (raw_bbox, coordinate_space, pdf_dpi) -> world bbox or None
ToWorld = Callable[[Any, str, float], Optional[BBox]]


def _match_side(change_type: object) -> str:
    """Mirror gui.drawing_compare_workbench.resolve_overlay_match_side without a
    GUI import (services must not depend on the GUI layer)."""

    normalized = str(change_type or "").lower()
    if "delete" in normalized or "remove" in normalized:
        return "a_only"
    if "add" in normalized:
        return "b_only"
    if "mixed" in normalized:
        return "mixed"
    return "matched"


def _present_side_raw_bboxes(overlay: Mapping[str, Any]) -> Iterable[Any]:
    """Yield the raw bbox(es) for the side(s) where this change's content lives,
    mirroring ``_focus_lightweight_on_zone_v2``'s per-side key selection:
    deleted → before (old_bbox), added → after (bbox), modified/moved → both."""

    side = _match_side(overlay.get("change_type"))
    old_b = overlay.get("old_bbox")
    new_b = overlay.get("bbox")
    if side == "a_only":  # deleted: content only in the BEFORE drawing
        yield old_b if old_b is not None else new_b
    elif side == "b_only":  # added: content only in the AFTER drawing
        yield new_b if new_b is not None else old_b
    else:  # matched / mixed: the change spans both locations
        yield old_b
        yield new_b


def _valid_world_bbox(value: object) -> Optional[BBox]:
    """Normalise and reject degenerate boxes. ``change_zones.to_dict`` can emit
    ``bbox=[0,0,0,0]`` for a side with no content, which must NOT widen the
    frame — drop any zero/negative-area box."""

    box = normalise_bbox(value)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return None
    return (float(x0), float(y0), float(x1), float(y1))


def _priority_key(item: Tuple[int, Mapping[str, Any]]) -> Tuple[float, float, int]:
    """Sort key: priority_rank ascending (1 = top), then priority_score
    descending, then original order. Missing rank/score sort last."""

    index, overlay = item
    try:
        rank = float(overlay.get("priority_rank"))
    except (TypeError, ValueError):
        rank = float("inf")
    try:
        score = float(overlay.get("priority_score"))
    except (TypeError, ValueError):
        score = float("-inf")
    return (rank, -score, index)


def _overlay_world_boxes(overlay: Mapping[str, Any], to_world: ToWorld) -> list[BBox]:
    space = str(overlay.get("bbox_coordinate_space") or "")
    try:
        dpi = float(overlay.get("pdf_dpi") or 0.0)
    except (TypeError, ValueError):
        dpi = 0.0
    boxes: list[BBox] = []
    for raw in _present_side_raw_bboxes(overlay):
        if raw is None:
            continue
        try:
            world = to_world(raw, space, dpi)
        except Exception:
            world = None
        valid = _valid_world_bbox(world)
        if valid is not None:
            boxes.append(valid)
    return boxes


def content_frame_from_zone_bboxes(
    overlays: Sequence[Mapping[str, Any]],
    to_world: ToWorld,
    *,
    padding_ratio: float = 0.4,
    min_span: float = 1.0,
    context_floor_ratio: float = 0.5,
) -> Optional[BBox]:
    """Padded world-space frame around the PRIMARY change zone, or ``None``.

    Frames to the highest-priority change zone (``priority_rank`` 1, the one the
    GUI also auto-selects), NOT the union of all zones. On a multi-detail sheet
    whose changes span the full extent, a union of all zones ≈ the whole sheet and
    gives no zoom; framing to the primary change area keeps a real zoom AND matches
    where the focus pane will auto-zoom, so both panes stay aligned (the actual
    "panes look at different positions" complaint, since the per-zone focus path is
    side-asymmetric for deleted/added zones and otherwise leaves the opposite pane
    on the full-sheet load frame). Neighbouring zones in the same detail fall inside
    the padding; other detail clusters are reached via the zone list / navigator.

    ``to_world(raw_bbox, coordinate_space, pdf_dpi)`` converts a raw overlay bbox
    to viewport world space (CAD pass-through; PDF pixel→points). Returns ``None``
    when no overlay yields a usable (non-degenerate) world bbox, so the caller
    safe-degrades to the existing full-extents framing.
    """

    indexed = [
        (i, ov)
        for i, ov in enumerate(overlays or ())
        if isinstance(ov, Mapping)
    ]
    if not indexed:
        return None

    # Context floor (live-test 2026-06-17): on a large multi-detail sheet a tiny,
    # ISOLATED primary zone (e.g. 889 mm in a 524 m sheet) framed bare zooms to a
    # few mm/px and the user is stuck over-zoomed, unable to place the change
    # ("전체 보이다가 클로즈업되어 고정"). Expand such a zone to a fraction of the
    # TYPICAL (median) zone span so it shows surrounding detail like its
    # neighbouring zones — WITHOUT using the outlier-inflated sheet extent. No-op
    # when zones are uniform (the primary already exceeds the floor) or singular.
    spans: list[float] = []
    for _i, _ov in indexed:
        zb = _zone_world_bbox(_ov, to_world)
        if zb is not None:
            spans.append(max(zb[2] - zb[0], zb[3] - zb[1]))
    context_floor = 0.0
    if len(spans) >= 2 and context_floor_ratio > 0.0:
        spans.sort()
        context_floor = float(context_floor_ratio) * spans[len(spans) // 2]

    for _index, overlay in sorted(indexed, key=_priority_key):
        boxes = _overlay_world_boxes(overlay, to_world)
        if not boxes:
            continue
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes)
        y1 = max(b[3] for b in boxes)

        # Min-span floor — guards a tiny zone from collapsing to a zero-area
        # window, and lifts an isolated tiny zone to the typical detail size.
        floor = max(0.0, float(min_span), context_floor)
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if x1 - x0 < floor:
            x0, x1 = cx - floor / 2.0, cx + floor / 2.0
        if y1 - y0 < floor:
            y0, y1 = cy - floor / 2.0, cy + floor / 2.0

        pad = max(0.0, float(padding_ratio))
        width, height = x1 - x0, y1 - y0
        return (x0 - width * pad, y0 - height * pad, x1 + width * pad, y1 + height * pad)

    return None


def _zone_world_bbox(overlay: Mapping[str, Any], to_world: ToWorld) -> Optional[BBox]:
    """Union of an overlay's present-side world bboxes, or None."""

    boxes = _overlay_world_boxes(overlay, to_world)
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _bbox_edge_gap(a: BBox, b: BBox) -> float:
    """Edge-to-edge distance between two bboxes (0 when they overlap)."""

    dx = max(0.0, a[0] - b[2], b[0] - a[2])
    dy = max(0.0, a[1] - b[3], b[1] - a[3])
    return math.hypot(dx, dy)


def _pad_bbox(bbox: BBox, padding_ratio: float, min_span: float) -> BBox:
    x0, y0, x1, y1 = bbox
    floor = max(0.0, float(min_span))
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    if x1 - x0 < floor:
        x0, x1 = cx - floor / 2.0, cx + floor / 2.0
    if y1 - y0 < floor:
        y0, y1 = cy - floor / 2.0, cy + floor / 2.0
    pad = max(0.0, float(padding_ratio))
    w, h = x1 - x0, y1 - y0
    return (x0 - w * pad, y0 - h * pad, x1 + w * pad, y1 + h * pad)


def cluster_zone_bboxes(
    overlays: Sequence[Mapping[str, Any]],
    to_world: ToWorld,
    *,
    padding_ratio: float = 0.15,
    min_span: float = 1.0,
    min_split_ratio: float = 3.0,
    max_zones: int = 96,
) -> List[dict]:
    """Group change zones into spatial detail clusters.

    Single-linkage on the zone bboxes' edge gaps, cut at the LARGEST RELATIVE
    (ratio) jump in the merge gaps. This is scale-free and outlier-resistant — it
    deliberately does NOT use a fraction of the zone-set diagonal as the gap (a
    single far-flung zone would otherwise reproduce the whole-sheet collapse the
    region detector suffers). Returns ``[]`` for no zones, one cluster when the
    zones do not separate cleanly (largest jump below ``min_split_ratio``).

    Each cluster: ``{"bbox": padded_union, "zone_ids": [...], "count": n}``,
    ordered left-to-right by x.
    """

    items: List[Tuple[str, BBox]] = []
    for overlay in overlays or ():
        if not isinstance(overlay, Mapping):
            continue
        bbox = _zone_world_bbox(overlay, to_world)
        if bbox is not None:
            items.append((str(overlay.get("zone_id") or ""), bbox))

    # Bound the O(n^2) single-linkage on the UI thread: a dense pair can produce
    # hundreds of change zones, but the navigator only needs the few largest
    # detail clusters. When over the cap keep the biggest-area zones (the most
    # significant changes); the long tail is still reachable via the zone list.
    if len(items) > max(1, int(max_zones)):
        items = sorted(
            items,
            key=lambda it: (it[1][2] - it[1][0]) * (it[1][3] - it[1][1]),
            reverse=True,
        )[: int(max_zones)]

    def _make(members: List[Tuple[str, BBox]]) -> dict:
        x0 = min(m[1][0] for m in members)
        y0 = min(m[1][1] for m in members)
        x1 = max(m[1][2] for m in members)
        y1 = max(m[1][3] for m in members)
        return {
            "bbox": _pad_bbox((x0, y0, x1, y1), padding_ratio, min_span),
            "zone_ids": [m[0] for m in members],
            "count": len(members),
        }

    n = len(items)
    if n == 0:
        return []
    if n == 1:
        return [_make(items)]

    edges = sorted(
        (_bbox_edge_gap(items[i][1], items[j][1]), i, j)
        for i in range(n)
        for j in range(i + 1, n)
    )

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    merge_gaps: List[float] = []
    for gap, i, j in edges:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
            merge_gaps.append(gap)

    # Largest RATIO jump between consecutive merge gaps marks the intra→inter
    # boundary. Cut just below it.
    cut_gap: Optional[float] = None
    best_ratio = 1.0
    for k in range(len(merge_gaps) - 1):
        lo = max(merge_gaps[k], 1.0)
        ratio = merge_gaps[k + 1] / lo
        if ratio > best_ratio:
            best_ratio = ratio
            cut_gap = merge_gaps[k]

    if cut_gap is None or best_ratio < float(min_split_ratio):
        return [_make(items)]

    parent = list(range(n))
    for gap, i, j in edges:
        if gap > cut_gap + 1e-6:
            break
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    grouped: dict = {}
    for idx in range(n):
        grouped.setdefault(find(idx), []).append(items[idx])
    clusters = [_make(members) for members in grouped.values()]
    clusters.sort(key=lambda c: c["bbox"][0])
    return clusters


__all__ = [
    "BBox",
    "ToWorld",
    "cluster_zone_bboxes",
    "content_frame_from_zone_bboxes",
]
