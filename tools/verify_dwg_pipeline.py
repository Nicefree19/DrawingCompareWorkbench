# -*- coding: utf-8 -*-
"""End-to-end DWG verification for the Phase G lightweight viewer pipeline.

Picks the user's most recent comparison artifact (or a path passed via
``--dwg``) and walks the full DWG → DXF → ScenePack → SpatialIndex →
ZoneFocus pipeline, printing wall-clock + primitive counts at every
stage so we can verify production-size DWG actually works.

Usage:
    python tools\\verify_dwg_pipeline.py --dwg "D:\\path\\to\\file.dwg"
    # or auto-discover the most recent cached DXF:
    python tools\\verify_dwg_pipeline.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional


def _setup_logging() -> None:
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%H:%M:%S")
    for noisy in ("PIL", "matplotlib", "fontTools", "ezdxf", "easyocr", "paddle"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _discover_recent_dwg() -> Optional[Path]:
    """Auto-discover a DWG path from the cache (looks for the source path
    embedded in any cached scene_pack)."""

    import json
    from src.services.comparison.cache_paths import (
        normalize_cache_dir,
        preview_cache_dir,
    )

    # Look for cached normalized DXF files — their stems hint at the source.
    norm = normalize_cache_dir()
    candidates = []
    if norm.exists():
        for p in sorted(norm.glob("*.dxf"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            # The cache key embeds the original DWG stem.
            stem = p.stem.split("__", 1)[0]
            candidates.append(stem)
        if candidates:
            print(f"Found {len(candidates)} cached DXFs from prior conversions.")
            print("Most recent stem: %r" % candidates[0])
    return None


def _format_seconds(s: float) -> str:
    if s < 1.0:
        return f"{s * 1000:.0f}ms"
    return f"{s:.2f}s"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="E2E DWG pipeline verification for Phase G."
    )
    parser.add_argument("--dwg", type=Path,
                        help="Path to a DWG file. If omitted, prints cache state.")
    parser.add_argument("--zone-bbox", type=str, default="",
                        help="Optional zone bbox 'x0,y0,x1,y1' to test zone_focus.")
    parser.add_argument("--out", type=Path, default=Path("verify_out"),
                        help="Output directory for built artifacts.")
    args = parser.parse_args()

    _setup_logging()

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    if args.dwg is None:
        _discover_recent_dwg()
        print()
        print("Pass --dwg <path> to run the full pipeline.")
        return 0

    if not args.dwg.exists():
        print(f"DWG not found: {args.dwg}")
        return 2

    src = args.dwg
    src_size = src.stat().st_size
    print(f"\n=== DWG E2E Verification ===")
    print(f"Source: {src}")
    print(f"Size  : {src_size / 1024:.1f} KB ({src_size / 1024 / 1024:.2f} MB)")
    print(f"Output: {args.out.resolve()}")
    print()

    # ---- Stage 1: DWG → DXF (Phase F P0 helper) -----------------------
    from src.services.comparison.zone_vector_renderer import resolve_dxf_path
    print("[1/4] Resolving DWG → DXF (DwgConverter cache)...")
    t0 = time.perf_counter()
    try:
        dxf_path = resolve_dxf_path(src)
    except Exception as exc:
        print(f"      FAILED: {exc}")
        return 3
    elapsed = time.perf_counter() - t0
    dxf_size = dxf_path.stat().st_size
    print(f"      OK in {_format_seconds(elapsed)}")
    print(f"      → {dxf_path}")
    print(f"      → {dxf_size / 1024 / 1024:.2f} MB DXF (ratio {dxf_size / src_size:.1f}x)")
    print()

    # ---- Stage 2: ScenePack build (Phase G1) --------------------------
    from src.services.comparison.scene_pack_builder import build_scene_pack
    print("[2/4] Building ScenePack (ezdxf flatten + bbox + spatial index)...")
    out_dir = args.out / "scene_pack"
    out_dir.mkdir(parents=True, exist_ok=True)

    progress_log = []

    def _print_progress(stage, percent, message):
        pct_str = f"{percent * 100:5.1f}%" if percent is not None else "  ?  "
        print(f"      [{pct_str}] {stage:<14s} {message}")
        progress_log.append((stage, message))

    t0 = time.perf_counter()
    result = build_scene_pack(src, out_dir, progress=_print_progress)
    elapsed = time.perf_counter() - t0
    print(f"      DONE in {_format_seconds(elapsed)}")
    print(f"      → primitives: {result.primitive_count} (truncated={result.truncated})")
    print(f"      → backend   : {result.backend_used}")
    print(f"      → world_bbox: {result.scene_pack_ref.drawing_world_bbox}")
    print(f"      → skipped types: {result.skipped_types}")
    if result.warnings:
        print(f"      → warnings  : {result.warnings}")
    print()

    # ---- Stage 3: Load + sanity-check the LOD0 overview ----------------
    import json
    print("[3/4] Sanity-check LOD0 skeleton...")
    overview_path = Path(result.scene_pack_ref.overview_lod0_path)
    if overview_path.exists():
        ov_data = json.loads(overview_path.read_text(encoding="utf-8"))
        ov_count = len(ov_data.get("primitives") or [])
        ov_size_kb = overview_path.stat().st_size / 1024
        print(f"      → LOD0 file size : {ov_size_kb:.1f} KB")
        print(f"      → LOD0 primitives: {ov_count} (vs full {result.primitive_count})")
        print(f"      → reduction      : {(1 - ov_count / max(1, result.primitive_count)) * 100:.0f}%")
    else:
        print(f"      MISSING: {overview_path}")
    print()

    # ---- Stage 4: ZoneFocus (optional) ---------------------------------
    print("[4/4] ZoneFocus build (optional)...")
    if not args.zone_bbox:
        print("      Skipped (no --zone-bbox provided)")
    else:
        try:
            parts = [float(v) for v in args.zone_bbox.split(",")]
            if len(parts) != 4:
                raise ValueError("zone-bbox needs 4 numbers")
            bbox = (parts[0], parts[1], parts[2], parts[3])
        except Exception as exc:
            print(f"      Bad --zone-bbox: {exc}")
            return 4
        from src.services.comparison.zone_render_worker import render_zone_focus
        zone_dir = args.out / "zone_focus"
        zone_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        zr = render_zone_focus(src, bbox, zone_dir)
        elapsed = time.perf_counter() - t0
        print(f"      DONE in {_format_seconds(elapsed)}")
        print(f"      → primitives  : {zr.primitive_count}")
        print(f"      → entities    : {zr.entity_count}")
        print(f"      → truncated   : {zr.truncated}")
        print(f"      → output      : {zr.output_path}")
        if zr.skipped_reason:
            print(f"      → skipped     : {zr.skipped_reason}")
    print()

    print("=== Summary ===")
    print(f"  source size : {src_size / 1024 / 1024:.2f} MB DWG")
    print(f"  DXF size    : {dxf_size / 1024 / 1024:.2f} MB")
    print(f"  primitives  : {result.primitive_count}")
    print(f"  total time  : {sum(1 for _ in progress_log)} stages reported")
    print(f"  cache root  : {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
