# ADR-004 AC1018/AC1021 Baseline Report

Date: 2026-06-02

## Scope

This report records the AC1018/AC1021 Phase 0-C follow-up. The goal was to find
real before/after DWG revision pairs for compare-recall baselines. The work is
evidence-only: no product runtime path invokes ODA, and no native AC1018/AC1021
DWG support is claimed.

## Artifacts

- Primary candidate JSON: `out/adr004_ac1018_ac1021_candidate_selection.json`
- Primary candidate Markdown: `out/adr004_ac1018_ac1021_candidate_selection.md`
- Drive/CAD-root candidate JSON: `out/adr004_ac1018_ac1021_candidate_selection_drivewide.json`
- Drive/CAD-root candidate Markdown: `out/adr004_ac1018_ac1021_candidate_selection_drivewide.md`
- Phase 0-C aggregate JSON: `out/adr004_phase0c_baseline_validation.json`
- Phase 0-C matrix: `docs/collab/ADR004_PHASE0C_BASELINE_METRICS.md`

## Search Method

Added `scripts/select_adr004_ac1018_ac1021_candidates.py` to scan DWG headers
for AC1018/AC1021 and rank possible before/after pairs across folders. The
selector uses filename similarity, date prefixes, revision markers, Korean
change markers, and path proximity. Path proximity alone is not enough to
create a candidate, because different sheets in the same folder can otherwise
look like false revision pairs.

Two scans were run:

1. Primary roots: local Seoul/ecoform PSRC-related roots.
2. Expanded CAD roots: discovered local `D:\` CAD-related roots such as drawing,
   PSRC, P5, structure, and case folders.

## Result

| Version | Primary samples | Expanded samples | Candidates | Classification |
| --- | ---: | ---: | ---: | --- |
| AC1018 | 7 | 7 | 0 | missing_compare_candidate |
| AC1021 | 4 | 5 | 0 | missing_compare_candidate |

No real before/after revision pair was found for either version. Earlier loose
matching produced AC1018 pairs such as `A31` vs `A33`, but those are different
sheet names rather than revision evidence, so they were rejected by the stricter
selector and locked with a regression test.

## Decision

No AC1018/AC1021 DXF sample pack was generated. Creating a compare baseline from
unrelated sheets would make Phase 0-C look complete while measuring the wrong
thing. The correct current state is:

- AC1018: import/header coverage exists, compare-recall baseline missing.
- AC1021: import/header coverage exists, compare-recall baseline missing.
- Phase 0-C remains `partial`.

## Next Requirement

To move Phase 0-C to `ok`, provide or locate actual before/after revision DWG
pairs for AC1018 and AC1021. Once those pairs exist, generate converted DXFs,
run `scripts/validate_adr004_version_sample_pack.py`, then rerun
`scripts/validate_adr004_phase0c_baselines.py` with the new validation summary.
