# Drawing Compare Workbench — Performance Notes

This document captures the performance characteristics of the
Drawing Compare Workbench after the Phase G2.7-PERF + Phase G3
optimisation passes (commits `5ff30277` → `b583797b`).

It's intended for:
- **Sales / pre-sales**: defending performance claims to customers
- **Operators**: tuning the env vars / DPI presets for their hardware
- **Developers**: regression baselines + reasoning about future changes

---

## TL;DR — what changed and why it matters

| Metric | Before | After | Improvement |
|---|---|---|---|
| Workbench startup RSS | 588 MB | **158 MB** | −73% (430 MB saved) |
| Subsequent PDF compare | 5.28 s | **1.24 s** | **4.3× faster** (model cache hit) |
| Skeleton render @ 5K lines | 27 ms | **4 ms** | 6.3× faster |
| Skeleton render @ 50K lines | 238 ms | **68 ms** | 3.5× faster |
| Unit-suite duration | 33 s | **18.5 s** | −44% |
| GUI verification on cluttered desktop | blocked | **always works** | (Qt-grab harness) |

---

## 1. PaddleOCR / EasyOCR lazy-load (Phase G2.7-PERF)

**Problem**. The drawing-compare module imported `paddleocr` and
`easyocr` at module load even for runs that never invoked OCR.
Workbench startup RSS jumped from ~50 MB to ~588 MB before the user
clicked anything. Most PDFs are vector and never need OCR fallback.

**Fix**. Three OCR-related modules now use a tri-state lazy probe:
- `_PADDLEOCR_AVAILABLE: Optional[bool] = None` (unprobed)
- First call to `_probe_paddleocr()` does the actual `import paddleocr`
- Result cached, subsequent calls return instantly
- Lock allocated at module load → fully thread-safe double-checked
  locking

**Files**:
- `src/services/comparison/paddle_ocr_backend.py`
- `src/services/comparison/layout_analyzer.py`
- `src/services/comparison/ocr_extractor.py`
- `src/services/comparison/drawing_differ.py` (defers
  `check_ocr_availability()` from module-load to runtime fallback)

**Trade-off**. The first PDF run that hits the OCR fallback path
(scanned drawing, no text layer) pays the import cost (~5s). Every
subsequent run reuses the cached models → 4.3× faster on repeat
compare.

**Verification**. 8 tests in `test_lazy_ocr_load.py` pin the
contract: module load doesn't probe, probe is idempotent, probe is
thread-safe, transitive imports don't accidentally re-trigger.

---

## 2. QSGLineItem — GPU-accelerated skeleton renderer (Phase G3)

**Problem**. The lightweight viewport drew skeleton primitives via
QML `Canvas { onPaint: ... }`. Canvas is single-threaded QPainter
that re-rasterises the entire scene every paint. For 50K line
segments this lands at 238 ms → <5 fps at high zoom.

**Fix**. `src/gui/qsg_line_item.py` adds a custom `QQuickItem` that
pushes line vertices straight into a GPU vertex buffer (`QSGGeometry`
in DrawLines mode). The QML container applies the world→pixel affine
via `transform: [Translate, Scale, Translate]` so the GPU does the
math, not Python/JS.

**Toggle**. `WORKBENCH_QSG` env var:
- `qsg` — force GPU path (default when import succeeds)
- `canvas` — force fallback (debugging, GPU driver issues)
- `auto` — QSG when available, else Canvas

**Trade-off**. Bezier curves degrade to straight-line endpoint
approximation in the QSG path (Canvas fallback supports full
beziers). For LOD0 skeletons this is visually negligible but it
matters if you need pixel-perfect curve rendering — set
`WORKBENCH_QSG=canvas` for that case.

---

## 3. Coordinate accuracy fixes (Phase G2.7-COORDFIX)

Bug class: PDF compare engine produces overlay bboxes at `pdf_dpi`
(default 200), but the lightweight viewport renders the page at
`preview_dpi` (default 400 for "구조도면 정밀"). Without DPI
scaling, overlays land at half the correct position.

**Fix**. Three call sites apply `image_dpi / pdf_dpi` scale:
- `src/gui/lightweight_viewport.py` — `convert_bbox_to_world_space`
  helper, used by `push_change_overlays_from_v1` and
  `_focus_lightweight_on_zone_v2`
- `src/gui/drawing_compare_workbench.py` — `_overlay_rect()` (legacy
  GpuDrawingViewport path)
- `src/services/comparison/confirmed_cloud_export.py` — cloud-export
  PNG generator (`_resolve_pixel_bbox` with `image_dpi` parameter)

**Verification**. `tools/verify_cloud_export_dpi_match.py` measures
cloud landing accuracy:
- Before fix: **0.9%** of clouds land on actual diff pixels
- After fix: **100%**

---

## 4. Page matching (Phase H1-H4)

PDF page matching algorithm — see
`src/services/comparison/page_matcher.py`. Recovers reorder
permutations even when page numbers shift between revisions.

| Pages | Matcher time | Accuracy |
|---|---|---|
| 9 | 1.3 s | 100% |
| 24 | 3.0 s | 100% |
| 100 (estimated) | ~10 s | (untested) |

Manual page override API (`manual_page_overrides.py`) lets the
reviewer force-match pairs the auto-matcher missed.

---

## 5. Memory budget (PDF compare)

Measured on user's `01.3PG1.pdf` (1 page, 200 DPI compare,
400 DPI preview):

| Stage | RSS | Notes |
|---|---|---|
| Process baseline | 20 MB | Python interpreter only |
| After workbench imports | 158 MB | (previously 588 MB before lazy-load) |
| After 1st compare | 767 MB | OCR models loaded once on demand |
| After 2nd compare | 875 MB | Stable; 4.3× faster than 1st run |
| Peak during 24-page compare | ~1.7 GB | Within 2 GB design budget |

**Operator guidance**:
- Vector PDFs (most CAD output): RSS stays under 200 MB
- Scanned PDFs (rare): expect ~500 MB once OCR models load
- 24+ page PDFs: budget 2 GB headroom

---

## 6. Performance benchmarks

Run `python tools/benchmark_workbench_perf.py` to measure on your
hardware. Output lands at `out/perf_<timestamp>.json` for CI
consumption.

Run `python tools/run_regression_dataset.py` for accuracy regression
testing — 6 samples covering small/PDF/multi-page/Korean filename
categories. All 6 must pass before shipping.

Run `python tools/gui_harness.py acceptance --before X.pdf --after
Y.pdf` for full GUI E2E without needing visible windows. Captures
5 screenshots + invariants JSON. Works on cluttered desktops where
computer-use masking would normally block visual verification.

---

## 7. Operator env vars

| Var | Values | Default | Purpose |
|---|---|---|---|
| `WORKBENCH_QSG` | `qsg` / `canvas` / `auto` | `auto` | Skeleton renderer |
| `QT_QPA_PLATFORM` | `offscreen` / `windows` | (Qt default) | Force headless |
| `WORKBENCH_PDF_DPI` | int (60-400) | 200 | Compare DPI |

---

## 8. Known limitations

- **Bezier in QSG**: straight-line endpoint approximation only. Set
  `WORKBENCH_QSG=canvas` if you need full bezier rendering.
- **Scanned PDFs**: first run pays ~5s OCR-import cost (lazy-load
  trade-off). Subsequent runs are normal speed.
- **24+ page PDFs**: page matcher is O(n²); plan for ~3s per 25
  pages.
- **GUI harness Korean text**: shows as squares in `out/gui_harness/
  *.png` because offscreen Qt platform lacks CJK font fallback.
  Production users see real text — captures are for layout
  verification only. Use `invariants_*.json` for text content
  assertions.

---

## 9. Regression coverage (as of commit b583797b)

- **1303 unit tests** in `tests/unit/` (services + gui)
- **6 sample regression dataset** in `tools/run_regression_dataset.py`
- **2 GUI scenarios** in `tools/gui_harness.py` (acceptance,
  multipage)
- **Performance baseline** in `tools/benchmark_workbench_perf.py`

All four must be re-run before shipping.
