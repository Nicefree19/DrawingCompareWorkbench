# -*- coding: utf-8 -*-
"""Change-list accessibility: small change sets must not hide changes.

Live-test bug (2026-06-17): "변경사항이 1개만 리스트업되어서 무엇이 바뀐건지
확인이 안 된다." A 14-zone drawing whose changes were near-duplicates folded into
ONE collapsed cluster row under ONE auto-expanded category header, so the
reviewer saw a single line. The data (all zones) was present as collapsed
children. Fix: when the whole set is small (<= ZONE_TREE_AUTO_EXPAND_MAX) expand
every category header AND cluster by default so nothing is hidden; large sets
keep the tidy default-collapsed clustering (+ explicit expand-all control).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

from src.gui.drawing_compare_workbench import (
    ZONE_TREE_AUTO_EXPAND_MAX,
    DrawingCompareWorkbenchV2,
    _build_zone_tree_plan_data_v2,
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _ov(zone_id, *, change_type="modified", severity="medium", entity_type="LINE",
        layer="BEAM", raw_change_count=1):
    return {
        "zone_id": zone_id,
        "change_type": change_type,
        "change_label": change_type,
        "severity": severity,
        "entity_type": entity_type,
        "layer": layer,
        "raw_change_count": raw_change_count,
    }


def _cat(name, boost=0):
    return SimpleNamespace(category=name, severity_boost=boost)


def _plan(overlays, category_by_zone, *, active=""):
    plan, _ = _build_zone_tree_plan_data_v2(
        dashboard_issues=[],
        overlays=overlays,
        preview_zones=[],
        category_by_zone=category_by_zone,
        active_zone_id=active,
        clustering_enabled=True,
    )
    return plan


def test_small_set_expands_every_header_and_cluster():
    # 3 near-duplicates (one cluster) in 구조 + 2 singletons in 치수 = 5 zones.
    overlays = [
        _ov("s1"), _ov("s2"), _ov("s3"),                       # identical -> cluster
        _ov("d1", layer="DIM-A"), _ov("d2", layer="DIM-B"),    # distinct singletons
    ]
    cats = {
        "s1": _cat("구조", 10), "s2": _cat("구조", 10), "s3": _cat("구조", 10),
        "d1": _cat("치수", 0), "d2": _cat("치수", 0),
    }
    plan = _plan(overlays, cats)
    assert len(plan) == 2
    # Every category header is expanded (no change hidden behind a collapsed header).
    assert all(group["expanded"] for group in plan), plan
    # The cluster of near-duplicates is expanded too.
    clusters = [it for group in plan for it in group["items"] if it["kind"] == "cluster"]
    assert clusters and all(c["expanded"] for c in clusters)
    # And it genuinely holds all 3 members (data is reachable, not dropped).
    assert sum(len(c["children"]) for c in clusters) == 3


def test_large_set_keeps_default_collapse():
    # 3 near-dup cluster in 구조 (group 0) + 42 distinct singletons in 치수 (group 1).
    overlays = [_ov("s1"), _ov("s2"), _ov("s3")]
    cats = {"s1": _cat("구조", 10), "s2": _cat("구조", 10), "s3": _cat("구조", 10)}
    for i in range(42):
        zid = f"d{i}"
        overlays.append(_ov(zid, layer=f"DIM-{i}"))
        cats[zid] = _cat("치수", 0)
    assert len(overlays) > ZONE_TREE_AUTO_EXPAND_MAX
    plan = _plan(overlays, cats)
    assert len(plan) == 2
    assert plan[0]["expanded"] is True          # first category always expands
    assert plan[1]["expanded"] is False         # later categories stay collapsed
    # The 구조 cluster stays collapsed when the set is large and not active.
    structural_clusters = [it for it in plan[0]["items"] if it["kind"] == "cluster"]
    assert structural_clusters and all(c["expanded"] is False for c in structural_clusters)


def test_large_set_active_zone_still_forces_open():
    # Large set, but the active zone lives in the 2nd category's cluster:
    # that header AND that cluster must open even though the set is large.
    overlays = [_ov(f"x{i}", layer=f"L{i}") for i in range(42)]  # 구조 singletons
    cats = {f"x{i}": _cat("구조", 10) for i in range(42)}
    for zid in ("c1", "c2", "c3"):                               # 치수 cluster
        overlays.append(_ov(zid, change_type="added", layer="DIM"))
        cats[zid] = _cat("치수", 0)
    plan = _plan(overlays, cats, active="c2")
    dim_group = next(g for g in plan if "치수" in g["header_text"])
    assert dim_group["expanded"] is True
    dim_clusters = [it for it in dim_group["items"] if it["kind"] == "cluster"]
    assert dim_clusters and all(c["expanded"] for c in dim_clusters)


def test_expand_collapse_controls_drive_the_tree(qapp):
    """The '모두 펼치기 / 접기' buttons exist and drive the real QTreeWidget."""
    from PySide6.QtWidgets import QTreeWidgetItem

    wb = DrawingCompareWorkbenchV2()
    try:
        assert hasattr(wb, "btn_zone_expand_all_v2")
        assert hasattr(wb, "btn_zone_collapse_all_v2")
        tree = wb.zone_list_v2
        tree.clear()
        parent = QTreeWidgetItem(["category"])
        parent.addChild(QTreeWidgetItem(["leaf"]))
        tree.addTopLevelItem(parent)

        wb._collapse_all_zones_v2()
        assert not parent.isExpanded()
        wb._expand_all_zones_v2()
        assert parent.isExpanded()
    finally:
        wb.deleteLater()
