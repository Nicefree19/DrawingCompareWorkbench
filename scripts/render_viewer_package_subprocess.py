# -*- coding: utf-8 -*-
"""Subprocess wrapper for export_viewer_package().

Audit-gates §10.5 Phase A — invoke ``export_viewer_package`` in an isolated
Python process so that a memory blow-up on a single S20-class drawing does
not freeze the GUI main process.

Protocol
========
- **stdin**: a single JSON object with ``options`` (kwargs forwarded to
  ``export_viewer_package``) and optional ``memory_cap_mb``. Reads until EOF.
- **stdout**: JSONL stream of progress events plus a final ``result`` event.
  - ``{"event": "started", "schema_version": 1}``
  - ``{"event": "memory_sample", "peak_working_set_mb": 1234.5}``
  - ``{"event": "result", "viewer_package": {...}}`` (final, before exit 0)
  - ``{"event": "error", "type": "MemoryBudgetExceeded", ...}`` (before exit 2)
  - ``{"event": "error", "type": "Exception", "message": "..."}`` (exit 1)
- **stderr**: human-readable log lines (do not parse).
- **exit codes**: 0 success, 1 generic failure, 2 memory budget exceeded,
  3 invalid input.

The proxy module ``viewer_package_proxy.py`` reads the JSONL stream and
exposes a synchronous API matching ``export_viewer_package``.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Ensure we can import src.* when run as a standalone script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


SCHEMA_VERSION = 1


def _emit(event: dict[str, Any]) -> None:
    """Write a JSON line to stdout and flush immediately so the parent sees it."""
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read_options() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("stdin payload empty")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stdin is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("stdin must be a JSON object")
    return payload


def _arm_subprocess_fault_handler(payload: dict[str, Any]) -> Path | None:
    """Arm the Windows fault handler in this child process before the heavy
    rendering work begins.

    Audit-gates §13.4 Phase B-2 — the GUI main process already arms
    ``enable_windows_fault_handler()`` in ``start_drawing_compare_workbench``,
    but the subprocess inherits NOTHING from that arming because faulthandler
    state is per-process. Without this, a native crash inside the renderer
    (Qt6Core fast-fail, SIGSEGV, stack overflow) leaves the parent with only
    ``exit_code=-1`` and no Python stack to debug from.

    The log directory comes from the parent via ``payload['fault_log_dir']``
    (preferred — keeps subprocess logs in the same tree as the parent), or
    falls back to ``<repo>/logs/subprocess`` so even a payload bug still
    produces a fault log.

    Returns the path to the fault log on success, or ``None`` if arming
    failed — arming is best-effort and must NOT abort the run.
    """
    try:
        from src.core.error_handler import enable_windows_fault_handler

        raw_dir = payload.get("fault_log_dir") if isinstance(payload, dict) else None
        if isinstance(raw_dir, str) and raw_dir.strip():
            log_dir: Path = Path(raw_dir)
        else:
            log_dir = _REPO_ROOT / "logs" / "subprocess"
        # cleanup_older_than_days=0: parent process is responsible for the
        # global retention policy; subprocess invocations should not be
        # racing the parent's housekeeping.
        return enable_windows_fault_handler(log_dir=log_dir, cleanup_older_than_days=0)
    except Exception:  # noqa: BLE001 — best-effort, never block the worker
        return None


def _resolve_path_kwargs(options: dict[str, Any]) -> dict[str, Any]:
    """Convert path-like string fields into ``Path`` instances."""
    path_keys = (
        "viewer_dir",
        "review_dashboard",
        "review_dashboard_path",
        "preview_manifest",
        "preview_manifest_path",
        "marked_pdf_selection_csv",
        "dxf_cache_dir",
        "viewer_cache_dir",
    )
    converted = dict(options)
    for key in path_keys:
        value = converted.get(key)
        if isinstance(value, str) and value:
            converted[key] = Path(value)
    return converted


def main() -> int:
    _emit({"event": "started", "schema_version": SCHEMA_VERSION})
    started_at = time.perf_counter()

    try:
        payload = _read_options()
    except ValueError as exc:
        _emit({"event": "error", "type": "InvalidInput", "message": str(exc)})
        return 3

    # Arm faulthandler immediately after payload parse so any native crash in
    # the renderer (Qt6Core fast-fail, SIGSEGV) captures a usable stack. The
    # parent reads ``fault_log_dir`` off the payload to keep child fault logs
    # alongside the parent's. Arming is best-effort — a failure here must not
    # abort the run.
    fault_log_path = _arm_subprocess_fault_handler(payload)
    if fault_log_path is not None:
        _emit(
            {
                "event": "fault_handler_armed",
                "fault_log": str(fault_log_path),
            }
        )

    options = payload.get("options")
    if not isinstance(options, dict):
        _emit(
            {
                "event": "error",
                "type": "InvalidInput",
                "message": "payload['options'] must be a dict",
            }
        )
        return 3

    artifact_dir_raw = options.pop("artifact_dir", None)
    if not artifact_dir_raw:
        _emit(
            {
                "event": "error",
                "type": "InvalidInput",
                "message": "options['artifact_dir'] is required",
            }
        )
        return 3
    artifact_dir = Path(str(artifact_dir_raw))
    options = _resolve_path_kwargs(options)

    memory_cap_mb = payload.get("memory_cap_mb")
    if memory_cap_mb is not None:
        try:
            memory_cap_mb = float(memory_cap_mb)
        except (TypeError, ValueError):
            memory_cap_mb = None
    options.setdefault("memory_cap_mb", memory_cap_mb)

    # Lazy import after stdin parsing so a bad payload exits before spinning
    # up heavy ezdxf/PIL modules.
    from src.services.comparison.runtime_budget import (
        MemoryBudgetExceeded,
        RuntimeBudgetSampler,
    )
    from src.services.comparison.viewer_package import export_viewer_package

    sampler = RuntimeBudgetSampler(spool_dirs=[artifact_dir])
    sampler.start_sampling()

    # Background reporter so the parent can render a live spinner even when
    # the worker is in a tight loop.
    stop_event = threading.Event()

    def _heartbeat() -> None:
        while not stop_event.wait(0.5):
            peek = sampler.peek_working_set_mb()
            _emit(
                {
                    "event": "memory_sample",
                    "peak_working_set_mb": peek,
                    "elapsed_s": round(time.perf_counter() - started_at, 3),
                }
            )

    heartbeat_thread = threading.Thread(target=_heartbeat, name="hb", daemon=True)
    heartbeat_thread.start()

    try:
        options["runtime_sampler"] = sampler
        viewer_package = export_viewer_package(artifact_dir, **options)
        _emit(
            {
                "event": "result",
                "viewer_package": viewer_package.to_dict(),
                "elapsed_s": round(time.perf_counter() - started_at, 3),
            }
        )
        return 0
    except MemoryBudgetExceeded as exc:
        _emit(
            {
                "event": "error",
                "type": "MemoryBudgetExceeded",
                "stage": exc.stage,
                "current_mb": exc.current_mb,
                "max_mb": exc.max_mb,
                "elapsed_s": round(time.perf_counter() - started_at, 3),
            }
        )
        return 2
    except Exception as exc:  # noqa: BLE001 — final boundary, must report
        _emit(
            {
                "event": "error",
                "type": type(exc).__name__,
                "message": str(exc),
                "elapsed_s": round(time.perf_counter() - started_at, 3),
            }
        )
        return 1
    finally:
        stop_event.set()
        try:
            sampler.stop()
        except Exception:
            pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
