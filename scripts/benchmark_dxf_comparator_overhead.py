# -*- coding: utf-8 -*-
"""cProfile benchmark for the DxfComparator hot-loop helper overhead
(Plan §16 Phase C-3.2).

The §15/§16 self-review found that ``DxfComparator._record_change`` and
``_record_changes`` (Plan §15 Phase C-1) and the ``time_to_first_stream_record_ms``
instrumentation (Plan §16 Phase C-3.1) were added without quantitative
proof that they impose ≤1% overhead on the hot loop. This script supplies
that proof, reproducibly.

Methodology
-----------
1.  We synthesise an in-memory ``entities_a`` / ``entities_b`` pair by
    duplicating a small seed fixture (``14_structural_submm_shift``) into
    two unequal scaled-up dicts with disjoint hashes. This drives ~N
    changes through the comparator without depending on any real DXF
    file of unusual size — every machine running this benchmark gets the
    same input shape.

2.  We then invoke ``DxfComparator().compare(entities_a, entities_b)``
    under ``cProfile`` TWICE — once in a "baseline" subprocess where the
    two helpers are monkey-patched down to a pure ``list.append`` /
    ``list.extend``, and once in an "instrumented" subprocess where the
    real helpers run. Each pass is a fresh ``python`` subprocess so the
    cProfile state and import cache are not shared between passes; that
    is what justifies the comparison as fair.

3.  We report ``delta_pct = (instr_mean - base_mean) / base_mean * 100``
    where the means are computed over ``--runs`` repetitions per pass
    (default 5), discarding the slowest and fastest, averaging the
    middle.

WARNING — what this benchmark does NOT measure
----------------------------------------------
This benchmark measures the IN-MEMORY hot-loop overhead only. It
deliberately leaves ``DxfComparator.change_zone_stream_path`` unset, so
``_write_change_zone_stream`` early-exits (see
``src/services/comparison/dxf_comparator.py`` ~line 802) and the
streaming-path overhead — including
``time_to_first_stream_record_ms`` — is NOT exercised. A separate
benchmark would be needed if the streaming overhead must also be
proven within budget. The §16 Phase C-3.1 instrumentation is
nevertheless still indirectly checked because
``_compare_start_perf`` and ``_stream_first_write_perf`` reset happens
unconditionally inside ``compare()`` entry and is included in the
profiled scope.

Usage
-----
::

    python -X utf8 scripts/benchmark_dxf_comparator_overhead.py \
        --runs 5 --target-changes 50000 \
        --output tmp/dxf_comparator_overhead.txt

Exit code is 0 on PASS (delta_pct ≤ ``--max-overhead-pct``) and 1 on
FAIL unless ``--no-fail-on-exceed`` is passed.

This script must NOT be run in production paths. It is a measurement
tool. The unit-suite smoke tests in
``tests/unit/scripts/test_benchmark_dxf_comparator_overhead.py`` cover
helper integrity only — they intentionally do NOT execute the real
benchmark (which takes minutes and writes large reports).
"""

from __future__ import annotations

import argparse
import contextlib
import cProfile
import dataclasses
import io
import json
import os
import pstats
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


SEED_FIXTURE_DIR = (
    _REPO_ROOT / "tests" / "data" / "comparison" / "golden" / "dxf" / "14_structural_submm_shift"
)
DEFAULT_TARGET_CHANGES = 50_000
DEFAULT_RUNS = 5
DEFAULT_MAX_OVERHEAD_PCT = 1.0


def _extract_entities_from_dxf(path: Path) -> Dict[str, List[Any]]:
    """Load ``path`` and return the
    ``Dict[entity_type, List[NormalizedEntity]]`` shape ``DxfComparator``
    consumes.

    We delegate to the public ``DxfEntityExtractor.extract_from_file``
    so the benchmark is not coupled to the private extraction order.
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    extractor = DxfEntityExtractor()
    return extractor.extract_from_file(path)


def _inflate_entities(
    base: Dict[str, List[Any]], factor: int, side: str
) -> Dict[str, List[Any]]:
    """Return a new dict where each entity list has been duplicated
    ``factor`` times, each copy carrying a unique ``hash`` so the
    comparator sees them as distinct (forcing them all into the change
    set).

    ``side`` is just a salt — using a different ``side`` for the two
    inputs guarantees no accidental hash collision between A and B.
    Slotted ``NormalizedEntity`` is replaced via
    ``dataclasses.replace`` which handles ``slots=True`` correctly.
    """
    inflated: Dict[str, List[Any]] = {}
    for entity_type, entities in base.items():
        if not entities:
            inflated[entity_type] = []
            continue
        new_list: List[Any] = []
        for i in range(factor):
            for entity in entities:
                # Per-copy unique hash. Keep the original prefix so a
                # debugger reading the report can still locate the seed.
                new_hash = f"{entity.hash}_dup{i}_{side}"
                new_list.append(dataclasses.replace(entity, hash=new_hash))
        inflated[entity_type] = new_list
    return inflated


def _build_synthetic_inputs(
    scale_factor: int,
) -> Tuple[Dict[str, List[Any]], Dict[str, List[Any]]]:
    """Build the (entities_a, entities_b) pair used by every pass.

    Both sides are inflated from the SAME seed; the only differences
    are the duplication factor (A vs A+1) and the side salt — that is
    enough to make every entity in B mismatch every entity in A,
    which is the worst-case path for ``_record_change``/
    ``_record_changes`` and so the strictest overhead probe.
    """
    seed_before = _extract_entities_from_dxf(SEED_FIXTURE_DIR / "before.dxf")
    seed_after = _extract_entities_from_dxf(SEED_FIXTURE_DIR / "after.dxf")
    inflated_a = _inflate_entities(seed_before, scale_factor, "a")
    inflated_b = _inflate_entities(seed_after, scale_factor + 1, "b")
    return inflated_a, inflated_b


def _seed_entity_count(base: Dict[str, List[Any]]) -> int:
    return sum(len(v) for v in base.values())


def _scale_factor_for_target_changes(target: int) -> int:
    """Estimate a scale_factor that produces at least ``target`` changes.

    Empirically: with the 6-entity seed and the
    ``(factor, factor+1)`` scheme, the comparator records roughly
    ``(factor + factor + 1) * seed_count`` changes (every duplicated
    entity has a unique hash so it lands in the change set). We round
    up to be safe.
    """
    seed_before = _extract_entities_from_dxf(SEED_FIXTURE_DIR / "before.dxf")
    seed_count = _seed_entity_count(seed_before)
    if seed_count <= 0:
        raise RuntimeError(
            f"Seed fixture at {SEED_FIXTURE_DIR / 'before.dxf'} contains no entities"
        )
    # changes_per_unit_scale = seed_count * 2 (one side per scale unit).
    # We add +1 to scale_factor for side B, so total changes ≈ (2f + 1) * seed_count.
    # Solve for f: f ≈ (target / seed_count - 1) / 2, then round UP.
    estimated = int((target / seed_count - 1) / 2) + 1
    return max(1, estimated)


@contextlib.contextmanager
def _monkeypatch_record_helpers_to_pure_list_ops():
    """Within this context, ``DxfComparator._record_change`` and
    ``_record_changes`` are replaced with the minimal pure-Python
    list-op equivalents so the cProfile pass exposes the marginal cost
    of the real helpers vs. raw list ops.

    Both passes (baseline + instrumented) still go through the same
    helper *call sites* in ``compare()`` — what changes is whether the
    helper body executes the peak-tracking logic or just appends.
    """
    from src.services.comparison import dxf_comparator as comparator_mod

    cls = comparator_mod.DxfComparator
    # ``__dict__`` returns the raw descriptor (``staticmethod`` object)
    # whereas ``cls._record_change`` triggers the descriptor protocol
    # and yields a bare function — restoring the bare function would
    # silently demote the helpers to bound methods on exit, breaking
    # every subsequent comparator call. Pull the descriptors directly.
    original_record_change = cls.__dict__["_record_change"]
    original_record_changes = cls.__dict__["_record_changes"]

    # NOTE on methodology (Plan §16 Phase C-3.3) — after the monotonic-
    # peak optimisation, the real helpers do nothing more than
    # ``result.changes.append/extend`` and the
    # ``peak_changes_pre_truncate`` invariant is satisfied by a single
    # ``len(result.changes)`` set at the END of compare() (before
    # ``result.stats = stats`` rebind). Therefore the baseline monkey-
    # patch is now also a pure list mutation — measuring the residual
    # dispatch overhead between (a) calling the real helper and (b)
    # bypass-style direct list mutation. Both satisfy the invariant.
    # Expected delta_pct ≈ 0% with measurement noise.
    def _pure_append(result, change):  # type: ignore[no-untyped-def]
        result.changes.append(change)

    def _pure_extend(result, changes):  # type: ignore[no-untyped-def]
        result.changes.extend(changes)

    cls._record_change = staticmethod(_pure_append)  # type: ignore[assignment]
    cls._record_changes = staticmethod(_pure_extend)  # type: ignore[assignment]
    try:
        yield
    finally:
        cls._record_change = original_record_change  # type: ignore[assignment]
        cls._record_changes = original_record_changes  # type: ignore[assignment]


def _run_compare_under_cprofile(
    entities_a: Dict[str, List[Any]],
    entities_b: Dict[str, List[Any]],
    monkeypatch_helpers: bool,
) -> Tuple[float, str, int]:
    """Run a single ``compare()`` under cProfile.

    Returns ``(wall_seconds, profile_dump_text, len_changes)``.

    ``wall_seconds`` is captured around the cProfile context with
    ``time.perf_counter()`` so it reflects pure wall time without
    cProfile's own overhead skew.
    """
    from src.services.comparison.dxf_comparator import DxfComparator

    comparator = DxfComparator()
    profiler = cProfile.Profile()

    cm = (
        _monkeypatch_record_helpers_to_pure_list_ops()
        if monkeypatch_helpers
        else contextlib.nullcontext()
    )
    with cm:
        wall_start = time.perf_counter()
        profiler.enable()
        try:
            result = comparator.compare(entities_a, entities_b)
        finally:
            profiler.disable()
        wall_elapsed = time.perf_counter() - wall_start

    buf = io.StringIO()
    pstats.Stats(profiler, stream=buf).strip_dirs().sort_stats("cumulative").print_stats(
        "dxf_comparator", 30
    )
    return wall_elapsed, buf.getvalue(), len(result.changes)


def _run_compare_wall_only(
    entities_a: Dict[str, List[Any]],
    entities_b: Dict[str, List[Any]],
    monkeypatch_helpers: bool,
) -> Tuple[float, int]:
    """Run a single ``compare()`` WITHOUT cProfile — pure wall time.

    Plan §16 R5 follow-up: cProfile itself amplifies per-call overhead
    (each function call carries the profiler's own bookkeeping cost),
    which inflates the measured delta between the lambda baseline and
    the real helper methods. Production code does NOT run under
    cProfile, so the wall-only measurement is the truer indicator of
    real-world overhead.

    Returns ``(wall_seconds, len_changes)``.
    """
    from src.services.comparison.dxf_comparator import DxfComparator

    comparator = DxfComparator()
    cm = (
        _monkeypatch_record_helpers_to_pure_list_ops()
        if monkeypatch_helpers
        else contextlib.nullcontext()
    )
    with cm:
        wall_start = time.perf_counter()
        result = comparator.compare(entities_a, entities_b)
        wall_elapsed = time.perf_counter() - wall_start

    return wall_elapsed, len(result.changes)


def _trimmed_mean(samples: List[float]) -> float:
    """Mean after dropping the slowest + fastest samples (if ≥3
    samples). For <3 samples we fall back to the plain mean.
    """
    if len(samples) < 3:
        return statistics.fmean(samples)
    middle = sorted(samples)[1:-1]
    return statistics.fmean(middle)


def _subprocess_pass(pass_name: str, scale_factor: int, runs: int) -> Dict[str, Any]:
    """Run the named pass in a fresh Python subprocess and decode the
    JSON it emits on stdout.
    """
    cmd = [
        sys.executable,
        "-X",
        "utf8",
        str(Path(__file__).resolve()),
        "--pass",
        pass_name,
        "--scale-factor",
        str(scale_factor),
        "--runs",
        str(runs),
    ]
    completed = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO_ROOT),
    )
    # The pass writes a single JSON line as its LAST stdout line; earlier
    # progress prints go to stderr so they do not pollute the parse.
    stdout = completed.stdout.strip().splitlines()
    if not stdout:
        raise RuntimeError(
            f"pass={pass_name} produced no stdout; stderr was:\n{completed.stderr}"
        )
    last_line = stdout[-1]
    try:
        return json.loads(last_line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"pass={pass_name} stdout was not valid JSON: {last_line!r}\n"
            f"full stderr:\n{completed.stderr}"
        ) from exc


def _run_pass_in_process(
    pass_name: str, scale_factor: int, runs: int
) -> Dict[str, Any]:
    """Execute one pass in-process (used by the --pass child subprocess).

    Plan §16 R5 follow-up — measures TWO wall-time series per pass:
    1. ``wall_samples_s``: pure perf_counter, no cProfile context.
       Represents the real production overhead.
    2. ``cprofile_wall_samples_s``: same compare() but inside an active
       cProfile.Profile() context. Inflates per-call overhead because
       every function call carries the profiler's bookkeeping cost.
       Kept for the pstats dump and as the worst-case bound.

    The first run is treated as a warm-up to populate the import cache,
    JIT, etc., and is excluded from the reported samples.
    """
    monkeypatch_helpers = pass_name == "baseline"

    print(
        f"[bench/{pass_name}] scale_factor={scale_factor} runs={runs} "
        f"(plus 1 warm-up, both wall + cProfile passes)",
        file=sys.stderr,
        flush=True,
    )
    entities_a, entities_b = _build_synthetic_inputs(scale_factor)
    total_a = _seed_entity_count(entities_a)
    total_b = _seed_entity_count(entities_b)

    # Warm-up: one wall-only run discarded (cheaper than cProfile so we
    # don't burn extra time but still populates the import cache).
    _ = _run_compare_wall_only(
        entities_a, entities_b, monkeypatch_helpers=monkeypatch_helpers
    )

    wall_samples: List[float] = []
    cprofile_samples: List[float] = []
    last_profile_text: str = ""
    last_change_count: int = 0
    for i in range(1, runs + 1):
        # Pass A — pure wall (no cProfile). This is the production-fair
        # measurement.
        wall, change_count = _run_compare_wall_only(
            entities_a, entities_b, monkeypatch_helpers=monkeypatch_helpers
        )
        wall_samples.append(wall)
        last_change_count = change_count

        # Pass B — same call under cProfile. Keeps pstats evidence
        # available even when the production gate is wall-based.
        cp_wall, profile_text, _ = _run_compare_under_cprofile(
            entities_a, entities_b, monkeypatch_helpers=monkeypatch_helpers
        )
        cprofile_samples.append(cp_wall)
        last_profile_text = profile_text

        print(
            f"[bench/{pass_name}]   run {i}/{runs}: wall={wall:.4f}s "
            f"cprofile_wall={cp_wall:.4f}s changes={change_count}",
            file=sys.stderr,
            flush=True,
        )

    return {
        "pass": pass_name,
        "scale_factor": scale_factor,
        "runs": runs,
        "entity_count_a": total_a,
        "entity_count_b": total_b,
        "change_count": last_change_count,
        # Production-fair metric (wall-only, no cProfile context).
        "wall_samples_s": wall_samples,
        "wall_trimmed_mean_s": _trimmed_mean(wall_samples),
        "wall_min_s": min(wall_samples),
        "wall_max_s": max(wall_samples),
        # Worst-case metric (cProfile context inflates per-call overhead).
        "cprofile_wall_samples_s": cprofile_samples,
        "cprofile_wall_trimmed_mean_s": _trimmed_mean(cprofile_samples),
        "cprofile_wall_min_s": min(cprofile_samples),
        "cprofile_wall_max_s": max(cprofile_samples),
        # cProfile dump is large; keep it only for the instrumented pass
        # to reduce subprocess stdout volume.
        "profile_text": last_profile_text if pass_name == "instrumented" else "",
    }


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
    baseline: Dict[str, Any],
    instrumented: Dict[str, Any],
    delta_pct: float,
    threshold_pct: float,
    verdict: str,
    scale_factor: int,
    target_changes: int,
) -> str:
    import platform

    buf = io.StringIO()
    buf.write("=" * 90 + "\n")
    buf.write("DxfComparator hot-loop overhead benchmark (Plan §16 Phase C-3.2)\n")
    buf.write("=" * 90 + "\n")
    buf.write(f"timestamp_utc:   {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
    buf.write(f"git_short_sha:   {_short_git_sha()}\n")
    buf.write(f"python_version:  {platform.python_version()}\n")
    buf.write(f"platform:        {platform.platform()}\n")
    buf.write("\n")
    buf.write("INPUTS\n")
    buf.write("------\n")
    buf.write(f"seed_fixture:           {SEED_FIXTURE_DIR.relative_to(_REPO_ROOT)}\n")
    buf.write(f"target_changes:         {target_changes}\n")
    buf.write(f"scale_factor (A):       {scale_factor}\n")
    buf.write(f"scale_factor (B):       {scale_factor + 1}\n")
    buf.write(f"entity_count_a:         {instrumented['entity_count_a']}\n")
    buf.write(f"entity_count_b:         {instrumented['entity_count_b']}\n")
    buf.write(f"achieved_change_count:  {instrumented['change_count']}\n")
    buf.write(f"runs_per_pass:          {instrumented['runs']} (+1 warm-up dropped)\n")
    buf.write("\n")
    buf.write("RESULTS (trimmed mean = drop slowest+fastest, average the middle)\n")
    buf.write("------\n")
    buf.write("PRODUCTION-FAIR METRIC (wall, no cProfile context)\n")
    buf.write(
        f"  baseline_wall_mean_s:     {baseline['wall_trimmed_mean_s']:.4f}  "
        f"[min={baseline['wall_min_s']:.4f}, max={baseline['wall_max_s']:.4f}, "
        f"samples={baseline['wall_samples_s']}]\n"
    )
    buf.write(
        f"  instrumented_wall_mean_s: {instrumented['wall_trimmed_mean_s']:.4f}  "
        f"[min={instrumented['wall_min_s']:.4f}, max={instrumented['wall_max_s']:.4f}, "
        f"samples={instrumented['wall_samples_s']}]\n"
    )
    buf.write(f"  delta_pct:                {delta_pct:+.4f}%\n")
    buf.write(f"  threshold_pct:            {threshold_pct:.4f}%\n")
    buf.write(f"  verdict:                  {verdict}\n")
    buf.write("\n")
    # Worst-case bound (cProfile context).
    cp_base_mean = baseline.get("cprofile_wall_trimmed_mean_s")
    cp_instr_mean = instrumented.get("cprofile_wall_trimmed_mean_s")
    if cp_base_mean and cp_base_mean > 0 and cp_instr_mean:
        cp_delta_pct = (cp_instr_mean - cp_base_mean) / cp_base_mean * 100.0
        buf.write("WORST-CASE BOUND (wall under cProfile context — inflates per-call overhead)\n")
        buf.write(
            f"  baseline_cprofile_mean_s:     {cp_base_mean:.4f}  "
            f"[samples={baseline.get('cprofile_wall_samples_s', [])}]\n"
        )
        buf.write(
            f"  instrumented_cprofile_mean_s: {cp_instr_mean:.4f}  "
            f"[samples={instrumented.get('cprofile_wall_samples_s', [])}]\n"
        )
        buf.write(f"  cprofile_delta_pct:           {cp_delta_pct:+.4f}%  (advisory — not gated)\n")
        buf.write("\n")
        buf.write(
            "INTERPRETATION: production code does NOT run under cProfile. The wall metric\n"
            "above is the production-fair overhead. The cProfile-context delta is reported\n"
            "as an upper bound because cProfile itself amplifies per-call overhead (each\n"
            "function call carries the profiler's own bookkeeping cost — method calls cost\n"
            "more than lambdas, which inflates the apparent helper overhead).\n"
        )
        buf.write("\n")
    buf.write("INSTRUMENTED pass — top 30 by cumulative time within dxf_comparator\n")
    buf.write("------\n")
    buf.write(instrumented.get("profile_text", "") or "(no profile text captured)\n")
    buf.write("\n")
    buf.write(
        "NOTE: streaming-path overhead (time_to_first_stream_record_ms) is NOT\n"
        "measured by this benchmark — change_zone_stream_path is intentionally\n"
        "unset so _write_change_zone_stream early-exits. See the module docstring.\n"
    )
    return buf.getvalue()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument(
        "--target-changes",
        type=int,
        default=DEFAULT_TARGET_CHANGES,
        help="Approximate change count to drive into the comparator.",
    )
    parser.add_argument(
        "--max-overhead-pct",
        type=float,
        default=DEFAULT_MAX_OVERHEAD_PCT,
        help="PASS threshold for delta_pct (default 1.0%%).",
    )
    parser.add_argument(
        "--fail-on-exceed",
        dest="fail_on_exceed",
        action="store_true",
        default=True,
        help="Exit non-zero when delta_pct exceeds --max-overhead-pct.",
    )
    parser.add_argument(
        "--no-fail-on-exceed",
        dest="fail_on_exceed",
        action="store_false",
        help="Always exit 0 regardless of the verdict (for measurement-only runs).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to write the human-readable summary (default: tmp/...).",
    )
    parser.add_argument(
        "--pass",
        dest="pass_name",
        choices=["baseline", "instrumented"],
        default=None,
        help=(
            "INTERNAL — when set, run only the named pass and emit JSON to "
            "stdout. The orchestrator (no --pass) launches two subprocesses "
            "with this flag and aggregates their outputs."
        ),
    )
    parser.add_argument(
        "--scale-factor",
        type=int,
        default=None,
        help="INTERNAL — scale factor passed to the --pass child subprocess.",
    )
    args = parser.parse_args(argv)

    # ---- child subprocess mode -------------------------------------
    if args.pass_name is not None:
        scale_factor = args.scale_factor
        if scale_factor is None:
            scale_factor = _scale_factor_for_target_changes(args.target_changes)
        result = _run_pass_in_process(args.pass_name, scale_factor, args.runs)
        sys.stdout.write(json.dumps(result))
        sys.stdout.write("\n")
        return 0

    # ---- orchestrator mode -----------------------------------------
    scale_factor = _scale_factor_for_target_changes(args.target_changes)
    print(
        f"[orchestrator] target_changes={args.target_changes} "
        f"scale_factor={scale_factor} runs={args.runs}"
    )

    print("[orchestrator] launching baseline subprocess ...", flush=True)
    baseline = _subprocess_pass("baseline", scale_factor, args.runs)
    print(
        f"[orchestrator] baseline wall_mean={baseline['wall_trimmed_mean_s']:.4f}s "
        f"cprofile_mean={baseline.get('cprofile_wall_trimmed_mean_s', 0):.4f}s",
        flush=True,
    )

    print("[orchestrator] launching instrumented subprocess ...", flush=True)
    instrumented = _subprocess_pass("instrumented", scale_factor, args.runs)
    print(
        f"[orchestrator] instrumented wall_mean={instrumented['wall_trimmed_mean_s']:.4f}s "
        f"cprofile_mean={instrumented.get('cprofile_wall_trimmed_mean_s', 0):.4f}s",
        flush=True,
    )

    base_mean = baseline["wall_trimmed_mean_s"]
    instr_mean = instrumented["wall_trimmed_mean_s"]
    if base_mean <= 0:
        print(
            "ERROR: baseline mean is zero or negative; cannot compute delta",
            file=sys.stderr,
        )
        return 2
    delta_pct = (instr_mean - base_mean) / base_mean * 100.0
    passed = delta_pct <= args.max_overhead_pct
    verdict = (
        f"delta_pct={delta_pct:+.4f}% — PASS (<= {args.max_overhead_pct:.4f}%)"
        if passed
        else f"delta_pct={delta_pct:+.4f}% — FAIL (> {args.max_overhead_pct:.4f}%)"
    )

    report = _format_report(
        baseline=baseline,
        instrumented=instrumented,
        delta_pct=delta_pct,
        threshold_pct=args.max_overhead_pct,
        verdict=verdict,
        scale_factor=scale_factor,
        target_changes=args.target_changes,
    )

    out_path = args.output
    if out_path is None:
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        out_path = _REPO_ROOT / "tmp" / f"dxf_comparator_overhead_{ts}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"[orchestrator] report -> {out_path}")
    print(f"[orchestrator] {verdict}")

    if not passed and args.fail_on_exceed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
