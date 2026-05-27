# -*- coding: utf-8 -*-
"""Runtime budget measurement (memory + first-review-ready timing).

외부 감사 리뷰 권고 ① 대응 모듈. 기존 ``timings.total_s`` 와
``change_records_in_memory`` 가 proxy metric 이라는 finding 을 해소하기
위해 다음을 직접 측정한다:

- ``peak_working_set_mb``: 프로세스 실 메모리 peak (Windows: peak_wset,
  타 OS: rss 상한)
- ``peak_disk_spool_mb``: tempdir 누적 spool 사이즈 peak
- ``first_review_ready_s``: pipeline.start() 시점부터 첫 review zone 이
  user 에게 노출 가능 ("first paint") 시점까지 wall time
- ``peak_compare_state_bytes``: change list + envelope cache 결합 추정치

설계 원칙
=========
1. **독립 모듈**: ``run_contract`` 의 RunManifestWriter 를 건드리지 않고
   manifest.outputs 또는 별도 ``runtime_budget`` 키에 결과 사출.
2. **선택적 측정**: ``RuntimeBudgetSampler.start_sampling()`` 를 호출
   하지 않으면 모든 값이 ``None`` (회귀 영향 0). 기존 fixture 유지.
3. **Sampling overhead 최소화**: 100ms 인터벌 daemon thread, peak 만 보존.
4. **Cross-platform fallback**: psutil ``memory_info().rss`` 를 항상
   사용. Windows 전용 ``peak_wset`` 은 ``memory_info()`` 가 노출 시에만.
5. **첫-검토 준비 타이밍**: pipeline 이 명시적으로 ``mark_first_review_ready()``
   를 호출해야 한다. preview 첫 zone path 가 생성될 때 호출 권장.

회귀 가드: 기존 ``RuntimeBudget(None, ...)`` 직렬화 결과는 fixture 11-15
와 호환 (모든 필드 optional). audit gate 는 ``--max-peak-working-set-mb``
와 ``--max-first-review-ready-s`` 가 명시될 때만 strict 검증.

Author: TEKLA_MCP Team
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .native_resource_sampler import (
    NATIVE_RESOURCE_SCHEMA_VERSION,
    native_resource_snapshot,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4  # P5-G23: +native resource / worker plateau metrics
SAMPLE_INTERVAL_S = 0.1
SPOOL_SCAN_INTERVAL_S = 1.0
DEFAULT_DISK_SPOOL_MB_CAP = 1024.0
DEFAULT_VIEWER_MEMORY_BUDGET_MB = 4096.0

_NATIVE_RESOURCE_COUNT_KEYS = (
    "process_handle_count",
    "open_file_descriptor_count",
    "gdi_handle_count",
    "user_handle_count",
)
_NATIVE_COUNT_KEYS = (
    *_NATIVE_RESOURCE_COUNT_KEYS,
    "worker_process_count",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MemoryBudgetExceeded(RuntimeError):
    """Raised when peak working-set exceeds a configured cap during a stage.

    Carries machine-readable fields so GUI callers can produce a localized
    dialog (current/max/stage) without parsing strings.
    """

    def __init__(self, *, stage: str, current_mb: float, max_mb: float) -> None:
        self.stage = stage
        self.current_mb = float(current_mb)
        self.max_mb = float(max_mb)
        super().__init__(
            f"memory_budget_exceeded:{stage}:current={current_mb:.1f}MB>max={max_mb:.1f}MB"
        )


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class RuntimeBudget:
    """단일 비교 실행의 runtime budget 측정값.

    모든 필드는 ``None`` 가능 (sampler 미사용 시). 검증 gate 는
    ``None`` 을 통과로 간주하지 않고 명시적으로 ``"missing"`` 으로
    실패 처리해야 한다 (audit `_check_runtime_budget` 책임).

    ``schema_version`` 은 manifest round-trip 안정성을 위해 항상 포함.
    """

    schema_version: int = SCHEMA_VERSION
    peak_working_set_mb: Optional[float] = None
    peak_rss_mb: Optional[float] = None  # cross-platform fallback
    peak_disk_spool_mb: Optional[float] = None
    first_review_ready_s: Optional[float] = None
    peak_compare_state_bytes: Optional[int] = None
    total_s: Optional[float] = None
    sample_count: int = 0
    sampler_active: bool = False
    notes: list[str] = field(default_factory=list)
    # Plan §16 Phase C-2.1 — closes the auditor CRITICAL finding that
    # ``peak_compare_state_bytes`` is a post-hoc proxy. ``peak_comparator_changes``
    # captures the monotonic-max pre-truncate length recorded inside
    # ``DxfComparator.compare()`` (see Phase C-1). ``time_to_first_stream_record_ms``
    # exposes how quickly the streaming spool path begins emitting records, so
    # operators can detect a stalled comparator (Phase C-3.1).
    peak_comparator_changes: Optional[int] = None
    time_to_first_stream_record_ms: Optional[float] = None
    # P5-G14 — instrumentation must prove its own overhead is bounded.
    sampler_tick_ms: Optional[dict[str, float]] = None
    spool_scan_ms: Optional[dict[str, float]] = None
    sampler_overhead_ms: Optional[float] = None
    telemetry_overhead_ratio: Optional[float] = None
    spool_scan_count: int = 0
    spool_scan_file_count: int = 0
    spool_scan_bytes: int = 0
    # P5-G23 — native resource / worker-tree plateau evidence.
    native_resource_schema_version: int = NATIVE_RESOURCE_SCHEMA_VERSION
    native_resource_platform: str = ""
    native_resource_available: bool = False
    native_resource_sample_count: int = 0
    start_process_handle_count: Optional[int] = None
    final_process_handle_count: Optional[int] = None
    peak_process_handle_count: Optional[int] = None
    process_handle_positive_delta: Optional[int] = None
    start_open_file_descriptor_count: Optional[int] = None
    final_open_file_descriptor_count: Optional[int] = None
    peak_open_file_descriptor_count: Optional[int] = None
    open_file_descriptor_positive_delta: Optional[int] = None
    start_gdi_handle_count: Optional[int] = None
    final_gdi_handle_count: Optional[int] = None
    peak_gdi_handle_count: Optional[int] = None
    gdi_handle_positive_delta: Optional[int] = None
    start_user_handle_count: Optional[int] = None
    final_user_handle_count: Optional[int] = None
    peak_user_handle_count: Optional[int] = None
    user_handle_positive_delta: Optional[int] = None
    start_worker_process_count: Optional[int] = None
    final_worker_process_count: Optional[int] = None
    peak_worker_process_count: Optional[int] = None
    worker_process_positive_delta: Optional[int] = None
    native_resource_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Round numeric fields for stable JSON diffs.
        for key in (
            "peak_working_set_mb",
            "peak_rss_mb",
            "peak_disk_spool_mb",
            "total_s",
            "sampler_overhead_ms",
            "telemetry_overhead_ratio",
        ):
            value = payload.get(key)
            if isinstance(value, (int, float)):
                payload[key] = round(float(value), 3)
        if isinstance(payload.get("first_review_ready_s"), (int, float)):
            payload["first_review_ready_s"] = round(
                float(payload["first_review_ready_s"]), 3
            )
        # Plan §16 Phase C-2.1 — round the streaming metric too (millisecond
        # precision is sufficient; trims spurious float jitter from JSON diffs).
        if isinstance(payload.get("time_to_first_stream_record_ms"), (int, float)):
            payload["time_to_first_stream_record_ms"] = round(
                float(payload["time_to_first_stream_record_ms"]), 3
            )
        for key in ("sampler_tick_ms", "spool_scan_ms"):
            if isinstance(payload.get(key), dict):
                payload[key] = _round_percentile_payload(payload[key])
        return payload


# ---------------------------------------------------------------------------
# Sampler
# ---------------------------------------------------------------------------


class RuntimeBudgetSampler:
    """Background sampler tracking memory + first-review-ready timing.

    Usage::

        sampler = RuntimeBudgetSampler(spool_dirs=[Path("tmp/")])
        sampler.start_sampling()
        ... pipeline work ...
        sampler.mark_first_review_ready()  # 첫 zone path 생성 직후
        ... more work ...
        budget = sampler.stop()

    스레드 안전성: peak 갱신은 GIL 보호. peak_compare_state_bytes 는
    호출자가 ``record_compare_state_bytes(int)`` 로 명시 보고.
    """

    def __init__(
        self,
        *,
        spool_dirs: Optional[list[Path]] = None,
        sample_interval_s: float = SAMPLE_INTERVAL_S,
        spool_scan_interval_s: float = SPOOL_SCAN_INTERVAL_S,
    ) -> None:
        self._spool_dirs = [Path(p) for p in (spool_dirs or [])]
        self._sample_interval_s = max(0.01, float(sample_interval_s))
        self._spool_scan_interval_s = max(
            self._sample_interval_s,
            float(spool_scan_interval_s),
        )
        self._last_spool_scan_perf: Optional[float] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._start_perf: Optional[float] = None
        self._first_review_ready_perf: Optional[float] = None

        # Peak trackers (initialised when sampling begins)
        self._peak_working_set_bytes: int = 0
        self._peak_rss_bytes: int = 0
        self._peak_spool_bytes: int = 0
        self._peak_compare_state_bytes: int = 0
        # Plan §16 Phase C-2.1 — pipeline-harvested comparator metrics
        self._peak_comparator_changes: int = 0
        self._time_to_first_stream_record_ms: Optional[float] = None
        self._sample_count: int = 0
        self._notes: list[str] = []
        self._psutil_proc: Any = None
        self._sampler_tick_ms_values: list[float] = []
        self._spool_scan_ms_values: list[float] = []
        self._spool_scan_count: int = 0
        self._spool_scan_file_count: int = 0
        self._spool_scan_bytes: int = 0
        self._native_start_counts: dict[str, int] = {}
        self._native_final_counts: dict[str, int] = {}
        self._native_peak_counts: dict[str, int] = {}
        self._native_resource_sample_count: int = 0
        self._native_resource_available: bool = False
        self._native_resource_platform: str = ""
        self._native_resource_notes: list[str] = []
        self._last_native_resource_sample_perf: Optional[float] = None
        self._last_worker_sample_perf: Optional[float] = None

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def start_sampling(self) -> None:
        """Begin background sampling. Idempotent — second call is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        try:
            import psutil  # type: ignore

            self._psutil_proc = psutil.Process()
        except Exception as exc:
            self._notes.append(f"psutil_unavailable:{exc!r}")
            logger.warning("RuntimeBudgetSampler: psutil unavailable (%s)", exc)
            self._psutil_proc = None

        self._start_perf = time.perf_counter()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="RuntimeBudgetSampler",
            daemon=True,
        )
        self._thread.start()

    def peek_working_set_mb(self) -> Optional[float]:
        """Return the current peak working-set in MiB without stopping the sampler.

        Useful for inline budget checks during long-running pipeline stages
        (e.g. viewer_package tile cache write) so a cap can be enforced before
        the OS starts paging. Returns ``None`` when sampling has never run or
        psutil is unavailable so callers can no-op cleanly.

        Cross-platform fallback: when ``peak_wset`` is zero (non-Windows), the
        last sampled RSS high-water mark is returned instead.
        """
        with self._lock:
            wset = self._peak_working_set_bytes
            rss = self._peak_rss_bytes
        if wset == 0 and rss == 0:
            return None
        effective = wset if wset > 0 else rss
        return effective / (1024 * 1024)

    def snapshot(self) -> dict[str, Any]:
        """Return a non-stopping point-in-time snapshot for stage telemetry."""

        with self._lock:
            working_set = self._peak_working_set_bytes
            rss = self._peak_rss_bytes
            spool = self._peak_spool_bytes
            sample_count = self._sample_count
            sampler_tick_ms = _percentile_summary(self._sampler_tick_ms_values)
            spool_scan_ms = _percentile_summary(self._spool_scan_ms_values)
            sampler_overhead_ms = sum(self._sampler_tick_ms_values)
            spool_scan_count = self._spool_scan_count
            spool_scan_file_count = self._spool_scan_file_count
            spool_scan_bytes = self._spool_scan_bytes
            start_perf = self._start_perf
            native_current = dict(self._native_final_counts)
            native_peak = dict(self._native_peak_counts)
            native_resource_available = self._native_resource_available
            native_resource_sample_count = self._native_resource_sample_count
            native_resource_platform = self._native_resource_platform
        if working_set == 0 and rss > 0:
            working_set = rss
        elapsed_ms = (
            max(0.0, (time.perf_counter() - start_perf) * 1000.0)
            if start_perf is not None
            else 0.0
        )
        return {
            "working_set_mb": (
                working_set / (1024 * 1024) if working_set else None
            ),
            "rss_mb": rss / (1024 * 1024) if rss else None,
            "spool_mb": spool / (1024 * 1024) if spool else None,
            "sample_count": sample_count,
            "sampler_tick_ms": sampler_tick_ms,
            "spool_scan_ms": spool_scan_ms,
            "sampler_overhead_ms": sampler_overhead_ms,
            "telemetry_overhead_ratio": (
                sampler_overhead_ms / elapsed_ms if elapsed_ms > 0 else None
            ),
            "spool_scan_count": spool_scan_count,
            "spool_scan_file_count": spool_scan_file_count,
            "spool_scan_bytes": spool_scan_bytes,
            "native_resource_schema_version": NATIVE_RESOURCE_SCHEMA_VERSION,
            "native_resource_platform": native_resource_platform,
            "native_resource_available": native_resource_available,
            "native_resource_sample_count": native_resource_sample_count,
            "process_handle_count": native_current.get("process_handle_count"),
            "open_file_descriptor_count": native_current.get("open_file_descriptor_count"),
            "gdi_handle_count": native_current.get("gdi_handle_count"),
            "user_handle_count": native_current.get("user_handle_count"),
            "worker_process_count": native_current.get("worker_process_count"),
            "peak_process_handle_count": native_peak.get("process_handle_count"),
            "peak_open_file_descriptor_count": native_peak.get("open_file_descriptor_count"),
            "peak_gdi_handle_count": native_peak.get("gdi_handle_count"),
            "peak_user_handle_count": native_peak.get("user_handle_count"),
            "peak_worker_process_count": native_peak.get("worker_process_count"),
        }

    def assert_within_memory_budget(
        self, max_mb: Optional[float], *, stage: str = "viewer_package"
    ) -> None:
        """Raise ``MemoryBudgetExceeded`` when the configured cap is exceeded.

        No-op when ``max_mb`` is None or no measurement is available yet.
        Call this from inside long loops (per-pair, per-tile) to abort early
        before the OS starts paging.
        """
        if max_mb is None or max_mb <= 0:
            return
        current = self.peek_working_set_mb()
        if current is None:
            return
        if current > max_mb:
            raise MemoryBudgetExceeded(
                stage=stage,
                current_mb=current,
                max_mb=float(max_mb),
            )

    def mark_first_review_ready(self) -> None:
        """Record the moment the first review zone is user-visible.

        Multiple calls keep the *first* recorded time. Safe to call from any
        pipeline stage; if sampling is not active, the call is silently
        ignored so callers don't need to guard.
        """
        with self._lock:
            if self._first_review_ready_perf is not None:
                return
            if self._start_perf is None:
                # Sampler not active — record relative to "now" so total_s and
                # first_review_ready_s remain None instead of misleading 0.0.
                self._notes.append("first_review_ready_called_before_start")
                return
            self._first_review_ready_perf = time.perf_counter()

    def record_compare_state_bytes(self, value: int) -> None:
        """Caller-supplied estimate (change list + envelope cache + R-tree)."""
        try:
            value_int = int(value)
        except (TypeError, ValueError):
            return
        with self._lock:
            if value_int > self._peak_compare_state_bytes:
                self._peak_compare_state_bytes = value_int

    def record_comparator_peak_changes(self, count: int) -> None:
        """Pipeline-harvested peak from ``DxfComparator.compare()`` (Plan §16).

        Closes the auditor CRITICAL finding: ``peak_compare_state_bytes`` is a
        post-hoc proxy that cannot detect in-flight memory spikes inside the
        comparator hot loop. Phase C-1 added the in-band ``peak_changes_pre_truncate``
        stat; this accessor harvests it across a pipeline run keeping a
        monotonic max so multi-pair runs report the worst case.

        Non-positive ``count`` is treated as a no-op — callers may pass 0 when
        the comparator did not surface the metric.
        """
        try:
            count_int = int(count)
        except (TypeError, ValueError):
            return
        if count_int <= 0:
            return
        with self._lock:
            if count_int > self._peak_comparator_changes:
                self._peak_comparator_changes = count_int

    def record_time_to_first_stream_record_ms(self, ms: float) -> None:
        """First-occurrence-wins time to the first streamed change record (Plan §16).

        Captures how long elapses between ``DxfComparator.compare()`` entry and
        the first ``_write_change_zone_stream`` callsite producing a record.
        Operators use this to detect stalled comparators — large values
        relative to ``total_s`` indicate the comparator is still in the
        accumulation phase and has not begun streaming.

        First non-positive call is ignored. Once a positive value is recorded
        subsequent calls are ignored so the first record (the genuine
        ``time-to-first-stream``) wins.
        """
        try:
            ms_value = float(ms)
        except (TypeError, ValueError):
            return
        if ms_value <= 0:
            return
        with self._lock:
            if self._time_to_first_stream_record_ms is None:
                self._time_to_first_stream_record_ms = ms_value

    def stop(self) -> RuntimeBudget:
        """Stop sampling and return the resulting RuntimeBudget snapshot."""
        end_perf = time.perf_counter()
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        # One final synchronous measurement so very short runs still produce
        # a non-zero working set value.
        if self._start_perf is not None or self._sample_count > 0:
            self._sample_once(force_spool_scan=True)

        with self._lock:
            total_s: Optional[float] = None
            first_review_ready_s: Optional[float] = None
            if self._start_perf is not None:
                total_s = max(0.0, end_perf - self._start_perf)
                if self._first_review_ready_perf is not None:
                    first_review_ready_s = max(
                        0.0, self._first_review_ready_perf - self._start_perf
                    )
            sampler_tick_ms = _percentile_summary(self._sampler_tick_ms_values)
            spool_scan_ms = _percentile_summary(self._spool_scan_ms_values)
            sampler_overhead_ms = sum(self._sampler_tick_ms_values)
            telemetry_overhead_ratio = (
                sampler_overhead_ms / (total_s * 1000.0)
                if total_s and total_s > 0
                else None
            )
            native_start = dict(self._native_start_counts)
            native_final = dict(self._native_final_counts)
            native_peak = dict(self._native_peak_counts)
            native_resource_available = self._native_resource_available
            native_resource_sample_count = self._native_resource_sample_count
            native_resource_platform = self._native_resource_platform
            native_resource_notes = list(self._native_resource_notes)
            return RuntimeBudget(
                peak_working_set_mb=(
                    self._peak_working_set_bytes / (1024 * 1024)
                    if self._peak_working_set_bytes
                    else None
                ),
                peak_rss_mb=(
                    self._peak_rss_bytes / (1024 * 1024)
                    if self._peak_rss_bytes
                    else None
                ),
                peak_disk_spool_mb=(
                    self._peak_spool_bytes / (1024 * 1024)
                    if self._peak_spool_bytes
                    else None
                ),
                first_review_ready_s=first_review_ready_s,
                peak_compare_state_bytes=(
                    self._peak_compare_state_bytes
                    if self._peak_compare_state_bytes
                    else None
                ),
                total_s=total_s,
                sample_count=self._sample_count,
                sampler_active=self._psutil_proc is not None,
                notes=list(self._notes),
                # Plan §16 Phase C-2.1 — surface pipeline-harvested comparator metrics
                peak_comparator_changes=(
                    self._peak_comparator_changes or None
                ),
                time_to_first_stream_record_ms=self._time_to_first_stream_record_ms,
                sampler_tick_ms=sampler_tick_ms,
                spool_scan_ms=spool_scan_ms,
                sampler_overhead_ms=sampler_overhead_ms,
                telemetry_overhead_ratio=telemetry_overhead_ratio,
                spool_scan_count=self._spool_scan_count,
                spool_scan_file_count=self._spool_scan_file_count,
                spool_scan_bytes=self._spool_scan_bytes,
                native_resource_platform=native_resource_platform,
                native_resource_available=native_resource_available,
                native_resource_sample_count=native_resource_sample_count,
                start_process_handle_count=native_start.get("process_handle_count"),
                final_process_handle_count=native_final.get("process_handle_count"),
                peak_process_handle_count=native_peak.get("process_handle_count"),
                process_handle_positive_delta=_positive_delta(
                    native_start.get("process_handle_count"),
                    native_final.get("process_handle_count"),
                ),
                start_open_file_descriptor_count=native_start.get("open_file_descriptor_count"),
                final_open_file_descriptor_count=native_final.get("open_file_descriptor_count"),
                peak_open_file_descriptor_count=native_peak.get("open_file_descriptor_count"),
                open_file_descriptor_positive_delta=_positive_delta(
                    native_start.get("open_file_descriptor_count"),
                    native_final.get("open_file_descriptor_count"),
                ),
                start_gdi_handle_count=native_start.get("gdi_handle_count"),
                final_gdi_handle_count=native_final.get("gdi_handle_count"),
                peak_gdi_handle_count=native_peak.get("gdi_handle_count"),
                gdi_handle_positive_delta=_positive_delta(
                    native_start.get("gdi_handle_count"),
                    native_final.get("gdi_handle_count"),
                ),
                start_user_handle_count=native_start.get("user_handle_count"),
                final_user_handle_count=native_final.get("user_handle_count"),
                peak_user_handle_count=native_peak.get("user_handle_count"),
                user_handle_positive_delta=_positive_delta(
                    native_start.get("user_handle_count"),
                    native_final.get("user_handle_count"),
                ),
                start_worker_process_count=native_start.get("worker_process_count"),
                final_worker_process_count=native_final.get("worker_process_count"),
                peak_worker_process_count=native_peak.get("worker_process_count"),
                worker_process_positive_delta=_positive_delta(
                    native_start.get("worker_process_count"),
                    native_final.get("worker_process_count"),
                ),
                native_resource_notes=native_resource_notes,
            )

    # ------------------------------------------------------------------
    # Internal sampling loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._sample_once()
            self._stop_event.wait(self._sample_interval_s)

    def _sample_once(self, *, force_spool_scan: bool = False) -> None:
        tick_started = time.perf_counter()
        try:
            proc = self._psutil_proc
            if proc is None and not self._spool_dirs and self._start_perf is None:
                return
            with self._lock:
                self._sample_count += 1

            if proc is not None:
                try:
                    info = proc.memory_info()
                except Exception as exc:  # process gone, permission denied, etc.
                    self._notes.append(f"memory_info_failed:{exc!r}")
                    self._psutil_proc = None
                    return
                rss = int(getattr(info, "rss", 0) or 0)
                wset = int(getattr(info, "peak_wset", 0) or 0)
                with self._lock:
                    if rss > self._peak_rss_bytes:
                        self._peak_rss_bytes = rss
                    if wset > self._peak_working_set_bytes:
                        self._peak_working_set_bytes = wset
                    # Fallback: when peak_wset is unavailable (non-Windows),
                    # mirror the rss high-water mark so the budget still reports
                    # a non-None working-set value rather than masking the metric.
                    if (
                        wset == 0
                        and rss > self._peak_working_set_bytes
                    ):
                        self._peak_working_set_bytes = rss

            sample_native_resource = self._should_sample_native_resource(
                force=force_spool_scan
            )
            sample_worker = self._should_sample_worker(force=force_spool_scan)
            if sample_native_resource or sample_worker:
                resource = native_resource_snapshot(
                    proc=proc,
                    include_process_memory=False,
                    include_process_resources=sample_native_resource,
                    include_worker_processes=sample_worker,
                )
                with self._lock:
                    self._record_native_resource_snapshot_locked(resource)

            if self._spool_dirs and self._should_scan_spool(force=force_spool_scan):
                scan_started = time.perf_counter()
                spool_bytes = 0
                spool_file_count = 0
                for spool_dir in self._spool_dirs:
                    scanned_bytes, scanned_files = _directory_scan(spool_dir)
                    spool_bytes += scanned_bytes
                    spool_file_count += scanned_files
                scan_elapsed_ms = (time.perf_counter() - scan_started) * 1000.0
                with self._lock:
                    self._spool_scan_ms_values.append(scan_elapsed_ms)
                    self._spool_scan_count += 1
                    self._spool_scan_file_count += spool_file_count
                    self._spool_scan_bytes += spool_bytes
                    if spool_bytes > self._peak_spool_bytes:
                        self._peak_spool_bytes = spool_bytes
        finally:
            tick_elapsed_ms = (time.perf_counter() - tick_started) * 1000.0
            with self._lock:
                self._sampler_tick_ms_values.append(tick_elapsed_ms)

    def _should_scan_spool(self, *, force: bool = False) -> bool:
        if force:
            self._last_spool_scan_perf = time.perf_counter()
            return True
        now = time.perf_counter()
        last = self._last_spool_scan_perf
        if last is not None and (now - last) < self._spool_scan_interval_s:
            return False
        self._last_spool_scan_perf = now
        return True

    def _should_sample_worker(self, *, force: bool = False) -> bool:
        if force:
            self._last_worker_sample_perf = time.perf_counter()
            return True
        now = time.perf_counter()
        last = self._last_worker_sample_perf
        if last is not None and (now - last) < self._spool_scan_interval_s:
            return False
        self._last_worker_sample_perf = now
        return True

    def _should_sample_native_resource(self, *, force: bool = False) -> bool:
        if force:
            self._last_native_resource_sample_perf = time.perf_counter()
            return True
        now = time.perf_counter()
        last = self._last_native_resource_sample_perf
        if last is not None and (now - last) < self._spool_scan_interval_s:
            return False
        self._last_native_resource_sample_perf = now
        return True

    def _record_native_resource_snapshot_locked(self, resource: dict[str, Any]) -> None:
        if not isinstance(resource, dict):
            return
        platform_value = str(resource.get("native_resource_platform") or "")
        if platform_value:
            self._native_resource_platform = platform_value
        notes = resource.get("native_resource_notes")
        if isinstance(notes, list):
            for note in notes:
                _append_unique(self._native_resource_notes, str(note))

        has_native_count = any(
            resource.get(key) is not None for key in _NATIVE_RESOURCE_COUNT_KEYS
        )
        if resource.get("native_resource_available") is True or has_native_count:
            self._native_resource_available = True
            self._native_resource_sample_count += 1

        for key in _NATIVE_COUNT_KEYS:
            value = _optional_int(resource.get(key))
            if value is None:
                continue
            if key not in self._native_start_counts:
                self._native_start_counts[key] = value
            self._native_final_counts[key] = value
            previous_peak = self._native_peak_counts.get(key)
            if previous_peak is None or value > previous_peak:
                self._native_peak_counts[key] = value


def _directory_size(path: Path) -> int:
    """Recursively sum file sizes under ``path``. Failures yield 0."""
    return _directory_scan(path)[0]


def _directory_scan(path: Path) -> tuple[int, int]:
    """Recursively sum file sizes and file count under ``path``."""
    if not path.exists():
        return 0, 0
    total = 0
    file_count = 0
    stack = [Path(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            file_count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, file_count


# ---------------------------------------------------------------------------
# Audit/validator helpers
# ---------------------------------------------------------------------------


def runtime_budget_from_dict(payload: Optional[dict[str, Any]]) -> RuntimeBudget:
    """Manifest round-trip: ``RuntimeBudget`` payload → dataclass.

    Unknown keys are ignored; missing numeric fields stay ``None``. ``None``
    is preserved (vs coerced to 0) so audit can distinguish "not measured"
    from "measured-and-zero".
    """
    if not isinstance(payload, dict):
        return RuntimeBudget()
    return RuntimeBudget(
        schema_version=int(payload.get("schema_version") or SCHEMA_VERSION),
        peak_working_set_mb=_optional_float(payload.get("peak_working_set_mb")),
        peak_rss_mb=_optional_float(payload.get("peak_rss_mb")),
        peak_disk_spool_mb=_optional_float(payload.get("peak_disk_spool_mb")),
        first_review_ready_s=_optional_float(payload.get("first_review_ready_s")),
        peak_compare_state_bytes=_optional_int(payload.get("peak_compare_state_bytes")),
        total_s=_optional_float(payload.get("total_s")),
        sample_count=int(payload.get("sample_count") or 0),
        sampler_active=bool(payload.get("sampler_active") or False),
        notes=[str(n) for n in (payload.get("notes") or [])],
        # Plan §16 Phase C-2.1 — round-trip the new comparator-derived metrics
        peak_comparator_changes=_optional_int(payload.get("peak_comparator_changes")),
        time_to_first_stream_record_ms=_optional_float(
            payload.get("time_to_first_stream_record_ms")
        ),
        sampler_tick_ms=_optional_percentile_payload(payload.get("sampler_tick_ms")),
        spool_scan_ms=_optional_percentile_payload(payload.get("spool_scan_ms")),
        sampler_overhead_ms=_optional_float(payload.get("sampler_overhead_ms")),
        telemetry_overhead_ratio=_optional_float(payload.get("telemetry_overhead_ratio")),
        spool_scan_count=_optional_int(payload.get("spool_scan_count")) or 0,
        spool_scan_file_count=_optional_int(payload.get("spool_scan_file_count")) or 0,
        spool_scan_bytes=_optional_int(payload.get("spool_scan_bytes")) or 0,
        native_resource_schema_version=(
            _optional_int(payload.get("native_resource_schema_version"))
            or NATIVE_RESOURCE_SCHEMA_VERSION
        ),
        native_resource_platform=str(payload.get("native_resource_platform") or ""),
        native_resource_available=bool(payload.get("native_resource_available") or False),
        native_resource_sample_count=_optional_int(payload.get("native_resource_sample_count")) or 0,
        start_process_handle_count=_optional_int(payload.get("start_process_handle_count")),
        final_process_handle_count=_optional_int(payload.get("final_process_handle_count")),
        peak_process_handle_count=_optional_int(payload.get("peak_process_handle_count")),
        process_handle_positive_delta=_optional_int(payload.get("process_handle_positive_delta")),
        start_open_file_descriptor_count=_optional_int(payload.get("start_open_file_descriptor_count")),
        final_open_file_descriptor_count=_optional_int(payload.get("final_open_file_descriptor_count")),
        peak_open_file_descriptor_count=_optional_int(payload.get("peak_open_file_descriptor_count")),
        open_file_descriptor_positive_delta=_optional_int(payload.get("open_file_descriptor_positive_delta")),
        start_gdi_handle_count=_optional_int(payload.get("start_gdi_handle_count")),
        final_gdi_handle_count=_optional_int(payload.get("final_gdi_handle_count")),
        peak_gdi_handle_count=_optional_int(payload.get("peak_gdi_handle_count")),
        gdi_handle_positive_delta=_optional_int(payload.get("gdi_handle_positive_delta")),
        start_user_handle_count=_optional_int(payload.get("start_user_handle_count")),
        final_user_handle_count=_optional_int(payload.get("final_user_handle_count")),
        peak_user_handle_count=_optional_int(payload.get("peak_user_handle_count")),
        user_handle_positive_delta=_optional_int(payload.get("user_handle_positive_delta")),
        start_worker_process_count=_optional_int(payload.get("start_worker_process_count")),
        final_worker_process_count=_optional_int(payload.get("final_worker_process_count")),
        peak_worker_process_count=_optional_int(payload.get("peak_worker_process_count")),
        worker_process_positive_delta=_optional_int(payload.get("worker_process_positive_delta")),
        native_resource_notes=[
            str(note) for note in (payload.get("native_resource_notes") or [])
        ],
    )


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_delta(start: Optional[int], final: Optional[int]) -> Optional[int]:
    if start is None or final is None:
        return None
    return max(0, int(final) - int(start))


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _optional_percentile_payload(value: Any) -> Optional[dict[str, float]]:
    if not isinstance(value, dict):
        return None
    payload = _round_percentile_payload(value)
    return payload if payload else None


def _round_percentile_payload(value: dict[str, Any]) -> dict[str, float]:
    payload: dict[str, float] = {}
    for key in ("p50", "p95", "p99", "max", "mean"):
        number = _optional_float(value.get(key))
        if number is not None:
            payload[key] = round(number, 3)
    return payload


def _percentile_summary(values: list[float]) -> Optional[dict[str, float]]:
    samples = sorted(float(value) for value in values if float(value) >= 0.0)
    if not samples:
        return None
    return {
        "p50": round(_percentile(samples, 0.50), 3),
        "p95": round(_percentile(samples, 0.95), 3),
        "p99": round(_percentile(samples, 0.99), 3),
        "max": round(samples[-1], 3),
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


__all__ = [
    "DEFAULT_DISK_SPOOL_MB_CAP",
    "DEFAULT_VIEWER_MEMORY_BUDGET_MB",
    "MemoryBudgetExceeded",
    "NATIVE_RESOURCE_SCHEMA_VERSION",
    "RuntimeBudget",
    "RuntimeBudgetSampler",
    "SAMPLE_INTERVAL_S",
    "SCHEMA_VERSION",
    "SPOOL_SCAN_INTERVAL_S",
    "runtime_budget_from_dict",
]
