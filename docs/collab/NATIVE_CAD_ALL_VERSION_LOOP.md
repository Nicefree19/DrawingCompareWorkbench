# Native CAD Full Coverage Goal Loop

This loop defines the path toward broad native CAD version coverage without
changing the current customer support claim. A version is not considered ready
until its own evidence packet passes contract, import, compare, viewer, and
release-policy gates.

## North Star

Build a native CAD bridge path that can eventually cover every DWG release
family the product chooses to accept, while preserving these rules:

- no default support claim is made before evidence exists;
- no DWG version is added to `DwgVersionDetector.SUPPORTED_CODES` by default
  until the release gate approves it;
- each version is enabled only through an explicit adapter or backend policy;
- existing DXF/PDF/fallback comparison behavior must stay green;
- no GUI monolith growth is allowed for the bridge work;
- bridge failures must stay structured and user-actionable.

## Version Matrix

Treat the target as a matrix, not a single switch.

The machine-readable matrix lives at
`docs/collab/native_cad_version_matrix.json` and is validated by
`scripts/native_cad_version_matrix.py validate`. The regular goal-loop
invariants run this matrix gate automatically.

Row-level evidence packets are generated with
`scripts/native_cad_row_evidence.py`. A packet imports one before/after pair
through `NativeCadBridgeAdapter`, validates canonical schema output, runs the
existing compare engine, checks LOD0 budget fields, and records the native CAD
cache identity fingerprint.

For loop rehearsal before real samples are available, run:

```powershell
python scripts\native_cad_row_evidence.py --code AC1032 --fixture-row
```

This writes local fixture inputs under `.local/native_cad_fixture_rows/` and a
local packet under `.local/native_cad_evidence/`. These files are execution
artifacts only; they do not promote a matrix row.

After a local public/sample corpus is collected, run:

```powershell
python scripts\native_cad_real_sample_smoke.py
```

This writes `.local/native_cad_real_samples/smoke_report.json` and `.md`. The
report verifies manifest integrity and target-code coverage separately from
the bounded importer probe, so a file can count as real sample coverage without
being treated as successful native parsing evidence.

| Track | Codes | Evidence target |
| --- | --- | --- |
| Legacy | `AC1009`, `AC1012`, `AC1014` | open, decode, normalize, compare, and report unsupported entity gaps |
| Baseline | `AC1015` | preserve existing clean-room behavior and fixture parity |
| Mid-era | `AC1018`, `AC1021`, `AC1024` | bridge import, text/block/layer/layout fidelity, compare deltas |
| Modern | `AC1027`, `AC1032` | bridge import, large model performance, xref/layout metadata, viewer LOD0 |
| Future | unknown `ACxxxx` | fail closed with diagnostic until a row is added to the matrix |

Each matrix row has one of these states:

- `blocked`: no approved reader path or required sample corpus is missing
- `contracted`: bridge can emit the native scene-pack schema
- `importable`: canonical drawing validates for representative files
- `comparable`: existing compare engine produces expected diffs
- `viewable`: LOD0 payload renders and stays within size/time budgets
- `release_candidate`: evidence packet is complete and policy review passes
- `enabled`: backend policy explicitly enables that version

## Loop Stages

Use the existing `scripts/native_cad_goal_loop.py` runner for invariants and
ledger checkpoints. The all-version loop adds matrix-specific work inside each
stage.

### G0 Context Lock

Goal: freeze the current repository state and declare the version matrix slice.

Required actions:

- run `python scripts\native_cad_goal_loop.py init --overwrite` only when the
  current work state is intentionally accepted as the loop baseline;
- record the selected target row, sample files, bridge command, and license
  posture in the local ledger;
- confirm no product text claims broad DWG readiness.

Exit gate:

- `python scripts\native_cad_goal_loop.py invariants --quick`

### G1 Matrix Row Contract

Goal: define one version row precisely before implementation.

Required actions:

- create or update a row in a local evidence table with code, release family,
  bridge adapter id, sample source, license status, expected entity coverage,
  and known exclusions;
- define fixture expectations for layers, blocks, layouts, text, dimensions,
  xrefs, extents, and display primitives;
- define expected compare results for at least one before/after pair.

Exit gate:

- scene-pack contract parser rejects bad schema versions;
- missing SDK/bridge path returns `SDK_UNAVAILABLE`;
- unsupported future code fails closed.

### G2 Bridge Contract Validation

Goal: prove the bridge can emit a stable JSON contract for the row.

Required actions:

- run `scripts/validate_native_cad_bridge_contract.py` against the row sample;
- run `scripts/native_cad_row_evidence.py` when a before/after row pair exists;
- store a compact contract summary in the evidence packet;
- verify `NativeScenePack.overview_lod0_payload()` contains stable bbox,
  primitive count, adapter metadata, and source signature.

Exit gate:

- bridge result schema matches `native-cad-bridge-result/v1`;
- scene-pack schema matches `native-scene-pack/v1`;
- no fallback parser path silently claims the version.

### G3 Import Fidelity

Goal: convert the row sample into canonical drawing data.

Required actions:

- import through `NativeCadBridgeAdapter`, not through a parallel importer;
- validate against `docs/canonical-drawing.schema.json`;
- assert layers, blocks, text, dimensions, xref metadata, units, bbox, and
  warnings are preserved at the contract level;
- map every unsupported entity to a counted warning rather than dropping it
  silently.

Exit gate:

- targeted importer tests pass;
- adapter metadata includes version row, bridge diagnostics, and source hash;
- default detector policy remains unchanged.

### G4 Compare Fidelity

Goal: prove the existing comparison engine benefits from the row.

Required actions:

- run a before/after pair through `DrawingCompareEngine`;
- assert expected added, removed, modified, and unchanged counts;
- assert semantic changes such as text, block attributes, layer moves, and
  geometry edits surface in field-level diffs;
- compare output fingerprint must be deterministic across repeated runs.

Exit gate:

- compare tests pass for the row;
- baseline fallback tests still pass.

### G5 Viewer And Performance

Goal: prove the row is viewable without loading a full heavy CAD model into
the GUI.

Required actions:

- verify LOD0 payload size, primitive count, world bbox, and source metadata;
- add large-file performance budgets as row-local evidence, not as new global
  P5 gates;
- ensure viewer artifacts are lazy and do not enter GUI hot paths.

Exit gate:

- no edits to `src/gui/drawing_compare_workbench.py`;
- LOD0 payload stays under the row budget;
- invariant monolith freeze passes.

### G6 Adversarial Review

Goal: try to disprove readiness before promoting the row.

Required checks:

- legal/licensing review: adapter license is allowed for the intended build;
- corruption/encryption/timeout/cancel tests return stable failure codes;
- cache identity includes version, adapter id, bridge version, and source hash;
- evidence packet contains a `native-cad-cache-identity/v1` fingerprint;
- unsupported entity gaps are visible to the user;
- release wording does not overstate readiness.

Exit gate:

- `python scripts\native_cad_goal_loop.py invariants --run-fallback-tests`
- targeted native row tests pass;
- evidence packet has no open `CRITICAL` or `HIGH` findings.

### G7 Controlled Enablement

Goal: enable the row only after evidence exists.

Required actions:

- add explicit backend policy for the approved row;
- add release notes that identify the enabled row and remaining limitations;
- do not broaden unrelated versions;
- keep planned or unknown codes fail-closed.

Exit gate:

- policy gate passes;
- row evidence packet is linked from `docs/collab/STATUS.md` or a sync packet
  when release work begins.

## Evidence Packet Template

For each version row, create one packet with this shape:

```text
VERSION_ROW:
  code:
  release_family:
  state:
  bridge_adapter:
  bridge_command_hash:
  license_id:
  sample_corpus:
  canonical_schema_result:
  compare_expected_result:
  viewer_lod0_budget:
  unsupported_entities:
  failure_code_tests:
  cache_identity_fields:
  policy_gate_result:
  fallback_test_result:
  promotion_decision:
```

## Goal Command

Use this as the agent loop command for a row:

```text
You are working in D:\00.Work_AI_Tool\DrawingCompareWorkbench.

Objective:
Advance exactly one Native CAD version-matrix row toward release_candidate.
Do not claim broad DWG readiness. Do not change default supported DWG codes
unless the row has passed G0-G6 and the user explicitly approves G7.

Loop:
1. Read AGENTS.md and docs/collab/NATIVE_CAD_ALL_VERSION_LOOP.md.
2. Select one version row and record it in the local ledger.
3. Run the current invariants.
4. Implement only the smallest code/test/doc change required for the row.
5. Run targeted tests for the touched modules.
6. Run native goal-loop invariants with fallback tests.
7. If any gate fails, fix the failure or checkpoint it as blocked.
8. Append a checkpoint with goal, actions, evidence, verdict, and next stage.

Hard stops:
- No GUI monolith growth.
- No PDF-first implementation.
- No new global P5 gate unless the structural-freeze questions are answered.
- No default version broadening.
- No customer-facing readiness claim without row evidence and policy approval.
```

## Promotion Rule

A row can move to `enabled` only when all are true:

- bridge contract validates;
- canonical import validates;
- compare results match expected deltas;
- LOD0 viewer payload is bounded;
- structured failure taxonomy is tested;
- cache identity is version/adapter-aware;
- fallback tests pass;
- CAD policy gate passes;
- unresolved `CRITICAL` or `HIGH` findings are zero.

Anything less remains an internal bridge capability or local experimental row,
not a product support claim.
