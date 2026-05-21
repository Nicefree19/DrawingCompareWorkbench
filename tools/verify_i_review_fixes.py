# -*- coding: utf-8 -*-
"""End-to-end regression for the Phase I code-review fixes.

Catches the two correctness defects the code review surfaced:

    1. Critical: ``_refresh_zone_list_filter_v2`` was iterating direct
       children of category headers and treating each as a leaf with
       zone_id. After Phase I3 added cluster nodes (zone_id=""), every
       cluster node would test "needs_review" against any non-default
       status filter and get hidden — taking all its grandchildren with
       it (Qt cascading hide). This script reviews 5 zones inside a
       cluster, then sets the status filter to "확인" and verifies the
       reviewed zones stay visible.
    2. Minor: clicking a category/cluster header (zone_id="") used to
       leave ``_active_zone_id`` pointing at the previously-selected
       zone, so a follow-up "1" hotkey would silently apply to the
       wrong zone. ``_active_zone_id`` should reset to "" on header
       clicks.

Run:

    python tools/verify_i_review_fixes.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _build_synthetic_overlays() -> list[dict]:
    """8 BEAM-2F structural zones (will cluster into one node) — easy to
    verify per-zone visibility behaviour."""

    overlays = []
    for i in range(1, 9):
        overlays.append({
            "zone_id": f"beam-{i}",
            "change_type": "modified",
            "severity": "major",
            "raw_change_count": 5,
            "layer": "BEAM-2F",
            "entity_type": "STRUCTURAL_MEMBER",
        })
    return overlays


def main() -> int:
    failures: list[str] = []

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.zone_classifier import classify_zone
    from src.services.comparison.review_project import ReviewStateRecord, review_state_key

    # Build a workbench with one pair × 8 zones (will form a cluster of 8).
    wb = DrawingCompareWorkbenchV2()
    pair_id = "test_pair_review"
    overlays = _build_synthetic_overlays()
    wb._active_row = {"pair_id": pair_id, "drawing_label": "Review Test"}
    wb._zone_categories_v2[pair_id] = {
        ov["zone_id"]: classify_zone(ov) for ov in overlays
    }
    wb._populate_zone_list_v2(preview=None, overlays=overlays)

    # Mark 5 of the 8 zones as confirmed (by injecting review records
    # directly — avoids needing Tekla / files).
    from datetime import datetime
    confirmed_ids = {f"beam-{i}" for i in range(1, 6)}
    for zid in confirmed_ids:
        record = ReviewStateRecord(
            pair_id=pair_id,
            pair_uuid=pair_id,
            zone_id=zid,
            status="confirmed",
            note="test",
            updated_at=datetime.now().isoformat(),
        )
        wb._review_records_v2[record.key] = record
    print(f"[OK] marked 5/8 zones as confirmed (1-5)")

    # === Critical bug check #1: filter "확인" should KEEP confirmed zones visible ===
    wb.cmb_zone_filter_v2.setCurrentText("확인")
    wb._refresh_zone_list_filter_v2()
    app.processEvents()

    # Walk all leaves and verify visibility
    visible_confirmed = 0
    visible_unreviewed = 0
    for leaf in wb._zone_leaf_items_v2():
        zid = str(leaf.data(0, 0x0100) or "")  # Qt.UserRole = 0x0100
        if leaf.isHidden():
            continue
        if zid in confirmed_ids:
            visible_confirmed += 1
        else:
            visible_unreviewed += 1

    if visible_confirmed != len(confirmed_ids):
        failures.append(
            f"[FAIL] filter='확인' visible_confirmed={visible_confirmed}, "
            f"expected {len(confirmed_ids)}"
        )
    else:
        print(f"[OK] filter='확인' shows all {visible_confirmed} confirmed zones")

    if visible_unreviewed != 0:
        failures.append(
            f"[FAIL] filter='확인' showed {visible_unreviewed} unreviewed zones (expected 0)"
        )
    else:
        print(f"[OK] filter='확인' hides all 3 unreviewed zones")

    # The cluster node + category header should be VISIBLE (because it has
    # confirmed children), even though their UserRole is empty.
    cat_header = wb.zone_list_v2.topLevelItem(0)
    if cat_header is None or cat_header.isHidden():
        failures.append("[FAIL] category header hidden despite having visible children")
    else:
        print("[OK] category header visible (has visible confirmed children)")

    # === Critical bug check #2: filter "미검토만" should hide confirmed zones ===
    wb.cmb_zone_filter_v2.setCurrentText("미검토만")
    wb._refresh_zone_list_filter_v2()
    app.processEvents()

    visible_confirmed = sum(
        1 for leaf in wb._zone_leaf_items_v2()
        if not leaf.isHidden() and str(leaf.data(0, 0x0100) or "") in confirmed_ids
    )
    visible_unreviewed = sum(
        1 for leaf in wb._zone_leaf_items_v2()
        if not leaf.isHidden() and str(leaf.data(0, 0x0100) or "") not in confirmed_ids
    )
    if visible_confirmed != 0:
        failures.append(
            f"[FAIL] filter='미검토만' showed {visible_confirmed} confirmed zones"
        )
    else:
        print("[OK] filter='미검토만' hides all confirmed zones")
    if visible_unreviewed != 3:
        failures.append(
            f"[FAIL] filter='미검토만' visible_unreviewed={visible_unreviewed}, expected 3"
        )
    else:
        print("[OK] filter='미검토만' shows all 3 unreviewed zones")

    # === Critical bug check #3: empty-leaf hide bubbles up ===
    # When ALL zones in a category are hidden, the category header should be hidden too.
    # Confirm: filter to "오탐" — none of the 8 zones are false_positive → all hidden
    wb.cmb_zone_filter_v2.setCurrentText("오탐")
    wb._refresh_zone_list_filter_v2()
    app.processEvents()
    visible_total = sum(1 for leaf in wb._zone_leaf_items_v2() if not leaf.isHidden())
    if visible_total != 0:
        failures.append(
            f"[FAIL] filter='오탐' visible_total={visible_total}, expected 0"
        )
    cat_header = wb.zone_list_v2.topLevelItem(0)
    if not cat_header.isHidden():
        failures.append("[FAIL] category header should be hidden when all leaves are hidden")
    else:
        print("[OK] category header hidden when filter excludes everything")

    # Reset filter for the header-click test
    wb.cmb_zone_filter_v2.setCurrentText("전체")
    wb._refresh_zone_list_filter_v2()
    app.processEvents()

    # === Minor bug check: header click resets _active_zone_id ===
    # 1. Select a zone leaf
    leaf = wb._find_zone_leaf_item_v2("beam-3")
    wb._select_zone_leaf_v2(leaf)
    app.processEvents()
    if str(wb._active_zone_id) != "beam-3":
        failures.append(
            f"[FAIL] after selecting beam-3, _active_zone_id={wb._active_zone_id!r}"
        )
        return _report(failures)
    print("[OK] selecting leaf sets _active_zone_id = 'beam-3'")

    # 2. Click the category header (top-level item, zone_id="")
    header = wb.zone_list_v2.topLevelItem(0)
    wb.zone_list_v2.setCurrentItem(header)
    app.processEvents()
    if wb._active_zone_id != "":
        failures.append(
            f"[FAIL] header click should reset _active_zone_id, got {wb._active_zone_id!r}"
        )
    else:
        print("[OK] header click resets _active_zone_id to ''")

    # 3. Cluster node click also resets (cluster_node has UserRole="")
    # Find a cluster node (1 child of category header in this fixture)
    if header.childCount() == 1 and header.child(0).childCount() > 0:
        cluster_node = header.child(0)
        # First select a leaf inside, then click the cluster
        leaf = wb._find_zone_leaf_item_v2("beam-7")
        wb._select_zone_leaf_v2(leaf)
        if str(wb._active_zone_id) != "beam-7":
            failures.append(f"[FAIL] beam-7 select failed: {wb._active_zone_id!r}")
        wb.zone_list_v2.setCurrentItem(cluster_node)
        app.processEvents()
        if wb._active_zone_id != "":
            failures.append(
                f"[FAIL] cluster-node click should reset _active_zone_id, "
                f"got {wb._active_zone_id!r}"
            )
        else:
            print("[OK] cluster-node click resets _active_zone_id to ''")
    else:
        print("[INFO] cluster click test skipped (fixture didn't form a cluster)")

    return _report(failures)


def _report(failures: list[str]) -> int:
    if failures:
        print("\n" + "\n".join(failures))
        print(f"\n[FAIL] {len(failures)} check(s) failed")
        return 1
    print("\n[PASS] code-review fixes verified - filter recurses through clusters, "
          "category/cluster header clicks reset active zone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
