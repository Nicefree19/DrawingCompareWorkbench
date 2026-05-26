# Canonical Entity Spec

Last updated: 2026-05-22 KST

## Purpose

`CanonicalDrawing` is the internal, format-neutral model used by DXF and DWG
importers. The comparison engine must consume this model only. It must not need
to know whether the original file was DXF or DWG.

This spec defines the semantic contract. The machine-readable contract is
`canonical-drawing.schema.json`.

## Design Goals

- DXF and DWG importers emit the same `CanonicalDrawing` shape.
- Comparison is based on canonical entities, layers, blocks, coordinates,
  bounding boxes, and hashes, not raw CAD handles.
- Source format details remain available for diagnostics, but are not required
  for matching or diff classification.
- Unsupported or approximated data is explicitly reported through
  `ImportWarning` and `UnsupportedEntityReport`.

## Top-Level Model

### CanonicalDrawing

Required fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | string | Must be `canonical-drawing/v1`. |
| `drawing` | object | File metadata, importer metadata, source format/version. |
| `units` | object | Source unit and canonical conversion policy. Canonical unit is `mm`. |
| `coordinate_system` | object | Canonical coordinate system. Must be WCS. |
| `tolerances` | object | Quantization and comparison defaults used by importer and compare engine. |
| `extents` | BBox | Optional drawing-level canonical extents. |
| `layers` | CanonicalLayer[] | Layer table normalized across formats. |
| `blocks` | CanonicalBlock[] | Block definitions normalized across formats. |
| `entities` | CanonicalEntity[] | All comparable modelspace/paperspace/block entities. |
| `import_report` | object | Import status, warnings, unsupported entity report, counts. |
| `metadata` | object | Extension field for non-critical data. |

Importer rule:

- A successful import returns `status="ok"` or `status="partial"`.
- `status="partial"` is acceptable only when unsupported or approximated
  entities are reported.
- `status="failed"` means the comparison engine must not compare the drawing.

### CanonicalLayer

Required fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable canonical id, usually `layer:<normalized_name_hash>` or deterministic importer id. |
| `name` | Original layer name. |
| `normalized_name` | Trimmed, Unicode-normalized, case-normalized layer key. |
| `visible` | Effective layer visibility. |
| `locked` | Effective layer lock state. |

Optional fields include `color`, `linetype`, `lineweight`, `frozen`, `plot`,
`source_handle`, and `metadata`.

Policy:

- Layer id is the reference used by entities.
- Layer display name changes are compared separately from geometry.
- Layer visibility/plot state is imported for filtering and diagnostics, but
  geometry comparison can still use hidden layers if the caller requests it.

### CanonicalBlock

Required fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable canonical block id. |
| `name` | Original block name. |
| `normalized_name` | Normalized block name. |
| `origin` | Block base point in canonical mm coordinates. |
| `entity_ids` | Canonical entity ids that belong to this block definition. |

Optional fields include `bbox`, `is_external_reference`, `source_path`, and
`metadata`.

Policy:

- A block definition is stored once in `blocks`.
- `BlockReference` entities refer to a block by `block_id`.
- Importers may also emit expanded child entities when the compare pipeline is
  configured to compare block-internal geometry. Expanded children must carry
  `space="block"` or source path metadata so the UI can explain provenance.
- XREFs are represented as blocks only when their content was actually resolved
  by the importer. Otherwise they must be reported as unsupported or partial.

### CanonicalEntity

Required fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable id inside one `CanonicalDrawing`. Must not rely solely on raw handle. |
| `type` | One of `line`, `arc`, `circle`, `polyline`, `text`, `mtext`, `block_reference`, `hatch`, `dimension`. |
| `source` | Raw CAD provenance: source format, raw type, handle, layout/block path. |
| `layer_id` | Reference to `CanonicalLayer.id`. |
| `space` | `model`, `paper`, or `block`. |
| `geometry` | Type-specific model described below. |
| `bbox` | Axis-aligned WCS bbox in mm. |
| `hashes` | Geometry/semantic/style/source hashes. |

Optional fields include `block_id`, `layout_name`, `style`, `visible`, and
`metadata`.

The comparison engine may use:

- `type`
- `geometry`
- `bbox`
- `layer_id`
- `hashes.geometry_hash`
- `hashes.semantic_hash`
- `hashes.style_hash`

The comparison engine must not require:

- `source.format`
- `source.handle`
- raw DXF/DWG entity classes
- importer-specific metadata

## Entity Models

### Line

`type="line"`

Geometry:

| Field | Required | Meaning |
| --- | --- | --- |
| `start` | yes | WCS start point in mm. |
| `end` | yes | WCS end point in mm. |

Normalization:

- Direction is hash-normalized. A line from A to B and B to A produce the same
  `geometry_hash`.
- `bbox` is the min/max of start/end.
- Zero-length lines are allowed only with an `ImportWarning`; otherwise reject.

### Circle

`type="circle"`

Geometry:

| Field | Required | Meaning |
| --- | --- | --- |
| `center` | yes | WCS center in mm. |
| `radius` | yes | Radius in mm. |

Normalization:

- Radius must be positive after unit conversion.
- OCS center from DXF/DWG must be transformed to WCS before serialization.
- `bbox` is `center +/- radius`.

### Arc

`type="arc"`

Geometry:

| Field | Required | Meaning |
| --- | --- | --- |
| `center` | yes | WCS center in mm. |
| `radius` | yes | Radius in mm. |
| `start_angle_deg` | yes | Canonical angle in degrees. |
| `end_angle_deg` | yes | Canonical angle in degrees. |
| `normal` | yes | Normal vector after OCS/WCS normalization. |
| `sweep_direction` | no | `ccw` by default. |

Normalization:

- Angles are normalized into `[0, 360)`.
- Default sweep is counter-clockwise in WCS XY.
- If a DWG/DXF reader cannot reconstruct exact WCS angles for a non-default
  OCS arc, it must still include the normal and emit an `ImportWarning` with
  `code="ARC_ANGLE_APPROXIMATED"`.

### Polyline

`type="polyline"`

Geometry:

| Field | Required | Meaning |
| --- | --- | --- |
| `vertices` | yes | Ordered vertex list. Each vertex has `point`, optional `bulge`, optional widths. |
| `closed` | yes | Whether the final segment closes back to the first vertex. |
| `polyline_kind` | no | `lwpolyline`, `2d_polyline`, or `3d_polyline`. |

Normalization:

- Vertex order is preserved for open polylines.
- Closed polylines are rotated to a deterministic starting vertex for hashing.
- Reversed closed polylines should produce the same topology hash only if the
  geometry is semantically identical after reversal.
- Bulge is preserved when available. If the importer flattens curved segments,
  it must emit `ImportWarning(code="POLYLINE_CURVE_FLATTENED")`.

### Text

`type="text"`

Geometry:

| Field | Required | Meaning |
| --- | --- | --- |
| `insert` | yes | WCS insertion/alignment anchor in mm. |
| `text` | yes | Raw visible text as decoded by importer. |
| `canonical_text` | yes | Text normalized for comparison. |
| `height` | yes | Text height in mm. |
| `rotation_deg` | yes | Rotation in degrees. |
| `alignment` | no | CAD alignment mode if available. |

Normalization:

- `canonical_text` trims surrounding whitespace, normalizes Unicode, and
  standardizes line endings.
- Geometry hash excludes `text` and `canonical_text`; semantic hash includes
  canonical text.
- Font metrics are not required for comparison. BBox may be `estimated`.

### MText

`type="mtext"`

Geometry:

| Field | Required | Meaning |
| --- | --- | --- |
| `insert` | yes | WCS insertion point in mm. |
| `plain_text` | yes | Text stripped of rich formatting. |
| `canonical_text` | yes | Text normalized for comparison. |
| `raw_content` | no | Raw MTEXT markup/content if available. |
| `height` | yes | Text height in mm. |
| `box_width` | no | Wrapping width in mm. |
| `rotation_deg` | yes | Rotation in degrees. |
| `attachment` | no | Attachment mode if available. |

Normalization:

- Rich formatting is not part of `semantic_hash` unless a future policy enables
  formatting comparison.
- BBox may be estimated when exact layout metrics are unavailable.

### BlockReference

`type="block_reference"`

Geometry:

| Field | Required | Meaning |
| --- | --- | --- |
| `block_id` | yes | Referenced canonical block id. |
| `block_name` | yes | Original referenced block name. |
| `insert` | yes | WCS insertion point in mm. |
| `scale` | yes | X/Y/Z scale vector. |
| `rotation_deg` | yes | Rotation about the block normal in degrees. |
| `matrix` | yes | 4x4 row-major transform matrix from block coordinates to WCS. |
| `attributes` | no | Realized attribute values with tag/text/position. |
| `expanded_entity_ids` | no | Child canonical entities emitted from this reference. |

Normalization:

- Transform matrix is authoritative for downstream expansion and bbox
  computation.
- Attribute values are included in `semantic_hash`.
- Block definition text may be represented either as expanded child entities or
  as a semantic fingerprint, but the importer must be consistent within one
  `CanonicalDrawing`.

### Hatch

`type="hatch"`

Geometry:

| Field | Required | Meaning |
| --- | --- | --- |
| `pattern_name` | yes | Pattern name or `SOLID`. |
| `solid_fill` | yes | Whether hatch is solid fill. |
| `pattern_scale` | no | Pattern scale. |
| `pattern_angle_deg` | no | Pattern angle. |
| `boundaries` | yes | Boundary loops with `outer`, `inner`, or `unknown` role. |

Normalization:

- Boundary vertices are preserved in WCS mm.
- If curved hatch edges are reduced to vertices, emit
  `ImportWarning(code="HATCH_EDGE_APPROXIMATED")`.
- Geometry hash includes pattern identity and boundary topology.

### Dimension

`type="dimension"`

Geometry:

| Field | Required | Meaning |
| --- | --- | --- |
| `dimension_type` | yes | `linear`, `aligned`, `angular`, `diameter`, `radius`, `ordinate`, `arc_length`, or `unknown`. |
| `measurement` | yes | Numeric measured value in mm/degrees as appropriate, or null if unavailable. |
| `text_override` | no | Raw dimension text override. |
| `canonical_text` | no | Normalized dimension text. |
| `defpoints` | yes | Defining points in WCS mm. |
| `text_midpoint` | no | WCS text midpoint when available. |

Normalization:

- Measurement and text override belong to `semantic_hash`.
- Defpoints and text midpoint belong to geometry hash.
- Generated dimension graphics are not required as child geometry unless the
  importer explicitly explodes dimensions.

## Hash Contract

Every entity has:

| Hash | Purpose |
| --- | --- |
| `geometry_hash` | Deterministic hash of canonical geometry only. Excludes source format, raw handle, layer, and style. |
| `semantic_hash` | Text/content/measurement/value hash. Null if entity has no semantic payload. |
| `style_hash` | Color, linetype, lineweight, text style, dimstyle. Null if style comparison is disabled or unavailable. |
| `source_fingerprint` | Debug/provenance hash. Must not be used as the primary comparison key. |

Hash algorithm:

1. Build a JSON object with sorted keys.
2. Quantize numeric values according to `normalization-tolerance-policy.md`.
3. Remove null/unknown fields unless the policy marks them semantically
   significant.
4. Serialize with UTF-8, no whitespace, sorted keys.
5. Hash with SHA-256.
6. Prefix with hash family, for example:
   `geom:v1:sha256:<64-hex>`.

## ImportWarning

Warnings describe recoverable import issues.

Required fields:

| Field | Meaning |
| --- | --- |
| `code` | Stable machine code such as `UNSUPPORTED_ENTITY`, `BBOX_ESTIMATED`, `OCS_APPROXIMATED`. |
| `severity` | `info`, `warning`, or `error`. |
| `message` | Human-readable explanation. |

Optional fields: `entity_id`, `source_handle`, `raw_type`, `details`.

Warnings must be emitted for:

- Unsupported skipped entity types.
- Approximation of curved, hatch, OCS, text, dimension, or proxy data.
- Missing/estimated bbox.
- Unit inference or user override.
- Entity cap/truncation.

## UnsupportedEntityReport

Unsupported entities are aggregated by raw CAD type.

Required fields:

| Field | Meaning |
| --- | --- |
| `raw_type` | Original CAD entity type, e.g. `3DSOLID`, `REGION`, `ACAD_PROXY_ENTITY`. |
| `count` | Number of encountered entities. |
| `policy` | `skipped`, `approximated`, `exploded`, or `rejected`. |

Optional fields:

| Field | Meaning |
| --- | --- |
| `impact` | `none`, `visual_only`, `geometry_missing`, `comparison_incomplete`, or `fatal`. |
| `examples` | Small sample of handle/layer/layout/reason records. |

## Importer Compliance Checklist

A DXF or DWG importer is compliant when:

1. It emits valid `canonical-drawing.schema.json`.
2. It uses canonical unit `mm`.
3. It transforms entity coordinates to WCS.
4. It assigns every entity a canonical layer id.
5. It emits bbox and bbox quality for every entity.
6. It emits deterministic hashes independent of raw handles.
7. It reports unsupported and approximated entities.
8. It does not expose raw DXF/DWG classes to the comparison engine.
