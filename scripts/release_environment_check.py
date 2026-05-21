#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Environment gate for the Drawing Compare Workbench release."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import_status(module_name: str) -> dict[str, Any]:
    try:
        module = __import__(module_name)
    except Exception as exc:  # pragma: no cover - defensive/environmental
        return {"available": False, "error": str(exc)}
    version = getattr(module, "__version__", None)
    return {"available": True, "version": version}


def _check_write_access(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".release-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"writable": True, "path": str(path)}
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"writable": False, "path": str(path), "error": str(exc)}


def _check_vc_runtime() -> dict[str, Any]:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = [
        windir / "System32" / "vcruntime140.dll",
        windir / "System32" / "vcruntime140_1.dll",
    ]
    existing = [str(path) for path in candidates if path.exists()]
    return {"available": bool(existing), "files": existing}


def _oda_status() -> dict[str, Any]:
    try:
        from src.services.comparison.dwg_differ import DwgDiffer

        status = DwgDiffer.get_status()
        return {
            "available": bool(status.get("oda_converter")),
            "path": status.get("oda_path"),
            "dwg_support": bool(status.get("dwg_support")),
            "details": status,
        }
    except Exception as exc:  # pragma: no cover - defensive/environmental
        return {"available": False, "error": str(exc)}


def collect_environment_report() -> dict[str, Any]:
    temp_root = Path(tempfile.gettempdir()) / "drawing_compare_release_gate"
    output_root = PROJECT_ROOT / "release" / ".environment_probe"
    runtime_modules = {
        "PySide6": _import_status("PySide6"),
        "fitz": _import_status("fitz"),
        "ezdxf": _import_status("ezdxf"),
        "cv2": _import_status("cv2"),
        "numpy": _import_status("numpy"),
        "PIL": _import_status("PIL"),
        "scipy": _import_status("scipy"),
        "skimage": _import_status("skimage"),
        "openpyxl": _import_status("openpyxl"),
    }
    return {
        "project_root": str(PROJECT_ROOT),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "is_frozen": bool(getattr(sys, "frozen", False)),
        },
        "write_access": {
            "temp": _check_write_access(temp_root),
            "release_output": _check_write_access(output_root),
        },
        "vc_runtime": _check_vc_runtime(),
        "runtime_modules": runtime_modules,
        "oda_converter": _oda_status(),
        "path_tools": {
            "pyinstaller": shutil.which("pyinstaller") or shutil.which("pyinstaller.exe"),
            "oda_file_converter": shutil.which("ODAFileConverter") or shutil.which("ODAFileConverter.exe"),
        },
    }


def _console_summary(report: dict[str, Any]) -> str:
    module_summary = ", ".join(
        f"{name}={'OK' if info.get('available') else 'FAIL'}"
        for name, info in report.get("runtime_modules", {}).items()
    )
    write_access = report.get("write_access", {})
    oda = report.get("oda_converter", {})
    return "\n".join(
        [
            "Drawing Compare Workbench Release Environment Check",
            f"Project: {report['project_root']}",
            f"Platform: {report['platform']['system']} {report['platform']['release']} ({report['platform']['machine']})",
            f"Python: {report['platform']['python_version']} | frozen={report['platform']['is_frozen']}",
            f"Temp write access: {'OK' if write_access.get('temp', {}).get('writable') else 'FAIL'} -> {write_access.get('temp', {}).get('path')}",
            f"Release write access: {'OK' if write_access.get('release_output', {}).get('writable') else 'FAIL'} -> {write_access.get('release_output', {}).get('path')}",
            f"VC runtime: {'OK' if report['vc_runtime']['available'] else 'MISSING'}",
            f"Modules: {module_summary}",
            f"ODA Converter: {'OK' if oda.get('available') else 'MISSING'} -> {oda.get('path') or oda.get('error') or 'not found'}",
            f"PyInstaller on PATH: {report['path_tools']['pyinstaller'] or 'not found'}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Drawing Compare Workbench release environment gate.")
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
