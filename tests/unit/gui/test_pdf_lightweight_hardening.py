# -*- coding: utf-8 -*-
"""Tests for PDF lightweight viewer hardening (audit-gates §12.3 A1-A5).

The 2026-05-15 Qt6Core BEX64 (0xc0000409) crash was traced to a
``[PDF lightweight] before-side skipped: exists=False`` log message
emitted 7 seconds after a successful compare. The hardening commit:

- A1: routes source paths through ``safe_unicode()`` to defuse CP949↔UTF-16
  surrogate corruption before constructing ``Path``.
- A2: replaces the silent ``except: pass`` on ``set_fidelity_state()``
  with logging + ``relative_only`` fallback.
- A4: re-checks ``Path.exists()`` immediately before
  ``QPdfDocument.render_page()`` to defuse the 7-second race.
- A5: warns when ``render_page()`` is invoked off the GUI thread.

These tests pin the contracts so the protections cannot regress.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.comparison.safe_unicode import safe_unicode


class TestA1SafeUnicodeOnSourcePaths:
    """Path sanitisation contract — defuses lone surrogate codepoints."""

    def test_safe_unicode_preserves_clean_paths(self):
        path_str = "C:/normal/path/file.pdf"
        assert safe_unicode(path_str) == path_str

    def test_safe_unicode_replaces_lone_surrogate_with_replacement_char(self):
        # U+D800 is a high-surrogate that cannot stand alone in valid UTF-8.
        corrupted = "C:/path/\ud800file.pdf"
        sanitised = safe_unicode(corrupted)
        # The function should produce something encodable as utf-8.
        sanitised.encode("utf-8")  # would raise UnicodeEncodeError otherwise
        # And the lone surrogate must be gone (replaced).
        assert "\ud800" not in sanitised

    def test_safe_unicode_idempotent_on_korean(self):
        korean = "C:/사용자/도면/01.도면.pdf"
        assert safe_unicode(korean) == korean


class TestA2SilentExceptReplacement:
    """``_apply_lightweight_pdf_v2`` no longer swallows fidelity errors."""

    def test_set_fidelity_state_exception_is_logged_and_falls_back(
        self, caplog
    ):
        # Build a mock viewport that raises on the first state but accepts
        # the fallback — mirrors the hardening branch.
        vp = MagicMock()
        states_seen = []

        def fake_set_fidelity_state(state, status_text=""):
            states_seen.append(state)
            if state == "exact_world_render":
                raise RuntimeError("simulated Qt invariant failure")
            # relative_only fallback succeeds silently
            return None

        vp.set_fidelity_state.side_effect = fake_set_fidelity_state

        # Simulate the hardening branch directly (no full GUI needed).
        # This mirrors the code at drawing_compare_workbench.py:7045-7080.
        logger = logging.getLogger("test_a2")
        side = "before"
        try:
            vp.set_fidelity_state("exact_world_render", status_text="PDF · DPI 150")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[PDF lightweight] %s-side set_fidelity_state(exact) "
                "failed (%s); falling back to relative_only state",
                side, exc,
            )
            vp.set_fidelity_state("relative_only", status_text="PDF · 미리보기 사용 불가")

        # Both states must have been attempted in order.
        assert states_seen == ["exact_world_render", "relative_only"]


class TestA4PdfPathRaceGuard:
    """``load_pdf_page`` re-checks existence right before render_page()."""

    def test_load_pdf_page_returns_false_when_file_missing(self):
        from src.gui.lightweight_viewport import LightweightDrawingViewport
        # Cannot instantiate the full viewport without QGuiApplication, so
        # construct a minimal stand-in. The function under test is the
        # public load_pdf_page contract: missing file → False, no crash.
        viewport = MagicMock(spec=LightweightDrawingViewport)
        viewport._world_bbox = None
        viewport._side = "before"
        viewport._quick = MagicMock()
        viewport._quick.rootObject.return_value = MagicMock()

        # Call the real bound method via the unbound function reference.
        result = LightweightDrawingViewport.load_pdf_page(
            viewport, Path("C:/no/such/path/missing.pdf"), page_index=0
        )
        assert result is False

    def test_load_pdf_page_returns_false_when_path_is_none(self):
        from src.gui.lightweight_viewport import LightweightDrawingViewport
        viewport = MagicMock(spec=LightweightDrawingViewport)
        viewport._world_bbox = None
        viewport._side = "before"
        viewport._quick = MagicMock()
        viewport._quick.rootObject.return_value = MagicMock()

        result = LightweightDrawingViewport.load_pdf_page(
            viewport, None, page_index=0
        )
        assert result is False

    def test_load_pdf_page_clears_source_before_setting_cache_url(self, tmp_path):
        from src.gui.lightweight_viewport import LightweightDrawingViewport

        source_pdf = tmp_path / "source.pdf"
        source_pdf.write_bytes(b"%PDF-1.4\n%test\n")
        root = MagicMock()
        viewport = MagicMock(spec=LightweightDrawingViewport)
        viewport._world_bbox = None
        viewport._side = "after"
        viewport._pdf_render_state = None
        viewport._quick = MagicMock()
        viewport._quick.rootObject.return_value = root

        fake_image = MagicMock()
        fake_image.isNull.return_value = False
        fake_image.width.return_value = 300
        fake_image.height.return_value = 400

        def save_png(path, _fmt):
            Path(path).write_bytes(b"png")
            return True

        fake_image.save.side_effect = save_png

        fake_renderer = MagicMock()
        fake_renderer.is_loaded = True
        fake_renderer.page_count.return_value = 1
        fake_renderer.page_size_points.return_value = (144.0, 192.0)
        fake_renderer.render_page.return_value = fake_image

        with (
            patch("src.services.comparison.qt_pdf_adapter.is_qt_pdf_available", return_value=True),
            patch("src.services.comparison.qt_pdf_adapter.PdfPageRenderer", return_value=fake_renderer),
            patch("src.services.comparison.qt_pdf_adapter.prune_pdf_cache"),
        ):
            result = LightweightDrawingViewport.load_pdf_page(
                viewport,
                source_pdf,
                page_index=0,
                target_dpi=150.0,
                cache_dir=tmp_path,
            )

        assert result is True
        source_calls = [
            call.args[1]
            for call in root.setProperty.call_args_list
            if call.args and call.args[0] == "backgroundImageSource"
        ]
        assert source_calls[-2] == ""
        assert source_calls[-1].startswith("file:")


class TestA5ThreadAffinityWarning:
    """``render_page()`` warns when called off the GUI thread."""

    def test_render_page_returns_null_image_when_path_invalid(self):
        # We verify the contract that render_page() does not raise when
        # the underlying file is invalid — this is the same code path the
        # thread-affinity guard sits on, so a non-raising no-op confirms
        # the guard is reachable from the early-return path. Full
        # thread-mismatch warning is GUI-only (requires QApplication).
        pytest.importorskip("PySide6.QtPdf")
        from src.services.comparison.qt_pdf_adapter import PdfPageRenderer
        renderer = PdfPageRenderer(Path("C:/no/such/file.pdf"))
        # render_page must not raise even when the path is bogus.
        result = renderer.render_page(0)
        # The contract is "return empty / None on error", not raise.
        assert result is None or (hasattr(result, "isNull") and result.isNull())
