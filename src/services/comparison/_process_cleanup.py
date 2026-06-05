"""Windows process enumeration and termination helpers shared by the DWG bridge
and its adapter.

Leaf module: depends only on the standard library so it can be imported both as
``src.services.comparison._process_cleanup`` (library/app context) and, when the
bridge runs as a standalone subprocess with neither ``src`` nor ``tools`` on the
path, by absolute file location via importlib. Keep it dependency-free.
"""
from __future__ import annotations

import csv
import ctypes
import os
import subprocess
from pathlib import Path

__all__ = [
    "process_ids_for_image",
    "kill_process_tree",
    "terminate_process",
]


def process_ids_for_image(image_name: str) -> set[int]:
    """PIDs of running processes whose image is ``image_name`` (e.g. ``ZWCAD.exe``).
    Uses the Toolhelp snapshot when available and falls back to ``tasklist``.
    Returns an empty set off Windows or on any enumeration failure."""
    if os.name != "nt":
        return set()
    native = _process_ids_for_image_toolhelp(image_name)
    if native is not None:
        return native
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if completed.returncode != 0:
        return set()
    expected = image_name.lower()
    pids: set[int] = set()
    for row in csv.reader(line for line in completed.stdout.splitlines() if line.strip()):
        if len(row) < 2:
            continue
        if row[0].strip('"').lower() != expected:
            continue
        try:
            pids.add(int(row[1]))
        except ValueError:
            continue
    return pids


def _process_ids_for_image_toolhelp(image_name: str) -> set[int] | None:
    if os.name != "nt":
        return set()
    expected = Path(image_name).name.casefold()
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return None

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    create_snapshot.restype = ctypes.c_void_p
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    process_first.restype = ctypes.c_int
    process_next = kernel32.Process32NextW
    process_next.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    process_next.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    snapshot = create_snapshot(0x00000002, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        return None
    pids: set[int] = set()
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        if not process_first(snapshot, ctypes.byref(entry)):
            return pids
        while True:
            if str(entry.szExeFile).casefold() == expected:
                pids.add(int(entry.th32ProcessID))
            if not process_next(snapshot, ctypes.byref(entry)):
                break
    finally:
        close_handle(snapshot)
    return pids


def kill_process_tree(pid: int) -> bool:
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return terminate_process(pid)
    return completed.returncode == 0 or terminate_process(pid)


def terminate_process(pid: int) -> bool:
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return False
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    terminate = kernel32.TerminateProcess
    terminate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    terminate.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    handle = open_process(0x0001, 0, int(pid))
    if not handle:
        return False
    try:
        return bool(terminate(handle, 1))
    finally:
        close_handle(handle)
