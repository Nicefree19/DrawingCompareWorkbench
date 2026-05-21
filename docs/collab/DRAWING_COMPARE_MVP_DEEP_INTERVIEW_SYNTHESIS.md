# Drawing Compare MVP Deep Interview Synthesis

Date: 2026-05-12 KST
Status: Not complete. The product implementation and synthetic/customer-package
gates are hardened, but final customer-grade evidence is still missing.

## 1. Interview Result In One Sentence

Drawing Compare is not a CAD operator diff viewer. The MVP is a Windows
Workbench for a structural drawing review lead/team lead to quickly find,
understand, decide, and export only important structural drawing changes.

## 2. Primary User And Job

Primary user:

- Structural drawing review lead or structural team lead.

Secondary/non-primary user:

- CAD operator. CAD operators may prepare files, but the first screen and
  workflow are not optimized around raw entity counts or drafting operations.

Job to be done:

- Given two drawing files or folders, identify the most important structural
  changes first, inspect each change in a synchronized Before/After view,
  understand the Korean summary and review reason, decide
  `confirmed` / `false_positive` / `hold`, then export only confirmed changes
  as clouds/reports for customer sharing.

## 3. Locked Product Decisions

These are treated as fixed MVP decisions, not open design options:

- The first-class object is `review_queue`.
- Review queue unit is `pair_uuid + zone_id`.
- First screen must show drawing-level structural-core Top 3-5 items before
  raw diff counts.
- CAD truth layer is entity diff.
- CAD structural text includes `TEXT`, `MTEXT`, `ATTRIB`, `ATTDEF`, and block
  text/attributes under `INSERT`.
- Block attribute/text detection must still work with `expand_blocks=False`;
  current evidence proves `block_text_detection=true` catches an `ATTRIB`
  spacing change without expanding blocks.
- `@100 -> @200`, including `D13@100 -> D13@200` and
  `SHD13@100 -> SHD13@200`, should be grouped as modified content when
  possible.
- PDF-PDF must provide the same review UX as CAD.
- PDF bbox coordinate space is `image_pixels`; PDF must not use CAD world
  transform.
- CAD-PDF cross comparison is explicitly blocked for MVP.
- Rendering is metadata-first and selected-zone-first; full drawing
  high-resolution pre-render is not the default path.
- Confirmed-only export is the default sharable export policy.
- Customer-shareable artifacts must not leak absolute paths or cache/state/temp
  paths.
- AI/LLM/embedding is optional quality improvement, not a required dependency.
  Missing models must degrade to warning plus heuristic fallback.

## 4. Explicit MVP Success Criteria

The MVP is complete only when all of these are true:

- DWG/DXF comparison is supported and evidenced.
- PDF-PDF comparison is supported and evidenced.
- CAD-PDF cross comparison is blocked with clear evidence.
- First screen exposes structural-core Top 3-5 review items per drawing.
- Each review item has required queue metadata:
  `drawing_label`, `category`, `priority_score`, `reason_ko`,
  `change_summary_ko`, `source_format`, `detection_source`, `bbox_status`,
  `review_status`.
- Selecting a change zone synchronously focuses Before/After using the same
  review window.
- User can understand what changed through Korean summary/reason.
- User can choose `confirmed`, `false_positive`, or `hold`.
- Confirmed-only clouds/reports can be exported.
- Customer-shareable package has zero absolute/cache/state/temp path leakage.
- `_SUCCESS` is required for completed validation outputs.
- Preflight checks ODA/PyMuPDF/rtree/cache/disk/temp/long-path/font/PDF support.
- 20-50 sheet first-review-ready screen is available within 30 minutes.
- Selected-zone cache hit is within 2 seconds and cold render p95 is within
  10 seconds.
- UI does not freeze during selected-zone rendering.
- Customer-grade evidence is approved, non-template, and audit-passed.

## 5. Current Artifact Map

Current implementation/audit files:

- `scripts/audit_drawing_compare_mvp_exit.py`:
  final exit audit. It validates queue fields, format support, CAD/PDF policy,
  selected-zone performance, confirmed-only export, preflight, release package,
  customer evidence manifest, and customer-shareable ZIP leakage.
- `scripts/prepare_drawing_compare_customer_evidence.py`:
  generates `customer_evidence_manifest.json` from validation outputs plus real
  customer/operator artifacts. It writes readiness status and fails incomplete
  evidence.
- `scripts/inventory_drawing_compare_customer_evidence.py`:
  pre-audit scanner. It reports missing customer-grade evidence before manifest
  generation, including existing `customer_evidence_manifest.json` readiness,
  path-audit, and approved-ground-truth self-checks.
- `scripts/release_drawing_compare_workbench.py`:
  packages the Workbench, evidence CLI tools, README/checklists/templates, and
  `DrawingCompareWorkbench_customer_shareable.zip`.
- `docs/collab/DRAWING_COMPARE_MVP_CURRENT_AUDIT.md`:
  current prompt-to-artifact completion audit.

Current generated evidence:

- `tmp/drawing_compare_customer_evidence_inventory_portable_current.json`
- `tmp/drawing_compare_mvp_exit_audit_current.json`
- `tmp/drawing_compare_cad_block_text_no_expand_current/validation_summary.json`
- `tmp/drawing_compare_customer_shareable_zip_scan_current.json`
- `tmp/drawing_compare_release_mvp_packaged_current/release_manifest.json`
- `tmp/drawing_compare_release_mvp_packaged_current/DrawingCompareWorkbench_customer_shareable.zip`

## 6. What Is Verified Now

The current synthetic/release evidence verifies:

- `completed_pairs=26`, inside the 20-50 pair scale target.
- DWG/DXF and PDF-PDF evidence exists.
- CAD-PDF block evidence exists.
- CAD block attribute/text detection with `expand_blocks=False` is evidenced by
  `tmp/drawing_compare_cad_block_text_no_expand_current`.
- The packaged README and prompt-to-artifact checklist include
  `<cad_block_text_no_expand_validation>` in the manifest and final audit
  commands, so customer evidence collection covers that gate explicitly.
- Inventory and manifest preparation now check the same no-expand CAD block
  text policy evidence before final audit, through
  `has_cad_block_text_no_expand` and
  `cad_policy_evidence.block_text_detection_without_expansion`.
- The final customer-grade audit also requires that manifest declaration and
  cross-checks it against audited no-expand CAD block-text evidence, so a stale
  manifest cannot pass by relying only on result folders.
- Inventory now summarizes any existing `customer_evidence_manifest.json`
  candidate and flags non-ready manifests or manifests whose
  `ground_truth.status` is not `approved` before the final customer-grade audit.
- Release README and `mvp_exit_prompt_to_artifact_checklist.md` surface those
  stale-manifest inventory diagnostics so operators have the same pre-final
  checklist in the customer-shareable package.
- Customer-shareable packages also include
  `customer_evidence_closeout_packet.md`, a guidance-only handoff sheet for the
  two external customer-grade artifacts and final commands; `closeout` paths
  are rejected as evidence.
- The prompt-to-artifact checklist now separates the starting workflow:
  selecting two DWG/DXF files/folders or two PDF files/folders and completing
  automatic comparison with `_SUCCESS`. Final packaged audit requires
  `input_selection` and `automatic_compare_completed` in both external and
  ZIP-internal checklist content.
- The packaged-release audit requires the same diagnostics as checklist terms,
  preventing stale customer packages with older prompt-to-artifact checklists
  from satisfying the customer-grade release gate.
- The packaged-release audit now also reads the customer ZIP-internal
  `mvp_exit_prompt_to_artifact_checklist.md` and applies the same required-term
  gate there, preventing stale customer ZIP checklist content from passing
  because the external release manifest/checklist was refreshed.
- All completed validation outputs expose structural-core Top review queue
  first-screen evidence.
- All completed validation outputs have selected-zone telemetry.
- Sharable validation outputs report path leakage 0.
- Workbench acceptance evidence exists for confirmed/false-positive/hold and
  confirmed-only export behavior.
- AI missing-model fallback is warning plus heuristic.
- Strict synthetic exit audit result is `24/25`.
- The only failing synthetic audit check is
  `customer_grade_evidence_declared`, which is intentionally required for final
  MVP completion.
- Current customer-shareable ZIP has no `release_manifest.json`, no
  `__pycache__`, no `.pyc/.pyo`, and direct payload path scan reports
  `leak_count=0` with positive text, first-party app, and binary scan coverage.

## 7. What Is Still Missing

These cannot be fabricated from code or synthetic outputs:

- A real, non-template `review_ground_truth.csv` for the customer/customer-grade
  20-50 sheet validation set.
- The truth CSV must keep this schema:
  `drawing_label,category,summary_contains,source_format,detection_source,bbox_status`.
- The truth CSV must replace template examples with approved expected changes;
  rows that still contain `example`, `sample`, or `template` markers are not
  accepted.
- The truth CSV must contain customer-approved structural changes and valid
  source/detection/bbox tokens.
- The truth CSV status must be `approved`; `reviewed` is no longer enough for
  manifest readiness or final customer-grade audit.
- Completed operator dry-run notes from an approved structural review lead/team
  lead.
- Operator notes must include an explicit `reviewer_role:` line with an
  approved role and checked workflow IDs for input selection, automatic
  comparison, Top queue review, synchronized zone zoom, Korean summary/reason,
  review decisions, confirmed-only export, and path leakage check.
- Operator notes must also include concrete dry-run observations beyond the
  role line and checked workflow IDs. A copied checklist with `[x]` rows but no
  substantive notes is rejected.
- Operator notes may be written as UTF-8, UTF-8 with BOM, or UTF-16; evidence
  tooling normalizes those encodings before checking reviewer role, workflow
  IDs, and substantive notes.
- Approved reviewer role values include `structural_review_lead` and Korean
  role labels such as `구조검토책임자`, `구조검토팀장`,
  `구조도면검토책임자`, and `구조도면검토팀장`.
- `customer_evidence_manifest.json` generated from those real artifacts.
- Generated customer evidence JSON artifacts must not contain absolute,
  cache/state/temp, or worktree paths; manifest and audit JSON references must
  be relative.
- Inventory JSON that may be attached to customer evidence should be generated
  with `--portable-paths` so local paths are represented by `root_N` aliases.
- Final audit command with `--evidence-level customer_grade` passing with zero
  failed checks.

## 8. Open Questions That Still Require User/Customer Input

Only these questions remain open because the repository cannot answer them:

1. Which 20-50 sheet customer/customer-grade validation set is approved for MVP
   exit?
2. Who is the approver for `dataset_provenance.approval_status=approved_for_mvp_exit`?
3. Who is the structural review lead/team lead who will perform and sign the
   operator dry run?
4. Where should the completed `review_ground_truth.csv` and
   `operator_dry_run_notes.md` be stored before manifest generation?

## 9. Final Closeout Sequence

Run the inventory first:

```powershell
python scripts\inventory_drawing_compare_customer_evidence.py `
  --root <customer_evidence_root> `
  --portable-paths `
  --out <inventory.json>
```

Do not proceed unless `status=ready_for_manifest`.

Then generate the manifest:

```powershell
python scripts\prepare_drawing_compare_customer_evidence.py `
  --results-dir <dwg_validation> `
  --results-dir <pdf_validation> `
  --results-dir <cad_pdf_block_validation> `
  --results-dir <cad_block_text_no_expand_validation> `
  --out <customer_evidence_manifest.json> `
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
  --confirmed-export-artifact <artifacts\confirmed_clouds\pair_confirmed.png> `
  --min-total-pairs 20 `
  --max-total-pairs 50 `
  --max-first-review-ready-s 1800 `
  --max-cold-zone-render-ms 10000 `
  --max-cache-hit-zone-render-ms 2000
```

Finally run the customer-grade exit audit:

```powershell
python scripts\audit_drawing_compare_mvp_exit.py `
  --results-dir <dwg_validation> `
  --results-dir <pdf_validation> `
  --results-dir <cad_pdf_block_validation> `
  --results-dir <cad_block_text_no_expand_validation> `
  --release-manifest <release_manifest.json> `
  --customer-evidence-manifest <customer_evidence_manifest.json> `
  --evidence-level customer_grade `
  --min-total-pairs 20 `
  --max-total-pairs 50 `
  --max-first-review-ready-s 1800 `
  --max-cold-zone-render-ms 10000 `
  --max-cache-hit-zone-render-ms 2000 `
  --out <mvp_exit_audit.json>
```

Completion can be declared only when this final audit returns `status=passed`.
