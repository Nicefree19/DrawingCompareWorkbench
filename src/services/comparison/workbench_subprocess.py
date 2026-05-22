# -*- coding: utf-8 -*-
"""Subprocess launch helpers for the Drawing Compare Workbench.

PyInstaller-built Windows apps expose ``sys.executable`` as the workbench
``.exe`` itself, not as ``python.exe``. Worker subprocesses therefore must be
started through explicit internal flags in frozen builds; otherwise the child
process re-enters the GUI and opens another window.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence


ZONE_RENDER_PROCESS_MODULE = "src.services.comparison.zone_render_process"
ZONE_VECTOR_WORKER_MODULE = "src.services.comparison.zone_vector_worker"
VIEWER_RENDER_WORKER_MODULE = "src.services.comparison.viewer_render_worker"
VIEWER_PACKAGE_WORKER_MODULE = "scripts.render_viewer_package_subprocess"

ZONE_RENDER_PROCESS_FLAG = "--drawing-compare-zone-render-process"
ZONE_VECTOR_WORKER_FLAG = "--drawing-compare-zone-vector-worker"
VIEWER_RENDER_WORKER_FLAG = "--drawing-compare-viewer-render-worker"
VIEWER_PACKAGE_WORKER_FLAG = "--drawing-compare-viewer-package-worker"

WORKBENCH_WORKER_FLAGS: dict[str, str] = {
    ZONE_RENDER_PROCESS_MODULE: ZONE_RENDER_PROCESS_FLAG,
    ZONE_VECTOR_WORKER_MODULE: ZONE_VECTOR_WORKER_FLAG,
    VIEWER_RENDER_WORKER_MODULE: VIEWER_RENDER_WORKER_FLAG,
    VIEWER_PACKAGE_WORKER_MODULE: VIEWER_PACKAGE_WORKER_FLAG,
}

_WORKER_MODULE_BY_FLAG = {flag: module for module, flag in WORKBENCH_WORKER_FLAGS.items()}


def _is_frozen(frozen: Optional[bool] = None) -> bool:
    return bool(getattr(sys, "frozen", False) if frozen is None else frozen)


def worker_command_for_module(
    module_name: str,
    *,
    executable: str | None = None,
    frozen: Optional[bool] = None,
) -> tuple[str, list[str]]:
    """Return the program and leading arguments for a workbench worker."""

    program = executable or sys.executable
    worker_flag = WORKBENCH_WORKER_FLAGS.get(module_name)
    if _is_frozen(frozen) and worker_flag:
        return program, [worker_flag]
    return program, ["-m", module_name]


def worker_working_directory(
    *,
    project_root: Path | None = None,
    executable: str | None = None,
    frozen: Optional[bool] = None,
) -> Path:
    """Return a stable cwd for worker subprocesses."""

    if _is_frozen(frozen):
        return Path(executable or sys.executable).resolve().parent
    return project_root or Path.cwd()


def dispatch_packaged_worker(argv: Sequence[str] | None = None) -> Optional[int]:
    """Run an internal worker command if ``argv`` targets one.

    Returns ``None`` when the argv belongs to the GUI app.
    """

    raw_argv = list(sys.argv if argv is None else argv)
    if len(raw_argv) < 2:
        return None
    flag = raw_argv[1]
    module_name = _WORKER_MODULE_BY_FLAG.get(flag)
    if not module_name:
        return None

    worker_argv = raw_argv[2:]
    if module_name == ZONE_RENDER_PROCESS_MODULE:
        from .zone_render_process import main as zone_render_main

        return int(zone_render_main())
    if module_name == ZONE_VECTOR_WORKER_MODULE:
        from .zone_vector_worker import main as zone_vector_main

        return int(zone_vector_main(worker_argv))
    if module_name == VIEWER_RENDER_WORKER_MODULE:
        from .viewer_render_worker import main as viewer_render_main

        return int(viewer_render_main(worker_argv))
    if module_name == VIEWER_PACKAGE_WORKER_MODULE:
        from scripts.render_viewer_package_subprocess import main as viewer_package_main

        return int(viewer_package_main())
    return None
