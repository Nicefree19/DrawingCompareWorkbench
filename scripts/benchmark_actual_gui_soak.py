# -*- coding: utf-8 -*-
"""P5-G22 actual Workbench GUI navigation soak benchmark.

This benchmark consumes an existing validation output and its viewer package,
loads the real viewer manifest into the Qt Workbench, and repeatedly exercises
drawing selection, matched PDF page stepping, and zone selection. Unlike the
P5-G16 replay benchmark, this drives the real Qt widgets and Workbench handlers.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # noqa: E402
    from scripts.benchmark_real_corpus_replay import (
        _corpus_summary,
        _dirty_worktree,
        _file_fingerprint,
        _load_json_dict,
        _module_available,
        _repo_relative,
        _resolve_customer_manifest,
        _resolve_viewer_root,
        _safe_float,
        _safe_int,
        _short_git_sha,
    )
except ModuleNotFoundError:  # pragma: no cover - release CLI copy without scripts package
    from benchmark_real_corpus_replay import (
        _corpus_summary,
        _dirty_worktree,
        _file_fingerprint,
        _load_json_dict,
        _module_available,
        _repo_relative,
        _resolve_customer_manifest,
        _resolve_viewer_root,
        _safe_float,
        _safe_int,
        _short_git_sha,
    )


BENCHMARK_ID = "p5_g22_actual_gui_soak"
PROFILE = "actual_gui_customer_corpus_soak"
SCHEMA_VERSION = 1


def _import_gui_hotpath():
    try:
        from scripts import benchmark_workbench_gui_hotpath as module
    except ModuleNotFoundError:  # pragma: no cover - release CLI copy without scripts package
        import benchmark_workbench_gui_hotpath as module
    return module


def _ensure_app():
    return _import_gui_hotpath()._ensure_app()


def _event_loop_gap_summary(values: list[float]) -> dict[str, Any]:
    return _import_gui_hotpath()._event_loop_gap_summary(values)


def _latency_summary(values: list[float]) -> dict[str, Any]:
    return _import_gui_hotpath()._latency_summary(values)


def _viewport_pdf_background_state(viewport: Any) -> dict[str, Any]:
    return _import_gui_hotpath()._viewport_pdf_background_state(viewport)


def _qprocess_not_running_state() -> Any:
    from PySide6.QtCore import QProcess

    return QProcess.NotRunning


def _dcw_module():
    from src.gui import drawing_compare_workbench as module

    return module


def _workbench_class():
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    return DrawingCompareWorkbenchV2


def _summarize_viewer_perf(viewer_root: Path) -> dict[str, Any]:
    from src.services.comparison.viewer_perf_summary import summarize_viewer_perf

    return summarize_viewer_perf(viewer_root)


def _sha256_for_files(paths: Iterable[Path], *, base: Optional[Path] = None) -> str:
    import hashlib

    payload = [_file_fingerprint(path, base=base) for path in paths]
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _find_viewer_manifest(validation_payload: dict[str, Any], validation_summary: Path, viewer_root: Optional[Path]) -> Optional[Path]:
    package = validation_payload.get("viewer_package") if isinstance(validation_payload.get("viewer_package"), dict) else {}
    output_paths = package.get("output_paths") if isinstance(package.get("output_paths"), dict) else {}
    candidates = [
        output_paths.get("viewer_manifest_json"),
        package.get("viewer_manifest_json"),
        output_paths.get("viewer_manifest"),
        package.get("viewer_manifest"),
    ]
    if viewer_root is not None:
        candidates.extend([Path(viewer_root) / "viewer_manifest.json", Path(viewer_root) / "viewer_manifest_v1.json"])
    candidates.append(validation_summary.parent / "viewer" / "viewer_manifest.json")

    bases = [validation_summary.parent, Path(str(validation_payload.get("output_dir") or validation_summary.parent)), _REPO_ROOT]
    for value in candidates:
        if not value:
            continue
        path = Path(value)
        if path.is_absolute() and path.exists():
            return path
        for base in bases:
            candidate = base / path
            if candidate.exists():
                return candidate
    return None


def _pair_is_pdf(pair: dict[str, Any]) -> bool:
    values = [pair.get("source_a"), pair.get("source_b"), pair.get("before_pdf"), pair.get("after_pdf")]
    return any(str(value or "").lower().endswith(".pdf") for value in values)


def _row_from_pair(pair: dict[str, Any]) -> dict[str, Any]:
    pair_id = str(pair.get("pair_id") or pair.get("pair_uuid") or pair.get("id") or "")
    overlay_count = _safe_int(pair.get("overlay_total_count") or pair.get("change_count") or pair.get("zone_count"))
    top_issues = pair.get("top_issues") if isinstance(pair.get("top_issues"), list) else []
    if overlay_count <= 0:
        overlay_count = len(top_issues)
    return {
        "pair_id": pair_id,
        "drawing_number": str(pair.get("drawing_number") or pair.get("label") or pair_id),
        "grade": str(pair.get("grade") or "B"),
        "priority_score": _safe_float(pair.get("priority_score"), 10.0),
        "raw_change_count": overlay_count,
        "zone_count": overlay_count,
        "review_issue_count": _safe_int(pair.get("review_issue_count"), len(top_issues)),
        "folded_issue_count": _safe_int(pair.get("folded_issue_count")),
        "cloud_region_count": _safe_int(pair.get("cloud_region_count"), len(top_issues)),
        "cloud_omitted_zone_count": _safe_int(pair.get("cloud_omitted_zone_count")),
        "top_layers": str(pair.get("top_layers") or pair.get("major_layers") or ""),
        "top_issues": top_issues,
        "preview_available": bool(pair.get("before_image") and pair.get("after_image")) or _pair_is_pdf(pair),
        "preview_status": str(pair.get("render_status") or "ready"),
    }


def _state_is_running(worker: Any) -> bool:
    try:
        if worker is None:
            return False
        if hasattr(worker, "isRunning"):
            return bool(worker.isRunning())
        if hasattr(worker, "state"):
            return worker.state() != _qprocess_not_running_state()
    except RuntimeError:
        return False
    return False


def _worker_snapshot(workbench: Any) -> dict[str, Any]:
    zone_controller = getattr(workbench, "_zone_render_controller_v2", None)
    zone_process = getattr(zone_controller, "_process", None)
    active_workers = {
        "auto_compare": _state_is_running(getattr(workbench, "_worker", None)),
        "pair_render": _state_is_running(getattr(workbench, "_render_worker", None)),
        "visible_tile": _state_is_running(getattr(workbench, "_visible_tile_worker_v2", None)),
        "full_tree_overlay": _state_is_running(getattr(workbench, "_full_zone_tree_overlay_worker_v2", None)),
        "full_tree_plan": _state_is_running(getattr(workbench, "_full_zone_tree_plan_worker_v2", None)),
        "zone_vector": _state_is_running(getattr(workbench, "_zone_vector_qprocess", None)),
        "zone_render_process": _state_is_running(zone_process),
        "zone_render_busy": bool(zone_controller.is_busy()) if zone_controller is not None else False,
    }
    retired = [
        worker
        for worker in list(getattr(workbench, "_retired_qthreads_v2", []) or [])
        if _state_is_running(worker)
    ]
    return {
        "active_workers": active_workers,
        "active_worker_count": sum(1 for value in active_workers.values() if value),
        "running_retired_qthread_count": len(retired),
    }


def _process_children_worker_count() -> int:
    try:
        from src.services.comparison.native_resource_sampler import worker_process_count

        return int(worker_process_count())
    except Exception:
        return 0


def _native_resource_snapshot() -> dict[str, Any]:
    try:
        from src.services.comparison.native_resource_sampler import native_resource_snapshot

        snapshot = native_resource_snapshot(include_worker_processes=False)
        return {
            "rss_mb": snapshot.get("rss_mb"),
            "process_handle_count": snapshot.get("process_handle_count"),
            "open_file_descriptor_count": snapshot.get("open_file_descriptor_count"),
            "gdi_handle_count": snapshot.get("gdi_handle_count"),
            "user_handle_count": snapshot.get("user_handle_count"),
            "native_resource_notes": snapshot.get("native_resource_notes") or [],
        }
    except Exception:
        return {
            "rss_mb": None,
            "process_handle_count": None,
            "open_file_descriptor_count": None,
            "gdi_handle_count": None,
            "user_handle_count": None,
            "native_resource_notes": ["native_resource_snapshot_failed"],
        }


def _numeric_slope_summary(samples: list[dict[str, Any]], *, key: str, warmup_visit: int) -> dict[str, Any]:
    tail = [
        sample
        for sample in samples
        if _safe_int(sample.get("visit_index"), -1) >= int(warmup_visit)
        and sample.get(key) is not None
    ]
    if len(tail) < 2:
        return {
            "available": False,
            "sample_count": len(tail),
            "slope_per_100_visits": None,
            "positive_end_delta": None,
            "tail_peak_delta": None,
        }
    first = tail[0]
    last = tail[-1]
    first_visit = _safe_int(first.get("visit_index"))
    last_visit = _safe_int(last.get("visit_index"))
    span = max(1, last_visit - first_visit)
    first_value = _safe_float(first.get(key))
    last_value = _safe_float(last.get(key))
    values = [_safe_float(sample.get(key)) for sample in tail if sample.get(key) is not None]
    positive_delta = max(0.0, last_value - first_value)
    return {
        "available": True,
        "sample_count": len(tail),
        "warmup_visit": int(warmup_visit),
        "slope_per_100_visits": round((positive_delta / span) * 100.0, 3),
        "positive_end_delta": round(positive_delta, 3),
        "tail_peak_delta": round(max(values) - min(values), 3) if values else 0.0,
    }


def _gate(
    name: str,
    observed: Any,
    threshold: Any,
    op: str,
    *,
    required: bool = True,
    domain: str = "",
    detail: str = "",
) -> dict[str, Any]:
    if not required:
        passed = True
    elif op == "==":
        passed = observed == threshold
    elif op == ">=":
        passed = _safe_float(observed, -1.0) >= _safe_float(threshold)
    elif op == "<=":
        passed = _safe_float(observed, 10**18) <= _safe_float(threshold)
    else:
        passed = False
    return {
        "name": name,
        "domain": domain,
        "passed": bool(passed),
        "observed": observed,
        "threshold": threshold,
        "actual": observed,
        "target": threshold,
        "op": op,
        "required": bool(required),
        "detail": detail,
    }


def _workbench_result_for_manifest(manifest_path: Path) -> SimpleNamespace:
    output_paths = {"viewer_manifest_json": str(manifest_path)}
    return SimpleNamespace(
        package_complete=True,
        first_review_metadata={},
        viewer_package=SimpleNamespace(output_paths=output_paths),
        artifact_package=SimpleNamespace(output_paths=output_paths),
        executive_package=SimpleNamespace(output_paths={}),
        compare_summary=SimpleNamespace(items=[]),
    )


def _load_workbench_with_manifest(manifest_path: Path, manifest: dict[str, Any]) -> Any:
    workbench = _workbench_class()()
    workbench._result = _workbench_result_for_manifest(manifest_path)
    workbench._load_ai_config_v2 = lambda: SimpleNamespace(enabled=False, use_embedding=False, use_llm=False)  # type: ignore[method-assign]
    workbench._load_viewer_manifest_v2()
    if not workbench._viewer_pairs_by_id:
        workbench._viewer_root = manifest_path.parent
        workbench._viewer_manifest_path = manifest_path
        workbench._viewer_manifest = manifest
        workbench._viewer_pairs_by_id = {
            str(pair.get("pair_id") or ""): pair
            for pair in manifest.get("pairs", [])
            if isinstance(pair, dict) and pair.get("pair_id")
        }
    workbench._drawing_rows = [
        _row_from_pair(pair)
        for pair in workbench._viewer_pairs_by_id.values()
        if isinstance(pair, dict) and str(pair.get("pair_id") or "")
    ]
    workbench._drawing_rows.sort(key=lambda row: str(row.get("drawing_number") or row.get("pair_id") or ""))
    workbench._refresh_drawing_list_v2()
    return workbench


def _select_drawing(workbench: Any, row_index: int) -> float:
    item = workbench.drawing_list_v2.item(row_index)
    started = time.perf_counter()
    workbench.drawing_list_v2.setCurrentItem(item)
    _ensure_app().processEvents()
    return round((time.perf_counter() - started) * 1000.0, 3)


def _select_zone(workbench: Any, zone_index: int) -> tuple[float, bool, str]:
    leaves = workbench._zone_leaf_items_v2()
    if not leaves:
        return 0.0, False, ""
    leaf = leaves[zone_index % len(leaves)]
    zone_id = str(leaf.data(0, _dcw_module().Qt.UserRole) or "")
    started = time.perf_counter()
    workbench._select_zone_leaf_v2(leaf)
    _ensure_app().processEvents()
    return round((time.perf_counter() - started) * 1000.0, 3), True, zone_id


def _step_page(workbench: Any, visit_index: int) -> tuple[float, bool]:
    pair_id = str((workbench._active_row or {}).get("pair_id") or "")
    pair = workbench._viewer_pairs_by_id.get(pair_id, {})
    if len(list(pair.get("page_match_pairs") or [])) < 2:
        return 0.0, False
    direction = 1 if visit_index % 2 == 0 else -1
    started = time.perf_counter()
    workbench._step_pdf_page_pair_v2(direction)
    _ensure_app().processEvents()
    return round((time.perf_counter() - started) * 1000.0, 3), True


def _wait_for_zone_worker_idle(workbench: Any, *, timeout_ms: float) -> bool:
    controller = getattr(workbench, "_zone_render_controller_v2", None)
    deadline = time.perf_counter() + max(0.0, float(timeout_ms)) / 1000.0
    app = _ensure_app()
    while controller is not None and controller.is_busy() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.005)
    return not bool(controller is not None and controller.is_busy())


def _cleanup_workbench(workbench: Any) -> tuple[bool, dict[str, Any]]:
    app = _ensure_app()
    ok = True
    try:
        ok = bool(workbench._stop_background_threads_for_close_v2()) and ok
    except Exception:
        ok = False
    try:
        workbench._zone_render_controller_v2.shutdown()
    except Exception:
        ok = False
    session = getattr(workbench, "_viewer_session", None)
    if session is not None:
        try:
            session.shutdown(wait=True, timeout=5.0)
        except Exception:
            ok = False
    app.processEvents()
    snapshot = _worker_snapshot(workbench)
    try:
        workbench.deleteLater()
    except Exception:
        ok = False
    app.processEvents()
    return ok, snapshot


def run_soak(args: argparse.Namespace) -> dict[str, Any]:
    validation_summary = Path(args.validation_summary)
    validation_payload = _load_json_dict(validation_summary)
    viewer_root = _resolve_viewer_root(validation_payload, validation_summary, args.viewer_root)
    manifest_path = _find_viewer_manifest(validation_payload, validation_summary, viewer_root)
    manifest = _load_json_dict(manifest_path) if manifest_path else {}
    customer_manifest_path = _resolve_customer_manifest(args, validation_summary)
    customer_manifest = _load_json_dict(customer_manifest_path) if customer_manifest_path else {}
    corpus = _corpus_summary(
        customer_manifest_path,
        customer_manifest,
        min_sheet_count=int(args.min_customer_sheet_count),
        max_sheet_count=int(args.max_customer_sheet_count),
    )

    app = _ensure_app()
    samples: list[dict[str, Any]] = []
    drawing_ms: list[float] = []
    page_ms: list[float] = []
    zone_ms: list[float] = []
    gaps_ms: list[float] = []
    blank_view_count = 0
    stale_active_pair_count = 0
    stale_active_zone_count = 0
    page_navigation_count = 0
    zone_selection_count = 0
    zone_leaf_missing_count = 0
    completed = bool(manifest_path and manifest.get("pairs"))
    start_snapshot = None
    cleanup_ok = False
    cleanup_snapshot: dict[str, Any] = {}
    workbench: Optional[Any] = None
    started = time.perf_counter()
    last_tick = started

    try:
        if manifest_path is None or not manifest.get("pairs"):
            completed = False
        else:
            workbench = _load_workbench_with_manifest(manifest_path, manifest)
            start_snapshot = _worker_snapshot(workbench)
            if args.skip_zone_render_workers:
                workbench._start_zone_crop_render_v2 = lambda _zone_id: None  # type: ignore[method-assign]
                workbench._apply_or_start_zone_vector_render_v2 = lambda *_a, **_kw: None  # type: ignore[method-assign]
            pair_count = workbench.drawing_list_v2.count()
            if pair_count <= 0:
                completed = False
            visits = max(1, int(args.visits))
            for visit_index in range(visits):
                if time.perf_counter() - started > max(1.0, float(args.timeout_s)):
                    completed = False
                    break
                row_index = visit_index % pair_count
                selection_ms = _select_drawing(workbench, row_index)
                drawing_ms.append(selection_ms)
                app.processEvents()
                step_ms, page_stepped = _step_page(workbench, visit_index)
                if page_stepped:
                    page_ms.append(step_ms)
                    page_navigation_count += 1
                zone_select_ms, zone_selected, zone_id = _select_zone(workbench, visit_index)
                if zone_selected:
                    zone_ms.append(zone_select_ms)
                    zone_selection_count += 1
                else:
                    zone_leaf_missing_count += 1
                _wait_for_zone_worker_idle(workbench, timeout_ms=float(args.zone_render_wait_ms))
                if float(args.settle_ms) > 0:
                    deadline = time.perf_counter() + float(args.settle_ms) / 1000.0
                    while time.perf_counter() < deadline:
                        app.processEvents()
                        time.sleep(0.005)

                now = time.perf_counter()
                gaps_ms.append(round((now - last_tick) * 1000.0, 3))
                last_tick = now
                active_pair = str((workbench._active_row or {}).get("pair_id") or "")
                expected_pair = str((workbench.drawing_list_v2.item(row_index).data(_dcw_module().Qt.UserRole) or {}).get("pair_id") or "")
                if active_pair != expected_pair:
                    stale_active_pair_count += 1
                active_zone = str(getattr(workbench, "_active_zone_id", "") or "")
                if zone_id and active_zone != zone_id:
                    stale_active_zone_count += 1
                before_state = _viewport_pdf_background_state(getattr(workbench, "preview_before_lightweight_v2", None))
                after_state = _viewport_pdf_background_state(getattr(workbench, "preview_after_lightweight_v2", None))
                if any(state.get("background_source_present") for state in (before_state, after_state)):
                    if not before_state.get("background_ready") or not after_state.get("background_ready"):
                        blank_view_count += 1
                resource = _native_resource_snapshot()
                samples.append(
                    {
                        "visit_index": int(visit_index),
                        "pair_id": active_pair,
                        "zone_id": active_zone,
                        "drawing_selection_ms": selection_ms,
                        "page_step_ms": step_ms if page_stepped else None,
                        "zone_selection_ms": zone_select_ms if zone_selected else None,
                        "gap_ms": gaps_ms[-1],
                        **resource,
                    }
                )
            completed = bool(completed and len(drawing_ms) == max(1, int(args.visits)))
    finally:
        if workbench is not None:
            cleanup_ok, cleanup_snapshot = _cleanup_workbench(workbench)

    viewer_perf_summary = _summarize_viewer_perf(viewer_root) if viewer_root else {}
    rss_slope = _numeric_slope_summary(samples, key="rss_mb", warmup_visit=max(0, int(args.warmup_visits)))
    handle_slope = _numeric_slope_summary(samples, key="process_handle_count", warmup_visit=max(0, int(args.warmup_visits)))
    fd_slope = _numeric_slope_summary(samples, key="open_file_descriptor_count", warmup_visit=max(0, int(args.warmup_visits)))
    gdi_slope = _numeric_slope_summary(samples, key="gdi_handle_count", warmup_visit=max(0, int(args.warmup_visits)))
    user_slope = _numeric_slope_summary(samples, key="user_handle_count", warmup_visit=max(0, int(args.warmup_visits)))
    orphan_worker_count = _process_children_worker_count()
    pairs = manifest.get("pairs") if isinstance(manifest.get("pairs"), list) else []
    native_measurement_available = any(
        sample.get("process_handle_count") is not None
        or sample.get("open_file_descriptor_count") is not None
        or sample.get("gdi_handle_count") is not None
        or sample.get("user_handle_count") is not None
        for sample in samples
    )
    worker_cleanup_ok = bool(
        cleanup_ok and cleanup_snapshot.get("active_worker_count", 0) == 0
    )
    native_resource_summary = {
        "measurement_available": native_measurement_available,
        "rss_slope": rss_slope,
        "process_handle_slope": handle_slope,
        "open_file_descriptor_slope": fd_slope,
        "gdi_handle_slope": gdi_slope,
        "user_handle_slope": user_slope,
        "positive_end_deltas": {
            "rss_mb": rss_slope.get("positive_end_delta"),
            "process_handle_count": handle_slope.get("positive_end_delta"),
            "open_file_descriptor_count": fd_slope.get("positive_end_delta"),
            "gdi_handle_count": gdi_slope.get("positive_end_delta"),
            "user_handle_count": user_slope.get("positive_end_delta"),
        },
    }
    worker_tree_summary = {
        "snapshot_start": start_snapshot or {},
        "snapshot_after_cleanup": cleanup_snapshot,
        "cleanup_ok": worker_cleanup_ok,
        "orphan_worker_count": int(orphan_worker_count),
    }
    summary = {
        "validation_summary_present": bool(validation_payload),
        "viewer_root_present": viewer_root is not None and viewer_root.exists(),
        "viewer_manifest_present": manifest_path is not None and manifest_path.exists(),
        "pair_count": len(pairs),
        "pdf_pair_count": sum(1 for pair in pairs if isinstance(pair, dict) and _pair_is_pdf(pair)),
        "visit_count": max(1, int(args.visits)),
        "completed_visit_count": len(samples),
        "gui_soak_completed": bool(completed),
        "drawing_selection_ms": _latency_summary(drawing_ms),
        "page_navigation_count": page_navigation_count,
        "page_step_ms": _latency_summary(page_ms),
        "zone_selection_count": zone_selection_count,
        "zone_leaf_missing_count": zone_leaf_missing_count,
        "zone_selection_ms": _latency_summary(zone_ms),
        "event_loop_gap_ms": _event_loop_gap_summary(gaps_ms),
        "blank_view_count": blank_view_count,
        "stale_active_pair_count": stale_active_pair_count,
        "stale_active_zone_count": stale_active_zone_count,
        "viewer_perf_stale_count": _safe_int(viewer_perf_summary.get("selected_zone_stale_count")),
        "viewer_perf_cancel_count": _safe_int(viewer_perf_summary.get("selected_zone_cancel_count")),
        "viewer_perf_fallback_count": _safe_int(viewer_perf_summary.get("selected_zone_fallback_count")),
        "rss_measurement_available": any(sample.get("rss_mb") is not None for sample in samples),
        "rss_slope": rss_slope,
        "native_resource_measurement_available": native_measurement_available,
        "native_resource_summary": native_resource_summary,
        "process_handle_slope": handle_slope,
        "open_file_descriptor_slope": fd_slope,
        "gdi_handle_slope": gdi_slope,
        "user_handle_slope": user_slope,
        "worker_snapshot_start": start_snapshot or {},
        "worker_snapshot_after_cleanup": cleanup_snapshot,
        "worker_tree_summary": worker_tree_summary,
        "worker_cleanup_ok": worker_cleanup_ok,
        "orphan_worker_count": int(orphan_worker_count),
    }
    gates = [
        _gate("p5_g22_validation_summary_present", summary["validation_summary_present"], True, "==", domain="source"),
        _gate("p5_g22_viewer_root_present", summary["viewer_root_present"], True, "==", domain="source"),
        _gate("p5_g22_viewer_manifest_present", summary["viewer_manifest_present"], True, "==", domain="source"),
        _gate("p5_g22_customer_manifest_present", corpus["manifest_present"], True, "==", required=bool(args.require_customer_corpus), domain="corpus"),
        _gate("p5_g22_real_corpus_declared", corpus["evidence_level"], "customer_grade", "==", required=bool(args.require_customer_corpus), domain="corpus"),
        _gate("p5_g22_customer_sheet_count_min", corpus["sheet_count"], int(args.min_customer_sheet_count), ">=", required=bool(args.require_customer_corpus), domain="corpus"),
        _gate("p5_g22_customer_sheet_count_max", corpus["sheet_count"], int(args.max_customer_sheet_count), "<=", required=bool(args.require_customer_corpus), domain="corpus"),
        _gate("p5_g22_pair_count", summary["pair_count"], int(args.min_pair_count), ">=", domain="gui"),
        _gate("p5_g22_gui_soak_completed", summary["gui_soak_completed"], True, "==", domain="gui"),
        _gate("p5_g22_drawing_selection_p95_ms", summary["drawing_selection_ms"]["p95_ms"], float(args.drawing_selection_p95_target_ms), "<=", domain="gui"),
        _gate("p5_g22_page_navigation_count", summary["page_navigation_count"], int(args.min_page_navigation_count), ">=", domain="gui"),
        _gate("p5_g22_zone_selection_count", summary["zone_selection_count"], int(args.min_zone_selection_count), ">=", domain="gui"),
        _gate("p5_g22_zone_selection_p95_ms", summary["zone_selection_ms"]["p95_ms"], float(args.zone_selection_p95_target_ms), "<=", required=summary["zone_selection_count"] > 0, domain="gui"),
        _gate("p5_g22_event_loop_gap_max_ms", summary["event_loop_gap_ms"]["max_ms"], float(args.event_loop_gap_max_target_ms), "<=", domain="gui"),
        _gate("p5_g22_blank_view_count", summary["blank_view_count"], 0, "==", domain="visual"),
        _gate("p5_g22_stale_active_pair_count", summary["stale_active_pair_count"], 0, "==", domain="gui"),
        _gate("p5_g22_stale_active_zone_count", summary["stale_active_zone_count"], 0, "==", domain="gui"),
        _gate("p5_g22_viewer_perf_stale_count", summary["viewer_perf_stale_count"], 0, "==", domain="visual"),
        _gate("p5_g22_worker_cleanup_ok", summary["worker_cleanup_ok"], True, "==", domain="worker"),
        _gate("p5_g22_orphan_worker_count", summary["orphan_worker_count"], 0, "==", domain="worker"),
        _gate("p5_g22_rss_measurement_available", summary["rss_measurement_available"], True, "==", required=not bool(args.allow_missing_psutil), domain="rss"),
        _gate("p5_g22_rss_slope_mb_per_100_visits", rss_slope.get("slope_per_100_visits"), float(args.rss_slope_target_mb_per_100), "<=", required=rss_slope.get("available") is True, domain="rss"),
        _gate("p5_g22_rss_positive_end_delta_mb", rss_slope.get("positive_end_delta"), float(args.rss_end_delta_mb), "<=", required=rss_slope.get("available") is True, domain="rss"),
        _gate("p5_g22_rss_tail_peak_delta_mb", rss_slope.get("tail_peak_delta"), float(args.rss_tail_delta_mb), "<=", required=rss_slope.get("available") is True, domain="rss"),
        _gate("p5_g22_native_resource_measurement_available", summary["native_resource_measurement_available"], True, "==", required=not bool(args.allow_missing_native_resources), domain="native_resource"),
        _gate("p5_g22_process_handle_positive_end_delta", handle_slope.get("positive_end_delta"), float(args.native_handle_end_delta), "<=", required=handle_slope.get("available") is True, domain="native_resource"),
        _gate("p5_g22_open_file_descriptor_positive_end_delta", fd_slope.get("positive_end_delta"), float(args.file_descriptor_end_delta), "<=", required=fd_slope.get("available") is True, domain="native_resource"),
        _gate("p5_g22_gdi_handle_positive_end_delta", gdi_slope.get("positive_end_delta"), float(args.gdi_handle_end_delta), "<=", required=gdi_slope.get("available") is True, domain="native_resource"),
        _gate("p5_g22_user_handle_positive_end_delta", user_slope.get("positive_end_delta"), float(args.user_handle_end_delta), "<=", required=user_slope.get("available") is True, domain="native_resource"),
    ]
    status = "passed" if all(gate["passed"] for gate in gates) else "failed"
    output_json = Path(args.output_json) if args.output_json else validation_summary.parent / "p5_g22_actual_gui_soak.json"
    source_paths = [validation_summary]
    if manifest_path:
        source_paths.append(manifest_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "profile": PROFILE,
        "status": status,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "git_short_sha": _short_git_sha(),
            "dirty_worktree": _dirty_worktree(),
            "source_signature": _sha256_for_files(source_paths, base=validation_summary.parent),
            "validation_summary": _file_fingerprint(validation_summary, base=validation_summary.parent),
            "viewer_manifest": _file_fingerprint(manifest_path, base=validation_summary.parent) if manifest_path else {},
        },
        "args": {
            "validation_summary": _repo_relative(validation_summary, base=validation_summary.parent),
            "viewer_root": _repo_relative(viewer_root, base=validation_summary.parent) if viewer_root else "",
            "viewer_manifest": _repo_relative(manifest_path, base=validation_summary.parent) if manifest_path else "",
            "visits": max(1, int(args.visits)),
            "warmup_visits": max(0, int(args.warmup_visits)),
            "min_pair_count": int(args.min_pair_count),
            "min_page_navigation_count": int(args.min_page_navigation_count),
            "min_zone_selection_count": int(args.min_zone_selection_count),
            "skip_zone_render_workers": bool(args.skip_zone_render_workers),
            "allow_missing_psutil": bool(args.allow_missing_psutil),
            "allow_missing_native_resources": bool(args.allow_missing_native_resources),
            "require_customer_corpus": bool(args.require_customer_corpus),
            "customer_evidence_manifest": (
                _repo_relative(customer_manifest_path, base=validation_summary.parent)
                if customer_manifest_path
                else ""
            ),
        },
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "psutil_available": _module_available("psutil"),
            "qtpdf_available": _module_available("PySide6.QtPdf"),
            "allow_missing_psutil": bool(args.allow_missing_psutil),
            "allow_missing_native_resources": bool(args.allow_missing_native_resources),
        },
        "artifacts": {
            "output_json": _repo_relative(output_json, base=validation_summary.parent),
            "viewer_root": _repo_relative(viewer_root, base=validation_summary.parent) if viewer_root else "",
            "viewer_manifest": _repo_relative(manifest_path, base=validation_summary.parent) if manifest_path else "",
        },
        "corpus": corpus,
        "gates": gates,
        "summary": summary,
        "samples": samples,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-summary", type=Path, required=True)
    parser.add_argument("--viewer-root", type=Path, default=None)
    parser.add_argument("--customer-evidence-manifest", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--visits", type=int, default=100)
    parser.add_argument("--warmup-visits", type=int, default=20)
    parser.add_argument("--settle-ms", type=float, default=0.0)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--zone-render-wait-ms", type=float, default=250.0)
    parser.add_argument("--skip-zone-render-workers", action="store_true")
    parser.add_argument("--min-pair-count", type=int, default=1)
    parser.add_argument("--min-page-navigation-count", type=int, default=0)
    parser.add_argument("--min-zone-selection-count", type=int, default=1)
    parser.add_argument("--drawing-selection-p95-target-ms", type=float, default=500.0)
    parser.add_argument("--zone-selection-p95-target-ms", type=float, default=500.0)
    parser.add_argument("--event-loop-gap-max-target-ms", type=float, default=500.0)
    parser.add_argument("--rss-slope-target-mb-per-100", type=float, default=5.0)
    parser.add_argument("--rss-end-delta-mb", type=float, default=64.0)
    parser.add_argument("--rss-tail-delta-mb", type=float, default=128.0)
    parser.add_argument("--native-handle-end-delta", type=float, default=32.0)
    parser.add_argument("--file-descriptor-end-delta", type=float, default=32.0)
    parser.add_argument("--gdi-handle-end-delta", type=float, default=16.0)
    parser.add_argument("--user-handle-end-delta", type=float, default=16.0)
    parser.add_argument("--allow-missing-psutil", action="store_true")
    parser.add_argument("--allow-missing-native-resources", action="store_true")
    parser.add_argument("--require-customer-corpus", action="store_true")
    parser.add_argument("--min-customer-sheet-count", type=int, default=20)
    parser.add_argument("--max-customer-sheet-count", type=int, default=50)
    parser.add_argument("--fail-on-gate", dest="fail_on_gate", action="store_true", default=True)
    parser.add_argument("--no-fail-on-gate", dest="fail_on_gate", action="store_false")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    payload = run_soak(args)
    output_json = Path(args.output_json) if args.output_json else Path(args.validation_summary).parent / "p5_g22_actual_gui_soak.json"
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[p5-g22] json -> {output_json}")
    print(f"[p5-g22] status={payload['status']}")
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    if payload["status"] != "passed" and args.fail_on_gate:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
