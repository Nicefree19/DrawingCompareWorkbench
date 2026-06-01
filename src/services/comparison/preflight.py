# -*- coding: utf-8 -*-
"""Operational preflight checks for drawing compare runs."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Union


@dataclass
class PreflightCheck:
    name: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class PreflightResult:
    generated_at: str
    status: str
    checks: list[PreflightCheck]

    @property
    def errors(self) -> list[PreflightCheck]:
        return [check for check in self.checks if check.status == "error"]

    @property
    def warnings(self) -> list[PreflightCheck]:
        return [check for check in self.checks if check.status == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "checks": [check.to_dict() for check in self.checks],
        }


def run_preflight(
    *,
    source_a: Union[str, Path],
    source_b: Union[str, Path],
    output_dir: Union[str, Path],
    dxf_cache_dir: Optional[Union[str, Path]] = None,
    compare_state_dir: Optional[Union[str, Path]] = None,
    allow_long_path_warning: bool = False,
) -> PreflightResult:
    """Run non-destructive checks and write-permission probes outside inputs."""

    checks: list[PreflightCheck] = []
    source_a_path = Path(source_a).resolve()
    source_b_path = Path(source_b).resolve()
    output_path = Path(output_dir).resolve()
    cache_path = Path(dxf_cache_dir).resolve() if dxf_cache_dir else output_path / "dxf_cache"
    state_path = Path(compare_state_dir).resolve() if compare_state_dir else output_path / "compare_state"

    for label, path in (("source_a", source_a_path), ("source_b", source_b_path)):
        checks.append(
            PreflightCheck(
                label,
                "ok" if path.exists() else "error",
                f"{label} exists" if path.exists() else f"{label} does not exist: {path}",
                {"path": str(path)},
            )
        )

    for label, path in (("output_dir", output_path), ("dxf_cache_dir", cache_path), ("compare_state_dir", state_path)):
        checks.append(_write_probe(label, path))

    for label, path in (("output_dir", output_path), ("dxf_cache_dir", cache_path), ("compare_state_dir", state_path)):
        status, message = _input_pollution_status(path, [source_a_path, source_b_path])
        checks.append(PreflightCheck(f"{label}_outside_inputs", status, message, {"path": str(path)}))

    checks.append(_disk_space_check(output_path))
    checks.append(_temp_dir_check())
    checks.extend(_long_path_checks([source_a_path, source_b_path, output_path, cache_path, state_path], allow_long_path_warning))
    checks.append(_rtree_check())
    checks.append(_oda_check())
    checks.append(_dwg_version_support_check([source_a_path, source_b_path]))
    checks.append(_pymupdf_check())
    checks.append(_pdf_support_check([source_a_path, source_b_path]))
    checks.append(_font_check())
    checks.append(_preview_dependency_check())

    status = "failed" if any(check.status == "error" for check in checks) else "warning" if any(
        check.status == "warning" for check in checks
    ) else "passed"
    return PreflightResult(datetime.now().isoformat(), status, checks)


def _write_probe(label: str, path: Path) -> PreflightCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".preflight_", suffix=".tmp", dir=str(path))
        os.close(fd)
        Path(temp_name).unlink(missing_ok=True)
        return PreflightCheck(label, "ok", f"{label} is writable", {"path": str(path)})
    except Exception as exc:
        return PreflightCheck(label, "error", f"{label} is not writable: {exc}", {"path": str(path)})


def _input_pollution_status(path: Path, inputs: Iterable[Path]) -> tuple[str, str]:
    for source in inputs:
        try:
            path.relative_to(source)
            return "error", f"output/cache path is inside input folder: {path}"
        except ValueError:
            continue
    return "ok", "path is outside input folders"


def _disk_space_check(path: Path) -> PreflightCheck:
    try:
        usage = shutil.disk_usage(path)
        free_gb = round(usage.free / (1024**3), 2)
        status = "warning" if usage.free < 2 * 1024**3 else "ok"
        return PreflightCheck("disk_space", status, f"free disk space: {free_gb} GB", {"free_gb": free_gb})
    except Exception as exc:
        return PreflightCheck("disk_space", "warning", f"could not check disk space: {exc}")


def _temp_dir_check() -> PreflightCheck:
    temp_path = Path(tempfile.gettempdir())
    check = _write_probe("temp_dir", temp_path)
    check.details["path"] = str(temp_path)
    return check


def _long_path_checks(paths: Iterable[Path], allow_long_path_warning: bool) -> list[PreflightCheck]:
    checks = []
    for path in paths:
        length = len(str(path))
        if length >= 260 and not allow_long_path_warning:
            status = "error"
            message = f"path length {length} may exceed Windows MAX_PATH: {path}"
        elif length >= 240:
            status = "warning"
            message = f"path length {length} is close to Windows MAX_PATH: {path}"
        else:
            status = "ok"
            message = f"path length {length} is acceptable"
        checks.append(PreflightCheck("windows_long_path", status, message, {"path": str(path), "length": length}))
    return checks


def _rtree_check() -> PreflightCheck:
    try:
        from .spatial_index import RTREE_AVAILABLE

        return PreflightCheck(
            "rtree",
            "ok" if RTREE_AVAILABLE else "warning",
            "rtree available" if RTREE_AVAILABLE else "rtree unavailable; grid/linear fallback will be used",
            {"available": bool(RTREE_AVAILABLE)},
        )
    except Exception as exc:
        return PreflightCheck("rtree", "warning", f"could not check rtree: {exc}")


def _oda_check() -> PreflightCheck:
    try:
        from .dwg_differ import DwgDiffer

        status = DwgDiffer.get_status()
        installed = bool(status.get("oda_converter"))
        return PreflightCheck(
            "oda_converter",
            "warning" if installed else "ok",
            (
                "Legacy ODA fallback detected; customer builds must keep it disabled"
                if installed
                else "Legacy ODA fallback not found; canonical CAD pipeline remains ODA-free"
            ),
            status,
        )
    except Exception as exc:
        return PreflightCheck("oda_converter", "warning", f"could not check legacy ODA fallback: {exc}")


def _dwg_version_support_check(paths: Iterable[Path]) -> PreflightCheck:
    samples = list(_iter_dwg_paths(paths, limit=10))
    if not samples:
        return PreflightCheck("dwg_version_support", "ok", "no DWG inputs detected", {"required": False})
    try:
        from .dwg_importer import DwgVersionDetector
    except Exception as exc:
        return PreflightCheck(
            "dwg_version_support",
            "warning",
            f"could not check DWG versions: {exc}",
            {"required": True, "sample_count": len(samples)},
        )

    supported: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    for path in samples:
        try:
            version = DwgVersionDetector.detect_file(path)
        except Exception as exc:
            unreadable.append({"path": str(path), "error": str(exc)})
            continue
        item = {"path": str(path), **version.to_dict()}
        if version.supported:
            supported.append(item)
        else:
            unsupported.append(item)

    details = {
        "required": True,
        "sample_count": len(samples),
        "supported_versions": sorted(DwgVersionDetector.SUPPORTED_CODES),
        "supported": supported,
        "unsupported": unsupported,
        "unreadable": unreadable,
        "note": "Compare converted DXF files or provide DWG versions supported by the native adapter.",
    }
    if unsupported:
        codes = sorted({item.get("code", "") for item in unsupported if item.get("code")})
        return PreflightCheck(
            "dwg_version_support",
            "error",
            "DWG input version is unsupported by the native adapter: "
            f"{', '.join(codes)}. Compare converted DXF files or supported AC1015 DWG files.",
            details,
        )
    if unreadable:
        return PreflightCheck(
            "dwg_version_support",
            "error",
            "DWG input version could not be detected; compare converted DXF files or repair the DWG source.",
            details,
        )
    return PreflightCheck(
        "dwg_version_support",
        "ok",
        "DWG inputs use supported native adapter versions",
        details,
    )


def _iter_dwg_paths(paths: Iterable[Path], *, limit: int) -> Iterable[Path]:
    emitted = 0
    for path in paths:
        if not path.exists():
            continue
        if path.is_file() and path.suffix.lower() == ".dwg":
            yield path
            emitted += 1
            if emitted >= limit:
                return
        elif path.is_dir():
            try:
                iterator = path.rglob("*.dwg")
                for child in iterator:
                    yield child
                    emitted += 1
                    if emitted >= limit:
                        return
            except Exception:
                continue


def _pymupdf_check() -> PreflightCheck:
    try:
        import fitz  # noqa: F401

        return PreflightCheck("pymupdf", "ok", "PyMuPDF available", {"available": True})
    except Exception as exc:
        return PreflightCheck(
            "pymupdf",
            "warning",
            f"PyMuPDF unavailable; PDF comparison support is limited: {exc}",
            {"available": False},
        )


def _pdf_support_check(paths: Iterable[Path]) -> PreflightCheck:
    pdf_path = _first_pdf_path(paths)
    if pdf_path is None:
        return PreflightCheck("pdf_support", "ok", "no PDF inputs detected", {"required": False})
    try:
        import fitz  # noqa: F401
    except Exception as exc:
        return PreflightCheck(
            "pdf_support",
            "error",
            f"PDF input detected but PyMuPDF is unavailable: {exc}",
            {"required": True, "sample": str(pdf_path)},
        )
    return PreflightCheck(
        "pdf_support",
        "ok",
        "PDF input detected and PyMuPDF is available",
        {"required": True, "sample": str(pdf_path)},
    )


def _first_pdf_path(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        if not path.exists():
            continue
        if path.is_file() and path.suffix.lower() == ".pdf":
            return path
        if path.is_dir():
            try:
                return next(path.rglob("*.pdf"))
            except StopIteration:
                continue
            except Exception:
                continue
    return None


def _font_check() -> PreflightCheck:
    candidates = []
    if os.name == "nt":
        candidates.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    candidates.extend([Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"])
    existing = [path for path in candidates if path.exists()]
    if existing:
        return PreflightCheck(
            "font_support",
            "ok",
            "font directory available",
            {"paths": [str(path) for path in existing]},
        )
    return PreflightCheck(
        "font_support",
        "warning",
        "font directory not found; PDF/CAD text rendering may use fallback fonts",
        {"paths": [str(path) for path in candidates]},
    )


def _preview_dependency_check() -> PreflightCheck:
    missing = []
    try:
        import PIL  # noqa: F401
    except Exception:
        missing.append("Pillow")
    try:
        import numpy  # noqa: F401
    except Exception:
        missing.append("numpy")
    if missing:
        return PreflightCheck(
            "preview_dependencies",
            "warning",
            "preview rendering dependencies missing: " + ", ".join(missing),
            {"missing": missing},
        )
    return PreflightCheck("preview_dependencies", "ok", "preview dependencies available")
