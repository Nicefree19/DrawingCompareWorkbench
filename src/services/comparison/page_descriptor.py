# -*- coding: utf-8 -*-
"""Per-page descriptor for multi-page PDF auto-matching.

Phase H1 entry point. The existing scan stage builds one
:class:`DrawingFileDescriptor` per file (drawing_batch.py:124), even for
multi-page PDFs — meaning if one PDF holds 5 drawing sheets, the matcher
treats it as a single unit and the comparison stage falls back to
sequential page comparison (A.page0 vs B.page0, A.page1 vs B.page1, ...).
That breaks the moment pages are reordered, inserted, or removed between
revisions.

This module produces a *page-level* descriptor so a downstream matcher
(``page_matcher.py``) can score every (A.page_i, B.page_j) pair and
solve the assignment via :func:`scipy.optimize.linear_sum_assignment`.

Five signals are extracted per page; weights live in ``page_matcher.py``:

  1. ``drawing_number`` — extracted from the title block via the existing
     ``PROJECT_DRAWING_NUMBER_PATTERN`` regex (drawing_batch.py:40)
  2. ``title_text`` — text from the bottom-right 25 % of the page
     (typical Korean structural / architectural title block location)
  3. ``visual_hash`` — perceptual hash of a 256 px page thumbnail
     (mirrors ``_pdf_thumbnail_hash`` from drawing_batch.py but per-page)
  4. ``full_text_hash`` — hash of the full page text content
  5. ``page_size`` — (width, height) for dimension match

Title-block extraction falls back to full-page text when the bottom-right
region yields fewer than 10 characters, so non-standard layouts still
contribute a usable title signal.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Phase H1 follow-up — single source of truth for the project drawing-
# number regex. ``drawing_batch.py`` (file-level matcher) and this module
# (page-level matcher) used to define duplicate copies which would drift
# if either side ever updated the pattern. Now both import from
# ``drawing_id_pattern``. Re-exported under the historical name so
# external callers + the module's __all__ continue to work.
from src.services.comparison.drawing_id_pattern import (
    DRAWING_NUMBER_PATTERN as PROJECT_DRAWING_NUMBER_PATTERN,
)

# Title block region — bottom-right 25 % of the page. PyMuPDF measures
# coordinates from the top-left, so "bottom" means high y values.
TITLE_BLOCK_LEFT_RATIO = 0.75    # start at 75 % from left edge
TITLE_BLOCK_TOP_RATIO = 0.75     # start at 75 % from top (= bottom 25 %)

# Minimum characters in the title-block region before we accept the
# extraction. Below this we fall back to full-page text.
TITLE_BLOCK_MIN_CHARS = 10

# Thumbnail rendering DPI for the visual hash. Low DPI is intentional:
# pHash is a coarse-bucket signal, and we want this to be cheap (Phase H1
# perf target: <100 ms per page total descriptor build).
THUMBNAIL_RENDER_SCALE = 0.25  # 25 % of native PDF resolution

# pHash bit count — 64 bits (8x8 reduced grayscale signature)
VISUAL_HASH_SIZE = 8


@dataclass
class PerPageDescriptor:
    """One PDF page's matching signals.

    All fields are populated by :func:`build_per_page_descriptor`. Empty
    strings represent "signal not available" — the matcher treats those
    as neutral (0.5) rather than rejecting outright.
    """

    pdf_path: str
    page_index: int                       # 0-based
    page_size: Tuple[float, float] = (0.0, 0.0)  # (width, height) in points
    drawing_number: str = ""              # e.g. "S20-0002"
    sheet: str = ""                       # secondary code (rare)
    title_text: str = ""                  # raw title-block text
    title_text_normalised: str = ""       # whitespace-collapsed for fuzzy match
    full_text_hash: str = ""              # hex digest of full page text
    visual_hash: str = ""                 # hex pHash
    title_block_used_fallback: bool = False  # True when full-page fallback fired

    def to_dict(self) -> dict[str, object]:
        return {
            "pdf_path": self.pdf_path,
            "page_index": self.page_index,
            "page_size": list(self.page_size),
            "drawing_number": self.drawing_number,
            "sheet": self.sheet,
            "title_text": self.title_text[:500],  # truncate for log/json sanity
            "title_text_normalised": self.title_text_normalised[:500],
            "full_text_hash": self.full_text_hash,
            "visual_hash": self.visual_hash,
            "title_block_used_fallback": self.title_block_used_fallback,
        }


# ---------------------------------------------------------------------------
# Helpers — kept module-level so unit tests can drive them directly
# ---------------------------------------------------------------------------


def normalise_text(text: str) -> str:
    """Collapse whitespace + lowercase for fuzzy matching.

    Korean characters survive .lower() unchanged; this helper just removes
    arbitrary whitespace differences (line wraps, multiple spaces) so two
    rasterised title-block scans of the same drawing produce comparable
    strings.
    """

    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned.lower()


def extract_drawing_number(text: str) -> str:
    """Apply the project drawing-number regex and return the first match.

    Phase H1 follow-up — delegates to
    :func:`drawing_id_pattern.extract_drawing_number` so file-level and
    page-level matching share a single implementation. Re-exported here
    for back-compat with existing imports.
    """

    from src.services.comparison.drawing_id_pattern import (
        extract_drawing_number as _extract,
    )
    return _extract(text)


def extract_title_block_text(page) -> tuple[str, bool]:
    """Pull text from the bottom-right 25 % of a PyMuPDF Page.

    Returns ``(text, used_fallback)``. When the title-block region yields
    fewer than ``TITLE_BLOCK_MIN_CHARS`` chars, falls back to the full
    page text and sets ``used_fallback=True``.

    Imported lazily so the module loads without PyMuPDF (tests can mock).
    """

    try:
        import fitz  # type: ignore
    except ImportError:
        return ("", False)

    rect = page.rect
    title_rect = fitz.Rect(
        rect.x0 + rect.width * TITLE_BLOCK_LEFT_RATIO,
        rect.y0 + rect.height * TITLE_BLOCK_TOP_RATIO,
        rect.x1,
        rect.y1,
    )
    try:
        text = page.get_text("text", clip=title_rect) or ""
    except Exception as exc:
        logger.warning("Title-block extraction failed: %s", exc)
        text = ""
    text = text.strip()
    if len(text) >= TITLE_BLOCK_MIN_CHARS:
        return (text, False)
    # Fallback — non-standard layout, use full page text.
    try:
        full = (page.get_text("text") or "").strip()
    except Exception:
        full = ""
    return (full, True)


def compute_visual_hash(page) -> str:
    """Perceptual hash (pHash) of a low-resolution page thumbnail.

    Uses a simple 8x8 average-hash variant — the rendered pixmap is
    converted to grayscale, downsampled to 8x8, and each pixel compared
    against the mean to form a 64-bit signature. Cheap (<10 ms) and
    produces hex output suitable for ``_hash_similarity`` Hamming-distance
    comparison in ``page_matcher.py``.
    """

    try:
        import fitz  # type: ignore
    except ImportError:
        return ""

    def _render_pixmap(target_page):
        return target_page.get_pixmap(
            matrix=fitz.Matrix(THUMBNAIL_RENDER_SCALE, THUMBNAIL_RENDER_SCALE),
            alpha=False,
            colorspace=fitz.csGRAY,
        )

    try:
        pix = _render_pixmap(page)
    except Exception as exc:
        # Defensive single-retry — PyMuPDF can invalidate a held Page
        # reference if newer pages are added to the same Document
        # (the underlying fz_page pointer becomes null). Re-fetch via
        # parent[index] when possible.
        parent_doc = getattr(page, "parent", None)
        page_number = getattr(page, "number", None)
        if parent_doc is not None and isinstance(page_number, int):
            try:
                refreshed = parent_doc[page_number]
                pix = _render_pixmap(refreshed)
            except Exception as exc2:
                logger.warning(
                    "Pixmap render failed for visual hash (after refresh retry): %s",
                    exc2,
                )
                return ""
        else:
            # When parent / number are also None the page is fully
            # invalidated (typical for synthetic in-memory test PDFs
            # that mutate the doc after taking a page reference).
            # Real saved PDFs never hit this path; keep the message at
            # DEBUG so the test environment doesn't spam WARNING logs.
            msg = str(exc)
            if "null reference" in msg.lower():
                logger.debug(
                    "Visual hash skipped (stale Page reference; "
                    "parent/number both None): %s", msg,
                )
            else:
                logger.warning("Pixmap render failed for visual hash: %s", exc)
            return ""

    # Downsample to 8x8 by averaging cells. PyMuPDF Pixmap exposes raw
    # bytes; do the resize manually so we don't add a Pillow/numpy
    # dependency just for this signature.
    src_w, src_h = pix.width, pix.height
    if src_w == 0 or src_h == 0:
        return ""
    samples = pix.samples  # bytes, length = src_w * src_h (grayscale)
    cell_w = max(1, src_w // VISUAL_HASH_SIZE)
    cell_h = max(1, src_h // VISUAL_HASH_SIZE)

    cells: List[float] = []
    for cy in range(VISUAL_HASH_SIZE):
        for cx in range(VISUAL_HASH_SIZE):
            x0 = cx * cell_w
            y0 = cy * cell_h
            x1 = min(src_w, x0 + cell_w)
            y1 = min(src_h, y0 + cell_h)
            total = 0
            count = 0
            for yy in range(y0, y1):
                row_off = yy * src_w
                for xx in range(x0, x1):
                    total += samples[row_off + xx]
                    count += 1
            cells.append(total / max(1, count))

    if not cells:
        return ""
    mean = sum(cells) / len(cells)
    bits = "".join("1" if c > mean else "0" for c in cells)
    # 64 bits → 16 hex chars
    return hex(int(bits, 2))[2:].zfill(16)


def hash_text(text: str) -> str:
    """SHA-1 of normalised text (16 hex chars). Used by full_text_hash."""

    if not text:
        return ""
    h = hashlib.sha1(normalise_text(text).encode("utf-8")).hexdigest()
    return h[:16]  # truncated for json sanity; collision risk negligible for matching


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_descriptor_from_open_page(
    pdf_path_str: str, page_index: int, page,
) -> PerPageDescriptor:
    """Build one descriptor from an ALREADY-OPEN PyMuPDF page.

    Phase H1 perf fix — separated from :func:`build_per_page_descriptor`
    so :func:`build_per_page_descriptors` (plural) can iterate pages
    without re-opening the PDF for each one. The plural form was
    triggering N+1 ``fitz.open`` calls (one per page), making 100-page
    PDFs ~100x slower than necessary.
    """

    title_text, used_fallback = extract_title_block_text(page)
    try:
        full_text = (page.get_text("text") or "").strip()
    except Exception:
        full_text = ""
    try:
        page_size = (float(page.rect.width), float(page.rect.height))
    except Exception:
        page_size = (0.0, 0.0)
    visual_hash = compute_visual_hash(page)

    # Drawing number — try title block first (more reliable), fall
    # back to scanning full text if not found in title region.
    drawing_number = extract_drawing_number(title_text)
    if not drawing_number and not used_fallback:
        drawing_number = extract_drawing_number(full_text)

    return PerPageDescriptor(
        pdf_path=pdf_path_str,
        page_index=page_index,
        page_size=page_size,
        drawing_number=drawing_number,
        sheet="",  # reserved for future per-sheet code extraction
        title_text=title_text,
        title_text_normalised=normalise_text(title_text),
        full_text_hash=hash_text(full_text),
        visual_hash=visual_hash,
        title_block_used_fallback=used_fallback,
    )


def build_per_page_descriptor(pdf_path: Path, page_index: int) -> Optional[PerPageDescriptor]:
    """Build a single page's descriptor.

    Returns None when PyMuPDF is unavailable or the page can't be opened.
    Used directly by unit tests; the plural-form
    :func:`build_per_page_descriptors` opens the PDF once and reuses the
    handle across pages instead of calling this function in a loop.
    """

    try:
        import fitz  # type: ignore
    except ImportError:
        logger.warning("PyMuPDF (fitz) not available; cannot build page descriptor")
        return None

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        logger.warning("Cannot open PDF %s: %s", pdf_path, exc)
        return None

    try:
        if page_index < 0 or page_index >= len(doc):
            logger.warning("Page index %d out of range for %s (page_count=%d)",
                           page_index, pdf_path, len(doc))
            return None
        return _build_descriptor_from_open_page(
            str(pdf_path), page_index, doc[page_index],
        )
    finally:
        try:
            doc.close()
        except Exception:
            pass


def build_per_page_descriptors(pdf_path: Path) -> List[PerPageDescriptor]:
    """Build descriptors for every page in a PDF.

    Returns an empty list when the PDF can't be opened. Each page is
    processed independently — failures in one page don't abort the rest;
    failed pages are simply skipped.

    Phase H1 perf fix — opens the PDF EXACTLY ONCE and reuses the same
    handle across pages. The earlier implementation called
    :func:`build_per_page_descriptor` in a loop, which itself opened the
    PDF, causing N+1 ``fitz.open`` for an N-page PDF (≈100x slowdown
    on 100-page reports).
    """

    try:
        import fitz  # type: ignore
    except ImportError:
        logger.warning("PyMuPDF (fitz) not available; returning empty descriptor list")
        return []

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        logger.warning("PDF not found: %s", pdf_path)
        return []

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        logger.warning("Cannot open PDF %s: %s", pdf_path, exc)
        return []

    results: List[PerPageDescriptor] = []
    pdf_path_str = str(pdf_path)
    try:
        page_count = len(doc)
        for idx in range(page_count):
            try:
                # Reuse the OPEN doc handle; do not re-open per page.
                page = doc[idx]
                desc = _build_descriptor_from_open_page(
                    pdf_path_str, idx, page,
                )
                results.append(desc)
            except Exception:
                logger.exception("Failed to build descriptor for %s page %d",
                                 pdf_path, idx)
    finally:
        try:
            doc.close()
        except Exception:
            pass

    logger.info(
        "Built %d page descriptors for %s (drawing numbers found: %d)",
        len(results), pdf_path.name,
        sum(1 for d in results if d.drawing_number),
    )
    return results


__all__ = [
    "PROJECT_DRAWING_NUMBER_PATTERN",
    "TITLE_BLOCK_LEFT_RATIO",
    "TITLE_BLOCK_TOP_RATIO",
    "TITLE_BLOCK_MIN_CHARS",
    "VISUAL_HASH_SIZE",
    "PerPageDescriptor",
    "normalise_text",
    "extract_drawing_number",
    "extract_title_block_text",
    "compute_visual_hash",
    "hash_text",
    "build_per_page_descriptor",
    "build_per_page_descriptors",
]
