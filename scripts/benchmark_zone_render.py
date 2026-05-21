# -*- coding: utf-8 -*-
"""Cold + cache-hit p50/p95 benchmark for the selected-zone render path.

Plan §17 Phase B-1b (GPT Pro F3 HIGH follow-up). The legacy
``_render_pdf_image_crop`` opens a full-page PNG via PIL for every
zone; the new DisplayList path renders only the clip region. This
benchmark measures the actual cold + cache-hit latency split so the
gates from the recommendation (cold p95 ≤ 2000 ms, cache-hit p95 ≤
500 ms) can be enforced in CI.

Two fixture types
-----------------
- **PDF** fixtures are synthesised inline using PyMuPDF (the
  ``--fixture`` choice selects the page size and entity count).
  Synthetic content is enough to exercise the DisplayList cache
  because the cache key is built from the file signature, not from
  visual content.
- **DXF** fixtures reuse the existing
  ``tests/data/comparison/golden/dxf/02_single_modification/before.dxf``
  so the harness can be cross-checked against the production
  ``DrawingRenderIndex`` cache.

Reading the report
------------------
- ``cold_pXX`` = p50 / p95 across all ``runs * zones`` measurements
  after wiping the cache between every zone (worst case — every zone
  pays the full parse + clip cost).
- ``cache_hit_pXX`` = same measurements immediately re-run with the
  cache populated. The wire metric the recommendation gates on.

Usage
-----
::

    python -X utf8 scripts/benchmark_zone_render.py \\
        --fixture small --zones 10 --runs 5 \\
        --cold-p95-target-ms 2000 --cache-hit-p95-target-ms 500

Exit code is 0 on PASS (both gates met) and 1 on FAIL unless
``--no-fail-on-exceed`` is passed.

This script is NOT a production code path. The unit smoke tests at
``tests/unit/scripts/test_benchmark_zone_render.py`` cover the helpers
only — they intentionally do NOT execute the full benchmark.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Reuse the existing DXF golden fixture (Plan §16 Phase C-3.2 uses the
# same `02_single_modification` shape for a different benchmark). This
# keeps fixture maintenance to one location.
DXF_GOLDEN_DIR = (
    _REPO_ROOT
    / "tests"
    / "data"
    / "comparison"
    / "golden"
    / "dxf"
    / "02_single_modification"
)

# PDF fixtures are synthesised on demand — see ``_build_pdf_fixture``.
# Different sizes exercise different DisplayList build costs.
PDF_FIXTURE_SPECS: Dict[str, Dict[str, Any]] = {
    "small": {"page_size": (612, 792), "rect_count": 4},
    "medium": {"page_size": (1190, 1684), "rect_count": 40},
    "large": {"page_size": (1684, 2384), "rect_count": 200},
}


DEFAULT_ZONES = 10
DEFAULT_RUNS = 5
DEFAULT_COLD_P95_MS = 2000.0
DEFAULT_CACHE_HIT_P95_MS = 500.0


@dataclass
class FixturePaths:
    """Resolved fixture artefacts for one benchmark fixture choice."""

    source_pdf: Path
    background_png: Path
    bg_w: int
    bg_h: int
    page_index: int = 0


def _build_pdf_fixture(spec_name: str, scratch_dir: Path) -> FixturePaths:
    """Synthesize a PDF + matching pre-rendered background PNG.

    Mirrors the production layout where ``before_background_image``
    is the full-page PNG previously produced by
    ``viewer_package._render_pdf_to_png`` and the cache key includes
    ``page_index`` from the background transform.
    """
    spec = PDF_FIXTURE_SPECS[spec_name]
    page_w, page_h = spec["page_size"]
    rect_count = int(spec["rect_count"])

    import fitz  # type: ignore[import-not-found]

    pdf_path = scratch_dir / f"benchmark_{spec_name}.pdf"
    bg_path = scratch_dir / f"benchmark_{spec_name}.png"

    doc = fitz.open()
    try:
        page = doc.new_page(width=float(page_w), height=float(page_h))
        # Distribute rects across the page so the DisplayList has real
        # content to parse — empty pages parse in microseconds.
        cols = max(1, int(rect_count ** 0.5))
        rows = (rect_count + cols - 1) // cols
        cell_w = page_w / max(cols + 1, 2)
        cell_h = page_h / max(rows + 1, 2)
        for i in range(rect_count):
            col = i % cols
            row = i // cols
            x0 = (col + 0.5) * cell_w
            y0 = (row + 0.5) * cell_h
            page.draw_rect(
                fitz.Rect(x0, y0, x0 + cell_w * 0.7, y0 + cell_h * 0.7),
                color=(0, 0, 0),
            )
            page.insert_text(
                (x0 + 5, y0 + 12),
                f"r-{i}",
                fontsize=8,
            )
        doc.save(str(pdf_path))

        # Pre-rendered background at 144 DPI (scale=2.0) — mirrors what
        # viewer_package would produce for the same PDF.
        page = doc[0]
        matrix = fitz.Matrix(2.0, 2.0)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        pixmap.save(str(bg_path))
        bg_w = int(pixmap.width)
        bg_h = int(pixmap.height)
    finally:
        doc.close()

    return FixturePaths(
        source_pdf=pdf_path,
        background_png=bg_path,
        bg_w=bg_w,
        bg_h=bg_h,
        page_index=0,
    )


def _build_zones(fixture: FixturePaths, zones: int) -> List[Dict[str, Any]]:
    """Produce ``zones`` non-overlapping (mostly) crop windows in
    image-pixel coordinates of the background PNG. Each zone covers
    roughly 1/zones of the page area so DisplayList re-uses are
    plausible but not 100% trivially cached.
    """
    out: List[Dict[str, Any]] = []
    cols = max(1, int(zones ** 0.5))
    rows = (zones + cols - 1) // cols
    cell_w = fixture.bg_w / cols
    cell_h = fixture.bg_h / rows
    for i in range(zones):
        col = i % cols
        row = i // cols
        x0 = col * cell_w
        y0 = row * cell_h
        out.append(
            {
                "zone_id": f"Z-{i:03d}",
                "xmin": float(x0),
                "ymin": float(y0),
                "xmax": float(x0 + cell_w),
                "ymax": float(y0 + cell_h),
            }
        )
    return out


def _percentile(samples: List[float], pct: float) -> float:
    """Linear-interpolation percentile. ``pct`` is in [0, 100]."""
    if not samples:
        return float("nan")
    if len(samples) == 1:
        return samples[0]
    ordered = sorted(samples)
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def _run_one_zone(
    fixture: FixturePaths,
    zone: Dict[str, Any],
    cache_root: Path,
    *,
    clear_cache_first: bool,
) -> float:
    """Render one zone (before+after both point at the same fixture
    so the harness drives the DisplayList path twice per zone, which
    mirrors production where the before/after PDFs are usually the
    same shape). Returns wall_ms.
    """
    from src.services.comparison.zone_render_service import (
        RenderJob,
        WorldWindow,
        render_zone_pair,
    )
    from src.services.comparison import pdf_display_list_cache

    if clear_cache_first:
        # Wipe BOTH the DisplayList cache AND the on-disk zone cache
        # so the next render hits the cold path end-to-end.
        pdf_display_list_cache._clear_cache()
        if cache_root.exists():
            shutil.rmtree(cache_root, ignore_errors=True)
        cache_root.mkdir(parents=True, exist_ok=True)

    bg_transform = {
        "coordinate_space": "image_pixels",
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": float(fixture.bg_w),
        "max_y": float(fixture.bg_h),
        "img_width": fixture.bg_w,
        "img_height": fixture.bg_h,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "page": fixture.page_index,
    }
    job = RenderJob(
        pair_uuid="benchmark-pair",
        zone_id=zone["zone_id"],
        source_before=fixture.source_pdf,
        source_after=fixture.source_pdf,
        world_window=WorldWindow(
            zone["xmin"], zone["ymin"], zone["xmax"], zone["ymax"]
        ),
        cache_root=cache_root,
        dxf_cache_dir=cache_root / "dxf",
        before_background_image=str(fixture.background_png),
        after_background_image=str(fixture.background_png),
        before_background_transform=bg_transform,
        after_background_transform=bg_transform,
    )

    started = time.perf_counter()
    result = render_zone_pair(job)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    # Sanity — the result must populate elapsed_ms and lifecycle must
    # be ready; otherwise the measurement is meaningless.
    if result.render_lifecycle != "ready":
        raise RuntimeError(
            f"zone render failed: lifecycle={result.render_lifecycle} "
            f"warnings={result.warnings}"
        )
    return elapsed_ms


def _run_pass(
    fixture: FixturePaths,
    zones: List[Dict[str, Any]],
    runs: int,
    cache_root: Path,
) -> Tuple[List[float], List[float]]:
    """Drive the cold + cache_hit measurement loop.

    Returns ``(cold_samples_ms, cache_hit_samples_ms)``.

    Methodology
    -----------
    For each ``run`` 1..N:
      - For each ``zone``:
          1. Cold: clear cache, render, record wall_ms.
          2. Cache hit: render the same zone again immediately,
             record wall_ms.

    The cache_hit measurement is dominated by the on-disk zone-cache
    JSON read (``render_zone_pair`` early-returns when
    ``meta_path.exists()``). The DisplayList cache hit is a sub-step
    inside that.
    """
    cold_samples: List[float] = []
    hit_samples: List[float] = []
    for run_idx in range(1, runs + 1):
        for zone in zones:
            cold_ms = _run_one_zone(
                fixture, zone, cache_root, clear_cache_first=True
            )
            cold_samples.append(cold_ms)
            hit_ms = _run_one_zone(
                fixture, zone, cache_root, clear_cache_first=False
            )
            hit_samples.append(hit_ms)
        print(
            f"[bench] run={run_idx}/{runs} cold_count={len(cold_samples)} "
            f"hit_count={len(hit_samples)}",
            file=sys.stderr,
            flush=True,
        )
    return cold_samples, hit_samples


def _short_git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(_REPO_ROOT),
            encoding="utf-8",
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _format_report(
    *,
    fixture_name: str,
    fixture: FixturePaths,
    zones: int,
    runs: int,
    cold_samples_ms: List[float],
    hit_samples_ms: List[float],
    cold_p95_target_ms: float,
    cache_hit_p95_target_ms: float,
    cold_verdict: str,
    hit_verdict: str,
) -> str:
    import platform

    cold_p50 = _percentile(cold_samples_ms, 50.0)
    cold_p95 = _percentile(cold_samples_ms, 95.0)
    hit_p50 = _percentile(hit_samples_ms, 50.0)
    hit_p95 = _percentile(hit_samples_ms, 95.0)
    cold_mean = statistics.fmean(cold_samples_ms) if cold_samples_ms else 0.0
    hit_mean = statistics.fmean(hit_samples_ms) if hit_samples_ms else 0.0

    buf = io.StringIO()
    buf.write("=" * 90 + "\n")
    buf.write("Selected-zone render benchmark (Plan §17 Phase B-1b)\n")
    buf.write("=" * 90 + "\n")
    buf.write(
        f"timestamp_utc:   {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
    )
    buf.write(f"git_short_sha:   {_short_git_sha()}\n")
    buf.write(f"python_version:  {platform.python_version()}\n")
    buf.write(f"platform:        {platform.platform()}\n")
    buf.write("\n")
    buf.write("INPUTS\n")
    buf.write("------\n")
    buf.write(f"fixture:                 {fixture_name}\n")
    buf.write(f"source_pdf:              {fixture.source_pdf}\n")
    buf.write(f"background_png:          {fixture.background_png}\n")
    buf.write(f"background_dimensions:   {fixture.bg_w} x {fixture.bg_h}\n")
    buf.write(f"zones_per_run:           {zones}\n")
    buf.write(f"runs:                    {runs}\n")
    buf.write(f"total_samples_per_phase: {zones * runs}\n")
    buf.write("\n")
    buf.write("RESULTS — COLD (cache cleared between every zone)\n")
    buf.write("--------------------------------------------------\n")
    buf.write(f"  samples:                 {len(cold_samples_ms)}\n")
    buf.write(f"  cold_mean_ms:            {cold_mean:.2f}\n")
    buf.write(f"  cold_p50_ms:             {cold_p50:.2f}\n")
    buf.write(f"  cold_p95_ms:             {cold_p95:.2f}\n")
    buf.write(f"  cold_p95_target_ms:      {cold_p95_target_ms:.2f}\n")
    buf.write(f"  cold_verdict:            {cold_verdict}\n")
    buf.write("\n")
    buf.write("RESULTS — CACHE HIT (same zone re-rendered immediately)\n")
    buf.write("--------------------------------------------------\n")
    buf.write(f"  samples:                 {len(hit_samples_ms)}\n")
    buf.write(f"  cache_hit_mean_ms:       {hit_mean:.2f}\n")
    buf.write(f"  cache_hit_p50_ms:        {hit_p50:.2f}\n")
    buf.write(f"  cache_hit_p95_ms:        {hit_p95:.2f}\n")
    buf.write(f"  cache_hit_p95_target_ms: {cache_hit_p95_target_ms:.2f}\n")
    buf.write(f"  cache_hit_verdict:       {hit_verdict}\n")
    buf.write("\n")
    buf.write("NOTE: the cold phase wipes BOTH the in-process DisplayList\n")
    buf.write("cache AND the on-disk zone_crops cache before every zone,\n")
    buf.write("so each cold measurement reflects the worst case (no\n")
    buf.write("memoization at any level). The cache-hit phase exercises\n")
    buf.write("the on-disk zone-cache early-return (render_zone_pair sees\n")
    buf.write("render_result.json and returns without re-rendering).\n")
    return buf.getvalue()


def _resolve_fixture(name: str, scratch_dir: Path) -> FixturePaths:
    if name not in PDF_FIXTURE_SPECS:
        raise ValueError(
            f"unknown fixture: {name!r}; choose from {list(PDF_FIXTURE_SPECS)}"
        )
    scratch_dir.mkdir(parents=True, exist_ok=True)
    return _build_pdf_fixture(name, scratch_dir)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        choices=list(PDF_FIXTURE_SPECS),
        default="small",
        help="Fixture size (default: small).",
    )
    parser.add_argument(
        "--zones",
        type=int,
        default=DEFAULT_ZONES,
        help=f"Zones per run (default: {DEFAULT_ZONES}).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Runs of the full zone set (default: {DEFAULT_RUNS}).",
    )
    parser.add_argument(
        "--cold-p95-target-ms",
        type=float,
        default=DEFAULT_COLD_P95_MS,
        help=f"PASS threshold for cold p95 (default: {DEFAULT_COLD_P95_MS} ms).",
    )
    parser.add_argument(
        "--cache-hit-p95-target-ms",
        type=float,
        default=DEFAULT_CACHE_HIT_P95_MS,
        help=(
            f"PASS threshold for cache-hit p95 "
            f"(default: {DEFAULT_CACHE_HIT_P95_MS} ms)."
        ),
    )
    parser.add_argument(
        "--fail-on-exceed",
        dest="fail_on_exceed",
        action="store_true",
        default=True,
        help="Exit non-zero when either p95 target is exceeded.",
    )
    parser.add_argument(
        "--no-fail-on-exceed",
        dest="fail_on_exceed",
        action="store_false",
        help="Always exit 0 regardless of verdict (measurement-only runs).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to write the human-readable summary (default: tmp/...).",
    )
    parser.add_argument(
        "--scratch-dir",
        type=Path,
        default=None,
        help="Scratch dir for fixtures + cache (default: tmp/zone_render_bench).",
    )
    args = parser.parse_args(argv)

    scratch = args.scratch_dir or (_REPO_ROOT / "tmp" / "zone_render_bench")
    scratch.mkdir(parents=True, exist_ok=True)
    fixture_dir = scratch / "fixtures"
    cache_root = scratch / "cache"

    print(
        f"[bench] fixture={args.fixture} zones={args.zones} runs={args.runs} "
        f"cold_p95_target={args.cold_p95_target_ms}ms "
        f"cache_hit_p95_target={args.cache_hit_p95_target_ms}ms"
    )
    fixture = _resolve_fixture(args.fixture, fixture_dir)
    zones = _build_zones(fixture, args.zones)

    cold_samples, hit_samples = _run_pass(fixture, zones, args.runs, cache_root)

    cold_p95 = _percentile(cold_samples, 95.0)
    hit_p95 = _percentile(hit_samples, 95.0)
    cold_pass = cold_p95 <= args.cold_p95_target_ms
    hit_pass = hit_p95 <= args.cache_hit_p95_target_ms
    cold_verdict = (
        f"cold_p95={cold_p95:.2f}ms — PASS (<= {args.cold_p95_target_ms:.2f}ms)"
        if cold_pass
        else f"cold_p95={cold_p95:.2f}ms — FAIL (> {args.cold_p95_target_ms:.2f}ms)"
    )
    hit_verdict = (
        f"cache_hit_p95={hit_p95:.2f}ms — PASS "
        f"(<= {args.cache_hit_p95_target_ms:.2f}ms)"
        if hit_pass
        else f"cache_hit_p95={hit_p95:.2f}ms — FAIL "
        f"(> {args.cache_hit_p95_target_ms:.2f}ms)"
    )

    report = _format_report(
        fixture_name=args.fixture,
        fixture=fixture,
        zones=args.zones,
        runs=args.runs,
        cold_samples_ms=cold_samples,
        hit_samples_ms=hit_samples,
        cold_p95_target_ms=args.cold_p95_target_ms,
        cache_hit_p95_target_ms=args.cache_hit_p95_target_ms,
        cold_verdict=cold_verdict,
        hit_verdict=hit_verdict,
    )

    out_path = args.output
    if out_path is None:
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        out_path = _REPO_ROOT / "tmp" / f"zone_render_benchmark_{ts}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"[bench] report -> {out_path}")
    print(f"[bench] {cold_verdict}")
    print(f"[bench] {hit_verdict}")

    # Also emit a machine-readable JSON line on stdout's last line so
    # other tools can scrape the verdicts without parsing the report.
    summary = {
        "fixture": args.fixture,
        "zones": args.zones,
        "runs": args.runs,
        "cold_p50_ms": _percentile(cold_samples, 50.0),
        "cold_p95_ms": cold_p95,
        "cache_hit_p50_ms": _percentile(hit_samples, 50.0),
        "cache_hit_p95_ms": hit_p95,
        "cold_pass": cold_pass,
        "cache_hit_pass": hit_pass,
    }
    sys.stdout.write(json.dumps(summary) + "\n")

    if not (cold_pass and hit_pass) and args.fail_on_exceed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
