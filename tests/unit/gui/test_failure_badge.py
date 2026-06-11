# -*- coding: utf-8 -*-
"""Unit tests for S1.4 — FailureBadge widget."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


def _ensure_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_failure_badge_starts_hidden() -> None:
    """S1.4: a fresh badge is invisible until codes are set."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    badge = FailureBadge()
    assert not badge.isVisible()
    assert badge.failure_codes() == ()


def test_failure_badge_stays_hidden_for_empty_codes() -> None:
    """S1.4: empty input keeps the badge hidden."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    badge = FailureBadge()
    badge.set_failure_codes(())
    assert not badge.isVisible()


def test_failure_badge_hidden_for_ok_only_codes() -> None:
    """S1.4: HIDDEN_CODES ('ok') are filtered out — badge stays hidden.

    The viewport may forward ('ok',) defensively; the badge must not
    surface a chip for it.
    """
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    badge = FailureBadge()
    badge.set_failure_codes(("ok",))
    assert not badge.isVisible()
    assert badge.failure_codes() == ()


def test_failure_badge_visible_for_info_code_with_grey_palette() -> None:
    """S1.4: a single info code makes the badge visible with grey chip."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    badge = FailureBadge()
    badge.set_failure_codes(("backend_fallback_canvas_skeleton",))
    assert badge.isVisible()
    assert badge.failure_codes() == ("backend_fallback_canvas_skeleton",)
    # info severity uses neutral gray #6B7280
    assert "6B7280" in badge._chip.styleSheet()


def test_failure_badge_warn_uses_amber_palette() -> None:
    """S1.4: warn severity renders with amber #F59E0B."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    badge = FailureBadge()
    badge.set_failure_codes(("dwg_unsupported_version",))
    assert badge.isVisible()
    assert "F59E0B" in badge._chip.styleSheet()


def test_failure_badge_error_uses_red_palette() -> None:
    """S1.4: error severity renders with red #DC2626."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    badge = FailureBadge()
    badge.set_failure_codes(("vector_draw_failed",))
    assert badge.isVisible()
    assert "DC2626" in badge._chip.styleSheet()


def test_failure_badge_picks_highest_severity_when_mixed() -> None:
    """S1.4: mixed info+warn+error must paint the chip with the error palette."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    badge = FailureBadge()
    badge.set_failure_codes((
        "backend_fallback_canvas_skeleton",  # info
        "dwg_unsupported_version",            # warn
        "vector_draw_failed",                 # error
    ))
    # red chip wins
    assert "DC2626" in badge._chip.styleSheet()
    # label shows count of visible codes
    assert "3건" in badge._chip.text()


def test_failure_badge_label_includes_korean_count() -> None:
    """S1.4: the chip label includes Korean ``N건`` count."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    badge = FailureBadge()
    badge.set_failure_codes((
        "backend_fallback_canvas_skeleton",
        "dwg_using_cached_dxf",
    ))
    text = badge._chip.text()
    assert "2건" in text
    # Korean character present
    assert any("가" <= ch <= "힣" for ch in text)


def test_failure_badge_tooltip_lists_active_codes_in_korean() -> None:
    """S1.4: tooltip text contains the Korean message for each code."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    badge = FailureBadge()
    badge.set_failure_codes(("dwg_unsupported_version",))
    tooltip = badge._chip.toolTip()
    # Either "DWG" or "AC1015" must appear (both are in the message)
    assert "DWG" in tooltip
    # Korean character present
    assert any("가" <= ch <= "힣" for ch in tooltip)


def test_failure_badge_clear_hides_chip() -> None:
    """S1.4: clear() restores the hidden state."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    badge = FailureBadge()
    badge.set_failure_codes(("vector_draw_failed",))
    assert badge.isVisible()

    badge.clear()
    assert not badge.isVisible()
    assert badge.failure_codes() == ()


def test_failure_badge_codes_changed_signal_emitted_on_change() -> None:
    """S1.4: codesChanged signal fires when the visible code set changes."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    received: list = []
    badge = FailureBadge()
    badge.codesChanged.connect(lambda t: received.append(t))

    badge.set_failure_codes(("vector_draw_failed",))
    badge.set_failure_codes(("dwg_unsupported_version",))
    badge.clear()

    assert len(received) == 3
    assert received[0] == ("vector_draw_failed",)
    assert received[1] == ("dwg_unsupported_version",)
    assert received[2] == ()


def test_failure_badge_signal_not_emitted_when_codes_unchanged() -> None:
    """S1.4: setting identical codes again does not re-emit the signal.

    Prevents log/telemetry noise when the workbench polls the
    viewport's failure_codes() on every render frame.
    """
    _ensure_app()
    from src.gui.failure_badge import FailureBadge

    received: list = []
    badge = FailureBadge()
    badge.codesChanged.connect(lambda t: received.append(t))

    badge.set_failure_codes(("vector_draw_failed",))
    badge.set_failure_codes(("vector_draw_failed",))  # same input

    assert len(received) == 1


def test_failure_badge_show_details_displays_korean_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1.4: _show_details() builds a Korean dialog body with all active codes.

    Monkey-patches QMessageBox.exec to avoid the modal blocking the test;
    captures the dialog text instead.
    """
    _ensure_app()
    from src.gui import failure_badge as fb_module

    captured: list = []

    def mock_exec(self):
        captured.append({"title": self.windowTitle(), "text": self.text()})
        return 0

    monkeypatch.setattr(fb_module.QMessageBox, "exec", mock_exec)

    badge = fb_module.FailureBadge()
    badge.set_failure_codes((
        "vector_draw_failed",
        "dwg_unsupported_version",
    ))
    badge._show_details()

    assert len(captured) == 1
    text = captured[0]["text"]
    # Each code's message present
    assert "벡터 렌더링 실패" in text
    assert "DWG" in text
    # Suggested action present for codes that have one
    assert "조치" in text
    # Code identifiers present for traceability
    assert "vector_draw_failed" in text
    assert "dwg_unsupported_version" in text


def test_failure_badge_show_details_silent_when_no_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1.4: _show_details() with no active codes opens no dialog."""
    _ensure_app()
    from src.gui import failure_badge as fb_module

    captured: list = []
    monkeypatch.setattr(fb_module.QMessageBox, "exec", lambda self: captured.append(True) or 0)

    badge = fb_module.FailureBadge()
    badge._show_details()

    assert captured == []


# ---------------------------------------------------------------------------
# S1.6 — collect_viewport_failure_codes helper
# ---------------------------------------------------------------------------


class _FakeViewportWithCodes:
    """Test double: a viewport-like object that exposes render_failure_codes."""

    def __init__(self, codes) -> None:
        self._codes = tuple(codes)

    def render_failure_codes(self):
        return self._codes


class _FakeViewportWithoutMethod:
    """Test double: stands in for QtQuickUnavailableLightweightViewport."""


def test_collect_viewport_failure_codes_aggregates_multiple_viewports() -> None:
    """S1.6: helper concatenates codes from every viewport, in order."""
    from src.gui.failure_badge import collect_viewport_failure_codes

    a = _FakeViewportWithCodes(["dwg_unsupported_version"])
    b = _FakeViewportWithCodes(["backend_fallback_canvas_skeleton", "vector_draw_failed"])
    result = collect_viewport_failure_codes(a, b)

    assert result == (
        "dwg_unsupported_version",
        "backend_fallback_canvas_skeleton",
        "vector_draw_failed",
    )


def test_collect_viewport_failure_codes_treats_missing_method_as_qquickwidget_fallback() -> None:
    """S1.6: viewport without render_failure_codes() → backend_fallback_qquickwidget.

    Matches the QtQuickUnavailableLightweightViewport scenario where Qt
    Quick itself is missing and the workbench fell back to the
    compatibility viewport.
    """
    from src.gui.failure_badge import collect_viewport_failure_codes

    a = _FakeViewportWithCodes(["vector_draw_failed"])
    b = _FakeViewportWithoutMethod()
    result = collect_viewport_failure_codes(a, b)

    assert "vector_draw_failed" in result
    assert "backend_fallback_qquickwidget" in result
    assert len(result) == 2


def test_collect_viewport_failure_codes_empty_args_returns_empty_tuple() -> None:
    """S1.6: zero viewports → empty tuple (no badge will render)."""
    from src.gui.failure_badge import collect_viewport_failure_codes

    assert collect_viewport_failure_codes() == ()


def test_collect_viewport_failure_codes_integrates_with_real_lightweight_viewport(
    monkeypatch,
) -> None:
    """S1.6: integration — real LightweightDrawingViewport works with the helper.

    T2 (2026-06-11): the QSG module now exists, so a fallback event is
    SIMULATED by breaking the import — the real viewport must report the
    code and the helper must propagate it.
    """
    import sys
    import types

    _ensure_app()
    from src.gui.lightweight_viewport import LightweightDrawingViewport
    from src.gui.failure_badge import collect_viewport_failure_codes

    monkeypatch.setitem(
        sys.modules, "src.gui.qsg_line_item", types.ModuleType("broken_qsg")
    )
    vp = LightweightDrawingViewport()
    codes = collect_viewport_failure_codes(vp)

    assert "backend_fallback_canvas_skeleton" in codes


# ---------------------------------------------------------------------------
# P0-1 — badge_codes_for_run (viewport + run-level merge for the monolith)
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Dict, List, Tuple  # noqa: E402


class _FakeViewport:
    def __init__(self, codes: Tuple[str, ...]) -> None:
        self._codes = codes

    def render_failure_codes(self) -> Tuple[str, ...]:
        return self._codes


@dataclass
class _FakeResult:
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeItem:
    result: Any = None


def test_badge_codes_for_run_merges_viewport_and_run_codes() -> None:
    """P0-1: a low-confidence alignment AND a backend fallback both surface."""
    from src.gui.failure_badge import badge_codes_for_run

    viewports = (_FakeViewport(("backend_fallback_canvas_skeleton",)),)
    items = [_FakeItem(result=_FakeResult(metadata={"alignment_low_confidence": True}))]
    codes = badge_codes_for_run(viewports, items)
    assert "alignment_low_confidence" in codes
    assert "backend_fallback_canvas_skeleton" in codes


def test_badge_codes_for_run_dedupes_count_accurately() -> None:
    """A code present in both viewport and run output appears once."""
    from src.gui.failure_badge import badge_codes_for_run

    viewports = (_FakeViewport(("vector_draw_failed", "ok")),)
    items = [
        _FakeItem(result=_FakeResult(metadata={"zone_failure_codes": ("vector_draw_failed",)})),
    ]
    # zone_failure_codes is read off the result by aggregate_run_failure_codes
    codes = badge_codes_for_run(viewports, items)
    assert codes.count("vector_draw_failed") == 1
    assert "ok" not in codes  # HIDDEN dropped


def test_badge_codes_for_run_clean_run_hides_badge() -> None:
    """A clean run with a healthy viewport yields no codes → badge hidden."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge, badge_codes_for_run

    viewports = (_FakeViewport(("ok",)),)
    items = [_FakeItem(result=_FakeResult())]
    badge = FailureBadge()
    badge.set_failure_codes(badge_codes_for_run(viewports, items))
    assert not badge.isVisible()
    assert badge.failure_codes() == ()


def test_badge_codes_for_run_low_confidence_makes_badge_visible() -> None:
    """End-to-end: a degraded run lights the badge (the P0-1 fix)."""
    _ensure_app()
    from src.gui.failure_badge import FailureBadge, badge_codes_for_run

    viewports = (_FakeViewport(()),)
    items = [_FakeItem(result=_FakeResult(metadata={"alignment_low_confidence": True}))]
    badge = FailureBadge()
    badge.set_failure_codes(badge_codes_for_run(viewports, items))
    assert badge.isVisible()
    assert "alignment_low_confidence" in badge.failure_codes()
