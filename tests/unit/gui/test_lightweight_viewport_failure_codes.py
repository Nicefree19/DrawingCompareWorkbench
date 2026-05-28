# -*- coding: utf-8 -*-
"""Unit tests for S1.3.4 — failure_codes surfaced by LightweightDrawingViewport.

Points 3 (QQuickWidget fallback) and 4 (QSGLineItem unavailable) of the
silent-fallback visibility roadmap.
"""

from __future__ import annotations

import os

# Run in offscreen mode so tests work without a real display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


def test_fallback_quick_widget_carries_static_failure_code() -> None:
    """S1.3.4 Point 3: the stand-in widget exposes its failure code as a
    class attribute so static analysis and tests can verify which
    RenderFailureCode the GUI badge (S1.4) will receive without
    spinning up Qt.
    """

    from src.gui.lightweight_viewport import _FallbackQuickWidget

    assert _FallbackQuickWidget.failure_code == "backend_fallback_qquickwidget"


def test_lightweight_viewport_render_failure_codes_returns_tuple() -> None:
    """S1.3.4: viewport exposes render_failure_codes() as an immutable tuple.

    Each emitted code must be a valid RenderFailureCode value with a
    valid severity tier.
    """

    from PySide6.QtWidgets import QApplication

    from src.gui.lightweight_viewport import LightweightDrawingViewport
    from src.services.comparison.render_failure_codes import (
        is_valid_code,
        severity_of,
    )

    _ = QApplication.instance() or QApplication([])
    viewport = LightweightDrawingViewport()

    codes = viewport.render_failure_codes()
    assert isinstance(codes, tuple)
    for code in codes:
        assert is_valid_code(code), f"unexpected code: {code!r}"
        assert severity_of(code) in {"info", "warn", "error"}


def test_lightweight_viewport_canvas_skeleton_code_present_in_test_env() -> None:
    """S1.3.4 Point 4: in the unit-test environment ``src.gui.qsg_line_item``
    is unavailable, so the viewport must report
    ``backend_fallback_canvas_skeleton``.

    This locks in the runtime contract: importing the optional QSG
    extension can fail without blanking the viewer. The viewer keeps
    rendering through the Canvas path and the badge tells the user.
    """

    from PySide6.QtWidgets import QApplication

    from src.gui.lightweight_viewport import LightweightDrawingViewport

    _ = QApplication.instance() or QApplication([])
    viewport = LightweightDrawingViewport()

    assert "backend_fallback_canvas_skeleton" in viewport.render_failure_codes()


def test_lightweight_viewport_qquickwidget_fallback_surfaces_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1.3.4 Point 3: when QQuickWidget construction raises, the
    viewport must include backend_fallback_qquickwidget in the
    accumulated failure_codes.

    Monkey-patches the ``QQuickWidget`` constructor on the module so
    ``_create_quick_widget`` is forced into its except branch and
    returns the ``_FallbackQuickWidget`` stand-in.
    """

    from PySide6.QtWidgets import QApplication

    from src.gui import lightweight_viewport

    def raise_quickwidget(*_args, **_kwargs):
        raise RuntimeError("QQuickWidget intentionally broken for S1.3.4 test")

    monkeypatch.setattr(lightweight_viewport, "QQuickWidget", raise_quickwidget)

    _ = QApplication.instance() or QApplication([])
    viewport = lightweight_viewport.LightweightDrawingViewport()

    codes = viewport.render_failure_codes()
    assert "backend_fallback_qquickwidget" in codes


def test_lightweight_viewport_failure_codes_method_returns_immutable_copy() -> None:
    """S1.3.4: callers cannot mutate the viewport's internal list.

    The public ``render_failure_codes()`` should hand out a tuple so a
    misbehaving caller cannot ``.append()`` and pollute the badge model.
    """

    from PySide6.QtWidgets import QApplication

    from src.gui.lightweight_viewport import LightweightDrawingViewport

    _ = QApplication.instance() or QApplication([])
    viewport = LightweightDrawingViewport()

    codes = viewport.render_failure_codes()
    assert isinstance(codes, tuple)
    # Tuples have no .append; this only fails to typecheck if codes is mutable.
    with pytest.raises(AttributeError):
        codes.append("ai_heuristic_fallback")  # type: ignore[attr-defined]
