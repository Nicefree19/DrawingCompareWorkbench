# -*- coding: utf-8 -*-
"""ADR-003 H5b — pair DWG drawings against PDF pages.

Orchestrates the existing ``page_matcher`` to pair a set of DWG drawings
(side A, via ``build_dwg_page_descriptor`` from H5a) against a set of PDF
pages (side B, via ``build_per_page_descriptors`` + OCR for image-only
PDFs). Handles BOTH single-page PDFs and multi-page booklets — the
matcher solves the N×M assignment by drawing_number + frame size + title.

Converts the matcher's index-based ``PageMatchCandidate`` into a
source-aware ``CadPdfPair`` (recovered DWG/PDF paths + drawing number),
so downstream H5c can build a per-pair ``CadPdfAlignment`` and H5d can
emit overlays.

Pure: delegates all matching to ``match_pdf_pages`` (no I/O). The
descriptor lists are built by the caller (H5d wiring), which also runs
OCR for image-only PDFs before building the PDF descriptors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .cad_pdf_alignment import CadPdfAlignment, align_cad_to_pdf
from .page_descriptor import PerPageDescriptor
from .page_matcher import (
    PageMatchOptions,
    PageMatchStatus,
    match_pdf_pages,
)
from .transform import Bbox, PixelSize


@dataclass(frozen=True)
class CadPdfPair:
    """One matched DWG-drawing <-> PDF-page pairing."""

    dwg_source: str          # DWG identifier (descriptor.pdf_path)
    dwg_index: int           # position in the DWG descriptor list
    pdf_source: str          # PDF identifier
    pdf_page_index: int      # 0-based page within the PDF
    drawing_number: str      # recovered code (DWG-side preferred)
    status: str              # "auto_confirmed" | "review_required"
    score: float

    def to_dict(self) -> dict[str, object]:
        return {
            "dwg_source": self.dwg_source,
            "dwg_index": self.dwg_index,
            "pdf_source": self.pdf_source,
            "pdf_page_index": self.pdf_page_index,
            "drawing_number": self.drawing_number,
            "status": self.status,
            "score": round(self.score, 4),
        }


@dataclass(frozen=True)
class CadPdfPairingResult:
    """Outcome of pairing DWG drawings to PDF pages."""

    pairs: List[CadPdfPair] = field(default_factory=list)
    unmatched_dwg: List[Tuple[str, int]] = field(default_factory=list)
    unmatched_pdf: List[Tuple[str, int]] = field(default_factory=list)

    @property
    def auto_count(self) -> int:
        return sum(1 for p in self.pairs if p.status == "auto_confirmed")

    @property
    def review_count(self) -> int:
        return sum(1 for p in self.pairs if p.status == "review_required")

    def to_dict(self) -> dict[str, object]:
        return {
            "pairs": [p.to_dict() for p in self.pairs],
            "unmatched_dwg": [list(t) for t in self.unmatched_dwg],
            "unmatched_pdf": [list(t) for t in self.unmatched_pdf],
            "auto_count": self.auto_count,
            "review_count": self.review_count,
        }


def pair_dwg_to_pdf(
    dwg_descriptors: Sequence[PerPageDescriptor],
    pdf_descriptors: Sequence[PerPageDescriptor],
    *,
    options: Optional[PageMatchOptions] = None,
) -> CadPdfPairingResult:
    """Pair DWG drawings (A) against PDF pages (B) by drawing-number/frame.

    ADR-003 H5b. Delegates the N×M assignment to
    :func:`page_matcher.match_pdf_pages` (single-page and multi-sheet both
    handled), then converts each matched/unmatched candidate into a
    source-aware result.

    Args:
        dwg_descriptors: DWG-side descriptors (H5a build_dwg_page_descriptor).
        pdf_descriptors: PDF-side page descriptors (build_per_page_descriptors,
            with OCR-recovered drawing numbers for image PDFs).
        options: optional matcher thresholds.

    Returns:
        A :class:`CadPdfPairingResult` with matched pairs (auto + review),
        plus the DWG drawings and PDF pages that found no partner.
    """

    candidates = match_pdf_pages(dwg_descriptors, pdf_descriptors, options=options)

    pairs: List[CadPdfPair] = []
    unmatched_dwg: List[Tuple[str, int]] = []
    unmatched_pdf: List[Tuple[str, int]] = []

    for cand in candidates:
        if cand.is_matched:
            dwg = dwg_descriptors[cand.page_a_index]
            pdf = pdf_descriptors[cand.page_b_index]
            number = dwg.drawing_number or pdf.drawing_number
            pairs.append(
                CadPdfPair(
                    dwg_source=dwg.pdf_path,
                    dwg_index=cand.page_a_index,
                    pdf_source=pdf.pdf_path,
                    pdf_page_index=pdf.page_index,
                    drawing_number=number,
                    status=cand.status.value,
                    score=cand.score,
                )
            )
        elif cand.status == PageMatchStatus.UNMATCHED_A:
            dwg = dwg_descriptors[cand.page_a_index]
            unmatched_dwg.append((dwg.pdf_path, cand.page_a_index))
        elif cand.status == PageMatchStatus.UNMATCHED_B:
            pdf = pdf_descriptors[cand.page_b_index]
            unmatched_pdf.append((pdf.pdf_path, pdf.page_index))

    return CadPdfPairingResult(
        pairs=pairs,
        unmatched_dwg=unmatched_dwg,
        unmatched_pdf=unmatched_pdf,
    )


@dataclass(frozen=True)
class PairedAlignment:
    """A matched pair together with its DWG-frame -> PDF-page alignment."""

    pair: CadPdfPair
    alignment: CadPdfAlignment


def build_pair_alignments(
    pairs: Sequence[CadPdfPair],
    *,
    cad_frames: dict,
    pdf_pixel_sizes: dict,
    padding_px: int = 0,
) -> List[PairedAlignment]:
    """Build a CadPdfAlignment for each matched pair (ADR-003 H5c).

    For every pair, look up the DWG drawing-frame bbox (cad_wcs_mm) and
    the PDF page pixel size, then delegate to
    :func:`cad_pdf_alignment.align_cad_to_pdf` (H2). The resulting
    alignment maps DWG diff bboxes onto the matched PDF page; its
    ``quality`` (exact / estimated / relative_only) flows to the viewer
    so a frame/page aspect mismatch is surfaced (S1 badge).

    Args:
        pairs: matched pairs from :func:`pair_dwg_to_pdf`.
        cad_frames: ``{dwg_source: (x0, y0, x1, y1)}`` DWG frame extents.
        pdf_pixel_sizes: ``{(pdf_source, pdf_page_index): (w, h)}`` rendered
            PDF page pixel sizes.
        padding_px: symmetric margin matching the PDF render.

    Returns:
        One :class:`PairedAlignment` per pair that has both a frame and a
        page size. Pairs missing either are skipped (the caller can log /
        fall back to relative pins for them).
    """

    out: List[PairedAlignment] = []
    for pair in pairs:
        frame: Optional[Bbox] = cad_frames.get(pair.dwg_source)
        size: Optional[PixelSize] = pdf_pixel_sizes.get(
            (pair.pdf_source, pair.pdf_page_index)
        )
        if frame is None or size is None:
            continue
        alignment = align_cad_to_pdf(frame, size, padding_px=padding_px)
        out.append(PairedAlignment(pair=pair, alignment=alignment))
    return out


__all__ = [
    "CadPdfPair",
    "CadPdfPairingResult",
    "PairedAlignment",
    "pair_dwg_to_pdf",
    "build_pair_alignments",
]
