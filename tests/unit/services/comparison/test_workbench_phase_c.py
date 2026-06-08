# -*- coding: utf-8 -*-
"""Unit tests for Phase C usability upgrades.

Covers:
- C1: review status filter dropdown + zone progress label content
- C2: zoom slider sync (slider value <-> viewport zoom property)
- C3: compare preset application (recursive + quality combo)
- C4: zone memo persistence path

These rely on widget instantiation but no live QML rendering.
"""

from __future__ import annotations

import importlib
import json
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem, QTreeWidgetItem


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_compare_presets_have_default_and_recursive_variants() -> None:
    from src.gui.drawing_compare_workbench import (
        COMPARE_PRESETS,
        COMPARE_PRESET_DEFAULT_INDEX,
        PREVIEW_QUALITY_AUTO_INDEX,
    )
    # Audit-gates §10 follow-up — preset list rewired so default uses
    # PREVIEW_QUALITY_AUTO_INDEX (was DPI 120). Total still 5.
    assert len(COMPARE_PRESETS) == 5
    labels = [p[0] for p in COMPARE_PRESETS]
    assert any("초고속" in label for label in labels)
    assert any("자동 검토" in label for label in labels)
    assert any("빠른" in label for label in labels)
    assert any("정밀" in label for label in labels)
    assert any("전체 폴더" in label for label in labels)
    # Default preset should map to the auto-quality sentinel
    assert COMPARE_PRESETS[COMPARE_PRESET_DEFAULT_INDEX][1] == PREVIEW_QUALITY_AUTO_INDEX
    # Default should be single-folder (recursive=False)
    assert COMPARE_PRESETS[COMPARE_PRESET_DEFAULT_INDEX][2] is False
    # Default should NOT be the speed preset (which uses lazy)
    assert COMPARE_PRESETS[COMPARE_PRESET_DEFAULT_INDEX][3] == "top-issues"


def test_compare_preset_change_updates_quality_and_recursive(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        # Audit-gates §10 follow-up — new index map (auto sentinel inserted
        # at PREVIEW_QUALITY_AUTO_INDEX = 0):
        #   0 = ⚡ 초고속 스캔 (DPI 80 [index 1], lazy, single)
        #   1 = 🤖 자동 검토 (auto [index 0], top-issues, single) ← default
        #   2 = 빠른 스캔 (DPI 80 [index 1], top-issues, single)
        #   3 = 정밀 검토 (초고화질 [index 3], top-issues, single)
        #   4 = 전체 폴더 스캔 (auto [index 0], top-issues, recursive)
        workbench.cmb_preset_v2.setCurrentIndex(4)  # 전체 폴더 스캔
        assert workbench.chk_recursive_v2.isChecked() is True
        assert workbench.cmb_quality_v2.currentIndex() == 0  # auto

        workbench.cmb_preset_v2.setCurrentIndex(3)  # 정밀 검토
        assert workbench.chk_recursive_v2.isChecked() is False
        assert workbench.cmb_quality_v2.currentIndex() == 3  # 초고화질
    finally:
        workbench.deleteLater()


def test_initial_selection_prefers_top_issues_without_overlay_json_load(qapp) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        def fail_overlay_load(_pair_id):
            raise AssertionError("initial top-issue selection should not read overlay_json")

        workbench._viewer_overlays_for_pair_v2 = fail_overlay_load  # type: ignore[method-assign]
        row = {
            "pair_id": "pair_top",
            "zone_count": 1000,
            "top_issues": [
                {"pair_id": "pair_top", "zone_id": f"z{i}", "priority_score": 1000 - i}
                for i in range(dcw.GUI_FIRST_SELECTION_ZONE_LIMIT + 25)
            ],
        }

        overlays, deferred, source = workbench._initial_overlays_for_pair_selection_v2(
            "pair_top",
            row,
            None,
            {"pair_id": "pair_top", "overlay_total_count": 1000},
        )

        assert len(overlays) == dcw.GUI_FIRST_SELECTION_ZONE_LIMIT
        assert overlays[0]["zone_id"] == "z0"
        assert deferred is True
        assert source == "top_issues"
    finally:
        workbench.deleteLater()


def test_initial_selection_defers_large_overlay_json_when_no_preview(qapp) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        def fail_overlay_load(_pair_id):
            raise AssertionError("large initial selection should defer overlay_json")

        workbench._viewer_overlays_for_pair_v2 = fail_overlay_load  # type: ignore[method-assign]
        row = {
            "pair_id": "pair_deferred",
            "zone_count": dcw.GUI_FIRST_SELECTION_ZONE_LIMIT + 100,
        }

        overlays, deferred, source = workbench._initial_overlays_for_pair_selection_v2(
            "pair_deferred",
            row,
            None,
            {"pair_id": "pair_deferred", "overlay_total_count": dcw.GUI_FIRST_SELECTION_ZONE_LIMIT + 100},
        )

        assert overlays == []
        assert deferred is True
        assert source == "overlay_json_deferred"
    finally:
        workbench.deleteLater()


def test_initial_selection_defers_unknown_large_overlay_json_without_declared_count(
    qapp,
    tmp_path,
    monkeypatch,
) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    monkeypatch.setattr(dcw, "GUI_UNKNOWN_OVERLAY_JSON_DEFER_BYTES", 16)
    overlay_path = tmp_path / "large_unknown_overlays.json"
    overlay_path.write_text('{"overlays": []}' + (" " * 32), encoding="utf-8")

    workbench = DrawingCompareWorkbenchV2()
    try:
        def fail_overlay_load(_pair_id):
            raise AssertionError("unknown large overlay_json should be deferred")

        workbench._viewer_root = tmp_path
        viewer_pair = {"pair_id": "pair_unknown", "overlay_json": str(overlay_path)}
        workbench._viewer_pairs_by_id = {"pair_unknown": viewer_pair}
        workbench._viewer_overlays_for_pair_v2 = fail_overlay_load  # type: ignore[method-assign]

        overlays, deferred, source = workbench._initial_overlays_for_pair_selection_v2(
            "pair_unknown",
            {"pair_id": "pair_unknown"},
            None,
            viewer_pair,
        )

        assert overlays == []
        assert deferred is True
        assert source == "overlay_json_deferred_large_unknown"
    finally:
        workbench.deleteLater()


def test_initial_selection_reads_bounded_paged_overlay_slice(qapp, tmp_path) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.viewer_overlay_pages import write_overlay_page_store

    overlays = [
        {"pair_id": "pair_paged", "zone_id": f"z{i}", "bbox": [0, 0, 1, 1], "page_a": 0, "page_b": 0}
        for i in range(20)
    ]
    summary = write_overlay_page_store(
        pair_id="pair_paged",
        overlays=overlays,
        output_root=tmp_path / "overlay_pages",
        page_size=5,
    )
    workbench = DrawingCompareWorkbenchV2()
    original_limit = dcw.GUI_FIRST_SELECTION_ZONE_LIMIT
    try:
        dcw.GUI_FIRST_SELECTION_ZONE_LIMIT = 6
        workbench._viewer_root = tmp_path
        workbench._viewer_pairs_by_id = {
            "pair_paged": {
                "pair_id": "pair_paged",
                "overlay_total_count": 20,
                "overlay_pages_manifest": str(summary.manifest_path),
            }
        }

        def fail_overlay_load(_pair_id):
            raise AssertionError("paged initial selection should not read legacy overlay_json")

        workbench._viewer_overlays_for_pair_v2 = fail_overlay_load  # type: ignore[method-assign]
        selected, deferred, source = workbench._initial_overlays_for_pair_selection_v2(
            "pair_paged",
            {"pair_id": "pair_paged", "zone_count": 20, "top_issues": []},
            None,
            workbench._viewer_pairs_by_id["pair_paged"],
        )

        assert [overlay["zone_id"] for overlay in selected] == [f"z{i}" for i in range(6)]
        assert deferred is True
        assert source == "paged_overlay_store"
    finally:
        dcw.GUI_FIRST_SELECTION_ZONE_LIMIT = original_limit
        workbench.deleteLater()


def test_progress_badge_uses_declared_counts_without_overlay_cache(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.review_project import ReviewStateRecord

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._drawing_rows = [{"pair_id": "pair_progress", "zone_count": 3}]
        for zone_id, status in (("z1", "confirmed"), ("z2", "hold")):
            record = ReviewStateRecord(pair_id="pair_progress", zone_id=zone_id, status=status)
            workbench._review_records_v2[record.key] = record

        assert workbench._drawing_progress_badge_v2("pair_progress") == "⏳ 2/3"
        assert workbench._viewer_overlay_cache == {}
    finally:
        workbench.deleteLater()


def test_auto_advance_uses_declared_counts_without_sync_overlay_load(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.review_project import ReviewStateRecord

    workbench = DrawingCompareWorkbenchV2()
    try:
        rows = [
            {"pair_id": "pair_done", "drawing_number": "A", "zone_count": 1},
            {"pair_id": "pair_next", "drawing_number": "B", "zone_count": 2},
        ]
        workbench._drawing_rows = rows
        for row in rows:
            item = QListWidgetItem(str(row["drawing_number"]))
            item.setData(Qt.UserRole, row)
            workbench.drawing_list_v2.addItem(item)
        workbench.drawing_list_v2.setCurrentRow(0)
        workbench._active_row = rows[0]
        record = ReviewStateRecord(pair_id="pair_next", zone_id="z1", status="confirmed")
        workbench._review_records_v2[record.key] = record

        def fail_overlay_load(_pair_id):
            raise AssertionError("auto-advance should use declared counts before loading overlays")

        workbench._viewer_overlays_for_pair_v2 = fail_overlay_load  # type: ignore[method-assign]
        was_blocked = workbench.drawing_list_v2.blockSignals(True)
        try:
            workbench._advance_to_next_unreviewed_zone_v2()
        finally:
            workbench.drawing_list_v2.blockSignals(was_blocked)

        assert workbench.drawing_list_v2.currentRow() == 1
    finally:
        workbench.deleteLater()


def test_zone_clustering_toggle_does_not_sync_load_full_paged_overlays(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._active_row = {"pair_id": "pair_cluster"}
        workbench._active_overlays_by_zone = {
            "z1": {"pair_id": "pair_cluster", "zone_id": "z1", "bbox": [0, 0, 1, 1]},
        }

        def fail_overlay_load(_pair_id):
            raise AssertionError("cluster toggle should rebuild from active overlays or defer")

        workbench._viewer_overlays_for_pair_v2 = fail_overlay_load  # type: ignore[method-assign]
        workbench._on_toggle_zone_clustering_v2(False)

        leaf_ids = [str(item.data(0, Qt.UserRole) or "") for item in workbench._zone_leaf_items_v2()]
        assert leaf_ids == ["z1"]
    finally:
        workbench.deleteLater()


def test_lightweight_pair_render_start_does_not_sync_load_overlay_json(qapp, tmp_path, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    created: list[dict] = []
    started: list[bool] = []

    class FakeSignal:
        def connect(self, _callback):
            return None

    class FakePairPreviewRenderWorker:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.finished = FakeSignal()
            self.error = FakeSignal()

        def isRunning(self):
            return False

        def start(self):
            started.append(True)

    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    before.write_text("0\nEOF\n", encoding="utf-8")
    after.write_text("0\nEOF\n", encoding="utf-8")

    monkeypatch.setattr(dcw, "PairPreviewRenderWorker", FakePairPreviewRenderWorker)
    workbench = DrawingCompareWorkbenchV2()
    try:
        def fail_overlay_load(_pair_id):
            raise AssertionError("lightweight render start should not synchronously read overlay_json")

        workbench._viewer_root = tmp_path
        workbench._viewer_manifest = {}
        workbench._viewer_overlays_for_pair_v2 = fail_overlay_load  # type: ignore[method-assign]
        workbench._start_pair_render_v2(
            "pair",
            {"pair_id": "pair", "source_a": str(before), "source_b": str(after)},
            {"pair_id": "pair"},
        )

        assert started == [True]
        assert created and created[0]["pair_id"] == "pair"
    finally:
        workbench.deleteLater()


def test_pdf_page_navigation_defers_missing_full_overlay_load_to_worker(qapp, tmp_path, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.viewer_perf_summary import summarize_viewer_perf

    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_IDLE_DELAY_MS", 1)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_ZONE_THRESHOLD", 3)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_ITEM_LIMIT", 2)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_TIME_BUDGET_MS", 1000.0)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_DELAY_MS", 0)
    overlays = [
        {
            "zone_id": f"page{i % 2}_{i}",
            "bbox": [i, i, i + 1, i + 1],
            "old_bbox": [i, i, i + 1, i + 1],
            "raw_change_count": 1,
            "change_type": "modified",
            "page_a": i % 2,
            "page_b": i % 2,
        }
        for i in range(8)
    ]
    overlay_path = tmp_path / "page_nav_overlays.json"
    overlay_path.write_text(json.dumps({"overlays": overlays}, ensure_ascii=False), encoding="utf-8")

    workbench = DrawingCompareWorkbenchV2()
    lightweight_loads: list[tuple[str, int, int]] = []
    try:
        def fail_sync_overlay_load(_pair_id):
            raise AssertionError("PDF page navigation should not synchronously read overlay_json")

        workbench._viewer_root = tmp_path
        workbench._active_row = {"pair_id": "pair", "top_issues": [], "zone_count": len(overlays)}
        workbench._viewer_pairs_by_id = {
            "pair": {
                "pair_id": "pair",
                "overlay_json": str(overlay_path),
                "page_a": 0,
                "page_b": 0,
                "source_a": str(tmp_path / "before.pdf"),
                "source_b": str(tmp_path / "after.pdf"),
                "page_match_pairs": [
                    {"page_a": 0, "page_b": 0, "status": "auto_confirmed", "score": 1.0},
                    {"page_a": 1, "page_b": 1, "status": "auto_confirmed", "score": 1.0},
                ],
            },
        }
        workbench._active_all_overlays_by_zone = {}
        workbench._viewer_overlays_for_pair_v2 = fail_sync_overlay_load  # type: ignore[method-assign]
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(enabled=False, use_embedding=False, use_llm=False)  # type: ignore[method-assign]
        workbench._schedule_lightweight_pair_load_v2 = (  # type: ignore[method-assign]
            lambda pair_id, viewer_pair: lightweight_loads.append((
                str(pair_id),
                int(viewer_pair.get("page_a") or 0),
                int(viewer_pair.get("page_b") or 0),
            ))
        )

        workbench._show_pdf_page_pair_v2(1, 1)

        assert lightweight_loads == [("pair", 1, 1)]
        assert workbench.zone_list_v2.topLevelItemCount() == 0

        deadline = time.time() + 3.0
        while time.time() < deadline:
            qapp.processEvents()
            if (
                workbench._full_zone_tree_chunk_state_v2 is None
                and workbench._full_zone_tree_overlay_worker_v2 is None
                and workbench._full_zone_tree_plan_worker_v2 is None
                and not workbench._pending_full_zone_tree_pair_id_v2
            ):
                break
            time.sleep(0.001)
        qapp.processEvents()

        leaf_ids = [str(item.data(0, Qt.UserRole) or "") for item in workbench._zone_leaf_items_v2()]
        assert len(leaf_ids) == 4
        assert all(zone_id.startswith("page1_") for zone_id in leaf_ids)
        summary = summarize_viewer_perf(tmp_path)
        assert summary["pdf_page_navigation_deferred_count"] == 1
        assert summary["full_tree_overlay_load_worker_count"] == 1
        assert summary["full_tree_plan_build_worker_count"] == 1
    finally:
        workbench.deleteLater()


def test_push_overlays_to_lightweight_does_not_sync_load_when_active_overlays_empty(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        def fail_sync_overlay_load(_pair_id):
            raise AssertionError("empty active overlays should not trigger synchronous overlay_json load")

        workbench._active_row = {"pair_id": "pair"}
        workbench._active_overlays_by_zone = {}
        workbench._viewer_overlays_for_pair_v2 = fail_sync_overlay_load  # type: ignore[method-assign]

        workbench._push_overlays_to_lightweight_v2("pair")
    finally:
        workbench.deleteLater()


def test_push_overlays_to_lightweight_does_not_full_load_inactive_pair(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        def fail_sync_overlay_load(_pair_id):
            raise AssertionError("inactive lightweight push should not materialize full overlays")

        workbench._active_row = {"pair_id": "active"}
        workbench._viewer_pairs_by_id = {"inactive": {"pair_id": "inactive"}}
        workbench._viewer_overlays_for_pair_v2 = fail_sync_overlay_load  # type: ignore[method-assign]

        workbench._push_overlays_to_lightweight_v2("inactive")
    finally:
        workbench.deleteLater()


def test_pdf_page_step_path_keeps_only_final_page_after_rapid_navigation(qapp, tmp_path, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.viewer_perf_summary import summarize_viewer_perf

    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_IDLE_DELAY_MS", 1)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_ZONE_THRESHOLD", 3)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_ITEM_LIMIT", 2)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_TIME_BUDGET_MS", 1000.0)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_DELAY_MS", 0)
    overlays = [
        {
            "zone_id": f"page{i % 3}_{i}",
            "bbox": [i, i, i + 1, i + 1],
            "old_bbox": [i, i, i + 1, i + 1],
            "raw_change_count": 1,
            "change_type": "modified",
            "page_a": i % 3,
            "page_b": i % 3,
        }
        for i in range(9)
    ]
    overlay_path = tmp_path / "rapid_page_nav_overlays.json"
    overlay_path.write_text(json.dumps({"overlays": overlays}, ensure_ascii=False), encoding="utf-8")

    workbench = DrawingCompareWorkbenchV2()
    lightweight_loads: list[tuple[str, int, int]] = []
    try:
        def fail_sync_overlay_load(_pair_id):
            raise AssertionError("rapid PDF page steps should not synchronously read overlay_json")

        workbench._viewer_root = tmp_path
        workbench._active_row = {"pair_id": "pair", "top_issues": [], "zone_count": len(overlays)}
        workbench._viewer_pairs_by_id = {
            "pair": {
                "pair_id": "pair",
                "overlay_json": str(overlay_path),
                "page_a": 0,
                "page_b": 0,
                "source_a": str(tmp_path / "before.pdf"),
                "source_b": str(tmp_path / "after.pdf"),
                "page_match_pairs": [
                    {"page_a": 0, "page_b": 0, "status": "auto_confirmed", "score": 1.0},
                    {"page_a": 1, "page_b": 1, "status": "auto_confirmed", "score": 1.0},
                    {"page_a": 2, "page_b": 2, "status": "auto_confirmed", "score": 1.0},
                ],
            },
        }
        workbench._active_pdf_page_index_v2 = 0
        workbench._active_all_overlays_by_zone = {}
        workbench._active_overlays_by_zone = {}
        workbench._viewer_overlays_for_pair_v2 = fail_sync_overlay_load  # type: ignore[method-assign]
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(enabled=False, use_embedding=False, use_llm=False)  # type: ignore[method-assign]
        workbench._schedule_lightweight_pair_load_v2 = (  # type: ignore[method-assign]
            lambda pair_id, viewer_pair: lightweight_loads.append((
                str(pair_id),
                int(viewer_pair.get("page_a") or 0),
                int(viewer_pair.get("page_b") or 0),
            ))
        )

        workbench._step_pdf_page_pair_v2(+1)
        workbench._step_pdf_page_pair_v2(+1)

        assert lightweight_loads == [("pair", 1, 1), ("pair", 2, 2)]
        viewer_pair = workbench._viewer_pairs_by_id["pair"]
        assert workbench._active_pdf_page_index_v2 == 2
        assert viewer_pair["page_a"] == 2
        assert viewer_pair["page_b"] == 2

        deadline = time.time() + 3.0
        while time.time() < deadline:
            qapp.processEvents()
            if (
                workbench._full_zone_tree_chunk_state_v2 is None
                and workbench._full_zone_tree_overlay_worker_v2 is None
                and workbench._full_zone_tree_plan_worker_v2 is None
                and not workbench._pending_full_zone_tree_pair_id_v2
            ):
                break
            time.sleep(0.001)
        qapp.processEvents()

        leaf_ids = [str(item.data(0, Qt.UserRole) or "") for item in workbench._zone_leaf_items_v2()]
        assert len(leaf_ids) == 3
        assert all(zone_id.startswith("page2_") for zone_id in leaf_ids)
        assert set(workbench._active_overlays_by_zone) == set(leaf_ids)
        assert all(
            int(overlay.get("page_a")) == 2 and int(overlay.get("page_b")) == 2
            for overlay in workbench._active_overlays_by_zone.values()
        )
        summary = summarize_viewer_perf(tmp_path)
        assert summary["pdf_page_navigation_deferred_count"] == 2
        assert summary["full_tree_overlay_load_worker_count"] == 1
        assert summary["full_tree_plan_build_worker_count"] == 1
    finally:
        workbench.deleteLater()


def test_pair_render_finished_preserves_current_pdf_page_after_navigation(qapp, tmp_path) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    overlays = [
        {
            "zone_id": f"page{i % 2}_{i}",
            "bbox": [i, i, i + 1, i + 1],
            "old_bbox": [i, i, i + 1, i + 1],
            "raw_change_count": 1,
            "change_type": "modified",
            "page_a": i % 2,
            "page_b": i % 2,
        }
        for i in range(8)
    ]
    before_pdf = tmp_path / "before.pdf"
    after_pdf = tmp_path / "after.pdf"
    before_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    after_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    before_png = tmp_path / "before.png"
    after_png = tmp_path / "after.png"
    before_png.write_bytes(b"")
    after_png.write_bytes(b"")

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._viewer_root = tmp_path
        workbench._active_row = {"pair_id": "pair", "top_issues": [], "zone_count": len(overlays)}
        workbench._active_pdf_page_index_v2 = 1
        workbench._viewer_pairs_by_id = {
            "pair": {
                "pair_id": "pair",
                "source_a": str(before_pdf),
                "source_b": str(after_pdf),
                "before_image": str(before_png),
                "after_image": str(after_png),
                "render_status": "rendered",
                "page_a": 1,
                "page_b": 1,
                "page_match_pairs": [
                    {"page_a": 0, "page_b": 0, "status": "auto_confirmed", "score": 1.0},
                    {"page_a": 1, "page_b": 1, "status": "auto_confirmed", "score": 1.0},
                ],
            },
        }
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(enabled=False, use_embedding=False, use_llm=False)  # type: ignore[method-assign]
        stale_render_pair = {
            "pair_id": "pair",
            "source_a": str(before_pdf),
            "source_b": str(after_pdf),
            "before_image": str(before_png),
            "after_image": str(after_png),
            "render_status": "rendered",
            "page_a": 0,
            "page_b": 0,
            "page_match_pairs": [
                {"page_a": 0, "page_b": 0, "status": "auto_confirmed", "score": 1.0},
                {"page_a": 1, "page_b": 1, "status": "auto_confirmed", "score": 1.0},
            ],
        }

        workbench._on_pair_render_finished_v2("pair", stale_render_pair, overlays)

        viewer_pair = workbench._viewer_pairs_by_id["pair"]
        assert viewer_pair["page_a"] == 1
        assert viewer_pair["page_b"] == 1
        leaf_ids = [str(item.data(0, Qt.UserRole) or "") for item in workbench._zone_leaf_items_v2()]
        assert len(leaf_ids) == 4
        assert all(zone_id.startswith("page1_") for zone_id in leaf_ids)
        assert all(
            int(overlay.get("page_a")) == 1 and int(overlay.get("page_b")) == 1
            for overlay in workbench._active_overlays_by_zone.values()
        )
    finally:
        workbench.deleteLater()


def test_active_pdf_page_pair_resets_viewer_pair_to_first_match(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        viewer_pair = {
            "page_a": 1,
            "page_b": 1,
            "page_match_pairs": [
                {"page_a": 0, "page_b": 0, "status": "auto_confirmed", "score": 1.0},
                {"page_a": 1, "page_b": 1, "status": "auto_confirmed", "score": 1.0},
            ],
        }
        workbench._active_pdf_page_index_v2 = 0

        workbench._apply_active_pdf_page_pair_to_viewer_pair_v2(viewer_pair)

        assert viewer_pair["page_a"] == 0
        assert viewer_pair["page_b"] == 0
        assert workbench._active_pdf_page_index_v2 == 0
    finally:
        workbench.deleteLater()


def test_viewer_overlay_cache_is_bounded_and_records_evictions(qapp) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        for idx in range(dcw.GUI_OVERLAY_CACHE_PAIR_LIMIT + 3):
            workbench._cache_viewer_overlays_v2(
                f"pair_{idx}",
                [{"zone_id": f"z{idx}"}],
            )

        assert len(workbench._viewer_overlay_cache) <= dcw.GUI_OVERLAY_CACHE_PAIR_LIMIT
        assert workbench._viewer_overlay_cache_evictions_v2 >= 3
        assert "pair_0" not in workbench._viewer_overlay_cache
    finally:
        workbench.deleteLater()


def test_viewer_overlay_cache_is_byte_bounded(qapp, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    monkeypatch.setattr(dcw, "GUI_OVERLAY_CACHE_PAIR_LIMIT", 10)
    monkeypatch.setattr(dcw, "GUI_OVERLAY_CACHE_BYTE_LIMIT", 2500)
    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._cache_viewer_overlays_v2(
            "large_1",
            [{"zone_id": f"z{i}", "label": "x" * 400} for i in range(4)],
        )
        workbench._cache_viewer_overlays_v2(
            "large_2",
            [{"zone_id": f"z{i}", "label": "y" * 400} for i in range(4)],
        )

        assert len(workbench._viewer_overlay_cache) < 2
        assert workbench._viewer_overlay_cache_total_bytes_v2 <= dcw.GUI_OVERLAY_CACHE_BYTE_LIMIT
        assert workbench._viewer_overlay_cache_evictions_v2 >= 1
        assert sum(workbench._viewer_overlay_cache_bytes_by_pair_v2.values()) == workbench._viewer_overlay_cache_total_bytes_v2
    finally:
        workbench.deleteLater()


def test_gui_overlay_cache_byte_limit_prefers_specific_env(monkeypatch) -> None:
    import src.gui.drawing_compare_workbench as dcw

    monkeypatch.setenv("DRAWING_COMPARE_GUI_OVERLAY_CACHE_MB", "3")
    monkeypatch.setenv("DRAWING_COMPARE_RENDER_CACHE_MB", "99")
    reloaded = importlib.reload(dcw)
    try:
        assert reloaded.GUI_OVERLAY_CACHE_BYTE_LIMIT == 3 * 1024 * 1024
    finally:
        monkeypatch.delenv("DRAWING_COMPARE_GUI_OVERLAY_CACHE_MB", raising=False)
        monkeypatch.delenv("DRAWING_COMPARE_RENDER_CACHE_MB", raising=False)
        importlib.reload(dcw)


def test_gui_overlay_cache_byte_limit_falls_back_to_shared_env(monkeypatch) -> None:
    import src.gui.drawing_compare_workbench as dcw

    monkeypatch.delenv("DRAWING_COMPARE_GUI_OVERLAY_CACHE_MB", raising=False)
    monkeypatch.setenv("DRAWING_COMPARE_RENDER_CACHE_MB", "5")
    reloaded = importlib.reload(dcw)
    try:
        assert reloaded.GUI_OVERLAY_CACHE_BYTE_LIMIT == 5 * 1024 * 1024
    finally:
        monkeypatch.delenv("DRAWING_COMPARE_RENDER_CACHE_MB", raising=False)
        importlib.reload(dcw)


def test_viewer_overlay_cache_evict_event_uses_overlay_cache_fields(
    qapp,
    tmp_path,
    monkeypatch,
) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    monkeypatch.setattr(dcw, "GUI_OVERLAY_CACHE_PAIR_LIMIT", 10)
    monkeypatch.setattr(dcw, "GUI_OVERLAY_CACHE_BYTE_LIMIT", 2500)
    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._viewer_root = tmp_path
        workbench._cache_viewer_overlays_v2(
            "large_1",
            [{"zone_id": f"z{i}", "label": "x" * 400} for i in range(4)],
        )
        workbench._cache_viewer_overlays_v2(
            "large_2",
            [{"zone_id": f"z{i}", "label": "y" * 400} for i in range(4)],
        )

        events = [
            json.loads(line)
            for line in (tmp_path / "viewer_perf.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        evict = [event for event in events if event.get("event") == "viewer_overlay_cache_evict"][-1]
        assert evict["overlay_cache_byte_limit"] == 2500
        assert evict["overlay_cache_pair_limit"] == 10
        assert evict["overlay_cache_evicted_bytes"] > 0
        assert evict["overlay_cache_eviction_count"] >= 1
        assert evict["overlay_cache_eviction_reason"] == "byte_limit"
        assert "cache_byte_limit" not in evict
    finally:
        workbench.deleteLater()


def test_selection_lod_tiles_disabled_for_first_review_contract(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._result = SimpleNamespace(package_complete=False, first_review_metadata={})
        workbench._viewer_manifest = {"build_lod_tiles": True}
        assert workbench._selection_build_lod_tiles_enabled_v2() is False

        workbench._result = SimpleNamespace(
            package_complete=True,
            first_review_metadata={"deferred_outputs": {"lod_tiles": "deferred"}},
        )
        assert workbench._selection_build_lod_tiles_enabled_v2() is False

        workbench._result = SimpleNamespace(
            package_complete=True,
            first_review_metadata={"deferred_outputs": {"lod_tiles": "completed"}},
        )
        workbench._viewer_manifest = {"build_lod_tiles": True}
        assert workbench._selection_build_lod_tiles_enabled_v2() is True
    finally:
        workbench.deleteLater()


def test_pair_preview_worker_can_skip_lod_tile_cache(qapp, tmp_path, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import PairPreviewRenderWorker

    viewer_root = tmp_path / "viewer"
    overlay_dir = viewer_root / "overlays"
    overlay_dir.mkdir(parents=True)
    overlay_path = overlay_dir / "pair.json"
    overlay_path.write_text(
        '{"overlays":[{"zone_id":"z1","bbox":[0,0,10,10],"old_bbox":[0,0,10,10]}]}',
        encoding="utf-8",
    )
    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    before.write_text("0\nEOF\n", encoding="utf-8")
    after.write_text("0\nEOF\n", encoding="utf-8")

    monkeypatch.setattr(
        dcw,
        "_render_pair_backgrounds_with_timeout",
        lambda **_kwargs: {
            "render_status": "rendered",
            "before_image": str(tmp_path / "before.png"),
            "after_image": str(tmp_path / "after.png"),
            "before_transform": {
                "min_x": 0,
                "min_y": 0,
                "max_x": 10,
                "max_y": 10,
                "width": 100,
                "height": 100,
            },
            "after_transform": {
                "min_x": 0,
                "min_y": 0,
                "max_x": 10,
                "max_y": 10,
                "width": 100,
                "height": 100,
            },
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        dcw,
        "write_pair_tile_cache",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("tile cache must be skipped")),
    )
    captured = []
    worker = PairPreviewRenderWorker(
        pair_id="pair",
        viewer_pair={
            "pair_id": "pair",
            "source_a": str(before),
            "source_b": str(after),
            "overlay_json": str(overlay_path),
        },
        dxf_cache_dir=tmp_path / "cache",
        viewer_root=viewer_root,
        build_lod_tiles=False,
    )
    worker.finished.connect(
        lambda pair_id, viewer_pair, overlays: captured.append(
            (pair_id, viewer_pair, overlays)
        )
    )

    worker.run()

    assert captured
    assert captured[0][1]["lod_tile_count"] == 0
    assert captured[0][1]["overlay_tile_count"] == 0
    assert captured[0][1]["tile_manifest"] == ""


def test_pair_preview_worker_uses_paged_overlay_store_for_pdf_without_legacy_json(qapp, tmp_path, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import PairPreviewRenderWorker
    from src.services.comparison.viewer_overlay_pages import write_overlay_page_store

    viewer_root = tmp_path / "viewer"
    overlay_dir = viewer_root / "overlays"
    overlay_dir.mkdir(parents=True)
    overlay_path = overlay_dir / "pair.json"
    overlay_path.write_text(
        '{"overlay_total_count":4,"overlays":[{"zone_id":"legacy"}]}',
        encoding="utf-8",
    )
    overlays = [
        {"pair_id": "pair", "zone_id": "p0", "bbox": [0, 0, 10, 10], "old_bbox": [0, 0, 10, 10], "page_a": 0, "page_b": 0},
        {"pair_id": "pair", "zone_id": "p1a", "bbox": [1, 1, 11, 11], "old_bbox": [1, 1, 11, 11], "page_a": 1, "page_b": 1},
        {"pair_id": "pair", "zone_id": "p1b", "bbox": [2, 2, 12, 12], "old_bbox": [2, 2, 12, 12], "page_a": 1, "page_b": 1},
        {"pair_id": "pair", "zone_id": "p2", "bbox": [3, 3, 13, 13], "old_bbox": [3, 3, 13, 13], "page_a": 2, "page_b": 2},
    ]
    summary = write_overlay_page_store(
        pair_id="pair",
        overlays=overlays,
        output_root=viewer_root / "overlay_pages",
        page_size=2,
    )
    before = tmp_path / "before.pdf"
    after = tmp_path / "after.pdf"
    before.write_bytes(b"%PDF-1.4\n%%EOF\n")
    after.write_bytes(b"%PDF-1.4\n%%EOF\n")

    monkeypatch.setattr(
        dcw,
        "_render_pair_backgrounds_with_timeout",
        lambda **_kwargs: {
            "render_status": "rendered",
            "before_image": str(tmp_path / "before.png"),
            "after_image": str(tmp_path / "after.png"),
            "before_transform": {"min_x": 0, "min_y": 0, "max_x": 20, "max_y": 20, "width": 200, "height": 200},
            "after_transform": {"min_x": 0, "min_y": 0, "max_x": 20, "max_y": 20, "width": 200, "height": 200},
            "warnings": [],
        },
    )
    legacy_reads = []
    original_read_json = dcw._read_json_file

    def counted_read_json(path):
        if Path(path) == overlay_path:
            legacy_reads.append(str(path))
        return original_read_json(path)

    monkeypatch.setattr(dcw, "_read_json_file", counted_read_json)
    captured = []
    worker = PairPreviewRenderWorker(
        pair_id="pair",
        viewer_pair={
            "pair_id": "pair",
            "source_a": str(before),
            "source_b": str(after),
            "overlay_json": str(overlay_path),
            "overlay_pages_manifest": str(summary.manifest_path),
            "page_a": 1,
            "page_b": 1,
        },
        dxf_cache_dir=tmp_path / "cache",
        viewer_root=viewer_root,
        build_lod_tiles=False,
    )
    worker.finished.connect(lambda pair_id, viewer_pair, overlays: captured.append((pair_id, viewer_pair, overlays)))

    worker.run()

    assert legacy_reads == []
    assert captured
    assert [overlay["zone_id"] for overlay in captured[0][2]] == ["p1a", "p1b"]
    assert captured[0][1]["_overlay_materialization_scope"] == "visible_pdf_page"
    assert captured[0][1]["_overlay_page_files_read"] == 2


def test_pair_preview_worker_uses_visible_first_lod_tiles(qapp, tmp_path, monkeypatch) -> None:
    from PIL import Image

    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import PairPreviewRenderWorker

    viewer_root = tmp_path / "viewer"
    overlay_dir = viewer_root / "overlays"
    overlay_dir.mkdir(parents=True)
    overlay_path = overlay_dir / "pair.json"
    overlay_path.write_text(
        json.dumps(
            {
                "overlays": [
                    {
                        "zone_id": "z1",
                        "selected_for_review": True,
                        "bbox": [10, 10, 20, 20],
                        "old_bbox": [10, 10, 20, 20],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    before.write_text("0\nEOF\n", encoding="utf-8")
    after.write_text("0\nEOF\n", encoding="utf-8")
    before_image = tmp_path / "before.png"
    after_image = tmp_path / "after.png"
    Image.new("RGB", (2048, 2048), "white").save(before_image)
    Image.new("RGB", (2048, 2048), "white").save(after_image)

    monkeypatch.setattr(
        dcw,
        "_render_pair_backgrounds_with_timeout",
        lambda **_kwargs: {
            "render_status": "rendered",
            "before_image": str(before_image),
            "after_image": str(after_image),
            "before_transform": {
                "min_x": 0,
                "min_y": 0,
                "max_x": 100,
                "max_y": 100,
                "width": 2048,
                "height": 2048,
            },
            "after_transform": {
                "min_x": 0,
                "min_y": 0,
                "max_x": 100,
                "max_y": 100,
                "width": 2048,
                "height": 2048,
            },
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        dcw,
        "write_pair_tile_cache",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("full tile pyramid must not run")),
    )
    captured = []
    worker = PairPreviewRenderWorker(
        pair_id="pair",
        viewer_pair={
            "pair_id": "pair",
            "source_a": str(before),
            "source_b": str(after),
            "overlay_json": str(overlay_path),
        },
        dxf_cache_dir=tmp_path / "cache",
        viewer_root=viewer_root,
        build_lod_tiles=True,
    )
    worker.finished.connect(
        lambda pair_id, viewer_pair, overlays: captured.append(
            (pair_id, viewer_pair, overlays)
        )
    )

    worker.run()

    assert captured
    viewer_pair = captured[0][1]
    manifest = json.loads((tmp_path / "viewer" / "tiles" / "pair" / "tile_manifest.json").read_text(encoding="utf-8"))
    assert viewer_pair["lod_tile_count"] == manifest["materialized_tile_count"]
    assert manifest["generation_mode"] == "visible_first"
    assert manifest["pyramid_complete"] is False
    assert manifest["materialized_tile_count"] < manifest["planned_tile_count"]
    assert manifest["deferred_lod_tiles"] is True


def test_visible_tile_window_worker_accumulates_pan_window(qapp, tmp_path) -> None:
    from PIL import Image

    from src.gui.drawing_compare_workbench import (
        GPU_VIEWER_MAX_VISIBLE_OVERLAYS,
        GPU_VIEWER_MEMORY_BUDGET_MB,
        GPU_VIEWER_TILE_SIZE,
        VisibleTileWindowWorker,
    )
    from src.services.comparison.viewer_tile_cache import (
        ViewerTileCacheOptions,
        visible_tile_model,
        write_pair_visible_tile_cache,
    )

    viewer_root = tmp_path / "viewer"
    before_image = tmp_path / "before.png"
    after_image = tmp_path / "after.png"
    Image.new("RGB", (2048, 2048), "white").save(before_image)
    Image.new("RGB", (2048, 2048), "white").save(after_image)
    options = ViewerTileCacheOptions(
        tile_size=GPU_VIEWER_TILE_SIZE,
        max_visible_overlays=GPU_VIEWER_MAX_VISIBLE_OVERLAYS,
        viewer_memory_budget_mb=GPU_VIEWER_MEMORY_BUDGET_MB,
    )
    first_manifest = write_pair_visible_tile_cache(
        pair_uuid="pair",
        before_image=str(before_image),
        after_image=str(after_image),
        overlays=[],
        tile_root=viewer_root / "tiles",
        overlay_tile_root=viewer_root / "overlay_tiles",
        options=options,
        viewport_rect={"x": 0.0, "y": 0.0, "width": 512.0, "height": 512.0},
        zoom=1.0,
        prefetch_radius=0,
        cache_key="same-source",
    )

    captured: list[tuple[str, int, dict]] = []
    worker = VisibleTileWindowWorker(
        pair_id="pair",
        generation=7,
        viewer_pair={
            "pair_id": "pair",
            "source_a": str(before_image),
            "source_b": str(after_image),
            "before_image": str(before_image),
            "after_image": str(after_image),
        },
        overlays=[],
        viewer_root=viewer_root,
        viewer_cache_root=viewer_root,
        viewport_rect={"x": 1536.0, "y": 1536.0, "width": 512.0, "height": 512.0},
        zoom=1.0,
        cache_key="same-source",
    )
    worker.finished.connect(lambda pair_id, generation, manifest: captured.append((pair_id, generation, manifest)))

    worker.run()

    assert captured
    _, generation, manifest = captured[0]
    assert generation == 7
    assert manifest["materialized_tile_count"] > first_manifest["materialized_tile_count"]
    assert manifest["planned_tile_count"] == first_manifest["planned_tile_count"]
    assert manifest["pyramid_complete"] is False
    first_window = visible_tile_model(
        pair_manifest=manifest,
        side="after",
        viewer_root=viewer_root,
        viewport_rect={"x": 0.0, "y": 0.0, "width": 512.0, "height": 512.0},
        zoom=1.0,
        prefetch_radius=0,
    )
    pan_window = visible_tile_model(
        pair_manifest=manifest,
        side="after",
        viewer_root=viewer_root,
        viewport_rect={"x": 1536.0, "y": 1536.0, "width": 512.0, "height": 512.0},
        zoom=1.0,
        prefetch_radius=1,
    )
    assert first_window["status"] == "tile_ready"
    assert pan_window["status"] == "tile_ready"

    global_manifest = json.loads((viewer_root / "tiles_manifest.json").read_text(encoding="utf-8"))
    assert global_manifest["pairs"]["pair"]["materialized_tile_count"] == manifest["materialized_tile_count"]
    assert (viewer_root / "viewer_perf.jsonl").exists()


def test_lightweight_visible_tile_request_maps_world_camera_to_pixel_rect(qapp, tmp_path) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    class FakeLightweightViewport:
        def width(self) -> int:
            return 512

        def visible_world_rect(self, center_x=None, center_y=None, units_per_pixel=None):
            return (512.0, 512.0, 1024.0, 1024.0)

    workbench = DrawingCompareWorkbenchV2()
    captured: list[tuple[str, dict, float]] = []
    try:
        workbench._viewer_root = tmp_path / "viewer"
        workbench._active_row = {"pair_id": "pair"}
        workbench._viewer_pairs_by_id = {
            "pair": {
                "pair_id": "pair",
                "after_transform": {
                    "min_x": 0.0,
                    "min_y": 0.0,
                    "max_x": 2048.0,
                    "max_y": 2048.0,
                    "img_width": 2048.0,
                    "img_height": 2048.0,
                },
            }
        }
        workbench.preview_after_lightweight_v2 = FakeLightweightViewport()  # type: ignore[assignment]
        workbench._is_lightweight_viewer_active_v2 = lambda: True  # type: ignore[method-assign]
        workbench._schedule_visible_tile_window_v2 = (  # type: ignore[method-assign]
            lambda pair_id, rect, zoom: captured.append((pair_id, rect, zoom))
        )

        workbench._schedule_lightweight_visible_tile_window_v2("after", 768.0, 768.0, 1.0)

        assert captured
        pair_id, rect, zoom = captured[0]
        assert pair_id == "pair"
        assert rect == {"x": 512.0, "y": 1024.0, "width": 512.0, "height": 512.0}
        assert zoom == 1.0
    finally:
        workbench.deleteLater()


def test_initial_zone_selection_defers_crop_and_vector_until_after_pair_paint(qapp, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    scheduled: list[tuple[int, object]] = []
    workbench = DrawingCompareWorkbenchV2()
    monkeypatch.setattr(
        dcw,
        "QTimer",
        SimpleNamespace(
            singleShot=lambda delay, callback: scheduled.append((delay, callback))
        ),
    )
    try:
        workbench._active_row = {"pair_id": "pair"}
        workbench._active_overlays_by_zone = {
            "z1": {"zone_id": "z1", "bbox": [0, 0, 10, 10], "old_bbox": [0, 0, 10, 10]}
        }
        workbench._defer_next_initial_zone_heavy_render_v2 = ("pair", "z1")
        workbench._set_lightweight_zone_side_messages_v2 = lambda _zone_id: None  # type: ignore[method-assign]
        workbench._focus_lightweight_on_zone_v2 = lambda _zone_id: None  # type: ignore[method-assign]
        focus_requests: list[str] = []
        workbench._request_zone_focus_v2 = lambda zone_id: focus_requests.append(zone_id)  # type: ignore[method-assign]
        workbench._zone_detail_text_v2 = lambda _zone_id: ""  # type: ignore[method-assign]
        workbench._load_current_zone_memo_v2 = lambda: None  # type: ignore[method-assign]
        workbench._record_zone_selection_event_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

        calls: list[tuple[str, str]] = []
        workbench._start_zone_crop_render_v2 = lambda zone_id: calls.append(("crop", zone_id))  # type: ignore[method-assign]
        workbench._start_zone_vector_render_v2 = lambda pair_id, zone_id: calls.append(("vector", f"{pair_id}:{zone_id}"))  # type: ignore[method-assign]

        item = QTreeWidgetItem(["z1"])
        item.setData(0, Qt.UserRole, "z1")

        workbench._on_zone_selected_v2(item)

        assert calls == []
        assert focus_requests == []
        assert scheduled
        assert scheduled[-1][0] == dcw.GUI_INITIAL_ZONE_HEAVY_RENDER_DELAY_MS

        scheduled[-1][1]()

        assert calls == [("crop", "z1")]
        assert focus_requests == []
    finally:
        workbench.deleteLater()


def test_zone_selection_starts_crop_only_and_defers_focus_vector_until_crop_finished(qapp, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    monkeypatch.setattr(dcw, "DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY", True)
    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._active_row = {"pair_id": "pair"}
        workbench._viewer_pairs_by_id = {
            "pair": {"pair_id": "pair", "source_a": "before.dxf", "source_b": "after.dxf"}
        }
        workbench._active_overlays_by_zone = {
            "z1": {"zone_id": "z1", "bbox": [0, 0, 10, 10], "old_bbox": [0, 0, 10, 10]}
        }
        workbench._set_lightweight_zone_side_messages_v2 = lambda _zone_id: None  # type: ignore[method-assign]
        workbench._focus_lightweight_on_zone_v2 = lambda _zone_id: None  # type: ignore[method-assign]
        workbench._zone_detail_text_v2 = lambda _zone_id: ""  # type: ignore[method-assign]
        workbench._load_current_zone_memo_v2 = lambda: None  # type: ignore[method-assign]
        workbench._record_zone_selection_event_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._set_preview_status_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._refresh_zone_vector_button_state_v2 = lambda: None  # type: ignore[method-assign]

        crop_calls: list[str] = []
        focus_requests: list[str] = []
        vector_calls: list[tuple[str, str]] = []
        workbench._start_zone_crop_render_v2 = lambda zone_id: crop_calls.append(zone_id)  # type: ignore[method-assign]
        workbench._request_zone_focus_v2 = lambda zone_id: focus_requests.append(zone_id)  # type: ignore[method-assign]
        workbench._start_zone_vector_render_v2 = lambda pair_id, zone_id: vector_calls.append((pair_id, zone_id))  # type: ignore[method-assign]

        item = QTreeWidgetItem(["z1"])
        item.setData(0, Qt.UserRole, "z1")
        workbench._on_zone_selected_v2(item)

        assert crop_calls == ["z1"]
        assert focus_requests == []
        assert vector_calls == []

        request_id = workbench._active_zone_render_request_id_v2("pair", "z1")
        workbench._on_zone_crop_render_finished_v2(
            "pair",
            "z1",
            {
                "request_id": request_id,
                "elapsed_ms": 1.0,
                "render_lifecycle": "ready",
                "visual_fidelity": "cad_render",
            },
            {"pair_id": "pair", "source_a": "before.dxf", "source_b": "after.dxf"},
            [{"zone_id": "z1", "bbox": [0, 0, 10, 10], "old_bbox": [0, 0, 10, 10]}],
        )

        assert focus_requests == ["z1"]
        assert vector_calls == [("pair", "z1")]
    finally:
        workbench.deleteLater()


def test_zone_crop_finished_ignores_stale_request_id_before_state_mutation(qapp, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    monkeypatch.setattr(dcw, "DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY", True)
    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._active_row = {"pair_id": "pair"}
        workbench._active_zone_id = "z1"
        original_pair = {"pair_id": "pair", "marker": "original"}
        original_overlay = {"zone_id": "z1", "marker": "original"}
        workbench._viewer_pairs_by_id = {"pair": dict(original_pair)}
        workbench._active_overlays_by_zone = {"z1": dict(original_overlay)}
        old_request_id = workbench._begin_selected_zone_render_request_v2("pair", "z1")
        workbench._begin_selected_zone_render_request_v2("pair", "z1")

        status_calls: list[tuple] = []
        pending_drains: list[str] = []
        workbench._set_preview_status_v2 = lambda *args, **_kwargs: status_calls.append(args)  # type: ignore[method-assign]
        workbench._start_pending_zone_render_v2 = lambda: pending_drains.append("drain")  # type: ignore[method-assign]

        workbench._on_zone_crop_render_finished_v2(
            "pair",
            "z1",
            {"request_id": old_request_id, "elapsed_ms": 1.0},
            {"pair_id": "pair", "marker": "stale"},
            [{"zone_id": "z1", "marker": "stale"}],
        )

        assert workbench._viewer_pairs_by_id["pair"] == original_pair
        assert workbench._active_overlays_by_zone["z1"] == original_overlay
        assert status_calls == []
        assert pending_drains == ["drain"]
    finally:
        workbench.deleteLater()


def test_zone_crop_finished_records_pdf_display_list_perf_metrics(qapp, tmp_path) -> None:
    import json

    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._viewer_root = tmp_path
        workbench._active_row = {"pair_id": "pair"}
        workbench._active_zone_id = "z1"
        workbench._viewer_pairs_by_id = {"pair": {"pair_id": "pair"}}
        workbench._set_preview_status_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._zone_detail_text_v2 = lambda _zone_id: ""  # type: ignore[method-assign]
        workbench._start_selected_zone_deferred_enhancement_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._refresh_viewer_perf_summary_only = lambda: None  # type: ignore[method-assign]
        workbench._start_pending_zone_render_v2 = lambda: None  # type: ignore[method-assign]
        request_id = workbench._begin_selected_zone_render_request_v2("pair", "z1")

        workbench._on_zone_crop_render_finished_v2(
            "pair",
            "z1",
            {
                "request_id": request_id,
                "elapsed_ms": 11.0,
                "cache_hit": False,
                "render_lifecycle": "ready",
                "visual_fidelity": "pdf_render",
                "renderer_backend": "pdf-image-crop",
                "warnings": ["renderer:pdf-display-list-clip"],
                "pdf_display_list_render_count": 2,
                "pdf_display_list_cache_lookup_count": 2,
                "pdf_display_list_cache_hit_count": 1,
                "pdf_display_list_cache_miss_count": 1,
                "pdf_display_list_cache_eviction_count": 0,
                "pdf_display_list_cache_total_estimated_bytes": 123456,
                "pdf_display_list_cache_byte_limit": 999999,
                "pdf_display_list_worker_rss_mb": 77.25,
                "pdf_pil_fallback_count": 0,
                "pdf_display_list_cache": {"render_count": 2},
                "dxf_index_cache_entries": 1,
                "dxf_index_cache_capacity_entries": 8,
                "dxf_index_cache_entry_estimated_bytes_max": 654321,
                "dxf_index_cache_lookup_count": 2,
                "dxf_index_cache_hit_count": 1,
                "dxf_index_cache_miss_count": 1,
                "dxf_index_cache_hit_rate": 0.5,
                "dxf_index_cache_eviction_count": 0,
                "dxf_index_cache_evicted_estimated_bytes": 0,
                "dxf_index_cache_total_estimated_bytes": 654321,
                "dxf_index_cache_byte_limit": 2222222,
                "dxf_index_cache_worker_rss_mb": 88.5,
                "dxf_index_cache": {"entries": 1},
            },
            {"pair_id": "pair"},
            [{"zone_id": "z1"}],
        )

        lines = [
            line
            for line in (tmp_path / "viewer_perf.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        payload = json.loads(lines[-1])
        assert payload["event"] == "zone_crop_render"
        assert payload["renderer_backend"] == "pdf-image-crop"
        assert payload["warnings"] == ["renderer:pdf-display-list-clip"]
        assert payload["pdf_display_list_render_count"] == 2
        assert payload["pdf_display_list_cache_hit_count"] == 1
        assert payload["pdf_display_list_cache_total_estimated_bytes"] == 123456
        assert payload["pdf_display_list_worker_rss_mb"] == 77.25
        assert payload["dxf_index_cache_lookup_count"] == 2
        assert payload["dxf_index_cache_hit_count"] == 1
        assert payload["dxf_index_cache_total_estimated_bytes"] == 654321
        assert payload["dxf_index_cache_worker_rss_mb"] == 88.5
    finally:
        workbench.deleteLater()


def _prepare_zone_finish_workbench(workbench, tmp_path):
    """Wire a workbench so _on_zone_crop_render_finished_v2 runs in isolation."""
    workbench._viewer_root = tmp_path
    workbench._active_row = {"pair_id": "pair"}
    workbench._active_zone_id = "z1"
    workbench._viewer_pairs_by_id = {"pair": {"pair_id": "pair"}}
    workbench._set_preview_status_v2 = lambda *_a, **_k: None  # type: ignore[method-assign]
    workbench._zone_detail_text_v2 = lambda _z: ""  # type: ignore[method-assign]
    workbench._start_selected_zone_deferred_enhancement_v2 = lambda *_a, **_k: None  # type: ignore[method-assign]
    workbench._refresh_viewer_perf_summary_only = lambda: None  # type: ignore[method-assign]
    workbench._start_pending_zone_render_v2 = lambda: None  # type: ignore[method-assign]
    workbench._apply_zone_crop_to_lightweight_v2 = lambda *_a, **_k: None  # type: ignore[method-assign]
    workbench._is_lightweight_viewer_active_v2 = lambda: True  # type: ignore[method-assign]


def _cad_crop_payload(request_id):
    return {
        "request_id": request_id,
        "elapsed_ms": 9.0,
        "cache_hit": False,
        "render_lifecycle": "ready",
        "visual_fidelity": "cad_render",
        "renderer_backend": "cad-background-image-crop",
        "before_image": "b.png",
        "after_image": "a.png",
        "warnings": [],
    }


def test_cad_background_crop_schedules_full_detail_upgrade(qapp, tmp_path, monkeypatch) -> None:
    # ② The simplified fast crop, once shown, schedules a prefer_source_render
    # re-render so text/dims/blocks appear. Verifies the finish path wires it.
    import src.gui.drawing_compare_workbench as dcw

    class _ImmediateTimer:
        @staticmethod
        def singleShot(_ms, callback):
            callback()

    workbench = dcw.DrawingCompareWorkbenchV2()
    try:
        _prepare_zone_finish_workbench(workbench, tmp_path)
        crop_calls: list[tuple] = []
        workbench._start_zone_crop_render_v2 = (  # type: ignore[method-assign]
            lambda zone_id, **kw: crop_calls.append((zone_id, kw))
        )
        request_id = workbench._begin_selected_zone_render_request_v2("pair", "z1")

        # Patch QTimer only now (after the controller captured the real one at
        # construction) so the upgrade callback runs synchronously.
        monkeypatch.setattr(dcw, "QTimer", _ImmediateTimer)
        workbench._on_zone_crop_render_finished_v2(
            "pair", "z1", _cad_crop_payload(request_id),
            {"pair_id": "pair"}, [{"zone_id": "z1"}],
        )

        assert crop_calls == [("z1", {"prefer_source_render": True})]
        # Loop guard recorded the upgraded request.
        assert workbench._zone_full_detail_started_request_v2 == ("pair", "z1", request_id)
    finally:
        workbench.deleteLater()


def test_pdf_crop_does_not_schedule_full_detail_upgrade(qapp, tmp_path, monkeypatch) -> None:
    # PDF crops already show the full visual page; no source upgrade is issued.
    import src.gui.drawing_compare_workbench as dcw

    class _ImmediateTimer:
        @staticmethod
        def singleShot(_ms, callback):  # pragma: no cover - must not fire here
            callback()

    workbench = dcw.DrawingCompareWorkbenchV2()
    try:
        _prepare_zone_finish_workbench(workbench, tmp_path)
        crop_calls: list[tuple] = []
        workbench._start_zone_crop_render_v2 = (  # type: ignore[method-assign]
            lambda zone_id, **kw: crop_calls.append((zone_id, kw))
        )
        request_id = workbench._begin_selected_zone_render_request_v2("pair", "z1")
        payload = _cad_crop_payload(request_id)
        payload.update(
            {"visual_fidelity": "pdf_render", "renderer_backend": "pdf-image-crop"}
        )

        monkeypatch.setattr(dcw, "QTimer", _ImmediateTimer)
        workbench._on_zone_crop_render_finished_v2(
            "pair", "z1", payload, {"pair_id": "pair"}, [{"zone_id": "z1"}],
        )

        assert crop_calls == []
        assert workbench._zone_full_detail_started_request_v2 is None
    finally:
        workbench.deleteLater()


def test_failed_full_detail_upgrade_keeps_fast_crop_out_of_fallback_counts(qapp, tmp_path) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.viewer_perf_summary import summarize_viewer_perf

    workbench = DrawingCompareWorkbenchV2()
    try:
        _prepare_zone_finish_workbench(workbench, tmp_path)
        status_calls: list[tuple] = []
        apply_calls: list[tuple] = []
        drain_calls: list[str] = []
        workbench._set_preview_status_v2 = lambda *args, **_kwargs: status_calls.append(args)  # type: ignore[method-assign]
        workbench._apply_zone_crop_to_lightweight_v2 = lambda *args, **_kwargs: apply_calls.append(args)  # type: ignore[method-assign]
        workbench._start_pending_zone_render_v2 = lambda: drain_calls.append("drain")  # type: ignore[method-assign]
        request_id = workbench._begin_selected_zone_render_request_v2("pair", "z1")

        payload = _cad_crop_payload(request_id)
        payload.update(
            {
                "prefer_source_render": True,
                "render_lifecycle": "fallback_visible",
                "visual_fidelity": "relative_overlay",
                "renderer_backend": "relative-overlay-fallback",
                "reason_code": "source_render_failed",
            }
        )
        workbench._on_zone_crop_render_finished_v2(
            "pair", "z1", payload, {"pair_id": "pair"}, [{"zone_id": "z1"}],
        )

        summary = summarize_viewer_perf(tmp_path)
        assert apply_calls == []
        assert status_calls == []
        assert drain_calls == ["drain"]
        assert workbench._viewer_pairs_by_id["pair"] == {"pair_id": "pair"}
        assert summary["selected_zone_fallback_count"] == 0
        assert summary["zone_crop_count"] == 0
        assert summary["event_count"] == 1
    finally:
        workbench.deleteLater()


def test_full_detail_timeout_keeps_fast_crop_status_and_logs_upgrade_failure(qapp, tmp_path) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        _prepare_zone_finish_workbench(workbench, tmp_path)
        status_calls: list[tuple] = []
        drain_calls: list[str] = []
        workbench._set_preview_status_v2 = lambda *args, **_kwargs: status_calls.append(args)  # type: ignore[method-assign]
        workbench._start_pending_zone_render_v2 = lambda: drain_calls.append("drain")  # type: ignore[method-assign]
        request_id = workbench._begin_selected_zone_render_request_v2("pair", "z1")

        workbench._on_zone_crop_render_error_v2(
            "pair",
            "z1",
            "source render took longer than the full-detail budget",
            "full_detail_render_timeout",
            request_id,
        )

        lines = [
            line
            for line in (tmp_path / "viewer_perf.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        payload = json.loads(lines[-1])
        assert status_calls == []
        assert drain_calls == ["drain"]
        assert payload["event"] == "zone_full_detail_upgrade_failed"
        assert payload["reason_code"] == "full_detail_render_timeout"
        assert payload["render_lifecycle"] == "timeout"
    finally:
        workbench.deleteLater()


def test_redacted_viewer_source_restores_from_compare_summary(qapp, tmp_path) -> None:
    from types import SimpleNamespace

    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    before = tmp_path / "S-REORIGIN_REV0.dxf"
    after = tmp_path / "S-REORIGIN_REV1.dxf"
    before.write_text("0\nEOF\n", encoding="utf-8")
    after.write_text("0\nEOF\n", encoding="utf-8")
    candidate = SimpleNamespace(
        source_a=SimpleNamespace(path=str(before)),
        source_b=SimpleNamespace(path=str(after)),
    )
    result = SimpleNamespace(
        compare_summary=SimpleNamespace(
            items=[SimpleNamespace(candidate=candidate)]
        )
    )

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._result = result
        pair_id = "pair-fe7b"
        import src.services.comparison.pair_identity as pair_identity

        original = pair_identity.candidate_pair_uuid
        pair_identity.candidate_pair_uuid = lambda _candidate: pair_id
        try:
            repaired = workbench._repair_viewer_pair_source_paths_v2(
                pair_id,
                {
                    "pair_id": pair_id,
                    "source_a": "<redacted>/S-REORIGIN_REV0.dxf",
                    "source_b": "<redacted>/S-REORIGIN_REV1.dxf",
                },
                {},
            )
        finally:
            pair_identity.candidate_pair_uuid = original

        assert repaired["source_a"] == str(before)
        assert repaired["source_b"] == str(after)
    finally:
        workbench.deleteLater()


def test_full_detail_upgrade_fires_once_and_respects_busy_and_pending(qapp, tmp_path) -> None:
    # Guard matrix for _maybe_start_zone_full_detail_v2: busy/pending/stale skip,
    # fires once per request, repeat is a no-op (never loops).
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        _prepare_zone_finish_workbench(workbench, tmp_path)
        crop_calls: list[tuple] = []
        workbench._start_zone_crop_render_v2 = (  # type: ignore[method-assign]
            lambda zone_id, **kw: crop_calls.append((zone_id, kw))
        )
        request_id = workbench._begin_selected_zone_render_request_v2("pair", "z1")

        # Busy controller -> skip (fast crop stays; do not disturb).
        workbench._zone_render_controller_v2._active_context = {"request_id": "x"}
        workbench._maybe_start_zone_full_detail_v2("pair", "z1", request_id)
        assert crop_calls == []

        # A queued different render -> skip.
        workbench._zone_render_controller_v2._active_context = None
        workbench._pending_zone_render_request_v2 = ("pair", "z2", "r2")
        workbench._maybe_start_zone_full_detail_v2("pair", "z1", request_id)
        assert crop_calls == []

        # Stale request id -> skip.
        workbench._pending_zone_render_request_v2 = None
        workbench._maybe_start_zone_full_detail_v2("pair", "z1", "stale-id")
        assert crop_calls == []

        # Free + current -> fires exactly once.
        workbench._maybe_start_zone_full_detail_v2("pair", "z1", request_id)
        assert crop_calls == [("z1", {"prefer_source_render": True})]

        # Repeat same request -> no-op (loop guard).
        workbench._maybe_start_zone_full_detail_v2("pair", "z1", request_id)
        assert crop_calls == [("z1", {"prefer_source_render": True})]
    finally:
        workbench.deleteLater()


def test_zone_crop_error_ignores_stale_request_id_and_keeps_current_status(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._active_row = {"pair_id": "pair"}
        workbench._active_zone_id = "z1"
        workbench._viewer_pairs_by_id = {"pair": {"pair_id": "pair"}}
        old_request_id = workbench._begin_selected_zone_render_request_v2("pair", "z1")
        workbench._begin_selected_zone_render_request_v2("pair", "z1")

        status_calls: list[tuple] = []
        pending_drains: list[str] = []
        workbench._set_preview_status_v2 = lambda *args, **_kwargs: status_calls.append(args)  # type: ignore[method-assign]
        workbench._start_pending_zone_render_v2 = lambda: pending_drains.append("drain")  # type: ignore[method-assign]

        workbench._on_zone_crop_render_error_v2(
            "pair",
            "z1",
            "old timeout",
            "render_timeout",
            old_request_id,
        )

        assert status_calls == []
        assert pending_drains == ["drain"]
    finally:
        workbench.deleteLater()


def test_zone_vector_inflight_does_not_rewrite_running_pending_metadata(qapp, tmp_path) -> None:
    from PySide6.QtCore import QProcess

    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    class RunningProcess:
        def state(self):
            return QProcess.Running

    source = tmp_path / "source.dxf"
    source.write_text("0\nEOF\n", encoding="utf-8")
    old_result = tmp_path / "old.result.json"

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._active_row = {"pair_id": "pair"}
        workbench._active_overlays_by_zone = {
            "z2": {"zone_id": "z2", "bbox": [0, 0, 10, 10], "old_bbox": [0, 0, 10, 10]}
        }
        workbench._viewer_pairs_by_id = {
            "pair": {"pair_id": "pair", "source_a": str(source), "source_b": str(source)}
        }
        workbench._zone_vector_qprocess = RunningProcess()  # type: ignore[assignment]
        workbench._zone_vector_pending = ("pair", "z1", "old.svg")
        workbench._zone_vector_result_json = old_result

        workbench._start_zone_vector_render_v2("pair", "z2")

        assert workbench._zone_vector_pending == ("pair", "z1", "old.svg")
        assert workbench._zone_vector_result_json == old_result
    finally:
        workbench._zone_vector_qprocess = None
        workbench.deleteLater()


def test_full_tree_rebuild_restores_active_zone_without_restarting_heavy_render(qapp, tmp_path) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        overlays = [
            {"zone_id": "z1", "bbox": [0, 0, 10, 10], "old_bbox": [0, 0, 10, 10], "raw_change_count": 1},
            {"zone_id": "z2", "bbox": [20, 20, 30, 30], "old_bbox": [20, 20, 30, 30], "raw_change_count": 1},
        ]
        workbench._viewer_root = tmp_path
        workbench._active_row = {"pair_id": "pair", "top_issues": []}
        workbench._active_zone_id = "z1"
        workbench._viewer_pairs_by_id = {"pair": {"pair_id": "pair"}}
        workbench._viewer_overlays_for_pair_v2 = lambda _pair_id: list(overlays)  # type: ignore[method-assign]
        workbench._visible_overlays_for_pdf_page_v2 = lambda *_args, **_kwargs: list(overlays)  # type: ignore[method-assign]
        workbench._refresh_zone_list_filter_v2 = lambda: None  # type: ignore[method-assign]
        workbench._update_review_progress_v2 = lambda: None  # type: ignore[method-assign]
        workbench._update_category_summary_v2 = lambda: None  # type: ignore[method-assign]
        workbench._zone_detail_text_v2 = lambda _zone_id: ""  # type: ignore[method-assign]
        workbench._load_current_zone_memo_v2 = lambda: None  # type: ignore[method-assign]

        calls: list[tuple[str, str]] = []
        workbench._request_zone_focus_v2 = lambda zone_id: calls.append(("focus", zone_id))  # type: ignore[method-assign]
        workbench._start_zone_crop_render_v2 = lambda zone_id: calls.append(("crop", zone_id))  # type: ignore[method-assign]
        workbench._start_zone_vector_render_v2 = lambda pair_id, zone_id: calls.append(("vector", f"{pair_id}:{zone_id}"))  # type: ignore[method-assign]

        workbench._run_full_zone_tree_rebuild_v2("pair", workbench._zone_tree_rebuild_generation_v2)

        assert calls == []
        assert workbench.zone_list_v2.currentItem() is not None
        assert workbench.zone_list_v2.currentItem().data(0, Qt.UserRole) == "z1"
    finally:
        workbench.deleteLater()


def test_tile_manifest_lookup_prefers_current_pair_manifest_and_skips_stale_cache_key(qapp, tmp_path) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.viewer_tile_cache import pair_tile_manifest_path

    viewer_root = tmp_path / "viewer"
    viewer_root.mkdir()
    pair_manifest = pair_tile_manifest_path(viewer_root / "tiles", "pair")
    pair_manifest.parent.mkdir(parents=True)
    pair_manifest.write_text(
        json.dumps({"pair_uuid": "pair", "cache_key": "key-1", "tile_count": 1}),
        encoding="utf-8",
    )
    global_manifest = viewer_root / "tiles_manifest.json"
    global_manifest.write_text(
        json.dumps(
            {
                "pairs": {
                    "pair": {"pair_uuid": "pair", "cache_key": "key-2", "tile_count": 2}
                }
            }
        ),
        encoding="utf-8",
    )

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._viewer_root = viewer_root
        workbench._viewer_manifest = {"tiles_manifest": str(global_manifest)}
        workbench._viewer_pairs_by_id = {
            "pair": {
                "pair_id": "pair",
                "tile_manifest": str(pair_manifest),
                "tile_cache_key": "key-1",
            }
        }

        assert workbench._tile_manifest_for_pair_v2("pair")["tile_count"] == 1

        workbench._viewer_pairs_by_id["pair"]["tile_cache_key"] = "key-2"

        assert workbench._tile_manifest_for_pair_v2("pair")["tile_count"] == 2
    finally:
        workbench.deleteLater()


def test_large_full_tree_rebuild_runs_in_chunks_and_can_finish(qapp, tmp_path, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_ZONE_THRESHOLD", 3)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_ITEM_LIMIT", 2)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_TIME_BUDGET_MS", 1000.0)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_DELAY_MS", 0)

    workbench = DrawingCompareWorkbenchV2()
    try:
        overlays = [
            {
                "zone_id": f"z{i}",
                "bbox": [i, i, i + 1, i + 1],
                "old_bbox": [i, i, i + 1, i + 1],
                "raw_change_count": 1,
            }
            for i in range(7)
        ]
        workbench._viewer_root = tmp_path
        workbench._active_row = {"pair_id": "pair", "top_issues": []}
        workbench._viewer_pairs_by_id = {"pair": {"pair_id": "pair"}}
        workbench._viewer_overlays_for_pair_v2 = lambda _pair_id: list(overlays)  # type: ignore[method-assign]
        workbench._visible_overlays_for_pdf_page_v2 = lambda *_args, **_kwargs: list(overlays)  # type: ignore[method-assign]
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(enabled=False, use_embedding=False, use_llm=False)  # type: ignore[method-assign]
        workbench._schedule_initial_zone_selection_v2 = lambda _pair_id: None  # type: ignore[method-assign]

        workbench._run_full_zone_tree_rebuild_v2("pair", workbench._zone_tree_rebuild_generation_v2)
        deadline = time.time() + 2.0
        while time.time() < deadline and workbench._full_zone_tree_chunk_state_v2 is not None:
            qapp.processEvents()
            time.sleep(0.001)
        qapp.processEvents()

        assert workbench._full_zone_tree_chunk_state_v2 is None
        assert workbench._pending_full_zone_tree_pair_id_v2 == ""
        assert workbench.zone_list_v2.topLevelItemCount() >= 1
        assert len(workbench._zone_leaf_items_v2()) == len(overlays)

        from src.services.comparison.viewer_perf_summary import summarize_viewer_perf

        summary = summarize_viewer_perf(tmp_path)
        assert summary["full_tree_rebuild_count"] == 1
        assert summary["full_tree_rebuild_chunked_count"] == 1
        assert summary["full_tree_rebuild_chunk_count"]["p95"] > 1
        assert summary["full_tree_rebuild_tree_item_count_max"] >= len(overlays)
    finally:
        workbench.deleteLater()


def test_full_tree_rebuild_loads_overlay_json_and_plan_on_workers(qapp, tmp_path, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.viewer_perf_summary import summarize_viewer_perf

    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_ZONE_THRESHOLD", 3)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_ITEM_LIMIT", 2)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_TIME_BUDGET_MS", 1000.0)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_DELAY_MS", 0)

    overlays = [
        {
            "zone_id": f"full{i}",
            "bbox": [i, i, i + 1, i + 1],
            "old_bbox": [i, i, i + 1, i + 1],
            "raw_change_count": i + 1,
            "change_type": "modified",
        }
        for i in range(7)
    ]
    overlay_path = tmp_path / "pair_overlays.json"
    overlay_path.write_text(json.dumps({"overlays": overlays}, ensure_ascii=False), encoding="utf-8")

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._viewer_root = tmp_path
        workbench._active_row = {
            "pair_id": "pair",
            "top_issues": [{"zone_id": "top-only", "priority_score": 999.0, "raw_change_count": 99}],
        }
        workbench._viewer_pairs_by_id = {"pair": {"pair_id": "pair", "overlay_json": str(overlay_path)}}
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(enabled=False, use_embedding=False, use_llm=False)  # type: ignore[method-assign]
        workbench._schedule_initial_zone_selection_v2 = lambda _pair_id: None  # type: ignore[method-assign]

        workbench._run_full_zone_tree_rebuild_v2("pair", workbench._zone_tree_rebuild_generation_v2)

        assert workbench.zone_list_v2.topLevelItemCount() == 0
        assert workbench._pending_full_zone_tree_pair_id_v2 == "pair"

        deadline = time.time() + 3.0
        while time.time() < deadline:
            qapp.processEvents()
            if (
                workbench._full_zone_tree_chunk_state_v2 is None
                and workbench._full_zone_tree_overlay_worker_v2 is None
                and workbench._full_zone_tree_plan_worker_v2 is None
                and not workbench._pending_full_zone_tree_pair_id_v2
            ):
                break
            time.sleep(0.001)
        qapp.processEvents()

        assert workbench._full_zone_tree_chunk_state_v2 is None
        assert workbench._pending_full_zone_tree_pair_id_v2 == ""
        assert len(workbench._zone_leaf_items_v2()) == len(overlays)
        assert workbench._find_zone_leaf_item_v2("top-only") is None

        summary = summarize_viewer_perf(tmp_path)
        assert summary["full_tree_rebuild_count"] == 1
        assert summary["full_tree_overlay_load_worker_count"] == 1
        assert summary["full_tree_plan_build_worker_count"] == 1
        assert summary["full_tree_overlay_json_bytes_max"] >= overlay_path.stat().st_size
    finally:
        workbench.deleteLater()


def test_full_tree_rebuild_prefers_paged_overlay_store_for_pdf_page(qapp, tmp_path, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.viewer_overlay_pages import write_overlay_page_store

    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_ZONE_THRESHOLD", 99)

    overlays = [
        {
            "zone_id": "page0-a",
            "bbox": [0, 0, 1, 1],
            "old_bbox": [0, 0, 1, 1],
            "page_a": 0,
            "page_b": 0,
            "raw_change_count": 1,
            "change_type": "modified",
        },
        {
            "zone_id": "page0-b",
            "bbox": [1, 1, 2, 2],
            "old_bbox": [1, 1, 2, 2],
            "page_a": 0,
            "page_b": 0,
            "raw_change_count": 1,
            "change_type": "modified",
        },
        {
            "zone_id": "page1-a",
            "bbox": [2, 2, 3, 3],
            "old_bbox": [2, 2, 3, 3],
            "page_a": 1,
            "page_b": 1,
            "raw_change_count": 2,
            "change_type": "modified",
        },
        {
            "zone_id": "page1-b",
            "bbox": [3, 3, 4, 4],
            "old_bbox": [3, 3, 4, 4],
            "page_a": 1,
            "page_b": 1,
            "raw_change_count": 3,
            "change_type": "modified",
        },
    ]
    overlay_path = tmp_path / "pair_overlays.json"
    overlay_path.write_text(json.dumps({"overlays": overlays}, ensure_ascii=False), encoding="utf-8")
    page_summary = write_overlay_page_store(
        pair_id="pair",
        overlays=overlays,
        output_root=tmp_path / "overlay_pages",
        page_size=2,
    )

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._viewer_root = tmp_path
        workbench._active_row = {"pair_id": "pair", "top_issues": []}
        workbench._viewer_pairs_by_id = {
            "pair": {
                "pair_id": "pair",
                "source_a": "old.pdf",
                "source_b": "new.pdf",
                "page_a": 1,
                "page_b": 1,
                "overlay_json": str(overlay_path),
                "overlay_pages_manifest": str(page_summary.manifest_path),
            }
        }
        workbench._schedule_initial_zone_selection_v2 = lambda _pair_id: None  # type: ignore[method-assign]

        workbench._run_full_zone_tree_rebuild_v2("pair", workbench._zone_tree_rebuild_generation_v2)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            qapp.processEvents()
            if (
                workbench._full_zone_tree_overlay_worker_v2 is None
                and workbench._full_zone_tree_plan_worker_v2 is None
                and not workbench._pending_full_zone_tree_pair_id_v2
            ):
                break
            time.sleep(0.001)
        qapp.processEvents()

        leaf_zone_ids = {str(item.data(0, Qt.UserRole) or "") for item in workbench._zone_leaf_items_v2()}
        assert leaf_zone_ids == {"page1-a", "page1-b"}
        assert "pair" not in workbench._viewer_overlay_cache

        events = [
            json.loads(line)
            for line in (tmp_path / "viewer_perf.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        full_tree_event = [event for event in events if event.get("event") == "full_zone_tree_rebuild"][-1]
        assert full_tree_event["overlay_load_strategy"] == "paged_overlay_store"
        assert full_tree_event["overlay_count"] == 4
        assert full_tree_event["materialized_overlay_count"] == 2
        assert full_tree_event["visible_overlay_count"] == 2
        assert full_tree_event["overlay_page_count"] == 2
        assert full_tree_event["overlay_page_files_read"] == 1
        assert full_tree_event["overlay_page_files_skipped"] == 1
    finally:
        workbench.deleteLater()


def test_full_tree_chunk_cancel_prevents_stale_tree_mutation(qapp, tmp_path, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_ZONE_THRESHOLD", 3)
    monkeypatch.setattr(dcw, "GUI_FULL_ZONE_TREE_CHUNK_DELAY_MS", 50)

    workbench = DrawingCompareWorkbenchV2()
    try:
        overlays = [
            {"zone_id": f"old{i}", "bbox": [0, 0, 1, 1], "old_bbox": [0, 0, 1, 1], "raw_change_count": 1}
            for i in range(6)
        ]
        workbench._viewer_root = tmp_path
        workbench._active_row = {"pair_id": "old_pair", "top_issues": []}
        workbench._viewer_pairs_by_id = {"old_pair": {"pair_id": "old_pair"}}
        workbench._viewer_overlays_for_pair_v2 = lambda _pair_id: list(overlays)  # type: ignore[method-assign]
        workbench._visible_overlays_for_pdf_page_v2 = lambda *_args, **_kwargs: list(overlays)  # type: ignore[method-assign]
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(enabled=False, use_embedding=False, use_llm=False)  # type: ignore[method-assign]

        workbench._run_full_zone_tree_rebuild_v2("old_pair", workbench._zone_tree_rebuild_generation_v2)
        assert workbench._full_zone_tree_chunk_state_v2 is not None

        workbench._active_row = {"pair_id": "new_pair", "top_issues": []}
        workbench._cancel_full_zone_tree_rebuild_v2("test_pair_switch", bump_generation=True)
        deadline = time.time() + 0.2
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.001)

        assert workbench._full_zone_tree_chunk_state_v2 is None
        assert workbench.zone_list_v2.topLevelItemCount() == 0
    finally:
        workbench.deleteLater()


def test_zone_category_cache_adds_missing_full_tree_zones(qapp, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.zone_classifier import ZoneCategoryResult

    classified: list[str] = []

    def fake_classify(zone, config=None):
        zone_id = str(zone.get("zone_id") or "")
        classified.append(zone_id)
        return ZoneCategoryResult(
            category=f"cat-{zone_id}",
            confidence=1.0,
            severity_boost=0,
            rationale_ko="test",
        )

    monkeypatch.setattr(dcw, "classify_zone_with_cascade", fake_classify)
    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._active_row = {"pair_id": "pair", "top_issues": []}
        workbench._load_ai_config_v2 = lambda: SimpleNamespace(use_embedding=False)  # type: ignore[method-assign]

        workbench._compute_zone_categories_for_pair_v2("pair", [{"zone_id": "z1"}])
        workbench._compute_zone_categories_for_pair_v2("pair", [{"zone_id": "z1"}, {"zone_id": "z2"}])

        assert classified == ["z1", "z2"]
        assert set(workbench._zone_categories_v2["pair"]) == {"z1", "z2"}
    finally:
        workbench.deleteLater()


def test_lightweight_only_skips_hidden_legacy_preview_async_loads(qapp, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    class PreviewStub:
        def __init__(self) -> None:
            self.load_calls: list[tuple[tuple, dict]] = []

        def load_preview(self, *args, **kwargs) -> None:
            self.load_calls.append((args, kwargs))

        def set_selected_zone(self, *_args, **_kwargs) -> None:
            pass

        def focus_zone(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr(dcw, "DRAWING_COMPARE_LIGHTWEIGHT_VIEWER_ONLY", True)
    workbench = DrawingCompareWorkbenchV2()
    try:
        before = PreviewStub()
        after = PreviewStub()
        workbench.preview_before_v2 = before  # type: ignore[assignment]
        workbench.preview_after_v2 = after  # type: ignore[assignment]
        workbench._active_row = {"pair_id": "pair"}
        workbench._active_zone_id = "z1"
        workbench._viewer_pairs_by_id = {"pair": {"pair_id": "pair"}}
        workbench._set_preview_status_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._update_viewer_manifest_pair_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._tile_manifest_for_pair_v2 = lambda *_args, **_kwargs: {}  # type: ignore[method-assign]
        workbench._selection_build_lod_tiles_enabled_v2 = lambda: False  # type: ignore[method-assign]
        workbench._populate_zone_list_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._select_zone_in_list_v2 = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        workbench._retire_render_worker_v2 = lambda: None  # type: ignore[method-assign]
        workbench._start_pending_zone_render_v2 = lambda: None  # type: ignore[method-assign]
        workbench._start_pending_render_v2 = lambda: None  # type: ignore[method-assign]
        workbench._zone_detail_text_v2 = lambda _zone_id: ""  # type: ignore[method-assign]

        workbench._on_pair_render_finished_v2(
            "pair",
            {"pair_id": "pair", "render_status": "rendered", "before_image": "before.png", "after_image": "after.png"},
            [{"zone_id": "z1"}],
        )
        workbench._on_pair_render_error_v2("pair", "failed")
        workbench._on_zone_crop_render_finished_v2(
            "pair",
            "z1",
            {"elapsed_ms": 1.0, "render_lifecycle": "ready"},
            {"pair_id": "pair"},
            [{"zone_id": "z1"}],
        )

        assert before.load_calls == []
        assert after.load_calls == []
    finally:
        workbench.deleteLater()


def test_viewer_pair_restores_redacted_sources_from_result_summary(qapp, tmp_path) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.drawing_batch import (
        DrawingFileDescriptor,
        DrawingKind,
        MatchCandidate,
        MatchStatus,
        parse_filename_identity,
    )
    from src.services.comparison.pair_identity import candidate_pair_uuid

    before = tmp_path / "S-101.dwg"
    after = tmp_path / "S-101_R1.dwg"
    before.write_bytes(b"dwg-a")
    after.write_bytes(b"dwg-b")

    candidate = MatchCandidate(
        source_a=DrawingFileDescriptor(
            path=str(before),
            kind=DrawingKind.CAD,
            extension=".dwg",
            identity=parse_filename_identity(before.name),
        ),
        source_b=DrawingFileDescriptor(
            path=str(after),
            kind=DrawingKind.CAD,
            extension=".dwg",
            identity=parse_filename_identity(after.name),
        ),
        score=0.95,
        status=MatchStatus.AUTO_CONFIRMED,
    )
    pair_id = candidate_pair_uuid(candidate)

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._viewer_pairs_by_id[pair_id] = {
            "pair_id": pair_id,
            "source_a": "<redacted>/S-101.dwg",
            "source_b": "<redacted>/S-101_R1.dwg",
            "before_image": "viewer/images/before.png",
            "after_image": "viewer/images/after.png",
        }
        workbench._source_a = ""
        workbench._source_b = ""
        workbench._result = SimpleNamespace(
            summary=SimpleNamespace(items=[SimpleNamespace(candidate=candidate)])
        )

        repaired = workbench._viewer_pair_from_row_v2(pair_id, {})

        assert repaired["source_a"] == str(before)
        assert repaired["source_b"] == str(after)
        assert workbench._viewer_pairs_by_id[pair_id]["source_a"] == str(before)
    finally:
        workbench.deleteLater()


def test_zone_filter_combo_offers_review_status_options(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        items = [workbench.cmb_zone_filter_v2.itemText(i) for i in range(workbench.cmb_zone_filter_v2.count())]
        assert "전체" in items
        assert "미검토만" in items
        assert "확인" in items
        assert "오탐" in items
    finally:
        workbench.deleteLater()


def test_zone_progress_label_shows_dash_when_empty(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._update_review_progress_v2()
        assert workbench.lbl_zone_progress_v2.text() == "진행: -"
    finally:
        workbench.deleteLater()


def test_zone_progress_label_counts_review_status(qapp) -> None:
    """Phase G3.6 + I2/I3 — the zone progress label switched to a rich
    HTML widget driven by ``_zone_leaf_items_v2`` (which walks the
    QTreeWidget recursively, leaf-only). This test injects 4 zones as
    top-level QTreeWidgetItems with UserRole zone_ids — the same shape
    the production tree builds for an unclustered category — and asserts
    the new HTML contains the expected per-status count chips.
    """

    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    from src.services.comparison.review_project import ReviewStateRecord

    workbench = DrawingCompareWorkbenchV2()
    try:
        # Inject a fake active row + zone tree items so the helper has
        # something to iterate. Phase I2 — zone_list_v2 is now a
        # QTreeWidget; leaves are top-level items with no children, each
        # carrying ``zone_id`` in column-0 UserRole (see _zone_leaf_items_v2).
        workbench._active_row = {"pair_id": "pair_test", "pair_uuid": "pair_test"}
        for zone_id in ["z1", "z2", "z3", "z4"]:
            item = QTreeWidgetItem([zone_id])
            item.setData(0, Qt.UserRole, zone_id)
            workbench.zone_list_v2.addTopLevelItem(item)
        # Mark z1=confirmed, z2=hold, z3=false_positive, z4=needs_review (default)
        for zone, status in [("z1", "confirmed"), ("z2", "hold"), ("z3", "false_positive")]:
            rec = ReviewStateRecord(
                pair_id="pair_test",
                pair_uuid="pair_test",
                zone_id=zone,
                status=status,
                note="",
            )
            workbench._review_records_v2[rec.key] = rec
        workbench._update_review_progress_v2()
        text = workbench.lbl_zone_progress_v2.text()
        # Phase G3.6 HTML format — counts now appear as "✓1", "✗1",
        # "⊘1", "⊙1" inline chips instead of "확인 1" prose. The "현재
        # 도면" prefix and "X/Y" + "%" tokens stay stable.
        assert "현재 도면" in text
        assert "3/4" in text
        assert "75%" in text
        assert "✓1" in text  # confirmed
        assert "⏸1" in text  # hold
        assert "⊘1" in text  # false_positive
        assert "⊙1" in text  # pending / needs_review
    finally:
        workbench.deleteLater()


def test_zoom_slider_change_updates_label(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        # Slider 250 → 250% / 2.5x
        workbench.sld_zoom_v2.setValue(250)
        assert workbench.lbl_zoom_value_v2.text() == "250%"
        # Reset button → 100%
        workbench._on_reset_zoom_v2()
        assert workbench.lbl_zoom_value_v2.text() == "100%"
    finally:
        workbench.deleteLater()


def test_recent_paths_helper_dedupes_and_caps(qapp, tmp_path, monkeypatch) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2, RECENT_PATHS_LIMIT
    from src.gui import drawing_compare_workbench as dcw

    # Redirect data dir to tmp to avoid touching real recent_paths.json
    monkeypatch.setattr(dcw, "_workbench_data_dir", lambda: tmp_path)

    workbench = DrawingCompareWorkbenchV2()
    try:
        # Add 12 entries; only RECENT_PATHS_LIMIT=10 should persist
        for i in range(12):
            workbench._add_recent_path_v2(f"/a/{i}", f"/b/{i}")
        loaded = workbench._load_recent_paths_v2()
        assert len(loaded) == RECENT_PATHS_LIMIT
        # Most recent first
        assert loaded[0]["a"] == "/a/11"
        # Re-adding an existing pair moves it to top without duplicating
        workbench._add_recent_path_v2("/a/5", "/b/5")
        loaded = workbench._load_recent_paths_v2()
        assert loaded[0]["a"] == "/a/5"
        assert sum(1 for e in loaded if e["a"] == "/a/5") == 1
    finally:
        workbench.deleteLater()


def test_viewer_manifest_load_clears_previous_compare_session_state(qapp, tmp_path) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    manifest = tmp_path / "viewer_manifest.json"
    manifest.write_text(
        '{"pairs": [{"pair_id": "new_pair", "render_status": "failed"}]}',
        encoding="utf-8",
    )

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench._zone_categories_v2["old_pair"] = {"old_zone": object()}
        workbench._active_category_filter_v2 = "old-category"
        workbench._user_picked_category_filter_v2 = True
        workbench._active_pattern_filter_v2 = "old-pattern"
        workbench._active_row = {"pair_id": "old_pair"}
        workbench._active_zone_id = "old_zone"
        workbench._active_issue_by_zone = {"old_zone": {"zone_id": "old_zone"}}
        workbench._active_overlays_by_zone = {"old_zone": {"zone_id": "old_zone"}}
        workbench._viewer_overlay_cache = {"old_pair": [{"zone_id": "old_zone"}]}
        workbench._render_status_by_pair = {"old_pair": "ready"}
        workbench._lightweight_raster_pairs = {"old_pair"}
        workbench._pending_render_request_v2 = ("old_pair", {}, {})
        workbench._pending_zone_render_request_v2 = ("old_pair", "old_zone")
        workbench._zone_vector_paths[("old_pair", "old_zone")] = "old.svg"
        workbench._zone_vector_pending = ("old_pair", "old_zone", "old.svg")
        workbench._zone_vector_result_json = tmp_path / "old.json"
        workbench._v2_fidelity_by_pair_id = {"old_pair": ("exact", "ready")}
        workbench._result = SimpleNamespace(
            viewer_package=SimpleNamespace(
                output_paths={"viewer_manifest_json": str(manifest)}
            ),
            artifact_package=SimpleNamespace(output_paths={}),
        )

        workbench._load_viewer_manifest_v2()

        assert "new_pair" in workbench._viewer_pairs_by_id
        assert workbench._render_status_by_pair == {"new_pair": "failed"}
        assert workbench._zone_categories_v2 == {}
        assert workbench._active_row is None
        assert workbench._active_zone_id == ""
        assert workbench._active_issue_by_zone == {}
        assert workbench._active_overlays_by_zone == {}
        assert workbench._viewer_overlay_cache == {}
        assert workbench._lightweight_raster_pairs == set()
        assert workbench._pending_render_request_v2 is None
        assert workbench._pending_zone_render_request_v2 is None
        assert workbench._zone_vector_paths == {}
        assert workbench._zone_vector_pending is None
        assert workbench._zone_vector_result_json is None
        assert workbench._active_pattern_filter_v2 == ""
        assert workbench._active_category_filter_v2 == "전체"
        assert workbench._user_picked_category_filter_v2 is False
        assert "old_pair" not in workbench._v2_fidelity_by_pair_id

        workbench._on_zone_crop_render_finished_v2(
            "old_pair",
            "old_zone",
            {},
            {"pair_id": "old_pair"},
            [{"zone_id": "old_zone"}],
        )
        assert "old_pair" not in workbench._viewer_pairs_by_id
        assert "old_zone" not in workbench._active_overlays_by_zone
    finally:
        workbench.deleteLater()
