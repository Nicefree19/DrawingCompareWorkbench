# -*- coding: utf-8 -*-
"""Unit tests for RuntimeBudget + RuntimeBudgetSampler.

권고 ① (외부 감사 리뷰) 검증:
- ``peak_working_set_mb`` / ``peak_rss_mb`` 측정
- ``first_review_ready_s`` 타이밍
- ``peak_disk_spool_mb`` tempdir 모니터
- ``runtime_budget_from_dict`` round-trip
- sampler 미사용 시 회귀 영향 0 (모든 필드 None)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.services.comparison.runtime_budget as runtime_budget_module
from src.services.comparison.runtime_budget import (
    DEFAULT_VIEWER_MEMORY_BUDGET_MB,
    MemoryBudgetExceeded,
    RuntimeBudget,
    RuntimeBudgetSampler,
    SPOOL_SCAN_INTERVAL_S,
    runtime_budget_from_dict,
    SCHEMA_VERSION,
)


class TestRuntimeBudgetDataclass:
    """RuntimeBudget 데이터클래스 기본 동작."""

    def test_default_budget_has_all_none(self):
        budget = RuntimeBudget()
        assert budget.peak_working_set_mb is None
        assert budget.peak_rss_mb is None
        assert budget.peak_disk_spool_mb is None
        assert budget.first_review_ready_s is None
        assert budget.peak_compare_state_bytes is None
        assert budget.total_s is None
        assert budget.sample_count == 0
        assert budget.sampler_active is False
        assert budget.notes == []
        assert budget.schema_version == SCHEMA_VERSION
        assert budget.sampler_tick_ms is None
        assert budget.spool_scan_ms is None
        assert budget.sampler_overhead_ms is None
        assert budget.telemetry_overhead_ratio is None
        assert budget.spool_scan_count == 0
        assert budget.spool_scan_file_count == 0
        assert budget.spool_scan_bytes == 0
        assert budget.native_resource_available is False
        assert budget.native_resource_sample_count == 0
        assert budget.peak_process_handle_count is None
        assert budget.peak_open_file_descriptor_count is None
        assert budget.peak_gdi_handle_count is None
        assert budget.peak_user_handle_count is None
        assert budget.peak_worker_process_count is None
        assert budget.native_resource_notes == []

    def test_to_dict_rounds_floats(self):
        budget = RuntimeBudget(
            peak_working_set_mb=1234.56789,
            peak_rss_mb=999.999999,
            peak_disk_spool_mb=10.0,
            first_review_ready_s=5.6789,
            total_s=120.123456,
            sampler_tick_ms={"p95": 1.23456, "max": 2.34567},
            sampler_overhead_ms=3.45678,
            telemetry_overhead_ratio=0.0123456,
        )
        payload = budget.to_dict()
        assert payload["peak_working_set_mb"] == 1234.568
        assert payload["peak_rss_mb"] == 1000.0
        assert payload["peak_disk_spool_mb"] == 10.0
        assert payload["first_review_ready_s"] == 5.679
        assert payload["total_s"] == 120.123
        assert payload["sampler_tick_ms"]["p95"] == 1.235
        assert payload["sampler_tick_ms"]["max"] == 2.346
        assert payload["sampler_overhead_ms"] == 3.457
        assert payload["telemetry_overhead_ratio"] == 0.012

    def test_to_dict_preserves_none(self):
        budget = RuntimeBudget(peak_working_set_mb=None)
        payload = budget.to_dict()
        # JSON-roundtrippable
        assert json.loads(json.dumps(payload))["peak_working_set_mb"] is None

    def test_notes_field_serialises_as_list(self):
        budget = RuntimeBudget(notes=["psutil_unavailable:RuntimeError"])
        payload = budget.to_dict()
        assert payload["notes"] == ["psutil_unavailable:RuntimeError"]


class TestRuntimeBudgetFromDict:
    """``runtime_budget_from_dict`` round-trip 보장."""

    def test_round_trip_preserves_metrics(self):
        original = RuntimeBudget(
            peak_working_set_mb=2048.0,
            peak_rss_mb=1900.0,
            peak_disk_spool_mb=512.0,
            first_review_ready_s=600.0,
            peak_compare_state_bytes=10_000_000,
            total_s=900.0,
            sample_count=9000,
            sampler_active=True,
            notes=["info"],
            native_resource_platform="windows",
            native_resource_available=True,
            native_resource_sample_count=3,
            peak_process_handle_count=12,
            process_handle_positive_delta=2,
            peak_gdi_handle_count=7,
            peak_user_handle_count=8,
            final_worker_process_count=0,
        )
        roundtripped = runtime_budget_from_dict(original.to_dict())
        assert roundtripped.peak_working_set_mb == 2048.0
        assert roundtripped.peak_rss_mb == 1900.0
        assert roundtripped.peak_disk_spool_mb == 512.0
        assert roundtripped.first_review_ready_s == 600.0
        assert roundtripped.peak_compare_state_bytes == 10_000_000
        assert roundtripped.total_s == 900.0
        assert roundtripped.sample_count == 9000
        assert roundtripped.sampler_active is True
        assert roundtripped.notes == ["info"]
        assert roundtripped.native_resource_platform == "windows"
        assert roundtripped.native_resource_available is True
        assert roundtripped.native_resource_sample_count == 3
        assert roundtripped.peak_process_handle_count == 12
        assert roundtripped.process_handle_positive_delta == 2
        assert roundtripped.peak_gdi_handle_count == 7
        assert roundtripped.peak_user_handle_count == 8
        assert roundtripped.final_worker_process_count == 0

    def test_missing_keys_become_none(self):
        budget = runtime_budget_from_dict({"schema_version": 1})
        assert budget.peak_working_set_mb is None
        assert budget.first_review_ready_s is None
        assert budget.sample_count == 0
        assert budget.sampler_tick_ms is None
        assert budget.spool_scan_count == 0

    def test_invalid_input_returns_default_budget(self):
        assert runtime_budget_from_dict(None).peak_working_set_mb is None
        assert runtime_budget_from_dict("not-a-dict").peak_working_set_mb is None
        assert runtime_budget_from_dict([]).peak_working_set_mb is None

    def test_invalid_numeric_values_become_none(self):
        budget = runtime_budget_from_dict(
            {
                "peak_working_set_mb": "garbage",
                "peak_compare_state_bytes": [1, 2, 3],
                "total_s": None,
                "sampler_tick_ms": "bad",
                "spool_scan_count": "bad",
                "peak_process_handle_count": "bad",
                "process_handle_positive_delta": [1],
            }
        )
        assert budget.peak_working_set_mb is None
        assert budget.peak_compare_state_bytes is None
        assert budget.total_s is None
        assert budget.sampler_tick_ms is None
        assert budget.spool_scan_count == 0
        assert budget.peak_process_handle_count is None
        assert budget.process_handle_positive_delta is None


class TestRuntimeBudgetSampler:
    """Sampler 동작 — psutil 의존이지만 requirements.txt 에 이미 있음."""

    def test_stop_without_start_yields_default_budget(self):
        sampler = RuntimeBudgetSampler()
        budget = sampler.stop()
        assert budget.total_s is None
        assert budget.first_review_ready_s is None
        # peak_working_set may stay None when start_sampling never called.
        assert budget.peak_working_set_mb is None

    def test_basic_sampling_records_memory(self):
        sampler = RuntimeBudgetSampler(sample_interval_s=0.05)
        sampler.start_sampling()
        # 활성 메모리 사용으로 peak 갱신 유도
        _ballast = [bytearray(256 * 1024) for _ in range(64)]  # ~16MB
        time.sleep(0.15)
        budget = sampler.stop()
        del _ballast

        assert budget.sampler_active is True
        assert budget.sample_count >= 1
        assert budget.total_s is not None and budget.total_s >= 0.0
        assert budget.sampler_tick_ms is not None
        assert budget.sampler_tick_ms["p95"] >= 0.0
        assert budget.sampler_tick_ms["max"] >= budget.sampler_tick_ms["p95"]
        assert budget.sampler_overhead_ms is not None
        assert budget.sampler_overhead_ms >= 0.0
        assert budget.telemetry_overhead_ratio is not None
        assert budget.telemetry_overhead_ratio >= 0.0
        # 적어도 peak_rss_mb 또는 peak_working_set_mb 중 하나는 측정되어야 함
        assert (
            budget.peak_rss_mb is not None or budget.peak_working_set_mb is not None
        )

    def test_first_review_ready_records_timing(self):
        sampler = RuntimeBudgetSampler(sample_interval_s=0.05)
        sampler.start_sampling()
        time.sleep(0.05)
        sampler.mark_first_review_ready()
        time.sleep(0.05)
        budget = sampler.stop()

        assert budget.first_review_ready_s is not None
        assert budget.first_review_ready_s > 0.0
        assert budget.total_s is not None
        assert budget.total_s >= budget.first_review_ready_s

    def test_first_review_ready_only_records_first_call(self):
        sampler = RuntimeBudgetSampler(sample_interval_s=0.05)
        sampler.start_sampling()
        time.sleep(0.05)
        sampler.mark_first_review_ready()
        first = sampler._first_review_ready_perf
        time.sleep(0.05)
        sampler.mark_first_review_ready()
        second = sampler._first_review_ready_perf
        assert first == second

    def test_first_review_ready_before_start_logs_note(self):
        sampler = RuntimeBudgetSampler()
        sampler.mark_first_review_ready()
        budget = sampler.stop()
        assert budget.first_review_ready_s is None
        assert any(
            "first_review_ready_called_before_start" in note for note in budget.notes
        )

    def test_record_compare_state_bytes_tracks_peak(self):
        sampler = RuntimeBudgetSampler()
        sampler.start_sampling()
        sampler.record_compare_state_bytes(1_000_000)
        sampler.record_compare_state_bytes(500_000)  # 더 작음 — 무시
        sampler.record_compare_state_bytes(2_500_000)  # 새 peak
        budget = sampler.stop()
        assert budget.peak_compare_state_bytes == 2_500_000

    def test_record_compare_state_bytes_handles_invalid(self):
        sampler = RuntimeBudgetSampler()
        sampler.start_sampling()
        sampler.record_compare_state_bytes(1000)
        sampler.record_compare_state_bytes("garbage")  # type: ignore[arg-type]
        sampler.record_compare_state_bytes(None)  # type: ignore[arg-type]
        budget = sampler.stop()
        assert budget.peak_compare_state_bytes == 1000

    def test_disk_spool_monitoring(self, tmp_path: Path):
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        sampler = RuntimeBudgetSampler(
            spool_dirs=[spool_dir], sample_interval_s=0.05
        )
        sampler.start_sampling()
        time.sleep(0.05)
        # 1MB 파일 생성으로 peak 유도
        (spool_dir / "data.bin").write_bytes(b"\0" * (1024 * 1024))
        time.sleep(0.15)
        budget = sampler.stop()
        # 1MB 이상 측정되어야 함 (실제 파일 시스템 + sampling 인터벌 영향)
        assert budget.peak_disk_spool_mb is not None
        assert budget.peak_disk_spool_mb >= 0.5  # 보수적 검증
        assert budget.spool_scan_ms is not None
        assert budget.spool_scan_ms["p95"] >= 0.0
        assert budget.spool_scan_count >= 1
        assert budget.spool_scan_file_count >= 1
        assert budget.spool_scan_bytes >= 1024 * 1024

    def test_spool_scan_is_throttled_between_memory_samples(self, tmp_path: Path, monkeypatch):
        calls = {"count": 0}

        def fake_directory_scan(path: Path) -> tuple[int, int]:
            calls["count"] += 1
            return 1024, 1

        monkeypatch.setattr(runtime_budget_module, "_directory_scan", fake_directory_scan)
        sampler = RuntimeBudgetSampler(
            spool_dirs=[tmp_path],
            sample_interval_s=0.01,
            spool_scan_interval_s=60.0,
        )

        sampler._sample_once()
        sampler._sample_once()
        sampler._sample_once()

        assert calls["count"] == 1
        assert sampler.snapshot()["spool_mb"] == pytest.approx(1024 / (1024 * 1024))
        snapshot = sampler.snapshot()
        assert snapshot["spool_scan_count"] == 1
        assert snapshot["spool_scan_file_count"] == 1
        assert snapshot["spool_scan_bytes"] == 1024

    def test_stop_forces_final_spool_scan_despite_throttle(self, tmp_path: Path, monkeypatch):
        calls = {"count": 0}

        def fake_directory_scan(path: Path) -> tuple[int, int]:
            calls["count"] += 1
            return calls["count"] * 1024, calls["count"]

        monkeypatch.setattr(runtime_budget_module, "_directory_scan", fake_directory_scan)
        sampler = RuntimeBudgetSampler(
            spool_dirs=[tmp_path],
            sample_interval_s=0.01,
            spool_scan_interval_s=60.0,
        )

        sampler._sample_once()
        budget = sampler.stop()

        assert calls["count"] == 2
        assert budget.peak_disk_spool_mb == pytest.approx((2 * 1024) / (1024 * 1024))
        assert budget.spool_scan_count == 2
        assert budget.spool_scan_file_count == 3
        assert budget.spool_scan_bytes == 3 * 1024
        assert budget.spool_scan_ms is not None

    def test_native_resource_sampling_is_throttled_between_memory_samples(self, monkeypatch):
        calls: list[dict[str, Any]] = []

        def fake_native_resource_snapshot(**kwargs):
            calls.append(dict(kwargs))
            return {
                "native_resource_platform": "windows",
                "native_resource_available": True,
                "process_handle_count": 10 + len(calls),
                "open_file_descriptor_count": None,
                "gdi_handle_count": 5,
                "user_handle_count": 6,
                "worker_process_count": 0,
                "native_resource_notes": [],
            }

        class FakeProc:
            def memory_info(self):
                return SimpleNamespace(rss=1024 * 1024, peak_wset=2 * 1024 * 1024)

        monkeypatch.setattr(
            runtime_budget_module,
            "native_resource_snapshot",
            fake_native_resource_snapshot,
        )
        sampler = RuntimeBudgetSampler(
            sample_interval_s=0.01,
            spool_scan_interval_s=60.0,
        )
        sampler._psutil_proc = FakeProc()
        sampler._start_perf = time.perf_counter()

        sampler._sample_once()
        sampler._sample_once()
        sampler._sample_once(force_spool_scan=True)

        assert len(calls) == 2
        assert calls[0]["include_process_memory"] is False
        assert calls[0]["include_process_resources"] is True
        assert calls[0]["include_worker_processes"] is True
        assert calls[1]["include_process_resources"] is True
        budget = sampler.stop()
        assert budget.native_resource_sample_count >= 2

    def test_native_resource_sampling_tracks_start_final_peak_and_delta(self, monkeypatch):
        samples = [
            {
                "native_resource_platform": "windows",
                "native_resource_available": True,
                "process_handle_count": 10,
                "open_file_descriptor_count": None,
                "gdi_handle_count": 5,
                "user_handle_count": 6,
                "worker_process_count": 0,
                "native_resource_notes": [],
            },
            {
                "native_resource_platform": "windows",
                "native_resource_available": True,
                "process_handle_count": 13,
                "open_file_descriptor_count": None,
                "gdi_handle_count": 7,
                "user_handle_count": 6,
                "worker_process_count": 1,
                "native_resource_notes": ["sample_note"],
            },
            {
                "native_resource_platform": "windows",
                "native_resource_available": True,
                "process_handle_count": 12,
                "open_file_descriptor_count": None,
                "gdi_handle_count": 6,
                "user_handle_count": 8,
                "worker_process_count": 0,
                "native_resource_notes": [],
            },
        ]

        def fake_native_resource_snapshot(**_kwargs):
            return samples.pop(0) if samples else {
                "native_resource_platform": "windows",
                "native_resource_available": True,
                "process_handle_count": 12,
                "open_file_descriptor_count": None,
                "gdi_handle_count": 6,
                "user_handle_count": 8,
                "worker_process_count": 0,
                "native_resource_notes": [],
            }

        class FakeProc:
            def memory_info(self):
                return SimpleNamespace(rss=1024 * 1024, peak_wset=2 * 1024 * 1024)

        monkeypatch.setattr(
            runtime_budget_module,
            "native_resource_snapshot",
            fake_native_resource_snapshot,
        )
        sampler = RuntimeBudgetSampler(sample_interval_s=0.05)
        sampler._psutil_proc = FakeProc()
        sampler._start_perf = time.perf_counter()

        sampler._sample_once(force_spool_scan=True)
        sampler._sample_once(force_spool_scan=True)
        budget = sampler.stop()

        assert budget.native_resource_platform == "windows"
        assert budget.native_resource_available is True
        assert budget.native_resource_sample_count == 3
        assert budget.start_process_handle_count == 10
        assert budget.final_process_handle_count == 12
        assert budget.peak_process_handle_count == 13
        assert budget.process_handle_positive_delta == 2
        assert budget.peak_gdi_handle_count == 7
        assert budget.peak_user_handle_count == 8
        assert budget.peak_worker_process_count == 1
        assert budget.final_worker_process_count == 0
        assert budget.worker_process_positive_delta == 0
        assert budget.native_resource_notes == ["sample_note"]

    def test_directory_size_uses_nested_files(self, tmp_path: Path):
        root = tmp_path / "spool"
        nested = root / "a" / "b"
        nested.mkdir(parents=True)
        (root / "one.bin").write_bytes(b"1234")
        (nested / "two.bin").write_bytes(b"123456")

        assert runtime_budget_module._directory_size(root) == 10
        assert runtime_budget_module._directory_scan(root) == (10, 2)

    def test_idempotent_start_sampling(self):
        sampler = RuntimeBudgetSampler(sample_interval_s=0.05)
        sampler.start_sampling()
        thread_first = sampler._thread
        sampler.start_sampling()  # 두 번째 호출은 no-op
        thread_second = sampler._thread
        assert thread_first is thread_second
        sampler.stop()

    def test_stop_is_safe_to_call_twice(self):
        sampler = RuntimeBudgetSampler(sample_interval_s=0.05)
        sampler.start_sampling()
        time.sleep(0.05)
        budget1 = sampler.stop()
        budget2 = sampler.stop()
        # Second stop returns a snapshot too; total_s should be >= budget1.total_s.
        assert budget1.total_s is not None
        assert budget2.total_s is not None
        assert budget2.total_s >= budget1.total_s


class TestPeekAndAssertBudget:
    """§10.4 단기 fix — peek + assert_within_memory_budget."""

    def test_peek_returns_none_before_sampling(self):
        sampler = RuntimeBudgetSampler()
        assert sampler.peek_working_set_mb() is None

    def test_peek_returns_value_after_sampling(self):
        sampler = RuntimeBudgetSampler(sample_interval_s=0.05)
        sampler.start_sampling()
        time.sleep(0.15)
        peek = sampler.peek_working_set_mb()
        sampler.stop()
        assert peek is not None
        assert peek > 0.0

    def test_assert_within_budget_no_op_when_max_none(self):
        sampler = RuntimeBudgetSampler(sample_interval_s=0.05)
        sampler.start_sampling()
        time.sleep(0.05)
        # None / 0 should never raise even after sampling
        sampler.assert_within_memory_budget(None)
        sampler.assert_within_memory_budget(0)
        sampler.assert_within_memory_budget(-1)
        sampler.stop()

    def test_assert_within_budget_no_op_when_no_measurement(self):
        sampler = RuntimeBudgetSampler()
        # Never started → peek None → no exception even with strict cap
        sampler.assert_within_memory_budget(1.0)

    def test_assert_within_budget_passes_under_cap(self):
        sampler = RuntimeBudgetSampler(sample_interval_s=0.05)
        sampler.start_sampling()
        time.sleep(0.1)
        # Set absurdly high cap → should pass
        sampler.assert_within_memory_budget(1_000_000.0)
        sampler.stop()

    def test_assert_within_budget_raises_when_over_cap(self):
        sampler = RuntimeBudgetSampler(sample_interval_s=0.05)
        sampler.start_sampling()
        time.sleep(0.1)
        # Set absurdly low cap → should raise
        with pytest.raises(MemoryBudgetExceeded) as exc_info:
            sampler.assert_within_memory_budget(0.001, stage="test_stage")
        sampler.stop()
        assert exc_info.value.stage == "test_stage"
        assert exc_info.value.current_mb > exc_info.value.max_mb
        assert exc_info.value.max_mb == 0.001

    def test_memory_budget_exceeded_message_format(self):
        exc = MemoryBudgetExceeded(stage="viewer_package", current_mb=5278.1, max_mb=4096.0)
        msg = str(exc)
        assert "memory_budget_exceeded" in msg
        assert "viewer_package" in msg
        assert "5278" in msg
        assert "4096" in msg

    def test_default_viewer_memory_budget_constant(self):
        assert DEFAULT_VIEWER_MEMORY_BUDGET_MB == 4096.0

    def test_default_spool_scan_interval_constant(self):
        assert SPOOL_SCAN_INTERVAL_S == 1.0


class TestRegressionBackwardCompat:
    """기존 schema(fixture 11-15) 와 호환 검증."""

    def test_schema_version_is_two(self):
        # P5-G23 bumped to v4 for native resource / worker plateau evidence.
        assert SCHEMA_VERSION == 4

    def test_default_budget_serialises_with_known_keys(self):
        payload = RuntimeBudget().to_dict()
        expected_keys = {
            "schema_version",
            "peak_working_set_mb",
            "peak_rss_mb",
            "peak_disk_spool_mb",
            "first_review_ready_s",
            "peak_compare_state_bytes",
            "total_s",
            "sample_count",
            "sampler_active",
            "notes",
            # Plan §16 Phase C-2.1 — comparator-derived metrics
            "peak_comparator_changes",
            "time_to_first_stream_record_ms",
            # P5-G14 — sampler/spool telemetry-overhead metrics
            "sampler_tick_ms",
            "spool_scan_ms",
            "sampler_overhead_ms",
            "telemetry_overhead_ratio",
            "spool_scan_count",
            "spool_scan_file_count",
            "spool_scan_bytes",
            # P5-G23 — native resource / worker plateau metrics
            "native_resource_schema_version",
            "native_resource_platform",
            "native_resource_available",
            "native_resource_sample_count",
            "start_process_handle_count",
            "final_process_handle_count",
            "peak_process_handle_count",
            "process_handle_positive_delta",
            "start_open_file_descriptor_count",
            "final_open_file_descriptor_count",
            "peak_open_file_descriptor_count",
            "open_file_descriptor_positive_delta",
            "start_gdi_handle_count",
            "final_gdi_handle_count",
            "peak_gdi_handle_count",
            "gdi_handle_positive_delta",
            "start_user_handle_count",
            "final_user_handle_count",
            "peak_user_handle_count",
            "user_handle_positive_delta",
            "start_worker_process_count",
            "final_worker_process_count",
            "peak_worker_process_count",
            "worker_process_positive_delta",
            "native_resource_notes",
        }
        assert set(payload.keys()) == expected_keys


class TestPlanS16ComparatorMetrics:
    """Plan §16 Phase C-2.1 — peak_comparator_changes + time_to_first_stream_record_ms."""

    def test_record_comparator_peak_changes_keeps_monotonic_max(self):
        sampler = RuntimeBudgetSampler()
        sampler.start_sampling()
        sampler.record_comparator_peak_changes(100)
        sampler.record_comparator_peak_changes(50)  # smaller — must NOT regress
        sampler.record_comparator_peak_changes(200)  # new max
        budget = sampler.stop()
        assert budget.peak_comparator_changes == 200

    def test_record_comparator_peak_changes_ignores_non_positive(self):
        sampler = RuntimeBudgetSampler()
        sampler.start_sampling()
        sampler.record_comparator_peak_changes(0)
        sampler.record_comparator_peak_changes(-5)
        sampler.record_comparator_peak_changes("garbage")  # type: ignore[arg-type]
        sampler.record_comparator_peak_changes(None)  # type: ignore[arg-type]
        budget = sampler.stop()
        # Nothing valid recorded — field stays None
        assert budget.peak_comparator_changes is None

    def test_record_time_to_first_stream_record_ms_keeps_first_value(self):
        sampler = RuntimeBudgetSampler()
        sampler.start_sampling()
        sampler.record_time_to_first_stream_record_ms(250.0)
        sampler.record_time_to_first_stream_record_ms(100.0)  # later call — must NOT overwrite
        sampler.record_time_to_first_stream_record_ms(900.0)
        budget = sampler.stop()
        assert budget.time_to_first_stream_record_ms == 250.0

    def test_record_time_to_first_stream_record_ms_ignores_non_positive(self):
        sampler = RuntimeBudgetSampler()
        sampler.start_sampling()
        sampler.record_time_to_first_stream_record_ms(0)
        sampler.record_time_to_first_stream_record_ms(-1.5)
        sampler.record_time_to_first_stream_record_ms("garbage")  # type: ignore[arg-type]
        budget = sampler.stop()
        assert budget.time_to_first_stream_record_ms is None

    def test_stop_returns_none_for_unset_new_fields(self):
        sampler = RuntimeBudgetSampler()
        sampler.start_sampling()
        # Do NOT call the new recorders
        budget = sampler.stop()
        assert budget.peak_comparator_changes is None
        assert budget.time_to_first_stream_record_ms is None

    def test_runtime_budget_from_dict_roundtrips_new_fields(self):
        original = RuntimeBudget(
            peak_comparator_changes=12_345,
            time_to_first_stream_record_ms=87.654,
            sampler_tick_ms={"p50": 1.0, "p95": 2.0, "max": 3.0},
            spool_scan_ms={"p50": 4.0, "p95": 5.0, "max": 6.0},
            sampler_overhead_ms=9.5,
            telemetry_overhead_ratio=0.01,
            spool_scan_count=2,
            spool_scan_file_count=3,
            spool_scan_bytes=4096,
        )
        roundtripped = runtime_budget_from_dict(original.to_dict())
        assert roundtripped.peak_comparator_changes == 12_345
        # Round-trip preserves rounded form (3 decimals)
        assert roundtripped.time_to_first_stream_record_ms == 87.654
        assert roundtripped.sampler_tick_ms == {"p50": 1.0, "p95": 2.0, "max": 3.0}
        assert roundtripped.spool_scan_ms == {"p50": 4.0, "p95": 5.0, "max": 6.0}
        assert roundtripped.sampler_overhead_ms == 9.5
        assert roundtripped.telemetry_overhead_ratio == 0.01
        assert roundtripped.spool_scan_count == 2
        assert roundtripped.spool_scan_file_count == 3
        assert roundtripped.spool_scan_bytes == 4096

    def test_runtime_budget_from_dict_invalid_new_fields_become_none(self):
        budget = runtime_budget_from_dict(
            {
                "peak_comparator_changes": "garbage",
                "time_to_first_stream_record_ms": [1, 2],
            }
        )
        assert budget.peak_comparator_changes is None
        assert budget.time_to_first_stream_record_ms is None
