# -*- coding: utf-8 -*-
"""Phase G2.7 — Native Qt PDF rendering for the lightweight viewer.

Wraps ``PySide6.QtPdf.QPdfDocument`` so the lightweight viewport can
render PDF pages with Qt-native quality (no PyMuPDF dependency for the
display path; PyMuPDF still drives the *comparison* algorithms).

Why not just reuse the existing PyMuPDF-rendered PNGs?

The legacy ``GpuDrawingViewport`` displays a **fixed-DPI** raster PNG
of each PDF page. When the user zooms in past ~1.5×, the PNG visibly
blurs (Qt scales the bitmap with no extra pixels). With this adapter,
the lightweight viewport can re-render at a higher DPI on demand —
each request returns a fresh ``QImage`` sharp at the requested zoom
level — without re-running the comparison pipeline.

Public API:

    PdfPageRenderer(pdf_path) — open a PDF document once, render many
                                pages on demand. Caches the loaded
                                document handle; release with .close().
    render_page(page_index, target_dpi) -> QImage
        Render at the requested DPI. Returns an empty QImage on error.
    page_size_inches(page_index) -> (width_in, height_in)
        Returns the page dimensions in inches; useful for picking a
        DPI that fits the available pixel budget.

Module-level helper ``render_pdf_page_once(pdf_path, page_index, dpi)``
opens, renders, and closes in one call — convenient for ad-hoc use
when caching isn't needed.

Failure mode: on **any** error (bad file, missing page, render fail)
methods return an empty/zero-sized result rather than raising. Callers
should check ``.isNull()`` / size > 0 before using.

Thread safety: ``QPdfDocument`` is not thread-safe. Renderer instances
must be used from a single thread (typically the GUI thread).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple, Union

logger = logging.getLogger(__name__)


# Standard PDF DPI (72 dpi = 1 PostScript point per pixel).
PDF_BASE_DPI: float = 72.0

# Keep first-display PDF renders responsive on large sheets. A1 at 150 DPI is
# roughly 17.4M pixels per side render pair and can visibly stall the GUI; this
# cap lets callers downshift the initial render while preserving zoom re-render
# support for users who need more detail.
PDF_INITIAL_RENDER_MAX_PIXELS: int = 5_000_000


class PdfPageRenderer:
    """Lazy-init wrapper around ``QPdfDocument``.

    Holds the loaded document so successive ``render_page`` calls don't
    re-parse the PDF. Construction is cheap; the document load happens
    on the first ``render_page`` / ``page_count`` access.
    """

    def __init__(self, pdf_path: Union[str, Path]) -> None:
        self._pdf_path = Path(pdf_path)
        self._doc = None  # QPdfDocument | None
        self._load_attempted = False
        self._load_failed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> bool:
        """Open the document if not already done. Returns True on success."""

        if self._doc is not None:
            return True
        if self._load_attempted and self._load_failed:
            return False
        self._load_attempted = True
        if not self._pdf_path.exists():
            logger.warning("PDF not found: %s", self._pdf_path)
            self._load_failed = True
            return False
        try:
            from PySide6.QtPdf import QPdfDocument  # type: ignore
        except ImportError as exc:
            logger.warning("Qt PDF unavailable (PySide6.QtPdf): %s", exc)
            self._load_failed = True
            return False

        doc = QPdfDocument()
        # ``load`` returns a status enum — non-zero means error.
        try:
            status = doc.load(str(self._pdf_path))
        except Exception as exc:  # noqa: BLE001 — defensive against backend variance
            logger.warning("QPdfDocument.load raised for %s: %s", self._pdf_path, exc)
            self._load_failed = True
            return False
        # PySide6 returns ``QPdfDocument.Error.None_`` (.value == 0) for
        # success. The enum doesn't support ``int(status)`` directly so
        # we read ``.value`` first; older PySide6 builds (or numeric
        # returns) fall through to ``int()`` for compatibility.
        err_value = None
        for getter in (lambda s: s.value, int):
            try:
                err_value = getter(status)
                break
            except (TypeError, AttributeError, ValueError):
                continue
        if err_value is None:
            # Last-resort: name-based check ("None_" / "None" → success)
            err_value = 0 if str(status).rsplit(".", 1)[-1] in ("None_", "None") else 1
        if err_value != 0:
            logger.warning(
                "QPdfDocument failed to load %s (status=%s)",
                self._pdf_path, status,
            )
            self._load_failed = True
            return False
        self._doc = doc
        return True

    def close(self) -> None:
        """Release the underlying document handle."""

        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:  # noqa: BLE001
                pass
            self._doc = None

    def __enter__(self) -> "PdfPageRenderer":
        self._ensure_loaded()
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._doc is not None

    @property
    def is_available(self) -> bool:
        """True when the PDF can be queried (loaded successfully)."""

        return self._ensure_loaded()

    def page_count(self) -> int:
        """Number of pages, or 0 if the document failed to load."""

        if not self._ensure_loaded():
            return 0
        try:
            return int(self._doc.pageCount())
        except Exception:  # noqa: BLE001
            return 0

    def page_size_points(self, page_index: int) -> Tuple[float, float]:
        """Return (width, height) in PDF points (1 point = 1/72 inch).

        Returns ``(0.0, 0.0)`` on failure or out-of-range index.
        """

        if not self._ensure_loaded():
            return (0.0, 0.0)
        if page_index < 0 or page_index >= self.page_count():
            return (0.0, 0.0)
        try:
            size = self._doc.pagePointSize(page_index)
            # QSizeF — width()/height() return floats
            return (float(size.width()), float(size.height()))
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)

    def page_size_inches(self, page_index: int) -> Tuple[float, float]:
        """Return (width, height) in inches."""

        w_pts, h_pts = self.page_size_points(page_index)
        return (w_pts / PDF_BASE_DPI, h_pts / PDF_BASE_DPI)

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render_page(
        self,
        page_index: int,
        target_dpi: float = 150.0,
    ):
        """Render a single page at the requested DPI.

        Returns a ``QImage`` (sharp at the chosen DPI). Returns an empty
        ``QImage`` (``isNull() == True``) on any error so callers can
        check without try/except.

        The output size in pixels is::

            width  = round(page_width_in_inches  * target_dpi)
            height = round(page_height_in_inches * target_dpi)

        The pixel size is clamped to a sane upper bound (8K per side)
        so a misconfigured DPI doesn't try to allocate gigabytes.
        """

        try:
            from PySide6.QtCore import QSize, QThread
            from PySide6.QtGui import QImage
        except ImportError:
            return None  # caller checks for None / isNull()

        # Audit-gates §12.3 A5 — QPdfDocument is NOT thread-safe. The
        # 2026-05-15 Qt6Core 0xc0000409 fast-fail likely involved a worker
        # thread touching this document while the GUI thread rendered.
        # Emit a warning when render_page() is invoked off the GUI thread
        # so the offending caller is caught in code review / log triage.
        # We log instead of raise so an in-flight render does not get
        # killed if the heuristic produces a false positive.
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                gui_thread = app.thread()
                current = QThread.currentThread()
                if current is not None and gui_thread is not None and current is not gui_thread:
                    import logging
                    logging.getLogger(__name__).warning(
                        "PdfPageRenderer.render_page() called off the GUI "
                        "thread (current=%r gui=%r). QPdfDocument is not "
                        "thread-safe; move the call to the GUI thread to "
                        "avoid Qt6Core fast-fail crashes.",
                        current, gui_thread,
                    )
        except Exception:
            # Thread-affinity check is best-effort diagnostics — never let
            # it raise from inside render_page().
            pass

        empty = QImage()
        if not self._ensure_loaded():
            return empty
        if page_index < 0 or page_index >= self.page_count():
            return empty
        target_dpi = max(10.0, float(target_dpi))

        w_in, h_in = self.page_size_inches(page_index)
        if w_in <= 0 or h_in <= 0:
            return empty

        target_w = int(round(w_in * target_dpi))
        target_h = int(round(h_in * target_dpi))
        # Cap at 8K per side to keep allocation bounded
        SAFETY_PIXEL_CAP = 8192
        if target_w > SAFETY_PIXEL_CAP or target_h > SAFETY_PIXEL_CAP:
            scale = SAFETY_PIXEL_CAP / max(target_w, target_h)
            target_w = max(1, int(target_w * scale))
            target_h = max(1, int(target_h * scale))
            logger.debug(
                "PDF render size capped at %dx%d for page %d (%s)",
                target_w, target_h, page_index, self._pdf_path.name,
            )

        try:
            img = self._doc.render(
                page_index, QSize(target_w, target_h),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "QPdfDocument.render failed for page %d of %s: %s",
                page_index, self._pdf_path, exc,
            )
            return empty
        if img is None:
            return empty
        return img


# ---------------------------------------------------------------------------
# Zoom-aware DPI selection (Phase G2.7 follow-up)
# ---------------------------------------------------------------------------


# Snap target DPIs to these buckets so zoom drift doesn't trigger
# constant re-renders. Each step is roughly 1.5x the previous so the
# sharper render is visibly distinct from the prior one.
PDF_DPI_BUCKETS: tuple[int, ...] = (75, 100, 150, 225, 300, 450, 600, 900, 1200)

# Default oversample factor — render at 1.5× the displayed size to give
# Qt headroom when bilinear-downsampling for the actual pixel grid.
PDF_RENDER_OVERSAMPLE: float = 1.5


def select_initial_pdf_render_dpi(
    page_size_points: Tuple[float, float],
    *,
    target_dpi: float = 150.0,
    max_pixels: Optional[int] = PDF_INITIAL_RENDER_MAX_PIXELS,
    min_dpi: float = 75.0,
) -> float:
    """Choose a first-display DPI that fits a page-level pixel budget.

    This is intentionally conservative and pure-Python: the caller can inspect
    PDF page dimensions before calling ``QPdfDocument.render()`` and avoid
    blocking the GUI thread with an oversized initial bitmap. Returned values
    are snapped to the existing DPI buckets so cache keys remain stable.
    """

    try:
        width_pts = float(page_size_points[0])
        height_pts = float(page_size_points[1])
        requested = max(10.0, float(target_dpi))
    except (TypeError, ValueError, IndexError):
        return 150.0
    if width_pts <= 0 or height_pts <= 0:
        return requested
    if max_pixels is None or int(max_pixels) <= 0:
        return requested

    page_area_in = (width_pts / PDF_BASE_DPI) * (height_pts / PDF_BASE_DPI)
    if page_area_in <= 0:
        return requested
    requested_pixels = page_area_in * requested * requested
    if requested_pixels <= float(max_pixels):
        return requested

    import math

    budgeted_dpi = math.sqrt(float(max_pixels) / page_area_in)
    budgeted_dpi = max(float(min_dpi), min(requested, budgeted_dpi))
    candidates = [
        float(bucket)
        for bucket in PDF_DPI_BUCKETS
        if float(bucket) <= requested and float(bucket) <= budgeted_dpi
    ]
    if candidates:
        return max(candidates)
    return min(requested, max(float(min_dpi), budgeted_dpi))


def select_pdf_render_dpi(
    *,
    base_upp: float,
    current_upp: float,
    base_dpi: float = 150.0,
    current_dpi: Optional[float] = None,
    max_dpi: float = 600.0,
    min_dpi: float = 75.0,
    oversample: float = PDF_RENDER_OVERSAMPLE,
) -> Optional[float]:
    """Pick a DPI to (re-)render a PDF page at given the current zoom.

    Returns the **next** DPI to render at (snapped to a bucket so we
    don't thrash on tiny zoom drift), or ``None`` when the existing
    render is already adequate (no change needed).

    Inputs:
        base_upp     — units-per-pixel at the initial fit-to-view (when
                       the page first appeared). Used as the zoom
                       reference point.
        current_upp  — units-per-pixel right now (smaller = zoomed in).
        base_dpi     — DPI used for the initial render (defaults 150).
        current_dpi  — DPI currently in use; if None, treat as base_dpi.
        max_dpi      — hard cap so bad input or extreme zoom can't blow
                       up memory (default 600 ≈ A4 at 5K wide).
        min_dpi      — never go below this (75 = base PDF point grid).
        oversample   — render at ``oversample × zoom × base_dpi`` so the
                       image has headroom for crisp downscale.

    Returns ``None`` when:
        - inputs are invalid (any non-positive value)
        - the bucketed target equals the current DPI (no work needed)
        - the bucketed target is *lower* than the current DPI (zoom-out
          shouldn't down-rez — keep the higher-quality render in place)

    Pure Python; no Qt dependency. Designed so the GUI can call it on
    every viewportChanged event without overhead.
    """

    if base_upp <= 0 or current_upp <= 0 or base_dpi <= 0:
        return None
    cur_dpi = float(current_dpi if current_dpi is not None else base_dpi)
    if cur_dpi <= 0:
        cur_dpi = base_dpi

    zoom = base_upp / current_upp  # >1 when zoomed in
    # Only re-render when the user has zoomed in **meaningfully**. 1.3×
    # is the threshold below which the human eye can't reliably see the
    # difference between a freshly-rendered higher-DPI image and the
    # bilinearly-upscaled base render (empirical — Qt's smooth scaling
    # is good enough up to ~1.3×). This also absorbs micro-scroll noise
    # from QML camera sync so viewportChanged doesn't trigger a render
    # storm during normal pan + tiny wheel adjustments.
    if zoom <= 1.3:
        return None
    target_raw = float(base_dpi) * float(zoom) * float(oversample)
    target_clamped = max(min_dpi, min(max_dpi, target_raw))

    # Snap to the nearest bucket so small zoom changes don't trigger
    # re-renders. Pick the smallest bucket >= target so we always have
    # at least the requested resolution.
    chosen = float(min_dpi)
    for bucket in PDF_DPI_BUCKETS:
        if bucket >= target_clamped:
            chosen = float(bucket)
            break
    else:
        chosen = float(PDF_DPI_BUCKETS[-1])
    chosen = min(chosen, float(max_dpi))

    if chosen <= cur_dpi:
        # Already at or above the needed DPI — keep the existing render.
        return None
    return chosen


# ---------------------------------------------------------------------------
# Cache cleanup (Phase G2.7 follow-up)
# ---------------------------------------------------------------------------


def prune_pdf_cache(
    cache_dir: Union[str, Path],
    *,
    max_bytes: int = 200 * 1024 * 1024,  # 200 MB default
    glob_pattern: str = "qtpdf_*.png",
) -> int:
    """Evict oldest cache files until directory total size ≤ ``max_bytes``.

    Returns the number of files deleted. Intended to be called after a
    fresh render writes a new PNG so cache size stays bounded over a
    long session.

    LRU is approximated by the file's last-access mtime (filesystems
    that disable atime fall back to mtime; both work for our case).
    Failure to delete a file (locked / permission) is logged and
    skipped — never raises.
    """

    cache_path = Path(cache_dir)
    if not cache_path.exists():
        return 0
    files: list[tuple[float, int, Path]] = []
    try:
        for f in cache_path.glob(glob_pattern):
            try:
                stat = f.stat()
            except OSError:
                continue
            files.append((stat.st_mtime, stat.st_size, f))
    except OSError as exc:
        logger.warning("prune_pdf_cache: could not list %s: %s", cache_path, exc)
        return 0

    total = sum(size for _, size, _ in files)
    if total <= max_bytes:
        return 0

    # Oldest first
    files.sort(key=lambda t: t[0])
    deleted = 0
    for mtime, size, path in files:
        if total <= max_bytes:
            break
        try:
            path.unlink()
            total -= size
            deleted += 1
        except OSError as exc:
            logger.debug("prune_pdf_cache: skip %s: %s", path, exc)
            continue
    if deleted > 0:
        logger.info(
            "PDF cache pruned: deleted %d file(s), %d bytes remaining (cap=%d)",
            deleted, total, max_bytes,
        )
    return deleted


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def render_pdf_page_once(
    pdf_path: Union[str, Path],
    page_index: int,
    *,
    target_dpi: float = 150.0,
):
    """One-shot helper: open, render, close.

    Returns the rendered ``QImage`` (or empty image on failure). Use
    ``PdfPageRenderer`` directly when rendering multiple pages from the
    same document — that path keeps the doc handle warm.
    """

    with PdfPageRenderer(pdf_path) as renderer:
        return renderer.render_page(page_index, target_dpi=target_dpi)


def is_qt_pdf_available() -> bool:
    """Probe whether QPdfDocument is importable in the current runtime.

    Returns False on bare PySide6 installs that omit the QtPdf module
    (some Linux distros), so callers can fall back to the legacy
    PyMuPDF-driven raster path without crashing on import.
    """

    try:
        from PySide6.QtPdf import QPdfDocument  # noqa: F401
    except ImportError:
        return False
    return True


__all__ = [
    "PDF_BASE_DPI",
    "PDF_DPI_BUCKETS",
    "PDF_INITIAL_RENDER_MAX_PIXELS",
    "PDF_RENDER_OVERSAMPLE",
    "PdfPageRenderer",
    "render_pdf_page_once",
    "is_qt_pdf_available",
    "select_initial_pdf_render_dpi",
    "select_pdf_render_dpi",
    "prune_pdf_cache",
]
