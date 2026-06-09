# Sheet-Frame Producer — Implementation Plan (P-A)

Created: 2026-06-09 (Claude, continuing Codex `codex/integrate-claude-p0-visuals` / PR #5)
Layer 0 work-memory doc per WORK_MEMORY protocol (3+ files, complexity > 0.5).

## 1. Objective

Make the existing **sheet-frame viewer alignment actually activate for real DWG/DXF
compares**. Codex (2026-06-08) shipped the consumer (`sheet_frame_alignment.py`),
the propagation (`viewer_package.py`), and the GUI hook
(`workbench_visual_extensions.py`), all green under synthetic tests — but the
**producer is missing**: nothing writes a detected drawing-frame (도곽) bbox into
the compare artifacts, so the consumer always sees `None` and the viewer always
falls back to world-union framing.

Desired user behavior (from handoff §4): *align both before/after panes by the
drawing border / title-block frame first, then inspect differences inside the
aligned sheet coordinate system.*

## 2. Confirmed root cause (verified 2026-06-09)

- `grep` over `src/`: **zero** writers of `before_cad_frame_bbox` /
  `after_cad_frame_bbox` / `sheet_frame_bbox_a|b` (only `viewer_package` *reads* +
  propagates them).
- Real final DWG artifact
  `build/manual_runtime_debug/.../cad_dwg_text_align_final_20260608_203448/results/viewer`
  contains only `ezdxf_bbox` / `ezdxf_bbox_resilient` (raw extents, deliberately
  **excluded** from sheet-frame hints). No frame keys.
- `detect_sheet_regions` runs only in the region-local / multi-detail path
  (`folder_compare_pipeline.py:1955`), which is **off by default**.
- Therefore every real compare uses `viewer_frame.py::shared_world_bbox_from_transforms`
  (union of raw extents). Sheet-frame alignment is dead in production.

This is orthogonal to the `alignment_precision_dead_end` finding: that was about
*registration precision / change counts*; this is about *viewport framing*.

## 3. Design

### 3.1 New producer module — `src/services/comparison/sheet_frame_detector.py`

Pure, headless, testable. Given a DXF modelspace (or cached path), detect the
**outer drawing frame (도곽)** bbox:

```
detect_sheet_frame_bbox(msp_or_path, *, dxf_cache_dir=None) -> SheetFrameResult | None
```

Algorithm (conservative, best-effort):
1. Compute full extents (reuse existing extents helper; resilient path).
2. Scan modelspace for **closed rectangular** entities (LWPOLYLINE/POLYLINE)
   reusing `sheet_region_detector` helpers: `_is_closed_rectangular_entity`,
   `_polyline_points_form_rectangle`, `_entity_bbox`, `_clean_bbox`.
3. Candidate = rectangle whose bbox covers a **large fraction of the extents**
   (e.g. area ≥ 0.55 of extents-area AND ≤ 1.0, anchored near extents corners).
   Prefer title-block/frame layer matches (`title_block_layer_patterns.py` /
   `region_profile.matches_frame_layer`) as a confidence boost, not a requirement.
4. If multiple, pick the **outermost** plausible 도곽 (largest covering rectangle
   that is not the entire extents noise and not a tiny title box).
5. Return `None` when no candidate clears the confidence gate → caller keeps the
   current world-union fallback. **Never guess.**

`SheetFrameResult`: `{ bbox, method, confidence, coverage_ratio, layer }`.

### 3.2 Injection point — per-pair manifest (default path)

Frame bbox must land where `viewer_package._sheet_frame_bbox_for_pair_side`
reads it: the **per-pair artifact/manifest** (`pair_artifact`) or change_zones
rows. `build_change_zones` only sees `ComparisonResult` (no source DXF), so inject
at the **per-pair manifest assembly** in the default compare path where the
effective (post-fallback) source DXF paths are known:
- `folder_compare_pipeline.py` (per-pair manifest entry) — PRIMARY.
- `import_compare_pipeline.py` — mirror if it assembles pairs independently.

Write `before_cad_frame_bbox` / `after_cad_frame_bbox` into the pair manifest
entry (and overlay JSON if assembled separately). Reuse the existing DXF cache so
the detector does **not** double-read large drawings.

> Phase 1 task = pin the exact function/lines that assemble the per-pair manifest
> with `source_a`/`source_b` (folder_compare_pipeline.py:~1836 area) and confirm a
> single producer site covers both file-pair and folder selections.

### 3.3 Consumer — no change

`sheet_frame_alignment.py` + `viewer_package.py` propagation + GUI hook already
consume these keys. Once the producer writes them, alignment lights up. Aspect-
mismatch rejection (`max_aspect_mismatch=0.08`) already guards bad pairs.

## 4. Files

| File | Change | New/Edit |
|---|---|---|
| `src/services/comparison/sheet_frame_detector.py` | producer detector | **new** |
| `src/services/comparison/sheet_region_detector.py` | export/reuse rect helpers (factor if private) | edit (minimal) |
| `src/services/comparison/folder_compare_pipeline.py` | call detector, write frame bbox into per-pair manifest | edit |
| `src/services/comparison/import_compare_pipeline.py` | mirror if needed | edit (maybe) |
| `tests/unit/services/comparison/test_sheet_frame_detector.py` | detector unit tests | **new** |
| `tests/unit/services/comparison/test_viewer_package.py` | producer→consumer integration (real-ish fixture) | edit |

No edits to `drawing_compare_workbench.py` (monolith). No new P5-G* gate.

## 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **도곽 detection unreliable** (not every drawing has a clean rectangular border) | Conservative confidence gate; return `None` → world-union fallback unchanged. Aspect-mismatch reject already in consumer. This is the crux — bias toward *not* emitting over emitting wrong. |
| Re-read cost on large DWG | Reuse `dxf_cache_dir`; run on the already-cached effective DXF; scan is O(entities) rectangle filter, cheap. |
| False 도곽 = inner detail frame | Require large coverage of extents + outermost selection; penalize table/title keywords. |
| Regressing world-union path | Producer is additive; if `None`, zero behavior change. Keep existing tests green. |
| Synthetic-only test trap (the very bug we're fixing) | Add a test that runs the **real** detector on a fixture DWG/DXF with a known 도곽 and asserts frame keys reach the generated manifest — not just injected metadata. |

## 6. Verification

1. Unit: `test_sheet_frame_detector.py` — rectangle/coverage/confidence/None cases.
2. Integration: detector → manifest carries `before/after_cad_frame_bbox` on a
   real fixture (not synthetic injection).
3. Regression: handoff-listed suite stays green —
   `test_sheet_frame_alignment.py`, `test_workbench_visual_extensions.py`,
   `test_viewer_package.py`, `test_viewer_frame_p0_3.py`, `test_zone_focus_zoom_match.py`.
4. Real artifact: regenerate the `D:\도면 비교` DWG pair package; grep
   `viewer_manifest.json` + `overlays/<pair>.json` for frame keys (must now be
   **present**, were absent on 2026-06-09).
5. GUI (P-B, follows): live run via windows-controller; both overview panes point
   at the same sheet-frame area; check `logs/error_20260609.log` delta = 0.
6. `python -m py_compile` touched files + `git diff --check`.

## 7. Rollback

Producer is additive and gated. Rollback = stop writing frame keys (revert the
pipeline edit); consumer reverts to `None`→world-union automatically. New module
is inert if not called.

## 8. Out of scope (frozen)

AC1032 native DWG, PDF-first skeleton (ADR-001 not accepted). Region-local /
multi-detail changes. Comparison/registration precision (dead end).

## 9. Implementation outcome (2026-06-09)

**Done & verified (additive, safe):**
- `sheet_frame_detector.py` — conservative outer-도곽 detector (polyline-only scan,
  reuses `sheet_region_detector` rectangle helpers, `None` when not confident).
- Producer wired as **renderer-emit** in `dxf_renderer.render_with_transform`:
  emits `cad_frame_bbox` into the transform while the modelspace is already open
  (zero extra DXF reads). Transform is serialized verbatim into the viewer
  manifest's `before/after_transform`; the GUI hook already reads `cad_frame_bbox`.
  Chosen over per-pair re-detection (which cost ~5.7 s/side re-reading the DXF).
- Clean-extents fix: detector uses the renderer's outlier-filtered extents, so the
  MULTILEADER contamination (stray y=-34.89M point) no longer zeroes coverage.
- Tests: 9 detector unit + 2 renderer-emit integration + producer→consumer
  contract; 126 regression green; `git diff --check` clean.

**⚠️ Real-world limitation (dominant):**
The primary customer drawing `240111_P5 복합동_PSRC,HMB` is a **459k-wide
multi-detail 상세도 with NO single modelspace 도곽** (56 closed rectangles =
detail frames, largest covers 0.05% of clean extents; the border/title block
lives in PAPERSPACE layouts 배치1/배치2). The detector honestly returns `None`,
so this work gives **no benefit to the primary multi-detail drawing** — it only
helps drawings that have a real modelspace border. This was caught by running the
producer on the actual drawing, exactly the synthetic-test trap this work set out
to avoid.

**Open decision — multi-detail re-origined framing (NOT yet done):**
For the re-origined (+128k) multi-detail case the correct viewer anchor is
**clean content-extents → per-side overlay framing** (not world-UNION). That
touches the path Codex deliberately excluded (`world_bbox`/`cad_world_bbox` as
sheet-frame hints). The renderer's outlier-filtered extents differ from that raw
value, and limiting it to a *last-resort fallback when 도곽 detection fails* +
the existing aspect gate would mitigate risk — but a 4-line-border sheet could
still misalign. This reverses a deliberate design decision → needs user/review
sign-off and a `STRUCTURAL_FREEZE_EXCEPTION_REQUEST.md` entry before implementing.
