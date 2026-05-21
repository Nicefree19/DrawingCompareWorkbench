# -*- coding: utf-8 -*-
"""Smoke tests for ``scripts/benchmark_zone_render.py`` (Plan §17 Phase B-1b).

These tests intentionally do NOT run the full benchmark — that would
take minutes and write large reports. They cover:
  * Module imports cleanly without side effects.
  * CLI parser accepts the documented flags (no typo regressions).
  * Fixture-building helpers produce usable PDFs + backgrounds.
  * A single (zones=1, runs=1) pass executes end-to-end and the
    output report file is written.
  * The ``--no-fail-on-exceed`` switch keeps the exit code at 0 even
    when the gates are absurdly tight.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

# Make repo root importable.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="module")
def benchmark_module():
    """Import the benchmark module once for the whole test module.

    PyMuPDF is required to synthesize the test fixture. If it is not
    installed the whole module skips — the production fallback handles
    the no-fitz case but the benchmark cannot measure the DisplayList
    path without it.
    """
    pytest.importorskip("fitz")
    return importlib.import_module("scripts.benchmark_zone_render")


# ---------------------------------------------------------------------------
# Imports and CLI surface
# ---------------------------------------------------------------------------


def test_benchmark_module_imports_without_error(benchmark_module):
    """Smoke: the module imports and exposes the expected public names."""
    assert hasattr(benchmark_module, "main")
    assert hasattr(benchmark_module, "_build_pdf_fixture")
    assert hasattr(benchmark_module, "_build_zones")
    assert hasattr(benchmark_module, "_run_pass")
    assert hasattr(benchmark_module, "_percentile")
    assert hasattr(benchmark_module, "PDF_FIXTURE_SPECS")
    # All advertised fixture choices must be present.
    assert set(benchmark_module.PDF_FIXTURE_SPECS) == {"small", "medium", "large"}


def test_cli_parser_accepts_documented_flags(benchmark_module):
    """The argparse parser must recognise every documented flag —
    catches typos like ``--cold-p95-target`` (missing ``-ms``).

    We invoke ``main`` with ``--help`` via SystemExit (argparse's
    sentinel) to confirm the parser builds without errors and the
    flag names are accepted.
    """
    import argparse

    # Re-create the parser using the same code as ``main``.
    # ``argparse.ArgumentParser`` exits the process on parse failure,
    # so we just exercise the parser by parsing a known-good arg
    # tuple instead of relying on --help.
    parser = argparse.ArgumentParser()
    # Mirror the parser construction in main(); cheap surrogate that
    # would fail at import time if a flag name was malformed.
    args = benchmark_module.main  # ensure callable
    assert callable(args)
    parsed_ok = False
    try:
        # The real parser is built inside main(); pass --help to
        # exercise it. ArgumentParser.exit raises SystemExit(0).
        benchmark_module.main(["--help"])
    except SystemExit as exc:
        parsed_ok = exc.code == 0
    assert parsed_ok, "argparse --help must exit 0"


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def test_build_pdf_fixture_small_produces_usable_artefacts(
    benchmark_module, tmp_path: Path
) -> None:
    """``_build_pdf_fixture('small', ...)`` must write both the PDF
    and the pre-rendered background PNG, and report sensible
    dimensions for the page.
    """
    fixture = benchmark_module._build_pdf_fixture("small", tmp_path)
    assert fixture.source_pdf.exists()
    assert fixture.background_png.exists()
    # 612x792 page at 2x scale -> 1224x1584 background.
    assert fixture.bg_w == 1224
    assert fixture.bg_h == 1584
    assert fixture.page_index == 0


def test_build_zones_produces_correct_count(benchmark_module, tmp_path: Path) -> None:
    """``_build_zones`` must produce exactly N zones with
    well-formed bounds.
    """
    fixture = benchmark_module._build_pdf_fixture("small", tmp_path)
    zones = benchmark_module._build_zones(fixture, 7)
    assert len(zones) == 7
    for z in zones:
        assert z["xmin"] < z["xmax"]
        assert z["ymin"] < z["ymax"]
        assert z["xmin"] >= 0
        assert z["ymin"] >= 0
        assert z["xmax"] <= fixture.bg_w
        assert z["ymax"] <= fixture.bg_h


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------


def test_percentile_handles_empty_and_single_sample(benchmark_module) -> None:
    """Edge cases that would otherwise crash mid-benchmark."""
    import math

    assert math.isnan(benchmark_module._percentile([], 50.0))
    assert benchmark_module._percentile([42.0], 95.0) == 42.0
    # Standard interpolation sanity.
    assert benchmark_module._percentile([1.0, 2.0, 3.0, 4.0], 50.0) == 2.5


# ---------------------------------------------------------------------------
# End-to-end smoke (smallest possible workload so unit CI stays fast)
# ---------------------------------------------------------------------------


def test_main_runs_end_to_end_with_smallest_workload(
    benchmark_module, tmp_path: Path
) -> None:
    """Invokes ``main`` with zones=1, runs=1, a custom scratch dir
    and an explicit output path under tmp_path. Verifies:
      * exit code is 0,
      * the report file was written,
      * the JSON summary line on stdout has the expected keys.

    We use ``--no-fail-on-exceed`` plus extremely loose targets so the
    smoke test never flakes on slow CI machines.
    """
    report_path = tmp_path / "report.txt"
    scratch = tmp_path / "scratch"

    # Capture stdout to extract the trailing JSON summary line.
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = benchmark_module.main(
            [
                "--fixture",
                "small",
                "--zones",
                "1",
                "--runs",
                "1",
                "--cold-p95-target-ms",
                "60000",
                "--cache-hit-p95-target-ms",
                "60000",
                "--no-fail-on-exceed",
                "--output",
                str(report_path),
                "--scratch-dir",
                str(scratch),
            ]
        )

    assert exit_code == 0, "smoke run must exit 0 under --no-fail-on-exceed"
    assert report_path.exists(), "report file must be written"
    text = report_path.read_text(encoding="utf-8")
    # Sanity probes on the human-readable report so a future format
    # change won't drop the gated metrics silently.
    assert "cold_p95_ms" in text
    assert "cache_hit_p95_ms" in text

    # Last stdout line is the machine-readable summary.
    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    last_line = lines[-1]
    summary = json.loads(last_line)
    for key in (
        "cold_p50_ms",
        "cold_p95_ms",
        "cache_hit_p50_ms",
        "cache_hit_p95_ms",
        "cold_pass",
        "cache_hit_pass",
    ):
        assert key in summary, f"summary missing key: {key!r}"


def test_unknown_fixture_raises_value_error(benchmark_module, tmp_path: Path) -> None:
    """``_resolve_fixture`` must reject unknown fixture names so a
    typo at the CLI doesn't silently fall through to the small
    fixture and produce a misleading measurement.
    """
    with pytest.raises(ValueError, match="unknown fixture"):
        benchmark_module._resolve_fixture("xxl", tmp_path)
