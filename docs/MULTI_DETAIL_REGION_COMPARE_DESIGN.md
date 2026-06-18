# Multi-Detail Region-Aware Compare — Design (Direction B)

Status: **Design / not yet implemented.** Authored 2026-06-17 from live-test
feedback on the SPLICE / POT BEARING pairs. This is the core-accuracy redesign
that fix A (zone-crop context floor) and L1/L2/L4/speed only worked *around*.

## 1. Problem (user, live test)

> "하나의 파일에 여러 도면이 들어가 있으면 실제 변경점을 파악해내지 못하고 너무
> 광범위를 변경 결과로 보여주니 정확도 및 효용성이 매우 떨어져."

On a sheet that contains **multiple detail views in one file**, the tool:
- flags changes **too broadly** — not localized to the detail view that changed,
- cannot pinpoint the actual change,
- frames the viewer wrong on load (over-zoom; mitigated separately by fix A).

## 2. Root cause — one coordinate space, outlier-inflated, no per-view localization

Measured (gui_run4, SPLICE REV01→REV02):

| Quantity | Value |
|---|---|
| extent recovery (outlier-inclusive) | **524,723 × 271,268 mm** |
| skeleton = real content | **150,899 × 30,661 mm** |
| detail-view zones | ~22,000–34,000 mm |
| a single tiny change zone | **889 mm** |

The diff runs over the **whole ~524 m space** (inflated by a few far-flung
outlier entities). There is no *detect → match → localize* of the N detail views
on the sheet, so changes scatter sheet-wide and "primary change" can be a tiny
isolated zone instead of "the detail view that changed."

## 3. Existing assets (present but NOT driving localization)

- `src/services/comparison/sheet_region_detector.py` — region detection exists.
- `region_detection_summary.json` / `region_match_summary.json` — produced per run
  but do **not** currently scope the entity diff.
- `content_frame.py::cluster_zone_bboxes` — scale-free spatial clustering of zones.
- `content_frame.py::context_floor_span` (new, fix A) — context sizing for crops.

The pieces exist; they are not composed into a per-view comparison pipeline.

## 4. Design — detail-view as a first-class unit

Pipeline: **sheet → detect detail views → match before↔after views → diff WITHIN
each matched view → present per view.**

### P1 — Outlier-resistant content extent
- Density-based content bbox that ignores far-flung outlier entities (the 150 m
  content vs the 524 m raw extent).
- Use it for the raster render extent, camera framing, and region-detection bounds.
- **Acceptance**: rendered/framed extent ≈ content (~150 m), not 524 m. Headless:
  assert the content bbox excludes the outliers that inflate the raw extent.

### P2 — Detail-view segmentation + matching (the hard part)
- Segment detail-view rectangles: title-block/frame lines, large whitespace gaps,
  or entity-density clustering. Evaluate + harden `sheet_region_detector`.
- Match before↔after views by position/size/content signature; absorb re-origin
  with a **per-view** alignment transform (connects to the L6 before/after
  coordinate-mismatch issue — alignment becomes per-view, not whole-sheet).
- **Acceptance**: the N detail views on the SPLICE / POT BEARING sheets are
  detected and matched; unmatched views are surfaced (added/removed view).

### P3 — Per-view localized diff
- Run the existing entity diff **within each matched view's bbox**, not the whole
  sheet. Changes are attributed to a specific view + view-local coordinates.
- "Primary change" = the view with the most significant change → the viewer frames
  to **that view** (a good detail-view scale, ~30 m), never a 889 mm sub-feature
  nor the 524 m sheet.
- **Acceptance**: every change attributed to a specific view; no sheet-wide scatter
  on a sheet where only one view changed.

### P4 — Viewer + change-list integration
- 3-level navigation: **sheet → view → zone**.
- Change list **grouped by view** — this also resolves the live-test "변경 1개만
  리스트업" fold (27 changes → 14 zones → 1 visible): the list shows views, each
  expandable to its zones, so nothing is silently folded away.
- On load: frame to the most-changed view (not the whole sheet, not a tiny zone).

## 5. Validation
- The current golden corpus is single-change synthetic; add **multi-detail-sheet
  golden pairs** (e.g. derived from the SPLICE / POT BEARING sheets).
- Metric: **change-localization accuracy** — is each change attributed to the
  correct detail view? (Complements the existing detection precision/recall.)
- The real bar: the user's own multi-detail sheets read as "this view changed,
  here," not "broad scatter."

## 6. Effort / risk / sequencing
- P1 medium · **P2 large (detection+matching is the crux)** · P3 medium (reuse the
  diff per view) · P4 medium (UI). A focused multi-session effort, not a patch.
- Highest-leverage item for the tool's usefulness on real (multi-detail) sheets —
  see `cold_critique_2026_06_17` (the differentiator hinges on this, not on the
  peripheral viewer fixes already shipped in PR #48).
- Predecessor roadmap: `MULTI_DETAIL_REGION_COMPARE_AGENT_ROADMAP.md` (if present)
  — reconcile with this design before starting P2.

## 7. What shipped around this (PR #48, do NOT mistake for solving §2)
L1 overlay-visibility, L2 zone-render resilience, L4 + zone-doc-cache speed, L5
content-frame floor, fix A zone-crop context floor. These improve the viewer but
the multi-detail **localization** in §2–§4 remains the open core problem.
