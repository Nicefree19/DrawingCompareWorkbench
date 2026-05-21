# -*- coding: utf-8 -*-
"""Tests for the runtime_budget audit gate (recommendation #1).

외부 감사 리뷰가 지적한 proxy metric 문제 해소를 위해 추가된
``_check_runtime_budget`` 게이트가 다음을 정확히 강제하는지 검증:

- ``--require-runtime-budget`` 시 모든 completed output 에 runtime_budget 필요
- ``--max-peak-working-set-mb`` 초과 시 fail
- ``--max-runtime-first-review-ready-s`` 초과 시 fail
- ``--max-peak-disk-spool-mb`` 초과 시 fail
- 모든 인자 unset 시 게이트 비활성 (회귀 영향 0)
- ``peak_working_set_mb`` 가 None 일 때 ``peak_rss_mb`` 폴백 사용
"""

from __future__ import annotations

from typing import Any

from scripts import audit_drawing_compare_mvp_exit as audit


def _summary_with_budget(
    *,
    completed_pairs: int = 5,
    runtime_budget: dict[str, Any] | None = None,
    output_dir: str = "/tmp/test_run",
) -> dict[str, Any]:
    """Minimal validation_summary skeleton for runtime_budget gate tests."""
    summary: dict[str, Any] = {
        "output_dir": output_dir,
        "comparison": {"completed_pairs": completed_pairs, "failed_pairs": 0},
        "timings": {"total_s": 60.0},
    }
    if runtime_budget is not None:
        summary["runtime_budget"] = runtime_budget
    return summary


def _passing_budget(
    *,
    peak_working_set_mb: float | None = 2048.0,
    peak_rss_mb: float | None = 1900.0,
    peak_disk_spool_mb: float | None = 256.0,
    first_review_ready_s: float | None = 450.0,
    sampler_active: bool = True,
    sample_count: int = 9000,
    # Plan §16 Phase C-2.3 — defaults exercise the new fields in every fixture
    peak_comparator_changes: int | None = 10_000,
    time_to_first_stream_record_ms: float | None = 250.0,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "peak_working_set_mb": peak_working_set_mb,
        "peak_rss_mb": peak_rss_mb,
        "peak_disk_spool_mb": peak_disk_spool_mb,
        "first_review_ready_s": first_review_ready_s,
        "peak_compare_state_bytes": 50_000_000,
        "total_s": 600.0,
        "sample_count": sample_count,
        "sampler_active": sampler_active,
        "notes": [],
        # Plan §16 Phase C-2.3 — comparator-derived fields
        "peak_comparator_changes": peak_comparator_changes,
        "time_to_first_stream_record_ms": time_to_first_stream_record_ms,
    }


class TestRuntimeBudgetCheckDisabledByDefault:
    """모든 인자 None + require=False → 게이트 비활성 → pass."""

    def test_no_args_passes_even_without_runtime_budget(self):
        summary = _summary_with_budget(runtime_budget=None)
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is True
        assert result.name == "runtime_budget_measurement"


class TestRuntimeBudgetRequireFlag:
    """--require-runtime-budget 시 missing block 거부."""

    def test_require_passes_when_budget_present(self):
        summary = _summary_with_budget(runtime_budget=_passing_budget())
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=True,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is True

    def test_require_fails_when_budget_missing(self):
        summary = _summary_with_budget(runtime_budget=None)
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=True,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is False
        assert "missing" in result.detail.lower()

    def test_require_fails_when_sampler_inactive(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(sampler_active=False)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=True,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is False
        assert "sampler_active=false" in result.detail

    def test_require_fails_when_no_completed_outputs(self):
        result = audit._check_runtime_budget(
            [],
            require_runtime_budget=True,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is False
        assert "no completed outputs" in result.detail

    def test_require_fails_when_zero_samples(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(sample_count=0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=True,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is False
        assert "sample_count=0" in result.detail


class TestPeakWorkingSetThreshold:
    """--max-peak-working-set-mb 강제."""

    def test_passes_under_threshold(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_working_set_mb=2000.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=4096.0,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is True

    def test_fails_over_threshold(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_working_set_mb=5000.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=4096.0,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is False
        assert "peak_working_set_mb=5000" in result.detail
        assert "> 4096" in result.detail

    def test_falls_back_to_peak_rss_when_working_set_missing(self):
        """non-Windows 에서 peak_working_set_mb 없을 때 peak_rss_mb 사용."""
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(
                peak_working_set_mb=None,
                peak_rss_mb=3500.0,
            )
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=4096.0,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is True

    def test_fails_when_both_memory_metrics_missing(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(
                peak_working_set_mb=None, peak_rss_mb=None
            )
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=4096.0,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is False
        assert "peak_working_set_mb missing" in result.detail


class TestMaxPeakRssMbThreshold:
    """Plan §17 A-3 (GPT Pro F5) — cross-platform RSS gate independent of
    the Windows-specific working-set gate. The two gates can coexist:
    - max_peak_working_set_mb: Windows wset (preferred on Windows)
    - max_peak_rss_mb: cross-platform RSS (always available via psutil)
    """

    def test_passes_under_threshold(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_rss_mb=1500.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_peak_rss_mb=2048.0,
        )
        assert result.passed is True

    def test_fails_over_threshold(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_rss_mb=3000.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_peak_rss_mb=2048.0,
        )
        assert result.passed is False
        assert "peak_rss_mb=3000" in result.detail
        assert "> 2048" in result.detail

    def test_fails_when_metric_missing(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_rss_mb=None)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_peak_rss_mb=2048.0,
        )
        assert result.passed is False
        assert "peak_rss_mb missing" in result.detail

    def test_rss_and_wset_gates_evaluated_independently(self):
        """Both gates active; an over-budget RSS fails even when wset OK."""
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(
                peak_working_set_mb=1000.0,  # well under wset gate
                peak_rss_mb=3000.0,           # over rss gate
            )
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=4096.0,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_peak_rss_mb=2048.0,
        )
        assert result.passed is False
        assert "peak_rss_mb=3000" in result.detail
        # wset stayed silent — the failure is RSS-only
        assert "peak_working_set_mb=1000" not in result.detail


class TestCustomerGradeAutoActivatedRssGate:
    """Plan §18 A-1 (GPT Pro F5 closure correction) — when
    evidence_level=customer_grade, the max_peak_rss_mb gate auto-
    activates with STRICT_MAX_PEAK_RSS_MB even if the operator did not
    pass --max-peak-rss-mb on the CLI. Verifies the cascade promoted
    the help-text recommendation to enforced behaviour.
    """

    def test_strict_constants_match_reviewer_recommendation(self) -> None:
        """The cascade constants must match the GPT Pro reviewer's
        recommended thresholds (0.85 / 2.0 / 2048). If anyone tweaks
        them, this test pins the contract explicitly."""
        assert audit.STRICT_REQUIRE_PRECISION_THRESHOLD == 0.85
        assert audit.STRICT_REQUIRE_BURDEN_THRESHOLD == 2.0
        assert audit.STRICT_MAX_PEAK_RSS_MB == 2048.0

    def test_customer_grade_auto_activates_rss_when_metric_present(self) -> None:
        """When the summary carries peak_rss_mb AND evidence_level is
        customer_grade, the cascade promotes max_peak_rss_mb to the
        strict constant. The simulation feeds a summary with RSS over
        the cap directly into _check_runtime_budget with the constant
        the cascade would inject."""
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_rss_mb=3000.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_peak_rss_mb=audit.STRICT_MAX_PEAK_RSS_MB,
        )
        assert result.passed is False
        assert "peak_rss_mb=3000" in result.detail
        assert "> 2048" in result.detail


class TestFirstReviewReadyThreshold:
    """--max-runtime-first-review-ready-s 강제 (별도 측정값)."""

    def test_passes_under_threshold(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(first_review_ready_s=500.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=1200.0,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is True

    def test_fails_over_threshold(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(first_review_ready_s=1500.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=1200.0,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is False
        assert "first_review_ready_s=1500" in result.detail

    def test_fails_when_first_review_ready_missing(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(first_review_ready_s=None)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=1200.0,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is False
        assert "first_review_ready_s missing" in result.detail


class TestPeakDiskSpoolThreshold:
    """--max-peak-disk-spool-mb 강제."""

    def test_passes_under_threshold(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_disk_spool_mb=500.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=1024.0,
        )
        assert result.passed is True

    def test_fails_over_threshold(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_disk_spool_mb=2000.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=1024.0,
        )
        assert result.passed is False
        assert "peak_disk_spool_mb=2000" in result.detail


class TestMultipleSummaries:
    """다중 summary 처리 — 하나라도 실패하면 fail."""

    def test_one_failure_fails_overall(self):
        summary_pass = _summary_with_budget(
            runtime_budget=_passing_budget(peak_working_set_mb=1000.0),
            output_dir="/tmp/run1",
        )
        summary_fail = _summary_with_budget(
            runtime_budget=_passing_budget(peak_working_set_mb=10000.0),
            output_dir="/tmp/run2",
        )
        result = audit._check_runtime_budget(
            [summary_pass, summary_fail],
            require_runtime_budget=False,
            max_peak_working_set_mb=4096.0,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        assert result.passed is False
        assert "/tmp/run2" in " ".join(result.evidence)

    def test_skips_outputs_with_zero_completed_pairs(self):
        summary_skipped = _summary_with_budget(
            completed_pairs=0,
            runtime_budget=None,
            output_dir="/tmp/empty",
        )
        summary_real = _summary_with_budget(
            runtime_budget=_passing_budget(),
            output_dir="/tmp/real",
        )
        result = audit._check_runtime_budget(
            [summary_skipped, summary_real],
            require_runtime_budget=True,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
        )
        # Real summary has runtime_budget — skipped one is excluded from gate.
        assert result.passed is True


class TestRunAuditIntegration:
    """run_audit 의 시그니처에 신규 파라미터가 잘 전달되는지 확인."""

    def test_run_audit_accepts_new_kwargs(self):
        # Empty result_dirs path triggers many failures, but the call should
        # at least not raise on the new keyword arguments themselves.
        report = audit.run_audit(
            result_dirs=[],
            require_runtime_budget=True,
            max_peak_working_set_mb=4096.0,
            max_runtime_first_review_ready_s=1200.0,
            max_peak_disk_spool_mb=1024.0,
        )
        assert isinstance(report, dict)
        # The runtime_budget check should appear in the checks list when any
        # gate-activating kwarg is supplied.
        check_names = {check["name"] for check in report["checks"]}
        assert "runtime_budget_measurement" in check_names

    def test_run_audit_accepts_plan_s16_kwargs(self):
        # Plan §16 Phase C-2.3 — run_audit must forward the new comparator
        # thresholds without raising on the keyword arguments.
        report = audit.run_audit(
            result_dirs=[],
            max_peak_comparator_changes=500_000,
            max_time_to_first_stream_record_ms=5000,
        )
        assert isinstance(report, dict)
        check_names = {check["name"] for check in report["checks"]}
        # The comparator threshold alone should activate the runtime_budget gate.
        assert "runtime_budget_measurement" in check_names


# ----------------------------------------------------------------------
# Plan §16 Phase C-2.3 — new comparator-derived audit gates
# ----------------------------------------------------------------------


class TestPeakComparatorChangesGate:
    """--max-peak-comparator-changes 강제 (Plan §16 Phase C-2.3)."""

    def test_disabled_when_none(self):
        # When max_peak_comparator_changes is None the gate is inert and a
        # passing budget should pass regardless of the field value.
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_comparator_changes=99_999_999)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_peak_comparator_changes=None,
        )
        assert result.passed is True

    def test_passes_under_limit(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_comparator_changes=5_000)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_peak_comparator_changes=10_000,
        )
        assert result.passed is True

    def test_fails_over_limit(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_comparator_changes=25_000)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_peak_comparator_changes=10_000,
        )
        assert result.passed is False
        # Detail must surface the offending value so operators can triage.
        assert "peak_comparator_changes=25000" in result.detail
        assert "10000" in result.detail

    def test_missing_when_required_reports_failure(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(peak_comparator_changes=None)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_peak_comparator_changes=10_000,
        )
        assert result.passed is False
        assert "peak_comparator_changes missing" in result.detail


class TestTimeToFirstStreamRecordGate:
    """--max-time-to-first-stream-record-ms 강제 (Plan §16 Phase C-2.3)."""

    def test_disabled_when_none(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(time_to_first_stream_record_ms=99_999.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_time_to_first_stream_record_ms=None,
        )
        assert result.passed is True

    def test_passes_under_limit(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(time_to_first_stream_record_ms=100.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_time_to_first_stream_record_ms=1000.0,
        )
        assert result.passed is True

    def test_fails_over_limit(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(time_to_first_stream_record_ms=5_000.0)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_time_to_first_stream_record_ms=1_000.0,
        )
        assert result.passed is False
        assert "time_to_first_stream_record_ms=5000" in result.detail
        assert "1000" in result.detail

    def test_missing_when_required_reports_failure(self):
        summary = _summary_with_budget(
            runtime_budget=_passing_budget(time_to_first_stream_record_ms=None)
        )
        result = audit._check_runtime_budget(
            [summary],
            require_runtime_budget=False,
            max_peak_working_set_mb=None,
            max_runtime_first_review_ready_s=None,
            max_peak_disk_spool_mb=None,
            max_time_to_first_stream_record_ms=1_000.0,
        )
        assert result.passed is False
        assert "time_to_first_stream_record_ms missing" in result.detail
