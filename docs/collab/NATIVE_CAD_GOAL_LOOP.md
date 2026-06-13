# Native CAD Goal Loop

This document turns the native CAD vertical-slice prompt into a repeatable
repo workflow. It does not approve a commercial DWG backend and it does not
change the current customer support claim.

## Purpose

Use `scripts/native_cad_goal_loop.py` to keep long-running agent work
resume-safe:

- capture the dirty-worktree baseline before edits;
- create a local append-only ledger;
- append loop checkpoints;
- run global invariants at loop boundaries;
- keep no-SDK and structural-freeze failures visible.

The actual native CAD implementation still belongs in the planned bridge,
scene-pack, importer, fixture, validation, and test modules. This loop runner
only keeps that work bounded and auditable.

## First Run

```powershell
python scripts\native_cad_goal_loop.py init
python scripts\native_cad_goal_loop.py invariants --quick
```

`init` writes:

- `.local/native_cad_goal_loop_state.json`
- `docs/collab/native_slice_ledger.md`

Both are local execution artifacts. The ledger is ignored by git and should not
be committed.

## Checkpoint

Append a ledger entry after each state-machine step:

```powershell
python scripts\native_cad_goal_loop.py checkpoint `
  --stage G1 `
  --goal "slice contract locked" `
  --actions "scope fence and oracle table written" `
  --evidence "see ledger section G1" `
  --verdict PASS `
  --next G2
```

## Invariants

For fast loop boundaries:

```powershell
python scripts\native_cad_goal_loop.py invariants --quick
```

For stronger boundaries:

```powershell
python scripts\native_cad_goal_loop.py invariants
```

For fallback-sensitive work:

```powershell
python scripts\native_cad_goal_loop.py invariants --run-fallback-tests
```

The invariant runner checks:

- the local ledger exists and has resume checkpoint fields;
- nothing is staged;
- the original dirty-worktree paths, statuses, and content hashes are still present;
- the workbench monolith did not exceed the 5-line added-code budget;
- the CAD policy gate, unless `--quick` is used;
- forbidden release wording in the current diff and untracked text files;
- optional fallback tests;
- `py_compile` for changed Python files;
- `git diff --check` plus trailing-whitespace checks for untracked text files.
- the native CAD version matrix validates against the current detector policy.

## Loop States

Use these states in ledger checkpoints:

- `G0`: context lock and baseline capture
- `G1`: slice contract and oracle table
- `G2`: design and seam review
- `G3`: implementation unit loop
- `G4`: verification
- `G5`: adversarial self-review
- `G6`: final evidence package

Forward motion should stop on any failing invariant. Classify the failure in
the ledger before patching it.
