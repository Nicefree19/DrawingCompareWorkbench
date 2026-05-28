# Drawing Compare Workbench

Standalone drawing comparison project extracted from the mixed TEKLA_MCP
workspace.

The project keeps the historical `src.gui` and `src.services.comparison`
package paths for now so migrated behavior can be validated before deeper
package renaming.

## Run

```powershell
python start_drawing_compare_workbench.py
```

Headless file compare:

```powershell
python -m src.cli.cad_compare file old.dxf new.dxf --output build\file-diff.json
```

Headless folder compare:

```powershell
python -m src.cli.cad_compare folder old_folder new_folder --output-dir build\folder-diff
```

After editable install, the same commands are available as:

```powershell
cad-compare file old.dxf new.dxf --output build\file-diff.json
drawing-compare-workbench
```

## Validate

```powershell
python -m py_compile start_drawing_compare_workbench.py src\cli\cad_compare.py src\services\comparison\import_pipeline.py src\services\comparison\drawing_compare_engine.py src\services\comparison\folder_compare_pipeline.py src\services\comparison\dwg_differ.py src\services\comparison\dwg_diagnostics.py scripts\dwg_native_diagnostics.py
python scripts\cad_policy_gate.py
python scripts\dwg_native_diagnostics.py
python scripts\validate_real_world_dwg_samples.py
python scripts\cad_format_regression.py
python scripts\cad_performance_benchmark.py --line-counts 1000,10000 --output build\reports\cad-performance-smoke.json
python -m pytest tests\unit\cli\test_cad_compare_cli.py tests\unit\services\comparison\test_import_compare_pipeline.py tests\unit\services\comparison\test_dwg_importer.py tests\unit\services\comparison\test_dwg_native_reader.py tests\unit\services\comparison\test_dwg_diagnostics.py tests\unit\scripts\test_dwg_native_diagnostics.py -q
```

To refresh local-only real-world DWG golden snapshots after intentional changes:

```powershell
python scripts\validate_real_world_dwg_samples.py --write-golden
```

Native DWG import support is currently limited to `AC1015`. AC1024 and AC1032
have diagnostic section-map reader shells only; they still do not import
geometry. For newer real-world DWG files, run
`python scripts\dwg_native_diagnostics.py` and see
`docs/DWG_NATIVE_READER_EXTENSION_SPEC.md` before claiming expanded support.

## Policy

Default/runtime requirements exclude ODA, LibreDWG, PyMuPDF/MuPDF, and other
copyleft or proprietary CAD conversion dependencies. See
`docs/THIRD_PARTY_LICENSE_POLICY.md`.
