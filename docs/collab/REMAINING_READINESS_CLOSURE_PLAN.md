# Remaining Readiness Closure Plan

Last updated: 2026-06-28

Current HEAD used for this plan:
`a00794573a3a63bd239dc5fb278066569f8420c3`

This is an execution plan, not a readiness claim. It upgrades the blocker table
in `docs/collab/CURRENT_READINESS_BLOCKERS.md` into ordered closure slices with
external verifier signals. Do not mark any item complete until the named command
or artifact proves it.

## Recommended sequence

1. Refresh full-suite health for current HEAD.
2. Intake or generate only real customer-grade evidence; stop if the approved
   truth CSV and structural lead dry-run notes are missing.
3. Run closeout dry-run and `audit_closeout_readiness.py` before any real
   customer-grade closeout.
4. Refresh release-readiness artifacts for the exact release scope.
5. Keep AC1032 native support as a claim-boundary guard unless native gates pass.

## Blocker Classes

| Class | Item | Why this class | First closure slice |
|-------|------|----------------|---------------------|
| **INTERNALLY CLOSABLE** | Full-suite freshness | The repository already has a manual workflow and local command. The blocker is missing current HEAD execution, not missing customer data. | Trigger or locally run the full suite, preserve the JUnit artifact, and update `docs/FULL_SUITE_HEALTH_REPORT.md` with exit code and test count. |
| **EXTERNAL EVIDENCE BLOCKED** | Customer-grade evidence | Current code and synthetic probes cannot replace approved `review_ground_truth.csv` plus structural review lead dry-run notes. | Run inventory only after real inputs exist; then generate the manifest and run closeout readiness. |
| **CLAIM-BOUNDARY GUARD** | AC1032 native support | Current inspected native artifacts are failed/skipped for AC1032; the safe action is preventing support overclaim. | Keep `cad_policy_gate.py` green and run native claim audits only when a native claim is explicitly requested. |
| **RELEASE-ARTIFACT FRESHNESS** | Release readiness artifacts | Current native release artifacts are fresh but failed/skipped; historical passing artifacts cannot prove a current release. | Regenerate release-readiness artifacts after the target release scope and evidence manifest are fixed. |

## Command Matrix

| Slice | Command | Required inputs | PASS signal | STOP condition |
|-------|---------|-----------------|-------------|----------------|
| Full-suite GitHub run | `gh workflow run full-suite-health.yml --ref main` | GitHub auth and Actions permission. | Workflow dispatch returns exit 0 and a run appears in `gh run list --workflow full-suite-health.yml --branch main --limit 1 --json databaseId,status,conclusion,headSha,createdAt,updatedAt`. | No run appears, run targets the wrong `headSha`, or the workflow cannot be dispatched. Record as freshness blocker. |
| Full-suite GitHub watch | `gh run watch <run-id> --exit-status` | Run id from the previous row. | Exit 0 and conclusion `success`. | Nonzero exit, cancelled run, timed out run, or runner crash. Do not update health as passing. |
| Full-suite artifact capture | `gh run download <run-id> -n full-suite-junit -D build\reports\full-suite-health_<run-id>` | Successful full-suite run with uploaded artifact. | `build\reports\full-suite-health_<run-id>\full-suite-junit.xml` exists and matches the watched run id. | Missing artifact or mismatched run id. Record artifact gap. |
| Full-suite local equivalent | `python -m pytest -p no:cacheprovider -n auto -q -o log_cli=false -rfE --junitxml=build\reports\full-suite-junit.xml` | Local pytest 9 stack with xdist, pytest-qt, PyMuPDF, and headless Qt environment as in `.github/workflows/full-suite-health.yml`. | Exit 0 and `build\reports\full-suite-junit.xml` exists. | Any test failure, access violation, worker crash, or missing JUnit output. Classify failures before claiming freshness. |
| Customer evidence inventory | `python scripts\inventory_drawing_compare_customer_evidence.py --root <validation_root> --root <customer_evidence_folder> --large-dwg-probe <large_dwg_probe.json> --portable-paths --out <inventory.json>` | Real non-template `review_ground_truth.csv`, structural review lead/team lead `operator_dry_run_notes.md`, validation outputs, and large-DWG probe. | Inventory JSON has `status=ready_for_manifest`, valid truth candidate diagnostics, approved structural role diagnostics, and no missing workflow checks. | Missing truth CSV, missing dry-run notes, template/probe paths, non-approved ground truth, missing large-DWG probe, or incomplete diagnostics. |
| Source checkout closeout dry-run | `python scripts\closeout_drawing_compare_customer_evidence.py --source-checkout . --dry-run --plan-json <closeout_plan.json> --readiness-json <closeout_readiness.json> --out <closeout_out> <customer evidence args>` | Source checkout path, standard result dirs, release manifest, large-DWG probe, approved truth CSV, review-decision truth, dataset strata, structural lead notes, confirmed export artifact, and dataset approval metadata. | Exit 0; `closeout_plan.json` and `closeout_readiness.json` exist. | Required input missing, generated plan unavailable, proof outputs mixed into final audit result dirs, or readiness status not ready. |
| Packaged closeout dry-run | `python cli\closeout_drawing_compare_customer_evidence.py --source-checkout <source_checkout> --dry-run --plan-json <closeout_plan.json> --readiness-json <closeout_readiness.json> --out <closeout_out> <customer evidence args>` | A packaged customer-shareable release copy that contains `cli\closeout_drawing_compare_customer_evidence.py`. | Same PASS signal as source checkout dry-run. | In the source checkout, `cli\closeout_drawing_compare_customer_evidence.py` does not exist. Use `scripts\...` there; use `cli\...` only inside packaged release copies. |
| Closeout readiness audit | `python scripts\audit_closeout_readiness.py --readiness-json <closeout_readiness.json> --plan-json <closeout_plan.json> --require-ready --out <closeout_readiness_audit.json>` | Dry-run readiness and plan JSON from the previous row. | Exit 0 and output JSON has `status=passed`. | Nonzero exit, `status` not passed, proof routing invariant failure, stale generated evidence path, or tile-cache env leakage. |
| Final customer-grade audit | `python scripts\audit_drawing_compare_mvp_exit.py --results-dir <dwg_validation> --results-dir <pdf_validation> --results-dir <cad_pdf_block_validation> --results-dir <cad_block_text_no_expand_validation> --release-manifest <release_manifest.json> --large-dwg-probe <large_dwg_probe.json> --require-large-dwg-probe --customer-evidence-manifest <customer_evidence_manifest.json> --evidence-level customer_grade --min-total-pairs 20 --max-total-pairs 50 --max-first-review-ready-s 1800 --max-cold-zone-render-ms 10000 --max-cache-hit-zone-render-ms 2000 --out <mvp_exit_audit.json>` | Generated ready customer evidence manifest and all standard validation outputs. | Exit 0; output JSON has `status=passed` and zero failed checks. | Any failed check. Do not claim customer-grade completion. |
| Release readiness audit | `python scripts\audit_drawing_compare_release_readiness.py --result-json <result.json> --run-manifest <run_manifest.json> --customer-evidence-manifest <customer_evidence_manifest.json> --baseline-metrics <baseline_metrics.json> --dwg-all-version-audit <dwg_all_version_audit.json> --out <release_readiness_audit.json>` | Current result JSON, run manifest, customer evidence manifest, baseline metrics, and all-version fallback audit for the intended release scope. | Exit 0; output JSON has `status=passed`, or an explicitly accepted `partial` with documented allowed partial reasons. | Missing provenance, missing customer evidence counts, forbidden wording, failed fallback readiness, path leakage, ODA/default runtime violation, or unsupported partial claim. |
| Native/AC1032 claim audit | `python scripts\audit_drawing_compare_release_readiness.py --result-json <result.json> --run-manifest <run_manifest.json> --customer-evidence-manifest <customer_evidence_manifest.json> --baseline-metrics <baseline_metrics.json> --dwg-all-version-audit <dwg_all_version_audit.json> --native-dwg-audit <native_dwg_audit.json> --dwg-json-bridge-contract <contract.json> --require-native-dwg --out <native_release_readiness_audit.json>` | Native audit, bridge contract, product-path bridge evidence, and release metrics for the exact native claim. | Exit 0 and `status=passed`; native-ready versions cover the claimed versions. | Current expected state is fail/blocked for customer-facing AC1032 native support. If this fails, keep AC1032 as experimental/default-off and claim-blocked. |
| Claim guard | `python scripts\cad_policy_gate.py` plus targeted `rg` over README/docs/release-facing text | Current docs and release-facing text. | Exit 0; matches are only forbidden examples, guard strings, or negative/blocker statements. | Any customer-facing text claims customer-grade completion, current full-suite pass, release-ready status, broad DWG support, or AC1032 native support. |

## Slice Acceptance Rules

### Full-suite freshness

Accept only a current HEAD full-suite run. A collect-only result such as
`4204 tests collected` is useful drift evidence, but it is not a full-suite
PASS. A historical 2026-06-10 report remains historical after the test count
changes.

Minimum evidence to update `docs/FULL_SUITE_HEALTH_REPORT.md`:

- command or GitHub run id,
- target `headSha`,
- exit code or conclusion,
- collected/passed/failed/skipped counts,
- JUnit artifact path or generated report path,
- known flake/crash classification if nonzero.

### Customer-grade evidence

Customer-grade closure is blocked until the external artifacts exist. Probe
folders, templates, copied release handoff files, synthetic validation, or a
hand-written manifest cannot satisfy this blocker.

Minimum evidence before final audit:

- approved non-template `review_ground_truth.csv`,
- structural review lead/team lead `operator_dry_run_notes.md` with required
  workflow checks and substantive observations,
- generated `customer_evidence_manifest.json` with `readiness.status=ready`,
- closeout readiness audit `status=passed`,
- final `audit_drawing_compare_mvp_exit.py --evidence-level customer_grade`
  output `status=passed`.

### Release artifact freshness

Regenerate release-readiness artifacts only for the exact claim scope. A
fallback/customer-ready release audit does not prove native AC1032 support. A
native/all-version claim needs the stricter native audit, bridge contract, and
product-path evidence.

### AC1032 claim boundary

The current safe posture is:

- AC1015 native baseline is the conservative default native path.
- Modern DWGs use converted-DXF fallback or explicit configured converter paths
  unless an approved experimental/internal path is intentionally selected.
- AC1032 customer-facing native support remains unclaimed until native release
  gates pass.

## Current Non-Claims

- No customer-grade completion is claimed.
- No full-suite freshness for current HEAD is claimed.
- No release-ready status is claimed from the failed/skipped native artifacts.
- No customer-facing AC1032 native DWG support is claimed.
