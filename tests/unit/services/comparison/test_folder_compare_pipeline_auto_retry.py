# -*- coding: utf-8 -*-
"""Tests for the MemoryBudgetExceeded auto-retry path (Plan C-1).

Audit §1.3 finding #2 — when ``viewer_package_proxy`` raises
``MemoryBudgetExceeded``, the pipeline used to surface the error directly to
the GUI, requiring the user to manually drop the quality combo and re-run.
Plan C-1 wired ``adaptive_quality.downgrade_one_step()`` into the catch
block so the pipeline auto-retries ONCE with a lower DPI tier.

Tests cover:
- Happy path: first call raises MemoryBudgetExceeded, retry succeeds.
- Hard failure: both calls raise — final exception propagates.
- Floor edge case: starting tier is already DPI 80 — no retry attempted.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.comparison import folder_compare_pipeline as pipeline
from src.services.comparison.drawing_batch import (
    BatchCompareItemResult,
    BatchCompareSummary,
    DrawingFileDescriptor,
    DrawingKind,
    MatchCandidate,
    MatchStatus,
    parse_filename_identity,
)
from src.services.comparison.runtime_budget import MemoryBudgetExceeded


# ---------------------------------------------------------------------------
# Test scaffolding — minimum stubs to reach the viewer-proxy catch block
# ---------------------------------------------------------------------------


def _descriptor(path: Path, kind: DrawingKind = DrawingKind.CAD) -> DrawingFileDescriptor:
    return DrawingFileDescriptor(
        path=str(path),
        kind=kind,
        extension=path.suffix,
        identity=parse_filename_identity(path),
    )


def _install_pipeline_stubs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Set up every pipeline collaborator EXCEPT the viewer proxy.

    Returns ``(old_dir, new_dir)`` so the caller can build a
    ``FolderCompareRunRequest``. The caller is expected to monkeypatch
    ``pipeline.export_viewer_package_isolated`` separately to drive the
    auto-retry behaviour under test.
    """
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old = old_dir / "S-100_REV0.dxf"
    new = new_dir / "S-100_REV1.dxf"
    old.write_text("0\nEOF\n", encoding="utf-8")
    new.write_text("0\nEOF\n", encoding="utf-8")
    desc_a = _descriptor(old)
    desc_b = _descriptor(new)
    candidate = MatchCandidate(desc_a, desc_b, score=0.95, status=MatchStatus.AUTO_CONFIRMED)

    monkeypatch.setattr(
        pipeline,
        "scan_drawing_inputs",
        lambda source, options: [desc_a] if Path(source) == old_dir else [desc_b],
    )
    monkeypatch.setattr(pipeline, "match_drawing_sets", lambda a, b, options: [candidate])

    class FakeJob:
        def __init__(self, candidates, options):
            self.candidates = candidates
            self.options = options

        def run(self, progress_callback=None, is_cancelled=None):
            summary = BatchCompareSummary(started_at=datetime.now(), requested_pairs=1)
            summary.items.append(BatchCompareItemResult(candidate=candidate, status="completed"))
            summary.finished_at = datetime.now()
            state_path = Path(self.options.compare_state_dir) / "compare_state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(
                    {
                        "summary": {
                            "items": [
                                {
                                    "candidate": {
                                        "source_a": {"path": str(old.resolve())},
                                        "source_b": {"path": str(new.resolve())},
                                    }
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            return summary

    monkeypatch.setattr(pipeline, "BatchCompareJob", FakeJob)

    def fake_artifacts(summary, output_dir, **kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            output_paths={
                "artifact_manifest_json": str(Path(output_dir) / "artifact_manifest.json"),
            },
            raw_change_count=1,
            zone_count=1,
            cloud_region_count=0,
            cloud_omitted_zone_count=0,
            to_dict=lambda: {"raw_change_count": 1},
        )

    def fake_preview(summary, output_dir, **kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        manifest = Path(output_dir) / "preview_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return SimpleNamespace(
            manifest_path=str(manifest),
            artifacts=[],
            preview_count=0,
            to_dict=lambda: {"preview_count": 0},
        )

    def fake_executive(output_dir, **kwargs):
        html = Path(output_dir) / "executive_review.html"
        html.write_text("<html></html>", encoding="utf-8")
        return SimpleNamespace(
            output_paths={
                "executive_review_html": str(html),
                "drawing_change_brief_csv": str(Path(output_dir) / "drawing_change_brief.csv"),
                "review_dashboard_json": str(Path(output_dir) / "review_dashboard.json"),
            },
            to_dict=lambda: {"output_paths": {"executive_review_html": str(html)}},
        )

    monkeypatch.setattr(pipeline, "export_change_artifacts", fake_artifacts)
    monkeypatch.setattr(pipeline, "export_preview_artifacts", fake_preview)
    monkeypatch.setattr(pipeline, "export_executive_review_from_artifacts", fake_executive)
    return old_dir, new_dir


def _success_dict(viewer_dir: str) -> dict:
    """Build a minimal viewer_package payload for a successful retry."""
    return {
        "output_paths": {
            "viewer_manifest_json": str(Path(viewer_dir) / "viewer_manifest.json"),
        },
        "pair_count": 1,
        "overlay_count": 0,
        "viewer_dir": viewer_dir,
    }


def _budget_exceeded_report() -> pipeline.SubprocessRunReport:
    """Build a SubprocessRunReport that mimics MemoryBudgetExceeded exit."""
    report = pipeline.SubprocessRunReport(exit_code=2, elapsed_s=1.0)
    report.error_type = "MemoryBudgetExceeded"
    report.error_stage = "viewer_package_subprocess"
    report.error_message = "memory_budget_exceeded:viewer_package_subprocess:current=4500MB>max=4096MB"
    report.error_current_mb = 4500.0
    report.error_max_mb = 4096.0
    return report


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_call_raises_memory_exceeded_second_call_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan C-1 happy path — proxy fails once, retry succeeds, run completes.

    Asserts:
      1. Pipeline returns successfully (no MemoryBudgetExceeded surfaces).
      2. Proxy was called exactly twice.
      3. Second call's ``preview_dpi`` is strictly lower than the first.
      4. A 97.5 progress event with the auto-retry message was emitted.
    """
    old_dir, new_dir = _install_pipeline_stubs(tmp_path, monkeypatch)

    call_log: list[dict] = []

    def flaky_proxy(
        artifact_dir,
        *,
        options=None,
        memory_cap_mb=None,
        timeout_s=None,
        progress_callback=None,
        python_executable=None,
        allow_inprocess_fallback=False,
        fault_log_dir=None,
    ):
        opts = dict(options or {})
        call_log.append({"preview_dpi": opts.get("preview_dpi"), "preview_max_edge_px": opts.get("preview_max_edge_px")})
        if len(call_log) == 1:
            return None, _budget_exceeded_report()
        viewer_dir_arg = opts.pop("viewer_dir", None)
        return (
            _success_dict(str(viewer_dir_arg) if viewer_dir_arg else str(artifact_dir)),
            pipeline.SubprocessRunReport(exit_code=0, elapsed_s=2.0),
        )

    monkeypatch.setattr(pipeline, "export_viewer_package_isolated", flaky_proxy)

    progress_events: list[tuple[str, float, str]] = []

    def capture_progress(stage, percent, message):
        progress_events.append((stage, percent, message))

    # Start at DPI 200 so we have room to downgrade.
    request = pipeline.FolderCompareRunRequest(
        old_dir, new_dir, tmp_path / "out",
        preview_dpi=200, preview_max_edge_px=6000,
    )
    result = pipeline.FolderComparePipeline(request).run(progress_callback=capture_progress)

    assert result is not None, "pipeline should complete despite first MemoryBudgetExceeded"
    assert len(call_log) == 2, f"proxy must be called exactly twice, got {len(call_log)}: {call_log}"
    first_dpi = call_log[0]["preview_dpi"]
    second_dpi = call_log[1]["preview_dpi"]
    assert first_dpi == 200, f"first call should use original DPI 200, got {first_dpi}"
    assert second_dpi is not None and second_dpi < first_dpi, (
        f"retry must use a lower DPI than {first_dpi}, got {second_dpi}"
    )
    assert call_log[1]["preview_max_edge_px"] is not None
    assert call_log[1]["preview_max_edge_px"] < call_log[0]["preview_max_edge_px"], (
        "retry must also lower preview_max_edge_px in lock step"
    )

    retry_events = [e for e in progress_events if "재시도" in e[2] or "DPI" in e[2]]
    assert retry_events, (
        f"a retry progress event with 'DPI' or '재시도' was expected, got: {progress_events}"
    )
    # Per Plan C-1, the auto-retry emit uses percent=97.5 with both
    # 'DPI' and '재시도' in the message.
    matching = [e for e in progress_events if "DPI" in e[2] and "재시도" in e[2]]
    assert matching, f"expected 'DPI ... 재시도' message, got: {progress_events}"
    assert any(abs(e[1] - 97.5) < 0.01 for e in matching), (
        f"auto-retry progress event must be at 97.5, got percents: {[e[1] for e in matching]}"
    )


def test_both_calls_raise_propagates_final_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard failure — both attempts hit the cap. Final exception escapes.

    Asserts:
      1. ``MemoryBudgetExceeded`` is raised to the caller.
      2. Proxy was called exactly twice (no infinite loop).
    """
    old_dir, new_dir = _install_pipeline_stubs(tmp_path, monkeypatch)

    call_count = {"n": 0}

    def always_oom_proxy(
        artifact_dir,
        *,
        options=None,
        memory_cap_mb=None,
        timeout_s=None,
        progress_callback=None,
        python_executable=None,
        allow_inprocess_fallback=False,
        fault_log_dir=None,
    ):
        call_count["n"] += 1
        return None, _budget_exceeded_report()

    monkeypatch.setattr(pipeline, "export_viewer_package_isolated", always_oom_proxy)

    request = pipeline.FolderCompareRunRequest(
        old_dir, new_dir, tmp_path / "out",
        preview_dpi=200, preview_max_edge_px=6000,
    )
    with pytest.raises(MemoryBudgetExceeded):
        pipeline.FolderComparePipeline(request).run()

    assert call_count["n"] == 2, (
        f"proxy must be called exactly twice (initial + retry), got {call_count['n']}"
    )


def test_already_at_lowest_tier_propagates_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Floor case — DPI 80 already, downgrade no-ops, no retry attempted.

    Asserts:
      1. ``MemoryBudgetExceeded`` is raised to the caller.
      2. Proxy was called exactly ONCE (no retry, since downgrade had nowhere
         to go from the safe-mode tier).
    """
    old_dir, new_dir = _install_pipeline_stubs(tmp_path, monkeypatch)

    call_count = {"n": 0}

    def floor_oom_proxy(
        artifact_dir,
        *,
        options=None,
        memory_cap_mb=None,
        timeout_s=None,
        progress_callback=None,
        python_executable=None,
        allow_inprocess_fallback=False,
        fault_log_dir=None,
    ):
        call_count["n"] += 1
        return None, _budget_exceeded_report()

    monkeypatch.setattr(pipeline, "export_viewer_package_isolated", floor_oom_proxy)

    # Start at DPI 80 (safe-mode floor) — downgrade_one_step should no-op.
    request = pipeline.FolderCompareRunRequest(
        old_dir, new_dir, tmp_path / "out",
        preview_dpi=80, preview_max_edge_px=2400,
    )
    with pytest.raises(MemoryBudgetExceeded):
        pipeline.FolderComparePipeline(request).run()

    assert call_count["n"] == 1, (
        f"at floor, proxy must be called exactly ONCE (no retry), got {call_count['n']}"
    )
