# Current Readiness Blockers

Last updated: 2026-06-28

Scope decision: **Option B plus bounded freshness probes**. This document records
the smallest safe next slice after the 2026-06-28 status/freeze sync: make the
current readiness blockers explicit with external evidence, without fabricating
customer-grade evidence and without changing product behavior.

Current HEAD used for this pass:
`6b13a395a4fcf788027f316f3ea89a969f916c5b`

Execution-grade follow-up plan:
`docs/collab/REMAINING_READINESS_CLOSURE_PLAN.md`

## Blocker Table

| Axis | Current state | External evidence | Closure condition | Next action |
|------|---------------|-------------------|-------------------|-------------|
| Full-suite freshness | **BLOCKED by failing current evidence**. Current HEAD proof now exists, but the full suite is not green. | GitHub `Full Suite Health` run `28323513823` targeted `6b13a395a4fcf788027f316f3ea89a969f916c5b` and concluded `failure`; downloaded artifact `build\reports\full-suite-health_28323513823\full-suite-junit.xml` contains `4,204` cases: `4,162 passed / 11 failed / 31 skipped / 0 errors`. | A full-suite health run for current HEAD exits 0 and its artifact is recorded. | Remediate or explicitly retire the 11 failing tests, rerun `.github/workflows/full-suite-health.yml`, and update `docs/FULL_SUITE_HEALTH_REPORT.md` with the new artifact and exit code. |
| Customer-grade evidence | **BLOCKED**. Customer-grade completion is not claimed. | `docs/collab/DRAWING_COMPARE_MVP_CURRENT_AUDIT.md` says `Status: **not complete**`; it lists missing approved non-empty `review_ground_truth.csv`, operator dry-run notes, `customer_evidence_manifest.json`, and final `audit_drawing_compare_mvp_exit.py --evidence-level customer_grade` pass. `Get-ChildItem tmp -Filter "*customer*"` and `*mvp*` printed no matching local evidence files in this pass. | Approved customer or customer-representative artifacts exist and the customer-grade audit exits 0. | Collect the 20-50 sheet approved ground truth, dry-run notes, and manifest; then run the customer-grade audit before changing status wording. |
| AC1032 support claim boundary | **GUARD**. Experimental AC1032 native reading exists, but customer-facing native support is not claimed. | `README.md` describes default native DWG import as conservative `AC1015` and routes newer DWG through converted-DXF fallback or configured converter paths; `build/reports/dwg-all-version-native-audit.json` currently has `status: failed`, `native_missing_versions: ["AC1032"]`; `build/reports/dwg-native-release-readiness-audit.json` has `status: skipped`, `reason: native_validation_failed`. | Customer-facing AC1032 native support may be claimed only after native release-readiness evidence passes and the support contract is updated. | Keep README/docs/release-facing text on the default-off experimental boundary. Do not advertise AC1032 native support. |
| Release artifact freshness | **OPEN**. Current release-readiness artifacts are not fresh passing release proof. | Latest inspected `build/reports/dwg-native-release-readiness-audit.json` is skipped because `native_validation_failed`; latest inspected `build/reports/dwg-all-version-native-audit.json` is failed for AC1032 native readiness. The latest GitHub `cad-format-regression.yml` run for `a00794573...` succeeded, but that is not a full release artifact refresh. | Release-facing evidence for current HEAD exists, exits 0, and is linked from the release notes/status before release claims. | Regenerate release-readiness artifacts once the intended release scope is fixed; do not use the failed/skipped native audits as release PASS evidence. |

## Evidence Log

| Claim | Command or file state | Result |
|-------|-----------------------|--------|
| Current CAD regression signal exists for HEAD | `gh run list --workflow cad-format-regression.yml --limit 1 --json databaseId,status,conclusion,headBranch,headSha,createdAt,updatedAt` | Exit 0. Latest `main` run `28289817876` succeeded for `a00794573a3a63bd239dc5fb278066569f8420c3`, created `2026-06-27T12:54:55Z`, updated `2026-06-27T12:57:05Z`. |
| Full-suite GitHub freshness exists but fails | `gh run view 28323513823 --json databaseId,status,conclusion,headBranch,headSha,createdAt,updatedAt,url,jobs` | Exit 0. Run `28323513823` completed with `conclusion=failure`, `headBranch=main`, `headSha=6b13a395a4fcf788027f316f3ea89a969f916c5b`, created `2026-06-28T13:18:08Z`, updated `2026-06-28T13:24:11Z`. |
| Full-suite JUnit artifact exists | `gh run download 28323513823 -n full-suite-junit -D build\reports\full-suite-health_28323513823`; `Test-Path build\reports\full-suite-health_28323513823\full-suite-junit.xml` | Download exit 0. Artifact exists locally and parses to `4,204` cases: `4,162 passed / 11 failed / 31 skipped / 0 errors`. |
| Local suite size has drifted since the historical report | `python -m pytest --collect-only -q -p no:cacheprovider -o log_cli=false` | Exit 0. Tail output included `4204 tests collected in 2.43s`. |
| Customer-grade local artifacts are not present in `tmp/` | `Get-ChildItem tmp -Filter "*customer*"` and `Get-ChildItem tmp -Filter "*mvp*"` | Exit 0. No matching files printed. |
| Native release-readiness proof is not passing | `Get-Content build\reports\dwg-native-release-readiness-audit.json` | File state: `{"schema_version":1,"status":"skipped","reason":"native_validation_failed","native_validation_status":"failed"}`. |
| All-version native readiness proof is not passing | `Get-Content build\reports\dwg-all-version-native-audit.json` | File state: `status: failed`; `native_missing_versions: ["AC1032"]`; blockers include `sample_count=0/2`, `real_pair_count=0/2`, and `native_supported=false`. |

## Non-Claims

- This document does **not** claim customer-grade completion.
- This document does **not** claim full-suite health is green for HEAD.
- This document does **not** claim customer-facing AC1032 native DWG support.
- This document does **not** claim release artifact freshness.
