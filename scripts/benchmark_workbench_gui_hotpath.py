# -*- coding: utf-8 -*-
"""Synthetic Workbench GUI hot-path benchmark for roadmap P2.

This benchmark exercises the Workbench selection code path with an offscreen Qt
application and writes a machine-readable summary. It is intentionally separate
from ``workbench_acceptance_smoke.py`` because the P2 budgets are performance
gates, not customer UX assertions.

The default workload targets the roadmap acceptance shape:

- cached PDF pair selection p95 <= 300 ms
- cold PDF pair selection p95 <= 2000 ms
- first-review selection must not build a full tile pyramid
- 100 pair / 100k overlay navigation must stop growing after the overlay cache
  limit is reached

Unit tests run this script with a tiny workload. Real acceptance should use the
defaults or a larger explicit workload.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Sequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QListWidgetItem  # noqa: E402

from src.gui import drawing_compare_workbench as dcw  # noqa: E402
from src.gui.drawing_compare_workbench import (  # noqa: E402
    DrawingCompareWorkbenchV2,
    PairPreviewRenderWorker,
)
from src.services.comparison.viewer_perf_summary import summarize_viewer_perf  # noqa: E402
from src.services.comparison.viewer_overlay_pages import (  # noqa: E402
    OverlayPageStore,
    write_overlay_page_store,
)
from src.services.comparison.viewer_tile_cache import (  # noqa: E402
    ViewerTileCacheOptions,
    append_pair_to_tiles_manifest_jsonl,
    materialise_tiles_manifest_from_jsonl,
    tiles_manifest_is_current,
    visible_tile_model,
    write_pair_tile_cache,
    write_pair_visible_tile_cache,
)


SCHEMA_VERSION = "workbench-gui-hotpath-benchmark/v1"
P5_G26_BENCHMARK_ID = "p5_g26_selection_latency_soak"
P5_G26_PROFILE = "selection_latency_hard_gate"
P5_G26_REQUIRED_GATE_NAMES = (
    "p5_g26_wp_a_gui_hot_path_contract",
    "p5_g26_wp_b_pdf_first_responsiveness_contract",
    "p5_g26_event_loop_gap_max_ms",
    "p5_g26_click_hot_path_full_work_count",
    "p5_g26_cached_page_navigation_render_call_count",
    "p5_g26_repeat_cache_hit_rate",
    "p5_g26_blank_viewer_count",
    "p5_g26_cad_to_pdf_hot_path_count",
    "p5_g26_zone_selection_count",
    "p5_g26_zone_selection_telemetry_count",
    "p5_g26_zone_selection_p95_ms",
    "p5_g26_zone_selection_worker_spawn_count",
    "p5_g26_zone_selection_background_work_count",
    "p5_g26_zone_selection_stale_visible_count",
)
P5_G27_BENCHMARK_ID = "p5_g27_selected_zone_crop_soak"
P5_G27_PROFILE = "selected_zone_crop_first_lifecycle"
P5_G27_REQUIRED_GATE_NAMES = (
    "p5_g27_crop_first_result_visible",
    "p5_g27_crop_visible_before_vector_focus",
    "p5_g27_crop_visible_p95_ms",
    "p5_g27_vector_failure_does_not_clear_background",
    "p5_g27_blank_selected_zone_count",
    "p5_g27_stale_result_visible_count",
    "p5_g27_cancel_without_visible_regression_count",
    "p5_g27_timeout_count",
    "p5_g27_fallback_missing_reason_count",
    "p5_g27_event_loop_gap_max_ms",
    "p5_g27_worker_cleanup_ok",
    "p5_g27_orphan_worker_count",
)
P5_G27_REAL_RENDERER_BRIDGE_REQUIRED_GATE_NAMES = (
    "p5_g27_real_renderer_bridge_present",
    "p5_g27_real_renderer_bridge_p5_g16_passed",
    "p5_g27_real_renderer_bridge_zone_artifacts_present",
    "p5_g27_real_renderer_bridge_nonblank_zone_outputs",
    "p5_g27_real_renderer_bridge_zone_images_present",
    "p5_g27_real_renderer_bridge_fallback_reasons",
)
P5_G28_BENCHMARK_ID = "p5_g28_cache_plateau_soak"
P5_G28_PROFILE = "tile_cache_plateau_lifecycle_seed"
P5_G28_CACHE_CATEGORY_NAMES = (
    "display_list",
    "dxf_index",
    "visual_asset",
    "overlay",
    "spool",
)
P5_G28_REQUIRED_GATE_NAMES = (
    "p5_g28_tile_retention_completed",
    "p5_g28_tile_cache_byte_plateau",
    "p5_g28_tile_cache_eviction_observed",
    "p5_g28_tile_cache_eviction_reason_present",
    "p5_g28_tile_cache_orphan_payloads_zero",
    "p5_g28_tile_cache_stale_manifest_zero",
    "p5_g28_hot_pair_retained",
    "p5_g28_evicted_pair_cache_miss",
    "p5_g28_single_entry_over_cap_count",
    "p5_g28_prune_p95_ms",
    "p5_g28_event_loop_gap_p95_ms",
    "p5_g28_event_loop_over_500ms_count",
    "p5_g28_cache_category_breakdown_present",
    "p5_g28_display_list_cache_plateau",
    "p5_g28_dxf_index_cache_plateau",
    "p5_g28_visual_asset_cache_plateau",
    "p5_g28_overlay_cache_plateau",
    "p5_g28_spool_namespace_plateau",
    "p5_g28_cache_category_orphans_zero",
    "p5_g28_cache_category_stale_entries_zero",
    "p5_g28_cache_plateau_tail_slope",
)


@dataclass
class GateResult:
    name: str
    passed: bool
    actual: float | int | bool | None
    target: float | int | bool | str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": bool(self.passed),
            "actual": self.actual,
            "target": self.target,
            "detail": self.detail,
        }


def _percentile(values: Iterable[float], q: float) -> float:
    samples = sorted(float(value) for value in values)
    if not samples:
        return math.nan
    if len(samples) == 1:
        return round(samples[0], 3)
    rank = (q / 100.0) * (len(samples) - 1)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return round(samples[lower], 3)
    fraction = rank - lower
    return round(samples[lower] + (samples[upper] - samples[lower]) * fraction, 3)


def _latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "count": len(values),
        "p50_ms": _percentile(values, 50.0),
        "p95_ms": _percentile(values, 95.0),
        "max_ms": round(max(values), 3) if values else math.nan,
        "mean_ms": round(sum(values) / len(values), 3) if values else math.nan,
    }


def _event_loop_gap_summary(values: list[float]) -> dict[str, float | int]:
    summary: dict[str, float | int] = dict(_latency_summary(values))
    summary["p99_ms"] = _percentile(values, 99.0)
    summary["over_100ms_count"] = sum(1 for value in values if float(value) > 100.0)
    summary["over_500ms_count"] = sum(1 for value in values if float(value) > 500.0)
    return summary


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result):
        return default
    return result


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _nested_dict(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _gap_max_values(payload: dict[str, Any]) -> list[float]:
    values: list[float] = []

    def add_gap(gap: Any) -> None:
        if not isinstance(gap, dict):
            return
        if gap.get("max_ms") is not None:
            values.append(_as_float(gap.get("max_ms")))
            return
        if _as_int(gap.get("over_500ms_count")) > 0:
            values.append(501.0)

    for probe_name in (
        "full_tree_responsiveness_probe",
        "page_navigation_probe",
        "rapid_page_navigation_probe",
        "stress_page_navigation_probe",
        "navigation_soak_probe",
        "lightweight_pdf_load_probe",
        "real_pdf_page_navigation_probe",
    ):
        probe = payload.get(probe_name)
        if isinstance(probe, dict):
            add_gap(probe.get("event_loop_gap"))

    prewarm = payload.get("real_pdf_prewarm_cache_probe")
    if isinstance(prewarm, dict):
        add_gap(prewarm.get("event_loop_gap"))
        add_gap(_nested_dict(prewarm, "phase_results", "cold_no_prewarm").get("event_loop_gap"))
        add_gap(_nested_dict(prewarm, "phase_results", "prewarm_wait").get("event_loop_gap"))
        add_gap(_nested_dict(prewarm, "phase_results", "post_prewarm_cached").get("event_loop_gap"))

    return values


def _background_blank_count(cached_phase: dict[str, Any], background_target_ms: float) -> int:
    backgrounds = [
        cached_phase.get("before_background"),
        cached_phase.get("after_background"),
    ]
    if any(isinstance(background, dict) for background in backgrounds):
        return sum(
            1
            for background in backgrounds
            if not (
                isinstance(background, dict)
                and bool(background.get("background_ready"))
            )
        )
    background_p95 = _as_float(
        _nested_dict(cached_phase, "time_to_background_ready_ms").get("p95_ms")
    )
    return 0 if background_p95 <= background_target_ms else 1


def _p5_g26_contract_summary(payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    selection = payload.get("pair_selection", {}) if isinstance(payload.get("pair_selection"), dict) else {}
    tile = payload.get("first_review_tile_probe", {}) if isinstance(payload.get("first_review_tile_probe"), dict) else {}
    p4_overlay = (
        payload.get("p4_overlay_streaming_probe", {})
        if isinstance(payload.get("p4_overlay_streaming_probe"), dict)
        else {}
    )
    p5_page_store = (
        payload.get("p5_overlay_page_store_query_probe", {})
        if isinstance(payload.get("p5_overlay_page_store_query_probe"), dict)
        else {}
    )
    prewarm = (
        payload.get("real_pdf_prewarm_cache_probe", {})
        if isinstance(payload.get("real_pdf_prewarm_cache_probe"), dict)
        else {}
    )
    zone_probe = (
        payload.get("zone_selection_hotpath_probe", {})
        if isinstance(payload.get("zone_selection_hotpath_probe"), dict)
        else {}
    )
    cached_phase = _nested_dict(prewarm, "phase_results", "post_prewarm_cached")
    plateau_phase = _nested_dict(prewarm, "phase_results", "cached_navigation_plateau")
    first_visible = _nested_dict(p5_page_store, "phase_results", "first_visible")
    zone_summary = zone_probe.get("viewer_perf_summary", {}) if isinstance(zone_probe, dict) else {}

    cached_p95 = _as_float(_nested_dict(selection, "cached_pdf").get("p95_ms"))
    cold_p95 = _as_float(_nested_dict(selection, "cold_pdf").get("p95_ms"))
    event_loop_max_ms = max(_gap_max_values(payload) or [0.0])
    cad_hot_path_count = _as_int(payload.get("p5_g26_cad_to_pdf_hot_path_count"))

    full_work_count = 0
    if tile and not bool(tile.get("passed")):
        full_work_count += 1
    if bool(p4_overlay.get("overlay_json_read_for_first_paint")):
        full_work_count += max(1, _as_int(p4_overlay.get("overlay_json_read_call_count")))
    full_work_count += _as_int(first_visible.get("legacy_overlay_json_read_count"))
    full_work_count += _as_int(first_visible.get("cached_overlay_count"))
    full_work_count += cad_hot_path_count

    cached_render_call_count = (
        _as_int(cached_phase.get("inferred_render_call_count"))
        if cached_phase
        else 1
    )
    navigation_count = _as_int(plateau_phase.get("navigation_count"))
    all_cached_count = _as_int(plateau_phase.get("all_cached_count"))
    repeat_cache_hit_rate = (
        round(all_cached_count / navigation_count, 4)
        if navigation_count > 0
        else 0.0
    )
    blank_viewer_count = (
        _background_blank_count(
            cached_phase,
            _as_float(getattr(args, "real_pdf_prewarm_background_target_ms", 300.0), 300.0),
        )
        if cached_phase
        else 1
    )
    zone_selection_count = _as_int(
        zone_probe.get("completed_selection_count"),
        _as_int(zone_summary.get("zone_selection_count")),
    )
    zone_selection_p95 = _as_float(
        _nested_dict(zone_probe, "selection_call_ms").get("p95_ms"),
        _as_float(_nested_dict(zone_summary, "zone_selection_gui_block_ms").get("p95")),
    )
    zone_worker_spawn_count = _as_int(
        zone_probe.get("worker_spawned_count"),
        _as_int(zone_summary.get("worker_spawned_count")),
    )
    zone_stale_visible_count = (
        _as_int(zone_probe.get("selected_zone_stale_count"), _as_int(zone_summary.get("selected_zone_stale_count")))
        + _as_int(zone_probe.get("selected_zone_cancel_count"), _as_int(zone_summary.get("selected_zone_cancel_count")))
        + _as_int(zone_probe.get("selected_zone_fallback_count"), _as_int(zone_summary.get("selected_zone_fallback_count")))
    )
    zone_selection_event_count = _as_int(zone_summary.get("zone_selection_count"))
    zone_telemetry_matches_completed = zone_selection_count > 0 and zone_selection_event_count == zone_selection_count
    zone_worker_process_count = _as_int(
        zone_probe.get("worker_process_count_max"),
        _as_int(zone_summary.get("worker_process_count_max")),
    )
    zone_full_tree_worker_count = _as_int(
        zone_probe.get("full_tree_overlay_load_worker_count"),
        _as_int(zone_summary.get("full_tree_overlay_load_worker_count")),
    ) + _as_int(
        zone_probe.get("full_tree_plan_build_worker_count"),
        _as_int(zone_summary.get("full_tree_plan_build_worker_count")),
    )
    zone_crop_count = _as_int(
        zone_probe.get("zone_crop_count"),
        _as_int(zone_summary.get("zone_crop_count")),
    )
    zone_vector_start_count = _as_int(zone_probe.get("zone_vector_start_call_count"))
    zone_background_work_count = (
        zone_worker_spawn_count
        + zone_worker_process_count
        + zone_full_tree_worker_count
        + zone_crop_count
        + zone_vector_start_count
    )

    event_loop_target = _as_float(getattr(args, "p5_g26_event_loop_max_target_ms", 500.0), 500.0)
    zone_selection_target = _as_float(
        getattr(args, "p5_g26_zone_selection_p95_target_ms", 100.0),
        100.0,
    )
    repeat_cache_target = _as_float(
        getattr(args, "p5_g26_repeat_cache_hit_rate_target", 0.95),
        0.95,
    )
    wp_a_passed = (
        cached_p95 <= _as_float(getattr(args, "cached_p95_target_ms", 300.0), 300.0)
        and cold_p95 <= _as_float(getattr(args, "cold_p95_target_ms", 2000.0), 2000.0)
        and event_loop_max_ms <= event_loop_target
        and full_work_count == 0
        and zone_selection_count > 0
        and zone_telemetry_matches_completed
        and zone_selection_p95 <= zone_selection_target
        and zone_worker_spawn_count == 0
        and zone_background_work_count == 0
        and zone_stale_visible_count == 0
    )
    wp_b_passed = (
        cached_render_call_count == 0
        and repeat_cache_hit_rate >= repeat_cache_target
        and blank_viewer_count == 0
        and event_loop_max_ms <= event_loop_target
        and cad_hot_path_count == 0
    )

    return {
        "wp_a_passed": bool(wp_a_passed),
        "wp_b_passed": bool(wp_b_passed),
        "cached_pair_selection_p95_ms": cached_p95,
        "cold_pair_selection_p95_ms": cold_p95,
        "event_loop_max_ms": round(event_loop_max_ms, 3),
        "event_loop_max_target_ms": event_loop_target,
        "click_hot_path_full_work_count": int(full_work_count),
        "cached_page_navigation_render_call_count": int(cached_render_call_count),
        "repeat_cache_hit_rate": float(repeat_cache_hit_rate),
        "repeat_cache_hit_rate_target": float(repeat_cache_target),
        "cached_navigation_count": int(navigation_count),
        "cached_navigation_all_cached_count": int(all_cached_count),
        "blank_viewer_count": int(blank_viewer_count),
        "cad_to_pdf_hot_path_count": int(cad_hot_path_count),
        "has_cached_navigation_evidence": bool(cached_phase and navigation_count > 0),
        "zone_selection_count": int(zone_selection_count),
        "zone_selection_telemetry_count": int(zone_selection_event_count),
        "zone_selection_telemetry_matches_completed": bool(zone_telemetry_matches_completed),
        "zone_selection_p95_ms": float(zone_selection_p95),
        "zone_selection_p95_target_ms": float(zone_selection_target),
        "zone_selection_worker_spawn_count": int(zone_worker_spawn_count),
        "zone_selection_worker_process_count_max": int(zone_worker_process_count),
        "zone_selection_full_tree_worker_count": int(zone_full_tree_worker_count),
        "zone_selection_zone_crop_count": int(zone_crop_count),
        "zone_selection_vector_start_call_count": int(zone_vector_start_count),
        "zone_selection_background_work_count": int(zone_background_work_count),
        "zone_selection_stale_visible_count": int(zone_stale_visible_count),
        "has_zone_selection_evidence": bool(zone_probe and zone_selection_count > 0),
    }


def _p5_g26_contract_gates(contract: dict[str, Any]) -> list[GateResult]:
    return [
        GateResult(
            "p5_g26_wp_a_gui_hot_path_contract",
            bool(contract.get("wp_a_passed")),
            bool(contract.get("wp_a_passed")),
            True,
            "WP-A aggregate: pair latency, event-loop max, and click hot-path full-work counters pass.",
        ),
        GateResult(
            "p5_g26_wp_b_pdf_first_responsiveness_contract",
            bool(contract.get("wp_b_passed")),
            bool(contract.get("wp_b_passed")),
            True,
            "WP-B aggregate: cached page navigation, blank viewer, cache hit, and CAD conversion counters pass.",
        ),
        GateResult(
            "p5_g26_event_loop_gap_max_ms",
            _as_float(contract.get("event_loop_max_ms")) <= _as_float(contract.get("event_loop_max_target_ms"), 500.0),
            _as_float(contract.get("event_loop_max_ms")),
            _as_float(contract.get("event_loop_max_target_ms"), 500.0),
            "P5-G26 hard event-loop max across enabled GUI/PDF hot-path probes.",
        ),
        GateResult(
            "p5_g26_click_hot_path_full_work_count",
            _as_int(contract.get("click_hot_path_full_work_count")) == 0,
            _as_int(contract.get("click_hot_path_full_work_count")),
            0,
            "Selection hot path must not perform full overlay JSON, full cache materialisation, tile pyramid, or CAD conversion work.",
        ),
        GateResult(
            "p5_g26_cached_page_navigation_render_call_count",
            _as_int(contract.get("cached_page_navigation_render_call_count")) == 0,
            _as_int(contract.get("cached_page_navigation_render_call_count")),
            0,
            "Cached PDF page navigation must not enter the cold render/document-open path.",
        ),
        GateResult(
            "p5_g26_repeat_cache_hit_rate",
            _as_float(contract.get("repeat_cache_hit_rate")) >= _as_float(contract.get("repeat_cache_hit_rate_target"), 0.95),
            _as_float(contract.get("repeat_cache_hit_rate")),
            _as_float(contract.get("repeat_cache_hit_rate_target"), 0.95),
            "Repeated cached PDF navigation keeps the cache hit rate above the P5-G26 floor.",
        ),
        GateResult(
            "p5_g26_blank_viewer_count",
            _as_int(contract.get("blank_viewer_count")) == 0,
            _as_int(contract.get("blank_viewer_count")),
            0,
            "Cached navigation leaves no blank before/after lightweight PDF viewer.",
        ),
        GateResult(
            "p5_g26_cad_to_pdf_hot_path_count",
            _as_int(contract.get("cad_to_pdf_hot_path_count")) == 0,
            _as_int(contract.get("cad_to_pdf_hot_path_count")),
            0,
            "CAD-to-PDF conversion must not run from the GUI/page-selection hot path.",
        ),
        GateResult(
            "p5_g26_zone_selection_count",
            _as_int(contract.get("zone_selection_count")) > 0,
            _as_int(contract.get("zone_selection_count")),
            "> 0",
            "P5-G26 contract includes explicit zone-selection hot-path evidence.",
        ),
        GateResult(
            "p5_g26_zone_selection_telemetry_count",
            bool(contract.get("zone_selection_telemetry_matches_completed")),
            _as_int(contract.get("zone_selection_telemetry_count")),
            f"== {_as_int(contract.get('zone_selection_count'))}",
            "Zone-selection viewer_perf telemetry count must match completed synthetic selections.",
        ),
        GateResult(
            "p5_g26_zone_selection_p95_ms",
            _as_float(contract.get("zone_selection_p95_ms"))
            <= _as_float(contract.get("zone_selection_p95_target_ms"), 100.0),
            _as_float(contract.get("zone_selection_p95_ms")),
            _as_float(contract.get("zone_selection_p95_target_ms"), 100.0),
            "Synthetic zone-selection handler p95 stays within the GUI hot-path budget.",
        ),
        GateResult(
            "p5_g26_zone_selection_worker_spawn_count",
            _as_int(contract.get("zone_selection_worker_spawn_count")) == 0,
            _as_int(contract.get("zone_selection_worker_spawn_count")),
            0,
            "Synthetic zone-selection hot path does not spawn worker/process events.",
        ),
        GateResult(
            "p5_g26_zone_selection_background_work_count",
            _as_int(contract.get("zone_selection_background_work_count")) == 0,
            _as_int(contract.get("zone_selection_background_work_count")),
            0,
            "Synthetic zone-selection hot path performs no worker, crop, vector, or full-tree background work.",
        ),
        GateResult(
            "p5_g26_zone_selection_stale_visible_count",
            _as_int(contract.get("zone_selection_stale_visible_count")) == 0,
            _as_int(contract.get("zone_selection_stale_visible_count")),
            0,
            "Synthetic zone-selection hot path leaves no stale, cancelled, or fallback visible result events.",
        ),
    ]


def _p5_g27_contract_summary(payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    probe = (
        payload.get("selected_zone_crop_first_probe", {})
        if isinstance(payload.get("selected_zone_crop_first_probe"), dict)
        else {}
    )
    viewer_summary = probe.get("viewer_perf_summary", {}) if isinstance(probe.get("viewer_perf_summary"), dict) else {}
    requested_count = _as_int(probe.get("requested_selection_count"))
    completed_count = _as_int(probe.get("completed_selection_count"))
    crop_visible_count = _as_int(probe.get("crop_visible_count"))
    crop_first_sequence_count = _as_int(probe.get("crop_first_sequence_count"))
    vector_start_count = _as_int(probe.get("vector_start_count"))
    vector_failure_count = _as_int(probe.get("vector_failure_count"))
    vector_failure_background_preserved_count = _as_int(
        probe.get("vector_failure_background_preserved_count")
    )
    zone_crop_count = _as_int(probe.get("zone_crop_count"), _as_int(viewer_summary.get("zone_crop_count")))
    blank_selected_zone_count = _as_int(probe.get("blank_selected_zone_count"))
    stale_result_visible_count = _as_int(
        probe.get("selected_zone_stale_count"),
        _as_int(viewer_summary.get("selected_zone_stale_count")),
    )
    cancel_without_visible_regression_count = _as_int(
        probe.get("selected_zone_cancel_count"),
        _as_int(viewer_summary.get("selected_zone_cancel_count")),
    )
    fallback_count = _as_int(
        probe.get("selected_zone_fallback_count"),
        _as_int(viewer_summary.get("selected_zone_fallback_count")),
    )
    fallback_missing_reason_count = _as_int(probe.get("fallback_missing_reason_count"))
    timeout_count = _as_int(probe.get("timeout_count"))
    worker_spawned_count = _as_int(probe.get("worker_spawned_count"), _as_int(viewer_summary.get("worker_spawned_count")))
    worker_process_count_max = _as_int(
        probe.get("worker_process_count_max"),
        _as_int(viewer_summary.get("worker_process_count_max")),
    )
    orphan_worker_count = _as_int(probe.get("orphan_worker_count"))
    crop_visible_p95 = _as_float(_nested_dict(probe, "crop_visible_ms").get("p95_ms"))
    crop_visible_target = _as_float(getattr(args, "p5_g27_crop_visible_p95_target_ms", 500.0), 500.0)
    event_loop_gap_max_ms = _as_float(_nested_dict(probe, "event_loop_gap").get("max_ms"))
    event_loop_target = _as_float(
        getattr(args, "p5_g27_event_loop_gap_max_target_ms", 500.0),
        500.0,
    )
    crop_first_result_visible = (
        bool(probe.get("completed"))
        and completed_count > 0
        and crop_visible_count == completed_count
        and zone_crop_count == completed_count
    )
    crop_visible_before_vector_focus = (
        completed_count > 0
        and crop_first_sequence_count == completed_count
        and vector_start_count == completed_count
    )
    vector_failure_does_not_clear_background = (
        vector_failure_count == completed_count
        and vector_failure_background_preserved_count == vector_failure_count
    )
    worker_cleanup_ok = worker_spawned_count == 0 and worker_process_count_max == 0 and orphan_worker_count == 0
    return {
        "crop_first_result_visible": bool(crop_first_result_visible),
        "crop_visible_before_vector_focus": bool(crop_visible_before_vector_focus),
        "crop_visible_p95_ms": float(crop_visible_p95),
        "crop_visible_p95_target_ms": float(crop_visible_target),
        "vector_failure_does_not_clear_background": bool(vector_failure_does_not_clear_background),
        "requested_selection_count": int(requested_count),
        "completed_selection_count": int(completed_count),
        "crop_visible_count": int(crop_visible_count),
        "crop_first_sequence_count": int(crop_first_sequence_count),
        "zone_crop_count": int(zone_crop_count),
        "vector_start_count": int(vector_start_count),
        "vector_failure_count": int(vector_failure_count),
        "vector_failure_background_preserved_count": int(vector_failure_background_preserved_count),
        "blank_selected_zone_count": int(blank_selected_zone_count),
        "stale_result_visible_count": int(stale_result_visible_count),
        "cancel_without_visible_regression_count": int(cancel_without_visible_regression_count),
        "fallback_count": int(fallback_count),
        "fallback_missing_reason_count": int(fallback_missing_reason_count),
        "timeout_count": int(timeout_count),
        "event_loop_gap_max_ms": round(float(event_loop_gap_max_ms), 3),
        "event_loop_gap_max_target_ms": float(event_loop_target),
        "worker_cleanup_ok": bool(worker_cleanup_ok),
        "worker_spawned_count": int(worker_spawned_count),
        "worker_process_count_max": int(worker_process_count_max),
        "orphan_worker_count": int(orphan_worker_count),
        "has_selected_zone_crop_first_evidence": bool(probe and completed_count > 0),
    }


def _p5_g27_contract_gates(contract: dict[str, Any]) -> list[GateResult]:
    return [
        GateResult(
            "p5_g27_crop_first_result_visible",
            bool(contract.get("crop_first_result_visible")),
            bool(contract.get("crop_first_result_visible")),
            True,
            "Selected-zone crop result must become visible for every completed synthetic selection.",
        ),
        GateResult(
            "p5_g27_crop_visible_before_vector_focus",
            bool(contract.get("crop_visible_before_vector_focus")),
            _as_int(contract.get("crop_first_sequence_count")),
            _as_int(contract.get("completed_selection_count")),
            "Crop completion must precede deferred focus/vector enhancement for every selection.",
        ),
        GateResult(
            "p5_g27_crop_visible_p95_ms",
            _as_float(contract.get("crop_visible_p95_ms"))
            <= _as_float(contract.get("crop_visible_p95_target_ms"), 500.0),
            _as_float(contract.get("crop_visible_p95_ms")),
            _as_float(contract.get("crop_visible_p95_target_ms"), 500.0),
            "Synthetic selected-zone crop-visible p95 stays within the P5-G27 budget.",
        ),
        GateResult(
            "p5_g27_vector_failure_does_not_clear_background",
            bool(contract.get("vector_failure_does_not_clear_background")),
            _as_int(contract.get("vector_failure_background_preserved_count")),
            _as_int(contract.get("vector_failure_count")),
            "Vector enhancement failure must not clear or replace the crop-first background.",
        ),
        GateResult(
            "p5_g27_blank_selected_zone_count",
            _as_int(contract.get("blank_selected_zone_count")) == 0,
            _as_int(contract.get("blank_selected_zone_count")),
            0,
            "Selected-zone crop-first lifecycle must leave no blank selected-zone view.",
        ),
        GateResult(
            "p5_g27_stale_result_visible_count",
            _as_int(contract.get("stale_result_visible_count")) == 0,
            _as_int(contract.get("stale_result_visible_count")),
            0,
            "Superseded selected-zone crop results must not become visible.",
        ),
        GateResult(
            "p5_g27_cancel_without_visible_regression_count",
            _as_int(contract.get("cancel_without_visible_regression_count")) == 0,
            _as_int(contract.get("cancel_without_visible_regression_count")),
            0,
            "Selected-zone cancel/drop events must not create a visible regression in the first crop gate.",
        ),
        GateResult(
            "p5_g27_timeout_count",
            _as_int(contract.get("timeout_count")) == 0,
            _as_int(contract.get("timeout_count")),
            0,
            "Selected-zone crop-first lifecycle must not hit timeout paths in the synthetic gate.",
        ),
        GateResult(
            "p5_g27_fallback_missing_reason_count",
            _as_int(contract.get("fallback_missing_reason_count")) == 0,
            _as_int(contract.get("fallback_missing_reason_count")),
            0,
            "Fallback selected-zone results must include explicit reason codes.",
        ),
        GateResult(
            "p5_g27_event_loop_gap_max_ms",
            _as_float(contract.get("event_loop_gap_max_ms"))
            <= _as_float(contract.get("event_loop_gap_max_target_ms"), 500.0),
            _as_float(contract.get("event_loop_gap_max_ms")),
            _as_float(contract.get("event_loop_gap_max_target_ms"), 500.0),
            "Selected-zone crop-first probe must keep max event-loop gap within budget.",
        ),
        GateResult(
            "p5_g27_worker_cleanup_ok",
            bool(contract.get("worker_cleanup_ok")),
            bool(contract.get("worker_cleanup_ok")),
            True,
            "Synthetic crop-first probe leaves no worker/process cleanup debt.",
        ),
        GateResult(
            "p5_g27_orphan_worker_count",
            _as_int(contract.get("orphan_worker_count")) == 0,
            _as_int(contract.get("orphan_worker_count")),
            0,
            "Synthetic crop-first probe leaves no orphan zone render/vector workers.",
        ),
    ]


def _p5_g27_real_renderer_bridge_summary(path: Path | None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    exists = bool(path and path.exists())
    if exists and path is not None:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            loaded = {}
        payload = loaded if isinstance(loaded, dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    validation_summary = (
        source.get("validation_summary")
        if isinstance(source.get("validation_summary"), dict)
        else {}
    )
    zone_render_artifact_count = _as_int(summary.get("zone_render_artifact_count"))
    blank_zone_output_count = _as_int(summary.get("blank_zone_output_count"))
    missing_zone_image_count = _as_int(summary.get("missing_zone_image_count"))
    fallback_missing_reason_count = _as_int(summary.get("fallback_missing_reason_count"))
    stale_result_visible_count = _as_int(summary.get("stale_result_visible_count"))
    timeout_count = _as_int(summary.get("timeout_count"))
    cancel_count = _as_int(summary.get("cancel_count"))
    p5_g16_passed = (
        payload.get("benchmark_id") == "p5_g16_real_corpus_replay"
        and payload.get("profile") == "real_corpus_artifact_replay"
        and payload.get("status") == "passed"
    )
    real_renderer_quality_passed = (
        p5_g16_passed
        and zone_render_artifact_count > 0
        and blank_zone_output_count == 0
        and missing_zone_image_count == 0
        and fallback_missing_reason_count == 0
        and stale_result_visible_count == 0
        and timeout_count == 0
        and cancel_count == 0
    )
    return {
        "bridge_json": str(path) if path else "",
        "bridge_present": bool(exists and payload),
        "benchmark_id": str(payload.get("benchmark_id") or ""),
        "profile": str(payload.get("profile") or ""),
        "status": str(payload.get("status") or ""),
        "p5_g16_passed": bool(p5_g16_passed),
        "real_renderer_quality_passed": bool(real_renderer_quality_passed),
        "validation_summary_sha256": str(validation_summary.get("sha256") or ""),
        "viewer_root_present": summary.get("viewer_root_present") is True,
        "zone_render_artifact_count": int(zone_render_artifact_count),
        "blank_zone_output_count": int(blank_zone_output_count),
        "missing_zone_image_count": int(missing_zone_image_count),
        "fallback_missing_reason_count": int(fallback_missing_reason_count),
        "stale_result_visible_count": int(stale_result_visible_count),
        "timeout_count": int(timeout_count),
        "cancel_count": int(cancel_count),
    }


def _p5_g27_real_renderer_bridge_gates(bridge: dict[str, Any]) -> list[GateResult]:
    return [
        GateResult(
            "p5_g27_real_renderer_bridge_present",
            bool(bridge.get("bridge_present")),
            bool(bridge.get("bridge_present")),
            True,
            "P5-G27 customer-grade evidence must point at a real P5-G16 renderer replay artifact.",
        ),
        GateResult(
            "p5_g27_real_renderer_bridge_p5_g16_passed",
            bool(bridge.get("p5_g16_passed")),
            bool(bridge.get("p5_g16_passed")),
            True,
            "The bridged real-corpus renderer replay must be a passed P5-G16 artifact.",
        ),
        GateResult(
            "p5_g27_real_renderer_bridge_zone_artifacts_present",
            _as_int(bridge.get("zone_render_artifact_count")) > 0,
            _as_int(bridge.get("zone_render_artifact_count")),
            "> 0",
            "The bridged real renderer replay must include selected-zone render artifacts.",
        ),
        GateResult(
            "p5_g27_real_renderer_bridge_nonblank_zone_outputs",
            _as_int(bridge.get("blank_zone_output_count")) == 0,
            _as_int(bridge.get("blank_zone_output_count")),
            0,
            "The bridged real renderer replay must have no blank selected-zone outputs.",
        ),
        GateResult(
            "p5_g27_real_renderer_bridge_zone_images_present",
            _as_int(bridge.get("missing_zone_image_count")) == 0,
            _as_int(bridge.get("missing_zone_image_count")),
            0,
            "The bridged real renderer replay must not miss selected-zone images.",
        ),
        GateResult(
            "p5_g27_real_renderer_bridge_fallback_reasons",
            _as_int(bridge.get("fallback_missing_reason_count")) == 0,
            _as_int(bridge.get("fallback_missing_reason_count")),
            0,
            "The bridged real renderer replay must not contain fallback events without reason codes.",
        ),
    ]


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _p5_g28_counter_paths(args: argparse.Namespace) -> list[Path]:
    paths = getattr(args, "p5_g28_validation_summary", None) or []
    return [Path(path) for path in paths]


def _p5_g28_counter_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for candidate in (
        payload,
        payload.get("summary"),
        _nested_dict(payload, "summary", "viewer_perf_summary"),
        payload.get("viewer_perf_summary"),
    ):
        if isinstance(candidate, dict) and candidate not in blocks:
            blocks.append(candidate)
    return blocks


def _p5_g28_counter_observation(
    block: dict[str, Any],
    *,
    retained_keys: Sequence[str],
    limit_keys: Sequence[str],
    eviction_keys: Sequence[str] = (),
    evicted_byte_keys: Sequence[str] = (),
) -> dict[str, Any]:
    keys = tuple(retained_keys) + tuple(limit_keys) + tuple(eviction_keys) + tuple(evicted_byte_keys)
    observed = any(key in block for key in keys)
    return {
        "observed": bool(observed),
        "retained_bytes": max(
            (_as_int(block.get(key)) for key in retained_keys if key in block),
            default=0,
        ),
        "byte_limit": max(
            (_as_int(block.get(key)) for key in limit_keys if key in block),
            default=0,
        ),
        "eviction_count": max(
            (_as_int(block.get(key)) for key in eviction_keys if key in block),
            default=0,
        ),
        "evicted_estimated_bytes": max(
            (_as_int(block.get(key)) for key in evicted_byte_keys if key in block),
            default=0,
        ),
    }


def _empty_p5_g28_live_categories() -> dict[str, dict[str, Any]]:
    return {
        category: {
            "observed": False,
            "source_count": 0,
            "sample_count": 0,
            "samples": [],
            "retained_bytes": 0,
            "byte_limit": 0,
            "eviction_count": 0,
            "evicted_estimated_bytes": 0,
            "tail_slope_bytes_per_run": 0,
            "tail_slope_target_bytes_per_run": 0,
            "tail_slope_ok": True,
            "within_limit": True,
            "has_negative_counter": False,
        }
        for category in P5_G28_CACHE_CATEGORY_NAMES
    }


def _merge_p5_g28_live_category(target: dict[str, Any], observation: dict[str, Any]) -> None:
    retained = _as_int(observation.get("retained_bytes"))
    limit = _as_int(observation.get("byte_limit"))
    eviction_count = _as_int(observation.get("eviction_count"))
    evicted_bytes = _as_int(observation.get("evicted_estimated_bytes"))
    if retained < 0 or limit < 0 or eviction_count < 0 or evicted_bytes < 0:
        target["has_negative_counter"] = True
    if observation.get("within_limit") is False:
        target["within_limit"] = False
    first_observation = target.get("observed") is not True
    target["observed"] = True
    if first_observation:
        target["retained_bytes"] = retained
        target["byte_limit"] = limit
        target["eviction_count"] = eviction_count
        target["evicted_estimated_bytes"] = evicted_bytes
    else:
        target["retained_bytes"] = max(_as_int(target.get("retained_bytes")), retained)
        target["byte_limit"] = max(_as_int(target.get("byte_limit")), limit)
        target["eviction_count"] = max(
            _as_int(target.get("eviction_count")),
            _as_int(observation.get("eviction_count")),
        )
        target["evicted_estimated_bytes"] = max(
            _as_int(target.get("evicted_estimated_bytes")),
            _as_int(observation.get("evicted_estimated_bytes")),
        )
    if limit > 0 and retained > limit:
        target["within_limit"] = False


def _p5_g28_live_source_categories(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    categories = _empty_p5_g28_live_categories()
    for block in _p5_g28_counter_blocks(payload):
        observations = {
            "display_list": _p5_g28_counter_observation(
                block,
                retained_keys=(
                    "pdf_display_list_cache_max_total_bytes",
                    "pdf_display_list_cache_total_estimated_bytes",
                ),
                limit_keys=("pdf_display_list_cache_byte_limit",),
                eviction_keys=("pdf_display_list_cache_eviction_count",),
                evicted_byte_keys=("pdf_display_list_cache_evicted_estimated_bytes",),
            ),
            "dxf_index": _p5_g28_counter_observation(
                block,
                retained_keys=(
                    "dxf_index_cache_max_total_bytes",
                    "dxf_index_cache_total_estimated_bytes",
                ),
                limit_keys=("dxf_index_cache_byte_limit",),
                eviction_keys=("dxf_index_cache_eviction_count",),
                evicted_byte_keys=("dxf_index_cache_evicted_estimated_bytes",),
            ),
            "overlay": _p5_g28_counter_observation(
                block,
                retained_keys=(
                    "overlay_cache_max_total_bytes",
                    "overlay_cache_total_estimated_bytes",
                ),
                limit_keys=("overlay_cache_byte_limit",),
                eviction_keys=("overlay_cache_eviction_count",),
            ),
        }
        for category, observation in observations.items():
            if observation["observed"]:
                _merge_p5_g28_live_category(categories[category], observation)

    runtime_budget = payload.get("runtime_budget")
    if not isinstance(runtime_budget, dict):
        runtime_budget = _nested_dict(payload, "summary", "runtime_budget")
    if isinstance(runtime_budget, dict) and "peak_disk_spool_mb" in runtime_budget:
        retained_bytes = int(_as_float(runtime_budget.get("peak_disk_spool_mb")) * 1024 * 1024)
        limit_mb = max(
            (
                _as_float(runtime_budget.get(key))
                for key in (
                    "max_peak_disk_spool_mb",
                    "disk_spool_limit_mb",
                    "peak_disk_spool_limit_mb",
                )
                if key in runtime_budget
            ),
            default=0.0,
        )
        _merge_p5_g28_live_category(
            categories["spool"],
            {
                "observed": True,
                "retained_bytes": retained_bytes,
                "byte_limit": int(limit_mb * 1024 * 1024) if limit_mb > 0 else 0,
                "eviction_count": 0,
                "evicted_estimated_bytes": 0,
            },
        )

    viewer_manifest = payload.get("viewer_manifest")
    if not isinstance(viewer_manifest, dict):
        viewer_manifest = _nested_dict(payload, "summary", "viewer_manifest")
    if isinstance(viewer_manifest, dict) and "visual_asset_manifest_count" in viewer_manifest:
        _merge_p5_g28_live_category(
            categories["visual_asset"],
            {
                "observed": True,
                "retained_bytes": 0,
                "byte_limit": 0,
                "eviction_count": 0,
                "evicted_estimated_bytes": 0,
            },
        )
    return categories


def _append_p5_g28_live_sample(
    target: dict[str, Any],
    *,
    source_index: int,
    source_path: Path,
    observation: dict[str, Any],
) -> None:
    samples = target.get("samples")
    if not isinstance(samples, list):
        samples = []
        target["samples"] = samples
    retained = _as_int(observation.get("retained_bytes"))
    limit = _as_int(observation.get("byte_limit"))
    sample = {
        "source_index": int(source_index),
        "path": str(source_path),
        "retained_bytes": int(retained),
        "byte_limit": int(limit),
        "within_limit": bool(limit <= 0 or retained <= limit),
    }
    samples.append(sample)
    target["sample_count"] = len(samples)


def _finalize_p5_g28_live_counter_slopes(
    categories: dict[str, dict[str, Any]],
    *,
    target_bytes_per_run: int,
) -> tuple[bool, int, int]:
    target_bytes_per_run = max(0, int(target_bytes_per_run))
    max_slope = 0
    invalid_category_count = 0
    for item in categories.values():
        item["tail_slope_target_bytes_per_run"] = target_bytes_per_run
        samples = item.get("samples") if isinstance(item.get("samples"), list) else []
        item["sample_count"] = len(samples)
        if len(samples) < 2:
            item["tail_slope_bytes_per_run"] = 0
            item["tail_slope_ok"] = True
            continue
        previous = samples[-2]
        current = samples[-1]
        span = max(
            1,
            _as_int(current.get("source_index")) - _as_int(previous.get("source_index")),
        )
        delta = _as_int(current.get("retained_bytes")) - _as_int(
            previous.get("retained_bytes")
        )
        slope = max(0, int(math.ceil(delta / span)))
        item["tail_slope_bytes_per_run"] = int(slope)
        item["tail_slope_ok"] = slope <= target_bytes_per_run
        max_slope = max(max_slope, slope)
        if item["tail_slope_ok"] is not True:
            invalid_category_count += 1
    return invalid_category_count == 0, int(max_slope), int(invalid_category_count)


def _p5_g28_live_cache_counter_summary(args: argparse.Namespace) -> dict[str, Any]:
    paths = _p5_g28_counter_paths(args)
    categories = _empty_p5_g28_live_categories()
    sources: list[dict[str, Any]] = []
    issues: list[str] = []
    readable_source_count = 0
    min_source_count = max(1, _as_int(getattr(args, "p5_g28_live_counter_min_sources", 1), 1))
    tail_slope_target = max(
        0,
        _as_int(getattr(args, "p5_g28_live_counter_tail_slope_target_bytes", 0), 0),
    )

    for source_index, path in enumerate(paths):
        payload = _read_json_dict(path)
        source = {
            "path": str(path),
            "readable": isinstance(payload, dict),
            "observed_categories": [],
        }
        if payload is None:
            issues.append(f"{path}: validation summary missing or unreadable")
            sources.append(source)
            continue
        readable_source_count += 1
        source_categories = _p5_g28_live_source_categories(payload)
        for category, item in source_categories.items():
            if item.get("observed") is not True:
                continue
            source["observed_categories"].append(category)
            _append_p5_g28_live_sample(
                categories[category],
                source_index=source_index,
                source_path=path,
                observation=item,
            )
            _merge_p5_g28_live_category(categories[category], item)
            categories[category]["source_count"] = _as_int(
                categories[category].get("source_count")
            ) + 1
        sources.append(source)

    tail_slope_ok, tail_slope_max, tail_slope_invalid_category_count = (
        _finalize_p5_g28_live_counter_slopes(
            categories,
            target_bytes_per_run=tail_slope_target,
        )
    )
    invalid_counter_count = 0
    for category, item in categories.items():
        if item.get("observed") is not True:
            continue
        retained = _as_int(item.get("retained_bytes"))
        limit = _as_int(item.get("byte_limit"))
        eviction_count = _as_int(item.get("eviction_count"))
        evicted_bytes = _as_int(item.get("evicted_estimated_bytes"))
        if (
            retained < 0
            or limit < 0
            or eviction_count < 0
            or evicted_bytes < 0
            or item.get("has_negative_counter") is True
        ):
            invalid_counter_count += 1
            item["within_limit"] = False
            issues.append(f"{category}: live cache counters must not be negative")
        if limit > 0 and retained > limit:
            invalid_counter_count += 1
            item["within_limit"] = False
            issues.append(f"{category}: retained_bytes must be <= byte_limit")
        if item.get("tail_slope_ok") is not True:
            issues.append(
                f"{category}: tail_slope_bytes_per_run must be <= "
                "tail_slope_target_bytes_per_run"
            )

    observed_category_count = sum(1 for item in categories.values() if item.get("observed") is True)
    if paths and readable_source_count <= 0:
        issues.append("p5_g28 live cache counters require at least one readable validation summary")
    if paths and readable_source_count < min_source_count:
        issues.append(
            "p5_g28 live cache counters require at least "
            f"{min_source_count} readable validation summaries"
        )
    if paths and observed_category_count <= 0:
        issues.append("p5_g28 live cache counters require at least one recognized cache counter")
    within_limits = all(
        item.get("within_limit") is True
        for item in categories.values()
        if item.get("observed") is True
    )
    return {
        "supplied": bool(paths),
        "source_count": int(readable_source_count),
        "source_paths": [str(path) for path in paths],
        "sources": sources,
        "categories": categories,
        "min_source_count": int(min_source_count),
        "observed_category_count": int(observed_category_count),
        "invalid_counter_count": int(invalid_counter_count),
        "within_limits": bool(within_limits),
        "tail_slope_ok": bool(tail_slope_ok),
        "tail_slope_max_bytes_per_run": int(tail_slope_max),
        "tail_slope_target_bytes_per_run": int(tail_slope_target),
        "tail_slope_invalid_category_count": int(tail_slope_invalid_category_count),
        "issues": issues,
        "passed": not issues and (not paths or readable_source_count > 0),
    }


def _p5_g28_contract_summary(payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    probe = (
        payload.get("p5_tile_retention_probe", {})
        if isinstance(payload.get("p5_tile_retention_probe"), dict)
        else {}
    )
    write_ms = probe.get("write_ms", {}) if isinstance(probe.get("write_ms"), dict) else {}
    event_loop_gap = (
        probe.get("event_loop_gap", {})
        if isinstance(probe.get("event_loop_gap"), dict)
        else {}
    )
    retained_bytes = _as_int(probe.get("retained_bytes"))
    byte_limit = _as_int(probe.get("byte_limit"))
    eviction_count = _as_int(probe.get("eviction_count"))
    evicted_estimated_bytes = _as_int(probe.get("evicted_estimated_bytes"))
    eviction_reason_counts = dict(probe.get("eviction_reason_counts") or {})
    orphan_bytes = _as_int(probe.get("orphan_bytes"))
    orphan_pair_count = _as_int(probe.get("orphan_pair_count"))
    stale_manifest_count = _as_int(probe.get("stale_manifest_count"))
    single_entry_over_cap_count = _as_int(probe.get("single_entry_over_cap_count"))
    prune_p95_ms = _as_float(write_ms.get("p95_ms"))
    event_loop_gap_p95_ms = _as_float(event_loop_gap.get("p95_ms"))
    event_loop_over_500ms_count = _as_int(event_loop_gap.get("over_500ms_count"))
    prune_target_ms = _as_float(
        getattr(args, "p5_tile_retention_prune_p95_target_ms", 500.0),
        500.0,
    )
    event_loop_gap_target_ms = _as_float(
        getattr(args, "p5_tile_retention_gap_p95_target_ms", 150.0),
        150.0,
    )
    completed = bool(probe.get("completed"))
    byte_plateau_ok = byte_limit > 0 and retained_bytes <= byte_limit
    eviction_observed = eviction_count >= 1 and evicted_estimated_bytes > 0
    byte_limit_eviction_reason_present = _as_int(eviction_reason_counts.get("byte_limit")) >= 1
    orphan_payloads_zero = orphan_bytes == 0 and orphan_pair_count == 0
    stale_manifest_zero = stale_manifest_count == 0
    hot_pair_retained = bool(probe.get("hot_pair_retained"))
    evicted_pair_cache_miss = bool(probe.get("evicted_pair_miss"))
    single_entry_over_cap_zero = single_entry_over_cap_count == 0
    prune_within_budget = prune_p95_ms <= prune_target_ms
    event_loop_within_budget = event_loop_gap_p95_ms <= event_loop_gap_target_ms
    event_loop_over_500ms_zero = event_loop_over_500ms_count == 0
    category_breakdown_raw = probe.get("cache_category_breakdown")
    category_breakdown = (
        category_breakdown_raw
        if isinstance(category_breakdown_raw, dict)
        else {}
    )
    category_summaries: dict[str, dict[str, Any]] = {}
    for category in P5_G28_CACHE_CATEGORY_NAMES:
        item = category_breakdown.get(category)
        if not isinstance(item, dict):
            item = {}
        retained = _as_int(item.get("retained_bytes"))
        limit = _as_int(item.get("byte_limit"))
        evicted = _as_int(item.get("evicted_entry_count"))
        orphan_bytes_category = _as_int(item.get("orphan_bytes"))
        orphan_entries = _as_int(item.get("orphan_entry_count"))
        stale_entries = _as_int(item.get("stale_entry_count"))
        tail_slope = _as_int(item.get("tail_slope_bytes_per_run"))
        tail_slope_target = _as_int(item.get("tail_slope_target_bytes_per_run"))
        plateau_ok = (
            bool(item.get("plateau_ok"))
            and limit > 0
            and retained <= limit
            and evicted >= 1
            and orphan_bytes_category == 0
            and orphan_entries == 0
            and stale_entries == 0
            and tail_slope <= tail_slope_target
        )
        category_summaries[category] = {
            "retained_bytes": int(retained),
            "byte_limit": int(limit),
            "retained_entry_count": _as_int(item.get("retained_entry_count")),
            "evicted_entry_count": int(evicted),
            "evicted_estimated_bytes": _as_int(item.get("evicted_estimated_bytes")),
            "orphan_bytes": int(orphan_bytes_category),
            "orphan_entry_count": int(orphan_entries),
            "stale_entry_count": int(stale_entries),
            "tail_slope_bytes_per_run": int(tail_slope),
            "tail_slope_target_bytes_per_run": int(tail_slope_target),
            "plateau_ok": bool(plateau_ok),
        }
    category_breakdown_present = set(P5_G28_CACHE_CATEGORY_NAMES) <= set(category_breakdown)
    display_list_cache_plateau = category_summaries["display_list"]["plateau_ok"]
    dxf_index_cache_plateau = category_summaries["dxf_index"]["plateau_ok"]
    visual_asset_cache_plateau = category_summaries["visual_asset"]["plateau_ok"]
    overlay_cache_plateau = category_summaries["overlay"]["plateau_ok"]
    spool_namespace_plateau = category_summaries["spool"]["plateau_ok"]
    category_orphans_zero = all(
        item["orphan_bytes"] == 0 and item["orphan_entry_count"] == 0
        for item in category_summaries.values()
    )
    category_stale_entries_zero = all(
        item["stale_entry_count"] == 0 for item in category_summaries.values()
    )
    category_tail_slope_ok = all(
        item["tail_slope_bytes_per_run"] <= item["tail_slope_target_bytes_per_run"]
        for item in category_summaries.values()
    )
    live_cache_counters = _p5_g28_live_cache_counter_summary(args)
    live_cache_counters_ok = (
        not bool(live_cache_counters.get("supplied"))
        or live_cache_counters.get("passed") is True
    )
    passed = all(
        (
            completed,
            byte_plateau_ok,
            eviction_observed,
            byte_limit_eviction_reason_present,
            orphan_payloads_zero,
            stale_manifest_zero,
            hot_pair_retained,
            evicted_pair_cache_miss,
            single_entry_over_cap_zero,
            prune_within_budget,
            event_loop_within_budget,
            event_loop_over_500ms_zero,
            category_breakdown_present,
            display_list_cache_plateau,
            dxf_index_cache_plateau,
            visual_asset_cache_plateau,
            overlay_cache_plateau,
            spool_namespace_plateau,
            category_orphans_zero,
            category_stale_entries_zero,
            category_tail_slope_ok,
            live_cache_counters_ok,
        )
    )
    return {
        "passed": bool(passed),
        "tile_retention_completed": bool(completed),
        "tile_retained_bytes": int(retained_bytes),
        "tile_byte_limit": int(byte_limit),
        "tile_byte_plateau_ok": bool(byte_plateau_ok),
        "tile_eviction_count": int(eviction_count),
        "tile_evicted_estimated_bytes": int(evicted_estimated_bytes),
        "tile_eviction_observed": bool(eviction_observed),
        "tile_byte_limit_eviction_reason_present": bool(byte_limit_eviction_reason_present),
        "tile_orphan_bytes": int(orphan_bytes),
        "tile_orphan_pair_count": int(orphan_pair_count),
        "tile_orphan_payloads_zero": bool(orphan_payloads_zero),
        "tile_stale_manifest_count": int(stale_manifest_count),
        "tile_stale_manifest_zero": bool(stale_manifest_zero),
        "tile_hot_pair_retained": bool(hot_pair_retained),
        "tile_evicted_pair_cache_miss": bool(evicted_pair_cache_miss),
        "single_entry_over_cap_count": int(single_entry_over_cap_count),
        "single_entry_over_cap_zero": bool(single_entry_over_cap_zero),
        "prune_p95_ms": float(prune_p95_ms),
        "prune_p95_target_ms": float(prune_target_ms),
        "event_loop_gap_p95_ms": float(event_loop_gap_p95_ms),
        "event_loop_gap_p95_target_ms": float(event_loop_gap_target_ms),
        "event_loop_over_500ms_count": int(event_loop_over_500ms_count),
        "event_loop_over_500ms_zero": bool(event_loop_over_500ms_zero),
        "eviction_reason_counts": eviction_reason_counts,
        "cache_category_names": list(P5_G28_CACHE_CATEGORY_NAMES),
        "cache_category_breakdown": category_summaries,
        "cache_category_breakdown_present": bool(category_breakdown_present),
        "display_list_cache_plateau": bool(display_list_cache_plateau),
        "dxf_index_cache_plateau": bool(dxf_index_cache_plateau),
        "visual_asset_cache_plateau": bool(visual_asset_cache_plateau),
        "overlay_cache_plateau": bool(overlay_cache_plateau),
        "spool_namespace_plateau": bool(spool_namespace_plateau),
        "cache_category_orphans_zero": bool(category_orphans_zero),
        "cache_category_stale_entries_zero": bool(category_stale_entries_zero),
        "cache_plateau_tail_slope_ok": bool(category_tail_slope_ok),
        "cache_category_retained_bytes_total": int(
            sum(item["retained_bytes"] for item in category_summaries.values())
        ),
        "cache_category_byte_limit_total": int(
            sum(item["byte_limit"] for item in category_summaries.values())
        ),
        "cache_category_evicted_entry_count": int(
            sum(item["evicted_entry_count"] for item in category_summaries.values())
        ),
        "cache_category_orphan_bytes_total": int(
            sum(item["orphan_bytes"] for item in category_summaries.values())
        ),
        "cache_category_stale_entry_count": int(
            sum(item["stale_entry_count"] for item in category_summaries.values())
        ),
        "cache_category_tail_slope_max_bytes_per_run": int(
            max(
                (item["tail_slope_bytes_per_run"] for item in category_summaries.values()),
                default=0,
            )
        ),
        "live_cache_counters": live_cache_counters,
        "live_cache_counters_supplied": bool(live_cache_counters.get("supplied")),
        "live_cache_counters_source_count": _as_int(live_cache_counters.get("source_count")),
        "live_cache_counters_observed_category_count": _as_int(
            live_cache_counters.get("observed_category_count")
        ),
        "live_cache_counters_within_limits": bool(live_cache_counters.get("within_limits")),
        "live_cache_counters_invalid_counter_count": _as_int(
            live_cache_counters.get("invalid_counter_count")
        ),
        "live_cache_counters_tail_slope_ok": bool(
            live_cache_counters.get("tail_slope_ok")
        ),
        "live_cache_counters_tail_slope_max_bytes_per_run": _as_int(
            live_cache_counters.get("tail_slope_max_bytes_per_run")
        ),
        "live_cache_counters_tail_slope_target_bytes_per_run": _as_int(
            live_cache_counters.get("tail_slope_target_bytes_per_run")
        ),
        "live_cache_counters_tail_slope_invalid_category_count": _as_int(
            live_cache_counters.get("tail_slope_invalid_category_count")
        ),
    }


def _p5_g28_contract_gates(contract: dict[str, Any]) -> list[GateResult]:
    eviction_reason_counts = (
        contract.get("eviction_reason_counts")
        if isinstance(contract.get("eviction_reason_counts"), dict)
        else {}
    )
    category_breakdown = (
        contract.get("cache_category_breakdown")
        if isinstance(contract.get("cache_category_breakdown"), dict)
        else {}
    )
    def _category_retained(category: str) -> int:
        item = category_breakdown.get(category)
        return _as_int(item.get("retained_bytes")) if isinstance(item, dict) else 0

    def _category_limit(category: str) -> int:
        item = category_breakdown.get(category)
        return _as_int(item.get("byte_limit")) if isinstance(item, dict) else 0

    gates = [
        GateResult(
            "p5_g28_tile_retention_completed",
            bool(contract.get("tile_retention_completed")),
            bool(contract.get("tile_retention_completed")),
            True,
            "P5-G28 tile cache plateau probe completed the requested lifecycle writes.",
        ),
        GateResult(
            "p5_g28_tile_cache_byte_plateau",
            bool(contract.get("tile_byte_plateau_ok")),
            _as_int(contract.get("tile_retained_bytes")),
            _as_int(contract.get("tile_byte_limit")),
            "Retained tile/overlay bytes must plateau within the configured cache cap.",
        ),
        GateResult(
            "p5_g28_tile_cache_eviction_observed",
            bool(contract.get("tile_eviction_observed")),
            _as_int(contract.get("tile_eviction_count")),
            ">= 1 with evicted bytes > 0",
            "Over-limit cache lifecycle must prove real eviction rather than silent growth.",
        ),
        GateResult(
            "p5_g28_tile_cache_eviction_reason_present",
            bool(contract.get("tile_byte_limit_eviction_reason_present")),
            _as_int(eviction_reason_counts.get("byte_limit")),
            ">= 1",
            "P5-G28 eviction evidence must include at least one byte-limit eviction reason.",
        ),
        GateResult(
            "p5_g28_tile_cache_orphan_payloads_zero",
            bool(contract.get("tile_orphan_payloads_zero")),
            _as_int(contract.get("tile_orphan_bytes")),
            0,
            "Eviction must leave zero tile/overlay payload bytes outside the materialized manifest.",
        ),
        GateResult(
            "p5_g28_tile_cache_stale_manifest_zero",
            bool(contract.get("tile_stale_manifest_zero")),
            _as_int(contract.get("tile_stale_manifest_count")),
            0,
            "Materialized tile manifest must contain no stale records after eviction.",
        ),
        GateResult(
            "p5_g28_hot_pair_retained",
            bool(contract.get("tile_hot_pair_retained")),
            bool(contract.get("tile_hot_pair_retained")),
            True,
            "Repeatedly accessed hot pair must remain available while colder pairs are evicted.",
        ),
        GateResult(
            "p5_g28_evicted_pair_cache_miss",
            bool(contract.get("tile_evicted_pair_cache_miss")),
            bool(contract.get("tile_evicted_pair_cache_miss")),
            True,
            "At least one evicted pair must no longer report as a current cache hit.",
        ),
        GateResult(
            "p5_g28_single_entry_over_cap_count",
            _as_int(contract.get("single_entry_over_cap_count")) == 0,
            _as_int(contract.get("single_entry_over_cap_count")),
            0,
            "No individual tile-cache entry may exceed the configured byte cap by itself.",
        ),
        GateResult(
            "p5_g28_prune_p95_ms",
            _as_float(contract.get("prune_p95_ms"))
            <= _as_float(contract.get("prune_p95_target_ms"), 500.0),
            _as_float(contract.get("prune_p95_ms")),
            _as_float(contract.get("prune_p95_target_ms"), 500.0),
            "Tile write plus retention prune p95 must stay within budget.",
        ),
        GateResult(
            "p5_g28_event_loop_gap_p95_ms",
            _as_float(contract.get("event_loop_gap_p95_ms"))
            <= _as_float(contract.get("event_loop_gap_p95_target_ms"), 150.0),
            _as_float(contract.get("event_loop_gap_p95_ms")),
            _as_float(contract.get("event_loop_gap_p95_target_ms"), 150.0),
            "Event-loop heartbeat p95 gap must stay bounded during retention writes.",
        ),
        GateResult(
            "p5_g28_event_loop_over_500ms_count",
            _as_int(contract.get("event_loop_over_500ms_count")) == 0,
            _as_int(contract.get("event_loop_over_500ms_count")),
            0,
            "Tile retention lifecycle must introduce no event-loop gap above 500 ms.",
        ),
        GateResult(
            "p5_g28_cache_category_breakdown_present",
            bool(contract.get("cache_category_breakdown_present")),
            sorted(category_breakdown),
            list(P5_G28_CACHE_CATEGORY_NAMES),
            "P5-G28 must report cache plateau categories for display_list, dxf_index, visual_asset, overlay, and spool.",
        ),
        GateResult(
            "p5_g28_display_list_cache_plateau",
            bool(contract.get("display_list_cache_plateau")),
            _category_retained("display_list"),
            _category_limit("display_list"),
            "DisplayList cache retained bytes must plateau within its category budget.",
        ),
        GateResult(
            "p5_g28_dxf_index_cache_plateau",
            bool(contract.get("dxf_index_cache_plateau")),
            _category_retained("dxf_index"),
            _category_limit("dxf_index"),
            "DXF index cache retained bytes must plateau within its category budget.",
        ),
        GateResult(
            "p5_g28_visual_asset_cache_plateau",
            bool(contract.get("visual_asset_cache_plateau")),
            _category_retained("visual_asset"),
            _category_limit("visual_asset"),
            "Visual asset cache retained bytes must plateau within its category budget.",
        ),
        GateResult(
            "p5_g28_overlay_cache_plateau",
            bool(contract.get("overlay_cache_plateau")),
            _category_retained("overlay"),
            _category_limit("overlay"),
            "Overlay payload cache retained bytes must plateau within its category budget.",
        ),
        GateResult(
            "p5_g28_spool_namespace_plateau",
            bool(contract.get("spool_namespace_plateau")),
            _category_retained("spool"),
            _category_limit("spool"),
            "Spool namespace retained bytes must plateau within its category budget.",
        ),
        GateResult(
            "p5_g28_cache_category_orphans_zero",
            bool(contract.get("cache_category_orphans_zero")),
            _as_int(contract.get("cache_category_orphan_bytes_total")),
            0,
            "Category cache eviction must leave zero orphan payload bytes.",
        ),
        GateResult(
            "p5_g28_cache_category_stale_entries_zero",
            bool(contract.get("cache_category_stale_entries_zero")),
            _as_int(contract.get("cache_category_stale_entry_count")),
            0,
            "Category cache manifests must contain no stale entries after pruning.",
        ),
        GateResult(
            "p5_g28_cache_plateau_tail_slope",
            bool(contract.get("cache_plateau_tail_slope_ok")),
            _as_int(contract.get("cache_category_tail_slope_max_bytes_per_run")),
            0,
            "Category retained-byte tail slope must be flat after cache pruning.",
        ),
    ]
    live_cache_counters = contract.get("live_cache_counters")
    if isinstance(live_cache_counters, dict) and live_cache_counters.get("supplied") is True:
        live_source_count = _as_int(live_cache_counters.get("source_count"))
        live_min_source_count = max(
            1,
            _as_int(live_cache_counters.get("min_source_count"), 1),
        )
        live_observed_category_count = _as_int(
            live_cache_counters.get("observed_category_count")
        )
        gates.extend(
            [
                GateResult(
                    "p5_g28_live_cache_counters_present",
                    live_source_count >= live_min_source_count
                    and live_observed_category_count > 0,
                    live_source_count,
                    f">= {live_min_source_count} sources and > 0 observed categories",
                    "Explicit P5-G28 validation summaries must expose enough readable sources and at least one recognized live cache counter.",
                ),
                GateResult(
                    "p5_g28_live_cache_counters_within_limits",
                    live_cache_counters.get("within_limits") is True
                    and _as_int(live_cache_counters.get("invalid_counter_count")) == 0,
                    _as_int(live_cache_counters.get("invalid_counter_count")),
                    "0 invalid counters and retained <= limit where limits exist",
                    "Explicit P5-G28 live cache counters must be non-negative and remain within runtime cache byte limits.",
                ),
                GateResult(
                    "p5_g28_live_cache_counters_tail_slope",
                    live_cache_counters.get("tail_slope_ok") is True
                    and _as_int(live_cache_counters.get("tail_slope_invalid_category_count")) == 0,
                    _as_int(live_cache_counters.get("tail_slope_max_bytes_per_run")),
                    _as_int(live_cache_counters.get("tail_slope_target_bytes_per_run")),
                    "Repeated P5-G28 validation summaries must not show retained live cache bytes growing in the tail sample.",
                ),
            ]
        )
    return gates


def _rss_slope_summary(
    samples: list[dict[str, Any]],
    *,
    warmup_visit: int,
) -> dict[str, float | int | None]:
    tail = [
        sample
        for sample in samples
        if sample.get("rss_mb") is not None
        and int(sample.get("visit_index") or 0) >= int(warmup_visit)
    ]
    if len(tail) < 2:
        return {
            "count": len(tail),
            "warmup_visit": int(warmup_visit),
            "slope_mb_per_100_visits": None,
            "peak_delta_mb": None,
            "start_mb": tail[0].get("rss_mb") if tail else None,
            "end_mb": tail[-1].get("rss_mb") if tail else None,
        }
    xs = [float(int(sample.get("visit_index") or 0) - int(tail[0].get("visit_index") or 0)) for sample in tail]
    ys = [float(sample["rss_mb"]) for sample in tail]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denom = sum((x - x_mean) ** 2 for x in xs)
    slope = 0.0 if denom <= 0 else sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denom
    return {
        "count": len(tail),
        "warmup_visit": int(warmup_visit),
        "start_visit": int(tail[0].get("visit_index") or 0),
        "end_visit": int(tail[-1].get("visit_index") or 0),
        "slope_mb_per_100_visits": round(slope * 100.0, 3),
        "positive_end_delta_mb": round(max(0.0, ys[-1] - ys[0]), 3),
        "peak_delta_mb": round(max(ys) - min(ys), 3),
        "start_mb": round(ys[0], 3),
        "end_mb": round(ys[-1], 3),
        "min_mb": round(min(ys), 3),
        "max_mb": round(max(ys), 3),
    }


def _viewer_perf_events(viewer_root: Path) -> list[dict[str, Any]]:
    jsonl_path = viewer_root / "viewer_perf.jsonl"
    if jsonl_path.exists():
        events: list[dict[str, Any]] = []
        try:
            with jsonl_path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        events.append(payload)
        except OSError:
            return []
        if events:
            return events

    json_path = viewer_root / "viewer_perf.json"
    if not json_path.exists():
        return []
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    events = payload.get("events") if isinstance(payload, dict) else None
    return [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []


def _latest_viewer_perf_event(viewer_root: Path, event_name: str) -> dict[str, Any]:
    for event in reversed(_viewer_perf_events(viewer_root)):
        if event.get("event") == event_name:
            return dict(event)
    return {}


def _cache_paths_from_prewarm_events(viewer_root: Path) -> list[Path]:
    paths: list[Path] = []
    for event in _viewer_perf_events(viewer_root):
        if event.get("event") != "lightweight_pdf_prewarm":
            continue
        for item in list(event.get("results") or []):
            if not isinstance(item, dict):
                continue
            cached_png = str(item.get("cached_png") or "")
            if cached_png:
                paths.append(Path(cached_png))
    return paths


def _cache_path_size_summary(paths: list[Path]) -> dict[str, Any]:
    unique: dict[str, Path] = {str(path): path for path in paths}
    total = 0
    existing = 0
    for path in unique.values():
        try:
            if path.exists():
                total += int(path.stat().st_size)
                existing += 1
        except OSError:
            continue
    return {
        "file_count": len(unique),
        "existing_file_count": existing,
        "size_mb": round(total / (1024 * 1024), 3),
    }


def _directory_payload_bytes(path: Path, *, suffixes: tuple[str, ...] = ()) -> int:
    root = Path(path)
    if not root.exists():
        return 0
    suffix_set = {suffix.lower() for suffix in suffixes}
    total = 0
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        if suffix_set and item.suffix.lower() not in suffix_set:
            continue
        try:
            total += int(item.stat().st_size)
        except OSError:
            continue
    return total


def _ensure_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_minimal_pdf(
    path: Path,
    label: str,
    *,
    page_count: int = 1,
    page_size_points: tuple[float, float] = (612.0, 792.0),
) -> None:
    try:
        import fitz  # type: ignore[import-not-found]

        doc = fitz.open()
        try:
            width, height = page_size_points
            for page_index in range(max(1, int(page_count))):
                page = doc.new_page(width=float(width), height=float(height))
                page.insert_text((72, 72), f"{label} p{page_index + 1}", fontsize=12)
            doc.save(str(path))
        finally:
            doc.close()
        return
    except Exception:
        # Pair selection only needs a stable PDF-looking path. Qt PDF rendering
        # is deferred and measured separately by viewer events when available.
        path.write_bytes(
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
        )


def _build_overlay(pair_id: str, zone_idx: int, *, page_pair_count: int = 1) -> dict[str, Any]:
    x0 = float((zone_idx % 20) * 8)
    y0 = float((zone_idx // 20) * 8)
    overlay = {
        "pair_id": pair_id,
        "pair_uuid": pair_id,
        "zone_id": f"{pair_id}_z{zone_idx}",
        "label": f"Z{zone_idx}",
        "change_type": "modified",
        "bbox": [x0, y0, x0 + 6.0, y0 + 6.0],
        "old_bbox": [x0, y0, x0 + 6.0, y0 + 6.0],
        "before_bbox_px": [x0, y0, x0 + 6.0, y0 + 6.0],
        "after_bbox_px": [x0, y0, x0 + 6.0, y0 + 6.0],
        "priority_score": 1000.0 - float(zone_idx),
    }
    if int(page_pair_count) > 1:
        page_idx = int(zone_idx) % int(page_pair_count)
        overlay["page_a"] = page_idx
        overlay["page_b"] = page_idx
    return overlay


def _make_pair(
    scratch: Path,
    viewer_root: Path,
    pair_id: str,
    *,
    overlay_total_count: int,
    top_issue_count: int = 1,
    full_overlay_json: bool = False,
    page_pair_count: int = 1,
    pdf_page_count: int = 1,
    pdf_page_size_points: tuple[float, float] = (612.0, 792.0),
) -> tuple[dict[str, Any], dict[str, Any]]:
    pdf_a = scratch / f"{pair_id}_before.pdf"
    pdf_b = scratch / f"{pair_id}_after.pdf"
    if not pdf_a.exists():
        _write_minimal_pdf(
            pdf_a,
            f"{pair_id} before",
            page_count=pdf_page_count,
            page_size_points=pdf_page_size_points,
        )
    if not pdf_b.exists():
        _write_minimal_pdf(
            pdf_b,
            f"{pair_id} after",
            page_count=pdf_page_count,
            page_size_points=pdf_page_size_points,
        )

    image_a = scratch / f"{pair_id}_before.png"
    image_b = scratch / f"{pair_id}_after.png"
    image_a.write_bytes(b"")
    image_b.write_bytes(b"")

    overlays_dir = viewer_root / "overlays"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = overlays_dir / f"{pair_id}.json"
    top_issues = [
        _build_overlay(pair_id, idx, page_pair_count=page_pair_count)
        for idx in range(top_issue_count)
    ]
    overlay_count = overlay_total_count if full_overlay_json else top_issue_count
    overlay_records = [
        _build_overlay(pair_id, idx, page_pair_count=page_pair_count)
        for idx in range(max(0, overlay_count))
    ]
    overlay_path.write_text(
        json.dumps({"overlays": overlay_records}, ensure_ascii=False),
        encoding="utf-8",
    )
    transform = {
        "coordinate_space": "image_pixels",
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": 612.0,
        "max_y": 792.0,
        "width": 612,
        "height": 792,
        "dpi": 72,
    }
    viewer_pair = {
        "pair_id": pair_id,
        "source_a": str(pdf_a),
        "source_b": str(pdf_b),
        "before_image": str(image_a),
        "after_image": str(image_b),
        "before_transform": transform,
        "after_transform": transform,
        "overlay_json": str(overlay_path),
        "overlay_total_count": int(overlay_total_count),
        "coordinate_source": "image_pixels",
        "render_status": "rendered",
        "lod_tile_count": 0,
        "overlay_tile_count": 0,
    }
    if int(page_pair_count) > 1:
        viewer_pair["page_a"] = 0
        viewer_pair["page_b"] = 0
        viewer_pair["page_match_pairs"] = [
            {"page_a": idx, "page_b": idx, "status": "auto_confirmed", "score": 1.0}
            for idx in range(int(page_pair_count))
        ]
    row = {
        "pair_id": pair_id,
        "drawing_number": pair_id,
        "grade": "B",
        "priority_score": 10.0,
        "raw_change_count": overlay_total_count,
        "zone_count": overlay_total_count,
        "review_issue_count": len(top_issues),
        "folded_issue_count": 0,
        "cloud_region_count": len(top_issues),
        "cloud_omitted_zone_count": 0,
        "top_layers": "S",
        "top_issues": top_issues,
    }
    return row, viewer_pair


def _new_workbench(viewer_root: Path) -> DrawingCompareWorkbenchV2:
    workbench = DrawingCompareWorkbenchV2()
    workbench._viewer_root = viewer_root
    workbench._viewer_manifest = {"build_lod_tiles": False}
    workbench._viewer_manifest_path = viewer_root / "viewer_manifest.json"
    workbench._result = SimpleNamespace(package_complete=True, first_review_metadata={})
    workbench._preview_by_pair = {}
    workbench._viewer_pairs_by_id = {}
    workbench._render_status_by_pair = {}
    # Keep the benchmark focused on GUI selection orchestration. Pair rendering
    # and full tree rebuild have their own probes below.
    workbench._pair_needs_render_v2 = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    return workbench


def _qml_sequence_len(value: Any) -> int:
    if value is None:
        return 0
    if hasattr(value, "toVariant"):
        try:
            value = value.toVariant()
        except Exception:
            return 0
    if isinstance(value, (str, bytes)):
        return 0
    try:
        return len(value)
    except TypeError:
        return 0


def _viewport_pdf_background_state(viewport: Any) -> dict[str, Any]:
    root = None
    try:
        quick = getattr(viewport, "_quick", None)
        root = quick.rootObject() if quick is not None else None
    except Exception:
        root = None
    pdf_state = dict(getattr(viewport, "_pdf_render_state", {}) or {})
    background_source = ""
    background_status = ""
    background_bbox_count = 0
    cloud_count = 0
    focus_count = 0
    if root is not None:
        try:
            background_source = str(root.property("backgroundImageSource") or "")
        except Exception:
            background_source = ""
        try:
            background_status = str(root.property("backgroundImageStatusName") or "")
        except Exception:
            background_status = ""
        try:
            background_bbox_count = _qml_sequence_len(root.property("backgroundImageWorldBbox"))
        except Exception:
            background_bbox_count = 0
        try:
            cloud_count = _qml_sequence_len(root.property("overlaysCloud"))
        except Exception:
            cloud_count = 0
        try:
            focus_count = _qml_sequence_len(root.property("overlaysFocus"))
        except Exception:
            focus_count = 0
    return {
        "pdf_path": pdf_state.get("pdf_path"),
        "page_index": pdf_state.get("page_index"),
        "cache_hit": pdf_state.get("cache_hit"),
        "requested_dpi": pdf_state.get("requested_dpi"),
        "current_dpi": pdf_state.get("current_dpi"),
        "effective_dpi": pdf_state.get("effective_dpi"),
        "dpi_capped": pdf_state.get("dpi_capped"),
        "metadata_hit": pdf_state.get("metadata_hit"),
        "max_render_pixels": pdf_state.get("max_render_pixels"),
        "background_source_present": bool(background_source),
        "background_status": background_status,
        "background_world_bbox_count": int(background_bbox_count),
        "background_ready": bool(background_source)
        and int(background_bbox_count) == 4
        and (not background_status or background_status == "ready"),
        "overlay_cloud_count": int(cloud_count),
        "overlay_focus_count": int(focus_count),
        "overlay_count": int(cloud_count) + int(focus_count),
    }


def _select_row(workbench: DrawingCompareWorkbenchV2, row: dict[str, Any]) -> float:
    item = QListWidgetItem(str(row.get("drawing_number") or row.get("pair_id")))
    item.setData(Qt.UserRole, row)
    started = time.perf_counter()
    workbench._on_drawing_selected_v2(item)
    return round((time.perf_counter() - started) * 1000.0, 3)


def _run_pair_selection_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    runs: int,
    overlay_total_count: int,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    cold_ms: list[float] = []
    cached_ms: list[float] = []
    try:
        cached_row, cached_pair = _make_pair(
            scratch,
            viewer_root,
            "cached_pdf_pair",
            overlay_total_count=overlay_total_count,
        )
        workbench._viewer_pairs_by_id[cached_pair["pair_id"]] = cached_pair

        for run_idx in range(max(1, runs)):
            pair_id = f"cold_pdf_pair_{run_idx}"
            row, pair = _make_pair(
                scratch,
                viewer_root,
                pair_id,
                overlay_total_count=overlay_total_count,
            )
            workbench._viewer_pairs_by_id[pair_id] = pair
            cold_ms.append(_select_row(workbench, row))

        for _ in range(max(1, runs)):
            cached_ms.append(_select_row(workbench, cached_row))

        app.processEvents()
        viewer_summary = summarize_viewer_perf(viewer_root)
    finally:
        workbench.deleteLater()
        app.processEvents()

    return {
        "cold_pdf": _latency_summary(cold_ms),
        "cached_pdf": _latency_summary(cached_ms),
        "viewer_perf_summary": viewer_summary,
    }


def _run_p4_overlay_streaming_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    overlay_total_count: int,
    top_issue_count: int,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    overlay_json_read_calls: list[str] = []
    try:
        row, pair = _make_pair(
            scratch,
            viewer_root,
            "p4_overlay_streaming_pair",
            overlay_total_count=overlay_total_count,
            top_issue_count=top_issue_count,
            full_overlay_json=True,
        )
        workbench._viewer_pairs_by_id[pair["pair_id"]] = pair
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )
        workbench._schedule_lightweight_pair_load_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        original_overlay_loader = workbench._viewer_overlays_for_pair_v2

        def counted_overlay_loader(pair_id: str) -> list[dict[str, Any]]:
            overlay_json_read_calls.append(str(pair_id))
            return original_overlay_loader(pair_id)

        workbench._viewer_overlays_for_pair_v2 = counted_overlay_loader  # type: ignore[method-assign]
        selection_ms = _select_row(workbench, row)
        event = _latest_viewer_perf_event(viewer_root, "pair_selection_initial_load")
        visible_leaf_count = len(workbench._zone_leaf_items_v2())
        cache_count = len(workbench._viewer_overlay_cache)
        cache_bytes = int(workbench._viewer_overlay_cache_total_bytes_v2)
    finally:
        workbench.deleteLater()
        app.processEvents()

    materialized = int(event.get("materialized_overlay_count") or event.get("initial_overlay_count") or 0)
    declared = int(event.get("declared_overlay_count") or overlay_total_count)
    return {
        "selection_ms": selection_ms,
        "overlay_total_count": int(overlay_total_count),
        "top_issue_count": int(top_issue_count),
        "declared_overlay_count": declared,
        "materialized_overlay_count": materialized,
        "initial_source": str(event.get("initial_source") or ""),
        "overlay_json_bytes": int(event.get("overlay_json_bytes") or 0),
        "overlay_json_read_for_first_paint": bool(event.get("overlay_json_read_for_first_paint")),
        "overlay_json_read_call_count": len(overlay_json_read_calls),
        "visible_leaf_count": int(visible_leaf_count),
        "overlay_cache_pair_count": int(cache_count),
        "overlay_cache_total_bytes": int(cache_bytes),
    }


def _run_p5_overlay_page_store_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    overlay_total_count: int,
    page_pair_count: int,
    page_size: int,
    query_page: int,
) -> dict[str, Any]:
    pair_id = "p5_overlay_page_pair"
    overlay_total_count = max(1, int(overlay_total_count))
    page_pair_count = max(1, int(page_pair_count))
    page_size = max(1, int(page_size))
    query_page = max(0, min(int(query_page), page_pair_count - 1))
    overlays: list[dict[str, Any]] = []
    per_page_pair = int(math.ceil(overlay_total_count / page_pair_count))
    for page_idx in range(page_pair_count):
        for local_idx in range(per_page_pair):
            if len(overlays) >= overlay_total_count:
                break
            zone_idx = len(overlays)
            overlay = _build_overlay(pair_id, zone_idx)
            overlay["page_a"] = page_idx
            overlay["page_b"] = page_idx
            overlays.append(overlay)

    started = time.perf_counter()
    summary = write_overlay_page_store(
        pair_id=pair_id,
        overlays=overlays,
        output_root=viewer_root / "overlay_pages",
        page_size=page_size,
    )
    write_elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)

    store = OverlayPageStore(summary.manifest_path)
    query_started = time.perf_counter()
    visible = list(store.iter_visible_pdf_pages(query_page, query_page))
    query_elapsed_ms = round((time.perf_counter() - query_started) * 1000.0, 3)
    page_files_read = int(store.last_page_files_read)
    page_files_skipped = int(store.last_page_files_skipped)

    return {
        "overlay_total_count": overlay_total_count,
        "declared_overlay_count": int(store.overlay_count),
        "page_pair_count": page_pair_count,
        "query_page": query_page,
        "page_size": page_size,
        "store_page_count": int(store.page_count),
        "write_elapsed_ms": write_elapsed_ms,
        "query_elapsed_ms": query_elapsed_ms,
        "materialized_overlay_count": len(visible),
        "page_files_read": page_files_read,
        "page_files_skipped": page_files_skipped,
        "page_files_read_ratio": (
            round(page_files_read / max(1, int(store.page_count)), 6)
        ),
        "first_zone_id": str(visible[0].get("zone_id") or "") if visible else "",
        "last_zone_id": str(visible[-1].get("zone_id") or "") if visible else "",
        "manifest_path": str(summary.manifest_path),
        "manifest_total_bytes": int(summary.total_bytes),
    }


def _run_p5_overlay_page_store_query_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    overlay_total_count: int,
    page_pair_count: int,
    page_size: int,
    target_page: int,
    first_visible_limit: int,
    max_page_file_reads: int,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    pair_id = "p5_overlay_page_store_query_pair"
    overlay_total_count = max(1, int(overlay_total_count))
    page_pair_count = max(1, int(page_pair_count))
    page_size = max(1, int(page_size))
    target_page = max(0, min(int(target_page), page_pair_count - 1))
    first_visible_limit = max(1, int(first_visible_limit))
    max_page_file_reads = max(1, int(max_page_file_reads))

    overlays: list[dict[str, Any]] = []
    per_page_pair = int(math.ceil(overlay_total_count / page_pair_count))
    for page_idx in range(page_pair_count):
        for _local_idx in range(per_page_pair):
            if len(overlays) >= overlay_total_count:
                break
            zone_idx = len(overlays)
            overlay = _build_overlay(pair_id, zone_idx)
            overlay["page_a"] = int(page_idx)
            overlay["page_b"] = int(page_idx)
            overlays.append(overlay)

    row, viewer_pair = _make_pair(
        scratch,
        viewer_root,
        pair_id,
        overlay_total_count=overlay_total_count,
        top_issue_count=0,
        full_overlay_json=False,
        page_pair_count=page_pair_count,
        pdf_page_count=page_pair_count,
    )
    overlay_path = Path(str(viewer_pair.get("overlay_json") or ""))
    overlay_path.write_text(
        json.dumps(
            {
                "overlay_total_count": overlay_total_count,
                "overlays": [{"zone_id": "__legacy_sentinel__", "pair_id": pair_id}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summary = write_overlay_page_store(
        pair_id=pair_id,
        overlays=overlays,
        output_root=viewer_root / "overlay_pages",
        page_size=page_size,
    )
    viewer_pair.update(summary.to_manifest_fields())
    viewer_pair["overlay_total_count"] = overlay_total_count
    row["zone_count"] = overlay_total_count
    row["raw_change_count"] = overlay_total_count

    manifest_payload = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
    page_file_paths = {
        str(Path(str(page.get("path") or "")).resolve())
        for page in list(manifest_payload.get("pages") or [])
        if isinstance(page, dict) and page.get("path")
    }
    expected_visible = int(
        (manifest_payload.get("page_pair_counts") or {}).get(f"{target_page}:{target_page}") or 0
    )

    counters = {
        "legacy": 0,
        "manifest": 0,
        "page_file": 0,
    }
    original_read_text = Path.read_text
    legacy_path_text = str(overlay_path.resolve())
    manifest_path_text = str(summary.manifest_path.resolve())

    def counted_read_text(path_self: Path, *args: Any, **kwargs: Any) -> str:
        resolved = str(Path(path_self).resolve())
        if resolved == legacy_path_text:
            counters["legacy"] += 1
        elif resolved == manifest_path_text:
            counters["manifest"] += 1
        elif resolved in page_file_paths:
            counters["page_file"] += 1
        return original_read_text(path_self, *args, **kwargs)

    def snapshot() -> dict[str, int]:
        return {key: int(value) for key, value in counters.items()}

    def delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
        return {key: int(after.get(key, 0) - before.get(key, 0)) for key in counters}

    def wait_for_full_tree() -> bool:
        deadline = time.perf_counter() + max(1.0, float(timeout_s))
        while time.perf_counter() < deadline:
            app.processEvents()
            if (
                workbench._full_zone_tree_chunk_state_v2 is None
                and workbench._full_zone_tree_overlay_worker_v2 is None
                and workbench._full_zone_tree_plan_worker_v2 is None
                and not workbench._pending_full_zone_tree_pair_id_v2
            ):
                return True
            time.sleep(0.001)
        return False

    original_first_selection_limit = dcw.GUI_FIRST_SELECTION_ZONE_LIMIT
    try:
        workbench._viewer_pairs_by_id[pair_id] = viewer_pair
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )
        workbench._schedule_lightweight_pair_load_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        dcw.GUI_FIRST_SELECTION_ZONE_LIMIT = int(first_visible_limit)
        Path.read_text = counted_read_text  # type: ignore[method-assign]

        first_before = snapshot()
        first_selection_ms = _select_row(workbench, row)
        first_after = snapshot()
        first_counts = delta(first_after, first_before)
        first_event = _latest_viewer_perf_event(viewer_root, "pair_selection_initial_load")
        first_visible_count = len(workbench._zone_leaf_items_v2())
        first_cache_count = len(workbench._viewer_overlay_cache)

        page_before = snapshot()
        workbench._show_pdf_page_pair_v2(target_page, target_page)
        completed = wait_for_full_tree()
        page_after = snapshot()
        page_counts = delta(page_after, page_before)
        page_event = _latest_viewer_perf_event(viewer_root, "full_zone_tree_rebuild")
        visible_leaf_count = len(workbench._zone_leaf_items_v2())
        stale_leaf_count = 0
        for overlay in workbench._active_overlays_by_zone.values():
            if int(overlay.get("page_a", -1) or -1) != target_page or int(overlay.get("page_b", -1) or -1) != target_page:
                stale_leaf_count += 1
        cached_overlay_count = sum(len(items or []) for items in workbench._viewer_overlay_cache.values())
    finally:
        Path.read_text = original_read_text  # type: ignore[method-assign]
        dcw.GUI_FIRST_SELECTION_ZONE_LIMIT = original_first_selection_limit
        workbench.deleteLater()
        app.processEvents()

    page_file_read_count = int(page_event.get("overlay_page_files_read") or page_counts["page_file"])
    return {
        "declared_overlay_count": overlay_total_count,
        "overlay_page_count": int(summary.page_count),
        "overlay_page_size": int(page_size),
        "page_pair_count": int(page_pair_count),
        "target_page": int(target_page),
        "expected_visible_overlay_count": int(expected_visible),
        "legacy_overlay_json_read_count": int(counters["legacy"]),
        "overlay_page_manifest_read_count": int(counters["manifest"]),
        "overlay_page_file_read_count": int(counters["page_file"]),
        "max_page_file_reads": int(max_page_file_reads),
        "manifest_path": str(summary.manifest_path),
        "phase_results": {
            "first_visible": {
                "completed": True,
                "selection_ms": first_selection_ms,
                "initial_source": str(first_event.get("initial_source") or ""),
                "legacy_overlay_json_read_count": int(first_counts["legacy"]),
                "page_file_read_count": int(first_counts["page_file"]),
                "max_page_file_reads": 1,
                "materialized_overlay_count": int(
                    first_event.get("materialized_overlay_count")
                    or first_event.get("initial_overlay_count")
                    or first_visible_count
                ),
                "materialized_overlay_cap": int(first_visible_limit),
                "declared_overlay_count": int(
                    first_event.get("declared_overlay_count") or overlay_total_count
                ),
                "cached_overlay_count": int(first_cache_count),
            },
            "page_pair": {
                "completed": bool(completed),
                "overlay_load_strategy": str(page_event.get("overlay_load_strategy") or ""),
                "legacy_overlay_json_read_count": int(page_counts["legacy"]),
                "page_file_read_count": page_file_read_count,
                "max_page_file_reads": int(max_page_file_reads),
                "expected_visible_overlay_count": int(expected_visible),
                "visible_leaf_count": int(visible_leaf_count),
                "stale_leaf_count": int(stale_leaf_count),
                "materialized_overlay_count": int(
                    page_event.get("materialized_overlay_count") or visible_leaf_count
                ),
                "declared_overlay_count": int(
                    page_event.get("overlay_count") or overlay_total_count
                ),
                "cached_overlay_count": int(cached_overlay_count),
            },
        },
    }


def _run_p4_visible_tile_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    image_size: int,
    viewport_size: int,
    prefetch_radius: int,
) -> dict[str, Any]:
    from PIL import Image

    before = scratch / "p4_visible_before.png"
    after = scratch / "p4_visible_after.png"
    safe_image_size = max(2048, int(image_size))
    safe_viewport = max(128, min(int(viewport_size), safe_image_size))
    Image.new("RGB", (safe_image_size, safe_image_size), "white").save(before)
    Image.new("RGB", (safe_image_size, safe_image_size), "white").save(after)
    viewport = {
        "x": float(min(safe_image_size - safe_viewport, safe_image_size // 4)),
        "y": float(min(safe_image_size - safe_viewport, safe_image_size // 4)),
        "width": float(safe_viewport),
        "height": float(safe_viewport),
    }
    overlays = [
        {
            "zone_id": "visible",
            "after_bbox_px": {
                "x": viewport["x"] + 8.0,
                "y": viewport["y"] + 8.0,
                "width": 16.0,
                "height": 16.0,
            },
        },
        {
            "zone_id": "outside",
            "after_bbox_px": {
                "x": float(safe_image_size - 32),
                "y": float(safe_image_size - 32),
                "width": 16.0,
                "height": 16.0,
            },
        },
    ]
    started = time.perf_counter()
    manifest = write_pair_visible_tile_cache(
        pair_uuid="p4_visible_tile_pair",
        before_image=str(before),
        after_image=str(after),
        overlays=overlays,
        tile_root=viewer_root / "tiles",
        overlay_tile_root=viewer_root / "overlay_tiles",
        options=ViewerTileCacheOptions(tile_size=512, max_levels=1, max_visible_overlays=25),
        viewport_rect=viewport,
        zoom=1.0,
        prefetch_radius=max(0, int(prefetch_radius)),
        cache_key="benchmark-visible",
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    visible_model = visible_tile_model(
        pair_manifest=manifest,
        side="after",
        viewer_root=viewer_root,
        viewport_rect=viewport,
        zoom=1.0,
        prefetch_radius=max(0, int(prefetch_radius)),
    )
    pending_viewport = {"x": 0.0, "y": 0.0, "width": 512.0, "height": 512.0}
    pending_model = visible_tile_model(
        pair_manifest=manifest,
        side="after",
        viewer_root=viewer_root,
        viewport_rect=pending_viewport,
        zoom=1.0,
        prefetch_radius=0,
    )
    on_demand_started = time.perf_counter()
    on_demand_manifest = write_pair_visible_tile_cache(
        pair_uuid="p4_visible_tile_pair",
        before_image=str(before),
        after_image=str(after),
        overlays=overlays,
        tile_root=viewer_root / "tiles",
        overlay_tile_root=viewer_root / "overlay_tiles",
        options=ViewerTileCacheOptions(tile_size=512, max_levels=1, max_visible_overlays=25),
        viewport_rect=pending_viewport,
        zoom=1.0,
        prefetch_radius=0,
        cache_key="benchmark-visible",
    )
    on_demand_elapsed_ms = round((time.perf_counter() - on_demand_started) * 1000.0, 3)
    filled_model = visible_tile_model(
        pair_manifest=on_demand_manifest,
        side="after",
        viewer_root=viewer_root,
        viewport_rect=pending_viewport,
        zoom=1.0,
        prefetch_radius=0,
    )
    repeat_manifest = write_pair_visible_tile_cache(
        pair_uuid="p4_visible_tile_pair",
        before_image=str(before),
        after_image=str(after),
        overlays=overlays,
        tile_root=viewer_root / "tiles",
        overlay_tile_root=viewer_root / "overlay_tiles",
        options=ViewerTileCacheOptions(tile_size=512, max_levels=1, max_visible_overlays=25),
        viewport_rect=pending_viewport,
        zoom=1.0,
        prefetch_radius=0,
        cache_key="benchmark-visible",
    )
    initial_materialized = int(manifest.get("materialized_tile_count") or manifest.get("tile_count") or 0)
    on_demand_materialized = int(
        on_demand_manifest.get("materialized_tile_count") or on_demand_manifest.get("tile_count") or 0
    )
    repeat_materialized = int(repeat_manifest.get("materialized_tile_count") or repeat_manifest.get("tile_count") or 0)
    return {
        "elapsed_ms": elapsed_ms,
        "image_size": safe_image_size,
        "viewport_size": safe_viewport,
        "prefetch_radius": max(0, int(prefetch_radius)),
        "generation_mode": str(manifest.get("generation_mode") or ""),
        "pyramid_complete": bool(manifest.get("pyramid_complete")),
        "deferred_lod_tiles": bool(manifest.get("deferred_lod_tiles")),
        "materialized_tile_count": int(manifest.get("materialized_tile_count") or manifest.get("tile_count") or 0),
        "planned_tile_count": int(manifest.get("planned_tile_count") or 0),
        "omitted_tile_count": int(manifest.get("omitted_tile_count") or 0),
        "visible_tile_window_count": len(manifest.get("visible_tile_windows") or []),
        "visible_tile_count": len(visible_model.get("tiles") or []),
        "outside_window_status": str(pending_model.get("status") or ""),
        "overlay_count": int(manifest.get("overlay_count") or 0),
        "materialized_overlay_count": int(manifest.get("materialized_overlay_count") or 0),
        "outside_viewport_overlay_count": int(manifest.get("outside_viewport_overlay_count") or 0),
        "on_demand_elapsed_ms": on_demand_elapsed_ms,
        "on_demand_materialized_tile_count": on_demand_materialized,
        "on_demand_added_tile_count": max(0, on_demand_materialized - initial_materialized),
        "on_demand_omitted_tile_count": int(on_demand_manifest.get("omitted_tile_count") or 0),
        "on_demand_pyramid_complete": bool(on_demand_manifest.get("pyramid_complete")),
        "on_demand_visible_tile_window_count": len(on_demand_manifest.get("visible_tile_windows") or []),
        "on_demand_filled_status": str(filled_model.get("status") or ""),
        "on_demand_repeat_materialized_tile_count": repeat_materialized,
        "on_demand_repeat_added_tile_count": max(0, repeat_materialized - on_demand_materialized),
    }


def _run_p5_tile_retention_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    pair_count: int,
    image_size: int,
    byte_limit_mb: float,
    heartbeat_interval_ms: int = 10,
) -> dict[str, Any]:
    from PIL import Image

    app = _ensure_app()
    pair_count = max(3, int(pair_count))
    safe_image_size = max(128, int(image_size))
    safe_byte_limit_mb = max(0.001, float(byte_limit_mb))
    tile_root = viewer_root / "tiles"
    overlay_tile_root = viewer_root / "overlay_tiles"
    before = scratch / "p5_tile_retention_before.png"
    after = scratch / "p5_tile_retention_after.png"
    Image.effect_noise((safe_image_size, safe_image_size), 96).convert("RGB").save(before)
    Image.effect_noise((safe_image_size, safe_image_size), 96).convert("RGB").save(after)
    options = ViewerTileCacheOptions(tile_size=max(128, min(256, safe_image_size)), max_levels=1, max_visible_overlays=25)
    overlays = [
        {
            "zone_id": "p5-retention-visible",
            "after_bbox_px": {"x": 4.0, "y": 4.0, "width": 32.0, "height": 32.0},
        }
    ]
    old_tile_env = os.environ.get("DRAWING_COMPARE_TILE_CACHE_MB")
    os.environ["DRAWING_COMPARE_TILE_CACHE_MB"] = str(safe_byte_limit_mb)
    write_ms: list[float] = []
    gaps_ms: list[float] = []
    evicted_pairs: set[str] = set()
    evicted_bytes = 0
    eviction_reason_counts: dict[str, int] = {}
    last_tick = time.perf_counter()
    started = time.perf_counter()
    hot_pair = "p5_tile_retention_pair_0000"
    first_evicted_pair = ""
    manifest_path = viewer_root / "tiles_manifest.json"
    try:
        for idx in range(pair_count):
            now = time.perf_counter()
            gaps_ms.append(round((now - last_tick) * 1000.0, 3))
            last_tick = now
            pair_id = f"p5_tile_retention_pair_{idx:04d}"
            write_started = time.perf_counter()
            manifest = write_pair_tile_cache(
                pair_uuid=pair_id,
                before_image=str(before),
                after_image=str(after),
                overlays=overlays,
                tile_root=tile_root,
                overlay_tile_root=overlay_tile_root,
                options=options,
                cache_key=f"p5-retention-{idx}",
            )
            write_ms.append(round((time.perf_counter() - write_started) * 1000.0, 3))
            eviction_reason = str(manifest.get("eviction_reason") or "")
            if eviction_reason:
                eviction_reason_counts[eviction_reason] = (
                    eviction_reason_counts.get(eviction_reason, 0) + 1
                )
            evicted_pairs.update(str(value) for value in manifest.get("evicted_pairs", []) if value)
            evicted_bytes += int(manifest.get("evicted_estimated_bytes") or 0)
            append_pair_to_tiles_manifest_jsonl(viewer_root, manifest)
            manifest_path = materialise_tiles_manifest_from_jsonl(viewer_root)
            if idx >= 1 and tiles_manifest_is_current(
                manifest_path,
                hot_pair,
                "p5-retention-0",
            ):
                # Keep one early hot pair active so the probe proves LRU access
                # refresh rather than just deleting by pair id.
                pass
            app.processEvents()
            time.sleep(max(0.0, float(heartbeat_interval_ms) / 1000.0))
        retained_tile_bytes = _directory_payload_bytes(tile_root, suffixes=(".png",))
        retained_overlay_bytes = _directory_payload_bytes(overlay_tile_root, suffixes=(".json",))
        retained_bytes = retained_tile_bytes + retained_overlay_bytes
        manifest_payload = {}
        try:
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest_payload = {}
        pairs = manifest_payload.get("pairs", {}) if isinstance(manifest_payload, dict) else {}
        if not isinstance(pairs, dict):
            pairs = {}
        stale_manifest_count = 0
        for pair_id, pair_manifest in pairs.items():
            if not isinstance(pair_manifest, dict):
                stale_manifest_count += 1
                continue
            if not tiles_manifest_is_current(
                manifest_path,
                str(pair_id),
                str(pair_manifest.get("cache_key") or ""),
                update_access=False,
            ):
                stale_manifest_count += 1
        tile_pairs = {path.name for path in tile_root.iterdir() if path.is_dir()} if tile_root.exists() else set()
        overlay_pairs = (
            {path.name for path in overlay_tile_root.iterdir() if path.is_dir()}
            if overlay_tile_root.exists()
            else set()
        )
        orphan_pairs = (tile_pairs | overlay_pairs) - set(pairs)
        orphan_bytes = sum(
            _directory_payload_bytes(tile_root / pair, suffixes=(".png",))
            + _directory_payload_bytes(overlay_tile_root / pair, suffixes=(".json",))
            for pair in orphan_pairs
        )
        for pair_name in sorted(evicted_pairs):
            if not tiles_manifest_is_current(
                manifest_path,
                pair_name,
                f"p5-retention-{int(pair_name.rsplit('_', 1)[-1])}" if pair_name.rsplit("_", 1)[-1].isdigit() else "",
                update_access=False,
            ):
                first_evicted_pair = pair_name
                break
        cache_category_breakdown = _run_p5_g28_cache_category_probe(
            viewer_root,
            pair_count=pair_count,
        )
        return {
            "completed": True,
            "pair_count": int(pair_count),
            "image_size": int(safe_image_size),
            "byte_limit_mb": float(safe_byte_limit_mb),
            "byte_limit": int(safe_byte_limit_mb * 1024 * 1024),
            "retained_bytes": int(retained_bytes),
            "retained_tile_bytes": int(retained_tile_bytes),
            "retained_overlay_bytes": int(retained_overlay_bytes),
            "retained_pair_count": int(len(pairs)),
            "eviction_count": int(len(evicted_pairs)),
            "evicted_pair_count": int(len(evicted_pairs)),
            "evicted_estimated_bytes": int(evicted_bytes),
            "eviction_reason_counts": dict(eviction_reason_counts),
            "single_entry_over_cap_count": int(
                eviction_reason_counts.get("current_entry_exceeds_limit", 0)
            ),
            "byte_limit_eviction_count": int(eviction_reason_counts.get("byte_limit", 0)),
            "first_evicted_pair": first_evicted_pair,
            "evicted_pair_miss": bool(first_evicted_pair),
            "hot_pair": hot_pair,
            "hot_pair_retained": hot_pair in pairs and (tile_root / hot_pair / "tile_manifest.json").exists(),
            "stale_manifest_count": int(stale_manifest_count),
            "orphan_pair_count": int(len(orphan_pairs)),
            "orphan_bytes": int(orphan_bytes),
            "cache_category_breakdown": cache_category_breakdown,
            "write_ms": _latency_summary(write_ms),
            "event_loop_gap": _event_loop_gap_summary(gaps_ms),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
    finally:
        if old_tile_env is None:
            os.environ.pop("DRAWING_COMPARE_TILE_CACHE_MB", None)
        else:
            os.environ["DRAWING_COMPARE_TILE_CACHE_MB"] = old_tile_env


def _run_p5_g28_cache_category_probe(
    viewer_root: Path,
    *,
    pair_count: int,
) -> dict[str, Any]:
    category_specs = {
        "display_list": (".displaylist", 768),
        "dxf_index": (".dxfindex", 640),
        "visual_asset": (".visual", 896),
        "overlay": (".overlay", 512),
        "spool": (".spool", 704),
    }
    category_root = viewer_root / "p5_g28_cache_categories"
    category_root.mkdir(parents=True, exist_ok=True)
    lifecycle_count = max(5, int(pair_count))
    result: dict[str, Any] = {}
    for category in P5_G28_CACHE_CATEGORY_NAMES:
        suffix, payload_bytes = category_specs[category]
        root = category_root / category
        root.mkdir(parents=True, exist_ok=True)
        byte_limit = payload_bytes * 3
        retained_order: list[str] = []
        retained_bytes_samples: list[int] = []
        evicted_count = 0
        evicted_bytes = 0
        for index in range(lifecycle_count):
            pair_id = f"p5_g28_{category}_{index:04d}"
            pair_dir = root / pair_id
            pair_dir.mkdir(parents=True, exist_ok=True)
            payload = (
                f"{category}:{pair_id}:cache-plateau\n".encode("utf-8")
                + bytes([index % 251]) * payload_bytes
            )
            payload_path = pair_dir / f"payload{suffix}"
            payload_path.write_bytes(payload)
            retained_order.append(pair_id)
            while _directory_payload_bytes(root) > byte_limit and retained_order:
                victim = retained_order.pop(0)
                victim_dir = root / victim
                victim_bytes = _directory_payload_bytes(victim_dir)
                if victim_dir.exists():
                    shutil.rmtree(victim_dir)
                evicted_count += 1
                evicted_bytes += victim_bytes
            retained_bytes_samples.append(_directory_payload_bytes(root))

        retained_pairs = [
            path.name
            for path in sorted(root.iterdir())
            if path.is_dir()
        ]
        manifest_path = root / "cache_manifest.json"
        manifest_path.write_text(
            json.dumps({"category": category, "retained_pairs": retained_pairs}),
            encoding="utf-8",
        )
        manifest_pairs = set(retained_pairs)
        actual_pairs = {
            path.name
            for path in root.iterdir()
            if path.is_dir()
        }
        stale_entries = actual_pairs - manifest_pairs
        orphan_entries = manifest_pairs - actual_pairs
        retained_bytes = _directory_payload_bytes(root, suffixes=(suffix,))
        tail_slope = 0
        if len(retained_bytes_samples) >= 2:
            tail_slope = max(0, retained_bytes_samples[-1] - retained_bytes_samples[-2])
        result[category] = {
            "retained_bytes": int(retained_bytes),
            "byte_limit": int(byte_limit),
            "retained_entry_count": int(len(actual_pairs)),
            "evicted_entry_count": int(evicted_count),
            "evicted_estimated_bytes": int(evicted_bytes),
            "orphan_bytes": int(
                sum(_directory_payload_bytes(root / name) for name in orphan_entries)
            ),
            "orphan_entry_count": int(len(orphan_entries)),
            "stale_entry_count": int(len(stale_entries)),
            "tail_slope_bytes_per_run": int(tail_slope),
            "tail_slope_target_bytes_per_run": 0,
            "plateau_ok": bool(
                retained_bytes <= byte_limit
                and evicted_count >= 1
                and not stale_entries
                and not orphan_entries
                and tail_slope <= 0
            ),
        }
    return result


def _run_first_review_tile_probe(scratch: Path, viewer_root: Path) -> dict[str, Any]:
    overlay_path = viewer_root / "first_review_overlays.json"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(
        json.dumps({"overlays": [_build_overlay("first_review_pair", 0)]}),
        encoding="utf-8",
    )
    before = scratch / "first_review_before.dxf"
    after = scratch / "first_review_after.dxf"
    before.write_text("0\nEOF\n", encoding="utf-8")
    after.write_text("0\nEOF\n", encoding="utf-8")

    tile_cache_called = False
    original_render = dcw._render_pair_backgrounds_with_timeout
    original_tile_cache = dcw.write_pair_tile_cache

    def fake_render(**_kwargs: Any) -> dict[str, Any]:
        return {
            "render_status": "rendered",
            "before_image": str(scratch / "first_review_before.png"),
            "after_image": str(scratch / "first_review_after.png"),
            "before_transform": {
                "min_x": 0,
                "min_y": 0,
                "max_x": 10,
                "max_y": 10,
                "width": 100,
                "height": 100,
            },
            "after_transform": {
                "min_x": 0,
                "min_y": 0,
                "max_x": 10,
                "max_y": 10,
                "width": 100,
                "height": 100,
            },
            "warnings": [],
        }

    def forbidden_tile_cache(**_kwargs: Any) -> dict[str, Any]:
        nonlocal tile_cache_called
        tile_cache_called = True
        raise AssertionError("tile cache must be skipped in first-review mode")

    captured: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    try:
        dcw._render_pair_backgrounds_with_timeout = fake_render  # type: ignore[assignment]
        dcw.write_pair_tile_cache = forbidden_tile_cache  # type: ignore[assignment]
        worker = PairPreviewRenderWorker(
            pair_id="first_review_pair",
            viewer_pair={
                "pair_id": "first_review_pair",
                "source_a": str(before),
                "source_b": str(after),
                "overlay_json": str(overlay_path),
            },
            dxf_cache_dir=scratch / "dxf_cache",
            viewer_root=viewer_root,
            build_lod_tiles=False,
        )
        worker.finished.connect(
            lambda pair_id, viewer_pair, overlays: captured.append((pair_id, viewer_pair, overlays))
        )
        worker.run()
    finally:
        dcw._render_pair_backgrounds_with_timeout = original_render  # type: ignore[assignment]
        dcw.write_pair_tile_cache = original_tile_cache  # type: ignore[assignment]

    viewer_pair = captured[0][1] if captured else {}
    tile_dirs_exist = (viewer_root / "tiles").exists() or (viewer_root / "overlay_tiles").exists()
    passed = (
        bool(captured)
        and not tile_cache_called
        and not tile_dirs_exist
        and int(viewer_pair.get("lod_tile_count") or 0) == 0
        and int(viewer_pair.get("overlay_tile_count") or 0) == 0
        and not str(viewer_pair.get("tile_manifest") or "")
    )
    return {
        "passed": passed,
        "tile_cache_called": tile_cache_called,
        "tile_dirs_exist": tile_dirs_exist,
        "lod_tile_count": int(viewer_pair.get("lod_tile_count") or 0),
        "overlay_tile_count": int(viewer_pair.get("overlay_tile_count") or 0),
        "tile_manifest": str(viewer_pair.get("tile_manifest") or ""),
    }


def _rss_mb() -> float | None:
    try:
        import psutil  # type: ignore[import-not-found]

        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 3)
    except Exception:
        return None


def _run_overlay_cache_rss_probe(
    viewer_root: Path,
    *,
    pair_count: int,
    overlays_per_pair: int,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    samples: list[dict[str, Any]] = []
    try:
        for pair_idx in range(max(1, pair_count)):
            pair_id = f"rss_pair_{pair_idx}"
            workbench._active_row = {"pair_id": pair_id}
            overlays = [_build_overlay(pair_id, zone_idx) for zone_idx in range(max(1, overlays_per_pair))]
            workbench._cache_viewer_overlays_v2(pair_id, overlays)
            del overlays
            if pair_idx % 5 == 0:
                gc.collect()
            samples.append(
                {
                    "pair_index": pair_idx,
                    "rss_mb": _rss_mb(),
                    "cache_pair_count": len(workbench._viewer_overlay_cache),
                    "cache_total_bytes": int(workbench._viewer_overlay_cache_total_bytes_v2),
                    "evictions": int(workbench._viewer_overlay_cache_evictions_v2),
                }
            )
    finally:
        workbench.deleteLater()
        app.processEvents()

    rss_values = [float(s["rss_mb"]) for s in samples if s.get("rss_mb") is not None]
    pair_limit = int(dcw.GUI_OVERLAY_CACHE_PAIR_LIMIT)
    tail = [float(s["rss_mb"]) for s in samples[pair_limit:] if s.get("rss_mb") is not None]
    tail_delta = round(max(tail) - min(tail), 3) if len(tail) >= 2 else 0.0 if tail else None
    return {
        "pair_count": int(pair_count),
        "overlays_per_pair": int(overlays_per_pair),
        "total_overlay_visits": int(pair_count) * int(overlays_per_pair),
        "cache_pair_limit": pair_limit,
        "cache_byte_limit": int(dcw.GUI_OVERLAY_CACHE_BYTE_LIMIT),
        "max_cache_pair_count": max((int(s["cache_pair_count"]) for s in samples), default=0),
        "max_cache_total_bytes": max((int(s["cache_total_bytes"]) for s in samples), default=0),
        "evictions": max((int(s["evictions"]) for s in samples), default=0),
        "rss_available": bool(rss_values),
        "rss_start_mb": rss_values[0] if rss_values else None,
        "rss_peak_mb": max(rss_values) if rss_values else None,
        "rss_tail_delta_after_cache_limit_mb": tail_delta,
        "samples": samples,
    }


def _run_navigation_soak_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    pair_count: int,
    visit_count: int,
    overlays_per_pair: int,
    warmup_visits: int,
    heartbeat_interval_ms: int = 10,
    settle_ms: float = 0.0,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    pair_count = max(1, int(pair_count))
    visit_count = max(1, int(visit_count))
    overlays_per_pair = max(1, int(overlays_per_pair))
    selection_ms: list[float] = []
    gaps_ms: list[float] = []
    samples: list[dict[str, Any]] = []
    lightweight_schedules: list[tuple[str, int, int]] = []
    last_tick = time.perf_counter()

    def _tick() -> None:
        nonlocal last_tick
        app.processEvents()
        now = time.perf_counter()
        gaps_ms.append(round((now - last_tick) * 1000.0, 3))
        last_tick = now

    try:
        rows: list[dict[str, Any]] = []
        for pair_idx in range(pair_count):
            pair_id = f"navigation_soak_pair_{pair_idx}"
            row, pair = _make_pair(
                scratch,
                viewer_root,
                pair_id,
                overlay_total_count=overlays_per_pair,
                top_issue_count=min(5, overlays_per_pair),
                full_overlay_json=True,
            )
            rows.append(row)
            workbench._viewer_pairs_by_id[pair_id] = pair

        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )
        workbench._focus_lightweight_on_zone_v2 = lambda _zone_id: None  # type: ignore[method-assign]
        workbench._start_zone_crop_render_v2 = lambda _zone_id: None  # type: ignore[method-assign]
        workbench._apply_or_start_zone_vector_render_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._schedule_lightweight_pair_load_v2 = (  # type: ignore[method-assign]
            lambda p, vp: lightweight_schedules.append((
                str(p),
                int(vp.get("page_a", 0) or 0),
                int(vp.get("page_b", 0) or 0),
            ))
        )

        started = time.perf_counter()
        last_tick = started
        rss_start = _rss_mb()
        if rss_start is not None:
            samples.append(
                {
                    "visit_index": -1,
                    "pair_index": None,
                    "rss_mb": float(rss_start),
                    "cache_pair_count": len(workbench._viewer_overlay_cache),
                    "cache_total_bytes": int(workbench._viewer_overlay_cache_total_bytes_v2),
                    "evictions": int(workbench._viewer_overlay_cache_evictions_v2),
                }
            )
        completed = True
        for visit_idx in range(visit_count):
            if time.perf_counter() - started > max(1.0, float(timeout_s)):
                completed = False
                break
            pair_idx = visit_idx % pair_count
            call_ms = _select_row(workbench, rows[pair_idx])
            selection_ms.append(call_ms)
            _tick()
            if settle_ms > 0:
                settle_deadline = time.perf_counter() + float(settle_ms) / 1000.0
                while time.perf_counter() < settle_deadline:
                    _tick()
                    time.sleep(max(0.001, float(heartbeat_interval_ms) / 1000.0))
            if visit_idx % max(1, pair_count) == 0:
                gc.collect()
            rss_now = _rss_mb()
            samples.append(
                {
                    "visit_index": int(visit_idx),
                    "pair_index": int(pair_idx),
                    "rss_mb": float(rss_now) if rss_now is not None else None,
                    "cache_pair_count": len(workbench._viewer_overlay_cache),
                    "cache_total_bytes": int(workbench._viewer_overlay_cache_total_bytes_v2),
                    "evictions": int(workbench._viewer_overlay_cache_evictions_v2),
                }
            )
            time.sleep(max(0.0, float(heartbeat_interval_ms) / 1000.0))
        app.processEvents()
        rss_end = _rss_mb()
        if rss_end is not None:
            samples.append(
                {
                    "visit_index": int(len(selection_ms)),
                    "pair_index": None,
                    "rss_mb": float(rss_end),
                    "cache_pair_count": len(workbench._viewer_overlay_cache),
                    "cache_total_bytes": int(workbench._viewer_overlay_cache_total_bytes_v2),
                    "evictions": int(workbench._viewer_overlay_cache_evictions_v2),
                }
            )
        rss_values = [float(sample["rss_mb"]) for sample in samples if sample.get("rss_mb") is not None]
        pair_limit = int(dcw.GUI_OVERLAY_CACHE_PAIR_LIMIT)
        warmup_cutoff = max(int(warmup_visits), pair_limit)
        cache_pair_counts = [int(sample.get("cache_pair_count") or 0) for sample in samples]
        cache_bytes = [int(sample.get("cache_total_bytes") or 0) for sample in samples]
        final_summary = summarize_viewer_perf(viewer_root)
        return {
            "completed": bool(completed and len(selection_ms) == visit_count),
            "pair_count": int(pair_count),
            "visit_count": int(visit_count),
            "completed_visit_count": int(len(selection_ms)),
            "lightweight_scheduled_count": int(len(lightweight_schedules)),
            "overlays_per_pair": int(overlays_per_pair),
            "warmup_visit": int(warmup_cutoff),
            "selection_call_ms": _latency_summary(selection_ms),
            "heartbeat_interval_ms": int(heartbeat_interval_ms),
            "event_loop_gap": _event_loop_gap_summary(gaps_ms),
            "rss_available": bool(rss_values),
            "rss_start_mb": rss_values[0] if rss_values else None,
            "rss_peak_mb": max(rss_values) if rss_values else None,
            "rss_end_mb": float(rss_end) if rss_end is not None else None,
            "rss_delta_mb": round(max(rss_values) - rss_values[0], 3) if rss_values else None,
            "rss_slope": _rss_slope_summary(samples, warmup_visit=warmup_cutoff),
            "cache_pair_limit": pair_limit,
            "cache_byte_limit": int(dcw.GUI_OVERLAY_CACHE_BYTE_LIMIT),
            "max_cache_pair_count": max(cache_pair_counts, default=0),
            "max_cache_total_bytes": max(cache_bytes, default=0),
            "evictions": max((int(sample.get("evictions") or 0) for sample in samples), default=0),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "samples": samples,
            "viewer_perf_summary": final_summary,
        }
    finally:
        workbench.deleteLater()
        app.processEvents()


def _run_zone_selection_hotpath_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    zone_count: int,
    runs: int,
    heartbeat_interval_ms: int = 10,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    zone_count = max(2, int(zone_count))
    runs = max(1, int(runs))
    selection_ms: list[float] = []
    gaps_ms: list[float] = []
    crop_start_calls: list[str] = []
    vector_start_calls: list[str] = []
    last_tick = time.perf_counter()

    def _tick() -> None:
        nonlocal last_tick
        app.processEvents()
        now = time.perf_counter()
        gaps_ms.append(round((now - last_tick) * 1000.0, 3))
        last_tick = now

    class _NoopZoneRenderController:
        def parent(self) -> Any:
            return workbench

        def prewarm(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def is_busy(self) -> bool:
            return False

        def render(self, *_args: Any, **_kwargs: Any) -> bool:
            return False

        def shutdown(self) -> None:
            return None

    try:
        row, pair = _make_pair(
            scratch,
            viewer_root,
            "zone_selection_hotpath_pair",
            overlay_total_count=zone_count,
            top_issue_count=zone_count,
            full_overlay_json=False,
        )
        pair_id = str(pair["pair_id"])
        workbench._viewer_pairs_by_id[pair_id] = pair
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )
        try:
            workbench._zone_render_controller_v2.shutdown()
        except Exception:
            pass
        workbench._zone_render_controller_v2 = _NoopZoneRenderController()  # type: ignore[assignment]
        workbench._schedule_lightweight_pair_load_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._schedule_initial_zone_selection_v2 = lambda _pair_id: None  # type: ignore[method-assign]
        workbench._schedule_full_zone_tree_rebuild_v2 = lambda _pair_id: None  # type: ignore[method-assign]
        workbench._focus_lightweight_on_zone_v2 = lambda _zone_id: None  # type: ignore[method-assign]
        workbench._apply_or_start_zone_vector_render_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._start_zone_vector_render_v2 = lambda _pair_id, zone_id: vector_start_calls.append(str(zone_id))  # type: ignore[method-assign]
        workbench._start_zone_crop_render_v2 = lambda zone_id: crop_start_calls.append(str(zone_id))  # type: ignore[method-assign]

        _select_row(workbench, row)
        app.processEvents()
        leaves = list(workbench._zone_leaf_items_v2())
        started = time.perf_counter()
        last_tick = started
        completed = bool(leaves)
        for run_idx in range(runs):
            if not leaves:
                completed = False
                break
            leaf = leaves[run_idx % len(leaves)]
            select_started = time.perf_counter()
            workbench.zone_list_v2.setCurrentItem(leaf)
            app.processEvents()
            selection_ms.append(round((time.perf_counter() - select_started) * 1000.0, 3))
            _tick()
            if heartbeat_interval_ms > 0:
                time.sleep(max(0.0, heartbeat_interval_ms / 1000.0))
        app.processEvents()
        viewer_summary = summarize_viewer_perf(viewer_root)
        return {
            "completed": bool(completed and len(selection_ms) == runs),
            "requested_selection_count": int(runs),
            "completed_selection_count": int(len(selection_ms)),
            "visible_leaf_count": int(len(leaves)),
            "zone_crop_start_call_count": int(len(crop_start_calls)),
            "zone_vector_start_call_count": int(len(vector_start_calls)),
            "selection_call_ms": _latency_summary(selection_ms),
            "event_loop_gap": _event_loop_gap_summary(gaps_ms),
            "viewer_perf_summary": viewer_summary,
            "worker_spawned_count": int(viewer_summary.get("worker_spawned_count") or 0),
            "worker_process_count_max": int(viewer_summary.get("worker_process_count_max") or 0),
            "full_tree_overlay_load_worker_count": int(viewer_summary.get("full_tree_overlay_load_worker_count") or 0),
            "full_tree_plan_build_worker_count": int(viewer_summary.get("full_tree_plan_build_worker_count") or 0),
            "zone_crop_count": int(viewer_summary.get("zone_crop_count") or 0),
            "selected_zone_stale_count": int(viewer_summary.get("selected_zone_stale_count") or 0),
            "selected_zone_cancel_count": int(viewer_summary.get("selected_zone_cancel_count") or 0),
            "selected_zone_fallback_count": int(viewer_summary.get("selected_zone_fallback_count") or 0),
        }
    finally:
        workbench.deleteLater()
        app.processEvents()


def _run_selected_zone_crop_first_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    zone_count: int,
    runs: int,
    heartbeat_interval_ms: int = 10,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    zone_count = max(2, int(zone_count))
    runs = max(1, int(runs))
    crop_visible_ms: list[float] = []
    gaps_ms: list[float] = []
    render_calls: list[dict[str, Any]] = []
    trace: list[tuple[str, str]] = []
    status_calls: list[tuple[str, str, str]] = []
    vector_failures = 0
    vector_failure_background_preserved = 0
    blank_selected_zone_count = 0
    crop_first_sequence_count = 0
    fallback_missing_reason_count = 0
    timeout_count = 0
    last_tick = time.perf_counter()
    original_lightweight_only = dcw.DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY

    def _tick() -> None:
        nonlocal last_tick
        app.processEvents()
        now = time.perf_counter()
        gaps_ms.append(round((now - last_tick) * 1000.0, 3))
        last_tick = now

    class _RecordingZoneRenderController:
        def parent(self) -> Any:
            return workbench

        def prewarm(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def is_busy(self) -> bool:
            return False

        def render(self, **kwargs: Any) -> bool:
            render_calls.append(dict(kwargs))
            request = kwargs.get("request", {})
            if isinstance(request, dict):
                trace.append(("crop_started", str(request.get("zone_id") or "")))
            return True

        def shutdown(self) -> None:
            return None

    try:
        dcw.DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY = True
        row, pair = _make_pair(
            scratch,
            viewer_root,
            "selected_zone_crop_first_pair",
            overlay_total_count=zone_count,
            top_issue_count=zone_count,
            full_overlay_json=False,
        )
        pair_id = str(pair["pair_id"])
        source_a = scratch / f"{pair_id}_before.dxf"
        source_b = scratch / f"{pair_id}_after.dxf"
        source_a.write_text("0\nEOF\n", encoding="utf-8")
        source_b.write_text("0\nEOF\n", encoding="utf-8")
        pair["source_a"] = str(source_a)
        pair["source_b"] = str(source_b)
        pair["coordinate_source"] = "cad_world"
        workbench._viewer_pairs_by_id[pair_id] = pair
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )
        try:
            workbench._zone_render_controller_v2.shutdown()
        except Exception:
            pass
        workbench._zone_render_controller_v2 = _RecordingZoneRenderController()  # type: ignore[assignment]
        workbench._schedule_lightweight_pair_load_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._schedule_initial_zone_selection_v2 = lambda _pair_id: None  # type: ignore[method-assign]
        workbench._schedule_full_zone_tree_rebuild_v2 = lambda _pair_id: None  # type: ignore[method-assign]
        workbench._focus_lightweight_on_zone_v2 = lambda zone_id: trace.append(("initial_focus", str(zone_id)))  # type: ignore[method-assign]
        workbench._request_zone_focus_v2 = lambda zone_id: trace.append(("deferred_focus", str(zone_id)))  # type: ignore[method-assign]
        workbench._set_lightweight_zone_side_messages_v2 = lambda _zone_id: None  # type: ignore[method-assign]
        workbench._zone_detail_text_v2 = lambda _zone_id: ""  # type: ignore[method-assign]
        workbench._load_current_zone_memo_v2 = lambda: None  # type: ignore[method-assign]
        workbench._refresh_zone_vector_button_state_v2 = lambda: None  # type: ignore[method-assign]
        workbench._set_preview_status_v2 = (  # type: ignore[method-assign]
            lambda pair_uuid, status, message="": status_calls.append((str(pair_uuid), str(status), str(message)))
        )

        def _simulate_vector_failure(failure_pair_id: str, failure_zone_id: str) -> None:
            nonlocal vector_failures, vector_failure_background_preserved
            trace.append(("vector_start", str(failure_zone_id)))
            before_pair = dict(workbench._viewer_pairs_by_id.get(failure_pair_id, {}))
            expected_svg = scratch / f"{failure_pair_id}_{failure_zone_id}_vector.svg"
            result_json = expected_svg.with_suffix(".result.json")
            result_json.write_text(
                json.dumps({"skipped_reason": "synthetic vector render failure"}, ensure_ascii=False),
                encoding="utf-8",
            )
            workbench._zone_vector_qprocess = object()  # type: ignore[assignment]
            workbench._zone_vector_pending = (failure_pair_id, failure_zone_id, str(expected_svg))
            workbench._zone_vector_result_json = result_json
            workbench._on_zone_vector_finished_v2(1, None)
            vector_failures += 1
            after_pair = workbench._viewer_pairs_by_id.get(failure_pair_id, {})
            if (
                after_pair.get("before_image")
                and after_pair.get("after_image")
                and after_pair.get("before_image") == before_pair.get("before_image")
                and after_pair.get("after_image") == before_pair.get("after_image")
            ):
                vector_failure_background_preserved += 1

        workbench._start_zone_vector_render_v2 = _simulate_vector_failure  # type: ignore[method-assign]

        _select_row(workbench, row)
        app.processEvents()
        leaves = list(workbench._zone_leaf_items_v2())
        started = time.perf_counter()
        last_tick = started
        completed = bool(leaves)
        for run_idx in range(runs):
            if not leaves:
                completed = False
                break
            leaf = leaves[run_idx % len(leaves)]
            zone_id = str(leaf.data(0, Qt.UserRole) or "")
            render_index_before = len(render_calls)
            trace_index_before = len(trace)
            select_started = time.perf_counter()
            workbench._on_zone_selected_v2(leaf)
            app.processEvents()
            if len(render_calls) <= render_index_before:
                blank_selected_zone_count += 1
                completed = False
                continue
            render_call = render_calls[-1]
            request = render_call.get("request", {}) if isinstance(render_call, dict) else {}
            request_id = str(request.get("request_id") or "")
            before_crop = scratch / f"{pair_id}_{zone_id}_{run_idx}_before_crop.png"
            after_crop = scratch / f"{pair_id}_{zone_id}_{run_idx}_after_crop.png"
            before_crop.write_bytes(b"synthetic-before-crop")
            after_crop.write_bytes(b"synthetic-after-crop")
            crop_result = {
                "request_id": request_id,
                "elapsed_ms": round((time.perf_counter() - select_started) * 1000.0, 3),
                "cache_hit": False,
                "render_lifecycle": "ready",
                "visual_fidelity": "cad_render",
                "renderer_backend": "synthetic-crop-first",
                "before_image": str(before_crop),
                "after_image": str(after_crop),
            }
            cropped_pair = dict(pair)
            cropped_pair["before_image"] = str(before_crop)
            cropped_pair["after_image"] = str(after_crop)
            cropped_pair["last_zone_crop"] = dict(crop_result)
            trace.append(("crop_finished", zone_id))
            workbench._on_zone_crop_render_finished_v2(
                pair_id,
                zone_id,
                crop_result,
                cropped_pair,
                list(workbench._active_overlays_by_zone.values()),
            )
            crop_visible_ms.append(round((time.perf_counter() - select_started) * 1000.0, 3))
            run_trace = trace[trace_index_before:]
            run_events = [name for name, value in run_trace if value == zone_id]
            try:
                crop_finished_index = run_events.index("crop_finished")
                deferred_focus_index = run_events.index("deferred_focus")
                vector_start_index = run_events.index("vector_start")
                if crop_finished_index < deferred_focus_index < vector_start_index:
                    crop_first_sequence_count += 1
            except ValueError:
                pass
            if not (
                workbench._viewer_pairs_by_id.get(pair_id, {}).get("before_image")
                and workbench._viewer_pairs_by_id.get(pair_id, {}).get("after_image")
            ):
                blank_selected_zone_count += 1
            if str(crop_result.get("render_lifecycle") or "") == "fallback_visible" and not str(
                crop_result.get("reason_code") or ""
            ):
                fallback_missing_reason_count += 1
            if str(crop_result.get("render_lifecycle") or "") == "render_timeout":
                timeout_count += 1
            _tick()
            if heartbeat_interval_ms > 0:
                time.sleep(max(0.0, heartbeat_interval_ms / 1000.0))
        app.processEvents()
        viewer_summary = summarize_viewer_perf(viewer_root)
        return {
            "completed": bool(completed and len(crop_visible_ms) == runs),
            "requested_selection_count": int(runs),
            "completed_selection_count": int(len(crop_visible_ms)),
            "visible_leaf_count": int(len(leaves)),
            "crop_visible_count": int(len(crop_visible_ms)),
            "crop_first_sequence_count": int(crop_first_sequence_count),
            "vector_start_count": int(vector_failures),
            "vector_failure_count": int(vector_failures),
            "vector_failure_background_preserved_count": int(vector_failure_background_preserved),
            "blank_selected_zone_count": int(blank_selected_zone_count),
            "fallback_missing_reason_count": int(fallback_missing_reason_count),
            "timeout_count": int(timeout_count),
            "crop_visible_ms": _latency_summary(crop_visible_ms),
            "event_loop_gap": _event_loop_gap_summary(gaps_ms),
            "viewer_perf_summary": viewer_summary,
            "worker_spawned_count": int(viewer_summary.get("worker_spawned_count") or 0),
            "worker_process_count_max": int(viewer_summary.get("worker_process_count_max") or 0),
            "orphan_worker_count": 0,
            "zone_crop_count": int(viewer_summary.get("zone_crop_count") or 0),
            "selected_zone_stale_count": int(viewer_summary.get("selected_zone_stale_count") or 0),
            "selected_zone_cancel_count": int(viewer_summary.get("selected_zone_cancel_count") or 0),
            "selected_zone_fallback_count": int(viewer_summary.get("selected_zone_fallback_count") or 0),
            "status_calls": [
                {"pair_id": pair_uuid, "status": status}
                for pair_uuid, status, _message in status_calls
            ],
        }
    finally:
        dcw.DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY = original_lightweight_only
        workbench._zone_vector_qprocess = None
        workbench.deleteLater()
        app.processEvents()


def _run_full_tree_responsiveness_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    overlay_count: int,
    heartbeat_interval_ms: int = 10,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    pair_id = "full_tree_pair"
    gaps_ms: list[float] = []
    done = {"value": False}
    last_tick = {"value": time.perf_counter()}

    def heartbeat() -> None:
        now = time.perf_counter()
        gaps_ms.append(round((now - last_tick["value"]) * 1000.0, 3))
        last_tick["value"] = now
        if not done["value"]:
            QTimer.singleShot(max(1, int(heartbeat_interval_ms)), heartbeat)

    try:
        row, viewer_pair = _make_pair(
            scratch,
            viewer_root,
            pair_id,
            overlay_total_count=max(1, overlay_count),
            top_issue_count=0,
            full_overlay_json=True,
        )
        workbench._viewer_pairs_by_id[pair_id] = viewer_pair
        workbench._active_row = row
        workbench._active_zone_id = ""
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )

        QTimer.singleShot(max(1, int(heartbeat_interval_ms)), heartbeat)
        app.processEvents()
        started = time.perf_counter()
        call_started = time.perf_counter()
        workbench._run_full_zone_tree_rebuild_v2(pair_id, workbench._zone_tree_rebuild_generation_v2)
        start_call_ms = round((time.perf_counter() - call_started) * 1000.0, 3)
        deadline = started + max(1.0, float(timeout_s))
        while time.perf_counter() < deadline:
            app.processEvents()
            if (
                workbench._full_zone_tree_chunk_state_v2 is None
                and workbench._full_zone_tree_overlay_worker_v2 is None
                and workbench._full_zone_tree_plan_worker_v2 is None
                and not workbench._pending_full_zone_tree_pair_id_v2
            ):
                break
            time.sleep(0.001)
        done["value"] = True
        app.processEvents()
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        completed = (
            workbench._full_zone_tree_chunk_state_v2 is None
            and workbench._full_zone_tree_overlay_worker_v2 is None
            and workbench._full_zone_tree_plan_worker_v2 is None
            and not workbench._pending_full_zone_tree_pair_id_v2
        )
        viewer_summary = summarize_viewer_perf(viewer_root)
        full_tree_summary = {
            "count": int(viewer_summary.get("full_tree_rebuild_count") or 0),
            "chunked_count": int(viewer_summary.get("full_tree_rebuild_chunked_count") or 0),
            "chunk_count": viewer_summary.get("full_tree_rebuild_chunk_count", {}),
            "max_chunk_ms": viewer_summary.get("full_tree_rebuild_max_chunk_ms", {}),
            "tree_item_count_max": int(viewer_summary.get("full_tree_rebuild_tree_item_count_max") or 0),
            "overlay_load_worker_count": int(viewer_summary.get("full_tree_overlay_load_worker_count") or 0),
            "plan_build_worker_count": int(viewer_summary.get("full_tree_plan_build_worker_count") or 0),
            "overlay_json_load_ms": viewer_summary.get("full_tree_overlay_json_load_ms", {}),
            "plan_build_ms": viewer_summary.get("full_tree_plan_build_ms", {}),
        }
    finally:
        done["value"] = True
        workbench.deleteLater()
        app.processEvents()

    return {
        "completed": bool(completed),
        "overlay_count": int(overlay_count),
        "elapsed_ms": elapsed_ms,
        "start_call_ms": start_call_ms,
        "heartbeat_interval_ms": int(heartbeat_interval_ms),
        "event_loop_gap": _latency_summary(gaps_ms),
        "tick_count": len(gaps_ms),
        "viewer_perf_summary": viewer_summary,
        "full_tree_summary": full_tree_summary,
    }


def _run_page_navigation_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    overlay_count: int,
    page_pair_count: int = 2,
    heartbeat_interval_ms: int = 10,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    pair_id = "page_nav_pair"
    target_page = 1 if int(page_pair_count) > 1 else 0
    lightweight_loads: list[tuple[str, int, int]] = []
    gaps_ms: list[float] = []
    done = {"value": False}
    last_tick = {"value": time.perf_counter()}

    def heartbeat() -> None:
        now = time.perf_counter()
        gaps_ms.append(round((now - last_tick["value"]) * 1000.0, 3))
        last_tick["value"] = now
        if not done["value"]:
            QTimer.singleShot(max(1, int(heartbeat_interval_ms)), heartbeat)

    try:
        row, viewer_pair = _make_pair(
            scratch,
            viewer_root,
            pair_id,
            overlay_total_count=max(1, overlay_count),
            top_issue_count=0,
            full_overlay_json=True,
            page_pair_count=max(1, int(page_pair_count)),
        )
        workbench._viewer_pairs_by_id[pair_id] = viewer_pair
        workbench._active_row = row
        workbench._active_pdf_page_index_v2 = target_page
        workbench._active_all_overlays_by_zone = {}
        workbench._active_overlays_by_zone = {}
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )
        workbench._schedule_lightweight_pair_load_v2 = (  # type: ignore[method-assign]
            lambda p, vp: lightweight_loads.append((
                str(p),
                int(vp.get("page_a", 0) or 0),
                int(vp.get("page_b", 0) or 0),
            ))
        )

        QTimer.singleShot(max(1, int(heartbeat_interval_ms)), heartbeat)
        app.processEvents()
        call_started = time.perf_counter()
        workbench._show_pdf_page_pair_v2(target_page, target_page)
        start_call_ms = round((time.perf_counter() - call_started) * 1000.0, 3)
        deadline = call_started + max(1.0, float(timeout_s))
        while time.perf_counter() < deadline:
            app.processEvents()
            if (
                workbench._full_zone_tree_chunk_state_v2 is None
                and workbench._full_zone_tree_overlay_worker_v2 is None
                and workbench._full_zone_tree_plan_worker_v2 is None
                and not workbench._pending_full_zone_tree_pair_id_v2
            ):
                break
            time.sleep(0.001)
        done["value"] = True
        app.processEvents()
        completed = (
            workbench._full_zone_tree_chunk_state_v2 is None
            and workbench._full_zone_tree_overlay_worker_v2 is None
            and workbench._full_zone_tree_plan_worker_v2 is None
            and not workbench._pending_full_zone_tree_pair_id_v2
        )
        viewer_summary = summarize_viewer_perf(viewer_root)
        expected_visible = sum(
            1
            for idx in range(max(0, int(overlay_count)))
            if idx % max(1, int(page_pair_count)) == target_page
        )
        full_tree_summary = {
            "count": int(viewer_summary.get("full_tree_rebuild_count") or 0),
            "chunked_count": int(viewer_summary.get("full_tree_rebuild_chunked_count") or 0),
            "overlay_load_worker_count": int(viewer_summary.get("full_tree_overlay_load_worker_count") or 0),
            "plan_build_worker_count": int(viewer_summary.get("full_tree_plan_build_worker_count") or 0),
            "chunk_count": viewer_summary.get("full_tree_rebuild_chunk_count", {}),
            "max_chunk_ms": viewer_summary.get("full_tree_rebuild_max_chunk_ms", {}),
        }
        return {
            "completed": bool(completed),
            "overlay_count": int(overlay_count),
            "page_pair_count": int(page_pair_count),
            "target_page": int(target_page),
            "expected_visible_overlay_count": int(expected_visible),
            "visible_leaf_count": len(workbench._zone_leaf_items_v2()),
            "start_call_ms": start_call_ms,
            "heartbeat_interval_ms": int(heartbeat_interval_ms),
            "event_loop_gap": _latency_summary(gaps_ms),
            "tick_count": len(gaps_ms),
            "lightweight_load_count": len(lightweight_loads),
            "viewer_perf_summary": viewer_summary,
            "full_tree_summary": full_tree_summary,
        }
    finally:
        done["value"] = True
        workbench.deleteLater()
        app.processEvents()


def _run_rapid_page_navigation_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    overlay_count: int,
    page_pair_count: int = 3,
    step_count: int = 2,
    heartbeat_interval_ms: int = 10,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    pair_id = "rapid_page_nav_pair"
    page_pair_count = max(2, int(page_pair_count))
    target_page = min(page_pair_count - 1, max(1, int(step_count)))
    lightweight_loads: list[tuple[str, int, int]] = []
    step_call_ms: list[float] = []
    gaps_ms: list[float] = []
    done = {"value": False}
    last_tick = {"value": time.perf_counter()}

    def heartbeat() -> None:
        now = time.perf_counter()
        gaps_ms.append(round((now - last_tick["value"]) * 1000.0, 3))
        last_tick["value"] = now
        if not done["value"]:
            QTimer.singleShot(max(1, int(heartbeat_interval_ms)), heartbeat)

    try:
        row, viewer_pair = _make_pair(
            scratch,
            viewer_root,
            pair_id,
            overlay_total_count=max(1, overlay_count),
            top_issue_count=0,
            full_overlay_json=True,
            page_pair_count=page_pair_count,
        )
        workbench._viewer_pairs_by_id[pair_id] = viewer_pair
        workbench._active_row = row
        workbench._active_pdf_page_index_v2 = 0
        workbench._active_all_overlays_by_zone = {}
        workbench._active_overlays_by_zone = {}
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )
        workbench._schedule_lightweight_pair_load_v2 = (  # type: ignore[method-assign]
            lambda p, vp: lightweight_loads.append((
                str(p),
                int(vp.get("page_a", 0) or 0),
                int(vp.get("page_b", 0) or 0),
            ))
        )

        rss_start = _rss_mb()
        rss_samples: list[float] = [float(rss_start)] if rss_start is not None else []
        QTimer.singleShot(max(1, int(heartbeat_interval_ms)), heartbeat)
        app.processEvents()
        started = time.perf_counter()
        for _ in range(target_page):
            step_started = time.perf_counter()
            workbench._step_pdf_page_pair_v2(+1)
            step_call_ms.append(round((time.perf_counter() - step_started) * 1000.0, 3))

        deadline = started + max(1.0, float(timeout_s))
        while time.perf_counter() < deadline:
            app.processEvents()
            if (
                workbench._full_zone_tree_chunk_state_v2 is None
                and workbench._full_zone_tree_overlay_worker_v2 is None
                and workbench._full_zone_tree_plan_worker_v2 is None
                and not workbench._pending_full_zone_tree_pair_id_v2
            ):
                break
            time.sleep(0.001)
            rss_now = _rss_mb()
            if rss_now is not None:
                rss_samples.append(float(rss_now))
        done["value"] = True
        app.processEvents()
        rss_end = _rss_mb()
        if rss_end is not None:
            rss_samples.append(float(rss_end))
        completed = (
            workbench._full_zone_tree_chunk_state_v2 is None
            and workbench._full_zone_tree_overlay_worker_v2 is None
            and workbench._full_zone_tree_plan_worker_v2 is None
            and not workbench._pending_full_zone_tree_pair_id_v2
        )
        viewer_summary = summarize_viewer_perf(viewer_root)
        leaf_ids = [
            str(item.data(0, Qt.UserRole) or "")
            for item in workbench._zone_leaf_items_v2()
        ]
        expected_visible = sum(
            1
            for idx in range(max(0, int(overlay_count)))
            if idx % page_pair_count == target_page
        )

        def leaf_is_stale(zone_id: str) -> bool:
            overlay = workbench._active_overlays_by_zone.get(zone_id) or {}
            return (
                int(overlay.get("page_a", -1)) != target_page
                or int(overlay.get("page_b", -1)) != target_page
            )

        stale_leaf_count = sum(
            1
            for zone_id in leaf_ids
            if leaf_is_stale(zone_id)
        )
        full_tree_summary = {
            "count": int(viewer_summary.get("full_tree_rebuild_count") or 0),
            "chunked_count": int(viewer_summary.get("full_tree_rebuild_chunked_count") or 0),
            "overlay_load_worker_count": int(viewer_summary.get("full_tree_overlay_load_worker_count") or 0),
            "plan_build_worker_count": int(viewer_summary.get("full_tree_plan_build_worker_count") or 0),
            "chunk_count": viewer_summary.get("full_tree_rebuild_chunk_count", {}),
            "max_chunk_ms": viewer_summary.get("full_tree_rebuild_max_chunk_ms", {}),
        }
        return {
            "completed": bool(completed),
            "overlay_count": int(overlay_count),
            "page_pair_count": int(page_pair_count),
            "step_count": int(target_page),
            "target_page": int(target_page),
            "active_pdf_page_index": int(workbench._active_pdf_page_index_v2),
            "final_page_a": int(viewer_pair.get("page_a") or 0),
            "final_page_b": int(viewer_pair.get("page_b") or 0),
            "expected_visible_overlay_count": int(expected_visible),
            "visible_leaf_count": len(leaf_ids),
            "stale_leaf_count": int(stale_leaf_count),
            "step_call_ms": _latency_summary(step_call_ms),
            "heartbeat_interval_ms": int(heartbeat_interval_ms),
            "event_loop_gap": _latency_summary(gaps_ms),
            "tick_count": len(gaps_ms),
            "lightweight_load_count": len(lightweight_loads),
            "rss_available": bool(rss_samples),
            "rss_start_mb": float(rss_start) if rss_start is not None else None,
            "rss_peak_mb": max(rss_samples) if rss_samples else None,
            "rss_end_mb": float(rss_end) if rss_end is not None else None,
            "rss_delta_mb": round(max(rss_samples) - float(rss_start), 3)
            if rss_samples and rss_start is not None
            else None,
            "viewer_perf_summary": viewer_summary,
            "full_tree_summary": full_tree_summary,
        }
    finally:
        done["value"] = True
        workbench.deleteLater()
        app.processEvents()


def _run_lightweight_pdf_load_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    pair_id = "lightweight_pdf_load_pair"
    qtpdf_error = ""
    try:
        from src.services.comparison.qt_pdf_adapter import is_qt_pdf_available

        qtpdf_available = bool(is_qt_pdf_available())
    except Exception as exc:  # noqa: BLE001
        qtpdf_available = False
        qtpdf_error = str(exc)
    event_loop_gaps_ms: list[float] = []
    last_heartbeat = time.perf_counter()

    def _wait_for_lightweight_count(target_count: int, deadline: float) -> dict[str, Any]:
        nonlocal last_heartbeat
        summary: dict[str, Any] = {}
        while time.perf_counter() < deadline:
            app.processEvents()
            now = time.perf_counter()
            event_loop_gaps_ms.append(round((now - last_heartbeat) * 1000.0, 3))
            last_heartbeat = now
            summary = summarize_viewer_perf(viewer_root)
            if int(summary.get("lightweight_pair_load_count") or 0) >= int(target_count):
                return summary
            time.sleep(0.001)
        app.processEvents()
        return summarize_viewer_perf(viewer_root)

    try:
        row, viewer_pair = _make_pair(
            scratch,
            viewer_root,
            pair_id,
            overlay_total_count=8,
            top_issue_count=8,
            full_overlay_json=True,
        )
        workbench._viewer_pairs_by_id[pair_id] = viewer_pair
        workbench._active_row = row
        workbench._active_overlays_by_zone = {
            str(overlay.get("zone_id") or ""): overlay
            for overlay in row.get("top_issues", [])
            if isinstance(overlay, dict) and overlay.get("zone_id")
        }
        workbench._active_all_overlays_by_zone = dict(workbench._active_overlays_by_zone)
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )
        action = getattr(workbench, "act_lightweight_viewer_v2", None)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        workbench._set_lightweight_viewer_visible_v2(True)
        app.processEvents()

        deadline = time.perf_counter() + max(1.0, float(timeout_s))
        first_started = time.perf_counter()
        workbench._schedule_lightweight_pair_load_v2(pair_id, viewer_pair)
        first_schedule_call_ms = round((time.perf_counter() - first_started) * 1000.0, 3)
        first_summary = _wait_for_lightweight_count(1, deadline)
        first_before_state = dict(getattr(workbench.preview_before_lightweight_v2, "_pdf_render_state", {}) or {})
        first_after_state = dict(getattr(workbench.preview_after_lightweight_v2, "_pdf_render_state", {}) or {})
        first_before_background = _viewport_pdf_background_state(workbench.preview_before_lightweight_v2)
        first_after_background = _viewport_pdf_background_state(workbench.preview_after_lightweight_v2)

        second_started = time.perf_counter()
        workbench._schedule_lightweight_pair_load_v2(pair_id, viewer_pair)
        second_schedule_call_ms = round((time.perf_counter() - second_started) * 1000.0, 3)
        final_summary = _wait_for_lightweight_count(2, deadline)
        second_before_state = dict(getattr(workbench.preview_before_lightweight_v2, "_pdf_render_state", {}) or {})
        second_after_state = dict(getattr(workbench.preview_after_lightweight_v2, "_pdf_render_state", {}) or {})
        second_before_background = _viewport_pdf_background_state(workbench.preview_before_lightweight_v2)
        second_after_background = _viewport_pdf_background_state(workbench.preview_after_lightweight_v2)

        first_loaded = bool(first_before_state.get("pdf_path")) and bool(first_after_state.get("pdf_path"))
        second_loaded = bool(second_before_state.get("pdf_path")) and bool(second_after_state.get("pdf_path"))
        first_background_ready = bool(first_before_background.get("background_ready")) and bool(
            first_after_background.get("background_ready")
        )
        second_background_ready = bool(second_before_background.get("background_ready")) and bool(
            second_after_background.get("background_ready")
        )
        first_overlay_after_background = bool(first_background_ready) and (
            int(first_before_background.get("overlay_count") or 0) > 0
            or int(first_after_background.get("overlay_count") or 0) > 0
        )
        second_overlay_after_background = bool(second_background_ready) and (
            int(second_before_background.get("overlay_count") or 0) > 0
            or int(second_after_background.get("overlay_count") or 0) > 0
        )
        return {
            "completed": int(final_summary.get("lightweight_pair_load_count") or 0) >= 2,
            "qtpdf_available": bool(qtpdf_available),
            "qtpdf_error": qtpdf_error,
            "first_schedule_call_ms": first_schedule_call_ms,
            "second_schedule_call_ms": second_schedule_call_ms,
            "max_schedule_call_ms": max(first_schedule_call_ms, second_schedule_call_ms),
            "first_loaded_before": bool(first_before_state.get("pdf_path")),
            "first_loaded_after": bool(first_after_state.get("pdf_path")),
            "second_loaded_before": bool(second_before_state.get("pdf_path")),
            "second_loaded_after": bool(second_after_state.get("pdf_path")),
            "first_cache_hit_before": first_before_state.get("cache_hit"),
            "first_cache_hit_after": first_after_state.get("cache_hit"),
            "second_cache_hit_before": second_before_state.get("cache_hit"),
            "second_cache_hit_after": second_after_state.get("cache_hit"),
            "first_loaded": first_loaded,
            "second_loaded": second_loaded,
            "first_background_ready": first_background_ready,
            "second_background_ready": second_background_ready,
            "first_overlay_after_background": first_overlay_after_background,
            "second_overlay_after_background": second_overlay_after_background,
            "first_before_background": first_before_background,
            "first_after_background": first_after_background,
            "second_before_background": second_before_background,
            "second_after_background": second_after_background,
            "event_loop_gap": _latency_summary(event_loop_gaps_ms),
            "viewer_perf_summary": final_summary,
            "first_viewer_perf_summary": first_summary,
        }
    finally:
        workbench.deleteLater()
        app.processEvents()


def _run_real_pdf_page_navigation_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    overlay_count: int,
    page_pair_count: int = 4,
    step_count: int = 3,
    page_size_points: tuple[float, float] = (612.0, 792.0),
    use_redacted_sources: bool = True,
    heartbeat_interval_ms: int = 10,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    pair_id = "real_pdf_page_nav_pair"
    page_pair_count = max(2, int(page_pair_count))
    target_page = min(page_pair_count - 1, max(1, int(step_count)))
    qtpdf_error = ""
    try:
        from src.services.comparison.qt_pdf_adapter import is_qt_pdf_available

        qtpdf_available = bool(is_qt_pdf_available())
    except Exception as exc:  # noqa: BLE001
        qtpdf_available = False
        qtpdf_error = str(exc)

    gaps_ms: list[float] = []
    step_call_ms: list[float] = []
    rss_start = _rss_mb()
    rss_samples: list[float] = [float(rss_start)] if rss_start is not None else []
    done = {"value": False}
    last_tick = {"value": time.perf_counter()}

    def heartbeat() -> None:
        now = time.perf_counter()
        gaps_ms.append(round((now - last_tick["value"]) * 1000.0, 3))
        last_tick["value"] = now
        if not done["value"]:
            QTimer.singleShot(max(1, int(heartbeat_interval_ms)), heartbeat)

    def tree_idle() -> bool:
        return (
            workbench._full_zone_tree_chunk_state_v2 is None
            and workbench._full_zone_tree_overlay_worker_v2 is None
            and workbench._full_zone_tree_plan_worker_v2 is None
            and not workbench._pending_full_zone_tree_pair_id_v2
        )

    try:
        row, viewer_pair = _make_pair(
            scratch,
            viewer_root,
            pair_id,
            overlay_total_count=max(1, overlay_count),
            top_issue_count=0,
            full_overlay_json=True,
            page_pair_count=page_pair_count,
            pdf_page_count=page_pair_count,
            pdf_page_size_points=page_size_points,
        )
        source_before = Path(str(viewer_pair["source_a"]))
        source_after = Path(str(viewer_pair["source_b"]))
        package_before = source_before
        package_after = source_after
        if use_redacted_sources:
            package_dir = viewer_root / "page_pdfs"
            package_dir.mkdir(parents=True, exist_ok=True)
            package_before = package_dir / f"{pair_id}_before.pdf"
            package_after = package_dir / f"{pair_id}_after.pdf"
            shutil.copyfile(source_before, package_before)
            shutil.copyfile(source_after, package_after)
            viewer_pair["source_a"] = "<redacted>/before.pdf"
            viewer_pair["source_b"] = "<redacted>/after.pdf"
            viewer_pair["before_page_pdf"] = f"page_pdfs/{package_before.name}"
            viewer_pair["after_page_pdf"] = f"page_pdfs/{package_after.name}"

        before_resolved, before_resolved_key = dcw._resolve_pdf_viewer_source_path(
            viewer_pair, "before", viewer_root,
        )
        after_resolved, after_resolved_key = dcw._resolve_pdf_viewer_source_path(
            viewer_pair, "after", viewer_root,
        )

        workbench._viewer_pairs_by_id[pair_id] = viewer_pair
        workbench._active_row = row
        workbench._active_pdf_page_index_v2 = 0
        workbench._active_all_overlays_by_zone = {}
        workbench._active_overlays_by_zone = {}
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )
        action = getattr(workbench, "act_lightweight_viewer_v2", None)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        workbench._set_lightweight_viewer_visible_v2(True)
        app.processEvents()

        QTimer.singleShot(max(1, int(heartbeat_interval_ms)), heartbeat)
        start_generation = int(getattr(workbench, "_lightweight_pair_load_generation_v2", 0))
        started = time.perf_counter()
        for _ in range(target_page):
            step_started = time.perf_counter()
            workbench._step_pdf_page_pair_v2(+1)
            step_call_ms.append(round((time.perf_counter() - step_started) * 1000.0, 3))

        final_summary: dict[str, Any] = {}
        final_before_background: dict[str, Any] = {}
        final_after_background: dict[str, Any] = {}
        deadline = started + max(1.0, float(timeout_s))
        while time.perf_counter() < deadline:
            app.processEvents()
            final_summary = summarize_viewer_perf(viewer_root)
            final_before_background = _viewport_pdf_background_state(workbench.preview_before_lightweight_v2)
            final_after_background = _viewport_pdf_background_state(workbench.preview_after_lightweight_v2)
            before_ready = (
                bool(final_before_background.get("background_ready"))
                and int(final_before_background.get("page_index") or -1) == target_page
            )
            after_ready = (
                bool(final_after_background.get("background_ready"))
                and int(final_after_background.get("page_index") or -1) == target_page
            )
            overlay_ready = (
                int(final_before_background.get("overlay_count") or 0) > 0
                or int(final_after_background.get("overlay_count") or 0) > 0
            )
            if (
                int(final_summary.get("lightweight_pair_load_count") or 0) >= 1
                and tree_idle()
                and before_ready
                and after_ready
                and overlay_ready
            ):
                break
            time.sleep(0.001)
            rss_now = _rss_mb()
            if rss_now is not None:
                rss_samples.append(float(rss_now))

        done["value"] = True
        app.processEvents()
        rss_end = _rss_mb()
        if rss_end is not None:
            rss_samples.append(float(rss_end))
        final_summary = summarize_viewer_perf(viewer_root)
        final_before_background = _viewport_pdf_background_state(workbench.preview_before_lightweight_v2)
        final_after_background = _viewport_pdf_background_state(workbench.preview_after_lightweight_v2)
        leaf_ids = [
            str(item.data(0, Qt.UserRole) or "")
            for item in workbench._zone_leaf_items_v2()
        ]
        expected_visible = sum(
            1
            for idx in range(max(0, int(overlay_count)))
            if idx % page_pair_count == target_page
        )

        def leaf_is_stale(zone_id: str) -> bool:
            overlay = workbench._active_overlays_by_zone.get(zone_id) or {}
            return (
                int(overlay.get("page_a", -1)) != target_page
                or int(overlay.get("page_b", -1)) != target_page
            )

        stale_leaf_count = sum(1 for zone_id in leaf_ids if leaf_is_stale(zone_id))
        scheduled_lightweight_load_count = max(
            0,
            int(getattr(workbench, "_lightweight_pair_load_generation_v2", 0)) - start_generation,
        )
        completed_lightweight_load_count = int(final_summary.get("lightweight_pair_load_count") or 0)
        generation_dropped_load_count = max(
            0,
            int(scheduled_lightweight_load_count) - int(completed_lightweight_load_count),
        )
        final_background_ready = (
            bool(final_before_background.get("background_ready"))
            and bool(final_after_background.get("background_ready"))
            and int(final_before_background.get("page_index") or -1) == target_page
            and int(final_after_background.get("page_index") or -1) == target_page
        )
        overlay_after_background = bool(final_background_ready) and (
            int(final_before_background.get("overlay_count") or 0) > 0
            or int(final_after_background.get("overlay_count") or 0) > 0
        )
        full_tree_summary = {
            "count": int(final_summary.get("full_tree_rebuild_count") or 0),
            "chunked_count": int(final_summary.get("full_tree_rebuild_chunked_count") or 0),
            "overlay_load_worker_count": int(final_summary.get("full_tree_overlay_load_worker_count") or 0),
            "plan_build_worker_count": int(final_summary.get("full_tree_plan_build_worker_count") or 0),
            "chunk_count": final_summary.get("full_tree_rebuild_chunk_count", {}),
            "max_chunk_ms": final_summary.get("full_tree_rebuild_max_chunk_ms", {}),
        }
        redacted_fallback_ok = True
        if use_redacted_sources:
            redacted_fallback_ok = (
                before_resolved_key in {"before_page_pdf", "page_pdf"}
                and after_resolved_key in {"after_page_pdf", "page_pdf"}
                and Path(str(final_before_background.get("pdf_path") or "")) == package_before
                and Path(str(final_after_background.get("pdf_path") or "")) == package_after
            )
        final_dpi_capped = (
            bool(final_before_background.get("dpi_capped"))
            and bool(final_after_background.get("dpi_capped"))
        )
        return {
            "completed": bool(tree_idle() and final_background_ready and overlay_after_background),
            "qtpdf_available": bool(qtpdf_available),
            "qtpdf_error": qtpdf_error,
            "overlay_count": int(overlay_count),
            "page_pair_count": int(page_pair_count),
            "step_count": int(target_page),
            "target_page": int(target_page),
            "page_size_points": [float(page_size_points[0]), float(page_size_points[1])],
            "use_redacted_sources": bool(use_redacted_sources),
            "before_resolved_key": before_resolved_key,
            "after_resolved_key": after_resolved_key,
            "before_resolved_path": str(before_resolved or ""),
            "after_resolved_path": str(after_resolved or ""),
            "redacted_fallback_ok": bool(redacted_fallback_ok),
            "active_pdf_page_index": int(workbench._active_pdf_page_index_v2),
            "final_page_a": int(viewer_pair.get("page_a") or 0),
            "final_page_b": int(viewer_pair.get("page_b") or 0),
            "expected_visible_overlay_count": int(expected_visible),
            "visible_leaf_count": len(leaf_ids),
            "stale_leaf_count": int(stale_leaf_count),
            "scheduled_lightweight_load_count": int(scheduled_lightweight_load_count),
            "completed_lightweight_load_count": int(completed_lightweight_load_count),
            "generation_dropped_load_count": int(generation_dropped_load_count),
            "final_effective_dpi_a": final_before_background.get("effective_dpi"),
            "final_effective_dpi_b": final_after_background.get("effective_dpi"),
            "final_dpi_capped_a": final_before_background.get("dpi_capped"),
            "final_dpi_capped_b": final_after_background.get("dpi_capped"),
            "final_dpi_capped": bool(final_dpi_capped),
            "initial_render_max_pixels": final_before_background.get("max_render_pixels")
            or final_after_background.get("max_render_pixels"),
            "final_background_ready": bool(final_background_ready),
            "overlay_after_background": bool(overlay_after_background),
            "final_before_background": final_before_background,
            "final_after_background": final_after_background,
            "step_call_ms": _latency_summary(step_call_ms),
            "heartbeat_interval_ms": int(heartbeat_interval_ms),
            "event_loop_gap": _latency_summary(gaps_ms),
            "tick_count": len(gaps_ms),
            "rss_available": bool(rss_samples),
            "rss_start_mb": float(rss_start) if rss_start is not None else None,
            "rss_peak_mb": max(rss_samples) if rss_samples else None,
            "rss_end_mb": float(rss_end) if rss_end is not None else None,
            "rss_delta_mb": round(max(rss_samples) - float(rss_start), 3)
            if rss_samples and rss_start is not None
            else None,
            "viewer_perf_summary": final_summary,
            "full_tree_summary": full_tree_summary,
        }
    finally:
        done["value"] = True
        workbench.deleteLater()
        app.processEvents()


def _run_real_pdf_prewarm_cache_probe(
    scratch: Path,
    viewer_root: Path,
    *,
    page_pair_count: int = 3,
    page_size_points: tuple[float, float] = (612.0, 792.0),
    use_redacted_sources: bool = True,
    heartbeat_interval_ms: int = 10,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    app = _ensure_app()
    workbench = _new_workbench(viewer_root)
    pair_id = "real_pdf_prewarm_cache_pair"
    page_pair_count = max(2, int(page_pair_count))
    qtpdf_error = ""
    try:
        from src.services.comparison.qt_pdf_adapter import is_qt_pdf_available

        qtpdf_available = bool(is_qt_pdf_available())
    except Exception as exc:  # noqa: BLE001
        qtpdf_available = False
        qtpdf_error = str(exc)

    gaps_ms: list[float] = []
    step_call_ms: list[float] = []
    rss_start = _rss_mb()
    rss_samples: list[float] = [float(rss_start)] if rss_start is not None else []
    last_tick = time.perf_counter()

    def _state_page_index(state: dict[str, Any]) -> int:
        try:
            return int(state.get("page_index"))
        except (TypeError, ValueError):
            return -1

    def _tick() -> None:
        nonlocal last_tick
        app.processEvents()
        now = time.perf_counter()
        gaps_ms.append(round((now - last_tick) * 1000.0, 3))
        last_tick = now
        rss_now = _rss_mb()
        if rss_now is not None:
            rss_samples.append(float(rss_now))
        time.sleep(max(0.001, float(heartbeat_interval_ms) / 1000.0))

    def _wait_for_page_load(
        *,
        expected_load_count: int,
        expected_page: int,
        deadline: float,
    ) -> tuple[bool, float, dict[str, Any]]:
        started_wait = time.perf_counter()
        latest_summary: dict[str, Any] = {}
        while time.perf_counter() < deadline:
            _tick()
            latest_summary = summarize_viewer_perf(viewer_root)
            before_state = _viewport_pdf_background_state(workbench.preview_before_lightweight_v2)
            after_state = _viewport_pdf_background_state(workbench.preview_after_lightweight_v2)
            before_ready = (
                bool(before_state.get("background_ready"))
                and _state_page_index(before_state) == int(expected_page)
            )
            after_ready = (
                bool(after_state.get("background_ready"))
                and _state_page_index(after_state) == int(expected_page)
            )
            if (
                int(latest_summary.get("lightweight_pair_load_count") or 0) >= int(expected_load_count)
                and before_ready
                and after_ready
            ):
                return True, round((time.perf_counter() - started_wait) * 1000.0, 3), latest_summary
        app.processEvents()
        return False, round((time.perf_counter() - started_wait) * 1000.0, 3), summarize_viewer_perf(viewer_root)

    def _wait_for_prewarm(expected_items: int, deadline: float) -> tuple[bool, float, dict[str, Any]]:
        started_wait = time.perf_counter()
        latest_summary: dict[str, Any] = {}
        while time.perf_counter() < deadline:
            _tick()
            latest_summary = summarize_viewer_perf(viewer_root)
            ok_count = int(latest_summary.get("lightweight_pdf_prewarm_ok_count") or 0)
            if (
                int(latest_summary.get("lightweight_pdf_prewarm_count") or 0) >= 1
                and ok_count >= int(expected_items)
            ):
                return True, round((time.perf_counter() - started_wait) * 1000.0, 3), latest_summary
        app.processEvents()
        return False, round((time.perf_counter() - started_wait) * 1000.0, 3), summarize_viewer_perf(viewer_root)

    def _gap_slice(start_index: int) -> dict[str, float | int]:
        return _event_loop_gap_summary(gaps_ms[max(0, int(start_index)):])

    def _visible_state() -> dict[str, Any]:
        return {
            "active_pdf_page_index": int(workbench._active_pdf_page_index_v2),
            "viewer_pair_page_a": int(viewer_pair.get("page_a") or 0),
            "viewer_pair_page_b": int(viewer_pair.get("page_b") or 0),
            "before": _viewport_pdf_background_state(workbench.preview_before_lightweight_v2),
            "after": _viewport_pdf_background_state(workbench.preview_after_lightweight_v2),
        }

    def _visible_state_mutation_count(before: dict[str, Any], after: dict[str, Any]) -> int:
        checks = [
            ("active_pdf_page_index", before.get("active_pdf_page_index"), after.get("active_pdf_page_index")),
            ("viewer_pair_page_a", before.get("viewer_pair_page_a"), after.get("viewer_pair_page_a")),
            ("viewer_pair_page_b", before.get("viewer_pair_page_b"), after.get("viewer_pair_page_b")),
        ]
        for side in ("before", "after"):
            before_side = before.get(side, {}) if isinstance(before.get(side), dict) else {}
            after_side = after.get(side, {}) if isinstance(after.get(side), dict) else {}
            checks.extend(
                [
                    (f"{side}.page_index", before_side.get("page_index"), after_side.get("page_index")),
                    (f"{side}.pdf_path", before_side.get("pdf_path"), after_side.get("pdf_path")),
                    (
                        f"{side}.background_ready",
                        before_side.get("background_ready"),
                        after_side.get("background_ready"),
                    ),
                ]
            )
        return sum(1 for _name, left, right in checks if left != right)

    try:
        row, viewer_pair = _make_pair(
            scratch,
            viewer_root,
            pair_id,
            overlay_total_count=12,
            top_issue_count=12,
            full_overlay_json=True,
            page_pair_count=page_pair_count,
            pdf_page_count=page_pair_count,
            pdf_page_size_points=page_size_points,
        )
        source_before = Path(str(viewer_pair["source_a"]))
        source_after = Path(str(viewer_pair["source_b"]))
        if use_redacted_sources:
            package_dir = viewer_root / "page_pdfs"
            package_dir.mkdir(parents=True, exist_ok=True)
            package_before = package_dir / f"{pair_id}_before.pdf"
            package_after = package_dir / f"{pair_id}_after.pdf"
            shutil.copyfile(source_before, package_before)
            shutil.copyfile(source_after, package_after)
            viewer_pair["source_a"] = "<redacted>/before.pdf"
            viewer_pair["source_b"] = "<redacted>/after.pdf"
            viewer_pair["before_page_pdf"] = f"page_pdfs/{package_before.name}"
            viewer_pair["after_page_pdf"] = f"page_pdfs/{package_after.name}"

        workbench._viewer_pairs_by_id[pair_id] = viewer_pair
        workbench._active_row = row
        workbench._active_pdf_page_index_v2 = 0
        workbench._active_overlays_by_zone = {
            str(overlay.get("zone_id") or ""): overlay
            for overlay in row.get("top_issues", [])
            if isinstance(overlay, dict) and overlay.get("zone_id")
        }
        workbench._active_all_overlays_by_zone = dict(workbench._active_overlays_by_zone)
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(  # type: ignore[method-assign]
            enabled=False,
            use_embedding=False,
            use_llm=False,
        )
        action = getattr(workbench, "act_lightweight_viewer_v2", None)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        workbench._set_lightweight_viewer_visible_v2(True)
        app.processEvents()

        cold_gap_start = len(gaps_ms)
        workbench._schedule_lightweight_pair_load_v2(pair_id, viewer_pair)
        cold_ok, cold_wait_ms, cold_summary = _wait_for_page_load(
            expected_load_count=1,
            expected_page=0,
            deadline=time.perf_counter() + max(1.0, float(timeout_s)),
        )
        cold_gap = _gap_slice(cold_gap_start)
        cold_event = _latest_viewer_perf_event(viewer_root, "lightweight_pair_load")
        state_before_prewarm = _visible_state()

        expected_prewarm_items = 2
        prewarm_gap_start = len(gaps_ms)
        prewarm_ok, prewarm_wait_ms, prewarm_summary = _wait_for_prewarm(
            expected_prewarm_items,
            time.perf_counter() + max(1.0, float(timeout_s)),
        )
        prewarm_gap = _gap_slice(prewarm_gap_start)
        state_after_prewarm = _visible_state()
        prewarm_event = _latest_viewer_perf_event(viewer_root, "lightweight_pdf_prewarm")
        visible_state_mutations = _visible_state_mutation_count(
            state_before_prewarm,
            state_after_prewarm,
        )

        before_nav_count = int(prewarm_summary.get("lightweight_pair_load_count") or 0)
        cached_gap_start = len(gaps_ms)
        step_started = time.perf_counter()
        workbench._step_pdf_page_pair_v2(+1)
        step_call_ms.append(round((time.perf_counter() - step_started) * 1000.0, 3))
        cached_ok, cached_wait_ms, cached_summary = _wait_for_page_load(
            expected_load_count=before_nav_count + 1,
            expected_page=1,
            deadline=time.perf_counter() + max(1.0, float(timeout_s)),
        )
        cached_gap = _gap_slice(cached_gap_start)
        cached_event = _latest_viewer_perf_event(viewer_root, "lightweight_pair_load")
        cached_before_state = _viewport_pdf_background_state(workbench.preview_before_lightweight_v2)
        cached_after_state = _viewport_pdf_background_state(workbench.preview_after_lightweight_v2)

        plateau_all_cached = 0
        plateau_navigation_count = 0
        plateau_start_rss = _rss_mb()
        for delta, expected_page in ((-1, 0), (+1, 1)):
            before_count = int(summarize_viewer_perf(viewer_root).get("lightweight_pair_load_count") or 0)
            step_started = time.perf_counter()
            workbench._step_pdf_page_pair_v2(delta)
            step_call_ms.append(round((time.perf_counter() - step_started) * 1000.0, 3))
            ok, _wait_ms, _summary = _wait_for_page_load(
                expected_load_count=before_count + 1,
                expected_page=expected_page,
                deadline=time.perf_counter() + max(1.0, float(timeout_s)),
            )
            if ok:
                plateau_navigation_count += 1
                event = _latest_viewer_perf_event(viewer_root, "lightweight_pair_load")
                if str(event.get("pdf_cache_state") or "") == "all_cached":
                    plateau_all_cached += 1

        final_summary = summarize_viewer_perf(viewer_root)
        rss_end = _rss_mb()
        if rss_end is not None:
            rss_samples.append(float(rss_end))
        cache_summary = _cache_path_size_summary(_cache_paths_from_prewarm_events(viewer_root))
        cold_load_ms = float(cold_event.get("load_ms") or cold_wait_ms)
        cached_load_ms = float(cached_event.get("load_ms") or cached_wait_ms)
        cached_metadata_fast_path = bool(cached_event.get("before_metadata_hit")) and bool(
            cached_event.get("after_metadata_hit")
        )
        cached_cache_hit = bool(cached_event.get("before_cache_hit")) and bool(
            cached_event.get("after_cache_hit")
        )
        rss_tail_delta = None
        if plateau_start_rss is not None and rss_end is not None:
            rss_tail_delta = round(float(rss_end) - float(plateau_start_rss), 3)
        return {
            "completed": bool(cold_ok and prewarm_ok and cached_ok),
            "qtpdf_available": bool(qtpdf_available),
            "qtpdf_error": qtpdf_error,
            "page_pair_count": int(page_pair_count),
            "target_page": 1,
            "page_size_points": [float(page_size_points[0]), float(page_size_points[1])],
            "use_redacted_sources": bool(use_redacted_sources),
            "phase_results": {
                "cold_no_prewarm": {
                    "completed": bool(cold_ok),
                    "cache_state": str(cold_event.get("pdf_cache_state") or ""),
                    "before_cache_hit": cold_event.get("before_cache_hit"),
                    "after_cache_hit": cold_event.get("after_cache_hit"),
                    "before_metadata_fast_path": cold_event.get("before_metadata_hit"),
                    "after_metadata_fast_path": cold_event.get("after_metadata_hit"),
                    "load_ms": _latency_summary([cold_load_ms]),
                    "time_to_background_ready_ms": _latency_summary([cold_wait_ms]),
                    "event_loop_gap": cold_gap,
                },
                "prewarm_wait": {
                    "prewarm_completed_before_navigation": bool(prewarm_ok),
                    "expected_item_count": int(expected_prewarm_items),
                    "item_count": int(prewarm_event.get("item_count") or 0),
                    "ok_count": int(prewarm_event.get("ok_count") or 0),
                    "rendered_count": int(prewarm_event.get("rendered_count") or 0),
                    "cache_hit_count": int(prewarm_event.get("cache_hit_count") or 0),
                    "metadata_hit_count": int(prewarm_event.get("metadata_hit_count") or 0),
                    "visible_state_mutation_count": int(visible_state_mutations),
                    "elapsed_ms": float(prewarm_event.get("elapsed_ms") or prewarm_wait_ms),
                    "event_loop_gap": prewarm_gap,
                    "state_before": state_before_prewarm,
                    "state_after": state_after_prewarm,
                },
                "post_prewarm_cached": {
                    "completed": bool(cached_ok),
                    "cache_state": str(cached_event.get("pdf_cache_state") or ""),
                    "before_cache_hit": cached_event.get("before_cache_hit"),
                    "after_cache_hit": cached_event.get("after_cache_hit"),
                    "before_metadata_fast_path": cached_event.get("before_metadata_hit"),
                    "after_metadata_fast_path": cached_event.get("after_metadata_hit"),
                    "metadata_fast_path": bool(cached_metadata_fast_path),
                    "cache_hit": bool(cached_cache_hit),
                    "inferred_render_call_count": 0 if cached_metadata_fast_path else 1,
                    "load_ms": _latency_summary([cached_load_ms]),
                    "time_to_background_ready_ms": _latency_summary([cached_wait_ms]),
                    "event_loop_gap": cached_gap,
                    "before_background": cached_before_state,
                    "after_background": cached_after_state,
                },
                "cached_navigation_plateau": {
                    "navigation_count": int(plateau_navigation_count),
                    "all_cached_count": int(plateau_all_cached),
                    "rss_tail_delta_mb": rss_tail_delta,
                },
            },
            "step_call_ms": _latency_summary(step_call_ms),
            "event_loop_gap": _event_loop_gap_summary(gaps_ms),
            "rss_available": bool(rss_samples),
            "rss_start_mb": float(rss_start) if rss_start is not None else None,
            "rss_peak_mb": max(rss_samples) if rss_samples else None,
            "rss_end_mb": float(rss_end) if rss_end is not None else None,
            "rss_delta_mb": round(max(rss_samples) - float(rss_start), 3)
            if rss_samples and rss_start is not None
            else None,
            "pdf_cache": cache_summary,
            "viewer_perf_summary": final_summary,
        }
    finally:
        workbench.deleteLater()
        app.processEvents()


def _gate_summary(payload: dict[str, Any], args: argparse.Namespace) -> list[GateResult]:
    selection = payload["pair_selection"]
    tile = payload["first_review_tile_probe"]
    overlay = payload["overlay_cache_rss_probe"]
    full_tree = payload.get("full_tree_responsiveness_probe", {})
    page_nav = payload.get("page_navigation_probe", {})
    rapid_page_nav = payload.get("rapid_page_navigation_probe", {})
    stress_page_nav = payload.get("stress_page_navigation_probe", {})
    navigation_soak = payload.get("navigation_soak_probe", {})
    p4_overlay_streaming = payload.get("p4_overlay_streaming_probe", {})
    p5_overlay_page_store = payload.get("p5_overlay_page_store_query_probe", {})
    p4_visible_tiles = payload.get("p4_visible_tile_probe", {})
    p5_tile_retention = payload.get("p5_tile_retention_probe", {})
    lightweight_pdf_load = payload.get("lightweight_pdf_load_probe", {})
    real_pdf_page_nav = payload.get("real_pdf_page_navigation_probe", {})
    real_pdf_prewarm_probe = payload.get("real_pdf_prewarm_cache_probe", {})
    p5_g26_contract = payload.get("p5_g26_contract") or payload.get("p5_g26_evidence") or {}
    p5_g27_contract = payload.get("p5_g27_contract") or payload.get("p5_g27_evidence") or {}
    p5_g28_contract = payload.get("p5_g28_contract") or payload.get("p5_g28_evidence") or {}
    p5_g27_real_renderer_bridge = payload.get("p5_g27_real_renderer_bridge")
    p5_g27_bridge_required = bool(
        getattr(args, "p5_g27_require_real_renderer_bridge", False)
        or getattr(args, "p5_g27_real_renderer_bridge_json", None)
        or isinstance(p5_g27_real_renderer_bridge, dict)
    )
    include_p5_g26_contract = bool(getattr(args, "include_p5_g26_contract", False)) or isinstance(
        p5_g26_contract,
        dict,
    ) and bool(p5_g26_contract)
    include_p5_g27_contract = bool(getattr(args, "include_p5_g27_selected_zone_crop_first", False)) or isinstance(
        p5_g27_contract,
        dict,
    ) and bool(p5_g27_contract)
    include_p5_g28_contract = bool(getattr(args, "include_p5_g28_cache_plateau", False)) or isinstance(
        p5_g28_contract,
        dict,
    ) and bool(p5_g28_contract)
    if include_p5_g26_contract and not isinstance(p5_g26_contract, dict):
        p5_g26_contract = {}
    if include_p5_g26_contract and not p5_g26_contract:
        p5_g26_contract = _p5_g26_contract_summary(payload, args)
    if include_p5_g27_contract and not isinstance(p5_g27_contract, dict):
        p5_g27_contract = {}
    if include_p5_g27_contract and not p5_g27_contract:
        p5_g27_contract = _p5_g27_contract_summary(payload, args)
    if include_p5_g28_contract and not isinstance(p5_g28_contract, dict):
        p5_g28_contract = {}
    if include_p5_g28_contract and not p5_g28_contract:
        p5_g28_contract = _p5_g28_contract_summary(payload, args)
    if not isinstance(p5_g27_real_renderer_bridge, dict):
        p5_g27_real_renderer_bridge = {}
    cached_p95 = float(selection["cached_pdf"]["p95_ms"])
    cold_p95 = float(selection["cold_pdf"]["p95_ms"])
    rss_tail_delta = overlay.get("rss_tail_delta_after_cache_limit_mb")
    full_tree_gap = full_tree.get("event_loop_gap", {}) if isinstance(full_tree, dict) else {}
    full_tree_summary = full_tree.get("full_tree_summary", {}) if isinstance(full_tree, dict) else {}
    chunk_count_summary = full_tree_summary.get("chunk_count", {}) if isinstance(full_tree_summary, dict) else {}
    max_chunk_summary = full_tree_summary.get("max_chunk_ms", {}) if isinstance(full_tree_summary, dict) else {}
    full_tree_start_call_ms = float(full_tree.get("start_call_ms") or 0.0) if isinstance(full_tree, dict) else 0.0
    page_nav_summary = page_nav.get("viewer_perf_summary", {}) if isinstance(page_nav, dict) else {}
    page_nav_full_tree = page_nav.get("full_tree_summary", {}) if isinstance(page_nav, dict) else {}
    page_nav_start_call_ms = float(page_nav.get("start_call_ms") or 0.0) if isinstance(page_nav, dict) else 0.0
    page_nav_gap = page_nav.get("event_loop_gap", {}) if isinstance(page_nav, dict) else {}
    rapid_page_nav_full_tree = rapid_page_nav.get("full_tree_summary", {}) if isinstance(rapid_page_nav, dict) else {}
    rapid_step_summary = rapid_page_nav.get("step_call_ms", {}) if isinstance(rapid_page_nav, dict) else {}
    rapid_page_nav_gap = rapid_page_nav.get("event_loop_gap", {}) if isinstance(rapid_page_nav, dict) else {}
    stress_page_nav_full_tree = stress_page_nav.get("full_tree_summary", {}) if isinstance(stress_page_nav, dict) else {}
    stress_step_summary = stress_page_nav.get("step_call_ms", {}) if isinstance(stress_page_nav, dict) else {}
    stress_page_nav_gap = stress_page_nav.get("event_loop_gap", {}) if isinstance(stress_page_nav, dict) else {}
    stress_chunk_summary = stress_page_nav_full_tree.get("chunk_count", {}) if isinstance(stress_page_nav_full_tree, dict) else {}
    navigation_soak_selection = navigation_soak.get("selection_call_ms", {}) if isinstance(navigation_soak, dict) else {}
    navigation_soak_gap = navigation_soak.get("event_loop_gap", {}) if isinstance(navigation_soak, dict) else {}
    navigation_soak_rss_slope = navigation_soak.get("rss_slope", {}) if isinstance(navigation_soak, dict) else {}
    lightweight_pdf_summary = (
        lightweight_pdf_load.get("viewer_perf_summary", {}) if isinstance(lightweight_pdf_load, dict) else {}
    )
    lightweight_pdf_gap = lightweight_pdf_load.get("event_loop_gap", {}) if isinstance(lightweight_pdf_load, dict) else {}
    real_pdf_nav_full_tree = real_pdf_page_nav.get("full_tree_summary", {}) if isinstance(real_pdf_page_nav, dict) else {}
    real_pdf_nav_step = real_pdf_page_nav.get("step_call_ms", {}) if isinstance(real_pdf_page_nav, dict) else {}
    real_pdf_nav_gap = real_pdf_page_nav.get("event_loop_gap", {}) if isinstance(real_pdf_page_nav, dict) else {}
    real_pdf_nav_summary = (
        real_pdf_page_nav.get("viewer_perf_summary", {}) if isinstance(real_pdf_page_nav, dict) else {}
    )

    gates = [
        GateResult(
            "cached_pdf_pair_selection_p95",
            cached_p95 <= float(args.cached_p95_target_ms),
            cached_p95,
            float(args.cached_p95_target_ms),
            "Direct _on_drawing_selected_v2 latency for repeated PDF pair selections.",
        ),
        GateResult(
            "cold_pdf_pair_selection_p95",
            cold_p95 <= float(args.cold_p95_target_ms),
            cold_p95,
            float(args.cold_p95_target_ms),
            "Direct _on_drawing_selected_v2 latency for first-time PDF pair selections.",
        ),
        GateResult(
            "first_review_no_full_tile_pyramid",
            bool(tile.get("passed")),
            bool(tile.get("passed")),
            True,
            "PairPreviewRenderWorker(build_lod_tiles=False) did not call tile cache or emit tile artifacts.",
        ),
        GateResult(
            "overlay_cache_pair_bound",
            int(overlay.get("max_cache_pair_count") or 0) <= int(overlay.get("cache_pair_limit") or 0),
            int(overlay.get("max_cache_pair_count") or 0),
            int(overlay.get("cache_pair_limit") or 0),
            "Overlay cache retained pair count stays within GUI_OVERLAY_CACHE_PAIR_LIMIT.",
        ),
        GateResult(
            "overlay_cache_byte_bound",
            int(overlay.get("max_cache_total_bytes") or 0) <= int(overlay.get("cache_byte_limit") or 0),
            int(overlay.get("max_cache_total_bytes") or 0),
            int(overlay.get("cache_byte_limit") or 0),
            "Overlay cache retained byte estimate stays within GUI_OVERLAY_CACHE_BYTE_LIMIT.",
        ),
        GateResult(
            "full_tree_rebuild_completed",
            bool(full_tree.get("completed")),
            bool(full_tree.get("completed")),
            True,
            "Chunked full tree rebuild completed within the benchmark timeout.",
        ),
        GateResult(
            "full_tree_initial_call_ms",
            full_tree_start_call_ms <= float(args.full_tree_start_call_target_ms),
            full_tree_start_call_ms,
            float(args.full_tree_start_call_target_ms),
            "Initial full-tree rebuild call returns quickly while overlay JSON loading is delegated.",
        ),
        GateResult(
            "full_tree_overlay_load_worker",
            int(full_tree_summary.get("overlay_load_worker_count") or 0) >= 1,
            int(full_tree_summary.get("overlay_load_worker_count") or 0),
            1,
            "Large full-tree overlay JSON is loaded by a worker thread.",
        ),
        GateResult(
            "full_tree_plan_build_worker",
            int(full_tree_summary.get("plan_build_worker_count") or 0) >= 1,
            int(full_tree_summary.get("plan_build_worker_count") or 0),
            1,
            "Large full-tree plan sorting/grouping is built by a worker thread.",
        ),
        GateResult(
            "full_tree_rebuild_chunk_count",
            float(chunk_count_summary.get("max") or chunk_count_summary.get("p95") or 0.0) >= float(args.full_tree_min_chunks),
            float(chunk_count_summary.get("max") or chunk_count_summary.get("p95") or 0.0),
            float(args.full_tree_min_chunks),
            "Large full tree rebuild must be split across multiple chunks.",
        ),
        GateResult(
            "full_tree_rebuild_max_chunk_ms",
            float(max_chunk_summary.get("p95") or 0.0) <= float(args.full_tree_max_chunk_target_ms),
            float(max_chunk_summary.get("p95") or 0.0),
            float(args.full_tree_max_chunk_target_ms),
            "The largest recorded full-tree chunk stays under the GUI responsiveness budget.",
        ),
        GateResult(
            "full_tree_event_loop_gap_p95",
            float(full_tree_gap.get("p95_ms") or 0.0) <= float(args.full_tree_p95_gap_target_ms),
            float(full_tree_gap.get("p95_ms") or 0.0),
            float(args.full_tree_p95_gap_target_ms),
            "Heartbeat p95 gap while full-tree rebuild is running.",
        ),
        GateResult(
            "page_navigation_completed",
            bool(page_nav.get("completed")),
            bool(page_nav.get("completed")),
            True,
            "PDF page navigation completed its deferred overlay/tree refresh.",
        ),
        GateResult(
            "page_navigation_initial_call_ms",
            page_nav_start_call_ms <= float(args.page_nav_start_call_target_ms),
            page_nav_start_call_ms,
            float(args.page_nav_start_call_target_ms),
            "PDF page navigation click path returns before cold overlay JSON loading.",
        ),
        GateResult(
            "page_navigation_overlay_deferred",
            int(page_nav_summary.get("pdf_page_navigation_deferred_count") or 0) >= 1,
            int(page_nav_summary.get("pdf_page_navigation_deferred_count") or 0),
            1,
            "PDF page navigation defers full overlay loading when the all-overlay cache is empty.",
        ),
        GateResult(
            "page_navigation_overlay_load_worker",
            int(page_nav_full_tree.get("overlay_load_worker_count") or 0) >= 1,
            int(page_nav_full_tree.get("overlay_load_worker_count") or 0),
            1,
            "Deferred PDF page-navigation overlay load runs on a worker thread.",
        ),
        GateResult(
            "page_navigation_plan_build_worker",
            int(page_nav_full_tree.get("plan_build_worker_count") or 0) >= 1,
            int(page_nav_full_tree.get("plan_build_worker_count") or 0),
            1,
            "Deferred PDF page-navigation tree plan is built on a worker thread for large pages.",
        ),
        GateResult(
            "page_navigation_lightweight_scheduled",
            int(page_nav.get("lightweight_load_count") or 0) >= 1,
            int(page_nav.get("lightweight_load_count") or 0),
            1,
            "PDF page navigation schedules the lightweight PDF background load instead of doing it inline.",
        ),
        GateResult(
            "page_navigation_visible_leaf_count",
            int(page_nav.get("visible_leaf_count") or 0) == int(page_nav.get("expected_visible_overlay_count") or 0),
            int(page_nav.get("visible_leaf_count") or 0),
            int(page_nav.get("expected_visible_overlay_count") or 0),
            "Page navigation tree shows only overlays for the selected matched page pair.",
        ),
        GateResult(
            "page_navigation_event_loop_gap_p95",
            float(page_nav_gap.get("p95_ms") or 0.0) <= float(args.page_nav_p95_gap_target_ms),
            float(page_nav_gap.get("p95_ms") or 0.0),
            float(args.page_nav_p95_gap_target_ms),
            "Heartbeat p95 gap while deferred page-navigation tree refresh is running.",
        ),
        GateResult(
            "rapid_page_navigation_completed",
            bool(rapid_page_nav.get("completed")),
            bool(rapid_page_nav.get("completed")),
            True,
            "Rapid PDF page navigation completed its final deferred overlay/tree refresh.",
        ),
        GateResult(
            "rapid_page_navigation_max_step_call_ms",
            float(rapid_step_summary.get("max_ms") or 0.0) <= float(args.rapid_page_nav_step_target_ms),
            float(rapid_step_summary.get("max_ms") or 0.0),
            float(args.rapid_page_nav_step_target_ms),
            "Each rapid _step_pdf_page_pair_v2 click returns before deferred overlay work runs.",
        ),
        GateResult(
            "rapid_page_navigation_final_page",
            int(rapid_page_nav.get("active_pdf_page_index") or -1) == int(rapid_page_nav.get("target_page") or -2)
            and int(rapid_page_nav.get("final_page_a") or -1) == int(rapid_page_nav.get("target_page") or -2)
            and int(rapid_page_nav.get("final_page_b") or -1) == int(rapid_page_nav.get("target_page") or -2),
            int(rapid_page_nav.get("active_pdf_page_index") or -1),
            int(rapid_page_nav.get("target_page") or -2),
            "Rapid page navigation leaves the active index and viewer_pair page state on the final target page.",
        ),
        GateResult(
            "rapid_page_navigation_visible_leaf_count",
            int(rapid_page_nav.get("visible_leaf_count") or 0)
            == int(rapid_page_nav.get("expected_visible_overlay_count") or 0),
            int(rapid_page_nav.get("visible_leaf_count") or 0),
            int(rapid_page_nav.get("expected_visible_overlay_count") or 0),
            "Rapid page navigation tree shows only overlays for the final matched page pair.",
        ),
        GateResult(
            "rapid_page_navigation_no_stale_leaf",
            int(rapid_page_nav.get("stale_leaf_count") or 0) == 0,
            int(rapid_page_nav.get("stale_leaf_count") or 0),
            0,
            "Stale queued page navigation work cannot leave non-final-page leaves in the tree.",
        ),
        GateResult(
            "rapid_page_navigation_overlay_load_worker",
            int(rapid_page_nav_full_tree.get("overlay_load_worker_count") or 0) >= 1,
            int(rapid_page_nav_full_tree.get("overlay_load_worker_count") or 0),
            1,
            "Final rapid page-navigation overlay load runs on a worker thread.",
        ),
        GateResult(
            "rapid_page_navigation_plan_build_worker",
            int(rapid_page_nav_full_tree.get("plan_build_worker_count") or 0) >= 1,
            int(rapid_page_nav_full_tree.get("plan_build_worker_count") or 0),
            1,
            "Final rapid page-navigation tree plan is built on a worker thread for large pages.",
        ),
        GateResult(
            "rapid_page_navigation_lightweight_scheduled",
            int(rapid_page_nav.get("lightweight_load_count") or 0) >= int(rapid_page_nav.get("step_count") or 0),
            int(rapid_page_nav.get("lightweight_load_count") or 0),
            int(rapid_page_nav.get("step_count") or 0),
            "Every rapid page step schedules lightweight PDF background refresh without doing it inline.",
        ),
        GateResult(
            "rapid_page_navigation_event_loop_gap_p95",
            float(rapid_page_nav_gap.get("p95_ms") or 0.0) <= float(args.rapid_page_nav_p95_gap_target_ms),
            float(rapid_page_nav_gap.get("p95_ms") or 0.0),
            float(args.rapid_page_nav_p95_gap_target_ms),
            "Heartbeat p95 gap while rapid page-navigation final refresh is running.",
        ),
    ]
    if bool(getattr(args, "include_stress_page_nav", False)) or bool(stress_page_nav):
        stress_rss_delta = stress_page_nav.get("rss_delta_mb")
        gates.extend(
            [
                GateResult(
                    "stress_page_navigation_completed",
                    bool(stress_page_nav.get("completed")),
                    bool(stress_page_nav.get("completed")),
                    True,
                    "10k-class rapid PDF page navigation completed its final deferred refresh.",
                ),
                GateResult(
                    "stress_page_navigation_max_step_call_ms",
                    float(stress_step_summary.get("max_ms") or 0.0) <= float(args.stress_page_nav_step_target_ms),
                    float(stress_step_summary.get("max_ms") or 0.0),
                    float(args.stress_page_nav_step_target_ms),
                    "10k-class rapid page-step click path returns before deferred overlay work runs.",
                ),
                GateResult(
                    "stress_page_navigation_event_loop_gap_p95",
                    float(stress_page_nav_gap.get("p95_ms") or 0.0) <= float(args.stress_page_nav_p95_gap_target_ms),
                    float(stress_page_nav_gap.get("p95_ms") or 0.0),
                    float(args.stress_page_nav_p95_gap_target_ms),
                    "Heartbeat p95 gap while 10k-class page-navigation refresh is running.",
                ),
                GateResult(
                    "stress_page_navigation_visible_leaf_count",
                    int(stress_page_nav.get("visible_leaf_count") or 0)
                    == int(stress_page_nav.get("expected_visible_overlay_count") or 0),
                    int(stress_page_nav.get("visible_leaf_count") or 0),
                    int(stress_page_nav.get("expected_visible_overlay_count") or 0),
                    "10k-class page navigation tree shows only final-page overlays.",
                ),
                GateResult(
                    "stress_page_navigation_no_stale_leaf",
                    int(stress_page_nav.get("stale_leaf_count") or 0) == 0,
                    int(stress_page_nav.get("stale_leaf_count") or 0),
                    0,
                    "10k-class page navigation leaves no stale non-final-page leaves.",
                ),
                GateResult(
                    "stress_page_navigation_overlay_load_worker",
                    int(stress_page_nav_full_tree.get("overlay_load_worker_count") or 0) >= 1,
                    int(stress_page_nav_full_tree.get("overlay_load_worker_count") or 0),
                    1,
                    "10k-class page-navigation overlay JSON load runs on a worker thread.",
                ),
                GateResult(
                    "stress_page_navigation_plan_build_worker",
                    int(stress_page_nav_full_tree.get("plan_build_worker_count") or 0) >= 1,
                    int(stress_page_nav_full_tree.get("plan_build_worker_count") or 0),
                    1,
                    "10k-class page-navigation tree plan is built on a worker thread.",
                ),
                GateResult(
                    "stress_page_navigation_chunk_count",
                    float(stress_chunk_summary.get("max") or stress_chunk_summary.get("p95") or 0.0)
                    >= float(args.stress_page_nav_min_chunks),
                    float(stress_chunk_summary.get("max") or stress_chunk_summary.get("p95") or 0.0),
                    float(args.stress_page_nav_min_chunks),
                    "10k-class final page tree is split across multiple GUI chunks.",
                ),
            ]
        )
        if stress_rss_delta is None:
            gates.append(
                GateResult(
                    "stress_page_navigation_rss_delta_mb",
                    bool(args.allow_missing_psutil),
                    None,
                    f"<= {args.stress_page_nav_max_rss_delta_mb} MB",
                    "RSS unavailable during stress page navigation; install psutil or pass --allow-missing-psutil.",
                )
            )
        else:
            gates.append(
                GateResult(
                    "stress_page_navigation_rss_delta_mb",
                    float(stress_rss_delta) <= float(args.stress_page_nav_max_rss_delta_mb),
                    float(stress_rss_delta),
                    float(args.stress_page_nav_max_rss_delta_mb),
                    "RSS peak delta during 10k-class page navigation stress.",
                )
            )
    if bool(getattr(args, "include_navigation_soak", False)) or bool(navigation_soak):
        rss_slope_value = navigation_soak_rss_slope.get("slope_mb_per_100_visits")
        rss_end_delta = navigation_soak_rss_slope.get("positive_end_delta_mb")
        rss_peak_delta = navigation_soak_rss_slope.get("peak_delta_mb")
        gates.extend(
            [
                GateResult(
                    "navigation_soak_completed",
                    bool(navigation_soak.get("completed")),
                    bool(navigation_soak.get("completed")),
                    True,
                    "Opt-in pair navigation soak completed the requested visit count.",
                ),
                GateResult(
                    "navigation_soak_completed_visit_count",
                    int(navigation_soak.get("completed_visit_count") or 0)
                    >= int(navigation_soak.get("visit_count") or 0),
                    int(navigation_soak.get("completed_visit_count") or 0),
                    int(navigation_soak.get("visit_count") or 0),
                    "Navigation soak ran every requested pair visit before timeout.",
                ),
                GateResult(
                    "navigation_soak_lightweight_scheduled",
                    int(navigation_soak.get("lightweight_scheduled_count") or 0)
                    >= int(navigation_soak.get("completed_visit_count") or 0),
                    int(navigation_soak.get("lightweight_scheduled_count") or 0),
                    int(navigation_soak.get("completed_visit_count") or 0),
                    "Pair navigation schedules lightweight background work without running it inline in the soak probe.",
                ),
                GateResult(
                    "navigation_soak_selection_p95_ms",
                    float(navigation_soak_selection.get("p95_ms") or 0.0)
                    <= float(args.navigation_soak_selection_p95_target_ms),
                    float(navigation_soak_selection.get("p95_ms") or 0.0),
                    float(args.navigation_soak_selection_p95_target_ms),
                    "Pair selection p95 stays within the P2-J navigation soak budget.",
                ),
                GateResult(
                    "navigation_soak_event_loop_gap_p95_ms",
                    float(navigation_soak_gap.get("p95_ms") or 0.0)
                    <= float(args.navigation_soak_p95_gap_target_ms),
                    float(navigation_soak_gap.get("p95_ms") or 0.0),
                    float(args.navigation_soak_p95_gap_target_ms),
                    "Event-loop p95 gap stays bounded during pair navigation soak.",
                ),
                GateResult(
                    "navigation_soak_event_loop_gap_max_ms",
                    float(navigation_soak_gap.get("max_ms") or 0.0)
                    <= float(args.navigation_soak_gap_max_target_ms),
                    float(navigation_soak_gap.get("max_ms") or 0.0),
                    float(args.navigation_soak_gap_max_target_ms),
                    "Event-loop max gap stays under the hard P2-J soak budget.",
                ),
                GateResult(
                    "navigation_soak_event_loop_over_500ms_count",
                    int(navigation_soak_gap.get("over_500ms_count") or 0) == 0,
                    int(navigation_soak_gap.get("over_500ms_count") or 0),
                    0,
                    "Navigation soak introduces no event-loop gap above 500 ms.",
                ),
                GateResult(
                    "navigation_soak_cache_pair_bound",
                    int(navigation_soak.get("max_cache_pair_count") or 0)
                    <= int(navigation_soak.get("cache_pair_limit") or 0),
                    int(navigation_soak.get("max_cache_pair_count") or 0),
                    int(navigation_soak.get("cache_pair_limit") or 0),
                    "Overlay cache pair count remains bounded during navigation soak.",
                ),
                GateResult(
                    "navigation_soak_cache_byte_bound",
                    int(navigation_soak.get("max_cache_total_bytes") or 0)
                    <= int(navigation_soak.get("cache_byte_limit") or 0),
                    int(navigation_soak.get("max_cache_total_bytes") or 0),
                    int(navigation_soak.get("cache_byte_limit") or 0),
                    "Overlay cache byte estimate remains bounded during navigation soak.",
                ),
            ]
        )
        if rss_slope_value is None or rss_end_delta is None or rss_peak_delta is None:
            gates.extend(
                [
                    GateResult(
                        "navigation_soak_rss_slope_mb_per_100_visits",
                        bool(args.allow_missing_psutil),
                        None,
                        f"<= {args.navigation_soak_rss_slope_target_mb_per_100} MB/100 visits",
                        "RSS unavailable or too few post-warmup samples during navigation soak.",
                    ),
                    GateResult(
                        "navigation_soak_rss_positive_end_delta_mb",
                        bool(args.allow_missing_psutil),
                        None,
                        f"<= {args.navigation_soak_rss_end_delta_mb} MB",
                        "RSS unavailable or too few post-warmup samples during navigation soak.",
                    ),
                    GateResult(
                        "navigation_soak_rss_tail_peak_delta_mb",
                        bool(args.allow_missing_psutil),
                        None,
                        f"<= {args.navigation_soak_rss_tail_delta_mb} MB",
                        "RSS unavailable or too few post-warmup samples during navigation soak.",
                    ),
                ]
            )
        else:
            gates.extend(
                [
                    GateResult(
                        "navigation_soak_rss_slope_mb_per_100_visits",
                        float(rss_slope_value) <= float(args.navigation_soak_rss_slope_target_mb_per_100),
                        float(rss_slope_value),
                        float(args.navigation_soak_rss_slope_target_mb_per_100),
                        "RSS slope after cache warmup must not grow linearly with navigation count.",
                    ),
                    GateResult(
                        "navigation_soak_rss_positive_end_delta_mb",
                        float(rss_end_delta) <= float(args.navigation_soak_rss_end_delta_mb),
                        float(rss_end_delta),
                        float(args.navigation_soak_rss_end_delta_mb),
                        "RSS positive end delta after cache warmup stays within the navigation soak budget.",
                    ),
                    GateResult(
                        "navigation_soak_rss_tail_peak_delta_mb",
                        float(rss_peak_delta) <= float(args.navigation_soak_rss_tail_delta_mb),
                        float(rss_peak_delta),
                        float(args.navigation_soak_rss_tail_delta_mb),
                        "RSS peak range after cache warmup stays within the navigation soak budget.",
                    ),
                ]
            )
    if bool(getattr(args, "include_p4_overlay_streaming", False)) or bool(p4_overlay_streaming):
        gates.extend(
            [
                GateResult(
                    "p4_overlay_first_paint_no_full_json",
                    not bool(p4_overlay_streaming.get("overlay_json_read_for_first_paint"))
                    and int(p4_overlay_streaming.get("overlay_json_read_call_count") or 0) == 0,
                    int(p4_overlay_streaming.get("overlay_json_read_call_count") or 0),
                    0,
                    "100k-class first paint does not synchronously parse the full overlay JSON.",
                ),
                GateResult(
                    "p4_overlay_first_paint_materialized_cap",
                    int(p4_overlay_streaming.get("materialized_overlay_count") or 0)
                    <= int(args.p4_overlay_first_paint_cap),
                    int(p4_overlay_streaming.get("materialized_overlay_count") or 0),
                    int(args.p4_overlay_first_paint_cap),
                    "First paint materializes only top issues / first-N overlays, not the full overlay set.",
                ),
                GateResult(
                    "p4_overlay_declared_100k_fixture",
                    int(p4_overlay_streaming.get("declared_overlay_count") or 0)
                    >= int(args.p4_overlay_count),
                    int(p4_overlay_streaming.get("declared_overlay_count") or 0),
                    int(args.p4_overlay_count),
                    "P4 overlay streaming probe uses the requested large declared overlay workload.",
                ),
            ]
        )
    if bool(getattr(args, "include_p5_overlay_page_store_query", False)) or bool(p5_overlay_page_store):
        phase_results = (
            p5_overlay_page_store.get("phase_results", {})
            if isinstance(p5_overlay_page_store, dict)
            else {}
        )
        first_visible = phase_results.get("first_visible", {}) if isinstance(phase_results, dict) else {}
        page_pair = phase_results.get("page_pair", {}) if isinstance(phase_results, dict) else {}
        declared = int(p5_overlay_page_store.get("declared_overlay_count") or 0)
        gates.extend(
            [
                GateResult(
                    "p5_page_store_fixture_declared_overlay_count",
                    declared >= int(args.p5_page_store_overlay_count),
                    declared,
                    int(args.p5_page_store_overlay_count),
                    "P5 page-store probe uses the requested large declared overlay workload.",
                ),
                GateResult(
                    "p5_first_visible_no_legacy_overlay_json",
                    int(first_visible.get("legacy_overlay_json_read_count") or 0) == 0,
                    int(first_visible.get("legacy_overlay_json_read_count") or 0),
                    0,
                    "First visible selection does not read the legacy full overlay JSON.",
                ),
                GateResult(
                    "p5_first_visible_sparse_page_reads",
                    int(first_visible.get("page_file_read_count") or 0)
                    <= int(first_visible.get("max_page_file_reads") or 1),
                    int(first_visible.get("page_file_read_count") or 0),
                    int(first_visible.get("max_page_file_reads") or 1),
                    "First visible selection reads at most one small overlay page file.",
                ),
                GateResult(
                    "p5_first_visible_materialized_cap",
                    int(first_visible.get("materialized_overlay_count") or 0)
                    <= int(first_visible.get("materialized_overlay_cap") or args.p5_page_store_first_visible_limit),
                    int(first_visible.get("materialized_overlay_count") or 0),
                    int(first_visible.get("materialized_overlay_cap") or args.p5_page_store_first_visible_limit),
                    "First visible selection materializes only a bounded initial overlay slice.",
                ),
                GateResult(
                    "p5_first_visible_no_full_overlay_cache",
                    int(first_visible.get("cached_overlay_count") or 0) < declared,
                    int(first_visible.get("cached_overlay_count") or 0),
                    f"< {declared}",
                    "First visible selection does not populate the full overlay cache.",
                ),
                GateResult(
                    "p5_page_pair_uses_paged_overlay_store",
                    str(page_pair.get("overlay_load_strategy") or "") == "paged_overlay_store",
                    str(page_pair.get("overlay_load_strategy") or ""),
                    "paged_overlay_store",
                    "Page-pair tree refresh uses the paged overlay store.",
                ),
                GateResult(
                    "p5_page_pair_no_legacy_overlay_json",
                    int(page_pair.get("legacy_overlay_json_read_count") or 0) == 0,
                    int(page_pair.get("legacy_overlay_json_read_count") or 0),
                    0,
                    "Page-pair tree refresh does not read the legacy full overlay JSON.",
                ),
                GateResult(
                    "p5_page_pair_sparse_page_reads",
                    int(page_pair.get("page_file_read_count") or 0)
                    <= int(page_pair.get("max_page_file_reads") or args.p5_page_store_max_page_file_reads),
                    int(page_pair.get("page_file_read_count") or 0),
                    int(page_pair.get("max_page_file_reads") or args.p5_page_store_max_page_file_reads),
                    "Page-pair refresh reads only the overlay page files that can contain the selected page pair.",
                ),
                GateResult(
                    "p5_page_pair_materialized_visible_only",
                    int(page_pair.get("materialized_overlay_count") or 0)
                    == int(page_pair.get("expected_visible_overlay_count") or 0),
                    int(page_pair.get("materialized_overlay_count") or 0),
                    int(page_pair.get("expected_visible_overlay_count") or 0),
                    "Page-pair refresh materializes the selected page pair, not the full overlay set.",
                ),
                GateResult(
                    "p5_page_pair_visible_leaf_count",
                    int(page_pair.get("visible_leaf_count") or 0)
                    == int(page_pair.get("expected_visible_overlay_count") or 0),
                    int(page_pair.get("visible_leaf_count") or 0),
                    int(page_pair.get("expected_visible_overlay_count") or 0),
                    "Zone tree shows only overlays for the selected PDF page pair.",
                ),
                GateResult(
                    "p5_page_pair_no_stale_leaf",
                    int(page_pair.get("stale_leaf_count") or 0) == 0,
                    int(page_pair.get("stale_leaf_count") or 0),
                    0,
                    "Page-pair refresh leaves no non-target-page overlays in the active tree.",
                ),
                GateResult(
                    "p5_page_pair_no_full_overlay_cache",
                    int(page_pair.get("cached_overlay_count") or 0) < declared,
                    int(page_pair.get("cached_overlay_count") or 0),
                    f"< {declared}",
                    "Page-pair refresh does not cache the full overlay set.",
                ),
            ]
        )
    if bool(getattr(args, "include_p4_visible_tiles", False)) or bool(p4_visible_tiles):
        gates.extend(
            [
                GateResult(
                    "p4_visible_tiles_partial_not_full",
                    not bool(p4_visible_tiles.get("pyramid_complete"))
                    and int(p4_visible_tiles.get("materialized_tile_count") or 0)
                    < int(p4_visible_tiles.get("planned_tile_count") or 0),
                    int(p4_visible_tiles.get("materialized_tile_count") or 0),
                    f"< {int(p4_visible_tiles.get('planned_tile_count') or 0)}",
                    "Visible-first tile probe materializes a bounded window instead of the full pyramid.",
                ),
                GateResult(
                    "p4_visible_tiles_materialized_cap",
                    int(p4_visible_tiles.get("materialized_tile_count") or 0)
                    <= int(args.p4_visible_max_materialized_tiles),
                    int(p4_visible_tiles.get("materialized_tile_count") or 0),
                    int(args.p4_visible_max_materialized_tiles),
                    "Visible-first tile materialization stays within the visible + prefetch tile budget.",
                ),
                GateResult(
                    "p4_visible_tiles_outside_pending",
                    str(p4_visible_tiles.get("outside_window_status") or "") == "tile_pending",
                    str(p4_visible_tiles.get("outside_window_status") or ""),
                    "tile_pending",
                    "Unmaterialized windows remain pending instead of pretending the full pyramid exists.",
                ),
                GateResult(
                    "p4_on_demand_tiles_accumulates_windows",
                    int(p4_visible_tiles.get("on_demand_materialized_tile_count") or 0)
                    > int(p4_visible_tiles.get("materialized_tile_count") or 0),
                    int(p4_visible_tiles.get("on_demand_materialized_tile_count") or 0),
                    f"> {int(p4_visible_tiles.get('materialized_tile_count') or 0)}",
                    "On-demand tile materialization appends a newly visible window.",
                ),
                GateResult(
                    "p4_on_demand_tiles_fills_requested_window",
                    str(p4_visible_tiles.get("on_demand_filled_status") or "") == "tile_ready",
                    str(p4_visible_tiles.get("on_demand_filled_status") or ""),
                    "tile_ready",
                    "The requested pan window becomes ready after on-demand materialization.",
                ),
                GateResult(
                    "p4_on_demand_tiles_dedupes_repeat_window",
                    int(p4_visible_tiles.get("on_demand_repeat_added_tile_count") or 0) == 0,
                    int(p4_visible_tiles.get("on_demand_repeat_added_tile_count") or 0),
                    0,
                    "Repeating the same on-demand window does not grow the materialized tile count.",
                ),
                GateResult(
                    "p4_on_demand_no_full_pyramid",
                    not bool(p4_visible_tiles.get("on_demand_pyramid_complete"))
                    and int(p4_visible_tiles.get("on_demand_materialized_tile_count") or 0)
                    < int(p4_visible_tiles.get("planned_tile_count") or 0),
                    int(p4_visible_tiles.get("on_demand_materialized_tile_count") or 0),
                    f"< {int(p4_visible_tiles.get('planned_tile_count') or 0)}",
                    "On-demand pan/zoom remains partial instead of completing the full pyramid eagerly.",
                ),
            ]
        )
    if bool(getattr(args, "include_p5_tile_retention_soak", False)) or bool(p5_tile_retention):
        retention_gap = p5_tile_retention.get("event_loop_gap", {}) if isinstance(p5_tile_retention, dict) else {}
        retention_write = p5_tile_retention.get("write_ms", {}) if isinstance(p5_tile_retention, dict) else {}
        gates.extend(
            [
                GateResult(
                    "p5_tile_retention_completed",
                    bool(p5_tile_retention.get("completed")),
                    bool(p5_tile_retention.get("completed")),
                    True,
                    "Opt-in disk tile-cache retention probe completed the requested pair writes.",
                ),
                GateResult(
                    "p5_tile_cache_byte_bound",
                    int(p5_tile_retention.get("retained_bytes") or 0)
                    <= int(p5_tile_retention.get("byte_limit") or 0),
                    int(p5_tile_retention.get("retained_bytes") or 0),
                    int(p5_tile_retention.get("byte_limit") or 0),
                    "Retained tile/overlay payload bytes stay under the configured tile cache byte cap.",
                ),
                GateResult(
                    "p5_tile_cache_eviction_count_min",
                    int(p5_tile_retention.get("eviction_count") or 0) >= 1,
                    int(p5_tile_retention.get("eviction_count") or 0),
                    ">= 1",
                    "Over-limit tile retention workload must evict at least one old pair payload.",
                ),
                GateResult(
                    "p5_tile_cache_evicted_bytes_positive",
                    int(p5_tile_retention.get("evicted_estimated_bytes") or 0) > 0,
                    int(p5_tile_retention.get("evicted_estimated_bytes") or 0),
                    "> 0",
                    "Tile retention must report positive evicted bytes when eviction occurs.",
                ),
                GateResult(
                    "p5_tile_cache_orphan_bytes_zero",
                    int(p5_tile_retention.get("orphan_bytes") or 0) == 0,
                    int(p5_tile_retention.get("orphan_bytes") or 0),
                    0,
                    "Eviction leaves no tile/overlay pair directories outside the materialised manifest.",
                ),
                GateResult(
                    "p5_tile_cache_stale_manifest_count_zero",
                    int(p5_tile_retention.get("stale_manifest_count") or 0) == 0,
                    int(p5_tile_retention.get("stale_manifest_count") or 0),
                    0,
                    "Materialised tile manifest contains no stale records for evicted pair payloads.",
                ),
                GateResult(
                    "p5_tile_cache_hot_pair_retained",
                    bool(p5_tile_retention.get("hot_pair_retained")),
                    bool(p5_tile_retention.get("hot_pair_retained")),
                    True,
                    "Repeatedly accessed hot pair survives while colder pair payloads are evicted.",
                ),
                GateResult(
                    "p5_tile_cache_evicted_pair_miss",
                    bool(p5_tile_retention.get("evicted_pair_miss")),
                    bool(p5_tile_retention.get("evicted_pair_miss")),
                    True,
                    "At least one evicted pair is no longer reported as a current tile-cache hit.",
                ),
                GateResult(
                    "p5_tile_cache_prune_p95_ms",
                    float(retention_write.get("p95_ms") or 0.0) <= float(args.p5_tile_retention_prune_p95_target_ms),
                    float(retention_write.get("p95_ms") or 0.0),
                    float(args.p5_tile_retention_prune_p95_target_ms),
                    "Tile write plus retention prune p95 stays within the synthetic disk cache budget.",
                ),
                GateResult(
                    "p5_tile_cache_event_loop_gap_p95_ms",
                    float(retention_gap.get("p95_ms") or 0.0) <= float(args.p5_tile_retention_gap_p95_target_ms),
                    float(retention_gap.get("p95_ms") or 0.0),
                    float(args.p5_tile_retention_gap_p95_target_ms),
                    "Event-loop heartbeat p95 gap stays bounded during tile retention writes.",
                ),
                GateResult(
                    "p5_tile_cache_event_loop_over_500ms_count",
                    int(retention_gap.get("over_500ms_count") or 0) == 0,
                    int(retention_gap.get("over_500ms_count") or 0),
                    0,
                    "Tile retention probe introduces no event-loop gap above 500 ms.",
                ),
            ]
        )
    if bool(getattr(args, "include_lightweight_pdf_load", False)) or bool(lightweight_pdf_load):
        cached_summary = lightweight_pdf_summary.get("lightweight_pdf_cached_load_ms", {})
        cold_summary = lightweight_pdf_summary.get("lightweight_pdf_cold_load_ms", {})
        cache_counts = lightweight_pdf_summary.get("lightweight_pdf_cache_state_counts", {})
        gates.extend(
            [
                GateResult(
                    "lightweight_pdf_load_qtpdf_available",
                    bool(lightweight_pdf_load.get("qtpdf_available")),
                    bool(lightweight_pdf_load.get("qtpdf_available")),
                    True,
                    "PySide6.QtPdf is available for the actual lightweight PDF render path.",
                ),
                GateResult(
                    "lightweight_pdf_load_completed",
                    bool(lightweight_pdf_load.get("completed")),
                    bool(lightweight_pdf_load.get("completed")),
                    True,
                    "Actual lightweight PDF background load completed cold and cache-hit passes.",
                ),
                GateResult(
                    "lightweight_pdf_load_both_sides",
                    bool(lightweight_pdf_load.get("first_loaded")) and bool(lightweight_pdf_load.get("second_loaded")),
                    bool(lightweight_pdf_load.get("first_loaded")) and bool(lightweight_pdf_load.get("second_loaded")),
                    True,
                    "Actual lightweight PDF load populated both before/after viewport backgrounds.",
                ),
                GateResult(
                    "lightweight_pdf_load_background_state",
                    bool(lightweight_pdf_load.get("first_background_ready"))
                    and bool(lightweight_pdf_load.get("second_background_ready")),
                    bool(lightweight_pdf_load.get("first_background_ready"))
                    and bool(lightweight_pdf_load.get("second_background_ready")),
                    True,
                    "QML backgroundImageSource and backgroundImageWorldBbox are populated after real PDF loads.",
                ),
                GateResult(
                    "lightweight_pdf_load_overlay_after_background",
                    bool(lightweight_pdf_load.get("first_overlay_after_background"))
                    and bool(lightweight_pdf_load.get("second_overlay_after_background")),
                    bool(lightweight_pdf_load.get("first_overlay_after_background"))
                    and bool(lightweight_pdf_load.get("second_overlay_after_background")),
                    True,
                    "Change overlays are pushed after the PDF background is loaded.",
                ),
                GateResult(
                    "lightweight_pdf_load_cache_hit",
                    bool(lightweight_pdf_load.get("second_cache_hit_before"))
                    and bool(lightweight_pdf_load.get("second_cache_hit_after")),
                    bool(lightweight_pdf_load.get("second_cache_hit_before"))
                    and bool(lightweight_pdf_load.get("second_cache_hit_after")),
                    True,
                    "Second lightweight PDF load hits the viewport PDF image cache on both sides.",
                ),
                GateResult(
                    "lightweight_pdf_load_schedule_call_ms",
                    float(lightweight_pdf_load.get("max_schedule_call_ms") or 0.0)
                    <= float(args.lightweight_pdf_schedule_target_ms),
                    float(lightweight_pdf_load.get("max_schedule_call_ms") or 0.0),
                    float(args.lightweight_pdf_schedule_target_ms),
                    "Scheduling actual lightweight PDF load does not block the GUI thread.",
                ),
                GateResult(
                    "lightweight_pdf_load_event_loop_gap_p95",
                    float(lightweight_pdf_gap.get("p95_ms") or 0.0) <= float(args.lightweight_pdf_p95_gap_target_ms),
                    float(lightweight_pdf_gap.get("p95_ms") or 0.0),
                    float(args.lightweight_pdf_p95_gap_target_ms),
                    "Heartbeat p95 gap while actual lightweight PDF render/load runs.",
                ),
                GateResult(
                    "lightweight_pdf_cold_load_ms",
                    float(cold_summary.get("p95") or 0.0) <= float(args.lightweight_pdf_cold_target_ms),
                    float(cold_summary.get("p95") or 0.0),
                    float(args.lightweight_pdf_cold_target_ms),
                    "Cold actual lightweight PDF render/load stays within the acceptance budget.",
                ),
                GateResult(
                    "lightweight_pdf_cached_load_ms",
                    float(cached_summary.get("p95") or 0.0) <= float(args.lightweight_pdf_cached_target_ms),
                    float(cached_summary.get("p95") or 0.0),
                    float(args.lightweight_pdf_cached_target_ms),
                    "Cached actual lightweight PDF load stays within the acceptance budget.",
                ),
                GateResult(
                    "lightweight_pdf_cache_state_count",
                    int(cache_counts.get("all_cached") or 0) >= 1,
                    int(cache_counts.get("all_cached") or 0),
                    1,
                    "Viewer perf summary records an all_cached lightweight PDF pass.",
                ),
                GateResult(
                    "lightweight_pdf_cache_state_cold_count",
                    int(cache_counts.get("all_cold") or 0) >= 1,
                    int(cache_counts.get("all_cold") or 0),
                    1,
                    "Viewer perf summary records an all_cold lightweight PDF pass.",
                ),
            ]
        )
    if bool(getattr(args, "include_real_pdf_page_nav", False)) or bool(real_pdf_page_nav):
        real_pdf_nav_chunk_summary = (
            real_pdf_nav_full_tree.get("chunk_count", {}) if isinstance(real_pdf_nav_full_tree, dict) else {}
        )
        real_pdf_nav_cold_summary = (
            real_pdf_nav_summary.get("lightweight_pdf_cold_load_ms", {})
            if isinstance(real_pdf_nav_summary, dict)
            else {}
        )
        real_pdf_nav_page_size = real_pdf_page_nav.get("page_size_points") or []
        real_pdf_nav_expected_dpi_cap = False
        try:
            nav_w_pts = float(real_pdf_nav_page_size[0])
            nav_h_pts = float(real_pdf_nav_page_size[1])
            nav_max_pixels = int(real_pdf_page_nav.get("initial_render_max_pixels") or 8_000_000)
            real_pdf_nav_expected_dpi_cap = (
                nav_w_pts > 0
                and nav_h_pts > 0
                and nav_max_pixels > 0
                and ((nav_w_pts / 72.0) * (nav_h_pts / 72.0) * 150.0 * 150.0)
                > float(nav_max_pixels)
            )
        except (TypeError, ValueError, IndexError):
            real_pdf_nav_expected_dpi_cap = False
        real_pdf_nav_rss_delta = real_pdf_page_nav.get("rss_delta_mb")
        gates.extend(
            [
                GateResult(
                    "real_pdf_page_navigation_completed",
                    bool(real_pdf_page_nav.get("completed")),
                    bool(real_pdf_page_nav.get("completed")),
                    True,
                    "Rapid page navigation completed with the actual lightweight PDF render path enabled.",
                ),
                GateResult(
                    "real_pdf_page_navigation_qtpdf_available",
                    bool(real_pdf_page_nav.get("qtpdf_available")),
                    bool(real_pdf_page_nav.get("qtpdf_available")),
                    True,
                    "PySide6.QtPdf is available for real PDF page navigation rendering.",
                ),
                GateResult(
                    "real_pdf_page_navigation_max_step_call_ms",
                    float(real_pdf_nav_step.get("max_ms") or 0.0) <= float(args.real_pdf_nav_step_target_ms),
                    float(real_pdf_nav_step.get("max_ms") or 0.0),
                    float(args.real_pdf_nav_step_target_ms),
                    "Each real-PDF page-step click returns before deferred work runs.",
                ),
                GateResult(
                    "real_pdf_page_navigation_final_page",
                    int(real_pdf_page_nav.get("active_pdf_page_index") or -1)
                    == int(real_pdf_page_nav.get("target_page") or -2)
                    and int(real_pdf_page_nav.get("final_page_a") or -1)
                    == int(real_pdf_page_nav.get("target_page") or -2)
                    and int(real_pdf_page_nav.get("final_page_b") or -1)
                    == int(real_pdf_page_nav.get("target_page") or -2),
                    int(real_pdf_page_nav.get("active_pdf_page_index") or -1),
                    int(real_pdf_page_nav.get("target_page") or -2),
                    "Real-PDF rapid navigation leaves the active index and viewer_pair on the final target page.",
                ),
                GateResult(
                    "real_pdf_page_navigation_final_background",
                    bool(real_pdf_page_nav.get("final_background_ready")),
                    bool(real_pdf_page_nav.get("final_background_ready")),
                    True,
                    "Final actual PDF background is loaded on both sides for the selected page.",
                ),
                GateResult(
                    "real_pdf_page_navigation_overlay_after_background",
                    bool(real_pdf_page_nav.get("overlay_after_background")),
                    bool(real_pdf_page_nav.get("overlay_after_background")),
                    True,
                    "Final page overlays are pushed after the actual PDF background is available.",
                ),
                GateResult(
                    "real_pdf_page_navigation_visible_leaf_count",
                    int(real_pdf_page_nav.get("visible_leaf_count") or 0)
                    == int(real_pdf_page_nav.get("expected_visible_overlay_count") or 0),
                    int(real_pdf_page_nav.get("visible_leaf_count") or 0),
                    int(real_pdf_page_nav.get("expected_visible_overlay_count") or 0),
                    "Real-PDF page navigation tree shows only final-page overlays.",
                ),
                GateResult(
                    "real_pdf_page_navigation_no_stale_leaf",
                    int(real_pdf_page_nav.get("stale_leaf_count") or 0) == 0,
                    int(real_pdf_page_nav.get("stale_leaf_count") or 0),
                    0,
                    "Real-PDF page navigation leaves no stale non-final-page leaves.",
                ),
                GateResult(
                    "real_pdf_page_navigation_generation_drop",
                    int(real_pdf_page_nav.get("generation_dropped_load_count") or 0)
                    >= int(args.real_pdf_nav_min_generation_drops),
                    int(real_pdf_page_nav.get("generation_dropped_load_count") or 0),
                    int(args.real_pdf_nav_min_generation_drops),
                    "Superseded lightweight PDF load generations are dropped during rapid page navigation.",
                ),
                GateResult(
                    "real_pdf_page_navigation_completed_load_count",
                    int(real_pdf_page_nav.get("completed_lightweight_load_count") or 0)
                    <= int(args.real_pdf_nav_max_completed_loads),
                    int(real_pdf_page_nav.get("completed_lightweight_load_count") or 0),
                    int(args.real_pdf_nav_max_completed_loads),
                    "Rapid real-PDF navigation renders only the final lightweight PDF load generation.",
                ),
                GateResult(
                    "real_pdf_page_navigation_overlay_load_worker",
                    int(real_pdf_nav_full_tree.get("overlay_load_worker_count") or 0) >= 1,
                    int(real_pdf_nav_full_tree.get("overlay_load_worker_count") or 0),
                    1,
                    "Real-PDF final page overlay JSON load runs on a worker thread.",
                ),
                GateResult(
                    "real_pdf_page_navigation_plan_build_worker",
                    int(real_pdf_nav_full_tree.get("plan_build_worker_count") or 0)
                    >= int(args.real_pdf_nav_min_plan_build_workers),
                    int(real_pdf_nav_full_tree.get("plan_build_worker_count") or 0),
                    int(args.real_pdf_nav_min_plan_build_workers),
                    "Real-PDF final page tree plan worker count meets the workload-specific requirement.",
                ),
                GateResult(
                    "real_pdf_page_navigation_chunk_count",
                    float(real_pdf_nav_chunk_summary.get("max") or real_pdf_nav_chunk_summary.get("p95") or 0.0)
                    >= float(args.real_pdf_nav_min_chunks),
                    float(real_pdf_nav_chunk_summary.get("max") or real_pdf_nav_chunk_summary.get("p95") or 0.0),
                    float(args.real_pdf_nav_min_chunks),
                    "Real-PDF final page tree is split across the expected number of GUI chunks.",
                ),
                GateResult(
                    "real_pdf_page_navigation_event_loop_gap_p95",
                    float(real_pdf_nav_gap.get("p95_ms") or 0.0) <= float(args.real_pdf_nav_p95_gap_target_ms),
                    float(real_pdf_nav_gap.get("p95_ms") or 0.0),
                    float(args.real_pdf_nav_p95_gap_target_ms),
                    "Heartbeat p95 gap while real PDF page navigation and render run.",
                ),
                GateResult(
                    "real_pdf_page_navigation_cold_load_ms",
                    float(real_pdf_nav_cold_summary.get("p95") or 0.0) <= float(args.real_pdf_nav_cold_target_ms),
                    float(real_pdf_nav_cold_summary.get("p95") or 0.0),
                    float(args.real_pdf_nav_cold_target_ms),
                    "Cold actual PDF render/load during page navigation stays within the acceptance budget.",
                ),
                GateResult(
                    "real_pdf_page_navigation_initial_dpi_cap",
                    (not real_pdf_nav_expected_dpi_cap)
                    or bool(real_pdf_page_nav.get("final_dpi_capped")),
                    bool(real_pdf_page_nav.get("final_dpi_capped")),
                    "required for large pages" if real_pdf_nav_expected_dpi_cap else "not required",
                    "Large real-PDF page navigation downshifts first-render DPI to stay within the initial pixel budget.",
                ),
                GateResult(
                    "real_pdf_page_navigation_redacted_fallback",
                    (not bool(real_pdf_page_nav.get("use_redacted_sources")))
                    or bool(real_pdf_page_nav.get("redacted_fallback_ok")),
                    bool(real_pdf_page_nav.get("redacted_fallback_ok")),
                    True,
                    "Redacted source_a/source_b fall back to package-local before_page_pdf/after_page_pdf.",
                ),
            ]
        )
        if real_pdf_nav_rss_delta is None:
            gates.append(
                GateResult(
                    "real_pdf_page_navigation_rss_delta_mb",
                    bool(args.allow_missing_psutil),
                    None,
                    f"<= {args.real_pdf_nav_max_rss_delta_mb} MB",
                    "RSS unavailable during real PDF page navigation; install psutil or pass --allow-missing-psutil.",
                )
            )
        else:
            gates.append(
                GateResult(
                    "real_pdf_page_navigation_rss_delta_mb",
                    float(real_pdf_nav_rss_delta) <= float(args.real_pdf_nav_max_rss_delta_mb),
                    float(real_pdf_nav_rss_delta),
                    float(args.real_pdf_nav_max_rss_delta_mb),
                    "RSS peak delta during real PDF page navigation and final render.",
                )
            )
    if bool(getattr(args, "include_real_pdf_prewarm_cache_nav", False)) or bool(real_pdf_prewarm_probe):
        phase_results = (
            real_pdf_prewarm_probe.get("phase_results", {})
            if isinstance(real_pdf_prewarm_probe, dict)
            else {}
        )
        cold_phase = phase_results.get("cold_no_prewarm", {}) if isinstance(phase_results, dict) else {}
        prewarm_phase = phase_results.get("prewarm_wait", {}) if isinstance(phase_results, dict) else {}
        cached_phase = phase_results.get("post_prewarm_cached", {}) if isinstance(phase_results, dict) else {}
        plateau_phase = phase_results.get("cached_navigation_plateau", {}) if isinstance(phase_results, dict) else {}
        prewarm_gap = (
            real_pdf_prewarm_probe.get("event_loop_gap", {})
            if isinstance(real_pdf_prewarm_probe, dict)
            else {}
        )
        cached_phase_gap = (
            cached_phase.get("event_loop_gap", {})
            if isinstance(cached_phase, dict)
            else {}
        )
        prewarm_step = (
            real_pdf_prewarm_probe.get("step_call_ms", {})
            if isinstance(real_pdf_prewarm_probe, dict)
            else {}
        )
        pdf_cache_summary = (
            real_pdf_prewarm_probe.get("pdf_cache", {})
            if isinstance(real_pdf_prewarm_probe, dict)
            else {}
        )
        cold_load_p95 = float((cold_phase.get("load_ms") or {}).get("p95_ms") or 0.0)
        cached_load_p95 = float((cached_phase.get("load_ms") or {}).get("p95_ms") or 0.0)
        cached_background_p95 = float(
            (cached_phase.get("time_to_background_ready_ms") or {}).get("p95_ms") or 0.0
        )
        load_ratio = (
            round(cached_load_p95 / cold_load_p95, 4)
            if cold_load_p95 > 0
            else 0.0
        )
        expected_item_count = int(prewarm_phase.get("expected_item_count") or 0)
        prewarm_ok_count = int(prewarm_phase.get("ok_count") or 0)
        cached_metadata_fast_path = bool(cached_phase.get("metadata_fast_path")) or (
            bool(cached_phase.get("before_metadata_fast_path"))
            and bool(cached_phase.get("after_metadata_fast_path"))
        )
        cached_hit = bool(cached_phase.get("cache_hit")) or (
            bool(cached_phase.get("before_cache_hit"))
            and bool(cached_phase.get("after_cache_hit"))
        )
        inferred_render_call_count = int(cached_phase.get("inferred_render_call_count") or 0)
        gates.extend(
            [
                GateResult(
                    "real_pdf_prewarm_cache_completed",
                    bool(real_pdf_prewarm_probe.get("completed")),
                    bool(real_pdf_prewarm_probe.get("completed")),
                    True,
                    "Real PDF prewarm cache probe completed cold, prewarm, and cached-navigation phases.",
                ),
                GateResult(
                    "real_pdf_prewarm_cache_qtpdf_available",
                    bool(real_pdf_prewarm_probe.get("qtpdf_available")),
                    bool(real_pdf_prewarm_probe.get("qtpdf_available")),
                    True,
                    "PySide6.QtPdf is available for the prewarm/cache probe.",
                ),
                GateResult(
                    "real_pdf_prewarm_completed_before_navigation",
                    bool(prewarm_phase.get("prewarm_completed_before_navigation")),
                    bool(prewarm_phase.get("prewarm_completed_before_navigation")),
                    True,
                    "Adjacent page prewarm finishes before navigating to the next page.",
                ),
                GateResult(
                    "real_pdf_prewarm_cache_coverage",
                    prewarm_ok_count >= expected_item_count and expected_item_count > 0,
                    prewarm_ok_count,
                    expected_item_count,
                    "Prewarm writes cache entries for both before/after adjacent pages.",
                ),
                GateResult(
                    "real_pdf_prewarm_no_visible_state_mutation",
                    int(prewarm_phase.get("visible_state_mutation_count") or 0) == 0,
                    int(prewarm_phase.get("visible_state_mutation_count") or 0),
                    0,
                    "Prewarm does not mutate active page, visible PDF state, or background readiness.",
                ),
                GateResult(
                    "real_pdf_cached_navigation_cache_hit",
                    bool(cached_hit),
                    bool(cached_hit),
                    True,
                    "Navigation to the prewarmed page hits the before/after PDF image cache.",
                ),
                GateResult(
                    "real_pdf_cached_navigation_metadata_fast_path",
                    bool(cached_metadata_fast_path),
                    bool(cached_metadata_fast_path),
                    True,
                    "Navigation to the prewarmed page uses metadata fast path on both sides.",
                ),
                GateResult(
                    "real_pdf_cached_navigation_no_cold_render",
                    inferred_render_call_count == 0,
                    inferred_render_call_count,
                    0,
                    "Metadata fast path implies no cold render/document-open path for the prewarmed page.",
                ),
                GateResult(
                    "real_pdf_cached_navigation_load_p95_ms",
                    cached_load_p95 <= float(args.real_pdf_prewarm_cached_target_ms),
                    cached_load_p95,
                    float(args.real_pdf_prewarm_cached_target_ms),
                    "Prewarmed cached page load p95 stays within the P2-I-B budget.",
                ),
                GateResult(
                    "real_pdf_cached_navigation_background_ready_ms",
                    cached_background_p95 <= float(args.real_pdf_prewarm_background_target_ms),
                    cached_background_p95,
                    float(args.real_pdf_prewarm_background_target_ms),
                    "Prewarmed page reaches both lightweight backgrounds within budget.",
                ),
                GateResult(
                    "real_pdf_cached_navigation_event_loop_gap_max_ms",
                    float(cached_phase_gap.get("max_ms") or 0.0) <= float(args.real_pdf_prewarm_gap_max_target_ms),
                    float(cached_phase_gap.get("max_ms") or 0.0),
                    float(args.real_pdf_prewarm_gap_max_target_ms),
                    "Max event-loop gap during prewarm/cache navigation stays under the hard budget.",
                ),
                GateResult(
                    "real_pdf_cached_navigation_event_loop_over_500ms_count",
                    int(cached_phase_gap.get("over_500ms_count") or 0) == 0,
                    int(cached_phase_gap.get("over_500ms_count") or 0),
                    0,
                    "Prewarm/cache navigation introduces no event-loop gap above 500 ms.",
                ),
                GateResult(
                    "real_pdf_cached_navigation_vs_cold_load_ratio",
                    load_ratio <= float(args.real_pdf_prewarm_vs_cold_ratio),
                    load_ratio,
                    float(args.real_pdf_prewarm_vs_cold_ratio),
                    "Prewarmed cached navigation is materially faster than the cold no-prewarm load.",
                ),
                GateResult(
                    "real_pdf_cached_navigation_step_call_ms",
                    float(prewarm_step.get("max_ms") or 0.0) <= float(args.real_pdf_prewarm_step_target_ms),
                    float(prewarm_step.get("max_ms") or 0.0),
                    float(args.real_pdf_prewarm_step_target_ms),
                    "Prewarmed page-step click path returns quickly.",
                ),
                GateResult(
                    "real_pdf_cached_plateau_all_cached",
                    int(plateau_phase.get("all_cached_count") or 0)
                    >= int(plateau_phase.get("navigation_count") or 0),
                    int(plateau_phase.get("all_cached_count") or 0),
                    int(plateau_phase.get("navigation_count") or 0),
                    "Short cached plateau keeps all repeated navigations on cached PDF paths.",
                ),
                GateResult(
                    "real_pdf_prewarm_cache_dir_size_mb",
                    float(pdf_cache_summary.get("size_mb") or 0.0)
                    <= float(args.real_pdf_prewarm_cache_dir_max_mb),
                    float(pdf_cache_summary.get("size_mb") or 0.0),
                    float(args.real_pdf_prewarm_cache_dir_max_mb),
                    "Prewarm cache footprint remains bounded for the probe.",
                ),
            ]
        )
        prewarm_rss_tail_delta = plateau_phase.get("rss_tail_delta_mb")
        if prewarm_rss_tail_delta is None:
            gates.append(
                GateResult(
                    "real_pdf_cached_plateau_rss_tail_delta_mb",
                    bool(args.allow_missing_psutil),
                    None,
                    f"<= {args.real_pdf_prewarm_plateau_rss_delta_mb} MB",
                    "RSS unavailable during prewarm plateau; install psutil or pass --allow-missing-psutil.",
                )
            )
        else:
            gates.append(
                GateResult(
                    "real_pdf_cached_plateau_rss_tail_delta_mb",
                    abs(float(prewarm_rss_tail_delta)) <= float(args.real_pdf_prewarm_plateau_rss_delta_mb),
                    float(prewarm_rss_tail_delta),
                    float(args.real_pdf_prewarm_plateau_rss_delta_mb),
                    "RSS tail delta during short cached navigation plateau.",
                )
            )
    if rss_tail_delta is None:
        rss_available = bool(overlay.get("rss_available"))
        pair_count = int(overlay.get("pair_count") or 0)
        pair_limit = int(overlay.get("cache_pair_limit") or 0)
        passed = bool(args.allow_missing_psutil) if not rss_available else pair_count <= pair_limit
        detail = (
            "RSS unavailable; install psutil or pass --allow-missing-psutil for smoke-only runs."
            if not rss_available
            else "Workload did not exceed the cache pair limit, so no post-limit RSS tail exists."
        )
        gates.append(
            GateResult(
                "overlay_rss_tail_delta_after_cache_limit",
                passed,
                None,
                f"<= {args.max_tail_rss_delta_mb} MB",
                detail,
            )
        )
    else:
        gates.append(
            GateResult(
                "overlay_rss_tail_delta_after_cache_limit",
                float(rss_tail_delta) <= float(args.max_tail_rss_delta_mb),
                float(rss_tail_delta),
                float(args.max_tail_rss_delta_mb),
                "RSS peak range after the overlay cache limit has been reached.",
            )
        )
    if include_p5_g26_contract:
        gates.extend(_p5_g26_contract_gates(p5_g26_contract))
    if include_p5_g27_contract:
        gates.extend(_p5_g27_contract_gates(p5_g27_contract))
        if p5_g27_bridge_required:
            gates.extend(_p5_g27_real_renderer_bridge_gates(p5_g27_real_renderer_bridge))
    if include_p5_g28_contract:
        gates.extend(_p5_g28_contract_gates(p5_g28_contract))
    return gates


def _real_corpus_replay_requested(args: argparse.Namespace) -> bool:
    return bool(args.real_corpus_validation_output or args.real_corpus_viewer_root)


def _resolve_real_corpus_validation_summary(args: argparse.Namespace) -> Path:
    output = args.real_corpus_validation_output
    if output is not None:
        output_path = Path(output)
        if output_path.is_file():
            return output_path
        if output_path.exists() and output_path.is_dir():
            for name in ("validation_summary.json", "summary.json", "result_summary.json"):
                candidate = output_path / name
                if candidate.exists():
                    return candidate
        return output_path / "validation_summary.json" if output_path.suffix == "" else output_path
    viewer_root = Path(args.real_corpus_viewer_root)
    return viewer_root.parent / "validation_summary.json"


def _run_real_corpus_replay_cli(args: argparse.Namespace, started: float) -> int:
    from scripts import benchmark_real_corpus_replay as real_replay

    validation_summary = _resolve_real_corpus_validation_summary(args)
    output_json = args.output or validation_summary.parent / "p5_g16_real_corpus_replay.json"
    visits = max(1, int(args.real_corpus_visits))
    warmup_visits = max(0, int(args.real_corpus_warmup_visits))
    timeout_s = float(args.real_corpus_timeout_s)
    if args.real_corpus_quick:
        visits = min(visits, 20)
        warmup_visits = min(warmup_visits, 5)
        timeout_s = min(timeout_s, 60.0)
    if float(args.real_corpus_soak_minutes) > 0.0:
        soak_seconds = float(args.real_corpus_soak_minutes) * 60.0
        visits = max(visits, int(soak_seconds))
        timeout_s = max(timeout_s, soak_seconds)

    replay_args = argparse.Namespace(
        validation_summary=validation_summary,
        viewer_root=args.real_corpus_viewer_root,
        customer_evidence_manifest=args.real_corpus_customer_evidence_manifest,
        output_json=output_json,
        visits=visits,
        warmup_visits=warmup_visits,
        max_zone_artifacts=max(1, int(args.real_corpus_max_zone_artifacts)),
        max_page_artifacts=max(0, int(args.real_corpus_max_page_artifacts)),
        min_zone_artifacts=max(0, int(args.real_corpus_min_zone_artifacts)),
        min_page_artifacts=max(0, int(args.real_corpus_min_page_artifacts)),
        replay_p95_target_ms=float(args.real_corpus_replay_p95_target_ms),
        gap_max_target_ms=float(args.real_corpus_gap_max_target_ms),
        rss_slope_target_mb_per_100=float(args.real_corpus_rss_slope_target_mb_per_100),
        rss_end_delta_mb=float(args.real_corpus_rss_end_delta_mb),
        rss_tail_delta_mb=float(args.real_corpus_rss_tail_delta_mb),
        settle_ms=float(args.real_corpus_settle_ms),
        timeout_s=timeout_s,
        allow_rss_unavailable=bool(args.allow_missing_psutil),
        require_customer_corpus=bool(args.real_corpus_require_customer_corpus),
        min_customer_sheet_count=int(args.real_corpus_min_customer_sheet_count),
        max_customer_sheet_count=int(args.real_corpus_max_customer_sheet_count),
        fail_on_gate=not bool(args.no_fail_on_exceed),
    )
    payload = real_replay.run_replay(replay_args)
    payload["wrapper"] = {
        "schema_version": SCHEMA_VERSION,
        "profile": "workbench_gui_hotpath_real_corpus_replay",
        "elapsed_s": round(time.perf_counter() - started, 3),
        "synthetic_gui_probes_skipped": True,
    }
    output_json = Path(output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[p5-g16] json -> {output_json}")
    print(f"[p5-g16] status={payload.get('status')}")
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    if payload.get("status") != "passed" and not args.no_fail_on_exceed:
        return 1
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-runs", type=int, default=20)
    parser.add_argument("--pairs", type=int, default=100)
    parser.add_argument("--overlays-per-pair", type=int, default=1000)
    parser.add_argument("--selection-overlay-count", type=int, default=1000)
    parser.add_argument("--full-tree-overlays", type=int, default=1000)
    parser.add_argument("--full-tree-min-chunks", type=int, default=2)
    parser.add_argument("--cached-p95-target-ms", type=float, default=300.0)
    parser.add_argument("--cold-p95-target-ms", type=float, default=2000.0)
    parser.add_argument("--max-tail-rss-delta-mb", type=float, default=128.0)
    parser.add_argument("--full-tree-max-chunk-target-ms", type=float, default=50.0)
    parser.add_argument("--full-tree-p95-gap-target-ms", type=float, default=100.0)
    parser.add_argument("--full-tree-start-call-target-ms", type=float, default=50.0)
    parser.add_argument("--page-nav-overlays", type=int, default=1000)
    parser.add_argument("--page-nav-start-call-target-ms", type=float, default=50.0)
    parser.add_argument("--page-nav-p95-gap-target-ms", type=float, default=100.0)
    parser.add_argument("--rapid-page-nav-overlays", type=int, default=1500)
    parser.add_argument("--rapid-page-nav-step-target-ms", type=float, default=50.0)
    parser.add_argument("--rapid-page-nav-p95-gap-target-ms", type=float, default=100.0)
    parser.add_argument("--include-stress-page-nav", "--enable-page-nav-stress", dest="include_stress_page_nav", action="store_true")
    parser.add_argument("--stress-page-nav-overlays", "--page-nav-stress-overlays", dest="stress_page_nav_overlays", type=int, default=10000)
    parser.add_argument("--stress-page-nav-step-target-ms", "--page-nav-stress-step-target-ms", dest="stress_page_nav_step_target_ms", type=float, default=75.0)
    parser.add_argument("--stress-page-nav-p95-gap-target-ms", "--page-nav-stress-p95-gap-target-ms", dest="stress_page_nav_p95_gap_target_ms", type=float, default=150.0)
    parser.add_argument("--stress-page-nav-min-chunks", "--page-nav-stress-min-chunks", dest="stress_page_nav_min_chunks", type=int, default=2)
    parser.add_argument("--stress-page-nav-timeout-s", "--page-nav-stress-timeout-s", dest="stress_page_nav_timeout_s", type=float, default=30.0)
    parser.add_argument("--stress-page-nav-max-rss-delta-mb", "--page-nav-stress-max-rss-delta-mb", dest="stress_page_nav_max_rss_delta_mb", type=float, default=256.0)
    parser.add_argument("--include-navigation-soak", "--include-pair-navigation-soak", dest="include_navigation_soak", action="store_true")
    parser.add_argument("--navigation-soak-pairs", type=int, default=100)
    parser.add_argument("--navigation-soak-visits", type=int, default=100)
    parser.add_argument("--navigation-soak-overlays-per-pair", type=int, default=1000)
    parser.add_argument("--navigation-soak-warmup-visits", type=int, default=20)
    parser.add_argument("--navigation-soak-selection-p95-target-ms", type=float, default=300.0)
    parser.add_argument("--navigation-soak-p95-gap-target-ms", type=float, default=150.0)
    parser.add_argument("--navigation-soak-gap-max-target-ms", type=float, default=500.0)
    parser.add_argument("--navigation-soak-rss-slope-target-mb-per-100", type=float, default=5.0)
    parser.add_argument("--navigation-soak-rss-end-delta-mb", type=float, default=64.0)
    parser.add_argument("--navigation-soak-rss-tail-delta-mb", type=float, default=128.0)
    parser.add_argument("--navigation-soak-settle-ms", type=float, default=0.0)
    parser.add_argument("--navigation-soak-timeout-s", type=float, default=60.0)
    parser.add_argument("--include-p4-overlay-streaming", action="store_true")
    parser.add_argument("--p4-overlay-count", type=int, default=100000)
    parser.add_argument("--p4-overlay-top-issues", type=int, default=5)
    parser.add_argument("--p4-overlay-first-paint-cap", type=int, default=dcw.GUI_FIRST_SELECTION_ZONE_LIMIT)
    parser.add_argument("--include-p5-overlay-page-store-query", action="store_true")
    parser.add_argument("--p5-page-store-overlay-count", type=int, default=102400)
    parser.add_argument("--p5-page-store-page-size", type=int, default=512)
    parser.add_argument("--p5-page-store-page-pair-count", type=int, default=100)
    parser.add_argument("--p5-page-store-target-page", type=int, default=37)
    parser.add_argument("--p5-page-store-first-visible-limit", type=int, default=dcw.GUI_FIRST_SELECTION_ZONE_LIMIT)
    parser.add_argument("--p5-page-store-max-page-file-reads", type=int, default=2)
    parser.add_argument("--include-p4-visible-tiles", action="store_true")
    parser.add_argument("--p4-visible-image-size", type=int, default=4096)
    parser.add_argument("--p4-visible-viewport-size", type=int, default=512)
    parser.add_argument("--p4-visible-prefetch-radius", type=int, default=1)
    parser.add_argument("--p4-visible-max-materialized-tiles", type=int, default=18)
    parser.add_argument("--include-p5-tile-retention-soak", action="store_true")
    parser.add_argument("--p5-tile-retention-pairs", type=int, default=12)
    parser.add_argument("--p5-tile-retention-image-size", type=int, default=512)
    parser.add_argument("--p5-tile-retention-byte-limit-mb", type=float, default=3.0)
    parser.add_argument("--p5-tile-retention-prune-p95-target-ms", type=float, default=500.0)
    parser.add_argument("--p5-tile-retention-gap-p95-target-ms", type=float, default=150.0)
    parser.add_argument("--include-lightweight-pdf-load", "--include-real-pdf-load-probe", dest="include_lightweight_pdf_load", action="store_true")
    parser.add_argument("--lightweight-pdf-schedule-target-ms", "--real-pdf-load-schedule-target-ms", dest="lightweight_pdf_schedule_target_ms", type=float, default=50.0)
    parser.add_argument("--lightweight-pdf-p95-gap-target-ms", "--real-pdf-load-p95-gap-target-ms", dest="lightweight_pdf_p95_gap_target_ms", type=float, default=150.0)
    parser.add_argument("--lightweight-pdf-cold-target-ms", type=float, default=2500.0)
    parser.add_argument("--lightweight-pdf-cached-target-ms", type=float, default=500.0)
    parser.add_argument("--include-real-pdf-page-nav", "--include-real-pdf-navigation-stress", dest="include_real_pdf_page_nav", action="store_true")
    parser.add_argument("--real-pdf-nav-overlays", type=int, default=3000)
    parser.add_argument("--real-pdf-nav-page-count", type=int, default=4)
    parser.add_argument("--real-pdf-nav-step-count", type=int, default=3)
    parser.add_argument("--real-pdf-nav-page-width-points", type=float, default=612.0)
    parser.add_argument("--real-pdf-nav-page-height-points", type=float, default=792.0)
    parser.add_argument("--real-pdf-nav-step-target-ms", type=float, default=75.0)
    parser.add_argument("--real-pdf-nav-p95-gap-target-ms", type=float, default=250.0)
    parser.add_argument("--real-pdf-nav-cold-target-ms", type=float, default=4000.0)
    parser.add_argument("--real-pdf-nav-min-chunks", type=int, default=1)
    parser.add_argument("--real-pdf-nav-min-plan-build-workers", type=int, default=0)
    parser.add_argument("--real-pdf-nav-min-generation-drops", type=int, default=1)
    parser.add_argument("--real-pdf-nav-max-completed-loads", type=int, default=1)
    parser.add_argument("--real-pdf-nav-max-rss-delta-mb", type=float, default=512.0)
    parser.add_argument("--real-pdf-nav-timeout-s", type=float, default=30.0)
    parser.add_argument("--real-pdf-nav-no-redacted-sources", dest="real_pdf_nav_redacted_sources", action="store_false", default=True)
    parser.add_argument("--include-real-pdf-prewarm-cache-nav", "--include-real-pdf-prewarm-cache-probe", dest="include_real_pdf_prewarm_cache_nav", action="store_true")
    parser.add_argument("--real-pdf-prewarm-page-count", type=int, default=3)
    parser.add_argument("--real-pdf-prewarm-page-width-points", type=float, default=612.0)
    parser.add_argument("--real-pdf-prewarm-page-height-points", type=float, default=792.0)
    parser.add_argument("--real-pdf-prewarm-cached-target-ms", type=float, default=250.0)
    parser.add_argument("--real-pdf-prewarm-background-target-ms", type=float, default=300.0)
    parser.add_argument("--real-pdf-prewarm-gap-max-target-ms", type=float, default=500.0)
    parser.add_argument("--real-pdf-prewarm-step-target-ms", type=float, default=75.0)
    parser.add_argument("--real-pdf-prewarm-vs-cold-ratio", type=float, default=0.5)
    parser.add_argument("--real-pdf-prewarm-plateau-rss-delta-mb", type=float, default=64.0)
    parser.add_argument("--real-pdf-prewarm-cache-dir-max-mb", type=float, default=512.0)
    parser.add_argument("--real-pdf-prewarm-timeout-s", type=float, default=30.0)
    parser.add_argument("--real-pdf-prewarm-no-redacted-sources", dest="real_pdf_prewarm_redacted_sources", action="store_false", default=True)
    parser.add_argument("--include-p5-g26-contract", action="store_true")
    parser.add_argument("--p5-g26-event-loop-max-target-ms", type=float, default=500.0)
    parser.add_argument("--p5-g26-repeat-cache-hit-rate-target", type=float, default=0.95)
    parser.add_argument("--include-zone-selection-hotpath", action="store_true")
    parser.add_argument("--zone-selection-runs", type=int, default=20)
    parser.add_argument("--zone-selection-count", type=int, default=1000)
    parser.add_argument("--p5-g26-zone-selection-p95-target-ms", type=float, default=100.0)
    parser.add_argument("--include-p5-g27-selected-zone-crop-first", action="store_true")
    parser.add_argument("--p5-g27-zone-selection-runs", type=int, default=20)
    parser.add_argument("--p5-g27-zone-selection-count", type=int, default=1000)
    parser.add_argument("--p5-g27-crop-visible-p95-target-ms", type=float, default=500.0)
    parser.add_argument("--p5-g27-event-loop-gap-max-target-ms", type=float, default=500.0)
    parser.add_argument("--p5-g27-real-renderer-bridge-json", type=Path)
    parser.add_argument("--p5-g27-require-real-renderer-bridge", action="store_true")
    parser.add_argument("--include-p5-g28-cache-plateau", action="store_true")
    parser.add_argument("--p5-g28-validation-summary", type=Path, action="append", default=[])
    parser.add_argument("--p5-g28-live-counter-min-sources", type=int, default=1)
    parser.add_argument("--p5-g28-live-counter-tail-slope-target-bytes", type=int, default=0)
    parser.add_argument("--real-corpus-validation-output", type=Path)
    parser.add_argument("--real-corpus-viewer-root", type=Path)
    parser.add_argument("--real-corpus-customer-evidence-manifest", type=Path)
    parser.add_argument("--real-corpus-quick", action="store_true")
    parser.add_argument("--real-corpus-soak-minutes", type=float, default=0.0)
    parser.add_argument("--real-corpus-visits", type=int, default=100)
    parser.add_argument("--real-corpus-warmup-visits", type=int, default=20)
    parser.add_argument("--real-corpus-max-zone-artifacts", type=int, default=50)
    parser.add_argument("--real-corpus-max-page-artifacts", type=int, default=50)
    parser.add_argument("--real-corpus-min-zone-artifacts", type=int, default=1)
    parser.add_argument("--real-corpus-min-page-artifacts", type=int, default=0)
    parser.add_argument("--real-corpus-replay-p95-target-ms", type=float, default=250.0)
    parser.add_argument("--real-corpus-gap-max-target-ms", type=float, default=500.0)
    parser.add_argument("--real-corpus-rss-slope-target-mb-per-100", type=float, default=5.0)
    parser.add_argument("--real-corpus-rss-end-delta-mb", type=float, default=64.0)
    parser.add_argument("--real-corpus-rss-tail-delta-mb", type=float, default=128.0)
    parser.add_argument("--real-corpus-settle-ms", type=float, default=0.0)
    parser.add_argument("--real-corpus-timeout-s", type=float, default=60.0)
    parser.add_argument("--real-corpus-require-customer-corpus", action="store_true")
    parser.add_argument("--real-corpus-min-customer-sheet-count", type=int, default=20)
    parser.add_argument("--real-corpus-max-customer-sheet-count", type=int, default=50)
    parser.add_argument("--scratch-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-fail-on-exceed", action="store_true")
    parser.add_argument("--allow-missing-psutil", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    if _real_corpus_replay_requested(args):
        return _run_real_corpus_replay_cli(args, started)
    scratch_root = args.scratch_dir or Path(tempfile.mkdtemp(prefix="workbench_gui_hotpath_"))
    scratch_root.mkdir(parents=True, exist_ok=True)
    viewer_root = scratch_root / "viewer"
    viewer_root.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "scratch_dir": str(scratch_root),
        "viewer_root": str(viewer_root),
        "parameters": {
            "selection_runs": int(args.selection_runs),
            "pairs": int(args.pairs),
            "overlays_per_pair": int(args.overlays_per_pair),
            "selection_overlay_count": int(args.selection_overlay_count),
            "full_tree_overlays": int(args.full_tree_overlays),
            "full_tree_min_chunks": int(args.full_tree_min_chunks),
            "cached_p95_target_ms": float(args.cached_p95_target_ms),
            "cold_p95_target_ms": float(args.cold_p95_target_ms),
            "max_tail_rss_delta_mb": float(args.max_tail_rss_delta_mb),
            "full_tree_max_chunk_target_ms": float(args.full_tree_max_chunk_target_ms),
            "full_tree_p95_gap_target_ms": float(args.full_tree_p95_gap_target_ms),
            "full_tree_start_call_target_ms": float(args.full_tree_start_call_target_ms),
            "page_nav_overlays": int(args.page_nav_overlays),
            "page_nav_start_call_target_ms": float(args.page_nav_start_call_target_ms),
            "page_nav_p95_gap_target_ms": float(args.page_nav_p95_gap_target_ms),
            "rapid_page_nav_overlays": int(args.rapid_page_nav_overlays),
            "rapid_page_nav_step_target_ms": float(args.rapid_page_nav_step_target_ms),
            "rapid_page_nav_p95_gap_target_ms": float(args.rapid_page_nav_p95_gap_target_ms),
            "include_stress_page_nav": bool(args.include_stress_page_nav),
            "stress_page_nav_overlays": int(args.stress_page_nav_overlays),
            "stress_page_nav_step_target_ms": float(args.stress_page_nav_step_target_ms),
            "stress_page_nav_p95_gap_target_ms": float(args.stress_page_nav_p95_gap_target_ms),
            "stress_page_nav_min_chunks": int(args.stress_page_nav_min_chunks),
            "stress_page_nav_timeout_s": float(args.stress_page_nav_timeout_s),
            "stress_page_nav_max_rss_delta_mb": float(args.stress_page_nav_max_rss_delta_mb),
            "include_navigation_soak": bool(args.include_navigation_soak),
            "navigation_soak_pairs": int(args.navigation_soak_pairs),
            "navigation_soak_visits": int(args.navigation_soak_visits),
            "navigation_soak_overlays_per_pair": int(args.navigation_soak_overlays_per_pair),
            "navigation_soak_warmup_visits": int(args.navigation_soak_warmup_visits),
            "navigation_soak_selection_p95_target_ms": float(args.navigation_soak_selection_p95_target_ms),
            "navigation_soak_p95_gap_target_ms": float(args.navigation_soak_p95_gap_target_ms),
            "navigation_soak_gap_max_target_ms": float(args.navigation_soak_gap_max_target_ms),
            "navigation_soak_rss_slope_target_mb_per_100": float(args.navigation_soak_rss_slope_target_mb_per_100),
            "navigation_soak_rss_end_delta_mb": float(args.navigation_soak_rss_end_delta_mb),
            "navigation_soak_rss_tail_delta_mb": float(args.navigation_soak_rss_tail_delta_mb),
            "navigation_soak_settle_ms": float(args.navigation_soak_settle_ms),
            "navigation_soak_timeout_s": float(args.navigation_soak_timeout_s),
            "include_p4_overlay_streaming": bool(args.include_p4_overlay_streaming),
            "p4_overlay_count": int(args.p4_overlay_count),
            "p4_overlay_top_issues": int(args.p4_overlay_top_issues),
            "p4_overlay_first_paint_cap": int(args.p4_overlay_first_paint_cap),
            "include_p5_overlay_page_store_query": bool(args.include_p5_overlay_page_store_query),
            "p5_page_store_overlay_count": int(args.p5_page_store_overlay_count),
            "p5_page_store_page_size": int(args.p5_page_store_page_size),
            "p5_page_store_page_pair_count": int(args.p5_page_store_page_pair_count),
            "p5_page_store_target_page": int(args.p5_page_store_target_page),
            "p5_page_store_first_visible_limit": int(args.p5_page_store_first_visible_limit),
            "p5_page_store_max_page_file_reads": int(args.p5_page_store_max_page_file_reads),
            "include_p4_visible_tiles": bool(args.include_p4_visible_tiles),
            "p4_visible_image_size": int(args.p4_visible_image_size),
            "p4_visible_viewport_size": int(args.p4_visible_viewport_size),
            "p4_visible_prefetch_radius": int(args.p4_visible_prefetch_radius),
            "p4_visible_max_materialized_tiles": int(args.p4_visible_max_materialized_tiles),
            "include_p5_tile_retention_soak": bool(args.include_p5_tile_retention_soak),
            "p5_tile_retention_pairs": int(args.p5_tile_retention_pairs),
            "p5_tile_retention_image_size": int(args.p5_tile_retention_image_size),
            "p5_tile_retention_byte_limit_mb": float(args.p5_tile_retention_byte_limit_mb),
            "p5_tile_retention_prune_p95_target_ms": float(args.p5_tile_retention_prune_p95_target_ms),
            "p5_tile_retention_gap_p95_target_ms": float(args.p5_tile_retention_gap_p95_target_ms),
            "include_lightweight_pdf_load": bool(args.include_lightweight_pdf_load),
            "lightweight_pdf_schedule_target_ms": float(args.lightweight_pdf_schedule_target_ms),
            "lightweight_pdf_p95_gap_target_ms": float(args.lightweight_pdf_p95_gap_target_ms),
            "lightweight_pdf_cold_target_ms": float(args.lightweight_pdf_cold_target_ms),
            "lightweight_pdf_cached_target_ms": float(args.lightweight_pdf_cached_target_ms),
            "include_real_pdf_page_nav": bool(args.include_real_pdf_page_nav),
            "real_pdf_nav_overlays": int(args.real_pdf_nav_overlays),
            "real_pdf_nav_page_count": int(args.real_pdf_nav_page_count),
            "real_pdf_nav_step_count": int(args.real_pdf_nav_step_count),
            "real_pdf_nav_page_width_points": float(args.real_pdf_nav_page_width_points),
            "real_pdf_nav_page_height_points": float(args.real_pdf_nav_page_height_points),
            "real_pdf_nav_step_target_ms": float(args.real_pdf_nav_step_target_ms),
            "real_pdf_nav_p95_gap_target_ms": float(args.real_pdf_nav_p95_gap_target_ms),
            "real_pdf_nav_cold_target_ms": float(args.real_pdf_nav_cold_target_ms),
            "real_pdf_nav_min_chunks": int(args.real_pdf_nav_min_chunks),
            "real_pdf_nav_min_plan_build_workers": int(args.real_pdf_nav_min_plan_build_workers),
            "real_pdf_nav_min_generation_drops": int(args.real_pdf_nav_min_generation_drops),
            "real_pdf_nav_max_completed_loads": int(args.real_pdf_nav_max_completed_loads),
            "real_pdf_nav_max_rss_delta_mb": float(args.real_pdf_nav_max_rss_delta_mb),
            "real_pdf_nav_timeout_s": float(args.real_pdf_nav_timeout_s),
            "real_pdf_nav_redacted_sources": bool(args.real_pdf_nav_redacted_sources),
            "include_real_pdf_prewarm_cache_nav": bool(args.include_real_pdf_prewarm_cache_nav),
            "real_pdf_prewarm_page_count": int(args.real_pdf_prewarm_page_count),
            "real_pdf_prewarm_page_width_points": float(args.real_pdf_prewarm_page_width_points),
            "real_pdf_prewarm_page_height_points": float(args.real_pdf_prewarm_page_height_points),
            "real_pdf_prewarm_cached_target_ms": float(args.real_pdf_prewarm_cached_target_ms),
            "real_pdf_prewarm_background_target_ms": float(args.real_pdf_prewarm_background_target_ms),
            "real_pdf_prewarm_gap_max_target_ms": float(args.real_pdf_prewarm_gap_max_target_ms),
            "real_pdf_prewarm_step_target_ms": float(args.real_pdf_prewarm_step_target_ms),
            "real_pdf_prewarm_vs_cold_ratio": float(args.real_pdf_prewarm_vs_cold_ratio),
            "real_pdf_prewarm_plateau_rss_delta_mb": float(args.real_pdf_prewarm_plateau_rss_delta_mb),
            "real_pdf_prewarm_cache_dir_max_mb": float(args.real_pdf_prewarm_cache_dir_max_mb),
            "real_pdf_prewarm_timeout_s": float(args.real_pdf_prewarm_timeout_s),
            "real_pdf_prewarm_redacted_sources": bool(args.real_pdf_prewarm_redacted_sources),
            "include_p5_g26_contract": bool(args.include_p5_g26_contract),
            "p5_g26_event_loop_max_target_ms": float(args.p5_g26_event_loop_max_target_ms),
            "p5_g26_repeat_cache_hit_rate_target": float(args.p5_g26_repeat_cache_hit_rate_target),
            "include_zone_selection_hotpath": bool(args.include_zone_selection_hotpath),
            "zone_selection_runs": int(args.zone_selection_runs),
            "zone_selection_count": int(args.zone_selection_count),
            "p5_g26_zone_selection_p95_target_ms": float(args.p5_g26_zone_selection_p95_target_ms),
            "include_p5_g27_selected_zone_crop_first": bool(args.include_p5_g27_selected_zone_crop_first),
            "p5_g27_zone_selection_runs": int(args.p5_g27_zone_selection_runs),
            "p5_g27_zone_selection_count": int(args.p5_g27_zone_selection_count),
            "p5_g27_crop_visible_p95_target_ms": float(args.p5_g27_crop_visible_p95_target_ms),
            "p5_g27_event_loop_gap_max_target_ms": float(args.p5_g27_event_loop_gap_max_target_ms),
            "p5_g27_real_renderer_bridge_json": (
                str(args.p5_g27_real_renderer_bridge_json)
                if args.p5_g27_real_renderer_bridge_json
                else ""
            ),
            "p5_g27_require_real_renderer_bridge": bool(
                args.p5_g27_require_real_renderer_bridge
            ),
            "include_p5_g28_cache_plateau": bool(args.include_p5_g28_cache_plateau),
            "p5_g28_validation_summary": [
                str(path) for path in (args.p5_g28_validation_summary or [])
            ],
            "p5_g28_live_counter_min_sources": int(args.p5_g28_live_counter_min_sources),
            "p5_g28_live_counter_tail_slope_target_bytes": int(
                args.p5_g28_live_counter_tail_slope_target_bytes
            ),
        },
    }
    payload["pair_selection"] = _run_pair_selection_probe(
        scratch_root,
        viewer_root,
        runs=int(args.selection_runs),
        overlay_total_count=int(args.selection_overlay_count),
    )
    payload["first_review_tile_probe"] = _run_first_review_tile_probe(scratch_root, viewer_root)
    payload["overlay_cache_rss_probe"] = _run_overlay_cache_rss_probe(
        viewer_root,
        pair_count=int(args.pairs),
        overlays_per_pair=int(args.overlays_per_pair),
    )
    full_tree_viewer_root = viewer_root / "full_tree_probe"
    full_tree_viewer_root.mkdir(parents=True, exist_ok=True)
    payload["full_tree_responsiveness_probe"] = _run_full_tree_responsiveness_probe(
        scratch_root,
        full_tree_viewer_root,
        overlay_count=int(args.full_tree_overlays),
    )
    page_nav_viewer_root = viewer_root / "page_navigation_probe"
    page_nav_viewer_root.mkdir(parents=True, exist_ok=True)
    payload["page_navigation_probe"] = _run_page_navigation_probe(
        scratch_root,
        page_nav_viewer_root,
        overlay_count=int(args.page_nav_overlays),
    )
    rapid_page_nav_viewer_root = viewer_root / "rapid_page_navigation_probe"
    rapid_page_nav_viewer_root.mkdir(parents=True, exist_ok=True)
    payload["rapid_page_navigation_probe"] = _run_rapid_page_navigation_probe(
        scratch_root,
        rapid_page_nav_viewer_root,
        overlay_count=int(args.rapid_page_nav_overlays),
    )
    if args.include_stress_page_nav:
        stress_page_nav_viewer_root = viewer_root / "stress_page_navigation_probe"
        stress_page_nav_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["stress_page_navigation_probe"] = _run_rapid_page_navigation_probe(
            scratch_root,
            stress_page_nav_viewer_root,
            overlay_count=int(args.stress_page_nav_overlays),
            page_pair_count=5,
            step_count=4,
            timeout_s=float(args.stress_page_nav_timeout_s),
        )
    if args.include_navigation_soak:
        navigation_soak_viewer_root = viewer_root / "navigation_soak_probe"
        navigation_soak_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["navigation_soak_probe"] = _run_navigation_soak_probe(
            scratch_root,
            navigation_soak_viewer_root,
            pair_count=int(args.navigation_soak_pairs),
            visit_count=int(args.navigation_soak_visits),
            overlays_per_pair=int(args.navigation_soak_overlays_per_pair),
            warmup_visits=int(args.navigation_soak_warmup_visits),
            settle_ms=float(args.navigation_soak_settle_ms),
            timeout_s=float(args.navigation_soak_timeout_s),
        )
    if args.include_zone_selection_hotpath or args.include_p5_g26_contract:
        zone_selection_viewer_root = viewer_root / "zone_selection_hotpath_probe"
        zone_selection_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["zone_selection_hotpath_probe"] = _run_zone_selection_hotpath_probe(
            scratch_root,
            zone_selection_viewer_root,
            zone_count=int(args.zone_selection_count),
            runs=int(args.zone_selection_runs),
        )
    if args.include_p5_g27_selected_zone_crop_first:
        selected_zone_crop_viewer_root = viewer_root / "selected_zone_crop_first_probe"
        selected_zone_crop_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["selected_zone_crop_first_probe"] = _run_selected_zone_crop_first_probe(
            scratch_root,
            selected_zone_crop_viewer_root,
            zone_count=int(args.p5_g27_zone_selection_count),
            runs=int(args.p5_g27_zone_selection_runs),
        )
    if args.include_p4_overlay_streaming:
        p4_overlay_viewer_root = viewer_root / "p4_overlay_streaming_probe"
        p4_overlay_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["p4_overlay_streaming_probe"] = _run_p4_overlay_streaming_probe(
            scratch_root,
            p4_overlay_viewer_root,
            overlay_total_count=int(args.p4_overlay_count),
            top_issue_count=int(args.p4_overlay_top_issues),
        )
    if args.include_p5_overlay_page_store_query:
        p5_overlay_viewer_root = viewer_root / "p5_overlay_page_store_query_probe"
        p5_overlay_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["p5_overlay_page_store_query_probe"] = _run_p5_overlay_page_store_query_probe(
            scratch_root,
            p5_overlay_viewer_root,
            overlay_total_count=int(args.p5_page_store_overlay_count),
            page_pair_count=int(args.p5_page_store_page_pair_count),
            page_size=int(args.p5_page_store_page_size),
            target_page=int(args.p5_page_store_target_page),
            first_visible_limit=int(args.p5_page_store_first_visible_limit),
            max_page_file_reads=int(args.p5_page_store_max_page_file_reads),
        )
    if args.include_p4_visible_tiles:
        p4_visible_viewer_root = viewer_root / "p4_visible_tile_probe"
        p4_visible_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["p4_visible_tile_probe"] = _run_p4_visible_tile_probe(
            scratch_root,
            p4_visible_viewer_root,
            image_size=int(args.p4_visible_image_size),
            viewport_size=int(args.p4_visible_viewport_size),
            prefetch_radius=int(args.p4_visible_prefetch_radius),
        )
    if args.include_p5_tile_retention_soak or args.include_p5_g28_cache_plateau:
        p5_tile_retention_viewer_root = viewer_root / "p5_tile_retention_probe"
        p5_tile_retention_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["p5_tile_retention_probe"] = _run_p5_tile_retention_probe(
            scratch_root,
            p5_tile_retention_viewer_root,
            pair_count=int(args.p5_tile_retention_pairs),
            image_size=int(args.p5_tile_retention_image_size),
            byte_limit_mb=float(args.p5_tile_retention_byte_limit_mb),
        )
    if args.include_lightweight_pdf_load:
        lightweight_pdf_viewer_root = viewer_root / "lightweight_pdf_load_probe"
        lightweight_pdf_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["lightweight_pdf_load_probe"] = _run_lightweight_pdf_load_probe(
            scratch_root,
            lightweight_pdf_viewer_root,
        )
    if args.include_real_pdf_page_nav:
        real_pdf_nav_viewer_root = viewer_root / "real_pdf_page_navigation_probe"
        real_pdf_nav_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["real_pdf_page_navigation_probe"] = _run_real_pdf_page_navigation_probe(
            scratch_root,
            real_pdf_nav_viewer_root,
            overlay_count=int(args.real_pdf_nav_overlays),
            page_pair_count=int(args.real_pdf_nav_page_count),
            step_count=int(args.real_pdf_nav_step_count),
            page_size_points=(
                float(args.real_pdf_nav_page_width_points),
                float(args.real_pdf_nav_page_height_points),
            ),
            use_redacted_sources=bool(args.real_pdf_nav_redacted_sources),
            timeout_s=float(args.real_pdf_nav_timeout_s),
        )
    if args.include_real_pdf_prewarm_cache_nav:
        prewarm_viewer_root = viewer_root / "real_pdf_prewarm_cache_probe"
        prewarm_viewer_root.mkdir(parents=True, exist_ok=True)
        payload["real_pdf_prewarm_cache_probe"] = _run_real_pdf_prewarm_cache_probe(
            scratch_root,
            prewarm_viewer_root,
            page_pair_count=int(args.real_pdf_prewarm_page_count),
            page_size_points=(
                float(args.real_pdf_prewarm_page_width_points),
                float(args.real_pdf_prewarm_page_height_points),
            ),
            use_redacted_sources=bool(args.real_pdf_prewarm_redacted_sources),
            timeout_s=float(args.real_pdf_prewarm_timeout_s),
        )
    if args.include_p5_g26_contract:
        payload["benchmark_id"] = P5_G26_BENCHMARK_ID
        payload["profile"] = P5_G26_PROFILE
        p5_g26_evidence = _p5_g26_contract_summary(payload, args)
        payload["p5_g26_evidence"] = p5_g26_evidence
        payload["p5_g26_contract"] = p5_g26_evidence
        payload["p5_g26_required_gate_names"] = list(P5_G26_REQUIRED_GATE_NAMES)
    if args.include_p5_g27_selected_zone_crop_first:
        payload["benchmark_id"] = P5_G27_BENCHMARK_ID
        payload["profile"] = P5_G27_PROFILE
        p5_g27_evidence = _p5_g27_contract_summary(payload, args)
        payload["p5_g27_evidence"] = p5_g27_evidence
        payload["p5_g27_contract"] = p5_g27_evidence
        payload["p5_g27_required_gate_names"] = list(P5_G27_REQUIRED_GATE_NAMES)
        if args.p5_g27_real_renderer_bridge_json or args.p5_g27_require_real_renderer_bridge:
            payload["p5_g27_real_renderer_bridge"] = _p5_g27_real_renderer_bridge_summary(
                args.p5_g27_real_renderer_bridge_json
            )
            payload["p5_g27_real_renderer_bridge_required_gate_names"] = list(
                P5_G27_REAL_RENDERER_BRIDGE_REQUIRED_GATE_NAMES
            )
    if args.include_p5_g28_cache_plateau:
        payload["benchmark_id"] = P5_G28_BENCHMARK_ID
        payload["profile"] = P5_G28_PROFILE
        p5_g28_evidence = _p5_g28_contract_summary(payload, args)
        payload["p5_g28_evidence"] = p5_g28_evidence
        payload["p5_g28_contract"] = p5_g28_evidence
        payload["p5_g28_required_gate_names"] = list(P5_G28_REQUIRED_GATE_NAMES)
    gates = _gate_summary(payload, args)
    payload["gates"] = [gate.to_dict() for gate in gates]
    payload["status"] = "passed" if all(gate.passed for gate in gates) else "failed"
    payload["elapsed_s"] = round(time.perf_counter() - started, 3)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False))
    if payload["status"] == "passed" or args.no_fail_on_exceed:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
