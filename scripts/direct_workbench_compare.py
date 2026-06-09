# -*- coding: utf-8 -*-
"""Run a Workbench-backed comparison without Windows UI input.

This script exists for repeatable debugging when Windows UI automation can
capture the Qt window but cannot type into its controls. It drives the same
FolderComparePipeline, loads the result into DrawingCompareWorkbenchV2, and
writes a small summary plus optional screenshots.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", type=Path, required=True, help="Before/source A file or folder")
    parser.add_argument("--b", type=Path, required=True, help="After/source B file or folder")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output root for comparison artifacts")
    parser.add_argument(
        "--screenshots-dir",
        type=Path,
        help="Screenshot output directory. Defaults to <out-dir>/screenshots.",
    )
    parser.add_argument("--skip-screenshots", action="store_true")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument(
        "--viewer-render-policy",
        default="top-issues",
        choices=("top-issues", "all", "lazy", "none"),
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Use the active Qt platform instead of forcing offscreen mode.",
    )
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=1500,
        help="Milliseconds to process Qt events after loading/selecting before screenshots.",
    )
    return parser.parse_args(argv)


def _configure_qt_platform(show: bool) -> None:
    if not show:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _load_qt_and_workbench():
    from PySide6.QtWidgets import QApplication

    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.folder_compare_pipeline import (
        FolderComparePipeline,
        FolderCompareRunRequest,
    )

    return QApplication, DrawingCompareWorkbenchV2, FolderComparePipeline, FolderCompareRunRequest


def _read_manifest_inputs(results_dir: Path) -> dict[str, object]:
    manifest_path = results_dir / "run_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    inputs = payload.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _descriptor_root(descriptors: object) -> str:
    paths: list[Path] = []
    for descriptor in descriptors or []:
        value = getattr(descriptor, "path", "")
        if not value:
            continue
        try:
            paths.append(Path(str(value)).resolve())
        except Exception:
            continue
    if not paths:
        return ""
    if len(paths) == 1:
        return str(paths[0])
    try:
        return str(Path(os.path.commonpath([str(path.parent) for path in paths])).resolve())
    except Exception:
        return str(paths[0].parent)


def _process_events_for(app: object, milliseconds: int) -> None:
    """Let delayed Qt paints/timers run before grabbing verification screenshots."""

    duration = max(0, int(milliseconds or 0)) / 1000.0
    deadline = time.perf_counter() + duration
    process_events = getattr(app, "processEvents", None)
    if not callable(process_events):
        return
    process_events()
    while time.perf_counter() < deadline:
        time.sleep(min(0.05, max(0.0, deadline - time.perf_counter())))
        process_events()
    process_events()


def _first_zone_leaf(workbench: object):
    helper = getattr(workbench, "_zone_leaf_items_v2", None)
    if callable(helper):
        leaves = helper()
        if leaves:
            return leaves[0]

    tree = getattr(workbench, "zone_list_v2", None)
    if tree is None:
        return None

    def _walk(item):
        if item is None:
            return None
        child_count = getattr(item, "childCount", lambda: 0)()
        if child_count == 0:
            return item
        child = getattr(item, "child", None)
        if not callable(child):
            return item
        for idx in range(child_count):
            found = _walk(child(idx))
            if found is not None:
                return found
        return None

    count = getattr(tree, "topLevelItemCount", lambda: 0)()
    top_item = getattr(tree, "topLevelItem", None)
    if not callable(top_item):
        return None
    for idx in range(count):
        found = _walk(top_item(idx))
        if found is not None:
            return found
    return None


def _zone_item_text(item: object | None) -> str:
    if item is None:
        return ""
    text = getattr(item, "text", None)
    if not callable(text):
        return ""
    try:
        return str(text(0))
    except Exception:
        return ""


def _zone_item_id(item: object | None) -> str:
    if item is None:
        return ""
    data = getattr(item, "data", None)
    if not callable(data):
        return ""
    try:
        from PySide6.QtCore import Qt

        return str(data(0, Qt.UserRole) or "")
    except Exception:
        try:
            return str(data(0, 256) or "")
        except Exception:
            return ""


def _manifest_fallback_fields(results_dir: Path, result: object | None = None) -> dict[str, object]:
    inputs = _read_manifest_inputs(results_dir)
    fallback = inputs.get("dwg_dxf_fallback")
    if not isinstance(fallback, dict):
        fallback = {}
    diagnostics = fallback.get("diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    effective_a = str(inputs.get("effective_source_a") or "")
    effective_b = str(inputs.get("effective_source_b") or "")
    if result is not None:
        if not effective_a or "<redacted>" in effective_a:
            effective_a = _descriptor_root(getattr(result, "descriptors_a", [])) or effective_a
        if not effective_b or "<redacted>" in effective_b:
            effective_b = _descriptor_root(getattr(result, "descriptors_b", [])) or effective_b
    return {
        "effective_source_a": effective_a,
        "effective_source_b": effective_b,
        "fallback_used": bool(fallback.get("used")),
        "fallback_reason": str(fallback.get("reason") or ""),
        "fallback_kind": str(diagnostics.get("fallback_kind") or ""),
    }


def run_direct_compare(args: argparse.Namespace) -> dict[str, object]:
    _configure_qt_platform(bool(args.show))
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    QApplication, DrawingCompareWorkbenchV2, FolderComparePipeline, FolderCompareRunRequest = _load_qt_and_workbench()

    source_a = args.a.resolve()
    source_b = args.b.resolve()
    out_root = args.out_dir.resolve()
    results_dir = out_root / "results"
    screenshots_dir = (args.screenshots_dir or (out_root / "screenshots")).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    if not args.skip_screenshots:
        screenshots_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    request = FolderCompareRunRequest(
        source_a=source_a,
        source_b=source_b,
        output_dir=results_dir,
        recursive=bool(args.recursive),
        viewer_render_policy=str(args.viewer_render_policy),
        viewer_perf_log=True,
    )
    try:
        result = FolderComparePipeline(request).run()
    except Exception as exc:
        summary = {
            "status": "failed",
            "elapsed_s": round(time.perf_counter() - started, 3),
            "source_a": str(source_a),
            "source_b": str(source_b),
            "results_dir": str(results_dir),
            "screenshots": [],
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            **_manifest_fallback_fields(results_dir),
        }
        preflight_path = results_dir / "preflight_report.json"
        if preflight_path.exists():
            summary["preflight_report"] = str(preflight_path)
        summary_path = out_root / "direct_compare_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    app = QApplication.instance() or QApplication(sys.argv)
    workbench = DrawingCompareWorkbenchV2()
    effective_sources = _manifest_fallback_fields(results_dir, result)
    workbench._source_a = str(effective_sources.get("effective_source_a") or source_a)
    workbench._source_b = str(effective_sources.get("effective_source_b") or source_b)
    workbench._on_auto_finished_v2(result)
    app.processEvents()
    workbench.resize(1440, 900)
    if args.show:
        workbench.show()
    _process_events_for(app, int(args.settle_ms))

    def _camera_state(vp) -> dict:
        # Diagnostic: read the lightweight viewport's live QML camera so framing
        # can be verified precisely (not via a coarse screenshot).
        try:
            root = vp._quick.rootObject()
            upp = float(root.property("unitsPerPixel") or 0.0)
            width = float(root.property("width") or 0.0)
            return {
                "cx": round(float(root.property("cameraCenterX") or 0.0), 1),
                "cy": round(float(root.property("cameraCenterY") or 0.0), 1),
                "unitsPerPixel": round(upp, 4),
                "view_world_width": round(upp * width, 1),
            }
        except Exception as exc:  # pragma: no cover - diagnostic only
            return {"error": str(exc)}

    on_load_cameras = {
        "before": _camera_state(workbench.preview_before_lightweight_v2),
        "after": _camera_state(workbench.preview_after_lightweight_v2),
    }
    selected_cameras: dict = {}

    screenshots: list[str] = []
    selected_zone_id = ""
    selected_zone_text = ""
    if not args.skip_screenshots:
        main_shot = screenshots_dir / "01_workbench_result_loaded.png"
        workbench.grab().save(str(main_shot))
        screenshots.append(str(main_shot))
        leaf = _first_zone_leaf(workbench)
        if leaf is not None:
            selected_zone_id = _zone_item_id(leaf)
            selected_zone_text = _zone_item_text(leaf)
            selector = getattr(workbench, "_select_zone_leaf_v2", None)
            if callable(selector):
                selector(leaf)
            else:
                workbench.zone_list_v2.setCurrentItem(leaf)
            _process_events_for(app, int(args.settle_ms))
            selected_cameras = {
                "before": _camera_state(workbench.preview_before_lightweight_v2),
                "after": _camera_state(workbench.preview_after_lightweight_v2),
            }
            selected_shot = screenshots_dir / "02_first_zone_selected.png"
            workbench.grab().save(str(selected_shot))
            screenshots.append(str(selected_shot))

    compare_summary = getattr(result, "compare_summary", None)
    summary = {
        "status": "completed",
        "elapsed_s": round(time.perf_counter() - started, 3),
        "source_a": str(source_a),
        "source_b": str(source_b),
        "results_dir": str(results_dir),
        "screenshots": screenshots,
        "completed_pairs": getattr(compare_summary, "completed_pairs", None),
        "failed_pairs": getattr(compare_summary, "failed_pairs", None),
        "total_changes": getattr(compare_summary, "total_changes", None),
        "zone_count": workbench.zone_list_v2.topLevelItemCount(),
        "selected_zone_id": selected_zone_id,
        "selected_zone_text": selected_zone_text,
        "on_load_cameras": on_load_cameras,
        "selected_cameras": selected_cameras,
        "status_label": workbench.lbl_status_v2.text(),
        "queue_label": workbench.lbl_review_queue_v2.text(),
        "viewer_perf_label": workbench.lbl_viewer_perf_v2.text(),
        **_manifest_fallback_fields(results_dir, result),
    }
    summary_path = out_root / "direct_compare_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    workbench.close()
    app.processEvents()
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_direct_compare(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") == "completed" and summary.get("failed_pairs") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
