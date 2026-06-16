"""Characterization tests for the V2 viewer-overlay LRU+byte cache.

SAFETY NET for the planned MONO-4 #5 extraction (docs/MONO_DECOMPOSITION_PLAN.md):
before the ``_viewer_overlay_cache*`` quintet + its touch/put/evict methods are
moved into an ``OverlayCache`` collaborator, these tests pin the load-bearing
behaviors a verbatim extraction must preserve — the subtle ones the existing
bounded/byte/telemetry tests in ``test_workbench_phase_c`` do NOT cover:

  * put stores overlays verbatim and tracks per-pair + total bytes,
  * LRU touch protects a recently-used pair from eviction,
  * the active pair (``_active_row.pair_id``) is never evicted,
  * re-caching a pair updates total bytes without double counting,
  * the recursive byte estimator's per-type contract,
  * empty pair_id is a no-op.

These exercise a real ``DrawingCompareWorkbenchV2`` instance (no method-rebinding
hack) so they stay valid whether the methods live on V2 or on a delegate.
"""

from __future__ import annotations

import pytest

from src.gui import drawing_compare_workbench as dcw
from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2


@pytest.fixture
def workbench(qapp):
    wb = DrawingCompareWorkbenchV2()
    try:
        yield wb
    finally:
        wb.deleteLater()


# --------------------------------------------------------------------------
# byte estimator (pure, the cache's sizing primitive)
# --------------------------------------------------------------------------

def test_byte_estimator_per_type_contract():
    est = DrawingCompareWorkbenchV2._estimate_overlay_value_bytes_v2
    assert est(None) == 0
    assert est(True) == 1            # bool checked before int
    assert est(5) == 8
    assert est(1.5) == 8
    assert est("ab") == 2            # utf-8 byte length
    assert est("한") == 3
    assert est({}) == 256            # dict base overhead
    assert est({"a": 1}) == 256 + 1 + 8
    assert est([1, 2]) == 64 + 8 + 8
    # recursion: a typical overlay row
    assert est({"zone_id": "z0"}) == 256 + len("zone_id") + len("z0")


def test_estimate_overlay_cache_bytes_sums_rows(workbench):
    rows = [{"zone_id": "z0"}, {"zone_id": "z1"}]
    expected = sum(
        DrawingCompareWorkbenchV2._estimate_overlay_value_bytes_v2(r) for r in rows
    )
    assert workbench._estimate_overlay_cache_bytes_v2(rows) == expected


# --------------------------------------------------------------------------
# put / accounting
# --------------------------------------------------------------------------

def test_put_stores_overlays_verbatim_and_tracks_bytes(workbench):
    overlays = [{"zone_id": "z0"}, {"zone_id": "z1"}]
    workbench._cache_viewer_overlays_v2("pair_a", overlays)

    assert workbench._viewer_overlay_cache["pair_a"] is overlays
    assert (
        workbench._viewer_overlay_cache_bytes_by_pair_v2["pair_a"]
        == workbench._estimate_overlay_cache_bytes_v2(overlays)
    )
    # invariant: total == sum of per-pair byte sizes
    assert workbench._viewer_overlay_cache_total_bytes_v2 == sum(
        workbench._viewer_overlay_cache_bytes_by_pair_v2.values()
    )


def test_recaching_same_pair_updates_total_bytes_without_double_count(workbench):
    workbench._cache_viewer_overlays_v2("pair_a", [{"zone_id": "z0"}])
    small_total = workbench._viewer_overlay_cache_total_bytes_v2

    bigger = [{"zone_id": "z0", "label": "x" * 100}]
    workbench._cache_viewer_overlays_v2("pair_a", bigger)

    assert len(workbench._viewer_overlay_cache) == 1  # still one pair
    assert workbench._viewer_overlay_cache_total_bytes_v2 == (
        workbench._estimate_overlay_cache_bytes_v2(bigger)
    )
    assert workbench._viewer_overlay_cache_total_bytes_v2 > small_total
    # invariant preserved after update
    assert workbench._viewer_overlay_cache_total_bytes_v2 == sum(
        workbench._viewer_overlay_cache_bytes_by_pair_v2.values()
    )


def test_empty_pair_id_is_a_no_op(workbench):
    workbench._cache_viewer_overlays_v2("", [{"zone_id": "z0"}])
    workbench._touch_viewer_overlay_cache_v2("")
    assert workbench._viewer_overlay_cache == {}
    assert workbench._viewer_overlay_cache_order_v2 == []


# --------------------------------------------------------------------------
# LRU ordering + protection
# --------------------------------------------------------------------------

def test_touch_moves_pair_to_most_recent(workbench):
    workbench._cache_viewer_overlays_v2("p1", [{"zone_id": "a"}])
    workbench._cache_viewer_overlays_v2("p2", [{"zone_id": "b"}])
    assert workbench._viewer_overlay_cache_order_v2 == ["p1", "p2"]

    workbench._touch_viewer_overlay_cache_v2("p1")
    assert workbench._viewer_overlay_cache_order_v2 == ["p2", "p1"]


def test_recently_touched_pair_survives_eviction(workbench, monkeypatch):
    monkeypatch.setattr(dcw, "GUI_OVERLAY_CACHE_PAIR_LIMIT", 2)
    workbench._active_row = None  # no active-pair protection in play

    workbench._cache_viewer_overlays_v2("p1", [{"zone_id": "a"}])
    workbench._cache_viewer_overlays_v2("p2", [{"zone_id": "b"}])
    # re-access p1 so p2 becomes the least-recently-used
    workbench._touch_viewer_overlay_cache_v2("p1")
    workbench._cache_viewer_overlays_v2("p3", [{"zone_id": "c"}])  # triggers eviction

    assert "p1" in workbench._viewer_overlay_cache       # touched -> survived
    assert "p3" in workbench._viewer_overlay_cache
    assert "p2" not in workbench._viewer_overlay_cache    # LRU -> evicted


def test_active_pair_is_never_evicted(workbench, monkeypatch):
    monkeypatch.setattr(dcw, "GUI_OVERLAY_CACHE_PAIR_LIMIT", 2)
    workbench._active_row = {"pair_id": "active"}

    # 'active' is cached first so it is the LRU candidate; eviction must skip it.
    workbench._cache_viewer_overlays_v2("active", [{"zone_id": "keep"}])
    for i in range(3):
        workbench._cache_viewer_overlays_v2(f"p{i}", [{"zone_id": f"z{i}"}])

    assert "active" in workbench._viewer_overlay_cache
    assert len(workbench._viewer_overlay_cache) <= dcw.GUI_OVERLAY_CACHE_PAIR_LIMIT
    assert workbench._viewer_overlay_cache_evictions_v2 >= 1
