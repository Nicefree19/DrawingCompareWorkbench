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
- `scripts/release_environment_check.py` was narrowed from the old Tekla/converter release gate to a Drawing Compare release gate. It now checks the extracted project root, write access, VC runtime, PySide6/PyMuPDF/ezdxf/runtime imports, ODA Converter availability, and PyInstaller presence.
- `release/` was added to `.gitignore` because full PyInstaller/customer-shareable packages are generated artifacts.

## Extracted Release Build

- `python scripts\release_environment_check.py --json-output release\environment_check.json`
  Result: PASS. ODA Converter detected at `C:\Program Files\ODA\ODAFileConverter 26.10.0\ODAFileConverter.exe`; PyInstaller detected on PATH.

- `python scripts\release_drawing_compare_workbench.py --out release\drawing_compare_extracted_build --skip-realset`
  Result: PASS. Real customer drawing validation was intentionally skipped because no external drawing set was supplied for this extraction check.

- Generated executable: `release\drawing_compare_extracted_build\dist\DrawingCompareWorkbench\DrawingCompareWorkbench.exe`
  Size: 59,912,804 bytes.

- Generated customer package: `release\drawing_compare_extracted_build\DrawingCompareWorkbench_customer_shareable.zip`
  Size: 509,037,697 bytes. SHA256: `70B1FBE84C983E8775D912C5B8CEA4C3F480DFC1A5B41EE1885C10FF76524A7D`.

- Package structure check: PASS. ZIP contains `README_INTERNAL_PILOT.md`, `customer_evidence_request_ko.md`, `app/DrawingCompareWorkbench/DrawingCompareWorkbench.exe`, `customer_package_manifest.json`, and `customer_package_path_audit.json`.

- Customer package path audit: PASS. `leak_count=0`, `disallowed_file_count=0`.

- Packaged executable diagnostics: `release\drawing_compare_extracted_build\dist\DrawingCompareWorkbench\DrawingCompareWorkbench.exe --diagnose`
  Result: PASS. Required and optional runtime dependencies were reported available: PySide6, ezdxf, PyMuPDF, Pillow, psutil, NumPy, OpenCV, rtree, matplotlib, and PyYAML.

## Next Recommended Step

Run the generated package on a clean Windows user profile or VM with ODA installed, then perform a real PDF/DWG sample comparison from the extracted build. Do not delete the original mixed TEKLA_MCP paths until that clean-environment smoke also passes.
