# DWG Native W1 AC1015 Goal Command

Date: 2026-06-14

This document turns the next default DWG-native workstream into a downstream
Goal Command Prompt. It plans W1 only: AC1015 evidence closure. It does not
approve broader DWG native support, explicit licensed backend usage, release
wording, or default enablement for other DWG generations.

## Input Snapshot

- project_goal: advance W1 AC1015 evidence closure by converting the current
  blockers into either real import/compare/viewer evidence or visible,
  stable unsupported-content classifications.
- repo_context: DrawingCompareWorkbench under
  `D:\00.Work_AI_Tool\DrawingCompareWorkbench`; collaboration source of truth is
  `docs/collab/`; target-generation state is tracked in
  `docs/collab/native_cad_version_matrix.json`.
- tech_stack: Python, pytest, PySide/Qt lightweight viewer, clean-room AC1015
  DWG preview reader, CAD comparison services, JSON evidence packets,
  Markdown planning docs, PowerShell command shell.
- current_failure:
  - `AC1015` matrix state is `importable`, `backend_policy` is
    `default_cleanroom`, and `default_enabled` is true only for the approved
    row-limited AC1015 clean-room preview.
  - `AC1015` still has blockers:
    `public_ac1015_object_map_offset_blocked`,
    `public_ac1015_standard_object_handle_1_payload_decode_blocked`, and
    `viewer_lod0_real_evidence_pending`.
  - A 2026-06-14 bounded real-sample smoke found AC1015 failures at:
    `.local/native_cad_real_samples/acadsharp/sample_AC1015.dwg` with
    `failure_stage=object map`, and
    `.local/native_cad_real_samples/libredwg_selected/example_2000.dwg` with
    `failure_stage=object decode`.
  - Fixture row evidence for `AC1015` passes and is not enough to close W1.
- available_tools:
  - `python scripts\native_cad_version_matrix.py validate`
  - `python scripts\native_cad_real_sample_smoke.py --import-samples-per-code 2 --json-report .local\native_cad_w1_goal_smoke.json --md-report .local\native_cad_w1_goal_smoke.md`
  - `python scripts\native_cad_row_evidence.py --code AC1015 --fixture-row --json-out .local\native_cad_w1_fixture_row_evidence.json`
  - `python scripts\native_cad_goal_loop.py invariants --quick`
  - `python scripts\native_cad_goal_loop.py invariants --run-fallback-tests`
  - `python scripts\cad_policy_gate.py`
  - `python -m pytest <targeted tests> -p no:xdist -q`
  - `python -m py_compile <changed python files>`
  - `git diff --check`
  - `python C:\Users\user\.codex\skills\project-loop-engineer\scripts\validate_protocol.py docs\collab\DWG_NATIVE_W1_AC1015_GOAL_COMMAND.md`
- observability_stack: `.agent/failure-history.md`,
  `.agent/validation-log.md`, `.local/native_cad_w1_*`,
  `.local/native_cad_real_samples/`, `.local/native_cad_fixture_rows/`,
  `build/reports/`, and `docs/collab/WORKLOG.md`.
- eval_assets: `docs/collab/native_cad_version_matrix.json`, current
  AC1015 fixture evidence, AC1015 real-sample smoke reports, native viewer
  evidence packets, converted-DXF oracle baselines when available, and targeted
  DWG reader/importer tests.
- deployment_policy: no release, PR, merge, deployment, support wording change,
  or default native enablement change without explicit user approval.
- risk_level: high because the slice touches proprietary CAD parsing,
  user-visible support claims, fail-closed behavior, and release gates.
- automation_boundary: W1 may touch docs/collab planning docs, native CAD
  scripts, clean-room AC1015 reader/importer service modules, targeted tests,
  and local `.agent` or `.local` evidence artifacts. GUI monolith growth remains
  blocked.
- human_approval_policy: required for commercial SDK usage, ODA/RealDWG or any
  license-dependent backend activation, GPL-family bundled/default dependency
  decisions, release wording, default enablement changes, PR creation, merge,
  deployment, paid calls, permission changes, deletion, and destructive git
  operations.

## Execution Prompt

Use this prompt when assigning W1 to a coding agent:

```text
Execute W1 AC1015 evidence closure only.

Read AGENTS.md, docs/collab/DWG_NATIVE_W1_AC1015_GOAL_COMMAND.md,
docs/collab/DWG_NATIVE_REMAINING_EXECUTION_PLAN.md,
docs/collab/DWG_NATIVE_ALL_VERSION_LONG_TERM_PROTOCOL.md,
docs/collab/DWG_ALL_VERSION_SUPPORT_STRATEGY.md,
docs/collab/NATIVE_CAD_ALL_VERSION_LOOP.md, and
docs/collab/native_cad_version_matrix.json before editing.

Goal: turn the current AC1015 real-sample blockers into either validated real
import/compare/viewer evidence or visible unsupported-content classifications.
Do not work on AC1018+ expansion. Do not use an explicit licensed bridge. Do
not broaden default DWG native support or release wording.

Start by reproducing the current AC1015 state:
- matrix validate
- bounded real-sample smoke with import-samples-per-code 2
- AC1015 fixture row evidence

Pick exactly one W1 target per iteration:
1. object-map offset blocker for sample_AC1015.dwg,
2. object-decode blocker for example_2000.dwg, or
3. real viewer LOD0 evidence for an already importable AC1015 public sample.

Allowed changes: AC1015 clean-room reader/importer modules, row-local evidence
scripts, targeted tests, docs/collab ledger entries, and .agent/.local evidence.
Blocked without approval: commercial SDKs, ODA/RealDWG, GPL-family bundling,
release wording, default enablement changes, PR/merge/deploy, deletion,
permission changes, destructive git, and GUI monolith growth.

Validate with deterministic commands before reporting: targeted pytest,
py_compile for changed Python files, matrix validate, bounded real-sample
smoke, row evidence for AC1015, cad_policy_gate if wording/policy changes,
native goal-loop invariants when behavior changes, protocol validation when
this document changes, and git diff --check.
```

## Goal Command Prompt

```text
You are a coding agent operating under a loop-engineering protocol.

GOAL
Close the next W1 AC1015 evidence gap by converting one current real-sample
blocker into validated real import/compare/viewer evidence or a stable visible
unsupported-content classification, without broadening DWG native support.

CONTEXT PACKET
- repo_root: D:\00.Work_AI_Tool\DrawingCompareWorkbench
- branch_state: inspect with git status --short before editing; do not stage,
  commit, switch branches, or create PRs unless explicitly requested.
- tech_stack: Python, pytest, PySide/Qt lightweight viewer, clean-room AC1015
  DWG preview reader, CAD comparison services, JSON evidence artifacts,
  Markdown planning docs, PowerShell.
- relevant_files:
  - AGENTS.md
  - docs/collab/DWG_NATIVE_W1_AC1015_GOAL_COMMAND.md
  - docs/collab/DWG_NATIVE_REMAINING_EXECUTION_PLAN.md
  - docs/collab/DWG_NATIVE_ALL_VERSION_LONG_TERM_PROTOCOL.md
  - docs/collab/DWG_ALL_VERSION_SUPPORT_STRATEGY.md
  - docs/collab/NATIVE_CAD_ALL_VERSION_LOOP.md
  - docs/collab/native_cad_version_matrix.json
  - src/services/comparison/dwg_binary_reader.py
  - src/services/comparison/dwg_section_reader.py
  - src/services/comparison/dwg_object_decoder.py
  - src/services/comparison/dwg_native_reader.py
  - src/services/comparison/dwg_importer.py
  - src/services/comparison/native_scene_pack.py
  - src/services/comparison/viewer_primitive_source.py
  - scripts/native_cad_real_sample_smoke.py
  - scripts/native_cad_row_evidence.py
  - scripts/native_cad_viewer_evidence_fixture.py
  - scripts/native_cad_version_matrix.py
  - tests/unit/services/comparison/test_dwg_native_reader.py
  - tests/unit/services/comparison/test_dwg_importer.py
  - tests/unit/scripts/test_native_cad_real_sample_smoke.py
  - tests/unit/scripts/test_native_cad_row_evidence.py
- existing_commands:
  - python scripts\native_cad_version_matrix.py validate
  - python scripts\native_cad_real_sample_smoke.py --import-samples-per-code 2 --json-report .local\native_cad_w1_goal_smoke.json --md-report .local\native_cad_w1_goal_smoke.md
  - python scripts\native_cad_row_evidence.py --code AC1015 --fixture-row --json-out .local\native_cad_w1_fixture_row_evidence.json
  - python scripts\native_cad_goal_loop.py invariants --quick
  - python scripts\native_cad_goal_loop.py invariants --run-fallback-tests
  - python scripts\cad_policy_gate.py
  - python -m py_compile <changed python files>
  - python -m pytest <targeted tests> -p no:xdist -q
  - git diff --check
- current_failure:
  - AC1015 fixture row evidence passes, but fixture evidence alone does not
    close W1.
  - Bounded real-sample smoke currently shows
    .local/native_cad_real_samples/acadsharp/sample_AC1015.dwg failing at
    failure_stage=object map.
  - Bounded real-sample smoke currently shows
    .local/native_cad_real_samples/libredwg_selected/example_2000.dwg failing
    at failure_stage=object decode.
  - native_cad_version_matrix.json still lists
    public_ac1015_object_map_offset_blocked,
    public_ac1015_standard_object_handle_1_payload_decode_blocked, and
    viewer_lod0_real_evidence_pending.
- unknowns_to_discover:
  - whether the selected AC1015 real sample can be decoded safely in the
    clean-room reader without overgeneralizing the parser.
  - whether the selected failure should become supported geometry or a visible
    unsupported-content diagnostic.
  - whether a real importable public sample can produce viewer LOD0 evidence
    through ViewerPrimitiveSource.

AUTONOMY BOUNDARY
Allowed:
- Read repository files and local evidence artifacts.
- Edit AC1015 clean-room reader/importer modules under src/services/comparison.
- Edit row-local scripts under scripts/native_cad_*.py when W1 evidence needs it.
- Edit targeted unit tests under tests/unit/services/comparison and
  tests/unit/scripts.
- Edit docs/collab planning or ledger entries and append docs/collab/WORKLOG.md
  for material planning or behavior changes.
- Write .agent/failure-history.md, .agent/validation-log.md, and .local W1
  evidence artifacts.
Blocked without explicit approval:
- deployment
- deletion
- permission changes
- paid API calls
- external production writes
- PR creation or merge
- staging or committing
- destructive git commands
- commercial SDK activation or license-dependent backend usage
- ODA, RealDWG, AutoCAD, TrueView, LibreDWG, or GPL-family bundled/default
  dependency decisions
- default DWG native support broadening
- release wording changes
- AC1018+ clean-room expansion
- edits to src/gui/drawing_compare_workbench.py beyond the structural-freeze
  allowance

LOOP
1. Context: inspect the selected W1 target, current matrix row, smoke report,
   reader code, and relevant tests. Record unknowns; do not guess.
2. Plan: choose exactly one target from object-map blocker, object-decode
   blocker, or real viewer LOD0 evidence. Name expected files and validators.
3. Implement: make the smallest coherent change. Prefer fail-closed support for
   one observed payload shape, or stable visible unsupported diagnostics when
   full decode would be unsafe.
4. Validate: run deterministic checks before self-review.
5. Ledger: write failures to .agent/failure-history.md and validation summaries
   to .agent/validation-log.md. Append docs/collab/WORKLOG.md for material
   behavior/planning changes.
6. Improve or stop: retry only when evidence identifies one concrete next
   change. Stop when the next action requires approval or the same failure
   repeats twice.

HARNESS
- lint_or_schema: python scripts\native_cad_version_matrix.py validate
- reproduce_real_state: python scripts\native_cad_real_sample_smoke.py --import-samples-per-code 2 --json-report .local\native_cad_w1_goal_smoke.json --md-report .local\native_cad_w1_goal_smoke.md
- fixture_row: python scripts\native_cad_row_evidence.py --code AC1015 --fixture-row --json-out .local\native_cad_w1_fixture_row_evidence.json
- typecheck: python -m py_compile <changed python files>
- unit_tests: python -m pytest <targeted tests> -p no:xdist -q
- integration_or_fallback: python scripts\native_cad_goal_loop.py invariants --run-fallback-tests when native/fallback behavior changes
- quick_invariants: python scripts\native_cad_goal_loop.py invariants --quick when only docs/evidence routing changes
- policy_check: python scripts\cad_policy_gate.py when wording, policy, backend, or support claims change
- protocol_check: python C:\Users\user\.codex\skills\project-loop-engineer\scripts\validate_protocol.py docs\collab\DWG_NATIVE_W1_AC1015_GOAL_COMMAND.md when this document changes
- artifact_check: git diff --check

SUCCESS METRICS
- workstream_scope: exactly one W1 target is selected per iteration.
- quality: the selected real AC1015 blocker either imports into canonical
  geometry with no silent drops, or fails with a stable visible unsupported
  diagnostic carrying stage and evidence details.
- viewer: any W1 viewer claim must produce native-cad-viewer-evidence/v1 or
  equivalent LOD0 evidence through the existing ViewerPrimitiveSource path.
- matrix_honesty: native_cad_version_matrix.json must not promote AC1015 unless
  real import/compare/viewer evidence and blockers support the promotion.
- cost: routine W1 loop uses <= 12 validation commands unless a broader
  regression run is justified in the ledger.
- latency: bounded smoke and row evidence complete within their script
  timeouts; long sample-pack work is scheduled, not hidden.
- safety: zero forbidden wording, zero unapproved backend activation, zero
  secrets in shareable artifacts, zero destructive git operations.

TRACE AND EVALS
- trace_store: .agent/validation-log.md, .agent/failure-history.md,
  .local/native_cad_w1_*.json, .local/native_cad_w1_*.md, and build/reports
  when generated.
- capture: selected target, sample path, command lines, exit codes, failure
  stage, error code, object handle/offset/payload prefix when available,
  canonical entity count, viewer primitive count, policy gate result, and matrix
  row state.
- online_eval: human approval is required for release wording, backend/license
  decisions, default enablement, PR/merge, or any expansion beyond W1.
- offline_eval: AC1015 fixture row evidence, bounded real-sample smoke,
  targeted reader/importer tests, viewer evidence artifacts, converted-DXF
  oracle checks when available.
- redaction: redact secrets, credentials, tokens, PII, payment data, and private
  customer paths as [REDACTED].

IMPROVEMENT FLYWHEEL
- trace -> eval -> failure cluster -> dataset -> optimizer -> registry ->
  controlled rollout -> rollback
- Keep runtime parser fixes separate from post-run improvement.
- Promote repeated AC1015 blockers into row-local tests or evidence fixtures.
- Do not optimize by hiding unsupported entities, warnings, license failures, or
  fallback behavior.

OPTIMIZATION AND ROLLOUT
- compile_layer: stable contracts for AC1015 failure taxonomy, row evidence,
  native scene pack, ViewerPrimitiveSource, and cache identity.
- runtime_layer: single-agent loop is sufficient for one W1 target; use a
  reviewer/human gate only for row promotion, release wording, or legal/backend
  decisions.
- harness_search: bounded candidates include import timeout, object-count probe
  cap, LOD0 primitive budget, sample slice size, and targeted test set.
- registry: native version matrix row state plus schema-versioned evidence JSON.
- rollout_gate: fixture baseline -> real sample blocker reproduction -> targeted
  parser/diagnostic fix -> real smoke/evidence -> matrix review -> limited
  AC1015 wording review only if requested.
- rollback_condition: quality, cost, latency, safety, matrix honesty, support
  wording, or fallback/default-backend regression.

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
- required_checks: matrix validate, targeted tests, py_compile for changed
  Python files, bounded real-sample smoke, AC1015 row evidence, native
  goal-loop invariants when behavior changes, cad_policy_gate when policy or
  wording changes, protocol validator when this document changes, git diff
  --check.
- merge_policy: block merge unless approval is explicit and required checks
  pass.
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
| context | W1 target or UNKNOWN | read W1 docs, matrix row, smoke report, selected reader code, tests, and dirty state | W1 context packet and unknowns | files and commands exist | target needs approval or sample is unavailable |
| plan | W1 context packet | select exactly one target and expected evidence | scoped W1 card | no support-claim broadening | target is not testable |
| implement | scoped W1 card | add one parser/diagnostic/evidence change | narrow diff | structural freeze and boundary rules | next action exceeds W1 |
| validate | changed files and evidence target | run deterministic checks | pass or actionable failure | harness commands | same failure repeats twice |
| ledger | validation output | update `.agent/` logs and WORKLOG if material | resumable record | timestamp and redaction rules | no material change |
| improve-or-stop | ledger and result | retry one concrete variable or stop | pass/block/approval decision | hard brakes | max iterations, no progress, or approval gate |

## Improvement Flywheel

| Stage | Data | Action | Gate | Rollback |
| --- | --- | --- | --- | --- |
| trace | W1 command summaries, sample path, failure stage, object metadata | collect redacted trace | selected blocker is reproducible | lower sample count |
| eval | fixture row evidence, real smoke, targeted tests | score support vs visible unsupported classification | no silent drops | keep previous blocker |
| failure-cluster | repeated object-map/object-decode failures | group by stage, handle, payload prefix, entity class | actionable cluster | discard overbroad inference |
| dataset | public/local AC1015 samples and row fixtures | promote representative blocker to test/evidence | coverage increases | revert fixture pointer |
| optimizer | timeouts, budgets, sample slice | tune bounded harness parameter | quality/cost/latency/safety improves | reject candidate |
| registry | matrix row and evidence schema | update row state only when justified | matrix validate and review | revert row state |
| rollout | AC1015 row evidence | limited internal preview only | no policy regression | restore previous support posture |

## Loop Plan

| State | Input | Action | Output | Validator | Stop Condition |
| --- | --- | --- | --- | --- | --- |
| G0 baseline | current repo and matrix | inspect W1 state and preserve dirty worktree | baseline note | matrix validate | dirty conflict blocks W1 |
| G1 reproduce | W1 smoke and fixture commands | reproduce current blocker and fixture pass | evidence baseline | smoke and fixture outputs | sample missing |
| G2 choose target | reproduced evidence | choose one blocker or viewer-evidence target | W1 target card | target maps to one validator | needs external backend |
| G3 execute | target card | implement or document one narrow slice | diff and evidence | py_compile/pytest | repeated failure |
| G4 validate | diff and evidence | run required harness | validation log | deterministic checks | high-risk regression |
| G5 ledger | validation log | append .agent logs and WORKLOG if material | resumable record | redaction rules | none |
| G6 stop | result | report pass, blocked, or approval need | final response | response contract | terminal |

## Harness Plan

| Order | Check | Command Or Tool | Exists? | Pass Criteria | Fallback |
| ---: | --- | --- | --- | --- | --- |
| 1 | matrix state | `python scripts\native_cad_version_matrix.py validate` | yes | exits 0 | inspect JSON row manually |
| 2 | real sample state | `python scripts\native_cad_real_sample_smoke.py --import-samples-per-code 2 --json-report .local\native_cad_w1_goal_smoke.json --md-report .local\native_cad_w1_goal_smoke.md` | yes | exits 0 and reports AC1015 result visibly | inspect generated JSON/MD |
| 3 | fixture evidence | `python scripts\native_cad_row_evidence.py --code AC1015 --fixture-row --json-out .local\native_cad_w1_fixture_row_evidence.json` | yes | exits 0 and `status=PASS` | run relevant unit tests |
| 4 | typecheck | `python -m py_compile <changed python files>` | yes | exits 0 | inspect syntax by targeted import |
| 5 | targeted unit tests | `python -m pytest <targeted tests> -p no:xdist -q` | yes | exits 0 | narrow to changed module tests and document gap |
| 6 | fallback invariant | `python scripts\native_cad_goal_loop.py invariants --run-fallback-tests` | yes | exits 0 when behavior changes | run `--quick` and affected fallback tests |
| 7 | policy wording | `python scripts\cad_policy_gate.py` | yes | exits 0 | remove unsupported wording |
| 8 | protocol structure | `python C:\Users\user\.codex\skills\project-loop-engineer\scripts\validate_protocol.py docs\collab\DWG_NATIVE_W1_AC1015_GOAL_COMMAND.md` | yes | exits 0 | manually check Golden Set |
| 9 | artifact check | `git diff --check` | yes | exits 0 | inspect changed hunks |

## Success Metrics

- metric: W1 target selection
  - measurement: one of object-map blocker, object-decode blocker, or real
    viewer LOD0 evidence is selected.
  - pass_threshold: exactly one selected target per loop.
  - evidence: plan note, validation log, or final response.
- metric: real evidence progress
  - measurement: selected AC1015 real sample changes from opaque failure to
    canonical evidence or stable visible unsupported classification.
  - pass_threshold: no silent drops and no ambiguous failure stage.
  - evidence: smoke JSON/MD, targeted tests, row evidence packet.
- metric: quality
  - measurement: canonical entity count, compare result, viewer primitive
    count, or stable unsupported diagnostic.
  - pass_threshold: geometry support must be exact for the observed payload;
    unsupported content must be visible and fail closed.
  - evidence: unit tests and evidence artifacts.
- metric: cost
  - measurement: command count and human approval touches.
  - pass_threshold: <= 12 commands for routine W1 iteration unless broader
    validation is justified.
  - evidence: `.agent/validation-log.md`.
- metric: latency
  - measurement: bounded smoke runtime, row evidence runtime, import elapsed_ms.
  - pass_threshold: scripts finish within configured timeouts; long corpus work
    is scheduled explicitly.
  - evidence: command output and JSON timing fields.
- metric: safety
  - measurement: policy gate, backend selection, wording, redaction, git action.
  - pass_threshold: zero forbidden wording, unapproved backend use, secret leak,
    destructive git, stage/commit/PR without approval.
  - evidence: policy gate and git status.
- metric: drift
  - measurement: matrix row state, evidence booleans, cache identity fields,
    schema versions.
  - pass_threshold: every drift has evidence and validator output.
  - evidence: matrix diff and row evidence.

## Validation Matrix

| Claim | Evidence Required | Validator | Pass Criteria |
| --- | --- | --- | --- |
| AC1015 object-map blocker is closed | `sample_AC1015.dwg` imports or has stable unsupported object-map diagnostic | real smoke plus targeted reader test | no opaque `DWG_ADAPTER_FAILED` without stage/details |
| AC1015 object-decode blocker is closed | `example_2000.dwg` imports supported geometry or reports stable unsupported payload class | real smoke plus targeted decoder test | no silent entity drop |
| Real viewer LOD0 evidence exists | native-cad-viewer-evidence/v1 or equivalent artifact from real AC1015 import | viewer evidence script/test | primitive count and bbox within budget |
| Matrix remains honest | row evidence flags match actual evidence | matrix validator | no promotion beyond evidence |
| Fallback/default policy remains safe | unsupported versions and fallbacks are not broadened | goal-loop invariants and cad_policy_gate | no unapproved backend or wording regression |

## Telemetry Plan

- trace_store: `.agent/validation-log.md`, `.agent/failure-history.md`,
  `.local/native_cad_w1_goal_smoke.json`,
  `.local/native_cad_w1_goal_smoke.md`,
  `.local/native_cad_w1_fixture_row_evidence.json`, and targeted build reports.
- spans_to_capture:
  - selected W1 target
  - command lines and exit codes
  - sample path and DWG code
  - failure stage and error code
  - reader error type and object handle/offset/payload prefix when available
  - canonical/raw entity counts
  - viewer primitive count and bbox
  - matrix row state and evidence booleans
  - guardrail events such as forbidden wording or approval blocks
- metrics_to_capture:
  - success rate
  - retry count
  - loop count
  - p95 import/smoke/evidence latency when available
  - command count as cost proxy
  - unsafe tool call rate
  - fallback/default backend violations
- sampling_policy: capture every W1 row-promotion attempt and every new
  real-sample failure class; sample stable fixture reruns after repeated passes.
- redaction_policy: redact secrets, credentials, tokens, PII, payment data, and
  private customer paths.
- retention_policy: keep raw `.local` sample artifacts local unless the user
  approves sharing; commit only planning docs, tests, code, and safe summaries.

## Eval Plan

- golden_set: AC1015 fixture row evidence, bounded public/local AC1015 smoke
  samples, targeted reader/decoder fixtures, converted-DXF oracle baselines
  where available.
- online_eval: human approval for row promotion beyond W1, support wording,
  release scope, backend/license decisions, PR/merge.
- offline_replay: real-sample smoke, targeted unit tests, row evidence,
  viewer LOD0 evidence, fallback invariants.
- human_review: required for any support claim change, default enablement,
  commercial or GPL-family backend decision, or unresolved high-risk finding.
- slice_coverage:
  - versions: `AC1015` only
  - samples: `sample_AC1015.dwg`, `example_2000.dwg`, already importable
    nextgis AC1015 samples when available
  - entities: LINE, CIRCLE, ARC, LWPOLYLINE first; visible unsupported for
    payloads outside the implemented clean-room slice
  - failures: object map offset, object decode payload, unsupported entity,
    corrupted/encrypted/timeout when encountered
- pass_fail_rules: fail W1 if fallback is counted as native, unsupported content
  is silent, matrix state exceeds evidence, or wording exceeds evidence.

## Skillification Plan

- stable_contract:
  - `native-cad-w1-target/v1`
  - `ac1015-failure-taxonomy/v1`
  - `native-cad-row-evidence/v1`
  - `native-cad-viewer-evidence/v1`
  - `ViewerPrimitiveSource`
  - `native-cad-cache-identity/v1`
- signatures_or_interfaces:
  - W1 target card
  - AC1015 failure classifier
  - real-sample smoke summary
  - row evidence packet
- scripts_to_create:
  - none required immediately; existing W1 scripts are enough.
  - optional future addition: a row-local AC1015 blocker report aggregator if
    W1 evidence remains manual after two iterations.
- references_to_create:
  - row-specific AC1015 decode notes only after a repeated payload class is
    understood and test-backed.
- assets_to_create:
  - optional W1 evidence checklist template after one full W1 loop succeeds.
- registry_artifact:
  - matrix row state plus schema-versioned AC1015 evidence JSON.

## Optimizer Plan

- prompt_or_program_optimizer: none until there are repeated W1 traces and
  stable pass/fail labels.
- harness_search_params:
  - import sample count
  - import timeout
  - object probe cap
  - LOD0 primitive budget
  - payload byte budget
  - targeted pytest set
  - reviewer use
- objective:
  - maximize quality: exact geometry or visible unsupported diagnostics
  - minimize cost: command count and human review minutes
  - minimize latency: bounded smoke and row evidence runtime
  - maintain safety: fail-closed behavior, policy wording, license compliance,
    redaction
- holdout_or_canary: customer-approved AC1015 real pair or additional public
  AC1015 sample only after current blockers are resolved.
- stop_conditions: reject candidates that hide warnings, classify fallback as
  native, worsen existing fixture evidence, exceed latency budgets, or require
  unapproved backend use.

## Registry Rollout Plan

- versioning: AC1015 matrix row state plus evidence schema version.
- aliases_or_labels:
  - `ac1015_importable`
  - `ac1015_visible_unsupported`
  - `ac1015_viewer_lod0_real`
  - `ac1015_comparable_candidate`
- canary_policy: no product canary for W1 unless explicitly approved; W1 is
  evidence and internal preview hardening only.
- promotion_gate: matrix validate, row evidence, real-sample smoke, targeted
  tests, fallback invariants, policy gate, and human review when wording or
  enablement changes.
- rollback_condition: quality, cost, latency, safety, matrix honesty, support
  wording, or fallback/default-backend regression.
- audit_record: append `docs/collab/WORKLOG.md` for material changes and link
  evidence from `STATUS.md` only when release work begins.

## Failure Ledger Policy

- failure_history_path: `.agent/failure-history.md`
- validation_log_path: `.agent/validation-log.md`
- timestamp_format: ISO 8601, for example `2026-06-14T00:30:00+09:00`
- required_fields:
  - timestamp
  - attempted_action
  - observed_failure
  - evidence
  - next_action_change
- redaction_rule: replace secrets, credentials, tokens, PII, payment data, and
  private customer paths with `[REDACTED]`.
- retention_rule: keep raw local evidence local unless user approves sharing.

## Self-Improvement Policy

- Retry only when validation evidence identifies a concrete W1 change.
- Change one major variable per retry when possible.
- Stop after the same failure appears twice.
- Stop after two no-progress turns.
- Do not improve apparent success by hiding fallback, warning, unsupported
  entities, object-map failures, object-decode failures, or license failures.
- Escalate when the next action requires approval outside the automation
  boundary.

## PR Management Protocol

- branch_strategy: inspect current branch before work; do not create, switch, or
  delete branches unless requested.
- commit_policy: do not stage or commit unless requested.
- pr_creation_condition: user explicitly asks for PR creation after checks pass.
- pr_body_template: changed files, W1 target, evidence outcome, validation,
  risks, rollback.
- required_checks:
  - `python scripts\native_cad_version_matrix.py validate`
  - `python scripts\native_cad_real_sample_smoke.py --import-samples-per-code 2 --json-report .local\native_cad_w1_goal_smoke.json --md-report .local\native_cad_w1_goal_smoke.md`
  - `python scripts\native_cad_row_evidence.py --code AC1015 --fixture-row --json-out .local\native_cad_w1_fixture_row_evidence.json`
  - `python -m py_compile <changed python files>`
  - targeted pytest for changed modules
  - native goal-loop invariants when behavior changes
  - `python scripts\cad_policy_gate.py` when wording or policy changes
  - protocol validator when this document changes
  - `git diff --check`
- reviewer_requirements: human review for support wording, row promotion beyond
  evidence, legal/license posture, release gates, PR/merge.
- merge_policy: block merge unless user approval is explicit and required
  checks pass.
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
  - staging or committing without explicit approval
  - license-dependent backend activation
  - commercial SDK or GPL-family default dependency decision
  - default native support broadening
  - release wording change
  - AC1018+ expansion

## Human Review Summary

- changed_files:
- behavior_change:
- validation_performed:
- known_risks:
- rollback_plan:
- user_decision_required:

## Golden Set

Each answer must be "yes" before this W1 prompt is considered usable:

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

