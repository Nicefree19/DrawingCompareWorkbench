# -*- coding: utf-8 -*-
"""Subprocess worker for optional CAD visual conversion."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .cad_visual_backend import (
    CAD_VISUAL_CONVERSION_CANCELLED,
    CAD_VISUAL_CONVERSION_FAILED,
    CAD_VISUAL_TIMEOUT,
    CadVisualConversionRequest,
    CadVisualConversionResult,
    conversion_result_from_exception,
)
from .render_backend_registry import (
    RenderBackendRegistry,
    get_default_render_backend_registry,
)
from .workbench_subprocess import CAD_VISUAL_CONVERSION_WORKER_MODULE, worker_command_for_module

DEFAULT_CAD_VISUAL_CONVERSION_TIMEOUT_S = 180.0


def run_conversion_request(
    payload: dict[str, Any],
    *,
    registry: Optional[RenderBackendRegistry] = None,
) -> CadVisualConversionResult:
    """Run one conversion request in-process and return a structured result."""

    started = time.perf_counter()
    request = CadVisualConversionRequest.from_dict(payload)
    active_registry = registry or get_default_render_backend_registry()
    try:
        result = active_registry.convert_cad_visual(request, allow_env=False)
    except Exception as exc:  # noqa: BLE001 - worker boundary must serialize all failures
        result = conversion_result_from_exception(request, exc)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return CadVisualConversionResult(
        status=result.status,
        reason_code=result.reason_code,
        source_path=result.source_path,
        output_path=result.output_path,
        output_format=result.output_format,
        backend_id=result.backend_id,
        backend_version=result.backend_version,
        license_id=result.license_id,
        elapsed_ms=result.elapsed_ms or elapsed_ms,
        warnings=list(result.warnings),
        metadata=dict(result.metadata),
    )


def convert_cad_visual_in_subprocess(
    request: CadVisualConversionRequest,
    *,
    timeout_s: float = DEFAULT_CAD_VISUAL_CONVERSION_TIMEOUT_S,
    cancel_callback: Optional[Callable[[], bool]] = None,
    python_executable: Optional[str] = None,
    cwd: Optional[Path] = None,
) -> CadVisualConversionResult:
    """Run conversion in a killable worker process."""

    if cancel_callback and cancel_callback():
        return CadVisualConversionResult(
            status="cancelled",
            reason_code=CAD_VISUAL_CONVERSION_CANCELLED,
            source_path=str(request.source_path),
            output_format=request.output_format,
            backend_id=request.backend_id,
            warnings=["CAD visual conversion was cancelled before launch."],
        )

    program, worker_args = worker_command_for_module(
        CAD_VISUAL_CONVERSION_WORKER_MODULE,
        executable=python_executable or sys.executable,
    )
    started = time.perf_counter()
    proc = subprocess.Popen(
        [program, *worker_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd or Path.cwd()),
        text=True,
        encoding="utf-8",
    )
    payload = json.dumps(request.to_dict(), ensure_ascii=False)
    deadline = started + max(0.01, float(timeout_s))
    pending_input: Optional[str] = payload
    while True:
        if cancel_callback and cancel_callback():
            proc.kill()
            stdout, stderr = _communicate_after_kill(proc)
            return CadVisualConversionResult(
                status="cancelled",
                reason_code=CAD_VISUAL_CONVERSION_CANCELLED,
                source_path=str(request.source_path),
                output_format=request.output_format,
                backend_id=request.backend_id,
                elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
                warnings=["CAD visual conversion was cancelled."],
                metadata={"stdout": str(stdout or "")[-2000:], "stderr": str(stderr or "")[-2000:]},
            )
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            proc.kill()
            stdout, stderr = _communicate_after_kill(proc)
            return CadVisualConversionResult(
                status="failed",
                reason_code=CAD_VISUAL_TIMEOUT,
                source_path=str(request.source_path),
                output_format=request.output_format,
                backend_id=request.backend_id,
                elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
                warnings=[f"CAD visual conversion timed out after {float(timeout_s):.1f}s"],
                metadata={"stdout": str(stdout or "")[-2000:], "stderr": str(stderr or "")[-2000:]},
            )
        try:
            stdout, stderr = proc.communicate(
                pending_input,
                timeout=min(0.25, max(0.01, remaining)),
            )
            break
        except subprocess.TimeoutExpired:
            pending_input = None

    result = _parse_worker_result(stdout)
    if result is not None and proc.returncode == 0:
        return result
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return CadVisualConversionResult(
        status="failed",
        reason_code=CAD_VISUAL_CONVERSION_FAILED,
        source_path=str(request.source_path),
        output_format=request.output_format,
        backend_id=request.backend_id,
        elapsed_ms=elapsed_ms,
        warnings=[f"CAD visual conversion worker failed with exit code {proc.returncode}"],
        metadata={"stdout": str(stdout or "")[-2000:], "stderr": str(stderr or "")[-2000:]},
    )


def _communicate_after_kill(proc: subprocess.Popen) -> tuple[str, str]:
    try:
        stdout, stderr = proc.communicate(timeout=5.0)
        return str(stdout or ""), str(stderr or "")
    except Exception:
        return "", ""


def _parse_worker_result(stdout: str) -> Optional[CadVisualConversionResult]:
    for line in reversed(str(stdout or "").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("event") == "result":
            return CadVisualConversionResult.from_dict(payload.get("result"))
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = run_conversion_request(payload)
        print(json.dumps({"event": "result", "result": result.to_dict()}, ensure_ascii=False), flush=True)
        return 0 if result.status in {"converted", "skipped", "cancelled"} else 1
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "event": "error",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CAD_VISUAL_CONVERSION_TIMEOUT_S",
    "convert_cad_visual_in_subprocess",
    "main",
    "run_conversion_request",
]
