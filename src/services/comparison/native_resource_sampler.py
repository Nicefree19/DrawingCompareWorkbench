# -*- coding: utf-8 -*-
"""Platform-aware native resource sampling for comparison performance gates."""

from __future__ import annotations

import ctypes
import platform
from typing import Any, Iterable

NATIVE_RESOURCE_SCHEMA_VERSION = 1

WORKER_PROCESS_TOKENS: tuple[str, ...] = (
    "--drawing-compare-zone-vector-worker",
    "--drawing-compare-viewer-render-worker",
    "--drawing-compare-viewer-package-worker",
    "--drawing-compare-cad-visual-conversion-worker",
)


def native_resource_snapshot(
    *,
    proc: Any = None,
    include_process_memory: bool = True,
    include_process_resources: bool = True,
    include_worker_processes: bool = True,
    worker_tokens: Iterable[str] = WORKER_PROCESS_TOKENS,
) -> dict[str, Any]:
    """Return a best-effort native resource snapshot.

    Unsupported metrics stay ``None``. Errors are recorded in
    ``native_resource_notes`` so callers can distinguish "not supported" from
    "not attempted" without parsing exceptions.
    """

    notes: list[str] = []
    payload: dict[str, Any] = {
        "native_resource_schema_version": NATIVE_RESOURCE_SCHEMA_VERSION,
        "native_resource_platform": platform.system().lower(),
        "rss_mb": None,
        "process_handle_count": None,
        "open_file_descriptor_count": None,
        "gdi_handle_count": None,
        "user_handle_count": None,
        "worker_process_count": None,
        "worker_processes": [],
        "worker_process_measurement_available": False,
        "native_resource_available": False,
        "native_resource_notes": notes,
    }

    process = proc
    if process is None:
        try:
            import psutil  # type: ignore[import-not-found]

            process = psutil.Process()
        except Exception as exc:
            notes.append(f"psutil_unavailable:{type(exc).__name__}")
            process = None

    if process is not None:
        if include_process_memory:
            _capture_process_memory(process, payload, notes)
        if include_process_resources:
            _capture_process_handles(process, payload, notes)
        if include_worker_processes:
            worker = worker_process_snapshot(proc=process, tokens=worker_tokens)
            payload["worker_process_count"] = worker["worker_process_count"]
            payload["worker_processes"] = worker["worker_processes"]
            payload["worker_process_measurement_available"] = worker[
                "worker_process_measurement_available"
            ]
            notes.extend(worker.get("native_resource_notes") or [])
    elif include_worker_processes:
        notes.append("worker_process_count_unavailable:no_process")

    if include_process_resources and payload["native_resource_platform"] == "windows":
        _capture_windows_gui_handles(payload, notes)

    payload["native_resource_available"] = any(
        payload.get(key) is not None
        for key in (
            "process_handle_count",
            "open_file_descriptor_count",
            "gdi_handle_count",
            "user_handle_count",
        )
    )
    payload["native_resource_notes"] = _dedupe_notes(notes)
    return payload


def worker_process_snapshot(
    *,
    proc: Any = None,
    tokens: Iterable[str] = WORKER_PROCESS_TOKENS,
) -> dict[str, Any]:
    notes: list[str] = []
    payload: dict[str, Any] = {
        "worker_process_count": None,
        "worker_processes": [],
        "worker_process_measurement_available": False,
        "native_resource_notes": notes,
    }
    process = proc
    if process is None:
        try:
            import psutil  # type: ignore[import-not-found]

            process = psutil.Process()
        except Exception as exc:
            notes.append(f"worker_process_psutil_unavailable:{type(exc).__name__}")
            return payload

    token_tuple = tuple(str(token) for token in tokens)
    workers: list[dict[str, Any]] = []
    try:
        children = process.children(recursive=True)
    except Exception as exc:
        notes.append(f"worker_process_children_failed:{type(exc).__name__}")
        return payload

    for child in children:
        try:
            cmdline = child.cmdline()
        except Exception:
            cmdline = []
        cmd = " ".join(str(part) for part in cmdline)
        matched = [token for token in token_tuple if token in cmd]
        if not matched:
            continue
        try:
            pid = int(child.pid)
        except Exception:
            pid = None
        try:
            name = str(child.name())
        except Exception:
            name = ""
        workers.append({"pid": pid, "name": name, "matched_tokens": matched})

    payload["worker_process_count"] = len(workers)
    payload["worker_processes"] = workers
    payload["worker_process_measurement_available"] = True
    payload["native_resource_notes"] = _dedupe_notes(notes)
    return payload


def worker_process_count(*, proc: Any = None) -> int:
    snapshot = worker_process_snapshot(proc=proc)
    value = snapshot.get("worker_process_count")
    return int(value) if isinstance(value, int) else 0


def _capture_process_memory(
    proc: Any,
    payload: dict[str, Any],
    notes: list[str],
) -> None:
    try:
        info = proc.memory_info()
    except Exception as exc:
        notes.append(f"memory_info_failed:{type(exc).__name__}")
        return
    rss = int(getattr(info, "rss", 0) or 0)
    if rss > 0:
        payload["rss_mb"] = round(rss / (1024 * 1024), 3)


def _capture_process_handles(
    proc: Any,
    payload: dict[str, Any],
    notes: list[str],
) -> None:
    if hasattr(proc, "num_handles"):
        try:
            payload["process_handle_count"] = int(proc.num_handles())
        except Exception as exc:  # pragma: no cover - depends on OS/process
            notes.append(f"process_handle_count_failed:{type(exc).__name__}")
    if hasattr(proc, "num_fds"):
        try:
            payload["open_file_descriptor_count"] = int(proc.num_fds())
        except Exception as exc:  # pragma: no cover - depends on OS/process
            notes.append(f"open_file_descriptor_count_failed:{type(exc).__name__}")


def _capture_windows_gui_handles(
    payload: dict[str, Any],
    notes: list[str],
) -> None:
    try:
        current = ctypes.windll.kernel32.GetCurrentProcess()
        payload["gdi_handle_count"] = int(
            ctypes.windll.user32.GetGuiResources(current, 0)
        )
        payload["user_handle_count"] = int(
            ctypes.windll.user32.GetGuiResources(current, 1)
        )
    except Exception as exc:  # pragma: no cover - only meaningful on Windows
        notes.append(f"windows_gui_handle_count_failed:{type(exc).__name__}")


def _dedupe_notes(notes: Iterable[Any]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for note in notes:
        text = str(note)
        if text and text not in seen:
            deduped.append(text)
            seen.add(text)
    return deduped


__all__ = [
    "NATIVE_RESOURCE_SCHEMA_VERSION",
    "WORKER_PROCESS_TOKENS",
    "native_resource_snapshot",
    "worker_process_count",
    "worker_process_snapshot",
]
