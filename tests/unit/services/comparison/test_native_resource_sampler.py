# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from src.services.comparison import native_resource_sampler as sampler


class _Proc:
    def __init__(self, *, children=None):
        self.pid = 100
        self._children = children or []

    def memory_info(self):
        return SimpleNamespace(rss=2 * 1024 * 1024)

    def num_handles(self):
        return 42

    def num_fds(self):
        return 7

    def children(self, recursive=True):
        return list(self._children)


class _Child:
    pid = 200

    def __init__(self, cmdline):
        self._cmdline = cmdline

    def cmdline(self):
        return list(self._cmdline)

    def name(self):
        return "python"


def test_native_resource_snapshot_captures_process_counts(monkeypatch):
    monkeypatch.setattr(sampler.platform, "system", lambda: "Linux")

    payload = sampler.native_resource_snapshot(proc=_Proc(), include_worker_processes=False)

    assert payload["native_resource_schema_version"] == 1
    assert payload["native_resource_available"] is True
    assert payload["rss_mb"] == 2.0
    assert payload["process_handle_count"] == 42
    assert payload["open_file_descriptor_count"] == 7
    assert payload["gdi_handle_count"] is None
    assert payload["user_handle_count"] is None


def test_worker_process_snapshot_counts_only_drawing_compare_workers():
    proc = _Proc(
        children=[
            _Child(["python", "--drawing-compare-zone-vector-worker"]),
            _Child(["python", "ordinary-script.py"]),
            _Child(["python", "--drawing-compare-cad-visual-conversion-worker"]),
        ]
    )

    payload = sampler.worker_process_snapshot(proc=proc)

    assert payload["worker_process_measurement_available"] is True
    assert payload["worker_process_count"] == 2
    assert len(payload["worker_processes"]) == 2


def test_worker_process_snapshot_records_failures_as_notes():
    class FailingProc:
        def children(self, recursive=True):
            raise RuntimeError("blocked")

    payload = sampler.worker_process_snapshot(proc=FailingProc())

    assert payload["worker_process_count"] is None
    assert payload["worker_process_measurement_available"] is False
    assert payload["native_resource_notes"] == ["worker_process_children_failed:RuntimeError"]
