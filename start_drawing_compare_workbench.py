# -*- coding: utf-8 -*-
"""Launch the Drawing Compare Workbench desktop app."""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.services.comparison.workbench_subprocess import dispatch_packaged_worker


def _configure_highdpi_environment() -> None:
    """Set Qt environment variables for crisp rendering on every monitor.

    Must run BEFORE any QApplication is instantiated. Addresses the user
    report of "UI 모듈이 모니터 화면에 따라 짤리는 현상" — on Windows the
    default fractional DPI scaling on a 1366×768 laptop or a 4K monitor
    would clip the workbench panels because Qt was rounding the device
    pixel ratio down to integers.

    - ``QT_ENABLE_HIGHDPI_SCALING=1`` activates the high-DPI scaling path
      (Qt 5.14+; on Qt 6 it is on by default but setting it explicitly is
      safe and forward-compatible).
    - ``QT_SCALE_FACTOR_ROUNDING_POLICY=PassThrough`` keeps the OS-provided
      scaling factor as a float (e.g. 1.25, 1.5) instead of rounding to the
      nearest integer, so widgets size correctly at 125%/150% Windows
      display settings.
    - ``QT_AUTO_SCREEN_SCALE_FACTOR=1`` lets Qt pick a per-screen factor for
      multi-monitor setups where each display has a different DPI.
    """
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")


def _arm_crash_diagnostics() -> None:
    """Install Python excepthook + Windows fault handler as early as possible.

    Audit-gates §12.4 B2 — without these, the Qt6Core BEX64 (0xc0000409) crash
    observed at 15:57:52 on 2026-05-15 bypassed every Python-level handler
    and the only evidence was the Windows Application Error log. With these
    armed:
    - Python exceptions land in ``logs/error_YYYYMMDD.log`` with traceback
      and a user-friendly Qt dialog (Headless test mode is auto-detected so
      pytest --offscreen does not hang waiting for a dialog click).
    - Native crashes (Qt6Core fast-fail, SIGSEGV, stack overflow) land in
      ``logs/fault_YYYYMMDD_HHMMSS.log`` with every Python thread's stack.
    """
    try:
        from src.core.error_handler import install_exception_handler
        install_exception_handler(use_qt_dialog=True, enable_fault_handler=True)
    except Exception:
        # Diagnostics arming is best-effort and must never block startup —
        # if PySide6 / logging fails to initialise we still want the app
        # to run so the user can at least see the error in stdout.
        import traceback
        traceback.print_exc()


def _maybe_run_diagnostics() -> Optional[int]:
    """Plan §18 B-3 (Agent F production-scale follow-up) — handle the
    ``--diagnose`` flag before any heavy import. Returns the desired
    exit code when the flag is present, else ``None`` so the launcher
    continues into the workbench.

    Without this, a customer hitting "Compare PDFs" with PyMuPDF
    missing would only see a gray placeholder + a ``logger.warning``
    that never reaches the screen. ``--diagnose`` surfaces a clear
    actionable report so the user fixes the install before clicking
    anything.
    """
    if "--diagnose" not in sys.argv:
        return None
    try:
        from src.core.runtime_diagnostics import cli_main
    except Exception:
        import traceback
        traceback.print_exc()
        return 2
    return cli_main(sys.argv)


def _emit_startup_dependency_warning() -> None:
    """Plan §18 B-3 — non-blocking startup probe. Writes a one-line
    summary to stderr when REQUIRED dependencies are missing so an
    operator who launched without ``--diagnose`` still sees something
    actionable before the GUI brings up its splash.

    Optional-only gaps are silent on startup to avoid noise; the
    customer can run ``--diagnose`` for the full report.
    """
    try:
        from src.core.runtime_diagnostics import check_dependencies
        report = check_dependencies()
    except Exception:
        return
    if not report.has_blockers:
        return
    missing = ", ".join(s.package for s in report.missing_required)
    print(
        f"[startup] Missing required dependency(s): {missing}. Run "
        f"'python start_drawing_compare_workbench.py --diagnose' for "
        f"install instructions.",
        file=sys.stderr,
    )


def main() -> int:
    multiprocessing.freeze_support()
    diag_exit = _maybe_run_diagnostics()
    if diag_exit is not None:
        return diag_exit
    _configure_highdpi_environment()
    _arm_crash_diagnostics()
    _emit_startup_dependency_warning()
    worker_status = dispatch_packaged_worker(sys.argv)
    if worker_status is not None:
        return worker_status

    from src.gui.drawing_compare_workbench import main as workbench_main

    return int(workbench_main())


if __name__ == "__main__":
    raise SystemExit(main())
