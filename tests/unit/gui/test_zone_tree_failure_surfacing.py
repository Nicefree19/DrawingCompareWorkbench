"""D3 (fail-loud): a full-zone-tree rebuild failure must reach the user.

Previously `_on_full_zone_tree_overlay_failed_v2` / `_plan_failed_v2` only wrote a
perf event, leaving the change-zone list empty/stale with no explanation. They
now set ``lbl_status_v2`` via the extracted satellite. Tests cover the pure
satellite helper and the monolith wiring (offscreen, no event loop).
"""

from __future__ import annotations

import os

import pytest

from src.gui.workbench_zone_tree_failure import (
    ZONE_TREE_REBUILD_FAILED_STATUS,
    append_zone_tree_rebuild_failure,
)


def test_helper_returns_status_without_viewer_root() -> None:
    assert (
        append_zone_tree_rebuild_failure(None, "pair1", "boom", plan_worker=False)
        == ZONE_TREE_REBUILD_FAILED_STATUS
    )


def test_helper_writes_perf_event_and_returns_status(tmp_path) -> None:
    # With a viewer root it records telemetry AND returns the user status.
    status = append_zone_tree_rebuild_failure(tmp_path, "pair1", "boom", plan_worker=True)
    assert status == ZONE_TREE_REBUILD_FAILED_STATUS
    # append_viewer_perf_event writes a perf artifact under the viewer root.
    assert any(tmp_path.rglob("*perf*")), "expected a perf event artifact under the viewer root"


@pytest.fixture(scope="module")
def _qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize(
    "callback_name, plan_worker",
    [
        ("_on_full_zone_tree_overlay_failed_v2", False),
        ("_on_full_zone_tree_plan_failed_v2", True),
    ],
)
def test_rebuild_failure_callback_surfaces_status(_qapp, monkeypatch, callback_name, plan_worker) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    window = DrawingCompareWorkbenchV2()
    try:
        # Bypass the request-currency + worker-retirement guards so the surfacing
        # path runs deterministically (those are covered elsewhere).
        monkeypatch.setattr(window, "_full_zone_tree_request_is_current_v2", lambda *a, **k: True)
        if plan_worker:
            monkeypatch.setattr(window, "_full_zone_tree_chunk_state_is_current_v2", lambda *a, **k: True)
            monkeypatch.setattr(window, "_retire_full_zone_tree_plan_worker_v2", lambda *a, **k: None)
        else:
            monkeypatch.setattr(window, "_retire_full_zone_tree_overlay_worker_v2", lambda *a, **k: None)
        window._viewer_root = None  # skip telemetry; we assert the user-facing status

        window.lbl_status_v2.setText("진행 중")
        getattr(window, callback_name)("pair1", 1, "worker exploded")

        assert window.lbl_status_v2.text() == ZONE_TREE_REBUILD_FAILED_STATUS
    finally:
        window.deleteLater()
