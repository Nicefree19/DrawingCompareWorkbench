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

## AC1027 (R2013) back-expansion (DoD-V1, 2026-06-29)

- **Finding**: AC1027 (R2013, the AutoCAD 2013-2017 file code) uses the SAME
  R2004+ container, R2010+ Common Entity Data, and R2007+ string stream as
  AC1032 (R2018). The existing clean-room R2018 decode chain therefore applies
  to AC1027 verbatim once the version gate accepts it — no new format
  reverse-engineering was needed.
- **Verification (clean-room, ODA-as-oracle-only)**: ODAFileConverter 26.10.0
  converted the local AC1027 sample to an ACAD2018 DXF OFFLINE; ezdxf read the
  ground-truth geometry (validation-only, never product code). The native
  clean-room decoder reproduced every supported entity type
  (LINE/CIRCLE/ARC/POINT/LWPOLYLINE/TEXT/MTEXT/INSERT/DIMENSION/HATCH/ELLIPSE/
  SPLINE/LEADER) within the AC1032 tolerances (coords 1e-6, angles 1e-4,
  measurement 1e-5) on `sample_AC1027.dwg` — the same drawing content as
  `sample_AC1032.dwg`, so the entity handles match the AC1032 golden test 1:1.
  Real AC1027 corpus also decodes: `example_2013.dwg` (171 entities) and the real
  Korean P5 PSRC pair (`..._2013.dwg` / `..._r1_2013.dwg`: 5851 / 6874 entities).
- **No R2013 delta observed**: across the corpus there was NO R2013-specific
  field-layout difference vs R2018 in any decoder. Should a future R2013 sample
  expose one, the per-version `version_code` threading already lets a decoder
  fail-closed (skip the entity) rather than emit wrong geometry.
- **Still gated**: `dwg_cleanroom_contract.py` AC1027 entry is `blocked`
  (`approved_reference_available=False`), mirroring AC1024/AC1032. The native
  AC1027 path is OFF by default behind the SAME experimental opt-in env as
  AC1032 (`DRAWING_COMPARE_DWG_AC1032_NATIVE`). No support claim is made.
