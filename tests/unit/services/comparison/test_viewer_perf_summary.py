# -*- coding: utf-8 -*-
"""Unit tests for viewer_perf.json aggregation used by the Workbench status line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.viewer_perf_summary import (
    VIEWER_PERF_FILENAME,
    VIEWER_PERF_JSONL_FILENAME,
    format_viewer_perf_summary_korean,
    summarize_viewer_perf,
)


def _write_perf(viewer_root: Path, events: list[dict]) -> None:
    viewer_root.mkdir(parents=True, exist_ok=True)
    (viewer_root / VIEWER_PERF_FILENAME).write_text(
        json.dumps({"schema_version": 1, "event_count": len(events), "events": events}),
        encoding="utf-8",
    )


def _write_perf_jsonl(viewer_root: Path, events: list[dict]) -> None:
    viewer_root.mkdir(parents=True, exist_ok=True)
    (viewer_root / VIEWER_PERF_JSONL_FILENAME).write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )


def test_summary_returns_missing_status_when_root_is_none() -> None:
    summary = summarize_viewer_perf(None)
    assert summary["status"] == "missing"
    assert summary["event_count"] == 0
    assert summary["summary_source"] == "none"
    assert summary["summary_input_bytes"] == 0
    assert summary["summary_elapsed_ms"] >= 0.0


def test_summary_returns_missing_status_when_file_absent(tmp_path: Path) -> None:
    summary = summarize_viewer_perf(tmp_path)
    assert summary["status"] == "missing"
    assert summary["summary_source"] == "none"
    assert summary["summary_input_bytes"] == 0
    assert summary["summary_elapsed_ms"] >= 0.0


def test_summary_returns_package_only_when_no_viewport_events(tmp_path: Path) -> None:
    # Backend tile-write events without any viewport interaction — surfaces as
    # ``package_only`` so the operator can tell the GUI was never used.
    _write_perf(tmp_path, [{"event": "package_tile_write", "tile_count": 4}])
    summary = summarize_viewer_perf(tmp_path)
    assert summary["status"] == "package_only"
    assert summary["viewport_model_count"] == 0
    assert summary["event_count"] == 1
    assert summary["summary_source"] == "legacy_json"
    assert summary["summary_input_bytes"] > 0
    assert summary["summary_elapsed_ms"] >= 0.0


def test_summary_returns_empty_when_event_list_is_empty(tmp_path: Path) -> None:
    _write_perf(tmp_path, [])
    summary = summarize_viewer_perf(tmp_path)
    assert summary["status"] == "empty"
    assert summary["event_count"] == 0
    assert summary["summary_source"] == "legacy_json"
    assert summary["summary_input_bytes"] > 0
    assert summary["summary_elapsed_ms"] >= 0.0


def test_summary_computes_cache_hit_rate_and_percentiles(tmp_path: Path) -> None:
    events = [
        {"event": "viewport_model", "tile_count": 0, "cull_ms": 1.0, "overlay_model_count": 5},
        {"event": "viewport_model", "tile_count": 4, "cull_ms": 2.0, "overlay_model_count": 6},
        {"event": "viewport_model", "tile_count": 8, "cull_ms": 3.0, "overlay_model_count": 7},
        {"event": "viewport_model", "tile_count": 2, "cull_ms": 4.0, "overlay_model_count": 8},
        {"event": "viewport_model", "tile_count": 1, "cull_ms": 9.0, "overlay_model_count": 9},
    ]
    _write_perf(tmp_path, events)
    summary = summarize_viewer_perf(tmp_path)
    assert summary["status"] == "ready"
    assert summary["event_count"] == 5
    assert summary["viewport_model_count"] == 5
    # 4 of 5 events have tile_count > 0
    assert summary["cache_hit_rate"] == pytest.approx(0.8)
    cull = summary["cull_ms"]
    assert cull["mean"] == pytest.approx(3.8)
    # Linear-interpolation percentile across [1, 2, 3, 4, 9]
    assert cull["p50"] == pytest.approx(3.0)
    # p95 = sample[3] + 0.8 * (sample[4] - sample[3]) = 4 + 0.8 * 5 = 8.0
    assert cull["p95"] == pytest.approx(8.0)
    assert summary["overlay_model_avg"] == pytest.approx(7.0)
    assert summary["tile_count_avg"] == pytest.approx(3.0)


def test_summary_prefers_jsonl_over_legacy_pointer(tmp_path: Path) -> None:
    _write_perf(
        tmp_path,
        [{"event": "viewport_model", "tile_count": 0, "cull_ms": 999.0, "overlay_model_count": 1}],
    )
    _write_perf_jsonl(
        tmp_path,
        [
            {"event": "viewport_model", "tile_count": 1, "cull_ms": 1.0, "overlay_model_count": 3},
            {"event": "viewport_model", "tile_count": 2, "cull_ms": 3.0, "overlay_model_count": 5},
        ],
    )

    summary = summarize_viewer_perf(tmp_path)

    assert summary["status"] == "ready"
    assert summary["event_count"] == 2
    assert summary["cache_hit_rate"] == pytest.approx(1.0)
    assert summary["cull_ms"]["mean"] == pytest.approx(2.0)
    assert summary["overlay_model_avg"] == pytest.approx(4.0)
    assert summary["summary_source"] == "jsonl"
    assert summary["summary_input_bytes"] > 0
    assert summary["summary_elapsed_ms"] >= 0.0


def test_summary_aggregates_native_resource_fields_from_events(tmp_path: Path) -> None:
    _write_perf_jsonl(
        tmp_path,
        [
            {
                "event": "pair_selection_initial_load",
                "native_resource_available": True,
                "native_resource_sample_count": 1,
                "process_handle_count": 20,
                "open_file_descriptor_count": 4,
                "gdi_handle_count": 6,
                "user_handle_count": 7,
                "worker_process_count": 1,
            },
            {
                "event": "zone_selection",
                "native_resource_available": True,
                "native_resource_sample_count": 3,
                "process_handle_count": 25,
                "open_file_descriptor_count": 5,
                "gdi_handle_count": 5,
                "user_handle_count": 8,
                "worker_process_count": 0,
            },
        ],
    )

    summary = summarize_viewer_perf(tmp_path)

    assert summary["status"] == "ready"
    assert summary["native_resource_available"] is True
    assert summary["native_resource_sample_count"] == 3
    assert summary["process_handle_count_max"] == 25
    assert summary["open_file_descriptor_count_max"] == 5
    assert summary["gdi_handle_count_max"] == 6
    assert summary["user_handle_count_max"] == 8
    assert summary["worker_process_count_max"] == 1


def test_summary_skips_malformed_jsonl_lines(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / VIEWER_PERF_JSONL_FILENAME).write_text(
        "\n".join(
            [
                json.dumps({"event": "package_tile_write", "tile_count": 4}),
                "not-json",
                json.dumps({"event": "viewport_model", "tile_count": 1, "cull_ms": 2.0}),
            ]
        ),
        encoding="utf-8",
    )

    summary = summarize_viewer_perf(tmp_path)

    assert summary["status"] == "ready"
    assert summary["event_count"] == 2
    assert summary["viewport_model_count"] == 1


def test_summary_falls_back_to_legacy_when_jsonl_has_no_valid_events(tmp_path: Path) -> None:
    _write_perf(tmp_path, [{"event": "viewport_model", "tile_count": 3, "cull_ms": 5.0}])
    (tmp_path / VIEWER_PERF_JSONL_FILENAME).write_text(
        "\n\nnot-json\n",
        encoding="utf-8",
    )

    summary = summarize_viewer_perf(tmp_path)

    assert summary["status"] == "ready"
    assert summary["event_count"] == 1
    assert summary["tile_count_avg"] == pytest.approx(3.0)
    assert summary["summary_source"] == "legacy_json"
    assert summary["summary_input_bytes"] > 0


def test_summary_filters_out_non_viewport_events_from_latency(tmp_path: Path) -> None:
    events = [
        {"event": "package_tile_write", "tile_count": 100, "cull_ms": 999.0},
        {"event": "viewport_model", "tile_count": 1, "cull_ms": 1.0, "overlay_model_count": 1},
    ]
    _write_perf(tmp_path, events)
    summary = summarize_viewer_perf(tmp_path)
    assert summary["status"] == "ready"
    assert summary["viewport_model_count"] == 1
    assert summary["cull_ms"]["p50"] == pytest.approx(1.0)
    assert summary["cull_ms"]["mean"] == pytest.approx(1.0)


def test_summary_computes_selected_zone_crop_latency(tmp_path: Path) -> None:
    events = [
        {"event": "zone_crop_render", "cache_hit": False, "render_ms": 4000.0, "render_lifecycle": "ready", "visual_fidelity": "cad_render", "renderer_backend": "cad-background-image-crop"},
        {"event": "zone_crop_render", "cache_hit": False, "render_ms": 9000.0, "render_lifecycle": "ready", "visual_fidelity": "cad_render", "renderer_backend": "cad-background-image-crop"},
        {"event": "zone_crop_render", "cache_hit": True, "render_ms": 250.0, "render_lifecycle": "ready", "visual_fidelity": "pdf_render", "renderer_backend": "pdf-image-crop"},
        {"event": "zone_crop_render", "cache_hit": True, "render_ms": 500.0, "render_lifecycle": "skipped_missing_page_bbox", "visual_fidelity": "relative_overlay", "renderer_backend": "pdf-page-bbox-required", "reason_code": "missing_page_bbox"},
    ]
    _write_perf(tmp_path, events)
    summary = summarize_viewer_perf(tmp_path)

    assert summary["status"] == "ready"
    assert summary["zone_crop_count"] == 4
    assert summary["zone_crop_cache_hit_rate"] == pytest.approx(0.5)
    assert summary["zone_crop_cold_ms"]["p95"] == pytest.approx(8750.0)
    assert summary["zone_crop_cache_hit_ms"]["p95"] == pytest.approx(487.5)
    assert summary["render_lifecycle_counts"]["ready"] == 3
    assert summary["render_lifecycle_counts"]["skipped_missing_page_bbox"] == 1
    assert summary["fidelity_counts"]["cad_render"] == 2
    assert summary["fidelity_counts"]["pdf_render"] == 1
    assert summary["renderer_backend_counts"]["cad-background-image-crop"] == 2
    assert summary["renderer_backend_counts"]["pdf-page-bbox-required"] == 1
    assert summary["reason_code_counts"]["missing_page_bbox"] == 1


def test_summary_aggregates_pdf_display_list_cache_telemetry(tmp_path: Path) -> None:
    events = [
        {
            "event": "zone_crop_render",
            "cache_hit": False,
            "render_ms": 1200.0,
            "render_lifecycle": "ready",
            "visual_fidelity": "pdf_render",
            "renderer_backend": "pdf-image-crop",
            "pdf_display_list_render_count": 2,
            "pdf_display_list_cache_lookup_count": 2,
            "pdf_display_list_cache_hit_count": 1,
            "pdf_display_list_cache_miss_count": 1,
            "pdf_display_list_cache_eviction_count": 1,
            "pdf_display_list_cache_total_estimated_bytes": 5 * 1024 * 1024,
            "pdf_display_list_cache_byte_limit": 10 * 1024 * 1024,
            "pdf_display_list_worker_rss_mb": 88.5,
            "pdf_pil_fallback_count": 0,
        },
        {
            "event": "zone_crop_render",
            "cache_hit": False,
            "render_ms": 900.0,
            "render_lifecycle": "ready",
            "visual_fidelity": "pdf_render",
            "renderer_backend": "pdf-image-crop",
            "warnings": ["renderer:pdf-pil-fallback"],
        },
    ]
    _write_perf_jsonl(tmp_path, events)

    summary = summarize_viewer_perf(tmp_path)

    assert summary["pdf_display_list_render_count"] == 2
    assert summary["pdf_display_list_cache_lookup_count"] == 2
    assert summary["pdf_display_list_cache_hit_count"] == 1
    assert summary["pdf_display_list_cache_miss_count"] == 1
    assert summary["pdf_display_list_cache_hit_rate"] == pytest.approx(0.5)
    assert summary["pdf_display_list_cache_eviction_count"] == 1
    assert summary["pdf_display_list_cache_max_total_bytes"] == 5 * 1024 * 1024
    assert summary["pdf_display_list_cache_byte_limit"] == 10 * 1024 * 1024
    assert summary["pdf_display_list_worker_rss_mb_max"] == pytest.approx(88.5)
    assert summary["pdf_pil_fallback_count"] == 1
    rendered = format_viewer_perf_summary_korean(summary)
    assert "PDF DL 2회" in rendered
    assert "PDF PIL fallback 1회" in rendered


def test_summary_aggregates_dxf_index_cache_telemetry(tmp_path: Path) -> None:
    events = [
        {
            "event": "zone_crop_render",
            "cache_hit": False,
            "render_ms": 1200.0,
            "render_lifecycle": "ready",
            "visual_fidelity": "cad_render",
            "renderer_backend": "ezdxf-matplotlib-zone",
            "dxf_index_cache_entries": 2,
            "dxf_index_cache_capacity_entries": 8,
            "dxf_index_cache_entry_estimated_bytes_max": 2 * 1024 * 1024,
            "dxf_index_cache_lookup_count": 2,
            "dxf_index_cache_hit_count": 1,
            "dxf_index_cache_miss_count": 1,
            "dxf_index_cache_eviction_count": 0,
            "dxf_index_cache_evicted_estimated_bytes": 0,
            "dxf_index_cache_total_estimated_bytes": 3 * 1024 * 1024,
            "dxf_index_cache_byte_limit": 8 * 1024 * 1024,
            "dxf_index_cache_worker_rss_mb": 96.25,
        },
        {
            "event": "zone_crop_render",
            "cache_hit": False,
            "render_ms": 900.0,
            "render_lifecycle": "ready",
            "visual_fidelity": "cad_render",
            "renderer_backend": "ezdxf-matplotlib-zone",
            "dxf_index_cache_entries": 1,
            "dxf_index_cache_capacity_entries": 8,
            "dxf_index_cache_lookup_count": 2,
            "dxf_index_cache_hit_count": 0,
            "dxf_index_cache_miss_count": 2,
            "dxf_index_cache_eviction_count": 1,
            "dxf_index_cache_evicted_estimated_bytes": 1024 * 1024,
            "dxf_index_cache_total_estimated_bytes": 4 * 1024 * 1024,
            "dxf_index_cache_byte_limit": 8 * 1024 * 1024,
            "dxf_index_cache_worker_rss_mb": 99.5,
        },
    ]
    _write_perf_jsonl(tmp_path, events)

    summary = summarize_viewer_perf(tmp_path)

    assert summary["dxf_index_cache_entries_max"] == 2
    assert summary["dxf_index_cache_capacity_entries"] == 8
    assert summary["dxf_index_cache_entry_estimated_bytes_max"] == 2 * 1024 * 1024
    assert summary["dxf_index_cache_lookup_count"] == 4
    assert summary["dxf_index_cache_hit_count"] == 1
    assert summary["dxf_index_cache_miss_count"] == 3
    assert summary["dxf_index_cache_hit_rate"] == pytest.approx(0.25)
    assert summary["dxf_index_cache_eviction_count"] == 1
    assert summary["dxf_index_cache_evicted_estimated_bytes"] == 1024 * 1024
    assert summary["dxf_index_cache_max_total_bytes"] == 4 * 1024 * 1024
    assert summary["dxf_index_cache_byte_limit"] == 8 * 1024 * 1024
    assert summary["dxf_index_cache_worker_rss_mb_max"] == pytest.approx(99.5)
    rendered = format_viewer_perf_summary_korean(summary)
    assert "DXF idx 4회" in rendered


def test_summary_computes_backend_gui_and_selection_health_telemetry(tmp_path: Path) -> None:
    events = [
        {"event": "package_background_render", "render_ms": 1000.0, "worker_spawned": True, "render_status": "rendered"},
        {
            "event": "package_tile_write",
            "tile_cache_hit": False,
            "cache_lookup_ms": 2.0,
            "tile_write_ms": 80.0,
            "tile_pyramid_ms": 50.0,
            "overlay_tile_ms": 20.0,
            "tile_cache_write_ms": 75.0,
            "tile_payload_bytes": 1000,
            "overlay_tile_payload_bytes": 400,
            "cache_total_estimated_bytes": 1400,
            "cache_retained_estimated_bytes": 1400,
            "cache_byte_limit": 10_000,
            "eviction_count": 0,
        },
        {
            "event": "package_tile_write",
            "tile_cache_hit": True,
            "cache_lookup_ms": 1.0,
            "tile_write_ms": 0.0,
            "tile_pyramid_ms": 0.0,
            "overlay_tile_ms": 0.0,
            "tile_cache_write_ms": 0.0,
            "cache_total_estimated_bytes": 1800,
            "cache_retained_estimated_bytes": 1500,
            "cache_byte_limit": 10_000,
            "eviction_count": 1,
            "evicted_pair_count": 1,
            "evicted_estimated_bytes": 300,
            "eviction_reason": "byte_limit",
        },
        {"event": "tiles_manifest_materialise", "materialise_ms": 5.0, "target": "viewer"},
        {
            "event": "pair_render",
            "render_ms": 250.0,
            "tile_ms": 40.0,
            "tile_cache_attempted": True,
            "tile_cache_hit": True,
        },
        {
            "event": "pair_selection_initial_load",
            "elapsed_ms": 130.0,
            "gui_block_ms": 120.0,
            "overlay_cache_total_bytes": 2000,
            "overlay_cache_byte_limit": 5000,
        },
        {
            "event": "pdf_page_navigation",
            "gui_block_ms": 9.0,
            "page_a": 1,
            "page_b": 1,
            "overlay_load_deferred": True,
        },
        {
            "event": "full_zone_tree_rebuild",
            "elapsed_ms": 240.0,
            "overlay_count": 1200,
            "visible_overlay_count": 500,
            "chunked": True,
            "chunk_count": 8,
            "max_chunk_elapsed_ms": 12.5,
            "tree_item_count": 620,
            "overlay_load_ms": 33.0,
            "overlay_json_bytes": 123456,
            "overlay_load_worker": True,
            "plan_build_ms": 17.5,
            "plan_build_worker": True,
        },
        {
            "event": "viewer_overlay_cache_evict",
            "overlay_cache_total_bytes": 2400,
            "overlay_cache_byte_limit": 5000,
            "overlay_cache_pair_limit": 8,
        },
        {
            "event": "lightweight_pair_load",
            "input_format": "pdf",
            "load_ms": 90.0,
            "pdf_cache_state": "all_cached",
            "before_metadata_hit": True,
            "after_metadata_hit": True,
        },
        {
            "event": "lightweight_pair_load",
            "input_format": "pdf",
            "load_ms": 900.0,
            "pdf_cache_state": "all_cold",
        },
        {
            "event": "lightweight_pdf_prewarm",
            "elapsed_ms": 120.0,
            "ok_count": 2,
            "rendered_count": 1,
            "cache_hit_count": 1,
            "metadata_hit_count": 0,
        },
        {"event": "zone_selection", "gui_block_ms": 30.0},
        {"event": "zone_render_stale", "reason_code": "inactive_pair_result"},
        {"event": "zone_render_pending_replaced"},
        {"event": "zone_render_fallback", "visual_fidelity": "relative_overlay", "reason_code": "missing_cad_bbox"},
    ]
    _write_perf_jsonl(tmp_path, events)

    summary = summarize_viewer_perf(tmp_path)

    assert summary["status"] == "ready"
    assert summary["package_background_render_count"] == 1
    assert summary["pair_render_count"] == 1
    assert summary["worker_spawned_count"] == 1
    assert summary["tile_cache_event_count"] == 3
    assert summary["tile_cache_hit_count"] == 2
    assert summary["tile_cache_miss_count"] == 1
    assert summary["tile_cache_hit_rate"] == pytest.approx(2 / 3, abs=0.0001)
    assert summary["package_background_render_ms"]["p95"] == pytest.approx(1000.0)
    assert summary["tile_cache_write_ms"]["p95"] == pytest.approx(71.25)
    assert summary["tile_cache_lookup_ms"]["mean"] == pytest.approx(1.5)
    assert summary["pair_selection_count"] == 1
    assert summary["pdf_page_navigation_count"] == 1
    assert summary["pdf_page_navigation_deferred_count"] == 1
    assert summary["pdf_page_navigation_gui_block_ms"]["p95"] == pytest.approx(9.0)
    assert summary["full_tree_rebuild_count"] == 1
    assert summary["full_tree_rebuild_ms"]["p95"] == pytest.approx(240.0)
    assert summary["full_tree_rebuild_max_overlay_count"] == 1200
    assert summary["full_tree_rebuild_chunked_count"] == 1
    assert summary["full_tree_rebuild_chunk_count"]["p95"] == pytest.approx(8.0)
    assert summary["full_tree_rebuild_max_chunk_ms"]["p95"] == pytest.approx(12.5)
    assert summary["full_tree_rebuild_tree_item_count_max"] == 620
    assert summary["full_tree_overlay_json_load_ms"]["p95"] == pytest.approx(33.0)
    assert summary["full_tree_plan_build_ms"]["p95"] == pytest.approx(17.5)
    assert summary["full_tree_overlay_json_bytes_max"] == 123456
    assert summary["full_tree_overlay_load_worker_count"] == 1
    assert summary["full_tree_plan_build_worker_count"] == 1
    assert summary["lightweight_pair_load_count"] == 2
    assert summary["lightweight_pair_load_ms"]["p95"] == pytest.approx(859.5)
    assert summary["lightweight_pdf_cached_load_ms"]["p95"] == pytest.approx(90.0)
    assert summary["lightweight_pdf_cold_load_ms"]["p95"] == pytest.approx(900.0)
    assert summary["lightweight_pdf_cache_state_counts"]["all_cached"] == 1
    assert summary["lightweight_pdf_cache_state_counts"]["all_cold"] == 1
    assert summary["lightweight_pdf_metadata_hit_count"] == 2
    assert summary["lightweight_pdf_prewarm_count"] == 1
    assert summary["lightweight_pdf_prewarm_ms"]["p95"] == pytest.approx(120.0)
    assert summary["lightweight_pdf_prewarm_ok_count"] == 2
    assert summary["lightweight_pdf_prewarm_rendered_count"] == 1
    assert summary["lightweight_pdf_prewarm_cache_hit_count"] == 1
    assert summary["zone_selection_count"] == 1
    assert summary["pair_selection_gui_block_ms"]["p95"] == pytest.approx(120.0)
    assert summary["zone_selection_gui_block_ms"]["p95"] == pytest.approx(30.0)
    assert summary["overlay_cache_eviction_count"] == 1
    assert summary["overlay_cache_max_total_bytes"] == 2400
    assert summary["overlay_cache_byte_limit"] == 5000
    assert summary["overlay_cache_pair_limit"] == 8
    assert summary["tile_cache_max_payload_bytes"] == 1800
    assert summary["tile_cache_retained_estimated_bytes"] == 1500
    assert summary["tile_cache_byte_limit"] == 10_000
    assert summary["tile_cache_eviction_count"] == 1
    assert summary["tile_cache_evicted_pair_count"] == 1
    assert summary["tile_cache_evicted_estimated_bytes"] == 300
    assert summary["tile_cache_eviction_reason_counts"]["byte_limit"] == 1
    assert "타일캐시 evict 1쌍/0.0MB" in format_viewer_perf_summary_korean(summary)
    assert summary["selected_zone_stale_count"] == 1
    assert summary["selected_zone_cancel_count"] == 1
    assert summary["selected_zone_fallback_count"] == 1
    assert summary["fidelity_counts"]["relative_overlay"] == 1
    assert summary["reason_code_counts"]["missing_cad_bbox"] == 1


def test_summary_handles_corrupt_json_gracefully(tmp_path: Path) -> None:
    (tmp_path / VIEWER_PERF_FILENAME).write_text("not json", encoding="utf-8")
    summary = summarize_viewer_perf(tmp_path)
    assert summary["status"] == "missing"
    assert summary["summary_source"] == "none"
    assert summary["summary_input_bytes"] == 0
    assert summary["summary_elapsed_ms"] >= 0.0


def test_format_summary_in_korean_for_ready_status() -> None:
    summary = {
        "status": "ready",
        "cache_hit_rate": 0.842,
        "cull_ms": {"p50": 1.2, "p95": 4.7, "p99": 9.0, "mean": 2.1},
        "overlay_model_avg": 12.5,
        "tile_count_avg": 3.7,
    }
    rendered = format_viewer_perf_summary_korean(summary)
    assert "캐시 적중 84.2%" in rendered
    assert "p50 1.2ms" in rendered
    assert "p95 4.7ms" in rendered
    # Python's banker's rounding makes 12.5 → 12; rely on the substring being present.
    assert "오버레이 평균" in rendered
    assert "타일 평균 3.7" in rendered


def test_format_summary_includes_selected_zone_p95_when_present() -> None:
    summary = {
        "status": "ready",
        "cache_hit_rate": 0.0,
        "cull_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "overlay_model_avg": 0.0,
        "tile_count_avg": 0.0,
        "zone_crop_count": 3,
        "zone_crop_cold_ms": {"p95": 9200.0},
        "zone_crop_cache_hit_ms": {"p95": 450.0},
    }
    rendered = format_viewer_perf_summary_korean(summary)
    assert "선택구역 3건" in rendered
    assert "cold p95 9.20s" in rendered
    assert "hit p95 0.45s" in rendered


@pytest.mark.parametrize("status", ["missing", "empty"])
def test_format_summary_indicates_when_data_unavailable(status: str) -> None:
    rendered = format_viewer_perf_summary_korean({"status": status})
    assert "성능 데이터 없음" in rendered


def test_format_summary_explains_package_only_status() -> None:
    rendered = format_viewer_perf_summary_korean({"status": "package_only", "event_count": 3})
    assert "백엔드 이벤트 3건" in rendered
    assert "GUI 미사용" in rendered
