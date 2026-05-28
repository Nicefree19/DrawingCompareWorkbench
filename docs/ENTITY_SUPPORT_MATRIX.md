# Entity Support Matrix

Last updated: 2026-05-22 KST

## Purpose

This matrix defines which DXF entities are supported by the in-process drawing
comparison engine after removing ODA File Converter. It covers entity extraction,
normalization, comparison, and audit behavior. It does not guarantee full CAD
visual rendering fidelity.

This document is an engineering support statement. Unsupported or limited entity
types must be surfaced in diagnostics where possible.

## 요약

- 핵심 지원 entity는 `LINE`, `CIRCLE`, `TEXT`, `ATTRIB`, `ATTDEF`이다.
- 제한지원 entity는 `ARC`, `LWPOLYLINE`, `POLYLINE`, `MTEXT`, `DIMENSION`, `INSERT`, `HATCH`, `SOLID`, `MULTILEADER`, `LEADER`, `SPLINE`, `ELLIPSE`이다.
- `3DFACE`, `3DSOLID`, `REGION`, `BODY`, `SURFACE`, `MESH`, `MLINE`, `XLINE`, `RAY`, `TRACE`, `TABLE`, `WIPEOUT`, underlay, proxy/custom object 계열은 미지원이다.
- 미지원 entity는 조용히 무시하지 않고 `unsupported_counts`로 노출해야 한다.
- 미리보기 렌더링은 성능 때문에 일부 heavy entity를 생략할 수 있으며, entity diff 결과가 비교의 기준이다.

## Support Levels

| Status | Meaning |
| --- | --- |
| 지원 | Entity is extracted, normalized, and compared as part of the core diff. |
| 제한지원 | Entity is extracted and compared, but only a simplified subset of semantics is normalized. |
| 미지원 | Entity is not normalized by the product. It is counted as unsupported when encountered. |
| 렌더링 제한 | Entity may be part of comparison data, but preview rendering may skip or simplify it for performance. |

## Supported and Limited Entity Types

The code-level source of truth for the ODA-free canonical pipeline is:

- `DxfEntityMapper.SUPPORTED_TYPES` in `src/services/comparison/dxf_importer.py`
- `DwgObjectDecoder` / `DwgNativeAc1015Adapter` for the AC1015 native MVP
- `DrawingNormalizer` in `src/services/comparison/drawing_normalizer.py`
- `GeometryDiff` in `src/services/comparison/drawing_compare_engine.py`

The legacy `DxfEntityExtractor`/`DxfComparator` path may still exist for
compatibility workflows, but it is not the canonical support source.

| Entity type | Status | Compared signals | Known limitations |
| --- | --- | --- | --- |
| `LINE` | 지원 | Start/end points, layer, cosmetic properties | Direction is normalized, so reversed endpoints are treated as identical. |
| `CIRCLE` | 지원 | Center, radius, layer, cosmetic properties | OCS center is converted to WCS when possible. |
| `ARC` | 제한지원 | Center, radius, start/end angle, extrusion key, layer, cosmetic properties | Extrusion changes are distinguished, but full WCS angle reconstruction is not guaranteed. |
| `LWPOLYLINE` | 제한지원 | Vertices, closed flag, layer, cosmetic properties | Bulge, width, curve interpolation, and all segment-level metadata are not fully compared. |
| `POLYLINE` | 제한지원 | Vertices, closed flag, layer, cosmetic properties | 3D polyline semantics, widths, mesh/polyface behavior, and bulge details are not fully compared. |
| `TEXT` | 지원 | Insert position, plain text content, layer, cosmetic properties | Text style, font substitution, height, width factor, oblique angle, and rich formatting are not primary diff keys. |
| `MTEXT` | 제한지원 | Insert position, `plain_text()`, layer, cosmetic properties | Rich formatting, columns, stacked text, embedded control codes, and visual wrapping are simplified. |
| `DIMENSION` | 제한지원 | Defpoint, measured value, text override, layer, cosmetic properties | Generated dimension geometry, arrow style, dimstyle variables, extension lines, tolerances, and associative behavior are simplified. |
| `INSERT` | 제한지원 | Block name, insert point, scale, rotation, extrusion key, optional block text fingerprint | Default block expansion depth is limited. Nested/dynamic block behavior is best effort. Geometry inside blocks is detected only when block expansion is enabled. |
| `ATTRIB` | 지원 | Tag, text/content, modelspace position derived from parent INSERT, layer, cosmetic properties | Attribute formatting and complex MText-style attribute layout are simplified. |
| `ATTDEF` | 지원 | Tag, default text/content, prompt, position, layer, cosmetic properties | Mostly relevant inside block definitions. Runtime attribute values are represented by `ATTRIB`. |
| `HATCH` | 제한지원 | Pattern name, scale, angle, path count, first boundary vertices, layer, cosmetic properties | Only simplified boundary signals are compared. Multiple paths, edge curves, associativity, islands, gradients, and full fill semantics are not fully normalized. |
| `SOLID` | 제한지원 | Corner points, layer, cosmetic properties | Covers 2D `SOLID`. Does not imply `3DSOLID` ACIS support. |
| `MULTILEADER` | 제한지원 | Text/block content fallback, anchor/base point, layer, cosmetic properties | Leader structure, block attributes, doglegs, landing geometry, style, and complex context data are simplified. |
| `LEADER` | 제한지원 | Vertex list, layer, cosmetic properties | Associated annotation and style metadata are not fully compared. |
| `SPLINE` | 제한지원 | Control points or fit points, degree, closed flag, layer, cosmetic properties | Knots, weights, rational curve details, exact curve geometry, and tolerances are simplified. |
| `ELLIPSE` | 제한지원 | Center, major axis vector, ratio, start/end parameter, layer, cosmetic properties | OCS handling and exact curve parameterization are best effort. |

## DWG Native Reader Scope

The native DWG reader is narrower than the DXF importer.

| DWG version | Entity type | Status | Notes |
| --- | --- | --- | --- |
| `AC1015` | `LAYER`, `BLOCK` table/object records | 제한지원 | Read-only MVP maps layer and block metadata into `CanonicalLayer` and `CanonicalBlock`. |
| `AC1015` | `LINE`, `CIRCLE`, `ARC`, `TEXT`, `INSERT`, `LWPOLYLINE` | 제한지원 | Supported by `DwgNativeAc1015Adapter` for simple 2D model/block-space drawings. |
| `AC1015` | `HATCH`, `DIMENSION`, `MTEXT`, `SPLINE`, `ELLIPSE` | 미지원 in native MVP | Use DXF import path or future DWG decoder expansion. |
| `AC1018`, `AC1021`, `AC1024`, `AC1027`, `AC1032` | Any native DWG entity | not implemented | Planned extension order after AC1015 validation. |

## Unsupported Entity Types

Unsupported entities must not be silently hidden in final diagnostics. The
extractor records `unsupported_counts`, and reports/UI should expose the top
unsupported types for each drawing pair.

| Entity or feature family | Status | Policy |
| --- | --- | --- |
| `3DFACE` | 미지원 | Count as unsupported. Add normalizer only after test fixtures exist. |
| `3DSOLID`, `BODY`, `REGION` | 미지원 | ACIS/B-rep data is out of scope for the current 2D drawing differ. |
| `SURFACE`, `PLANESURFACE`, `EXTRUDEDSURFACE`, `LOFTEDSURFACE`, `REVOLVEDSURFACE`, `SWEPTSURFACE` | 미지원 | 3D surface modeling data is out of scope. |
| `MESH`, `POLYFACE`, `POLYMESH` | 미지원 | Mesh/polyface semantics are out of scope unless a dedicated normalizer is added. |
| `MLINE` | 미지원 | Multiline style expansion is not normalized. |
| `XLINE`, `RAY` | 미지원 | Infinite construction geometry is not normalized. |
| `TRACE` | 미지원 | Legacy trace geometry is not normalized. |
| `SHAPE` | 미지원 | Shape/font definition dependency is not normalized. |
| `TABLE` | 미지원 | Table cells and formatting are not normalized. |
| `TOLERANCE` | 미지원 | GD&T frame semantics are not normalized. |
| `WIPEOUT` | 미지원 | Masking/coverage effects are preview/render concerns and not compared as geometry. |
| `IMAGE`, `IMAGEDEF` | 미지원 | Raster references are not embedded entity-diff data. |
| `PDFUNDERLAY`, `DGNUNDERLAY`, `DWFUNDERLAY` | 미지원 | Underlay content is external reference data and not parsed. |
| `ACAD_PROXY_ENTITY`, `ACAD_PROXY_OBJECT` | 미지원 | Custom/proxy object semantics are unknown. |
| XREF referenced drawings | 제한지원 | Only entities physically present in the parsed DXF are compared. External referenced files are not resolved. |
| Dynamic block evaluation state | 제한지원 | Static block definition/INSERT signals are compared. Full dynamic evaluation is not reproduced. |
| Custom dictionaries, reactors, constraints, extension data | 미지원 | Retention by the DXF library does not mean semantic comparison support. |

## Cross-Cutting Behavior

| Area | Policy |
| --- | --- |
| Layer filters | Include/exclude filters apply to direct entities and block/attribute paths where implemented. |
| Block expansion | Default is enabled. If disabled, block-internal geometry changes may be skipped and must be audited. |
| Text precision | `TEXT`, `MTEXT`, `DIMENSION`, and `MULTILEADER` use broader near-match logic to avoid false added/deleted pairs when text moves and content changes together. |
| Cosmetic changes | Color, lineweight, and linetype can be detected separately from geometry. Suppression must be counted if enabled. |
| OCS/WCS | OCS-aware entities are converted to WCS where implemented. Remaining OCS edge cases are limited support. |
| Paperspace | Paperspace layouts are extracted by default and hash-namespaced by layout. |
| Large drawings | Entity records may be capped for memory safety. Truncation must be visible in audit output. |
| Preview rendering | Lightweight preview may skip heavy entities such as `INSERT`, `HATCH`, `MTEXT`, `DIMENSION`, `LEADER`, `MULTILEADER`, `WIPEOUT`, and proxy objects. The entity diff remains the source of truth. |

## Required Test Gates Before Upgrading an Entity to 지원

An entity currently marked `제한지원` or `미지원` may be upgraded only when all
of the following are true:

1. A normalizer exists in `entity_normalizers.py`.
2. Golden DXF fixtures cover unchanged, added, deleted, modified, and cosmetic-only behavior where applicable.
3. At least one realistic customer-style drawing sample exercises the entity.
4. Suppression/audit output remains correct when the entity is absent, unsupported, filtered, or truncated.
5. Preview rendering behavior is documented separately if it differs from comparison support.

## 법무 검토 필요

The entity matrix itself does not approve any third-party CAD SDK. Legal review
is required before adding entity support through GPL/AGPL libraries, ODA SDKs,
commercial SDKs, or code/data derived from proprietary DWG/DXF specifications
under restrictive terms.

## References

- Local source: `src/services/comparison/dxf_entity_extractor.py`
- Local source: `src/services/comparison/entity_normalizers.py`
- Local source: `src/services/comparison/suppression_audit.py`
- ezdxf capabilities and limitations: https://ezdxf.readthedocs.io/en/stable/introduction.html
