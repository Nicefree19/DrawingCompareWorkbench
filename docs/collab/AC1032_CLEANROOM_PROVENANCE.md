# AC1032 Clean-room Provenance & Approval (Spike S0)

Date: 2026-06-14
Scope: governance record for the own-viewer AC1032 spike (see
`OWN_VIEWER_AC1032_SPIKE_TECHSPEC.md`, `own-cad-viewer-goal`).

This document records the clean-room approval evidence required by
`src/services/comparison/dwg_cleanroom_contract.py` before AC1032 work proceeds.
It is **not** a support claim and does **not** enable default AC1032 import.

## Approved reference

- **Reference**: Open Design Alliance, *"Open Design Specification for .dwg
  files"* — publicly published by the ODA for implementers. Implementing a DWG
  reader from this specification is its intended use.
- **License/provenance posture**: clean-room from the public ODA specification
  only. **No GPL-family source is referenced or copied** (LibreDWG and similar
  GPL readers are explicitly NOT used as a code reference). No ODA SDK runtime
  dependency. Removing the ODA/converter runtime dependency is the program's end
  goal.
- Algorithms used (all documented in the public spec, not copied from any
  implementation): the R2004+ file-header LCG de-obfuscation and the
  `AcFssFcAJMB` header magic; the section-page-map / section-map navigation.

## Product-owner approval

- Granted by the product owner on **2026-06-14** (deep-interview), confirming:
  read-only own DWG reader for AC1032 first; clean-room from the ODA public
  spec; no GPL reference; current ODA/ezdxf pipeline stays the default behind a
  flag.

## Scope and gating (honest boundary)

- **Approved now**: diagnostic-only exploration of the AC1032 (R2018) container
  — file header, R2004 header de-obfuscation, section-page-map / section-map
  navigation — plus diagnostic tests. This satisfies the contract's
  "diagnostic-only tests before any entity decoding support claim" requirement.
- **Still gated** (`dwg_cleanroom_contract.py` AC1032 stays `blocked`): any
  AC1032 *object/entity decoding support claim*. The contract is flipped only
  AFTER the diagnostics are reviewed and decoding support is explicitly
  approved. The spike therefore leaves the contract object and its safety tests
  unchanged on purpose.

## Clean-room implementation notes

- Implementation is written from the public-spec description and verified
  against locally held real AC1032 samples (own files) by observation — never by
  reading another implementation's source.
- S1 verification (2026-06-14): the documented LCG de-obfuscation of the 0x6C
  bytes at offset 0x80 reproduces the `AcFssFcAJMB\0` magic on two independent
  real AC1032 files, and the decoded section-page-map address lands inside the
  file bounds. This confirms the container is navigable from the public spec.
