# Drawing Compare PR 5 Pilot Stabilization

## Positioning

PR 5 is treated as an internal Pilot candidate for the drawing change review product, not as a broad production rollout. The CAD entity diff remains the truth layer. PDF, PNG, viewer overlays, executive reports, and cloud-marked outputs remain review and presentation layers.

## Review Response Matrix

| Review item | Status | PR 5 response |
| --- | --- | --- |
| Manual match one-to-one integrity | Resolved | Confirmed pairs now enforce unique A and unique B assignments before compare execution. Duplicate A and duplicate B are quality-gate failures. |
| Pair identity collision risk | Resolved | A deterministic `pair_uuid` is the canonical artifact key. `pair_id` remains an alias for compatibility, and drawing numbers/stems are display labels only. |
| Scan and compare cache reuse | Resolved | Folder pipeline and Workbench V2 pass the same DXF cache root through scan, compare, preview, viewer, and export stages. |
| Partial output mistaken as complete | Resolved | Runs now write `run_manifest.json`, `_SUCCESS`, and `_FAILED`. Workbench does not show a missing-success result as clean completion. |
| Operational preflight | Resolved for Pilot | CLI and pipeline emit `preflight_report.json` covering input/output paths, cache/state writeability, input-folder pollution, disk space, long-path risk, rtree, ODA, and preview dependencies. |
| Path leakage in shared artifacts | Resolved for Pilot | `--export-profile internal|sharable` was added. `sharable` redacts source/cache/state absolute paths from JSON outputs. |
| Preview state ambiguity | Resolved for Pilot | Review dashboard and Workbench expose preview status as `real_preview`, `relative_only`, `render_pending`, or `render_failed`. |
| Repetitive layer folding hides issues | Mitigated | Folded repetitive groups remain summarized, but representative folded issues are preserved in top issue selection. |
| Fully streaming zone clustering | Follow-up | Current stream-backed zoning is adequate for Pilot scale, but tile-based incremental clustering remains a follow-up. |
| Installer, code signing, team sharing | Follow-up | Packaging hardening, signed installer, telemetry dashboard, and DB/API-based team workflows are outside PR 5. |

## Local Verification

- `python -m py_compile` on comparison, CLI, and Workbench modules: passed.
- Targeted drawing compare tests: `56 passed`.
- Full comparison regression: `668 passed`.
- R-tree benchmark executed without skip:
  - `R-tree (10K): 0.035s`
  - `Fallback (1K): 1.631s`

## DWG_DIFF_TEST Acceptance

Acceptance output:

`tmp/drawing_validation_DWG_DIFF_TEST_pilot_acceptance`

Result summary:

- Quality gate: passed.
- Run manifest: completed.
- `_SUCCESS`: present.
- `_FAILED`: absent.
- Confirmed pairs: 29.
- Completed pairs: 29.
- Failed pairs: 0.
- Duplicate A assignments: 0.
- Duplicate B assignments: 0.
- Blocked CAD/PDF pairs: 0.
- Raw changes: 79,273.
- Change-zone stream records: 79,273.
- Stream mismatch pairs: 0.
- Change zones: 39,809.
- Viewer overlay records: 39,809.
- Input-folder `.drawing_compare_cache`: removed after confirming it was a generated cache from earlier runs.

Generated key artifacts:

- `preflight_report.json`
- `run_manifest.json`
- `_SUCCESS`
- `validation_summary.json`
- `quality_gate.json`
- `change_artifacts/artifact_manifest.json`
- `change_artifacts/change_zones.csv`
- `change_artifacts/review_dashboard.json`
- `viewer/viewer_manifest.json`

## Sharable Export Verification

Sharable output:

`tmp/drawing_validation_DWG_DIFF_TEST_pilot_sharable`

Result summary:

- Quality gate: passed.
- JSON path leakage scan: no source A path, source B path, DXF cache path, or compare state path found in sharable JSON outputs.
- `internal` remains the default for local debugging. `sharable` is the external-review profile.

## Scope Boundaries

The following dirty worktree items are not part of PR 5 drawing compare stabilization and should remain separate:

- `src/core/generators/mgt_generator.py`
- `tests/integration/test_mgt_round_trip.py`

## Recommended PR Comment

This PR is ready to remain an internal Pilot candidate after the stabilization pass. The review feedback was accepted and split into immediate correctness fixes versus follow-up platform work. The immediate PR changes address one-to-one matching integrity, pair identity stability, cache reuse, run completion contracts, preflight reporting, sharable export redaction, and preview status clarity. Broader production hardening such as fully streaming zone clustering, installer/code signing, telemetry dashboards, and team DB/API sharing remains explicitly out of PR 5 scope.
