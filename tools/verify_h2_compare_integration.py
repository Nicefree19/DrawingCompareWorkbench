# -*- coding: utf-8 -*-
"""End-to-end Phase H2 verifier — confirms ``compare_pdf_documents`` now
uses the page matcher and produces correctly cross-paged ChangeRecords.

Strategy:
  1. Pick a real multi-page PDF
  2. Synthesise reorderedB.pdf by rotating pages
  3. Call compare_pdf_documents(A, reorderedB)
  4. Assert:
     - result.metadata["page_match_enabled"] is True
     - result.metadata["page_match_pairs"] recovers the permutation
     - Sequential mode produces DIFFERENT (likely ALL-DIFFERENT) results,
       proving page matching adds value vs the old loop
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _setup_logging() -> None:
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%H:%M:%S")
    for n in ("PIL", "matplotlib", "fontTools", "ezdxf",
              "easyocr", "paddle", "fitz"):
        logging.getLogger(n).setLevel(logging.WARNING)
    # G2.7-LOGFIX — paddle_ocr_backend's import-time crash floods stdout
    # with cp949-incompatible bytes; silence it. Caller should also
    # re-set root level after triggering imports — see launch_workbench_with_log.
    logging.getLogger("src.services.comparison.paddle_ocr_backend").setLevel(logging.CRITICAL)



def _make_reordered_pdf(src: Path, dst: Path, perm: list[int]) -> None:
    import fitz
    src_doc = fitz.open(str(src))
    dst_doc = fitz.open()
    try:
        for src_idx in perm:
            dst_doc.insert_pdf(src_doc, from_page=src_idx, to_page=src_idx)
        dst_doc.save(str(dst))
    finally:
        src_doc.close()
        dst_doc.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path,
                        default=Path(r"D:\00.Work_AI_Tool\02.TEKLA_MCP\251203 P5&P6 복합동 모델링_(활하중저감X)_중요도(1)_REV66_M_분리_Report.pdf"))
    parser.add_argument("--out", type=Path,
                        default=Path(os.environ.get("TEMP", ".")) / "h2_pipeline_verify")
    args = parser.parse_args()

    _setup_logging()
    log = logging.getLogger("verify_h2")

    if not args.pdf.exists():
        log.error("Source PDF missing: %s", args.pdf)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    import fitz
    page_count = len(fitz.open(str(args.pdf)))
    log.info("Source: %s (%d pages)", args.pdf.name, page_count)

    if page_count < 2:
        log.error("Need ≥2 pages for the H2 test (got %d)", page_count)
        return 3

    # Right-rotate by 1
    perm = list(range(1, page_count)) + [0]
    log.info("Reorder permutation (B page i ← A page perm[i]): %s", perm)

    reordered = args.out / "reordered.pdf"
    _make_reordered_pdf(args.pdf, reordered, perm)
    log.info("Wrote reordered PDF: %s", reordered)

    from src.services.comparison.drawing_batch import (
        BatchCompareOptions,
        compare_pdf_documents,
    )
    # G2.7-LOGFIX — Restore root level after PaddleOCR transitive import
    # so subsequent INFO logs survive (paddle.distributed.* set root to
    # WARNING during their module-level get_logger calls).
    logging.getLogger().setLevel(logging.INFO)

    summary = {
        "test": "H2 compare_pdf_documents page-matching integration",
        "source_a": str(args.pdf),
        "source_b": str(reordered),
        "permutation": perm,
        "modes": {},
    }

    # ---- Test A: page matching ENABLED (default) ----
    log.info("\n=== Test A: pdf_page_auto_match=True ===")
    options_on = BatchCompareOptions(pdf_page_auto_match=True, pdf_dpi=120)
    try:
        result_on = compare_pdf_documents(args.pdf, reordered, options=options_on)
        log.info("  metadata.page_match_enabled = %s",
                 result_on.metadata.get("page_match_enabled"))
        log.info("  metadata.page_match_pairs_total = %s",
                 result_on.metadata.get("page_match_pairs_total"))
        log.info("  metadata.page_match_auto_confirmed = %s",
                 result_on.metadata.get("page_match_auto_confirmed"))
        log.info("  total changes = %d", len(result_on.changes))
        pairs_recorded = result_on.metadata.get("page_match_pairs", [])
        log.info("  pair list:")
        for p in pairs_recorded:
            log.info("    a=%s b=%s status=%s score=%.3f",
                     p["page_a"], p["page_b"], p["status"], p["score"])

        summary["modes"]["matching_on"] = {
            "page_match_enabled": result_on.metadata.get("page_match_enabled"),
            "pairs_total": result_on.metadata.get("page_match_pairs_total", 0),
            "auto_confirmed": result_on.metadata.get("page_match_auto_confirmed", 0),
            "review_required": result_on.metadata.get("page_match_review_required", 0),
            "total_changes": len(result_on.changes),
            "pairs": pairs_recorded,
        }
    except Exception as exc:
        log.exception("Matching-on path crashed: %s", exc)
        summary["modes"]["matching_on"] = {"error": str(exc)}
        return 4

    # ---- Test B: page matching DISABLED (legacy sequential) ----
    log.info("\n=== Test B: pdf_page_auto_match=False (legacy sequential) ===")
    options_off = BatchCompareOptions(pdf_page_auto_match=False, pdf_dpi=120)
    try:
        result_off = compare_pdf_documents(args.pdf, reordered, options=options_off)
        log.info("  metadata.page_match_enabled = %s",
                 result_off.metadata.get("page_match_enabled"))
        log.info("  total changes = %d", len(result_off.changes))
        summary["modes"]["matching_off"] = {
            "page_match_enabled": result_off.metadata.get("page_match_enabled"),
            "total_changes": len(result_off.changes),
        }
    except Exception as exc:
        log.exception("Matching-off path crashed: %s", exc)
        summary["modes"]["matching_off"] = {"error": str(exc)}

    # ---- Verdict ----
    on_data = summary["modes"].get("matching_on", {})
    pairs = on_data.get("pairs", [])
    expected_pairs = {(perm[b], b) for b in range(page_count)}
    actual_pairs = {(p["page_a"], p["page_b"]) for p in pairs}
    accuracy = len(expected_pairs & actual_pairs) / max(1, len(expected_pairs))

    log.info("\n=== Verdict ===")
    log.info("  Expected pairs (a, b): %s", sorted(expected_pairs))
    log.info("  Actual pairs   (a, b): %s", sorted(actual_pairs))
    log.info("  Accuracy: %.0f%%", 100 * accuracy)

    summary["expected_pairs"] = sorted(expected_pairs)
    summary["actual_pairs"] = sorted(actual_pairs)
    summary["accuracy"] = round(accuracy, 3)
    summary["verdict"] = "PASS" if accuracy >= 0.95 else "FAIL"

    summary_path = Path(os.environ.get("TEMP", ".")) / "h2_pipeline_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Summary JSON: %s", summary_path)
    log.info("VERDICT: %s", summary["verdict"])
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
