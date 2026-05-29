# -*- coding: utf-8 -*-
"""ADR-003 H2 — align a DWG drawing frame to a rendered PDF page.

The PDF-first hybrid viewer detects differences in CAD entity space
(cad_wcs_mm) but displays them over a rendered PDF page in image pixels.
This module computes the alignment between the DWG drawing frame (도곽)
and the PDF page pixel canvas, and maps CAD-world bboxes (DWG diff
change-zones) into image-pixel space using the H1 transform.

Alignment quality (ADR-003 §8-6, plot-setting drift):

* ``exact``         — DWG frame aspect matches the PDF page aspect within
                      tolerance, i.e. they almost certainly came from the
                      same plot. Overlays land precisely.
* ``estimated``     — aspect mismatch beyond tolerance. The fit still maps
                      points, but the DWG and PDF likely used different
                      plot settings, so overlays may drift. The viewer
                      should surface this (S1 failure badge).
* ``relative_only`` — degenerate frame (collapsed bbox). No reliable
                      mapping; the viewer falls back to relative pins.

Pure Python — depends only on ``transform.py``. No Qt / ezdxf / I/O, so
it imports in microseconds and is safe in worker subprocesses + tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .transform import (
    Bbox,
    PixelSize,
    TransformQuality,
    cad_world_to_image_pixels_bbox,
    normalise_bbox,
)

#: Relative aspect-ratio difference below which alignment is "exact".
#: 2% tolerates rounding + thin plot margins while still catching a
#: genuinely different plot (e.g. A3 frame plotted onto an A1 page).
DEFAULT_ASPECT_TOLERANCE: float = 0.02


@dataclass(frozen=True)
class CadPdfAlignment:
    """Result of aligning a DWG frame to a PDF page pixel canvas.

    Attributes:
        cad_frame_bbox: the DWG drawing-frame bbox in cad_wcs_mm.
        pdf_pixel_size: ``(w, h)`` of the rendered PDF page in pixels.
        quality: ``exact`` / ``estimated`` / ``relative_only``.
        cad_aspect: width/height of the DWG frame.
        pdf_aspect: width/height of the PDF page.
        aspect_mismatch: relative difference between the two aspects.
        padding_px: symmetric pixel margin used by the fit.
    """

    cad_frame_bbox: Bbox
    pdf_pixel_size: PixelSize
    quality: TransformQuality
    cad_aspect: float
    pdf_aspect: float
    aspect_mismatch: float
    padding_px: int = 0

    @property
    def is_usable(self) -> bool:
        """True when overlays can be mapped (not relative_only)."""

        return self.quality != "relative_only"

    def map_cad_bbox(self, bbox: object) -> Optional[Bbox]:
        """Map one CAD-world bbox into image-pixel space (H1).

        Returns ``None`` for unusable alignment (relative_only) or
        unparseable input, so callers fall back to relative pins.
        """

        if not self.is_usable:
            return None
        return cad_world_to_image_pixels_bbox(
            bbox,
            cad_frame_bbox=self.cad_frame_bbox,
            pixel_size=self.pdf_pixel_size,
            padding_px=self.padding_px,
        )

    def map_cad_bboxes(self, bboxes: object) -> List[Optional[Bbox]]:
        """Map a sequence of CAD-world bboxes; preserves order.

        Each element is the mapped pixel bbox or ``None`` (unparseable /
        unusable). Callers typically zip this with the source change
        records and skip the ``None`` entries.
        """

        if not isinstance(bboxes, (list, tuple)):
            return []
        return [self.map_cad_bbox(b) for b in bboxes]

    def to_dict(self) -> dict[str, object]:
        return {
            "cad_frame_bbox": list(self.cad_frame_bbox),
            "pdf_pixel_size": list(self.pdf_pixel_size),
            "quality": self.quality,
            "cad_aspect": self.cad_aspect,
            "pdf_aspect": self.pdf_aspect,
            "aspect_mismatch": self.aspect_mismatch,
            "padding_px": self.padding_px,
        }


def align_cad_to_pdf(
    cad_frame_bbox: object,
    pdf_pixel_size: PixelSize,
    *,
    padding_px: int = 0,
    aspect_tolerance: float = DEFAULT_ASPECT_TOLERANCE,
) -> CadPdfAlignment:
    """Compute the alignment between a DWG frame and a PDF page.

    Args:
        cad_frame_bbox: DWG drawing-frame bbox in cad_wcs_mm (dict or
            4-list). Typically the DWG modelspace/paperspace extents or a
            detected 도곽.
        pdf_pixel_size: ``(w, h)`` of the rendered PDF page in pixels.
        padding_px: symmetric margin used by the underlying fit (match the
            PDF render margin if any).
        aspect_tolerance: relative aspect difference below which the
            alignment is graded ``exact``.

    Returns:
        A :class:`CadPdfAlignment`. ``relative_only`` when the frame or
        page is degenerate; ``exact`` when aspects match within tolerance;
        ``estimated`` otherwise.
    """

    frame = normalise_bbox(cad_frame_bbox)
    pw, ph = pdf_pixel_size if pdf_pixel_size else (0, 0)

    # Degenerate frame or page -> no reliable mapping.
    if frame is None or pw <= 0 or ph <= 0:
        return CadPdfAlignment(
            cad_frame_bbox=frame or (0.0, 0.0, 0.0, 0.0),
            pdf_pixel_size=(int(pw), int(ph)),
            quality="relative_only",
            cad_aspect=0.0,
            pdf_aspect=0.0,
            aspect_mismatch=float("inf"),
            padding_px=padding_px,
        )

    fx0, fy0, fx1, fy1 = frame
    cad_w = fx1 - fx0
    cad_h = fy1 - fy0
    if cad_w <= 0 or cad_h <= 0:
        return CadPdfAlignment(
            cad_frame_bbox=frame,
            pdf_pixel_size=(int(pw), int(ph)),
            quality="relative_only",
            cad_aspect=0.0,
            pdf_aspect=float(pw) / float(ph),
            aspect_mismatch=float("inf"),
            padding_px=padding_px,
        )

    cad_aspect = cad_w / cad_h
    pdf_aspect = float(pw) / float(ph)
    aspect_mismatch = abs(cad_aspect - pdf_aspect) / pdf_aspect

    quality: TransformQuality = (
        "exact" if aspect_mismatch <= aspect_tolerance else "estimated"
    )

    return CadPdfAlignment(
        cad_frame_bbox=frame,
        pdf_pixel_size=(int(pw), int(ph)),
        quality=quality,
        cad_aspect=cad_aspect,
        pdf_aspect=pdf_aspect,
        aspect_mismatch=aspect_mismatch,
        padding_px=padding_px,
    )


__all__ = [
    "CadPdfAlignment",
    "align_cad_to_pdf",
    "DEFAULT_ASPECT_TOLERANCE",
]
