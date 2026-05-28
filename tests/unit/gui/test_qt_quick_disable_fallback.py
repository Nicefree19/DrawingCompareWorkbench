# -*- coding: utf-8 -*-
"""Regression tests for Qt Quick startup fallback."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_disable_qt_quick_env_uses_compatibility_viewer() -> None:
    code = r"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
import src.gui.drawing_compare_workbench as wb

app = QApplication.instance() or QApplication([])
view = wb.QtQuickUnavailableLightweightViewport(side="before")
scene_result = view.load_scene_pack(None, empty_notice="empty")
pdf_result = view.load_pdf_page("missing.pdf")
print(json.dumps({
    "qt_quick_disabled": wb.QT_QUICK_DISABLED,
    "qt_quick_available": wb.QT_QUICK_AVAILABLE,
    "qquick_widget_is_none": wb.QQuickWidget is None,
    "lightweight_only": wb.DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY,
    "viewer_class": type(view).__name__,
    "scene_result": scene_result,
    "pdf_result": pdf_result,
}))
"""
    env = os.environ.copy()
    env["DRAWING_COMPARE_DISABLE_QT_QUICK"] = "1"
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "qt_quick_disabled": True,
        "qt_quick_available": False,
        "qquick_widget_is_none": True,
        "lightweight_only": False,
        "viewer_class": "QtQuickUnavailableLightweightViewport",
        "scene_result": False,
        "pdf_result": False,
    }


def test_qt_quick_import_failure_uses_compatibility_viewer() -> None:
    code = r"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

class BlockQtQuickWidgets:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PySide6.QtQuickWidgets":
            raise ImportError("blocked QtQuickWidgets for fallback test")
        return None

sys.meta_path.insert(0, BlockQtQuickWidgets())
import src.gui.drawing_compare_workbench as wb
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
window = wb.DrawingCompareWorkbenchV2()
window.act_lightweight_viewer_v2.setChecked(True)

print(json.dumps({
    "qt_quick_disabled": wb.QT_QUICK_DISABLED,
    "qt_quick_available": wb.QT_QUICK_AVAILABLE,
    "qquick_widget_is_none": wb.QQuickWidget is None,
    "lightweight_only": wb.DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY,
    "action_enabled": window.act_lightweight_viewer_v2.isEnabled(),
    "action_visible": window.act_lightweight_viewer_v2.isVisible(),
    "action_checked": window.act_lightweight_viewer_v2.isChecked(),
    "active": window._is_lightweight_viewer_active_v2(),
}))
"""
    env = os.environ.copy()
    env.pop("DRAWING_COMPARE_DISABLE_QT_QUICK", None)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "qt_quick_disabled": False,
        "qt_quick_available": False,
        "qquick_widget_is_none": True,
        "lightweight_only": False,
        "action_enabled": False,
        "action_visible": False,
        "action_checked": False,
        "active": False,
    }
