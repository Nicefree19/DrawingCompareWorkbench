# -*- coding: utf-8 -*-
"""Startup dependency diagnostics for Drawing Compare (Plan §18 B-3).

Agent F (production-scale verification, 2026-05-17) found that a
customer hitting "Compare PDFs" with PyMuPDF missing sees only a gray
placeholder + a ``logger.warning`` that never reaches the screen. The
customer concludes "the tool is broken", files a support ticket, and
costs the operator 4 hours of MTTR.

This module surfaces missing or out-of-version dependencies at GUI
startup so the user sees an actionable message BEFORE they click
anything.

Public API
==========
- ``check_dependencies()`` — returns a ``DependencyReport`` listing
  required + optional libraries with their availability + version.
- ``format_report()`` — turn the report into a human-readable
  multi-line string the launcher prints to stderr.
- ``cli_main()`` — entry point for ``python start_drawing_compare
  _workbench.py --diagnose``: prints the report and exits 0/1
  depending on whether any REQUIRED dependency is missing.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
import platform
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DependencyStatus:
    """Single dependency probe result."""

    package: str
    required: bool
    available: bool
    version: Optional[str] = None
    error: Optional[str] = None
    install_hint: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "required": self.required,
            "available": self.available,
            "version": self.version,
            "error": self.error,
            "install_hint": self.install_hint,
        }


@dataclass
class DependencyReport:
    """Result of a full dependency probe."""

    schema_version: int = SCHEMA_VERSION
    python_version: str = field(default_factory=lambda: platform.python_version())
    platform: str = field(default_factory=lambda: platform.platform())
    statuses: list[DependencyStatus] = field(default_factory=list)

    @property
    def missing_required(self) -> list[DependencyStatus]:
        return [s for s in self.statuses if s.required and not s.available]

    @property
    def missing_optional(self) -> list[DependencyStatus]:
        return [s for s in self.statuses if not s.required and not s.available]

    @property
    def has_blockers(self) -> bool:
        return bool(self.missing_required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "python_version": self.python_version,
            "platform": self.platform,
            "statuses": [s.to_dict() for s in self.statuses],
            "missing_required_count": len(self.missing_required),
            "missing_optional_count": len(self.missing_optional),
            "has_blockers": self.has_blockers,
        }


# Curated list of probed dependencies. Required = customer cannot run
# the GUI without it. Optional = degraded experience but tool still
# loads (e.g. AI classifier disabled if torch missing).
_PROBED_DEPENDENCIES: tuple[tuple[str, bool, str], ...] = (
    # (module name, required, install hint)
    ("PySide6", True, "pip install PySide6==6.10.1"),
    ("ezdxf", True, "pip install ezdxf>=1.4,<2"),
    ("fitz", True, "pip install PyMuPDF>=1.26.7,<2  # 'fitz' is the import name"),
    ("PIL", True, "pip install Pillow>=10.0"),
    ("psutil", True, "pip install psutil>=5.9"),
    ("numpy", True, "pip install numpy>=1.24"),
    ("cv2", False, "pip install opencv-python>=4.8  # required for raster PDF compare"),
    ("rtree", False, "pip install Rtree>=1.0  # speeds up spatial indexes"),
    ("matplotlib", False, "pip install matplotlib>=3.7"),
    ("yaml", False, "pip install PyYAML>=6.0"),
)


def _probe(module_name: str, required: bool, install_hint: str) -> DependencyStatus:
    """Try importing the module; capture version + any error."""
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        return DependencyStatus(
            package=module_name,
            required=required,
            available=False,
            version=None,
            error=str(exc),
            install_hint=install_hint,
        )
    except Exception as exc:  # noqa: BLE001 — exotic import-time failures
        return DependencyStatus(
            package=module_name,
            required=required,
            available=False,
            version=None,
            error=f"{type(exc).__name__}: {exc}",
            install_hint=install_hint,
        )

    # Version detection — try module.__version__, fall back to
    # importlib.metadata (handles cases like fitz which has no
    # ``__version__`` on older versions).
    version: Optional[str] = getattr(module, "__version__", None)
    if version is None:
        # Map import names to distribution names where they differ.
        dist_name = {
            "fitz": "PyMuPDF",
            "PIL": "Pillow",
            "cv2": "opencv-python",
            "yaml": "PyYAML",
        }.get(module_name, module_name)
        try:
            version = importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            version = None
    return DependencyStatus(
        package=module_name,
        required=required,
        available=True,
        version=str(version) if version is not None else "(unknown)",
        error=None,
        install_hint=install_hint,
    )


def check_dependencies() -> DependencyReport:
    """Probe every curated dependency and return a structured report."""
    report = DependencyReport()
    for name, required, hint in _PROBED_DEPENDENCIES:
        report.statuses.append(_probe(name, required, hint))
    return report


def format_report(report: DependencyReport) -> str:
    """Render the report as a multi-line human-readable string."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Drawing Compare — runtime dependency diagnostics")
    lines.append("=" * 70)
    lines.append(f"Python:   {report.python_version}")
    lines.append(f"Platform: {report.platform}")
    lines.append("")
    if report.has_blockers:
        lines.append(
            f"[BLOCKER] {len(report.missing_required)} required dependency "
            "missing — GUI cannot start until installed."
        )
    elif report.missing_optional:
        lines.append(
            f"[WARN] {len(report.missing_optional)} optional dependency "
            "missing — some features will be degraded."
        )
    else:
        lines.append("[OK] All required + optional dependencies available.")
    lines.append("")
    lines.append("Detail:")
    for status in report.statuses:
        marker = (
            "OK"
            if status.available
            else ("MISSING (required)" if status.required else "MISSING (optional)")
        )
        version_part = f"v{status.version}" if status.version else ""
        lines.append(f"  [{marker:>18}] {status.package:<14} {version_part}")
        if not status.available and status.install_hint:
            lines.append(f"      → {status.install_hint}")
        if not status.available and status.error:
            lines.append(f"      error: {status.error}")
    lines.append("=" * 70)
    return "\n".join(lines)


def cli_main(argv: Optional[list[str]] = None) -> int:
    """CLI entry — run by ``start_drawing_compare_workbench.py --diagnose``.

    Returns 0 when no required dependency is missing, 1 otherwise.
    """
    del argv  # Unused — diagnostic does not accept flags yet.
    report = check_dependencies()
    print(format_report(report), file=sys.stderr)
    return 1 if report.has_blockers else 0


__all__ = [
    "DependencyReport",
    "DependencyStatus",
    "SCHEMA_VERSION",
    "check_dependencies",
    "cli_main",
    "format_report",
]
