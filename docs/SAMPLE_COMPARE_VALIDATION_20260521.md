# Drawing Compare Sample Validation

Date: 2026-05-21 KST

This records the first sample compare runs executed from the extracted
`D:\00.Work_AI_Tool\DrawingCompareWorkbench` repository after the standalone
release build passed.

## Repository Baseline

- Baseline commit: `a29ab48 chore: extract drawing compare workbench baseline`
- Release validation commit: `ae034d1 chore: validate extracted release build`
- Generated artifacts are under `release/`, which is intentionally ignored by Git.

## Commands

### DXF Golden Pair

```powershell
python scripts\validate_drawing_compare_realset.py `
  --a tests\data\comparison\golden\dxf\02_single_modification\before.dxf `
  --b tests\data\comparison\golden\dxf\02_single_modification\after.dxf `
  --out release\sample_compare_dxf_02_manual `
  --manual-matches release\sample_inputs\manual_matches_dxf_02.csv `
  --export-profile sharable `
  --change-zone-report `
  --executive-review `
  --review-dashboard `
  --export-viewer-package `
  --viewer-render-policy top-issues `
  --viewer-perf-log `
  --render-selected-zone-evidence `
  --selected-zone-evidence-per-pair 1 `
  --export-cloud-marks `
  --cloud-export-mode all `
  --measure-runtime-budget `
  --quality-gate `
  --max-workers 1
```

Result: PASS.

- `_SUCCESS`: present
- `quality_gate.status`: `passed`
- completed pairs: 1
- total changes: 2
- change zones: 1
- review dashboard structural-core issues: 1 member issue
- selected-zone evidence: `passed`
- cold selected-zone render: 214.899 ms
- cache-hit selected-zone render: 1.259 ms
- generated cloud-marked DXF files: 1
- total runtime: 1.850 s

Note: the first DXF run without `--manual-matches` produced artifacts but failed
the requested quality gate because `before.dxf` and `after.dxf` were only
classified as a review-required match. For same-drawing files with generic names,
the operator should supply a manual matches CSV or use clearer drawing names.

### PDF Sample Pair

```powershell
python scripts\validate_drawing_compare_realset.py `
  --a "D:\00.Work_AI_Tool\02.TEKLA_MCP\test_data\e2e\drawing\plan_rev0.pdf" `
  --b "D:\00.Work_AI_Tool\02.TEKLA_MCP\test_data\e2e\drawing\plan_rev1.pdf" `
  --out release\sample_compare_pdf_plan `
  --export-profile sharable `
  --change-zone-report `
  --executive-review `
  --review-dashboard `
  --export-viewer-package `
  --viewer-render-policy top-issues `
  --viewer-perf-log `
  --render-selected-zone-evidence `
  --selected-zone-evidence-per-pair 2 `
  --export-marked-pdf `
  --marked-pdf-mode all `
  --measure-runtime-budget `
  --quality-gate `
  --max-workers 1
```

Result: PASS.

- `_SUCCESS`: present
- `quality_gate.status`: `passed`
- completed pairs: 1
- total changes: 16
- change zones: 1
- review dashboard structural-core issues: 1 dimension issue
- selected-zone evidence: `passed`
- cold selected-zone render: 211.385 ms
- cache-hit selected-zone render: 1.441 ms
- generated marked PDFs: 1
- total runtime: 1.669 s

### DWG Sample Pair

```powershell
python scripts\validate_drawing_compare_realset.py `
  --a "D:\00.Work_AI_Tool\07.Dwg_diff\도면비교\1.dwg" `
  --b "D:\00.Work_AI_Tool\07.Dwg_diff\도면비교\2.dwg" `
  --out release\sample_compare_dwg_1_2 `
  --manual-matches release\sample_inputs\manual_matches_dwg_1_2.csv `
  --dxf-cache-dir release\sample_dwg_cache `
  --export-profile sharable `
  --change-zone-report `
  --executive-review `
  --review-dashboard `
  --export-viewer-package `
  --viewer-render-policy top-issues `
  --viewer-perf-log `
  --render-selected-zone-evidence `
  --selected-zone-evidence-per-pair 1 `
  --export-cloud-marks `
  --cloud-export-mode all `
  --measure-runtime-budget `
  --quality-gate `
  --max-workers 1
```

Result: PASS.

- `_SUCCESS`: present
- `quality_gate.status`: `passed`
- ODA conversion path exercised through `D:\00.Work_AI_Tool\07.Dwg_diff\도면비교\1.dwg` and `2.dwg`
- completed pairs: 1
- total changes: 11
- change zones: 11
- review dashboard structural-core issues: 3 member issues
- selected-zone evidence: `passed`
- cold selected-zone render: 54,142.289 ms
- cache-hit selected-zone render: 1.884 ms
- generated cloud-marked DXF files: 1
- total runtime: 183.746 s
- peak RSS: 720.328 MB
- peak disk spool: 41.517 MB

## Observations

- The extracted repository can run DXF, PDF, and DWG sample comparisons without
  depending on the mixed TEKLA_MCP source tree.
- The AI embedding warning is non-blocking in these runs. The classifier
  abstains because optional local embedding models are not installed, while the
  heuristic/review dashboard path still produces usable structural review items.
- The DWG path is functionally valid but still slow for selected-zone cold
  rendering. The 54.1 s cold render should be treated as the next performance
  improvement target before broader pilot use.
- Generic file names such as `before.dxf`/`after.dxf` or `1.dwg`/`2.dwg` should
  be paired with a manual matches CSV. This is expected operator workflow for
  ambiguous file names.

## DWG Selected-Zone Fast-Crop Follow-Up

Change: `src/services/comparison/zone_render_service.py` now reuses
pre-rendered viewer `before_image` / `after_image` backgrounds for CAD
selected-zone crops when those backgrounds and transforms are already present.
This avoids reopening and re-rendering the original DWG/DXF for the first
selected-zone crop. If the background crop is unavailable or fails, the service
falls back to the previous source render path.

Regression test added:

```powershell
python -m pytest tests\unit\services\comparison\test_zone_render_service.py -q --tb=short --disable-warnings -o log_cli=false --capture=sys
```

Result: PASS, 20 passed.

Broader focused regression:

```powershell
python -m pytest tests\unit\services\comparison\test_zone_render_service.py tests\unit\services\comparison\test_validate_drawing_compare_realset.py tests\unit\services\comparison\test_viewer_package.py tests\unit\services\comparison\test_viewer_perf_summary.py -q --tb=short --disable-warnings -o log_cli=false --capture=sys
```

Result: PASS, 75 passed.

DWG sample rerun:

```powershell
python scripts\validate_drawing_compare_realset.py `
  --a "D:\00.Work_AI_Tool\07.Dwg_diff\도면비교\1.dwg" `
  --b "D:\00.Work_AI_Tool\07.Dwg_diff\도면비교\2.dwg" `
  --out release\sample_compare_dwg_1_2_fastcrop `
  --manual-matches release\sample_inputs\manual_matches_dwg_1_2.csv `
  --dxf-cache-dir release\sample_dwg_cache `
  --export-profile sharable `
  --change-zone-report `
  --executive-review `
  --review-dashboard `
  --export-viewer-package `
  --viewer-render-policy top-issues `
  --viewer-perf-log `
  --render-selected-zone-evidence `
  --selected-zone-evidence-per-pair 1 `
  --export-cloud-marks `
  --cloud-export-mode all `
  --measure-runtime-budget `
  --quality-gate `
  --max-workers 1
```

Result: PASS.

| Metric | Before | After |
| --- | ---: | ---: |
| selected-zone cold render | 54,142.289 ms | 31.171 ms |
| selected-zone cache-hit render | 1.884 ms | 2.751 ms |
| artifact stage | 134.414 s | 81.098 s |
| total runtime | 183.746 s | 109.564 s |
| first dashboard ready | 183.256 s | 109.166 s |
| peak RSS | 720.328 MB | 717.828 MB |

The first selected-zone crop now reports `renderer_backend =
cad-background-image-crop`, `visual_fidelity = cad_render`, and
`render_lifecycle = ready`.

## Fast-Crop Release Package Validation

Date: 2026-05-22 KST

Validated commit: `51cea4c perf: reuse CAD viewer backgrounds for zone crops`.

Environment check:

```powershell
python scripts\release_environment_check.py --json-output release\environment_check_fastcrop_20260522.json
```

Result: PASS.

- ODA File Converter detected at
  `C:\Program Files\ODA\ODAFileConverter 26.10.0\ODAFileConverter.exe`
- PyInstaller detected on `PATH`

Release command:

```powershell
python scripts\release_drawing_compare_workbench.py `
  --out release\drawing_compare_fastcrop_build_20260522 `
  --skip-realset
```

Result: PASS.

- release manifest status: `passed`
- compile step: `passed`
- comparison unit tests: `passed`
- PyInstaller build: `passed`
- packaged app launch smoke: `passed`
- customer shareable package path audit: `passed`
- path leak count: 0
- disallowed file count: 0

Packaged executable diagnostic:

```powershell
release\drawing_compare_fastcrop_build_20260522\dist\DrawingCompareWorkbench\DrawingCompareWorkbench.exe --diagnose
```

Result: PASS, all required and optional runtime dependencies available.

Customer-shareable ZIP:

- path:
  `release\drawing_compare_fastcrop_build_20260522\DrawingCompareWorkbench_customer_shareable.zip`
- size: 509,041,664 bytes
- SHA256:
  `E9E9E5A4D5EC3EBE741ACA98DBAF44E23E1B0995A9823952FD40FC6E234C82D3`
- ZIP entries: 8,620
- required entries verified:
  - `README_INTERNAL_PILOT.md`
  - `customer_evidence_request_ko.md`
  - `app/DrawingCompareWorkbench/DrawingCompareWorkbench.exe`
  - `customer_package_manifest.json`
  - `customer_package_path_audit.json`

Scope note: this release build intentionally used `--skip-realset` because the
sample compare and focused regression were already recorded above. Before wider
pilot distribution, still run a clean Windows GUI smoke with actual operator
input files.

## Next Recommended Step

The next performance target is the remaining DWG artifact stage time. The
largest remaining costs are full background generation and DWG/DXF comparison,
not selected-zone crop latency.
