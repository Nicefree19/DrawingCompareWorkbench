# ADR-004 Phase 1 Risk Register

Date: 2026-06-02

## Summary

Native DWG expansion is high-risk because DWG is proprietary, version-specific,
and easy to overclaim. The current safe workflow remains user-provided
converted DXF for AC1018+.

| Risk | Severity | Evidence | Mitigation | Release impact |
| --- | --- | --- | --- | --- |
| Incompatible source contamination | critical | ODA/GPL/AGPL material is prohibited by policy | Use only approved public references and first-party clean-room notes; record reviewer/date/license | Blocks implementation and release claim |
| Premature all-version support claim | high | ADR-004 says AC1032/all-version support must not be advertised yet | Require version-specific release gate and exact wording review | Blocks release notes/UI wording |
| Missing converted-DXF baseline | high | AC1018/AC1021/AC1024/AC1027 baseline pairs are missing | Use Phase 0-B conversion request packet before native evaluation | Blocks compare-recall and false-positive metrics |
| Section map decoder uncertainty | high | AC1024/AC1032 are blocked at `section_map_decoder` | Update clean-room contract before decoding; keep diagnostics-only until approved | Blocks object map/entity work |
| Silent entity loss | high | Basic native path may skip unsupported entities | Emit unsupported entity counts and warning details in artifacts | Blocks support claim |
| Large drawing resource usage | medium | Full scan found large modern DWGs and corrupted samples | Add object/entity limits, timeout, memory, cancel tests | Blocks default enablement |
| Version reuse mistakes | medium | AC1018/AC1021/AC1024/AC1027/AC1032 may differ materially | Reuse parser components only after version-specific evidence | Blocks version promotion |
| Corrupted/encrypted handling gaps | medium | `D:\04. 작성도면` full scan found 5 corrupted/unreadable DWGs | Add fail-closed tests for corrupted, encrypted, truncated inputs | Blocks robustness claim |
| UI provenance ambiguity | medium | Converted-DXF fallback can mask original DWG status | Preserve original/effective source provenance in artifacts and UI notices | Blocks customer workflow trust |
| Gate inflation | low | Existing project has many P5-G gates | Do not add new P5-G gates; use ADR/cad policy gates and version metrics | Avoids process noise |

## Current Blockers

- AC1018: no confirmed before/after pair and no converted-DXF baseline.
- AC1021: no confirmed before/after pair and no converted-DXF baseline.
- AC1024: candidate selected, but converted-DXF baseline missing.
- AC1027: candidate selected, but converted-DXF baseline missing.
- AC1032: first baseline exists, but clean-room contract is blocked for native
  section-map decoding.

## Recommended Risk Burn-Down Order

1. Complete converted-DXF baselines for AC1024 and AC1027 selected pairs.
2. Identify AC1018 and AC1021 before/after pairs and collect converted DXFs.
3. Fill clean-room evidence packet for AC1018/AC1021 first.
4. Extend diagnostics-only tooling before enabling any parser.
5. Promote one version at a time through baseline metrics.

