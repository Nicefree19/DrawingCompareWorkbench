# Codex to Claude Code Handoff - 2026-06-09

## Purpose

Claude Code가 과거 작업 상태로 되돌아가거나 같은 문제를 중복 구현하지 않도록, 현재 `codex/integrate-claude-p0-visuals` 브랜치의 작업 맥락, 완료 범위, 충돌 위험 지점, 다음 작업 순서를 정리한다.

## Current Repository State

- Branch: `codex/integrate-claude-p0-visuals`
- PR: `#5` - <https://github.com/Nicefree19/DrawingCompareWorkbench/pull/5>
- PR base: `main`
- PR state at handoff: `OPEN`
- Latest local/remote commit: `348b25ca78cd367152403eb713928c36fab70c93`
- Latest commit title: `Fix sheet-frame viewer alignment propagation`
- `HEAD` and `origin/codex/integrate-claude-p0-visuals` are synchronized.
- Worktree was clean before this handoff document was created. After this document is added, expect this file to be the only new uncommitted file unless further work is done.
- Visible Workbench runtime from the previous Codex run is no longer running. PID `1526804` was checked and is not active.

## Do Not Re-merge Old Claude Worktree Wholesale

Claude PR `#3` is already merged into `main`, but the missing local Claude work was from `feat/p0-reliability-and-pvh-viewer`. Codex already selectively integrated the user-visible pieces from that branch into the current PR.

Do not merge `feat/p0-reliability-and-pvh-viewer` wholesale into this branch. Its older alignment/auto-register path conflicts with newer re-origin and selected-zone fixes on `main`.

If more Claude-side changes are needed, use this sequence:

1. `git fetch origin`
2. `git checkout codex/integrate-claude-p0-visuals`
3. `git pull --ff-only`
4. Review `git log --oneline -8`
5. Cherry-pick or reimplement only narrowly scoped missing changes after reading the current modules.

## Structural Freeze Rules Still Apply

- Avoid adding code to `src/gui/drawing_compare_workbench.py`. Move new GUI logic into helper modules such as `src/gui/workbench_visual_extensions.py`.
- Do not add new P5-G* audit gates unless the gate-inflation questions in `AGENTS.md` are answered.
- Do not implement PDF-first skeleton/prototype code until `docs/adr/ADR-001-pdf-first-transition.md` is accepted.
- If any freeze rule must be violated, write an exception request first in `docs/collab/STRUCTURAL_FREEZE_EXCEPTION_REQUEST.md`.

## What Codex Completed

### 1. Claude Visual/PVH Work Integrated Without Regressing Re-origin Fixes

Relevant commits:

- `dc17a8a Integrate Claude visual viewer hardening`
- `f488a75 Fix re-origin matching and zone crop rendering`
- `a5e1374 fix(gui): harden selected-zone PDF and DWG renders`
- `0647700 Fix selected-zone crop alignment and DXF text rendering`
- `348b25c Fix sheet-frame viewer alignment propagation`

Integrated user-visible pieces:

- Reference-PDF overlay path.
- Shared lightweight camera framing.
- Layer-filtered extents.
- Minimum visible revision-cloud footprint.
- Geometry-aware cloud overlay behavior.
- Oversized leader-line rendering.
- Runtime diagnostics around unsupported DWG/AC1032 paths.

Preserved newer `main` behavior:

- Re-origin matching fixes.
- Conservative canonical registered-space matching.
- Selected-zone source/crop restoration.
- ODA auto-convert remains limited to explicit converter-backed folder compare paths.

### 2. Selected-zone Before/After Crop Synchronization

Problem observed:

- Before/after selected-zone panes could show different world areas.
- One side could be blank or stale while the other side loaded.
- Full-detail CAD source rendering was being cut off near the old fast-crop timeout.

Fixes already in the branch:

- Blank sides receive a shared world frame for selected-zone crops.
- Before/after transforms can share identical crop windows for deleted/added cases.
- Full-detail CAD source upgrades get a longer timeout than fast crops.
- Transient fast-crop fallback telemetry is cleared when full-detail source rendering later succeeds.

Primary validation artifacts:

- `build\manual_runtime_debug\gui_compare_20260608\cad_dwg_text_align_final_20260608_203448`
- The final C-001 full-detail crop used `renderer_backend=ezdxf-matplotlib-zone`.
- Before/after transforms were identical for the selected deleted-zone crop.

### 3. DXF Text Rendering

Problem observed:

- Fast DXF render skipped `TEXT`/`DIMENSION`, so the viewer could not expose text changes reliably.
- Korean/CJK text could fall back to square glyphs or unreadable font output.

Fixes already in the branch:

- Fast renderer now draws text entities.
- Preferred CAD text font selection avoids the prior CJK square-glyph fallback.
- Existing verification rendered the relevant `BACK ... (PSRC+HMB)` label in the focused probe.

Useful tests:

- `tests\unit\services\comparison\test_dxf_renderer_backends.py::test_fast_renderer_draws_text_entities`
- `tests\unit\services\comparison\test_dxf_renderer_backends.py::test_preferred_cad_text_font_replaces_arial_style_for_cjk_labels`

### 4. Sheet-frame / Drawing-frame Viewer Alignment

User problem:

- The before and after drawing viewers could look at different positions even when comparing the same sheet.
- The next desired behavior is: align both panes by drawing border/title-block frame first, then find differences inside the aligned sheet coordinate system.

Current implementation:

- New module: `src/services/comparison/sheet_frame_alignment.py`
- GUI hook: `src/gui/workbench_visual_extensions.py`
- Package propagation: `src/services/comparison/viewer_package.py`

Behavior:

- If both sides provide usable frame bbox metadata, the viewer maps each pane through the same sheet-local coordinate window.
- A full-sheet local bbox is `(0.0, 0.0, 1.0, 1.0)`.
- Each side still uses its own native CAD bbox, so different origins/scales can align visually by sheet frame.
- Aspect mismatch is rejected before applying the alignment.
- If no usable frame metadata exists, the code falls back to the existing world-union camera frame.

Important design decision:

- `world_bbox` and `cad_world_bbox` are deliberately not promoted to sheet-frame hints. They can represent raw drawing extents rather than the border/title-block frame and would reintroduce misalignment.

### 5. Viewer Package Metadata Propagation

Latest commit `348b25c` made the sheet-frame path usable from generated CAD-CAD viewer packages.

What it adds:

- Extracts frame-only keys such as:
  - `before_cad_frame_bbox`
  - `after_cad_frame_bbox`
  - `sheet_frame_bbox_a`
  - `sheet_frame_bbox_b`
  - `drawing_frame_bbox`
  - `frame_bbox`
- Writes frame bbox hints into:
  - pair manifest entries
  - per-pair overlay JSON
  - `before_transform`
  - `after_transform`
- Adds tests to ensure `world_bbox` / `cad_world_bbox` do not become sheet-frame hints.

Key files:

- `src/services/comparison/viewer_package.py`
- `src/services/comparison/sheet_frame_alignment.py`
- `src/gui/workbench_visual_extensions.py`
- `tests/unit/services/comparison/test_viewer_package.py`
- `tests/unit/services/comparison/test_sheet_frame_alignment.py`
- `tests/unit/gui/test_workbench_visual_extensions.py`

## Verification Already Run

Core checks from the final sheet-frame work:

```powershell
python -m pytest tests\unit\services\comparison\test_sheet_frame_alignment.py tests\unit\gui\test_workbench_visual_extensions.py -q
```

Result: 7 passed.

```powershell
python -m pytest tests\unit\services\comparison\test_viewer_frame_p0_3.py tests\unit\services\comparison\test_sheet_frame_alignment.py tests\unit\gui\test_workbench_visual_extensions.py tests\unit\gui\test_zone_focus_zoom_match.py -q
```

Result: 18 passed.

```powershell
python -m pytest tests\unit\services\comparison\test_viewer_package.py::test_sheet_frame_bboxes_annotate_transforms_without_world_bbox_fallback tests\unit\services\comparison\test_viewer_package.py::test_viewer_package_propagates_sheet_frame_bboxes_to_cad_pair_manifest tests\unit\services\comparison\test_sheet_frame_alignment.py tests\unit\gui\test_workbench_visual_extensions.py -q
```

Result: 9 passed.

```powershell
python -m pytest tests\unit\services\comparison\test_viewer_package.py -q
```

Result: 27 passed.

```powershell
python -m py_compile src\services\comparison\viewer_package.py src\services\comparison\sheet_frame_alignment.py src\gui\workbench_visual_extensions.py
git diff --check
```

Result: passed.

Runtime smoke from previous Codex run:

- `logs\runtime_monitor\user_live_sheet_frame_pkg_20260608_220817\run_manifest.json`
- PID at that time: `1526804`
- Window title: `도면 변경 비교`
- Responding: `true`
- `logs\error_20260608.log` delta: `0`
- That PID is no longer running as of this handoff.

## Known Open Work

### A. Real drawing-frame extraction coverage

The viewer alignment now consumes and propagates frame bbox metadata, but it still depends on frame metadata being present. If a CAD-CAD package lacks `before_cad_frame_bbox` / `after_cad_frame_bbox` or equivalent frame-specific fields, the viewer falls back to world-union framing.

Recommended next work:

1. Trace where actual drawing-frame bbox detection is produced for real DWG/DXF runs.
2. Ensure that detector output is written into compare artifacts or `change_zones.csv` as frame-specific keys.
3. Re-run a real DWG GUI compare and inspect generated `viewer_manifest.json` plus `overlays\<pair>.json` for `before_cad_frame_bbox` and `after_cad_frame_bbox`.

Do not use `world_bbox` as a shortcut.

### B. GUI verification with real drawing files

Recommended manual/automated check:

1. Run Workbench with the known DWG pair from `D:\도면 비교\...` or the latest available real test pair.
2. Confirm overview before/after panes show the same sheet-frame area.
3. Select several added/deleted/modified zones.
4. Confirm selected-zone crop still uses identical before/after windows where expected.
5. Confirm text remains readable in full-detail CAD crops.
6. Check `logs\error_20260609.log` delta and viewer perf summary.

### C. AC1032 / native DWG support remains separate

Direct AC1032 DWG input still needs a converted-DXF fallback or explicitly configured converter. This is not solved by the sheet-frame work. Do not mix AC1032 native support work into viewer alignment unless the user explicitly asks.

### D. PDF-first remains frozen

Do not start PDF-first implementation work from the performance roadmaps until the ADR is accepted. Current allowed work is hardening existing PDF/DWG viewer and comparison paths.

## Suggested Next Work Order for Claude

1. Pull this PR branch exactly:

   ```powershell
   git fetch origin
   git checkout codex/integrate-claude-p0-visuals
   git pull --ff-only
   ```

2. Confirm no local drift:

   ```powershell
   git status --short --branch
   git log --oneline -5
   ```

3. Start with artifact inspection, not code edits:

   - Generate or open a current CAD-CAD viewer package.
   - Inspect `viewer_manifest.json`.
   - Inspect `overlays\<pair>.json`.
   - Check whether frame-specific bbox fields are present.

4. If frame fields are absent, add the narrowest upstream propagation from the detector/artifact writer into viewer package inputs.

5. Re-run the package and GUI checks listed above.

6. Update `docs/collab/STATUS.md` and `docs/collab/WORKLOG.md` only after behavior changes are verified.

## Conflict Avoidance Notes

- Do not edit `src/gui/drawing_compare_workbench.py` unless absolutely necessary. The visual extension hook exists to avoid growing that monolith.
- Treat these files as the active alignment surface:
  - `src/gui/workbench_visual_extensions.py`
  - `src/services/comparison/sheet_frame_alignment.py`
  - `src/services/comparison/viewer_package.py`
- Treat these tests as required for alignment changes:
  - `tests/unit/services/comparison/test_sheet_frame_alignment.py`
  - `tests/unit/gui/test_workbench_visual_extensions.py`
  - `tests/unit/services/comparison/test_viewer_package.py`
  - `tests/unit/gui/test_zone_focus_zoom_match.py`
  - `tests/unit/services/comparison/test_viewer_frame_p0_3.py`
- If touching DXF text rendering, also run:
  - `tests/unit/services/comparison/test_dxf_renderer_backends.py`
- If touching selected-zone process/runtime behavior, also run:
  - `tests/unit/services/comparison/test_zone_render_process.py`
  - `tests/unit/gui/test_zone_render_process_controller.py`
  - `tests/unit/scripts/test_direct_workbench_compare.py`

## PR Review Focus

When reviewing this PR, prioritize:

- Whether sheet-frame bbox metadata is present in real generated packages.
- Whether before/after overview panes now point at the same sheet area.
- Whether text is visible in real CAD full-detail crops.
- Whether fallback behavior remains stable when no frame metadata exists.
- Whether no old re-origin false-positive regression returns.

Do not reopen broad architecture work unless a real verification failure points to it.
