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

## Next Recommended Step

Prioritize DWG selected-zone cold-render optimization and then run the same DWG
sample again to confirm cold render drops below the customer-facing target.
