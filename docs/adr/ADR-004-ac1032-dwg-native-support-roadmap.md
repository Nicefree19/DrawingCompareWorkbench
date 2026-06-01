# ADR-004: AC1032 DWG Native Support Roadmap

| Item | Value |
|---|---|
| Status | Accepted |
| Date | 2026-06-01 |
| Decision Date | 2026-06-01 |
| Owner | Codex / DrawingCompareWorkbench maintainers |
| Scope | DWG native reader expansion beyond AC1015 |

## Context

The current product policy is ODA-free. DXF is the accepted CAD baseline, and
DWG native support is limited to the AC1015 simple-2D native reader path.
Modern customer DWGs such as AC1032 are detected, but not decoded by the native
adapter.

The real `D:\도면 비교` corpus confirms the operational gap:

- Original DWG pair is AC1032.
- Direct native DWG compare is unsupported.
- Converted DXF pair under `dxf_registered/before` and `dxf_registered/after`
  compares successfully with 33 raw changes and 7 review zones.
- Folder/file selection now auto-resolves unsupported AC1032 DWGs to matching
  converted DXFs when those converted inputs are present.

This ADR separates the short-term converted-DXF workflow from any future claim
of native AC1032 or all-version DWG support.

## Decision

Do not advertise AC1032 native DWG support yet.

Keep the current production workflow as:

1. AC1015 DWG: limited native reader path, still subject to simple-2D scope.
2. AC1018/AC1021/AC1024/AC1027/AC1032 DWG: user-provided converted DXF is the
   supported workflow.
3. If matching converted DXFs are present in a known work folder layout, the
   pipeline may auto-select those DXFs and must preserve provenance in
   `run_manifest.json`, `direct_compare_summary.json`, and `review_project.json`.

Native support beyond AC1015 requires a separate implementation approval with
license, corpus, parser-contract, and release-claim gates.

## Non-Goals

- Do not add ODA File Converter auto-invocation to customer/runtime defaults.
- Do not bundle ODA, GPL, or AGPL DWG libraries.
- Do not implement speculative PDF-first DWG conversion under this ADR.
- Do not claim "all DWG versions supported" until version-specific evidence is
  complete.

## Support Expansion Plan

### Phase 0: Policy and Corpus Gate

- Confirm license posture against `docs/CAD_FORMAT_SUPPORT_POLICY.md`.
- Collect representative real DWG samples for each target version:
  `AC1018`, `AC1021`, `AC1024`, `AC1027`, `AC1032`.
- For each version, require paired converted DXF truth data and expected compare
  outputs.
- Define pass/fail metrics:
  - import success rate
  - entity coverage
  - unsupported entity counts
  - compare recall against converted-DXF baseline
  - false-positive delta against converted-DXF baseline
  - runtime and memory ceilings

### Phase 1: Adapter Contract Hardening

- Keep `DwgImporter` as the only native adapter boundary.
- Version detection remains cheap and deterministic before full import.
- Unsupported versions must fail before expensive compare work unless an
  approved converted-DXF fallback is selected.
- Every fallback must record original and effective sources.

### Phase 2: Version-by-Version Reader Expansion

Recommended order:

1. `AC1018` / AutoCAD 2004
2. `AC1021` / AutoCAD 2007
3. `AC1024` / AutoCAD 2010
4. `AC1027` / AutoCAD 2013
5. `AC1032` / AutoCAD 2018+

Each version must ship behind explicit tests and diagnostics before enabling it
as a default native-supported version.

### Phase 3: Customer-Grade Support Claim

A DWG version can be documented as natively supported only when:

- parser implementation is approved for license/provenance risk;
- regression corpus covers real model-space, blocks, text, dimensions, hatches,
  splines, leaders, proxy/custom-object failure modes, and corrupted/encrypted
  cases;
- compare results match converted-DXF baseline within accepted thresholds;
- Workbench UI and package artifacts clearly distinguish native import from
  converted-DXF fallback;
- release notes state exact supported DWG versions and exclusions.

## Acceptance Gates

- `DwgVersionDetector` has version-specific tests for every advertised code.
- Native reader diagnostics produce actionable unsupported-section/entity data.
- Preflight rejects unsupported versions before compare work unless fallback is
  resolved.
- Converted-DXF fallback remains available and visible.
- No customer/runtime default path invokes unapproved ODA/GPL/AGPL tooling.

## Current Operational Recommendation

For `D:\도면 비교` and similar AC1032 work folders, use the converted-DXF fallback
workflow. The preferred layout is:

```text
<work folder>/
  before-or-original.dwg
  after-or-revision.dwg
  dxf_registered/
    before/
      before-or-original.dxf
    after/
      after-or-revision.dxf
```

`dxf_registered` is preferred over `dxf_clean` and `dxf_compare` because the real
validation matrix produced substantially lower noise:

| Input path | Raw changes | Review zones |
|---|---:|---:|
| `dxf_registered` fallback | 33 | 7 |
| `dxf_clean` direct | 6,620 | 117 |
| `dxf_compare` direct | 7,654 | 607 |

## Related Documents

- `docs/CAD_FORMAT_SUPPORT_POLICY.md`
- `docs/adr/ADR-001-pdf-first-transition.md`
- `docs/adr/ADR-003-pdf-first-hybrid-viewer.md`
- `out/user_drawing_compare_20260601/VALIDATION_REPORT.md`
