# Own CAD Viewer — AC1032 Spike TechSpec

Date: 2026-06-14
Status: IN PROGRESS (de-risking spike; not a support claim, not default enablement)
North star: `own-cad-viewer-goal` (deep-interview, 2026-06-14)
Progress: **S0 (provenance) + S1 (R2018 container navigable) DONE 2026-06-14** —
the documented R2004 LCG de-obfuscation reproduces the `AcFssFcAJMB` magic and
locates the section-page-map in bounds on two real AC1032 files
(`dwg_r2018_reader.py`, 8 tests). The spike's biggest unknown (is the R2018
container navigable clean-room from the public spec?) is answered YES. Next: S2.

## 1. Goal

Prove, on one real **AC1032** drawing pair, that the product can read DWG with an
**own clean-room reader** (no ODA, no ezdxf), render it with the **own renderer**,
and auto-draw **revision clouds** on the diff using the **existing** canonical
diff/cloud engine — with **zero ODA calls**. The spike exists to de-risk the one
unknown that gates the whole program: *can AC1032 (R2018) geometry be parsed from
the public ODA spec in a clean room?*

This is a thin vertical slice, not the product. It is allowed to support only
basic geometry and one drawing pair.

## 2. Architecture (what is new vs reused)

```
 NEW (own)                              NEW (skeleton exists)        REUSED (unchanged)
 AC1032 DWG --clean-room parse--> canonical --native_scene_pack--> viewport
   dwg_r2018_*.py                  (canonical/v1)   _builder + ViewerPrimitiveSource
                                                          |
                                                          v
                                       existing diff + revision-cloud engine
                                       (drawing_differ / change_zones / revision_marker)
                                                  --> 구름마크 (auto)
```

- **Reader (NEW):** AC1032 binary → `canonical-drawing/v1`. Reuses the AC1015
  clean-room *architecture* (`dwg_binary_reader` bit/byte reader; the
  reader→section→object→canonical layering) — NOT its R2000 section format.
- **Renderer (skeleton exists):** `native_scene_pack_builder.build_native_scene_pack(_ref)`
  + `resolve_viewer_primitive_source` already turn canonical entities into
  viewport primitives (test-proven, commit 3c7bbcb). Extend entity coverage only.
- **Diff + clouds (REUSED, untouched):** the canonical-level engine already
  produces change zones and revision clouds. The spike feeds it canonical from
  the own reader instead of ezdxf/ODA.

## 3. Clean-room governance gate (Step 0 — mandatory, do FIRST)

`dwg_cleanroom_contract.py` already gates AC1032 as `approval_status="blocked"`
with `next_safe_step = "record an approved AC1032 section-map format contract
before decoding"`. Required evidence to unblock:

1. approved public-reference citation with license/provenance — **ODA "Open
   Design Specification for .dwg files"** (publicly published for implementers;
   implementing from it is permitted). **No GPL (LibreDWG) code referenced.**
2. product-owner approval recorded in docs — **granted 2026-06-14** (deep-interview).
3. clean-room implementation notes written without incompatible source code.
4. diagnostic-only tests before any entity-decoding support claim.

**S0 deliverable:** flip the AC1032 contract to an approved state (reference =
ODA public spec, provenance recorded), add a short `docs/collab/` clean-room
provenance note. No byte layouts in the contract object itself. Until S0 lands,
S1+ must not decode AC1032 object bytes.

## 4. Spike steps (each step ships a test + a kill/go check)

| Step | Deliverable | Validates | Kill signal |
| --- | --- | --- | --- |
| **S0 governance** | Approved AC1032 clean-room contract + provenance note | gate unblocked legitimately | (none — paperwork) |
| **S1 header+section-page-map (diagnostic only)** | `dwg_r2018_reader`: parse the R2018 file header → locate the section-page map / section map; emit a DIAGNOSTIC (counts, offsets), decode NO objects | the R2018 container is navigable from spec | header/section map not reproducible from spec in ~1 wk → reassess |
| **S2 section extract (decompress)** | Locate + decompress the `AcDb:Handles` (object map) and `AcDb:AcDbObjects` sections (R2004+ compression) | object stream is reachable | compression/encryption not decodable from spec → reassess |
| **S3 basic-geometry decode** | Decode LINE/LWPOLYLINE/CIRCLE/ARC/TEXT objects → `canonical-drawing/v1` (fail-closed visible-unsupported for the rest, per the W1 pattern) | real AC1032 imports to canonical entities | per-object layout not stable across the sample → narrow scope |
| **S4 render** | Feed canonical → `build_native_scene_pack_ref` → `resolve_viewer_primitive_source`; confirm primitives + bbox render | own renderer shows the drawing | (renderer already proven; low risk) |
| **S5 diff + clouds** | Run one real AC1032 before/after pair through the existing diff/cloud engine on own-reader canonical; clouds appear on the changes | end-to-end own stack, ODA calls = 0 | clouds misplaced → coordinate/transform gap to fix |

Steps land as separate commits behind a flag; the current ODA/ezdxf pipeline is
untouched and remains the default.

## 5. Go / Kill criteria

- **GO (spike succeeds):** one real AC1032 pair renders basic geometry and shows
  auto revision clouds via the own stack, ODA calls = 0, diff matches the
  existing ODA-based result on the basic-geometry subset (golden compare).
  → proceed to the entity-coverage roadmap (MTEXT/INSERT/HATCH/DIMENSION…).
- **KILL / REASSESS:** S1 or S2 cannot be reproduced from the ODA public spec
  within their week budget (R2018 container or compression intractable
  clean-room). → report honestly; reconsider scope (e.g. start at an easier
  version, or revisit the ODA-dependency decision). No silent grinding.

## 6. Validation harness

- `python -m pytest tests/unit/services/comparison/test_dwg_r2018_reader.py -p no:xdist -q`
- diagnostic-only fixtures first (S1/S2), real-sample integration behind `skipif`
  on a local AC1032 sample (S3–S5), mirroring `test_native_scene_pack_builder.py`.
- `python scripts/cad_policy_gate.py` (no forbidden wording / no support claim).
- ODA-call assertion: the spike path must not import or invoke any ODA/converter.
- `git diff --check`.

## 7. Done criteria (this spike only)

A repeatable command/test takes one real AC1032 pair → own reader → own render →
existing cloud engine → revision clouds, with ODA calls = 0, and a golden compare
showing the basic-geometry diff matches the existing pipeline. Matrix promotion
and any "native AC1032" support wording remain OUT (HITL, later).

## 8. Out of scope (spike)

DWG write/edit; AC1032 entity types beyond the 5 basic ones; other versions
(AC1027/24/21); DXF/PDF paths; performance tuning; default enablement; support
wording; GUI monolith changes (service layer only).

## 9. Sample data

Local AC1032 samples already present (git-ignored): e.g.
`.local/native_cad_real_samples/acadsharp/sample_AC1032.dwg`,
`.local/native_cad_real_samples/calpoly_floor_plans/Building001-0_Floor2.dwg`,
and the user's `D:\도면 비교` / `D:\04. 작성도면` AC1032 sets for a real
before/after pair. The spike picks one real pair for S5; raw drawings stay local.

## 10. Open questions (resolved by the spike or deferred)

1. AC1032 spec parsing feasibility — the spike's core test (S1/S2).
2. Entity-coverage order after GO — by real structural-drawing frequency.
3. Golden accuracy bar — basic-geometry diff parity vs the existing pipeline (S5).
4. Renderer performance on large sheets — deferred until after GO.
5. Full "ship" definition (coverage/versions) — deferred until after GO.
