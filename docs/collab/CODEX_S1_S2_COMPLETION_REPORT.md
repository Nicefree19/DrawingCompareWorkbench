# Codex S1/S2 Completion Report

Date: 2026-06-01

## S1 Deliverables

- `src/gui/lightweight_viewport.py`: wired the `QSGLineItem unavailable` fallback log through `log_once`.
- `tests/unit/gui/test_lightweight_viewport_failure_codes.py`: added first-INFO/subsequent-DEBUG regression coverage.
- Existing `src/utils/once_per_session_logger.py` helper remains the throttle mechanism.

## S1 Validation

- `python -m py_compile src/gui/lightweight_viewport.py tests/unit/gui/test_lightweight_viewport_failure_codes.py` -> passed
- `python -m pytest tests/unit/utils/test_once_per_session_logger.py tests/unit/gui/test_lightweight_viewport_failure_codes.py -q` -> 13 passed

## S1 Silent Fallbacks

- Verified active silent fallback surfaced in this slice: `backend_fallback_canvas_skeleton` for missing optional `QSGLineItem`.
- Repeated `QSGLineItem unavailable` notices now emit once at INFO and then at DEBUG with the throttle marker.

## S2 Deliverables

- `src/services/comparison/sheet_match_metrics.py`: precision, recall, F1, false-match, unmatched, manual-review, confidence-distribution metrics.
- `scripts/build_multi_sheet_fixtures.py`: synthetic 2/3/5-sheet DXF fixture and ground-truth manifest generator.
- `scripts/benchmark_sheet_match_accuracy.py`: synthetic benchmark CLI for the `sheet_match_*` namespace.
- `tests/unit/services/comparison/test_sheet_match_metrics.py`: metric and synthetic benchmark regression coverage.
- `tests/data/multi_sheet/`: generated synthetic fixture set and `multi_sheet_ground_truth.json`.

## S2 Validation

- `python -m py_compile src/services/comparison/sheet_match_metrics.py scripts/build_multi_sheet_fixtures.py scripts/benchmark_sheet_match_accuracy.py` -> passed
- `python -m pytest tests/unit/services/comparison/test_sheet_match_metrics.py -q` -> 5 passed
- `python scripts/build_multi_sheet_fixtures.py --out tests/data/multi_sheet` -> generated 3 synthetic fixtures
- `python scripts/benchmark_sheet_match_accuracy.py --fixture-root tests/data/multi_sheet --out .benchmarks/sheet_match_accuracy_synthetic.json` -> passed

## S2 Synthetic Metrics

- precision: 1.0
- recall: 1.0
- f1: 1.0
- false_match_count: 0
- manual_match_required_count: 2
- message: `ready to gate real fixtures`

## Direct Compare Follow-up

- Added `scripts/direct_workbench_compare.py` to repeat the Workbench-backed compare without relying on Windows UI text input.
- Windows UI automation captured the Qt Workbench window, but line edits/file-dialog activation did not accept automated input in this environment.
- `python scripts/direct_workbench_compare.py --a tests/data/multi_sheet/5sheets_one_renamed.dxf --b tests/data/multi_sheet/5sheets_one_renamed_after.dxf --out-dir out/direct_compare_script_5sheets` -> completed.
- Direct result: completed_pairs 1, failed_pairs 0, total_changes 1, zone_count 1.
- Review evidence screenshots:
  - `out/direct_compare_script_5sheets/screenshots/01_workbench_result_loaded.png`
  - `out/direct_compare_script_5sheets/screenshots/02_first_zone_selected.png`
- Detected change: `S25-0005R`, zone `C-001`, medium severity, `TITLE`/`text`, bbox `2012.8,-21.0,2122.8,89.0`.

## S2 Actual Region Metrics

- Added `scripts/score_region_sheet_match.py` to map `region_match_summary.json` frame ids back to fixture sheet ids and score them through `compute_sheet_match_metrics`.
- Added `tests/unit/scripts/test_score_region_sheet_match.py`.
- `python scripts/score_region_sheet_match.py --region-summary out/direct_compare_script_5sheets/results/artifacts/region_match_summary.json --ground-truth tests/data/multi_sheet/multi_sheet_ground_truth.json --fixture-name 5sheets_one_renamed --out out/direct_compare_script_5sheets/sheet_match_metrics_region.json` -> passed.
- Actual direct-run metrics: precision 1.0, recall 1.0, f1 1.0, false_match_count 0, manual_match_required_count 1, prediction_count 4.

## Focused Regression

- `python -m pytest tests/unit/services/comparison/test_viewer_package.py tests/unit/services/comparison/test_cad_pdf_alignment.py tests/unit/services/comparison/test_viewer_manifest_v3.py tests/unit/services/comparison/test_sheet_match_metrics.py tests/unit/utils/test_once_per_session_logger.py tests/unit/gui/test_lightweight_viewport_failure_codes.py tests/unit/services/comparison/test_render_failure_codes.py tests/unit/gui/test_failure_badge.py -q` -> 123 passed
- `python -m py_compile scripts/direct_workbench_compare.py scripts/score_region_sheet_match.py` -> passed
- `python -m pytest tests/unit/scripts/test_score_region_sheet_match.py tests/unit/services/comparison/test_sheet_match_metrics.py -q` -> 6 passed
- `python scripts/cad_policy_gate.py` -> passed
- `git diff --check` -> passed
- `python -m py_compile src/services/comparison/viewer_package.py src/services/comparison/sheet_match_metrics.py src/gui/lightweight_viewport.py scripts/build_multi_sheet_fixtures.py scripts/benchmark_sheet_match_accuracy.py` -> passed

## Remaining

- Synthetic S2 results and the direct 5-sheet fixture are not customer-grade evidence; real multi-sheet fixtures are still needed before release claims.
- `drawing_compare_workbench.py` changed lines: 0. Current file line count observed in this run: 14,088.
