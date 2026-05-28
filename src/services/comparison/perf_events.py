# -*- coding: utf-8 -*-
"""Append-only performance event log for drawing comparison runs.

The event log is internal telemetry. Customer/shareable packages should keep
the compact summary JSON and remove the raw JSONL stream.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

PERF_EVENTS_SCHEMA_VERSION = 1
PERF_EVENTS_FILENAME = "perf_events.jsonl"
PERF_EVENTS_SUMMARY_FILENAME = "perf_events_summary.json"

_PERF_EVENT_WRITE_LOCK = threading.Lock()


class PerfEventWriter:
    """Small append-only writer for run/stage performance events."""

    def __init__(
        self,
        root: Path,
        *,
        run_id: str = "",
        runtime_sampler: Any = None,
        enabled: bool = True,
    ) -> None:
        self.root = Path(root)
        self.path = self.root / PERF_EVENTS_FILENAME
        self.run_id = str(run_id or "")
        self.runtime_sampler = runtime_sampler
        self.enabled = bool(enabled)

    def append(
        self,
        *,
        stage: str,
        event: str,
        elapsed_ms: float | None = None,
        pair_id: str = "",
        input_bytes: int | None = None,
        entity_count: int | None = None,
        cache_namespace: str = "",
        cache_key: str = "",
        cache_key_hash: str = "",
        cache_hit: bool | None = None,
        warning_count: int | None = None,
        error_code: str = "",
        render_mode: str = "",
        fidelity: str = "",
        **extra: Any,
    ) -> None:
        if not self.enabled:
            return
        append_perf_event(
            self.path,
            run_id=self.run_id,
            stage=stage,
            event=event,
            elapsed_ms=elapsed_ms,
            pair_id=pair_id,
            runtime_sampler=self.runtime_sampler,
            input_bytes=input_bytes,
            entity_count=entity_count,
            cache_namespace=cache_namespace,
            cache_key=cache_key,
            cache_key_hash=cache_key_hash,
            cache_hit=cache_hit,
            warning_count=warning_count,
            error_code=error_code,
            render_mode=render_mode,
            fidelity=fidelity,
            **extra,
        )

    def stage_event(
        self,
        stage: str,
        event: str,
        started_perf: float,
        **extra: Any,
    ) -> None:
        elapsed_ms = max(0.0, (time.perf_counter() - float(started_perf)) * 1000.0)
        self.append(stage=stage, event=event, elapsed_ms=elapsed_ms, **extra)

    def summarize(self, *, write: bool = True) -> dict[str, Any]:
        summary = summarize_perf_events(self.path)
        if write:
            write_perf_events_summary(self.root, summary)
        return summary


def append_perf_event(
    path: Path,
    *,
    run_id: str = "",
    stage: str,
    event: str,
    elapsed_ms: float | None = None,
    pair_id: str = "",
    runtime_sampler: Any = None,
    input_bytes: int | None = None,
    entity_count: int | None = None,
    cache_namespace: str = "",
    cache_key: str = "",
    cache_key_hash: str = "",
    cache_hit: bool | None = None,
    warning_count: int | None = None,
    error_code: str = "",
    render_mode: str = "",
    fidelity: str = "",
    **extra: Any,
) -> None:
    """Append one event as JSONL using same-process locking."""

    snapshot = _runtime_snapshot(runtime_sampler)
    key_hash = cache_key_hash or _hash_cache_key(cache_key)
    payload = {
        "schema_version": PERF_EVENTS_SCHEMA_VERSION,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": str(run_id or ""),
        "pair_id": str(pair_id or ""),
        "stage": str(stage or ""),
        "event": str(event or ""),
        "elapsed_ms": _round_optional(elapsed_ms),
        "rss_mb": _round_optional(snapshot.get("rss_mb")),
        "working_set_mb": _round_optional(snapshot.get("working_set_mb")),
        "spool_mb": _round_optional(snapshot.get("spool_mb")),
        "native_resource_available": (
            bool(snapshot.get("native_resource_available"))
            if snapshot.get("native_resource_available") is not None
            else None
        ),
        "native_resource_sample_count": _optional_int(
            snapshot.get("native_resource_sample_count")
        ),
        "process_handle_count": _optional_int(snapshot.get("process_handle_count")),
        "open_file_descriptor_count": _optional_int(
            snapshot.get("open_file_descriptor_count")
        ),
        "gdi_handle_count": _optional_int(snapshot.get("gdi_handle_count")),
        "user_handle_count": _optional_int(snapshot.get("user_handle_count")),
        "worker_process_count": _optional_int(snapshot.get("worker_process_count")),
        "input_bytes": _optional_int(input_bytes),
        "entity_count": _optional_int(entity_count),
        "cache_namespace": str(cache_namespace or ""),
        "cache_key_hash": key_hash,
        "cache_hit": cache_hit if cache_hit is None else bool(cache_hit),
        "warning_count": _optional_int(warning_count),
        "error_code": str(error_code or ""),
        "render_mode": str(render_mode or ""),
        "fidelity": str(fidelity or ""),
    }
    for key, value in extra.items():
        if key not in payload:
            payload[key] = _json_safe(value)

    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = line.encode("utf-8")
    with _PERF_EVENT_WRITE_LOCK:
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)


def iter_perf_events(path_or_root: Path) -> Iterator[dict[str, Any]]:
    path = _event_path(path_or_root)
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
    except OSError:
        return


def summarize_perf_events(path_or_root: Path | None) -> dict[str, Any]:
    started = time.perf_counter()
    summary = _empty_summary()
    if path_or_root is None:
        summary["status"] = "missing"
        return _finalize_summary_overhead(summary, started)
    path = _event_path(Path(path_or_root))
    summary["summary_input_bytes"] = _file_size(path)
    if not path.exists():
        summary["status"] = "missing"
        return _finalize_summary_overhead(summary, started)

    summary["status"] = "ready"
    elapsed_by_stage: dict[str, list[float]] = {}
    cache_keys_by_namespace: dict[str, list[str]] = {}
    cache_hit_reasons: dict[str, int] = {}
    cache_miss_reasons: dict[str, int] = {}
    reason_code_counts: dict[str, int] = {}
    cache_hit_count = 0
    cache_miss_count = 0
    warning_total = 0
    error_count = 0
    event_count = 0
    total_recorded_elapsed_ms = 0.0
    for event in iter_perf_events(path):
        event_count += 1
        stage = str(event.get("stage") or "")
        name = str(event.get("event") or "")
        summary["stage_counts"][stage] = summary["stage_counts"].get(stage, 0) + 1
        summary["event_counts"][name] = summary["event_counts"].get(name, 0) + 1

        elapsed = _optional_float(event.get("elapsed_ms"))
        if elapsed is not None and elapsed >= 0:
            elapsed_by_stage.setdefault(stage, []).append(elapsed)
            total_recorded_elapsed_ms += elapsed

        for key in ("rss_mb", "working_set_mb", "spool_mb"):
            value = _optional_float(event.get(key))
            if value is not None:
                peak_key = f"peak_{key}"
                summary[peak_key] = max(float(summary.get(peak_key) or 0.0), value)
        for key in (
            "process_handle_count",
            "open_file_descriptor_count",
            "gdi_handle_count",
            "user_handle_count",
            "worker_process_count",
        ):
            value = _optional_int(event.get(key))
            if value is not None:
                summary[f"{key}_max"] = max(
                    int(summary.get(f"{key}_max") or 0),
                    value,
                )
        native_sample_count = _optional_int(event.get("native_resource_sample_count"))
        if native_sample_count is not None:
            summary["native_resource_sample_count"] = max(
                int(summary.get("native_resource_sample_count") or 0),
                native_sample_count,
            )
        if event.get("native_resource_available") is True:
            summary["native_resource_available"] = True

        warning_total += int(event.get("warning_count") or 0)
        if str(event.get("error_code") or ""):
            error_count += 1
        _increment_count(reason_code_counts, event.get("reason_code"))

        if event.get("cache_hit") is True:
            cache_hit_count += 1
            _increment_count(cache_hit_reasons, event.get("cache_hit_reason"))
        elif event.get("cache_hit") is False:
            cache_miss_count += 1
            _increment_count(cache_miss_reasons, event.get("cache_miss_reason"))
        key_hash = str(event.get("cache_key_hash") or "")
        namespace = str(event.get("cache_namespace") or "")
        if key_hash:
            cache_keys_by_namespace.setdefault(namespace, []).append(key_hash)

    if event_count <= 0:
        summary["status"] = "empty"
        return _finalize_summary_overhead(summary, started)

    summary["event_count"] = event_count
    summary["total_recorded_elapsed_ms"] = round(total_recorded_elapsed_ms, 3)
    summary["elapsed_ms_by_stage"] = {
        stage: _percentile_summary(values)
        for stage, values in sorted(elapsed_by_stage.items())
    }
    summary["warning_count"] = warning_total
    summary["error_count"] = error_count
    cache_total = cache_hit_count + cache_miss_count
    summary["cache_hit_count"] = cache_hit_count
    summary["cache_miss_count"] = cache_miss_count
    summary["cache_hit_rate"] = round(cache_hit_count / cache_total, 4) if cache_total else 0.0
    summary["cache_hit_reasons"] = cache_hit_reasons
    summary["cache_miss_reasons"] = cache_miss_reasons
    summary["reason_code_counts"] = reason_code_counts
    duplicates = 0
    duplicate_by_namespace: dict[str, int] = {}
    for namespace, keys in cache_keys_by_namespace.items():
        duplicate_count = len(keys) - len(set(keys))
        if duplicate_count > 0:
            duplicate_by_namespace[namespace or "default"] = duplicate_count
            duplicates += duplicate_count
    summary["cache_key_duplicate_count"] = duplicates
    summary["cache_key_duplicates_by_namespace"] = duplicate_by_namespace
    return _finalize_summary_overhead(summary, started)


def write_perf_events_summary(
    root: Path,
    summary: Optional[dict[str, Any]] = None,
) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    payload = summary if summary is not None else summarize_perf_events(root)
    path = root / PERF_EVENTS_SUMMARY_FILENAME
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def remove_raw_perf_events(root: Path) -> bool:
    path = Path(root) / PERF_EVENTS_FILENAME
    try:
        if path.exists():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def _runtime_snapshot(runtime_sampler: Any) -> dict[str, Any]:
    if runtime_sampler is None:
        return {}
    snapshot = getattr(runtime_sampler, "snapshot", None)
    if callable(snapshot):
        try:
            value = snapshot()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    peek = getattr(runtime_sampler, "peek_working_set_mb", None)
    if callable(peek):
        try:
            return {"working_set_mb": peek()}
        except Exception:
            return {}
    return {}


def _event_path(path_or_root: Path) -> Path:
    path = Path(path_or_root)
    if path.name == PERF_EVENTS_FILENAME:
        return path
    return path / PERF_EVENTS_FILENAME


def _file_size(path: Path) -> int:
    try:
        return int(Path(path).stat().st_size)
    except OSError:
        return 0


def _elapsed_ms_since(started_perf: float) -> float:
    return round(max(0.0, (time.perf_counter() - float(started_perf)) * 1000.0), 3)


def _hash_cache_key(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _empty_summary() -> dict[str, Any]:
    return {
        "schema_version": PERF_EVENTS_SCHEMA_VERSION,
        "status": "missing",
        "event_count": 0,
        "stage_counts": {},
        "event_counts": {},
        "elapsed_ms_by_stage": {},
        "summary_input_bytes": 0,
        "summary_elapsed_ms": 0.0,
        "total_recorded_elapsed_ms": 0.0,
        "summary_overhead_ratio": None,
        "peak_rss_mb": 0.0,
        "peak_working_set_mb": 0.0,
        "peak_spool_mb": 0.0,
        "native_resource_available": False,
        "native_resource_sample_count": 0,
        "process_handle_count_max": 0,
        "open_file_descriptor_count_max": 0,
        "gdi_handle_count_max": 0,
        "user_handle_count_max": 0,
        "worker_process_count_max": 0,
        "cache_hit_count": 0,
        "cache_miss_count": 0,
        "cache_hit_rate": 0.0,
        "cache_key_duplicate_count": 0,
        "cache_key_duplicates_by_namespace": {},
        "cache_hit_reasons": {},
        "cache_miss_reasons": {},
        "reason_code_counts": {},
        "warning_count": 0,
        "error_count": 0,
    }


def _finalize_summary_overhead(
    summary: dict[str, Any],
    started_perf: float,
) -> dict[str, Any]:
    summary["summary_elapsed_ms"] = _elapsed_ms_since(started_perf)
    total_recorded_elapsed_ms = _optional_float(summary.get("total_recorded_elapsed_ms"))
    if total_recorded_elapsed_ms and total_recorded_elapsed_ms > 0:
        summary["summary_overhead_ratio"] = round(
            float(summary["summary_elapsed_ms"]) / total_recorded_elapsed_ms,
            6,
        )
    else:
        summary["summary_overhead_ratio"] = None
    return summary


def _increment_count(counts: dict[str, int], value: Any) -> None:
    key = str(value or "").strip()
    if not key:
        return
    counts[key] = counts.get(key, 0) + 1


def _percentile_summary(values: Iterable[float]) -> dict[str, float]:
    samples = sorted(float(v) for v in values if float(v) >= 0)
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0}
    return {
        "p50": round(_percentile(samples, 0.50), 3),
        "p95": round(_percentile(samples, 0.95), 3),
        "p99": round(_percentile(samples, 0.99), 3),
        "mean": round(sum(samples) / len(samples), 3),
    }


def _percentile(samples: list[float], q: float) -> float:
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


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_optional(value: Any) -> Optional[float]:
    number = _optional_float(value)
    return round(number, 3) if number is not None else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = [
    "PERF_EVENTS_SCHEMA_VERSION",
    "PERF_EVENTS_FILENAME",
    "PERF_EVENTS_SUMMARY_FILENAME",
    "PerfEventWriter",
    "append_perf_event",
    "iter_perf_events",
    "summarize_perf_events",
    "write_perf_events_summary",
    "remove_raw_perf_events",
]
