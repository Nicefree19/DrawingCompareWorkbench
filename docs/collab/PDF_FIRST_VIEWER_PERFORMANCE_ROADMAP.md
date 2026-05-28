# PDF-First Viewer and Performance Roadmap

Last updated: 2026-05-27

## Decision

The program should move to a **PDF-first viewer** while keeping **CAD entity
comparison as the source of truth**.

The repeated blank-viewer problem is not a single rendering bug. It is caused
by several coupled issues:

- DWG/DXF vector rendering assumes that all CAD entities can be expanded by
  `ezdxf`, but real drawings contain fragile entities such as `MULTILEADER`,
  proxy graphics, missing styles, missing fonts, XREF-dependent content, and
  vendor-specific blocks.
- Viewer rendering, selected-zone rendering, scene packs, and comparison each
  reopen or reconvert the same source through different cache keys.
- CAD WCS, PDF page points, and rendered image pixels are not contractually
  separated in all manifests, which creates overlay accuracy risk.
- Large drawing performance is affected by normalization memory peaks,
  candidate matching explosion, eager tile/background rendering, and cold
  selected-zone worker state.

Therefore the target architecture is:

```text
DWG/DXF/PDF input
  -> compare truth layer
     - DWG/DXF: CAD canonical/entity diff
     - PDF: visual/text/OCR diff
  -> visual asset layer
     - source PDF or approved CAD->PDF/PDF-like artifact
     - PDF/PNG/WebP tile cache
  -> lightweight viewer
     - PDF/tile background first
     - CAD comparison overlays/clouds/pins
     - optional vector focus overlay only as a secondary enhancement
```

## Multi-Agent Findings

Four review agents were used:

- Rendering architecture: PDF-first viewer is the correct direction, but
  `ezdxf`/SVG must not be the primary viewer path.
- Performance/memory: bottlenecks are distributed across CAD normalization,
  candidate matching, cache-key fragmentation, eager viewer/tile rendering,
  and cold selected-zone workers.
- Accuracy/coordinates: the largest correctness risk is mixing `cad_wcs_mm`,
  `pdf_page_points`, and `image_pixels` under ambiguous names such as
  `world_bbox`.
- UX/operations: PDF display should be a normal state, not a preview failure.
  Render failures must degrade to visible backgrounds, pins, relative overlays,
  and external-open actions without repeated modal errors.

## Product Principles

1. **No blank viewer**
   A pair or selected zone must always show one of: exact PDF/tile background,
   raster fallback, skeleton fallback, relative-only pins, or explicit source
   open action.

2. **Compare truth is separate from visual fidelity**
   CAD entity comparison remains the authoritative difference source for
   CAD inputs. PDF/tile rendering is a display layer unless the input itself is
   PDF.

3. **Every visual result has a fidelity badge**
   Viewer state must distinguish:
   - `exact_world_render`
   - `pdf_visual_background`
   - `raster_refined`
   - `skeleton_preview`
   - `relative_only`
   - `render_failed_but_compare_ok`

4. **Failure is structured, not generic**
   Do not show repeated "vector render failed" messages. Record and display
   exact reason codes such as `cad_to_pdf_unavailable`, `render_timeout`,
   `missing_source`, `unsupported_dwg_version`, `bbox_transform_estimated`,
   `zone_vector_skipped`.

5. **First review beats perfect pre-render**
   For large drawings, the first reviewable result should not wait for all
   backgrounds, tiles, region-local artifacts, or vector focus renders.

## Backend Policy

The MVP should start with:

- user-provided PDF or sidecar PDF if available;
- Qt/PDF viewer path for PDF display;
- current CAD entity comparison for precision;
- current raster/skeleton fallback as safety net.

Commercial or legally sensitive CAD/PDF engines must be feature-gated and
reviewed before customer distribution.

| Backend option | Use | Risk |
|---|---|---|
| User-provided/sidecar PDF | MVP primary visual source | User must export with correct plot/profile |
| Qt/PySide PDF path | PDF viewer/display | Qt/PySide licensing obligations must be reviewed |
| Autodesk APS AutoCAD Automation | Future PoC for DWG->PDF | Cloud upload, latency, cost, customer data approval |
| Local AutoCAD/TrueView plot | Enterprise optional | Installed software/EULA/automation stability |
| ODA Drawings SDK | Commercial option | Membership/redistribution/legal approval |
| QCAD Professional CLI | PoC option | License/redistribution/fidelity verification |
| Aspose.CAD | PoC option | Cost, lock-in, fidelity verification |
| Current `ezdxf` renderer | Fallback only | Cannot be primary for real-world DWG/DXF fidelity |
| LibreDWG | Avoid for customer build | GPLv3+ compatibility risk |
| PyMuPDF/MuPDF/Ghostscript | Avoid unless licensed | AGPL/commercial licensing implications |

Reference links:

- Qt licensing: https://doc.qt.io/qt-6/licensing.html
- Qt LGPL obligations: https://www.qt.io/licensing/open-source-lgpl-obligations
- PyMuPDF licensing: https://pymupdf.io/
- Ghostscript licensing FAQ: https://ghostscript.com/faq
- Autodesk Automation API overview: https://aps.autodesk.com/design-automation-apis
- Autodesk Design Automation DWG/PDF overview: https://aps.autodesk.com/api/autocadio/
- QCAD command line tools: https://qcad.org/en/qcad-command-line-tools
- Aspose.CAD product overview: https://products.aspose.com/cad/net/
- LibreDWG GPLv3+ manual: https://www.gnu.org/software/libredwg/manual/LibreDWG.pdf

Final licensing decisions require legal review. This roadmap treats all
non-current rendering engines as optional backends behind explicit flags.

## Coordinate Tech Spec

All artifacts must declare coordinate space explicitly.

```text
CAD truth bbox:
  space = cad_wcs_mm
  bbox = [x0, y0, x1, y1]
  axis = x right, y up
  unit = mm or drawing units declared by importer

Region-local bbox:
  space = region_local_cad
  region_id = ...
  bbox_local = bbox_cad - region.origin

PDF page points:
  space = pdf_page_points_bl
  axis = x right, y up
  unit = pt
  page_bbox = [0, 0, page_w_pt, page_h_pt]

Rendered image pixels:
  space = image_pixels_tl
  axis = x right, y down
  unit = px
  dpi = effective render dpi
```

Required manifest fields:

```text
source_truth = cad_entity | pdf_visual | pdf_text | ocr
cad_bbox_space
cad_bbox
sheet_region_id
page_index / page_a / page_b
pdf_page_size_pt
pdf_render_dpi
effective_dpi
bbox_coordinate_space
world_to_pixel
pixel_to_world
transform_quality = exact | estimated | relative_only
y_axis = up | down
visual_asset_id
visual_backend_id
visual_backend_version
visual_backend_license_id
```

Transform rules:

```text
CAD bbox -> PDF/tile:
  transform all four bbox corners with an affine matrix,
  then recompute min/max.

PDF image pixels -> PDF points:
  x_pt = x_px * 72 / dpi
  y_pt_bl = page_h_pt - (y_px * 72 / dpi)

PDF points -> rendered image pixels:
  x_px = x_pt * image_w_px / page_w_pt
  y_px_tl = (page_h_pt - y_pt_bl) * image_h_px / page_h_pt

Tile index:
  tile_x = floor(x_px / tile_size)
  tile_y = floor(y_px / tile_size)
```

If the CAD-to-PDF transform is not exact, overlays must not be shown as exact.
Use `estimated` or `relative_only`.

## Performance Tech Spec

### Unified Perf Event Log

Add append-only `perf_events.jsonl` per run.

Required fields:

```text
run_id
pair_id
stage
event
elapsed_ms
rss_mb
working_set_mb
spool_mb
input_bytes
entity_count
cache_namespace
cache_key_hash
cache_hit
warning_count
error_code
render_mode
fidelity
```

Stage coverage:

- `scan`
- `match`
- `compare`
- `artifact`
- `region`
- `preview`
- `viewer`
- `tile`
- `zone_render`
- `export_profile`

### Performance Hypotheses To Validate

1. `DrawingNormalizer.normalize()` and `DxfImporter` create large memory peaks.
2. Dense text/dimension near-match paths can inflate candidate edges before
   truncation.
3. DWG->DXF cache keys are fragmented across compare, preview, scene pack,
   zone render, and vector render paths.
4. Viewer package generation is too eager when `render_policy=all` or top issue
   count is high.
5. Selected-zone rendering loses process-local DXF/PDF display-list caches
   when workers are restarted.
6. Region-local comparison and region viewer artifacts should run after first
   review, not before it.

### Cache Strategy

Introduce a single source signature:

```text
source_signature = hash(
  resolved_path_or_stable_id,
  file_size,
  mtime_ns,
  optional_head_tail_hash,
  importer_version,
  render_backend_id,
  plot_profile_hash,
  config_fingerprint
)
```

Namespaces using the signature:

- `compare_dxf`
- `preview_dxf`
- `scene_pack`
- `visual_asset_pdf`
- `visual_asset_tile`
- `zone_crop`
- `zone_focus`
- `region_detection`

Acceptance: the same DWG/DXF must not generate duplicate normalized DXF files
across compare/preview/scene/zone paths in a repeated run.

## Fallback Chain

For pair viewer:

```text
1. Source PDF or sidecar PDF -> Qt PDF/lightweight viewer
2. Approved CAD->PDF backend -> PDF viewer/tile
3. Approved CAD->PNG backend -> image/tile viewer
4. Existing DXF raster preview -> image viewer
5. Scene skeleton/LOD0 -> lightweight vector skeleton
6. Relative-only pins/clouds + external source open
```

For selected zone:

```text
1. Crop from existing PDF/tile/background image
2. PDF display-list crop if source PDF is available
3. Cached raster crop
4. Safe simplified vector focus, never primary
5. Relative-only zone marker with reason code
```

For vector SVG:

```text
Use only as optional overlay.
Never clear an existing PDF/tile/raster background because SVG failed.
Skip or simplify fragile entities; report entity fallback counts.
```

## Roadmap

### R0. Baseline Freeze and Evidence Capture

Goal: capture current failures and performance before changing architecture.

Tasks:

- Save current failing run metadata and vector result JSON samples.
- Record current first-review time, selected-zone cold/cache-hit latency,
  peak RSS, and duplicate DXF conversion count.
- Add a short baseline report under `docs/collab`.

Acceptance:

- At least one real DWG pair baseline.
- At least one multi-sheet/region pair baseline.
- At least one PDF pair baseline.

### R1. Coordinate Contract and Manifest Hardening

Goal: stop mixing CAD WCS, PDF points, and pixels.

Files/modules:

- `src/services/comparison/viewer_manifest_v2.py`
- `src/services/comparison/viewer_manifest_v3.py`
- `src/services/comparison/viewer_package.py`
- `src/services/comparison/zone_render_service.py`
- `src/gui/lightweight_viewport.py`
- tests: `test_cad_pdf_tile_transform.py`, `test_lightweight_viewport_coord_fix.py`

Tasks:

- Add coordinate-space enums/constants.
- Add explicit transform metadata to overlay records.
- Mark overlays as `exact`, `estimated`, or `relative_only`.
- Add CAD->pixel->CAD round-trip tests.

Acceptance:

- 72/150/200/400 DPI y-flip tests pass.
- Tile-boundary bbox tests pass.
- Existing CAD change counts unchanged.

### R2. Perf Event Logging and Diagnostics

Goal: make performance regressions measurable.

Files/modules:

- `src/services/comparison/runtime_budget.py`
- `src/services/comparison/folder_compare_pipeline.py`
- `src/services/comparison/drawing_batch.py`
- `src/services/comparison/viewer_package.py`
- `src/services/comparison/zone_render_service.py`
- `scripts/audit_drawing_compare_mvp_exit.py`

Tasks:

- Add append-only `perf_events.jsonl`.
- Record stage elapsed/RSS/cache hits.
- Add conversion-cache duplicate detector.
- Extend viewer perf summary to include zone render lifecycle.

Acceptance:

- `sample_count > 0` for scan/match/compare/viewer/zone stages.
- Instrumentation overhead <= 1% in comparator benchmark.
- Diagnostics explain whether slowness is compare, render, tile, or cache.

### R3. Unified Source Signature and Cache Routing

Goal: eliminate repeated DWG->DXF and repeated visual asset work.

Files/modules:

- `src/services/comparison/cache_paths.py`
- `src/services/comparison/dwg_differ.py`
- `src/services/comparison/zone_vector_renderer.py`
- `src/services/comparison/scene_pack_builder.py`
- `src/services/comparison/viewer_tile_cache.py`
- new: `src/services/comparison/source_signature.py`

Tasks:

- Implement shared source signature.
- Route compare/preview/scene/zone through shared cache lookup.
- Preserve legacy cache fallback for existing users.
- Record cache namespace and hit/miss reason.

Acceptance:

- Same DWG rerun gives DWG->DXF cache hit >= 95%.
- No duplicate DXF generation for same source signature in one run.
- Cache fallback reason is visible in diagnostics.

### R4. Visual Asset Manifest and PDF-First Viewer

Goal: make PDF/tile the normal viewer path.

Files/modules:

- new: `src/services/comparison/visual_asset.py`
- new: `src/services/comparison/render_backend_registry.py`
- `src/services/comparison/qt_pdf_adapter.py`
- `src/gui/lightweight_viewport.py`
- `src/gui/drawing_compare_workbench.py`

Tasks:

- Define `VisualAssetManifest`.
- Treat source PDF and sidecar PDF as first-class visual assets.
- Update GUI status: PDF visual background is not preview failure.
- Ensure vector overlay failure cannot clear the background.

Acceptance:

- PDF-first pair opens without "preview failed" wording.
- Blank viewer count = 0 for tested PDF and CAD fallback cases.
- User can still open original CAD/PDF externally.

### R5. CAD-to-PDF Backend Interface

Goal: prepare optional converters without locking product to one engine.

Files/modules:

- new: `src/services/comparison/cad_visual_backend.py`
- new: `src/services/comparison/cad_visual_conversion_worker.py`
- new: `src/services/comparison/render_backend_registry.py`

Tasks:

- Define backend interface: `probe()`, `convert_to_pdf()`,
  `convert_to_image()`, `capabilities()`, `license_id`.
- Add subprocess worker with timeout/cancel.
- Keep all non-approved engines disabled by default.
- Support backend PoC adapters behind flags.

Acceptance:

- No GUI freeze during conversion.
- Timeout default <= 180s with structured reason.
- Manifest records backend id/version/license id.

### R6. Lazy Tile and First-Review Optimization

Goal: avoid blocking the user on full rendering.

Files/modules:

- `src/services/comparison/viewer_package.py`
- `src/services/comparison/viewer_session.py`
- `src/services/comparison/viewer_tile_cache.py`
- `src/gui/drawing_compare_workbench.py`

Tasks:

- Default large runs to `fast_first_review + lazy/top-issues`.
- Generate overview/active pair first.
- Generate focus tiles on viewport/zone demand.
- Debounce stale selection renders.

Acceptance:

- Large run first-review-ready <= 300s initially, target <= 120s later.
- Tile manifest materialization <= 5s / 1000 pairs.
- Full tile generation never blocks result list availability.

### R7. Selected-Zone Render Stabilization

Goal: selected-zone view should not repeatedly fail or cold-start.

Files/modules:

- `src/services/comparison/zone_render_service.py`
- `src/services/comparison/zone_render_worker.py`
- `src/services/comparison/pdf_display_list_cache.py`
- `src/gui/drawing_compare_workbench.py`
- `scripts/benchmark_zone_render.py`

Tasks:

- Prefer crop from existing PDF/tile/background.
- Keep persistent zone render worker where possible.
- Prewarm active pair DXF index/PDF display list.
- Cache result by source signature + bbox + visual profile.
- Ensure failure returns visible fallback.

Acceptance:

- Cold p95 <= 2000ms for normal PDF/image path.
- Cache-hit p95 <= 500ms.
- Large DXF first zone <= 10s or explicit fallback, subsequent same source <= 1s.
- Selected-zone blank failure = 0.

### R8. Multi-Sheet Matching and Region-Local Compare Gate

Goal: one file with multiple drawings must compare matching sheets only.

Files/modules:

- `src/services/comparison/sheet_region_detector.py`
- `src/services/comparison/detail_region_matcher.py`
- `src/services/comparison/region_compare_pipeline.py`
- `src/services/comparison/region_match_overrides.py`
- tests: `test_multi_sheet_matching_oracle.py`,
  `test_region_aware_compare.py`

Tasks:

- Gate automatic region-local compare on high-confidence sheet matching.
- Treat whole-modelspace fallback as review-required, not auto-compare.
- Compare greedy matcher with Hungarian/global oracle in tests.
- Keep manual overrides as first-class input.

Acceptance:

- Whole-modelspace fallback rate = 0 for auto-enabled region compare.
- Ambiguous similar sheets go to review/manual matching.
- Same sheet moved in modelspace yields no false large diff.

### R9. Memory and Large-CAD Compare Hardening

Goal: reduce memory peaks and make large drawings predictable.

Files/modules:

- `src/services/comparison/drawing_normalizer.py`
- `src/services/comparison/dxf_importer.py`
- `src/services/comparison/dxf_comparator.py`
- `src/services/comparison/drawing_batch.py`
- `scripts/cad_performance_benchmark.py`

Tasks:

- Identify `deepcopy` and full-list accumulation hot spots.
- Stream or top-N retain changes earlier.
- Bound worker concurrency by estimated bytes/entity/RSS, not only file count.
- Record candidate edge counts and max cluster sizes.

Acceptance:

- 10MB/100k synthetic completes without crash and <= 1.15x baseline.
- 50MB test completes or exits with explicit limit/timeout reason.
- Large run peak RSS <= 2GB or <= current baseline + 10%.

### R10. QA, Release Gate, and Rollback

Goal: ship safely with measurable gates.

Tasks:

- Build customer-grade dataset:
  - CAD >= 8 pairs
  - PDF >= 8 pairs
  - large DWG >= 2
  - multi-sheet >= 4
  - raster/low-quality PDF >= 2
  - negative controls >= 2
- Add acceptance smoke for PDF-first viewer.
- Add path-leakage and dependency-license gate.
- Add rollback flags.

Acceptance:

- PDF pairs show zero repeated preview-failure messages.
- `_SUCCESS` and run manifest agree.
- Path leakage = 0 in shareable package.
- Non-approved render backends disabled in customer build.
- Rollback to lazy viewer and region-local off is documented and tested.

## Implementation Workstreams

Use separate agent/worktree ownership to avoid conflicts:

| Workstream | Owner scope | Primary files |
|---|---|---|
| WS1 Coordinate contract | transforms/manifests/tests | `viewer_manifest_v*.py`, `viewer_package.py`, `transform.py`, tests |
| WS2 Perf telemetry | append-only events/audit | `runtime_budget.py`, pipeline, audit scripts |
| WS3 Cache unification | source signature/cache routing | `cache_paths.py`, `dwg_differ.py`, `zone_*`, `scene_pack_builder.py` |
| WS4 PDF-first GUI | viewer status/fallback UX | `drawing_compare_workbench.py`, `lightweight_viewport.py` |
| WS5 Visual backend interface | converter abstraction | new backend modules, worker |
| WS6 Zone render stability | crop/prewarm/cache/fallback | `zone_render_service.py`, `zone_render_worker.py` |
| WS7 Multi-sheet accuracy | region detection/matching | `sheet_region_detector.py`, `detail_region_matcher.py`, `region_compare_pipeline.py` |
| WS8 QA/release | gates and datasets | `scripts/*audit*`, `scripts/*benchmark*`, docs |

## Go / No-Go Gates

No-Go if any of these remain true:

- viewer can become blank after a render/vector failure;
- PDF pair is labeled as generic preview failure;
- same source is converted to DXF more than once per run without reason;
- CAD/PDF/pixel bbox space is ambiguous in manifest;
- selected-zone cache hit p95 exceeds 2s on normal cases;
- large run has no RSS/elapsed diagnostic evidence;
- customer build enables unapproved AGPL/GPL/ODA backend automatically.

Go when:

- blank viewer count = 0 across pilot corpus;
- first-review-ready budget is met;
- cache hit and duplicate conversion metrics are visible;
- coordinate round-trip tests pass;
- multi-sheet ambiguous cases require review rather than auto-mismatch;
- release audit and path leakage audit pass.

## Immediate Next Step

Start with R1 and R2 before adding a CAD-to-PDF converter. Without coordinate
contracts and perf instrumentation, PDF-first work can hide accuracy bugs or
move the performance problem into a new renderer.

Recommended first implementation sequence:

1. R1 coordinate contract tests.
2. R2 `perf_events.jsonl` minimal writer.
3. R4 source/sidecar PDF-first viewer status fix.
4. R3 source signature cache consolidation.
5. R7 selected-zone crop/persistent worker stabilization.
6. R5 converter interface and backend PoC.
