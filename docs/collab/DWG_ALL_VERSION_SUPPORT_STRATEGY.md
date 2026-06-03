# DWG All-Version Support Strategy

Date: 2026-06-02

This document defines the practical paths for making DrawingCompareWorkbench
handle all major DWG generations. It is a strategy and gate document, not an
implementation approval for native AC1018+ parsing.

## Decision Frame

There are three viable strategies:

| Option | Claim potential | Policy fit | Cost/risk | Verdict |
| --- | --- | --- | --- | --- |
| Commercial DWG SDK | Highest chance of broad native import | Requires legal/product policy change | License, redistribution, SBOM, CI, packaging, SDK failure handling | Best path for true broad DWG import |
| User/registered converted-DXF fallback | Handles broad DWG inputs after external conversion | Fits current ODA-free customer policy | Customer conversion workflow and provenance burden | Best near-term production path |
| Clean-room native parser expansion | Keeps ODA-free posture | Fits policy only after clean-room gates | Very high engineering and correctness risk | Version-by-version long-term path |

The current product can pursue customer readiness through converted-DXF
fallbacks. Explicit local Autodesk TrueView managed-bridge evidence now passes
the final product gate for the target DWG generations; default/customer native
paths still require explicit approved backend selection.

## DWG Version Matrix

Version mappings follow Autodesk drawing version codes and the current in-repo
detector. Autodesk ObjectARX `DwgVersion` is also used as a recent-version
cross-check where available.

| DWG code | AutoCAD generation | Current native state | Current fallback state | Corpus required | Claim now |
| --- | --- | --- | --- | --- | --- |
| `AC1009` | R11/R12 | Native-ready under explicit local Autodesk TrueView managed bridge evidence | Fallback-ready with 2 converted-DXF baselines | Release evidence and metrics pass | Explicit approved bridge claim only |
| `AC1012` | R13 | Native-ready under explicit local Autodesk TrueView managed bridge evidence | Fallback-ready with 2 converted-DXF baselines | Release evidence and metrics pass | Explicit approved bridge claim only |
| `AC1014` | R14 | Native-ready under explicit local Autodesk TrueView managed bridge evidence | Fallback-ready with 2 converted-DXF baselines | Release evidence and metrics pass | Explicit approved bridge claim only |
| `AC1015` | 2000/2000i/2002 | Native-ready under explicit local Autodesk TrueView managed bridge evidence; limited ODA-free fixture path still exists | Fallback-ready with 2 converted-DXF baselines plus fixture native baseline | Release evidence and metrics pass | Explicit approved bridge claim only |
| `AC1018` | 2004/2005/2006 | Native-ready under explicit local Autodesk TrueView managed bridge evidence | Fallback-ready with 2 converted-DXF baselines | Release evidence and metrics pass | Explicit approved bridge claim only |
| `AC1021` | 2007/2008/2009 | Native-ready under explicit local Autodesk TrueView managed bridge evidence | Fallback-ready with 2 converted-DXF baselines | Release evidence and metrics pass | Explicit approved bridge claim only |
| `AC1024` | 2010/2011/2012 | Native-ready under explicit local Autodesk TrueView managed bridge evidence | Fallback-ready with 2 converted-DXF baselines | Release evidence and metrics pass | Explicit approved bridge claim only |
| `AC1027` | 2013/2014/2015/2016/2017 | Native-ready under explicit local Autodesk TrueView managed bridge evidence | Fallback-ready with 2 converted-DXF baselines | Release evidence and metrics pass | Explicit approved bridge claim only |
| `AC1032` | 2018+ | Native-ready under explicit local Autodesk TrueView managed bridge evidence | Fallback-ready with registered/converted-DXF baselines; ODA remains explicit local/internal only | Release evidence and metrics pass | Explicit approved bridge claim only |
| Newer code | Future/unknown | Unsupported | User exports supported DXF | Official mapping, samples, baseline | No claim |

## Recommended Architecture

Keep the existing backend boundary and make support claims depend on backend
approval:

| Backend | Current state | Customer default eligibility |
| --- | --- | --- |
| `cleanroom_native` | AC1015 limited native path | Eligible only for approved AC1015 scope |
| `user_converter` | Registered DXF or customer-provided converter workflow | Eligible when provenance/cache metadata is preserved |
| `oda_converter` | Explicit local/internal fallback | Not customer default |
| `commercial_sdk` | Fail-closed placeholder | Eligible only after legal/license/release gates |

Every backend must produce `CanonicalDrawing` and must record:

- original source path and detected DWG code;
- effective imported path or backend-native source;
- backend mode and adapter identity;
- warnings for partial, proxy, xref, skipped, or approximated content;
- stable `error_code` for unsupported, encrypted, corrupted, timeout, cancel,
  license failure, or adapter failure cases.

The default/customer path must fail closed when no license-approved backend is
available.

## Commercial SDK Entry Gate

Before wiring a commercial DWG backend:

1. Legal approves vendor, license scope, redistribution, and customer build use.
2. SBOM, NOTICE, installer, support, and update obligations are documented.
3. CI/release environments distinguish licensed and unlicensed builds.
4. SDK absent/expired/license-failed behavior returns a clear fail-closed result.
5. `cad_policy_gate.py` is updated to permit only the approved backend surface.
6. Release wording names exact supported DWG generations and exclusions.

Candidate SDK paths:

- Autodesk RealDWG: official Autodesk SDK for DWG/DXF support in third-party
  applications.
- ODA Drawings SDK: commercial SDK for DWG/DXF/DGN access, edit, view, and save
  workflows.

## Corpus Gate

Each claimed DWG generation needs at least:

- 2 real before/after DWG pairs;
- 2 converted-DXF baseline pairs;
- expected compare summaries;
- corrupted and encrypted samples;
- one large drawing sample;
- block, text, MTEXT, dimension, hatch, xref, spline, leader, proxy/custom-object
  samples;
- reviewer evidence showing native/commercial-SDK results against converted-DXF
  baselines.

Known current gaps:

- `AC1009`, `AC1012`, `AC1014`, `AC1015`, `AC1018`, and `AC1021`: the second
  p5case local version-matrix baseline now validates with `--compare-source dxf`
  after spatial-index optimization, so the fallback-scope all-version audit
  passes for every target generation.
- These lower-version baselines are local compatibility evidence. Customer
  release evidence should still replace or supplement them with
  customer-approved real revision pairs where confidentiality and corpus policy
  require that distinction.
- A small DXF-fixture matrix attempt was rejected by ODA for the minimal fixture
  DXF and is not evidence.
- Explicit local Autodesk TrueView 2020 managed bridge evidence now makes the
  native all-version audit pass for `AC1009` through `AC1032` in
  `build/reports/dwg-product-release-gate-trueview2020-all.full.json`.
  This is native backend evidence, not a customer/default release claim.
- Latest explicit local/internal ODA fallback evidence is captured in
  `build/reports/dwg-oda-fallback-support-evidence.json` and audited by
  `build/reports/dwg-oda-fallback-support-audit.json`. That audit is
  `claim_scope=fallback` only.
- The latest final product/native claim gate passes in
  `build/reports/dwg-product-release-gate-trueview2020-all.full.json`.
  Native validation, bridge contract, product `cad_compare file` evidence,
  native all-version audit, and release readiness all report `passed`.
  The approved scope remains the explicit local Autodesk TrueView managed
  bridge plus documented converted-DXF and explicit local/internal fallback
  paths; default/customer native behavior remains fail-closed unless an
  approved backend is explicitly selected.

## Accuracy and Stability Gates

Against converted-DXF baselines:

| Metric | Required |
| --- | ---: |
| Main change recall | `>= 90%` |
| Main change precision | `>= 85%` |
| False-positive zone rate | `<= 15%` |
| Duplicate zone rate | `<= 10%` |
| Small drawing runtime | `<= 30s` |
| Medium drawing runtime | `<= 120s` |
| Large drawing runtime | `<= 10min` or clear timeout/failure |
| Cancel response | `<= 10s` |
| Orphan processes | `0` |

Partial import is acceptable only when warnings and unsupported entity classes
are visible in result JSON, manifests, and review artifacts.

## Release Wording

Allowed before commercial/native gates pass:

- "Modern DWGs can be compared through user-provided or registered converted DXF
  when provenance is preserved."
- "Explicit local/internal ODA fallback is available only when `oda_converter`
  is selected."
- "A licensed backend can be evaluated for broader DWG import after legal and
  evidence gates pass."

Forbidden before gates pass:

- "All DWG versions supported."
- "Modern DWG native support is complete."
- "AC1032 native DWG is supported."
- "Customer default path automatically converts all DWGs."

Even after gates pass, avoid unlimited wording. Prefer scoped wording such as:

- "Standard AutoCAD DWG generations `AC1009` through `AC1032` are supported
  under the licensed backend, excluding encrypted, corrupted, and unsupported
  proxy/custom objects as documented."

## Implementation Tracks

### Track 1: Commercial SDK Pilot

1. Create legal/license ADR.
2. Provide a licensed adapter in a separate module and expose it with
   `DRAWING_COMPARE_COMMERCIAL_DWG_ADAPTER=<module>:<factory>`.
   The selector remains fail-closed when the variable is absent or the adapter
   cannot be loaded.
   For a subprocess-based integration, use the built-in JSON bridge factory:
   `DRAWING_COMPARE_COMMERCIAL_DWG_ADAPTER=src.services.comparison.commercial_dwg_json_adapter:create_adapter`.
   The bridge also requires:
   - `DRAWING_COMPARE_COMMERCIAL_DWG_JSON_COMMAND`
   - `DRAWING_COMPARE_COMMERCIAL_DWG_JSON_LICENSE_ID`
   - `DRAWING_COMPARE_COMMERCIAL_DWG_JSON_SUPPORTED_VERSIONS`
   - optional `DRAWING_COMPARE_COMMERCIAL_DWG_JSON_ARGS_JSON`

   The wrapper command must emit a JSON object containing either a
   `DwgAdapterDrawing` payload or `{ "drawing": <DwgAdapterDrawing> }` to
   stdout. It remains unavailable until the command, approved license id, and
   supported version list are all configured. The bridge records command
   resolution, command SHA-256, args template, license id, timeout, and
   supported versions in import reports and native validation artifacts.
   Before running the full sample-pack gate, smoke-test the wrapper contract
   against one or more representative DWGs:
   `python scripts/validate_dwg_json_bridge_contract.py <dwg> --dwg-allowed-license-id <APPROVED-LICENSE> --bridge-command <wrapper> --bridge-license-id <APPROVED-LICENSE> --bridge-supported-versions <ACCODE,...>`.
   The helper `tools/dwg_converted_dxf_json_bridge.py` can adapt an explicit
   external DWG-to-DXF converter into this JSON bridge contract for fallback
   diagnostics. Its output is marked `converted_dxf_bridge` and must not be
   counted as native DWG evidence.
   The helper `tools/autocad_dwg_json_bridge.py` can drive an installed
   Autodesk AcCoreConsole plus generated AutoLISP to emit the same JSON
   contract from the opened DWG database. This is an explicit local/commercial
   native bridge only; it must be invoked through the `commercial_sdk` backend
   with an approved license id such as `AUTODESK-AUTOCAD-LOCAL`, and it must
   not be enabled in default/customer paths. On the current workstation,
   AutoCAD 2017 proved an AC1027 native bridge smoke path, while DWG TrueView
   2020 opened DWGs but rejected LISP loading and therefore is not sufficient
   for this native JSON bridge.
   The helper `tools/autodesk_dwg_json_bridge.py` compiles and runs
   `tools/autodesk_dwg_json_extractor.cs` against an installed Autodesk managed
   runtime. On the current workstation, DWG TrueView 2020 proved the standalone
   managed native bridge path and produced all-target-version native audit
   evidence with license id `AUTODESK-TRUEVIEW-LOCAL`. This bridge uses native
   DWG database reads, does not convert to DXF, and remains explicit
   local/internal evidence only unless legal/product release gates approve it.
   JSON bridge output is counted as native evidence only when the wrapper
   positively declares native DWG provenance with `uses_native_dwg=true` or an
   approved `evidence_scope` such as `native_dwg_bridge`,
   `commercial_dwg_native`, or `commercial_sdk_native`.
3. Map SDK output to `DwgAdapterDrawing` or directly to `CanonicalDrawing`.
4. Keep unlicensed builds fail-closed. File and folder CLI runs must also pass
   the approved license id explicitly, for example
   `--dwg-backend commercial_sdk --dwg-allowed-license-id <APPROVED-LICENSE>`.
   When using the JSON bridge through the product CLI, pass the wrapper settings
   explicitly on the same command:
   `python -m src.cli.cad_compare file <before.dwg> <after.dwg> --dwg-backend commercial_sdk --dwg-commercial-adapter-spec src.services.comparison.commercial_dwg_json_adapter:create_adapter --dwg-allowed-license-id <APPROVED-LICENSE> --dwg-bridge-command <wrapper> --dwg-bridge-args-json <ARGS-JSON> --dwg-bridge-license-id <APPROVED-LICENSE> --dwg-bridge-supported-versions <ACCODE,...> --output <result.json>`.
   Folder preflight, descriptor scan, BatchCompare, and region-aware sidecar
   source resolution must receive the same allowlist so unsupported DWG versions
   are downgraded only when the approved adapter is actually loaded.
5. Validate the version sample pack through direct DWG compare with the approved
   backend while keeping converted-DXF outputs as the oracle baseline:
   `python scripts/validate_adr004_version_sample_pack.py <sample-pack> --compare-source dwg --dwg-backend commercial_sdk --dwg-allowed-license-id <APPROVED-LICENSE> --json-report <sdk-validation.json>`.
6. Prefer the native backend validation runner when executing the full gate:
   `python scripts/validate_dwg_native_backend.py <sample-pack> --adapter-spec src.services.comparison.commercial_dwg_json_adapter:create_adapter --dwg-allowed-license-id <APPROVED-LICENSE> --bridge-command <wrapper> --bridge-args-json <ARGS-JSON> --bridge-license-id <APPROVED-LICENSE> --bridge-supported-versions <ACCODE,...> --bridge-contract-json build\reports\dwg-json-bridge-contract.json --product-evidence-json build\reports\dwg-product-bridge-evidence.json`.
   This writes the validation summary, native evidence manifest, and native
   all-version audit in one pass, and fails closed if the adapter is absent,
   unavailable, placeholder, license-disallowed, or does not explicitly report
   support for every target DWG version in the validation scope. When the
   selected adapter is the commercial JSON bridge, the optional
   `--bridge-contract-json` output records a preflight import contract for the
   sample-pack DWG inputs and fails the native validation run if the contract
   fails. The optional `--product-evidence-json` output runs the public
   `cad_compare file` path for sample-pack DWG pairs and fails the native
   validation run if product-path bridge evidence fails.
   Before running the full gate, use
   `python scripts/doctor_dwg_native_bridge.py --bridge-command <wrapper> --bridge-args-json <ARGS-JSON> --bridge-license-id <APPROVED-LICENSE> --bridge-supported-versions <ACCODE,...> --dwg-allowed-license-id <APPROVED-LICENSE> --target-version <ACCODE> --probe-input <sample.dwg> --require-probe --out build\reports\dwg-native-bridge-doctor.json`.
   The doctor is a fast P0 environment check for command existence, license
   allowlisting, complete supported-version declaration, and positive native
   bridge provenance. A passing doctor report is not sufficient for the product
   claim, but a failing doctor report means the final product gate cannot pass.
7. Generate product-path evidence through the public CLI with
   `python scripts/run_dwg_product_bridge_evidence.py <sample-pack> --dwg-allowed-license-id <APPROVED-LICENSE> --bridge-command <wrapper> --bridge-args-json <ARGS-JSON> --bridge-license-id <APPROVED-LICENSE> --bridge-supported-versions <ACCODE,...> --summary-json build\reports\dwg-product-bridge-evidence.json`.
   This runs `python -m src.cli.cad_compare file` for sample-pack DWG pairs and
   records per-pair result JSON, exit code, timeout status, source provenance,
   cleanup evidence, `commercial_dwg_json_bridge` diagnostics, and bridge
   adapter metadata. The runner fails closed if product CLI output lacks
   positive native provenance (`uses_native_dwg=true` or an approved native
   `evidence_scope`) or is marked as converted-DXF/fallback bridge evidence.
   Its summary can be supplied to release audit as product result/run evidence.
8. Aggregate the SDK validation summary with
   `python scripts/build_dwg_all_version_support_evidence.py --summary <sdk-validation.json> --out <evidence.json>`.
   Native/commercial evidence is counted only when the run used direct DWG
   compare, an approved native/commercial backend, successful imports/compare,
   and valid converted-DXF before/after outputs for the same pair.
9. Run the final product release gate with
   `python scripts/validate_dwg_product_release_gate.py <sample-pack> --customer-evidence-manifest <customer_evidence_manifest.json> --baseline-metrics <baseline_metrics.json> --dwg-all-version-audit build\reports\dwg-all-version-support-audit.json --dwg-allowed-license-id <APPROVED-LICENSE> --bridge-command <wrapper> --bridge-args-json <ARGS-JSON> --bridge-license-id <APPROVED-LICENSE> --bridge-supported-versions <ACCODE,...>`.
   This orchestrates native validation, bridge contract, product
   `cad_compare file` evidence, native all-version audit, and release readiness
   audit. The output `dwg-product-release-gate.json` is the final claim gate
   summary, including blocked native versions and version-level `next_actions`.
   It must remain failed until native validation and release readiness both
   pass.
   When the version evidence is split across multiple sample packs, pass the
   first pack positionally and add the rest with `--extra-sample-pack <pack>`.
   The gate validates each pack only for the versions present in that pack,
   aggregates the validation summaries, bridge contracts, and product
   `cad_compare` evidence, then runs one native all-version audit over the
   combined evidence. A single pack may contribute valid import/compare
   evidence even when it cannot satisfy the native baseline minimum alone; the
   combined native audit is the baseline-count authority for multi-pack runs.
   Its summary also reconciles the supplied fallback audit
   with the native audit so versions that already have fallback corpus evidence
   do not keep surfacing sample-collection work ahead of the real blocker:
   configuring an approved native/commercial DWG backend and capturing native
   baselines.

### Track 2: Fallback Production Hardening

1. Expand registered/user-converted DXF corpus.
2. Generate local real-world DWG manifests with
   `python scripts/build_real_world_dwg_manifest.py <local-dwg-folder> --manifest <local-manifest.json>`,
   then validate them with `scripts/validate_real_world_dwg_samples.py`. The
   manifest builder excludes generated repository output directories such as
   `out/` and `build/` by default; use `--include-generated` only for
   diagnostics, not customer evidence.
3. Aggregate version validation summaries with
   `python scripts/build_dwg_all_version_support_evidence.py --summary <validation.json> --out <evidence.json>`.
   Use `scripts/validate_adr004_version_sample_pack.py --version <ACCODE>`
   when a large sample pack needs bounded per-version revalidation. Use
   `--compare-source dxf` when the evidence being measured is a registered or
   user-converted DXF baseline rather than a DWG/user-converter route.
4. Run
   `python scripts/audit_dwg_all_version_support.py --evidence-manifest <evidence.json> --phase0-inventory <inventory.json> --phase0c-baselines <baseline.json> --real-world-validation <validation.json> --out <audit.json>`
   to separate fallback-ready gaps from native-ready gaps. Treat its
   `next_actions` list as the version-by-version evidence backlog.
5. Run `scripts/audit_drawing_compare_release_readiness.py` with
   `--dwg-all-version-audit <audit.json>` so the release gate hard-checks that
   every target generation is fallback-ready and that default/customer ODA calls
   remain zero.
6. Run the same release audit with `--native-dwg-audit <native-audit.json>
   --require-native-dwg` only when making a native/all-version claim; this must
   remain failed until every target generation is native-ready.
7. Keep claims limited to fallback processing until the native claim gate passes.

### Track 3: Clean-Room Native Expansion

1. Approve `DWG_CLEANROOM_FORMAT_CONTRACT`.
2. Implement diagnostics-only reader for the target generation.
3. Add section/entity support incrementally.
4. Compare native output against converted-DXF baselines.
5. Enable each generation only after its exit gate passes.

Recommended order remains:

1. `AC1018`
2. `AC1021`
3. `AC1024`
4. `AC1027`
5. `AC1032`

## Test and Policy Plan

- Keep `src/gui/drawing_compare_workbench.py` unchanged.
- Extend `cad_policy_gate.py` only after an approved commercial SDK policy
  exists.
- Add forbidden-claim scanning for broader native-support wording when release
  documentation expands.
- Keep unit coverage proving file and folder converted-DXF fallback can route
  all major unsupported DWG codes (`AC1009`, `AC1012`, `AC1014`, `AC1018`,
  `AC1021`, `AC1024`, `AC1027`, `AC1032`) plus unknown future `AC` signatures
  through registered converted-DXF fallback when matching DXFs exist.
- Keep unit coverage proving explicit `user_converter` can invoke a
  customer-provided converter executable, cache the converted DXF by source
  signature, reuse cache hits without reconversion, and preserve failure
  provenance.
- Keep coverage proving the same `user_converter` options flow through
  folder/BatchCompare runs, including preflight downgrade of native-unsupported
  DWG versions when a valid explicit converter is configured.
- Keep descriptor and region-aware sidecar paths aligned with `user_converter`
  so folder scans, matching, compare, and optional localized compare can share
  the same converted-DXF cache.
- Keep coverage proving approved commercial SDK adapter availability and
  license allowlists flow through file, folder, descriptor scan, preflight,
  BatchCompare, and region-aware sidecar runs, including preflight downgrade of
  native-unsupported DWG versions only when the adapter is approved.
- Add unit tests for backend selection, license failure, unsupported versions,
  corrupted/encrypted files, and provenance.
- Add integration tests for every claimed DWG generation.
- Run real-sample tests, `audit_dwg_all_version_support.py`, and release
  readiness audit before any customer claim.

## References

- Autodesk drawing version codes:
  https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/drawing-version-codes-for-autocad.html
- Autodesk ObjectARX `DwgVersion` enum:
  https://help.autodesk.com/view/OARX/2026/ENU/?guid=OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_DwgVersion
- Autodesk RealDWG overview:
  https://aps.autodesk.com/developer/overview/realdwg
- ODA Drawings SDK:
  https://www.opendesign.com/products/drawings
- Project policy:
  `docs/THIRD_PARTY_LICENSE_POLICY.md`,
  `docs/CAD_FORMAT_SUPPORT_POLICY.md`,
  `docs/adr/ADR-004-ac1032-dwg-native-support-roadmap.md`.
