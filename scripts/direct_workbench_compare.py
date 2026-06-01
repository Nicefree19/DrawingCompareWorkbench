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
        }
        preflight_path = results_dir / "preflight_report.json"
        if preflight_path.exists():
            summary["preflight_report"] = str(preflight_path)
        summary_path = out_root / "direct_compare_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    app = QApplication.instance() or QApplication(sys.argv)
    workbench = DrawingCompareWorkbenchV2()
    workbench._on_auto_finished_v2(result)
    app.processEvents()
    workbench.resize(1440, 900)
    app.processEvents()

    screenshots: list[str] = []
    if not args.skip_screenshots:
        main_shot = screenshots_dir / "01_workbench_result_loaded.png"
        workbench.grab().save(str(main_shot))
        screenshots.append(str(main_shot))
        if workbench.zone_list_v2.topLevelItemCount():
            workbench.zone_list_v2.setCurrentItem(workbench.zone_list_v2.topLevelItem(0))
            app.processEvents()
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
        "status_label": workbench.lbl_status_v2.text(),
        "queue_label": workbench.lbl_review_queue_v2.text(),
        "viewer_perf_label": workbench.lbl_viewer_perf_v2.text(),
    }
    summary_path = out_root / "direct_compare_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_direct_compare(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") == "completed" and summary.get("failed_pairs") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
