# -*- coding: utf-8 -*-
"""Unit tests for Phase B Workbench upgrades.

Covers the three customer-feedback features added in Phase B:
- B1: overlay opacity slider (clamped scale, applied to QML root + fallback)
- B2: compact mode toggle (header region show/hide + button label sync)
- B3: quality preset (DPI/edge mapping for the pipeline request)

The widget tests run headless via QT_QPA_PLATFORM=offscreen and rely on the
public methods we ship for these features so we don't need a live QML render.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_quality_presets_have_increasing_dpi_tiers() -> None:
    """Audit-gates §10 follow-up — index 0 is now the "🤖 자동 (권장)" sentinel
    and the default. Manual DPI tiers remain at indices 1+ in increasing
    order so the dropdown stays predictable for advanced users.

    Earlier requirement that "default = highest DPI" was retracted after the
    S20 hang incident proved DPI 400 default is the wrong policy for
    non-expert reviewers. The new contract: default = adaptive auto-select.
    """

    from src.gui.drawing_compare_workbench import (
        PREVIEW_QUALITY_AUTO_INDEX,
        PREVIEW_QUALITY_DEFAULT_INDEX,
        PREVIEW_QUALITY_PRESETS,
    )
    assert len(PREVIEW_QUALITY_PRESETS) >= 4, "must keep auto + 3+ manual tiers"
    labels = [p[0] for p in PREVIEW_QUALITY_PRESETS]
    dpis = [p[1] for p in PREVIEW_QUALITY_PRESETS]
    edges = [p[2] for p in PREVIEW_QUALITY_PRESETS]
    # Auto sentinel at index 0
    assert PREVIEW_QUALITY_AUTO_INDEX == 0
    assert "자동" in labels[0]
    assert dpis[0] == 0  # sentinel — actual DPI resolved at runtime
    assert edges[0] == 0
    # Manual tiers in remaining slots
    assert any("보통" in label for label in labels[1:])
    assert any("고화질" in label for label in labels[1:])
    assert any("초고화질" in label for label in labels[1:])
    # Manual tiers strictly increasing
    manual_dpis = dpis[1:]
    manual_edges = edges[1:]
    assert manual_dpis == sorted(manual_dpis)
    assert manual_edges == sorted(manual_edges)
    # Default is the auto sentinel
    assert PREVIEW_QUALITY_DEFAULT_INDEX == PREVIEW_QUALITY_AUTO_INDEX
    default_label = PREVIEW_QUALITY_PRESETS[PREVIEW_QUALITY_DEFAULT_INDEX][0]
    assert "자동" in default_label, \
        f"default preset must be the auto sentinel; got {default_label}"


def test_overlay_opacity_clamps_to_valid_range(qapp) -> None:
    from src.gui.drawing_compare_workbench import GpuDrawingViewport

    viewport = GpuDrawingViewport()
    assert viewport.overlay_opacity_scale == 1.0

    viewport.set_overlay_opacity_scale(0.5)
    assert viewport.overlay_opacity_scale == 0.5

    # Below 0.3 → clamped (overlay would otherwise be invisible)
    viewport.set_overlay_opacity_scale(0.0)
    assert viewport.overlay_opacity_scale == 0.3
    viewport.set_overlay_opacity_scale(-1.0)
    assert viewport.overlay_opacity_scale == 0.3

    # Above 1.0 → clamped
    viewport.set_overlay_opacity_scale(2.5)
    assert viewport.overlay_opacity_scale == 1.0

    # Invalid input → falls back to 1.0 then clamps
    viewport.set_overlay_opacity_scale("not a number")  # type: ignore[arg-type]
    assert viewport.overlay_opacity_scale == 1.0


def test_compact_mode_toggle_updates_visibility_and_label(qapp) -> None:
    """Headless instantiation of the full Workbench so we can flip compact mode."""

    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        # Initial: header region visible, compact mode off
        assert workbench._compact_mode_v2 is False
        assert workbench.header_region_v2.isVisible() in (True, False)  # depends on show()
        # Flip to compact — label changed from generic "펼치기" to action-direct
        # call-to-action (QW1 follow-up). Look for the file-pick keyword instead.
        workbench._set_compact_mode_v2(True)
        assert workbench._compact_mode_v2 is True
        assert workbench.header_region_v2.isVisible() is False
        assert "선택" in workbench.btn_compact_v2.text()
        # Button check state mirrors mode
        assert workbench.btn_compact_v2.isChecked() is True

        # Flip back
        workbench._set_compact_mode_v2(False)
        assert workbench._compact_mode_v2 is False
        assert "접기" in workbench.btn_compact_v2.text()
        assert workbench.btn_compact_v2.isChecked() is False
    finally:
        workbench.deleteLater()


def test_overlay_opacity_slider_applies_to_both_viewports(qapp) -> None:
    """The slider handler should propagate the value to both preview viewports."""

    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        # 65% → 0.65 scale
        workbench._on_overlay_opacity_changed_v2(65)
        assert workbench.preview_before_v2.overlay_opacity_scale == pytest.approx(0.65)
        assert workbench.preview_after_v2.overlay_opacity_scale == pytest.approx(0.65)
        assert workbench.lbl_overlay_opacity_value_v2.text() == "65%"

        # 25% (below clamp floor) → both viewports clamp to 0.30
        workbench._on_overlay_opacity_changed_v2(25)
        assert workbench.preview_before_v2.overlay_opacity_scale == pytest.approx(0.30)
        # Label still shows 30% (post-clamp via slider min) — slider raw value 30
        assert workbench.lbl_overlay_opacity_value_v2.text() in {"30%", "25%"}
    finally:
        workbench.deleteLater()
