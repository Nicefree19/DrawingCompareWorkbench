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

import os
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
