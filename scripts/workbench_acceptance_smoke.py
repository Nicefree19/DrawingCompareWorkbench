# -*- coding: utf-8 -*-
"""Headless GUI acceptance smoke for Drawing Compare Workbench V2.

Loads a precomputed validation result into Workbench V2 (offscreen Qt platform)
and inspects widget state programmatically to verify the customer-grade UX
upgrades shipped in commit ``0722838c`` / ``dcca28c6``.

This script does *not* render pixels — it asserts on the data layer that drives
the UI: model entries pushed to QML, formatted labels in QListWidgets, detail
panel text content, and the run-completion gate. Run after a validation pipeline
has produced ``out/acceptance_smoke/results/`` artifacts.

Exits 0 on full pass, 1 on any failure. Prints a structured pass/fail table.
"""

from __future__ import annotations

import os
import sys
import json
import shutil
import argparse
import time
from contextlib import contextmanager
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import Sequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Force UTF-8 stdout so we can print Korean labels and ascii pass/fail markers on
# cp949 Windows consoles without UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QProcess, Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox

from src.gui.drawing_compare_workbench import (
    DrawingCompareWorkbenchV2,
    build_overlay_entries,
    compute_pdf_page_pin_overlay,
    format_pattern_group_label,
    format_top_issue_label,
    match_side_ko,
    natural_change_summary,
    split_overlay_entries,
)
from src.services.comparison.folder_compare_pipeline import (
    FolderComparePipeline,
    FolderCompareRunRequest,
)
from src.services.comparison.export_profiles import audit_sharable_paths as audit_package_sharable_paths
from src.services.comparison.run_contract import validate_run_completion
from src.services.comparison.viewer_perf_summary import (
    format_viewer_perf_summary_korean,
    summarize_viewer_perf,
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str

    def line(self) -> str:
        mark = "[PASS]" if self.passed else "[FAIL]"
        return f"{mark} {self.name}: {self.detail}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=ROOT / "out" / "acceptance_smoke" / "results",
        help="Validation output directory containing validation_summary.json and _SUCCESS",
    )
    parser.add_argument(
        "--a",
        type=Path,
        help="A/source folder or file. Defaults to <results-dir>/../A.",
    )
    parser.add_argument(
        "--b",
        type=Path,
        help="B/source folder or file. Defaults to <results-dir>/../B.",
    )
    parser.add_argument(
        "--screenshots-dir",
        type=Path,
        help="Directory for offscreen screenshots. Defaults to <results-dir>/../screenshots.",
    )
    parser.add_argument("--skip-screenshots", action="store_true")
    return parser.parse_args(argv)


def _build_pipeline_result(out_dir: Path, source_a: Path | None = None, source_b: Path | None = None) -> object:
    """Re-run the pipeline programmatically so we get a FolderCompareRunResult.

    The CLI script writes JSON to disk but does not return the in-memory dataclass
    that the Workbench expects. Re-running the pipeline against the same A/B set
    is the cleanest way to obtain a result object — it will reuse the dxf cache
    that the CLI just populated.
    """

    a_dir = source_a or out_dir.parent / "A"
    b_dir = source_b or out_dir.parent / "B"
    request = FolderCompareRunRequest(
        source_a=a_dir,
        source_b=b_dir,
        output_dir=out_dir,
        recursive=False,
        viewer_render_policy="top-issues",
        viewer_perf_log=True,
    )
    pipeline = FolderComparePipeline(request)
    return pipeline.run()


@contextmanager
def _suppress_workbench_export_dialogs():
    original_info = QMessageBox.information
    original_question = QMessageBox.question
    original_critical = QMessageBox.critical
    original_open = QDesktopServices.openUrl
    try:
        QMessageBox.information = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)  # type: ignore[assignment]
        QMessageBox.question = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes)  # type: ignore[assignment]
        QMessageBox.critical = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)  # type: ignore[assignment]
        QDesktopServices.openUrl = staticmethod(lambda *args, **kwargs: True)  # type: ignore[assignment]
        yield
    finally:
        QMessageBox.information = original_info  # type: ignore[assignment]
        QMessageBox.question = original_question  # type: ignore[assignment]
        QMessageBox.critical = original_critical  # type: ignore[assignment]
        QDesktopServices.openUrl = original_open  # type: ignore[assignment]


def _run_acceptance(
    workbench: DrawingCompareWorkbenchV2,
    results_dir: Path,
    runtime_metrics: dict[str, float] | None = None,
) -> list[CheckResult]:
    checks: list[CheckResult] = []

    # --- Item 1: cloud/focus overlay separation (helper-level proof) ---
    entries = build_overlay_entries(
        zone_id="z-test",
        rect=(0.0, 0.0, 100.0, 60.0),
        change_type="deleted",
        label="z-test",
        selected=True,
        before=True,
    )
    cloud, focus = split_overlay_entries(entries)
    item1_ok = (
        len(cloud) == 1
        and len(focus) == 1
        and cloud[0].get("dimmed") is True
        and cloud[0].get("matchSide") == "a_only"
        and focus[0].get("crosshair") is True
        and focus[0].get("pinX") == 50.0
    )
    checks.append(
        CheckResult(
            "1. cloud/focus 데이터 분리 + 매칭 상태",
            item1_ok,
            f"cloud(dimmed={cloud[0].get('dimmed')}, matchSide={cloud[0].get('matchSide')}) + "
            f"focus(crosshair={focus[0].get('crosshair')}, pinX={focus[0].get('pinX')})",
        )
    )

    # --- Item 2: before/after match-side color differentiation (data driver) ---
    before_entries = build_overlay_entries(
        zone_id="add-1",
        rect=(0.0, 0.0, 30.0, 30.0),
        change_type="added",
        label="add-1",
        selected=False,
        before=True,
    )
    after_entries = build_overlay_entries(
        zone_id="add-1",
        rect=(0.0, 0.0, 30.0, 30.0),
        change_type="added",
        label="add-1",
        selected=False,
        before=False,
    )
    # added on the before viewport is dimmed (b_only), normal on after
    item2_ok = (
        before_entries[0].get("dimmed") is True
        and after_entries[0].get("dimmed") is False
        and before_entries[0].get("matchSide") == "b_only"
    )
    checks.append(
        CheckResult(
            "2. before/after 매칭 상태 색 차이 (b_only→before dim)",
            item2_ok,
            f"before.dimmed={before_entries[0].get('dimmed')} / after.dimmed={after_entries[0].get('dimmed')}",
        )
    )

    # --- Item 3: PDF page-level pin fallback ---
    enriched = compute_pdf_page_pin_overlay(
        {"zone_id": "pdf-z", "change_type": "modified"},
        {"width": 1240.0, "height": 1754.0},
    )
    item3_ok = (
        enriched is not None
        and enriched.get("pin_only") is True
        and enriched.get("pdf_page_pin") is True
        and enriched["bbox"]["min_x"] > 0
    )
    checks.append(
        CheckResult(
            "3. PDF 페이지 핀 fallback (1240x1754 → 중앙 200x150)",
            item3_ok,
            f"pin_only={enriched.get('pin_only') if enriched else None}, bbox center≈({(enriched['bbox']['min_x']+enriched['bbox']['max_x'])/2 if enriched else 0:.0f}, {(enriched['bbox']['min_y']+enriched['bbox']['max_y'])/2 if enriched else 0:.0f})",
        )
    )

    # --- Item 4: viewer_perf_summary status line ---
    perf = summarize_viewer_perf(workbench._viewer_root)
    perf_label = format_viewer_perf_summary_korean(perf)
    item4_ok = perf.get("event_count", 0) > 0 and (
        "캐시 적중" in perf_label or "백엔드 이벤트" in perf_label
    )
    workbench_label = workbench.lbl_viewer_perf_v2.text() if hasattr(workbench, "lbl_viewer_perf_v2") else ""
    checks.append(
        CheckResult(
            "4. viewer_perf_summary 상태 라인",
            item4_ok,
            f"events={perf.get('event_count')}, status={perf.get('status')}, label='{perf_label[:60]}'",
        )
    )

    # --- Item 5: review_queue is visible as first-class Top issue UI, not just raw counts ---
    top_count = workbench.top_issues_list_v2.count() if hasattr(workbench, "top_issues_list_v2") else 0
    pattern_count = workbench.pattern_group_list_v2.count() if hasattr(workbench, "pattern_group_list_v2") else 0
    has_jump = hasattr(workbench, "_jump_to_pair_zone_v2") and callable(workbench._jump_to_pair_zone_v2)
    has_filter = hasattr(workbench, "_clear_pattern_filter_v2") and callable(workbench._clear_pattern_filter_v2)
    queue = workbench._dashboard.get("review_queue", {}) if workbench._dashboard else {}
    queue_items = queue.get("items") if isinstance(queue, dict) else []
    queue_item_count = len(queue_items) if isinstance(queue_items, list) else 0
    try:
        top_per_drawing = int(queue.get("top_per_drawing") or 0) if isinstance(queue, dict) else 0
    except Exception:
        top_per_drawing = 0
    queue_label = workbench.lbl_review_queue_v2.text() if hasattr(workbench, "lbl_review_queue_v2") else ""
    first_top_label = (
        workbench.top_issues_list_v2.item(0).text()
        if top_count and hasattr(workbench, "top_issues_list_v2")
        else ""
    )
    item5_ok = (
        bool(has_jump and has_filter)
        and isinstance(queue, dict)
        and queue.get("mode") == "structural_core"
        and 3 <= top_per_drawing <= 5
        and queue_item_count > 0
        and top_count > 0
        and "우선 검토" in queue_label
        and "점수" in first_top_label
        and "raw" in first_top_label
    )
    checks.append(
        CheckResult(
            "5. review_queue first-screen Top 이슈 + 점프/필터",
            item5_ok,
            f"queue_mode={queue.get('mode') if isinstance(queue, dict) else None}, "
            f"top_per_drawing={top_per_drawing}, queue_items={queue_item_count}, "
            f"top_issues_list={top_count}, pattern_group_list={pattern_count}, "
            f"jump_method={has_jump}, filter_method={has_filter}, "
            f"queue_label='{queue_label[:80]}', first_top='{first_top_label[:80]}'",
        )
    )

    # --- Item 6: detail panel match-side line + pattern_group helper ---
    side_a = match_side_ko("deleted")
    side_b = match_side_ko("added")
    side_match = match_side_ko("modified")
    natural = natural_change_summary({}, added=5, deleted=0, modified=2, moved=0, top_layers="GRID | DIM")
    item6_ok = (
        "변경 전(A)에만 존재" in side_a
        and "변경 후(B)에만 존재" in side_b
        and "양쪽 매칭됨" in side_match
        and "GRID 레이어에 추가 5건, 수정 2건" in natural
    )
    checks.append(
        CheckResult(
            "6. detail 매칭 상태 + 자연어 요약",
            item6_ok,
            f"A-only='{side_a}', B-only='{side_b}', natural='{natural}'",
        )
    )

    # --- Item 7: validation_summary contains sharable_audit + run_contract gate ---
    summary_json = json.loads((results_dir / "validation_summary.json").read_text(encoding="utf-8"))
    success_path = results_dir / "_SUCCESS"
    manifest_path = results_dir / "run_manifest.json"
    completion = validate_run_completion(str(manifest_path), str(success_path))
    item7a = success_path.exists() and completion["valid"]
    has_perf_summary_field = "viewer_perf_summary" in summary_json
    label_format_holds = "성능" in workbench_label or "viewer" in workbench_label.lower()
    item7_ok = item7a and has_perf_summary_field
    checks.append(
        CheckResult(
            "7. _SUCCESS gating + viewer_perf_summary 필드",
            item7_ok,
            f"_SUCCESS valid={completion['valid']} (run_id={completion['run_id'][:16]}...), perf_field={has_perf_summary_field}, workbench_label='{workbench_label[:50]}'",
        )
    )

    # --- Item 8: reviewer confirms one zone -> confirmed-only cloud export ---
    item8_ok = False
    item8_detail = ""
    try:
        select_started = time.perf_counter()
        if workbench.drawing_list_v2.count():
            workbench.drawing_list_v2.setCurrentRow(0)
        QApplication.processEvents()
        leaves = workbench._zone_leaf_items_v2() if hasattr(workbench, "_zone_leaf_items_v2") else []
        if leaves:
            workbench._select_zone_leaf_v2(leaves[0])
        QApplication.processEvents()
        if runtime_metrics is not None and "dashboard_select_to_first_zone_open_ms" not in runtime_metrics:
            runtime_metrics["dashboard_select_to_first_zone_open_ms"] = round(
                (time.perf_counter() - select_started) * 1000.0,
                3,
            )
        pair_id = str((workbench._active_row or {}).get("pair_id") or "")
        zone_id = str(workbench._active_zone_id or "")
        if not pair_id or not zone_id:
            item8_detail = f"no active pair/zone after selection (pair={pair_id!r}, zone={zone_id!r})"
        else:
            workbench._auto_advance_v2 = False
            workbench._set_zone_review_status_v2("confirmed")
            artifact_dir = Path(workbench._result.artifact_dir) / "confirmed_clouds"  # type: ignore[union-attr]
            if artifact_dir.exists():
                shutil.rmtree(artifact_dir)
            with _suppress_workbench_export_dialogs():
                workbench._export_confirmed_cloud_marks_v2(all_pairs=False)
            outputs = sorted(artifact_dir.glob("*_confirmed.png")) if artifact_dir.exists() else []
            review_status = workbench._review_status_for_zone_v2(pair_id, zone_id)
            item8_ok = review_status == "confirmed" and bool(outputs)
            item8_detail = (
                f"pair={pair_id}, zone={zone_id}, status={review_status}, "
                f"confirmed_png={outputs[0].name if outputs else '<none>'}"
            )
    except Exception as exc:
        item8_detail = f"exception={exc}"
    checks.append(
        CheckResult(
            "8. Workbench confirmed 판정 → confirmed-only 구름마크 export",
            item8_ok,
            item8_detail,
        )
    )

    # --- Item 8b: non-confirmed decisions persist and are excluded from confirmed-only export ---
    item8b_ok = False
    item8b_detail = ""
    try:
        pair_id = str((workbench._active_row or {}).get("pair_id") or "")
        zone_id = str(workbench._active_zone_id or "")
        artifact_dir = Path(workbench._result.artifact_dir) / "confirmed_clouds"  # type: ignore[union-attr]
        if not pair_id or not zone_id:
            item8b_detail = f"no active pair/zone after item 8 (pair={pair_id!r}, zone={zone_id!r})"
        else:
            excluded: dict[str, bool] = {}
            observed_statuses: dict[str, str] = {}
            for status in ("hold", "false_positive"):
                if artifact_dir.exists():
                    shutil.rmtree(artifact_dir)
                workbench._set_zone_review_status_v2(status)
                observed = workbench._review_status_for_zone_v2(pair_id, zone_id)
                observed_statuses[status] = observed
                with _suppress_workbench_export_dialogs():
                    workbench._export_confirmed_cloud_marks_v2(all_pairs=False)
                outputs = sorted(artifact_dir.glob("*_confirmed.png")) if artifact_dir.exists() else []
                excluded[status] = observed == status and not outputs

            # Restore one confirmed artifact so later report-generation checks keep
            # validating the customer handoff path.
            if artifact_dir.exists():
                shutil.rmtree(artifact_dir)
            workbench._set_zone_review_status_v2("confirmed")
            with _suppress_workbench_export_dialogs():
                workbench._export_confirmed_cloud_marks_v2(all_pairs=False)
            restored_outputs = sorted(artifact_dir.glob("*_confirmed.png")) if artifact_dir.exists() else []
            item8b_ok = all(excluded.values()) and bool(restored_outputs)
            item8b_detail = (
                f"pair={pair_id}, zone={zone_id}, statuses={observed_statuses}, "
                f"excluded={excluded}, restored_confirmed_png={restored_outputs[0].name if restored_outputs else '<none>'}"
            )
    except Exception as exc:
        item8b_detail = f"exception={exc}"
    checks.append(
        CheckResult(
            "8b. Workbench 보류/오탐 판정 → confirmed-only export 제외",
            item8b_ok,
            item8b_detail,
        )
    )

    # --- Item 9: selected-zone render perf is measured against MVP budgets ---
    deadline = time.monotonic() + 12.0
    zone_summary = summarize_viewer_perf(workbench._viewer_root)
    while int(zone_summary.get("zone_crop_count") or 0) <= 0 and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)
        zone_summary = summarize_viewer_perf(workbench._viewer_root)
    zone_count = int(zone_summary.get("zone_crop_count") or 0)
    cold = zone_summary.get("zone_crop_cold_ms") or {}
    hit = zone_summary.get("zone_crop_cache_hit_ms") or {}
    cold_p95 = float(cold.get("p95") or 0.0) if isinstance(cold, dict) else 0.0
    hit_p95 = float(hit.get("p95") or 0.0) if isinstance(hit, dict) else 0.0
    item9_ok = zone_count > 0 and (cold_p95 == 0.0 or cold_p95 <= 10_000.0) and (hit_p95 == 0.0 or hit_p95 <= 2_000.0)
    checks.append(
        CheckResult(
            "9. selected-zone render p95 계측 (cold≤10s / hit≤2s)",
            item9_ok,
            f"zone_crop_count={zone_count}, cold_p95_ms={cold_p95:.1f}, hit_p95_ms={hit_p95:.1f}",
        )
    )

    # --- Item 9b: selected-zone Before/After synchronized focus/window ---
    item9b_ok = False
    item9b_detail = ""
    try:
        QApplication.processEvents()
        pair_id = str((workbench._active_row or {}).get("pair_id") or "")
        zone_id = str(workbench._active_zone_id or "")

        def _viewport_state(viewport: object) -> dict[str, object]:
            image = str(getattr(viewport, "_last_image_path", "") or "")
            image_path = Path(image) if image else None
            overlays = getattr(viewport, "_overlays_by_zone", {}) or {}
            if not isinstance(overlays, dict):
                overlays = {}
            selected = str(getattr(viewport, "_selected_zone_id", "") or "")
            pair = str(getattr(viewport, "_pair_id", "") or "")
            root_selected = selected
            focus_zone = ""
            try:
                quick = getattr(viewport, "_quick", None)
                root = quick.rootObject() if quick is not None else None
                if root is not None:
                    root_selected = str(root.property("selectedZoneId") or root_selected)
                    focus_zone = str(root.property("focusZoneId") or "")
            except Exception:
                root_selected = selected
            return {
                "selected": selected,
                "root_selected": root_selected,
                "focus_zone": focus_zone,
                "pair_id": pair,
                "last_before": bool(getattr(viewport, "_last_before", False)),
                "overlay_present": zone_id in overlays,
                "image": image,
                "image_exists": bool(image_path and image_path.exists()),
                "image_parent": str(image_path.parent) if image_path else "",
            }

        before_state: dict[str, object] = {}
        after_state: dict[str, object] = {}
        same_selection = False
        same_pair = False
        side_flags = False
        both_overlays = False
        both_images = False
        same_crop_dir = False
        render_meta_ok = False
        deadline = time.monotonic() + 12.0
        while True:
            QApplication.processEvents()
            before_state = _viewport_state(workbench.preview_before_v2)
            after_state = _viewport_state(workbench.preview_after_v2)
            same_selection = (
                bool(pair_id and zone_id)
                and before_state["selected"] == zone_id
                and after_state["selected"] == zone_id
                and before_state["root_selected"] == zone_id
                and after_state["root_selected"] == zone_id
            )
            same_pair = before_state["pair_id"] == pair_id and after_state["pair_id"] == pair_id
            side_flags = before_state["last_before"] is True and after_state["last_before"] is False
            both_overlays = before_state["overlay_present"] is True and after_state["overlay_present"] is True
            both_images = before_state["image_exists"] is True and after_state["image_exists"] is True
            same_crop_dir = (
                bool(before_state["image_parent"])
                and before_state["image_parent"] == after_state["image_parent"]
            )
            render_meta_ok = False
            if both_images and same_crop_dir:
                meta_path = Path(str(before_state["image_parent"])) / "render_result.json"
                if meta_path.exists():
                    payload = json.loads(meta_path.read_text(encoding="utf-8"))
                    window = payload.get("world_window") if isinstance(payload, dict) else None
                    render_meta_ok = (
                        str(payload.get("pair_uuid") or "") == pair_id
                        and str(payload.get("zone_id") or "") == zone_id
                        and isinstance(window, dict)
                        and {"xmin", "ymin", "xmax", "ymax"}.issubset(window)
                    )
            synchronized_crop = both_images and same_crop_dir and render_meta_ok
            synchronized_relative_fallback = not both_images and both_overlays
            item9b_ok = (
                same_selection
                and same_pair
                and side_flags
                and both_overlays
                and (synchronized_crop or synchronized_relative_fallback)
            )
            if item9b_ok or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        item9b_detail = (
            f"pair={pair_id}, zone={zone_id}, same_selection={same_selection}, "
            f"same_pair={same_pair}, side_flags={side_flags}, both_overlays={both_overlays}, "
            f"both_images={both_images}, same_crop_dir={same_crop_dir}, render_meta_ok={render_meta_ok}, "
            f"before_image={Path(str(before_state['image'])).name if before_state['image'] else '<none>'}, "
            f"after_image={Path(str(after_state['image'])).name if after_state['image'] else '<none>'}"
        )
    except Exception as exc:
        item9b_detail = f"exception={exc}"
    checks.append(
        CheckResult(
            "9b. selected-zone Before/After synchronized focus/window",
            item9b_ok,
            item9b_detail,
        )
    )

    # --- Item 9c: selected-zone render stays subprocess-bounded and the UI loop remains live ---
    item9c_ok = False
    item9c_detail = ""
    try:
        controller = getattr(workbench, "_zone_render_controller_v2", None)
        timeout_ms = int(getattr(controller, "timeout_ms", 0) or 0)
        process_key = str(getattr(controller, "_process_key", "") or "acceptance-smoke")
        if controller is not None and hasattr(controller, "prewarm"):
            controller.prewarm(process_key)
        QApplication.processEvents()
        process_obj = getattr(controller, "_process", None)
        qprocess_ok = isinstance(process_obj, QProcess)
        qprocess_running = qprocess_ok and process_obj.state() != QProcess.NotRunning
        timeout_timer_ok = isinstance(getattr(controller, "_timeout_timer", None), QTimer)
        ticks = _event_loop_tick_count(150)
        item9c_ok = (
            controller is not None
            and controller.__class__.__name__ == "ZoneRenderProcessController"
            and 0 < timeout_ms <= 10_000
            and timeout_timer_ok
            and qprocess_running
            and ticks >= 3
            and not controller.is_busy()
        )
        item9c_detail = (
            f"controller={controller.__class__.__name__ if controller else '<none>'}, "
            f"timeout_ms={timeout_ms}, qprocess_running={qprocess_running}, "
            f"timeout_timer={timeout_timer_ok}, event_loop_ticks_150ms={ticks}, "
            f"busy={controller.is_busy() if controller else '<none>'}"
        )
    except Exception as exc:
        item9c_detail = f"exception={exc}"
    checks.append(
        CheckResult(
            "9c. selected-zone render subprocess timeout + responsive UI loop",
            item9c_ok,
            item9c_detail,
        )
    )

    # --- Item 10: confirmed-only review report PDF + sharable path audit ---
    item10_ok = False
    item10_detail = ""
    try:
        artifact_dir = Path(workbench._result.artifact_dir)  # type: ignore[union-attr]
        before_reports = set(artifact_dir.glob("review_report_*.pdf"))
        with _suppress_workbench_export_dialogs():
            workbench._export_review_report_pdf_v2()

        reports = sorted(set(artifact_dir.glob("review_report_*.pdf")) - before_reports)
        if not reports:
            reports = sorted(artifact_dir.glob("review_report_*.pdf"))
        leaks = audit_package_sharable_paths(results_dir)
        report_path = reports[-1] if reports else None
        item10_ok = bool(report_path and report_path.exists()) and not leaks
        item10_detail = (
            f"report={report_path.name if report_path else '<none>'}, "
            f"path_leak_count={len(leaks)}"
        )
    except Exception as exc:
        item10_detail = f"exception={exc}"
    checks.append(
        CheckResult(
            "10. confirmed-only 검토 보고서 PDF 생성 + path leakage audit",
            item10_ok,
            item10_detail,
        )
    )

    return checks


def _event_loop_tick_count(duration_ms: int) -> int:
    ticks = 0
    loop = QEventLoop()
    ticker = QTimer()
    ticker.setInterval(10)

    def _tick() -> None:
        nonlocal ticks
        ticks += 1

    ticker.timeout.connect(_tick)
    ticker.start()
    QTimer.singleShot(max(1, int(duration_ms)), loop.quit)
    loop.exec()
    ticker.stop()
    ticker.deleteLater()
    QApplication.processEvents()
    return ticks


def _format_widget_state_table(workbench: DrawingCompareWorkbenchV2) -> str:
    """One-shot snapshot of headline workbench widget state for the report."""

    drawing_count = workbench.drawing_list_v2.count() if hasattr(workbench, "drawing_list_v2") else 0
    summary_state = (
        workbench.summary_labels.get("zones").text()
        if "zones" in workbench.summary_labels
        else "(no zones label)"
    )
    perf_label = workbench.lbl_viewer_perf_v2.text() if hasattr(workbench, "lbl_viewer_perf_v2") else "(no perf label)"
    queue_label = workbench.lbl_review_queue_v2.text() if hasattr(workbench, "lbl_review_queue_v2") else ""
    return (
        f"  drawing_list: {drawing_count} rows\n"
        f"  zones summary: {summary_state}\n"
        f"  perf label: {perf_label[:80]}\n"
        f"  queue label: {queue_label[:80]}"
    )


def _capture_screenshots(workbench: DrawingCompareWorkbenchV2, out_dir: Path) -> list[str]:
    """Capture offscreen screenshots of the loaded Workbench at key states."""

    from PySide6.QtCore import QTimer, QEventLoop

    def _spin(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    workbench.resize(1600, 950)
    workbench.show()
    _spin(300)

    # 1. Initial drawing-row selection (default state)
    if workbench.drawing_list_v2.count():
        workbench.drawing_list_v2.setCurrentRow(0)
        _spin(1500)  # let the GpuViewport image actually decode + paint
    img1 = out_dir / "01_workbench_initial.png"
    workbench.grab().save(str(img1))
    saved.append(str(img1))

    # 2. Switch to Top issues tab (index 1) - first column tab widget.
    from PySide6.QtWidgets import QTabWidget
    tabs = workbench.findChildren(QTabWidget)
    if tabs:
        tabs[0].setCurrentIndex(1)
        _spin(400)
        img2 = out_dir / "02_top_issues_tab.png"
        workbench.grab().save(str(img2))
        saved.append(str(img2))

        # 3. Switch to Pattern groups tab (index 2)
        tabs[0].setCurrentIndex(2)
        _spin(400)
        img3 = out_dir / "03_pattern_groups_tab.png"
        workbench.grab().save(str(img3))
        saved.append(str(img3))

        tabs[0].setCurrentIndex(0)
        _spin(400)

    # 4. Click first zone in zone tree to render detail panel + selection.
    if workbench.zone_list_v2.topLevelItemCount():
        workbench.zone_list_v2.setCurrentItem(workbench.zone_list_v2.topLevelItem(0))
        _spin(400)
        img4 = out_dir / "04_zone_selected_detail.png"
        workbench.grab().save(str(img4))
        saved.append(str(img4))
    return saved


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    results_dir = args.results_dir.resolve()
    if not results_dir.exists():
        print(f"FATAL: acceptance results not found at {results_dir}", file=sys.stderr)
        return 2

    app = QApplication.instance() or QApplication(sys.argv)
    print("=== Workbench Acceptance Smoke ===")
    print(f"Loading pipeline result from {results_dir}")
    open_started = time.perf_counter()
    result = _build_pipeline_result(results_dir, args.a, args.b)
    workbench = DrawingCompareWorkbenchV2()
    workbench._on_auto_finished_v2(result)
    app.processEvents()
    runtime_metrics = {
        "app_open_to_dashboard_ms": round((time.perf_counter() - open_started) * 1000.0, 3)
    }

    print("\n--- Workbench widget state after load ---")
    print(_format_widget_state_table(workbench))

    print("\n--- Acceptance items ---")
    checks = _run_acceptance(workbench, results_dir, runtime_metrics=runtime_metrics)
    for chk in checks:
        print(chk.line())

    failed = [c for c in checks if not c.passed]
    print(f"\nResult: {len(checks) - len(failed)}/{len(checks)} passed")

    if not args.skip_screenshots:
        print("\n--- Capturing screenshots ---")
        try:
            screenshots_dir = (args.screenshots_dir or (results_dir.parent / "screenshots")).resolve()
            if screenshots_dir.exists():
                shutil.rmtree(screenshots_dir)
            saved = _capture_screenshots(workbench, screenshots_dir)
            for path in saved:
                print(f"  saved: {path}")
        except Exception as exc:
            print(f"  (screenshot capture skipped: {exc})")
            screenshots_dir = None
            saved = []
    else:
        screenshots_dir = None
        saved = []

    summary_path = results_dir / "workbench_acceptance_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed" if not failed else "failed",
                "passed": len(checks) - len(failed),
                "failed": len(failed),
                "total": len(checks),
                "checks": [asdict(check) for check in checks],
                "runtime_metrics": runtime_metrics,
                "screenshots_dir": str(screenshots_dir) if screenshots_dir else "",
                "screenshots": saved,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nAcceptance summary: {summary_path}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
