# -*- coding: utf-8 -*-
"""Tests for optional CAD visual conversion backend contracts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from src.services.comparison.cad_visual_backend import (
    CAD_VISUAL_BACKEND_DISABLED,
    CAD_VISUAL_CONVERSION_CANCELLED,
    CAD_VISUAL_TIMEOUT,
    CadVisualBackend,
    CadVisualBackendCapabilities,
    CadVisualConversionRequest,
    CadVisualConversionResult,
)
from src.services.comparison.cad_visual_conversion_worker import (
    convert_cad_visual_in_subprocess,
    run_conversion_request,
)
from src.services.comparison.render_backend_registry import (
    DEFAULT_DISABLED_BACKEND_ID,
    RenderBackendRegistry,
    get_default_render_backend_registry,
)
from src.services.comparison.workbench_subprocess import (
    CAD_VISUAL_CONVERSION_WORKER_MODULE,
    worker_command_for_module,
)


class _FakePdfBackend(CadVisualBackend):
    @property
    def capabilities(self) -> CadVisualBackendCapabilities:
        return CadVisualBackendCapabilities(
            backend_id="fake_pdf",
            backend_version="1.2.3",
            license_id="test_license",
            can_convert_to_pdf=True,
            enabled_by_default=False,
        )

    def convert_to_pdf(self, request: CadVisualConversionRequest) -> CadVisualConversionResult:
        output = request.output_dir / "converted.pdf"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-1.4\n")
        caps = self.capabilities
        return CadVisualConversionResult(
            status="converted",
            reason_code="",
            source_path=str(request.source_path),
            output_path=str(output),
            output_format="pdf",
            backend_id=caps.backend_id,
            backend_version=caps.backend_version,
            license_id=caps.license_id,
        )


def test_default_registry_keeps_cad_visual_conversion_disabled(tmp_path: Path) -> None:
    request = CadVisualConversionRequest(
        source_path=tmp_path / "a.dwg",
        output_dir=tmp_path / "out",
        output_format="pdf",
    )

    result = get_default_render_backend_registry().convert_cad_visual(request)

    assert result.status == "skipped"
    assert result.reason_code == CAD_VISUAL_BACKEND_DISABLED
    assert result.backend_id == DEFAULT_DISABLED_BACKEND_ID
    assert result.license_id == "none"
    assert result.output_path == ""


def test_registry_serializes_backend_manifest_fields(tmp_path: Path) -> None:
    request = CadVisualConversionRequest(
        source_path=tmp_path / "a.dxf",
        output_dir=tmp_path / "out",
        output_format="pdf",
        backend_id="fake_pdf",
        pair_id="P-001",
        side="after",
    )
    registry = RenderBackendRegistry()
    registry.register(_FakePdfBackend())

    result = registry.convert_cad_visual(request, allow_env=False)
    payload = result.to_dict()

    assert payload["ok"] is True
    assert payload["backend_id"] == "fake_pdf"
    assert payload["backend_version"] == "1.2.3"
    assert payload["license_id"] == "test_license"
    assert Path(payload["output_path"]).exists()
    assert CadVisualConversionResult.from_dict(payload).ok is True


def test_conversion_worker_default_request_returns_structured_skip(tmp_path: Path) -> None:
    request = CadVisualConversionRequest(
        source_path=tmp_path / "a.dwg",
        output_dir=tmp_path / "out",
        output_format="pdf",
    )

    result = run_conversion_request(request.to_dict())

    assert result.status == "skipped"
    assert result.reason_code == CAD_VISUAL_BACKEND_DISABLED
    assert result.backend_id == DEFAULT_DISABLED_BACKEND_ID
    assert result.elapsed_ms >= 0


def test_conversion_worker_ignores_env_only_backend_selection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DRAWING_COMPARE_CAD_VISUAL_BACKEND", "qcad_professional_cli")
    request = CadVisualConversionRequest(
        source_path=tmp_path / "a.dwg",
        output_dir=tmp_path / "out",
        output_format="pdf",
    )

    result = run_conversion_request(request.to_dict())

    assert result.status == "skipped"
    assert result.reason_code == CAD_VISUAL_BACKEND_DISABLED
    assert result.backend_id == DEFAULT_DISABLED_BACKEND_ID


def test_subprocess_timeout_returns_structured_reason(tmp_path: Path, monkeypatch) -> None:
    class _TimeoutPopen:
        returncode = None

        def __init__(self, *args, **kwargs) -> None:
            self.killed = False

        def communicate(self, input=None, timeout=None):  # noqa: ANN001
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return "", "after kill"

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    monkeypatch.setattr(
        "src.services.comparison.cad_visual_conversion_worker.subprocess.Popen",
        _TimeoutPopen,
    )
    request = CadVisualConversionRequest(
        source_path=tmp_path / "slow.dwg",
        output_dir=tmp_path / "out",
        output_format="pdf",
        backend_id="fake_slow",
    )

    result = convert_cad_visual_in_subprocess(request, timeout_s=0.02)

    assert result.status == "failed"
    assert result.reason_code == CAD_VISUAL_TIMEOUT
    assert result.backend_id == "fake_slow"
    assert "timed out" in result.warnings[0]


def test_subprocess_running_cancel_kills_worker(tmp_path: Path, monkeypatch) -> None:
    class _SlowPopen:
        returncode = None

        def __init__(self, *args, **kwargs) -> None:
            self.killed = False

        def communicate(self, input=None, timeout=None):  # noqa: ANN001
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return "", "cancelled"

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    monkeypatch.setattr(
        "src.services.comparison.cad_visual_conversion_worker.subprocess.Popen",
        _SlowPopen,
    )
    request = CadVisualConversionRequest(
        source_path=tmp_path / "slow.dwg",
        output_dir=tmp_path / "out",
        output_format="pdf",
        backend_id="fake_slow",
    )
    calls = {"count": 0}

    def should_cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 1

    result = convert_cad_visual_in_subprocess(
        request,
        timeout_s=10.0,
        cancel_callback=should_cancel,
    )

    assert result.status == "cancelled"
    assert result.reason_code == CAD_VISUAL_CONVERSION_CANCELLED
    assert result.backend_id == "fake_slow"


def test_worker_command_registers_packaged_cad_visual_flag() -> None:
    program, args = worker_command_for_module(
        CAD_VISUAL_CONVERSION_WORKER_MODULE,
        executable="DrawingCompareWorkbench.exe",
        frozen=True,
    )

    assert program == "DrawingCompareWorkbench.exe"
    assert args == ["--drawing-compare-cad-visual-conversion-worker"]


def test_worker_stdout_result_payload_round_trips() -> None:
    result = CadVisualConversionResult(
        status="skipped",
        reason_code=CAD_VISUAL_BACKEND_DISABLED,
        source_path="a.dwg",
        backend_id="disabled",
        license_id="none",
    )
    line = json.dumps({"event": "result", "result": result.to_dict()})

    parsed = CadVisualConversionResult.from_dict(json.loads(line)["result"])

    assert parsed.reason_code == CAD_VISUAL_BACKEND_DISABLED
    assert parsed.license_id == "none"
