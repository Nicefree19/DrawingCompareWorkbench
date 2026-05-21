# -*- coding: utf-8 -*-
"""Smoke tests for ``scripts/benchmark_dxf_comparator_overhead.py``
(Plan §16 Phase C-3.2).

These tests intentionally do NOT execute the full benchmark — which
takes minutes and writes large reports. They cover the helper
functions and the ``--pass`` child-subprocess code path with the
smallest possible scale so unit CI stays fast.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest


# Make the repo root importable.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="module")
def benchmark_module():
    """Import the benchmark module once for the whole test module."""
    return importlib.import_module("scripts.benchmark_dxf_comparator_overhead")


def test_benchmark_module_imports_without_error(benchmark_module):
    """Smoke: the module imports and exposes the expected public names."""
    assert hasattr(benchmark_module, "main")
    assert hasattr(benchmark_module, "_extract_entities_from_dxf")
    assert hasattr(benchmark_module, "_inflate_entities")
    assert hasattr(benchmark_module, "_run_compare_under_cprofile")
    assert hasattr(benchmark_module, "SEED_FIXTURE_DIR")
    # SEED_FIXTURE_DIR must point at an existing fixture.
    assert benchmark_module.SEED_FIXTURE_DIR.exists(), (
        f"seed fixture missing: {benchmark_module.SEED_FIXTURE_DIR}"
    )
    assert (benchmark_module.SEED_FIXTURE_DIR / "before.dxf").exists()
    assert (benchmark_module.SEED_FIXTURE_DIR / "after.dxf").exists()


def test_extract_entities_from_seed_fixture_returns_dict_shape(benchmark_module):
    """``_extract_entities_from_dxf`` returns a dict whose values are
    lists of ``NormalizedEntity``-shaped objects with a ``hash`` field.
    """
    entities = benchmark_module._extract_entities_from_dxf(
        benchmark_module.SEED_FIXTURE_DIR / "before.dxf"
    )
    assert isinstance(entities, dict)
    assert entities, "seed must produce at least one entity type bucket"
    # At least one bucket must be non-empty (the 14_structural_submm_shift
    # seed has 6 LINE entities at the time this benchmark was written).
    non_empty = {k: v for k, v in entities.items() if v}
    assert non_empty, "every bucket was empty; seed fixture has degraded"
    # Sample one entity and verify the slotted-dataclass shape we rely on.
    sample = next(iter(non_empty.values()))[0]
    assert hasattr(sample, "hash")
    assert hasattr(sample, "entity_type")
    assert hasattr(sample, "layer")


def test_inflate_entities_scales_count_and_unique_handles(benchmark_module):
    """``_inflate_entities`` must (a) multiply the count by ``factor``
    and (b) produce globally-unique ``hash`` strings so every
    duplicated entity registers as distinct.
    """
    base = benchmark_module._extract_entities_from_dxf(
        benchmark_module.SEED_FIXTURE_DIR / "before.dxf"
    )
    original_total = sum(len(v) for v in base.values())
    inflated = benchmark_module._inflate_entities(base, factor=3, side="a")
    inflated_total = sum(len(v) for v in inflated.values())
    assert inflated_total == original_total * 3, (
        f"expected {original_total * 3} entities, got {inflated_total}"
    )
    seen_hashes = set()
    for entities in inflated.values():
        for entity in entities:
            assert entity.hash not in seen_hashes, (
                f"duplicate hash leaked through inflation: {entity.hash!r}"
            )
            seen_hashes.add(entity.hash)


def test_run_compare_under_cprofile_baseline_and_instrumented_produce_same_change_set(
    benchmark_module,
):
    """Methodology guarantee: switching helper bodies must NOT change
    ``len(result.changes)`` — only ``peak_changes_pre_truncate``
    differs. We probe at scale_factor=1 (tiny) so the test runs fast.
    """
    entities_a, entities_b = benchmark_module._build_synthetic_inputs(scale_factor=1)
    base_wall, _base_profile, base_count = benchmark_module._run_compare_under_cprofile(
        entities_a, entities_b, monkeypatch_helpers=True
    )
    instr_wall, _instr_profile, instr_count = benchmark_module._run_compare_under_cprofile(
        entities_a, entities_b, monkeypatch_helpers=False
    )
    assert base_count == instr_count, (
        f"baseline produced {base_count} changes but instrumented "
        f"produced {instr_count}; the helpers are not change-set-equivalent"
    )
    # Wall times are positive floats and sane for a sub-millisecond pass.
    assert base_wall > 0.0
    assert instr_wall > 0.0
    # cProfile output text mentions the comparator module.
    _, instr_profile_text, _ = benchmark_module._run_compare_under_cprofile(
        entities_a, entities_b, monkeypatch_helpers=False
    )
    assert "dxf_comparator" in instr_profile_text


def test_main_with_pass_flag_returns_json_to_stdout(benchmark_module):
    """``main(['--pass', 'baseline', ...])`` must emit a parsable JSON
    blob as its last stdout line. We use scale-factor=1 + runs=1 to
    keep the test under a second.
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
        rc = benchmark_module.main(
            [
                "--pass",
                "baseline",
                "--scale-factor",
                "1",
                "--runs",
                "1",
                "--target-changes",
                "10",
            ]
        )
    assert rc == 0, (
        f"main returned {rc}; stderr was:\n{stderr_buf.getvalue()}"
    )
    stdout_text = stdout_buf.getvalue().strip()
    assert stdout_text, "main(--pass=baseline) produced no stdout"
    last_line = stdout_text.splitlines()[-1]
    payload = json.loads(last_line)
    assert payload["pass"] == "baseline"
    assert payload["scale_factor"] == 1
    assert payload["runs"] == 1
    assert "wall_samples_s" in payload
    assert "wall_trimmed_mean_s" in payload
    # Plan §16 R5 follow-up: payload must carry BOTH wall-only AND
    # cProfile-context measurements so the report can show production-
    # fair delta alongside the worst-case bound.
    assert "cprofile_wall_samples_s" in payload
    assert "cprofile_wall_trimmed_mean_s" in payload
    assert len(payload["cprofile_wall_samples_s"]) == 1  # runs=1
    assert payload["cprofile_wall_trimmed_mean_s"] > 0
    assert payload["change_count"] > 0
