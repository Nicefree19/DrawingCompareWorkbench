# DWG Native All-Version Long-Term Protocol

This protocol turns the DWG native all-version roadmap into an executable
loop-engineering plan for coding agents. It is a planning and control document;
it does not approve broad DWG native support by itself.

## Input Snapshot

- project_goal: advance native DWG support from limited AC1015 clean-room scope
  toward row-by-row all-version readiness without weakening current
  PDF/DXF/fallback behavior or overstating customer support claims.
- repo_context: DrawingCompareWorkbench, Windows/Python project under
  `D:\00.Work_AI_Tool\DrawingCompareWorkbench`; collaboration source of truth is
  `docs/collab/`; native CAD row state lives in
  `docs/collab/native_cad_version_matrix.json`.
- tech_stack: Python, pytest, PyQt/Qt viewer code, CAD comparison services,
  JSON/Markdown evidence artifacts, PowerShell command environment.
- current_failure: direct/default native DWG support is not broad. AC1015 is the
  only default clean-room path and still has public-sample blockers. AC1018+
  rows are contracted or explicit-bridge-only, not default customer native.
- available_tools:
  - `python scripts\native_cad_goal_loop.py`
  - `python scripts\native_cad_version_matrix.py validate`
  - `python scripts\native_cad_row_evidence.py`
  - `python scripts\native_cad_real_sample_smoke.py`
  - `python scripts\validate_native_cad_bridge_contract.py`
  - `python scripts\validate_dwg_native_backend.py`
  - `python scripts\validate_dwg_product_release_gate.py`
  - `python scripts\audit_dwg_all_version_support.py`
  - `python scripts\cad_policy_gate.py`
  - `python -m pytest`
  - `git diff --check`
- observability_stack: local JSON/Markdown artifacts under `.local/`,
  `build/reports/`, `docs/collab/native_slice_ledger.md`, `.agent/` ledgers.
- eval_assets: `docs/collab/native_cad_version_matrix.json`, ADR-004 sample
  packs when available, converted-DXF oracle baselines, public DWG manifest,
  golden DXF accuracy baseline, release readiness metrics.
- deployment_policy: no deployment, release, PR creation, merge, or customer
  claim without explicit user approval and passing release gates.
- risk_level: high, because broad DWG support touches proprietary formats,
  license posture, customer claims, parser correctness, and release readiness.
- automation_boundary: documentation, tests, scripts, service-layer native CAD
  adapter code, evidence packets, `.agent/` task ledgers. GUI monolith growth is
  blocked.
- human_approval_policy: required for legal/license decisions, commercial SDK or
  paid tooling use, broad support wording, default enablement, PR creation,
  merge, release, deployment, permission changes, or destructive git commands.

## Execution Prompt

Use this prompt for a downstream agent assigned to one roadmap slice:

```text
Advance exactly one DWG native version-matrix row or one shared native-CAD
control surface. Keep the current customer support claim unchanged. Read
AGENTS.md, docs/collab/DWG_NATIVE_ALL_VERSION_LONG_TERM_PROTOCOL.md,
docs/collab/DWG_ALL_VERSION_SUPPORT_STRATEGY.md,
docs/collab/NATIVE_CAD_ALL_VERSION_LOOP.md,
docs/CAD_FORMAT_SUPPORT_POLICY.md, and
docs/collab/native_cad_version_matrix.json before editing.

Allowed changes: row-local evidence generation, native CAD service modules,
native CAD scripts, tests, and docs/collab ledger entries.
Blocked changes: broad default DWG support, GUI monolith growth, new global P5
gates, PDF-first implementation, ODA/GPL/AGPL default paths, PR creation,
merge, release, deployment, paid API/tool calls, destructive git commands.

Before edits, select one row or one shared gate and record the intended evidence
target. After edits, run deterministic validation: matrix validate, targeted
tests, native goal-loop invariants when relevant, cad_policy_gate when support
policy is touched, and git diff --check. Update docs/collab/WORKLOG.md only for
material behavior or planning changes. Record failures in
.agent/failure-history.md and validation summaries in .agent/validation-log.md.

Stop if the same blocker repeats twice, if approval is required, or if the next
step would broaden customer/native support claims without passing row gates.
```

## Goal Command Prompt

```text
You are a coding agent operating under a loop-engineering protocol.

GOAL
Advance one explicitly selected DWG native all-version roadmap slice toward row
readiness while preserving the current customer-safe PDF/DXF and converted-DXF
fallback behavior.

CONTEXT PACKET
- repo_root: D:\00.Work_AI_Tool\DrawingCompareWorkbench
- branch_state: UNKNOWN; inspect with git status --short before editing.
- tech_stack: Python, pytest, PyQt/Qt viewer, CAD comparison services,
  JSON/Markdown evidence artifacts, PowerShell.
- relevant_files:
  - AGENTS.md
  - docs/collab/DWG_NATIVE_ALL_VERSION_LONG_TERM_PROTOCOL.md
  - docs/collab/DWG_ALL_VERSION_SUPPORT_STRATEGY.md
  - docs/collab/NATIVE_CAD_ALL_VERSION_LOOP.md
  - docs/collab/NATIVE_CAD_GOAL_LOOP.md
  - docs/collab/native_cad_version_matrix.json
  - docs/CAD_FORMAT_SUPPORT_POLICY.md
  - docs/ENTITY_SUPPORT_MATRIX.md
  - docs/DWG_CLEANROOM_FORMAT_CONTRACT.md
  - src/services/comparison/
  - scripts/native_cad_*.py
  - scripts/validate_dwg_native_backend.py
  - scripts/validate_dwg_product_release_gate.py
  - tests/unit/
- existing_commands:
  - python scripts\native_cad_version_matrix.py validate
  - python scripts\native_cad_goal_loop.py invariants --quick
  - python scripts\native_cad_goal_loop.py invariants --run-fallback-tests
  - python scripts\native_cad_row_evidence.py --code AC1015 --fixture-row
  - python scripts\native_cad_real_sample_smoke.py
  - python scripts\cad_policy_gate.py
  - python -m pytest <targeted test files> -q
  - git diff --check
- current_failure:
  - AC1015 clean-room native path is not yet a broad claim and has public sample
    blockers.
  - AC1018/AC1021/AC1024/AC1027/AC1032 are not default native-supported.
  - explicit licensed bridge evidence is not the same as default customer
    native enablement.
- unknowns_to_discover:
  - selected target row
  - available real sample pair and converted-DXF oracle
  - bridge command and license posture, if any
  - current dirty worktree interaction with the selected row

AUTONOMY BOUNDARY
Allowed:
- Read repository files.
- Edit docs/collab, scripts/native_cad_*.py, service-layer native CAD modules,
  and targeted tests when the selected slice requires it.
- Write task-local .agent/failure-history.md and .agent/validation-log.md.
- Generate local evidence under .local/ or build/reports/ when needed.
Blocked without explicit user approval:
- deployment
- deletion
- permission changes
- paid API calls
- external production writes
- PR creation or merge
- broad default DWG native support claims
- adding AC1018+ to default supported codes
- ODA/GPL/AGPL default/customer paths
- edits to src/gui/drawing_compare_workbench.py beyond the freeze allowance
- destructive git commands

LOOP
1. Context: inspect only required files, matrix row state, and existing scripts.
2. Plan: select one row or shared gate, define expected evidence and validation.
3. Implement: make the smallest coherent change for that row or gate.
4. Validate: run deterministic checks before LLM review.
5. Ledger: write failures to .agent/failure-history.md and validation evidence
   to .agent/validation-log.md; append docs/collab/WORKLOG.md for material
   changes.
6. Improve or stop: retry only when evidence identifies a concrete next change.

HARNESS
- lint_or_schema: python scripts\native_cad_version_matrix.py validate
- typecheck: python -m py_compile <changed python files>
- unit_tests: python -m pytest <targeted native CAD/script tests> -q
- build_or_integration: python scripts\native_cad_goal_loop.py invariants --run-fallback-tests
- artifact_check: git diff --check
- policy_check_when_support_claim_changes: python scripts\cad_policy_gate.py
- release_check_when_L2_or_L3_claim_is requested: python scripts\validate_dwg_product_release_gate.py <sample_pack> with approved bridge and evidence args

SUCCESS METRICS
- selected row advances by at most one lifecycle state per iteration.
- matrix validation passes.
- targeted tests pass.
- fallback tests pass when importer/backend behavior changes.
- no default/customer support wording broadens without row evidence and approval.
- quality: compare recall >= 90 percent and precision >= 85 percent against
  converted-DXF oracle before release_candidate.
- cost: command count per iteration <= 12 unless user approves broader run.
- latency: routine row loop completes within wall_clock_limit_minutes; long
  sample-pack gates are explicitly scheduled.
- safety: zero unapproved ODA/GPL/AGPL/default backend use; zero secret leaks;
  zero destructive git commands.

TRACE AND EVALS
- trace_store: .agent/validation-log.md plus row evidence JSON under .local/ or
  build/reports/.
- capture: selected row, command lines, command outcomes, evidence paths,
  failure codes, bridge diagnostics, cache identity fields, policy decisions.
- online_eval: human approval for license/support wording and any L2/L3 claim.
- offline_eval: converted-DXF oracle baselines, golden DXF fixtures, real sample
  smoke reports, all-version audit outputs.
- redaction: redact secrets, credentials, tokens, PII, payment data, private
  customer paths in shareable artifacts.

IMPROVEMENT FLYWHEEL
- trace -> eval -> failure cluster -> dataset -> optimizer -> registry -> canary
  or alias deploy -> rollback.
- Keep runtime row work separate from post-run improvement.
- Promote repeated failure clusters into sample fixtures or row evidence tests.

OPTIMIZATION AND ROLLOUT
- compile_layer: optional stable contracts for bridge result, scene pack, row
  evidence, and failure taxonomy.
- runtime_layer: optional planner/worker/reviewer split for high-risk row
  promotion; human approval remains required for support claims.
- harness_search: tune timeout, max entities, LOD0 primitive budget, sample
  slice size, and reviewer use; do not tune by hiding failures.
- registry: version row states in docs/collab/native_cad_version_matrix.json and
  evidence artifacts by schema version.
- rollout_gate: baseline -> candidate row evidence -> canary/internal bridge ->
  controlled release -> default enablement review.
- rollback_condition: quality, cost, latency, safety, license, or claim-wording
  regression; revert the row state or disable the backend selector.

HARD BRAKES
- max_outer_iterations: 5
- same_failure_retry_limit: 2
- same_command_rerun_limit: 2
- no_progress_turn_limit: 2
- wall_clock_limit_minutes: 30
- stop immediately when the next action requires approval outside the autonomy
  boundary.

FAILURE LEDGER RULES
- Use .agent/failure-history.md for failed attempts.
- Use .agent/validation-log.md for command output summaries and evidence.
- Use ISO 8601 timestamps.
- Record timestamp, attempted_action, observed_failure, evidence, and
  next_action_change.
- Redact secrets, credentials, tokens, PII, and payment data as [REDACTED].

PR GATES
- branch_strategy: inspect current branch; do not create or switch branches
  unless requested.
- commit_policy: do not stage or commit unless requested.
- pr_creation_condition: blocked unless the user explicitly asks for a PR.
- required_checks: matrix validate, targeted tests, goal-loop invariants,
  cad_policy_gate when policy changes, git diff --check.
- merge_policy: block merge unless approval is explicit and required checks pass.
- rollback_plan: prefer git revert <commit> or discard feature branch; do not use
  destructive reset unless explicitly requested.

FINAL RESPONSE CONTRACT
When finished, report changed_files, behavior_change, validation_performed,
telemetry_or_eval_artifacts, known_risks, rollback_plan, and
user_decision_required.
```

## Runtime Loop

| State | Input | Action | Output | Validator | Stop Condition |
| --- | --- | --- | --- | --- | --- |
| context | user objective, repo docs, selected row or UNKNOWN | Read only the relevant policy, matrix, scripts, tests, and current status | scoped context packet and unknowns | paths and commands exist locally | missing approval or unavailable required samples |
| plan | context packet | Select one row or shared gate; define evidence target and validation | row-local plan | plan does not broaden default support | user rejects direction or target is ambiguous and high risk |
| implement | accepted plan | Edit only needed docs/scripts/service/tests | small diff | structural freeze and policy constraints | next action would require approval |
| validate | changed files | Run deterministic commands before review | validation evidence | commands pass or fail with actionable reason | same command fails twice with same root cause |
| ledger | validation output | Record failures and validation summaries | `.agent/` ledgers and optional WORKLOG entry | redaction and timestamp rules | no material change requiring ledger |
| improve-or-stop | ledger and validation result | Retry one concrete variable or stop | passed row slice or blocked checkpoint | retry limits and success metrics | max iterations, no progress, or approval gate |

## Improvement Flywheel

| Stage | Data | Action | Gate | Rollback |
| --- | --- | --- | --- | --- |
| trace | command logs, bridge diagnostics, row evidence JSON | collect sampled, redacted traces per row | trace covers import, compare, viewer, policy | lower sampling or disable optional trace |
| eval | oracle baselines, smoke reports, tests | score row quality, latency, safety, support wording | baseline or better | keep current row state |
| failure-cluster | repeated blockers | group by parser stage, entity type, version, license, timeout | actionable cluster with owner | discard false positives |
| dataset | public/customer-approved samples | add fixture or replay case for the cluster | slice coverage increases without leaking data | revert dataset addition |
| optimizer | row parameters and prompts | tune timeout, LOD0 budget, sample slice, bridge args | Pareto improvement in quality/cost/latency/safety | reject candidate |
| registry | matrix row and evidence schema | version artifact and state transition | review approval | revert row state |
| rollout | explicit bridge or clean-room row | internal canary or controlled release decision | no regression and approved wording | disable backend or rollback alias/branch |

## Loop Plan

| State | Input | Action | Output | Validator | Stop Condition |
| --- | --- | --- | --- | --- | --- |
| G0 context lock | dirty worktree and selected row | run goal-loop init only if current state is intentionally accepted | loop baseline | `native_cad_goal_loop.py invariants --quick` | dirty state conflicts with row work |
| G1 row contract | target code | define sample, expected entities, exclusions, failure codes | row contract | bridge contract rejects bad schema and missing SDK | no approved path or no sample |
| G2 bridge/import | row contract | generate fixture or real row evidence | evidence JSON | schema, source hash, adapter metadata | import fails without actionable stage |
| G3 compare | canonical drawing | run existing compare engine against expected deltas | compare evidence | deterministic output and oracle thresholds | false negatives exceed threshold |
| G4 viewer | scene pack | generate bounded LOD0 viewer evidence | viewer packet | primitive and byte budgets pass | full model leaks into GUI path |
| G5 policy review | complete row evidence | run policy and wording checks | promotion decision | zero CRITICAL/HIGH findings | approval required |
| G6 enablement | approved row | explicit backend or row enablement only | release candidate or enabled row | release/native audits pass | claim wording would overstate support |

## Harness Plan

| Order | Check | Command Or Tool | Exists? | Pass Criteria | Fallback |
| --- | --- | --- | --- | --- | --- |
| 1 | matrix schema/state | `python scripts\native_cad_version_matrix.py validate` | yes | exits 0 | inspect JSON row manually and do not promote |
| 2 | goal-loop quick invariant | `python scripts\native_cad_goal_loop.py invariants --quick` | yes | exits 0 | run matrix validate plus `git diff --check` |
| 3 | row fixture evidence | `python scripts\native_cad_row_evidence.py --code <ACCODE> --fixture-row` | yes | writes passing row evidence | use existing fixture tests only |
| 4 | real sample smoke | `python scripts\native_cad_real_sample_smoke.py` | yes | manifest integrity and coverage pass | mark real coverage UNKNOWN |
| 5 | targeted tests | `python -m pytest <targeted tests> -q` | yes | exits 0 | py_compile changed files and document missing test |
| 6 | fallback invariant | `python scripts\native_cad_goal_loop.py invariants --run-fallback-tests` | yes | exits 0 | run targeted fallback tests listed in failure output |
| 7 | policy gate | `python scripts\cad_policy_gate.py` | yes | exits 0 | no support claim changes allowed |
| 8 | all-version native audit | `python scripts\validate_dwg_native_backend.py <sample_pack> ...` | yes | exits 0 with approved backend | block L2 claim |
| 9 | product release gate | `python scripts\validate_dwg_product_release_gate.py <sample_pack> ...` | yes | exits 0 and summary passed | block release claim |
| 10 | artifact whitespace | `git diff --check` | yes | exits 0 | manually inspect changed hunks |

## Success Metrics

- metric: row lifecycle progress
  - measurement: matrix row state
  - pass_threshold: at most one state promotion per validated iteration
  - evidence: matrix diff plus validation log
- metric: native import quality
  - measurement: canonical schema pass, unsupported counts, parser failure stages
  - pass_threshold: no silent drops; unsupported content is counted
  - evidence: row evidence JSON
- metric: compare quality
  - measurement: recall and precision against converted-DXF oracle
  - pass_threshold: recall >= 90 percent, precision >= 85 percent before
    release_candidate
  - evidence: compare summary and audit output
- metric: viewer latency and size
  - measurement: LOD0 primitive count, bytes, generation time
  - pass_threshold: row budget passes; no GUI hot-path full model load
  - evidence: native-cad-viewer-evidence/v1 packet
- metric: cost
  - measurement: command count, agent loop count, human review minutes
  - pass_threshold: command count <= 12 per routine row iteration
  - evidence: `.agent/validation-log.md`
- metric: safety
  - measurement: unapproved backend calls, support wording, path leaks
  - pass_threshold: zero unapproved ODA/GPL/AGPL/default calls; zero broad claims
  - evidence: policy gate and review notes
- metric: drift
  - measurement: row state, schema version, bridge adapter identity changes
  - pass_threshold: every drift item has an evidence link
  - evidence: matrix and row packet

## Validation Matrix

| Claim | Evidence Required | Validator | Pass Criteria |
| --- | --- | --- | --- |
| L0 fallback remains safe | converted-DXF provenance, fallback tests | goal-loop fallback tests, release readiness audit when claiming | no regression and ODA/customer defaults remain zero |
| L1 AC1015 limited native preview | real AC1015 import, compare, viewer, warnings | row evidence, targeted tests, policy gate | simple 2D scope works; blockers visible |
| L2 explicit bridge all-version candidate | approved license, bridge contract, product CLI evidence, all-version audit | `validate_dwg_product_release_gate.py` | every target row passes through approved explicit bridge |
| L3 default native all-version support | every row `enabled`, legal/product approval, release gate | matrix validate, release audit, human gate | no unresolved CRITICAL/HIGH finding |
| Unknown future DWG fails closed | unsupported code fixture or test | targeted detector/preflight test | stable diagnostic and no fallback masquerade |
| Unsupported entity is visible | unsupported counts and warnings | row evidence and review artifact | no silent geometry parity claim |

## Telemetry Plan

- trace_store: `.agent/validation-log.md`, row evidence JSON, native bridge
  diagnostics, `build/reports/` release artifacts.
- spans_to_capture:
  - model calls when an agent is used
  - tool calls and command summaries
  - handoffs between planner, implementer, reviewer, and user
  - guardrail events such as license blocks or forbidden wording
  - validation commands and evidence paths
- metrics_to_capture:
  - row success rate
  - retry count
  - loop count
  - p95 import/compare/viewer generation latency when measurable
  - command count as cost proxy
  - unsafe tool call rate
  - fallback/default backend violations
- sampling_policy: capture every row promotion attempt; sample routine fixture
  reruns after stable pass history.
- redaction_policy: replace secrets, credentials, tokens, PII, payment data, and
  sensitive customer paths in shareable artifacts with `[REDACTED]`.
- retention_policy: keep committed docs and schema-level evidence references;
  keep raw `.local/` artifacts local unless user approves sharing.

## Eval Plan

- golden_set: converted-DXF oracle pairs, golden DXF accuracy fixtures, public
  DWG smoke corpus, row fixture pairs.
- online_eval: human review for license posture, customer wording, row promotion,
  and L2/L3 release claims.
- offline_replay: rerun row evidence and all-version audits against pinned sample
  packs.
- human_review: required for legal/license, commercial SDK, default enablement,
  release wording, and PR/merge.
- slice_coverage:
  - version: AC1009, AC1012, AC1014, AC1015, AC1018, AC1021, AC1024, AC1027,
    AC1032
  - entities: line, circle, arc, polyline, text, attrib, insert, dimension,
    hatch, leader, spline, proxy/custom, xref
  - failure modes: encrypted, corrupted, timeout, unsupported section,
    unsupported entity, large model
- pass_fail_rules: a row cannot advance if evidence is missing, warnings are
  silent, fallback is counted as native, or support wording overstates scope.

## Skillification Plan

- stable_contract:
  - native-cad-bridge-result/v1
  - native-scene-pack/v1
  - native-cad-row-evidence/v1
  - native-cad-viewer-evidence/v1
  - native-cad-cache-identity/v1
- signatures_or_interfaces:
  - bridge import contract
  - row evidence packet contract
  - failure taxonomy contract
  - promotion decision contract
- scripts_to_create:
  - only after repeated manual work is observed; current required scripts already
    exist for matrix validation, row evidence, smoke, and release gates.
- references_to_create:
  - row-specific notes only when a row has unique parser or license constraints.
- assets_to_create:
  - optional row evidence checklist template if repeated reviews need it.
- registry_artifact:
  - version matrix row state plus schema-versioned evidence JSON.

## Optimizer Plan

- prompt_or_program_optimizer: none for core parser correctness until stable
  datasets exist; optional prompt templates may be tuned for review packet
  generation only.
- harness_search_params:
  - import timeout
  - compare timeout
  - max entities
  - max DXF tokens
  - LOD0 primitive budget
  - sample slice size
  - reviewer use
- objective:
  - maximize quality: recall, precision, unsupported visibility, deterministic
    output
  - minimize cost: command count, human review minutes, bridge invocations
  - minimize latency: import, compare, viewer generation, release gate duration
  - maintain safety: license compliance, fail-closed behavior, redaction
- holdout_or_canary: customer-approved sample pack or internal bridge canary
  before L2 wording.
- stop_conditions: reject a candidate if it hides warnings, worsens recall,
  exceeds latency budget, or requires unapproved backend use.

## Registry Rollout Plan

- versioning: evidence schema version plus matrix row state.
- aliases_or_labels:
  - `fallback-ready`
  - `cleanroom-preview`
  - `explicit-bridge-candidate`
  - `release-candidate`
  - `enabled`
- canary_policy: L2 bridge canary is internal/controlled only and requires
  approved license, product CLI evidence, and rollback switch.
- promotion_gate: matrix validate, row evidence, fallback invariants, policy
  gate, release audit, human approval where needed.
- rollback_condition: any quality, cost, latency, safety, license, or support
  wording regression.
- audit_record: append `docs/collab/WORKLOG.md` for material changes and link
  row evidence from a sync packet or `STATUS.md` only when release work begins.

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
  with `[REDACTED]`.
- retention_rule: keep task-local ledgers local; do not move project failure
  history into global skills or global memory.

## Self-Improvement Policy

- Retry only when validation evidence identifies a concrete change.
- Change one major variable per retry when possible.
- Stop after the same failure appears twice.
- Stop after two no-progress turns.
- Do not improve accuracy by adding unbounded agent turns, unbounded samples, or
  hidden fallback paths.
- Escalate when the next action requires legal/license, release wording,
  external production writes, paid tools, PR creation, merge, deployment, or
  default native enablement.

## PR Management Protocol

- branch_strategy: inspect current branch before work; do not create, switch, or
  delete branches unless requested.
- commit_policy: do not stage or commit unless requested.
- pr_creation_condition: user explicitly asks for PR creation after checks pass.
- pr_body_template: use the repository's existing PR convention if available;
  otherwise include changed files, behavior change, validation, risks, and
  rollback.
- required_checks:
  - `python scripts\native_cad_version_matrix.py validate`
  - targeted pytest
  - native goal-loop invariants when native/fallback behavior changes
  - `python scripts\cad_policy_gate.py` when policy/support claim changes
  - `git diff --check`
- reviewer_requirements: human review for L2/L3 claims, legal/license posture,
  and release wording.
- merge_policy: block merge unless the user explicitly approves and required
  checks pass.
- rollback_plan: prefer `git revert <commit>` or closing/discarding the feature
  branch; avoid destructive reset commands unless explicitly requested.

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
  - default native support broadening
  - commercial SDK or license-dependent backend activation without approval

## Human Review Summary

- changed_files:
- behavior_change:
- validation_performed:
- known_risks:
- rollback_plan:
- user_decision_required:

## Golden Set

Each question must be answerable with yes or no before a row promotion or PR:

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
