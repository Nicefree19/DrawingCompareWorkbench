# -*- coding: utf-8 -*-
"""Headless paint-latency benchmark for the lightweight viewport Canvas.

Measures the THREE numbers that decide interaction feel (T0 of the
viewport-lightness work, 2026-06-10):

  1. settled_paint_ms      — full Canvas repaint cost at fit-to-view
                             (every wheel/drag tick paid this before T1)
  2. burst_paint_count     — how many real paints 30 rapid camera ticks
                             trigger (before T1: ~1 per tick; after T1's
                             cheap-pan: ~0 during + 1 on settle)
  3. zoomed_drawn/culled   — segments actually stroked when zoomed to ~1%
                             of the sheet (culling effectiveness)

Synthetic load mirrors a CAD LOD0 skeleton: many small per-entity "lines"
primitives spread over a large world bbox. Run before/after renderer
changes and diff the numbers.

Usage:
    python scripts/benchmark_viewport_paint.py [--segments 20000] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Must be set before any Qt import: deterministic CPU rendering, no window.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

WORLD = (0.0, 0.0, 100_000.0, 60_000.0)
VIEW_W, VIEW_H = 1200, 800


def make_primitives(total_segments: int) -> list[dict]:
    """Per-entity 'lines' primitives (5 segments each) spread over WORLD.

    Deterministic pseudo-random layout (LCG) — no random module so runs are
    reproducible and comparable across commits.
    """

    prims: list[dict] = []
    seed = 0x2545F491
    x0, y0, x1, y1 = WORLD
    w, h = x1 - x0, y1 - y0
    segs_per_prim = 5
    count = max(1, total_segments // segs_per_prim)
    for i in range(count):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        px = x0 + (seed % 10_000) / 10_000.0 * w
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        py = y0 + (seed % 10_000) / 10_000.0 * h
        geometry = []
        for k in range(segs_per_prim):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            dx = ((seed % 2000) - 1000) / 10.0  # ±100 world units
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            dy = ((seed % 2000) - 1000) / 10.0
            geometry.append([px, py, px + dx, py + dy])
            px, py = px + dx, py + dy
        prims.append({"type": "lines", "geometry": geometry, "properties": {}})
    return prims


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments", type=int, default=20_000)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from src.gui.lightweight_viewport import LightweightDrawingViewport

    app = QApplication.instance() or QApplication([])
    vp = LightweightDrawingViewport()
    vp.resize(VIEW_W, VIEW_H)
    vp.show()
    quick = getattr(vp, "_quick", None)
    root = quick.rootObject() if quick is not None and hasattr(quick, "rootObject") else None
    if root is None:
        print("ABORT: QML root unavailable (FallbackQuickWidget?) — cannot benchmark paint")
        return 2

    def pump(ms: float) -> None:
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.002)

    def force_frame() -> None:
        # grabFramebuffer forces a synchronous scene-graph render pass.
        try:
            quick.grabFramebuffer()
        except Exception:  # noqa: BLE001
            pass

    def wait_paint(min_count: int, timeout_s: float = 3.0) -> bool:
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            if int(root.property("paintCount") or 0) >= min_count:
                return True
            app.processEvents()
            time.sleep(0.003)
            if time.perf_counter() > deadline - timeout_s / 2:
                force_frame()
        return int(root.property("paintCount") or 0) >= min_count

    # --- load the synthetic skeleton -----------------------------------
    prims = make_primitives(args.segments)
    total_segments = sum(len(p["geometry"]) for p in prims)
    cx = (WORLD[0] + WORLD[2]) / 2.0
    cy = (WORLD[1] + WORLD[3]) / 2.0
    fit_upp = max((WORLD[2] - WORLD[0]) / VIEW_W, (WORLD[3] - WORLD[1]) / VIEW_H)

    root.setProperty("worldBbox", list(WORLD))
    root.setProperty("cameraCenterX", cx)
    root.setProperty("cameraCenterY", cy)
    root.setProperty("unitsPerPixel", fit_upp)
    root.setProperty("primitives", prims)
    pump(50)
    ok = wait_paint(1, timeout_s=6.0)
    initial = {
        "painted": ok,
        "paint_ms": float(root.property("lastPaintMs") or 0),
        "drawn": int(root.property("lastPaintDrawnSegments") or 0),
        "culled": int(root.property("lastPaintCulledSegments") or 0),
    }
    print(f"initial_fit_paint: {initial}")

    # --- settled paint cost (median over reps) --------------------------
    settled_ms: list[float] = []
    for i in range(8):
        before = int(root.property("paintCount") or 0)
        root.setProperty("cameraCenterX", cx + (i + 1) * 2.0)
        # allow any settle timer (post-T1) to fire, then require a paint
        pump(200)
        wait_paint(before + 1, timeout_s=3.0)
        settled_ms.append(float(root.property("lastPaintMs") or 0))
    settled_sorted = sorted(settled_ms)
    settled = {
        "median_ms": settled_sorted[len(settled_sorted) // 2],
        "max_ms": settled_sorted[-1],
        "all": settled_ms,
    }
    print(f"settled_paint: {settled}")

    # --- interaction burst: 30 rapid ticks ------------------------------
    before = int(root.property("paintCount") or 0)
    t0 = time.perf_counter()
    for i in range(30):
        root.setProperty("cameraCenterX", cx + i * 25.0)
        root.setProperty("cameraCenterY", cy + (i % 7) * 10.0)
        app.processEvents()
        time.sleep(0.005)
    during = int(root.property("paintCount") or 0) - before
    burst_wall_ms = (time.perf_counter() - t0) * 1000.0
    pump(400)  # settle window
    force_frame()
    pump(50)
    total = int(root.property("paintCount") or 0) - before
    burst = {
        "paints_during_30_ticks": during,
        "paints_total_after_settle": total,
        "burst_wall_ms": round(burst_wall_ms, 1),
    }
    print(f"interaction_burst: {burst}")

    # --- zoomed-in paint (culling effectiveness) ------------------------
    before = int(root.property("paintCount") or 0)
    root.setProperty("unitsPerPixel", fit_upp * 0.01)  # view ~1% of sheet
    pump(200)
    wait_paint(before + 1, timeout_s=3.0)
    zoomed = {
        "paint_ms": float(root.property("lastPaintMs") or 0),
        "drawn": int(root.property("lastPaintDrawnSegments") or 0),
        "culled": int(root.property("lastPaintCulledSegments") or 0),
    }
    print(f"zoomed_1pct_paint: {zoomed}")

    payload = {
        "segments_requested": args.segments,
        "segments_actual": total_segments,
        "view": [VIEW_W, VIEW_H],
        "initial_fit_paint": initial,
        "settled_paint": settled,
        "interaction_burst": burst,
        "zoomed_1pct_paint": zoomed,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    vp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
