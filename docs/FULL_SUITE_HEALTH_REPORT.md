# Full-Suite Health Report — 2026-06-10 (overnight run)

First documented full-suite execution + failure classification. Until now the
full suite was never run in one process (every documented command in README /
STATUS is a hand-picked subset) and CI runs only 12 files (~2.7% of tests), so
suite-wide health was unknown.

## Headline

| run | result |
|---|---|
| Collection | **3,872 tests collected, 0 errors** |
| Serial in-process (`pytest -q`) | **aborts before summary** — native `Windows fatal exception: access violation` in QML viewport construction late in the run |
| Parallel (`pytest -n auto`), before fixes | 3,864 passed / **5 failed** / 3 skipped (2 of the 5 = worker-crash casualties of the same QML fault) |
| Parallel (`pytest -n auto`), **after fixes** | **3,869 passed / 0 failed / 3 skipped, exit 0, no worker crashes** |

Reproduce: `python -m pytest -p no:cacheprovider -n auto -q -o log_cli=false -rfE`

## The 5 failures, classified (isolate-rerun discipline)

| test | classification | resolution |
|---|---|---|
| `test_phase_c_audit_chain::test_validation_summary_contains_runtime_budget_with_comparator_metrics` | **Real regression** — `peak_comparator_changes=None` | Fixed (`431a33f`): the canonical DEFAULT compare path never set `metadata["peak_changes_pre_truncate"]` (legacy-comparator-only plumbing), so the release audit gate's large-drawing change bound silently could not be enforced |
| `test_phase_c_audit_chain::test_audit_gate_passes_when_peak_comparator_changes_under_threshold` | Same root cause | Fixed by the same commit |
| `test_sprint_9_dwg::test_05_dwg_differ_integration` | **Stale test debt** — asserted pre-ODA-free behavior (path present → converter auto-loads) | Fixed (`20d0d49`): converted into an ODA-free policy guard (converter must stay `None` without explicit `allow_oda_fallback` opt-in) |
| `test_workbench_phase_c::test_zoom_slider_change_updates_label` | xdist worker crashed (QML fault below) | Intermittent — passed in the confirming run |
| `test_workbench_phase_c::test_full_detail_upgrade_fires_once_and_respects_busy_and_pending` | xdist worker crashed (QML fault below) | Intermittent — passed in the confirming run |

All three logical failures were verified REAL by serial isolated re-runs
before fixing (not xdist artifacts). They had rotted invisibly because the
full suite is never executed and CI covers ~2.7% of tests.

## The native QML crash (test-harness, not production)

- Site: `lightweight_viewport.py` `__init__` → `QQuickWidget.setSource`
  (observed via faulthandler in `test_workbench_phase_c` tests).
- Behavior: single test passes; the whole 55-test file passes alone; the
  crash appears only deep into a full-suite run — accumulated native Qt/QML
  state on the session-scoped `QApplication` (thousands of widget
  constructions in one process). Intermittent under xdist (run 1: 2 worker
  crashes; run 2: 0).
- Production risk: **low** — the real app never constructs thousands of
  viewports in one process. This is a test-harness ceiling.
- Practical mitigation: run the full suite with `-n auto` (pytest-xdist is
  installed; `pytest.ini` line 58 already carries a commented `-n auto`).
  Worker isolation both contains a crash to one worker and usually keeps each
  worker under the accumulation threshold.

## Skips (3) — environment-conditional

rtree not installed / optional backends absent; see test-level skip reasons.
None hide product defects.

## Recommendations (not applied here)

1. **CI: run the full suite with `-n auto`** instead of the 12-file subset —
   this report is direct evidence of what the 2.7% gate misses (a release-gate
   metric regression rotted undetected).
2. Keep `-n auto` opt-in for local focused runs (don't force it via
   `addopts`); document the full-suite command above as the standard health
   check.
3. If the QML accumulation crash starts biting under xdist regularly,
   consider `--dist loadgroup` + grouping `test_workbench_phase_c` GUI tests,
   or a per-file fresh-process runner for GUI-heavy files.
