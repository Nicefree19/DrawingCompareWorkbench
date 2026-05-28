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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Tuple

from .cache_budget import process_rss_mb, resolve_cache_byte_limit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache state — module-level, single-threaded by contract.
# ---------------------------------------------------------------------------

_CacheKey = Tuple[str, int, int, int]


@dataclass
class _DisplayListCacheEntry:
    display_list: Any
    rect_w: float
    rect_h: float
    estimated_bytes: int
    created_seq: int
    last_access_seq: int


# Cache key: (resolved_path_str, mtime_ns, size, page_index). Value:
# cached DisplayList plus the page rect and a conservative memory
# estimate. The page rect is captured at build time so the clip-mapping
# math does not need to re-open the document.
_DISPLAY_LIST_CACHE: dict[_CacheKey, _DisplayListCacheEntry] = {}
_DISPLAY_LIST_CACHE_ORDER: list[_CacheKey] = []
_DISPLAY_LIST_CACHE_TOTAL_ESTIMATED_BYTES = 0
_DISPLAY_LIST_HIT_COUNT = 0
_DISPLAY_LIST_MISS_COUNT = 0
_DISPLAY_LIST_EVICTION_COUNT = 0
_DISPLAY_LIST_EVICTED_ESTIMATED_BYTES = 0
_DISPLAY_LIST_LAST_EVICTION_REASON = ""
_DISPLAY_LIST_ACCESS_SEQ = 0

_DISPLAY_LIST_CACHE_MB_ENV_VAR = "DRAWING_COMPARE_DISPLAY_LIST_CACHE_MB"
_DEFAULT_DISPLAY_LIST_CACHE_MB = 256
_MIN_DISPLAY_LIST_ENTRY_BYTES = 64 * 1024


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


def _resolve_cache_byte_limit() -> int:
    """Return the DisplayList cache byte budget.

    ``DRAWING_COMPARE_DISPLAY_LIST_CACHE_MB`` is the specific knob for
    this cache. ``DRAWING_COMPARE_RENDER_CACHE_MB`` is accepted as a
    broader future-proof budget for render caches. If neither is set,
    default to a bounded but generous 256 MiB so existing installations
    keep their previous behaviour while gaining eviction telemetry.
    """
    return resolve_cache_byte_limit(
        specific_env_var=_DISPLAY_LIST_CACHE_MB_ENV_VAR,
        default_mb=_DEFAULT_DISPLAY_LIST_CACHE_MB,
    )


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


def _next_access_seq() -> int:
    global _DISPLAY_LIST_ACCESS_SEQ
    _DISPLAY_LIST_ACCESS_SEQ += 1
    return _DISPLAY_LIST_ACCESS_SEQ


def _estimate_display_list_bytes(rect_w: float, rect_h: float, source_size: int) -> int:
    """Return a conservative RSS proxy for one cached DisplayList.

    PyMuPDF does not expose the native DisplayList allocation size, so
    use the larger of page-area bytes and source-file bytes. This is
    intentionally conservative; the budget's job is to prevent runaway
    retention, not to perform exact accounting.
    """
    try:
        area_bytes = int(max(0.0, float(rect_w)) * max(0.0, float(rect_h)) * 4.0)
    except (TypeError, ValueError, OverflowError):
        area_bytes = 0
    try:
        file_bytes = max(0, int(source_size))
    except (TypeError, ValueError, OverflowError):
        file_bytes = 0
    file_bytes = min(file_bytes, 64 * 1024 * 1024)
    return max(_MIN_DISPLAY_LIST_ENTRY_BYTES, area_bytes, file_bytes)


def _process_rss_mb() -> float:
    return process_rss_mb()


def _snapshot_cache_stats(
    *,
    entry: _DisplayListCacheEntry | None = None,
    lookup_cache_hit: bool | None = None,
) -> dict[str, Any]:
    capacity = max(1, int(_resolve_cache_capacity()))
    byte_limit = max(1, int(_resolve_cache_byte_limit()))
    stats: dict[str, Any] = {
        "entries": len(_DISPLAY_LIST_CACHE),
        "capacity": capacity,
        "capacity_entries": capacity,
        "byte_limit": byte_limit,
        "total_estimated_bytes": int(_DISPLAY_LIST_CACHE_TOTAL_ESTIMATED_BYTES),
        "hit_count": int(_DISPLAY_LIST_HIT_COUNT),
        "miss_count": int(_DISPLAY_LIST_MISS_COUNT),
        "eviction_count": int(_DISPLAY_LIST_EVICTION_COUNT),
        "evicted_estimated_bytes": int(_DISPLAY_LIST_EVICTED_ESTIMATED_BYTES),
        "last_eviction_reason": _DISPLAY_LIST_LAST_EVICTION_REASON,
    }
    if entry is not None:
        stats["entry_estimated_bytes"] = int(entry.estimated_bytes)
    if lookup_cache_hit is not None:
        stats["lookup_cache_hit"] = bool(lookup_cache_hit)
    rss_mb = _process_rss_mb()
    if rss_mb > 0:
        stats["process_rss_mb"] = rss_mb
    return stats


def _evict_key(key: _CacheKey, *, reason: str) -> None:
    global _DISPLAY_LIST_CACHE_TOTAL_ESTIMATED_BYTES
    global _DISPLAY_LIST_EVICTION_COUNT
    global _DISPLAY_LIST_EVICTED_ESTIMATED_BYTES
    global _DISPLAY_LIST_LAST_EVICTION_REASON

    entry = _DISPLAY_LIST_CACHE.pop(key, None)
    if entry is None:
        return
    _DISPLAY_LIST_CACHE_TOTAL_ESTIMATED_BYTES = max(
        0,
        _DISPLAY_LIST_CACHE_TOTAL_ESTIMATED_BYTES - int(entry.estimated_bytes),
    )
    _DISPLAY_LIST_EVICTION_COUNT += 1
    _DISPLAY_LIST_EVICTED_ESTIMATED_BYTES += int(entry.estimated_bytes)
    _DISPLAY_LIST_LAST_EVICTION_REASON = str(reason or "unknown")


def _evict_to_capacity(capacity: int, byte_limit: int | None = None) -> None:
    """LRU eviction by entry count and estimated bytes."""
    capacity = max(1, int(capacity))
    byte_limit = max(1, int(byte_limit or _resolve_cache_byte_limit()))
    while len(_DISPLAY_LIST_CACHE_ORDER) > capacity:
        oldest = _DISPLAY_LIST_CACHE_ORDER.pop(0)
        _evict_key(oldest, reason="entry_capacity")
    while (
        len(_DISPLAY_LIST_CACHE_ORDER) > 1
        and _DISPLAY_LIST_CACHE_TOTAL_ESTIMATED_BYTES > byte_limit
    ):
        oldest = _DISPLAY_LIST_CACHE_ORDER.pop(0)
        _evict_key(oldest, reason="byte_capacity")


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


def _lookup_display_list_entry(
    pdf_path: Path,
    page_index: int,
    *,
    count_lookup: bool,
) -> tuple[_DisplayListCacheEntry, dict[str, Any]]:
    """Build or fetch a cached ``fitz.DisplayList`` entry.

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
    global _DISPLAY_LIST_CACHE_TOTAL_ESTIMATED_BYTES
    global _DISPLAY_LIST_HIT_COUNT
    global _DISPLAY_LIST_MISS_COUNT

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
        cached.last_access_seq = _next_access_seq()
        # Move to MRU end.
        if cache_key in _DISPLAY_LIST_CACHE_ORDER:
            _DISPLAY_LIST_CACHE_ORDER.remove(cache_key)
        _DISPLAY_LIST_CACHE_ORDER.append(cache_key)
        if count_lookup:
            _DISPLAY_LIST_HIT_COUNT += 1
        return cached, _snapshot_cache_stats(entry=cached, lookup_cache_hit=True)

    if count_lookup:
        _DISPLAY_LIST_MISS_COUNT += 1

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

    seq = _next_access_seq()
    entry = _DisplayListCacheEntry(
        display_list=display_list,
        rect_w=rect_w,
        rect_h=rect_h,
        estimated_bytes=_estimate_display_list_bytes(rect_w, rect_h, sig_size),
        created_seq=seq,
        last_access_seq=seq,
    )
    _DISPLAY_LIST_CACHE[cache_key] = entry
    _DISPLAY_LIST_CACHE_ORDER.append(cache_key)
    _DISPLAY_LIST_CACHE_TOTAL_ESTIMATED_BYTES += int(entry.estimated_bytes)
    _evict_to_capacity(_resolve_cache_capacity(), _resolve_cache_byte_limit())
    current_entry = _DISPLAY_LIST_CACHE.get(cache_key, entry)
    return current_entry, _snapshot_cache_stats(
        entry=current_entry,
        lookup_cache_hit=False,
    )


def get_display_list(pdf_path: Path, page_index: int) -> Any:
    """Return a cached ``fitz.DisplayList`` for ``(pdf_path, page_index)``."""
    entry, _stats = _lookup_display_list_entry(
        pdf_path,
        page_index,
        count_lookup=True,
    )
    return entry.display_list


def get_display_list_entry(
    pdf_path: Path,
    page_index: int,
) -> tuple[Any, float, float, dict[str, Any]]:
    """Return DisplayList, page rect and lookup telemetry in one call."""
    entry, stats = _lookup_display_list_entry(
        pdf_path,
        page_index,
        count_lookup=True,
    )
    return entry.display_list, entry.rect_w, entry.rect_h, stats


def get_page_rect(pdf_path: Path, page_index: int) -> Tuple[float, float]:
    """Return ``(width, height)`` of the cached page rect in PDF points.

    Triggers a cache build if the entry is not yet present so callers
    can ask for the rect before rendering and not pay a separate
    open-document cost. The corresponding DisplayList stays in cache.
    """
    _assert_main_thread()
    entry, _stats = _lookup_display_list_entry(
        pdf_path,
        page_index,
        count_lookup=False,
    )
    return entry.rect_w, entry.rect_h


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


def cache_stats() -> dict[str, Any]:
    """Return diagnostic counts for the in-process cache."""
    return _snapshot_cache_stats()


def _clear_cache() -> None:
    """Test hook — wipe the in-process cache so each test starts clean.

    Intentionally underscore-prefixed; production callers should never
    need this because the file-signature cache key invalidates entries
    automatically on source mtime change.
    """
    global _DISPLAY_LIST_CACHE_TOTAL_ESTIMATED_BYTES
    global _DISPLAY_LIST_HIT_COUNT
    global _DISPLAY_LIST_MISS_COUNT
    global _DISPLAY_LIST_EVICTION_COUNT
    global _DISPLAY_LIST_EVICTED_ESTIMATED_BYTES
    global _DISPLAY_LIST_LAST_EVICTION_REASON
    global _DISPLAY_LIST_ACCESS_SEQ

    _DISPLAY_LIST_CACHE.clear()
    _DISPLAY_LIST_CACHE_ORDER.clear()
    _DISPLAY_LIST_CACHE_TOTAL_ESTIMATED_BYTES = 0
    _DISPLAY_LIST_HIT_COUNT = 0
    _DISPLAY_LIST_MISS_COUNT = 0
    _DISPLAY_LIST_EVICTION_COUNT = 0
    _DISPLAY_LIST_EVICTED_ESTIMATED_BYTES = 0
    _DISPLAY_LIST_LAST_EVICTION_REASON = ""
    _DISPLAY_LIST_ACCESS_SEQ = 0


__all__ = [
    "get_display_list",
    "get_display_list_entry",
    "get_page_rect",
    "render_clip_to_png",
    "cache_stats",
]
