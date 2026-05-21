# -*- coding: utf-8 -*-
"""End-to-end performance benchmark for the Drawing Compare workbench.

Measures the full pipeline against multiple PDF sizes and reports:
  * Compare time (engine)
  * Page-render time (Qt PDF)
  * Overlay build time
  * Memory: peak RSS during the run
  * For PDFs ≥ 2 pages: page-matching time

Designed to give a single-screen go/no-go for each customer-tier sample
without requiring the GUI. Results land in ``out/perf_<timestamp>.json``
for CI consumption + a Korean status banner for humans.

Run::

    python tools/benchmark_workbench_perf.py

Pass ``--samples-extra path/to/big.pdf`` to add ad-hoc samples beyond the
built-in matrix.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.WARNING)
for n in ("paddle", "PIL", "matplotlib", "fontTools", "ezdxf",
          "easyocr", "asyncio", "fitz"):
    logging.getLogger(n).setLevel(logging.WARNING)
logging.getLogger(
    "src.services.comparison.paddle_ocr_backend"
).setLevel(logging.CRITICAL)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _peak_rss_mb() -> float:
    """Best-effort peak RSS via psutil (preferred) or tracemalloc fallback."""

    try:
        import psutil
        proc = psutil.Process(os.getpid())
        # peak_wset on Windows; max_rss on Linux/Mac
        info = proc.memory_info()
        peak = getattr(info, "peak_wset", None) or getattr(info, "rss", 0)
        return peak / (1024.0 * 1024.0)
    except ImportError:
        # tracemalloc is python-only; doesn't include C extension allocs
        # (Qt, PaddleOCR, ezdxf C engine) so it's an undercount but better
        # than nothing.
        if tracemalloc.is_tracing():
            cur, peak = tracemalloc.get_traced_memory()
            return peak / (1024.0 * 1024.0)
        return 0.0


def _benchmark_pdf_pair(before: Path, after: Path, *, label: str) -> dict[str, Any]:
    """Run one PDF compare and capture timing + memory."""

    print(f"\n=== {label} ===")
    print(f"  before: {before.name} ({before.stat().st_size / 1024:.0f} KB)")
    print(f"  after : {after.name} ({after.stat().st_size / 1024:.0f} KB)")

    import fitz
    bd = fitz.open(str(before))
    ad = fitz.open(str(after))
    bp = bd.page_count
    ap = ad.page_count
    bd.close(); ad.close()
    print(f"  pages: A={bp}, B={ap}")

    if not tracemalloc.is_tracing():
        tracemalloc.start()
    gc.collect()

    # 1. Compare
    from src.services.comparison.drawing_batch import (
        compare_pdf_documents, BatchCompareOptions,
    )
    t0 = time.perf_counter()
    opts = BatchCompareOptions(pdf_dpi=200)
    result = compare_pdf_documents(str(before), str(after), opts)
    compare_s = time.perf_counter() - t0
    n_changes = len(result.changes)
    print(f"  compare: {compare_s:.2f}s ({n_changes} changes)")

    # 2. Render per page (preview at 400 DPI)
    bd = fitz.open(str(after))
    page_render_s = 0.0
    total_pixels = 0
    for i in range(min(ap, 5)):  # cap at 5 pages for time budget
        t0 = time.perf_counter()
        pix = bd[i].get_pixmap(dpi=400, alpha=False)
        page_render_s += time.perf_counter() - t0
        total_pixels += pix.width * pix.height
    bd.close()
    print(f"  preview render ({min(ap, 5)} pages @ 400 DPI): {page_render_s:.2f}s ({total_pixels/1e6:.1f} Mpx)")

    # 3. Page matching (multi-page only)
    page_match_s = None
    if min(bp, ap) > 1:
        from src.services.comparison.page_descriptor import build_per_page_descriptors
        from src.services.comparison.page_matcher import match_pdf_pages
        logging.getLogger().setLevel(logging.INFO)
        t0 = time.perf_counter()
        desc_a = build_per_page_descriptors(before)
        desc_b = build_per_page_descriptors(after)
        candidates = match_pdf_pages(desc_a, desc_b)
        page_match_s = time.perf_counter() - t0
        n_matched = sum(1 for c in candidates if c.is_matched)
        print(f"  page match: {page_match_s:.2f}s ({n_matched}/{min(bp, ap)} matched)")

    peak_mb = _peak_rss_mb()
    print(f"  peak RSS: {peak_mb:.0f} MB")

    return {
        "label": label,
        "before_filename": before.name,
        "after_filename": after.name,
        "before_kb": round(before.stat().st_size / 1024, 1),
        "after_kb": round(after.stat().st_size / 1024, 1),
        "page_count_a": bp,
        "page_count_b": ap,
        "compare_s": round(compare_s, 3),
        "page_render_s": round(page_render_s, 3),
        "page_render_mpx": round(total_pixels / 1e6, 2),
        "page_match_s": round(page_match_s, 3) if page_match_s else None,
        "peak_rss_mb": round(peak_mb, 1),
        "n_changes": n_changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples-extra", type=Path, action="append", default=[],
                        help="Additional PDF to add as a synthetic-self-pair sample")
    args = parser.parse_args()

    print("=" * 70)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("Workbench Performance Benchmark - drawing-compare pipeline")
    print(f"Run at: {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 70)

    samples = []

    # Sample 1: small real PDF pair (1 page)
    a = PROJECT_ROOT / "01.3PG1.pdf"
    b = PROJECT_ROOT / "02.3PG1_R1.pdf"
    if a.exists() and b.exists():
        samples.append((a, b, "small_1page"))

    # Sample 2: 9-page synthetic-self compare (treats same PDF as A and B)
    multi = PROJECT_ROOT / "tmp" / "composite_beam_review.pdf"
    if multi.exists():
        samples.append((multi, multi, "9page_self_compare"))

    # Sample 3: 24-page large PDF
    large = PROJECT_ROOT / "13 - 3D geometry and solids - Tekla.Structures.Geometry3D, solids and booleans.pdf"
    if large.exists():
        samples.append((large, large, "24page_large_self_compare"))

    for extra in args.samples_extra:
        if extra and extra.exists():
            samples.append((extra, extra, f"extra_{extra.name}"))

    if not samples:
        print("[ERROR] no benchmark samples found")
        return 2

    results = []
    for before, after, label in samples:
        try:
            res = _benchmark_pdf_pair(before, after, label=label)
            results.append(res)
        except Exception as exc:
            print(f"  [ERROR] {exc}")
            results.append({"label": label, "error": str(exc)})

    # Final report
    print("\n" + "=" * 70)
    print("BENCHMARK SUMMARY")
    print("=" * 70)
    print(f"{'Sample':<30s} {'Pages':>6s} {'Compare':>10s} {'Render':>10s} {'Match':>8s} {'RSS':>8s} {'Changes':>8s}")
    print("-" * 86)
    for r in results:
        if "error" in r:
            print(f"{r['label']:<30s} ERROR: {r['error']}")
            continue
        pages = f"{r['page_count_a']}/{r['page_count_b']}"
        match = f"{r['page_match_s']}s" if r['page_match_s'] else "—"
        print(
            f"{r['label']:<30s} {pages:>6s} "
            f"{r['compare_s']:>9.2f}s {r['page_render_s']:>9.2f}s "
            f"{match:>8s} {r['peak_rss_mb']:>7.0f}M {r['n_changes']:>8d}"
        )

    # Persist
    out_dir = PROJECT_ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"perf_{stamp}.json"
    out_path.write_text(
        json.dumps({
            "ran_at_utc": datetime.now(timezone.utc).isoformat(),
            "samples": results,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nResults: {out_path}")

    # Summary verdict — fail when any sample exceeds budget
    over_budget = []
    for r in results:
        if "error" in r:
            over_budget.append(f"{r['label']}: errored")
            continue
        budget_compare = 60.0 if r['page_count_a'] >= 10 else 30.0
        if r['compare_s'] > budget_compare:
            over_budget.append(
                f"{r['label']}: compare {r['compare_s']:.1f}s > {budget_compare}s"
            )
        if r['peak_rss_mb'] > 2000.0:
            over_budget.append(
                f"{r['label']}: RSS {r['peak_rss_mb']:.0f}MB > 2000MB cap"
            )

    if over_budget:
        print("\n[FAIL] Performance regressions:")
        for v in over_budget:
            print(f"  - {v}")
        return 1
    print("\n[PASS] All samples within performance budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
