# -*- coding: utf-8 -*-
"""End-to-end accuracy check for PDF change-zone detection.

Compares two PDFs at pixel level and measures what fraction of the
detected change zones actually overlap with real pixel differences.

Outputs three numbers:
    * **true positive rate** — fraction of detected zones that contain
      ≥1 differing pixel pair (lower bound on usefulness)
    * **false positive rate** — fraction of detected zones with NO
      differing pixels (upper bound on noise)
    * **coverage rate** — fraction of differing-pixel area covered by
      the detected zones (lower bound on completeness)

Run::

    python tools/verify_zone_match_accuracy.py \
      --before D:/00.Work_AI_Tool/02.TEKLA_MCP/01.3PG1.pdf \
      --after  D:/00.Work_AI_Tool/02.TEKLA_MCP/02.3PG1_R1.pdf

Quiet by default; pass ``--verbose`` to dump per-zone breakdown.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Quiet noisy libs before any import that triggers them.
logging.basicConfig(level=logging.WARNING)
for noisy in ("paddle", "PIL", "matplotlib", "fontTools", "ezdxf",
              "asyncio", "easyocr"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logging.getLogger(
    "src.services.comparison.paddle_ocr_backend"
).setLevel(logging.CRITICAL)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _pdf_page_pixels(pdf_path: Path, dpi: int) -> "numpy.ndarray":
    """Render PDF page 0 to grayscale numpy array at the given DPI."""

    import fitz  # PyMuPDF
    import numpy as np

    doc = fitz.open(str(pdf_path))
    page = doc[0]
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )
    if pix.n >= 3:
        # BGR or RGB → grayscale
        gray = (arr[..., 0].astype(np.int32)
                + arr[..., 1].astype(np.int32)
                + arr[..., 2].astype(np.int32)) // 3
        gray = gray.astype(np.uint8)
    else:
        gray = arr[..., 0]
    doc.close()
    return gray


def _zone_diff_score(
    before_arr: "numpy.ndarray",
    after_arr: "numpy.ndarray",
    bbox: list[float],
    threshold: int = 25,
) -> tuple[int, int]:
    """Return ``(differing_pixels, total_pixels)`` inside ``bbox``.

    A pixel "differs" when |before - after| > threshold (default 25/255
    catches anti-aliased edge variation while ignoring imperceptible
    JPEG/PDF compression noise).
    """

    import numpy as np

    h_a, w_a = before_arr.shape
    h_b, w_b = after_arr.shape
    h, w = min(h_a, h_b), min(w_a, w_b)
    x0, y0, x1, y1 = (int(round(v)) for v in bbox[:4])
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(w, x1); y1 = min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return 0, 0
    a_crop = before_arr[y0:y1, x0:x1].astype(np.int16)
    b_crop = after_arr[y0:y1, x0:x1].astype(np.int16)
    diff = np.abs(a_crop - b_crop) > threshold
    return int(diff.sum()), int(diff.size)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--threshold", type=int, default=25)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    print(f"PDF compare accuracy verification")
    print(f"  before: {args.before}")
    print(f"  after:  {args.after}")
    print(f"  dpi: {args.dpi}, pixel-diff threshold: {args.threshold}/255")
    print()

    # 1. Run comparison engine
    from src.services.comparison.drawing_batch import (
        compare_pdf_documents, BatchCompareOptions,
    )
    opts = BatchCompareOptions(pdf_dpi=args.dpi)
    result = compare_pdf_documents(str(args.before), str(args.after), opts)
    changes = [
        c for c in result.changes
        if c.metadata and c.metadata.get("bbox")
    ]
    print(f"Detected {len(changes)} change regions with bbox")
    if not changes:
        print("[PASS] no changes — nothing to verify")
        return 0

    # 2. Render both PDFs at the same DPI as comparison
    print(f"Rendering both PDFs at {args.dpi} DPI for ground-truth diff…")
    before = _pdf_page_pixels(args.before, args.dpi)
    after = _pdf_page_pixels(args.after, args.dpi)
    print(f"  before: {before.shape} | after: {after.shape}")
    print()

    # 3. Per-zone accuracy
    true_pos = 0  # zone contains ≥1 differing pixel
    false_pos = 0  # zone contains 0 differing pixels
    rows = []
    for c in changes:
        bbox = c.metadata["bbox"]
        n_diff, n_total = _zone_diff_score(
            before, after, bbox, threshold=args.threshold,
        )
        if n_diff >= 1:
            true_pos += 1
        else:
            false_pos += 1
        rows.append((c.key, bbox, n_diff, n_total))

    # 4. Coverage — total differing pixels in ALL zones vs whole-page
    import numpy as np
    h, w = min(before.shape[0], after.shape[0]), min(before.shape[1], after.shape[1])
    full_diff = (np.abs(
        before[:h, :w].astype(np.int16) - after[:h, :w].astype(np.int16)
    ) > args.threshold)
    total_diff_pixels = int(full_diff.sum())
    covered = 0
    if total_diff_pixels > 0:
        # Build a coverage mask of all zones
        mask = np.zeros((h, w), dtype=bool)
        for c in changes:
            bbox = c.metadata["bbox"]
            x0, y0, x1, y1 = (int(round(v)) for v in bbox[:4])
            x0 = max(0, x0); y0 = max(0, y0)
            x1 = min(w, x1); y1 = min(h, y1)
            if x1 > x0 and y1 > y0:
                mask[y0:y1, x0:x1] = True
        covered = int((full_diff & mask).sum())

    # 5. Report
    tp_rate = true_pos / len(changes)
    fp_rate = false_pos / len(changes)
    coverage = covered / total_diff_pixels if total_diff_pixels else 0.0

    print("=" * 60)
    print("ACCURACY METRICS")
    print("=" * 60)
    print(f"True positives (zones with ≥1 diff pixel):  {true_pos}/{len(changes)} ({tp_rate:.1%})")
    print(f"False positives (zones with 0 diff pixels): {false_pos}/{len(changes)} ({fp_rate:.1%})")
    print(f"Coverage (diff pixels inside zones):         {covered}/{total_diff_pixels} ({coverage:.1%})")
    print()
    print(f"Total page diff pixels: {total_diff_pixels:,}")
    print(f"Detected change zones:  {len(changes)}")
    if args.verbose:
        # Force UTF-8 stdout so the per-zone OK/MISS marks render on
        # Windows cp949 consoles (default for Korean Windows).
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        print()
        print("Per-zone breakdown:")
        for key, bbox, nd, nt in sorted(rows, key=lambda r: -r[2]):
            label = "OK  " if nd > 0 else "MISS"
            print(f"  [{label}] {key:30s} bbox={bbox} diff={nd}/{nt}")

    # Pass if true positive rate ≥ 80%
    if tp_rate < 0.8:
        print(f"\n[FAIL] true positive rate {tp_rate:.1%} below 80% threshold")
        return 1
    print(f"\n[PASS] true positive rate {tp_rate:.1%} ≥ 80%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
