# -*- coding: utf-8 -*-
"""Smoke tests for ``scripts/benchmark_workbench_gui_hotpath.py``.

The full P2 workload is intentionally not run in unit CI. These tests cover the
CLI surface, summary shape, gate logic, and a smallest-workload end-to-end run.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="module")
def benchmark_module():
    pytest.importorskip("PySide6")
    return importlib.import_module("scripts.benchmark_workbench_gui_hotpath")


def test_module_imports_and_exposes_expected_helpers(benchmark_module) -> None:
    assert benchmark_module.SCHEMA_VERSION == "workbench-gui-hotpath-benchmark/v1"
    assert hasattr(benchmark_module, "main")
    assert hasattr(benchmark_module, "_run_pair_selection_probe")
    assert hasattr(benchmark_module, "_run_first_review_tile_probe")
    assert hasattr(benchmark_module, "_run_overlay_cache_rss_probe")
    assert hasattr(benchmark_module, "_run_full_tree_responsiveness_probe")
    assert hasattr(benchmark_module, "_run_page_navigation_probe")
    assert hasattr(benchmark_module, "_run_rapid_page_navigation_probe")
    assert hasattr(benchmark_module, "_run_navigation_soak_probe")
    assert hasattr(benchmark_module, "_run_p4_overlay_streaming_probe")
    assert hasattr(benchmark_module, "_run_p5_overlay_page_store_query_probe")
    assert hasattr(benchmark_module, "_run_p4_visible_tile_probe")
    assert hasattr(benchmark_module, "_run_p5_tile_retention_probe")
    assert hasattr(benchmark_module, "_run_lightweight_pdf_load_probe")
    assert hasattr(benchmark_module, "_run_real_pdf_page_navigation_probe")
    assert hasattr(benchmark_module, "_run_real_pdf_prewarm_cache_probe")
    assert hasattr(benchmark_module, "_run_real_corpus_replay_cli")
    assert hasattr(benchmark_module, "_gate_summary")


def test_percentile_and_latency_summary_are_stable(benchmark_module) -> None:
    assert benchmark_module._percentile([], 95.0) != benchmark_module._percentile([], 95.0)
    assert benchmark_module._percentile([5.0], 95.0) == 5.0
    assert benchmark_module._percentile([1.0, 2.0, 3.0, 4.0], 50.0) == 2.5
    summary = benchmark_module._latency_summary([10.0, 20.0, 30.0])
    assert summary["count"] == 3
    assert summary["p50_ms"] == 20.0
    assert summary["p95_ms"] == 29.0


def test_parse_args_exposes_real_pdf_prewarm_targets(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-real-pdf-prewarm-cache-nav",
            "--real-pdf-prewarm-cached-target-ms",
            "250",
            "--real-pdf-prewarm-background-target-ms",
            "300",
            "--real-pdf-prewarm-gap-max-target-ms",
            "500",
        ]
    )

    assert args.include_real_pdf_prewarm_cache_nav is True
    assert args.real_pdf_prewarm_cached_target_ms == 250.0
    assert args.real_pdf_prewarm_background_target_ms == 300.0
    assert args.real_pdf_prewarm_gap_max_target_ms == 500.0


def test_parse_args_exposes_navigation_soak_targets(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-navigation-soak",
            "--navigation-soak-pairs",
            "20",
            "--navigation-soak-visits",
            "60",
            "--navigation-soak-rss-slope-target-mb-per-100",
            "5",
            "--navigation-soak-rss-end-delta-mb",
            "64",
        ]
    )

    assert args.include_navigation_soak is True
    assert args.navigation_soak_pairs == 20
    assert args.navigation_soak_visits == 60
    assert args.navigation_soak_rss_slope_target_mb_per_100 == 5.0
    assert args.navigation_soak_rss_end_delta_mb == 64.0


def test_parse_args_exposes_p4_overlay_streaming_targets(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-p4-overlay-streaming",
            "--p4-overlay-count",
            "100000",
            "--p4-overlay-first-paint-cap",
            "500",
        ]
    )

    assert args.include_p4_overlay_streaming is True
    assert args.p4_overlay_count == 100000
    assert args.p4_overlay_first_paint_cap == 500


def test_parse_args_exposes_p5_overlay_page_store_query_targets(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-p5-overlay-page-store-query",
            "--p5-page-store-overlay-count",
            "1024",
            "--p5-page-store-page-size",
            "64",
            "--p5-page-store-page-pair-count",
            "8",
            "--p5-page-store-target-page",
            "3",
            "--p5-page-store-first-visible-limit",
            "32",
            "--p5-page-store-max-page-file-reads",
            "2",
        ]
    )

    assert args.include_p5_overlay_page_store_query is True
    assert args.p5_page_store_overlay_count == 1024
    assert args.p5_page_store_page_size == 64
    assert args.p5_page_store_page_pair_count == 8
    assert args.p5_page_store_target_page == 3
    assert args.p5_page_store_first_visible_limit == 32
    assert args.p5_page_store_max_page_file_reads == 2


def test_parse_args_exposes_p4_visible_tile_targets(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-p4-visible-tiles",
            "--p4-visible-image-size",
            "4096",
            "--p4-visible-viewport-size",
            "512",
            "--p4-visible-prefetch-radius",
            "1",
            "--p4-visible-max-materialized-tiles",
            "18",
        ]
    )

    assert args.include_p4_visible_tiles is True
    assert args.p4_visible_image_size == 4096
    assert args.p4_visible_viewport_size == 512
    assert args.p4_visible_prefetch_radius == 1
    assert args.p4_visible_max_materialized_tiles == 18


def test_parse_args_exposes_p5_tile_retention_targets(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-p5-tile-retention-soak",
            "--p5-tile-retention-pairs",
            "8",
            "--p5-tile-retention-byte-limit-mb",
            "1.5",
            "--p5-tile-retention-prune-p95-target-ms",
            "250",
        ]
    )

    assert args.include_p5_tile_retention_soak is True
    assert args.p5_tile_retention_pairs == 8
    assert args.p5_tile_retention_byte_limit_mb == 1.5
    assert args.p5_tile_retention_prune_p95_target_ms == 250.0


def test_parse_args_exposes_real_corpus_replay_targets(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--real-corpus-validation-output",
            "validation-out",
            "--real-corpus-viewer-root",
            "validation-out/viewer",
            "--real-corpus-customer-evidence-manifest",
            "customer-manifest.json",
            "--real-corpus-quick",
            "--real-corpus-soak-minutes",
            "2",
            "--real-corpus-visits",
            "120",
            "--real-corpus-warmup-visits",
            "10",
            "--real-corpus-require-customer-corpus",
            "--real-corpus-min-customer-sheet-count",
            "20",
            "--real-corpus-max-customer-sheet-count",
            "50",
        ]
    )

    assert args.real_corpus_validation_output == Path("validation-out")
    assert args.real_corpus_viewer_root == Path("validation-out/viewer")
    assert args.real_corpus_customer_evidence_manifest == Path("customer-manifest.json")
    assert args.real_corpus_quick is True
    assert args.real_corpus_soak_minutes == 2.0
    assert args.real_corpus_visits == 120
    assert args.real_corpus_warmup_visits == 10
    assert args.real_corpus_require_customer_corpus is True
    assert args.real_corpus_min_customer_sheet_count == 20
    assert args.real_corpus_max_customer_sheet_count == 50


def _passing_gate_payload() -> dict:
    return {
        "pair_selection": {
            "cached_pdf": {"p95_ms": 1.0},
            "cold_pdf": {"p95_ms": 1.0},
        },
        "first_review_tile_probe": {"passed": True},
        "p4_overlay_streaming_probe": {
            "declared_overlay_count": 100000,
            "materialized_overlay_count": 5,
            "overlay_json_read_for_first_paint": False,
            "overlay_json_read_call_count": 0,
        },
        "p4_visible_tile_probe": {
            "pyramid_complete": False,
            "materialized_tile_count": 18,
            "planned_tile_count": 128,
            "outside_window_status": "tile_pending",
            "on_demand_materialized_tile_count": 20,
            "on_demand_filled_status": "tile_ready",
            "on_demand_repeat_added_tile_count": 0,
            "on_demand_pyramid_complete": False,
        },
        "p5_tile_retention_probe": {
            "completed": True,
            "retained_bytes": 900,
            "byte_limit": 1000,
            "eviction_count": 2,
            "evicted_estimated_bytes": 500,
            "orphan_bytes": 0,
            "stale_manifest_count": 0,
            "hot_pair_retained": True,
            "evicted_pair_miss": True,
            "write_ms": {"p95_ms": 20.0},
            "event_loop_gap": {"p95_ms": 10.0, "over_500ms_count": 0},
        },
        "overlay_cache_rss_probe": {
            "pair_count": 1,
            "cache_pair_limit": 8,
            "max_cache_pair_count": 1,
            "cache_byte_limit": 1000,
            "max_cache_total_bytes": 1,
            "rss_available": True,
        },
        "full_tree_responsiveness_probe": {
            "completed": True,
            "start_call_ms": 1.0,
            "event_loop_gap": {"p95_ms": 1.0},
            "full_tree_summary": {
                "chunk_count": {"p95": 2.0},
                "max_chunk_ms": {"p95": 1.0},
                "overlay_load_worker_count": 1,
                "plan_build_worker_count": 1,
            },
        },
        "page_navigation_probe": {
            "completed": True,
            "start_call_ms": 1.0,
            "visible_leaf_count": 1,
            "expected_visible_overlay_count": 1,
            "lightweight_load_count": 1,
            "event_loop_gap": {"p95_ms": 1.0},
            "viewer_perf_summary": {"pdf_page_navigation_deferred_count": 1},
            "full_tree_summary": {"overlay_load_worker_count": 1, "plan_build_worker_count": 1},
        },
        "rapid_page_navigation_probe": {
            "completed": True,
            "target_page": 1,
            "active_pdf_page_index": 1,
            "final_page_a": 1,
            "final_page_b": 1,
            "visible_leaf_count": 1,
            "expected_visible_overlay_count": 1,
            "stale_leaf_count": 0,
            "step_count": 1,
            "step_call_ms": {"max_ms": 1.0},
            "lightweight_load_count": 1,
            "event_loop_gap": {"p95_ms": 1.0},
            "full_tree_summary": {"overlay_load_worker_count": 1, "plan_build_worker_count": 1},
        },
    }


def test_gate_summary_flags_real_pdf_prewarm_cache_failures(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-real-pdf-prewarm-cache-nav",
            "--real-pdf-prewarm-cached-target-ms",
            "250",
            "--real-pdf-prewarm-background-target-ms",
            "300",
            "--real-pdf-prewarm-gap-max-target-ms",
            "500",
            "--real-pdf-prewarm-vs-cold-ratio",
            "0.5",
            "--real-pdf-prewarm-plateau-rss-delta-mb",
            "64",
            "--real-pdf-prewarm-cache-dir-max-mb",
            "512",
        ]
    )
    payload = _passing_gate_payload()
    payload["real_pdf_prewarm_cache_probe"] = {
        "completed": False,
        "qtpdf_available": False,
        "phase_results": {
            "cold_no_prewarm": {"load_ms": {"p95_ms": 600.0}},
            "prewarm_wait": {
                "prewarm_completed_before_navigation": False,
                "expected_item_count": 2,
                "ok_count": 1,
                "visible_state_mutation_count": 1,
            },
            "post_prewarm_cached": {
                "cache_hit": False,
                "metadata_fast_path": False,
                "inferred_render_call_count": 1,
                "load_ms": {"p95_ms": 401.0},
                "time_to_background_ready_ms": {"p95_ms": 301.0},
                "event_loop_gap": {"max_ms": 501.0, "over_500ms_count": 1},
            },
            "cached_navigation_plateau": {
                "navigation_count": 2,
                "all_cached_count": 1,
                "rss_tail_delta_mb": 65.0,
            },
        },
        "step_call_ms": {"max_ms": 76.0},
        "event_loop_gap": {"max_ms": 501.0, "over_500ms_count": 1},
        "pdf_cache": {"size_mb": 513.0},
    }

    gates = benchmark_module._gate_summary(payload, args)
    by_name = {gate.name: gate for gate in gates}

    assert by_name["real_pdf_prewarm_cache_completed"].passed is False
    assert by_name["real_pdf_prewarm_cache_qtpdf_available"].passed is False
    assert by_name["real_pdf_prewarm_completed_before_navigation"].passed is False
    assert by_name["real_pdf_prewarm_cache_coverage"].passed is False
    assert by_name["real_pdf_prewarm_no_visible_state_mutation"].passed is False
    assert by_name["real_pdf_cached_navigation_cache_hit"].passed is False
    assert by_name["real_pdf_cached_navigation_metadata_fast_path"].passed is False
    assert by_name["real_pdf_cached_navigation_no_cold_render"].passed is False
    assert by_name["real_pdf_cached_navigation_load_p95_ms"].passed is False
    assert by_name["real_pdf_cached_navigation_background_ready_ms"].passed is False
    assert by_name["real_pdf_cached_navigation_event_loop_gap_max_ms"].passed is False
    assert by_name["real_pdf_cached_navigation_event_loop_over_500ms_count"].passed is False
    assert by_name["real_pdf_cached_navigation_vs_cold_load_ratio"].passed is False
    assert by_name["real_pdf_cached_navigation_step_call_ms"].passed is False
    assert by_name["real_pdf_cached_plateau_all_cached"].passed is False
    assert by_name["real_pdf_prewarm_cache_dir_size_mb"].passed is False
    assert by_name["real_pdf_cached_plateau_rss_tail_delta_mb"].passed is False


def test_gate_summary_passes_real_pdf_prewarm_cache_success_payload(benchmark_module) -> None:
    args = benchmark_module.parse_args(["--include-real-pdf-prewarm-cache-nav"])
    payload = _passing_gate_payload()
    payload["real_pdf_prewarm_cache_probe"] = {
        "completed": True,
        "qtpdf_available": True,
        "phase_results": {
            "cold_no_prewarm": {"load_ms": {"p95_ms": 600.0}},
            "prewarm_wait": {
                "prewarm_completed_before_navigation": True,
                "expected_item_count": 2,
                "ok_count": 2,
                "visible_state_mutation_count": 0,
            },
            "post_prewarm_cached": {
                "cache_hit": True,
                "metadata_fast_path": True,
                "inferred_render_call_count": 0,
                "load_ms": {"p95_ms": 120.0},
                "time_to_background_ready_ms": {"p95_ms": 150.0},
                "event_loop_gap": {"max_ms": 40.0, "over_500ms_count": 0},
            },
            "cached_navigation_plateau": {
                "navigation_count": 2,
                "all_cached_count": 2,
                "rss_tail_delta_mb": 1.0,
            },
        },
        "step_call_ms": {"max_ms": 20.0},
        "event_loop_gap": {"max_ms": 40.0, "over_500ms_count": 0},
        "pdf_cache": {"size_mb": 1.0},
    }

    gates = benchmark_module._gate_summary(payload, args)
    by_name = {gate.name: gate for gate in gates}

    assert by_name["real_pdf_prewarm_cache_completed"].passed is True
    assert by_name["real_pdf_prewarm_cache_coverage"].passed is True
    assert by_name["real_pdf_cached_navigation_metadata_fast_path"].passed is True
    assert by_name["real_pdf_cached_navigation_no_cold_render"].passed is True
    assert by_name["real_pdf_cached_plateau_all_cached"].passed is True


def test_gate_summary_flags_navigation_soak_failures(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-navigation-soak",
            "--navigation-soak-selection-p95-target-ms",
            "300",
            "--navigation-soak-p95-gap-target-ms",
            "150",
            "--navigation-soak-gap-max-target-ms",
            "500",
            "--navigation-soak-rss-slope-target-mb-per-100",
            "5",
            "--navigation-soak-rss-end-delta-mb",
            "64",
            "--navigation-soak-rss-tail-delta-mb",
            "128",
        ]
    )
    payload = _passing_gate_payload()
    payload["navigation_soak_probe"] = {
        "completed": False,
        "visit_count": 100,
        "completed_visit_count": 99,
        "lightweight_scheduled_count": 98,
        "selection_call_ms": {"p95_ms": 301.0},
        "event_loop_gap": {"p95_ms": 151.0, "max_ms": 501.0, "over_500ms_count": 1},
        "max_cache_pair_count": 9,
        "cache_pair_limit": 8,
        "max_cache_total_bytes": 1001,
        "cache_byte_limit": 1000,
        "rss_slope": {
            "slope_mb_per_100_visits": 5.1,
            "positive_end_delta_mb": 65.0,
            "peak_delta_mb": 129.0,
        },
    }

    gates = benchmark_module._gate_summary(payload, args)
    by_name = {gate.name: gate for gate in gates}

    assert by_name["navigation_soak_completed"].passed is False
    assert by_name["navigation_soak_completed_visit_count"].passed is False
    assert by_name["navigation_soak_lightweight_scheduled"].passed is False
    assert by_name["navigation_soak_selection_p95_ms"].passed is False
    assert by_name["navigation_soak_event_loop_gap_p95_ms"].passed is False
    assert by_name["navigation_soak_event_loop_gap_max_ms"].passed is False
    assert by_name["navigation_soak_event_loop_over_500ms_count"].passed is False
    assert by_name["navigation_soak_cache_pair_bound"].passed is False
    assert by_name["navigation_soak_cache_byte_bound"].passed is False
    assert by_name["navigation_soak_rss_slope_mb_per_100_visits"].passed is False
    assert by_name["navigation_soak_rss_positive_end_delta_mb"].passed is False
    assert by_name["navigation_soak_rss_tail_peak_delta_mb"].passed is False


def test_gate_summary_passes_navigation_soak_success_payload(benchmark_module) -> None:
    args = benchmark_module.parse_args(["--include-navigation-soak"])
    payload = _passing_gate_payload()
    payload["navigation_soak_probe"] = {
        "completed": True,
        "visit_count": 100,
        "completed_visit_count": 100,
        "lightweight_scheduled_count": 100,
        "selection_call_ms": {"p95_ms": 120.0},
        "event_loop_gap": {"p95_ms": 40.0, "max_ms": 80.0, "over_500ms_count": 0},
        "max_cache_pair_count": 8,
        "cache_pair_limit": 8,
        "max_cache_total_bytes": 900,
        "cache_byte_limit": 1000,
        "rss_slope": {
            "slope_mb_per_100_visits": 1.0,
            "positive_end_delta_mb": 2.0,
            "peak_delta_mb": 8.0,
        },
    }

    gates = benchmark_module._gate_summary(payload, args)
    by_name = {gate.name: gate for gate in gates}

    assert by_name["navigation_soak_completed"].passed is True
    assert by_name["navigation_soak_lightweight_scheduled"].passed is True
    assert by_name["navigation_soak_cache_pair_bound"].passed is True
    assert by_name["navigation_soak_rss_slope_mb_per_100_visits"].passed is True
    assert by_name["navigation_soak_rss_positive_end_delta_mb"].passed is True


def test_gate_summary_flags_p5_tile_retention_failures(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-p5-tile-retention-soak",
            "--p5-tile-retention-prune-p95-target-ms",
            "100",
            "--p5-tile-retention-gap-p95-target-ms",
            "50",
        ]
    )
    payload = _passing_gate_payload()
    payload["p5_tile_retention_probe"] = {
        "completed": False,
        "retained_bytes": 1001,
        "byte_limit": 1000,
        "eviction_count": 0,
        "evicted_estimated_bytes": 0,
        "orphan_bytes": 1,
        "stale_manifest_count": 1,
        "hot_pair_retained": False,
        "evicted_pair_miss": False,
        "write_ms": {"p95_ms": 101.0},
        "event_loop_gap": {"p95_ms": 51.0, "over_500ms_count": 1},
    }

    gates = benchmark_module._gate_summary(payload, args)
    by_name = {gate.name: gate for gate in gates}

    assert by_name["p5_tile_retention_completed"].passed is False
    assert by_name["p5_tile_cache_byte_bound"].passed is False
    assert by_name["p5_tile_cache_eviction_count_min"].passed is False
    assert by_name["p5_tile_cache_evicted_bytes_positive"].passed is False
    assert by_name["p5_tile_cache_orphan_bytes_zero"].passed is False
    assert by_name["p5_tile_cache_stale_manifest_count_zero"].passed is False
    assert by_name["p5_tile_cache_hot_pair_retained"].passed is False
    assert by_name["p5_tile_cache_evicted_pair_miss"].passed is False
    assert by_name["p5_tile_cache_prune_p95_ms"].passed is False
    assert by_name["p5_tile_cache_event_loop_gap_p95_ms"].passed is False
    assert by_name["p5_tile_cache_event_loop_over_500ms_count"].passed is False


def test_gate_summary_passes_p5_tile_retention_success_payload(benchmark_module) -> None:
    args = benchmark_module.parse_args(["--include-p5-tile-retention-soak"])
    payload = _passing_gate_payload()

    gates = benchmark_module._gate_summary(payload, args)
    by_name = {gate.name: gate for gate in gates}

    assert by_name["p5_tile_retention_completed"].passed is True
    assert by_name["p5_tile_cache_byte_bound"].passed is True
    assert by_name["p5_tile_cache_hot_pair_retained"].passed is True


def test_gate_summary_flags_p5_overlay_page_store_query_failures(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-p5-overlay-page-store-query",
            "--p5-page-store-overlay-count",
            "1024",
            "--p5-page-store-first-visible-limit",
            "32",
            "--p5-page-store-max-page-file-reads",
            "2",
        ]
    )
    payload = _passing_gate_payload()
    payload["p5_overlay_page_store_query_probe"] = {
        "declared_overlay_count": 1000,
        "phase_results": {
            "first_visible": {
                "legacy_overlay_json_read_count": 1,
                "page_file_read_count": 2,
                "max_page_file_reads": 1,
                "materialized_overlay_count": 1000,
                "materialized_overlay_cap": 32,
                "cached_overlay_count": 1000,
            },
            "page_pair": {
                "overlay_load_strategy": "overlay_json",
                "legacy_overlay_json_read_count": 1,
                "page_file_read_count": 3,
                "max_page_file_reads": 2,
                "expected_visible_overlay_count": 128,
                "visible_leaf_count": 127,
                "stale_leaf_count": 1,
                "materialized_overlay_count": 1000,
                "cached_overlay_count": 1000,
            },
        },
    }

    gates = benchmark_module._gate_summary(payload, args)
    by_name = {gate.name: gate for gate in gates}

    assert by_name["p5_page_store_fixture_declared_overlay_count"].passed is False
    assert by_name["p5_first_visible_no_legacy_overlay_json"].passed is False
    assert by_name["p5_first_visible_sparse_page_reads"].passed is False
    assert by_name["p5_first_visible_materialized_cap"].passed is False
    assert by_name["p5_first_visible_no_full_overlay_cache"].passed is False
    assert by_name["p5_page_pair_uses_paged_overlay_store"].passed is False
    assert by_name["p5_page_pair_no_legacy_overlay_json"].passed is False
    assert by_name["p5_page_pair_sparse_page_reads"].passed is False
    assert by_name["p5_page_pair_materialized_visible_only"].passed is False
    assert by_name["p5_page_pair_visible_leaf_count"].passed is False
    assert by_name["p5_page_pair_no_stale_leaf"].passed is False
    assert by_name["p5_page_pair_no_full_overlay_cache"].passed is False


def test_gate_summary_passes_p5_overlay_page_store_query_success_payload(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--include-p5-overlay-page-store-query",
            "--p5-page-store-overlay-count",
            "1024",
            "--p5-page-store-first-visible-limit",
            "32",
            "--p5-page-store-max-page-file-reads",
            "2",
        ]
    )
    payload = _passing_gate_payload()
    payload["p5_overlay_page_store_query_probe"] = {
        "declared_overlay_count": 1024,
        "phase_results": {
            "first_visible": {
                "legacy_overlay_json_read_count": 0,
                "page_file_read_count": 1,
                "max_page_file_reads": 1,
                "materialized_overlay_count": 32,
                "materialized_overlay_cap": 32,
                "cached_overlay_count": 0,
            },
            "page_pair": {
                "overlay_load_strategy": "paged_overlay_store",
                "legacy_overlay_json_read_count": 0,
                "page_file_read_count": 2,
                "max_page_file_reads": 2,
                "expected_visible_overlay_count": 128,
                "visible_leaf_count": 128,
                "stale_leaf_count": 0,
                "materialized_overlay_count": 128,
                "cached_overlay_count": 0,
            },
        },
    }

    gates = benchmark_module._gate_summary(payload, args)
    p5_gates = [gate for gate in gates if gate.name.startswith("p5_")]

    assert p5_gates
    assert all(gate.passed for gate in p5_gates)


def test_gate_summary_flags_budget_failures(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--cached-p95-target-ms",
            "300",
            "--cold-p95-target-ms",
            "2000",
            "--max-tail-rss-delta-mb",
            "128",
            "--full-tree-max-chunk-target-ms",
            "50",
            "--full-tree-p95-gap-target-ms",
            "100",
            "--full-tree-start-call-target-ms",
            "50",
            "--page-nav-start-call-target-ms",
            "50",
            "--page-nav-p95-gap-target-ms",
            "100",
            "--rapid-page-nav-step-target-ms",
            "50",
            "--rapid-page-nav-p95-gap-target-ms",
            "100",
            "--include-stress-page-nav",
            "--stress-page-nav-step-target-ms",
            "75",
            "--stress-page-nav-p95-gap-target-ms",
            "150",
            "--stress-page-nav-min-chunks",
            "2",
            "--stress-page-nav-max-rss-delta-mb",
            "256",
            "--include-lightweight-pdf-load",
            "--lightweight-pdf-schedule-target-ms",
            "50",
            "--lightweight-pdf-p95-gap-target-ms",
            "150",
            "--lightweight-pdf-cold-target-ms",
            "2500",
            "--lightweight-pdf-cached-target-ms",
            "500",
            "--include-real-pdf-page-nav",
            "--real-pdf-nav-step-target-ms",
            "75",
            "--real-pdf-nav-p95-gap-target-ms",
            "250",
            "--real-pdf-nav-cold-target-ms",
            "4000",
            "--real-pdf-nav-min-chunks",
            "2",
            "--real-pdf-nav-min-plan-build-workers",
            "1",
            "--real-pdf-nav-min-generation-drops",
            "1",
            "--real-pdf-nav-max-completed-loads",
            "1",
            "--real-pdf-nav-max-rss-delta-mb",
            "512",
        ]
    )
    payload = {
        "pair_selection": {
            "cached_pdf": {"p95_ms": 301.0},
            "cold_pdf": {"p95_ms": 1999.0},
        },
        "first_review_tile_probe": {"passed": True},
        "overlay_cache_rss_probe": {
            "pair_count": 100,
            "cache_pair_limit": 8,
            "max_cache_pair_count": 8,
            "cache_byte_limit": 1000,
            "max_cache_total_bytes": 900,
            "rss_available": True,
            "rss_tail_delta_after_cache_limit_mb": 129.0,
        },
        "full_tree_responsiveness_probe": {
            "completed": True,
            "start_call_ms": 51.0,
            "event_loop_gap": {"p95_ms": 101.0},
            "full_tree_summary": {
                "chunk_count": {"p95": 3.0},
                "max_chunk_ms": {"p95": 51.0},
                "overlay_load_worker_count": 0,
                "plan_build_worker_count": 0,
            },
        },
        "page_navigation_probe": {
            "completed": True,
            "start_call_ms": 51.0,
            "visible_leaf_count": 10,
            "expected_visible_overlay_count": 11,
            "lightweight_load_count": 0,
            "event_loop_gap": {"p95_ms": 101.0},
            "viewer_perf_summary": {"pdf_page_navigation_deferred_count": 0},
            "full_tree_summary": {"overlay_load_worker_count": 0, "plan_build_worker_count": 0},
        },
        "rapid_page_navigation_probe": {
            "completed": False,
            "target_page": 2,
            "active_pdf_page_index": 1,
            "final_page_a": 1,
            "final_page_b": 1,
            "visible_leaf_count": 10,
            "expected_visible_overlay_count": 11,
            "stale_leaf_count": 1,
            "step_count": 2,
            "step_call_ms": {"max_ms": 51.0},
            "lightweight_load_count": 1,
            "event_loop_gap": {"p95_ms": 101.0},
            "full_tree_summary": {"overlay_load_worker_count": 0, "plan_build_worker_count": 0},
        },
        "stress_page_navigation_probe": {
            "completed": False,
            "visible_leaf_count": 100,
            "expected_visible_overlay_count": 101,
            "stale_leaf_count": 1,
            "step_call_ms": {"max_ms": 76.0},
            "event_loop_gap": {"p95_ms": 151.0},
            "rss_delta_mb": 257.0,
            "full_tree_summary": {
                "overlay_load_worker_count": 0,
                "plan_build_worker_count": 0,
                "chunk_count": {"p95": 1.0},
            },
        },
        "lightweight_pdf_load_probe": {
            "completed": False,
            "qtpdf_available": False,
            "first_loaded": False,
            "second_loaded": False,
            "first_background_ready": False,
            "second_background_ready": False,
            "first_overlay_after_background": False,
            "second_overlay_after_background": False,
            "second_cache_hit_before": False,
            "second_cache_hit_after": False,
            "max_schedule_call_ms": 51.0,
            "event_loop_gap": {"p95_ms": 151.0},
            "viewer_perf_summary": {
                "lightweight_pdf_cold_load_ms": {"p95": 2501.0},
                "lightweight_pdf_cached_load_ms": {"p95": 501.0},
                "lightweight_pdf_cache_state_counts": {"all_cached": 0, "all_cold": 0},
            },
        },
        "real_pdf_page_navigation_probe": {
            "completed": False,
            "qtpdf_available": False,
            "active_pdf_page_index": 1,
            "target_page": 2,
            "page_size_points": [1684.0, 2384.0],
            "final_page_a": 1,
            "final_page_b": 1,
            "final_dpi_capped": False,
            "initial_render_max_pixels": 8_000_000,
            "final_background_ready": False,
            "overlay_after_background": False,
            "visible_leaf_count": 10,
            "expected_visible_overlay_count": 11,
            "stale_leaf_count": 1,
            "generation_dropped_load_count": 0,
            "completed_lightweight_load_count": 2,
            "step_call_ms": {"max_ms": 76.0},
            "event_loop_gap": {"p95_ms": 251.0},
            "rss_delta_mb": 513.0,
            "use_redacted_sources": True,
            "redacted_fallback_ok": False,
            "viewer_perf_summary": {
                "lightweight_pdf_cold_load_ms": {"p95": 4001.0},
            },
            "full_tree_summary": {
                "overlay_load_worker_count": 0,
                "plan_build_worker_count": 0,
                "chunk_count": {"p95": 1.0},
            },
        },
    }

    gates = benchmark_module._gate_summary(payload, args)
    by_name = {gate.name: gate for gate in gates}

    assert by_name["cached_pdf_pair_selection_p95"].passed is False
    assert by_name["cold_pdf_pair_selection_p95"].passed is True
    assert by_name["overlay_rss_tail_delta_after_cache_limit"].passed is False
    assert by_name["full_tree_initial_call_ms"].passed is False
    assert by_name["full_tree_overlay_load_worker"].passed is False
    assert by_name["full_tree_plan_build_worker"].passed is False
    assert by_name["full_tree_rebuild_max_chunk_ms"].passed is False
    assert by_name["full_tree_event_loop_gap_p95"].passed is False
    assert by_name["page_navigation_initial_call_ms"].passed is False
    assert by_name["page_navigation_overlay_deferred"].passed is False
    assert by_name["page_navigation_overlay_load_worker"].passed is False
    assert by_name["page_navigation_plan_build_worker"].passed is False
    assert by_name["page_navigation_lightweight_scheduled"].passed is False
    assert by_name["page_navigation_visible_leaf_count"].passed is False
    assert by_name["page_navigation_event_loop_gap_p95"].passed is False
    assert by_name["rapid_page_navigation_completed"].passed is False
    assert by_name["rapid_page_navigation_max_step_call_ms"].passed is False
    assert by_name["rapid_page_navigation_final_page"].passed is False
    assert by_name["rapid_page_navigation_visible_leaf_count"].passed is False
    assert by_name["rapid_page_navigation_no_stale_leaf"].passed is False
    assert by_name["rapid_page_navigation_overlay_load_worker"].passed is False
    assert by_name["rapid_page_navigation_plan_build_worker"].passed is False
    assert by_name["rapid_page_navigation_lightweight_scheduled"].passed is False
    assert by_name["rapid_page_navigation_event_loop_gap_p95"].passed is False
    assert by_name["stress_page_navigation_completed"].passed is False
    assert by_name["stress_page_navigation_max_step_call_ms"].passed is False
    assert by_name["stress_page_navigation_event_loop_gap_p95"].passed is False
    assert by_name["stress_page_navigation_visible_leaf_count"].passed is False
    assert by_name["stress_page_navigation_no_stale_leaf"].passed is False
    assert by_name["stress_page_navigation_overlay_load_worker"].passed is False
    assert by_name["stress_page_navigation_plan_build_worker"].passed is False
    assert by_name["stress_page_navigation_chunk_count"].passed is False
    assert by_name["stress_page_navigation_rss_delta_mb"].passed is False
    assert by_name["lightweight_pdf_load_completed"].passed is False
    assert by_name["lightweight_pdf_load_qtpdf_available"].passed is False
    assert by_name["lightweight_pdf_load_both_sides"].passed is False
    assert by_name["lightweight_pdf_load_background_state"].passed is False
    assert by_name["lightweight_pdf_load_overlay_after_background"].passed is False
    assert by_name["lightweight_pdf_load_cache_hit"].passed is False
    assert by_name["lightweight_pdf_load_schedule_call_ms"].passed is False
    assert by_name["lightweight_pdf_load_event_loop_gap_p95"].passed is False
    assert by_name["lightweight_pdf_cold_load_ms"].passed is False
    assert by_name["lightweight_pdf_cached_load_ms"].passed is False
    assert by_name["lightweight_pdf_cache_state_count"].passed is False
    assert by_name["lightweight_pdf_cache_state_cold_count"].passed is False
    assert by_name["real_pdf_page_navigation_completed"].passed is False
    assert by_name["real_pdf_page_navigation_qtpdf_available"].passed is False
    assert by_name["real_pdf_page_navigation_max_step_call_ms"].passed is False
    assert by_name["real_pdf_page_navigation_final_page"].passed is False
    assert by_name["real_pdf_page_navigation_final_background"].passed is False
    assert by_name["real_pdf_page_navigation_overlay_after_background"].passed is False
    assert by_name["real_pdf_page_navigation_visible_leaf_count"].passed is False
    assert by_name["real_pdf_page_navigation_no_stale_leaf"].passed is False
    assert by_name["real_pdf_page_navigation_generation_drop"].passed is False
    assert by_name["real_pdf_page_navigation_completed_load_count"].passed is False
    assert by_name["real_pdf_page_navigation_overlay_load_worker"].passed is False
    assert by_name["real_pdf_page_navigation_plan_build_worker"].passed is False
    assert by_name["real_pdf_page_navigation_chunk_count"].passed is False
    assert by_name["real_pdf_page_navigation_event_loop_gap_p95"].passed is False
    assert by_name["real_pdf_page_navigation_cold_load_ms"].passed is False
    assert by_name["real_pdf_page_navigation_initial_dpi_cap"].passed is False
    assert by_name["real_pdf_page_navigation_redacted_fallback"].passed is False
    assert by_name["real_pdf_page_navigation_rss_delta_mb"].passed is False


def test_main_real_corpus_replay_branch_skips_synthetic_probes(
    benchmark_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    validation_out = tmp_path / "validation"
    validation_out.mkdir()
    (validation_out / "validation_summary.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "p5_g16.json"
    real_replay = importlib.import_module("scripts.benchmark_real_corpus_replay")
    called: dict[str, object] = {}

    def fake_run_replay(args) -> dict:
        called["validation_summary"] = args.validation_summary
        called["output_json"] = args.output_json
        called["visits"] = args.visits
        return {
            "schema_version": 1,
            "benchmark_id": "p5_g16_real_corpus_replay",
            "profile": "real_corpus_artifact_replay",
            "status": "passed",
            "gates": [],
            "summary": {},
        }

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("synthetic GUI probe should be skipped for real-corpus replay")

    monkeypatch.setattr(real_replay, "run_replay", fake_run_replay)
    monkeypatch.setattr(benchmark_module, "_run_pair_selection_probe", fail_if_called)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = benchmark_module.main(
            [
                "--real-corpus-validation-output",
                str(validation_out),
                "--real-corpus-quick",
                "--real-corpus-visits",
                "40",
                "--output",
                str(output),
            ]
        )

    assert exit_code == 0
    assert called["validation_summary"] == validation_out / "validation_summary.json"
    assert called["output_json"] == output
    assert called["visits"] == 20
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["benchmark_id"] == "p5_g16_real_corpus_replay"
    assert payload["wrapper"]["synthetic_gui_probes_skipped"] is True
    stdout_payload = json.loads([line for line in buf.getvalue().splitlines() if line.startswith("{")][-1])
    assert stdout_payload["status"] == "passed"


def test_main_runs_smallest_workload_and_writes_json(benchmark_module, tmp_path: Path) -> None:
    output = tmp_path / "hotpath.json"
    scratch = tmp_path / "scratch"

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = benchmark_module.main(
            [
                "--selection-runs",
                "1",
                "--pairs",
                "3",
                "--overlays-per-pair",
                "5",
                "--selection-overlay-count",
                "5",
                "--full-tree-overlays",
                "520",
                "--cached-p95-target-ms",
                "60000",
                "--cold-p95-target-ms",
                "60000",
                "--max-tail-rss-delta-mb",
                "60000",
                "--full-tree-max-chunk-target-ms",
                "60000",
                "--full-tree-p95-gap-target-ms",
                "60000",
                "--full-tree-start-call-target-ms",
                "60000",
                "--page-nav-start-call-target-ms",
                "60000",
                "--page-nav-p95-gap-target-ms",
                "60000",
                "--rapid-page-nav-step-target-ms",
                "60000",
                "--rapid-page-nav-p95-gap-target-ms",
                "60000",
                "--include-stress-page-nav",
                "--stress-page-nav-overlays",
                "2500",
                "--stress-page-nav-step-target-ms",
                "60000",
                "--stress-page-nav-p95-gap-target-ms",
                "60000",
                "--stress-page-nav-min-chunks",
                "1",
                "--stress-page-nav-max-rss-delta-mb",
                "60000",
                "--include-navigation-soak",
                "--navigation-soak-pairs",
                "3",
                "--navigation-soak-visits",
                "5",
                "--navigation-soak-overlays-per-pair",
                "5",
                "--navigation-soak-warmup-visits",
                "2",
                "--navigation-soak-selection-p95-target-ms",
                "60000",
                "--navigation-soak-p95-gap-target-ms",
                "60000",
                "--navigation-soak-gap-max-target-ms",
                "60000",
                "--navigation-soak-rss-slope-target-mb-per-100",
                "60000",
                "--navigation-soak-rss-end-delta-mb",
                "60000",
                "--navigation-soak-rss-tail-delta-mb",
                "60000",
                "--include-p4-overlay-streaming",
                "--p4-overlay-count",
                "100",
                "--p4-overlay-top-issues",
                "3",
                "--p4-overlay-first-paint-cap",
                "10",
                "--include-p5-overlay-page-store-query",
                "--p5-page-store-overlay-count",
                "1024",
                "--p5-page-store-page-size",
                "64",
                "--p5-page-store-page-pair-count",
                "8",
                "--p5-page-store-target-page",
                "3",
                "--p5-page-store-first-visible-limit",
                "32",
                "--p5-page-store-max-page-file-reads",
                "2",
                "--include-p4-visible-tiles",
                "--p4-visible-image-size",
                "2048",
                "--p4-visible-viewport-size",
                "512",
                "--p4-visible-prefetch-radius",
                "0",
                "--p4-visible-max-materialized-tiles",
                "2",
                "--include-p5-tile-retention-soak",
                "--p5-tile-retention-pairs",
                "3",
                "--p5-tile-retention-image-size",
                "128",
                "--p5-tile-retention-byte-limit-mb",
                "0.2",
                "--p5-tile-retention-prune-p95-target-ms",
                "60000",
                "--p5-tile-retention-gap-p95-target-ms",
                "60000",
                "--allow-missing-psutil",
                "--no-fail-on-exceed",
                "--scratch-dir",
                str(scratch),
                "--output",
                str(output),
            ]
        )

    assert exit_code == 0
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == benchmark_module.SCHEMA_VERSION
    assert payload["pair_selection"]["cold_pdf"]["count"] == 1
    assert payload["pair_selection"]["cached_pdf"]["count"] == 1
    assert payload["first_review_tile_probe"]["passed"] is True
    assert payload["overlay_cache_rss_probe"]["total_overlay_visits"] == 15
    assert payload["full_tree_responsiveness_probe"]["completed"] is True
    assert payload["full_tree_responsiveness_probe"]["overlay_count"] == 520
    assert payload["full_tree_responsiveness_probe"]["start_call_ms"] >= 0.0
    full_tree_summary = payload["full_tree_responsiveness_probe"]["full_tree_summary"]
    assert full_tree_summary["overlay_load_worker_count"] >= 1
    assert full_tree_summary["plan_build_worker_count"] >= 1
    assert payload["page_navigation_probe"]["completed"] is True
    assert payload["page_navigation_probe"]["visible_leaf_count"] == payload["page_navigation_probe"]["expected_visible_overlay_count"]
    assert payload["page_navigation_probe"]["full_tree_summary"]["overlay_load_worker_count"] >= 1
    assert payload["page_navigation_probe"]["full_tree_summary"]["plan_build_worker_count"] >= 1
    assert payload["page_navigation_probe"]["lightweight_load_count"] >= 1
    assert payload["rapid_page_navigation_probe"]["completed"] is True
    assert payload["rapid_page_navigation_probe"]["active_pdf_page_index"] == payload["rapid_page_navigation_probe"]["target_page"]
    assert payload["rapid_page_navigation_probe"]["final_page_a"] == payload["rapid_page_navigation_probe"]["target_page"]
    assert payload["rapid_page_navigation_probe"]["final_page_b"] == payload["rapid_page_navigation_probe"]["target_page"]
    assert payload["rapid_page_navigation_probe"]["visible_leaf_count"] == payload["rapid_page_navigation_probe"]["expected_visible_overlay_count"]
    assert payload["rapid_page_navigation_probe"]["stale_leaf_count"] == 0
    assert payload["rapid_page_navigation_probe"]["full_tree_summary"]["overlay_load_worker_count"] >= 1
    assert payload["rapid_page_navigation_probe"]["full_tree_summary"]["plan_build_worker_count"] >= 1
    assert payload["rapid_page_navigation_probe"]["lightweight_load_count"] >= payload["rapid_page_navigation_probe"]["step_count"]
    assert payload["stress_page_navigation_probe"]["completed"] is True
    assert payload["stress_page_navigation_probe"]["visible_leaf_count"] == payload["stress_page_navigation_probe"]["expected_visible_overlay_count"]
    assert payload["stress_page_navigation_probe"]["stale_leaf_count"] == 0
    assert payload["stress_page_navigation_probe"]["full_tree_summary"]["overlay_load_worker_count"] >= 1
    assert payload["stress_page_navigation_probe"]["full_tree_summary"]["plan_build_worker_count"] >= 1
    assert payload["navigation_soak_probe"]["completed"] is True
    assert payload["navigation_soak_probe"]["completed_visit_count"] == 5
    assert payload["navigation_soak_probe"]["lightweight_scheduled_count"] >= 5
    assert payload["navigation_soak_probe"]["max_cache_pair_count"] <= payload["navigation_soak_probe"]["cache_pair_limit"]
    assert payload["p4_overlay_streaming_probe"]["declared_overlay_count"] == 100
    assert payload["p4_overlay_streaming_probe"]["materialized_overlay_count"] == 3
    assert payload["p4_overlay_streaming_probe"]["overlay_json_read_call_count"] == 0
    p5_probe = payload["p5_overlay_page_store_query_probe"]
    assert p5_probe["declared_overlay_count"] == 1024
    assert p5_probe["legacy_overlay_json_read_count"] == 0
    assert p5_probe["phase_results"]["first_visible"]["initial_source"] == "paged_overlay_store"
    assert p5_probe["phase_results"]["first_visible"]["materialized_overlay_count"] == 32
    assert p5_probe["phase_results"]["first_visible"]["page_file_read_count"] == 1
    assert p5_probe["phase_results"]["page_pair"]["overlay_load_strategy"] == "paged_overlay_store"
    assert p5_probe["phase_results"]["page_pair"]["legacy_overlay_json_read_count"] == 0
    assert p5_probe["phase_results"]["page_pair"]["page_file_read_count"] <= 2
    assert p5_probe["phase_results"]["page_pair"]["visible_leaf_count"] == 128
    assert p5_probe["phase_results"]["page_pair"]["cached_overlay_count"] == 0
    assert payload["p4_visible_tile_probe"]["generation_mode"] == "visible_first"
    assert payload["p4_visible_tile_probe"]["materialized_tile_count"] == 2
    assert payload["p4_visible_tile_probe"]["planned_tile_count"] == 32
    assert payload["p4_visible_tile_probe"]["outside_window_status"] == "tile_pending"
    assert payload["p4_visible_tile_probe"]["on_demand_materialized_tile_count"] == 4
    assert payload["p4_visible_tile_probe"]["on_demand_filled_status"] == "tile_ready"
    assert payload["p4_visible_tile_probe"]["on_demand_repeat_added_tile_count"] == 0
    assert payload["p5_tile_retention_probe"]["completed"] is True
    assert payload["p5_tile_retention_probe"]["eviction_count"] >= 1
    assert payload["p5_tile_retention_probe"]["retained_bytes"] <= payload["p5_tile_retention_probe"]["byte_limit"]
    assert payload["p5_tile_retention_probe"]["stale_manifest_count"] == 0
    assert payload["p5_tile_retention_probe"]["orphan_bytes"] == 0
    assert {gate["name"] for gate in payload["gates"]} >= {
        "cached_pdf_pair_selection_p95",
        "cold_pdf_pair_selection_p95",
        "first_review_no_full_tile_pyramid",
        "overlay_cache_pair_bound",
        "overlay_cache_byte_bound",
        "overlay_rss_tail_delta_after_cache_limit",
        "full_tree_rebuild_completed",
        "full_tree_initial_call_ms",
        "full_tree_overlay_load_worker",
        "full_tree_plan_build_worker",
        "full_tree_rebuild_chunk_count",
        "full_tree_rebuild_max_chunk_ms",
        "full_tree_event_loop_gap_p95",
        "page_navigation_completed",
        "page_navigation_initial_call_ms",
        "page_navigation_overlay_deferred",
        "page_navigation_overlay_load_worker",
        "page_navigation_plan_build_worker",
        "page_navigation_lightweight_scheduled",
        "page_navigation_visible_leaf_count",
        "page_navigation_event_loop_gap_p95",
        "rapid_page_navigation_completed",
        "rapid_page_navigation_max_step_call_ms",
        "rapid_page_navigation_final_page",
        "rapid_page_navigation_visible_leaf_count",
        "rapid_page_navigation_no_stale_leaf",
        "rapid_page_navigation_overlay_load_worker",
        "rapid_page_navigation_plan_build_worker",
        "rapid_page_navigation_lightweight_scheduled",
        "rapid_page_navigation_event_loop_gap_p95",
        "stress_page_navigation_completed",
        "stress_page_navigation_max_step_call_ms",
        "stress_page_navigation_event_loop_gap_p95",
        "stress_page_navigation_visible_leaf_count",
        "stress_page_navigation_no_stale_leaf",
        "stress_page_navigation_overlay_load_worker",
        "stress_page_navigation_plan_build_worker",
        "stress_page_navigation_chunk_count",
        "stress_page_navigation_rss_delta_mb",
        "navigation_soak_completed",
        "navigation_soak_completed_visit_count",
        "navigation_soak_lightweight_scheduled",
        "navigation_soak_selection_p95_ms",
        "navigation_soak_event_loop_gap_p95_ms",
        "navigation_soak_event_loop_gap_max_ms",
        "navigation_soak_event_loop_over_500ms_count",
        "navigation_soak_cache_pair_bound",
        "navigation_soak_cache_byte_bound",
        "navigation_soak_rss_slope_mb_per_100_visits",
        "navigation_soak_rss_positive_end_delta_mb",
        "navigation_soak_rss_tail_peak_delta_mb",
        "p4_overlay_first_paint_no_full_json",
        "p4_overlay_first_paint_materialized_cap",
        "p4_overlay_declared_100k_fixture",
        "p5_page_store_fixture_declared_overlay_count",
        "p5_first_visible_no_legacy_overlay_json",
        "p5_first_visible_sparse_page_reads",
        "p5_first_visible_materialized_cap",
        "p5_first_visible_no_full_overlay_cache",
        "p5_page_pair_uses_paged_overlay_store",
        "p5_page_pair_no_legacy_overlay_json",
        "p5_page_pair_sparse_page_reads",
        "p5_page_pair_materialized_visible_only",
        "p5_page_pair_visible_leaf_count",
        "p5_page_pair_no_stale_leaf",
        "p5_page_pair_no_full_overlay_cache",
        "p4_visible_tiles_partial_not_full",
        "p4_visible_tiles_materialized_cap",
        "p4_visible_tiles_outside_pending",
        "p4_on_demand_tiles_accumulates_windows",
        "p4_on_demand_tiles_fills_requested_window",
        "p4_on_demand_tiles_dedupes_repeat_window",
        "p4_on_demand_no_full_pyramid",
        "p5_tile_retention_completed",
        "p5_tile_cache_byte_bound",
        "p5_tile_cache_eviction_count_min",
        "p5_tile_cache_evicted_bytes_positive",
        "p5_tile_cache_orphan_bytes_zero",
        "p5_tile_cache_stale_manifest_count_zero",
        "p5_tile_cache_hot_pair_retained",
        "p5_tile_cache_evicted_pair_miss",
        "p5_tile_cache_prune_p95_ms",
        "p5_tile_cache_event_loop_gap_p95_ms",
        "p5_tile_cache_event_loop_over_500ms_count",
    }
    stdout_payload = json.loads([line for line in buf.getvalue().splitlines() if line.strip()][-1])
    assert stdout_payload["status"] == payload["status"]
