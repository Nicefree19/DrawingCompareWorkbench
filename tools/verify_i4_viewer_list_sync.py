# -*- coding: utf-8 -*-
"""End-to-end verification for Phase I4 — viewer ↔ list bidirectional sync.

Spins up a headless QApplication, builds a real DrawingCompareWorkbenchV2
with synthetic overlays, then simulates a viewer overlay click by directly
emitting the ``overlayClicked`` Qt signal from the viewport. Verifies:

    1. Both legacy and lightweight viewports expose ``overlayClicked``
       (Signal[str]).
    2. The workbench wires the signal to ``_on_viewer_overlay_clicked_v2``.
    3. Emitting ``overlayClicked(zone_id)`` triggers
       ``_select_zone_in_list_v2``, which selects the leaf in the tree.
    4. The clicked zone's category + cluster (if any) are auto-expanded.
    5. ``_active_zone_id`` is updated to the clicked zone.
    6. Self-loop guard: emitting overlayClicked for the already-selected
       zone is a no-op (no infinite loop with the focus_zone callback).
    7. QML files declare the ``overlayClicked`` signal (regex-grep so we
       don't need a running QtQuick instance to validate the contract).

Run:

    python tools/verify_i4_viewer_list_sync.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _build_synthetic_overlays() -> list[dict]:
    overlays: list[dict] = []
    # 4 BEAM-2F structural zones (will cluster)
    for i in range(1, 5):
        overlays.append({
            "zone_id": f"beam-{i}",
            "change_type": "modified",
            "severity": "major",
            "raw_change_count": 5,
            "layer": "BEAM-2F",
            "entity_type": "STRUCTURAL_MEMBER",
        })
    # 5 GRID-X1..X5 added LINE (will cluster — prefix fold)
    for i in range(1, 6):
        overlays.append({
            "zone_id": f"grid-{i}",
            "change_type": "added",
            "severity": "minor",
            "raw_change_count": 1,
            "layer": f"GRID-X{i}",
            "entity_type": "LINE",
        })
    # 2 unique singletons
    for i in range(1, 3):
        overlays.append({
            "zone_id": f"unique-{i}",
            "change_type": "modified",
            "severity": "minor",
            "raw_change_count": 1,
            "layer": f"UNIQUE_LAYER_{i}",
            "entity_type": "INSERT",
        })
    return overlays


def main() -> int:
    failures: list[str] = []

    # 7. QML signal declaration check (no Qt needed for this part)
    qml_dir = Path(__file__).resolve().parent.parent / "src" / "gui" / "assets" / "drawing_compare"
    for qml_name in ("DrawingGpuViewport.qml", "LightweightDrawingViewport.qml"):
        qml_path = qml_dir / qml_name
        if not qml_path.exists():
            failures.append(f"[FAIL] missing QML file: {qml_name}")
            continue
        src = qml_path.read_text(encoding="utf-8")
        if not re.search(r"signal\s+overlayClicked\s*\(\s*string\s+zoneId\s*\)", src):
            failures.append(
                f"[FAIL] {qml_name}: 'signal overlayClicked(string zoneId)' not declared"
            )
        else:
            print(f"[OK] {qml_name}: overlayClicked signal declared")
        # MouseArea inside cloud Repeater
        if "root.overlayClicked(zid)" not in src:
            failures.append(
                f"[FAIL] {qml_name}: cloud MouseArea doesn't emit overlayClicked"
            )
        else:
            print(f"[OK] {qml_name}: cloud MouseArea emits overlayClicked")

    if failures:
        # Surface QML failures early — Python tests won't reach the QML
        return _report(failures)

    # === Python-side checks ===
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    from src.gui.drawing_compare_workbench import (
        DrawingCompareWorkbenchV2, GpuDrawingViewport,
    )
    from src.gui.lightweight_viewport import LightweightDrawingViewport
    from src.services.comparison.zone_classifier import classify_zone

    # 1. Both viewport classes expose overlayClicked
    if not hasattr(GpuDrawingViewport, "overlayClicked"):
        failures.append("[FAIL] GpuDrawingViewport missing overlayClicked Signal")
    else:
        print("[OK] GpuDrawingViewport exposes overlayClicked Signal")
    if not hasattr(LightweightDrawingViewport, "overlayClicked"):
        failures.append("[FAIL] LightweightDrawingViewport missing overlayClicked Signal")
    else:
        print("[OK] LightweightDrawingViewport exposes overlayClicked Signal")

    # 2. Workbench wires the signal
    try:
        wb = DrawingCompareWorkbenchV2()
    except Exception as exc:
        print(f"[FAIL] DrawingCompareWorkbenchV2() raised: {exc}")
        return _report(failures + [f"[FAIL] {exc}"])

    if not hasattr(wb, "_on_viewer_overlay_clicked_v2"):
        failures.append("[FAIL] workbench has no _on_viewer_overlay_clicked_v2 handler")
    else:
        print("[OK] workbench has _on_viewer_overlay_clicked_v2 handler")

    # 3. Build a real tree + emit overlayClicked → verify selection
    overlays = _build_synthetic_overlays()
    pair_id = "test_pair_i4"
    wb._active_row = {"pair_id": pair_id, "drawing_label": "Synthetic I4 Test"}
    wb._zone_categories_v2[pair_id] = {
        ov["zone_id"]: classify_zone(ov) for ov in overlays
    }
    wb._populate_zone_list_v2(preview=None, overlays=overlays)

    # Locate beam-3 leaf — it's inside a cluster (4 beams) inside category
    target_zone_id = "beam-3"
    leaf_before = wb._find_zone_leaf_item_v2(target_zone_id)
    if leaf_before is None:
        failures.append(f"[FAIL] {target_zone_id} not in the tree at all")
        return _report(failures)

    # Sanity — ancestor cluster is collapsed initially (active zone is none yet)
    cluster_node = leaf_before.parent()
    cat_node = cluster_node.parent() if cluster_node else None

    # Simulate viewer overlay click
    wb._active_zone_id = ""  # reset so the early-return guard doesn't fire
    wb.preview_before_v2.overlayClicked.emit(target_zone_id)
    app.processEvents()

    # 4 + 5. Verify selection state
    if str(wb._active_zone_id) != target_zone_id:
        failures.append(
            f"[FAIL] _active_zone_id = {wb._active_zone_id!r}, expected {target_zone_id!r}"
        )
    else:
        print(f"[OK] viewer click → _active_zone_id = {target_zone_id}")

    leaf_after = wb._find_zone_leaf_item_v2(target_zone_id)
    if leaf_after is not wb.zone_list_v2.currentItem():
        failures.append(
            "[FAIL] zone_list_v2.currentItem() is not the clicked leaf"
        )
    else:
        print("[OK] tree currentItem set to clicked leaf")

    cluster_node = leaf_after.parent() if leaf_after else None
    cat_node = cluster_node.parent() if cluster_node else None
    if cluster_node and not cluster_node.isExpanded():
        failures.append("[FAIL] cluster ancestor not expanded after viewer click")
    elif cat_node and not cat_node.isExpanded():
        failures.append("[FAIL] category ancestor not expanded after viewer click")
    else:
        print("[OK] cluster + category ancestors auto-expanded")

    # 6. Self-loop guard — emitting again for the already-selected zone is no-op
    snapshot_zone = wb._active_zone_id
    wb.preview_before_v2.overlayClicked.emit(target_zone_id)  # already selected
    app.processEvents()
    if wb._active_zone_id != snapshot_zone:
        failures.append(
            f"[FAIL] re-clicking the active zone changed _active_zone_id "
            f"({snapshot_zone!r} → {wb._active_zone_id!r})"
        )
    else:
        print("[OK] self-loop guard: re-click on active zone is no-op")

    # Lightweight viewport same path
    wb._active_zone_id = ""
    wb.preview_before_lightweight_v2.overlayClicked.emit("grid-2")
    app.processEvents()
    if str(wb._active_zone_id) != "grid-2":
        failures.append(
            f"[FAIL] lightweight viewer click failed: "
            f"_active_zone_id = {wb._active_zone_id!r}"
        )
    else:
        print("[OK] lightweight viewer click → list select works")

    return _report(failures)


def _report(failures: list[str]) -> int:
    if failures:
        print("\n" + "\n".join(failures))
        print(f"\n[FAIL] {len(failures)} check(s) failed")
        return 1
    print("\n[PASS] Phase I4 verified - viewer overlay clicks select the "
          "matching zone in the tree, ancestors auto-expand, no feedback loop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
