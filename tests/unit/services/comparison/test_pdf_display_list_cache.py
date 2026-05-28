"""Unit tests for the per-page PyMuPDF DisplayList cache.

Plan §17 Phase B-1b — GPT Pro F3 (HIGH) follow-up. Covers:
  * Cache reuse for repeated (path, page) lookups
  * Cache invalidation when the source PDF mtime changes
  * Capacity respects ``DRAWING_COMPARE_INDEX_CACHE_SIZE`` env var
  * ``render_clip_to_png`` produces a non-empty PNG at the requested
    clip and the byte size scales with clip area
  * Clean ImportError when PyMuPDF is unavailable
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Tuple

import pytest

# Make repo root importable.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Skip the entire module if PyMuPDF is missing — the production
# fallback handles that case, but the unit tests deliberately exercise
# the DisplayList-only paths.
fitz = pytest.importorskip("fitz")

from src.services.comparison import pdf_display_list_cache  # noqa: E402


def _make_sample_pdf(path: Path, *, page_count: int = 1, page_size: Tuple[int, int] = (612, 792)) -> Path:
    """Write a minimal valid PDF containing ``page_count`` pages with
    some drawing content (so the DisplayList has something to cache).

    Avoids any external fixture dependency — the cache only cares
    about the parsed PDF structure, not its visual content.
    """
    doc = fitz.open()
    try:
        for i in range(max(1, int(page_count))):
            page = doc.new_page(width=float(page_size[0]), height=float(page_size[1]))
            # Add a recognisable marker so test failures are easier to
            # diagnose when comparing rendered output.
            page.insert_text((50, 80 + 30 * i), f"page-{i}", fontsize=16)
            page.draw_rect(
                fitz.Rect(40, 40, page.rect.width - 40, page.rect.height - 40),
                color=(0, 0, 0),
            )
        doc.save(str(path))
    finally:
        doc.close()
    return path


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch: pytest.MonkeyPatch):
    """Each test starts and ends with an empty cache so ordering does
    not affect outcomes."""
    pdf_display_list_cache._clear_cache()
    yield
    pdf_display_list_cache._clear_cache()


# ---------------------------------------------------------------------------
# Core caching behaviour
# ---------------------------------------------------------------------------


def test_get_display_list_caches_per_page(tmp_path: Path) -> None:
    """Same (path, page) tuple must return the SAME DisplayList object
    on the second call — that is the whole point of the cache.
    """
    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=2)

    first = pdf_display_list_cache.get_display_list(pdf, 0)
    second = pdf_display_list_cache.get_display_list(pdf, 0)

    assert first is second, (
        "second lookup with same key must return the same DisplayList; "
        "otherwise the cache is not working"
    )
    # Different page index must produce a different DisplayList.
    page1 = pdf_display_list_cache.get_display_list(pdf, 1)
    assert page1 is not first


def test_cache_invalidates_on_mtime_change(tmp_path: Path) -> None:
    """Re-saving the source PDF must invalidate the cached entry so
    callers never receive stale content after the user edits the
    drawing.
    """
    pdf = tmp_path / "doc.pdf"
    _make_sample_pdf(pdf, page_count=1)
    first = pdf_display_list_cache.get_display_list(pdf, 0)

    # Re-save with a bumped mtime and different content so the
    # signature actually changes.
    import time as _time

    _time.sleep(0.01)
    _make_sample_pdf(pdf, page_count=2)
    if os.name == "nt":
        # NTFS may keep the same mtime within the same second; force
        # an update.
        os.utime(pdf, None)

    second = pdf_display_list_cache.get_display_list(pdf, 0)
    assert second is not first, (
        "cache must invalidate when source mtime changes; "
        "otherwise editing the PDF gives stale renders"
    )


def test_cache_size_respects_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting ``DRAWING_COMPARE_INDEX_CACHE_SIZE`` must clamp the
    cache to that many entries. Mirrors the policy used by
    ``DrawingRenderIndex`` (Plan §15 A-3) so operators only need one
    knob.
    """
    monkeypatch.setenv("DRAWING_COMPARE_INDEX_CACHE_SIZE", "2")

    # Build three distinct cache entries (three pages of the same PDF).
    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=3)
    pdf_display_list_cache.get_display_list(pdf, 0)
    pdf_display_list_cache.get_display_list(pdf, 1)
    pdf_display_list_cache.get_display_list(pdf, 2)

    stats = pdf_display_list_cache.cache_stats()
    assert stats["entries"] == 2, (
        f"capacity=2 should cap the cache at 2; got {stats}"
    )
    assert stats["capacity"] == 2


def test_cache_stats_track_hits_misses_and_page_rect_is_not_double_counted(tmp_path: Path) -> None:
    """The combined entry API records one lookup, while ``get_page_rect``
    can reuse that entry without inflating hit/miss counters.
    """
    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=1)

    display_list, rect_w, rect_h, first_stats = pdf_display_list_cache.get_display_list_entry(pdf, 0)

    assert display_list is not None
    assert rect_w > 0
    assert rect_h > 0
    assert first_stats["lookup_cache_hit"] is False
    assert first_stats["miss_count"] == 1
    assert first_stats["hit_count"] == 0
    assert first_stats["total_estimated_bytes"] >= first_stats["entry_estimated_bytes"] > 0

    assert pdf_display_list_cache.get_page_rect(pdf, 0) == (rect_w, rect_h)
    after_rect = pdf_display_list_cache.cache_stats()
    assert after_rect["miss_count"] == 1
    assert after_rect["hit_count"] == 0

    assert pdf_display_list_cache.get_display_list(pdf, 0) is display_list
    after_hit = pdf_display_list_cache.cache_stats()
    assert after_hit["miss_count"] == 1
    assert after_hit["hit_count"] == 1


def test_cache_byte_limit_evicts_lru_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tiny byte budget must evict the oldest DisplayList before RSS can
    grow with page navigation.
    """
    monkeypatch.setenv("DRAWING_COMPARE_DISPLAY_LIST_CACHE_MB", "1")
    monkeypatch.setattr(
        pdf_display_list_cache,
        "_estimate_display_list_bytes",
        lambda _w, _h, _size: 600_000,
    )

    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=3, page_size=(120, 120))
    pdf_display_list_cache.get_display_list(pdf, 0)
    pdf_display_list_cache.get_display_list(pdf, 1)

    stats = pdf_display_list_cache.cache_stats()
    assert stats["byte_limit"] == 1024 * 1024
    assert stats["entries"] == 1
    assert stats["total_estimated_bytes"] <= stats["byte_limit"]
    assert stats["eviction_count"] == 1
    assert stats["last_eviction_reason"] == "byte_capacity"


def test_clear_cache_resets_byte_and_lookup_stats(tmp_path: Path) -> None:
    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=1)
    pdf_display_list_cache.get_display_list(pdf, 0)
    pdf_display_list_cache.get_display_list(pdf, 0)
    assert pdf_display_list_cache.cache_stats()["total_estimated_bytes"] > 0
    assert pdf_display_list_cache.cache_stats()["hit_count"] == 1

    pdf_display_list_cache._clear_cache()

    stats = pdf_display_list_cache.cache_stats()
    assert stats["entries"] == 0
    assert stats["total_estimated_bytes"] == 0
    assert stats["hit_count"] == 0
    assert stats["miss_count"] == 0
    assert stats["eviction_count"] == 0


def test_get_display_list_raises_on_missing_file(tmp_path: Path) -> None:
    """Surfacing the missing-file error early lets callers fall back
    to the PIL path with a clear reason instead of producing an
    unhelpful PyMuPDF error.
    """
    with pytest.raises(FileNotFoundError):
        pdf_display_list_cache.get_display_list(tmp_path / "does-not-exist.pdf", 0)


def test_get_display_list_raises_on_bad_page_index(tmp_path: Path) -> None:
    """Out-of-range page indices raise rather than silently clamping
    so the caller can decide what to fall back to — the legacy
    ``_render_pdf_to_png`` clamps with a warning but that policy is
    too permissive for the DisplayList path where stale results
    would otherwise be cached against the wrong key.
    """
    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=1)
    with pytest.raises(IndexError):
        pdf_display_list_cache.get_display_list(pdf, 5)


def test_get_display_list_normalizes_corrupt_pdf_open_errors(tmp_path: Path) -> None:
    """Bad PDFs should use the known fallback contract, not leak fitz internals."""

    pdf = tmp_path / "corrupt.pdf"
    pdf.write_bytes(b"not a pdf")

    with pytest.raises(ValueError, match="PDF could not be opened"):
        pdf_display_list_cache.get_display_list(pdf, 0)


def test_get_display_list_normalizes_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Permission-denied PDF opens should be treated as fallbackable input errors."""

    pdf = _make_sample_pdf(tmp_path / "locked.pdf", page_count=1)

    def _deny_open(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(fitz, "open", _deny_open)

    with pytest.raises(ValueError, match="PDF could not be opened"):
        pdf_display_list_cache.get_display_list(pdf, 0)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_clip_produces_png(tmp_path: Path) -> None:
    """Smoke: ``render_clip_to_png`` must produce a non-empty PNG and
    a telemetry dict carrying wall_ms + output_bytes.
    """
    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=1)
    display_list = pdf_display_list_cache.get_display_list(pdf, 0)
    out = tmp_path / "clip.png"

    result = pdf_display_list_cache.render_clip_to_png(
        display_list,
        clip=(50.0, 50.0, 250.0, 250.0),
        output_path=out,
        scale=2.0,
    )

    assert out.exists()
    assert out.stat().st_size > 0
    assert result["wall_ms"] >= 0.0
    assert result["output_bytes"] > 0
    assert result["scale"] == 2.0
    assert result["clip"] == (50.0, 50.0, 250.0, 250.0)


def test_clip_smaller_than_page_renders_smaller_pixmap(tmp_path: Path) -> None:
    """Sanity: rendering a 100x100 clip must produce a smaller PNG
    than rendering the full page rect. Catches a regression where
    the ``clip`` argument is dropped and the full page is rendered
    every time.
    """
    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=1, page_size=(612, 792))
    display_list = pdf_display_list_cache.get_display_list(pdf, 0)
    page_w, page_h = pdf_display_list_cache.get_page_rect(pdf, 0)

    full_path = tmp_path / "full.png"
    pdf_display_list_cache.render_clip_to_png(
        display_list,
        clip=(0.0, 0.0, page_w, page_h),
        output_path=full_path,
        scale=2.0,
    )

    small_path = tmp_path / "small.png"
    pdf_display_list_cache.render_clip_to_png(
        display_list,
        clip=(50.0, 50.0, 150.0, 150.0),
        output_path=small_path,
        scale=2.0,
    )

    assert full_path.stat().st_size > small_path.stat().st_size, (
        "small clip should produce a smaller PNG than the full page; "
        "if not, clip is being ignored"
    )


def test_render_clip_rejects_inverted_rectangle(tmp_path: Path) -> None:
    """An empty or inverted clip rectangle must fail loudly — the
    caller is responsible for clamping, and silently producing an
    empty PNG would mask upstream bugs.
    """
    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=1)
    display_list = pdf_display_list_cache.get_display_list(pdf, 0)

    with pytest.raises(ValueError):
        pdf_display_list_cache.render_clip_to_png(
            display_list,
            clip=(100.0, 100.0, 50.0, 200.0),  # right < left
            output_path=tmp_path / "bad.png",
        )


# ---------------------------------------------------------------------------
# Thread-safety guard
# ---------------------------------------------------------------------------


def test_non_main_thread_call_raises_runtime_error(tmp_path: Path) -> None:
    """PyMuPDF is not thread-safe; the guard must surface a clear
    error rather than silently corrupting state.
    """
    import threading

    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=1)

    captured: list[Exception] = []

    def _worker() -> None:
        try:
            pdf_display_list_cache.get_display_list(pdf, 0)
        except Exception as exc:
            captured.append(exc)

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join(timeout=5.0)

    assert len(captured) == 1, "expected RuntimeError from worker"
    assert isinstance(captured[0], RuntimeError)
    assert "single-threaded" in str(captured[0])


# ---------------------------------------------------------------------------
# Import-error contract
# ---------------------------------------------------------------------------


def test_import_error_when_fitz_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When PyMuPDF is missing the helper raises ImportError so the
    caller can fall back to the PIL path. We simulate the missing
    import by hiding the ``fitz`` module from
    :func:`importlib.import_module`.
    """
    import builtins

    pdf = _make_sample_pdf(tmp_path / "doc.pdf", page_count=1)

    real_import = builtins.__import__

    def _blocking_import(name: str, *args, **kwargs):
        if name == "fitz" or name.startswith("fitz."):
            raise ImportError("simulated missing PyMuPDF")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)

    with pytest.raises(ImportError, match="PyMuPDF"):
        pdf_display_list_cache.get_display_list(pdf, 0)
