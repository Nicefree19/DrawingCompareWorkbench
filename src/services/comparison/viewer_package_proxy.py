# -*- coding: utf-8 -*-
"""Subprocess proxy for export_viewer_package().

Audit-gates §10.5 Phase A — wraps ``scripts/render_viewer_package_subprocess.py``
so callers can invoke ``export_viewer_package_isolated()`` and obtain the
same ``ViewerPackage`` payload as the in-process API while keeping the host
process insulated from memory blow-ups.

Design notes
============
- **Single dependency** on the subprocess JSONL protocol from
  ``scripts/render_viewer_package_subprocess.py``.
- **Streaming heartbeat**: the proxy yields per-progress events to a callback
  so the GUI can keep a live spinner / progress bar updated.
- **Graceful fallback**: when ``allow_inprocess_fallback=True`` and the
  subprocess fails to start or returns exit code 1 (generic error), the
  proxy retries by calling ``export_viewer_package`` in-process. Memory
  budget exceptions are *never* fallen back to in-process (that defeats
  the purpose of isolation).
- **Cap discovery**: the parent owns the ``memory_cap_mb`` decision so the
  subprocess can fail fast before the OS starts paging.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .workbench_subprocess import VIEWER_PACKAGE_WORKER_MODULE, worker_command_for_module

logger = logging.getLogger(__name__)

SUBPROCESS_SCRIPT_REL = Path("scripts") / "render_viewer_package_subprocess.py"
DEFAULT_TIMEOUT_S = 1800.0  # 30 minutes — accommodates S20-class DWG renders


@dataclass
class SubprocessRunReport:
    """Diagnostic record for a single subprocess invocation."""

    exit_code: int
    elapsed_s: float
    last_memory_sample_mb: Optional[float] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    error_stage: Optional[str] = None
    error_current_mb: Optional[float] = None
    error_max_mb: Optional[float] = None
    progress_event_count: int = 0
    fallback_used: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "elapsed_s": round(self.elapsed_s, 3),
            "last_memory_sample_mb": self.last_memory_sample_mb,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "error_stage": self.error_stage,
            "error_current_mb": self.error_current_mb,
            "error_max_mb": self.error_max_mb,
            "progress_event_count": self.progress_event_count,
            "fallback_used": self.fallback_used,
            "notes": list(self.notes),
        }


def _resolve_repo_root() -> Path:
    """Best-effort: find repo root by walking up until ``scripts/`` exists."""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / SUBPROCESS_SCRIPT_REL).exists():
            return candidate
    # Fallback: 4 levels up (src/services/comparison/ -> repo root)
    return here.parents[3]


def _build_subprocess_payload(
    artifact_dir: Path,
    options: dict[str, Any],
    memory_cap_mb: Optional[float],
    fault_log_dir: Optional[Path] = None,
) -> dict[str, Any]:
    serialised: dict[str, Any] = {"artifact_dir": str(artifact_dir)}
    for key, value in options.items():
        if value is None:
            continue
        if isinstance(value, Path):
            serialised[key] = str(value)
        elif isinstance(value, (str, int, float, bool, list, dict)):
            serialised[key] = value
        else:
            # Skip non-serialisable fields (e.g. preview/review object refs).
            logger.debug("Skipping non-serialisable option %s (%s)", key, type(value).__name__)
    payload: dict[str, Any] = {"options": serialised, "memory_cap_mb": memory_cap_mb}
    # Audit-gates §13.4 Phase B-2 — tell the child where to write its fault
    # log. Without this, a native crash in the renderer leaves the parent
    # with only ``exit_code=-1``. The child has a sensible fallback
    # (``<repo>/logs/subprocess``) so passing ``None`` here is still safe.
    if fault_log_dir is not None:
        payload["fault_log_dir"] = str(fault_log_dir)
    return payload


def export_viewer_package_isolated(
    artifact_dir: Path,
    *,
    options: Optional[dict[str, Any]] = None,
    memory_cap_mb: Optional[float] = 4096.0,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
    python_executable: Optional[str] = None,
    allow_inprocess_fallback: bool = False,
    fault_log_dir: Optional[Path] = None,
) -> tuple[Optional[dict[str, Any]], SubprocessRunReport]:
    """Run export_viewer_package in a subprocess and return its result + report.

    Returns:
        ``(viewer_package_dict, report)`` on success.
        ``(None, report)`` on failure; check ``report.error_type``.

    Notes:
        - When ``allow_inprocess_fallback=True`` and the subprocess fails with
          a generic error (exit 1), the function silently retries in-process
          and sets ``report.fallback_used=True``.
        - ``MemoryBudgetExceeded`` (exit 2) is *never* fallen back — that is
          the entire point of isolation.
        - The subprocess is killed on ``timeout_s``; the report records
          ``error_type="Timeout"``.
    """
    import time as _time

    options = dict(options or {})
    repo_root = _resolve_repo_root()
    script_path = repo_root / SUBPROCESS_SCRIPT_REL
    if not script_path.exists():
        return None, SubprocessRunReport(
            exit_code=-1,
            elapsed_s=0.0,
            error_type="ScriptMissing",
            error_message=f"subprocess script not found: {script_path}",
        )

    payload = _build_subprocess_payload(
        artifact_dir, options, memory_cap_mb, fault_log_dir=fault_log_dir
    )
    # Plan §19 A-5 (Agent T finding T2) — unversioned ``"python"``
    # fallback was a PATH-hijack vector: any directory early in the
    # user's PATH containing a malicious ``python`` binary would be
    # executed in this subprocess context. Replace with an explicit
    # error so the caller has to supply a verified interpreter path.
    interpreter = python_executable or sys.executable
    if not interpreter:
        raise RuntimeError(
            "no Python interpreter available — set ``python_executable`` "
            "explicitly or ensure ``sys.executable`` is populated. The "
            "previous unversioned ``'python'`` fallback was removed per "
            "Plan §19 A-5 (Agent T T2) to prevent PATH-hijack injection."
        )

    started = _time.perf_counter()
    notes: list[str] = []
    last_memory_mb: Optional[float] = None
    progress_event_count = 0
    result_payload: Optional[dict[str, Any]] = None
    error_event: Optional[dict[str, Any]] = None
    timed_out = False
    cancelled = False

    try:
        program, worker_args = worker_command_for_module(
            VIEWER_PACKAGE_WORKER_MODULE,
            executable=interpreter,
        )
        proc = subprocess.Popen(
            [program, *worker_args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(repo_root),
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            text=True,
        )
    except Exception as exc:
        elapsed = _time.perf_counter() - started
        return None, SubprocessRunReport(
            exit_code=-1,
            elapsed_s=elapsed,
            error_type="SubprocessLaunchFailed",
            error_message=str(exc),
        )

    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload, ensure_ascii=False))
        proc.stdin.close()
    except Exception as exc:
        notes.append(f"stdin_write_failed:{exc!r}")

    assert proc.stdout is not None
    deadline = started + max(1.0, float(timeout_s))
    import queue as _queue
    import threading as _threading

    stdout_queue: _queue.Queue[Optional[str]] = _queue.Queue()

    def _read_stdout() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                stdout_queue.put(line)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"stdout_reader_failed:{exc!r}")
        finally:
            stdout_queue.put(None)

    stdout_thread = _threading.Thread(
        target=_read_stdout,
        name="viewer-package-stdout-reader",
        daemon=True,
    )
    stdout_thread.start()
    try:
        while True:
            if cancel_callback is not None:
                try:
                    if cancel_callback():
                        proc.kill()
                        cancelled = True
                        notes.append("cancel_killed")
                        break
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"cancel_callback_failed:{exc!r}")
            if _time.perf_counter() > deadline:
                proc.kill()
                timed_out = True
                notes.append("timeout_killed")
                break
            try:
                raw_line = stdout_queue.get(timeout=0.2)
            except _queue.Empty:
                poll = getattr(proc, "poll", None)
                if callable(poll) and poll() is not None and stdout_queue.empty():
                    break
                continue
            if raw_line is None:
                break
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                notes.append(f"bad_jsonl:{line[:120]!r}")
                continue
            progress_event_count += 1
            event_type = str(event.get("event") or "")
            if event_type == "memory_sample":
                value = event.get("peak_working_set_mb")
                if isinstance(value, (int, float)):
                    last_memory_mb = float(value)
            elif event_type == "result":
                result_payload = event.get("viewer_package") if isinstance(event, dict) else None
            elif event_type == "error":
                error_event = event
            if progress_callback is not None:
                try:
                    progress_callback(event)
                except Exception:
                    notes.append("progress_callback_raised")
    finally:
        try:
            exit_code = proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            exit_code = proc.wait()
            notes.append("forced_kill_after_stream")
        # P0 leak fix (multi-agent audit 2026-05-15): the JSONL stream loop
        # above only consumes stdout. proc.stderr stays open and the underlying
        # OS pipe FD leaks across thousands of subprocess invocations, which
        # eventually exhausts the per-process FD limit on long sessions. Close
        # both pipes unconditionally — Popen has already returned, and the
        # subprocess's own writes complete on .wait().
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:
                pass

    elapsed = _time.perf_counter() - started
    report = SubprocessRunReport(
        exit_code=exit_code,
        elapsed_s=elapsed,
        last_memory_sample_mb=last_memory_mb,
        progress_event_count=progress_event_count,
        notes=notes,
    )
    if timed_out:
        report.error_type = "Timeout"
        report.error_message = f"subprocess exceeded timeout_s={timeout_s}"
        return None, report
    if cancelled:
        report.error_type = "Cancelled"
        report.error_message = "subprocess cancelled by caller"
        return None, report
    if error_event is not None:
        report.error_type = str(error_event.get("type") or "Unknown")
        report.error_message = str(error_event.get("message") or "")
        report.error_stage = error_event.get("stage")
        report.error_current_mb = error_event.get("current_mb")
        report.error_max_mb = error_event.get("max_mb")

    if exit_code == 0 and result_payload is not None:
        return result_payload, report

    if exit_code == 2 or report.error_type == "MemoryBudgetExceeded":
        return None, report

    if allow_inprocess_fallback and exit_code != 0:
        notes.append(f"falling_back_to_inprocess:exit={exit_code}")
        report.fallback_used = True
        try:
            from .viewer_package import export_viewer_package

            inprocess_options = dict(options)
            inprocess_options.pop("runtime_sampler", None)
            viewer_package = export_viewer_package(
                artifact_dir, **inprocess_options, memory_cap_mb=memory_cap_mb
            )
            return viewer_package.to_dict(), report
        except Exception as exc:
            report.notes.append(f"fallback_failed:{exc!r}")
            if report.error_type is None:
                report.error_type = type(exc).__name__
                report.error_message = str(exc)

    return None, report


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "SubprocessRunReport",
    "export_viewer_package_isolated",
]
