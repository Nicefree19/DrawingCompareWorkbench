# -*- coding: utf-8 -*-
"""Change-zone grouping and review artifact export for drawing comparison.

Raw CAD entity deltas are too noisy for human review.  This module groups
nearby entity-level changes into stable review zones, assigns traceable labels,
and can export the zone register plus cloud-marked DXF artifacts.
"""

from __future__ import annotations

import csv
import html
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Tuple, Union

from .base import ChangeRecord, ChangeType, ComparisonResult
from .pair_identity import candidate_display_label, candidate_pair_uuid, safe_display_label

BBox = Tuple[float, float, float, float]
CHANGE_ZONE_STREAM_SCHEMA_VERSION = 1


class ChangeZoneStreamError(RuntimeError):
    """Raised when a declared change-zone stream cannot be used."""


@dataclass
class ChangeZoneOptions:
    """Options for grouping raw change records into review zones."""

    cluster_distance: float = 250.0
    bbox_margin: float = 30.0
    min_marker_size: float = 50.0
    max_representative_changes: int = 25
    ignore_layers: Tuple[str, ...] = tuple()
    ignore_layer_patterns: Tuple[str, ...] = tuple()
    ignore_title_block_layers: bool = False
    # Phase Q7 (RV-20260509-002) — anchored regex SSoT (title_block_layer_patterns
    # 모듈) 가 default 적용. **이 tuple 의 default 가 빈 tuple 로 변경됨**
    # — 기존 fnmatch 와일드카드 (``*REV*``) 가 ``REVERSE`` / ``OVERRIDE`` /
    # ``SHEETPILE`` 같은 layer 까지 silent drop 하던 critical false-positive
    # 제거. SSoT helper ``is_title_block_layer`` 가 모든 표준 패턴 (TITLE,
    # BORDER, SHEET, REV, REVISION, STAMP, DRAWING_FRAME, DWG_FRAME, +
    # 한국어 표제란/도면틀/도장/개정) 을 word-boundary 매칭으로 처리.
    #
    # 이 tuple 은 backward-compat 위해 유지 — 사용자가 명시적으로 추가
    # 커스텀 fnmatch 패턴 (예: ``("*COMPANY_TB*", "*PROJECT_HEADER*")``)
    # 을 설정한 경우만 매칭. SSoT 와 OR 로 결합.
    title_block_layer_patterns: Tuple[str, ...] = ()
    # Phase O4 — single-entity zone promote 차단 (의도된 off-by-default 노브).
    # 게이트: ``len(group) < min_changes_per_zone`` AND noise_score >= 임계
    # → zone 미promote. default=1 이면 ``len < 1`` 이 항상 거짓 → 게이트 비활성.
    # 이는 死코드가 아니라 안전상 의도된 기본값이다: 구조 도면 리뷰에서 변경
    # 누락이 최대 리스크이므로 기본은 '모든 변경 노출'. 2 로 올리면 고노이즈
    # 단일 변경 zone 만 억제하는 opt-in (노이즈 위주 리뷰용). 양쪽 동작은
    # test_change_zone_noise_filter.py 가 고정. golden 정확도는 change 단위라 이
    # zone 게이트에 둔감 → flip 효과는 golden 으로 검증 불가 (2026-06-17 확인).
    min_changes_per_zone: int = 1
    # Block-DEFINITION-space records (entity ``space == "block"``) carry
    # block-LOCAL coordinates — a bbox like (0,0)-(20,0) for a block inserted
    # at (500,400) — so their zones land near the origin instead of at the
    # change (golden fixture 11_block_geometry_change). The realized INSERT
    # sibling (space "model") carries the correct world-space zone, and the
    # definition record itself stays in the change list/counts; only zone
    # creation skips it. Suppressed records are surfaced via
    # ``change_zone_block_definition_skipped_count`` (demote-not-drop).
    suppress_block_definition_zones: bool = True
    single_entity_noise_score_threshold: float = 0.7
    # Prevent one transitive cluster chain from becoming a drawing-wide
    # review/cloud zone. Large groups are split into stable spatial buckets
    # before zone IDs are assigned so markers stay near the actual changes.
    max_zone_raw_changes: int = 5000
    max_zone_span: float = 50000.0
    mega_zone_grid_size: float = 20000.0
    # 구조 변경 layer 패턴 — noise_score 산출 시 이 패턴에 해당하는 layer 는
    # noise 가산점 안 받음 (구조 변경은 단일 entity 라도 중요).
    # Phase P (RV-20260508-013) — single source of truth 로 통합. 한국어
    # ("기둥", "보", "가새", "벽") 도 ``is_structural_layer`` helper 가 잡음.
    # 이 fnmatch 튜플은 외부 caller (export_profiles 등) 호환성 위해 유지.
    structural_layer_patterns: Tuple[str, ...] = (
        "*BEAM*", "*COL*", "*COLUMN*", "*BRACE*", "*BRACING*",
        "*GIRDER*", "*TRUSS*", "*WALL*", "*SLAB*", "*PLATE*",
        "*FOOTING*", "*FOUNDATION*", "*PILE*", "*FRAME*",
        "*GR_*", "*BM_*", "*CL_*", "*WL_*", "*FT_*",
    )


@dataclass
class CloudMarkOptions:
    """Options for exporting bounded cloud-mark DXF artifacts."""

    export_mode: str = "selected"
    region_distance: float = 1000.0
    max_regions_per_pair: int = 150
    max_regions_total: int = 3000
    selected_zone_keys: Tuple[str, ...] = tuple()


@dataclass
class DrawingChangeZone:
    """Human-reviewable drawing change zone."""

    zone_id: str
    pair_id: str
    pair_uuid: str = ""
    display_label: str = ""
    drawing_number: str = ""
    change_type: str = "mixed"
    severity: str = "medium"
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)
    old_bbox: Optional[BBox] = None
    # B안 — defining geometry of a single representative entity in CAD-world mm
    # (e.g. ``{"type": "LINE", "points": [[x0, y0], [x1, y1]]}``) so a revision
    # cloud can follow the actual shape (a long leader line) instead of its
    # axis-aligned bbox. None for ambiguous multi-entity zones → viewer falls
    # back to the bbox outline.
    geometry: Optional[dict[str, Any]] = None
    centroid: Tuple[float, float] = (0.0, 0.0)
    raw_change_count: int = 0
    added_count: int = 0
    deleted_count: int = 0
    modified_count: int = 0
    layers: Tuple[str, ...] = tuple()
    entity_types: Tuple[str, ...] = tuple()
    representative_change_keys: Tuple[str, ...] = tuple()
    status: str = "review_required"
    reasons: Tuple[str, ...] = tuple()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.zone_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "pair_id": self.pair_id,
            "pair_uuid": self.pair_uuid or self.pair_id,
            "display_label": self.display_label or self.drawing_number or self.pair_id,
            "drawing_number": self.drawing_number,
            "change_type": self.change_type,
            "severity": self.severity,
            "bbox": list(self.bbox),
            "old_bbox": list(self.old_bbox) if self.old_bbox else None,
            "geometry": self.geometry,
            "centroid": list(self.centroid),
            "raw_change_count": self.raw_change_count,
            "added_count": self.added_count,
            "deleted_count": self.deleted_count,
            "modified_count": self.modified_count,
            "layers": list(self.layers),
            "entity_types": list(self.entity_types),
            "representative_change_keys": list(self.representative_change_keys),
            "status": self.status,
            "reasons": list(self.reasons),
            "metadata": self.metadata,
        }


@dataclass
class CloudMarkRegion:
    """Aggregated region used only for cloud-mark DXF output."""

    region_id: str
    pair_id: str
    pair_uuid: str = ""
    display_label: str = ""
    drawing_number: str = ""
    change_type: str = "mixed"
    severity: str = "medium"
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)
    old_bbox: Optional[BBox] = None
    raw_change_count: int = 0
    added_count: int = 0
    deleted_count: int = 0
    modified_count: int = 0
    source_zone_ids: Tuple[str, ...] = tuple()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.region_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "pair_id": self.pair_id,
            "pair_uuid": self.pair_uuid or self.pair_id,
            "display_label": self.display_label or self.drawing_number or self.pair_id,
            "drawing_number": self.drawing_number,
            "change_type": self.change_type,
            "severity": self.severity,
            "bbox": list(self.bbox),
            "old_bbox": list(self.old_bbox) if self.old_bbox else None,
            "raw_change_count": self.raw_change_count,
            "added_count": self.added_count,
            "deleted_count": self.deleted_count,
            "modified_count": self.modified_count,
            "source_zone_ids": list(self.source_zone_ids),
            "metadata": self.metadata,
        }


@dataclass
class MarkedArtifact:
    """Cloud-marked output paths for one compared pair."""

    pair_id: str
    pair_uuid: str = ""
    display_label: str = ""
    drawing_number: str = ""
    source_a: str = ""
    source_b: str = ""
    after_marked_dxf: Optional[str] = None
    before_marked_dxf: Optional[str] = None
    zone_count: int = 0
    raw_change_count: int = 0
    zone_input_source: str = "memory"
    zone_input_count: int = 0
    zone_coverage_complete: bool = True
    cloud_export_mode: str = "off"
    cloud_region_count: int = 0
    cloud_omitted_zone_count: int = 0
    warnings: list[str] = field(default_factory=list)
    # P0-2b — RigidTransform.to_dict() (after->before) from the comparison
    # metadata, carried through the artifact manifest so the viewer can warp the
    # after raster into the before frame. None = no significant alignment.
    alignment: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "pair_uuid": self.pair_uuid or self.pair_id,
            "display_label": self.display_label or self.drawing_number or self.pair_id,
            "drawing_number": self.drawing_number,
            "source_a": self.source_a,
            "source_b": self.source_b,
            "after_marked_dxf": self.after_marked_dxf,
            "before_marked_dxf": self.before_marked_dxf,
            "zone_count": self.zone_count,
            "raw_change_count": self.raw_change_count,
            "zone_input_source": self.zone_input_source,
            "zone_input_count": self.zone_input_count,
            "zone_coverage_complete": self.zone_coverage_complete,
            "cloud_export_mode": self.cloud_export_mode,
            "cloud_region_count": self.cloud_region_count,
            "cloud_omitted_zone_count": self.cloud_omitted_zone_count,
            "warnings": self.warnings,
            "alignment": self.alignment,
        }


@dataclass
class ChangeArtifactPackage:
    """Aggregate artifact export result."""

    output_dir: str
    generated_at: str
    pair_count: int
    zone_count: int
    raw_change_count: int
    zone_input_count: int = 0
    zone_coverage_complete: bool = True
    cloud_export_mode: str = "off"
    cloud_region_count: int = 0
    cloud_omitted_zone_count: int = 0
    dxf_cache_dir: str = ""
    compare_state_dir: str = ""
    artifacts: list[MarkedArtifact] = field(default_factory=list)
    output_paths: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": self.output_dir,
            "generated_at": self.generated_at,
            "pair_count": self.pair_count,
            "zone_count": self.zone_count,
            "raw_change_count": self.raw_change_count,
            "zone_input_count": self.zone_input_count,
            "zone_coverage_complete": self.zone_coverage_complete,
            "cloud_export_mode": self.cloud_export_mode,
            "cloud_region_count": self.cloud_region_count,
            "cloud_omitted_zone_count": self.cloud_omitted_zone_count,
            "dxf_cache_dir": self.dxf_cache_dir,
            "compare_state_dir": self.compare_state_dir,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "output_paths": self.output_paths,
            "warnings": self.warnings,
        }


@dataclass
class ExecutiveReviewOptions:
    """Options for lightweight executive review outputs."""

    top_drawings: int = 15
    top_zones: int = 30


@dataclass
class ExecutiveReviewPackage:
    """Generated lightweight dashboard and brief artifacts."""

    output_dir: str
    generated_at: str
    drawing_count: int
    zone_count: int
    raw_change_count: int
    cloud_region_count: int = 0
    cloud_omitted_zone_count: int = 0
    zone_coverage_complete: bool = True
    top_drawings: list[dict[str, Any]] = field(default_factory=list)
    top_zones: list[dict[str, Any]] = field(default_factory=list)
    repeated_patterns: list[dict[str, Any]] = field(default_factory=list)
    output_paths: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": self.output_dir,
            "generated_at": self.generated_at,
            "drawing_count": self.drawing_count,
            "zone_count": self.zone_count,
            "raw_change_count": self.raw_change_count,
            "cloud_region_count": self.cloud_region_count,
            "cloud_omitted_zone_count": self.cloud_omitted_zone_count,
            "zone_coverage_complete": self.zone_coverage_complete,
            "top_drawings": self.top_drawings,
            "top_zones": self.top_zones,
            "repeated_patterns": self.repeated_patterns,
            "output_paths": self.output_paths,
            "warnings": self.warnings,
        }


@dataclass
class _ChangeEnvelope:
    index: int
    change: ChangeRecord
    bbox: BBox
    old_bbox: Optional[BBox]


@dataclass
class _ZoneInput:
    envelopes: list[_ChangeEnvelope]
    source: str
    input_count: int
    skipped_count: int = 0
    coverage_complete: bool = True
    warning: str = ""
    # Intentional suppressions (block-LOCAL-coordinate definition records) —
    # separate from ``skipped_count`` so they do not flag coverage as broken.
    block_definition_skipped: int = 0


def build_change_zones(
    result: ComparisonResult,
    *,
    pair_id: str = "",
    drawing_number: str = "",
    options: Optional[ChangeZoneOptions] = None,
) -> list[DrawingChangeZone]:
    """Group a comparison result into deterministic review zones."""

    zone_options = options or ChangeZoneOptions()
    zone_input = _zone_input_from_result(result, zone_options)
    result.metadata["change_zone_input_source"] = zone_input.source
    result.metadata["change_zone_input_count"] = zone_input.input_count
    result.metadata["change_zone_skipped_record_count"] = zone_input.skipped_count
    result.metadata["change_zone_coverage_complete"] = zone_input.coverage_complete
    if zone_input.warning:
        result.metadata["change_zone_warning"] = zone_input.warning
    if zone_input.block_definition_skipped:
        result.metadata["change_zone_block_definition_skipped_count"] = (
            zone_input.block_definition_skipped
        )
    envelopes = zone_input.envelopes

    if not envelopes:
        return []

    groups = _cluster_envelopes(envelopes, zone_options.cluster_distance)
    zones: list[DrawingChangeZone] = []
    suppressed_count = 0  # Phase O4 — single-entity 차단 통계

    next_zone_index = 1
    for group in groups:
        split_groups = _split_mega_group(group, zone_options)
        split_count = len(split_groups)
        for split_index, (zone_group, split_meta) in enumerate(split_groups, start=1):
            noise_score = _compute_zone_noise_score(zone_group, zone_options)
            if (
                len(zone_group) < zone_options.min_changes_per_zone
                and noise_score >= zone_options.single_entity_noise_score_threshold
            ):
                suppressed_count += 1
                continue

            zone = _build_zone(
                zone_id=f"C-{next_zone_index:03d}",
                pair_id=pair_id or _default_pair_id(result),
                drawing_number=drawing_number,
                envelopes=zone_group,
                options=zone_options,
            )
            next_zone_index += 1
            zone.metadata.update(
                {
                    "zone_input_source": zone_input.source,
                    "zone_input_count": zone_input.input_count,
                    "zone_coverage_complete": zone_input.coverage_complete,
                    "noise_score": round(noise_score, 3),
                    **split_meta,
                }
            )
            if split_count > 1:
                zone.metadata.setdefault("mega_zone_split_index", split_index)
                zone.metadata.setdefault("mega_zone_split_count", split_count)
            zones.append(zone)

    if suppressed_count:
        result.metadata["change_zone_noise_suppressed_count"] = suppressed_count
    linked_pairs = link_relocation_zone_pairs(zones)
    if linked_pairs:
        result.metadata["relocation_pair_count"] = linked_pairs
    return zones


# Relocation distance cap (2026-06-18, rebar AC1024 robustness): a relocation is
# a move WITHIN the drawing, so the from→to distance cannot plausibly exceed the
# drawing's content extent. Without a cap, a size-identical deleted/added pair was
# linked regardless of distance — a 160×160 mm stray hatch (a runaway block-local
# ±34.9M-coord entity inserted on one side) got "relocation"-linked to a same-size
# add 34.8 KM away. Cap at FACTOR × the ROBUST centroid diagonal (IQR-fenced so the
# strays themselves don't inflate the cap), floored for tiny/clean drawings.
_RELOCATION_DIST_CAP_FACTOR = 2.0
_RELOCATION_DIST_CAP_FLOOR = 500_000.0  # mm


def _robust_span(values: Sequence[float]) -> float:
    """Span of ``values`` after dropping Tukey far outliers (k=3) — so a few
    runaway coordinates don't dominate. Falls back to the full span for < 4."""

    vals = sorted(float(v) for v in values)
    n = len(vals)
    if n == 0:
        return 0.0
    if n < 4:
        return vals[-1] - vals[0]
    q1 = vals[n // 4]
    q3 = vals[(3 * n) // 4]
    iqr = q3 - q1
    lo, hi = q1 - 3.0 * iqr, q3 + 3.0 * iqr
    kept = [v for v in vals if lo <= v <= hi]
    return (kept[-1] - kept[0]) if len(kept) >= 2 else (vals[-1] - vals[0])


def link_relocation_zone_pairs(zones: Sequence["DrawingChangeZone"]) -> int:
    """Link size-identical deleted↔added zone pairs as probable relocations.

    Live review on the real 240111_P5 pair: C-001 (deleted, 13 records,
    15,702×17,309 mm) and C-002 (added, 13 records, SAME bbox size) are one
    notes block moved ~82 m. The comparator honestly reports delete+add (the
    move exceeds every matching tolerance), but the reviewer should see the
    relationship. This links such pairs via METADATA ONLY — zones, counts and
    change types are untouched (demote-not-drop; no count cosmetics — the
    341 km fake-consolidation lesson). Criteria are deliberately conservative:

      * one zone deleted-only, the other added-only
      * record counts equal (and > 0)
      * bbox width AND height equal within max(2 mm, 1%)

    Greedy best match (smallest size difference, then nearest centroid);
    each zone links at most once. Returns the number of linked pairs.

    Linked metadata (both zones): ``relocation_pair_id``,
    ``relocation_role`` ("from"/"to"), ``relocation_counterpart`` (zone id),
    ``relocation_counterpart_bbox`` (CAD-world list), ``relocation_offset``
    ([dx, dy], from→to). The GUI uses the counterpart bbox to frame
    before=old location / after=new location simultaneously.
    """

    deleted = [
        z for z in zones
        if z.change_type == "deleted" and z.deleted_count > 0 and z.added_count == 0
    ]
    added = [
        z for z in zones
        if z.change_type == "added" and z.added_count > 0 and z.deleted_count == 0
    ]
    if not deleted or not added:
        return 0

    def _dims(zone: "DrawingChangeZone") -> Tuple[float, float]:
        box = zone.bbox
        return (float(box[2]) - float(box[0]), float(box[3]) - float(box[1]))

    # A move can't exceed the drawing's (robust) content extent — cap the link
    # distance so runaway strays aren't paired across the whole inflated space.
    dist_cap = max(
        _RELOCATION_DIST_CAP_FACTOR * math.hypot(
            _robust_span([float(z.centroid[0]) for z in zones]),
            _robust_span([float(z.centroid[1]) for z in zones]),
        ),
        _RELOCATION_DIST_CAP_FLOOR,
    )

    candidates: list[tuple[float, float, "DrawingChangeZone", "DrawingChangeZone"]] = []
    for d_zone in deleted:
        dw, dh = _dims(d_zone)
        for a_zone in added:
            if d_zone.deleted_count != a_zone.added_count:
                continue
            aw, ah = _dims(a_zone)
            tol_w = max(2.0, 0.01 * max(dw, aw))
            tol_h = max(2.0, 0.01 * max(dh, ah))
            if abs(dw - aw) > tol_w or abs(dh - ah) > tol_h:
                continue
            size_diff = abs(dw - aw) + abs(dh - ah)
            dist = math.hypot(
                float(a_zone.centroid[0]) - float(d_zone.centroid[0]),
                float(a_zone.centroid[1]) - float(d_zone.centroid[1]),
            )
            # Reject implausibly far "moves" (runaway-stray false links). A real
            # relocation stays within the content; a 34.8 km pair is a stray.
            if dist > dist_cap:
                continue
            candidates.append((size_diff, dist, d_zone, a_zone))

    candidates.sort(key=lambda item: (item[0], item[1]))
    used: set[int] = set()
    pairs = 0
    for _size_diff, _dist, d_zone, a_zone in candidates:
        if id(d_zone) in used or id(a_zone) in used:
            continue
        used.add(id(d_zone))
        used.add(id(a_zone))
        pairs += 1
        pair_id = f"R-{pairs:03d}"
        offset = [
            float(a_zone.centroid[0]) - float(d_zone.centroid[0]),
            float(a_zone.centroid[1]) - float(d_zone.centroid[1]),
        ]
        d_zone.metadata.update({
            "relocation_pair_id": pair_id,
            "relocation_role": "from",
            "relocation_counterpart": a_zone.zone_id,
            "relocation_counterpart_bbox": [float(v) for v in a_zone.bbox],
            "relocation_offset": offset,
        })
        a_zone.metadata.update({
            "relocation_pair_id": pair_id,
            "relocation_role": "to",
            "relocation_counterpart": d_zone.zone_id,
            "relocation_counterpart_bbox": [
                float(v) for v in (d_zone.old_bbox or d_zone.bbox)
            ],
            "relocation_offset": offset,
        })
    return pairs


def _zone_input_from_result(
    result: ComparisonResult,
    zone_options: ChangeZoneOptions,
) -> _ZoneInput:
    stream_path = (result.metadata or {}).get("change_zone_stream_path")
    if stream_path:
        return _zone_input_from_stream(Path(stream_path), result, zone_options)

    envelopes: list[_ChangeEnvelope] = []
    skipped = 0
    block_definition_skipped = 0
    for index, change in enumerate(result.changes):
        if _change_ignored(change, zone_options):
            continue
        if zone_options.suppress_block_definition_zones and _is_block_definition_change(change):
            block_definition_skipped += 1
            continue
        bbox = change_record_bbox(change, zone_options)
        if bbox is None:
            skipped += 1
            continue
        old_bbox = change_record_old_bbox(change, zone_options)
        envelopes.append(_ChangeEnvelope(index=index, change=change, bbox=bbox, old_bbox=old_bbox))
    truncated = bool((result.metadata or {}).get("truncated_changes"))
    warning = ""
    if truncated:
        warning = (
            "change zones use retained detailed change records; "
            "stream metadata is missing"
        )
    return _ZoneInput(
        envelopes=envelopes,
        source="memory",
        input_count=len(result.changes),
        skipped_count=skipped,
        coverage_complete=not truncated and skipped == 0,
        warning=warning,
        block_definition_skipped=block_definition_skipped,
    )


def _zone_input_from_stream(
    path: Path,
    result: ComparisonResult,
    zone_options: ChangeZoneOptions,
) -> _ZoneInput:
    if not path.exists():
        raise ChangeZoneStreamError(f"change-zone stream does not exist: {path}")
    envelopes: list[_ChangeEnvelope] = []
    skipped = 0
    block_definition_skipped = 0
    total = 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                total += 1
                try:
                    record = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ChangeZoneStreamError(
                        f"invalid change-zone stream JSON at line {line_number}: {exc}"
                    ) from exc
                if record.get("schema_version") != CHANGE_ZONE_STREAM_SCHEMA_VERSION:
                    raise ChangeZoneStreamError(
                        f"unsupported change-zone stream schema at line {line_number}: "
                        f"{record.get('schema_version')}"
                    )
                bbox = _coerce_bbox(record.get("bbox"))
                if bbox is None:
                    skipped += 1
                    continue
                old_bbox = _coerce_bbox(record.get("old_bbox"))
                change = _change_record_from_stream_record(record)
                if _change_ignored(change, zone_options):
                    continue
                if zone_options.suppress_block_definition_zones and _is_block_definition_change(change):
                    block_definition_skipped += 1
                    continue
                envelopes.append(
                    _ChangeEnvelope(
                        index=line_number - 1,
                        change=change,
                        bbox=bbox,
                        old_bbox=old_bbox,
                    )
                )
    except OSError as exc:
        raise ChangeZoneStreamError(f"cannot read change-zone stream {path}: {exc}") from exc

    stream_complete = bool((result.metadata or {}).get("change_zone_stream_complete", True))
    return _ZoneInput(
        envelopes=envelopes,
        source="stream",
        input_count=total,
        skipped_count=skipped,
        coverage_complete=stream_complete and skipped == 0,
        block_definition_skipped=block_definition_skipped,
    )


def change_record_bbox(
    change: ChangeRecord,
    options: Optional[ChangeZoneOptions] = None,
) -> Optional[BBox]:
    """Return the best available new/current bbox for a raw change record."""

    zone_options = options or ChangeZoneOptions()
    metadata = change.metadata or {}
    entity_type = str(metadata.get("entity_type") or "").upper()
    data = change.new_value if change.change_type != ChangeType.DELETED else change.old_value
    bbox = _bbox_from_entity_data(entity_type, data, zone_options)
    if bbox is None:
        bbox = _bbox_from_metadata(metadata, zone_options)
    if bbox is None:
        bbox = _bbox_from_location(change.location, zone_options)
    # Block-LOCAL data → world re-anchoring (CAD-only; PDF records carry
    # pixel bboxes with prose/points locations — see _metadata_blocks_anchoring).
    if _metadata_blocks_anchoring(metadata):
        return bbox
    anchored, _delta = _anchor_bbox_to_location(bbox, _location_point(change.location))
    return anchored


def change_record_old_bbox(
    change: ChangeRecord,
    options: Optional[ChangeZoneOptions] = None,
) -> Optional[BBox]:
    """Return old/origin bbox for deletion and moved/modified records."""

    zone_options = options or ChangeZoneOptions()
    metadata = change.metadata or {}
    entity_type = str(metadata.get("entity_type") or "").upper()
    bbox = _bbox_from_entity_data(entity_type, change.old_value, zone_options)
    if bbox is not None:
        # Block-LOCAL data → world re-anchoring (CAD-only; see
        # _metadata_blocks_anchoring for the PDF exclusion).
        if _metadata_blocks_anchoring(metadata):
            return bbox
        anchor = (
            _point_from_metadata(metadata, "old_x", "old_y")
            or _point(metadata.get("old_location"))
            or _location_point(change.location)
        )
        anchored, _delta = _anchor_bbox_to_location(bbox, anchor)
        return anchored
    raw_old_bbox = _coerce_bbox(metadata.get("old_bbox"))
    if raw_old_bbox is not None:
        return _ensure_min_bbox(raw_old_bbox, zone_options.min_marker_size)
    if metadata.get("old_x") is not None and metadata.get("old_y") is not None:
        try:
            x = float(metadata["old_x"])
            y = float(metadata["old_y"])
            return _point_bbox((x, y), zone_options.min_marker_size)
        except Exception:
            return None
    return None


def write_change_zone_stream(
    changes: Sequence[Any],
    stream_path: Union[str, Path],
    *,
    pair_id: str = "",
) -> dict[str, Any]:
    """Write compact change-location records for full-coverage zone generation."""

    stream_path = Path(stream_path).resolve()
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = stream_path.with_name(f"{stream_path.name}.{os.getpid()}.tmp")
    record_count = 0
    missing_bbox_count = 0
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            for index, change in enumerate(changes):
                record = change_to_stream_record(change, pair_id=pair_id, index=index)
                if record.get("bbox") is None:
                    missing_bbox_count += 1
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
                record_count += 1
        os.replace(temp_path, stream_path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
    return {
        "change_zone_stream_path": str(stream_path),
        "change_zone_record_count": record_count,
        "change_zone_stream_complete": True,
        "change_zone_stream_schema_version": CHANGE_ZONE_STREAM_SCHEMA_VERSION,
        "change_zone_missing_bbox_count": missing_bbox_count,
    }


def change_to_stream_record(change: Any, *, pair_id: str = "", index: int = 0) -> dict[str, Any]:
    change_type = _change_type_value(getattr(change, "change_type", "modified"))
    metadata = getattr(change, "metadata", None) or {}
    entity_type = str(getattr(change, "entity_type", "") or metadata.get("entity_type") or "")
    layer = str(getattr(change, "layer", "") or metadata.get("layer") or "")
    old_data = getattr(change, "old_data", None)
    if old_data is None:
        old_data = getattr(change, "old_value", None)
    new_data = getattr(change, "new_data", None)
    if new_data is None:
        new_data = getattr(change, "new_value", None)
    location = _location_point(getattr(change, "location", None)) or _point_from_metadata(metadata, "x", "y")
    old_location = _location_point(getattr(change, "old_location", None)) or _point_from_metadata(
        metadata,
        "old_x",
        "old_y",
    )
    metadata_bbox = _bbox_from_metadata(metadata, ChangeZoneOptions())
    bbox = metadata_bbox or _bbox_for_stream_change(change_type, entity_type, old_data, new_data, location)
    old_bbox = _bbox_for_stream_old_change(entity_type, old_data, old_location)
    if old_bbox is None and metadata_bbox is not None and change_type in {"deleted", "modified"}:
        old_bbox = metadata_bbox
    # Block-LOCAL data → world re-anchoring (see _anchor_bbox_to_location).
    # Without this every block-internal change zone/cloud on the legacy path
    # pointed at the origin area instead of the actual drawing location.
    # CAD-only: PDF records carry pixel bboxes with prose/points locations —
    # anchoring them corrupts the zones (2026-06-11 regression).
    if _metadata_blocks_anchoring(metadata):
        new_delta = old_delta = (0.0, 0.0)
    else:
        bbox, new_delta = _anchor_bbox_to_location(bbox, location)
        old_bbox, old_delta = _anchor_bbox_to_location(old_bbox, old_location or location)
    old_text = _text_value_from_change_data(old_data)
    new_text = _text_value_from_change_data(new_data)
    # B3 — preserve the entity's real geometry through the stream so large
    # (stream-built) comparisons can draw geometry-aware clouds. Optional field;
    # absent on older streams → recovered as None (backward compatible, no
    # schema-version bump needed since this is purely additive).
    geometry = _geometry_points_from_entity_data(entity_type, new_data)
    geometry_delta = new_delta
    if not geometry:
        geometry = _geometry_points_from_entity_data(entity_type, old_data)
        geometry_delta = old_delta
    if geometry and (geometry_delta[0] or geometry_delta[1]):
        # Shift the geometry by the SAME world anchor delta as its source
        # side's bbox so clouds stay congruent with the zone.
        gdx, gdy = geometry_delta
        try:
            geometry = {
                **geometry,
                "points": [[p[0] + gdx, p[1] + gdy] for p in geometry.get("points", [])],
            }
        except (TypeError, IndexError):
            pass
    # Preserve the canonical entity ``space`` through the compact stream so
    # stream-built zones can suppress block-DEFINITION-space records (their
    # bboxes are block-LOCAL, not world). Additive optional field — absent on
    # older streams → None → no suppression (backward compatible, same
    # convention as the B3 ``geometry`` field above).
    entity_space = ""
    for data in (new_data, old_data):
        if isinstance(data, dict) and data.get("space"):
            entity_space = str(data.get("space"))
            break
    key = getattr(change, "key", "") or f"{entity_type}_{change_type}_{index}"
    return {
        "schema_version": CHANGE_ZONE_STREAM_SCHEMA_VERSION,
        "pair_id": pair_id,
        "key": str(key),
        "change_type": change_type,
        "layer": layer,
        "entity_type": entity_type,
        "bbox": list(bbox) if bbox else None,
        "old_bbox": list(old_bbox) if old_bbox else None,
        "geometry": geometry,
        "location": list(location) if location else None,
        "old_location": list(old_location) if old_location else None,
        "change_category": getattr(change, "change_category", None) or metadata.get("change_category"),
        "change_detail": getattr(change, "change_detail", None) or metadata.get("change_detail"),
        "entity_space": entity_space or None,
        "page": metadata.get("page"),
        "page_a": metadata.get("page_a"),
        "page_b": metadata.get("page_b"),
        "page_match_status": metadata.get("page_match_status"),
        "page_match_score": metadata.get("page_match_score"),
        "source_format": metadata.get("source_format"),
        "detection_source": metadata.get("detection_source"),
        "bbox_status": metadata.get("bbox_status"),
        "bbox_coordinate_space": metadata.get("bbox_coordinate_space"),
        "pdf_dpi": metadata.get("pdf_dpi"),
        "compare_pdf_dpi": metadata.get("compare_pdf_dpi"),
        "effective_dpi": metadata.get("effective_dpi"),
        "old_text": old_text,
        "new_text": new_text,
        "old_content": old_text,
        "new_content": new_text,
    }


def _text_value_from_change_data(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, dict):
        for key in ("text", "content", "old_text", "new_text", "value", "label"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _point_from_metadata(
    metadata: dict[str, Any],
    x_key: str,
    y_key: str,
) -> Optional[Tuple[float, float]]:
    if metadata.get(x_key) is None or metadata.get(y_key) is None:
        return None
    try:
        return (float(metadata[x_key]), float(metadata[y_key]))
    except Exception:
        return None


# A location string is only a coordinate when the WHOLE string is one —
# legacy CAD ChangeRecords store ``str(tuple)`` ("(x, y)") or bare "x,y".
# PDF records store prose like ``"page 1: ..."``; loosely grabbing the first
# two numbers from those turned "page 1" into a (1, ...) anchor point and
# dragged every PDF zone bbox to x≈0 (2026-06-11 PDF-compare regression).
_COORD_STRING_RE = re.compile(
    r"^\s*\(?\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*,"
    r"\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(?:,\s*[-+]?\d+(?:\.\d+)?"
    r"(?:[eE][-+]?\d+)?\s*)?\)?\s*$"
)


def _location_point(value: Any) -> Optional[Tuple[float, float]]:
    """Parse a change location given as a tuple/list OR a coordinate string.

    Strings must be a PURE coordinate form ("(x, y)", "x,y", optionally with
    a z component) — prose locations such as ``"page 1: ..."`` return None so
    they can never become anchoring targets.
    """

    point = _point(value)
    if point is not None:
        return point
    if value is None:
        return None
    match = _COORD_STRING_RE.match(str(value))
    if match:
        try:
            return (float(match.group(1)), float(match.group(2)))
        except ValueError:
            return None
    return None


def _metadata_blocks_anchoring(metadata: Any) -> bool:
    """True when a record's coordinate convention forbids location anchoring.

    PDF visual records keep their bbox in ``image_pixels`` while their
    ``location`` is prose/points — two DIFFERENT spaces, so the world
    re-anchoring that fixes block-LOCAL CAD data would corrupt them
    (2026-06-11 regression: every PDF zone bbox got dragged to x≈0 and 9
    zones collapsed into 4). Anchoring is a CAD-only repair.
    """

    if not isinstance(metadata, dict):
        return False
    if str(metadata.get("bbox_coordinate_space") or "") == "image_pixels":
        return True
    return str(metadata.get("source_format") or "").lower() == "pdf"


def _anchor_bbox_to_location(
    bbox: Optional[BBox],
    location: Optional[Tuple[float, float]],
) -> Tuple[Optional[BBox], Tuple[float, float]]:
    """Re-anchor a data-derived bbox to the record's WORLD location.

    Legacy block-expanded entities keep their geometry data in BLOCK-LOCAL
    coordinates while ``change.location`` is world (verified on the POT
    BEARING pair: ARC data center ``(0,0)`` vs world location
    ``(516460,-107284)``), so a data-derived bbox lands near the origin and
    every zone/cloud built from it points at the wrong place. When the bbox
    centre disagrees with the world location by more than the bbox diagonal
    (+1 mm) — far beyond any legitimate centre/anchor offset such as TEXT
    inserted at a corner — translate the bbox so its centre sits at the
    location, preserving its size. Returns the (possibly translated) bbox
    and the applied ``(dx, dy)`` so callers can shift companion payloads
    (the stream ``geometry`` points) coherently.
    """

    if bbox is None or location is None:
        return bbox, (0.0, 0.0)
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    diagonal = math.hypot(bbox[2] - bbox[0], bbox[3] - bbox[1])
    if math.hypot(location[0] - cx, location[1] - cy) <= diagonal + 1.0:
        return bbox, (0.0, 0.0)
    dx, dy = location[0] - cx, location[1] - cy
    return (bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy), (dx, dy)


def _bbox_for_stream_change(
    change_type: str,
    entity_type: str,
    old_data: Any,
    new_data: Any,
    location: Optional[Tuple[float, float]],
) -> Optional[BBox]:
    options = ChangeZoneOptions()
    data = old_data if change_type == "deleted" else new_data
    bbox = _bbox_from_entity_data(entity_type.upper(), data, options)
    if bbox is None and isinstance(data, dict):
        bbox = _bbox_from_metadata(data, options)
    if bbox is None and location:
        bbox = _point_bbox(location, options.min_marker_size)
    if bbox is None and change_type == "deleted":
        bbox = _bbox_from_entity_data(entity_type.upper(), old_data, options)
    return bbox


def _bbox_for_stream_old_change(
    entity_type: str,
    old_data: Any,
    old_location: Optional[Tuple[float, float]],
) -> Optional[BBox]:
    options = ChangeZoneOptions()
    bbox = _bbox_from_entity_data(entity_type.upper(), old_data, options)
    if bbox is None and isinstance(old_data, dict):
        bbox = _bbox_from_metadata(old_data, options)
    if bbox is None and old_location:
        bbox = _point_bbox(old_location, options.min_marker_size)
    return bbox


def _change_record_from_stream_record(record: dict[str, Any]) -> ChangeRecord:
    metadata = {
        "layer": record.get("layer") or "",
        "entity_type": record.get("entity_type") or "",
        "change_type": record.get("change_type") or "",
        "change_category": record.get("change_category"),
        "change_detail": record.get("change_detail"),
        "entity_space": record.get("entity_space"),
        "page": record.get("page"),
        "page_a": record.get("page_a"),
        "page_b": record.get("page_b"),
        "page_match_status": record.get("page_match_status"),
        "page_match_score": record.get("page_match_score"),
        "source_format": record.get("source_format"),
        "detection_source": record.get("detection_source"),
        "bbox_status": record.get("bbox_status"),
        "bbox_coordinate_space": record.get("bbox_coordinate_space"),
        "pdf_dpi": record.get("pdf_dpi"),
        "compare_pdf_dpi": record.get("compare_pdf_dpi"),
        "effective_dpi": record.get("effective_dpi"),
    }
    if record.get("location"):
        metadata["x"] = record["location"][0]
        metadata["y"] = record["location"][1]
    if record.get("old_location"):
        metadata["old_x"] = record["old_location"][0]
        metadata["old_y"] = record["old_location"][1]
    # B3 — recover the entity geometry stashed by change_to_stream_record so
    # stream-built zones can render geometry-aware clouds (see _geometry_from_envelopes).
    if isinstance(record.get("geometry"), dict):
        metadata["geometry"] = record["geometry"]
    return ChangeRecord(
        key=str(record.get("key") or ""),
        change_type=_stream_change_type(record.get("change_type")),
        old_value=_stream_value_from_record(record, old=True),
        new_value=_stream_value_from_record(record, old=False),
        location=str(record.get("location") or ""),
        metadata=metadata,
    )


def _stream_value_from_record(record: dict[str, Any], *, old: bool) -> Optional[dict[str, Any]]:
    bbox_key = "old_bbox" if old else "bbox"
    text_key = "old_text" if old else "new_text"
    content_key = "old_content" if old else "new_content"
    value: dict[str, Any] = {}
    if record.get(bbox_key):
        value["bbox"] = record.get(bbox_key)
    text = str(record.get(text_key) or record.get(content_key) or "").strip()
    if text:
        value["text"] = text
        value["content"] = text
    return value or None


def _stream_change_type(value: Any) -> ChangeType:
    text = str(value or "").lower()
    if text == ChangeType.ADDED.value:
        return ChangeType.ADDED
    if text == ChangeType.DELETED.value:
        return ChangeType.DELETED
    if text == ChangeType.MODIFIED.value:
        return ChangeType.MODIFIED
    return ChangeType.MODIFIED


def export_change_artifacts(
    summary: Any,
    output_dir: Union[str, Path],
    *,
    dxf_cache_dir: Optional[Union[str, Path]] = None,
    compare_state_dir: Optional[Union[str, Path]] = None,
    zone_options: Optional[ChangeZoneOptions] = None,
    cloud_options: Optional[CloudMarkOptions] = None,
    export_cloud_marks: bool = True,
    export_before_marks: bool = False,
) -> ChangeArtifactPackage:
    """Export change zone register, review HTML, and optional cloud-marked DXF."""

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cloud_dir = output_dir / "cloud_marked"
    cloud_dir.mkdir(parents=True, exist_ok=True)

    zone_options = zone_options or ChangeZoneOptions()
    cloud_options = cloud_options or CloudMarkOptions()
    if not export_cloud_marks:
        cloud_options = CloudMarkOptions(export_mode="off")
    all_zones: list[DrawingChangeZone] = []
    zones_by_pair: dict[str, list[DrawingChangeZone]] = {}
    candidate_by_pair: dict[str, Any] = {}
    result_by_pair: dict[str, ComparisonResult] = {}
    artifacts: list[MarkedArtifact] = []
    warnings: list[str] = []

    for item in getattr(summary, "items", []):
        if getattr(item, "status", "") != "completed" or not getattr(item, "result", None):
            continue
        candidate = item.candidate
        result: ComparisonResult = item.result
        drawing_number = _candidate_drawing_number(candidate)
        pair_id = candidate_pair_uuid(candidate)
        display_label = candidate_display_label(candidate, drawing_number or pair_id)
        artifact = MarkedArtifact(
            pair_id=pair_id,
            pair_uuid=pair_id,
            display_label=display_label,
            drawing_number=drawing_number,
            source_a=candidate.source_a.path if candidate.source_a else "",
            source_b=candidate.source_b.path if candidate.source_b else "",
            raw_change_count=_result_change_count(result),
            cloud_export_mode=cloud_options.export_mode,
        )
        # P0-2b — carry the rigid alignment (after->before) so the viewer can
        # visually align the after raster. Only a dict survives (defensive).
        _alignment_meta = result.metadata.get("alignment")
        if isinstance(_alignment_meta, dict):
            artifact.alignment = _alignment_meta
        try:
            zones = build_change_zones(
                result,
                pair_id=pair_id,
                drawing_number=drawing_number,
                options=zone_options,
            )
        except ChangeZoneStreamError as exc:
            zones = []
            result.metadata["change_zone_count"] = 0
            result.metadata["change_zones"] = []
            result.metadata["change_zone_input_source"] = "stream"
            result.metadata["change_zone_input_count"] = 0
            result.metadata["change_zone_coverage_complete"] = False
            result.metadata["change_zone_stream_error"] = str(exc)
            artifact.zone_coverage_complete = False
            artifact.warnings.append(str(exc))
            warnings.append(f"{pair_id}: {exc}")
        for zone in zones:
            zone.pair_uuid = pair_id
            zone.display_label = display_label
            zone.metadata.update(
                {
                    "pair_uuid": pair_id,
                    "display_label": display_label,
                    "source_a": candidate.source_a.path if candidate.source_a else "",
                    "source_b": candidate.source_b.path if candidate.source_b else "",
                }
            )
        all_zones.extend(zones)
        zones_by_pair[pair_id] = zones
        candidate_by_pair[pair_id] = candidate
        result_by_pair[pair_id] = result
        artifact.zone_count = len(zones)
        artifact.zone_input_source = result.metadata.get("change_zone_input_source", "memory")
        artifact.zone_input_count = int(result.metadata.get("change_zone_input_count", len(result.changes)) or 0)
        artifact.zone_coverage_complete = bool(result.metadata.get("change_zone_coverage_complete", True))
        if (result.metadata or {}).get("truncated_changes"):
            if result.metadata.get("change_zone_input_source") == "stream":
                warning = (
                    "change zones use full change-zone stream; retained detailed "
                    "change records may still be truncated"
                )
            else:
                warning = (
                    "change zones use retained detailed change records; full raw counts "
                    "remain available in metadata.change_counts"
                )
            artifact.warnings.append(warning)
            warnings.append(f"{pair_id}: {warning}")
        result.metadata["change_zone_count"] = len(zones)
        result.metadata["change_zones"] = [zone.to_dict() for zone in zones]
        artifacts.append(artifact)

    paths = {
        "change_zones_json": str(output_dir / "change_zones.json"),
        "change_zones_csv": str(output_dir / "change_zones.csv"),
        "review_index_html": str(output_dir / "review_index.html"),
        "artifact_manifest_json": str(output_dir / "artifact_manifest.json"),
        "change_register_xlsx": str(output_dir / "change_register.xlsx"),
        "cloud_omitted_zones_csv": str(output_dir / "cloud_omitted_zones.csv"),
        "cloud_marked_dir": str(cloud_dir),
    }
    regions_by_pair, omitted_zones = _build_cloud_regions_by_pair(zones_by_pair, cloud_options)
    omitted_by_pair = Counter(zone.pair_id for zone, _reason in omitted_zones)
    artifact_by_pair = {artifact.pair_id: artifact for artifact in artifacts}
    if export_cloud_marks and cloud_options.export_mode != "off":
        for pair_id, regions in regions_by_pair.items():
            artifact = artifact_by_pair.get(pair_id)
            candidate = candidate_by_pair.get(pair_id)
            if artifact is None or candidate is None:
                continue
            artifact.cloud_region_count = len(regions)
            artifact.cloud_omitted_zone_count = int(omitted_by_pair.get(pair_id, 0))
            if regions:
                _write_cloud_artifacts(
                    candidate,
                    regions,
                    cloud_dir,
                    artifact,
                    dxf_cache_dir=dxf_cache_dir,
                    export_before_marks=export_before_marks,
                )
    for artifact in artifacts:
        artifact.cloud_region_count = int(artifact.cloud_region_count or len(regions_by_pair.get(artifact.pair_id, [])))
        artifact.cloud_omitted_zone_count = int(
            artifact.cloud_omitted_zone_count or omitted_by_pair.get(artifact.pair_id, 0)
        )
        result = result_by_pair.get(artifact.pair_id)
        if result is not None:
            result.metadata["cloud_export_mode"] = cloud_options.export_mode
            result.metadata["cloud_region_count"] = artifact.cloud_region_count
            result.metadata["cloud_omitted_zone_count"] = artifact.cloud_omitted_zone_count
            result.metadata["marked_artifacts"] = artifact.to_dict()
    _write_cloud_omitted_zones_csv(Path(paths["cloud_omitted_zones_csv"]), omitted_zones)

    package = ChangeArtifactPackage(
        output_dir=str(output_dir),
        generated_at=datetime.now().isoformat(),
        pair_count=len(artifacts),
        zone_count=len(all_zones),
        raw_change_count=sum(artifact.raw_change_count for artifact in artifacts),
        zone_input_count=sum(artifact.zone_input_count for artifact in artifacts),
        zone_coverage_complete=all(artifact.zone_coverage_complete for artifact in artifacts),
        cloud_export_mode=cloud_options.export_mode,
        cloud_region_count=sum(artifact.cloud_region_count for artifact in artifacts),
        cloud_omitted_zone_count=sum(artifact.cloud_omitted_zone_count for artifact in artifacts),
        dxf_cache_dir=str(Path(dxf_cache_dir).resolve()) if dxf_cache_dir else "",
        compare_state_dir=str(Path(compare_state_dir).resolve()) if compare_state_dir else "",
        artifacts=artifacts,
        output_paths=paths,
        warnings=warnings,
    )
    _write_change_zones_json(Path(paths["change_zones_json"]), all_zones, package)
    _write_change_zones_csv(Path(paths["change_zones_csv"]), all_zones)
    _write_review_index(Path(paths["review_index_html"]), package, all_zones)
    _write_change_register(Path(paths["change_register_xlsx"]), all_zones, package)
    _write_json(Path(paths["artifact_manifest_json"]), package.to_dict())
    return package


def export_executive_review_from_artifacts(
    artifact_dir: Union[str, Path],
    *,
    top_drawings: int = 15,
    top_zones: int = 30,
    top_review_issues: int = 100,
    top_issues_per_drawing: int = 20,
    fold_repetitive_layers: bool = True,
) -> ExecutiveReviewPackage:
    """Create lightweight human-readable dashboard outputs from existing artifacts.

    This intentionally reads the already exported zone CSV/manifest so it can be
    rerun without comparing drawings or regenerating DWG->DXF conversions.
    """

    artifact_dir = Path(artifact_dir).resolve()
    manifest_path = artifact_dir / "artifact_manifest.json"
    zones_csv_path = artifact_dir / "change_zones.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"artifact manifest not found: {manifest_path}")
    if not zones_csv_path.exists():
        raise FileNotFoundError(f"change zone CSV not found: {zones_csv_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    zone_rows = _read_change_zone_csv_rows(zones_csv_path)
    artifact_by_pair = _artifact_lookup_from_manifest(manifest)
    options = ExecutiveReviewOptions(
        top_drawings=max(int(top_drawings or 15), 1),
        top_zones=max(int(top_zones or 30), 1),
    )

    from .review_dashboard import export_review_dashboard

    dashboard_package = export_review_dashboard(
        artifact_dir,
        top_review_issues=top_review_issues,
        top_issues_per_drawing=top_issues_per_drawing,
        fold_repetitive_layers=fold_repetitive_layers,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    drawing_rows = _summarize_executive_drawings(zone_rows, artifact_by_pair)
    top_drawing_rows = drawing_rows[: options.top_drawings]
    top_zone_rows = _top_executive_zones(zone_rows, artifact_by_pair, options.top_zones)
    repeated_patterns = _summarize_repeated_layer_patterns(zone_rows)

    zone_count = _int_cell(manifest.get("zone_count"), len(zone_rows))
    raw_change_count = _int_cell(
        manifest.get("raw_change_count"),
        sum(_int_cell(row.get("raw_change_count")) for row in zone_rows),
    )
    computed_raw = sum(_int_cell(row.get("raw_change_count")) for row in zone_rows)
    warnings: list[str] = []
    if computed_raw and raw_change_count != computed_raw:
        warnings.append(
            f"manifest raw_change_count={raw_change_count} differs from change_zones.csv sum={computed_raw}"
        )
    if zone_count != len(zone_rows):
        warnings.append(f"manifest zone_count={zone_count} differs from change_zones.csv rows={len(zone_rows)}")

    paths = {
        "executive_review_html": str(artifact_dir / "executive_review.html"),
        "drawing_change_brief_md": str(artifact_dir / "drawing_change_brief.md"),
        "drawing_change_brief_csv": str(artifact_dir / "drawing_change_brief.csv"),
    }
    paths.update(dashboard_package.output_paths)
    package = ExecutiveReviewPackage(
        output_dir=str(artifact_dir),
        generated_at=datetime.now().isoformat(),
        drawing_count=len(drawing_rows),
        zone_count=zone_count,
        raw_change_count=raw_change_count,
        cloud_region_count=_int_cell(manifest.get("cloud_region_count")),
        cloud_omitted_zone_count=_int_cell(manifest.get("cloud_omitted_zone_count")),
        zone_coverage_complete=bool(manifest.get("zone_coverage_complete", True)),
        top_drawings=top_drawing_rows,
        top_zones=top_zone_rows,
        repeated_patterns=repeated_patterns,
        output_paths=paths,
        warnings=warnings,
    )

    _write_executive_brief_csv(Path(paths["drawing_change_brief_csv"]), drawing_rows)
    _write_executive_brief_md(Path(paths["drawing_change_brief_md"]), package, manifest)
    _write_executive_review_html(
        Path(paths["executive_review_html"]),
        package,
        manifest,
        dashboard=dashboard_package.to_dict(),
    )
    _update_manifest_with_executive_outputs(manifest_path, package)
    return package


def _build_cloud_regions_by_pair(
    zones_by_pair: dict[str, list[DrawingChangeZone]],
    options: CloudMarkOptions,
) -> tuple[dict[str, list[CloudMarkRegion]], list[tuple[DrawingChangeZone, str]]]:
    mode = (options.export_mode or "selected").lower()
    if mode == "off":
        return {}, [(zone, "cloud_export_off") for zones in zones_by_pair.values() for zone in zones]

    selected_keys = {key.strip() for key in options.selected_zone_keys if key.strip()}
    regions_by_pair: dict[str, list[CloudMarkRegion]] = {}
    omitted: list[tuple[DrawingChangeZone, str]] = []

    for pair_id, zones in zones_by_pair.items():
        if mode == "csv":
            included = [zone for zone in zones if _zone_selection_key_matches(zone, selected_keys)]
            included_ids = {id(zone) for zone in included}
            omitted.extend((zone, "not_selected") for zone in zones if id(zone) not in included_ids)
        else:
            included = list(zones)

        pair_regions = _zones_to_cloud_regions(
            pair_id,
            included,
            max(float(options.region_distance), 0.0),
        )
        pair_regions = sorted(pair_regions, key=_cloud_region_sort_key)
        if mode == "selected" and options.max_regions_per_pair > 0:
            keep = pair_regions[: int(options.max_regions_per_pair)]
            dropped = pair_regions[int(options.max_regions_per_pair) :]
            kept_zone_ids = {zone_id for region in keep for zone_id in region.source_zone_ids}
            for region in dropped:
                for zone in included:
                    if zone.zone_id in region.source_zone_ids and zone.zone_id not in kept_zone_ids:
                        omitted.append((zone, "max_region_cap"))
            pair_regions = keep
        regions_by_pair[pair_id] = pair_regions

    if mode == "selected" and options.max_regions_total > 0:
        all_regions = [
            (pair_id, region)
            for pair_id, regions in regions_by_pair.items()
            for region in regions
        ]
        all_regions = sorted(all_regions, key=lambda item: _cloud_region_sort_key(item[1]))
        keep_pairs = all_regions[: int(options.max_regions_total)]
        kept_ids = {(pair_id, region.region_id) for pair_id, region in keep_pairs}
        kept_zone_ids = {
            (pair_id, zone_id)
            for pair_id, region in keep_pairs
            for zone_id in region.source_zone_ids
        }
        for pair_id, regions in list(regions_by_pair.items()):
            kept = []
            for region in regions:
                if (pair_id, region.region_id) in kept_ids:
                    kept.append(region)
                else:
                    for zone in zones_by_pair.get(pair_id, []):
                        if (
                            zone.zone_id in region.source_zone_ids
                            and (pair_id, zone.zone_id) not in kept_zone_ids
                        ):
                            omitted.append((zone, "max_total_region_cap"))
            regions_by_pair[pair_id] = kept

    return regions_by_pair, omitted


def _zones_to_cloud_regions(
    pair_id: str,
    zones: Sequence[DrawingChangeZone],
    region_distance: float,
) -> list[CloudMarkRegion]:
    if not zones:
        return []
    groups = _cluster_zone_groups(zones, region_distance)
    regions = [
        _build_cloud_region(f"R-{index:03d}", pair_id, group)
        for index, group in enumerate(groups, start=1)
    ]
    return regions


def _cluster_zone_groups(
    zones: Sequence[DrawingChangeZone],
    cluster_distance: float,
) -> list[list[DrawingChangeZone]]:
    parent = list(range(len(zones)))
    rank = [0 for _ in zones]
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    cell_size = max(float(cluster_distance), 1.0)

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_l = find(left)
        root_r = find(right)
        if root_l == root_r:
            return
        if rank[root_l] < rank[root_r]:
            parent[root_l] = root_r
        elif rank[root_l] > rank[root_r]:
            parent[root_r] = root_l
        else:
            parent[root_r] = root_l
            rank[root_l] += 1

    for index, zone in enumerate(zones):
        search_bbox = _inflate_bbox(zone.bbox, cluster_distance)
        candidates: set[int] = set()
        for cell in _bbox_cells(search_bbox, cell_size):
            candidates.update(cells.get(cell, []))
        for other_index in sorted(candidates):
            if _bbox_distance(zone.bbox, zones[other_index].bbox) <= cluster_distance:
                union(index, other_index)
        for cell in _bbox_cells(zone.bbox, cell_size):
            cells[cell].append(index)

    grouped: dict[int, list[DrawingChangeZone]] = defaultdict(list)
    for index, zone in enumerate(zones):
        grouped[find(index)].append(zone)
    return sorted(
        grouped.values(),
        key=lambda group: (
            min(zone.bbox[1] for zone in group),
            min(zone.bbox[0] for zone in group),
            min(zone.zone_id for zone in group),
        ),
    )


def _build_cloud_region(
    region_id: str,
    pair_id: str,
    zones: Sequence[DrawingChangeZone],
) -> CloudMarkRegion:
    bbox = _union_bboxes([zone.bbox for zone in zones])
    old_boxes = [zone.old_bbox for zone in zones if zone.old_bbox is not None]
    old_bbox = _union_bboxes(old_boxes) if old_boxes else None
    type_counts = Counter(zone.change_type for zone in zones)
    change_type = zones[0].change_type if len(type_counts) == 1 else "mixed"
    severity = max((zone.severity for zone in zones), key=_severity_rank)
    return CloudMarkRegion(
        region_id=region_id,
        pair_id=pair_id,
        pair_uuid=pair_id,
        display_label=(zones[0].display_label if zones else "") or (zones[0].drawing_number if zones else "") or pair_id,
        drawing_number=zones[0].drawing_number if zones else "",
        change_type=change_type,
        severity=severity,
        bbox=bbox,
        old_bbox=old_bbox,
        raw_change_count=sum(zone.raw_change_count for zone in zones),
        added_count=sum(zone.added_count for zone in zones),
        deleted_count=sum(zone.deleted_count for zone in zones),
        modified_count=sum(zone.modified_count for zone in zones),
        source_zone_ids=tuple(zone.zone_id for zone in zones),
        metadata={
            "source_zone_count": len(zones),
            "source_zones": [zone.zone_id for zone in zones],
        },
    )


def _zone_selection_key_matches(zone: DrawingChangeZone, selected_keys: set[str]) -> bool:
    if not selected_keys:
        return False
    candidates = {
        zone.zone_id,
        f"{zone.pair_id}:{zone.zone_id}",
        f"{zone.drawing_number}:{zone.zone_id}",
    }
    return bool(candidates & selected_keys)


def _cloud_region_sort_key(region: CloudMarkRegion) -> tuple[int, int, float, str]:
    return (
        -_severity_rank(region.severity),
        -int(region.raw_change_count),
        -_bbox_area(region.bbox),
        region.region_id,
    )


def _severity_rank(value: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(str(value).lower(), 0)


def _bbox_area(bbox: BBox) -> float:
    return max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))


def _write_cloud_artifacts(
    candidate: Any,
    zones: Sequence[Any],
    cloud_dir: Path,
    artifact: MarkedArtifact,
    *,
    dxf_cache_dir: Optional[Union[str, Path]],
    export_before_marks: bool,
) -> None:
    if not candidate.source_a or not candidate.source_b:
        artifact.warnings.append("missing source path")
        return
    if candidate.source_a.kind.value != "cad" or candidate.source_b.kind.value != "cad":
        artifact.warnings.append("cloud-mark DXF export is CAD-only")
        return

    from .dwg_differ import DwgDiffer
    from .dxf_cloud_marker import DxfCloudMarker

    marker = DxfCloudMarker(add_labels=True, margin=0.0)
    differ = DwgDiffer(dxf_cache_dir=dxf_cache_dir)
    try:
        source_b_dxf = differ._ensure_dxf(candidate.source_b.path_obj)
        after_path = cloud_dir / f"{artifact.pair_id}_after_marked.dxf"
        marker.create_marked_dxf_from_zones(source_b_dxf, zones, after_path)
        artifact.after_marked_dxf = str(after_path)

        before_zones = [
            zone for zone in zones if zone.deleted_count > 0 or zone.old_bbox is not None
        ]
        if export_before_marks and before_zones:
            source_a_dxf = differ._ensure_dxf(candidate.source_a.path_obj)
            before_path = cloud_dir / f"{artifact.pair_id}_before_marked.dxf"
            marker.create_marked_dxf_from_zones(
                source_a_dxf,
                before_zones,
                before_path,
                use_old_bbox=True,
            )
            artifact.before_marked_dxf = str(before_path)
    except Exception as exc:
        artifact.warnings.append(str(exc))
    finally:
        differ._cleanup_temp()


def _cluster_envelopes(
    envelopes: Sequence[_ChangeEnvelope],
    cluster_distance: float,
) -> list[list[_ChangeEnvelope]]:
    parent = list(range(len(envelopes)))
    rank = [0 for _ in envelopes]
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    cell_size = max(float(cluster_distance), 1.0)

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_l = find(left)
        root_r = find(right)
        if root_l == root_r:
            return
        if rank[root_l] < rank[root_r]:
            parent[root_l] = root_r
        elif rank[root_l] > rank[root_r]:
            parent[root_r] = root_l
        else:
            parent[root_r] = root_l
            rank[root_l] += 1

    for index, envelope in enumerate(envelopes):
        search_bbox = _inflate_bbox(envelope.bbox, cluster_distance)
        candidates: set[int] = set()
        for cell in _bbox_cells(search_bbox, cell_size):
            candidates.update(cells.get(cell, []))
        for other_index in sorted(candidates):
            if _bbox_distance(envelope.bbox, envelopes[other_index].bbox) <= cluster_distance:
                union(index, other_index)
        for cell in _bbox_cells(envelope.bbox, cell_size):
            cells[cell].append(index)

    grouped: dict[int, list[_ChangeEnvelope]] = defaultdict(list)
    for index, envelope in enumerate(envelopes):
        grouped[find(index)].append(envelope)
    return sorted(
        grouped.values(),
        key=lambda group: (
            min(item.bbox[1] for item in group),
            min(item.bbox[0] for item in group),
            min(item.index for item in group),
        ),
    )


def _split_mega_group(
    group: Sequence[_ChangeEnvelope],
    options: ChangeZoneOptions,
) -> list[tuple[list[_ChangeEnvelope], dict[str, Any]]]:
    if not group:
        return []
    max_raw = max(0, int(options.max_zone_raw_changes or 0))
    max_span = max(0.0, float(options.max_zone_span or 0.0))
    bbox = _union_bboxes([item.bbox for item in group])
    span_x = abs(float(bbox[2]) - float(bbox[0]))
    span_y = abs(float(bbox[3]) - float(bbox[1]))
    needs_split = (
        (max_raw > 0 and len(group) > max_raw)
        or (max_span > 0 and max(span_x, span_y) > max_span and len(group) > 1)
    )
    if not needs_split:
        return [(list(group), {})]

    grid_size = max(
        float(options.mega_zone_grid_size or 0.0),
        float(options.cluster_distance or 0.0) * 2.0,
        1.0,
    )
    buckets: dict[tuple[int, int], list[_ChangeEnvelope]] = defaultdict(list)
    for item in group:
        cx = (float(item.bbox[0]) + float(item.bbox[2])) / 2.0
        cy = (float(item.bbox[1]) + float(item.bbox[3])) / 2.0
        buckets[(math.floor(cx / grid_size), math.floor(cy / grid_size))].append(item)

    split: list[tuple[list[_ChangeEnvelope], dict[str, Any]]] = []
    original_count = len(group)
    original_span = round(max(span_x, span_y), 3)
    chunk_size = max_raw if max_raw > 0 else original_count
    for bucket_key, items in sorted(
        buckets.items(),
        key=lambda entry: (
            min(item.bbox[1] for item in entry[1]),
            min(item.bbox[0] for item in entry[1]),
            min(item.index for item in entry[1]),
        ),
    ):
        ordered = sorted(items, key=lambda item: (item.bbox[1], item.bbox[0], item.index))
        for offset in range(0, len(ordered), chunk_size):
            chunk = ordered[offset : offset + chunk_size]
            split.append(
                (
                    chunk,
                    {
                        "mega_zone_split": True,
                        "mega_zone_original_raw_change_count": original_count,
                        "mega_zone_original_max_span": original_span,
                        "mega_zone_grid_size": grid_size,
                        "mega_zone_grid_bucket": f"{bucket_key[0]},{bucket_key[1]}",
                    },
                )
            )
    return split or [(list(group), {})]


def _compute_zone_noise_score(
    envelopes: Sequence[_ChangeEnvelope],
    options: ChangeZoneOptions,
) -> float:
    """Phase O4 — zone candidate 의 노이즈 정도를 0..1 로 추정.

    가중치 (총 합 1.0):
    - 0.30 — 단일 entity 변경 (cluster size == 1)
    - 0.30 — 모든 envelope 의 change_category 가 "cosmetic"
    - 0.20 — bbox 대각선이 1mm 미만 (micro-shift)
    - 0.20 — 모든 layer 가 structural 패턴에 매칭 안 됨
              (구조 변경이 아닌 마킹/주석/임의 layer)

    structural layer 매칭이 1개라도 있으면 마지막 항목은 가산 안 함 —
    구조 변경은 단일 entity 라도 보존되어야 함.
    """
    if not envelopes:
        return 0.0

    score = 0.0

    # Signal 1 — single entity
    if len(envelopes) == 1:
        score += 0.3

    # Signal 2 — cosmetic-only
    def _category(env: _ChangeEnvelope) -> str:
        change = env.change
        cat = getattr(change, "change_category", None)
        if cat:
            return str(cat).lower()
        meta_cat = (change.metadata or {}).get("change_category")
        return str(meta_cat).lower() if meta_cat else ""

    if all(_category(e) == "cosmetic" for e in envelopes):
        score += 0.3

    # Signal 3 — micro bbox (모든 envelope 의 bbox 대각선 < 1mm)
    def _diag(bbox: BBox) -> float:
        return math.hypot(bbox[2] - bbox[0], bbox[3] - bbox[1])

    if all(_diag(e.bbox) < 1.0 for e in envelopes):
        score += 0.2

    # Signal 4 — non-structural layers
    # Phase P (RV-20260508-013) — SSoT helper 사용. 한국어 layer
    # ("기둥-1F" 등) 가 fnmatch 영문 패턴에 안 잡혀 silent drop 되던 회귀
    # 차단. ``is_structural_layer`` 가 한국어 substring + 영문 regex
    # 둘 다 검사.
    #
    # Codex RV-20260509 P2 — caller 가 ``structural_layer_patterns=()``
    # (빈 튜플) 로 layer-기반 structural 검사를 disable 하도록 요청하면
    # SSoT 검사도 skip 하고 +0.2 penalty 도 부여하지 않아야 함. 이전
    # 패치는 ``is_structural=False`` 로 강제 후 그대로 +0.2 penalty 를
    # 적용하여 customized 호출자의 단일 entity zone 이 모두 폐기됨.
    if options.structural_layer_patterns is None or options.structural_layer_patterns:
        from .structural_layer_patterns import is_structural_layer  # local import
        layers = [
            str((e.change.metadata or {}).get("layer") or "")
            for e in envelopes
        ]
        is_structural = any(is_structural_layer(layer) for layer in layers)
        if not is_structural:
            score += 0.2
    # 빈 튜플이면 Signal 4 자체를 건너뜀 (penalty 없음) — caller 의 명시적
    # disable 의도 존중.

    return min(1.0, score)


def _build_zone(
    *,
    zone_id: str,
    pair_id: str,
    drawing_number: str,
    envelopes: Sequence[_ChangeEnvelope],
    options: ChangeZoneOptions,
) -> DrawingChangeZone:
    bbox = _union_bboxes([item.bbox for item in envelopes])
    old_boxes = [item.old_bbox for item in envelopes if item.old_bbox is not None]
    old_bbox = _union_bboxes(old_boxes) if old_boxes else None
    type_counts = Counter(_change_type_value(item.change.change_type) for item in envelopes)
    layer_counts = Counter(str((item.change.metadata or {}).get("layer") or "") for item in envelopes)
    entity_counts = Counter(
        str((item.change.metadata or {}).get("entity_type") or "") for item in envelopes
    )

    has_moved_origin = any(
        _change_has_moved_origin(item.change, item.bbox, item.old_bbox)
        for item in envelopes
    )
    change_type = _zone_change_type(type_counts, has_moved_origin)
    added = int(type_counts.get("added", 0))
    deleted = int(type_counts.get("deleted", 0))
    modified = int(type_counts.get("modified", 0))
    severity, severity_reasons = _zone_severity(
        envelopes, type_counts, layer_counts, entity_counts, change_type=change_type
    )
    reasons = [f"clustered {len(envelopes)} raw change(s)"] + severity_reasons
    text_evidence = _zone_text_evidence(envelopes)
    if text_evidence.get("reason_text"):
        reasons.append(str(text_evidence["reason_text"]))

    geometry = _geometry_from_envelopes(envelopes)

    bbox = _inflate_bbox(_ensure_min_bbox(bbox, options.min_marker_size), options.bbox_margin)
    if old_bbox:
        old_bbox = _inflate_bbox(_ensure_min_bbox(old_bbox, options.min_marker_size), options.bbox_margin)

    return DrawingChangeZone(
        zone_id=zone_id,
        pair_id=pair_id,
        pair_uuid=pair_id,
        display_label=drawing_number or pair_id,
        drawing_number=drawing_number,
        change_type=change_type,
        severity=severity,
        bbox=bbox,
        old_bbox=old_bbox,
        geometry=geometry,
        centroid=((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0),
        raw_change_count=len(envelopes),
        added_count=added,
        deleted_count=deleted,
        modified_count=modified,
        layers=tuple(sorted(layer for layer in layer_counts if layer)),
        entity_types=tuple(sorted(entity for entity in entity_counts if entity)),
        representative_change_keys=tuple(
            item.change.key for item in sorted(envelopes, key=lambda item: item.index)[
                : options.max_representative_changes
            ]
        ),
        reasons=tuple(reasons),
        metadata={
            "layer_counts": dict(layer_counts),
            "entity_type_counts": dict(entity_counts),
            "source_format": _first_metadata_value(envelopes, "source_format"),
            "detection_source": _first_metadata_value(envelopes, "detection_source"),
            "bbox_status": _first_metadata_value(envelopes, "bbox_status"),
            "bbox_coordinate_space": _first_metadata_value(envelopes, "bbox_coordinate_space"),
            "page": _first_metadata_value(envelopes, "page"),
            "page_a": _first_metadata_value(envelopes, "page_a"),
            "page_b": _first_metadata_value(envelopes, "page_b"),
            "page_match_status": _first_metadata_value(envelopes, "page_match_status"),
            "page_match_score": _first_metadata_value(envelopes, "page_match_score"),
            "pdf_dpi": _first_metadata_value(envelopes, "pdf_dpi"),
            "compare_pdf_dpi": _first_metadata_value(envelopes, "compare_pdf_dpi"),
            "effective_dpi": _first_metadata_value(envelopes, "effective_dpi"),
            **text_evidence,
        },
    )


def _geometry_points_from_entity_data(
    entity_type: str, data: Any
) -> Optional[dict[str, Any]]:
    """Return ``{"type", "points"}`` (CAD-world mm) for a single LINE / polyline
    entity, else None. Shared by the memory path and the change-zone stream (B3).
    """
    if not isinstance(data, dict):
        return None
    et = str(entity_type or "").upper()
    if et == "LINE":
        start = _point(data.get("start"))
        end = _point(data.get("end"))
        if start and end:
            return {"type": "LINE", "points": [[start[0], start[1]], [end[0], end[1]]]}
    if et in {"LWPOLYLINE", "POLYLINE"}:
        pts = [_point(point) for point in data.get("points", [])]
        pts = [[point[0], point[1]] for point in pts if point]
        if len(pts) >= 2:
            return {"type": et, "points": pts}
    return None


def _geometry_from_envelopes(
    envelopes: Sequence[_ChangeEnvelope],
) -> Optional[dict[str, Any]]:
    """B안 — capture a single representative entity's defining geometry (CAD-world
    mm) so a revision cloud can follow the actual shape instead of the bbox.

    Emitted only for an unambiguous single LINE / polyline change (the long
    leader-line case that Phase A draws as an oversized bbox). Multi-entity or
    non-polyline zones return None so the viewer keeps the bbox outline.

    Stream-built zones (B3, large files) recover the geometry from
    ``metadata["geometry"]`` stashed by ``_change_record_from_stream_record``;
    memory-built zones derive it directly from the entity data.
    """
    if len(envelopes) != 1:
        return None
    change = envelopes[0].change
    metadata = change.metadata or {}
    stashed = metadata.get("geometry")
    if isinstance(stashed, dict) and stashed.get("points"):
        return stashed
    entity_type = str(metadata.get("entity_type") or "").upper()
    data = change.new_value if change.change_type != ChangeType.DELETED else change.old_value
    if not isinstance(data, dict):
        data = change.old_value if isinstance(change.old_value, dict) else change.new_value
    return _geometry_points_from_entity_data(entity_type, data)


def _zone_text_evidence(envelopes: Sequence[_ChangeEnvelope]) -> dict[str, Any]:
    pairs: list[tuple[str, str]] = []
    details: list[str] = []
    categories: list[str] = []
    for item in envelopes:
        change = item.change
        old_text = _text_value_from_change_data(getattr(change, "old_value", None))
        new_text = _text_value_from_change_data(getattr(change, "new_value", None))
        if old_text or new_text:
            pair = (old_text, new_text)
            if pair not in pairs:
                pairs.append(pair)
        metadata = change.metadata or {}
        detail = str(metadata.get("change_detail") or "").strip()
        if detail and detail not in details:
            details.append(detail)
        category = str(metadata.get("change_category") or "").strip()
        if category and category not in categories:
            categories.append(category)

    out: dict[str, Any] = {}
    if pairs:
        old_values = [old for old, _new in pairs if old]
        new_values = [new for _old, new in pairs if new]
        if old_values:
            out["old_text"] = old_values[0]
            out["old_content"] = old_values[0]
        if new_values:
            out["new_text"] = new_values[0]
            out["new_content"] = new_values[0]
        pair_summaries = [
            f"{old or '(none)'} -> {new or '(none)'}"
            for old, new in pairs[:3]
            if old != new
        ]
        if pair_summaries:
            out["reason_text"] = " | ".join(pair_summaries)
            out["text_change_count"] = len(pair_summaries)
    if details:
        out["change_detail"] = " | ".join(details[:3])
    if categories:
        out["change_category"] = " | ".join(categories[:3])
    return out


def _first_metadata_value(envelopes: Sequence[_ChangeEnvelope], key: str) -> str:
    for item in envelopes:
        value = (item.change.metadata or {}).get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _is_block_definition_change(change: ChangeRecord) -> bool:
    """True when this record describes a BLOCK-DEFINITION-space entity.

    The canonical importer (expand_blocks) realizes INSERT instances into
    model space AND keeps the definition entities (``space == "block"``) in
    the compared entity list, so one block edit yields two records: the
    realized instance in world coordinates and the definition in block-LOCAL
    coordinates (e.g. bbox (0,0)-(20,0) for a block inserted at (500,400)).
    A block-local bbox is meaningless in the drawing's world zone map — it
    placed the change zone near the origin (verified on golden fixture
    ``11_block_geometry_change``). Canonical entity dicts carry ``space``
    directly; stream-rebuilt records carry it as ``metadata.entity_space``.
    """

    for value in (change.new_value, change.old_value):
        if isinstance(value, dict) and str(value.get("space") or "").lower() == "block":
            return True
    meta = change.metadata or {}
    return str(meta.get("entity_space") or "").lower() == "block"


def _change_ignored(change: ChangeRecord, options: ChangeZoneOptions) -> bool:
    layer = str((change.metadata or {}).get("layer") or "")
    normalized = layer.upper()
    if normalized and normalized in {value.upper() for value in options.ignore_layers}:
        return True
    for pattern in options.ignore_layer_patterns:
        if fnmatch(normalized, pattern.upper()):
            return True
    if options.ignore_title_block_layers and layer:
        # Phase Q7 (RV-20260509-002) — anchored regex SSoT 가 우선 적용.
        # ``*REV*`` 와일드카드가 ``REVERSE`` / ``OVERRIDE`` / ``REVENUE``
        # 같은 layer 까지 잡아 silent drop 하던 문제 해결.
        try:
            from src.services.comparison.title_block_layer_patterns import (
                is_title_block_layer,
            )
            if is_title_block_layer(layer):
                return True
        except Exception:
            # SSoT 모듈 import 실패 시 legacy fnmatch 만 사용 (안전 fallback).
            pass
        # Backward-compat: 사용자 커스텀 fnmatch 패턴 (SSoT 미커버) 도 적용.
        for pattern in options.title_block_layer_patterns:
            if fnmatch(normalized, pattern.upper()):
                return True
    return False


def _bbox_from_entity_data(
    entity_type: str,
    data: Any,
    options: ChangeZoneOptions,
) -> Optional[BBox]:
    if not isinstance(data, dict):
        return None
    raw_bbox = _coerce_bbox(data.get("bbox"))
    if raw_bbox:
        return _ensure_min_bbox(raw_bbox, options.min_marker_size)

    if entity_type == "LINE":
        start = _point(data.get("start"))
        end = _point(data.get("end"))
        if start and end:
            return _ensure_min_bbox(
                (min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1])),
                options.min_marker_size,
            )
    if entity_type in {"CIRCLE", "ARC"}:
        center = _point(data.get("center"))
        radius = _float(data.get("radius"))
        if center and radius is not None:
            return (
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            )
    if entity_type in {"LWPOLYLINE", "POLYLINE"}:
        points = [_point(point) for point in data.get("points", [])]
        points = [point for point in points if point]
        if points:
            return _bbox_from_points(points, options.min_marker_size)
    if entity_type in {"TEXT", "MTEXT"}:
        point = _point(data.get("position"))
        if point:
            return _point_bbox(point, options.min_marker_size)
    if entity_type == "DIMENSION":
        point = _point(data.get("defpoint")) or _point(data.get("position"))
        if point:
            return _point_bbox(point, options.min_marker_size)
    if entity_type == "INSERT":
        point = _point(data.get("insert_point"))
        if point:
            return _point_bbox(point, max(options.min_marker_size, 100.0))

    point = _point(data.get("location"))
    if point:
        return _point_bbox(point, options.min_marker_size)
    return None


def _bbox_from_metadata(metadata: dict[str, Any], options: ChangeZoneOptions) -> Optional[BBox]:
    try:
        raw_bbox = _coerce_bbox(metadata.get("bbox"))
        if raw_bbox:
            return _ensure_min_bbox(raw_bbox, options.min_marker_size)
        if metadata.get("x") is None or metadata.get("y") is None:
            return None
        x = float(metadata["x"])
        y = float(metadata["y"])
        w = float(metadata.get("w") or options.min_marker_size)
        h = float(metadata.get("h") or options.min_marker_size)
        if _metadata_uses_top_left_bbox(metadata):
            return _ensure_min_bbox((x, y, x + w, y + h), options.min_marker_size)
        return _ensure_min_bbox((x - w / 2.0, y - h / 2.0, x + w / 2.0, y + h / 2.0), options.min_marker_size)
    except Exception:
        return None


def _metadata_uses_top_left_bbox(metadata: dict[str, Any]) -> bool:
    source_format = str(metadata.get("source_format") or "").lower()
    entity_type = str(metadata.get("entity_type") or "").upper()
    return source_format == "pdf" or entity_type.startswith("PDF_")


def _bbox_from_location(location: Any, options: ChangeZoneOptions) -> Optional[BBox]:
    if not location:
        return None
    if isinstance(location, (tuple, list)):
        point = _point(location)
        return _point_bbox(point, options.min_marker_size) if point else None
    numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", str(location))
    if len(numbers) >= 2:
        return _point_bbox((float(numbers[0]), float(numbers[1])), options.min_marker_size)
    return None


def _point(value: Any) -> Optional[Tuple[float, float]]:
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        try:
            return (float(value[0]), float(value[1]))
        except Exception:
            return None
    return None


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _coerce_bbox(value: Any) -> Optional[BBox]:
    if isinstance(value, (tuple, list)) and len(value) >= 4:
        try:
            x1, y1, x2, y2 = [float(value[index]) for index in range(4)]
            return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        except Exception:
            return None
    if isinstance(value, dict):
        try:
            if all(key in value for key in ("min_x", "min_y", "max_x", "max_y")):
                x1 = float(value["min_x"])
                y1 = float(value["min_y"])
                x2 = float(value["max_x"])
                y2 = float(value["max_y"])
                return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            if all(key in value for key in ("x_min", "y_min", "x_max", "y_max")):
                x1 = float(value["x_min"])
                y1 = float(value["y_min"])
                x2 = float(value["x_max"])
                y2 = float(value["y_max"])
                return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        except Exception:
            return None
    return None


def _point_bbox(point: Optional[Tuple[float, float]], size: float) -> Optional[BBox]:
    if point is None:
        return None
    half = max(size, 1.0) / 2.0
    return (point[0] - half, point[1] - half, point[0] + half, point[1] + half)


def _bbox_from_points(points: Sequence[Tuple[float, float]], min_size: float) -> BBox:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return _ensure_min_bbox((min(xs), min(ys), max(xs), max(ys)), min_size)


def _ensure_min_bbox(bbox: BBox, min_size: float) -> BBox:
    min_x, min_y, max_x, max_y = bbox
    width = max_x - min_x
    height = max_y - min_y
    if width >= min_size and height >= min_size:
        return bbox
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    half_w = max(width, min_size) / 2.0
    half_h = max(height, min_size) / 2.0
    return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def _inflate_bbox(bbox: BBox, amount: float) -> BBox:
    amount = max(float(amount), 0.0)
    return (bbox[0] - amount, bbox[1] - amount, bbox[2] + amount, bbox[3] + amount)


def _bbox_distance(a: BBox, b: BBox) -> float:
    dx = max(b[0] - a[2], a[0] - b[2], 0.0)
    dy = max(b[1] - a[3], a[1] - b[3], 0.0)
    return math.hypot(dx, dy)


def _bbox_cells(bbox: BBox, cell_size: float) -> Iterable[tuple[int, int]]:
    min_x = math.floor(bbox[0] / cell_size)
    max_x = math.floor(bbox[2] / cell_size)
    min_y = math.floor(bbox[1] / cell_size)
    max_y = math.floor(bbox[3] / cell_size)
    max_span = 256
    if max_x - min_x > max_span or max_y - min_y > max_span:
        cx = math.floor(((bbox[0] + bbox[2]) / 2.0) / cell_size)
        cy = math.floor(((bbox[1] + bbox[3]) / 2.0) / cell_size)
        yield (cx, cy)
        return
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            yield (x, y)


def _union_bboxes(bboxes: Sequence[BBox]) -> BBox:
    return (
        min(bbox[0] for bbox in bboxes),
        min(bbox[1] for bbox in bboxes),
        max(bbox[2] for bbox in bboxes),
        max(bbox[3] for bbox in bboxes),
    )


def _change_type_value(change_type: Any) -> str:
    if isinstance(change_type, ChangeType):
        return change_type.value
    if hasattr(change_type, "value"):
        return str(change_type.value).lower()
    return str(change_type).lower()


def _zone_change_type(type_counts: Counter[str], has_moved_origin: bool) -> str:
    non_zero = [name for name, count in type_counts.items() if count]
    if has_moved_origin and type_counts.get("modified"):
        return "moved"
    if len(non_zero) == 1:
        return non_zero[0]
    return "mixed"


def _change_has_moved_origin(
    change: ChangeRecord,
    bbox: BBox,
    old_bbox: Optional[BBox],
) -> bool:
    if change.change_type != ChangeType.MODIFIED or old_bbox is None:
        return False
    metadata = change.metadata or {}
    if metadata.get("registered_reorigin"):
        return False
    if metadata.get("old_x") is not None and metadata.get("old_y") is not None:
        return True
    return _bbox_distance(bbox, old_bbox) > 1.0


# Annotation/markup entity types whose pure repositioning is review noise, not a
# structural change. A dimension/text that merely MOVED must not be flagged
# critical just for being a DIMENSION.
_ANNOTATION_ENTITY_TYPES = frozenset(
    {
        "TEXT",
        "MTEXT",
        "DIMENSION",
        "ARC_DIMENSION",
        "MULTILEADER",
        "MLEADER",
        "LEADER",
        "ATTRIB",
        "ATTDEF",
    }
)
# Change categories that mean "position-only" (no value/content/size/geometry
# change). Empty category on a ``moved`` zone is treated as position-only. These
# are the fine-grained categories the production comparator (DxfComparator, used
# by DwgDiffer) emits. Do NOT add the canonical drawing_compare_engine's umbrella
# "geometry" category here: it covers BOTH pure moves (geometry.insert/start) AND
# real shape/value changes (geometry.radius/height/vertex_count), so demoting on
# it would silently hide genuine structural changes.
_POSITION_ONLY_CATEGORIES = frozenset({"position", "layer_move", "cosmetic"})


def _zone_is_annotation_reposition(
    envelopes: Sequence[_ChangeEnvelope],
    layer_counts: Counter[str],
    entity_counts: Counter[str],
    change_type: str,
) -> bool:
    """True when a zone is purely a repositioning of annotation/markup
    (text/dimension/leader) on non-structural layers — review noise.

    Such zones are DEMOTED (kept visible at low severity with a reason), never
    dropped, so structural changes surface first without hiding anything.
    """

    if change_type != "moved":
        return False
    entity_types = {str(name).upper() for name in entity_counts if name}
    if not entity_types or not entity_types <= _ANNOTATION_ENTITY_TYPES:
        return False
    from .structural_layer_patterns import is_structural_layer  # local import

    if any(is_structural_layer(layer) for layer in layer_counts if layer):
        return False
    # Every change must be position-only (no content/size/rotation/scale change),
    # so a moved-AND-edited annotation is NOT demoted.
    for item in envelopes:
        change = item.change
        raw = str(
            getattr(change, "change_category", None)
            or (change.metadata or {}).get("change_category")
            or ""
        )
        categories = {part.strip().lower() for part in raw.split(",") if part.strip()}
        if categories and not categories <= _POSITION_ONLY_CATEGORIES:
            return False
    return True


def _zone_severity(
    envelopes: Sequence[_ChangeEnvelope],
    type_counts: Counter[str],
    layer_counts: Counter[str],
    entity_counts: Counter[str],
    *,
    change_type: str = "",
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    # Pure annotation/markup repositioning is layout cleanup, not a structural
    # change — demote (keep visible, ranked low) instead of letting the
    # DIMENSION→critical rule below over-flag every moved dimension.
    if _zone_is_annotation_reposition(envelopes, layer_counts, entity_counts, change_type):
        return "low", ["주석/치수 위치 이동 (annotation reposition)"]
    if entity_counts.get("DIMENSION", 0) > 0:
        return "critical", ["contains dimension changes"]
    structural = any(
        re.search(r"(BEAM|COLUMN|COL|SLAB|WALL|GRID|FOUND|PILE|BRACE)", layer.upper())
        for layer in layer_counts
        if layer
    )
    if structural:
        reasons.append("contains structural layer changes")
    raw_count = len(envelopes)
    if raw_count >= 100 or structural:
        return "high", reasons or ["large clustered change"]
    if raw_count >= 10 or type_counts.get("deleted", 0) or type_counts.get("modified", 0):
        return "medium", reasons
    return "low", reasons


def _default_pair_id(result: ComparisonResult) -> str:
    source = Path(result.source_b or result.source_a)
    return _safe_stem(source.stem or "pair")


def _candidate_drawing_number(candidate: Any) -> str:
    for descriptor in (getattr(candidate, "source_b", None), getattr(candidate, "source_a", None)):
        if descriptor and descriptor.identity and descriptor.identity.drawing_number:
            return str(descriptor.identity.drawing_number)
    return ""


def _safe_pair_id(candidate: Any, index: int) -> str:
    try:
        return candidate_pair_uuid(candidate)
    except Exception:
        drawing_number = _candidate_drawing_number(candidate)
        if drawing_number:
            return _safe_stem(drawing_number)
        descriptor = getattr(candidate, "source_b", None) or getattr(candidate, "source_a", None)
        if descriptor:
            return _safe_stem(Path(descriptor.path).stem)
        return f"pair_{index:03d}"


def _safe_stem(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe.strip("._") or "drawing"


def _display_filename(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        return Path(text).name or text
    except Exception:
        return text


def _result_change_count(result: ComparisonResult) -> int:
    counts = result.metadata.get("change_counts") if result.metadata else None
    if counts:
        return sum(int(counts.get(name, 0) or 0) for name in ("added", "deleted", "modified"))
    return result.total_changes


def _write_change_zones_json(
    path: Path,
    zones: Sequence[DrawingChangeZone],
    package: ChangeArtifactPackage,
) -> None:
    _write_json(
        path,
        {
            "zones": [zone.to_dict() for zone in zones],
            "zone_count": len(zones),
            "zone_input_count": package.zone_input_count,
            "zone_coverage_complete": package.zone_coverage_complete,
            "raw_change_count": package.raw_change_count,
            "cloud_export_mode": package.cloud_export_mode,
            "cloud_region_count": package.cloud_region_count,
            "cloud_omitted_zone_count": package.cloud_omitted_zone_count,
            "warnings": package.warnings,
        },
    )


_RELOCATION_METADATA_KEYS = (
    "relocation_pair_id",
    "relocation_role",
    "relocation_counterpart",
    "relocation_counterpart_bbox",
    "relocation_offset",
)


def _relocation_payload(zone: "DrawingChangeZone") -> dict[str, Any]:
    """Compact relocation-link dict from zone metadata (for CSV/overlay)."""

    return {
        key: zone.metadata[key]
        for key in _RELOCATION_METADATA_KEYS
        if key in zone.metadata
    }


def _write_change_zones_csv(path: Path, zones: Sequence[DrawingChangeZone]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "pair_id",
        "pair_uuid",
        "display_label",
        "zone_id",
        "drawing_number",
        "change_type",
        "severity",
        "status",
        "raw_change_count",
        "added",
        "deleted",
        "modified",
        "bbox_min_x",
        "bbox_min_y",
        "bbox_max_x",
        "bbox_max_y",
        "old_bbox_min_x",
        "old_bbox_min_y",
        "old_bbox_max_x",
        "old_bbox_max_y",
        "layers",
        "entity_types",
        "source_format",
        "detection_source",
        "bbox_status",
        "bbox_coordinate_space",
        "page",
        "page_a",
        "page_b",
        "page_match_status",
        "page_match_score",
        "pdf_dpi",
        "compare_pdf_dpi",
        "effective_dpi",
        "change_category",
        "change_detail",
        "old_text",
        "new_text",
        "old_content",
        "new_content",
        "source_a",
        "source_b",
        "zone_input_source",
        "zone_input_count",
        "zone_coverage_complete",
        "reasons",
        # B안 — entity geometry (JSON) so the reloaded overlay_json path can draw
        # the cloud along the real shape. Appended last to keep column order
        # stable for existing readers.
        "geometry",
        # Relocation-pair link (JSON; see link_relocation_zone_pairs) so the
        # reloaded overlay path keeps the from→to navigation. Appended last.
        "relocation",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for zone in zones:
            old = zone.old_bbox or ("", "", "", "")
            writer.writerow(
                {
                    "pair_id": zone.pair_id,
                    "pair_uuid": zone.pair_uuid or zone.pair_id,
                    "display_label": zone.display_label or zone.drawing_number or zone.pair_id,
                    "zone_id": zone.zone_id,
                    "drawing_number": zone.drawing_number,
                    "change_type": zone.change_type,
                    "severity": zone.severity,
                    "status": zone.status,
                    "raw_change_count": zone.raw_change_count,
                    "added": zone.added_count,
                    "deleted": zone.deleted_count,
                    "modified": zone.modified_count,
                    "bbox_min_x": round(zone.bbox[0], 3),
                    "bbox_min_y": round(zone.bbox[1], 3),
                    "bbox_max_x": round(zone.bbox[2], 3),
                    "bbox_max_y": round(zone.bbox[3], 3),
                    "old_bbox_min_x": round(old[0], 3) if old[0] != "" else "",
                    "old_bbox_min_y": round(old[1], 3) if old[1] != "" else "",
                    "old_bbox_max_x": round(old[2], 3) if old[2] != "" else "",
                    "old_bbox_max_y": round(old[3], 3) if old[3] != "" else "",
                    "layers": " | ".join(zone.layers),
                    "entity_types": " | ".join(zone.entity_types),
                    "source_format": zone.metadata.get("source_format", ""),
                    "detection_source": zone.metadata.get("detection_source", ""),
                    "bbox_status": zone.metadata.get("bbox_status", ""),
                    "bbox_coordinate_space": zone.metadata.get("bbox_coordinate_space", ""),
                    "page": zone.metadata.get("page", ""),
                    "page_a": zone.metadata.get("page_a", ""),
                    "page_b": zone.metadata.get("page_b", ""),
                    "page_match_status": zone.metadata.get("page_match_status", ""),
                    "page_match_score": zone.metadata.get("page_match_score", ""),
                    "pdf_dpi": zone.metadata.get("pdf_dpi", ""),
                    "compare_pdf_dpi": zone.metadata.get("compare_pdf_dpi", ""),
                    "effective_dpi": zone.metadata.get("effective_dpi", ""),
                    "change_category": zone.metadata.get("change_category", ""),
                    "change_detail": zone.metadata.get("change_detail", ""),
                    "old_text": zone.metadata.get("old_text", ""),
                    "new_text": zone.metadata.get("new_text", ""),
                    "old_content": zone.metadata.get("old_content", ""),
                    "new_content": zone.metadata.get("new_content", ""),
                    "source_a": _display_filename(zone.metadata.get("source_a", "")),
                    "source_b": _display_filename(zone.metadata.get("source_b", "")),
                    "zone_input_source": zone.metadata.get("zone_input_source", ""),
                    "zone_input_count": zone.metadata.get("zone_input_count", ""),
                    "zone_coverage_complete": zone.metadata.get("zone_coverage_complete", ""),
                    "reasons": " | ".join(zone.reasons),
                    "geometry": (
                        json.dumps(zone.geometry, separators=(",", ":"))
                        if zone.geometry
                        else ""
                    ),
                    "relocation": (
                        json.dumps(_relocation_payload(zone), separators=(",", ":"))
                        if zone.metadata.get("relocation_pair_id")
                        else ""
                    ),
                }
            )


def _write_cloud_omitted_zones_csv(
    path: Path,
    omitted_zones: Sequence[tuple[DrawingChangeZone, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "pair_id",
        "pair_uuid",
        "display_label",
        "zone_id",
        "drawing_number",
        "severity",
        "raw_change_count",
        "bbox",
        "omitted_reason",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for zone, reason in omitted_zones:
            writer.writerow(
                {
                    "pair_id": zone.pair_id,
                    "pair_uuid": zone.pair_uuid or zone.pair_id,
                    "display_label": zone.display_label or zone.drawing_number or zone.pair_id,
                    "zone_id": zone.zone_id,
                    "drawing_number": zone.drawing_number,
                    "severity": zone.severity,
                    "raw_change_count": zone.raw_change_count,
                    "bbox": _bbox_text(zone.bbox),
                    "omitted_reason": reason,
                }
            )


def _write_review_index(
    path: Path,
    package: ChangeArtifactPackage,
    zones: Sequence[DrawingChangeZone],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact_by_pair = {artifact.pair_id: artifact for artifact in package.artifacts}
    rows = []
    for zone in zones:
        artifact = artifact_by_pair.get(zone.pair_id)
        after_link = _relative_link(path, artifact.after_marked_dxf) if artifact else ""
        before_link = _relative_link(path, artifact.before_marked_dxf) if artifact else ""
        rows.append(
            "<tr>"
            f"<td>{html.escape(zone.pair_id)}</td>"
            f"<td>{html.escape(zone.zone_id)}</td>"
            f"<td>{html.escape(zone.drawing_number)}</td>"
            f"<td>{html.escape(zone.change_type)}</td>"
            f"<td>{html.escape(zone.severity)}</td>"
            f"<td>{zone.raw_change_count}</td>"
            f"<td>{zone.added_count}</td><td>{zone.deleted_count}</td><td>{zone.modified_count}</td>"
            f"<td>{html.escape(', '.join(zone.layers))}</td>"
            f"<td>{html.escape(_bbox_text(zone.bbox))}</td>"
            f"<td>{html.escape(str(zone.metadata.get('zone_input_source', '')))}</td>"
            f"<td>{html.escape(str(zone.metadata.get('zone_coverage_complete', '')))}</td>"
            f"<td>{_artifact_anchor(after_link, 'after')}</td>"
            f"<td>{_artifact_anchor(before_link, 'before')}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="15" class="muted">No change zones.</td></tr>')
    document = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Drawing Change Review</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #222; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 7px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .metric {{ border: 1px solid #d0d7de; border-radius: 6px; padding: 10px; }}
    .metric strong {{ display: block; font-size: 18px; margin-top: 4px; }}
    .muted {{ color: #57606a; }}
  </style>
</head>
<body>
  <h1>Drawing Change Review</h1>
  <p class="muted">Generated at {html.escape(package.generated_at)}</p>
  <div class="grid">
     {_metric("Compared pairs", package.pair_count)}
     {_metric("Change zones", package.zone_count)}
     {_metric("Raw changes", package.raw_change_count)}
     {_metric("Zone input records", package.zone_input_count)}
     {_metric("Coverage complete", package.zone_coverage_complete)}
     {_metric("Cloud mode", package.cloud_export_mode)}
     {_metric("Cloud regions", package.cloud_region_count)}
     {_metric("Omitted zones", package.cloud_omitted_zone_count)}
   </div>
  <table>
    <thead>
      <tr><th>Pair</th><th>Zone</th><th>Drawing No.</th><th>Type</th><th>Severity</th>
      <th>Raw</th><th>Added</th><th>Deleted</th><th>Modified</th><th>Layers</th>
      <th>BBox</th><th>Input</th><th>Coverage</th><th>After Marked</th><th>Before Marked</th></tr>
    </thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def _write_change_register(
    path: Path,
    zones: Sequence[DrawingChangeZone],
    package: ChangeArtifactPackage,
) -> None:
    try:
        import openpyxl
    except ImportError:
        package.warnings.append("openpyxl is not installed; change_register.xlsx was not written")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Change Zones"
    ws.append(
        [
            "Pair ID",
            "Zone ID",
            "Drawing Number",
            "Type",
            "Severity",
            "Status",
            "Raw Changes",
            "Added",
            "Deleted",
            "Modified",
            "Layers",
            "Entity Types",
            "BBox",
            "Source Format",
            "Detection Source",
            "BBox Status",
            "Change Detail",
            "Old Text",
            "New Text",
            "Zone Input Source",
            "Zone Input Count",
            "Coverage Complete",
            "Source A",
            "Source B",
        ]
    )
    for zone in zones:
        ws.append(
            [
                zone.pair_id,
                zone.zone_id,
                zone.drawing_number,
                zone.change_type,
                zone.severity,
                zone.status,
                zone.raw_change_count,
                zone.added_count,
                zone.deleted_count,
                zone.modified_count,
                " | ".join(zone.layers),
                " | ".join(zone.entity_types),
                _bbox_text(zone.bbox),
                zone.metadata.get("source_format", ""),
                zone.metadata.get("detection_source", ""),
                zone.metadata.get("bbox_status", ""),
                zone.metadata.get("change_detail", ""),
                zone.metadata.get("old_text", ""),
                zone.metadata.get("new_text", ""),
                zone.metadata.get("zone_input_source", ""),
                zone.metadata.get("zone_input_count", ""),
                zone.metadata.get("zone_coverage_complete", ""),
                _display_filename(zone.metadata.get("source_a", "")),
                _display_filename(zone.metadata.get("source_b", "")),
            ]
        )

    run_ws = wb.create_sheet("Run Summary")
    run_ws.append(["Metric", "Value"])
    run_ws.append(["Compared Pairs", package.pair_count])
    run_ws.append(["Change Zones", package.zone_count])
    run_ws.append(["Raw Changes", package.raw_change_count])
    run_ws.append(["Zone Input Records", package.zone_input_count])
    run_ws.append(["Coverage Complete", package.zone_coverage_complete])
    run_ws.append(["Cloud Export Mode", package.cloud_export_mode])
    run_ws.append(["Cloud Regions", package.cloud_region_count])
    run_ws.append(["Cloud Omitted Zones", package.cloud_omitted_zone_count])
    wb.save(str(path))


def _read_change_zone_csv_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(dict(row))
    return rows


def _artifact_lookup_from_manifest(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for artifact in manifest.get("artifacts", []) or []:
        pair_id = str(artifact.get("pair_id") or "")
        if pair_id:
            lookup[pair_id] = artifact
    return lookup


def _summarize_executive_drawings(
    zone_rows: Sequence[dict[str, Any]],
    artifact_by_pair: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    layer_counters: dict[str, Counter[str]] = defaultdict(Counter)
    for row in zone_rows:
        pair_id = str(row.get("pair_id") or row.get("drawing_number") or "")
        drawing_number = str(row.get("drawing_number") or pair_id)
        key = pair_id or drawing_number
        artifact = artifact_by_pair.get(pair_id, {})
        summary = summaries.setdefault(
            key,
            {
                "pair_id": pair_id,
                "drawing_number": drawing_number,
                "zone_count": 0,
                "raw_change_count": 0,
                "high_zone_count": 0,
                "medium_zone_count": 0,
                "low_zone_count": 0,
                "added_count": 0,
                "deleted_count": 0,
                "modified_count": 0,
                "cloud_region_count": _int_cell(artifact.get("cloud_region_count")),
                "cloud_omitted_zone_count": _int_cell(artifact.get("cloud_omitted_zone_count")),
                "after_marked_dxf": artifact.get("after_marked_dxf") or "",
            },
        )
        summary["zone_count"] += 1
        summary["raw_change_count"] += _int_cell(row.get("raw_change_count"))
        summary["added_count"] += _int_cell(row.get("added"))
        summary["deleted_count"] += _int_cell(row.get("deleted"))
        summary["modified_count"] += _int_cell(row.get("modified"))
        severity = str(row.get("severity") or "").lower()
        if severity == "high":
            summary["high_zone_count"] += 1
        elif severity == "low":
            summary["low_zone_count"] += 1
        else:
            summary["medium_zone_count"] += 1
        layer_counters[key].update(_split_layers(row.get("layers")))

    for key, summary in summaries.items():
        summary["top_layers"] = " | ".join(layer for layer, _count in layer_counters[key].most_common(4))
    return sorted(
        summaries.values(),
        key=lambda item: (
            -_int_cell(item.get("raw_change_count")),
            -_int_cell(item.get("zone_count")),
            str(item.get("drawing_number") or ""),
        ),
    )


def _top_executive_zones(
    zone_rows: Sequence[dict[str, Any]],
    artifact_by_pair: dict[str, dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in zone_rows:
        pair_id = str(row.get("pair_id") or "")
        artifact = artifact_by_pair.get(pair_id, {})
        enriched.append(
            {
                "pair_id": pair_id,
                "drawing_number": str(row.get("drawing_number") or pair_id),
                "zone_id": str(row.get("zone_id") or ""),
                "change_type": str(row.get("change_type") or ""),
                "severity": str(row.get("severity") or ""),
                "raw_change_count": _int_cell(row.get("raw_change_count")),
                "added_count": _int_cell(row.get("added")),
                "deleted_count": _int_cell(row.get("deleted")),
                "modified_count": _int_cell(row.get("modified")),
                "layers": _short_layers(_split_layers(row.get("layers"))),
                "bbox": _bbox_from_zone_row(row),
                "after_marked_dxf": artifact.get("after_marked_dxf") or "",
            }
        )
    return sorted(
        enriched,
        key=lambda item: (
            -_int_cell(item.get("raw_change_count")),
            -_severity_rank(item.get("severity")),
            str(item.get("drawing_number") or ""),
            str(item.get("zone_id") or ""),
        ),
    )[:limit]


def _summarize_repeated_layer_patterns(zone_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    pattern_defs = (
        ("AA-DETL-PCN8", "AA-DETL-PCN8"),
        ("AA-XXXX-*", "AA-XXXX"),
    )
    summaries: dict[str, dict[str, Any]] = {}
    layer_counters: dict[str, Counter[str]] = defaultdict(Counter)
    drawings_by_pattern: dict[str, set[str]] = defaultdict(set)
    for row in zone_rows:
        layers = _split_layers(row.get("layers"))
        if not layers:
            continue
        raw = _int_cell(row.get("raw_change_count"))
        drawing = str(row.get("drawing_number") or row.get("pair_id") or "")
        for label, prefix in pattern_defs:
            matched_layers = [layer for layer in layers if layer.upper().startswith(prefix)]
            if not matched_layers:
                continue
            summary = summaries.setdefault(
                label,
                {
                    "pattern": label,
                    "zone_count": 0,
                    "raw_change_count": 0,
                    "affected_drawing_count": 0,
                    "top_layers": "",
                    "interpretation": "Repeated detail/reference layer changes; review as a pattern before inspecting every zone.",
                },
            )
            summary["zone_count"] += 1
            summary["raw_change_count"] += raw
            drawings_by_pattern[label].add(drawing)
            layer_counters[label].update(matched_layers)
    for label, summary in summaries.items():
        summary["affected_drawing_count"] = len(drawings_by_pattern[label])
        summary["top_layers"] = " | ".join(layer for layer, _count in layer_counters[label].most_common(5))
    return sorted(
        summaries.values(),
        key=lambda item: (-_int_cell(item.get("raw_change_count")), str(item.get("pattern") or "")),
    )


def _write_executive_brief_csv(path: Path, drawing_rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "drawing_number",
        "pair_id",
        "raw_change_count",
        "zone_count",
        "high_zone_count",
        "medium_zone_count",
        "low_zone_count",
        "added_count",
        "deleted_count",
        "modified_count",
        "top_layers",
        "cloud_region_count",
        "cloud_omitted_zone_count",
        "after_marked_dxf",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in drawing_rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _write_executive_brief_md(
    path: Path,
    package: ExecutiveReviewPackage,
    manifest: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 도면 변경 요약",
        "",
        f"- 생성 시각: {package.generated_at}",
        f"- 비교 완료 도면: {manifest.get('pair_count', package.drawing_count)}",
        f"- 원시 변경: {package.raw_change_count:,}",
        f"- 변경구역: {package.zone_count:,}",
        f"- 위치 보존 완료: {package.zone_coverage_complete}",
        f"- 구름마크 출력: {package.cloud_region_count:,}",
        f"- 구름마크 생략: {package.cloud_omitted_zone_count:,}",
        "",
        "## 가장 먼저 볼 도면",
        "",
        "| 도면 | 원시 변경 | 변경구역 | 높음 | 보통 | 추가 | 삭제 | 수정 | 주요 레이어 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in package.top_drawings:
        lines.append(
            "| {drawing} | {raw:,} | {zones:,} | {high:,} | {medium:,} | {added:,} | {deleted:,} | {modified:,} | {layers} |".format(
                drawing=row.get("drawing_number") or row.get("pair_id") or "",
                raw=_int_cell(row.get("raw_change_count")),
                zones=_int_cell(row.get("zone_count")),
                high=_int_cell(row.get("high_zone_count")),
                medium=_int_cell(row.get("medium_zone_count")),
                added=_int_cell(row.get("added_count")),
                deleted=_int_cell(row.get("deleted_count")),
                modified=_int_cell(row.get("modified_count")),
                layers=str(row.get("top_layers") or "").replace("|", "/"),
            )
        )
    if package.repeated_patterns:
        lines.extend(["", "## 반복 패턴 변경", ""])
        for pattern in package.repeated_patterns:
            lines.append(
                "- {pattern}: {drawings:,}개 도면, {zones:,}개 변경구역, 원시 변경 {raw:,}. 레이어: {layers}".format(
                    pattern=pattern.get("pattern", ""),
                    raw=_int_cell(pattern.get("raw_change_count")),
                    drawings=_int_cell(pattern.get("affected_drawing_count")),
                    zones=_int_cell(pattern.get("zone_count")),
                    layers=pattern.get("top_layers", ""),
                )
            )
    if package.warnings:
        lines.extend(["", "## 경고", ""])
        lines.extend(f"- {warning}" for warning in package.warnings)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_executive_review_html(
    path: Path,
    package: ExecutiveReviewPackage,
    manifest: dict[str, Any],
    *,
    dashboard: Optional[dict[str, Any]] = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dashboard = dashboard or {}
    dashboard_drawings = dashboard.get("drawings") or []
    dashboard_issues = dashboard.get("top_project_issues") or []
    dashboard_patterns = dashboard.get("layer_patterns") or []
    top_drawings_html = "".join(
        _executive_dashboard_drawing_row(path, row)
        for row in (dashboard_drawings[:15] if dashboard_drawings else package.top_drawings)
    )
    if not top_drawings_html:
        top_drawings_html = '<tr><td colspan="10" class="muted">요약할 도면이 없습니다.</td></tr>'
    top_issues_html = "".join(
        _executive_dashboard_issue_row(path, row)
        for row in (dashboard_issues if dashboard_issues else package.top_zones)
    )
    if not top_issues_html:
        top_issues_html = '<tr><td colspan="11" class="muted">우선 검토 변경구역이 없습니다.</td></tr>'
    pattern_html = "".join(
        _executive_dashboard_pattern_row(row)
        for row in (dashboard_patterns if dashboard_patterns else package.repeated_patterns)
    )
    if not pattern_html:
        pattern_html = '<tr><td colspan="6" class="muted">접어서 표시할 반복 패턴이 없습니다.</td></tr>'
    output_paths = package.output_paths
    change_zones_link = _relative_link(path, str(Path(package.output_dir) / "change_zones.csv"))
    review_index_link = _relative_link(path, str(Path(package.output_dir) / "review_index.html"))
    cloud_dir_link = _relative_link(path, str(Path(package.output_dir) / "cloud_marked"))
    manifest_link = _relative_link(path, str(Path(package.output_dir) / "artifact_manifest.json"))
    csv_link = _relative_link(path, output_paths.get("drawing_change_brief_csv"))
    md_link = _relative_link(path, output_paths.get("drawing_change_brief_md"))
    dashboard_link = _relative_link(path, output_paths.get("review_dashboard_json"))
    priority_link = _relative_link(path, output_paths.get("review_priority_csv"))
    pattern_link = _relative_link(path, output_paths.get("layer_pattern_summary_csv"))
    quality_text = "완전" if package.zone_coverage_complete else "불완전"
    dashboard_totals = dashboard.get("totals") if isinstance(dashboard.get("totals"), dict) else {}
    issue_count = _int_cell(dashboard_totals.get("review_issue_count"), len(dashboard_issues))
    pattern_count = _int_cell(dashboard_totals.get("folded_pattern_count"), len(dashboard_patterns))
    document = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>도면 변경 검토 요약</title>
  <style>
    body {{ font-family: "Segoe UI", "Malgun Gothic", Arial, sans-serif; margin: 0; color: #111827; background: #F7F8FA; }}
    header {{ background: #ffffff; border-bottom: 1px solid #9CA3AF; padding: 22px 28px; }}
    main {{ padding: 22px 28px 40px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; }}
    h2 {{ margin-top: 28px; font-size: 18px; }}
    p {{ line-height: 1.5; }}
    .muted {{ color: #374151; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-top: 16px; }}
    .card {{ background: #ffffff; border: 1px solid #9CA3AF; border-radius: 8px; padding: 12px; }}
    .card span {{ color: #374151; font-size: 12px; }}
    .card strong {{ display: block; margin-top: 6px; font-size: 22px; }}
    .status-ok {{ color: #116329; font-weight: 700; }}
    .status-warn {{ color: #8A3A00; font-weight: 700; }}
    table {{ border-collapse: collapse; width: 100%; background: #ffffff; border: 1px solid #9CA3AF; }}
    th, td {{ border-bottom: 1px solid #D1D5DB; padding: 8px 9px; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ background: #EEF2F7; font-weight: 700; color: #111827; }}
    tr:last-child td {{ border-bottom: 0; }}
    .number {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .pill {{ display: inline-block; border: 1px solid #6B7280; border-radius: 999px; padding: 1px 7px; font-size: 12px; background: #F9FAFB; color: #111827; }}
    .links a {{ margin-right: 12px; }}
    .notice {{ border-left: 4px solid #005FCC; background: #ffffff; padding: 12px 14px; margin: 18px 0; }}
    a {{ color: #005FCC; }}
  </style>
</head>
<body>
  <header>
    <h1>도면 변경 검토 요약</h1>
    <div class="muted">생성 시각: {html.escape(package.generated_at)}</div>
    <p>비교 결과는 완료되었고, 이 화면은 전체 {package.zone_count:,}개 변경구역을 모두 펼치지 않고 사용자가 먼저 확인할 도면과 변경구역을 우선순위로 정리합니다.</p>
    <div class="links">
      {_artifact_anchor(priority_link, "우선 검토 CSV")}
      {_artifact_anchor(pattern_link, "반복 패턴 CSV")}
      {_artifact_anchor(dashboard_link, "검토 대시보드 JSON")}
      {_artifact_anchor(change_zones_link, "전체 변경구역 CSV")}
      {_artifact_anchor(review_index_link, "전체 HTML 상세")}
      {_artifact_anchor(cloud_dir_link, "구름마크 DXF 폴더")}
      {_artifact_anchor(csv_link, "도면별 요약 CSV")}
      {_artifact_anchor(md_link, "도면별 요약 MD")}
      {_artifact_anchor(manifest_link, "산출물 manifest")}
    </div>
  </header>
  <main>
    <h2>이번 비교 판정</h2>
    <div class="cards">
      {_metric("비교 완료 도면", manifest.get("pair_count", package.drawing_count))}
      {_metric("원시 변경", f"{package.raw_change_count:,}")}
      {_metric("변경구역", f"{package.zone_count:,}")}
      {_metric("위치 보존", quality_text)}
      {_metric("우선 검토", f"{issue_count:,}")}
      {_metric("반복 패턴", f"{pattern_count:,}")}
      {_metric("구름마크 출력", f"{package.cloud_region_count:,}")}
      {_metric("구름마크 생략", f"{package.cloud_omitted_zone_count:,}")}
    </div>
    <div class="notice">
      전체 변경 데이터는 CSV/Excel/JSON에 보존되어 있습니다. 구름마크 DXF는 selected mode 정책에 따라 우선순위 기반으로 출력되며, 생략된 변경구역은 실패가 아니라 과도한 출력량을 막기 위한 정책입니다.
    </div>
    <h2>가장 먼저 볼 도면</h2>
    <table>
      <thead>
        <tr><th>도면</th><th class="number">우선순위</th><th class="number">원시 변경</th><th class="number">변경구역</th><th class="number">우선 검토</th><th class="number">접힌 항목</th><th>주요 레이어</th><th>미리보기</th><th class="number">구름마크</th><th>구름마크 도면</th></tr>
      </thead>
      <tbody>{top_drawings_html}</tbody>
    </table>
    <h2>우선 검토 변경구역</h2>
    <table>
      <thead>
        <tr><th>도면</th><th>Zone</th><th>유형</th><th>심각도</th><th class="number">점수</th><th class="number">원시 변경</th><th>주요 레이어</th><th>위치</th><th>우선 검토 이유</th><th>미리보기</th><th>구름마크 도면</th></tr>
      </thead>
      <tbody>{top_issues_html}</tbody>
    </table>
    <h2>반복 패턴 변경</h2>
    <table>
      <thead>
        <tr><th>패턴</th><th class="number">원시 변경</th><th class="number">변경구역</th><th class="number">영향 도면</th><th>대표 도면/Zone</th><th>검토 방법</th></tr>
      </thead>
      <tbody>{pattern_html}</tbody>
    </table>
    <h2>구름마크가 생략된 이유</h2>
    <p>구름마크는 모든 변경구역에 무제한 생성하지 않습니다. 전체 변경구역은 보존하고, DXF 출력은 중요도와 상한을 적용해 CAD에서 열 수 있는 크기로 제한합니다. 생략된 항목은 <code>cloud_omitted_zones.csv</code>와 전체 변경구역 CSV에서 추적합니다.</p>
    <h2>상세 자료 링크</h2>
    <p class="links">
      {_artifact_anchor(priority_link, "우선 검토 CSV")}
      {_artifact_anchor(pattern_link, "반복 패턴 CSV")}
      {_artifact_anchor(change_zones_link, "전체 변경구역 CSV")}
      {_artifact_anchor(cloud_dir_link, "구름마크 DXF 폴더")}
    </p>
  </main>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def _executive_dashboard_drawing_row(index_path: Path, row: dict[str, Any]) -> str:
    marked_link = _relative_link(index_path, row.get("after_marked_dxf"))
    preview = "가능" if row.get("preview_available") else "미리보기 실패"
    return (
        "<tr>"
        f"<td>{html.escape(str(row.get('drawing_number') or row.get('pair_id') or ''))}</td>"
        f"<td class=\"number\">{float(row.get('priority_score') or 0.0):.1f}</td>"
        f"<td class=\"number\">{_int_cell(row.get('raw_change_count')):,}</td>"
        f"<td class=\"number\">{_int_cell(row.get('zone_count')):,}</td>"
        f"<td class=\"number\">{_int_cell(row.get('review_issue_count')):,}</td>"
        f"<td class=\"number\">{_int_cell(row.get('folded_issue_count')):,}</td>"
        f"<td>{html.escape(str(row.get('major_layers') or row.get('top_layers') or ''))}</td>"
        f"<td>{html.escape(preview)}</td>"
        f"<td class=\"number\">{_int_cell(row.get('cloud_region_count')):,}</td>"
        f"<td>{_artifact_anchor(marked_link, 'DXF')}</td>"
        "</tr>"
    )


def _executive_dashboard_issue_row(index_path: Path, row: dict[str, Any]) -> str:
    marked_link = _relative_link(index_path, row.get("after_marked_dxf"))
    preview = "가능" if row.get("preview_available") else "미리보기 실패"
    return (
        "<tr>"
        f"<td>{html.escape(str(row.get('drawing_number') or row.get('pair_id') or ''))}</td>"
        f"<td>{html.escape(str(row.get('zone_id') or ''))}</td>"
        f"<td>{html.escape(str(row.get('change_type_ko') or row.get('change_type') or ''))}</td>"
        f"<td><span class=\"pill\">{html.escape(str(row.get('severity_ko') or row.get('severity') or ''))}</span></td>"
        f"<td class=\"number\">{float(row.get('priority_score') or 0.0):.1f}</td>"
        f"<td class=\"number\">{_int_cell(row.get('raw_change_count')):,}</td>"
        f"<td>{html.escape(str(row.get('major_layers') or row.get('layers') or ''))}</td>"
        f"<td>{html.escape(str(row.get('bbox_text') or row.get('bbox') or ''))}</td>"
        f"<td>{html.escape(str(row.get('priority_reason_ko') or ''))}</td>"
        f"<td>{html.escape(preview)}</td>"
        f"<td>{_artifact_anchor(marked_link, 'DXF')}</td>"
        "</tr>"
    )


def _executive_dashboard_pattern_row(row: dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td>{html.escape(str(row.get('pattern') or ''))}</td>"
        f"<td class=\"number\">{_int_cell(row.get('raw_change_count')):,}</td>"
        f"<td class=\"number\">{_int_cell(row.get('zone_count')):,}</td>"
        f"<td class=\"number\">{_int_cell(row.get('affected_drawing_count')):,}</td>"
        f"<td>{html.escape(str(row.get('representative_zones') or row.get('top_drawings') or ''))}</td>"
        f"<td>{html.escape(str(row.get('interpretation_ko') or row.get('interpretation') or ''))}<br><span class=\"muted\">{html.escape(str(row.get('top_layers') or ''))}</span></td>"
        "</tr>"
    )


def _update_manifest_with_executive_outputs(
    manifest_path: Path,
    package: ExecutiveReviewPackage,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outputs = manifest.setdefault("output_paths", {})
    outputs.update(package.output_paths)
    manifest["executive_review_html"] = package.output_paths.get("executive_review_html", "")
    manifest["drawing_change_brief_md"] = package.output_paths.get("drawing_change_brief_md", "")
    manifest["drawing_change_brief_csv"] = package.output_paths.get("drawing_change_brief_csv", "")
    manifest["executive_review"] = package.to_dict()
    _write_json(manifest_path, manifest)


def _int_cell(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _split_layers(value: Any) -> list[str]:
    layers = []
    for raw in str(value or "").split("|"):
        layer = raw.strip()
        if layer:
            layers.append(layer)
    return layers


def _short_layers(layers: Sequence[str], *, limit: int = 4, max_chars: int = 90) -> str:
    text = " | ".join(layers[:limit])
    if len(layers) > limit:
        text += f" | +{len(layers) - limit}"
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def _severity_rank(value: Any) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(str(value or "").lower(), 0)


def _bbox_from_zone_row(row: dict[str, Any]) -> str:
    values = [
        row.get("bbox_min_x"),
        row.get("bbox_min_y"),
        row.get("bbox_max_x"),
        row.get("bbox_max_y"),
    ]
    if all(value not in (None, "") for value in values):
        try:
            return ", ".join(f"{float(value):.1f}" for value in values)
        except (TypeError, ValueError):
            pass
    return ""


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _metric(label: str, value: Any) -> str:
    return f'<div class="metric"><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></div>'


def _bbox_text(bbox: BBox) -> str:
    return ", ".join(f"{value:.1f}" for value in bbox)


def _artifact_anchor(link: str, label: str) -> str:
    if not link:
        return ""
    return f'<a href="{html.escape(link)}">{html.escape(label)}</a>'


def _relative_link(index_path: Path, target: Optional[str]) -> str:
    if not target:
        return ""
    try:
        return str(Path(target).resolve().relative_to(index_path.parent.resolve())).replace("\\", "/")
    except Exception:
        return str(target).replace("\\", "/")
