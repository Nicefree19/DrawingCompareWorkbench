# -*- coding: utf-8 -*-
"""Tests for Plan §15.5 (HIGH-2) dual-queue batch scheduler.

External auditor #2 finding:
    ``_resolve_batch_workers()`` previously returned workers=1 whenever
    ANY confirmed candidate qualified as a large CAD pair, downshifting
    the entire batch to sequential.  These tests pin the new behaviour:

    * Large lane runs serially (workers=1) on its own pool.
    * Normal lane keeps the resolved parallel worker count.
    * summary.items preserves input candidate order (R1 risk).
    * Cancel propagates to BOTH pools (R2 risk).
    * Backward-compat: all-large input still falls through to the
      legacy single-pool path.

Tests use a fake ``compare_candidate`` (monkeypatched) so we don't need
real DXF/PDF files — the focus here is the scheduler, not the diff.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import List
from unittest import mock

import pytest

from src.services.comparison.base import ComparisonResult
from src.services.comparison.drawing_batch import (
    BatchCompareJob,
    BatchCompareOptions,
    DrawingFileDescriptor,
    DrawingKind,
    FilenameIdentity,
    MatchCandidate,
    MatchStatus,
    _candidate_contains_large_cad,
    _partition_candidates_by_size,
    _resolve_batch_workers,
    parse_filename_identity,
)


# ---------------------------------------------------------------------------
# Fixture helpers (mirrors patterns in test_drawing_batch.py)
# ---------------------------------------------------------------------------


def _descriptor(
    name: str,
    *,
    entity_counts: dict[str, int] | None = None,
) -> DrawingFileDescriptor:
    identity = parse_filename_identity(name)
    return DrawingFileDescriptor(
        path=str(Path("C:/drawings") / name),
        kind=DrawingKind.CAD,
        extension=Path(name).suffix.lower(),
        relative_path=name,
        identity=identity,
        entity_counts=entity_counts or {},
    )


def _small_candidate(name: str) -> MatchCandidate:
    """Build a confirmed candidate with no entity counts (small)."""
    return MatchCandidate(
        source_a=_descriptor(f"{name}.dwg"),
        source_b=_descriptor(f"{name}_REV1.dwg"),
        score=0.9,
        status=MatchStatus.AUTO_CONFIRMED,
    )


def _large_candidate(name: str, entity_count: int = 1_000_000) -> MatchCandidate:
    """Build a confirmed candidate whose A side exceeds the entity threshold."""
    return MatchCandidate(
        source_a=_descriptor(
            f"{name}.dwg",
            entity_counts={"LINE": entity_count},
        ),
        source_b=_descriptor(f"{name}_REV1.dwg"),
        score=0.9,
        status=MatchStatus.AUTO_CONFIRMED,
    )


# ---------------------------------------------------------------------------
# Test 1 — partition helper classifies candidates correctly
# ---------------------------------------------------------------------------


def test_dual_queue_partitions_by_predicted_load() -> None:
    """``_partition_candidates_by_size`` returns ``(large, normal)`` lanes
    that match ``_candidate_contains_large_cad`` exactly and stamps the
    ``predicted_load`` field on each candidate."""

    small_a = _small_candidate("S-200")
    large_b = _large_candidate("L-001")
    small_c = _small_candidate("S-201")
    large_d = _large_candidate("L-002")

    options = BatchCompareOptions()
    large_lane, normal_lane = _partition_candidates_by_size(
        [small_a, large_b, small_c, large_d], options
    )

    assert large_lane == [large_b, large_d]
    assert normal_lane == [small_a, small_c]
    # Predicate consistency (sanity)
    for c in large_lane:
        assert _candidate_contains_large_cad(c, options) is True
    for c in normal_lane:
        assert _candidate_contains_large_cad(c, options) is False
    # Side-effect: predicted_load stamped
    assert large_b.predicted_load == "large"
    assert large_d.predicted_load == "large"
    assert small_a.predicted_load == "small"
    assert small_c.predicted_load == "small"


# ---------------------------------------------------------------------------
# Test 2 — small pairs run in parallel even when a large pair is present
# ---------------------------------------------------------------------------


def test_small_pairs_run_parallel_when_large_pair_present(monkeypatch) -> None:
    """The HIGH-2 fix:  1 large + 4 small must NOT collapse to workers=1.

    We monkey-patch ``compare_candidate`` to record entry timestamps for
    each pair and add a deliberate sleep on the large pair.  The small
    pairs' entry timestamps must overlap (i.e. multiple threads running
    concurrently), and the small pairs must finish before the large pair
    finishes (large lane runs serially in its own pool).
    """

    enter_times: dict[str, float] = {}
    exit_times: dict[str, float] = {}
    lock = threading.Lock()

    def fake_compare(candidate, options, is_cancelled=None, progress_callback=None):
        name = candidate.source_a.name
        with lock:
            enter_times[name] = time.monotonic()
        # Large pair takes longer; small pairs only briefly sleep so
        # parallel scheduling is observable on slow CI runners.
        if "L-" in name:
            time.sleep(0.30)
        else:
            time.sleep(0.10)
        with lock:
            exit_times[name] = time.monotonic()
        return ComparisonResult(
            source_a=candidate.source_a.path,
            source_b=candidate.source_b.path,
        )

    monkeypatch.setattr(
        "src.services.comparison.drawing_batch.compare_candidate",
        fake_compare,
    )

    large = _large_candidate("L-001")
    smalls = [_small_candidate(f"S-{200 + i}") for i in range(4)]
    candidates = [large, *smalls]

    # max_workers=4 → normal lane gets 4 workers, large lane=1 (separate pool).
    summary = BatchCompareJob(
        candidates, options=BatchCompareOptions(max_workers=4)
    ).run()

    assert summary.completed_pairs == 5
    assert summary.cancelled is False
    # All names entered
    assert set(enter_times.keys()) == {
        "L-001.dwg",
        "S-200.dwg",
        "S-201.dwg",
        "S-202.dwg",
        "S-203.dwg",
    }

    # Key assertion: at least two small pairs entered while at least
    # one other small pair was still running → parallelism observed.
    small_names = [f"S-{200 + i}.dwg" for i in range(4)]
    small_enter = sorted(enter_times[n] for n in small_names)
    small_exit = sorted(exit_times[n] for n in small_names)
    # If workers=1 had been forced, all entries would be strictly serial
    # (each enter >= previous exit).  Parallel scheduling means at least
    # one later entry happens BEFORE an earlier exit.
    overlaps = sum(
        1
        for i in range(1, len(small_enter))
        if small_enter[i] < small_exit[i - 1]
    )
    assert overlaps >= 1, (
        f"Expected small pairs to overlap (parallel), but timeline was serial. "
        f"enter={small_enter}, exit={small_exit}"
    )


# ---------------------------------------------------------------------------
# Test 3 — cancel callback shuts down BOTH pools
# ---------------------------------------------------------------------------


def test_cancel_callback_stops_both_lanes(monkeypatch) -> None:
    """When ``is_cancelled`` flips mid-run, both the large and normal
    pools must shut down promptly without leaking worker threads."""

    cancel_flag = {"value": False}
    submitted: List[str] = []
    submitted_lock = threading.Lock()

    def fake_compare(candidate, options, is_cancelled=None, progress_callback=None):
        name = candidate.source_a.name
        with submitted_lock:
            submitted.append(name)
        # First small pair flips cancel after entering
        if name == "S-200.dwg":
            cancel_flag["value"] = True
        # Honour cooperative cancel like the real compare_candidate
        if is_cancelled and is_cancelled():
            from src.services.comparison.drawing_batch import (
                BatchCompareItemResult,
            )

            # Mimic returning a cancelled result through the worker
            # wrapper — the scheduler will surface it as a cancelled
            # item via _run_candidate_item.
            raise RuntimeError("cancelled by test")
        time.sleep(0.05)
        return ComparisonResult(
            source_a=candidate.source_a.path,
            source_b=candidate.source_b.path,
        )

    monkeypatch.setattr(
        "src.services.comparison.drawing_batch.compare_candidate",
        fake_compare,
    )

    # Track shutdown calls on both pools by patching the executor class.
    original_executor_cls = (
        __import__(
            "src.services.comparison.drawing_batch", fromlist=["ThreadPoolExecutor"]
        ).ThreadPoolExecutor
    )
    shutdown_calls: list[dict] = []

    class TrackingExecutor(original_executor_cls):
        def shutdown(self, *args, **kwargs):
            shutdown_calls.append(
                {
                    "prefix": getattr(self, "_thread_name_prefix", ""),
                    "kwargs": dict(kwargs),
                }
            )
            return super().shutdown(*args, **kwargs)

    monkeypatch.setattr(
        "src.services.comparison.drawing_batch.ThreadPoolExecutor",
        TrackingExecutor,
    )

    candidates = [
        _large_candidate("L-001"),
        _small_candidate("S-200"),
        _small_candidate("S-201"),
        _small_candidate("S-202"),
    ]

    summary = BatchCompareJob(
        candidates, options=BatchCompareOptions(max_workers=4)
    ).run(is_cancelled=lambda: cancel_flag["value"])

    # Cancel was triggered → summary should reflect it
    assert summary.cancelled is True
    # Both pools (large + normal) must have had shutdown(cancel_futures=True)
    # invoked.  We can't predict which prefixes survived to shutdown if a
    # __init__ failure happened, but in the happy path both fire.
    prefixes = [call["prefix"] for call in shutdown_calls]
    assert any("dwgcmp-large" in p for p in prefixes), (
        f"large-lane pool was never shut down; prefixes={prefixes}"
    )
    assert any("dwgcmp-normal" in p for p in prefixes), (
        f"normal-lane pool was never shut down; prefixes={prefixes}"
    )
    # And the cancel_futures flag must have been forwarded for both.
    cancel_kwargs = [
        call
        for call in shutdown_calls
        if call["prefix"].startswith("dwgcmp-")
    ]
    for call in cancel_kwargs:
        assert call["kwargs"].get("cancel_futures") is True, (
            f"Expected cancel_futures=True for {call['prefix']}, got {call['kwargs']}"
        )


# ---------------------------------------------------------------------------
# Test 4 — all-large input falls back to the legacy single-pool path
# ---------------------------------------------------------------------------


def test_all_large_falls_back_to_serial_with_no_normal_lane(monkeypatch) -> None:
    """When every confirmed candidate is large there is no point spinning
    up two pools.  The scheduler must take the legacy parallel-or-serial
    path with the resolved worker count, NOT the dual-lane path.

    Behaviourally we can verify this by asserting that the
    ``dwgcmp-large`` / ``dwgcmp-normal`` named pools are NOT created —
    instead a single anonymous pool is used."""

    pool_prefixes: list[str] = []

    original_executor_cls = (
        __import__(
            "src.services.comparison.drawing_batch", fromlist=["ThreadPoolExecutor"]
        ).ThreadPoolExecutor
    )

    class TrackingExecutor(original_executor_cls):
        def __init__(self, *args, **kwargs):
            pool_prefixes.append(kwargs.get("thread_name_prefix", ""))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        "src.services.comparison.drawing_batch.ThreadPoolExecutor",
        TrackingExecutor,
    )

    def fake_compare(candidate, options, is_cancelled=None, progress_callback=None):
        return ComparisonResult(
            source_a=candidate.source_a.path,
            source_b=candidate.source_b.path,
        )

    monkeypatch.setattr(
        "src.services.comparison.drawing_batch.compare_candidate",
        fake_compare,
    )

    candidates = [
        _large_candidate("L-001"),
        _large_candidate("L-002"),
        _large_candidate("L-003"),
    ]

    summary = BatchCompareJob(
        candidates, options=BatchCompareOptions(max_workers=4)
    ).run()

    assert summary.completed_pairs == 3
    # No dual-lane pools should have been created.
    assert not any(p.startswith("dwgcmp-large") for p in pool_prefixes), (
        f"Unexpected large-lane pool spun up for all-large input: {pool_prefixes}"
    )
    assert not any(p.startswith("dwgcmp-normal") for p in pool_prefixes), (
        f"Unexpected normal-lane pool spun up for all-large input: {pool_prefixes}"
    )


# ---------------------------------------------------------------------------
# Test 5 (bonus) — summary.items ordering matches input order across lanes
# ---------------------------------------------------------------------------


def test_summary_items_ordering_preserved_across_lanes(monkeypatch) -> None:
    """R1 mitigation — even when the large lane finishes after the small
    lane (or vice versa), ``summary.items`` must reflect input candidate
    order, not completion order."""

    def fake_compare(candidate, options, is_cancelled=None, progress_callback=None):
        name = candidate.source_a.name
        # Reverse the natural completion order: large is fast, small is slow.
        # This is the opposite of what one would expect, so if items end up
        # in completion order the test will fail.
        if "L-" in name:
            time.sleep(0.01)
        else:
            time.sleep(0.10)
        return ComparisonResult(
            source_a=candidate.source_a.path,
            source_b=candidate.source_b.path,
        )

    monkeypatch.setattr(
        "src.services.comparison.drawing_batch.compare_candidate",
        fake_compare,
    )

    # Interleave large/small to make ordering non-trivial.
    candidates = [
        _small_candidate("S-200"),
        _large_candidate("L-001"),
        _small_candidate("S-201"),
        _large_candidate("L-002"),
        _small_candidate("S-202"),
    ]
    expected_order = [
        "S-200.dwg",
        "L-001.dwg",
        "S-201.dwg",
        "L-002.dwg",
        "S-202.dwg",
    ]

    summary = BatchCompareJob(
        candidates, options=BatchCompareOptions(max_workers=4)
    ).run()

    actual_order = [item.candidate.source_a.name for item in summary.items]
    assert actual_order == expected_order, (
        f"summary.items ordering broken: expected input order {expected_order}, "
        f"got completion order {actual_order}"
    )


# ---------------------------------------------------------------------------
# Test 6 — _resolve_batch_workers no longer collapses on large CAD
# ---------------------------------------------------------------------------


def test_resolve_batch_workers_no_longer_collapses_on_large_cad() -> None:
    """Behaviour change pinning — the legacy ``any(large_cad) → 1``
    collapse must be GONE.  ``_resolve_batch_workers`` now returns the
    normal-lane worker count regardless of large pairs in the input
    (callers are expected to partition first)."""

    options = BatchCompareOptions()  # no max_workers override
    mixed = [
        _small_candidate("S-200"),
        _large_candidate("L-001"),
        _small_candidate("S-201"),
    ]

    workers = _resolve_batch_workers(mixed, options)
    # Pre-§15.5 this would have returned 1.  Post-fix it returns
    # min(4, len(candidates), cpu_count) >= 2 on any modern dev box.
    assert workers >= 2, (
        f"_resolve_batch_workers still collapses on large CAD presence "
        f"(returned {workers}); §15.5 HIGH-2 fix regressed."
    )
    # max_workers override still wins
    assert (
        _resolve_batch_workers(mixed, BatchCompareOptions(max_workers=1)) == 1
    )
    assert (
        _resolve_batch_workers(mixed, BatchCompareOptions(max_workers=7)) == 7
    )
