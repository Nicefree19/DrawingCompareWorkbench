"""Tests for Drawing Compare workbench acceptance smoke reporting."""

from __future__ import annotations

import argparse
import json

from scripts import workbench_acceptance_smoke as smoke


def test_workbench_acceptance_smoke_writes_runtime_metrics(tmp_path, monkeypatch) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    class FakeApplication:
        @staticmethod
        def instance():
            return None

        def __init__(self, argv):
            self.argv = argv

        def processEvents(self) -> None:
            return None

    class FakeWorkbench:
        def _on_auto_finished_v2(self, result) -> None:
            self.result = result

    ticks = iter([10.0, 10.25])

    monkeypatch.setattr(smoke, "parse_args", lambda argv=None: argparse.Namespace(
        results_dir=results_dir,
        a=None,
        b=None,
        screenshots_dir=None,
        skip_screenshots=True,
    ))
    monkeypatch.setattr(smoke, "QApplication", FakeApplication)
    monkeypatch.setattr(smoke, "_build_pipeline_result", lambda *args, **kwargs: object())
    monkeypatch.setattr(smoke, "DrawingCompareWorkbenchV2", FakeWorkbench)
    monkeypatch.setattr(smoke, "_format_widget_state_table", lambda workbench: "ok")
    monkeypatch.setattr(smoke.time, "perf_counter", lambda: next(ticks))

    def fake_run_acceptance(workbench, results_dir_arg, runtime_metrics=None):
        runtime_metrics["dashboard_select_to_first_zone_open_ms"] = 321.0
        return [smoke.CheckResult("runtime metrics", True, "ok")]

    monkeypatch.setattr(smoke, "_run_acceptance", fake_run_acceptance)

    assert smoke.main([]) == 0
    summary = json.loads((results_dir / "workbench_acceptance_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "passed"
    assert summary["runtime_metrics"]["app_open_to_dashboard_ms"] == 250.0
    assert summary["runtime_metrics"]["dashboard_select_to_first_zone_open_ms"] == 321.0
