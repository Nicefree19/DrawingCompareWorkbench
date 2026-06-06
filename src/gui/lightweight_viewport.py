# -*- coding: utf-8 -*-
"""LightweightDrawingViewport — Python widget for the diff-steered viewer.

Phase G2.2. Replaces the raster-PNG ``GpuDrawingViewport`` with a pure
vector renderer. Loads scene packs produced by Phase G1's
``scene_pack_builder`` and pushes the LOD0 skeleton primitives directly
to a QML Canvas where they are drawn natively. The result stays sharp
at any zoom level and paints in milliseconds even for large drawings.

Layered design (matches ``LightweightDrawingViewport.qml``):

* skeleton: lines + path primitives from ``overview_lod0.json``
* vector_focus: optional zone vector micro-pack (G2.3 will populate)
* change overlays: cloud bbox + focus marker (existing v2 model)
* state badge: 7-state ``RenderMode`` from ``render_modes.RENDER_MODE_STYLES``

The widget is a drop-in replacement for ``GpuDrawingViewport`` — same
``load_preview`` / ``set_overlays`` / ``set_overlay_opacity_scale`` /
``set_fidelity_state`` API surface. The Workbench can swap the two
viewports behind a feature flag without touching the overlay model.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QUrl, Qt, Signal
from PySide6.QtGui import QImageReader
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QStackedLayout, QWidget

from src.services.comparison.render_failure_codes import RenderFailureCode
from src.services.comparison.render_modes import RenderMode, style_for
from src.services.comparison.transform import (
    convert_bbox_to_world_space as _convert_bbox_to_world_space_contract,
    normalise_bbox as _normalise_bbox_contract,
)
from src.services.comparison.viewer_manifest_v3 import ScenePackRef
from src.utils.once_per_session_logger import log_once

logger = logging.getLogger(__name__)

MAX_QML_CHANGE_CLOUD_OVERLAYS = 120
FOCUS_ONLY_CHANGE_OVERLAY_SOURCE_THRESHOLD = 300
PDF_CACHE_MAX_BYTES = 200 * 1024 * 1024


class _FallbackSignal:
    def connect(self, *_args: object, **_kwargs: object) -> None:
        return None


class _FallbackQuickRoot:
    def __init__(self) -> None:
        self._properties: dict[str, object] = {}
        self.viewportChanged = _FallbackSignal()
        self.overlayClicked = _FallbackSignal()

    def setProperty(self, name: str, value: object) -> bool:
        self._properties[str(name)] = value
        return True

    def property(self, name: str) -> object:
        return self._properties.get(str(name), "")


class _FallbackQuickWidget(QWidget):
    """Minimal QWidget stand-in used when QQuickWidget is unavailable or mocked.

    S1.3.4: callers can detect this stand-in via ``isinstance`` and the
    ``failure_code`` class attribute lets static analysis confirm the
    code that the GUI badge (S1.4) will display.
    """

    failure_code: RenderFailureCode = "backend_fallback_qquickwidget"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._source = QUrl()
        self._root = _FallbackQuickRoot()

    def setResizeMode(self, *_args: object, **_kwargs: object) -> None:
        return None

    def setSource(self, source: QUrl) -> None:
        self._source = source

    def setClearColor(self, *_args: object, **_kwargs: object) -> None:
        return None

    def status(self) -> int:
        return 1

    def errors(self) -> list[object]:
        return []

    def rootObject(self) -> _FallbackQuickRoot:
        return self._root


def _create_quick_widget(parent: QWidget) -> QWidget:
    try:
        widget = QQuickWidget(parent)
        if not isinstance(widget, QWidget):
            raise TypeError(f"QQuickWidget returned non-QWidget {type(widget)!r}")
        return widget
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "QQuickWidget unavailable or invalid (%s); using QWidget fallback",
            exc,
        )
        return _FallbackQuickWidget(parent)


def _normalise_bbox(raw) -> Optional[tuple[float, float, float, float]]:
    """Return a 4-tuple of floats from any common bbox representation.

    Real production v1 overlays use the dict form
    ``{"min_x", "min_y", "max_x", "max_y"}``. Some test fixtures + the
    viewer manifest v3 schema use the 4-element list form
    ``[x0, y0, x1, y1]``. This helper accepts both. Returns ``None`` for
    anything we can't interpret so callers can skip invalid overlays
    without crashing.
    """

    return _normalise_bbox_contract(raw)


def convert_bbox_to_world_space(
    bbox: object,
    *,
    coordinate_space: str = "",
    pdf_dpi: float = 0.0,
    page_height_points: float = 0.0,
) -> Optional[tuple[float, float, float, float]]:
    """Phase G2.7-COORDFIX — translate an overlay bbox to the lightweight
    viewport's world space.

    The viewport's world space is determined by the loaded background:
      * **PDF backgrounds** (``load_pdf_page``) use **PDF points** — i.e.
        ``world_bbox = (0, 0, page_width_pt, page_height_pt)`` regardless
        of the raster DPI used to actually render the page bitmap.
      * **DXF/DWG backgrounds** use **CAD world units** (mm/m).

    The comparison engine, however, stamps PDF overlays as
    ``bbox_coordinate_space == "image_pixels"`` with pixel coords measured
    at ``pdf_dpi`` (e.g. 200). To land overlays correctly on the PDF
    background we therefore need the conversion ``pt = px * 72 / pdf_dpi``.
    QML world coordinates are Y-up, while PDF image pixels are Y-down, so
    callers that know the page height should pass ``page_height_points`` to
    flip Y into the same world space as ``backgroundImageWorldBbox``.

    DXF/DWG overlays carry no ``bbox_coordinate_space`` (or an empty/world
    value) and pass through unchanged.

    Returns the converted ``(x0, y0, x1, y1)`` tuple, or ``None`` if the
    input bbox couldn't be parsed (degenerate / missing keys).
    """

    return _convert_bbox_to_world_space_contract(
        bbox,
        coordinate_space=coordinate_space,
        pdf_dpi=pdf_dpi,
        page_height_points=page_height_points,
    )


def _page_height_points_from_world_bbox(
    world_bbox: object,
) -> float:
    """Return PDF page height from a viewport world bbox when available."""

    coords = _normalise_bbox(world_bbox)
    if coords is None:
        return 0.0
    _x0, y0, _x1, y1 = coords
    height = abs(float(y1) - float(y0))
    return height if height > 0 else 0.0


def _stop_pdf_rerender_timer(viewport: Any) -> None:
    timer = getattr(viewport, "_pdf_rerender_timer", None)
    if timer is None:
        return
    try:
        timer.stop()
    except Exception:
        logger.debug("PDF rerender timer stop failed", exc_info=True)


def _clear_pdf_background_state(viewport: Any, root: Any) -> None:
    """Clear any raster/PDF background and stale PDF rerender state."""

    root.setProperty("backgroundImageSource", "")
    root.setProperty("backgroundImageWorldBbox", [])
    viewport._pdf_render_state = None
    _stop_pdf_rerender_timer(viewport)


def _readable_pdf_notice(notice: str) -> str:
    """Return a readable PDF notice even if an older literal is mojibake."""

    text = str(notice or "")
    if not any("\u4e00" <= char <= "\u9fff" or char == "\ufffd" for char in text):
        return text
    if "Qt PDF" in text:
        return "Qt PDF module is unavailable; lightweight PDF preview cannot be shown."
    if "PDF" in text:
        return "PDF preview unavailable."
    return "Preview unavailable."


def _default_pdf_cache_dir() -> Path:
    import tempfile

    return Path(tempfile.gettempdir()) / "tekla_mcp_qtpdf_cache"


def _pdf_source_signature(source_path: Path) -> str:
    stat = source_path.stat()
    return f"{source_path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"


# Zoom-in re-render pixel cap. The initial page load uses ~5 MP (proven to
# display). An UNCAPPED zoom re-render asked for 600 DPI and produced a
# 5793x8192 (~47 MP) image that the Canvas-fallback viewport could not display
# -> the PDF background went blank ("실배경 아님"). 10 MP keeps the largest
# dimension well under the failing 8192 while still rendering sharper than the
# 150-DPI base. (Crisp deep-zoom ultimately needs region-cropped rendering.)
_PDF_RERENDER_MAX_PIXELS = 10_000_000


def _normalise_pdf_pixel_budget(max_render_pixels: Optional[int]) -> Optional[int]:
    if max_render_pixels is None:
        return None
    try:
        value = int(max_render_pixels)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _pdf_cache_png_path(
    cache_dir: Path,
    *,
    source_sig: str,
    page_index: int,
    effective_dpi: float,
) -> Path:
    dpi_int = int(float(effective_dpi))
    stem_key = hashlib.sha1(
        f"{source_sig}|{int(page_index)}|{dpi_int}".encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return cache_dir / f"qtpdf_{stem_key}_dpi{dpi_int}.png"


def _pdf_cache_metadata_path(
    cache_dir: Path,
    *,
    source_sig: str,
    page_index: int,
    requested_dpi: float,
    max_render_pixels: Optional[int],
) -> Path:
    pixel_budget = _normalise_pdf_pixel_budget(max_render_pixels)
    meta_key = hashlib.sha1(
        f"{source_sig}|{int(page_index)}|{int(float(requested_dpi))}|{pixel_budget or 0}".encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return cache_dir / f"qtpdf_{meta_key}_meta.json"


def _read_pdf_cache_metadata(
    cache_dir: Path,
    *,
    source_sig: str,
    page_index: int,
    requested_dpi: float,
    max_render_pixels: Optional[int],
) -> Optional[dict[str, Any]]:
    meta_path = _pdf_cache_metadata_path(
        cache_dir,
        source_sig=source_sig,
        page_index=page_index,
        requested_dpi=requested_dpi,
        max_render_pixels=max_render_pixels,
    )
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("source_sig") or "") != source_sig:
        return None
    try:
        if int(payload.get("page_index")) != int(page_index):
            return None
        if int(float(payload.get("requested_dpi"))) != int(float(requested_dpi)):
            return None
    except (TypeError, ValueError):
        return None
    pixel_budget = _normalise_pdf_pixel_budget(max_render_pixels)
    if _normalise_pdf_pixel_budget(payload.get("max_render_pixels")) != pixel_budget:
        return None
    cached_name = str(payload.get("cached_png") or "")
    if not cached_name:
        return None
    cached_path = cache_dir / cached_name
    try:
        if not cached_path.exists() or cached_path.stat().st_size <= 0:
            return None
    except OSError:
        return None
    try:
        w_pts = float(payload.get("page_width_points"))
        h_pts = float(payload.get("page_height_points"))
        effective_dpi = float(payload.get("effective_dpi"))
    except (TypeError, ValueError):
        return None
    if w_pts <= 0 or h_pts <= 0 or effective_dpi <= 0:
        return None
    payload["cached_path"] = cached_path
    return payload


def _write_pdf_cache_metadata(
    cache_dir: Path,
    *,
    source_sig: str,
    source_path: Path,
    page_index: int,
    requested_dpi: float,
    effective_dpi: float,
    max_render_pixels: Optional[int],
    page_size_points: tuple[float, float],
    cached_png: Path,
    pixel_size: tuple[int, int] | None = None,
) -> Path:
    from src.services.comparison.safe_unicode import safe_unicode

    meta_path = _pdf_cache_metadata_path(
        cache_dir,
        source_sig=source_sig,
        page_index=page_index,
        requested_dpi=requested_dpi,
        max_render_pixels=max_render_pixels,
    )
    pixel_w, pixel_h = pixel_size or (0, 0)
    payload = {
        "schema_version": "qtpdf-cache-metadata/v1",
        "source_sig": source_sig,
        "source_path": str(source_path),
        "page_index": int(page_index),
        "requested_dpi": float(requested_dpi),
        "effective_dpi": float(effective_dpi),
        "dpi_capped": float(effective_dpi) < float(requested_dpi) - 0.01,
        "max_render_pixels": _normalise_pdf_pixel_budget(max_render_pixels),
        "page_width_points": float(page_size_points[0]),
        "page_height_points": float(page_size_points[1]),
        "cached_png": cached_png.name,
        "pixel_width": int(pixel_w),
        "pixel_height": int(pixel_h),
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = meta_path.with_name(f"{meta_path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(safe_unicode(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(meta_path)
    return meta_path


def _save_png_atomic(image: Any, cached_png: Path) -> bool:
    cached_png.parent.mkdir(parents=True, exist_ok=True)
    tmp = cached_png.with_name(f"{cached_png.name}.{os.getpid()}.tmp")
    try:
        if not image.save(str(tmp), "PNG"):
            return False
        tmp.replace(cached_png)
        return True
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def prewarm_pdf_page_cache(
    pdf_path: Optional[Path],
    page_index: int = 0,
    *,
    target_dpi: float = 150.0,
    cache_dir: Optional[Path] = None,
    max_render_pixels: Optional[int] = None,
) -> dict[str, Any]:
    """Render a PDF page into the lightweight-viewer cache without UI state.

    The helper is used by idle adjacent-page prewarm and by benchmarks. It
    intentionally does not touch QML, viewport state, or overlays.
    """

    if pdf_path is None or not Path(pdf_path).exists():
        return {"ok": False, "reason": "missing_pdf", "cache_hit": False}
    source_path = Path(pdf_path)
    cache_root = Path(cache_dir) if cache_dir is not None else _default_pdf_cache_dir()
    cache_root.mkdir(parents=True, exist_ok=True)
    requested_dpi = max(10.0, float(target_dpi))
    pixel_budget = _normalise_pdf_pixel_budget(max_render_pixels)
    try:
        source_sig = _pdf_source_signature(source_path)
    except OSError as exc:
        return {"ok": False, "reason": f"stat_failed:{exc}", "cache_hit": False}

    metadata = _read_pdf_cache_metadata(
        cache_root,
        source_sig=source_sig,
        page_index=page_index,
        requested_dpi=requested_dpi,
        max_render_pixels=pixel_budget,
    )
    if metadata is not None:
        return {
            "ok": True,
            "cache_hit": True,
            "metadata_hit": True,
            "cached_png": str(metadata.get("cached_path") or ""),
            "effective_dpi": float(metadata.get("effective_dpi") or requested_dpi),
            "dpi_capped": bool(metadata.get("dpi_capped")),
        }

    from src.services.comparison.qt_pdf_adapter import (
        PdfPageRenderer,
        is_qt_pdf_available,
        prune_pdf_cache,
        select_initial_pdf_render_dpi,
    )

    if not is_qt_pdf_available():
        return {"ok": False, "reason": "qtpdf_unavailable", "cache_hit": False}
    renderer = PdfPageRenderer(source_path)
    try:
        renderer._ensure_loaded()  # noqa: SLF001 - same pre-flight as load_pdf_page
        if not renderer.is_loaded:
            return {"ok": False, "reason": "pdf_load_failed", "cache_hit": False}
        if page_index < 0 or page_index >= renderer.page_count():
            return {"ok": False, "reason": "page_out_of_range", "cache_hit": False}
        w_pts, h_pts = renderer.page_size_points(page_index)
        if w_pts <= 0 or h_pts <= 0:
            return {"ok": False, "reason": "invalid_page_size", "cache_hit": False}
        effective_dpi = select_initial_pdf_render_dpi(
            (float(w_pts), float(h_pts)),
            target_dpi=requested_dpi,
            max_pixels=pixel_budget,
        )
        cached_png = _pdf_cache_png_path(
            cache_root,
            source_sig=source_sig,
            page_index=page_index,
            effective_dpi=effective_dpi,
        )
        try:
            if cached_png.exists() and cached_png.stat().st_size > 0:
                _write_pdf_cache_metadata(
                    cache_root,
                    source_sig=source_sig,
                    source_path=source_path,
                    page_index=page_index,
                    requested_dpi=requested_dpi,
                    effective_dpi=effective_dpi,
                    max_render_pixels=pixel_budget,
                    page_size_points=(float(w_pts), float(h_pts)),
                    cached_png=cached_png,
                )
                return {
                    "ok": True,
                    "cache_hit": True,
                    "metadata_hit": False,
                    "cached_png": str(cached_png),
                    "effective_dpi": float(effective_dpi),
                    "dpi_capped": float(effective_dpi) < requested_dpi - 0.01,
                }
        except OSError:
            logger.debug("PDF prewarm cache stat failed for %s", cached_png, exc_info=True)

        if not source_path.exists():
            return {"ok": False, "reason": "source_disappeared", "cache_hit": False}
        img = renderer.render_page(page_index, target_dpi=float(effective_dpi))
        if img is None or img.isNull():
            return {"ok": False, "reason": "render_failed", "cache_hit": False}
        if not _save_png_atomic(img, cached_png):
            return {"ok": False, "reason": "cache_save_failed", "cache_hit": False}
        _write_pdf_cache_metadata(
            cache_root,
            source_sig=source_sig,
            source_path=source_path,
            page_index=page_index,
            requested_dpi=requested_dpi,
            effective_dpi=effective_dpi,
            max_render_pixels=pixel_budget,
            page_size_points=(float(w_pts), float(h_pts)),
            cached_png=cached_png,
            pixel_size=(int(img.width()), int(img.height())),
        )
        try:
            prune_pdf_cache(cache_root, max_bytes=PDF_CACHE_MAX_BYTES)
        except Exception:
            logger.debug("prune_pdf_cache raised during prewarm", exc_info=True)
        return {
            "ok": True,
            "cache_hit": False,
            "metadata_hit": False,
            "cached_png": str(cached_png),
            "effective_dpi": float(effective_dpi),
            "dpi_capped": float(effective_dpi) < requested_dpi - 0.01,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("PDF cache prewarm failed")
        return {"ok": False, "reason": f"{exc.__class__.__name__}: {exc}", "cache_hit": False}
    finally:
        try:
            renderer.close()
        except Exception:
            pass


def _resolve_qml_asset(name: str) -> Path:
    """Same resolver pattern the existing Workbench uses for QML asset
    discovery — works in both the development tree and a PyInstaller
    frozen bundle (``sys._MEIPASS``)."""

    candidates = [
        Path(__file__).resolve().parent / "assets" / "drawing_compare" / name,
    ]
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        root = Path(frozen_root)
        candidates.extend([
            root / "src" / "gui" / "assets" / "drawing_compare" / name,
            root / "assets" / "drawing_compare" / name,
            root / "drawing_compare" / name,
        ])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


class LightweightDrawingViewport(QWidget):
    """Drop-in Phase G2.2 viewer widget.

    Public API (mirrors ``GpuDrawingViewport`` so the Workbench can
    swap with one branch):

    * :meth:`load_scene_pack` — primary entry: load primitives from a
      ``ScenePackRef`` (LOD0 skeleton subset). Pushes to QML Canvas.
    * :meth:`set_overlays` — push cloud + focus overlay model (Phase F shape).
    * :meth:`set_overlay_opacity_scale` — same clamp [0.3, 1.0] as the legacy.
    * :meth:`set_fidelity_state` — drives the 7-state badge + watermark.
    * :meth:`fit_to_view` — recompute the world→pixel transform.
    """

    viewportChanged = Signal(float, float, float)  # center_x, center_y, units_per_pixel
    # Phase I4 — re-emitted from QML root.overlayClicked when the user
    # clicks a cloud/focus marker. Workbench connects this to
    # _select_zone_in_list_v2 so the zone tree auto-selects the clicked zone.
    overlayClicked = Signal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        side: str = "after",
    ) -> None:
        super().__init__(parent)
        self._side = side
        self._overlay_opacity_scale: float = 1.0
        self._render_mode: RenderMode = "relative_only"
        self._world_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self._loaded_pack_path: Optional[str] = None
        self._primitive_count: int = 0
        # S1.3.4: silent-fallback codes accumulated during __init__.
        # Surfaces QSGLineItem unavailability (Point 4) and QQuickWidget
        # fallback (Point 3) so the FailureBadge (S1.4) can show them.
        self._render_failure_codes: list[RenderFailureCode] = []
        # Phase G2.7-FU2 — track per-PDF render state so we can re-render
        # at higher DPI when the user zooms in. Cleared on non-PDF loads.
        # Keys:
        #   "pdf_path", "page_index", "current_dpi", "base_upp",
        #   "cache_dir"
        self._pdf_render_state: Optional[dict] = None
        # Debounce timer for zoom-triggered re-render. We only schedule a
        # re-render after the camera has been still for a moment so the
        # user can mid-zoom without firing N rapid renders.
        self._pdf_rerender_timer: Optional["QTimer"] = None

        self._layout = QStackedLayout(self)
        self._layout.setStackingMode(QStackedLayout.StackOne)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # Optional QSG acceleration. The QML intentionally has no static
        # `import TeklaQSG` dependency because packaged builds may omit the
        # native extension; a missing QSG module must not blank PDF preview.
        try:
            from src.gui.qsg_line_item import register_qml_type
            register_qml_type()
            self._qsg_available = True
        except Exception as exc:  # noqa: BLE001
            log_once(
                logger,
                logging.INFO,
                "lightweight_viewport.qsg_line_item_unavailable",
                "QSGLineItem unavailable (%s); using Canvas skeleton", exc
            )
            self._qsg_available = False
            # S1.3.4 Point 4: surface the Canvas fallback so the badge
            # can show ℹ️ "QSGLineItem 모듈 없음 — 표준 Canvas 사용".
            self._render_failure_codes.append("backend_fallback_canvas_skeleton")

        # Resolve renderer choice from env var (operator override). This
        # standalone QML is Canvas-safe; do not allow WORKBENCH_QSG=qsg to
        # hide the Canvas when the optional QSG item is absent.
        env_choice = os.environ.get("WORKBENCH_QSG", "auto").strip().lower()
        if env_choice == "qsg":
            logger.warning(
                "WORKBENCH_QSG=qsg ignored in this build; using Canvas "
                "skeleton so the lightweight viewer root remains loadable"
            )
        self._skeleton_renderer = "canvas"

        self._quick = _create_quick_widget(self)
        # S1.3.4 Point 3: surface the QQuickWidget fallback so the badge
        # can show ⚠️ "Qt Quick 위젯 사용 불가 — 호환 모드 동작 중".
        if isinstance(self._quick, _FallbackQuickWidget):
            self._render_failure_codes.append("backend_fallback_qquickwidget")
        try:
            self._quick.setResizeMode(QQuickWidget.SizeRootObjectToView)
        except Exception:
            logger.debug("LightweightViewport: setResizeMode unavailable on fallback")
        self._quick.setAttribute(Qt.WA_AlwaysStackOnTop)
        self._quick.setClearColor(Qt.transparent)

        qml_path = _resolve_qml_asset("LightweightDrawingViewport.qml")
        self._quick.setSource(QUrl.fromLocalFile(str(qml_path)))

        self._layout.addWidget(self._quick)
        self.setLayout(self._layout)

        # Defer signal wiring until rootObject is ready. setSource is
        # synchronous in QQuickWidget, so we can wire immediately.
        root = self._quick.rootObject()
        if root is not None:
            try:
                root.viewportChanged.connect(self._on_qml_viewport_changed)
            except Exception:
                logger.debug("LightweightViewport: viewportChanged signal "
                             "missing or already wired")
            # Phase I4 — overlay click forwarded to Qt signal for the
            # workbench tree auto-select.
            try:
                root.overlayClicked.connect(self._on_qml_overlay_clicked)
            except Exception:
                logger.debug("LightweightViewport: overlayClicked signal "
                             "missing or already wired", exc_info=True)
            # Apply default fidelity state so the badge isn't empty.
            self._apply_fidelity_to_qml(self._render_mode)
            # Phase G3 — tell QML which skeleton renderer to use.
            try:
                root.setProperty("skeletonRenderer", self._skeleton_renderer)
            except Exception:
                logger.debug(
                    "LightweightViewport: skeletonRenderer property unavailable",
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # S1.3.4 — Silent fallback visibility
    # ------------------------------------------------------------------

    def render_failure_codes(self) -> tuple[RenderFailureCode, ...]:
        """Return RenderFailureCodes accumulated during viewport setup.

        S1.3.4: the constructor accumulates fallback events into
        ``self._render_failure_codes``:

        * ``backend_fallback_canvas_skeleton`` (info) — when the
          optional QSGLineItem extension isn't importable. Canvas
          rendering is the normal degraded path on packaged builds.
        * ``backend_fallback_qquickwidget`` (warn) — when QQuickWidget
          itself isn't constructible (rare; signals a broken Qt
          install). The viewport uses ``_FallbackQuickWidget`` as a
          stand-in but the user should know they aren't seeing the
          real QML output.

        The future GUI badge (S1.4) reads this tuple at the workbench
        level and renders the highest-severity colour chip.
        """

        return tuple(self._render_failure_codes)

    # ------------------------------------------------------------------
    # Phase G3 — QSG line layer plumbing
    # ------------------------------------------------------------------

    def _push_primitives_to_qsg(
        self,
        primitives: list[dict],
    ) -> None:
        """Forward scene_pack primitives to the GPU QSGLineItem.

        Quietly no-ops when the QSG path is disabled or the QML hasn't
        loaded yet — the Canvas fallback path will pick up the same
        primitives via its own QML property binding.
        """

        if self._skeleton_renderer != "qsg":
            return
        root = self._quick.rootObject() if self._quick else None
        if root is None:
            return
        try:
            from src.gui.qsg_line_item import QSGLineItem
            qsg_item = root.findChild(QSGLineItem, "qsgSkeleton")
            if qsg_item is None:
                # QML may not be ready yet, or the import failed silently.
                logger.debug("QSGLineItem 'qsgSkeleton' not found in QML tree")
                return
            qsg_item.setPrimitives(primitives or [])
            logger.debug(
                "QSG skeleton: pushed %d primitives → %d line segments",
                len(primitives or []), qsg_item.lineCount(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to push primitives to QSGLineItem")

    # ------------------------------------------------------------------
    # Scene pack loading
    # ------------------------------------------------------------------

    def load_scene_pack(
        self,
        pack_ref: Optional[ScenePackRef],
        *,
        empty_notice: str = "도면을 선택하면 빠르게 표시됩니다.",
    ) -> int:
        """Load primitives from a ``ScenePackRef`` and push to QML.

        Reads the ``overview_lod0.json`` first (skeleton subset, smaller +
        faster) — that gives the user instant first paint. G2.3 will
        layer the per-zone vector micro-pack on top when a zone is
        selected.

        Returns the number of primitives pushed.
        """

        root = self._quick.rootObject()
        if root is None:
            logger.warning("LightweightViewport: QML root not ready, deferring load")
            return 0

        if pack_ref is None or not pack_ref.overview_lod0_path:
            _clear_pdf_background_state(self, root)
            root.setProperty("primitives", [])
            root.setProperty("emptyNotice", empty_notice)
            self._loaded_pack_path = None
            self._primitive_count = 0
            # Phase G2.7-FU2 — leaving PDF mode; clear the auto-rerender
            # state so a stale wheel-zoom event doesn't try to re-render
            # a PDF that's no longer the active source.
            return 0

        overview_path = Path(pack_ref.overview_lod0_path)
        if not overview_path.exists():
            logger.warning("LightweightViewport: overview LOD0 missing at %s", overview_path)
            _clear_pdf_background_state(self, root)
            root.setProperty("primitives", [])
            self._loaded_pack_path = None
            self._primitive_count = 0
            root.setProperty("emptyNotice", "신형 뷰어 데이터가 아직 빌드되지 않았습니다.")
            return 0

        try:
            data = json.loads(overview_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("LightweightViewport: failed to read overview: %s", exc)
            _clear_pdf_background_state(self, root)
            root.setProperty("primitives", [])
            self._loaded_pack_path = None
            self._primitive_count = 0
            root.setProperty(
                "emptyNotice", f"신형 뷰어 데이터 로드 실패:\n{exc}"
            )
            return 0

        primitives = data.get("primitives") or []
        world_bbox = data.get("world_bbox") or [0.0, 0.0, 1.0, 1.0]
        try:
            self._world_bbox = (
                float(world_bbox[0]), float(world_bbox[1]),
                float(world_bbox[2]), float(world_bbox[3]),
            )
        except (TypeError, ValueError, IndexError):
            self._world_bbox = (0.0, 0.0, 1.0, 1.0)

        # Push to QML: world bbox first (triggers fitToView), then primitives.
        # Scene mode must clear any previous PDF/raster bitmap first.
        _clear_pdf_background_state(self, root)
        root.setProperty("worldBbox", list(self._world_bbox))
        root.setProperty("primitives", primitives)
        root.setProperty("emptyNotice", "")
        self._loaded_pack_path = str(overview_path)
        self._primitive_count = len(primitives)
        # Phase G3 — push to the GPU skeleton item too. The Canvas
        # fallback reads `primitives` directly, but QSGLineItem needs
        # an explicit setLines() call (it's not a QML binding source).
        self._push_primitives_to_qsg(primitives)

        logger.info(
            "LightweightViewport(%s): loaded %d skeleton primitives, "
            "world_bbox=(%.1f, %.1f, %.1f, %.1f)",
            self._side, len(primitives),
            self._world_bbox[0], self._world_bbox[1],
            self._world_bbox[2], self._world_bbox[3],
        )
        return len(primitives)

    def load_raster_image(
        self,
        image_path: Optional[Path],
        *,
        world_bbox: Optional[tuple[float, float, float, float]] = None,
        empty_notice: str = "Raster preview is not available.",
    ) -> bool:
        """Load an existing rendered PNG/JPEG as the lightweight background.

        Some CAD/DWG runs only produce the legacy raster preview plus world
        transforms, not a scene pack. This keeps the lightweight viewer as the
        single visible path while still showing those rendered drawings.
        """

        root = self._quick.rootObject()
        if root is None:
            logger.warning(
                "LightweightViewport: QML root not ready, deferring raster load"
            )
            return False

        def _clear_background(notice: str) -> None:
            _clear_pdf_background_state(self, root)
            root.setProperty("primitives", [])
            root.setProperty("emptyNotice", _readable_pdf_notice(notice))
            self._pdf_render_state = None

        if image_path is None or not Path(image_path).exists():
            _clear_background(empty_notice)
            return False

        path = Path(image_path)
        bbox = _normalise_bbox(world_bbox)
        if bbox is None:
            size = QImageReader(str(path)).size()
            if not size.isValid() or size.width() <= 0 or size.height() <= 0:
                _clear_background(empty_notice)
                return False
            bbox = (0.0, 0.0, float(size.width()), float(size.height()))

        self._world_bbox = bbox
        root.setProperty("worldBbox", list(bbox))
        root.setProperty("backgroundImageWorldBbox", list(bbox))
        root.setProperty("backgroundImageSource", "")
        root.setProperty("backgroundImageSource", QUrl.fromLocalFile(str(path.resolve())).toString())
        root.setProperty("primitives", [])
        root.setProperty("emptyNotice", "")
        self._loaded_pack_path = None
        self._primitive_count = 0
        self._pdf_render_state = None
        logger.info(
            "LightweightViewport(%s): loaded raster background %s "
            "world_bbox=(%.1f, %.1f, %.1f, %.1f)",
            self._side,
            path.name,
            bbox[0],
            bbox[1],
            bbox[2],
            bbox[3],
        )
        return True

    # ------------------------------------------------------------------
    # PDF page loading (Phase G2.7)
    # ------------------------------------------------------------------

    def load_pdf_page(
        self,
        pdf_path: Optional[Path],
        page_index: int = 0,
        *,
        target_dpi: float = 150.0,
        cache_dir: Optional[Path] = None,
        max_render_pixels: Optional[int] = None,
        empty_notice: str = "PDF 페이지를 선택하면 표시됩니다.",
    ) -> bool:
        """Phase G2.7 — Render a PDF page via Qt PDF and push it as the
        background image in the lightweight viewport.

        The rendered PNG is written to ``cache_dir`` (defaults to
        ``%TEMP%/tekla_mcp_qtpdf_cache``) and its file:// URL is set as
        ``backgroundImageSource`` on the QML root. The world bbox is
        derived from the PDF page's point dimensions so the same affine
        the overlay layers use lines up with the image.

        Returns True on success. On any failure (Qt PDF missing, bad
        file, render error) returns False and clears the background.
        """

        root = self._quick.rootObject()
        if root is None:
            logger.warning(
                "LightweightViewport: QML root not ready, deferring PDF load"
            )
            return False

        def _clear_background(notice: str) -> None:
            _clear_pdf_background_state(self, root)
            root.setProperty("primitives", [])
            root.setProperty("emptyNotice", _readable_pdf_notice(notice))

        if pdf_path is None or not Path(pdf_path).exists():
            _clear_background(empty_notice)
            return False
        source_path = Path(pdf_path)
        if cache_dir is None:
            cache_dir = _default_pdf_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            source_sig = _pdf_source_signature(source_path)
        except OSError as exc:
            logger.warning("load_pdf_page: could not stat source %s: %s", source_path, exc)
            _clear_background(f"PDF ?뚯씪 ?뺣낫瑜??쎌? 紐삵뻽?듬땲?? {source_path.name}")
            return False
        requested_dpi = max(10.0, float(target_dpi))
        pixel_budget = _normalise_pdf_pixel_budget(max_render_pixels)
        target_dpi = float(requested_dpi)
        dpi_capped = False

        from src.services.comparison.qt_pdf_adapter import (
            PdfPageRenderer,
            is_qt_pdf_available,
            prune_pdf_cache,
            select_initial_pdf_render_dpi,
        )

        def _push_cached_background(
            cached_path: Path,
            w_pts: float,
            h_pts: float,
            *,
            cache_hit: bool,
            pixel_size: tuple[int, int] | None = None,
            metadata_hit: bool = False,
        ) -> bool:
            world_bbox = (0.0, 0.0, float(w_pts), float(h_pts))
            self._world_bbox = world_bbox
            root.setProperty("worldBbox", list(world_bbox))
            root.setProperty("backgroundImageWorldBbox", list(world_bbox))
            image_url = QUrl.fromLocalFile(str(cached_path.resolve())).toString()
            root.setProperty("backgroundImageSource", "")
            root.setProperty("backgroundImageSource", image_url)
            root.setProperty("primitives", [])
            root.setProperty("emptyNotice", "")
            self._loaded_pack_path = None
            self._primitive_count = 0

            width, height = pixel_size or (0, 0)
            logger.info(
                "LightweightViewport(%s): loaded PDF page %d at %d DPI "
                "(%s, %dx%d px, %.1fx%.1f pts, cache=%s)",
                self._side, page_index, int(target_dpi),
                "cache-hit" if cache_hit else "rendered",
                width, height, w_pts, h_pts, cached_path.name,
            )
            try:
                base_upp_now = float(root.property("unitsPerPixel") or 0.0)
            except Exception:
                base_upp_now = 0.0
            existing = self._pdf_render_state or {}
            self._pdf_render_state = {
                "pdf_path": str(source_path),
                "page_index": int(page_index),
                "current_dpi": float(target_dpi),
                "requested_dpi": float(requested_dpi),
                "effective_dpi": float(target_dpi),
                "base_upp": existing.get("base_upp") or base_upp_now,
                "base_dpi": existing.get("base_dpi") or float(target_dpi),
                "cache_dir": str(cache_dir),
                "cache_hit": bool(cache_hit),
                "metadata_hit": bool(metadata_hit),
                "dpi_capped": bool(dpi_capped),
                "max_render_pixels": pixel_budget,
                "pending_dpi": None,
            }
            try:
                prune_pdf_cache(cache_dir, max_bytes=PDF_CACHE_MAX_BYTES)
            except Exception:
                logger.debug("prune_pdf_cache raised", exc_info=True)
            return True

        metadata = _read_pdf_cache_metadata(
            cache_dir,
            source_sig=source_sig,
            page_index=page_index,
            requested_dpi=requested_dpi,
            max_render_pixels=pixel_budget,
        )
        if metadata is not None:
            target_dpi = float(metadata.get("effective_dpi") or requested_dpi)
            dpi_capped = bool(metadata.get("dpi_capped"))
            return _push_cached_background(
                Path(metadata["cached_path"]),
                float(metadata.get("page_width_points") or 0.0),
                float(metadata.get("page_height_points") or 0.0),
                cache_hit=True,
                metadata_hit=True,
                pixel_size=(
                    int(metadata.get("pixel_width") or 0),
                    int(metadata.get("pixel_height") or 0),
                ),
            )

        if not is_qt_pdf_available():
            _clear_background(
                "Qt PDF 모듈이 없어 신형 뷰어로 PDF를 표시할 수 없습니다."
            )
            return False

        try:
            renderer = PdfPageRenderer(pdf_path)
            renderer._ensure_loaded()  # noqa: SLF001 — pre-flight to surface load failure
            if not renderer.is_loaded:
                _clear_background(
                    f"PDF 로드 실패: {Path(pdf_path).name}"
                )
                renderer.close()
                return False
            if page_index < 0 or page_index >= renderer.page_count():
                _clear_background(
                    f"페이지 인덱스 범위 초과: {page_index} (총 {renderer.page_count()}쪽)"
                )
                renderer.close()
                return False

            # World bbox in PDF points (1pt = 1/72 inch). Using points
            # rather than pixels keeps the overlay layer's world coords
            # unit-consistent with the diff pipeline (which also uses
            # PDF points for PDF inputs).
            w_pts, h_pts = renderer.page_size_points(page_index)
            if w_pts <= 0 or h_pts <= 0:
                _clear_background(
                    "PDF 페이지 크기를 읽지 못했습니다."
                )
                renderer.close()
                return False

            effective_dpi = select_initial_pdf_render_dpi(
                (float(w_pts), float(h_pts)),
                target_dpi=requested_dpi,
                max_pixels=pixel_budget,
            )
            dpi_capped = float(effective_dpi) < float(requested_dpi) - 0.01
            if dpi_capped:
                logger.info(
                    "LightweightViewport(%s): capped initial PDF render DPI "
                    "from %d to %d for page %.1fx%.1f pts (pixel_budget=%s)",
                    self._side,
                    int(requested_dpi),
                    int(effective_dpi),
                    w_pts,
                    h_pts,
                    pixel_budget,
                )
            target_dpi = float(effective_dpi)
            cached_png = _pdf_cache_png_path(
                cache_dir,
                source_sig=source_sig,
                page_index=page_index,
                effective_dpi=target_dpi,
            )

            # Audit-gates §12.3 A4 — re-check existence right before render
            try:
                if cached_png.exists() and cached_png.stat().st_size > 0:
                    _write_pdf_cache_metadata(
                        cache_dir,
                        source_sig=source_sig,
                        source_path=source_path,
                        page_index=page_index,
                        requested_dpi=requested_dpi,
                        effective_dpi=target_dpi,
                        max_render_pixels=pixel_budget,
                        page_size_points=(float(w_pts), float(h_pts)),
                        cached_png=cached_png,
                    )
                    renderer.close()
                    return _push_cached_background(
                        cached_png,
                        float(w_pts),
                        float(h_pts),
                        cache_hit=True,
                    )
            except OSError:
                logger.debug("PDF cache stat failed for %s", cached_png, exc_info=True)

            # to defuse the 7-second race observed in the 2026-05-15 Qt6Core
            # fast-fail crash. The user can move / delete / network-unmount
            # the source between the initial exists() (line 409) and this
            # final render_page() call. QPdfDocument is not robust to a
            # disappeared file mid-render and aborts via __fastfail
            # (0xc0000409 STATUS_STACK_BUFFER_OVERRUN).
            if not Path(pdf_path).exists():
                logger.warning(
                    "load_pdf_page: source disappeared between initial check "
                    "and render: %s", pdf_path,
                )
                _clear_background(
                    f"PDF 파일이 사라졌습니다: {Path(pdf_path).name}"
                )
                try:
                    renderer.close()
                except Exception:
                    pass
                return False
            img = renderer.render_page(page_index, target_dpi=target_dpi)
            renderer.close()
            if img is None or img.isNull():
                _clear_background("PDF 렌더 실패")
                return False

            # Write to a stable cache file so QML's Image can pick it up
            # via file://. Naming includes pdf hash + page + dpi so a
            # repeated render at the same DPI hits the file cache.
            try:
                # Save as PNG (lossless). Returns False on disk failure.
                if not _save_png_atomic(img, cached_png):
                    _clear_background("PDF 캐시 저장 실패")
                    return False
            except Exception as exc:  # noqa: BLE001
                logger.warning("PDF cache save failed for %s: %s", cached_png, exc)
                _clear_background(f"PDF 캐시 저장 오류: {exc}")
                return False
            _write_pdf_cache_metadata(
                cache_dir,
                source_sig=source_sig,
                source_path=source_path,
                page_index=page_index,
                requested_dpi=requested_dpi,
                effective_dpi=target_dpi,
                max_render_pixels=pixel_budget,
                page_size_points=(float(w_pts), float(h_pts)),
                cached_png=cached_png,
                pixel_size=(int(img.width()), int(img.height())),
            )

            return _push_cached_background(
                cached_png,
                float(w_pts),
                float(h_pts),
                cache_hit=False,
                pixel_size=(int(img.width()), int(img.height())),
            )

            # Push the URL + world bbox to QML.
            world_bbox = (0.0, 0.0, float(w_pts), float(h_pts))
            self._world_bbox = world_bbox
            root.setProperty("worldBbox", list(world_bbox))
            root.setProperty("backgroundImageWorldBbox", list(world_bbox))
            # QML Image needs a file:// URL on Windows; QUrl.fromLocalFile
            # gives us the right form regardless of platform.
            image_url = QUrl.fromLocalFile(str(cached_png.resolve())).toString()
            # Force QML Image to reload even when the same PDF/page/DPI cache
            # path is re-used after a new compare run. Without clearing first,
            # QML may keep a stale failed/blank Image status because the URL
            # string did not change.
            root.setProperty("backgroundImageSource", "")
            root.setProperty("backgroundImageSource", image_url)
            root.setProperty("primitives", [])  # PDF has no skeleton vector
            root.setProperty("emptyNotice", "")

            logger.info(
                "LightweightViewport(%s): loaded PDF page %d at %d DPI "
                "(%dx%d px, %.1f×%.1f pts, cache=%s)",
                self._side, page_index, int(target_dpi),
                img.width(), img.height(), w_pts, h_pts, cached_png.name,
            )

            # Phase G2.7-FU2 — remember this PDF so zoom-in can re-render
            # at higher DPI. The base_upp comes from the QML root after
            # fitToView runs (we read it lazily in _maybe_schedule…).
            try:
                base_upp_now = float(root.property("unitsPerPixel") or 0.0)
            except Exception:
                base_upp_now = 0.0
            existing = self._pdf_render_state or {}
            self._pdf_render_state = {
                "pdf_path": str(pdf_path),
                "page_index": int(page_index),
                "current_dpi": float(target_dpi),
                # Lock in the FIRST base_upp (the fit-to-view value);
                # subsequent zoom re-renders shouldn't reset the
                # reference point.
                "base_upp": existing.get("base_upp") or base_upp_now,
                "base_dpi": existing.get("base_dpi") or float(target_dpi),
                "cache_dir": str(cache_dir),
                "pending_dpi": None,
            }

            # Phase G2.7-FU3 — keep the on-disk PNG cache bounded.
            try:
                prune_pdf_cache(cache_dir, max_bytes=200 * 1024 * 1024)
            except Exception:  # noqa: BLE001 — never let cache hygiene break the load
                logger.debug("prune_pdf_cache raised", exc_info=True)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("LightweightViewport: load_pdf_page failed")
            _clear_background("PDF 로드 중 예외")
            return False

    # ------------------------------------------------------------------
    # Overlay model — same shape as Phase F's GpuDrawingViewport
    # ------------------------------------------------------------------

    def set_overlays(
        self,
        cloud: List[Dict[str, Any]],
        focus: List[Dict[str, Any]],
    ) -> None:
        """Push cloud + focus overlay lists to QML."""

        root = self._quick.rootObject()
        if root is None:
            return
        root.setProperty("overlaysCloud", list(cloud or []))
        root.setProperty("overlaysFocus", list(focus or []))

    def set_side_message(self, message: str = "") -> None:
        """Show a short side-specific explanation over the viewport."""

        root = self._quick.rootObject()
        if root is None:
            return
        root.setProperty("sideMessage", str(message or ""))

    def set_overlay_opacity_scale(self, scale: float) -> None:
        """Same clamp policy as the legacy viewport."""

        try:
            value = float(scale)
        except (TypeError, ValueError):
            value = 1.0
        clamped = max(0.3, min(1.0, value))
        self._overlay_opacity_scale = clamped
        root = self._quick.rootObject()
        if root is not None:
            root.setProperty("overlayOpacityScale", clamped)

    @property
    def overlay_opacity_scale(self) -> float:
        return self._overlay_opacity_scale

    # ------------------------------------------------------------------
    # Render mode (drives badge + watermark)
    # ------------------------------------------------------------------

    def set_fidelity_state(
        self,
        render_mode: RenderMode,
        status_text: str = "",
    ) -> None:
        """Push the 7-state RenderMode + an optional Korean status string.

        ``status_text`` appears as italic suffix on the badge (e.g.
        '12 개 변경 / 2.4 s'). Pass empty string to hide.
        """

        self._render_mode = render_mode if render_mode else "relative_only"
        self._apply_fidelity_to_qml(self._render_mode, status_text=status_text)

    def _apply_fidelity_to_qml(
        self,
        mode: RenderMode,
        *,
        status_text: str = "",
    ) -> None:
        root = self._quick.rootObject()
        if root is None:
            return
        style = style_for(mode)
        root.setProperty("renderMode", mode)
        root.setProperty("renderModeLabel", style.label_ko)
        root.setProperty("renderModeBadgeColor", style.badge_color)
        root.setProperty("showWatermark", bool(style.show_watermark))
        if status_text:
            root.setProperty("statusText", status_text)

    # ------------------------------------------------------------------
    # View control
    # ------------------------------------------------------------------

    def fit_to_view(self) -> None:
        """Recompute the world→pixel transform to fit the loaded drawing."""

        root = self._quick.rootObject()
        if root is None:
            return
        try:
            root.metaObject().invokeMethod(root, "fitToView")
        except Exception:
            # PySide6 invokeMethod arity varies; fall back to direct call
            try:
                root.fitToView()  # type: ignore[attr-defined]
            except Exception:
                logger.debug("LightweightViewport: fitToView call failed")

    # ------------------------------------------------------------------
    # Phase G2.3 — camera + zone focus + overlay adapter
    # ------------------------------------------------------------------

    def set_camera_to_world_bbox(
        self,
        bbox: tuple[float, float, float, float],
        *,
        padding_ratio: float = 0.25,
    ) -> None:
        """Centre the camera on ``bbox`` and zoom so the bbox + padding fits.

        Used when the user clicks a change zone in the list — the lightweight
        viewport pans/zooms to show that zone with context. Padding ratio of
        0.25 gives a 25 % margin around the bbox so the user sees nearby
        primitives, not a pixel-tight crop.
        """

        root = self._quick.rootObject()
        if root is None:
            return
        coords = _normalise_bbox(bbox)
        if coords is None:
            return
        x0, y0, x1, y1 = coords
        if x1 <= x0 or y1 <= y0:
            return
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        ww = max(1.0, (x1 - x0) * (1.0 + padding_ratio * 2.0))
        wh = max(1.0, (y1 - y0) * (1.0 + padding_ratio * 2.0))
        # Pixel size from QML root (live size, not the value at construction)
        try:
            avail_w = float(root.property("width") or self.width()) or 800.0
            avail_h = float(root.property("height") or self.height()) or 600.0
        except Exception:
            avail_w, avail_h = 800.0, 600.0
        upp = max(ww / avail_w, wh / avail_h)
        try:
            root.setProperty("cameraCenterX", cx)
            root.setProperty("cameraCenterY", cy)
            root.setProperty("unitsPerPixel", upp)
        except Exception:
            logger.exception("LightweightViewport: setProperty failed")
        # Phase G2.7-FU2 follow-up — a programmatic zone-focus zoom sets
        # unitsPerPixel directly (no QML wheel event), so it never reached
        # _on_qml_viewport_changed and the PDF was left at the base 150-DPI
        # render -> the auto-zoomed change looked blurry ("흐리게"). Trigger the
        # same debounced higher-DPI re-render here. No-op outside PDF mode.
        try:
            self._maybe_schedule_pdf_rerender(float(upp))
        except Exception:  # noqa: BLE001 — never let re-render scheduling break focus
            logger.debug("zone-focus PDF rerender schedule failed", exc_info=True)

    def _fit_units_per_pixel(self) -> float:
        """unitsPerPixel that fits the whole drawing — matches QML fitToView.

        QML ``fitToView`` uses ``max(ww/availW, wh/availH) * 1.05`` (5 %
        margin). Mirroring it here lets the zoom slider treat 100 % as the
        fit zoom so the slider and the wheel/fit button share one scale.
        """

        root = self._quick.rootObject()
        if root is None:
            return 0.0
        coords = _normalise_bbox(self._world_bbox)
        if coords is None:
            return 0.0
        x0, y0, x1, y1 = coords
        ww = max(1.0, float(x1) - float(x0))
        wh = max(1.0, float(y1) - float(y0))
        try:
            avail_w = float(root.property("width") or self.width() or 0.0)
            avail_h = float(root.property("height") or self.height() or 0.0)
        except (TypeError, ValueError):
            return 0.0
        if avail_w <= 0.0 or avail_h <= 0.0:
            return 0.0
        return max(ww / avail_w, wh / avail_h) * 1.05

    def apply_zoom_factor(self, factor: float) -> None:
        """Zoom the camera to ``factor`` × fit-to-view (1.0 == whole drawing).

        The zoom slider is absolute: 100 % is the fit-to-view zoom and higher
        values zoom in. The lightweight viewport's zoom IS its
        ``unitsPerPixel`` (smaller == more zoomed), so we anchor to the same
        fit formula QML's ``fitToView`` uses and divide by the factor. The
        camera centre is preserved so zooming doesn't also pan, and a
        higher-DPI PDF re-render is scheduled (no-op outside PDF mode).
        """

        root = self._quick.rootObject()
        if root is None:
            return
        fit_upp = self._fit_units_per_pixel()
        if fit_upp <= 0.0:
            return
        try:
            zoom = float(factor)
        except (TypeError, ValueError):
            return
        upp = max(0.0001, fit_upp / max(0.05, zoom))
        try:
            cx = float(root.property("cameraCenterX") or 0.0)
            cy = float(root.property("cameraCenterY") or 0.0)
        except (TypeError, ValueError):
            return
        self.set_camera(cx, cy, upp)
        try:
            self._maybe_schedule_pdf_rerender(upp)
        except Exception:  # noqa: BLE001 — never let re-render break zoom
            logger.debug("zoom-slider PDF rerender schedule failed", exc_info=True)

    def set_camera(
        self,
        center_x: float,
        center_y: float,
        units_per_pixel: float,
    ) -> None:
        """Apply an externally-computed camera state (used for sync between
        before/after viewports)."""

        root = self._quick.rootObject()
        if root is None:
            return
        try:
            root.setProperty("cameraCenterX", float(center_x))
            root.setProperty("cameraCenterY", float(center_y))
            root.setProperty("unitsPerPixel", max(0.0001, float(units_per_pixel)))
        except Exception:
            logger.exception("LightweightViewport: set_camera failed")

    def push_zone_focus_pack(self, focus_path: Path) -> int:
        """Phase G2.3 — Layer the zone-focus primitive pack on top of the
        skeleton overview already loaded.

        ``focus_path`` is the path to a ``zone_focus.json`` produced by
        ``zone_render_worker.render_zone_focus``. Reads the file, appends
        its primitives to the current ``primitives`` array on QML, and
        triggers a repaint. Returns the number of primitives appended.
        """

        path = Path(focus_path)
        if not path.exists() or path.stat().st_size <= 0:
            return 0
        root = self._quick.rootObject()
        if root is None:
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("LightweightViewport: zone focus read failed: %s", exc)
            return 0
        focus_prims = data.get("primitives") or []
        if not focus_prims:
            return 0
        current = list(root.property("primitives") or [])
        merged = current + list(focus_prims)
        root.setProperty("primitives", merged)
        # Set badge to vector_focus while focus pack is in view.
        self._apply_fidelity_to_qml(
            "vector_focus",
            status_text=f"focus +{len(focus_prims)}",
        )
        logger.info(
            "LightweightViewport(%s): added %d focus primitives",
            self._side, len(focus_prims),
        )
        return len(focus_prims)

    def push_change_overlays_from_v1(
        self,
        overlays: list[dict],
        *,
        side: str = "",
        focus_zone_id: str = "",
    ) -> None:
        """Adapter — convert v1 overlay records (CAD-world bbox) to the
        lightweight viewport's flat {x, y, w, h, color, label} model.

        The v1 overlay carries:
          * ``bbox`` — after-side world bbox. Format is **dict** in
            production (``{"min_x", "min_y", "max_x", "max_y"}``); some
            legacy fixtures use a 4-element list. Both supported.
          * ``old_bbox`` — before-side world bbox (optional, same shape).
          * ``zone_id``, ``change_type``, ``severity``

        We pick the bbox for the matching ``side`` and route to either
        ``overlaysCloud`` (default) or ``overlaysFocus`` (when zone matches
        ``focus_zone_id``).
        """

        cloud: list[dict] = []
        focus: list[dict] = []
        side = (side or self._side).lower()

        for ov in overlays or []:
            if not isinstance(ov, dict):
                continue
            zid = str(ov.get("zone_id") or "")
            bbox_key = "old_bbox" if side == "before" else "bbox"
            change_type = str(ov.get("change_type") or "")
            lowered_type = change_type.lower()
            match_side = ""
            if "delete" in lowered_type or "remove" in lowered_type:
                match_side = "a_only"
            elif "add" in lowered_type:
                match_side = "b_only"
            if side == "before" and match_side == "b_only":
                continue
            if side == "after" and match_side == "a_only":
                continue
            raw_bbox = ov.get(bbox_key)
            if raw_bbox is None and not match_side:
                raw_bbox = ov.get("bbox") or ov.get("old_bbox")
            # Phase G2.7-COORDFIX — convert PDF image_pixels → PDF points so
            # the marker lands on the correct spot of the page background.
            # DXF/DWG overlays pass through unchanged.
            coords = convert_bbox_to_world_space(
                raw_bbox,
                coordinate_space=str(ov.get("bbox_coordinate_space") or ""),
                pdf_dpi=float(ov.get("pdf_dpi") or 0.0),
                page_height_points=_page_height_points_from_world_bbox(self._world_bbox),
            )
            if coords is None:
                continue
            x0, y0, x1, y1 = coords
            if x1 <= x0 or y1 <= y0:
                continue

            color = "#DC2626"  # red default
            if change_type == "added":
                color = "#16A34A"  # green
            elif change_type == "deleted":
                color = "#DC2626"  # red
            elif change_type == "modified":
                color = "#F59E0B"  # amber
            elif change_type == "moved":
                color = "#2563EB"  # blue

            entry = {
                "x": x0, "y": y0,
                "w": x1 - x0, "h": y1 - y0,
                "color": color,
                "label": zid,
                "dimmed": bool(focus_zone_id and focus_zone_id != zid),
                "zoneId": zid,  # required for QML overlayClicked routing
            }

            if focus_zone_id and zid == focus_zone_id:
                focus.append(entry)
            else:
                cloud.append(entry)

        source_count = sum(1 for ov in overlays or [] if isinstance(ov, dict))
        if source_count > FOCUS_ONLY_CHANGE_OVERLAY_SOURCE_THRESHOLD:
            if cloud:
                logger.debug(
                    "LightweightViewport(%s): focus-only overlay mode for %d source overlays",
                    self._side,
                    source_count,
                )
            cloud = []
        elif len(cloud) > MAX_QML_CHANGE_CLOUD_OVERLAYS:
            logger.debug(
                "LightweightViewport(%s): capped QML cloud overlays %d -> %d",
                self._side,
                len(cloud),
                MAX_QML_CHANGE_CLOUD_OVERLAYS,
            )
            cloud = cloud[:MAX_QML_CHANGE_CLOUD_OVERLAYS]

        # Push to QML. The selected zone, when present, is carried separately
        # in ``focus`` and is never removed by the cloud cap above.
        self.set_overlays(cloud, focus)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def primitive_count(self) -> int:
        return self._primitive_count

    @property
    def render_mode(self) -> RenderMode:
        return self._render_mode

    @property
    def world_bbox(self) -> tuple[float, float, float, float]:
        return self._world_bbox

    def visible_world_rect(
        self,
        center_x: Optional[float] = None,
        center_y: Optional[float] = None,
        units_per_pixel: Optional[float] = None,
    ) -> Optional[tuple[float, float, float, float]]:
        """Return the currently visible QML camera rectangle in world units."""

        root = self._quick.rootObject()
        if root is None:
            return None
        try:
            cx = float(center_x if center_x is not None else root.property("cameraCenterX"))
            cy = float(center_y if center_y is not None else root.property("cameraCenterY"))
            upp = max(0.0001, float(units_per_pixel if units_per_pixel is not None else root.property("unitsPerPixel")))
            width = max(1.0, float(root.property("width") or self.width() or 1.0))
            height = max(1.0, float(root.property("height") or self.height() or 1.0))
        except (TypeError, ValueError):
            return None
        half_w = width * upp / 2.0
        half_h = height * upp / 2.0
        return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)

    # ------------------------------------------------------------------
    # Internal slot
    # ------------------------------------------------------------------

    def _on_qml_viewport_changed(
        self, center_x: float, center_y: float, upp: float,
    ) -> None:
        """QML reported a pan/zoom — re-emit so callers can sync the
        before/after viewports, and schedule a higher-DPI re-render
        when the user has zoomed into a PDF.
        """

        try:
            self.viewportChanged.emit(float(center_x), float(center_y), float(upp))
        except Exception:
            pass
        # Phase G2.7-FU2 — auto re-render PDF at higher DPI on zoom-in.
        # Cheap pure-Python check via select_pdf_render_dpi; only when it
        # returns a non-None value do we schedule the actual re-render
        # (debounced 400ms so the user can mid-zoom without thrashing).
        self._maybe_schedule_pdf_rerender(float(upp))

    def _maybe_schedule_pdf_rerender(self, current_upp: float) -> None:
        """Phase G2.7-FU2 — Schedule a debounced re-render at higher DPI
        when the user has zoomed into a PDF page enough to make the
        current render visibly soft.

        No-op when:
          - No PDF is currently loaded (DXF / scene-pack mode)
          - Zoom is below the threshold inside ``select_pdf_render_dpi``
          - Current DPI is already ≥ the target bucket

        The debounce uses ``QTimer.singleShot`` so rapid wheel zooms
        coalesce into a single render at the final DPI.
        """

        state = self._pdf_render_state
        if not state:
            return
        try:
            from src.services.comparison.qt_pdf_adapter import select_pdf_render_dpi
        except Exception:
            return

        target_dpi = select_pdf_render_dpi(
            base_upp=float(state.get("base_upp", 0)),
            current_upp=float(current_upp),
            base_dpi=float(state.get("base_dpi", 150)),
            current_dpi=float(state.get("current_dpi", 150)),
        )
        if target_dpi is None:
            return
        # Stash the pending target so the debounced fire reads the latest
        state["pending_dpi"] = float(target_dpi)
        try:
            from PySide6.QtCore import QTimer
        except Exception:
            return
        # Reset the timer (so consecutive zooms restart the 400ms wait)
        if self._pdf_rerender_timer is None:
            self._pdf_rerender_timer = QTimer(self)
            self._pdf_rerender_timer.setSingleShot(True)
            self._pdf_rerender_timer.timeout.connect(self._fire_pdf_rerender)
        self._pdf_rerender_timer.start(400)

    def _fire_pdf_rerender(self) -> None:
        """Phase G2.7-FU2 — Debounce timeout: do the actual re-render.

        Reads the latest ``pending_dpi`` from the stashed PDF state and
        re-invokes ``load_pdf_page`` with that DPI. The resulting cached
        PNG file is keyed by DPI so repeated zoom cycles hit the disk
        cache instantly.
        """

        state = self._pdf_render_state
        if not state:
            return
        target_dpi = state.get("pending_dpi")
        pdf_path = state.get("pdf_path")
        page_index = state.get("page_index", 0)
        cache_dir = state.get("cache_dir")
        if target_dpi is None or pdf_path is None:
            return
        # Clear pending first so a load failure doesn't leave a stale value
        state["pending_dpi"] = None
        logger.info(
            "Auto re-rendering PDF at DPI %d (was %d) on zoom-in",
            int(target_dpi), int(state.get("current_dpi", 150)),
        )
        # The load_pdf_page call updates self._pdf_render_state.current_dpi.
        # Cap the pixel budget so a deep zoom (e.g. 600 DPI) cannot produce an
        # oversized image the viewport can't display (which blanked the PDF
        # background). load_pdf_page lowers the effective DPI to fit the budget.
        try:
            self.load_pdf_page(
                Path(str(pdf_path)),
                page_index=int(page_index),
                target_dpi=float(target_dpi),
                cache_dir=Path(str(cache_dir)) if cache_dir else None,
                max_render_pixels=_PDF_RERENDER_MAX_PIXELS,
            )
        except Exception:  # noqa: BLE001
            logger.exception("PDF zoom-rerender failed")

    def _on_qml_overlay_clicked(self, zone_id: str) -> None:
        """Phase I4 — Re-emit QML overlay click as a Qt signal.

        Empty zone_id is silently ignored (defensive — QML may emit when
        the modelData lacks zoneId, e.g. legacy overlay payloads).
        """

        zid = str(zone_id or "").strip()
        if zid:
            try:
                self.overlayClicked.emit(zid)
            except Exception:
                pass


__all__ = ["LightweightDrawingViewport"]
