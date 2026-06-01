# AC1032 Converted-DXF Fallback Release Packet

Date: 2026-06-01
Branch: `codex/hybrid-viewer-sheet-match-metrics`
PR: <https://github.com/Nicefree19/DrawingCompareWorkbench/pull/4>

## Scope

This packet covers the customer workflow where the selected DWG inputs are
modern unsupported DWG files, especially AC1032, but matching converted DXFs
already exist in the work folder.

The accepted behavior is:

- AC1032 native DWG support is not claimed.
- If matching converted DXFs exist under `dxf_registered/before` and
  `dxf_registered/after`, the pipeline may compare those effective DXF inputs.
- Original and effective input provenance must be visible in run artifacts.

## GUI Exposure Decision

Decision: do not modify `src/gui/drawing_compare_workbench.py` for a visible GUI
banner in this release slice.

Rationale:

- `src/gui/drawing_compare_workbench.py` is under structural freeze.
- The fallback is already visible in durable artifacts:
  - `run_manifest.json.inputs.dwg_dxf_fallback`
  - `review_project.json.options.input_resolution`
  - `direct_compare_summary.json` fields `effective_source_a`,
    `effective_source_b`, `fallback_used`, `fallback_reason`, and
    `fallback_kind`
- The next GUI-facing step, if required, should be a separate module or panel
  that reads `input_resolution` metadata instead of adding new monolith logic.

Future GUI option:

- Add a small `src/gui/input_resolution_notice.py` helper and wire it with a
  narrow adapter only after separate approval.

## Real Corpus Evidence

Corpus: `D:\도면 비교`

| Run | Input | Status | Raw changes | Review zones | Notes |
|---|---|---:|---:|---:|---|
| `same_folder_final_verify` | `D:\도면 비교` vs `D:\도면 비교` | passed | 33 | 7 | Auto fallback to `dxf_registered/before` and `dxf_registered/after` |
| `dxf_clean_folder_verify` | `dxf_clean/before` vs `dxf_clean/after` | passed | 6,620 | 117 | Much noisier than registered fallback |
| `dxf_compare_folder_verify` | `dxf_compare/before` vs `dxf_compare/after` | passed | 7,654 | 607 | Much noisier than registered fallback |

Conclusion: `dxf_registered` is the correct automatic fallback priority for
this corpus.

## Regression Evidence

Targeted tests:

```powershell
python -m pytest tests\unit\scripts\test_direct_workbench_compare.py `
  tests\unit\services\comparison\test_folder_compare_pipeline.py::test_folder_compare_pipeline_uses_converted_dxf_fallback_for_unsupported_dwg_folder `
  tests\unit\services\comparison\test_dwg_dxf_fallback.py `
  tests\unit\services\comparison\test_preflight.py -q
```

Result: 17 passed.

Additional checks:

```powershell
python -m py_compile scripts\direct_workbench_compare.py `
  tests\unit\scripts\test_direct_workbench_compare.py `
  tests\unit\services\comparison\test_folder_compare_pipeline.py `
  src\services\comparison\dwg_dxf_fallback.py `
  src\services\comparison\folder_compare_pipeline.py `
  src\services\comparison\preflight.py `
  tests\unit\services\comparison\test_dwg_dxf_fallback.py `
  tests\unit\services\comparison\test_preflight.py

python scripts\cad_policy_gate.py
git diff --check
```

All passed.

## Release Decision

Release this as converted-DXF fallback hardening, not as native DWG support.

Required release-note wording:

- "Unsupported modern DWG selections can automatically use matching converted
  DXF folders when present."
- "Native AC1032 DWG support is not included."
- "Fallback provenance is recorded in run artifacts."

## Follow-Up Work

1. Decide whether a separate GUI notice helper is needed after user review.
2. Continue ADR-004 version-by-version native reader planning.

## AC1032 Phase 0 Started

Added `scripts/inventory_dwg_native_phase0.py` as a non-importing corpus
inventory tool. It records:

- DWG version counts
- unsupported DWG count
- folder-level converted-DXF fallback readiness
- fallback candidate ordering and DXF before/after counts

Real corpus run:

```powershell
python scripts\inventory_dwg_native_phase0.py 'D:\도면 비교' `
  --out out\user_drawing_compare_20260601\dwg_native_phase0_inventory.json
```

Result:

- DWG count: 2
- Version counts: `AC1032: 2`
- Unsupported count: 2
- Converted-DXF fallback-ready folders: 1
- Preferred fallback: `dxf_registered/before_after_dirs`

Regression:

```powershell
python -m pytest tests\unit\scripts\test_inventory_dwg_native_phase0.py -q
```

Result: 3 passed.
