# Collaboration Status

Last updated: 2026-06-07

## Active Work

- Current owner: Codex
- Current thread: Continue Claude Code re-origin comparison hardening
- State: implementation and runtime verification complete, uncommitted

## Current Decision

- Treat the re-origin work as a classification-quality fix, not a broad change-count reduction claim.
- In large re-origin cases, compare residual changes in registered space while preserving emitted native coordinates.
- Remove unchanged entities only when full geometry can be verified for supported entity types.
- Keep unsupported re-origin entity types as visible changes and report them through metadata.
- Apply the same conservative registered-space matching to the public canonical engine path used by CLI and Workbench automation.
- Preserve before/after native bboxes for re-origin selected-zone rendering; use side-specific crop windows instead of one shared native window.
- Restore local DXF source paths from the live compare summary when sharable viewer manifests contain `<redacted>` sources.
- Treat failed full-detail source-render upgrades as upgrade failures, not as user-visible selected-zone fallback regressions.
- Keep ODA conversion quarantined and require explicit `oda_converter` backend selection for folder-compare auto-convert.

## Verification

- `python -m py_compile src\services\comparison\folder_compare_pipeline.py src\services\comparison\dwg_converter.py src\services\comparison\dwg_dxf_fallback.py src\services\comparison\dxf_comparator.py`
- `python -m pytest tests\unit\services\comparison\test_folder_compare_pipeline.py tests\unit\services\comparison\test_auto_convert_dwg.py tests\unit\services\comparison\test_import_compare_pipeline.py -q`
- `python -m pytest tests\unit\services\comparison\test_dxf_global_alignment.py tests\unit\services\comparison\test_alignment_artifact_guard.py tests\unit\services\comparison\test_q6_structural_threshold.py tests\unit\services\comparison\test_q_fu2_alignment_layer_aware.py tests\unit\services\comparison\test_dxf_comparator_modified.py tests\unit\services\comparison\test_dxf_comparator_large_mode.py tests\unit\services\comparison\test_text_near_match_radius.py tests\unit\services\comparison\test_suppression_audit.py tests\unit\services\comparison\test_dxf_comparator_reorigin.py -q`
- `python scripts\cad_policy_gate.py`
- `git diff --check`
- `python -m py_compile src\services\comparison\drawing_compare_engine.py tests\unit\services\comparison\test_drawing_compare_engine.py`
- `python -m pytest tests\unit\services\comparison\test_drawing_compare_engine.py::test_canonical_reorigin_registered_matching_surfaces_real_changes -q`
- `python -m src.cli.cad_compare file build\manual_runtime_debug\reorigin_case\A\S-REORIGIN_REV0.dxf build\manual_runtime_debug\reorigin_case\B\S-REORIGIN_REV1.dxf --output build\manual_runtime_debug\reorigin_case\file_compare_after_fix.json --max-dxf-tokens 2000000`
- `python -m src.cli.cad_compare file build\manual_runtime_debug\reorigin_case\A\S-REORIGIN_REV0.dxf build\manual_runtime_debug\reorigin_case\B\S-REORIGIN_REV1.dxf --output build\manual_runtime_debug\reorigin_case\file_compare_computer_use_check.json --max-dxf-tokens 2000000`
- `python scripts\direct_workbench_compare.py --a build\manual_runtime_debug\reorigin_case\A --b build\manual_runtime_debug\reorigin_case\B --out-dir build\manual_runtime_debug\reorigin_case\workbench_after_fix --viewer-render-policy none`
- `python scripts\direct_workbench_compare.py --a build\manual_runtime_debug\reorigin_case\A --b build\manual_runtime_debug\reorigin_case\B --out-dir build\manual_runtime_debug\reorigin_case\workbench_top_issues_after_fix`
- Computer Use visible Workbench run via `build\manual_runtime_debug\reorigin_case\launch_visible_workbench_reorigin.py`; result summary: `build\manual_runtime_debug\reorigin_case\workbench_computer_use_visible\visible_summary.json`; UI selected `C-001` added and `C-003` modified zones.
- `python -m pytest tests\unit\services\comparison\test_drawing_compare_engine.py tests\unit\services\comparison\test_import_compare_pipeline.py tests\unit\services\comparison\test_folder_compare_pipeline.py tests\unit\services\comparison\test_dxf_comparator_reorigin.py -q`
- `python -m pytest tests\unit\services\comparison\test_workbench_phase_c.py::test_redacted_viewer_source_restores_from_compare_summary tests\unit\services\comparison\test_workbench_phase_c.py::test_failed_full_detail_upgrade_keeps_fast_crop_out_of_fallback_counts -q`
- Workbench runtime probe `build\manual_runtime_debug\reorigin_case\workbench_zone_crop_source_restore_check\zone_crop_source_restore_runtime_summary.json`: `total_changes=4`, `failed_pairs=0`, selected `C-003`, `selected_zone_fallback_count=0`, `zone_crop_count=2`, renderers `cad-background-image-crop` and `ezdxf-matplotlib-zone`, both `ready/cad_render`.
- `python -m pytest tests\unit\services\comparison\test_change_zones.py tests\unit\services\comparison\test_drawing_compare_engine.py tests\unit\services\comparison\test_zone_render_service.py tests\unit\services\comparison\test_review_dashboard.py tests\unit\services\comparison\test_workbench_phase_c.py tests\unit\services\comparison\test_import_compare_pipeline.py tests\unit\services\comparison\test_folder_compare_pipeline.py tests\unit\services\comparison\test_dxf_comparator_reorigin.py -q`

## Open Notes

- Untracked Claude/probe files remain intentionally unstaged.
- Manual runtime fixture and screenshots are under `build\manual_runtime_debug\reorigin_case\`.
- Computer Use visible run produced `workbench_computer_use_visible\results\artifacts\change_zones.json` with 4 zones: added 1, deleted 1, modified 2.
- Current re-origin engine and selected-zone render fixes are not staged or committed yet.
- Final customer-grade release evidence and full MVP closeout remain separate work.
