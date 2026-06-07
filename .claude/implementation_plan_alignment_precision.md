# Implementation Plan — Alignment Precision (re-origin false-change removal)

> ## CONCLUSION (2026-06-07, after implementing + measuring): NOT SHIPPED — premise refuted.
> The implementation was built and run on the real pair, then **reverted**. The decisive
> measurements showed the alignment is ALREADY near-exact and the change count is GENUINE:
> - Full affine fit: **sx=1.000017, rot=0.00013°** (no hidden scale/rotation); rigid centroid
>   residual **0.022mm**. The transform is essentially perfect — precision is NOT the bottleneck.
> - Registered FULL-geometry matching finds only **531** truly-identical entities (flat from
>   0.5→2.0mm — not a tolerance issue). D1's "~5000" came from its self-admitted buggy matcher.
> - Of 3382 centroid-near (0.022mm) candidates, ~2851 have matching POSITION but DIFFERENT
>   geometry → genuinely MODIFIED in place. The rest are genuine ADD/DELETE. R1 is a real
>   major revision (+65% entities, redrawn sections). **~97% of the 16,253 changes are real.**
> - The removal-only build gave 19,910 (WORSE) because the baseline's 4,719 MODIFIED is mostly
>   GARBAGE consolidation by the 341km near-match — it makes the count *look* lower than honest
>   reality. An honest registered classification would likely RAISE the count, not lower it.
> **There is no large pool of false changes to remove. Alignment precision cannot honestly
> reduce this count.** The one genuine (QUALITY, not count) improvement available: replace the
> pathological 341km-tolerance near-match with registered-space near-match so MODIFIED pairs
> become MEANINGFUL (same-position-modified entities paired correctly, native coords). That is a
> separate, clearly-scoped change — it improves reviewability, not the number. See memory
> [[alignment_precision_dead_end]].

Status: REVERTED (premise refuted by measurement). Author: ultracode session 2026-06-07.
Layer-0 artifact (WORK_MEMORY protocol). Evidence: C:\Users\user\.claude\jobs\33eafe28\tmp\
ALIGNMENT_EVIDENCE.md + WF_D1_true_floor.md + WF_D2_regression_surface.md + WF_D3_quality_metric.md.

## Objective
Reduce FALSE changes when a drawing is re-originated (whole drawing re-inserted at a
different model-space origin, e.g. POT BEARING R1: B is +(-128347,+315950)mm from A),
WITHOUT (a) hiding genuine changes, (b) corrupting overlay coordinate spaces, or
(c) regressing any of the 56 guarded invariants (see WF_D2_regression_surface.md).

## Diagnosis (proven on the real pair; do not re-derive)
- A=7919, B=13053 entities. Transform B->A: dx=-128347.6 dy=315950.3 theta~1e-7 (PURE
  translation), inlier 0.78. Transform is ACCURATE (~0.1mm; MTEXT 744/744 within 0.1mm).
- Today: 16,253 changes (ADDED 8334 / DELETED 3200 / MODIFIED 4719), alignment_suppressed=0.
- `_geometry_hash` / NormalizedEntity.hash bake ABSOLUTE coords -> a re-origin makes every
  entity hash-mismatch -> all A deleted + all B added by `compare()`.
- The alignment is NEVER applied to coords; instead near-match tolerance is widened to
  shift_mag (=341,024mm) -> `find_near_matches` pairs WRONG counterparts; only 434/4719
  MODIFIED have displacement matching the alignment.
- Refuted naive fixes (measured):
  - register-then-rehash: 16,253 -> 17,513 (WORSE). exact-hash needs 0.01mm; residual ~0.1mm.
  - engage suppression (gate 0.85->0.50): 16,253 -> 15,819 (only -434; wrong pairs).
- Achievable reduction (D1, partial): ~5,000 B entities are re-origined-but-IDENTICAL
  (2620 LINE + 936 ARC + 744 MTEXT + INSERT/HATCH/DIM/...). Removing those unchanged pairs
  -> ~11,000. The rest is GENUINE R1 change (B has +65% entities). Honest floor is high.

## Why the obvious fixes are wrong (design rationale)
- The suppression check `_is_pure_alignment_artifact` is POSITION-ONLY
  (`|displacement + alignment.d| < threshold`). Under a global re-origin EVERY entity's
  displacement ~= -alignment, INCLUDING genuinely-modified ones (text/geometry changed but
  still moved with the drawing). So opening the gate would SILENTLY DROP real changes. Reject.
- exact-hash on registered coords misses unchanged entities (0.1mm residual > 0.01mm round).

## Chosen design: registered FULL-GEOMETRY unchanged-pair removal (re-origin-gated)
Add a pre-classification step that runs ONLY for a genuine re-origin and removes from the
deleted/added sets the pairs that are geometrically IDENTICAL after registration (so only
true unchanged vanish; genuine data/geometry/position changes survive as before).

### Gating (keeps EVERY fixture on the existing code path)
`is_reorigin = alignment is not None and alignment.is_significant
               and alignment.translation_magnitude > REORIGIN_TRANSLATION_MM (=1000.0)`
Fixtures 03 (0.7mm), 08 (<=50mm), 14 (0.5mm) NEVER reach this — they keep today's behaviour
exactly. A >=1m COHERENT global translation with the estimator's existing gates
(scale==1+-1%, inlier>=0.5, coarse-vetted) can only be a re-origin (a single moved detail
yields an IDENTITY global RANSAC fit, not a 1m+ transform).

### Algorithm (new helper `_remove_reorigin_unchanged_pairs`)
Operates on the post-`compare()` deleted[] (A, carries old_data) and added[] (B, new_data):
1. For each added change, transform its geometry into A-space using `alignment` and build a
   per-(entity_type, layer) spatial bucket keyed by transformed centroid (cell = match tol).
2. For each deleted change, look up same-type+layer added within the bucket; accept a pair iff
   FULL GEOMETRY matches within a LAYER-AWARE tolerance:
   - reuse the existing Q6 layer threshold semantics: structural layers 0.1mm, else 1.0mm
     (`_position_threshold_for_layer`) so a BEAM 0.4mm shift in a re-origined drawing is NOT
     removed (stays a MODIFIED) — preserves invariants 9-12.
   - "full geometry" per type from the change's data dicts (old_data vs transformed new_data):
     LINE start+end, CIRCLE center+radius, ARC center+radius+angles, LWPOLYLINE all points,
     TEXT/MTEXT position+content, INSERT insert_point+block_name+xscale+yscale+rotation,
     ELLIPSE center+major_axis+ratio; SPLINE/HATCH/SOLID/etc. -> centroid + non-positional
     discriminators (point_count/degree/closed, pattern_*, corner_count, block fingerprint).
     A type whose geometry cannot be verified from `data` (only centroid) is matched on
     centroid+discriminators ONLY (conservative: never drop without a discriminator match).
3. Matched pairs are removed from BOTH deleted and added (1:1, no reuse) and counted in
   `result.stats["reorigin_unchanged_removed"]` + `result.metadata[...]` (audit/honesty).
4. The remaining deleted/added flow into the EXISTING near-match/MODIFIED/suppression path
   UNCHANGED. (For re-origin we also skip the 341km tolerance widen — see below.)

### Tolerance widen change (re-origin only)
At dxf_comparator.py ~2282-2284: when `is_reorigin`, do NOT widen to shift_mag. The unchanged
pairs are already removed in registered space; the surviving genuine deleted/added should
near-match only TRUE residual moves -> use a bounded tolerance
(`max(self.tolerance, REORIGIN_RESIDUAL_MATCH_MM=2.0)`) in REGISTERED space (transform added
locations for the matching predicate only; emit native coords via original change objects).
NON-re-origin path is byte-for-byte unchanged.

### Coordinate-space safety (HARD constraint)
- The removal step only DROPS pairs; it never mutates emitted changes.
- Surviving MODIFIED still come from `_create_modified_change(d, original_a)` with
  location=new_loc(B native) / old_location=old_loc(A native). Registered coords are used
  ONLY as a transient matching key (shallow copies / local arrays), never stored on changes
  or entities_b. Overlays' bbox(B) / old_bbox(A) stay native.

## Files to change
- src/services/comparison/dxf_comparator.py
  - new module consts REORIGIN_TRANSLATION_MM=1000.0, REORIGIN_RESIDUAL_MATCH_MM=2.0
  - new helper `_remove_reorigin_unchanged_pairs(deleted, added, alignment) -> (kept_del, kept_add, removed)`
    + small geometry-feature extractor `_registered_geometry_key`/`_geometry_matches_registered`
  - compare_with_modified_detection: compute is_reorigin; call removal before near-match;
    re-origin tolerance handling; record stats. ALL inside `if is_reorigin:` branches.
- NO change to global_alignment.py, NO change to the inlier metric, NO change to
  `_is_pure_alignment_artifact` math, NO change to sensitivity defaults.

## Regression safety (must hold — from WF_D2_regression_surface.md, 56 invariants)
- Fixtures 03/08/14: translation <=50mm -> is_reorigin False -> existing path -> bit-identical.
  Re-run their guarding tests explicitly (test_alignment_artifact_guard, test_q6_structural_
  threshold, test_q_fu2_alignment_layer_aware, test_dxf_comparator_modified, _large_mode,
  test_text_near_match_radius, test_drawing_compare_engine, test_dxf_global_alignment,
  test_suppression_audit, test_phase_r1_alignment_skip_warp).
- Invariant 55 (fixture 08 inlier 0.83): UNTOUCHED — we don't change inlier or the gate.
- Invariants 9-12 (structural sub-mm preserved): removal uses the SAME layer-aware threshold.
- Large-mode (45-47), peak_changes invariant (47): removal happens on change lists before
  finalize; route counts through existing `_record_change`-compatible rebuild; assert the
  peak invariant in the new test.

## New tests
- test_dxf_comparator_reorigin.py:
  1. Synthetic re-origin (shift all entities +(150000,-90000)) with: N identical entities,
     1 genuine TEXT-content change at shifted pos, 1 genuine added, 1 genuine deleted, 1 BEAM
     0.4mm extra shift. Assert: identical removed (reorigin_unchanged_removed==N), TEXT change
     survives as MODIFIED with NATIVE coords, added/deleted survive, BEAM 0.4mm survives.
  2. translation just below REORIGIN_TRANSLATION_MM -> removal NOT triggered (existing path).
  3. peak_changes_pre_truncate invariant holds.

## Verification on real data
- `PYTHONPATH=... python diag_alignment_ceiling.py`-style run of compare_with_modified_detection
  on the POT BEARING pair. Expect total ~= 11,000 (down from 16,253), with the removed count
  ~= 5,000 surfaced in stats, and spot-check that known genuine changes (C-020 section, added
  stiffener plates) still appear. Report honestly; do NOT tune to a target.
- Full suite: pytest tests/unit/services/comparison -q (expect 3259 baseline still green +
  new tests). tests/unit/gui green. Collection clean.

## Honesty / anti-gate-inflation
- No new scorecard/gate. One new stat key (reorigin_unchanged_removed) for audit transparency.
- Report the genuine-change floor honestly (R1 is a major revision; ~11k is mostly real).
- Monolith net: 0 (changes are in src/services, not the GUI monolith).
