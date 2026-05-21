# -*- coding: utf-8 -*-
"""Verify the Phase G2.5 PDF compare DPI regression is fixed.

Strategy:
  1. Run a fresh comparison on the same PDF pair the user tested:
     01.3PG1.pdf vs 02.3PG1_R1.pdf
  2. Read its overlay JSON and extract zone bboxes
  3. Compare to the OLD baseline (compare_20260502_235720, pre-regression)
  4. Bboxes should now match the OLD coordinates (~ DPI 200 baseline),
     NOT the bloated DPI 400 coordinates (1.85x larger) of the regression.

A passing test means each NEW zone bbox lies within ±10% of the OLD bbox.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _setup_logging() -> None:
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%H:%M:%S")
    for noisy in ("PIL", "matplotlib", "fontTools", "ezdxf",
                  "easyocr", "paddle"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _read_zone_bboxes(viewer_dir: Path) -> dict[str, tuple[float, float, float, float]]:
    """Return ``{zone_id: (min_x, min_y, max_x, max_y)}`` from the first
    overlay JSON in this viewer dir."""

    overlay_dir = viewer_dir / "overlays"
    if not overlay_dir.exists():
        return {}
    for path in sorted(overlay_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out: dict[str, tuple[float, float, float, float]] = {}
        for ov in data.get("overlays") or []:
            zid = str(ov.get("zone_id") or "")
            bbox = ov.get("bbox")
            if zid and isinstance(bbox, dict):
                try:
                    out[zid] = (
                        float(bbox["min_x"]), float(bbox["min_y"]),
                        float(bbox["max_x"]), float(bbox["max_y"]),
                    )
                except (KeyError, TypeError, ValueError):
                    pass
        return out
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify PDF compare DPI stability against a known baseline run."
    )
    parser.add_argument(
        "--source-a",
        type=Path,
        default=Path(os.environ.get("PDF_COMPARE_SOURCE_A", r"D:\00.Work_AI_Tool\02.TEKLA_MCP\01.3PG1.pdf")),
        help="Baseline PDF input.",
    )
    parser.add_argument(
        "--source-b",
        type=Path,
        default=Path(os.environ.get("PDF_COMPARE_SOURCE_B", r"D:\00.Work_AI_Tool\02.TEKLA_MCP\02.3PG1_R1.pdf")),
        help="Revision PDF input.",
    )
    parser.add_argument(
        "--old-run",
        type=Path,
        default=Path(os.environ.get(
            "PDF_COMPARE_OLD_RUN",
            "C:/Users/user/AppData/Local/DrawingCompareWorkbench/runs/compare_20260502_235720",
        )),
        help="Existing compare_* run whose viewer bboxes are the expected baseline.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("TEMP", ".")) / "pdf_verify_run",
        help="Temporary output directory for the fresh comparison run.",
    )
    args = parser.parse_args()

    _setup_logging()
    log = logging.getLogger("verify_pdf")

    src_a = args.source_a
    src_b = args.source_b
    if not src_a.exists() or not src_b.exists():
        log.error("PDF inputs missing: %s / %s", src_a.exists(), src_b.exists())
        return 2

    # Output to a fresh per-test directory so we don't pollute the user's runs.
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("=== Test 1: Run PDF comparison with G2.5 fix ===")
    log.info("Source A: %s (%.1f MB)", src_a.name, src_a.stat().st_size / 1024 / 1024)
    log.info("Source B: %s (%.1f MB)", src_b.name, src_b.stat().st_size / 1024 / 1024)

    from src.services.comparison.folder_compare_pipeline import (
        FolderComparePipeline,
        FolderCompareRunRequest,
    )

    request = FolderCompareRunRequest(
        source_a=src_a,
        source_b=src_b,
        output_dir=out_dir,
        recursive=False,
        max_workers=1,
        preview_dpi=400,         # GUI default — high quality preview
        preview_max_edge_px=10000,
        # CRITICAL — this is the fix being verified. Even though preview_dpi
        # is 400, pdf_compare_dpi must stay at 200 (the baseline).
        pdf_compare_dpi=200,
        viewer_render_policy="top-issues",
    )

    log.info("FolderCompareRunRequest:")
    log.info("  preview_dpi      : %d  (high-quality viewport preview)", request.preview_dpi)
    log.info("  pdf_compare_dpi  : %d  (decoupled — must produce stable bbox)",
             request.pdf_compare_dpi)

    t0 = time.perf_counter()
    job = FolderComparePipeline(request)
    progress_log: list[tuple[str, int, str]] = []

    def _progress(stage: str, percent: int, msg: str) -> None:
        progress_log.append((stage, percent, msg))
        if percent in {0, 25, 50, 75, 100} or stage == "compare":
            log.info("Progress: %s @ %d%% — %s", stage, percent, msg)

    result = job.run(progress_callback=_progress)
    elapsed = time.perf_counter() - t0
    log.info("Compare done in %.1fs — status=%s", elapsed, getattr(result, "status", "?"))

    # Find the artifact dir for this run
    new_run_dir = Path(result.artifact_dir) if hasattr(result, "artifact_dir") else None
    if not new_run_dir or not new_run_dir.exists():
        log.error("No artifact_dir on result — cannot read bboxes")
        return 3

    log.info("New run artifacts: %s", new_run_dir)

    new_viewer_dir = new_run_dir / "viewer"
    if not new_viewer_dir.exists():
        log.error("No viewer dir in run output")
        return 4

    new_bboxes = _read_zone_bboxes(new_viewer_dir)
    log.info("New zones detected: %d", len(new_bboxes))

    # Compare to OLD baseline
    old_run = args.old_run
    old_bboxes = _read_zone_bboxes(old_run / "viewer")
    log.info("OLD baseline zones (5/2 23:57, DPI 200): %d", len(old_bboxes))

    # ---- Compare zone bboxes ------------------------------------------
    log.info("")
    log.info("=== ZONE BBOX COMPARISON (OLD baseline vs NEW with fix) ===")
    log.info("%-10s %-30s %-30s %-10s", "Zone", "OLD bbox", "NEW bbox", "Match?")

    matches = 0
    mismatches = 0
    summary_rows = []
    for zid in sorted(set(old_bboxes) | set(new_bboxes)):
        ob = old_bboxes.get(zid)
        nb = new_bboxes.get(zid)
        if ob is None:
            log.warning("  %s: NEW only (no OLD baseline)", zid)
            mismatches += 1
            continue
        if nb is None:
            log.warning("  %s: OLD only (NEW missed it)", zid)
            mismatches += 1
            continue
        # Compare; allow 10% tolerance per coord
        ox = (ob[0] + ob[2]) / 2.0
        nx = (nb[0] + nb[2]) / 2.0
        oy = (ob[1] + ob[3]) / 2.0
        ny = (nb[1] + nb[3]) / 2.0
        ow = ob[2] - ob[0]
        nw = nb[2] - nb[0]
        oh = ob[3] - ob[1]
        nh = nb[3] - nb[1]
        x_ratio = nx / max(0.001, ox)
        y_ratio = ny / max(0.001, oy)
        # Acceptable when ratio in 0.9~1.1
        ok = 0.9 <= x_ratio <= 1.1 and 0.9 <= y_ratio <= 1.1
        marker = "OK" if ok else "MISMATCH"
        log.info("  %s: OLD(cx=%.1f,cy=%.1f w=%.1f h=%.1f) NEW(cx=%.1f,cy=%.1f w=%.1f h=%.1f) ratio_x=%.2f ratio_y=%.2f %s",
                 zid, ox, oy, ow, oh, nx, ny, nw, nh, x_ratio, y_ratio, marker)
        summary_rows.append({
            "zone_id": zid,
            "old_bbox": list(ob),
            "new_bbox": list(nb),
            "x_ratio": round(x_ratio, 3),
            "y_ratio": round(y_ratio, 3),
            "match": ok,
        })
        if ok:
            matches += 1
        else:
            mismatches += 1

    total = matches + mismatches
    log.info("")
    log.info("=== SUMMARY ===")
    log.info("Total zones compared : %d", total)
    log.info("Matched within 10%%   : %d (%.0f%%)", matches, 100.0 * matches / max(1, total))
    log.info("Mismatched           : %d", mismatches)

    # JSON summary
    summary_path = Path(os.environ.get("TEMP", ".")) / "pdf_verify_summary.json"
    summary = {
        "test": "G2.5 PDF DPI regression fix verification",
        "preview_dpi": request.preview_dpi,
        "pdf_compare_dpi": request.pdf_compare_dpi,
        "elapsed_s": round(elapsed, 2),
        "old_run": str(old_run),
        "new_run": str(new_run_dir),
        "old_zone_count": len(old_bboxes),
        "new_zone_count": len(new_bboxes),
        "matches": matches,
        "mismatches": mismatches,
        "match_rate": round(100.0 * matches / max(1, total), 1),
        "rows": summary_rows,
        "verdict": "PASS" if matches >= total * 0.8 else "FAIL",
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Summary JSON: %s", summary_path)

    if matches >= total * 0.8:
        log.info("VERDICT: PASS — PDF compare DPI fix verified, bboxes match baseline")
        return 0
    log.error("VERDICT: FAIL — bboxes do not match baseline; fix may be incomplete")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
