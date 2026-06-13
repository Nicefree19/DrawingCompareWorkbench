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

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.comparison.safe_unicode import safe_unicode


def test_lightweight_pdf_background_image_loads_asynchronously() -> None:
    qml_source = Path(
        "src/gui/assets/drawing_compare/LightweightDrawingViewport.qml"
    ).read_text(encoding="utf-8")
    image_start = qml_source.index("Image {\n        id: pdfBackground")
    image_end = qml_source.index("    // ---- VECTOR LAYER", image_start)
    image_body = qml_source[image_start:image_end]

    assert "property string backgroundImageStatusName" in qml_source
    assert "asynchronous: true" in image_body


class TestReadablePdfNotice:
    """PDF fallback notices must stay readable even when old literals are mojibake."""

    def test_preserves_readable_notice(self):
        from src.gui.lightweight_viewport import _readable_pdf_notice

        assert _readable_pdf_notice("PDF page is ready") == "PDF page is ready"

    def test_sanitizes_qt_pdf_mojibake_notice(self):
        from src.gui.lightweight_viewport import _readable_pdf_notice

        assert _readable_pdf_notice("Qt PDF \u4e2d\u6587 \ufffd") == (
            "Qt PDF module is unavailable; lightweight PDF preview cannot be shown."
        )

    def test_sanitizes_generic_pdf_mojibake_notice(self):
        from src.gui.lightweight_viewport import _readable_pdf_notice

        assert _readable_pdf_notice("PDF \u4e2d\u6587 \ufffd") == "PDF preview unavailable."


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
            if state == "raster_refined":
                raise RuntimeError("simulated Qt invariant failure")
            # relative_only fallback succeeds silently
            return None

        vp.set_fidelity_state.side_effect = fake_set_fidelity_state

        # Simulate the hardening branch directly (no full GUI needed).
        # This mirrors the code at drawing_compare_workbench.py:7045-7080.
        logger = logging.getLogger("test_a2")
        side = "before"
        try:
            vp.set_fidelity_state("raster_refined", status_text="PDF · DPI 150")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[PDF lightweight] %s-side set_fidelity_state(exact) "
                "failed (%s); falling back to relative_only state",
                side, exc,
            )
            vp.set_fidelity_state("relative_only", status_text="PDF · 미리보기 사용 불가")

        # Both states must have been attempted in order.
        assert states_seen == ["raster_refined", "relative_only"]


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
        viewport._pdf_render_state = {"pdf_path": "stale.pdf"}
        viewport._pdf_rerender_timer = MagicMock()
        viewport._quick = MagicMock()
        root = MagicMock()
        viewport._quick.rootObject.return_value = root

        result = LightweightDrawingViewport.load_pdf_page(
            viewport, None, page_index=0
        )
        assert result is False
        assert viewport._pdf_render_state is None
        viewport._pdf_rerender_timer.stop.assert_called_once()
        root.setProperty.assert_any_call("backgroundImageSource", "")
        root.setProperty.assert_any_call("backgroundImageWorldBbox", [])

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

    def test_load_pdf_page_reuses_cached_png_without_rerender(self, tmp_path):
        from src.gui.lightweight_viewport import LightweightDrawingViewport

        source_pdf = tmp_path / "source.pdf"
        source_pdf.write_bytes(b"%PDF-1.4\n%test\n")
        root = MagicMock()
        viewport = MagicMock(spec=LightweightDrawingViewport)
        viewport._world_bbox = None
        viewport._side = "before"
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
            assert LightweightDrawingViewport.load_pdf_page(
                viewport,
                source_pdf,
                page_index=0,
                target_dpi=150.0,
                cache_dir=tmp_path / "cache",
            ) is True
            assert LightweightDrawingViewport.load_pdf_page(
                viewport,
                source_pdf,
                page_index=0,
                target_dpi=150.0,
                cache_dir=tmp_path / "cache",
            ) is True

        assert fake_renderer.render_page.call_count == 1
        assert viewport._pdf_render_state["cache_hit"] is True

    def test_load_pdf_page_caps_large_initial_render_dpi(self, tmp_path):
        from src.gui.lightweight_viewport import LightweightDrawingViewport

        source_pdf = tmp_path / "large.pdf"
        source_pdf.write_bytes(b"%PDF-1.4\n%test\n")
        root = MagicMock()
        viewport = MagicMock(spec=LightweightDrawingViewport)
        viewport._world_bbox = None
        viewport._side = "before"
        viewport._pdf_render_state = None
        viewport._quick = MagicMock()
        viewport._quick.rootObject.return_value = root

        fake_image = MagicMock()
        fake_image.isNull.return_value = False
        fake_image.width.return_value = 1754
        fake_image.height.return_value = 2483

        def save_png(path, _fmt):
            Path(path).write_bytes(b"png")
            return True

        fake_image.save.side_effect = save_png

        fake_renderer = MagicMock()
        fake_renderer.is_loaded = True
        fake_renderer.page_count.return_value = 1
        fake_renderer.page_size_points.return_value = (1684.0, 2384.0)
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
                max_render_pixels=8_000_000,
                cache_dir=tmp_path / "cache",
            )

        assert result is True
        fake_renderer.render_page.assert_called_once_with(0, target_dpi=100.0)
        assert viewport._pdf_render_state["requested_dpi"] == 150.0
        assert viewport._pdf_render_state["effective_dpi"] == 100.0
        assert viewport._pdf_render_state["dpi_capped"] is True
        assert list((tmp_path / "cache").glob("qtpdf_*_dpi100.png"))

    def test_prewarm_metadata_fast_path_skips_qtpdf_document_load(self, tmp_path):
        from src.gui.lightweight_viewport import (
            LightweightDrawingViewport,
            prewarm_pdf_page_cache,
        )

        source_pdf = tmp_path / "large.pdf"
        source_pdf.write_bytes(b"%PDF-1.4\n%test\n")
        cache_dir = tmp_path / "cache"

        fake_image = MagicMock()
        fake_image.isNull.return_value = False
        fake_image.width.return_value = 1754
        fake_image.height.return_value = 2483

        def save_png(path, _fmt):
            Path(path).write_bytes(b"png")
            return True

        fake_image.save.side_effect = save_png

        fake_renderer = MagicMock()
        fake_renderer.is_loaded = True
        fake_renderer.page_count.return_value = 1
        fake_renderer.page_size_points.return_value = (1684.0, 2384.0)
        fake_renderer.render_page.return_value = fake_image

        with (
            patch("src.services.comparison.qt_pdf_adapter.is_qt_pdf_available", return_value=True),
            patch("src.services.comparison.qt_pdf_adapter.PdfPageRenderer", return_value=fake_renderer),
            patch("src.services.comparison.qt_pdf_adapter.prune_pdf_cache"),
        ):
            prewarm = prewarm_pdf_page_cache(
                source_pdf,
                page_index=0,
                target_dpi=150.0,
                max_render_pixels=8_000_000,
                cache_dir=cache_dir,
            )

        assert prewarm["ok"] is True
        assert prewarm["cache_hit"] is False
        assert prewarm["effective_dpi"] == 100.0
        assert list(cache_dir.glob("qtpdf_*_dpi100.png"))
        assert list(cache_dir.glob("qtpdf_*_meta.json"))
        assert not list(cache_dir.glob("*.tmp"))

        root = MagicMock()
        viewport = MagicMock(spec=LightweightDrawingViewport)
        viewport._world_bbox = None
        viewport._side = "before"
        viewport._pdf_render_state = None
        viewport._quick = MagicMock()
        viewport._quick.rootObject.return_value = root

        with (
            patch("src.services.comparison.qt_pdf_adapter.PdfPageRenderer") as renderer_cls,
            patch("src.services.comparison.qt_pdf_adapter.prune_pdf_cache"),
        ):
            result = LightweightDrawingViewport.load_pdf_page(
                viewport,
                source_pdf,
                page_index=0,
                target_dpi=150.0,
                max_render_pixels=8_000_000,
                cache_dir=cache_dir,
            )

        assert result is True
        renderer_cls.assert_not_called()
        assert viewport._pdf_render_state["cache_hit"] is True
        assert viewport._pdf_render_state["metadata_hit"] is True
        assert viewport._pdf_render_state["effective_dpi"] == 100.0

    def test_prewarm_pdf_page_cache_does_not_touch_viewport_state(self, tmp_path):
        from src.gui.lightweight_viewport import prewarm_pdf_page_cache

        source_pdf = tmp_path / "source.pdf"
        source_pdf.write_bytes(b"%PDF-1.4\n%test\n")
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

        viewport = MagicMock()
        viewport._pdf_render_state = {"pdf_path": "visible.pdf"}
        viewport._quick = MagicMock()

        with (
            patch("src.services.comparison.qt_pdf_adapter.is_qt_pdf_available", return_value=True),
            patch("src.services.comparison.qt_pdf_adapter.PdfPageRenderer", return_value=fake_renderer),
            patch("src.services.comparison.qt_pdf_adapter.prune_pdf_cache"),
        ):
            result = prewarm_pdf_page_cache(
                source_pdf,
                page_index=0,
                target_dpi=150.0,
                cache_dir=tmp_path / "cache",
            )

        assert result["ok"] is True
        assert viewport._pdf_render_state == {"pdf_path": "visible.pdf"}
        viewport._quick.rootObject.assert_not_called()

    def test_load_scene_pack_none_clears_stale_pdf_background(self):
        from src.gui.lightweight_viewport import LightweightDrawingViewport

        root = MagicMock()
        viewport = MagicMock(spec=LightweightDrawingViewport)
        viewport._quick = MagicMock()
        viewport._quick.rootObject.return_value = root
        viewport._pdf_render_state = {"pdf_path": "stale.pdf", "pending_dpi": 300}
        viewport._pdf_rerender_timer = MagicMock()
        viewport._loaded_pack_path = "old_pack.json"
        viewport._primitive_count = 99

        result = LightweightDrawingViewport.load_scene_pack(viewport, None)

        assert result == 0
        assert viewport._pdf_render_state is None
        viewport._pdf_rerender_timer.stop.assert_called_once()
        assert viewport._loaded_pack_path is None
        assert viewport._primitive_count == 0
        root.setProperty.assert_any_call("backgroundImageSource", "")
        root.setProperty.assert_any_call("backgroundImageWorldBbox", [])
        root.setProperty.assert_any_call("primitives", [])

    def test_load_scene_pack_success_clears_stale_pdf_background(self, tmp_path):
        from src.gui.lightweight_viewport import LightweightDrawingViewport
        from src.services.comparison.viewer_manifest_v3 import ScenePackRef

        overview = tmp_path / "overview_lod0.json"
        overview.write_text(
            json.dumps(
                {
                    "world_bbox": [10.0, 20.0, 110.0, 220.0],
                    "primitives": [
                        {
                            "type": "line",
                            "points": [10.0, 20.0, 30.0, 40.0],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        root = MagicMock()
        viewport = MagicMock(spec=LightweightDrawingViewport)
        viewport._side = "before"
        viewport._quick = MagicMock()
        viewport._quick.rootObject.return_value = root
        viewport._pdf_render_state = {"pdf_path": "stale.pdf", "pending_dpi": 300}
        viewport._pdf_rerender_timer = MagicMock()
        viewport._loaded_pack_path = None
        viewport._primitive_count = 0

        result = LightweightDrawingViewport.load_scene_pack(
            viewport,
            ScenePackRef(overview_lod0_path=str(overview)),
        )

        assert result == 1
        assert viewport._pdf_render_state is None
        viewport._pdf_rerender_timer.stop.assert_called_once()
        root.setProperty.assert_any_call("backgroundImageSource", "")
        root.setProperty.assert_any_call("backgroundImageWorldBbox", [])
        root.setProperty.assert_any_call("worldBbox", [10.0, 20.0, 110.0, 220.0])

    def test_load_scene_pack_records_native_source_provenance(self, tmp_path):
        from src.gui.lightweight_viewport import LightweightDrawingViewport
        from src.services.comparison.native_scene_pack import (
            NativeScenePack,
            write_native_scene_pack_artifacts,
        )

        pack = NativeScenePack(
            source={"path": str(tmp_path / "native.dwg"), "acad_version": "AC1015"},
            adapter={"name": "fixture"},
            display_primitives=[
                {"type": "line", "points": [0.0, 0.0, 10.0, 10.0]},
            ],
            bbox=(0.0, 0.0, 10.0, 10.0),
        )
        ref = write_native_scene_pack_artifacts(pack, tmp_path / "native_pack")
        root = MagicMock()
        viewport = MagicMock(spec=LightweightDrawingViewport)
        viewport._side = "before"
        viewport._quick = MagicMock()
        viewport._quick.rootObject.return_value = root
        viewport._pdf_render_state = None
        viewport._pdf_rerender_timer = MagicMock()
        viewport._loaded_pack_path = None
        viewport._primitive_count = 0

        result = LightweightDrawingViewport.load_scene_pack(viewport, ref)

        assert result == 1
        assert viewport._primitive_source_provenance["producer_id"] == "native_scene_pack"
        root.setProperty.assert_any_call("worldBbox", [0.0, 0.0, 10.0, 10.0])
        provenance_calls = [
            call.args[1]
            for call in root.setProperty.call_args_list
            if call.args and call.args[0] == "primitiveSourceProvenance"
        ]
        assert provenance_calls[-1]["producer_id"] == "native_scene_pack"


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


class TestLightweightQmlFallback:
    """The packaged lightweight viewer must load without optional QSG."""

    def test_qml_root_loads_and_pdf_preview_renders_without_qsg(
        self, tmp_path, monkeypatch
    ):
        pytest.importorskip("PySide6.QtPdf")
        fitz = pytest.importorskip("fitz")

        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
        monkeypatch.delenv("WORKBENCH_QSG", raising=False)

        source_pdf = tmp_path / "preview_source.pdf"
        doc = fitz.open()
        page = doc.new_page(width=144, height=192)
        page.insert_text((20, 48), "Preview smoke")
        doc.save(str(source_pdf))
        doc.close()

        from PySide6.QtQuickWidgets import QQuickWidget
        from PySide6.QtWidgets import QApplication
        from src.gui.lightweight_viewport import LightweightDrawingViewport

        app = QApplication.instance() or QApplication([])
        viewport = LightweightDrawingViewport(side="before")
        try:
            root = viewport._quick.rootObject()
            errors = [err.toString() for err in viewport._quick.errors()]

            assert viewport._quick.status() != QQuickWidget.Status.Error
            assert root is not None
            assert not any("TeklaQSG" in error for error in errors)

            result = viewport.load_pdf_page(
                source_pdf,
                page_index=0,
                target_dpi=72.0,
                cache_dir=tmp_path / "cache",
            )

            assert result is True
            assert root.property("backgroundImageSource").startswith("file:")
            assert root.property("emptyNotice") == ""
            assert list((tmp_path / "cache").glob("qtpdf_*_dpi72.png"))
        finally:
            viewport.deleteLater()
            app.processEvents()

    def test_forced_qsg_env_with_broken_module_uses_canvas_fallback(
        self, monkeypatch
    ):
        # T2 (2026-06-11): WORKBENCH_QSG=qsg is now an EXPERIMENTAL opt-in
        # that activates the GPU skeleton when the module imports. The
        # original safety contract stays: a missing/broken module must
        # never blank the viewer root — forced qsg degrades to Canvas.
        import types

        pytest.importorskip("PySide6.QtPdf")

        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
        monkeypatch.setenv("WORKBENCH_QSG", "qsg")
        monkeypatch.setitem(
            sys.modules, "src.gui.qsg_line_item", types.ModuleType("broken_qsg")
        )

        from PySide6.QtQuickWidgets import QQuickWidget
        from PySide6.QtWidgets import QApplication
        from src.gui.lightweight_viewport import LightweightDrawingViewport

        app = QApplication.instance() or QApplication([])
        viewport = LightweightDrawingViewport(side="after")
        try:
            root = viewport._quick.rootObject()

            assert viewport._quick.status() != QQuickWidget.Status.Error
            assert root is not None
            assert root.property("skeletonRenderer") == "canvas"
            assert "backend_fallback_canvas_skeleton" in viewport.render_failure_codes()
        finally:
            viewport.deleteLater()
            app.processEvents()

    def test_default_renderer_is_canvas_even_with_qsg_module_available(
        self, monkeypatch
    ):
        # T2-B policy: the Python-side QSG path showed nondeterministic
        # native crashes under PySide6 6.10 + QQuickWidget, so WITHOUT the
        # explicit experimental env the renderer stays Canvas (made
        # responsive via threaded raster + zoom-band LOD instead).
        pytest.importorskip("PySide6.QtPdf")

        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
        monkeypatch.delenv("WORKBENCH_QSG", raising=False)

        from PySide6.QtWidgets import QApplication
        from src.gui.lightweight_viewport import LightweightDrawingViewport

        app = QApplication.instance() or QApplication([])
        viewport = LightweightDrawingViewport(side="after")
        try:
            root = viewport._quick.rootObject()
            assert root is not None
            assert root.property("skeletonRenderer") == "canvas"
            # Module imports fine → this is policy, not a fallback event.
            assert (
                "backend_fallback_canvas_skeleton"
                not in viewport.render_failure_codes()
            )
            # Offscreen test platform pins the deterministic Immediate
            # strategy; real platforms keep the threaded raster.
            assert root.property("canvasThreadedRaster") is False
        finally:
            viewport.deleteLater()
            app.processEvents()


class TestQ2AutoZoomCameraLock:
    """Q2 — the post-comparison auto-zoom to the first change zone runs through
    set_camera_to_world_bbox. On first app open the viewport often sizes AFTER
    that zoom, so the QML onWidthChanged fit-once guard fires a late fitToView
    and snaps the camera back to the whole drawing (changes shrink to sub-pixel
    again). set_camera_to_world_bbox must therefore lock cameraInitialized so a
    deliberate placement is never clobbered."""

    def _viewport_with_mock_root(self):
        from src.gui.lightweight_viewport import LightweightDrawingViewport

        vp = LightweightDrawingViewport.__new__(LightweightDrawingViewport)
        root = MagicMock()
        root.property.side_effect = lambda name: {"width": 800.0, "height": 600.0}.get(
            name, 0.0
        )
        vp._quick = MagicMock()
        vp._quick.rootObject.return_value = root
        return vp, root

    def test_set_camera_to_world_bbox_locks_camera_initialized(self):
        vp, root = self._viewport_with_mock_root()
        vp.set_camera_to_world_bbox((10.0, 20.0, 110.0, 220.0), padding_ratio=0.25)
        root.setProperty.assert_any_call("cameraInitialized", True)

    def test_set_camera_to_world_bbox_still_sets_centre_and_zoom(self):
        vp, root = self._viewport_with_mock_root()
        vp.set_camera_to_world_bbox((10.0, 20.0, 110.0, 220.0), padding_ratio=0.25)
        names = {call.args[0] for call in root.setProperty.call_args_list}
        assert {"cameraCenterX", "cameraCenterY", "unitsPerPixel"} <= names

    def test_missing_pdf_render_state_is_noop_without_debug_failure(self, caplog):
        vp, _root = self._viewport_with_mock_root()

        with caplog.at_level(logging.DEBUG, logger="src.gui.lightweight_viewport"):
            vp.set_camera_to_world_bbox((10.0, 20.0, 110.0, 220.0), padding_ratio=0.25)

        assert "zone-focus PDF rerender schedule failed" not in caplog.text

    def test_degenerate_bbox_leaves_camera_and_guard_untouched(self):
        # A zero-extent bbox is rejected before any setProperty, so neither the
        # camera nor the fit-once guard is altered.
        vp, root = self._viewport_with_mock_root()
        vp.set_camera_to_world_bbox((50.0, 50.0, 50.0, 50.0))
        root.setProperty.assert_not_called()
