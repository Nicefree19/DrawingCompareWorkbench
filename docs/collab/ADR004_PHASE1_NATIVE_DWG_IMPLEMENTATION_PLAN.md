# ADR-004 Phase 1 Native DWG Implementation Plan

Date: 2026-06-02

## Scope

This is a planning document for native DWG reader expansion beyond the current
AC1015 simple-2D preview. It does not implement a parser, does not add converter
automation, and does not claim AC1032 or all-version native DWG support.

Phase 1 may prepare contracts, diagnostics, tests, fixtures, and implementation
work items. Version support remains unavailable until every version-specific
gate in this document is satisfied.

## Current Truth

| Area | Current state |
| --- | --- |
| Native supported DWG code | AC1015 only |
| Detected but unsupported codes | AC1009, AC1012, AC1014, AC1018, AC1021, AC1024, AC1027, AC1032 |
| Phase 0 sampled target versions | AC1018=7, AC1021=4, AC1024=54, AC1027=3, AC1032=11 |
| `D:\04. 작성도면` full scan | 302 DWGs: AC1021=1, AC1024=236, AC1027=34, AC1032=26, corrupted=5 |
| Converted-DXF baseline-ready target | AC1032 only, `D:\도면 비교` |
| AC1024/AC1027 selected candidates | yes |
| AC1024/AC1027 converted DXF baseline | missing |

## Non-Negotiable Gates

No implementation work may advance past diagnostics until these gates are true:

1. Clean-room reference approval is recorded for the target version.
2. No ODA SDK, ODA File Converter, ODA sample code, LibreDWG, GPL, AGPL, or
   derived implementation material is used.
3. First-party clean-room notes exist and contain no copied tables, offsets,
   pseudocode, or incompatible source-derived material.
4. A version-specific converted-DXF baseline pair exists.
5. The baseline pair has a captured compare summary with completed/failed
   counts, total changes, review zones, elapsed time, and source provenance.
6. The version has a diagnostics test plan for malformed, encrypted, truncated,
   large, proxy/custom-object, and unsupported-section inputs.
7. Release wording continues to say the version is unsupported until native
   import reaches `partial` or `ok` on real samples and passes baseline metrics.

## Implementation Order

The implementation order is fixed for ADR-004:

1. AC1018 / AutoCAD 2004
2. AC1021 / AutoCAD 2007
3. AC1024 / AutoCAD 2010
4. AC1027 / AutoCAD 2013
5. AC1032 / AutoCAD 2018+

Later versions may reuse lower-version parser components, but support claims
must be made version-by-version.

## Parser Architecture

Keep `DwgImporter` as the only runtime boundary that can produce
`CanonicalDrawing` from DWG. Version-specific readers should feed the existing
adapter model instead of bypassing comparison services.

Planned layers:

| Layer | Responsibility | Claim status |
| --- | --- | --- |
| Version detector | Read the 6-byte ACAD code and fail fast for unsupported or corrupt headers | already present |
| Reader registry | Map approved versions to approved reader classes | planning only |
| Binary stream guard | Bounds, endian/bit reads, limits, timeout/cancel checks | planning only |
| Header reader | Header variables required for units, extents, layout hints, handles | planning only |
| Section map reader | Locate logical sections safely for each version family | blocked pending clean-room evidence |
| Object directory/map reader | Enumerate object handles, offsets, owners, and type metadata | blocked until section map exists |
| Table readers | Layers, blocks, linetypes/styles as comparable metadata | blocked until object map exists |
| Entity decoders | Produce basic 2D `DwgAdapterEntity` values | blocked until tables/object map exist |
| Canonical mapper | Reuse existing `DwgObjectDecoder` and `DwgImporter` diagnostics | partial AC1015 path exists |

Do not wire any target version into `SUPPORTED_CODES` until the version has
passed its version-specific gates.

## Minimum Import Target

Each target version must reach this minimum before it can be considered for
`partial` support:

1. Header and version metadata.
2. Section map or equivalent logical section locator.
3. Object directory/object map with safe offset validation.
4. Table shells for layers and blocks.
5. Model-space ownership traversal.
6. Basic 2D entity extraction into `DwgAdapterDrawing`.
7. Structured warning list for skipped objects/entities.
8. Deterministic bounding box and entity count diagnostics.

## Entity Priority

First-pass comparable entities:

- LINE
- CIRCLE
- ARC
- LWPOLYLINE
- TEXT
- MTEXT
- INSERT, including block reference shell and transform metadata

Second-pass or partial entities:

- HATCH
- DIMENSION
- ELLIPSE
- SPLINE
- LEADER
- proxy/custom object diagnostics

Unsupported entities must increase explicit counters and warning details. They
must not be silently ignored.

## Version Plans

### AC1018

Status: not implemented. Best first expansion target after AC1015 because it is
the oldest target in ADR-004 and appears in the Phase 0 corpus.

Required parser scope:

- Confirm whether AC1018 can share the AC1015 section/object reader shape or
  needs a separate section map reader.
- Implement only enough header, section map, object directory, tables, layers,
  blocks, and model-space traversal for basic 2D entities.
- Keep proxy/custom objects as structured unsupported diagnostics.

Required corpus:

- Confirmed before/after DWG pair.
- Matching user-converted DXF pair.
- Converted-DXF compare summary.
- Corrupt/truncated fixture for fail-closed behavior.

Fallback behavior:

- Before approval: `DWG_UNSUPPORTED_VERSION`.
- During diagnostics: fail with `blocking_stage=section_locator` or a more
  specific stage.
- After partial support: unsupported entities emit warnings and counters.

### AC1021

Status: not implemented. It follows AC1018 and should reuse any AC1018-safe
stream/table/object abstractions only after AC1018 has passed.

Required parser scope:

- Verify version-specific section/object map differences.
- Support the same minimum import target as AC1018.
- Preserve UTF-8/text encoding diagnostics where applicable.

Required corpus:

- Confirmed before/after DWG pair.
- Matching converted-DXF baseline.
- At least one text-heavy sample to test encoding and MTEXT behavior.

Fallback behavior:

- Before approval: `DWG_UNSUPPORTED_VERSION`.
- Partial import must remain visibly partial until entity coverage and compare
  metrics pass.

### AC1024

Status: blocked beyond diagnostics. Existing spec says AC1024 reaches
`section_map_decoder` shell, but the binary decoder is pending
`DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1` approval.

Required parser scope:

- Approved section-map decoder.
- Object directory with hard limits on entry count, offsets, and elapsed time.
- Table readers for layer/block ownership.
- Basic entity decoders and diagnostics.

Required corpus:

- Selected candidate:
  `D:\04. 작성도면\230203_P5+P6 복합동 HMB+PC보 일람표.dwg`
  and
  `D:\04. 작성도면\230203_P5+P6 복합동 HMB+PC보 일람표_Rev.01_skY.dwg`.
- User-converted DXF baseline requested in
  `docs/collab/ADR004_PHASE0B_DXF_CONVERSION_REQUEST.md`.
- Additional large AC1024 sample after first baseline is stable.

Fallback behavior:

- Before contract approval: structured diagnostic with
  `approved_format_contract_required`.
- Before converted-DXF baseline exists: no native-support claim and no default
  runtime enablement.

### AC1027

Status: not implemented. Phase 0-B full scan found 34 AC1027 files in
`D:\04. 작성도면`, and selected a stronger likely revision pair.

Required parser scope:

- Verify AC1027 section/object map differences after AC1024 implementation.
- Reuse AC1024 components only if clean-room evidence permits.
- Add table/entity compatibility tests for 2013-era DWG objects.

Required corpus:

- Selected candidate:
  `D:\04. 작성도면\231020_P5 복합동_구조평면도(10.20  발송용 for 삼우 AFC R1.0)_REV1.dwg`
  and
  `D:\04. 작성도면\231020_P5 복합동_구조평면도(10.20 발송용 for 삼우 AFC R1.0)_REV3.dwg`.
- User-converted DXF baseline requested in
  `docs/collab/ADR004_PHASE0B_DXF_CONVERSION_REQUEST.md`.
- One text/dimension-heavy AC1027 sample.

Fallback behavior:

- Before approval: `DWG_UNSUPPORTED_VERSION`.
- After diagnostics only: still unsupported unless `CanonicalDrawing` partial
  import metrics pass.

### AC1032

Status: blocked beyond diagnostics. Existing spec says AC1032 reaches
`section_map_decoder` shell. One AC1032 converted-DXF baseline is ready under
`D:\도면 비교`.

Required parser scope:

- Approved section-map decoder for AC1032.
- Compression/encryption/unknown-layout guards.
- Object directory, table, block, and entity decoding after section-map success.
- Larger stress samples after the first ready baseline.

Required corpus:

- Ready baseline:
  `D:\도면 비교\240111_P5 복합동_PSRC,HMB 상세도.dwg`
  and
  `D:\도면 비교\240111_P5 복합동_PSRC,HMB 상세도_r1.dwg`.
- Converted-DXF summary:
  `D:\도면 비교\codex_validation_20260601_1930\dxf_registered_utf8\direct_compare_summary.json`.
- Additional AC1032 pairs from `D:\04. 작성도면` after converted DXFs are supplied.

Fallback behavior:

- Continue using converted-DXF fallback when available.
- Native AC1032 must stay disabled by default until compare metrics pass against
  the converted-DXF baseline.

## Diagnostics And Error Codes

Use existing codes where possible:

| Code/stage | Meaning |
| --- | --- |
| `DWG_UNSUPPORTED_VERSION` | Known version with no approved reader |
| `DWG_CORRUPTED` | Malformed header, offsets, sections, or object metadata |
| `DWG_ENCRYPTED` | Encrypted/password-protected DWG detected |
| `DWG_ADAPTER_FAILED` | Approved reader failed after version acceptance |
| `DWG_NO_READABLE_ENTITIES` | Metadata parsed but no comparable entities produced |
| `DWG_ENTITY_LIMIT_EXCEEDED` | Entity or object count cap reached |
| `DWG_IMPORT_TIMEOUT` | Runtime budget exceeded |
| `DWG_IMPORT_CANCELLED` | User cancellation honored |

Required `blocking_stage` values:

- version
- section_locator
- section_map_decoder
- object_map
- tables
- entity_decoder
- canonical_mapping

## Baseline Metrics

Every target version must report these metrics against its converted-DXF
baseline:

| Metric | Requirement before native enablement |
| --- | --- |
| import success rate | at least one real pair imports as `partial` or `ok`; broader threshold set after corpus reaches 5+ pairs |
| entity coverage | entity-type coverage table is recorded; unsupported entity count is visible |
| unsupported entity count | no silent drops; proxy/custom objects counted |
| compare recall | measured against converted-DXF baseline, threshold defined before release claim |
| false-positive delta | measured against converted-DXF baseline, threshold defined before release claim |
| runtime | version-specific budget set from converted-DXF baseline and DWG import overhead |
| memory | peak RSS and retained-object limits defined for large samples |
| cancel responsiveness | cancel reaches idle within the existing CAD stability budget |

## Test Plan

Unit tests:

- Version detector tests for every supported and unsupported code.
- Stream/bounds tests for short, non-ASCII, encrypted, corrupted, and unknown
  headers.
- Section map reader tests for approved synthetic fixtures only.
- Object map reader tests for offset bounds, entry limits, duplicate handles,
  and malformed ownership.
- Entity decoder tests per entity type with unsupported-entity warnings.

Integration tests:

- One converted-DXF baseline pair per target version.
- Native import result compared with the converted-DXF baseline.
- Provenance assertions for original DWG and effective DXF fallback paths.
- Cancel/timeout tests for large or intentionally slow fixture paths.

Regression tests:

- `python scripts\cad_policy_gate.py`
- `python scripts\dwg_native_diagnostics.py`
- `python scripts\validate_real_world_dwg_samples.py`
- Focused pytest for importer, native reader, diagnostics, and baseline compare.

## Release Claim Gate

A release note, UI label, README, policy document, or marketing-facing summary
must not claim native support for AC1018, AC1021, AC1024, AC1027, AC1032, or
"all DWG versions" until all of these are true for each claimed version:

1. Clean-room contract approved for that version.
2. Reader enabled only through `DwgImporter` adapter boundary.
3. At least one real local pair imports as `partial` or `ok`.
4. Converted-DXF baseline comparison is captured and metrics pass.
5. Unsupported sections/entities are visible in diagnostics and artifacts.
6. Runtime, memory, cancel, and corrupted/encrypted failure behavior pass.
7. Documentation names the exact supported DWG version and exclusions.

Until then, product wording remains:

```text
AC1018+ DWG native import is not supported yet. Use user-provided converted DXF
fallback for comparison.
```

## Rollback And Fallback Policy

- Keep converted-DXF fallback available for every target version.
- If native import fails or metrics regress, route back to unsupported/fallback
  behavior and preserve original/effective source provenance.
- Do not remove `DWG_UNSUPPORTED_VERSION` behavior for a version until native
  support is stable and explicitly released for that version.
- Partial import is acceptable only when the warning/report surface makes
  skipped data visible.

## Phase 1 Work Breakdown

1. Approval packet work: fill clean-room reference evidence for AC1018 and
   AC1021 first, then AC1024, AC1027, AC1032.
2. Corpus work: collect missing converted-DXF baselines for AC1018, AC1021,
   AC1024, AC1027, and additional AC1032 pairs.
3. Diagnostics work: extend `dwg_native_diagnostics.py` reporting without
   decoding unapproved binary sections.
4. Contract work: define version reader interfaces and test fixtures without
   enabling runtime support.
5. Implementation work, after approval: add version-specific section map,
   object map, table, and entity readers in the fixed version order.
6. Release work: update policy only after version-specific metrics pass.

