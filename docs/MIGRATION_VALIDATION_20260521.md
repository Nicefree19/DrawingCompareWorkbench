# Drawing Compare Workbench Migration Validation

Date: 2026-05-21 KST

## Source

- Source root: `D:\00.Work_AI_Tool\02.TEKLA_MCP\.claude\worktrees\audit-gates`
- Strategy: copy-first extraction, preserving existing `src.gui` and `src.services.comparison` import paths.
- Target root: `D:\00.Work_AI_Tool\DrawingCompareWorkbench`

## Validation Results

- `python -m py_compile start_drawing_compare_workbench.py scripts\release_drawing_compare_workbench.py scripts\validate_drawing_compare_realset.py scripts\audit_drawing_compare_mvp_exit.py scripts\inventory_drawing_compare_customer_evidence.py scripts\prepare_drawing_compare_customer_evidence.py scripts\workbench_acceptance_smoke.py src\gui\drawing_compare_workbench.py src\gui\lightweight_viewport.py`  
  Result: PASS

- `python -m pytest tests\unit\services\comparison -q --tb=short --disable-warnings --maxfail=20 -o log_cli=false --capture=sys`  
  Result: PASS, 2551 passed / 2 skipped

- `python -m pytest tests\integration\services\comparison -q --tb=short --disable-warnings -o log_cli=false --capture=sys`  
  Result: PASS, 4 passed / 1 skipped

- `python -m pytest tests\unit\gui\test_drawing_compare_cache.py tests\unit\gui\test_compare_worker_noise_filter.py tests\unit\gui\test_drawing_comparison_viewer_behaviors.py tests\unit\gui\test_pdf_lightweight_hardening.py tests\unit\gui\test_workbench_ai_prepare.py -q --tb=short --disable-warnings -o log_cli=false --capture=sys`  
  Result: PASS, 61 passed

## Adjustments Made During Migration

- `src/core` was trimmed to `__init__.py`, `error_handler.py`, and `runtime_diagnostics.py`.
- `src/utils` was trimmed to `__init__.py` and `security_validators.py`; the package initializer no longer imports MGT parser helpers.
- `scripts/verify_dxf_plot_ready.py`, `docs/api/DXF_PARSER.md`, and `docs/TECHSPEC_DXF_PLOT_READY.md` were excluded because they depend on non-drawing-compare modules.

## Next Recommended Step

Initialize a separate Git repository or worktree for this extracted folder, then perform a second-pass dependency trim. Do not delete the original mixed TEKLA_MCP paths until one full release package is successfully built from this extracted folder.
