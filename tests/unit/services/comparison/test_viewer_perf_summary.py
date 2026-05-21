# -*- coding: utf-8 -*-
"""Unit tests for viewer_perf.json aggregation used by the Workbench status line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.viewer_perf_summary import (
    VIEWER_PERF_FILENAME,
    format_viewer_perf_summary_korean,
    summarize_viewer_perf,
)


def _write_perf(viewer_root: Path, events: list[dict]) -> None:
    viewer_root.mkdir(parents=True, exist_ok=True)
    (viewer_root / VIEWER_PERF_FILENAME).write_text(
        json.dumps({"schema_version": 1, "event_count": len(events), "events": events}),
        encoding="utf-8",
    )


def test_summary_returns_missing_status_when_root_is_none() -> None:
    summary = summarize_viewer_perf(None)
    assert summary["status"] == "missing"
    assert summary["event_count"] == 0


def test_summary_returns_missing_status_when_file_absent(tmp_path: Path) -> None:
    summary = summarize_viewer_perf(tmp_path)
    assert summary["status"] == "missing"


def test_summary_returns_package_only_when_no_viewport_events(tmp_path: Path) -> None:
    # Backend tile-write events without any viewport interaction — surfaces as
    # ``package_only`` so the operator can tell the GUI was never used.
    _write_perf(tmp_path, [{"event": "package_tile_write", "tile_count": 4}])
    summary = summarize_viewer_perf(tmp_path)
    assert summary["status"] == "package_only"
    assert summary["viewport_model_count"] == 0
    assert summary["event_count"] == 1


def test_summary_returns_empty_when_event_list_is_empty(tmp_path: Path) -> None:
    _write_perf(tmp_path, [])
    summary = summarize_viewer_perf(tmp_path)
    assert summary["status"] == "empty"
    assert summary["event_count"] == 0


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
        {"event": "zone_crop_render", "cache_hit": False, "render_ms": 4000.0},
        {"event": "zone_crop_render", "cache_hit": False, "render_ms": 9000.0},
        {"event": "zone_crop_render", "cache_hit": True, "render_ms": 250.0},
        {"event": "zone_crop_render", "cache_hit": True, "render_ms": 500.0},
    ]
    _write_perf(tmp_path, events)
    summary = summarize_viewer_perf(tmp_path)

    assert summary["status"] == "ready"
    assert summary["zone_crop_count"] == 4
    assert summary["zone_crop_cache_hit_rate"] == pytest.approx(0.5)
    assert summary["zone_crop_cold_ms"]["p95"] == pytest.approx(8750.0)
    assert summary["zone_crop_cache_hit_ms"]["p95"] == pytest.approx(487.5)


def test_summary_handles_corrupt_json_gracefully(tmp_path: Path) -> None:
    (tmp_path / VIEWER_PERF_FILENAME).write_text("not json", encoding="utf-8")
    summary = summarize_viewer_perf(tmp_path)
    assert summary["status"] == "missing"


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
