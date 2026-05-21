# -*- coding: utf-8 -*-
"""Unit tests for the per-page PDF descriptor (Phase H1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison.page_descriptor import (
    PROJECT_DRAWING_NUMBER_PATTERN,
    PerPageDescriptor,
    extract_drawing_number,
    hash_text,
    normalise_text,
)

# fitz (PyMuPDF) is required for the page-extraction tests; mark them
# skip when unavailable.
fitz = pytest.importorskip("fitz")  # noqa: F401  (used by all live-pdf tests)


# ---------------------------------------------------------------------------
# Pure-Python helpers (no PDF needed)
# ---------------------------------------------------------------------------


def test_normalise_text_collapses_whitespace_and_lowercases() -> None:
    assert normalise_text("  Hello\nWorld\t!  ") == "hello world !"


def test_normalise_text_preserves_korean() -> None:
    # Hangul (한글) is unaffected by .lower() — should pass through unchanged
    out = normalise_text("S20-0002 평택 5동 구조평면도")
    assert "평택" in out
    assert "s20-0002" in out


def test_extract_drawing_number_finds_simple_code() -> None:
    assert extract_drawing_number("도면번호: S20-0002 (3층 평면도)") == "S20-0002"


def test_extract_drawing_number_normalises_separator() -> None:
    # Pattern allows separators like ".", "_", " "
    assert extract_drawing_number("S20.0002") == "S20-0002"
    assert extract_drawing_number("S20_0002") == "S20-0002"
    assert extract_drawing_number("S20 0002") == "S20-0002"


def test_extract_drawing_number_returns_first_match() -> None:
    text = "S20-0001 referenced from S20-0002"
    assert extract_drawing_number(text) == "S20-0001"


def test_extract_drawing_number_returns_empty_when_none() -> None:
    assert extract_drawing_number("아무 번호도 없는 도면") == ""
    assert extract_drawing_number("") == ""


def test_extract_drawing_number_uppercases() -> None:
    assert extract_drawing_number("s20-0002") == "S20-0002"


def test_hash_text_stable() -> None:
    assert hash_text("hello") == hash_text("HELLO")  # normalised → identical
    assert hash_text("hello") != hash_text("world")
    assert hash_text("") == ""


def test_hash_text_length_truncated() -> None:
    assert len(hash_text("a")) == 16


# ---------------------------------------------------------------------------
# PDF-driven tests — synthesise a tiny in-memory PDF and exercise the builder
# ---------------------------------------------------------------------------


def _make_synthetic_pdf(tmp_path: Path, *, pages: int = 1,
                        title_block_text: str = "S20-0002 3층 구조평면도") -> Path:
    """Build a tiny PDF with a known title-block region.

    Each page draws ``title_block_text`` in the bottom-right area so the
    descriptor's title-block extractor can find it.
    """

    import fitz  # noqa: F811 — re-import inside fixture for clarity
    doc = fitz.open()
    for page_idx in range(pages):
        # Standard A4 portrait: 595 x 842 points
        page = doc.new_page(width=595, height=842)
        # Title block region: (446, 632) — (595, 842) ≈ bottom-right 25 %
        # Insert text at top-left of that region.
        rect = fitz.Rect(450, 700, 590, 830)
        page.insert_text(
            (rect.x0 + 5, rect.y0 + 20),
            f"{title_block_text} - Page {page_idx + 1}",
            fontsize=10,
        )
        # Some body content so the page isn't empty
        page.insert_text((50, 100), f"Page {page_idx + 1} body content", fontsize=12)
    pdf_path = tmp_path / f"synthetic_{pages}p.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def test_build_descriptor_extracts_title_block(tmp_path: Path) -> None:
    from src.services.comparison.page_descriptor import build_per_page_descriptor

    pdf = _make_synthetic_pdf(tmp_path, pages=1)
    desc = build_per_page_descriptor(pdf, 0)
    assert desc is not None
    assert desc.page_index == 0
    # Title block region should contain our drawing number text
    assert "S20-0002" in desc.title_text or desc.drawing_number == "S20-0002"
    assert desc.drawing_number == "S20-0002"
    assert desc.title_block_used_fallback is False
    assert desc.page_size[0] > 0 and desc.page_size[1] > 0


def test_build_descriptor_falls_back_when_title_region_empty(tmp_path: Path) -> None:
    """If we put text only in the upper-left, the title-block region is
    empty → fallback to full-page text."""

    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "S30-9999 outside title region", fontsize=10)
    pdf_path = tmp_path / "no_title_block.pdf"
    doc.save(str(pdf_path))
    doc.close()

    from src.services.comparison.page_descriptor import build_per_page_descriptor
    desc = build_per_page_descriptor(pdf_path, 0)
    assert desc is not None
    assert desc.title_block_used_fallback is True
    # Fallback path still finds the drawing number from full text
    assert desc.drawing_number == "S30-9999"


def test_build_descriptors_for_multipage_pdf(tmp_path: Path) -> None:
    from src.services.comparison.page_descriptor import build_per_page_descriptors

    pdf = _make_synthetic_pdf(tmp_path, pages=3)
    descs = build_per_page_descriptors(pdf)
    assert len(descs) == 3
    for i, d in enumerate(descs):
        assert d.page_index == i
        assert d.drawing_number == "S20-0002"
        assert d.visual_hash != ""
        assert d.full_text_hash != ""


def test_build_descriptors_handles_missing_pdf(tmp_path: Path) -> None:
    from src.services.comparison.page_descriptor import build_per_page_descriptors
    descs = build_per_page_descriptors(tmp_path / "does_not_exist.pdf")
    assert descs == []


def test_visual_hash_is_deterministic(tmp_path: Path) -> None:
    """Same page → same hash."""

    from src.services.comparison.page_descriptor import build_per_page_descriptor

    pdf = _make_synthetic_pdf(tmp_path, pages=1)
    d1 = build_per_page_descriptor(pdf, 0)
    d2 = build_per_page_descriptor(pdf, 0)
    assert d1 is not None and d2 is not None
    assert d1.visual_hash == d2.visual_hash


def test_visual_hash_differs_between_different_pages(tmp_path: Path) -> None:
    """Pages with different content should have different hashes."""

    import fitz
    doc = fitz.open()
    page1 = doc.new_page(width=595, height=842)
    page1.insert_text((50, 100), "Page A content", fontsize=14)
    page2 = doc.new_page(width=595, height=842)
    # Cover the page in dense text so the visual hash diverges
    for y in range(50, 800, 12):
        page2.insert_text((50, y), "█" * 80, fontsize=10)
    pdf_path = tmp_path / "two_diff_pages.pdf"
    doc.save(str(pdf_path))
    doc.close()

    from src.services.comparison.page_descriptor import build_per_page_descriptors
    descs = build_per_page_descriptors(pdf_path)
    assert len(descs) == 2
    assert descs[0].visual_hash != descs[1].visual_hash


def test_descriptor_to_dict_truncates_long_text(tmp_path: Path) -> None:
    desc = PerPageDescriptor(
        pdf_path="x.pdf", page_index=0, page_size=(595.0, 842.0),
        title_text="A" * 1000,
        title_text_normalised="a" * 1000,
    )
    d = desc.to_dict()
    assert len(d["title_text"]) <= 500
    assert len(d["title_text_normalised"]) <= 500


# ---------------------------------------------------------------------------
# Direct tests for extract_title_block_text (Phase H1 follow-up — coverage
# gap noted in code review). Drives the function with crafted PyMuPDF Page
# objects so we exercise the bottom-right region clip + fallback paths
# without going through the full builder.
# ---------------------------------------------------------------------------


def test_extract_title_block_picks_bottom_right_text(tmp_path: Path) -> None:
    """Text drawn in the bottom-right 25 % of the page is returned with
    used_fallback=False.

    Uses ASCII-only text — PyMuPDF's default Helvetica font does not
    embed Korean glyphs, and inserting Hangul without loading a CJK
    font causes the chars to render as ``?`` placeholders, which would
    make the ``"in text"`` assertion brittle. The key invariant we
    verify is the bottom-right CLIP region selection, not Korean
    rendering — that's tested via real PDFs in the E2E suite.
    """

    import fitz
    from src.services.comparison.page_descriptor import extract_title_block_text

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Bottom-right region starts at x>=446, y>=632; place text inside.
    page.insert_text((460, 720), "S20-0002 - Floor 3 Structural Plan", fontsize=10)
    # Also add some unrelated text in the top-left (should NOT be picked).
    page.insert_text((50, 50), "TOP-LEFT noise text not in title block", fontsize=12)

    text, used_fallback = extract_title_block_text(page)
    # The title text contains the drawing code at minimum. Some words may
    # be truncated when the rendered text extends past the clip's right
    # edge — that's acceptable for matching purposes (the drawing number
    # is the most important signal).
    assert "S20-0002" in text
    assert "Structural" in text
    assert "TOP-LEFT" not in text  # bottom-right clip excluded the top-left
    assert used_fallback is False
    doc.close()


def test_extract_title_block_falls_back_when_region_empty(tmp_path: Path) -> None:
    """If the bottom-right is empty AND there's text elsewhere, falls back
    to the full page text and sets used_fallback=True."""

    import fitz
    from src.services.comparison.page_descriptor import extract_title_block_text

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Only top-left content
    page.insert_text((50, 50), "S30-9999 — outside the title-block region", fontsize=10)

    text, used_fallback = extract_title_block_text(page)
    assert used_fallback is True
    assert "S30-9999" in text  # full-page text recovered the content
    doc.close()


def test_extract_title_block_returns_empty_when_page_empty(tmp_path: Path) -> None:
    """A page with NO text at all returns empty + used_fallback=True (the
    fallback fires, but it too is empty)."""

    import fitz
    from src.services.comparison.page_descriptor import extract_title_block_text

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # No text inserted

    text, used_fallback = extract_title_block_text(page)
    assert text == ""
    assert used_fallback is True
    doc.close()


def test_extract_title_block_below_min_chars_triggers_fallback(tmp_path: Path) -> None:
    """When the title region has < TITLE_BLOCK_MIN_CHARS, fallback fires."""

    import fitz
    from src.services.comparison.page_descriptor import (
        TITLE_BLOCK_MIN_CHARS,
        extract_title_block_text,
    )

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Tiny text in title region (< MIN_CHARS) + larger text elsewhere
    page.insert_text((460, 720), "x", fontsize=10)  # 1 char only
    page.insert_text((50, 50), "fallback content with enough characters here", fontsize=12)

    text, used_fallback = extract_title_block_text(page)
    assert used_fallback is True
    assert "fallback" in text
    doc.close()


# ---------------------------------------------------------------------------
# Direct tests for compute_visual_hash (Phase H1 follow-up — coverage
# gap noted in code review). Verifies hex format + size + reproducibility
# without relying on the full builder.
# ---------------------------------------------------------------------------


def test_compute_visual_hash_returns_16_hex_chars(tmp_path: Path) -> None:
    """8x8 average-hash signature → 64 bits → 16 hex characters."""

    import fitz
    from src.services.comparison.page_descriptor import compute_visual_hash

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((100, 100), "Page content for hashing", fontsize=14)

    h = compute_visual_hash(page)
    assert len(h) == 16
    # Each char must be a valid hex digit
    int(h, 16)  # raises if not valid hex
    doc.close()


def test_compute_visual_hash_deterministic(tmp_path: Path) -> None:
    """Same page → same hash on repeat invocations."""

    import fitz
    from src.services.comparison.page_descriptor import compute_visual_hash

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((100, 100), "Stable content", fontsize=14)

    h1 = compute_visual_hash(page)
    h2 = compute_visual_hash(page)
    assert h1 == h2
    doc.close()


def test_compute_visual_hash_blank_page_yields_uniform_hash(tmp_path: Path) -> None:
    """A completely blank page should produce a stable hex value."""

    import fitz
    from src.services.comparison.page_descriptor import compute_visual_hash

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # No content

    h = compute_visual_hash(page)
    assert len(h) == 16
    doc.close()


def test_compute_visual_hash_diverges_for_visually_different_pages(tmp_path: Path) -> None:
    """Pages with markedly different visual content → different hashes.

    Saves the doc to a real PDF first then re-opens it — matches the
    real-world flow of ``build_per_page_descriptors`` and avoids the
    PyMuPDF stale-page-reference quirk that bites in-memory ``fitz.open()``
    tests when ``new_page()`` invalidates earlier page handles.
    """

    import fitz
    from src.services.comparison.page_descriptor import compute_visual_hash

    pdf_path = tmp_path / "diverge.pdf"
    doc = fitz.open()

    # Page 1: sparse — just a small label
    page1 = doc.new_page(width=595, height=842)
    page1.insert_text((50, 100), "Sparse", fontsize=14)

    # Page 2: dense — many lines of text covering most of the page
    page2 = doc.new_page(width=595, height=842)
    for y in range(50, 800, 12):
        page2.insert_text((50, y), "X" * 80, fontsize=10)

    doc.save(str(pdf_path))
    doc.close()

    # Re-open from disk so page handles are fresh (matches real-world).
    doc2 = fitz.open(str(pdf_path))
    h1 = compute_visual_hash(doc2[0])
    h2 = compute_visual_hash(doc2[1])
    doc2.close()

    # Both must produce a real 16-hex signature (no silent empty fallback)
    assert len(h1) == 16
    assert len(h2) == 16
    assert h1 != h2
