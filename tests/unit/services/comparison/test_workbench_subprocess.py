# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
import types
from pathlib import Path

from src.services.comparison.workbench_subprocess import (
    CAD_VISUAL_CONVERSION_WORKER_FLAG,
    CAD_VISUAL_CONVERSION_WORKER_MODULE,
    VIEWER_PACKAGE_WORKER_FLAG,
    VIEWER_PACKAGE_WORKER_MODULE,
    VIEWER_RENDER_WORKER_FLAG,
    VIEWER_RENDER_WORKER_MODULE,
    WORKBENCH_WORKER_FLAGS,
    ZONE_RENDER_PROCESS_FLAG,
    ZONE_RENDER_PROCESS_MODULE,
    ZONE_VECTOR_WORKER_FLAG,
    ZONE_VECTOR_WORKER_MODULE,
    dispatch_packaged_worker,
    worker_command_for_module,
    worker_working_directory,
)


def test_worker_command_uses_python_module_in_development() -> None:
    program, args = worker_command_for_module(
        ZONE_RENDER_PROCESS_MODULE,
        executable="python.exe",
        frozen=False,
    )

    assert program == "python.exe"
    assert args == ["-m", ZONE_RENDER_PROCESS_MODULE]


def test_worker_command_uses_internal_flags_in_frozen_build() -> None:
    executable = r"C:\Program Files\DrawingCompareWorkbench\DrawingCompareWorkbench.exe"

    assert worker_command_for_module(
        ZONE_RENDER_PROCESS_MODULE,
        executable=executable,
        frozen=True,
    ) == (executable, [ZONE_RENDER_PROCESS_FLAG])
    assert worker_command_for_module(
        ZONE_VECTOR_WORKER_MODULE,
        executable=executable,
        frozen=True,
    ) == (executable, [ZONE_VECTOR_WORKER_FLAG])
    assert worker_command_for_module(
        VIEWER_RENDER_WORKER_MODULE,
        executable=executable,
        frozen=True,
    ) == (executable, [VIEWER_RENDER_WORKER_FLAG])
    assert worker_command_for_module(
        VIEWER_PACKAGE_WORKER_MODULE,
        executable=executable,
        frozen=True,
    ) == (executable, [VIEWER_PACKAGE_WORKER_FLAG])
    assert worker_command_for_module(
        CAD_VISUAL_CONVERSION_WORKER_MODULE,
        executable=executable,
        frozen=True,
    ) == (executable, [CAD_VISUAL_CONVERSION_WORKER_FLAG])


def test_unknown_worker_module_still_uses_python_module_mode() -> None:
    program, args = worker_command_for_module(
        "src.services.comparison.unknown_worker",
        executable="python.exe",
        frozen=True,
    )

    assert program == "python.exe"
    assert args == ["-m", "src.services.comparison.unknown_worker"]


def test_worker_working_directory_points_to_exe_folder_when_frozen() -> None:
    executable = r"C:\Program Files\DrawingCompareWorkbench\DrawingCompareWorkbench.exe"

    assert worker_working_directory(executable=executable, frozen=True) == Path(
        executable
    ).parent
    assert worker_working_directory(project_root=Path(r"C:\repo"), frozen=False) == Path(
        r"C:\repo"
    )


def test_dispatch_packaged_worker_returns_none_for_gui_argv() -> None:
    assert dispatch_packaged_worker(["DrawingCompareWorkbench.exe", "--smoke-exit-ms", "1000"]) is None


def test_dispatch_packaged_worker_routes_cli_args(monkeypatch) -> None:
    module_name = ZONE_VECTOR_WORKER_MODULE
    fake_module = types.ModuleType(module_name)
    seen: dict[str, object] = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 7

    fake_module.main = fake_main
    monkeypatch.setitem(sys.modules, module_name, fake_module)

    status = dispatch_packaged_worker(
        ["DrawingCompareWorkbench.exe", WORKBENCH_WORKER_FLAGS[module_name], "--a", "1"]
    )

    assert status == 7
    assert seen == {"argv": ["--a", "1"]}


def test_dispatch_packaged_worker_routes_viewer_package_worker(monkeypatch) -> None:
    module_name = VIEWER_PACKAGE_WORKER_MODULE
    fake_module = types.ModuleType(module_name)

    def fake_main():
        return 11

    fake_module.main = fake_main
    monkeypatch.setitem(sys.modules, module_name, fake_module)

    status = dispatch_packaged_worker(
        ["DrawingCompareWorkbench.exe", WORKBENCH_WORKER_FLAGS[module_name]]
    )

    assert status == 11


def test_dispatch_packaged_worker_routes_cad_visual_conversion_worker(monkeypatch) -> None:
    module_name = CAD_VISUAL_CONVERSION_WORKER_MODULE
    fake_module = types.ModuleType(module_name)

    def fake_main():
        return 13

    fake_module.main = fake_main
    monkeypatch.setitem(sys.modules, module_name, fake_module)

    status = dispatch_packaged_worker(
        ["DrawingCompareWorkbench.exe", WORKBENCH_WORKER_FLAGS[module_name]]
    )

    assert status == 13
