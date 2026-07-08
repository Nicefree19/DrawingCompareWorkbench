# Collaboration Status

Last updated: 2026-06-28

## Active Work

- Current owner: Codex
- Current thread: Full-suite freshness closure loop - current HEAD evidence refresh without product behavior changes
- Branch: `main`
- Current HEAD: `6b13a395a4fcf788027f316f3ea89a969f916c5b`
- State: The repository is healthier than the stale 2026-06-10 status suggested, but it is not customer-grade complete and full-suite health is not green. GitHub full-suite run `28323513823` executed against current HEAD `6b13a395a4fcf788027f316f3ea89a969f916c5b` and failed with `4,162 passed / 11 failed / 31 skipped / 0 errors`; the downloaded JUnit artifact is `build\reports\full-suite-health_28323513823\full-suite-junit.xml`.

## Current Truths

- **Release/pilot packaging**: PR #56-#62 reliability/pilot enablement is merged. `scripts/build_pilot_packet.py` assembles a reproducible pilot packet from a built app directory, with auto `pilot_spotcheck.md` guide wording and deterministic tests.
- **Customer-grade readiness**: Still open. `docs/collab/DRAWING_COMPARE_MVP_CURRENT_AUDIT.md` remains `Status: not complete`; customer-approved ground truth, operator dry-run notes, ready manifest, and final `audit_drawing_compare_mvp_exit.py --evidence-level customer_grade` pass are still required before any customer-grade completion claim.
- **Full-suite health**: `docs/FULL_SUITE_HEALTH_REPORT.md` now records current-HEAD freshness evidence from GitHub run `28323513823`. The run targeted `6b13a395a4fcf788027f316f3ea89a969f916c5b`, produced `build\reports\full-suite-health_28323513823\full-suite-junit.xml`, and failed with `11` test failures, so full-suite health remains an open remediation blocker rather than a PASS.
- **Current readiness blockers**: The live blocker table is `docs/collab/CURRENT_READINESS_BLOCKERS.md`; it separates full-suite freshness, customer-grade evidence, AC1032 support-claim boundary, and release artifact freshness, with command/file/GitHub evidence for each state.
- **Remaining readiness closure plan**: `docs/collab/REMAINING_READINESS_CLOSURE_PLAN.md` is the execution-grade sequence for the remaining work: full-suite freshness first, external customer-grade evidence intake, closeout readiness audit, release artifact refresh, and AC1032 claim guard.
- **DWG support posture**: Default/customer-safe native DWG import remains conservative. AC1015 is the safe native baseline; newer DWG workflows should use converted-DXF fallback or configured converter paths unless explicitly testing experimental features. AC1032 has an experimental clean-room opt-in path reachable from settings/env, but it is default-off, contract-blocked for support claims, and must not be described as customer-facing native DWG support.
- **Monolith freeze**: `src/gui/drawing_compare_workbench.py` is still the main structural risk. Current measured line count is 13,467 lines on the same `splitlines()` basis used by `scripts/cad_policy_gate.py`. This sync aligns `AGENTS.md` and `MONOLITH_LINE_CEILINGS` to that exact current baseline so future growth is blocked.

## Current Decisions

- Do not claim full DWG or AC1032 native support in customer-facing text.
- Do not claim customer-grade MVP completion until the customer-grade audit artifacts exist and pass.
- Do not add new P5-G gates. Existing gate hardening and line-count ratcheting are allowed.
- Do not add code to `src/gui/drawing_compare_workbench.py`; new GUI/worker behavior must live in separate modules.
- Treat `docs/collab/WORKLOG.md` as the append-only chronology, but treat this file as the current navigation summary.

## Verification Snapshot

- `python scripts\cad_policy_gate.py` — expected gate for support wording and monolith ceiling.
- `git diff --check` — expected clean before handoff.
- `python -m pytest tests/unit/scripts/test_lint_ci_enforced.py tests/unit/scripts/test_build_pilot_packet.py -q` — targeted policy/packet regression.
- Latest GitHub `cad-format-regression.yml` main run for `a007945` passed on 2026-06-27.
- Full-suite GitHub workflow run `28323513823` is the current freshness evidence for HEAD `6b13a395a4fcf788027f316f3ea89a969f916c5b`; it failed, so rerun only after remediating or explicitly retiring the 11 failures.

## Open Notes

- Existing root `.harness/` belongs to the completed pilot-packet builder task and should be preserved.
- `.harness/status_deep_analysis_20260628/` and `.harness/dual_status_freeze_20260628/` are Codex analysis/execution harnesses from 2026-06-28.
- `.harness/full_suite_freshness_20260628/` records the current full-suite freshness closure loop and its GitHub/JUnit evidence.
- Historical visible app PIDs and manual runtime artifacts in older status entries are no longer current live-state claims.
