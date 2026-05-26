# Drawing Compare Operation Cleanup

Date prepared: 2026-05-22

## Scope

This note separates the current dirty worktree into commit-safe groups so the
Drawing Compare region-aware work is not mixed with Claude's CAD importer work.

## Current Source Of Truth

- Working folder: `D:\00.Work_AI_Tool\DrawingCompareWorkbench`
- Original mixed worktree `C:\Users\user\.codex\worktrees\45ea\02.TEKLA_MCP` is not used for new implementation.
- No `docs/collab/WORKLOG.md` or `docs/collab/STATUS.md` exists in this extracted folder, so no append/update was made there.

## Commit Group A - Region-Aware Viewer/Compare MVP

Use this group for the latest work on one-sided zone UX, detail-region detection,
region matching, localized compare summaries, and pipeline side-car outputs.

```powershell
git add -- `
  src/gui/assets/drawing_compare/LightweightDrawingViewport.qml `
  src/gui/drawing_compare_workbench.py `
  src/gui/lightweight_viewport.py `
  src/services/comparison/folder_compare_pipeline.py `
  src/services/comparison/sheet_region_detector.py `
  src/services/comparison/detail_region_matcher.py `
  src/services/comparison/localized_compare.py `
  tests/unit/services/comparison/test_region_aware_compare.py `
  tests/unit/services/comparison/test_korean_workbench_ux.py `
  docs/collab/DRAWING_COMPARE_OPERATION_CLEANUP.md
```

Note: `LightweightDrawingViewport.qml`, `drawing_compare_workbench.py`, and
`lightweight_viewport.py` also contain earlier viewer hardening changes. If a
pure region-aware-only commit is required, stage these files with `git add -p`
instead of full-file staging.

## Commit Group B - Prior Preview/Renderer Regression Fixes

Keep this separate if the goal is to preserve the earlier fixes for blank PDF/DWG
preview and DXF fallback extents.

```powershell
git add -- `
  src/services/comparison/dxf_renderer.py `
  tests/unit/services/comparison/test_dxf_renderer_backends.py `
  tests/unit/gui/test_pdf_lightweight_hardening.py
```

## Commit Group C - Claude CAD Importer / Format Track

Do not mix this with Group A unless intentionally merging the canonical CAD
importer track.

```powershell
git add -- `
  .gitignore `
  .github/workflows/cad-format-regression.yml `
  docs/CAD_FORMAT_REGRESSION_REPORT.md `
  docs/CAD_FORMAT_SUPPORT_POLICY.md `
  docs/CAD_PERFORMANCE_OPTIMIZATION_REPORT.md `
  docs/ENTITY_SUPPORT_MATRIX.md `
  docs/THIRD_PARTY_LICENSE_POLICY.md `
  docs/canonical-drawing.schema.json `
  docs/canonical-entity-spec.md `
  docs/drawing-diff.schema.json `
  docs/error-code-spec.md `
  docs/normalization-tolerance-policy.md `
  scripts/cad_format_regression.py `
  scripts/cad_performance_benchmark.py `
  src/services/comparison/__init__.py `
  src/services/comparison/cad_stability.py `
  src/services/comparison/dwg_differ.py `
  src/services/comparison/drawing_compare_engine.py `
  src/services/comparison/drawing_normalizer.py `
  src/services/comparison/dwg_importer.py `
  src/services/comparison/dxf_importer.py `
  src/services/comparison/dxf_writer.py `
  src/services/comparison/import_pipeline.py `
  tests/data/comparison/cad_samples `
  tests/data/comparison/dxf_writer `
  tests/unit/services/comparison/test_cad_format_regression.py `
  tests/unit/services/comparison/test_cad_stability_limits.py `
  tests/unit/services/comparison/test_drawing_compare_engine.py `
  tests/unit/services/comparison/test_drawing_normalizer.py `
  tests/unit/services/comparison/test_dwg_importer.py `
  tests/unit/services/comparison/test_dxf_importer.py `
  tests/unit/services/comparison/test_dxf_writer.py `
  tests/unit/services/comparison/test_import_compare_pipeline.py
```

## Validation Already Run

```powershell
python -m py_compile `
  src/services/comparison/sheet_region_detector.py `
  src/services/comparison/detail_region_matcher.py `
  src/services/comparison/localized_compare.py `
  src/services/comparison/folder_compare_pipeline.py `
  src/gui/lightweight_viewport.py `
  src/gui/drawing_compare_workbench.py

git diff --check -- `
  src/services/comparison/sheet_region_detector.py `
  src/services/comparison/detail_region_matcher.py `
  src/services/comparison/localized_compare.py `
  src/services/comparison/folder_compare_pipeline.py `
  src/gui/lightweight_viewport.py `
  src/gui/drawing_compare_workbench.py `
  src/gui/assets/drawing_compare/LightweightDrawingViewport.qml `
  tests/unit/services/comparison/test_region_aware_compare.py `
  tests/unit/services/comparison/test_korean_workbench_ux.py

python -m pytest `
  tests/unit/services/comparison/test_dxf_renderer_backends.py `
  tests/unit/services/comparison/test_zone_render_service.py `
  tests/unit/services/comparison/test_viewer_perf_summary.py `
  tests/unit/gui/test_pdf_lightweight_hardening.py `
  tests/unit/services/comparison/test_korean_workbench_ux.py `
  tests/unit/services/comparison/test_region_aware_compare.py `
  -q --log-cli-level=CRITICAL
```

Result: `73 passed`; `py_compile` passed; `git diff --check` passed.

## Remaining Operational Rule

Do not stage or commit Group C with Group A unless the user explicitly wants a
combined CAD importer + region-aware compare commit.
