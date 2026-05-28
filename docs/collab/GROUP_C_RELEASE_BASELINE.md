# Group C Release Baseline

Last verification target: Group C CAD-core only.
Last verified: 2026-05-26 17:33 KST, `passed`.

## Purpose

This baseline separates the releasable CAD-core track from viewer/UX hardening
that is currently present in the same working tree. Group C is the release
candidate for ODA-free CAD comparison: importer, canonical model, normalization,
comparison, DWG diagnostics, policy gates, samples, and performance evidence.

## Group C Includes

- CAD policy/spec documents: `CAD_FORMAT_*`, `ENTITY_SUPPORT_MATRIX.md`,
  `THIRD_PARTY_LICENSE_POLICY.md`, `DWG_*`, canonical schemas, diff/error-code
  specs, normalization policy, and this baseline.
- CAD scripts: `scripts\cad_*`, `scripts\dwg_*`,
  `scripts\validate_real_world_dwg_samples.py`, and release environment checks
  that enforce ODA/PyMuPDF policy.
- CAD services: `dxf_*`, `dwg_*`, `drawing_compare_engine.py`,
  `drawing_normalizer.py`, `import_pipeline.py`, `cad_stability.py`, and
  `localized_compare.py`.
- CAD tests/data: `tests\data\comparison\cad_samples`,
  `tests\data\comparison\dxf_writer`, `tests\data\comparison\real_world`,
  CAD importer/normalizer/writer/diff tests, DWG diagnostic tests, and script
  tests for CAD policy/regression/performance/release checks.

## Group C Excludes

- Viewer/UX files under `src\gui`, QML viewport changes, lightweight viewport
  plumbing, and Korean workbench UX tests.
- PDF preview hardening, viewer package/proxy/render worker changes, zone
  render service/worker/vector renderer changes, and related visual workflow
  tests.
- Any ODA fallback changes except policy wording and quarantine checks needed
  to keep customer/runtime default paths ODA-free.

## Release Gate

Run the Group C gate with:

```powershell
python scripts\cad_group_c_release_gate.py
```

For the full smoke set including the larger performance benchmark:

```powershell
python scripts\cad_group_c_release_gate.py --include-performance
```

The gate writes:

- `build\reports\group-c-release-gate.json`
- `build\reports\group-c-release-gate.md`
- `build\reports\cad-format-regression-report.md`
- `build\reports\cad-performance-smoke.json` when `--include-performance` is used

The gate commands are:

- `git diff --check`
- `python scripts\cad_policy_gate.py`
- `python scripts\cad_format_regression.py --check --report build\reports\cad-format-regression-report.md`
- `python scripts\dwg_native_diagnostics.py`
- `python scripts\validate_real_world_dwg_samples.py`
- `python -m pytest tests\unit\services\comparison -q --tb=short --disable-warnings -o log_cli=false -o addopts=''`
- `python -m pytest tests\unit\scripts -q --tb=short -o log_cli=false`

## Current Verification Summary

- Latest full gate: `python scripts\cad_group_c_release_gate.py --include-performance`
  passed on 2026-05-26 17:33 KST.
- DXF: ODA-free import/normalize/compare path is the releasable baseline.
- DWG: `AC1015` native MVP only. `AC1024` and `AC1032` remain blocked by
  `DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1`.
- ODA fallback: quarantined legacy/internal opt-in only; customer/runtime
  default path must not require or auto-invoke ODA.
- Remaining blocking stage: `section_map_decoder` for all current AC1024/AC1032
  real-world samples.

## Remaining Risks

- Group C is still mixed in the working tree with Group A/B viewer changes, so
  commit or release packaging must stage Group C paths deliberately.
- AC1024/AC1032 DWG availability must not be claimed until approved clean-room
  evidence exists and at least one real sample imports as `partial` or `ok`.
- Broad text scans can produce false positives on internal diagnostic constants
  such as `unsupported_version`; rely on `cad_policy_gate.py` for enforced
  product wording policy.
