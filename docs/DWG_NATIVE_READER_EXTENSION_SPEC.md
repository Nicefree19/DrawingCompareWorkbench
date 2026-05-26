# DWG Native Reader Extension Spec

## Purpose

Extend the ODA-free DWG import path from the current `AC1015` MVP to the real
project samples detected as `AC1024` and `AC1032`.

The target is not a general CAD converter.  The target is enough read-only DWG
decoding to produce `CanonicalDrawing` for drawing comparison.

## Current State

- `DwgVersionDetector` detects `AC1009`, `AC1012`, `AC1014`, `AC1015`,
  `AC1018`, `AC1021`, `AC1024`, `AC1027`, and `AC1032`.
- `DwgNativeAc1015Adapter` only supports `AC1015`.
- `DwgSectionReader` decodes the AC1015 section locator and object map used by
  the native MVP.
- `DwgVersionedSectionMapReader` has AC1024 and AC1032 diagnostic shells. It
  does not decode the binary section map yet, but it moves those samples from
  the broad `section_locator` block to the more precise `section_map_decoder`
  block.
- `DWG_CLEANROOM_FORMAT_CONTRACT.md` is the required approval gate for
  AC1024/AC1032 section-map decoding. Until that contract records approved
  public-reference evidence, the runtime diagnostic detail remains
  `approved_format_contract_required`.
- `DwgObjectDecoder` maps the MVP object payload into `DwgAdapterDrawing`.
- `DwgImporterAdapter.supports_version()` lets a future approved adapter claim
  support for planned versions without changing the detector policy.

## Local Diagnostics

Run:

```powershell
python scripts\dwg_native_diagnostics.py
```

Current real-world sample result:

| Version | Count | Status | Blocking Stage |
| --- | ---: | --- | --- |
| `AC1024` | 1 | blocked version | `section_map_decoder` |
| `AC1032` | 3 | blocked version | `section_map_decoder` |

Interpretation: the next implementation blocker is not entity mapping yet.  The
real-world AC1024/AC1032 paths now reach registered section-map reader shells and
need the actual versioned section-map decoder before object-map work can begin.

## Constraints

- Do not use ODA File Converter, ODA SDK, ODA samples, or ODA binary reverse
  engineering output.
- Do not embed LibreDWG or GPL/AGPL code.
- Approved runtime dependencies remain MIT/BSD/Apache-2.0-style or internal
  first-party code.
- Public specifications may be used as technical references, but copied code or
  implementation details from incompatible licensed code must not enter this
  repository.
- Unsupported objects must produce warnings/reports, not whole-file crashes.

## Implementation Order

1. `AC1024` section diagnostics
   - Status: shell complete; binary decoder pending.
   - Prerequisite: `DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1` must be approved for
     AC1024 implementation use.
   - Decode enough header metadata to enumerate logical sections safely.
   - Produce stable diagnostics: section count, known/unknown sections, and the
     next blocking stage.
   - Acceptance: the `AC1024` local sample advances from
     `section_map_decoder` to `object_map` or fails with a more specific
     section metadata/error stage.

2. `AC1032` section diagnostics
   - Status: shell complete; binary decoder pending.
   - Prerequisite: `DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1` must be approved for
     AC1032 implementation use.
   - Keep compression/encryption/unknown layout failures explicit.
   - Acceptance: all three `AC1032` local samples advance from
     `section_map_decoder` to `object_map` or fail with a more specific section
     metadata/error stage.

3. Object directory / object map reader
   - Read handle/object location metadata without decoding entity payloads.
   - Enforce hard limits for entry count, offset range, and elapsed time.
   - Acceptance: diagnostics include object count estimates and no out-of-range
     offsets.

4. Table readers
   - Decode layer table names and basic attributes.
   - Decode block table names and model-space/paper-space ownership metadata.
   - Acceptance: `DwgAdapterDrawing.layers` and block shells are populated.

5. Basic 2D entity decoders
   - First pass: `LINE`, `CIRCLE`, `ARC`, `LWPOLYLINE`, `TEXT`, `MTEXT`,
     `INSERT`.
   - Second pass: `HATCH`, `DIMENSION`, `ELLIPSE`, `SPLINE` as approximated or
     partial entities.
   - Acceptance: real samples import as `partial` or `ok` with entity counts,
     layer counts, bbox, and warning counts.

6. Golden transition
   - Update `tests/data/comparison/real_world/golden-results.json`.
   - Move samples from expected `failed/DWG_UNSUPPORTED_VERSION` to `partial`
     or `ok`.
   - Preserve current descriptor-cache deltas as historical comparison context.

## Error Codes

Use existing DWG import codes where possible:

- `DWG_UNSUPPORTED_VERSION`: version known but no approved reader is available.
- `DWG_CORRUPTED`: malformed header/section metadata.
- `DWG_ENCRYPTED`: encrypted DWG detected.
- `DWG_ADAPTER_FAILED`: approved adapter or native reader failed after version
  acceptance.
- `DWG_NO_READABLE_ENTITIES`: section/table parsing succeeded but no comparable
  entities were produced.
- `DWG_ENTITY_LIMIT_EXCEEDED`, `DWG_IMPORT_TIMEOUT`, `DWG_IMPORT_CANCELLED`:
  stability limits.

Diagnostics should also include `blocking_stage`:

- `version`
- `section_locator`
- `section_map_decoder`
- `object_map`
- `tables`
- `entity_decoder`
- `canonical_mapping`

## Verification

Required before claiming expanded DWG support:

```powershell
python scripts\cad_policy_gate.py
python scripts\dwg_native_diagnostics.py
python scripts\validate_real_world_dwg_samples.py
python -m pytest tests\unit\services\comparison\test_dwg_importer.py tests\unit\services\comparison\test_dwg_native_reader.py tests\unit\services\comparison\test_dwg_diagnostics.py -q
```

Expanded support is not complete until at least one local real-world `AC1024` or
`AC1032` file imports to `CanonicalDrawing` without ODA and returns structured
warnings instead of a generic adapter failure.
