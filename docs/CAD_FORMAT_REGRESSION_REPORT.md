# CAD Format Regression Report

Status: **PASS**

## Scope

- Manifest: `tests/data/comparison/cad_samples/manifest.yaml`
- Golden results: `tests/data/comparison/cad_samples/golden-results.json`
- CI workflow: `.github/workflows/cad-format-regression.yml`
- Test entrypoint: `tests/unit/services/comparison/test_cad_format_regression.py`

## Coverage Summary

- Samples: 100
- Manifest sample definitions: 7
- Generated sample definitions: 7
- Diff cases: 1
- Fuzz cases: 2
- Golden sample snapshots: 100
- Current sample snapshots: 100

## Sample Snapshots

| Sample | Category | Format | Status | Entities | Layers | Warnings | Geometry Hash |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| block_centered | block_centered | dxf | ok | 8 | 2 | 0 | 59254294f2f9 |
| gen_block_001 | block_centered | dxf | ok | 5 | 5 | 0 | e61e0771a437 |
| gen_block_002 | block_centered | dxf | ok | 8 | 5 | 0 | 89031488e7a3 |
| gen_block_003 | block_centered | dxf | ok | 11 | 5 | 0 | ded8758a6ae6 |
| gen_block_004 | block_centered | dxf | ok | 5 | 5 | 0 | 21fad19a6f5c |
| gen_block_005 | block_centered | dxf | ok | 8 | 5 | 0 | cd1331e2e2a5 |
| gen_block_006 | block_centered | dxf | ok | 11 | 5 | 0 | c55846f539c5 |
| gen_block_007 | block_centered | dxf | ok | 5 | 5 | 0 | 130bb3974f9d |
| gen_block_008 | block_centered | dxf | ok | 8 | 5 | 0 | 672b61926a08 |
| gen_block_009 | block_centered | dxf | ok | 11 | 5 | 0 | c2fbf35d3b16 |
| gen_block_010 | block_centered | dxf | ok | 5 | 5 | 0 | 13d7b9c79f31 |
| gen_block_011 | block_centered | dxf | ok | 8 | 5 | 0 | ff668fe9c1f4 |
| gen_block_012 | block_centered | dxf | ok | 11 | 5 | 0 | fb81201b4774 |
| gen_block_013 | block_centered | dxf | ok | 5 | 5 | 0 | b1594b7fd749 |
| gen_block_014 | block_centered | dxf | ok | 8 | 5 | 0 | 0c7b1f17768b |
| gen_block_015 | block_centered | dxf | ok | 11 | 5 | 0 | 38ad75d35322 |
| gen_customer_sanitized_001 | customer_sanitized | dxf | ok | 4 | 5 | 0 | 1665de940b95 |
| gen_customer_sanitized_002 | customer_sanitized | dxf | ok | 4 | 5 | 0 | 3b675f9247b0 |
| gen_customer_sanitized_003 | customer_sanitized | dxf | ok | 4 | 5 | 0 | 8e74ce511c32 |
| gen_customer_sanitized_004 | customer_sanitized | dxf | ok | 4 | 5 | 0 | 5a6a968edd0d |
| gen_customer_sanitized_005 | customer_sanitized | dxf | ok | 4 | 5 | 0 | 0e4de7df288c |
| gen_customer_sanitized_006 | customer_sanitized | dxf | ok | 4 | 5 | 0 | 521c164e721e |
| gen_customer_sanitized_007 | customer_sanitized | dxf | ok | 4 | 5 | 0 | f04e4e5b4d31 |
| gen_customer_sanitized_008 | customer_sanitized | dxf | ok | 4 | 5 | 0 | a6608228e57f |
| gen_hatch_dimension_001 | hatch_dimension_centered | dxf | ok | 3 | 5 | 0 | 061516248871 |
| gen_hatch_dimension_002 | hatch_dimension_centered | dxf | ok | 3 | 5 | 0 | 352c1a99e6a0 |
| gen_hatch_dimension_003 | hatch_dimension_centered | dxf | ok | 3 | 5 | 0 | 3e133bfe0ff6 |
| gen_hatch_dimension_004 | hatch_dimension_centered | dxf | ok | 3 | 5 | 0 | 67b2011f666d |
| gen_hatch_dimension_005 | hatch_dimension_centered | dxf | ok | 3 | 5 | 0 | 5b91e909e6c1 |
| gen_hatch_dimension_006 | hatch_dimension_centered | dxf | ok | 3 | 5 | 0 | e07321313f4e |
| gen_hatch_dimension_007 | hatch_dimension_centered | dxf | ok | 3 | 5 | 0 | ea599bb04c76 |
| gen_hatch_dimension_008 | hatch_dimension_centered | dxf | ok | 3 | 5 | 0 | faf0040fb294 |
| gen_hatch_dimension_009 | hatch_dimension_centered | dxf | ok | 3 | 5 | 0 | 48f0e4c4bb6c |
| gen_hatch_dimension_010 | hatch_dimension_centered | dxf | ok | 3 | 5 | 0 | 3b85387a6129 |
| gen_large_001 | large_synthetic | dxf | ok | 25 | 5 | 0 | 78e934548ab2 |
| gen_large_002 | large_synthetic | dxf | ok | 26 | 5 | 0 | 4a3f51cdc01c |
| gen_large_003 | large_synthetic | dxf | ok | 27 | 5 | 0 | 92d516f9c2c7 |
| gen_large_004 | large_synthetic | dxf | ok | 28 | 5 | 0 | 480569e919d9 |
| gen_large_005 | large_synthetic | dxf | ok | 29 | 5 | 0 | 95f3b8ec30ee |
| gen_large_006 | large_synthetic | dxf | ok | 30 | 5 | 0 | 0782fa37a2fd |
| gen_large_007 | large_synthetic | dxf | ok | 31 | 5 | 0 | b4fa1d079604 |
| gen_large_008 | large_synthetic | dxf | ok | 32 | 5 | 0 | 45ea04c91916 |
| gen_large_009 | large_synthetic | dxf | ok | 33 | 5 | 0 | d6efcc3645a3 |
| gen_large_010 | large_synthetic | dxf | ok | 34 | 5 | 0 | 6cd6acd54751 |
| gen_large_011 | large_synthetic | dxf | ok | 35 | 5 | 0 | c566786d39f3 |
| gen_large_012 | large_synthetic | dxf | ok | 36 | 5 | 0 | 172c9474a5c2 |
| gen_large_013 | large_synthetic | dxf | ok | 37 | 5 | 0 | 779909603e56 |
| gen_large_014 | large_synthetic | dxf | ok | 38 | 5 | 0 | c9589bce74b0 |
| gen_large_015 | large_synthetic | dxf | ok | 39 | 5 | 0 | 63c80262893b |
| gen_simple_001 | simple | dxf | ok | 5 | 5 | 0 | 58bdd15fa0db |
| gen_simple_002 | simple | dxf | ok | 6 | 5 | 0 | b290c1498c3d |
| gen_simple_003 | simple | dxf | ok | 7 | 5 | 0 | 3e29fa7acb81 |
| gen_simple_004 | simple | dxf | ok | 8 | 5 | 0 | ecfada448d22 |
| gen_simple_005 | simple | dxf | ok | 9 | 5 | 0 | 2993c1a814b5 |
| gen_simple_006 | simple | dxf | ok | 10 | 5 | 0 | a8e8ecc9fd13 |
| gen_simple_007 | simple | dxf | ok | 11 | 5 | 0 | 706a009e64fe |
| gen_simple_008 | simple | dxf | ok | 12 | 5 | 0 | 499cfbe09e7a |
| gen_simple_009 | simple | dxf | ok | 13 | 5 | 0 | b7b2a7318c5f |
| gen_simple_010 | simple | dxf | ok | 14 | 5 | 0 | 14664916dca9 |
| gen_simple_011 | simple | dxf | ok | 15 | 5 | 0 | cd43b9f80551 |
| gen_simple_012 | simple | dxf | ok | 16 | 5 | 0 | 49d1d8ffbfd2 |
| gen_simple_013 | simple | dxf | ok | 17 | 5 | 0 | f961c97a89f7 |
| gen_simple_014 | simple | dxf | ok | 18 | 5 | 0 | 48c6c2e6232e |
| gen_simple_015 | simple | dxf | ok | 19 | 5 | 0 | e6ed395e740a |
| gen_simple_016 | simple | dxf | ok | 20 | 5 | 0 | 6f0d851dfdf1 |
| gen_simple_017 | simple | dxf | ok | 21 | 5 | 0 | 157fc687caca |
| gen_simple_018 | simple | dxf | ok | 22 | 5 | 0 | 60c2209168c9 |
| gen_simple_019 | simple | dxf | ok | 23 | 5 | 0 | 643b9ab0e915 |
| gen_simple_020 | simple | dxf | ok | 24 | 5 | 0 | 1c4ad23ee2a1 |
| gen_text_001 | text_centered | dxf | ok | 2 | 5 | 0 | 8230eab93bb8 |
| gen_text_002 | text_centered | dxf | ok | 2 | 5 | 0 | b573319c03c5 |
| gen_text_003 | text_centered | dxf | ok | 2 | 5 | 0 | 98f89b462343 |
| gen_text_004 | text_centered | dxf | ok | 2 | 5 | 0 | a654b1607e2e |
| gen_text_005 | text_centered | dxf | ok | 2 | 5 | 0 | 05f638317521 |
| gen_text_006 | text_centered | dxf | ok | 2 | 5 | 0 | 3eef58d61c44 |
| gen_text_007 | text_centered | dxf | ok | 2 | 5 | 0 | 41c860948a34 |
| gen_text_008 | text_centered | dxf | ok | 2 | 5 | 0 | 6a52a8744836 |
| gen_text_009 | text_centered | dxf | ok | 2 | 5 | 0 | 43fee076c8a3 |
| gen_text_010 | text_centered | dxf | ok | 2 | 5 | 0 | 430699d8733a |
| gen_text_011 | text_centered | dxf | ok | 2 | 5 | 0 | b86e5d5fac45 |
| gen_text_012 | text_centered | dxf | ok | 2 | 5 | 0 | 5e330ecded23 |
| gen_text_013 | text_centered | dxf | ok | 2 | 5 | 0 | bb831ed4774e |
| gen_text_014 | text_centered | dxf | ok | 2 | 5 | 0 | fda7c0cbe1f4 |
| gen_text_015 | text_centered | dxf | ok | 2 | 5 | 0 | a2b9d1ee0f47 |
| gen_unsupported_001 | unsupported_malformed | dxf | partial | 0 | 5 | 3 | 4f53cda18c2b |
| gen_unsupported_002 | unsupported_malformed | dxf | partial | 0 | 5 | 3 | 4f53cda18c2b |
| gen_unsupported_003 | unsupported_malformed | dxf | partial | 0 | 5 | 3 | 4f53cda18c2b |
| gen_unsupported_004 | unsupported_malformed | dxf | partial | 0 | 5 | 3 | 4f53cda18c2b |
| gen_unsupported_005 | unsupported_malformed | dxf | partial | 0 | 5 | 3 | 4f53cda18c2b |
| gen_unsupported_006 | unsupported_malformed | dxf | partial | 0 | 5 | 3 | 4f53cda18c2b |
| gen_unsupported_007 | unsupported_malformed | dxf | partial | 0 | 5 | 3 | 4f53cda18c2b |
| gen_unsupported_008 | unsupported_malformed | dxf | partial | 0 | 5 | 3 | 4f53cda18c2b |
| gen_unsupported_009 | unsupported_malformed | dxf | partial | 0 | 5 | 3 | 4f53cda18c2b |
| gen_unsupported_010 | unsupported_malformed | dxf | partial | 0 | 5 | 3 | 4f53cda18c2b |
| hatch_centered | hatch_centered | dxf | ok | 2 | 2 | 0 | 11bd3336b473 |
| large_grid | large | dxf | ok | 120 | 2 | 0 | 8b48a0b237d0 |
| simple_base | simple | dxf | ok | 4 | 2 | 0 | 523cbc02185b |
| simple_modified | simple | dxf | ok | 5 | 2 | 0 | e0ed7dcc8d42 |
| text_centered | text_centered | dxf | ok | 3 | 2 | 0 | 1e0104abc432 |
| unsupported_objects | unsupported_objects | dxf | partial | 0 | 2 | 3 | 4f53cda18c2b |

## Diff Snapshots

| Case | Total Changes | Added / Removed / Modified / Unchanged | Fingerprint |
| --- | ---: | --- | --- |
| simple_base_vs_modified | 3 | +1 / -0 / ~2 / =2 | 0c08f73be8c0 |

## Normalization Policy

```json
{
  "angle_quantum_deg": 0.001,
  "bbox_quantum_mm": 0.01,
  "coordinate_quantum_mm": 0.01,
  "flatten_curves": true,
  "flatten_tolerance_mm": 0.1,
  "max_flatten_segments": 128,
  "near_zero_area_mm2": 0.0001,
  "near_zero_length_mm": 0.01,
  "normalize_polyline_direction": true,
  "normalize_polyline_vertices": true,
  "normalize_text": true,
  "remove_near_zero_geometry": false,
  "resolve_bylayer_byblock": true,
  "scale_quantum": 1e-09,
  "strip_mtext_formatting": true,
  "update_hashes": true,
  "vertex_merge_tolerance_mm": 0.01
}
```

## Validation Findings

- No manifest or golden snapshot mismatches detected.

## Maintenance

Run `python scripts/cad_format_regression.py --check --report build/reports/cad-format-regression-report.md` before merging importer, normalizer, writer, or compare-engine changes.
When an intentional behavior change is reviewed, run `python scripts/cad_format_regression.py --update-golden --report docs/CAD_FORMAT_REGRESSION_REPORT.md` and review the JSON and report diff together.
