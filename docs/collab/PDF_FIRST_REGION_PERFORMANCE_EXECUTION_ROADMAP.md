# PDF-First Region Compare Performance Execution Plan

Last updated: 2026-05-27

Status: planning baseline for R6-B and later implementation

Related performance-degradation planning:

- `docs/collab/DRAWING_COMPARE_PERFORMANCE_DEGRADATION_TECHSPEC_ROADMAP.md`
  expands this roadmap into a dedicated slowdown/memory/benchmark work plan
  covering multi-agent findings, hot-path bottlenecks, telemetry fields,
  acceptance gates, and sequential work packages P0-P8.

## 0. Korean Executive Summary

핵심 결론:

- PDF-first는 맞는 방향이지만, CAD를 PDF로 바꿔서 비교의 기준으로
  삼는 방식은 금지한다. CAD/DWG/DXF는 엔티티 비교를 truth layer로
  유지하고, PDF/sidecar PDF/CAD-to-PDF 결과물은 viewer 안정화를 위한
  visual asset layer로만 사용한다.
- 현재 체감 성능 저하의 핵심은 비교 알고리즘 하나가 아니라 첫 결과
  표시, 도면 선택, overlay/tree 생성, tile/cache I/O, zone render,
  perf telemetry, runtime sampler가 한 번에 얽힌 구조다.
- 다음 구현은 R6-B부터 시작한다. 먼저 `viewer_perf` I/O와
  `RuntimeBudgetSampler` overhead를 줄이고, first-review-ready와
  package-complete를 분리해야 한다.
- 다중 도곽 문제는 R8의 핵심이다. whole-modelspace fallback은
  자동 비교로 쓰면 안 되고, 도곽 후보가 있으면 ambiguous review로
  보내야 한다.
- 완료 판정은 단위 테스트 통과가 아니라 실도면 realset, screenshot,
  region match evidence, latency/RSS/perf event evidence, rollback gate가
  모두 통과했을 때만 가능하다.

즉시 실행 순서:

1. R6-B: 성능 계측 자체의 병목 제거 및 first-result split.
2. R6-C: 첫 도면 선택 시 top issues/first-N만 즉시 표시하고 전체
   overlay/tree/tile 작업은 lazy 처리.
3. R7: 선택구역 crop-first renderer와 structured fallback 구축.
4. R8: 다중 도곽 detection/matching을 spatial index와 review gate로
   재구성.
5. R9/R10: large CAD 성능/메모리와 realset release gate로 완료 검증.

## 1. Current Judgement

The current implementation is not ready to be judged as complete for the user
problem.

Recent unit and focused regression coverage is useful, but it does not prove
that real multi-detail drawings are solved. The latest real-run evidence still
shows the critical failure pattern:

- only one detected region per source in a multi-detail scenario;
- `auto_matched=0`;
- unmatched before/after regions;
- `region_local_not_enabled`;
- a large viewer bbox mismatch between the two sides.

Therefore the correct next stage is not to enable more rendering by default.
The next stage is to separate first result visibility from expensive follow-up
work, remove performance regressions introduced by diagnostics/viewer work, and
then harden PDF-first visual assets and multi-region matching behind measurable
gates.

## 2. Multi-Agent Review Synthesis

Three focused planning agents reviewed the repository:

| Agent focus | Main conclusion |
| --- | --- |
| Performance | R6-B/R6-C must split first result display from full export/render work. GUI selection, tile generation, telemetry I/O, and runtime sampling are current performance risks. |
| PDF-first architecture | PDF-first is the correct viewer strategy, but PDF must remain a visual layer. CAD entity comparison remains the truth layer for CAD inputs. |
| QA and release gates | Current status is partially implemented. Realset, screenshot, region-match, and performance evidence are still missing before default enablement. |

Additional code-review notes identified two immediate high-confidence
performance problems:

- `viewer_perf.json` is read and rewritten for every viewer event.
- `RuntimeBudgetSampler` can recursively scan the full output directory every
  100 ms, so diagnostics can become a runtime bottleneck on large runs.

## 3. Product Principles

1. No blank viewer.
   The viewer must show a PDF background, sidecar visual asset, raster fallback,
   skeleton fallback, relative pins, or a structured source-open action.

2. CAD truth and visual fidelity are separate.
   DWG/DXF comparison should remain entity-based. PDF/PNG/WebP assets are used
   for inspection and UX unless the input itself is PDF.

3. First review is a first-class deliverable.
   A user-visible pair list, top issues, and an initial preview must not wait
   for all exports, marked PDFs, full tiles, all overlays, or region sidecars.

4. Diagnostics must not create the slowdown they measure.
   Runtime sampling, perf events, summary refreshes, and manifest updates must
   have bounded I/O and bounded memory.

5. Multi-detail comparison must be review-gated.
   Whole-modelspace fallback must not silently auto-compare when frame or
   region candidates exist. Ambiguous matching goes to review.

## 4. Target Architecture

```text
Input DWG/DXF/PDF
  -> Truth layer
     - CAD input: CAD canonical/entity diff
     - PDF input: visual/text/OCR diff
  -> Visual asset layer
     - source PDF
     - sidecar PDF
     - approved CAD->PDF artifact
     - raster fallback
     - skeleton/relative-only fallback
  -> Region layer
     - frame/title/page detection
     - one-to-one region matching
     - manual override/review
     - region-local compare only after approval
  -> Viewer layer
     - first result/top issues first
     - PDF/page background before tile pyramid
     - lazy overlays/zones/tiles
     - selected-zone crop before vector focus
```

## 5. CAD-to-PDF Policy

CAD-to-PDF conversion is allowed only as a visual asset backend. It must not be
treated as the comparison truth for CAD inputs.

Default order:

1. source PDF;
2. sidecar PDF;
3. approved CAD-to-PDF backend;
4. approved CAD-to-image backend;
5. current raster fallback;
6. skeleton or relative-only pins.

CAD-to-PDF backends remain disabled by default until all of these gates pass:

- explicit backend allowlist;
- `backend_id`, `backend_version`, `license_id`, and provenance in every visual
  manifest;
- subprocess-only execution with timeout, cancel, and bounded temp dirs;
- no shell string invocation;
- output PDF validation;
- cache key includes backend, plot profile, layout/page, DPI, coordinate
  contract version, and source signature;
- legal review for any commercial, GPL, AGPL, cloud, or bundled rendering
  dependency.

Forbidden designs:

- enabling a converter through environment variables alone;
- running conversion in the GUI thread or selection hot path;
- using CAD-to-PDF output as CAD diff truth;
- showing estimated transform overlays as exact;
- clearing an existing PDF/raster background because vector focus failed;
- uploading drawings to a cloud converter without explicit approval and audit
  evidence.

## 6. Performance Degradation Model

The following bottlenecks must be tracked as first-class risks:

| Risk | Current symptom | Required direction |
| --- | --- | --- |
| First result hidden behind full pipeline completion | Worker emits only after `pipeline.run()` finishes | add first-result-ready event/result path |
| Pair selection triggers full preview/tile work | selection can start background render and tile generation | PDF/page background first, defer full tiles |
| Overlay and zone UI materializes too much | full overlay JSON cached per pair, full tree built in GUI thread | top-issues/first-N initial tree, idle rebuild, bounded cache |
| Zone selection fan-out | crop, vector focus, lightweight focus can run together | one orchestrator, crop first, vector later |
| Viewer perf event I/O | read-modify-write JSON per event | append-only JSONL or bounded write buffer |
| Perf summary refresh | repeated full JSON read and list materialization | streaming single-pass summary |
| Runtime sampler overhead | recursive output scan every sample tick | throttle spool scan and narrow directories |
| Tile manifest updates | legacy full JSON merge during selection | JSONL append and one final materialization |
| DXF candidate explosion | post-hoc truncation after large accumulation | candidate edge telemetry and earlier caps |
| Region detection scaling | candidate frame x entity scans | spatial index/grid queries and hard caps |

## 7. Required Tech Spec Updates

### 7.1 Visual Asset Manifest

Every viewer background or fallback asset must record:

```text
asset_id
asset_kind = source_pdf | sidecar_pdf | cad_pdf | cad_image | raster_fallback | skeleton | relative_only
source_signature
source_path_redacted
backend_id
backend_version
backend_license_id
plot_profile_hash
layout_name
page_index
dpi
page_size_pt
pixel_size
coordinate_contract_version
transform_quality = exact | estimated | relative_only | unavailable
status = ready | deferred | failed | license_blocked | timeout | unavailable
reason_code
created_at
cache_key_hash
```

Viewer code must refuse to treat an asset as exact if `transform_quality` is
not `exact`.

### 7.2 Perf Event Contract

Use append-only run-level `perf_events.jsonl` and viewer-level
`viewer_perf.jsonl` for high-frequency events.

Required fields:

```text
run_id
pair_id
region_id
stage
event
elapsed_ms
queue_wait_ms
gui_block_ms
rss_mb
working_set_mb
spool_mb
input_bytes
entity_count
overlay_count
materialized_overlay_count
candidate_edge_count
cache_namespace
cache_key_hash
cache_hit
render_mode
fidelity
reason_code
```

High-frequency events must be sampled, batched, or append-only. They must not
rewrite a large JSON document on every pan, zoom, crop, or selection event.

### 7.3 Runtime Sampler

The sampler must separate memory sampling from disk spool scanning:

- memory interval: 100 ms is acceptable;
- spool scan interval: default 1-2 seconds;
- final spool scan at stop;
- `os.scandir()`-style traversal preferred over repeated `Path.rglob()`;
- GUI should pass the narrowest spool dirs, not the entire output directory;
- sampler overhead target: p95 sample tick <= 20 ms and total runtime overhead
  <= 2%.

### 7.4 GUI First Selection

Initial pair selection must avoid full materialization:

- use `top_issues` first when present;
- otherwise cap initial zones to `GUI_FIRST_SELECTION_ZONE_LIMIT`, initially
  500;
- schedule full tree rebuild with `QTimer.singleShot` or equivalent idle work;
- cancel stale rebuilds when active pair changes;
- defer first zone auto-selection until the preview has a chance to paint;
- skip full LOD tile generation in first-review mode.

### 7.5 Zone Render Orchestrator

Zone selection must be serialized through one request coordinator:

1. show selected bbox/pin immediately;
2. crop from existing PDF/tile/background;
3. use PDF display-list crop if licensed and available;
4. use cached raster crop;
5. use CAD/vector focus only as deferred enhancement;
6. return visible fallback with reason code on timeout/failure.

Stale zone requests must be cancelled or ignored. Cache keys must include source
signature, pair id, page/layout, bbox, DPI/profile, backend, and transform
quality.

### 7.6 Multi-Detail Scaling

Frame detection and matching must avoid unbounded cross-products:

- build a spatial index over entities before frame-inside queries;
- record `candidate_frame_count`, `inside_query_count`,
  `virtual_insert_expansion_count`, and `dropped_candidate_count`;
- cap candidate frames and mark overflow as review-required;
- use one-to-one matching with ambiguity margin;
- block auto match if drawing numbers conflict;
- whole-modelspace fallback is allowed only when no frame/region candidates
  exist.

## 8. Roadmap

### R6-B. Performance Telemetry and First-Result Split

Goal: make first review visible quickly and stop diagnostics from slowing the
program.

Primary tasks:

- add a first-result-ready event/result path before deferred exports;
- make GUI distinguish `review_ready` from `package_complete`;
- convert viewer perf writes to append-only or bounded writes;
- make viewer perf summary streaming/single-pass;
- throttle runtime spool scanning and narrow GUI sampler dirs;
- add benchmark fixtures for 1k/10k/100k overlay event workloads.

Files:

- `src/gui/drawing_compare_workbench.py`
- `src/services/comparison/folder_compare_pipeline.py`
- `src/services/comparison/viewer_tile_cache.py`
- `src/services/comparison/viewer_perf_summary.py`
- `src/services/comparison/runtime_budget.py`
- `tests/unit/services/comparison/test_viewer_tile_cache.py`
- `tests/unit/services/comparison/test_viewer_perf_summary.py`
- `tests/unit/services/comparison/test_runtime_budget.py`

Acceptance:

- first result list is available before deferred export artifacts finish;
- normal fixture first result p95 <= 30 s;
- large fixture first result p95 <= 120 s, hard cap <= 300 s;
- viewer perf append p95 <= 5 ms on 1000 event workload;
- sampler overhead <= 2%;
- spool scan p95 <= 20 ms when throttled;
- no full output-tree scan every 100 ms.

### R6-C. Lazy Viewer Selection and Bounded GUI Memory

Goal: selecting a drawing must not load or generate everything.

Primary tasks:

- top-issues or first-N initial zone tree;
- idle full tree rebuild with stale-pair guard;
- bounded overlay cache with eviction metrics;
- avoid selection-time full tile pyramid;
- use PDF page/background first when available;
- record pair selection latency and GUI block time.

Acceptance:

- cached PDF pair selection p95 <= 300 ms;
- cold PDF pair selection p95 <= 2 s;
- selecting a pair does not call full tile pyramid generation in first-review
  mode;
- 100 pair / 100k overlay navigation does not show linear RSS growth after
  cache limit is reached.

### R7. Selected-Zone Render Stabilization

Goal: selected-zone view never becomes a repeated blank/failure loop.

Primary tasks:

- implement zone render orchestrator;
- crop from existing visual assets before vector work;
- persistent worker/prewarm for active pair;
- cache by source signature and bbox profile;
- structured fallback reason codes;
- rapid-click stale request tests.

Acceptance:

- cached crop p95 <= 500 ms;
- cold PDF/image crop p95 <= 2 s;
- CAD source crop either succeeds within 10 s or shows visible fallback;
- blank selected-zone failure count = 0;
- reason codes appear in viewer perf summary and UX.

### R8. Multi-Detail Region Detection, Matching, and Review Gate

Goal: files with multiple drawings compare matching regions only.

Primary tasks:

- spatial-index frame candidate scoring;
- title/drawing number extraction from text, attributes, block metadata, and
  PDF visual clues when available;
- one-to-one matching with ambiguity margin;
- manual override persistence;
- review UI for ambiguous/whole fallback cases;
- region-local compare only for approved matches.

Acceptance:

- synthetic multi-frame CAD expected region detection = 100%;
- pilot realset region detection >= 80%, target >= 90%;
- user-reviewed match accuracy >= 95%;
- whole-modelspace auto-compare count = 0 when candidates exist;
- moved same sheet does not create false large diff.

### R9. Large CAD Memory and Comparator Scaling

Goal: make large drawings predictable instead of merely survivable.

Primary tasks:

- add candidate edge and cluster-size telemetry;
- reduce post-hoc truncation by top-N/streaming retention earlier;
- bound worker concurrency by estimated bytes/entity/RSS;
- detect duplicate source conversions in one run;
- benchmark near-match explosion fixtures.

Acceptance:

- 10 MB / 100k synthetic completes <= baseline * 1.15;
- 50 MB synthetic passes or exits with explicit timeout/limit reason;
- peak RSS <= 2 GB or <= current realset baseline + 10%;
- duplicate DWG/DXF conversion for same source signature = 0 without reason.

### R10. Realset QA, Release Gate, and Rollback

Goal: enable defaults only with customer-grade evidence.

Required corpus:

- CAD pairs >= 8;
- PDF pairs >= 8;
- large DWG pairs >= 2;
- multi-detail pairs >= 4;
- raster/low-quality PDF pairs >= 2;
- negative controls >= 2.

Acceptance:

- `validate_multi_detail_region_compare.py` overall status is passed;
- screenshots prove region viewer and PDF-first viewer for at least three
  representative cases;
- path leakage = 0;
- non-approved visual backends disabled;
- rollback flags tested for region-local and CAD-to-PDF paths.

### R11. Optional CAD-to-PDF Backend Lab

Goal: evaluate one converter safely without making it customer default.

Primary tasks:

- choose one backend for internal lab only;
- add adapter behind allowlist;
- subprocess timeout/cancel/output validation;
- compare visual fidelity against source/sidecar PDF baseline;
- legal and redistribution review.

Acceptance:

- customer build still defaults to source/sidecar PDF only;
- backend is disabled unless explicitly allowed;
- failed conversion degrades to existing visible fallback;
- provenance and license fields are present in every manifest.

## 9. Execution Order

Recommended next implementation order:

1. R6-B runtime sampler throttling and viewer perf JSONL/bounded writes.
2. R6-B first-result-ready split.
3. R6-C top-issues/first-N initial zone tree and deferred full rebuild.
4. R6-C disable first-review selection-time full tile generation.
5. R7 zone render orchestrator and crop-first fallback.
6. R8 spatial-indexed multi-detail detection and review-gated matching.
7. R9 comparator/memory hardening.
8. R10 realset gate.
9. R11 optional CAD-to-PDF lab.

This order is intentional. It prevents the planning work from making the
program slower while trying to make rendering more reliable.

## 10. Gate Commands

Core regression:

```powershell
python -m pytest tests\unit\services\comparison\test_viewer_tile_cache.py tests\unit\services\comparison\test_viewer_perf_summary.py tests\unit\services\comparison\test_runtime_budget.py -q -o log_cli=false
python -m pytest tests\unit\services\comparison\test_folder_compare_pipeline.py tests\unit\services\comparison\test_viewer_package.py -q -o log_cli=false
python scripts\cad_policy_gate.py
```

Region gate:

```powershell
python -m pytest tests\unit\services\comparison\test_region_aware_compare.py tests\unit\services\comparison\test_region_profile.py tests\unit\services\comparison\test_region_match_overrides.py -q -o log_cli=false
python scripts\validate_multi_detail_region_compare.py --input <pilot-manifest.json> --output build\multi-detail-pilot
```

Performance gate:

```powershell
python scripts\benchmark_viewer_build.py --fixture large --runs 3
python scripts\benchmark_zone_render.py --fixture large --runs 3
python scripts\cad_performance_benchmark.py --fixture synthetic_100k --runs 3
```

Release gate:

```powershell
python scripts\audit_drawing_compare_mvp_exit.py --strict-zone-render-budget --require-runtime-budget --require-dataset-composition
python scripts\cad_policy_gate.py
```

## 11. Completion Standard

Implementation is complete only when all of the following are true:

- multi-detail regions are detected;
- the same logical drawing is matched or sent to review;
- ambiguous matches do not auto-compare;
- approved matches produce region-local primary output;
- viewer shows PDF/visual background or a visible fallback, never blank;
- selected-zone crop has bounded latency and structured fallback;
- first result visibility is measured separately from package completion;
- performance telemetry overhead is bounded;
- realset evidence and screenshots exist;
- rollback flags are tested.

Sidecar files, completed runs, and unit-test pass counts are not sufficient by
themselves.
