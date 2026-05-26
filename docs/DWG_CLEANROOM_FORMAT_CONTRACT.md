# DWG Clean-Room Format Contract

Contract id: `DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1`

## Purpose

This document is the approval gate for native DWG reader expansion beyond the
current `AC1015` MVP. It is not an implementation note and intentionally does
not contain byte offsets, copied tables, pseudocode, or decoder algorithms.

The immediate scope is the versioned section-map reader needed before any
`AC1024` or `AC1032` object-map, table, or entity decoder can be implemented.

## Current Approval State

| Version | Family | Section-map decoder status | Approved reference available | Product claim |
| --- | --- | --- | --- | --- |
| `AC1024` | AutoCAD 2010/2011/2012 | blocked | no | blocked/experimental diagnostics only |
| `AC1032` | AutoCAD 2018+ | blocked | no | blocked/experimental diagnostics only |

The runtime diagnostic detail for both versions must remain
`approved_format_contract_required` until this contract is updated with approved
evidence.

## Allowed Source Classes

- Public references that legal/product owners explicitly approve for
  implementation use.
- First-party clean-room notes written from approved references.
- First-party synthetic fixtures created without incompatible source material.
- Internal tests that validate bounds, diagnostics, and failure modes before any
  entity support claim.

## Prohibited Source Classes

- ODA File Converter, ODA SDK, ODA sample code, or ODA binary reverse
  engineering output.
- GPL or AGPL CAD reader source code or derived implementation details.
- Decompiled binaries, copied proprietary tables, or snippets without clear
  license/provenance.
- Product claims that native import for `AC1024` or `AC1032` is available before
  a real sample imports to `CanonicalDrawing`.

## Required Approval Evidence

Before code may advance past `section_map_decoder`, record all of the following:

- Approved reference title, URL or storage path, license/provenance, reviewer,
  and review date.
- Legal or product-owner approval that the reference may be used for
  implementation.
- Clean-room implementation notes that describe the intended parser behavior
  without incompatible source code.
- Test plan for bounds checks, compression/encryption guards, malformed input,
  and diagnostic failure stages.
- Explicit product wording that still treats the version as unsupported until a
  real local sample imports as `partial` or `ok`.

## Evidence Packet

| Version | Reference title | Source URL/path | License/provenance | Reviewer | Review date | Allowed use | Forbidden material confirmation | Implementation scope | Approval status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `AC1024` | pending | pending | pending | pending | pending | none until approved | pending; ODA/GPL/AGPL material must remain excluded | section-map diagnostics only after approval | blocked |
| `AC1032` | pending | pending | pending | pending | pending | none until approved | pending; ODA/GPL/AGPL material must remain excluded | section-map diagnostics only after approval | blocked |

## Unlock Criteria

The contract may move from `blocked` to implementation-ready only when:

1. The approval evidence above is complete.
2. `scripts\cad_policy_gate.py` passes after the contract update.
3. `scripts\dwg_native_diagnostics.py` reports a more specific stage than
   generic unsupported for at least one real sample.
4. No customer-facing document claims expanded DWG support before validation.

Until then, `DwgVersionedSectionMapReader` must return structured diagnostics
and must not attempt binary section-map decoding for `AC1024` or `AC1032`.
