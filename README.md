# Drawing Compare Workbench

This folder is a copy-first extraction of the Drawing Compare Workbench from the mixed TEKLA_MCP workspace.

The first migration stage intentionally preserves the existing `src.gui` and `src.services.comparison` import paths so behavior can be validated before package renaming or deeper refactoring.

## Validate

```powershell
python -m py_compile start_drawing_compare_workbench.py scripts\release_drawing_compare_workbench.py scripts\validate_drawing_compare_realset.py scripts\audit_drawing_compare_mvp_exit.py scripts\inventory_drawing_compare_customer_evidence.py scripts\prepare_drawing_compare_customer_evidence.py scripts\workbench_acceptance_smoke.py
python -m pytest tests\unit\services\comparison -q
python -m pytest tests\integration\services\comparison -q
```

See `docs/MIGRATION_MANIFEST.drawing_compare.json` for the source branch, commit, copied paths, and excluded scope.
