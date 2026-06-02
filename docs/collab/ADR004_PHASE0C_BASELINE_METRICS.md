# ADR-004 Phase 0-C Baseline Metrics

Date: 2026-06-02

## Scope

This matrix consolidates local ADR-004 converted-DXF validation summaries.
It is baseline evidence only and does not claim native AC1018+ DWG support.

## Status

- Overall: `partial`
- Compare-ready versions: `AC1024, AC1027, AC1032`
- Compare gaps: `AC1018, AC1021`
- Header errors: `0`
- Source validation errors: `0`

## Source Summaries

| status | versions | summary | sample pack |
| --- | ---: | --- | --- |
| partial | 5 | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_version_samples_20260602_104044\validation_summary_v2.json` | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_version_samples_20260602_104044` |
| partial | 3 | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_compact_compare_samples_20260602_131140\validation_summary.json` | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_compact_compare_samples_20260602_131140` |
| ok | 1 | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_ac1032_registered_baseline_20260602_132007\validation_summary.json` | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_ac1032_registered_baseline_20260602_132007` |

## Baseline Matrix

| version | Phase 0-C status | pair kind | import entities | warnings | compare | diff summary | elapsed | selected source |
| --- | --- | --- | ---: | --- | --- | --- | ---: | --- |
| AC1018 | import_only_duplicate | single_file_duplicated_import_baseline_small | 10233 / 10233 | UNSUPPORTED_ENTITY, XREF_NOT_RESOLVED, DWG_CONVERTED_DXF_FALLBACK | partial | added 0, removed 0, modified 0, unchanged 10233, total_changes 0 | 43.67s | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_version_samples_20260602_104044\validation_summary_v2.json` |
| AC1021 | import_only_duplicate | single_file_duplicated_import_baseline | 3311 / 3311 | UNSUPPORTED_ENTITY, XREF_NOT_RESOLVED, DWG_CONVERTED_DXF_FALLBACK | partial | added 0, removed 0, modified 0, unchanged 3311, total_changes 0 | 3.15s | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_version_samples_20260602_104044\validation_summary_v2.json` |
| AC1024 | compare_baseline_ready | compact_likely_revision_pair | 6590 / 6792 | UNSUPPORTED_ENTITY, ENTITY_APPROXIMATED, XREF_NOT_RESOLVED, DWG_CONVERTED_DXF_FALLBACK | partial | added 342, removed 140, modified 19, unchanged 6431, total_changes 501 | 36.67s | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_compact_compare_samples_20260602_131140\validation_summary.json` |
| AC1027 | compare_baseline_ready | compact_likely_revision_pair | 21988 / 48727 | ENTITY_APPROXIMATED, UNSUPPORTED_ENTITY, DWG_CONVERTED_DXF_FALLBACK | partial | added 26741, removed 2, modified 0, unchanged 21986, total_changes 26743 | 41.76s | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_compact_compare_samples_20260602_131140\validation_summary.json` |
| AC1032 | compare_baseline_ready | confirmed_revision_pair_existing_registered_dxf | 7197 / 7195 | ENTITY_APPROXIMATED, UNSUPPORTED_ENTITY, DWG_CONVERTED_DXF_FALLBACK | partial | added 37, removed 39, modified 207, unchanged 6951, total_changes 283 | 41.42s | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_ac1032_registered_baseline_20260602_132007\validation_summary.json` |

## Gap Evidence

| evidence | version | samples | candidates | classification | reason |
| --- | --- | ---: | ---: | --- | --- |
| `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_ac1018_ac1021_candidate_selection_drivewide.json` | AC1018 | 7 | 0 | missing_compare_candidate | multiple samples exist, but no filename/path/revision evidence supports a real before-after pair |
| `D:\00.Work_AI_Tool\DrawingCompareWorkbench\out\adr004_ac1018_ac1021_candidate_selection_drivewide.json` | AC1021 | 5 | 0 | missing_compare_candidate | multiple samples exist, but no filename/path/revision evidence supports a real before-after pair |

## Interpretation

- `compare_baseline_ready` means a non-duplicated before/after pair compared successfully with status `ok` or `partial`.
- `import_only_duplicate` means the version has import/header coverage but no real compare-recall baseline.
- `compare_blocked` means a candidate exists but compare failed, timed out, or was skipped.
