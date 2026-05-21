# Drawing Compare MVP Scorecard

Date: 2026-05-13 KST
Work item: WI-20260510-001
Status: 9.6/10 by current audited gates, not customer-grade complete

## Executive Verdict

The current Drawing Compare Workbench is implementation-ready for an internal
pilot and close to customer-distribution readiness. The active 10/10 goal is not
complete because the final customer-grade evidence gate still lacks a real,
approved review ground-truth CSV and structural review lead dry-run notes. The
customer evidence manifest is generated from those inputs by
`prepare_drawing_compare_customer_evidence.py`; it is not a hand-written
external input.

The latest explicit customer-grade audit probe is:

`tmp/drawing_compare_mvp_exit_audit_customer_grade_gate_schema15_current.json`

Result:

- `status=failed`
- `summary.passed=25`
- `summary.failed=1`
- `completed_pairs=26`
- `queue_items=26`
- only failed check:
  `customer_grade_evidence_declared: --customer-evidence-manifest is required for customer_grade evidence`

## Score Summary

| Area | Score | Basis | Residual risk |
| --- | ---: | --- | --- |
| Core review-lead workflow | 9.5/10 | `review_queue`, Korean summaries, synced selected-zone review, tri-state review status, confirmed-only export are covered by synthetic exit audit, Workbench acceptance smoke, and the 2026-05-13 16:05 focused regression re-run. | Needs structural review lead dry-run on real/customer-grade set. |
| DWG/DXF comparison and large-DWG performance | 9.7/10 | S20 stalled pair now completes `DwgDiffer.compare()` in `55.235s`; 350,178 records streamed; memory list capped at 50,000; final audit now includes passing `large_dwg_performance_probe`; full focused regression passed. | Real customer DWG diversity and ODA conversion edge cases still require field evidence. |
| PDF-PDF comparison and viewer behavior | 9.1/10 | PDF-PDF evidence, `image_pixels` bbox policy, selected-zone telemetry, and DPI-scaled selected-zone viewer fix are covered. | Real scanned/low-quality PDFs may expose OCR/visual-diff recall gaps. |
| Review queue quality and structural coverage | 9.4/10 | Audit covers member add/delete/move, section/dimension, D13/SHD13 spacing, grid, structural text, Korean reasons/summaries, and Top 3-5 first display. | Recall is proven on controlled evidence, not yet on approved customer truth. |
| Export and customer-shareable package | 9.8/10 | PyInstaller build, launch smoke, customer package path audit, direct ZIP inspection, no raw JSONL/NDJSON, and leakage `0` all pass. | Final customer package should be re-audited after attaching real evidence manifest. |
| Operational reliability | 9.6/10 | `_SUCCESS`, preflight, AI-optional heuristic fallback, selected-zone render budgets, package smoke, and PowerShell-safe customer CLI JSON are covered. | Long-running real pilot should still monitor ODA/temp/disk/font failures. |
| Customer-grade evidence readiness | 6.0/10 | Evidence tools, templates, inventory, manifest, final audit, and anti-bypass checks are implemented. | Required external artifacts are absent, so final MVP exit remains blocked. |

Overall audited gate score: **25/26 = 9.6/10**.

Formal customer-grade completion: **not passed**.

## Concrete Evidence

| Evidence | Current result |
| --- | --- |
| Large DWG S20 probe | `tmp/dwg_s20_dwg_differ_after_fix.json`, `elapsed_s=55.235` |
| Large DWG audit gate | `large_dwg_performance_probe`, passed in current synthetic and customer-grade gate probes |
| Inventory large-DWG readiness gate | `tmp/drawing_compare_customer_evidence_inventory_completion_recheck_schema12_large_dwg.json`, `large_dwg_probe_passed=true`; recommended final audit command includes `--large-dwg-probe` and `--require-large-dwg-probe` |
| Current packaged release | `tmp/drawing_compare_release_mvp_packaged_fix_large_dwg_request_ko_ascii_json_probe_filter_precise/release_manifest.json`, `status=passed` |
| Full comparison regression | Release manifest `comparison_tests`, `status=passed`, `2232 passed, 2 skipped` in `78.29s` |
| PyInstaller build | Release manifest `pyinstaller_build`, `status=passed` |
| Packaged launch smoke | Release manifest `packaged_app_launch_smoke`, `status=passed` |
| Customer path leakage audit | `customer_package_path_audit.json`, `leak_count=0` |
| Customer Korean request sheet | Current package file and ZIP entry byte-match `docs/collab/DRAWING_COMPARE_CUSTOMER_EVIDENCE_REQUEST_KO.md`, SHA256 `D59182998D74A539D35FDD8D48002ED719EACE93401DF149D0E42D144BFBB48A`; final audit now rejects ZIP request sheets missing required Korean request terms |
| Synthetic MVP exit audit | `tmp/drawing_compare_mvp_exit_audit_synthetic_schema26_current.json`, `25/26`, only `customer_grade_evidence_declared` failing because synthetic evidence is not final completion evidence |
| Explicit customer-grade gate probe | `tmp/drawing_compare_mvp_exit_audit_customer_grade_gate_schema15_current.json`, `25/26`, missing customer evidence manifest; `large_dwg_performance_probe` passed |
| Focused release/evidence/audit regression | 2026-05-13 16:05 KST re-run passed 156/156 for release, prepare, inventory, and audit tests |
| Candidate evidence scan | `docs/collab/DRAWING_COMPARE_CUSTOMER_EVIDENCE_CANDIDATE_SCAN.md`, no ready customer-grade evidence found after workspace, user-profile, common sync/public, targeted drive-root, Google Drive, Gmail, Dropbox, Notion/internal-source, and GitHub searches |
| Closeout runbook | `docs/collab/DRAWING_COMPARE_CUSTOMER_GRADE_CLOSEOUT_RUNBOOK.md` |
| Schema29 intake check | `tmp/drawing_compare_customer_evidence_intake_check_schema29.json`, `status=missing_required_files`; the prepared inbox still lacks real `review_ground_truth.csv`, real `operator_dry_run_notes.md`, and a generated ready `customer_evidence_manifest.json` |
| Current evidence inbox handoff ZIP | `tmp/drawing_compare_customer_evidence_inbox_request_ko_ascii_json_schema31.zip`, 5 entries, SHA256 `F27EB812C82334ED93793B123F0511244C0F3AD5A4142CCBB6992C8DB7D2058F`; README includes the current same-name probe manifest recheck, handoff only, not completion evidence |
| Korean evidence request package smoke | `tmp/drawing_compare_release_mvp_smoke_customer_evidence_request_ko`, request sheet included in package/ZIP and path audit `leak_count=0` |
| Copied package CLI verification | `tmp/drawing_compare_mvp_exit_audit_large_dwg_request_ko_ascii_json_probe_filter_precise_copied_cli.json`, `tmp/drawing_compare_customer_evidence_inventory_probe_filter_precise_copied_cli_whole_workspace.json`, and `tmp/drawing_compare_customer_evidence_inventory_copied_cli_package_self_scan_large_dwg.json`; copied inventory reports `large_dwg_probe_passed=true` and outputs PowerShell-safe JSON |

## Findings

- Severity: HIGH
  Impact: The active 10/10 customer MVP goal cannot be closed yet.
  Evidence: `customer_grade` audit fails because no
  `customer_evidence_manifest.json` is supplied.
  Recommendation: collect approved non-template `review_ground_truth.csv` and
  structural review lead dry-run notes, then generate the manifest and rerun the
  final audit.
  Tests: `tmp/drawing_compare_mvp_exit_audit_customer_grade_gate_schema15_current.json`.

- Severity: MEDIUM
  Impact: Product behavior is well covered on synthetic/controlled cases, but
  recall on customer drawings is not yet independently proven.
  Evidence: Current candidate scan found only probe/template artifacts, no
  approved customer-grade truth set.
  Recommendation: run the current package on a 20-50 sheet customer-grade set
  and approve the resulting review ground truth.
  Tests: final `audit_drawing_compare_mvp_exit.py --evidence-level customer_grade`.

- Severity: LOW
  Impact: The current working tree contains broad unrelated historical changes,
  which increases review overhead.
  Evidence: `git status --short` lists many Drawing Compare and legacy files
  beyond the large-DWG fix set.
  Recommendation: keep the large-DWG/performance/package changes reviewed as a
  focused slice and avoid reverting unrelated dirty files.
  Tests: targeted diff/stat review before any commit.

## Final Gate To Reach 10/10

The following must all be true before the active goal can be marked complete:

1. A real `review_ground_truth.csv` exists with the required schema, no template
   rows, and `ground_truth.status=approved`.
2. A real `operator_dry_run_notes.md` exists from an approved structural review
   lead/team lead role, covers all required workflow IDs, and contains
   substantive observations.
3. `prepare_drawing_compare_customer_evidence.py` creates a
   `customer_evidence_manifest.json` with `readiness.status=ready` and
   `readiness.issue_count=0`.
4. `audit_drawing_compare_mvp_exit.py --evidence-level customer_grade` passes
   with `status=passed` and zero failed checks.

Until those four conditions pass, the correct status is:

**IN_PROGRESS, not complete.**
