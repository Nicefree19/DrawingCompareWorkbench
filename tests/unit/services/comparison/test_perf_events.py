# -*- coding: utf-8 -*-
"""Tests for append-only performance event telemetry."""

from __future__ import annotations

import json
from pathlib import Path

from src.services.comparison.perf_events import (
    PERF_EVENTS_FILENAME,
    PerfEventWriter,
    append_perf_event,
    iter_perf_events,
    remove_raw_perf_events,
    summarize_perf_events,
    write_perf_events_summary,
)


class _Sampler:
    def snapshot(self) -> dict:
        return {
            "rss_mb": 12.5,
            "working_set_mb": 20.0,
            "spool_mb": 3.25,
            "native_resource_available": True,
            "native_resource_sample_count": 2,
            "process_handle_count": 50,
            "open_file_descriptor_count": 9,
            "gdi_handle_count": 4,
            "user_handle_count": 5,
            "worker_process_count": 1,
        }


def test_append_and_summarize_perf_events(tmp_path: Path) -> None:
    path = tmp_path / PERF_EVENTS_FILENAME
    append_perf_event(
        path,
        run_id="run-1",
        stage="scan",
        event="completed",
        elapsed_ms=10.0,
        runtime_sampler=_Sampler(),
        input_bytes=1234,
        entity_count=99,
    )
    append_perf_event(
        path,
        run_id="run-1",
        stage="viewer",
        event="completed",
        elapsed_ms=50.0,
        cache_namespace="viewer",
        cache_key="same-key",
        cache_hit=True,
        cache_hit_reason="existing_render_result",
    )
    append_perf_event(
        path,
        run_id="run-1",
        stage="viewer",
        event="completed",
        elapsed_ms=70.0,
        cache_namespace="viewer",
        cache_key="same-key",
        cache_hit=False,
        cache_miss_reason="artifact_missing",
        reason_code="missing_page_bbox",
        warning_count=2,
        error_code="WARNED",
    )

    events = list(iter_perf_events(path))
    assert len(events) == 3
    assert events[0]["working_set_mb"] == 20.0
    assert events[0]["process_handle_count"] == 50
    assert events[0]["worker_process_count"] == 1

    summary = summarize_perf_events(path)
    assert summary["status"] == "ready"
    assert summary["event_count"] == 3
    assert summary["stage_counts"]["viewer"] == 2
    assert summary["cache_hit_rate"] == 0.5
    assert summary["cache_key_duplicate_count"] == 1
    assert summary["cache_hit_reasons"]["existing_render_result"] == 1
    assert summary["cache_miss_reasons"]["artifact_missing"] == 1
    assert summary["reason_code_counts"]["missing_page_bbox"] == 1
    assert summary["warning_count"] == 2
    assert summary["error_count"] == 1
    assert summary["peak_working_set_mb"] == 20.0
    assert summary["native_resource_available"] is True
    assert summary["native_resource_sample_count"] == 2
    assert summary["process_handle_count_max"] == 50
    assert summary["open_file_descriptor_count_max"] == 9
    assert summary["gdi_handle_count_max"] == 4
    assert summary["user_handle_count_max"] == 5
    assert summary["worker_process_count_max"] == 1
    assert summary["summary_input_bytes"] > 0
    assert summary["summary_elapsed_ms"] >= 0.0
    assert summary["total_recorded_elapsed_ms"] == 130.0
    assert summary["summary_overhead_ratio"] is not None
    assert summary["summary_overhead_ratio"] >= 0.0


def test_writer_summary_and_remove_raw_stream(tmp_path: Path) -> None:
    writer = PerfEventWriter(tmp_path, run_id="run-2")
    writer.append(stage="match", event="completed", elapsed_ms=5.0)
    summary = writer.summarize(write=True)

    summary_path = write_perf_events_summary(tmp_path, summary)
    assert summary_path.exists()
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["stage_counts"]["match"] == 1

    assert remove_raw_perf_events(tmp_path) is True
    assert not (tmp_path / PERF_EVENTS_FILENAME).exists()
    assert remove_raw_perf_events(tmp_path) is False


def test_malformed_lines_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / PERF_EVENTS_FILENAME
    path.write_text('{"stage":"scan","event":"completed"}\nnot-json\n', encoding="utf-8")

    events = list(iter_perf_events(path))

    assert events == [{"stage": "scan", "event": "completed"}]
    assert summarize_perf_events(path)["event_count"] == 1


def test_missing_and_empty_summary_include_overhead_fields(tmp_path: Path) -> None:
    missing = summarize_perf_events(tmp_path)
    assert missing["status"] == "missing"
    assert missing["summary_input_bytes"] == 0
    assert missing["summary_elapsed_ms"] >= 0.0
    assert missing["total_recorded_elapsed_ms"] == 0.0
    assert missing["summary_overhead_ratio"] is None

    path = tmp_path / PERF_EVENTS_FILENAME
    path.write_text("", encoding="utf-8")

    empty = summarize_perf_events(path)
    assert empty["status"] == "empty"
    assert empty["summary_input_bytes"] == 0
    assert empty["summary_elapsed_ms"] >= 0.0
    assert empty["total_recorded_elapsed_ms"] == 0.0
    assert empty["summary_overhead_ratio"] is None


def test_duplicate_cache_keys_are_counted_per_namespace(tmp_path: Path) -> None:
    path = tmp_path / PERF_EVENTS_FILENAME
    for namespace in ("zone_render", "zone_render", "zone_render", "viewer_package"):
        append_perf_event(
            path,
            stage=namespace,
            event="completed",
            cache_namespace=namespace,
            cache_key="same-key",
            cache_hit=True,
        )

    summary = summarize_perf_events(path)

    assert summary["cache_key_duplicate_count"] == 2
    assert summary["cache_key_duplicates_by_namespace"] == {"zone_render": 2}


def test_summary_overhead_ratio_is_none_without_elapsed_events(tmp_path: Path) -> None:
    path = tmp_path / PERF_EVENTS_FILENAME
    append_perf_event(path, stage="scan", event="started")

    summary = summarize_perf_events(path)

    assert summary["event_count"] == 1
    assert summary["total_recorded_elapsed_ms"] == 0.0
    assert summary["summary_overhead_ratio"] is None
