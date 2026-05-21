# Drawing Compare MVP Current Completion Audit

## Verdict

Status: **not complete**.

The implementation and synthetic release evidence are strong enough to show the
Workbench MVP behavior on controlled DWG/DXF/PDF cases, but the active customer
deployment goal still requires customer-grade evidence. The remaining blockers
are not accepted as proxy signals:

- Approved non-empty `review_ground_truth.csv` for a 20-50 sheet customer or
  customer-grade validation set.
- Checked operator dry-run notes from a structural review lead covering every
  required workflow ID plus substantive observed dry-run notes. The manifest
  reviewer role must be one of the approved structural review lead/team lead
  roles, not a generic operator role.
- A `customer_evidence_manifest.json` generated from those artifacts and a final
  `audit_drawing_compare_mvp_exit.py --evidence-level customer_grade` pass.

## Objective Restated As Deliverables

The MVP is complete only when a structural drawing review lead can use the
Windows Workbench to compare DWG/DXF and PDF-PDF drawings, review structural-core
changes through a first-class `review_queue`, inspect selected zones in synced
Before/After windows, classify each queue item as `confirmed`,
`false_positive`, or `hold`, export only confirmed clouds/reports, and share the
package without absolute path/cache/state/temp leakage.

## Prompt-To-Artifact Checklist

| Requirement | Concrete evidence inspected | Current status |
| --- | --- | --- |
| Windows Workbench exists for review lead workflow | `scripts/workbench_acceptance_smoke.py`, `tmp/drawing_compare_release_mvp_packaged_current/release_manifest.json`, `tmp/drawing_compare_release_mvp_packaged_current/DrawingCompareWorkbench_customer_shareable.zip` | Synthetic smoke passed |
| Two files/folders selection and automatic comparison | Release validation command in `release_manifest.json`; Workbench acceptance smoke | Synthetic smoke passed |
| DWG/DXF comparison support | `dwg_dxf_cad_support` in `tmp/drawing_compare_mvp_exit_audit_current.json`; curated inventory has `.dwg` and `.dxf` CAD evidence | Synthetic evidence passed |
| PDF-PDF comparison support | `pdf_pdf_support` in strict audit; 20-pair PDF validation output | Synthetic evidence passed |
| CAD-PDF cross comparison blocked | `cad_pdf_cross_compare_blocked` strict audit check; `tmp/drawing_compare_cad_pdf_block_smoke/blocked_pairs.csv` | Synthetic block evidence passed |
| First screen prioritizes structural Top 3-5, not raw counts | `top_structural_review_queue_first` strict audit check; inventory `top_review_queue_first_all_completed=true` | Synthetic evidence passed |
| `review_queue` is first-class, keyed by `pair_uuid + zone_id` | `review_queue_required_fields` strict audit check validates required fields, canonical `queue_key`, uniqueness, status and metadata domains | Synthetic evidence passed |
| Korean `reason_ko` and `change_summary_ko` | `korean_reason_and_summary` strict audit check | Synthetic evidence passed |
| Selected zone zooms Before/After together | Workbench acceptance Item 9b and `selected_zone_render_perf` strict audit check | Synthetic evidence passed |
| Selected-zone rendering does not freeze UI | Workbench acceptance Item 9c checks QProcess-backed timeout-bounded render and event-loop ticks | Synthetic evidence passed |
| Review statuses include `confirmed`, `false_positive`, `hold` | Workbench acceptance Items 8/8b/10 and confirmed-only export audit | Synthetic evidence passed |
| Confirmed-only cloud/report export | `confirmed_only_cloud_and_report_export` strict audit check requires concrete `*_confirmed` artifact and report files | Synthetic evidence passed |
| CAD entity diff is truth layer | CAD queue metadata requires `source_format=cad`, `detection_source=cad_entity` | Synthetic evidence passed |
| CAD structural text includes TEXT/MTEXT/ATTRIB/ATTDEF/INSERT and block text | `cad_structural_text_modified_grouping` strict audit and unit coverage | Synthetic evidence passed |
| Block attribute/text changes are detected with `expand_blocks=False` | `cad_block_text_detection_without_expansion` strict audit check; `tmp/drawing_compare_cad_block_text_no_expand_current` | Synthetic evidence passed |
| `@100 -> @200` changes grouped as modified content | `cad_structural_text_modified_grouping` strict audit check | Synthetic evidence passed |
| PDF truth source labels and image-pixel bbox policy | `pdf_bbox_image_pixels_policy` strict audit inspects `change_artifacts/change_zones.csv` | Synthetic evidence passed |
| PDF bbox fallback is explicit | Review queue metadata domain gate allows only `exact`, `page_fallback`, `relative_only` | Implemented; customer cases still need evidence if present |
| Metadata-first and selected-zone-first viewer | `viewer_metadata_first_render_policy` strict audit rejects full pre-render policy | Synthetic evidence passed |
| Sharable package has path leakage 0 | `sharable_path_leakage_zero` strict audit and inventory `sharable_path_leakage_zero_all=true` | Synthetic evidence passed |
| Customer-shareable release package metadata has path leakage 0 | `customer_shareable_package_path_audit` release step and `customer_package_path_audit.json` | Synthetic package evidence passed |
| Raw JSONL/NDJSON streams absent from sharable export | `sharable_raw_jsonl_streams_absent` strict audit check | Synthetic evidence passed |
| AI embedding/LLM is optional | `ai_optional_heuristic_fallback` strict audit check proves warning plus heuristic fallback | Synthetic evidence passed |
| Operational preflight covers dependencies and environment | `preflight_passed` strict audit check | Synthetic evidence passed |
| 20-50 sheet first-review-ready performance | `twenty_to_fifty_sheet_scale` and `first_review_ready_within_30min` strict audit checks | Synthetic evidence passed |
| Customer-grade evidence provenance | `customer_grade_evidence_declared` strict audit check | **Missing** |
| Approved review ground truth | Current inventory issue: `missing non-empty review_ground_truth CSV` | **Missing** |
| Structural lead operator dry-run notes | Current inventory issue: `missing operator dry-run notes with all required workflow IDs checked`; substantive-note diagnostic is false because no completed notes are present | **Missing** |

## Current Evidence Commands

Curated inventory:

```powershell
python scripts\inventory_drawing_compare_customer_evidence.py `
  --root tmp\drawing_compare_dwg_smoke_sharable_topissues_selected_zone_current `
  --root tmp\drawing_compare_pdf_20_auto_sharable_topissues_selected_zone_current `
  --root tmp\drawing_compare_pdf_structural_coverage_selected_zone_current `
  --root tmp\drawing_compare_release_mvp_smoke_review_truth_gate\realset_validation `
  --root tmp\drawing_compare_cad_pdf_block_smoke `
  --root tmp\drawing_compare_ai_policy_probe_current\validation `
  --root tmp\drawing_compare_cad_block_text_no_expand_current `
  --root tmp\drawing_compare_release_mvp_packaged_current `
  --portable-paths `
  --out tmp\drawing_compare_customer_evidence_inventory_portable_current.json
```

Latest curated inventory result:

- `status=incomplete`
- `completed_pairs=26`
- `validation_output_count=7`
- `completed_validation_output_count=6`
- `has_cad_block_text_no_expand=true`
- `selected_zone_telemetry_all_completed=true`
- `top_review_queue_first_all_completed=true`
- `sharable_path_leakage_zero_all=true`
- `customer_evidence_manifest_summaries=[]`
- `diagnostics.customer_evidence_manifest_count=0`
- Issues: `missing non-empty review_ground_truth CSV`; `missing operator dry-run notes with all required workflow IDs checked`
- Operator diagnostics include `operator_notes_with_substantive_review_notes=false`

Strict synthetic audit:

```powershell
python scripts\audit_drawing_compare_mvp_exit.py `
  --results-dir tmp\drawing_compare_dwg_smoke_sharable_topissues_selected_zone_current `
  --results-dir tmp\drawing_compare_pdf_20_auto_sharable_topissues_selected_zone_current `
  --results-dir tmp\drawing_compare_release_mvp_smoke_review_truth_gate\realset_validation `
  --results-dir tmp\drawing_compare_cad_pdf_block_smoke `
  --results-dir tmp\drawing_compare_pdf_structural_coverage_selected_zone_current `
  --results-dir tmp\drawing_compare_ai_policy_probe_current\validation `
  --results-dir tmp\drawing_compare_cad_block_text_no_expand_current `
  --release-manifest tmp\drawing_compare_release_mvp_packaged_current\release_manifest.json `
  --evidence-level synthetic `
  --min-total-pairs 20 `
  --max-total-pairs 50 `
  --max-first-review-ready-s 1800 `
  --max-cold-zone-render-ms 10000 `
  --max-cache-hit-zone-render-ms 2000 `
  --out tmp\drawing_compare_mvp_exit_audit_current.json
```

Latest strict audit result:

- `status=failed`
- `passed=24`
- `failed=1`
- `completed_pairs=26`
- `queue_items=26`
- Failed check: `customer_grade_evidence_declared`

## Release Evidence Tooling

The release template package includes:

- `DrawingCompareWorkbench_customer_shareable.zip`
- `customer_shareable_package/customer_package_manifest.json`
- `customer_shareable_package/customer_package_path_audit.json`
- `cli/inventory_drawing_compare_customer_evidence.py`
- `cli/prepare_drawing_compare_customer_evidence.py`
- `cli/audit_drawing_compare_mvp_exit.py`
- `review_ground_truth_template.csv`
- `operator_dry_run_checklist_template.md`
- `customer_evidence_manifest_template.json`
- `mvp_exit_prompt_to_artifact_checklist.md`
- `customer_evidence_closeout_packet.md`

The inventory tool intentionally ignores release templates and operator handoff
docs when scanning for real customer evidence. Verified by:

- Unit test: `tests/unit/services/comparison/test_inventory_drawing_compare_customer_evidence.py`
- Release smoke: `tmp/drawing_compare_release_mvp_smoke_inventory_template_filter`
- Copied CLI self-scan: 0 truth/operator candidates from packaged templates

The manifest preparation and final audit tools also reject template or handoff
paths when they are supplied explicitly as customer evidence. Verified by:

- Unit tests:
  `test_prepare_customer_evidence_rejects_template_truth_and_operator_notes`
  and `test_customer_grade_audit_rejects_template_truth_and_operator_notes`
- Focused evidence/release regression: `90 passed`
- Release smoke: `tmp/drawing_compare_release_mvp_smoke_manifest_template_reject`
- Copied CLI probe:
  `tmp/drawing_compare_release_mvp_smoke_manifest_template_reject/template_probe_prepare_stdout.json`
  reports both template-evidence blocker messages
- Release README/operator guidance smoke:
  `tmp/drawing_compare_release_mvp_smoke_template_guidance` confirms the package
  tells operators to copy templates to real evidence filenames, not pass template
  paths, and the prompt-to-artifact checklist states template/handoff paths are
  rejected as evidence
- Review ground-truth schema hardening:
  `review_ground_truth.csv` is no longer accepted as an arbitrary non-empty CSV.
  Inventory, manifest preparation, and final audit now require the completed
  customer evidence file to keep the template schema
  `drawing_label,category,summary_contains,source_format,detection_source,bbox_status`,
  with non-empty row values and valid `source_format`, `detection_source`, and
  `bbox_status` tokens. A copied template is also rejected if any row still
  contains `example`, `sample`, or `template` markers, so renaming
  `review_ground_truth_template.csv` is not enough to satisfy customer-grade
  evidence. Inventory exposes
  `diagnostics.review_ground_truth_csv_schema_issues` and does not insert a
  schema-invalid truth CSV into the recommended manifest command. The
  customer-grade gate now also requires `ground_truth.status=approved`;
  `reviewed` is not sufficient for manifest readiness or final audit.
- Manifest readiness hardening:
  `prepare_drawing_compare_customer_evidence.py` now writes `readiness.status`,
  `readiness.issue_count`, and `readiness.issues` into generated manifests. The
  final audit now requires the readiness block and rejects missing readiness,
  non-`ready` status, or non-empty readiness issues. Release copy probes confirm
  incomplete manifests are visibly marked `readiness.status=incomplete` and are
  rejected by the copied `cli/audit_drawing_compare_mvp_exit.py`.
- Customer-shareable package hardening:
  `release_drawing_compare_workbench.py` now creates
  `DrawingCompareWorkbench_customer_shareable.zip` without the internal
  `release_manifest.json`. The package carries a relative
  `customer_package_manifest.json` and `customer_package_path_audit.json`; the
  current package audit reports `leak_count=0`, and the strict exit audit now
  requires the release manifest to reference the customer-shareable ZIP, the
  customer package manifest, and the passed package path audit. The package
  manifest must declare `package_type=customer_shareable` and
  `internal_release_manifest_included=false`, so a green path audit alone cannot
  stand in for a real customer-shareable package. The package path audit scans
  all customer-facing text metadata, first-party app text under
  `app/DrawingCompareWorkbench/_internal/src`, and selected customer-visible
  binaries such as the Workbench executable for build-machine path leakage; the
  current audit reports `leak_count=0`, `scanned_files=11`,
  `scanned_app_first_party_files=472`, and `scanned_binary_files=7`. The strict
  release audit also verifies
  both `customer_package_manifest.json` and the ZIP entries contain the actual
  Workbench executable, evidence CLI tools, internal-pilot README, MVP
  prompt-to-artifact checklist, operator checklist template, review ground-truth
  template, and package audit metadata. It also rejects package path-audit JSON
  that reports `leak_count=0` without positive `scanned_files`,
  `scanned_app_first_party_files`, and `scanned_binary_files`, so an empty or
  stale scan cannot satisfy the leakage gate. The strict release audit now reads
  the ZIP-internal
  `customer_package_manifest.json` and `customer_package_path_audit.json`
  payloads, applies the same customer-package and path-audit checks inside the
  ZIP as it does to the external package artifacts, and rejects any
  customer-shareable ZIP that contains the internal `release_manifest.json`.
  A binary string probe found that copied Python bytecode under
  `_internal/src/**/__pycache__/*.pyc` embedded build-worktree absolute paths,
  so customer-shareable package generation now excludes `__pycache__`, `.pyc`,
  and `.pyo` entries. The package path audit also reports
  `disallowed_file_count` and fails if any bytecode/cache file is present, and
  the final release audit independently rejects disallowed bytecode/cache
  entries in the external package manifest or the ZIP entry list.
  Current ZIP verification confirms the internal payload gate is present, the
  ZIP has no `release_manifest.json`, the package manifest declares
  `package_type=customer_shareable` and
  `internal_release_manifest_included=false`, and the ZIP-internal path audit
  reports `status=passed`, `leak_count=0`, `disallowed_file_count=0`,
  `scanned_files=11`, `scanned_app_first_party_files=472`, and
  `scanned_binary_files=7`. Direct ZIP byte-string verification now reports
  build-path `leak_entry_count=0`, `pycache_entry_count=0`, and
  `pyc_entry_count=0`. The final release audit now also independently scans
  the actual ZIP payload instead of trusting only the packaged
  `customer_package_path_audit.json`: customer-facing text files,
  first-party app text under `app/DrawingCompareWorkbench/_internal/src`, and
  selected binaries are re-read from the ZIP and must report `leak_count=0`
  with positive scan coverage. Current direct final-audit ZIP scan reports
  `leak_count=0`, `scanned_files=12`,
  `scanned_app_first_party_files=472`, and `scanned_binary_files=7`.
- Structural lead dry-run hardening:
  `prepare_drawing_compare_customer_evidence.py` and
  `audit_drawing_compare_mvp_exit.py` now require
  `operator_dry_run.reviewer_role` to normalize to an approved structural
  review lead/team lead role. A generic operator/checker label can no longer
  satisfy customer-grade evidence even if the checklist text exists.
  `inventory_drawing_compare_customer_evidence.py` also reports
  `operator_notes_with_approved_structural_role`, the approved role list, and
  note candidates missing that role so the issue is visible before manifest
  preparation. The
  release README sample command now uses
  `--operator-reviewer-role structural_review_lead`. The generated
  `operator_dry_run_checklist_template.md` now includes
  `reviewer_role: structural_review_lead`, and the prompt-to-artifact checklist
  explicitly requires an approved structural review lead/team lead role. The
  customer evidence tools also accept Korean role labels
  `구조검토책임자`, `구조검토팀장`, `구조도면검토책임자`,
  `구조도면검토팀장`, and the same labels with spaces normalized to
  underscores, so a Korean operator dry-run artifact can satisfy the structural
  reviewer role gate without switching to an English role id. The
  manifest preparation tool and final audit now also read the completed notes
  file and require it to include the same reviewer role recorded in
  `operator_dry_run.reviewer_role` through an explicit `reviewer_role:` (or
  equivalent reviewer-role key) line, so free-text mentions or a command-line
  role claim cannot stand in for the operator dry-run artifact. The tools now
  also require substantive dry-run observations beyond the role line and
  checked workflow IDs: copied checklist-only notes, blank `Operator notes:`
  sections, and placeholder notes are rejected by inventory, manifest
  preparation, and final audit. Release README/checklist guidance tells the
  operator to record concrete drawing/zone observations, synchronized
  Before/After review, decisions, confirmed-only export, and path-audit result.
  The copied package CLI probe
  `tmp/drawing_compare_operator_notes_substance_probe_current` returns
  `status=incomplete` with
  `operator notes file must include substantive dry-run review notes beyond role and checklist`.
  Customer evidence manifest path-leakage hardening now also prevents
  `prepare_drawing_compare_customer_evidence.py` from writing absolute paths
  into `customer_evidence_manifest.json` or its generated
  `sharable_path_audit_summary.json`: artifact, Workbench acceptance, and
  path-audit source references are stored as POSIX-style relative paths. The
  final customer-grade audit independently scans both JSON files and rejects
  absolute/cache/temp path leakage. The copied package CLI probe
  `tmp/drawing_compare_manifest_path_probe_current` confirms generated manifest
  and audit JSON files have no drive-path or `.codex` leakage even when the
  validation folders are outside the evidence manifest directory. Operator
  dry-run notes are now read with UTF-8 BOM and UTF-16 tolerance before role,
  workflow, and substantive-note checks, so Windows-created evidence files are
  not rejected because of encoding alone. The copied package CLI probe
  `tmp/drawing_compare_operator_notes_encoding_probe_current` confirms a
  PowerShell-written UTF-8 BOM notes file satisfies the structural reviewer role
  parser. The customer evidence inventory now also supports
  `--portable-paths`, which emits `root_N` aliases instead of absolute local
  paths for inventory JSON that may be attached to customer evidence; the
  current portable inventory probe has no drive, user-profile, temp, or
  `.codex` path matches.
  CAD block text no-expansion evidence is now explicit:
  `validate_drawing_compare_realset.py` records
  `input.cad_policy.expand_blocks` and
  `input.cad_policy.block_text_detection`; the current validation output
  `tmp/drawing_compare_cad_block_text_no_expand_current` was run with
  `--no-expand-blocks`, keeps `block_text_detection=true`, and detects an
  `ATTRIB` `@100 -> @200` spacing change as modified content rather than
  added/deleted noise. The strict audit check
  `cad_block_text_detection_without_expansion` now requires this evidence.
  Release README and the prompt-to-artifact checklist now include
  `<cad_block_text_no_expand_validation>` in the manifest and final-audit
  example commands so customer operators do not omit this required result
  source. Inventory and manifest preparation now also expose and require the
  same evidence through `has_cad_block_text_no_expand`,
  `diagnostics.validation_outputs_with_cad_block_text_no_expand`, and
  `cad_policy_evidence.block_text_detection_without_expansion`, so the gap is
  caught before the final customer-grade audit. The final audit now also
  rejects stale customer manifests that omit
  `cad_policy_evidence.block_text_detection_without_expansion` or declare it
  without matching audited no-expand CAD block-text evidence.
  The current
  customer-shareable ZIP was
  re-synchronized with the stricter audit CLI and template copies; the copied
  ZIP audit CLI contains the stale-manifest guard and the ZIP manifest template
  contains the `cad_policy_evidence` block.
  Customer-grade ground truth was tightened to approved-only after this pass:
  manifest preparation, inventory recommended commands, release README/checklist,
  and final audit now require `ground_truth.status=approved`, and copied package
  CLI/README content reflects the same rule.
  Customer evidence inventory now also self-checks any existing
  `customer_evidence_manifest.json` found in scanned roots and reports
  `customer_evidence_manifest_summaries`,
  `diagnostics.customer_evidence_manifests_not_ready`, and
  `diagnostics.customer_evidence_manifests_missing_approved_ground_truth`.
  This exposes stale readiness blocks, non-ready manifests, path-audit failures,
  and reviewed-only ground-truth manifests before the final customer-grade
  audit. The current portable inventory has no existing customer manifest
  candidates, so these diagnostics are empty.
  Release README and `mvp_exit_prompt_to_artifact_checklist.md` now also tell
  operators to inspect these stale-manifest diagnostics before final audit; the
  current customer-shareable ZIP includes the updated README/checklist. The
  packaged-release audit now also requires the stale-manifest diagnostics as
  prompt-to-artifact checklist terms, so an old checklist cannot pass the
  customer-grade release gate. The same required-term gate is now applied to
  the ZIP-internal `mvp_exit_prompt_to_artifact_checklist.md`, so a refreshed
  external release manifest cannot hide stale customer ZIP checklist content.
  Customer-shareable packages now also include
  `customer_evidence_closeout_packet.md`, which names the two remaining
  external artifacts, the required ready inventory status, manifest generation
  command, and final customer-grade audit command. The closeout packet is
  guidance-only and `closeout` paths are rejected by inventory, manifest
  preparation, and final audit if used as truth/operator evidence.
  Focused validate/evidence/audit/inventory/release regression passed
  `174/174`; script bytecode compilation passed; direct customer ZIP scan
  reports `leak_count=0`, `scanned_files=12`,
  `scanned_app_first_party_files=472`, and `scanned_binary_files=7`; and the
  refreshed strict synthetic exit audit remains `24/25` with only
  `customer_grade_evidence_declared` failing by design.
- Starting workflow gate hardening: `mvp_exit_prompt_to_artifact_checklist.md`
  now includes a dedicated row for selecting two DWG/DXF files/folders or two
  PDF files/folders and completing automatic comparison with `_SUCCESS`.
  Customer-grade packaged release audit requires `input_selection` and
  `automatic_compare_completed` terms in both the external checklist and the
  ZIP-internal checklist. The current package was rebuilt with PyInstaller
  after this change; packaged launch smoke passed, copied-package CLI audit
  reproduces the expected `24/25` synthetic result, and focused regression is
  `174/174`.

## Required Final Closeout

To close the active goal:

1. Run validation on an approved 20-50 sheet customer/customer-grade set with
   DWG/DXF evidence, PDF-PDF evidence, CAD-PDF blocked evidence, selected-zone
   evidence enabled, and `--review-ground-truth`.
2. Have a structural review lead/team lead run the Workbench workflow, set an
   approved `operator_dry_run.reviewer_role`, check every required operator
   workflow ID in the dry-run notes, and write concrete observations beyond the
   checklist.
3. Ensure the real `review_ground_truth.csv` keeps the required columns
   `drawing_label,category,summary_contains,source_format,detection_source,bbox_status`
   has `ground_truth.status=approved`, and is not a release template/handoff
   artifact.
4. Generate `customer_evidence_manifest.json` with
   `prepare_drawing_compare_customer_evidence.py`.
5. Run `audit_drawing_compare_mvp_exit.py --evidence-level customer_grade`.
6. Declare completion only when the final audit passes with no failed checks.
