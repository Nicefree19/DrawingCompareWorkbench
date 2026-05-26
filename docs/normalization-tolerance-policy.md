# Normalization and Tolerance Policy

Last updated: 2026-05-22 KST

## Purpose

This policy defines how DXF and DWG importers normalize units, coordinates,
bounding boxes, tolerances, and hashes before producing `CanonicalDrawing`.

The goal is simple: once imported, the comparison engine operates on canonical
geometry only and does not need to know the original file format.

## Canonical Unit Policy

| Item | Policy |
| --- | --- |
| Canonical unit | Millimeter (`mm`). |
| Source units | Importer reads CAD unit metadata when available. |
| Unknown units | Use project/importer default only with `ImportWarning(code="UNIT_INFERRED")`. |
| User override | Allowed, but must be recorded with `unit_source="user_override"`. |
| Scale storage | `units.scale_to_mm` records multiplier from source unit to mm. |

Default unit conversion:

| Source unit | `scale_to_mm` |
| --- | ---: |
| mm | 1.0 |
| cm | 10.0 |
| m | 1000.0 |
| inch | 25.4 |
| foot | 304.8 |
| unitless/unknown | project default; warning required |

Importer rule:

- All coordinates, lengths, radii, bbox values, and lineweights represented as
  dimensions must be emitted in canonical mm unless the field explicitly states
  otherwise.
- Angular fields are degrees.

## Coordinate System Policy

| Area | Policy |
| --- | --- |
| Canonical space | WCS. |
| Axis order | `x`, `y`, `z`. |
| Origin | Preserve CAD world origin. Do not re-center drawings during import. |
| OCS/UCS/DCS | Convert entity geometry to WCS before writing canonical geometry. |
| Z values | Preserve z when available. 2D comparison may flatten later, but importer must not discard z silently. |
| Layouts | Modelspace and paperspace are both allowed. Layout names must namespace hashes when needed. |
| Blocks | Block definition coordinates remain block-local; block references carry a 4x4 transform to WCS. Expanded block children must be transformed to WCS. |

Z policy:

- `z_policy="preserve"` when importer preserves z values for all entities.
- `z_policy="flatten_to_xy"` only when the importer intentionally sets z to 0.
- `z_policy="mixed"` when some entities are true 3D but unsupported or partly approximated.

If non-default OCS cannot be fully transformed, importer must emit a warning:

```json
{
  "code": "OCS_APPROXIMATED",
  "severity": "warning",
  "message": "Entity uses non-default OCS; canonical geometry is best effort.",
  "raw_type": "ARC",
  "source_handle": "2A7"
}
```

## Numeric Quantization

Quantization is for canonical serialization and hashing. It is not the same as
comparison tolerance.

| Field family | Default quantum | Notes |
| --- | ---: | --- |
| Coordinates | 0.01 mm | Applies to points, centers, vertices, defpoints, insert points. |
| Bounding boxes | 0.01 mm | Computed after geometry normalization. |
| Text anchors | 0.1 mm | Used only for semantic near-match keys, not raw geometry storage. |
| Length/radius/measurement | 0.01 mm | Dimension measurement may additionally use semantic tolerance. |
| Angles | 0.001 deg | Hash quantization. Compare threshold can be larger. |
| Scale factors | 0.000001 | Preserve small scale changes. |
| Bulge values | 0.000001 | Preserve polyline arc semantics. |

Implementation requirements:

- Quantization must be deterministic and use decimal rounding rules, not binary
  float string formatting.
- Negative zero must serialize as `0`.
- NaN and Infinity are invalid in canonical output.
- Importers should preserve enough precision before quantization to avoid
  accumulating transformation error.

## Comparison Tolerance Defaults

These defaults belong in `CanonicalDrawing.tolerances` so both importer and
comparison engine know the assumptions used to generate hashes.

| Tolerance | Default | Purpose |
| --- | ---: | --- |
| `position_match_tolerance_mm` | 1.0 mm | General near-match threshold for moved geometry. |
| `structural_position_tolerance_mm` | 0.1 mm | Stricter threshold for structural layers. |
| `text_near_match_radius_mm` | 50.0 mm | Match text/dimension/leader content when anchor also moved. |
| `dimension_abs_threshold_mm` | 1.0 mm | Dimension measurement change threshold. |
| `dimension_rel_threshold` | 0.001 | 0.1 percent relative dimension threshold. |
| `rotation_threshold_deg` | 0.1 deg | Rotation change threshold. |
| `scale_threshold_ratio` | 0.01 | 1 percent scale change threshold. |
| `bbox_overlap_epsilon_mm` | 0.01 mm | Prevents edge-touch precision artifacts. |

Layer-specific overrides:

- Structural layers may use `structural_position_tolerance_mm`.
- Title block/revision/frame layers may use looser thresholds or be filtered by
  caller configuration.
- Layer override policy must be outside the importer. Importers emit facts;
  comparison configuration decides which layers matter.

## Bounding Box Policy

Every canonical entity must have a `bbox` object.

| `bbox.quality` | Meaning | Compare behavior |
| --- | --- | --- |
| `exact` | BBox is exact for the normalized geometry. | Full spatial matching allowed. |
| `estimated` | BBox is approximate, usually due to text/font/dimension layout. | Spatial matching allowed with warning-aware confidence. |
| `control_points` | BBox covers control/fit points but not guaranteed curve extents. | Matching allowed, but reports may mark evidence as approximate. |
| `missing` | BBox could not be computed. | Entity can be compared by hash/semantic data only; UI crop must not assume location. |

Entity-specific bbox rules:

- Line: min/max of endpoints.
- Circle: center plus/minus radius.
- Arc: exact arc extents when practical; otherwise control-points quality.
- Polyline: vertex and bulge-aware extents when practical; otherwise vertices.
- Text/MText: exact when text engine metrics are available; otherwise estimated.
- BlockReference: transformed block bbox if block definition is available.
- Hatch: boundary extents.
- Dimension: defpoints plus text midpoint when available; estimated if generated
  dimension geometry is not resolved.

Degenerate bboxes:

- Zero-width or zero-height bboxes are valid for point/line-like entities.
- Rendering/crop layers may expand degenerate bboxes for display, but must not
  rewrite canonical bbox values.

## Geometry Hash Policy

Hashing provides deterministic fast-path matching. It is not the only matching
mechanism; the comparison engine may still use spatial near-match logic.

### Hash Families

| Hash | Includes | Excludes |
| --- | --- | --- |
| `geometry_hash` | Entity type and normalized geometry. | Source handle, file format, layer, style, text content where text is semantic. |
| `semantic_hash` | Text content, attribute values, dimension measurement/text override, hatch pattern identity when treated as semantic. | Source handle, style, layer. |
| `style_hash` | Color, lineweight, linetype, text style, dimension style. | Geometry, source handle. |
| `source_fingerprint` | Raw handle/path/type/layout for diagnostics. | Not used for primary comparison. |

Recommended hash string format:

```text
geom:v1:sha256:<64 lowercase hex chars>
sem:v1:sha256:<64 lowercase hex chars>
style:v1:sha256:<64 lowercase hex chars>
src:v1:sha256:<64 lowercase hex chars>
```

### Canonical Serialization

Before hashing:

1. Build a JSON object containing only fields for that hash family.
2. Convert all numeric fields to canonical unit and quantize.
3. Normalize strings with Unicode NFC.
4. Trim text for semantic hash, but preserve internal line breaks as `\n`.
5. Sort object keys lexicographically.
6. Omit null fields unless null carries semantic meaning.
7. Serialize as compact UTF-8 JSON with no extra spaces.
8. Hash with SHA-256.

### Entity-Specific Hash Notes

| Entity | Geometry hash notes | Semantic hash notes |
| --- | --- | --- |
| Line | Endpoint order sorted so A-B equals B-A. | Null. |
| Circle | Center and radius. | Null. |
| Arc | Center, radius, start/end angle, normal, sweep direction. | Null. |
| Polyline | Ordered vertices, closed flag, bulges, widths. Closed paths start at deterministic minimum vertex. | Null unless future metadata adds semantic payload. |
| Text | Insert, height, rotation, alignment. | `canonical_text`. |
| MText | Insert, height, width, rotation, attachment. | `canonical_text`. |
| BlockReference | Block id/name, insert, scale, rotation, transform matrix. | Attribute tag/value pairs and optional block text fingerprint. |
| Hatch | Boundary topology and fill geometry. | Pattern name/scale/angle may be included in semantic hash if fill style changes matter. |
| Dimension | Defpoints and text midpoint. | Measurement, dimension type, text override, canonical text. |

## Import Warning Policy

Importers must emit warnings for recoverable fidelity loss.

Recommended warning codes:

| Code | When to emit |
| --- | --- |
| `UNIT_INFERRED` | Source unit was absent or ambiguous. |
| `UNIT_OVERRIDDEN` | User/project override changed source unit. |
| `UNSUPPORTED_ENTITY` | Raw entity was skipped. |
| `ENTITY_APPROXIMATED` | Entity was converted to simplified canonical geometry. |
| `BBOX_ESTIMATED` | BBox is estimated. |
| `BBOX_MISSING` | BBox could not be computed. |
| `OCS_APPROXIMATED` | Non-default OCS was not exactly reconstructed. |
| `POLYLINE_CURVE_FLATTENED` | Curved polyline segments were flattened. |
| `HATCH_EDGE_APPROXIMATED` | Hatch boundary curve edges were simplified. |
| `TEXT_FORMAT_LOSS` | Rich text formatting was stripped. |
| `DIMENSION_GEOMETRY_NOT_RESOLVED` | Generated dimension graphics were not resolved. |
| `BLOCK_EXPANSION_LIMIT` | Block recursion or entity cap prevented full expansion. |
| `XREF_NOT_RESOLVED` | External reference was not loaded. |
| `PROXY_OBJECT_SKIPPED` | Proxy/custom object was skipped. |

Severity policy:

- `info`: fidelity loss does not affect comparison.
- `warning`: comparison is valid but incomplete or approximate.
- `error`: import is partial or failed for affected entity/group.

## Unsupported Entity Report Policy

Unsupported entities are aggregated in `import_report.unsupported_entities`.

Each report must include:

- `raw_type`
- `count`
- `policy`
- `impact`
- up to 5 `examples`

Impact levels:

| Impact | Meaning |
| --- | --- |
| `none` | Entity had no compare-relevant content. |
| `visual_only` | Visual preview may differ, but entity comparison is not materially affected. |
| `geometry_missing` | Geometry may be absent from comparison. |
| `comparison_incomplete` | Diff result can miss changes. |
| `fatal` | Import should fail or comparison should be blocked. |

Example:

```json
{
  "raw_type": "3DSOLID",
  "count": 12,
  "policy": "skipped",
  "impact": "comparison_incomplete",
  "examples": [
    {
      "handle": "4A2",
      "layer_name": "STEEL-BEAM",
      "layout_name": "Model",
      "reason": "3D ACIS body is outside canonical 2D entity scope"
    }
  ]
}
```

## DXF/DWG Importer Equivalence Rules

DXF and DWG importers are equivalent only if they satisfy all rules below:

1. The same source drawing exported as DXF and read as DWG produces identical
   entity `type`, units, WCS coordinates, bboxes, and hashes for supported
   entities after quantization.
2. Raw handles may differ and must not affect `geometry_hash` or
   `semantic_hash`.
3. Layer names and block names must be normalized identically.
4. Text decoding must produce the same `canonical_text` for the same visible
   string.
5. Unsupported entities must be reported with the same `impact` semantics even
   if the raw CAD type names differ between import backends.
6. Importer-specific approximation must be reflected in warning codes and bbox
   quality, not hidden in metadata.

## Comparison Engine Contract

The comparison engine may assume:

- Input conforms to `canonical-drawing.schema.json`.
- Coordinates are WCS mm.
- Entity bbox is present.
- Hashes are deterministic and format-neutral.
- Import warnings describe known fidelity loss.

The comparison engine must not assume:

- Raw CAD handles are stable across revisions.
- DXF and DWG entity class names are available.
- BBox is always exact.
- Block references are always expanded.
- Text bbox is exact.
