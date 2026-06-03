# Drawing Compare Release Readiness Metrics

Date: 2026-06-02

This document defines the measurable release-readiness gate for the
DrawingCompareWorkbench drawing comparison module. It supports fallback-based
customer-ready claims and, when combined with the final product gate, the
explicit local Autodesk TrueView managed-bridge DWG evidence bundle.

## Scope

| Scope | Release posture |
| --- | --- |
| PDF compare | Claimable when evidence and audit gates pass |
| DXF compare | Claimable when evidence and audit gates pass |
| AC1015 native DWG baseline | Claimable only under the approved ODA-free limited path |
| AC1018/AC1021/AC1024/AC1027/AC1032 native DWG | Product gate passed under explicit local Autodesk TrueView managed bridge evidence; not customer/default native unless that backend is explicitly selected and approved |
| Converted-DXF fallback | Claimable when provenance records original and effective inputs |
| Explicit ODA fallback | Local/internal only, claimable only as explicit `oda_converter` fallback |
| Commercial SDK backend | Explicit only; requires `DRAWING_COMPARE_COMMERCIAL_DWG_ADAPTER` plus an approved license allowlist propagated through file, folder, preflight, descriptor scan, and batch paths |

## Go / No-Go

Go requires all of these:

- Supported workflows either complete or fail with stable `error_code`, message,
  backend mode, original input, and effective input provenance.
- Default/customer paths invoke ODA SDK, ODA File Converter, LibreDWG, GPL, and
  AGPL 0 times.
- Partial imports show warnings and unsupported/skipped/approximated evidence.
- Timeout, cancel, progress, cache, and cleanup evidence is present.
- For an all-version fallback product claim,
  `build\reports\dwg-all-version-support-audit.json` is supplied and passes
  with every target generation fallback-ready and zero default/customer ODA
  calls.
- The release readiness audit reports `status=passed` or an explicitly accepted
  `status=partial` with only known AC1018/AC1021 baseline gaps.

No-Go if any of these occur:

- Hang, unhandled exception, or unclear failure.
- Partial result presented as complete geometry parity.
- Missing fallback provenance.
- ODA/GPL/AGPL use in a default/customer path.
- Unqualified modern/native DWG or broad DWG support wording outside the
  explicit approved backend/evidence scope.

## Quantitative Thresholds

| Metric | Minimum release gate | Preferred |
| --- | ---: | ---: |
| Main change recall | `>= 90%` | `>= 95%` |
| Main change precision | `>= 85%` | `>= 90%` |
| False-positive zone rate | `<= 15%` | `<= 10%` |
| Duplicate zone rate | `<= 10%` | `<= 5%` |
| PDF/DXF overlay error | `<= 10px @150dpi` | `<= 5px @150dpi` |
| Small drawing runtime | `<= 30s` | lower is better |
| Medium drawing runtime | `<= 120s` | lower is better |
| Large drawing runtime | `<= 10min` or clear timeout/failure | lower is better |
| Progress max gap | `<= 10s` | `<= 5s` |
| Cancel response | `<= 10s` | `<= 5s` |
| Orphan processes | `0` | `0` |
| Default/customer ODA calls | `0` | `0` |
| Exported sensitive local path leaks | `0` | `0` |

## Evidence Minimums

The customer evidence manifest must include at least:

| Evidence class | Required count |
| --- | ---: |
| PDF before/after pairs | 10 |
| DXF before/after pairs | 10 |
| Large CAD/DXF pairs | 3 |
| AC1015 native baselines | 3 |
| AC1024 converted-DXF fallback pairs | 2 |
| AC1027 converted-DXF fallback pairs | 2 |
| AC1032 converted-DXF fallback pairs | 2 |
| Negative/failure samples | 5 |
| Partial import samples | 3 |
| Block/text/dimension focused pairs | 5 |

Current audit evidence marks every target generation from `AC1009` through
`AC1032` fallback-ready. Explicit local Autodesk TrueView 2020 managed bridge
evidence also marks every target generation native-ready in
`build\reports\dwg-product-release-gate-trueview2020-all.full.json`.

The current explicit ODA fallback evidence is stored at
`build/reports/dwg-oda-fallback-support-evidence.json` and
`build/reports/dwg-oda-fallback-support-audit.json`. It may support only an
explicit local/internal fallback statement, not a default customer or native
support statement.

The latest final product gate artifact is
`build/reports/dwg-product-release-gate-trueview2020-all.full.json`. Its
current state is `passed`: native validation, bridge contract, product
`cad_compare file` evidence, native all-version audit, and release readiness
all pass with zero hard failures.

The current release baseline evidence counts are `pdf_pairs=10`,
`dxf_pairs=15`, `large_cad_dxf_pairs=3`, `ac1015_native_baselines=4`,
`ac1024_converted_dxf_fallback_pairs=2`,
`ac1027_converted_dxf_fallback_pairs=2`,
`ac1032_converted_dxf_fallback_pairs=2`,
`negative_failure_samples=5`, `partial_import_samples=15`, and
`block_text_dimension_pairs=5`.

The current release metrics are `recall=1.0`, `precision=0.923077`,
`false_positive_zone_rate=0.076923`, `duplicate_zone_rate=0.0`,
`overlay_error_px_150dpi=0.0`, `small_drawing_seconds=3.854053`,
`medium_drawing_seconds=2.432519`, `large_drawing_seconds=4.497614`,
`progress_max_gap_s=0.69042`, `cancel_response_s=0.003209`,
`orphan_processes=0`, `customer_path_oda_calls=0`, and
`exported_sensitive_path_leaks=0`.

## Release Readiness Audit

Run:

```powershell
python scripts\audit_drawing_compare_release_readiness.py `
  --result-json <result.json> `
  --run-manifest <run_manifest.json> `
  --customer-evidence-manifest <customer_evidence_manifest.json> `
  --baseline-metrics <baseline_metrics.json> `
  --dwg-all-version-audit build\reports\dwg-all-version-support-audit.json `
  --out <release_readiness_audit.json>
```

Audit inputs:

- Result JSON.
- Run manifest.
- Customer evidence manifest.
- Optional baseline metrics JSON.
- Optional all-version fallback audit JSON. Supply this for any all-generation
  fallback product claim.
- Optional native DWG audit JSON. It is informational unless
  `--require-native-dwg` is set.
- Native/commercial SDK evidence manifests must come from
  `validate_adr004_version_sample_pack.py --compare-source dwg --dwg-backend commercial_sdk`
  or an approved clean-room native backend, then be aggregated by
  `build_dwg_all_version_support_evidence.py`; DXF-only validation cannot
  satisfy native readiness.
- The preferred native gate runner is
  `python scripts/validate_dwg_native_backend.py <sample-pack> --adapter-spec <module>:<factory> --dwg-allowed-license-id <APPROVED-LICENSE>`,
  which emits validation, evidence, and native audit artifacts and fails closed
  when backend approval, license evidence, or required-version support evidence
  is missing.
- Subprocess SDK integrations may use
  `src.services.comparison.commercial_dwg_json_adapter:create_adapter`, but the
  evidence is valid only when the command, approved license id, supported
  versions, command hash, and JSON bridge output are recorded in the validation
  artifacts.
- `scripts/validate_dwg_json_bridge_contract.py` is the preflight smoke gate
  for a JSON bridge wrapper. It must pass before its output can be treated as
  native/commercial SDK evidence for the full sample-pack gate.
- `tools/dwg_converted_dxf_json_bridge.py` is a fallback bridge helper for
  explicit external DWG-to-DXF converters. It may satisfy the JSON bridge
  contract smoke test, but its metadata marks `evidence_scope` as
  `converted_dxf_bridge`, and the all-version evidence builder excludes those
  records from native baseline counts.
- JSON bridge records count toward native baselines only when the wrapper
  positively marks native DWG provenance with `uses_native_dwg=true` or an
  approved `evidence_scope` such as `native_dwg_bridge`,
  `commercial_dwg_native`, or `commercial_sdk_native`.
- `scripts/validate_dwg_native_backend.py --bridge-contract-json <contract.json>`
  can emit that same contract artifact from the sample-pack DWG inputs while
  producing the native validation summary, evidence manifest, and native audit.
  Pass `--bridge-command`, `--bridge-args-json`, `--bridge-license-id`, and
  `--bridge-supported-versions` on the same command when using the JSON bridge
  adapter so the run is reproducible without hidden environment setup.
- The same runner can emit product-path evidence with
  `--product-evidence-json <product-evidence.json>`. When this option is
  supplied for the JSON bridge adapter, the runner invokes the public
  `cad_compare file` path for sample-pack DWG pairs and fails closed if that
  product evidence fails.
- Product-path native evidence must include at least one `cad_compare file` or
  `cad_compare folder` run using `--dwg-backend commercial_sdk`,
  `--dwg-commercial-adapter-spec`, `--dwg-allowed-license-id`, and the
  `--dwg-bridge-*` options when the JSON bridge is the selected adapter.
- `scripts/run_dwg_product_bridge_evidence.py` generates this product-path
  evidence from an ADR-004 sample pack by invoking `python -m src.cli.cad_compare
  file` for each DWG pair. The summary records per-pair result JSON, exit code,
  timeout status, source/effective provenance, cleanup evidence, bridge
  diagnostics, and bridge adapter metadata. It fails closed when the product
  result lacks positive native provenance (`uses_native_dwg=true` or an approved
  native `evidence_scope`) or is marked as converted-DXF/fallback evidence.
- `scripts/audit_drawing_compare_release_readiness.py --require-native-dwg`
  hard-fails `dwg_json_bridge_product_evidence` when the native audit uses the
  JSON bridge but the supplied result/run artifacts lack product-path bridge
  diagnostics or positive native bridge provenance.
- When `--require-native-dwg` is used and the native audit reports
  `commercial_dwg_json_bridge` diagnostics, the release audit must also receive
  `--dwg-json-bridge-contract <contract.json>` and that contract report must
  pass.
- `scripts/validate_dwg_product_release_gate.py` is the final all-version DWG
  product gate. It runs native backend validation, emits bridge contract and
  product `cad_compare file` evidence, then runs this release readiness audit
  with `--require-native-dwg`. Treat its summary JSON as the final go/no-go
  artifact for any all-version/native DWG product claim; it also carries
  blocked native versions and version-level `next_actions` from the native
  all-version audit. If the required DWG generations are split across multiple
  ADR-004 sample packs, provide the first pack positionally and add the rest
  with `--extra-sample-pack <pack>` so the final gate aggregates one native
  all-version audit from all pack-level validation outputs. The final summary
  includes both `fallback_audit_matrix` and `effective_native_blockers`, so a
  passed fallback corpus gate is not mistaken for native completion and does
  not obscure the remaining approved-backend/native-baseline work.

Audit output:

- `status: passed | failed | partial`
- `failed_metrics`
- `missing_evidence`
- `partial_reasons`
- `allowed_release_claims`
- `forbidden_release_claims`

Hard-fail conditions:

- Default/customer ODA/GPL/AGPL runtime evidence.
- Forbidden modern/native DWG wording.
- Failed or missing all-version fallback readiness when
  `--dwg-all-version-audit` is supplied.
- Failed native readiness when `--require-native-dwg` is supplied.
- Missing or failed DWG JSON bridge contract when the native audit uses the
  commercial JSON bridge.
- Commercial SDK use without an explicit approved adapter and license allowlist.
- Commercial SDK folder/batch runs where preflight, descriptor scan, compare, or
  manifests omit the approved license allowlist.
- Missing provenance.
- Partial import without warnings.
- Missing timeout/orphan cleanup evidence.
- Missing required customer evidence manifest counts.

Native claim check:

```powershell
python scripts\audit_drawing_compare_release_readiness.py `
  --result-json <result.json> `
  --run-manifest <run_manifest.json> `
  --customer-evidence-manifest <customer_evidence_manifest.json> `
  --baseline-metrics <baseline_metrics.json> `
  --dwg-all-version-audit build\reports\dwg-all-version-support-audit.json `
  --native-dwg-audit build\reports\dwg-all-version-native-audit.json `
  --dwg-json-bridge-contract build\reports\dwg-json-bridge-contract.json `
  --require-native-dwg `
  --out <native_release_readiness_audit.json>
```

This native check must fail until `native_ready_versions` covers every target
generation and the release readiness manifest/metrics checks also pass.

## Safe Wording

- "PDF and DXF comparison are supported when release evidence gates pass."
- "AC1015 native DWG baseline is supported under the approved ODA-free limited path."
- "Modern DWGs can be compared through user-provided or registered converted DXF when provenance is preserved."
- "Explicit local/internal ODA fallback is available only when `oda_converter` is selected."

## Forbidden Wording

- "All DWG versions supported."
- "Modern DWG native support is complete."
- "AC1018/AC1021/AC1024/AC1027/AC1032 native DWG support."
- "AC1032 native DWG is supported."
- "ODA conversion is automatic in customer builds."
- "Partial imports are complete geometry parity."

## Required Verification

- Targeted comparison pytest suite.
- `python scripts\cad_policy_gate.py`
- `git diff --check`
- Release readiness audit on current evidence artifacts.
- Real sample validation when the local drawing comparison corpus is available.
