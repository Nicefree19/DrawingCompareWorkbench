# -*- coding: utf-8 -*-
"""Tests for viewer_package_proxy.export_viewer_package_isolated.

Audit-gates §10.5 Phase A — verify the subprocess wrapper streams progress,
forwards results, surfaces MemoryBudgetExceeded distinctly, and handles
fallback to in-process when allowed.

Strategy: substitute ``subprocess.Popen`` via monkeypatch so tests run in
~50ms instead of spawning real processes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from src.services.comparison.viewer_package_proxy import (
    DEFAULT_TIMEOUT_S,
    SubprocessRunReport,
    export_viewer_package_isolated,
)
from src.services.comparison.workbench_subprocess import VIEWER_PACKAGE_WORKER_FLAG


class _FakePopen:
    """Minimal ``subprocess.Popen`` stand-in that emits scripted JSONL events."""

    def __init__(self, jsonl_lines: list[str], exit_code: int = 0) -> None:
        self._lines = list(jsonl_lines)
        self._exit_code = exit_code
        self.stdin = MagicMock()
        self.stderr = MagicMock()
        self.stdout = iter([line + "\n" for line in self._lines])
        self.returncode: Optional[int] = None
        self.kill_called = False

    def wait(self, timeout: Optional[float] = None) -> int:
        self.returncode = self._exit_code
        return self._exit_code

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9


def _result_jsonl(viewer_package: dict[str, Any]) -> list[str]:
    return [
        json.dumps({"event": "started", "schema_version": 1}),
        json.dumps({"event": "memory_sample", "peak_working_set_mb": 1024.0}),
        json.dumps({"event": "memory_sample", "peak_working_set_mb": 2048.0}),
        json.dumps({"event": "result", "viewer_package": viewer_package}),
    ]


def _memory_cap_jsonl() -> list[str]:
    return [
        json.dumps({"event": "started", "schema_version": 1}),
        json.dumps({"event": "memory_sample", "peak_working_set_mb": 4500.0}),
        json.dumps(
            {
                "event": "error",
                "type": "MemoryBudgetExceeded",
                "stage": "viewer_package.pair_loop",
                "current_mb": 4500.0,
                "max_mb": 4096.0,
            }
        ),
    ]


def _generic_error_jsonl() -> list[str]:
    return [
        json.dumps({"event": "started", "schema_version": 1}),
        json.dumps({"event": "error", "type": "RuntimeError", "message": "boom"}),
    ]


@pytest.fixture
def fake_artifact_dir(tmp_path: Path) -> Path:
    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    return artifact


class TestSuccessPath:
    def test_returns_viewer_package_dict_on_success(self, fake_artifact_dir):
        viewer_package_payload = {
            "viewer_dir": "/tmp/viewer",
            "pair_count": 3,
            "tile_count": 100,
        }
        fake = _FakePopen(_result_jsonl(viewer_package_payload), exit_code=0)
        with patch(
            "src.services.comparison.viewer_package_proxy.subprocess.Popen",
            return_value=fake,
        ):
            result, report = export_viewer_package_isolated(
                fake_artifact_dir,
                options={"viewer_mode": "image-tiles"},
                memory_cap_mb=4096.0,
            )
        assert result == viewer_package_payload
        assert report.exit_code == 0
        assert report.error_type is None
        assert report.last_memory_sample_mb == 2048.0
        assert report.progress_event_count == 4

    def test_development_subprocess_uses_module_entrypoint(self, fake_artifact_dir):
        viewer_package_payload = {"viewer_dir": "/tmp/viewer", "pair_count": 1}
        fake = _FakePopen(_result_jsonl(viewer_package_payload), exit_code=0)
        captured: dict[str, Any] = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return fake

        with patch(
            "src.services.comparison.viewer_package_proxy.subprocess.Popen",
            side_effect=fake_popen,
        ):
            result, report = export_viewer_package_isolated(
                fake_artifact_dir,
                options={},
                python_executable="python.exe",
            )

        assert result == viewer_package_payload
        assert report.exit_code == 0
        assert captured["cmd"] == [
            "python.exe",
            "-m",
            "scripts.render_viewer_package_subprocess",
        ]

    def test_frozen_subprocess_uses_internal_worker_flag(
        self, fake_artifact_dir, monkeypatch
    ):
        viewer_package_payload = {"viewer_dir": "/tmp/viewer", "pair_count": 1}
        fake = _FakePopen(_result_jsonl(viewer_package_payload), exit_code=0)
        captured: dict[str, Any] = {}
        monkeypatch.setattr("sys.frozen", True, raising=False)

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return fake

        with patch(
            "src.services.comparison.viewer_package_proxy.subprocess.Popen",
            side_effect=fake_popen,
        ):
            result, report = export_viewer_package_isolated(
                fake_artifact_dir,
                options={},
                python_executable=r"C:\Program Files\DrawingCompareWorkbench\DrawingCompareWorkbench.exe",
            )

        assert result == viewer_package_payload
        assert report.exit_code == 0
        assert captured["cmd"] == [
            r"C:\Program Files\DrawingCompareWorkbench\DrawingCompareWorkbench.exe",
            VIEWER_PACKAGE_WORKER_FLAG,
        ]

    def test_progress_callback_receives_events(self, fake_artifact_dir):
        viewer_package_payload = {"viewer_dir": "/tmp/v", "pair_count": 1}
        fake = _FakePopen(_result_jsonl(viewer_package_payload), exit_code=0)
        observed: list[dict[str, Any]] = []
        with patch(
            "src.services.comparison.viewer_package_proxy.subprocess.Popen",
            return_value=fake,
        ):
            export_viewer_package_isolated(
                fake_artifact_dir,
                options={},
                progress_callback=lambda ev: observed.append(ev),
            )
        event_types = [ev.get("event") for ev in observed]
        assert "started" in event_types
        assert event_types.count("memory_sample") == 2
        assert "result" in event_types


class TestMemoryBudgetExceeded:
    def test_memory_cap_does_not_fall_back(self, fake_artifact_dir):
        fake = _FakePopen(_memory_cap_jsonl(), exit_code=2)
        with patch(
            "src.services.comparison.viewer_package_proxy.subprocess.Popen",
            return_value=fake,
        ):
            result, report = export_viewer_package_isolated(
                fake_artifact_dir,
                options={},
                memory_cap_mb=4096.0,
                allow_inprocess_fallback=True,  # explicitly allow but we should ignore
            )
        assert result is None
        assert report.error_type == "MemoryBudgetExceeded"
        assert report.error_stage == "viewer_package.pair_loop"
        assert report.error_current_mb == 4500.0
        assert report.error_max_mb == 4096.0
        assert report.fallback_used is False  # MUST NOT fall back


class TestGenericError:
    def test_generic_error_no_fallback_when_disabled(self, fake_artifact_dir):
        fake = _FakePopen(_generic_error_jsonl(), exit_code=1)
        with patch(
            "src.services.comparison.viewer_package_proxy.subprocess.Popen",
            return_value=fake,
        ):
            result, report = export_viewer_package_isolated(
                fake_artifact_dir,
                options={},
                allow_inprocess_fallback=False,
            )
        assert result is None
        assert report.error_type == "RuntimeError"
        assert report.error_message == "boom"
        assert report.fallback_used is False

    def test_fallback_runs_inprocess_when_enabled(self, fake_artifact_dir):
        fake = _FakePopen(_generic_error_jsonl(), exit_code=1)
        # Also patch the in-process fallback so we don't actually run it.
        fake_viewer_package = MagicMock()
        fake_viewer_package.to_dict.return_value = {
            "viewer_dir": "/tmp/inproc",
            "pair_count": 0,
        }
        with patch(
            "src.services.comparison.viewer_package_proxy.subprocess.Popen",
            return_value=fake,
        ), patch(
            "src.services.comparison.viewer_package.export_viewer_package",
            return_value=fake_viewer_package,
        ):
            result, report = export_viewer_package_isolated(
                fake_artifact_dir,
                options={"viewer_mode": "image-tiles"},
                allow_inprocess_fallback=True,
            )
        assert result == {"viewer_dir": "/tmp/inproc", "pair_count": 0}
        assert report.fallback_used is True


class TestSubprocessLaunchFailure:
    def test_script_missing_yields_clean_report(self, fake_artifact_dir, monkeypatch):
        # Make the script lookup point to a non-existent path
        monkeypatch.setattr(
            "src.services.comparison.viewer_package_proxy._resolve_repo_root",
            lambda: Path("/no/such/place"),
        )
        result, report = export_viewer_package_isolated(
            fake_artifact_dir, options={}, memory_cap_mb=4096.0
        )
        assert result is None
        assert report.error_type == "ScriptMissing"
        assert report.exit_code == -1

    def test_popen_exception_recorded(self, fake_artifact_dir):
        with patch(
            "src.services.comparison.viewer_package_proxy.subprocess.Popen",
            side_effect=PermissionError("denied"),
        ):
            result, report = export_viewer_package_isolated(
                fake_artifact_dir, options={}, memory_cap_mb=4096.0
            )
        assert result is None
        assert report.error_type == "SubprocessLaunchFailed"
        assert "denied" in str(report.error_message)


class TestPayloadSerialization:
    def test_path_objects_serialised_to_strings(self, fake_artifact_dir, tmp_path):
        captured_stdin: list[str] = []
        fake = _FakePopen(
            _result_jsonl({"viewer_dir": "/tmp/v", "pair_count": 1}),
            exit_code=0,
        )

        def capture_write(value):
            captured_stdin.append(value)

        fake.stdin.write = capture_write
        # Use tmp_path so the test is OS-agnostic (Path("/tmp/...") on Windows
        # resolves with backslashes, defeating literal string equality).
        viewer_dir = tmp_path / "explicit_viewer"
        dxf_cache_dir = tmp_path / "dxf"
        with patch(
            "src.services.comparison.viewer_package_proxy.subprocess.Popen",
            return_value=fake,
        ):
            export_viewer_package_isolated(
                fake_artifact_dir,
                options={
                    "viewer_dir": viewer_dir,
                    "dxf_cache_dir": dxf_cache_dir,
                },
                memory_cap_mb=4096.0,
            )
        assert captured_stdin
        payload = json.loads(captured_stdin[0])
        assert payload["options"]["viewer_dir"] == str(viewer_dir)
        assert payload["options"]["dxf_cache_dir"] == str(dxf_cache_dir)
        assert payload["memory_cap_mb"] == 4096.0


class TestSubprocessRunReportSerialization:
    def test_to_dict_round_trips_all_fields(self):
        report = SubprocessRunReport(
            exit_code=0,
            elapsed_s=12.345,
            last_memory_sample_mb=2048.0,
            error_type=None,
            progress_event_count=10,
        )
        payload = report.to_dict()
        assert payload["exit_code"] == 0
        assert payload["elapsed_s"] == 12.345
        assert payload["last_memory_sample_mb"] == 2048.0
        assert payload["progress_event_count"] == 10
        assert payload["fallback_used"] is False
        assert payload["notes"] == []


class TestDefaults:
    def test_default_timeout_is_30_minutes(self):
        assert DEFAULT_TIMEOUT_S == 1800.0


class TestSubprocessInterpreterFallbackRemoved:
    """Plan §19 A-5 (Agent T finding T2) — the unversioned ``"python"``
    fallback was a PATH-hijack vector. The hardened code raises
    RuntimeError instead, so the caller has to supply a verified
    interpreter path.
    """

    def test_raises_when_interpreter_and_sys_executable_both_falsy(
        self, tmp_path, monkeypatch
    ) -> None:
        # Stage a minimal artifact dir so we get past the script-missing
        # guard (line 130 of viewer_package_proxy.py).
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        # Force sys.executable to empty so the fallback decision triggers.
        monkeypatch.setattr("sys.executable", "")
        with pytest.raises(RuntimeError, match="no Python interpreter available"):
            export_viewer_package_isolated(
                artifact_dir,
                options={"viewer_dir": str(tmp_path / "viewer")},
                memory_cap_mb=1024.0,
                python_executable=None,
            )
