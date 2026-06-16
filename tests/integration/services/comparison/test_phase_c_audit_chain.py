# -*- coding: utf-8 -*-
"""End-to-end integration tests for Plan §16 Phase C audit chain.

Proves the full pipeline → sampler → audit gate chain works for the
two CRITICAL-closure metrics: ``peak_comparator_changes`` and
``time_to_first_stream_record_ms``.

These tests sit one layer above the unit-test fixtures in
``test_audit_runtime_budget.py`` (which stub the validation_summary as
a literal dict). Here we actually drive ``run_validation`` over a real
golden DXF pair (``02_single_modification``), then feed the produced
``validation_summary.json`` into ``run_audit`` and assert the gate
fails when a deliberately-tight threshold is configured.

Marker
======
``@pytest.mark.audit_chain_integration`` — run via
``pytest -m audit_chain_integration`` to invoke these explicitly.
They are NOT opt-out from the default suite because they use small
golden fixtures and complete in seconds.

Skip-conditions
===============
Test 4 (``time_to_first_stream_record_ms`` breach) is conditionally
skipped because ``run_validation`` does not expose
``change_zone_stream_path`` configuration via any Namespace attribute
or CLI flag — only the compare_state path drives streaming
(``drawing_batch.py:1797-1803``) and that's a derived/implicit option.
A unit-level breach assertion for the streaming gate already exists at
``tests/unit/services/comparison/test_audit_runtime_budget.py:471-482``.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from scripts import audit_drawing_compare_mvp_exit as audit_module
from scripts import validate_drawing_compare_realset as validator_module


GOLDEN_FIXTURE_DIR = Path(
    "tests/data/comparison/golden/dxf/02_single_modification"
).resolve()


def _build_validation_namespace(
    *,
    a_dir: Path,
    b_dir: Path,
    out_dir: Path,
    skip_compare: bool = False,
    measure_runtime_budget: bool = True,
) -> argparse.Namespace:
    """Build the minimum-viable Namespace for ``run_validation``.

    Mirrors ``tests/unit/services/comparison/test_validate_drawing_compare_realset.py::_args``
    field set (verified against Phase 1 spot-check) and toggles
    ``measure_runtime_budget=True`` so the ``RuntimeBudgetSampler`` is
    instantiated and the runtime_budget block surfaces in the summary.
    """
    return argparse.Namespace(
        a=a_dir,
        b=b_dir,
        recursive=False,
        out=out_dir,
        ground_truth=None,
        review_ground_truth=None,
        manual_matches=None,
        write_ground_truth_template=False,
        skip_compare=skip_compare,
        max_workers=None,
        no_cache=True,
        no_expand_blocks=False,
        no_block_text_detection=False,
        reuse_match_candidates=None,
        dxf_cache_dir=None,
        compare_state_dir=None,
        reuse_compare_state=None,
        export_cloud_marks=False,
        export_before_cloud_marks=False,
        change_zone_report=False,
        artifact_dir=None,
        review_state=None,
        export_preview=False,
        preview_dpi=80,
        preview_max_edge_px=2400,
        executive_review=False,
        executive_top_drawings=15,
        executive_top_zones=30,
        review_dashboard=False,
        top_review_issues=100,
        top_issues_per_drawing=20,
        fold_repetitive_layers=True,
        export_viewer_package=False,
        viewer_mode="image-tiles",
        viewer_render_policy="lazy",
        viewer_engine="auto",
        viewer_cache_dir=None,
        tile_size=512,
        max_visible_overlays=500,
        viewer_memory_budget_mb=512,
        render_selected_on_open=False,
        prefetch_neighbor_tiles=True,
        tile_prefetch_radius=1,
        overview_max_edge=2200,
        focus_tile_max_edge=1600,
        viewer_perf_log=False,
        render_selected_zone_evidence=False,
        selected_zone_evidence_per_pair=1,
        max_viewer_pages=30,
        max_zone_tiles=300,
        export_marked_pdf=False,
        marked_pdf_mode="selected",
        cloud_export_mode="selected",
        cloud_selection_csv=None,
        cloud_region_distance=1000.0,
        max_cloud_regions_per_pair=150,
        max_cloud_regions_total=3000,
        export_profile="internal",
        baseline=None,
        update_baseline=False,
        quality_gate=False,
        min_auto_precision=0.99,
        min_recall=0.95,
        max_match_time_regression=0.30,
        # Plan §16 Phase C — opt-in runtime budget capture
        measure_runtime_budget=measure_runtime_budget,
    )


def _stage_golden_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Stage the 02_single_modification fixture into a fresh tmp_path.

    Renames to a revision-paired filename so ``parse_filename_identity``
    yields the same ``match_key`` (verified: ``S-001_REV0`` and
    ``S-001_REV1`` both → ``S001``). The default ``before.dxf`` /
    ``after.dxf`` names produce ``BEFORE`` / ``AFTER`` keys that do
    NOT match.
    """
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    out_dir = tmp_path / "out"
    a_dir.mkdir()
    b_dir.mkdir()
    shutil.copy(
        GOLDEN_FIXTURE_DIR / "before.dxf", a_dir / "S-001_REV0.dxf"
    )
    shutil.copy(
        GOLDEN_FIXTURE_DIR / "after.dxf", b_dir / "S-001_REV1.dxf"
    )
    return a_dir, b_dir, out_dir


def _read_validation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Read the validation_summary.json file referenced by a run_validation payload."""
    summary_path_str = payload["outputs"]["summary_json"]
    summary_path = Path(summary_path_str)
    assert summary_path.exists(), f"validation_summary.json not written: {summary_path}"
    return json.loads(summary_path.read_text(encoding="utf-8"))


@pytest.mark.audit_chain_integration
@pytest.mark.integration
def test_validation_summary_contains_runtime_budget_with_comparator_metrics(
    tmp_path: Path,
) -> None:
    """Phase C-4.1 Test 1 — run_validation produces runtime_budget with new fields.

    Proves the pipeline end-to-end: golden DXF pair → run_validation →
    validation_summary.json → runtime_budget block contains the two
    Plan §16 Phase C metrics.

    ``peak_comparator_changes`` MUST be >= 1 because
    ``02_single_modification`` is guaranteed to surface at least one
    change record. ``time_to_first_stream_record_ms`` MAY be None
    because streaming requires a compare_state_dir and the default
    namespace here does not set one — the key must still exist with
    None value to prove the schema is wired through.
    """
    a_dir, b_dir, out_dir = _stage_golden_pair(tmp_path)
    args = _build_validation_namespace(a_dir=a_dir, b_dir=b_dir, out_dir=out_dir)

    payload = validator_module.run_validation(args)
    summary = _read_validation_summary(payload)

    # comparison.completed_pairs > 0 is required for the audit gate to evaluate
    # this output's runtime_budget. If this fires, the golden pair didn't pair.
    completed = int(
        summary.get("comparison", {}).get("completed_pairs", 0) or 0
    )
    assert completed >= 1, (
        f"02_single_modification did not produce a completed pair "
        f"(comparison.completed_pairs={completed}); check filename matching "
        f"(parse_filename_identity match_key collision)"
    )

    runtime_budget = summary.get("runtime_budget")
    assert isinstance(runtime_budget, dict), (
        f"runtime_budget missing or not a dict: {type(runtime_budget).__name__}"
    )

    # Plan §16 Phase C-2.1 — peak_comparator_changes must be populated
    peak = runtime_budget.get("peak_comparator_changes")
    assert peak is not None and int(peak) >= 1, (
        f"peak_comparator_changes={peak} — expected >=1 for "
        f"02_single_modification (1 LINE MODIFIED). Either the comparator "
        f"hot-loop helper (_record_change/_record_changes) bypassed the "
        f"peak counter, or the harvester at "
        f"validate_drawing_compare_realset.py:713-735 lost the metric."
    )

    # Plan §16 Phase C-3.1 — the field must exist (schema gate); value
    # may be None because no compare_state_dir was set on this run.
    assert "time_to_first_stream_record_ms" in runtime_budget, (
        "time_to_first_stream_record_ms key absent from runtime_budget — "
        "schema_version=2 contract violated"
    )


@pytest.mark.audit_chain_integration
@pytest.mark.integration
def test_audit_gate_fails_on_peak_comparator_changes_breach(tmp_path: Path) -> None:
    """Phase C-4.1 Test 2 — audit gate FAILS when peak_comparator_changes exceeds threshold.

    Proves the new ``--max-peak-comparator-changes`` gate auto-activates
    when its keyword is supplied (per
    ``audit_drawing_compare_mvp_exit.py:718-722`` — the gate's
    activation condition includes the comparator-threshold kwargs)
    and that the threshold actually triggers a failure detail string.
    """
    a_dir, b_dir, out_dir = _stage_golden_pair(tmp_path)
    args = _build_validation_namespace(a_dir=a_dir, b_dir=b_dir, out_dir=out_dir)

    validator_module.run_validation(args)

    # Use threshold=0 so the breach is guaranteed regardless of the
    # actual peak value (we already proved in Test 1 that it's >=1).
    report = audit_module.run_audit(
        result_dirs=[out_dir],
        max_peak_comparator_changes=0,
    )

    assert report["status"] == "failed", (
        f"audit gate did not fail with max_peak_comparator_changes=0; "
        f"summary={report.get('summary')}"
    )

    # Locate the runtime_budget check and verify its detail references
    # the breached comparator threshold.
    runtime_check = next(
        (
            check
            for check in report["checks"]
            if check.get("name") == "runtime_budget_measurement"
        ),
        None,
    )
    assert runtime_check is not None, "runtime_budget_measurement check not present in report"
    assert runtime_check["passed"] is False, (
        f"runtime_budget_measurement passed despite threshold=0; "
        f"detail={runtime_check.get('detail')}"
    )
    detail = str(runtime_check.get("detail") or "")
    assert "peak_comparator_changes=" in detail, (
        f"failure detail missing 'peak_comparator_changes=' marker — got: {detail!r}"
    )


@pytest.mark.audit_chain_integration
@pytest.mark.integration
def test_audit_gate_passes_when_peak_comparator_changes_under_threshold(
    tmp_path: Path,
) -> None:
    """Phase C-4.1 Test 3 — gate does NOT flag comparator changes when threshold is generous.

    Negative-control for Test 2: confirms the gate is not a no-op false
    alarm. With ``max_peak_comparator_changes=100_000`` (well above any
    plausible value from the tiny golden fixture), no failure detail
    should mention the comparator threshold.

    The overall audit ``status`` may still be ``failed`` because other
    audit checks (customer_grade_evidence, scale, etc.) are unrelated
    to runtime_budget and may legitimately fail in a tiny synthetic
    run. We assert specifically that *no comparator-changes failure*
    surfaces, which is the property under test.
    """
    a_dir, b_dir, out_dir = _stage_golden_pair(tmp_path)
    args = _build_validation_namespace(a_dir=a_dir, b_dir=b_dir, out_dir=out_dir)

    validator_module.run_validation(args)

    report = audit_module.run_audit(
        result_dirs=[out_dir],
        max_peak_comparator_changes=100_000,
    )

    runtime_check = next(
        (
            check
            for check in report["checks"]
            if check.get("name") == "runtime_budget_measurement"
        ),
        None,
    )
    assert runtime_check is not None, "runtime_budget_measurement check not present"

    detail = str(runtime_check.get("detail") or "")
    # The detail string MAY include "max_peak_comparator_changes=100000"
    # as a configuration echo (per
    # audit_drawing_compare_mvp_exit.py:2012-2015), but it MUST NOT
    # report "peak_comparator_changes=NNN > 100000" as a failure.
    assert "> 100000" not in detail, (
        f"runtime_budget gate spuriously reported a comparator breach with "
        f"threshold=100000 against a tiny fixture — detail: {detail!r}"
    )
    # Stronger property: the runtime_budget check itself should pass at
    # this threshold (the gate is the only one we configured to be
    # strict; if the rest of the report fails, that's other gates).
    assert runtime_check["passed"] is True, (
        f"runtime_budget_measurement failed with generous threshold; "
        f"detail={detail!r}"
    )
