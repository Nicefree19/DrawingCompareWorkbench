# CAD Performance Optimization Report

## Scope

This report covers stability and performance hardening for the ODA-free CAD pipeline:

`Importer -> CanonicalDrawing -> Normalize -> Compare`

## Stability Limits

Runtime guardrails are centralized in `CadStabilityLimits`.

| Limit | Default | Purpose |
| --- | ---: | --- |
| `import_timeout_seconds` | 30.0 | Stop pathological or unexpectedly large imports. |
| `max_entities` | 100000 | Keep CanonicalDrawing memory bounded for production runs. |
| `max_dxf_tokens` | 2500000 | Stop malformed or very large ASCII DXF streams before expansion. |
| `max_block_depth` | 4 | Prevent recursive/nested block expansion blow-ups. |
| `max_spatial_cells_per_entity` | 4096 | Prevent huge malformed bboxes from exploding spatial-index buckets. |

Timeout and cancellation are reported as explicit error codes, not process crashes.

## Optimization Changes

- DXF tokenizer now checks cancellation, timeout, and token limits during tokenization.
- DXF importer now checks timeout/cancel/entity count while mapping block and model entities.
- DWG importer now checks timeout/cancel/entity count while mapping adapter entities.
- Block expansion emits `CAD_BLOCK_RECURSION_LIMIT` warning when max depth is reached.
- Compare matcher now pre-matches identical `type + layer + geometry_hash` entities before spatial search.
- Spatial index now routes very wide malformed bboxes through an overflow path instead of enumerating unbounded grid cells.
- BBox normalization now clamps non-finite values before spatial indexing.

## Benchmark

Benchmark CLI:

```powershell
python scripts\cad_performance_benchmark.py --line-counts 1000,10000,100000 --target-mb 10,50 --size-case-lines 100000 --timeout 300 --max-entities 120000 --max-tokens 30000000 --output tmp\cad-performance-benchmark.json
```

Benchmark cases and acceptance signals:

| Case | Target | Required signal |
| --- | --- | --- |
| 10 MB DXF-equivalent | Import + normalize + self-compare finishes under timeout | No crash; stable entity count and diff summary |
| 50 MB DXF-equivalent | Import either finishes or fails with explicit limit/timeout | No process crash |
| 100k entities | Import + normalize + self-compare with hash pre-match | No O(n^2) candidate explosion |

Local synthetic LINE benchmark on 2026-05-22:

| Case | Input bytes | Entities | Import | Normalize | Compare | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1k lines | 54036 | 1000 | 0.021s | 0.105s | 0.119s | 1000 unchanged |
| 10k lines | 558036 | 10000 | 0.323s | 0.722s | 1.319s | 10000 unchanged |
| 100k lines | 5778036 | 100000 | 3.129s | 7.806s | 14.881s | 100000 unchanged |
| 10 MB input, 100k lines | 10485760 | 100000 | 2.756s | 8.117s | 14.278s | 100000 unchanged |
| 50 MB input, 100k lines | 52428800 | 100000 | 2.807s | 8.123s | 13.921s | 100000 unchanged |

The 10 MB and 50 MB cases use valid DXF comments as padding so input-size handling is measured independently from entity-count growth.

The generated JSON records `import_s`, `normalize_s`, `compare_s`, `entity_count`, input bytes, limits, diff summary, and `stability_cases` for malformed/unsupported-heavy DXF inputs. Store benchmark outputs under `tmp/` or CI artifacts, not as golden source.

## Malformed Input Defense

Covered cases:

- Invalid DXF group code -> `DXF_PARSE_ERROR`
- DXF timeout -> `CAD_IMPORT_TIMEOUT`
- DXF token limit -> `CAD_TOKEN_LIMIT_EXCEEDED`
- DXF entity limit -> `CAD_ENTITY_LIMIT_EXCEEDED`
- DXF cancel callback -> `CAD_IMPORT_CANCELLED`
- Corrupted DWG header -> `DWG_CORRUPTED`
- DWG entity limit -> `DWG_ENTITY_LIMIT_EXCEEDED`
- Non-finite bbox in compare input -> no crash; bbox is clamped before spatial indexing

## Operational Policy

- Customer builds should keep default `CadStabilityLimits` unless a project-specific benchmark justifies raising them.
- For very large real drawings, raise limits in config with an explicit timeout and keep cancellation wired from the UI worker.
- A limit failure is a controlled failed import, not an application failure.
- Performance benchmark changes should be reviewed with both wall-clock time and candidate count behavior.
