# Collaboration Status

Last updated: 2026-06-08

## Active Work

- Current owner: Codex
- Current thread: Integrate Claude visual/PVH work onto latest re-origin main
- Branch: `codex/integrate-claude-p0-visuals`
- State: post-push runtime review and lightweight PDF rerender hardening complete, uncommitted

## Current Decision

- Claude PR `#3` is already merged into `main`; the missing work was the local branch `feat/p0-reliability-and-pvh-viewer`.
- Do not merge that branch wholesale. Its older `e5b7d78` alignment/auto-register path conflicts with the newer `main` re-origin fixes.
- Preserve current `main` re-origin behavior in `dxf_comparator.py`, `global_alignment.py`, `drawing_compare_engine.py`, and the selected-zone source/crop restoration path.
- Port the user-visible Claude visual work: reference-PDF overlay, shared lightweight camera frame, layer-filtered extents, minimum visible cloud footprint, oversized leader-line rendering, and geometry-aware cloud overlays.
- Keep `src/gui/drawing_compare_workbench.py` changes narrow by moving visual hook logic into `src/gui/workbench_visual_extensions.py` and AC1032 runtime diagnostics into `src/gui/compare_runtime_diagnostics.py`.
- Direct AC1032 DWG files still need converted DXF fallback or an explicitly configured converter; this PC currently reports native DWG support limited to AC1015 and ODA unavailable.

## Verification

- `python -m py_compile src\gui\workbench_visual_extensions.py src\gui\hybrid_reference_pdf.py src\gui\lightweight_viewport.py src\gui\drawing_compare_workbench.py src\services\comparison\cad_pdf_overlay.py src\services\comparison\layer_filter.py src\services\comparison\viewer_frame.py src\services\comparison\change_zones.py src\services\comparison\drawing_normalizer.py src\services\comparison\dxf_renderer.py src\services\comparison\review_project.py src\services\comparison\viewer_package.py`
- `python -m pytest tests\unit\services\comparison\test_cad_pdf_overlay.py tests\unit\services\comparison\test_viewer_frame_p0_3.py tests\unit\services\comparison\test_p0_reliability_harness.py tests\unit\services\comparison\test_change_zones.py tests\unit\services\comparison\test_workbench_overlay_model.py tests\unit\gui\test_pdf_lightweight_hardening.py -q`
- `python -m pytest tests\unit\services\comparison\test_dxf_comparator_reorigin.py tests\unit\services\comparison\test_drawing_compare_engine.py tests\unit\services\comparison\test_import_compare_pipeline.py tests\unit\services\comparison\test_folder_compare_pipeline.py tests\unit\services\comparison\test_workbench_phase_c.py::test_redacted_viewer_source_restores_from_compare_summary tests\unit\services\comparison\test_workbench_phase_c.py::test_failed_full_detail_upgrade_keeps_fast_crop_out_of_fallback_counts -q`
- `python scripts\cad_policy_gate.py`
- `git diff --check`
- `DRAWING_COMPARE_SMOKE_EXIT_MS=2000 python -u start_drawing_compare_workbench.py`
- Visible Workbench run from `codex/integrate-claude-p0-visuals`: `logs\runtime_monitor\integration_20260607_231756\run_manifest.json`; PID `476040`, window title `도면 변경 비교`, responding `true`, new app error bytes `0` before it was stopped for the final smoke checks.
- `python -m py_compile src\gui\compare_runtime_diagnostics.py src\gui\drawing_compare_workbench.py`
- `python -m pytest tests\unit\gui\test_compare_runtime_diagnostics.py tests\unit\services\comparison\test_dwg_dxf_fallback.py -q`
- `python -m pytest tests\unit\gui\test_compare_runtime_diagnostics.py tests\unit\services\comparison\test_folder_compare_pipeline.py::test_folder_compare_pipeline_uses_converted_dxf_fallback_for_unsupported_dwg_folder tests\unit\services\comparison\test_import_compare_pipeline.py::test_oda_fallback_is_disabled_by_default_for_dwg_failures -q`
- Final smoke after runtime fix: `DRAWING_COMPARE_SMOKE_EXIT_MS=2000 python -u start_drawing_compare_workbench.py`, exit code 0, `logs\error_20260607.log` delta 0.
- 2026-06-08 smoke: `DRAWING_COMPARE_SMOKE_EXIT_MS=2500 python -u start_drawing_compare_workbench.py`, exit code 0, `logs\error_20260608.log` delta 0.
- 2026-06-08 visible UI run: `logs\runtime_monitor\codex_verify_20260608_000349\run_manifest.json`, PID `550808`, window title `도면 변경 비교`, responding `true`, app error delta 0.
- `python -m pytest tests\unit\gui\test_pdf_lightweight_hardening.py::TestQ2AutoZoomCameraLock tests\unit\services\comparison\test_zone_focus_pdf_rerender.py -q`
- Post-hardening smoke: `DRAWING_COMPARE_SMOKE_EXIT_MS=2500 python -u start_drawing_compare_workbench.py`, exit code 0, `logs\error_20260608.log` delta 0.
- Post-hardening visible UI run: `logs\runtime_monitor\codex_verify_postfix_20260608_000808\run_manifest.json`, PID `556284`, window title `도면 변경 비교`, responding `true`, app error delta 0.
- `python -m pytest tests\unit\gui\test_pdf_lightweight_hardening.py tests\unit\services\comparison\test_zone_focus_pdf_rerender.py tests\unit\gui\test_compare_runtime_diagnostics.py -q`

## Open Notes

- Changes are intentionally not staged or committed yet.
- Existing untracked Claude/probe files remain intentionally untouched.
- `logs\error_20260607.log` contains unsupported-AC1032 preflight errors from direct DWG file runs; the subsequent folder run completed through converted DXF fallback.
- Current visible 2026-06-08 run remains open for inspection at PID `556284`.
