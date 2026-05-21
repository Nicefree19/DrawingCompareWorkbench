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
from pathlib import Path
from typing import Any, Iterable, Optional

VIEWER_PERF_SUMMARY_SCHEMA_VERSION = 1
VIEWER_PERF_FILENAME = "viewer_perf.json"


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

    summary = _empty_summary()
    if viewer_root is None:
        summary["status"] = "missing"
        return summary
    path = Path(viewer_root) / VIEWER_PERF_FILENAME
    if not path.exists():
        summary["status"] = "missing"
        return summary
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary["status"] = "missing"
        return summary
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list) or not events:
        summary["status"] = "empty"
        return summary

    summary["event_count"] = len(events)
    viewport_events = [e for e in events if isinstance(e, dict) and e.get("event") == "viewport_model"]
    zone_crop_events = [e for e in events if isinstance(e, dict) and e.get("event") == "zone_crop_render"]
    summary["viewport_model_count"] = len(viewport_events)
    summary["zone_crop_count"] = len(zone_crop_events)

    if viewport_events:
        cache_hits = sum(1 for e in viewport_events if _to_int(e.get("tile_count")) > 0)
        summary["cache_hit_rate"] = round(cache_hits / len(viewport_events), 4)
        cull_values = [_to_float(e.get("cull_ms")) for e in viewport_events if e.get("cull_ms") is not None]
        cull_values = [v for v in cull_values if v >= 0]
        if cull_values:
            summary["cull_ms"] = _percentile_summary(cull_values)
        overlay_values = [_to_float(e.get("overlay_model_count")) for e in viewport_events if e.get("overlay_model_count") is not None]
        if overlay_values:
            summary["overlay_model_avg"] = round(sum(overlay_values) / len(overlay_values), 2)
        tile_values = [_to_float(e.get("tile_count")) for e in viewport_events if e.get("tile_count") is not None]
        if tile_values:
            summary["tile_count_avg"] = round(sum(tile_values) / len(tile_values), 2)
        summary["status"] = "ready"
    if zone_crop_events:
        cache_hit_events = [e for e in zone_crop_events if _to_bool(e.get("cache_hit"))]
        cold_events = [e for e in zone_crop_events if not _to_bool(e.get("cache_hit"))]
        summary["zone_crop_cache_hit_rate"] = round(len(cache_hit_events) / len(zone_crop_events), 4)
        all_ms = [_to_float(e.get("render_ms")) for e in zone_crop_events if e.get("render_ms") is not None]
        hit_ms = [_to_float(e.get("render_ms")) for e in cache_hit_events if e.get("render_ms") is not None]
        cold_ms = [_to_float(e.get("render_ms")) for e in cold_events if e.get("render_ms") is not None]
        if all_ms:
            summary["zone_crop_ms"] = _percentile_summary([v for v in all_ms if v >= 0])
        if hit_ms:
            summary["zone_crop_cache_hit_ms"] = _percentile_summary([v for v in hit_ms if v >= 0])
        if cold_ms:
            summary["zone_crop_cold_ms"] = _percentile_summary([v for v in cold_ms if v >= 0])
        summary["status"] = "ready"
    if not viewport_events and not zone_crop_events:
        # Backend events (package_tile_write etc.) recorded, but no viewport
        # interactions happened yet — distinguish from 'no events at all' so the
        # status line can hint that the GUI hasn't been opened.
        summary["status"] = "package_only" if summary["event_count"] > 0 else "empty"
    return summary


def format_viewer_perf_summary_korean(summary: dict[str, Any]) -> str:
    """Render the summary as a single Korean status line for the GUI."""

    if not isinstance(summary, dict):
        return "성능 데이터 없음"
    status = str(summary.get("status") or "missing")
    if status == "missing":
        return "성능 데이터 없음 (viewer_perf.json 미생성)"
    if status == "empty":
        return "성능 데이터 없음 (이벤트 없음)"
    if status == "package_only":
        return f"뷰어 성능: 백엔드 이벤트 {summary.get('event_count', 0)}건 (viewport 사용 기록 없음 - GUI 미사용)"
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
    return line


def _empty_summary() -> dict[str, Any]:
    return {
        "schema_version": VIEWER_PERF_SUMMARY_SCHEMA_VERSION,
        "status": "missing",
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
    }


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


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "hit"}
