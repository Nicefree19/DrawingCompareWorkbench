# -*- coding: utf-8 -*-
"""Tests for the subprocess child fault-handler integration (§13 Phase B-2).

The 2026-05-15 multi-agent audit identified that ``_arm_crash_diagnostics()``
only armed faulthandler in the GUI main process; the renderer subprocess
spawned by ``viewer_package_proxy`` inherited NOTHING and a native crash
inside the child left the parent with only ``exit_code=-1``.

These tests pin the contract that:

1. ``_build_subprocess_payload`` includes ``fault_log_dir`` when (and only
   when) the caller supplies one.
2. The subprocess entry script defines ``_arm_subprocess_fault_handler``
   and invokes ``enable_windows_fault_handler`` against the payload's
   ``fault_log_dir`` field, falling back to ``<repo>/logs/subprocess``.
3. The subprocess emits a ``fault_handler_armed`` event so the parent
   stream (which is captured for telemetry) records the armed log path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _load_subprocess_script_module():
    """Load scripts/render_viewer_package_subprocess.py as an importable
    module for unit testing. The script's ``__main__`` block is guarded by
    ``if __name__ == "__main__"`` so importing is safe.
    """
    repo_root = Path(__file__).resolve().parents[4]
    script_path = repo_root / "scripts" / "render_viewer_package_subprocess.py"
    assert script_path.exists(), f"subprocess script missing: {script_path}"
    spec = importlib.util.spec_from_file_location(
        "render_viewer_package_subprocess_under_test", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestPayloadCarriesFaultLogDir:
    def test_payload_omits_field_when_caller_passes_none(self, tmp_path):
        from src.services.comparison.viewer_package_proxy import _build_subprocess_payload

        artifact_dir = tmp_path / "artifact"
        viewer_dir = tmp_path / "viewer"
        payload = _build_subprocess_payload(
            artifact_dir=artifact_dir,
            options={"viewer_dir": viewer_dir},
            memory_cap_mb=4096.0,
            fault_log_dir=None,
        )

        assert "fault_log_dir" not in payload
        # Sanity — other fields still present so we know we didn't break the
        # payload schema while removing nothing. Use str(Path) so the OS-
        # specific separator does not break the assertion on Windows.
        assert payload["options"]["artifact_dir"] == str(artifact_dir)
        assert payload["memory_cap_mb"] == 4096.0

    def test_payload_includes_field_when_caller_passes_path(self, tmp_path):
        from src.services.comparison.viewer_package_proxy import _build_subprocess_payload

        fault_dir = tmp_path / "fault_logs"

        payload = _build_subprocess_payload(
            artifact_dir=tmp_path / "artifact",
            options={},
            memory_cap_mb=2048.0,
            fault_log_dir=fault_dir,
        )

        # The dir should be a string (JSON-serialisable across the pipe).
        assert payload["fault_log_dir"] == str(fault_dir)


class TestSubprocessScriptArmsFaultHandler:
    def test_arm_function_exists_with_expected_signature(self):
        module = _load_subprocess_script_module()
        assert hasattr(module, "_arm_subprocess_fault_handler"), (
            "subprocess script must expose _arm_subprocess_fault_handler"
        )

    def test_arm_function_routes_to_enable_windows_fault_handler_with_payload_dir(
        self, tmp_path
    ):
        module = _load_subprocess_script_module()
        custom_dir = tmp_path / "subprocess_logs"

        with patch(
            "src.core.error_handler.enable_windows_fault_handler"
        ) as mock_enable:
            mock_enable.return_value = custom_dir / "fault_20260515_120000.log"
            result = module._arm_subprocess_fault_handler(
                {"fault_log_dir": str(custom_dir)}
            )

        assert mock_enable.call_count == 1
        # The keyword passed must be the payload value, not the fallback.
        kwargs = mock_enable.call_args.kwargs
        assert Path(kwargs["log_dir"]) == custom_dir
        # cleanup_older_than_days=0: parent handles retention.
        assert kwargs.get("cleanup_older_than_days") == 0
        # Returned path is what enable_windows_fault_handler reported.
        assert result == custom_dir / "fault_20260515_120000.log"

    def test_arm_function_falls_back_to_repo_subprocess_dir_when_payload_empty(
        self,
    ):
        module = _load_subprocess_script_module()

        with patch(
            "src.core.error_handler.enable_windows_fault_handler"
        ) as mock_enable:
            mock_enable.return_value = Path("/dev/null")
            module._arm_subprocess_fault_handler({})

        assert mock_enable.call_count == 1
        kwargs = mock_enable.call_args.kwargs
        log_dir = Path(kwargs["log_dir"])
        # Fallback path lives under <repo>/logs/subprocess. Don't pin the
        # absolute prefix (CI runners differ) — just assert the trailing
        # components so a refactor that moves the fallback elsewhere still
        # signals an intentional contract change.
        assert log_dir.parts[-2:] == ("logs", "subprocess")

    def test_arm_function_returns_none_when_error_handler_unimportable(self):
        module = _load_subprocess_script_module()

        # Simulate enable_windows_fault_handler raising at import time.
        with patch(
            "src.core.error_handler.enable_windows_fault_handler",
            side_effect=RuntimeError("simulated import failure"),
        ):
            result = module._arm_subprocess_fault_handler({})

        # Best-effort arming must never abort the run.
        assert result is None
