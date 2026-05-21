# -*- coding: utf-8 -*-
"""Probe whether preview viewports actually load the background PNG.

Builds a Workbench with the acceptance result, selects the first drawing row,
processes events generously, and reads back the QML root properties that drive
the viewport (imageSource, hasBackground, statusText). Prints whether the PNG
is reaching the viewport so we can identify why screenshots come up blank.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PySide6.QtCore import QTimer, QEventLoop
from PySide6.QtWidgets import QApplication

from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
from src.services.comparison.folder_compare_pipeline import (
    FolderComparePipeline,
    FolderCompareRunRequest,
)


def _spin(app: QApplication, ms: int) -> None:
    """Run the event loop for ``ms`` milliseconds without blocking."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    out_dir = ROOT / "out" / "acceptance_smoke" / "results"
    a_dir = ROOT / "out" / "acceptance_smoke" / "A"
    b_dir = ROOT / "out" / "acceptance_smoke" / "B"
    request = FolderCompareRunRequest(
        source_a=a_dir,
        source_b=b_dir,
        output_dir=out_dir,
        recursive=False,
        viewer_render_policy="top-issues",
        viewer_perf_log=True,
    )
    pipeline = FolderComparePipeline(request)
    result = pipeline.run()

    app = QApplication.instance() or QApplication(sys.argv)
    workbench = DrawingCompareWorkbenchV2()
    workbench._on_auto_finished_v2(result)
    workbench.show()
    _spin(app, 200)

    # Force selection of first drawing
    if workbench.drawing_list_v2.count():
        workbench.drawing_list_v2.setCurrentRow(0)
    _spin(app, 1500)  # generous wait for async image load

    print("\n=== preview_before_v2 QML state ===")
    quick = workbench.preview_before_v2._quick
    if quick and quick.rootObject():
        root = quick.rootObject()
        print(f"  imageSource = {root.property('imageSource')!r}")
        print(f"  hasBackground = {root.property('hasBackground')}")
        print(f"  useTiles = {root.property('useTiles')}")
        print(f"  sceneWidth/Height = {root.property('sceneWidth')} x {root.property('sceneHeight')}")
        print(f"  statusText = {root.property('statusText')!r}")
        # Find background Image item by id
        bg = root.findChild(type(root), "background")
        if bg is None:
            for child in root.findChildren(type(root)):
                if child.property("source") and "before" in str(child.property("source")):
                    bg = child
                    break
        if bg:
            print(f"  background.source = {bg.property('source')}")
            print(f"  background.status = {bg.property('status')} (2=Ready)")
            print(f"  background.implicit = {bg.property('implicitWidth')} x {bg.property('implicitHeight')}")
            print(f"  background.visible = {bg.property('visible')}")
            print(f"  background.width/height = {bg.property('width')} x {bg.property('height')}")
        overlays_cloud = root.property("overlaysCloud") or []
        overlays_focus = root.property("overlaysFocus") or []
        try:
            cloud_n = len(overlays_cloud)
            focus_n = len(overlays_focus)
        except TypeError:
            cloud_n = focus_n = "(opaque)"
        print(f"  overlaysCloud count = {cloud_n}")
        print(f"  overlaysFocus count = {focus_n}")
    else:
        print("  (no quick view — fallback widget engaged)")

    print(f"\n  workbench._last_image_path = {workbench.preview_before_v2._last_image_path!r}")
    print(f"  workbench._render_status_by_pair = {dict(workbench._render_status_by_pair)}")
    print(f"  active_row.pair_id = {(workbench._active_row or {}).get('pair_id')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
