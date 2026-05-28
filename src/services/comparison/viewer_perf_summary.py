# -*- coding: utf-8 -*-
"""Aggregate ``viewer_perf.json`` events into a UI-friendly summary.

The Workbench writes a per-event log via :func:`append_viewer_perf_event` (see
``viewer_tile_cache``). For customer pilots we need a small, computable summary
that the GUI can show in a single status line and the validation pipeline can
embed in ``validation_summary.json``. The format is intentionally compact so it
can be diffed across runs.

Public entry point:
- :func:`summarize_viewer_perf` — read events.json from a viewer root and return
  a dict with cache-hit and latency statistics.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Iterable, Optional

VIEWER_PERF_SUMMARY_SCHEMA_VERSION = 9
VIEWER_PERF_FILENAME = "viewer_perf.json"
VIEWER_PERF_JSONL_FILENAME = "viewer_perf.jsonl"


def summarize_viewer_perf(viewer_root: Optional[Path]) -> dict[str, Any]:
    """Read ``viewer/viewer_perf.json`` from ``viewer_root`` and return a summary.

    Returns a dict with the following keys (all numbers, never None) so the GUI
    can format it without conditional checks:

    - ``schema_version`` — bumped when the summary shape changes
    - ``event_count`` — total events recorded
    - ``viewport_model_count`` — events whose ``event`` field is ``viewport_model``
    - ``cache_hit_rate`` — fraction of viewport_model events with non-empty tiles
    - ``cull_ms`` — dict of ``p50``/``p95``/``p99`` and ``mean`` of ``cull_ms``
    - ``overlay_model_avg`` — mean of ``overlay_model_count``
    - ``tile_count_avg`` — mean of ``tile_count``
    - ``zone_crop_*`` — selected-zone render latency/cache-hit summaries
    - ``status`` — ``ready`` | ``empty`` | ``missing`` (so the UI can show a hint)
    """

    started = time.perf_counter()
    summary = _empty_summary()
    if viewer_root is None:
        summary["status"] = "missing"
        return _finalize_summary_overhead(summary, started)
    root = Path(viewer_root)
    jsonl_path = root / VIEWER_PERF_JSONL_FILENAME
    json_path = root / VIEWER_PERF_FILENAME
    if not jsonl_path.exists() and not json_path.exists():
        summary["status"] = "missing"
        return _finalize_summary_overhead(summary, started)
    use_jsonl = jsonl_path.exists() and _jsonl_has_valid_event(jsonl_path)
    if not use_jsonl and json_path.exists():
        try:
            json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary["status"] = "missing"
            return _finalize_summary_overhead(summary, started)
    if use_jsonl:
        summary["summary_source"] = "jsonl"
        summary["summary_input_bytes"] = _file_size(jsonl_path)
    elif json_path.exists():
        summary["summary_source"] = "legacy_json"
        summary["summary_input_bytes"] = _file_size(json_path)

    event_count = 0
    viewport_count = 0
    viewport_cache_hits = 0
    cull_values: list[float] = []
    overlay_sum = 0.0
    overlay_count = 0
    tile_sum = 0.0
    tile_value_count = 0
    zone_crop_count = 0
    zone_crop_cache_hits = 0
    zone_crop_all_ms: list[float] = []
    zone_crop_hit_ms: list[float] = []
    zone_crop_cold_ms: list[float] = []
    render_lifecycle_counts: dict[str, int] = {}
    fidelity_counts: dict[str, int] = {}
    renderer_backend_counts: dict[str, int] = {}
    reason_code_counts: dict[str, int] = {}
    package_background_render_count = 0
    package_background_render_ms: list[float] = []
    pair_render_count = 0
    pair_render_ms: list[float] = []
    tile_build_ms: list[float] = []
    tile_pyramid_ms: list[float] = []
    overlay_tile_ms: list[float] = []
    tile_cache_write_ms: list[float] = []
    tile_cache_lookup_ms: list[float] = []
    tile_cache_event_count = 0
    tile_cache_hit_count = 0
    tile_cache_max_payload_bytes = 0
    tile_cache_retained_estimated_bytes = 0
    tile_cache_byte_limit = 0
    tile_cache_eviction_count = 0
    tile_cache_evicted_pair_count = 0
    tile_cache_evicted_estimated_bytes = 0
    tile_cache_eviction_reason_counts: dict[str, int] = {}
    tile_overlay_count_max = 0
    tile_materialized_overlay_count_max = 0
    tile_omitted_overlay_count_max = 0
    tiles_manifest_materialise_ms: list[float] = []
    worker_spawned_count = 0
    pair_selection_count = 0
    pair_selection_gui_block_ms: list[float] = []
    pdf_page_navigation_count = 0
    pdf_page_navigation_gui_block_ms: list[float] = []
    pdf_page_navigation_deferred_count = 0
    full_tree_rebuild_count = 0
    full_tree_rebuild_ms: list[float] = []
    full_tree_rebuild_max_overlay_count = 0
    full_tree_rebuild_chunked_count = 0
    full_tree_rebuild_chunk_counts: list[float] = []
    full_tree_rebuild_max_chunk_ms: list[float] = []
    full_tree_rebuild_tree_item_count_max = 0
    full_tree_overlay_json_load_ms: list[float] = []
    full_tree_plan_build_ms: list[float] = []
    full_tree_overlay_json_bytes_max = 0
    full_tree_overlay_load_worker_count = 0
    full_tree_plan_build_worker_count = 0
    lightweight_pair_load_count = 0
    lightweight_pair_load_ms: list[float] = []
    lightweight_pdf_cached_ms: list[float] = []
    lightweight_pdf_cold_ms: list[float] = []
    lightweight_pdf_cache_state_counts: dict[str, int] = {}
    lightweight_pdf_metadata_hit_count = 0
    lightweight_pdf_prewarm_count = 0
    lightweight_pdf_prewarm_ms: list[float] = []
    lightweight_pdf_prewarm_ok_count = 0
    lightweight_pdf_prewarm_rendered_count = 0
    lightweight_pdf_prewarm_cache_hit_count = 0
    lightweight_pdf_prewarm_metadata_hit_count = 0
    zone_selection_count = 0
    zone_selection_gui_block_ms: list[float] = []
    selected_zone_stale_count = 0
    selected_zone_cancel_count = 0
    selected_zone_fallback_count = 0
    pdf_display_list_render_count = 0
    pdf_display_list_cache_lookup_count = 0
    pdf_display_list_cache_hit_count = 0
    pdf_display_list_cache_miss_count = 0
    pdf_display_list_cache_eviction_count = 0
    pdf_display_list_cache_max_total_bytes = 0
    pdf_display_list_cache_byte_limit = 0
    pdf_display_list_worker_rss_mb_max = 0.0
    pdf_pil_fallback_count = 0
    dxf_index_cache_entries_max = 0
    dxf_index_cache_capacity_entries = 0
    dxf_index_cache_entry_estimated_bytes_max = 0
    dxf_index_cache_lookup_count = 0
    dxf_index_cache_hit_count = 0
    dxf_index_cache_miss_count = 0
    dxf_index_cache_eviction_count = 0
    dxf_index_cache_evicted_estimated_bytes = 0
    dxf_index_cache_max_total_bytes = 0
    dxf_index_cache_byte_limit = 0
    dxf_index_cache_worker_rss_mb_max = 0.0
    overlay_cache_eviction_count = 0
    overlay_cache_max_total_bytes = 0
    overlay_cache_byte_limit = 0
    overlay_cache_pair_limit = 0
    native_resource_sample_count = 0
    native_resource_available = False
    process_handle_count_max = 0
    open_file_descriptor_count_max = 0
    gdi_handle_count_max = 0
    user_handle_count_max = 0
    worker_process_count_max = 0

    for event_payload in _iter_viewer_perf_events(root, use_jsonl=use_jsonl):
        event_count += 1
        event_name = event_payload.get("event")
        if _to_bool(event_payload.get("native_resource_available")):
            native_resource_available = True
        native_resource_sample_count = max(
            native_resource_sample_count,
            _to_int(event_payload.get("native_resource_sample_count")),
        )
        process_handle_count_max = max(
            process_handle_count_max,
            _to_int(event_payload.get("process_handle_count")),
        )
        open_file_descriptor_count_max = max(
            open_file_descriptor_count_max,
            _to_int(event_payload.get("open_file_descriptor_count")),
        )
        gdi_handle_count_max = max(
            gdi_handle_count_max,
            _to_int(event_payload.get("gdi_handle_count")),
        )
        user_handle_count_max = max(
            user_handle_count_max,
            _to_int(event_payload.get("user_handle_count")),
        )
        worker_process_count_max = max(
            worker_process_count_max,
            _to_int(event_payload.get("worker_process_count")),
        )
        if _to_bool(event_payload.get("worker_spawned")):
            worker_spawned_count += 1
        if event_name == "viewport_model":
            viewport_count += 1
            tile_count = _to_int(event_payload.get("tile_count"))
            if tile_count > 0:
                viewport_cache_hits += 1
            if event_payload.get("cull_ms") is not None:
                cull_ms = _to_float(event_payload.get("cull_ms"))
                if cull_ms >= 0:
                    cull_values.append(cull_ms)
            if event_payload.get("overlay_model_count") is not None:
                overlay_sum += _to_float(event_payload.get("overlay_model_count"))
                overlay_count += 1
            if event_payload.get("tile_count") is not None:
                tile_sum += _to_float(event_payload.get("tile_count"))
                tile_value_count += 1
        elif event_name == "zone_crop_render":
            zone_crop_count += 1
            cache_hit = _to_bool(event_payload.get("cache_hit"))
            if cache_hit:
                zone_crop_cache_hits += 1
            _count_value_into(render_lifecycle_counts, event_payload.get("render_lifecycle"))
            _count_value_into(fidelity_counts, event_payload.get("visual_fidelity"))
            _count_value_into(renderer_backend_counts, event_payload.get("renderer_backend"))
            _count_value_into(reason_code_counts, event_payload.get("reason_code"))
            if event_payload.get("render_ms") is not None:
                render_ms = _to_float(event_payload.get("render_ms"))
                if render_ms >= 0:
                    zone_crop_all_ms.append(render_ms)
                    if cache_hit:
                        zone_crop_hit_ms.append(render_ms)
                    else:
                        zone_crop_cold_ms.append(render_ms)
            pdf_display_list_render_count += _to_int(
                event_payload.get("pdf_display_list_render_count")
            )
            pdf_display_list_cache_lookup_count += _to_int(
                event_payload.get("pdf_display_list_cache_lookup_count")
            )
            pdf_display_list_cache_hit_count += _to_int(
                event_payload.get("pdf_display_list_cache_hit_count")
            )
            pdf_display_list_cache_miss_count += _to_int(
                event_payload.get("pdf_display_list_cache_miss_count")
            )
            pdf_display_list_cache_eviction_count = max(
                pdf_display_list_cache_eviction_count,
                _to_int(event_payload.get("pdf_display_list_cache_eviction_count")),
            )
            pdf_display_list_cache_max_total_bytes = max(
                pdf_display_list_cache_max_total_bytes,
                _to_int(event_payload.get("pdf_display_list_cache_total_estimated_bytes")),
            )
            pdf_display_list_cache_byte_limit = max(
                pdf_display_list_cache_byte_limit,
                _to_int(event_payload.get("pdf_display_list_cache_byte_limit")),
            )
            pdf_display_list_worker_rss_mb_max = max(
                pdf_display_list_worker_rss_mb_max,
                _to_float(event_payload.get("pdf_display_list_worker_rss_mb")),
            )
            dxf_index_cache_entries_max = max(
                dxf_index_cache_entries_max,
                _to_int(event_payload.get("dxf_index_cache_entries")),
            )
            dxf_index_cache_capacity_entries = max(
                dxf_index_cache_capacity_entries,
                _to_int(event_payload.get("dxf_index_cache_capacity_entries")),
            )
            dxf_index_cache_entry_estimated_bytes_max = max(
                dxf_index_cache_entry_estimated_bytes_max,
                _to_int(event_payload.get("dxf_index_cache_entry_estimated_bytes_max")),
            )
            dxf_index_cache_hit_count += _to_int(event_payload.get("dxf_index_cache_hit_count"))
            dxf_index_cache_miss_count += _to_int(event_payload.get("dxf_index_cache_miss_count"))
            dxf_index_cache_lookup_count += _to_int(
                event_payload.get("dxf_index_cache_lookup_count")
            )
            dxf_index_cache_eviction_count += _to_int(
                event_payload.get("dxf_index_cache_eviction_count")
            )
            dxf_index_cache_evicted_estimated_bytes += _to_int(
                event_payload.get("dxf_index_cache_evicted_estimated_bytes")
            )
            dxf_index_cache_max_total_bytes = max(
                dxf_index_cache_max_total_bytes,
                _to_int(event_payload.get("dxf_index_cache_total_estimated_bytes")),
            )
            dxf_index_cache_byte_limit = max(
                dxf_index_cache_byte_limit,
                _to_int(event_payload.get("dxf_index_cache_byte_limit")),
            )
            dxf_index_cache_worker_rss_mb_max = max(
                dxf_index_cache_worker_rss_mb_max,
                _to_float(event_payload.get("dxf_index_cache_worker_rss_mb")),
            )
            explicit_pil_fallback_count = _to_int(event_payload.get("pdf_pil_fallback_count"))
            if explicit_pil_fallback_count:
                pdf_pil_fallback_count += explicit_pil_fallback_count
            elif _warning_contains(event_payload, "renderer:pdf-pil-fallback"):
                pdf_pil_fallback_count += 1
            if _is_selected_zone_fallback(event_payload):
                selected_zone_fallback_count += 1
        elif event_name == "package_background_render":
            package_background_render_count += 1
            _append_non_negative(package_background_render_ms, event_payload.get("render_ms"))
        elif event_name == "pair_render":
            pair_render_count += 1
            _append_non_negative(pair_render_ms, event_payload.get("render_ms"))
            _append_non_negative(tile_build_ms, event_payload.get("tile_ms"))
            _append_non_negative(tile_pyramid_ms, event_payload.get("tile_pyramid_ms"))
            _append_non_negative(overlay_tile_ms, event_payload.get("overlay_tile_ms"))
            _append_non_negative(tile_cache_write_ms, event_payload.get("tile_cache_write_ms"))
            tile_cache_max_payload_bytes = max(
                tile_cache_max_payload_bytes,
                _to_int(event_payload.get("cache_total_estimated_bytes")),
            )
            tile_cache_byte_limit = max(
                tile_cache_byte_limit,
                _to_int(event_payload.get("cache_byte_limit")),
            )
            tile_cache_eviction_count += _to_int(event_payload.get("eviction_count"))
            tile_cache_evicted_pair_count += _to_int(event_payload.get("evicted_pair_count"))
            tile_cache_evicted_estimated_bytes += _to_int(event_payload.get("evicted_estimated_bytes"))
            tile_cache_retained_estimated_bytes = max(
                tile_cache_retained_estimated_bytes,
                _to_int(event_payload.get("cache_retained_estimated_bytes")),
            )
            _count_value_into(
                tile_cache_eviction_reason_counts,
                _non_empty_reason(event_payload.get("eviction_reason")),
            )
            tile_overlay_count_max = max(tile_overlay_count_max, _to_int(event_payload.get("overlay_count")))
            tile_materialized_overlay_count_max = max(
                tile_materialized_overlay_count_max,
                _to_int(event_payload.get("materialized_overlay_count")),
            )
            tile_omitted_overlay_count_max = max(
                tile_omitted_overlay_count_max,
                _to_int(event_payload.get("overlay_omitted_count")),
            )
            if _to_bool(event_payload.get("tile_cache_attempted")) and event_payload.get("tile_cache_hit") is not None:
                tile_cache_event_count += 1
                if _to_bool(event_payload.get("tile_cache_hit")):
                    tile_cache_hit_count += 1
        elif event_name == "package_tile_write":
            _append_non_negative(tile_build_ms, event_payload.get("tile_write_ms"))
            _append_non_negative(tile_pyramid_ms, event_payload.get("tile_pyramid_ms"))
            _append_non_negative(overlay_tile_ms, event_payload.get("overlay_tile_ms"))
            _append_non_negative(tile_cache_write_ms, event_payload.get("tile_cache_write_ms"))
            _append_non_negative(tile_cache_lookup_ms, event_payload.get("cache_lookup_ms"))
            tile_cache_max_payload_bytes = max(
                tile_cache_max_payload_bytes,
                _to_int(event_payload.get("cache_total_estimated_bytes")),
            )
            tile_cache_byte_limit = max(
                tile_cache_byte_limit,
                _to_int(event_payload.get("cache_byte_limit")),
            )
            tile_cache_eviction_count += _to_int(event_payload.get("eviction_count"))
            tile_cache_evicted_pair_count += _to_int(event_payload.get("evicted_pair_count"))
            tile_cache_evicted_estimated_bytes += _to_int(event_payload.get("evicted_estimated_bytes"))
            tile_cache_retained_estimated_bytes = max(
                tile_cache_retained_estimated_bytes,
                _to_int(event_payload.get("cache_retained_estimated_bytes")),
            )
            _count_value_into(
                tile_cache_eviction_reason_counts,
                _non_empty_reason(event_payload.get("eviction_reason")),
            )
            tile_overlay_count_max = max(tile_overlay_count_max, _to_int(event_payload.get("overlay_count")))
            tile_materialized_overlay_count_max = max(
                tile_materialized_overlay_count_max,
                _to_int(event_payload.get("materialized_overlay_count")),
            )
            tile_omitted_overlay_count_max = max(
                tile_omitted_overlay_count_max,
                _to_int(event_payload.get("overlay_omitted_count")),
            )
            if event_payload.get("tile_cache_hit") is not None:
                tile_cache_event_count += 1
                if _to_bool(event_payload.get("tile_cache_hit")):
                    tile_cache_hit_count += 1
        elif event_name == "visible_tile_window_materialise":
            _append_non_negative(tile_build_ms, event_payload.get("tile_cache_write_ms"))
            tile_cache_max_payload_bytes = max(
                tile_cache_max_payload_bytes,
                _to_int(event_payload.get("cache_total_estimated_bytes")),
            )
            tile_cache_byte_limit = max(
                tile_cache_byte_limit,
                _to_int(event_payload.get("cache_byte_limit")),
            )
            tile_cache_eviction_count += _to_int(event_payload.get("eviction_count"))
            tile_cache_evicted_pair_count += _to_int(event_payload.get("evicted_pair_count"))
            tile_cache_evicted_estimated_bytes += _to_int(event_payload.get("evicted_estimated_bytes"))
            tile_cache_retained_estimated_bytes = max(
                tile_cache_retained_estimated_bytes,
                _to_int(event_payload.get("cache_retained_estimated_bytes")),
            )
            _count_value_into(
                tile_cache_eviction_reason_counts,
                _non_empty_reason(event_payload.get("eviction_reason")),
            )
        elif event_name == "tiles_manifest_materialise":
            _append_non_negative(tiles_manifest_materialise_ms, event_payload.get("materialise_ms"))
        elif event_name == "pair_selection_initial_load":
            pair_selection_count += 1
            _append_non_negative(
                pair_selection_gui_block_ms,
                event_payload.get("gui_block_ms", event_payload.get("elapsed_ms")),
            )
            overlay_cache_max_total_bytes = max(
                overlay_cache_max_total_bytes,
                _to_int(event_payload.get("overlay_cache_total_bytes")),
            )
            overlay_cache_byte_limit = max(
                overlay_cache_byte_limit,
                _to_int(event_payload.get("overlay_cache_byte_limit")),
            )
        elif event_name == "pdf_page_navigation":
            pdf_page_navigation_count += 1
            _append_non_negative(pdf_page_navigation_gui_block_ms, event_payload.get("gui_block_ms"))
            if _to_bool(event_payload.get("overlay_load_deferred")):
                pdf_page_navigation_deferred_count += 1
        elif event_name == "full_zone_tree_rebuild":
            full_tree_rebuild_count += 1
            _append_non_negative(full_tree_rebuild_ms, event_payload.get("elapsed_ms"))
            full_tree_rebuild_max_overlay_count = max(
                full_tree_rebuild_max_overlay_count,
                _to_int(event_payload.get("overlay_count")),
            )
            if _to_bool(event_payload.get("chunked")):
                full_tree_rebuild_chunked_count += 1
            _append_non_negative(full_tree_rebuild_chunk_counts, event_payload.get("chunk_count"))
            _append_non_negative(
                full_tree_rebuild_max_chunk_ms,
                event_payload.get("max_chunk_elapsed_ms", event_payload.get("max_chunk_ms")),
            )
            full_tree_rebuild_tree_item_count_max = max(
                full_tree_rebuild_tree_item_count_max,
                _to_int(event_payload.get("tree_item_count")),
            )
            _append_non_negative(
                full_tree_overlay_json_load_ms,
                event_payload.get("overlay_load_ms", event_payload.get("overlay_json_load_ms")),
            )
            _append_non_negative(full_tree_plan_build_ms, event_payload.get("plan_build_ms"))
            full_tree_overlay_json_bytes_max = max(
                full_tree_overlay_json_bytes_max,
                _to_int(event_payload.get("overlay_json_bytes")),
            )
            if _to_bool(event_payload.get("overlay_load_worker")):
                full_tree_overlay_load_worker_count += 1
            if _to_bool(event_payload.get("plan_build_worker")):
                full_tree_plan_build_worker_count += 1
        elif event_name == "lightweight_pair_load":
            lightweight_pair_load_count += 1
            load_ms = event_payload.get("load_ms", event_payload.get("elapsed_ms"))
            _append_non_negative(lightweight_pair_load_ms, load_ms)
            cache_state = str(event_payload.get("pdf_cache_state") or "unknown")
            _count_value_into(lightweight_pdf_cache_state_counts, cache_state)
            if event_payload.get("input_format") == "pdf":
                if _to_bool(event_payload.get("before_metadata_hit")):
                    lightweight_pdf_metadata_hit_count += 1
                if _to_bool(event_payload.get("after_metadata_hit")):
                    lightweight_pdf_metadata_hit_count += 1
                if cache_state == "all_cached":
                    _append_non_negative(lightweight_pdf_cached_ms, load_ms)
                elif cache_state in {"all_cold", "mixed", "unavailable"}:
                    _append_non_negative(lightweight_pdf_cold_ms, load_ms)
        elif event_name == "lightweight_pdf_prewarm":
            lightweight_pdf_prewarm_count += 1
            _append_non_negative(lightweight_pdf_prewarm_ms, event_payload.get("elapsed_ms"))
            lightweight_pdf_prewarm_ok_count += _to_int(event_payload.get("ok_count"))
            lightweight_pdf_prewarm_rendered_count += _to_int(event_payload.get("rendered_count"))
            lightweight_pdf_prewarm_cache_hit_count += _to_int(event_payload.get("cache_hit_count"))
            lightweight_pdf_prewarm_metadata_hit_count += _to_int(event_payload.get("metadata_hit_count"))
        elif event_name == "zone_selection":
            zone_selection_count += 1
            _append_non_negative(zone_selection_gui_block_ms, event_payload.get("gui_block_ms"))
        elif event_name == "viewer_overlay_cache_evict":
            overlay_cache_eviction_count += 1
            overlay_cache_max_total_bytes = max(
                overlay_cache_max_total_bytes,
                max(
                    _to_int(event_payload.get("overlay_cache_total_bytes")),
                    _to_int(event_payload.get("cache_total_bytes")),
                ),
            )
            overlay_cache_byte_limit = max(
                overlay_cache_byte_limit,
                max(
                    _to_int(event_payload.get("overlay_cache_byte_limit")),
                    _to_int(event_payload.get("cache_byte_limit")),
                ),
            )
            overlay_cache_pair_limit = max(
                overlay_cache_pair_limit,
                max(
                    _to_int(event_payload.get("overlay_cache_pair_limit")),
                    _to_int(event_payload.get("cache_pair_limit")),
                ),
            )
        elif event_name == "zone_render_stale":
            selected_zone_stale_count += 1
        elif event_name in {"zone_render_cancelled", "zone_render_pending_replaced", "zone_render_pending_dropped"}:
            selected_zone_cancel_count += 1
        elif event_name == "zone_render_fallback":
            selected_zone_fallback_count += 1
            _count_value_into(fidelity_counts, event_payload.get("visual_fidelity"))
            _count_value_into(reason_code_counts, event_payload.get("reason_code"))

    summary["event_count"] = event_count
    if not event_count:
        summary["status"] = "empty"
        return _finalize_summary_overhead(summary, started)
    summary["viewport_model_count"] = viewport_count
    summary["zone_crop_count"] = zone_crop_count
    summary["package_background_render_count"] = package_background_render_count
    summary["pair_render_count"] = pair_render_count
    summary["worker_spawned_count"] = worker_spawned_count
    summary["pair_selection_count"] = pair_selection_count
    summary["pdf_page_navigation_count"] = pdf_page_navigation_count
    summary["pdf_page_navigation_deferred_count"] = pdf_page_navigation_deferred_count
    summary["full_tree_rebuild_count"] = full_tree_rebuild_count
    summary["full_tree_rebuild_max_overlay_count"] = full_tree_rebuild_max_overlay_count
    summary["full_tree_rebuild_chunked_count"] = full_tree_rebuild_chunked_count
    summary["full_tree_rebuild_tree_item_count_max"] = full_tree_rebuild_tree_item_count_max
    summary["full_tree_overlay_json_bytes_max"] = full_tree_overlay_json_bytes_max
    summary["full_tree_overlay_load_worker_count"] = full_tree_overlay_load_worker_count
    summary["full_tree_plan_build_worker_count"] = full_tree_plan_build_worker_count
    summary["lightweight_pair_load_count"] = lightweight_pair_load_count
    summary["lightweight_pdf_cache_state_counts"] = lightweight_pdf_cache_state_counts
    summary["lightweight_pdf_metadata_hit_count"] = lightweight_pdf_metadata_hit_count
    summary["lightweight_pdf_prewarm_count"] = lightweight_pdf_prewarm_count
    summary["lightweight_pdf_prewarm_ok_count"] = lightweight_pdf_prewarm_ok_count
    summary["lightweight_pdf_prewarm_rendered_count"] = lightweight_pdf_prewarm_rendered_count
    summary["lightweight_pdf_prewarm_cache_hit_count"] = lightweight_pdf_prewarm_cache_hit_count
    summary["lightweight_pdf_prewarm_metadata_hit_count"] = lightweight_pdf_prewarm_metadata_hit_count
    summary["zone_selection_count"] = zone_selection_count
    summary["selected_zone_stale_count"] = selected_zone_stale_count
    summary["selected_zone_cancel_count"] = selected_zone_cancel_count
    summary["selected_zone_fallback_count"] = selected_zone_fallback_count
    summary["pdf_display_list_render_count"] = pdf_display_list_render_count
    summary["pdf_display_list_cache_lookup_count"] = pdf_display_list_cache_lookup_count
    summary["pdf_display_list_cache_hit_count"] = pdf_display_list_cache_hit_count
    summary["pdf_display_list_cache_miss_count"] = pdf_display_list_cache_miss_count
    summary["pdf_display_list_cache_eviction_count"] = pdf_display_list_cache_eviction_count
    summary["pdf_display_list_cache_max_total_bytes"] = pdf_display_list_cache_max_total_bytes
    summary["pdf_display_list_cache_byte_limit"] = pdf_display_list_cache_byte_limit
    summary["pdf_display_list_worker_rss_mb_max"] = round(pdf_display_list_worker_rss_mb_max, 3)
    summary["pdf_pil_fallback_count"] = pdf_pil_fallback_count
    if dxf_index_cache_lookup_count <= 0:
        dxf_index_cache_lookup_count = dxf_index_cache_hit_count + dxf_index_cache_miss_count
    summary["dxf_index_cache_entries_max"] = dxf_index_cache_entries_max
    summary["dxf_index_cache_capacity_entries"] = dxf_index_cache_capacity_entries
    summary["dxf_index_cache_entry_estimated_bytes_max"] = dxf_index_cache_entry_estimated_bytes_max
    summary["dxf_index_cache_lookup_count"] = dxf_index_cache_lookup_count
    summary["dxf_index_cache_hit_count"] = dxf_index_cache_hit_count
    summary["dxf_index_cache_miss_count"] = dxf_index_cache_miss_count
    summary["dxf_index_cache_eviction_count"] = dxf_index_cache_eviction_count
    summary["dxf_index_cache_evicted_estimated_bytes"] = dxf_index_cache_evicted_estimated_bytes
    summary["dxf_index_cache_max_total_bytes"] = dxf_index_cache_max_total_bytes
    summary["dxf_index_cache_byte_limit"] = dxf_index_cache_byte_limit
    summary["dxf_index_cache_worker_rss_mb_max"] = round(dxf_index_cache_worker_rss_mb_max, 3)
    if dxf_index_cache_lookup_count:
        summary["dxf_index_cache_hit_rate"] = round(
            dxf_index_cache_hit_count / dxf_index_cache_lookup_count,
            4,
        )
    if pdf_display_list_cache_lookup_count:
        summary["pdf_display_list_cache_hit_rate"] = round(
            pdf_display_list_cache_hit_count / pdf_display_list_cache_lookup_count,
            4,
        )
    summary["overlay_cache_eviction_count"] = overlay_cache_eviction_count
    summary["overlay_cache_max_total_bytes"] = overlay_cache_max_total_bytes
    summary["overlay_cache_byte_limit"] = overlay_cache_byte_limit
    summary["overlay_cache_pair_limit"] = overlay_cache_pair_limit
    summary["native_resource_available"] = native_resource_available
    summary["native_resource_sample_count"] = native_resource_sample_count
    summary["process_handle_count_max"] = process_handle_count_max
    summary["open_file_descriptor_count_max"] = open_file_descriptor_count_max
    summary["gdi_handle_count_max"] = gdi_handle_count_max
    summary["user_handle_count_max"] = user_handle_count_max
    summary["worker_process_count_max"] = worker_process_count_max
    summary["tile_cache_max_payload_bytes"] = tile_cache_max_payload_bytes
    summary["tile_cache_retained_estimated_bytes"] = tile_cache_retained_estimated_bytes
    summary["tile_cache_byte_limit"] = tile_cache_byte_limit
    summary["tile_cache_eviction_count"] = tile_cache_eviction_count
    summary["tile_cache_evicted_pair_count"] = tile_cache_evicted_pair_count
    summary["tile_cache_evicted_estimated_bytes"] = tile_cache_evicted_estimated_bytes
    summary["tile_cache_eviction_reason_counts"] = tile_cache_eviction_reason_counts
    summary["tile_cache_event_count"] = tile_cache_event_count
    summary["tile_cache_hit_count"] = tile_cache_hit_count
    summary["tile_cache_miss_count"] = max(0, tile_cache_event_count - tile_cache_hit_count)
    summary["tile_overlay_count_max"] = tile_overlay_count_max
    summary["tile_materialized_overlay_count_max"] = tile_materialized_overlay_count_max
    summary["tile_omitted_overlay_count_max"] = tile_omitted_overlay_count_max
    if tile_cache_event_count:
        summary["tile_cache_hit_rate"] = round(tile_cache_hit_count / tile_cache_event_count, 4)
    if package_background_render_ms:
        summary["package_background_render_ms"] = _percentile_summary(package_background_render_ms)
    if pair_render_ms:
        summary["pair_render_ms"] = _percentile_summary(pair_render_ms)
    if tile_build_ms:
        summary["tile_build_ms"] = _percentile_summary(tile_build_ms)
    if tile_pyramid_ms:
        summary["tile_pyramid_ms"] = _percentile_summary(tile_pyramid_ms)
    if overlay_tile_ms:
        summary["overlay_tile_ms"] = _percentile_summary(overlay_tile_ms)
    if tile_cache_write_ms:
        summary["tile_cache_write_ms"] = _percentile_summary(tile_cache_write_ms)
    if tile_cache_lookup_ms:
        summary["tile_cache_lookup_ms"] = _percentile_summary(tile_cache_lookup_ms)
    if tiles_manifest_materialise_ms:
        summary["tiles_manifest_materialise_ms"] = _percentile_summary(tiles_manifest_materialise_ms)
    if pair_selection_gui_block_ms:
        summary["pair_selection_gui_block_ms"] = _percentile_summary(pair_selection_gui_block_ms)
    if pdf_page_navigation_gui_block_ms:
        summary["pdf_page_navigation_gui_block_ms"] = _percentile_summary(pdf_page_navigation_gui_block_ms)
    if full_tree_rebuild_ms:
        summary["full_tree_rebuild_ms"] = _percentile_summary(full_tree_rebuild_ms)
    if full_tree_rebuild_chunk_counts:
        summary["full_tree_rebuild_chunk_count"] = _percentile_summary(full_tree_rebuild_chunk_counts)
    if full_tree_rebuild_max_chunk_ms:
        summary["full_tree_rebuild_max_chunk_ms"] = _percentile_summary(full_tree_rebuild_max_chunk_ms)
    if full_tree_overlay_json_load_ms:
        summary["full_tree_overlay_json_load_ms"] = _percentile_summary(full_tree_overlay_json_load_ms)
    if full_tree_plan_build_ms:
        summary["full_tree_plan_build_ms"] = _percentile_summary(full_tree_plan_build_ms)
    if lightweight_pair_load_ms:
        summary["lightweight_pair_load_ms"] = _percentile_summary(lightweight_pair_load_ms)
    if lightweight_pdf_cached_ms:
        summary["lightweight_pdf_cached_load_ms"] = _percentile_summary(lightweight_pdf_cached_ms)
    if lightweight_pdf_cold_ms:
        summary["lightweight_pdf_cold_load_ms"] = _percentile_summary(lightweight_pdf_cold_ms)
    if lightweight_pdf_prewarm_ms:
        summary["lightweight_pdf_prewarm_ms"] = _percentile_summary(lightweight_pdf_prewarm_ms)
    if zone_selection_gui_block_ms:
        summary["zone_selection_gui_block_ms"] = _percentile_summary(zone_selection_gui_block_ms)
    if viewport_count:
        summary["cache_hit_rate"] = round(viewport_cache_hits / viewport_count, 4)
        if cull_values:
            summary["cull_ms"] = _percentile_summary(cull_values)
        if overlay_count:
            summary["overlay_model_avg"] = round(overlay_sum / overlay_count, 2)
        if tile_value_count:
            summary["tile_count_avg"] = round(tile_sum / tile_value_count, 2)
        summary["status"] = "ready"
    if zone_crop_count:
        summary["render_lifecycle_counts"] = render_lifecycle_counts
        summary["fidelity_counts"] = fidelity_counts
        summary["renderer_backend_counts"] = renderer_backend_counts
        summary["reason_code_counts"] = reason_code_counts
        summary["zone_crop_cache_hit_rate"] = round(zone_crop_cache_hits / zone_crop_count, 4)
        if zone_crop_all_ms:
            summary["zone_crop_ms"] = _percentile_summary(zone_crop_all_ms)
        if zone_crop_hit_ms:
            summary["zone_crop_cache_hit_ms"] = _percentile_summary(zone_crop_hit_ms)
        if zone_crop_cold_ms:
            summary["zone_crop_cold_ms"] = _percentile_summary(zone_crop_cold_ms)
        summary["status"] = "ready"
    if render_lifecycle_counts:
        summary["render_lifecycle_counts"] = render_lifecycle_counts
    if fidelity_counts:
        summary["fidelity_counts"] = fidelity_counts
    if renderer_backend_counts:
        summary["renderer_backend_counts"] = renderer_backend_counts
    if reason_code_counts:
        summary["reason_code_counts"] = reason_code_counts
    interactive_event_count = (
        pair_selection_count
        + pdf_page_navigation_count
        + full_tree_rebuild_count
        + lightweight_pair_load_count
        + lightweight_pdf_prewarm_count
        + zone_selection_count
        + selected_zone_stale_count
        + selected_zone_cancel_count
        + selected_zone_fallback_count
    )
    if interactive_event_count and summary["status"] != "ready":
        summary["status"] = "ready"
    if not viewport_count and not zone_crop_count and not interactive_event_count:
        # Backend events (package_tile_write etc.) recorded, but no viewport
        # interactions happened yet — distinguish from 'no events at all' so the
        # status line can hint that the GUI hasn't been opened.
        summary["status"] = "package_only" if summary["event_count"] > 0 else "empty"
    return _finalize_summary_overhead(summary, started)


def _jsonl_has_valid_event(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    return True
    except OSError:
        return False
    return False


def _iter_viewer_perf_events(viewer_root: Path, *, use_jsonl: bool) -> Iterable[dict[str, Any]]:
    jsonl_path = viewer_root / VIEWER_PERF_JSONL_FILENAME
    if use_jsonl:
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
                        yield payload
        except OSError:
            return
        return

    json_path = viewer_root / VIEWER_PERF_FILENAME
    if not json_path.exists():
        return
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return
    for event_payload in events:
        if isinstance(event_payload, dict):
            yield event_payload


def format_viewer_perf_summary_korean(summary: dict[str, Any]) -> str:
    """Render the summary as a single Korean status line for the GUI."""

    if not isinstance(summary, dict):
        return "성능 데이터 없음"
    status = str(summary.get("status") or "missing")
    if status == "missing":
        return "성능 데이터 없음 (viewer_perf 로그 미생성)"
    if status == "empty":
        return "성능 데이터 없음 (이벤트 없음)"
    if status == "package_only":
        line = f"뷰어 성능: 백엔드 이벤트 {summary.get('event_count', 0)}건 (viewport 사용 기록 없음 - GUI 미사용)"
        background = summary.get("package_background_render_ms") or {}
        tile_write = summary.get("tile_cache_write_ms") or {}
        if isinstance(background, dict) and float(background.get("p95") or 0.0) > 0:
            line = f"{line} · 배경렌더 p95 {float(background.get('p95') or 0.0) / 1000.0:.2f}s"
        if isinstance(tile_write, dict) and float(tile_write.get("p95") or 0.0) > 0:
            line = f"{line} · 타일쓰기 p95 {float(tile_write.get('p95') or 0.0) / 1000.0:.2f}s"
        return line
    cache_pct = round(float(summary.get("cache_hit_rate") or 0.0) * 100.0, 1)
    cull = summary.get("cull_ms") or {}
    p50 = float(cull.get("p50") or 0.0) if isinstance(cull, dict) else 0.0
    p95 = float(cull.get("p95") or 0.0) if isinstance(cull, dict) else 0.0
    overlay_avg = float(summary.get("overlay_model_avg") or 0.0)
    tile_avg = float(summary.get("tile_count_avg") or 0.0)
    line = (
        f"뷰어 성능: 캐시 적중 {cache_pct:.1f}% · "
        f"cull p50 {p50:.1f}ms / p95 {p95:.1f}ms · "
        f"오버레이 평균 {overlay_avg:.0f} · 타일 평균 {tile_avg:.1f}"
    )
    zone_count = int(float(summary.get("zone_crop_count") or 0))
    if zone_count:
        cold = summary.get("zone_crop_cold_ms") or {}
        hit = summary.get("zone_crop_cache_hit_ms") or {}
        cold_p95 = float(cold.get("p95") or 0.0) if isinstance(cold, dict) else 0.0
        hit_p95 = float(hit.get("p95") or 0.0) if isinstance(hit, dict) else 0.0
        line = (
            f"{line} · 선택구역 {zone_count}건 "
            f"cold p95 {cold_p95 / 1000.0:.2f}s / hit p95 {hit_p95 / 1000.0:.2f}s"
        )
    pdf_dl_count = int(summary.get("pdf_display_list_render_count") or 0)
    if pdf_dl_count:
        dl_hit_pct = round(float(summary.get("pdf_display_list_cache_hit_rate") or 0.0) * 100.0, 1)
        dl_bytes = int(summary.get("pdf_display_list_cache_max_total_bytes") or 0)
        line = f"{line} · PDF DL {pdf_dl_count}회 hit {dl_hit_pct:.1f}% cache {dl_bytes / (1024 * 1024):.1f}MB"
    pdf_pil_fallback = int(summary.get("pdf_pil_fallback_count") or 0)
    if pdf_pil_fallback:
        line = f"{line} · PDF PIL fallback {pdf_pil_fallback}회"
    dxf_index_lookups = int(summary.get("dxf_index_cache_lookup_count") or 0)
    if dxf_index_lookups:
        dxf_hit_pct = round(float(summary.get("dxf_index_cache_hit_rate") or 0.0) * 100.0, 1)
        dxf_bytes = int(summary.get("dxf_index_cache_max_total_bytes") or 0)
        line = (
            f"{line} · DXF idx {dxf_index_lookups}회 "
            f"hit {dxf_hit_pct:.1f}% cache {dxf_bytes / (1024 * 1024):.1f}MB"
        )
    pair_select = summary.get("pair_selection_gui_block_ms") or {}
    if isinstance(pair_select, dict) and int(summary.get("pair_selection_count") or 0):
        line = f"{line} · 도면선택 p95 {float(pair_select.get('p95') or 0.0):.1f}ms"
    tile_write = summary.get("tile_cache_write_ms") or {}
    if isinstance(tile_write, dict) and float(tile_write.get("p95") or 0.0) > 0:
        line = f"{line} · 타일쓰기 p95 {float(tile_write.get('p95') or 0.0) / 1000.0:.2f}s"
    tile_evicted_pairs = int(summary.get("tile_cache_evicted_pair_count") or 0)
    if tile_evicted_pairs:
        tile_evicted_mb = int(summary.get("tile_cache_evicted_estimated_bytes") or 0) / (1024 * 1024)
        line = f"{line} · 타일캐시 evict {tile_evicted_pairs}쌍/{tile_evicted_mb:.1f}MB"
    stale = int(summary.get("selected_zone_stale_count") or 0)
    dropped = int(summary.get("selected_zone_cancel_count") or 0)
    fallback = int(summary.get("selected_zone_fallback_count") or 0)
    if stale or dropped or fallback:
        line = f"{line} · stale/drop/fallback {stale}/{dropped}/{fallback}"
    return line


def _empty_summary() -> dict[str, Any]:
    return {
        "schema_version": VIEWER_PERF_SUMMARY_SCHEMA_VERSION,
        "status": "missing",
        "summary_source": "none",
        "summary_input_bytes": 0,
        "summary_elapsed_ms": 0.0,
        "event_count": 0,
        "viewport_model_count": 0,
        "cache_hit_rate": 0.0,
        "cull_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "overlay_model_avg": 0.0,
        "tile_count_avg": 0.0,
        "zone_crop_count": 0,
        "zone_crop_cache_hit_rate": 0.0,
        "zone_crop_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "zone_crop_cold_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "zone_crop_cache_hit_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "render_lifecycle_counts": {},
        "fidelity_counts": {},
        "renderer_backend_counts": {},
        "reason_code_counts": {},
        "package_background_render_count": 0,
        "package_background_render_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "pair_render_count": 0,
        "pair_render_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "tile_build_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "tile_pyramid_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "overlay_tile_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "tile_cache_write_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "tile_cache_lookup_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "tiles_manifest_materialise_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "tile_overlay_count_max": 0,
        "tile_materialized_overlay_count_max": 0,
        "tile_omitted_overlay_count_max": 0,
        "worker_spawned_count": 0,
        "pair_selection_count": 0,
        "pair_selection_gui_block_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "pdf_page_navigation_count": 0,
        "pdf_page_navigation_gui_block_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "pdf_page_navigation_deferred_count": 0,
        "full_tree_rebuild_count": 0,
        "full_tree_rebuild_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "full_tree_rebuild_max_overlay_count": 0,
        "full_tree_rebuild_chunked_count": 0,
        "full_tree_rebuild_chunk_count": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "full_tree_rebuild_max_chunk_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "full_tree_rebuild_tree_item_count_max": 0,
        "full_tree_overlay_json_load_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "full_tree_plan_build_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "full_tree_overlay_json_bytes_max": 0,
        "full_tree_overlay_load_worker_count": 0,
        "full_tree_plan_build_worker_count": 0,
        "lightweight_pair_load_count": 0,
        "lightweight_pair_load_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "lightweight_pdf_cached_load_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "lightweight_pdf_cold_load_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "lightweight_pdf_cache_state_counts": {},
        "lightweight_pdf_metadata_hit_count": 0,
        "lightweight_pdf_prewarm_count": 0,
        "lightweight_pdf_prewarm_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "lightweight_pdf_prewarm_ok_count": 0,
        "lightweight_pdf_prewarm_rendered_count": 0,
        "lightweight_pdf_prewarm_cache_hit_count": 0,
        "lightweight_pdf_prewarm_metadata_hit_count": 0,
        "zone_selection_count": 0,
        "zone_selection_gui_block_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0},
        "selected_zone_stale_count": 0,
        "selected_zone_cancel_count": 0,
        "selected_zone_fallback_count": 0,
        "pdf_display_list_render_count": 0,
        "pdf_display_list_cache_lookup_count": 0,
        "pdf_display_list_cache_hit_count": 0,
        "pdf_display_list_cache_miss_count": 0,
        "pdf_display_list_cache_hit_rate": 0.0,
        "pdf_display_list_cache_eviction_count": 0,
        "pdf_display_list_cache_max_total_bytes": 0,
        "pdf_display_list_cache_byte_limit": 0,
        "pdf_display_list_worker_rss_mb_max": 0.0,
        "pdf_pil_fallback_count": 0,
        "dxf_index_cache_entries_max": 0,
        "dxf_index_cache_capacity_entries": 0,
        "dxf_index_cache_entry_estimated_bytes_max": 0,
        "dxf_index_cache_lookup_count": 0,
        "dxf_index_cache_hit_count": 0,
        "dxf_index_cache_miss_count": 0,
        "dxf_index_cache_hit_rate": 0.0,
        "dxf_index_cache_eviction_count": 0,
        "dxf_index_cache_evicted_estimated_bytes": 0,
        "dxf_index_cache_max_total_bytes": 0,
        "dxf_index_cache_byte_limit": 0,
        "dxf_index_cache_worker_rss_mb_max": 0.0,
        "overlay_cache_eviction_count": 0,
        "overlay_cache_max_total_bytes": 0,
        "overlay_cache_byte_limit": 0,
        "overlay_cache_pair_limit": 0,
        "native_resource_available": False,
        "native_resource_sample_count": 0,
        "process_handle_count_max": 0,
        "open_file_descriptor_count_max": 0,
        "gdi_handle_count_max": 0,
        "user_handle_count_max": 0,
        "worker_process_count_max": 0,
        "tile_cache_max_payload_bytes": 0,
        "tile_cache_retained_estimated_bytes": 0,
        "tile_cache_byte_limit": 0,
        "tile_cache_eviction_count": 0,
        "tile_cache_evicted_pair_count": 0,
        "tile_cache_evicted_estimated_bytes": 0,
        "tile_cache_eviction_reason_counts": {},
        "tile_cache_event_count": 0,
        "tile_cache_hit_count": 0,
        "tile_cache_miss_count": 0,
        "tile_cache_hit_rate": 0.0,
    }


def _finalize_summary_overhead(
    summary: dict[str, Any],
    started_perf: float,
) -> dict[str, Any]:
    summary["summary_elapsed_ms"] = round(
        max(0.0, (time.perf_counter() - float(started_perf)) * 1000.0),
        3,
    )
    return summary


def _file_size(path: Path) -> int:
    try:
        return int(Path(path).stat().st_size)
    except OSError:
        return 0


def _percentile_summary(values: Iterable[float]) -> dict[str, float]:
    samples = sorted(float(v) for v in values)
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0}
    return {
        "p50": round(_percentile(samples, 0.50), 3),
        "p95": round(_percentile(samples, 0.95), 3),
        "p99": round(_percentile(samples, 0.99), 3),
        "mean": round(sum(samples) / len(samples), 3),
    }


def _percentile(samples: list[float], q: float) -> float:
    """Linear-interpolation percentile matching numpy default behavior."""

    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    rank = q * (len(samples) - 1)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return samples[lower]
    fraction = rank - lower
    return samples[lower] + (samples[upper] - samples[lower]) * fraction


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _append_non_negative(values: list[float], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    number = _to_float(value)
    if number >= 0:
        values.append(number)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "hit"}


def _count_values(values: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        _count_value_into(counts, value)
    return counts


def _count_value_into(counts: dict[str, int], value: Any) -> None:
    key = str(value or "").strip()
    if not key:
        return
    counts[key] = counts.get(key, 0) + 1


def _non_empty_reason(value: Any) -> str:
    reason = str(value or "").strip()
    return "" if reason == "within_limit" else reason


def _warning_contains(payload: dict[str, Any], needle: str) -> bool:
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        return any(needle in str(item) for item in warnings)
    return needle in str(warnings or "")


def _is_selected_zone_fallback(payload: dict[str, Any]) -> bool:
    lifecycle = str(payload.get("render_lifecycle") or "").strip().lower()
    fidelity = str(payload.get("visual_fidelity") or "").strip().lower()
    reason_code = str(payload.get("reason_code") or "").strip()
    return bool(
        lifecycle in {"fallback_visible", "skipped_missing_page_bbox"}
        or fidelity == "relative_overlay"
        or reason_code
    )
