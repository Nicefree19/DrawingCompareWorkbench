#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Environment gate for the limited-customer Windows release."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent.parent
if not (PROJECT_ROOT / "scripts" / "cli_converter.py").exists():
    fallback_root = SCRIPT_PATH.parents[2]
    if (fallback_root / "scripts" / "cli_converter.py").exists():
        PROJECT_ROOT = fallback_root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import_status(module_name: str) -> Dict[str, Any]:
    try:
        __import__(module_name)
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "error": str(exc)}
    return {"available": True}


def _check_write_access(path: Path) -> Dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".release-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"writable": True, "path": str(path)}
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"writable": False, "path": str(path), "error": str(exc)}


def _check_vc_runtime() -> Dict[str, Any]:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = [
        windir / "System32" / "vcruntime140.dll",
        windir / "System32" / "vcruntime140_1.dll",
    ]
    existing = [str(path) for path in candidates if path.exists()]
    return {"available": bool(existing), "files": existing}


def collect_environment_report() -> Dict[str, Any]:
    from scripts.cli_converter import ConverterAPI

    temp_root = Path(tempfile.gettempdir()) / "conversion_workbench_release_gate"
    runtime_modules = {
        "PySide6": _import_status("PySide6"),
        "pandas": _import_status("pandas"),
        "numpy": _import_status("numpy"),
        "openpyxl": _import_status("openpyxl"),
        "fitz": _import_status("fitz"),
        "ezdxf": _import_status("ezdxf"),
        "cv2": _import_status("cv2"),
    }
    tekla_status = ConverterAPI().diagnose_tekla(force=True)
    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "is_frozen": bool(getattr(sys, "frozen", False)),
        },
        "write_access": _check_write_access(temp_root),
        "vc_runtime": _check_vc_runtime(),
        "runtime_modules": runtime_modules,
        "tekla": tekla_status,
    }


def _console_summary(report: Dict[str, Any]) -> str:
    tekla = report.get("tekla", {})
    status = tekla.get("status", {})
    module_summary = ", ".join(
        f"{name}={'OK' if info.get('available') else 'FAIL'}"
        for name, info in report.get("runtime_modules", {}).items()
    )
    return "\n".join(
        [
            "Conversion Workbench Release Environment Check",
            f"Platform: {report['platform']['system']} {report['platform']['release']} ({report['platform']['machine']})",
            f"Python: {report['platform']['python_version']} | frozen={report['platform']['is_frozen']}",
            f"Write access: {'OK' if report['write_access']['writable'] else 'FAIL'} -> {report['write_access']['path']}",
            f"VC runtime: {'OK' if report['vc_runtime']['available'] else 'MISSING'}",
            f"Modules: {module_summary}",
            f"Tekla: api={status.get('api_available')} running={status.get('structures_running')} ready={tekla.get('connection_ready')}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the limited-release environment gate.")
    parser.add_argument("--json-output", help="Optional JSON output path")
    args = parser.parse_args()

    report = collect_environment_report()
    print(_console_summary(report))
    if args.json_output:
        output_path = Path(args.json_output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON report written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
