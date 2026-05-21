"""Subprocess entrypoint for timeboxed viewer background rendering."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, Sequence

from .review_project import _ensure_preview_dxf, _render_dxf_to_png

logger = logging.getLogger(__name__)

CAD_EXTENSIONS = {".dwg", ".dxf"}
PDF_EXTENSIONS = {".pdf"}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    # Sanitize lone surrogate codepoints — Korean filenames from Windows can
    # carry CP949↔UTF-16 surrogates that crash utf-8 encoding here. The
    # subprocess returning a malformed JSON also surfaces in the GUI as
    # "선택 구역 렌더 실패 - 상대 위치 표시를 유지합니다" with the
    # underlying "'utf-8' codec can't encode character ... surrogates not
    # allowed" error from the workbench manifest write path.
    from .safe_unicode import safe_unicode

    tmp.write_text(
        json.dumps(safe_unicode(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def render_pair_backgrounds(
    *,
    pair_id: str,
    source_a: Optional[Path],
    source_b: Optional[Path],
    image_dir: Path,
    dxf_cache_dir: Path,
    dpi: int,
    max_edge_px: int,
    page_a: int = 0,
    page_b: int = 0,
) -> dict[str, Any]:
    """Render the before/after backgrounds for one viewer pair (subprocess copy).

    Phase H integration — ``page_a``/``page_b`` are the per-side PDF
    page indices. Defaults of 0 keep DXF/single-page PDF working.
    """

    warnings: list[str] = []
    if not source_a or not source_b:
        return {
            "before_image": "",
            "after_image": "",
            "before_transform": None,
            "after_transform": None,
            "render_status": "render_failed",
            "warnings": ["source paths are missing"],
        }
    if source_a.suffix.lower() in PDF_EXTENSIONS and source_b.suffix.lower() in PDF_EXTENSIONS:
        try:
            safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in pair_id)[:120] or "pair"
            before_image = image_dir / f"{safe}_before.png"
            after_image = image_dir / f"{safe}_after.png"
            before_transform = _render_pdf_to_png(
                source_a, before_image,
                dpi=dpi, max_edge_px=max_edge_px, page_index=int(page_a),
            )
            after_transform = _render_pdf_to_png(
                source_b, after_image,
                dpi=dpi, max_edge_px=max_edge_px, page_index=int(page_b),
            )
            return {
                "before_image": str(before_image),
                "after_image": str(after_image),
                "before_transform": before_transform,
                "after_transform": after_transform,
                "render_status": "rendered",
                "warnings": warnings,
            }
        except Exception as exc:
            logger.warning("Failed to render PDF viewer background for %s: %s", pair_id, exc)
            return {
                "before_image": "",
                "after_image": "",
                "before_transform": None,
                "after_transform": None,
                "render_status": "render_failed",
                "warnings": [f"PDF viewer render failed: {exc}"],
            }

    if source_a.suffix.lower() not in CAD_EXTENSIONS or source_b.suffix.lower() not in CAD_EXTENSIONS:
        return {
            "before_image": "",
            "after_image": "",
            "before_transform": None,
            "after_transform": None,
            "render_status": "render_failed",
            "warnings": ["source pair is not CAD; skipped PNG rendering"],
        }
    try:
        before_dxf = _ensure_preview_dxf(source_a, dxf_cache_dir)
        after_dxf = _ensure_preview_dxf(source_b, dxf_cache_dir)
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in pair_id)[:120] or "pair"
        before_image = image_dir / f"{safe}_before.png"
        after_image = image_dir / f"{safe}_after.png"
        before_transform = _render_dxf_to_png(before_dxf, before_image, dpi=dpi, max_edge_px=max_edge_px)
        after_transform = _render_dxf_to_png(after_dxf, after_image, dpi=dpi, max_edge_px=max_edge_px)
        return {
            "before_image": str(before_image),
            "after_image": str(after_image),
            "before_transform": before_transform,
            "after_transform": after_transform,
            "render_status": "rendered",
            "warnings": warnings,
        }
    except Exception as exc:
        logger.warning("Failed to render viewer background for %s: %s", pair_id, exc)
        return {
            "before_image": "",
            "after_image": "",
            "before_transform": None,
            "after_transform": None,
            "render_status": "render_failed",
            "warnings": [f"viewer render failed: {exc}"],
        }


def _render_pdf_to_png(
    pdf_path: Path,
    output_path: Path,
    *,
    dpi: int,
    max_edge_px: int,
    page_index: int = 0,
) -> dict[str, Any]:
    """Subprocess copy of the per-page renderer.

    Phase H integration — ``page_index`` lets the matched page be
    rendered. Out-of-range indices clamp to 0 with a warning so a bad
    Phase H emission doesn't crash the comparison run.
    """

    import fitz  # type: ignore

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    try:
        if len(doc) == 0:
            raise ValueError(f"PDF has no pages: {pdf_path}")
        safe_index = int(page_index)
        if safe_index < 0 or safe_index >= len(doc):
            logger.warning(
                "PDF render: page_index %d out of range [0, %d) for %s; "
                "falling back to page 0",
                safe_index, len(doc), pdf_path,
            )
            safe_index = 0
        page = doc[safe_index]
        requested_scale = max(float(dpi), 1.0) / 72.0
        max_page_edge = max(float(page.rect.width), float(page.rect.height), 1.0)
        edge_scale = float(max_edge_px) / max_page_edge if max_edge_px and max_edge_px > 0 else requested_scale
        scale = min(requested_scale, edge_scale)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pixmap.save(str(output_path))
        return {
            "min_x": 0.0,
            "min_y": 0.0,
            "max_x": float(pixmap.width),
            "max_y": float(pixmap.height),
            "img_width": int(pixmap.width),
            "img_height": int(pixmap.height),
            "scale_x": 1.0,
            "scale_y": 1.0,
            "coordinate_space": "image_pixels",
            "page": safe_index,
            "dpi": dpi,
        }
    finally:
        doc.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render one viewer pair background.")
    parser.add_argument("--pair-id", required=True)
    parser.add_argument("--source-a", required=True)
    parser.add_argument("--source-b", required=True)
    parser.add_argument("--image-dir", required=True)
    parser.add_argument("--dxf-cache-dir", required=True)
    parser.add_argument("--dpi", type=int, required=True)
    parser.add_argument("--max-edge-px", type=int, required=True)
    parser.add_argument("--result-json", required=True)
    # Phase H — per-side PDF page indices. Default to 0 so legacy
    # callers that don't pass these still work.
    parser.add_argument("--page-a", type=int, default=0)
    parser.add_argument("--page-b", type=int, default=0)
    args = parser.parse_args(argv)

    payload = render_pair_backgrounds(
        pair_id=args.pair_id,
        source_a=Path(args.source_a),
        source_b=Path(args.source_b),
        image_dir=Path(args.image_dir),
        dxf_cache_dir=Path(args.dxf_cache_dir),
        dpi=args.dpi,
        max_edge_px=args.max_edge_px,
        page_a=args.page_a,
        page_b=args.page_b,
    )
    _write_json_atomic(Path(args.result_json), payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
