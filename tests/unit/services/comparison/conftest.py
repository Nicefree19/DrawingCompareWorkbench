# -*- coding: utf-8 -*-
"""Shared pytest fixtures for the comparison test suite.

Auto-suppresses the Workbench V2 first-run tutorial so headless tests don't
hang on a modal dialog. The tutorial flag file is created in a session-scoped
temporary directory before any DrawingCompareWorkbenchV2 is instantiated.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _stub_viewer_package_subprocess(request, monkeypatch):
    """Audit-gates §11.4 — pipeline now invokes ``export_viewer_package_isolated``
    (subprocess proxy) instead of the in-process function. For unit tests we
    transparently route the call back through the in-process exporter so we
    don't need real fixture data on disk to spawn a Python child.

    Tests that explicitly monkeypatch ``pipeline.export_viewer_package`` keep
    working because we call that name (post-patch) inside this stub. Tests
    that already monkeypatch ``pipeline.export_viewer_package_isolated``
    override us cleanly — pytest applies their patch after this fixture, and
    unwinds it first on teardown.

    Failures (e.g. missing change_zones.csv when no fixture data is present)
    surface as a ``RuntimeError`` from the pipeline boundary, mirroring the
    real subprocess error path so existing assertions still apply.

    Self-review 2026-05-15 (§1.3 finding #4) — tests marked with
    ``@pytest.mark.subprocess_real`` opt out of this stub so they exercise
    the real subprocess code path. This is required because the autouse
    stub otherwise gives the subprocess proxy 0% real coverage.
    """
    if request.node.get_closest_marker("subprocess_real") is not None:
        # Opt-out: do not monkeypatch — let the real subprocess proxy run.
        return

    try:
        from src.services.comparison import folder_compare_pipeline as pipeline_mod
    except Exception:
        return

    def _inprocess(
        artifact_dir,
        *,
        options=None,
        memory_cap_mb=None,
        timeout_s=None,
        progress_callback=None,
        python_executable=None,
        allow_inprocess_fallback=False,
        fault_log_dir=None,  # §13.4 Phase B-2 — child fault log dir passthrough
    ):
        opts = dict(options or {})
        viewer_dir_arg = opts.pop("viewer_dir", None)
        try:
            result = pipeline_mod.export_viewer_package(
                artifact_dir, viewer_dir_arg, **opts
            )
        except Exception as exc:
            return (
                None,
                pipeline_mod.SubprocessRunReport(
                    exit_code=1,
                    elapsed_s=0.0,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                ),
            )
        # Normalise to the dict shape the proxy returns.
        if hasattr(result, "to_dict"):
            payload = result.to_dict()
            payload.setdefault("output_paths", getattr(result, "output_paths", {}))
            payload.setdefault("overlay_count", getattr(result, "overlay_count", 0))
            payload.setdefault("pair_count", getattr(result, "pair_count", 0))
        elif isinstance(result, dict):
            payload = dict(result)
        else:
            payload = {
                "output_paths": getattr(result, "output_paths", {}),
                "overlay_count": getattr(result, "overlay_count", 0),
                "pair_count": getattr(result, "pair_count", 0),
            }
        return (
            payload,
            pipeline_mod.SubprocessRunReport(exit_code=0, elapsed_s=0.0),
        )

    monkeypatch.setattr(
        pipeline_mod, "export_viewer_package_isolated", _inprocess, raising=False
    )


@pytest.fixture(autouse=True, scope="session")
def _disable_first_run_tutorial(tmp_path_factory):
    """Create a tutorial-completed flag file in a tmp dir and redirect the
    Workbench data dir there for the test session.

    This prevents the tutorial QDialog from auto-popping during widget tests —
    such modal dialogs would block the headless event loop and time out the
    test. Production behaviour is unaffected.
    """

    tmp_dir = tmp_path_factory.mktemp("workbench_tutorial_suppress")
    flag = tmp_dir / "tutorial_completed.flag"
    flag.write_text("completed_at=test\nresult=auto-suppress\n", encoding="utf-8")

    try:
        from src.gui import drawing_compare_workbench as dcw
    except Exception:
        # If the GUI module isn't importable in this test (e.g. PySide6 missing),
        # there's nothing to patch — let the test handle the import failure.
        yield
        return

    original = dcw._workbench_data_dir
    dcw._workbench_data_dir = lambda: tmp_dir
    yield
    dcw._workbench_data_dir = original
