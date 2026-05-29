# -*- coding: utf-8 -*-
"""Unit tests for ADR-003 H5a — build_dwg_page_descriptor + matcher compat.

Pure (no fitz/ezdxf), so kept in a separate file from
test_page_descriptor.py (which importorskips fitz for its PDF tests).
"""

from __future__ import annotations

from src.services.comparison.page_descriptor import (
    PerPageDescriptor,
    build_dwg_page_descriptor,
)
from src.services.comparison.page_matcher import (
    PageMatchStatus,
    match_pdf_pages,
)


def test_extracts_drawing_number_from_texts() -> None:
    d = build_dwg_page_descriptor(
        "a.dwg", texts=["일반 주기", "S20-0002", "3층"], frame_bbox=(0, 0, 420, 297)
    )
    assert d.drawing_number == "S20-0002"


def test_page_size_from_frame_bbox() -> None:
    """H5a: page_size is the frame width/height (offset frame supported)."""
    d = build_dwg_page_descriptor(
        "a.dwg", texts=[], frame_bbox=(10.0, 20.0, 430.0, 317.0)
    )
    assert d.page_size == (420.0, 297.0)


def test_visual_and_text_hash_left_empty_neutral() -> None:
    """H5a: visual/full_text hashes blank -> matcher treats as neutral."""
    d = build_dwg_page_descriptor(
        "a.dwg", texts=["S20-0002"], frame_bbox=(0, 0, 420, 297)
    )
    assert d.visual_hash == ""
    assert d.full_text_hash == ""


def test_source_path_in_pdf_path_field() -> None:
    """H5a: source DWG path stored in pdf_path (identifier only)."""
    d = build_dwg_page_descriptor("C:/x/3PG1.dwg", texts=[], frame_bbox=(0, 0, 1, 1))
    assert d.pdf_path == "C:/x/3PG1.dwg"


def test_empty_texts_yield_no_drawing_number() -> None:
    d = build_dwg_page_descriptor("a.dwg", texts=[], frame_bbox=(0, 0, 420, 297))
    assert d.drawing_number == ""


def test_title_texts_drive_title_not_number() -> None:
    """H5a: title_texts feed title_text; drawing_number still from texts."""
    d = build_dwg_page_descriptor(
        "a.dwg",
        texts=["S20-0002 본문"],
        frame_bbox=(0, 0, 1, 1),
        title_texts=["제목블록"],
    )
    assert d.drawing_number == "S20-0002"   # from joined texts
    assert d.title_text == "제목블록"        # from title_texts


def test_returns_per_page_descriptor_type() -> None:
    d = build_dwg_page_descriptor(
        "a.dwg", texts=["S20-0002"], frame_bbox=(0, 0, 420, 297)
    )
    assert isinstance(d, PerPageDescriptor)


# ---------------------------------------------------------------------------
# Matcher compatibility — the H5a core: a DWG descriptor must pair with a
# PDF descriptor through the existing page_matcher (no path re-open).
# ---------------------------------------------------------------------------


def test_dwg_descriptor_pairs_with_pdf_by_drawing_number() -> None:
    """H5a: same drawing number -> DWG<->PDF page is matched.

    Proves a build_dwg_page_descriptor output is consumable by the
    existing match_pdf_pages without modification.
    """
    dwg = build_dwg_page_descriptor(
        "3PG1.dwg", texts=["S20-0002", "3층 골조 평면도"], frame_bbox=(0, 0, 420, 297)
    )
    pdf = PerPageDescriptor(
        pdf_path="3PG1.pdf",
        page_index=0,
        page_size=(420.0, 297.0),
        drawing_number="S20-0002",
        title_text="3층 골조 평면도",
        title_text_normalised="3층 골조 평면도",
    )
    cands = match_pdf_pages([dwg], [pdf])
    pair = [c for c in cands if c.page_a_index == 0 and c.page_b_index == 0]
    assert len(pair) == 1
    assert pair[0].is_matched  # same drawing number -> matched


def test_dwg_pdf_different_numbers_not_auto_confirmed() -> None:
    """H5a: different drawing numbers must not auto-confirm (cap 0.59)."""
    dwg = build_dwg_page_descriptor(
        "a.dwg", texts=["S20-0002"], frame_bbox=(0, 0, 420, 297)
    )
    pdf = PerPageDescriptor(
        pdf_path="b.pdf",
        page_index=0,
        page_size=(420.0, 297.0),
        drawing_number="S30-9999",
        title_text="완전히 다른 도면 제목",
        title_text_normalised="완전히 다른 도면 제목",
    )
    cands = match_pdf_pages([dwg], [pdf])
    auto = [c for c in cands if c.status == PageMatchStatus.AUTO_CONFIRMED]
    assert len(auto) == 0  # mismatched drawing number -> not auto-confirmed
