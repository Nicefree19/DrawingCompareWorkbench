# Collaboration Status

Last updated: 2026-06-07

## Active Work

- Current owner: Codex
- Current thread: Continue Claude Code re-origin comparison hardening
- State: implementation complete, staged/commit requested

## Current Decision

- Treat the re-origin work as a classification-quality fix, not a broad change-count reduction claim.
- In large re-origin cases, compare residual changes in registered space while preserving emitted native coordinates.
- Remove unchanged entities only when full geometry can be verified for supported entity types.
- Keep unsupported re-origin entity types as visible changes and report them through metadata.
- Keep ODA conversion quarantined and require explicit `oda_converter` backend selection for folder-compare auto-convert.

## Verification

- `python -m py_compile src\services\comparison\folder_compare_pipeline.py src\services\comparison\dwg_converter.py src\services\comparison\dwg_dxf_fallback.py src\services\comparison\dxf_comparator.py`
- `python -m pytest tests\unit\services\comparison\test_folder_compare_pipeline.py tests\unit\services\comparison\test_auto_convert_dwg.py tests\unit\services\comparison\test_import_compare_pipeline.py -q`
- `python -m pytest tests\unit\services\comparison\test_dxf_global_alignment.py tests\unit\services\comparison\test_alignment_artifact_guard.py tests\unit\services\comparison\test_q6_structural_threshold.py tests\unit\services\comparison\test_q_fu2_alignment_layer_aware.py tests\unit\services\comparison\test_dxf_comparator_modified.py tests\unit\services\comparison\test_dxf_comparator_large_mode.py tests\unit\services\comparison\test_text_near_match_radius.py tests\unit\services\comparison\test_suppression_audit.py tests\unit\services\comparison\test_dxf_comparator_reorigin.py -q`
- `python scripts\cad_policy_gate.py`
- `git diff --check`

## Open Notes

- Untracked Claude/probe files remain intentionally unstaged.
- Final customer-grade release evidence and full MVP closeout remain separate work.
