# DWG Native Remaining Execution Plan

Date: 2026-06-13

This document continues the DWG native target-generation planning after the
lightweight viewer primitive-source seam was completed. It is an execution plan,
not a support claim and not approval to enable broad default native DWG import.

## Input Snapshot

- project_goal: convert the remaining DWG native roadmap into ordered,
  evidence-bound workstreams that a coding agent can execute without broadening
  customer support claims.
- repo_context: DrawingCompareWorkbench under
  `D:\00.Work_AI_Tool\DrawingCompareWorkbench`; collaboration source of truth is
  `docs/collab/`; target-generation state is tracked in
  `docs/collab/native_cad_version_matrix.json`.
- tech_stack: Python, pytest, Qt/PySide viewer, CAD comparison services,
  JSON evidence packets, Markdown planning docs, PowerShell command shell.
- current_failure:
  - `AC1015` is `importable` and default clean-room scoped, but real public
    sample blockers remain.
  - `AC1009`, `AC1012`, `AC1014`, `AC1018`, `AC1021`, `AC1024`, `AC1027`, and
    `AC1032` are `contracted` with `explicit_bridge_only`.
  - `NativeScenePack` can now reach the real lightweight viewport through
    `ViewerPrimitiveSource`, so the remaining viewer gap is row evidence and
    promotion, not another render-source seam.
- available_tools:
  - `python scripts\native_cad_version_matrix.py validate`
  - `python scripts\native_cad_goal_loop.py invariants --quick`
  - `python scripts\native_cad_goal_loop.py invariants --run-fallback-tests`
  - `python scripts\native_cad_row_evidence.py --code <ACCODE> --fixture-row`
  - `python scripts\native_cad_real_sample_smoke.py`
  - `python scripts\validate_native_cad_bridge_contract.py`
  - `python scripts\validate_dwg_native_backend.py`
  - `python scripts\validate_dwg_product_release_gate.py`
  - `python scripts\audit_dwg_all_version_support.py`
  - `python scripts\cad_policy_gate.py`
  - `python -m pytest <targeted tests> -p no:xdist -q`
  - `git diff --check`
- observability_stack: `.agent/failure-history.md`,
  `.agent/validation-log.md`, `docs/collab/native_slice_ledger.md`,
  `.local/native_cad_*`, and `build/reports/`.
- eval_assets: `docs/collab/native_cad_version_matrix.json`, converted-DXF
  oracle baselines, public/local real DWG smoke reports, native row evidence,
  product bridge evidence, and release readiness audit JSON.
- deployment_policy: no release, PR, merge, deployment, default native
  enablement, or support wording change without explicit user approval.
- risk_level: high because the work touches proprietary CAD formats, licensing,
  customer claims, parser correctness, and release gates.
- automation_boundary: docs, native CAD scripts, service-layer native CAD
  modules, tests, and local evidence artifacts. GUI monolith growth remains
  blocked.
- human_approval_policy: required for commercial SDK adoption, ODA/RealDWG or
  other license-dependent backend usage, GPL-family dependency decisions,
  release wording, default enablement, PR creation, merge, deployment, paid
  calls, permission changes, and destructive git operations.

## Current Planning Baseline

| Item | State | Planning consequence |
| --- | --- | --- |
| Viewer render-source debt | Retired for first paint by `ViewerPrimitiveSource` | Do not plan another viewer-loading seam; plan row evidence through the seam |
| Cache version debt | `RENDER_CONTRACT_VERSION` drives pack/zone families | Any render contract change is one version bump plus cache tests |
| `AC1015` clean-room row | `importable`, blockers still listed | Next implementation slice should close or reclassify real-sample blockers |
| Non-AC1015 rows | `contracted`, explicit bridge only | No default enablement; choose license/bridge path before product claim work |
| External bridge options | ODA/RealDWG-style licensed SDKs require approval | Treat as HITL decision, not engineering assumption |
| LibreDWG-style path | GPL-family license and coverage tradeoffs | Do not bundle or default without legal approval and architecture isolation |

## Remaining Workstreams

| Order | Workstream | Objective | Entry condition | Exit gate | Claim posture |
| ---: | --- | --- | --- | --- | --- |
| 1 | W1 AC1015 evidence closure | Resolve or explicitly classify current real-sample blockers and produce row evidence through the viewer seam | Current matrix passes; target samples are available | `AC1015` has real import/compare/viewer evidence or visible unsupported-content blockers | Limited AC1015 wording only |
| 2 | W2 Corpus and oracle ledger | Build row-by-row sample ledger separating public, customer-approved, converted-DXF oracle, corrupted, encrypted, and large drawings | Read-only inventory access or user-provided paths | Each target row has coverage status and next missing artifact | No new native claim |
| 3 | W3 License and backend decision | Decide whether near-term target-generation coverage uses approved bridge or clean-room-only path | User/legal decision available | ADR or decision packet records approve/reject/defer | Determines L2 path only |
| 4A | W4 Explicit bridge release candidate | If W3 approves a backend, run bridge contract, product CLI evidence, native audit, and release readiness gate | Approved backend, license id, sample pack | Product release gate passes for selected scope | Scoped explicit-backend wording only |
| 4B | W5 Clean-room expansion pilot | If W3 rejects or defers a backend, start `AC1018` clean-room row after AC1015 closes | W1 done; no approved bridge path | `AC1018` reaches `importable` or blockers are classified | Row-specific preview only |
| 5 | W6 Large-model and modern-row budget | Add row-local performance evidence for `AC1027`/`AC1032` without new global P5 gates | Modern sample pack available | LOD0, import, compare, and cancel budgets pass or fail visibly | No broad wording |
| 6 | W7 Default enablement review | Consider default native enablement only after all row gates and approvals | Every target row is release-ready and approved | Matrix rows are `enabled`, policy gate passes, support wording approved | Explicitly approved L3 wording only |

The W1 downstream Goal Command Prompt is now isolated in
`docs/collab/DWG_NATIVE_W1_AC1015_GOAL_COMMAND.md`. Use that document for the
next default implementation slice unless the user selects W2-W7.

## Execution Prompt

Use this prompt when assigning the next remaining planning slice to a coding
agent:

```text
Continue the DWG native remaining execution plan for exactly one workstream.
Read AGENTS.md, docs/collab/DWG_NATIVE_REMAINING_EXECUTION_PLAN.md,
docs/collab/DWG_NATIVE_ALL_VERSION_LONG_TERM_PROTOCOL.md,
docs/collab/DWG_ALL_VERSION_SUPPORT_STRATEGY.md,
docs/collab/NATIVE_CAD_ALL_VERSION_LOOP.md, and
docs/collab/native_cad_version_matrix.json before editing.

Default target is W1 AC1015 evidence closure unless the user explicitly selects
another workstream. Do not broaden default DWG native support. Do not add new
global P5 gates. Do not grow src/gui/drawing_compare_workbench.py. Do not treat
explicit bridge evidence as default customer support.

Allowed changes: row-local evidence scripts, native CAD service modules,
targeted tests, docs/collab planning or ledger entries, and local .agent
validation/failure logs.

Blocked without approval: commercial SDK or paid tooling use, GPL-family
bundling/default dependency decisions, license-dependent backend activation,
release wording, PR creation, merge, deployment, deletion, permission changes,
external production writes, destructive git commands, or default native
enablement.

Validate with deterministic commands before reporting: matrix validate,
targeted pytest for touched modules, native goal-loop invariants when native or
fallback behavior changes, cad_policy_gate when support wording or policy
changes, protocol validation when this planning document is changed, and
git diff --check.
```

## Goal Command Prompt

```text
You are a coding agent operating under a loop-engineering protocol.

GOAL
Advance one selected remaining DWG native workstream without changing the
current customer support claim or default native enablement.

CONTEXT PACKET
- repo_root: D:\00.Work_AI_Tool\DrawingCompareWorkbench
- branch_state: inspect with git status --short before editing
- tech_stack: Python, pytest, PySide/Qt viewer, CAD comparison services,
  JSON evidence artifacts, Markdown planning docs, PowerShell
- relevant_files:
  - AGENTS.md
  - docs/collab/DWG_NATIVE_REMAINING_EXECUTION_PLAN.md
  - docs/collab/DWG_NATIVE_ALL_VERSION_LONG_TERM_PROTOCOL.md
  - docs/collab/DWG_ALL_VERSION_SUPPORT_STRATEGY.md
  - docs/collab/NATIVE_CAD_ALL_VERSION_LOOP.md
  - docs/collab/native_cad_version_matrix.json
  - src/services/comparison/native_scene_pack.py
  - src/services/comparison/viewer_primitive_source.py
  - scripts/native_cad_*.py
  - scripts/validate_dwg_native_backend.py
  - scripts/validate_dwg_product_release_gate.py
  - tests/unit/services/comparison/
- existing_commands:
  - python scripts\native_cad_version_matrix.py validate
  - python scripts\native_cad_goal_loop.py invariants --quick
  - python scripts\native_cad_goal_loop.py invariants --run-fallback-tests
  - python scripts\native_cad_row_evidence.py --code AC1015 --fixture-row
  - python scripts\native_cad_real_sample_smoke.py
  - python scripts\cad_policy_gate.py
  - python -m pytest <targeted tests> -p no:xdist -q
  - git diff --check
- current_failure:
  - AC1015 remains row-limited and has real-sample blockers.
  - Non-AC1015 rows are explicit-bridge-only and not default-enabled.
  - License-dependent backend decisions are not approved by this plan.
- unknowns_to_discover:
  - selected workstream if not W1
  - available real sample paths
  - approved bridge/license posture, if any
  - expected converted-DXF oracle baseline for the selected row

AUTONOMY BOUNDARY
Allowed:
- Read repository files.
- Edit docs/collab planning docs, native CAD scripts, service-layer native CAD
  modules, and targeted tests when the selected slice requires it.
- Write .agent/failure-history.md and .agent/validation-log.md.
- Generate local evidence under .local/ or build/reports/.
Blocked without explicit approval:
- deployment
- deletion
- permission changes
- paid API calls
- external production writes
- PR creation or merge
- commercial SDK activation or license-dependent backend usage
- GPL-family bundled/default dependency decisions
- default native support broadening
- edits to src/gui/drawing_compare_workbench.py beyond the freeze allowance
- destructive git commands

LOOP
1. Context: inspect the selected workstream, current matrix row, and available
   scripts.
2. Plan: define one expected evidence target and validation commands.
3. Implement: make the smallest coherent change for that workstream.
4. Validate: run deterministic checks before LLM review.
5. Ledger: write failures to .agent/failure-history.md and validation summaries
   to .agent/validation-log.md; append docs/collab/WORKLOG.md for material
   planning or behavior changes.
6. Improve or stop: retry only when evidence identifies a concrete next change.

HARNESS
- lint_or_schema: python scripts\native_cad_version_matrix.py validate
- typecheck: python -m py_compile <changed python files>
- unit_tests: python -m pytest <targeted tests> -p no:xdist -q
- build_or_integration: python scripts\native_cad_goal_loop.py invariants --run-fallback-tests
- policy_check: python scripts\cad_policy_gate.py when wording or backend policy changes
- protocol_check: python C:\Users\user\.codex\skills\project-loop-engineer\scripts\validate_protocol.py docs\collab\DWG_NATIVE_REMAINING_EXECUTION_PLAN.md
- artifact_check: git diff --check

SUCCESS METRICS
- selected workstream has a clear next state, owner boundary, and exit gate.
- quality: row evidence validates or blockers are classified with stable codes.
- cost: routine slice uses <= 12 commands unless broader validation is justified.
- latency: routine loop stays within wall_clock_limit_minutes; long sample-pack
  gates are scheduled explicitly.
- safety: zero forbidden support wording, zero unapproved backend activation,
  zero secrets in shareable artifacts, zero destructive git operations.

TRACE AND EVALS
- trace_store: .agent/validation-log.md plus evidence JSON under .local/ or build/reports/
- capture: commands, outcomes, selected row/workstream, evidence paths,
  failure codes, bridge diagnostics, policy decisions
- online_eval: user/legal approval for license, release wording, and default enablement
- offline_eval: converted-DXF oracle baselines, native row evidence, real sample smoke, release audits
- redaction: redact secrets, credentials, tokens, PII, payment data, and private customer paths in shareable artifacts

IMPROVEMENT FLYWHEEL
- trace -> eval -> failure cluster -> dataset -> optimizer -> registry -> canary or alias deploy -> rollback
- Keep runtime workstream fixes separate from post-run improvement.
- Promote repeated blockers into row-local fixtures or evidence tests.

OPTIMIZATION AND ROLLOUT
- compile_layer: stable contracts for bridge result, native scene pack,
  ViewerPrimitiveSource, row evidence, and failure taxonomy
- runtime_layer: planner/implementer/reviewer split only when row promotion or
  license gating is high risk
- harness_search: tune import timeout, compare timeout, max entities, LOD0
  primitive budget, sample slice size, and reviewer use
- registry: native version matrix row state and schema-versioned evidence
- rollout_gate: baseline -> candidate row evidence -> explicit internal bridge
  canary -> controlled release -> default enablement review
- rollback_condition: quality, cost, latency, safety, license, or wording regression

HARD BRAKES
- max_outer_iterations: 5
- same_failure_retry_limit: 2
- same_command_rerun_limit: 2
- no_progress_turn_limit: 2
- wall_clock_limit_minutes: 30
- stop immediately when the next action requires approval outside the autonomy boundary

FAILURE LEDGER RULES
- Use .agent/failure-history.md for failed attempts.
- Use .agent/validation-log.md for command output summaries and evidence.
- Use ISO 8601 timestamps.
- Record timestamp, attempted_action, observed_failure, evidence, and next_action_change.
- Redact secrets, credentials, tokens, PII, and payment data as [REDACTED].

PR GATES
- branch_strategy: inspect current branch; do not create or switch branches unless requested.
- commit_policy: do not stage or commit unless requested.
- pr_creation_condition: blocked unless the user explicitly asks for a PR.
- required_checks: matrix validate, targeted tests, native goal-loop invariants
  when behavior changes, cad_policy_gate when policy changes, protocol validator
  when planning docs change, git diff --check.
- merge_policy: block merge unless approval is explicit and required checks pass.
- rollback_plan: prefer git revert <commit> or discard the feature branch; do
  not use destructive reset unless explicitly requested.

FINAL RESPONSE CONTRACT
Report changed_files, behavior_change, validation_performed,
telemetry_or_eval_artifacts, known_risks, rollback_plan, and
user_decision_required.
```

## Runtime Loop

| State | Input | Action | Output | Validator | Stop Condition |
| --- | --- | --- | --- | --- | --- |
| context | selected workstream or UNKNOWN | read only relevant docs, matrix, scripts, current dirty state | context packet and unknowns | files and commands exist | approval required or target ambiguous |
| plan | context packet | define one workstream, evidence target, allowed files, validation | scoped work item | no support-claim broadening | user rejects scope |
| implement | scoped work item | edit docs/scripts/services/tests only as needed | narrow diff | structural freeze and policy rules | next step exceeds boundary |
| validate | changed files | run deterministic commands | command evidence | pass or actionable failure | same failure repeats twice |
| ledger | validation output | update `.agent/` logs and WORKLOG if material | resumable record | timestamp/redaction rules | no material change |
| improve-or-stop | ledger and result | retry one concrete variable or stop | pass/block decision | hard brakes | max iterations or approval gate |

## Improvement Flywheel

| Stage | Data | Action | Gate | Rollback |
| --- | --- | --- | --- | --- |
| trace | command summaries, evidence JSON, bridge diagnostics | collect sampled redacted traces | selected workstream is traceable | lower capture scope |
| eval | oracle baselines, matrix state, policy gate | score quality, latency, cost, safety | baseline or better | keep prior row state |
| failure-cluster | repeated parser/license/perf blockers | group by row, entity, stage, backend | actionable cluster | discard false positives |
| dataset | public or customer-approved samples | promote blocker to fixture or sample ledger | coverage increases safely | revert sample pointer |
| optimizer | timeouts, budgets, sample slices | search bounded harness parameters | Pareto improvement | reject candidate |
| registry | matrix row and evidence schema | version row state and labels | review approval | revert row state |
| rollout | explicit bridge or clean-room row | canary/internal review before wider wording | no regression and approval | disable backend selector |

## Loop Plan

| State | Input | Action | Output | Validator | Stop Condition |
| --- | --- | --- | --- | --- | --- |
| G0 planning lock | current repo and matrix | accept or refresh local baseline only when intentional | stable context | `native_cad_version_matrix.py validate` | dirty conflict |
| G1 select workstream | W1-W7 | choose exactly one workstream | row/workstream card | scope is within boundary | needs approval |
| G2 evidence target | selected card | name artifact, tests, blockers, claim posture | evidence contract | target is testable | target is untestable |
| G3 execute | evidence contract | implement or document the slice | changed files | py_compile/pytest/protocol checks | repeated failure |
| G4 review | validation output | check claim wording, license posture, fallback safety | findings or no findings | policy gate and diff check | open high risk |
| G5 ledger | review result | update WORKLOG and local validation logs | resumable record | redaction/timestamp rules | none |
| G6 stop | ledger | report done, blocked, or approval needed | final response | final contract | terminal |

## Harness Plan

| Order | Check | Command Or Tool | Exists? | Pass Criteria | Fallback |
| ---: | --- | --- | --- | --- | --- |
| 1 | matrix state | `python scripts\native_cad_version_matrix.py validate` | yes | exits 0 | inspect JSON and block promotion |
| 2 | protocol structure | `python C:\Users\user\.codex\skills\project-loop-engineer\scripts\validate_protocol.py docs\collab\DWG_NATIVE_REMAINING_EXECUTION_PLAN.md` | yes | exits 0 | manually run Golden Set |
| 3 | policy wording | `python scripts\cad_policy_gate.py` | yes | exits 0 | no support wording changes allowed |
| 4 | targeted tests | `python -m pytest <targeted tests> -p no:xdist -q` | yes | exits 0 | py_compile and document test gap |
| 5 | integration invariant | `python scripts\native_cad_goal_loop.py invariants --run-fallback-tests` | yes | exits 0 | run failing subcommands listed by runner |
| 6 | release gate | `python scripts\validate_dwg_product_release_gate.py <sample-pack> ...` | yes | exits 0 only for approved release scope | block L2/L3 wording |
| 7 | artifact check | `git diff --check` | yes | exits 0 | inspect changed hunks manually |

## Success Metrics

- metric: workstream selection
  - measurement: one W1-W7 card selected per iteration
  - pass_threshold: exactly one selected target
  - evidence: plan section or ledger entry
- metric: row lifecycle progress
  - measurement: `native_cad_version_matrix.json` row state and evidence flags
  - pass_threshold: at most one promotion per validated slice
  - evidence: matrix diff plus validator output
- metric: quality
  - measurement: import/compare/viewer row evidence and converted-DXF oracle
  - pass_threshold: no silent drops; recall >= 90 percent and precision >= 85
    percent before release candidate
  - evidence: row evidence and audit JSON
- metric: cost
  - measurement: command count and human approval touches
  - pass_threshold: <= 12 commands for routine slice unless broader gate is
    justified
  - evidence: `.agent/validation-log.md`
- metric: latency
  - measurement: import, compare, viewer generation, release gate duration
  - pass_threshold: row-local budget passes or timeout is visible
  - evidence: evidence packet timing fields
- metric: safety
  - measurement: policy gate, license gate, redaction, support wording
  - pass_threshold: zero unapproved backend calls and zero broad claim wording
  - evidence: policy gate and review checklist
- metric: drift
  - measurement: row state, backend id, schema version, cache contract version
  - pass_threshold: every drift has an evidence link
  - evidence: matrix and row packet

## Validation Matrix

| Claim | Evidence Required | Validator | Pass Criteria |
| --- | --- | --- | --- |
| W1 closes AC1015 evidence | real import/compare/viewer packet or visible blocker classification | row evidence, targeted importer/viewer tests | no silent failure; matrix remains honest |
| W2 corpus ledger is useful | row coverage table with sample class and gaps | smoke report and matrix validate | every row has next artifact |
| W3 backend decision is actionable | ADR/decision packet with approve/reject/defer | human gate | no engineering assumption substitutes approval |
| W4 explicit bridge candidate is valid | approved license, bridge contract, product CLI evidence, audits | product release gate | every scoped row passes under explicit backend |
| W5 clean-room pilot is bounded | one target row, parser stage, oracle, unsupported warnings | targeted tests and fallback invariants | no default support broadening |
| W6 modern budget is safe | large-sample timings, LOD0 bytes, cancel/timeout behavior | row-local perf evidence | no new global P5 gate |
| W7 default enablement is allowed | all target rows enabled, approvals, policy wording | policy and release gates | zero unresolved high-risk finding |

## Telemetry Plan

- trace_store: `.agent/validation-log.md`, `.agent/failure-history.md`,
  `.local/native_cad_*`, `build/reports/`.
- spans_to_capture:
  - selected workstream and row
  - command lines and exit codes
  - bridge adapter id, license id, and backend policy when applicable
  - import stage and failure code
  - compare metrics and viewer primitive budgets
  - guardrail events such as forbidden wording or approval blocks
- metrics_to_capture:
  - success rate
  - retry count
  - loop count
  - p95 import/compare/viewer latency when available
  - command count as cost proxy
  - unsafe tool call rate
  - fallback/default backend violations
- sampling_policy: capture every row promotion and release-gate attempt; sample
  routine fixture reruns after repeated stable passes.
- redaction_policy: redact secrets, credentials, tokens, PII, payment data, and
  private customer paths in shareable artifacts.
- retention_policy: commit planning and schema-level evidence references; keep
  raw `.local/` artifacts local unless approved.

## Eval Plan

- golden_set: converted-DXF oracle pairs, public DWG smoke corpus, customer
  approved row samples when available, native row fixture pairs.
- online_eval: human approval for W3 license/backend decision, W4 release
  wording, and W7 default enablement.
- offline_replay: native row evidence, sample-pack validation, all-version
  audit, release readiness audit.
- human_review: required for legal/license, commercial backend, default
  enablement, release wording, PR/merge.
- slice_coverage:
  - versions: `AC1009`, `AC1012`, `AC1014`, `AC1015`, `AC1018`, `AC1021`,
    `AC1024`, `AC1027`, `AC1032`
  - entities: line, circle, arc, polyline, text, attrib, insert, dimension,
    hatch, leader, spline, proxy/custom, xref
  - failures: encrypted, corrupted, timeout, unsupported section, unsupported
    entity, large model, license unavailable
- pass_fail_rules: fail a row if evidence is missing, fallback is counted as
  native, unsupported content is silent, or wording exceeds evidence.

## Skillification Plan

- stable_contract:
  - `native-cad-bridge-result/v1`
  - `native-scene-pack/v1`
  - `overview-lod0/v1`
  - `ViewerPrimitiveSource`
  - `native-cad-viewer-evidence/v1`
  - `native-cad-cache-identity/v1`
- signatures_or_interfaces:
  - remaining-workstream card
  - row evidence packet
  - backend decision record
  - release-gate summary
- scripts_to_create:
  - none required immediately; existing scripts cover the next slice
  - optional future script: build a consolidated row coverage ledger from smoke
    reports and matrix state
- references_to_create:
  - row-specific notes only when a row has unique parser/license constraints
- assets_to_create:
  - optional row evidence checklist template after W1 repeats manually
- registry_artifact:
  - matrix row state plus schema-versioned evidence JSON and release labels

## Optimizer Plan

- prompt_or_program_optimizer: none for parser correctness until stable datasets
  exist; review-packet prompts may be tuned after repeated human review.
- harness_search_params:
  - import timeout
  - compare timeout
  - max entities
  - max DXF tokens
  - LOD0 primitive budget
  - sample slice size
  - reviewer use
- objective:
  - maximize quality: row evidence completeness, recall, precision,
    unsupported visibility
  - minimize cost: command count and human review minutes
  - minimize latency: import, compare, viewer generation, release gate duration
  - maintain safety: license compliance, fail-closed behavior, redaction
- holdout_or_canary: customer-approved sample pack or explicit internal bridge
  canary before W4 wording.
- stop_conditions: reject any candidate that hides warnings, worsens oracle
  metrics, exceeds latency budget, or requires unapproved backend use.

## Registry Rollout Plan

- versioning: matrix row state plus evidence schema version.
- aliases_or_labels:
  - `contracted`
  - `importable`
  - `comparable`
  - `viewable`
  - `release_candidate`
  - `enabled`
- canary_policy: W4 explicit bridge canary is internal/controlled only and
  requires approved license, product CLI evidence, and rollback switch.
- promotion_gate: matrix validate, row evidence, fallback invariants, policy
  gate, release audit, and human approval when needed.
- rollback_condition: quality, cost, latency, safety, license, support wording,
  or default-backend regression.
- audit_record: append `docs/collab/WORKLOG.md` for material changes and link
  release evidence from `STATUS.md` only when release work begins.

## Failure Ledger Policy

- failure_history_path: `.agent/failure-history.md`
- validation_log_path: `.agent/validation-log.md`
- timestamp_format: ISO 8601, for example `2026-06-13T23:59:00+09:00`
- required_fields:
  - timestamp
  - attempted_action
  - observed_failure
  - evidence
  - next_action_change
- redaction_rule: replace secrets, credentials, tokens, PII, and payment data
  with `[REDACTED]`
- retention_rule: keep raw local evidence local unless user approves sharing

## Self-Improvement Policy

- Retry only when validation evidence identifies a concrete next change.
- Change one major variable per retry when possible.
- Stop after the same failure appears twice.
- Stop after two no-progress turns.
- Do not improve apparent success by hiding fallback, warning, unsupported
  entities, or license failures.
- Escalate when the next action requires approval outside the automation
  boundary.

## PR Management Protocol

- branch_strategy: inspect current branch before work; do not create, switch, or
  delete branches unless requested.
- commit_policy: do not stage or commit unless requested.
- pr_creation_condition: user explicitly asks for PR creation after checks pass.
- pr_body_template: changed files, behavior/planning change, validation, risks,
  rollback.
- required_checks:
  - `python scripts\native_cad_version_matrix.py validate`
  - targeted pytest when code/tests change
  - protocol validator when this document changes
  - `python scripts\cad_policy_gate.py` when wording or policy changes
  - native goal-loop invariants when native/fallback behavior changes
  - `git diff --check`
- reviewer_requirements: human review for W3, W4, W7, support wording, legal
  or license posture.
- merge_policy: block merge unless user approval is explicit and checks pass.
- rollback_plan: prefer `git revert <commit>` or discard the feature branch;
  do not use destructive reset unless explicitly requested.

## Hard Brakes

- max_outer_iterations: 5
- same_failure_retry_limit: 2
- same_command_rerun_limit: 2
- no_progress_turn_limit: 2
- wall_clock_limit_minutes: 30
- cost_limit: enforce only when a cost tracker exists; otherwise use command
  count and wall-clock limits.
- stop_on_unapproved_actions:
  - deployment
  - deletion
  - permission change
  - paid API call
  - external production write
  - PR creation or merge without explicit approval
  - license-dependent backend activation
  - commercial SDK or GPL-family default dependency decision
  - default native support broadening

## Human Review Summary

- changed_files:
- behavior_change:
- validation_performed:
- known_risks:
- rollback_plan:
- user_decision_required:

## Golden Set

Each answer must be "yes" before a workstream is considered planned:

1. Does the protocol include a downstream execution prompt?
2. Does the protocol include a ready-to-run Goal Command Prompt?
3. Does the protocol list only existing executable tools as mandatory commands?
4. Does the protocol provide fallback checks for missing tools?
5. Does the protocol avoid self-critique-only validation for critical work?
6. Does the protocol include task-local `.agent/` ledger paths?
7. Does the protocol include numeric hard brakes?
8. Does the protocol include quality, cost, latency, and safety metrics?
9. Does the protocol include trace/eval/optimizer/registry rollout gates?
10. Does the protocol include HITL gates for high-risk actions?
11. Does the protocol define PR and rollback gates?

## External Source Snapshot

Checked on 2026-06-13:

- ODA Drawings SDK publicly lists DWG generation support through `AC1032`
  families and has a 27.5 release dated 2026-06-12. This supports treating ODA
  as a plausible explicit licensed backend candidate only after approval.
- GNU LibreDWG documentation describes broad DWG read coverage and GPLv3+
  licensing. This makes it useful as research/evidence context, but not a
  default bundled customer dependency without legal approval and architectural
  isolation.
- Autodesk version-code references remain the canonical mapping source for
  `ACxxxx` planning, but product claims must still come from this repository's
  own row evidence and release gates.
