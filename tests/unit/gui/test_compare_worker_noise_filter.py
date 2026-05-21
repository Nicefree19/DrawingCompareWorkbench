# -*- coding: utf-8 -*-
"""Phase O — legacy CompareWorker should pick up noise filter dialog
settings via ``load_noise_filter_settings``.

Without this test, a future refactor could silently drop the disk
read in ``_create_comparison_config`` and the legacy DrawingCompareTab
workflow would stop honouring the dialog (the v2 Workbench would
still work, but the user would see split behaviour between the two
panes — exactly the bug Codex review caught for the artifact path).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _qapp():
    """CompareWorker is a QThread — needs a QApplication for QObject base."""
    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 not importable in this env")
    app = QApplication.instance() or QApplication([])
    yield app


def _make_worker(options: dict | None = None):
    from src.gui.unified_load_module.workers.compare_worker import CompareWorker
    return CompareWorker(
        old_path="dummy_a.dxf",
        new_path="dummy_b.dxf",
        options=options or {},
    )


def test_compare_worker_overlays_noise_filter_settings_on_sensitivity():
    """Dialog-saved settings reach SensitivityConfig via the disk read."""
    from src.services.comparison.noise_filter_io import NoiseFilterSettings

    custom = NoiseFilterSettings(
        global_alignment_enabled=False,
        hungarian_max_subset=350,
        cosmetic_detection_enabled=True,
        suppress_cosmetic_only=True,
        cosmetic_attributes=("color", "lineweight"),
    )

    with patch(
        "src.services.comparison.noise_filter_io.load_noise_filter_settings",
        return_value=custom,
    ):
        worker = _make_worker()
        config = worker._create_comparison_config()

    sens = config.sensitivity
    assert sens.global_alignment_enabled is False
    assert sens.hungarian_max_subset == 350
    assert sens.cosmetic_detection_enabled is True
    assert sens.suppress_cosmetic_only is True
    assert sens.cosmetic_attributes == ("color", "lineweight")


def test_compare_worker_preserves_per_tab_position_threshold():
    """Tab's preset (Strict/Normal/Relaxed) for position_threshold +
    near_match_radius MUST not be overridden by the noise filter dialog.
    The two control orthogonal axes — only Phase O fields are layered."""
    from src.services.comparison.noise_filter_io import NoiseFilterSettings

    with patch(
        "src.services.comparison.noise_filter_io.load_noise_filter_settings",
        return_value=NoiseFilterSettings.default(),
    ):
        worker = _make_worker(options={
            "position_threshold": 0.25,  # 사용자가 Strict 프리셋 선택한 값
            "near_match_radius": 5.0,
        })
        config = worker._create_comparison_config()

    sens = config.sensitivity
    # 탭 preset 값 보존
    assert sens.position_threshold == pytest.approx(0.25)
    assert sens.near_match_radius == pytest.approx(5.0)


def test_compare_worker_silent_fallback_on_load_failure():
    """If load_noise_filter_settings raises (rare — disk corruption past
    the never-raise guard), CompareWorker should still build a working
    ComparisonConfig with default SensitivityConfig values, not crash
    the comparison."""
    with patch(
        "src.services.comparison.noise_filter_io.load_noise_filter_settings",
        side_effect=RuntimeError("disk fault"),
    ):
        worker = _make_worker()
        config = worker._create_comparison_config()

    # Default SensitivityConfig values (not raised)
    assert config is not None
    assert config.sensitivity.global_alignment_enabled is True  # default
    assert config.sensitivity.suppress_cosmetic_only is False  # default


# ---------------------------------------------------------------------------
# Phase O5 in legacy worker — _resolve_noise_filter_strength
# (post-RV-20260508-001 #3 follow-up implementation)
# ---------------------------------------------------------------------------


def test_resolve_noise_filter_strength_returns_dialog_value():
    """When dialog has saved noise_filter_strength="high", the legacy
    worker's image-compare path should pick it up via the helper."""
    from src.services.comparison.noise_filter_io import NoiseFilterSettings

    with patch(
        "src.services.comparison.noise_filter_io.load_noise_filter_settings",
        return_value=NoiseFilterSettings(noise_filter_strength="high"),
    ):
        worker = _make_worker()
        assert worker._resolve_noise_filter_strength() == "high"


def test_resolve_noise_filter_strength_defaults_to_medium():
    """Default config returns "medium" — matches DrawingDiffer default."""
    from src.services.comparison.noise_filter_io import NoiseFilterSettings

    with patch(
        "src.services.comparison.noise_filter_io.load_noise_filter_settings",
        return_value=NoiseFilterSettings.default(),
    ):
        worker = _make_worker()
        assert worker._resolve_noise_filter_strength() == "medium"


def test_resolve_noise_filter_strength_silent_fallback_on_load_error():
    """Disk error → fallback to "medium" rather than raise. Mirrors the
    same defensive pattern as _create_comparison_config."""
    with patch(
        "src.services.comparison.noise_filter_io.load_noise_filter_settings",
        side_effect=OSError("permission denied"),
    ):
        worker = _make_worker()
        assert worker._resolve_noise_filter_strength() == "medium"
