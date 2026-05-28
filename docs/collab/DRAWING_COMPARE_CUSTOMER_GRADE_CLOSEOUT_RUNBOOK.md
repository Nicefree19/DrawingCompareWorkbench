# Drawing Compare Customer-Grade Closeout Runbook

Date: 2026-05-13 KST
Work item: WI-20260510-001
Status: waiting for external customer-grade evidence

## Purpose

This runbook is the concrete final path from the current large-DWG fixed build
to a customer-grade 10/10 MVP exit audit.

The code, package, synthetic audit, and focused inventory are ready. The only
remaining inputs are external:

1. Approved, non-template `review_ground_truth.csv`.
2. Structural review lead/team lead `operator_dry_run_notes.md`.

The final `customer_evidence_manifest.json` is generated from those two real
inputs in the Manifest Generation step below. Do not hand-write a manifest to
force readiness.

Do not use release templates, example files, this runbook, or the closeout
packet as evidence.

## Current Verified Inputs

- Current release manifest:
  `tmp\drawing_compare_release_mvp_packaged_fix_large_dwg_request_ko_ascii_json_probe_filter_precise\release_manifest.json`
- Local focused inventory:
  `tmp\drawing_compare_customer_evidence_inventory_large_dwg_request_ko_ascii_json_probe_filter_precise_focused_local.json`
- Portable focused inventory:
  `tmp\drawing_compare_customer_evidence_inventory_large_dwg_request_ko_ascii_json_probe_filter_precise_focused_with_release.json`
- Synthetic exit audit:
  `tmp\drawing_compare_mvp_exit_audit_large_dwg_request_ko_ascii_json_probe_filter_precise.json`
- S20 large-DWG performance probe:
  `tmp\dwg_s20_dwg_differ_after_fix.json`
- Current explicit customer-grade gate re-run:
  `tmp\drawing_compare_mvp_exit_audit_customer_grade_gate_schema15_current.json`
  (`25/26`, only `customer_grade_evidence_declared` failing)
- Current evidence inbox handoff ZIP:
  `tmp\drawing_compare_customer_evidence_inbox_request_ko_ascii_json_schema31.zip`
  (5 entries, SHA256
  `F27EB812C82334ED93793B123F0511244C0F3AD5A4142CCBB6992C8DB7D2058F`)
- Current local inbox fast check:
  `tmp\drawing_compare_customer_evidence_intake_check_schema29.json`
  (`status=missing_required_files`, because the inbox still lacks real
  `review_ground_truth.csv`, real `operator_dry_run_notes.md`, and a generated
  ready `customer_evidence_manifest.json`).

The inbox ZIP is guidance only. It contains template copies and a Korean
follow-up message, but it must not be submitted as final evidence.

## Evidence Intake Check

If using the prepared local inbox, place the real customer-grade inputs at:

- `tmp\drawing_compare_customer_evidence_inbox_request_ko_ascii_json\review_ground_truth.csv`
- `tmp\drawing_compare_customer_evidence_inbox_request_ko_ascii_json\operator_dry_run_notes.md`

Do not rename or submit `*_TEMPLATE_COPY.*` files as evidence. The optional
schema29 fast check is a final bundle readiness check, so it remains incomplete
until the generated ready manifest also exists.

Run from the repository root after placing the real customer-grade truth CSV
and operator notes somewhere under the evidence folder:

```powershell
python scripts\inventory_drawing_compare_customer_evidence.py `
  --root tmp\drawing_compare_dwg_smoke_sharable_topissues_selected_zone_current `
  --root tmp\drawing_compare_pdf_20_auto_sharable_topissues_selected_zone_current `
  --root tmp\drawing_compare_release_mvp_smoke_review_truth_gate\realset_validation `
  --root tmp\drawing_compare_cad_pdf_block_smoke `
  --root tmp\drawing_compare_pdf_structural_coverage_selected_zone_current `
  --root tmp\drawing_compare_ai_policy_probe_current\validation `
  --root tmp\drawing_compare_cad_block_text_no_expand_current `
  --root tmp\drawing_compare_release_mvp_packaged_fix_large_dwg_request_ko_ascii_json_probe_filter_precise `
  --root <customer_evidence_folder> `
  --large-dwg-probe tmp\dwg_s20_dwg_differ_after_fix.json `
  --out tmp\drawing_compare_customer_evidence_inventory_customer_grade_local.json
```

The inventory must report `status=ready_for_manifest` before the final manifest
is generated.

The current customer-package `cli\*.py` tools emit ASCII-safe UTF-8 JSON, so
PowerShell `Get-Content -Raw <json> | ConvertFrom-Json` can be used to inspect
inventory and audit outputs on Windows.

Required clean diagnostics:

- `valid_review_ground_truth_csv_candidates` includes the real truth CSV.
- `operator_notes_with_approved_structural_role=true`.
- `operator_notes_with_substantive_review_notes=true`.
- `large_dwg_probe_passed=true` and `large_dwg_probe_issues=[]`.
- `missing_operator_workflow_checks=[]`.
- `customer_evidence_manifests_not_ready=[]`.
- `customer_evidence_manifests_missing_approved_ground_truth=[]`.

## Manifest Generation

Replace the angle-bracket placeholders, then run:

```powershell
python scripts\prepare_drawing_compare_customer_evidence.py `
  --results-dir tmp\drawing_compare_ai_policy_probe_current\validation `
  --results-dir tmp\drawing_compare_cad_block_text_no_expand_current `
  --results-dir tmp\drawing_compare_cad_pdf_block_smoke `
  --results-dir tmp\drawing_compare_dwg_smoke_sharable_topissues_selected_zone_current `
  --results-dir tmp\drawing_compare_pdf_20_auto_sharable_topissues_selected_zone_current `
  --results-dir tmp\drawing_compare_pdf_structural_coverage_selected_zone_current `
  --results-dir tmp\drawing_compare_release_mvp_smoke_review_truth_gate\realset_validation `
  --out tmp\customer_evidence_manifest_customer_grade.json `
  --dataset-id <dataset_id> `
  --dataset-source-kind customer_grade `
  --dataset-source-description "20-50 sheet customer-grade validation set approved for MVP exit" `
  --dataset-approval-status approved_for_mvp_exit `
  --dataset-approver <approver> `
  --ground-truth-owner <owner> `
  --review-ground-truth <review_ground_truth.csv> `
  --ground-truth-status approved `
  --operator-reviewer-role structural_review_lead `
  --operator-notes-file <operator_dry_run_notes.md> `
  --confirmed-export-artifact tmp\drawing_compare_release_mvp_smoke_review_truth_gate\realset_validation\artifacts\confirmed_clouds\pair_719a640cfa3ab6af_confirmed.png `
  --min-total-pairs 20 `
  --max-total-pairs 50 `
  --max-first-review-ready-s 1800 `
  --max-cold-zone-render-ms 10000 `
  --max-cache-hit-zone-render-ms 2000
```

The generated manifest must contain:

- `evidence_level=customer_grade`
- `readiness.status=ready`
- `readiness.issue_count=0`
- `ground_truth.status=approved`
- `dataset_provenance.approval_status=approved_for_mvp_exit`
- `cad_policy_evidence.block_text_detection_without_expansion=true`

## P5-G16 Real-Corpus Replay Evidence

For a direct manual audit, generate the replay JSON after the customer evidence
manifest exists and pass it explicitly to the final audit:

```powershell
python scripts\benchmark_real_corpus_replay.py `
  --validation-summary tmp\drawing_compare_release_mvp_smoke_review_truth_gate\realset_validation\validation_summary.json `
  --output-json tmp\p5_g16_real_corpus_replay_customer_grade.json `
  --customer-evidence-manifest tmp\customer_evidence_manifest_customer_grade.json `
  --require-customer-corpus `
  --min-customer-sheet-count 20 `
  --max-customer-sheet-count 50 `
  --visits 100 `
  --warmup-visits 20 `
  --timeout-s 60
```

The closeout runner performs this step automatically when it can identify a
standard validation output with completed pairs, then routes the generated
`p5_g16_real_corpus_replay.json` through prepare, readiness audit, and the final
customer-grade audit.

## P5-G22 Actual GUI Soak Evidence

For a direct manual audit, generate the actual GUI soak JSON after the customer
evidence manifest exists and pass it explicitly to the final audit:

```powershell
python scripts\benchmark_actual_gui_soak.py `
  --validation-summary tmp\drawing_compare_release_mvp_smoke_review_truth_gate\realset_validation\validation_summary.json `
  --output-json tmp\p5_g22_actual_gui_soak_customer_grade.json `
  --customer-evidence-manifest tmp\customer_evidence_manifest_customer_grade.json `
  --require-customer-corpus `
  --min-customer-sheet-count 20 `
  --max-customer-sheet-count 50 `
  --visits 100 `
  --warmup-visits 20 `
  --timeout-s 120 `
  --zone-render-wait-ms 250
```

The closeout runner performs this step automatically for standard validation
outputs with completed pairs, then routes the generated
`p5_g22_actual_gui_soak.json` through prepare, readiness audit, and the final
customer-grade audit. Customer-grade audit fails if the GUI soak artifact is
missing, stale, tied to a different manifest hash, or fails blank/stale/RSS,
native-resource, event-loop, page/zone navigation, or worker-cleanup gates.

## Final Customer-Grade Audit

Run:

```powershell
python scripts\audit_drawing_compare_mvp_exit.py `
  --results-dir tmp\drawing_compare_ai_policy_probe_current\validation `
  --results-dir tmp\drawing_compare_cad_block_text_no_expand_current `
  --results-dir tmp\drawing_compare_cad_pdf_block_smoke `
  --results-dir tmp\drawing_compare_dwg_smoke_sharable_topissues_selected_zone_current `
  --results-dir tmp\drawing_compare_pdf_20_auto_sharable_topissues_selected_zone_current `
  --results-dir tmp\drawing_compare_pdf_structural_coverage_selected_zone_current `
  --results-dir tmp\drawing_compare_release_mvp_smoke_review_truth_gate\realset_validation `
  --release-manifest tmp\drawing_compare_release_mvp_packaged_fix_large_dwg_request_ko_ascii_json_probe_filter_precise\release_manifest.json `
  --large-dwg-probe tmp\dwg_s20_dwg_differ_after_fix.json `
  --require-large-dwg-probe `
  --customer-evidence-manifest tmp\customer_evidence_manifest_customer_grade.json `
  --evidence-level customer_grade `
  --min-total-pairs 20 `
  --max-total-pairs 50 `
  --max-first-review-ready-s 1800 `
  --max-cold-zone-render-ms 10000 `
  --max-cache-hit-zone-render-ms 2000 `
  --p5-g16-benchmark-json tmp\p5_g16_real_corpus_replay_customer_grade.json `
  --p5-g22-gui-soak-json tmp\p5_g22_actual_gui_soak_customer_grade.json `
  --out tmp\drawing_compare_mvp_exit_audit_customer_grade.json
```

Completion can be declared only when
`tmp\drawing_compare_mvp_exit_audit_customer_grade.json` reports
`status=passed` with zero failed checks.

## Closeout Readiness Audit

Before running full closeout, run the closeout runner once in dry-run mode and
audit the generated readiness packet:

```powershell
python cli\closeout_drawing_compare_customer_evidence.py `
  --dry-run `
  --plan-json <closeout_plan.json> `
  --readiness-json <closeout_readiness.json> `
  <same closeout arguments as the real run>

python cli\audit_closeout_readiness.py `
  --readiness-json <closeout_readiness.json> `
  --plan-json <closeout_plan.json> `
  --require-ready `
  --out <closeout_readiness_audit.json>
```

Do not proceed to full closeout unless
`closeout_readiness_audit.json` has `status=passed`. This audit cross-checks
that the readiness summary matches the plan, proof outputs are not passed as
final audit `--results-dir`, and `DRAWING_COMPARE_TILE_CACHE_MB` is isolated to
forced P5-G7 proof validation steps. It also checks that P5-G16 replay JSON
paths and P5-G22 actual GUI soak JSON paths are routed consistently from the
closeout plan into prepare and the final customer-grade audit.

## Audit Gates Reinforcement (External Audit Recommendations, 2026-05-15)

The external audit reviewer flagged that several gates were proxy metrics.
Branch `fix/audit-recommendation-gates` adds four new audit gates so the
final customer-grade audit can prove customer reality directly. These gates
are off by default; enable them by adding the new CLI flags below to the
`Final Customer-Grade Audit` invocation.

### Recommendation #1 — Runtime budget (memory + first-review-ready)
Run the validator with `--measure-runtime-budget` so each
`validation_summary.json` carries a `runtime_budget` block, then add:
```
--require-runtime-budget `
--max-peak-working-set-mb 4096 `
--max-runtime-first-review-ready-s 1200 `
--max-peak-disk-spool-mb 1024 `
```
Required input: validator must be invoked with `--measure-runtime-budget`.

### Recommendation #2 — Selected-zone actual_crop rate
After running the validator with `--render-selected-zone-evidence` (already
required), add:
```
--require-actual-crop-rate-pdf 0.85 `
--require-actual-crop-rate-cad 0.95 `
--require-actual-crop-rate-overall 0.90 `
```
Required input: `selected_zone_evidence.json` (already produced by the existing
`--render-selected-zone-evidence` flag).

### Recommendation #3 — Review queue precision and burden
Once each `validation_summary.json` carries a `review_burden` block (operator
decisions joined with ground truth via `review_burden.compute_review_burden`),
add:
```
--require-precision-threshold 0.80 `
--require-burden-threshold 3.0 `
--require-burden-minutes-threshold 5.0 `
```
Required input: `review_ground_truth.csv` and operator decisions feeding
`compute_review_burden`. Without ground truth the gate fails as `missing`.

### Recommendation #4 — Dataset stratification
Add the strict gate (defaults: CAD≥8, PDF≥8, blocked≥1, no_expand≥2,
large_drawing≥2, plus coverage buckets):
```
--require-dataset-composition `
--composition-mode strict `
```
Required input: `customer_evidence_manifest.json` produced by
`prepare_drawing_compare_customer_evidence.py` must include a
`dataset_composition` block. Use `--composition-mode advisory` while the
inventory pipeline is being extended to populate the block automatically.

### Verification
- 117 new unit tests across `tests/unit/services/comparison/test_runtime_budget.py`,
  `test_audit_runtime_budget.py`, `test_zone_render_outcome.py`,
  `test_audit_actual_crop_rate.py`, `test_review_burden.py`,
  `test_audit_review_burden.py`, `test_dataset_composition.py`,
  `test_audit_dataset_composition.py`.
- Full `tests/unit/services/comparison/` regression: 2353 passed / 2 skipped /
  0 failed (70.81s).
- All new flags default to off / None so existing audit invocations remain
  byte-compatible.
