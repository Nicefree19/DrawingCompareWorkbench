# CAD Milestone Verification Report

Date: 2026-05-22

Scope: validation of the ODA-free CAD import, canonical model, normalization,
comparison, DXF writer, regression-data, pipeline, stability, and AC1015 native
DWG reader milestone set.

## Result

Status: conditionally passed.

The current implementation satisfies the milestone integration target for
ODA-free DXF comparison and AC1015 MVP DWG ingestion through the canonical
pipeline. Remaining risk is limited to legal/product sign-off and real customer
DWG coverage, not to a known automatic ODA invocation in the validated default
path.

## Requirement Evidence

| Area | Evidence | Verification result |
| --- | --- | --- |
| Format support and license policy | `docs/CAD_FORMAT_SUPPORT_POLICY.md`, `docs/THIRD_PARTY_LICENSE_POLICY.md`, `docs/ENTITY_SUPPORT_MATRIX.md` | Version policy, entity support matrix, GPL/AGPL/ODA prohibitions, and legal review items are documented. |
| CanonicalDrawing model | `docs/canonical-drawing.schema.json`, `docs/canonical-entity-spec.md`, `docs/normalization-tolerance-policy.md` | Importers and compare engine share a format-independent canonical schema. |
| ASCII DXF importer | `src/services/comparison/dxf_importer.py` | Tokenizer, sections, supported entity mapping, block INSERT handling, warnings, limits, and tests are present. |
| Normalization | `src/services/comparison/drawing_normalizer.py` | Rounding, near-zero filtering, polyline canonicalization, flattening options, BYLAYER/BYBLOCK interpretation, and text normalization are implemented. |
| Compare engine | `src/services/comparison/drawing_compare_engine.py` | Spatial candidate matching, deterministic scoring, entity diffs, and UI-oriented JSON result structure are implemented. |
| DWG adapter and native reader | `src/services/comparison/dwg_importer.py`, `src/services/comparison/dwg_binary_reader.py`, `src/services/comparison/dwg_section_reader.py`, `src/services/comparison/dwg_object_decoder.py`, `src/services/comparison/dwg_native_reader.py` | AC1015 native MVP reads header/sections/object-map style payloads and maps basic 2D objects to CanonicalDrawing; AC1018+ remain planned. |
| DXF writer | `src/services/comparison/dxf_writer.py` | CanonicalDrawing to R2000 DXF export and roundtrip tests are present. |
| Regression data and CI | `tests/data/comparison/cad_samples/`, `tests/data/comparison/cad_samples/golden-results.json`, `.github/workflows/cad-format-regression.yml` | Manifest, golden results, regression script, and CI workflow are wired. CI now also covers batch default engine, preflight, and zone vector DWG debug export. |
| Import/compare pipeline | `src/services/comparison/import_pipeline.py`, `src/services/comparison/dwg_differ.py`, `src/services/comparison/drawing_batch.py` | Default path is CanonicalDrawing import, normalize, compare. Legacy ezdxf/ODA path is explicit opt-in only. |
| Stability and performance | `src/services/comparison/cad_stability.py`, `scripts/cad_performance_benchmark.py`, `docs/CAD_PERFORMANCE_OPTIMIZATION_REPORT.md` | Timeout, token/entity/block limits, malformed input defenses, and benchmark script are present. |

## Defects Fixed During Verification

| Finding | Fix |
| --- | --- |
| Policy docs still implied direct DWG support was not embedded after AC1015 native MVP work. | Updated `CAD_FORMAT_SUPPORT_POLICY.md`, `THIRD_PARTY_LICENSE_POLICY.md`, and `ENTITY_SUPPORT_MATRIX.md` to distinguish internal AC1015 native preview from unsupported AC1018+ native scope. |
| Performance report did not mention DXF import timeout defense. | Updated `CAD_PERFORMANCE_OPTIMIZATION_REPORT.md`. |
| Zone vector rendering still converted DWG through `DwgConverter` automatically. | Replaced that path with `ImportPipeline(... allow_oda_fallback=False)` plus `DxfWriter` debug export in `zone_vector_renderer.py`; added a no-ODA regression test. |
| Batch comparison still defaulted to `legacy_ezdxf`, which could reach ODA for DWG. | Changed `BatchCompareOptions.cad_compare_engine` default to `canonical`; legacy is now explicit opt-in. |
| Preflight and release environment text treated ODA as required/missing. | Reworded ODA as a legacy fallback that is not required and must remain disabled in customer builds. |

## Verification Commands

```powershell
python -m py_compile src\services\comparison\cad_stability.py src\services\comparison\dxf_importer.py src\services\comparison\dxf_writer.py src\services\comparison\drawing_normalizer.py src\services\comparison\drawing_compare_engine.py src\services\comparison\dwg_importer.py src\services\comparison\dwg_binary_reader.py src\services\comparison\dwg_section_reader.py src\services\comparison\dwg_object_decoder.py src\services\comparison\dwg_native_reader.py src\services\comparison\import_pipeline.py src\services\comparison\drawing_batch.py src\services\comparison\preflight.py src\services\comparison\zone_vector_renderer.py scripts\cad_format_regression.py scripts\cad_performance_benchmark.py scripts\release_environment_check.py scripts\release_drawing_compare_workbench.py
```

Result: passed.

```powershell
python scripts\cad_format_regression.py --check --report build\reports\cad-format-regression-report.md
```

Result: passed; report written to `build/reports/cad-format-regression-report.md`.

```powershell
python scripts\cad_performance_benchmark.py --line-counts 1000 --target-mb 1 --size-case-lines 1000 --timeout 60 --max-entities 2000 --max-tokens 1000000
```

Result: passed.

| Case | Input bytes | Entities | Import | Normalize | Compare |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1000-lines | 54,036 | 1,000 | 0.020924s | 0.112093s | 0.136575s |
| 1MB-input | 1,048,576 | 1,000 | 0.022120s | 0.061613s | 0.131100s |

```powershell
python -m pytest tests\unit\services\comparison\test_dxf_importer.py tests\unit\services\comparison\test_dxf_writer.py tests\unit\services\comparison\test_drawing_normalizer.py tests\unit\services\comparison\test_drawing_compare_engine.py tests\unit\services\comparison\test_dwg_importer.py tests\unit\services\comparison\test_dwg_native_reader.py tests\unit\services\comparison\test_import_compare_pipeline.py tests\unit\services\comparison\test_cad_stability_limits.py tests\unit\services\comparison\test_cad_format_regression.py tests\unit\services\comparison\test_dwg_differ_cleanup.py tests\unit\services\comparison\test_drawing_batch.py tests\unit\services\comparison\test_preflight.py tests\unit\services\comparison\test_zone_vector_renderer.py -q -o log_cli=false
```

Result: `134 passed`.

## Legal Review Required

The following items must remain flagged for legal/product review before a
customer release:

- Any direct DWG support claim beyond the documented AC1015 native MVP scope.
- Any ODA residual code path in installer, CI, release scripts, or customer
  runtime packaging.
- Any GPL, AGPL, no-commercial, no-redistribution, or copyleft CAD dependency.
- Any use of public DWG specification material beyond clean-room,
  implementation-safe references approved by counsel.

## Residual Risks

- AC1015 native reader coverage is MVP-level and fixture/sample oriented. It is
  not yet certified against broad commercial AC1015 DWG object diversity.
- AC1018, AC1021, AC1024, AC1027, and AC1032 native readers are planned, not
  implemented as production-capable decoders.
- The benchmark run is synthetic and small by design. The code has benchmark
  tooling and limits, but customer-grade 10MB/50MB/100k-entity evidence still
  requires representative real drawings.
- Legacy ODA wrapper code remains in source behind explicit fallback/internal
  compatibility paths. It must not be packaged or enabled for customer builds
  without separate approval.
