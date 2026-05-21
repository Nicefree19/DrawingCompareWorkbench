# -*- coding: utf-8 -*-
"""Real-subprocess integration test for ``export_viewer_package_isolated``.

Audit self-review 2026-05-15 (§1.3 finding #4) — the unit suite's autouse
fixture in ``tests/unit/services/comparison/conftest.py`` monkeypatches
``export_viewer_package_isolated`` to an in-process stub. That means every
unit test currently bypasses the real subprocess code path. The "2860 passed"
green wall is faux stability for the subprocess proxy, which actually has
0% real coverage from the unit suite.

This test opts out of that stub via the ``@pytest.mark.subprocess_real``
marker (registered in ``pytest.ini``; the conftest fixture checks for it
and skips its monkeypatch). The test then drives a real Python subprocess
through ``viewer_package_proxy.export_viewer_package_isolated`` against a
synthetic ``artifact_dir``.

Acceptable-simplification strategy
==================================
A full viewer-build run requires golden DXF fixtures, an artifact manifest,
overlays, and other heavy state. We deliberately do *not* provide that
data. Instead, we provide just enough for the subprocess to:

1. Parse stdin payload (must include ``artifact_dir`` — proves InvalidInput
   path is NOT the failure mode).
2. Arm the Windows fault handler (proves the subprocess Python interpreter
   actually started and imported ``src.core.error_handler``).
3. Reach ``export_viewer_package`` and raise ``FileNotFoundError`` because
   ``change_zones.csv`` is missing (proves the subprocess actually executes
   our renderer entry point and produces a *structured* error event — not
   a launch failure or silent crash).

The point is to prove the subprocess actually runs Python and emits
structured JSONL events. A zero-render run that returns an InvalidInput-
or FileNotFoundError-typed report — with ``fault_handler_armed`` event
captured — is sufficient to refute the "0% coverage" finding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.services.comparison.viewer_package_proxy import (
    SubprocessRunReport,
    export_viewer_package_isolated,
)


@pytest.mark.subprocess_real
@pytest.mark.integration
def test_real_subprocess_runs_and_emits_structured_error(tmp_path: Path) -> None:
    """Spawn the real subprocess against an empty artifact_dir and verify
    structured event emission + structured error reporting.

    This is the minimum that proves the subprocess code path actually runs
    end-to-end (interpreter spawn → JSONL stdin → Python imports →
    fault handler armed → renderer entry → structured error → exit code).
    """
    artifact_dir = tmp_path / "empty_artifacts"
    artifact_dir.mkdir()
    fault_log_dir = tmp_path / "fault_logs"
    fault_log_dir.mkdir()

    captured_events: list[dict[str, Any]] = []

    def _on_progress(event: dict[str, Any]) -> None:
        captured_events.append(event)

    result_payload, report = export_viewer_package_isolated(
        artifact_dir,
        options={},
        memory_cap_mb=512.0,
        timeout_s=60.0,
        progress_callback=_on_progress,
        allow_inprocess_fallback=False,
        fault_log_dir=fault_log_dir,
    )

    # --- Subprocess execution proof ---------------------------------------
    # The proxy must return a SubprocessRunReport even on failure.
    assert isinstance(report, SubprocessRunReport), (
        "Proxy must always return a SubprocessRunReport; got "
        f"{type(report).__name__}"
    )

    # Exit code must NOT be the launch-failure sentinel (-1). That would mean
    # Popen failed before the subprocess Python interpreter started, which
    # would make this test prove nothing about the subprocess code path.
    assert report.exit_code != -1, (
        "Subprocess failed to launch — proxy never reached the JSONL stream. "
        f"This test proves nothing. report={report.to_dict()}"
    )

    # The proxy must have parsed at least one JSONL event (started + at
    # minimum one error). Zero events here would mean the subprocess never
    # wrote to stdout, which is a different failure mode than what we
    # intend to cover.
    assert report.progress_event_count >= 1, (
        f"Subprocess emitted no events — JSONL stream broken. report={report.to_dict()}"
    )

    # --- fault_handler_armed event capture --------------------------------
    # This is the single strongest proof that the subprocess actually
    # executed Python code (not just started the interpreter). The event
    # is emitted only after ``src.core.error_handler.enable_windows_fault_handler``
    # is successfully imported and called from inside the subprocess.
    fault_handler_events = [
        e for e in captured_events if e.get("event") == "fault_handler_armed"
    ]
    assert len(fault_handler_events) >= 1, (
        "Subprocess did not emit 'fault_handler_armed' event — either the "
        "subprocess never reached the post-payload-parse code, or the "
        "fault-handler arming silently failed. Captured events: "
        f"{[e.get('event') for e in captured_events]}"
    )

    # --- 'started' event sanity -------------------------------------------
    started_events = [e for e in captured_events if e.get("event") == "started"]
    assert len(started_events) >= 1, (
        "Subprocess did not emit 'started' event. "
        f"Captured: {[e.get('event') for e in captured_events]}"
    )
    assert started_events[0].get("schema_version") == 1, (
        "Subprocess JSONL schema_version drifted; expected 1, got "
        f"{started_events[0].get('schema_version')}"
    )

    # --- Structured error reporting ---------------------------------------
    # Because artifact_dir is empty, the subprocess will reach
    # ``export_viewer_package`` and raise FileNotFoundError on the missing
    # ``change_zones.csv``. The subprocess script catches this in its
    # generic Exception handler and emits a structured 'error' event with
    # exit code 1. (If the renderer ever changes to validate inputs upfront
    # and exit 3 with InvalidInput, that's also acceptable — both prove the
    # subprocess produces a structured error.)
    assert result_payload is None, (
        "Subprocess unexpectedly returned a viewer_package payload from an "
        "empty artifact_dir. Either the renderer became too permissive, or "
        "the test setup is no longer minimal. report=" + str(report.to_dict())
    )
    assert report.error_type is not None, (
        "Subprocess failed without producing a structured error event. "
        f"report={report.to_dict()}"
    )
    assert report.error_type in {
        "FileNotFoundError",  # change_zones.csv missing — current behaviour
        "InvalidInput",       # if renderer adds upfront validation later
    }, (
        f"Unexpected error_type={report.error_type!r}; expected one of "
        "{'FileNotFoundError', 'InvalidInput'}. This may indicate the "
        "renderer entry point changed, the subprocess crashed before "
        "reaching the renderer, or environment drift. "
        f"report={report.to_dict()}"
    )
    assert report.exit_code in {1, 3}, (
        f"Unexpected exit_code={report.exit_code}; expected 1 (generic "
        "exception) or 3 (InvalidInput). report=" + str(report.to_dict())
    )
