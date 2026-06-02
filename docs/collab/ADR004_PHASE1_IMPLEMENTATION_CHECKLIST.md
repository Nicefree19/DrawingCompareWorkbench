# ADR-004 Phase 1 Implementation Checklist

Date: 2026-06-02

This checklist turns the Phase 1 plan into implementation-ready work units.
Items marked blocked must not be treated as implementation approval.

## Global Gates

- [ ] Confirm no ODA SDK, ODA File Converter, ODA samples, LibreDWG, GPL, or
  AGPL-derived material is used.
- [ ] Confirm `docs/DWG_CLEANROOM_FORMAT_CONTRACT.md` has approved evidence for
  the target version before binary section decoding starts.
- [ ] Confirm target version has a converted-DXF baseline pair.
- [ ] Confirm baseline compare summary is captured.
- [ ] Confirm release wording still blocks all-version/native-support claims.
- [ ] Run `python scripts\cad_policy_gate.py`.
- [ ] Run `git diff --check`.

## AC1018

- [ ] Select confirmed before/after DWG pair.
- [ ] Collect user-converted DXF before/after pair.
- [ ] Capture converted-DXF compare summary.
- [ ] Record clean-room reference evidence.
- [ ] Define section/object/table parser notes from approved material.
- [ ] Add diagnostics-only tests.
- [ ] Implement reader only after approval.
- [ ] Compare native result against converted-DXF baseline.

## AC1021

- [ ] Select confirmed before/after DWG pair.
- [ ] Collect user-converted DXF before/after pair.
- [ ] Capture converted-DXF compare summary.
- [ ] Record clean-room reference evidence.
- [ ] Add text/encoding-specific diagnostics cases.
- [ ] Implement reader only after AC1018 lessons are stable.
- [ ] Compare native result against converted-DXF baseline.

## AC1024

- [x] Select likely DWG pair.
- [ ] Collect requested converted DXF pair.
- [ ] Capture converted-DXF compare summary.
- [ ] Update `DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1` with approved evidence.
- [ ] Move diagnostics beyond `approved_format_contract_required`.
- [ ] Implement section map reader only after approval.
- [ ] Implement object map/table/entity layers after section map succeeds.
- [ ] Compare native result against converted-DXF baseline.

## AC1027

- [x] Select likely DWG pair.
- [ ] Collect requested converted DXF pair.
- [ ] Capture converted-DXF compare summary.
- [ ] Record clean-room reference evidence.
- [ ] Verify reuse boundaries from AC1024.
- [ ] Implement reader only after AC1024 components are stable.
- [ ] Compare native result against converted-DXF baseline.

## AC1032

- [x] Confirm first baseline-ready DWG pair under `D:\도면 비교`.
- [x] Capture first converted-DXF compare summary.
- [ ] Collect additional AC1032 baseline pairs for breadth.
- [ ] Update clean-room contract with approved AC1032 evidence.
- [ ] Preserve compression/encryption/unknown-layout guards.
- [ ] Implement section map reader only after approval.
- [ ] Implement object map/table/entity layers after section map succeeds.
- [ ] Compare native result against converted-DXF baseline.

## Support Claim Checklist

Before any target version is described as natively supported:

- [ ] Exact version is named.
- [ ] Unsupported entities and exclusions are listed.
- [ ] Converted-DXF baseline metrics are recorded.
- [ ] Runtime/memory/cancel behavior is recorded.
- [ ] Corrupted/encrypted failure behavior is recorded.
- [ ] UI wording distinguishes native import from converted-DXF fallback.
- [ ] Release note avoids "all DWG versions" unless every claimed version
  independently passed.

