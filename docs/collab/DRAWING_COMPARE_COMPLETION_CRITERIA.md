# Drawing Compare Completion Criteria

Date: 2026-06-03

This document defines when the DrawingCompareWorkbench drawing comparison module
can be called customer-ready. It separates customer production readiness from
explicit approved DWG backend claims. For strategy options and gates needed to
handle all major DWG generations, see
`docs/collab/DWG_ALL_VERSION_SUPPORT_STRATEGY.md`.

## Scope Split

| Scope | Current target | Claim allowed now |
| --- | --- | --- |
| Customer-ready comparison workflow | PDF, DXF, AC1015 native DWG, user-provided converted DXF, registered/converted DXF fallback, explicit customer-provided `user_converter`, explicit local/internal ODA fallback | Yes, with documented fallback and partial-import limits |
| Modern DWG native support | AC1018, AC1021, AC1024, AC1027, AC1032 | Passed for explicit local Autodesk TrueView managed bridge evidence; no customer/default native path unless explicitly selected and approved |
| Target DWG generation coverage | Every claimed DWG generation passing version-specific native gates plus release readiness | Passed for the explicit local Autodesk TrueView managed bridge evidence bundle |

Customer-ready means the module produces repeatable comparison output or a clear
failure with provenance, guardrails, and reviewable warnings. It does not mean
every DWG file is decoded natively.

## Format Support Matrix

| Input path | Default customer path | Explicit/internal path | Current status | Completion condition |
| --- | --- | --- | --- | --- |
| PDF | Enabled through PDF-first/hybrid viewer paths | N/A | Supported by current workflow | Real customer evidence manifest includes PDF pairs and review outputs |
| DXF | Enabled | N/A | Supported; large DXF guarded by token/entity/time limits | Targeted tests and real large-DXF probe remain within budget or fail clearly |
| DWG AC1015 | ODA-free native preview adapter | N/A | Supported baseline only for approved AC1015 fixture/native path | Native result, failure behavior, and policy gate stay green |
| DWG AC1018+ | Fail-closed native path; converted-DXF fallback when user supplies/registration resolves DXF | `user_converter` registered-DXF or customer-provided converter fallback | Fallback-ready; explicit local Autodesk TrueView managed bridge product gate passed | Version-specific converted-DXF baseline and explicit backend gates remain reproducible |
| DWG commercial/native bridge | Disabled unless explicitly selected | `--dwg-backend commercial_sdk` plus approved adapter, license allowlist, and bridge options | Explicit local Autodesk TrueView managed bridge product gate passed for every target generation | Native audit, bridge contract, product `cad_compare` evidence, customer evidence manifest, and release metrics stay green |
| ODA converted fallback | Disabled by default | `--dwg-backend oda_converter` only | Local/internal fallback stabilized with source-signature DXF cache | Explicit mode only, cache provenance present, timeout/token failure clear |

## Policy Requirements

- Default/customer builds must not invoke ODA SDK, ODA File Converter, LibreDWG,
  GPL, AGPL, or derived samples.
- `oda_converter` is allowed only as an explicit local/internal fallback mode.
- `user_converter` and registered/converted DXF fallback are valid customer
  workflows when provenance records original/effective inputs, converted DXF
  path, cache hit/miss, and converter identity where applicable.
- `commercial_sdk` is valid for native claims only when an approved adapter is
  selected explicitly, its license id is allowlisted, bridge command provenance
  is recorded, and the same configuration passes both validation runner and
  product `cad_compare` evidence gates. For the JSON bridge adapter, product
  evidence should be generated with `scripts/run_dwg_product_bridge_evidence.py`
  or `scripts/validate_dwg_native_backend.py --product-evidence-json ...` so
  per-pair result JSON, bridge diagnostics, timeout, and provenance are
  reproducible from the sample pack.
- The final DWG product claim gate is
  `scripts/validate_dwg_product_release_gate.py`; the current
  `build/reports/dwg-product-release-gate-trueview2020-all.full.json` summary
  is `passed` for the explicit local Autodesk TrueView managed bridge evidence
  bundle.
- `src/gui/drawing_compare_workbench.py` remains structurally frozen; new
  widgets/workers must be added outside that monolith unless an approved
  exception is recorded.
- Release wording must say "converted-DXF fallback", "explicit approved
  commercial/native bridge", or "explicit local/internal ODA fallback" where
  applicable. Avoid unqualified native/default DWG wording outside the approved
  evidence scope.

## Current Gap Matrix

| Version/path | Real DWG pair | Converted-DXF baseline | Native reader state | Compare baseline state | Remaining blocker |
| --- | --- | --- | --- | --- | --- |
| AC1009 | 2 local version-matrix source pairs | Fallback-ready: 2 converted-DXF baselines | Not implemented | Partial baselines captured from `out/adr004_legacy_version_matrix_20260603_020500` and p5case matrix | Native backend not implemented |
| AC1012 | 2 local version-matrix source pairs | Fallback-ready: 2 converted-DXF baselines | Not implemented | Partial baselines captured from legacy and p5case matrices | Native backend not implemented |
| AC1014 | 2 local version-matrix source pairs | Fallback-ready: 2 converted-DXF baselines | Not implemented | Partial baselines captured from legacy and p5case matrices | Native backend not implemented |
| AC1015 | 2 local version-matrix source pairs plus fixture/native baseline | Fallback-ready: 2 converted-DXF baselines | ODA-free native fixture adapter only | Native fixture supported; fallback baselines captured from legacy and p5case matrices | Keep native claim limited |
| AC1018 | 2 local version-matrix source pairs plus import-only duplicated baseline | Fallback-ready: 2 converted-DXF baselines | Not implemented | Partial baselines captured from legacy and p5case matrices | Native backend not implemented |
| AC1021 | 2 local version-matrix source pairs plus import-only duplicated baseline | Fallback-ready: 2 converted-DXF baselines | Not implemented | Partial baselines captured from legacy and p5case matrices | Native backend not implemented |
| AC1024 | 2 likely revision pairs | Fallback-ready: 2 converted-DXF baselines | Not implemented | Compact partial baseline: added 342, removed 140, modified 19, unchanged 6431; larger partial baseline: added 0, removed 6686, modified 0, unchanged 113443 | Clean-room evidence and native reader approval |
| AC1027 | 2 likely revision pairs | Fallback-ready: 2 converted-DXF baselines | Not implemented | Compact partial baseline: added 26741, removed 2, modified 0, unchanged 21986; larger partial baseline: added 2732, removed 2244, modified 1, unchanged 52501 | Clean-room evidence and native reader approval |
| AC1032 | 3 real/likely revision pairs including `D:\도면 비교` | Fallback-ready: 2 registered/converted-DXF baselines; ODA cached pair also completes with raised token budget | Not implemented | Registered baseline partial: added 37, removed 39, modified 207, unchanged 6951; compact partial baseline: added 12746, removed 9428, modified 0, unchanged 160695; ODA cached compare partial in about 87.9s with raised DXF token budget | Clean-room evidence and native reader approval |

## Customer-Ready Completion Criteria

The drawing comparison module is customer-ready only when all of these are true:

1. Supported inputs either compare successfully or fail with stable
   `error_code`, message, and provenance.
2. Default/customer execution path does not call ODA/GPL/AGPL tooling.
3. Fallback results expose original input, effective converted input, backend
   mode, cache hit/miss where relevant, and unsupported/partial warnings.
4. Folder compare and direct file compare both preserve input-resolution
   provenance into result JSON, run manifest, and review project artifacts.
5. Large drawing behavior is bounded by runtime, token, entity, memory, cancel,
   and progress gates.
6. Partial imports are accepted only when skipped/approximated entities are
   visible to the reviewer and release notes disclose the limitation.
7. Customer evidence includes CAD and PDF pairs, large drawings, blocked/negative
   controls, block-text cases, and reviewer/operator sign-off.

## Performance Thresholds

| Gate | Required behavior |
| --- | --- |
| Cache reuse | Re-running the same explicit ODA fallback pair must avoid ODA reconversion and record `cache.hit=true` |
| Large cached ODA AC1032 pair | With explicit raised DXF token budget, the current `D:\도면 비교` pair completes or fails clearly within 120s |
| Default ODA fallback budget | Without raised token budget, oversized converted DXF must fail fast with `CAD_TOKEN_LIMIT_EXCEEDED` instead of hanging |
| Progress/cancel | Long runs must emit progress often enough for `progress_max_gap_s <= 10` where the worker supports progress instrumentation |
| Failure cleanup | Converter temp output and subprocesses must not remain after timeout/failure |

The current local evidence after closed-polyline optimization:

- Cached ODA AC1032 import plus normalize: previously `>120s` timeout, now about
  `22.8s`.
- Cached ODA AC1032 compare with `--max-dxf-tokens 12000000`: about `87.9s`,
  `status=partial`, `added=4607`, `deleted=3575`, `modified=6`,
  `unchanged=5632`.
- Default ODA token budget path fails fast with `CAD_TOKEN_LIMIT_EXCEEDED`.

## Partial Import Acceptance Criteria

Partial import can be accepted for customer readiness only when:

- `status=partial` is visible in payload and reviewer UI.
- Warnings list unsupported, approximated, skipped, xref, or fallback conditions.
- The result summary is not marketed as complete geometry parity.
- Release notes name the unsupported entity classes or known exclusions.
- Critical evidence workflows include at least one partial-import sample and one
  clean unsupported/fail-closed sample.

Partial import is not acceptable for native-support release claims unless the
version-specific release wording explicitly lists exclusions and the converted
DXF baseline comparison remains within accepted tolerances.

## Error-Code Policy

| Scenario | Required code/message behavior |
| --- | --- |
| Unsupported DWG version in default path | `DWG_UNSUPPORTED_VERSION` or equivalent fail-closed DWG import code |
| ODA fallback disabled | Clear message that legacy ODA fallback is disabled or not configured |
| Explicit ODA conversion failure | `ODA_FALLBACK_FAILED` with exception type/provenance |
| Converted DXF token excess | `CAD_TOKEN_LIMIT_EXCEEDED` with estimated token count and max budget |
| Import timeout/cancel | `CAD_IMPORT_TIMEOUT` or `CAD_IMPORT_CANCELLED` with budget details |
| Unsupported entities | Partial warning with unsupported raw type and comparison impact |
| Missing fallback pair | Clear missing converted-DXF or input-resolution error |

## Release Gate

The measurable release readiness gate is defined in
`docs/collab/DRAWING_COMPARE_RELEASE_READINESS_METRICS.md`. Use that document
and `scripts/audit_drawing_compare_release_readiness.py` to evaluate whether
fallback-based customer-ready claims are supported by current evidence.

### Code Gate

- `python -m pytest tests\unit\services\comparison\test_drawing_normalizer.py tests\unit\services\comparison\test_import_compare_pipeline.py tests\unit\services\comparison\test_dwg_importer.py tests\unit\cli\test_cad_compare_cli.py -q`
- `python scripts\cad_policy_gate.py`
- `git diff --check`

### Real Sample Gate

- `D:\도면 비교` registered/converted DXF path completes with stable summary.
- `D:\도면 비교` explicit `oda_converter` cached path records both cache hits on
  repeated runs.
- Default customer/native DWG path remains fail-closed for unsupported AC1032.

### Evidence Gate

- Result JSON includes source/import/diff summaries.
- Run manifest records original and effective paths.
- Review project exposes fallback/partial notices.
- Customer evidence manifest passes inventory/audit requirements, including
  large-DWG probe evidence.

## Native DWG Phase 1 Entry Gates

No binary section decoding may start for a target version until all entry gates
are true:

1. Real before/after DWG pair exists for the version.
2. User-converted or registered DXF before/after baseline exists.
3. Converted-DXF compare summary is captured.
4. `docs/DWG_CLEANROOM_FORMAT_CONTRACT.md` or equivalent approved contract
   contains clean-room evidence for the target version.
5. Diagnostics-only tests exist and still report unsupported/blocked status
   before implementation.
6. Section-map reader approval is recorded.

Implementation order is fixed:

1. AC1018
2. AC1021
3. AC1024
4. AC1027
5. AC1032

## Native DWG Phase 1 Exit Gates

Before a target version is described as natively supported:

1. Native import result is captured for the target baseline pair.
2. Native result compares against converted-DXF baseline within accepted metrics.
3. Unsupported, corrupted, encrypted, timeout, cancel, and large-input behavior
   are covered by tests.
4. Runtime and memory evidence is recorded.
5. Partial import limitations and unsupported entities are documented.
6. Release wording names the exact version and excludes unpassed versions.
7. `cad_policy_gate.py`, targeted tests, and `git diff --check` pass.

## Claim Rules

Allowed wording now:

- "DXF and PDF comparison are supported."
- "AC1015 native DWG baseline is supported under the approved ODA-free path."
- "Modern DWGs can be compared through user-provided converted DXF where
  matching converted files are available."
- "Explicit local/internal ODA fallback is available only when
  `oda_converter` is selected."

Forbidden wording now:

- "AC1032 native DWG is supported."
- "All DWG versions are supported."
- "ODA conversion is automatic in customer builds."
- "Partial import results are complete geometry parity."

## Next Work Priority

1. Complete customer evidence manifest and release audit for the fallback-based
   customer-ready claim.
2. Replace local version-matrix evidence with customer-approved real revision
   pairs where a stricter release needs customer corpus rather than local
   compatibility evidence.
3. Add more AC1032 breadth pairs beyond `D:\도면 비교`.
4. Prepare clean-room evidence packets before any Phase 1 native reader work.
