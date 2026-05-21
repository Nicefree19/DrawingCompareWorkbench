# -*- coding: utf-8 -*-
"""Phase I3 — Cluster repeated change zones into a single representative row.

After ``zone_classifier`` buckets zones into AI categories, this module
folds **near-duplicate zones inside the same category** into clusters
so the GUI list isn't drowned by 47 nearly-identical "DIM 레이어 텍스트
수정" rows. The reviewer sees one row "[12] DIM 레이어 텍스트 수정"
that they can expand to inspect individual zones.

This is a presentation-layer concern only — the underlying ChangeZone
data is not modified. The classifier and the manual review state stay
unaware of clustering; the GUI populator decides whether to fold.

Clustering keys (all must match for two zones to land in the same cluster):
    1. ``change_type``      (added / deleted / modified / mixed)
    2. ``severity``         (minor / normal / major / …)
    3. ``entity_type``      (TEXT / LINE / INSERT / …)
    4. ``layer_prefix``     — see ``_layer_prefix`` (strips trailing digits
                              so GRID-X1, GRID-X2, GRID-X3 cluster together)

Singletons (``min_cluster_size = 3`` by default) are NOT clustered — a
single zone deserves its own row, and a pair of two related zones is
arguably easier to scan than a "[2]" cluster header. Tunable via the
``ClusterOptions`` dataclass.

The output preserves the input order so the caller (the GUI populator)
keeps its existing severity-then-priority sort.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# Trailing-digit stripper used to fold GRID-X1, GRID-X2, ... into GRID-X*.
# Also folds CHAR + DIGITS at the end:  BEAM-2F   → BEAM-*F (no, keep prefix)
# We choose the simpler "strip trailing digits" because it matches the
# common naming convention well without over-clustering.
_TRAILING_DIGITS_RE = re.compile(r"\d+$")


@dataclass(frozen=True)
class ClusterOptions:
    """Tunable knobs for the clusterer.

    All defaults chosen so a 47-zone drawing typically folds to ~10-15
    rows when the changes are dominated by repeated patterns (DIM,
    GRID-Y*, etc.).
    """

    min_cluster_size: int = 3
    """Singletons and pairs stay un-clustered (own row each)."""

    enabled: bool = True
    """When False, every zone becomes its own singleton — useful for
    the "고급 모드" GUI toggle that bypasses clustering."""


@dataclass
class ZoneCluster:
    """One cluster — either a single zone (singleton) or a fold of N>=
    ``min_cluster_size`` near-duplicates."""

    cluster_key: str
    """Stable string key — same across runs for the same input."""

    representative: dict
    """Pick one zone (the first in input order) to drive label rendering."""

    members: list[dict] = field(default_factory=list)
    """All zones folded into this cluster (includes the representative).
    For singletons, ``len(members) == 1``."""

    summary_label: str = ""
    """Pre-formatted Korean label, e.g. ``[12] DIM 레이어 텍스트 수정``.
    Empty for singletons — the caller already has its own label format
    for individual zones."""

    @property
    def is_singleton(self) -> bool:
        return len(self.members) <= 1

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def member_zone_ids(self) -> list[str]:
        return [str(z.get("zone_id") or "") for z in self.members]


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def cluster_zones(
    zones: list[dict],
    *,
    options: Optional[ClusterOptions] = None,
) -> list[ZoneCluster]:
    """Group a category's zones into clusters of near-duplicates.

    The caller is expected to pass zones from ONE category at a time —
    cross-category clustering is intentionally not supported (we want
    the AI category boundaries to remain visible to the user).

    Returns a list of ``ZoneCluster`` in the order each cluster's first
    member appeared in the input. Singletons (when count <
    ``min_cluster_size``) appear as 1-member clusters with empty
    ``summary_label`` so the GUI knows to render them inline.
    """

    opts = options or ClusterOptions()
    if not zones:
        return []
    if not opts.enabled:
        return [_singleton_for(zone) for zone in zones if isinstance(zone, dict)]

    # Group by clustering key while preserving first-seen order.
    buckets: dict[str, list[dict]] = {}
    bucket_order: list[str] = []
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        key = _cluster_key_for(zone)
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(zone)

    out: list[ZoneCluster] = []
    for key in bucket_order:
        members = buckets[key]
        if len(members) < opts.min_cluster_size:
            # Each zone stays its own singleton, preserving order
            for zone in members:
                out.append(_singleton_for(zone))
        else:
            rep = members[0]
            label = _summary_label_for_cluster(rep, len(members))
            out.append(ZoneCluster(
                cluster_key=key,
                representative=rep,
                members=list(members),
                summary_label=label,
            ))
    return out


# ---------------------------------------------------------------------------
# Clustering key
# ---------------------------------------------------------------------------


def _cluster_key_for(zone: dict) -> str:
    """Build the stable clustering key from the 4 dimensions."""

    change_type = str(zone.get("change_type") or "").lower().strip()
    severity = str(zone.get("severity") or "").lower().strip()
    entity_type = str(zone.get("entity_type") or "").upper().strip()
    layer_prefix = _layer_prefix(zone)
    return f"{change_type}|{severity}|{entity_type}|{layer_prefix}"


def _layer_prefix(zone: dict) -> str:
    """Strip trailing digits from the primary layer name.

    GRID-X1  → GRID-X
    GRID-X12 → GRID-X
    BEAM     → BEAM
    DIM-A    → DIM-A
    "" / missing → ""
    """

    layer = str(zone.get("layer") or "").strip()
    if not layer:
        # Fall back to the first ``top_layers`` entry if available
        top_layers = zone.get("top_layers")
        if isinstance(top_layers, (list, tuple)) and top_layers:
            layer = str(top_layers[0] or "").strip()
    if not layer:
        return ""
    # Strip any trailing run of digits ("GRID-X12" → "GRID-X")
    return _TRAILING_DIGITS_RE.sub("", layer)


# ---------------------------------------------------------------------------
# Singleton + label
# ---------------------------------------------------------------------------


def _singleton_for(zone: dict) -> ZoneCluster:
    return ZoneCluster(
        cluster_key=f"singleton:{zone.get('zone_id') or id(zone)}",
        representative=zone,
        members=[zone],
        summary_label="",  # signal to caller: render the zone's own label
    )


def _summary_label_for_cluster(representative: dict, count: int) -> str:
    """Build a Korean cluster summary line.

    Format: ``[N] LAYER_PREFIX · CHANGE_TYPE_KO · ENTITY_TYPE``
        e.g.  ``[12] DIM-A · 수정 · TEXT``
              ``[5] GRID-X · 추가 · LINE``

    The leading ``[N]`` makes the count visible at a glance; the layer
    prefix is the primary anchor users recognise on a structural
    drawing.
    """

    layer = _layer_prefix(representative)
    change_ko = _change_type_phrase(str(representative.get("change_type") or ""))
    entity = str(representative.get("entity_type") or "").upper()

    parts = [f"[{count}]"]
    if layer:
        parts.append(layer)
    if change_ko:
        parts.append(change_ko)
    if entity and entity not in ("UNKNOWN", "NONE"):
        parts.append(entity)
    return " · ".join(parts)


def _change_type_phrase(change_type: str) -> str:
    return {
        "added": "추가",
        "deleted": "삭제",
        "modified": "수정",
        "moved": "이동",
        "mixed": "혼합",
    }.get(str(change_type or "").lower(), str(change_type or ""))


__all__ = [
    "ClusterOptions",
    "ZoneCluster",
    "cluster_zones",
]
