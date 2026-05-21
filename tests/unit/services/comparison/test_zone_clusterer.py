# -*- coding: utf-8 -*-
"""Unit tests for the Phase I3 zone clusterer.

Covers ``zone_clusterer.cluster_zones`` which folds repeated near-duplicate
change zones inside a single AI category into one row. Pure Python, no Qt.
"""

from __future__ import annotations

import pytest

from src.services.comparison.zone_clusterer import (
    ClusterOptions,
    ZoneCluster,
    cluster_zones,
)


def _z(zid: str, **kw) -> dict:
    """Shorthand zone-dict builder with sensible defaults."""

    base = {
        "zone_id": zid,
        "change_type": "modified",
        "severity": "minor",
        "entity_type": "TEXT",
        "layer": "DIM-A",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Empty / boundary
# ---------------------------------------------------------------------------


def test_empty_zones_returns_empty() -> None:
    assert cluster_zones([]) == []


def test_non_dict_entries_silently_skipped() -> None:
    out = cluster_zones([_z("z1"), "garbage", None, _z("z2")])  # type: ignore[list-item]
    # 2 zones, both singletons (default min_cluster_size=3)
    assert len(out) == 2
    assert all(c.is_singleton for c in out)
    assert [c.representative["zone_id"] for c in out] == ["z1", "z2"]


# ---------------------------------------------------------------------------
# Singleton behavior
# ---------------------------------------------------------------------------


def test_pair_stays_two_singletons_by_default() -> None:
    """min_cluster_size=3 → a pair of identicals stays unclustered."""

    out = cluster_zones([_z("z1"), _z("z2")])
    assert len(out) == 2
    assert all(c.is_singleton for c in out)
    assert all(c.summary_label == "" for c in out)


def test_singleton_flag_and_size() -> None:
    out = cluster_zones([_z("z1")])
    assert len(out) == 1
    c = out[0]
    assert c.is_singleton
    assert c.size == 1
    assert c.member_zone_ids == ["z1"]


# ---------------------------------------------------------------------------
# Clustering — same key
# ---------------------------------------------------------------------------


def test_three_identical_zones_cluster() -> None:
    out = cluster_zones([_z(f"z{i}") for i in range(1, 4)])
    assert len(out) == 1
    c = out[0]
    assert not c.is_singleton
    assert c.size == 3
    assert c.member_zone_ids == ["z1", "z2", "z3"]


def test_cluster_summary_label_includes_count_and_layer() -> None:
    out = cluster_zones([_z(f"d{i}", layer="DIM-A") for i in range(1, 13)])
    assert len(out) == 1
    c = out[0]
    assert c.summary_label.startswith("[12]")
    assert "DIM-A" in c.summary_label


def test_cluster_summary_uses_korean_change_type() -> None:
    out = cluster_zones([_z(f"a{i}", change_type="added") for i in range(1, 4)])
    assert "추가" in out[0].summary_label
    out2 = cluster_zones([_z(f"d{i}", change_type="deleted") for i in range(1, 4)])
    assert "삭제" in out2[0].summary_label


# ---------------------------------------------------------------------------
# Layer prefix folding (GRID-X1, GRID-X2, ... → GRID-X)
# ---------------------------------------------------------------------------


def test_trailing_digits_in_layer_fold_into_one_cluster() -> None:
    zones = [_z(f"g{i}", layer=f"GRID-X{i}", entity_type="LINE", change_type="added")
             for i in range(1, 6)]
    out = cluster_zones(zones)
    assert len(out) == 1
    assert out[0].size == 5
    # Prefix without digits in label
    assert "GRID-X" in out[0].summary_label
    assert "GRID-X1" not in out[0].summary_label


def test_different_layer_roots_stay_separate_clusters() -> None:
    zones = [
        _z("a", layer="BEAM"),
        _z("b", layer="COL"),
        _z("c", layer="BEAM"),
        _z("d", layer="COL"),
        _z("e", layer="BEAM"),
        _z("f", layer="COL"),
    ]
    out = cluster_zones(zones)
    # Two clusters of 3
    assert len(out) == 2
    sizes = sorted(c.size for c in out)
    assert sizes == [3, 3]


def test_no_layer_uses_top_layers_fallback() -> None:
    zones = [
        {"zone_id": f"z{i}", "change_type": "modified", "severity": "minor",
         "entity_type": "TEXT", "top_layers": ["DIM-MAIN"]}
        for i in range(1, 4)
    ]
    out = cluster_zones(zones)
    assert len(out) == 1
    assert "DIM-MAIN" in out[0].summary_label


def test_no_layer_at_all_still_clusters_by_other_keys() -> None:
    zones = [_z(f"z{i}", layer="") for i in range(1, 4)]
    out = cluster_zones(zones)
    assert len(out) == 1
    assert out[0].size == 3


# ---------------------------------------------------------------------------
# Different key dimensions split clusters
# ---------------------------------------------------------------------------


def test_different_change_type_splits_clusters() -> None:
    zones = (
        [_z(f"a{i}", change_type="added") for i in range(1, 4)]
        + [_z(f"d{i}", change_type="deleted") for i in range(1, 4)]
    )
    out = cluster_zones(zones)
    assert len(out) == 2
    keys = {c.cluster_key for c in out}
    assert any("added" in k for k in keys)
    assert any("deleted" in k for k in keys)


def test_different_severity_splits_clusters() -> None:
    zones = (
        [_z(f"a{i}", severity="major") for i in range(1, 4)]
        + [_z(f"b{i}", severity="minor") for i in range(1, 4)]
    )
    out = cluster_zones(zones)
    assert len(out) == 2


def test_different_entity_type_splits_clusters() -> None:
    zones = (
        [_z(f"t{i}", entity_type="TEXT") for i in range(1, 4)]
        + [_z(f"l{i}", entity_type="LINE") for i in range(1, 4)]
    )
    out = cluster_zones(zones)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Order preservation
# ---------------------------------------------------------------------------


def test_input_order_preserved_across_clusters() -> None:
    """Order in which clusters appear matches first-seen order of their key."""

    zones = [
        _z("first-singleton", layer="UNIQUE-A"),  # singleton bucket A
        _z("clust1-a", layer="REPEATED"),
        _z("second-singleton", layer="UNIQUE-B"),
        _z("clust1-b", layer="REPEATED"),
        _z("clust1-c", layer="REPEATED"),
    ]
    out = cluster_zones(zones)
    # 4 entries: UNIQUE-A singleton, REPEATED cluster (3), UNIQUE-B singleton
    assert len(out) == 3
    assert out[0].representative["zone_id"] == "first-singleton"
    assert out[1].size == 3
    assert out[2].representative["zone_id"] == "second-singleton"


def test_member_order_within_cluster_preserved() -> None:
    """Within one cluster, members appear in the order they were passed."""

    zones = [_z(z, layer="X") for z in ("a", "b", "c", "d")]
    out = cluster_zones(zones)
    assert len(out) == 1
    assert out[0].member_zone_ids == ["a", "b", "c", "d"]


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def test_disabled_option_returns_only_singletons() -> None:
    opts = ClusterOptions(enabled=False)
    zones = [_z(f"z{i}") for i in range(1, 11)]
    out = cluster_zones(zones, options=opts)
    assert len(out) == 10
    assert all(c.is_singleton for c in out)


def test_min_cluster_size_threshold_respected() -> None:
    opts = ClusterOptions(min_cluster_size=5)
    zones = [_z(f"z{i}") for i in range(1, 5)]  # 4 zones
    out = cluster_zones(zones, options=opts)
    # All 4 stay singletons because below the new threshold of 5
    assert all(c.is_singleton for c in out)
    assert len(out) == 4


def test_min_cluster_size_threshold_one_clusters_pairs() -> None:
    opts = ClusterOptions(min_cluster_size=2)
    zones = [_z(f"z{i}") for i in range(1, 3)]  # pair
    out = cluster_zones(zones, options=opts)
    assert len(out) == 1
    assert out[0].size == 2


# ---------------------------------------------------------------------------
# Realistic mixed scenario — the user's actual pain point
# ---------------------------------------------------------------------------


def test_realistic_drawing_drops_row_count_significantly() -> None:
    """A typical structural drawing where 47 zones fold to ~6 rows."""

    zones = []
    # 12 DIM text edits (cluster)
    for i in range(1, 13):
        zones.append(_z(f"d{i}", layer="DIM-A"))
    # 8 GRID-X line additions across X1..X8 (cluster via prefix fold)
    for i in range(1, 9):
        zones.append(_z(f"g{i}", layer=f"GRID-X{i}",
                        entity_type="LINE", change_type="added"))
    # 5 BEAM modifications (cluster)
    for i in range(1, 6):
        zones.append(_z(f"b{i}", layer="BEAM-2F",
                        entity_type="STRUCTURAL_MEMBER", severity="major"))
    # 2 unique singletons (column add, slab modify)
    zones.append(_z("c1", layer="COLUMN-A1",
                   entity_type="STRUCTURAL_MEMBER", change_type="added"))
    zones.append(_z("s1", layer="SLAB-3F", entity_type="LINE",
                   change_type="modified"))

    out = cluster_zones(zones)
    # Expected: 3 clusters + 2 singletons = 5 rows
    assert len(out) == 5
    cluster_sizes = sorted(c.size for c in out if not c.is_singleton)
    singleton_count = sum(1 for c in out if c.is_singleton)
    assert cluster_sizes == [5, 8, 12]
    assert singleton_count == 2

    # Total members preserved
    total_members = sum(c.size for c in out)
    assert total_members == len(zones)
