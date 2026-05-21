# -*- coding: utf-8 -*-
"""cProfile benchmark for the viewer build step (§13 Phase C).

The 2026-05-15 self-review found that every published time estimate for
``viewer_package.export_viewer_package`` (the suspected source of the
12-minute user-visible wait on S20-class drawings) was a guess. No cProfile
data existed. No baseline existed. This script produces one.

Usage::

    python -X utf8 scripts/benchmark_viewer_build.py \
        --fixture small --runs 3 --output tmp/viewer_build_profile.txt

The benchmark wires the in-process ``export_viewer_package`` through the
proxy so we measure what actually runs in the renderer subprocess — not
the conftest in-process stub the unit suite uses.

The output report ranks functions by *cumulative time spent in the call
tree rooted at that function* (``pstats.sort='cumulative'``), filtered to
the ``viewer_package`` / ``viewer_tile_cache`` / ``zone_render`` module
families so reviewers can see where the time is going without wading
through 5,000 lines of generic Python overhead.

The script must NOT be run in production paths. It is a measurement tool.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


FIXTURES: dict[str, Path] = {
    "small": _REPO_ROOT / "tests" / "data" / "comparison" / "golden" / "dxf" / "02_single_modification",
    "medium": _REPO_ROOT / "tests" / "data" / "comparison" / "golden" / "dxf" / "08_intentional_zone_shift_beam",
    "structural": _REPO_ROOT / "tests" / "data" / "comparison" / "golden" / "dxf" / "14_structural_submm_shift",
}


def _stage_inputs(fixture_dir: Path, tmp_base: Path) -> tuple[Path, Path]:
    """Copy before/after DXFs into source_a / source_b folders the pipeline
    expects.
    """
    source_a = tmp_base / "source_a"
    source_b = tmp_base / "source_b"
    source_a.mkdir(parents=True, exist_ok=True)
    source_b.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixture_dir / "before.dxf", source_a / "drawing.dxf")
    shutil.copy(fixture_dir / "after.dxf", source_b / "drawing.dxf")
    return source_a, source_b


def _stub_subprocess_with_in_process(monkeypatched: list):
    """Route ``export_viewer_package_isolated`` to the in-process exporter so
    cProfile actually captures viewer_package internals (a real subprocess
    would not show up in our parent profiler).
    """
    from src.services.comparison import folder_compare_pipeline as pipeline_mod

    original = pipeline_mod.export_viewer_package_isolated

    def _inprocess(
        artifact_dir,
        *,
        options=None,
        memory_cap_mb=None,
        timeout_s=None,
        progress_callback=None,
        python_executable=None,
        allow_inprocess_fallback=False,
        fault_log_dir=None,
    ):
        opts = dict(options or {})
        viewer_dir_arg = opts.pop("viewer_dir", None)
        try:
            result = pipeline_mod.export_viewer_package(
                artifact_dir, viewer_dir_arg, **opts
            )
        except Exception as exc:
            return (
                None,
                pipeline_mod.SubprocessRunReport(
                    exit_code=1,
                    elapsed_s=0.0,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                ),
            )
        payload = {
            "output_paths": dict(result.output_paths),
            "pair_count": result.pair_count,
            "overlay_count": result.overlay_count,
        }
        return (
            payload,
            pipeline_mod.SubprocessRunReport(exit_code=0, elapsed_s=0.0),
        )

    pipeline_mod.export_viewer_package_isolated = _inprocess
    monkeypatched.append((pipeline_mod, "export_viewer_package_isolated", original))


def _profile_one_run(fixture_dir: Path) -> tuple[cProfile.Profile, float]:
    from src.services.comparison.folder_compare_pipeline import (
        FolderCompareRunRequest,
        FolderComparePipeline,
    )

    profiler = cProfile.Profile()
    with tempfile.TemporaryDirectory(prefix="viewer_bench_") as tmp:
        tmp_base = Path(tmp)
        source_a, source_b = _stage_inputs(fixture_dir, tmp_base)
        request = FolderCompareRunRequest(
            source_a=source_a,
            source_b=source_b,
            output_dir=tmp_base / "out",
        )
        pipeline = FolderComparePipeline(request)

        wall_start = time.perf_counter()
        profiler.enable()
        try:
            pipeline.run()
        finally:
            profiler.disable()
        wall_elapsed = time.perf_counter() - wall_start

    return profiler, wall_elapsed


VIEWER_FAMILIES = (
    "viewer_package",
    "viewer_tile_cache",
    "zone_render",
    "matplotlib",
    "PIL",
)


def _format_pstats(profiler: cProfile.Profile, top_n: int = 40) -> str:
    buf = io.StringIO()
    stats = pstats.Stats(profiler, stream=buf).strip_dirs().sort_stats("cumulative")

    buf.write("=" * 90 + "\n")
    buf.write("TOP {0} BY CUMULATIVE TIME (all functions)\n".format(top_n))
    buf.write("=" * 90 + "\n")
    stats.print_stats(top_n)
    buf.write("\n" + "=" * 90 + "\n")
    buf.write("VIEWER FAMILY FUNCTIONS ONLY (filter: viewer_package / viewer_tile_cache / zone_render / matplotlib / PIL)\n")
    buf.write("=" * 90 + "\n")
    stats.print_stats("|".join(VIEWER_FAMILIES))
    return buf.getvalue()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        choices=sorted(FIXTURES.keys()),
        default="small",
        help="Which golden fixture pair to benchmark.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of repeat runs (the slowest is reported; reduces JIT noise).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to write the pstats report. Defaults to tmp/viewer_build_profile_<ts>.txt.",
    )
    args = parser.parse_args(argv)

    fixture_dir = FIXTURES[args.fixture]
    if not fixture_dir.exists():
        print(f"ERROR: fixture missing: {fixture_dir}", file=sys.stderr)
        return 2

    print(f"[benchmark] fixture={args.fixture} ({fixture_dir})")
    print(f"[benchmark] runs={args.runs}")

    monkeypatched: list = []
    _stub_subprocess_with_in_process(monkeypatched)

    try:
        wall_times: list[float] = []
        best_profiler: Optional[cProfile.Profile] = None
        best_wall: float = float("inf")
        for i in range(1, args.runs + 1):
            print(f"[benchmark] run {i}/{args.runs} ...", flush=True)
            profiler, wall = _profile_one_run(fixture_dir)
            wall_times.append(wall)
            print(f"[benchmark]   wall_elapsed = {wall:.3f}s")
            if wall < best_wall:
                best_wall = wall
                best_profiler = profiler

        if best_profiler is None:
            print("ERROR: no runs completed", file=sys.stderr)
            return 1

        report = _format_pstats(best_profiler)

        out_path = args.output
        if out_path is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
            out_path = _REPO_ROOT / "tmp" / f"viewer_build_profile_{ts}.txt"
            out_path.parent.mkdir(parents=True, exist_ok=True)

        with out_path.open("w", encoding="utf-8") as fh:
            fh.write(f"benchmark: viewer_build cProfile baseline\n")
            fh.write(f"fixture: {args.fixture} ({fixture_dir.name})\n")
            fh.write(f"runs: {args.runs}\n")
            fh.write(f"wall_times (s): {wall_times}\n")
            fh.write(f"fastest_wall_s: {best_wall:.3f}\n")
            fh.write(f"slowest_wall_s: {max(wall_times):.3f}\n")
            fh.write(f"median_wall_s: {sorted(wall_times)[len(wall_times)//2]:.3f}\n")
            fh.write("\n")
            fh.write(report)

        print(f"[benchmark] report -> {out_path}")
        print(f"[benchmark] fastest wall_elapsed: {best_wall:.3f}s")
        return 0

    finally:
        # Restore monkeypatched names so importing modules after this
        # benchmark does not see the in-process stub leaked.
        for module, attr, original in monkeypatched:
            setattr(module, attr, original)


if __name__ == "__main__":
    sys.exit(main())
