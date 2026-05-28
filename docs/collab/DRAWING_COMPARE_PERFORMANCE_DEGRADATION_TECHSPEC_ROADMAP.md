# Drawing Compare Performance Degradation Tech Spec and Roadmap

Last updated: 2026-05-28

Status: planning baseline for performance-degradation hardening

Current planning decision, 2026-05-27:

- 성능 저하는 렌더러 버그의 부수효과가 아니라 별도 P0 품질 축으로
  관리한다.
- PDF-first 전환은 UX 안정화 수단이지만, 측정/캐시/취소/메모리 예산 없이
  먼저 켜면 새 병목을 만든다.
- malformed DXF read smoke는 개선됐지만, multi-detail baseline은 아직
  완료로 보지 않는다. 현재 필요한 것은 진짜 다중 도곽 fixture, region
  sidecar contract, DWG cached-DXF region-local compare gate, 성능 예산
  증거다.

Current planning refinement, 2026-05-28:

- 합성 benchmark 통과와 고객 도면 성능 안정성은 별도 증거로 판정한다.
  실제 20-50장 고객 승인 corpus에서 latency, RSS slope, cache cap, blank
  count, stale result, tile/orphan payload가 같은 packet 안에서 통과해야
  완료로 본다.
- PDF-first는 viewer 안정화 전략이다. CAD/DWG/DXF 비교 truth를 PDF
  visual output으로 대체하지 않는다.
- 성능저하 검증은 unit test가 아니라 benchmark profile, real-corpus
  replay, 30분 soak, screenshot/nonblank, runtime/viewer telemetry, release
  audit, closeout readiness audit를 함께 요구한다.
- `--allow-missing-psutil` 같은 measurement bypass는 smoke 전용이며,
  customer-grade release gate에서는 실패로 취급한다.

## 0. Korean Executive Summary

사용자가 체감한 "이전보다 너무 느림", "비교 결과가 늦게 또는 전혀 안 보임",
"선택구역 렌더 실패", "벡터 렌더 실패로 빈 화면"은 하나의 버그가 아니다.
현재 구조에서는 비교, viewer package, PDF/raster background, overlay JSON,
tile pyramid, selected-zone crop, vector focus, diagnostics가 한 선택 동작에
묶일 수 있다. 따라서 성능 개선은 단순히 렌더러 하나를 바꾸는 것이 아니라
hot path와 deferred path를 분리하는 작업이어야 한다.

핵심 결정:

- CAD/DWG/DXF 비교의 truth layer는 기존 canonical/entity diff를 유지한다.
- PDF/sidecar PDF/CAD-to-PDF는 viewer 안정성을 위한 visual asset layer로
  사용한다.
- first-review-ready는 package-complete와 분리한다.
- 도면 선택은 top issues 또는 first-N만 즉시 표시하고 full overlay/tree/tile은
  idle/lazy 처리한다.
- selected-zone은 crop-first orchestrator로 직렬화한다. vector SVG는 보조
  enhancement이며 실패해도 배경을 지우면 안 된다.
- 성능 게이트는 unit test가 아니라 realset, benchmark, screenshot, nonblank
  pixel check, RSS/latency telemetry로 판정한다.

## 1. Multi-Agent Synthesis

세 개의 읽기 전용 검토 에이전트를 사용했다. 2026-05-27 추가 기획
라운드에서는 같은 축을 더 세분화해 성능/메모리, 렌더링/PDF 전환,
QA/릴리즈 게이트 관점으로 재검토했다.

| Agent focus | Main finding |
| --- | --- |
| Performance/memory | Viewer package, PDF full-page render, LOD tile generation, GUI selection fan-out, overlay/tile JSON I/O, cold CAD/vector render cache가 주요 병목이다. |
| PDF-first architecture | PDF-first는 visual layer로 옳지만 CAD truth를 대체하면 안 된다. converter provenance, license, coordinate quality, output validation이 필수다. |
| Test/benchmark/release | 테스트는 많지만 실제 느려짐을 막는 장기 baseline, soak benchmark, pixel-level nonblank verification, realset gate가 부족하다. |

추가로 이전 R6-C 검토에서 확인된 사항:

- fast-first-review에서도 top-issue background 일부는 eager render될 수 있다.
- selection-time full LOD tile pyramid는 first-review mode에서 금지해야 한다.
- `_on_drawing_selected_v2()`는 overlay/tree 일부를 줄였지만 PDF/raster load,
  ViewerSession, pair render, category/tree update가 여전히 동기 또는 즉시
  fan-out될 수 있다.

추가 냉정 결론:

- `dxf_read` tolerant path는 malformed `LWPOLYLINE` read 실패를 줄였지만,
  성능 저하나 multi-detail 정확도를 직접 해결하지 않는다.
- DWG 입력은 cached DXF가 있어도 자동 region-local compare가 직접 `.dxf`
  pair만 허용하면 건너뛰어질 수 있다. 이 게이트는 correctness와 성능
  양쪽의 blocker다.
- 렌더 실패를 PDF 변환으로 덮는 방식은 background blank를 줄일 수 있지만,
  conversion provenance, cache key, page/region coordinate contract가 없으면
  잘못된 위치에 변경 마커가 찍히는 UX 회귀가 된다.

## 2. Problem Taxonomy

### 2.1 Result Visibility

Symptom:

- 비교가 끝난 것처럼 보이는데 결과 목록이 안 나온다.
- viewer package/export가 오래 걸리면 사용자는 "비교 실패"로 느낀다.

Root causes:

- 비교 완료와 검토 가능 결과 표시가 같은 완료 이벤트에 묶여 있었다.
- full viewer package, marked PDF, full tiles, compare state export가 first
  review보다 먼저 실행될 수 있다.

Current mitigation:

- `FolderCompareRunResult.result_state`가 `review_ready`와 `package_complete`를
  구분한다.
- GUI가 partial result를 받을 수 있는 callback 경로를 갖기 시작했다.

Remaining risk:

- real GUI에서 결과 목록과 첫 top issue가 실제로 몇 초 안에 표시되는지
  자동 E2E gate가 없다.

### 2.2 Pair Selection Latency

Symptom:

- 도면 한 쌍을 선택하면 GUI가 멈추거나 늦게 반응한다.

Root causes:

- full overlay JSON read/parse.
- full zone tree rebuild and classification.
- tile manifest read/write.
- lightweight PDF/raster load.
- ViewerSession scene pack, pair preview render, zone auto-selection이 같은
  사용자 동작에서 이어질 수 있다.

Current mitigation:

- initial overlays use top issues or first `GUI_FIRST_SELECTION_ZONE_LIMIT`.
- full tree rebuild is scheduled with an idle timer and stale pair guard.
- overlay cache is bounded by pair count.
- first zone selection is delayed until after paint.
- first-review selection can skip LOD tile generation.

Remaining risk:

- cache limit is pair-count based, not bytes based.
- PDF page load and raster load can still occur on the GUI thread.
- category computation and tree build for the initial subset can still be visible
  on very heavy rows.

### 2.3 Viewer Package and Tile Explosion

Symptom:

- large PDF/DWG runs spend excessive time building viewer artifacts.
- output folder and RSS grow quickly.

Root causes:

- `export_viewer_package()` groups and materializes all zones for pair overlays.
- `_render_pdf_to_png()` creates full-page pixmaps.
- `_write_image_pyramid()` holds full RGB image and resized copies while writing
  tile pyramids.
- overlay tile buckets and manifest JSON can be repeatedly materialized.

Current mitigation:

- fast-first-review caps rendered pages, overlay records, zone tiles, and LOD
  tile generation.
- viewer perf logging moved to append-only JSONL.

Remaining risk:

- normal mode can still materialize all overlays and full tiles.
- tile manifest merging still has JSON rewrite paths.
- no 100-pair / 100k-overlay navigation soak gate exists yet.

### 2.4 Selected-Zone Render Failure and Slowdown

Symptom:

- 선택구역 렌더 실패.
- vector render failure makes the selected-zone view blank or confusing.
- cold zone render is very slow on large DXF/DWG.

Root causes:

- crop, vector focus, lightweight focus can run together.
- vector SVG path hits fragile CAD entities such as block, hatch, text,
  dimensions, multileader, proxy objects.
- CAD render index cold start can reopen and reprocess large DXF.
- fallback reason is not always tied to a visible stable background.

Current mitigation:

- selected-zone fallback work has started, including relative overlay fallback
  and reason propagation.

Required direction:

- One zone render orchestrator.
- Existing PDF/tile/background crop first.
- PDF DisplayList crop second.
- cached raster crop third.
- CAD/vector focus only as deferred enhancement.
- visible fallback with reason code on failure.

### 2.5 PDF-First Conversion and Accuracy Risk

Symptom:

- direct CAD render fails frequently.
- converting drawings to PDF could improve visual reliability, but can also
  break accuracy if treated as truth.

Root causes:

- DWG direct support is limited.
- DXF fast render intentionally skips or degrades heavy entities.
- CAD-to-PDF backends have license, cloud, reproducibility, plot profile, and
  coordinate alignment risks.

Policy:

- CAD-to-PDF is visual asset only.
- CAD entity diff remains authoritative for CAD inputs.
- visual asset exactness must be declared in manifests.

### 2.6 Performance and Memory Risk Matrix

이 항목은 "느리다"를 단일 증상으로 보지 않고, 어디서 느려졌는지 증명하기
위한 진단 축이다.

| Risk | Hot path | Why it can regress | Required evidence |
| --- | --- | --- | --- |
| Full package before first review | `FolderComparePipeline.run()`, `export_viewer_package()` | 비교 완료, viewer export, render, tile, marked PDF가 같은 완료감으로 보이면 결과가 안 나온 것처럼 보인다. | `first_review_ready_s`, `package_complete_s`, ready/deferred outputs |
| GUI selection fan-out | `drawing_compare_workbench.py` pair/zone selection handlers | 한 클릭이 overlay parse, tree rebuild, PDF/raster load, preview render, zone auto-select를 연쇄 실행한다. | `gui_block_ms`, initial overlay count, full tree deferred flag |
| Full-page PDF/raster cost | `viewer_package._render_pair_backgrounds()`, PDF background render | PDF-first가 full-page pixmap storm으로 바뀌면 안정성 대신 지연과 RSS 증가를 만든다. | per-page render ms, pixel count, estimated image bytes, cache hit |
| Selected-zone duplicate render | `zone_render_service.render_zone_pair()` | crop, DisplayList, PIL fallback, CAD/vector focus가 중복 실행되면 cold path가 급격히 늦어진다. | orchestrator request id, stale/cancel count, backend chosen |
| CAD render index retention | `zone_render_service._INDEX_CACHE` | entity envelope, bbox cache, ezdxf doc/modelspace가 entry 단위로 오래 남아 RSS가 계단식 상승할 수 있다. | cache entry count, entity_count-weighted bytes, eviction reason |
| PDF DisplayList retention | `pdf_display_list_cache._DISPLAY_LIST_CACHE` | PyMuPDF DisplayList가 page/doc reference를 잡고 있어 capacity가 byte-aware가 아니면 파일 핸들/RSS가 남을 수 있다. | displaylist cache entries, page bytes estimate, close/evict count |
| Runtime telemetry overhead | `runtime_budget._directory_size()`, `perf_events.summarize_perf_events()` | 측정기가 output tree scan 또는 JSONL list materialization으로 병목을 만들 수 있다. | sampler overhead ms, spool scan interval, summary input bytes |
| Tile/overlay materialization | `viewer_tile_cache.write_pair_tile_cache()` | image pyramid와 overlay buckets가 first selection 또는 package time에 대량 생성된다. | tile_count, overlay_tile_count, materialized_overlay_count |
| Region detection scaling | `sheet_region_detector` frame/line/text scans | frame 후보 x entity/title query가 커지면 다중 도곽 감지가 비교보다 비싸질 수 있다. | candidate_count, candidate_edge_count, query mode, entity cap |
| DWG cached-DXF gate miss | `folder_compare_pipeline._build_auto_region_compare_payload()` | cached DXF가 있어도 source suffix가 DWG면 region-local compare가 skip될 수 있다. | unsupported_pair_count, resolved_dxf_source fields |

Memory leak definition for this roadmap:

- 누수는 Python object leak만 의미하지 않는다. cache가 의도대로 비워지지
  않거나, subprocess/temp/page assets가 해제되지 않아 작업 반복마다 working
  set 또는 spool이 단조 증가하면 같은 등급의 결함으로 본다.
- 합격 기준은 "한 번 빠르다"가 아니라 100-pair navigation soak, rapid
  zone-click soak, repeated compare run에서 p95와 RSS 기울기가 제한 안에
  들어오는 것이다.

### 2.7 Current Code Hotspots To Investigate First

성능/메모리 에이전트가 지목한 우선순위다. 구현자는 이 목록을 P0/P1
계측 포인트로 먼저 삼아야 한다.

| Priority | Hotspot | Why it matters | Required next action |
| --- | --- | --- | --- |
| P0 | `drawing_compare_workbench.py` pair and zone selection | 한 번의 선택이 overlay load, preview/session, full tree rebuild, pair render, initial zone selection, crop/vector render를 연쇄 실행한다. | request id, GUI block ms, spawned worker count, stale result count 계측 후 fan-out 축소 |
| P0 | `viewer_tile_cache._write_image_pyramid()` and `_write_overlay_tiles()` | 원본 이미지를 통째 RGB 변환하고 모든 level/tile/overlay bucket을 eager materialize한다. | first-review path에서 금지하고 visible-first/lazy/background export로 이동 |
| P0 | `dxf_comparator.compare()` candidate accumulation | added/deleted/cosmetic/change와 near-match 후보가 먼저 누적되고 뒤늦게 truncate될 수 있다. | pre-materialization cap, streaming spool, candidate edge count budget |
| P0 | `dxf_entity_extractor.extract()` and tolerant read | all layouts, block expansion, repaired DXF string이 list/string으로 중복 materialize될 수 있다. | block expansion generator, repair memory budget, large-file streaming repair 검토 |
| P0 | `zone_vector_renderer` and `zone_render_worker` | zone마다 DXF read/cache/primitive materialization이 반복될 수 있어 first selected zone이 매우 느려진다. | vector는 deferred enhancement로 두고 crop-first orchestrator를 primary로 승격 |
| P1 | `zone_render_service._INDEX_CACHE` | entry count 기준 캐시가 ezdxf doc/modelspace/bbox cache/envelopes를 byte cap 없이 보관한다. | entity/byte weighted LRU, explicit eviction telemetry |
| P1 | `sheet_region_detector` and localized compare | candidate마다 entity/title scans와 region-local entity map 복사가 반복될 수 있다. | spatial index, candidate cap, approved-region-only compare |
| P1 | `viewer_perf` and `perf_events` summaries | telemetry JSONL append, pointer JSON write, full list materialization, GUI refresh가 측정 대상 자체를 느리게 할 수 있다. | streaming summary, sampled refresh, telemetry overhead gate |

## 3. Performance Tech Spec

### 3.1 Unified Perf Event Fields

Every high-value path should emit append-only events with these fields when
available:

```text
run_id
pair_id
zone_id
region_id
stage
event
elapsed_ms
queue_wait_ms
gui_block_ms
rss_mb
peak_rss_mb
working_set_mb
spool_mb
input_bytes
entity_count
page_count
pair_count
zone_count
overlay_count
materialized_overlay_count
candidate_edge_count
tile_count
overlay_tile_count
cache_namespace
cache_key_hash
cache_hit
cache_miss_reason
dpi
pixel_width
pixel_height
estimated_image_bytes
render_backend
render_mode
fidelity
reason_code
```

High-frequency events must be append-only, sampled, batched, or summarized.
They must never rewrite a growing JSON document per pan, zoom, selection, or
crop.

### 3.2 Stage Instrumentation

Required measurement points:

| Stage | Code area | Required metrics |
| --- | --- | --- |
| Pipeline | `FolderComparePipeline.run()` | stage elapsed, first-review-ready ms, package-complete ms, peak RSS, spool MB, pair/zone/page counts |
| Viewer package | `export_viewer_package()` | pair loop elapsed, overlay JSON bytes/time, render ms, tile ms, tile count, cache hit, RSS before/after |
| PDF display | `load_pdf_page()` / `render_page()` | QPdf open ms, render ms, save ms, prune ms, cache hit, pixel dimensions |
| Tile cache | `_write_image_pyramid()` / `_write_overlay_tiles()` | level count, tile count, generated bytes, max bucket size, elapsed |
| GUI pair selection | `_on_drawing_selected_v2()` | initial source, initial overlay count, GUI block ms, full-tree deferred, spawned workers |
| Full tree rebuild | `_run_full_zone_tree_rebuild_v2()` | overlay count, visible count, tree item count, elapsed |
| Zone selection | `_on_zone_selected_v2()` and orchestrator | request id, stale/cancel count, crop ms, vector ms, fallback reason |
| CAD/vector cache | `zone_render_service`, `zone_vector_renderer` | index build ms, cache entries, visible entity count, cold/hit split |
| Region-local | `region_compare_pipeline`, `region_viewer_package` | region count, matched count, localized compare count, region viewer eager render count |

### 3.3 Runtime Sampler Contract

- memory sample interval: 100 ms acceptable.
- spool scan interval: default 1-2 seconds.
- final spool scan at sampler stop.
- use narrow spool dirs, not the whole output root when possible.
- use `os.scandir()` traversal.
- sampler tick p95 <= 20 ms.
- total telemetry overhead <= 2%.

### 3.4 Cache Contract

Use one source signature across compare, preview, viewer, zone, and region:

```text
source_signature = hash(
  stable_source_id_or_resolved_path,
  file_size,
  mtime_ns,
  optional_head_tail_hash,
  importer_version,
  render_backend_id,
  plot_profile_hash,
  config_fingerprint
)
```

Cache namespaces:

- `compare_dxf`
- `preview_dxf`
- `scene_pack`
- `visual_asset_pdf`
- `visual_asset_tile`
- `page_descriptor`
- `pdf_display_list`
- `zone_crop`
- `zone_focus`
- `region_detection`
- `region_viewer`

Acceptance:

- repeated run on same source should not duplicate DWG/DXF conversion without a
  recorded reason.
- GUI overlay cache should be bounded by estimated bytes or overlay count, not
  only pair count.
- tile manifest should move toward append/materialize, not repeated full JSON
  rewrite in selection hot paths.

### 3.5 Performance Budget Tiers

Budgets are intentionally split by rollout stage. P0 freezes current evidence;
P1 makes missing measurement fail; P2 and later optimize.

| Tier | Scope | Hard fail | Soft warn |
| --- | --- | --- | --- |
| P0 evidence | Baseline/inventory only | Missing `validation_summary.json`, runtime budget, viewer perf, selected-zone evidence, nonblank/screenshot evidence | Single fixture only, synthetic-only evidence |
| P1 instrumentation | Every release-gate run | Missing `perf_events_summary.json`, missing `runtime_budget`, telemetry overhead > 2% | Event fields incomplete for a stage |
| P2 GUI hot path | Pair click / first paint | cached pair selection p95 > 300 ms, cold p95 > 2 s, GUI block > 100 ms | full tree deferred but visible delay > 500 ms |
| P3 PDF visual path | PDF/page render and crop | selected-zone PDF cold p95 > 2 s, hit p95 > 500 ms, blank background | repeated PDF open on cache hit |
| P4 overlay/tile | 1k/10k/100k overlay workloads | full overlay JSON required for first paint, RSS grows linearly after cache cap | tile manifest materialization exceeds budget |
| P5 selected-zone | Rapid click and malformed CAD | blank selected-zone count > 0, stale request wins, no reason_code | visible fallback but fidelity/reason hidden |
| P6 multi-detail | Region detection/matching | whole-modelspace auto-compare when candidates exist, DWG cached-DXF region compare skipped without explicit reason | review queue too noisy |
| P8 release | Realset/customer package | rollback flag missing, path leak, raw sensitive perf logs in shareable package | unresolved failure lacks UX copy |

Initial numeric budgets:

- P0 first-review-ready release gate: <= 30 s for normal CAD/PDF, <= 120 s for
  large CAD, hard cap 300 s.
- P1/P2 target first-review-ready after hot-path work: <= 3 s for normal
  CAD/PDF and <= 10 s for large CAD when cached visual assets exist.
- package-complete: measured but not blocking first-review; regression > baseline
  +10% requires explanation.
- peak working set: <= 2 GB for large CAD target or <= current baseline +10%;
  hard cap 4 GB.
- selected-zone CAD crop: <= 10 s cold or visible fallback; <= 1 s cache hit.
- selected-zone PDF/background crop: <= 2 s cold; <= 500 ms cache hit.
- telemetry overhead: <= 2%.
- 100-pair navigation soak: RSS slope after cache warmup <= 5 MB per 100
  selections, unless a bounded cache explicitly explains the retained memory.

## 4. PDF-First Visual Asset Spec

### 4.1 Backend Order

Default visual source priority:

1. source PDF;
2. sidecar PDF;
3. approved CAD-to-PDF backend;
4. approved CAD-to-image backend;
5. current DXF/raster fallback;
6. skeleton or relative-only fallback.

CAD-to-PDF must not become the CAD compare truth.

### 4.2 Visual Asset Manifest Fields

```text
asset_id
asset_kind = source_pdf | sidecar_pdf | cad_pdf | cad_image | raster_fallback | skeleton | relative_only
source_signature
source_path_redacted
backend_id
backend_version
backend_license_id
license_mode
requires_network
plot_profile_hash
layout_name
page_index
dpi
page_size_pt
pixel_size
coordinate_contract_version
world_to_pdf
pdf_to_pixel
world_to_pixel
transform_quality = exact | estimated | relative_only | unavailable
status = ready | deferred | failed | license_blocked | timeout | unavailable
reason_code
created_at
cache_key_hash
```

Viewer must refuse to show exact overlay badges when `transform_quality` is not
`exact`.

### 4.3 Converter Gate

All CAD-to-PDF backends are disabled by default until:

- explicit backend allowlist exists outside environment variables;
- subprocess execution is killable and timeout-bounded;
- cloud upload requires explicit user approval and audit evidence;
- output PDF opens successfully;
- page count/media box/thumbnail/nonblank preview checks pass;
- converter log and warning count are stored;
- legal/license redistribution review is complete.

Reason codes:

```text
source_pdf_missing
sidecar_not_found
cad_to_pdf_disabled
backend_license_blocked
backend_timeout
backend_cancelled
backend_crashed
output_pdf_invalid
output_pdf_blank
page_match_review_required
region_match_ambiguous
transform_estimated
zone_crop_failed
direct_render_degraded
```

### 4.4 Hybrid Compare API Contract

PDF-first is a visual strategy, not a hidden replacement for CAD truth.

```text
build_visual_assets(source, profile, backends) -> visual_asset_manifest.json
compare_pair_truth(before, after, mode=cad_entity|pdf_raster|hybrid) -> comparison_result
detect_regions(source, visual_asset=None, layers=cad|pdf_text|pdf_vector) -> region_detection_summary.json
match_regions(before_regions, after_regions, overrides=None) -> region_match_summary.json
build_region_local_compare(pair, approved_matches) -> localized_change_zones_v2.json
render_selected_zone(pair, zone, prefer=background_crop|pdf_display_list|cad_source|vector) -> render_result.json
```

Allowed truth modes:

| Mode | Inputs | Truth source | Use case | Default |
| --- | --- | --- | --- | --- |
| `cad_entity` | DWG/DXF | canonical/entity diff | CAD reports and change CSVs | yes for CAD |
| `pdf_raster` | PDF/PDF | visual/text/OCR/image diff | PDF-only inputs | yes for PDF |
| `hybrid` | CAD with visual asset | CAD entity diff plus PDF/raster viewer | recommended CAD UX after gates | guarded |
| `visual_only` | CAD/PDF mixed or conversion-only | no authoritative diff | manual diagnostics only | no |

Required manifest fields for hybrid mode:

- `truth_source`: `cad_entity`, `pdf_raster`, or `visual_only`.
- `truth_quality`: `exact`, `degraded`, `visual_only`, or `unavailable`.
- `visual_asset_id` and `visual_asset_transform_quality`.
- `source_signature` for original source and resolved cached DXF/PDF.
- `coordinate_contract_version`.
- `fallback_reason_code` when any layer is not exact.

CLI target shape:

```powershell
python -m src.cli.cad_compare visual-assets --input A.dwg --out run --backend qcad --profile plot.json --validate
python -m src.cli.cad_compare folder --before old --after new --output run --viewer-render-policy top-issues
python -m src.cli.cad_compare regions detect --input A.dxf --visual visual_asset_manifest.json --out region_detection_summary.json
python -m src.cli.cad_compare regions match --before before_regions.json --after after_regions.json --overrides manual_region_matches.json
python -m src.cli.cad_compare render-zone --pair-manifest viewer_manifest.json --zone-id Z001 --prefer pdf-display-list
```

## 5. Benchmark and Acceptance Gates

### 5.1 Required Corpora

Minimum realset before default enablement:

- CAD pairs >= 8;
- PDF pairs >= 8;
- large DWG/DXF pairs >= 2;
- multi-detail pairs >= 4;
- raster/low-quality PDF pairs >= 2;
- negative controls >= 2.

Synthetic stress fixtures:

- 10 MB / 100k entity CAD fixture;
- 50 MB CAD stress fixture;
- block-heavy DXF fixture;
- malformed `LWPOLYLINE` large-file fixture;
- 100 pair / 100k overlay viewer package;
- multi-page PDF reorder fixture;
- multi-detail 4+ region frame/title ambiguity fixture;
- PDF sidecar/display-list crop fixture;
- forced render failure fixture.

### 5.2 Performance Gates

| Gate | Target |
| --- | --- |
| Normal first result p95 | <= 30 s |
| Large first result p95 | <= 120 s, hard cap <= 300 s |
| Cached PDF pair selection p95 | <= 300 ms |
| Cold PDF pair selection p95 | <= 2 s |
| PDF/image selected-zone cold crop p95 | <= 2 s |
| Selected-zone cache-hit p95 | <= 500 ms |
| CAD selected-zone behavior | success <= 10 s or visible fallback |
| Viewer perf append p95 | <= 5 ms for 1k/10k/100k event workloads |
| Runtime sampler tick p95 | <= 20 ms |
| Total telemetry overhead | <= 2% |
| Large CAD peak RSS | <= 2 GB or current realset baseline +10% |
| Release hard RSS cap | <= 4 GB |
| Normal spool | <= 1 GB |
| Large spool | <= 4 GB |
| 10 MB / 100k synthetic | <= baseline * 1.15 |
| 50 MB synthetic | pass or explicit timeout/limit reason |
| 100 pair / 100k overlay navigation | no linear RSS growth after cache limit |
| 30 min pair/zone navigation soak | RSS stabilizes near cache limit |

### 5.3 Correctness and UX Gates

| Gate | Target |
| --- | --- |
| Pair viewer blank count | 0 |
| Selected-zone blank count | 0 |
| Nonblank screenshot pixel check | pass on representative cases |
| PDF actual crop rate | >= 0.85 |
| CAD actual crop rate | >= 0.95 |
| Overall actual crop rate | >= 0.90 |
| Page auto-match precision | >= 0.99 |
| Exact transform round-trip | <= 2 px or <= 0.5 mm |
| Multi-detail region detection | >= 80%, target >= 90% |
| User-approved region match accuracy | >= 95% |
| Whole-modelspace auto-compare when candidates exist | 0 |
| Path leakage in sharable outputs | 0 |

### 5.4 Output Evidence

Every release-gate run must preserve:

```text
validation_summary.json
run_manifest.json
_SUCCESS
perf_events_summary.json
artifacts/region_detection_summary.json
artifacts/region_match_summary.json
artifacts/localized_compare_summary.json
artifacts/localized_region_compare_results.json
artifacts/localized_change_zones_v2.json
artifacts/region_aware_status.json
artifacts/multi_frame_validation.json
artifacts/region_viewer/region_viewer_manifest.json
viewer/viewer_perf.jsonl
viewer/viewer_perf.json
viewer/selected_zone_evidence.json
workbench_acceptance_summary.json
screenshots/
nonblank_pixel_probe.json
```

Sharable/customer packages should remove raw `perf_events.jsonl` but keep
summary JSON.

## 6. Work Package Roadmap

### P0. Baseline Freeze and Repro Evidence

Goal: stop guessing. Capture current failures and speed/memory baselines before
more architectural changes.

Current status, 2026-05-27:

- P0 inventory tooling exists in `scripts/inventory_performance_baselines.py`.
- Latest generated report: `docs/collab/DRAWING_COMPARE_P0_BASELINE_REPORT.md`.
- PDF, CAD, and large CAD baselines have minimum P0 evidence, but still show P1
  instrumentation gaps (`perf_events_summary.json`, `viewer_perf.jsonl`, and
  persisted `nonblank_pixel_probe.json`).
- Large CAD must retain both slow and fastcrop evidence. The current slow
  baseline is `release/sample_compare_dwg_1_2` with total runtime 183.746 s and
  selected-zone cold p95 54142.289 ms. The fastcrop contrast is
  `release/sample_compare_dwg_1_2_fastcrop` with total runtime 109.564 s and
  selected-zone cold p95 31.171 ms.
- Multi-detail is not P0-complete. The only discovered candidate
  `build/cad-compare-smoke/folder-run` has zero detected detail regions, two
  region-detection failures, one viewer render failure, and the root cause
  warning `missing 'AcDbPolyline' subclass in LWPOLYLINE(#13)`.
- Follow-up tolerant-read smoke:
  `build/multi-detail-baseline/acdbpolyline_repair_smoke` now validates the
  malformed DXF read/render path: status `passed`, total 2.807 s, peak working
  set 320.684 MB, selected-zone actual crop rate 1.0, viewer render status
  `rendered`, and transform metadata records `dxf_read_sanitized=true`.
- That smoke is not accepted as the multi-detail baseline because it has one
  detected region per side and does not prove region sidecar/matching/localized
  compare behavior for a real multi-detail drawing. Inventory currently
  classifies it as CAD baseline, not multi-detail completion.
- The next P0 blocker is therefore no longer just malformed DXF parsing. It is
  the combined contract of: real multi-detail fixture, persisted region
  sidecars, DWG cached-DXF eligibility, region-local compare evidence, viewer
  nonblank evidence, and performance budget evidence.

Tasks:

- collect latest real failing runs from logs/AppData/output folders;
- store first-review, package-complete, selected-zone cold/hit, peak RSS, blank
  viewer evidence under `.benchmarks/`;
- add a short baseline report under `docs/collab`;
- identify at least one real multi-detail case where whole-modelspace fallback
  is currently misleading.

Acceptance:

- one PDF baseline, one CAD baseline, one large CAD baseline, one multi-detail
  baseline exist;
- each baseline has run manifest, perf summary, viewer perf summary, screenshot
  or nonblank evidence;
- current p95/RSS numbers are recorded before optimization.

Owner agents:

- Explorer: evidence inventory.
- Worker: baseline report generator and `.benchmarks` layout.
- Reviewer: verifies evidence is enough to reproduce.

P0-B next execution contract:

1. Use a valid multi-detail before/after folder pair, not the current broken
   smoke DXF pair.
2. Run the P0-B wrapper, which sets region-mode environment variables, launches
   the realset validator with P0 evidence options, writes post-run
   `perf_events_summary.json` and `nonblank_pixel_probe.json`, then re-runs
   inventory:

   ```powershell
   python scripts\p0_multi_detail_baseline_runner.py --before <before-multi-detail-folder> --after <after-multi-detail-folder> --case-id <case-id>
   ```

   Equivalent underlying validator command:

   ```powershell
   $env:DRAWING_COMPARE_MULTI_FRAME='auto'
   $env:DRAWING_COMPARE_AUTO_REGION_COMPARE='1'
   python scripts\validate_drawing_compare_realset.py --a <before-multi-detail-folder> --b <after-multi-detail-folder> --out build\multi-detail-baseline\<case-id> --measure-runtime-budget --change-zone-report --review-dashboard --executive-review --export-viewer-package --viewer-render-policy top-issues --viewer-perf-log --render-selected-zone-evidence --selected-zone-evidence-per-pair 1 --export-profile internal
   ```

3. Re-run the P0 inventory:

   ```powershell
   python scripts\inventory_performance_baselines.py --max-runs 200 --output-json .benchmarks\performance_baseline_inventory.json --output-md docs\collab\DRAWING_COMPARE_P0_BASELINE_REPORT.md --fail-on-incomplete
   ```

4. Do not move to P1 optimization until multi-detail has a successful
   `validation_summary.json`, viewer perf, selected-zone evidence, nonblank
   visual evidence, detected detail regions, approved matches or review gate,
   and no background render failure.

P0-C sidecar/performance contract:

1. `p0_multi_detail_baseline_runner.py` must fail loudly, not silently pass, when
   the run lacks these sidecars:
   - `artifacts/region_detection_summary.json`
   - `artifacts/region_match_summary.json`
   - `artifacts/localized_compare_summary.json`
   - `artifacts/multi_frame_validation.json`
   - `viewer/region_viewer_manifest.json` or equivalent viewer region manifest
   The current implementation names are the contract for this phase. Do not
   introduce parallel `*_v2.json` names unless an adapter writes both old and new
   names and tests prove the inventory can read them.
2. Each sidecar must expose counts and skip reasons:
   - detected frame/detail count by side
   - whole-modelspace fallback count
   - auto/manual/review-required/unmatched match counts
   - localized compared/skipped/unsupported counts
   - `unsupported_reason`, especially DWG cached-DXF gate misses
3. The runner must write performance evidence in the same output root:
   - `perf_events_summary.json`
   - `viewer/viewer_perf.jsonl` plus compact `viewer_perf.json`
   - `viewer/selected_zone_evidence.json`
   - `nonblank_pixel_probe.json`
   - `runtime_budget` embedded in `validation_summary.json`
4. If the fixture has only one region per side, inventory should classify it as
   malformed-CAD smoke or CAD baseline, not as multi-detail completion.

### P1. Mandatory Instrumentation Gate

Goal: every slowdown report should answer "which stage got slower" without
manual log spelunking.

Tasks:

- extend perf event coverage using the schema in section 3;
- add GUI selection `gui_block_ms` and spawned worker counts;
- add PDF page render/cache/prune timings;
- add tile pyramid and overlay tile generation timings;
- add selected-zone stale/cancel/fallback counts;
- make validation summary embed perf and runtime summaries by default.

Acceptance:

- `audit_drawing_compare_mvp_exit.py` fails strict mode if perf/runtime evidence
  is missing;
- synthetic fixture shows stage-level elapsed and peak RSS;
- telemetry overhead gates pass.

Owner agents:

- Worker A: run-level perf summary and audit integration.
- Worker B: GUI/viewer perf events.
- Worker C: PDF/tile/zone timing.
- Reviewer: overhead and schema review.

### P2. GUI Hot Path Fan-Out Reduction

Goal: a drawing click should show a reviewable pair quickly and not start every
expensive subsystem.

Tasks:

- move PDF/raster loading behind an async or idle gate where safe;
- keep initial zone tree to top issues or first-N only;
- make full tree rebuild debounced and cancellable;
- ensure first auto-zone selection does not trigger crop + vector before first
  pair paint;
- skip hidden legacy preview loads in lightweight-only mode;
- record pair selection p50/p95 from real GUI events.

Acceptance:

- cached PDF pair selection p95 <= 300 ms;
- cold PDF pair selection p95 <= 2 s;
- selecting a pair in first-review mode does not call full tile pyramid
  generation;
- 100 pair / 100k overlay navigation does not show linear RSS growth after
  cache limit.

Owner agents:

- Worker A: GUI selection orchestration.
- Worker B: overlay cache byte-bound LRU.
- Worker C: selection latency tests and soak harness.

### P3. PDF Hot Path and Visual Asset Cache

Goal: PDF-first should make the viewer more reliable without causing full-page
render storms.

Tasks:

- add page metadata cache so cache hits avoid repeated PDF open/page-size work;
- make PDF cache prune amortized/background, not per page load;
- cap render by total pixel/memory budget, not only max side;
- prefer DisplayList crop for selected zones;
- add visual asset manifest output validation for source/sidecar PDFs;
- preserve background when vector focus fails.

Acceptance:

- PDF page cache hit avoids full render and avoids repeated document open where
  possible;
- selected-zone PDF crop cold p95 <= 2 s and hit p95 <= 500 ms;
- nonblank screenshot checks pass for representative PDF cases;
- no vector failure clears a valid PDF/raster background.

Owner agents:

- Worker A: PDF metadata/cache/prune.
- Worker B: DisplayList crop integration.
- Reviewer: coordinate/fidelity checks.

### P4. Overlay and Tile Streaming

Goal: large overlays and tiles become on-demand data, not startup cost.

Tasks:

- replace selection-time tile manifest JSON rewrite with append/materialize path;
- introduce paged or tile-indexed overlay store for large pairs;
- add in-memory tile manifest cache with invalidation by source signature;
- generate LOD tiles visible-first or explicitly deferred;
- record overlay JSON bytes and materialized overlay count.

Acceptance:

- full overlay JSON is not required for first paint;
- overlay materialization in first selection is capped;
- tile manifest update p95 meets target on 1k/10k/100k overlay workloads;
- RSS stays bounded in navigation soak.

Owner agents:

- Worker A: overlay store abstraction.
- Worker B: tile manifest append/materialize.
- Worker C: benchmark fixtures.

### P5. Selected-Zone Render Orchestrator

Goal: selected-zone view must never enter a repeated blank/failure loop.

Tasks:

- introduce one request coordinator with request id and stale cancellation;
- prioritize crop from existing background/tile/PDF visual asset;
- use PDF DisplayList crop before CAD/vector focus;
- keep CAD/vector as deferred enhancement or explicit action;
- add persistent source-index worker with weighted LRU and RSS cap;
- emit structured fallback reason codes.

Acceptance:

- blank selected-zone failure count = 0;
- CAD source crop succeeds <= 10 s or visible fallback appears;
- rapid click test leaves only the latest request visible;
- reason code appears in UX and viewer perf summary.

Owner agents:

- Worker A: orchestrator state machine.
- Worker B: persistent cache/worker.
- Worker C: UX/fallback reason surface.
- Reviewer: rapid-click/stale behavior.

### P6. Multi-Detail Scaling and Review Gate

Goal: one CAD/PDF file with multiple drawings compares matching regions only.

Tasks:

- use spatial index for frame/title/entity-inside queries;
- detect PDF vector/text frames and title blocks when source/sidecar PDF exists;
- block whole-modelspace auto-compare when region candidates exist;
- persist manual region overrides;
- make region viewer manifest-only until selected;
- run region-local compare only for approved matches.

Acceptance:

- synthetic multi-frame detection = 100%;
- pilot realset region detection >= 80%, target >= 90%;
- approved match accuracy >= 95%;
- whole-modelspace auto-compare count = 0 when candidates exist;
- moved same sheet does not produce a false large diff.

Owner agents:

- Worker A: spatial detection.
- Worker B: PDF frame/title extraction.
- Worker C: review gate and overrides.
- Reviewer: ambiguity and false-positive cases.

### P7. CAD-to-PDF Backend Lab

Goal: evaluate converter value safely without shipping hidden legal or accuracy
risk.

Tasks:

- choose one internal backend candidate;
- implement adapter behind explicit allowlist;
- validate output PDF and manifest provenance;
- compare visual fidelity against source/sidecar PDFs;
- keep customer build default disabled.

Acceptance:

- failed conversion degrades to existing visible fallback;
- no backend runs from environment variable alone;
- provenance/license fields are present;
- legal review is recorded before distribution.

Owner agents:

- Worker A: adapter skeleton.
- Worker B: output validation and cache.
- Reviewer: policy/license gate.

### P8. Release Gate and Rollback

Goal: defaults change only after evidence says the program is better.

Tasks:

- integrate benchmark output into `audit_drawing_compare_mvp_exit.py`;
- add nonblank pixel screenshot gate to workbench acceptance;
- run realset corpus;
- verify rollback flags for PDF-first, region-local, CAD-to-PDF, and vector
  enhancement paths;
- write release summary with before/after p95 and RSS.

Acceptance:

- all gates in sections 5.2 and 5.3 pass;
- rollback path tested;
- customer package contains summaries but not raw sensitive logs;
- unresolved failures have reason codes and are not silent.

### P9. Agent Execution Board

Use this board when assigning future multi-agent work. Agents should work on
disjoint files and report changed paths, commands, and remaining risks.

| Step | Agent lane | Write ownership | Inputs | Output | Gate |
| --- | --- | --- | --- | --- | --- |
| P0-C1 | Evidence runner | `scripts/p0_multi_detail_baseline_runner.py`, runner tests | current P0 report, real multi-detail fixture | runner fails on missing sidecars and records perf evidence | `test_run_p0_multi_detail_baseline.py` |
| P0-C2 | Region contract | `folder_compare_pipeline.py`, `region_compare_pipeline.py`, region tests | DWG cached DXF pair | sidecars written for DWG/DXF and skip reasons explicit | `test_region_aware_compare.py` |
| P1-A | Run telemetry | `perf_events.py`, `runtime_budget.py`, audit tests | P0 runs | bounded summary and overhead fields | runtime/perf tests |
| P1-B | Viewer telemetry | `viewer_tile_cache.py`, `viewer_perf_summary.py`, GUI tests | viewer package output | JSONL append, cold/hit/fallback summaries | viewer perf tests |
| P2-A | GUI selection | `drawing_compare_workbench.py`, GUI tests | 100-pair manifest | first-N overlay, deferred full tree, no blocking full tiles | GUI selection tests |
| P3-A | PDF cache | `pdf_display_list_cache.py`, `zone_render_service.py` | PDF fixtures | DisplayList crop first, byte-aware cache telemetry | PDF crop tests |
| P4-A | Overlay/tile | `viewer_tile_cache.py`, package tests | 100k overlay synthetic | append/materialize manifest, visible-first tiles | tile benchmark |
| P5-A | Zone orchestrator | `zone_render_service.py`, worker/process tests | malformed CAD/PDF cases | request-id cancellation, visible fallback, reason_code | rapid-click/zone tests |
| P6-A | Multi-detail scale | `sheet_region_detector.py`, matcher tests | real/synthetic frames | spatial-indexed detection and review gate | multi-detail validation |
| P8-A | Release gate | `audit_drawing_compare_mvp_exit.py`, docs | realset outputs | stop/go summary and rollback proof | strict audit |

Execution rule:

- Do not optimize before the relevant P0/P1 metric exists.
- Do not enable PDF-first or CAD-to-PDF defaults before rollback and provenance
  gates pass.
- Do not mark multi-detail complete from a single-region malformed-CAD smoke.
- Every worker must add or update at least one focused test unless the task is
  documentation-only.

## 7. Immediate Next Steps

Recommended sequence from the current state:

1. Close P0-C: make the multi-detail runner/validator sidecar contract explicit
   and rerun a real multi-detail fixture.
2. Fix DWG cached-DXF region-local compare eligibility so DWG inputs do not skip
   localized compare solely because the original suffix is `.dwg`.
3. Implement P1 mandatory instrumentation gates and fail strict audit when
   runtime/viewer/perf evidence is missing.
4. Implement P2 GUI hot path fan-out reduction with p95 pair-selection evidence.
5. Implement P3/P5 PDF crop-first and selected-zone orchestrator work before
   adding any new CAD-to-PDF backend.
6. Implement P6 multi-detail scaling with spatial index and review-gated
   matching.
7. Run P8 realset/release gates and compare before/after p95/RSS before changing
   defaults.

Do not start with CAD-to-PDF conversion as the first implementation step. It can
improve viewer fidelity, but without P0-P2 and P5 it can also add converter
latency, cache churn, and coordinate ambiguity on top of the current slowdown.

## 8. Gate Commands

Focused regression:

```powershell
python -m pytest tests\unit\services\comparison\test_workbench_phase_c.py tests\unit\services\comparison\test_korean_workbench_ux.py tests\unit\services\comparison\test_viewer_package.py tests\unit\services\comparison\test_viewer_tile_cache.py -q -o log_cli=false
python -m pytest tests\unit\services\comparison\test_runtime_budget.py tests\unit\services\comparison\test_viewer_perf_summary.py tests\unit\services\comparison\test_perf_events.py -q -o log_cli=false
python scripts\cad_policy_gate.py
```

Benchmarks:

```powershell
python scripts\benchmark_zone_render.py --fixture large --zones 10 --runs 5
python scripts\benchmark_viewer_build.py --fixture structural --runs 3
python scripts\cad_performance_benchmark.py --line-counts 1000,10000,100000 --target-mb 10
```

Realset gates:

```powershell
python scripts\validate_multi_detail_region_compare.py --input <pilot-manifest.json> --output build\multi-detail-pilot
python scripts\validate_drawing_compare_realset.py --input <realset-manifest.json> --output build\realset-performance
python scripts\audit_drawing_compare_mvp_exit.py --strict-zone-render-budget --require-runtime-budget --require-dataset-composition
```

## 9. Completion Standard

This performance work is complete only when:

- first-review-ready and package-complete are measured separately;
- the GUI shows a result list and first review item before heavy exports finish;
- pair selection p95 and selected-zone p95 meet gates on real and synthetic data;
- viewer and selected-zone blank counts are zero;
- telemetry overhead is bounded;
- RSS does not grow linearly after cache limits;
- multi-detail candidates are matched or review-gated, not silently global-compared;
- PDF visual assets have provenance and coordinate quality;
- realset evidence and rollback tests exist.

Passing unit tests alone is not enough.

## 10. 2026-05-27 Deep Planning Addendum

This addendum converts the slowdown concern into an execution plan. The key
decision is to treat performance degradation as a measurable product failure,
not as a secondary cleanup item after rendering and region matching.

### 10.1 Performance Failure Model

Every future bug report or implementation step should classify slowdown into
one or more of these buckets:

| Bucket | User symptom | Primary evidence | Hard stop condition |
| --- | --- | --- | --- |
| First result latency | 비교가 끝났는지 모르겠고 결과가 안 보임 | `first_review_ready_ms`, `package_complete_ms`, result row timestamp | first review waits for full package/tile/export work |
| Pair selection latency | 도면 행을 누르면 UI가 멈춤 | `pair_selection_gui_block_ms`, event-loop max gap, worker count | cached pair p95 > 300 ms or GUI block > 100 ms |
| PDF/page navigation latency | PDF 페이지 이동이 이전보다 느림 | cold/cached page load split, metadata hit, render call count | cached/prewarmed load reopens/rerenders the PDF |
| Selected-zone latency | 선택구역 렌더 실패 또는 매우 늦음 | request id, crop/vector/backend elapsed, stale/cancel count | stale result wins, blank selected-zone, no reason code |
| Memory/RSS climb | 오래 쓰면 점점 느려짐 | RSS slope, cache entry bytes, file handle count, spool MB | RSS keeps rising after bounded cache warmup |
| Overlay/tile explosion | 큰 도면에서 첫 화면이 늦음 | overlay bytes, materialized overlay count, tile count | first paint requires full overlay/tile pyramid |
| Multi-detail scaling | 여러 도곽에서 엉뚱한 도면이 비교됨 | detected region count, match status, whole-modelspace fallback count | whole-modelspace auto-compare when candidates exist |
| Telemetry overhead | 측정 기능을 켰더니 더 느려짐 | sampler tick p95, perf append p95, summary input bytes | telemetry overhead > 2% |

### 10.2 Measurement Spec

Required metrics by stage:

| Stage | Required fields | Target |
| --- | --- | --- |
| Compare run | pair count, entity count, first-review-ready, package-complete, peak RSS, spool MB | first-review separated from package completion |
| PDF initial load | requested/effective DPI, pixel budget, render ms, cache hit, metadata hit, document open count | A1/A0 first render capped by pixel budget |
| PDF prewarm | target page, generation id, ok/cache/rendered/metadata counts, elapsed, state mutation flag | no visible viewer state mutation |
| GUI selection | GUI block ms, event-loop gap max, initial overlay count, full tree deferred flag | cached p95 <= 300 ms, cold p95 <= 2 s |
| Overlay/tree | overlay JSON bytes, worker load ms, plan build ms, tree item count, chunk max ms | full tree never blocks first paint |
| Selected-zone | request id, backend order, crop/vector elapsed, fallback reason, stale/cancel counts | blank count = 0 |
| Region matching | candidate count, detected regions by side, score components, review-required count | ambiguous matches are review-gated |
| Cache/memory | cache namespace, key hash, entry count, estimated bytes, evict reason, RSS before/after | RSS slope bounded after warmup |

Performance data must be recorded as append-only events or compact summaries.
No hot path may repeatedly rewrite a growing JSON document.

### 10.3 Repro and Benchmark Matrix

Minimum matrix before changing defaults:

| Scenario | Purpose | Required gate |
| --- | --- | --- |
| A1/A0 multi-page PDF cold load | proves initial DPI cap prevents huge pixmaps | first render <= budget, nonblank viewer |
| A1/A0 multi-page PDF prewarm hit | proves adjacent-page prewarm avoids reopen/rerender | metadata hit true, render call count zero |
| 100-pair navigation soak | catches GUI cache and RSS slope regressions | RSS slope <= 5 MB / 100 selections after warmup |
| 100k overlay synthetic | catches tree/tile materialization explosion | first paint uses top-N/lazy path |
| rapid zone click soak | catches stale render and duplicate work | only latest request visible, blank count zero |
| malformed/block-heavy CAD | catches direct vector failure fallback | visible crop/fallback plus reason code |
| real multi-detail CAD pair | proves region detection/matching path | no silent whole-modelspace auto-compare |
| repeated same-source compare | catches cache churn and file leak | no duplicate conversion without reason |
| telemetry-on/off A/B | catches measurement overhead | overhead <= 2% |

### 10.4 Concrete Roadmap Clarification

The next work should proceed in this strict order:

1. **P2-I-B: PDF metadata fast path and adjacent prewarm**
   - Finish current PDF cache metadata fast path, adjacent page prewarm, and
     benchmark evidence.
   - Gate: prewarmed cached page load has `metadata_hit=true`, no cold render,
     no document open, and no visible state mutation from prewarm.

2. **P2-J: Navigation soak and RSS regression harness**
   - Add 100-pair and repeated PDF page navigation probes.
   - Gate: event-loop max gap and RSS slope stay below budget after cache
     warmup.

3. **P3-A: Selected-zone crop-first orchestrator**
   - Make existing PDF/raster/tile crop the primary selected-zone result.
     Vector/CAD focus becomes deferred enhancement.
   - Gate: selected-zone blank count is zero and stale requests cannot win.

4. **P4-A: Overlay and tile streaming**
   - Remove first-selection dependency on full overlay JSON and full tile
     pyramid generation.
   - Gate: 100k overlay fixture first paint does not materialize all overlays.

5. **P5-A: Byte-aware cache and memory budget**
   - Convert entry-count caches to byte/entity-weighted LRU where needed.
   - Gate: long navigation and repeated compare runs show bounded RSS.

6. **P6-A: Multi-detail spatial detection and review gate**
   - Detect frame/title candidates with spatial indexes, match same logical
     drawings, and route weak matches to review.
   - Gate: when candidates exist, whole-modelspace fallback cannot be used as
     an automatic successful comparison.

7. **P7-A: CAD-to-PDF backend lab**
   - Evaluate converter as a visual asset only, behind explicit allowlist and
     provenance.
   - Gate: no converter runs in GUI hot path, and CAD entity diff remains the
     truth layer for CAD inputs.

8. **P8-A: Release and rollback gate**
   - Run realset, screenshot/nonblank, perf/RSS, and region-match gates before
     enabling defaults.
   - Gate: release summary includes before/after p95, RSS, blank count, and
     rollback flags.

### 10.5 Agent Work Board for the Next Iteration

Use disjoint ownership to avoid merge conflicts:

| Lane | Ownership | Deliverable | Validation |
| --- | --- | --- | --- |
| Performance profiler agent | benchmark scripts, perf summary tests | navigation/RSS/prewarm probes | benchmark JSON plus strict gates |
| PDF viewer agent | `lightweight_viewport.py`, PDF adapter tests | metadata fast path, prewarm, prune policy | no rerender on metadata hit |
| GUI orchestration agent | `drawing_compare_workbench.py`, GUI tests | stale-safe scheduling and lazy fan-out | event-loop and GUI block budgets |
| Region matching agent | region detector/matcher/pipeline tests | spatial-indexed detection and review gate | multi-detail real/synthetic fixtures |
| QA gate agent | audit scripts, acceptance smoke | strict stop/go report | missing evidence fails loudly |

No lane should mark its work done without a focused unit test and at least one
benchmark or realset artifact when the change touches runtime behavior.

### 10.6 MVP Scope and Default-Enablement Rules

MVP scope:

- DXF and currently supported DWG paths keep CAD entity comparison as the
  truth layer.
- PDF/PDF inputs use PDF visual/text/image comparison.
- Multi-detail files detect candidate frames and match the same logical drawing
  by drawing number, title text, frame geometry, layer histogram, and entity
  histogram.
- Low-confidence or ambiguous matches go to user review.
- Region-local compare runs only for approved matches.
- Viewer output must never be blank: use source PDF, sidecar PDF, existing
  raster background crop, skeleton, or relative-only fallback with a reason
  code.
- First review shows result rows and top issues before full package export,
  full tiles, marked PDFs, or complete tree materialization.

Explicitly outside MVP:

- treating CAD-to-PDF output as CAD truth;
- default cloud conversion;
- unreviewed commercial/GPL/AGPL converter dependency;
- fully automatic handling of every customer title block style;
- region-local primary default without real pilot evidence;
- CAD/PDF mixed entity-level comparison.

Default enablement is blocked until all of these are true:

- real multi-detail pilot includes 10-20 drawing pairs;
- detected region rate >= 80%, target >= 90%;
- approved match accuracy >= 95%;
- whole-modelspace auto-compare count is 0 when frame candidates exist;
- first-review, pair-selection, selected-zone, peak RSS, and telemetry overhead
  budgets pass;
- screenshot/nonblank evidence exists for representative CAD, PDF, and
  multi-detail cases;
- feature flags can roll back PDF-first visual assets, region-local compare,
  and CAD-to-PDF labs independently.

### 10.7 Data Model and Pipeline Lock

The implementation should converge on these contracts instead of adding
parallel ad-hoc artifacts:

```text
SourceSignature
VisualAssetManifest
SheetRegionV2
RegionMatchV2
ManualRegionOverride
LocalizedCompareResultV2
ReviewQueueItem
PerfEvent
```

Target pipeline:

```text
input files
  -> preflight/source signatures
  -> truth layer split (CAD entity vs PDF visual/text/image)
  -> visual asset preparation
  -> frame/title/detail-region detection
  -> region identity extraction
  -> region matching
  -> user review/manual override for ambiguous matches
  -> approved region-local compare
  -> lazy viewer package and preview
  -> review queue/export
  -> perf/audit/validation evidence
```

Implementation rule:

- visual assets may improve inspection and cropping, but they do not override
  CAD entity truth.
- any transform that is estimated must be marked as estimated in the manifest
  and must not display an exact-overlay badge.
- unresolved or review-required region matches block automatic primary compare
  for that region.

### 10.8 Next-Step Execution Checklist

For the next coding pass, execute in this order:

1. Finish P2-I-B PDF prewarm verification:
   - prove metadata fast path skips `PdfPageRenderer`;
   - prove prewarm does not mutate visible viewport state;
   - add benchmark evidence for cached page navigation.
2. Add P2-J soak harness:
   - 100 pair/page navigation;
   - rapid zone selection;
   - RSS slope and event-loop gap gates.
3. Harden selected-zone render:
   - request id;
   - stale cancellation;
   - crop-first;
   - vector as deferred enhancement;
   - visible fallback with reason code.
4. Close P0-C/P6 blocker:
   - collect real multi-detail fixture;
   - require region sidecars;
   - prevent silent whole-modelspace success when candidates exist.
5. Move to byte-aware caches only after the above metrics exist:
   - PDF DisplayList;
   - DXF render index;
   - overlay/tile cache.

This order is intentional: without the probes, a cache or converter change can
hide the current slowdown rather than remove it.

### 10.9 P4 Overlay/Tile Streaming Clarification

The 2026-05-27 P4-A planning review split overlay/tile work into three
separate acceptance layers. They must not be collapsed into one "tile work is
done" label:

| Layer | Required behavior | Evidence |
| --- | --- | --- |
| First paint | selecting a drawing does not synchronously parse full overlay JSON | 100k overlay first-paint probe, read-call count = 0 |
| Selection-time tile metadata | pair preview render does not rewrite a growing global `tiles_manifest.json` | per-pair manifest plus JSONL append/materialize path |
| Tile payload/RSS | tile and overlay generation are visible-first or explicitly deferred | 1k/10k/100k tile workload p95 and RSS soak |

Current P4-A/P4-B/P4-C slices close the first visible-review tile layers:

- per-pair tile manifests are preferred and guarded by `tile_cache_key`;
- selection-time pair renders append/materialize tile metadata instead of
  repeatedly merging a global manifest;
- overlay tile materialization records declared/materialized/omitted counts;
- 100k first-paint benchmark evidence shows declared overlays = 100000,
  materialized first-paint overlays = 5, full overlay JSON read calls = 0, and
  overlay JSON bytes about 38.8 MB.
- `write_pair_visible_tile_cache()` materializes only the visible viewport plus
  prefetch window while preserving the v1 tile-manifest contract;
- selection-time pair preview render uses visible-first tiles when a selected
  bbox can provide an initial viewport;
- full viewer-package export now requires `pyramid_complete` cache hits, so a
  partial first-review cache cannot be mistaken for a complete share/export
  cache;
- 4096x4096 visible-tile benchmark evidence shows materialized tiles = 18,
  planned tiles = 128, omitted tiles = 110, and outside-window status =
  `tile_pending`.
- pan/zoom on-demand tile materialization is now wired through the legacy GPU
  viewport and the active lightweight viewer path; lightweight camera world
  rects are mapped back to rendered image pixel rects before requesting tiles;
- `VisibleTileWindowWorker` appends only the missing visible window, refreshes
  the partial manifest, and ignores stale results through a generation guard;
- P4-C benchmark evidence shows a second pan window increases materialized
  tiles from 18 to 20, changes the requested outside window from
  `tile_pending` to `tile_ready`, keeps `pyramid_complete=false`, and repeating
  the same window adds 0 tiles.

The 2026-05-27 P5-A slice started closing the post-first-paint overlay memory
risk:

- viewer packages now write `overlay_pages/{pair}/manifest.json` plus paged
  overlay shards for every pair;
- large legacy `overlays/{pair}.json` payloads keep only a compatibility/top
  subset and record `overlay_legacy_truncated`, `overlay_legacy_count`, and
  `overlay_pages_manifest`;
- the full-zone tree worker receives `overlay_pages_manifest` and prefers it
  over the legacy `overlay_json`;
- for PDF page-pair refresh, the worker streams page-store records and only
  materializes overlays matching the current `(page_a, page_b)` pair;
- page navigation clears stale all-overlay state when a paged store exists, so
  page switches defer to the page-store worker instead of filtering an already
  materialized full list.

P5-A verification added targeted tests for:

- ordered/corrupt/missing overlay-page store behavior;
- large viewer packages avoiding full legacy overlay duplication;
- full-zone rebuild using `paged_overlay_store`, reporting declared vs
  materialized overlay counts, and avoiding full overlay cache retention for a
  PDF page-pair subset.

The 2026-05-27 P5-B slice tightened the GUI hot paths around the page store:

- initial drawing selection can read a bounded `OverlayPageStore.iter_initial`
  slice instead of returning to the legacy full-list helper;
- PDF page-pair tree refresh proves `overlay_load_strategy=paged_overlay_store`,
  selected-page materialization only, no stale leaves, and no full overlay cache
  retention;
- `PairPreviewRenderWorker` is page-store aware for PDF pairs and enriches only
  the current page pair, without reading or rewriting the legacy `overlay_json`;
- auto-advance, zone-clustering toggle, lightweight overlay push, and progress
  badges avoid synchronous full overlay loading when declared counts or active
  paged overlays are enough;
- the opt-in P5 benchmark now instruments exact legacy JSON reads and overlay
  page-file reads, with a 102400-overlay / 100-page-pair default gate requiring
  first visible = 1 page file and selected page-pair = 2 page files.

Remaining P5 risk is still real, but narrower: `_viewer_overlays_for_pair_v2`
remains a compatibility escape hatch for explicit full-list operations such as
exports/reports, non-PDF full-tree rebuilds still materialize full overlay
lists, and cache budgets are not yet unified across all render/viewer cache
families. The next performance step should therefore finish these explicit
contracts before claiming memory-stable overlay/tile streaming:

1. Paged overlay store:
   - input: large overlay JSON or stream;
   - output: page/tile-indexed overlay shards plus compact index;
   - gate: 100k overlay worker path does not materialize the whole overlay list
     when only a page/tile viewport is requested.
2. Byte-aware cache budget:
   - input: PDF DisplayList, image tile, DXF index, and overlay-cache entries;
   - output: shared estimated-byte accounting and eviction reason events;
   - gate: 30 minute navigation soak reaches a plateau instead of linear RSS
     growth.

P4-A/P4-B/P4-C may be used as regression guards for first-paint responsiveness,
initial visible-tile payload control, and on-demand pan/zoom tile laziness. They
are not sufficient evidence for post-first-paint overlay memory stability or
long-running RSS plateau behavior.

The 2026-05-27 P5-C slice covers the first byte-aware cache contract and the
most obvious remaining full-overlay hot-path escapes:

- `pdf_display_list_cache` now tracks estimated bytes, byte-limit eviction,
  hit/miss/eviction counters, and worker RSS snapshots. Operators can tune it
  with `DRAWING_COMPARE_DISPLAY_LIST_CACHE_MB` or the broader
  `DRAWING_COMPARE_RENDER_CACHE_MB`.
- selected-zone PDF crops use a single `get_display_list_entry()` lookup so
  page rect access does not double-count cache hits. `RenderResult`,
  `perf_events`, GUI `viewer_perf.jsonl`, `viewer_perf_summary`, and
  `benchmark_zone_render.py` now expose DisplayList render count, hit/miss
  count, byte limit, estimated retained bytes, eviction count, worker RSS, and
  PIL fallback count.
- lightweight overlay push no longer falls back to `_viewer_overlays_for_pair_v2`
  for inactive pairs, and first selection defers unknown large legacy
  `overlay_json` files by file size even when `overlay_total_count` metadata is
  missing.

P5-C does not finish the shared cache budget across DXF render indexes, image
tiles, overlay caches, and DisplayLists. The remaining P5-D work should unify
those per-cache estimates under one budget event stream and add a real soak gate
that proves RSS plateau over long navigation sessions.

The 2026-05-27 P5-D slice closes the next in-process cache risk for selected
zone rendering:

- `cache_budget.py` is now the shared env-resolution point for cache byte caps:
  specific cache env vars take priority, then `DRAWING_COMPARE_RENDER_CACHE_MB`,
  then a local default.
- `DrawingRenderIndex` now carries `estimated_bytes`; the DXF index cache tracks
  total estimated retained bytes, byte-limit eviction, evicted bytes, hit/miss
  counters, entry max bytes, last eviction reason, and worker RSS snapshots.
- Entry-count eviction remains based on rebuild cost
  (`entity_count * render_time_ms`), while byte-limit pressure evicts oldest
  entries separately. This prevents a large cheap-to-rebuild index from being
  kept only because it is large.
- DXF source-render paths now emit per-request delta telemetry through
  `RenderResult.to_dict()`, `perf_events.jsonl`, GUI `viewer_perf.jsonl`, and
  `viewer_perf_summary` Korean status output. Cached zone-crop artifacts do not
  replay stale DXF cache lookups.
- Regression coverage now fixes the shared env fallback, DXF byte eviction,
  cache stat resets, DXF render event forwarding, GUI viewer perf pass-through,
  and viewer summary aggregation.

Remaining after P5-D:

- image tile cache and visible tile manifests still need byte payload telemetry
  and, later, disk-retention enforcement;
- GUI overlay cache has byte eviction but is still not normalized to the shared
  budget helper/event schema;
- the final acceptance evidence still needs the long navigation soak gate that
  proves RSS plateau with PDF DisplayList, DXF index, tile, and overlay caches
  active together.

The 2026-05-27 P5-E slice closes the telemetry/budget normalization part of the
remaining tile/overlay risk:

- `write_pair_tile_cache()` and `write_pair_visible_tile_cache()` now record
  actual materialized tile PNG bytes, overlay-tile JSON bytes, combined
  estimated payload bytes, cache byte limit, and `eviction_count=0` in the
  additive pair manifest fields. This covers both full-pyramid and
  visible-first sparse tile paths without increasing per-tile manifest detail.
- `DRAWING_COMPARE_TILE_CACHE_MB` can override the tile byte-limit telemetry;
  otherwise it falls back to `DRAWING_COMPARE_RENDER_CACHE_MB`, then the
  existing `viewer_memory_budget_mb` option.
- `pair_render`, `package_tile_write`, and `visible_tile_window_materialise`
  events forward the tile payload byte fields, and `viewer_perf_summary`
  records tile cache max payload bytes, byte limit, and eviction count.
- GUI overlay cache byte limits now resolve through the shared cache budget
  helper with `DRAWING_COMPARE_GUI_OVERLAY_CACHE_MB`, then
  `DRAWING_COMPARE_RENDER_CACHE_MB`, then the 8 MiB default.
- overlay cache eviction events now emit namespaced `overlay_cache_*` fields;
  the summary reader accepts both the new names and legacy generic fields for
  compatibility.

Remaining after P5-E:

- tile cache eviction is still telemetry-only (`eviction_count=0`) and does not
  yet delete old disk tile payloads;
- a final navigation soak must still run with PDF DisplayList, DXF index,
  tile payloads, and GUI overlay cache enabled together to prove RSS and disk
  cache growth plateau under real review behavior.

The 2026-05-27 P5-F validation slice ran the synthetic GUI hot-path/navigation
soak gate with PDF DisplayList, DXF index, tile payload telemetry, and GUI
overlay cache accounting enabled together:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python scripts\benchmark_workbench_gui_hotpath.py `
  --include-navigation-soak `
  --navigation-soak-pairs 100 `
  --navigation-soak-visits 100 `
  --navigation-soak-overlays-per-pair 1000 `
  --navigation-soak-rss-slope-target-mb-per-100 5 `
  --output .benchmarks\p5f_navigation_soak.json
```

P5-F acceptance evidence:

- overall benchmark status: `passed`, elapsed `8.258 s`;
- cold PDF pair-selection p95: `9.616 ms` against the `2000 ms` gate;
- cached PDF pair-selection p95: `8.106 ms` against the `300 ms` gate;
- overlay cache probe retained at most `8` pairs, `6,318,240` estimated bytes
  against the `8,388,608` byte cap, with `92` expected evictions;
- navigation soak completed `100/100` visits;
- navigation soak selection p95: `8.432 ms` against the `300 ms` gate;
- event-loop gap p95/max: `20.638 ms` / `82.439 ms`, with zero gaps above
  `500 ms`;
- RSS slope: `0.002 MB / 100 visits` against the `5.0 MB / 100 visits`
  gate;
- positive RSS end delta and tail-peak delta were both `0.004 MB`, well below
  the `64 MB` / `128 MB` gates.

Interpretation:

- the in-process cache families now have enough shared byte accounting and
  viewer telemetry to detect selection/navigation regressions early;
- the synthetic GUI soak shows plateau behavior for overlay memory and event
  loop responsiveness under a 100-pair/100k-overlay navigation pattern;
- this does not yet prove production-corpus behavior because it does not
  exercise real DWG/PDF geometry diversity, OCR/vector-render edge cases, or
  long wall-clock operator sessions.

Remaining after P5-F:

- implement real disk tile-cache retention/eviction instead of reporting
  `eviction_count=0`;
- run the same soak gates on the real validation corpus and retain the
  benchmark JSON, viewer perf summary, runtime budget, selected-zone evidence,
  and screenshots as release evidence;
- add failure triage rules that map a gate failure to the responsible cache
  family: GUI overlay cache, PDF DisplayList cache, DXF index cache, tile
  payload cache, or telemetry spool overhead.

The 2026-05-28 P5-G1 slice implements the first real disk tile-cache retention
contract:

- full-pyramid and visible-first tile writes now enforce the resolved
  `DRAWING_COMPARE_TILE_CACHE_MB` / `DRAWING_COMPARE_RENDER_CACHE_MB` byte cap
  immediately after writing the current pair;
- eviction is pair-directory scoped: `tiles/{pair}` and
  `overlay_tiles/{pair}` are deleted together, while the just-written pair is
  protected even if it alone exceeds the cap;
- `tiles_manifest_is_current()` now verifies the per-pair
  `tile_manifest.json` still exists before treating a cache-key match as a
  hit, preventing stale manifest records from masking evicted payloads;
- cache hits touch the per-pair manifest so the retention order behaves as a
  simple LRU without rewriting the full consolidated manifest;
- JSONL materialisation and legacy manifest merge filter out records whose
  per-pair tile payload was already evicted;
- retention telemetry is additive in existing manifests/events:
  `cache_estimated_bytes_before_eviction`,
  `cache_retained_estimated_bytes`, `evicted_pair_count`,
  `evicted_estimated_bytes`, `evicted_pairs`, and `eviction_reason`;
- `viewer_perf_summary` now aggregates retained bytes, evicted pair count,
  evicted bytes, and eviction reasons, and the Korean status line reports tile
  cache eviction when it happens.

P5-G1 validation evidence:

- targeted tile/streaming/summary tests: `53 passed`;
- package/workbench/tile/summary regression slice: `120 passed`;
- `py_compile` passed for the changed tile cache, package, summary, GUI, and
  test modules.

Remaining after P5-G1:

- add realset release gates that read `viewer_perf_summary`,
  `tiles_manifest.json`, runtime budget, selected-zone evidence, and
  screenshots together before claiming production cache plateau behavior;
- add explicit orphan/stale/prune-error counters if field runs show external
  deletion, Windows file locks, or interrupted writes in the persistent cache.

The 2026-05-28 P5-G2 slice adds that opt-in synthetic disk tile-retention soak
to the GUI hot-path benchmark:

- `benchmark_workbench_gui_hotpath.py --include-p5-tile-retention-soak` now
  writes multiple noisy pair tile payloads under a deliberately small tile cache
  byte cap, keeps one pair hot through manifest cache hits, materialises the
  append-only tile manifest, and verifies the disk payload after eviction;
- the gate proves retained tile/overlay bytes stay under the configured cap,
  eviction count and evicted bytes are positive, stale manifest records are
  zero, orphan tile/overlay pair directories are zero, a repeatedly accessed hot
  pair survives, and at least one evicted pair is no longer reported as a cache
  hit;
- the probe also records tile write plus retention-prune p95 and event-loop
  heartbeat p95/`>500 ms` counts so disk cleanup cannot silently become the next
  GUI freeze source.

P5-G2 validation evidence:

```powershell
python scripts\benchmark_workbench_gui_hotpath.py `
  --selection-runs 1 --pairs 3 --overlays-per-pair 5 `
  --selection-overlay-count 5 `
  --full-tree-overlays 1000 `
  --page-nav-overlays 1000 `
  --rapid-page-nav-overlays 1500 `
  --include-p5-tile-retention-soak `
  --p5-tile-retention-pairs 6 `
  --p5-tile-retention-image-size 128 `
  --p5-tile-retention-byte-limit-mb 0.2 `
  --output .benchmarks\p5g_tile_retention_probe.json
```

- overall benchmark status: `passed`;
- retained tile/overlay bytes: `157,744` against the `209,715` byte cap;
- retained pair count: `2` after writing `6` pairs;
- eviction count / evicted pairs / evicted bytes: `4` / `4` / `315,488`;
- hot-pair retained: `true`;
- first evicted pair cache miss: `true`;
- stale manifest count: `0`;
- orphan pair directories / orphan bytes: `0` / `0`;
- write-plus-prune p95: `37.714 ms`;
- event-loop gap p95: `81.602 ms`, with zero gaps above `500 ms`.

Remaining after P5-G2:

- promote the synthetic P5-G2 gate into a production-corpus release gate by
  running it together with `validate_drawing_compare_realset.py` evidence:
  `viewer_perf_summary`, runtime budget, selected-zone evidence, screenshot
  nonblank checks, and tile manifest payload validation;
- add explicit `prune_error_count`, `stale_manifest_count`, and
  `orphan_payload_bytes` summary fields only if realset or field runs show
  Windows file locks, external deletion, or interrupted cache writes;
- define the long-session persistent-cache policy separately from per-run
  package caches so customer outputs are reproducible while the shared viewer
  cache remains bounded.

The 2026-05-28 P5-G3 slice promotes the P5-G2 risk model into an opt-in
realset validation gate:

- `validate_drawing_compare_realset.py --p5-g3-realset-gate` now records a
  `p5_g3_realset_gate` payload in `validation_summary.json` and makes the
  quality gate fail if the combined release evidence is incomplete;
- the gate checks completed comparison pairs, runtime-budget evidence,
  `viewer_perf_summary` events, selected-zone render evidence, nonblank visual
  output evidence, and tile-manifest payload consistency together instead of
  accepting any single metric in isolation;
- nonblank evidence prefers an existing `nonblank_pixel_probe.json` and falls
  back to a bounded scan of known visual-output directories only, so ordinary
  validation runs avoid broad recursive image scanning;
- tile-manifest evidence validates root and per-pair manifests, stale manifest
  counts, and orphan tile/overlay payload bytes, tying the disk-cache retention
  work to the actual viewer package exported for review;
- manifest-driven realset runs propagate the P5-G3 flags to child validation
  runs and can enforce a minimum dataset count before the aggregate gate passes.

The first P5-G3-enabled PDF/PDF validation run found a real packaging bug:
when `viewer_cache_root` and `viewer_root` were the same directory, the first
manifest materialisation removed `tiles_manifest.jsonl`; the second
materialisation then wrote an empty root `tiles_manifest.json`. That made the
viewer package contain tile payloads while the root manifest reported zero
pairs. `viewer_package.py` now preserves the JSONL stream until the final
same-root materialisation, keeping the packaged manifest consistent with the
payload on disk.

P5-G3 validation evidence:

- `py_compile` passed for the validator, viewer package, and validator tests;
- P5-G3 validator regression slice: `37 passed`;
- tile/package/perf-summary regression slice: `109 passed`;
- GUI hot-path benchmark unit slice: `18 passed`;
- the real PDF/PDF validator test now runs with `--p5-g3-realset-gate` and
  asserts `p5_g3_realset_gate.status == "passed"`.

Remaining after P5-G3:

- run the gate against a customer-approved production corpus of 20-50 sheets
  and retain `validation_summary.json`, `viewer_perf_summary`,
  `runtime_budget`, selected-zone evidence, nonblank probe output, screenshots,
  and `tiles_manifest.json` as release artifacts;
- decide whether production acceptance should require observed tile eviction
  under a constrained byte cap or whether P5-G2 synthetic eviction plus P5-G3
  realset manifest consistency is sufficient for the first customer release.

The 2026-05-28 P5-G4 slice wires the P5-G3 evidence gate into the final
release/audit path:

- `audit_drawing_compare_mvp_exit.py` now has
  `--require-p5-g3-realset-gate` and a dedicated
  `p5_g3_realset_release_gate` check;
- `customer_grade` audits enable that requirement automatically, while
  blocked-only validation outputs with `completed_pairs=0` are ignored so
  CAD/PDF-blocking evidence folders do not fail the P5-G3 requirement;
- the audit check rejects missing, `not_requested`, failed, or incomplete
  P5-G3 gate payloads and surfaces the failing evidence domain, including
  `tile_manifest`, `nonblank`, `selected_zone_evidence`, `runtime_budget`, and
  `viewer_perf_summary`;
- `release_drawing_compare_workbench.py` adds `--p5-g3-realset-gate` to the
  default realset validation command when selected-zone evidence is enabled,
  adds `--require-p5-g3-realset-gate` to the generated customer-grade audit
  command, and rejects the contradictory combination of customer-grade audit
  generation with `--skip-selected-zone-evidence`;
- release templates and the prompt-to-artifact checklist now name the P5-G3
  release gate explicitly.

P5-G4 validation evidence:

- `py_compile` passed for the audit script, release script, and changed tests;
- targeted P5-G4 audit/release tests: `8 passed`;
- full MVP exit audit test file: `91 passed`;
- full release orchestrator test file: `24 passed`.

Remaining after P5-G4:

- run the P5-G3/P5-G4 release gate against a customer-approved production
  corpus of 20-50 sheets and retain the full artifact set as release evidence;
- if the production corpus does not naturally trigger tile eviction, decide
  whether to add a controlled low-byte-cap production run to prove realset
  eviction behavior in addition to the P5-G2 synthetic soak.

The 2026-05-28 P5-G5 slice adds machine-readable field triage hints to the
P5-G3 audit failure path:

- `p5_g3_realset_release_gate` now attaches `triage_hints` evidence for failed
  P5-G3 domains;
- the initial ownership map is:
  `comparison` -> realset matching/comparison completion,
  `runtime_budget` -> RuntimeBudgetSampler or long-running pipeline budget,
  `viewer_perf_summary` -> viewer telemetry JSONL/summary emission,
  `selected_zone_evidence` -> selected-zone render worker/fallback pipeline,
  `nonblank` -> screenshot/nonblank pixel evidence capture, and
  `tile_manifest` -> viewer package manifest materialisation or disk tile cache
  retention;
- tile-manifest failure tests now assert the audit evidence names the package
  manifest/disk-cache owner rather than leaving operators to infer the subsystem
  from a raw JSON failure.

P5-G5 validation evidence:

- `py_compile` passed for the audit script and audit tests;
- targeted tile-manifest triage test: `1 passed`.

Remaining after P5-G5:

- run the P5-G3/P5-G4 release gate against a customer-approved production
  corpus of 20-50 sheets and retain the full artifact set as release evidence;
- if the production corpus does not naturally trigger tile eviction, decide
  whether to add a controlled low-byte-cap production run to prove realset
  eviction behavior in addition to the P5-G2 synthetic soak.

The 2026-05-28 P5-G6 slice makes that controlled low-byte-cap production run
auditable instead of relying on an operator's interpretation of viewer logs:

- the realset validator now supports an opt-in tile-eviction requirement through
  `--p5-g6-require-tile-eviction --p5-g6-min-tile-evicted-pairs <n>
  --p5-g6-min-tile-evicted-bytes <n>`; the older P5-G3 flag spellings are kept
  as compatibility aliases because the evidence still lives inside the
  `p5_g3_realset_gate` payload;
- the eviction requirement implicitly activates the P5-G3 realset quality gate,
  so a direct validator invocation with only the eviction flag cannot silently
  become a no-op;
- the tile-manifest evidence records whether eviction was required, the minimum
  required evicted pair/byte counts, and the observed
  `tile_cache_evicted_pair_count` / `tile_cache_evicted_estimated_bytes` from
  viewer performance summaries;
- the MVP exit audit and release orchestrator expose matching
  `--require-p5-g6-tile-eviction` aliases and fail unless every completed
  validation output both required and observed the configured eviction evidence;
- routine `customer_grade` exit still requires P5-G3/P5-G4 realset evidence by
  default, but does not require eviction by default because a healthy production
  run may fit under the tile-cache byte cap without evicting.

P5-G6 validation evidence:

- `py_compile` passed for the validator, audit, release orchestrator, and
  changed tests;
- combined validator, audit, and release unit test files passed after the alias
  and no-op hardening changes: `162 passed in 13.38s`.

Remaining after P5-G6:

- run the customer-approved 20-50 sheet production corpus with the P5-G3/P5-G4
  release gate and retain the complete audit artifact set;
- run a separate controlled P5-G6 low-byte-cap probe, normally by lowering
  `DRAWING_COMPARE_TILE_CACHE_MB`, when the release claim includes realset tile
  eviction rather than only synthetic P5-G2 soak evidence;
- keep the normal customer-grade run and the forced-eviction run separate in
  evidence naming so operators can distinguish natural production performance
  from cache-pressure proof.

The 2026-05-28 P5-G7 slice makes the low-byte-cap probe reproducible and
audit-enforced:

- `validate_drawing_compare_realset.py` now accepts `--p5-g6-tile-cache-mb`,
  applies it to `DRAWING_COMPARE_TILE_CACHE_MB` before viewer tile generation,
  propagates it through manifest child runs, and records both the configured
  value and observed environment value in `p5_g3_realset_gate.evidence.
  tile_manifest`;
- `release_drawing_compare_workbench.py` passes the same cap into the validator
  command, injects it into the `realset_validation` and
  `workbench_acceptance_smoke` subprocess environments, and records the
  `env_overrides` in release step metadata;
- `audit_drawing_compare_mvp_exit.py` accepts `--p5-g6-tile-cache-mb` and, when
  tile eviction is required, fails unless validation evidence shows matching
  `configured_tile_cache_mb`, `tile_cache_env_mb`, and a `byte_limit` matching
  `MB * 1024 * 1024` with a one-byte tolerance;
- release templates now show the full forced-eviction command, including the
  cap value, instead of relying on an out-of-band environment variable.

P5-G7 validation evidence:

- `py_compile` passed for the validator, audit, release orchestrator, and
  changed tests;
- targeted P5-G7 validator/release/audit tests passed: `8 passed in 2.54s`.
- full related unit files passed after the cap-audit hardening:
  validator `42 passed`, MVP exit audit `95 passed`, release orchestrator
  `29 passed`.

Remaining after P5-G7:

- run the customer-approved 20-50 sheet production corpus with standard
  customer-grade P5-G3/P5-G4 gates;
- if the release claim includes realset tile eviction, run the separate P5-G7
  forced-eviction command with an explicit `--p5-g6-tile-cache-mb` value and
  retain the release manifest, validation summary, audit JSON, viewer perf
  summary, and tile manifest together.

The 2026-05-28 P5-G8 slice preserves that separate forced-eviction proof in
customer evidence tooling without letting it inflate the customer corpus:

- `prepare_drawing_compare_customer_evidence.py` now accepts
  `--p5-g7-tile-eviction-proof-dir`, `--require-p5-g7-tile-eviction-proof`,
  and `--p5-g6-tile-cache-mb`, and writes a dedicated
  `p5_g7_forced_tile_eviction` manifest block;
- proof runs are supporting evidence only: they are not included in
  `sheet_count`, `completed_pairs`, format coverage, ground-truth row matching,
  or selected-zone/customer readiness summaries;
- if a forced-eviction proof is accidentally supplied through `--results-dir`,
  prepare-time readiness fails with an explicit instruction to pass it through
  `--p5-g7-tile-eviction-proof-dir`;
- `inventory_drawing_compare_customer_evidence.py` identifies explicit
  forced-eviction outputs only when `p5_g3_realset_gate.requested` and
  `tile_manifest.require_eviction` are true, keeps non-forced tile-cache
  metadata as normal customer evidence, and adds optional
  `--require-p5-g7-forced-tile-eviction` inventory gating;
- the inventory recommended prepare command forwards passing proof directories
  and the expected cap, but does not attach an unrelated release manifest
  automatically.

P5-G8 validation evidence:

- `py_compile` passed for the prepare/inventory scripts and changed tests;
- full prepare manifest unit file: `40 passed`;
- full customer-evidence inventory unit file: `26 passed`;
- a read-only multi-agent review found and the implementation fixed three
  risks: proof-in-`--results-dir` corpus inflation, overly broad proof
  candidate detection, and unrelated release-manifest attachment.

Remaining after P5-G8:

- run the customer-approved 20-50 sheet production corpus with standard
  customer-grade P5-G3/P5-G4 gates;
- run a separate forced-eviction proof only when the release claim includes
  realset tile eviction, then attach it through the P5-G8 proof path so the
  normal corpus metrics remain uncontaminated;
- decide whether the release package should expose a single closeout command
  that chains standard corpus validation, optional P5-G7 proof validation, P5-G8
  manifest preparation, and final audit generation.

The 2026-05-28 P5-G9 slice implements that closeout command and makes the
P5-G7 proof visible in the final audit without changing the P5-G3/P5-G6 gate
semantics:

- `scripts/closeout_drawing_compare_customer_evidence.py` is a packaged
  one-command runner. It can chain standard corpus validation manifests,
  existing standard result dirs, optional P5-G7 proof manifests or proof dirs,
  inventory generation, customer-evidence manifest preparation, and final
  customer-grade audit generation;
- the runner keeps the routing invariant explicit: standard corpus outputs are
  passed as `--results-dir`, while forced tile-eviction outputs are passed only
  as `--p5-g7-tile-eviction-proof-dir`. The final audit command receives only
  standard corpus `--results-dir` values;
- when `--source-checkout` is supplied, the runner prefers source-checkout
  evidence scripts so provenance generation and verification can use the
  `src.services.comparison.manifest_provenance` helper even from a packaged
  release `cli` folder;
- `release_drawing_compare_workbench.py` now compiles, copies, manifests, and
  documents the closeout runner, and the customer manifest template includes
  the optional `p5_g7_forced_tile_eviction` block;
- `audit_drawing_compare_mvp_exit.py` adds
  `p5_g7_forced_tile_eviction_manifest`, which fails inconsistent required
  proof blocks but does not let a passed P5-G7 manifest satisfy
  `--require-p5-g3-tile-eviction`. The latter remains based on actual
  validation-summary tile-manifest evidence.

P5-G9 validation evidence:

- `py_compile` passed for the closeout runner, release script, audit script,
  and changed tests;
- new closeout runner unit test: `1 passed`;
- full MVP exit audit unit file: `98 passed`;
- full release orchestrator/template unit file: `29 passed`.

Remaining after P5-G9:

- run the customer-approved 20-50 sheet production corpus with standard
  customer-grade gates using either existing validation outputs or the closeout
  runner's `--standard-validation-manifest` path;
- if and only if the release claim includes realset tile eviction, run or attach
  the separate P5-G7 forced-eviction proof through the closeout runner proof
  options;
- run the generated `mvp_exit_audit.json` review against the real customer
  corpus, not synthetic or unit-test fixtures.

The 2026-05-28 P5-G10 slice makes the P5-G9 closeout runner usable as a
pre-execution readiness gate before the real customer corpus is available:

- `closeout_drawing_compare_customer_evidence.py` now has `--dry-run` and
  `--plan-json <closeout_plan.json>`. The dry run validates inputs and writes
  the exact command plan without launching validation, inventory, prepare, or
  audit subprocesses;
- closeout preflight now fails early when the supplied source checkout is
  missing `scripts/validate_drawing_compare_realset.py`, the evidence scripts,
  or `src/services/comparison/manifest_provenance.py`. This prevents a packaged
  `cli` run from generating a customer manifest that later fails provenance
  verification;
- existing `--standard-results-dir` and `--p5-g7-tile-eviction-proof-dir`
  values must contain both `validation_summary.json` and `_SUCCESS`, so stale or
  partial validation folders are rejected before the final audit command;
- closeout applies `DRAWING_COMPARE_TILE_CACHE_MB` only to P5-G7 proof
  validation subprocesses. Standard customer-corpus validation steps run without
  the low-byte-cap proof env override, so the proof condition cannot contaminate
  the normal corpus evidence;
- preflight rejects duplicate standard result dirs, generated-output collisions,
  forced proof outputs passed as standard corpus, and non-forced outputs passed
  as required P5-G7 proof dirs;
- the plan JSON records the corpus/proof routing invariants:
  `proof_dirs_excluded_from_final_audit_results_dir=true` and
  `final_audit_results_dirs_equal_standard_result_dirs=true`;
- manifest-driven dry runs include the planned standard validation and P5-G7
  proof validation steps before inventory/prepare/audit, so an operator can
  review output locations and low-byte-cap proof flags before executing.

P5-G10 validation evidence:

- `py_compile` passed for the closeout runner and closeout tests;
- closeout runner unit file: `9 passed`;
- release template tests were updated to require the dry-run/source-checkout
  guidance in the customer-facing packet.

Remaining after P5-G10:

- run `closeout_drawing_compare_customer_evidence.py --dry-run --plan-json`
  against the real customer-approved corpus inputs and review all preflight
  issues before launching the full closeout;
- run the full closeout on the customer-approved 20-50 sheet corpus, attach the
  P5-G7 proof only if the release claim includes realset tile eviction, and
  retain `closeout_plan.json`, `inventory.json`, `customer_evidence_manifest.json`,
  and `mvp_exit_audit.json` together as final release evidence.

The 2026-05-28 P5-G11 slice makes closeout subprocess failures
release-reviewable instead of console-only:

- `closeout_drawing_compare_customer_evidence.py` now writes
  `closeout_failure.json` by default, or the path supplied with
  `--failure-json <closeout_failure.json>`, when any validation, inventory,
  prepare, or final-audit subprocess fails;
- the failure report records `failure_kind`, `failed_step_index`,
  `failed_step_name`, `failed_returncode`, `failed_step.index/name/cwd`,
  `failed_step.command`, `failed_step.returncode`, `failed_step.elapsed_s`,
  `failed_step.env_overrides`, `failed_step.command_context`,
  `completed_steps`, `remaining_steps`, `plan_invariants`, `triage_hints`,
  and `stdout_stderr.capture_mode=inherited_console`;
- `subprocess_nonzero_exit` and subprocess launch `spawn_error` are separated,
  so missing Python/script launch failures are not misread as validator quality
  failures;
- if a test fake or future runner supplies subprocess stdout/stderr values, the
  report preserves bounded `stdout_tail` and `stderr_tail`; normal closeout runs
  still stream subprocess output to the parent console;
- the generated release README and `customer_evidence_closeout_packet.md` now
  tell reviewers to retain `closeout_failure.json` with `closeout_plan.json` and
  the parent console log before rerunning.

P5-G11 validation evidence:

- `py_compile` passed for the closeout runner, release script, and changed
  closeout/release tests;
- closeout runner unit file: `11 passed`;
- release orchestrator/template unit file: `29 passed`.

Remaining after P5-G11:

- run the closeout dry-run against the real customer-approved 20-50 sheet corpus
  inputs, preserve `closeout_plan.json`, and resolve all preflight issues;
- run the full closeout on the real corpus. If it fails, retain
  `closeout_failure.json`, `closeout_plan.json`, and the parent console log as
  the debugging packet before rerunning;
- final completion still requires the real corpus `inventory.json`,
  `customer_evidence_manifest.json`, and `mvp_exit_audit.json` with
  customer-grade status `passed`.

The 2026-05-28 P5-G12 slice exposes closeout readiness as a release-review
artifact instead of a transient dry-run side effect:

- `closeout_drawing_compare_customer_evidence.py` now writes
  `closeout_readiness.json` by default, or `--readiness-json
  <closeout_readiness.json>`, before launching any subprocess;
- when preflight fails, the report uses `status=preflight_failed` and records
  `preflight.issue_count`, `preflight.issues`, input/output paths, and
  `plan.available=false`, so missing source checkout/provenance, partial
  validation outputs, duplicate result dirs, or proof/corpus routing mistakes
  are retained as an artifact;
- when preflight passes, the report uses `status=ready_for_closeout` and records
  `outputs.plan_json`, `outputs.readiness_json`, `outputs.failure_json`,
  `outputs.inventory_json`, `outputs.customer_evidence_manifest`,
  `outputs.audit_json`, `routing_expectations.*`, `plan.step_count`,
  per-step command context including inventory `--root`, and the corpus/proof
  routing invariants;
- the release README, closeout packet, and prompt-to-artifact checklist now
  require retaining `closeout_readiness.json` with `closeout_plan.json`,
  `inventory.json`, `customer_evidence_manifest.json`, and `mvp_exit_audit.json`
  as the final closeout evidence packet.

P5-G12 validation evidence:

- `py_compile` passed for the closeout runner, release script, and changed
  closeout/release tests;
- closeout runner unit file: readiness pass/fail and routing-failure readiness
  coverage included in the targeted file;
- release template tests require `closeout_readiness.json`,
  `preflight.issue_count=0`, output path fields, routing expectation fields,
  and final-audit routing invariants in the README, closeout packet, and
  prompt-to-artifact checklist.

Remaining after P5-G12:

- run the actual customer-approved 20-50 sheet closeout dry-run and attach
  `closeout_readiness.json` plus `closeout_plan.json`;
- proceed to full closeout only when readiness is `ready_for_closeout`,
  `preflight.issue_count=0`, and plan invariants are true;
- preserve any `preflight_failed` readiness report or `closeout_failure.json`
  with the parent console log if reruns are needed.

## 11. 2026-05-28 Performance Degradation Deep Planning Addendum

This section consolidates the latest multi-agent planning pass. The purpose is
to turn the user's "the program feels slower" report into a concrete execution
plan that can be delegated step by step, measured, and release-gated. The
central rule is that performance is a product correctness axis: if results are
late, blank, stale, memory-growing, or only visible after heavy packaging, the
comparison feature is not complete even when the entity diff is correct.

### 11.1 Multi-Agent Planning Inputs

| Agent focus | Planning conclusion |
| --- | --- |
| Performance and memory | Existing synthetic gates are useful but not enough. Add a production-corpus evidence contract with RSS slope, cache retained bytes, event-loop gaps, tile/orphan payloads, and selected-zone blank/stale counts. |
| PDF-first rendering | PDF-first should be the normal viewer strategy, but only as a visual asset layer. CAD entity diff remains the truth layer. CAD-to-PDF backends stay disabled by default until provenance, license, cache, timeout, and coordinate gates pass. |
| QA and release | The roadmap needs an agent execution board, customer-corpus composition rules, machine-readable closeout readiness audit, and strict exit criteria that forbid completion before real corpus evidence passes. |

### 11.2 Final Goal and Non-Goals

Goal:

- comparison results become reviewable quickly, before heavy export/package work;
- pair/page/zone navigation stays responsive under large drawings and long use;
- selected-zone render failure never produces a blank viewer;
- memory, handles, spool, tile payloads, and cache retention reach a plateau;
- multi-detail drawings are region-matched or review-gated, not silently
  whole-modelspace compared;
- final release evidence can be reviewed from artifacts, not operator claims.

Non-goals:

- do not replace CAD/DWG/DXF truth comparison with CAD-to-PDF or raster output;
- do not enable a converter through environment variables alone;
- do not run converter, full tile pyramid, full overlay tree, or vector focus on
  the first-review hot path;
- do not call synthetic benchmark success equivalent to customer corpus success.

### 11.3 Root-Cause Hypothesis Map

| Hypothesis | How it appears to the user | Required measurement | Owner slice |
| --- | --- | --- | --- |
| First review waits for packaging | result list appears late or never | `first_review_ready_s`, `package_complete_s`, deferred artifact count | P5-G14 |
| GUI selection fan-out | clicking a drawing freezes UI | `pair_selection_gui_block_ms`, event-loop gap, worker spawn count, stale result count | P5-G16 |
| PDF/page render storm | PDF-first makes opening pages slow | page render ms, document open count, effective DPI, cache hit, pixel budget | P5-G16 |
| Selected-zone duplicate work | selected area is slow or blank | request id, backend order, crop/vector elapsed, blank count, fallback reason | P5-G15 |
| Cache retention or leak | app gets slower after repeated use | RSS slope, retained bytes by cache namespace, file/GDI/User handles | P5-G14/P5-G16 |
| Tile/overlay materialization | large drawings spend time writing viewer assets | tile count, overlay tile bytes, retained bytes, stale manifest, orphan bytes | P5-G16 |
| Multi-detail scaling | wrong detail drawing is compared | detected region count, match confidence, whole-modelspace fallback count | R8/P6 |
| Telemetry overhead | measuring makes the app slower | sampler tick p95, spool scan p95, summary elapsed, scanned bytes | P5-G14 |
| CAD-to-PDF conversion churn | viewer improves but latency/RSS worsens | conversion queue wait, timeout/cancel, cache key reuse, duplicate conversion count | PDF-first P6 |
| Closeout uncertainty | reviewer cannot tell if full closeout is safe | `closeout_readiness_audit.json status=passed` | P5-G17 |

### 11.4 Production Evidence Contract

Customer-grade performance claims require both synthetic and production-corpus
evidence. The minimum production corpus is 20-50 completed sheets/pairs with:

- CAD evidence >= 8 pairs;
- PDF evidence >= 8 pairs;
- large DWG/DXF evidence >= 2 pairs;
- multi-detail evidence >= 4 pairs;
- raster or low-quality PDF evidence >= 2 pairs;
- negative-control evidence >= 2 pairs;
- approved `dataset_strata.csv`, `review_ground_truth.csv`,
  `review_decision_truth.csv`, and substantive `operator_dry_run_notes.md`.

The release packet must retain:

- `validation_summary.json`;
- `perf_events_summary.json`;
- `viewer_perf.json` or `viewer_perf_summary`;
- `runtime_budget` with memory and spool data;
- selected-zone evidence with backend, blank, stale, and fallback fields;
- screenshot/nonblank proof;
- `tiles_manifest.json` and tile retention fields;
- benchmark JSON for each required profile;
- `closeout_plan.json`, `closeout_readiness.json`,
  `closeout_readiness_audit.json`, `inventory.json`,
  `customer_evidence_manifest.json`, and `mvp_exit_audit.json`;
- `closeout_failure.json` plus parent console log when a closeout subprocess
  fails.

Synthetic evidence can prove algorithmic regression coverage. It cannot, by
itself, satisfy customer-grade performance completion.

### 11.5 Required Benchmark Profiles

All benchmark JSON outputs must include `status`, `gates`, command arguments,
commit or source signature, machine/OS summary, cache caps, `psutil_available`,
and raw artifact references.

| Profile | Purpose | Required hard gates |
| --- | --- | --- |
| quick synthetic GUI | routine regression before merging | status passed, selection p95 within gate, no event-loop gap above hard cap |
| real PDF page navigation | proves PDF-first page load is responsive | blank count 0, cached navigation does not cold-render, event-loop gap max <= 500 ms |
| real PDF prewarm/cache | proves adjacent-page prewarm is not visible-state mutation | metadata hit, document open count not increasing on cached hit, render call count zero on hit |
| selected-zone render memory | catches crop/vector/cache duplication | blank count 0, stale wins 0, cold/hit p95 pass, RSS delta bounded |
| navigation/RSS soak | catches memory and handle leaks | RSS slope <= 5 MB / 100 visits, end delta <= 64 MB, tail peak delta <= 128 MB |
| tile retention soak | catches disk cache growth | retained bytes <= cap, stale manifest 0, orphan payload bytes 0 |
| production corpus replay | bridges synthetic and customer reality | all above domains pass on real validation/viewer artifacts |

Target command shapes after the next implementation slices:

```powershell
python scripts\benchmark_workbench_gui_hotpath.py --include-navigation-soak --output .benchmarks\p5g16_navigation_soak.json
python scripts\benchmark_workbench_gui_hotpath.py --include-p5-tile-retention-soak --output .benchmarks\p5g16_tile_retention.json
python scripts\benchmark_zone_render.py --fixture <real_or_large_fixture> --zones 20 --runs 5 --output-json .benchmarks\p5g15_zone_render_memory.json
python scripts\validate_drawing_compare_realset.py --input <realset_manifest.json> --output <validation_out> --p5-g3-realset-gate
python cli\closeout_drawing_compare_customer_evidence.py --dry-run --plan-json <closeout_plan.json> --readiness-json <closeout_readiness.json>
python cli\audit_closeout_readiness.py --readiness-json <closeout_readiness.json> --plan-json <closeout_plan.json> --out <closeout_readiness_audit.json> --require-ready
```

### 11.6 Memory Leak and Resource Plateau Spec

A leak is any monotonic growth that survives cache warmup, not only a Python
object leak. The same severity applies to unbounded caches, orphan worker
processes, open file handles, GDI/User handles on Windows, retained PDF
documents, persistent temp files, and disk tile payloads.

Required fields:

- `rss_slope_mb_per_100_visits`;
- `positive_end_delta_mb`;
- `tail_peak_delta_mb`;
- `peak_rss_mb`;
- `working_set_mb` where available;
- `open_file_handle_count`;
- `gdi_handle_count` and `user_handle_count` on Windows where available;
- `worker_process_count` and orphan worker count;
- `cache_namespace`, `cache_entry_count`, `cache_retained_estimated_bytes`,
  `cache_byte_limit`, and `eviction_reason`;
- `spool_bytes`, `temp_bytes`, `orphan_payload_bytes`.

Hard gates:

- RSS slope <= 5 MB / 100 visits after warmup;
- positive end delta <= 64 MB for the standard soak;
- tail peak delta <= 128 MB;
- retained bytes <= configured cap per cache namespace;
- stale manifests = 0;
- orphan payload bytes = 0;
- orphan worker processes = 0;
- file/GDI/User handle counts do not grow linearly after warmup.

### 11.7 Telemetry Overhead Spec

Instrumentation must not become the performance problem. High-frequency paths
must append, batch, or stream; they must not rewrite growing JSON documents on
each pan, zoom, crop, selection, or tile event.

Required fields:

- `sampler_tick_ms.p50/p95/max`;
- `spool_scan_ms.p50/p95/max`;
- `scanned_file_count`;
- `scanned_bytes`;
- `summary_input_bytes`;
- `summary_elapsed_ms`;
- `telemetry_overhead_ratio`.

Hard gates:

- sampler tick p95 <= 20 ms;
- spool scan p95 <= 200 ms on the configured output roots;
- summary elapsed <= 1000 ms for release audit summaries;
- telemetry overhead ratio <= 2%;
- customer-grade release fails if required memory/RSS measurement is missing.

### 11.8 PDF-First Technical Contract

Backend priority:

1. source PDF;
2. sidecar PDF;
3. approved CAD-to-PDF backend;
4. approved CAD-to-image backend;
5. raster fallback;
6. skeleton or relative-only pins.

`VisualAssetManifest v1.1` must include:

- `source_signature` or `source_hash`, `file_size`, and `mtime_ns`;
- `source_path_redacted`;
- `asset_kind`;
- `backend_id`, `backend_version`, `backend_license_id`;
- `plot_profile_hash`, `layout_name`, and `page_index`;
- `page_size_pt`, `pixel_size`, `dpi`, and `effective_dpi`;
- `coordinate_contract_version`;
- `bbox_coordinate_space`;
- `transform_quality = exact | estimated | relative_only | unavailable`;
- `cache_key_hash`;
- `status`, `reason_code`, and `created_at`.

Cache key contract:

```text
namespace
+ source_signature
+ backend_id
+ backend_version
+ backend_license_id
+ plot_profile_hash
+ layout_name/page_index
+ dpi/effective_dpi
+ coordinate_contract_version
+ transform_quality
```

Go/No-Go rules:

- CAD overlay on PDF background is exact only when transform quality is exact;
- `estimated` overlays must be visibly marked as estimated;
- `relative_only` allows pins or relative overlays only;
- page-index-less overlay on a multi-page asset is not exact;
- converter output must pass PDF open, page count, nonblank, provenance, timeout,
  cancel, and license gates;
- vector render failure must not clear an existing PDF/raster background.

### 11.9 Selected-Zone Failure Prevention Spec

Selected-zone rendering must be orchestrated as one request lifecycle:

1. existing background/tile crop;
2. PDF DisplayList crop;
3. PIL/raster crop;
4. CAD/vector focus as deferred enhancement only;
5. bounded fallback with relative pins and reason code.

Required fields:

- `request_id`;
- `pair_id`, `zone_id`, `region_id`;
- `backend_order`;
- `selected_backend`;
- `crop_elapsed_ms`, `vector_elapsed_ms`, `queue_wait_ms`;
- `cache_hit`;
- `blank_pixel_count` or `nonblank_status`;
- `fallback_reason_code`;
- `stale_result_dropped`, `stale_result_visible`;
- PDF DisplayList retained bytes;
- DXF index retained bytes;
- worker timeout/cancel count.

Hard gates:

- blank selected-zone count = 0;
- stale result visible count = 0;
- cache-hit selected-zone p95 <= 500 ms;
- cold PDF/image crop p95 <= 2000 ms;
- customer-grade audit fails when fallback reason is missing for a failed vector
  or source render path.

### 11.10 Multi-Detail Region Matching Spec

When a file contains multiple drawings or title frames, whole-modelspace
auto-compare is unsafe unless the detector proves the source is single-detail.

Required region candidate features:

- frame geometry and bbox;
- title text and drawing number normalization;
- page/layout identity;
- scale/rotation estimate;
- entity density and dominant layer evidence;
- spatial index query evidence;
- candidate score components.

Rules:

- if region candidates exist, silent whole-modelspace global compare is blocked;
- one-to-one auto match requires score and ambiguity thresholds;
- ambiguous or unmatched regions enter review-gated/manual override flow;
- region-local compare uses approved matches only;
- unmatched detail zones are explicit artifacts, not hidden omissions.

### 11.11 Sequential Agent Work Board

| Slice | Agent ownership | Write scope | Inputs | Outputs | Exit criteria |
| --- | --- | --- | --- | --- | --- |
| P5-G13 | planning/release agent | roadmap, WORKLOG | multi-agent findings | performance evidence contract and next-agent board | doc review passes, no code behavior change |
| P5-G14 | instrumentation agent | `runtime_budget.py`, `perf_events.py`, `viewer_perf_summary.py`, tests | existing telemetry | sampler/summary overhead fields | targeted tests pass, overhead fields present in summaries |
| P5-G15 | selected-zone benchmark agent | `benchmark_zone_render.py`, zone render tests | selected-zone fixtures | RSS/blank/fallback/cache stats benchmark JSON | blank/stale/fallback gates testable |
| P5-G16 | real-corpus replay agent | GUI benchmark/replay scripts, tests | validation output/viewer package | production replay and 30-minute soak artifacts | RSS/event-loop/cache/tile gates pass on real artifacts |
| P5-G17 | release readiness audit agent | `audit_closeout_readiness.py`, release templates/tests | `closeout_readiness.json`, `closeout_plan.json` | `closeout_readiness_audit.json` | audit `status=passed` required before full closeout |
| P5-G18 | MVP audit integration agent | `audit_drawing_compare_mvp_exit.py`, release docs/tests | benchmark JSON and production evidence | failure-domain triage in `mvp_exit_audit.json` | customer-grade audit rejects missing benchmark evidence |
| P5-G19 | customer closeout agent | no source edits unless defects found | real 20-50 sheet corpus | final closeout packet | `mvp_exit_audit.json status=passed` on real customer corpus |

Every slice must include:

- explicit write ownership;
- input artifacts;
- output artifacts;
- test command;
- benchmark command when relevant;
- rollback note;
- handoff note naming remaining risks.

### 11.12 Closeout Readiness Audit Spec

`closeout_readiness.json` is necessary but not sufficient. It must be audited by
a standalone pass/fail artifact before full closeout.

`audit_closeout_readiness.py` should check:

- readiness `schema_version == 1`;
- readiness `status=ready_for_closeout`;
- `preflight.status=passed`;
- `preflight.issue_count=0` and `preflight.issues=[]`;
- output paths for plan, readiness, failure, inventory, customer manifest, and
  final audit are present;
- plan is available and step count matches the plan file;
- inventory, prepare, and final audit steps are present in the expected order;
- proof dirs and standard result dirs do not overlap;
- final audit `--results-dir` equals only standard result dirs;
- P5-G7 proof dirs flow only through proof channels;
- `DRAWING_COMPARE_TILE_CACHE_MB` is applied only to forced proof validation
  steps;
- P5-G3/P5-G7 performance evidence requirements are reflected in the planned
  commands.

Full closeout must not run when this audit fails. A failed audit should be kept
with the readiness file as the pre-execution failure packet.

### 11.13 Agent Prompts For The Next Execution Steps

Use these prompts when delegating the next slices:

```text
P5-G14 instrumentation agent:
Inspect runtime_budget.py, perf_events.py, viewer_perf_summary.py, and related
tests. Add telemetry-overhead fields for sampler tick, spool scan, scanned
bytes/file count, summary elapsed, and overhead ratio. Keep high-frequency paths
append-only or streaming. Add tests proving summaries expose the fields and
customer-grade release can reject missing measurement.
```

```text
P5-G15 selected-zone benchmark agent:
Inspect benchmark_zone_render.py, zone_render_service.py,
pdf_display_list_cache.py, and DXF index cache tests. Extend the benchmark
contract to emit RSS deltas, blank/nonblank counts, fallback reason counts,
worker timeout/cancel counts, DisplayList retained bytes, and DXF index retained
bytes. Add gates for blank=0, stale visible=0, cache-hit p95, cold p95, and
bounded RSS delta.
```

```text
P5-G16 real-corpus replay agent:
Build a benchmark/replay mode that consumes an existing validation output or
viewer package and repeats pair, page, and selected-zone navigation without
generating synthetic fixtures. Emit event-loop, RSS slope, handle count,
cache-retained bytes, tile stale/orphan counts, and blank/stale selected-zone
counts. Add a quick mode for tests and a 30-minute soak mode for release.
```

```text
P5-G17 readiness audit agent:
Create audit_closeout_readiness.py and tests. Validate closeout_readiness.json
against closeout_plan.json, including preflight pass, output paths, step
ordering, proof/corpus separation, final-audit results-dir purity, and tile-cache
env isolation. Package the tool in the release CLI and require
closeout_readiness_audit.json status=passed in docs/checklists.
```

### 11.14 Completion Standard For Performance-Degradation Work

This work can be called complete only when:

- first-review-ready and package-complete are separately measured on the real
  corpus;
- pair selection, page navigation, selected-zone render, and real-corpus replay
  pass p95 and event-loop gates;
- blank viewer and blank selected-zone counts are zero;
- stale selected-zone or stale page result visible counts are zero;
- RSS, file handles, worker processes, spool, and tile payloads plateau after
  warmup;
- telemetry overhead is measured and below gate;
- PDF-first visual assets have provenance, license status, cache key, and
  coordinate quality;
- multi-detail candidates are matched or review-gated;
- `closeout_readiness_audit.json status=passed`;
- `mvp_exit_audit.json status=passed`;
- the passing evidence comes from the customer-approved 20-50 sheet corpus, not
  only from unit tests or synthetic benchmarks.

## 12. 2026-05-28 P5-G14 Telemetry Overhead Implementation

The P5-G14 slice turns the planning requirement "telemetry must not become the
slowdown" into emitted evidence:

- `RuntimeBudget` schema version is now `3` and carries sampler-overhead fields:
  `sampler_tick_ms`, `spool_scan_ms`, `sampler_overhead_ms`,
  `telemetry_overhead_ratio`, `spool_scan_count`, `spool_scan_file_count`, and
  `spool_scan_bytes`;
- `RuntimeBudgetSampler._sample_once()` measures its whole tick elapsed time and
  measures spool scan elapsed/file-count/byte-count separately while preserving
  the existing throttled spool-scan behavior and final forced scan;
- `perf_events_summary` now records `total_recorded_elapsed_ms` and
  `summary_overhead_ratio`, with `None` when the denominator is not measured;
- `viewer_perf_summary` schema version is now `8` and records
  `summary_source=jsonl|legacy_json|none`, `summary_input_bytes`, and
  `summary_elapsed_ms` for both JSONL and legacy pointer inputs;
- compatibility helpers still keep `_directory_size()` available while adding a
  file-counting `_directory_scan()` helper for overhead evidence.

P5-G14 validation evidence:

- `py_compile` passed for `runtime_budget.py`, `perf_events.py`,
  `viewer_perf_summary.py`, and the changed telemetry tests;
- targeted telemetry tests passed: `64 passed`;
- validator and MVP audit regression slice passed: `140 passed`.

Remaining after P5-G14:

- P5-G15 should extend selected-zone benchmark output with RSS, blank/stale,
  fallback, timeout/cancel, DisplayList, and DXF-index cache evidence;
- P5-G16 should add real-corpus replay/soak gates so the overhead fields are
  exercised against production validation outputs rather than only unit tests;
- P5-G18 should make customer-grade audit fail on missing or excessive
  telemetry-overhead evidence from the original validation summary, not from
  audit-time recomputation.

## 13. 2026-05-28 P5-G15 Selected-Zone Benchmark Evidence Implementation

P5-G15 converts `scripts/benchmark_zone_render.py` from a latency-only smoke
benchmark into a durable performance-degradation evidence producer:

- each selected-zone attempt is now recorded as a `ZoneRenderMeasurement` with
  phase, run index, zone id, request id, lifecycle, fidelity, backend, warning
  count, reason code, cache-hit flag, image byte sizes, blank/nonblank status,
  RSS before/after/delta, PDF DisplayList cache details, and DXF index cache
  details;
- `_run_pass()` returns `BenchmarkPassResult` so cold/cache-hit latency samples
  and full per-render diagnostics stay together instead of discarding the
  `RenderResult`;
- `--output-json` writes the full machine-readable benchmark artifact, defaulting
  to the text report path with `.json` suffix when omitted;
- stdout still emits a final JSON line with legacy flat latency keys so existing
  scrapers remain compatible;
- JSON schema version `2` now includes `benchmark_id`, `profile`, `status`,
  `timestamp_utc`, `source`, `args`, `environment`, `artifacts`, parseable
  `gates`, a nested `summary`, and full `measurements`;
- gates cover cold p95, cache-hit p95, render exceptions, render lifecycle
  failures, blank outputs, missing outputs, stale visible results, timeout,
  cancel, missing fallback reasons, RSS measurement presence, and cache retained
  bytes within PDF/DXF byte caps;
- synthetic small PDF fixtures now place ink more densely so a 1-zone smoke
  benchmark is not a false blank-output pass/fail edge case;
- `render_zone_pair()` cache-hit returns now preserve cached `dxf_index_cache`
  evidence in addition to cached `pdf_display_list_cache` evidence.

Important interpretation rules:

- `cache_hit_*` remains the selected-zone artifact-cache hit path, not a pure
  DisplayList or DXF index cache benchmark;
- timeout/cancel counters are passive evidence in this in-process benchmark and
  must be upgraded by P5-G16/P5-G18 when exercising subprocess or GUI worker
  cancellation paths;
- the P5-G15 synthetic benchmark can prove the evidence contract and fast
  regression behavior, but customer-grade closeout still requires real-corpus
  replay/soak evidence.

P5-G15 validation evidence:

- `py_compile` passed for `benchmark_zone_render.py`, `zone_render_service.py`,
  and the changed benchmark/service tests;
- targeted benchmark and zone-render service tests passed: `31 passed`;
- the JSON sidecar smoke test asserts `status=passed`, gate pass state,
  environment/source/artifact metadata, nested summary, full measurements,
  RSS fields, blank/stale/fallback counters, and legacy flat stdout keys.

Remaining after P5-G15:

- P5-G16 should add real-corpus replay/soak mode with RSS slope/end/peak,
  handle/process plateau, event-loop responsiveness, selected-zone blank/stale
  counts, and worker timeout/cancel evidence;
- P5-G18 should load this P5-G15 JSON contract and fail customer-grade audit on
  missing, schema-incompatible, failed, RSS-missing, blank/stale-positive,
  fallback-reason-incomplete, or cache-over-cap evidence;
- a DXF-specific or real-fixture benchmark profile is still needed before DXF
  index cache performance claims are customer-grade rather than schema-level.

## 14. 2026-05-28 P5-G16 Real-Corpus Replay/Soak Evidence Implementation

P5-G16 adds a real-corpus artifact replay gate around existing validation
outputs. It is intentionally not another synthetic drawing benchmark:

- `scripts/benchmark_real_corpus_replay.py` consumes a real
  `validation_summary.json`, resolves its viewer package root, fingerprints the
  validation summary, scans selected-zone `render_result.json` artifacts plus
  viewer page/tile/focus-tile images, and repeatedly revisits those artifacts;
- the output JSON is `benchmark_id=p5_g16_real_corpus_replay`,
  `profile=real_corpus_artifact_replay`, and records `source`, `environment`,
  `artifacts`, `corpus`, `gates`, `summary`, and sampled replay evidence;
- gates cover validation/viewer presence, customer manifest availability when
  requested, 20-50 sheet corpus bounds, DWG/DXF and PDF/PDF coverage flags,
  selected-zone/page artifact counts, replay completion, replay p95 latency,
  max per-visit gap, blank/missing/stale/fallback/timeout/cancel counts,
  RSS availability/slope/end/tail, and retained cache bytes for PDF
  DisplayList, DXF index, tile, and overlay caches;
- each gate carries `name`, `passed`, `actual`, `target`, `observed`,
  `threshold`, `domain`, `detail`, and `required` so P5-G18 can audit it
  without reverse-engineering text logs;
- `scripts/benchmark_workbench_gui_hotpath.py` now exposes
  `--real-corpus-validation-output`, `--real-corpus-viewer-root`,
  `--real-corpus-customer-evidence-manifest`, `--real-corpus-quick`,
  `--real-corpus-soak-minutes`, RSS/latency target flags, and customer-corpus
  requirement flags;
- when a real-corpus output or viewer root is supplied, the GUI hot-path harness
  runs only the P5-G16 replay branch and marks `synthetic_gui_probes_skipped`
  in the wrapper metadata.

P5-G16 validation evidence:

- `py_compile` passed for `benchmark_real_corpus_replay.py`,
  `benchmark_workbench_gui_hotpath.py`, and their changed tests;
- targeted benchmark tests passed: `23 passed`;
- the hot-path wrapper test asserts that real-corpus replay delegates to
  `benchmark_real_corpus_replay.run_replay()`, writes the P5-G16 JSON, clamps
  quick-mode visits, and does not run the synthetic GUI probes.

Remaining after P5-G16:

- customer-grade acceptance still requires running this against the approved
  20-50 sheet corpus with a real manifest, not only unit fixtures;
- worker-process timeout/cancel behavior is currently audited from produced
  artifact metadata and still needs a P5-G17/P5-G18 subprocess or GUI-worker
  closeout gate;
- P5-G18 should make customer-grade audits fail when P5-G16 evidence is absent,
  stale, synthetic-only, failed, RSS-unavailable without explicit smoke-mode
  allowance, blank/stale-positive, fallback-reason-incomplete, or over cache/RSS
  budgets.

## 15. 2026-05-28 P5-G18 MVP Audit Integration Implementation

P5-G18 promotes the real-corpus replay artifact from an optional benchmark into
a customer-grade audit requirement. The goal is to prevent a release packet from
claiming performance stability when the approved validation output has not been
replayed, fingerprinted, and tied back to the customer evidence manifest.

Implemented audit behavior:

- `scripts/audit_drawing_compare_mvp_exit.py` now accepts repeatable
  `--p5-g16-benchmark-json` inputs, with `--p5-g16-real-corpus-replay` kept as
  an alias;
- `evidence_level=customer_grade` automatically requires
  `p5_g16_real_corpus_replay.json`;
- candidate discovery checks explicit CLI inputs, release manifest artifact
  keys, customer evidence manifest performance fields, validation-summary
  output/benchmark references, and the default sibling path
  `<result-dir>/p5_g16_real_corpus_replay.json`;
- the audit validates `benchmark_id=p5_g16_real_corpus_replay`,
  `profile=real_corpus_artifact_replay`, `status=passed`, schema compatibility,
  required gate coverage, required gate pass state, customer-corpus declaration,
  RSS measurement availability, zero blank/missing/stale/fallback/timeout/cancel
  counts, and replay completion;
- the replay artifact must hash-match an audited `validation_summary.json` and
  must hash-match the current `customer_evidence_manifest.json`, so stale or
  copied benchmark JSON cannot satisfy customer-grade exit;
- missing or failed evidence is surfaced as a dedicated
  `p5_g16_real_corpus_replay` audit check instead of being hidden inside generic
  release-manifest validation.

P5-G18 validation evidence:

- `test_audit_drawing_compare_mvp_exit.py` covers parser flags, missing
  customer-grade P5-G16 evidence, default sibling discovery, failed replay
  gates, stale validation-summary hash, and customer manifest hash refresh after
  fixture mutations;
- targeted audit regression passed: `103 passed`;
- P5-G16 benchmark/wrapper regression had previously passed and remains part of
  the combined closeout verification suite.

Remaining after P5-G18:

- the closeout runner, inventory, prepare script, and customer-grade runbook
  still need first-class P5-G16 artifact propagation so operators do not have to
  wire the replay JSON manually;
- the standalone closeout readiness gate was implemented in P5-G20 below, so
  the remaining work is to make later evidence propagation pass that gate
  without manual path surgery;
- artifact replay does not fully exercise actual Qt/QML image cache, GPU
  texture retention, Windows GDI/User handles, or real click-driven event-loop
  behavior. Those require the expanded performance-degradation roadmap below.

## 16. 2026-05-28 Expanded Performance-Degradation Planning Refinement

This refinement comes from the 2026-05-28 multi-agent review focused on
performance/memory, PDF-first architecture, and QA/release gates. It tightens
the next work packages around the user's core concern: the program must not only
avoid render failure, it must also avoid getting slower after real use.

### 16.1 Key Planning Corrections

- P5-G16 artifact replay is necessary but not sufficient. It proves that
  produced viewer artifacts are stable, but it does not fully measure Qt/QML
  `Image` cache behavior, `QImage/QPixmap` retention, GPU texture growth, real
  mouse/keyboard navigation, or Windows handle leaks.
- PDF-first remains the correct viewer reliability direction, but CAD-to-PDF
  output must stay a visual asset. CAD/DWG/DXF truth remains entity/canonical
  comparison unless a future explicitly approved mode declares otherwise.
- A release gate must combine visual availability, selected-zone correctness,
  real-corpus replay, actual GUI soak, resource plateau, CAD policy, PDF visual
  provenance, and multi-detail review gating into one customer-grade decision.
- Synthetic benchmark success must never be treated as customer-corpus success.

### 16.2 Additional Metrics Now Required

Add these fields to the customer-grade performance packet before final closeout:

- real GUI navigation event-loop p95/max gap;
- open file handle count and delta after warmup;
- Windows GDI handle count and User handle count when available;
- worker process count, orphan worker count, timeout cleanup result, and cancel
  cleanup result;
- Qt/QML image cache state where observable, or a documented probe result when
  unavailable;
- duplicate CAD-to-PDF conversion count by source signature;
- visual asset cache hit rate by source signature, backend, layout, page, DPI,
  plot profile, and coordinate contract version;
- multi-detail detector candidate count, matcher edge count, ambiguity count,
  manual override count, and whole-modelspace fallback count.

Hard gates to add:

- orphan worker processes = 0;
- handle/GDI/User counts do not grow linearly after warmup;
- duplicate conversion for the same source signature = 0 unless a reason code
  explains invalidation;
- GUI soak event-loop max gap <= 500 ms for standard navigation probes;
- CAD-to-PDF visual asset cache hit rate >= 95% after warmup for repeat runs;
- whole-modelspace fallback auto-compare count = 0 when multiple detail regions
  are detected.

### 16.3 Unified Customer Visual Performance Release Gate

Introduce a top-level release decision named
`customer_visual_performance_release_gate`. It should pass only when all of the
following sub-gates pass:

| Sub-gate | Evidence | Failure examples |
| --- | --- | --- |
| P5-G3 realset gate | `validation_summary.json`, viewer perf, runtime budget, selected-zone evidence, nonblank proof | missing runtime/viewer evidence, blank viewer, stale tile manifest |
| P5-G15 selected-zone gate | selected-zone benchmark JSON | blank selected-zone, stale visible result, fallback reason missing, cache bytes over cap |
| P5-G16 real-corpus replay | `p5_g16_real_corpus_replay.json` | stale validation hash, manifest hash mismatch, RSS unavailable, replay failed |
| Actual GUI soak | GUI benchmark JSON from real corpus | Qt/QML navigation stalls, event-loop max gap over cap, handle growth |
| PDF visual policy | visual asset manifests, CAD policy gate | converter used as CAD truth, missing license/provenance, coordinate quality unknown |
| Multi-detail gate | region detection/matching report and overrides | ambiguous auto-match, whole-modelspace fallback, unmatched regions hidden |
| Closeout readiness | `closeout_readiness_audit.json` | plan/readiness mismatch, proof dirs mixed into final corpus, tile-cache env leakage |

### 16.4 Next Agent Execution Board

| Slice | Purpose | Write scope | Exit criteria |
| --- | --- | --- | --- |
| P5-G20 closeout readiness audit | Implement standalone audit for `closeout_readiness.json` and `closeout_plan.json` | new `scripts/audit_closeout_readiness.py`, tests, runbook | closeout is blocked unless `closeout_readiness_audit.json status=passed` |
| P5-G21 evidence pipeline propagation | Wire P5-G16 generation/discovery through inventory, prepare, closeout, release runbook | closeout/inventory/prepare scripts and docs | final audit finds replay JSON without manual path surgery |
| P5-G22 actual GUI soak | Exercise real pair/page/zone GUI navigation on approved corpus | GUI benchmark harness/tests | event-loop, RSS, handle, worker, and blank/stale gates pass |
| P5-G23 native resource sampler | Add file/GDI/User handle and worker-tree plateau fields | `runtime_budget.py`, perf summary tests | customer-grade packet fails when native resource measurement is missing or growing |
| P5-G24 PDF visual asset policy gate | Harden source/sidecar/CAD-to-PDF provenance and cache-key checks | visual asset, policy gate, audit tests | converter output cannot be used without license/provenance/nonblank/coordinate gates |
| P5-G25 multi-detail customer gate | Bind region detection/matching results into final audit | multi-detail validation and MVP audit tests | multiple-detail drawings are matched or review-gated before compare |

### 16.5 Risk Register Update

| Risk | Severity | Mitigation |
| --- | --- | --- |
| P5-G16 replay is not real GUI use | High | Add P5-G22 actual GUI soak with Qt/QML navigation and handle probes |
| Closeout readiness is not independently audited | High | Implement P5-G20 before full customer closeout |
| P5-G16 artifact path is not propagated through closeout tooling | High | Implement P5-G21 pipeline propagation |
| RSS alone misses native leaks | Medium-High | Add file/GDI/User handles and worker tree plateau metrics |
| PDF fallback can show the wrong coordinate location | High | Require transform quality and coordinate contract in visual asset manifests |
| CAD-to-PDF backend creates legal or data-egress risk | High | Keep disabled by default and require backend allowlist/license provenance |
| Multi-detail drawings silently compare whole modelspace | High | Require region matching or review-gated manual override |
| Synthetic pass is mistaken for customer pass | High | Customer-grade audit requires real manifest and real replay hash matches |
| Runbook lags behind audit gates | High | Treat runbook command drift as release-blocking documentation debt |

## 17. 2026-05-28 P5-G20 Closeout Readiness Audit Implementation

P5-G20 turns closeout readiness from a generated helper artifact into an
independently auditable release gate. The intent is to catch operator-facing
performance evidence mistakes before a full customer closeout run burns time:
mixed proof/corpus directories, stale plan summaries, missing outputs,
tile-cache environment leakage, and final audits that are not actually running
as customer-grade audits.

Implemented behavior:

- added `scripts/audit_closeout_readiness.py` as a read-only auditor for
  `closeout_readiness.json` and `closeout_plan.json`;
- the auditor emits `closeout_readiness_audit.json` and exits non-zero when a
  required readiness invariant fails;
- readiness checks now cover schema versions, dry-run/ready status, preflight
  success, required output paths, plan-summary consistency, step order,
  invariant preservation, final-audit `--results-dir` purity, proof-vs-corpus
  routing, P5-G7 tile-cache environment isolation, and required
  `customer_grade` final-audit flags;
- release packaging now includes `cli\audit_closeout_readiness.py`, manifest
  metadata, README instructions, and closeout packet guidance;
- the customer-grade runbook now requires a dry-run readiness packet plus
  `closeout_readiness_audit.json status=passed` before the full closeout run.

P5-G20 validation evidence:

- `test_audit_closeout_readiness.py` covers a passing closeout dry-run plan,
  failed preflight/readiness status, proof dirs accidentally entering the final
  audit corpus, standard/proof overlap, tile-cache env leakage, mismatched
  forced-eviction cap env, and CLI non-zero behavior for bad plans;
- release tests confirm the readiness-audit CLI is copied into the customer
  package and referenced by release documentation and manifests;
- targeted verification passed: `py_compile` for the new/changed scripts and
  tests, plus `22 passed` across closeout readiness, closeout runner, and
  release packaging regressions.

Remaining after P5-G20:

- P5-G21 propagation is implemented in section 18 below; future closeout work
  should treat missing/misrouted P5-G16 replay JSON as a release-blocking
  pipeline defect, not as an operator handoff task;
- P5-G22 must add an actual GUI soak that exercises real Qt/QML navigation,
  event-loop stalls, blank/stale views, RSS, native handles, and worker cleanup;
- P5-G24 must formalize PDF visual asset provenance, cache-key quality, and
  CAD-to-PDF policy so PDF rendering improves UX without becoming false CAD
  truth;
- P5-G25 must bind multi-detail region detection/matching into the customer
  gate so multiple drawings inside one source are matched or review-blocked
  before precision comparison.

## 18. 2026-05-28 P5-G21 Evidence Pipeline Propagation Implementation

P5-G21 removes the manual handoff gap between P5-G16 real-corpus replay
evidence and the customer-grade closeout pipeline. The closeout runner now plans
the replay artifact as a first-class step: inventory runs first, prepare writes
the customer evidence manifest, `benchmark_real_corpus_replay.py` generates the
P5-G16 JSON against that manifest hash, and the final MVP audit consumes the
same replay JSON.

Implemented behavior:

- `prepare_drawing_compare_customer_evidence.py` accepts repeatable
  `--p5-g16-benchmark-json` inputs, records them in manifest `artifacts`, adds a
  `performance_benchmarks.p5_g16_real_corpus_replay` summary, and hashes present
  replay files in provenance;
- `inventory_drawing_compare_customer_evidence.py` discovers
  `p5_g16_real_corpus_replay.json`, summarizes pass/fail status, reports counts,
  and injects the benchmark path into recommended prepare/final-audit commands;
- `closeout_drawing_compare_customer_evidence.py` can generate P5-G16 replay
  JSON after manifest creation and before the final customer-grade audit, with
  configurable visits, warmup visits, and timeout;
- `audit_closeout_readiness.py` validates that prepare and final-audit commands
  route exactly the replay JSONs declared in the plan and that generated replay
  outputs have matching pipeline steps;
- `audit_drawing_compare_mvp_exit.py` discovers P5-G16 replay JSONs from release
  manifests, customer evidence manifests, and benchmark summaries in addition to
  explicit CLI arguments;
- `release_drawing_compare_workbench.py` packages
  `benchmark_real_corpus_replay.py`, records replay artifacts in the release
  manifest, forwards replay JSONs to MVP exit audit commands, and updates the
  customer closeout documentation/checklist.

P5-G21 validation evidence:

- targeted propagation regression passed: `25 passed` across closeout runner,
  closeout readiness audit, prepare, inventory, and release package tests;
- broader related regression passed across the full closeout/readiness,
  prepare, inventory, and release unit test files;
- `git diff --check` passed for the changed scripts and tests.

Remaining after P5-G21:

- P5-G22 must add actual GUI soak evidence because P5-G16 replay still exercises
  persisted validation/viewer artifacts, not a live Qt/QML navigation session;
- native handle/GDI/User resource growth is still only indirectly covered by RSS
  until P5-G23 lands;
- PDF visual asset provenance and CAD-to-PDF policy remain P5-G24 release
  blockers before PDF conversion can be treated as a robust UX path;
- multi-detail matching must still become a final customer gate in P5-G25 so one
  source containing multiple drawings is segmented, matched, or review-blocked.

## 19. 2026-05-28 P5-G22 Actual GUI Soak Pipeline Propagation

P5-G22 closes the gap identified by the multi-agent review: P5-G16 proves that
persisted validation/viewer artifacts can be replayed, but it does not prove
that the live Qt/QML Workbench can repeatedly select drawings, step pages,
focus zones, clean up workers, and plateau memory/native resources on a
customer-approved corpus. P5-G22 makes that live GUI behavior a customer-grade
audit requirement instead of an optional benchmark.

Implemented behavior:

- `benchmark_actual_gui_soak.py` drives the real Workbench over pair, page, and
  zone navigation, records event-loop gap, RSS, native process/GDI/User handle
  availability, blank/stale view counts, page/zone navigation counts, and
  worker cleanup/orphan worker state;
- the benchmark now supports release-folder
  `cli\benchmark_actual_gui_soak.py --help` without importing the source tree,
  while still lazy-loading GUI dependencies only when the actual soak runs;
- `audit_drawing_compare_mvp_exit.py` auto-requires
  `p5_g22_actual_gui_soak` for `evidence_level=customer_grade`, accepts
  repeatable `--p5-g22-gui-soak-json` inputs, discovers sibling/manifest
  artifacts, and rejects missing, stale, failed-gate, wrong-profile, synthetic,
  hash-mismatched, blank/stale, RSS-missing, native-resource-missing, or
  worker-cleanup-failed evidence;
- `prepare_drawing_compare_customer_evidence.py` records P5-G22 artifact refs
  under manifest `artifacts` and
  `performance_benchmarks.p5_g22_actual_gui_soak`;
- `inventory_drawing_compare_customer_evidence.py` discovers
  `p5_g22_actual_gui_soak.json`, summarizes pass/fail status, exposes
  diagnostics, and injects `--p5-g22-gui-soak-json` into recommended prepare
  and final-audit commands;
- `closeout_drawing_compare_customer_evidence.py` plans P5-G22 GUI soak
  generation after manifest preparation and before the final audit, with
  configurable visits, warmup visits, timeout, zone-render wait, and page
  navigation minimums;
- `audit_closeout_readiness.py` now validates that planned/generated P5-G22 JSON
  paths are routed through prepare, soak generation, and final audit, and that
  the plan invariant
  `final_audit_p5_g22_gui_soak_jsons_equal_plan=true` holds;
- `release_drawing_compare_workbench.py` packages the P5-G22 tool, includes it
  in customer-shareable packages, forwards explicit soak JSONs to the MVP exit
  audit, and documents the gate in the README, closeout packet, checklist, and
  customer-grade runbook.

P5-G22 validation evidence:

- targeted P5-G22/closeout propagation regression passed: 226 tests across
  `test_benchmark_actual_gui_soak.py`, MVP audit, prepare, inventory, closeout,
  readiness audit, and release packaging;
- release CLI smoke now includes `cli\benchmark_actual_gui_soak.py --help`,
  proving the packaged evidence tool starts without source-tree imports;
- direct P5-G22 audit tests cover parser flags, missing customer-grade GUI soak
  evidence, and failed required gate rejection;
- prepare/inventory/release tests now assert that P5-G22 artifacts are recorded,
  recommended, packaged, zipped, and forwarded to final audit commands.

Remaining after P5-G22:

- P5-G23 must promote native resource sampling from benchmark-local evidence to
  a shared runtime/viewer/audit contract so file handles, Windows process
  handles, GDI handles, User handles, and worker-tree plateaus are available in
  normal customer evidence packets, not only the actual GUI soak artifact;
- P5-G24 must harden PDF visual asset provenance, cache keys, converter
  allowlists, license evidence, and coordinate contracts before CAD-to-PDF can
  be relied on as the default viewer UX path;
- P5-G25 must make multi-detail region detection/matching a final audit gate so
  multiple drawings inside one source are segmented, matched, or review-blocked
  before precision comparison.

## 20. 2026-05-28 Performance Degradation Planning Consolidation

This section refines the next roadmap specifically around the user's concern
that the program can become slower than earlier versions. The conclusion from
the latest multi-agent review is that slowdown must be treated as a release
blocking product defect, not as a secondary optimization task.

### 20.1 Multi-Agent Inputs Used

This planning pass consolidated the active read-only agent reviews that were
already running in the thread:

| Agent lens | Finding used in this plan |
| --- | --- |
| Actual GUI soak | Synthetic navigation is not enough. Customer-grade evidence must drive the real Qt/QML Workbench through pair/page/zone navigation and must capture blank/stale views, event-loop stalls, RSS, native handles, and worker cleanup. |
| Release pipeline | P5-G22 routing is now present, but the native/worker data is still flattened. The next schema must expose shared `native_resource_summary` and `worker_tree_summary` blocks. |
| Native resources | RSS alone misses Windows handle, GDI/User, file descriptor, and orphan worker leaks. P5-G23 must move native telemetry into `RuntimeBudgetSampler`, `perf_events`, and viewer summaries. |
| QA/audit | A benchmark is only useful when the final customer-grade audit fails on missing, stale, contradictory, or bypassed performance evidence. |

### 20.2 Performance Failure Model

The program is considered performance-regressed when any of these occur on a
real or release-like corpus:

| Failure class | User symptom | Root technical risk | Blocking evidence |
| --- | --- | --- | --- |
| First result delay | "비교 결과가 안 나온다" | full package/export/tile/render work blocks first review | `first_review_ready_s`, `package_complete_s`, partial result callback evidence |
| Selection stall | clicking a drawing freezes the app | pair selection fans out into overlay load, tree build, PDF render, preview render, zone auto-select | GUI block p95/max, event-loop gap, selected pair request id |
| Page navigation stall | PDF page switching is slow or stale | cold PDF background load, stale render worker result, hidden full overlay load | page switch p95/max, stale leaf count, background ready time |
| Selected-zone stall | selected region render fails or takes too long | crop/vector/focus duplicate work, cold DXF index, fragile vector entities | crop-first backend, vector deferred flag, stale/cancel count, fallback reason |
| Native leak | app gets slower over repeated use | handles, GDI/User objects, fds, QProcess/worker subprocesses accumulate | native deltas/slopes, worker tree after cleanup |
| Cache growth | memory/disk grows each run | DisplayList, DXF index, tile/overlay pages, viewer packages are not byte capped | cache byte caps, retained bytes, evictions, orphan payloads |
| Telemetry overhead | measurement makes the run slower | recursive spool scan, JSONL summary materialization, viewer_perf refresh | sampler overhead ratio, summary elapsed/input bytes |
| Multi-detail blowup | one file with many drawings is slow and mismatched | frame/title detection and region-local compare scale poorly or fallback to modelspace | region candidate count, auto-match count, review-gated ambiguous pairs |
| PDF-first regression | viewer is stable but slower | full-page CAD-to-PDF/PDF raster conversion happens in hot path | visual asset cache hit rate, conversion count, backend latency |

No single metric is sufficient. Customer-grade performance requires a combined
latency, memory, native resource, nonblank, and correctness packet.

### 20.3 Performance Tech Spec Additions

#### Runtime budget schema v4

`RuntimeBudget` must add native and worker fields while preserving existing
memory/spool/timing fields:

```text
native_resource_schema_version = 1
native_resource_available
native_resource_sample_count
start_process_handle_count
final_process_handle_count
peak_process_handle_count
process_handle_positive_delta
start_open_file_descriptor_count
final_open_file_descriptor_count
peak_open_file_descriptor_count
open_file_descriptor_positive_delta
start_gdi_handle_count
final_gdi_handle_count
peak_gdi_handle_count
gdi_handle_positive_delta
start_user_handle_count
final_user_handle_count
peak_user_handle_count
user_handle_positive_delta
start_worker_process_count
final_worker_process_count
peak_worker_process_count
worker_process_positive_delta
native_resource_notes
```

Rules:

- Windows process handles use `psutil.Process().num_handles()` when available.
- Unix-like file descriptors use `psutil.Process().num_fds()` when available.
- Windows GDI/User handles use `GetGuiResources(GetCurrentProcess(), 0/1)`.
- Worker count comes from recursive child-process inspection with Drawing
  Compare worker command tokens.
- Unsupported metrics stay `null`; supported metrics that fail must add a
  reason to `native_resource_notes`.
- Customer-grade on Windows requires process handle, GDI, and User handle
  availability unless the audit explicitly records an unsupported platform.

#### P5-G22 shared summary blocks

`p5_g22_actual_gui_soak.json` must keep legacy flattened fields but also emit:

```text
summary.native_resource_summary.measurement_available
summary.native_resource_summary.rss_slope
summary.native_resource_summary.process_handle_slope
summary.native_resource_summary.open_file_descriptor_slope
summary.native_resource_summary.gdi_handle_slope
summary.native_resource_summary.user_handle_slope
summary.native_resource_summary.positive_end_deltas
summary.worker_tree_summary.snapshot_start
summary.worker_tree_summary.snapshot_after_cleanup
summary.worker_tree_summary.cleanup_ok
summary.worker_tree_summary.orphan_worker_count
```

The final audit should prefer the nested summaries and use legacy fields only
as compatibility fallback. For customer-grade, missing nested summaries are a
failure once this schema lands.

#### Viewer/perf event fields

High-frequency viewer and pipeline events should carry only bounded scalar
fields:

```text
gui_block_ms
event_loop_gap_ms
pair_id
page_a
page_b
zone_id
request_id
generation
backend_id
backend_latency_ms
background_status
blank_visible
stale_result_dropped
worker_spawned
worker_finished
process_handle_count
open_file_descriptor_count
gdi_handle_count
user_handle_count
worker_process_count
cache_key_hash
cache_hit
retained_cache_bytes
evicted_cache_bytes
telemetry_overhead_ms
```

Do not log full overlay payloads, raw source paths, entity lists, or large
worker snapshots in high-frequency events. Use separate evidence artifacts for
heavy diagnostics.

### 20.4 Benchmarks and Gates

| Profile | Purpose | Required gates |
| --- | --- | --- |
| `p5_g22_actual_gui_soak` | real Workbench navigation on customer corpus | no blank/stale views, event-loop max <= 500 ms, RSS/native plateaus, worker cleanup |
| `p5_g23_native_resource_soak` | repeated selection/page/zone cycles with native sampling | handle/GDI/User/fd deltas under cap, final worker count 0 |
| `p5_g24_pdf_visual_asset_gate` | source/sidecar/CAD-to-PDF visual asset policy | no converter in GUI hot path, provenance/license/cache key present, nonblank output |
| `p5_g25_multi_detail_customer_gate` | multi-sheet/title-block extraction and same-drawing matching | detected regions >= expected, ambiguous auto-match = 0, approved/manual matches compare locally |
| `p5_g26_selection_latency_soak` | 100-pair/100k-overlay navigation pressure | selection p95 under cap, overlay cache byte plateau, no eager full pyramid |
| `p5_g27_selected_zone_crop_soak` | rapid selected-zone interactions | crop-first result visible, vector failures do not blank viewer, stale/cancel counts bounded |
| `p5_g28_cache_plateau_soak` | repeated run/open/close lifecycle | DisplayList/DXF/tile/cache bytes plateau, eviction reasons present, orphan payloads 0 |
| `p5_g29_telemetry_overhead_gate` | prove measurement is not the bottleneck | runtime sampler overhead ratio under cap, summary elapsed/input bytes under cap |
| `p5_g30_customer_visual_performance_release_gate` | final composite release decision | all above evidence discovered and passing in final audit |

Default threshold policy:

- strict gates should fail when required measurement is missing;
- smoke-only flags such as `--allow-missing-psutil` are not allowed in
  customer-grade evidence;
- slopes are measured after warmup, not from cold start only;
- p95 is the primary responsiveness metric, max is the hard hang detector;
- every failed fallback must include a reason code visible in evidence.

### 20.5 Revised Roadmap

| Slice | Goal | Main files | Acceptance |
| --- | --- | --- | --- |
| P5-G23 | shared native resource sampler | `native_resource_sampler.py`, `runtime_budget.py`, `perf_events.py`, `viewer_perf_summary.py`, `benchmark_actual_gui_soak.py`, audit tests | runtime/viewer/GUI soak all expose native and worker summaries; customer-grade fails if missing |
| P5-G24 | PDF visual asset policy gate | `visual_asset.py`, `cad_visual_backend.py`, `render_backend_registry.py`, `cad_policy_gate.py`, MVP audit | PDF/sidecar/CAD-to-PDF has provenance, license, cache key, transform quality, nonblank proof |
| P5-G25 | multi-detail customer gate | `sheet_region_detector.py`, `region_compare_pipeline.py`, `region_match_overrides.py`, validator/audit docs | multiple drawings are detected, matched, or review-blocked; whole-modelspace auto-compare is rejected when regions exist |
| P5-G26 | selection latency hard gate | `drawing_compare_workbench.py`, `benchmark_workbench_gui_hotpath.py`, viewer perf tests | pair/page selection p95 and event-loop gaps are release-gated on large fixture |
| P5-G27 | selected-zone crop-first soak | `zone_render_service.py`, `zone_render_worker.py`, `zone_vector_renderer.py`, GUI selected-zone tests | crop result is visible before vector focus; vector failure never clears background |
| P5-G28 | cache plateau and leak audit | `pdf_display_list_cache.py`, `cache_budget.py`, `viewer_tile_cache.py`, `zone_render_service.py` | repeated open/run/close shows bounded retained bytes and zero orphan payloads |
| P5-G29 | telemetry cost audit | `runtime_budget.py`, `perf_events.py`, `viewer_perf_summary.py` | sampler and summary overhead are measured and capped in final audit |
| P5-G30 | composite customer visual performance gate | audit/release/closeout scripts and runbook | final release is blocked unless all performance, PDF policy, native resource, and multi-detail gates pass |

### 20.6 Agent Execution Plan

Future multi-agent execution should use disjoint scopes so the work can proceed
without merge conflicts:

| Agent | Scope | Deliverable |
| --- | --- | --- |
| A. Native telemetry agent | runtime/native sampling only | `native_resource_sampler.py`, RuntimeBudget v4 tests, benchmark compatibility wrappers |
| B. Audit/release agent | final audit, inventory, prepare, closeout, release docs | customer-grade failures for missing native/shared summaries and routed evidence |
| C. PDF policy agent | visual asset and converter policy | cache/provenance/license/coordinate gates, no hot-path conversion proofs |
| D. Multi-detail agent | region detection/matching/review | customer gate tying detection, matching, overrides, and localized compare evidence |
| E. GUI latency agent | selection/page/zone interaction | p95/max/event-loop benchmarks, stale-result and request-id evidence |
| F. Cache/leak agent | cache byte plateaus and lifecycle cleanup | retained byte caps, eviction telemetry, repeated lifecycle soak |
| G. QA critic agent | independent read-only review | attempts to falsify the release packet and lists missing evidence before merge |

Each implementation slice must end with:

- focused unit tests for schema and failure cases;
- one benchmark artifact or synthetic fixture proving the gate shape;
- `py_compile` or import smoke for changed scripts;
- final audit or readiness test showing that missing evidence fails;
- roadmap and runbook update before moving to the next slice.

### 20.7 Immediate Next Step

The next executable slice should be P5-G23:

1. Add `native_resource_sampler.py` with platform-aware process/fd/GDI/User and
   worker-tree snapshots.
2. Bump `RuntimeBudget` to schema v4 and record start/final/peak/delta native
   fields.
3. Propagate native resource fields into `perf_events`, `viewer_perf_summary`,
   and `p5_g22_actual_gui_soak.json` shared summaries.
4. Make `audit_drawing_compare_mvp_exit.py` fail customer-grade packets when
   native telemetry or worker cleanup evidence is missing or contradictory.
5. Add tests for supported, unsupported, failed, and contradictory telemetry
   cases.

P5-G23 is the right next step because it makes future slowdown and leak claims
measurable across normal validation runs, not only inside a single GUI soak
benchmark.

## 21. 2026-05-28 Deep Multi-Agent Planning for Performance Degradation

This section freezes the refined plan requested after the PDF-first discussion.
The planning pass used three read-only agent lenses: performance/memory,
PDF-first architecture, and QA/product release validation. The conclusion is
that performance degradation must be planned as a release-blocking system risk,
not as a renderer cleanup item.

### 21.1 Core Product Decisions

- PDF-first is the correct viewer reliability direction, but PDF is a visual
  asset layer for CAD inputs. CAD/DWG/DXF precision comparison remains
  canonical/entity or approved region-local CAD truth.
- A blank viewer or blank selected-zone panel is a product failure even when
  the comparison engine produced data.
- "Fast enough once" is not accepted. The program must remain responsive after
  repeated pair selection, page navigation, zone clicks, cancellation, and
  reruns.
- Synthetic benchmarks and real customer evidence are separate. Synthetic
  gates can catch regressions, but customer-grade closeout requires a real
  approved corpus and hash-linked evidence artifacts.
- Multi-detail drawings cannot silently fall back to whole-modelspace compare
  when frame/title/region candidates exist. Ambiguous matches must be reviewed.

### 21.2 Expanded Failure Taxonomy

| Failure class | Customer symptom | Required diagnostic answer |
| --- | --- | --- |
| First result delay | 비교 결과가 안 보임 | Did first-review-ready wait for package/export/tile work? |
| Pair selection stall | 도면 행 클릭 시 앱이 멈춤 | Which work fanned out: overlay load, tree build, PDF render, preview, or zone auto-select? |
| Page navigation stall | PDF 페이지 이동이 느림 | Was the page cache hit real, or did the app reopen/rerender the PDF? |
| Selected-zone failure | 선택구역 렌더 실패 | Did crop-first render produce a visible result before vector enhancement? |
| Vector failure blanking | 벡터 렌더 실패 후 빈 화면 | Did SVG/vector failure clear an already valid PDF/raster background? |
| Native leak | 오래 쓰면 점점 느림 | Are handles, GDI/User objects, file descriptors, workers, or subprocesses increasing after warmup? |
| Cache growth | 반복 실행 후 메모리/디스크 증가 | Which cache namespace retained bytes, and was eviction recorded? |
| Telemetry overhead | 계측을 켜면 더 느림 | What is sampler tick p95 and summary overhead ratio? |
| Multi-detail mismatch | 한 파일 안 여러 도면이 엉뚱하게 비교됨 | Were regions detected, matched, approved, skipped, or review-blocked with reasons? |
| False negative | 실제 차이가 있는데 변경 0건 | Which golden/customer truth gate failed: block, text, OCS/WCS, dimension, paperspace, or structural delta? |

### 21.3 Required Technical Specification

#### Performance packet

Every customer-grade performance packet must contain:

```text
validation_summary.json
run_manifest.json
perf_events_summary.json
viewer/viewer_perf.json
viewer/selected_zone_evidence.json
nonblank_pixel_probe.json
p5_g16_real_corpus_replay.json
p5_g22_actual_gui_soak.json
runtime_budget with native_resource_summary and worker_tree_summary
region_detection_summary.json when multi-detail inputs are present
region_match_summary.json when multi-detail inputs are present
localized_compare_summary.json when approved region matches exist
```

Raw JSONL traces may be used internally, but customer-shareable packages should
keep compact summaries unless a support/debug packet is explicitly requested.

#### Native and memory budget

Customer-grade runtime evidence must measure both managed and native resource
growth:

- peak RSS, RSS slope after warmup, spool MB, cache retained bytes;
- process handle count where supported;
- open file descriptor count where supported;
- Windows GDI/User handle counts where supported;
- worker process count before, during, and after cleanup;
- orphan worker count and cleanup status;
- sampler tick p95 and total telemetry overhead ratio.

Missing native measurement is allowed only in smoke mode or on a platform where
the metric is explicitly unsupported. Missing measurement in customer-grade
Windows evidence is a failure.

#### PDF visual asset contract

Every PDF or CAD-to-PDF visual asset must declare:

```text
asset_kind
source_signature
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
world_to_pdf
pdf_to_pixel
world_to_pixel
transform_quality
status
reason_code
cache_key_hash
nonblank_probe_status
```

Rules:

- source PDF and sidecar PDF are the preferred visual inputs;
- CAD-to-PDF converters stay disabled by default until allowlist, provenance,
  license, timeout/cancel, output validation, nonblank proof, and coordinate
  gates pass;
- no converter may run in the GUI selection hot path;
- `transform_quality=estimated` or `relative_only` must never be displayed as
  exact overlay quality.

#### Multi-detail contract

Multi-detail evidence must expose:

- detected region count by side;
- candidate frame count and rejected candidate count;
- drawing number/title evidence;
- matcher edge count and score components;
- auto/manual/review-required/unmatched counts;
- whole-modelspace fallback count;
- localized compared/skipped count;
- skip or unsupported reason codes.

If multiple candidates exist, whole-modelspace auto-compare is a hard failure
unless the run explicitly enters a review-required state.

### 21.4 Acceptance Gates

| Gate | Target |
| --- | --- |
| Normal first-review-ready p95 | <= 30 s |
| Large first-review-ready p95 | <= 120 s, hard cap <= 300 s |
| Cached pair selection p95 | <= 300 ms |
| Cold pair selection p95 | <= 2 s |
| GUI block max for standard probes | <= 100 ms |
| GUI soak event-loop max gap | <= 500 ms |
| Selected-zone PDF/image cold crop p95 | <= 2 s |
| Selected-zone cache-hit p95 | <= 500 ms |
| CAD selected-zone | <= 10 s or visible fallback with reason code |
| Pair viewer blank count | 0 |
| Selected-zone blank count | 0 |
| Stale selected-zone result wins | 0 |
| Peak RSS | <= 2 GB or baseline +10%, hard cap 4 GB |
| RSS slope after warmup | <= 5 MB / 100 selections |
| Orphan worker process count | 0 |
| Telemetry overhead | <= 2% |
| Runtime sampler tick p95 | <= 20 ms |
| Duplicate conversion for same source signature | 0 unless invalidation reason exists |
| CAD-to-PDF repeat cache hit after warmup | >= 95% when backend is enabled |
| Multi-detail detected region rate | >= 80%, target >= 90% |
| Approved region match accuracy | >= 95% |
| Ambiguous region auto-match count | 0 |
| Whole-modelspace auto-compare when region candidates exist | 0 |
| Critical golden false negative count | 0 |
| Path leakage in customer package | 0 |

### 21.5 Revised Execution Roadmap

| Step | Slice | Purpose | Primary evidence |
| --- | --- | --- | --- |
| 1 | P5-G23 native resource sampler | Make native leaks measurable across runtime, viewer, and GUI soak | RuntimeBudget v4, native summaries, worker cleanup gates |
| 2 | P5-G24 PDF visual policy | Make PDF-first reliable without false CAD truth or illegal/slow converters | visual asset manifests, nonblank proof, cache/provenance/license gates |
| 3 | P5-G25 multi-detail customer gate | Ensure one file with multiple drawings is segmented, matched, or review-blocked | region sidecars, match summaries, localized compare evidence |
| 4 | P5-G26 selection latency gate | Prevent pair/page selection from launching full overlay/tree/tile work | GUI hot-path benchmark, viewer perf summaries |
| 5 | P5-G27 selected-zone crop-first soak | Make selected-zone visible even when vector fails | crop-first benchmark, stale/cancel/fallback reason evidence |
| 6 | P5-G28 cache plateau and lifecycle audit | Prove DisplayList, DXF index, tile, overlay, and spool growth are bounded | retained-byte plateau, eviction events, orphan payload check |
| 7 | P5-G29 telemetry cost audit | Prove measurement itself is not the slowdown | sampler overhead and summary input/elapsed gates |
| 8 | P5-G30 composite release gate | Combine performance, PDF policy, multi-detail, native resources, and closeout readiness | customer_visual_performance_release_gate |

Do not advance a slice to complete unless it has focused tests, at least one
benchmark or synthetic evidence artifact, and a failure-mode audit proving
missing or contradictory evidence is rejected.

### 21.6 Multi-Agent Work Allocation

| Agent lane | Scope | Write ownership |
| --- | --- | --- |
| Native telemetry agent | process/fd/GDI/User/worker sampling and RuntimeBudget schema | `native_resource_sampler.py`, `runtime_budget.py`, native sampler tests |
| Audit/release agent | evidence discovery, customer-grade failures, closeout propagation | audit, inventory, prepare, closeout, release scripts |
| PDF policy agent | visual asset provenance, backend allowlist, cache keys, nonblank validation | `visual_asset.py`, `cad_visual_backend.py`, `render_backend_registry.py`, CAD policy gate |
| Multi-detail agent | frame/title detection, matcher review gate, localized compare sidecars | region detector, matcher, overrides, validator tests |
| GUI latency agent | pair/page/zone selection fan-out, event-loop gaps, stale result handling | Workbench GUI, viewer perf, hot-path benchmarks |
| Cache/leak agent | DisplayList/DXF/tile/overlay/spool retained bytes and eviction | cache modules, tile cache, zone render service |
| QA critic agent | independent falsification of release packet | no write scope unless assigned fixes after review |

Disjoint ownership is mandatory when multiple workers run at once. Agents must
not revert each other's changes; they should report changed paths, commands,
new evidence artifacts, and remaining risks.

### 21.7 Verification Matrix

| Scenario | Required outcome |
| --- | --- |
| PDF, DXF, DWG cached-DXF, malformed CAD viewer open | no blank viewer; fallback or PDF background is visible |
| Rapid selected-zone clicks | only latest request wins; visible crop/fallback remains; reason code exists |
| Vector render failure | PDF/tile/raster background is preserved |
| 20-50 pair customer corpus | first-review, pair selection, selected-zone, RSS, native resources, and worker cleanup pass |
| 100-pair/100k-overlay navigation | no eager full overlay/tile materialization; RSS plateaus |
| Repeated same-source compare | no duplicate conversion without cache invalidation reason |
| Multi-detail before/after file | same logical drawings are matched or review-gated before localized compare |
| Golden false-negative suite | critical structural/text/block/page changes are not silently missed |
| Telemetry on/off A/B | overhead stays under cap |
| Customer package audit | summaries present, raw sensitive logs/path leakage absent, rollback flags verified |

### 21.8 Immediate Implementation Order

1. Finish P5-G23 before new PDF conversion work. Native resource and worker
   plateau evidence is the missing foundation for slowdown claims.
2. Then implement P5-G24 PDF visual policy. The converter interface may exist,
   but no CAD-to-PDF backend should become default until provenance, cache, and
   coordinate gates pass.
3. Then implement P5-G25 multi-detail customer gate so multi-drawing files are
   no longer judged by global fallback success.
4. Only after those foundations should P5-G26 through P5-G30 harden selection
   latency, selected-zone soak, cache plateau, telemetry cost, and the final
   customer visual performance release gate.

## 22. 2026-05-28 P5-G23 Native Resource Sampler Implementation

P5-G23 promotes native resource and worker-tree evidence from benchmark-local
signals into shared runtime, viewer, GUI soak, prepare/inventory, and final
audit contracts. This closes the gap where RSS alone could pass while Windows
handles, GDI/User objects, file descriptors, or worker processes leaked across
repeated Workbench use.

Implemented behavior:

- added shared native resource sampling for process handles, open file
  descriptors, Windows GDI/User handles, worker process snapshots, and
  best-effort notes;
- bumped `RuntimeBudget` to schema v4 and added start/final/peak/positive-delta
  fields for process handles, file descriptors, GDI handles, User handles, and
  worker process count;
- throttled native handle/GDI/User and worker-tree sampling separately from the
  100 ms memory sampler so P5-G23 does not become a new telemetry slowdown;
- avoided duplicate `memory_info()` calls by letting RuntimeBudget own RSS
  sampling while native resource snapshots can skip process memory;
- propagated bounded scalar native fields into perf events and viewer perf
  summaries;
- added nested `summary.native_resource_summary` and
  `summary.worker_tree_summary` blocks to P5-G22 actual GUI soak evidence while
  keeping legacy flattened fields for compatibility;
- made customer-grade MVP audit reject missing, unavailable, contradictory, or
  growing native/worker evidence;
- made prepare/inventory surface P5-G22 shared summaries and reject provided
  P5-G22 artifacts that lack native/worker summary blocks.

P5-G23 validation evidence:

- targeted native/runtime/perf/viewer tests passed:
  `69 passed` for native sampler, RuntimeBudget, perf events, and viewer perf
  summary;
- targeted P5-G22 propagation/audit tests passed:
  `216 passed` across actual GUI soak, runtime-budget audit, MVP exit audit,
  prepare, and inventory tests;
- combined P5-G23 target suite passed:
  `287 passed`;
- `py_compile` passed for the changed runtime, perf, benchmark, audit, prepare,
  and inventory modules;
- `git diff --check` passed for the P5-G23 touched files.

Remaining after P5-G23:

- P5-G24 must formalize PDF visual asset policy gates: provenance, license,
  cache key, coordinate quality, nonblank validation, and no GUI-hot-path
  converter execution;
- P5-G25 must bind multi-detail region detection/matching into final customer
  gates so multiple drawings inside one source are matched or review-blocked;
- P5-G29 should later revisit `RuntimeBudgetSampler.snapshot()` percentile
  recomputation and GUI soak telemetry self-cost as part of the telemetry cost
  audit.

## 23. 2026-05-28 P5-G24 PDF Visual Asset Policy Foundation

P5-G24 started with the policy foundation rather than enabling any converter.
The goal of this slice is to make PDF/source/sidecar/CAD-to-PDF visual assets
auditable before they can influence customer-grade release decisions.

Implemented in the foundation slice:

- expanded `VisualAssetManifest` with source signature, cache key, plot profile,
  layout, DPI, page size, pixel size, nonblank status, network requirement, and
  hot-path conversion fields;
- added `build_visual_asset_cache_key()` so source hash, backend id/version,
  license, plot profile, layout, page index, DPI, and coordinate contract
  produce a deterministic cache key;
- added `validate_visual_asset_policy(customer_grade=True)` to reject missing
  provenance, missing or stale cache keys, missing nonblank proof, unknown asset
  kinds, CAD-to-PDF hot-path conversion, unapproved network conversion, and
  non-exact assets marked as exact overlays;
- changed the CAD visual conversion worker to ignore env-only backend selection
  by calling the registry with `allow_env=False`;
- extended `cad_policy_gate.py` so GUI/viewer hot paths fail if they call
  `convert_cad_visual`, `convert_cad_visual_in_subprocess`, or
  `run_conversion_request`.

P5-G24 foundation validation evidence:

- visual asset, CAD visual backend, and CAD policy tests passed:
  `24 passed`;
- `python scripts\cad_policy_gate.py` passed on the current repository;
- `py_compile` passed for the changed P5-G24 modules and tests;
- `git diff --check` passed for the touched P5-G23/P5-G24 files.

Remaining before P5-G24 is complete:

- viewer/package generation must write actual `VisualAssetManifest` artifacts
  for source PDF, sidecar PDF, raster fallback, and CAD visual provenance;
- prepare/inventory/final MVP audit must discover those manifests and run
  `validate_visual_asset_policy(customer_grade=True)`;
- source/sidecar PDF nonblank probe artifacts must be hash-linked instead of
  relying only on the manifest's `nonblank_probe_status` field;
- tile/viewer cache keys must consume the visual asset cache key or rendered
  background signature so profile/DPI/page/layout changes cannot reuse stale
  tiles.

## 24. 2026-05-28 Multi-Agent Performance Degradation Planning Addendum

This section refines the roadmap after the additional user request to treat
program slowdown, memory leaks, and user-perceived responsiveness as first-class
planning concerns. Three read-only agent lenses were used:

| Agent lens | Main contribution |
| --- | --- |
| Performance/memory responsiveness | Identified GUI-thread telemetry refresh, visible-first full-image conversion, tile manifest rewrites, synchronous PDF prewarm, weak vector QProcess timeout, and single-entry-over-cap cache cases as explicit slowdown risks. |
| PDF visual asset/render/cache architecture | Identified that PDF-first can still regress performance if package generation performs eager PDF-to-PNG rendering, full LOD tile creation, stale tile reuse, or weak source signatures. |
| QA/release/customer evidence | Confirmed that release approval must be the final customer-grade MVP audit, not prepare/inventory readiness, and that P5-G24/P5-G25 remain release-blocking until wired into audit evidence. |

### 24.1 Product Planning Decisions

- PDF-first remains the reliability direction for the viewer, but it must not
  become an eager conversion pipeline. First review must be visible-first and
  lazy; expensive PDF rasterization, full LOD tile creation, and CAD-to-PDF
  conversion are forbidden in GUI selection hot paths.
- Performance degradation is a release-blocking defect. A run is not acceptable
  merely because comparison data exists; pair viewer, page navigation, and
  selected-zone UI must stay visible and responsive after repeated use.
- Telemetry must prove its own cost. If viewer/perf JSONL summaries, cache
  scans, native sampling, or manifest materialization create stalls, the
  instrumentation itself fails P5-G29.
- Cache identity must be visual-asset based. Tile/viewer cache keys must include
  source signature, page/layout/profile/DPI, transform quality, nonblank proof,
  and rendered background signature. Reusing stale tiles after a page/profile
  change is a correctness and performance failure.
- Customer-grade release cannot enable CAD-to-PDF by default until provenance,
  license, timeout/cancel, output-open, nonblank, coordinate, cache, and audit
  gates all pass.

### 24.2 Concrete Slowdown Risk Register

| Risk | Why it matters | Required mitigation |
| --- | --- | --- |
| GUI-thread viewer_perf summary refresh | Re-reading or summarizing large JSONL every few seconds can become the stall users feel. | Incremental summaries, bounded input bytes, background summary worker, and P5-G29 summary elapsed gate. |
| Visible-first tile still full-image converts | Disk tile count can shrink while peak RSS still loads the whole page image. | Region/tile crop before full RGB conversion where possible, pixel cap evidence, single-entry-over-cap gate. |
| Tile manifest rewrite on interaction | Pan/zoom/selection may rewrite JSON manifests repeatedly. | Append-only or incremental materialization, manifest materialize p95 cap, full-rewrite count evidence. |
| Synchronous PDF lightweight load/prewarm | PDF open/render/save/prune on GUI thread can block the event loop. | Worker-backed prewarm, cache-hit navigation with render call count 0, GUI block max gate. |
| Eager viewer package rendering | PDF-first packages can still render all pair images and full LOD pyramids before review. | First-review-ready decoupled from full package completion; no full tile/overlay/tree work in pair selection. |
| Weak visual cache identity | Path/mtime/size signatures can reuse stale renders after regenerated sources. | Customer-grade hash or sample hash, visual asset cache key v1.1, stale tile reuse test. |
| Vector QProcess lifecycle gap | Vector render timeout or orphan process can leak native resources and blank selected-zone. | Parent-enforced timeout/cancel, worker cleanup proof, vector failure preserves PDF/raster background. |
| Single cache entry over cap | A large PDF page/display list can exceed byte budget while still retained. | `single_entry_over_cap` reason code, downgrade/fallback policy, RSS/native cap gate. |
| Telemetry overhead | Measurement can make the app slower than the uninstrumented path. | Telemetry on/off A/B, append p95, summary elapsed/input bytes, sampler tick p95. |

### 24.3 Required Metrics and Log Schema

Customer-grade performance evidence must record these bounded scalar fields. Raw
large traces can exist in debug packets, but customer-shareable packages should
keep compact summaries.

GUI responsiveness:

```text
event_loop_gap_ms.p95
event_loop_gap_ms.max
pair_selection_gui_block_ms.p95
pdf_page_navigation_gui_block_ms.p95
zone_selection_gui_block_ms.p95
qthread_spawn_count_per_click
qprocess_spawn_count_per_click
pending_request_count
stale_result_dropped_count
stale_result_visible_count
```

PDF-first visual path:

```text
visual_asset_cache_key_hash
source_signature_hash
asset_kind
page_index
layout_name
plot_profile_hash
effective_dpi
page_size_pt
pixel_size
transform_quality
pdf_open_ms
pdf_render_ms
pdf_save_ms
pdf_prune_ms
cache_hit
cache_miss_reason
duplicate_conversion_count
conversion_invoked_from_hot_path
```

Memory, native resources, and worker lifecycle:

```text
rss_start_mb
rss_peak_mb
rss_end_mb
rss_slope_after_warmup
rss_tail_peak_delta_mb
process_handle_start/final/peak/delta
gdi_handle_start/final/peak/delta
user_handle_start/final/peak/delta
open_fd_start/final/peak/delta
worker_process_start/final/peak
orphan_worker_count_after_cleanup
```

Cache namespaces:

```text
namespace = visual_asset | qtpdf_png | pdf_display_list | dxf_index | tile |
            overlay | zone_crop | spool_temp
entry_count
byte_limit
retained_bytes
eviction_count
evicted_bytes
eviction_reason
single_entry_over_cap
retention_scan_ms
orphan_payload_bytes
stale_manifest_count
```

Selected-zone lifecycle:

```text
request_id
generation
queue_wait_ms
crop_backend
crop_ms
vector_backend
vector_ms
nonblank_result
fallback_reason
timeout_count
cancel_count
stale_dropped_count
background_preserved_after_vector_failure
```

Telemetry cost:

```text
append_perf_event_ms.p95
append_viewer_perf_event_ms.p95
summary_input_bytes
summary_elapsed_ms
sampler_tick_ms.p95
spool_scan_ms.p95
telemetry_on_off_overhead_ratio
```

### 24.4 Work Packages and Acceptance Criteria

| Package | Scope | Acceptance criteria |
| --- | --- | --- |
| WP-A GUI Hot Path Gate | Pair/page/zone selection on 100 repeated interactions. | Cached pair p95 <= 300 ms; cold pair p95 <= 2 s; event-loop max <= 500 ms; click hot path full overlay/tree/tile/PDF conversion count = 0. |
| WP-B PDF-First Responsiveness Gate | Source PDF, sidecar PDF, and prewarmed page navigation. | Cached page navigation render call count = 0; repeat cache hit >= 95%; blank viewer = 0; GUI block max <= 500 ms; CAD-to-PDF hot-path count = 0. |
| WP-C Visual Asset Cache Identity Gate | Visual asset manifests and tile/viewer cache linkage. | Stale tile reuse = 0 when page/layout/profile/DPI/source hash changes; duplicate conversion = 0 without invalidation reason; customer-grade source hash present. |
| WP-D Tile/Manifest Plateau Gate | 100-pair/100k-overlay pan/zoom and visible tile generation. | Retained bytes <= cap; orphan payload bytes = 0; stale manifest = 0; full manifest rewrite not repeated per interaction; retention scan p95 under cap. |
| WP-E Selected-Zone Lifecycle Gate | Rapid zone clicks with vector failure/timeout fixtures. | Crop-first visible result; selected-zone blank = 0; stale visible = 0; cache-hit p95 <= 500 ms; PDF crop cold p95 <= 2 s; CAD crop <= 10 s or visible fallback reason; vector orphan process = 0. |
| WP-F Native/QML Leak Soak | Live Workbench open/select/page/zone/close loop for long soak. | RSS slope <= 5 MB/100 selections; end delta <= 64 MB; tail peak delta <= 128 MB; no increasing handle/GDI/User/fd slope; final worker/orphan process count = 0. |
| WP-G Telemetry Cost Gate | Telemetry on/off A/B and large JSONL summary pressure. | Total overhead <= 2%; append p95 <= 5 ms; summary elapsed <= 1 s or backgrounded; runtime sampler tick p95 <= 20 ms. |
| WP-H Composite Customer Release Gate | Final evidence packet audit. | `audit_drawing_compare_mvp_exit.py --evidence-level customer_grade` status = passed; missing, stale, contradictory, or unsupported required evidence fails release. |

### 24.5 Roadmap Refinement

The next implementation sequence should be:

1. Finish P5-G24 by writing real `VisualAssetManifest` artifacts for source PDF,
   sidecar PDF, raster fallback, and CAD visual provenance, then make prepare,
   inventory, and final MVP audit run `validate_visual_asset_policy()`.
2. Extend visual asset cache key v1.1 and wire tile/viewer cache keys to visual
   asset or rendered background signatures. Add stale tile reuse and duplicate
   conversion failure tests.
3. Implement WP-A/WP-B as P5-G26: pair/page selection hot-path gates, PDF page
   navigation cache-hit evidence, and no eager conversion/full tile/full overlay
   work on interaction.
4. Implement WP-D as P5-G28: tile, overlay, DisplayList, DXF index, visual asset,
   and spool namespace byte plateau evidence, including `single_entry_over_cap`.
5. Implement WP-E as P5-G27: selected-zone crop-first result, vector failure
   background preservation, timeout/cancel cleanup, and rapid-click stale result
   rejection.
6. Implement WP-G as P5-G29: telemetry on/off A/B, incremental viewer_perf
   summary, bounded JSONL summary cost, and sampler overhead gates.
7. Implement WP-H as P5-G30: composite customer visual performance release gate
   tying P5-G3, P5-G16, P5-G22, P5-G24, P5-G25, P5-G26, P5-G27, P5-G28, and
   P5-G29 into one blocking audit.

### 24.6 Multi-Agent Execution Plan

| Agent lane | Ownership | First deliverable |
| --- | --- | --- |
| PDF visual asset agent | `visual_asset.py`, `viewer_package.py`, visual manifest tests | Write source/sidecar/raster/CAD visual manifests and audit discovery tests. |
| Cache identity agent | `viewer_tile_cache.py`, `pdf_display_list_cache.py`, `source_signature.py` | Visual asset cache key v1.1 and stale tile reuse tests. |
| GUI hot-path agent | `drawing_compare_workbench.py`, `benchmark_workbench_gui_hotpath.py` | Pair/page/zone hot-path counters and p95 gate tests. |
| Selected-zone lifecycle agent | `zone_render_service.py`, `zone_render_worker.py`, `zone_vector_renderer.py` | Crop-first visible proof, vector timeout cleanup, background preservation tests. |
| Cache/leak plateau agent | cache modules, runtime/native summaries, lifecycle benchmarks | Namespace retained-byte summaries and single-entry-over-cap evidence. |
| Telemetry cost agent | `perf_events.py`, `viewer_perf_summary.py`, `runtime_budget.py` | Incremental summary or bounded summary cost with on/off A/B evidence. |
| QA critic agent | audit/prepare/inventory/release scripts | Customer-grade audit failures for missing P5-G24/P5-G26-P5-G29 evidence. |

Agents must use disjoint write scopes and must not revert unrelated repository
changes. Each lane ends with focused tests, one synthetic or customer-like
evidence artifact, `py_compile` for changed scripts/modules, and a roadmap or
runbook update.

### 24.7 Immediate Next Step

The next concrete step is still P5-G24 completion, but it must be executed with
the performance gates above in mind:

1. Add visual asset manifest generation to viewer/package outputs without
   enabling new CAD-to-PDF defaults.
2. Hash-link nonblank probe artifacts and source signatures into each manifest.
3. Make customer-grade audit fail on missing visual asset manifests.
4. Wire tile/viewer cache keys to visual asset cache identity.
5. Add tests proving stale visual assets, hot-path conversion, and missing
   nonblank proof are release-blocking.

Only after this should the roadmap proceed to P5-G26/P5-G28 performance gates,
because otherwise PDF-first may hide renderer failures while introducing new
latency, memory, and cache regressions.

## 25. 2026-05-28 P5-G24 Visual Asset Manifest and Audit Gate Slice

This slice moves P5-G24 from policy-only code into emitted viewer-package
evidence and final customer-grade audit enforcement. It intentionally does not
mark nonblank proof as passed unless a future pixel probe supplies that evidence.

Implemented behavior:

- viewer package now writes per-pair visual asset manifests under
  `viewer/visual_assets/{pair}/{before|after}/{role}/visual_asset_manifest.json`;
- source PDF package copies are represented as `source_pdf` assets;
- rendered PNG backgrounds are represented as `raster_fallback` assets;
- CAD visual conversion metadata is represented as `cad_visual_provenance`
  `relative_only/skipped` assets, preserving disabled/deferred backend
  provenance without running CAD-to-PDF conversion in the viewer hot path;
- each emitted visual asset manifest includes source signatures with sample hash,
  deterministic cache key, plot profile hash, DPI/page/pixel metadata where
  available, backend/license fields, and a hash-linked `nonblank_probe.json`
  placeholder;
- the placeholder nonblank probe is recorded as `not_probed` with method
  `hash_link_only`, so customer-grade audit rejects it until a real pixel
  nonblank probe is wired;
- viewer pair entries and top-level viewer manifest now expose
  `visual_assets`, `visual_asset_manifest_paths`, and
  `visual_asset_manifest_count`;
- tile cache keys now accept a rendered background signature so page/DPI/render
  identity changes cannot reuse stale tiles only because the source path stayed
  the same;
- final MVP audit now adds `p5_g24_visual_asset_policy` for customer-grade
  evidence, discovers visual asset manifests through the audited viewer manifest,
  and fails on missing manifests or `validate_visual_asset_policy()` violations.

Validation evidence:

- `py_compile` passed for changed viewer package, tile cache, audit, and tests;
- targeted viewer package, tile cache, and MVP audit tests passed:
  `146 passed`;
- expanded P5-G24 target suite passed:
  `170 passed` across visual asset policy, CAD visual backend, CAD policy gate,
  viewer package, tile cache, and MVP audit tests;
- repository CAD policy gate passed with `python scripts\cad_policy_gate.py`;
- `git diff --check` passed for the touched P5-G24 files.

Self-review findings:

- This slice deliberately makes real viewer-package outputs fail customer-grade
  P5-G24 until a pixel-level nonblank probe is implemented. That is correct:
  a file hash is provenance, not visual nonblank evidence.
- `prepare_drawing_compare_customer_evidence.py` and
  `inventory_drawing_compare_customer_evidence.py` still need P5-G24 summary
  propagation so operators can see the issue before the final audit.
- Sidecar PDF discovery is still limited; the next slice should add explicit
  sidecar keys from artifact manifests/rows before treating CAD sidecar PDFs as
  first-class visual assets.
- Visual asset cache key v1.1 still needs page size, pixel size, transform
  quality, and nonblank probe hash as first-class key components. The tile cache
  currently consumes rendered background signature as the safe interim guard.

Next slice:

1. Add a real pixel nonblank probe for PDF/page/raster visual assets and hash
   link it into `VisualAssetManifest`.
2. Make prepare/inventory summarize P5-G24 visual asset readiness and fail
   readiness when manifests are missing or policy-invalid.
3. Add sidecar PDF discovery and tests for `sidecar_pdf` manifests.
4. Promote visual asset cache key v1.1 once nonblank probe hash and page geometry
   are available.

## 26. 2026-05-28 P5-G24 Pixel Nonblank Probe and Evidence Propagation

This slice turns the previous hash-link-only placeholder into real pixel-level
viewer evidence for already-rendered bitmap targets. It keeps the PDF-first
boundary strict: the probe does not open PDFs or CAD files by itself, and it does
not add a new render/conversion path. Source PDFs are probed through the rendered
PNG background that the viewer package already produced.

Implemented behavior:

- `probe_visual_asset_nonblank()` records schema v2 pixel probe artifacts with
  asset hash, probe-target hash, cache key, page/DPI, pixel dimensions, mean,
  channel ranges, extrema, nonblank boolean, and deterministic `probe_hash`;
- PDF visual assets require an explicit rendered bitmap `probe_target_path`;
  without one, they are recorded as `not_probed` instead of pretending a PDF byte
  hash proves visual content;
- viewer-package manifests now write `pixel_nonblank_probe` metadata for
  source-PDF and raster fallback assets when a rendered PNG target exists;
- customer-grade visual asset policy now requires `nonblank_probe`,
  `nonblank_probe_hash`, `probe_target_hash`, and `probe_method`;
- final MVP audit reloads the probe JSON, recomputes the probe hash, verifies the
  probe status/nonblank result, resolves the probe target, and compares the
  recorded target hash against the actual file;
- prepare and inventory now summarize `p5_g24_visual_asset_policy` and fail
  customer-grade readiness when completed validation outputs lack viewer visual
  asset manifests or have failed/missing nonblank proof.

Validation evidence:

- prepare/inventory P5-G24 propagation tests passed: `75 passed`;
- expanded P5-G24 target suite passed: `252 passed` across visual asset policy,
  CAD visual backend, CAD policy gate, tile cache, viewer package, MVP audit,
  prepare, and inventory tests;
- repository policy gate passed with `python scripts\cad_policy_gate.py`;
- `git diff --check` passed after code and test changes.

Self-review findings:

- This is intentionally probe-over-rendered-output, not a second renderer. That
  avoids reintroducing the performance issue the PDF-first plan is trying to
  eliminate.
- The audit now treats manifests as pointers to evidence, not evidence by
  themselves. Broken probe files, altered probe hashes, and target-hash mismatch
  are release-blocking.
- Sidecar PDF discovery is still not complete. `source_pdf` and
  `raster_fallback` are now hardened; `sidecar_pdf` should be the next P5-G24
  completion item before enabling any CAD-to-PDF customer workflow.
- Visual asset cache key v1.1 still needs page geometry, transform quality, and
  probe hash promoted into first-class cache identity.

Next slice:

1. Add sidecar PDF discovery and `sidecar_pdf` visual asset manifests.
2. Promote visual asset cache key v1.1 with page geometry, transform quality,
   and nonblank probe hash.
3. Add stale visual asset reuse tests that prove changed rendered output cannot
   reuse old tiles or old probe evidence.
4. Move to P5-G26 GUI hot-path gates once P5-G24 cache identity is closed.

## 27. 2026-05-28 P5-G24 Sidecar PDF and Visual Cache Identity Slice

This slice closes the next P5-G24 gap without enabling CAD-to-PDF conversion.
It treats sidecar PDFs as discovered visual assets only, and hardens cache
identity so page geometry, pixel geometry, transform quality, and rendered
visual content can invalidate stale viewer tiles.

Implemented behavior:

- viewer package discovers conservative sidecar PDF fields from pair artifacts
  or zone rows: `sidecar_pdf_a/b`, `before_sidecar_pdf/after_sidecar_pdf`,
  `source_a_pdf/source_b_pdf`, `before_pdf/after_pdf`, and converted-PDF
  aliases;
- discovered sidecar PDFs are copied into `viewer/pages` and emitted as
  `sidecar_pdf` visual asset manifests under the existing visual-assets tree;
- PDF sources still use `source_pdf`; sidecar discovery does not duplicate
  `source_pdf` assets when the source itself is already PDF;
- `build_visual_asset_cache_key()` is promoted to schema `1.1` by adding
  `page_size_pt`, `pixel_size`, and `transform_quality` to the provenance hash;
- `validate_visual_asset_policy()` now recomputes the v1.1 key with the same
  geometry/quality fields and rejects stale keys when transform quality or
  page/pixel geometry changes;
- viewer-package tile cache keys now receive a pair-level visual asset identity
  hash built from visual asset cache keys and content hashes, including
  `probe_target_hash`, while avoiding path-sensitive `probe_hash` values so
  repeat exports to different viewer directories still reuse cache correctly.

Validation evidence:

- focused visual/cache/viewer tests passed: `56 passed`;
- audit/prepare/inventory propagation tests passed: `186 passed`;
- expanded P5-G24 target suite passed: `257 passed` across visual asset policy,
  CAD visual backend, CAD policy gate, tile cache, viewer package, MVP audit,
  prepare, and inventory tests;
- repository policy gate passed with `python scripts\cad_policy_gate.py`;
- `git diff --check` passed.

Self-review findings:

- The slice intentionally records discovered sidecar PDFs as `source_only`
  unless a future rendering path proves the sidecar rendered pixels. That keeps
  provenance honest and avoids claiming customer-grade nonblank evidence from a
  file path alone.
- The initial design idea of placing `nonblank_probe_hash` directly into tile
  cache identity was rejected during testing because probe hashes include output
  paths and would break repeat-cache reuse across viewer directories. The final
  design uses stable content hashes (`asset_hash`, `probe_target_hash`) for tile
  invalidation and leaves `probe_hash` for audit evidence integrity.
- Existing cache entries will intentionally miss after key v1.1 because the
  identity now covers geometry and transform quality.

Next slice:

1. Add a rendered-sidecar path only when the viewer has explicit sidecar-derived
   bitmap evidence, then allow `sidecar_pdf` to become customer-grade `ready`.
2. Add stale visual asset reuse tests around changed `probe_target_hash` with
   retained cache manifests from a previous run.
3. Start P5-G26 GUI hot-path gates: prove pair/page selection does not trigger
   full conversion, full tile pyramid generation, or eager overlay materializing.

## 28. 2026-05-28 P5-G26 Selection Latency Contract Seed

This slice starts P5-G26 without replacing the existing P2/P5 GUI benchmark
probes. It adds a named evidence envelope and aggregate contract gates on top
of the existing hot-path measurements so later prepare/inventory/audit scripts
can discover and validate P5-G26 as a first-class customer artifact.

Implemented behavior:

- `benchmark_workbench_gui_hotpath.py` now exposes
  `P5_G26_BENCHMARK_ID = "p5_g26_selection_latency_soak"` and
  `P5_G26_PROFILE = "selection_latency_hard_gate"`;
- `--include-p5-g26-contract` writes `benchmark_id`, `profile`,
  `p5_g26_evidence`, `p5_g26_contract`, and `p5_g26_required_gate_names` into
  the benchmark payload;
- P5-G26 WP-A gates aggregate cached/cold pair selection latency, hard
  event-loop max, no eager tile-pyramid work, no first-paint full overlay JSON,
  no first-visible full overlay cache materialisation, and no CAD conversion on
  the click hot path;
- P5-G26 WP-B gates aggregate cached page navigation render-call count,
  repeated cached-navigation hit rate, blank viewer count, event-loop max, and
  CAD-to-PDF hot-path count;
- `--include-p5-g26-contract` now also runs the synthetic
  `zone_selection_hotpath_probe`, and `--include-zone-selection-hotpath` can run
  it directly with configurable selection count, zone count, and p95 target;
- the zone-selection gates require explicit evidence, matching viewer_perf
  telemetry count, p95 within budget, no worker/process/full-tree/crop/vector
  background work, and no stale/cancel/fallback visible-zone results;
- the contract is opt-in, so the default fast benchmark keeps its existing
  behavior while explicit P5-G26 runs produce machine-readable evidence.

Validation evidence:

- `python -m py_compile scripts\benchmark_workbench_gui_hotpath.py
  tests\unit\scripts\test_benchmark_workbench_gui_hotpath.py`;
- `python -m pytest tests\unit\scripts\test_benchmark_workbench_gui_hotpath.py
  -q -o log_cli=false --tb=short` passed with `24 passed`.

Self-review findings:

- This is an evidence contract seed, not full customer-grade propagation. It
  gives downstream scripts a stable benchmark id/profile/gate set to validate,
  but prepare/inventory/final audit still need explicit P5-G26 wiring.
- The CAD-to-PDF hot-path count is currently a contract field. Final
  customer-grade enforcement should combine this benchmark field with P5-G24
  visual-asset policy checks so conversion provenance cannot bypass the gate.
- `--include-p5-g26-contract` intentionally fails when cached page-navigation
  evidence is missing. P5-G26 acceptance runs should include the real PDF
  prewarm/cache navigation probe rather than treating missing evidence as a
  pass.
- The zone-selection probe is a handler-level hot-path proof. It intentionally
  disables lightweight pair loading, full-tree rebuild scheduling, vector
  render, crop QProcess launch, and delayed zone-render prewarm so it can
  isolate selection responsiveness; the production crop-first worker lifecycle
  still needs its own P5-G27/P5-G26 hardening gate.

Next slice:

1. Add production crop-first lifecycle gates for P5-G27/P5-G26: selected-zone
   crop worker cancellation, stale result suppression, and fallback-free visible
   output validation.
2. Run an end-to-end P5-G26 acceptance artifact with real PDF prewarm/cache
   navigation enabled, not just unit fixtures.
3. Feed P5-G26 into the composite P5-G30 customer visual-performance release
   gate.

## 29. 2026-05-28 P5-G26 Evidence Propagation

This slice turns the P5-G26 benchmark envelope into a customer-grade evidence
artifact instead of leaving it as a standalone benchmark JSON.

Implemented behavior:

- `prepare_drawing_compare_customer_evidence.py` accepts repeatable
  `--p5-g26-selection-latency-json` / `--p5-g26-selection-latency-soak`
  inputs, records safe manifest artifact refs, includes input hashes in
  provenance, summarizes the P5-G26 contract, and rejects provided artifacts
  unless the contract and required gates pass;
- `inventory_drawing_compare_customer_evidence.py` discovers
  `p5_g26_selection_latency_soak.json`, summarizes pass/fail gate state,
  exposes count/pass diagnostics, and carries the P5-G26 path into recommended
  prepare/final-audit commands;
- `audit_drawing_compare_mvp_exit.py` accepts repeatable P5-G26 paths, searches
  release/customer manifests and result-dir sibling artifacts, and makes
  `p5_g26_selection_latency_soak` required automatically for
  `customer_grade` audits;
- final audit validates the P5-G26 string schema, benchmark id/profile, status,
  `p5_g26_contract` / `p5_g26_evidence`, WP-A/WP-B pass flags,
  zone-selection evidence, no CAD conversion/background work counters, declared
  required gate names, and all required gate pass states.

Validation evidence:

- `python -m py_compile scripts\prepare_drawing_compare_customer_evidence.py
  scripts\inventory_drawing_compare_customer_evidence.py
  scripts\audit_drawing_compare_mvp_exit.py
  tests\unit\services\comparison\test_prepare_drawing_compare_customer_evidence.py
  tests\unit\services\comparison\test_inventory_drawing_compare_customer_evidence.py
  tests\unit\services\comparison\test_audit_drawing_compare_mvp_exit.py`;
- `python -m pytest
  tests\unit\services\comparison\test_prepare_drawing_compare_customer_evidence.py
  tests\unit\services\comparison\test_inventory_drawing_compare_customer_evidence.py
  tests\unit\services\comparison\test_audit_drawing_compare_mvp_exit.py
  -q -o log_cli=false --tb=short` passed with `193 passed`.

Self-review findings:

- The propagation gate proves artifact shape, required gate names, and pass/fail
  state. It does not prove a real customer-sized P5-G26 run has already been
  executed; that remains an acceptance execution task.
- P5-G26 is intentionally not tied to customer manifest hashes because it is a
  synthetic hot-path benchmark over GUI/PDF behavior. The audit instead proves
  that customer-grade release packets include a current, passing benchmark
  contract.
- The production selected-zone crop/vector worker lifecycle remains outside
  this slice and belongs in P5-G27/P5-G26 hardening.

## 30. 2026-05-28 P5-G27 Selected-Zone Crop-First Contract Seed

This slice starts P5-G27 as a separate selected-zone lifecycle benchmark rather
than overloading the P5-G26 selection-latency contract. P5-G26 proves the zone
selection handler does not trigger heavy work. P5-G27 proves the heavy selected
zone lifecycle itself is safe: crop becomes visible first, vector enhancement
is deferred, and vector failure does not blank the already visible crop.

Implemented behavior:

- `benchmark_workbench_gui_hotpath.py` now exposes
  `P5_G27_BENCHMARK_ID = "p5_g27_selected_zone_crop_soak"` and
  `P5_G27_PROFILE = "selected_zone_crop_first_lifecycle"`;
- `--include-p5-g27-selected-zone-crop-first` writes `benchmark_id`,
  `profile`, `p5_g27_evidence`, `p5_g27_contract`, and
  `p5_g27_required_gate_names` into the benchmark payload;
- the new `selected_zone_crop_first_probe` uses the real workbench zone
  selection and crop-finish handlers with a fake crop controller so the test
  exercises request-id ordering without spawning QProcess workers;
- the probe forces a non-PDF/CAD pair so vector enhancement is attempted after
  crop completion, then simulates vector worker failure through the existing
  vector finish handler;
- P5-G27 gates require crop-visible evidence for every completed selection,
  crop completion before deferred focus/vector start, crop-visible p95 within
  budget, vector failure preserving the crop background, zero blank selected
  zones, zero stale/cancel/fallback/timeout regressions, bounded event-loop
  gaps, and no worker cleanup debt.
- comparison package import now disables Python's Windows WMI platform probe
  before CAD/ezdxf imports, forcing the stdlib fallback path when WMI is
  unhealthy so benchmark/test startup does not hang in `platform.system()`.

Validation evidence:

- `python -m py_compile scripts\benchmark_workbench_gui_hotpath.py`;
- `python -m pytest tests\unit\scripts\test_benchmark_workbench_gui_hotpath.py
  -q -o log_cli=false --tb=short` passed with `28 passed`;
- selected-zone GUI lifecycle subset passed with `5 passed, 43 deselected`
  under the same WMI fallback wrapper;
- smoke artifact command passed and wrote
  `.tmp\p5_g27_smoke_current\p5_g27_selected_zone_crop_soak.json` with
  `benchmark_id=p5_g27_selected_zone_crop_soak`, `profile`,
  `status=passed`, required gate names, and all P5-G27 gates passing.

Self-review findings:

- This is a contract seed over synthetic selected-zone lifecycle behavior. It
  proves crop-first ordering and vector-failure isolation through the real GUI
  handlers, but it does not yet replace P5-G15/P5-G16 real render evidence.
- The fake crop controller is intentional: this first gate should catch GUI
  lifecycle regressions without depending on DXF/PDF renderer cost or worker
  availability. Production renderer cache/RSS behavior remains covered by
  P5-G15/P5-G16 and should be joined into P5-G27 propagation later.
- P5-G27 is not yet wired into prepare/inventory/final audit. The next slice
  should mirror the P5-G26 propagation pattern with
  `p5_g27_selected_zone_crop_soak.json`.
- The Windows WMI import guard is intentionally local to the comparison
  package. Standalone pytest startup can still hit WMI before importing project
  code through third-party packages such as `faker` or `pyreadline3`; the local
  validation wrapper patched `platform._wmi_query` at process start to prove the
  project tests.

Next slice:

1. Propagate `p5_g27_selected_zone_crop_soak.json` through inventory, prepare,
   and final audit, including required gate-name validation.
2. Add a real renderer-backed P5-G27 mode or bridge from P5-G15/P5-G16 so
   customer-grade packages prove both lifecycle safety and render quality.
3. Feed P5-G27 into the P5-G30 composite visual-performance release gate.

## 31. 2026-05-28 P5-G27 Evidence Propagation

This slice promotes the P5-G27 crop-first benchmark from a standalone synthetic
contract into the customer-grade evidence pipeline.

Implemented behavior:

- `prepare_drawing_compare_customer_evidence.py` accepts repeatable
  `--p5-g27-selected-zone-crop-json` /
  `--p5-g27-selected-zone-crop-soak` inputs, records safe artifact refs,
  includes input hashes in manifest provenance, summarizes the P5-G27 contract,
  and rejects provided artifacts unless required gates pass;
- `inventory_drawing_compare_customer_evidence.py` discovers
  `p5_g27_selected_zone_crop_soak.json`, reports candidate/pass/fail
  diagnostics, and carries the path into recommended prepare/final-audit
  commands;
- `audit_drawing_compare_mvp_exit.py` accepts explicit P5-G27 paths, searches
  release/customer manifest references and result-dir sibling artifacts, and
  auto-requires `p5_g27_selected_zone_crop_soak` for `customer_grade` audits;
- final audit validates schema/profile/status, `p5_g27_contract` /
  `p5_g27_evidence`, crop-first visibility, crop-before-vector ordering,
  vector-failure background preservation, evidence presence, worker cleanup,
  zero blank/stale/cancel/timeout/fallback/orphan counters, declared required
  gate names, and all required gate pass states.

Validation evidence:

- `python -m py_compile scripts\prepare_drawing_compare_customer_evidence.py
  scripts\inventory_drawing_compare_customer_evidence.py
  scripts\audit_drawing_compare_mvp_exit.py
  tests\unit\services\comparison\test_prepare_drawing_compare_customer_evidence.py
  tests\unit\services\comparison\test_inventory_drawing_compare_customer_evidence.py
  tests\unit\services\comparison\test_audit_drawing_compare_mvp_exit.py`;
- focused P5-G27 propagation tests passed with `7 passed`;
- prepare/inventory/final-audit regression files passed with `200 passed`;
- `git diff --check` passed.

Self-review findings:

- The final audit now prevents a customer-grade packet from omitting the
  crop-first lifecycle proof, but the benchmark remains synthetic.
- The P5-G27 propagation proves lifecycle ordering and failure isolation, not
  production renderer visual quality. Real renderer-backed P5-G27 or a bridge
  from P5-G15/P5-G16 remains the next technical risk to close.
- Candidate path discovery mirrors P5-G26, so release manifests, customer
  manifests, validation summaries, and result-dir sibling JSON are all accepted.

Next slice:

1. Add a real renderer-backed P5-G27 evidence mode or explicitly bind P5-G27
   to P5-G15/P5-G16 replay outputs for production visual-quality proof.
2. Feed P5-G27 status into the P5-G30 composite visual-performance release
   gate.
3. Add closeout/readiness/release packaging propagation if the customer packet
   tooling needs a separate P5-G27 handoff path beyond final audit discovery.

## 32. 2026-05-28 P5-G27 Closeout and Release Routing

This slice closes the handoff gap left after P5-G27 evidence propagation by
making the closeout runner, readiness audit, release manifest, README, and
customer closeout packet all route the same crop-first artifact explicitly.

Implemented behavior:

- `closeout_drawing_compare_customer_evidence.py` accepts repeatable
  `--p5-g27-selected-zone-crop-json` /
  `--p5-g27-selected-zone-crop-soak` inputs, checks they exist during
  preflight, forwards them to manifest preparation and final audit commands,
  records them in dry-run/passed outputs, and adds the invariant
  `final_audit_p5_g27_selected_zone_crop_jsons_equal_plan`;
- `audit_closeout_readiness.py` audits P5-G27 routing when explicit crop-first
  JSONs are supplied and fails the readiness packet if prepare/final-audit
  command values diverge from `plan.p5_g27_selected_zone_crop_jsons`;
- `release_drawing_compare_workbench.py` accepts and validates P5-G27 JSON
  inputs, writes release-manifest artifact refs, forwards the paths to the
  optional customer-grade audit, and includes the P5-G27 flag in the generated
  README, prompt-to-artifact checklist, and customer closeout packet examples;
- release/closeout tests now cover alias parsing, missing artifact rejection,
  manifest artifact refs, explicit routing success, and mismatched final-audit
  routing failure.

Validation evidence:

- `python -m py_compile scripts\closeout_drawing_compare_customer_evidence.py
  scripts\audit_closeout_readiness.py
  scripts\release_drawing_compare_workbench.py
  tests\unit\scripts\test_audit_closeout_readiness.py
  tests\unit\services\comparison\test_release_drawing_compare_workbench.py`;
- focused closeout/release P5-G27 tests passed with `14 passed`;
- closeout/readiness/release regression files passed with `51 passed`;
- prepare/inventory/final-audit/closeout/readiness/release regression files
  passed with `251 passed`;
- `python scripts\cad_policy_gate.py` passed;
- `git diff --check` passed.

Self-review findings:

- P5-G27 is now visible across final-audit discovery and explicit closeout
  handoff paths, so reviewers no longer need to infer whether the crop-first
  JSON reached the final customer-grade audit.
- The closeout runner still does not generate P5-G27 automatically. That is
  intentional for this slice because generation order must be handled carefully:
  prepare rejects invalid supplied P5-G27 artifacts, while generated artifacts
  would need to exist before strict manifest preparation can include them.
- The technical risk remaining from Section 31 is unchanged: current P5-G27
  evidence proves GUI lifecycle ordering and failure isolation, not a real
  renderer-backed crop-quality contract.

Next slice:

1. Add a real renderer-backed P5-G27 mode or explicitly bind P5-G27 to
   P5-G15/P5-G16 outputs so crop-first safety and production visual quality are
   proven together.
2. Decide whether closeout should auto-generate P5-G27 after a strict
   generation-order design, or keep P5-G27 as an explicit supplied artifact.
3. Feed P5-G27 status into the P5-G30 composite visual-performance release
   gate.

## 33. 2026-05-28 P5-G27 Real Renderer Bridge

This slice binds P5-G27 crop-first lifecycle evidence to existing P5-G16
real-corpus renderer replay evidence. It avoids adding another heavy renderer
pass while ensuring customer-grade final audit no longer accepts a purely
synthetic P5-G27 JSON.

Implemented behavior:

- `benchmark_workbench_gui_hotpath.py` accepts
  `--p5-g27-real-renderer-bridge-json <p5_g16_real_corpus_replay.json>` and
  `--p5-g27-require-real-renderer-bridge` for P5-G27 runs;
- P5-G27 payloads can now include `p5_g27_real_renderer_bridge` with the
  bridged P5-G16 benchmark id/profile/status, validation-summary hash,
  selected-zone artifact count, nonblank/missing-image/fallback-reason
  counters, stale/timeout/cancel counters, and an aggregate
  `real_renderer_quality_passed` flag;
- P5-G27 benchmark gates add
  `p5_g27_real_renderer_bridge_*` checks when a bridge is supplied or required;
- customer-grade `audit_drawing_compare_mvp_exit.py` now requires the bridge
  inside `p5_g27_selected_zone_crop_soak.json`, requires the bridge gate-name
  declaration, and fails when P5-G16 did not pass, no real selected-zone render
  artifacts exist, blank/missing/fallback/stale/timeout/cancel counts are
  nonzero, or bridge gates are missing/failed;
- release README/checklist/closeout-packet text now states that the supplied
  P5-G27 JSON must include the P5-G16 bridge so crop-first safety is bound to
  real nonblank selected-zone render artifacts.

Validation evidence:

- `python -m py_compile scripts\benchmark_workbench_gui_hotpath.py
  scripts\audit_drawing_compare_mvp_exit.py
  scripts\release_drawing_compare_workbench.py
  tests\unit\scripts\test_benchmark_workbench_gui_hotpath.py
  tests\unit\services\comparison\test_audit_drawing_compare_mvp_exit.py
  tests\unit\services\comparison\test_release_drawing_compare_workbench.py`;
- focused P5-G27 bridge/audit/release tests passed with `7 passed` and
  `3 passed`;
- full benchmark/final-audit regression files passed with `152 passed`;
- release regression file passed with `31 passed`;
- `python scripts\cad_policy_gate.py` passed;
- `git diff --check` passed.

Self-review findings:

- The bridge is a strict evidence join, not a new renderer execution path. This
  is the right minimal step because P5-G16 already validates real selected-zone
  artifacts, RSS/cache behavior, blank/missing image counts, and fallback
  reason hygiene.
- Customer-grade final audit now rejects synthetic-only P5-G27 payloads. Any
  closeout packet that supplies P5-G27 must regenerate it with the P5-G16 bridge
  or the final audit will fail.
- The remaining limitation is generation orchestration: closeout still routes
  explicit P5-G27 JSON but does not auto-generate a bridge-bearing P5-G27 JSON
  after P5-G16. That ordering should be designed before enabling automatic
  generation.

Next slice:

1. Design closeout generation order for bridge-bearing P5-G27, or keep P5-G27
   as explicit supplied evidence with stronger inventory diagnostics.
2. Add a P5-G30 composite customer visual-performance release gate that joins
   P5-G3, P5-G16, P5-G22, P5-G24, P5-G26, and P5-G27 status.
3. If renderer failures still appear in live runs, add a targeted production
   P5-G27 mode that opens real viewer packages and measures crop-first ordering
   directly rather than through the P5-G16 bridge.
