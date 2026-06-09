# Content-Aware Framing — Verified Root-Cause Improvement Plan

Created: 2026-06-09 (Claude). Source: 16-agent root-cause workflow `wf_60e71f26` (5 investigators → 3 lens proposals → synthesis → 7 adversarial verifications). Every load-bearing claim was checked against code; corrections from verification are baked in below.

## 1. The verified root cause

The viewer frames **both** lightweight cameras to the **union of the two sides' FULL render extents** — the entire ~459k-unit multi-detail sheet — at load time, even though the system **already holds the world-space bbox of every change zone** in `self._active_overlays_by_zone`.

- Framing path: `drawing_compare_workbench.py:10002` → `workbench_visual_extensions.apply_shared_lightweight_camera_frame` (helper, line 47) → `viewer_frame.shared_world_bbox_from_transforms` (line 34-60, a plain min/max union of the two before/after render-extent boxes).
- The spatial knowledge it discards: each overlay carries a CAD-WCS-mm bbox, consumed **per-click** by `_focus_lightweight_on_zone_v2` (10546-10649). At load it frames the empty sheet instead of the changed content.

For a sheet whose ink is ~0.27% across 3 far-apart clusters, fit-to-whole-sheet mathematically forces every detail sub-pixel → near-blank overview.

**This single decision is the common parent of all symptoms:**
| Symptom | Why this root cause explains it |
|---|---|
| Unusable near-blank overview | Camera framed to full extents, not to the ink. Reframe → content fills viewport, **zero new render**. |
| ~13s first-zone latency on critical path | Because the overview shows nothing, the user **must** click a zone to see anything → fires the serial cold crop render. Frame-to-changes makes changes visible *before* any crop → 13s leaves the must-wait path. (Explains why naive prewarm was ineffective.) |
| No spatial "which detail changed" map | `zone_clusterer` keys on change_type/severity/layer **semantics, not position** (zone_clusterer.py:157-164). |
| Sheet-frame producer (PR#5) returns None; content-extents overlay contraindicated; prewarm ineffective | All three assume a **single bordered sheet** or attack latency directly. The root is multi-cluster **framing**. |

### Reconciliation with the earlier "world-union is correct" finding
Earlier this session we confirmed before/after **share origin**, so world-union correctly **aligns** the two sides. That is about *alignment* and remains true. The root cause is about *framing scope* (whole sheet vs changed content). The fix **keeps one shared frame** (preserves the shared-origin invariant) and merely **shrinks** it to the change-zone union. This is NOT the per-side content-extents overlay we correctly rejected.

### Correction made by verification (do not repeat the error)
The synthesis first blamed the region-detector's gap-collapse (`sheet_region_detector.py:1123`, `gap=max(diag*0.035,100)` inflated by a y≈-34.9M outlier). Verification found this a **category error**: that code is a different, **gated** subsystem (`detect_sheet_regions`) the load path never calls. The load path uses the plain 2-box union. Also the figures y=-34.9M / 459,000 / 178k are this session's **runtime measurements**, not in the codebase — treat as illustrative.

## 2. Why NOT the "obvious" fixes (verified)
- **Enable/fix the region-compare pipeline (R0-R10)**: REJECTED as the near-term fix. It collapses to 1 region on the real pair, its **per-region crop viewer manifest has ZERO `src/gui` consumers** (dead output, verified), and its R9 pilot never passed. L-effort across a broken/dead/gated chain to deliver what content-framing delivers now from data in hand. (Remains the right *long-term* track.)
- **Render-index sidecar / prewarm**: attacks the 13s number, not the blank overview; with content-framing the 13s leaves the critical path anyway. Secondary, deferred (P4).
- **Precision/registration tuning**: known dead-end, forbidden.

## 3. Phased plan

### P1 — Content-aware overview auto-frame (the root fix) · effort M · ✅ DONE (verified on real pair)

> **Design pivot (real-data, 2026-06-09):** the original "frame to the **union** of all change zones" was refuted by running it on the real pair — the changes span far-left↔far-right, so the union ≈ **161% of the full sheet** (no zoom). Implemented instead: **frame to the PRIMARY change zone** (`priority_rank` 1 = the zone the GUI also auto-selects). Verified on the real 240111_P5 pair: both panes frame to the C-001 area = **~7% of the sheet (≈15× zoom)**, centered (451905, -85039), and **aligned** (same shared frame), surviving the auto-select. Because the per-zone focus path skips the opposite pane for deleted/added zones, the previously-misaligned pane (full sheet) now matches → fixes "panes look at different positions". Camera state dumped via `direct_workbench_compare.py` (new diagnostic).

**Goal**: on load, frame both cameras to the primary change zone so details are visible at real size without clicking a zone.

Tasks:
1. New pure module `src/services/comparison/content_frame.py`:
   - `content_frame_from_zone_bboxes(overlays, *, padding_ratio=0.15) -> Optional[Bbox]` — union of valid change-zone world bboxes.
   - **MANDATORY correctness (from verification)**:
     - Per-side key selection mirroring `_focus_lightweight_on_zone_v2:10612-10627`: `added` → `bbox`, `deleted` → `old_bbox`, `matched` → union of both. Do NOT blindly union both keys (would corrupt the frame when one side is missing).
     - Drop degenerate/None boxes: `change_zones.to_dict` can emit `bbox=[0,0,0,0]` / `old_bbox=None`; validate `b[2]>b[0] and b[3]>b[1]`.
     - Coordinate space: reuse `convert_bbox_to_world_space` + `bbox_coordinate_space`/`pdf_dpi` gating (and `_backfill_pdf_overlay_coord_space_v2`) exactly as the focus path does. Empty/`cad_wcs_mm` pass through (transform.py:228-251); `image_pixels` (PDF) must convert — or be excluded in v1 (raster scope).
     - Min-span floor for a degenerate single/co-located union; apply padding.
   - Returns `None` on empty/all-degenerate → caller safe-degrades.
2. Modify the existing helper `src/gui/workbench_visual_extensions.py:apply_shared_lightweight_camera_frame`: insert content-frame **between** `apply_sheet_frame_camera_alignment` (returns None here) and the `apply_shared_camera_frame` full-extents fallback. Read `workbench._active_overlays_by_zone`; on a valid frame call `set_camera_to_world_bbox` on both viewports and return; else fall through. Existing top-level try/except already safe-degrades (lines 84-85). Add an honest status label (e.g. "변경 구역에 맞춰 표시 중").
3. **Scope v1 to the RASTER/DWG path** (`_load_lightweight_raster_preview_v2`, the only caller at :10002). The PDF path (`_load_lightweight_pdf_v2`, 9661-9924) does NOT call this helper — either add a separate PDF hook later or state the raster-only scope explicitly in the PR.
4. Ordering is safe (verified): `_set_active_overlays_v2` (:8254) runs synchronously at :11135 before the deferred `_run_lightweight_pair_load_v2` reaches :10002. Add an empty-overlays guard anyway (overlays reset to `{}` at 7950/9574/10251).

**Exit criteria (validate on the REAL 240111_P5 pair, not synthetic)**:
- Headless unit: feed the 7 real change-zone bboxes → returned frame contains all 7 and is a small fraction (≤~10-60%) of the full-extents union.
- Headless integration through the helper with a stub viewport recording `set_camera_to_world_bbox` args → asserts the content-frame path fires for this CAD pair.
- Visible-window ink fraction from the existing overview PNG jumps from ~0.27% to legible.
- **Live windows-controller** (headless can't reproduce framing/surrogate bugs — see memory): on load, details visible at real size WITHOUT clicking a zone; first meaningful change visible before any cold crop.
- Regression: bordered single-sheet drawings frame identically; PDF pairs unaffected (excluded/converted); full unit suite green.

### P2 — Cluster navigator (spatial map) · effort L · ✅ DONE (verified on real pair)

> **Implemented**: `content_frame.cluster_zone_bboxes` (single-linkage on zone bboxes, cut at the **largest RELATIVE/ratio jump** in merge gaps — scale-free, outlier-resistant, NOT a diagonal-fraction gap per the verifier) + new helper widget `src/gui/cluster_navigator.py` (a thin strip of per-cluster jump buttons; hidden when <2 clusters; click → `set_camera_to_world_bbox` pans BOTH panes to that cluster). Two thin monolith hooks: `attach_cluster_navigator` in the viewer-panel build (above the panes) and `update_cluster_navigator` in `_set_active_overlays_v2`. Verified on the real 240111_P5 pair: clusters into **3 distinct details** ({C-005/6/7 far-left notes}, {C-002/4 mid-right}, {C-001/3 far-right}); live run confirms the navigator is **visible with 3 buttons** and clicking the far-left cluster pans **both panes (aligned) to the notes area** (center ~(4122,4647), view ~3.3k). Thumbnails deferred (text buttons "디테일 N · 변경 K" deliver the spatial-jump value without the PNG-crop complexity).

**Goal**: a "which detail changed" map + instant cluster jump, without enabling the frozen region pipeline.

Tasks:
1. `cluster_zone_bboxes(zone_bboxes, *, gap)` in `content_frame.py` — single-linkage on the ~7 zones. **Gap MUST be absolute or median-spacing-based, NOT a fraction of the zone-set diagonal** (verifier: a diagonal-fraction gap would reproduce the very collapse that breaks `sheet_region_detector`). Add an outlier-zone guard.
2. New thin widget `src/gui/cluster_navigator.py` — small `QListView` IconMode of cluster thumbnails (crops of the EXISTING overview PNG via the existing `_expanded_crop_box`); click → `set_camera_to_world_bbox` to pan to that cluster. Hidden on any failure.
3. One-line hook from the existing pair-load completion path.

**Exit criteria**: headless `cluster_zone_bboxes` on the 7 real zones yields ≥2 clusters with C-001/003, C-002/004, C-005/006/007 in spatially distinct buckets; live navigator shows ≥2 thumbnails, click pans instantly with no 13s block; monolith delta ≤ a few hook lines.

### P3 — Title-block/notes noise suppression (independent secondary) · effort S
**Goal**: drop the C-005/6/7 title-block/notes "moved" zones to raise signal-to-noise — **only if the real layer names justify it.**

Tasks (verifier-corrected — 3 coordinated edits, not 1):
1. **Dump the real pair's modelspace layer-name set FIRST** (no `src/` tooling exists; `mcp__cad-drawing-mcp__list_layers` can serve). Confirm title/border/표제란/개정 tokens appear on the C-005/6/7 entities. **Skip the phase if absent** (the filter would be blind).
2. If justified: add `ignore_title_block_layers` to `NoiseFilterSettings` (with disk round-trip plumbing, noise_filter_io.py:57), pass it into `ChangeZoneOptions` at `folder_compare_pipeline.py:331-336` (reuses `is_title_block_layer` SSoT, title_block_layer_patterns.py:96), add a dialog checkbox.
3. **Surface a "dropped by title-block: N" count in the UI — do not drop silently** (honesty per `silent_fallback_pattern`; historically `ignore_title_block_layers=True` silently dropped real structural changes on REVERSE/SHEETPILE layers — the Q7 word-boundary regex fixes that class but the drop is still silent).

**Exit criteria**: on the real pair, title-block/notes moved zones removed (or phase skipped with evidence tokens are absent); no real structural edits dropped; drop count visible; tests green.

### P4 — Deferred render-index sidecar (only if cold render still hurts after P1) · effort M
**Goal**: cut residual cold parse cost after content-framing already removed it from the critical path. **Do not start unless P1 metrics show a residual user-visible cold wait.**

Tasks: measure post-P1; if material, `src/services/render_index_sidecar.py` persisting the render-index envelope keyed by `source_signature` (source_signature.py:15-65), loaded on cold start instead of recomputing whole-modelspace extents (zone_vector_renderer.py:545-554). Validate first-zone cold drops toward the parse-only floor.

## 4. Risks (verified)
- Coordinate-space correctness (PDF image_pixels) — reuse the focus path's conversion + gating; scope v1 raster.
- The change-zone union of two far-apart clusters spans ~80% width on this pair → P1 alone is a big-but-not-total win; P2 fully closes it. P1 still far better than 99.7% white and degrades safely.
- Degenerate/co-located zones over-zoom → min-span floor + padding.
- Ordering regression if a refactor moves overlay population after the deferred load → empty-overlays guard + a test asserting overlays precede the frame helper.
- P3 depends on real layer names (dump first) and must not drop silently.
- Live verification has windows-controller traps (image-scale clicks, console focus-steal, Korean-path surrogates) — follow the documented procedure.

## 5. Constraints honored
Helper/service modules only (no growth in the 14k-line monolith — the only monolith change is a call-site/hook). No new P5-G* gate. No PDF-skeleton (ADR-001 frozen). No AC1032. No precision tuning. The whole-sheet fallback guarantees no regression for bordered single-sheet drawings.
