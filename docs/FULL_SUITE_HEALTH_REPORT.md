# Full-Suite Health Report

## Current HEAD Refresh - 2026-07-08 (GREEN — full-suite health closed)

Status: **full-suite health is GREEN for the current PR HEAD**
(`fix/full-suite-remediation-and-p5-research`, PR #68). This closes the
full-suite freshness + health remediation blocker; it lands on `main` when
PR #68 merges.

| Evidence | Value |
|---|---|
| Workflow | `.github/workflows/full-suite-health.yml` (`workflow_dispatch`) |
| GitHub run (Full Suite Health) | `28908912548` — conclusion `success`, exit `0` |
| URL | `https://github.com/Nicefree19/DrawingCompareWorkbench/actions/runs/28908912548` |
| Branch / PR | `fix/full-suite-remediation-and-p5-research` / #68 |
| Target headSha | `2b10eaadce6d5ce26570243d403d9565078b4865` (branch tip, post-formatting) |
| Local JUnit artifact | `build\reports\full-suite-health_28908912548\full-suite-junit.xml` |
| Counts | `4,227` JUnit cases: `4,183 passed / 0 failed / 0 errors / 44 skipped` |
| CAD Format Regression (PR gate) | run `28908914295` on the same commit — `success` (black+isort lint of changed `.py` + CAD importer/normalizer/writer/diff subset) |
| Prior full-suite green | run `28908447851` on `5653b6c` (pre-formatting) — also `success`, identical counts |

How the 11 failures of run `28323513823` were closed:

| Failure group (count) | Resolution | Commit |
|---|---|---|
| `test_structural_review_draft_composer` `FileNotFoundError` (4) | Recovered `docs/schemas/structural-review-draft-v0.1.schema.json` from **dangling commit `f5cdfd0`** (never merged to any ref, so `main` and the working tree both lacked it) and tracked it via the existing `.gitignore` exception. | `4f02a70` |
| `test_pdf_lightweight_hardening` mock (2) | Mock viewport given the required `_color_mode = "light"`. Test-only. | `4f02a70` |
| `test_dwg_differ_cleanup` path alias (2) | Compare the same filesystem path via `Path.samefile()` (runner `runneradmin` vs `RUNNER~1`). Test-only. | `4f02a70` |
| `test_korean_workbench_ux` stale wording (2) | Assertions realigned to the post-extraction module / README contract. Test-only. | `4f02a70` |
| `test_01_dwg_converter_path` env (1) | `skipUnless(_find_installed_oda_converter())` — SKIPS when the ODA binary is absent (GitHub runner), still RUNS+asserts where installed. | `5653b6c` |

Progression: run `28907534076` (HEAD `1a2fabd`, before the skip fix) already
reduced the 11 failures to a single one (`4,183 passed / 1 failed`), confirming
the schema + drift repairs landed; run `28908447851` (`5653b6c`) closed the last
one to `0 failed`; and after black+isort formatting (`2b10eaa`, to satisfy the
CAD Format Regression lint gate) the final run `28908912548` re-confirmed
`0 failed` with the CAD Format Regression gate also green on the same commit.

Product behavior unchanged: every change is a test / schema / gitignore /
formatting change; no `src/` logic was modified.

## Current HEAD Refresh - 2026-06-28

Status: **fresh current-HEAD evidence recorded; full-suite health is not green**.

| Evidence | Value |
|---|---|
| Workflow | `.github/workflows/full-suite-health.yml` (`workflow_dispatch` on `main`) |
| GitHub run | `28323513823` |
| Job | `83909487801` (`full-suite`) |
| URL | `https://github.com/Nicefree19/DrawingCompareWorkbench/actions/runs/28323513823` |
| Target headSha | `6b13a395a4fcf788027f316f3ea89a969f916c5b` |
| Run window | Created `2026-06-28T13:18:08Z`; completed `2026-06-28T13:24:11Z` |
| Conclusion / exit | GitHub conclusion `failure`; pytest step completed with exit code `1` |
| Local JUnit artifact | `build\reports\full-suite-health_28323513823\full-suite-junit.xml` |
| Saved job log | `.harness\full_suite_freshness_20260628\run_28323513823_full_suite_job.log` |
| Counts | `4,204` JUnit cases: `4,162 passed / 11 failed / 31 skipped / 0 errors`; log summary: `11 failed, 4162 passed, 31 skipped in 220.37s (0:03:40)` |

This refresh supersedes the 2026-06-10 run for **freshness only**. It does not
claim release readiness, customer-grade completion, or green full-suite health.

Current failure set from the downloaded JUnit artifact:

| Area | Failing tests / evidence |
|---|---|
| DWG sprint converter path | `tests/test_sprint_9_dwg.py::TestSprint9Core::test_01_dwg_converter_path` (`AssertionError: False is not true`) |
| PDF lightweight hardening mock contract | `tests/unit/gui/test_pdf_lightweight_hardening.py::TestA4PdfPathRaceGuard::test_load_scene_pack_success_clears_stale_pdf_background`; `test_load_scene_pack_records_native_source_provenance` (`Mock object has no attribute '_color_mode'`) |
| Windows short-vs-long temp path handling | `tests/unit/services/comparison/test_dwg_differ_cleanup.py::TestDwgDifferCleanup::test_dxf_conversion_cache_reuses_existing_output`; `test_dxf_cache_can_reuse_same_stem_when_exact_key_changes` (`runneradmin` vs `RUNNER~1` path mismatch) |
| Stale source-text UX guards | `tests/unit/services/comparison/test_korean_workbench_ux.py::test_workbench_caps_immediate_qml_overlay_load_for_responsiveness`; `test_workbench_backend_status_describes_native_dwg_scope_without_oda_requirement` (expected literals are not present in `drawing_compare_workbench.py`) |
| Missing schema in GitHub checkout | Four `tests/unit/services/comparison/test_structural_review_draft_composer.py` tests fail with `FileNotFoundError` for `docs\schemas\structural-review-draft-v0.1.schema.json` |

Next closure condition: remediate or explicitly retire the 11 failures, rerun
the same full-suite workflow for the then-current HEAD, and record a new JUnit
artifact plus exit code.

## Historical Baseline - 2026-06-10

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
