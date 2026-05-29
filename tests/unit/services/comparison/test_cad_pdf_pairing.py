# -*- coding: utf-8 -*-
"""Unit tests for ADR-003 H5b — pair_dwg_to_pdf orchestrator."""

from __future__ import annotations

from src.services.comparison.page_descriptor import (
    PerPageDescriptor,
    build_dwg_page_descriptor,
)
from src.services.comparison.cad_pdf_pairing import (
    CadPdfPair,
    CadPdfPairingResult,
    PairedAlignment,
    build_pair_alignments,
    pair_dwg_to_pdf,
)


def _pdf_desc(path: str, page: int, number: str, title: str = "") -> PerPageDescriptor:
    """A PDF-side descriptor as build_per_page_descriptors (+OCR) would yield."""
    return PerPageDescriptor(
        pdf_path=path,
        page_index=page,
        page_size=(420.0, 297.0),
        drawing_number=number,
        title_text=title,
        title_text_normalised=title,
    )


def _dwg_desc(path: str, number: str, title: str = "") -> PerPageDescriptor:
    return build_dwg_page_descriptor(
        path,
        texts=[number, title] if title else [number],
        frame_bbox=(0, 0, 420, 297),
        title_texts=[title] if title else None,
    )


def test_single_pdf_pairs_with_dwg() -> None:
    """H5b: 1 DWG + 1 single-page PDF with same number -> 1 pair."""
    dwg = [_dwg_desc("3PG1.dwg", "S20-0002", "3층 골조")]
    pdf = [_pdf_desc("3PG1.pdf", 0, "S20-0002", "3층 골조")]
    result = pair_dwg_to_pdf(dwg, pdf)
    assert len(result.pairs) == 1
    p = result.pairs[0]
    assert p.dwg_source == "3PG1.dwg"
    assert p.pdf_source == "3PG1.pdf"
    assert p.pdf_page_index == 0
    assert p.drawing_number == "S20-0002"
    assert not result.unmatched_dwg
    assert not result.unmatched_pdf


def test_multi_sheet_booklet_pairs_pages() -> None:
    """H5b: 2 DWG vs a 3-page PDF booklet -> 2 pairs + 1 unmatched PDF page."""
    dwg = [
        _dwg_desc("a.dwg", "S20-0001", "1층"),
        _dwg_desc("b.dwg", "S20-0002", "2층"),
    ]
    pdf = [
        _pdf_desc("book.pdf", 0, "S20-0001", "1층"),
        _pdf_desc("book.pdf", 1, "S20-0002", "2층"),
        _pdf_desc("book.pdf", 2, "S20-0099", "여분 시트"),
    ]
    result = pair_dwg_to_pdf(dwg, pdf)
    assert len(result.pairs) == 2
    numbers = {p.drawing_number for p in result.pairs}
    assert numbers == {"S20-0001", "S20-0002"}
    # the extra PDF page has no DWG partner
    assert len(result.unmatched_pdf) == 1
    assert result.unmatched_pdf[0] == ("book.pdf", 2)


def test_pdf_page_index_recovered_for_booklet() -> None:
    """H5b: the PDF page index is preserved through pairing."""
    dwg = [_dwg_desc("b.dwg", "S20-0002", "2층 평면")]
    pdf = [
        _pdf_desc("book.pdf", 0, "S20-0001", "1층"),
        _pdf_desc("book.pdf", 1, "S20-0002", "2층 평면"),
    ]
    result = pair_dwg_to_pdf(dwg, pdf)
    assert len(result.pairs) == 1
    assert result.pairs[0].pdf_page_index == 1  # matched the 2nd page
    assert ("book.pdf", 0) in result.unmatched_pdf


def test_dwg_without_match_is_unmatched() -> None:
    """H5b: a DWG whose number isn't in the PDF set -> unmatched_dwg."""
    dwg = [_dwg_desc("orphan.dwg", "S99-1234", "고아 도면")]
    pdf = [_pdf_desc("other.pdf", 0, "S20-0002", "다른 도면 제목 전혀 무관")]
    result = pair_dwg_to_pdf(dwg, pdf)
    # different numbers + titles -> below review threshold -> unmatched
    assert len(result.pairs) == 0
    assert ("orphan.dwg", 0) in result.unmatched_dwg


def test_status_and_score_carried() -> None:
    """H5b: pair status is one of the matched enum string values + score."""
    dwg = [_dwg_desc("a.dwg", "S20-0002", "3층 골조")]
    pdf = [_pdf_desc("a.pdf", 0, "S20-0002", "3층 골조")]
    result = pair_dwg_to_pdf(dwg, pdf)
    assert len(result.pairs) == 1
    assert result.pairs[0].status in {"auto_confirmed", "review_required"}
    assert 0.0 <= result.pairs[0].score <= 1.0
    assert result.auto_count + result.review_count == 1


def test_empty_inputs() -> None:
    """H5b: empty DWG/PDF lists -> empty result, no crash."""
    assert pair_dwg_to_pdf([], []).pairs == []
    r = pair_dwg_to_pdf([_dwg_desc("a.dwg", "S20-0002")], [])
    assert r.pairs == []
    assert r.unmatched_dwg == [("a.dwg", 0)]


def test_to_dict_serialisable() -> None:
    """H5b: result serialises for telemetry/manifest."""
    dwg = [_dwg_desc("a.dwg", "S20-0002", "3층")]
    pdf = [_pdf_desc("a.pdf", 0, "S20-0002", "3층")]
    d = pair_dwg_to_pdf(dwg, pdf).to_dict()
    import json

    json.dumps(d, ensure_ascii=False)  # must not raise
    assert "pairs" in d and "auto_count" in d


# ---------------------------------------------------------------------------
# ADR-003 H5c — build_pair_alignments
# ---------------------------------------------------------------------------


def test_build_pair_alignments_makes_alignment_per_pair() -> None:
    """H5c: each matched pair gets a CadPdfAlignment from frame + page size."""
    dwg = [_dwg_desc("a.dwg", "S20-0002", "3층 골조")]
    pdf = [_pdf_desc("a.pdf", 0, "S20-0002", "3층 골조")]
    result = pair_dwg_to_pdf(dwg, pdf)
    aligned = build_pair_alignments(
        result.pairs,
        cad_frames={"a.dwg": (0.0, 0.0, 420.0, 297.0)},
        pdf_pixel_sizes={("a.pdf", 0): (840, 594)},  # aspect-matched
    )
    assert len(aligned) == 1
    assert isinstance(aligned[0], PairedAlignment)
    assert aligned[0].pair.drawing_number == "S20-0002"
    assert aligned[0].alignment.quality == "exact"  # matched aspect
    # the alignment maps a CAD bbox onto the page
    px = aligned[0].alignment.map_cad_bbox((210.0, 148.5, 210.0, 148.5))
    assert px is not None


def test_build_pair_alignments_skips_missing_frame_or_size() -> None:
    """H5c: a pair with no frame or no page size is skipped."""
    dwg = [_dwg_desc("a.dwg", "S20-0002", "3층")]
    pdf = [_pdf_desc("a.pdf", 0, "S20-0002", "3층")]
    result = pair_dwg_to_pdf(dwg, pdf)
    # no frame for a.dwg
    assert build_pair_alignments(
        result.pairs, cad_frames={}, pdf_pixel_sizes={("a.pdf", 0): (840, 594)}
    ) == []
    # no size for the page
    assert build_pair_alignments(
        result.pairs, cad_frames={"a.dwg": (0, 0, 420, 297)}, pdf_pixel_sizes={}
    ) == []


def test_build_pair_alignments_flags_aspect_mismatch_estimated() -> None:
    """H5c: frame/page aspect mismatch -> alignment quality 'estimated'."""
    dwg = [_dwg_desc("a.dwg", "S20-0002", "3층 골조")]
    pdf = [_pdf_desc("a.pdf", 0, "S20-0002", "3층 골조")]
    result = pair_dwg_to_pdf(dwg, pdf)
    aligned = build_pair_alignments(
        result.pairs,
        cad_frames={"a.dwg": (0.0, 0.0, 420.0, 297.0)},  # 1.41 aspect
        pdf_pixel_sizes={("a.pdf", 0): (1754, 2481)},      # portrait 0.71
    )
    assert len(aligned) == 1
    assert aligned[0].alignment.quality == "estimated"
