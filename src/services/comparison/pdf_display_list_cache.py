"""Per-page PyMuPDF DisplayList cache for selected-zone PDF crops.

Plan §17 Phase B-1b — GPT Pro F3 (HIGH) follow-up. The legacy
``_render_pdf_image_crop`` path in ``zone_render_service.py`` opens the
full pre-rendered PNG via PIL on every zone, even though typical zones
cover < 10% of the page. PyMuPDF's
``page.get_pixmap(clip=...)`` reads only the requested clip region from
a cached ``fitz.DisplayList``, which avoids the full-page bitmap cost
and reuses the per-page parse across consecutive zones on the same
page.

Cache key
---------
``(resolved_path, mtime_ns, size, page_index)``. Including the file
signature (mtime + size) means that re-saving the source PDF
automatically invalidates the cache without callers having to remember
to call :func:`_clear_cache`.

Thread-safety
-------------
**This module is intentionally single-threaded.** PyMuPDF is not
thread-safe; even reading from a ``fitz.Document`` across threads can
corrupt the parsed state. The selected-zone render path runs inside
the single-threaded subprocess at ``scripts/zone_render_process.py``,
so an in-process module-level cache is safe there.

To catch accidental multi-thread misuse early, the public helpers
assert they are being called from the main thread of the calling
process. The check is cheap (one ``threading.current_thread()`` call)
and would otherwise have to be debugged through hard-to-reproduce
corruption symptoms.

Capacity
--------
The cache reuses the same env-var override as the existing
``DrawingRenderIndex`` cache so operators only have to tune one knob.
See ``zone_render_service._resolve_max_cache_entries()`` for the
policy.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache state — module-level, single-threaded by contract.
# ---------------------------------------------------------------------------

# Cache key: (resolved_path_str, mtime_ns, size, page_index). Value: a
# (display_list, page_rect_width, page_rect_height) tuple. The page
# rect is captured at build time so the clip-mapping math does not need
# to re-open the document.
_DISPLAY_LIST_CACHE: dict[Tuple[str, int, int, int], Tuple[Any, float, float]] = {}
_DISPLAY_LIST_CACHE_ORDER: list[Tuple[str, int, int, int]] = []


def _assert_main_thread() -> None:
    """Raise a clear error if called from a non-MainThread context.

    PyMuPDF is not thread-safe (see module docstring); accidental
    multi-thread use corrupts the parsed state in ways that surface as
    silent rendering failures hours later.
    """
    current = threading.current_thread()
    if current is not threading.main_thread():
        raise RuntimeError(
            "pdf_display_list_cache is single-threaded by contract "
            "(PyMuPDF is not thread-safe); "
            f"called from thread {current.name!r}"
        )


def _resolve_cache_capacity() -> int:
    """Reuse the policy used by ``DrawingRenderIndex`` (Plan §15 A-3).

    Imported lazily to avoid a circular import with
    ``zone_render_service`` — both modules live in the same package.
    """
    try:
        from .zone_render_service import _resolve_max_cache_entries

        return _resolve_max_cache_entries()
    except Exception:
        # Fallback to the original hardcoded default if the helper is
        # ever moved or removed. Mirrors the legacy
        # ``_MAX_INDEX_CACHE_ENTRIES = 4`` behaviour.
        return 4


def _pdf_open_error_types(fitz_module: Any) -> Tuple[type[BaseException], ...]:
    """Return PyMuPDF document-open failures that should trigger fallback."""

    errors: list[type[BaseException]] = [PermissionError]
    for name in (
        "FileDataError",
        "FileNotFoundError",
        "EmptyFileError",
        "FileError",
        "ParsingError",
    ):
        candidate = getattr(fitz_module, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            errors.append(candidate)
    return tuple(dict.fromkeys(errors))


def _evict_to_capacity(capacity: int) -> None:
    """LRU eviction — drop oldest entries until at most ``capacity`` remain.

    The DisplayList cache uses pure LRU (no cost-weighted retention)
    because every DisplayList is cheap-to-rebuild relative to a full
    DXF parse; the policy mismatch from the DrawingRenderIndex cache
    is intentional. If profiling shows DisplayList rebuilds are
    actually expensive, switch to ``_evict_to_capacity`` from
    ``zone_render_service``.
    """
    while len(_DISPLAY_LIST_CACHE_ORDER) > capacity:
        oldest = _DISPLAY_LIST_CACHE_ORDER.pop(0)
        _DISPLAY_LIST_CACHE.pop(oldest, None)


def _file_signature(path: Path) -> Tuple[str, int, int]:
    """Return ``(resolved_path_str, mtime_ns, size)`` for cache invalidation.

    Mirrors ``zone_render_service.file_signature`` but returns a tuple
    instead of a dict so it can be hashed directly into the cache key.
    """
    resolved = Path(path).resolve()
    try:
        stat = resolved.stat()
        return (str(resolved), stat.st_mtime_ns, stat.st_size)
    except OSError:
        # Best-effort: if stat fails the cache still "works" but every
        # call invalidates because the signature is unstable.
        return (str(resolved), 0, 0)


def get_display_list(pdf_path: Path, page_index: int) -> Any:
    """Return a cached ``fitz.DisplayList`` for ``(pdf_path, page_index)``.

    Parameters
    ----------
    pdf_path:
        The source PDF on disk. Must exist; raises ``FileNotFoundError``
        otherwise.
    page_index:
        Zero-based page index. Out-of-range indices raise
        ``IndexError`` rather than silently clamping — the legacy
        ``_render_pdf_to_png`` clamps to 0 with a warning, but the
        DisplayList cache must surface the mismatch so the caller can
        decide what to fall back to.

    Returns
    -------
    The cached ``fitz.DisplayList``. The page's rect (width, height in
    PDF points) is *not* returned here; use :func:`get_page_rect` for
    that. Splitting the two avoids re-rendering when the caller only
    needs the rect for clip-mapping.

    Raises
    ------
    ImportError
        PyMuPDF is not installed. The caller is expected to fall back
        to the legacy PIL path.
    FileNotFoundError
        ``pdf_path`` does not exist.
    IndexError
        ``page_index`` is out of range for this document.
    RuntimeError
        Called from a non-main thread.
    """
    _assert_main_thread()
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "PyMuPDF (fitz) is required for the DisplayList cache; "
            "fall back to the PIL crop path when unavailable"
        ) from exc

    resolved = Path(pdf_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"PDF source not found: {resolved}")

    page_index = int(page_index)
    sig_path, sig_mtime, sig_size = _file_signature(pdf_path)
    cache_key = (sig_path, sig_mtime, sig_size, page_index)

    cached = _DISPLAY_LIST_CACHE.get(cache_key)
    if cached is not None:
        # Move to MRU end.
        if cache_key in _DISPLAY_LIST_CACHE_ORDER:
            _DISPLAY_LIST_CACHE_ORDER.remove(cache_key)
        _DISPLAY_LIST_CACHE_ORDER.append(cache_key)
        return cached[0]

    # Cache miss — open the document, fetch the page, build the
    # DisplayList. Per PyMuPDF docs (1.18+), the DisplayList keeps a
    # strong reference to the underlying Page, which in turn keeps the
    # Document alive — so we do not need to keep our own doc handle on
    # the success path.
    try:
        doc = fitz.open(str(resolved))
    except _pdf_open_error_types(fitz) as exc:
        raise ValueError(
            f"PDF could not be opened by PyMuPDF: {resolved}: {exc}"
        ) from exc
    page_count_snapshot = len(doc)
    try:
        if page_count_snapshot == 0:
            raise IndexError(f"PDF has no pages: {resolved}")
        if page_index < 0 or page_index >= page_count_snapshot:
            raise IndexError(
                f"page_index {page_index} out of range [0, {page_count_snapshot}) "
                f"for {resolved}"
            )
        page = doc[page_index]
        display_list = page.get_displaylist()
        page_rect = page.rect
        rect_w = float(page_rect.width)
        rect_h = float(page_rect.height)
    except Exception:
        # On any failure during build, close the document so we don't
        # leak file descriptors. The DisplayList path takes over the
        # document reference only on the success path.
        try:
            doc.close()
        except Exception:
            pass
        raise

    _DISPLAY_LIST_CACHE[cache_key] = (display_list, rect_w, rect_h)
    _DISPLAY_LIST_CACHE_ORDER.append(cache_key)
    _evict_to_capacity(_resolve_cache_capacity())
    return display_list


def get_page_rect(pdf_path: Path, page_index: int) -> Tuple[float, float]:
    """Return ``(width, height)`` of the cached page rect in PDF points.

    Triggers a cache build if the entry is not yet present so callers
    can ask for the rect before rendering and not pay a separate
    open-document cost. The corresponding DisplayList stays in cache.
    """
    _assert_main_thread()
    # Force-populate by calling get_display_list, then read the rect
    # from the cache. The lookup is O(1).
    get_display_list(pdf_path, page_index)
    sig_path, sig_mtime, sig_size = _file_signature(pdf_path)
    cache_key = (sig_path, sig_mtime, sig_size, int(page_index))
    entry = _DISPLAY_LIST_CACHE.get(cache_key)
    if entry is None:
        # Race-free in single-threaded use; this branch indicates the
        # cache was evicted by a parallel call (shouldn't happen by
        # contract). Fall back to a fresh open.
        import fitz  # type: ignore[import-not-found]

        doc = fitz.open(str(Path(pdf_path).resolve()))
        try:
            page = doc[int(page_index)]
            return float(page.rect.width), float(page.rect.height)
        finally:
            doc.close()
    return entry[1], entry[2]


def render_clip_to_png(
    display_list: Any,
    clip: Tuple[float, float, float, float],
    output_path: Path,
    *,
    scale: float = 2.0,
) -> dict[str, Any]:
    """Render a clip region of ``display_list`` to a PNG at ``output_path``.

    Parameters
    ----------
    display_list:
        A ``fitz.DisplayList`` returned by :func:`get_display_list`.
    clip:
        ``(left, top, right, bottom)`` rectangle in PDF page-space points.
        Caller is responsible for clamping to the page rect.
    output_path:
        Destination PNG path. Parent directory is created if missing.
    scale:
        Render scale. ``2.0`` ≈ 144 DPI (PDF defaults to 72 DPI), which
        matches the existing PIL crop's resolution for typical
        2-page-wide PDFs. Override only when the caller knows the
        target resolution.

    Returns
    -------
    Telemetry dict ``{"wall_ms": float, "output_bytes": int, "scale":
    float, "clip": tuple}``. Useful for the benchmark harness without
    requiring it to re-stat the file.
    """
    _assert_main_thread()
    import time

    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "PyMuPDF (fitz) is required to render a clip"
        ) from exc

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    left, top, right, bottom = (float(v) for v in clip)
    if right <= left or bottom <= top:
        raise ValueError(
            f"clip rectangle is empty or inverted: {clip}"
        )

    matrix = fitz.Matrix(float(scale), float(scale))
    clip_rect = fitz.Rect(left, top, right, bottom)

    started = time.perf_counter()
    # ``DisplayList.get_pixmap`` accepts ``matrix`` + ``clip``
    # identically to ``Page.get_pixmap``; both render via the same
    # low-level Fitz primitive. Using the DisplayList skips the
    # per-call page-parse cost.
    pixmap = display_list.get_pixmap(matrix=matrix, clip=clip_rect, alpha=False)
    pixmap.save(str(output_path))
    wall_ms = (time.perf_counter() - started) * 1000.0

    try:
        out_bytes = output_path.stat().st_size
    except OSError:
        out_bytes = 0

    return {
        "wall_ms": round(wall_ms, 3),
        "output_bytes": int(out_bytes),
        "scale": float(scale),
        "clip": (left, top, right, bottom),
    }


def cache_stats() -> dict[str, int]:
    """Return diagnostic counts for the in-process cache."""
    return {
        "entries": len(_DISPLAY_LIST_CACHE),
        "capacity": _resolve_cache_capacity(),
    }


def _clear_cache() -> None:
    """Test hook — wipe the in-process cache so each test starts clean.

    Intentionally underscore-prefixed; production callers should never
    need this because the file-signature cache key invalidates entries
    automatically on source mtime change.
    """
    _DISPLAY_LIST_CACHE.clear()
    _DISPLAY_LIST_CACHE_ORDER.clear()


__all__ = [
    "get_display_list",
    "get_page_rect",
    "render_clip_to_png",
    "cache_stats",
]
